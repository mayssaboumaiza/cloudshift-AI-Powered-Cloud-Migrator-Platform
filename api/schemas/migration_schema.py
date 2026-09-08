"""
migration_schema.py - Pydantic schemas for Migration entity.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator
import re

from core.enums import CloudProvider, MigrationStatus

# Allowlist of trusted repository hostnames (SSRF prevention)
_ALLOWED_REPO_HOSTS = {
    "github.com",
    "www.github.com",
    "gitlab.com",
    "www.gitlab.com",
    "bitbucket.org",
    "www.bitbucket.org",
}


def _validate_single_repo_url(v: str) -> str:
    """SSRF guard for a single repo URL. Reused by both repo_url and repo_urls validators."""
    if not v.startswith("https://"):
        raise ValueError(f"repo URL must use HTTPS: {v}")
    match = re.match(r"https://([^/]+)", v)
    if not match:
        raise ValueError(f"repo URL has an invalid format: {v}")
    host = match.group(1).lower().split(":")[0]
    internal = ("localhost", "127.", "10.", "172.", "192.168.", "0.0.0.0", "::1")
    if any(host == h or host.startswith(h) for h in internal):
        raise ValueError(f"repo URL must not point to internal/local addresses: {v}")
    if host not in _ALLOWED_REPO_HOSTS:
        raise ValueError(
            f"repo URL host '{host}' is not allowed. "
            f"Accepted: {', '.join(sorted(_ALLOWED_REPO_HOSTS))}"
        )
    return v


class MigrationBase(BaseModel):
    """Common fields for Migration."""
    repo_url: str = Field(
        ..., min_length=1, max_length=500,
        examples=["https://github.com/owner/repo"],
        description="Primary repo URL. For multi-repo migrations use repo_urls instead.",
    )
    repo_urls: Optional[List[str]] = Field(
        None,
        description="List of repo URLs for multi-repo (platform) migrations. "
                    "When provided, repo_url is derived from the first entry.",
        examples=[["https://github.com/org/backend", "https://github.com/org/infra"]],
    )
    source_cloud: CloudProvider = Field(..., examples=["aws"])
    target_cloud: CloudProvider = Field(..., examples=["gcp"])

    @field_validator("repo_url")
    @classmethod
    def validate_repo_url(cls, v: str) -> str:
        return _validate_single_repo_url(v)

    @field_validator("repo_urls", mode="before")
    @classmethod
    def validate_repo_urls(cls, v: object) -> object:
        if v is None:
            return v
        if not isinstance(v, list):
            raise ValueError("repo_urls must be a list of strings")
        if len(v) == 0:
            raise ValueError("repo_urls must contain at least one URL")
        if len(v) > 20:
            raise ValueError("repo_urls must not exceed 20 repos per migration")
        return [_validate_single_repo_url(url) for url in v]
    github_token: str = Field(
        ..., min_length=1, max_length=200,
        examples=["YOUR_GITHUB_TOKEN"],
        description="Token GitHub avec scope 'repo'. Jamais persisté en clair."
    )
    monthly_budget_usd: Optional[float] = Field(
        None, ge=0,
        description="Monthly budget cap in USD, 0 = no limit"
    )
    target_region: Optional[str] = Field(
        None, max_length=50,
        description="Target cloud region (e.g. eu-west-1, westeurope, europe-west1)",
        examples=["eu-west-1", "westeurope", "europe-west1"]
    )
    data_residency_requirement: Optional[str] = Field(
        None, max_length=100,
        examples=["Union Europeenne"]
    )
    regulatory_constraints: Optional[str] = Field(
        None, max_length=1000,
        examples=["GDPR, HIPAA"],
        description="Deprecated — use compliance_standards instead. Kept for backward compat.",
    )
    compliance_standards: Optional[List[str]] = Field(
        None,
        description="Structured compliance standards. Each value activates deterministic Terraform rules.",
        examples=[["GDPR", "ISO27001"]],
    )
    high_availability_required: Optional[bool] = Field(
        False,
        description="When true, HA blocks are generated and region AZ support is validated first.",
    )
    network_isolation_required: Optional[bool] = Field(
        False,
        description="When true, public_network_access_enabled=false on all resources.",
    )
    # ai_stack is auto-detected from repo — not user input.
    # Kept in response for display only.
    ai_stack: Optional[Dict[str, str]] = Field(
        None,
        description="AI stack auto-detected from repo analysis. Read-only.",
    )


class MigrationCreate(MigrationBase):
    """Schema for POST /migrations/"""
    credentials_pre_validated: Optional[bool] = False
    owned_repos: Optional[List[str]] = Field(
        None,
        description=(
            "Repos for which the user confirms they own and manage the database "
            "(Terraform/Bicep IaC present). Nodes from these repos get "
            "owns_resource=True, bypassing the external-connection filter in Agent 01."
        ),
        examples=[["https://github.com/org/infra"]],
    )

    @field_validator("owned_repos", mode="before")
    @classmethod
    def validate_owned_repos(cls, v: object) -> object:
        if v is None:
            return v
        if not isinstance(v, list):
            raise ValueError("owned_repos must be a list of strings")
        return [_validate_single_repo_url(url) for url in v]


class MigrationUpdatePartial(BaseModel):
    """Schema for PATCH /migrations/{id} - all fields optional."""
    repo_url: Optional[str] = Field(None, min_length=1, max_length=500)
    source_cloud: Optional[CloudProvider] = None
    target_cloud: Optional[CloudProvider] = None
    monthly_budget_usd: Optional[float] = Field(None, ge=0)
    target_region: Optional[str] = Field(None, max_length=50)
    data_residency_requirement: Optional[str] = Field(None, max_length=100)
    regulatory_constraints: Optional[str] = Field(None, max_length=1000)
    compliance_standards: Optional[List[str]] = None
    high_availability_required: Optional[bool] = None
    network_isolation_required: Optional[bool] = None
    ai_stack: Optional[Dict[str, str]] = None
    credentials_pre_validated: Optional[bool] = Field(
        None,
        description="Stored inside artifacts.credentials_pre_validated. "
                    "Set to true only after both github and cloud creds are confirmed in Vault.",
    )


class PartialRejectRequest(BaseModel):
    """Schema for POST /migrations/{id}/partial-reject."""
    rejected_services: List[Dict[str, Any]] = Field(
        ...,
        description="List of rejected service dicts from migration_plan.resources",
    )
    rejection_reasons: Dict[str, str] = Field(
        default_factory=dict,
        description="Map of source_service → rejection reason string",
        examples=[{"aws:rds": "trop cher", "aws:lambda": "préférons ECS"}],
    )


class MigrationResponse(MigrationBase):
    """Schema for GET responses."""
    id: str
    status: MigrationStatus
    github_token: Optional[str] = Field(None, exclude=True)
    dependency_graph: Optional[Dict[str, Any]] = None
    migration_plan: Optional[Dict[str, Any]] = None
    preview_report: Optional[str] = None
    iac_output: Optional[str] = None
    generated_files: Optional[List[str]] = None
    deployment_status: Optional[str] = None
    errors: Optional[List[str]] = None
    warnings: Optional[List[str]] = None
    artifacts: Optional[Dict[str, Any]] = None
    thread_id: Optional[str] = None
    current_step: Optional[str] = None
    iac_validation_success: Optional[bool] = None
    needs_human_escalation: Optional[bool] = None
    correction_counts: Optional[Dict[str, Any]] = None
    intent_issues: Optional[List[Any]] = None
    # GitHub output repo — populated by publish_github_node after a successful deploy
    github_pr_url: Optional[str] = None
    github_repo_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MigrationSummaryResponse(BaseModel):
    """Lightweight response for list endpoints."""
    id: str
    repo_url: str
    repo_urls: Optional[List[str]] = None
    source_cloud: CloudProvider
    target_cloud: CloudProvider
    status: MigrationStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AnalysisResponse(BaseModel):
    """Response after triggering analysis."""
    id: str
    status: MigrationStatus
    dependency_graph: Optional[Dict[str, Any]] = None
    migration_plan: Optional[Dict[str, Any]] = None
    preview_report: Optional[str] = None
    errors: Optional[List[str]] = None

    model_config = ConfigDict(from_attributes=True)


class DecisionResponse(BaseModel):
    """Response after accept/reject."""
    id: str
    status: MigrationStatus
    deployment_status: Optional[str] = None
    generated_files: Optional[List[str]] = None
    errors: Optional[List[str]] = None


class ServiceItem(BaseModel):
    """One selected cloud service from the frontend checklist."""
    service: str = Field(..., min_length=1, max_length=80, examples=["s3", "rds", "lambda"])
    cloud: str   = Field(..., pattern=r"^(aws|gcp|azure)$", examples=["aws"])
    type: Optional[str] = Field(None, examples=["storage", "database", "compute"])


class ServiceSelectionRequest(BaseModel):
    """Body for POST /migrations/{id}/submit-services."""
    services: List[ServiceItem] = Field(
        ..., min_length=1,
        description="At least one service must be selected.",
    )


class ServiceSelectionResponse(BaseModel):
    """Response after submitting service selection."""
    id: str
    status: MigrationStatus
    dependency_graph: Optional[Dict[str, Any]] = None
    errors: Optional[List[str]] = None

    model_config = ConfigDict(from_attributes=True)


# ── GitHub input layer schemas ────────────────────────────────────────────────

class GitHubAnalyzeRequest(BaseModel):
    """Body pour POST /github/analyze — point d'entrée du pipeline."""
    github_token: str = Field(
        ..., min_length=1, max_length=200,
        description="Token GitHub avec scope 'repo'",
    )
    github_url: str = Field(
        ..., min_length=1, max_length=500,
        description="URL du compte GitHub (ex: github.com/acme)",
        examples=["github.com/acme", "https://github.com/acme"],
    )
    cloud_source: str = Field(
        ..., description="Cloud source : AWS / Azure / GCP / On-premise",
        examples=["AWS", "Azure", "GCP", "On-premise"],
    )
    cloud_target: str = Field(
        ..., description="Cloud cible : AWS / Azure / GCP",
        examples=["AWS", "Azure", "GCP"],
    )
    budget_max: float = Field(
        ..., ge=0,
        description="Budget mensuel maximum en EUR",
        examples=[500.0],
    )
    target_region: str = Field(
        ..., min_length=1, max_length=100,
        description="Région cible (ex: eu-west-1, westeurope)",
        examples=["eu-west-1"],
    )
    data_residency: str = Field(
        ..., min_length=1, max_length=200,
        description="Exigence de résidence des données (ex: France, EU)",
        examples=["EU", "France"],
    )
    selected_repos: List[str] = Field(
        ..., min_length=1,
        description="full_names des repos sélectionnés (ex: ['acme/auth', 'acme/api'])",
        examples=[["acme/auth", "acme/api"]],
    )


class GitHubReposRequest(BaseModel):
    """Query params pour GET /github/repos."""
    github_token: str = Field(..., min_length=1, max_length=200)
    github_url: str = Field(..., min_length=1, max_length=500)


class ServiceInfo(BaseModel):
    """Un service détecté dans le repo, prêt pour stack_analyzer."""
    service_name: str
    repo: str
    path: str
    local_path: str


class GitHubAnalyzeResponse(BaseModel):
    """Réponse de POST /github/analyze."""
    case: str = Field(..., description="'A' = repo unique (1 service), 'B' = multi-services (multi-repos ou monorepo)")
    services: List[ServiceInfo]
    migration_context: Dict[str, Any]
    analysis: Optional[Dict[str, Any]] = Field(
        None,
        description="Résultat agrégé de stack_analyzer (dependency_graph, etc.)",
    )

