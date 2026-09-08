"""
api/routers/v1/secrets_router.py — Phase 1 secret lifecycle endpoints.

Endpoints
─────────
POST   /secrets                            store a secret → secret_ref + metadata
GET    /secrets/{secret_ref}               metadata ONLY (never the value)
DELETE /secrets/{secret_ref}               delete from broker + DB + audit
GET    /migrations/{migration_id}/secrets  list all refs for a migration

POST   /secrets/{secret_ref}/retrieve      JIT retrieval (worker/internal use only).
                                           Returns the actual secret value.
                                           In production: gate behind internal-only
                                           auth (service token, network policy, etc.)

Security rules:
  • POST /secrets and /retrieve never log the data dict.
  • GET endpoints never return data — only metadata (ref, role, timestamps).
  • Audit entry written on every mutating call.
  • expired secrets are rejected on retrieve.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from configuration.database import get_db_session
from data.repositories.secret_repository import SecretRepository
from services.credentials.broker import get_secret_broker

logger = logging.getLogger("SecretsRouter")


# Cached at module load — reading os.environ on every request is unnecessary.
_INTERNAL_SERVICE_TOKEN: str = os.getenv("INTERNAL_SERVICE_TOKEN", "").strip()


# ── Internal token guard ──────────────────────────────────────────────────────

def _require_internal_token(
    x_internal_token: str | None = Header(default=None),
) -> None:
    """Protect /retrieve behind INTERNAL_SERVICE_TOKEN when set.

    Dev mode (env var absent): no token required.
    Production (env var set): X-Internal-Token header must match exactly.
    """
    if not _INTERNAL_SERVICE_TOKEN:
        return  # dev / no-auth mode
    if not x_internal_token or x_internal_token != _INTERNAL_SERVICE_TOKEN:
        raise HTTPException(
            status_code=403,
            detail="X-Internal-Token header is required to call this endpoint",
        )

router = APIRouter(prefix="/secrets", tags=["SECRETS"])


# ── Request / Response schemas ─────────────────────────────────────────────────

VALID_ROLES = {"github_token", "aws", "gcp", "azure", "ssh_key"}


class StoreSecretRequest(BaseModel):
    migration_id: str = Field(..., min_length=1)
    role: str = Field(..., description="github_token | aws | gcp | azure | ssh_key")
    data: dict[str, Any] = Field(..., description="Secret key-value pairs — NEVER logged")


class SecretMetaResponse(BaseModel):
    secret_ref: str
    migration_id: str
    role: str
    backend: str
    created_at: str
    expires_at: str | None
    last_accessed_at: str | None


class StoreSecretResponse(SecretMetaResponse):
    stored: bool = True


# ── Helper ────────────────────────────────────────────────────────────────────

def _meta_response(record) -> dict:
    return {
        "secret_ref": record.id,
        "migration_id": record.migration_id,
        "role": record.role,
        "backend": record.backend,
        "created_at": record.created_at.isoformat(),
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "last_accessed_at": record.last_accessed_at.isoformat() if record.last_accessed_at else None,
    }


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return getattr(request.client, "host", None)


# ── POST /secrets — store ─────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED, response_model=StoreSecretResponse)
async def store_secret(
    body: StoreSecretRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Store a secret in the broker and create a tracking record.

    Returns metadata including *secret_ref* (UUID). The data dict is NEVER
    echoed back in the response.
    """
    if body.role not in VALID_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid role '{body.role}'. Must be one of: {sorted(VALID_ROLES)}",
        )

    broker = get_secret_broker()
    repo = SecretRepository(db)

    vault_path = broker.migration_path(body.migration_id, body.role)

    try:
        broker.put(vault_path, body.data)
    except Exception as exc:
        logger.error("store_secret: broker.put failed migration=%s role=%s: %s", body.migration_id, body.role, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to store secret in backend")

    record = await repo.upsert(
        migration_id=body.migration_id,
        role=body.role,
        backend=broker.__class__.__name__.lower().replace("broker", ""),
        vault_path=vault_path,
    )

    await repo.audit(
        action="store",
        secret_id=record.id,
        migration_id=body.migration_id,
        role=body.role,
        actor="user",
        ip_address=_client_ip(request),
        extra={"request_id": request.headers.get("x-request-id")},
    )

    return {**_meta_response(record), "stored": True}


# ── GET /secrets/{secret_ref} — metadata only ─────────────────────────────────

@router.get("/{secret_ref}", response_model=SecretMetaResponse)
async def get_secret_metadata(
    secret_ref: str,
    db: AsyncSession = Depends(get_db_session),
):
    """Return metadata for a secret ref. Never returns the value."""
    repo = SecretRepository(db)
    record = await repo.get(secret_ref)
    if not record:
        raise HTTPException(status_code=404, detail=f"Secret ref '{secret_ref}' not found")
    return _meta_response(record)


# ── DELETE /secrets/{secret_ref} ──────────────────────────────────────────────

@router.delete("/{secret_ref}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    secret_ref: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Delete a secret from the broker and the reference DB row."""
    repo = SecretRepository(db)
    record = await repo.get(secret_ref)
    if not record:
        raise HTTPException(status_code=404, detail=f"Secret ref '{secret_ref}' not found")

    broker = get_secret_broker()
    try:
        broker.delete(record.vault_path)
    except Exception as exc:
        logger.warning("delete_secret: broker.delete failed path=%s: %s", record.vault_path, exc)

    await repo.audit(
        action="delete",
        secret_id=record.id,
        migration_id=record.migration_id,
        role=record.role,
        actor="user",
        ip_address=_client_ip(request),
        extra={"request_id": request.headers.get("x-request-id")},
    )

    await repo.delete(secret_ref)


# ── GET /migrations/{migration_id}/secrets — list ────────────────────────────

secrets_migration_router = APIRouter(tags=["SECRETS"])


@secrets_migration_router.get(
    "/migrations/{migration_id}/secrets",
    response_model=list[SecretMetaResponse],
)
async def list_migration_secrets(
    migration_id: str,
    db: AsyncSession = Depends(get_db_session),
):
    """List all secret refs registered for a migration. Values are never returned."""
    repo = SecretRepository(db)
    records = await repo.list_for_migration(migration_id)
    return [_meta_response(r) for r in records]


# ── POST /secrets/{secret_ref}/retrieve — JIT retrieval ──────────────────────

@router.post("/{secret_ref}/retrieve", dependencies=[Depends(_require_internal_token)])
async def retrieve_secret(
    secret_ref: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """JIT secret retrieval for workers.

    Returns the actual secret data dict. Gate this endpoint behind an internal
    network policy or service token in production — it must NOT be reachable
    from the public internet.

    Rejected if the secret is expired.
    """
    repo = SecretRepository(db)
    record = await repo.get(secret_ref)
    if not record:
        raise HTTPException(status_code=404, detail=f"Secret ref '{secret_ref}' not found")

    if record.is_expired():
        raise HTTPException(status_code=410, detail="Secret has expired")

    broker = get_secret_broker()
    try:
        data = broker.get(record.vault_path)
    except Exception as exc:
        logger.error("retrieve_secret: broker.get failed ref=%s: %s", secret_ref, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to retrieve secret from backend")

    if data is None:
        raise HTTPException(status_code=404, detail="Secret not found in backend (may have been deleted)")

    # Touch access timestamp and write audit log
    await repo.touch(secret_ref)
    await repo.audit(
        action="retrieve",
        secret_id=record.id,
        migration_id=record.migration_id,
        role=record.role,
        actor="worker",
        ip_address=_client_ip(request),
        extra={"request_id": request.headers.get("x-request-id")},
    )

    # Return the data — this is the ONLY endpoint that returns a value
    return {"secret_ref": secret_ref, "role": record.role, "data": data}
