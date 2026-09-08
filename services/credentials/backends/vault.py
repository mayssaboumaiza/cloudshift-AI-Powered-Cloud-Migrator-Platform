"""
service/credentials/backends/vault.py — HashiCorp Vault KV v2 backend.

Requires:
    hvac>=2.1.0  (already in requirements.txt)
    VAULT_URL    — e.g. http://localhost:8200
    VAULT_TOKEN  — Vault token with read/write on VAULT_MOUNT/data/migrations/*
    VAULT_MOUNT  — KV v2 mount point (default: cloud-migrator)

NEVER log secret values or partial values.
"""
from __future__ import annotations

import logging
from typing import Any

from services.credentials.broker import SecretBroker

logger = logging.getLogger("SecretBroker.Vault")


class VaultBroker(SecretBroker):
    """Vault KV v2 implementation of SecretBroker."""

    def __init__(self, url: str, token: str, mount: str = "cloud-migrator") -> None:
        import hvac
        self._client = hvac.Client(url=url, token=token)
        self._mount = mount
        if not self._client.is_authenticated():
            raise RuntimeError(f"Vault authentication failed at {url}")
        logger.info("VaultBroker authenticated at %s (mount=%s)", url, mount)

    # ── SecretBroker interface ────────────────────────────────────────────────

    def put(self, path: str, data: dict[str, Any]) -> str:
        """Write *data* to Vault at *path*. Returns path."""
        self._client.secrets.kv.v2.create_or_update_secret(
            path=path,
            secret=data,
            mount_point=self._mount,
        )
        logger.debug("VaultBroker.put: stored secret at path=%s", path)
        return path

    def get(self, path: str) -> dict[str, Any] | None:
        """Read secret from Vault. Returns data dict or None if not found."""
        try:
            resp = self._client.secrets.kv.v2.read_secret_version(
                path=path,
                mount_point=self._mount,
                raise_on_deleted_version=True,
            )
            return resp["data"]["data"]
        except Exception as exc:
            logger.debug("VaultBroker.get: miss at path=%s (%s)", path, type(exc).__name__)
            return None

    def delete(self, path: str) -> None:
        """Permanently delete all versions of the secret at *path*."""
        try:
            self._client.secrets.kv.v2.delete_metadata_and_all_versions(
                path=path,
                mount_point=self._mount,
            )
            logger.debug("VaultBroker.delete: deleted path=%s", path)
        except Exception as exc:
            logger.warning("VaultBroker.delete: failed for path=%s (%s)", path, exc)

    def exists(self, path: str) -> bool:
        """Return True if a live (non-deleted) secret exists at *path*."""
        return self.get(path) is not None
