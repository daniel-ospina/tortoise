"""E2E regression tests for signup form safety (#527) — gated like the legal
suite (RUN_LEGAL_E2E=1, local previews; ALLOW_PROD=1 for prod URLs).

Covers the production-failure contracts fixed in #527, retargeted to the BFF:
  1. JS-disabled form submission must NOT echo credentials into the URL
     (the original "static shell" behavior — ?email=...&password=...).
  2. The production-verified 429 over_email_send_rate_limit must render the
     friendly humanized copy, not the raw "Email rate limit exceeded".
  3. A backend fault must surface a clear, retryable "temporarily unavailable"
     state instead of a dead form (the historical trigger for #1). The old
     subject was a blocked supabase-js CDN; that script is GONE from the page
     (pinned by tests/test_signup_form_safety.py::
     test_migrated_auth_page_has_no_client_cdn_machinery and by the
     `@supabase/supabase-js` marker in
     tests/e2e/auth/test_signup_page_bff_static.py), and the page's own comment
     names the replacement: "a BFF fault surfaces as an honest 503".
     The healthy-load half of that pair is asserted separately: a CLEAN load
     must present an enabled form with no error banner
     (`test_healthy_load_shows_no_error_banner`), which is what the deleted
     watchdog test's false-fault assertion was really protecting
     (`test_healthy_load_does_not_show_watchdog_error`, deleted with the CDN).

#3501/#4054 BFF contract: the page belongs to the `tortoise-dashboard` project
(app.premiselabs.co, served locally by `wrangler pages dev dist` on :8790) and
its submit calls the SAME-ORIGIN `/auth/signup` route — never the retired
`api.premiselabs.co/v1/signup/email` (server-first) nor GoTrue's
`auth/v1/signup`. A provider rate-limit arrives as HTTP 429 carrying the
provider's own code in `providerError` (see `bffError()` in signup.html), and a
provider refusal as 400. The mocks below therefore answer those two routes only.

The APP origin is REQUIRED: `/auth` and `/signup` on the marketing origin are
only 301s to the app origin since #4054, so a BASE_URL-targeted run would follow
that redirect into DEPLOYED PRODUCTION and assert against whatever main last
shipped.

Run:
  cd website/apps/dashboard && npm ci && npm run build
    && npx wrangler@4 pages dev dist --port 8790 --ip 127.0.0.1 --d1 SESSIONS
  RUN_LEGAL_E2E=1 APP_BASE_URL=http://127.0.0.1:8790 \
    python -m pytest tests/e2e/test_signup_form_safety_e2e.py -v
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid

import pytest
from playwright.sync_api import Page, expect

if not os.environ.get("RUN_LEGAL_E2E"):
    pytest.skip("signup safety suite: opt-in via RUN_LEGAL_E2E=1",
                allow_module_level=True)

# The app origin — the project that serves the page AND the BFF it calls.
APP_BASE_URL = os.environ.get("APP_BASE_URL", "")
if not APP_BASE_URL and os.environ.get("BASE_URL", "").startswith("https://"):
    APP_BASE_URL = "https://app.premiselabs.co"
if not APP_BASE_URL:
    pytest.fail(
        "APP_BASE_URL is not set — this suite drives the app origin's signup "
        "page (the marketing origin only 301s to it since #4054). Local runs "
        "must point APP_BASE_URL at the dashboard preview, e.g. "
        "http://127.0.0.1:8790."
    )
if APP_BASE_URL.startswith("https://") and os.environ.get("ALLOW_PROD") != "1":
    pytest.skip("ALLOW_PROD=1 required to run against production",
                allow_module_level=True)

SIGNUP_URL = APP_BASE_URL.rstrip("/") + "/signup"

# Browser-level network log noise from deliberately-failed requests (429 / 5xx)
# — not page JS errors; the zero-console-errors assertions filter it.
_RESOURCE_LOG_RE = re.compile(r"Failed to load resource")


def _page_js_errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text)
            if m.type == "error" and not _RESOURCE_LOG_RE.search(m.text) else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


def _open_signup(page: Page) -> None:
    """Load the app origin's signup page and open the email modal."""
    # The post-signup navigation target defaults to PRODUCTION
    # (`__DASHBOARD_BASE_URL`), which would carry the whole test to
    # app.premiselabs.co; pin it to the origin under test.
    page.add_init_script(
        f"window.__DASHBOARD_BASE_URL = {json.dumps(APP_BASE_URL.rstrip('/'))};"
    )
    page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=30_000)
    page.locator("#btn-email").click()


def test_js_disabled_modal_unreachable_no_credential_echo(browser) -> None:
    """#527 original bug: with JS disabled (CDN blocked / CSP / regression),
    credentials must never reach the URL. The email form now lives in a modal
    opened by JS (#1494) — without JS it stays closed, so there is NO
    submittable credential surface at all (stronger than the old method=post
    belt, and the modal forms still carry method=post + explicit action per
    the #527 contract)."""
    with browser.new_context(java_script_enabled=False) as nojs_ctx:
        nojs_page = nojs_ctx.new_page()
        nojs_page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=30_000)
        # Neither modal can open without JS — the credential forms are
        # unreachable, so nothing can echo into the URL.
        assert nojs_page.locator("#email-modal").is_hidden()
        assert nojs_page.locator("#email-form").is_hidden()
        assert nojs_page.locator("#apikey-modal").is_hidden()
        url = nojs_page.url
        assert "email=" not in url, f"credentials echoed into URL: {url}"
        assert "password=" not in url, f"credentials echoed into URL: {url}"


def test_429_signup_rate_limit_is_humanized(page: Page) -> None:
    """The production-verified failure (over_email_send_rate_limit 429) must
    show friendly copy and keep the URL clean.

    The BFF carries the provider's mechanism in `providerError`; the page's
    `bffError()` maps it onto the {code, message} pair the lockout and
    humanizer helpers read."""
    console_errors = _page_js_errors(page)
    calls = {"n": 0}

    def handle(route):
        if "/auth/signup" not in route.request.url:
            route.continue_()
            return
        if route.request.method != "POST":
            route.continue_()
            return
        calls["n"] += 1
        route.fulfill(status=429, content_type="application/json",
                      body=json.dumps({"providerError": "over_email_send_rate_limit",
                                       "message": "email rate limit exceeded"}))

    page.route("**/auth/signup*", handle)
    _open_signup(page)
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
        if "/auth/signup" not in route.request.url:
            route.continue_()
            return
        if route.request.method != "POST":
            route.continue_()
            return
        route.fulfill(status=429, content_type="application/json",
                      body=json.dumps({"providerError": "over_request_rate_limit_ip",
                                       "message": "request rate limit reached"}))

    page.route("**/auth/signup*", handle)
    _open_signup(page)
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
    """#801: only rate-limit errors may trigger the lockout — a provider
    REFUSAL (400 + `providerError`, the BFF's shape for validation/
    already-exists/weak-password) must leave the form usable and write NO
    storage key."""
    console_errors = _page_js_errors(page)

    def handle(route):
        if "/auth/signup" not in route.request.url:
            route.continue_()
            return
        if route.request.method != "POST":
            route.continue_()
            return
        route.fulfill(status=400, content_type="application/json",
                      body=json.dumps({"providerError": "validation_failed",
                                       "message": "Invalid email or password"}))

    # NB: password must satisfy the input's native minlength="6" (browser
    # validation would otherwise block the submit event entirely) — the
    # mocked refusal exercises the server-error path.
    page.route("**/auth/signup*", handle)
    _open_signup(page)
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
    """#801: a 429 on `/auth/resend` (an HTTP STATUS, not a silent
    {data,error} tuple) must NOT show the false 'Resent' success; it must set
    the lockout and show the rate-limit note. Resend burns the same
    project-wide bucket.

    The confirmation-email funnel is the API's opt-in mode, surfaced by the
    route as `confirmationRequired:true` — that is how the deployed page
    reaches the check-your-inbox state (and its resend button) at all."""
    console_errors = _page_js_errors(page)
    resend_calls = {"n": 0}

    def handle(route):
        url = route.request.url
        if "/auth/signup" in url and route.request.method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"confirmationRequired": True}))
            return
        if "/auth/resend" in url and route.request.method == "POST":
            resend_calls["n"] += 1
            route.fulfill(status=429, content_type="application/json",
                          body=json.dumps({"providerError": "over_email_send_rate_limit",
                                           "message": "email rate limit exceeded"}))
            return
        route.continue_()

    page.route("**/auth/signup*", handle)
    page.route("**/auth/resend*", handle)
    _open_signup(page)
    page.locator("#email").fill("resend-429@premise-labs.dev")
    page.locator("#password").fill("ResendPass-429!")
    page.locator("#btn-submit").click()

    # inbox state visible (confirmationRequired)
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


def test_backend_unavailable_shows_retryable_not_dead_form(page: Page) -> None:
    """A BFF/provider fault (503) must surface the honest, retryable copy and
    leave the form usable — never a dead form, and never a "signed out"/refusal
    interpretation. This is the migrated successor of the #527 blocked-CDN
    assertion: the CDN is gone, so the fault now arrives from the route.

    Distinct from a refusal: 503 is "we could not do it", so the button must
    come back and the message must invite a retry rather than blame the input.
    """
    console_errors = _page_js_errors(page)

    def handle(route):
        if "/auth/signup" not in route.request.url:
            route.continue_()
            return
        if route.request.method != "POST":
            route.continue_()
            return
        route.fulfill(status=503, content_type="application/json",
                      body=json.dumps({"error": "signup_unavailable",
                                       "message": "provider unreachable"}))

    page.route("**/auth/signup*", handle)
    _open_signup(page)
    page.locator("#email").fill("unavailable@premise-labs.dev")
    page.locator("#password").fill("Unavailable-1!")
    page.locator("#btn-submit").click()

    expect(page.locator("#error")).to_contain_text(
        "temporarily unavailable", timeout=10_000)
    # Retryable, not a lockout and not a refusal: the form stays usable.
    expect(page.locator("#btn-submit")).to_be_enabled(timeout=5_000)
    until = page.evaluate(
        "parseInt(sessionStorage.getItem('tortoise_signup_rate_limited_until') || '0', 10)")
    assert until == 0, f"a 503 wrote a lockout key: {until}"
    assert console_errors == [], f"page JS errors: {console_errors}"


def test_healthy_load_shows_no_error_banner(page: Page) -> None:
    """A HEALTHY load must present an enabled form and NO error banner.

    Successor to the deleted `test_healthy_load_does_not_show_watchdog_error`,
    whose watchdog fired a false "temporarily unavailable" when it misread its
    own init state — so a healthy load had to be asserted past the deadline. The
    watchdog went with the supabase-js CDN, but the property it guarded is still
    real: a clean load must not manufacture a fault.
    """
    console_errors = _page_js_errors(page)
    _open_signup(page)
    expect(page.locator("#btn-submit")).to_be_enabled(timeout=10_000)
    # Past the old 6s watchdog deadline — a reintroduced watchdog (or any other
    # false-fault path) fires by now.
    page.wait_for_timeout(6500)
    assert not page.locator("#error").is_visible(), \
        f"a healthy load showed an error banner: {page.locator('#error').inner_text()!r}"
    expect(page.locator("#confirmation-required")).to_be_hidden()
    assert console_errors == [], f"page JS errors: {console_errors}"


def test_mock_email_signup_created_signs_in_and_redirects_url_clean(page: Page) -> None:
    """Success path — the BFF contract: `/auth/signup` 200 with the created user
    means the account exists AND the route signed it in server-side, so the page
    navigates to its post-login target. Must keep the URL clean and push the
    x_signup conversion event (#736).

    `__DASHBOARD_BASE_URL` is pinned to the app origin under test (see
    `_open_signup`) — at its production default the navigation itself would
    carry the test to app.premiselabs.co."""
    email = f"e2e-{uuid.uuid4().hex[:8]}@premise-labs.dev"
    console_errors = _page_js_errors(page)
    # Stash the x_signup push in sessionStorage as it is MADE. pushSignupEvents
    # runs in the same task as the navigation, so the JS context is destroyed on
    # commit and a post-navigation read of window/dataLayer always misses it.
    # sessionStorage survives a same-origin navigation, so the value is still
    # there on the landing page — and unlike an exposed-function binding (which
    # is a round-trip that the immediately-following navigation can drop) the
    # stash is a synchronous write in the page.
    page.add_init_script("""
        (function () {
          var origPush = Array.prototype.push;
          var dl = (window.dataLayer = window.dataLayer || []);
          dl.push = function () {
            var entry = arguments[0];
            var result = origPush.apply(this, arguments);
            if (entry && entry.event === 'x_signup') {
              try { sessionStorage.setItem('__e2e_x_signup', JSON.stringify(entry)); } catch (e) {}
            }
            return result;
          };
        })();
    """)

    def handle(route):
        if "/auth/signup" not in route.request.url:
            route.continue_()
            return
        if route.request.method != "POST":
            route.continue_()
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"user": {"id": "mock-user", "email": email,
                                                "identities": [{"id": "mock-id"}]}}))

    page.route("**/auth/signup*", handle)
    _open_signup(page)
    page.locator("#email").fill(email)
    page.locator("#password").fill("E2ePass-12345!")
    page.locator("#btn-submit").click()

    # Created server-side → the page navigates to its post-login target, which
    # __DASHBOARD_BASE_URL pins to the app origin under test.
    # The landing URL is the pinned app origin AND a different path — asserting
    # only the origin prefix would match the signup page itself and pass before
    # the navigation happened.
    page.wait_for_url(
        lambda url: url.startswith(APP_BASE_URL) and url.rstrip("/") != SIGNUP_URL,
        timeout=15_000,
    )
    assert "email=" not in page.url and "password=" not in page.url
    raw = page.evaluate("() => sessionStorage.getItem('__e2e_x_signup')")
    assert raw, "x_signup entry missing from dataLayer"
    entry = json.loads(raw)
    assert entry.get("conversion_id") and entry.get("email") == email, \
        f"x_signup entry malformed: {entry}"
    assert console_errors == [], f"page JS errors: {console_errors}"
