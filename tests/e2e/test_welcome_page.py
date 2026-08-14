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
       SUPABASE_URL/SUPABASE_SERVICE_KEY enable the live no-429 signup smoke
       (skipped by default — no creds in CI; see #801).
"""
from __future__ import annotations

import json
import os
import time
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


def test_welcome_callback_session_in_url_reaches_success(page: Page) -> None:
    """Email-confirmation / OAuth callback simulation (#527): the session
    arrives via the URL fragment (implicit flow) and supabase-js parses it
    asynchronously (GET /auth/v1/user round-trip). The page must WAIT for the
    session (defensive waitForSession) and reach the success state — it must
    NOT bounce to 'No active session' while the parse is in flight."""
    user_id = _fake_user_id()

    def handle(route):
        url = route.request.url
        method = route.request.method
        if "auth/v1/user" in url and method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "id": user_id, "aud": "authenticated", "role": "authenticated",
                "email": "e2e@premise-labs.dev",
                "app_metadata": {"provider": "email"},
                "user_metadata": {"email": "e2e@premise-labs.dev"},
            }))
            return
        if "team_memberships" in url and method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "team_id": f"team_{user_id[:8]}", "team_name": "Test Team",
                "graph_name": f"team_{user_id[:8]}", "status": "active",
            }))
            return
        if "rpc/reveal_api_key" in url and method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps("tt_e2e_mock_api_key_1234567890abcdef"))
            return
        route.continue_()

    page.route("**/*", handle)
    # Implicit-flow callback: session params in the URL fragment.
    page.goto(WELCOME_URL + "#access_token=fake-at&refresh_token=fake-rt&expires_in=3600&token_type=bearer",
              wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("#success")).not_to_be_hidden(timeout=15_000)
    expect(page.locator("#api-key")).to_contain_text("tt_", timeout=15_000)


def _fake_user_id() -> str:
    return str(uuid.uuid4())


def _seed_local_session(page: Page, user_id: str) -> None:
    """Seed a supabase-js session in localStorage under BOTH storage keys —
    the prod project ref (sb-ybetwichurajbfswfeqa) and the local CLI ref
    (127.0.0.1 → sb-127) — so the mocked tests run against either the live
    site (premiselabs.co) or a wrangler pages dev preview (localhost:8788)."""
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
    team_row = {
        "team_id": f"team_{user_id[:8]}",
        "team_name": team_name,
        "graph_name": f"team_{user_id[:8]}",
        "status": "active",
    }

    _seed_local_session(page, user_id)

    def _handle(route):
        url = route.request.url
        method = route.request.method
        # GET /rest/v1/team_memberships?user_id=eq.<id>&select=...
        # (.single() sends Accept: application/vnd.pgrst.object+json and
        # expects a BARE object body, not an array)
        if "team_memberships" in url and method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(team_row))
            return
        # POST /rest/v1/rpc/reveal_api_key — supabase-js .rpc() parses JSON;
        # the RPC returns a plain text key, so the mock must send it as a
        # JSON string ("tt_...") for supabase-js to decode data correctly.
        if "rpc/reveal_api_key" in url and method == "POST":
            route.fulfill(status=200,
                          content_type="application/json",
                          body=json.dumps(reveal_result))
            return
        route.continue_()

    page.route("**/*", _handle)
    return user_id


def _open_mcp_path(page: Page) -> None:
    """Drive the two-path chooser (#1170): the MCP card (harness tabs, manual
    setup) is hidden until the user picks "Use Tortoise (MCP)". Skips when the
    deployed page predates the chooser (TORTISE_HOST_CHECK pattern)."""
    chooser = page.locator("#choose-mcp")
    if chooser.count() == 0:
        pytest.skip(
            "deployed welcome page predates the #1170 two-path chooser — runs post-deploy"
        )
    chooser.click()
    expect(page.locator("#mcp-card")).not_to_be_hidden(timeout=15_000)


def _open_manual_setup(page: Page) -> None:
    """Expand the collapsed manual-setup section (#1170): the env-var config
    and onboarding-prompt copy buttons live there behind the one-click flow."""
    link = page.locator("#manual-toggle-link")
    if link.count() == 0:
        pytest.skip(
            "deployed welcome page predates the #1170 manual-setup toggle — runs post-deploy"
        )
    link.click()
    expect(page.locator("#manual-setup")).not_to_be_hidden(timeout=15_000)


def test_welcome_success_shows_key_and_artifacts(page: Page) -> None:
    """With a mocked provisioned session the page shows the API key, harness
    tabs, and the one-click setup prompt (welcome page v2 success state)."""
    _mock_supabase_success(page)
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("#success")).not_to_be_hidden(timeout=15_000)
    expect(page.locator("#api-key")).to_contain_text("tt_", timeout=15_000)
    # Regression guard (#728): showSuccess() must complete (no throw on the
    # removed #mcp-snippet-key line) and reveal the dashboard CTA for
    # first-time users — previously the button never appeared.
    expect(page.locator("#btn-dashboard")).to_be_visible(timeout=15_000)
    # Two-path UI (#1170): harness tabs + the setup flows live in the MCP
    # panel, hidden until the user picks "Use Tortoise (MCP)".
    _open_mcp_path(page)
    expect(page.locator("#harness-tabs")).to_be_visible()
    # Four harness tabs (Claude Code / Codex / Cursor / Pi)
    expect(page.locator(".harness-tab")).to_have_count(4)
    # The one-click setup prompt (the visible default, #1170) embeds the
    # revealed key and the Claude Code CLI one-liner (#529).
    page.locator('.harness-tab[data-harness="claude"]').click()
    setup = page.locator("#setup-prompt-text").inner_text()
    assert "https://api.premiselabs.co/mcp" in setup
    assert "Bearer tt_" in setup
    assert "claude mcp add" in setup
    # Manual env-var form (#1170): the key-less .mcp.json alternative sits in
    # the collapsed manual-setup section — expand it and verify the config.
    _open_manual_setup(page)
    config = page.locator("#mcp-config-text").inner_text()
    assert "https://api.premiselabs.co/mcp" in config
    assert "TORTOISE_API_KEY" in config


def test_harness_tabs_switch_config(page: Page) -> None:
    """Switching harness tabs must swap the one-click setup prompt variant
    (each harness has its own install line — epic #529)."""
    _mock_supabase_success(page)
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("#success")).not_to_be_hidden(timeout=15_000)
    expect(page.locator("#api-key")).to_contain_text("tt_", timeout=15_000)

    # Two-path UI (#1170): open the MCP path to reach the harness tabs.
    _open_mcp_path(page)

    # Each harness renders its own one-click setup prompt (epic #529):
    # claude = CLI one-liner, codex = export + codex mcp add, cursor/pi =
    # config-file forms — all four renderings are distinct.
    seen: set[str] = set()
    for harness in ("claude", "codex", "cursor", "pi"):
        page.locator(f'.harness-tab[data-harness="{harness}"]').click()
        text = page.locator("#setup-prompt-text").inner_text()
        assert 'https://api.premiselabs.co/mcp' in text, f"bad setup prompt for {harness}"
        seen.add(text)
    assert len(seen) >= 4, f"expected distinct setup prompts, got {len(seen)}"
    page.locator('.harness-tab[data-harness="codex"]').click()
    codex_text = page.locator("#setup-prompt-text").inner_text()
    assert "codex mcp add" in codex_text
    assert "--bearer-token-env-var TORTOISE_API_KEY" in codex_text


def test_mcp_config_copy_puts_harness_config_on_clipboard(page: Page) -> None:
    """Clicking Copy MCP config (Claude tab) must place that harness's Block
    A on the clipboard — the CLI one-liner post-#529-deploy, the JSON shape
    before (live-page suite; capstone #969 tightens post-deploy)."""
    _mock_supabase_success(page)
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("#success")).not_to_be_hidden(timeout=15_000)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"],
                                   origin=_clipboard_origin())
    # Two-path UI (#1170): the copy buttons live in the collapsed manual-setup
    # section behind the MCP chooser. Harness tab first, THEN manual setup —
    # this order also works pre-deploy (the live page before #1189 collapses
    # manual setup on a harness switch; post-deploy both orders work).
    _open_mcp_path(page)
    page.locator(".harness-tab[data-harness=\"claude\"]").click()
    _open_manual_setup(page)
    page.locator("#btn-copy-mcp").click()
    clip = page.evaluate("navigator.clipboard.readText()")
    assert "https://api.premiselabs.co/mcp" in clip
    # The copy button lives in the MANUAL setup section (#1170 layout), which
    # renders the key-less env-var form: .mcp.json with ${TORTOISE_API_KEY}
    # indirection (never a literal key in the config block).
    assert "TORTOISE_API_KEY" in clip, f"env-var form missing: {clip[:200]!r}"
    assert "Bearer ${TORTOISE_API_KEY}" in clip or "Bearer $TORTOISE_API_KEY" in clip
    config_block, sep, export_line = clip.partition("export TORTOISE_API_KEY=")
    assert sep, f"env-var form missing the export line: {clip[:200]!r}"
    # The config block must reference the key only via env-var indirection;
    # the literal key belongs in the export line and nowhere else (order-robust
    # partition, not a position-dependent slice).
    assert "tt_e2e_mock_api_key" not in config_block, \
        "config block must not embed the literal key"
    assert "tt_e2e_mock_api_key" in export_line, "export line missing the key"


def test_prompt_copy_uses_fetched_markdown(page: Page) -> None:
    """copyPrompt() fetches the canonical prompt URL and puts its markdown on
    the clipboard (regression for #540 — previously served HTML)."""
    _mock_supabase_success(page)
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("#success")).not_to_be_hidden(timeout=15_000)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"],
                                   origin=_clipboard_origin())
    # Two-path UI (#1170): the onboarding-prompt copy button lives in the
    # collapsed manual-setup section behind the MCP chooser.
    _open_mcp_path(page)
    _open_manual_setup(page)
    page.locator("#btn-copy-prompt").click()
    clip = page.evaluate("navigator.clipboard.readText()")
    assert clip.startswith("# Tortoise Onboarding"), "prompt copy is not the markdown"
    assert "### Q1" in clip, "prompt missing Q1"


def _require_returning_redirect(page: Page) -> None:
    """Skip when the deployed welcome page predates the #1147 returning-
    visitor dashboard redirect. The CI welcome-e2e job tests the LIVE prod
    page, which only gains the redirect once this PR deploys — pre-deploy
    runs skip (green-with-annotation, the repo's TORTISE_HOST_CHECK pattern)
    and post-deploy runs exercise the routing on every subsequent run."""
    has = page.evaluate(
        "typeof window.showAlreadyProvisioned !== 'undefined' && "
        "window.showAlreadyProvisioned.toString().includes('app.premiselabs.co')"
    )
    if not has:
        pytest.skip(
            "deployed welcome page predates #1147 returning-visit redirect — "
            "runs post-deploy"
        )


def test_returning_visitor_routes_to_dashboard(page: Page) -> None:
    """A returning visitor (key already consumed — reveal returns 'pending')
    is ROUTED to the dashboard (app.premiselabs.co) instead of dead-ending on
    a 'can't be shown again' card (#1147): the key is shown exactly once on
    first visit; subsequent visits go to the dashboard hub where keys live.
    """
    # Route the dashboard target so the redirect navigation is deterministic.
    page.route("**//app.premiselabs.co/**", lambda route: route.fulfill(
        status=200, content_type="text/html", body="<html><body>dashboard</body></html>"
    ))
    _mock_supabase_success(page, reveal_result="pending")
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    _require_returning_redirect(page)
    # The hold card paints before the (1.2s-delayed) redirect navigates away.
    expect(page.locator("#returning-block")).not_to_be_hidden(timeout=15_000)
    page.wait_for_url("**://app.premiselabs.co/**", timeout=15_000)
    # A returning visitor must NOT see a re-revealed key.
    expect(page.locator("#reveal-block")).to_be_hidden(timeout=5_000)


def test_reveal_shows_key_once_then_returning_state(page: Page) -> None:
    """E2E-6 (#770): the welcome-page reveal shows the plaintext key exactly
    once, and the server nulls it — a second visit must get the returning-
    visitor state (no re-reveal). The nulled row keeps lookup_hash (asserted
    in the 0010 SQL suite) so the API-key auth path can still resolve the key
    (Task 3 wires the actual lookup). Stateful mock: first reveal RPC returns
    the key, every later one returns null (what the nulled row produces)."""
    user_id = _mock_supabase_success(page)  # default: static key per call
    # Rebuild the mock statefully: reveal #1 returns the key, reveal #2+ null.
    # (Reuse the session seeding by overriding the route handler afterwards.)
    reveal_calls = {"n": 0}

    def _handle(route):
        url = route.request.url
        method = route.request.method
        if "team_memberships" in url and method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({
                              "team_id": f"team_{user_id[:8]}",
                              "team_name": "Test Team",
                              "graph_name": f"team_{user_id[:8]}",
                              "status": "active",
                          }))
            return
        if "rpc/reveal_api_key" in url and method == "POST":
            reveal_calls["n"] += 1
            body = "tt_e2e_mock_api_key_1234567890abcdef" if reveal_calls["n"] == 1 else None
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(body))
            return
        route.continue_()

    page.route("**/*", _handle)

    # First visit: the plaintext key is shown once.
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("#success")).not_to_be_hidden(timeout=15_000)
    expect(page.locator("#api-key")).to_contain_text("tt_", timeout=15_000)

    # Second visit: the key was nulled server-side → route to the dashboard
    # (no re-reveal, no 'can't be shown again' dead-end, #1147).
    page.route("**//app.premiselabs.co/**", lambda route: route.fulfill(
        status=200, content_type="text/html", body="<html><body>dashboard</body></html>"
    ))
    page.reload(wait_until="domcontentloaded")
    _require_returning_redirect(page)
    # Hold card paints, then the redirect routes to the dashboard.
    expect(page.locator("#returning-block")).not_to_be_hidden(timeout=15_000)
    page.wait_for_url("**://app.premiselabs.co/**", timeout=15_000)
    expect(page.locator("#reveal-block")).to_be_hidden(timeout=5_000)
    assert reveal_calls["n"] == 2, (
        f"expected exactly 2 reveal RPCs (one per visit), got {reveal_calls['n']}"
    )


# ── Client-side provisioning tests (#527) ───────────────────────────────
# The after_user_created webhook is DISABLED on this Supabase plan (its
# Standard-Webhooks signature can't be verified), so the welcome page must
# provision through the edge function's JWT path: session present + NO
# membership row → POST /functions/v1/tenant-provision with
# Authorization: Bearer <access_token> → 201 → re-query membership → reveal.


def _mock_empty_membership_then_provision(page: Page, provision_status: int = 201,
                                           existing_membership: bool = False) -> dict:
    """Seed a session with NO real membership and intercept the client-side
    provisioning flow: team_memberships returns the PLACEHOLDER row
    (team_id='' — what the on_auth_user_created trigger inserts at signup;
    the page's poll skips it because team_id is falsy) → tenant-provision
    call (recorded) → membership appears → reveal_api_key.

    provision_status: HTTP status the edge function mock returns (201 = mint
    succeeds; 401/500 exercise the retry + contact-support fallback).
    existing_membership: True = the membership row is present from the start
    (idempotency case — provision must NEVER fire).

    Returns a state dict: {user_id, provision_requests: [playwright Request],
    provisioned: bool}."""
    user_id = _fake_user_id()
    state = {"user_id": user_id, "provision_requests": [], "provisioned": False}
    placeholder_row = {
        "team_id": "",
        "team_name": "provisioning...",
        "graph_name": "",
        "status": "active",
    }
    team_row = {
        "team_id": f"team_{user_id[:8]}",
        "team_name": "Test Team",
        "graph_name": f"team_{user_id[:8]}",
        "status": "active",
    }
    provision_body = {
        "team_id": f"team_{user_id[:8]}",
        "team_name": "Test Team",
        "api_key": "tt_provisioned_mock_key_abcdef0123456789",
        "graph_name": f"team_{user_id[:8]}",
    }

    _seed_local_session(page, user_id)

    def _handle(route):
        url = route.request.url
        method = route.request.method
        if "functions/v1/tenant-provision" in url and method == "POST":
            state["provision_requests"].append(route.request)
            if provision_status == 201:
                state["provisioned"] = True
                route.fulfill(status=201, content_type="application/json",
                              body=json.dumps(provision_body))
            else:
                route.fulfill(status=provision_status, content_type="application/json",
                              body=json.dumps({"error": "Unauthorized: mock"}))
            return
        if "team_memberships" in url and method == "GET":
            if state["provisioned"] or existing_membership:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(team_row))
            else:
                # Pre-provision state: the placeholder row (team_id='') from
                # the on_auth_user_created trigger. The page's poll guard
                # (data.team_id truthy) skips it and proceeds to provision.
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(placeholder_row))
            return
        if "rpc/reveal_api_key" in url and method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps("tt_provisioned_mock_key_abcdef0123456789"))
            return
        route.continue_()

    page.route("**/*", _handle)
    return state


def _clipboard_origin() -> str:
    """Clipboard permissions must be granted for the page's ACTUAL origin:
    https://premiselabs.co in CI/live runs, http://127.0.0.1:8788 in local
    wrangler pages dev runs. Derive it from WELCOME_URL (#527 local runs)."""
    from urllib.parse import urlsplit

    u = urlsplit(WELCOME_URL)
    return f"{u.scheme}://{u.netloc}"


def _require_client_side_provisioning(page: Page) -> None:
    """Skip when the deployed welcome page predates the #527 client-side
    provisioning code. The CI welcome-e2e job tests the LIVE prod page
    (premiselabs.co/welcome), which only gains provisionViaEdgeFunction once
    this PR deploys — so pre-deploy runs skip (green-with-annotation, the
    repo's TORTISE_HOST_CHECK pattern) and post-deploy runs exercise the
    mocked provision flow on every subsequent run."""
    has = page.evaluate("typeof window.provisionViaEdgeFunction !== 'undefined'")
    if not has:
        pytest.skip(
            "deployed welcome page predates #527 client-side provisioning — "
            "runs post-deploy"
        )


def test_welcome_provisions_via_edge_function_when_no_membership(page: Page) -> None:
    """#527: session + NO membership row → the page calls tenant-provision
    with the user's JWT (Authorization: Bearer), then re-queries the
    membership and reveals the key through the canonical reveal path."""
    state = _mock_empty_membership_then_provision(page)
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    _require_client_side_provisioning(page)
    expect(page.locator("#success")).not_to_be_hidden(timeout=30_000)
    expect(page.locator("#api-key")).to_contain_text("tt_provisioned_mock_key", timeout=15_000)

    assert len(state["provision_requests"]) == 1, \
        f"expected exactly 1 provision call, got {len(state['provision_requests'])}"
    req = state["provision_requests"][0]
    assert req.headers.get("authorization") == "Bearer fake-access-token", \
        f"provision call missing the user JWT: {req.headers.get('authorization')!r}"
    payload = json.loads(req.post_data or "{}")
    assert payload["user_id"] == state["user_id"]
    assert payload["email"] == "e2e@premise-labs.dev"


def test_welcome_provision_failure_retries_once_then_contact_support(page: Page) -> None:
    """#527: a failed provision call (401) is retried exactly ONCE, then the
    page shows a humanized error with a contact-support note — never raw
    JSON, never a broken page."""
    state = _mock_empty_membership_then_provision(page, provision_status=401)
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    _require_client_side_provisioning(page)
    expect(page.locator("#error-state")).not_to_be_hidden(timeout=30_000)
    msg = page.locator("#error-message").inner_text()
    assert "contact support" in msg, f"error lacks the contact-support note: {msg!r}"
    assert "{" not in msg, f"error leaks raw JSON: {msg!r}"
    assert len(state["provision_requests"]) == 2, \
        f"expected 1 retry (2 calls total), got {len(state['provision_requests'])}"


def test_welcome_existing_membership_skips_provisioning(page: Page) -> None:
    """#527 idempotency guard: when a membership row already exists the edge
    function is NEVER called — a second call would mint a NEW team (the
    function is not idempotent server-side, so the page must guard)."""
    state = _mock_empty_membership_then_provision(page, existing_membership=True)
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    _require_client_side_provisioning(page)
    expect(page.locator("#success")).not_to_be_hidden(timeout=15_000)
    expect(page.locator("#api-key")).to_contain_text("tt_", timeout=15_000)
    assert len(state["provision_requests"]) == 0, \
        f"provision called despite existing membership: {len(state['provision_requests'])} calls"


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
    page.route("**/welcome*", lambda route: route.fulfill(
        status=200, content_type="text/html", body="<html><body>ok</body></html>"
    ))
    email = f"e2e-live-{uuid.uuid4().hex[:8]}@premise-labs.dev"
    password = f"E2eLivePass-{uuid.uuid4().hex[:8]}!"
    try:
        page.goto("https://premiselabs.co/signup", wait_until="domcontentloaded", timeout=30_000)
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
            f"live signup returned {signup['status']} — rate-limited or error: {signup['body']!r}")
        # #801: created pre-confirmed → the page auto-signs-in.
        deadline = time.time() + 30
        while token["status"] is None and time.time() < deadline:
            page.wait_for_timeout(250)
        assert token["status"] is not None, "no auto sign-in (auth/v1/token) response observed"
        assert token["status"] == 200, f"auto sign-in returned {token['status']}"
        # The flow redirects to /welcome (route-blocked stub above) — the
        # redirect itself is the user-visible success state of #801.
        page.wait_for_url("**/welcome*", timeout=15_000)
        assert "email=" not in page.url and "password=" not in page.url, \
            f"credentials echoed into URL: {page.url}"
    finally:
        from supabase_admin import delete_user_by_email
        delete_user_by_email(os.environ["SUPABASE_URL"],
                             os.environ["SUPABASE_SERVICE_KEY"], email)

