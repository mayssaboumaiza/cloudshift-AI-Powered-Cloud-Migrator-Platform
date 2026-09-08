"""Unit tests for api/auth/api_key.py — X-API-Key header authentication."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth.api_key import require_api_key


# ─────────────────────────────────────────────────────────────────────────────
# Minimal test app
# ─────────────────────────────────────────────────────────────────────────────

def _make_app(api_key_env: str | None = "test-secret-key") -> tuple[FastAPI, TestClient]:
    """Build a minimal FastAPI app with one protected and one public route."""
    import os
    if api_key_env is not None:
        os.environ["API_KEY"] = api_key_env
    else:
        os.environ.pop("API_KEY", None)

    app = FastAPI()

    @app.get("/protected", dependencies=[])
    async def protected_route(key: str = __import__("fastapi").Depends(require_api_key)):
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics():
        return {"metrics": "data"}

    return app, TestClient(app, raise_server_exceptions=False)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestApiKey:
    def setup_method(self):
        self.app, self.client = _make_app("test-secret-key")

    def test_valid_api_key_returns_200(self):
        resp = self.client.get("/protected", headers={"X-API-Key": "test-secret-key"})
        assert resp.status_code == 200

    def test_missing_api_key_returns_403(self):
        resp = self.client.get("/protected")
        assert resp.status_code == 403

    def test_wrong_api_key_returns_403(self):
        resp = self.client.get("/protected", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 403

    def test_empty_api_key_returns_403(self):
        resp = self.client.get("/protected", headers={"X-API-Key": ""})
        assert resp.status_code in (403, 422)

    def test_health_endpoint_accessible_without_api_key(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200

    def test_metrics_endpoint_accessible_without_api_key(self):
        resp = self.client.get("/metrics")
        assert resp.status_code == 200


class TestApiKeyWhenEnvNotSet:
    """When API_KEY env var is empty the dependency should always reject."""

    def setup_method(self):
        self.app, self.client = _make_app(api_key_env="")

    def test_any_key_rejected_when_env_empty(self):
        resp = self.client.get("/protected", headers={"X-API-Key": "anything"})
        assert resp.status_code == 403
