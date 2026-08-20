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
