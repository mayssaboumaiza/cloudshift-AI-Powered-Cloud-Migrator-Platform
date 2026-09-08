"""
vault_store.py - Encrypted storage for user credentials.

Uses HashiCorp Vault (KV v2) as the primary backend, with an encrypted
file backend as a dev fallback. The file backend uses Fernet (AES-128 GCM
via cryptography) so local development doesn't require Vault.

Env vars:
  VAULT_URL            - Vault address (e.g. http://localhost:8200)
  VAULT_TOKEN          - Vault token with write access to /cloud-migrator/*
  VAULT_MOUNT          - KV v2 mount point (default: cloud-migrator)
  CREDENTIALS_FERNET_KEY - Base64 key for the file backend fallback

NEVER log credentials, even truncated.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("VaultStore")

_MOUNT = os.getenv("VAULT_MOUNT", "cloud-migrator")


class CredentialStore:
    """Abstract store. The concrete backend is chosen at construction time."""

    def store(self, user_id: str, provider: str, creds: dict) -> None:
        raise NotImplementedError

    def get(self, user_id: str, provider: str) -> dict | None:
        raise NotImplementedError

    def delete(self, user_id: str, provider: str) -> None:
        raise NotImplementedError


class VaultBackend(CredentialStore):
    def __init__(self, url: str, token: str, mount: str = _MOUNT):
        import hvac
        self.client = hvac.Client(url=url, token=token)
        self.mount = mount
        if not self.client.is_authenticated():
            raise RuntimeError("Vault authentication failed")

    def store(self, user_id: str, provider: str, creds: dict) -> None:
        self.client.secrets.kv.v2.create_or_update_secret(
            path=f"users/{user_id}/{provider}",
            secret=creds,
            mount_point=self.mount,
        )

    def get(self, user_id: str, provider: str) -> dict | None:
        try:
            resp = self.client.secrets.kv.v2.read_secret_version(
                path=f"users/{user_id}/{provider}", mount_point=self.mount,
            )
            return resp["data"]["data"]
        except Exception:
            return None

    def delete(self, user_id: str, provider: str) -> None:
        try:
            self.client.secrets.kv.v2.delete_metadata_and_all_versions(
                path=f"users/{user_id}/{provider}", mount_point=self.mount,
            )
        except Exception as e:
            logger.warning(f"Vault delete failed: {e}")


class EncryptedFileBackend(CredentialStore):
    """Dev-only fallback. Keep the key OFF the repo."""

    def __init__(self, storage_dir: str | Path, key: bytes):
        from cryptography.fernet import Fernet
        self.dir = Path(storage_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fernet = Fernet(key)

    def _path(self, user_id: str, provider: str) -> Path:
        # Avoid any path traversal from untrusted user_id/provider
        safe_user = "".join(c for c in user_id if c.isalnum() or c in "-_")
        safe_prov = "".join(c for c in provider if c.isalnum() or c in "-_")
        return self.dir / f"{safe_user}_{safe_prov}.enc"

    def store(self, user_id: str, provider: str, creds: dict) -> None:
        payload = json.dumps(creds).encode("utf-8")
        self._path(user_id, provider).write_bytes(self.fernet.encrypt(payload))

    def get(self, user_id: str, provider: str) -> dict | None:
        p = self._path(user_id, provider)
        if not p.exists():
            return None
        try:
            return json.loads(self.fernet.decrypt(p.read_bytes()).decode("utf-8"))
        except Exception as e:
            logger.warning(f"Decrypt failed: {type(e).__name__}")
            return None

    def delete(self, user_id: str, provider: str) -> None:
        p = self._path(user_id, provider)
        if p.exists():
            p.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────
_singleton: CredentialStore | None = None


def get_credential_store() -> CredentialStore:
    """Return Vault-backed store if configured, else the dev file backend."""
    global _singleton
    if _singleton is not None:
        return _singleton

    url = os.getenv("VAULT_URL")
    token = os.getenv("VAULT_TOKEN")
    if url and token:
        try:
            _singleton = VaultBackend(url=url, token=token, mount=_MOUNT)
            logger.info(f"CredentialStore: Vault backend at {url}")
            return _singleton
        except Exception as e:
            logger.warning(f"Vault unavailable ({e}) — falling back to file backend")

    key_str = os.getenv("CREDENTIALS_FERNET_KEY")
    if not key_str:
        # Generate a warning — this should only happen in dev.
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        logger.warning(
            "CREDENTIALS_FERNET_KEY not set — generated ephemeral key. "
            "Stored credentials will NOT survive process restart."
        )
    else:
        key = key_str.encode() if isinstance(key_str, str) else key_str

    storage_dir = os.getenv("CREDENTIALS_DIR", "./data/credentials")
    _singleton = EncryptedFileBackend(storage_dir=storage_dir, key=key)
    logger.info(f"CredentialStore: encrypted file backend at {storage_dir}")
    return _singleton
