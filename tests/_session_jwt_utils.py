"""Shared JWT-mint helpers for session-auth tests (issue #1460).

Consumed by `tests/test_session_auth.py` (unit) and the hosted e2e harness
(`tests/e2e/hosted/conftest.py`, `tests/e2e/hosted/test_13_claim.py`).

Import contract: `tests/`, `tests/e2e/`, `tests/e2e/hosted/` have no
`__init__.py` — the unit side resolves via pytest basedir insertion; the
hosted e2e modules resolve via namespace-package (`from tests._session_jwt_utils
import ...`), relying on `tests/conftest.py`'s `sys.path.insert` having run.
Do not add `__init__.py`.
"""

from __future__ import annotations

import base64
from typing import Any

import jwt as pyjwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_ec_keypair() -> tuple:
    """(private_key, public_key) for ES256 (P-256)."""
    priv = ec.generate_private_key(ec.SECP256R1())
    return priv, priv.public_key()


def make_rsa_keypair() -> tuple:
    """(private_key, public_key) for RS256 (2048-bit)."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


def build_ec_jwks(public_key, kid: str) -> dict:
    """JWKS dict for an EC P-256 public key (`kty: EC, crv: P-256, alg: ES256`)."""
    nums = public_key.public_numbers()
    return {
        "keys": [
            {
                "kty": "EC",
                "crv": "P-256",
                "kid": kid,
                "alg": "ES256",
                "x": _b64url(nums.x.to_bytes(32, "big")),
                "y": _b64url(nums.y.to_bytes(32, "big")),
            }
        ]
    }


def build_rsa_jwks(public_key, kid: str) -> dict:
    """JWKS dict for an RSA public key (`kty: RSA, alg: RS256`)."""
    nums = public_key.public_numbers()
    n_len = (nums.n.bit_length() + 7) // 8
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": kid,
                "alg": "RS256",
                "n": _b64url(nums.n.to_bytes(n_len, "big")),
                "e": _b64url(nums.e.to_bytes(4, "big")),
            }
        ]
    }


def _default_iss() -> str:
    """Match the module's issuer derivation (never drift from session_auth)."""
    from tortoise import session_auth as sa

    return sa._SUPABASE_URL.rstrip("/") + "/auth/v1"


def mint_es256_token(private_key, kid: str, payload: dict, iss: str | None = None) -> str:
    """Mint an ES256 token. ⛔ Use PyJWT's encode (DER→raw internal) — do NOT
    sign with `cryptography` `ec.ECDSA()` directly (returns 72-byte DER, which
    PyJWT rejects).

    `iss` is an explicit parameter; when omitted it defaults to the module's
    SUPABASE_URL-derived issuer (unit-test call site — never drifts), and a
    payload-provided iss is left untouched. When PROVIDED, iss overrides any
    payload value. The e2e harness MUST pass the mock JWKS URL as `iss`
    (exact-issuer verification).
    """
    p = dict(payload)
    if iss is None:
        p.setdefault("iss", _default_iss())
    else:
        p["iss"] = iss
    return pyjwt.encode(p, private_key, algorithm="ES256", headers={"kid": kid})


def mint_rs256_token(private_key, kid: str, payload: dict, iss: str | None = None) -> str:
    """Mint an RS256 token (same `iss` contract as mint_es256_token)."""
    p = dict(payload)
    if iss is None:
        p.setdefault("iss", _default_iss())
    else:
        p["iss"] = iss
    return pyjwt.encode(p, private_key, algorithm="RS256", headers={"kid": kid})


def sign_raw_es256(private_key, signing_input: bytes) -> bytes:
    """Sign with `cryptography` and return the RAW r‖s 64-byte ES256 signature
    (for negative tests that need a raw-sig token PyJWT would reject)."""
    sig = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    r, s = decode_dss_signature(sig)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def build_token_raw(header: dict, payload: Any, signature_b64: str) -> str:
    """Assemble a JWT from raw parts (for malformed/edge-case tests)."""
    import json as _json

    def _enc(obj) -> str:
        return _b64url(_json.dumps(obj).encode("utf-8"))

    return f"{_enc(header)}.{_enc(payload)}.{signature_b64}"
