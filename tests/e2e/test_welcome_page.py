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

#1721: the playwright chain is module-scoped in tests/e2e/conftest.py (the
# root-cause fix for the full-suite asyncio event-loop cascade — a
# session-scoped playwright loop parked in the main thread poisoned every
# later asyncio.run()/@pytest.mark.asyncio test).
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid

import pytest
from playwright.sync_api import Page, expect

# Canonical host for the auth surface is tortoise.premiselabs.co (host
# consolidation 2026-08-17: premiselabs.co 301s /welcome → the tortoise host).
WELCOME_URL = os.environ.get("WELCOME_URL", "https://tortoise.premiselabs.co/welcome")
PROMPT_URL = os.environ.get("PROMPT_URL", "https://premiselabs.co/onboarding-prompt.md")



# ── Live/static tests (no auth) ─────────────────────────────────────


def test_welcome_page_no_session_redirects_to_auth(page: Page) -> None:
    """Without a Supabase session the page must send the visitor to the
    single auth page (/auth) after the bounded session wait — the
    no-session contract of the page (single auth surface, #1493)."""
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page).to_have_url(re.compile(r"/auth($|\?|#)"), timeout=25_000)


def test_onboarding_prompt_serves_markdown(page: Page) -> None:
    """The canonical onboarding prompt (#540) must be fetchable as markdown —
    this is the onboarding-prompt URL."""
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
    redirects to the app root (signup.html:536 WELCOME_URL =
    https://app.premiselabs.co — #1566: the post-auth destination for the
    SIGNUP flow is the CROSS-SITE app root, where first-timers are
    provisioned in welcome mode). (The login path still transits legacy
    /welcome.html — signin.html:366 — which bounces signed-in users to the
    app root; this monitor drives signup only.)

    The app-origin navigation is route-blocked: a live landing on the app
    root would run the #1566 welcome-mode provisioning and mint an
    un-deletable prod team + api_keys row + FalkorDB graph (no cleanup
    endpoint in-repo) — the monitor only needs the signup + auto sign-in to
    succeed, and the intercepted navigation still proves the redirect fired.

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
    # #1566: the account is created pre-confirmed, so the SIGNUP flow
    # redirects to the APP ROOT (signup.html WELCOME_URL =
    # https://app.premiselabs.co) — block that origin so the app's
    # welcome-mode provisioning (prod team + api_keys row + FalkorDB graph
    # mint) never runs against prod. (The legacy /welcome stub is dead for
    # THIS flow: the signup path navigates straight cross-site;
    # tortoise.premiselabs.co/welcome is only reached via the login path,
    # which this monitor never drives.)
    page.route(
        "**://app.premiselabs.co/**",
        lambda route: route.fulfill(
            status=200, content_type="text/html",
            body="<html><body>LIVE-SIGNUP-ROUTE-BLOCKED</body></html>",
        ),
    )
    email = f"e2e-live-{uuid.uuid4().hex[:8]}@premise-labs.dev"
    password = f"E2eLivePass-{uuid.uuid4().hex[:8]}!"
    try:
        page.goto(
            "https://tortoise.premiselabs.co/signup", wait_until="domcontentloaded", timeout=30_000
        )
        # #1494: the email+password form lives in the email modal (the ids
        # of the retired inline form were kept for the #527 pins) — open it
        # before filling or fill waits on a display:none input forever.
        page.locator("#btn-email").click()
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
        # The flow redirects to the app root (route-blocked stub above) —
        # the redirect itself is the user-visible success state of #801.
        page.wait_for_url("**://app.premiselabs.co/**", timeout=15_000)
        # Fail-closed tripwire (#2140 review): the URL match alone proves
        # nothing — it passes whether the stub served the app-origin page or
        # the REAL app loaded (which would run #1566 welcome-mode
        # provisioning against prod). Assert the stub's unique marker so a
        # glob under-match (host drift, www/port variant) fails the monitor
        # instead of silently re-minting prod state.
        expect(page.locator("body")).to_contain_text(
            "LIVE-SIGNUP-ROUTE-BLOCKED", timeout=5_000)
        assert "email=" not in page.url and "password=" not in page.url, (
            f"credentials echoed into URL: {page.url}"
        )
    finally:
        from supabase_admin import delete_user_by_email

        delete_user_by_email(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"], email)
