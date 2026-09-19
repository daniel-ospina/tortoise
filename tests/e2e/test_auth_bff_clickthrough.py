"""
Real-browser clickthrough for the #3501 BFF auth flow.

Unlike `tests/e2e/auth/test_bff_flow.py` (HTTP-level), this drives an actual
Chromium through the flow and asserts the properties that only a browser can
demonstrate:

  1. the session cookie is genuinely `HttpOnly` — `document.cookie` cannot see it
  2. `document.cookie` exposes NO session material at all (the property the whole
     redesign exists to obtain — the old flow put tokens where JS could read them)
  3. the redirect chain completes and the app's own session endpoint agrees

Harness: serve the `tortoise-dashboard` project with
`wrangler pages dev dist` (the BFF and the session cookie issuance live there
since #4054), mock Supabase Auth locally (real ES256 + real S256).
Opt-in via AUTH_CLICKTHROUGH=1.
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

# Ports are claimed at runtime. Both e2e modules previously defaulted to the
# SAME pair (8993/8994) and are run in one pytest invocation in CI, and
# `_wait()` returns True for ANY listener — so a stale wrangler from a
# previous run (built from older code) could be silently adopted.
APP_PORT = int(os.environ.get("AUTH_CT_APP_PORT", "9000"))
MOCK_PORT = int(os.environ.get("AUTH_CT_MOCK_PORT", "9001"))
APP = f"http://localhost:{APP_PORT}"
MOCK_URL = f"http://localhost:{MOCK_PORT}"

pytestmark = pytest.mark.skipif(
    os.environ.get("AUTH_CLICKTHROUGH") != "1",
    reason="opt-in: set AUTH_CLICKTHROUGH=1 (spawns a local server + browser)",
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
def running_stack():
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
    mock_env["MOCK_PORT"] = str(MOCK_PORT)
    mock = subprocess.Popen(
        [shutil.which("node"), str(MOCK)], cwd=str(MOCK.parent), env=mock_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    assert _wait(MOCK_PORT), "mock supabase failed to start"

    app = subprocess.Popen(
        [
            shutil.which("wrangler"), "pages", "dev", "dist",
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
        ],
        cwd=str(DASHBOARD_DIR),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    ok = _wait(APP_PORT)
    if not ok:
        os.killpg(os.getpgid(app.pid), signal.SIGTERM)
        os.killpg(os.getpgid(mock.pid), signal.SIGTERM)
        pytest.fail("wrangler pages dev failed to start")
    time.sleep(2.5)

    yield {"app": APP, "mock": MOCK_URL}

    for p in (app, mock):
        _stop(p)


def test_browser_clickthrough_session_is_httponly_and_invisible_to_js(running_stack):
    """The one assertion this whole redesign exists to make true."""
    import playwright.sync_api as playwright_sync  # hard dep: see the note above
    with playwright_sync.sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context()
        page = ctx.new_page()

        # Click through: /auth/start -> authorize -> /auth/callback -> /welcome
        page.goto(f"{APP}/auth/start", wait_until="load", timeout=45_000)
        page.wait_for_load_state("load", timeout=30_000)

        # --- the security property -------------------------------------------------
        cookies = {c["name"]: c for c in ctx.cookies()}
        session = cookies.get("__Host-session")
        assert session is not None, (
            f"no __Host-session cookie after clickthrough; url={page.url} "
            f"cookies={list(cookies)}"
        )
        assert session["httpOnly"] is True, "SESSION COOKIE IS READABLE BY JAVASCRIPT"
        assert session["secure"] is True, "session cookie must be Secure"
        assert session["sameSite"].lower() == "lax", session["sameSite"]
        assert session["path"] == "/", session["path"]
        # `__Host-` semantics: no Domain attribute at all.
        assert not session.get("domain", "").startswith("."), (
            f"session cookie must be host-only, got domain={session.get('domain')}"
        )

        # --- JS cannot see it, and no token material leaks into JS ------------------
        visible = page.evaluate("() => document.cookie")
        assert "__Host-session" not in visible, f"JS can read the session cookie: {visible}"
        assert "access_token" not in visible and "refresh_token" not in visible, visible

        # No session material left in storage either — the old flow leaked here.
        storage = page.evaluate(
            "() => JSON.stringify({ls: {...localStorage}, ss: {...sessionStorage}})"
        )
        assert "eyJ" not in storage, f"a JWT is sitting in web storage: {storage[:400]}"
        assert "access_token" not in storage, storage[:400]

        # --- the app agrees ---------------------------------------------------------
        status = page.evaluate(
            "async () => (await fetch('/api/session', {credentials: 'same-origin'})).status"
        )
        assert status == 200, (
            f"/api/session should report signed in, got {status}; page.url={page.url}"
        )

        body = page.evaluate(
            "async () => await (await fetch('/api/session', {credentials:'same-origin'})).json()"
        )
        assert body.get("user", {}).get("id"), body

        browser.close()


def test_browser_cross_host_credential_gets_interstitial(running_stack):
    """Class-8 binding, observed in a real browser.

    A credential arriving on a host that never started the flow must NOT
    silently produce a session. This is the fixation vector.
    """
    import playwright.sync_api as playwright_sync  # hard dep: see the note above
    with playwright_sync.sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context()
        page = ctx.new_page()
        # Direct callback with no flow cookie present at all.
        page.goto(f"{APP}/auth/callback?code=anything", wait_until="load", timeout=45_000)
        names = {c["name"] for c in ctx.cookies()}
        assert "__Host-session" not in names, (
            f"SECURITY: session minted without a matching flow cookie: {names}"
        )
        assert "interstitial=1" in page.url, page.url
        browser.close()
