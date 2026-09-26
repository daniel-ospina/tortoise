"""OAuth 2.1 remote-MCP auth tests (#524).

Covers the locked scoping surface (docs/scoping/2026-08-15-524-oauth-mcp-scoping.md):
  * P1 — RFC 9728 PRM + RFC 8414 AS metadata endpoints (root + /mcp variants)
  * P3 — DCR /register (RFC 7591, D1)
  * P2 — auth-code + PKCE (S256) flow, Supabase-auth-backed consent (D2)
  * P4 — RFC 8707 resource→team mapping (D4, client-declared, no picker UI)
  * D5 — rotating refresh tokens per (user, team); family revocation on team
         suspension
  * D6/D3 — oat_ access tokens self-sufficient at the MCP boundary; tt_
         fallback unchanged (existing suites keep covering the tt_ path)

The Supabase control plane is the in-memory FakeControlPlane (zero network);
the browser-session JWT verification is stubbed via hosted_api.verify_session_jwt
(the established test pattern — test_auth_flip / test_agent_signup).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Mount

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import FakeControlPlane  # noqa: E402, RUF100
from tortoise import hosted_api as _ha_mod  # noqa: E402, RUF100
from tortoise.hosted_api import (  # noqa: E402, F401, I001, RUF100
    RateLimitMiddleware,
    app,
    verify_session_jwt,
)
from tortoise.mcp_server import create_http_app  # noqa: E402, RUF100
from tortoise.oauth import (  # noqa: E402, RUF100
    ACCESS_TOKEN_PREFIX,
    SCOPES_ACCEPTED,
    SCOPES_SUPPORTED,
    _sha256,
    _valid_redirect_uri,
    mcp_resource_url,
    org_resource_url,
    resolve_oauth_access_token,
)

# #1719 (Task 3): org_memberships.user_id is a uuid column — real JWT
# subjects are UUIDs; non-UUID user_id literals are prod-impossible.
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"

TEST_BASE = "http://testserver"  # TestClient's request.base_url

REDIRECT = "http://127.0.0.1:8765/callback"  # RFC 8252 loopback redirect

TEAM_FREE = {
    "id": "team-free-001", "name": "Free Team", "tier": "free",
    "max_users": 1, "max_graphs": 1, "graph_size_cap": 10000,
    "ops_allowance": 1000,
}
TEAM_TEAM = {
    "id": "team-team-001", "name": "Team Tier", "tier": "team",
    "max_users": None, "max_graphs": None, "graph_size_cap": 500000,
    "ops_allowance": None,
}


def _member(user_id: str, org_id: str, role: str = "owner") -> dict:
    return {"user_id": user_id, "org_id": org_id, "role": role,
            "status": "active"}


def _join_second_team(api_client) -> None:
    """Seed a second active membership for _U1 (team-team-001) on the
    fixture control plane."""
    _, cp = api_client
    cp.tables["org_memberships"].append(
        _member(_U1, "team-team-001", "member"))


def _enable_supabase(monkeypatch, cp: FakeControlPlane) -> FakeControlPlane:
    import tortoise.supabase_control as sc
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    return cp


def _pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge) — S256."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _consent(tc, *, client_id: str, redirect_uri: str, challenge: str,
             state: str = "st-123", resource: str | None = None) -> httpx.Response:
    """POST /oauth/consent without asserting — the caller asserts the
    outcome, so negative cases reuse the same request shape."""
    return tc.post("/oauth/consent", json={
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": "mcp",
        "resource": resource,
    }, headers={"Authorization": "Bearer fake-session-jwt"})


def _auth_code_flow(tc, cp, *, user_id: str = _U1,
                    resource: str | None = None,
                    client_id: str | None = None,
                    redirect_uri: str = REDIRECT) -> dict:
    """Register → consent → return the auth code + client_id (P2 path).

    Assumes the caller has stubbed hosted_api.verify_session_jwt.
    """
    if client_id is None:
        client_id = _register_client(tc)["client_id"]
    verifier, challenge = _pkce()
    r = _consent(tc, client_id=client_id, redirect_uri=redirect_uri,
                 challenge=challenge, resource=resource)
    assert r.status_code == 200, r.text
    return {"client_id": client_id, "code": r.json()["code"],
            "verifier": verifier, "challenge": challenge}


def _register_client(tc, **overrides) -> dict:
    body = {
        "client_name": "test-connector",
        "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "mcp",
    }
    body.update(overrides)
    r = tc.post("/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _exchange(tc, *, client_id: str, code: str, verifier: str,
              redirect_uri: str = REDIRECT, resource: str | None = None,
              extra: dict | None = None) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    if resource is not None:
        data["resource"] = resource
    if extra:
        data.update(extra)
    return tc.post("/oauth/token", data=data)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def supabase_cp(monkeypatch) -> FakeControlPlane:
    """Supabase mode on + fake control plane seeded with two teams."""
    cp = FakeControlPlane({
        "organizations": [dict(TEAM_FREE), dict(TEAM_TEAM)],
        "org_memberships": [_member(_U1, "team-free-001")],
        "api_keys": [],
    })
    _enable_supabase(monkeypatch, cp)
    return cp


@pytest.fixture
def api_client(supabase_cp):
    """TestClient over the real hosted_api app (OAuth + well-known routes)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "oauth.db")
        # #2127: shared helper (tests._http_fixtures.patched_tortoise_sdk) —
        # patch __init__ → temp DB + #1950 TORTOISE_DB_PATH pin + close-then-
        # clear at enter; pop-pin → restore __init__ → deterministic anchor
        # close → clear overrides at exit (replaces the local
        # _patch_tortoise_sdk_init copy).
        with patched_tortoise_sdk(db_path), TestClient(app) as tc:
            yield tc, supabase_cp


@pytest.fixture
def session_user(api_client, monkeypatch):
    """Stub the browser-session JWT verification (JWKS path exercised by the
    session_auth suite; here the user identity is the test's concern)."""

    def _set(user_id: str = _U1, email: str = "u@example.com"):
        async def _fake(request):
            return {"user_id": user_id, "email": email}

        monkeypatch.setattr("tortoise.hosted_api.verify_session_jwt", _fake)

    yield _set


def _mounted_test_client(mcp_app) -> TestClient:
    @asynccontextmanager
    async def _lifespan(parent_app):
        async with mcp_app.lifespan(mcp_app):
            yield

    parent = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=mcp_app)])
    return TestClient(parent)


def _mcp_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }


def _parse_sse_json(r) -> dict | None:
    text = r.text
    if text.startswith("{"):
        return r.json()
    for line in text.splitlines():
        if line.startswith("data: "):
            import json
            return json.loads(line[6:])
    return None


# ═══════════════════════════════════════════════════════════════════════════
# P1 — metadata endpoints (RFC 9728 + RFC 8414)
# ═══════════════════════════════════════════════════════════════════════════

class TestMetadata:
    def test_prm_root(self, api_client):
        tc, _ = api_client
        r = tc.get("/.well-known/oauth-protected-resource")
        assert r.status_code == 200
        body = r.json()
        assert body["resource"] == mcp_resource_url(TEST_BASE)
        assert "http://testserver" in body["authorization_servers"]
        assert body["scopes_supported"] == ["mcp"]
        assert "header" in body["bearer_methods_supported"]

    def test_prm_path_variant(self, api_client):
        tc, _ = api_client
        r = tc.get("/.well-known/oauth-protected-resource/mcp")
        assert r.status_code == 200
        assert r.json()["resource"] == mcp_resource_url(TEST_BASE)

    def test_as_metadata_root(self, api_client):
        tc, _ = api_client
        r = tc.get("/.well-known/oauth-authorization-server")
        assert r.status_code == 200
        body = r.json()
        assert body["issuer"] == TEST_BASE
        assert body["authorization_endpoint"] == TEST_BASE + "/oauth/authorize"
        assert body["token_endpoint"] == TEST_BASE + "/oauth/token"
        assert body["registration_endpoint"] == TEST_BASE + "/register"
        assert body["response_types_supported"] == ["code"]
        assert body["grant_types_supported"] == ["authorization_code", "refresh_token"]
        assert body["token_endpoint_auth_methods_supported"] == ["none", "client_secret_post"]
        assert body["code_challenge_methods_supported"] == ["S256"]

    def test_as_metadata_path_variant(self, api_client):
        tc, _ = api_client
        r = tc.get("/.well-known/oauth-authorization-server/mcp")
        assert r.status_code == 200
        assert r.json()["issuer"] == TEST_BASE

    def test_metadata_endpoints_skip_auth(self, api_client):
        """Metadata is public (no Authorization header) — and works even in
        registry mode (static JSON describing the hosted AS)."""
        tc, _ = api_client
        r = tc.get("/.well-known/oauth-protected-resource")
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# P3 — DCR /register (RFC 7591, D1)
# ═══════════════════════════════════════════════════════════════════════════

class TestDynamicClientRegistration:
    def test_register_public_client(self, api_client):
        tc, cp = api_client
        reg = _register_client(tc)
        assert reg["client_id"].startswith("ct_")
        assert "client_secret" not in reg
        assert reg["redirect_uris"] == [REDIRECT]
        assert reg["grant_types"] == ["authorization_code", "refresh_token"]
        assert reg["token_endpoint_auth_method"] == "none"
        # Persisted with hash-only secret material
        rows = cp.tables["oauth_clients"]
        assert len(rows) == 1
        assert rows[0]["client_secret_hash"] is None
        assert rows[0]["id"] == reg["client_id"]

    def test_register_confidential_client_issues_secret(self, api_client):
        tc, cp = api_client
        reg = _register_client(tc, token_endpoint_auth_method="client_secret_post")
        assert reg["client_secret"]
        row = cp.tables["oauth_clients"][0]
        # stored hashed, never plaintext
        assert row["client_secret_hash"] != reg["client_secret"]
        assert row["client_secret_hash"] == hashlib.sha256(
            reg["client_secret"].encode()).hexdigest()

    def test_register_rejects_invalid_redirect(self, api_client):
        tc, _ = api_client
        r = tc.post("/register", json={
            "client_name": "bad", "redirect_uris": ["http://evil.example/cb"]})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_client_metadata"

    def test_register_rejects_bad_scope(self, api_client):
        tc, _ = api_client
        r = tc.post("/register", json={
            "client_name": "bad", "redirect_uris": [REDIRECT], "scope": "admin"})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_client_metadata"

    def test_register_rejects_unsupported_grant(self, api_client):
        tc, _ = api_client
        r = tc.post("/register", json={
            "client_name": "bad", "redirect_uris": [REDIRECT],
            "grant_types": ["password"]})
        assert r.status_code == 400

    def test_register_requires_client_name(self, api_client):
        tc, _ = api_client
        r = tc.post("/register", json={"redirect_uris": [REDIRECT]})
        assert r.status_code == 400

    def test_register_fails_closed_in_registry_mode(self, monkeypatch):
        """D3: OAuth is hosted-only — DCR 503s without the Supabase plane."""
        cp = FakeControlPlane()  # noqa: F841
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        with tempfile.TemporaryDirectory() as tmpdir, \
                patched_tortoise_sdk(os.path.join(tmpdir, "r.db")), \
                TestClient(app) as tc:
            # #2127: shared helper — the caller path arg is deliberately
            # dropped by the helper (documented); immaterial here (the 503
            # fires before any SDK construction). The helper additionally
            # closes any lifespan anchor deterministically at exit.
            r = tc.post("/register", json={
                "client_name": "x", "redirect_uris": [REDIRECT]})
            assert r.status_code == 503


# ═══════════════════════════════════════════════════════════════════════════
# P2 — authorize page + consent (PKCE-bound auth code)
# ═══════════════════════════════════════════════════════════════════════════

class TestAuthorizePage:
    def test_authorize_renders_consent_html(self, api_client):
        tc, _ = api_client
        reg = _register_client(tc)
        verifier, challenge = _pkce()  # noqa: RUF059
        r = tc.get("/oauth/authorize", params={
            "client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "st-1",
            "scope": "mcp", "resource": ""})
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "test-connector" in r.text  # client name embedded
        assert '"/oauth/consent"' in r.text
        # #1701 regression: the template was written with {{ }} (a .format()
        # escape) but is served via .replace() — double braces reached the
        # browser, breaking BOTH the CSS (unstyled page) and the inline JS
        # (SyntaxError → 'Team resolving…' hangs, Authorize does nothing).
        assert "{{" not in r.text and "}}" not in r.text
        assert r.text.count("{") == r.text.count("}")  # no unbalanced braces
        # #1704: the page reuses the dashboard's parent-domain cookie session
        # (sb-tortoise-auth-token) — a signed-in dashboard user must not see a
        # second login. The storage must declare the FULL SupportedStorage
        # interface: a missing setItem breaks gotrue's _saveSession (the
        # OAuth/email fallback would TypeError on every sign-in).
        assert "sb-tortoise-auth-token" in r.text
        assert "cookieStorage" in r.text
        assert "getItem(key) {" in r.text
        assert "setItem(key, value) {" in r.text
        assert "removeItem(key) {" in r.text
        assert "SIZE_GUARD" in r.text  # #1225 cookie-cap guard ported

    def test_consent_page_escapes_script_breakout(self, api_client):
        """P1 (PR #1264 review): a malicious state / client_name containing
        ``</script><script>`` cannot break out of the JSON-embedded PARAMS
        block, and the page is served with a nonce-gated CSP."""
        tc, _ = api_client
        reg = _register_client(
            tc, client_name='</script><script>window.pwned=1</script>')
        verifier, challenge = _pkce()  # noqa: RUF059
        evil_state = '</script><script>window.pwned=1</script>'
        r = tc.get("/oauth/authorize", params={
            "client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": evil_state,
            "scope": "mcp", "resource": ""})
        assert r.status_code == 200
        # the raw breakout payload must never appear verbatim in the page
        assert "</script><script>window.pwned=1</script>" not in r.text
        assert "<script>window.pwned=1" not in r.text
        # the JSON is embedded with < > & escaped as \u sequences
        assert "\\u003c/script\\u003e" in r.text
        assert "const PARAMS = {" in r.text
        # the embedded JSON still parses as a JS object literal
        import json as _json
        payload = r.text.split("const PARAMS = ", 1)[1].split(";", 1)[0]
        parsed = _json.loads(payload.encode().decode("unicode_escape"))
        assert parsed["state"] == evil_state
        assert parsed["client_name"] == reg["client_name"]
        # CSP is served with the nonce used on both script tags
        csp = r.headers.get("content-security-policy", "")
        assert "script-src" in csp and "nonce-" in csp
        assert "frame-ancestors 'none'" in csp
        assert r.text.count('nonce="') == 2  # CDN + inline script tags

    # ═════ #1701 R1 — consent page team-chooser + hardening (static strings) ═══
    # The page JS has no jsdom harness in this repo, so each hardening behavior
    # is pinned by a static-string assertion on the server-rendered page.

    def _consent_html(self, api_client, *, client_name: str = "test-connector") -> str:
        tc, _ = api_client
        reg = _register_client(tc, client_name=client_name)
        verifier, challenge = _pkce()  # noqa: RUF059
        r = tc.get("/oauth/authorize", params={
            "client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "st-1",
            "scope": "mcp", "resource": ""})
        assert r.status_code == 200
        return r.text

    def test_consent_html_team_picker_wiring(self, api_client):
        html = self._consent_html(api_client)
        assert 'id="org-select"' in html
        # the Authorize POST carries the PICKER selection first, then the
        # client-declared resource (single-team flow unchanged)
        assert "resource: orgResource || PARAMS.resource || null" in html
        # options carry each membership's team-scoped resource as the value
        assert "opt.value = m.resource" in html
        assert "memberships.forEach" in html

    def test_consent_html_single_team_select_not_visible(self, api_client):
        html = self._consent_html(api_client)
        # select present-but-hidden in the shared markup; only unhidden for
        # memberships.length > 1
        assert 'id="org-select" style="display:none' in html
        assert "memberships && memberships.length > 1" in html
        assert 'id="org-line"' in html
        assert html.count('id="org-select"') == 1

    def test_consent_html_authorize_disabled_in_markup(self, api_client):
        html = self._consent_html(api_client)
        # Authorize starts DISABLED in the markup and is enabled only after
        # the preview resolves (single team) or an explicit picker selection
        assert '<button class="btn-auth" id="btn-auth" disabled>' in html
        assert "function enableAuthorize()" in html
        assert "function disableAuthorize()" in html
        assert "if (authBtn().disabled)" in html  # submit guard

    def test_consent_html_picker_placeholder_and_no_autobind(self, api_client):
        html = self._consent_html(api_client)
        # no silent auto-bind: a leading disabled placeholder forces an explicit
        # change event, and teamResource is set ONLY in the change handler
        assert "Choose an org…" in html
        assert "placeholder.disabled = true" in html
        assert 'orgResource = orgSelectEl.value' in html
        assert "orgResource = null" in html  # reset at every run entry
        assert "if (orgResource) enableAuthorize(); else disableAuthorize();" in html

    def test_consent_html_401_recovery_refresh_first_no_signout(self, api_client):
        html = self._consent_html(api_client)
        # stale session recovery is refresh-first with a one-attempt cap and
        # NO signOut on the preview-401 path (shared parent-domain session)
        assert "supabaseClient.auth.refreshSession()" in html
        assert "staleRefreshes < 1" in html
        assert "Your session expired — sign in again." in html
        assert html.count("signOut(") == 0

    def test_consent_html_consent_post_401_refreshes_once(self, api_client):
        html = self._consent_html(api_client)
        # the Authorize POST 401 path refreshes once and re-POSTs with the
        # fresh session token; a second 401 shows the expired-session view
        assert "res.status === 401" in html
        assert "refreshSession()" in html
        assert "d2.session.access_token" in html
        assert html.count("signOut(") == 0

    def test_consent_html_retry_and_inflight_guard(self, api_client):
        html = self._consent_html(api_client)
        assert 'id="btn-retry-preview"' in html
        # in-flight guard spans the async preview + options are rebuilt from
        # scratch (no duplicate rows on sequential re-runs)
        assert "let previewInFlight = false" in html
        assert "if (previewInFlight) return;" in html
        assert "while (orgSelect.firstChild) orgSelect.removeChild" in html
        assert "onAuthStateChange" in html
        assert 'event === "INITIAL_SESSION"' in html

    def test_authorize_invalid_request_redirects_error(self, api_client):
        """A REGISTERED redirect_uri receives the error redirect (RFC 6749
        §4.1.2.1) when the request is invalid (here: missing PKCE)."""
        tc, _ = api_client
        reg = _register_client(tc)
        r = tc.get("/oauth/authorize", params={
            "client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "response_type": "code", "state": "st-1"},
            follow_redirects=False)
        assert r.status_code == 307
        assert "error=invalid_request" in r.headers["location"]
        assert "state=st-1" in r.headers["location"]

    def test_authorize_invalid_client_no_open_redirect(self, api_client):
        """Open-redirect guard: an unknown client's error is NOT redirected
        to an unregistered redirect_uri — it returns JSON instead."""
        tc, _ = api_client
        verifier, challenge = _pkce()  # noqa: RUF059
        r = tc.get("/oauth/authorize", params={
            "client_id": "ct_nope", "redirect_uri": "https://evil.example/cb",
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "st-1"},
            follow_redirects=False)
        assert r.status_code == 400
        assert "Location" not in r.headers or "evil.example" not in r.headers.get("Location", "")

    def test_authorize_requires_pkce(self, api_client):
        tc, _ = api_client
        reg = _register_client(tc)
        r = tc.get("/oauth/authorize", params={
            "client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "response_type": "code", "state": "st-1"},
            follow_redirects=False)
        assert "error=" in r.headers["location"]

    def test_authorize_rejects_plain_challenge(self, api_client):
        tc, _ = api_client
        reg = _register_client(tc)
        r = tc.get("/oauth/authorize", params={
            "client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": "x" * 64, "code_challenge_method": "plain"},
            follow_redirects=False)
        assert "error=" in r.headers["location"]


class TestLoopbackPortAgnostic:
    """#2846 — RFC 8252 §7.3: for loopback redirect URIs the AS must ignore the
    port, because a native client (Claude Code CLI) binds an ephemeral port at
    request time and cannot know it at registration. Anthropic's connector docs
    require the same.

    Live repro before the fix: registering ``http://localhost/callback`` and
    authorizing with ``http://localhost:3118/callback`` returned
    ``400 redirect_uri is not registered for this client`` — identical to the
    response for a genuinely wrong PATH, so the AS could not even distinguish
    the two cases.
    """

    PORTLESS = "http://localhost/callback"

    def test_full_flow_with_ephemeral_port(self, api_client, session_user):
        """The issue's indicator: register WITHOUT a port, complete consent and
        token exchange presenting an ephemeral port at every step."""
        tc, cp = api_client
        session_user(_U1)
        reg = _register_client(tc, redirect_uris=[self.PORTLESS])
        assert reg["redirect_uris"] == [self.PORTLESS]
        presented = "http://localhost:3118/callback"
        flow = _auth_code_flow(tc, cp, client_id=reg["client_id"],
                               redirect_uri=presented)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"], redirect_uri=presented)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"].startswith(ACCESS_TOKEN_PREFIX)
        assert body["token_type"] == "Bearer"
        # The code is bound to the PRESENTED uri, so the token step's exact
        # comparison (RFC 6749 §4.1.3) is satisfied without being loosened.
        assert cp.tables["oauth_codes"][-1]["redirect_uri"] == presented

    def test_authorize_page_accepts_ephemeral_port(self, api_client):
        """The GET /oauth/authorize leg must also accept it (it validates
        through the same helper). On rejection it redirects with ``error=``.

        Also asserts the presented URI actually reached the rendered page's
        embedded PARAMS — that embedding is what `redirectBack` later navigates
        to, so a validation pass that silently dropped it would still fail the
        real journey."""
        tc, _ = api_client
        reg = _register_client(tc, redirect_uris=[self.PORTLESS])
        r = tc.get("/oauth/authorize", params={
            "client_id": reg["client_id"],
            "redirect_uri": "http://localhost:3118/callback",
            "response_type": "code", "state": "st-1",
            "code_challenge": "x" * 43,
            "code_challenge_method": "S256"},
            follow_redirects=False)
        assert r.status_code == 200, r.text
        assert "error=" not in r.headers.get("location", "")
        assert "localhost:3118" in r.text

    def test_authorize_page_rejects_different_path(self, api_client):
        """GET-level negative control, mirroring the consent-side one."""
        tc, _ = api_client
        reg = _register_client(tc, redirect_uris=[self.PORTLESS])
        r = tc.get("/oauth/authorize", params={
            "client_id": reg["client_id"],
            "redirect_uri": "http://localhost:3118/evil",
            "response_type": "code", "state": "st-1",
            "code_challenge": "x" * 43,
            "code_challenge_method": "S256"},
            follow_redirects=False)
        assert "error=" in r.headers.get("location", "") or r.status_code == 400

    def test_error_is_relayed_to_ephemeral_listener(self, api_client):
        """REVIEW P2 — the invalid-params error path has its OWN open-redirect
        guard, which used strict membership. A client that registered a PORTLESS
        loopback URI therefore got a JSON 400 where its ephemeral listener was
        waiting for a redirect, so the CLI could never surface the error. The
        guard now uses the same matcher — and is still an open-redirect guard,
        because the matcher refuses parse-differential input.
        """
        tc, _ = api_client
        reg = _register_client(tc, redirect_uris=[self.PORTLESS])
        # response_type=token is invalid -> validate_authorize_params raises.
        r = tc.get("/oauth/authorize", params={
            "client_id": reg["client_id"],
            "redirect_uri": "http://localhost:3118/callback",
            "response_type": "token", "state": "st-1",
            "code_challenge": "x" * 43,
            "code_challenge_method": "S256"},
            follow_redirects=False)
        assert r.status_code in (302, 303, 307), r.text
        loc = r.headers["location"]
        assert loc.startswith("http://localhost:3118/callback")
        assert "error=invalid_request" in loc

    def test_error_path_does_not_echo_parser_differential(self, api_client):
        """The error relay must not become a DIRECT open redirect: a
        differential URI is refused by the matcher, so we return JSON rather
        than a Location header pointing at the attacker."""
        tc, _ = api_client
        reg = _register_client(tc, redirect_uris=[self.PORTLESS])
        r = tc.get("/oauth/authorize", params={
            "client_id": reg["client_id"],
            "redirect_uri": "http://evil.example:8443\\@localhost/callback",
            "response_type": "token", "state": "st-1"},
            follow_redirects=False)
        assert r.status_code == 400, r.text
        assert "evil.example" not in r.headers.get("location", "")

    def test_different_path_still_rejected(self, api_client, session_user):
        """Negative control: the port is ignored, the PATH is not."""
        tc, _ = api_client
        session_user(_U1)
        reg = _register_client(tc, redirect_uris=[self.PORTLESS])
        _, challenge = _pkce()
        r = _consent(tc, client_id=reg["client_id"],
                     redirect_uri="http://localhost:3118/evil",
                     challenge=challenge)
        assert r.status_code == 400
        assert "not registered" in r.json()["error_description"]

    def test_host_is_not_relaxed(self, api_client, session_user):
        """Only the PORT varies. ``127.0.0.1`` must not satisfy a registration
        for ``localhost`` — both are loopback, but they are different hosts."""
        tc, _ = api_client
        session_user(_U1)
        reg = _register_client(tc, redirect_uris=[self.PORTLESS])
        _, challenge = _pkce()
        r = _consent(tc, client_id=reg["client_id"],
                     redirect_uri="http://127.0.0.1:3118/callback",
                     challenge=challenge)
        assert r.status_code == 400
        assert "not registered" in r.json()["error_description"]

    def test_non_loopback_keeps_strict_exact_match(self, api_client, session_user):
        """The hosted posture is unchanged for https: an explicit port is a
        different string and must not match."""
        tc, _ = api_client
        session_user(_U1)
        reg = _register_client(tc,
                               redirect_uris=["https://app.example.com/callback"])
        _, challenge = _pkce()
        r = _consent(tc, client_id=reg["client_id"],
                     redirect_uri="https://app.example.com:443/callback",
                     challenge=challenge)
        assert r.status_code == 400
        assert "not registered" in r.json()["error_description"]


class TestRedirectUriMatches:
    """Unit coverage for the helper — the HTTP tests above prove it is wired,
    these pin the predicate's boundaries directly."""

    def test_loopback_port_ignored(self):
        from tortoise.oauth import _redirect_uri_matches as m
        assert m("http://localhost/callback", "http://localhost:3118/callback")
        assert m("http://localhost:3118/callback", "http://localhost/callback")
        assert m("http://127.0.0.1/cb", "http://127.0.0.1:8765/cb")
        assert m("http://[::1]/cb", "http://[::1]:8765/cb")
        assert m("http://localhost:1/cb", "http://localhost:2/cb")

    def test_exact_match_still_true_for_non_loopback(self):
        from tortoise.oauth import _redirect_uri_matches as m
        assert m("https://app.example.com/cb", "https://app.example.com/cb")

    def test_non_loopback_is_strict(self):
        from tortoise.oauth import _redirect_uri_matches as m
        assert not m("https://app.example.com/cb", "https://app.example.com:443/cb")
        assert not m("https://app.example.com/cb", "https://app.example.com/other")

    def test_host_and_scheme_are_never_relaxed(self):
        from tortoise.oauth import _redirect_uri_matches as m
        assert not m("http://localhost/cb", "http://127.0.0.1:3118/cb")
        assert not m("http://localhost/cb", "https://localhost:3118/cb")
        # The relaxation keys on LOOPBACK, not on the http scheme — the issue's
        # Target draws the boundary at "non-loopback URIs keep strict exact
        # match". https-on-loopback is loopback, so the port rule applies to it
        # too; scheme/host/path are still compared exactly. Pinned explicitly so
        # this boundary is a decision rather than an accident.
        assert m("https://localhost/cb", "https://localhost:3118/cb")

    def test_path_query_and_fragment_are_compared(self):
        from tortoise.oauth import _redirect_uri_matches as m
        assert not m("http://localhost/cb", "http://localhost:3118/evil")
        assert not m("http://localhost/cb?x=1", "http://localhost:3118/cb?x=2")
        assert not m("http://localhost/cb#a", "http://localhost:3118/cb#b")
        assert m("http://localhost/cb?x=1", "http://localhost:3118/cb?x=1")

    def test_non_string_inputs_never_raise(self):
        """``redirect_uri`` is typed ``str | None`` at the call site, but a
        corrupt row can hold anything — the helper must never raise.

        ``123`` is the case where the isinstance guard is the ONLY protection
        (``urlparse(None)`` does not raise — its ``.hostname`` is None, caught by
        the hostname guard instead), so it is pinned separately from ``None``.
        """
        from tortoise.oauth import _redirect_uri_matches as m
        assert not m("http://localhost/cb", None)
        assert not m(None, "http://localhost/cb")
        assert not m("http://localhost/cb", 123)  # isinstance is the only guard
        assert not m("http://localhost/cb", "")
        assert not m("", "http://localhost:3118/cb")
        assert not m("http://localhost/cb", "not a url")

    def test_unparseable_input_returns_false(self):
        """Actually exercises the ``except ValueError`` branch — an unbalanced
        IPv6 bracket is one of the few inputs ``urlparse`` rejects outright.
        (``"not a url"`` does NOT: it parses as a relative reference.)"""
        from tortoise.oauth import _redirect_uri_matches as m
        assert not m("http://localhost/cb", "http://[::1/cb")
        assert not m("http://[::1/cb", "http://localhost/cb")

    def test_hostless_uri_only_matches_itself(self):
        """A hostless URI cannot be REGISTERED — `_valid_redirect_uri` requires
        a hostname — so it can never enter `redirect_uris`. The exact-match
        short-circuit runs before the hostname guard, which preserves the
        pre-#2846 semantics for the equal case (a strict superset of behaviour)
        while the guard still blocks any relaxation."""
        from tortoise.oauth import _redirect_uri_matches as m
        assert m("http:///cb", "http:///cb")         # exact match, as before
        assert not m("http:///cb", "http:///other")  # no host → no relaxation
        assert not m("http:///cb", "http://localhost:1/cb")

    def test_uppercase_host_and_scheme_relax(self):
        """RFC 3986 §3.2.2 — the host is case-insensitive, so an uppercase
        form must not defeat the relaxation."""
        from tortoise.oauth import _redirect_uri_matches as m
        assert m("http://LOCALHOST/cb", "http://localhost:3118/cb")
        assert m("HTTP://localhost/cb", "http://localhost:3118/cb")

    def test_userinfo_must_match_exactly(self):
        """Userinfo is compared, so a crafted userinfo cannot ride a loopback
        registration. (`urlparse` takes the host after the LAST ``@``, so without
        this check `http://evil@localhost/cb` would resolve to loopback.)"""
        from tortoise.oauth import _redirect_uri_matches as m
        assert not m("http://localhost/cb", "http://evil@localhost:3118/cb")
        assert not m("http://evil@localhost/cb", "http://localhost:3118/cb")
        assert m("http://a@localhost/cb", "http://a@localhost:3118/cb")

    def test_port_values_are_not_validated(self):
        """Pinned deliberately: the matcher compares only that the port is
        IGNORED, never that it is sane. ``urlparse`` raises on ``:abc`` only if
        ``.port`` is touched, which this code never does, and a browser refuses
        to navigate such a URL at all — so an insane port cannot become an
        exfiltration path. Documented here so it is a known boundary."""
        from tortoise.oauth import _redirect_uri_matches as m
        assert m("http://localhost/cb", "http://localhost:0/cb")
        assert m("http://localhost/cb", "http://localhost:abc/cb")
        assert m("http://localhost/cb", "http://localhost:99999/cb")

    def test_unsafe_bytes_never_match(self):
        r"""REVIEW P0 — a raw backslash moves the authority boundary between
        Python's `urlsplit` and the browser's WHATWG parser, so
        `http://evil.example\@localhost/cb` is host `localhost` to us and
        `evil.example` to the browser. The code is delivered by navigating the
        browser to the RAW string, so the pre-fix predicate validated that URI as
        loopback and then handed the authorization code to the attacker.

        The assertions are grouped by WHY they fail, because two review rounds
        misattributed that. In particular the control characters are NOT
        differentials: `urlsplit` strips tab/CR/LF exactly as a browser does, so
        tab is refused by this gate only because the gate is broader than the
        differential — not because the two parsers disagree.
        """
        from tortoise.oauth import _redirect_uri_matches as m
        attack = "http://evil.example:8443\\@127.0.0.1/callback"

        # ── Group 1: enforced by the byte gate ALONE ──────────────────────
        # Each of these is mutation-verified to FAIL when
        # `_unsafe_redirect_uri_bytes` is removed from `_redirect_uri_matches`.
        #
        # The exact-match short-circuit must also be gated, or a differential
        # string already sitting in a client row bypasses the guard entirely.
        assert not m(attack, attack)
        assert not m("http://evil.example\\@localhost/callback",
                     "http://evil.example\\@localhost/callback")
        # Userinfo identical on both sides and differing ONLY by port, so
        # nothing but the byte gate can refuse this pair.
        assert not m("http://evil.example:8443\\@127.0.0.1:1/callback",
                     "http://evil.example:8443\\@127.0.0.1:2/callback")
        # Tab: both sides parse to the same loopback host, so again only the
        # byte gate can refuse it.
        assert not m("http://localhost/cb", "http://localhost\t:3118/cb")

        # ── Group 2: behaviour pins, NOT gate isolation ────────────────────
        # These stay False with the gate removed — they are refused by the
        # loopback predicate (the parsed host is not a loopback host) or by the
        # userinfo comparison. Kept because they pin the boundary, not because
        # they prove the guard.
        assert not m("http://127.0.0.1/callback", attack)
        assert not m("http://localhost/callback",
                     "http://evil.example\\@localhost:3118/callback")
        # The backslash lands in the REGISTERED host, so `_is_loopback` rejects
        # it before any comparison.
        assert not m("http://localhost\\cb", "http://localhost:3118\\cb")
        assert not m("http://localhost/cb", "http://local\x00host:3118/cb")
        assert not m("http://localhost/cb", "http://localhost\x7f:3118/cb")

    def test_differential_uris_are_refused_even_on_exact_match(self):
        """The broad rejection is DELIBERATE, not an oversight: a byte we refuse
        to reason about is refused before the exact-match short-circuit, so a URI
        that registered before this gate existed stops matching. A raw backslash
        is not legal in a URI (RFC 3986), so nothing legitimate is lost, and
        fail-closed is the only safe direction on input that decides where a
        credential is sent. Pinned so a future loosening is a decision."""
        from tortoise.oauth import _redirect_uri_matches as m
        assert not m("https://app.example.com/cb?q=C:\\Users\\x",
                     "https://app.example.com/cb?q=C:\\Users\\x")
        assert not m("https://app.example.com/cb?a=1\x7fb",
                     "https://app.example.com/cb?a=1\x7fb")


class TestRedirectUriParserDifferential:
    """REVIEW P0 — a parse-differential redirect_uri must be refused at
    REGISTRATION as well as at validation, or the same string can re-enter via
    a client row created earlier."""

    ATTACK = "http://evil.example:8443\\@127.0.0.1/callback"

    def test_registration_rejects_backslash_authority(self, api_client):
        tc, _ = api_client
        r = tc.post("/register", json={
            "client_name": "differential",
            "redirect_uris": [self.ATTACK],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        })
        assert r.status_code == 400, r.text
        assert _valid_redirect_uri(self.ATTACK) is False

    def test_registration_rejects_control_characters(self, api_client):
        tc, _ = api_client
        r = tc.post("/register", json={
            "client_name": "differential",
            "redirect_uris": ["http://localhost\t/cb"],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        })
        assert r.status_code == 400, r.text

    def test_valid_loopback_and_https_still_register(self, api_client):
        """The guard must not over-reject: ordinary URIs still register."""
        tc, _ = api_client
        for uri in ("http://localhost/callback", "http://127.0.0.1:8765/callback",
                    "http://[::1]/callback", "https://app.example.com/callback"):
            assert _valid_redirect_uri(uri) is True, uri
        assert _register_client(
            tc, redirect_uris=["http://localhost/callback"])["client_id"]


class TestCursorPrivateUseRedirectScheme:
    """#3579 — Cursor IDE's MCP OAuth DCR sends a private-use callback scheme
    (RFC 8252 §7.1). Registration is all-or-nothing, so before this change a
    single `cursor://` entry rejected the WHOLE request: Cursor got no
    client_id, never reached /oauth/authorize, and a Cursor tester had no way
    to sign in. Reproduced live against api.premiselabs.co before the fix.
    """

    CURSOR_CB = "cursor://anysphere.cursor-mcp/oauth/callback"

    def test_cursor_custom_scheme_registers(self, api_client):
        tc, _ = api_client
        assert _valid_redirect_uri(self.CURSOR_CB) is True
        body = _register_client(tc, redirect_uris=[self.CURSOR_CB])
        assert body["client_id"].startswith("ct_")

    def test_cursor_mixed_registration_is_accepted_whole(self, api_client):
        """The real Cursor payload: the documented loopback + https pair AND
        the legacy custom scheme. One invalid entry used to sink all three."""
        tc, _ = api_client
        uris = ["https://www.cursor.com/agents/mcp/oauth/callback",
                "http://localhost:8787/callback",
                self.CURSOR_CB]
        body = _register_client(tc, redirect_uris=uris)
        assert body["redirect_uris"] == uris

    def test_cursor_documented_pair_unchanged(self, api_client):
        """Additive: the Cursor path that already worked still works."""
        tc, _ = api_client
        for uri in ("https://www.cursor.com/agents/mcp/oauth/callback",
                    "http://localhost:8787/callback"):
            assert _valid_redirect_uri(uri) is True, uri
        assert _register_client(
            tc, redirect_uris=["http://localhost:8787/callback"])["client_id"]

    def test_non_allowlisted_schemes_fail_closed(self):
        """The allowlist fails closed on every scheme not explicitly reasoned
        about — especially the browser-executed ones, which the consent page
        would otherwise navigate to and execute in its own origin."""
        for uri in ("javascript:alert(1)",
                    "data:text/html,<script>x</script>",
                    "vbscript:msgbox(1)",
                    "file:///etc/passwd",
                    "vscode://x/callback",
                    "claude://x/callback",
                    self.CURSOR_CB + "#frag",
                    "cursor:",
                    "http://evil.example\\@127.0.0.1/callback"):
            assert _valid_redirect_uri(uri) is False, uri

    def test_fragment_refused_for_https_and_loopback_too(self):
        """RFC 6749 §3.1.2. The error message already promised this; the check
        now matches it for every scheme (registration-time only, so no existing
        registered client is affected).

        The BARE trailing `#` is the case review caught: `parsed.fragment` is
        empty for it, so a value testing only `parsed.fragment` was a false
        PASS on this very check."""
        assert _valid_redirect_uri("https://app.example.com/cb#frag") is False
        assert _valid_redirect_uri("http://localhost:8787/cb#frag") is False
        assert _valid_redirect_uri("https://app.example.com/cb#") is False
        assert _valid_redirect_uri("http://localhost:8787/cb#") is False
        assert _valid_redirect_uri("https://app.example.com/cb?#") is False
        assert _valid_redirect_uri(self.CURSOR_CB + "#") is False
        assert _valid_redirect_uri("https://app.example.com/cb") is True

    def test_cursor_scheme_still_matches_exactly_at_authorize(self):
        """The #2846 port relaxation keys on TWO loopback hosts, so a
        private-use scheme keeps strict exact-string matching — one Cursor
        client can never satisfy another's registration."""
        from tortoise.oauth import _redirect_uri_matches as m
        assert m(self.CURSOR_CB, self.CURSOR_CB)
        assert not m(self.CURSOR_CB, "cursor://evil.example/oauth/callback")
        assert not m(self.CURSOR_CB,
                     "cursor://anysphere.cursor-mcp/oauth/other")
        assert not m("http://localhost:8787/callback",
                     "http://localhost:8787/callback#")
        assert not m("http://localhost:8787/callback#frag",
                     "http://localhost:8787/callback#other")
        assert not m(self.CURSOR_CB,
                     "cursor://anysphere.cursor-mcp/oauth/callback?x=1")

    def test_non_allowlisted_scheme_rejected_on_the_endpoint(self, api_client):
        tc, _ = api_client
        r = tc.post("/register", json={
            "client_name": "javascript-scheme",
            "redirect_uris": ["javascript:alert(1)"],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        })
        assert r.status_code == 400, r.text
        assert r.json()["error"] == "invalid_client_metadata"

    def test_cursor_scheme_drives_the_full_flow_to_a_team_scoped_token(
            self, api_client, session_user):
        """Drive the REAL flow, not just the boolean helper: register ->
        GET /oauth/authorize -> POST /oauth/consent -> /oauth/token -> resolve
        the minted access token. Cursor failed at REGISTRATION, so every later
        leg was unreachable; asserting each one here pins the whole path, and
        the final leg proves the token is bound to the consented team."""
        tc, cp = api_client
        session_user(_U1)
        reg = _register_client(tc, redirect_uris=[self.CURSOR_CB])
        assert reg["redirect_uris"] == [self.CURSOR_CB]
        verifier, challenge = _pkce()

        page = tc.get("/oauth/authorize", params={
            "client_id": reg["client_id"], "redirect_uri": self.CURSOR_CB,
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "st-cur",
            "scope": "mcp", "resource": ""})
        assert page.status_code == 200, page.text
        assert "text/html" in page.headers["content-type"]

        con = _consent(tc, client_id=reg["client_id"],
                       redirect_uri=self.CURSOR_CB, challenge=challenge)
        assert con.status_code == 200, con.text

        tok = _exchange(tc, client_id=reg["client_id"],
                        code=con.json()["code"], verifier=verifier,
                        redirect_uri=self.CURSOR_CB,
                        resource=mcp_resource_url(TEST_BASE))
        assert tok.status_code == 200, tok.text
        access = tok.json()["access_token"]
        assert access.startswith(ACCESS_TOKEN_PREFIX)

        # The token is team-scoped by construction: its row carries the team the
        # consent chose, and the MCP boundary resolves it to THAT team only.
        team = resolve_oauth_access_token(cp, access)
        assert team is not None
        assert team["org_id"] == "team-free-001"

    def test_cursor_mismatch_is_refused_on_the_authorize_endpoint(
            self, api_client, session_user):
        """Registration accepting the scheme must NOT widen matching: a
        different host, path or query is refused where it counts — the
        authorize leg that would otherwise hand over a code."""
        tc, _ = api_client
        session_user(_U1)
        reg = _register_client(tc, redirect_uris=[self.CURSOR_CB])
        for bad in ("cursor://evil.example/oauth/callback",
                    "cursor://anysphere.cursor-mcp/oauth/other",
                    "https://anysphere.cursor-mcp/oauth/callback",
                    self.CURSOR_CB + "?x=1",
                    self.CURSOR_CB + "#frag",
                    self.CURSOR_CB + "#"):
            r = tc.get("/oauth/authorize", params={
                "client_id": reg["client_id"], "redirect_uri": bad,
                "response_type": "code", "code_challenge": "x" * 60,
                "code_challenge_method": "S256", "state": "st",
                "scope": "mcp", "resource": ""})
            assert r.status_code == 400, (bad, r.status_code, r.text)


class TestConsentPreview:
    def test_preview_resolves_default_team(self, api_client, session_user):
        tc, _ = api_client
        session_user(_U1)
        r = tc.get("/oauth/consent/preview", params={"resource": ""},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 200
        assert r.json()["org_id"] == "team-free-001"
        assert r.json()["org_name"] == "Free Team"

    def test_preview_resolves_team_scoped_resource(self, api_client, session_user):
        tc, _ = api_client
        session_user(_U1)
        r = tc.get("/oauth/consent/preview",
                   params={"resource": org_resource_url(TEST_BASE, "team-free-001")},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 200
        assert r.json()["org_id"] == "team-free-001"

    def test_preview_non_member_403(self, api_client, session_user):
        tc, _ = api_client
        session_user(_U1)
        r = tc.get("/oauth/consent/preview",
                   params={"resource": org_resource_url(TEST_BASE, "team-team-001")},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 403

    # ═════ #1701 R1 — consent account-chooser (resource-less OAuth clients) ═══

    def test_preview_multi_team_no_resource_returns_memberships(self, api_client, session_user):
        """Two active teams + no team-scoped resource → the chooser list, not a
        400 (a ChatGPT-style client cannot declare an RFC 8707 resource)."""
        tc, _ = api_client
        session_user(_U1)
        # seed the second membership via the fixture's control plane
        _join_second_team(api_client)
        r = tc.get("/oauth/consent/preview", params={"resource": ""},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 200
        body = r.json()
        assert body["org_id"] is None
        assert body["org_name"] is None
        assert body["resource"] == mcp_resource_url(TEST_BASE)
        assert [m["org_id"] for m in body["memberships"]] == [
            "team-free-001", "team-team-001"]  # deterministic sort
        for m in body["memberships"]:
            assert m["resource"] == org_resource_url(TEST_BASE, m["org_id"])
            assert m["org_name"]

    def test_preview_multi_team_origin_root_echo_returns_memberships(self, api_client, session_user):
        """An OpenAI-style origin-root resource echo is treated as no team
        scope → the chooser list for a multi-team account."""
        tc, _ = api_client
        session_user(_U1)
        _join_second_team(api_client)
        r = tc.get("/oauth/consent/preview", params={"resource": TEST_BASE},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 200
        body = r.json()
        assert body["org_id"] is None
        assert len(body["memberships"]) == 2

    def test_preview_single_team_origin_root_echo_binds_sole_team(self, api_client, session_user):
        """Origin-root echo on a single-team account resolves to the sole team
        (byte-identical shape to today's bare-resource default)."""
        tc, _ = api_client
        session_user(_U1)
        r = tc.get("/oauth/consent/preview", params={"resource": TEST_BASE},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 200
        body = r.json()
        assert body["org_id"] == "team-free-001"
        assert "memberships" not in body

    def test_preview_declared_bare_mcp_resource_keeps_resource_field(self, api_client, session_user):
        """Byte-identical contract: a truthy DECLARED bare-MCP resource keeps
        today's team-scoped resource field on the single-team preview."""
        tc, _ = api_client
        session_user(_U1)
        r = tc.get("/oauth/consent/preview",
                   params={"resource": mcp_resource_url(TEST_BASE)},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 200
        body = r.json()
        assert body["org_id"] == "team-free-001"
        assert body["resource"] == org_resource_url(TEST_BASE, "team-free-001")

    def test_preview_memberships_exclude_suspended_teams(self, api_client, session_user):
        """2 active + 1 suspended membership → the chooser lists only the two
        active teams (a suspended team can never be picked)."""
        tc, cp = api_client
        session_user(_U1)
        _join_second_team(api_client)
        cp.tables["organizations"].append({
            "id": "team-suspended-001", "name": "Suspended Team",
            "tier": "free", "suspended_at": "2026-08-15T00:00:00Z"})
        cp.tables["org_memberships"].append(
            _member(_U1, "team-suspended-001", "member"))
        r = tc.get("/oauth/consent/preview", params={"resource": ""},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 200
        body = r.json()
        assert [m["org_id"] for m in body["memberships"]] == [
            "team-free-001", "team-team-001"]

    def test_preview_one_active_one_suspended_autobinds_active(self, api_client, session_user):
        """1 active + 1 suspended membership → sole-ACTIVE-team auto-bind shape
        (no memberships key; the suspended team never counts)."""
        tc, cp = api_client
        session_user(_U1)
        _join_second_team(api_client)
        cp.tables["organizations"][1]["suspended_at"] = "2026-08-15T00:00:00Z"  # team-team-001
        r = tc.get("/oauth/consent/preview", params={"resource": ""},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 200
        body = r.json()
        assert body["org_id"] == "team-free-001"
        assert "memberships" not in body

    def test_preview_all_teams_suspended_403(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        cp.tables["organizations"][0]["suspended_at"] = "2026-08-15T00:00:00Z"
        r = tc.get("/oauth/consent/preview", params={"resource": ""},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 403
        assert r.json()["error"] == "invalid_grant"

    def test_preview_declared_resource_suspended_team_403(self, api_client, session_user):
        """A declared team-scoped resource for a suspended team 403s at
        preview — never a code that dies at a later exchange."""
        tc, cp = api_client
        session_user(_U1)
        cp.tables["organizations"][0]["suspended_at"] = "2026-08-15T00:00:00Z"
        r = tc.get("/oauth/consent/preview",
                   params={"resource": org_resource_url(TEST_BASE, "team-free-001")},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 403
        assert r.json()["error"] == "invalid_grant"


# ═══════════════════════════════════════════════════════════════════════════
# P2 + P4 — code exchange, PKCE, RFC 8707 mapping
# ═══════════════════════════════════════════════════════════════════════════

class TestCodeExchange:
    def test_happy_path_full_flow(self, api_client, session_user):
        """register → consent → token exchange → oat_ token bound to the
        user's sole team (RFC 8707 default mapping)."""
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["token_type"] == "Bearer"
        assert body["access_token"].startswith(ACCESS_TOKEN_PREFIX)
        assert body["refresh_token"].startswith("ort_")
        assert body["expires_in"] == 3600
        # token rows persist with the bound team (P4)
        acc = cp.tables["oauth_access_tokens"][0]
        assert acc["org_id"] == "team-free-001"
        assert acc["user_id"] == _U1
        assert acc["token_hash"] == hashlib.sha256(
            body["access_token"].encode()).hexdigest()
        ref = cp.tables["oauth_refresh_tokens"][0]
        assert ref["org_id"] == "team-free-001"

    def test_wrong_verifier_rejected(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier="v" * 60)
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_grant"

    def test_code_is_single_use(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        r1 = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                       verifier=flow["verifier"])
        assert r1.status_code == 200
        r2 = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                       verifier=flow["verifier"])
        assert r2.status_code == 400
        assert r2.json()["error"] == "invalid_grant"

    def test_code_claim_is_atomic(self, api_client, session_user):
        """P2 (PR #1264 review): the single-use claim is one conditional
        UPDATE — a second (concurrent) exchange sees no claimable row and
        must never double-issue a token pair."""
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        r1 = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                       verifier=flow["verifier"])
        assert r1.status_code == 200
        # second exchange (simulating a racing worker) fails
        r2 = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                       verifier=flow["verifier"])
        assert r2.status_code == 400
        assert r2.json()["error"] == "invalid_grant"
        # exactly ONE token pair was ever minted — no double issue
        live_access = [t for t in cp.tables["oauth_access_tokens"]
                       if t["revoked_at"] is None]
        live_refresh = [t for t in cp.tables["oauth_refresh_tokens"]
                        if t["revoked_at"] is None]
        assert len(live_access) == 1
        assert len(live_refresh) == 1

    def test_consume_code_claim_via_fake(self, api_client, session_user):
        """The atomic claim updates in place: after a successful consume the
        row carries used_at AND the durable 'claimed' state, and a second consume
        cannot double-issue.

        #3027: the second consume of a code whose first claim has no recorded
        OUTCOME is terminal `invalid_grant` — the claim is consumed by an attempt
        that may still be running, and a different request must neither re-arm it
        (two live families) nor report it retryable (the retry can terminate).
        The reconciler settles the residue; a code that MINTED is terminal too —
        see `test_code_is_single_use` / `test_code_claim_is_atomic`."""
        from tortoise.oauth import OAuthError, _consume_code
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        row = _consume_code(cp, flow["code"])
        assert row["code_hash"] == hashlib.sha256(
            flow["code"].encode()).hexdigest()
        stored = cp.tables["oauth_codes"][0]
        assert stored["used_at"] is not None  # claimed in place
        assert stored["redemption_state"] == "claimed"
        # #3027: the second consume is TERMINAL, not retryable — the retry can
        # terminate (the first attempt may settle `minted`), and advertising it as
        # retryable is the untruthful signal #2863 removed. A code that actually
        # MINTED is also terminal — see `test_code_is_single_use`.
        with pytest.raises(OAuthError) as exc:
            _consume_code(cp, flow["code"])
        assert exc.value.status == 400
        assert exc.value.error == "invalid_grant"

    def test_redirect_uri_mismatch_rejected(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"],
                      redirect_uri="http://127.0.0.1:9999/other")
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_grant"

    def test_client_mismatch_rejected(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        other = _register_client(tc, client_name="other")
        r = _exchange(tc, client_id=other["client_id"], code=flow["code"],
                      verifier=flow["verifier"])
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_grant"

    def test_expired_code_rejected(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        cp.tables["oauth_codes"][0]["expires_at"] = "2020-01-01T00:00:00+00:00"
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"])
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_grant"

    def test_unknown_client_401(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        r = _exchange(tc, client_id="ct_bogus", code=flow["code"],
                      verifier=flow["verifier"])
        assert r.status_code == 401
        assert r.json()["error"] == "invalid_client"

    def test_confidential_client_secret_required(self, api_client, session_user):
        tc, cp = api_client  # noqa: RUF059
        session_user(_U1)
        reg = _register_client(tc, token_endpoint_auth_method="client_secret_post")
        verifier, challenge = _pkce()
        tc.post("/oauth/consent", json={
            "client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "scope": "mcp"},
            headers={"Authorization": "Bearer fake"})
        # no secret → 401
        r = tc.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "code": "whatever", "code_verifier": verifier})
        assert r.status_code == 401
        assert r.json()["error"] == "invalid_client"

    def test_confidential_client_exchange_success(self, api_client, session_user):
        """client_secret_post: the correct secret completes the exchange."""
        tc, cp = api_client  # noqa: RUF059
        session_user(_U1)
        reg = _register_client(tc, token_endpoint_auth_method="client_secret_post")
        verifier, challenge = _pkce()
        r = tc.post("/oauth/consent", json={
            "client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "scope": "mcp"},
            headers={"Authorization": "Bearer fake"})
        code = r.json()["code"]
        r2 = tc.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "client_id": reg["client_id"],
            "client_secret": reg["client_secret"],
            "redirect_uri": REDIRECT,
            "code": code, "code_verifier": verifier})
        assert r2.status_code == 200, r2.text
        assert r2.json()["access_token"].startswith(ACCESS_TOKEN_PREFIX)
        # wrong secret → 401
        r3 = tc.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "client_id": reg["client_id"],
            "client_secret": "cs_wrong_secret",
            "redirect_uri": REDIRECT,
            "code": code, "code_verifier": verifier})
        assert r3.status_code == 401
        assert r3.json()["error"] == "invalid_client"


# ═══════════════════════════════════════════════════════════════════════════
# P4 — RFC 8707 resource indicator → team mapping (D4)
# ═══════════════════════════════════════════════════════════════════════════

class TestParseResource:
    """#1701 R1 — origin-root tolerance (exact equality only) + boundary."""

    def test_origin_root_maps_to_bare_mcp(self):
        from tortoise.oauth import parse_resource
        canonical, org_id = parse_resource(TEST_BASE, TEST_BASE)
        assert canonical == mcp_resource_url(TEST_BASE)
        assert org_id is None
        canonical2, org_id2 = parse_resource(TEST_BASE, TEST_BASE + "/")
        assert canonical2 == mcp_resource_url(TEST_BASE)
        assert org_id2 is None

    def test_origin_root_rejected_for_foreign_origin(self):
        from tortoise.oauth import OAuthError, parse_resource
        with pytest.raises(OAuthError) as ei:
            parse_resource(TEST_BASE, "https://evil.example/mcp")
        assert ei.value.status == 400
        assert ei.value.error == "invalid_resource"

    def test_origin_path_prefix_rejected(self):
        """Exact equality only — {base}/v1/keys is NOT a bare-MCP alias."""
        from tortoise.oauth import OAuthError, parse_resource
        with pytest.raises(OAuthError):
            parse_resource(TEST_BASE, TEST_BASE + "/v1/keys")

    def test_bare_mcp_trailing_slash_accepted(self):
        from tortoise.oauth import parse_resource
        canonical, org_id = parse_resource(TEST_BASE, mcp_resource_url(TEST_BASE) + "/")
        assert canonical == mcp_resource_url(TEST_BASE)
        assert org_id is None

    def test_team_scoped_still_parses(self):
        from tortoise.oauth import parse_resource
        resource = org_resource_url(TEST_BASE, "team-free-001")
        canonical, org_id = parse_resource(TEST_BASE, resource)
        assert canonical == resource
        assert org_id == "team-free-001"


class TestRfc8707Mapping:
    def test_team_scoped_resource_binds_that_team(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        # user-1 joins the second team
        cp.tables["org_memberships"].append(
            _member(_U1, "team-team-001", "member"))
        resource = org_resource_url(TEST_BASE, "team-team-001")
        flow = _auth_code_flow(tc, cp, resource=resource)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"], resource=resource)
        assert r.status_code == 200, r.text
        assert cp.tables["oauth_access_tokens"][0]["org_id"] == "team-team-001"
        assert cp.tables["oauth_refresh_tokens"][0]["org_id"] == "team-team-001"

    def test_multi_team_default_requires_declaration(self, api_client, session_user):
        """D4 (no picker UI): a multi-team user MUST declare the resource."""
        tc, cp = api_client
        session_user(_U1)
        cp.tables["org_memberships"].append(
            _member(_U1, "team-team-001", "member"))
        r = tc.post("/oauth/consent", json={
            "client_id": _register_client(tc)["client_id"],
            "redirect_uri": REDIRECT, "response_type": "code",
            "code_challenge": "x" * 60, "code_challenge_method": "S256",
            "scope": "mcp", "resource": None},
            headers={"Authorization": "Bearer fake"})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_resource"

    def test_resource_for_non_member_team_rejected(self, api_client, session_user):
        tc, cp = api_client  # noqa: RUF059
        session_user(_U1)
        resource = org_resource_url(TEST_BASE, "team-team-001")  # not a member
        r = tc.post("/oauth/consent", json={
            "client_id": _register_client(tc)["client_id"],
            "redirect_uri": REDIRECT, "response_type": "code",
            "code_challenge": "x" * 60, "code_challenge_method": "S256",
            "scope": "mcp", "resource": resource},
            headers={"Authorization": "Bearer fake"})
        assert r.status_code == 403
        assert r.json()["error"] == "invalid_resource"

    def test_resource_mismatch_at_exchange_rejected(self, api_client, session_user):
        """RFC 8707: the token request's resource must match the authorized
        team (token exfiltration guard)."""
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)  # bound to team-free-001
        other = org_resource_url(TEST_BASE, "team-team-001")
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"], resource=other)
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_grant"

    def test_unknown_resource_rejected(self, api_client, session_user):
        tc, cp = api_client  # noqa: RUF059
        session_user(_U1)
        r = tc.post("/oauth/consent", json={
            "client_id": _register_client(tc)["client_id"],
            "redirect_uri": REDIRECT, "response_type": "code",
            "code_challenge": "x" * 60, "code_challenge_method": "S256",
            "scope": "mcp", "resource": "https://evil.example/other"},
            headers={"Authorization": "Bearer fake"})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_resource"

    def test_zero_team_user_rejected(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        cp.tables["org_memberships"] = []
        r = tc.post("/oauth/consent", json={
            "client_id": _register_client(tc)["client_id"],
            "redirect_uri": REDIRECT, "response_type": "code",
            "code_challenge": "x" * 60, "code_challenge_method": "S256",
            "scope": "mcp", "resource": None},
            headers={"Authorization": "Bearer fake"})
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# D5 — rotating refresh tokens, revocation on suspension
# ═══════════════════════════════════════════════════════════════════════════

class TestRefreshRotation:
    def test_refresh_rotates_pair(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"])
        first = r.json()
        old_refresh = first["refresh_token"]
        old_access = first["access_token"]

        r2 = tc.post("/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": old_refresh,
            "client_id": flow["client_id"],
        })
        assert r2.status_code == 200, r2.text
        second = r2.json()
        assert second["access_token"] != old_access
        assert second["refresh_token"] != old_refresh

        # D5 rotation: the old refresh token is dead
        r3 = tc.post("/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": old_refresh,
            "client_id": flow["client_id"],
        })
        assert r3.status_code == 400
        assert r3.json()["error"] == "invalid_grant"

        # and the old access token was revoked
        old_row = [t for t in cp.tables["oauth_access_tokens"]  # noqa: RUF015
                   if t["token_hash"] == hashlib.sha256(
                       old_access.encode()).hexdigest()][0]
        assert old_row["revoked_at"] is not None

        # the new refresh token still works (chain continues)
        r4 = tc.post("/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": second["refresh_token"],
            "client_id": flow["client_id"],
        })
        assert r4.status_code == 200, r4.text

    def test_rotation_race_single_winner(self, api_client, session_user):
        """P2 + P3 (PR #1264 review): two workers that both pass the initial
        revoked_at check race the rotation claim — the winner's pair
        survives, the loser's orphan pair is rolled back, and the old token
        is revoked exactly once."""
        from tortoise.oauth import OAuthError, _issue_tokens
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"])
        assert r.status_code == 200, r.text
        prev = [t for t in cp.tables["oauth_refresh_tokens"]  # noqa: RUF015
                if t["revoked_at"] is None][0]
        prev_access = [t for t in cp.tables["oauth_access_tokens"]  # noqa: RUF015
                       if t["revoked_at"] is None][0]
        args = dict(client_id=flow["client_id"], user_id=_U1,
                    org_id="team-free-001", scope="mcp", resource=None)
        # worker A wins the atomic claim
        out_a = _issue_tokens(cp, prev_refresh=prev,
                              prev_access_id=prev_access["id"], **args)
        # worker B races with the SAME presented token: claim fails and the
        # orphan pair is rolled back (no second live grant)
        with pytest.raises(OAuthError) as exc:
            _issue_tokens(cp, prev_refresh=prev,
                          prev_access_id=prev_access["id"], **args)
        assert exc.value.status == 400
        assert exc.value.error == "invalid_grant"
        live = [t for t in cp.tables["oauth_refresh_tokens"]
                if t["revoked_at"] is None]
        assert len(live) == 1
        assert live[0]["id"] == out_a["_refresh_id"]
        assert live[0]["rotated_from"] == prev["id"]
        # B's orphan pair was rolled back (revoked), not left live
        orphans = [t for t in cp.tables["oauth_refresh_tokens"]
                   if t["rotated_from"] == prev["id"]
                   and t["id"] != out_a["_refresh_id"]]
        assert len(orphans) == 1
        assert orphans[0]["revoked_at"] is not None
        live_access = [t for t in cp.tables["oauth_access_tokens"]
                       if t["revoked_at"] is None]
        assert [t["id"] for t in live_access] == [out_a["_access_id"]]

    def test_refresh_rejects_wrong_client(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"])
        other = _register_client(tc, client_name="other")
        r2 = tc.post("/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": r.json()["refresh_token"],
            "client_id": other["client_id"],
        })
        assert r2.status_code == 401
        assert r2.json()["error"] == "unauthorized_client"

    def test_refresh_rejects_expired(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"])
        cp.tables["oauth_refresh_tokens"][0]["expires_at"] = "2020-01-01T00:00:00+00:00"
        r2 = tc.post("/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": r.json()["refresh_token"],
            "client_id": flow["client_id"],
        })
        assert r2.status_code == 400
        assert r2.json()["error"] == "invalid_grant"


class TestSuspensionRevocation:
    def _granted(self, tc, cp):
        client_id = _register_client(tc)["client_id"]
        flow = _auth_code_flow(tc, cp, client_id=client_id)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"])
        out = r.json()
        out["_client_id"] = client_id
        return out

    def test_suspended_team_revokes_family_on_refresh(self, api_client, session_user):
        """D5: team suspension revokes the whole (user, team) refresh family
        and rejects the refresh."""
        tc, cp = api_client
        session_user(_U1)
        tokens = self._granted(tc, cp)
        # suspend the team (durable suspended_at — the #308 authority)
        cp.tables["organizations"][0]["suspended_at"] = "2026-08-15T00:00:00Z"
        r = tc.post("/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": tokens["_client_id"],
        })
        assert r.status_code == 403
        assert r.json()["error"] == "invalid_grant"
        # the whole family is revoked (D5) — nothing left to rotate
        active = [t for t in cp.tables["oauth_refresh_tokens"]
                  if t["revoked_at"] is None]
        assert active == []

    # #1701 R1: suspension now refuses at the consent MINT
    # (test_suspended_team_consent_mint_refused) AND at a mint-then-suspend
    # exchange (test_exchange_guard_still_rejects_mint_then_suspend_race).

    def test_suspended_team_consent_mint_refused(self, api_client, session_user):
        """#1701 R1: a suspended team can never MINT a code — the consent POST
        (with a declared team resource for the now-suspended team) 403s
        cleanly instead of minting a code that dies at the later exchange."""
        tc, cp = api_client
        session_user(_U1)
        cp.tables["organizations"][0]["suspended_at"] = "2026-08-15T00:00:00Z"
        r = tc.post("/oauth/consent", json={
            "client_id": _register_client(tc)["client_id"],
            "redirect_uri": REDIRECT, "response_type": "code",
            "code_challenge": "x" * 60, "code_challenge_method": "S256",
            "scope": "mcp",
            "resource": org_resource_url(TEST_BASE, "team-free-001")},
            headers={"Authorization": "Bearer fake"})
        assert r.status_code == 403
        assert r.json()["error"] == "invalid_grant"
        assert cp.tables.get("oauth_codes", []) == []

    def test_exchange_guard_still_rejects_mint_then_suspend_race(self, api_client, session_user):
        """#1701 R1: the exchange-time backstop stays live — a code minted
        while the team was ACTIVE, then the team suspended before redemption,
        must 403 at the token endpoint (no tokens issued)."""
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)  # minted while active
        cp.tables["organizations"][0]["suspended_at"] = "2026-08-15T00:00:00Z"
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"])
        assert r.status_code == 403
        assert r.json()["error"] == "invalid_grant"
        assert cp.tables.get("oauth_access_tokens", []) == []

    def test_lapsed_membership_revokes_refresh(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        tokens = self._granted(tc, cp)
        cp.tables["org_memberships"] = []  # seat removed
        r = tc.post("/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": tokens["_client_id"],
        })
        assert r.status_code == 403
        assert r.json()["error"] == "invalid_grant"
        assert cp.tables["oauth_refresh_tokens"][0]["revoked_at"] is not None

    def test_explicit_revocation(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        tokens = self._granted(tc, cp)
        r = tc.post("/oauth/revoke", data={
            "token": tokens["refresh_token"],
            "token_type_hint": "refresh_token",
            "client_id": tokens["_client_id"],
        })
        assert r.status_code == 200
        r2 = tc.post("/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": tokens["_client_id"],
        })
        assert r2.status_code == 400
        assert r2.json()["error"] == "invalid_grant"

    def test_revoke_unknown_token_is_200(self, api_client, session_user):
        tc, _ = api_client
        r = tc.post("/oauth/revoke", data={"token": "ort_bogus"})
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# D6 + D3 — MCP boundary: oat_ tokens introspect; tt_ fallback unchanged
# ═══════════════════════════════════════════════════════════════════════════

class TestMcpBoundary:
    def _mcp(self, cp, *, supabase: bool = True):
        mcp_app = create_http_app(allowed_origins=[])
        return _mounted_test_client(mcp_app)

    def test_oauth_token_works_on_mcp(self, api_client, session_user):
        """Full flow → oat_ access token authenticates the MCP endpoint
        (D6: introspected at the boundary; no tt_ key minting)."""
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"])
        access = r.json()["access_token"]

        mcp_tc = self._mcp(cp)
        mcp_tc.headers.update(_mcp_headers(access))
        with mcp_tc:
            rr = mcp_tc.post("/mcp", json={
                "jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert rr.status_code == 200, rr.text
            assert "result" in _parse_sse_json(rr)

    def test_oauth_token_suspended_team_403(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"])
        cp.tables["organizations"][0]["suspended_at"] = "2026-08-15T00:00:00Z"
        mcp_tc = self._mcp(cp)
        mcp_tc.headers.update(_mcp_headers(r.json()["access_token"]))
        with mcp_tc:
            rr = mcp_tc.post("/mcp", json={
                "jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert rr.status_code == 403
            body = _parse_sse_json(rr)
            assert body["error"]["code"] == -32006  # ERR_SUSPENDED

    def test_revoked_oauth_token_401(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"])
        access = r.json()["access_token"]
        tc.post("/oauth/revoke", data={"token": access,
                                        "token_type_hint": "access_token"})
        mcp_tc = self._mcp(cp)
        mcp_tc.headers.update(_mcp_headers(access))
        with mcp_tc:
            rr = mcp_tc.post("/mcp", json={
                "jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert rr.status_code == 401

    def test_revoked_oauth_token_401_on_a_warm_cache_within_ttl(self, api_client, session_user):
        """#2864 — the 60s warm-token cache is REAL and this pins it.

        ``TeamResolutionMiddleware`` keys its cache by raw token and serves a
        warm hit for 60s WITHOUT re-introspecting (``mcp_auth.py:279-297``), so
        ``resolve_oauth_access_token``'s ``revoked_at`` check is bypassed until
        the entry ages out. ``test_revoked_oauth_token_401`` above cannot see
        this: it builds a FRESH app per call, i.e. a cold cache.

        This deliberately drives ONE client (one app -> one middleware -> one
        cache) so the grace window is observable. The grace is a documented
        design trade-off for the multi-machine deployment (a revocation on one
        Fly machine cannot invalidate another's cache), not an accident —
        pinning the exact bound is what stops it drifting silently.
        """
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        access = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                           verifier=flow["verifier"]).json()["access_token"]
        call = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}

        mcp_tc = self._mcp(cp)
        mcp_tc.headers.update(_mcp_headers(access))
        with mcp_tc:
            # 1. a good request warms the cache for this token
            assert mcp_tc.post("/mcp", json=call).status_code == 200
            # 2. revoke out of band. The revoke ENDPOINT returns 200 even for an
            #    unknown or already-revoked token (RFC 7009 idempotence —
            #    oauth.py::revoke_token), so a 200 proves NOTHING about whether
            #    the row was actually marked. Assert the stored row instead, or
            #    step 3 could pass while the revoke silently no-opped.
            rev = tc.post("/oauth/revoke",
                          data={"token": access,
                                "token_type_hint": "access_token"})
            assert rev.status_code == 200, rev.text
            h = _sha256(access)
            rows = cp.tables["oauth_access_tokens"]
            assert any(r.get("token_hash") == h and r.get("revoked_at")
                       for r in rows), (
                f"revoke did not land: no oauth_access_tokens row for THIS token "
                f"is marked revoked ({len(rows)} rows) — step 3 below would prove "
                f"nothing. (Matching on token_hash, not just any revoked_at, so a "
                f"revoke of some other token cannot make this pass.)")
            # 3. warm hit still authenticates — the bounded grace
            assert mcp_tc.post("/mcp", json=call).status_code == 200

    def test_revoked_oauth_token_rejected_once_the_cache_entry_expires(
            self, api_client, session_user, monkeypatch):
        """#2864 — the other half of the bound: past 60s the entry is stale, the
        token is re-introspected, and the revocation takes effect. Without this
        half the grace above would be indistinguishable from a token that is
        never revoked at all."""
        from types import SimpleNamespace

        import tortoise.mcp_auth as mcp_auth

        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        access = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                           verifier=flow["verifier"]).json()["access_token"]
        call = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}

        mcp_tc = self._mcp(cp)
        mcp_tc.headers.update(_mcp_headers(access))
        with mcp_tc:
            assert mcp_tc.post("/mcp", json=call).status_code == 200
            rev = tc.post("/oauth/revoke",
                          data={"token": access,
                                "token_type_hint": "access_token"})
            assert rev.status_code == 200, rev.text
            h = _sha256(access)
            assert any(r.get("token_hash") == h and r.get("revoked_at")
                       for r in cp.tables["oauth_access_tokens"]), (
                "revoke did not land for this token")

            # Scope the clock shift to mcp_auth only — patching the stdlib
            # `time` module itself would freeze time for every other consumer
            # in the process for the duration of the test.
            real_time = mcp_auth.time.time
            monkeypatch.setattr(
                mcp_auth, "time", SimpleNamespace(time=lambda: real_time() + 61))
            rr = mcp_tc.post("/mcp", json=call)
        assert rr.status_code == 401, (
            "a cache entry older than the 60s TTL must re-introspect and reject"
        )

    def test_bogus_oauth_token_401(self, api_client):
        tc, cp = api_client  # noqa: RUF059
        mcp_tc = self._mcp(cp)
        mcp_tc.headers.update(_mcp_headers("oat_" + "x" * 40))
        with mcp_tc:
            rr = mcp_tc.post("/mcp", json={
                "jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert rr.status_code == 401

    def test_tt_key_still_works_alongside_oauth(self, api_client):
        """D3: the tt_ fallback path is byte-identical alongside oat_."""
        from tests.test_supabase_control import TOKEN, _key_row
        tc, cp = api_client  # noqa: RUF059
        cp.seed("api_keys", [_key_row()])
        mcp_tc = self._mcp(cp)
        mcp_tc.headers.update(_mcp_headers(TOKEN))
        with mcp_tc:
            rr = mcp_tc.post("/mcp", json={
                "jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert rr.status_code == 200, rr.text
            assert "result" in _parse_sse_json(rr)

    def test_oauth_token_401_in_registry_mode(self, monkeypatch):
        """D3: OAuth is hosted-only — oat_ never authenticates on the
        registry/selfhost control plane (no Supabase creds → registry mode)."""
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        mcp_app = create_http_app(allowed_origins=[])
        mcp_tc = _mounted_test_client(mcp_app)
        mcp_tc.headers.update(_mcp_headers("oat_" + "x" * 40))
        with mcp_tc:
            rr = mcp_tc.post("/mcp", json={
                "jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert rr.status_code == 401

    def test_oauth_sessionless_create_point_actor_stamped(self, api_client,
                                                          session_user):
        """#2600 Task 3 E2E-4(a): a SESSIONLESS write through the oat_ MCP
        lane (no tt_ key minting) is attributed to the token's human — the
        middleware resolves user_id → ContextVar → ``_emit_event`` merges it
        onto the :GraphEvent node payload (the journaled-event actor
        backstop). Read via DIRECT graph query on the team namespace."""
        import json as _json
        import uuid as _uuid

        import tortoise.hosted_api as ha_mod
        tc, cp = api_client
        session_user(_U1)  # browser session subject = the OAuth user
        flow = _auth_code_flow(tc, cp)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"])
        access = r.json()["access_token"]
        assert access.startswith("oat_"), access

        # distinct content (scoping P2 — dedup=True would no-op a repeat)
        content = f"e2e4a oat attribution spike {_uuid.uuid4().hex[:10]}"
        mcp_tc = self._mcp(cp)
        mcp_tc.headers.update(_mcp_headers(access))
        with mcp_tc:
            rr = mcp_tc.post("/mcp", json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "tortoise_create_point",
                            "arguments": {"kind": "statement",
                                           "content": content}}})
            assert rr.status_code == 200, rr.text
            body = _parse_sse_json(rr)
            assert "result" in body, body
            text = "".join(c.get("text", "") for c in
                            body["result"].get("content", []))
            assert content.split()[-1] in text or content in text, text

        # the :GraphEvent PointAdded node carries the token user's actor
        sdk = ha_mod._make_sdk(namespace="team-free-001")
        rows = sdk._get_proj().g.query(
            "MATCH (e:GraphEvent {type:'PointAdded'}) "
            "RETURN e.payload ORDER BY e.seq").result_set
        payloads = [_json.loads(r[0]) for r in rows]
        hits = [p for p in payloads if p.get("content_hash")]
        assert hits, "no PointAdded GraphEvent journaled"
        newest = hits[-1]
        assert newest.get("actor_user_id") == _U1, newest


# ── #2975: the consent-page literal must parse without SyntaxWarning ─────────


def test_consent_html_literal_parses_without_syntax_warning():
    """#2975: ``_CONSENT_HTML`` embeds a JavaScript RFC-1918 regex
    (``/^172\\.(1[6-9]|2\\d|3[01])\\./``) whose backslashes are invalid Python
    escapes. Left in a non-raw literal they emit
    ``SyntaxWarning: invalid escape sequence`` on *every* parse (and become a
    ``SyntaxError`` on a future Python), polluting every test run. The literal
    must therefore stay raw — and staying raw must not alter the emitted bytes.
    """
    import warnings
    from pathlib import Path

    oauth_path = Path(__file__).resolve().parent.parent / "tortoise" / "oauth.py"
    source = oauth_path.read_text(encoding="utf-8")

    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        compile(source, str(oauth_path), "exec")

    # The raw prefix must not have re-interpreted any escape in the literal.
    from tortoise.oauth import _CONSENT_HTML

    assert r"/^172\.(1[6-9]|2\d|3[01])\./" in _CONSENT_HTML
# ═══════════════════════════════════════════════════════════════════════════
# #2866 — DCR capacity policy: bounded store, stated caps, CIDR exemption
# ═══════════════════════════════════════════════════════════════════════════
# Legs (a)–(y) of docs/plans/2026-09-11-2866-dcr-capacity-policy.md §5. Every
# knob override uses monkeypatch.setenv (function-scoped, auto-restored) so
# no bound masks another and no override leaks into a later leg. The window
# is pinned with a fake clock (monkeypatch.setattr(hosted_api.time, ...)) for
# the aging/ordering legs.

_DCR_TRUSTED = "160.79.104.11"      # inside the default 160.79.104.0/21
_DCR_TRUSTED_ALT = "160.79.104.12"  # same /21, distinct address
_DCR_UNTRUSTED = "203.0.113.7"
_DCR_CUSTOM_NET = "198.51.100.0/24"


def _dcr_reset() -> None:
    """Swap in fresh stores + a fresh lock by module-global lookup.

    The limiter reads the four stores dynamically off the module globals (no
    default-argument capture, no import-time alias), so a rebind here is
    picked up on the next call. The fresh lock also keeps the concurrency
    legs' ``asyncio.run`` loop from inheriting a lock already bound to the
    TestClient portal loop.
    """
    _ha_mod._OAUTH_DCR_BUCKETS = OrderedDict()
    _ha_mod._OAUTH_DCR_TRUSTED = OrderedDict()
    _ha_mod._OAUTH_DCR_OVERFLOW = OrderedDict()
    _ha_mod._OAUTH_DCR_ANON = OrderedDict()
    _ha_mod._OAUTH_DCR_LOCK = asyncio.Lock()


def _dcr_post(tc, ip=None, headers=None, **body_overrides):
    body = {"client_name": "dcr-leg", "redirect_uris": [REDIRECT]}
    body.update(body_overrides)
    hdrs = dict(headers or {})
    if ip is not None:
        hdrs["Fly-Client-IP"] = ip
    return tc.post("/register", json=body, headers=hdrs)


def _find_rate_limit_middleware():
    """The live generic RateLimitMiddleware instance (Starlette builds the
    middleware stack lazily on the first ASGI call)."""
    node = getattr(app, "middleware_stack", None)
    while node is not None:
        if isinstance(node, RateLimitMiddleware):
            return node
        node = getattr(node, "app", None)
    return None


class _CountingStore(OrderedDict):
    """OrderedDict that counts every iteration entry point (#2866 leg f)."""

    def __init__(self):
        super().__init__()
        self.iterations = 0

    def reset(self):
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        return super().__iter__()

    def items(self):
        self.iterations += 1
        return super().items()

    def keys(self):
        self.iterations += 1
        return super().keys()

    def values(self):
        self.iterations += 1
        return super().values()

    def __reversed__(self):
        self.iterations += 1
        return super().__reversed__()

    def copy(self):
        self.iterations += 1
        return super().copy()


class TestDcrCapacityPolicy:
    """#2866 — the DCR limiter's stated capacity policy."""

    @pytest.fixture(autouse=True)
    def _dcr_policy(self, api_client, monkeypatch):
        """Function-scoped isolation (class scope would raise ScopeMismatch,
        since it must depend on the function-scoped api_client).

        Load-bearing: Starlette builds the middleware stack lazily on the
        first ASGI call. ``TestClient(app).__enter__`` (inside api_client)
        triggers that build while RATE_LIMIT_DISABLED=1 is still set
        (tests/conftest.py:22 + this module's setdefault), so the generic
        RateLimitMiddleware is constructed disabled and STAYS disabled after
        the flag is deleted below. If an earlier test deleted the flag before
        the session's first app call, that build is unrecoverable — fail
        loudly rather than silently breaking the flood legs.
        """
        tc, _cp = api_client
        tc.get("/health")  # belt-and-suspenders rebuild check (not the mechanism)
        mw = _find_rate_limit_middleware()
        assert mw is not None, "generic RateLimitMiddleware not found on the stack"
        assert mw._disabled is True, (
            "generic RateLimitMiddleware was NOT built with RATE_LIMIT_DISABLED=1 "
            "— the DCR flood legs would trip it. The app's first ASGI call "
            "happened after some test deleted the flag.")
        monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        _dcr_reset()

    # ── (a) anonymous per-key 429 + Retry-After ─────────────────────────
    def test_a_anonymous_per_key_429(self, api_client, monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "2")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR", "1000")
        assert _dcr_post(tc, ip=_DCR_UNTRUSTED).status_code == 201
        assert _dcr_post(tc, ip=_DCR_UNTRUSTED).status_code == 201
        r = _dcr_post(tc, ip=_DCR_UNTRUSTED)
        assert r.status_code == 429, r.text
        assert int(r.headers["Retry-After"]) >= 1

    # ── (b) fresh-key flood: bounded store + overflow binds ─────────────
    def test_b_fresh_key_flood_bounded_store_and_overflow(self, api_client,
                                                          monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_STORE_CAP", "8")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "2")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        for i in range(8):
            assert _dcr_post(tc, ip=f"198.51.100.{i}").status_code == 201
        assert len(_ha_mod._OAUTH_DCR_BUCKETS) == 8
        # the store is full: new keys are served by the shared overflow bucket
        # until its derived cap (= PER_HOUR) binds.
        assert _dcr_post(tc, ip="198.51.100.200").status_code == 201
        assert _dcr_post(tc, ip="198.51.100.201").status_code == 201
        r = _dcr_post(tc, ip="198.51.100.202")
        assert r.status_code == 429, r.text
        assert "Retry-After" in r.headers
        # a genuine non-exempt new IP still 429s while the store is full
        assert _dcr_post(tc, ip="198.51.100.203").status_code == 429
        assert len(_ha_mod._OAUTH_DCR_BUCKETS) <= 8

    # ── (c) a tracked key's 429 is not reset by fresh keys ──────────────
    def test_c_tracked_key_429_not_reset_by_fresh_keys(self, api_client,
                                                       monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "1")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_STORE_CAP", "2")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        assert _dcr_post(tc, ip="192.0.2.10").status_code == 201
        assert _dcr_post(tc, ip="192.0.2.10").status_code == 429
        for i in range(6):
            _dcr_post(tc, ip=f"192.0.2.{100 + i}")
        assert _dcr_post(tc, ip="192.0.2.10").status_code == 429

    # ── (d) ordering invariant: reclaim stops at the first active head ──
    def test_d_ordering_invariant_reclaims_only_inactive_lru(self, api_client,
                                                             monkeypatch):
        tc, _ = api_client
        now = [1_800_000_000.0]
        monkeypatch.setattr(_ha_mod.time, "time", lambda: now[0])
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_STORE_CAP", "2")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "5")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        assert _dcr_post(tc, ip="192.0.2.1").status_code == 201  # A
        assert _dcr_post(tc, ip="192.0.2.2").status_code == 201  # B (store full)
        now[0] += 3500
        assert _dcr_post(tc, ip="192.0.2.1").status_code == 201  # A recharged → MRU
        now[0] += 101  # t=3601: B's only charge has aged out, A's has not
        assert _dcr_post(tc, ip="192.0.2.3").status_code == 201  # C
        buckets = _ha_mod._OAUTH_DCR_BUCKETS
        assert "192.0.2.3" in buckets, "new key must get a bucket, not overflow"
        assert "192.0.2.2" not in buckets, "inactive LRU head must be reclaimed"
        assert "192.0.2.1" in buckets, "an active key must never be evicted"

    # ── (e) aged buckets are reclaimed; a recharged key survives ────────
    def test_e_aged_buckets_reclaimed(self, api_client, monkeypatch):
        tc, _ = api_client
        now = [1_800_000_000.0]
        monkeypatch.setattr(_ha_mod.time, "time", lambda: now[0])
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_STORE_CAP", "2")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "5")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        assert _dcr_post(tc, ip="192.0.2.1").status_code == 201
        assert _dcr_post(tc, ip="192.0.2.2").status_code == 201
        now[0] += 3700
        assert _dcr_post(tc, ip="192.0.2.1").status_code == 201  # A recharged
        now[0] += 1
        assert _dcr_post(tc, ip="192.0.2.3").status_code == 201  # reclaims B
        buckets = _ha_mod._OAUTH_DCR_BUCKETS
        assert "192.0.2.2" not in buckets
        assert "192.0.2.1" in buckets, "the recharged key survives the reclaim"
        assert "192.0.2.3" in buckets
        # every remaining head is aged out: reclaim makes room again
        now[0] += 5000
        assert _dcr_post(tc, ip="192.0.2.4").status_code == 201
        buckets = _ha_mod._OAUTH_DCR_BUCKETS
        assert "192.0.2.1" not in buckets
        assert "192.0.2.4" in buckets

    # ── (f) lookups are not O(n) at store-cap scale ─────────────────────
    def test_f_lookups_not_on_at_store_cap_scale(self, api_client, monkeypatch):
        tc, _ = api_client
        now = [1_800_000_001.0]
        monkeypatch.setattr(_ha_mod.time, "time", lambda: now[0])
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "50")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        counting = _CountingStore()
        for i in range(10_001):
            counting[f"10.{i // 256}.{i % 256}.1"] = [now[0] - 1.0]
        counting["10.0.0.1"] = [now[0] - 1.0]  # tracked key
        counting.reset()
        _ha_mod._OAUTH_DCR_BUCKETS = counting
        assert _dcr_post(tc, ip="10.0.0.1").status_code == 201
        assert counting.iterations == 0, (
            "a tracked key's charge must not iterate the store "
            f"(saw {counting.iterations})")
        # a new key at an all-live cap inspects at most the LRU head
        counting2 = _CountingStore()
        for i in range(8):
            counting2[f"10.1.{i}.1"] = [now[0]]
        counting2.reset()
        _ha_mod._OAUTH_DCR_BUCKETS = counting2
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_STORE_CAP", "8")
        assert _dcr_post(tc, ip="10.9.9.9").status_code == 201
        assert counting2.iterations <= 1, (
            f"reclaim inspected {counting2.iterations} heads at an all-live cap")

    # ── (g) IPv6 /64 collapse + recharge keeps the key usable/MRU ───────
    def test_g_ipv6_64_collapse_and_recharge(self, api_client, monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "5")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        assert _dcr_post(tc, ip="2001:db8:aaaa:1::1").status_code == 201
        assert _dcr_post(tc, ip="2001:db8:aaaa:1::2").status_code == 201
        buckets = _ha_mod._OAUTH_DCR_BUCKETS
        assert list(buckets) == ["2001:db8:aaaa:1::/64"], list(buckets)
        assert len(buckets["2001:db8:aaaa:1::/64"]) == 2
        # a further charged hit from the same /64 must not KeyError and must
        # move the key to the MRU end (last-charge ordering)
        assert _dcr_post(tc, ip="2001:db8:bbbb::1").status_code == 201
        assert _dcr_post(tc, ip="2001:db8:aaaa:1::3").status_code == 201
        assert list(buckets)[-1] == "2001:db8:aaaa:1::/64"
        assert len(buckets["2001:db8:aaaa:1::/64"]) == 3

    # ── (h) IPv4-mapped IPv6 is normalized BEFORE match/keying ──────────
    def test_h_mapped_ipv6_normalized_before_match(self, api_client, monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        assert _ha_mod._oauth_dcr_store_key("::ffff:1.2.3.4") == "1.2.3.4"
        # non-canonical mapped spellings must resolve to the SAME key, or one
        # IPv4 address holds two bucket identities (its own + the shared /64)
        for spelling in ("::FFFF:1.2.3.4", "0:0:0:0:0:ffff:1.2.3.4",
                         "::ffff:0102:0304"):
            assert _ha_mod._oauth_dcr_store_key(spelling) == "1.2.3.4", spelling
            assert _ha_mod._oauth_dcr_trusted_net(spelling) is None, spelling
        assert _dcr_post(tc, ip="::ffff:160.79.104.11").status_code == 201
        assert not _ha_mod._OAUTH_DCR_BUCKETS, "trusted ⇒ no per-key bucket"
        assert len(_ha_mod._OAUTH_DCR_TRUSTED["160.79.104.0/21"]) == 1
        # ...and every spelling of a trusted address is exempt too
        for spelling in ("::FFFF:160.79.104.11", "0:0:0:0:0:ffff:160.79.104.11"):
            assert _ha_mod._oauth_dcr_trusted_net(spelling) is not None, spelling
        assert _dcr_post(tc, ip="::ffff:1.2.3.4").status_code == 201
        assert list(_ha_mod._OAUTH_DCR_BUCKETS) == ["1.2.3.4"], \
            "mapped IPv4 must key as the IPv4 address, not as ::/64"

    # ── (i) the trusted carve-out is REACHABLE ──────────────────────────
    def test_i_trusted_carve_out_reachable(self, api_client, monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_TRUSTED_PER_HOUR", "2")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "1")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        # A first-time trusted IP is an UNTRACKED key — a design that charges
        # trusted traffic to the per-key/overflow path 429s the second
        # request here (PER_HOUR=1). Discriminates by construction.
        assert _dcr_post(tc, ip=_DCR_TRUSTED).status_code == 201
        assert _dcr_post(tc, ip=_DCR_TRUSTED).status_code == 201
        assert not _ha_mod._OAUTH_DCR_BUCKETS
        assert not _ha_mod._OAUTH_DCR_ANON.get(_ha_mod._OAUTH_DCR_ANON_KEY)
        # ...then the trusted aggregate binds (one CIDR = one bucket)
        r = _dcr_post(tc, ip=_DCR_TRUSTED_ALT)
        assert r.status_code == 429, r.text
        assert "Retry-After" in r.headers

    # ── (j) flag unset ⇒ a spoofed Fly-Client-IP is NOT exempt ──────────
    def test_j_flag_unset_spoofed_header_not_exempt(self, api_client,
                                                    monkeypatch):
        tc, _ = api_client
        monkeypatch.delenv("TORTOISE_TRUST_FLY_CLIENT_IP", raising=False)
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        assert _dcr_post(tc, ip=_DCR_TRUSTED).status_code == 201
        assert not _ha_mod._OAUTH_DCR_TRUSTED
        # ClientIPMiddleware fell back to request.client.host ("testclient")
        assert _ha_mod._OAUTH_DCR_MALFORMED_KEY in _ha_mod._OAUTH_DCR_BUCKETS

    # ── (k) flag set + no Fly header ⇒ a spoofed XFF is NOT exempt ──────
    def test_k_flag_set_xff_not_trusted(self, api_client, monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        # XFF is set to an address INSIDE the trusted range, so an impl that
        # wrongly read XFF would exempt it.
        assert _dcr_post(tc, headers={"X-Forwarded-For": _DCR_TRUSTED}
                         ).status_code == 201
        assert not _ha_mod._OAUTH_DCR_TRUSTED
        assert _ha_mod._OAUTH_DCR_MALFORMED_KEY in _ha_mod._OAUTH_DCR_BUCKETS

    # ── (l) the anonymous aggregate binds across distinct keys ──────────
    def test_l_anonymous_aggregate_binds_across_keys(self, api_client,
                                                    monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR", "3")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "10")
        for i in range(3):
            assert _dcr_post(tc, ip=f"203.0.113.{i}").status_code == 201
        r = _dcr_post(tc, ip="203.0.113.99")
        assert r.status_code == 429, r.text
        assert len(_ha_mod._OAUTH_DCR_ANON[_ha_mod._OAUTH_DCR_ANON_KEY]) == 3
        assert "203.0.113.99" not in _ha_mod._OAUTH_DCR_BUCKETS

    # ── (m) atomicity both directions + overflow charges the aggregate ──
    def test_m_atomicity_and_overflow_charges_aggregate(self, api_client,
                                                        monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "10")
        # direction 1: a 429 charges nothing and inserts nothing
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR", "1")
        assert _dcr_post(tc, ip="203.0.113.1").status_code == 201
        assert _dcr_post(tc, ip="203.0.113.2").status_code == 429
        assert "203.0.113.2" not in _ha_mod._OAUTH_DCR_BUCKETS
        assert len(_ha_mod._OAUTH_DCR_ANON[_ha_mod._OAUTH_DCR_ANON_KEY]) == 1
        # direction 2 (D9): an overflow charge hits overflow AND the aggregate
        _dcr_reset()
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_STORE_CAP", "0")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "1")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR", "50")
        assert _dcr_post(tc, ip="203.0.113.3").status_code == 201
        assert not _ha_mod._OAUTH_DCR_BUCKETS
        assert len(_ha_mod._OAUTH_DCR_OVERFLOW[
            _ha_mod._OAUTH_DCR_OVERFLOW_KEY]) == 1
        assert len(_ha_mod._OAUTH_DCR_ANON[_ha_mod._OAUTH_DCR_ANON_KEY]) == 1

    # ── (n) malformed inputs fail closed, never 500 ─────────────────────
    def test_n_malformed_inputs_fail_closed(self, api_client, monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        # malformed client IP → the single constant key, no 500
        assert _dcr_post(tc, ip="not-an-ip").status_code == 201
        assert _ha_mod._OAUTH_DCR_MALFORMED_KEY in _ha_mod._OAUTH_DCR_BUCKETS
        # a mixed CIDR list keeps the valid entry and skips the malformed one
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_TRUSTED_CIDRS",
                           "not-a-cidr, 160.79.104.0/21 ,, also-bad")
        assert _dcr_post(tc, ip=_DCR_TRUSTED).status_code == 201
        assert list(_ha_mod._OAUTH_DCR_TRUSTED) == ["160.79.104.0/21"]
        # out-of-range prefixes fall back to 64 — never clamp to 0/1 (which
        # would collapse EVERY IPv6 address into one bucket)
        for bad in ("0", "129", "-1", "abc", "64x"):
            _dcr_reset()
            monkeypatch.setenv("TORTOISE_OAUTH_DCR_IPV6_PREFIX", bad)
            assert _dcr_post(tc, ip="2001:db8:aaaa:1::1").status_code == 201
            assert _dcr_post(tc, ip="2001:db8:aaaa:2::1").status_code == 201
            keys = list(_ha_mod._OAUTH_DCR_BUCKETS)
            assert len(keys) == 2, (bad, keys)

    def test_n2_missing_client_early_returns(self, api_client):
        """request.client is None ⇒ early-return like the shared primitive:
        no AttributeError/500, no charge, no bucket (#2866 D6)."""
        _dcr_reset()
        req = Request({"type": "http", "method": "POST", "path": "/register",
                       "headers": [], "query_string": b""})
        asyncio.run(_ha_mod._check_oauth_dcr_rate_limit(req))
        assert not _ha_mod._OAUTH_DCR_BUCKETS
        assert not _ha_mod._OAUTH_DCR_ANON
        assert not _ha_mod._OAUTH_DCR_TRUSTED

    # ── (o1) the zero rule denies the FIRST request, per dimension ──────
    def test_o1_zero_rate_knobs_deny_first_request(self, api_client,
                                                   monkeypatch):
        tc, _ = api_client
        # per-key dimension
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "0")
        r = _dcr_post(tc, ip="203.0.113.1")
        assert r.status_code == 429, r.text
        assert r.headers["Retry-After"] == "3600"
        assert not _ha_mod._OAUTH_DCR_BUCKETS
        # anonymous-aggregate dimension (per-key must pass first)
        _dcr_reset()
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "10")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR", "0")
        r = _dcr_post(tc, ip="203.0.113.2")
        assert r.status_code == 429, r.text
        assert r.headers["Retry-After"] == "3600"
        assert not _ha_mod._OAUTH_DCR_BUCKETS, "a 429 must not insert a bucket"
        assert not _ha_mod._OAUTH_DCR_ANON.get(_ha_mod._OAUTH_DCR_ANON_KEY)
        # trusted-aggregate dimension
        _dcr_reset()
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_TRUSTED_PER_HOUR", "0")
        r = _dcr_post(tc, ip=_DCR_TRUSTED)
        assert r.status_code == 429, r.text
        assert r.headers["Retry-After"] == "3600"
        assert not _ha_mod._OAUTH_DCR_TRUSTED

    # ── (o2) PER_HOUR=1 → 201 then 429 ─────────────────────────────────
    def test_o2_per_hour_one(self, api_client, monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "1")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR", "1000")
        assert _dcr_post(tc, ip="203.0.113.5").status_code == 201
        assert _dcr_post(tc, ip="203.0.113.5").status_code == 429
        # (m)(ii): a PER-KEY 429 must leave the anonymous aggregate uncharged —
        # otherwise one client hammering its own bucket burns the global
        # budget for everybody (phase 2 is unreachable on a denied request).
        assert len(_ha_mod._OAUTH_DCR_ANON[
            _ha_mod._OAUTH_DCR_ANON_KEY]) == 1

    # ── (o3) STORE_CAP=0 → served by overflow, then its cap binds ───────
    def test_o3_store_cap_zero_uses_overflow(self, api_client, monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_STORE_CAP", "0")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        assert _dcr_post(tc, ip="203.0.113.1").status_code == 201
        assert not _ha_mod._OAUTH_DCR_BUCKETS
        assert len(_ha_mod._OAUTH_DCR_OVERFLOW[
            _ha_mod._OAUTH_DCR_OVERFLOW_KEY]) == 1
        for i in range(2, 21):  # overflow charges 2..20 (= derived cap)
            assert _dcr_post(tc, ip=f"203.0.113.{i}").status_code == 201
        r = _dcr_post(tc, ip="203.0.113.21")
        assert r.status_code == 429, r.text
        assert not _ha_mod._OAUTH_DCR_BUCKETS

    # ── (p) scope vectors + AS metadata + offline_access round trip ─────
    def test_p_dcr_scope_vectors(self, api_client, session_user, monkeypatch):
        tc, _ = api_client
        assert SCOPES_SUPPORTED == ["mcp"]
        assert set(SCOPES_SUPPORTED) <= set(SCOPES_ACCEPTED)
        assert "offline_access" in SCOPES_ACCEPTED
        # AS metadata advertises the ACCEPTED superset; the PRM document keeps
        # the client-facing default set.
        as_meta = tc.get("/.well-known/oauth-authorization-server").json()
        assert as_meta["scopes_supported"] == SCOPES_ACCEPTED
        prm = tc.get("/.well-known/oauth-protected-resource").json()
        assert prm["scopes_supported"] == SCOPES_SUPPORTED
        for scope in (None, "mcp", "mcp offline_access", "offline_access"):
            body = {"client_name": "scope-vector", "redirect_uris": [REDIRECT]}
            if scope is not None:
                body["scope"] = scope
            r = tc.post("/register", json=body)
            assert r.status_code == 201, (scope, r.text)
        r = tc.post("/register", json={"client_name": "bad",
                                        "redirect_uris": [REDIRECT],
                                        "scope": "admin"})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_client_metadata"
        assert "offline_access" in r.json()["error_description"]
        # the offline_access round trip mints a refresh token that works
        session_user(_U1)
        reg = tc.post("/register", json={
            "client_name": "offline",
            "redirect_uris": [REDIRECT],
            "scope": "mcp offline_access"}).json()
        verifier, challenge = _pkce()
        r = tc.post("/oauth/consent", json={
            "client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "st-1",
            "scope": "mcp offline_access", "resource": None},
            headers={"Authorization": "Bearer fake-session-jwt"})
        assert r.status_code == 200, r.text
        tok = _exchange(tc, client_id=reg["client_id"], code=r.json()["code"],
                        verifier=verifier)
        assert tok.status_code == 200, tok.text
        assert tok.json()["refresh_token"].startswith("ort_")
        rr = tc.post("/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": tok.json()["refresh_token"],
            "client_id": reg["client_id"]})
        assert rr.status_code == 200, rr.text

    # ── (q) RATE_LIMIT_DISABLED opts out ────────────────────────────────
    def test_q_rate_limit_disabled_is_a_no_op(self, api_client, monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "0")
        assert _dcr_post(tc, ip="203.0.113.1").status_code == 201
        assert not _ha_mod._OAUTH_DCR_BUCKETS
        assert not _ha_mod._OAUTH_DCR_ANON

    # ── (r) default constants pin the stated policy ─────────────────────
    def test_r_defaults_pin_the_stated_policy(self):
        assert _ha_mod._OAUTH_DCR_PER_HOUR_DEFAULT == 20
        assert _ha_mod._OAUTH_DCR_ANON_AGGREGATE_PER_HOUR_DEFAULT == 600
        assert _ha_mod._OAUTH_DCR_TRUSTED_PER_HOUR_DEFAULT == 1200
        assert _ha_mod._OAUTH_DCR_STORE_CAP_DEFAULT == 256
        assert _ha_mod._OAUTH_DCR_IPV6_PREFIX_DEFAULT == 64
        assert _ha_mod._OAUTH_DCR_WINDOW_S == 3600
        assert _ha_mod._OAUTH_DCR_TRUSTED_CIDRS_DEFAULT == "160.79.104.0/21"
        # the cap must stay below the anonymous aggregate or the
        # reject-new/overflow branch is dead at shipped defaults
        assert (_ha_mod._OAUTH_DCR_STORE_CAP_DEFAULT
                < _ha_mod._OAUTH_DCR_ANON_AGGREGATE_PER_HOUR_DEFAULT)
        # derived distinct-new-anonymous-key ceiling (STORE_CAP + PER_HOUR)
        assert (_ha_mod._OAUTH_DCR_STORE_CAP_DEFAULT
                + _ha_mod._OAUTH_DCR_PER_HOUR_DEFAULT) == 276
        # unset env yields exactly the stated defaults (call-time reads)
        for name, attr in (
            ("TORTOISE_OAUTH_DCR_PER_HOUR", "_OAUTH_DCR_PER_HOUR_DEFAULT"),
            ("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
             "_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR_DEFAULT"),
            ("TORTOISE_OAUTH_DCR_TRUSTED_PER_HOUR",
             "_OAUTH_DCR_TRUSTED_PER_HOUR_DEFAULT"),
            ("TORTOISE_OAUTH_DCR_STORE_CAP", "_OAUTH_DCR_STORE_CAP_DEFAULT"),
        ):
            assert os.environ.get(name) is None, f"{name} leaked into this test"
            assert getattr(_ha_mod, attr) == int(
                _ha_mod._int_env(name, getattr(_ha_mod, attr)))

    # ── (s) registry-mode 503 precedes the limiter (no charge) ──────────
    def test_s_registry_503_precedes_limiter(self, api_client, monkeypatch):
        _dcr_reset()
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        with tempfile.TemporaryDirectory() as tmpdir, \
                patched_tortoise_sdk(os.path.join(tmpdir, "r.db")), \
                TestClient(app) as tc:
            r = tc.post("/register", json={"client_name": "x",
                                            "redirect_uris": [REDIRECT]})
            assert r.status_code == 503
        assert not _ha_mod._OAUTH_DCR_BUCKETS
        assert not _ha_mod._OAUTH_DCR_ANON

    # ── (t) parity: the DCR window helpers vs the shared primitive ──────
    def test_t_window_helper_parity_with_primitive(self, monkeypatch):
        now = 1_800_000_000.0
        monkeypatch.setattr(_ha_mod.time, "time", lambda: now)
        rows = (
            [0.0],                       # just charged
            [3600.0],                    # exactly at the boundary → pruned
            [3599.5],                    # just inside
            [0.0, 3599.75],              # two in-window entries
            [3600.0, 3700.0],            # everything pruned
            [3599.6666667, 100.0],       # non-integer remainder
            [0.0, 1800.0, 3599.9],       # three in-window entries
        )
        for row in rows:
            store = {"9.9.9.9": [now - age for age in row]}
            req = Request({"type": "http", "method": "POST", "path": "/x",
                           "headers": [], "query_string": b"",
                           "client": ("9.9.9.9", 1234)})
            pruned = _ha_mod._dcr_prune_window(list(store["9.9.9.9"]), now,
                                               _ha_mod._OAUTH_DCR_WINDOW_S)
            try:
                asyncio.run(_ha_mod._check_ip_bucket_rate_limit(
                    req, buckets=store, lock=asyncio.Lock(), limit=1,
                    window_s=_ha_mod._OAUTH_DCR_WINDOW_S, detail="parity",
                    retry_after_s=None, defer_charge=True))
                denied = False
            except HTTPException as exc:
                denied = True
                assert exc.status_code == 429
                assert exc.headers["Retry-After"] == str(
                    _ha_mod._dcr_retry_after_s(pruned, now,
                                               _ha_mod._OAUTH_DCR_WINDOW_S))
            assert denied == bool(pruned), (row, denied, pruned)
            assert store["9.9.9.9"] == pruned, (row, store["9.9.9.9"], pruned)

    # ── (u) concurrency: atomicity under interleaving ───────────────────
    @staticmethod
    def _burst(ips):
        async def _run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as ac:
                return await asyncio.gather(*[
                    ac.post("/register",
                            json={"client_name": "burst",
                                  "redirect_uris": [REDIRECT]},
                            headers={"Fly-Client-IP": ip})
                    for ip in ips])
        return asyncio.run(_run())

    def test_u1_concurrent_same_key_single_winner(self, api_client, monkeypatch):
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "1")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        results = self._burst(["203.0.113.77"] * 6)
        codes = sorted(r.status_code for r in results)
        assert codes == [201] + [429] * 5, codes
        assert len(_ha_mod._OAUTH_DCR_BUCKETS["203.0.113.77"]) == 1

    def test_u2_concurrent_distinct_keys_respect_cap(self, api_client,
                                                     monkeypatch):
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_STORE_CAP", "1")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "20")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        results = self._burst([f"203.0.113.{i}" for i in range(1, 5)])
        assert all(r.status_code == 201 for r in results), [
            r.text for r in results]
        assert len(_ha_mod._OAUTH_DCR_BUCKETS) <= 1

    # ── (v) a repeat charge on a tracked key consumes the aggregate ─────
    def test_v_repeat_charge_consumes_aggregate(self, api_client, monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR", "3")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "10")
        for _ in range(3):
            assert _dcr_post(tc, ip="203.0.113.9").status_code == 201
        assert _dcr_post(tc, ip="203.0.113.9").status_code == 429
        assert len(_ha_mod._OAUTH_DCR_ANON[_ha_mod._OAUTH_DCR_ANON_KEY]) == 3

    # ── (w) trusted traffic does not consume the anonymous aggregate ────
    def test_w_trusted_does_not_consume_anonymous_aggregate(self, api_client,
                                                            monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR", "1")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_TRUSTED_PER_HOUR", "100")
        assert _dcr_post(tc, ip=_DCR_TRUSTED).status_code == 201
        assert not _ha_mod._OAUTH_DCR_ANON.get(_ha_mod._OAUTH_DCR_ANON_KEY)
        assert _dcr_post(tc, ip="203.0.113.4").status_code == 201

    # ── (x) TRUSTED_CIDRS is a live knob (no cache, no falsy-coalesce) ──
    def test_x_trusted_cidrs_is_a_live_knob(self, api_client, monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_PER_HOUR", "1")
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_TRUSTED_CIDRS", _DCR_CUSTOM_NET)
        # an address inside the DEFAULT range is no longer exempt (so an
        # `or DEFAULT` read would fail this assertion)
        assert _dcr_post(tc, ip=_DCR_TRUSTED).status_code == 201
        assert _DCR_TRUSTED in _ha_mod._OAUTH_DCR_BUCKETS
        assert not _ha_mod._OAUTH_DCR_TRUSTED
        assert _dcr_post(tc, ip=_DCR_TRUSTED).status_code == 429
        # an address in the CUSTOM range is exempt
        assert _dcr_post(tc, ip="198.51.100.5").status_code == 201
        assert _DCR_CUSTOM_NET in _ha_mod._OAUTH_DCR_TRUSTED
        # an EMPTY value disables the exemption (the fail-closed lever)
        _dcr_reset()
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_TRUSTED_CIDRS", "")
        assert _dcr_post(tc, ip=_DCR_TRUSTED).status_code == 201
        assert _DCR_TRUSTED in _ha_mod._OAUTH_DCR_BUCKETS
        assert not _ha_mod._OAUTH_DCR_TRUSTED

    # ── (y) IPV6_PREFIX is a live knob ──────────────────────────────────
    def test_y_ipv6_prefix_is_a_live_knob(self, api_client, monkeypatch):
        tc, _ = api_client
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_ANON_AGGREGATE_PER_HOUR",
                           "1000000")
        a, b = "2001:db8:aaaa:1::1", "2001:db8:aaaa:2::1"
        # shipped default /64 → the pair is distinct
        assert _dcr_post(tc, ip=a).status_code == 201
        assert _dcr_post(tc, ip=b).status_code == 201
        assert len(_ha_mod._OAUTH_DCR_BUCKETS) == 2
        # /48 → the pair collapses (a difference in the 3rd hextet would
        # collapse under neither prefix, which is why the 4th is used)
        _dcr_reset()
        monkeypatch.setenv("TORTOISE_OAUTH_DCR_IPV6_PREFIX", "48")
        assert _dcr_post(tc, ip=a).status_code == 201
        assert _dcr_post(tc, ip=b).status_code == 201
        keys = list(_ha_mod._OAUTH_DCR_BUCKETS)
        assert keys == ["2001:db8:aaaa::/48"], keys
        assert len(_ha_mod._OAUTH_DCR_BUCKETS[keys[0]]) == 2


# ═══════════════════════════════════════════════════════════════════════════
# #2847 — CIMD: a client obtains an identity WITHOUT POST /register
#
# The SSRF control set behind the fetch is covered by tests/test_cimd_ssrf.py.
# This class is the *integration* half of the issue's indicator: the AS metadata
# advertises a non-DCR client-identity path, and the full consent → code → token
# flow completes with every registration entry point sabotaged.
# ═══════════════════════════════════════════════════════════════════════════

CIMD_CLIENT_ID = "https://claude.ai/.well-known/oauth-client-metadata"


def _cimd_document(**overrides) -> dict:
    doc = {
        "client_id": CIMD_CLIENT_ID,
        # Deliberately self-asserted nonsense: the AS must display the HOST.
        "client_name": "Totally Not Claude",
        "redirect_uris": [REDIRECT],          # loopback → same-origin exempt
    }
    doc.update(overrides)
    return doc


@pytest.fixture
def cimd_document(monkeypatch):
    """Serve the CIMD document from memory; the fetch itself is out of scope
    here (see tests/test_cimd_ssrf.py)."""
    from tortoise import cimd

    cimd._cache_reset()
    cimd._rate_limit_reset()
    doc = _cimd_document()
    monkeypatch.setattr(cimd, "fetch_client_metadata", lambda _client_id: doc)
    yield doc
    cimd._cache_reset()
    cimd._rate_limit_reset()


@pytest.fixture
def register_forbidden(monkeypatch):
    """The indicator, enforced: any registration call is a hard failure.

    The DCR stores are reset here because ``conftest._reset_ip_rate_limits``
    does NOT touch ``_OAUTH_DCR_BUCKETS`` (only the in-file ``_dcr_reset``
    does) — without this, the "no DCR charge" assertion below would depend on
    pytest's test order.
    """
    def _boom(*_a, **_k):
        raise AssertionError("POST /register (DCR) must not be reached")

    monkeypatch.setattr("tortoise.oauth.register_client", _boom)
    monkeypatch.setattr("tortoise.hosted_api._check_oauth_dcr_rate_limit", _boom)
    _dcr_reset()
    return _boom


class TestCimdClientIdentity:
    def test_metadata_advertises_a_non_dcr_path(self, api_client):
        """Both values, in one place: Claude selects CIMD only when the flag AND
        `"none"` are present, otherwise it falls back to DCR."""
        tc, _ = api_client
        body = tc.get("/.well-known/oauth-authorization-server").json()
        assert body["client_id_metadata_document_supported"] is True
        assert "none" in body["token_endpoint_auth_methods_supported"]

    def test_metadata_flag_follows_the_env_lever(self, api_client, monkeypatch):
        monkeypatch.setenv("TORTOISE_OAUTH_CIMD", "0")
        tc, _ = api_client
        body = tc.get("/.well-known/oauth-authorization-server").json()
        assert body["client_id_metadata_document_supported"] is False

    def test_identity_without_register(self, api_client, session_user,
                                      cimd_document, register_forbidden):
        """consent → code → token, with the registry path unreachable and no DCR
        budget consumed."""
        tc, cp = api_client
        session_user(_U1)
        verifier, challenge = _pkce()
        r = _consent(tc, client_id=CIMD_CLIENT_ID, redirect_uri=REDIRECT,
                     challenge=challenge)
        assert r.status_code == 200, r.text
        tok = _exchange(tc, client_id=CIMD_CLIENT_ID, code=r.json()["code"],
                        verifier=verifier)
        assert tok.status_code == 200, tok.text
        assert tok.json()["access_token"].startswith(ACCESS_TOKEN_PREFIX)
        assert not _ha_mod._OAUTH_DCR_BUCKETS, "no DCR charge may be incurred"
        # The FK on oauth_codes/oauth_access_tokens requires a client row.
        rows = cp.tables["oauth_clients"]
        assert [row["id"] for row in rows] == [CIMD_CLIENT_ID]

    def test_provisioned_row_is_the_host_and_is_deduplicated(
            self, api_client, session_user, cimd_document, register_forbidden):
        """Growth bound: ONE row per distinct client_id URL — O(client
        implementations), not DCR's O(connections)."""
        tc, cp = api_client
        session_user(_U1)
        for _ in range(3):
            verifier, challenge = _pkce()
            r = _consent(tc, client_id=CIMD_CLIENT_ID, redirect_uri=REDIRECT,
                         challenge=challenge)
            assert r.status_code == 200, r.text
            assert _exchange(tc, client_id=CIMD_CLIENT_ID,
                             code=r.json()["code"],
                             verifier=verifier).status_code == 200
        rows = cp.tables["oauth_clients"]
        assert len(rows) == 1, "three connections must not mint three clients"
        assert rows[0]["client_name"] == "claude.ai", (
            "the consent screen must show the client_id HOST, never the "
            "document's self-asserted client_name")
        assert rows[0]["token_endpoint_auth_method"] == "none"
        assert rows[0]["client_secret_hash"] is None

    def test_consent_page_shows_the_host_not_the_document_name(
            self, api_client, cimd_document):
        tc, _ = api_client
        verifier, challenge = _pkce()  # noqa: RUF059
        r = tc.get("/oauth/authorize", params={
            "client_id": CIMD_CLIENT_ID, "redirect_uri": REDIRECT,
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "st-1",
            "scope": "mcp", "resource": ""})
        assert r.status_code == 200, r.text
        assert "claude.ai" in r.text
        assert "Totally Not Claude" not in r.text

    def test_unresolvable_document_is_an_oauth_error_not_a_5xx(
            self, api_client, monkeypatch):
        from tortoise import cimd

        def _refuse(_client_id):
            raise cimd.CimdError("refused")

        monkeypatch.setattr(cimd, "fetch_client_metadata", _refuse)
        tc, _ = api_client
        verifier, challenge = _pkce()  # noqa: RUF059
        r = tc.get("/oauth/authorize", params={
            "client_id": CIMD_CLIENT_ID, "redirect_uri": REDIRECT,
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "st-1",
            "scope": "mcp", "resource": ""})
        assert r.status_code == 400, r.text
        assert r.json()["error"] == "invalid_request"

    def test_disabled_cimd_falls_back_to_unknown_client(
            self, api_client, monkeypatch, cimd_document):
        monkeypatch.setenv("TORTOISE_OAUTH_CIMD", "0")
        tc, _ = api_client
        verifier, challenge = _pkce()  # noqa: RUF059
        r = tc.get("/oauth/authorize", params={
            "client_id": CIMD_CLIENT_ID, "redirect_uri": REDIRECT,
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "st-1",
            "scope": "mcp", "resource": ""})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_request"

    def test_registry_client_still_wins_over_cimd(
            self, api_client, session_user, cimd_document):
        """A DCR/operator-issued row must be untouched by the CIMD path."""
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        assert flow["client_id"].startswith("ct_")
        assert _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                         verifier=flow["verifier"]).status_code == 200
        assert all(row["id"].startswith("ct_")
                   for row in cp.tables["oauth_clients"])

    def test_revoked_cimd_client_is_refused(self, api_client, session_user,
                                           cimd_document):
        """#2847 review P1 (revocation fail-open).

        `_persist_cimd_client`'s duplicate re-read uses the RAW `_client_row`,
        so a revoked CIMD client came back non-None while the registry path
        returned None — authorizing a revoked integration and minting
        `oauth_codes`. The guard belongs in `resolve_client`, on the one
        resolver both paths share.
        """
        tc, cp = api_client
        session_user(_U1)
        cp.tables.setdefault("oauth_clients", []).append({
            "id": CIMD_CLIENT_ID, "client_secret_hash": None,
            "client_name": "claude.ai", "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none", "scope": "mcp",
            "created_at": "2026-01-01T00:00:00+00:00",
            "revoked_at": "2026-01-02T00:00:00+00:00"})
        verifier, challenge = _pkce()  # noqa: RUF059
        r = tc.get("/oauth/authorize", params={
            "client_id": CIMD_CLIENT_ID, "redirect_uri": REDIRECT,
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "st-1",
            "scope": "mcp", "resource": ""})
        assert r.status_code == 400, r.text
        assert r.json()["error"] == "invalid_request"
        consent = _consent(tc, client_id=CIMD_CLIENT_ID,
                           redirect_uri=REDIRECT, challenge=challenge)
        assert consent.status_code == 400, consent.text
        assert not cp.tables.get("oauth_codes"), "no code may be minted"

    def test_provisioning_write_failure_is_not_a_5xx(self, api_client,
                                                     cimd_document,
                                                     monkeypatch):
        """#2847 review P2 — the provisioning insert sits INSIDE
        `resolve_client`'s guard, so a control-plane write failure is an
        unknown-client 400, never a 500 (the fetch is attacker-reachable, so
        its failures must not become an availability oracle).

        Without the guard this raises out of `/oauth/authorize` as a 500.
        """
        tc, cp = api_client
        original = cp.query

        def _fail_post(table, **kwargs):
            if table == "oauth_clients" and kwargs.get("method") == "POST":
                raise RuntimeError("control plane 500")
            return original(table, **kwargs)

        monkeypatch.setattr(cp, "query", _fail_post)
        verifier, challenge = _pkce()  # noqa: RUF059
        r = tc.get("/oauth/authorize", params={
            "client_id": CIMD_CLIENT_ID, "redirect_uri": REDIRECT,
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "st-1",
            "scope": "mcp", "resource": ""})
        assert r.status_code == 400, r.text
        assert r.status_code < 500
        assert r.json()["error"] == "invalid_request"


# ═══════════════════════════════════════════════════════════════════════════
# #3669 — the CIMD fetch must not occupy the event loop, and its bounds must
#          count ALL FOUR unauthenticated front doors
# ═══════════════════════════════════════════════════════════════════════════
#
# `resolve_client` is the one resolver shared by /oauth/authorize,
# /oauth/consent, and BOTH /oauth/token grants (via `_verify_client_auth`).
# `resolve_client_metadata` is the one place every door passes through, so the
# in-flight cap + wall-clock budget charged there are the occupancy accounting
# for all four. These tests are the #3669 falsifiers.


def _authorize_params(challenge: str) -> dict:
    return {
        "client_id": CIMD_CLIENT_ID, "redirect_uri": REDIRECT,
        "response_type": "code", "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "st-1",
        "scope": "mcp", "resource": "",
    }


@pytest.fixture(autouse=True)
def _clean_cimd_stores():
    """The CIMD limiter/cache/budget are process-wide module state; reset per
    test so ordering cannot matter (same pattern as test_cimd_ssrf)."""
    from tortoise import cimd
    cimd._rate_limit_reset()
    cimd._cache_reset()
    yield
    cimd._rate_limit_reset()
    cimd._cache_reset()


class TestCimdOccupancy3669:
    def test_cimd_fetch_runs_on_the_dedicated_oauth_worker(
            self, api_client, monkeypatch):
        """FALSIFIER for the offload: the CIMD fetch must run on the dedicated
        ``oauth`` pool, not on the caller's (event-loop) thread.

        Before #3669 `validate_authorize_params` ran in the coroutine, so the
        recorded thread was the TestClient portal thread. Now the RESOLUTION is
        offloaded and every fetch is recorded from a `tortoise-oauth-*` worker.
        """
        from tortoise import cimd
        from tortoise.monitoring import CONTROL_PLANE_OAUTH_WORKER_NAME

        tc, _cp = api_client
        threads: list[str] = []

        def _refuse(_client_id):
            threads.append(threading.current_thread().name)
            raise cimd.CimdError("refused")

        monkeypatch.setattr(cimd, "fetch_client_metadata", _refuse)
        verifier, challenge = _pkce()  # noqa: RUF059
        r = tc.get("/oauth/authorize", params=_authorize_params(challenge))
        assert r.status_code == 400, r.text
        assert threads, "the CIMD fetch was never attempted"
        assert all(t.startswith(CONTROL_PLANE_OAUTH_WORKER_NAME) for t in threads), (
            f"the CIMD fetch ran on {threads} — the OAuth resolution must be "
            "offloaded to the dedicated oauth pool (#3669)"
        )

    def test_cimd_fetch_does_not_occupy_the_event_loop(self, monkeypatch):
        """BEHAVIOURAL OCCUPIED-LOOP PROOF (fails without the fix).

        A heartbeat coroutine ticks every 5 ms on the SAME event loop while a
        CIMD fetch is parked for 0.4 s. With the fetch on the loop the beat
        count stalls (~0 ticks); with the offload the loop keeps beating. This
        drives the REAL ASGI app through an async transport, so it does not
        depend on a thread NAME (the mutation is reverting the offload).
        """
        from tortoise import cimd

        cp = FakeControlPlane({"organizations": [], "oauth_clients": []})
        monkeypatch.setattr(_ha_mod, "_oauth_control_plane", lambda: (cp, True))

        park_s = 2.0
        threads: list[str] = []

        def _parked_fetch(_client_id):
            threads.append(threading.current_thread().name)
            time.sleep(park_s)
            raise cimd.CimdError("refused")

        monkeypatch.setattr(cimd, "fetch_client_metadata", _parked_fetch)
        verifier, challenge = _pkce()  # noqa: RUF059
        params = _authorize_params(challenge)

        async def _run() -> tuple[int, float]:
            loop = asyncio.get_running_loop()
            gaps: list[float] = []
            last = loop.time()

            async def _heartbeat():
                nonlocal last
                while True:
                    now = loop.time()
                    gaps.append(now - last)
                    last = now
                    await asyncio.sleep(0.005)

            hb = asyncio.ensure_future(_heartbeat())
            await asyncio.sleep(0.05)          # let the heartbeat settle
            transport = httpx.ASGITransport(app=_ha_mod.app)
            async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver") as client:
                r = await client.get("/oauth/authorize", params=params)
            hb.cancel()
            with __import__("contextlib").suppress(asyncio.CancelledError):
                await hb
            return r.status_code, max(gaps)

        status, max_gap = asyncio.run(_run())
        assert status == 400
        # Deterministic half: the fetch ran on a worker, not the loop thread.
        assert threads and not threads[0].startswith("MainThread"), (
            f"the CIMD fetch ran on {threads} — the event-loop thread")
        # Behavioural half: the loop was never stalled for anywhere near the
        # park. A mutation that reverts the offload stalls it for the full
        # `park_s`, so the threshold on HALF the park cleanly separates the two
        # while tolerating the heaviest scheduler blips on a loaded box.
        assert max_gap < park_s / 2, (
            f"the event loop stalled {max_gap:.3f}s while a {park_s}s CIMD fetch "
            "was in flight — the fetch is occupying the loop (#3669)")

    @pytest.mark.parametrize("door", [
        "authorize", "consent", "token_code", "token_refresh"])
    def test_every_front_door_charges_the_shared_fetch_bound(
            self, api_client, monkeypatch, door):
        """All FOUR unauthenticated front doors reach the shared, bounded
        resolver — so an occupancy bound charged in `resolve_client_metadata`
        counts every door, and no door can escape it."""
        from tortoise import cimd

        tc, _cp = api_client
        calls: list[str] = []

        def _refuse(client_id):
            calls.append(client_id)
            raise cimd.CimdError("refused")

        monkeypatch.setattr(cimd, "fetch_client_metadata", _refuse)
        verifier, challenge = _pkce()
        if door == "authorize":
            r = tc.get("/oauth/authorize", params=_authorize_params(challenge))
        elif door == "consent":
            r = _consent(tc, client_id=CIMD_CLIENT_ID, redirect_uri=REDIRECT,
                         challenge=challenge)
        elif door == "token_code":
            r = tc.post("/oauth/token", data={
                "grant_type": "authorization_code", "code": "bogus",
                "redirect_uri": REDIRECT, "client_id": CIMD_CLIENT_ID,
                "code_verifier": verifier})
        else:
            r = tc.post("/oauth/token", data={
                "grant_type": "refresh_token", "refresh_token": "bogus",
                "client_id": CIMD_CLIENT_ID})
        assert r.status_code in (400, 401), f"{door}: {r.status_code} {r.text}"
        assert len(calls) == 1, (
            f"door {door!r} attempted {len(calls)} CIMD fetches — every door "
            "must reach the shared fetch accounting exactly once")

    def test_failing_authorize_resolution_fetches_exactly_once(
            self, api_client, monkeypatch):
        """#3669 finding 2 — a FAILING /oauth/authorize resolution used to
        re-resolve in the error handler (a second CIMD fetch + rate-limit
        charge), halving the effective failure budget. The resolved client is
        now stamped on the raised OAuthError.

        Without the fix this records two fetch attempts; with it, exactly one.
        """
        from tortoise import cimd

        tc, _cp = api_client
        calls: list[str] = []

        def _refuse(client_id):
            calls.append(client_id)
            raise cimd.CimdError("refused")

        monkeypatch.setattr(cimd, "fetch_client_metadata", _refuse)
        verifier, challenge = _pkce()  # noqa: RUF059
        r = tc.get("/oauth/authorize", params=_authorize_params(challenge))
        assert r.status_code == 400, r.text
        assert r.json()["error"] == "invalid_request"
        assert len(calls) == 1, (
            f"a failing authorize resolution cost {len(calls)} fetch attempts "
            "— the error handler must not re-resolve (#3669 finding 2)")

