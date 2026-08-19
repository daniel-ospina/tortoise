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

# #863 mechanism-accurate copy: the email-send limit is PROJECT-WIDE, not
# per-network. These are the exact acceptance-(b) literals the pages must
# carry (email bucket vs per-IP request throttling).
EMAIL_BUCKET_COPY = "Signup emails are temporarily exhausted (too many signups right now). Try again in about an hour."
NETWORK_COPY = "Too many attempts from this network. Please wait about an hour and try again."


def test_humanize_auth_error_present_with_rate_limit_mapping() -> None:
    """humanizeAuthError() must exist on both auth pages, reference at least
    3 error codes, and map the production-verified 429 codes to friendly
    copy instead of the raw "Email rate limit exceeded". #863: BOTH the
    email-bucket copy and the per-IP network copy must be present, and the
    mechanism codes must be pinned separately (over_email_send_rate_limit
    vs over_request_rate_limit_ip)."""
    for name, html in (("signup", SIGNUP), ("signin", SIGNIN)):
        assert "humanizeAuthError" in html, f"{name}.html missing humanizeAuthError()"
        # Literal error codes (stable — the function keys on error_code first)
        for code in ("over_email_send_rate_limit", "over_request_rate_limit_ip",
                     "invalid_credentials", "email_not_confirmed", "weak_password"):
            assert code in html, f"{name}.html missing literal error code {code!r}"
        # #863: friendly copy for BOTH mechanisms — email bucket (project-wide
        # exhaustion) and per-IP request throttling (network attribution).
        assert EMAIL_BUCKET_COPY in html, \
            f"{name}.html missing the email-bucket exhaustion copy"
        assert NETWORK_COPY in html, \
            f"{name}.html missing the per-IP network-attribution copy"


def test_email_bucket_copy_mechanism_split() -> None:
    """#863: the two 429 copies must be distinct literals on both pages — the
    email-bucket copy must not contain the network attribution and vice
    versa, and the email code must not share a mapping entry with the per-IP
    codes (the pseudo-code email_rate_limit entry comes first)."""
    assert EMAIL_BUCKET_COPY != NETWORK_COPY
    assert "from this network" not in EMAIL_BUCKET_COPY, \
        "email-bucket copy still blames the network (#863 misattribution)"
    for name, html in (("signup", SIGNUP), ("signin", SIGNIN)):
        # The email-bucket entry must be a separate array entry from the
        # per-IP entry: pin the pseudo-code + code separation.
        assert '"email_rate_limit"' in html, \
            f"{name}.html missing the email_rate_limit pseudo-code (substring-fallback trap)"
        assert "over_email_send_rate_limit" in html
        assert "over_request_rate_limit_ip" in html


def test_no_raw_error_message_leakage() -> None:
    """The old `showError(error.message)` verbatim-surfacing must be gone from
    both handlers (replaced by humanizeAuthError)."""
    assert "showError(error.message)" not in SIGNUP, \
        "signup.html still surfaces raw error.message"
    assert "showError(error.message)" not in SIGNIN, \
        "signin.html still surfaces raw error.message"


def test_rate_limit_lockout_guards_present() -> None:
    """#801/#863: after a 429 (project-wide email bucket) the client must lock
    out email signup (signup.html, #801) and the recovery surface (signin.html,
    #863) for ~1h — sessionStorage timestamp, disabled submit, countdown
    label, early-return guard. Literal pins (no regex)."""
    assert "tortoise_signup_rate_limited_until" in SIGNUP
    assert "RATE_LIMIT_LOCKOUT_MS" in SIGNUP
    assert "SHORT_RATE_LIMIT_LOCKOUT_MS" in SIGNUP  # two-tier: per-IP limits ≠ email bucket
    assert "applyRateLimitLockout" in SIGNUP
    assert "sessionStorage" in SIGNUP
    # the guard runs before any request: top-of-handler early return
    assert "rateLimitRemainingMs() > 0" in SIGNUP
    # #863: the LOGIN surface (email modal + forgot-password) carries its own
    # page-scoped bucket on the auth page (tortoise_signin_* — migrated from
    # the retired signin.html) so a login throttle never disables the signup
    # form; signin.html keeps the same machinery as a legacy static pin.
    assert "tortoise_signin_rate_limited_until" in SIGNUP
    assert "tortoise_signin_rate_limit_tier" in SIGNUP
    assert "LOGIN_RATE_LIMIT_KEY" in SIGNUP
    assert "resetPasswordForEmail" in SIGNUP
    assert "tortoise_signin_rate_limited_until" in SIGNIN
    assert "tortoise_signin_rate_limit_tier" in SIGNIN
    assert "RATE_LIMIT_LOCKOUT_MS" in SIGNIN
    assert "SHORT_RATE_LIMIT_LOCKOUT_MS" in SIGNIN
    assert "applyRateLimitLockout" in SIGNIN
    assert "rateLimitRemainingMs() > 0" in SIGNIN
    assert "resetPasswordForEmail" in SIGNIN


def test_recovery_flow_present() -> None:
    """#863: the recovery request-link flow — now on the single auth page's
    login modal (email + forgot-password, POST /auth/v1/recover surface via
    resetPasswordForEmail) — plus the legacy signin.html pins and the
    reset-password landing on welcome.html (recovery-link redirect target).
    #527 form-safety contract + #863 double-submit guards + expired-link
    copy must hold."""
    # The LIVE surface: auth page login modal carries the forgot-password
    # entry + login-scoped bucket (#863 separation, #1493).
    assert 'id="modal-forgot-link"' in SIGNUP
    assert "modalForgotPassword" in SIGNUP
    assert "resetPasswordForEmail" in SIGNUP
    assert "LOGIN_RATE_LIMIT_KEY" in SIGNUP  # login bucket ≠ signup bucket
    # Legacy signin.html pins (file retained for static contract).
    assert 'id="forgot-link"' in SIGNIN
    assert 'id="recovery-form"' in SIGNIN
    assert 'id="btn-recovery"' in SIGNIN
    assert "recoveryInFlight" in SIGNIN  # double-submit guard (bucket burn)
    # #527 contract: recovery form must be method=post with explicit action
    recovery_form = re.search(r'<form[^>]*id="recovery-form"[^>]*>', SIGNIN).group(0)
    assert re.search(r'method="post"', recovery_form), "recovery form must be method=post"
    assert re.search(r'action="/signin"', recovery_form), "recovery form must action=/signin"
    # welcome.html: recovery-landing reset panel
    assert 'id="reset-form"' in WELCOME
    assert 'id="reset-error"' in WELCOME
    assert 'id="btn-reset"' in WELCOME
    assert "PASSWORD_RECOVERY" in WELCOME
    assert "updateUser" in WELCOME
    assert "resetInFlight" in WELCOME
    # recovery mode must short-circuit provisioning (no team mint mid-reset)
    assert "waitForProvisioning" in WELCOME
    assert "recoveryMode" in WELCOME
    assert "This reset link has expired or is invalid. Request a new one." in WELCOME


def test_server_429_tier_parsing_pins() -> None:
    """#863: the server-first signup 429 path (signup.html) must resolve the
    mechanism from the API's detail payload — the one layer that is neither
    e2e-testable nor unit-tested (no JS test harness): error_code read,
    string-detail hint scan, and the fail-safe email-bucket default."""
    assert "lastServer429Code" in SIGNUP
    assert "lastServer429Message" in SIGNUP
    assert "detail.error_code" in SIGNUP
    assert "resolveServer429Code" in SIGNUP
    assert 'return "over_email_send_rate_limit"' in SIGNUP  # fail-safe default


# ── CDN / script-failure guards ────────────────────────────────────────────


def test_cdn_failure_guards_present() -> None:
    """Three-mechanism guard against the historical trigger (CDN blocked →
    inline script dies → dead form): (1) typeof check + try/catch around
    createClient, (2) CDN <script> onerror belt, (3) watchdog block that
    surfaces "temporarily unavailable" if the client never arrives."""
    for name, html in (("signup", SIGNUP), ("signin", SIGNIN)):
        # #1225: the CDN guard moved to the null-safe shared factory
        # (assets/supabase-session.js) — same fail-closed intent, new surface.
        assert 'typeof window.createTortoiseSupabaseClient === "function"' in html, \
            f"{name}.html missing the null-safe client factory guard"
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
    assert "Sign up at" in docs and "/auth" in docs
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
    # #863 review: match attribute-bearing <script> tags too (a <script defer> or
    # <script type="module"> block would otherwise be silently skipped — the
    # exact regression class this gate exists for). External <script src=...>
    # blocks (CDN supabase-js) have empty bodies and are skipped below.
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S)
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
    bounded wait for the session (SIGNED_IN / getSession) before redirecting
    an unauthenticated visitor to the single auth page (/auth) — prevents
    bouncing legitimate callbacks to a dead state on older/cached
    supabase-js builds."""
    assert "waitForSession" in WELCOME
    assert "SIGNED_IN" in WELCOME
    assert 'window.location.href = "/auth"' in WELCOME  # no-session → /auth
