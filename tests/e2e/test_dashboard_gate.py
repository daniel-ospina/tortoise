"""#1511 dashboard gate + claim-paste e2e (RUN_DASHBOARD_E2E opt-in).

Harness (pinned in the #1511 plan Task 5):
- Serve the site root (the /auth page) with `wrangler@4 pages dev . --port 8788`
  from website/ (the legal suite's server).
- Serve the dashboard dist with `wrangler@4 pages dev dist --port 8790`
  from website/apps/dashboard/.
- `__AUTH_BASE_URL = 'https://tortoise.premiselabs.co'` via addInitScript so
  the dashboard gate emits the ABSOLUTE target; intercepted
  `https://tortoise.premiselabs.co/auth` requests are re-fetched from the
  :8788 server (route handler proxies via page.request).
- Opt-in: RUN_DASHBOARD_E2E=1 (mirrors RUN_LEGAL_E2E).

Flows:
1. No session/claim → instant redirect to /auth (the gate emits the absolute
   target; the intercepted request is served the auth page).
2. Claim in flight (tt_claim_pending cookie only; ?claim=1 only; tt_claim_key
   only) → the claim-paste screen ("Claim your organization") shows.
3. Stored key alone (tortoise_api_key, no claim markers) → redirect to /auth
   (the storedKey exemption is gone).
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_session_login_flow import (
    APP_HOST,
    AUTH_HOST,
    _proxy_body,
    _session_json,
    _wire_prod_domains,
)

if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("dashboard e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

ROOT = Path(__file__).resolve().parent.parent.parent

AUTH_ORIGIN = os.environ.get("DASHBOARD_AUTH_BASE", "http://127.0.0.1:8788")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8790/")
AUTH_TARGET = "https://tortoise.premiselabs.co/auth"

# The legal suite's /signup → /auth rewrite serves the same content.
AUTH_LOCAL = AUTH_ORIGIN + "/auth"


def _mock_bootstrap_200(route, url: str, json_mod, team: dict | None = None) -> bool:
    """#1885: mock the post-mint dashboard bootstrap reads with 200s so the
    shell loads instead of 401-ing into the generic error card. Returns True
    if the request was handled. ``json_mod`` is the caller's json module
    (each handle imports its own); ``team`` overrides the /v1/team payload.
    #1828: the shell pins ?team_id= on these reads — match query-tolerant."""
    path = url.split("?", 1)[0]
    if path.endswith("/v1/team/keys"):
        route.fulfill(status=200, content_type="application/json", body="[]")
        return True
    if path.endswith("/v1/sessions"):
        route.fulfill(status=200, content_type="application/json", body="[]")
        return True
    if path.endswith("/backups"):
        route.fulfill(status=200, content_type="application/json",
                      body=json_mod.dumps({"backups": []}))
        return True
    if path.endswith("/v1/team"):
        t = team or {"team_id": "team_m429", "name": "M429", "tier": "free", "anon": False}
        route.fulfill(status=200, content_type="application/json",
                      body=json_mod.dumps(t))
        return True
    if "/members" in path:
        route.fulfill(status=200, content_type="application/json", body="[]")
        return True
    if path.endswith("/v1/graphs") or path.endswith("/v1/team/alerts"):
        route.fulfill(status=200, content_type="application/json", body="[]")
        return True
    return False


def _wire_auth_intercept(page: Page) -> None:
    """Intercept the absolute /auth target and serve the local auth page."""

    def handle(route):
        url = route.request.url
        if url.startswith((AUTH_TARGET, "https://tortoise.premiselabs.co/")):
            local = AUTH_LOCAL + url[len(AUTH_TARGET):]
            try:
                # #1941: content-type-aware fulfillment — resp.text() decodes
                # as UTF-8 and throws UnicodeDecodeError on binary assets
                # (PNG favicon/og:image).
                _proxy_body(route, local, page)
            except Exception:
                route.fulfill(status=200, content_type="text/html",
                              body="<html><body>auth</body></html>")
            return
        route.continue_()

    page.route("**://tortoise.premiselabs.co/**", handle)


def _goto_dashboard(page: Page) -> None:
    page.add_init_script("window.__AUTH_BASE_URL = 'https://tortoise.premiselabs.co';")
    page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30_000)


def test_no_session_redirects_to_auth(page: Page) -> None:
    """No session, no claim markers, no stored key → instant redirect to /auth
    (Back-proof: the gate uses location.replace)."""
    _wire_auth_intercept(page)
    _goto_dashboard(page)
    expect(page).to_have_url(re.compile(r"^https://tortoise\.premiselabs\.co/auth"), timeout=15_000)


def test_stored_key_alone_redirects_to_auth(page: Page) -> None:
    """A stored app-origin key WITHOUT claim markers is no longer a dashboard
    credential — the gate redirects to /auth (the key is a 'Last used' hint
    there, #1511)."""
    page.add_init_script("localStorage.setItem('tortoise_api_key', 'tt_stale');")
    _wire_auth_intercept(page)
    _goto_dashboard(page)
    expect(page).to_have_url(re.compile(r"^https://tortoise\.premiselabs\.co/auth"), timeout=15_000)


@pytest.mark.parametrize("claim_seed", [
    "query",       # ?claim=1 only (the ANON funnel lands here pre-paste)
    "in_flight",   # tt_claim_key + tt_claim_pending (an OAuth claim returning)
])
def test_claim_intent_shows_claim_paste(page: Page, claim_seed: str) -> None:
    """In-flight claim-intent renders the claim-paste screen — NOT a redirect
    to /auth (D2: anon-team account setup). Intent is IN-FLIGHT ONLY: the
    ?claim=1 route, or a claim key accompanied by the 1h tt_claim_pending
    marker (code-review P1: a BARE stale key/marker must not pin the screen)."""
    if claim_seed == "in_flight":
        page.add_init_script("""
          sessionStorage.setItem('tt_claim_key', 'tt_claim');
          document.cookie = 'tt_claim_pending=1; Path=/;';
        """)
    # query seeds via the URL itself
    url = DASHBOARD_URL + ("?claim=1" if claim_seed == "query" else "")
    _wire_auth_intercept(page)
    page.add_init_script("window.__AUTH_BASE_URL = 'https://tortoise.premiselabs.co';")
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Claim your organization", timeout=20_000)


@pytest.mark.parametrize("stale_seed", [
    "cookie",    # tt_claim_pending alone (a stale 1h marker, no key in flight)
    "session",   # tt_claim_key alone (a stale pasted key, no claim in flight)
])
def test_stale_claim_markers_redirect_to_auth(page: Page, stale_seed: str) -> None:
    """#1511 (code-review P1): a BARE stale claim marker or key is NOT
    claim-intent — it must not pin a sessionless user on the claim screen
    (which has no other affordances) and must not misroute a signed-in user.
    Both redirect to /auth like any other no-session visitor."""
    if stale_seed == "cookie":
        page.add_init_script(
            "document.cookie = 'tt_claim_pending=1; Path=/;';")
    else:
        page.add_init_script("sessionStorage.setItem('tt_claim_key', 'tt_claim');")
    _wire_auth_intercept(page)
    _goto_dashboard(page)
    expect(page).to_have_url(re.compile(r"^https://tortoise\.premiselabs\.co/auth"), timeout=15_000)


def test_claim_paste_has_back_to_signin_escape(page: Page) -> None:
    """#1511 (code-review P1): the claim-paste screen has a hard escape hatch
    — 'Back to sign in' links to the /auth page (a trapped sessionless user
    can always leave)."""
    page.add_init_script("""
      sessionStorage.setItem('tt_claim_key', 'tt_claim');
      document.cookie = 'tt_claim_pending=1; Path=/;';
    """)
    _wire_auth_intercept(page)
    page.add_init_script("window.__AUTH_BASE_URL = 'https://tortoise.premiselabs.co';")
    page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Claim your organization", timeout=20_000)
    expect(page.locator("a[href='https://tortoise.premiselabs.co/auth']")).to_be_visible()


def test_fresh_session_login_renders_session_only_with_zero_mint(page: Page) -> None:
    """#2167 (F1/F6 home — inverts the #1830/#1559 mint-429 test): a fresh
    session login (RETURNING user, team exists, NO stored key) issues ZERO
    POST /v1/session/key — the mount never mints a bootstrap key. The
    dashboard renders session-only on the JWT (Team/Keys/Sessions/Backups
    all 200 via the shell mocks) with NO agent-key banner (the old
    'Couldn't create an agent key' recoverable-mint leg is gone with the
    mint machinery). A regression mint fails loudly (loud-500 tripwire)."""
    import json as _json
    import time as _time
    import urllib.parse as _up
    mint_calls: list = []
    sess = {"access_token": "fake.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sig",
            "refresh_token": "rt", "expires_in": 3600,
            "expires_at": int(_time.time()) + 3600, "token_type": "bearer",
            "user": {"id": "u-mint429", "email": "mint429@premise-labs.dev"}}
    page.context.add_cookies([{"name": "sb-tortoise-auth-token",
                               "value": _up.quote(_json.dumps(sess)),
                               "domain": ".premiselabs.co", "path": "/"}])
    def handle(route):
        url = route.request.url
        if "api.premiselabs.co" in url:
            if url.endswith("/v1/session/key"):
                # loud 500 + counter — the journey must never reach it
                mint_calls.append(url)
                route.fulfill(status=500, content_type="application/json",
                              body=_json.dumps({"detail": "#2167 zero-mint tripwire"}))
                return
            if url.endswith("/v1/teams"):
                route.fulfill(status=200, content_type="application/json",
                              body=_json.dumps([{"team_id": "team_m429", "name": "M429"}]))
                return
            if url.endswith("/v1/onboarding/state") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=_json.dumps({"onboarding": {"onboarding_complete": True}}))
                return
            if url.endswith("/v1/user/identity") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=_json.dumps({"methods": [], "login_methods": 0,
                                                "keys_tier": 0, "banner": {"show": False}}))
                return
            # session-only render mocks (query-tolerant, #1828)
            if _mock_bootstrap_200(route, url, _json,
                                   team={"team_id": "team_m429", "team_name": "M429",
                                         "tier": "free", "anon": False, "graph_ready": True,
                                         "point_count": 0}):
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if url.startswith(AUTH_HOST):
            local = AUTH_ORIGIN + url[len(AUTH_HOST):]
            _proxy_body(route, local, page)
            return
        if url.startswith(APP_HOST):
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            _proxy_body(route, local, page)
            return
        route.continue_()

    page.route("**/*", handle)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    # The dashboard renders session-only (chrome up, no mint banner, never
    # the silent redirect shell).
    expect(page.locator("body")).to_contain_text("Graphs", timeout=25_000)
    assert mint_calls == [], f"zero-mint: POST /v1/session/key fired: {mint_calls}"
    expect(page.locator("body")).not_to_contain_text("Couldn't create an agent key")
    expect(page.locator("body")).not_to_contain_text("Redirecting to the sign-in page")
    expect(page.locator("body")).not_to_contain_text("HTTP 401")


def test_welcome_mode_provisions_and_reveals_key_once(page: Page) -> None:
    """#1566: a first-timer (valid session, NO teams) landing on the app is
    provisioned IN-APP — tenant-provision → membership poll → reveal — and
    the key is shown in the welcome card exactly once (A13). A returning
    visit (onboarding complete) lands on the dashboard's first-run card
    with NO re-reveal (#1885: the welcome-card reveal only fires on the
    provisioning path)."""
    import time as _time
    import urllib.parse as _up
    user_id = "u-welcome1566"
    sess = {"access_token": "fake.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sig",
            "refresh_token": "rt", "expires_in": 3600,
            "expires_at": int(_time.time()) + 3600, "token_type": "bearer",
            "user": {"id": user_id, "email": "welcome1566@premise-labs.dev",
                     "user_metadata": {"display_name": "Welcome Test"}}}
    page.context.add_cookies([{"name": "sb-tortoise-auth-token",
                               "value": _up.quote(json.dumps(sess)),
                               "domain": ".premiselabs.co", "path": "/"}])
    reveal_calls = {"n": 0}

    def handle(route):
        url = route.request.url
        if "api.premiselabs.co" in url:
            if url.endswith("/v1/teams"):
                # First-timer: no teams → the app provisions.
                route.fulfill(status=200, content_type="application/json", body="[]")
                return
            if url.endswith("/v1/onboarding/state") and route.request.method == "GET":
                # #1885: the shell calls this FIRST (re-fired per #1847) — a
                # 401 catch-all shows the generic error card before the flow.
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"onboarding": {}}))
                return
            if url.endswith("/v1/user/identity") and route.request.method == "GET":
                # #1885: post-#1765 bootstrap also reads the identity inventory.
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"methods": [], "login_methods": 0,
                                               "keys_tier": 0, "banner": {"show": False}}))
                return
            if _mock_bootstrap_200(route, url, json,
                                   team={"team_id": "team_w", "team_name": "Welcome Team",
                                         "tier": "free", "anon": False}):
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if "functions/v1/tenant-provision" in url and route.request.method == "POST":
            route.fulfill(status=201, content_type="application/json",
                          body=json.dumps({"team_id": "team_w", "team_name": "Welcome Team",
                                           "api_key": "tt_welcome_key_1234567890abcdef"}))
            return
        if "team_memberships" in url and route.request.method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"team_id": "team_w", "team_name": "Welcome Team",
                                           "graph_name": "team_w", "status": "active"}))
            return
        if "rpc/reveal_api_key" in url and route.request.method == "POST":
            reveal_calls["n"] += 1
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps("tt_welcome_key_1234567890abcdef"))
            return
        if url.startswith(AUTH_HOST):
            local = AUTH_ORIGIN + url[len(AUTH_HOST):]
            _proxy_body(route, local, page)
            return
        if url.startswith(APP_HOST):
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            _proxy_body(route, local, page)
            return
        route.continue_()

    page.route("**/*", handle)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("tt_welcome_key_1234567890abcdef", timeout=20_000)
    assert reveal_calls["n"] == 1, f"reveal must fire exactly once, got {reveal_calls['n']}"
    # The raw key must be displayed (a revealed-once key is never shown again).
    expect(page.locator("body")).to_contain_text("copy it now", timeout=10_000)
    # Returning visit: the key is consumed (reveal returns 'pending') → the
    # ready card, no re-reveal.
    reveal_calls["n"] = 0
    mint_calls: list = []
    def handle_returning(route):
        url = route.request.url
        if "rpc/reveal_api_key" in url and route.request.method == "POST":
            reveal_calls["n"] += 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps("pending"))
            return
        if url.endswith("/v1/session/key") and route.request.method == "POST":
            # #2167: the returning visit NEVER mints — the mount is
            # session-only (or adopts the stored welcome key via its probe).
            # Loud 500 + counter so a regression mint fails the journey.
            mint_calls.append(url)
            route.fulfill(status=500, content_type="application/json",
                          body=json.dumps({"detail": "#2167 zero-mint tripwire"}))
            return
        if url.endswith("/v1/teams"):
            # #1885: returning visit — the team EXISTS now; the shared handle
            # mocks teams→[] (first-timer), which would re-trigger provisioning.
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps([{"team_id": "team_w", "team_name": "Welcome Team",
                                            "tier": "free"}]))
            return
        _path_ret = url.split("?", 1)[0]
        if _path_ret.endswith("/v1/onboarding/state") and route.request.method == "GET":
            # #1885: returning visit — onboarding is COMPLETE (the shared handle
            # returns an empty onboarding dict → the setup wizard re-appears).
            # Query-strip: the post-#1828 shell pins ?team_id= on this read.
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"onboarding": {"onboarding_complete": True}}))
            return
        handle(route)
    page.route("**/*", handle_returning)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    # #1885: a returning user (onboarding complete) lands on the dashboard's
    # first-run card — the key is NEVER re-revealed (reveal_calls stays 0;
    # the welcome-card reveal only fires on the provisioning path).
    expect(page.locator("body")).to_contain_text("Welcome to your Tortoise graph", timeout=20_000)
    assert reveal_calls["n"] == 0, f"no re-reveal on the returning dashboard path, got {reveal_calls['n']}"
    assert mint_calls == [], f"zero-mint: POST /v1/session/key on the returning visit: {mint_calls}"
    expect(page.locator("body")).not_to_contain_text("copy it now")
    # no mint → no recoverable-mint banner (the #2167 deletion)
    expect(page.locator("body")).not_to_contain_text("Couldn't create an agent key")


def test_welcome_mode_provision_failure_shows_error_card(page: Page) -> None:
    """#1566: an edge-function provisioning failure shows the actionable
    error card with a retry — never the silent stuck shell (the #1559
    pattern applied to the welcome mode)."""
    import time as _time
    import urllib.parse as _up
    user_id = "u-wfail"
    sess = {"access_token": "fake.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sig",
            "refresh_token": "rt", "expires_in": 3600,
            "expires_at": int(_time.time()) + 3600, "token_type": "bearer",
            "user": {"id": user_id, "email": "wfail@premise-labs.dev"}}
    page.context.add_cookies([{"name": "sb-tortoise-auth-token",
                               "value": _up.quote(json.dumps(sess)),
                               "domain": ".premiselabs.co", "path": "/"}])

    def handle(route):
        url = route.request.url
        if "api.premiselabs.co" in url:
            if url.endswith("/v1/teams"):
                route.fulfill(status=200, content_type="application/json", body="[]")
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if "functions/v1/tenant-provision" in url and route.request.method == "POST":
            route.fulfill(status=500, content_type="application/json",
                          body=json.dumps({"error": "boom"}))
            return
        if url.startswith(AUTH_HOST):
            local = AUTH_ORIGIN + url[len(AUTH_HOST):]
            _proxy_body(route, local, page)
            return
        if url.startswith(APP_HOST):
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            _proxy_body(route, local, page)
            return
        route.continue_()

    page.route("**/*", handle)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Could not create your organization — try again.", timeout=20_000)
    expect(page.locator("body")).to_contain_text("Try again", timeout=10_000)


def test_welcome_mode_provision_401_clears_session_and_redirects(page: Page) -> None:
    """#1566/#1511 semantic: a 401 from tenant-provision means the session is
    stale — the app clears it and goes to /auth (never an error card or a
    stuck state)."""
    import time as _time
    import urllib.parse as _up
    user_id = "u-w401"
    sess = {"access_token": "fake.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sig",
            "refresh_token": "rt", "expires_in": 3600,
            "expires_at": int(_time.time()) + 3600, "token_type": "bearer",
            "user": {"id": user_id, "email": "w401@premise-labs.dev"}}
    page.context.add_cookies([{"name": "sb-tortoise-auth-token",
                               "value": _up.quote(json.dumps(sess)),
                               "domain": ".premiselabs.co", "path": "/"}])

    def handle(route):
        url = route.request.url
        if "api.premiselabs.co" in url:
            if url.endswith("/v1/teams"):
                route.fulfill(status=200, content_type="application/json", body="[]")
                return
            if url.endswith("/v1/onboarding/state") and route.request.method == "GET":
                # #1885: the shell calls this FIRST (re-fired per #1847).
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"onboarding": {}}))
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if "functions/v1/tenant-provision" in url and route.request.method == "POST":
            route.fulfill(status=401, content_type="application/json",
                          body=json.dumps({"error": "Unauthorized"}))
            return
        if url.startswith(AUTH_HOST):
            local = AUTH_ORIGIN + url[len(AUTH_HOST):]
            _proxy_body(route, local, page)
            return
        if url.startswith(APP_HOST):
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            _proxy_body(route, local, page)
            return
        route.continue_()

    page.route("**/*", handle)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page).to_have_url(re.compile(rf"^{re.escape(AUTH_HOST)}/auth"), timeout=20_000)


def test_oauth_callback_fragment_lands_in_dashboard(page: Page) -> None:
    """#1566 (code-review P0): a first-time OAuth return lands on the app with
    the session in the FRAGMENT (#access_token=…) and NO cookie yet — the
    synchronous head gate must NOT bounce (that would drop the fragment and
    loop back to /auth); supabase-js ingests it and the dashboard mounts."""
    user_id = "u-frag"
    # NO session cookie — the fragment carries the tokens (supabase-js
    # ingests them; the mocked /auth/v1/user returns the identity). All
    # supabase-host calls are intercepted (401 fallback) so a real network
    # round trip can't invalidate the ingested fake session.
    def handle(route):
        url = route.request.url
        if "ybetwichurajbfswfeqa.supabase.co" in url:
            if "auth/v1/user" in url:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"id": user_id, "aud": "authenticated",
                                               "role": "authenticated",
                                               "email": "frag@premise-labs.dev",
                                               "app_metadata": {"provider": "github"},
                                               "user_metadata": {"display_name": "Frag"}}))
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if "api.premiselabs.co" in url:
            # #1828: loadAll pins ?team_id= on overview reads — match on the
            # query-stripped path so /v1/team/keys?team_id=… still resolves.
            path = urllib.parse.urlsplit(url).path
            if path.endswith("/v1/teams"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([{"team_id": "team_frag", "name": "Frag Team"}]))
                return
            if path.endswith("/v1/session/key"):
                # #2167: a fragment-auth first landing never mints (no stored
                # key → the mount is session-only) — loud 500 tripwire.
                route.fulfill(status=500, content_type="application/json",
                              body=json.dumps({"detail": "#2167 zero-mint tripwire"}))
                return
            if path.endswith("/v1/team") or path.endswith("/v1/team/"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"team_id": "team_frag", "name": "Frag Team", "tier": "free"}))
                return
            if path.endswith("/v1/team/keys"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"keys": []}))
                return
            if path.endswith("/v1/sessions"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"sessions": []}))
                return
            if path.endswith("/backups"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"backups": []}))
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if url.startswith(AUTH_HOST):
            local = AUTH_ORIGIN + url[len(AUTH_HOST):]
            _proxy_body(route, local, page)
            return
        if url.startswith(APP_HOST):
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            _proxy_body(route, local, page)
            return
        route.continue_()
    page.route("**/*", handle)
    # Implicit-flow fragment return (the signup.html OAuth target).
    _FRAG_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiAidS1mcmFnIiwgImF1ZCI6ICJhdXRoZW50aWNhdGVkIiwgInJvbGUiOiAiYXV0aGVudGljYXRlZCIsICJleHAiOiA0MTAyNDQ0ODAwLCAiZW1haWwiOiAiZnJhZ0BwcmVtaXNlLWxhYnMuZGV2In0.sig"
    page.goto(APP_HOST + "/#access_token=" + _FRAG_TOKEN + "&refresh_token=fake-rt&expires_in=3600&token_type=bearer",
              wait_until="domcontentloaded", timeout=30_000)
    # The gate must NOT bounce to /auth; the session ingests and the app
    # chrome (with a team) renders.
    expect(page).not_to_have_url(re.compile(rf"^{re.escape(AUTH_HOST)}/auth"), timeout=10_000)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=25_000)


def _mock_session_shell(route, url: str, json_mod, mint_calls: list | None = None) -> bool:
    """#2167: the session-only dashboard shell reads — onboarding state,
    identity inventory, keys/sessions/backups (query-tolerant #1828) — 200
    empty so the chrome renders on the session JWT alone. POST /v1/session/key
    is a loud-500 + counter zero-mint tripwire. Returns True if handled (the
    caller's /v1/teams + /v1/team branches run first)."""
    path = url.split("?", 1)[0]
    if path.endswith("/v1/session/key"):
        if mint_calls is not None:
            mint_calls.append(url)
        route.fulfill(status=500, content_type="application/json",
                      body=json_mod.dumps({"detail": "#2167 zero-mint tripwire"}))
        return True
    if path.endswith("/v1/onboarding/state") and not url.rstrip("/").endswith("PATCH"):
        route.fulfill(status=200, content_type="application/json",
                      body=json_mod.dumps({"onboarding": {"onboarding_complete": True}}))
        return True
    if path.endswith("/v1/user/identity"):
        route.fulfill(status=200, content_type="application/json",
                      body=json_mod.dumps({"methods": [], "login_methods": 0,
                                            "keys_tier": 0, "banner": {"show": False}}))
        return True
    if path.endswith("/v1/team/keys") or path.endswith("/v1/sessions") or path.endswith("/backups"):
        route.fulfill(status=200, content_type="application/json", body=json_mod.dumps({"keys": [], "sessions": [], "backups": []}))
        return True
    if "/members" in path or path.endswith("/v1/graphs") or path.endswith("/v1/team/alerts"):
        route.fulfill(status=200, content_type="application/json", body="[]")
        return True
    return False


def test_probe_401_drops_stored_key_and_renders_session_only(page: Page) -> None:
    """#2167 F3: a stored REVOKED/EXPIRED/disabled durable (rejections are
    identical — 401 'Invalid API key') is DROPPED at the mount probe: the
    KEY_STORAGE slot clears, apiKey state clears, and the dashboard renders
    session-only (never the mint fallback, never an error card)."""
    import time as _time
    import urllib.parse as _up
    user_id = "u-drop401"
    dead_key = "tt_dead_abcdef0123456789"
    mint_calls: list = []
    sess = {"access_token": "fake.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sig",
            "refresh_token": "rt", "expires_in": 3600,
            "expires_at": int(_time.time()) + 3600, "token_type": "bearer",
            "user": {"id": user_id, "email": "drop401@premise-labs.dev"}}
    page.context.add_cookies([{"name": "sb-tortoise-auth-token",
                               "value": _up.quote(json.dumps(sess)),
                               "domain": ".premiselabs.co", "path": "/"}])
    page.add_init_script(f"localStorage.setItem('tortoise_api_key', '{dead_key}');")

    def handle(route):
        url = route.request.url
        if "api.premiselabs.co" in url:
            path = url.split("?", 1)[0]
            if path.endswith("/v1/teams"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([{"team_id": "team_ok", "name": "OK", "tier": "free"}]))
                return
            if path.endswith("/v1/team") or path.endswith("/v1/team/"):
                auth = (route.request.headers.get("authorization") or "")
                if auth.startswith("Bearer tt_"):
                    # the stored-key probe: revoked/disabled/expired reject
                    # identically with 401 (resolve_api_key, supabase_control)
                    route.fulfill(status=401, content_type="application/json",
                                  body=json.dumps({"detail": "Invalid API key"}))
                    return
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"team_id": "team_ok", "team_name": "OK",
                                               "tier": "free", "anon": False, "graph_ready": True,
                                               "point_count": 1}))
                return
            if _mock_session_shell(route, url, json, mint_calls):
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if url.startswith(AUTH_HOST):
            local = AUTH_ORIGIN + url[len(AUTH_HOST):]
            _proxy_body(route, local, page)
            return
        if url.startswith(APP_HOST):
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            _proxy_body(route, local, page)
            return
        route.continue_()

    page.route("**/*", handle)
    # wait for the MOUNT PROBE (the key-authed GET /v1/team → 401) so the
    # slot-drop assert below never races the in-flight mount
    with page.expect_response(
            lambda r: "/v1/team" in r.url
            and (r.request.headers.get("authorization") or "").startswith("Bearer tt_"),
            timeout=20000):
        page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=25_000)
    assert mint_calls == [], f"zero-mint: POST /v1/session/key fired: {mint_calls}"
    # the dead key material is gone: slot cleared, no key-authed leftovers
    slot = page.evaluate("localStorage.getItem('tortoise_api_key')")
    assert slot is None, f"probe-401 must clear the slot, got {slot!r}"
    # session-only render — never an error card / 'Invalid API key' banner
    expect(page.locator("body")).not_to_contain_text("Invalid API key")
    expect(page.locator("body")).not_to_contain_text("Redirecting to the sign-in page")


def test_probe_403_suspended_dict_keeps_key_and_renders_appeal(page: Page) -> None:
    """#2167 rule 5d + F8 (probe path): a stored DURABLE on a SUSPENDED team
    probes 403 {detail:{code:'SUSPENDED',…}} on the KEY lane (hosted_api.py
    L1620-1623) — the recoverable durable is KEPT (slot retained) and the
    appeal path renders. Never a mint, never a drop."""
    import time as _time
    import urllib.parse as _up
    user_id = "u-susp"
    held_key = "tt_susp_abcdef0123456789"
    mint_calls: list = []
    sess = {"access_token": "fake.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sig",
            "refresh_token": "rt", "expires_in": 3600,
            "expires_at": int(_time.time()) + 3600, "token_type": "bearer",
            "user": {"id": user_id, "email": "susp@premise-labs.dev"}}
    page.context.add_cookies([{"name": "sb-tortoise-auth-token",
                               "value": _up.quote(json.dumps(sess)),
                               "domain": ".premiselabs.co", "path": "/"}])
    page.add_init_script(f"localStorage.setItem('tortoise_api_key', '{held_key}');")

    def handle(route):
        url = route.request.url
        if "api.premiselabs.co" in url:
            path = url.split("?", 1)[0]
            if path.endswith("/v1/teams"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([{"team_id": "team_susp", "name": "Suspended Co",
                                                "suspended_at": "2026-09-01T00:00:00Z"}]))
                return
            if path.endswith("/v1/team") or path.endswith("/v1/team/"):
                auth = (route.request.headers.get("authorization") or "")
                if auth.startswith("Bearer tt_"):
                    # the probe (key lane): suspended team → 403 dict
                    route.fulfill(status=403, content_type="application/json",
                                  body=json.dumps({"detail": {"code": "SUSPENDED",
                                                                "message": "Suspended for review",
                                                                "appeal_url": "https://premise-labs.dev/appeal"}}))
                    return
                route.fulfill(status=403, content_type="application/json",
                              body=json.dumps({"detail": {"code": "SUSPENDED",
                                                            "message": "Suspended for review",
                                                            "appeal_url": "https://premise-labs.dev/appeal"}}))
                return
            if _mock_session_shell(route, url, json, mint_calls):
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if url.startswith(AUTH_HOST):
            local = AUTH_ORIGIN + url[len(AUTH_HOST):]
            _proxy_body(route, local, page)
            return
        if url.startswith(APP_HOST):
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            _proxy_body(route, local, page)
            return
        route.continue_()

    page.route("**/*", handle)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    # the appeal path renders (blocking error card + CTA, pre-change parity)
    expect(page.locator("body")).to_contain_text("Suspended for review", timeout=25_000)
    expect(page.locator("body")).to_contain_text("Appeal the suspension", timeout=10_000)
    assert mint_calls == [], f"zero-mint: POST /v1/session/key fired: {mint_calls}"
    # 5d: the recoverable durable is KEPT — slot untouched
    slot = page.evaluate("localStorage.getItem('tortoise_api_key')")
    assert slot == held_key, f"5d must retain the slot, got {slot!r}"


def test_fresh_login_suspended_team_shows_appeal_banner(page: Page) -> None:
    """#2167 rule 9 + F8 (session-read path — the ACTUAL fresh-login
    mechanism): a suspended team + NO stored key → the mount runs session-only
    and the session-authed team read hits _suspended_detail()'s 403 dict →
    the appeal banner renders. (Distinct from the rule-5d probe test above.)"""
    import time as _time
    import urllib.parse as _up
    user_id = "u-susp2"
    mint_calls: list = []
    sess = {"access_token": "fake.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sig",
            "refresh_token": "rt", "expires_in": 3600,
            "expires_at": int(_time.time()) + 3600, "token_type": "bearer",
            "user": {"id": user_id, "email": "susp2@premise-labs.dev"}}
    page.context.add_cookies([{"name": "sb-tortoise-auth-token",
                               "value": _up.quote(json.dumps(sess)),
                               "domain": ".premiselabs.co", "path": "/"}])

    def handle(route):
        url = route.request.url
        if "api.premiselabs.co" in url:
            path = url.split("?", 1)[0]
            if path.endswith("/v1/teams"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([{"team_id": "team_susp", "name": "Suspended Co",
                                                "suspended_at": "2026-09-01T00:00:00Z"}]))
                return
            if path.endswith("/v1/team") or path.endswith("/v1/team/"):
                # session read → _suspended_detail() 403 dict (#308/#1912)
                route.fulfill(status=403, content_type="application/json",
                              body=json.dumps({"detail": {"code": "SUSPENDED",
                                                            "message": "Suspended for review",
                                                            "appeal_url": "https://premise-labs.dev/appeal"}}))
                return
            if _mock_session_shell(route, url, json, mint_calls):
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if url.startswith(AUTH_HOST):
            local = AUTH_ORIGIN + url[len(AUTH_HOST):]
            _proxy_body(route, local, page)
            return
        if url.startswith(APP_HOST):
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            _proxy_body(route, local, page)
            return
        route.continue_()

    page.route("**/*", handle)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Suspended for review", timeout=25_000)
    expect(page.locator("body")).to_contain_text("Appeal the suspension", timeout=10_000)
    assert mint_calls == [], f"zero-mint: POST /v1/session/key fired: {mint_calls}"


def test_multi_membership_suspended_first_healthy_second_renders(page: Page) -> None:
    """#2167 #1912 P1 pin: a multi-membership user whose FIRST membership is
    suspended but who holds a healthy SECOND team lands on the healthy team —
    never the suspension error card (the mount pins the first healthy team
    BEFORE completeLogin on every session-only landing; the old unpinned
    reads resolved memberships[0] → 403 → error card on every reload)."""
    import time as _time
    import urllib.parse as _up
    user_id = "u-1912"
    mint_calls: list = []
    sess = {"access_token": "fake.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sig",
            "refresh_token": "rt", "expires_in": 3600,
            "expires_at": int(_time.time()) + 3600, "token_type": "bearer",
            "user": {"id": user_id, "email": "u1912@premise-labs.dev"}}
    page.context.add_cookies([{"name": "sb-tortoise-auth-token",
                               "value": _up.quote(json.dumps(sess)),
                               "domain": ".premiselabs.co", "path": "/"}])

    def handle(route):
        url = route.request.url
        if "api.premiselabs.co" in url:
            path = url.split("?", 1)[0]
            if path.endswith("/v1/teams"):
                # suspended FIRST membership + healthy second (#1912)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([
                                  {"team_id": "team_sus", "name": "Suspended Co",
                                   "tier": "free", "suspended_at": "2026-09-01T00:00:00Z"},
                                  {"team_id": "team_ok", "name": "Healthy Co", "tier": "free"},
                              ]))
                return
            if path.endswith("/v1/team") or path.endswith("/v1/team/"):
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                tid = (qs.get("team_id") or [""] )[0]
                if tid == "team_sus":
                    route.fulfill(status=403, content_type="application/json",
                                  body=json.dumps({"detail": {"code": "SUSPENDED",
                                                                "message": "Suspended for review",
                                                                "appeal_url": "https://premise-labs.dev/appeal"}}))
                    return
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"team_id": tid or "team_ok", "team_name": "Healthy Co",
                                               "tier": "free", "anon": False, "graph_ready": True,
                                               "point_count": 1}))
                return
            if _mock_session_shell(route, url, json, mint_calls):
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if url.startswith(AUTH_HOST):
            local = AUTH_ORIGIN + url[len(AUTH_HOST):]
            _proxy_body(route, local, page)
            return
        if url.startswith(APP_HOST):
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            _proxy_body(route, local, page)
            return
        route.continue_()

    page.route("**/*", handle)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    # the healthy second team renders — chrome up, NO suspension card
    expect(page.locator("body")).to_contain_text("Graphs", timeout=25_000)
    expect(page.locator("body")).not_to_contain_text("Suspended for review")
    expect(page.locator("body")).not_to_contain_text("Appeal the suspension")
    assert mint_calls == [], f"zero-mint: POST /v1/session/key fired: {mint_calls}"
    # the healthy team is selected (the account blob names it)
    expect(page.get_by_role("button", name=re.compile(r"Account menu"))).to_contain_text("Healthy Co", timeout=10_000)


def test_logout_redirects_to_auth(page: Page) -> None:
    """#1511 (VGATE P1): a signed-in user clicking Log out is redirected to
    /auth — the key-only card is gone, so sign-out must land on the login
    page, never the dead redirect shell. #2167 rule 8 (F5): the logout wipe
    is RETAINED — a probe-adopted durable held in KEY_STORAGE is cleared on
    logout (the slot never survives a sign-out). Requires the loop harness: a
    valid session cookie → dashboard renders → Log out → /auth."""
    durable = "tt_loop_durable_abcdef0123456789"
    _wire_prod_domains(page)
    page.context.add_cookies([{
        "name": "sb-tortoise-auth-token",
        "value": urllib.parse.quote(json.dumps(_session_json())),
        "domain": ".premiselabs.co", "path": "/",
    }])
    page.add_init_script(f"window.__AUTH_BASE_URL = '{AUTH_HOST}';")
    # #2167: the durable arrives via the localStorage-seeded mount probe
    # (the wire's /v1/team 200s the key-authed probe → 5b adopt)
    page.add_init_script(f"localStorage.setItem('tortoise_api_key', '{durable}');")
    # #2167 rule 8 (F5): the logout wipe is synchronous on the APP origin,
    # but the /auth bounce lands on the TORTUISE origin (localStorage is
    # per-origin — a post-navigation read would be vacuous). Patch
    # Storage.prototype.removeItem to log the KEY_STORAGE wipe into a
    # PARENT-DOMAIN cookie (readable from /auth after the bounce).
    page.add_init_script("""
      (function () {
        const orig = Storage.prototype.removeItem;
        Storage.prototype.removeItem = function (k) {
          if (k === 'tortoise_api_key' && this === window.localStorage) {
            try { document.cookie = 'tt_wipe_log=1; Domain=.premiselabs.co; Path=/; Max-Age=600'; } catch (e) {}
          }
          return orig.apply(this, arguments);
        };
      })();
    """)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=20_000)
    # the probe adopted the stored durable (held key renders in state)
    assert page.evaluate("localStorage.getItem('tortoise_api_key')") == durable
    page.locator(".account-blob-btn").click()
    expect(page.locator(".account-menu-logout")).to_be_visible()
    page.locator(".account-menu-logout").click()
    expect(page).to_have_url(re.compile(rf"^{re.escape(AUTH_HOST)}/auth"), timeout=20_000)
    # rule 8: the app-origin wipe fired before the bounce (cookie set by the
    # patched removeItem) — never "undefined"/"null" residue, never a
    # surviving credential
    wiped = page.evaluate("document.cookie.indexOf('tt_wipe_log=1') !== -1")
    assert wiped, "logout must wipe KEY_STORAGE on the app origin (no tt_wipe_log cookie)"
