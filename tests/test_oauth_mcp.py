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

import base64
import hashlib
import os
import secrets
import tempfile
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

from tortoise.hosted_api import app, verify_session_jwt  # noqa: E402, F401, I001, RUF100
from tortoise.mcp_server import create_http_app  # noqa: E402, RUF100
from tortoise.oauth import (  # noqa: E402, RUF100
    ACCESS_TOKEN_PREFIX,
    _sha256,
    mcp_resource_url,
    team_resource_url,
)

from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import FakeControlPlane  # noqa: E402, RUF100

# #1719 (Task 3): team_memberships.user_id is a uuid column — real JWT
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


def _member(user_id: str, team_id: str, role: str = "owner") -> dict:
    return {"user_id": user_id, "team_id": team_id, "role": role,
            "status": "active"}


def _join_second_team(api_client) -> None:
    """Seed a second active membership for _U1 (team-team-001) on the
    fixture control plane."""
    _, cp = api_client
    cp.tables["team_memberships"].append(
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


def _auth_code_flow(tc, cp, *, user_id: str = _U1,
                    resource: str | None = None,
                    client_id: str | None = None) -> dict:
    """Register → consent → return the auth code + client_id (P2 path).

    Assumes the caller has stubbed hosted_api.verify_session_jwt.
    """
    if client_id is None:
        client_id = _register_client(tc)["client_id"]
    verifier, challenge = _pkce()
    r = tc.post("/oauth/consent", json={
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "st-123",
        "scope": "mcp",
        "resource": resource,
    }, headers={"Authorization": "Bearer fake-session-jwt"})
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
        "teams": [dict(TEAM_FREE), dict(TEAM_TEAM)],
        "team_memberships": [_member(_U1, "team-free-001")],
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
        assert 'id="team-select"' in html
        # the Authorize POST carries the PICKER selection first, then the
        # client-declared resource (single-team flow unchanged)
        assert "resource: teamResource || PARAMS.resource || null" in html
        # options carry each membership's team-scoped resource as the value
        assert "opt.value = m.resource" in html
        assert "memberships.forEach" in html

    def test_consent_html_single_team_select_not_visible(self, api_client):
        html = self._consent_html(api_client)
        # select present-but-hidden in the shared markup; only unhidden for
        # memberships.length > 1
        assert 'id="team-select" style="display:none' in html
        assert "memberships && memberships.length > 1" in html
        assert 'id="team-line"' in html
        assert html.count('id="team-select"') == 1

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
        assert "Choose a team…" in html
        assert "placeholder.disabled = true" in html
        assert 'teamResource = teamSelectEl.value' in html
        assert "teamResource = null" in html  # reset at every run entry
        assert "if (teamResource) enableAuthorize(); else disableAuthorize();" in html

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
        assert "while (teamSelect.firstChild) teamSelect.removeChild" in html
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


class TestConsentPreview:
    def test_preview_resolves_default_team(self, api_client, session_user):
        tc, _ = api_client
        session_user(_U1)
        r = tc.get("/oauth/consent/preview", params={"resource": ""},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 200
        assert r.json()["team_id"] == "team-free-001"
        assert r.json()["team_name"] == "Free Team"

    def test_preview_resolves_team_scoped_resource(self, api_client, session_user):
        tc, _ = api_client
        session_user(_U1)
        r = tc.get("/oauth/consent/preview",
                   params={"resource": team_resource_url(TEST_BASE, "team-free-001")},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 200
        assert r.json()["team_id"] == "team-free-001"

    def test_preview_non_member_403(self, api_client, session_user):
        tc, _ = api_client
        session_user(_U1)
        r = tc.get("/oauth/consent/preview",
                   params={"resource": team_resource_url(TEST_BASE, "team-team-001")},
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
        assert body["team_id"] is None
        assert body["team_name"] is None
        assert body["resource"] == mcp_resource_url(TEST_BASE)
        assert [m["team_id"] for m in body["memberships"]] == [
            "team-free-001", "team-team-001"]  # deterministic sort
        for m in body["memberships"]:
            assert m["resource"] == team_resource_url(TEST_BASE, m["team_id"])
            assert m["team_name"]

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
        assert body["team_id"] is None
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
        assert body["team_id"] == "team-free-001"
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
        assert body["team_id"] == "team-free-001"
        assert body["resource"] == team_resource_url(TEST_BASE, "team-free-001")

    def test_preview_memberships_exclude_suspended_teams(self, api_client, session_user):
        """2 active + 1 suspended membership → the chooser lists only the two
        active teams (a suspended team can never be picked)."""
        tc, cp = api_client
        session_user(_U1)
        _join_second_team(api_client)
        cp.tables["teams"].append({
            "id": "team-suspended-001", "name": "Suspended Team",
            "tier": "free", "suspended_at": "2026-08-15T00:00:00Z"})
        cp.tables["team_memberships"].append(
            _member(_U1, "team-suspended-001", "member"))
        r = tc.get("/oauth/consent/preview", params={"resource": ""},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 200
        body = r.json()
        assert [m["team_id"] for m in body["memberships"]] == [
            "team-free-001", "team-team-001"]

    def test_preview_one_active_one_suspended_autobinds_active(self, api_client, session_user):
        """1 active + 1 suspended membership → sole-ACTIVE-team auto-bind shape
        (no memberships key; the suspended team never counts)."""
        tc, cp = api_client
        session_user(_U1)
        _join_second_team(api_client)
        cp.tables["teams"][1]["suspended_at"] = "2026-08-15T00:00:00Z"  # team-team-001
        r = tc.get("/oauth/consent/preview", params={"resource": ""},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 200
        body = r.json()
        assert body["team_id"] == "team-free-001"
        assert "memberships" not in body

    def test_preview_all_teams_suspended_403(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        cp.tables["teams"][0]["suspended_at"] = "2026-08-15T00:00:00Z"
        r = tc.get("/oauth/consent/preview", params={"resource": ""},
                   headers={"Authorization": "Bearer fake"})
        assert r.status_code == 403
        assert r.json()["error"] == "invalid_grant"

    def test_preview_declared_resource_suspended_team_403(self, api_client, session_user):
        """A declared team-scoped resource for a suspended team 403s at
        preview — never a code that dies at a later exchange."""
        tc, cp = api_client
        session_user(_U1)
        cp.tables["teams"][0]["suspended_at"] = "2026-08-15T00:00:00Z"
        r = tc.get("/oauth/consent/preview",
                   params={"resource": team_resource_url(TEST_BASE, "team-free-001")},
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
        assert acc["team_id"] == "team-free-001"
        assert acc["user_id"] == _U1
        assert acc["token_hash"] == hashlib.sha256(
            body["access_token"].encode()).hexdigest()
        ref = cp.tables["oauth_refresh_tokens"][0]
        assert ref["team_id"] == "team-free-001"

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
        row carries used_at, and a second consume raises invalid_grant even
        when called directly (no HTTP layer involved)."""
        from tortoise.oauth import OAuthError, _consume_code
        tc, cp = api_client
        session_user(_U1)
        flow = _auth_code_flow(tc, cp)
        row = _consume_code(cp, flow["code"])
        assert row["code_hash"] == hashlib.sha256(
            flow["code"].encode()).hexdigest()
        stored = cp.tables["oauth_codes"][0]
        assert stored["used_at"] is not None  # claimed in place
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
        canonical, team_id = parse_resource(TEST_BASE, TEST_BASE)
        assert canonical == mcp_resource_url(TEST_BASE)
        assert team_id is None
        canonical2, team_id2 = parse_resource(TEST_BASE, TEST_BASE + "/")
        assert canonical2 == mcp_resource_url(TEST_BASE)
        assert team_id2 is None

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
        canonical, team_id = parse_resource(TEST_BASE, mcp_resource_url(TEST_BASE) + "/")
        assert canonical == mcp_resource_url(TEST_BASE)
        assert team_id is None

    def test_team_scoped_still_parses(self):
        from tortoise.oauth import parse_resource
        resource = team_resource_url(TEST_BASE, "team-free-001")
        canonical, team_id = parse_resource(TEST_BASE, resource)
        assert canonical == resource
        assert team_id == "team-free-001"


class TestRfc8707Mapping:
    def test_team_scoped_resource_binds_that_team(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        # user-1 joins the second team
        cp.tables["team_memberships"].append(
            _member(_U1, "team-team-001", "member"))
        resource = team_resource_url(TEST_BASE, "team-team-001")
        flow = _auth_code_flow(tc, cp, resource=resource)
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"], resource=resource)
        assert r.status_code == 200, r.text
        assert cp.tables["oauth_access_tokens"][0]["team_id"] == "team-team-001"
        assert cp.tables["oauth_refresh_tokens"][0]["team_id"] == "team-team-001"

    def test_multi_team_default_requires_declaration(self, api_client, session_user):
        """D4 (no picker UI): a multi-team user MUST declare the resource."""
        tc, cp = api_client
        session_user(_U1)
        cp.tables["team_memberships"].append(
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
        resource = team_resource_url(TEST_BASE, "team-team-001")  # not a member
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
        other = team_resource_url(TEST_BASE, "team-team-001")
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
        cp.tables["team_memberships"] = []
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
                    team_id="team-free-001", scope="mcp", resource=None)
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
        cp.tables["teams"][0]["suspended_at"] = "2026-08-15T00:00:00Z"
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
        cp.tables["teams"][0]["suspended_at"] = "2026-08-15T00:00:00Z"
        r = tc.post("/oauth/consent", json={
            "client_id": _register_client(tc)["client_id"],
            "redirect_uri": REDIRECT, "response_type": "code",
            "code_challenge": "x" * 60, "code_challenge_method": "S256",
            "scope": "mcp",
            "resource": team_resource_url(TEST_BASE, "team-free-001")},
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
        cp.tables["teams"][0]["suspended_at"] = "2026-08-15T00:00:00Z"
        r = _exchange(tc, client_id=flow["client_id"], code=flow["code"],
                      verifier=flow["verifier"])
        assert r.status_code == 403
        assert r.json()["error"] == "invalid_grant"
        assert cp.tables.get("oauth_access_tokens", []) == []

    def test_lapsed_membership_revokes_refresh(self, api_client, session_user):
        tc, cp = api_client
        session_user(_U1)
        tokens = self._granted(tc, cp)
        cp.tables["team_memberships"] = []  # seat removed
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
        cp.tables["teams"][0]["suspended_at"] = "2026-08-15T00:00:00Z"
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
