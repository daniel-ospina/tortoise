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
import threading as _threading

_logger = logging.getLogger(__name__)

# TORTOISE_SECRET_PEPPER is mandatory in PRODUCTION. Without a stable pepper,
# API key hashes cannot be verified across process restarts (#67).
# Dev mode (no TORTOISE_API_KEY): auth is bypassed and hashing is never used,
# so fall back to a stable dev-only pepper instead of failing at import
# (which broke local MCP server / pi sessions and the test suite).
_DEV_PEPPER = "dev-mode-tortoise-pepper-do-not-use-in-production"
_SECRET_PEPPER = os.environ.get("TORTOISE_SECRET_PEPPER", "")
if not _SECRET_PEPPER:
    if os.environ.get("TORTOISE_API_KEY"):
        raise RuntimeError(
            "TORTOISE_API_KEY is the hosted/cloud key — it is not used by local "
            "stdio MCP and causes this startup error. For local stdio, UNSET "
            "TORTOISE_API_KEY (dev mode). For authenticated local MCP, run "
            "'tortoise serve --http' (needs TORTOISE_SECRET_PEPPER for key "
            "hashing; set a stable value, e.g. openssl rand -hex 32). "
            "API key hashes cannot be verified without a stable pepper value."
        )
    _SECRET_PEPPER = _DEV_PEPPER

_PEPPER_BYTES = _SECRET_PEPPER.encode()

# #2204: the dev-pepper fallback is NOISE at module import — most processes
# (doctor/init/index, plain SDK use) never touch the pepper, and dev mode
# (no TORTOISE_API_KEY) bypasses auth entirely, so the fallback is inert
# there. The warning that matters is the one at first actual pepper USE
# (hash_api_key / verify_api_key / lookup_hash) in a process that really
# does key hashing with the dev fallback — emitted once per process via
# _warn_dev_pepper_once(). Keeps `import tortoise.*` clean without masking
# the production misconfig signal (missing TORTOISE_SECRET_PEPPER where keys
# are actually hashed/verified).
_DEV_PEPPER_WARNED = False
_DEV_PEPPER_WARN_LOCK = _threading.Lock()


def _warn_dev_pepper_once() -> None:
    """Warn (once per process) when key hashing runs on the dev pepper."""
    global _DEV_PEPPER_WARNED
    if _SECRET_PEPPER != _DEV_PEPPER or _DEV_PEPPER_WARNED:
        return
    with _DEV_PEPPER_WARN_LOCK:
        if _DEV_PEPPER_WARNED:
            return
        _logger.warning(
            "TORTOISE_SECRET_PEPPER not set — hashing with dev-mode pepper. "
            "Set TORTOISE_SECRET_PEPPER before minting/verifying keys in any "
            "non-dev deployment."
        )
        _DEV_PEPPER_WARNED = True


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


# C2 (#2111): accepted API-key prefixes. ``tt_`` = legacy/current mints;
# ``tk_`` = C2/C3 per-graph scoped keys (epic #2083 — minted by the
# provisioning service). A tuple so callers use ``token.startswith(PREFIXES)``.
API_KEY_PREFIXES = ("tt_", "tk_")


def hash_api_key(key: str) -> str:
    """PBKDF2-HMAC-SHA256 hash an API key for at-rest storage. Not reversible.

    Uses a per-key 32-byte random salt stored alongside the hash.
    TORTOISE_SECRET_PEPPER is injected as additional key material.
    100,000 iterations.

    Returns "salt_hex:hash_hex" — store this full string; pass it to
    verify_api_key() which extracts the salt.
    """
    _warn_dev_pepper_once()
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
    _warn_dev_pepper_once()
    try:
        salt_hex, expected_hex = stored.split(":", 1)
        if len(salt_hex) != 64:
            return False
    except (ValueError, AttributeError):
        return False
    # #750.1: a non-hex salt (corrupt/garbage stored value) must fail closed
    # with False, not raise ValueError → 500.
    try:
        per_key_salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    if len(per_key_salt) != 32:
        return False
    key_material = key.encode() + _PEPPER_BYTES
    computed = hashlib.pbkdf2_hmac(
        "sha256", key_material, per_key_salt, 100_000
    )
    return _hmac.compare_digest(computed.hex(), expected_hex)


def lookup_hash(key: str) -> str:
    """Instant key-lookup hash for the Supabase control plane (#669 plan P1-1).

    lookup_hash := SHA-256(pepper + key), hex-encoded. Unlike the salted
    PBKDF2 hash_api_key() (verification-only, iterated, salt per key), this
    is a deterministic one-way digest used to LOOK UP a key at request time:
    the presented key is hashed and matched against the indexed
    lookup_hash column (team_memberships / api_keys) — O(1) index equality,
    no scan. The pepper is held in app code (never the DB), so the DB cannot
    reverse the digest without it.

    Construction is "pepper first, then key" — the plan's exact spelling
    ("SHA-256(pepper + key)"). The TS mirror lives in
    supabase/functions/_shared/lookup.ts and MUST stay byte-identical;
    supabase/tests/lookup_parity.test.mjs locks both sides to the same
    test vectors. Do NOT change the order here without updating the mirror
    and the parity vectors.
    """
    _warn_dev_pepper_once()
    return hashlib.sha256(_PEPPER_BYTES + key.encode()).hexdigest()
