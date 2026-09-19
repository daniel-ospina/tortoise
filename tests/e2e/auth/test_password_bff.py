"""
POST /auth/password — the BFF email+password sign-in route (#4054).

Runs the REAL Cloudflare Pages runtime (`wrangler pages dev` from the dashboard
Pages project root) against a focused mock GoTrue that validates the password
grant. The mock is focused rather than the shared one because the shared
`mock_supabase.mjs` answers the password grant with an unconditional 200: it
cannot show a 401, cannot show that GoTrue was NOT contacted for a malformed
request, and cannot inject a provider outage.

Everything asserted here is observed at a surface that can actually disagree:
  - the D1 row is read back with sqlite3, so "was a session created?" is not
    inferred from a 200
  - the upstream request log is read from the mock, so "was GoTrue called?" is
    not inferred from a status code
  - the raw Set-Cookie header is inspected, so the `__Host-` properties are
    checked rather than assumed

Status discipline under test (the #3485 class): a provider or store fault is
503 and must NEVER be 401.
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
# The BFF moved to the DASHBOARD Pages project (issue #4054): `functions/` sits
# beside the site dir, so `wrangler pages dev .` runs from there.
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MOCK = Path(__file__).resolve().parent / "mock_password_supabase.mjs"

APP_PORT = int(os.environ.get("PASSWORD_TEST_APP_PORT", "9030"))
MOCK_PORT = int(os.environ.get("PASSWORD_TEST_MOCK_PORT", "9031"))
NOSTORE_PORT = int(os.environ.get("PASSWORD_TEST_NOSTORE_PORT", "9032"))

APP = ""       # assigned in the `stack` fixture
NOSTORE = ""   # assigned in the `nostore` fixture
MOCK_URL = ""

VALID = {"email": "known@example.test", "password": "correct-horse-battery-staple"}
WRONG = {"email": "known@example.test", "password": "definitely-not-the-password"}
UNKNOWN = {"email": "nobody@example.test", "password": "definitely-not-the-password"}


# ---------------------------------------------------------------------------
# Process + request helpers
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


def _start_app(port: int, with_d1: bool) -> Proc:
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
        pytest.fail("mock GoTrue failed to start")

    app = _start_app(APP_PORT, with_d1=True)
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
    """The same route with NO D1 binding — the store-unavailable branch.

    A store OUTAGE cannot be induced from outside, but the branch is the same
    one, and its contract (503, never 401) is what the #3485 rule turns on. An
    unbound store is a real misconfiguration, not a contrived state.
    """
    global NOSTORE_PORT, NOSTORE
    NOSTORE_PORT = pick_free_port(NOSTORE_PORT, {int(APP_PORT), int(MOCK_PORT)})
    NOSTORE = f"http://127.0.0.1:{NOSTORE_PORT}"
    app = _start_app(NOSTORE_PORT, with_d1=False)
    if not _wait(NOSTORE_PORT):
        app.stop()
        pytest.fail("pages dev (no D1) failed to start")
    time.sleep(2.5)
    yield {"app": NOSTORE}
    app.stop()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _headers(r) -> dict:
    """Headers as a plain dict, PRESERVING duplicates (Set-Cookie)."""
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


def _get(base: str, path: str):
    req = urllib.request.Request(f"{base}{path}", method="GET")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=45) as r:
            return r.status, r.read().decode("utf-8", "replace"), _headers(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), _headers(e)


def _calls(reset: bool = False) -> list[dict]:
    data = json.dumps({"reset": reset}).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/password-calls", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())["calls"]


def _fault(**kwargs) -> None:
    req = urllib.request.Request(
        f"{MOCK_URL}/__mock/fault", method="POST", data=json.dumps(kwargs).encode(),
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def _set_cookie_value(header: str, name: str) -> str | None:
    for part in header.split(","):
        part = part.strip()
        if part.startswith(f"{name}="):
            return part[len(name) + 1:].split(";")[0]
    return None


def _d1_sqlite() -> Path:
    """Newest local D1 database file, waiting for wrangler to create it."""
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
# 401: bad credentials, no account-existence oracle
# ---------------------------------------------------------------------------
def test_wrong_password_is_401(stack):
    _calls(reset=True)
    status, body, _ = _post(APP, "/auth/password", WRONG)
    assert status == 401, f"a wrong password must be 401, got {status} {body}"
    parsed = json.loads(body)
    assert parsed["error"] == "invalid_credentials", body
    # The credential reached GoTrue; the 401 is the provider's verdict, not the
    # route short-circuiting.
    calls = _calls()
    assert len(calls) == 1, f"expected exactly one password grant, got {calls}"
    assert calls[0]["email"] == WRONG["email"], calls
    assert calls[0]["hasPassword"] is True, calls
    assert calls[0]["accepted"] is False, calls


def test_unknown_email_is_indistinguishable_from_a_wrong_password(stack):
    """The non-enumeration property, asserted as an EQUALITY of responses.

    If a known address and an unknown one ever produced different bytes, the
    route would be an account-existence oracle.
    """
    _calls(reset=True)
    s_known, b_known, _ = _post(APP, "/auth/password", WRONG)
    s_unknown, b_unknown, _ = _post(APP, "/auth/password", UNKNOWN)

    assert s_known == 401 and s_unknown == 401, f"{s_known} / {s_unknown}"
    assert b_known == b_unknown, (
        "a wrong password and an unknown email produced different responses — "
        f"the route leaks whether an account exists:\n{b_known}\n{b_unknown}"
    )
    assert UNKNOWN["email"] not in b_unknown, (
        "the response echoed the submitted address, which makes it an oracle "
        "once the client renders it"
    )
    # Both reached GoTrue, so the equality is not an artifact of short-circuiting.
    calls = _calls()
    assert len(calls) == 2, f"expected two password grants, got {calls}"
    assert {c["email"] for c in calls} == {WRONG["email"], UNKNOWN["email"]}, calls
    assert all(c["accepted"] is False for c in calls), calls


# ---------------------------------------------------------------------------
# 200: session row + __Host-session cookie + the /api/session shape
# ---------------------------------------------------------------------------
def test_valid_credentials_mint_a_session_and_set_the_host_cookie(stack):
    _calls(reset=True)
    before_ms = int(time.time() * 1000)
    status, body, headers = _post(APP, "/auth/password", VALID)
    assert status == 200, f"valid credentials must be 200, got {status} {body}"

    parsed = json.loads(body)
    # The shape /api/session returns and the client reuses.
    assert parsed["user"]["id"] == "user-pw-1", body
    assert parsed["user"]["email"] == VALID["email"], body
    assert parsed["user"]["displayName"] == "Known User", body
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
    assert row["user_id"] == "user-pw-1", row
    assert row["revoked"] == 0, row
    assert row["refresh_token"], row
    assert row["expires_at"] > before_ms, row

    # No credential material may reach the browser.
    assert "mock-access" not in body and "mock-refresh" not in body, (
        f"a server-held token leaked into the response: {body}"
    )
    assert "no-store" in headers.get("Cache-Control", ""), headers.get("Cache-Control")


# ---------------------------------------------------------------------------
# A malformed 200 grant is infrastructure, never a 500 (#4104)
# ---------------------------------------------------------------------------
def test_a_malformed_200_grant_is_503_not_a_500(stack):
    """A 200 with no usable token shape must be 503.

    `call()` accepts ANY 2xx as `{ok:true, data}`, so dereferencing
    `result.data.user.id` / `.refresh_token` on a malformed body throws a
    TypeError — an infrastructure fault surfacing as an unhandled 500 instead of
    this route's declared 503. `/auth/signup` and `/auth/api-key` already guard
    the shape; this test pins the same guard here.
    """
    _fault(malformed=True)
    try:
        _calls(reset=True)
        status, body, _ = _post(APP, "/auth/password", VALID)
    finally:
        _fault(malformed=False)

    assert status == 503, (
        f"a malformed 200 grant must surface as 503, got {status} {body} — a 500 "
        "means the route dereferenced an unvalidated token shape"
    )
    assert status != 500, "an upstream body shape error became an unhandled 500"
    assert json.loads(body)["error"] == "provider_unavailable", body
    # The grant WAS attempted, so the branch is genuinely exercised.
    assert _calls(), "the provider was never reached"


# ---------------------------------------------------------------------------
# 400: malformed input, BEFORE any upstream call
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"password": "x"},
        {"email": "known@example.test"},
        {"email": "", "password": "x"},
        {"email": "known@example.test", "password": ""},
        {"email": "   ", "password": "x"},
        {"email": None, "password": "x"},
        {"email": "known@example.test", "password": None},
    ],
)
def test_missing_credentials_are_400_and_gotrue_is_not_called(stack, payload):
    _calls(reset=True)
    status, body, _ = _post(APP, "/auth/password", payload)
    assert status == 400, f"{payload!r} must be 400, got {status} {body}"
    assert json.loads(body)["error"] == "invalid_request", body
    assert _calls() == [], (
        f"GoTrue was contacted for a malformed request {payload!r} — validation "
        "must run before the upstream call"
    )


# ---------------------------------------------------------------------------
# 503: infrastructure faults are NEVER 401 (the #3485 rule)
# ---------------------------------------------------------------------------
def test_provider_outage_is_503_not_401(stack):
    _fault(passwordGrant=True)
    try:
        _calls(reset=True)
        status, body, _ = _post(APP, "/auth/password", VALID)
    finally:
        _fault(passwordGrant=False)

    assert status == 503, (
        f"a provider outage must be 503, never 401 — got {status} {body}"
    )
    assert status != 401, "a provider fault was reported as 'not signed in' (#3485)"
    assert json.loads(body)["error"] == "provider_unavailable", body
    assert _calls(), "the provider was never actually reached, so the outage path was not exercised"


def test_store_unavailable_is_503_not_401(nostore):
    _calls(reset=True)
    status, body, _ = _post(NOSTORE, "/auth/password", VALID)
    assert status == 503, (
        f"an unavailable session store must be 503, never 401 — got {status} {body}"
    )
    assert status != 401, "a store fault was reported as 'not signed in' (#3485)"
    assert json.loads(body)["error"] == "session_store_unavailable", body
    # The store guard runs before the credential exchange: nothing to sign in to.
    assert _calls() == [], "GoTrue was contacted before the session store was known to be usable"


# ---------------------------------------------------------------------------
# `next`: honoured same-origin, refused otherwise, never an open redirect
# ---------------------------------------------------------------------------
def test_next_same_origin_path_is_returned(stack):
    _calls(reset=True)
    status, body, headers = _post(APP, "/auth/password?next=/settings/profile", VALID)
    assert status == 200, f"{status} {body}"
    assert json.loads(body)["next"] == "/settings/profile", body
    # It is data, not a redirect — this route must not emit a Location at all.
    assert "Location" not in headers, headers


@pytest.mark.parametrize(
    "evil",
    [
        "https://evil.example/steal",
        "http://evil.example/steal",
        "//evil.example/steal",
        "/..//evil.example/steal",
        "/\t/evil.example",
        "https://evil.example",
        # A different port on the same host is a DIFFERENT origin.
        "http://127.0.0.1:1/steal",
    ],
)
def test_off_origin_next_is_refused(stack, evil):
    _calls(reset=True)
    status, body, headers = _post(
        APP, f"/auth/password?next={quote(evil, safe='')}", VALID
    )
    assert status == 200, f"a bad `next` must not fail the sign-in: {status} {body}"
    assert json.loads(body)["next"] is None, (
        f"off-origin `next`={evil!r} was honoured: {body}"
    )
    assert "Location" not in headers, f"the route redirected to {evil!r}"


def test_absent_next_is_null(stack):
    status, body, _ = _post(APP, "/auth/password", VALID)
    assert status == 200
    assert json.loads(body)["next"] is None, body


# ---------------------------------------------------------------------------
# Method discipline
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "OPTIONS"])
def test_non_post_methods_are_405(stack, method):
    _calls(reset=True)
    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(f"{APP}/auth/password", method=method)
    try:
        with opener.open(req, timeout=30) as r:
            status, body, headers = r.status, r.read().decode(), _headers(r)
    except urllib.error.HTTPError as e:
        status, body, headers = e.code, e.read().decode(), _headers(e)

    assert status == 405, f"{method} must be 405, got {status} {body}"
    assert json.loads(body)["error"] == "method_not_allowed", body
    assert "POST" in headers.get("Allow", ""), headers.get("Allow")
    assert _calls() == [], f"{method} reached GoTrue"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
