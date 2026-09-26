"""Static regression tests for the signup form-safety hardening (#527).

Pins the contracts that keep the signup/login funnel from regressing into the
original "static shell" bug (#527): native form GET-echo of credentials, the
supabase-js duplicate-identifier script kill, raw Supabase error leakage, and
missing CDN-failure guards.

#4054: the LIVE auth surface is the single `/auth` page
(`website/apps/dashboard/public/signup.html`, which carries BOTH signup and login
modes). The legacy marketing-origin `website/signin.html` was DELETED — every
`/signin*` URL 301s to `/auth` (`website/_redirects`, plus the company-host
consolidation in `website/functions/_middleware.ts`), so the asset was
unreachable and only kept the last legacy-bridge page alive. Its #527/#863
static pins are retired below; each invariant they covered is now asserted
against the live `/auth` page, is enforced repo-wide by
tests/test_no_legacy_token_path.py, or no longer applies because the live
recovery surface is a JS link rather than a form.

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
# #4054: the BFF surfaces moved to the APP Pages project
# (`website/apps/dashboard/public/`). The legacy marketing-origin signin.html
# was DELETED with the bridge; SIGNUP is now the single /auth surface.
DASHBOARD_PUBLIC = WEBSITE / "apps" / "dashboard" / "public"

SIGNUP = (DASHBOARD_PUBLIC / "signup.html").read_text()
WELCOME = (DASHBOARD_PUBLIC / "welcome.html").read_text()


def test_retired_signin_page_stays_deleted() -> None:
    """#4054: `website/signin.html` was the last page on the legacy
    cross-subdomain session bridge and is DELETED. Every `/signin*` URL 301s to
    `/auth` (`website/_redirects`), so the asset was unreachable; a
    reintroduction would re-arm the JS-readable bridge.

    REPLACES the retired signin.html static pins: the #527 form contract, the
    #863 recovery lockout and the three-mechanism CDN guard are now asserted
    against the LIVE /auth page (SIGNUP), are covered repo-wide by
    tests/test_no_legacy_token_path.py, or are no longer invariants (the live
    recovery request is a JS link, not a form). The pin here is the ABSENCE of
    the retired page, so it cannot silently come back.
    """
    assert not (WEBSITE / "signin.html").exists(), (
        "website/signin.html is back — it 301s to /auth on every host and its "
        "only purpose was to keep the legacy session bridge alive (#4054)"
    )


def _strip_html_comments(text: str) -> str:
    """Remove HTML and JS comments from a page source.

    Absence assertions MUST run against comment-stripped source. welcome.html
    explains the #3501 removal in a comment that names
    `createTortoiseSupabaseClient`, so an unstripped check fails on the
    documentation of the fix rather than on a reintroduction of the bug.

    TRAILING `//` comments are stripped too, not just line-start ones: a future
    `x = 1; // … location` would otherwise fail the navigation guard on a
    comment. The `//` must be preceded by whitespace, `;`, `)` or line start, so
    a URL's `//` (preceded by `:`) is left intact.
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)(^|[;\s])//[^\n]*", r"\1", text)


WELCOME_CODE = _strip_html_comments(WELCOME)


# ── Form safety: method=post + explicit action kills the GET echo ──────────


def test_email_form_is_post_with_explicit_action() -> None:
    """The email forms must be method=post with an explicit same-path action.
    With no method/action the HTML default is GET-to-current-URL: if the JS
    handler ever fails to run (CDN blocked, CSP, regression), credentials are
    echoed into the URL (?email=...&password=...) — the original #527 shell
    behavior. method=post + action means a JS-failure submission can never
    put credentials in the URL (Cloudflare Pages discards POST bodies)."""
    signup_form = re.search(r'<form[^>]*id="email-form"[^>]*>', SIGNUP).group(0)
    assert re.search(r'method="post"', signup_form), "signup form must be method=post"
    # #1494: the ONE email form now lives in the email modal (signup AND
    # login per the toggle); its action is the same-path canonical /auth.
    assert re.search(r'action="/auth"', signup_form), "signup form must action=/auth"
    # #4054: the deleted signin.html's duplicate `action="/signin"` email form is
    # retired with the page — the /auth page is the single email form, and this
    # assertion is what keeps the #527 GET-echo contract on the live surface.
    # The API-key modal form keeps the same #527 contract (no GET echo).
    apikey_form = re.search(r'<form[^>]*id="apikey-form"[^>]*>', SIGNUP).group(0)
    assert re.search(r'method="post"', apikey_form), "apikey form must be method=post"
    assert re.search(r'action="/auth"', apikey_form), "apikey form must action=/auth"


def test_email_and_password_have_autocomplete() -> None:
    """Autofill attributes must be retained (autocomplete=email /
    new-password / current-password) — the plan explicitly retains them."""
    assert 'autocomplete="email"' in SIGNUP
    assert 'autocomplete="new-password"' in SIGNUP
    # #4054: the deleted signin.html's static `autocomplete="current-password"`
    # pin is replaced by the LIVE /auth page's runtime assignment — the email
    # modal toggles between signup and login, so the attribute is set in JS.
    assert '"current-password"' in SIGNUP, (
        "the /auth login modal must still request current-password autofill"
    )


# ── #3781: the private-beta gate must never come back ──────────────────────


def test_no_client_side_beta_gate() -> None:
    """#3781: the signup funnel must stay OPEN for a real new user.

    The retired gate was a full-viewport overlay that covered all four
    sign-in options until the visitor typed a hardcoded client-side
    constant (`BETA_ACCESS_CODE = "betatester"`) or carried a
    `localStorage['tortoise_beta_access']` flag — it blocked every real
    signup and restricted nobody who read the page source (the endpoint it
    appeared to protect, POST /v1/signup/email, is in SKIP_AUTH).

    Checked against comment-stripped source so documenting the removal in a
    comment can never miss a re-introduction of the overlay itself.
    """
    stripped = _strip_html_comments(SIGNUP)
    # Quote- and attribute-agnostic on purpose (review P2): pinning `id="beta-gate"`
    # missed `id='beta-gate'`, a `class="beta-gate"` overlay, and the `.beta-gate`
    # CSS rule — a re-introduction in any of those spellings passed the guard while
    # restoring the exact defect. The bare identifier covers every spelling; the
    # constant/flag tokens are already spelling-independent.
    for token, why in (
        ("beta-gate", "the full-viewport overlay (or its CSS) is back"),
        ("BETA_ACCESS_CODE", "the hardcoded client-side access code is back"),
        ("BETA_ACCESS_KEY", "the client-side access-key constant is back"),
        ("confirmBetaAccess", "the client-side unlock handler is back"),
        ("tortoise_beta_access", "the localStorage unlock flag is back"),
    ):
        assert token not in stripped, f"#3781 regression: {why} ({token})"
    # The front door itself must remain reachable (the gate's inverse).
    assert 'id="btn-email"' in stripped, "the email signup CTA is missing"


# ── The historical script-kill: no `let supabase` shadowing ────────────────


def test_no_supabase_identifier_shadowing() -> None:
    """#527 original root cause: supabase-js v2 UMD declares a global `var
    supabase`; the pages' inline scripts used `let supabase` which is a
    redeclaration → SyntaxError → the whole inline script died at parse time,
    leaving a static shell. Both pages must keep using a different identifier
    (supabaseClient) forever."""
    # #4054: signin.html is deleted (see the module docstring); the shadowing
    # contract is asserted on the live surfaces that remain.
    for name, html in (("signup", SIGNUP), ("welcome", WELCOME)):
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
    for name, html in (("signup", SIGNUP),):
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
    for name, html in (("signup", SIGNUP),):
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
    # #4054: the deleted signin.html's copy of this check is retired; the
    # repo-wide absence of a raw-message leak is enforced by
    # tests/test_no_legacy_token_path.py and the BFF status-branch test below.


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
    # page-scoped bucket on the auth page (tortoise_signin_*) so a login throttle
    # never disables the signup form.
    assert "tortoise_signin_rate_limited_until" in SIGNUP
    assert "tortoise_signin_rate_limit_tier" in SIGNUP
    assert "LOGIN_RATE_LIMIT_KEY" in SIGNUP
    # #3501: the reset call is now the BFF route (the client no longer calls
    # GoTrue directly).
    assert 'bffPost("/auth/reset"' in SIGNUP
    # #4054: the duplicate signin.html pins of the SAME login-bucket machinery
    # (tortoise_signin_*, RATE_LIMIT_LOCKOUT_MS, SHORT_RATE_LIMIT_LOCKOUT_MS,
    # applyRateLimitLockout, rateLimitRemainingMs, resetPasswordForEmail) are
    # retired with the page. Every invariant they named is asserted on SIGNUP
    # above, which is now the single login surface.


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
    # #3501: the reset request is the BFF route, not a client GoTrue call.
    assert 'bffPost("/auth/reset"' in SIGNUP
    assert "LOGIN_RATE_LIMIT_KEY" in SIGNUP  # login bucket ≠ signup bucket
    # #4054: the legacy signin.html recovery pins (id="forgot-link",
    # id="recovery-form", id="btn-recovery", recoveryInFlight, and the #527
    # method=post/action="/signin" contract) are retired with the page. The LIVE
    # recovery REQUEST is the /auth login modal's `modal-forgot-link` link
    # (asserted above), which is a JS link, not a form — so the recovery-form
    # #527 contract no longer applies. The #527 contract for the reset LANDING
    # form is still asserted on welcome.html below.
    # welcome.html: recovery-landing reset panel.
    #
    # #3501 replaced the client-side reset (a supabase-js `updateUser` call on a
    # JavaScript-readable session) with a same-origin POST to the BFF. The
    # assertions below pin the NEW contract; the ABSENCE assertions after them
    # are the load-bearing half — a regression that reintroduces the bridge
    # would pass every positive pin here while restoring the #3485 login loop.
    assert 'id="reset-form"' in WELCOME
    assert 'id="reset-error"' in WELCOME
    assert 'id="btn-reset"' in WELCOME
    assert 'action="/auth/update-password"' in WELCOME
    assert 'fetch("/auth/update-password"' in WELCOME_CODE
    assert 'credentials: "same-origin"' in WELCOME_CODE
    # #527 form-safety contract still holds on the new form: method=post with
    # an explicit same-origin action (the native form must not GET-echo the
    # password in a query string if JS fails).
    reset_form = re.search(r'<form[^>]*id="reset-form"[^>]*>', WELCOME).group(0)
    assert re.search(r'method="post"', reset_form), "reset form must be method=post"
    assert re.search(r'action="/auth/update-password"', reset_form), (
        "reset form must action=/auth/update-password"
    )
    # Double-submit guard (bucket burn): the in-flight latch must survive.
    assert "var inFlight = false" in WELCOME_CODE
    assert "if (inFlight) return" in WELCOME_CODE
    # 401 vs 503 must stay DISTINCT. 401 = the recovery link is dead; the
    # catch-all must say "try again", never "you are signed out" — conflating
    # store/fault with signed-out is the #3485 class.
    assert "r.status === 401" in WELCOME_CODE
    assert "This reset link has expired or is invalid" in WELCOME_CODE
    assert "sign in with your new password" in WELCOME_CODE
    # The legacy client bridge is GONE. It resolved the legacy parent-domain
    # cookie, so a browser holding no legacy session had no session it could see
    # and was bounced back to /auth.
    for legacy in (
        "runSessionBridge",
        "createTortoiseSupabaseClient",
        "PASSWORD_RECOVERY",
        "updateUser",
        "supabase",
    ):
        assert legacy not in WELCOME_CODE, (
            f"welcome.html still contains {legacy!r} — the client session "
            "bridge was removed in #3501 and must not come back"
        )
    assert "This reset link has expired or is invalid. Request a new one." in WELCOME


def test_bff_status_branches_pin() -> None:
    """#3501: the page branches on the BFF HTTP STATUS and maps the JSON payload
    onto the {code, message} shape the lockout helpers read.

    Replaces the retired #863 `resolveServer429Code` pin: signup/password no
    longer receive the provider's mechanism code directly (the BFF folds a
    provider 429 into 503 for those routes), so the load-bearing property is now
    the STATUS discipline — a 503 must be handled as a retryable fault and never
    fall through to a "signed out"/"refused" branch (#3485).
    """
    assert "function bffPost(" in SIGNUP
    assert "function bffError(" in SIGNUP
    # The provider code rides `providerError` on the signup rejection; every
    # other route uses `error`.
    assert "data.providerError || data.error" in SIGNUP
    # Explicit 503 branches, with honest retryable copy.
    assert "Signup is temporarily unavailable" in SIGNUP
    assert "Sign-in is temporarily unavailable" in SIGNUP
    assert "We couldn't send the reset email right now" in SIGNUP
    # The API-key route's 429 keeps its hour-scale copy.
    assert "Too many sign-in attempts from this network" in SIGNUP
    assert "Retry-After" in SIGNUP


# ── CDN / script-failure guards ────────────────────────────────────────────


def test_migrated_auth_page_has_no_client_cdn_machinery() -> None:
    """The /auth page is on the BFF: there is NO CDN script and NO client, so
    the CDN-death guard is deliberately ABSENT — a reintroduction of
    `createTortoiseSupabaseClient` IS the bug. That absence is enforced by this
    test, the static BFF suite, and tests/test_no_legacy_token_path.py.

    RENAMED from `test_cdn_failure_guards_present`: the POSITIVE half of that
    test pinned the three-mechanism CDN-death guard on the legacy signin.html,
    which is DELETED (#4054). The guard's subject is gone, so only the absence
    half remains — and it is the load-bearing half for the migrated surface.
    """
    # Migrated signup surface: none of the client machinery may be present.
    # Comment-stripped: the page documents the removal in comments that name it.
    signup_code = _strip_html_comments(SIGNUP)
    for legacy in (
        "createTortoiseSupabaseClient",
        "window.supabaseClient",
        "supabase-session.js",
        "@supabase/supabase-js",
        "SUPABASE_ANON_KEY",
    ):
        assert legacy not in signup_code, (
            f"signup.html is on the BFF and must not carry the client CDN guard "
            f"({legacy!r}) — the client-side session is retired (#3501)"
        )


def test_noscript_notice_present() -> None:
    """A <noscript> notice must explain the no-JS case (no dead form).

    #4054: the deleted signin.html's copy of this pin is retired; the live /auth
    page (SIGNUP) is the only form surface left, and this asserts it directly.
    """
    assert "<noscript>" in SIGNUP and "enable JavaScript" in SIGNUP


def test_confirmation_state_hides_provider_buttons() -> None:
    """The check-your-inbox state must hide the OAuth provider buttons — the
    selector is .btn-provider (not the stale .oauth-btn from the plan)."""
    assert ".btn-provider" in SIGNUP
    assert ".oauth-btn" not in SIGNUP


# ── Docs promise (the funnel contract) ──────────────────────────────────────


def test_docs_promise_intact() -> None:
    """The docs must still promise the exact journey this issue protects:
    sign up → in-app welcome card → API key shown once (#1566 moved the key
    reveal in-app; welcome.html is a pure bridge since #1730)."""
    docs = (WEBSITE / "docs.html").read_text()
    assert "Sign up at" in docs and "/auth" in docs
    assert "API key in the in-app welcome card" in docs
    assert "shown once" in docs


# ── JS syntax gate (node --check on inline scripts) ────────────────────────
# The 2026-08-08 root cause was a parse-time SyntaxError that killed the
# inline script. node --check on every inline <script> block catches any
# future syntax regression at test time.

_HAS_NODE = shutil.which("node") is not None

pytestmark = [
    pytest.mark.skipif(not _HAS_NODE, reason="node not available — syntax gate skipped"),
]


@pytest.mark.parametrize(
    "page",
    [
        DASHBOARD_PUBLIC / "signup.html",
        DASHBOARD_PUBLIC / "welcome.html",
    ],
    ids=["signup.html", "welcome.html"],
)
def test_inline_scripts_pass_node_syntax_check(page: Path) -> None:
    html = page.read_text()
    fname = page.name
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


def test_welcome_does_not_wait_for_a_client_session() -> None:
    """#3501: welcome.html must NOT wait for, or read, a client-side session.

    This replaces the pre-#3501 assertion that the page ran a bounded
    `waitForSession`/`SIGNED_IN` wait. That wait resolved the legacy parent-domain
    `sb-tortoise-auth-token` cookie, which a BFF login never writes (the BFF session
    is the HttpOnly `__Host-session`), so a browser holding no legacy session could
    only ever time out — and its no-session branch bounced that visitor back to
    /auth. That was the #3485 login loop, reproduced by construction for that visitor.

    The decision now happens SERVER-side in `functions/welcome.ts`, which reads
    the HttpOnly cookie and redirects before any HTML is served (pinned by
    `tests/e2e/auth/test_welcome_and_password.py`). This test pins the ABSENCE of
    the client-side implementation, so a regression fails here instead of in
    production.
    """
    for legacy in ("waitForSession", "SIGNED_IN", "getSession"):
        assert legacy not in WELCOME_CODE, (
            f"welcome.html still contains {legacy!r} — the client-side session "
            "wait was the #3485 login loop and must stay removed (#3501)"
        )
    # The page must not perform a client-side navigation; that is the server's
    # job now, and a JS bounce back to /auth IS the #3485 loop.
    #
    # See _NAVIGATION_BANS for the mechanism list and its honestly-stated
    # limits. The patterns are shared with the discrimination matrix below, so
    # there is ONE definition rather than two that can drift apart.
    for what, pattern in _NAVIGATION_BANS:
        assert not re.search(pattern, WELCOME_CODE.lower()), (
            f"welcome.html must not {what} (matched {pattern!r}) — the page "
            "must not navigate client-side (#3501). The auth decision is the "
            "server's, and a client-side bounce back to /auth is the #3485 "
            "login loop."
        )


# ── The client-navigation guard: its mechanisms and its limits ─────────────
#
# A guard that has never been shown to fail is not a guard, and a guard whose
# claim was never falsifiable is not evidence. Three generations were tried:
#
#   v1  substring `/auth'` / `"/auth"` / `location.replace`
#   v2  regex, re.I: `location\s*\.\s*(?:replace|assign|href)\s*[(=]`
#                   | `http-equiv\s*=\s*["']refresh["']`
#   v3  the mechanism bans below (current)
#
# v3 vs v2 — NOT a strict superset in either direction, and the matrix below
# does not prove that it is:
#   * v2 missed `location['href'] = ...`, `location = '/auth'` and
#     `setAttribute("http-equiv", ...)`; v3 catches all three.
#   * v3's `\blocation\b` deliberately NARROWS v2, which had no left word
#     boundary and so also matched any identifier merely ENDING in it:
#     `_location.href = '/auth'`, `prevLocation.replace('/auth')`,
#     `foo_location.assign('/auth')`. Those are v2-caught / v3-missed. Losing
#     them costs no real coverage — they are v2 false positives on unrelated
#     identifiers — but they are a genuine loss, so this is not "strictly
#     stronger". The `\b` is kept because it is what keeps `relocation` /
#     `allocation` prose out.
#   A 37-form matrix cannot assert a universal property; it asserts its own
#   rows. Read the claim as "every form in the matrix is caught", nothing more.
#
# v3 does NOT restore everything v1 caught, and that is a TRADE, not an
# improvement. v1 matched the TARGET literal `/auth`, so it also caught
# navigations that name no mechanism at all — `document.write(url='/auth')`,
# `a.setAttribute("href", "/auth")`. Those are MISSED here.
#
# The reason they were not restored: matching the target cannot distinguish a
# navigation from a legitimate reference to the same path. The demonstration is
# `action="/auth"` (website/signup.html:612,638), which v1's `"/auth"` literal
# DOES catch — i.e. the literal fires on a plain form action, not only on a
# bounce. (Note v1 happens to pass on welcome.html: its `/auth` references
# INCLUDE `action="/auth/update-password"`, `fetch("/auth/update-password"` and
# two `href="/auth?mode=login"` links, none of which contain the exact literals.
# That is luck of quoting, not the property v1 was pinning — which is itself the
# reason to pin mechanisms.)
#
# Known limits, stated rather than implied: indirection THROUGH a mechanism
# (`window.open.call(window, '/auth')`), computed member access
# (`window['loc'+'ation']`), unicode escapes (`loca\u0074ion`), and
# target-only navigations (above). A static gate cannot close these. The
# behavioural proof is tests/e2e/auth/test_welcome_and_password.py.
_NAVIGATION_BANS = (
    ("read or assign `location`", r"\blocation\b"),
    ("navigate via `history`", r"\bhistory\b"),
    ("embed a meta-refresh redirect", r"http-equiv"),
    ("open a window", r"\bopen\s*\("),
    ("submit a form programmatically", r"\.\s*submit\s*\("),
    ("click an element programmatically", r"\.\s*click\s*\("),
    # Bracket/quoted method access (`window['open']('/auth')`) evades the dotted
    # patterns and is a plausible reintroduction rather than exotic obfuscation.
    # Requiring the INVOCATION keeps `type="submit"`, `class="btn-submit"` and
    # `addEventListener("submit", ...)` out of it.
    ("invoke a navigation method by name",
     r"['\"]\s*(?:open|submit|click)\s*['\"]\s*\]?\s*\("),
)


def _guard_flags(source: str) -> str | None:
    """Return the ban a source trips, or None.

    Shared by the real-page assertion above and the matrix below, so the matrix
    exercises the SAME patterns the guard enforces.
    """
    code = _strip_html_comments(source).lower()
    for what, pattern in _NAVIGATION_BANS:
        if re.search(pattern, code):
            return what
    return None


# Every mechanism the guard claims to catch, as it would appear reintroduced.
# Each is injected into a copy of the REAL page, so the matrix exercises the
# actual guard over the actual file rather than a synthetic fixture.
_NAVIGATION_FORMS = (
    "location.replace('/auth')",
    "location.assign('/auth?next=1')",
    "location.href = '/auth'",
    "location = '/auth'",
    "window.location.href = '/auth'",
    "document.location = '/auth'",
    "self.location = '/auth'",
    "top.location = '/auth'",
    "parent.location.href = '/auth'",
    "frames[0].location = '/auth'",
    "location['replace']('/auth')",
    "location['href'] = '/auth'",
    "window['location']['replace']('/auth')",
    "location[k]('/auth')",
    "location.assign?.('/auth')",
    "location.href ||= '/auth'",
    "LOCATION.HREF = '/auth'",
    "Location.Replace('/auth')",
    "location . replace ( '/auth' )",
    "location\n.href\n= '/auth'",
    "history.pushState({}, '', '/auth')",
    "history.replaceState({}, '', '/auth')",
    '<meta http-equiv="refresh" content="0;url=/auth">',
    "<meta http-equiv='refresh' content='0;url=/auth'>",
    '<meta http-equiv=refresh content="0;url=/auth">',
    '<meta http-equiv = "refresh" content="0;url=/auth">',
    'x.setAttribute("http-equiv","refresh")',
    'document.write(\'<meta http-equiv="refresh">\')',
    'x.innerHTML = `<meta http-equiv="refresh">`',
    "open('/auth')",
    "window.open('/auth','_self')",
    "window['open']('/auth')",
    "window . open('/auth')",
    "document.getElementById('f').submit()",
    "document.forms[0].submit()",
    "document.getElementById('a').click()",
    "el['click']()",
)

# Constructs the REAL page legitimately contains. None may trip the guard: a
# guard that fires on the correct implementation gets deleted by the next
# person, and then it protects nothing.
_LEGITIMATE_CONSTRUCTS = (
    '<form id="reset-form" method="post" action="/auth/update-password">',
    '<a href="/auth?mode=login">Sign in</a>',
    '<a href="/auth?mode=signup">Create account</a>',
    'fetch("/auth/update-password", { credentials: "same-origin" })',
    '<button type="submit" class="btn-submit" id="btn-reset">Update</button>',
    'form.addEventListener("submit", function (e) { e.preventDefault(); })',
    '<link rel="icon" type="image/png" href="/logo.png">',
    '<script src="/consent.js" defer></script>',
    'var u = "https://app.premiselabs.co/x";',
    '// a trailing comment mentioning location must not fire',
)


def test_the_navigation_guard_is_not_tripped_by_the_real_page() -> None:
    """Non-vacuity, both directions.

    The guard must PASS the real correct implementation, and the matrix must be
    large enough to be meaningful — a matrix that silently shrank to two cases
    would make the parametrized tests below pass while asserting almost
    nothing.
    """
    assert _guard_flags(WELCOME) is None, (
        "the guard fires on website/welcome.html as it actually is — a guard "
        "that breaks the correct implementation gets deleted, not respected"
    )
    assert len(_NAVIGATION_FORMS) >= 30, (
        f"discrimination matrix shrank to {len(_NAVIGATION_FORMS)} — the "
        "guard's claim is only as strong as this list"
    )
    assert len(_LEGITIMATE_CONSTRUCTS) >= 8, (
        f"legitimate-construct list shrank to {len(_LEGITIMATE_CONSTRUCTS)}"
    )


@pytest.mark.parametrize("anchor", ["</body>", "</head>"], ids=["body", "head"])
@pytest.mark.parametrize("payload", _NAVIGATION_FORMS)
def test_navigation_guard_catches_each_mechanism(payload: str, anchor: str) -> None:
    """Every navigating mechanism must be flagged when injected into the real
    page source.

    This is the matrix the guard's claim rests on, run in CI. An earlier
    version of this evidence lived in a scratch script under /tmp and was cited
    in a commit message; it could not be re-run by anyone, it re-implemented the
    guard instead of importing it, and it could not fail. This cannot drift
    from the guard: both read _NAVIGATION_BANS.
    """
    injected = WELCOME.replace(anchor, f"<script>{payload}</script>\n{anchor}", 1)
    assert injected != WELCOME, f"injection anchor {anchor!r} missing"
    assert _guard_flags(injected), (
        f"guard MISSED {payload!r} injected at {anchor} — a client-side bounce "
        "back to /auth is the #3485 login loop"
    )


@pytest.mark.parametrize("construct", _LEGITIMATE_CONSTRUCTS)
def test_navigation_guard_permits_legitimate_constructs(construct: str) -> None:
    """And it must not fire on the constructs the page actually needs."""
    injected = WELCOME.replace("</body>", construct + "\n</body>", 1)
    assert injected != WELCOME
    assert _guard_flags(injected) is None, (
        f"guard false-fired on legitimate {construct!r} — a guard that breaks "
        "correct code gets deleted, and then it protects nothing"
    )
