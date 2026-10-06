"""
The shared CSRF / login-CSRF guard on the state-changing BFF routes (#4104).

WHY THIS SUITE EXISTS
---------------------
The session-ISSUING routes (`/auth/password`, `/auth/signup`, `/auth/api-key`)
need no cookie to work — they are how you GET a session — so `SameSite=Lax`
protects nothing. An attacker page can auto-submit

    <form action="https://app.premiselabs.co/auth/password" method="POST"
          enctype="text/plain">
      <input name='{"email":"attacker@example.com","password":"hunter2","x":"' value='"}'>
    </form>

which produces a body that IS valid JSON, and the route would answer 200 with a
session issued INTO the victim's browser (login CSRF / session fixation).

The guard is TWO layers, applied through ONE shared helper
(`functions/_shared/auth/csrf.ts`):
  1. Content-Type must be `application/json` (an HTML form cannot send it) → 415.
  2. `Origin`, when present, must be the app origin → 403.

Every assertion below is observed at a surface that can disagree: the upstream
mock RECORDS each call, so "GoTrue was not contacted" is observed rather than
inferred from a status code. The negative cases assert the upstream log is
empty, which is what makes "refused BEFORE the body reached GoTrue" real.

Runs the REAL Cloudflare Pages runtime against the focused email-flows mock,
which implements and records the password grant, `/recover` and `/resend`.
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

APP_PORT = int(os.environ.get("CSRF_TEST_APP_PORT", "9050"))
MOCK_PORT = int(os.environ.get("CSRF_TEST_MOCK_PORT", "9051"))

APP = ""      # assigned in the `stack` fixture
MOCK_URL = ""

# The attacker's forged body, valid JSON with an extra field — exactly the shape
# an `enctype=text/plain` form produces.
ATTACK = {
    "email": "attacker@example.test",
    "password": "attacker-chosen-password",
    "x": "=",
}
VALID = {"email": "known@example.test", "password": "correct-horse-battery-staple"}
EVIL_ORIGIN = "https://evil.example"


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

    app = Proc(
        [
            shutil.which("wrangler"), "pages", "dev", "dist",
            "--port", str(APP_PORT), "--ip", "127.0.0.1",
            "--d1", "SESSIONS",
            "-b", f"SUPABASE_URL={MOCK_URL}",
            "-b", "SUPABASE_ANON_KEY=mock-anon-key",
            "-b", f"APP_ORIGIN={APP}",
        ],
        cwd=str(DASHBOARD_DIR),
    )
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


# `...` distinguishes "do not send the header" from an explicit value.
_UNSET = object()


def _post(path: str, payload: dict | None, *, content_type=_UNSET, origin=_UNSET):
    data = json.dumps(payload if payload is not None else {}).encode()
    req = urllib.request.Request(f"{APP}{path}", method="POST", data=data)
    if content_type is not _UNSET:
        req.add_header("Content-Type", content_type)
    if origin is not _UNSET:
        req.add_header("Origin", origin)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=45) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


def _calls(reset: bool = True) -> list[dict]:
    data = json.dumps({"reset": reset}).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/calls", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())["calls"]


# ---------------------------------------------------------------------------
# LAYER 1 — Content-Type: an HTML form cannot send application/json
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path,payload",
    [
        ("/auth/password", ATTACK),
        ("/auth/reset", {"email": ATTACK["email"]}),
        ("/auth/resend", {"email": ATTACK["email"]}),
    ],
)
def test_text_plain_forged_form_is_415_and_upstream_untouched(stack, path, payload):
    """The attack shape: valid JSON, but an HTML form's media type.

    This is THE regression test for the login-CSRF vector. The body parses as
    JSON — so input validation alone would not stop it — but `text/plain` is the
    only encoding an HTML form can produce here, so the media-type gate refuses
    it before the body is read and before any session can be issued.
    """
    _calls()
    status, body, _ = _post(path, payload, content_type="text/plain")
    assert status == 415, f"{path} accepted a text/plain forged form: {status} {body}"
    assert json.loads(body)["error"] == "unsupported_media_type", body
    assert _calls() == [], (
        f"{path} reached the upstream despite a text/plain Content-Type — the "
        "forged-form vector is live"
    )


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/auth/password", VALID),
        ("/auth/reset", {"email": VALID["email"]}),
        ("/auth/resend", {"email": VALID["email"]}),
    ],
)
def test_missing_content_type_is_415(stack, path, payload):
    """A missing Content-Type is not a JSON client; refuse it too.

    Allowing an absent media type would let a form POST through by merely
    omitting the header.
    """
    _calls()
    status, body, _ = _post(path, payload)  # no Content-Type header at all
    assert status == 415, f"{path} accepted a request with no Content-Type: {status} {body}"
    assert _calls() == [], f"{path} reached the upstream with no Content-Type"


def test_form_urlencoded_is_415(stack):
    _calls()
    status, body, _ = _post(
        "/auth/password", ATTACK, content_type="application/x-www-form-urlencoded"
    )
    assert status == 415, f"a form-urlencoded body must be 415, got {status} {body}"
    assert _calls() == []


# ---------------------------------------------------------------------------
# LAYER 2 — Origin: a cross-origin JSON POST is refused
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path,payload",
    [
        ("/auth/password", ATTACK),
        ("/auth/reset", {"email": ATTACK["email"]}),
        ("/auth/resend", {"email": ATTACK["email"]}),
    ],
)
def test_cross_origin_json_post_is_403_and_upstream_untouched(stack, path, payload):
    _calls()
    status, body, _ = _post(
        path, payload, content_type="application/json", origin=EVIL_ORIGIN
    )
    assert status == 403, f"{path} accepted a cross-origin request: {status} {body}"
    assert json.loads(body)["error"] == "forbidden_origin", body
    assert _calls() == [], f"{path} reached the upstream from {EVIL_ORIGIN}"


# ---------------------------------------------------------------------------
# The allowed callers: same-origin JSON, and a non-browser (absent Origin)
# ---------------------------------------------------------------------------
def test_same_origin_json_is_allowed(stack):
    """The legitimate browser caller must still pass — a guard that blocks
    everything is not a guard, it is an outage."""
    _calls()
    status, body, _ = _post(
        "/auth/password", VALID, content_type="application/json", origin=APP
    )
    assert status == 200, f"same-origin JSON sign-in must be allowed: {status} {body}"
    # The upstream WAS reached, so the negative tests' empty log is meaningful.
    assert [c["flow"] for c in _calls(reset=False)] == ["password"], _calls(reset=False)


def test_absent_origin_is_allowed_for_server_side_callers(stack):
    """A browser cross-site form post always carries an Origin; an absent one
    cannot be one, and genuinely server-side/test callers omit it."""
    _calls()
    status, body, _ = _post("/auth/password", VALID, content_type="application/json")
    assert status == 200, f"an origin-less JSON caller must be allowed: {status} {body}"
    assert [c["flow"] for c in _calls(reset=False)] == ["password"], _calls(reset=False)


def test_charset_parameter_is_accepted(stack):
    """`application/json; charset=utf-8` is a legitimate JSON client."""
    status, _body, _ = _post(
        "/auth/password", VALID, content_type="application/json; charset=utf-8"
    )
    assert status == 200, f"a charset parameter must not be refused, got {status}"


# ---------------------------------------------------------------------------
# EVERY guarded route is wired — the shared helper cannot be half-applied
# ---------------------------------------------------------------------------
# A route that forgets to call the guard is indistinguishable from a guarded one
# until a forged request reaches it. These cases assert the guard bites at each
# call site, so a future refactor that drops one call site reddens here.
GUARDED_ROUTES = [
    ("POST", "/auth/password", {"email": "a@b.test", "password": "x"}),
    ("POST", "/auth/signup", {"email": "a@b.test", "password": "x"}),
    ("POST", "/auth/api-key", {"api_key": "tt_forged"}),
    ("POST", "/auth/reset", {"email": "a@b.test"}),
    ("POST", "/auth/resend", {"email": "a@b.test"}),
    ("POST", "/auth/set-email", {"email": "a@b.test"}),
    ("POST", "/auth/update-password", {"password": "Abcdefg1!"}),
    ("POST", "/auth/link", None),
    ("POST", "/api/profile", {"displayName": "x"}),
    ("PATCH", "/api/profile", {"displayName": "x"}),
]


@pytest.mark.parametrize("method,path,payload", GUARDED_ROUTES)
def test_every_guarded_route_rejects_a_text_plain_body(stack, method, path, payload):
    """415 before the body is parsed, at each call site.

    These routes need either a session or no session at all; the guard runs
    FIRST, so the refusal does not depend on the route's auth state.
    """
    data = json.dumps(payload if payload is not None else {}).encode()
    req = urllib.request.Request(f"{APP}{path}", method=method, data=data)
    req.add_header("Content-Type", "text/plain")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=30) as r:
            status, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode("utf-8", "replace")
    assert status == 415, (
        f"{method} {path} must refuse a text/plain body with 415 (the guard is "
        f"missing at this call site), got {status} {body}"
    )


@pytest.mark.parametrize("method,path,payload", GUARDED_ROUTES)
def test_every_guarded_route_rejects_a_cross_origin_request(stack, method, path, payload):
    data = json.dumps(payload if payload is not None else {}).encode()
    req = urllib.request.Request(f"{APP}{path}", method=method, data=data)
    req.add_header("Content-Type", "application/json")
    req.add_header("Origin", EVIL_ORIGIN)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=30) as r:
            status, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode("utf-8", "replace")
    assert status == 403, (
        f"{method} {path} must refuse a request from {EVIL_ORIGIN} with 403 (the "
        f"guard is missing at this call site), got {status} {body}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
