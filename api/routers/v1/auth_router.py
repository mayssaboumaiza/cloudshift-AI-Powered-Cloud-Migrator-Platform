"""
auth_router.py — Authentication and User Management endpoints.

Public  (no auth):
    POST /api/v1/auth/register              — bootstrap first admin only
    POST /api/v1/auth/login                 — obtain access + refresh tokens
    POST /api/v1/auth/refresh               — exchange refresh token for new access token
    GET  /api/v1/auth/invite/{token}        — validate invitation token
    POST /api/v1/auth/invite/{token}/accept — accept invitation → create account + tokens

Protected (Bearer JWT):
    GET  /api/v1/auth/me                    — current user profile
    PUT  /api/v1/auth/me/password           — change own password

Admin only:
    POST   /api/v1/auth/invite              — create + send invitation email
    GET    /api/v1/auth/invitations         — list pending invitations
    DELETE /api/v1/auth/invitations/{id}    — revoke invitation
    GET    /api/v1/auth/users               — list all users
    GET    /api/v1/auth/users/{id}          — get user by id
    POST   /api/v1/auth/users               — create user (admin shortcut)
    PATCH  /api/v1/auth/users/{id}          — update user (role, is_active, etc.)
    DELETE /api/v1/auth/users/{id}          — delete user
"""
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from api.rate_limiter import limiter, SLOWAPI_AVAILABLE
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from api.auth.jwt_handler import (
    TokenData,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    require_admin,
)
from api.auth.password import hash_password, verify_password
from api.schemas.auth_schema import (
    AcceptInviteRequest,
    ChangePasswordRequest,
    InviteInfoResponse,
    InviteRequest,
    InviteResponse,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserResponse,
    UserUpdate,
)
from api.services.email_service import send_invitation_email
from configuration.database import get_db_session
from data.models.invitation_model import Invitation
from data.models.user_model import User, UserRole
from data.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_ACCESS_EXPIRE_SEC = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "60")) * 60


def _repo(db: AsyncSession = Depends(get_db_session)) -> UserRepository:
    return UserRepository(db)


# ── Register ────────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bootstrap first admin account (closed after first use)",
)
@(limiter.limit("5/minute") if SLOWAPI_AVAILABLE else lambda f: f)
async def register(
    request: Request,
    payload: UserCreate,
    repo: UserRepository = Depends(_repo),
):
    """Bootstrap-only registration.

    Creates the first ADMIN account. Once an admin exists, registration is
    closed — use POST /auth/invite instead.
    """
    if await repo.admin_exists():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Registration is closed. Ask an administrator to invite you.",
        )

    if await repo.email_taken(payload.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    if await repo.username_taken(payload.username):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")

    user = User(
        email=payload.email,
        username=payload.username.strip(),
        hashed_password=hash_password(payload.password),
        role=UserRole.ADMIN,
    )
    user = await repo.create(user)
    return user


# ── Invitations ──────────────────────────────────────────────────────────────

@router.post(
    "/invite",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create and send an invitation [admin]",
    dependencies=[Depends(require_admin)],
)
async def create_invitation(
    payload: InviteRequest,
    current: TokenData = Depends(require_admin),
    repo: UserRepository = Depends(_repo),
    db: AsyncSession = Depends(get_db_session),
):
    if await repo.email_taken(payload.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "This email already has an account")

    # Check no pending invitation for this email
    result = await db.execute(
        select(Invitation).where(
            Invitation.email == payload.email.lower(),
            Invitation.used_at.is_(None),
            Invitation.expires_at > datetime.now(timezone.utc),
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A pending invitation already exists for this email",
        )

    inviter = await repo.get_by_id(current.user_id)
    invited_by_name = inviter.username if inviter else "Un administrateur"

    invitation = Invitation(
        id=str(uuid.uuid4()),
        token=secrets.token_urlsafe(32),
        email=payload.email.lower(),
        role=payload.role,
        invited_by=current.user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
    )
    db.add(invitation)
    await db.commit()
    await db.refresh(invitation)

    try:
        await send_invitation_email(
            to_email=invitation.email,
            token=invitation.token,
            invited_by_name=invited_by_name,
            role=invitation.role.value,
        )
    except Exception as exc:
        logger.error("Failed to send invitation email to %s: %s", invitation.email, exc)
        # Don't fail the request — admin can still share the link manually

    return InviteResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        used=False,
    )


@router.get(
    "/invitations",
    response_model=list[InviteResponse],
    summary="List pending invitations [admin]",
    dependencies=[Depends(require_admin)],
)
async def list_invitations(
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(Invitation)
        .where(Invitation.used_at.is_(None))
        .order_by(Invitation.created_at.desc())
    )
    invitations = result.scalars().all()
    return [
        InviteResponse(
            id=inv.id,
            email=inv.email,
            role=inv.role,
            expires_at=inv.expires_at,
            used=inv.is_used,
        )
        for inv in invitations
    ]


@router.delete(
    "/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an invitation [admin]",
    dependencies=[Depends(require_admin)],
)
async def revoke_invitation(
    invitation_id: str,
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(Invitation).where(Invitation.id == invitation_id)
    )
    invitation = result.scalar_one_or_none()
    if not invitation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    await db.delete(invitation)
    await db.commit()


@router.get(
    "/invite/{token}",
    response_model=InviteInfoResponse,
    summary="Validate invitation token (public)",
)
async def get_invite_info(
    token: str,
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(Invitation).where(Invitation.token == token)
    )
    invitation = result.scalar_one_or_none()
    if not invitation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid invitation link")
    if invitation.is_used:
        raise HTTPException(status.HTTP_410_GONE, "This invitation has already been used")
    if invitation.is_expired:
        raise HTTPException(status.HTTP_410_GONE, "This invitation has expired")

    return InviteInfoResponse(
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
    )


@router.post(
    "/invite/{token}/accept",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Accept invitation and create account (public)",
)
@(limiter.limit("10/minute") if SLOWAPI_AVAILABLE else lambda f: f)
async def accept_invitation(
    request: Request,
    token: str,
    payload: AcceptInviteRequest,
    repo: UserRepository = Depends(_repo),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(Invitation).where(Invitation.token == token)
    )
    invitation = result.scalar_one_or_none()
    if not invitation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid invitation link")
    if invitation.is_used:
        raise HTTPException(status.HTTP_410_GONE, "This invitation has already been used")
    if invitation.is_expired:
        raise HTTPException(status.HTTP_410_GONE, "This invitation has expired")

    if await repo.email_taken(invitation.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    if await repo.username_taken(payload.username):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")

    user = User(
        email=invitation.email,
        username=payload.username.strip(),
        hashed_password=hash_password(payload.password),
        role=invitation.role,
    )
    user = await repo.create(user)

    invitation.used_at = datetime.now(timezone.utc)
    await db.commit()

    return TokenResponse(
        access_token=create_access_token(user.id, user.email, user.role),
        refresh_token=create_refresh_token(user.id),
        expires_in=_ACCESS_EXPIRE_SEC,
    )


# ── Admin exists check (public) ─────────────────────────────────────────────

@router.get(
    "/admin-exists",
    summary="Check if an admin account exists (used to show/hide register link)",
)
async def admin_exists(repo: UserRepository = Depends(_repo)):
    exists = await repo.admin_exists()
    return {"exists": exists}


# ── Login ────────────────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Obtain access + refresh tokens",
)
@(limiter.limit("10/minute") if SLOWAPI_AVAILABLE else lambda f: f)
async def login(
    request: Request,
    payload: LoginRequest,
    repo: UserRepository = Depends(_repo),
):
    user = await repo.get_by_email(payload.email)
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")

    return TokenResponse(
        access_token=create_access_token(user.id, user.email, user.role),
        refresh_token=create_refresh_token(user.id),
        expires_in=_ACCESS_EXPIRE_SEC,
    )


# ── Refresh ──────────────────────────────────────────────────────────────────

@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange refresh token for a new access token",
)
async def refresh_token(
    payload: RefreshRequest,
    repo: UserRepository = Depends(_repo),
):
    data = decode_token(payload.refresh_token, expected_type="refresh")
    user = await repo.get_by_id(data["sub"])
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")

    return TokenResponse(
        access_token=create_access_token(user.id, user.email, user.role),
        refresh_token=create_refresh_token(user.id),
        expires_in=_ACCESS_EXPIRE_SEC,
    )


# ── Current user ─────────────────────────────────────────────────────────────

@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get own profile",
)
async def me(
    current: TokenData = Depends(get_current_user),
    repo: UserRepository = Depends(_repo),
):
    user = await repo.get_by_id(current.user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.put(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change own password",
)
async def change_own_password(
    payload: ChangePasswordRequest,
    current: TokenData = Depends(get_current_user),
    repo: UserRepository = Depends(_repo),
):
    user = await repo.get_by_id(current.user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    await repo.update(user, {"hashed_password": hash_password(payload.new_password)})


# ── Admin: User Management ────────────────────────────────────────────────────

@router.get(
    "/users",
    response_model=list[UserResponse],
    summary="List all users [admin]",
    dependencies=[Depends(require_admin)],
)
async def list_users(
    skip: int = 0,
    limit: int = 50,
    repo: UserRepository = Depends(_repo),
):
    return await repo.get_all(skip=skip, limit=limit)


@router.get(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Get user by ID [admin]",
    dependencies=[Depends(require_admin)],
)
async def get_user(
    user_id: str,
    repo: UserRepository = Depends(_repo),
):
    user = await repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create user [admin]",
    dependencies=[Depends(require_admin)],
)
async def create_user(
    payload: UserCreate,
    repo: UserRepository = Depends(_repo),
):
    if await repo.email_taken(payload.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    if await repo.username_taken(payload.username):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")

    user = User(
        email=payload.email,
        username=payload.username.strip(),
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    return await repo.create(user)


@router.patch(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Update user (role, active, password) [admin]",
    dependencies=[Depends(require_admin)],
)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    repo: UserRepository = Depends(_repo),
):
    user = await repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    updates: dict = {}
    if payload.username is not None:
        if await repo.username_taken(payload.username, exclude_id=user_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")
        updates["username"] = payload.username.strip()
    if payload.role is not None:
        updates["role"] = payload.role
    if payload.is_active is not None:
        updates["is_active"] = payload.is_active
    if payload.password is not None:
        updates["hashed_password"] = hash_password(payload.password)

    return await repo.update(user, updates)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete user [admin]",
    dependencies=[Depends(require_admin)],
)
async def delete_user(
    user_id: str,
    current: TokenData = Depends(require_admin),
    repo: UserRepository = Depends(_repo),
):
    if user_id == current.user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete your own account")
    user = await repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await repo.delete(user)
