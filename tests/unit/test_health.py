"""Unit tests for the /health endpoint in app.py.

All external I/O (database) is mocked — these are pure unit tests.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client_with_healthy_db(monkeypatch):
    """TestClient whose DB mock returns a non-zero table count."""
    import os
    os.environ.setdefault("API_KEY", "test-key")

    row_mock = MagicMock()
    row_mock.__getitem__ = lambda self, i: 5  # 5 tables

    result_mock = MagicMock()
    result_mock.fetchone.return_value = row_mock

    conn_mock = AsyncMock()
    conn_mock.execute.return_value = result_mock

    engine_ctx = AsyncMock()
    engine_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    engine_ctx.__aexit__ = AsyncMock(return_value=False)

    engine_mock = MagicMock()
    engine_mock.begin.return_value = engine_ctx

    with patch("app.engine", engine_mock), \
         patch("app.init_db", AsyncMock()), \
         patch("pipeline.pipeline_graph.get_compiled_graph", MagicMock()):
        from app import create_app
        app = create_app()
        yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def client_with_down_db(monkeypatch):
    """TestClient whose DB mock raises to simulate a down database."""
    import os
    os.environ.setdefault("API_KEY", "test-key")

    engine_ctx = AsyncMock()
    engine_ctx.__aenter__ = AsyncMock(side_effect=ConnectionRefusedError("DB down"))
    engine_ctx.__aexit__ = AsyncMock(return_value=False)

    engine_mock = MagicMock()
    engine_mock.begin.return_value = engine_ctx

    with patch("app.engine", engine_mock), \
         patch("app.init_db", AsyncMock()), \
         patch("pipeline.pipeline_graph.get_compiled_graph", MagicMock()):
        from app import create_app
        app = create_app()
        yield TestClient(app, raise_server_exceptions=False)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_health_returns_200_when_db_reachable(client_with_healthy_db):
    resp = client_with_healthy_db.get("/health")
    assert resp.status_code == 200


def test_health_returns_503_when_db_down(client_with_down_db):
    resp = client_with_down_db.get("/health")
    assert resp.status_code == 503


def test_health_response_contains_status_key(client_with_healthy_db):
    resp = client_with_healthy_db.get("/health")
    body = resp.json()
    assert "status" in body


def test_health_response_status_ok_when_db_reachable(client_with_healthy_db):
    resp = client_with_healthy_db.get("/health")
    body = resp.json()
    assert body["status"] == "ok"


def test_health_response_status_degraded_when_db_down(client_with_down_db):
    resp = client_with_down_db.get("/health")
    body = resp.json()
    assert body["status"] == "degraded"


def test_health_response_contains_database_key(client_with_healthy_db):
    resp = client_with_healthy_db.get("/health")
    body = resp.json()
    assert "database" in body


def test_health_endpoint_does_not_require_api_key(client_with_healthy_db):
    """Health must be accessible without authentication."""
    resp = client_with_healthy_db.get("/health")
    assert resp.status_code != 403
