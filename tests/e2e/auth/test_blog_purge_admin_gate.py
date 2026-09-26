"""
/blog/api/purge — the admin gate on the blog edge-cache purge route (#1865).

WHY THIS SUITE IS SEPARATE FROM test_bff_flow.py
------------------------------------------------
Issue #4054 moved the auth/BFF Functions OUT of `website/functions/` (the
`premise-labs` Pages project, tortoise.premiselabs.co) INTO
`website/apps/dashboard/functions/` (the `tortoise-dashboard` project,
app.premiselabs.co). The blog Functions — `/blog/api/*`, `/blog/*`, the feed and
sitemap — deliberately STAYED in `website/functions/`.

A single `wrangler pages dev` serves one `functions/` tree beside one site dir;
it cannot serve both trees at once. These cases need `/blog/api/purge`, so this
suite keeps a `website/`-rooted server, while `test_bff_flow.py` runs the
`website/apps/dashboard`-rooted server for the moved `/auth/*` and `/api/*`
routes. Before the split they shared one website-rooted server, which is exactly
why the moved routes 404'd and these cases could not be exercised at all once
the fixture was repointed.

The assertions are the ones from test_bff_flow.py, unchanged:
  - a provider outage on the bearer path is 503, never 401 (the #3485 class)
  - a genuine non-admin bearer is 401
  - an admin bearer passes the gate and then fails on its own bad input (400)
  - a dead BFF cookie must NOT fall back to a legacy bearer (F15)
  - a malformed cookie must not 500 on this route

WHY THE FIXTURE SEEDS THE SCHEMA
--------------------------------
`requireAdmin` resolves a PRESENTED `__Host-session` against the D1 `sessions`
table. With no table at all the lookup throws, which `resolveSession` reports as
`unavailable` — the route then answers 503, and the dead-cookie case would be
asserting the wrong branch (it failed for exactly this reason when this suite was
first split out). Applying the auth migration (schema only, zero rows) leaves a
table with no matching row, which is precisely the "presented but resolved to
nothing" state those tests are about. This is setup, not a weakened assertion:
the property under test is untouched.
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
# The blog Functions stayed in `website/` (the `premise-labs` project), so this
# server is rooted there — `functions/` must sit beside the site directory.
WEBSITE_DIR = REPO_ROOT / "website"
MIGRATION = WEBSITE_DIR / "migrations" / "0001_auth_sessions.sql"
MOCK = REPO_ROOT / "tests" / "e2e" / "auth" / "mock_supabase.mjs"

# Distinct from every other auth suite's range (8790-8801, 8970-8971,
# 8980-8981, 8995-8998, 9002-9004) so parallel collection cannot collide.
APP_PORT = int(os.environ.get("AUTH_BLOG_APP_PORT", "9005"))
MOCK_PORT = int(os.environ.get("AUTH_BLOG_MOCK_PORT", "9006"))
APP = f"http://127.0.0.1:{APP_PORT}"
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"


def _wait(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def _d1_files() -> list[Path]:
    return [
        p
        for p in WEBSITE_DIR.glob(".wrangler/state/v3/d1/**/*.sqlite")
        if p.name != "metadata.sqlite"
    ]


def _warm_d1() -> None:
    """Force Miniflare to MATERIALISE the bound D1 database file.

    `--d1 SESSIONS` only declares the binding: the SQLite file appears on the
    first D1 ACCESS, not at boot. Seeding the schema first (the obvious order)
    therefore times out on a clean CI runner and passes only where an earlier
    run already created the file. A purge request carrying an unknown
    `__Host-session` handle IS a D1 read — `requireAdmin` resolves the cookie
    against `sessions` before it reaches the purge — and is refused.
    """
    deadline = time.time() + 30
    while time.time() < deadline:
        req = urllib.request.Request(f"{APP}/blog/api/purge", method="POST", data=b"{}")
        req.add_header("Cookie", "__Host-session=" + "0" * 64)
        req.add_header("Content-Type", "application/json")
        with contextlib.suppress(Exception):
            urllib.request.urlopen(req, timeout=15).read()
        if _d1_files():
            return
        time.sleep(0.3)
    raise RuntimeError(f"no D1 sqlite appeared under {WEBSITE_DIR}")


def _d1_sqlite() -> Path:
    """Newest local D1 database file, waiting for wrangler to create it.

    `metadata.sqlite` is miniflare's own bookkeeping DB, not the bound database —
    excluding it keeps a schema seed off the wrong file.
    """
    deadline = time.time() + 30
    while time.time() < deadline:
        files = _d1_files()
        if files:
            return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[0]
        time.sleep(0.3)
    raise RuntimeError(f"no D1 sqlite appeared under {WEBSITE_DIR}")


def _seed_schema() -> None:
    """Create the auth tables in the LOCAL D1 — schema only, no rows."""
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        con.executescript(MIGRATION.read_text(encoding="utf-8"))
        con.commit()
    finally:
        con.close()


@pytest.fixture(scope="module")
def stack():
    # FAIL, do not skip: a skipped security suite is indistinguishable from a
    # passing one (see bff_test_helpers).
    require_toolchain()

    global APP_PORT, MOCK_PORT, APP, MOCK_URL
    claimed: set[int] = set()
    APP_PORT = pick_free_port(APP_PORT, claimed)
    MOCK_PORT = pick_free_port(MOCK_PORT, claimed)
    assert APP_PORT != MOCK_PORT
    APP = f"http://127.0.0.1:{APP_PORT}"
    MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"

    env = os.environ.copy()
    env["MOCK_PORT"] = str(MOCK_PORT)
    mock = subprocess.Popen(
        [shutil.which("node"), str(MOCK)], cwd=str(MOCK.parent), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    assert _wait(MOCK_PORT), "mock failed to start"

    app = subprocess.Popen(
        [
            shutil.which("wrangler"), "pages", "dev", ".",
            "--port", str(APP_PORT), "--ip", "127.0.0.1",
            "--d1", "SESSIONS",
            "-b", f"SUPABASE_URL={MOCK_URL}",
            "-b", "SUPABASE_ANON_KEY=mock-anon-key",
            # Required by the purge route's config guard — without it the route
            # answers 503 not_configured and requireAdmin is never reached, so
            # every admin-gate assertion below would be testing the guard.
            "-b", "SUPABASE_SERVICE_ROLE_KEY=mock-service-role",
        ],
        cwd=str(WEBSITE_DIR),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    if not _wait(APP_PORT):
        stop(app)
        stop(mock)
        pytest.fail("pages dev failed to start")
    time.sleep(2.5)

    # The local D1 sqlite is created LAZILY, on first D1 ACCESS — make one
    # request touch the binding before seeding the schema into it, or the glob
    # below times out on a clean runner (it only passes where an earlier run
    # already created the file).
    _warm_d1()
    _seed_schema()

    yield {"app": APP, "mock": MOCK_URL}

    for p in (app, mock):
        stop(p)


def _fault(**kwargs) -> dict:
    """Toggle an injected fault on the mock."""
    data = json.dumps(kwargs).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/fault", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _blog_admin(user_id: str | None = None, clear: bool = False) -> dict:
    """Grant or clear blog_admins membership on the mock."""
    payload: dict = {}
    if user_id:
        payload["userId"] = user_id
    if clear:
        payload["clear"] = True
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/blog-admin", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _purge_with_bearer() -> tuple[int, str]:
    req = urllib.request.Request(f"{APP}/blog/api/purge", method="POST", data=b"{}")
    req.add_header("Authorization", "Bearer legacy-token")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_legacy_bearer_provider_outage_is_503_not_401(stack):
    """The legacy bearer path must not turn a provider outage into a sign-out.

    This is the #3485 class on the LAST path still carrying it: `verifySession`
    returned null for both "invalid token" and "provider down", and requireAdmin
    answered 401 for both.
    """
    _fault(authUser=True)
    try:
        req = urllib.request.Request(f"{APP}/blog/api/purge", method="POST", data=b"{}")
        req.add_header("Authorization", "Bearer legacy-token")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status, body = r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read().decode()
    finally:
        _fault(authUser=False)

    assert status == 503, (
        f"a provider outage on the bearer path must be 503, never 401 — got {status} {body}"
    )


def test_legacy_bearer_non_admin_is_401(stack):
    """A genuinely non-admin bearer IS a refusal — the other side of the line.

    Depends on the mock actually answering /rest/v1/blog_admins with an empty set.
    Before that route existed this passed on a 404 for the wrong reason.
    """
    _blog_admin(clear=True)
    status, body = _purge_with_bearer()
    assert status == 401, f"a non-admin must be 401, got {status} {body}"


def test_legacy_bearer_admin_is_accepted(stack):
    """The ADMIN branch must be reachable — a 404 mock had made it untestable.

    With membership granted, the request must get PAST the admin gate. It may then
    fail for its own reasons (bad body), but it must not be refused as
    unauthorized — which is the only thing that distinguishes the branch.
    """
    _blog_admin(user_id="user-123")
    try:
        status, body = _purge_with_bearer()
    finally:
        _blog_admin(clear=True)
    # Assert the POST-gate status, not merely `!= 401`. `!= 401` also passes for
    # 400/500/503, so a regression that answered 503 would keep this green.
    assert status == 400, (
        f"an ADMIN bearer must pass the admin gate and then fail on its own bad "
        f"input (expected 400 invalid_slug), got {status} {body}"
    )
    assert json.loads(body).get("error") == "invalid_slug", body


def test_dead_bff_cookie_does_not_fall_back_to_a_legacy_bearer(stack):
    """A REVOKED BFF cookie must not resurrect a legacy bearer.

    Cycle 2 narrowed the legacy fallback so it only applies when no BFF cookie was
    sent at all. Without that guard, F15's "a password change revokes every
    session" is false for any browser still holding a legacy token, because D1
    revocation cannot reach a Supabase access token.

    This test sends BOTH (dead cookie + bearer) and asserts the request is still
    refused. Deleting the `presentedBffCookie` guard makes this fail: the bearer
    would be accepted and the admin gate reached.
    """

    # Grant the mock user admin rights, so an ACCEPTED bearer gets past the gate
    # and the two outcomes are distinguishable.
    _blog_admin(user_id="user-123")
    try:
        req = urllib.request.Request(f"{APP}/blog/api/purge", method="POST", data=b"{}")
        # A BFF cookie that is present but resolves to nothing (never issued).
        req.add_header("Cookie", "__Host-session=deadbeefdeadbeef")
        req.add_header("Authorization", "Bearer legacy-token")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status, body = r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read().decode()
    finally:
        _blog_admin(clear=True)

    assert status == 401, (
        f"a dead BFF cookie must NOT fall back to the legacy bearer, got {status} {body} "
        "— if this passed the admin gate, F15 is false for legacy-token holders"
    )


def test_malformed_cookie_does_not_500_on_the_blog_route(stack):
    """The `/blog/api/purge` half of the malformed-cookie property.

    A bare `%` raises URIError inside decodeURIComponent; on this route it must
    stay inside the admin gate's contract rather than become a 500. The other
    three paths of the original case (/api/session, /api/v1/teams, /welcome)
    moved with the BFF and are asserted in test_bff_flow.py.
    """
    req = urllib.request.Request(f"{APP}/blog/api/purge", method="POST", data=b"{}")
    req.add_header("Cookie", "__Host-session=%")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status != 500, f"/blog/api/purge 500s on a malformed cookie: {status}"
