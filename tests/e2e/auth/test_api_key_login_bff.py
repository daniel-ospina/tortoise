"""
POST /auth/api-key — the BFF API-key login route (#3501/#4054).

Runs the REAL Cloudflare Pages runtime (`wrangler pages dev` from the dashboard
Pages project root) against a focused mock that serves BOTH upstreams the route
touches: `api.premiselabs.co/v1/session/login` (the key→session exchange) and
`/auth/v1/user` (the profile read).

Everything asserted here is observed at a surface that can actually disagree:
  - the D1 row is read back with sqlite3, so "was a session created?" is not
    inferred from a 200
  - the upstream call log is read from the mock, so "was the key forwarded?" is
    not inferred from a status code
  - the raw Set-Cookie header is inspected, so the `__Host-` properties are
    checked rather than assumed
  - the response body is scanned, so "no token reached the browser" is proven

Status discipline under test (the #3485 class): an infrastructure fault is 503
and must NEVER be 401 — a wrong API key is the ONLY thing that may be 401. A
403 (unclaimed team / minted key / missing account) is a decision about the KEY
and must pass through with its machine code, not be collapsed into 503.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import pytest
from bff_test_helpers import d1_sqlite_files, pick_free_port, require_toolchain

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MOCK = Path(__file__).resolve().parent / "mock_api_key_session.mjs"

APP_PORT = int(os.environ.get("APIKEY_TEST_APP_PORT", "9060"))
MOCK_PORT = int(os.environ.get("APIKEY_TEST_MOCK_PORT", "9061"))
NOSTORE_PORT = int(os.environ.get("APIKEY_TEST_NOSTORE_PORT", "9062"))
NOAPI_PORT = int(os.environ.get("APIKEY_TEST_NOAPI_PORT", "9063"))

APP = ""       # assigned in the `stack` fixture
NOSTORE = ""
NOAPI = ""
MOCK_URL = ""

VALID = {"api_key": "tt_valid_key"}
INVALID = {"api_key": "tt_invalid_key"}
ANON = {"api_key": "tt_anon_key"}
MINTED = {"api_key": "tt_minted_key"}
ACCOUNT_MISSING = {"api_key": "tt_account_missing"}
RATELIMITED = {"api_key": "tt_ratelimited"}
OUTAGE = {"api_key": "tt_outage"}
EMPTY_SESSION = {"api_key": "tt_empty_session"}


# ---------------------------------------------------------------------------
# Process + request helpers (mirrors tests/e2e/auth/test_password_bff.py)
# ---------------------------------------------------------------------------
class Proc:
    def __init__(self, argv, cwd, env=None):
        self.p = subprocess.Popen(
            argv, cwd=cwd, env=env or os.environ.copy(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
        )

    def stop(self):
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(self.p.pid), signal.SIGTERM)


def _wait(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def _start_app(port: int, *, with_d1: bool, with_api_origin: bool) -> Proc:
    argv = [
        shutil.which("wrangler"), "pages", "dev", "dist",
        "--port", str(port), "--ip", "127.0.0.1",
    ]
    if with_d1:
        argv += ["--d1", "SESSIONS"]
    argv += [
        "-b", f"SUPABASE_URL={MOCK_URL}",
        "-b", "SUPABASE_ANON_KEY=mock-anon-key",
        "-b", f"APP_ORIGIN=http://127.0.0.1:{port}",
    ]
    if with_api_origin:
        argv += ["-b", f"API_ORIGIN={MOCK_URL}"]
    return Proc(argv, cwd=str(DASHBOARD_DIR))


@pytest.fixture(scope="module")
def stack():
    # FAIL, do not skip: a skipped security suite is indistinguishable from a
    # passing one in CI (see bff_test_helpers.require_toolchain).
    require_toolchain()

    global APP_PORT, MOCK_PORT, APP, MOCK_URL
    claimed: set[int] = set()
    APP_PORT = pick_free_port(APP_PORT, claimed)
    MOCK_PORT = pick_free_port(MOCK_PORT, claimed)
    assert APP_PORT != MOCK_PORT
    APP = f"http://127.0.0.1:{APP_PORT}"
    MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"

    mock_env = os.environ.copy()
    mock_env["MOCK_PORT"] = str(MOCK_PORT)
    mock = Proc([shutil.which("node"), str(MOCK)], cwd=str(MOCK.parent), env=mock_env)
    if not _wait(MOCK_PORT):
        mock.stop()
        pytest.fail("mock api-key upstream failed to start")

    app = _start_app(APP_PORT, with_d1=True, with_api_origin=True)
    if not _wait(APP_PORT):
        app.stop()
        mock.stop()
        pytest.fail("pages dev failed to start")
    time.sleep(2.5)  # let the function bundler finish

    yield {"app": APP, "mock": MOCK_URL}
    app.stop()
    mock.stop()


@pytest.fixture(scope="module")
def nostore(stack):
    """The same route with NO D1 binding — the store-unavailable branch."""
    global NOSTORE_PORT, NOSTORE
    NOSTORE_PORT = pick_free_port(NOSTORE_PORT, {int(APP_PORT), int(MOCK_PORT)})
    NOSTORE = f"http://127.0.0.1:{NOSTORE_PORT}"
    app = _start_app(NOSTORE_PORT, with_d1=False, with_api_origin=True)
    if not _wait(NOSTORE_PORT):
        app.stop()
        pytest.fail("pages dev (no D1) failed to start")
    time.sleep(2.5)
    yield {"app": NOSTORE}
    app.stop()


@pytest.fixture(scope="module")
def noapi(stack):
    """The same route with D1 but NO API_ORIGIN — a deployment fault, not a bad key."""
    global NOAPI_PORT, NOAPI
    NOAPI_PORT = pick_free_port(NOAPI_PORT, {int(APP_PORT), int(MOCK_PORT), int(NOSTORE_PORT)})
    NOAPI = f"http://127.0.0.1:{NOAPI_PORT}"
    app = _start_app(NOAPI_PORT, with_d1=True, with_api_origin=False)
    if not _wait(NOAPI_PORT):
        app.stop()
        pytest.fail("pages dev (no API_ORIGIN) failed to start")
    time.sleep(2.5)
    yield {"app": NOAPI}
    app.stop()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _headers(r) -> dict:
    h: dict[str, str] = {}
    for k, v in r.headers.items():
        h[k] = f"{h[k]}, {v}" if k in h else v
    return h


def _post(base: str, path: str, payload: dict | None, cookie: str | None = None):
    data = json.dumps(payload if payload is not None else {}).encode()
    req = urllib.request.Request(f"{base}{path}", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=45) as r:
            return r.status, r.read().decode("utf-8", "replace"), _headers(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), _headers(e)


def _calls(reset: bool = False) -> list[dict]:
    data = json.dumps({"reset": reset}).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/calls", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())["calls"]


def _set_cookie_value(header: str, name: str) -> str | None:
    for part in header.split(","):
        part = part.strip()
        if part.startswith(f"{name}="):
            return part[len(name) + 1:].split(";")[0]
    return None


def _d1_sqlite() -> Path:
    deadline = time.time() + 30
    while time.time() < deadline:
        files = sorted(
            d1_sqlite_files(DASHBOARD_DIR),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if files:
            return files[0]
        time.sleep(0.3)
    raise RuntimeError(f"no D1 sqlite appeared under {DASHBOARD_DIR}")


def _session_row(handle: str) -> dict | None:
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM sessions WHERE handle = ?", (handle,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 200: the key is exchanged server-side; only the opaque handle reaches the page
# ---------------------------------------------------------------------------
def test_valid_key_mints_a_session_and_sets_the_host_cookie(stack):
    _calls(reset=True)
    before_ms = int(time.time() * 1000)
    status, body, headers = _post(APP, "/auth/api-key", VALID)
    assert status == 200, f"a valid key must be 200, got {status} {body}"

    parsed = json.loads(body)
    # The shape /api/session returns and the client reuses.
    assert parsed["user"]["id"] == "user-apikey-1", body
    assert parsed["user"]["email"] == "key@example.test", body
    assert parsed["user"]["displayName"] == "Key User", body
    assert isinstance(parsed["expiresAt"], int), body
    assert parsed["expiresAt"] > before_ms, body

    set_cookie = headers.get("Set-Cookie", "")
    assert "__Host-session=" in set_cookie, f"session cookie missing: {set_cookie!r}"
    assert "HttpOnly" in set_cookie, f"cookie must be HttpOnly: {set_cookie!r}"
    assert "Secure" in set_cookie, f"__Host- requires Secure: {set_cookie!r}"
    assert "Path=/" in set_cookie, f"__Host- requires Path=/: {set_cookie!r}"
    assert "Domain=" not in set_cookie, f"__Host- forbids Domain: {set_cookie!r}"

    # The D1 row is the session — read it back rather than trusting the 200.
    handle = _set_cookie_value(set_cookie, "__Host-session")
    assert handle, f"no handle in {set_cookie!r}"
    row = _session_row(handle)
    assert row is not None, "no D1 session row was created for the issued cookie"
    assert row["user_id"] == "user-apikey-1", row
    assert row["revoked"] == 0, row
    assert row["refresh_token"], row
    assert row["expires_at"] > before_ms, row

    # The credential is exchanged UPSTREAM and never handed back: neither the
    # submitted key nor either token may appear in the response.
    calls = _calls()
    assert len(calls) == 1, f"expected exactly one exchange, got {calls}"
    assert calls[0]["path"] == "/v1/session/login", calls
    assert calls[0]["outcome"] == "valid", calls
    assert calls[0]["hasKey"] is True, calls
    assert calls[0]["authorization"] is None, (
        f"the key must ride the BODY (the upstream's key-auth contract), not a header: {calls}"
    )
    assert VALID["api_key"] not in body, f"the raw API key was echoed into the response: {body}"
    assert "mock-access" not in body and "mock-refresh" not in body, (
        f"a server-held token leaked into the response: {body}"
    )
    assert "no-store" in headers.get("Cache-Control", ""), headers.get("Cache-Control")


# ---------------------------------------------------------------------------
# 401: a rejected key, and ONLY that
# ---------------------------------------------------------------------------
def test_invalid_key_is_401(stack):
    _calls(reset=True)
    status, body, _ = _post(APP, "/auth/api-key", INVALID)
    assert status == 401, f"an invalid key must be 401, got {status} {body}"
    parsed = json.loads(body)
    assert parsed["error"] == "invalid_api_key", body
    # The exchange reached the upstream; the 401 is its verdict.
    calls = _calls()
    assert len(calls) == 1 and calls[0]["outcome"] == "invalid", calls


# ---------------------------------------------------------------------------
# 403: upstream POLICY decisions pass through with their machine code
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload,code",
    [
        (ANON, "ANON_TEAM_NO_OWNER"),
        (MINTED, "KEY_NOT_USER_MINTED"),
        (ACCOUNT_MISSING, "ACCOUNT_MISSING"),
    ],
)
def test_policy_refusals_are_403_with_their_code(stack, payload, code):
    _calls(reset=True)
    status, body, _ = _post(APP, "/auth/api-key", payload)
    assert status == 403, f"{code} must be 403, got {status} {body}"
    assert status not in (401, 503), (
        f"{code} is a decision about the KEY and must not be collapsed into 401/503: {body}"
    )
    assert json.loads(body)["error"] == code, body


# ---------------------------------------------------------------------------
# 429: the hour-scale bucket is forwarded, not reported as an outage
# ---------------------------------------------------------------------------
def test_rate_limited_is_429_with_retry_after(stack):
    _calls(reset=True)
    status, body, headers = _post(APP, "/auth/api-key", RATELIMITED)
    assert status == 429, f"a throttled exchange must stay 429, got {status} {body}"
    assert status != 503, "a throttle was reported as an outage"
    assert json.loads(body)["error"] == "rate_limited", body
    assert headers.get("Retry-After") == "3600", (
        f"the upstream Retry-After must be forwarded so the page can render the "
        f"exact wait: {headers}"
    )


# ---------------------------------------------------------------------------
# 503: infrastructure faults are NEVER 401 (the #3485 rule)
# ---------------------------------------------------------------------------
def test_exchange_outage_is_503_not_401(stack):
    _calls(reset=True)
    status, body, _ = _post(APP, "/auth/api-key", OUTAGE)
    assert status == 503, f"an upstream outage must be 503, never 401 — got {status} {body}"
    assert status != 401, "an upstream fault was reported as 'invalid key' (#3485/#1719)"
    assert json.loads(body)["error"] == "provider_unavailable", body
    assert _calls(), "the upstream was never actually reached, so the outage path was not exercised"


def test_a_200_with_no_session_is_503_not_a_signed_in_state(stack):
    _calls(reset=True)
    status, body, headers = _post(APP, "/auth/api-key", EMPTY_SESSION)
    assert status == 503, (
        f"a 200 from the upstream with no session is corruption, not a sign-in — got {status} {body}"
    )
    assert "__Host-session=" not in headers.get("Set-Cookie", ""), (
        f"a session cookie was minted with no session behind it: {headers.get('Set-Cookie')!r}"
    )


def test_store_unavailable_is_503_not_401(nostore):
    _calls(reset=True)
    status, body, _ = _post(NOSTORE, "/auth/api-key", VALID)
    assert status == 503, (
        f"an unavailable session store must be 503, never 401 — got {status} {body}"
    )
    assert json.loads(body)["error"] == "session_store_unavailable", body
    # The store guard runs before the exchange: nothing to sign in to, and the
    # user's key must not be spent on an upstream call.
    assert _calls() == [], "the upstream was contacted before the store was known usable"


def test_missing_api_origin_is_503_not_401(noapi):
    """A missing binding is a DEPLOYMENT fault, never 'invalid key' (#1719)."""
    _calls(reset=True)
    status, body, _ = _post(NOAPI, "/auth/api-key", VALID)
    assert status == 503, f"a missing API_ORIGIN must be 503, got {status} {body}"
    assert status != 401, "a deployment misconfiguration was reported as 'invalid key'"
    assert json.loads(body)["error"] == "provider_unavailable", body
    assert _calls() == [], "the upstream was contacted with no API_ORIGIN configured"


# ---------------------------------------------------------------------------
# 400: malformed input, BEFORE any upstream call
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"api_key": ""},
        {"api_key": "   "},
        {"api_key": None},
        {"api_key": 12345},
        {"api_key": ["tt_x"]},
    ],
)
def test_missing_or_nonstring_key_is_400_and_upstream_is_not_called(stack, payload):
    _calls(reset=True)
    status, body, _ = _post(APP, "/auth/api-key", payload)
    assert status == 400, f"{payload!r} must be 400, got {status} {body}"
    assert json.loads(body)["error"] == "invalid_request", body
    assert _calls() == [], (
        f"the upstream was contacted for a malformed request {payload!r} — validation "
        "must run before the exchange"
    )


# ---------------------------------------------------------------------------
# `next`: honoured same-origin, refused otherwise, never an open redirect
# ---------------------------------------------------------------------------
def test_next_same_origin_path_is_returned(stack):
    _calls(reset=True)
    status, body, headers = _post(APP, "/auth/api-key?next=/settings", VALID)
    assert status == 200, f"{status} {body}"
    assert json.loads(body)["next"] == "/settings", body
    assert "Location" not in headers, headers


@pytest.mark.parametrize(
    "evil",
    [
        "https://evil.example/steal",
        "//evil.example/steal",
        "/..//evil.example/steal",
        "/\t/evil.example",
    ],
)
def test_off_origin_next_is_refused(stack, evil):
    _calls(reset=True)
    status, body, headers = _post(APP, f"/auth/api-key?next={quote(evil, safe='')}", VALID)
    assert status == 200, f"a bad `next` must not fail the sign-in: {status} {body}"
    assert json.loads(body)["next"] is None, f"off-origin `next`={evil!r} was honoured: {body}"
    assert "Location" not in headers, f"the route redirected to {evil!r}"


# ---------------------------------------------------------------------------
# Method discipline
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "OPTIONS"])
def test_non_post_methods_are_405(stack, method):
    _calls(reset=True)
    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(f"{APP}/auth/api-key", method=method)
    try:
        with opener.open(req, timeout=30) as r:
            status, body, headers = r.status, r.read().decode(), _headers(r)
    except urllib.error.HTTPError as e:
        status, body, headers = e.code, e.read().decode(), _headers(e)

    assert status == 405, f"{method} must be 405, got {status} {body}"
    assert json.loads(body)["error"] == "method_not_allowed", body
    assert "POST" in headers.get("Allow", ""), headers.get("Allow")
    assert _calls() == [], f"{method} reached the upstream"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
