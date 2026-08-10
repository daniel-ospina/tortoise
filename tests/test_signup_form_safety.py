"""Static regression tests for the signup/signin form-safety hardening (#527).

Pins the contracts that keep the signup/login funnel from regressing into the
original "static shell" bug (#527): native form GET-echo of credentials, the
supabase-js duplicate-identifier script kill, raw Supabase error leakage, and
missing CDN-failure guards.

Unconditional (no network, no browser): runs in the main suite. The node
--check gate is skipped when node is unavailable (e.g. minimal runners).

Harness contract: plain string/DOM pins on the checked-in HTML files — no
Playwright, no live URLs, no env vars.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

WEBSITE = Path(__file__).resolve().parent.parent / "website"

SIGNUP = (WEBSITE / "signup.html").read_text()
SIGNIN = (WEBSITE / "signin.html").read_text()
WELCOME = (WEBSITE / "welcome.html").read_text()


# ── Form safety: method=post + explicit action kills the GET echo ──────────


def test_email_form_is_post_with_explicit_action() -> None:
    """The email forms must be method=post with an explicit same-path action.
    With no method/action the HTML default is GET-to-current-URL: if the JS
    handler ever fails to run (CDN blocked, CSP, regression), credentials are
    echoed into the URL (?email=...&password=...) — the original #527 shell
    behavior. method=post + action means a JS-failure submission can never
    put credentials in the URL (Cloudflare Pages discards POST bodies)."""
    signup_form = re.search(r'<form[^>]*id="email-form"[^>]*>', SIGNUP).group(0)
    signin_form = re.search(r'<form[^>]*id="email-form"[^>]*>', SIGNIN).group(0)
    assert re.search(r'method="post"', signup_form), "signup form must be method=post"
    assert re.search(r'action="/signup"', signup_form), "signup form must action=/signup"
    assert re.search(r'method="post"', signin_form), "signin form must be method=post"
    assert re.search(r'action="/signin"', signin_form), "signin form must action=/signin"


def test_email_and_password_have_autocomplete() -> None:
    """Autofill attributes must be retained (autocomplete=email /
    new-password / current-password) — the plan explicitly retains them."""
    assert 'autocomplete="email"' in SIGNUP
    assert 'autocomplete="new-password"' in SIGNUP
    assert 'autocomplete="email"' in SIGNIN
    assert 'autocomplete="current-password"' in SIGNIN


# ── The historical script-kill: no `let supabase` shadowing ────────────────


def test_no_supabase_identifier_shadowing() -> None:
    """#527 original root cause: supabase-js v2 UMD declares a global `var
    supabase`; the pages' inline scripts used `let supabase` which is a
    redeclaration → SyntaxError → the whole inline script died at parse time,
    leaving a static shell. Both pages must keep using a different identifier
    (supabaseClient) forever."""
    for name, html in (("signup", SIGNUP), ("signin", SIGNIN), ("welcome", WELCOME)):
        assert not re.search(r'\blet\s+supabase\b', html), \
            f"{name}.html must not declare `let supabase` (kills the inline script)"


# ── Error humanization: raw Supabase messages must never surface ───────────


def test_humanize_auth_error_present_with_rate_limit_mapping() -> None:
    """humanizeAuthError() must exist on both auth pages, reference at least
    3 error codes, and specifically map the production-verified 429
    over_email_send_rate_limit (the confirmed real-user signup failure) to
    friendly copy instead of the raw "Email rate limit exceeded"."""
    for name, html in (("signup", SIGNUP), ("signin", SIGNIN)):
        assert "humanizeAuthError" in html, f"{name}.html missing humanizeAuthError()"
        # Literal error codes (stable — the function keys on error_code first)
        for code in ("over_email_send_rate_limit", "invalid_credentials",
                     "email_not_confirmed", "weak_password"):
            assert code in html, f"{name}.html missing literal error code {code!r}"
        # Friendly copy for the rate-limit case
        assert "Too many attempts from this network" in html, \
            f"{name}.html missing friendly rate-limit copy"


def test_no_raw_error_message_leakage() -> None:
    """The old `showError(error.message)` verbatim-surfacing must be gone from
    both handlers (replaced by humanizeAuthError)."""
    assert "showError(error.message)" not in SIGNUP, \
        "signup.html still surfaces raw error.message"
    assert "showError(error.message)" not in SIGNIN, \
        "signin.html still surfaces raw error.message"


def test_rate_limit_lockout_guards_present() -> None:
    """#801: after a 429 (project-wide email bucket) the client must lock
    out email signup for ~1h — sessionStorage timestamp, disabled submit,
    countdown label, early-return guard. Literal pins (no regex)."""
    assert "tortoise_signup_rate_limited_until" in SIGNUP
    assert "RATE_LIMIT_LOCKOUT_MS" in SIGNUP
    assert "SHORT_RATE_LIMIT_LOCKOUT_MS" in SIGNUP  # two-tier: per-IP limits ≠ email bucket
    assert "applyRateLimitLockout" in SIGNUP
    assert "sessionStorage" in SIGNUP
    # the guard runs before any request: top-of-handler early return
    assert "rateLimitRemainingMs() > 0" in SIGNUP


# ── CDN / script-failure guards ────────────────────────────────────────────


def test_cdn_failure_guards_present() -> None:
    """Three-mechanism guard against the historical trigger (CDN blocked →
    inline script dies → dead form): (1) typeof check + try/catch around
    createClient, (2) CDN <script> onerror belt, (3) watchdog block that
    surfaces "temporarily unavailable" if the client never arrives."""
    for name, html in (("signup", SIGNUP), ("signin", SIGNIN)):
        assert 'typeof window.supabase !== "undefined"' in html, \
            f"{name}.html missing the createClient typeof guard"
        assert 'onerror="(function(){var e=document.getElementById(\'error\')' in html, \
            f"{name}.html missing the CDN script onerror belt"
        assert "temporarily unavailable" in html, \
            f"{name}.html missing the temporarily-unavailable copy"
        # The watchdog reads window.supabaseClient — the client must be exposed
        # on window (a top-level `let` does not create a window property), or
        # the watchdog false-fires on healthy loads (review P1).
        assert "window.supabaseClient = supabaseClient" in html, \
            f"{name}.html missing window.supabaseClient exposure"
        assert "setTimeout(showWatchdogError, 6000)" in html, \
            f"{name}.html missing the watchdog script block"


def test_noscript_notice_present() -> None:
    """A <noscript> notice must explain the no-JS case (no dead form)."""
    assert "<noscript>" in SIGNUP and "enable JavaScript" in SIGNUP
    assert "<noscript>" in SIGNIN and "enable JavaScript" in SIGNIN


def test_confirmation_state_hides_provider_buttons() -> None:
    """The check-your-inbox state must hide the OAuth provider buttons — the
    selector is .btn-provider (not the stale .oauth-btn from the plan)."""
    assert ".btn-provider" in SIGNUP
    assert ".oauth-btn" not in SIGNUP


# ── Docs promise (the funnel contract) ──────────────────────────────────────


def test_docs_promise_intact() -> None:
    """The docs must still promise the exact journey this issue protects:
    sign up → welcome page → API key shown once."""
    docs = (WEBSITE / "docs.html").read_text()
    assert "Sign up at" in docs and "/signup" in docs
    assert "API key on the welcome page" in docs
    assert "shown once" in docs


# ── JS syntax gate (node --check on inline scripts) ────────────────────────
# The 2026-08-08 root cause was a parse-time SyntaxError that killed the
# inline script. node --check on every inline <script> block catches any
# future syntax regression at test time.

_HAS_NODE = shutil.which("node") is not None

pytestmark = [
    pytest.mark.skipif(not _HAS_NODE, reason="node not available — syntax gate skipped"),
]


@pytest.mark.parametrize("fname", ["signup.html", "signin.html", "welcome.html"])
def test_inline_scripts_pass_node_syntax_check(fname: str) -> None:
    html = (WEBSITE / fname).read_text()
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert scripts, f"{fname}: expected at least one inline script block"
    for i, body in enumerate(scripts):
        if not body.strip():
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         dir=tempfile.gettempdir()) as f:
            f.write(body)
            path = f.name
        try:
            r = subprocess.run(["node", "--check", path],
                               capture_output=True, text=True, timeout=30)
            assert r.returncode == 0, \
                f"{fname} script {i} failed node --check:\n{r.stderr}"
        finally:
            os.unlink(path)


# ── Welcome page: defensive session wait (the "No active session" bounce) ──


def test_welcome_waits_for_session_before_erroring() -> None:
    """welcome.html must give the email-confirmation / OAuth callback a
    bounded wait for the session (SIGNED_IN / getSession) before declaring
    "No active session" — prevents bouncing legitimate callbacks to a dead
    state on older/cached supabase-js builds."""
    assert "waitForSession" in WELCOME
    assert "SIGNED_IN" in WELCOME
    assert "No active session" in WELCOME  # message retained (E2E contract)
