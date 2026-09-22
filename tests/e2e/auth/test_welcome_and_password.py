"""
/welcome + /auth/update-password — the post-auth landing, decided server-side (#3501).

WHY THESE EXIST
---------------
`welcome.html` used to decide in the browser whether a visitor was signed in, via
a hard gate calling `readValidSession()`. That function reads the legacy
parent-domain `sb-tortoise-auth-token` cookie — after first migrating any legacy
localStorage session into it — and a BFF login writes only the HttpOnly
`__Host-session`, so a browser holding no legacy session read as signed OUT and
was bounced to /auth: the #3485 loop.

The fix is that the server decides. These tests pin that decision, because the
failure mode (a client with no session it can see, bouncing anyway) is invisible
to every other test in the suite: the unit tests all passed while /welcome was
broken.

They also pin the reset flow, which could not survive the migration untouched:
the form used to call `supabaseClient.auth.updateUser()` from the browser, which
can never work when the BFF session cookie is HttpOnly and the page has no
client-readable BFF credential.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from bff_test_helpers import pick_free_port, require_toolchain, stop

REPO_ROOT = Path(__file__).resolve().parents[3]
# The BFF moved to the DASHBOARD Pages project (issue #4054) — FROM
# `website/functions/` (the `premise-labs` project) TO
# `website/apps/dashboard/functions/` (the `tortoise-dashboard` project). A
# Pages project's `functions/` directory must sit beside the site directory, so
# `wrangler pages dev .` now runs from `website/apps/dashboard`, not `website/`.
# Running from the old root logs "No Functions. Shimming..." and every /welcome
# + /auth/* route 404s.
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MOCK = REPO_ROOT / "tests" / "e2e" / "auth" / "mock_supabase.mjs"

APP_PORT = int(os.environ.get("AUTH_WP_APP_PORT", "8997"))
MOCK_PORT = int(os.environ.get("AUTH_WP_MOCK_PORT", "8998"))
APP = f"http://localhost:{APP_PORT}"
MOCK_URL = f"http://localhost:{MOCK_PORT}"


def _wait(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


@pytest.fixture(scope="module")
def stack():
    # FAIL, do not skip — see conftest.py.
    require_toolchain()

    # Claim ports at runtime so this cannot attach to a foreign listener.
    global APP_PORT, MOCK_PORT, APP, MOCK_URL
    claimed: set[int] = set()
    APP_PORT = pick_free_port(APP_PORT, claimed)
    MOCK_PORT = pick_free_port(MOCK_PORT, claimed)
    assert APP_PORT != MOCK_PORT
    APP = f"http://localhost:{APP_PORT}"
    MOCK_URL = f"http://localhost:{MOCK_PORT}"

    env = os.environ.copy()
    env["MOCK_PORT"] = str(MOCK_PORT)
    mock = subprocess.Popen(
        [shutil.which("node"), str(MOCK)], cwd=str(MOCK.parent), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    assert _wait(MOCK_PORT), "mock failed to start"

    app = subprocess.Popen(
        [
            shutil.which("wrangler"), "pages", "dev", "dist",
            "--port", str(APP_PORT), "--ip", "127.0.0.1",
            "--d1", "SESSIONS",
            "-b", f"SUPABASE_URL={MOCK_URL}",
            "-b", "SUPABASE_ANON_KEY=mock-anon-key",
            "-b", f"AUTH_CALLBACK_URL={APP}/auth/callback",
            "-b", f"API_ORIGIN={MOCK_URL}",
            # Topology as configuration. Without this /welcome redirects a
            # signed-in visitor to the real app origin and the client follows it
            # off-box. Binding locally also exercises the config-not-literal
            # change from SCOPE.md §6.
            "-b", f"APP_ORIGIN={APP}",
        ],
        cwd=str(DASHBOARD_DIR),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    if not _wait(APP_PORT):
        os.killpg(os.getpgid(app.pid), signal.SIGTERM)
        os.killpg(os.getpgid(mock.pid), signal.SIGTERM)
        pytest.fail("pages dev failed to start")
    time.sleep(2.5)

    yield {"app": APP}

    for p in (app, mock):
        stop(p)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface redirects instead of following them — the assertion IS the Location."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _req(path: str, cookie: str | None = None, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{APP}{path}", method=method, data=data)
    if cookie:
        req.add_header("Cookie", cookie)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=45) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def _session_cookie() -> str:
    """Drive the real sign-in flow and return the `__Host-session` cookie."""
    import http.cookiejar

    class Policy(http.cookiejar.DefaultCookiePolicy):
        def return_ok_secure(self, cookie, request):
            u = request.get_full_url() or ""
            if u.startswith(("http://127.0.0.1", "http://localhost")):
                return True
            return super().return_ok_secure(cookie, request)

    jar = http.cookiejar.CookieJar(policy=Policy())
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with opener.open(f"{APP}/auth/start", timeout=45) as r:
        r.read()
    for c in jar:
        if c.name == "__Host-session":
            return f"__Host-session={c.value}"
    raise AssertionError("sign-in did not produce a session cookie")


# ── /welcome: the server decides ──────────────────────────────────────────


def test_anonymous_welcome_redirects_to_auth(stack):
    """Genuinely signed out -> /auth. This is a 302 from the SERVER.

    It must not be a rendered page that then decides — that was the old design,
    and a browser holding no legacy session had no credential the client could see,
    so the client gate decided "signed out".
    """
    status, _, headers = _req("/welcome")
    assert status == 302, f"anonymous /welcome must redirect, got {status}"
    loc = headers.get("Location", "")
    assert loc.startswith("/auth"), loc
    assert "stale=1" in loc, "the redirect should mark the state stale"


def test_signed_in_welcome_redirects_to_the_app(stack):
    """Signed in -> straight to the app. NOT back to /auth.

    This is the #3485 regression test: the old client gate sent a browser holding
    no legacy session to /auth on every load, because it could never observe the
    BFF session.
    """
    cookie = _session_cookie()
    status, _, headers = _req("/welcome", cookie=cookie)
    assert status == 302, f"signed-in /welcome must redirect, got {status}"
    loc = headers.get("Location", "")
    assert APP in loc, f"signed-in /welcome must go to the app origin, got {loc!r}"
    assert "/auth" not in loc, "signed-in user must NOT be bounced back to /auth — that is #3485"


def test_reset_landing_serves_the_panel(stack):
    """?reset=1 with a valid session is the ONE rendered case.

    #4054: `functions/welcome.ts` serves this with `env.ASSETS.fetch(request)`,
    so the asset must exist in THIS project. `welcome.html` (the only file
    containing `#reset-panel`) was moved to `website/apps/dashboard/public/`
    with the rest of the auth surface, so the panel now resolves on the app
    origin.

    This asserts against the BUILT root (`dist/`) because that is what gets
    deployed — see `tests/e2e/auth/conftest.py`. Against the source root the
    page is not at `/welcome` at all and Pages answers with the SPA shell, which
    would make this assertion silently check `index.html`.
    """
    cookie = _session_cookie()
    status, body, _ = _req("/welcome?reset=1", cookie=cookie)
    assert status == 200, f"reset landing must render, got {status}"
    assert "reset-panel" in body, "reset panel markup missing"
    # Strip HTML comments first: the file deliberately explains what was removed,
    # so scanning raw source would match the explanation rather than live code.
    import re as _re

    code = _re.sub(r"<!--.*?-->", "", body, flags=_re.DOTALL)
    assert "@supabase/supabase-js" not in code, "welcome.html still loads supabase-js"
    assert (
        "window.createTortoiseSupabaseClient(" not in code
    ), "welcome.html still builds a client session"
    assert "cdn.jsdelivr.net" not in code, "welcome.html still pulls a CDN script"


# ── /auth/update-password ─────────────────────────────────────────────────


def test_update_password_anonymous_is_401(stack):
    """No session -> 401. Only ever means "not signed in"."""
    status, body, _ = _req("/auth/update-password", method="POST", body={"password": "Abcdefg1!"})
    assert status == 401, f"anonymous update must be 401, got {status} {body}"
    assert json.loads(body)["error"] == "not_signed_in"


def test_update_password_weak_is_400_before_any_provider_call(stack):
    """The policy is enforced SERVER-SIDE. The client check is convenience only.

    A forged request must not bypass it, so this is asserted against the server
    with no client involved.
    """
    cookie = _session_cookie()
    # NOTE: there is deliberately no uppercase requirement in the policy, so an
    # all-lowercase password with a digit + symbol IS valid. An earlier version of
    # this test asserted otherwise and was wrong — the code was right.
    for weak in ("", "short1!", "nodigits!!", "NoSymbols1", "      1!"):
        status, body, _ = _req(
            "/auth/update-password", cookie=cookie, method="POST", body={"password": weak}
        )
        assert status == 400, f"weak password {weak!r} must be 400, got {status} {body}"
        assert json.loads(body)["error"] == "weak_password"


def test_update_password_revokes_the_session(stack):
    """F15: a password change must leave the session DEAD.

    Without this, `revokeAllForUser` in update-password.ts could be deleted and the
    suite would stay green — the property is presented as a security guarantee and
    was asserted nowhere.
    """
    import http.cookiejar

    class _Policy(http.cookiejar.DefaultCookiePolicy):
        def return_ok_secure(self, cookie, request):
            u = request.get_full_url() or ""
            if u.startswith(("http://127.0.0.1", "http://localhost")):
                return True
            return super().return_ok_secure(cookie, request)

    jar = http.cookiejar.CookieJar(policy=_Policy())
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with opener.open(f"{APP}/auth/start", timeout=45) as r:
        r.read()
    handle = next((c.value for c in jar if c.name == "__Host-session"), None)
    assert handle, "sign-in produced no session"

    status, body, _ = _req(
        "/auth/update-password",
        cookie=f"__Host-session={handle}",
        method="POST",
        body={"password": "An0ther-Pass!"},
    )
    assert status == 200, f"password change should succeed, got {status} {body}"

    # Replay the SAME handle. F15 says every session for this user is revoked.
    status, body, _ = _req("/api/session", cookie=f"__Host-session={handle}")
    assert status == 401, (
        f"the session must be dead after a password change, got {status} {body} — "
        "if 200, revokeAllForUser did not run (F15)"
    )


def test_update_password_succeeds_for_a_valid_password(stack):
    """Happy path: the BFF makes the call on the user's behalf."""
    cookie = _session_cookie()
    status, body, _ = _req(
        "/auth/update-password", cookie=cookie, method="POST", body={"password": "Str0ng-Pass!"}
    )
    assert status == 200, f"valid password must succeed, got {status} {body}"
    assert json.loads(body)["ok"] is True
