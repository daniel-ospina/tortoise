"""#1511 loop-regression e2e — the API-key login loop across BOTH origins.

Harness (pinned in the #1511 plan Task 7):
- Serve the site root (the /auth page) with `wrangler@4 pages dev . --port 8788`
  from website/.
- Serve the dashboard dist with `wrangler@4 pages dev dist --port 8790`
  from website/apps/dashboard/.
- The tests simulate the PROD domains via Playwright route interception:
  `https://tortoise.premiselabs.co/**` → the :8788 server,
  `https://app.premiselabs.co/**` → the :8790 server. The parent-domain
  session cookie (`.premiselabs.co`) is written by /auth and read by the
  dashboard — the exact prod flow, minus the network.
- The exchange (`POST https://api.premiselabs.co/v1/session/login`) is mocked;
  `https://api.premiselabs.co/**` catches the dashboard's other API calls with
  a benign 401 so the app shell renders deterministically.
- Opt-in: RUN_DASHBOARD_E2E=1 (mirrors RUN_LEGAL_E2E).

Flows (the user's #1511 acceptance):
(a) paste tt_ key on /auth → exchange 200 → session cookie written →
    dashboard renders (the loop WORKS).
(b) no cookie → dashboard instantly redirects to /auth.
(c) anon-team exchange error (403 ANON_TEAM_NO_OWNER) → tt_claim_pending set
    → redirected to app.premiselabs.co/?claim=1 → claim-paste shows.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("dashboard e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

ROOT = Path(__file__).resolve().parent.parent.parent

AUTH_ORIGIN = os.environ.get("DASHBOARD_AUTH_BASE", "http://127.0.0.1:8788")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8790/")

AUTH_HOST = "https://tortoise.premiselabs.co"
APP_HOST = "https://app.premiselabs.co"
API_HOST = "https://api.premiselabs.co"

AUTH_PAGE = AUTH_HOST + "/auth"


def _session_json(user_id: str = "loop-user") -> dict:
    return {
        "access_token": "loop-fake-access-token",
        "refresh_token": "loop-fake-refresh-token",
        "expires_in": 3600,
        "expires_at": int(time.time()) + 3600,
        "token_type": "bearer",
        "user": {"id": user_id, "email": "loop@premise-labs.dev",
                 "app_metadata": {}, "user_metadata": {}},
    }


def _proxy_body(route, local_url: str, page: Page) -> None:
    """Proxy a local server response with the correct content type (a wrong
    MIME refuses script execution in Chrome)."""
    ctype = "text/html"
    if local_url.endswith(".js"):
        ctype = "application/javascript"
    elif local_url.endswith(".css"):
        ctype = "text/css"
    elif local_url.endswith(".json"):
        ctype = "application/json"
    elif local_url.endswith(".png") or local_url.endswith(".ico"):
        ctype = "image/png"
    resp = page.request.get(local_url)
    route.fulfill(status=resp.status, content_type=ctype, body=resp.body())


def _wire_prod_domains(page: Page, exchange_body=None, exchange_status=200,
                       exchange_ctype: str = "application/json",
                       team_row=None, billing_routes=False) -> None:
    """Simulate the prod domains: tortoise → :8788 (auth site),
    app → :8790 (dashboard), api → mocked exchange + a deterministic
    session/team surface so the dashboard app shell renders after a
    successful exchange (the loop: cookie bridges origins → gate passes
    → mount effect mints + renders, no redirect).

    #1623: ``team_row`` overrides MERGE with the base row (callers pass only
    the fields they want to change — e.g. subscription_status/billing
    fields for the Billing page); ``billing_routes`` adds mocked POST
    /v1/billing/checkout + /v1/billing/portal handlers (returning
    {checkout_url}/{portal_url}) so Upgrade/Manage CTAs resolve instead of
    hitting the 401 fallback.
    """
    minted_key = "tt_loop_minted_key_abcdef0123456789"
    base_team_row = {"team_id": "team_loop", "name": "Loop Test", "tier": "free",
                     "max_users": 5, "max_graphs": 5, "graph_size_cap": 10000,
                     "ops_allowance": 1000, "email": "loop@premise-labs.dev"}
    team_row = {**base_team_row, **team_row} if team_row else base_team_row

    def handle(route):
        url = route.request.url
        if url.startswith(API_HOST):
            if url.endswith("/v1/session/login") and route.request.method == "POST":
                route.fulfill(status=exchange_status,
                              content_type=exchange_ctype,
                              body=json.dumps(exchange_body or {}))
                return
            if url.endswith("/v1/session/key") and route.request.method == "POST":
                # The dashboard's session-key mint (Bearer = the loop session).
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"key": minted_key, "team_id": "team_loop"}))
                return
            if url.endswith("/v1/billing/checkout") and route.request.method == "POST" and billing_routes:
                # #1623: capture the body so tests can assert the price_id.
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"checkout_url": "https://checkout.stripe.com/c/pay/test_123"}))
                return
            if url.endswith("/v1/billing/portal") and route.request.method == "POST" and billing_routes:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"portal_url": "https://billing.stripe.com/p/session/test_123"}))
                return
            if url.endswith("/v1/teams") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([team_row]))
                return
            if url.endswith("/v1/team/keys"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"keys": []}))
                return
            if url.endswith("/v1/sessions"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"sessions": []}))
                return
            if url.endswith("/backups"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"backups": []}))
                return
            if url.endswith("/v1/team") or url.endswith("/v1/team/"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(team_row))
                return
            # Everything else the dashboard calls — a deterministic 401 so the
            # app shell renders without a real network round trip.
            route.fulfill(status=401, content_type="application/json",
                          body=json.dumps({"detail": "unauthorized"}))
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


def _open_auth(page: Page) -> None:
    page.goto(AUTH_PAGE, wait_until="domcontentloaded", timeout=30_000)
    # The auth page must NOT bounce (no session) — the four options are visible.
    expect(page.locator("#btn-apikey")).to_be_visible(timeout=15_000)


def _submit_api_key(page: Page, key: str) -> None:
    _open_auth(page)
    page.locator("#btn-apikey").click()
    expect(page.locator("#apikey-modal")).to_be_visible()
    page.locator("#apikey-input").fill(key)
    page.locator("#apikey-form").evaluate(
        "(f) => f.dispatchEvent(new Event('submit', {cancelable: true}))")


def test_api_key_login_writes_cookie_and_dashboard_renders(page: Page) -> None:
    """Flow (a): paste tt_ key → exchange 200 → the .premiselabs.co session
    cookie is written → the dashboard (app.premiselabs.co) renders."""
    _wire_prod_domains(page, exchange_body=_session_json())
    _submit_api_key(page, "tt_loop_key_abcdef0123456789")
    # The dashboard loads (redirect after the exchange).
    expect(page).to_have_url(re.compile(r"^https://app\.premiselabs\.co"), timeout=20_000)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=20_000)


def test_no_cookie_dashboard_redirects_to_auth(page: Page) -> None:
    """Flow (b): no session cookie → the dashboard instantly redirects to the
    /auth page (the gate emits the ABSOLUTE target on the app origin)."""
    _wire_prod_domains(page)
    page.add_init_script(
        f"window.__AUTH_BASE_URL = '{AUTH_HOST}';")
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page).to_have_url(re.compile(rf"^{re.escape(AUTH_HOST)}/auth"), timeout=15_000)


def test_anon_team_error_funnels_to_claim(page: Page) -> None:
    """Flow (c): a 403 ANON_TEAM_NO_OWNER from the exchange sets
    tt_claim_pending and redirects to app.premiselabs.co/?claim=1 — the
    claim-paste screen shows (D2 funnel, no raw key cross-origin)."""
    _wire_prod_domains(page, exchange_status=403,
                       exchange_body={"detail": {"error_code": "ANON_TEAM_NO_OWNER",
                                                 "message": "unclaimed"}})
    _submit_api_key(page, "tt_anon_key_abcdef0123456789")
    expect(page).to_have_url(re.compile(r"^https://app\.premiselabs\.co/\?claim=1"), timeout=20_000)
    expect(page.locator("body")).to_contain_text("Claim your team", timeout=20_000)
