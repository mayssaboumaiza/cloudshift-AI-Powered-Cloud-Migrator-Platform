"""
core/crypto.py - Fernet-based encryption utilities for sensitive fields.

Usage:
    from core.token_encryption import encrypt_token, decrypt_token

    encrypted = encrypt_token("YOUR_GITHUB_TOKEN")          # stored in DB
    plaintext = decrypt_token(encrypted)            # used by agents

The Fernet key MUST be set in the environment:
    FERNET_KEY=<base64-url-safe 32-byte key>

Generate a key once with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import logging
import os

logger = logging.getLogger("Crypto")

try:
    from cryptography.fernet import Fernet, InvalidToken
    _HAS_FERNET = True
except ImportError:
    _HAS_FERNET = False
    logger.warning(
        "cryptography package not installed — GitHub tokens stored in plaintext. "
        "Run: pip install cryptography"
    )


def _get_fernet() -> "Fernet | None":
    """Return a Fernet instance from FERNET_KEY env var, or None if unavailable."""
    if not _HAS_FERNET:
        return None
    key = os.getenv("FERNET_KEY", "").strip()
    if not key:
        logger.warning(
            "FERNET_KEY env var not set — GitHub tokens stored in plaintext. "
            "Generate a key: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
        return None
    try:
        return Fernet(key.encode())
    except Exception as exc:
        logger.error(f"Invalid FERNET_KEY: {exc} — tokens stored in plaintext")
        return None


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token string. Returns encrypted bytes as a UTF-8 string.

    If Fernet is unavailable or FERNET_KEY is not set, returns the plaintext unchanged
    (with a warning). This ensures the system still works without encryption configured.

    Args:
        plaintext: The raw token string (e.g. "YOUR_GITHUB_TOKENxxxxxxxx").

    Returns:
        Encrypted token string (URL-safe base64), or plaintext if encryption unavailable.
    """
    if not plaintext:
        return plaintext
    fernet = _get_fernet()
    if fernet is None:
        return plaintext
    try:
        return fernet.encrypt(plaintext.encode()).decode()
    except Exception as exc:
        logger.error(f"encrypt_token failed: {exc} — storing plaintext")
        return plaintext


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a token string previously encrypted with encrypt_token.

    If decryption fails (wrong key, plaintext stored), returns the input unchanged.
    This ensures backward compatibility with un-encrypted tokens already in DB.

    Args:
        ciphertext: The encrypted token string from the database.

    Returns:
        Decrypted plaintext token, or ciphertext if decryption not possible.
    """
    if not ciphertext:
        return ciphertext
    fernet = _get_fernet()
    if fernet is None:
        return ciphertext
    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except Exception:
        # Token was stored in plaintext (before encryption was added) — return as-is
        logger.debug("decrypt_token: token appears to be plaintext (pre-encryption) — returning as-is")
        return ciphertext


def is_encrypted(value: str) -> bool:
    """Heuristic check: Fernet tokens start with 'gAAA' (base64 encoded prefix)."""
    return bool(value and value.startswith("gAAA"))

