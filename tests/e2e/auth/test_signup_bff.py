"""
POST /auth/signup — the BFF email signup route (#4054) and the #801 fix.

WHY THIS SUITE EXISTS
---------------------
The route used to create accounts by calling GoTrue's anon-key
`/auth/v1/signup`, which makes GoTrue send a confirmation email through
Supabase's built-in SMTP. That path is PROJECT-WIDE bucketed (30 sends/hr shared
by ALL users of the project): once the bucket is exhausted EVERY signup from ANY
network 429s (`over_email_send_rate_limit`) and no account is created — the P1
production signup blocker (#801). The canonical path is the hosted API's
`POST /v1/signup/email`, which creates the user server-side via the GoTrue ADMIN
API with `email_confirm=true` and sends NO email. This route now proxies it and
then signs the user in server-side.

Everything asserted here is observed at a surface that can actually disagree:
  - the D1 row is read back with sqlite3, so "was a session created?" is not
    inferred from a 200
  - BOTH upstream mocks record their calls, so "which upstream was contacted?"
    is not inferred from a status code. The GoTrue mock still records
    `/auth/v1/signup`, so the #801 regression guard — ZERO signup calls — is a
    direct observation
  - the raw Set-Cookie header is inspected, so the `__Host-` properties are
    checked rather than assumed

Runs the REAL Cloudflare Pages runtime (`wrangler pages dev` from the dashboard
Pages project root) against two mocks on DIFFERENT origins, mirroring production
(`SUPABASE_URL` != `API_ORIGIN`):
  - mock_email_flows_supabase.mjs  — GoTrue: the password grant + profile
  - mock_signup_api.mjs            — the hosted API: /v1/signup/email

Status discipline under test (the #3485 class): a provider, session-store or
account-creation-follow-up fault is 503 and must NEVER be 400/401 — and once the
API reports `user_created`, the account EXISTS, so a session failure must never
be reported as a refused signup.
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

import pytest
from bff_test_helpers import d1_sqlite_files, pick_free_port, require_toolchain

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MOCK = Path(__file__).resolve().parent / "mock_email_flows_supabase.mjs"
API_MOCK = Path(__file__).resolve().parent / "mock_signup_api.mjs"

APP_PORT = int(os.environ.get("SIGNUP_TEST_APP_PORT", "9040"))
MOCK_PORT = int(os.environ.get("SIGNUP_TEST_MOCK_PORT", "9041"))
API_PORT = int(os.environ.get("SIGNUP_TEST_API_PORT", "9042"))
NOSTORE_PORT = int(os.environ.get("SIGNUP_TEST_NOSTORE_PORT", "9046"))

APP = ""      # assigned in the `stack` fixture
NOSTORE = ""  # assigned in the `nostore` fixture
MOCK_URL = ""  # GoTrue
API_URL = ""   # the hosted API origin

NEW = {"email": "new@example.test", "password": "correct-horse-battery-staple"}
CONFIRM = {"email": "confirm@example.test", "password": "correct-horse-battery-staple"}
EXISTING = {"email": "existing@example.test", "password": "correct-horse-battery-staple"}
WEAK = {"email": "weak@example.test", "password": "correct-horse-battery-staple"}
# The Turnstile token the page adds when a site key is provisioned (#4104).
TURNSTILE = {
    "email": "turnstile@example.test",
    "password": "correct-horse-battery-staple",
    "cf-turnstile-response": "turnstile-token-abc123",
}

# The user id / refresh token the GoTrue mock mints for an address, so tests can
# assert on the D1 row without hardcoding an unrelated literal.
NEW_USER_ID = f"user-{NEW['email']}"


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
        # The hosted API origin is a DIFFERENT binding from SUPABASE_URL — the
        # route must reach the API for account creation, never GoTrue.
        "-b", f"API_ORIGIN={API_URL}",
        "-b", f"APP_ORIGIN=http://127.0.0.1:{port}",
    ]
    return Proc(argv, cwd=str(DASHBOARD_DIR))


@pytest.fixture(scope="module")
def stack():
    # FAIL, do not skip: a skipped security suite is indistinguishable from a
    # passing one in CI (see bff_test_helpers.require_toolchain).
    require_toolchain()

    global APP_PORT, MOCK_PORT, API_PORT, APP, MOCK_URL, API_URL
    claimed: set[int] = set()
    APP_PORT = pick_free_port(APP_PORT, claimed)
    MOCK_PORT = pick_free_port(MOCK_PORT, claimed)
    API_PORT = pick_free_port(API_PORT, claimed)
    assert len({APP_PORT, MOCK_PORT, API_PORT}) == 3
    APP = f"http://127.0.0.1:{APP_PORT}"
    MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"
    API_URL = f"http://127.0.0.1:{API_PORT}"

    mock_env = os.environ.copy()
    mock_env["MOCK_PORT"] = str(MOCK_PORT)
    gt = Proc([shutil.which("node"), str(MOCK)], cwd=str(MOCK.parent), env=mock_env)
    if not _wait(MOCK_PORT):
        gt.stop()
        pytest.fail("mock GoTrue failed to start")

    api_env = os.environ.copy()
    api_env["MOCK_PORT"] = str(API_PORT)
    api = Proc([shutil.which("node"), str(API_MOCK)], cwd=str(API_MOCK.parent), env=api_env)
    if not _wait(API_PORT):
        api.stop()
        gt.stop()
        pytest.fail("mock signup API failed to start")

    app = _start_app(APP_PORT, with_d1=True)
    if not _wait(APP_PORT):
        app.stop()
        api.stop()
        gt.stop()
        pytest.fail("pages dev failed to start")
    time.sleep(2.5)  # let the function bundler finish

    yield {"app": APP, "mock": MOCK_URL, "api": API_URL}
    app.stop()
    api.stop()
    gt.stop()


@pytest.fixture(scope="module")
def nostore(stack):
    """The same route with NO D1 binding — the store-unavailable branch.

    An unbound store is a real misconfiguration, not a contrived state, and its
    contract (503, never 4xx, upstream untouched) is what the #3485 rule turns on.
    """
    global NOSTORE_PORT, NOSTORE
    NOSTORE_PORT = pick_free_port(NOSTORE_PORT, {int(APP_PORT), int(MOCK_PORT), int(API_PORT)})
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


def _post(base: str, path: str, payload: dict | None):
    data = json.dumps(payload if payload is not None else {}).encode()
    req = urllib.request.Request(f"{base}{path}", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=45) as r:
            return r.status, r.read().decode("utf-8", "replace"), _headers(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), _headers(e)


def _mock_post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url, method="POST", data=json.dumps(payload).encode())
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _gt_calls(reset: bool = False) -> list[dict]:
    """GoTrue calls the mock recorded (signup, password, recover, resend)."""
    return _mock_post(f"{MOCK_URL}/__mock/calls", {"reset": reset})["calls"]


def _api_calls(reset: bool = False) -> list[dict]:
    """Hosted-API `/v1/signup/email` calls the mock recorded."""
    return _mock_post(f"{API_URL}/__api/calls", {"reset": reset})["calls"]


def _gt_fault(**kwargs) -> None:
    _mock_post(f"{MOCK_URL}/__mock/fault", kwargs)


def _api_fault(**kwargs) -> None:
    _mock_post(f"{API_URL}/__api/fault", kwargs)


def _reset_upstream():
    _gt_calls(reset=True)
    _api_calls(reset=True)


def _signal_signup_calls() -> list[dict]:
    """Every GoTrue `/auth/v1/signup` call — which MUST be empty (#801)."""
    return [c for c in _gt_calls() if c["flow"] == "signup"]


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


def _session_count(user_id: str) -> int:
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
            ).fetchone()
        except sqlite3.OperationalError:
            return 0  # table absent → no rows
        return row[0] if row else 0
    finally:
        con.close()


def _install_insert_fault() -> None:
    """Make every INSERT into `sessions` abort — a REAL store write fault.

    Used to exercise the "account created, session store failed" branch without
    a test-only hook in the route: the route's own `createSession` call fails
    because the database refuses the write.
    """
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        con.execute(
            "CREATE TRIGGER IF NOT EXISTS test_block_session_insert "
            "BEFORE INSERT ON sessions BEGIN "
            "SELECT RAISE(ABORT, 'injected session store fault'); END;"
        )
        con.commit()
    finally:
        con.close()


def _clear_insert_fault() -> None:
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        con.execute("DROP TRIGGER IF EXISTS test_block_session_insert")
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# THE REGRESSION GUARD: an account is created with ZERO GoTrue /signup calls
# ---------------------------------------------------------------------------
def test_signup_creates_the_account_without_touching_gotrue_signup(stack):
    """#801: the account is created through the API, never GoTrue's SMTP path.

    THIS IS THE POINT OF THE FIX. The GoTrue mock still implements and records
    `/auth/v1/signup`; the route must contact it ZERO times, because that call
    is what burns Supabase's project-wide email bucket and 429s every signup.
    """
    _reset_upstream()

    status, body, _ = _post(APP, "/auth/signup", NEW)
    assert status == 200, f"{status} {body}"

    # The account was created UPSTREAM, at the API origin.
    api_calls = _api_calls()
    assert len(api_calls) == 1, api_calls
    assert api_calls[0]["flow"] == "signup_email", api_calls
    assert api_calls[0]["email"] == NEW["email"], api_calls
    assert api_calls[0]["hasPassword"] is True, api_calls

    # THE GUARD: GoTrue's email-sending signup was NEVER called.
    assert _signal_signup_calls() == [], (
        "the route called GoTrue's anon-key /auth/v1/signup — that is the #801 "
        "P1 email-bucket blocker reintroduced"
    )

    # The one GoTrue call was the server-side password grant, not a signup.
    gt = _gt_calls()
    assert [c["flow"] for c in gt] == ["password"], gt
    assert gt[0]["email"] == NEW["email"], gt
    assert gt[0]["grantType"] == "password", gt


def test_account_created_mints_a_session_and_sets_the_host_cookie(stack):
    _reset_upstream()
    before_ms = int(time.time() * 1000)
    status, body, headers = _post(APP, "/auth/signup", NEW)
    assert status == 200, f"{status} {body}"

    parsed = json.loads(body)
    assert parsed["ok"] is True, body
    assert parsed["confirmationRequired"] is False, body
    assert parsed["user"]["id"] == NEW_USER_ID, body
    assert parsed["user"]["email"] == NEW["email"], body
    assert parsed["user"]["displayName"] == "Mock User", body
    assert isinstance(parsed["expiresAt"], int) and parsed["expiresAt"] > before_ms, body

    set_cookie = headers.get("Set-Cookie", "")
    assert "__Host-session=" in set_cookie, f"session cookie missing: {set_cookie!r}"
    assert "HttpOnly" in set_cookie, f"cookie must be HttpOnly: {set_cookie!r}"
    assert "Secure" in set_cookie, f"__Host- requires Secure: {set_cookie!r}"
    assert "Path=/" in set_cookie, f"__Host- requires Path=/: {set_cookie!r}"
    assert "Domain=" not in set_cookie, f"__Host- forbids Domain: {set_cookie!r}"
    assert "no-store" in headers.get("Cache-Control", ""), headers.get("Cache-Control")

    # The D1 row is the session — read it back rather than trusting the 200.
    handle = _set_cookie_value(set_cookie, "__Host-session")
    assert handle, f"no handle in {set_cookie!r}"
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM sessions WHERE handle = ?", (handle,)).fetchone()
    finally:
        con.close()
    assert row is not None, "no D1 session row was created for the issued cookie"
    assert dict(row)["user_id"] == NEW_USER_ID, dict(row)
    assert dict(row)["refresh_token"], dict(row)
    # No credential material may reach the browser.
    assert "mock-access" not in body and "mock-refresh" not in body, body
    # And no GoTrue signup, still.
    assert _signal_signup_calls() == [], _gt_calls()


# ---------------------------------------------------------------------------
# Turnstile: the token MUST be forwarded (#4104)
# ---------------------------------------------------------------------------
def test_turnstile_token_is_forwarded_to_the_api(stack):
    """`signup.html` adds `cf-turnstile-response`; the route must forward it.

    The hosted API's `_check_turnstile` 400s when `TURNSTILE_SECRET_KEY` is
    configured but the token is absent, so dropping it here would make
    provisioning Turnstile break EVERY signup.
    """
    _reset_upstream()
    status, body, _ = _post(APP, "/auth/signup", TURNSTILE)
    assert status == 200, f"{status} {body}"

    api_calls = _api_calls()
    assert len(api_calls) == 1, api_calls
    assert api_calls[0]["turnstile"] == TURNSTILE["cf-turnstile-response"], (
        "the Turnstile token was DROPPED on the way to the API — provisioning "
        "Turnstile would then break every signup"
    )


def test_signup_without_a_turnstile_token_is_unaffected(stack):
    """A deployment with no Turnstile must not gain a phantom empty field."""
    _reset_upstream()
    status, body, _ = _post(APP, "/auth/signup", NEW)
    assert status == 200, f"{status} {body}"
    api_calls = _api_calls()
    assert len(api_calls) == 1, api_calls
    assert api_calls[0]["turnstile"] is None, (
        f"an absent token must not be forwarded as a key: {api_calls[0]}"
    )


# ---------------------------------------------------------------------------
# CSRF: a forged text/plain form and a cross-origin POST are refused
# ---------------------------------------------------------------------------
def test_signup_text_plain_forged_form_is_415_and_creates_nothing(stack):
    _reset_upstream()
    req = urllib.request.Request(
        f"{APP}/auth/signup",
        method="POST",
        data=json.dumps(TURNSTILE).encode(),
    )
    req.add_header("Content-Type", "text/plain")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=30) as r:
            status, body = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode()
    assert status == 415, f"a text/plain forged signup must be 415, got {status} {body}"
    assert _api_calls() == [], "the API was contacted for a forged form"
    assert _gt_calls() == [], "GoTrue was contacted for a forged form"


def test_signup_cross_origin_json_is_403_and_creates_nothing(stack):
    _reset_upstream()
    req = urllib.request.Request(
        f"{APP}/auth/signup",
        method="POST",
        data=json.dumps(TURNSTILE).encode(),
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Origin", "https://evil.example")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=30) as r:
            status, body = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode()
    assert status == 403, f"a cross-origin signup must be 403, got {status} {body}"
    assert _api_calls() == [], "the API was contacted from a foreign origin"
    assert _gt_calls() == [], "GoTrue was contacted from a foreign origin"


# ---------------------------------------------------------------------------
# The opt-in email funnel (email_confirm:false): account exists, needs confirm
# ---------------------------------------------------------------------------
def test_confirmation_required_signs_nobody_in(stack):
    _reset_upstream()
    status, body, headers = _post(APP, "/auth/signup", CONFIRM)
    assert status == 200, f"an accepted signup must be 200, got {status} {body}"

    parsed = json.loads(body)
    assert parsed["ok"] is True, body
    assert parsed["confirmationRequired"] is True, (
        f"the opt-in funnel (email_confirm:false) must report confirmationRequired — got {body}"
    )
    # A confirmation-required 200 must not carry session material.
    assert "__Host-session=" not in headers.get("Set-Cookie", ""), (
        f"a session cookie was set with no session behind it: {headers.get('Set-Cookie')!r}"
    )
    assert not any("__Host-session" in v for v in headers.values()), headers
    assert "mock-access" not in body and "mock-refresh" not in body, body

    # The API was asked to create the account (with the opt-in email funnel)...
    api_calls = _api_calls()
    assert len(api_calls) == 1 and api_calls[0]["email"] == CONFIRM["email"], api_calls
    # ...and NO session was minted: no password grant, no GoTrue signup.
    assert _gt_calls() == [], _gt_calls()
    # No D1 session row was created for the (unconfirmed) user.
    assert _session_count("api-user-confirm-1") == 0, (
        "a D1 session row was written even though the account needs confirmation"
    )


# ---------------------------------------------------------------------------
# 400: a genuine refusal, with the shape the page's copy is keyed on
# ---------------------------------------------------------------------------
def test_already_registered_is_400_with_the_provider_code(stack):
    _reset_upstream()
    status, body, _ = _post(APP, "/auth/signup", EXISTING)
    assert status == 400, f"a provider refusal must be 400, got {status} {body}"
    parsed = json.loads(body)
    assert parsed["error"] == "rejected", body
    assert parsed["providerError"] == "user_already_exists", body

    calls = _api_calls()
    assert len(calls) == 1 and calls[0]["email"] == EXISTING["email"], calls
    # A refusal short-circuits BEFORE the session path: no sign-in attempted,
    # and GoTrue's signup still never called.
    assert _signal_signup_calls() == [], _gt_calls()
    assert _gt_calls() == [], _gt_calls()
    assert _session_count(f"user-{EXISTING['email']}") == 0


def test_weak_password_refusal_is_400_with_the_upstream_message(stack):
    _reset_upstream()
    status, body, _ = _post(APP, "/auth/signup", WEAK)
    assert status == 400, f"a provider refusal must be 400, got {status} {body}"
    parsed = json.loads(body)
    assert parsed["error"] == "rejected", body
    assert "too weak" in parsed.get("message", ""), body
    assert _signal_signup_calls() == [], _gt_calls()


# ---------------------------------------------------------------------------
# Malformed input: 400 BEFORE any upstream call (both origins untouched)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"password": "x"},
        {"email": "new@example.test"},
        {"email": "", "password": "x"},
        {"email": "new@example.test", "password": ""},
        {"email": "   ", "password": "x"},
        {"email": None, "password": "x"},
        {"email": "new@example.test", "password": None},
        {"email": "not-an-address", "password": "x"},
        {"email": "a@" + "b" * 260 + ".test", "password": "x"},
        {"email": "new@example.test", "password": "p" * 5000},
    ],
)
def test_malformed_input_is_400_and_no_upstream_is_called(stack, payload):
    _reset_upstream()
    status, body, _ = _post(APP, "/auth/signup", payload)
    assert status == 400, f"{payload!r} must be 400, got {status} {body}"
    assert json.loads(body)["error"] in {"invalid_request", "invalid_email"}, body
    assert _api_calls() == [], (
        f"the API was contacted for a malformed request {payload!r} — validation "
        "must run before the upstream call"
    )
    assert _gt_calls() == [], (
        f"GoTrue was contacted for a malformed request {payload!r} — validation "
        "must run before the upstream call"
    )


# ---------------------------------------------------------------------------
# 429: the shared bucket's throttle, forwarded with its Retry-After
# ---------------------------------------------------------------------------
def test_rate_limit_is_429_with_retry_after_preserved(stack):
    _api_fault(signup=429)
    try:
        _reset_upstream()
        status, body, headers = _post(APP, "/auth/signup", NEW)
    finally:
        _api_fault(signup=False)

    assert status == 429, f"a throttle must be 429, got {status} {body}"
    parsed = json.loads(body)
    assert parsed["error"] == "rate_limited", body
    # The page keys its tier-aware lockout copy on the mechanism code.
    assert parsed["providerError"] == "over_request_rate_limit_ip", body
    assert parsed["message"], body
    assert headers.get("Retry-After") == "3600", (
        f"the upstream Retry-After must be preserved, got {headers.get('Retry-After')!r}"
    )
    # A throttle is not a session path, and it is never a GoTrue signup.
    assert _signal_signup_calls() == [], _gt_calls()
    assert _gt_calls() == [], _gt_calls()


# ---------------------------------------------------------------------------
# 503: infrastructure faults are NEVER 4xx (the #3485 rule)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fault", [500, 503, 401, "network"])
def test_upstream_fault_is_503_never_400_or_401(stack, fault):
    """A provider/edge/transport fault is retryable, at every injected shape.

    Status alone would misclassify a 401 (`Invalid API key`, a wrong key) as a
    refused signup — only classifying by the failure separates them. "network"
    drops the connection so the transport branch is exercised, not a status.
    """
    _api_fault(signup=fault)
    try:
        _reset_upstream()
        status, body, _ = _post(APP, "/auth/signup", NEW)
    finally:
        _api_fault(signup=False)

    assert status == 503, f"fault {fault!r} must be 503, got {status} {body}"
    assert status not in (400, 401), f"fault {fault!r} was reported as a refusal"
    assert json.loads(body)["error"] == "provider_unavailable", body
    assert _signal_signup_calls() == [], _gt_calls()


def test_store_unavailable_is_503_not_400(nostore):
    _reset_upstream()
    status, body, _ = _post(NOSTORE, "/auth/signup", NEW)
    assert status == 503, (
        f"an unavailable session store must be 503, never 400 — got {status} {body}"
    )
    assert json.loads(body)["error"] == "session_store_unavailable", body
    # The store guard runs before ANY upstream call.
    assert _api_calls() == [], "the API was contacted before the store was known usable"
    assert _gt_calls() == [], "GoTrue was contacted before the store was known usable"


# ---------------------------------------------------------------------------
# Account created, session NOT established — never reported as a refusal
# ---------------------------------------------------------------------------
def test_signin_infrastructure_fault_reports_account_created(stack):
    """The account exists; only the follow-up sign-in failed. Never a refusal."""
    _gt_fault(password=500)
    before = _session_count(NEW_USER_ID)
    try:
        _reset_upstream()
        status, body, _ = _post(APP, "/auth/signup", NEW)
    finally:
        _gt_fault(password=False)

    assert status == 503, f"the follow-up fault must be 503, got {status} {body}"
    parsed = json.loads(body)
    assert parsed["error"] == "session_unavailable", body
    assert parsed["accountCreated"] is True, (
        f"the route must say the ACCOUNT was created, not that signup was refused: {body}"
    )
    assert parsed["confirmationRequired"] is False, body
    assert "created" in parsed.get("message", "").lower(), body
    # The account really was created upstream.
    assert any(c["flow"] == "signup_email" for c in _api_calls()), _api_calls()
    # And this request wrote no new session row (earlier tests' rows are fixed).
    assert _session_count(NEW_USER_ID) == before


def test_session_store_fault_after_creation_is_503_not_refused(stack):
    """The account exists and the sign-in succeeded, but the D1 write failed.

    A real store fault (an INSERT-blocking trigger), so the route's
    `createSession` rejects — it must answer 503 and say the account was
    created, never "your signup was refused".
    """
    # Warm the schema with a normal signup so the trigger has a table to attach
    # to (this request also proves the healthy path immediately before the fault).
    warm = {"email": "warm@example.test", "password": NEW["password"]}
    status, _body, _ = _post(APP, "/auth/signup", warm)
    assert status == 200, f"warm-up signup failed: {status} {_body}"

    _install_insert_fault()
    before = _session_count(NEW_USER_ID)
    try:
        _reset_upstream()
        status, body, _ = _post(APP, "/auth/signup", NEW)
    finally:
        _clear_insert_fault()

    assert status == 503, f"a store write fault must be 503, got {status} {body}"
    parsed = json.loads(body)
    assert parsed["error"] == "session_store_unavailable", body
    assert parsed["accountCreated"] is True, (
        f"the store fault must not be reported as a refused signup: {body}"
    )
    assert parsed["error"] != "rejected", body
    # The account WAS created and the sign-in DID run before the store failed.
    assert any(c["flow"] == "signup_email" for c in _api_calls()), _api_calls()
    assert any(c["flow"] == "password" for c in _gt_calls()), _gt_calls()
    assert _signal_signup_calls() == [], _gt_calls()
    # The fault request created no new row (the write was refused).
    assert _session_count(NEW_USER_ID) == before


# ---------------------------------------------------------------------------
# Method discipline (onRequestPost → Cloudflare answers 405 for non-POST
# methods; a GET falls through to the Pages SPA shell, exactly like the sibling
# `onRequestPost` routes. The property that matters is that no non-POST request
# ever becomes an upstream call.)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["PUT", "DELETE", "OPTIONS"])
def test_non_post_methods_are_405(stack, method):
    _reset_upstream()
    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(f"{APP}/auth/signup", method=method)
    try:
        with opener.open(req, timeout=30) as r:
            status = r.status
            r.read()
    except urllib.error.HTTPError as e:
        status = e.code
        e.read()

    assert status == 405, f"{method} must be 405, got {status}"
    assert _api_calls() == [], f"{method} reached the API"
    assert _gt_calls() == [], f"{method} reached GoTrue"


def test_get_does_not_reach_upstream(stack):
    """A GET is served the SPA shell by Pages, never a signup attempt.

    `onRequestPost` leaves GET unmatched, so Pages falls through to the site
    asset. Asserting both upstream logs are empty is what makes this meaningful:
    a stray GET can never create an account.
    """
    _reset_upstream()
    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(f"{APP}/auth/signup", method="GET")
    try:
        with opener.open(req, timeout=30) as r:
            r.read()
    except urllib.error.HTTPError as e:
        e.read()
    assert _api_calls() == [], "a GET reached the API"
    assert _gt_calls() == [], "a GET reached GoTrue"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
