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


def _is_bff_signup_request(method: str, url: str) -> bool:
    """True for the BFF call this monitor watches (`POST /auth/signup`).

    ONE predicate governs all three listeners (request/response/requestfailed), so
    the filter the pins exercise is the filter that actually gates the verdict.
    """
    return method == "POST" and url.endswith("/auth/signup")


def _signup_transport_failure(method: str, url: str, failure: str | None) -> str | None:
    """The entry to record for a FAILED BFF signup request, else None.

    Split out of the listener so the method/URL filter and the entry format are
    pinnable without a browser: a pin over the message alone cannot see a deleted
    listener or a broken filter (#4940 review).
    """
    if not _is_bff_signup_request(method, url):
        return None
    # `Request.failure` is Optional[str] in playwright-python and can be None at
    # handler time; name that rather than rendering "None".
    return f"{method} {url} — {failure or 'unknown transport failure'}"


def _unobserved_outcome(*, transport_failures: list[str], post_issued: bool) -> tuple[str, str]:
    """Return `(verdict, message)` for a signup POST that captured NOTHING (#4940).

    `page.on("response")` only fires when a request gets an ANSWER, so
    `signup["status"]` stays `None` for three different situations, and they do
    NOT share a verdict:

    - the request DIED at the transport layer (DNS, TLS, connection refused) —
      UNAVAILABLE;
    - the request was ISSUED but never answered (connection blackhole, slow
      connect timeout, client-side abort) — UNAVAILABLE;
    - the form never submitted a request at all — the other PRODUCT condition,
      and the one the pre-#4940 message asserted for all three.

    `net::ERR_ABORTED` is deliberately NOT a product failure: the signup POST is a
    plain `fetch` carrying no `AbortController`/`signal` (verified: `signup.html`),
    so nothing in the SIGNUP PATH can abort it — an abort is a navigation or
    context teardown superseding the in-flight fetch, i.e. a never-answered
    request, which #4940's taxonomy buckets as UNAVAILABLE.

    The verdict is RETURNED rather than acted on so the split itself is pinnable:
    a message-only pin cannot see the buckets collapse, and collapsing them is how
    a product regression gets reported as an availability blip and exits the suite
    green.

    Run 35780459764 (2026-09-22T20:27:57Z, `main`) failed on the pre-#4940 message
    while the page had loaded and every locator had filled and clicked — a form
    that never submitted because it was never served fails on a locator timeout,
    not here — and the test had simply waited out its whole 30s budget.
    """
    if transport_failures:
        return (
            "unavailable",
            "no POST to the BFF /auth/signup got no response (observed: "
            + "; ".join(transport_failures)
            + "); the smoke does not attribute a cause",
        )
    if post_issued:
        return (
            "unavailable",
            "the POST to the BFF /auth/signup was issued and no response arrived within "
            "the poll budget — the smoke cannot distinguish an unreachable host from a "
            "hung BFF route, so it does not assert a cause",
        )
    return (
        "product",
        "no POST to the BFF /auth/signup was observed — the form did not "
        "submit, or it is still posting straight to Supabase",
    )


def _post_issued_flag() -> dict:
    """The `post_issued` register for the smoke, initially False.

    `post_issued` is the ONLY discriminator between "the form never submitted"
    (PRODUCT) and "issued but never answered" (UNAVAILABLE), so its initial value
    is pinned here rather than trusted: hardcoding it True turns a product
    regression into a SKIP and the suite exits GREEN (#4940 review round 6).
    """
    return {"value": False}


def _register_signup_capture(
    page,
    *,
    signup: dict,
    transport_failures: list[str],
    post_issued: dict,
    browser_to_supabase: list[str],
) -> None:
    """Register the smoke's three capture listeners. The `page` is INJECTED.

    Injecting `page` is what makes the live wiring executable browser-free: a
    recording double captures the registered handlers, and they can then be
    replayed with fake request/response objects. Without this, the pins covered
    only the pure helpers BENEATH the closures — so a dead `_on_response` body, a
    widened request filter, or a dropped verifier argument all survived, and three
    of those turn a product regression into a GREEN skip (#4940 review round 7,
    which measured exactly that against the previous revision).
    """

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
        _note_signup_response(resp, signup, browser_to_supabase)

    def _on_request(req):
        # #4940: record that the BFF POST was ACTUALLY ISSUED.
        if _is_bff_signup_request(req.method, req.url):
            post_issued["value"] = True

    def _on_requestfailed(req):
        # #4940: a request that did not produce a usable response arrives here;
        # the transport reason is in `req.failure` (e.g.
        # "net::ERR_NAME_NOT_RESOLVED" / "net::ERR_CONNECTION_REFUSED").
        entry = _signup_transport_failure(req.method, req.url, req.failure)
        if entry is not None:
            transport_failures.append(entry)

    page.on("response", _on_response)
    page.on("request", _on_request)
    page.on("requestfailed", _on_requestfailed)


def _dispose_captured(
    signup: dict,
    transport_failures: list[str],
    post_issued: dict,
    browser_to_supabase: list[str],
    *,
    skip,
    fail,
) -> None:
    """Dispose of a captured-nothing signup POST, taking the live STATE by reference.

    The call site passes state rather than `post_issued["value"]` so the read of
    the live register is inside a pinned function: spelled out at the call site,
    hardcoding it `True` survived every pin while turning a "form never submitted"
    PRODUCT regression into a skip (#4940 review round 7).
    """
    _handle_unobserved(
        transport_failures,
        post_issued["value"],
        browser_to_supabase,
        skip=skip,
        fail=fail,
    )


def _note_signup_response(resp, signup: dict, browser_to_supabase: list[str]) -> None:
    """Record a BFF signup response, or the #4054 tripwire. Pure: no playwright state.

    Extracted so the CAPTURE is pinnable with a fake response. A dead body here
    left every other pin green while a real non-200 was never recorded — so
    `status` stayed None, the case became UNAVAILABLE, and a 429 regression exited
    the suite GREEN with the no-429 contract unverified (#4940 review round 5).
    """
    if _is_bff_signup_request(resp.request.method, resp.url):
        signup["status"] = resp.status
        signup["body"] = resp.text()[:400]
    elif "v1/signup/email" in resp.url or "grant_type=password" in resp.url:
        browser_to_supabase.append(resp.url)


def _handle_unobserved(
    transport_failures: list[str],
    post_issued: bool,
    browser_to_supabase: list[str],
    *,
    skip,
    fail,
) -> None:
    """Dispose of a captured-nothing signup POST. The runner callables are injected.

    Pure and injectable so every property here is an assertion against recording
    doubles instead of source text — which round 6 measured to be the wrong
    instrument (a reformat false-RED it, while `or True` stayed green). What this
    pins, because each has been a real defect:

    - the TRIPWIRE is checked BEFORE any skip. `skip` raises, so checking it after
      would discard evidence the tripwire exists to collect (#4940 round 4);
    - an UNAVAILABLE verdict SKIPS and anything else FAILS (#4940 round 3);
    - the observed transport evidence reaches the message rather than being
      dropped (#4940 round 6).
    """
    if browser_to_supabase:
        fail(
            "the browser called Supabase directly instead of going through the "
            f"BFF: {browser_to_supabase}"
        )
        return
    verdict, message = _unobserved_outcome(
        transport_failures=transport_failures, post_issued=post_issued
    )
    if verdict == "unavailable":
        skip(message)
        return
    fail(message)

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


def test_signup_transport_failure_is_filtered_and_formatted() -> None:
    """#4940: pins the LISTENER half, not just the message half.

    A pin over the message alone cannot see a deleted listener or a broken
    method/URL filter — deleting `page.on("requestfailed", ...)` left it green.
    """
    url = "https://app.premiselabs.co/auth/signup"
    assert _is_bff_signup_request("POST", url)
    # A different method or path must not be recorded as a signup failure.
    assert not _is_bff_signup_request("GET", url)
    assert not _is_bff_signup_request("POST", "https://app.premiselabs.co/auth/login")

    assert _signup_transport_failure("POST", url, "net::ERR_NAME_NOT_RESOLVED") == (
        f"POST {url} — net::ERR_NAME_NOT_RESOLVED"
    )
    assert _signup_transport_failure("GET", url, "net::ERR_ABORTED") is None
    assert _signup_transport_failure("POST", "https://x/auth/other", "net::ERR_ABORTED") is None
    # `Request.failure` is Optional[str]; it must not render as "None".
    assert _signup_transport_failure("POST", url, None) == (
        f"POST {url} — unknown transport failure"
    )


def test_unobserved_outcome_is_disposed_correctly() -> None:
    """#4940: the skip/fail DISPATCH is pinned as behaviour, by injection.

    Source-text pins could not establish this: swapping the two call-site branches
    kept every assertable string and left a product regression exiting GREEN.
    """
    calls: list[tuple[str, str]] = []

    def _skip(message: str) -> None:
        calls.append(("skip", message))

    def _fail(message: str) -> None:
        calls.append(("fail", message))

    # A POST issued but never answered is UNAVAILABLE -> skip.
    _handle_unobserved([], True, [], skip=_skip, fail=_fail)
    # Nothing issued at all is a PRODUCT condition -> fail.
    _handle_unobserved([], False, [], skip=_skip, fail=_fail)
    # Observed transport evidence must REACH the message, not be dropped.
    _handle_unobserved(["obs ERR_NAME_NOT_RESOLVED"], True, [], skip=_skip, fail=_fail)
    # A client-side abort is never-answered, i.e. UNAVAILABLE (not product).
    _handle_unobserved(["POST ... net::ERR_ABORTED"], True, [], skip=_skip, fail=_fail)
    # The tripwire MUST win over a skip: `skip` raises, so a guard placed second
    # discards the evidence it exists to collect (#4940 rounds 4 and 6).
    _handle_unobserved([], True, ["https://x/auth/v1/token"], skip=_skip, fail=_fail)

    assert [kind for kind, _ in calls] == ["skip", "fail", "skip", "skip", "fail"]
    assert "ERR_NAME_NOT_RESOLVED" in calls[2][1]
    assert "Supabase directly" in calls[4][1]


def test_post_issued_flag_starts_false() -> None:
    """#4940: the register must start False, pinned as behaviour.

    Hardcoding it True leaves every other pin green while turning a
    "form never submitted" PRODUCT regression into a SKIP — a GREEN exit with the
    product fault unreported (#4940 review round 6).
    """
    flag = _post_issued_flag()
    assert flag == {"value": False}
    # It must be a mutable register: the `request` listener sets it in place, so a
    # fresh immutable object per call would silently break the signal.
    flag["value"] = True
    assert flag == {"value": True}


def test_signup_capture_listeners_are_registered_and_wired() -> None:
    """#4940: the LIVE wiring is pinned by replaying the registered handlers.

    Round 7 measured the hole this closes. The pins covered only the pure helpers
    BENEATH the closures, so deleting a listener body, widening its filter, or
    dropping the verifier's argument all survived — and three of those turn a real
    product regression into a GREEN skip on a production monitor. A recording
    `page` executes the registration; replaying each handler with a fake
    request/response executes the wiring. Nothing here is a source-text scan.

    Scope, stated so it is not over-read: this pins the callbacks' behaviour, not
    that playwright itself delivers events to them.
    """

    class _Page:
        def __init__(self) -> None:
            self.handlers: dict = {}

        def on(self, event: str, fn) -> None:
            self.handlers[event] = fn

    class _Req:
        def __init__(self, method: str, url: str, failure: str | None = None) -> None:
            self.method = method
            self.url = url
            self.failure = failure

    class _Resp:
        def __init__(self, method: str, url: str, status: int) -> None:
            self.request = _Req(method, url)
            self.url = url
            self.status = status

        def text(self) -> str:
            return "rate limited"

    signup_url = "https://app.premiselabs.co/auth/signup"
    page = _Page()
    signup: dict = {"status": None, "body": ""}
    failures: list[str] = []
    post_issued = _post_issued_flag()
    tripwire: list[str] = []

    _register_signup_capture(
        page,
        signup=signup,
        transport_failures=failures,
        post_issued=post_issued,
        browser_to_supabase=tripwire,
    )
    assert set(page.handlers) == {"response", "request", "requestfailed"}

    # The contract response must be RECORDED. A dead body made a real 429 exit
    # GREEN with the no-429 contract unverified (rounds 5 and 7).
    page.handlers["response"](_Resp("POST", signup_url, 429))
    assert signup["status"] == 429
    assert signup["body"] == "rate limited"
    assert tripwire == []
    # ...and a non-signup response must NOT overwrite it (a widened filter would
    # take the verdict from the first document/asset response).
    page.handlers["response"](_Resp("POST", "https://app.premiselabs.co/assets/x.js", 200))
    assert signup["status"] == 429
    # ...and the #4054 tripwire still records a browser->Supabase reach.
    page.handlers["response"](
        _Resp("POST", "https://x.supabase.co/auth/v1/token?grant_type=password", 200)
    )
    assert tripwire == ["https://x.supabase.co/auth/v1/token?grant_type=password"]

    # `post_issued` must be set by the SIGNUP POST only: widened, the page's own
    # navigations set it and a "form never submitted" PRODUCT regression is
    # reported UNAVAILABLE and skipped (rounds 6 and 7).
    page.handlers["request"](_Req("GET", "https://app.premiselabs.co/signup"))
    assert post_issued["value"] is False
    page.handlers["request"](_Req("POST", signup_url))
    assert post_issued["value"] is True

    # The transport verifier must record the REASON, which is the evidence #4940
    # requires the message to state.
    page.handlers["requestfailed"](
        _Req("GET", "https://app.premiselabs.co/assets/x.js", "net::ERR_ABORTED")
    )
    assert failures == []
    page.handlers["requestfailed"](_Req("POST", signup_url, "net::ERR_NAME_NOT_RESOLVED"))
    assert failures == [f"POST {signup_url} — net::ERR_NAME_NOT_RESOLVED"]


def test_dispose_captured_reads_the_live_register() -> None:
    """#4940: the call site must pass STATE, and the read must be the live value.

    With `post_issued["value"]` spelled out at the call site, hardcoding it `True`
    survived every pin while turning a "form never submitted" PRODUCT regression
    into a skip — a GREEN exit (round 7). `_dispose_captured` owns that read.
    """
    calls: list[tuple[str, str]] = []
    skip = lambda m: calls.append(("skip", m))  # noqa: E731
    fail = lambda m: calls.append(("fail", m))  # noqa: E731

    _dispose_captured({"status": None}, [], {"value": False}, [], skip=skip, fail=fail)
    assert [kind for kind, _ in calls] == ["fail"]

    calls.clear()
    _dispose_captured({"status": None}, [], {"value": True}, [], skip=skip, fail=fail)
    assert [kind for kind, _ in calls] == ["skip"]

    # The tripwire still wins over a skip, through this entry point too.
    calls.clear()
    _dispose_captured(
        {"status": None}, [], {"value": True}, ["https://x/auth/v1/token"], skip=skip, fail=fail
    )
    assert [kind for kind, _ in calls] == ["fail"]



def test_unobserved_outcome_splits_verdicts() -> None:
    """#4940: the VERDICT split is pinned, not just the message text.

    Collapsing the buckets is the failure that matters: if every unobserved case
    became UNAVAILABLE, a product regression would exit the suite GREEN. This
    test reddens on that collapse and on the split being reverted to the old
    single assert.
    """
    signup_url = "https://app.premiselabs.co/auth/signup"

    # A client-side abort (net::ERR_ABORTED) is a never-answered request, not a
    # product failure: the signup fetch has no AbortController, so the page cannot
    # abort its own POST — it is browser/lifecycle cancellation.
    verdict, message = _unobserved_outcome(
        transport_failures=["POST https://app.premiselabs.co/auth/signup — net::ERR_ABORTED"],
        post_issued=True,
    )
    assert verdict == "unavailable"

    # Host/dependency transport death is UNAVAILABLE.
    verdict, message = _unobserved_outcome(
        transport_failures=[f"POST {signup_url} — net::ERR_NAME_NOT_RESOLVED"],
        post_issued=True,
    )
    assert verdict == "unavailable"
    # Round 6: the message must state the OBSERVATION and decline a cause. Naming a
    # layer was itself a claim the file contradicts — `net::ERR_ABORTED` also lands
    # in this branch and the docstring says an abort is not reachability.
    assert "net::ERR_NAME_NOT_RESOLVED" in message
    assert "does not attribute a cause" in message
    # The two fabricated product causes must NOT appear when a transport failure
    # is what actually explains the absence.
    assert "did not submit" not in message
    assert "Supabase" not in message

    # Issued but never answered (connection blackhole / slow connect timeout): no
    # failure event fires, so this must not fall through to the product cause.
    verdict, message = _unobserved_outcome(transport_failures=[], post_issued=True)
    assert verdict == "unavailable"
    assert "did not submit" not in message
    assert "Supabase" not in message
    assert "no response" in message

    # Nothing issued at all IS the product condition — the original wording is
    # right here, and is now selected rather than assumed.
    verdict, message = _unobserved_outcome(transport_failures=[], post_issued=False)
    assert verdict == "product"
    assert "did not submit" in message
    assert "TRANSPORT" not in message


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
    # #4940: a request that never gets an ANSWER fires no `response` event, so the
    # response-only capture below cannot tell "the BFF did not answer" from "the
    # form never submitted". Record transport-level failures separately so the
    # assertion can name the condition it actually observed.
    transport_failures: list[str] = []
    # #4940: set by the `request` listener when the BFF POST is actually issued.
    # A request that is issued but never answered fires neither `response` nor
    # `requestfailed`, so this flag is the only thing that distinguishes that
    # availability condition from "the form never submitted".
    post_issued = _post_issued_flag()
    # Tripwire for the BFF contract (#4054): these Supabase endpoints must never
    # be reached FROM THE BROWSER. Before the move the page called them
    # directly; now it must not — for the BFF session the browser holds only the
    # HttpOnly handle.
    browser_to_supabase: list[str] = []

    _register_signup_capture(
        page,
        signup=signup,
        transport_failures=transport_failures,
        post_issued=post_issued,
        browser_to_supabase=browser_to_supabase,
    )
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
        if signup["status"] is None:
            # #4940/#4686: the BFF never ANSWERED. The verdict split, the tripwire's
            # precedence over a skip, and the evidence that reaches the message all
            # live in `_handle_unobserved`, pinned behaviourally below rather than
            # trusted.
            _dispose_captured(
                signup,
                transport_failures,
                post_issued,
                browser_to_supabase,
                skip=pytest.skip,
                fail=pytest.fail,
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
