"""
services/change_detector.py — Path 1 vs Path 2 decision engine.

Called at the START of every pipeline run to decide:

  Path 1 (full)        — no previous successful migration for (repo, target_cloud)
                         OR git SHA cannot be retrieved (safe fallback)

  Path 2 (incremental) — previous successful migration exists AND git SHA changed.
                         Returns a structured diff: new_resources, modified_files, etc.

  Path 2 / no-op      — previous successful migration exists AND git SHA is identical.
                         Pipeline emits "already_up_to_date" immediately.

Public API
----------
  detect_migration_mode(repo_url, target_cloud, github_token, db_session)
      -> ModeResult

The result is injected into the LangGraph state so every downstream node
(Agent 01, Agent 02, Agent 03) can adapt their scope accordingly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from services.stack_analyzer.github_tools import _get_current_sha, _get_repo_diff

logger = logging.getLogger("ChangeDetector")


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ModeResult:
    """Returned by detect_migration_mode() and stored in pipeline state."""

    mode: str                        # "full" | "incremental" | "up_to_date"
    current_sha: str | None = None   # HEAD SHA at detection time
    previous_sha: str | None = None  # SHA of last successful migration
    previous_migration_id: str | None = None

    # Populated only for mode == "incremental"
    added_files: list[str]    = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    removed_files: list[str]  = field(default_factory=list)
    new_resources: list[str]  = field(default_factory=list)

    # Human-readable summary for the frontend
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "current_sha": self.current_sha,
            "previous_sha": self.previous_sha,
            "previous_migration_id": self.previous_migration_id,
            "added_files": self.added_files,
            "modified_files": self.modified_files,
            "removed_files": self.removed_files,
            "new_resources": self.new_resources,
            "summary": self.summary,
        }


# ── Main entry point ──────────────────────────────────────────────────────────

async def detect_migration_mode(
    repo_url: str,
    target_cloud: str,
    github_token: str,
    db_session: Any,
) -> ModeResult:
    """Determine whether this run is Path 1 (full) or Path 2 (incremental).

    Algorithm:
    1. Fetch current git SHA from GitHub API.
    2. Look for a previous COMPLETED migration with the same (repo_url, target_cloud).
    3. Compare SHAs:
       - No previous migration          → Path 1  (full)
       - SHA unchanged                  → up_to_date (skip)
       - SHA changed                    → Path 2  (incremental + diff)
       - Cannot get SHA (API error)     → Path 1  (safe fallback)
    """
    # Step 1 — get current HEAD SHA
    current_sha = _get_current_sha(repo_url, github_token)
    if not current_sha:
        logger.warning("ChangeDetector: cannot get SHA for %s — falling back to full", repo_url)
        return ModeResult(
            mode="full",
            summary="Premier lancement ou SHA GitHub non disponible — génération complète.",
        )

    # Step 2 — find previous successful migration for (repo, target_cloud)
    previous = await _find_previous_migration(repo_url, target_cloud, db_session)

    if previous is None:
        logger.info("ChangeDetector: no previous migration for %s→%s — Path 1 (full)", repo_url, target_cloud)
        return ModeResult(
            mode="full",
            current_sha=current_sha,
            summary="Première migration pour ce dépôt et ce cloud cible — génération complète.",
        )

    previous_sha = previous.get("last_git_sha")
    previous_id  = previous.get("id")

    if not previous_sha:
        # Previous migration exists but SHA was not recorded (ran before Path 2 was added)
        logger.info("ChangeDetector: previous migration %s has no SHA — Path 1 (full)", previous_id)
        return ModeResult(
            mode="full",
            current_sha=current_sha,
            previous_migration_id=previous_id,
            summary="Migration précédente sans SHA enregistré — génération complète.",
        )

    # Step 3 — compare SHAs
    if previous_sha == current_sha:
        logger.info(
            "ChangeDetector: SHA unchanged (%s) — up_to_date", current_sha[:8]
        )
        return ModeResult(
            mode="up_to_date",
            current_sha=current_sha,
            previous_sha=previous_sha,
            previous_migration_id=previous_id,
            summary=f"Aucun changement détecté depuis la dernière migration (SHA {current_sha[:8]}).",
        )

    # Step 4 — SHAs differ → compute diff
    logger.info(
        "ChangeDetector: SHA changed %s → %s — Path 2 (incremental)",
        previous_sha[:8], current_sha[:8],
    )
    diff = _get_repo_diff(repo_url, previous_sha, current_sha, github_token)

    added_files    = diff.get("added_files", [])
    modified_files = diff.get("modified_files", [])
    removed_files  = diff.get("removed_files", [])
    new_resources  = diff.get("new_resources", [])

    n_added    = len(added_files)
    n_modified = len(modified_files)
    n_removed  = len(removed_files)
    n_resources = len(new_resources)

    parts = []
    if n_resources:
        parts.append(f"{n_resources} nouvelle(s) ressource(s) cloud détectée(s)")
    if n_added:
        parts.append(f"{n_added} fichier(s) ajouté(s)")
    if n_modified:
        parts.append(f"{n_modified} fichier(s) modifié(s)")
    if n_removed:
        parts.append(f"{n_removed} fichier(s) supprimé(s)")

    summary = "Changements détectés : " + ", ".join(parts) + "." if parts else "Changements détectés (diff disponible)."

    return ModeResult(
        mode="incremental",
        current_sha=current_sha,
        previous_sha=previous_sha,
        previous_migration_id=previous_id,
        added_files=added_files,
        modified_files=modified_files,
        removed_files=removed_files,
        new_resources=new_resources,
        summary=summary,
    )


# ── DB helper ─────────────────────────────────────────────────────────────────

async def _find_previous_migration(
    repo_url: str,
    target_cloud: str,
    db_session: Any,
) -> dict | None:
    """Return the most recent Completed/Exported migration for (repo, target_cloud).

    Returns a dict with {id, last_git_sha, migration_plan, ...} or None.
    """
    try:
        from sqlalchemy import select, or_
        from data.models.migration_model import Migration

        stmt = (
            select(Migration)
            .where(
                Migration.repo_url == repo_url,
                Migration.target_cloud == target_cloud,
                or_(
                    Migration.status == "Completed",
                    Migration.status == "Exported",
                ),
            )
            .order_by(Migration.created_at.desc())
            .limit(1)
        )
        result = await db_session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return {
            "id": row.id,
            "last_git_sha": row.last_git_sha,
            "migration_plan": row.migration_plan,
            "generated_files": row.generated_files,
        }
    except Exception as exc:
        logger.error("ChangeDetector._find_previous_migration: %s", exc)
        return None


# ── Sync-status helper (used by GET /migrations/{id}/sync-status) ─────────────

async def get_sync_status(
    migration_id: str,
    github_token: str,
    db_session: Any,
) -> dict[str, Any]:
    """Check whether the repo for an existing migration has changed since it ran.

    Returns:
      { up_to_date: bool, current_sha, last_sha, summary, diff? }
    """
    try:
        from sqlalchemy import select
        from data.models.migration_model import Migration

        stmt = select(Migration).where(Migration.id == migration_id)
        result = await db_session.execute(stmt)
        m = result.scalar_one_or_none()
        if m is None:
            return {"error": "Migration not found"}

        current_sha = _get_current_sha(m.repo_url, github_token or m.github_token or "")
        if not current_sha:
            return {"error": "Cannot reach GitHub repository"}

        if not m.last_git_sha:
            return {
                "up_to_date": False,
                "current_sha": current_sha,
                "last_sha": None,
                "summary": "SHA de référence non disponible — relancez pour établir la baseline.",
            }

        if current_sha == m.last_git_sha:
            return {
                "up_to_date": True,
                "current_sha": current_sha,
                "last_sha": m.last_git_sha,
                "summary": f"Infrastructure à jour (SHA {current_sha[:8]}).",
            }

        diff = _get_repo_diff(m.repo_url, m.last_git_sha, current_sha,
                              github_token or m.github_token or "")
        return {
            "up_to_date": False,
            "current_sha": current_sha,
            "last_sha": m.last_git_sha,
            "summary": f"{diff.get('total_files_changed', '?')} fichier(s) modifié(s) depuis la dernière migration.",
            "diff": diff,
        }
    except Exception as exc:
        logger.error("get_sync_status: %s", exc)
        return {"error": str(exc)}
