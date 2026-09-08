"""Unit tests for agents/migration_planner/audit_logger.py.

Pure helper functions (_infer_provider, _safe_float) are tested directly.
DB-dependent functions (log_migration_decisions, detect_planning_errors)
are tested via mocking.
"""
from unittest.mock import MagicMock, patch

import pytest

from agents.migration_planner.audit_logger import (
    _infer_provider,
    _safe_float,
    detect_planning_errors,
    log_migration_decisions,
)


# ── _infer_provider ───────────────────────────────────────────────────────────

def test_infer_provider_aws():
    assert _infer_provider("aws_rds_cluster") == "aws"

def test_infer_provider_aws_prefix_variants():
    assert _infer_provider("aws_s3_bucket") == "aws"
    assert _infer_provider("aws_lambda_function") == "aws"

def test_infer_provider_azure():
    assert _infer_provider("azurerm_postgresql_flexible_server") == "azure"
    assert _infer_provider("azurerm_storage_account") == "azure"

def test_infer_provider_gcp():
    assert _infer_provider("google_storage_bucket") == "gcp"
    assert _infer_provider("google_container_cluster") == "gcp"

def test_infer_provider_unknown():
    assert _infer_provider("unknown_service") == "unknown"

def test_infer_provider_empty():
    assert _infer_provider("") == "unknown"

def test_infer_provider_none_safe():
    assert _infer_provider(None) == "unknown"


# ── _safe_float ───────────────────────────────────────────────────────────────

def test_safe_float_int():
    assert _safe_float(1) == 1.0

def test_safe_float_float():
    assert _safe_float(0.85) == pytest.approx(0.85)

def test_safe_float_string():
    assert _safe_float("0.92") == pytest.approx(0.92)

def test_safe_float_zero():
    assert _safe_float(0) == 0.0

def test_safe_float_none():
    assert _safe_float(None) is None

def test_safe_float_invalid_string():
    assert _safe_float("not_a_number") is None

def test_safe_float_empty_string():
    assert _safe_float("") is None


# ── log_migration_decisions — no-op cases ────────────────────────────────────

def test_log_empty_plan_returns_zero():
    assert log_migration_decisions("mig-001", {}) == 0

def test_log_none_plan_returns_zero():
    assert log_migration_decisions("mig-001", None) == 0

def test_log_empty_services_returns_zero():
    assert log_migration_decisions("mig-001", {"services": []}) == 0

def test_log_empty_resources_returns_zero():
    assert log_migration_decisions("mig-001", {"resources": []}) == 0


# ── log_migration_decisions — DB mock ─────────────────────────────────────────

def _make_conn_mock(rowcount=1):
    """Return a raw connection mock."""
    conn = MagicMock()
    cur = MagicMock()
    cur.rowcount = rowcount
    conn.cursor.return_value = cur
    return conn


def _pg_ctx(conn):
    """Wrap conn as a context-manager mock for get_pg_connection().
    Production code uses: with get_pg_connection() as conn: ...
    """
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def test_log_calls_executemany_and_commit():
    conn = _make_conn_mock()
    plan = {"services": [
        {"service_name": "db", "source_service": "aws_rds_cluster",
         "target_service": "azurerm_postgresql_flexible_server",
         "strategy_7r": "REPLATFORM", "equivalence_score": 0.90},
    ]}
    with patch("agents.migration_planner.audit_logger.get_pg_connection", return_value=_pg_ctx(conn)):
        result = log_migration_decisions("mig-001", plan)
    conn.cursor.return_value.executemany.assert_called_once()
    conn.commit.assert_called_once()
    assert result == 1


def test_log_db_failure_returns_zero():
    conn = MagicMock()
    conn.cursor.side_effect = Exception("DB down")
    plan = {"services": [{"service_name": "db", "source_service": "aws_rds_cluster",
                          "target_service": "azurerm_pg", "strategy_7r": "REPLATFORM"}]}
    with patch("agents.migration_planner.audit_logger.get_pg_connection", return_value=_pg_ctx(conn)):
        result = log_migration_decisions("mig-001", plan)
    assert result == 0


def test_log_reasoning_dict_serialized():
    """reasoning field as dict must be JSON-serialized before insertion."""
    conn = _make_conn_mock()
    plan = {"services": [
        {"service_name": "db", "source_service": "aws_rds_cluster",
         "target_service": "azurerm_pg", "strategy_7r": "REPLATFORM",
         "reasoning": {"step": "analyzed", "score": 0.90}},
    ]}
    with patch("agents.migration_planner.audit_logger.get_pg_connection", return_value=_pg_ctx(conn)):
        log_migration_decisions("mig-001", plan)
    rows_arg = conn.cursor.return_value.executemany.call_args[0][1]
    reasoning_val = rows_arg[0][-1]  # last column is reasoning
    assert isinstance(reasoning_val, str)


# ── detect_planning_errors ────────────────────────────────────────────────────

def _row(strategy="REPLATFORM", eq=0.90, onto=0.88, intent_valid=True, mismatch=None):
    return {
        "service_name": "db",
        "source_service": "aws_rds_cluster",
        "target_service": "azurerm_postgresql_flexible_server",
        "strategy_7r": strategy,
        "equivalence_score": eq,
        "ontology_score": onto,
        "intent_valid": intent_valid,
        "intent_mismatch": mismatch,
    }


def test_detect_no_errors_clean_row():
    with patch("agents.migration_planner.audit_logger.get_audit_report", return_value=[_row()]):
        errors = detect_planning_errors("mig-001")
    assert errors == []


def test_detect_intent_invalid_flagged():
    with patch("agents.migration_planner.audit_logger.get_audit_report",
               return_value=[_row(intent_valid=False, mismatch="relational→nosql")]):
        errors = detect_planning_errors("mig-001")
    assert len(errors) == 1
    assert any("Intent mismatch" in r for r in errors[0]["errors"])


def test_detect_refactor_with_high_equivalence():
    """REFACTOR strategy with equivalence > 0.85 should be flagged."""
    with patch("agents.migration_planner.audit_logger.get_audit_report",
               return_value=[_row(strategy="REFACTOR", eq=0.92)]):
        errors = detect_planning_errors("mig-001")
    assert len(errors) == 1
    assert any("REFACTOR" in r for r in errors[0]["errors"])


def test_detect_ontology_gap_flagged():
    """embedding >> ontology (gap > 0.20) should be flagged as possible hallucination."""
    with patch("agents.migration_planner.audit_logger.get_audit_report",
               return_value=[_row(eq=0.95, onto=0.70)]):
        errors = detect_planning_errors("mig-001")
    assert len(errors) == 1
    assert any("ontology score" in r for r in errors[0]["errors"])


def test_detect_no_flag_when_refactor_low_equivalence():
    """REFACTOR + eq < 0.85 is correct — should not be flagged."""
    with patch("agents.migration_planner.audit_logger.get_audit_report",
               return_value=[_row(strategy="REFACTOR", eq=0.75, onto=0.72)]):
        errors = detect_planning_errors("mig-001")
    assert errors == []


def test_detect_multiple_errors_in_same_row():
    """A row can have multiple errors (intent_valid=False AND ontology gap)."""
    with patch("agents.migration_planner.audit_logger.get_audit_report",
               return_value=[_row(intent_valid=False, mismatch="mismatch", eq=0.96, onto=0.70)]):
        errors = detect_planning_errors("mig-001")
    assert len(errors) == 1
    assert len(errors[0]["errors"]) >= 2


def test_detect_db_failure_returns_empty():
    with patch("agents.migration_planner.audit_logger.get_audit_report", return_value=[]):
        errors = detect_planning_errors("mig-xyz")
    assert errors == []
