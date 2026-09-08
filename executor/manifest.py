"""
executor/manifest.py — DeploymentManifest: typed contract between Agent 03 and the TerraformRunner.

Agent 03 builds a manifest instead of executing terraform. The runner picks it up
from the job queue and runs INIT → PLAN → APPROVAL → APPLY → VERIFY in a
separate process, never inside the FastAPI worker.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CloudProvider(str, Enum):
    aws = "aws"
    gcp = "gcp"
    azure = "azure"


class TerraformBackendConfig(BaseModel):
    """Optional remote backend (S3, GCS, azurerm). Omit for local state."""
    type: str = Field(..., description="s3 | gcs | azurerm | local")
    config: dict[str, Any] = Field(default_factory=dict)


class DeploymentManifest(BaseModel):
    """
    Describes everything the TerraformRunner needs to execute a migration deployment.

    Produced by Agent 03 (agent_03.py) and stored in MigrationState.deployment_manifest.
    Serialised to JSON and written to runner_jobs.manifest at enqueue time.

    Credential fields contain ONLY the SecretBroker vault_path (never plaintext values).
    The runner fetches the actual credentials JIT from the broker at APPLY stage.
    """

    # ── Identity ───────────────────────────────────────────────────────────────
    migration_id: str = Field(..., description="FK to migrations.id")
    thread_id: str = Field(..., description="LangGraph thread_id for SSE event routing")

    # ── Terraform workspace ────────────────────────────────────────────────────
    work_dir: str = Field(
        ...,
        description="Absolute path to the per-migration Terraform working directory. "
                    "Created by enqueue_deploy node before the job is written to the queue.",
    )
    tf_files: list[str] = Field(
        default_factory=list,
        description="Relative paths of .tf files inside work_dir (informational; runner scans dir).",
    )

    # ── Target cloud ───────────────────────────────────────────────────────────
    provider: CloudProvider = Field(..., description="Target cloud provider")
    target_region: str = Field(..., description="Target deployment region")

    # ── Credentials (vault paths only — NO plaintext values) ──────────────────
    secret_refs: dict[str, str] = Field(
        default_factory=dict,
        description="role → secret_ref mapping. Runner calls SecretBroker.get(vault_path) at APPLY. "
                    "Example: {'aws': 'migrations/abc/aws', 'github_token': 'migrations/abc/github_token'}",
    )

    # ── Approval gate ─────────────────────────────────────────────────────────
    auto_approve: bool = Field(
        default=False,
        description="If True, skip AWAITING_APPROVAL and proceed directly to APPLY "
                    "after the plan is generated. Default False — always require human sign-off.",
    )

    # ── Backend ───────────────────────────────────────────────────────────────
    backend: TerraformBackendConfig | None = Field(
        default=None,
        description="Remote backend config. None = local .tfstate (dev/test).",
    )

    # ── Extra env for the terraform subprocess ────────────────────────────────
    extra_env: dict[str, str] = Field(
        default_factory=dict,
        description="Additional environment variables injected into the terraform subprocess "
                    "(e.g. TF_LOG=DEBUG). Must NOT contain credentials — use secret_refs instead.",
    )

    # ── Budget guardrail ──────────────────────────────────────────────────────
    budget_usd: float | None = Field(
        default=None,
        description="If set, infracost estimate must be ≤ this value or the job is rejected.",
    )

    # ── Agent 03 artifacts (informational — already written to work_dir) ──────
    deploy_script: str | None = Field(
        default=None,
        description="Relative path to deploy.sh inside work_dir.",
    )
    cicd_yaml: str | None = Field(
        default=None,
        description="Relative path to deploy.yml inside work_dir.",
    )

    # ── Destroy mode ──────────────────────────────────────────────────────────
    destroy: bool = Field(
        default=False,
        description="If True, run terraform destroy instead of apply. "
                    "Used by the infra-destroy endpoint to tear down deployed resources.",
    )

    model_config = ConfigDict(use_enum_values=True)
