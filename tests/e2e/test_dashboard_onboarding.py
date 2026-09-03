"""#1643 onboarding wizard e2e (RUN_DASHBOARD_E2E opt-in, two-origin harness).

Journey coverage: first-timer wizard steps (harness → skills → GitHub →
seed → done), the harness copy, the STATE seed (Object + aboutObject point),
completion (onboarding_complete), and the returning empty-graph re-entry
card.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse

import pytest
from playwright.sync_api import Page, expect

if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("dashboard e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

from tests.e2e.test_session_login_flow import APP_HOST, AUTH_HOST, DASHBOARD_URL, _proxy_body


def _session(user_id: str) -> dict:
    return {"access_token": "fake.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sig",
            "refresh_token": "rt", "expires_in": 3600,
            "expires_at": int(time.time()) + 3600, "token_type": "bearer",
            "user": {"id": user_id, "email": f"{user_id}@premise-labs.dev",
                     "user_metadata": {"display_name": "Onboarding Test"}}}


def _seed_cookie(page: Page, user_id: str) -> None:
    page.context.add_cookies([{"name": "sb-tortoise-auth-token",
                               "value": urllib.parse.quote(json.dumps(_session(user_id))),
                               "domain": ".premiselabs.co", "path": "/"}])


def _wire(page: Page, *, provision: bool, seed_objects: list = None, onboarding_state: dict | None = None) -> dict:  # noqa: RUF013
    """Route harness: the API mocks for the wizard journey. Returns the
    capture dict ({objects, points, state_patches, org_create, checkpoint})."""
    cap = {"objects": [], "points": [], "state": [], "org_create": [], "checkpoint": []}
    seed_objects = seed_objects or [{"id": "obj-1", "name": "Onboarding Test", "objectKind": "project", "status": "in_progress"}]

    def handle(route):
        url = route.request.url
        method = route.request.method
        # #1828: loadAll pins ?team_id= on overview reads — match on the
        # query-stripped path so /v1/team/keys?team_id=… still resolves.
        path = url.split("?", 1)[0]
        if "api.premiselabs.co" in url:
            if path.endswith("/v1/teams") and method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([{"team_id": "team_o", "name": "Onboarding Test"}]))
                return
            if path.endswith("/v1/session/key") and method == "POST":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"key": "tt_onb_key_1234567890abcdef", "team_id": "team_o"}))
                return
            if path.endswith("/v1/team") or path.endswith("/v1/team/"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"team_id": "team_o", "name": "Onboarding Test",
                                               "tier": "free", "graph_ready": True, "point_count": 0,
                                               "subscription_status": "active",
                                               "checkout_price_ids": {"solo": "price_solo", "pro": "price_pro", "team": "price_team"},
                                               "write_ops_limit": 1000, "write_ops_used": 0}))
                return
            if path.endswith("/v1/sessions") or path.endswith("/v1/team/keys") or path.endswith("/backups"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"sessions": [], "keys": [], "backups": []}))
                return
            if path.endswith("/v1/objects") and method == "POST":
                body = json.loads(route.request.post_data or "{}")
                cap["objects"].append(body)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(seed_objects[0]))
                return
            if path.endswith("/v1/points") and method == "POST":
                body = json.loads(route.request.post_data or "{}")
                cap["points"].append(body)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"id": "point-1", "content": body.get("content", "")}))
                return
            if path.endswith("/v1/onboarding/state") and method == "PATCH":
                cap["state"].append(json.loads(route.request.post_data or "{}"))
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"onboarding_complete": True}))
                return
            # #1997 (W1): the org-create step posts to /v1/onboarding/team.
            # Returning users (this journey) already created their org — the
            # one-shot team_created guard answers 409 → the wizard advances.
            if path.endswith("/v1/onboarding/team") and method == "POST":
                cap["org_create"].append(json.loads(route.request.post_data or "{}"))
                route.fulfill(status=409, content_type="application/json",
                              body=json.dumps({"detail": "Sub-team already created"}))
                return
            # #1997 (W1): fork set-once + catalog-presented checkpoint writes.
            if path.endswith("/v1/onboarding/state/checkpoint") and method == "POST":
                body = json.loads(route.request.post_data or "{}")
                cap["checkpoint"].append(body)
                fork = body.get("fork")
                onboarding = {"fork": fork, "status": "active",
                              "onboarding_complete": False, "completed_steps": []}
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"created_steps": [], "noop_steps": [],
                                                "onboarding": onboarding}))
                return
            if path.endswith("/v1/onboarding/github/connect") and method == "POST":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"auth_url": "https://github.com/login/oauth/authorize?fake"}))
                return
            if path.endswith("/v1/onboarding/github/status"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"connected": False, "repos_count": None}))
                return
            if path.endswith("/v1/teams") or path.endswith("/v1/team/keys") or path.endswith("/v1/sessions") or path.endswith("/backups"):
                route.fulfill(status=401, content_type="application/json", body="{}")
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if url.startswith(AUTH_HOST):
            from tests.e2e.test_session_login_flow import AUTH_ORIGIN
            _proxy_body(route, AUTH_ORIGIN + url[len(AUTH_HOST):], page)
            return
        if url.startswith(APP_HOST):
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            ctype = "application/javascript" if local.endswith(".js") else ("text/css" if local.endswith(".css") else "text/html")
            resp = page.request.get(local)
            route.fulfill(status=resp.status, content_type=ctype, body=resp.body())
            return
        route.continue_()

    page.route("**/*", handle)
    return cap


def test_first_timer_wizard_human_steps(page: Page) -> None:
    """#1997 (W1): a returning-style session (team exists, empty graph)
    walks the NEW 5 HUMAN steps (epic plan P1): orientation → org-create/join
    → fork card → connect-consent → done. Org-create 409s (one-shot
    team_created — the org already exists) → advances. The done step exits
    WITHOUT patching onboarding_complete (accept-and-drop: the node's
    fork-aware gate owns completion)."""
    _seed_cookie(page, "u-onb")
    cap = _wire(page, provision=False)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    # Re-entry card (empty graph) → Continue setup opens the wizard at
    # step 0 (orientation — per the plan, orientation IS a wizard step).
    expect(page.locator("body")).to_contain_text("Continue setup", timeout=20_000)
    page.get_by_role("button", name="Continue setup").click()
    # STEP 0: orientation.
    expect(page.locator("body")).to_contain_text("Orientation", timeout=15_000)
    expect(page.locator("body")).to_contain_text("What you're setting up", timeout=5_000)
    page.get_by_role("button", name="Continue →").click()
    # STEP 1: create/join org — name REQUIRED + editable prefill (DE2E-3);
    # submit 409 (already created) → advance.
    expect(page.locator("body")).to_contain_text("Create your Organization", timeout=10_000)
    expect(page.locator("body")).to_contain_text("Organization name", timeout=5_000)
    page.get_by_role("button", name="Create Organization").click()
    expect(page.locator("body")).to_contain_text("Choose how you'll use Tortoise", timeout=10_000)
    # STEP 2: fork card — self-use (presentation fork, once per org).
    expect(page.locator("body")).to_contain_text("Use it for your own agents", timeout=5_000)
    page.get_by_role("button", name="Use it for your own agents").click()
    expect(page.locator("body")).to_contain_text("Connect your agent", timeout=10_000)
    assert any(c.get("fork") == "self" for c in cap["checkpoint"]), f"fork not checkpointed: {cap['checkpoint']}"
    # STEP 3: connect-consent — the universal command (harness tabs + copy).
    expect(page.locator(".harness-tab")).to_have_count(4)
    page.locator(".harness-tab", has_text="Claude Code").click()
    page.get_by_role("button", name="Copy setup").click()
    expect(page.locator("body")).to_contain_text("Copied", timeout=5_000)
    page.get_by_role("button", name="Skip for now").click()
    # STEP 4: done — agent takes over; NO onboarding_complete PATCH (the
    # node's gate owns completion; accept-and-drop).
    expect(page.locator("body")).to_contain_text("You're all set", timeout=10_000)
    page.get_by_role("button", name="Open my dashboard →").click()
    assert not any("onboarding_complete" in p for p in cap["state"]), \
        f"done step must NOT patch onboarding_complete: {cap['state']}"


def test_first_timer_wizard_build_fork_marks_catalog(page: Page) -> None:
    """#1997 (W1, review P1 regression): picking the BUILD fork on the fork
    card must mark catalog-presented via the checkpoint (the render-time
    effect cannot observe the fresh pick — React batches the fork-chosen +
    advance states — so the handler fires it directly). The build-fork gate
    (harness-connected + first-points-filed + catalog-presented) must be
    evaluable."""
    _seed_cookie(page, "u-bld")
    cap = _wire(page, provision=False)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Continue setup", timeout=20_000)
    page.get_by_role("button", name="Continue setup").click()
    expect(page.locator("body")).to_contain_text("Orientation", timeout=15_000)
    page.get_by_role("button", name="Continue →").click()
    expect(page.locator("body")).to_contain_text("Create your Organization", timeout=10_000)
    page.get_by_role("button", name="Create Organization").click()
    # fork card — pick BUILD (the build branch renders the catalog)
    expect(page.locator("body")).to_contain_text("Build an application on top", timeout=10_000)
    page.get_by_role("button", name="Build an application on top").click()
    # the capability catalog renders on step 2 (build stays; the user
    # reviews what they can build on, then continues) — W8 (#2004): the
    # registry endpoint backs it; these text pins match the canonical names.
    expect(page.locator("body")).to_contain_text("Build catalog", timeout=10_000)
    expect(page.locator("body")).to_contain_text("Session recorder", timeout=5_000)
    page.get_by_role("button", name="Continue →").click()
    expect(page.locator("body")).to_contain_text("Connect your agent", timeout=10_000)
    assert any(c.get("fork") == "build" for c in cap["checkpoint"]), \
        f"build fork not checkpointed: {cap['checkpoint']}"
    assert any(c.get("step") == "catalog-presented" for c in cap["checkpoint"]), \
        f"catalog-presented not marked: {cap['checkpoint']}"
