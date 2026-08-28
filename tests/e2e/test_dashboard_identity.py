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
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("dashboard e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

ROOT = Path(__file__).resolve().parent.parent.parent
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8790/")


def _session(user_id: str = "u-e2e-identity", display_name: str = "danielospinabotero") -> dict:
    """Supabase session JSON for the parent-domain cookie (mirrors
    test_dashboard_gate's _session). ``display_name=None`` omits
    user_metadata entirely — the email-prefix fallback branch."""
    now = datetime.now(timezone.utc)
    user = {
        "id": user_id,
        "aud": "authenticated",
        "email": "identity-e2e@premise-labs.dev",
        "email_confirmed_at": "2026-08-01T00:00:00Z",
        "app_metadata": {"providers": ["github"]},
        "identities": [],
    }
    if display_name is not None:
        user["user_metadata"] = {"display_name": display_name}
    return {
        "access_token": "fake-access-token-e2e",
        "refresh_token": "fake-refresh-token",
        "expires_in": 3600,
        "expires_at": int((now + timedelta(hours=1)).timestamp()),
        "token_type": "bearer",
        "user": user,
    }


def _seed(page: Page, display_name: str | None = "danielospinabotero") -> None:
    from urllib.parse import urlparse
    host = urlparse(DASHBOARD_URL).hostname or "127.0.0.1"
    page.context.add_cookies([{
        "name": "sb-tortoise-auth-token",
        "value": urllib.parse.quote(json.dumps(_session(display_name=display_name))),
        "domain": host, "path": "/",
    }])


def _inventory(login_methods: int = 1, linking: bool = True) -> dict:
    methods = [{"id": "id-gh-e2e", "provider": "github", "provider_id": "gh-e2e",
                "email_confirmed_at": "2026-08-01T00:00:00Z"}]
    if login_methods >= 2:
        methods.append({"id": "id-gl-e2e", "provider": "google", "provider_id": "gl-e2e",
                        "email_confirmed_at": "2026-08-01T00:00:00Z"})
    if login_methods >= 3:
        methods.append({"id": "id-ms-e2e", "provider": "microsoft", "provider_id": "ms-e2e",
                        "email_confirmed_at": "2026-08-01T00:00:00Z"})
    return {
        "methods": methods,
        "has_password": False, "email_method": True,
        "login_methods": login_methods, "keys_tier": 0,
        "banner": {"show": login_methods <= 1},
        "linking_available": linking,
        "email": "identity-e2e@premise-labs.dev",
        "email_confirmed_at": "2026-08-01T00:00:00Z",
        "last_sign_in_at": datetime.now(timezone.utc).isoformat(),
        "reauth_required": False,
    }


def _wire(page: Page, *, inv: dict | None = None,
          commit_calls: list = None, inv_factory=None,  # noqa: RUF013
          teams: list | None = None, team_reads: list | None = None) -> None:
    """Intercept api.premiselabs.co — mock the identity endpoints + the
    shell's /v1/teams + /v1/session/key (the mount bootstrap needs them;
    review P1). ``inv_factory`` (callable) lets a test flip the inventory
    response AFTER a mutation (e.g. post-commit refetch shows banner gone).
    ``teams`` (list of team dicts) drives the team-scoped responses; when
    more than one team is supplied, /v1/session/key mints per-request team_id
    (body) and /v1/team returns the matching team (#1874 two-team regression).
    ``team_reads`` (list) records every /v1/team team_id the app requested —
    pin the post-switch read so a dropped ?team_id= pin cannot false-pass."""

    if teams is None:
        # NOTE: the shell reads t.team_name (main.jsx:452) — `name` renders empty.
        teams = [{"team_id": "team_e2e", "team_name": "E2E", "tier": "free"}]

    def team_for(team_id: str) -> dict | None:
        # #1874 test-review P1: FAIL LOUDLY on an unknown/missing team_id
        # (404) instead of silently falling back to teams[0] — a dropped
        # ?team_id= pin must surface, not mask.
        return next((t for t in teams if t["team_id"] == team_id), None)

    def inventory_payload():
        if inv_factory is not None:
            return inv_factory()
        return inv if inv is not None else _inventory()

    def handle(route):
        url = route.request.url
        # #1874 + #1828: the shell pins /v1/team?team_id=… (multi-membership
        # resolution) — matchers must be query-tolerant or every team-scoped
        # read 404s (pre-existing breakage from #1828, fixed here).
        path = url.split("?", 1)[0]
        method = route.request.method
        if "api.premiselabs.co" not in url:
            route.continue_()
            return
        if path.endswith("/v1/onboarding/state") and method == "GET":
            # #1877/#1885: the shell calls this FIRST (re-fired per #1847).
            # Returning test users are ONBOARDED — onboarding_complete:true
            # keeps the shell in the normal dashboard state (a falsy
            # onboarding dict re-triggers the setup wizard, no dashboard
            # mint, blob "No team").
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"onboarding": {"onboarding_complete": True}}))
            return
        if path.endswith("/v1/user/identity") and method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(inventory_payload()))
            return
        if path.endswith("/v1/user/identity/link-intent") and method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"intent_ref": "e2e-ref.abcdef",
                                           "expires_in": 120, "provider": "github"}))
            return
        if path.endswith("/v1/user/identity/link-commit") and method == "POST":
            if commit_calls is not None:
                commit_calls.append(route.request.post_data or "")
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"linked": True, "already": False,
                                           "provider": "github", "adoption_signal": False}))
            return
        if path.endswith("/v1/user/identity/unlink") and method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"unlinked": True, "remaining_login_methods": 1}))
            return
        if path.endswith("/v1/user/identity/resend-confirmation") and method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"sent": True}))
            return
        if path.endswith("/v1/teams") and method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(teams))
            return
        if path.endswith("/v1/team") and method == "GET":
            # #1874: key by the ?team_id= query the app pins (main.jsx
            # loadTeam) — 404 on unknown ids (fail-loud, no fallback).
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            tid = (qs.get("team_id") or ["team_e2e"])[0]
            if team_reads is not None:
                team_reads.append(tid)
            t = team_for(tid)
            if t is None:
                route.fulfill(status=404, content_type="application/json", body="{}")
                return
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"team_id": t["team_id"],
                                           "team_name": t["team_name"],
                                           "tier": t["tier"], "anon": False}))
            return
        if path.endswith("/v1/session/key") and method == "POST":
            body = json.loads(route.request.post_data or "{}")
            tid = (body.get("team_id") or "team_e2e")
            t = team_for(tid)
            if t is None:
                # fail-loud (test-review c2): unknown team_id → 400, never
                # index None inside the handler
                route.fulfill(status=400, content_type="application/json", body="{}")
                return
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"key": f"tt_minted_{t['team_id']}",
                                           "team_id": t["team_id"]}))
            return
        if "/v1/teams/" in path and path.endswith("/members") and method == "GET":
            # members tab: one owner row (the seeded user)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps([{"user_id": "u-e2e-identity",
                                            "role": "owner", "status": "active",
                                            "email": "identity-e2e@premise-labs.dev"}]))
            return
        route.fulfill(status=404, content_type="application/json", body="{}")

    page.route("**/*", handle)


def _open_account_menu(page: Page) -> None:
    # #1874: blob aria-label is `Account menu — {team}` (regex match)
    page.get_by_role("button", name=re.compile(r"Account menu")).click()


def _open_profile_via_menu(page: Page) -> None:
    # #1874: menu entry (scoped to .account-menu — avoids strict-mode
    # double-match while the nav tab still exists during the transition)
    page.locator(".account-menu").get_by_role("button", name="Profile").click()


def test_account_menu_identity_block_single_team(page: Page):
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1))
    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    expect(page.locator(".account-identity-name")).to_have_text("danielospinabotero")
    expect(page.locator(".account-identity-email")).to_have_text("identity-e2e@premise-labs.dev")
    # tier badge — scoped to the menu (the header's "free tier · Upgrade"
    # anchor is outside .account-menu)
    expect(page.locator(".account-menu .tier-badge")).to_have_text("free")
    expect(page.locator(".account-menu").get_by_role("button", name="Profile")).to_be_visible()
    expect(page.locator(".account-menu").get_by_role("button", name="Log out")).to_be_visible()
    expect(page.locator(".account-menu").get_by_text("Switch team")).to_have_count(0)


def test_account_menu_email_prefix_fallback(page: Page):
    """#1874: session WITHOUT display_name → identity block shows the
    email-prefix (identity-e2e@… → identity-e2e), not the team name."""
    _seed(page, display_name=None)
    _wire(page, inv=_inventory(login_methods=1))
    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    expect(page.locator(".account-identity-name")).to_have_text("identity-e2e")
    expect(page.locator(".account-identity-email")).to_have_text("identity-e2e@premise-labs.dev")
    expect(page.locator(".account-identity-name")).not_to_have_text("E2E")


def test_account_menu_multi_team_switch(page: Page):
    """#1874 regression guard: two-team switch list renders, aria-current on
    the active team, AND the post-switch /v1/team read is pinned to the new
    team_id (a dropped ?team_id= pin must not false-pass)."""
    _seed(page)
    teams = [
        {"team_id": "team_a", "team_name": "Alpha", "tier": "free"},
        {"team_id": "team_b", "team_name": "Bravo", "tier": "free"},
    ]
    team_reads: list = []
    _wire(page, inv=_inventory(login_methods=1), teams=teams, team_reads=team_reads)
    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    menu = page.locator(".account-menu")
    expect(menu.get_by_text("Switch team")).to_be_visible()
    expect(menu.get_by_text("Alpha")).to_be_visible()
    expect(menu.get_by_text("Bravo")).to_be_visible()
    # active team carries aria-current
    expect(menu.locator("button[aria-current='true']")).to_contain_text("Alpha")
    # switch to Bravo → active moves AND the data layer re-reads team_b
    # switch to Bravo — the ?team_id= pin must reach the API. expect_response
    # pumps the Playwright sync event loop while waiting (a Python sleep-poll
    # would block the message pump and stall the mock's response — the
    # observed 15s "stall" was exactly that artifact).
    with page.expect_response(lambda r: "/v1/team" in r.url and "team_id=team_b" in r.url,
                              timeout=15000):
        menu.get_by_role("button", name="Bravo").click()
    assert "team_b" in team_reads, f"?team_id= pin must reach the API: {team_reads}"
    _open_account_menu(page)
    expect(page.locator(".account-menu").locator("button[aria-current='true']")).to_contain_text("Bravo")


def test_members_heading_and_nav(page: Page):
    """#1874: no Profile button in nav; Members section reads Team members."""
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1))
    page.goto(DASHBOARD_URL)
    expect(page.locator("nav").get_by_role("button", name="Profile")).to_have_count(0)
    page.get_by_role("button", name="Members").click()
    expect(page.get_by_role("heading", name="Team members")).to_be_visible()



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
    assert "e2e-ref.abcdef" in commits[0], f"link-commit must carry the intent_ref: {commits}"
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
    # #1874 test-review P2: the restructured menu is the overflow-risk surface
    # — re-check with the account menu OPEN (pinned visible so a no-op click
    # cannot silently re-test the closed layout).
    _open_account_menu(page)
    expect(page.locator(".account-menu")).to_be_visible()
    scroll_w = page.evaluate("document.documentElement.scrollWidth")
    client_w = page.evaluate("document.documentElement.clientWidth")
    assert scroll_w <= client_w + 1, f"horizontal overflow with menu open: {scroll_w} > {client_w}"


def test_change_email_requires_reauth(page: Page):
    """#1765 plan-review P1-1: changing email via the add-email+password path
    (unconfirmed email) MUST be gated by the ReauthDialog — a stale session
    must not reach updateUser (stolen-session ATO chain). (The reauth
    staleness MECHANISM itself is unit-tested in identity.test.js; here we
    verify the gate fires on the change-email path.)"""
    _seed(page)
    inv = _inventory(login_methods=1)
    inv["email_confirmed_at"] = None  # unconfirmed email → change-email path
    _wire(page, inv=inv)
    page.goto(DASHBOARD_URL)
    # Profile → menu entry (nav tab removed in #1874)
    _open_account_menu(page)
    _open_profile_via_menu(page)
    page.get_by_role("button", name="Connect email and password").click()
    page.get_by_label("Email").fill("new@premise-labs.dev")
    page.get_by_label("Password").fill("hunter22")
    page.get_by_role("button", name="Connect email & password").click()
    # the ReauthDialog must gate (no direct updateUser)
    expect(page.get_by_role("dialog", name="Confirm it's you")).to_be_visible()


def test_unlink_confirm_flow(page: Page):
    """Journey step 5: Remove shows the confirm dialog NAMING the provider
    (recorded, not blindly accepted), and the post-unlink refetch drops the
    method — Remove buttons go 3 → 2 and the unlink POST body carries the
    identity row id."""
    _seed(page)
    dialogs: list = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    unlink_calls: list = []
    state = {"methods": 3}

    def _inv():
        return _inventory(login_methods=state["methods"])

    _wire(page, inv=_inventory(login_methods=3), inv_factory=_inv)
    # re-route unlink to capture + decrement — NARROW pattern so this handler
    # only intercepts the unlink POST (a **/* re-route shadows the _wire mock
    # and its continue_() fails the api requests — handler-chain gotcha).
    def handle(route):
        if route.request.method == "POST":
            unlink_calls.append(route.request.post_data or "")
            state["methods"] = 2
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"unlinked": True, "remaining_login_methods": 2}))
            return
        route.continue_()
    page.route("**/v1/user/identity/unlink*", handle)

    page.goto(DASHBOARD_URL)
    # Profile → menu entry (nav tab removed in #1874)
    _open_account_menu(page)
    _open_profile_via_menu(page)
    # 3 methods → 3 Remove buttons
    expect(page.get_by_role("button", name="Remove", exact=True)).to_have_count(3)
    page.get_by_role("button", name="Remove", exact=True).first.click()
    # the confirm dialog named the provider (regression guard: no silent unlink)
    assert len(dialogs) == 1 and "github" in dialogs[0], f"unexpected dialogs: {dialogs}"
    # the unlink POST fired with the identity row id
    assert unlink_calls and "id-gh-e2e" in unlink_calls[0], f"unlink body: {unlink_calls}"
    # post-refetch: 2 methods → 2 Remove buttons (the drop is observable)
    expect(page.get_by_role("button", name="Remove", exact=True)).to_have_count(2)
    assert len(unlink_calls) == 1, "unlink POST must fire after confirm"


def test_create_team_success(page: Page):
    """#1877: the menu entry is visible for a SINGLE-team user; the dialog
    creates a team and the dashboard switches to it."""
    _seed(page)
    teams = [{"team_id": "team_e2e", "team_name": "E2E", "tier": "free"}]
    _wire(page, inv=_inventory(login_methods=1), teams=teams)

    def handle_create(route):
        # Tight path match (the loadTeams GET + the create POST); NEVER
        # continue_() — the handler-chain fall-through hangs in this
        # Playwright build (the #1874 gotcha). /v1/teams/{id}/members etc.
        # don't match the exact path and fall to _wire directly.
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/v1/teams"):
            if route.request.method == "POST":
                name = (json.loads(route.request.post_data or "{}").get("name") or "newteam")
                teams.append({"team_id": "team_new", "team_name": name, "tier": "free"})
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"team_id": "team_new", "graph_name": "team_new",
                                               "tier": "free", "name": name}))
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(teams))
            return
        route.continue_()
    page.route("**/v1/teams", handle_create)

    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    menu = page.locator(".account-menu")
    expect(menu.get_by_role("button", name="+ Create new team")).to_be_visible()
    expect(menu.get_by_text("Switch team")).to_have_count(0)  # single-team: no switch label
    menu.get_by_role("button", name="+ Create new team").click(force=True)  # menu closes + dialog opens → unmounts
    expect(page.get_by_role("dialog", name="Create a new team")).to_be_visible()
    # validation mirrors the API (spaces rejected) — inline error, no POST
    page.get_by_label("Team name").fill("bad name")
    page.locator(".modal .btn-primary").click(force=True)
    expect(page.locator(".modal")).to_contain_text("Invalid team name", timeout=10000)
    page.get_by_label("Team name").fill("newteam")
    page.locator(".modal .btn-primary").click(force=True)  # busy-state re-render detaches the name-changed button
    # the dashboard switches to the new team (the blob shows its name)
    expect(page.get_by_role("button", name=re.compile(r"Account menu"))).to_contain_text("newteam", timeout=15000)


def test_create_team_free_capped_gate(page: Page):
    """#1877: a 402 from POST /v1/teams surfaces the gated-on-click upgrade
    UX in the dialog (message + Upgrade CTA landing on Billing)."""
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1))

    def handle_create(route):
        # Same tight-path + no-continue pattern as test_create_team_success
        # (the handler-chain fall-through hangs in this Playwright build).
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/v1/teams"):
            if route.request.method == "POST":
                route.fulfill(status=402, content_type="application/json",
                              body=json.dumps({"detail": "Create another team requires a "
                                                         "paid plan — upgrade an existing team first"}))
            else:
                # default single-team fixture shape (this test uses _wire's default)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([{"team_id": "team_e2e", "team_name": "E2E",
                                                "tier": "free"}]))
            return
        route.continue_()
    page.route("**/v1/teams", handle_create)

    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    page.locator(".account-menu").get_by_role("button", name="+ Create new team").click(force=True)  # menu closes → unmounts
    page.get_by_label("Team name").fill("blocked")
    page.locator(".modal .btn-primary").click(force=True)  # busy-state re-render
    dialog = page.get_by_role("dialog", name="Create a new team")
    expect(dialog).to_contain_text("upgrade an existing team", timeout=15000)
    expect(dialog.get_by_role("button", name="Upgrade")).to_be_visible()
    dialog.get_by_role("button", name="Upgrade").click()
    expect(page.get_by_role("heading", name="Billing")).to_be_visible()
