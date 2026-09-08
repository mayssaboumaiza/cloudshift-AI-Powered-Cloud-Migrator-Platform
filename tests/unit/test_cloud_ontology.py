"""Unit tests for rag/cloud_ontology.py.

DB calls are mocked — no real PostgreSQL needed.
"""
from unittest.mock import MagicMock, patch

import pytest

from rag.cloud_ontology import (
    _ONTOLOGY_ENTRIES,
    get_best_target,
    get_ontology_score,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _mock_conn(fetchone=None):
    """Create a raw connection mock with a cursor that returns fetchone."""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    conn.cursor.return_value = cur
    return conn


def _pg_ctx(conn):
    """Wrap a raw conn mock as a context-manager mock for get_pg_connection().
    The production code uses: with get_pg_connection() as conn: ...
    so get_pg_connection() must return an object that supports __enter__/__exit__.
    """
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


# ── _ONTOLOGY_ENTRIES static validation ───────────────────────────────────────

def test_ontology_has_minimum_entries():
    assert len(_ONTOLOGY_ENTRIES) >= 30, "Ontology should have at least 30 curated entries"


def test_ontology_entry_format():
    for entry in _ONTOLOGY_ENTRIES:
        assert len(entry) == 9, f"Wrong tuple length: {entry}"
        src_prov, src_svc, tgt_prov, tgt_svc, eq, compat, conf, mtype, notes = entry
        assert isinstance(src_prov, str) and src_prov
        assert isinstance(tgt_prov, str) and tgt_prov
        assert 0.0 <= eq   <= 1.0, f"eq out of range: {eq} in {entry}"
        assert 0.0 <= compat <= 1.0, f"compat out of range: {compat}"
        assert 0.0 <= conf <= 1.0, f"conf out of range: {conf}"
        assert mtype in ("REHOST", "REPLATFORM", "REFACTOR", "REPURCHASE"), \
            f"Invalid migration_type: {mtype}"


def test_ontology_covers_key_aws_azure_pairs():
    pairs = {(e[0], e[1], e[2], e[3]) for e in _ONTOLOGY_ENTRIES}
    required = [
        ("aws", "aws_rds_cluster",   "azure", "azurerm_postgresql_flexible_server"),
        ("aws", "aws_s3_bucket",     "azure", "azurerm_storage_account"),
        ("aws", "aws_eks_cluster",   "azure", "azurerm_kubernetes_cluster"),
        ("aws", "aws_lambda_function","azure","azurerm_linux_function_app"),
    ]
    for pair in required:
        assert pair in pairs, f"Missing expected ontology entry: {pair}"


def test_ontology_has_gcp_entries():
    gcp_entries = [e for e in _ONTOLOGY_ENTRIES if e[2] == "gcp"]
    assert len(gcp_entries) >= 3


# ── get_ontology_score ────────────────────────────────────────────────────────

def test_get_ontology_score_found():
    conn = _mock_conn(fetchone=(0.93, 1.0))
    with patch("rag.cloud_ontology.get_pg_connection", return_value=_pg_ctx(conn)):
        score = get_ontology_score("aws", "aws_rds_cluster", "azure",
                                   "azurerm_postgresql_flexible_server")
    assert score == pytest.approx(0.93 * 1.0)


def test_get_ontology_score_weighted_by_confidence():
    """Score should be eq * confidence (e.g. 0.80 * 0.8 = 0.64)."""
    conn = _mock_conn(fetchone=(0.80, 0.8))
    with patch("rag.cloud_ontology.get_pg_connection", return_value=_pg_ctx(conn)):
        score = get_ontology_score("aws", "aws_neptune_cluster", "azure",
                                   "azurerm_cosmosdb_account")
    assert score == pytest.approx(0.80 * 0.8)


def test_get_ontology_score_not_found_returns_none():
    conn = _mock_conn(fetchone=None)
    with patch("rag.cloud_ontology.get_pg_connection", return_value=_pg_ctx(conn)):
        score = get_ontology_score("aws", "aws_unknown_service", "azure", "azurerm_unknown")
    assert score is None


def test_get_ontology_score_db_error_returns_none():
    conn = MagicMock()
    conn.cursor.side_effect = Exception("Connection refused")
    with patch("rag.cloud_ontology.get_pg_connection", return_value=_pg_ctx(conn)):
        score = get_ontology_score("aws", "aws_rds_cluster", "azure",
                                   "azurerm_postgresql_flexible_server")
    assert score is None


def test_get_ontology_score_calls_correct_query():
    conn = _mock_conn(fetchone=(0.91, 1.0))
    with patch("rag.cloud_ontology.get_pg_connection", return_value=_pg_ctx(conn)):
        get_ontology_score("aws", "aws_s3_bucket", "azure", "azurerm_storage_account")
    call_args = conn.cursor.return_value.execute.call_args
    sql = call_args[0][0]
    assert "cloud_service_ontology" in sql
    assert "equivalence_score" in sql


def test_get_ontology_score_conn_closed_on_success():
    """Context manager __exit__ must be called — ensures connection is always released."""
    conn = _mock_conn(fetchone=(0.91, 1.0))
    ctx = _pg_ctx(conn)
    with patch("rag.cloud_ontology.get_pg_connection", return_value=ctx):
        get_ontology_score("aws", "aws_s3_bucket", "azure", "azurerm_storage_account")
    ctx.__exit__.assert_called_once()


def test_get_ontology_score_conn_closed_on_error():
    """Context manager __exit__ must be called even when cursor raises."""
    conn = MagicMock()
    conn.cursor.side_effect = Exception("DB down")
    ctx = _pg_ctx(conn)
    with patch("rag.cloud_ontology.get_pg_connection", return_value=ctx):
        get_ontology_score("aws", "aws_s3_bucket", "azure", "azurerm_storage_account")
    ctx.__exit__.assert_called_once()


# ── get_best_target ───────────────────────────────────────────────────────────

_BEST_ROW = (
    "azurerm_postgresql_flexible_server",  # target_service
    0.93,   # equivalence_score
    0.90,   # compatibility_score
    1.0,    # confidence
    "REPLATFORM",
    "RDS Aurora → Azure PostgreSQL Flexible Server",
)


def test_get_best_target_found():
    conn = _mock_conn(fetchone=_BEST_ROW)
    with patch("rag.cloud_ontology.get_pg_connection", return_value=_pg_ctx(conn)):
        result = get_best_target("aws", "aws_rds_cluster", "azure")
    assert result is not None
    assert result["target_service"] == "azurerm_postgresql_flexible_server"
    assert result["migration_type"] == "REPLATFORM"
    assert result["equivalence_score"] == 0.93


def test_get_best_target_returns_all_fields():
    conn = _mock_conn(fetchone=_BEST_ROW)
    with patch("rag.cloud_ontology.get_pg_connection", return_value=_pg_ctx(conn)):
        result = get_best_target("aws", "aws_rds_cluster", "azure")
    expected_keys = {"target_service", "equivalence_score", "compatibility_score",
                     "confidence", "migration_type", "notes"}
    assert expected_keys == set(result.keys())


def test_get_best_target_not_found_returns_none():
    conn = _mock_conn(fetchone=None)
    with patch("rag.cloud_ontology.get_pg_connection", return_value=_pg_ctx(conn)):
        result = get_best_target("aws", "aws_unknown", "azure")
    assert result is None


def test_get_best_target_db_error_returns_none():
    conn = MagicMock()
    conn.cursor.side_effect = Exception("DB down")
    with patch("rag.cloud_ontology.get_pg_connection", return_value=_pg_ctx(conn)):
        result = get_best_target("aws", "aws_rds_cluster", "azure")
    assert result is None
