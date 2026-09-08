"""
vault_helpers.py — Helpers for fetching per-migration secrets from the credential store.

The GitHub token is no longer carried in MigrationState (it was stored in the LangGraph
checkpoint in plaintext). All token lookups go through the CredentialStore (Vault backend
in production, encrypted file backend in dev).
"""
import logging
import os

logger = logging.getLogger("VaultHelpers")

try:
    from services.credentials.vault_store import get_credential_store
except ImportError:  # pragma: no cover
    get_credential_store = None  # type: ignore[assignment]


def get_github_token(migration_id: str) -> str:
    """Return the GitHub token for *migration_id* from the credential store.

    Resolution order:
      1. CredentialStore (Vault KV v2 in prod, encrypted file in dev)
      2. GITHUB_TOKEN environment variable (server-level fallback)
      3. Empty string — callers must handle the missing-token case.
    """
    if not migration_id:
        return os.getenv("GITHUB_TOKEN", "")

    try:
        store = get_credential_store()
        creds = store.get(user_id=str(migration_id), provider="github_token")
        if creds:
            token = creds.get("token", "")
            if token:
                logger.debug("VaultHelpers: GitHub token retrieved for migration %s", migration_id)
                return token
    except Exception as exc:
        logger.warning(
            "VaultHelpers: credential store lookup failed for migration %s (%s) — "
            "falling back to GITHUB_TOKEN env var",
            migration_id,
            exc,
        )

    return os.getenv("GITHUB_TOKEN", "")
