"""D1 tests — session-endpoint auth (JWKS verification) + session endpoints.

Epic: 2026-08-07-tortoise-user-journeys · Issue: #568 (D1)
Plan §5.3 #2/#2b — two-tier auth: session endpoints E1-E8 JWT/JWKS,
data-plane stays tt_. R16: KID-miss refetch + bounded timeout.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import tortoise.session_auth as sa


class TestJWKSVerify:
    def test_decode_jwt_malformed(self):
        from starlette.datastructures import Headers
        req = Request({"type": "http", "method": "GET", "path": "/v1/teams",
                       "headers": Headers({"authorization": "Bearer not-a-jwt"}).raw})
        with pytest.raises(HTTPException) as ei:
            import asyncio
            asyncio.run(sa.verify_session_jwt(req))
        assert ei.value.status_code == 401

    def test_missing_bearer(self):
        from starlette.datastructures import Headers
        req = Request({"type": "http", "method": "GET", "path": "/v1/teams",
                       "headers": Headers({}).raw})
        import asyncio
        with pytest.raises(HTTPException) as ei:
            asyncio.run(sa.verify_session_jwt(req))
        assert ei.value.status_code == 401

    def test_jwk_public_key_der_builds(self):
        # Minimal RSA key roundtrip via the DER builder
        from cryptography.hazmat.primitives.asymmetric import rsa
        priv = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        pub = priv.public_key()
        from cryptography.hazmat.primitives import serialization
        der = pub.public_bytes(serialization.Encoding.DER,
                               serialization.PublicFormat.SubjectPublicKeyInfo)
        n = pub.public_numbers().n
        e = pub.public_numbers().e
        rebuilt = sa._public_key_der(n.to_bytes((n.bit_length() + 7) // 8, "big"),
                                     e.to_bytes(4, "big"))
        assert rebuilt == der
