"""Playwright E2E tests for the hosted onboarding welcome page (#541).

Targets the live welcome page (premiselabs.co/welcome — the active custom
domain of the premise-labs Pages project; tortoise.premiselabs.co still
CNAMEs to a legacy project, see #540).

Two test groups:
1. Static/live tests — no Supabase session needed:
   - page loads, shows loading state then the no-session error
   - the canonical prompt URL serves markdown (PROMPT_URL contract)
2. Mocked-session tests — drive the success state (harness tabs, copy
   buttons, MCP config JSON) by intercepting Supabase REST calls. These
   verify the welcome page v2 UI without needing real credentials.

Run:  python -m pytest tests/e2e/ -q
Env:   WELCOME_URL overrides the target (default https://premiselabs.co/welcome)
       SUPABASE_URL/SUPABASE_SERVICE_KEY + a real session enable the live
       signup E2E (skipped by default — no creds in CI).
"""
from __future__ import annotations

import json
import os
import re
import uuid

import pytest
from playwright.sync_api import Page, expect

WELCOME_URL = os.environ.get("WELCOME_URL", "https://premiselabs.co/welcome")
PROMPT_URL = os.environ.get("PROMPT_URL", "https://premiselabs.co/onboarding-prompt.md")

# ── Live/static tests (no auth) ─────────────────────────────────────


def test_welcome_page_loads_and_shows_no_session_error(page: Page) -> None:
    """Without a Supabase session the page must show the error state
    (loading → 'No active session') — the base contract of the page."""
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("#error-state")).not_to_be_hidden(timeout=15_000)
    expect(page.locator("#error-message")).to_contain_text("No active session")


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
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"},
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        timeout=15_000,
    )
    assert resp.status == 401, f"expected 401, got {resp.status}"


# ── Mocked-session tests (welcome page v2 success state) ────────────
# Intercept the Supabase REST calls the page makes and drive the
# provisioning flow: auth.getSession → team_memberships poll →
# reveal_api_key RPC → success state with harness tabs + artifacts.


def _fake_user_id() -> str:
    return str(uuid.uuid4())


def _mock_supabase_success(page: Page, team_name: str = "Test Team",
                            reveal_result: str = "tt_e2e_mock_api_key_1234567890abcdef") -> str:
    """Drive the welcome page into its success state by (1) seeding a
    Supabase session in localStorage (supabase-js v2 reads getSession() from
    localStorage, not the network) and (2) route-intercepting the REST calls
    (team_memberships poll + reveal_api_key RPC) it makes with that session.

    Returns the fake user id used by the session.

    reveal_result: the body the reveal_api_key RPC returns — a real tt_ key
    (success state) or "pending" (returning-visitor state).
    """
    user_id = _fake_user_id()
    session = {
        "access_token": "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "expires_in": 3600,
        "expires_at": 2**31,
        "token_type": "bearer",
        "user": {
            "id": user_id,
            "aud": "authenticated",
            "role": "authenticated",
            "email": "e2e@premise-labs.dev",
            "app_metadata": {"provider": "email"},
            "user_metadata": {"email": "e2e@premise-labs.dev"},
        },
    }
    team_row = {
        "team_id": f"team_{user_id[:8]}",
        "team_name": team_name,
        "graph_name": f"team_{user_id[:8]}",
        "status": "active",
    }

    # supabase-js v2 stores the session under sb-<project-ref>-auth-token in
    # localStorage; seed it before the page script runs.
    page.add_init_script(f"""
      localStorage.setItem("sb-ybetwichurajbfswfeqa-auth-token", JSON.stringify({{
        access_token: "fake-access-token",
        refresh_token: "fake-refresh-token",
        expires_in: 3600,
        expires_at: {2**31},
        token_type: "bearer",
        user: {{ id: "{user_id}", email: "e2e@premise-labs.dev" }}
      }}));
    """)

    def _handle(route):
        url = route.request.url
        method = route.request.method
        # GET /rest/v1/team_memberships?user_id=eq.<id>&select=...
        if "team_memberships" in url and method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps([team_row]))
            return
        # POST /rest/v1/rpc/reveal_api_key
        if "rpc/reveal_api_key" in url and method == "POST":
            route.fulfill(status=200, content_type="text/plain;charset=utf-8",
                          body=reveal_result)
            return
        route.continue_()

    page.route("**/*", _handle)
    return user_id


def test_welcome_success_shows_key_and_artifacts(page: Page) -> None:
    """With a mocked provisioned session the page shows the API key, harness
    tabs, MCP config, and prompt block (welcome page v2 success state)."""
    _mock_supabase_success(page)
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("#success")).not_to_be_hidden(timeout=15_000)
    expect(page.locator("#api-key")).to_contain_text("tt_")
    expect(page.locator("#harness-tabs")).to_be_visible()
    # Four harness tabs (Claude Code / Codex / Cursor / Pi)
    expect(page.locator(".harness-tab")).to_have_count(4)
    # MCP config rendered for the default harness (Claude Code)
    config = page.locator("#mcp-config-text").inner_text()
    assert '"url": "https://api.premiselabs.co/mcp"' in config
    assert "Bearer" in config


def test_harness_tabs_switch_config(page: Page) -> None:
    """Switching harness tabs must swap the Streamable HTTP config variant
    (each harness has its own mcpServers JSON)."""
    _mock_supabase_success(page)
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("#success")).not_to_be_hidden(timeout=15_000)

    default = page.locator("#mcp-config-text").inner_text()
    for harness in ("codex", "cursor", "pi"):
        page.locator(f'.harness-tab[data-harness="{harness}"]').click()
        new_text = page.locator("#mcp-config-text").inner_text()
        assert new_text != default, f"config did not change for {harness}"

    # Back to Claude Code — config should match the original default
    page.locator('.harness-tab[data-harness="claude"]').click()
    assert page.locator("#mcp-config-text").inner_text() == default


def test_mcp_config_copy_puts_bearer_json_on_clipboard(page: Page) -> None:
    """Clicking Copy MCP config must place valid JSON with the Bearer header
    (tt_ key) on the clipboard."""
    _mock_supabase_success(page)
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("#success")).not_to_be_hidden(timeout=15_000)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"],
                                   origin="https://premiselabs.co")
    page.locator("#btn-copy-mcp").click()
    clip = page.evaluate("navigator.clipboard.readText()")
    parsed = json.loads(clip)
    servers = parsed["mcpServers"]["tortoise"]
    assert servers["url"] == "https://api.premiselabs.co/mcp"
    assert "Bearer tt_" in servers["headers"]["Authorization"]


def test_prompt_copy_uses_fetched_markdown(page: Page) -> None:
    """copyPrompt() fetches the canonical prompt URL and puts its markdown on
    the clipboard (regression for #540 — previously served HTML)."""
    _mock_supabase_success(page)
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("#success")).not_to_be_hidden(timeout=15_000)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"],
                                   origin="https://premiselabs.co")
    page.locator("#btn-copy-prompt").click()
    clip = page.evaluate("navigator.clipboard.readText()")
    assert clip.startswith("# Tortoise Onboarding"), "prompt copy is not the markdown"
    assert "### Q1" in clip, "prompt missing Q1"


def test_returning_visitor_shows_dashboard_hub(page: Page) -> None:
    """A returning visitor (key already consumed — reveal returns 'pending')
    sees the dashboard-hub state instead of a re-revealed key."""
    _mock_supabase_success(page, reveal_result="pending")
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("#returning-block")).not_to_be_hidden(timeout=15_000)


# ── Live signup E2E (requires real Supabase creds + session) ────────

LIVE_SIGNUP = pytest.mark.skipif(
    not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY")),
    reason="SUPABASE_URL/SUPABASE_SERVICE_KEY not set — live signup test skipped",
)


@LIVE_SIGNUP
def test_live_signup_redirects_to_welcome_with_key(page: Page) -> None:
    """Full live journey: sign up via Supabase → welcome page → key shown.
    Requires a real signup (email) against the production Supabase project.
    NOTE: this creates a real team in prod — run sparingly with a throwaway
    email like e2e-<ts>@premise-labs.dev."""
    page.goto("https://premiselabs.co/signup", wait_until="domcontentloaded", timeout=30_000)
    ts = uuid.uuid4().hex[:8]
    email = f"e2e-live-{ts}@premise-labs.dev"
    page.locator('input[type="email"], input[name="email"]').first.fill(email)
    password_field = page.locator('input[type="password"]').first
    password_field.fill(f"E2eLivePass-{ts}!")
    page.locator('button[type="submit"], button:has-text("Sign up")').first.click()
    expect(page).to_have_url(re.compile(r"welcome"), timeout=30_000)
    expect(page.locator("#success")).not_to_be_hidden(timeout=60_000)
    expect(page.locator("#api-key")).to_contain_text("tt_")
