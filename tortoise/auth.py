"""P1-9 #6984: API key Bearer token auth + dev bypass + secret hashing.

If TORTOISE_API_KEY is set, require `Authorization: Bearer <key>`.
If not set, warn but allow (dev mode).

#7395: hash_api_key() for encrypting secrets at rest in the graph.
Uses PBKDF2-HMAC-SHA256 with per-key random salt stored alongside the hash.
TORTOISE_SECRET_PEPPER is mandatory — injected as additional PBKDF2 input.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import logging
import os
import secrets

_logger = logging.getLogger(__name__)

# TORTOISE_SECRET_PEPPER is mandatory. Without a stable pepper, API key
# hashes cannot be verified across process restarts (#67).
_SECRET_PEPPER = os.environ.get("TORTOISE_SECRET_PEPPER", "")

if not _SECRET_PEPPER:
    raise RuntimeError(
        "TORTOISE_SECRET_PEPPER is not set. "
        "Set TORTOISE_SECRET_PEPPER in production to ensure API key hashes "
        "survive process restart. API key hashes cannot be verified without "
        "a stable pepper value."
    )

_PEPPER_BYTES = _SECRET_PEPPER.encode()


def require_auth(headers: dict | None = None) -> bool:
    """Check if the request is authorized. Returns True if allowed.

    Dev mode (no TORTOISE_API_KEY set): always True.
    Production mode: requires `Authorization: Bearer <key>` header match.
    """
    api_key = os.environ.get("TORTOISE_API_KEY", "")
    if not api_key:
        _logger.warning("TORTOISE_API_KEY not set — running in dev mode (no auth)")
        return True

    if headers is None:
        return False

    auth = headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        return _hmac.compare_digest(token, api_key)

    return False


def is_dev_mode() -> bool:
    """Return True if no API key is configured (dev bypass active)."""
    return not bool(os.environ.get("TORTOISE_API_KEY", ""))


def hash_api_key(key: str) -> str:
    """PBKDF2-HMAC-SHA256 hash an API key for at-rest storage. Not reversible.

    Uses a per-key 32-byte random salt stored alongside the hash.
    TORTOISE_SECRET_PEPPER is injected as additional key material.
    100,000 iterations.

    Returns "salt_hex:hash_hex" — store this full string; pass it to
    verify_api_key() which extracts the salt.
    """
    per_key_salt = secrets.token_bytes(32)
    # Pepper is mixed into the key material (not used as salt)
    key_material = key.encode() + _PEPPER_BYTES
    digest = hashlib.pbkdf2_hmac(
        "sha256", key_material, per_key_salt, 100_000
    )
    return f"{per_key_salt.hex()}:{digest.hex()}"


def verify_api_key(key: str, stored: str) -> bool:
    """Verify a provided API key against a stored "salt:hash" string.

    Parses the per-key salt from the stored value, recomputes the hash,
    and compares in constant time via hashlib.compare_digest.
    """
    try:
        salt_hex, expected_hex = stored.split(":", 1)
        if len(salt_hex) != 64:
            return False
    except (ValueError, AttributeError):
        return False
    per_key_salt = bytes.fromhex(salt_hex)
    key_material = key.encode() + _PEPPER_BYTES
    computed = hashlib.pbkdf2_hmac(
        "sha256", key_material, per_key_salt, 100_000
    )
    return _hmac.compare_digest(computed.hex(), expected_hex)
