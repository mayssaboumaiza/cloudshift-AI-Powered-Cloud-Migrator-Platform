"""
service/credentials/backends/file_backend.py — Encrypted-file dev fallback.

Uses Fernet (AES-128 CBC + HMAC-SHA256) to encrypt secrets as JSON files.
Keys do NOT survive a process restart unless CREDENTIALS_FERNET_KEY is set.

WARNING: This backend is for local development only.
         In staging/production always configure Vault.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from services.credentials.broker import SecretBroker

logger = logging.getLogger("SecretBroker.File")


class FileBroker(SecretBroker):
    """Fernet-encrypted file-system backend (dev-only)."""

    def __init__(self, storage_dir: str | Path, fernet_key: str | bytes | None = None) -> None:
        from cryptography.fernet import Fernet
        self._dir = Path(storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

        if fernet_key is None:
            key = Fernet.generate_key()
            logger.warning(
                "FileBroker: no CREDENTIALS_FERNET_KEY set — generated ephemeral key. "
                "Secrets will NOT survive process restart."
            )
        else:
            key = fernet_key.encode() if isinstance(fernet_key, str) else fernet_key

        self._fernet = Fernet(key)

    # ── Path helpers ──────────────────────────────────────────────────────────

    def _file_path(self, vault_path: str) -> Path:
        """Convert vault path (e.g. migrations/abc/github_token) to a safe file path."""
        safe = vault_path.replace("/", "__").replace("..", "")
        safe = "".join(c for c in safe if c.isalnum() or c in "-_")
        return self._dir / f"{safe}.enc"

    # ── SecretBroker interface ────────────────────────────────────────────────

    def put(self, path: str, data: dict[str, Any]) -> str:
        payload = json.dumps(data).encode("utf-8")
        self._file_path(path).write_bytes(self._fernet.encrypt(payload))
        logger.debug("FileBroker.put: stored at path=%s", path)
        return path

    def get(self, path: str) -> dict[str, Any] | None:
        p = self._file_path(path)
        if not p.exists():
            return None
        try:
            return json.loads(self._fernet.decrypt(p.read_bytes()).decode("utf-8"))
        except Exception as exc:
            logger.warning("FileBroker.get: decrypt failed at path=%s (%s)", path, type(exc).__name__)
            return None

    def delete(self, path: str) -> None:
        p = self._file_path(path)
        if p.exists():
            p.unlink()
            logger.debug("FileBroker.delete: removed path=%s", path)

    def exists(self, path: str) -> bool:
        return self._file_path(path).exists()
