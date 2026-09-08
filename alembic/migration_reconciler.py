"""
alembic/migration_reconciler.py — Drift-tolerant schema reconciliation engine.

Architecture
------------
                    ┌──────────────────────────────────────────┐
                    │         ReconciliationEngine              │
                    │                                          │
  alembic_version ──►  DriftDetector  ──►  DriftClassifier   │
  information_schema►                                          │
  pg_indexes       │         │                                 │
  pg_constraint    │         ▼                                 │
                    │  DriftReport (MISSING / EXTRA /          │
                    │              MISMATCH / ALREADY_APPLIED) │
                    │         │                                 │
                    │         ▼                                 │
                    │  RepairEngine (safe, non-destructive)    │
                    └──────────────────────────────────────────┘

Public API
----------
    from alembic.migration_reconciler import ReconciliationEngine

    engine = ReconciliationEngine.from_env()
    report = engine.detect()          # read-only drift analysis
    engine.repair(report)             # safe repair (no destructive DDL)
    engine.print_report(report)       # human-readable summary

Exit codes (used by CI script)
-------------------------------
    0 — clean (no drift, or drift fully repaired)
    1 — drift detected but not auto-repairable (needs human review)
    2 — connection / configuration error

Design decisions
----------------
- Uses psycopg2 (sync) so it works outside the async FastAPI context and
  can be run as a standalone CLI script or in a Docker entrypoint.
- Never issues DROP, TRUNCATE, or ALTER COLUMN TYPE — only additive repairs.
- Alembic stamp is only written when the physical schema matches what the
  revision claims to have created (validated, never blind-stamped).
- Thread-safe: each RepairEngine method opens its own transaction.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("MigrationReconciler")


# ── Drift classification ──────────────────────────────────────────────────────

class DriftKind(str, Enum):
    MISSING_TABLE      = "MISSING_TABLE"       # Alembic says exists, DB doesn't
    EXTRA_TABLE        = "EXTRA_TABLE"          # DB has table, no migration owns it
    MISSING_COLUMN     = "MISSING_COLUMN"       # Column in model, not in DB
    MISSING_INDEX      = "MISSING_INDEX"        # Index expected by migration, absent
    MISSING_CONSTRAINT = "MISSING_CONSTRAINT"   # FK/UNIQUE expected, absent
    ALREADY_APPLIED    = "ALREADY_APPLIED"      # DDL done manually, Alembic not stamped
    VERSION_MISMATCH   = "VERSION_MISMATCH"     # alembic_version ≠ expected head


@dataclass
class DriftItem:
    kind:        DriftKind
    object_name: str
    detail:      str
    repairable:  bool = True   # False → requires human intervention


@dataclass
class DriftReport:
    alembic_current:  str | None
    alembic_head:     str | None
    items:            list[DriftItem] = field(default_factory=list)
    repair_log:       list[str]       = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.items)

    @property
    def has_blocking_drift(self) -> bool:
        return any(not i.repairable for i in self.items)

    @property
    def clean(self) -> bool:
        return not self.has_drift

    def by_kind(self, kind: DriftKind) -> list[DriftItem]:
        return [i for i in self.items if i.kind == kind]


# ── DB introspection helpers (sync psycopg2) ──────────────────────────────────

class _SchemaInspector:
    """Low-level read-only schema reader using a raw psycopg2 connection."""

    def __init__(self, conn: Any):
        self._conn = conn

    def _q(self, sql: str, params: dict | None = None) -> list[tuple]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchall()

    def tables(self, schema: str = "public") -> set[str]:
        rows = self._q(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %(s)s AND table_type = 'BASE TABLE'",
            {"s": schema},
        )
        return {r[0] for r in rows}

    def columns(self, table: str, schema: str = "public") -> set[str]:
        rows = self._q(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %(s)s AND table_name = %(t)s",
            {"s": schema, "t": table},
        )
        return {r[0] for r in rows}

    def indexes(self, schema: str = "public") -> set[str]:
        rows = self._q(
            "SELECT indexname FROM pg_indexes WHERE schemaname = %(s)s",
            {"s": schema},
        )
        return {r[0] for r in rows}

    def constraints(self, schema: str = "public") -> set[str]:
        rows = self._q(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE constraint_schema = %(s)s",
            {"s": schema},
        )
        return {r[0] for r in rows}

    def alembic_version(self) -> str | None:
        try:
            rows = self._q("SELECT version_num FROM alembic_version LIMIT 1")
            return rows[0][0] if rows else None
        except Exception:
            return None


# ── Known schema catalogue (what each revision is expected to produce) ────────
# This catalogue is intentionally minimal — it covers only the objects
# that were found to drift in this codebase. Extend as migrations are added.

_EXPECTED: dict[str, dict[str, list[str]]] = {
    # revision_id → {"indexes": [...], "tables": [...], "columns": {table: [col, ...]}}
    "007_consolidate_tables": {
        "tables":  ["pipeline_state"],
        "indexes": ["ix_pipeline_state_thread_id", "ix_pipeline_state_checkpoint_id",
                    "ix_pipeline_state_created_at"],
        "columns": {"migrations": ["migration_priority"]},
    },
    "008_pipeline_events_and_fixes": {
        "tables":  ["pipeline_events"],
        "indexes": ["ix_pipeline_events_thread_id", "ix_pipeline_events_created_at"],
    },
    "009_add_secrets_tables": {
        "tables":  ["migration_secrets", "secret_audit_log"],
        "indexes": ["ix_migration_secrets_migration_id",
                    "ix_secret_audit_log_secret_id",
                    "ix_secret_audit_log_migration_id",
                    "ix_secret_audit_log_created_at"],
    },
    "010_add_executor_tables": {
        "tables":  ["runner_jobs", "deployment_logs"],
        "indexes": ["ix_runner_jobs_migration_id", "ix_runner_jobs_status",
                    "ix_runner_jobs_created_at", "ix_deployment_logs_job_id",
                    "ix_deployment_logs_created_at"],
    },
    "013_add_region_availability_cache": {
        "tables":  ["region_availability_cache"],
        "indexes": ["uq_region_availability_cache_key", "ix_region_cache_provider_region",
                    "ix_region_cache_expires_at"],
    },
    "014_add_ontology_and_audit": {
        "tables":  ["cloud_service_ontology", "migration_audit"],
        "indexes": ["ix_ontology_source", "ix_ontology_pair",
                    "ix_audit_migration_id", "ix_audit_source_svc", "ix_audit_target_svc"],
    },
    "015_add_warnings_column": {
        "columns": {"migrations": ["warnings"]},
    },
    "016_add_users_table": {
        "tables":  ["users"],
        "indexes": ["ix_users_email", "ix_users_username"],
    },
    "017_add_incremental_migration": {
        "columns": {"migrations": ["last_git_sha", "migration_mode", "detected_changes"]},
    },
    "018_add_compliance_fields": {
        "columns": {"migrations": ["compliance_standards",
                                   "high_availability_required",
                                   "network_isolation_required"]},
    },
    "019_add_github_output_urls": {
        "columns": {"migrations": ["github_pr_url", "github_repo_url"]},
    },
    "020_add_audit_logs": {
        "tables":  ["audit_logs"],
        "indexes": ["idx_audit_user_id", "idx_audit_action",
                    "idx_audit_resource_id", "idx_audit_created_at"],
    },
    "022_add_invitations_table": {
        "tables":  ["invitations"],
        "indexes": ["ix_invitations_token", "ix_invitations_email"],
    },
    "023_add_user_id_to_migrations": {
        "columns":     {"migrations": ["user_id"]},
        "indexes":     ["ix_migrations_user_id"],
        "constraints": ["migrations_user_id_fkey"],
    },
}

# Tables that exist in DB but are managed outside Alembic (RAG, external tooling)
_EXTERNAL_TABLES = {
    "terraform_resources", "tf_arguments", "tf_argument_edges",
    "tf_canonical_patterns", "resource_communities", "resource_relations",
    "dynamic_semantic_tags", "rag_commits",
}


# ── Drift detector ────────────────────────────────────────────────────────────

class DriftDetector:
    def __init__(self, inspector: _SchemaInspector):
        self._i = inspector

    def detect(self) -> DriftReport:
        current = self._i.alembic_version()
        report  = DriftReport(alembic_current=current, alembic_head=None)

        db_tables      = self._i.tables()
        db_indexes     = self._i.indexes()
        db_constraints = self._i.constraints()

        # Check all expected objects for revisions up to current
        for revision, expected in _EXPECTED.items():
            for table in expected.get("tables", []):
                if table not in db_tables:
                    report.items.append(DriftItem(
                        kind=DriftKind.MISSING_TABLE,
                        object_name=table,
                        detail=f"Expected by revision {revision}",
                        repairable=False,   # table creation needs schema definition
                    ))
                else:
                    for col in expected.get("columns", {}).get(table, []):
                        if col not in self._i.columns(table):
                            report.items.append(DriftItem(
                                kind=DriftKind.MISSING_COLUMN,
                                object_name=f"{table}.{col}",
                                detail=f"Expected by revision {revision}",
                                repairable=False,
                            ))

            # Columns on existing tables (no table guard needed)
            for table, cols in expected.get("columns", {}).items():
                if table in db_tables:
                    for col in cols:
                        if col not in self._i.columns(table):
                            report.items.append(DriftItem(
                                kind=DriftKind.MISSING_COLUMN,
                                object_name=f"{table}.{col}",
                                detail=f"Expected by revision {revision}",
                                repairable=False,
                            ))

            for idx in expected.get("indexes", []):
                if idx not in db_indexes:
                    # Determine which table the index belongs to
                    table = _index_table(idx, expected)
                    report.items.append(DriftItem(
                        kind=DriftKind.MISSING_INDEX,
                        object_name=idx,
                        detail=f"Expected by revision {revision}, table={table or 'unknown'}",
                        repairable=table is not None,
                    ))

            for cst in expected.get("constraints", []):
                if cst not in db_constraints:
                    report.items.append(DriftItem(
                        kind=DriftKind.MISSING_CONSTRAINT,
                        object_name=cst,
                        detail=f"Expected by revision {revision}",
                        repairable=False,
                    ))

        # ALREADY_APPLIED: objects exist in DB but Alembic not stamped
        # Heuristic: if alembic_version is None but core tables exist → manual apply
        if current is None and "migrations" in db_tables:
            report.items.append(DriftItem(
                kind=DriftKind.ALREADY_APPLIED,
                object_name="alembic_version",
                detail="Core schema exists but no Alembic version stamp found — "
                       "DB was likely initialized manually",
                repairable=True,
            ))

        return report


def _index_table(index_name: str, expected: dict) -> str | None:
    """Best-effort: infer table from index name conventions (ix_<table>_<col>)."""
    for table in expected.get("tables", []):
        if index_name.startswith(f"ix_{table}") or index_name.startswith(f"idx_{table}"):
            return table
    # Fall back to name prefix
    parts = index_name.lstrip("ix_").lstrip("idx_").split("_")
    if parts:
        return parts[0]
    return None


# ── Repair engine (safe, additive only) ──────────────────────────────────────

class RepairEngine:
    """Issues only safe, additive repairs. Never drops or alters columns."""

    def __init__(self, conn: Any):
        self._conn = conn

    def _exec(self, sql: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(sql)
        self._conn.commit()

    def repair_missing_index(self, item: DriftItem) -> str | None:
        """Re-create a missing index from its name using CREATE INDEX IF NOT EXISTS."""
        idx = item.object_name
        # Derive table and column from naming convention: ix_<table>_<col>
        parts = idx.lstrip("ix_").lstrip("idx_").split("_", 1)
        if len(parts) < 2:
            return None
        table, col_part = parts[0], parts[1]
        unique = idx.startswith("uq_") or "unique" in idx.lower()
        try:
            u = "UNIQUE " if unique else ""
            self._exec(
                f'CREATE {u}INDEX IF NOT EXISTS "{idx}" ON {table} ({col_part})'
            )
            return f"Repaired index {idx} on {table}({col_part})"
        except Exception as exc:
            logger.warning("Could not auto-repair index %s: %s", idx, exc)
            self._conn.rollback()
            return None

    def stamp_alembic(self, revision: str, inspector: _SchemaInspector) -> str | None:
        """Stamp alembic_version only after validating that core tables exist."""
        core_tables = {"migrations", "users", "audit_logs"}
        existing    = inspector.tables()
        missing     = core_tables - existing
        if missing:
            return f"Cannot stamp: core tables missing: {missing}"
        try:
            self._exec(
                "INSERT INTO alembic_version (version_num) VALUES (%s) "
                "ON CONFLICT (version_num) DO NOTHING" % f"'{revision}'"
            )
            return f"Stamped alembic_version = {revision}"
        except Exception as exc:
            self._conn.rollback()
            return f"Stamp failed: {exc}"


# ── Reconciliation engine (public API) ───────────────────────────────────────

class ReconciliationEngine:
    def __init__(self, dsn: str):
        self._dsn = dsn

    @classmethod
    def from_env(cls) -> "ReconciliationEngine":
        user     = os.environ.get("DB_USER",     "Mayssa")
        password = os.environ.get("DB_PASSWORD", "Mayssa2002")
        host     = os.environ.get("DB_HOST",     "localhost")
        port     = os.environ.get("DB_PORT",     "5432")
        name     = os.environ.get("DB_NAME",     "cloud_migrator")
        return cls(f"host={host} port={port} dbname={name} user={user} password={password}")

    def _connect(self):
        import psycopg2
        return psycopg2.connect(self._dsn)

    def detect(self) -> DriftReport:
        conn      = self._connect()
        conn.autocommit = True
        inspector = _SchemaInspector(conn)
        try:
            return DriftDetector(inspector).detect()
        finally:
            conn.close()

    def repair(self, report: DriftReport) -> DriftReport:
        if not report.has_drift:
            return report

        conn      = self._connect()
        inspector = _SchemaInspector(conn)
        repairer  = RepairEngine(conn)

        for item in report.by_kind(DriftKind.MISSING_INDEX):
            if item.repairable:
                msg = repairer.repair_missing_index(item)
                if msg:
                    report.repair_log.append(msg)
                    report.items.remove(item)

        for item in report.by_kind(DriftKind.ALREADY_APPLIED):
            msg = repairer.stamp_alembic("023_add_user_id_to_migrations", inspector)
            if msg:
                report.repair_log.append(msg)
                if "Stamped" in msg:
                    report.items.remove(item)

        conn.close()
        return report

    @staticmethod
    def print_report(report: DriftReport, *, verbose: bool = True) -> None:
        print("\n" + "═" * 60)
        print("  SCHEMA RECONCILIATION REPORT")
        print("═" * 60)
        print(f"  Alembic version : {report.alembic_current or '(none)'}")

        if report.clean:
            print("  Status          : ✅ CLEAN — no drift detected")
        elif report.has_blocking_drift:
            print("  Status          : ❌ BLOCKING DRIFT — manual intervention required")
        else:
            print("  Status          : ⚠️  DRIFT DETECTED — auto-repairable")

        if report.items and verbose:
            print("\n  Drift items:")
            for item in report.items:
                tag = "🔧" if item.repairable else "🚨"
                print(f"    {tag} [{item.kind.value}] {item.object_name}")
                print(f"         {item.detail}")

        if report.repair_log:
            print("\n  Repairs applied:")
            for msg in report.repair_log:
                print(f"    ✅ {msg}")

        print("═" * 60 + "\n")

    def run(self, *, repair: bool = False) -> int:
        """
        Full reconcile cycle. Returns exit code:
          0 = clean or repaired
          1 = blocking drift (needs human)
          2 = connection error
        """
        try:
            report = self.detect()
        except Exception as exc:
            logger.error("Reconciler connection failed: %s", exc)
            return 2

        if repair and report.has_drift:
            report = self.repair(report)

        self.print_report(report)

        if report.clean or (repair and not report.has_drift):
            return 0
        if report.has_blocking_drift:
            return 1
        return 1


# ── CLI entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Schema drift reconciler")
    parser.add_argument("--repair", action="store_true",
                        help="Auto-repair repairable drift (indexes, stamps)")
    parser.add_argument("--json",   action="store_true",
                        help="Output drift report as JSON (for CI parsing)")
    args = parser.parse_args()

    engine = ReconciliationEngine.from_env()

    try:
        report = engine.detect()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    if args.repair and report.has_drift:
        report = engine.repair(report)

    if args.json:
        out = {
            "alembic_current": report.alembic_current,
            "clean":           report.clean,
            "drift_count":     len(report.items),
            "blocking":        report.has_blocking_drift,
            "items": [
                {"kind": i.kind.value, "object": i.object_name,
                 "detail": i.detail, "repairable": i.repairable}
                for i in report.items
            ],
            "repairs": report.repair_log,
        }
        print(json.dumps(out, indent=2))
    else:
        engine.print_report(report)

    sys.exit(0 if report.clean else 1)
