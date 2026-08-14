"""E2E regression tests for signup form safety (#527) — gated like the legal
suite (RUN_LEGAL_E2E=1, local wrangler preview; ALLOW_PROD=1 for prod URLs).

Covers the three production-failure contracts fixed in #527:
  1. JS-disabled form submission must NOT echo credentials into the URL
     (the original "static shell" behavior — ?email=...&password=...).
  2. The production-verified 429 over_email_send_rate_limit must render the
     friendly humanized copy, not the raw "Email rate limit exceeded".
  3. A blocked Supabase CDN must surface a clear "temporarily unavailable"
     state instead of a dead form (the historical trigger for #1).

#801 server-first contract (the deployed signup flow, #1190): the submit
handler calls api.premiselabs.co/v1/signup/email — NOT the legacy
client-side auth/v1/signup, which only runs on local/dev previews or when
the server endpoint reports unavailable. The 429-mock tests therefore
intercept the server endpoint, with the rate-limit mechanism carried in
detail.error_code (email bucket vs per-IP) exactly as hosted_api.py sends
it. The legacy client-side path remains covered through the
503-degradation resend test (the only way the deployed page reaches the
check-your-inbox state).

Run:
  cd website && npx wrangler@4 pages dev . --port 8788 --ip 127.0.0.1
  RUN_LEGAL_E2E=1 BASE_URL=http://127.0.0.1:8788 \
    python -m pytest tests/e2e/test_signup_form_safety_e2e.py -v
"""
from __future__ import annotations

import json
import os
import time
import re
import uuid

import pytest
from playwright.sync_api import Page, expect

if not os.environ.get("RUN_LEGAL_E2E"):
    pytest.skip("signup safety suite: opt-in via RUN_LEGAL_E2E=1",
                allow_module_level=True)

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8788")
if BASE_URL.startswith("https://") and os.environ.get("ALLOW_PROD") != "1":
    pytest.skip("ALLOW_PROD=1 required to run against production",
                allow_module_level=True)

# Browser-level network log noise from deliberately-failed requests (429 /
# blocked CDN) — not page JS errors; the zero-console-errors assertions filter it.
_RESOURCE_LOG_RE = re.compile(r"Failed to load resource")

# CORS preflight headers for the mocked cross-origin endpoints (the browser
# preflights the api.premiselabs.co fetch and the supabase auth calls before
# the real POST — the OPTIONS must be fulfilled locally or the request is
# blocked before the mock can answer).
_CORS_PREFLIGHT = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
    "Access-Control-Allow-Headers": "*",
}

# The deployed form is SERVER-FIRST on the hosted site (#801) but runs the
# LEGACY client-side auth/signUp flow on local/dev previews (isLocal in
# signup.html). Tests mock BOTH endpoints so both documented run modes work:
#   prod (BASE_URL=https://...)        -> /v1/signup/email (server-first)
#   local wrangler preview (127.0.0.1) -> auth/v1/signup (legacy fallback)
IS_LOCAL = "127.0.0.1" in BASE_URL or "localhost" in BASE_URL


def _page_js_errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text)
            if m.type == "error" and not _RESOURCE_LOG_RE.search(m.text) else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


def test_js_disabled_form_submission_does_not_echo_credentials(
        browser) -> None:
    """#527 original bug: with JS disabled (CDN blocked / CSP / regression),
    the native form submission must not put credentials in the URL. method=post
    + action=/signup means the browser POSTs to /signup and Cloudflare Pages
    discards the body — the URL stays clean."""
    with browser.new_context(java_script_enabled=False) as nojs_ctx:
        nojs_page = nojs_ctx.new_page()
        nojs_page.goto(BASE_URL + "/signup", wait_until="domcontentloaded", timeout=30_000)
        nojs_page.locator("#email").fill("nojs-527@premise-labs.dev")
        nojs_page.locator("#password").fill("NoJsPass-527!")
        nojs_page.locator("#btn-submit").click()
        nojs_page.wait_for_timeout(800)
        url = nojs_page.url
        assert "email=" not in url, f"credentials echoed into URL: {url}"
        assert "password=" not in url, f"credentials echoed into URL: {url}"


def test_429_signup_rate_limit_is_humanized(page: Page) -> None:
    """The production-verified failure (over_email_send_rate_limit 429) must
    show friendly copy and keep the URL clean.

    #801 server-first: on the hosted site the submit calls
    api.premiselabs.co/v1/signup/email (the API carries the mechanism in
    detail.error_code); on local previews the legacy client-side
    auth/v1/signup path runs instead. Both endpoints are mocked (the page
    exercises exactly one of them per mode)."""
    console_errors = _page_js_errors(page)
    calls = {"n": 0}

    def handle(route):
        url = route.request.url
        if "v1/signup/email" in url:
            if route.request.method == "OPTIONS":  # CORS preflight (cross-origin fetch to api.premiselabs.co)
                route.fulfill(status=204, headers=_CORS_PREFLIGHT)
                return
            if route.request.method == "POST":
                calls["n"] += 1
                # #801 server-first: the API carries the mechanism in
                # detail.error_code (hosted_api.py /v1/signup/email).
                route.fulfill(status=429, content_type="application/json",
                              headers={"Access-Control-Allow-Origin": "*"},
                              body=json.dumps({"detail": {
                                  "error_code": "over_email_send_rate_limit",
                                  "message": "email rate limit exceeded"}}))
            return
        if "auth/v1/signup" in url:
            # local-preview (isLocal) legacy path — same email-bucket 429.
            if route.request.method == "OPTIONS":  # CORS preflight
                route.fulfill(status=204, headers=_CORS_PREFLIGHT)
                return
            if route.request.method == "POST":
                calls["n"] += 1
                route.fulfill(status=429, content_type="application/json",
                              headers={"Access-Control-Allow-Origin": "*"},
                              body=json.dumps({"code": 429,
                                               "error_code": "over_email_send_rate_limit",
                                               "msg": "email rate limit exceeded"}))
            return
        route.continue_()

    page.route("**/v1/signup/email*", handle)
    page.route("**/auth/v1/signup*", handle)
    page.goto(BASE_URL + "/signup", wait_until="domcontentloaded", timeout=30_000)
    page.locator("#email").fill("rate-527@premise-labs.dev")
    page.locator("#password").fill("RatePass-527!")
    page.locator("#btn-submit").click()

    # #863: over_email_send_rate_limit is the PROJECT-WIDE email bucket —
    # the page must show the mechanism-accurate email-bucket copy, not the
    # network-attribution sentence.
    expect(page.locator("#error")).to_contain_text(
        "Signup emails are temporarily exhausted", timeout=10_000)
    assert "email=" not in page.url and "password=" not in page.url
    assert "Email rate limit exceeded" not in page.locator("#error").inner_text()

    # ── #801 lockout: disabled button + countdown + storage + early return ──
    btn = page.locator("#btn-submit")
    expect(btn).to_be_disabled(timeout=5_000)
    assert re.search(r"Try again in \d{2}:\d{2}", btn.inner_text()), btn.inner_text()
    # resend is force-disabled during the lockout (shared email bucket, #801)
    expect(page.locator("#btn-resend")).to_be_disabled(timeout=5_000)
    until = page.evaluate(
        "parseInt(sessionStorage.getItem('tortoise_signup_rate_limited_until') || '0', 10)")
    now_ms = time.time() * 1000
    # Discriminating two-tier assert: the mock is over_email_send_rate_limit
    # (email bucket) → lockout must be the 1h tier, so the remaining time must
    # be ~1h — a regression to the 60s tier would fail the lower bound.
    assert until - now_ms >= 50 * 60 * 1000, f"lockout NOT 1h tier: until={until}"
    assert until <= now_ms + 3_600_000 + 5_000, f"lockout until={until}"
    tier = page.evaluate(
        "sessionStorage.getItem('tortoise_signup_rate_limit_tier')")
    assert tier == "email", f"1h lockout tier mismatch: {tier!r}"
    # early-return guard: re-dispatching submit must NOT fire a second request
    page.evaluate("document.getElementById('email-form')"
                  ".dispatchEvent(new Event('submit', {cancelable: true}))")
    page.wait_for_timeout(500)
    assert calls["n"] == 1, f"lockout failed to guard: {calls['n']} signup requests"
    # persistence across reload
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#btn-submit")).to_be_disabled(timeout=5_000)
    assert re.search(r"Try again in \d{2}:\d{2}", btn.inner_text()), "countdown lost on reload"
    assert console_errors == [], f"page JS errors: {console_errors}"


def test_429_short_tier_lockout_60s_then_expiry(page: Page) -> None:
    """#801 two-tier: a per-IP auth-attempt 429 (over_request_rate_limit_ip) must
    lock out for ~60s (NOT 1h), show the short-tier copy, and fully recover on
    expiry — submit re-enabled with the original label, no stale lockout."""
    console_errors = _page_js_errors(page)

    def handle(route):
        url = route.request.url
        if "v1/signup/email" in url:
            if route.request.method == "OPTIONS":  # CORS preflight
                route.fulfill(status=204, headers=_CORS_PREFLIGHT)
                return
            if route.request.method == "POST":
                route.fulfill(status=429, content_type="application/json",
                              headers={"Access-Control-Allow-Origin": "*"},
                              body=json.dumps({"detail": {
                                  "error_code": "over_request_rate_limit_ip",
                                  "message": "request rate limit reached"}}))
            return
        if "auth/v1/signup" in url:
            # local-preview (isLocal) legacy path — same per-IP 429.
            if route.request.method == "OPTIONS":  # CORS preflight
                route.fulfill(status=204, headers=_CORS_PREFLIGHT)
                return
            if route.request.method == "POST":
                route.fulfill(status=429, content_type="application/json",
                              headers={"Access-Control-Allow-Origin": "*"},
                              body=json.dumps({"code": 429,
                                               "error_code": "over_request_rate_limit_ip",
                                               "msg": "request rate limit reached"}))
            return
        route.continue_()

    page.route("**/v1/signup/email*", handle)
    page.route("**/auth/v1/signup*", handle)
    page.goto(BASE_URL + "/signup", wait_until="domcontentloaded", timeout=30_000)
    page.locator("#email").fill("rate-short@premise-labs.dev")
    page.locator("#password").fill("RatePass-Short!")
    page.locator("#btn-submit").click()

    expect(page.locator("#error")).to_contain_text(
        "Too many attempts from this network", timeout=10_000)
    expect(page.locator("#btn-submit")).to_be_disabled(timeout=5_000)
    until = page.evaluate(
        "parseInt(sessionStorage.getItem('tortoise_signup_rate_limited_until') || '0', 10)")
    now_ms = time.time() * 1000
    # short tier: remaining must be ~60s (≤ 2 min), NOT the 1h tier
    assert 0 < until - now_ms <= 2 * 60 * 1000, f"lockout NOT 60s tier: until={until}"
    tier = page.evaluate(
        "sessionStorage.getItem('tortoise_signup_rate_limit_tier')")
    assert tier == "short", f"60s lockout tier mismatch: {tier!r}"
    # short-tier copy: not the pinned 1h sentence
    assert "about an hour" not in page.locator("#error").inner_text(), \
        "short-tier lockout shows the 1h copy"
    # expiry: force the stored timestamp AND the in-memory mirror into the
    # past, then re-apply — the ms<=0 branch must restore the button + label,
    # clear the timer, drop the stale message, and remove the tier key.
    page.evaluate("() => { sessionStorage.setItem('tortoise_signup_rate_limited_until', '1'); rateLimitUntil = 1; applyRateLimitLockout(); }")
    expect(page.locator("#btn-submit")).to_be_enabled(timeout=5_000)
    assert page.locator("#btn-submit").inner_text() == "Create account", \
        page.locator("#btn-submit").inner_text()
    # stale lockout message dropped (clearError removes the visible class;
    # inner_text would still show textContent for a hidden element)
    expect(page.locator("#error")).to_be_hidden(timeout=5_000)
    assert page.evaluate("sessionStorage.getItem('tortoise_signup_rate_limit_tier')") is None, \
        "tier key survived expiry"
    assert console_errors == [], f"page JS errors: {console_errors}"


def test_non_rate_limit_error_does_not_lock_out(page: Page) -> None:
    """#801: only rate-limit errors may trigger the lockout — a 422 from the
    server-first endpoint (validation; 400 is Turnstile-only per hosted_api.py)
    must leave the form usable and write NO storage key."""
    console_errors = _page_js_errors(page)

    def handle(route):
        url = route.request.url
        if "v1/signup/email" in url:
            if route.request.method == "OPTIONS":  # CORS preflight
                route.fulfill(status=204, headers=_CORS_PREFLIGHT)
                return
            if route.request.method == "POST":
                # Real endpoint contract (hosted_api.py /v1/signup/email):
                # validation failures are 422 with a STRING detail (never
                # str(ValidationError)); 400 is reserved for Turnstile. The
                # string-vs-object detail handling is what this exercises.
                route.fulfill(status=422, content_type="application/json",
                              headers={"Access-Control-Allow-Origin": "*"},
                              body=json.dumps({"detail": "Invalid email or password"}))
            return
        if "auth/v1/signup" in url:
            # local-preview (isLocal) legacy path — same non-rate-limit 422.
            if route.request.method == "OPTIONS":  # CORS preflight
                route.fulfill(status=204, headers=_CORS_PREFLIGHT)
                return
            if route.request.method == "POST":
                route.fulfill(status=422, content_type="application/json",
                              headers={"Access-Control-Allow-Origin": "*"},
                              body=json.dumps({"code": 422,
                                               "error_code": "validation_failed",
                                               "msg": "Invalid email or password"}))
            return
        route.continue_()

    # NB: password must satisfy the input's native minlength="6" (browser
    # validation would otherwise block the submit event entirely) — the
    # mocked 422 exercises the server-error path.
    page.route("**/v1/signup/email*", handle)
    page.route("**/auth/v1/signup*", handle)
    page.goto(BASE_URL + "/signup", wait_until="domcontentloaded", timeout=30_000)
    page.locator("#email").fill("nolate@premise-labs.dev")
    page.locator("#password").fill("ShortPass!")
    page.locator("#btn-submit").click()

    expect(page.locator("#error")).not_to_have_text("", timeout=10_000)
    expect(page.locator("#btn-submit")).to_be_enabled(timeout=5_000)
    until = page.evaluate(
        "parseInt(sessionStorage.getItem('tortoise_signup_rate_limited_until') || '0', 10)")
    assert until == 0, f"non-rate-limit error wrote a lockout key: {until}"
    assert console_errors == [], f"page JS errors: {console_errors}"


def test_resend_429_sets_lockout_and_disables_resend(page: Page) -> None:
    """#801: a 429 on auth.resend (returned, not thrown — supabase-js v2)
    must NOT show the false 'Resent' success; it must set the lockout and
    show the rate-limit note. Resend burns the same project-wide bucket.

    #801 server-first: the hosted page reaches the check-your-inbox state
    (with the resend button) only through the LEGACY client-side fallback —
    when the server endpoint reports unavailable (503) the form degrades to
    supabaseClient.auth.signUp. This test drives BOTH deployed paths:
    /v1/signup/email -> 503 (unavailable -> legacy fallback), then
    auth/v1/signup -> session-less success (inbox state), then
    auth/v1/resend -> 429 (lockout + note)."""
    console_errors = _page_js_errors(page)
    resend_calls = {"n": 0}

    def handle_server(route):
        url = route.request.url
        if "v1/signup/email" in url:
            if route.request.method == "OPTIONS":  # CORS preflight
                route.fulfill(status=204, headers=_CORS_PREFLIGHT)
                return
            if route.request.method == "POST":
                # server unavailable -> the page degrades to the legacy
                # client-side auth.signUp flow (which shows the inbox state).
                route.fulfill(status=503, content_type="application/json",
                              headers={"Access-Control-Allow-Origin": "*"},
                              body=json.dumps({"detail": "Email signup is not available on this deployment."}))
            return
        route.continue_()

    def handle_legacy(route):
        url = route.request.url
        if "auth/v1/signup" in url:
            if route.request.method == "OPTIONS":  # CORS preflight
                route.fulfill(status=204, headers=_CORS_PREFLIGHT)
                return
            if route.request.method == "POST":
                # session-less success -> check-your-inbox state (confirmations ON)
                route.fulfill(status=200, content_type="application/json",
                              headers={"Access-Control-Allow-Origin": "*"},
                              body=json.dumps({"user": {"id": "u-1", "email": "resend-429@premise-labs.dev",
                                                         "identities": [{"id": "u-1"}]}}))
            return
        if "auth/v1/resend" in url:
            if route.request.method == "OPTIONS":  # CORS preflight
                route.fulfill(status=204, headers=_CORS_PREFLIGHT)
                return
            if route.request.method == "POST":
                resend_calls["n"] += 1
                route.fulfill(status=429, content_type="application/json",
                              headers={"Access-Control-Allow-Origin": "*"},
                              body=json.dumps({"code": 429,
                                               "error_code": "over_email_send_rate_limit",
                                               "msg": "email rate limit exceeded"}))
            return
        route.continue_()

    page.route("**/v1/signup/email*", handle_server)
    page.route("**/auth/v1/**", handle_legacy)
    page.goto(BASE_URL + "/signup", wait_until="domcontentloaded", timeout=30_000)
    page.locator("#email").fill("resend-429@premise-labs.dev")
    page.locator("#password").fill("ResendPass-429!")
    page.locator("#btn-submit").click()

    # inbox state visible (confirmations ON)
    expect(page.locator("#confirmation-required")).to_be_visible(timeout=10_000)
    page.locator("#btn-resend").click()

    # 429 handled: rate-limit note (NOT the false success), lockout set,
    # resend force-disabled by the lockout
    expect(page.locator("#resend-note")).to_contain_text("Email limit reached", timeout=10_000)
    assert "Resent" not in page.locator("#resend-note").inner_text(), \
        "resend 429 reported as success"
    expect(page.locator("#btn-resend")).to_be_disabled(timeout=5_000)
    until = page.evaluate(
        "parseInt(sessionStorage.getItem('tortoise_signup_rate_limited_until') || '0', 10)")
    now_ms = time.time() * 1000
    assert until - now_ms >= 50 * 60 * 1000, f"resend 429 lockout NOT 1h tier: until={until}"
    assert resend_calls["n"] == 1
    assert console_errors == [], f"page JS errors: {console_errors}"


def test_blocked_supabase_cdn_shows_clear_error(page: Page) -> None:
    """Abort the supabase-js CDN request: the page must show the
    'temporarily unavailable' state (onerror belt / typeof guard), not a
    dead form."""
    console_errors = _page_js_errors(page)

    def handle(route):
        if "supabase.min.js" in route.request.url:
            route.abort()
            return
        route.continue_()

    page.route("**/*", handle)
    page.goto(BASE_URL + "/signup", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("#error")).to_contain_text(
        "temporarily unavailable", timeout=10_000)
    assert console_errors == [], f"page JS errors: {console_errors}"


def test_healthy_load_does_not_show_watchdog_error(page: Page) -> None:
    """Regression for the review P1: the watchdog reads window.supabaseClient
    (a top-level `let` is NOT a window property), so on a HEALTHY load — CDN
    present — the 6s watchdog must not fire a false 'temporarily unavailable'
    error. Waits past the watchdog deadline to catch the false positive."""
    page.goto(BASE_URL + "/signup", wait_until="domcontentloaded", timeout=30_000)
    # Give the client time to init, then wait past the 6s watchdog deadline.
    expect(page.locator("#btn-submit")).to_be_enabled(timeout=10_000)
    page.wait_for_timeout(6500)
    assert not page.locator("#error").is_visible(), \
        f"watchdog fired a false error on a healthy load: {page.locator('#error').inner_text()!r}"
    # And the client must still be functional.
    assert page.evaluate("() => !!window.supabaseClient"), "supabaseClient not exposed on window"


def test_mock_email_signup_created_signs_in_and_redirects_url_clean(page: Page) -> None:
    """Success path — the #801 server-first contract (created server-side →
    direct sign-in → /welcome redirect; the check-your-inbox state is NOT
    part of the hosted happy path, email_confirm=true server-side). Must
    keep the URL clean and push the x_signup conversion event (#736).

    Local-preview mode (isLocal) runs the legacy client-side flow instead —
    there the same mocks drive auth/v1/signup → session-less success → the
    check-your-inbox state, and no redirect happens."""
    email = f"e2e-{uuid.uuid4().hex[:8]}@premise-labs.dev"
    console_errors = _page_js_errors(page)
    captured = {"x_signup": None}
    # Capture the x_signup push SYNCHRONOUSLY in the page (pushSignupEvents
    # runs in the same task as the /welcome redirect — the JS context is
    # destroyed on commit, so post-navigation reads always miss it).
    page.expose_function("__e2eCaptureXSignup", lambda entry: captured.update(x_signup=entry))
    page.add_init_script("""
        (function () {
          var origPush = Array.prototype.push;
          var dl = (window.dataLayer = window.dataLayer || []);
          dl.push = function () {
            var entry = arguments[0];
            var result = origPush.apply(this, arguments);
            if (entry && entry.event === 'x_signup') window.__e2eCaptureXSignup(entry);
            return result;
          };
        })();
    """)

    def handle(route):
        url = route.request.url
        if "v1/signup/email" in url:
            if route.request.method == "OPTIONS":  # CORS preflight
                route.fulfill(status=204, headers=_CORS_PREFLIGHT)
                return
            if route.request.method == "POST":
                route.fulfill(status=200, content_type="application/json",
                              headers={"Access-Control-Allow-Origin": "*"},
                              body=json.dumps({"user_id": "mock-user", "email": email,
                                               "email_confirm": True, "message": "user_created"}))
            return
        if "auth/v1/signup" in url:
            # local-preview (isLocal) legacy path — session-less success →
            # the check-your-inbox state (no sign-in / redirect).
            if route.request.method == "OPTIONS":  # CORS preflight
                route.fulfill(status=204, headers=_CORS_PREFLIGHT)
                return
            if route.request.method == "POST":
                route.fulfill(status=200, content_type="application/json",
                              headers={"Access-Control-Allow-Origin": "*"},
                              body=json.dumps({"user": {"id": "mock-user", "email": email,
                                                         "identities": [{"id": "mock-id"}]}}))
            return
        if "auth/v1/token" in url:
            if route.request.method == "OPTIONS":  # CORS preflight
                route.fulfill(status=204, headers=_CORS_PREFLIGHT)
                return
            if route.request.method == "POST":
                # signInAndGo — the created account signs in directly with the
                # password; the session user carries identities.
                route.fulfill(status=200, content_type="application/json",
                          headers={"Access-Control-Allow-Origin": "*"},
                          body=json.dumps({
                              "access_token": "mock-at", "token_type": "bearer",
                              "expires_in": 3600, "refresh_token": "mock-rt",
                              "user": {"id": "mock-user", "email": email,
                                       "identities": [{"id": "mock-id"}]}}))
            return
        route.continue_()

    page.route("**/v1/signup/email*", handle)
    page.route("**/auth/v1/signup*", handle)
    page.route("**/auth/v1/token*", handle)
    page.goto(BASE_URL + "/signup", wait_until="domcontentloaded", timeout=30_000)
    page.locator("#email").fill(email)
    page.locator("#password").fill("E2ePass-12345!")
    page.locator("#btn-submit").click()

    if IS_LOCAL:
        # local-preview (isLocal) legacy path: session-less success → the
        # check-your-inbox state (no sign-in / redirect on the legacy flow).
        expect(page.locator("#confirmation-required")).to_be_visible(timeout=10_000)
        expect(page.locator("#confirm-email")).to_have_text(email)
    else:
        # Deployed happy path: server-side creation → direct sign-in →
        # redirect to the welcome page (no check-your-inbox step, #801).
        page.wait_for_url("**/welcome*", timeout=15_000)
    assert "email=" not in page.url and "password=" not in page.url
    assert captured["x_signup"], "x_signup entry missing from dataLayer"
    entry = captured["x_signup"]
    assert entry.get("conversion_id") and entry.get("email") == email, \
        f"x_signup entry malformed: {entry}"
    assert console_errors == [], f"page JS errors: {console_errors}"
