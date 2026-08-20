"""D1 tests — session-endpoint auth (JWKS verification) + session endpoints.

Epic: 2026-08-07-tortoise-user-journeys · Issue: #1460 (ES256 Supabase JWTs)

The verify core was swapped from a hand-rolled RS256-only verifier to PyJWT
(algorithms=["RS256","ES256"]) with a fail-closed 401/503 boundary and a
hardened `_JWKSCache` (kid-aware single-flight, failure/miss cooldown,
stale-serve, never-evict, first-wins). See
docs/plans/2026-08-18-es256-session-auth.md for the full reviewed matrix.

Plan-review note: exact-tick boundary pins (exp == now-30, iat/nbf == now+30)
are UPSTREAM-SEMANTICS regression pins for pyjwt 2.13.0 — annotate with a
version note on any pyjwt bump.
"""
from __future__ import annotations

import asyncio
import base64
import binascii  # noqa: F401
import json
import os
import time

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import warnings  # noqa: I001

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.requests import Request

import tortoise.session_auth as sa
import jwt as pyjwt
from tests import _session_jwt_utils as u

# ── Test doubles ──────────────────────────────────────────────────────────

FIXED_ISSUER = "https://test-project.supabase.co/auth/v1"


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected status {self.status_code}")


def make_request(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/teams",
            "headers": Headers({"authorization": f"Bearer {token}"}).raw,
        }
    )


def base_payload(**overrides) -> dict:
    now = int(time.time())
    p = {
        "iss": FIXED_ISSUER,
        "sub": "user-123",
        "aud": "authenticated",
        "exp": now + 3600,
        "iat": now,
        "email": "user@example.com",
        "app_metadata": {"providers": ["github"]},
    }
    p.update(overrides)
    return p


class FetchStub:
    """Async _fetch_jwks stub with a fetch counter."""

    def __init__(self, body: bytes | None = None, error: Exception | None = None):
        self.body = body
        self.error = error
        self.count = 0

    async def __call__(self) -> bytes:
        self.count += 1
        if self.error:
            raise self.error
        if self.body is None:
            raise AssertionError("FetchStub: no body configured")
        return self.body


def ec_jwks_bytes(kid: str = "kid-1") -> bytes:
    priv, pub = u.make_ec_keypair()  # noqa: RUF059
    return json.dumps(u.build_ec_jwks(pub, kid)).encode()


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _auth_sandbox(monkeypatch):
    """Isolated session_auth state per test.

    - ONE event loop per test, reused across all operations (`run_until_complete`).
      `asyncio.run()` creates a NEW loop per call — the shared `asyncio.Lock`
      would raise `RuntimeError: bound to a different event loop` on the second
      operation. The loop is set as the event-loop for the test duration.
    - Fresh `_JWKSCache` + fresh `asyncio.Lock` per test (the lock binds to the
      fixture loop on first await; no cross-test leakage).
    - Pinned SUPABASE_URL so issuer derivation is deterministic regardless of
      ambient env (CI/dev hosts export SUPABASE_URL).
    """
    old_cache = sa._jwks
    old_url = sa._SUPABASE_URL
    old_jwks_url = sa._JWKS_URL
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    sa._jwks = sa._JWKSCache()
    sa._jwks._lock = asyncio.Lock()
    sa._SUPABASE_URL = "https://test-project.supabase.co"
    sa._JWKS_URL = FIXED_ISSUER.replace("/auth/v1", "/auth/v1/.well-known/jwks.json")
    yield loop
    loop.close()
    sa._jwks = old_cache
    sa._SUPABASE_URL = old_url
    sa._JWKS_URL = old_jwks_url


def _run(coro):
    """Run a coroutine on the per-test loop (reusable — unlike asyncio.run)."""
    return asyncio.get_event_loop().run_until_complete(coro)


def seed_keys(monkeypatch, jwks: dict) -> FetchStub:
    """Inject `jwks` as the fetched JWKS (stub _fetch_jwks) and force a fetch."""
    stub = FetchStub(body=json.dumps(jwks).encode())
    monkeypatch.setattr(sa, "_fetch_jwks", stub)
    return stub


def warm_cache(monkeypatch, jwks: dict) -> FetchStub:
    """Fetch once so the cache is warm (keys populated, TTL fresh)."""
    stub = seed_keys(monkeypatch, jwks)
    _run(sa._jwks.get())
    return stub


async def _verify_ok(token: str) -> dict:
    return await sa.verify_session_jwt(make_request(token))


def verify_ok(token: str) -> dict:
    return _run(sa.verify_session_jwt(make_request(token)))


# ── Happy paths ───────────────────────────────────────────────────────────


class TestHappyPaths:
    def test_es256_verifies(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        token = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        result = verify_ok(token)
        assert result["user_id"] == "user-123"
        assert result["email"] == "user@example.com"
        assert result["app_metadata"] == {"providers": ["github"]}

    def test_rs256_verifies(self, monkeypatch):
        # RS256 regression pin — the allowlist keeps RS256 for older
        # Supabase projects/selfhost; a distinct code branch needing coverage.
        priv, pub = u.make_rsa_keypair()
        warm_cache(monkeypatch, u.build_rsa_jwks(pub, "kid-1"))
        token = u.mint_rs256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        result = verify_ok(token)
        assert result["user_id"] == "user-123"

    def test_missing_email_returns_none(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        token = u.mint_es256_token(priv, "kid-1", base_payload(email=None), iss=FIXED_ISSUER)
        assert verify_ok(token)["email"] is None


# ── Negative matrix (all → HTTPException 401, never 500) ──────────────────


def _corrupt_signature(token: str) -> str:
    parts = token.split(".")
    sig = parts[2]
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    return f"{parts[0]}.{parts[1]}.{flipped}"


def _require_401(monkeypatch, token: str, jwks: dict | None = None, fetch_error=None):
    """Assert verify → HTTPException with status 401 (never a raw 500)."""
    stub = seed_keys(monkeypatch, jwks or {"keys": []})
    if fetch_error:
        stub.error = fetch_error
    with pytest.raises(HTTPException) as ei:
        verify_ok(token)
    assert ei.value.status_code == 401


class TestNegativeMatrix:
    def test_wrong_signature(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        token = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        _require_401(monkeypatch, _corrupt_signature(token), u.build_ec_jwks(pub, "kid-1"))

    def test_short_x_jwk(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        jwks = u.build_ec_jwks(pub, "kid-1")
        jwks["keys"][0]["x"] = "c2hvcnQ"  # valid b64, wrong length
        token = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        _require_401(monkeypatch, token, jwks)

    def test_oct_kty_jwk(self, monkeypatch):
        jwks = {"keys": [{"kty": "oct", "kid": "kid-1", "alg": "HS256", "k": "AAAA"}]}
        priv, _ = u.make_ec_keypair()
        token = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        _require_401(monkeypatch, token, jwks)

    def test_non_base64url_x(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        jwks = u.build_ec_jwks(pub, "kid-1")
        jwks["keys"][0]["x"] = "!!!not-b64!!!"
        token = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        _require_401(monkeypatch, token, jwks)

    def test_wrong_typed_x(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        jwks = u.build_ec_jwks(pub, "kid-1")
        jwks["keys"][0]["x"] = 123
        token = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        _require_401(monkeypatch, token, jwks)

    def test_bad_ec_point(self, monkeypatch):
        jwks = {
            "keys": [
                {
                    "kty": "EC",
                    "crv": "P-256",
                    "kid": "kid-1",
                    "alg": "ES256",
                    "x": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                    "y": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                }
            ]
        }
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        token = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        _require_401(monkeypatch, token, jwks)

    def test_es256_token_vs_rsa_jwk(self, monkeypatch):
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        _, rsa_pub = u.make_rsa_keypair()
        token = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        _require_401(monkeypatch, token, u.build_rsa_jwks(rsa_pub, "kid-1"))

    def test_rs256_token_vs_ec_jwk(self, monkeypatch):
        _, ec_pub = u.make_ec_keypair()
        rsa_priv, _ = u.make_rsa_keypair()
        token = u.mint_rs256_token(rsa_priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        _require_401(monkeypatch, token, u.build_ec_jwks(ec_pub, "kid-1"))

    def test_alg_none_and_unknown(self, monkeypatch):
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        for alg in ("none", "HS256", "RS512"):
            header = {"alg": alg, "typ": "JWT", "kid": "kid-1"}
            p = base_payload()
            sig = base64.urlsafe_b64encode(b"x").rstrip(b"=").decode()
            token = u.build_token_raw(header, p, sig)
            with pytest.raises(HTTPException) as ei:
                verify_ok(token)
            assert ei.value.status_code == 401

    def test_alg_absent_header(self, monkeypatch):
        # Warm cache so the token actually reaches jwt.decode (the alg-absent
        # → InvalidAlgorithmError path), and pin fetch-count == 1.
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        stub = warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        stub.count = 0
        token = u.build_token_raw(
            {"typ": "JWT", "kid": "kid-1"}, base_payload(), "AAAA"
        )
        with pytest.raises(HTTPException) as ei:
            verify_ok(token)
        assert ei.value.status_code == 401
        # Cache-served (warm): zero fetches — the point is the header path
        # REACHES jwt.decode (alg-absent → InvalidAlgorithmError), not that a
        # fetch happens.
        assert stub.count == 0

    def test_b64_false_header(self, monkeypatch):
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        token = u.build_token_raw(
            {"alg": "ES256", "kid": "kid-1", "b64": False}, base_payload(), "AAAA"
        )
        with pytest.raises(HTTPException) as ei:
            verify_ok(token)
        assert ei.value.status_code == 401

    def test_crit_header(self, monkeypatch):
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        token = u.build_token_raw(
            {"alg": "ES256", "kid": "kid-1", "crit": ["exp"]}, base_payload(), "AAAA"
        )
        with pytest.raises(HTTPException) as ei:
            verify_ok(token)
        assert ei.value.status_code == 401

    def test_expired(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        now = int(time.time())
        for exp, expect_ok in [
            (now - 61, False),
            (now - 20, True),  # leeway
            (now - 30, False),  # exact boundary — inclusive `<=` (pyjwt 2.13 semantics)
        ]:
            token = u.mint_es256_token(priv, "kid-1", base_payload(exp=exp), iss=FIXED_ISSUER)
            if expect_ok:
                assert verify_ok(token)["user_id"] == "user-123"
            else:
                with pytest.raises(HTTPException) as ei:
                    verify_ok(token)
                assert ei.value.status_code == 401

    def test_future_iat(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        now = int(time.time())
        for iat, expect_ok in [
            (now + 3600, False),
            (now + 10, True),  # leeway
            (now + 30, True),  # exact boundary — strict `>` (pyjwt 2.13 semantics)
        ]:
            token = u.mint_es256_token(priv, "kid-1", base_payload(iat=iat), iss=FIXED_ISSUER)
            if expect_ok:
                assert verify_ok(token)["user_id"] == "user-123"
            else:
                with pytest.raises(HTTPException) as ei:
                    verify_ok(token)
                assert ei.value.status_code == 401

    def test_future_nbf(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        now = int(time.time())
        for nbf, expect_ok in [
            (now + 3600, False),
            (now - 10, True),
            (now + 30, True),  # exact boundary — strict `>`
        ]:
            token = u.mint_es256_token(
                priv, "kid-1", base_payload(nbf=nbf), iss=FIXED_ISSUER
            )
            if expect_ok:
                assert verify_ok(token)["user_id"] == "user-123"
            else:
                with pytest.raises(HTTPException) as ei:
                    verify_ok(token)
                assert ei.value.status_code == 401

    def test_missing_exp_iat(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        for payload in (base_payload(exp=None), base_payload(iat=None)):
            token = u.mint_es256_token(priv, "kid-1", payload, iss=FIXED_ISSUER)
            with pytest.raises(HTTPException) as ei:
                verify_ok(token)
            assert ei.value.status_code == 401

    def test_inf_time_claims(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        for claim in ("exp", "iat", "nbf"):
            token = u.mint_es256_token(
                priv, "kid-1", base_payload(**{claim: float("inf")}), iss=FIXED_ISSUER
            )
            with pytest.raises(HTTPException) as ei:
                verify_ok(token)
            assert ei.value.status_code == 401, claim

    def test_issuer_variants(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))

        # Genuinely issuer-less token (empty-string iss would PASS PyJWT's
        # substring membership check — build the raw token without the claim).
        header = {"alg": "ES256", "typ": "JWT", "kid": "kid-1"}
        payload = {k: v for k, v in base_payload().items() if k != "iss"}
        signing_input = f"{u._b64url(json.dumps(header).encode())}.{u._b64url(json.dumps(payload).encode())}"
        sig = u._b64url(u.sign_raw_es256(priv, signing_input.encode()))
        no_iss_token = f"{signing_input}.{sig}"

        cases = [
            ("https://other.supabase.co/auth/v1", False),  # wrong issuer
            (no_iss_token, False),  # missing issuer
            (FIXED_ISSUER + "/", False),  # trailing slash
            (FIXED_ISSUER, True),
        ]
        for iss, expect_ok in cases:
            if iss is no_iss_token:
                token = no_iss_token
            else:
                token = u.mint_es256_token(priv, "kid-1", base_payload(), iss=iss)
            if expect_ok:
                assert verify_ok(token)["user_id"] == "user-123"
            else:
                with pytest.raises(HTTPException) as ei:
                    verify_ok(token)
                assert ei.value.status_code == 401

    def test_audience_variants(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        cases = [
            ("other-aud", False),
            (None, False),  # missing aud (require)
            (["authenticated", "evil"], False),  # list-form aud → strict_aud
            ("authenticated", True),
        ]
        for aud, expect_ok in cases:
            token = u.mint_es256_token(priv, "kid-1", base_payload(aud=aud), iss=FIXED_ISSUER)
            if expect_ok:
                assert verify_ok(token)["user_id"] == "user-123"
            else:
                with pytest.raises(HTTPException) as ei:
                    verify_ok(token)
                assert ei.value.status_code == 401

    def test_sub_variants(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        for sub, expect_ok in [
            (None, False),
            ("", False),
            ("   ", False),  # whitespace sub — pyjwt accepts, guard must reject
            (0, False),
            (123, False),
            (["abc"], False),
            ("user-123", True),
        ]:
            token = u.mint_es256_token(priv, "kid-1", base_payload(sub=sub), iss=FIXED_ISSUER)
            if expect_ok:
                assert verify_ok(token)["user_id"] == "user-123"
            else:
                with pytest.raises(HTTPException) as ei:
                    verify_ok(token)
                assert ei.value.status_code == 401

    def test_wrong_typed_claims(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        for claim, val in [
            ("app_metadata", "x"), ("email", 123),
            ("exp", [9999999999]), ("exp", {"a": 1}), ("iat", [9999999999]),
        ]:
            token = u.mint_es256_token(priv, "kid-1", base_payload(**{claim: val}), iss=FIXED_ISSUER)
            with pytest.raises(HTTPException) as ei:
                verify_ok(token)
            assert ei.value.status_code == 401, claim

    def test_oversized_token_boundary(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        # Repo guard (_MAX_TOKEN_BYTES=16000) is BELOW the server's ~16KB
        # header-line cap so it — not a raw server 400/431 — is the first
        # line of rejection (keeping the failure inside the CORS-stamped
        # HTTPException path). Boundary: ≤16,000 → 200 (15,999 bytes),
        # >16,000 → 401 (16,001 bytes) — 16,000 itself is unrepresentable
        # due to base64 quantization.
        def token_with(n_pad):
            return u.mint_es256_token(
                priv, "kid-1", base_payload(padding="x" * n_pad), iss=FIXED_ISSUER
            )

        # Base64 inflates ~4/3x, so find the padding that lands the token at
        # the exact 16,000-byte boundary via binary search (monotonic in n).
        lo, hi = 0, 20000
        while lo < hi:
            mid = (lo + hi) // 2
            if len(token_with(mid).encode()) < 16000:
                lo = mid + 1
            else:
                hi = mid
        token_at = token_with(lo - 1)
        token_over = token_with(lo)
        assert len(token_at.encode()) <= 16000 < len(token_over.encode())
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            assert verify_ok(token_at)["user_id"] == "user-123"
            assert not [x for x in w if issubclass(x.category, DeprecationWarning)], "decode emitted DeprecationWarning (unsupported kwargs)"
        with pytest.raises(HTTPException) as ei:
            verify_ok(token_over)
        assert ei.value.status_code == 401

    def test_malformed_token(self, monkeypatch):
        _require_401(monkeypatch, "not-a-jwt", {"keys": []})

    def test_non_dict_segments(self, monkeypatch):
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        # header segments decoding to [1,2], 123, "x" — valid JSON, non-dict
        for encoded in ("WzEsMl0", "MTIz", "Ingi"):
            token = f"{encoded}.eyJzdWIiOiJ4In0.AAAA"
            with pytest.raises(HTTPException) as ei:
                verify_ok(token)
            assert ei.value.status_code == 401

    def test_no_kid_zero_fetch(self, monkeypatch):
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        stub = FetchStub(body=ec_jwks_bytes())
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        token = u.build_token_raw({"alg": "ES256", "typ": "JWT"}, base_payload(), "AAAA")
        with pytest.raises(HTTPException) as ei:
            verify_ok(token)
        assert ei.value.status_code == 401
        assert stub.count == 0

    def test_whitespace_and_nontstring_kid_zero_fetch(self, monkeypatch):
        stub = FetchStub(body=ec_jwks_bytes())
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        for kid in ("   ", 123, True):
            token = u.build_token_raw(
                {"alg": "ES256", "typ": "JWT", "kid": kid}, base_payload(), "AAAA"
            )
            with pytest.raises(HTTPException) as ei:
                verify_ok(token)
            assert ei.value.status_code == 401, repr(kid)
        assert stub.count == 0

    def test_malformed_rsa_jwk_incident_class(self, monkeypatch):
        # The #1460 incident class: KeyError('n') on a malformed RSA JWK must
        # 401, never 500.
        jwks = {"keys": [{"kty": "RSA", "kid": "kid-1", "alg": "RS256"}]}  # missing n/e
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        token = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        _require_401(monkeypatch, token, jwks)

        jwks2 = {"keys": [{"kty": "RSA", "kid": "kid-1", "alg": "RS256", "n": 123, "e": 65537}]}
        _require_401(monkeypatch, token, jwks2)

    def test_okp_eddsa_family(self, monkeypatch):
        # Ed25519 OKP JWK: PyJWK binds "EdDSA" → any ES256/RS256 token 401.
        okp_jwks = {
            "keys": [
                {
                    "kty": "OKP",
                    "crv": "Ed25519",
                    "kid": "kid-1",
                    "alg": "EdDSA",
                    "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo",
                }
            ]
        }
        priv, pub = u.make_ec_keypair()
        token = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        _require_401(monkeypatch, token, okp_jwks)

        # OKP JWK missing x → 401
        okp_missing = {"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": "kid-1", "alg": "EdDSA"}]}
        _require_401(monkeypatch, token, okp_missing)

        # EC + sibling OKP → EC token for the EC kid still verifies (no poisoning)
        ec_jwks = u.build_ec_jwks(pub, "kid-ec")
        ec_jwks["keys"].append(
            {"kty": "OKP", "crv": "Ed25519", "kid": "kid-okp", "alg": "EdDSA",
             "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"}
        )
        warm_cache(monkeypatch, ec_jwks)
        tok = u.mint_es256_token(priv, "kid-ec", base_payload(), iss=FIXED_ISSUER)
        assert verify_ok(tok)["user_id"] == "user-123"

    def test_future_curve_family(self, monkeypatch):
        # P-384 sibling: helper-built entry carries alg=ES256 → binds ES256 →
        # 401 arrives via signature-verify failure against the P-384 key.
        from cryptography.hazmat.primitives.asymmetric import ec as _ec

        p384 = _ec.generate_private_key(_ec.SECP384R1())
        nums = p384.public_key().public_numbers()
        p384_jwks = {
            "keys": [
                {
                    "kty": "EC", "crv": "P-384", "kid": "kid-384", "alg": "ES256",
                    "x": u._b64url(nums.x.to_bytes(48, "big")),
                    "y": u._b64url(nums.y.to_bytes(48, "big")),
                }
            ]
        }
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        token = u.mint_es256_token(priv, "kid-384", base_payload(), iss=FIXED_ISSUER)
        _require_401(monkeypatch, token, p384_jwks)

        # Unknown kty
        unknown = {"keys": [{"kty": "X25519", "kid": "kid-x", "alg": "EdDSA", "x": "AAAA"}]}
        _require_401(monkeypatch, token, unknown)

    def test_deeply_nested_payload(self, monkeypatch):
        # Hand-encode a ~1,200-deep nested payload (~2-3KB, under the 16KB
        # guard) and WARM the cache so the token actually reaches jwt.decode.
        # The malformed signature (AAAA) → InvalidSignatureError pins the
        # decode-boundary fail-closed path (401, never 500). The RecursionError
        # arm of the catch tuple is unreachable-defensive on CPython (C-json
        # trips it only at ~10k nesting, >16KB guard) — not pinned here.
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        nested = "x"
        for _ in range(1200):
            nested = [nested]
        payload_b64 = u._b64url(json.dumps({"x": nested}).encode())
        header_b64 = u._b64url(json.dumps({"alg": "ES256", "kid": "kid-1"}).encode())
        token = f"{header_b64}.{payload_b64}.AAAA"
        with pytest.raises(HTTPException) as ei:
            verify_ok(token)
        assert ei.value.status_code == 401


# ── Cache hardening ───────────────────────────────────────────────────────


class TestCacheHardening:
    def test_fetch_failure_warm_serves_stale(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        stub = warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        sa._jwks._fetched_at = time.monotonic() - sa._JWKS_TTL - 1  # force TTL expiry (monotonic-relative)
        stub.error = OSError("jwks down")
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        # stale key set serves the token
        assert verify_ok(tok)["user_id"] == "user-123"
        assert stub.count == 2  # initial warm + one failed refresh

    def test_fetch_failure_cold_returns_503(self, monkeypatch):
        stub = FetchStub(error=OSError("jwks down"))
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        assert ei.value.status_code == 503

    def test_cooldown_skipped_no_last_good_503(self, monkeypatch):
        # After a failure the cooldown is armed; a cooldown-skipped fetch with
        # no last-good keys must 503, never None-crash.
        stub = FetchStub(error=OSError("down"))
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        monkeypatch.setattr(sa, "_COOLDOWN_S", 30.0)
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)  # first: fetch fails → 503, cooldown armed
        assert ei.value.status_code == 503
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)  # second: cooldown-skipped, no keys → 503
        assert ei.value.status_code == 503
        assert stub.count == 1

    def test_first_fetch_200_empty_401(self, monkeypatch):
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        _require_401(monkeypatch, tok, {"keys": []})

    def test_kid_miss_failing_refetch_preserves_keys(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        stub = warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        tok = u.mint_es256_token(priv, "kid-unknown", base_payload(), iss=FIXED_ISSUER)
        stub.error = OSError("down")  # refetch fails
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        assert ei.value.status_code == 401
        # last-good keys preserved (no eviction)
        assert "kid-1" in sa._jwks._keys
        # a valid token still verifies from stale keys
        tok1 = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        assert verify_ok(tok1)["user_id"] == "user-123"

    def test_positive_r16_rotation(self, monkeypatch):
        _, pub1 = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub1, "kid-old"))
        priv2, pub2 = u.make_ec_keypair()
        new_jwks = u.build_ec_jwks(pub2, "kid-new")
        new_jwks["keys"].append(u.build_ec_jwks(pub1, "kid-old")["keys"][0])
        stub = FetchStub(body=json.dumps(new_jwks).encode())
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        tok = u.mint_es256_token(priv2, "kid-new", base_payload(), iss=FIXED_ISSUER)
        assert verify_ok(tok)["user_id"] == "user-123"
        assert "kid-new" in sa._jwks._keys  # cache updated
        assert stub.count == 1

    def test_removed_kid_after_refetch_401(self, monkeypatch):
        _, pub1 = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub1, "kid-1"))
        sa._jwks._fetched_at = time.monotonic() - sa._JWKS_TTL - 1  # force TTL expiry (monotonic-relative)
        _, pub2 = u.make_ec_keypair()
        stub = FetchStub(body=json.dumps(u.build_ec_jwks(pub2, "kid-new")).encode())
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        priv, _ = u.make_ec_keypair()
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        assert ei.value.status_code == 401

    def test_rotation_under_outage_tradeoff(self, monkeypatch):
        # Bounded revocation window: warm K1, upstream removes K1 + fetch fails
        # → K1 still verifies (documented availability-vs-security tradeoff).
        priv, pub1 = u.make_ec_keypair()
        stub = warm_cache(monkeypatch, u.build_ec_jwks(pub1, "kid-1"))
        sa._jwks._fetched_at = time.monotonic() - sa._JWKS_TTL - 1  # force TTL expiry (monotonic-relative)
        stub.error = OSError("outage")
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        assert verify_ok(tok)["user_id"] == "user-123"
        # after recovery (upstream returns a key set WITHOUT kid-1) → 401
        _, pub2 = u.make_ec_keypair()
        stub.body = json.dumps(u.build_ec_jwks(pub2, "kid-new")).encode()
        stub.error = None
        monkeypatch.setattr(sa, "_COOLDOWN_S", 0.0)  # force refetch now
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        assert ei.value.status_code == 401

    def test_200_empty_warm_serves_stale(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        stub = warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        sa._jwks._fetched_at = time.monotonic() - sa._JWKS_TTL - 1  # force TTL expiry (monotonic-relative)
        stub.body = json.dumps({"keys": []}).encode()  # upstream returns empty
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        assert verify_ok(tok)["user_id"] == "user-123"  # stale-on-empty

    def test_duplicate_kid_first_wins(self, monkeypatch):
        priv1, pub1 = u.make_ec_keypair()
        priv2, pub2 = u.make_ec_keypair()
        jwks = u.build_ec_jwks(pub1, "kid-1")
        jwks["keys"].append(u.build_ec_jwks(pub2, "kid-1")["keys"][0])  # dup kid
        warm_cache(monkeypatch, jwks)
        # first key wins → token signed by priv1 verifies, priv2 does not
        tok1 = u.mint_es256_token(priv1, "kid-1", base_payload(), iss=FIXED_ISSUER)
        assert verify_ok(tok1)["user_id"] == "user-123"
        tok2 = u.mint_es256_token(priv2, "kid-1", base_payload(), iss=FIXED_ISSUER)
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok2)
        assert ei.value.status_code == 401

    def test_kid_less_keys_dropped(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        jwks = u.build_ec_jwks(pub, "kid-1")
        jwks["keys"].append({"kty": "EC", "crv": "P-256"})  # no kid
        warm_cache(monkeypatch, jwks)
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        assert verify_ok(tok)["user_id"] == "user-123"  # kid-less entry dropped

    def test_nontstring_kid_value_zero_usable(self, monkeypatch):
        # {"kid": 123} entry: string-kid filter drops it → zero usable →
        # failure cooldown recorded → sequential forged-kid requests bounded.
        jwks = {
            "keys": [
                {
                    "kty": "EC", "crv": "P-256", "kid": 123, "alg": "ES256",
                    "x": "AAAA", "y": "BBBB",
                }
            ]
        }
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        stub = warm_cache(monkeypatch, jwks)
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        fetch_count_before = stub.count
        for _ in range(5):
            with pytest.raises(HTTPException) as ei:
                verify_ok(tok)
            assert ei.value.status_code == 401
        # cooldown armed on the zero-usable fetch → no per-request refetch
        assert stub.count <= fetch_count_before + 1

    def test_malformed_key_entry_fails_closed(self, monkeypatch):
        jwks = {"keys": ["not-a-dict"]}
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        stub = seed_keys(monkeypatch, jwks)
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        assert ei.value.status_code in (401, 503)  # fetch failure semantics
        assert stub.count >= 1

    def test_oversized_jwks_body(self, monkeypatch):
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        stub = FetchStub(body=b"x" * 70000)  # > 64KB
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        assert ei.value.status_code == 503  # cold, no last-good

    def test_garbage_200_bodies(self, monkeypatch):
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        for body in (b"<html>error</html>", b"[]", b'{"keys": null}'):
            stub = FetchStub(body=body)
            monkeypatch.setattr(sa, "_fetch_jwks", stub)
            with pytest.raises(HTTPException) as ei:
                verify_ok(tok)
            assert ei.value.status_code == 503, body

    def test_post_200_empty_recovery(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        stub = FetchStub(body=json.dumps({"keys": []}).encode())
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        assert ei.value.status_code == 401
        count_after_first = stub.count
        # immediate second request: zero additional fetches (cooldown armed)
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        assert ei.value.status_code == 401
        assert stub.count == count_after_first
        # recovery: after cooldown with healthy upstream → verifies
        monkeypatch.setattr(sa, "_COOLDOWN_S", 0.0)
        stub.body = json.dumps(u.build_ec_jwks(pub, "kid-1")).encode()
        assert verify_ok(tok)["user_id"] == "user-123"

    def test_pristine_cold_start_single_fetch(self, monkeypatch):
        # Unset _last_failure_at sentinel: an unarmed cooldown must never
        # block a legitimate first fetch.
        priv, pub = u.make_ec_keypair()
        stub = FetchStub(body=json.dumps(u.build_ec_jwks(pub, "kid-1")).encode())
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        assert sa._jwks._last_failure_at is None
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        assert verify_ok(tok)["user_id"] == "user-123"
        assert stub.count == 1

    def test_success_does_not_rearm_cooldown(self, monkeypatch):
        _, pub1 = u.make_ec_keypair()
        priv2, pub2 = u.make_ec_keypair()
        priv3, pub3 = u.make_ec_keypair()
        jwks = u.build_ec_jwks(pub1, "kid-1")
        warm_cache(monkeypatch, jwks)
        # K2 appears: force-refetch succeeds → K2 verifies
        jwks2 = u.build_ec_jwks(pub2, "kid-2")
        jwks2["keys"].append(jwks["keys"][0])
        stub = FetchStub(body=json.dumps(jwks2).encode())
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        tok2 = u.mint_es256_token(priv2, "kid-2", base_payload(), iss=FIXED_ISSUER)
        assert verify_ok(tok2)["user_id"] == "user-123"
        assert stub.count == 1
        # K3 immediately after: a NEW fetch must occur (success didn't re-arm)
        jwks3 = u.build_ec_jwks(pub3, "kid-3")
        jwks3["keys"].extend(jwks2["keys"])
        stub.body = json.dumps(jwks3).encode()
        tok3 = u.mint_es256_token(priv3, "kid-3", base_payload(), iss=FIXED_ISSUER)
        assert verify_ok(tok3)["user_id"] == "user-123"
        assert stub.count == 2

    def test_unsupported_algorithm_fail_closed(self, monkeypatch):
        from cryptography.exceptions import UnsupportedAlgorithm

        priv, pub = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))

        def boom(*args, **kwargs):
            raise UnsupportedAlgorithm("FIPS backend")

        monkeypatch.setattr(pyjwt.PyJWK, "from_dict", boom)
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        assert ei.value.status_code == 401

    def test_state_restoration_no_loop_error(self, monkeypatch):
        # Two cache-touching tests back-to-back (simulated here) must not hit
        # "bound to a different event loop" — the autouse fixture replaces the
        # lock per test.
        priv, pub = u.make_ec_keypair()
        stub = warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        stub.error = OSError("down")
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        # warm cache → stale-serve
        assert verify_ok(tok)["user_id"] == "user-123"
        # cold start in the SAME test (fresh lock usage)
        monkeypatch.setattr(sa, "_jwks", sa._JWKSCache())
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        assert ei.value.status_code == 503


# ── Concurrency ───────────────────────────────────────────────────────────


class TestConcurrency:
    def _burst(self, n, token):
        async def go():
            return await asyncio.gather(
                *[sa.verify_session_jwt(make_request(token)) for _ in range(n)],
                return_exceptions=True,
            )

        return _run(go())

    def _results(self, outcomes):
        codes = []
        for o in outcomes:
            if isinstance(o, HTTPException):
                codes.append(o.status_code)
            elif isinstance(o, dict):
                codes.append(200)
            else:
                codes.append(type(o).__name__)
        return codes

    def test_failure_non_force_single_fetch(self, monkeypatch):
        priv, pub = u.make_ec_keypair()
        stub = warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        stub.count = 0  # exclude the warm fetch from the count
        monkeypatch.setattr(sa, "_COOLDOWN_S", 30.0)
        # force TTL expiry so the non-force path refetches
        sa._jwks._fetched_at = time.monotonic() - sa._JWKS_TTL - 1
        stub.error = OSError("down")
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        outcomes = self._burst(20, tok)
        assert all(c == 200 for c in self._results(outcomes))  # stale-served
        assert stub.count == 1

    def test_failure_force_single_fetch(self, monkeypatch):
        _, pub1 = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub1, "kid-1"))
        stub = FetchStub(error=OSError("down"))
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        priv, _ = u.make_ec_keypair()
        tok = u.mint_es256_token(priv, "kid-2", base_payload(), iss=FIXED_ISSUER)
        outcomes = self._burst(20, tok)
        assert all(c == 401 for c in self._results(outcomes))
        assert stub.count == 1

    def test_success_force_single_fetch(self, monkeypatch):
        _, pub1 = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub1, "kid-1"))
        priv2, pub2 = u.make_ec_keypair()
        jwks = u.build_ec_jwks(pub2, "kid-2")
        jwks["keys"].append(u.build_ec_jwks(pub1, "kid-1")["keys"][0])
        stub = FetchStub(body=json.dumps(jwks).encode())
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        tok = u.mint_es256_token(priv2, "kid-2", base_payload(), iss=FIXED_ISSUER)
        outcomes = self._burst(20, tok)
        assert all(c == 200 for c in self._results(outcomes))
        assert stub.count == 1  # kid-aware single-flight

    def test_unknown_kid_miss_healthy_upstream(self, monkeypatch):
        # Forged-kid flood against a healthy-but-kid-absent upstream: bounded
        # to one fetch (miss arms cooldown).
        _, pub1 = u.make_ec_keypair()
        warm_cache(monkeypatch, u.build_ec_jwks(pub1, "kid-1"))
        stub = FetchStub(body=json.dumps(u.build_ec_jwks(pub1, "kid-1")).encode())
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        priv, _ = u.make_ec_keypair()
        tok = u.mint_es256_token(priv, "kid-2", base_payload(), iss=FIXED_ISSUER)
        outcomes = self._burst(20, tok)
        assert all(c == 401 for c in self._results(outcomes))
        assert stub.count == 1

    def test_success_non_force_ttl_single_fetch(self, monkeypatch):
        priv, pub1 = u.make_ec_keypair()
        stub = warm_cache(monkeypatch, u.build_ec_jwks(pub1, "kid-1"))
        stub.count = 0  # exclude the warm fetch from the count
        sa._jwks._fetched_at = time.monotonic() - sa._JWKS_TTL - 1  # TTL expired (monotonic-relative)
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        outcomes = self._burst(20, tok)
        assert all(c == 200 for c in self._results(outcomes))
        assert stub.count == 1  # double-checked TTL coalescing

    def test_cold_start_concurrent_all_503(self, monkeypatch):
        # 20 concurrent requests with a cold cache + failing fetch: every
        # outcome is HTTPException 503 (never a raw 500 / None-crash), and the
        # lock + cooldown coalesce to exactly ONE fetch attempt.
        stub = FetchStub(error=OSError("jwks down"))
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        priv, _ = u.make_ec_keypair()
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        outcomes = self._burst(20, tok)
        assert all(c == 503 for c in self._results(outcomes))
        assert stub.count == 1

    def test_ttl_refresh_plus_miss_double_fetch(self, monkeypatch):
        priv, pub1 = u.make_ec_keypair()
        stub = warm_cache(monkeypatch, u.build_ec_jwks(pub1, "kid-1"))
        stub.count = 0  # exclude the warm fetch from the count
        sa._jwks._fetched_at = time.monotonic() - sa._JWKS_TTL - 1  # TTL expired (monotonic-relative)
        tok = u.mint_es256_token(priv, "kid-2", base_payload(), iss=FIXED_ISSUER)
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        assert ei.value.status_code == 401
        assert stub.count == 2  # TTL refresh + miss refetch
        # second unknown-kid token: cooldown armed → 0 additional fetches
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        assert ei.value.status_code == 401
        assert stub.count == 2

    def test_mixed_burst_bounded(self, monkeypatch):
        priv1, pub1 = u.make_ec_keypair()
        stub = warm_cache(monkeypatch, u.build_ec_jwks(pub1, "kid-1"))
        stub.count = 0  # exclude the warm fetch from the count
        sa._jwks._fetched_at = time.monotonic() - sa._JWKS_TTL - 1  # TTL expired (monotonic-relative)
        priv2, _ = u.make_ec_keypair()
        tok_valid = u.mint_es256_token(priv1, "kid-1", base_payload(), iss=FIXED_ISSUER)
        tok_forged = u.mint_es256_token(priv2, "kid-2", base_payload(), iss=FIXED_ISSUER)
        tokens = [tok_valid] * 10 + [tok_forged] * 10

        async def go():
            return await asyncio.gather(
                *[sa.verify_session_jwt(make_request(t)) for t in tokens],
                return_exceptions=True,
            )

        outcomes = _run(go())
        codes = []
        for o in outcomes:
            if isinstance(o, dict):
                codes.append(200)
            elif isinstance(o, HTTPException):
                codes.append(o.status_code)
        assert codes.count(200) == 10
        assert codes.count(401) == 10
        assert stub.count <= 2  # one TTL-refresh + one miss-refetch, then cooldown
