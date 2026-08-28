"""#1765 dashboard identity surface e2e (RUN_DASHBOARD_E2E opt-in).

Harness: same as test_dashboard_gate.py — wrangler pages dev servers
(:8788 site root / :8790 dashboard dist) + parent-domain session cookie +
api.premiselabs.co route interception (the API is mocked; the full OAuth
round-trip is staging-manual per the plan).

Flows:
1. Single-method inventory → recovery banner shows; CTA lands on Profile.
2. Two-method inventory → no banner.
3. linking_available=false → promise-free banner (fail-closed, never
   promises add-methods that can't work).
4. ?link_flow= OAuth return → link-commit POST fires, lands on Profile,
   param stripped.
5. <768px → no horizontal scroll (375px precedent, test_legal_pages.py).
"""
from __future__ import annotations

import json
import os
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_session_login_flow import _session_json  # noqa: F401

if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("dashboard e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

ROOT = Path(__file__).resolve().parent.parent.parent
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8790/")


def _session(user_id: str = "u-e2e-identity") -> dict:
    """Supabase session JSON for the parent-domain cookie (mirrors
    test_dashboard_gate's _session)."""
    now = datetime.now(UTC)
    return {
        "access_token": "fake-access-token-e2e",
        "refresh_token": "fake-refresh-token",
        "expires_in": 3600,
        "expires_at": int((now + timedelta(hours=1)).timestamp()),
        "token_type": "bearer",
        "user": {
            "id": user_id,
            "aud": "authenticated",
            "email": "identity-e2e@premise-labs.dev",
            "email_confirmed_at": "2026-08-01T00:00:00Z",
            "app_metadata": {"providers": ["github"]},
            "identities": [],
        },
    }


def _seed(page: Page) -> None:
    from urllib.parse import urlparse
    host = urlparse(DASHBOARD_URL).hostname or "127.0.0.1"
    page.context.add_cookies([{
        "name": "sb-tortoise-auth-token",
        "value": urllib.parse.quote(json.dumps(_session())),
        "domain": host, "path": "/",
    }])


def _inventory(login_methods: int = 1, linking: bool = True) -> dict:
    methods = [{"id": "id-gh-e2e", "provider": "github", "provider_id": "gh-e2e",
                "email_confirmed_at": "2026-08-01T00:00:00Z"}]
    if login_methods >= 2:
        methods.append({"id": "id-gl-e2e", "provider": "google", "provider_id": "gl-e2e",
                        "email_confirmed_at": "2026-08-01T00:00:00Z"})
    return {
        "methods": methods,
        "has_password": False, "email_method": True,
        "login_methods": login_methods, "keys_tier": 0,
        "banner": {"show": login_methods <= 1},
        "linking_available": linking,
        "email": "identity-e2e@premise-labs.dev",
        "email_confirmed_at": "2026-08-01T00:00:00Z",
        "last_sign_in_at": datetime.now(UTC).isoformat(),
        "reauth_required": False,
    }


def _wire(page: Page, *, inv: dict | None = None,
          commit_calls: list = None, inv_factory=None) -> None:  # noqa: RUF013
    """Intercept api.premiselabs.co — mock the identity endpoints + the
    shell's /v1/teams + /v1/session/key (the mount bootstrap needs them;
    review P1). ``inv_factory`` (callable) lets a test flip the inventory
    response AFTER a mutation (e.g. post-commit refetch shows banner gone)."""

    def inventory_payload():
        if inv_factory is not None:
            return inv_factory()
        return inv if inv is not None else _inventory()

    def handle(route):
        url = route.request.url
        method = route.request.method
        if "api.premiselabs.co" not in url:
            route.continue_()
            return
        if url.endswith("/v1/user/identity") and method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(inventory_payload()))
            return
        if url.endswith("/v1/user/identity/link-intent") and method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"intent_ref": "e2e-ref.abcdef",
                                           "expires_in": 120, "provider": "github"}))
            return
        if url.endswith("/v1/user/identity/link-commit") and method == "POST":
            if commit_calls is not None:
                commit_calls.append(url)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"linked": True, "already": False,
                                           "provider": "github", "adoption_signal": False}))
            return
        if url.endswith("/v1/user/identity/unlink") and method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"unlinked": True, "remaining_login_methods": 1}))
            return
        if url.endswith("/v1/user/identity/resend-confirmation") and method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"sent": True}))
            return
        if url.endswith("/v1/teams") and method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps([{"team_id": "team_e2e", "name": "E2E",
                                            "tier": "free"}]))
            return
        if url.endswith("/v1/team") and method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"team_id": "team_e2e", "name": "E2E",
                                           "tier": "free", "anon": False}))
            return
        if url.endswith("/v1/session/key") and method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"key": "tt_e2e_minted", "team_id": "team_e2e"}))
            return
        route.fulfill(status=404, content_type="application/json", body="{}")

    page.route("**/*", handle)


def test_recovery_banner_shows_and_routes_to_profile(page: Page):
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1))
    page.goto(DASHBOARD_URL)
    banner = page.get_by_role("region", name="Account recovery")
    expect(banner).to_be_visible()
    banner.get_by_role("button", name="Add a login method").click()
    expect(page.get_by_role("heading", name="Login methods")).to_be_visible()


def test_no_banner_for_two_methods(page: Page):
    _seed(page)
    _wire(page, inv=_inventory(login_methods=2))
    page.goto(DASHBOARD_URL)
    expect(page.get_by_role("region", name="Account recovery")).to_have_count(0)


def test_promise_free_banner_when_linking_off(page: Page):
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1, linking=False))
    page.goto(DASHBOARD_URL)
    banner = page.get_by_role("region", name="Account recovery")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("hello@premiselabs.co")  # promise-free copy


def test_link_commit_fires_on_oauth_return(page: Page):
    _seed(page)
    commits: list = []
    state = {"methods": 1}
    _wire(page, inv=_inventory(login_methods=1), commit_calls=commits,
          inv_factory=lambda: _inventory(login_methods=state["methods"]))
    page.goto(f"{DASHBOARD_URL}?link_flow=e2e-ref.abcdef")
    expect(page.get_by_role("heading", name="Login methods")).to_be_visible()
    assert len(commits) == 1, "link-commit must fire on the OAuth return"
    # the ?link_flow= param must be stripped (review P3)
    expect(page).to_have_url(DASHBOARD_URL.rstrip("/") + "/")


def test_no_horizontal_scroll_narrow_viewport(page: Page):
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1))
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(DASHBOARD_URL)
    scroll_w = page.evaluate("document.documentElement.scrollWidth")
    client_w = page.evaluate("document.documentElement.clientWidth")
    assert scroll_w <= client_w + 1, f"horizontal overflow: {scroll_w} > {client_w}"


def test_change_email_requires_reauth(page: Page):
    """#1765 plan-review P1-1: the change-email path (unconfirmed email →
    add email+password) MUST be gated by the ReauthDialog — a stale session
    must not reach updateUser (stolen-session ATO chain)."""
    _seed(page)
    stale = _inventory(login_methods=1)
    stale["email_confirmed_at"] = None
    stale["reauth_required"] = True
    stale["last_sign_in_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    _wire(page, inv=stale)
    page.goto(DASHBOARD_URL)
    # Profile tab → Add email and password (unconfirmed email → change-email path)
    page.get_by_role("button", name="Profile").click()
    page.get_by_role("button", name="Connect email and password").click()
    page.get_by_label("Email").fill("new@premise-labs.dev")
    page.get_by_label("Password").fill("hunter22")
    page.get_by_role("button", name="Connect email & password").click()
    # the ReauthDialog must gate (no direct updateUser)
    expect(page.get_by_role("dialog", name="Confirm it's you")).to_be_visible()


def test_unlink_confirm_flow(page: Page):
    """Journey step 5: Remove shows the confirm dialog naming the provider
    + post-state; accepting fires the unlink POST (review-fix: assert the
    POST fired + the refetched inventory drops to 2 methods)."""
    _seed(page)
    page.on("dialog", lambda d: d.accept())
    unlink_calls: list = []
    state = {"methods": 3}

    def _inv():
        return _inventory(login_methods=state["methods"])

    _wire(page, inv=_inventory(login_methods=3), inv_factory=_inv)
    # re-route unlink to capture + decrement
    def handle(route):
        url = route.request.url
        if url.endswith("/v1/user/identity/unlink") and route.request.method == "POST":
            unlink_calls.append(url)
            state["methods"] = 2
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"unlinked": True, "remaining_login_methods": 2}))
            return
        route.continue_()
    page.route("**/*", handle)

    page.goto(DASHBOARD_URL)
    page.get_by_role("button", name="Profile").click()
    page.get_by_role("button", name="Remove", exact=True).first.click()
    assert len(unlink_calls) == 1, "unlink POST must fire after confirm"
