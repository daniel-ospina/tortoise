"""
Clickthrough verification for BFF profile + email change (#4054).

Routes under test (both resolve the BFF session server-side from the opaque
`__Host-session` handle; for that session the browser holds no token):
  GET/POST/PATCH /api/profile   — read the profile; update the display name
  POST           /auth/set-email — request an email change via GoTrue

HARNESS
-------
Modelled on `tests/e2e/auth/test_link_flow.py` — i.e. it runs `wrangler pages
dev` with cwd `website/apps/dashboard`, because the auth Functions were moved
there (the same change #4054 carries; the older `test_bff_flow.py` /
`test_welcome_and_password.py` still point at `website/`, where the Functions no
longer live). It seeds the session row directly into local D1, and it uses its
OWN focused mock (`mock_profile_supabase.mjs`) because the shared
`mock_supabase.mjs` does not record what a caller sent to `PUT /auth/v1/user`,
so "was the SERVER-held token attached?" and "what was written?" would be
unobservable. `test_link_flow.py` did the same for the same reason.

WHY THE ASSERTIONS ARE SHAPED THIS WAY
  - "GoTrue was NOT called" is read off the mock's own request log, not inferred
    from a 400.
  - "the SERVER-held token was used" is proven by seeding a distinctive cached
    token AND sending a conflicting client `Authorization` header, then asserting
    which one the upstream observed. If the route forwarded the client header (or
    the anon key), this fails.
  - The email-change assertions cover BOTH outcomes: a request that GoTrue leaves
    PENDING (double_confirm_changes=true) must not be reported as `changed`, and
    one the mock applies immediately must be. A route that hardcoded either value
    fails one of the two.
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
from bff_test_helpers import require_toolchain

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MIGRATION = REPO_ROOT / "website" / "migrations" / "0001_auth_sessions.sql"
MOCK = Path(__file__).resolve().parent / "mock_profile_supabase.mjs"

# Outside every range the sibling suites use (8790-8801, 8970-8971, 8980-8981,
# 8997-8998).
APP_PORT = int(os.environ.get("PROFILE_TEST_APP_PORT", "8990"))
MOCK_PORT = int(os.environ.get("PROFILE_TEST_MOCK_PORT", "8991"))
APP = f"http://127.0.0.1:{APP_PORT}"
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"

SEED_HANDLE = "a" * 64
SEED_USER = "user-123"
# Deliberately distinctive: the mock records the exact Authorization header, so
# the test can prove THIS value reached GoTrue and a client-supplied one did not.
SEED_TOKEN = "seed-access-token"

# Set by the `stack` fixture to this suite's isolated D1 persist directory, so a
# concurrent sibling suite in the same worktree cannot be read from or written to.
PERSIST: Path | None = None


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
    """The local D1 DATABASE file in this suite's isolated persist dir.

    Not simply "the newest *.sqlite": the persist tree also holds
    `metadata.sqlite` files (D1's index, plus the cache/observability stores), and
    picking one of those seeds a database the Worker never reads — which presents
    as a route 503 (`ALTER TABLE sessions` on a DB with no such table), not as a
    seeding error. The database itself is the non-`metadata.sqlite` file under
    `**/d1/**`.
    """
    assert PERSIST is not None, "stack fixture must run first"
    deadline = time.time() + 30
    while time.time() < deadline:
        files = [
            p
            for p in PERSIST.glob("**/d1/**/*.sqlite")
            if p.name != "metadata.sqlite"
        ]
        if files:
            return max(files, key=lambda p: p.stat().st_mtime)
        time.sleep(0.3)
    raise RuntimeError(f"no D1 database sqlite appeared under {PERSIST}")


def _seed_session(handle: str, user_id: str, access_token: str) -> None:
    """Create the schema and a live session row with a cached access token.

    The cached token means `getAccessTokenForSession` returns it without a refresh
    grant, keeping this mock focused on `/auth/v1/user` (and making the exact
    bearer the route forwards an observable, deterministic value).
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
                access_token, now + 3_600_000,
            ),
        )
        con.commit()
    finally:
        con.close()


def _session_access_token(handle: str) -> str | None:
    """Read the D1 `access_token` for a handle — evidence a helper ran on it."""
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        row = con.execute(
            "SELECT access_token FROM sessions WHERE handle = ?", (handle,)
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    # FAIL, do not skip: a skipped security suite is indistinguishable from a
    # passing one in CI.
    require_toolchain()
    node = shutil.which("node")
    wrangler = shutil.which("wrangler")

    global APP_PORT, MOCK_PORT, APP, MOCK_URL, PERSIST
    claimed: set[int] = set()
    APP_PORT = _pick_free_port(APP_PORT, claimed)
    MOCK_PORT = _pick_free_port(MOCK_PORT, claimed)
    assert APP_PORT != MOCK_PORT
    APP = f"http://127.0.0.1:{APP_PORT}"
    MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"
    PERSIST = tmp_path_factory.mktemp("profile-bff-d1")

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
    # `--persist-to` gives this suite its own D1 so a concurrent sibling suite
    # sharing DASHBOARD_DIR cannot cross-contaminate it.
    app = Proc(
        [
            wrangler, "pages", "dev", "dist",
            "--port", str(APP_PORT), "--ip", "127.0.0.1",
            "--d1", "SESSIONS",
            "--persist-to", str(PERSIST),
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

    # The local D1 database file is created LAZILY, on the first D1 access, so
    # warm it with a request that reads the store before seeding into it. The
    # unknown handle makes the route answer 401; the file is what we need.
    _request("/api/session", cookie="__Host-session=" + "0" * 64)
    _seed_session(SEED_HANDLE, SEED_USER, SEED_TOKEN)

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
    h: dict[str, str] = {}
    for k, v in r.headers.items():
        h[k] = f"{h[k]}, {v}" if k in h else v
    return h


def _request(
    path: str,
    method: str = "GET",
    cookie: str | None = None,
    body: dict | None = None,
    extra_headers: dict | None = None,
):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{APP}{path}", method=method, data=data)
    if cookie:
        req.add_header("Cookie", cookie)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (extra_headers or {}).items():
        req.add_header(k, v)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace"), _headers(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), _headers(e)


def _calls(reset: bool = False) -> list[dict]:
    data = json.dumps({"reset": reset}).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/calls", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())["calls"]


def _fault(**kwargs) -> dict:
    data = json.dumps(kwargs).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/fault", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _state(reset: bool = False) -> dict:
    data = json.dumps({"reset": reset}).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/state", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


AUTH_COOKIE = f"__Host-session={SEED_HANDLE}"


@pytest.fixture(autouse=True)
def _fresh(stack):
    """Reset mock state/faults and re-seed the session before every test.

    Re-seeding matters because the upstream-401 test legitimately NULLs the
    cached `access_token` (that is `invalidateCachedToken` working). Without a
    fresh row the next test would try to refresh against a mock that has no
    token endpoint, and fail for the wrong reason.
    """
    _seed_session(SEED_HANDLE, SEED_USER, SEED_TOKEN)
    _state(reset=True)
    _fault(outage=False, unauthorized=False, rateLimit=False, immediateEmailChange=False, reject=None)
    _calls(reset=True)
    yield


# ---------------------------------------------------------------------------
# /auth/set-email
# ---------------------------------------------------------------------------
def test_set_email_without_session_is_401_and_gotrue_untouched(stack):
    status, body, _ = _request("/auth/set-email", method="POST", body={"email": "new@example.test"})
    assert status == 401, f"anonymous must be 401, got {status} {body}"
    assert json.loads(body)["error"] == "not_signed_in", body
    assert _calls() == [], "GoTrue must not be contacted without a session"


def test_set_email_uses_the_server_held_token(stack):
    """The BFF must attach the SESSION's token — never a client-supplied one."""
    status, body, _ = _request(
        "/auth/set-email",
        method="POST",
        cookie=AUTH_COOKIE,
        body={"email": "new@example.test"},
        # A hostile client-supplied credential. It must NOT win.
        extra_headers={"Authorization": "Bearer attacker-token"},
    )
    assert status == 200, f"expected the request to be accepted, got {status} {body}"

    writes = [c for c in _calls() if c["method"] == "PUT"]
    assert len(writes) == 1, f"expected exactly one GoTrue write, got {writes}"
    assert writes[0]["authorization"] == f"Bearer {SEED_TOKEN}", (
        "the write must carry the SERVER-held session token; "
        f"got {writes[0]['authorization']!r}"
    )
    assert "attacker" not in (writes[0]["authorization"] or ""), (
        "a client-supplied Authorization header must never reach GoTrue"
    )
    assert writes[0]["body"] == {"email": "new@example.test"}, writes[0]["body"]


def test_set_email_pending_change_is_reported_honestly(stack):
    """`double_confirm_changes=true` -> the change is PENDING, not done.

    The mock returns the user with `email` unchanged and `new_email` set, exactly
    as GoTrue does. The route must NOT report that as a completed change.
    """
    status, body, _ = _request(
        "/auth/set-email", method="POST", cookie=AUTH_COOKIE,
        body={"email": "pending@example.test"},
    )
    payload = json.loads(body)
    assert status == 200, f"the request should be ACCEPTED (pending), got {status} {body}"
    assert payload["ok"] is True
    assert payload["changed"] is False, f"an unconfirmed change must not claim `changed`: {payload}"
    assert payload["pending"] is True, payload
    assert payload["pendingEmail"] == "pending@example.test", payload
    assert payload["email"] == "user-123@example.test", (
        f"the CURRENT email is still the old one until confirmation: {payload}"
    )


def test_set_email_immediate_change_is_reported_as_changed(stack):
    """The other side of the line: when GoTrue DOES apply it, say so.

    Proves `pending` is derived from the upstream response, not hardcoded.
    """
    _fault(immediateEmailChange=True)
    status, body, _ = _request(
        "/auth/set-email", method="POST", cookie=AUTH_COOKIE,
        body={"email": "instant@example.test"},
    )
    payload = json.loads(body)
    assert status == 200, f"got {status} {body}"
    assert payload["changed"] is True, payload
    assert payload["pending"] is False, payload
    assert payload["email"] == "instant@example.test", payload
    assert payload["pendingEmail"] is None, payload


def test_set_email_invalid_address_is_400_before_gotrue(stack):
    for bad in ("", "   ", "nope", "a@b", "x" * 300 + "@example.test"):
        status, body, _ = _request(
            "/auth/set-email", method="POST", cookie=AUTH_COOKIE, body={"email": bad}
        )
        assert status == 400, f"email={bad!r} must be 400, got {status} {body}"
        assert json.loads(body)["error"] == "invalid_email", body
    assert _calls() == [], "GoTrue was called for an invalid address"


def test_set_email_provider_rejection_is_not_a_fake_200(stack):
    """A provider 4xx (`email_exists`) must surface as a refusal, never a 200."""
    _fault(reject="email_exists")
    status, body, _ = _request(
        "/auth/set-email", method="POST", cookie=AUTH_COOKIE,
        body={"email": "taken@example.test"},
    )
    payload = json.loads(body)
    assert status == 400, f"a rejected change must be 400, got {status} {body}"
    assert payload["error"] == "rejected", payload
    assert payload["status"] == 422, payload
    assert payload["providerError"] == "email_exists", payload


def test_set_email_provider_outage_is_503_not_200(stack):
    _fault(outage=True)
    status, body, _ = _request(
        "/auth/set-email", method="POST", cookie=AUTH_COOKIE,
        body={"email": "new@example.test"},
    )
    assert status == 503, f"a provider outage must be 503, got {status} {body}"
    assert json.loads(body)["error"] == "provider_unavailable", body


def test_set_email_upstream_401_is_503_and_invalidates_the_cached_token(stack):
    """An upstream 401 is NOT our 401: the session is live, the credential is not.

    Reporting our 401 would sign the user out over a rejected credential (the
    #3485 class). The cached token must also be invalidated so a retry refreshes.
    """
    _fault(unauthorized=True)
    status, body, headers = _request(
        "/auth/set-email", method="POST", cookie=AUTH_COOKIE,
        body={"email": "new@example.test"},
    )
    assert status == 503, f"an upstream 401 must NOT be our 401, got {status} {body}"
    assert json.loads(body)["error"] == "upstream_unauthorized", body
    assert "Set-Cookie" not in headers, (
        f"the session must not be cleared for a rejected credential: {headers}"
    )
    # Direct evidence that invalidateCachedToken ran, not an inference from 503.
    assert _session_access_token(SEED_HANDLE) is None, (
        "the cached access token must be invalidated so the retry refreshes it"
    )


# ---------------------------------------------------------------------------
# GET /api/profile — shape parity with /api/session
# ---------------------------------------------------------------------------
def test_profile_get_without_session_is_401_and_gotrue_untouched(stack):
    status, body, _ = _request("/api/profile")
    assert status == 401, f"anonymous must be 401, got {status} {body}"
    assert json.loads(body)["error"] == "not_signed_in", body
    assert _calls() == [], "GoTrue must not be contacted without a session"


def test_profile_get_shape_matches_api_session(stack):
    """The two endpoints must describe the SAME user in the SAME shape.

    The requirement is parity, so the assertion compares the two live responses
    field by field rather than hardcoding a shape in one place.
    """
    s_prof, b_prof, _ = _request("/api/profile", cookie=AUTH_COOKIE)
    s_sess, b_sess, _ = _request("/api/session", cookie=AUTH_COOKIE)
    assert s_prof == 200, f"/api/profile should be 200, got {s_prof} {b_prof}"
    assert s_sess == 200, f"/api/session should be 200, got {s_sess} {b_sess}"

    prof, sess = json.loads(b_prof), json.loads(b_sess)
    assert set(prof.keys()) == {"user", "expiresAt"}, prof
    assert prof["user"] == sess["user"], (
        f"/api/profile and /api/session disagree about the user: {prof['user']} vs {sess['user']}"
    )
    assert prof["expiresAt"] == sess["expiresAt"], (prof["expiresAt"], sess["expiresAt"])
    assert set(prof["user"].keys()) == {"id", "email", "displayName"}, prof["user"]
    assert prof["user"]["id"] == SEED_USER
    assert prof["user"]["email"] == "user-123@example.test"
    assert prof["user"]["displayName"] == "Display user-123"


def test_profile_get_degrades_on_lookup_failure(stack):
    """A cosmetic lookup must not sign the user out; identity degrades to id-only."""
    _fault(outage=True)
    status, body, _ = _request("/api/profile", cookie=AUTH_COOKIE)
    assert status == 200, f"a profile outage must not sign the user out, got {status} {body}"
    user = json.loads(body)["user"]
    assert user["id"] == SEED_USER
    assert user.get("email") is None, user
    assert user.get("displayName") is None, user


# ---------------------------------------------------------------------------
# POST/PATCH /api/profile — display name
# ---------------------------------------------------------------------------
def test_profile_update_without_session_is_401_and_gotrue_untouched(stack):
    status, body, _ = _request(
        "/api/profile", method="PATCH", body={"displayName": "Ada Lovelace"}
    )
    assert status == 401, f"anonymous must be 401, got {status} {body}"
    assert json.loads(body)["error"] == "not_signed_in", body
    assert _calls() == [], "GoTrue must not be contacted without a session"


def test_profile_patch_sets_display_name_and_returns_the_session_shape(stack):
    status, body, _ = _request(
        "/api/profile", method="PATCH", cookie=AUTH_COOKIE,
        body={"displayName": "Ada Lovelace"},
        extra_headers={"Authorization": "Bearer attacker-token"},
    )
    payload = json.loads(body)
    assert status == 200, f"got {status} {body}"
    assert set(payload.keys()) == {"user", "expiresAt"}, payload
    assert set(payload["user"].keys()) == {"id", "email", "displayName"}, payload["user"]
    assert payload["user"]["displayName"] == "Ada Lovelace", payload["user"]
    assert payload["user"]["id"] == SEED_USER, payload["user"]

    writes = [c for c in _calls() if c["method"] == "PUT"]
    assert len(writes) == 1, f"expected exactly one GoTrue write, got {writes}"
    assert writes[0]["authorization"] == f"Bearer {SEED_TOKEN}", writes[0]["authorization"]
    assert writes[0]["body"] == {"data": {"display_name": "Ada Lovelace"}}, writes[0]["body"]


def test_profile_post_is_an_alias_for_the_update(stack):
    status, body, _ = _request(
        "/api/profile", method="POST", cookie=AUTH_COOKIE,
        body={"displayName": "Grace Hopper"},
    )
    assert status == 200, f"got {status} {body}"
    assert json.loads(body)["user"]["displayName"] == "Grace Hopper", body


def test_profile_update_rejects_invalid_display_name_before_gotrue(stack):
    for bad in ("", "   ", None, 5, "x" * 81, "bad\u0000name"):
        status, body, _ = _request(
            "/api/profile", method="PATCH", cookie=AUTH_COOKIE, body={"displayName": bad}
        )
        assert status == 400, f"displayName={bad!r} must be 400, got {status} {body}"
        assert json.loads(body)["error"] == "invalid_display_name", body
    assert _calls() == [], "GoTrue was called for an invalid display name"


def test_profile_update_provider_outage_is_503_not_200(stack):
    _fault(outage=True)
    status, body, _ = _request(
        "/api/profile", method="PATCH", cookie=AUTH_COOKIE, body={"displayName": "Ada"}
    )
    assert status == 503, f"a provider outage must be 503, got {status} {body}"
    assert json.loads(body)["error"] == "provider_unavailable", body


def test_profile_update_provider_rejection_is_400_not_200(stack):
    _fault(reject="validation_failed")
    status, body, _ = _request(
        "/api/profile", method="PATCH", cookie=AUTH_COOKIE, body={"displayName": "Ada"}
    )
    assert status == 400, f"a provider rejection must be 400, got {status} {body}"
    assert json.loads(body)["error"] == "rejected", body


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
