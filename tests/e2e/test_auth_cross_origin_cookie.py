"""
Proof for the cross-origin session-cookie constraint (W6 blocker).

Question: can the browser's `__Host-session` cookie authenticate the dashboard's
calls to `api.premiselabs.co`?

`__Host-` requires: Secure, Path=/, and NO Domain attribute — so the cookie is
HOST-ONLY. The claim to test is that a host-only cookie is therefore NOT sent to
a sibling origin (`api.premiselabs.co` from `app.premiselabs.co`), which would
mean the BFF session cannot authenticate the dashboard's own API calls.

This is asserted by OBSERVATION here, not by argument: the mock echoes the exact
Cookie header the browser sent to a different ORIGIN.

The sibling origin stands in for api.premiselabs.co: what matters is the cookie
scope rule, which is host-based — so the stand-in must differ by HOSTNAME, not
merely by port (cookies ignore the port, RFC 6265 §5.1.3).
"""
from __future__ import annotations

import contextlib
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest

from tests.e2e.auth.bff_test_helpers import ensure_dashboard_dist

REPO_ROOT = Path(__file__).resolve().parents[2]
# #4054: the BFF moved to the `tortoise-dashboard` project. These suites
# must boot THAT Pages project — serving website/ would answer /auth/*
# with the SPA fallback and no session would ever be minted.
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MOCK = REPO_ROOT / "tests" / "e2e" / "auth" / "mock_supabase.mjs"

APP_PORT = int(os.environ.get("AUTH_CX_APP_PORT", "8993"))
API_PORT = int(os.environ.get("AUTH_CX_API_PORT", "9101"))
APP = f"http://localhost:{APP_PORT}"
# MUST be a different HOSTNAME, not just a different port. Cookies are
# host-scoped and ignore the port (RFC 6265 §5.1.3), so two ports on the same
# host share cookies and would produce a false "the cookie WAS sent" result —
# which is exactly what the first version of this test did.
API = f"http://127.0.0.1:{API_PORT}"

pytestmark = pytest.mark.skipif(
    os.environ.get("AUTH_CLICKTHROUGH") != "1",
    reason="opt-in: set AUTH_CLICKTHROUGH=1",
)


def _stop(proc) -> None:
    """Terminate a process group and REAP it.

    Without the wait, a subsequent module's readiness probe can succeed against
    this dying server and adopt it — green, testing stale code.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        return
    try:
        proc.wait(timeout=15)
    except Exception:
        # Escalate to SIGKILL; the process may already be gone, which is fine.
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def _wait(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


@pytest.fixture(scope="module")
def two_origins():
    for binary in ("node", "wrangler"):
        if not shutil.which(binary):
            if os.environ.get("AUTH_ALLOW_NO_TOOLCHAIN") == "1":
                pytest.skip(f"{binary} unavailable (AUTH_ALLOW_NO_TOOLCHAIN=1)")
            pytest.fail(
                f"{binary} not available — this suite is the ONLY place the browser-level\n"
                "session properties are asserted; it must not silently skip. Install it or\n"
                "set AUTH_ALLOW_NO_TOOLCHAIN=1 to opt out explicitly."
            )

    # `wrangler pages dev dist` needs the built root (vite copies public/ into
    # dist/); building here keeps the suite self-sufficient rather than depending
    # on `tests/e2e/auth/` having run first in the same job.
    ensure_dashboard_dist()

    mock_env = os.environ.copy()
    mock_env["MOCK_PORT"] = str(API_PORT)
    # The app's Supabase calls also go to the "api" origin here.
    api = subprocess.Popen(
        [shutil.which("node"), str(MOCK)], cwd=str(MOCK.parent), env=mock_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    assert _wait(API_PORT), "mock api failed to start"

    app = subprocess.Popen(
        [
            shutil.which("wrangler"), "pages", "dev", "dist",
            "--port", str(APP_PORT), "--ip", "127.0.0.1",
            "--d1", "SESSIONS",
            "-b", f"SUPABASE_URL={API}",
            "-b", "SUPABASE_ANON_KEY=mock-anon-key",
            "-b", f"AUTH_CALLBACK_URL={APP}/auth/callback",
        # Topology as configuration. Without this, /welcome redirects a
        # signed-in visitor to the real app origin and the test client follows
        # that redirect off-box (403). Binding it locally also exercises the
        # config-not-literal change from SCOPE.md 6.
        "-b", f"APP_ORIGIN={APP}",
        ],
        cwd=str(DASHBOARD_DIR),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    if not _wait(APP_PORT):
        os.killpg(os.getpgid(app.pid), signal.SIGTERM)
        os.killpg(os.getpgid(api.pid), signal.SIGTERM)
        pytest.fail("wrangler pages dev failed to start")
    time.sleep(2.5)

    yield {"app": APP, "api": API}

    for p in (app, api):
        _stop(p)


def test_host_only_session_cookie_is_not_sent_to_a_sibling_origin(two_origins, request):
    """The W6 blocker, demonstrated.

    If this FAILS (cookie IS sent), the BFF session can authenticate the
    dashboard's API calls directly and no proxy is needed. A failure here is
    therefore informative in either direction — which is the point of testing it
    rather than reasoning about it.
    """
    import playwright.sync_api as playwright_sync  # hard dep: see the note above
    with playwright_sync.sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context()
        page = ctx.new_page()

        # Sign in through the real flow so a genuine __Host-session exists.
        page.goto(f"{APP}/auth/start", wait_until="load", timeout=45_000)
        cookies = {c["name"]: c for c in ctx.cookies()}
        assert "__Host-session" in cookies, (
            f"setup failed: not signed in; url={page.url} cookies={list(cookies)}"
        )

        # Now ask a DIFFERENT origin what Cookie header it receives.
        #
        # Through the browser context's own request API rather than
        # `page.evaluate(fetch(...))`: the app's responses now carry a
        # Content-Security-Policy (#3525) whose `connect-src` cannot list a
        # throwaway localhost port, so a renderer-side fetch to the sibling
        # origin is refused before dispatch, and the test would then fail
        # for a reason that has nothing to do with cookies. Observed:
        #
        #   Connecting to 'http://127.0.0.1:9101/__mock/echo-cookie'
        #   violates the following Content Security Policy directive:
        #   "connect-src 'self'". The action has been blocked.
        #   -> TypeError: Failed to fetch
        #
        # `ctx.request` shares the context's cookie JAR, which is where the
        # `__Host-` host-scoping rule actually lives, so the OBSERVATION and
        # the assertion's power are unchanged: a cookie ever emitted with a
        # Domain attribute would still be attached to this sibling request.
        # Positive control FIRST: prove the channel actually carries cookies to
        # the sibling, so a "not in sent" result below cannot pass vacuously. A
        # cookie scoped to the sibling's own host MUST come back.
        ctx.add_cookies([{"name": "sibling_probe", "value": "1",
                          "domain": "127.0.0.1", "path": "/"}])
        control = ctx.request.get(f"{API}/__mock/echo-cookie").json().get("cookie") or ""
        assert "sibling_probe=1" in control, (
            f"control failed: the sibling request carried no cookie at all "
            f"({control!r}) — the host-only assertion below would be vacuous"
        )

        echoed = ctx.request.get(f"{API}/__mock/echo-cookie").json()
        sent = echoed.get("cookie") or ""

        # Record the observation so the result is visible either way.
        request.node.user_properties.append(("cookie_sent_to_sibling", sent))

        assert "__Host-session" not in sent, (
            "UNEXPECTED: the host-only session cookie WAS sent to a sibling "
            f"origin ({sent!r}). If this is reproducible, the BFF session can "
            "authenticate cross-origin API calls and W6 is unnecessary."
        )

        browser.close()
