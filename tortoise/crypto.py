"""Encryption helpers for at-rest secrets (#499, #324 follow-up).

Fernet (AES-128-CBC + HMAC-SHA256) symmetric encryption for OAuth tokens
stored on the Team node. The key comes from TORTOISE_ENCRYPTION_KEY
(Fly.io secret). A missing/malformed key fails loudly — silent decryption
failure would corrupt tokens.
"""
from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet, InvalidToken


def _get_key() -> bytes:
    """Return the Fernet key (32 url-safe base64 bytes) from env.

    Raises RuntimeError if unset — fail-fast, never encrypt with a default.
    """
    raw = os.environ.get("TORTOISE_ENCRYPTION_KEY")
    if not raw:
        raise RuntimeError(
            "TORTOISE_ENCRYPTION_KEY not set — required for token encryption. "
            "Generate with: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    try:
        return raw.encode() if isinstance(raw, str) else raw
    except Exception as e:
        raise RuntimeError(f"Invalid TORTOISE_ENCRYPTION_KEY: {e}")


def encrypt_token(token: str) -> str:
    """Encrypt a secret at rest. Returns url-safe base64 string."""
    if token is None:
        raise ValueError("Cannot encrypt None")
    f = Fernet(_get_key())
    return f.encrypt(token.encode("utf-8")).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt a stored secret. Raises ValueError on tamper/malformed input."""
    if not encrypted:
        raise ValueError("Cannot decrypt empty ciphertext")
    f = Fernet(_get_key())
    try:
        return f.decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError(f"Decryption failed — token tampered or wrong key: {e}")
    except Exception as e:
        raise ValueError(f"Decryption failed: {e}")
