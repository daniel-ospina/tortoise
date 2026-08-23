"""#1643 onboarding wizard e2e (RUN_DASHBOARD_E2E opt-in, two-origin harness).

Journey coverage: first-timer wizard steps (harness → skills → GitHub →
seed → done), the harness copy, the STATE seed (Object + aboutObject point),
completion (onboarding_complete), and the returning empty-graph re-entry
card.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse

import pytest
from playwright.sync_api import Page, expect

if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("dashboard e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

from tests.e2e.test_session_login_flow import AUTH_HOST, APP_HOST, DASHBOARD_URL


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


def _wire(page: Page, *, provision: bool, seed_objects: list = None, onboarding_state: dict | None = None) -> dict:
    """Route harness: the API mocks for the wizard journey. Returns the
    capture dict ({objects, points, state_patches})."""
    cap = {"objects": [], "points": [], "state": []}
    seed_objects = seed_objects or [{"id": "obj-1", "name": "Onboarding Test", "objectKind": "project", "status": "in_progress"}]

    def handle(route):
        url = route.request.url
        method = route.request.method
        if "api.premiselabs.co" in url:
            if url.endswith("/v1/teams") and method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([{"team_id": "team_o", "name": "Onboarding Test"}]))
                return
            if url.endswith("/v1/session/key") and method == "POST":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"key": "tt_onb_key_1234567890abcdef", "team_id": "team_o"}))
                return
            if url.endswith("/v1/team") or url.endswith("/v1/team/"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"team_id": "team_o", "name": "Onboarding Test",
                                               "tier": "free", "graph_ready": True, "point_count": 0,
                                               "subscription_status": "active",
                                               "checkout_price_ids": {"solo": "price_solo", "pro": "price_pro", "team": "price_team"},
                                               "write_ops_limit": 1000, "write_ops_used": 0}))
                return
            if url.endswith("/v1/sessions") or url.endswith("/v1/team/keys") or url.endswith("/backups"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"sessions": [], "keys": [], "backups": []}))
                return
            if url.endswith("/v1/objects") and method == "POST":
                body = json.loads(route.request.post_data or "{}")
                cap["objects"].append(body)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(seed_objects[0]))
                return
            if url.endswith("/v1/points") and method == "POST":
                body = json.loads(route.request.post_data or "{}")
                cap["points"].append(body)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"id": "point-1", "content": body.get("content", "")}))
                return
            if url.endswith("/v1/onboarding/state") and method == "PATCH":
                cap["state"].append(json.loads(route.request.post_data or "{}"))
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"onboarding_complete": True}))
                return
            if url.endswith("/v1/onboarding/github/connect") and method == "POST":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"auth_url": "https://github.com/login/oauth/authorize?fake"}))
                return
            if url.endswith("/v1/onboarding/github/status"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"connected": False, "repos_count": None}))
                return
            if url.endswith("/v1/teams") or url.endswith("/v1/team/keys") or url.endswith("/v1/sessions") or url.endswith("/backups"):
                route.fulfill(status=401, content_type="application/json", body="{}")
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if url.startswith(AUTH_HOST):
            from tests.e2e.test_session_login_flow import AUTH_ORIGIN
            resp = page.request.get(AUTH_ORIGIN + url[len(AUTH_HOST):])
            route.fulfill(status=resp.status, content_type="text/html", body=resp.text())
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


def test_first_timer_wizard_harness_to_done(page: Page) -> None:
    """A returning-style session (team exists, empty graph) walks the wizard:
    the re-entry card → harness tabs + copy → skills → GitHub → seed STATE →
    done. The seed lands an Object + an aboutObject point; completion patches
    onboarding_complete."""
    _seed_cookie(page, "u-onb")
    cap = _wire(page, provision=False)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    # Re-entry card (empty graph) → Continue setup opens the wizard at the
    # skills step (per the plan); Back reaches the harness chooser.
    expect(page.locator("body")).to_contain_text("Continue setup", timeout=20_000)
    page.get_by_role("button", name="Continue setup").click()
    expect(page.locator("body")).to_contain_text("Your agent's toolkit", timeout=15_000)
    page.get_by_role("button", name="← Back").click()
    # STEP 0: harness chooser — the four harness tabs + copy.
    expect(page.locator("body")).to_contain_text("Connect your tool", timeout=15_000)
    expect(page.locator(".harness-tab")).to_have_count(4)
    page.locator(".harness-tab", has_text="Claude Code").click()
    page.get_by_role("button", name="Copy setup").click()
    expect(page.locator("body")).to_contain_text("Copied!", timeout=5_000)
    # STEP 1: skills.
    page.get_by_role("button", name="Skip →").click()
    expect(page.locator("body")).to_contain_text("Your agent's toolkit", timeout=10_000)
    expect(page.locator("body")).to_contain_text("tortoise-decide", timeout=5_000)
    expect(page.locator("body")).to_contain_text("how-to-use-tortoise", timeout=5_000)
    page.get_by_role("button", name="Next").click()
    # STEP 2: GitHub connect.
    expect(page.locator("body")).to_contain_text("Connect GitHub", timeout=10_000)
    page.get_by_role("button", name="Skip →").click()
    # STEP 3: seed STATE.
    expect(page.locator("body")).to_contain_text("Seed your graph", timeout=10_000)
    page.get_by_role("button", name="Seed a sample memory").click()
    expect(page.locator("body")).to_contain_text("Seeded", timeout=10_000)
    assert cap["objects"] and cap["objects"][0]["status"] == "in_progress", f"objects: {cap['objects']}"
    assert cap["points"] and cap["points"][0].get("about_object") == "obj-1", f"points: {cap['points']}"
    page.get_by_role("button", name="Finish").click()
    # STEP 4: done.
    expect(page.locator("body")).to_contain_text("You're set", timeout=10_000)
    page.get_by_role("button", name="Open my dashboard →").click()
    assert any("onboarding_complete" in p for p in cap["state"]), f"completion not patched: {cap['state']}"
