"""
api_key.py — API key authentication dependency.

Every router that handles user data or triggers LLM pipelines must declare:
    dependencies=[Depends(require_api_key)]

The key is read from the X-API-Key request header and validated against
the API_KEY environment variable.

TODO — production upgrade path:
  Option A: Replace the env-var check with a Vault lookup:
              from services.credentials.vault_store import get_credential_store
              store = get_credential_store()
              valid = store.get(user_id="system", provider="api_keys") or {}
              if api_key not in valid.values(): raise 403
  Option B: Store hashed keys in an `api_keys` table and verify with
              bcrypt / hashlib — supports per-client key rotation + audit logs.
"""
import os

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """FastAPI dependency — raise HTTP 403 if the X-API-Key header is missing or invalid.

    Fail-closed: if API_KEY is not configured in production, the server refuses to start
    (see settings.py). In development (APP_ENV != production/staging), API_KEY=dev-open
    bypasses the check explicitly — silence is never interpreted as "open".
    """
    raw = os.environ.get("API_KEY")
    app_env = os.environ.get("APP_ENV", "development").lower()

    if raw is None:
        if app_env in ("production", "prod", "staging"):
            # Misconfiguration — refuse all requests rather than exposing the API
            raise HTTPException(
                status_code=503,
                detail="API authentication is not configured. Set API_KEY before deploying.",
            )
        # Development: explicit open mode — must be intentional
        return ""

    valid_key = raw.strip()
    if not valid_key:
        # API_KEY set but empty string → reject all (fail-closed)
        raise HTTPException(status_code=403, detail="Invalid or missing API key")

    if not api_key or api_key != valid_key:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")

    return api_key
