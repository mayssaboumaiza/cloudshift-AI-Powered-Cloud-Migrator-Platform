"""
service/credentials/broker.py — SecretBroker: single gateway for all secret I/O.

The broker is the ONLY layer allowed to see plaintext secret values.
All callers receive or supply opaque paths; actual values never leave this module
into logs, DB columns, or API responses.

Vault path convention (KV v2, mount = VAULT_MOUNT env var, default "cloud-migrator"):
    migrations/{migration_id}/{role}

Roles: github_token | aws | gcp | azure | ssh_key

Factory:
    get_secret_broker() → SecretBroker singleton
    Vault if VAULT_URL + VAULT_TOKEN set, encrypted-file fallback otherwise.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("SecretBroker")

# ── Abstract interface ────────────────────────────────────────────────────────

class SecretBroker(ABC):
    """Abstract interface for secret storage backends."""

    @abstractmethod
    def put(self, path: str, data: dict[str, Any]) -> str:
        """Store *data* at *path*. Returns the canonical vault_path stored."""

    def store(self, path: str, data: dict[str, Any]) -> str:
        """Alias for put() — used by TerraformRunner._generate_tf_var_secrets."""
        return self.put(path, data)

    @abstractmethod
    def get(self, path: str) -> dict[str, Any] | None:
        """Retrieve secret by vault_path. Returns None if not found."""

    @abstractmethod
    def delete(self, path: str) -> None:
        """Delete secret at path. No-op if not found."""

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Return True if a secret exists at path."""

    @staticmethod
    def migration_path(migration_id: str, role: str) -> str:
        """Canonical vault path for a migration-scoped secret."""
        safe_id = "".join(c for c in migration_id if c.isalnum() or c in "-_")
        safe_role = "".join(c for c in role if c.isalnum() or c in "-_")
        return f"migrations/{safe_id}/{safe_role}"


# ── Singleton factory ─────────────────────────────────────────────────────────

_broker: SecretBroker | None = None


def get_secret_broker() -> SecretBroker:
    """Return the singleton SecretBroker.

    Resolution order:
    1. HashiCorp Vault (VAULT_URL + VAULT_TOKEN set)
    2. Encrypted-file backend (dev / no-Vault fallback)
    """
    global _broker
    if _broker is not None:
        return _broker

    vault_url = os.getenv("VAULT_URL")
    vault_token = os.getenv("VAULT_TOKEN")
    vault_mount = os.getenv("VAULT_MOUNT", "cloud-migrator")

    if vault_url and vault_token:
        try:
            from services.credentials.backends.vault import VaultBroker
            _broker = VaultBroker(url=vault_url, token=vault_token, mount=vault_mount)
            logger.info("SecretBroker: Vault backend at %s (mount=%s)", vault_url, vault_mount)
            return _broker
        except Exception as exc:
            logger.warning("SecretBroker: Vault unavailable (%s) — falling back to file backend", exc)

    from services.credentials.backends.file_backend import FileBroker
    storage_dir = os.getenv("SECRETS_DIR", "./data/secrets")
    fernet_key = os.getenv("CREDENTIALS_FERNET_KEY") or os.getenv("FERNET_KEY")
    _broker = FileBroker(storage_dir=storage_dir, fernet_key=fernet_key)
    logger.info("SecretBroker: file backend at %s", storage_dir)
    return _broker


def reset_broker() -> None:
    """Reset singleton — for testing only."""
    global _broker
    _broker = None
