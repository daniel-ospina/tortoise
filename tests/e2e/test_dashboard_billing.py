"""#1623 billing-page e2e (RUN_DASHBOARD_E2E opt-in).

Harness: same two wrangler servers + prod-domain interception as
test_session_login_flow.py (tortoise.premiselabs.co → :8788 auth page,
app.premiselabs.co → :8790 dashboard dist, api.premiselabs.co → mocked).

Flows:
1. Free team → Billing tab renders the current-plan card (plan label, usage
   cards, usage bar) + the plan grid; clicking Upgrade on a paid card POSTs
   /v1/billing/checkout with that tier's price_id (the contract).
2. Active subscriber (subscription_status=active) → the page shows the
   "Manage subscription" CTA and POSTs /v1/billing/portal.
"""
from __future__ import annotations

import json
import os
import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_session_login_flow import (
    API_HOST,
    APP_HOST,
    AUTH_HOST,
    DASHBOARD_URL,
    _session_json,
    _submit_api_key,
    _wire_prod_domains,
)

if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("dashboard e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

# The mock catalog's price ids (mirror tests/test_billing.py VALID_CATALOG).
PRO_PRICE_ID = "price_200proMM"

BILLING_ROW = {
    "subscription_status": None,
    "customer_email": "loop@premise-labs.dev",
    "checkout_price_id": PRO_PRICE_ID,
    "checkout_price_ids": {
        "solo": "price_100soloM",
        "pro": PRO_PRICE_ID,
        "team": "price_300teamM",
    },
    "write_ops_used": 1234,
    "write_ops_limit": 10000,
    "write_ops_period": "monthly",
    "point_count": 42,
    "overage_eligible": False,
    "overage_cost_usd": None,
}


def _open_billing(page: Page) -> None:
    _wire_prod_domains(page, exchange_body=_session_json(),
                       team_row=BILLING_ROW, billing_routes=True)
    _submit_api_key(page, "tt_loop_key_abcdef0123456789")
    expect(page).to_have_url(re.compile(r"^https://app\.premiselabs\.co"), timeout=20_000)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=20_000)
    page.locator("nav button", has_text="Billing").click()
    expect(page.locator("body")).to_contain_text("Billing", timeout=10_000)


def test_billing_tab_renders_plan_and_usage(page: Page) -> None:
    """The Billing tab shows the current plan (free), the usage cards and
    the usage bar from the mocked /v1/team row."""
    _open_billing(page)
    # Current-plan card: plan label + status + usage cards + usage bar.
    expect(page.locator("body")).to_contain_text("Free plan")
    expect(page.locator("body")).to_contain_text("1,234")
    expect(page.locator("body")).to_contain_text("10,000")
    expect(page.locator("body")).to_contain_text("42")
    # Plan grid: the four public tiers.
    expect(page.locator("body")).to_contain_text("Solo")
    expect(page.locator("body")).to_contain_text("Pro")
    expect(page.locator("body")).to_contain_text("Team")
    # Free card is current → "Current plan" badge, no Upgrade CTA.
    expect(page.locator(".plan-card.current")).to_contain_text("Free")


def test_upgrade_posts_checkout_with_tier_price_id(page: Page) -> None:
    """Clicking Upgrade on the Pro card POSTs /v1/billing/checkout with the
    Pro monthly price id from the mock checkout_price_ids (the contract — a
    free team has no active subscription, so checkout is the path)."""
    _open_billing(page)
    with page.expect_request(lambda r: r.url.endswith("/v1/billing/checkout")) as req_info:
        # The Pro card's Upgrade button (the Pro card contains 'Pro' + 'Upgrade').
        pro_card = page.locator(".plan-card", has_text="Pro")
        pro_card.locator("button", has_text="Upgrade").click()
    req = req_info.value
    body = json.loads(req.post_data or "{}")
    assert body.get("price_id") == PRO_PRICE_ID, body
    # The checkout URL is opened in a popup (window.open) — the page itself
    # stays; no assertion on the popup (headless may block it).


def test_active_subscriber_manage_subscription_posts_portal(page: Page) -> None:
    """An active subscriber sees 'Manage subscription' and clicking it POSTs
    /v1/billing/portal (plan changes route through the Stripe portal — the
    checkout endpoint 409s on active subscriptions by design)."""
    _wire_prod_domains(page, exchange_body=_session_json(),
                       team_row={**BILLING_ROW, "subscription_status": "active",
                                 "tier": "pro"},
                       billing_routes=True)
    _submit_api_key(page, "tt_loop_key_abcdef0123456789")
    expect(page).to_have_url(re.compile(r"^https://app\.premiselabs\.co"), timeout=20_000)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=20_000)
    # Header manage-subscription button (restored #310 surface) renders.
    expect(page.locator("button.tier-manage")).to_contain_text("Manage subscription")
    page.locator("nav button", has_text="Billing").click()
    expect(page.locator("body")).to_contain_text("Pro plan", timeout=10_000)
    expect(page.locator("body")).to_contain_text("Active")
    with page.expect_request(lambda r: r.url.endswith("/v1/billing/portal")) as req_info:
        # The prominent header Manage button (the billing row has a second
        # one for active subscribers).
        page.locator("button.tier-manage").first.click()
    req = req_info.value
    assert req.method == "POST"


# ── Welcome plan step (first-timer) ─────────────────────────────────────────
# The in-app provisioning (#1566) calls the REAL Supabase edge function + REST
# (ybetwichurajbfswfeqa.supabase.co) — mocked here so the reveal completes and
# the welcome plan step renders with server-resolved checkout_price_ids.

WELCOME_KEY = "tt_welcome_key_abcdef0123456789"


def _wire_welcome_flow(page: Page) -> None:
    """Session cookie + mocked Supabase provisioning + API surface for the
    first-timer welcome flow (no teams → in-app provision → reveal → plan
    step → dashboard)."""
    import time as _time
    import urllib.parse as _up
    sess = {"access_token": "fake-welcome-access-token",
            "refresh_token": "rt", "expires_in": 3600,
            "expires_at": int(_time.time()) + 3600, "token_type": "bearer",
            "user": {"id": "u-welcome", "email": "welcome@premise-labs.dev",
                     "app_metadata": {}, "user_metadata": {}}}
    page.context.add_cookies([{"name": "sb-tortoise-auth-token",
                               "value": _up.quote(json.dumps(sess)),
                               "domain": ".premiselabs.co", "path": "/"}])

    from tests.e2e.test_session_login_flow import AUTH_ORIGIN, _proxy_body

    def handle(route):
        url = route.request.url
        if "supabase.co" in url:
            if "/functions/v1/tenant-provision" in url and route.request.method == "POST":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"api_key": WELCOME_KEY,
                                                "team_name": "Welcome Team",
                                                "graph_name": "main"}))
                return
            if "/rest/v1/team_memberships" in url:
                # maybeSingle() → PostgREST returns the single object.
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"team_id": "team_welcome",
                                                "team_name": "Welcome Team",
                                                "graph_name": "main",
                                                "status": "active"}))
                return
            if "/rest/v1/rpc/reveal_api_key" in url:
                # rpc returning a text scalar → JSON-quoted string.
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(WELCOME_KEY))
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if url.startswith(API_HOST):
            if url.endswith("/v1/teams"):
                # First-timer: NO teams → the welcome card + wizard render
                # (no auto-provision at mount — #2323 Option B).
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([]))
                return
            if url.endswith("/v1/onboarding/state") and route.request.method == "GET":
                # #1885: the shell calls this FIRST — a 401 catch-all would
                # show the generic error card before the wizard renders.
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"onboarding": {}}))
                return
            if url.endswith("/v1/team") or url.endswith("/v1/team/"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(BILLING_ROW))
                return
            if url.endswith("/v1/billing/checkout") and route.request.method == "POST":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"checkout_url": "https://checkout.stripe.com/c/pay/test_456"}))
                return
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


def test_welcome_reveal_shows_orientation_then_dashboard_exit(page: Page) -> None:
    """A first-timer (no teams) is NOT auto-provisioned at mount (#2323
    Option B) — the W1 (#1997) wizard ORIENTATION renders first, then the
    org-create step provisions in-app with the typed name (tenant-provision
    201). The welcome heading flips to the provisioned org and the header
    'Open my dashboard →' exit (enabled once an org exists) opens the
    dashboard at /."""
    _wire_welcome_flow(page)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    # Teamless first-timer: welcome card + orientation (no key yet, no
    # auto-provision at mount).
    expect(page.locator("body")).to_contain_text("Welcome to Tortoise", timeout=25_000)
    # W1 interposes the orientation step (wizardFlow.js WIZARD_STEPS[0] —
    # title 'Orientation' + the intro list; 'Choose how you'll use it' is the
    # orientation-unique item).
    expect(page.locator("body")).to_contain_text("Orientation", timeout=15_000)
    expect(page.locator("body")).to_contain_text("Choose how you'll use it", timeout=5_000)
    # Org-create step: type the org name → the SUBMIT provisions
    # (tenant-provision with the typed name; 201 carries the plaintext).
    page.get_by_role("button", name="Continue →").click()
    expect(page.locator("body")).to_contain_text("Create your Organization", timeout=10_000)
    page.get_by_label("Organization name").fill("acme")
    page.get_by_role("button", name="Create Organization").click()
    # Provisioned: the welcome heading flips to the org — the header exit is
    # enabled once an org exists.
    expect(page.locator("body")).to_contain_text("Welcome Team is set up", timeout=20_000)
    # Escape hatch: the header 'Open my dashboard →' (enabled once the org
    # exists) → dashboard shell at /. Scoped to the header — the done-step
    # wizard carries a same-named button.
    page.locator("header").get_by_role("button", name="Open my dashboard →").click()
    expect(page).to_have_url(re.compile(r"^https://app\.premiselabs\.co/$"), timeout=15_000)
    expect(page.locator("body")).to_contain_text("API Keys", timeout=15_000)
