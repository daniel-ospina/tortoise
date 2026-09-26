"""
Static contracts for the migrated `/auth` page (website/apps/dashboard/public/signup.html).

WHY A STATIC SUITE
------------------
The page is 1,700+ lines of inline JS with no module boundary and no unit-test
harness. The migration off supabase-js (#3501/#4054) is a set of ABSENCE
properties — "no client holds a token", "no GoTrue call is made from the
browser" — and absence-by-pointer is exactly what a grep proves and a behaviour
test cannot: a stray reintroduced `supabaseClient` call would sit in a code path
no test happens to execute.

The e2e route suites (`test_*_bff.py`) prove the BFF routes behave; this file
proves the PAGE talks only to them. Both halves are needed: a correct route with
a page still on the legacy client is the half-migrated state the cutover exists
to prevent.

NON-LEGACY MARKERS ARE CHECKED COMMENT-STRIPPED. The page's own comments name
the removed machinery (that is how the removal is documented), so an
unstripped check would fire on the explanation rather than a reintroduction.
A parametrized matrix injects each marker into a copy of the real page and
asserts the guard catches it — a guard that cannot fail is not a guard.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PAGE = REPO / "website/apps/dashboard/public/signup.html"


def _strip_comments(text: str) -> str:
    """Remove HTML and JS comments before ABSENCE scans.

    `//` preceded by `:` is left intact so URLs (`https://…`) survive.
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", "", text)


def _code() -> str:
    return _strip_comments(PAGE.read_text(encoding="utf-8"))


# The machinery the BFF replaces. Each token is checked against comment-stripped
# source; the matrix test below proves the guard catches every one of them.
LEGACY_MARKERS = (
    "supabaseClient",
    "createTortoiseSupabaseClient",
    "supabase-session.js",
    "sb-tortoise-auth-token",
    "@supabase/supabase-js",
    "SUPABASE_ANON_KEY",
    "SUPABASE_URL",
    "SIGNUP_API_URL",
    "api.premiselabs.co",
    "storeSession",
    "signInWithOAuth",
    "signInWithPassword",
    "resetPasswordForEmail",
    "auth.signUp",
    "auth.resend",
    "auth.getSession",
    "gotrueRedirectTarget",
)


def _legacy_flags(source: str) -> list[str]:
    code = _strip_comments(source)
    return [m for m in LEGACY_MARKERS if m in code]


# ---------------------------------------------------------------------------
# The absence contract (A) — no client-side session machinery survives
# ---------------------------------------------------------------------------
def test_the_real_page_has_no_legacy_client_machinery():
    assert PAGE.exists(), f"{PAGE} missing"
    flags = _legacy_flags(PAGE.read_text(encoding="utf-8"))
    assert not flags, (
        "legacy client-side auth machinery is still present in the migrated "
        f"/auth page: {flags}"
    )


def test_the_absence_guard_catches_each_marker():
    """MUTATION PROOF for the guard above.

    A guard that has never been shown to fail protects nothing. Each marker is
    injected into a copy of the REAL page, at both the head and the body, and
    the guard must flag it.
    """
    source = PAGE.read_text(encoding="utf-8")
    assert len(LEGACY_MARKERS) >= 15, "the marker list shrank — the claim weakens with it"
    for marker, anchor in [(m, a) for m in LEGACY_MARKERS for a in ("</head>", "</body>")]:
        injected = source.replace(anchor, f"<script>var x = {marker!r};</script>\n{anchor}", 1)
        assert injected != source, f"anchor {anchor!r} missing from the page"
        assert marker in _legacy_flags(injected), (
            f"the absence guard MISSED a reintroduced {marker!r} at {anchor} — the "
            "page could silently return to the client-side session"
        )


# ---------------------------------------------------------------------------
# OAuth must go through /auth/start (server-side PKCE)
# ---------------------------------------------------------------------------
def test_oauth_goes_through_the_bff_start_route():
    code = _code()
    assert '"/auth/start?provider="' in code, (
        "OAuth must navigate to /auth/start so the BFF generates the PKCE "
        "code_verifier SERVER-SIDE; a client-generated verifier can never be "
        "seen by /auth/callback"
    )
    assert "oauthNextPath" in code, "the return-to must be passed to /auth/start as `next`"
    assert "signInWithOAuth" not in code, "the page still builds a GoTrue OAuth URL client-side"


def test_oauth_next_path_is_a_relative_path_not_a_go_true_redirect():
    """`/auth/start` persists `next` and `/auth/callback` re-validates it.

    The value must be a same-origin path (the server re-checks it with
    `safeNext`), and the admin return-to must win over claim routing.
    """
    code = _code()
    i = code.find("function oauthNextPath")
    assert i != -1, "oauthNextPath was removed"
    body = code[i : code.find("\n    }", i)]
    assert 'return window.__ADMIN_RETURN_TO;' in body, body
    assert 'return "/?claim=1";' in body, body
    assert 'return "/";' in body, body
    assert "window.location.origin" not in body, (
        "next must be a path, not an absolute URL — `/auth/start` validates it "
        "as same-origin and re-serialises it"
    )


# ---------------------------------------------------------------------------
# Every auth call site targets a BFF route
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "call",
    [
        'fetch("/api/session"',         # session truth (503-aware)
        'fetch("/auth/api-key"',        # API-key exchange (server-side)
        'bffPost("/auth/signup"',       # email signup
        'bffPost("/auth/password"',     # email + password sign-in
        'bffPost("/auth/reset"',        # password reset
        'bffPost("/auth/resend"',       # resend confirmation
        '"/auth/start?provider="',      # OAuth start
    ],
)
def test_auth_call_site_targets_a_bff_route(call):
    assert call in _code(), f"the page no longer calls the BFF route {call!r}"


# ---------------------------------------------------------------------------
# 503 is "we could not tell" — never "signed out" (#3485)
# ---------------------------------------------------------------------------
def test_the_page_never_treats_a_store_fault_as_signed_out():
    code = _code()
    # The probe consumer must branch on 503/0 explicitly and must not redirect
    # for them.
    assert "status === 503 || status === 0" in code, (
        "the session probe does not distinguish 'store unreachable' from 'signed out'"
    )
    notice = "couldn't check whether you're already signed in"
    assert notice in code, (
        "a store/network fault must surface as a retryable notice, not as the "
        "signed-out card"
    )
    # The ONLY redirect condition in the probe consumer is 200.
    i = code.find("window.__SESSION_PROBE.then")
    assert i != -1, "the session probe consumer was removed"
    body = code[i : code.find("\n    }", i)]
    first_redirect = body.find("location.replace")
    assert first_redirect != -1, f"the probe no longer forwards a signed-in visitor: {body!r}"
    # The redirect must sit under the `status === 200` branch: if it precedes the
    # 200 check, a 503 could reach it.
    j = body.find("status === 200")
    assert j != -1, body
    assert first_redirect > j, (
        "a redirect is reachable without a confirmed 200 — a 503 could sign the user out"
    )


# ---------------------------------------------------------------------------
# Preserved behaviours (the hard requirements)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "token,why",
    [
        ("loginRateLimitRemainingMs", "the login rate-limit lockout was removed"),
        ("showApiKeyModalError", "the API-key modal error surface was removed"),
        ("applyLoginRateLimitLockout", "the login lockout apply path was removed"),
        ("tortoise_signin_rate_limited_until", "the login lockout storage key was removed"),
        ("bad_oauth_state", "the #1224 OAuth state-expiry banner was removed"),
        ("oauthErrorParams", "the #1909 denied-provider hash handling was removed"),
        ("TURNSTILE_SITE_KEY", "the Turnstile/CAPTCHA path was removed"),
        ("initTurnstile", "Turnstile widget injection was removed"),
        ("resetTurnstile", "the single-use Turnstile re-arm was removed"),
        ("signupInFlight", "the double-submit guard was removed"),
        ("if (signupInFlight) return", "the double-submit early-return was removed"),
        ("window.__ADMIN_STALE", "the #3080 stale-bounce loop breaker was removed"),
        ("window.__ADMIN_RETURN_TO", "the #3080 admin return-to was removed"),
        ("claimRedirectTarget", "the claim-intent post-login routing was removed"),
        ("window.setLastAuthMethod", "the last-used preference writer was removed"),
        ("window.getLastAuthMethod", "the last-used preference reader was removed"),
        ("hash.replace", "the #1909 fragment error surface was removed"),
    ],
)
def test_preserved_behaviour_is_still_present(token, why):
    assert token in _code(), f"{why} ({token!r})"


def test_no_hardcoded_api_origin_remains():
    """The retired `/v1/*` fetches were the only reason for this constant.

    `API_ORIGIN` now lives in the BFF binding, so the page must not name the
    hosted API at all.
    """
    assert "api.premiselabs.co" not in _code(), (
        "the page still hardcodes the hosted API origin — the exchange belongs "
        "to the BFF (or the retired server-first signup is back)"
    )


def test_inline_scripts_parse(monkeypatch):
    """A parse-time SyntaxError kills the whole inline block (the #527 class)."""
    import os
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    html = PAGE.read_text(encoding="utf-8")
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S)
    assert scripts, "no inline script blocks found"
    for i, body in enumerate(scripts):
        if not body.strip():
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(body)
            path = f.name
        try:
            r = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=30)
            assert r.returncode == 0, f"signup.html script {i} failed node --check:\n{r.stderr}"
        finally:
            os.unlink(path)
