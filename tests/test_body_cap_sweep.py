"""#2032 body-sweep cap tests — public surfaces.

The sweep replaces every `request.json()`/`request.body()` in
tortoise/hosted_api.py with the #2029 streaming cap helper
(`_read_capped_body`) + per-surface caps. These tests pin the TWO load-bearing
properties of the sweep:

1. **413-before-parse:** an oversized CHUNKED body (no Content-Length — the
   RFC 7230 Transfer-Encoding case the cap exists for) returns 413 with the
   per-surface detail, and 413 is NOT swallowed by any local try/catch-all.
2. **Contract preservation:** malformed / empty / valid bodies behave exactly
   as before the sweep (500 / 422 / 400 / {} / mint).

Auth-gated surfaces (create_api_key, commit_session, claim_team,
agent_token_revoke, /internal/provision, /internal/demo) get their cap tests
in their home files (test_hosted_api.py, test_commit_endpoint.py,
test_claim_endpoints.py, test_signup_token_revoke.py) where the auth fixtures
live. This file covers the PUBLIC + webhook + OAuth surfaces with
self-contained fixtures.

#2048 extends this file with the RESIDUAL class #2032 left open: the
`body: XxxRequest` / `body: dict` endpoints (FastAPI buffers the full wire body
before validation) capped by `CappedBodyMiddleware`, and the /mcp sub-app's
chunked `Transfer-Encoding` bypass of its header-only size check.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Pepper + disabled rate limiting BEFORE tortoise imports (mirrors
# test_hosted_api.py / test_billing.py); the pepper is required by
# tortoise.auth at import time.
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")
# Registry-lane determinism: never inherit a dev shell's Supabase env.
for _v in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY",
           "TORTOISE_CONTROL_PLANE", "TURNSTILE_SECRET_KEY",
           "TORTOISE_SIGNUP_EMAIL_CONFIRM"):
    os.environ.pop(_v, None)

from tortoise.hosted_api import app  # noqa: E402, I001

from tests._http_fixtures import patched_tortoise_sdk  # noqa: E402
from tests.fake_control_plane import FakeControlPlane  # noqa: E402


def _oversized_chunked(n_chunks: int = 8, step: int = 8192):
    """Chunked generator, NO content-length — forces the streaming-cap path
    (a buffering parse of these chunks would 500, so 413 uniquely proves the
    cap fired before parse). 64 KiB total with defaults — > every test cap
    (256 B / 8192 B / 1024 B monkeypatched below)."""
    for _ in range(n_chunks):
        yield b"x" * step


@pytest.fixture
def embedded_client(monkeypatch, tmp_path):
    """Registry-lane TestClient on a temp embedded DB (no Docker)."""

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    for _v in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
               "SUPABASE_SERVICE_KEY", "TORTOISE_CONTROL_PLANE",
               "TURNSTILE_SECRET_KEY", "TORTOISE_SIGNUP_EMAIL_CONFIRM"):
        monkeypatch.delenv(_v, raising=False)
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    db_path = os.path.join(tmp_path, "sweep.db")
    # #2127: shared helper (tests._http_fixtures.patched_tortoise_sdk) —
    # patch __init__ → temp DB + #1950 TORTOISE_DB_PATH pin + close-then-
    # clear at enter; pop-pin → restore __init__ → deterministic anchor
    # close → clear overrides at exit. Its internal CM already closed the
    # anchors at teardown (most helper-like local shape) — the helper adds
    # the env pin + close-at-enter.
    with patched_tortoise_sdk(db_path), \
            TestClient(app, raise_server_exceptions=False) as tc:
        yield tc


@pytest.fixture
def supabase_client(monkeypatch, tmp_path):
    """Supabase-mode TestClient (FakeControlPlane) for the OAuth surfaces."""
    import tortoise.supabase_control as sc

    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    cp = FakeControlPlane({"organizations": [], "api_keys": [],
                           "org_memberships": [], "invitations": []})
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    db_path = os.path.join(tmp_path, "oauth_sweep.db")
    # #2127: shared helper (see embedded_client).
    with patched_tortoise_sdk(db_path), \
            TestClient(app, raise_server_exceptions=False) as tc:
        yield tc


# ── 413 detail literals (import-time derivation pin) ───────────────────


class TestDetailConstantsPinned:
    """#2032 review (second-model gate P2): the 413 detail strings derive
    their byte counts from the cap constants at IMPORT time. Pin the exact
    literals here (no monkeypatched caps) so a derivation regression — or a
    source-level cap change that alters the message — is caught; the per-site
    413 tests above assert `detail == ha_mod._BODY_413_DETAIL` (constant
    reference), which cannot catch message drift."""

    def test_body_detail_literals(self):
        import tortoise.hosted_api as ha_mod
        assert ha_mod._BODY_413_DETAIL == (
            "request body exceeds the size cap (256 KiB)")
        assert ha_mod._COMMIT_SESSION_413_DETAIL == (
            "commit session request body exceeds the size cap (8 MiB)")
        assert ha_mod._STRIPE_WEBHOOK_413_DETAIL == (
            "Stripe webhook body exceeds the size cap (1 MiB)")


# ── /v1/register ────────────────────────────────────────────────────────


class TestRegisterCap:
    def test_register_oversized_chunked_413(self, embedded_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = embedded_client.post("/v1/register", content=_oversized_chunked())
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._BODY_413_DETAIL

    def test_register_oversized_spoofed_cl_413(self, embedded_client, monkeypatch):
        """Valid payload + a spoofed short Content-Length: the streaming cap
        catches the under-claim (the CL header is never trusted)."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        payload = b'{"email": "a@b.co", "password": "secret123"}'
        r = embedded_client.post(
            "/v1/register", content=_oversized_chunked(),
            headers={"content-length": str(len(payload))})
        assert r.status_code == 413

    def test_register_malformed_500_preserved(self, embedded_client):
        """Malformed JSON → uncaught JSONDecodeError → 500 (unchanged)."""
        r = embedded_client.post(
            "/v1/register", content=b"{not json",
            headers={"content-type": "application/json"})
        assert r.status_code == 500

    def test_register_valid_mint_200(self, embedded_client):
        r = embedded_client.post(
            "/v1/register",
            json={"email": "cap-sweep@example.com", "password": "supersecret1"})
        assert r.status_code == 200, r.text
        assert r.json()["org_id"]


# ── /v1/signup/email ─────────────────────────────────────────────────────


class TestEmailSignupCap:
    def test_email_signup_oversized_chunked_413(self, embedded_client, monkeypatch):
        """Cap fires BEFORE the unconfigured-503 check (the 503 runs after the
        parse) — an oversized body 413s even on an unconfigured deployment."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = embedded_client.post("/v1/signup/email", content=_oversized_chunked())
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._BODY_413_DETAIL

    def test_email_signup_malformed_422_exact_detail(self, embedded_client):
        """Malformed/empty → 422 with the EXACT long string (unchanged)."""
        r = embedded_client.post(
            "/v1/signup/email", content=b"{",
            headers={"content-type": "application/json"})
        assert r.status_code == 422
        assert r.json()["detail"] == (
            "Invalid email or password. Check the email format and that the "
            "password is at least 6 characters.")

    def test_email_signup_valid_unconfigured_503(self, embedded_client):
        """A VALID body parses past the cap and hits the unconfigured 503 —
        proves the capped read is byte-transparent for under-cap bodies."""
        r = embedded_client.post(
            "/v1/signup/email",
            json={"email": "cap@example.com", "password": "supersecret1"})
        assert r.status_code == 503, r.text


# ── /v1/session/login ────────────────────────────────────────────────────


class TestSessionLoginCap:
    def test_session_login_oversized_chunked_413(self, embedded_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = embedded_client.post("/v1/session/login", content=_oversized_chunked())
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._BODY_413_DETAIL

    def test_session_login_empty_body_401_preserved(self, embedded_client):
        """Empty body → {} coercion → empty api_key → 401 prefix gate
        (unchanged — the 413 must NOT leak into the catch-all)."""
        r = embedded_client.post(
            "/v1/session/login", content=b"",
            headers={"content-type": "application/json"})
        assert r.status_code == 401

    def test_session_login_malformed_401_preserved(self, embedded_client):
        r = embedded_client.post(
            "/v1/session/login", content=b"{nope",
            headers={"content-type": "application/json"})
        assert r.status_code == 401


# ── /v1/agent/signup + /v1/agent/recover ─────────────────────────────────


class TestAgentSignupCap:
    def test_agent_signup_oversized_json_413(self, embedded_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = embedded_client.post(
            "/v1/agent/signup", content=_oversized_chunked(),
            headers={"content-type": "application/json"})
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._BODY_413_DETAIL

    def test_agent_signup_oversized_non_json_not_capped(self, embedded_client, monkeypatch):
        """PIN: the capped read sits INSIDE the content-type branch — an
        oversized NON-JSON body is ignored ({} path), never 413'd. A misplaced
        read outside the branch would turn this into a 413."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = embedded_client.post(
            "/v1/agent/signup", content=_oversized_chunked(),
            headers={"content-type": "text/plain"})
        assert r.status_code != 413

    def test_agent_signup_malformed_json_500_preserved(self, embedded_client):
        """JSON content-type + malformed → uncaught → 500 (unchanged)."""
        r = embedded_client.post(
            "/v1/agent/signup", content=b"{oops",
            headers={"content-type": "application/json"})
        assert r.status_code == 500

    def test_agent_signup_empty_json_500_preserved(self, embedded_client):
        """Empty body + JSON content-type → json.loads(b'') raises → 500
        (unchanged — the rejected `if raw else None` guard would have made
        this a mint; the sweep must NOT change it)."""
        r = embedded_client.post(
            "/v1/agent/signup", content=b"",
            headers={"content-type": "application/json"})
        assert r.status_code == 500

    def test_agent_signup_empty_no_ct_mints_200(self, embedded_client):
        """No content-type → body {} → mint path (unchanged)."""
        r = embedded_client.post("/v1/agent/signup", content=b"")
        assert r.status_code == 200, r.text
        assert r.json()["key"]


class TestAgentRecoverCap:
    def test_agent_recover_oversized_json_413(self, embedded_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = embedded_client.post(
            "/v1/agent/recover", content=_oversized_chunked(),
            headers={"content-type": "application/json"})
        assert r.status_code == 413

    def test_agent_recover_empty_body_422_preserved(self, embedded_client):
        """{} body → no signup_token → uniform 422 (unchanged)."""
        r = embedded_client.post("/v1/agent/recover", content=b"{}")
        assert r.status_code == 422
        assert r.json()["detail"]["error_code"] == "invalid_signup_token"


# ── /webhooks/stripe ─────────────────────────────────────────────────────


class TestStripeWebhookCap:
    def test_webhook_oversized_chunked_413(self, embedded_client, monkeypatch):
        """413 fires BEFORE signature verification — no Stripe env needed."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_STRIPE_WEBHOOK_MAX_BYTES", 1024)
        r = embedded_client.post(
            "/webhooks/stripe", content=_oversized_chunked(),
            headers={"stripe-signature": "t=1,v1=deadbeef"})
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._STRIPE_WEBHOOK_413_DETAIL


# ── OAuth surfaces (Supabase mode) ───────────────────────────────────────


class TestOAuthCaps:
    def test_oauth_token_oversized_413(self, supabase_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = supabase_client.post(
            "/oauth/token", content=_oversized_chunked(),
            headers={"content-type": "application/x-www-form-urlencoded"})
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._BODY_413_DETAIL

    def test_oauth_token_empty_form_400_preserved(self, supabase_client):
        """Empty form → parse_qs {} → grant_type None → RFC 6749
        unsupported_grant_type (unchanged)."""
        r = supabase_client.post("/oauth/token", content=b"")
        assert r.status_code == 400
        assert r.json()["error"] == "unsupported_grant_type"

    def test_oauth_revoke_oversized_413(self, supabase_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = supabase_client.post(
            "/oauth/revoke", content=_oversized_chunked(),
            headers={"content-type": "application/x-www-form-urlencoded"})
        assert r.status_code == 413

    def test_oauth_consent_oversized_413(self, supabase_client, monkeypatch):
        """Cap fires BEFORE verify_session_jwt — no session stub needed."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = supabase_client.post("/oauth/consent", content=_oversized_chunked())
        assert r.status_code == 413

    def test_oauth_dcr_register_oversized_413(self, supabase_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = supabase_client.post("/register", content=_oversized_chunked())
        assert r.status_code == 413

    def test_oauth_consent_malformed_400_preserved(self, supabase_client):
        r = supabase_client.post("/oauth/consent", content=b"{",
                                 headers={"content-type": "application/json"})
        assert r.status_code == 400
        assert r.json()["detail"] == "Invalid JSON body"


# ── #2048: pydantic/dict-body class + the /mcp chunked bypass ───────────────
# The residual class #2048 closes: a `body: XxxRequest` / `body: dict`
# parameter is parsed by FastAPI's `get_request_handler` (`await
# request.body()`) BEFORE any dependency or handler body runs, so those
# endpoints buffer the WHOLE wire body upstream of where #2032's per-site
# `_read_capped_body` call could sit. `CappedBodyMiddleware`
# (tortoise/body_limits.py) caps them at the default 256 KiB while the body is
# still a stream, then replays under-cap bytes to the router.

def _oversized_default_chunked(n_chunks: int = 6, step: int = 100_000):
    """600 KiB, chunked (NO content-length) — above the 256 KiB default cap.
    A buffering parse would 500/401 on this junk, so a 413 uniquely proves the
    middleware cap fired before parse (the #2032 `_oversized_chunked` idiom)."""
    for _ in range(n_chunks):
        yield b"x" * step


def _mcp_oversized_chunked(n_chunks: int = 12, step: int = 100_000):
    """1.2 MB, chunked — above the /mcp sub-app's 1 MB cap."""
    for _ in range(n_chunks):
        yield b"x" * step


class TestPydanticBodyClassCap:
    """#2048 — the pydantic/`dict`-body endpoints.

    Guard is LOAD-BEARING: with `CappedBodyMiddleware`'s default raised (the
    mutation), these bodies are parsed by the endpoint and return 401/422/200
    instead of 413.
    """

    def test_public_claim_email_oversized_chunked_413(self, embedded_client):
        """`POST /v1/claim/email` has NO auth dependency — the truly public
        member of the class (the issue also named
        `/v1/onboarding/github/connect`, which is auth-gated; see the issue
        note)."""
        import tortoise.body_limits as bl
        r = embedded_client.post("/v1/claim/email", content=_oversized_default_chunked())
        assert r.status_code == 413, r.text
        assert r.json()["detail"] == bl.BODY_413_DETAIL

    def test_auth_gated_pydantic_oversized_413_before_auth(self, embedded_client):
        """`/v1/objects` is auth-gated, but FastAPI buffers the declared body
        BEFORE the dependency — so an unauthenticated caller can drive the
        buffering and now gets 413, not 401. This is the class's whole point:
        auth-gating does NOT mitigate it."""
        r = embedded_client.post(
            "/v1/objects", content=_oversized_default_chunked(),
            headers={"content-type": "application/json"})
        assert r.status_code == 413, r.text

    def test_auth_gated_pydantic_spoofed_short_cl_413(self, embedded_client):
        """Valid JSON + a spoofed short Content-Length: the streaming cap
        catches the under-claim (the CL header is never trusted)."""
        r = embedded_client.post(
            "/v1/objects", content=_oversized_default_chunked(),
            headers={"content-type": "application/json",
                     "content-length": "37"})
        assert r.status_code == 413

    def test_auth_gated_pydantic_under_cap_reaches_auth_401(self, embedded_client):
        """Under the cap the middleware is BYTE-TRANSPARENT: the replayed body
        is parsed and the auth dependency runs → the pre-#2048 401."""
        r = embedded_client.post("/v1/objects", json={"name": "cap-probe"})
        assert r.status_code == 401, r.text

    def test_middleware_413_carries_cors_header(self, embedded_client):
        """Placement pin: `CappedBodyMiddleware` is registered FIRST so it is
        INNERMOST (inside CORSMiddleware). A middleware 413 therefore carries
        the SAME response headers a handler 413 would. (Mutation: register the
        middleware last/outermost → no access-control-allow-origin here.)"""
        r = embedded_client.post(
            "/v1/objects", content=_oversized_default_chunked(),
            headers={"content-type": "application/json",
                     "origin": "https://app.premiselabs.co"})
        assert r.status_code == 413
        assert (r.headers.get("access-control-allow-origin")
                == "https://app.premiselabs.co")


class TestCaptureSessionCapOverride:
    """`/v1/sessions` (capture_session) carries `conversation` — per-turn
    content is schema-UNBOUNDED on the wire, so the 256 KiB default would
    false-413 a legal capture. It is overridden to the capture hook's own
    16 MiB spool ceiling (tortoise/body_limits.py::CAPTURE_SESSION_MAX_BYTES)."""

    def test_300kib_capture_body_not_413(self, embedded_client):
        """300 KiB > the 256 KiB default: reaching the auth dependency (401)
        proves the override raised the cap. (Mutation: drop the override →
        413.)"""
        body = b" " * (300 * 1024) + b'{"conversation": []}'
        r = embedded_client.post(
            "/v1/sessions", content=body,
            headers={"content-type": "application/json"})
        assert r.status_code != 413, r.text

    def test_capture_override_value_pinned(self):
        """The override value is the client spool ceiling, derived in ONE
        place — pin it so a silent retune is caught."""
        import tortoise.body_limits as bl
        assert bl.CAPTURE_SESSION_MAX_BYTES == 16 * 1024 * 1024
        assert bl.CAPTURE_SESSION_413_DETAIL == (
            "session request body exceeds the size cap (16 MiB)")

    def test_default_cap_constant_shared(self):
        """hosted_api's `_BODY_MAX_BYTES` / `_BODY_413_DETAIL` are ALIASES of
        the shared module's — a drift would give the middleware and a handler
        cap different values for the same surface."""
        import tortoise.body_limits as bl
        import tortoise.hosted_api as ha_mod
        assert ha_mod._BODY_MAX_BYTES == bl.BODY_MAX_BYTES
        assert ha_mod._BODY_413_DETAIL == bl.BODY_413_DETAIL


class TestMcpChunkedBodyCap:
    """#2048 — the /mcp sub-app's `RequestBodySizeMiddleware` checked
    `content-length` ONLY, so a chunked `Transfer-Encoding` body (no CL header)
    bypassed the 1 MB cap. The streaming rewrite reads the body under the same
    cap. Mutation: restore the header-only check and
    `test_mcp_chunked_oversized_413` fails (the oversized body reaches the
    sub-app's auth middleware → 401)."""

    def test_mcp_chunked_oversized_413(self, embedded_client):
        r = embedded_client.post(
            "/mcp", content=_mcp_oversized_chunked(),
            headers={"content-type": "application/json",
                     "accept": "application/json, text/event-stream"})
        assert r.status_code == 413, r.text
        body = r.json()
        assert body["error"]["code"] == -32600
        assert body["error"]["message"] == "Request body too large (max 1MB)"

    def test_mcp_chunked_under_cap_replays_to_auth_401(self, embedded_client):
        """A small CHUNKED JSON-RPC body must survive the streaming read (the
        bytes are replayed downstream) and reach the auth middleware → 401.
        A cap that consumed the body without replaying would 400/hang here."""
        payload = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
        r = embedded_client.post(
            "/mcp", content=iter([payload]),
            headers={"content-type": "application/json",
                     "accept": "application/json, text/event-stream"})
        assert r.status_code == 401, r.text


class TestMiddlewareExemptionMap:
    """#2048: the middleware's scope is EXACTLY the previously-uncapped class.

    Every #2032 handler-capped route is exempt (its own `_read_capped_body`
    runs at its documented position). Two of those are EXACT-path exemptions
    where a prefix would have leaked coverage onto a pydantic sibling — the
    assertions below pin that, so a future prefix "simplification" is caught.
    """

    @staticmethod
    def _middleware_kwargs():
        import tortoise.body_limits as bl
        from tortoise.hosted_api import app
        entry = next(m for m in app.user_middleware
                     if m.cls is bl.CappedBodyMiddleware)
        return bl.CappedBodyMiddleware, entry.kwargs

    def test_2032_handler_capped_paths_exempt(self):
        cls, kwargs = self._middleware_kwargs()
        mw = cls(None, **kwargs)
        for path in ("/v1/register", "/v1/session/login", "/v1/signup/email",
                     "/v1/team/keys", "/v1/team/keys/k1/rotate", "/v1/claim",
                     "/v1/agent/signup", "/v1/agent/recover",
                     "/v1/agent/token/revoke", "/oauth/consent", "/oauth/token",
                     "/oauth/revoke", "/register", "/v1/sessions/commit",
                     "/v1/packs/manifests", "/webhooks/stripe",
                     "/v1/organizations/org1/import", "/internal/demo",
                     "/v1/internal/backups/purge", "/mcp/"):
            assert mw.resolve_cap(path) is None, path

    def test_pydantic_siblings_stay_capped(self):
        """`/v1/team/keys/{key_id}` (pydantic PATCH) and `/v1/claim/email`
        (public pydantic POST) sit NEXT TO an exempt path — they must keep the
        default cap. A prefix exemption on `/v1/team/keys` or `/v1/claim` would
        silently drop them (they are the class this middleware exists for)."""
        import tortoise.body_limits as bl
        cls, kwargs = self._middleware_kwargs()
        mw = cls(None, **kwargs)
        assert mw.resolve_cap("/v1/team/keys/k1") == (
            bl.BODY_MAX_BYTES, bl.BODY_413_DETAIL)
        assert mw.resolve_cap("/v1/claim/email") == (
            bl.BODY_MAX_BYTES, bl.BODY_413_DETAIL)
        assert mw.resolve_cap("/v1/objects") == (
            bl.BODY_MAX_BYTES, bl.BODY_413_DETAIL)
