"""
POST /auth/resend — the BFF resend-confirmation route (#4054).

Runs the REAL Cloudflare Pages runtime (`wrangler pages dev` from the dashboard
Pages project root) against a focused mock GoTrue that emulates `/resend`.

The property under test is NON-ENUMERATION, asserted as an EQUALITY of responses
across addresses GoTrue treats differently (a normal address, answered 200, and
`refused@example.test`, answered 422). The route also must pin `type:"signup"` so
it cannot be asked to resend any other flow — that is read off the mock's request
log, not assumed.

Status discipline under test (the #3485 class): a provider/config fault is 503
and must never be folded into the 200.
"""
from __future__ import annotations

import contextlib
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
from bff_test_helpers import pick_free_port, require_toolchain

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MOCK = Path(__file__).resolve().parent / "mock_email_flows_supabase.mjs"

APP_PORT = int(os.environ.get("RESEND_TEST_APP_PORT", "9044"))
MOCK_PORT = int(os.environ.get("RESEND_TEST_MOCK_PORT", "9045"))

APP = ""       # assigned in the `stack` fixture
MOCK_URL = ""

KNOWN = {"email": "known@example.test"}
UNKNOWN = {"email": "nobody@example.test"}
REFUSED = {"email": "refused@example.test"}  # mock answers 422 user_not_found
# The mock answers 429 for this address ONLY — GoTrue's `over_email_send_rate_limit`
# is per ADDRESS, so the route must not let it distinguish accounts.
RATELIMITED = {"email": "ratelimited@example.test"}


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


def _start_app(port: int) -> Proc:
    argv = [
        shutil.which("wrangler"), "pages", "dev", "dist",
        "--port", str(port), "--ip", "127.0.0.1",
        "-b", f"SUPABASE_URL={MOCK_URL}",
        "-b", "SUPABASE_ANON_KEY=mock-anon-key",
        "-b", f"APP_ORIGIN=http://127.0.0.1:{port}",
    ]
    return Proc(argv, cwd=str(DASHBOARD_DIR))


@pytest.fixture(scope="module")
def stack():
    # FAIL, do not skip: a skipped security suite is indistinguishable from a
    # passing one in CI.
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

    app = _start_app(APP_PORT)
    if not _wait(APP_PORT):
        app.stop()
        mock.stop()
        pytest.fail("pages dev failed to start")
    time.sleep(2.5)

    yield {"app": APP, "mock": MOCK_URL}
    app.stop()
    mock.stop()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _headers(r) -> dict:
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


def _calls(reset: bool = False) -> list[dict]:
    data = json.dumps({"reset": reset}).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/calls", method="POST", data=data)
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


# ---------------------------------------------------------------------------
# 200: the confirmation mail is re-requested, type pinned, confirm redirect
# ---------------------------------------------------------------------------
def test_valid_email_requests_a_signup_confirmation_with_the_confirm_redirect(stack):
    _calls(reset=True)
    status, body, headers = _post(APP, "/auth/resend", KNOWN)
    assert status == 200, f"a valid address must be 200, got {status} {body}"
    assert json.loads(body) == {"ok": True}, body

    calls = _calls()
    assert len(calls) == 1, f"expected exactly one /resend call, got {calls}"
    assert calls[0]["flow"] == "resend", calls
    assert calls[0]["email"] == KNOWN["email"], calls
    # `type` is pinned to signup by the shared primitive — this route cannot be
    # asked to resend another flow.
    assert calls[0]["type"] == "signup", calls
    assert calls[0]["redirectTo"] == f"{APP}/auth/confirm", calls
    assert calls[0]["authorization"] == "present", calls
    assert "no-store" in headers.get("Cache-Control", ""), headers.get("Cache-Control")


# ---------------------------------------------------------------------------
# NON-ENUMERATION: different addresses, IDENTICAL responses
# ---------------------------------------------------------------------------
def test_known_and_unknown_addresses_are_indistinguishable(stack):
    _calls(reset=True)
    s_known, b_known, _ = _post(APP, "/auth/resend", KNOWN)
    s_unknown, b_unknown, _ = _post(APP, "/auth/resend", UNKNOWN)

    assert s_known == 200 and s_unknown == 200, f"{s_known} / {s_unknown}"
    assert b_known == b_unknown, (
        "a known and an unknown address produced different responses — the route "
        f"leaks whether an account exists:\n{b_known}\n{b_unknown}"
    )
    assert UNKNOWN["email"] not in b_unknown, (
        "the response echoed the submitted address, which makes it an oracle once "
        "the client renders it"
    )
    calls = _calls()
    assert len(calls) == 2, f"expected two /resend calls, got {calls}"
    assert {c["email"] for c in calls} == {KNOWN["email"], UNKNOWN["email"]}, calls


def test_a_provider_refusal_is_still_an_indistinguishable_200(stack):
    """GoTrue refuses a syntactically valid address with 422 — still a flat 200.

    This is the assertion that fails the moment the route propagates a provider
    refusal, which would make the status itself the oracle.
    """
    _calls(reset=True)
    s_normal, b_normal, _ = _post(APP, "/auth/resend", KNOWN)
    s_refused, b_refused, _ = _post(APP, "/auth/resend", REFUSED)

    assert s_refused == 200, (
        f"a provider refusal must be folded into the enumeration-safe 200, got {s_refused} {b_refused}"
    )
    assert s_normal == 200, f"the normal address must also be 200, got {s_normal} {b_normal}"
    assert b_refused == b_normal, (
        "a provider refusal produced a different body than an accepted request — "
        f"that difference is the oracle:\n{b_normal}\n{b_refused}"
    )
    calls = _calls()
    assert {c["email"] for c in calls} == {KNOWN["email"], REFUSED["email"]}, calls


# ---------------------------------------------------------------------------
# 400: malformed input, BEFORE any upstream call
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"email": ""},
        {"email": "   "},
        {"email": None},
        {"email": 123},
        {"email": "not-an-address"},
        {"email": "a@" + "b" * 260 + ".test"},
    ],
)
def test_malformed_input_is_400_and_gotrue_is_not_called(stack, payload):
    _calls(reset=True)
    status, body, _ = _post(APP, "/auth/resend", payload)
    assert status == 400, f"{payload!r} must be 400, got {status} {body}"
    assert json.loads(body)["error"] in {"invalid_request", "invalid_email"}, body
    assert _calls() == [], (
        f"GoTrue was contacted for a malformed request {payload!r} — validation "
        "must run before the upstream call"
    )


def test_a_per_address_provider_429_is_an_indistinguishable_200(stack):
    """A per-ADDRESS provider 429 must not leak existence (#4104).

    GoTrue's `over_email_send_rate_limit` applies to the address being mailed,
    so a 503 would make repeated resends for a KNOWN address differ from an
    UNKNOWN one — an account-existence oracle this route promises not to be.
    The mock answers 429 for `ratelimited@example.test` ONLY.
    """
    _calls(reset=True)
    s_normal, b_normal, _ = _post(APP, "/auth/resend", KNOWN)
    s_limited, b_limited, _ = _post(APP, "/auth/resend", RATELIMITED)

    assert s_limited == 200, (
        f"a per-address provider 429 must be folded into the enumeration-safe "
        f"200, got {s_limited} {b_limited}"
    )
    assert s_normal == 200, f"the normal address must also be 200, got {s_normal} {b_normal}"
    assert b_limited == b_normal, (
        "a rate-limited address produced a different body than a normal one — "
        f"that difference is the oracle:\n{b_normal}\n{b_limited}"
    )
    calls = _calls()
    assert {c["email"] for c in calls} == {KNOWN["email"], RATELIMITED["email"]}, calls


# ---------------------------------------------------------------------------
# 503: infrastructure faults surface honestly (and still reveal nothing)
# ---------------------------------------------------------------------------
def test_provider_outage_is_503(stack):
    _fault(resend=500)
    try:
        _calls(reset=True)
        status, body, _ = _post(APP, "/auth/resend", KNOWN)
    finally:
        _fault(resend=False)

    assert status == 503, f"a provider outage must be 503, got {status} {body}"
    assert json.loads(body)["error"] == "provider_unavailable", body
    assert _calls(), "the provider was never reached, so the outage path was not exercised"


def test_config_fault_401_is_503(stack):
    """A wrong anon key (401) is infrastructure — it must NOT be reported as 200."""
    _fault(resend=401)
    try:
        _calls(reset=True)
        status, body, _ = _post(APP, "/auth/resend", KNOWN)
    finally:
        _fault(resend=False)

    assert status == 503, f"a config fault must be 503, got {status} {body}"
    assert status != 200, "a wrong anon key was reported as an accepted resend request"
    assert json.loads(body)["error"] == "provider_unavailable", body
    assert _calls(), "the provider was never reached"


# ---------------------------------------------------------------------------
# Method discipline (onRequestPost → 405 for non-GET methods; GET falls through
# to the Pages SPA shell, like the sibling routes. No non-POST reaches GoTrue.)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["PUT", "DELETE", "OPTIONS"])
def test_non_post_methods_are_405(stack, method):
    _calls(reset=True)
    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(f"{APP}/auth/resend", method=method)
    try:
        with opener.open(req, timeout=30) as r:
            status = r.status
            r.read()
    except urllib.error.HTTPError as e:
        status = e.code
        e.read()

    assert status == 405, f"{method} must be 405, got {status}"
    assert _calls() == [], f"{method} reached GoTrue"


def test_get_does_not_reach_gotrue(stack):
    """A GET is served the SPA shell by Pages, never a resend request."""
    _calls(reset=True)
    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(f"{APP}/auth/resend", method="GET")
    try:
        with opener.open(req, timeout=30) as r:
            r.read()
    except urllib.error.HTTPError as e:
        e.read()
    assert _calls() == [], "a GET reached GoTrue"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
