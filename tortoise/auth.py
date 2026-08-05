"""P1-9 #6984: API key Bearer token auth + dev bypass + secret hashing.

If TORTOISE_API_KEY is set, require `Authorization: Bearer <key>`.
If not set, warn but allow (dev mode).

#7395: hash_api_key() for encrypting secrets at rest in the graph.
Uses PBKDF2-HMAC-SHA256 with TORTOISE_SECRET_PEPPER as salt if set,
or a random app-level salt generated at startup otherwise.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import logging
import os
import secrets

_logger = logging.getLogger(__name__)

# Module-level constants for pepper only (key check reads env directly
# so tests can manipulate os.environ without reloading dependents)
_SECRET_PEPPER = os.environ.get("TORTOISE_SECRET_PEPPER", "")

if not _SECRET_PEPPER:
    _logger.warning(
        "TORTOISE_SECRET_PEPPER not set — using random app-level salt "
        "for PBKDF2. API key hashes will not survive process restart. "
        "Set TORTOISE_SECRET_PEPPER in production."
    )
    _FALLBACK_SALT = secrets.token_bytes(32)
else:
    _FALLBACK_SALT = None


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

    Uses TORTOISE_SECRET_PEPPER as salt if set, otherwise a random
    app-level salt generated at startup. 100,000 iterations.

    Returns hex digest string. The original key is returned to the caller
    at creation time and never stored in plaintext.
    """
    salt = _SECRET_PEPPER.encode() if _SECRET_PEPPER else _FALLBACK_SALT
    return hashlib.pbkdf2_hmac(
        "sha256", key.encode(), salt, 100_000
    ).hex()


def verify_api_key(key: str, stored_hash: str) -> bool:
    """Verify a provided API key against a stored hash.

    Uses constant-time comparison via hashlib.compare_digest.
    """
    return _hmac.compare_digest(hash_api_key(key), stored_hash)
