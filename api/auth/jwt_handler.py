"""
jwt_handler.py — JWT token creation and verification.

Access token  : short-lived (ACCESS_TOKEN_EXPIRE_MINUTES, default 60 min)
Refresh token : long-lived  (REFRESH_TOKEN_EXPIRE_DAYS,   default 7 days)

Secret key    : JWT_SECRET_KEY env var (required in production)
Algorithm     : HS256

Token payload (access):
    sub   — user.id  (UUID string)
    email — user.email
    role  — user.role value
    type  — "access"
    exp   — expiry timestamp
    iat   — issued at

Token payload (refresh):
    sub   — user.id
    type  — "refresh"
    exp / iat
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwt

from data.models.user_model import UserRole

_raw_secret = os.environ.get("JWT_SECRET_KEY", "").strip()

# Refuse to start with a missing or obviously weak secret.
# A known placeholder in production would let anyone forge admin tokens.
# strip() prevents bypassing the length check with whitespace-only values.
_KNOWN_WEAK = {"", "CHANGE_ME_in_production_use_32+_random_bytes", "secret", "changeme"}
if _raw_secret in _KNOWN_WEAK or len(_raw_secret) < 32:
    _app_env = os.environ.get("APP_ENV", "development").lower()
    if _app_env in ("production", "prod", "staging"):
        raise SystemExit(
            "[FATAL] JWT_SECRET_KEY is absent or too short (< 32 chars). "
            "Set a cryptographically random value before starting in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    # In development, fall back to a fixed dev-only secret and warn loudly.
    import warnings
    warnings.warn(
        "JWT_SECRET_KEY is not set or is too short — using an insecure development "
        "placeholder. Set JWT_SECRET_KEY before deploying to production.",
        stacklevel=2,
    )
    _raw_secret = "dev-only-insecure-placeholder-do-not-use-in-production-32chars"

_SECRET  = _raw_secret
_ALGO    = "HS256"
_ACCESS_EXPIRE_MIN  = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
_REFRESH_EXPIRE_DAYS = int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

bearer_scheme = HTTPBearer(auto_error=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: str, email: str, role: UserRole) -> str:
    payload = {
        "sub":   user_id,
        "email": email,
        "role":  role.value,
        "type":  "access",
        "iat":   _now(),
        "exp":   _now() + timedelta(minutes=_ACCESS_EXPIRE_MIN),
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALGO)


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub":  user_id,
        "type": "refresh",
        "iat":  _now(),
        "exp":  _now() + timedelta(days=_REFRESH_EXPIRE_DAYS),
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALGO)


def decode_token(token: str, expected_type: Literal["access", "refresh"] = "access") -> dict:
    """Decode and validate a JWT.  Raises HTTP 401 on any failure."""
    try:
        payload = jwt.decode(token, _SECRET, algorithms=[_ALGO])
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Expected {expected_type} token",
        )
    return payload


# ── FastAPI dependencies ────────────────────────────────────────────────────

class TokenData:
    """Parsed token data injected via Depends."""
    def __init__(self, payload: dict):
        self.user_id: str      = payload["sub"]
        self.email:   str      = payload.get("email", "")
        self.role:    UserRole = UserRole(payload.get("role", UserRole.ANALYST.value))


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> TokenData:
    """Dependency: require a valid Bearer access token."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials, expected_type="access")
    return TokenData(payload)


def require_role(*roles: UserRole):
    """Dependency factory: require current user to have one of the listed roles."""
    async def _check(current: TokenData = Depends(get_current_user)) -> TokenData:
        if current.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient role. Required: {[r.value for r in roles]}",
            )
        return current
    return _check


# Convenience role aliases
require_admin   = require_role(UserRole.ADMIN)
require_analyst = require_role(UserRole.ADMIN, UserRole.ANALYST)
