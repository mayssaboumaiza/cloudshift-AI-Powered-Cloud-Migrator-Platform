"""
dependencies.py - FastAPI Dependency Injection.

Every external client (DB, Vault) is exposed as a FastAPI dependency so that
routers and services can be tested with the real implementations replaced by
lightweight in-memory mocks — without changing any application logic.

Usage in a router:
    @router.get("/")
    async def handler(db: AsyncSession = Depends(get_db), vault: CredentialStore = Depends(get_vault)):
        ...

Usage in tests:
    app.dependency_overrides[get_db] = lambda: FakeSession()
    app.dependency_overrides[get_vault] = lambda: FakeVault()
"""
from typing import Optional
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from configuration.database import get_db_session
from data.models.user_model import UserRole
from data.repositories.migration_repository import MigrationRepository
from services.migration_service import MigrationService


# ── Database ──────────────────────────────────────────────────────────────────

async def get_db(
    db: AsyncSession = Depends(get_db_session),
) -> AsyncSession:
    """Yield an async SQLAlchemy session. Override in tests via dependency_overrides."""
    yield db


# ── Vault / CredentialStore ───────────────────────────────────────────────────

def get_vault():
    """Return the CredentialStore singleton (Vault in prod, file backend in dev).

    Thin wrapper around get_credential_store() so tests can override the
    entire vault without patching module-level globals.
    """
    from services.credentials.vault_store import get_credential_store
    return get_credential_store()


# ── Service layer (composes DB + business logic) ──────────────────────────────

async def get_migration_repository(
    db: AsyncSession = Depends(get_db_session),
) -> MigrationRepository:
    return MigrationRepository(db)


async def get_migration_service(
    repo: MigrationRepository = Depends(get_migration_repository),
) -> MigrationService:
    return MigrationService(repo)


# ── Optional caller identity (progressive multi-tenancy) ─────────────────────

@dataclass
class CallerIdentity:
    """Resolved caller identity — None fields mean 'unauthenticated via JWT'."""
    user_id: Optional[str]
    is_admin: bool


async def get_caller_identity(request: Request) -> CallerIdentity:
    """Extract JWT identity if a Bearer token is present; return anonymous otherwise.

    This dependency is intentionally non-blocking: if no Bearer token is sent
    (legacy API-key-only clients) it returns CallerIdentity(user_id=None, is_admin=False).
    Routers that require a JWT must additionally declare Depends(get_current_user).

    Phase 1 behaviour:
      - Bearer present + valid  → user_id and role resolved
      - Bearer absent/invalid   → user_id=None, is_admin=False (legacy mode, logs warning)
    """
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from api.auth.jwt_handler import decode_token
    import logging
    _log = logging.getLogger("CallerIdentity")

    bearer = HTTPBearer(auto_error=False)
    credentials: Optional[HTTPAuthorizationCredentials] = await bearer(request)

    if credentials is None:
        _log.debug("No Bearer token — anonymous caller (API-key-only mode)")
        return CallerIdentity(user_id=None, is_admin=False)

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
        role = payload.get("role", "")
        return CallerIdentity(
            user_id=payload.get("sub"),
            is_admin=(role == UserRole.ADMIN.value),
        )
    except Exception:
        # Invalid or expired JWT — treat as anonymous (API key still guards the route)
        _log.debug("Bearer token present but invalid — falling back to anonymous")
        return CallerIdentity(user_id=None, is_admin=False)
