"""Playwright E2E tests for the hosted onboarding welcome page (#541).

Targets the live welcome page on the canonical auth host
(tortoise.premiselabs.co/welcome — host consolidation 2026-08-17: the
premiselabs.co copies of /welcome and the other auth/legal pages 301 to the
tortoise host; both hosts share the premise-labs Pages project).

Two test groups:
1. Static/live tests — no Supabase session needed:
   - page loads, shows loading state then the no-session error
   - the canonical prompt URL serves markdown (PROMPT_URL contract)
2. Mocked-session tests — drive the success state (harness tabs, copy
   buttons, MCP config JSON) by intercepting Supabase REST calls. These
   verify the welcome page v2 UI without needing real credentials.

Run:  python -m pytest tests/e2e/ -q
Env:   WELCOME_URL overrides the target (default https://tortoise.premiselabs.co/welcome)
       SUPABASE_URL/SUPABASE_SERVICE_KEY enable the live no-429 signup smoke
       (skipped by default — no creds in CI; see #801).

#1721: the playwright fixtures are re-declared here at MODULE scope (they
# override pytest-playwright's session-scoped ones for this file only) — the
# root-cause fix for the full-suite asyncio event-loop cascade (see the
# "module-scoped playwright chain" block below).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
import uuid
from collections.abc import Callable, Generator
from typing import Any

import pytest
from playwright.sync_api import (
    Browser,
    BrowserType,
    Page,
    Playwright,
    expect,
    sync_playwright,
)

# Canonical host for the auth surface is tortoise.premiselabs.co (host
# consolidation 2026-08-17: premiselabs.co 301s /welcome → the tortoise host).
WELCOME_URL = os.environ.get("WELCOME_URL", "https://tortoise.premiselabs.co/welcome")
PROMPT_URL = os.environ.get("PROMPT_URL", "https://premiselabs.co/onboarding-prompt.md")

# ── #1721: module-scoped playwright chain (root-cause fix) ─────────────
# pytest-playwright's `playwright` fixture is SESSION-scoped. Playwright's
# sync API (playwright/sync_api/_context_manager.py __enter__) owns a private
# asyncio loop and parks its dispatcher greenlet mid-run_until_complete; while
# parked, `loop._running` stays True and asyncio._set_running_loop(loop) is
# live on the main thread's thread-local. With a session-scoped fixture the
# loop stays "running" from this module's first page use until SESSION end —
# so in a full-suite run (`pytest tests/`, which collects tests/e2e/ early)
# every later test that calls asyncio.run() (test_abuse TestTurnstile,
# test_agent_signup, ...) dies with "asyncio.run() cannot be called from a
# running event loop" and every @pytest.mark.asyncio test (test_client_ip_
# middleware, ...) dies with "Runner.run() cannot be called from a running
# event loop" — the order-dependent cascade of #1721.
#
# sync_playwright().stop() (__exit__) closes the loop and clears the
# thread-local running loop, so owning playwright per MODULE bounds the parked
# loop to this file: after the module finishes, the main thread is clean and
# the rest of the suite runs without the cascade. Fixtures defined in a test
# module override plugin fixtures with the same name for that module only —
# the hosted / legal / signup-form e2e suites keep the plugin's session scope.
# The mirrors below match pytest_playwright's definitions 1:1 (scope module).


@pytest.fixture(scope="module")
def playwright() -> Generator[Playwright, None, None]:
    # Guard (review P1, #1721): if an EARLIER opt-in e2e module
    # (legal/signup/dashboard/hosted with RUN_LEGAL_E2E=1 etc.) already
    # parked its SESSION-scoped playwright loop in this thread, the sync
    # API's __enter__ would raise "Playwright Sync API inside the asyncio
    # loop" — skip instead of erroring. In the default suite welcome is the
    # only playwright user and runs first, so the normal path is taken.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pw = sync_playwright().start()
        yield pw
        pw.stop()
        return
    pytest.skip(
        "a session-scoped playwright loop is already parked in this thread "
        "(combined opt-in e2e run) — run tests/e2e/test_welcome_page.py "
        "separately (#1721)"
    )


@pytest.fixture(scope="module")
def browser_type(playwright: Playwright, browser_name: str) -> BrowserType:
    return getattr(playwright, browser_name)


@pytest.fixture(scope="module")
def connect_options() -> dict | None:
    return None


@pytest.fixture(scope="module")
def launch_browser(
    browser_type_launch_args: dict[str, Any],
    browser_type: BrowserType,
    connect_options: dict | None,
) -> Callable[..., Browser]:
    def launch(**kwargs: dict[str, Any]) -> Browser:
        launch_options = {**browser_type_launch_args, **kwargs}
        if connect_options:
            browser = browser_type.connect(
                **(
                    {
                        **connect_options,
                        "headers": {
                            "x-playwright-launch-options": json.dumps(launch_options),
                            **(connect_options.get("headers") or {}),
                        },
                    }
                )
            )
        else:
            browser = browser_type.launch(**launch_options)
        return browser

    return launch


@pytest.fixture(scope="module")
def browser(launch_browser: Callable[..., Browser]) -> Generator[Browser, None, None]:
    browser = launch_browser()
    yield browser
    browser.close()


@pytest.fixture(scope="module")
def browser_context_args(
    pytestconfig: Any,
    playwright: Playwright,
    device: str | None,
    base_url: str | None,
    _pw_artifacts_folder: tempfile.TemporaryDirectory,
) -> dict:
    context_args = {}
    if device:
        context_args.update(playwright.devices[device])
    if base_url:
        context_args["base_url"] = base_url

    video_option = pytestconfig.getoption("--video")
    capture_video = video_option in ["on", "retain-on-failure"]
    if capture_video:
        context_args["record_video_dir"] = _pw_artifacts_folder.name

    return context_args


# ── Live/static tests (no auth) ─────────────────────────────────────


def test_welcome_page_no_session_redirects_to_auth(page: Page) -> None:
    """Without a Supabase session the page must send the visitor to the
    single auth page (/auth) after the bounded session wait — the
    no-session contract of the page (single auth surface, #1493)."""
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page).to_have_url(re.compile(r"/auth($|\?|#)"), timeout=25_000)


def test_onboarding_prompt_serves_markdown(page: Page) -> None:
    """The canonical onboarding prompt (#540) must be fetchable as markdown —
    this is the PROMPT_URL the welcome page's copyPrompt() uses."""
    resp = page.request.get(PROMPT_URL, timeout=15_000)
    assert resp.ok, f"prompt URL returned {resp.status}"
    assert "text/markdown" in (resp.headers.get("content-type") or "")
    body = resp.text()
    assert body.startswith("# Tortoise Onboarding"), "unexpected prompt body"
    assert "Q1" in body and "Q6" in body, "prompt missing question set"


def test_mcp_endpoint_rejects_unauthenticated(page: Page) -> None:
    """The MCP endpoint must 401 without a Bearer token (not 421/404) —
    regression guard for the deploy pipeline fixes (#545/#609/#610)."""
    resp = page.request.post(
        "https://api.premiselabs.co/mcp/",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        timeout=15_000,
    )
    assert resp.status == 401, f"expected 401, got {resp.status}"


# ── Mocked-session tests (welcome page v2 success state) ────────────
# Intercept the Supabase REST calls the page makes and drive the
# provisioning flow: auth.getSession → team_memberships poll →
# reveal_api_key RPC → success state with harness tabs + artifacts.


def test_welcome_signed_in_redirects_to_app(page: Page) -> None:
    """#1566: provisioning moved INTO the app — a signed-in visitor on the
    legacy welcome page (email-confirmation / OAuth callback, or a direct
    visit with a session) is redirected to app.premiselabs.co/welcome, where
    the dashboard's welcome mode provisions + reveals the key. welcome.html
    no longer provisions (except recovery mode)."""
    user_id = _fake_user_id()
    _seed_local_session(page, user_id)
    page.route(
        "**://app.premiselabs.co/**",
        lambda r: r.fulfill(
            status=200, content_type="text/html", body="<html><body>APP-WELCOME</body></html>"
        ),
    )
    page.goto(
        WELCOME_URL
        + "#access_token=fake-at&refresh_token=fake-rt&expires_in=3600&token_type=bearer",
        wait_until="domcontentloaded",
        timeout=30_000,
    )
    expect(page).to_have_url(re.compile(r"^https://app\.premiselabs\.co"), timeout=20_000)


def _fake_user_id() -> str:
    return str(uuid.uuid4())


def _seed_local_session(page: Page, user_id: str) -> None:
    """Seed a supabase-js session in localStorage under BOTH storage keys —
    the prod project ref (sb-ybetwichurajbfswfeqa) and the local CLI ref
    (127.0.0.1 → sb-127) — so the mocked tests run against either the live
    site (tortoise.premiselabs.co) or a wrangler pages dev preview (localhost:8788)."""
    page.add_init_script(f"""
      const session = JSON.stringify({{ 
        access_token: "fake-access-token",
        refresh_token: "fake-refresh-token",
        expires_in: 3600,
        expires_at: {2**31},
        token_type: "bearer",
        user: {{ id: "{user_id}", email: "e2e@premise-labs.dev" }}
      }});
      localStorage.setItem("sb-ybetwichurajbfswfeqa-auth-token", session);
      localStorage.setItem("sb-127-auth-token", session);
    """)


# ── Live signup E2E (requires real Supabase creds + session) ────────

LIVE_SIGNUP = pytest.mark.skipif(
    not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY")),
    reason="SUPABASE_URL/SUPABASE_SERVICE_KEY not set — live signup test skipped",
)


@LIVE_SIGNUP
def test_live_signup_no_429_confirmation_required(page: Page) -> None:
    """#801 live no-429 monitor (on-merge + scheduled smoke).

    Real signup against PROD through the SERVER-SIDE path (#801): the form
    posts to /v1/signup/email (hosted API → GoTrue Admin API with
    email_confirm=true — NO confirmation email is sent). The POST must return
    200 — NOT 429 (over_email_send_rate_limit / per-IP register buckets) —
    then the page auto-signs-in (auth/v1/token?grant_type=password) and
    redirects to /welcome.

    The /welcome navigation is route-blocked: a live key-reveal there would
    mint an un-deletable prod team + api_keys row + FalkorDB graph (no
    cleanup endpoint in-repo) — the monitor only needs the signup + auto
    sign-in to succeed.

    Teardown deletes the created auth user via the Admin API (best-effort;
    the FK cascade removes the placeholder team_memberships row)."""
    signup = {"status": None, "body": ""}
    token = {"status": None}

    def _on_response(resp):
        if "v1/signup/email" in resp.url and resp.request.method == "POST":
            signup["status"] = resp.status
            signup["body"] = resp.text()[:400]
        elif "token?grant_type=password" in resp.url and resp.request.method == "POST":
            token["status"] = resp.status

    page.on("response", _on_response)
    # #801: the account is created pre-confirmed, so the page redirects to
    # /welcome — block it so the welcome page's provisioning never runs.
    page.route(
        "**/welcome*",
        lambda route: route.fulfill(
            status=200, content_type="text/html", body="<html><body>ok</body></html>"
        ),
    )
    email = f"e2e-live-{uuid.uuid4().hex[:8]}@premise-labs.dev"
    password = f"E2eLivePass-{uuid.uuid4().hex[:8]}!"
    try:
        page.goto(
            "https://tortoise.premiselabs.co/signup", wait_until="domcontentloaded", timeout=30_000
        )
        page.locator("#email").fill(email)
        page.locator("#password").fill(password)
        page.locator("#btn-submit").click()
        # Direct no-429 proof: the server-side signup endpoint must accept it.
        # expect.poll is NOT available in playwright-python (JS-only) — poll
        # the captured response manually (code-review P1).
        deadline = time.time() + 30
        while signup["status"] is None and time.time() < deadline:
            page.wait_for_timeout(250)
        assert signup["status"] is not None, "no /v1/signup/email response observed"
        assert signup["status"] == 200, (
            f"live signup returned {signup['status']} — rate-limited or error: {signup['body']!r}"
        )
        # #801: created pre-confirmed → the page auto-signs-in.
        deadline = time.time() + 30
        while token["status"] is None and time.time() < deadline:
            page.wait_for_timeout(250)
        assert token["status"] is not None, "no auto sign-in (auth/v1/token) response observed"
        assert token["status"] == 200, f"auto sign-in returned {token['status']}"
        # The flow redirects to /welcome (route-blocked stub above) — the
        # redirect itself is the user-visible success state of #801.
        page.wait_for_url("**/welcome*", timeout=15_000)
        assert "email=" not in page.url and "password=" not in page.url, (
            f"credentials echoed into URL: {page.url}"
        )
    finally:
        from supabase_admin import delete_user_by_email

        delete_user_by_email(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"], email)
