"""Session-endpoint auth — Supabase JWT verification via JWKS (D1, plan §5.3 #2b).

The two-tier auth model (plan §5.3 #2/#2b): session endpoints (E1–E8:
/session/key, /teams, /graphs, /invites, member management) authenticate with
a Supabase access token verified server-side via JWKS. The data-plane
(/v1/points, /v1/search, /v1/sessions, /v1/team, /v1/team/keys, MCP) stays on
`tt_` keys via get_current_team.

Verification: fetch `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`, verify
RS256 signature with the matching kid, check issuer + audience (project ref)
+ exp. JWKS cached with TTL; KID-miss triggers a refetch (R16). Shared-HMAC
(SUPABASE_JWT_SECRET) is rejected — JWKS is the standard, key-rotation-safe
path.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.request

import httpx
from fastapi import HTTPException, Request

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ybetwichurajbfswfeqa.supabase.co")
_PROJECT_REF = _SUPABASE_URL.rstrip("/").split("//")[-1].split(".")[0]
_JWKS_URL = f"{_SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
_JWKS_TTL = float(os.environ.get("TORTOISE_JWKS_TTL", "300"))  # seconds
_FETCH_TIMEOUT = float(os.environ.get("TORTOISE_JWKS_TIMEOUT", "5"))


class _JWKSCache:
    def __init__(self):
        self._keys: dict[str, dict] | None = None
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> dict[str, dict]:
        """Return {kid: jwk}. Refetch if stale; KID-miss handled by caller."""
        now = time.monotonic()
        if self._keys is not None and now - self._fetched_at < _JWKS_TTL:
            return self._keys
        async with self._lock:
            now = time.monotonic()
            if self._keys is not None and now - self._fetched_at < _JWKS_TTL:
                return self._keys
            try:
                async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
                    resp = await client.get(_JWKS_URL)
                    resp.raise_for_status()
                jwks = resp.json()
                self._keys = {k["kid"]: k for k in jwks.get("keys", []) if "kid" in k}
                self._fetched_at = time.monotonic()
            except Exception:
                # Keep serving a cached key set on fetch failure (bounded outage)
                if self._keys is None:
                    raise
            return self._keys


_jwks = _JWKSCache()


def _b64url_decode(part: str) -> bytes:
    pad = "=" * (-len(part) % 4)
    return base64.urlsafe_b64decode(part + pad)


def _decode_jwt(token: str) -> tuple[dict, dict]:
    """Split a JWT into {header, payload}. Raises 401 on malformed input."""
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="Invalid session token")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        payload = json.loads(_b64url_decode(parts[1]))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session token")
    return header, payload


def _verify_rs256(header: dict, payload: dict, token: str, jwk: dict) -> None:
    """Verify RS256 signature against the JWK. Raises 401 on failure."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    n = _b64url_decode(jwk["n"])
    e = _b64url_decode(jwk["e"])
    pub = serialization.load_der_public_key(_public_key_der(n, e))
    parts = token.split(".")
    signing_input = f"{parts[0]}.{parts[1]}".encode()
    signature = _b64url_decode(parts[2])
    try:
        pub.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session token signature")


def _public_key_der(n: bytes, e: bytes) -> bytes:
    """Build a DER SubjectPublicKeyInfo for an RSA public key (n, e)."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    pub = rsa.RSAPublicNumbers(int.from_bytes(e, "big"), int.from_bytes(n, "big")).public_key()
    return pub.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


async def verify_session_jwt(request: Request) -> dict:
    """Verify the Supabase access token and return {user_id, email?}.

    Raises 401 on missing/invalid/expired token. KID-miss triggers a JWKS
    refetch (R16).
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing session token")
    token = auth[7:]

    header, payload = _decode_jwt(token)
    kid = header.get("kid")
    if not kid:
        raise HTTPException(status_code=401, detail="Invalid session token")

    keys = await _jwks.get()
    jwk = keys.get(kid)
    if jwk is None:
        # KID miss → refetch once (R16)
        _jwks._keys = None  # force refetch
        keys = await _jwks.get()
        jwk = keys.get(kid)
    if jwk is None:
        raise HTTPException(status_code=401, detail="Unknown signing key")

    _verify_rs256(header, payload, token, jwk)

    # Issuer + audience + exp validation
    iss = payload.get("iss", "")
    if _SUPABASE_URL.rstrip("/") + "/auth/v1" not in iss:
        raise HTTPException(status_code=401, detail="Invalid session token issuer")
    aud = payload.get("aud")
    if aud is not None and aud != "authenticated":
        raise HTTPException(status_code=401, detail="Invalid session token audience")
    exp = payload.get("exp")
    if exp is not None and time.time() > float(exp):
        raise HTTPException(status_code=401, detail="Session token expired")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid session token")
    return {"user_id": user_id, "email": payload.get("email")}


async def get_current_user(request: Request) -> dict:
    """FastAPI dependency: session-authenticated user (JWT → user_id)."""
    return await verify_session_jwt(request)
