"""
credentials_router.py — Endpoints de validation des credentials cloud.

Permet au frontend de valider les credentials avant de lancer une migration.
Tous les validateurs sont non-destructifs (aucune ressource créée).

Endpoints:
  POST /credentials/validate          — endpoint unifié (provider + credentials)
  POST /credentials/validate/github   — valide un PAT GitHub
  POST /credentials/validate/aws      — valide des clés AWS (STS + IAM simulator)
  POST /credentials/validate/gcp      — valide un service account GCP
  POST /credentials/validate/azure    — valide un Service Principal Azure
  POST /credentials/store             — stocke les credentials validés dans le vault
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Any

from api.auth.api_key import require_api_key

logger = logging.getLogger("CredentialsRouter")

# All credentials endpoints require a valid X-API-Key header.
# In dev mode (API_KEY env var unset) auth is bypassed automatically by require_api_key.
router = APIRouter(
    prefix="/credentials",
    tags=["CREDENTIALS"],
    dependencies=[Depends(require_api_key)],
)


# ── Request / Response models ─────────────────────────────────────────────────

class GitHubValidateRequest(BaseModel):
    token: str = Field(..., min_length=1, description="GitHub PAT or fine-grained token")
    repo: str | None = Field(None, description="owner/name — verify repo access if provided")


class AWSValidateRequest(BaseModel):
    access_key: str = Field(..., min_length=16)
    secret_key: str = Field(..., min_length=1)
    region: str = Field(default="us-east-1")
    session_token: str | None = None


class GCPValidateRequest(BaseModel):
    service_account_json: dict = Field(..., description="GCP service account JSON key")
    project_id: str = Field(..., min_length=1)


class AzureValidateRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1)
    client_id: str = Field(..., min_length=1)
    client_secret: str = Field(..., min_length=1)
    subscription_id: str = Field(..., min_length=1)


class UnifiedValidateRequest(BaseModel):
    provider: str = Field(..., description="aws | gcp | azure | github")
    credentials: dict[str, Any] = Field(..., description="Provider-specific credentials dict")


class StoreCredentialsRequest(BaseModel):
    user_id: str = Field(..., min_length=1, description="migration_id (used as vault path scope)")
    provider: str = Field(..., description="aws | gcp | azure | github")
    credentials: dict[str, Any] = Field(..., description="Credentials to encrypt and store")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/validate/github", status_code=status.HTTP_200_OK)
async def validate_github(body: GitHubValidateRequest):
    """Validate a GitHub token and optionally check repo access."""
    from services.credentials.validators import validate_github_token
    result = validate_github_token(body.token, expected_repo=body.repo)
    return result


@router.post("/validate/aws", status_code=status.HTTP_200_OK)
async def validate_aws(body: AWSValidateRequest):
    """Validate AWS credentials via STS GetCallerIdentity + IAM simulator."""
    from services.credentials.validators import validate_aws_credentials
    result = validate_aws_credentials(
        access_key=body.access_key,
        secret_key=body.secret_key,
        target_region=body.region,
        session_token=body.session_token,
    )
    return result


@router.post("/validate/gcp", status_code=status.HTTP_200_OK)
async def validate_gcp(body: GCPValidateRequest):
    """Validate GCP service account credentials via Project + testIamPermissions."""
    from services.credentials.validators import validate_gcp_credentials
    result = validate_gcp_credentials(
        service_account_json=body.service_account_json,
        project_id=body.project_id,
    )
    return result


@router.post("/validate/azure", status_code=status.HTTP_200_OK)
async def validate_azure(body: AzureValidateRequest):
    """Validate Azure Service Principal credentials via Subscription + role listing."""
    from services.credentials.validators import validate_azure_credentials
    result = validate_azure_credentials(
        tenant_id=body.tenant_id,
        client_id=body.client_id,
        client_secret=body.client_secret,
        subscription_id=body.subscription_id,
    )
    return result


@router.post("/validate", status_code=status.HTTP_200_OK)
async def validate_unified(body: UnifiedValidateRequest):
    """Unified credentials validation endpoint.

    Body: { provider: "aws"|"gcp"|"azure"|"github", credentials: {...} }
    Dispatches to the per-provider validator. Credentials are NEVER logged.
    """
    provider = body.provider.lower()
    creds = body.credentials

    if provider == "github":
        from services.credentials.validators import validate_github_token
        token = creds.get("token")
        if not token:
            raise HTTPException(status_code=422, detail="credentials.token is required for GitHub")
        return validate_github_token(token, expected_repo=creds.get("repo"))

    if provider == "aws":
        from services.credentials.validators import validate_aws_credentials
        access_key = creds.get("access_key")
        secret_key = creds.get("secret_key")
        if not access_key or not secret_key:
            raise HTTPException(status_code=422, detail="credentials.access_key and secret_key are required for AWS")
        return validate_aws_credentials(
            access_key=access_key,
            secret_key=secret_key,
            target_region=creds.get("region", "us-east-1"),
            session_token=creds.get("session_token"),
        )

    if provider == "gcp":
        from services.credentials.validators import validate_gcp_credentials
        sa_json = creds.get("service_account_json")
        project_id = creds.get("project_id")
        if not sa_json or not project_id:
            raise HTTPException(status_code=422, detail="credentials.service_account_json and project_id are required for GCP")
        return validate_gcp_credentials(service_account_json=sa_json, project_id=project_id)

    if provider == "azure":
        from services.credentials.validators import validate_azure_credentials
        required = ["tenant_id", "client_id", "client_secret", "subscription_id"]
        missing = [k for k in required if not creds.get(k)]
        if missing:
            raise HTTPException(status_code=422, detail=f"Missing Azure credentials: {missing}")
        return validate_azure_credentials(
            tenant_id=creds["tenant_id"],
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            subscription_id=creds["subscription_id"],
        )

    raise HTTPException(status_code=422, detail=f"Unknown provider '{provider}'. Must be: aws, gcp, azure, github")


@router.post("/azure/regions", status_code=status.HTTP_200_OK)
async def get_azure_allowed_regions(body: AzureValidateRequest):
    """Return the list of Azure regions allowed by the subscription's Policy assignments.

    Queries Azure Policy assignments for location restrictions. If no restriction
    policy is found, returns all known Azure regions. Used by the frontend to show
    only regions the subscription can actually deploy into.
    """
    from services.credentials.validators import get_azure_allowed_regions as _get_regions
    regions = _get_regions(
        tenant_id=body.tenant_id,
        client_id=body.client_id,
        client_secret=body.client_secret,
        subscription_id=body.subscription_id,
    )
    return {"regions": regions}


@router.post("/store", status_code=status.HTTP_200_OK)
async def store_credentials(body: StoreCredentialsRequest):
    """Store validated credentials via SecretBroker and track the reference in DB.

    *user_id* is treated as the migration_id for vault path scoping.
    Returns *secret_ref* — an opaque UUID clients can use to check metadata.
    Credentials are NEVER logged or returned.
    """
    from configuration.database import async_session
    from data.repositories.secret_repository import SecretRepository
    from services.credentials.broker import get_secret_broker

    # Map provider name → canonical role
    role_map = {
        "github": "github_token",
        "github_token": "github_token",
        "aws": "aws",
        "gcp": "gcp",
        "azure": "azure",
    }
    role = role_map.get(body.provider.lower())
    if not role:
        raise HTTPException(status_code=422, detail=f"Unknown provider '{body.provider}'")

    migration_id = body.user_id
    broker = get_secret_broker()
    vault_path = broker.migration_path(migration_id, role)

    try:
        broker.put(vault_path, body.credentials)
    except Exception as exc:
        logger.error("store_credentials: broker.put failed migration=%s role=%s: %s", migration_id, role, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to store credentials in backend")

    # Track in DB — use a fresh session outside FastAPI DI (this endpoint has no Depends)
    secret_ref: str | None = None
    try:
        async with async_session() as db:
            repo = SecretRepository(db)
            record = await repo.upsert(
                migration_id=migration_id,
                role=role,
                backend=broker.__class__.__name__.lower().replace("broker", ""),
                vault_path=vault_path,
            )
            await repo.audit(
                action="store",
                secret_id=record.id,
                migration_id=migration_id,
                role=role,
                actor="user",
                extra={"provider": body.provider},
            )
            secret_ref = record.id
    except Exception as exc:
        # DB tracking failure is non-fatal — secret is already in vault
        logger.warning("store_credentials: DB tracking failed migration=%s role=%s: %s", migration_id, role, type(exc).__name__)

    return {
        "stored": True,
        "provider": body.provider,
        "user_id": body.user_id,
        "secret_ref": secret_ref,
    }
