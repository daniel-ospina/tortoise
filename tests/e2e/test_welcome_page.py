"""Playwright E2E tests for the hosted onboarding welcome page (#541).

Targets the live welcome page on the canonical auth host
(tortoise.premiselabs.co/welcome — host consolidation 2026-08-17: the
premiselabs.co copies of /welcome and the other auth/legal pages 301 to the
tortoise host; both hosts share the premise-labs Pages project).

Two test groups:
1. Static/live tests — no Supabase session needed:
   - page loads, shows loading state then the no-session error
   - the live tortoise-onboarding instructions mirror serves markdown
     (ONBOARDING_SKILL_URL contract — #1998 superseded the retired
     onboarding-prompt.md URL; see the module constant comment)
2. Mocked-session tests — drive the success state (harness tabs, copy
   buttons, MCP config JSON) by intercepting Supabase REST calls. These
   verify the welcome page v2 UI without needing real credentials.

Run:  python -m pytest tests/e2e/ -q
Env:   WELCOME_URL overrides the target (default https://tortoise.premiselabs.co/welcome)
       ONBOARDING_SKILL_URL overrides the onboarding-skill target
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
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

# Canonical host for the auth surface is tortoise.premiselabs.co (host
# consolidation 2026-08-17: premiselabs.co 301s /welcome → the tortoise host).
WELCOME_URL = os.environ.get("WELCOME_URL", "https://tortoise.premiselabs.co/welcome")
# The canonical onboarding artifact is the tortoise-onboarding instructions mirror
# (app.premiselabs.co/skills/tortoise-onboarding/SKILL.md) — W2 #1998 archived
# the AGENT_ONBOARDING.md prompt pipeline (stage_variants.py -> website/
# onboarding-prompt.md) under tortoise/onboarding/archive/ (M8: one live
# onboarding script; deployed mirror byte-identical by test). The old
# premiselabs.co/onboarding-prompt.md URL is retired: no deployment has staged
# it since #2161 (2026-09-03) and requests fall through to the Pages HTML
# fallback — the live-signup monitor failures #2171/#2187/#2190/#2191 were this
# static assertion against the retired URL, masked intermittently by a stale
# CDN cache entry (this module's e2e was the consumer the M8 sweep missed).
ONBOARDING_SKILL_URL = os.environ.get(
    "ONBOARDING_SKILL_URL",
    "https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md",
)

# #4686: the MCP probe below targets LIVE PRODUCTION, and a transport failure
# is not a 401-contract violation — but until this block it surfaced as one.
# `APIRequestContext.post: Timeout 15000ms exceeded` reads exactly like "the
# endpoint stopped rejecting unauthenticated callers", so the same red meant
# both "prod is down" and "this change broke the contract". Two things made it
# worse than a flaky test:
#   * the job stops at the FIRST failing step, so while the probe is down a
#     genuine failure in a later file of this job is INVISIBLE — the false red
#     does not merely add a red, it hides the real ones;
#   * there was no retry, although availability-watchdog already retries this
#     exact class of probe (attempt 1 -> attempt 2 -> verdict).
# The host's AVAILABILITY already has a discriminating monitor on main
# (availability-watchdog -> incident issues). This probe's job is the 401
# CONTRACT, so it asserts that contract only when the host actually answers;
# an unreachable host is reported as UNAVAILABLE, never as a contract failure.
MCP_PROBE_URL = os.environ.get("MCP_PROBE_URL", "https://api.premiselabs.co/mcp/")
# Same politeness budget as availability-watchdog's PROBE_ATTEMPTS/retry.
MCP_PROBE_ATTEMPTS = 3
MCP_PROBE_RETRY_S = 10



# ── Live/static tests (no auth) ─────────────────────────────────────


def test_welcome_page_no_session_redirects_to_auth(page: Page) -> None:
    """Without a Supabase session the page must send the visitor to the
    single auth page (/auth) after the bounded session wait — the
    no-session contract of the page (single auth surface, #1493)."""
    page.goto(WELCOME_URL, wait_until="domcontentloaded", timeout=30_000)
    expect(page).to_have_url(re.compile(r"/auth($|\?|#)"), timeout=25_000)


def test_onboarding_instructions_serves_markdown(page: Page) -> None:
    """The live tortoise-onboarding INSTRUCTIONS document (#4365) must be
    fetchable as markdown from the deployed dashboard mirror — the onboarding
    artifact URL the CLI prints after `tortoise onboard` (#544, repointed by
    #1998). Since #4365 it is instructions the agent READS, not an installed
    skill: the installer ships the three reusable capabilities only. The
    skill-shaped filename/URL is kept deliberately (it is the served path)."""
    resp = page.request.get(ONBOARDING_SKILL_URL, timeout=15_000)
    assert resp.ok, f"instructions URL returned {resp.status}"
    assert "text/markdown" in (resp.headers.get("content-type") or "")
    body = resp.text()
    assert body.startswith("---"), "unexpected instructions body (frontmatter missing)"
    assert "name: tortoise-onboarding" in body, "unexpected instructions body (name)"
    assert "tortoise_health" in body and "harness-connected" in body, (
        "instructions missing canonical content markers"
    )


def test_mcp_endpoint_rejects_unauthenticated(page: Page) -> None:
    """The MCP endpoint must 401 without a Bearer token (not 421/404) —
    regression guard for the deploy pipeline fixes (#545/#609/#610).

    #4686: assert the contract ONLY against a host that answers. A transport
    failure (timeout / DNS / connection refused) is an AVAILABILITY condition,
    not a broken 401 contract, and reporting it as the latter reds every open
    PR at once for a reason that has nothing to do with any of them. See the
    MCP_PROBE_* block for the full rationale.
    """
    transport: Exception | None = None
    for attempt in range(1, MCP_PROBE_ATTEMPTS + 1):
        try:
            resp = page.request.post(
                MCP_PROBE_URL,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
                timeout=15_000,
            )
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            transport = exc
            if attempt < MCP_PROBE_ATTEMPTS:
                time.sleep(MCP_PROBE_RETRY_S * attempt)
            continue
        # A 5xx is NOT an answer. This is the repo's OWN recorded rule, not a
        # convenience: availability-watchdog.yml states it verbatim -- "A 2xx or
        # a 401 both mean 'the app answered'; a timeout, a 5xx or a connection
        # error mean it did not" -- and availability-watchdog.sh encodes it as
        # `000|5??) printf 'DOWN'`. A Fly/Cloudflare ORIGIN outage answers
        # 502/521/503, so treating a 5xx as a contract verdict would reproduce
        # exactly the false-401 red this change exists to remove: reported as
        # "the endpoint stopped rejecting unauthenticated callers" while the
        # real fault is availability, and fired on every open PR at once while
        # the watchdog files the actual incident.
        if resp.status >= 500:
            transport = f"HTTP {resp.status} from the edge (no answer)"
            if attempt < MCP_PROBE_ATTEMPTS:
                time.sleep(MCP_PROBE_RETRY_S * attempt)
            continue
        # The host ANSWERED with a real status. The contract is the point, so
        # assert it — this is the ONLY path that may red, and it names the
        # status it actually got.
        assert resp.status == 401, (
            f"the MCP endpoint ANSWERED but returned {resp.status}, not 401 — "
            f"the unauthenticated-rejection contract is broken "
            f"(#545/#609/#610) at {MCP_PROBE_URL}"
        )
        return
    pytest.skip(
        f"{MCP_PROBE_URL} was UNREACHABLE after {MCP_PROBE_ATTEMPTS} attempts "
        f"(last transport error: {transport!r}). This is an availability "
        f"condition, NOT a 401-contract violation, so it is not reported as "
        f"one (#4686). Host availability is monitored by "
        f".github/workflows/availability-watchdog.yml"
    )


# ── Mocked-session tests (welcome page v2 success state) ────────────
# Intercept the Supabase REST calls the page makes and drive the
# provisioning flow: auth.getSession → org_memberships poll →
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

    Real signup against PROD through the SERVER-SIDE BFF path (#801/#4054): the
    form POSTs SAME-ORIGIN to /auth/signup, which proxies
    `POST {API_ORIGIN}/v1/signup/email` (hosted API → GoTrue Admin API with
    email_confirm=true — NO confirmation email is sent) and then signs the user
    in server-side. The BFF's response must be 200 — NOT 429
    (over_email_send_rate_limit / per-IP register buckets) — and the flow then
    redirects to the app root (WELCOME_URL = https://app.premiselabs.co).

    This monitors the BFF boundary, not a Supabase URL: after #4054 the browser
    no longer talks to Supabase for signup at all, and a Worker's outbound fetch
    is invisible to `page.on("response")`. A separate tripwire asserts the
    browser does NOT reach those upstreams directly — if it does, the BFF move is
    incomplete and the token is back in the page's reach.

    The app-origin navigation is route-blocked: a live landing on the app
    root would run the #1566 welcome-mode provisioning and mint an
    un-deletable prod team + api_keys row + FalkorDB graph (no cleanup
    endpoint in-repo) — the monitor only needs the signup + auto sign-in to
    succeed, and the intercepted navigation still proves the redirect fired.

    Teardown deletes the created auth user via the Admin API (best-effort;
    the FK cascade removes the placeholder org_memberships row)."""
    signup = {"status": None, "body": ""}
    # Tripwire for the BFF contract (#4054): these Supabase endpoints must never
    # be reached FROM THE BROWSER. Before the move the page called them
    # directly; now it must not — for the BFF session the browser holds only the
    # HttpOnly handle.
    browser_to_supabase: list[str] = []

    def _on_response(resp):
        # #4054/#4171: the auth pages moved onto the app origin and the BFF
        # became a TRUE backend. The form POSTs SAME-ORIGIN to /auth/signup, and
        # functions/auth/signup.ts performs BOTH upstream calls SERVER-side
        # (`POST ${API_ORIGIN}/v1/signup/email`, then `signInWithPassword`). A
        # Worker's outbound fetch never surfaces in `page.on("response")`, so
        # the pre-BFF listeners that matched `v1/signup/email` and
        # `token?grant_type=password` matched NOTHING and left both statuses
        # None — the monitor failed on "no /v1/signup/email response observed"
        # even when signup was perfectly healthy. The observable boundary is now
        # the BFF call itself.
        if resp.request.method == "POST" and resp.url.endswith("/auth/signup"):
            signup["status"] = resp.status
            signup["body"] = resp.text()[:400]
        elif "v1/signup/email" in resp.url or "grant_type=password" in resp.url:
            browser_to_supabase.append(resp.url)

    page.on("response", _on_response)
    # #1566: the account is created pre-confirmed, so the SIGNUP flow
    # redirects to the APP ROOT (signup.html WELCOME_URL =
    # https://app.premiselabs.co) — block that ROOT DOCUMENT so the app's
    # welcome-mode provisioning (prod team + api_keys row + FalkorDB graph
    # mint) never runs against prod.
    #
    # ONLY THE ROOT, not the whole origin (#4104). It used to be
    # `**://app.premiselabs.co/**`, which was correct while the signup FORM was
    # served from tortoise.premiselabs.co. #4171 moved the auth pages onto the
    # app origin, so `/signup` now 301s there — and the blanket block then
    # intercepted the FORM ITSELF, serving the stub where the form should be.
    # The click on `#btn-email` timed out against a page that had no form, and
    # the monitor reported a signup-funnel failure that was really a fixture
    # colliding with its own block.
    #
    # Narrowing to the root is sufficient for the guard's purpose: the root
    # document is what boots the SPA, so serving the stub there means the app
    # never loads and provisioning cannot run. Sub-resources (`/assets/*`) are
    # irrelevant once the document is the stub.
    page.route(
        re.compile(r"^https://app\.premiselabs\.co/?([?#].*)?$"),
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
        assert signup["status"] is not None, (
            "no POST to the BFF /auth/signup was observed — the form did not "
            "submit, or it is still posting straight to Supabase"
        )
        assert signup["status"] == 200, (
            f"live signup returned {signup['status']} — rate-limited or error: "
            f"{signup['body']!r}"
        )
        # The BFF contract (#4054): the browser must not reach these upstreams
        # itself. If it does, the move is incomplete and the access token is
        # back within the page's reach.
        assert not browser_to_supabase, (
            "the browser called Supabase directly instead of going through the "
            f"BFF: {browser_to_supabase}"
        )
        # #801: the account is created pre-confirmed, so the BFF signs the user
        # in SERVER-side (`signInWithPassword`) and answers with a redirect —
        # there is no client-visible `auth/v1/token` response to observe any
        # more (that assertion is why this monitor was red). The signed-in
        # state is proven by the app-origin navigation below.
        # The flow redirects to the app ROOT (route-blocked stub above) —
        # the redirect itself is the user-visible success state of #801.
        # Assert the ROOT specifically (#4104): the form page itself now lives
        # on the app origin, so a `**://app.premiselabs.co/**` glob would match
        # the URL the browser was already on and the wait would be vacuous.
        try:
            page.wait_for_url(
                re.compile(r"^https://app\.premiselabs\.co/?([?#].*)?$"), timeout=15_000
            )
        except PlaywrightTimeoutError as exc:  # pragma: no cover - live monitor
            raise AssertionError(
                "the post-signup redirect did not reach the app root; "
                f"still on {page.url!r}"
            ) from exc
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
