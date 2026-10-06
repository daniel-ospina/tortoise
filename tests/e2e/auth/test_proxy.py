"""
W6 proxy tests — the Token Handler route (/api/v1/*).

Verifies the behaviour that makes the proxy worth building:
  - the credential is attached SERVER-SIDE and works upstream
  - the credential NEVER reaches the browser
  - the raw session cookie is NOT forwarded upstream (it is ours, not theirs)
  - failure semantics stay distinct: 401 = signed out, 503 = store/upstream down
  - WebSocket upgrades are refused explicitly rather than half-forwarded
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
# Running from the old root logs "No Functions. Shimming..." and every /api/*
# route 404s.
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MOCK = REPO_ROOT / "tests" / "e2e" / "auth" / "mock_supabase.mjs"

APP_PORT = int(os.environ.get("AUTH_PROXY_APP_PORT", "8995"))
MOCK_PORT = int(os.environ.get("AUTH_PROXY_MOCK_PORT", "8996"))
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
def proxied():
    # FAIL, do not skip: a skipped security suite is indistinguishable from a
    # passing one. This suite reported "33 skipped" in CI while asserting nothing.
    require_toolchain()

    # Claim ports at runtime. Fixed ports let the suite attach to an unrelated
    # process that happens to be listening — green, asserting nothing.
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
        # Topology as configuration. Without this, /welcome redirects a
        # signed-in visitor to the real app origin and the test client follows
        # that redirect off-box (403). Binding it locally also exercises the
        # config-not-literal change from SCOPE.md 6.
        "-b", f"APP_ORIGIN={APP}",
            "-b", f"API_ORIGIN={MOCK_URL}",
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


def _session_cookie() -> str:
    """Drive the real sign-in flow with a plain cookie jar and return the cookie."""
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


def test_anonymous_proxy_call_is_401(proxied):
    req = urllib.request.Request(f"{APP}/api/v1/teams", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status, body = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode()
    assert status == 401, f"anonymous must be 401, got {status} {body}"
    assert json.loads(body)["error"] == "not_signed_in"


def test_proxy_attaches_credential_server_side(proxied):
    cookie = _session_cookie()
    req = urllib.request.Request(f"{APP}/api/v1/teams", method="GET")
    req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=45) as r:
        status, body = r.status, r.read().decode()
    assert status == 200, f"expected proxied 200, got {status} {body}"

    upstream = json.loads(body)
    # The upstream saw a real bearer credential the browser never had.
    assert upstream["authorization"], "proxy did not attach an Authorization header"
    assert upstream["authorization"].startswith("Bearer "), upstream["authorization"]
    # A JWT-shaped token, i.e. an actual Supabase access token.
    assert upstream["authorization"].count(".") == 2, upstream["authorization"]
    # Our session cookie must NOT be forwarded upstream — it is ours.
    assert upstream["cookieLeaked"] is False, "session cookie leaked to the upstream API"


def _fault(**kwargs) -> dict:
    """Toggle an injected fault on the mock. Returns the new fault state."""
    data = json.dumps(kwargs).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/fault", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def test_credential_never_reaches_the_browser(proxied):
    cookie = _session_cookie()
    req = urllib.request.Request(f"{APP}/api/v1/teams", method="GET")
    req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read().decode()
        headers = dict(r.headers)

    # The proxy's own job is to keep the token off the client. The mock echoes
    # back what it actually received, so this is observed, not assumed.
    upstream = json.loads(raw)
    assert upstream["cookieLeaked"] is False, "the session cookie was forwarded upstream"
    assert "__Host-session" not in raw, "session handle echoed to the client"
    # A Set-Cookie is a HEADER. Asserting its absence in the body would be
    # structurally incapable of failing — it was, in an earlier version.
    assert "Set-Cookie" not in headers, f"proxy must not set cookies, got {headers.get('Set-Cookie')}"


def test_upstream_failure_is_503_not_401(proxied):
    """The #3485 rule, at the proxy layer: an upstream fault is not a sign-out.

    This test previously documented that it could not reach the failure branch and
    asserted a 401 (signed-out) under a 503 name — so the property in the docstring
    had no guard at all. The mock now injects the fault, so the branch is real.
    """
    cookie = _session_cookie()
    _fault(upstream=True)
    try:
        req = urllib.request.Request(f"{APP}/api/v1/teams", method="GET")
        req.add_header("Cookie", cookie)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status, body = r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read().decode()
    finally:
        _fault(upstream=False)

    assert status == 503, (
        f"an upstream outage must be 503 (try again), never 401 (signed out) — "
        f"got {status} {body}"
    )
    assert json.loads(body)["error"] != "not_signed_in"


def _refresh_grants(reset: bool = False) -> int:
    """How many refresh_token grants the mock has served (optionally resetting)."""
    data = json.dumps({"reset": True} if reset else {}).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/stats", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())["refreshGrants"]


def _seen_paths(reset: bool = False) -> list[str]:
    """Paths the upstream mock was asked for (optionally resetting first)."""
    data = json.dumps({"reset": True} if reset else {}).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/paths", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())["paths"]


def test_upstream_401_is_503_and_does_not_storm_refreshes(proxied):
    """An UPSTREAM 401 is not OUR 401 — and must not stampede the token endpoint.

    Two properties, both previously unguarded:

    1. Our 401 means "you are not signed in", and only ever that. Forwarding an
       upstream 401 verbatim made /api/v1 say "signed out" while /api/session said
       200 for the same session — the #3485 divergence, mirrored.
    2. Clearing the cached token on every such rejection turns a persistent
       upstream 401 into one rotating-refresh-token grant per request, which is
       the stampede the D1 cache exists to prevent (and GoTrue's reuse detection
       can kill the session family). The cooldown bounds it.
    """
    cookie = _session_cookie()
    # Warm the token cache BEFORE measuring, so the baseline is a cached token.
    req0 = urllib.request.Request(f"{APP}/api/v1/teams", method="GET")
    req0.add_header("Cookie", cookie)
    with urllib.request.urlopen(req0, timeout=30) as r:
        r.read()

    _refresh_grants(reset=True)
    _fault(upstream401=True)
    try:
        statuses = []
        for _ in range(3):
            req = urllib.request.Request(f"{APP}/api/v1/teams", method="GET")
            req.add_header("Cookie", cookie)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    statuses.append(r.status)
            except urllib.error.HTTPError as e:
                statuses.append(e.code)
        grants = _refresh_grants()
    finally:
        _fault(upstream401=False)

    assert all(s == 503 for s in statuses), (
        f"an upstream credential rejection must be 503 (retry), never 401 "
        f"(signed out) — got {statuses}"
    )
    # The property the test's NAME claims. Without this the cooldown could be
    # deleted and the test would stay green (it only checked the status).
    assert grants <= 1, (
        f"3 rejected requests must not cause {grants} refresh grants — each one "
        "burns a single-use rotating refresh token, and GoTrue reuse-detection can "
        "then kill the session family"
    )


def test_proxy_cannot_escape_the_v1_prefix(proxied):
    """The wildcard must never reach a non-`/v1/` path on the upstream.

    WHATWG URL normalisation resolves dot-segments and treats PERCENT-ENCODED dots
    as dots, so `/api/v1/%2e%2e/admin` normalises to `<origin>/admin`. That would
    let an authenticated caller drive ANY path on API_ORIGIN with a valid Bearer
    attached — the opposite of this route's stated scope.

    The assertion is on what the UPSTREAM RECEIVED, not on the HTTP status. An
    earlier version asserted `status in (400, 404)` and was vacuous: Cloudflare's
    router normalises `%2e%2e` BEFORE function matching, so the request never
    reached this handler at all and the 404 came from Pages, not from our guard —
    deleting the guard left the test green. Observing the upstream is the only
    end-to-end proof.

    #4054 note: the dev server is now the DASHBOARD project, which is an SPA — so
    a normalised-away path is answered by the app shell (`index.html`, 200 +
    text/html) where the marketing project answered a Pages 404. The status check
    is therefore applied only when the PROXY actually handled the request (a JSON
    response); a proxied success is still a hard failure. The upstream-path check
    below remains the layout-independent proof.
    """
    cookie = _session_cookie()
    _seen_paths(reset=True)

    for hostile in (
        "/api/v1/%2e%2e/admin",
        "/api/v1/.%2e/admin",
        "/api/v1/..%2fadmin",
        "/api/v1/a/../../admin",
        "/api/v1/../../../etc/passwd",
    ):
        req = urllib.request.Request(f"{APP}{hostile}", method="GET")
        req.add_header("Cookie", cookie)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status = r.status
                ctype = r.headers.get("Content-Type", "")
                r.read()
        except urllib.error.HTTPError as e:
            status = e.code
            ctype = e.headers.get("Content-Type", "")
            e.read()
        # Two outcomes are legitimate and only these:
        #   1. the PROXY handled it and refused it — a 4xx, served as JSON.
        #   2. Cloudflare's router resolved the dot-segments before function
        #      matching, so `/api/v1/[[path]]` never ran and the SPA shell
        #      answered (HTML). Neither reaches the upstream.
        # A proxied SUCCESS is neither, and is the regression this guards.
        if "application/json" in ctype:
            assert status >= 400, (
                f"{hostile} was handled by the proxy and answered {status} — "
                "the traversal guard did not refuse it"
            )

    # Flag anything that could be a traversal, either as a foreign path or as an
    # encoded dot-segment the UPSTREAM may decode. Checking only the namespace
    # would miss `/v1/..%2fadmin`: URL normalisation does not decode `%2f`, so it
    # stays under `/v1/` and is forwarded verbatim for the upstream to interpret.
    legitimate = ("/v1/", "/auth/", "/__mock/")
    suspicious = [
        p
        for p in _seen_paths()
        if (not p.startswith(legitimate))
        or (".." in p)
        or ("%2e" in p.lower())
        or ("%2f" in p.lower())
        or ("%5c" in p.lower())
    ]
    assert not suspicious, (
        f"the proxy forwarded traversal-capable paths upstream: {suspicious} — the "
        "wildcard reached beyond its prefix or handed the upstream an encoded "
        "dot-segment with a valid bearer attached"
    )


def test_proxy_forwards_a_legitimate_nested_path(proxied):
    """The prefix check must not reject real nested routes."""
    cookie = _session_cookie()
    req = urllib.request.Request(f"{APP}/api/v1/teams/abc/members", method="GET")
    req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=30) as r:
        assert r.status == 200
        assert json.loads(r.read().decode())["path"] == "/v1/teams/abc/members"


def test_unknown_handle_is_401(proxied):
    """A dead handle IS a sign-out — the other side of the same distinction."""
    req = urllib.request.Request(f"{APP}/api/v1/teams", method="GET")
    req.add_header("Cookie", "__Host-session=deadbeef")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status, body = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode()
    assert status == 401, f"unknown handle must be 401, got {status} {body}"
    assert json.loads(body)["error"] == "not_signed_in"


def test_websocket_upgrade_is_refused_explicitly(proxied):
    """A half-supported tunnel is worse than an honest refusal."""
    cookie = _session_cookie()
    req = urllib.request.Request(f"{APP}/api/v1/stream", method="GET")
    req.add_header("Cookie", cookie)
    req.add_header("Upgrade", "websocket")
    req.add_header("Connection", "Upgrade")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status, body = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode()
    assert status == 426, f"expected 426 Upgrade Required, got {status} {body}"
    assert json.loads(body)["error"] == "websocket_not_supported"


def test_proxy_response_is_not_cacheable(proxied):
    cookie = _session_cookie()
    req = urllib.request.Request(f"{APP}/api/v1/teams", method="GET")
    req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=45) as r:
        cc = r.headers.get("Cache-Control", "")
    assert "no-store" in cc, f"authenticated proxy response must not be cached: {cc!r}"
