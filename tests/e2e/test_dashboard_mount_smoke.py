"""#2709 (P0) authed-mount regression guard (RUN_DASHBOARD_E2E opt-in).

Boot smoke for the TDZ bug class (#2449 / #2621 / #2709): the #2698 wizard
"auto-open key modal" effect listed `harnessKey` — a `const` declared ~4,500
lines later in App() — in its deps array. Deps arrays are evaluated eagerly
during render, so App() threw

    ReferenceError: Cannot access ... before initialization

on EVERY render and the dashboard white-screened for every signed-in user.
Unauthenticated loads redirect before App mounts, so the login gate never
exercised the crash path — which is exactly why the deploy gate missed it.

This test mounts the REAL dashboard (the committed dist served on :8790 by the
dashboard-e2e two-server harness) with a cookie-seeded session + mocked
/v1/* API and asserts BOTH:
  (a) real shell content renders — the app nav "Graphs" link (the same bar
      test_keys_table_mixed.py boots against), and
  (b) ZERO page errors and ZERO console.error entries.
The console/page-error assertion is the sharp net for this class: an uncaught
render exception (React 19 unmounts the tree) OR any error surfaced after
partial render fails the test even if some text still appears. The pre-#2709
bundle fails (a) with the TDZ ReferenceError as a pageerror + console error;
a partial-render regression still fails (b).
"""
from __future__ import annotations

import json
import os
import urllib.parse

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_session_login_flow import (
    API_HOST,
    APP_HOST,
    AUTH_HOST,
    DASHBOARD_URL,
    _proxy_body,
    _session_json,
)

if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("dashboard e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

AUTH_ORIGIN = os.environ.get("DASHBOARD_AUTH_BASE", "http://127.0.0.1:8788")

TEAM_ID = "team_smoke2709"
TEAM_ROW = {
    "team_id": TEAM_ID,
    "name": "Mount Smoke",
    "tier": "free",
    "anon": False,
    # role:'owner' → isOwnerAdmin True. Note: the #2709 effect's owner-gated
    # BRANCH also needs wizardStep === 2 (only reached under /welcome), which
    # this boot smoke does not drive — the crash is caught regardless because
    # React evaluates the deps array on EVERY render of App().
    "role": "owner",
}


def _wire_routes(page: Page) -> None:
    """Minimal API mock: the reads the shell needs to render (teams row →
    team → empty keys/sessions/backups/graphs/alerts/identity), default 401
    for everything else so no real network round trip occurs."""

    def handle(route):
        url = route.request.url
        if url.startswith(API_HOST):
            path = urllib.parse.urlsplit(url).path
            if path.endswith("/v1/teams") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([TEAM_ROW]))
                return
            if path.endswith("/v1/team") or path.endswith("/v1/team/"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({**TEAM_ROW, "graph_ready": True,
                                               "point_count": 0,
                                               "subscription_status": "active"}))
                return
            if (path.endswith("/v1/sessions") or path.endswith("/v1/team/keys")
                    or path.endswith("/backups")):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"sessions": [], "keys": [], "backups": []}))
                return
            # shell mount reads — 200 shapes proven by the sibling suites
            # (test_graphs_management / test_dashboard_identity) so the smoke
            # asserts a genuinely clean console (no artifact 401s).
            if path.endswith("/v1/graphs") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json", body="[]")
                return
            if path.endswith("/v1/graphs/trash") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json", body="[]")
                return
            if "/v1/teams/" in path and path.endswith("/members") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json", body="[]")
                return
            if path.endswith("/v1/team/alerts"):
                route.fulfill(status=200, content_type="application/json", body="[]")
                return
            if path.endswith("/v1/onboarding/state") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"onboarding": {"onboarding_complete": True}}))
                return
            if path.endswith("/v1/user/identity") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"methods": [], "login_methods": 0,
                                               "banner": {"show": False}}))
                return
            if path.endswith("/v1/session/key") and route.request.method == "POST":
                # #2167 zero-mint tripwire (same as the sibling suites).
                route.fulfill(status=500, content_type="application/json",
                              body=json.dumps({"detail": "#2167 zero-mint tripwire"}))
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


def _wire_boot(page: Page) -> None:
    """Authed boot: route mock + a cookie-seeded session."""
    _wire_routes(page)
    page.context.add_cookies([{
        "name": "sb-tortoise-auth-token",
        "value": urllib.parse.quote(json.dumps(_session_json("u-smoke2709"))),
        "domain": ".premiselabs.co", "path": "/",
    }])


def test_authed_mount_renders_with_zero_console_errors(page: Page) -> None:
    """#2709: a signed-in user's boot must render the Overview surface with
    ZERO page errors / console errors (regression: TDZ ReferenceError from the
    wizard effect deps white-screened every authenticated mount)."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on("console", lambda msg: (errors.append(f"console.{msg.type}: {msg.text}")
                                    if msg.type == "error" else None))
    _wire_boot(page)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    try:
        # (a) the Overview surface rendered — the sr-only live region flips to
        # "Overview loaded" only once team + graphs + members + backups all
        # resolve (all mocked above). This is the deterministic post-mount
        # barrier: no fixed sleep, and it asserts the actual user journey
        # rather than a tab-bar label that renders on every tab.
        expect(page.locator('p[role="status"]')).to_contain_text(
            "Overview loaded", timeout=25_000)
    except AssertionError as exc:
        # surface the captured TDZ error (if any) alongside the timeout — on the
        # buggy bundle the body is empty and only this makes the regression
        # legible in CI output.
        raise AssertionError(
            f"{exc}\n#2709: authed mount did not reach the Overview surface. "
            f"console/page errors: {errors or '(none captured)'}") from exc
    # (b) zero console/page errors — the sharp net for uncaught render throws
    # (this also covers errors surfaced by post-mount effects).
    assert errors == [], \
        "#2709 regression guard: authed mount produced errors:\n" + "\n".join(errors)


def test_unauthed_load_redirects_to_auth(page: Page) -> None:
    """#2709 (guard companion): the UNAUTHENTICATED path must still redirect
    to the auth origin — the fix must not break the login gate (the gate is
    what hid the bug in the first place; keep it pinned)."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on("console", lambda msg: (errors.append(f"console.{msg.type}: {msg.text}")
                                    if msg.type == "error" else None))
    _wire_routes(page)
    # no cookie seeded — plain anonymous load
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    # redirect target: AUTH_HOST /auth — the proxy serves the local auth site
    # (wrangler :8788); assert navigation happened away from the app origin.
    page.wait_for_url("**/auth**", timeout=20_000)
    assert page.url.startswith(AUTH_HOST), f"expected auth redirect, got {page.url}"
    assert errors == [], "unauthed redirect produced page errors:\n" + "\n".join(errors)
    assert errors == [], "unauthed redirect produced page errors:\n" + "\n".join(errors)
