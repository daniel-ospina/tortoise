"""
Clickthrough verification for the #3501 BFF session flow.

Runs the REAL Cloudflare Pages runtime (`wrangler pages dev
website/apps/dashboard`) against a mock Supabase that performs REAL ES256
signing and REAL PKCE S256 verification.

The BFF moved with #4054: the auth + `/api/*` Functions now live in the
`tortoise-dashboard` Pages project rooted at `website/apps/dashboard`, so the
dev server runs from there. The blog Functions stayed in `website/` (the
`premise-labs` project); their admin-gate cases need a `website/`-rooted server
and — because one `wrangler pages dev` cannot serve both `functions/` trees —
they moved to `test_blog_purge_admin_gate.py`.

Nothing here is stubbed in a way that would hide a defect:
  - JWKS/token signing is genuine (Node crypto, raw r||s conversion)
  - the PKCE verifier check is genuine (S256 recompute + compare) — a mock that
    accepted any verifier would make the whole test worthless, since the entire
    design premise is that the verifier is SERVER-held
  - D1 is the real local D1 binding

Each assertion maps to a contract in SCOPE.md, named in the test.
"""
from __future__ import annotations

import contextlib
import http.cookiejar
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from bff_test_helpers import d1_sqlite_files, require_toolchain

REPO_ROOT = Path(__file__).resolve().parents[3]
# The BFF moved to the DASHBOARD Pages project (issue #4054). A Pages project's
# `functions/` directory must sit beside the site directory, so `wrangler pages
# dev .` runs from `website/apps/dashboard` — not `website/`.
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MOCK = Path(__file__).resolve().parent / "mock_supabase.mjs"

# Ports deliberately outside the 8790-8801 range: a pre-existing
# /tmp/wr-supervisor.sh holds a `wrangler pages dev dist --port 8790` on this
# machine. Colliding with it silently skipped the whole suite once already.
APP_PORT = int(os.environ.get("AUTH_TEST_APP_PORT", "8970"))
MOCK_PORT = int(os.environ.get("AUTH_TEST_MOCK_PORT", "8971"))
APP = f"http://127.0.0.1:{APP_PORT}"
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _pick_free_port(start: int, taken: set[int] | None = None) -> int:
    """Claim a free port, never returning one already handed out in this run.

    Two independent calls can both probe a free port and return the same value
    (the probe releases before the server binds). That previously made the app
    and the mock collide on one port, which presents as a confusing 404 rather
    than as a port error. `taken` closes that hole.

    An earlier version SKIPPED the whole suite when a port was busy. That is a
    no-op gate: green while testing nothing.
    """
    taken = taken if taken is not None else set()
    for port in range(start, start + 100):
        if port in taken:
            continue
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                taken.add(port)
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free port in {start}..{start + 100}")


def _wait(port: int, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.4)
    raise RuntimeError(f"port {port} never opened")


class Proc:
    def __init__(self, argv, cwd, env=None):
        self.p = subprocess.Popen(
            argv, cwd=cwd, env=env or os.environ.copy(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
        )

    def stop(self):
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(self.p.pid), signal.SIGTERM)


@pytest.fixture(scope="module")
def stack():
    # FAIL, do not skip. This is the largest auth suite (21 security assertions),
    # and when it skipped on a wrangler-less runner it reported "21 skipped" with
    # exit 0 — a green step asserting nothing. Only test_proxy.py and
    # test_welcome_and_password.py had been converted when cycle 4 caught this.
    require_toolchain()
    node = shutil.which("node")
    wrangler = shutil.which("wrangler")

    # Resolve free ports at runtime; never skip because of a collision.
    global APP_PORT, MOCK_PORT, APP, MOCK_URL
    claimed: set[int] = set()
    APP_PORT = _pick_free_port(APP_PORT, claimed)
    MOCK_PORT = _pick_free_port(MOCK_PORT, claimed)
    assert APP_PORT != MOCK_PORT, "app and mock must not share a port"
    APP = f"http://127.0.0.1:{APP_PORT}"
    MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"

    mock_env = os.environ.copy()
    mock_env["MOCK_PORT"] = str(MOCK_PORT)
    mock = Proc([node, str(MOCK)], cwd=str(MOCK.parent), env=mock_env)
    _wait(MOCK_PORT)

    # MUST run from inside the site directory. `wrangler pages dev <dir>` from
    # the repo root does NOT discover `<dir>/functions` — it logs
    # "No Functions. Shimming..." and every route 404s. With #4054 the Functions
    # live under DASHBOARD_DIR, so that is the site directory and the argv is ".".
    app = Proc(
        [
            wrangler, "pages", "dev", "dist",
            "--port", str(APP_PORT), "--ip", "127.0.0.1",
            "--d1", "SESSIONS",
            "-b", f"SUPABASE_URL={MOCK_URL}",
            "-b", "SUPABASE_ANON_KEY=mock-anon-key",
            "-b", f"AUTH_CALLBACK_URL={APP}/auth/callback",
        # Topology as configuration. Without this, /welcome redirects a
        # signed-in visitor to the real app origin and the test client follows
        # that redirect off-box (403). Binding it locally also exercises the
        # config-not-literal change from SCOPE.md 6.
        "-b", f"APP_ORIGIN={APP}",
        # Needed by /api/v1. Without it the proxy answers 503 proxy_not_configured
        # before the token path runs, so a data-path assertion would be testing the
        # config guard instead of the property.
        "-b", f"API_ORIGIN={MOCK_URL}",
        ],
        cwd=str(DASHBOARD_DIR),
    )
    try:
        _wait(APP_PORT)
    except RuntimeError:
        app.stop()
        mock.stop()
        out = b""
        with contextlib.suppress(Exception):
            out = app.p.stdout.read() if app.p.stdout else b""
        pytest.fail(f"pages dev failed to start; output:\n{out.decode('utf-8', 'replace')[-3000:]}")
    time.sleep(2.0)  # let the function bundler finish
    yield {"app": APP, "mock": MOCK_URL}
    app.stop()
    mock.stop()


class _LocalhostSecurePolicy(http.cookiejar.DefaultCookiePolicy):
    """Accept `Secure` cookies over http on loopback.

    Browsers treat http://127.0.0.1 as a secure context and store Secure cookies
    there; Python's cookiejar does not. Without this, every `__Host-` cookie is
    silently dropped and the flow tests fail for a reason that has nothing to do
    with the application — a false negative that would have masked real bugs.
    """

    def return_ok_secure(self, cookie, request):
        host = request.get_full_url() or ""
        if host.startswith("http://127.0.0.1") or host.startswith("http://localhost"):
            return True
        return super().return_ok_secure(cookie, request)

    def set_ok_domain(self, cookie, request):
        # A `__Host-` cookie legitimately has no Domain attribute.
        return super().set_ok_domain(cookie, request)


class Jar:
    """Cookie jar with redirect-following, recording the hop chain."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar(policy=_LocalhostSecurePolicy())
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPRedirectHandler(),
        )
        self.hops: list[tuple[str, int]] = []

    def get(self, url, follow=True):
        req = urllib.request.Request(url, method="GET")
        if not follow:
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a, **k):
                    return None
            op = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(self.jar), NoRedirect
            )
        else:
            op = self.opener
        try:
            with op.open(req, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
                self.hops.append((url, r.status))
                return r.status, body, _headers(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            self.hops.append((url, e.code))
            return e.code, body, _headers(e)

    def cookie(self, name):
        for c in self.jar:
            if c.name == name:
                return c.value
        return None


def _jar_call(j: Jar, req) -> tuple[int, str, str]:
    """Send `req` through `j`'s cookie jar WITHOUT following the redirect.

    Asserting a 302 needs the response itself; `j.opener` follows and would
    report the landing page's 200. Returns (status, Location, Set-Cookie).
    """
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(j.jar), _NoRedirect)
    try:
        with op.open(req, timeout=30) as r:
            return r.status, _headers(r).get("Location", ""), _headers(r).get("Set-Cookie", "")
    except urllib.error.HTTPError as e:
        return e.code, _headers(e).get("Location", ""), _headers(e).get("Set-Cookie", "")


def _headers(r) -> dict:
    """Headers as a plain dict, PRESERVING duplicate values.

    `dict(r.headers)` keeps only one Set-Cookie, and a response that both mints a
    session and clears the flow cookie sends two — so a naive dict would silently
    hide the very value under test.
    """
    h: dict[str, str] = {}
    for k, v in r.headers.items():
        h[k] = f"{h[k]}, {v}" if k in h else v
    return h


# ---------------------------------------------------------------------------
# Contract: /auth/start mints a flow and sets a host-only, HttpOnly flow cookie
# ---------------------------------------------------------------------------
def test_start_sets_bound_httponly_flow_cookie(stack):
    j = Jar()
    status, _, headers = j.get(f"{APP}/auth/start", follow=False)

    assert status == 302, f"expected redirect from /auth/start, got {status}"
    set_cookie = headers.get("Set-Cookie", "")
    assert "__Host-authflow=" in set_cookie, f"flow cookie missing: {set_cookie}"
    assert "HttpOnly" in set_cookie, "flow cookie must be HttpOnly"
    assert "Secure" in set_cookie, "flow cookie must be Secure (__Host- requires it)"
    assert "Path=/" in set_cookie, "__Host- requires Path=/"
    # The __Host- prefix forbids a Domain attribute. Emitting one would silently
    # break the prefix — this is the property that replaced the parent-domain
    # cookie, so assert its absence explicitly.
    assert "Domain=" not in set_cookie, "__Host- must not carry a Domain attribute"
    assert "Location" in headers
    assert MOCK_URL in headers["Location"], "must redirect to the Supabase authorize endpoint"
    assert "code_challenge=" in headers["Location"], "PKCE challenge must be sent"
    assert "code_challenge_method=s256" in headers["Location"], "S256 only"


# ---------------------------------------------------------------------------
# Contract: full happy path — start -> authorize -> callback -> session -> API
# ---------------------------------------------------------------------------
def test_full_clickthrough_signs_in_and_session_endpoint_agrees(stack):
    j = Jar()
    status, _, _ = j.get(f"{APP}/auth/start")
    assert status == 200, "redirect chain should complete"

    # After the chain, the session cookie must exist and be HttpOnly/host-only.
    assert j.cookie("__Host-session"), f"no session cookie; hops={j.hops}"

    status, body, headers = j.get(f"{APP}/api/session")
    assert status == 200, f"/api/session should report signed in, got {status} {body}"
    payload = json.loads(body)
    assert payload["user"]["id"] == "user-123", payload
    assert "no-store" in headers.get("Cache-Control", ""), "session responses must not be cached"


def test_session_cookie_is_httponly_hostonly(stack):
    """The single security property the whole redesign exists to obtain.

    Asserted on the RAW Set-Cookie header. An earlier version read the property off
    the cookie jar as `c.has_nonstandard_attr("HttpOnly") or True`, inside a loop
    that never executed: Python's cookiejar does not model HttpOnly, so the
    assertion had been rewritten into a form that could never fail. The property
    was therefore asserted nowhere outside the opt-in browser test.
    """
    # A missing flow cookie must not mint a session.
    j = Jar()
    status, _, _ = j.get(f"{APP}/auth/callback?code=missing", follow=False)
    assert status in (302, 200)
    assert j.cookie("__Host-session") is None, "must not mint a session without a flow cookie"

    # Walk the real chain with redirects OFF, so each hop's headers are visible.
    j2 = Jar()
    status, _, headers = j2.get(f"{APP}/auth/start", follow=False)
    assert status == 302, f"expected a redirect from /auth/start, got {status}"
    authorize_url = headers["Location"]

    status, _, headers = j2.get(authorize_url, follow=False)
    assert status == 302, f"expected the authorize hop to redirect, got {status}"
    callback_url = headers["Location"]
    assert "/auth/callback" in callback_url, callback_url

    status, _, headers = j2.get(callback_url, follow=False)
    assert status == 302, f"expected the callback to redirect, got {status}"
    set_cookie = headers.get("Set-Cookie", "")
    assert "__Host-session=" in set_cookie, (
        f"the callback did not set a session cookie: {set_cookie!r}"
    )
    assert "HttpOnly" in set_cookie, "the session cookie MUST be HttpOnly — that is the redesign"
    assert "Secure" in set_cookie, "__Host- requires Secure"
    assert "Path=/" in set_cookie, "__Host- requires Path=/"
    assert "Domain=" not in set_cookie, "__Host- must not carry Domain — it silently breaks the prefix"


# ---------------------------------------------------------------------------
# Contract: class-8 binding — a credential with no matching flow is NOT accepted
# ---------------------------------------------------------------------------
def test_callback_without_matching_flow_gets_interstitial_not_session(stack):
    j = Jar()
    status, _, headers = j.get(f"{APP}/auth/callback?code=anything", follow=False)
    assert status == 302, f"expected interstitial redirect, got {status}"
    assert "interstitial=1" in headers.get("Location", ""), headers
    assert j.cookie("__Host-session") is None, "SECURITY: logged in without a flow cookie"


def test_callback_with_forged_flow_cookie_is_rejected(stack):
    """A cookie that merely EXISTS is not the mitigation — it must MATCH a row.

    The previous version of this test ended in `assert True`, which tested
    nothing. It now asserts the security-relevant outcome directly: a forged
    flow cookie must not yield a session cookie.
    """
    req = urllib.request.Request(f"{APP}/auth/callback?code=x")
    req.add_header("Cookie", "__Host-authflow=deadbeefdeadbeef")

    # Do NOT follow redirects: the interstitial IS a 302, and following it would
    # report the /auth page's 200 and hide the assertion being made here.
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=30) as r:
            status, headers = r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        status, headers = e.code, dict(e.headers)

    set_cookie = headers.get("Set-Cookie", "")
    assert "__Host-session=" not in set_cookie, (
        f"SECURITY: forged flow cookie minted a session — {set_cookie}"
    )
    assert status == 302, f"forged flow must be answered with the interstitial, got {status}"
    assert "interstitial=1" in headers.get("Location", ""), headers.get("Location")


def test_email_confirmation_completes_through_the_interstitial(stack):
    """#3528: an emailed confirmation link must COMPLETE.

    `type=email` (signup confirmation AND magic link) used to be answered
    `302 /auth?interstitial=1` whenever the browser carried no `__Host-authflow`
    cookie matching a live `auth_flows` row — and NO email flow establishes one:
    `FLOW_COOKIE` is minted only by `/auth/start` (OAuth) and `/auth/link`
    (identity linking), while `/auth/reset` and `/auth/resend` set
    `redirect_to=/auth/confirm` without one. A link opened from an inbox was
    therefore answered with a redirect nothing consumes, and the single-use
    `token_hash` was dropped — email confirmation did not work at all.

    It now has the same contract as recovery: the GET verifies the token,
    renders the consent interstitial, and mints NOTHING; the CSRF-guarded POST
    mints exactly one session. A confirmation is NOT a password reset, so it
    lands on `/welcome`, never on the reset panel.
    """
    j = Jar()
    status, body, _ = j.get(
        f"{APP}/auth/confirm?token_hash=abc&type=email", follow=False
    )
    assert status == 200, (
        f"an email confirmation link must render the interstitial, got {status} {body[:200]}"
    )
    assert "Confirm it's you" in body, f"not the interstitial: {body[:200]!r}"
    assert "password" not in body.lower(), (
        "a confirmation link must not be presented as a password reset"
    )
    assert j.cookie("__Host-session") is None, (
        "the confirm GET minted a session from the link alone — the fixation vector"
    )
    assert j.cookie("__Host-authflow"), (
        "the interstitial must bind the pending confirmation to this browser"
    )

    # The POST carries the id the PAGE displayed; the cookie alone is
    # per-browser, not per-tab (see confirm.ts).
    req = urllib.request.Request(
        f"{APP}/auth/confirm",
        method="POST",
        data=json.dumps({"pending": j.cookie("__Host-authflow")}).encode(),
    )
    req.add_header("Content-Type", "application/json")
    status, location, _ = _jar_call(j, req)
    assert status == 302, f"the confirm POST must redirect, got {status}"
    assert "/welcome" in location, f"confirmation must land on /welcome, got {location!r}"
    assert "reset=1" not in location, f"a confirmation is not a reset: {location!r}"
    assert j.cookie("__Host-session"), "the confirmed POST minted no session"


def test_recovery_confirm_completes_through_the_interstitial(stack):
    """#4104 review (cycle 2): recovery completes CROSS-DEVICE, WITHOUT minting
    a session from the link alone.

    The earlier shape exempted `type=recovery` from the class-8 `__Host-authflow`
    binding and minted a session straight from the `token_hash`. That re-opened
    the session-fixation vector: an attacker requests a reset for THEIR OWN
    address, gets a genuine link, and a victim who clicks it has the ATTACKER's
    session minted into their browser. The `token_hash` alone cannot stop that —
    the attacker can always obtain one for their own account.

    So the GET verifies the token, mints NOTHING, and renders an interstitial
    naming the account; the POST (CSRF-guarded, bound to the pending record by
    the `__Host-authflow` cookie the GET just set in THIS browser) is what mints
    the session. Both hops happen in one browser, so cross-device still works:
    the cookie is created by the GET, not required to pre-exist.
    """
    j = Jar()
    status, body, _ = j.get(
        f"{APP}/auth/confirm?token_hash=recovery-token&type=recovery", follow=False
    )
    assert status == 200, (
        f"the recovery GET must render the consent interstitial, got {status} {body[:200]}"
    )
    assert "Confirm it's you" in body, f"not the interstitial: {body[:200]!r}"
    assert j.cookie("__Host-session") is None, (
        "the recovery GET minted a session from the link alone — the fixation vector"
    )
    assert j.cookie("__Host-authflow"), (
        "the interstitial must bind the pending recovery to this browser"
    )

    # The interstitial's own Continue action: a JSON POST carrying the cookie.
    # The POST carries the id the PAGE displayed; the cookie alone is
    # per-browser, not per-tab (see confirm.ts).
    req = urllib.request.Request(
        f"{APP}/auth/confirm",
        method="POST",
        data=json.dumps({"pending": j.cookie("__Host-authflow")}).encode(),
    )
    req.add_header("Content-Type", "application/json")
    # No-redirect opener that still carries the jar: the response to ASSERT is
    # the 302 itself (a redirect-following opener swallows it and returns 200).
    status, location, _ = _jar_call(j, req)
    assert status == 302, f"the confirm POST must redirect, got {status}"
    assert "/welcome?reset=1" in location, (
        f"a completed recovery must land on the reset panel, got {location!r}"
    )
    assert j.cookie("__Host-session"), (
        "recovery did not mint a session — the reset panel is then unreachable"
    )


def test_recovery_confirm_replaces_a_stale_flow_cookie(stack):
    """A leftover flow cookie from an aborted sign-in must not block recovery.

    A stale `__Host-authflow` in the recovering browser is common; it must be
    REPLACED by the pending recovery the GET creates, and the POST must then
    complete — not be refused as an unbound flow.
    """
    j = Jar()
    # Pre-seed the stale cookie in the jar, so the GET sees it and the POST
    # would carry it if the GET had not replaced it.
    j.jar.set_cookie(http.cookiejar.Cookie(
        version=0, name="__Host-authflow", value="deadbeefdeadbeef",
        port=None, port_specified=False, domain="127.0.0.1",
        domain_specified=True, domain_initial_dot=False, path="/",
        path_specified=True, secure=True, expires=None, discard=False,
        comment=None, comment_url=None, rest={}, rfc2109=False,
    ))

    status, body, _ = j.get(
        f"{APP}/auth/confirm?token_hash=recovery-token&type=recovery", follow=False
    )
    assert status == 200, f"a stale flow cookie must not block recovery, got {status}"
    assert "Confirm it's you" in body, f"not the interstitial: {body[:200]!r}"
    assert j.cookie("__Host-authflow") not in (None, "deadbeefdeadbeef"), (
        "the stale __Host-authflow was not replaced by the pending recovery"
    )

    # The POST carries the id the PAGE displayed; the cookie alone is
    # per-browser, not per-tab (see confirm.ts).
    req = urllib.request.Request(
        f"{APP}/auth/confirm",
        method="POST",
        data=json.dumps({"pending": j.cookie("__Host-authflow")}).encode(),
    )
    req.add_header("Content-Type", "application/json")
    status, location, _ = _jar_call(j, req)
    assert status == 302, f"the confirm POST must redirect, got {status}"
    assert "interstitial" not in location, (
        f"recovery was treated as an unbound flow: {location!r}"
    )
    assert "/welcome?reset=1" in location, f"expected the reset panel, got {location!r}"
    assert j.cookie("__Host-session"), (
        "recovery with a stale flow cookie minted no session"
    )


# ---------------------------------------------------------------------------
# Contract: signed-out is 401, store-unreachable is 503 — never conflated (#3485)
# ---------------------------------------------------------------------------
def test_session_contract_includes_profile_for_the_chrome(stack):
    """§8.2 contract, EXTENDED: the UI needs email + display name.

    The D1 row stores neither (§8.1), so the BFF asks GoTrue with the token it
    holds. Pins the extended shape so the dashboard migration can rely on it.
    """
    j = Jar()
    j.get(f"{APP}/auth/start")
    status, body, _ = j.get(f"{APP}/api/session")
    assert status == 200, body
    payload = json.loads(body)
    user = payload["user"]
    assert user["id"], payload
    assert user.get("email"), f"email missing from /api/session: {payload}"
    assert user.get("displayName"), f"displayName missing from /api/session: {payload}"


def _fault(**kwargs) -> dict:
    """Toggle an injected fault on the mock."""
    data = json.dumps(kwargs).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/fault", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def test_profile_lookup_failure_does_not_sign_the_user_out(stack):
    """A cosmetic profile lookup must never invalidate a valid session.

    The fault is INJECTED. An earlier version accepted a `monkeypatch` argument,
    never used it, and re-ran the happy path asserting `user.id` — so it could not
    fail for the reason its name asserts.
    """
    j = Jar()
    j.get(f"{APP}/auth/start")
    assert j.cookie("__Host-session"), "sign-in must produce a session first"

    _fault(authUser=True)
    try:
        status, body, _ = j.get(f"{APP}/api/session")
    finally:
        _fault(authUser=False)

    assert status == 200, (
        f"a profile outage must NOT sign the user out (got {status} {body}) — the "
        "session is valid and identity should degrade to id-only"
    )
    user = json.loads(body)["user"]
    assert user["id"], "id must survive a profile failure"
    assert user.get("email") is None, "profile fields should be absent when the lookup failed"


def test_signout_revokes_the_row_not_just_the_cookie(stack):
    """Sign-out must REVOKE server-side, not merely clear the cookie client-side.

    The previous version could not fail: the POST's `Max-Age=0` makes the cookie
    jar drop the cookie, so the follow-up request sent no cookie and 401'd whether
    or not revocation ran. Deleting `revokeSession` left it green. So the raw
    handle is replayed explicitly.
    """
    j = Jar()
    j.get(f"{APP}/auth/start")
    handle = j.cookie("__Host-session")
    assert handle, f"sign-in produced no session; hops={j.hops}"

    req = urllib.request.Request(f"{APP}/api/session", method="POST", data=b"{}")
    # The CSRF guard's first layer requires a JSON media type (415 otherwise) —
    # the browser client sends it, so the replay must too.
    req.add_header("Content-Type", "application/json")
    with j.opener.open(req, timeout=30) as r:
        assert r.status == 200

    # Replay the ORIGINAL handle — proving the row was revoked, not that the
    # browser stopped sending it.
    req2 = urllib.request.Request(f"{APP}/api/session", method="GET")
    req2.add_header("Cookie", f"__Host-session={handle}")
    try:
        with urllib.request.urlopen(req2, timeout=30) as r:
            status, body = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode()
    assert status == 401, (
        f"a revoked handle must be 401 even when replayed directly, got {status} {body} "
        "— if this is 200 the row was NOT revoked"
    )


def test_next_destination_persists_and_is_honoured(stack):
    """`next` must survive the flow — it is stored, not passed through the URL.

    Also a regression test for schema drift: `CREATE TABLE IF NOT EXISTS` cannot
    add a column to an existing table, so if `next` were only declared in the
    CREATE statement this INSERT would fail (500) on any pre-existing database.
    """
    j = Jar()
    status, _, headers = j.get(f"{APP}/auth/start?next=%2Fdashboard%2Fsettings", follow=False)
    assert status == 302, f"/auth/start broke — likely schema drift, got {status}"

    authorize_url = headers["Location"]
    status, _, headers = j.get(authorize_url, follow=False)
    assert status == 302, f"authorize hop failed: {status}"
    status, _, headers = j.get(headers["Location"], follow=False)
    assert status == 302, f"callback failed: {status}"

    loc = headers.get("Location", "")
    assert loc == "/dashboard/settings", (
        f"the stored `next` should be honoured, got {loc!r} — if this is /welcome, "
        "`next` was dropped"
    )


def test_next_rejects_cross_origin_destinations(stack):
    """`next` must not become an open redirect.

    The first version of `safeNext` was a prefix check, and it was bypassable: a
    TAB survives `startsWith("/")` and WHATWG URL parsing STRIPS it, so
    `/\t/evil.example` resolves to `https://evil.example/`. The victim completes a
    normal login and lands on the attacker's origin with a live session.

    So this covers every vector, and asserts on the RESOLVED origin rather than on
    a string prefix — prefix-matching was the bug.
    """
    from urllib.parse import quote, urljoin

    vectors = [
        "https://evil.example/steal",  # absolute
        "//evil.example/steal",  # protocol-relative
        "/\\evil.example",  # backslash
        "/\tevil.example",  # TAB — stripped by the URL parser
        "/\t/evil.example",  # TAB before a slash
        "/\n/evil.example",  # LF (also corrupts the Location header)
        "/\r\nX-Injected: 1",  # CRLF header injection attempt
        "/\x00evil.example",  # NUL
        "/..//evil.example",  # resolves same-origin, serialises to a network-path ref
        "/a/..//evil.example",
        "/.//evil.example",
        "mailto:x@evil.example",  # non-http scheme
        "javascript:alert(1)",  # scheme confusion
    ]
    base = "http://localhost"  # any origin; we only compare, never fetch

    for hostile in vectors:
        j = Jar()
        status, _, headers = j.get(
            f"{APP}/auth/start?next={quote(hostile, safe='')}", follow=False
        )
        assert status == 302, f"/auth/start must not 500 on {hostile!r}: {status}"
        status, _, headers = j.get(headers["Location"], follow=False)
        assert status == 302, f"authorize hop failed for {hostile!r}: {status}"
        status, _, headers = j.get(headers["Location"], follow=False)
        # A CRLF value previously threw here (Invalid header value -> 500).
        assert status == 302, f"callback must not 500 on {hostile!r}: {status}"
        loc = headers.get("Location", "")
        assert loc == "/welcome", f"hostile next {hostile!r} must fall back, got {loc!r}"
        # The real property: resolving the Location against our origin must stay
        # on our origin. This is what the prefix check failed to guarantee.
        assert urljoin(base, loc).startswith("http://localhost/"), (
            f"{hostile!r} produced an off-origin redirect: {urljoin(base, loc)!r}"
        )


def test_next_accepts_a_legitimate_same_origin_path(stack):
    """The defense must not be a blanket 'reject everything'."""
    j = Jar()
    status, _, headers = j.get(f"{APP}/auth/start?next=%2Fapp%2Fsettings%3Ftab%3Dx", follow=False)
    assert status == 302
    status, _, headers = j.get(headers["Location"], follow=False)
    assert status == 302
    status, _, headers = j.get(headers["Location"], follow=False)
    assert status == 302
    assert headers.get("Location") == "/app/settings?tab=x"


def test_malformed_cookie_does_not_500(stack):
    """An undecodable cookie must be treated as unsigned, not crash the endpoint.

    A bare `%` raises URIError inside decodeURIComponent, which escaped and turned
    every endpoint's careful 401/503 contract into a 500.
    """
    # `/blog/api/purge` is NOT served by this server — the blog Functions stayed
    # in the `premise-labs` project (website/), so its malformed-cookie case
    # lives in test_blog_purge_admin_gate.py against a website/-rooted server.
    # Dropping it here would silently lose that route's coverage (a 404 is also
    # `!= 500`), which is why it was moved rather than left to pass vacuously.
    for path in ("/api/session", "/api/v1/teams", "/welcome"):
        req = urllib.request.Request(f"{APP}{path}", method="GET")
        req.add_header("Cookie", "__Host-session=%")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status != 500, f"{path} 500s on a malformed cookie"


def test_unconfigured_provider_is_503_not_401(stack):
    """A server MISCONFIGURATION must not be reported as 'you are signed out'.

    `call()` returned non-retryable for 'supabase not configured', so
    getAccessTokenForSession classified it as `no_session` and the proxy answered
    401 AND cleared the session cookie — while /api/session reported the same
    session as valid. The server asserted both signed-in and signed-out for one
    session; that is the #3485 divergence.

    Asserted structurally at the source, because the running stack has the
    provider configured: a not-configured result must be RETRYABLE, which is what
    routes it to 503 rather than 401.
    """
    src = (DASHBOARD_DIR / "functions/_shared/auth/supabase.ts").read_text(encoding="utf-8")
    marker = 'error: "supabase not configured"'
    idx = src.index(marker)
    line = src[:idx].count("\n")
    block = src.splitlines()[line]
    assert "retryable: true" in block, (
        f"a not-configured provider must be retryable (-> 503), got: {block.strip()}"
    )
    assert "retryable: false" not in block


def test_dead_refresh_token_401s_consistently(stack):
    """`/api/session` and `/api/v1` must AGREE about a dead refresh token.

    This was the surviving #3485 divergence: with a dead refresh token,
    `/api/v1` answered 401 + cleared the cookie while `/api/session` kept
    answering 200 — permanently. Both endpoints were describing one session
    differently, and `/api/session` is documented as the "single source of
    session truth".
    """
    j = Jar()
    j.get(f"{APP}/auth/start")
    assert j.cookie("__Host-session"), "need a session first"

    # Force the session's refresh token to be unusable.
    _fault(refreshDead=True)
    try:
        s1, _b1, _ = j.get(f"{APP}/api/session")
        # Clear the cached access token so the next call must refresh into the
        # dead-token path rather than serving from the D1 cache.
        import sqlite3

        for db in d1_sqlite_files(DASHBOARD_DIR):
            try:
                con = sqlite3.connect(db)
                con.execute(
                    "UPDATE sessions SET access_token = NULL, access_token_expires_at = NULL"
                )
                con.commit()
                con.close()
            except Exception:
                pass
        s2, b2, _ = j.get(f"{APP}/api/session")
    finally:
        _fault(refreshDead=False)

    assert s2 == 401, (
        f"a dead refresh token means the session is UNUSABLE — /api/session must "
        f"answer 401, got {s2} {b2} (it answered {s1} before)"
    )


def test_expired_session_is_401_on_both_endpoints(stack):
    """An EXPIRED session must be dead on the DATA path too, not just on /api/session.

    `getAccessTokenForSession` selected `WHERE handle = ? AND revoked = 0` with no
    expiry predicate, so the proxy kept serving authenticated upstream data (200)
    while `/api/session` answered 401 `session_expired` for the SAME cookie — and
    it re-minted from the still-valid refresh token indefinitely, because the
    400-day handle TTL was never applied. The two endpoints disagreed, with the
    permissive side on the one that returns data.

    Every other consumer enforces `expires_at`; this pins that the data path does
    too.
    """
    import sqlite3

    j = Jar()
    j.get(f"{APP}/auth/start")
    handle = j.cookie("__Host-session")
    assert handle, "need a session first"

    # Force the row's TTL into the past.
    patched = 0
    for db in d1_sqlite_files(DASHBOARD_DIR):
        try:
            con = sqlite3.connect(db)
            cur = con.execute(
                "UPDATE sessions SET expires_at = ?1 WHERE revoked = 0", (1,)
            )
            patched += cur.rowcount
            con.commit()
            con.close()
        except Exception:
            pass
    assert patched > 0, "could not expire a session row — the test would be vacuous"

    # `/api/v1` FIRST, with the raw handle replayed explicitly.
    #
    # Order matters and an earlier version of this test got it wrong: calling
    # /api/session first REVOKES the row and returns clearCookie, so a later
    # /api/v1 sees `revoked = 1` (or no cookie) and 401s *whether or not* the
    # expires_at predicate exists. That version passed with the fix deleted.
    # Replaying the handle directly also keeps the assertion independent of the
    # jar dropping a cleared cookie.
    req = urllib.request.Request(f"{APP}/api/v1/teams", method="GET")
    req.add_header("Cookie", f"__Host-session={handle}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            s_proxy, b_proxy = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        s_proxy, b_proxy = e.code, e.read().decode()

    assert s_proxy == 401, (
        f"/api/v1 must 401 an expired session, got {s_proxy} {b_proxy} — a 200 here "
        "means the data path ignores expires_at and serves upstream data for a "
        "session the other endpoint calls expired"
    )

    # Now /api/session, same raw handle — it must agree.
    req2 = urllib.request.Request(f"{APP}/api/session", method="GET")
    req2.add_header("Cookie", f"__Host-session={handle}")
    try:
        with urllib.request.urlopen(req2, timeout=30) as r:
            s_sess, b_sess = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        s_sess, b_sess = e.code, e.read().decode()

    assert s_sess == 401, f"/api/session must 401 an expired session, got {s_sess} {b_sess}"


def test_anonymous_session_read_is_401_not_503(stack):
    j = Jar()
    status, body, headers = j.get(f"{APP}/api/session")
    assert status == 401, f"anonymous must be 401, got {status} {body}"
    assert json.loads(body)["error"] == "not_signed_in"
    assert "no-store" in headers.get("Cache-Control", "")


# ---------------------------------------------------------------------------
# Contract: deprecated verify types are refused (SCOPE.md §5.2)
# ---------------------------------------------------------------------------
def test_deprecated_verify_type_is_refused_by_upstream(stack):
    """Guards §5.2's correction: `signup`/`magiclink` are deprecated."""
    import urllib.request as u

    def post(path, payload):
        req = u.Request(
            f"{MOCK_URL}{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with u.urlopen(req, timeout=20) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    assert post("/auth/v1/verify", {"token_hash": "t", "type": "signup"}) == 400
    assert post("/auth/v1/verify", {"token_hash": "t", "type": "magiclink"}) == 400
    assert post("/auth/v1/verify", {"token_hash": "t", "type": "email"}) == 200


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
