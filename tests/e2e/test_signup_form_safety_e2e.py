"""E2E regression tests for signup form safety (#527) — gated like the legal
suite (RUN_LEGAL_E2E=1, local wrangler preview; ALLOW_PROD=1 for prod URLs).

Covers the three production-failure contracts fixed in #527:
  1. JS-disabled form submission must NOT echo credentials into the URL
     (the original "static shell" behavior — ?email=...&password=...).
  2. The production-verified 429 over_email_send_rate_limit must render the
     friendly humanized copy, not the raw "Email rate limit exceeded".
  3. A blocked Supabase CDN must surface a clear "temporarily unavailable"
     state instead of a dead form (the historical trigger for #1).

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
    show friendly copy and keep the URL clean."""
    console_errors = _page_js_errors(page)
    calls = {"n": 0}

    def handle(route):
        url = route.request.url
        if "auth/v1/signup" in url and route.request.method == "POST":
            calls["n"] += 1
            route.fulfill(status=429, content_type="application/json",
                          headers={"Access-Control-Allow-Origin": "*"},
                          body=json.dumps({"code": 429,
                                           "error_code": "over_email_send_rate_limit",
                                           "msg": "email rate limit exceeded"}))
            return
        route.continue_()

    page.route("**/*", handle)
    page.goto(BASE_URL + "/signup", wait_until="domcontentloaded", timeout=30_000)
    page.locator("#email").fill("rate-527@premise-labs.dev")
    page.locator("#password").fill("RatePass-527!")
    page.locator("#btn-submit").click()

    expect(page.locator("#error")).to_contain_text(
        "Too many attempts from this network", timeout=10_000)
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
    assert now_ms < until <= now_ms + 3_600_000 + 5_000, f"lockout until={until}"
    # two-tier check: the mocked 429 is over_email_send_rate_limit (email bucket)
    # → lockout must be the 1h tier (SHORT_RATE_LIMIT_LOCKOUT_MS would fail this)
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


def test_mock_email_signup_inbox_state_url_clean(page: Page) -> None:
    """Success path (email confirmation ON → check-your-inbox state) must
    keep the URL clean and push the x_signup conversion event (#736)."""
    email = f"e2e-{uuid.uuid4().hex[:8]}@premise-labs.dev"
    console_errors = _page_js_errors(page)

    def handle(route):
        url = route.request.url
        if "auth/v1/signup" in url and route.request.method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          headers={"Access-Control-Allow-Origin": "*"},
                          body=json.dumps({"user": {"id": "mock-user",
                                                    "email": email,
                                                    "identities": [{"id": "mock-id"}]}}))
            return
        route.continue_()

    page.route("**/*", handle)
    page.goto(BASE_URL + "/signup", wait_until="domcontentloaded", timeout=30_000)
    page.locator("#email").fill(email)
    page.locator("#password").fill("E2ePass-12345!")
    page.locator("#btn-submit").click()

    expect(page.locator("#confirmation-required")).to_be_visible(timeout=10_000)
    expect(page.locator("#confirm-email")).to_have_text(email)
    assert "email=" not in page.url and "password=" not in page.url
    data_layer = page.evaluate("() => window.dataLayer || []")
    assert any(e.get("event") == "x_signup" and e.get("conversion_id")
               and e.get("email") == email for e in data_layer), \
        "x_signup entry missing from dataLayer"
    assert console_errors == [], f"page JS errors: {console_errors}"
