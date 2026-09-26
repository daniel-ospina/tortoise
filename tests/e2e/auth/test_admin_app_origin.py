"""#4171 — the blog admin console and its blog-API proxy on the APP origin.

WHY THIS SUITE EXISTS
---------------------
#4054 moved the session BFF (and the host-only `__Host-session` cookie) to
app.premiselabs.co but left the console on tortoise.*, so its relative
`/api/session` check read the SPA shell and the operator bounced to sign-in. The
console now lives at app.premiselabs.co/admin (same-origin with the session) and
its `/blog/api/*` calls go through a same-origin Token Handler proxy on the app
origin (SCOPE.md §4 W6).

These cases run the REAL Pages runtime (`wrangler pages dev dist` from the
dashboard project, which is where the gate + proxy Functions live) against a
local mock. Nothing is stubbed in a way that hides the property:
  - D1 is the real local binding; the session row is seeded and resolved
    server-side exactly as production does;
  - the gate really mints the access token and sends it as the USER — the mock
    records the Authorization header and the test asserts it;
  - the proxy really streams the request upstream and the mock really refuses
    the empty body (400 invalid_slug), so a proxy that swallowed the response
    would fail.

The shell is staged from the COMMITTED blog-admin `dist/` (the same snapshot the
`#3952` guards read), so the suite needs no blog-admin node_modules.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from bff_test_helpers import pick_free_port, require_toolchain, stop

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
BLOG_ADMIN_DIST = REPO_ROOT / "website" / "apps" / "blog-admin" / "dist"
MIGRATION = REPO_ROOT / "website" / "migrations" / "0001_auth_sessions.sql"
MOCK = Path(__file__).resolve().parent / "mock_blog_admin_rpc.mjs"

# Distinct from every other auth suite's range so parallel collection cannot
# collide (8790-8801, 8970-8971, 8980-8981, 8995-8998, 9002-9006).
APP_PORT = int(os.environ.get("AUTH_ADMIN_APP_PORT", "9010"))
MOCK_PORT = int(os.environ.get("AUTH_ADMIN_MOCK_PORT", "9011"))
APP = f"http://127.0.0.1:{APP_PORT}"
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"

HANDLE = "a" * 64
UNKNOWN_HANDLE = "b" * 64
ACCESS = "mock-access-token"

# This suite's PRIVATE D1 persist dir, set by the `stack` fixture.
#
# Without it the suite reads the SHARED `website/apps/dashboard/.wrangler` state,
# and that state accumulates rows from every other auth suite forever. That is
# not hypothetical: `test_link_flow.py` seeds a session for handle `"b" * 64`
# (user-aaa, token seed-access-token), which is exactly this suite's
# UNKNOWN_HANDLE — so the "unknown handle bounces to sign-in" case resolved a
# real row and got 200 instead of 302, permanently, once test_link_flow had ever
# run against that shared state. The assertion was measuring another suite's
# leftovers. `test_bff_profile_and_email.py` already isolates for this reason;
# this suite now does too (#4104).
PERSIST: Path | None = None


def _wait(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def _d1_files() -> list[Path]:
    """The D1 DATABASE files in this suite's isolated persist dir.

    Not simply "the newest *.sqlite": the persist tree also holds
    `metadata.sqlite` (D1's index) and the observability trace store, and
    picking one of those seeds a database the Worker never reads — which
    presents as a route 503 with the schema looking fine in the file, not as a
    seeding error.
    """
    assert PERSIST is not None, "stack fixture must run first"
    return [
        p for p in PERSIST.glob("**/d1/**/*.sqlite") if p.name != "metadata.sqlite"
    ]


def _d1_sqlite() -> Path:
    deadline = time.time() + 30
    while time.time() < deadline:
        files = _d1_files()
        if files:
            return max(files, key=lambda p: p.stat().st_mtime)
        time.sleep(0.3)
    raise RuntimeError(f"no D1 database sqlite appeared under {PERSIST}")


def _warm_d1() -> None:
    """Force Miniflare to MATERIALISE the bound D1 database file.

    `--d1 SESSIONS` only declares the binding: the SQLite file appears on the
    first D1 ACCESS, not at boot — so seeding the schema first times out on a
    clean runner. A request carrying an unknown `__Host-session` IS a D1 read.
    """
    deadline = time.time() + 30
    while time.time() < deadline:
        req = urllib.request.Request(f"{APP}/admin")
        req.add_header("Cookie", "__Host-session=" + "0" * 64)
        with contextlib.suppress(Exception):
            urllib.request.urlopen(req, timeout=15).read()
        if _d1_files():
            return
        time.sleep(0.3)
    raise RuntimeError(f"no D1 database sqlite appeared under {PERSIST}")


def _seed() -> None:
    """Apply the auth migration, then insert ONE live session with a cached token.

    The cached token means `getAccessTokenForSession` never calls GoTrue, so the
    only upstream the gate touches is the `is_admin()` RPC — which the mock
    records.
    """
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        con.executescript(MIGRATION.read_text(encoding="utf-8"))
        now = int(time.time() * 1000)
        con.execute(
            "INSERT OR REPLACE INTO sessions "
            "(handle,user_id,refresh_token,revoked,created_at,expires_at,"
            " access_token,access_token_expires_at) "
            "VALUES (?,?,?,0,?,?,?,?)",
            (HANDLE, "user-admin", "mock-refresh", now, now + 400 * 24 * 3600 * 1000, ACCESS, now + 3600 * 1000),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture(scope="module")
def stack(_dashboard_dist_built, tmp_path_factory):
    require_toolchain()
    node = shutil.which("node")
    wrangler = shutil.which("wrangler")

    # Stage the committed console shell where the gate's ASSETS read finds it.
    admin_dst = DASHBOARD_DIR / "dist" / "admin"
    assert BLOG_ADMIN_DIST.is_dir(), (
        f"committed blog-admin dist missing at {BLOG_ADMIN_DIST} — run "
        "`cd website/apps/blog-admin && npm run build`"
    )
    if admin_dst.exists():
        shutil.rmtree(admin_dst)
    shutil.copytree(BLOG_ADMIN_DIST, admin_dst)

    global APP_PORT, MOCK_PORT, APP, MOCK_URL, PERSIST
    claimed: set[int] = set()
    APP_PORT = pick_free_port(APP_PORT, claimed)
    MOCK_PORT = pick_free_port(MOCK_PORT, claimed)
    APP = f"http://127.0.0.1:{APP_PORT}"
    MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"
    # A private D1 for this suite alone — see the PERSIST comment above.
    PERSIST = tmp_path_factory.mktemp("admin-bff-d1")

    env = os.environ.copy()
    env["MOCK_PORT"] = str(MOCK_PORT)
    mock = subprocess.Popen(
        [node, str(MOCK)], cwd=str(MOCK.parent), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    assert _wait(MOCK_PORT), "mock failed to start"

    app = subprocess.Popen(
        [
            wrangler, "pages", "dev", "dist",
            "--port", str(APP_PORT), "--ip", "127.0.0.1",
            "--compatibility-date=2026-08-26",
            "--d1", "SESSIONS",
            "--persist-to", str(PERSIST),
            "-b", f"SUPABASE_URL={MOCK_URL}",
            "-b", "SUPABASE_ANON_KEY=mock-anon-key",
            # Both the gate's RPC and the proxy's upstream live on the mock.
            "-b", f"BLOG_ORIGIN={MOCK_URL}",
        ],
        cwd=str(DASHBOARD_DIR),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    if not _wait(APP_PORT):
        stop(app)
        stop(mock)
        pytest.fail("pages dev failed to start")
    time.sleep(2.5)
    _warm_d1()
    _seed()

    yield {"app": APP, "mock": MOCK_URL}

    for p in (app, mock):
        stop(p)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _req(path: str, method: str = "GET", cookie: str | None = None, data: bytes | None = None):
    req = urllib.request.Request(APP + path, method=method, data=data)
    if cookie:
        req.add_header("Cookie", cookie)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _OPENER.open(req, timeout=30) as r:
            return r.status, r.read().decode(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers


def _state() -> dict:
    with urllib.request.urlopen(f"{MOCK_URL}/__mock/state", timeout=15) as r:
        return json.loads(r.read().decode())


def _set_admin(value: bool) -> None:
    req = urllib.request.Request(f"{MOCK_URL}/__mock/admin", method="POST", data=json.dumps({"value": value}).encode())
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def _reset() -> None:
    req = urllib.request.Request(f"{MOCK_URL}/__mock/reset", method="POST", data=b"{}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def test_admin_shell_is_served_on_the_app_origin_for_an_admin(stack):
    """(b) the console is reachable on the app origin, same-origin with the session.

    A `__Host-session` cookie that resolves to an admin serves the SPA shell
    from this project's ASSETS — the shell's own asset refs are `/admin/...`, so
    the bundle the browser then requests is same-origin too.
    """
    _reset()
    status, body, headers = _req("/admin", cookie=f"__Host-session={HANDLE}")
    assert status == 200, f"an admin must get the shell, got {status} {body[:200]}"
    assert "text/html" in headers.get("Content-Type", "")
    assert "/admin/assets/" in body, (
        "the served shell does not reference /admin/assets/ — the console would not load its bundle"
    )
    # The gate really did authorise AS THE USER: the RPC carried the minted token.
    rpc = [s for s in _state()["seen"] if s["kind"] == "is_admin"]
    assert rpc, "the gate never called is_admin()"
    assert rpc[-1]["auth"] == f"Bearer {ACCESS}", (
        f"the is_admin() call did not carry the minted user token: {rpc[-1]['auth']!r}"
    )


def test_admin_bounce_is_same_origin_and_not_a_cross_origin_ping_pong(stack):
    """(d) no origin bounce: the unauthenticated bounce is a same-origin path.

    The bug #4171 fixes was a console on tortoise.* whose session lived on app.*;
    the gate must send the operator to THIS origin's /auth, never back to
    tortoise.* (which would 301 here again).
    """
    status, _body, headers = _req("/admin", cookie=f"__Host-session={UNKNOWN_HANDLE}")
    assert status == 302, f"an unknown handle must bounce to sign-in, got {status}"
    location = headers.get("Location", "")
    assert location == "/auth?next=%2Fadmin&stale=1", (
        f"the bounce must be a same-origin path with the return-to and stale marker, got {location!r}"
    )
    assert not location.startswith("http"), f"the bounce left the origin: {location!r}"

    # And with no cookie at all the same contract holds.
    status, _body, headers = _req("/admin")
    assert status == 302 and headers.get("Location", "").startswith("/auth?next=%2Fadmin"), headers


def test_a_signed_in_non_admin_gets_an_explicit_403(stack):
    """A non-admin must not loop; the gate says so plainly (#3080)."""
    _reset()
    _set_admin(False)
    try:
        status, body, _headers = _req("/admin", cookie=f"__Host-session={HANDLE}")
    finally:
        _set_admin(True)
    assert status == 403, f"a signed-in non-admin must be 403, got {status}"
    assert "Not a blog admin" in body, body[:200]


def test_blog_api_proxy_forwards_the_server_minted_credential(stack):
    """(item 3) the relative /blog/api/* call works from the app origin.

    For the BFF session the browser holds only the HttpOnly handle; the proxy mints the token and
    attaches it upstream. The assertion is on the UPSTREAM request the mock
    recorded — a proxy that answered 200 without forwarding would fail.
    """
    _reset()
    status, body, _headers = _req(
        "/blog/api/purge", method="POST", cookie=f"__Host-session={HANDLE}", data=b"{}"
    )
    # The mock's refusal is forwarded verbatim (a real 4xx, not our 401/503).
    assert status == 400, f"expected the upstream 400 to pass through, got {status} {body}"
    assert json.loads(body).get("error") == "invalid_slug", body
    blog = [s for s in _state()["seen"] if s["kind"] == "blog"]
    assert blog, "the proxy never reached the blog upstream"
    assert blog[-1]["path"] == "/blog/api/purge", blog[-1]
    assert blog[-1]["auth"] == f"Bearer {ACCESS}", (
        f"the proxy did not attach the minted token upstream: {blog[-1]['auth']!r}"
    )
    assert blog[-1]["body"] == "{}", f"the request body was not forwarded: {blog[-1]['body']!r}"


def test_blog_api_proxy_requires_a_session(stack):
    """No cookie → OUR 401, and the upstream is never touched."""
    _reset()
    status, body, _headers = _req("/blog/api/purge", method="POST", data=b"{}")
    assert status == 401, f"an anonymous proxy call must be 401, got {status} {body}"
    assert json.loads(body).get("error") == "not_signed_in", body
    assert not [s for s in _state()["seen"] if s["kind"] == "blog"], (
        "the proxy reached the upstream without a session"
    )
