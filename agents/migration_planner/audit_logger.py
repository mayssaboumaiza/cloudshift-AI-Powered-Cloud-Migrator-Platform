"""
audit_logger.py — Append-only migration decision audit log.

Logs every AWS→Azure service mapping decision produced by Agent 01 into
the migration_audit PostgreSQL table (created by Alembic migration 014).

Public API:
    log_migration_decisions(migration_id, migration_plan)      → int (rows inserted)
    get_audit_report(migration_id)                             → list[dict]
    detect_planning_errors(migration_id)                       → list[dict]

Feedback loop: detect_planning_errors() identifies decisions where:
  - intent_valid=False (IntentValidator flagged a category mismatch)
  - ontology_score is much lower than equivalence_score (possible hallucination)
  - strategy is REFACTOR but equivalence_score > 0.85 (threshold violation)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from rag.rag_config import get_pg_connection

logger = logging.getLogger("AuditLogger")

# Thresholds for post-hoc error detection
_ONTOLOGY_GAP_THRESHOLD = 0.20   # flag if embedding score exceeds ontology score by this
_STRATEGY_REFACTOR_MAX_EQ = 0.85  # REFACTOR with equivalence > this is suspicious


def log_migration_decisions(migration_id: str, migration_plan: dict) -> int:
    """Insert one audit row per service in the migration plan.

    Resilient: failures log a warning and return 0 so the pipeline continues.
    Should be called after Agent 01 finalises the migration_plan.
    """
    if not migration_plan:
        return 0

    services = migration_plan.get("services") or migration_plan.get("resources") or []
    if not services:
        return 0

    rows: list[tuple] = []
    for svc in services:
        service_name    = svc.get("service_name") or svc.get("resource_name") or "unknown"
        source_provider = _infer_provider(svc.get("source_service", ""))
        source_service  = svc.get("source_service") or ""
        target_provider = _infer_provider(svc.get("target_service", ""))
        target_service  = svc.get("target_service") or ""
        strategy_7r     = (svc.get("strategy_7r") or svc.get("strategy") or "UNKNOWN").upper()
        equivalence     = _safe_float(svc.get("equivalence_score"))
        saw_score       = _safe_float(svc.get("score"))
        ontology_score  = _safe_float(svc.get("ontology_score"))
        confidence      = _safe_float(svc.get("confidence"))
        intent_valid    = svc.get("intent_valid")  # set by IntentValidator before call
        intent_mismatch = svc.get("intent_mismatch") or None
        reasoning       = svc.get("reasoning") or ""
        if isinstance(reasoning, dict):
            import json
            reasoning = json.dumps(reasoning)

        rows.append((
            migration_id, service_name,
            source_provider, source_service,
            target_provider, target_service,
            strategy_7r, equivalence, saw_score, ontology_score,
            confidence, intent_valid, intent_mismatch, reasoning,
        ))

    try:
        with get_pg_connection() as conn:
            cur = conn.cursor()
            cur.executemany("""
                INSERT INTO migration_audit
                  (migration_id, service_name,
                   source_provider, source_service,
                   target_provider, target_service,
                   strategy_7r, equivalence_score, saw_score, ontology_score,
                   confidence, intent_valid, intent_mismatch, reasoning)
                VALUES (%s,%s, %s,%s, %s,%s, %s,%s,%s,%s, %s,%s,%s,%s)
            """, rows)
            conn.commit()
            inserted = len(rows)
            logger.info("[Audit] Logged %d migration decisions for migration_id=%s", inserted, migration_id)
            return inserted
    except Exception as exc:
        logger.warning("[Audit] log_migration_decisions failed: %s", exc)
        return 0


def get_audit_report(migration_id: str) -> list[dict[str, Any]]:
    """Retrieve the full audit log for a migration run."""
    try:
        with get_pg_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT service_name, source_provider, source_service,
                       target_provider, target_service, strategy_7r,
                       equivalence_score, saw_score, ontology_score,
                       confidence, intent_valid, intent_mismatch,
                       reasoning, created_at
                FROM migration_audit
                WHERE migration_id = %s
                ORDER BY created_at
            """, (migration_id,))
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            return [dict(zip(cols, row)) for row in rows]
    except Exception as exc:
        logger.warning("[Audit] get_audit_report failed: %s", exc)
        return []


def detect_planning_errors(migration_id: str) -> list[dict[str, Any]]:
    """Post-hoc analysis: flag suspicious mapping decisions.

    Detects:
    1. intent_valid=False — IntentValidator found a category mismatch
    2. REFACTOR with equivalence_score > threshold (should be REPLATFORM/REHOST)
    3. Large embedding vs ontology gap (possible hallucinated mapping)
    """
    report = get_audit_report(migration_id)
    errors: list[dict] = []

    for row in report:
        reasons: list[str] = []

        if row.get("intent_valid") is False:
            reasons.append(
                f"Intent mismatch: {row.get('intent_mismatch', 'category mismatch detected')}"
            )

        eq = row.get("equivalence_score")
        strategy = row.get("strategy_7r", "")
        if strategy == "REFACTOR" and eq is not None and eq > _STRATEGY_REFACTOR_MAX_EQ:
            reasons.append(
                f"Strategy=REFACTOR but equivalence_score={eq:.3f} > {_STRATEGY_REFACTOR_MAX_EQ} "
                "(should be REPLATFORM or REHOST)"
            )

        onto = row.get("ontology_score")
        if eq is not None and onto is not None and (eq - onto) > _ONTOLOGY_GAP_THRESHOLD:
            reasons.append(
                f"Embedding score={eq:.3f} >> ontology score={onto:.3f} "
                f"(gap={eq - onto:.3f} > {_ONTOLOGY_GAP_THRESHOLD}): "
                "possible hallucinated or incorrect target mapping"
            )

        if reasons:
            errors.append({
                "service_name":   row["service_name"],
                "source_service": row["source_service"],
                "target_service": row["target_service"],
                "strategy_7r":    strategy,
                "errors":         reasons,
            })

    if errors:
        logger.warning(
            "[Audit] detect_planning_errors: %d suspicious decision(s) in migration=%s",
            len(errors), migration_id,
        )
    else:
        logger.info("[Audit] detect_planning_errors: ✓ no suspicious decisions in migration=%s", migration_id)

    return errors


# ── Helpers ───────────────────────────────────────────────────────────────────

def _infer_provider(resource_type: str) -> str:
    t = (resource_type or "").lower()
    if t.startswith("aws_"):
        return "aws"
    if t.startswith("azurerm_"):
        return "azure"
    if t.startswith("google_"):
        return "gcp"
    return "unknown"


def _safe_float(val: Any) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None
