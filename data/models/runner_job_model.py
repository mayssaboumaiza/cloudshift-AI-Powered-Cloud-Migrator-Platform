"""
data/models/runner_job_model.py — ORM models for the Terraform Runner job queue.

RunnerJob     : one row per deployment attempt. Claimed by a worker via SKIP LOCKED.
DeploymentLog : append-only stdout/stderr lines from the terraform subprocess.
                Written by the runner; read by the /runner/{job_id}/logs endpoint.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from configuration.database import Base


class RunnerJob(Base):
    """One deployment job per migration.

    State machine:
      pending → claimed → init → plan → awaiting_approval → apply → verify → done
                                                                           ↘ failed
    Each transition is an UPDATE by the worker; never by the API process.
    """

    __tablename__ = "runner_jobs"
    __table_args__ = (
        UniqueConstraint("migration_id", name="uq_runner_jobs_migration_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    migration_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("migrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Serialised DeploymentManifest JSON
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False)

    # pending | claimed | init | plan | awaiting_approval | apply | verify | done | failed
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)

    # Worker identity — set at claim time; NULL when pending
    worker_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Last error message (NEVER contains credentials)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Terraform plan output summary (cost, changes count — never secrets)
    plan_summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Final terraform apply outputs dict
    apply_outputs: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Retry counter
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DeploymentLog(Base):
    """Append-only stderr/stdout stream from the terraform subprocess.

    Rows are written by the runner during execution and streamed back to the
    client via the /runner/{job_id}/logs SSE endpoint.
    NEVER contains credential values.
    """

    __tablename__ = "deployment_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("runner_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Stage that produced this line
    stage: Mapped[str] = mapped_column(String(30), nullable=False)

    # stdout | stderr | system
    stream: Mapped[str] = mapped_column(String(10), nullable=False, default="stdout")

    line: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
