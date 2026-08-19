"""Session-endpoint auth — Supabase JWT verification via JWKS (D1, plan §5.3 #2b).

The two-tier auth model (plan §5.3 #2/#2b): session endpoints (E1–E8:
/session/key, /teams, /graphs, /invites, member management) authenticate with
a Supabase access token verified server-side via JWKS. The data-plane
(/v1/points, /v1/search, /v1/sessions, /v1/team, /v1/team/keys, MCP) stays on
`tt_` keys via get_current_team.

Verification: fetch `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`, verify the
RS256 **or ES256** signature (alg dispatch per token header — #1460: this
project signs ES256; RS256 kept for older projects/selfhost), check issuer +
audience + exp/iat/nbf via PyJWT. JWKS cached with TTL; KID-miss triggers a
refetch (R16). Shared-HMAC (SUPABASE_JWT_SECRET) is rejected — JWKS is the
standard, key-rotation-safe path.

Issue #1460: the verifier previously only handled RS256 (`jwk["n"]` KeyError
on the EC JWKS → unhandled 500 → no CORS headers → browser CORS-wall →
dashboard login wall). The verify core now delegates to PyJWT
(`pyjwt[crypto] 2.13`, already shipped via `mcp`) with a fail-closed boundary:
every verify-path failure is an HTTPException (401/503), never a raw 500.
See docs/plans/2026-08-18-es256-session-auth.md (9 review cycles + second-model
gate) for the full matrix.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import secrets
import time

import httpx
import jwt as pyjwt
from cryptography.exceptions import UnsupportedAlgorithm
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ybetwichurajbfswfeqa.supabase.co")
_JWKS_URL = f"{_SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
_JWKS_TTL = float(os.environ.get("TORTOISE_JWKS_TTL", "300"))  # seconds
_FETCH_TIMEOUT = float(os.environ.get("TORTOISE_JWKS_TIMEOUT", "5"))  # seconds
_COOLDOWN_S = float(os.environ.get("TORTOISE_JWKS_COOLDOWN", "30"))  # failure/miss cooldown
_MAX_JWKS_BYTES = 65536  # post-buffer JWKS body cap (defense-in-depth; httpx buffers first)
_MAX_TOKEN_BYTES = 16000  # repo-enforced token cap — BELOW the server's ~16KB
# header-line limit (uvicorn/h11 max_incomplete_event_size) so the repo guard —
# not a raw server 400/431 without CORS headers — is the first line of rejection
# (pyjwt 2.13 has no max_length kwarg). Code-review #1467 P2.


class _JWKSCache:
    """In-process {kid: jwk} cache — TTL, stale-serve, kid-aware single-flight,
    failure/miss cooldown. Per-process semantics: the deployment is
    single-worker (no `--workers`), so per-process caching is sound
    (mirrors the note at hosted_api.py:7120).

    Never evicts last-good keys: on ANY failed/empty/malformed fetch the
    previous key set keeps serving (bounded revocation window: TTL + cooldown
    + refetch timeout). Failures arm a cooldown so an outage (or an
    unauthenticated forged-kid flood) cannot cause per-request refetches.
    """

    def __init__(self):
        self._keys: dict[str, dict] | None = None
        self._fetched_at: float = 0.0
        self._last_failure_at: float | None = None  # None = never failed (unarmed)
        self._lock = asyncio.Lock()

    async def get(self, force: bool = False, kid: str | None = None) -> dict[str, dict]:
        """Return {kid: jwk}.

        - TTL-serve when fresh; kid-aware early return when the requested kid
          already resolves (single-flight success path).
        - Cooldown-skipped fetch with no last-good keys → HTTPException 503
          (never returns None — callers must not crash on a None key set).
        - Fetch failure / zero-usable-keys / miss (force + kid absent after a
          successful refetch) arm the cooldown (`_last_failure_at`).
        - `force` bypasses the TTL but NOT the cooldown.
        """
        now = time.monotonic()
        if not force and self._keys is not None and now - self._fetched_at < _JWKS_TTL:
            return self._keys
        if kid is not None and self._keys is not None and kid in self._keys:
            return self._keys
        async with self._lock:
            now = time.monotonic()
            if not force and self._keys is not None and now - self._fetched_at < _JWKS_TTL:
                return self._keys
            if kid is not None and self._keys is not None and kid in self._keys:
                return self._keys
            # Failure/miss cooldown — inside the lock (single-flight: exactly
            # one fetch attempt per cooldown window under concurrency).
            if self._last_failure_at is not None and now - self._last_failure_at < _COOLDOWN_S:
                if self._keys is None:
                    raise HTTPException(status_code=503, detail="Session verification unavailable")
                return self._keys
            try:
                content = await _fetch_jwks()
                if len(content) > _MAX_JWKS_BYTES:
                    raise ValueError("JWKS response exceeds size cap")
                parsed = _parse_jwks(content)
                if not parsed:
                    # Zero usable keys = failure semantics: arm the cooldown,
                    # do NOT refresh the TTL (recovery via force path after the
                    # cooldown lapses, or TTL expiry), keep last-good on warm.
                    self._last_failure_at = time.monotonic()
                    if self._keys is None:
                        self._keys = {}
                    logger.warning("JWKS fetch returned zero usable keys — serving stale/empty")
                    return self._keys
                self._keys = parsed
                self._fetched_at = time.monotonic()
                if kid is not None and kid not in parsed:
                    # Miss = failure semantics: a forged-kid flood against a
                    # healthy-but-kid-absent upstream must not refetch per
                    # request. Documented tradeoff: a flood can delay a
                    # legitimately rotated key's refetch by ≤ cooldown.
                    # Code-review #1467 SEC-001: JITTER the miss window to
                    # [C, 1.5·C] so a poller cannot deterministically re-arm
                    # the cooldown at expiry (an attacker winning every round
                    # would starve key rotation indefinitely). Failure-arm
                    # below stays deterministic. CSPRNG draw (secrets) —
                    # re-review flagged MT19937 state-recovery as theoretical;
                    # secrets removes the argument at zero cost.
                    self._last_failure_at = time.monotonic() + (
                        secrets.randbelow(int(_COOLDOWN_S * 500)) / 1000
                    )
            except HTTPException:
                raise
            except Exception as exc:  # network, json, shape, size, filter errors
                self._last_failure_at = time.monotonic()
                if self._keys is None:
                    logger.warning("JWKS unavailable (cold) — 503: %s", exc)
                    raise HTTPException(
                        status_code=503, detail="Session verification unavailable"
                    ) from exc
                logger.warning("JWKS fetch failed — serving stale: %s", exc)
            return self._keys


def _parse_jwks(content: bytes) -> dict[str, dict]:
    """Parse a JWKS body into {kid: jwk}.

    - Entries without a STRING kid (kid-less, or wrong-typed kid VALUE like
      `123`/`true`) are dropped.
    - Duplicate kids: FIRST wins (the previous `{k["kid"]: k ...}` comprehension
      was LAST-wins and could silently switch keys on a bad rotation).
    - Malformed entries raise → the caller treats the whole fetch as failed
      (fail-closed; a partial overwrite of last-good keys is never allowed).
    """
    jwks = json.loads(content)
    keys_list = jwks.get("keys")
    if not isinstance(keys_list, list):
        raise ValueError("JWKS keys is not a list")
    parsed: dict[str, dict] = {}
    for entry in keys_list:
        if not isinstance(entry, dict):
            raise ValueError("JWKS entry is not a dict")
        kid_value = entry.get("kid")
        if not isinstance(kid_value, str) or not kid_value:
            continue  # kid-less / wrong-typed kid dropped
        if kid_value in parsed:
            continue  # first-wins
        parsed[kid_value] = entry
    return parsed


async def _fetch_jwks() -> bytes:
    """Fetch the JWKS body (bounded timeout). Seam for tests."""
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
        resp = await client.get(_JWKS_URL)
        resp.raise_for_status()
        return resp.content


_jwks = _JWKSCache()


def _b64url_decode(part: str) -> bytes:
    pad = "=" * (-len(part) % 4)
    return base64.urlsafe_b64decode(part + pad)


def _decode_header(token: str) -> dict:
    """Parse + shape-check the JWT header only (PyJWT owns payload parsing).

    401 on malformed/oversized input. The 16KB length guard is repo-enforced
    defense-in-depth — pyjwt 2.13 has no `max_length` kwarg, and the effective
    HTTP cap is the server's ~16KB header-line limit anyway.
    """
    if len(token.encode()) > _MAX_TOKEN_BYTES:
        raise HTTPException(status_code=401, detail="Invalid session token")
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="Invalid session token")
    try:
        header = json.loads(_b64url_decode(parts[0]))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session token")
    if not isinstance(header, dict):
        # Non-dict header segments (valid JSON: [1,2], 123, "x") must 401,
        # never AttributeError → 500.
        raise HTTPException(status_code=401, detail="Invalid session token")
    return header


async def verify_session_jwt(request: Request) -> dict:
    """Verify the Supabase access token and return {user_id, email, app_metadata}.

    Raises 401 on missing/invalid/expired/malformed token (fail-closed), 503
    when the JWKS is unreachable with no last-good key set. KID-miss triggers a
    single-flight, cooldown-aware JWKS refetch (R16).

    Fail-closed boundary: every non-HTTPException escaping
    PyJWK.from_dict / jwt.decode is converted to 401 — an auth boundary must
    never leak an unhandled exception (a raw 500 lacks CORS headers and is
    misread by browsers as a CORS failure — the #1460 incident class).
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing session token")
    token = auth[7:]

    header = _decode_header(token)
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid.strip():
        # Missing / whitespace / non-string kid: 401 with zero network I/O.
        raise HTTPException(status_code=401, detail="Invalid session token")

    # JWKS fetch boundary — 503 on unreachable/no-last-good, stale-serve if
    # last-good exists (inside get()).
    try:
        keys = await _jwks.get()
        jwk = keys.get(kid)
        if jwk is None:
            keys = await _jwks.get(force=True, kid=kid)  # R16: single-flight, cooldown-aware
            jwk = keys.get(kid)
        if jwk is None:
            raise HTTPException(status_code=401, detail="Unknown signing key")
    except HTTPException:
        raise

    try:
        # Passing the PyJWK object (not jwk.key) keeps PyJWT's alg/kty-confusion
        # defense live: `alg != key.algorithm_name` → InvalidAlgorithmError.
        key = pyjwt.PyJWK.from_dict(jwk)
        claims = pyjwt.decode(
            token,
            key=key,
            algorithms=["RS256", "ES256"],
            audience="authenticated",
            issuer=_SUPABASE_URL.rstrip("/") + "/auth/v1",
            leeway=30,  # #750.4 clock-skew grace (old code: exp + 30)
            options={
                "require": ["sub", "exp", "iat", "iss"],  # iss required (old code rejected missing iss)
                "verify_iat": True,  # deltas vs old code: iat/nbf enforced, aud required+strict,
                "verify_nbf": True,  # exact issuer match, exp leeway inclusive
                "strict_aud": True,  # list-form aud rejected (string-exact)
            },
        )
    except pyjwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail="Invalid session token") from e
    except (KeyError, ValueError, TypeError, binascii.Error, OverflowError, RecursionError, UnsupportedAlgorithm) as e:
        # PyJWK.from_dict / wrong-typed / out-of-range claim failures are NOT
        # PyJWTError subclasses — fail closed. (binascii.Error is a ValueError
        # subclass — kept for self-documentation. RecursionError is
        # defensive: json parsers are recursion-limited (runtime-dependent —
        # CPython C-json ~10k nesting, pure-python json ~1k), so a small
        # deeply-nested header can trip it — keep the entry.
        # UnsupportedAlgorithm covers FIPS/ancient-OpenSSL backends. ⛔ All
        # names in this tuple are bound at module/function top — except-clause
        # names are evaluated at match time, before the body runs.)
        raise HTTPException(status_code=401, detail="Invalid session token") from e
    except Exception as e:
        # Fail-closed completeness: the enumerated tuple cannot be proven
        # exhaustive against the "never a raw 500" acceptance. Auth boundary:
        # ANY unhandled exception → 401. (Nothing in this try raises
        # HTTPException — the "Unknown signing key" 401 lives in the outer
        # fetch try, which re-raises HTTPException first.)
        raise HTTPException(status_code=401, detail="Invalid session token") from e

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        # pyjwt's verify_sub rejects non-string subs; the guard covers the
        # empty/whitespace-string case pyjwt accepts.
        raise HTTPException(status_code=401, detail="Invalid session token")
    app_metadata = claims.get("app_metadata")
    if app_metadata is not None and not isinstance(app_metadata, dict):
        # Downstream (claim path) does app_metadata.providers — shape-guard here
        # so a corrupted token cannot 500 one hop later.
        raise HTTPException(status_code=401, detail="Invalid session token")
    email = claims.get("email")
    if email is not None and not isinstance(email, str):
        # Consumers do string ops on user["email"].
        raise HTTPException(status_code=401, detail="Invalid session token")
    return {
        "user_id": user_id,
        "email": email,
        # #1082 (claim path): app_metadata is user-level, always present,
        # survives token refresh — unlike `amr` which is optional and
        # refresh-mutated to `token_refresh`. The claim endpoint asserts
        # app_metadata.providers ∩ {github, google} ≠ ∅ (provider-verified
        # email invariant). Additive key — existing callers read known keys.
        "app_metadata": app_metadata or {},
    }


async def get_current_user(request: Request) -> dict:
    """FastAPI dependency: session-authenticated user (JWT → user_id)."""
    return await verify_session_jwt(request)
