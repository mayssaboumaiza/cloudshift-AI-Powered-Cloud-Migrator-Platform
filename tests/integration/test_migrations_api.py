"""Integration tests for POST/GET /api/v1/migrations endpoints.

All I/O is mocked — no real database or service layer is started.
The test app uses dependency_overrides to replace get_migration_service
with an in-memory stub.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from services.custom_service_exceptions import MigrationDoesNotExist

_API_KEY = "integration-test-key"
_AUTH_HEADERS = {"X-API-Key": _API_KEY}


# ─────────────────────────────────────────────────────────────────────────────
# In-memory migration store
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


class _AttrDict(dict):
    """dict that also exposes keys as attributes — mirrors ORM-object access
    patterns like `result.id` used by routers (e.g. for audit logging) while
    still serializing/behaving like the plain dicts other fake methods return."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


def _make_migration(
    migration_id: str | None = None,
    repo_url: str = "https://github.com/test/repo",
    source_cloud: str = "aws",
    target_cloud: str = "gcp",
) -> dict[str, Any]:
    return {
        "id": migration_id or str(uuid.uuid4()),
        "repo_url": repo_url,
        "repo_urls": None,
        "source_cloud": source_cloud,
        "target_cloud": target_cloud,
        "status": "Created",
        "github_token": None,
        "dependency_graph": None,
        "migration_plan": None,
        "preview_report": None,
        "iac_output": None,
        "generated_files": None,
        "deployment_status": None,
        "errors": None,
        "warnings": None,
        "artifacts": None,
        "thread_id": str(uuid.uuid4()),
        "current_step": None,
        "iac_validation_success": None,
        "needs_human_escalation": None,
        "correction_counts": None,
        "intent_issues": None,
        "monthly_budget_usd": None,
        "timeline": None,
        "target_region": None,
        "data_residency_requirement": None,
        "regulatory_constraints": None,
        "ai_stack": None,
        "credentials_pre_validated": False,
        "created_at": _now(),
        "updated_at": _now(),
    }


class FakeMigrationService:
    """Minimal in-memory stub with the same interface as MigrationService."""

    def __init__(self, seed: list[dict] | None = None):
        self._store: dict[str, dict] = {}
        for item in (seed or []):
            self._store[item["id"]] = item

    async def get_all(self, skip: int = 0, limit: int = 20) -> list[dict]:
        items = list(self._store.values())
        return items[skip: skip + limit]

    async def get_all_for_user(self, caller_id, is_admin: bool = False, skip: int = 0, limit: int = 20) -> list[dict]:
        return await self.get_all(skip=skip, limit=limit)

    async def get_by_id(self, migration_id: str) -> dict:
        if migration_id not in self._store:
            raise MigrationDoesNotExist(f"Migration {migration_id} not found")
        return self._store[migration_id]

    async def get_by_id_for_user(self, migration_id: str, caller_id, is_admin: bool = False) -> dict:
        return await self.get_by_id(migration_id)

    async def create(self, data, owner_id=None) -> dict:
        m = _AttrDict(_make_migration(
            repo_url=data.repo_url,
            source_cloud=str(data.source_cloud.value if hasattr(data.source_cloud, "value") else data.source_cloud),
            target_cloud=str(data.target_cloud.value if hasattr(data.target_cloud, "value") else data.target_cloud),
        ))
        self._store[m["id"]] = m
        return m


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def seeded_migration() -> dict:
    return _make_migration(migration_id="known-id-123")


@pytest.fixture()
def app_client(seeded_migration, monkeypatch):
    """TestClient with dependency_overrides replacing the real MigrationService."""
    import os
    monkeypatch.setenv("API_KEY", _API_KEY)

    fake_service = FakeMigrationService(seed=[seeded_migration])

    from unittest.mock import patch
    with patch("app.init_db", AsyncMock()), \
         patch("pipeline.pipeline_graph.get_compiled_graph", MagicMock()):
        from app import create_app
        from api.dependencies import get_migration_service
        application = create_app()
        application.dependency_overrides[get_migration_service] = lambda: fake_service
        yield TestClient(application, raise_server_exceptions=False)


@pytest.fixture()
def empty_app_client(monkeypatch):
    """TestClient with no seeded migrations."""
    import os
    monkeypatch.setenv("API_KEY", _API_KEY)

    fake_service = FakeMigrationService(seed=[])

    from unittest.mock import patch
    with patch("app.init_db", AsyncMock()), \
         patch("pipeline.pipeline_graph.get_compiled_graph", MagicMock()):
        from app import create_app
        from api.dependencies import get_migration_service
        application = create_app()
        application.dependency_overrides[get_migration_service] = lambda: fake_service
        yield TestClient(application, raise_server_exceptions=False)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/migrations
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateMigration:
    def test_create_returns_201(self, empty_app_client):
        resp = empty_app_client.post(
            "/api/v1/migrations/",
            json={
                "repo_url": "https://github.com/test/repo",
                "source_cloud": "aws",
                "target_cloud": "gcp",
                "github_token": "FAKE_GITHUB_TOKEN",
            },
            headers=_AUTH_HEADERS,
        )
        assert resp.status_code == 201

    def test_create_returns_migration_id(self, empty_app_client):
        resp = empty_app_client.post(
            "/api/v1/migrations/",
            json={
                "repo_url": "https://github.com/test/repo",
                "source_cloud": "aws",
                "target_cloud": "gcp",
                "github_token": "FAKE_GITHUB_TOKEN",
            },
            headers=_AUTH_HEADERS,
        )
        body = resp.json()
        assert "id" in body
        assert body["id"]  # non-empty

    def test_create_without_api_key_returns_403(self, empty_app_client):
        resp = empty_app_client.post(
            "/api/v1/migrations/",
            json={
                "repo_url": "https://github.com/test/repo",
                "source_cloud": "aws",
                "target_cloud": "gcp",
                "github_token": "FAKE_GITHUB_TOKEN",
            },
        )
        assert resp.status_code == 403

    def test_create_with_invalid_repo_url_returns_422(self, empty_app_client):
        resp = empty_app_client.post(
            "/api/v1/migrations/",
            json={
                "repo_url": "http://not-https.com/repo",  # must be HTTPS
                "source_cloud": "aws",
                "target_cloud": "gcp",
                "github_token": "FAKE_GITHUB_TOKEN",
            },
            headers=_AUTH_HEADERS,
        )
        assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/migrations/
# ─────────────────────────────────────────────────────────────────────────────

class TestListMigrations:
    def test_list_returns_200(self, app_client):
        resp = app_client.get("/api/v1/migrations/", headers=_AUTH_HEADERS)
        assert resp.status_code == 200

    def test_list_returns_list(self, app_client):
        resp = app_client.get("/api/v1/migrations/", headers=_AUTH_HEADERS)
        assert isinstance(resp.json(), list)

    def test_list_empty_when_no_migrations(self, empty_app_client):
        resp = empty_app_client.get("/api/v1/migrations/", headers=_AUTH_HEADERS)
        assert resp.json() == []

    def test_list_without_api_key_returns_403(self, app_client):
        resp = app_client.get("/api/v1/migrations/")
        assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/migrations/{id}
# ─────────────────────────────────────────────────────────────────────────────

class TestGetMigration:
    def test_get_known_id_returns_200(self, app_client):
        resp = app_client.get("/api/v1/migrations/known-id-123", headers=_AUTH_HEADERS)
        assert resp.status_code == 200

    def test_get_known_id_returns_migration_data(self, app_client):
        resp = app_client.get("/api/v1/migrations/known-id-123", headers=_AUTH_HEADERS)
        body = resp.json()
        assert body["id"] == "known-id-123"

    def test_get_unknown_id_returns_404(self, app_client):
        resp = app_client.get("/api/v1/migrations/does-not-exist", headers=_AUTH_HEADERS)
        assert resp.status_code == 404

    def test_get_without_api_key_returns_403(self, app_client):
        resp = app_client.get("/api/v1/migrations/known-id-123")
        assert resp.status_code == 403

