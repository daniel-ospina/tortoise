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
   only) → the claim-paste screen ("Claim your team") shows.
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


def _wire_auth_intercept(page: Page) -> None:
    """Intercept the absolute /auth target and serve the local auth page."""

    def handle(route):
        url = route.request.url
        if url.startswith((AUTH_TARGET, "https://tortoise.premiselabs.co/")):
            local = AUTH_LOCAL + url[len(AUTH_TARGET):]
            try:
                resp = page.request.get(local)
                route.fulfill(status=resp.status, content_type="text/html",
                              body=resp.text())
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
    expect(page.locator("body")).to_contain_text("Claim your team", timeout=20_000)


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
    expect(page.locator("body")).to_contain_text("Claim your team", timeout=20_000)
    expect(page.locator("a[href='https://tortoise.premiselabs.co/auth']")).to_be_visible()


def test_mint_429_shows_error_card_not_stuck_shell(page: Page) -> None:
    """#1559: a session-key mint 429 (the live global-IP-bucket bug) must
    render an actionable error card with a retry — never the silent
    'Redirecting to the sign-in page…' shell (which does NOT navigate and
    stranded every new user after OAuth)."""
    import json as _json
    import time as _time
    import urllib.parse as _up
    sess = {"access_token": "fake.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sig",
            "refresh_token": "rt", "expires_in": 3600,
            "expires_at": int(_time.time()) + 3600, "token_type": "bearer",
            "user": {"id": "u-mint429", "email": "mint429@premise-labs.dev"}}
    page.context.add_cookies([{"name": "sb-tortoise-auth-token",
                               "value": _up.quote(_json.dumps(sess)),
                               "domain": ".premiselabs.co", "path": "/"}])
    from tests.e2e.test_session_login_flow import AUTH_ORIGIN, DASHBOARD_URL, _proxy_body  # noqa: F401

    def handle(route):
        url = route.request.url
        if "api.premiselabs.co" in url:
            if url.endswith("/v1/session/key"):
                route.fulfill(status=429, content_type="application/json",
                              headers={"Retry-After": "60"},
                              body=_json.dumps({"detail": "Rate limit exceeded."}))
                return
            if url.endswith("/v1/teams"):
                # A RETURNING user (team exists) hits the mint path; a
                # first-timer would go through the #1566 in-app provisioning.
                route.fulfill(status=200, content_type="application/json",
                              body=_json.dumps([{"team_id": "team_m429", "name": "M429"}]))
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
    expect(page.locator("body")).to_contain_text("Too many requests from this network", timeout=20_000)
    expect(page.locator("body")).not_to_contain_text("Redirecting to the sign-in page")


def test_welcome_mode_provisions_and_reveals_key_once(page: Page) -> None:
    """#1566: a first-timer (valid session, NO teams) landing on the app is
    provisioned IN-APP — tenant-provision → membership poll → reveal — and
    the key is shown in the welcome card exactly once (A13). A returning
    visit (key consumed) shows the ready card without re-revealing."""
    import urllib.parse as _up
    import time as _time
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
            from tests.e2e.test_session_login_flow import AUTH_ORIGIN
            local = AUTH_ORIGIN + url[len(AUTH_HOST):]
            resp = page.request.get(local)
            route.fulfill(status=resp.status, content_type="text/html", body=resp.text())
            return
        if url.startswith(APP_HOST):
            from tests.e2e.test_session_login_flow import DASHBOARD_URL
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            ctype = "application/javascript" if local.endswith(".js") else ("text/css" if local.endswith(".css") else "text/html")
            resp = page.request.get(local)
            route.fulfill(status=resp.status, content_type=ctype, body=resp.body())
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
    def handle_returning(route):
        url = route.request.url
        if "rpc/reveal_api_key" in url and route.request.method == "POST":
            reveal_calls["n"] += 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps("pending"))
            return
        handle(route)
    page.route("**/*", handle_returning)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Welcome back", timeout=20_000)
    assert reveal_calls["n"] == 1, "returning visit reveals once (pending), no re-reveal"


def test_welcome_mode_provision_failure_shows_error_card(page: Page) -> None:
    """#1566: an edge-function provisioning failure shows the actionable
    error card with a retry — never the silent stuck shell (the #1559
    pattern applied to the welcome mode)."""
    import urllib.parse as _up
    import time as _time
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
            from tests.e2e.test_session_login_flow import AUTH_ORIGIN
            resp = page.request.get(AUTH_ORIGIN + url[len(AUTH_HOST):])
            route.fulfill(status=resp.status, content_type="text/html", body=resp.text())
            return
        if url.startswith(APP_HOST):
            from tests.e2e.test_session_login_flow import DASHBOARD_URL
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            ctype = "application/javascript" if local.endswith(".js") else ("text/css" if local.endswith(".css") else "text/html")
            resp = page.request.get(local)
            route.fulfill(status=resp.status, content_type=ctype, body=resp.body())
            return
        route.continue_()

    page.route("**/*", handle)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Could not create your team — try again.", timeout=20_000)
    expect(page.locator("body")).to_contain_text("Try again", timeout=10_000)


def test_welcome_mode_provision_401_clears_session_and_redirects(page: Page) -> None:
    """#1566/#1511 semantic: a 401 from tenant-provision means the session is
    stale — the app clears it and goes to /auth (never an error card or a
    stuck state)."""
    import urllib.parse as _up
    import time as _time
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
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if "functions/v1/tenant-provision" in url and route.request.method == "POST":
            route.fulfill(status=401, content_type="application/json",
                          body=json.dumps({"error": "Unauthorized"}))
            return
        if url.startswith(AUTH_HOST):
            from tests.e2e.test_session_login_flow import AUTH_ORIGIN
            resp = page.request.get(AUTH_ORIGIN + url[len(AUTH_HOST):])
            route.fulfill(status=resp.status, content_type="text/html", body=resp.text())
            return
        if url.startswith(APP_HOST):
            from tests.e2e.test_session_login_flow import DASHBOARD_URL
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            ctype = "application/javascript" if local.endswith(".js") else ("text/css" if local.endswith(".css") else "text/html")
            resp = page.request.get(local)
            route.fulfill(status=resp.status, content_type=ctype, body=resp.body())
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
    import urllib.parse as _up
    import time as _time
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
            if url.endswith("/v1/teams"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([{"team_id": "team_frag", "name": "Frag Team"}]))
                return
            if url.endswith("/v1/session/key"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"key": "tt_frag_key_1234567890abcdef", "team_id": "team_frag"}))
                return
            if url.endswith("/v1/team") or url.endswith("/v1/team/"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"team_id": "team_frag", "name": "Frag Team", "tier": "free"}))
                return
            if url.endswith("/v1/team/keys"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"keys": []}))
                return
            if url.endswith("/v1/sessions"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"sessions": []}))
                return
            if url.endswith("/backups"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"backups": []}))
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if url.startswith(AUTH_HOST):
            from tests.e2e.test_session_login_flow import AUTH_ORIGIN
            resp = page.request.get(AUTH_ORIGIN + url[len(AUTH_HOST):])
            route.fulfill(status=resp.status, content_type="text/html", body=resp.text())
            return
        if url.startswith(APP_HOST):
            from tests.e2e.test_session_login_flow import DASHBOARD_URL
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            ctype = "application/javascript" if local.endswith(".js") else ("text/css" if local.endswith(".css") else "text/html")
            resp = page.request.get(local)
            route.fulfill(status=resp.status, content_type=ctype, body=resp.body())
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


def test_logout_redirects_to_auth(page: Page) -> None:
    """#1511 (VGATE P1): a signed-in user clicking Log out is redirected to
    /auth — the key-only card is gone, so sign-out must land on the login
    page, never the dead redirect shell. Requires the loop harness: a valid
    session cookie → dashboard renders → Log out → /auth."""
    _wire_prod_domains(page)
    page.context.add_cookies([{
        "name": "sb-tortoise-auth-token",
        "value": urllib.parse.quote(json.dumps(_session_json())),
        "domain": ".premiselabs.co", "path": "/",
    }])
    page.add_init_script(f"window.__AUTH_BASE_URL = '{AUTH_HOST}';")
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=20_000)
    page.locator(".account-blob-btn").click()
    expect(page.locator(".account-menu-logout")).to_be_visible()
    page.locator(".account-menu-logout").click()
    expect(page).to_have_url(re.compile(rf"^{re.escape(AUTH_HOST)}/auth"), timeout=20_000)
