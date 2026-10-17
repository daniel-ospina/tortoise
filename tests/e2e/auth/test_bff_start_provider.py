"""
Provider selection for `/auth/start` (#4054): a strict allowlist, never a passthrough.

Runs the REAL Cloudflare Pages runtime (`wrangler pages dev`) with the REAL D1
binding and the REAL S256 PKCE exchange against the shared mock Supabase. So
"GoTrue was called", "the flow row was written" and "the method is s256" are
OBSERVED rather than inferred from a status code.

It also carries BOTH halves of the #3647 class — the sweeps that collect
abandoned pre-auth rows. `/auth/start` is where they all hang: it sweeps
`auth_flows` (#3647), and the `email_flow_pending` sweep (#8032) runs beside it
because this is the one path a schedule already drives (the #3628 probe). The
`sessions` sweep (#8032) hangs off `createSession`, so it is observed by doing
what a user does — completing the flow.

HARNESS NOTE — why this is a separate file
------------------------------------------
The auth Functions live at `website/apps/dashboard/functions` (moved there by
#4054). `tests/e2e/auth/test_bff_flow.py` still stands wrangler up from
`website/` — the marketing site — where `/auth/*` no longer exists: its own
`test_start_sets_bound_httponly_flow_cookie` returns 404 on an unmodified
checkout. That file also covers `/blog/api/purge`, which only exists under
`website/functions`, so its fixture cannot be repointed at the dashboard without
losing that half. This suite therefore uses the corrected dashboard harness
already established in this directory by `test_link_flow.py`, and reuses the
shared `mock_supabase.mjs` (which implements the authorize hop and validates the
PKCE verifier for real).
"""
from __future__ import annotations

import contextlib
import http.cookiejar
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import pytest
from bff_test_helpers import (
    d1_sqlite_files,
    dashboard_persist_args,
    pick_free_port,
    require_toolchain,
    stop,
    wait_for_port,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MOCK = Path(__file__).resolve().parent / "mock_supabase.mjs"
SESSION_TS = DASHBOARD_DIR / "functions" / "_shared" / "auth" / "session.ts"

SESSION_COOKIE = "__Host-session"

# Every garbage-collection assertion counts only ITS OWN rows: the module-scoped
# `stack` shares one local D1 across this whole file, so a count without a prefix
# would be reading the residue of whatever test ran before it.
GC_ABANDONED = "gc-abandoned-"
GC_LIVE = "gc-live-"
GC_BACKLOG = "gc-backlog-"
# #8032 — the `email_flow_pending` sweep (driven by `/auth/start`).
GC_PENDING_ABANDONED = "gc-pending-abandoned-"
GC_PENDING_LIVE = "gc-pending-live-"
GC_PENDING_BACKLOG = "gc-pending-backlog-"
# #8032 — the `sessions` sweep (driven by `createSession`).
GC_SESS_EXPIRED = "gc-sess-expired-"
GC_SESS_LIVE = "gc-sess-live-"
GC_SESS_REVOKED = "gc-sess-revoked-"
GC_SESS_GRACE = "gc-sess-grace-"

# Outside every range the sibling suites claim (8790-8801, 8970-8971,
# 8980-8981, 8995, 8997-8998, 9002-9003).
APP_PORT = int(os.environ.get("AUTH_START_APP_PORT", "9004"))
MOCK_PORT = int(os.environ.get("AUTH_START_MOCK_PORT", "9005"))
APP = f"http://127.0.0.1:{APP_PORT}"
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"


class Proc:
    def __init__(self, argv, cwd, env=None):
        self.p = subprocess.Popen(
            argv, cwd=cwd, env=env or os.environ.copy(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
        )

    def stop(self):
        stop(self.p)


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


def _flow_row(flow_id: str) -> dict | None:
    db = _d1_sqlite()
    con = sqlite3.connect(db, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT flow_id,kind,verifier,expires_at FROM auth_flows WHERE flow_id = ?",
            (flow_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def _warm_sessions_table() -> None:
    """Materialise `sessions` (+ its indexes) through a real read path.

    `/auth/start` bootstraps only the PRE-AUTH tables. `sessions` comes from
    `ensureSchema`, reached via `ensureSchemaTokenColumns` on an authenticated
    read — so this is the same bootstrap a returning browser goes through. The
    handle is one the store has never seen, which is enough to run the DDL and
    enough to be told `not_signed_in` (401), never a 503.
    """
    req = urllib.request.Request(f"{APP}/api/session", method="GET")
    req.add_header("Cookie", f"{SESSION_COOKIE}=gc-nonexistent-handle")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raise AssertionError(f"expected 401 for an unknown handle, got {r.status}")
    except urllib.error.HTTPError as e:
        assert e.code == 401, f"sessions bootstrap read answered {e.code}, not 401"


@pytest.fixture(scope="module")
def stack():
    # FAIL, do not skip: a skipped security suite is indistinguishable from a
    # passing one in CI.
    require_toolchain()
    node = shutil.which("node")
    wrangler = shutil.which("wrangler")

    global APP_PORT, MOCK_PORT, APP, MOCK_URL
    claimed: set[int] = set()
    APP_PORT = pick_free_port(APP_PORT, claimed)
    MOCK_PORT = pick_free_port(MOCK_PORT, claimed)
    assert APP_PORT != MOCK_PORT, "app and mock must not share a port"
    APP = f"http://127.0.0.1:{APP_PORT}"
    MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"

    mock_env = os.environ.copy()
    mock_env["MOCK_PORT"] = str(MOCK_PORT)
    mock = Proc([node, str(MOCK)], cwd=str(MOCK.parent), env=mock_env)
    try:
        if not wait_for_port(MOCK_PORT):
            raise RuntimeError(f"mock never opened {MOCK_PORT}")
    except RuntimeError:
        mock.stop()
        pytest.fail("mock supabase failed to start")

    # `wrangler pages dev <dir>` from the repo root does not discover
    # `<dir>/functions`, so cwd is the site dir and the argv is ".".
    app = Proc(
        [
            wrangler, "pages", "dev", "dist",
            "--port", str(APP_PORT), "--ip", "127.0.0.1",
            "--d1", "SESSIONS",
            # Pinned: `_d1_sqlite()` below reads THIS path, and wrangler's own
            # state-root walk can resolve it outside the checkout (see
            # `dashboard_persist_args`).
            *dashboard_persist_args(),
            "-b", f"SUPABASE_URL={MOCK_URL}",
            "-b", "SUPABASE_ANON_KEY=mock-anon-key",
            "-b", f"AUTH_CALLBACK_URL={APP}/auth/callback",
            "-b", f"APP_ORIGIN={APP}",
        ],
        cwd=str(DASHBOARD_DIR),
    )
    try:
        if not wait_for_port(APP_PORT):
            raise RuntimeError(f"app never opened {APP_PORT}")
    except RuntimeError:
        app.stop()
        mock.stop()
        out = b""
        with contextlib.suppress(Exception):
            out = app.p.stdout.read() if app.p.stdout else b""
        pytest.fail(f"pages dev failed to start; output:\n{out.decode('utf-8', 'replace')[-3000:]}")
    time.sleep(2.0)  # let the function bundler finish

    yield {"app": APP, "mock": MOCK_URL}
    app.stop()
    mock.stop()


# ---------------------------------------------------------------------------
# Request + observation helpers
# ---------------------------------------------------------------------------
class _LocalhostSecurePolicy(http.cookiejar.DefaultCookiePolicy):
    """Accept `Secure` cookies over http on loopback.

    Browsers treat http://127.0.0.1 as a secure context and store Secure cookies
    there; Python's cookiejar does not. Without this every `__Host-` cookie is
    silently dropped and the tests fail for a reason unrelated to the app.
    """

    def return_ok_secure(self, cookie, request):
        host = request.get_full_url() or ""
        if host.startswith("http://127.0.0.1") or host.startswith("http://localhost"):
            return True
        return super().return_ok_secure(cookie, request)


class Jar:
    """Cookie jar with redirect-following disabled, recording the hop chain."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar(policy=_LocalhostSecurePolicy())
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect()
        )

    def get(self, url, follow=False):
        req = urllib.request.Request(url, method="GET")
        if follow:
            op = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(self.jar),
                urllib.request.HTTPRedirectHandler(),
            )
        else:
            op = self.opener
        try:
            with op.open(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace"), _headers(r)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), _headers(e)

    def cookie(self, name):
        for c in self.jar:
            if c.name == name:
                return c.value
        return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _headers(r) -> dict:
    """Headers as a plain dict, PRESERVING duplicate values (Set-Cookie)."""
    h: dict[str, str] = {}
    for k, v in r.headers.items():
        h[k] = f"{h[k]}, {v}" if k in h else v
    return h


def _warm_up(j: Jar) -> None:
    """Drive one real `/auth/start`, which is what materialises local D1.

    Two things depend on this and neither is incidental:

    * `wrangler pages dev` creates the D1 database file on first USE, not at
      startup — `--persist-to` only ever holds `metadata.sqlite` until a query
      runs. `_d1_sqlite()` therefore finds nothing until a D1-touching request
      has happened, and seeding at fixture time would fail with "no D1 sqlite
      appeared", far from the cause.
    * `auth_flows` and its `expires_at` index are created by the RUNTIME bootstrap
      (`ensureFlowTable`), which is also how production creates them
      (`config/required-bindings.yml`: "Tables are created at runtime"). Warming
      up through the route means these tests sweep a table shaped exactly like
      production's, rather than one this file built for itself.
    """
    status, body, _ = j.get(f"{APP}/auth/start", follow=False)
    assert status == 302, f"warm-up start failed: {status} {body}"


def _seed_flow_rows(count: int, *, expires_at: int, prefix: str) -> list[str]:
    """Insert `auth_flows` rows directly, so a sweep is OBSERVED, not inferred."""
    ids = [f"{prefix}{i}" for i in range(count)]
    now = int(time.time() * 1000)
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        con.executemany(
            "INSERT OR REPLACE INTO auth_flows "
            "(flow_id,verifier,kind,created_at,expires_at) VALUES (?,'v','signin',?,?)",
            [(fid, now, expires_at) for fid in ids],
        )
        con.commit()
    finally:
        con.close()
    return ids


def _count_flow_rows(prefix: str) -> int:
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM auth_flows WHERE flow_id LIKE ?", (f"{prefix}%",)
        ).fetchone()[0]
    finally:
        con.close()


def _index_exists(name: str) -> bool:
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        return (
            con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?", (name,)
            ).fetchone()[0]
            == 1
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# #8032 helpers — `email_flow_pending` and `sessions` rows seeded directly, so
# the sweeps are OBSERVED in local D1 rather than inferred from handler intent.
# ---------------------------------------------------------------------------
def _seed_pending_rows(count: int, *, expires_at: int, prefix: str) -> list[str]:
    ids = [f"{prefix}{i}" for i in range(count)]
    now = int(time.time() * 1000)
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        con.executemany(
            "INSERT OR REPLACE INTO email_flow_pending "
            "(flow_id,kind,user_id,refresh_token,email,created_at,expires_at) "
            "VALUES (?,'recovery','gc-user','gc-refresh-token','gc@example.test',?,?)",
            [(fid, now, expires_at) for fid in ids],
        )
        con.commit()
    finally:
        con.close()
    return ids


def _count_pending_rows(prefix: str) -> int:
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM email_flow_pending WHERE flow_id LIKE ?", (f"{prefix}%",)
        ).fetchone()[0]
    finally:
        con.close()


def _pending_row(flow_id: str) -> dict | None:
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT flow_id,refresh_token,expires_at FROM email_flow_pending WHERE flow_id = ?",
            (flow_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def _seed_session_rows(rows: list[tuple[str, int, int]]) -> list[str]:
    """`(handle, revoked, expires_at)` triples, inserted directly."""
    _warm_sessions_table()
    now = int(time.time() * 1000)
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        con.executemany(
            "INSERT OR REPLACE INTO sessions "
            "(handle,user_id,refresh_token,revoked,created_at,expires_at) VALUES (?,?,?,?,?,?)",
            [
                (handle, "gc-user", "gc-refresh-token", revoked, now, expires_at)
                for handle, revoked, expires_at in rows
            ],
        )
        con.commit()
    finally:
        con.close()
    return [handle for handle, _, _ in rows]


def _count_session_rows(prefix: str) -> int:
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM sessions WHERE handle LIKE ?", (f"{prefix}%",)
        ).fetchone()[0]
    finally:
        con.close()


def _session_row(handle: str) -> dict | None:
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT handle,revoked,expires_at FROM sessions WHERE handle = ?", (handle,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def _mint_session(j: Jar) -> str:
    """Complete a REAL session mint: `/auth/start` -> authorize -> `/auth/callback`.

    `createSession` is where the `sessions` sweep hangs, so it can only be
    observed by doing what a user does. Returns the handle the route set in
    `__Host-session`.
    """
    status, body, headers = j.get(f"{APP}/auth/start?provider=github", follow=False)
    assert status == 302, f"session mint: start failed: {status} {body}"
    status, _, headers = j.get(headers["Location"], follow=False)  # GoTrue authorize
    assert status == 302, f"session mint: authorize hop failed: {status}"
    status, _, headers = j.get(headers["Location"], follow=False)  # /auth/callback
    assert status == 302, f"session mint: callback hop failed: {status}"
    handle = j.cookie(SESSION_COOKIE)
    assert handle, f"session mint produced no {SESSION_COOKIE}: {headers.get('Set-Cookie')}"
    return handle


def _sweep_limit() -> int:
    """`FLOW_SWEEP_LIMIT` read from the source that enforces it.

    A hardcoded cap here would silently stop testing the cap the moment the
    constant moved, and the bound IS the property under test. #8032: the same
    bound is shared by all three sweeps, so it is read once and used by each.
    """
    m = re.search(
        r"export const FLOW_SWEEP_LIMIT\s*=\s*(\d+)", SESSION_TS.read_text(encoding="utf-8")
    )
    assert m, (
        f"FLOW_SWEEP_LIMIT not found in {SESSION_TS} — the sweep's bound was "
        "renamed or removed, so this suite can no longer check it"
    )
    return int(m.group(1))


def _session_grace_s() -> int:
    """`SESSION_SWEEP_GRACE_S` read from the source that enforces it (#8032).

    The policy IS the number here — a `sessions` row is kept for its own TTL
    plus this grace and no longer — so a hardcoded copy in the test would stop
    testing the policy the moment the constant moved.
    """
    m = re.search(
        r"export const SESSION_SWEEP_GRACE_S\s*=\s*([0-9]+(?:\s*\*\s*[0-9]+)*)",
        SESSION_TS.read_text(encoding="utf-8"),
    )
    assert m, (
        f"SESSION_SWEEP_GRACE_S not found in {SESSION_TS} — the retention "
        "policy was renamed or removed, so this suite can no longer check it"
    )
    value = 1
    for part in m.group(1).split("*"):
        value *= int(part.strip())
    return value


def _mock_paths(reset: bool = False) -> list[str]:
    """Paths the mock upstream has actually been asked for.

    Observation, not inference: a 400 from our own handler and a 400 produced
    after an upstream call look identical from the response alone, so "GoTrue was
    NOT called" has to be read off the upstream's own request log.
    """
    data = json.dumps({"reset": reset}).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/paths", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())["paths"]


def _authorize(headers: dict) -> tuple[str, dict]:
    loc = headers.get("Location", "")
    assert loc, f"expected a Location header, got: {headers}"
    return loc, parse_qs(urlparse(loc).query)


# ---------------------------------------------------------------------------
# Contract: an allowlisted provider is forwarded with the PKCE contract intact
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["github", "google"])
def test_start_provider_targets_gotrue_with_s256(stack, provider):
    j = Jar()
    status, body, headers = j.get(f"{APP}/auth/start?provider={provider}", follow=False)
    assert status == 302, f"provider={provider} must redirect, got {status} {body}"

    loc, q = _authorize(headers)
    assert loc.startswith(f"{MOCK_URL}/auth/v1/authorize"), loc
    assert q.get("provider") == [provider], f"provider not forwarded to GoTrue: {q}"
    # The PKCE contract is unchanged by the new parameter — S256 only, never plain.
    assert q.get("code_challenge"), f"PKCE challenge missing for {provider}: {q}"
    assert q.get("code_challenge_method") == ["s256"], q
    assert q.get("redirect_to") == [f"{APP}/auth/callback"], q

    # The flow cookie is the binding half of the class-8 mitigation.
    assert "__Host-authflow=" in headers.get("Set-Cookie", ""), (
        f"flow cookie missing: {headers.get('Set-Cookie')}"
    )


def test_start_provider_absent_defaults_to_email(stack):
    """Today's behaviour, preserved exactly: no param -> provider=email."""
    j = Jar()
    status, _, headers = j.get(f"{APP}/auth/start", follow=False)
    assert status == 302, f"absent provider must still work, got {status}"
    _, q = _authorize(headers)
    assert q.get("provider") == ["email"], f"absent provider must default to email, got {q}"
    assert q.get("code_challenge_method") == ["s256"], q


# ---------------------------------------------------------------------------
# Contract: anything else is refused with a 400 and never reaches GoTrue
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad",
    [
        "RANDOM",  # unknown
        "<script>alert(1)</script>",  # injection attempt
        "",  # present but EMPTY — not the same as absent
        "GITHUB",  # case is not normalised
        "github2",  # prefix attack against a naive startsWith check
        "email,github",  # multi-value smuggling
        "github ",  # trailing space
        "github&redirect_to=https://evil.example",  # param smuggling
    ],
)
def test_start_rejects_invalid_provider_with_400(stack, bad):
    j = Jar()
    _mock_paths(reset=True)
    status, body, headers = j.get(
        f"{APP}/auth/start?provider={quote(bad, safe='')}", follow=False
    )
    assert status == 400, f"provider={bad!r} must be 400, got {status} {body}"
    assert json.loads(body).get("error") == "unsupported_provider", body
    # Rejected means nothing was minted and nothing was forwarded upstream.
    assert "__Host-authflow=" not in headers.get("Set-Cookie", ""), (
        f"a rejected provider minted a flow cookie: {headers.get('Set-Cookie')}"
    )
    assert "Location" not in headers, f"a rejected provider redirected: {headers}"
    assert "/auth/v1/authorize" not in _mock_paths(), (
        f"GoTrue was called for a rejected provider {bad!r}"
    )


def test_start_ignores_a_client_supplied_redirect_to(stack):
    """No open redirect: `redirect_to` is server-owned, never read from the query."""
    j = Jar()
    status, _, headers = j.get(
        f"{APP}/auth/start?provider=github"
        "&redirect_to=https%3A%2F%2Fevil.example%2Fsteal",
        follow=False,
    )
    assert status == 302
    _, q = _authorize(headers)
    assert q.get("redirect_to") == [f"{APP}/auth/callback"], (
        f"a client-controlled redirect_to leaked into the authorize URL: {q}"
    )


# ---------------------------------------------------------------------------
# Contract: the flow row is written, and `kind` stays orthogonal to `provider`
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["github", "google"])
def test_start_provider_writes_a_matching_flow_row(stack, provider):
    """The positive case: a live `auth_flows` row, bound to the flow cookie.

    Asserted BOTH ways, so neither half can hide a regression:
      - directly, by reading the D1 row the cookie names
      - end-to-end, by completing the S256 exchange (the mock recomputes the
        verifier, so a session can only be minted from a correct row)
    """
    j = Jar()
    status, _, headers = j.get(f"{APP}/auth/start?provider={provider}", follow=False)
    assert status == 302
    flow_id = j.cookie("__Host-authflow")
    assert flow_id, f"flow cookie must be set for provider={provider}"

    row = _flow_row(flow_id)
    assert row is not None, (
        f"provider={provider} produced no `auth_flows` row — the INSERT did not run"
    )
    assert row["verifier"], f"the row's PKCE verifier must be present: {row}"
    # `kind` is the FLOW kind (signin/recovery — consumed by confirm.ts), not the
    # OAuth provider. Provider selects the GoTrue identity flow; it must NOT
    # overwrite the flow kind, or recovery detection breaks.
    assert row["kind"] == "signin", (
        f"provider must not clobber the flow kind, got kind={row['kind']!r}"
    )

    status, _, headers = j.get(headers["Location"], follow=False)  # GoTrue authorize
    assert status == 302, f"authorize hop failed for {provider}: {status}"
    status, _, headers = j.get(headers["Location"], follow=False)  # /auth/callback
    assert status == 302, f"callback hop failed for {provider}: {status}"
    assert "__Host-session=" in headers.get("Set-Cookie", ""), (
        f"no session for provider={provider} — the auth_flows row is missing or "
        f"its verifier does not match: {headers.get('Set-Cookie')}"
    )


def test_start_provider_does_not_clobber_an_explicit_flow_kind(stack):
    """`kind=recovery` + `provider=github` must stay retrieval-safe."""
    j = Jar()
    status, _, headers = j.get(
        f"{APP}/auth/start?kind=recovery&provider=github", follow=False
    )
    assert status == 302
    _, q = _authorize(headers)
    assert q.get("provider") == ["github"], q

    flow_id = j.cookie("__Host-authflow")
    assert flow_id
    row = _flow_row(flow_id)
    assert row is not None
    assert row["kind"] == "recovery", (
        f"an explicit flow kind must survive provider selection, got {row['kind']!r}"
    )


# ---------------------------------------------------------------------------
# Contract: the POST alias is untouched
# ---------------------------------------------------------------------------
def test_start_post_alias_still_accepts_a_provider(stack):
    """`onRequestPost` is the same handler; adding a param must not break it."""
    req = urllib.request.Request(f"{APP}/auth/start?provider=google", method="POST", data=b"")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=30) as r:
            status, headers = r.status, _headers(r)
    except urllib.error.HTTPError as e:
        status, headers = e.code, _headers(e)

    assert status == 302, f"POST alias must redirect, got {status}"
    _, q = _authorize(headers)
    assert q.get("provider") == ["google"], q


# ---------------------------------------------------------------------------
# Contract: expired flows are garbage-collected (#3647)
#
# The defect these guard. Every deletion of an `auth_flows` row was coupled to
# the flow COMPLETING — `/auth/callback` on a successful return trip,
# `/auth/link` when the upstream call never started a flow. A flow that is
# started and ABANDONED reaches neither, so its row stayed forever; the
# scheduled `/auth/start` probe (#3628) abandons one flow per run BY DESIGN
# (reading the redirect's `Location` is what lets it catch the #3616 class), so
# it was a steady producer of exactly those rows.
#
# Each assertion is the row observed in local D1 BEFORE and AFTER a real
# `GET /auth/start`, never a read of the handler's intent.
# ---------------------------------------------------------------------------
def test_an_abandoned_expired_flow_is_collected_by_the_next_start(stack):
    """#3647: the row a probe leaves behind is gone after the next start."""
    j = Jar()
    _warm_up(j)

    expired_at = int(time.time() * 1000) - 60_000  # past its 30-minute window
    _seed_flow_rows(1, expires_at=expired_at, prefix=GC_ABANDONED)
    assert _count_flow_rows(GC_ABANDONED) == 1, (
        "seeding failed — the pre-condition for this test is not established"
    )

    status, body, headers = j.get(f"{APP}/auth/start", follow=False)
    assert status == 302, f"the sweep must not break the start path: {status} {body}"

    assert _count_flow_rows(GC_ABANDONED) == 0, (
        "an expired, abandoned flow row survived a later /auth/start — this is "
        "exactly the unbounded growth #3647 describes"
    )
    # ... and the sweep did not eat the flow this very request minted.
    minted = j.cookie("__Host-authflow")
    assert minted, f"no flow cookie on a 302 start: {headers.get('Set-Cookie')}"
    assert _flow_row(minted) is not None, (
        "the sweep deleted the flow it was running inside — the new row must never "
        "be its own victim"
    )


def test_the_sweep_never_deletes_a_flow_that_is_still_live(stack):
    """Collection keys on `expires_at`, NEVER on "unfinished".

    An in-flight flow that has not been redeemed yet is exactly the state a
    legitimate user is in between `/auth/start` and `/auth/callback`; sweeping it
    would break the sign-in it was meant to protect.
    """
    _j = Jar()
    _warm_up(_j)
    _seed_flow_rows(1, expires_at=int(time.time() * 1000) + 600_000, prefix=GC_LIVE)

    status, _, _ = _j.get(f"{APP}/auth/start", follow=False)
    assert status == 302

    assert _count_flow_rows(GC_LIVE) == 1, (
        "the sweep deleted an UNEXPIRED flow — /auth/callback could still have "
        "redeemed it, so the predicate must be expires_at, not completion"
    )


def test_the_sweep_is_bounded_per_request_and_converges(stack):
    """A backlog is drained in slices, and the bound is progress, not a cap.

    Both halves matter. The bound is what keeps one user-facing request from
    trying to delete a year of accumulated rows; convergence is what stops the
    bound from stranding the remainder forever.
    """
    limit = _sweep_limit()
    backlog = limit + 100
    j = Jar()
    _warm_up(j)
    _seed_flow_rows(backlog, expires_at=int(time.time() * 1000) - 60_000, prefix=GC_BACKLOG)
    assert _count_flow_rows(GC_BACKLOG) == backlog

    status, _, _ = j.get(f"{APP}/auth/start", follow=False)
    assert status == 302
    assert _count_flow_rows(GC_BACKLOG) == backlog - limit, (
        f"expected exactly one bounded slice of {limit} rows per request"
    )

    status, _, _ = j.get(f"{APP}/auth/start", follow=False)
    assert status == 302
    assert _count_flow_rows(GC_BACKLOG) == 0, (
        "the backlog did not drain across successive starts — the bound has "
        "become a cap that strands rows"
    )


def test_the_start_bootstrap_recreates_the_expires_index(stack):
    """The sweep's cost argument IS the index, so the bootstrap must create it.

    `idx_auth_flows_expires` is declared in the migration, but a database created
    by the runtime bootstraps alone never got it — and `config/required-bindings.yml`
    records that production's tables are created exactly that way. Without the
    index the sweep's range predicate degrades to a table scan on the sign-in
    path, which is the whole reason the bound and the index ship together.
    """
    name = "idx_auth_flows_expires"
    j = Jar()
    _warm_up(j)

    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        con.execute(f"DROP INDEX IF EXISTS {name}")
        con.commit()
    finally:
        con.close()
    assert not _index_exists(name), "could not establish the pre-condition"

    status, _, _ = j.get(f"{APP}/auth/start", follow=False)
    assert status == 302
    assert _index_exists(name), (
        "the flow bootstrap did not create idx_auth_flows_expires on a database "
        "that lacked it — the sweep would fall back to a full table scan"
    )


# ---------------------------------------------------------------------------
# Contract: expired `email_flow_pending` rows are garbage-collected (#8032)
#
# The defect these guard. A pending record is written when a VERIFIED email link
# is held for the user's explicit confirmation, and its ONLY deletion was on
# CONSUMPTION (`/auth/confirm` POST). A user who lands on the interstitial and
# closes the tab reaches no other deletion, so the row stayed forever — and that
# row's payload is the flow's `refresh_token`, a live credential. The sweep runs
# on `/auth/start`, beside the `auth_flows` sweep, because this is the one path a
# schedule already drives (the #3628 probe, every 5 minutes): sweeping only on
# the next `/auth/confirm` would leave the LAST abandoned link's credential in
# the store indefinitely.
#
# Each assertion is the row observed in local D1 BEFORE and AFTER a real
# `GET /auth/start`, never a read of the handler's intent.
# ---------------------------------------------------------------------------
def test_an_abandoned_expired_pending_row_is_collected_by_the_next_start(stack):
    """#8032: the row an abandoned interstitial leaves is gone after the next start."""
    j = Jar()
    _warm_up(j)

    expired_at = int(time.time() * 1000) - 60_000  # past its 10-minute window
    _seed_pending_rows(1, expires_at=expired_at, prefix=GC_PENDING_ABANDONED)
    assert _count_pending_rows(GC_PENDING_ABANDONED) == 1, (
        "seeding failed — the pre-condition for this test is not established"
    )

    status, body, headers = j.get(f"{APP}/auth/start", follow=False)
    assert status == 302, f"the sweep must not break the start path: {status} {body}"

    assert _count_pending_rows(GC_PENDING_ABANDONED) == 0, (
        "an expired, abandoned `email_flow_pending` row survived a later "
        "/auth/start — an unusable credential stayed readable in the store, "
        "which is exactly the unbounded retention #8032 describes"
    )
    # ... and the sweep did not eat the flow this very request minted.
    minted = j.cookie("__Host-authflow")
    assert minted, f"no flow cookie on a 302 start: {headers.get('Set-Cookie')}"
    assert _flow_row(minted) is not None, (
        "the auth_flows side of this request's sweep deleted the flow it was "
        "running inside"
    )


def test_the_pending_sweep_never_deletes_a_row_that_is_still_live(stack):
    """Collection keys on `expires_at`, NEVER on "the user has not confirmed yet".

    A record inside its 10-minute window is exactly the state a legitimate user
    is in between the link click and the consent button; sweeping it would break
    the confirmation it was holding the credential for.
    """
    j = Jar()
    _warm_up(j)
    live = f"{GC_PENDING_LIVE}0"
    _seed_pending_rows(1, expires_at=int(time.time() * 1000) + 600_000, prefix=GC_PENDING_LIVE)

    status, _, _ = j.get(f"{APP}/auth/start", follow=False)
    assert status == 302

    assert _count_pending_rows(GC_PENDING_LIVE) == 1, (
        "the sweep deleted an UNEXPIRED pending record — /auth/confirm could "
        "still have consumed it, so the predicate must be expires_at"
    )
    row = _pending_row(live)
    assert row is not None and row["refresh_token"] == "gc-refresh-token", (
        f"the surviving row lost the credential it exists to hold: {row}"
    )


def test_the_pending_sweep_is_bounded_per_request_and_converges(stack):
    """A backlog drains in slices, and the bound is progress, not a cap.

    Both halves matter: the bound is what keeps one user-facing request (or one
    probe run) from trying to delete a year of accumulated rows, and
    convergence is what stops the bound from stranding the remainder forever.
    """
    limit = _sweep_limit()
    backlog = limit + 100
    j = Jar()
    _warm_up(j)
    _seed_pending_rows(
        backlog, expires_at=int(time.time() * 1000) - 60_000, prefix=GC_PENDING_BACKLOG
    )
    assert _count_pending_rows(GC_PENDING_BACKLOG) == backlog

    status, _, _ = j.get(f"{APP}/auth/start", follow=False)
    assert status == 302
    assert _count_pending_rows(GC_PENDING_BACKLOG) == backlog - limit, (
        f"expected exactly one bounded slice of {limit} rows per request"
    )

    status, _, _ = j.get(f"{APP}/auth/start", follow=False)
    assert status == 302
    assert _count_pending_rows(GC_PENDING_BACKLOG) == 0, (
        "the backlog did not drain across successive starts — the bound has "
        "become a cap that strands rows"
    )


def test_the_start_bootstrap_creates_the_pending_table_and_its_expires_index(stack):
    """The pending sweep's cost argument IS the index, so the bootstrap makes both.

    `idx_email_flow_pending_expires` is declared in the migration, but a database
    created by the runtime bootstraps alone — how production's tables are created
    (`config/required-bindings.yml`) — would never receive it. The table matters
    for the same reason from the other side: the sweep runs on `/auth/start`, a
    path that does not write the table, so an absent table would make every probe
    log a failure that nothing could fix.
    """
    name = "idx_email_flow_pending_expires"
    j = Jar()
    _warm_up(j)

    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        con.execute(f"DROP INDEX IF EXISTS {name}")
        con.execute("DROP TABLE IF EXISTS email_flow_pending")
        con.commit()
    finally:
        con.close()
    assert not _index_exists(name), "could not establish the pre-condition"

    status, _, _ = j.get(f"{APP}/auth/start", follow=False)
    assert status == 302
    assert _index_exists(name), (
        "the flow bootstrap did not create the `email_flow_pending` table and "
        "its expires index on a database that lacked them — the sweep would "
        "fail on every probe, and its range predicate would fall back to a scan"
    )


# ---------------------------------------------------------------------------
# Contract: `sessions` rows are garbage-collected at a mint, on `expires_at`
# (#8032)
#
# The defect these guard. Nothing ever deleted a `sessions` row: sign-out and F15
# "sign out everywhere" are `UPDATE sessions SET revoked = 1`, and no
# `DELETE FROM sessions` existed anywhere — so every row ever minted (400-day
# TTL, revoked or not) was kept forever, each carrying a `refresh_token`. Unlike
# `auth_flows`, `expires_at` here does NOT encode the row's whole usable life, so
# the sweep carries a grace: readers deliberately look at a row after its TTL has
# passed (`session_expired` is a distinct 401, and it revokes the row), and that
# is what the grace keeps reachable.
#
# The sweep hangs off `createSession`, so each assertion is the row observed in
# local D1 BEFORE and AFTER a real completed sign-in.
# ---------------------------------------------------------------------------
def test_an_expired_session_is_collected_when_a_session_is_minted(stack):
    """#8032: a row past its own life (+ the retention grace) is gone after a mint."""
    grace_ms = _session_grace_s() * 1000
    now = int(time.time() * 1000)
    j = Jar()
    _warm_up(j)
    _seed_session_rows([(f"{GC_SESS_EXPIRED}0", 0, now - grace_ms - 60_000)])
    assert _count_session_rows(GC_SESS_EXPIRED) == 1, (
        "seeding failed — the pre-condition for this test is not established"
    )

    handle = _mint_session(j)

    assert _count_session_rows(GC_SESS_EXPIRED) == 0, (
        "a `sessions` row past its own `expires_at` (+ the retention grace) "
        "survived a later sign-in — this is exactly the unbounded growth #8032 "
        "describes, and the row holds a refresh token"
    )
    # ... and the sweep did not eat the session this very request minted.
    row = _session_row(handle)
    assert row is not None, (
        "the sweep deleted the session it was minting — the new row has "
        "`expires_at = now + SESSION_MAX_AGE_S`, so it must never be its own "
        "victim"
    )
    assert row["revoked"] == 0, f"the minted session arrived revoked: {row}"


def test_the_session_sweep_never_deletes_a_live_revoked_or_within_grace_row(stack):
    """The predicate is `expires_at`, NEVER `revoked` — and the grace is real.

    Three rows must all survive a mint:
      - a live one (a session in use);
      - a REVOKED but unexpired one — deleting it early would end the window in
        which the CAS in `revokeSession` and abuse forensics can still see it;
      - one that expired INSIDE the grace — a reader that arrives late must still
        be told `session_expired` (and get the row revoked), not
        `not_signed_in`, which is the distinction the grace exists to keep.
    """
    grace_ms = _session_grace_s() * 1000
    hour_ms = 3_600_000
    assert grace_ms > hour_ms, (
        "the grace is now shorter than the 1-hour-expired row this test seeds, "
        "so the test no longer proves anything about grace semantics"
    )
    now = int(time.time() * 1000)
    j = Jar()
    _warm_up(j)
    _seed_session_rows(
        [
            (f"{GC_SESS_LIVE}0", 0, now + hour_ms),
            (f"{GC_SESS_REVOKED}0", 1, now + hour_ms),
            (f"{GC_SESS_GRACE}0", 1, now - hour_ms),
        ]
    )
    assert _count_session_rows(GC_SESS_LIVE) == 1

    _mint_session(j)

    assert _count_session_rows(GC_SESS_LIVE) == 1, (
        "the sweep deleted a LIVE session — /api/session could still have "
        "resolved it"
    )
    assert _count_session_rows(GC_SESS_REVOKED) == 1, (
        "the sweep deleted a REVOKED session before its own expiry — the "
        "predicate must be expires_at, not revoked"
    )
    assert _count_session_rows(GC_SESS_GRACE) == 1, (
        "the sweep deleted a session inside the retention grace — a late reader "
        "can no longer be told `session_expired`, and a mis-shipped TTL has "
        "lost its rollback window"
    )


def test_the_sessions_bootstrap_recreates_the_expires_index(stack):
    """The sessions sweep's cost argument IS the index, so a mint must create it.

    `idx_sessions_expires` is declared in the migration, but production's tables
    are created by the runtime bootstraps, which would never receive it — and
    without it the sweep's range predicate degrades to a full scan of the
    LARGEST table in this store, on the sign-in path.
    """
    name = "idx_sessions_expires"
    j = Jar()
    _warm_up(j)
    _warm_sessions_table()

    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        con.execute(f"DROP INDEX IF EXISTS {name}")
        con.commit()
    finally:
        con.close()
    assert not _index_exists(name), "could not establish the pre-condition"

    _mint_session(j)
    assert _index_exists(name), (
        "the sessions bootstrap did not create idx_sessions_expires on a "
        "database that lacked it — the sweep would fall back to a full table "
        "scan on every sign-in"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
