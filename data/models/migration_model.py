"""
migration_model.py - SQLAlchemy ORM model for Migration entity.
"""
from typing import Optional
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, Enum, Float, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from configuration.database import Base
from core.enums import MigrationStatus, CloudProvider


class Migration(Base):
    """ORM Model for a cloud migration job."""
    __tablename__ = "migrations"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Owner — nullable for backward-compat with pre-IAM rows (migration 023).
    # New rows always have user_id set by the service layer from the JWT token.
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Required fields
    repo_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        index=True,
    )
    source_cloud: Mapped[str] = mapped_column(
        Enum(CloudProvider),
        nullable=False,
    )
    target_cloud: Mapped[str] = mapped_column(
        Enum(CloudProvider),
        nullable=False,
    )
    github_token: Mapped[Optional[str]] = mapped_column(
        String, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(MigrationStatus),
        default=MigrationStatus.CREATED,
        nullable=False,
        index=True,
    )

    # Constraints (optional)
    monthly_budget_usd: Mapped[float | None] = mapped_column("budget_max_mensuel", Float, nullable=True)
    timeline: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_region: Mapped[str | None] = mapped_column(String(50), nullable=True)
    data_residency_requirement: Mapped[str | None] = mapped_column(String(100), nullable=True)
    regulatory_constraints: Mapped[str | None] = mapped_column(Text, nullable=True)  # kept for backward compat

    # Structured compliance constraints (replaces free-text regulatory_constraints)
    compliance_standards: Mapped[list | None] = mapped_column(JSON, nullable=True)
    high_availability_required: Mapped[bool | None] = mapped_column(
        "high_availability_required", nullable=True, default=False,
    )
    network_isolation_required: Mapped[bool | None] = mapped_column(
        "network_isolation_required", nullable=True, default=False,
    )

    # Multi-repo support: list of repo URLs (None = single repo via repo_url)
    repo_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # AI Stack (auto-detected from repo — not user input)
    ai_stack: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Agent outputs (JSON blobs)
    migration_priority: Mapped[list | None] = mapped_column(JSON, nullable=True)
    dependency_graph: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    migration_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    preview_report: Mapped[str | None] = mapped_column(Text, nullable=True)
    iac_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_files: Mapped[list | None] = mapped_column(JSON, nullable=True)
    deployment_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    errors: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    artifacts: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Workflow state
    thread_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        unique=True,
        index=True,
    )

    # ── Incremental migration (Path 2) ───────────────────────────────────────
    # SHA of the last commit that was successfully analyzed. Compared to the
    # current HEAD at the start of each pipeline run to detect repo changes.
    last_git_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # 'full' = Path 1 (first run or new target cloud)
    # 'incremental' = Path 2 (same repo+target, changes detected)
    migration_mode: Mapped[str | None] = mapped_column(String(20), nullable=True, default="full")

    # JSON summary of what changed between last_git_sha and HEAD:
    # {new_resources, modified_files, removed_files, current_sha, previous_sha}
    detected_changes: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # GitHub output repo + PR — set by publish_github_node after successful pipeline
    github_pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    github_repo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
