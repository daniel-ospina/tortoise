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
            "path": "/v1/organizations",
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


# ── Cold start: the fetch budget must BOUND the request (#3284) ────────────


class SlowFetchStub(FetchStub):
    """Fetch stub that outlives any budget the caller configured."""

    def __init__(self, delay: float, body: bytes | None = None,
                 error: Exception | None = None):
        super().__init__(body=body, error=error)
        self.delay = delay

    async def __call__(self) -> bytes:
        self.count += 1
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        if self.body is None:
            raise AssertionError("SlowFetchStub: no body configured")
        return self.body


class TestColdStartBound:
    """#3284: the first request on a cold process must be BOUNDED.

    ``httpx``'s ``timeout=`` is PER-PHASE (connect/read/write/pool), so it does
    not bound a fetch: pre-fix, one fetch could burn connect(5) + read(5) ≈ 10s
    and the request path pays up to TWO (TTL refresh + kid-miss refetch, R16) —
    the 15–35s first request in #3144/#3284. The hard deadline is therefore
    applied OUTSIDE the ``_fetch_jwks`` seam (``_fetch_jwks_bounded``), which is
    what these tests pin: the fake below IGNORES any transport timeout, so only
    a bound outside the seam can stop it.
    """

    BUDGET = 0.25  # small enough to keep the suite fast, large enough to be real

    def test_slow_cold_fetch_is_hard_bounded_and_retryable(self, monkeypatch):
        # raising=False: pre-fix the knob does not exist, so this test must
        # fail on the BEHAVIOUR (the fetch runs to completion), not on a
        # missing attribute (#3284 regression evidence).
        monkeypatch.setattr(sa, "_JWKS_FETCH_TOTAL_S", self.BUDGET, raising=False)
        stub = SlowFetchStub(1.0, error=OSError("jwks down"))
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        priv, _ = u.make_ec_keypair()
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)

        t0 = time.monotonic()
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        elapsed = time.monotonic() - t0

        assert ei.value.status_code == 503
        # The bound, not the fake's duration (pre-fix: elapsed ≈ 1.0s).
        assert elapsed < self.BUDGET + 0.25, (
            f"cold request took {elapsed:.3f}s — the fetch was not bounded")
        # A bounded 503 must still be ACTIONABLE (#3284): Retry-After tells the
        # client when a retry can succeed instead of letting it hammer a
        # cooldown that is already answering 503.
        assert ei.value.headers, "503 carries no headers"
        assert int(ei.value.headers["Retry-After"]) >= 1
        assert ei.value.detail  # JSON body, never a zero-byte response

    def test_slow_double_fetch_chain_stays_within_the_documented_bound(
            self, monkeypatch):
        """The documented 2 × per-fetch worst case must actually be ENFORCED.

        The bound key resolution may rely on is
        ``_JWKS_RESOLVE_WORST_CASE_S == 2 × fetch`` (a TTL refresh + a kid-miss
        refetch, R16 — the R16 double fetch itself is pinned by
        ``TestConcurrency::test_ttl_refresh_plus_miss_double_fetch``). This
        test makes the stub outlive the bound by 4×, so a removed/widened bound
        makes the elapsed time blow past ``2 × fetch`` and fails here. The
        previous version slept ``BUDGET / 2``, so it passed with the bound
        removed and pinned nothing.

        Two independent COLD attempts are used rather than one request: a
        request whose first fetch times out arms the cooldown and (correctly)
        never reaches a second fetch, so it cannot exhibit the chain. Each cold
        attempt must still make exactly one bounded fetch, and the pair must
        stay inside the documented worst case.
        """
        # raising=False: pre-fix the knob does not exist, so this test must
        # fail on the BEHAVIOUR, not on a missing attribute (#3284 evidence).
        monkeypatch.setattr(sa, "_JWKS_FETCH_TOTAL_S", self.BUDGET, raising=False)
        # Scale the documented bound with the shortened budget (the identity
        # itself is pinned by test_documented_chain_bound_tracks_the_fetch_bound).
        monkeypatch.setattr(sa, "_JWKS_RESOLVE_WORST_CASE_S", 2 * self.BUDGET)
        stub = SlowFetchStub(4 * self.BUDGET, error=OSError("jwks down"))
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        priv, _ = u.make_ec_keypair()
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)

        t0 = time.monotonic()
        for _ in range(2):
            sa._jwks = sa._JWKSCache()
            sa._jwks._lock = asyncio.Lock()
            with pytest.raises(HTTPException) as ei:
                verify_ok(tok)
            assert ei.value.status_code == 503
        elapsed = time.monotonic() - t0

        assert stub.count == 2, "each cold attempt must make one bounded fetch"
        assert elapsed <= sa._JWKS_RESOLVE_WORST_CASE_S + 0.25, (
            f"two bounded fetches took {elapsed:.3f}s — the documented "
            f"{sa._JWKS_RESOLVE_WORST_CASE_S:.2f}s worst case is not enforced")

    def test_slow_refresh_keeps_the_bound_and_still_serves_stale(self, monkeypatch):
        """TTL expiry + a SLOW upstream + a valid token: bounded, and still 200.

        This is the production shape of the symptom: the key set is past its
        TTL, the upstream is slow, and a real user is waiting. The request must
        stay inside the budget (pre-fix it waited the fetch out — ~1.0s here,
        with no bound at all) and must serve from the last-good key set instead
        of failing. A bound that costs availability is not a fix.
        """
        priv, pub = u.make_ec_keypair()
        warm = warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        sa._jwks._fetched_at = time.monotonic() - sa._JWKS_TTL - 1  # TTL expired
        slow = SlowFetchStub(
            1.0, body=json.dumps(u.build_ec_jwks(pub, "kid-1")).encode())
        monkeypatch.setattr(sa, "_fetch_jwks", slow)
        monkeypatch.setattr(sa, "_JWKS_FETCH_TOTAL_S", self.BUDGET, raising=False)
        assert warm.count == 1  # the warm fetch
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)

        t0 = time.monotonic()
        assert verify_ok(tok)["user_id"] == "user-123"
        elapsed = time.monotonic() - t0

        assert elapsed < self.BUDGET + 0.25, (
            f"slow refresh took {elapsed:.3f}s — not bounded")
        assert slow.count == 1, "the bounded attempt was retried on the request path"
        assert "kid-1" in sa._jwks._keys  # last-good keys survived the timeout

    def test_documented_chain_bound_tracks_the_fetch_bound(self):
        """The documented worst case must be the arithmetic it claims.

        A drift here silently re-opens #3284: the bound the request path is
        allowed to rely on is ``2 × fetch`` (two fetches, R16), and the phases
        must sum BELOW the hard total so a phase timeout normally fires first
        (a hard deadline that wins that race strands the httpx worker —
        CPython #87185 cannot cancel it).
        """
        assert sa._JWKS_RESOLVE_WORST_CASE_S == 2 * sa._JWKS_FETCH_TOTAL_S
        assert sa._JWKS_FETCH_PHASE_TOTAL_S < sa._JWKS_FETCH_TOTAL_S
        assert sa._JWKS_FETCH_TOTAL_S >= (
            sa._JWKS_FETCH_PHASE_TOTAL_S + sa._JWKS_FETCH_MARGIN_S)

    def test_prefetch_pays_the_first_fetch_not_the_first_request(self, monkeypatch):
        """#3284 Move A: after the warm-up, the first request pays ZERO fetches."""
        priv, pub = u.make_ec_keypair()
        stub = seed_keys(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        report = _run(sa.prefetch_jwks())
        assert report["ok"] is True and report["keys"] == 1, report
        assert report["error"] is None
        assert stub.count == 1  # the warm-up paid it
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        assert verify_ok(tok)["user_id"] == "user-123"
        assert stub.count == 1, "the request path paid a fetch after a warm cache"

    def test_prefetch_failure_is_reported_and_does_not_arm_the_cooldown(
            self, monkeypatch):
        """#3284 regression (review P1): a boot warm-up must not deny the
        first request.

        The warm-up runs milliseconds after boot, exactly when DNS/egress are
        least ready. If its single failed attempt armed the REQUEST-path
        cooldown, every request for the next ``_COOLDOWN_S`` is refused in ~0ms
        with fetch attempts frozen at 1 — even after the upstream recovers.
        The first request must instead make its own (still bounded) attempt and
        recover; the #3284 bound is what makes that safe.
        """
        monkeypatch.setattr(sa, "_JWKS_FETCH_TOTAL_S", self.BUDGET, raising=False)
        priv, pub = u.make_ec_keypair()
        good = json.dumps(u.build_ec_jwks(pub, "kid-1")).encode()
        calls = {"n": 0}

        async def _flaky() -> bytes:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("boot-time DNS blip")  # the warm-up's only attempt
            return good

        monkeypatch.setattr(sa, "_fetch_jwks", _flaky)

        report = _run(sa.prefetch_jwks())
        assert report["ok"] is False and calls["n"] == 1
        assert sa._jwks._last_failure_at is None, (
            "the boot warm-up armed the request-path cooldown")

        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        assert verify_ok(tok)["user_id"] == "user-123"  # recovered immediately
        assert calls["n"] == 2, "the first request was refused instead of trying"

    def test_prefetch_failure_reports_why_and_the_first_request_is_bounded(
            self, monkeypatch):
        """A failed warm-up must convert a HANG into a bounded retryable 503.

        Corrected from the pre-review version: the warm-up deliberately does
        NOT arm the request-path cooldown, so the first request PAYS its own
        bounded fetch attempt (one ``BUDGET`` here) and then answers 503 with
        ``Retry-After``. The old assertion that the request is refused in
        < BUDGET encoded the boot-armed-cooldown regression and was wrong.
        """
        monkeypatch.setattr(sa, "_JWKS_FETCH_TOTAL_S", self.BUDGET, raising=False)
        monkeypatch.setattr(sa, "_fetch_jwks",
                            SlowFetchStub(1.0, error=OSError("jwks down")))
        report = _run(sa.prefetch_jwks())  # bounded, never raises
        assert report["ok"] is False
        assert report["error"], "a failed warm-up must report why"
        assert report["elapsed_ms"] < (self.BUDGET + 0.25) * 1000
        assert sa._jwks._last_failure_at is None, (
            "the warm-up must not arm the request-path cooldown")

        priv, _ = u.make_ec_keypair()
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        t0 = time.monotonic()
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        elapsed = time.monotonic() - t0
        assert ei.value.status_code == 503
        # The request makes its OWN bounded attempt — not a 0ms cooldown
        # refusal (the pre-fix behaviour) and not an unbounded wait.
        assert self.BUDGET - 0.02 <= elapsed < self.BUDGET + 0.25, (
            f"the first request took {elapsed:.3f}s — expected one bounded attempt")
        assert sa._jwks._last_failure_at is not None, (
            "the REQUEST (not the warm-up) arms the cooldown on its own failure")
        assert int(ei.value.headers["Retry-After"]) >= 1

    def test_empty_jwks_is_reported_as_empty_not_as_a_transport_failure(
            self, monkeypatch):
        """A 200 with zero usable keys is a DIFFERENT failure from an outage.

        The boot log must say so (#2922: every failure states its reason), and
        the request path must answer 401 "Unknown signing key", never 503: a
        zero-key body is cached as ``{}``.
        """
        stub = FetchStub(body=b'{"keys": []}')
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        report = _run(sa.prefetch_jwks())
        assert report["ok"] is False
        assert report["keys"] == 0
        assert report["outcome"] == "empty", report
        assert report["error"], "every failure must state its reason (#2922)"
        assert stub.count == 1
        assert sa._jwks._last_failure_at is None, (
            "an empty body must not arm the request-path cooldown from boot")

    def test_empty_key_set_is_not_served_from_the_ttl_fast_path(
            self, monkeypatch):
        """An empty parse must not read as a FRESH cache entry.

        ``_parse_jwks`` leaves ``_fetched_at == 0.0`` with ``_keys == {}``; on
        a host whose monotonic clock is below the TTL, a bare
        ``now - _fetched_at < TTL`` check treats that as fresh and serves
        ``{}`` — a 401 window of up to a full TTL with the upstream healthy.
        The fast path is forced here by stamping ``_fetched_at`` with a recent
        monotonic value; the NEXT request must still attempt a fetch.
        """
        stub = FetchStub(body=b'{"keys": []}')
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        _run(sa.prefetch_jwks())
        assert stub.count == 1
        assert sa._jwks._keys == {}
        sa._jwks._fetched_at = time.monotonic()  # host uptime < TTL case

        # Assert on ``get()`` DIRECTLY — no ``verify_session_jwt``. R16 makes
        # that caller force-refetch on a kid miss, so its fetch count is 2 with
        # OR without the fix (an empty serve is followed by the forced
        # refetch) and it cannot discriminate. ``get()`` can: with the
        # usable-key-set guard the empty set is re-attempted (count 2); with
        # ``self._keys is not None`` it is TTL-served (count 1).
        keys = _run(sa._jwks.get())
        assert keys == {}
        assert stub.count == 2, (
            "the empty key set was TTL-served instead of re-attempted")

        # And the request path must still answer 401 "Unknown signing key"
        # from the empty set (never 503), at no additional fetch cost.
        priv, _ = u.make_ec_keypair()
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        with pytest.raises(HTTPException) as ei:
            verify_ok(tok)
        assert ei.value.status_code == 401  # unknown signing key, not 503
        assert stub.count == 2

    def test_prewarm_that_serves_stale_keys_is_not_reported_ready(
            self, monkeypatch):
        """A failed warm-up that served last-good keys is NOT ``ready`` (#2922).

        With last-good keys cached, a raising fetch makes ``get()`` log
        "serving stale" and return the OLD set — so the pre-fix ``if not keys``
        check is false and the report said ``ok=True/outcome="ready"`` for a
        genuinely down upstream. The report must say the warm-up did not
        refresh, and carry the reason.
        """
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        good = FetchStub(body=json.dumps(u.build_ec_jwks(pub, "kid-1")).encode())
        monkeypatch.setattr(sa, "_fetch_jwks", good)
        _run(sa._jwks.get(force=True))
        assert sa._jwks._keys, "precondition: last-good keys must be cached"

        monkeypatch.setattr(sa, "_fetch_jwks",
                            FetchStub(error=OSError("jwks down")))
        report = _run(sa.prefetch_jwks())
        assert report["ok"] is False, report
        assert report["outcome"] == "stale", report
        assert report["keys"] == 1, report
        assert report["error"] and "jwks down" in report["error"], report

    def test_prewarm_serving_stale_after_an_empty_rotation_is_not_ready(
            self, monkeypatch):
        """A zero-usable-key 200 against a WARM cache is also a stale serve.

        The upstream answered (200) but with no usable keys: the warm-up did
        NOT refresh, it serves the last-good set, and the boot report must not
        say ``ready`` (nor ``empty`` — the served cache is not empty).
        """
        priv, pub = u.make_ec_keypair()  # noqa: RUF059
        good = FetchStub(body=json.dumps(u.build_ec_jwks(pub, "kid-1")).encode())
        monkeypatch.setattr(sa, "_fetch_jwks", good)
        _run(sa._jwks.get(force=True))
        assert sa._jwks._keys, "precondition: last-good keys must be cached"

        monkeypatch.setattr(sa, "_fetch_jwks", FetchStub(body=b'{"keys": []}'))
        report = _run(sa.prefetch_jwks())
        assert report["ok"] is False, report
        assert report["outcome"] == "stale", report
        assert report["keys"] == 1, report
        assert "0 usable keys" in report["error"], report

    def test_prefetch_raising_fetch_with_an_empty_cache_reports_transport_error(
            self, monkeypatch):
        """A RAISING fetch with an empty cache is an OUTAGE, not an empty 200.

        ``_keys == {}`` is NOT ``None``, so the raise path has no last-good set
        to serve and returns ``({}, "JWKS fetch failed: …")``. Keyed off
        ``if not keys`` first, the empty-check swallowed that real reason and
        reported "upstream JWKS answered 200 with 0 usable keys (empty body /
        bad rotation)" — which the boot log renders as "NOT a transport
        outage" during an actual transport outage. ``_jwks`` is a MODULE global
        that is NOT reset per lifespan (#3284), so a prior empty 200 — or a
        prior lifespan — leaves exactly this state when the next boot's fetch
        raises.
        """
        stub = FetchStub(error=OSError("network down"))
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        # An EMPTY (not cold) cache: what a previous empty 200 leaves behind.
        sa._jwks._keys = {}
        sa._jwks._fetched_at = time.monotonic()

        report = _run(sa.prefetch_jwks())

        assert report["ok"] is False, report
        assert report["keys"] == 0, report
        assert report["outcome"] == "transport_error", report
        assert "network down" in report["error"], (
            "the REAL reason must survive, not the empty-rotation story: "
            + repr(report))
        assert "0 usable keys" not in report["error"], report
        assert stub.count == 1
        assert sa._jwks._last_failure_at is None, (
            "the boot warm-up must not arm the request-path cooldown")

    def test_prefetch_with_a_fresh_cache_and_armed_cooldown_reports_ready(
            self, monkeypatch):
        """A TTL-FRESH set is ``ready`` even when a cooldown blocks the fetch.

        The coverage gap a re-review named: the warm-up passes ``force=True``,
        so it skips the TTL fast path BY DESIGN; a cooldown armed by an earlier
        failure then made ``_resolve`` return the "refetch not attempted"
        reason for a cache that is milliseconds old. The report said ``stale``
        and the boot log warned "did NOT refresh — serving N last-good key(s)"
        about a set the first request serves at zero fetch cost. Outcome keys
        off FRESHNESS, not off "did this call fetch?" — while the separate
        stale-serve case (an EXPIRED set behind a cooldown) must stay ``stale``
        (pinned by test_prewarm_that_serves_stale_keys_is_not_reported_ready
        and its empty-rotation sibling).
        """
        priv, pub = u.make_ec_keypair()
        warm = warm_cache(monkeypatch, u.build_ec_jwks(pub, "kid-1"))
        assert warm.count == 1
        assert sa._jwks._keys, "precondition: a fresh key set is cached"
        # Arm the request-path cooldown; the cache stays TTL-fresh.
        sa._jwks._last_failure_at = time.monotonic()
        stub = FetchStub(error=OSError("must not be fetched"))
        monkeypatch.setattr(sa, "_fetch_jwks", stub)

        report = _run(sa.prefetch_jwks())

        assert report["ok"] is True, report
        assert report["outcome"] == "ready", report
        assert report["keys"] == 1, report
        assert report["error"] is None, report
        assert stub.count == 0, "a fresh cache needs no fetch"

        # The first request is served from that set at zero fetch cost.
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)
        assert verify_ok(tok)["user_id"] == "user-123"
        assert stub.count == 0

    def test_prefetch_cannot_raise_even_if_the_cache_explodes(self, monkeypatch):
        class _Exploding:
            async def get(self, *args, **kwargs):
                raise RuntimeError("cache exploded")

            async def get_with_origin(self, *args, **kwargs):
                raise RuntimeError("cache exploded")

        monkeypatch.setattr(sa, "_jwks", _Exploding())
        report = _run(sa.prefetch_jwks())
        assert report["ok"] is False and "RuntimeError" in report["error"]

    def test_slow_cold_burst_is_bounded_and_coalesced(self, monkeypatch):
        """The dashboard's PARALLEL boot calls on a cold process (#3284).

        One bounded fetch attempt; every concurrent request answers within the
        budget because the lock + cooldown coalesce them onto that attempt.
        Pre-fix all 20 waited the fetch out together (one 5–10s stall for
        every parallel call).
        """
        monkeypatch.setattr(sa, "_JWKS_FETCH_TOTAL_S", self.BUDGET, raising=False)
        stub = SlowFetchStub(1.0, error=OSError("jwks down"))
        monkeypatch.setattr(sa, "_fetch_jwks", stub)
        priv, _ = u.make_ec_keypair()
        tok = u.mint_es256_token(priv, "kid-1", base_payload(), iss=FIXED_ISSUER)

        async def _burst():
            return await asyncio.gather(
                *[sa.verify_session_jwt(make_request(tok)) for _ in range(20)],
                return_exceptions=True,
            )

        t0 = time.monotonic()
        outcomes = _run(_burst())
        elapsed = time.monotonic() - t0

        assert all(isinstance(o, HTTPException) and o.status_code == 503
                   for o in outcomes), [type(o).__name__ for o in outcomes]
        assert stub.count == 1, "the cold burst was not coalesced"
        assert elapsed < self.BUDGET + 0.5, (
            f"20 cold requests took {elapsed:.3f}s — not bounded")
        assert all(o.headers and int(o.headers["Retry-After"]) >= 1
                   for o in outcomes)

    def test_fetch_total_resolver_is_clamped_and_total_semantic(self, monkeypatch):
        """``TORTOISE_JWKS_TIMEOUT`` is a TOTAL, never below the phase sum."""
        floor = sa._JWKS_FETCH_PHASE_TOTAL_S + sa._JWKS_FETCH_MARGIN_S
        monkeypatch.setenv("TORTOISE_JWKS_TIMEOUT", "9")
        assert sa._resolve_fetch_total() == 9.0
        # Below the phase sum the hard deadline would win the race against the
        # phases and strand the httpx worker (CPython #87185) — clamp up.
        monkeypatch.setenv("TORTOISE_JWKS_TIMEOUT", "0.1")
        assert sa._resolve_fetch_total() == floor
        # Garbage / non-positive values fall back to the floor, never raise at
        # import (a malformed knob must not make the module unimportable).
        monkeypatch.setenv("TORTOISE_JWKS_TIMEOUT", "not-a-number")
        assert sa._resolve_fetch_total() == floor
        monkeypatch.setenv("TORTOISE_JWKS_TIMEOUT", "0")
        assert sa._resolve_fetch_total() == floor
        # NON-FINITE values are rejected, not clamped (review P2-1). The old
        # ``not v > 0`` guard is NaN-safe but NOT inf-safe: ``inf`` (and
        # ``1e309``, which ``float()`` evaluates to ``inf``) passed it, was not
        # ``< floor``, and landed in ``asyncio.timeout(inf)`` — which NEVER
        # fires, silently disabling the #3284 hard deadline.
        monkeypatch.setenv("TORTOISE_JWKS_TIMEOUT", "inf")
        assert sa._resolve_fetch_total() == floor
        monkeypatch.setenv("TORTOISE_JWKS_TIMEOUT", "1e309")
        assert sa._resolve_fetch_total() == floor
        monkeypatch.setenv("TORTOISE_JWKS_TIMEOUT", "nan")
        assert sa._resolve_fetch_total() == floor
