"""
tests/integration/test_audit_api.py — Tests de l'API audit (RNF-4 Traçabilité).

Couvre RNF-4 (Traçabilité des actions) :
  - GET /api/v1/audit/logs : accessible uniquement par ADMIN (HTTP 403 pour ANALYST)
  - Sans token → HTTP 401
  - Avec token ADMIN → HTTP 200, liste JSON
  - Pagination : skip/limit fonctionnels
  - Filtres : action, user_id, resource_id, success
  - Format de réponse : champs requis présents dans chaque entrée

Architecture : FastAPI TestClient + dependency_overrides pour la DB (AsyncSession mockée).
Aucune vraie base de données n'est démarrée.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.auth.jwt_handler import create_access_token, _SECRET, _ALGO
from data.models.user_model import UserRole


# ── Helpers JWT ───────────────────────────────────────────────────────────────

def _bearer(role: UserRole, user_id: str = "user-001") -> dict:
    token = create_access_token(user_id, f"{role.value}@test.com", role)
    return {"Authorization": f"Bearer {token}"}


# ── AuditLog stub ─────────────────────────────────────────────────────────────

def _make_audit_log(
    action: str = "migration.created",
    user_id: str = "user-001",
    resource_id: str | None = None,
    success: bool = True,
) -> MagicMock:
    """Retourne un objet qui mime un ORM AuditLog."""
    log = MagicMock()
    log.id            = str(uuid.uuid4())
    log.user_id       = user_id
    log.username      = "testuser"
    log.user_role     = "admin"
    log.ip_address    = "127.0.0.1"
    log.action        = action
    log.resource_type = "migration"
    log.resource_id   = resource_id or str(uuid.uuid4())
    log.resource_name = "my-app"
    log.details       = {"note": "test"}
    log.method        = "POST"
    log.path          = "/api/v1/migrations"
    log.status_code   = 201
    log.success       = success
    log.error         = None
    log.created_at    = datetime.now(timezone.utc)
    return log


def _make_db_session(logs: list) -> AsyncMock:
    """Mock AsyncSession qui retourne les logs fournis via execute()."""
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = logs
    session.execute = AsyncMock(return_value=result_mock)
    return session


async def _fake_db_session(logs: list):
    """Dependency override pour get_db_session."""
    yield _make_db_session(logs)


# ── Fixture client ────────────────────────────────────────────────────────────

@pytest.fixture()
def audit_app(monkeypatch):
    """
    Retourne un callable (logs) -> TestClient.
    Injecte les logs fournis dans la session DB mockée.
    """
    monkeypatch.setenv("API_KEY", "test-key")

    def _make_client(logs: list | None = None) -> TestClient:
        _logs = logs or []

        async def _override_db() -> AsyncGenerator:
            yield _make_db_session(_logs)

        with patch("app.init_db", AsyncMock()), \
             patch("pipeline.pipeline_graph.get_compiled_graph", MagicMock()):
            from app import create_app
            from configuration.database import get_db_session
            app = create_app()
            app.dependency_overrides[get_db_session] = _override_db
            return TestClient(app, raise_server_exceptions=False)

    return _make_client


# ── Contrôle d'accès ──────────────────────────────────────────────────────────

class TestAuditAccessControl:

    def test_no_token_returns_401(self, audit_app):
        client = audit_app()
        resp = client.get("/api/v1/audit/logs")
        assert resp.status_code == 401

    def test_analyst_token_returns_403(self, audit_app):
        """Un token ANALYST ne peut pas accéder aux logs d'audit."""
        client = audit_app()
        resp = client.get("/api/v1/audit/logs",
                          headers=_bearer(UserRole.ANALYST))
        assert resp.status_code == 403

    def test_analyst_403_detail_mentions_role(self, audit_app):
        client = audit_app()
        resp = client.get("/api/v1/audit/logs",
                          headers=_bearer(UserRole.ANALYST))
        body = resp.json()
        detail = body.get("detail", "").lower()
        assert "role" in detail or "admin" in detail or "insufficient" in detail

    def test_admin_token_returns_200(self, audit_app):
        """Un token ADMIN peut accéder aux logs d'audit."""
        client = audit_app([_make_audit_log()])
        resp = client.get("/api/v1/audit/logs",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.status_code == 200

    def test_invalid_token_returns_401(self, audit_app):
        client = audit_app()
        resp = client.get("/api/v1/audit/logs",
                          headers={"Authorization": "Bearer not.a.real.token"})
        assert resp.status_code == 401

    def test_malformed_bearer_returns_401(self, audit_app):
        client = audit_app()
        resp = client.get("/api/v1/audit/logs",
                          headers={"Authorization": "Token abc123"})
        assert resp.status_code == 401


# ── Format de réponse ─────────────────────────────────────────────────────────

class TestAuditResponseFormat:

    def test_returns_list(self, audit_app):
        client = audit_app([_make_audit_log()])
        resp = client.get("/api/v1/audit/logs",
                          headers=_bearer(UserRole.ADMIN))
        assert isinstance(resp.json(), list)

    def test_empty_db_returns_empty_list(self, audit_app):
        client = audit_app([])
        resp = client.get("/api/v1/audit/logs",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_response_contains_required_fields(self, audit_app):
        """Chaque entrée doit contenir les champs définis dans AuditLogResponse."""
        client = audit_app([_make_audit_log()])
        resp = client.get("/api/v1/audit/logs",
                          headers=_bearer(UserRole.ADMIN))
        entry = resp.json()[0]
        required = ["id", "action", "success", "created_at"]
        for field in required:
            assert field in entry, f"Champ manquant: {field!r}"

    def test_response_action_matches_log(self, audit_app):
        log = _make_audit_log(action="migration.created")
        client = audit_app([log])
        resp = client.get("/api/v1/audit/logs",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.json()[0]["action"] == "migration.created"

    def test_success_field_is_boolean(self, audit_app):
        client = audit_app([_make_audit_log(success=True)])
        resp = client.get("/api/v1/audit/logs",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.json()[0]["success"] is True

    def test_multiple_logs_returned(self, audit_app):
        logs = [_make_audit_log(action=f"action.{i}") for i in range(3)]
        client = audit_app(logs)
        resp = client.get("/api/v1/audit/logs",
                          headers=_bearer(UserRole.ADMIN))
        assert len(resp.json()) == 3

    def test_content_type_is_json(self, audit_app):
        client = audit_app([])
        resp = client.get("/api/v1/audit/logs",
                          headers=_bearer(UserRole.ADMIN))
        assert "application/json" in resp.headers.get("content-type", "")


# ── Paramètres de pagination ──────────────────────────────────────────────────

class TestAuditPagination:

    def test_limit_param_accepted(self, audit_app):
        """Le paramètre limit=10 est accepté sans erreur 422."""
        client = audit_app([])
        resp = client.get("/api/v1/audit/logs?limit=10",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.status_code == 200

    def test_skip_param_accepted(self, audit_app):
        """Le paramètre skip=5 est accepté sans erreur 422."""
        client = audit_app([])
        resp = client.get("/api/v1/audit/logs?skip=5",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.status_code == 200

    def test_limit_zero_returns_422(self, audit_app):
        """limit=0 est inférieur au minimum (ge=1) → HTTP 422."""
        client = audit_app([])
        resp = client.get("/api/v1/audit/logs?limit=0",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.status_code == 422

    def test_limit_above_500_returns_422(self, audit_app):
        """limit=501 dépasse le maximum (le=500) → HTTP 422."""
        client = audit_app([])
        resp = client.get("/api/v1/audit/logs?limit=501",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.status_code == 422

    def test_skip_negative_returns_422(self, audit_app):
        """skip=-1 est invalide (ge=0) → HTTP 422."""
        client = audit_app([])
        resp = client.get("/api/v1/audit/logs?skip=-1",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.status_code == 422

    def test_combined_skip_limit_accepted(self, audit_app):
        client = audit_app([])
        resp = client.get("/api/v1/audit/logs?skip=0&limit=50",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.status_code == 200


# ── Paramètres de filtre ──────────────────────────────────────────────────────

class TestAuditFilters:
    """Vérifie que les query params de filtre sont transmis sans erreur 422.
    La logique de filtrage réelle est dans la couche SQL (non testée ici sans vraie DB)."""

    def test_action_filter_accepted(self, audit_app):
        client = audit_app([])
        resp = client.get("/api/v1/audit/logs?action=migration",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.status_code == 200

    def test_user_id_filter_accepted(self, audit_app):
        client = audit_app([])
        resp = client.get("/api/v1/audit/logs?user_id=user-001",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.status_code == 200

    def test_resource_id_filter_accepted(self, audit_app):
        client = audit_app([])
        rid = str(uuid.uuid4())
        resp = client.get(f"/api/v1/audit/logs?resource_id={rid}",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.status_code == 200

    def test_success_true_filter_accepted(self, audit_app):
        client = audit_app([])
        resp = client.get("/api/v1/audit/logs?success=true",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.status_code == 200

    def test_success_false_filter_accepted(self, audit_app):
        client = audit_app([])
        resp = client.get("/api/v1/audit/logs?success=false",
                          headers=_bearer(UserRole.ADMIN))
        assert resp.status_code == 200

    def test_all_filters_combined_accepted(self, audit_app):
        """Tous les filtres combinés → HTTP 200 (pas de collision de paramètres)."""
        client = audit_app([])
        resp = client.get(
            "/api/v1/audit/logs?action=migration&user_id=u1&success=true&skip=0&limit=20",
            headers=_bearer(UserRole.ADMIN),
        )
        assert resp.status_code == 200


# ── Sécurité : tokens forgés ──────────────────────────────────────────────────

class TestAuditTokenSecurity:

    def test_forged_admin_token_rejected(self, audit_app):
        """Un token signé avec la mauvaise clé est rejeté même s'il claim role=admin."""
        from jose import jwt as jose_jwt
        from datetime import timedelta
        payload = {
            "sub": "attacker",
            "email": "evil@hacker.com",
            "role": "admin",
            "type": "access",
            "exp": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
        }
        forged = jose_jwt.encode(payload, "wrong-key-32-chars-long-padding!!", algorithm="HS256")
        client = audit_app([])
        resp = client.get("/api/v1/audit/logs",
                          headers={"Authorization": f"Bearer {forged}"})
        assert resp.status_code == 401

    def test_expired_admin_token_rejected(self, audit_app):
        from jose import jwt as jose_jwt
        from datetime import timedelta
        payload = {
            "sub": "admin-1",
            "email": "admin@test.com",
            "role": "admin",
            "type": "access",
            "exp": (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp(),
        }
        expired = jose_jwt.encode(payload, _SECRET, algorithm=_ALGO)
        client = audit_app([])
        resp = client.get("/api/v1/audit/logs",
                          headers={"Authorization": f"Bearer {expired}"})
        assert resp.status_code == 401
