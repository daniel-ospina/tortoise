"""
Clickthrough verification for /auth/link — the BFF link-identity flow (#4054).

Runs the REAL Cloudflare Pages runtime (`wrangler pages dev`) against a mock
Supabase that emulates GoTrue's AUTHENTICATED link-identity endpoint. Nothing
that matters is stubbed in a way that hides a defect:
  - the link endpoint requires a Bearer token, so "did /auth/link attach the
    session's credential?" is observed, not assumed
  - the PKCE verifier is recomputed from the persisted D1 row and compared to
    the challenge the mock received, so "was the flow row written?" is proven
  - every upstream request is recorded, so "GoTrue was NOT called" is read off
    the upstream's own request log rather than inferred from a 400

Harness is modelled on tests/e2e/auth/test_bff_flow.py. It points at
`website/apps/dashboard` because the auth Functions were moved there (the same
change #4054 carries), and the shared `mock_supabase.mjs` does not implement the
link endpoint — so this suite brings its own focused mock rather than editing
the shared one.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
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
from bff_test_helpers import d1_sqlite_files, require_toolchain

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MIGRATION = REPO_ROOT / "website" / "migrations" / "0001_auth_sessions.sql"
MOCK = Path(__file__).resolve().parent / "mock_link_supabase.mjs"

# Outside the ranges other suites use (8790-8801 and 8970-8971).
APP_PORT = int(os.environ.get("LINK_TEST_APP_PORT", "8980"))
MOCK_PORT = int(os.environ.get("LINK_TEST_MOCK_PORT", "8981"))
APP = f"http://127.0.0.1:{APP_PORT}"
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"

SEED_HANDLE = "a" * 64
SEED_USER = "user-123"


def _pick_free_port(start: int, taken: set[int]) -> int:
    for port in range(start, start + 100):
        if port in taken:
            continue
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                taken.add(port)
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free port in {start}..{start + 100}")


def _wait(port: int, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.4)
    raise RuntimeError(f"port {port} never opened")


class Proc:
    def __init__(self, argv, cwd, env=None):
        self.p = subprocess.Popen(
            argv, cwd=cwd, env=env or os.environ.copy(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
        )

    def stop(self):
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(self.p.pid), signal.SIGTERM)


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


def _seed_session(handle: str, user_id: str) -> None:
    """Create the schema and a live session row directly in local D1.

    A real session is expensive to obtain here (it needs the full sign-in walk
    and a sign-in mock); the `/auth/link` contract only needs a live session
    row, so it is seeded. `access_token` is pre-cached so the request never
    needs a refresh grant, keeping the mock focused on the link endpoint.
    """
    db = _d1_sqlite()
    now = int(time.time() * 1000)
    con = sqlite3.connect(db, timeout=15)
    try:
        con.executescript(MIGRATION.read_text(encoding="utf-8"))
        con.execute(
            "INSERT OR REPLACE INTO sessions "
            "(handle,user_id,refresh_token,revoked,created_at,expires_at,"
            " access_token,access_token_expires_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                handle, user_id, "seed-refresh-token", 0, now, now + 86_400_000,
                "seed-access-token", now + 3_600_000,
            ),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture(scope="module")
def stack():
    # FAIL, do not skip: a skipped security suite is indistinguishable from a
    # passing one in CI.
    require_toolchain()
    node = shutil.which("node")
    wrangler = shutil.which("wrangler")

    global APP_PORT, MOCK_PORT, APP, MOCK_URL
    claimed: set[int] = set()
    APP_PORT = _pick_free_port(APP_PORT, claimed)
    MOCK_PORT = _pick_free_port(MOCK_PORT, claimed)
    assert APP_PORT != MOCK_PORT
    APP = f"http://127.0.0.1:{APP_PORT}"
    MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"

    mock_env = os.environ.copy()
    mock_env["MOCK_PORT"] = str(MOCK_PORT)
    mock = Proc([node, str(MOCK)], cwd=str(MOCK.parent), env=mock_env)
    try:
        _wait(MOCK_PORT)
    except RuntimeError:
        mock.stop()
        pytest.fail("mock failed to start")

    # `wrangler pages dev <dir>` from the repo root does not discover
    # `<dir>/functions`, so cwd is the site dir and the argv is ".".
    app = Proc(
        [
            wrangler, "pages", "dev", "dist",
            "--port", str(APP_PORT), "--ip", "127.0.0.1",
            "--d1", "SESSIONS",
            "-b", f"SUPABASE_URL={MOCK_URL}",
            "-b", "SUPABASE_ANON_KEY=mock-anon-key",
            "-b", f"AUTH_CALLBACK_URL={APP}/auth/callback",
            "-b", f"APP_ORIGIN={APP}",
        ],
        cwd=str(DASHBOARD_DIR),
    )
    try:
        _wait(APP_PORT)
    except RuntimeError:
        app.stop()
        mock.stop()
        out = b""
        with contextlib.suppress(Exception):
            out = app.p.stdout.read() if app.p.stdout else b""
        pytest.fail(f"pages dev failed to start; output:\n{out.decode('utf-8', 'replace')[-3000:]}")
    time.sleep(2.0)  # let the function bundler finish

    _seed_session(SEED_HANDLE, SEED_USER)

    yield {"app": APP, "mock": MOCK_URL}
    app.stop()
    mock.stop()


# ---------------------------------------------------------------------------
# Request + observation helpers
# ---------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _headers(r) -> dict:
    """Headers as a plain dict, PRESERVING duplicate values (Set-Cookie)."""
    h: dict[str, str] = {}
    for k, v in r.headers.items():
        h[k] = f"{h[k]}, {v}" if k in h else v
    return h


def _get(url: str, cookie: str | None = None, follow: bool = False):
    req = urllib.request.Request(url, method="GET")
    if cookie:
        req.add_header("Cookie", cookie)
    handler = urllib.request.HTTPRedirectHandler() if follow else _NoRedirect()
    opener = urllib.request.build_opener(handler)
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace"), _headers(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), _headers(e)


def _link_calls(reset: bool = False) -> list[dict]:
    data = json.dumps({"reset": reset}).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/link-calls", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())["calls"]


def _set_cookie_value(header: str, name: str) -> str | None:
    for part in header.split(","):
        part = part.strip()
        if part.startswith(f"{name}="):
            return part[len(name) + 1:].split(";")[0]
    return None


def _flow_row(flow_id: str) -> dict | None:
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT flow_id, verifier, kind, user_id, next, created_at, expires_at "
            "FROM auth_flows WHERE flow_id = ?",
            (flow_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


AUTH_COOKIE = f"__Host-session={SEED_HANDLE}"


# ---------------------------------------------------------------------------
# Contract: no session -> 401 with the shared error shape, and GoTrue untouched
# ---------------------------------------------------------------------------
def test_link_without_session_is_401(stack):
    _link_calls(reset=True)
    status, body, _ = _get(f"{APP}/auth/link?provider=github")
    assert status == 401, f"anonymous must be 401, got {status} {body}"
    assert json.loads(body)["error"] == "not_signed_in", body
    assert _link_calls() == [], "GoTrue must not be contacted without a session"


# ---------------------------------------------------------------------------
# Contract: valid session + allowed provider -> 302 into GoTrue's link flow,
# PKCE S256, and a persisted, verifier-bearing flow row
# ---------------------------------------------------------------------------
def test_link_github_redirects_into_gotrue_with_s256_and_writes_flow(stack):
    _link_calls(reset=True)
    status, body, headers = _get(f"{APP}/auth/link?provider=github", cookie=AUTH_COOKIE, follow=False)
    assert status == 302, f"expected a redirect into the link flow, got {status} {body}"

    location = headers.get("Location", "")
    assert MOCK_URL in location, f"the 302 must go to GoTrue's returned link flow: {location!r}"
    assert "code_challenge_method=s256" in location, (
        f"the link flow must be PKCE S256, got: {location!r}"
    )

    set_cookie = headers.get("Set-Cookie", "")
    assert "__Host-authflow=" in set_cookie, f"flow cookie missing: {set_cookie!r}"
    assert "HttpOnly" in set_cookie, "flow cookie must be HttpOnly"
    assert "Secure" in set_cookie, "__Host- requires Secure"
    assert "Path=/" in set_cookie, "__Host- requires Path=/"
    assert "Domain=" not in set_cookie, "__Host- must not carry Domain"

    # Observed at the upstream, not inferred: exactly one authenticated call,
    # with the right provider and the S256 parameters.
    calls = _link_calls()
    assert len(calls) == 1, f"expected exactly one GoTrue link call, got {calls}"
    call = calls[0]
    assert call["provider"] == "github", call
    assert call["code_challenge_method"] == "s256", call
    assert call["code_challenge"], f"PKCE challenge missing: {call}"
    assert call["redirect_to"] == f"{APP}/auth/callback", call
    assert call["skip_http_redirect"] == "true", (
        f"the server-side call must ask GoTrue for the URL, not a browser hop: {call}"
    )
    assert call["authorization"] == "present", (
        "the link call MUST carry the session's credential — this is the user "
        f"binding at initiation: {call}"
    )

    flow_id = _set_cookie_value(set_cookie, "__Host-authflow")
    assert flow_id, f"could not read the flow id from {set_cookie!r}"
    row = _flow_row(flow_id)
    assert row is not None, "no auth_flows row was written"
    assert row["kind"] == "link", f"flow kind must be link, got {row}"
    # The verifier is SERVER-held and must match the challenge GoTrue received.
    assert row["verifier"], row
    assert _s256(row["verifier"]) == call["code_challenge"], (
        "the persisted verifier does not hash to the challenge sent to GoTrue"
    )


def test_link_google_also_allowed(stack):
    _link_calls(reset=True)
    status, _, _ = _get(f"{APP}/auth/link?provider=google", cookie=AUTH_COOKIE, follow=False)
    assert status == 302, f"google must be allowed, got {status}"
    calls = _link_calls()
    assert len(calls) == 1 and calls[0]["provider"] == "google", calls


# ---------------------------------------------------------------------------
# Contract: absent/unsupported provider -> 400 BEFORE GoTrue, no flow minted
# ---------------------------------------------------------------------------
def test_link_absent_provider_is_400_and_gotrue_untouched(stack):
    _link_calls(reset=True)
    status, body, headers = _get(f"{APP}/auth/link", cookie=AUTH_COOKIE, follow=False)
    assert status == 400, f"absent provider must be 400, got {status} {body}"
    assert json.loads(body)["error"] == "unsupported_provider", body
    assert "__Host-authflow=" not in headers.get("Set-Cookie", ""), "a rejected provider minted a flow"
    assert _link_calls() == [], "GoTrue was called for an absent provider"


@pytest.mark.parametrize(
    "bad",
    [
        "RANDOM",
        "<script>alert(1)</script>",
        "",
        "GITHUB",
        "github2",
        "github,google",
        "github ",
        "github&redirect_to=https://evil.example",
        # `email` is a valid /auth/start provider but is NOT linkable here.
        "email",
    ],
)
def test_link_rejects_invalid_provider_with_400(stack, bad):
    _link_calls(reset=True)
    status, body, headers = _get(
        f"{APP}/auth/link?provider={quote(bad, safe='')}", cookie=AUTH_COOKIE, follow=False
    )
    assert status == 400, f"provider={bad!r} must be 400, got {status} {body}"
    assert json.loads(body)["error"] == "unsupported_provider", body
    assert "__Host-authflow=" not in headers.get("Set-Cookie", ""), (
        f"a rejected provider minted a flow: {headers.get('Set-Cookie')!r}"
    )
    assert "Location" not in headers, f"a rejected provider redirected: {headers}"
    assert _link_calls() == [], f"GoTrue was called for a rejected provider {bad!r}"


# ---------------------------------------------------------------------------
# Contract: the flow is bound to the CURRENT session's user
#
# What the code ACTUALLY enforces, asserted here:
#   (1) the server-side GoTrue call carries the session's credential, so the
#       user is fixed at initiation by GoTrue; and
#   (2) the flow row records the session's user_id.
#
# What is NOT enforced, and is reported rather than tested around:
#   /auth/callback does not read auth_flows.user_id, so the D1-side binding is
#   RECORDED, not ENFORCED. The cross-account guarantee rests on (1).
# ---------------------------------------------------------------------------
def test_link_flow_is_bound_to_the_current_session_user(stack):
    # Two distinct users, each with their own session. If the binding were a
    # constant (or absent), the two flow rows could not carry different users.
    handle_a, user_a = "b" * 64, "user-aaa"
    handle_b, user_b = "c" * 64, "user-bbb"
    _seed_session(handle_a, user_a)
    _seed_session(handle_b, user_b)

    _link_calls(reset=True)
    status_a, _, headers_a = _get(
        f"{APP}/auth/link?provider=github", cookie=f"__Host-session={handle_a}", follow=False
    )
    status_b, _, headers_b = _get(
        f"{APP}/auth/link?provider=github", cookie=f"__Host-session={handle_b}", follow=False
    )
    assert status_a == 302 and status_b == 302, f"{status_a} / {status_b}"

    flow_a = _set_cookie_value(headers_a.get("Set-Cookie", ""), "__Host-authflow")
    flow_b = _set_cookie_value(headers_b.get("Set-Cookie", ""), "__Host-authflow")
    assert flow_a and flow_b and flow_a != flow_b, "each request must mint its own flow"

    row_a = _flow_row(flow_a)
    row_b = _flow_row(flow_b)
    assert row_a is not None and row_b is not None
    assert row_a["user_id"] == user_a, (
        f"flow for session A must bind to {user_a!r}, got {row_a['user_id']!r}"
    )
    assert row_b["user_id"] == user_b, (
        f"flow for session B must bind to {user_b!r}, got {row_b['user_id']!r}"
    )

    # The initiation binding: both upstream calls carried a credential, and the
    # user is decided by whatever that credential identifies.
    calls = _link_calls()
    assert len(calls) == 2, calls
    assert all(c["authorization"] == "present" for c in calls), calls


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
