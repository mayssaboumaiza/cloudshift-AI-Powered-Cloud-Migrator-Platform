"""Unit tests for core/vault_helpers.py — GitHub token resolution."""
import os
from unittest.mock import MagicMock, patch

import pytest

from core.vault_helpers import get_github_token


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


def _make_store(token: str | None = "FAKE_GITHUB_TOKEN") -> MagicMock:
    store = MagicMock()
    store.get.return_value = {"token": token} if token else None
    return store


# ─────────────────────────────────────────────────────────────────────────────
# Tests — vault path
# ─────────────────────────────────────────────────────────────────────────────

def test_get_github_token_calls_store_get_with_migration_id():
    store = _make_store("FAKE_TOKEN")
    with patch("core.vault_helpers.get_credential_store", return_value=store):
        result = get_github_token("mig-123")
    store.get.assert_called_once_with(user_id="mig-123", provider="github_token")


def test_get_github_token_returns_value_from_vault():
    store = _make_store("FAKE_TOKEN_FROM_VAULT")
    with patch("services.credentials.vault_store.get_credential_store", return_value=store), \
         patch("core.vault_helpers.get_credential_store", return_value=store):
        result = get_github_token("mig-456")
    assert result == "FAKE_TOKEN_FROM_VAULT"


def test_get_github_token_falls_back_to_env_when_vault_empty(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "FAKE_TOKEN_FROM_ENV")
    store = _make_store(token=None)  # vault returns no creds
    with patch("core.vault_helpers.get_credential_store", return_value=store):
        result = get_github_token("mig-789")
    assert result == "FAKE_TOKEN_FROM_ENV"


def test_get_github_token_falls_back_to_env_when_vault_raises(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "FAKE_ENV_TOKEN")
    store = MagicMock()
    store.get.side_effect = RuntimeError("Vault unavailable")
    with patch("core.vault_helpers.get_credential_store", return_value=store):
        result = get_github_token("mig-err")
    assert result == "FAKE_ENV_TOKEN"


def test_get_github_token_returns_empty_string_when_nothing_configured():
    store = _make_store(token=None)
    with patch("core.vault_helpers.get_credential_store", return_value=store):
        result = get_github_token("mig-000")
    assert result == ""


# ─────────────────────────────────────────────────────────────────────────────
# Tests — empty migration_id (no vault lookup)
# ─────────────────────────────────────────────────────────────────────────────

def test_get_github_token_skips_vault_for_empty_migration_id(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "FAKE_GLOBAL_TOKEN")
    store = MagicMock()
    with patch("core.vault_helpers.get_credential_store", return_value=store):
        result = get_github_token("")
    store.get.assert_not_called()
    assert result == "FAKE_GLOBAL_TOKEN"


def test_get_github_token_returns_empty_for_empty_id_and_no_env():
    store = MagicMock()
    with patch("core.vault_helpers.get_credential_store", return_value=store):
        result = get_github_token("")
    assert result == ""

