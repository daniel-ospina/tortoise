"""
/api/sb/* — the Supabase Token Handler for the blog admin console (#4178).

WHY THIS SUITE EXISTS
---------------------
`website/apps/dashboard/functions/api/sb/[[path]].ts` is the seam that attaches
the Supabase credential for the console. It is NOT a general gateway: it carries
an explicit allowlist (the `blog_posts` table and the `blog-images` bucket) and
an `is_admin()` check, and it strips the client's own `cookie`/`authorization`/
`apikey` before setting its own. None of that had behavioural coverage:
`securityHeaders.test.js` only pins its cookie-stripping SHAPE, and
`blog-api.test.ts` mocks `@/lib/backend`, so `proxiedFetch` never executes. The
sibling Token Handlers are covered (`/api/v1` by `test_proxy.py`, `/blog/api` by
`test_blog_purge_admin_gate.py`) — this route was the gap (#3559 review).

The cases run the REAL Pages runtime (`wrangler pages dev dist` from the
dashboard project, where the Functions live) against a local mock:
  - D1 is the real local binding; the session row is seeded with a cached access
    token, so no GoTrue call is needed and the only upstream the route touches is
    the mock;
  - the mock records the FULL credential headers the upstream actually saw, so
    "the browser's credential never reached the upstream" is observed, not
    assumed;
  - the allowlist, the encoded-separator guard and the 503-not-401 fault
    semantics each have their own case.

Failures FAIL (never skip) when node/wrangler are absent — a skipped security
suite is indistinguishable from a passing one (see `bff_test_helpers`).
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
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MIGRATION = REPO_ROOT / "website" / "migrations" / "0001_auth_sessions.sql"
MOCK = Path(__file__).resolve().parent / "mock_blog_admin_rpc.mjs"

# Distinct from every other auth suite's default range (8790-8801, 8970-8971,
# 8980-8981, 8990-8991, 8995-8998, 9002-9006, 9010-9011, 9030-9032, 9040-9046,
# 9050-9051, 9060-9063) so parallel collection cannot collide.
APP_PORT = int(os.environ.get("AUTH_SB_APP_PORT", "9070"))
MOCK_PORT = int(os.environ.get("AUTH_SB_MOCK_PORT", "9071"))
APP = f"http://127.0.0.1:{APP_PORT}"
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"

HANDLE = "c" * 64
UNKNOWN_HANDLE = "d" * 64
# The cached access token the route mints/uses. Seeding it means the session
# never reaches GoTrue, so the mock is the only upstream observed.
ACCESS = "mock-sb-access-token"
# The server's anon key (bound via `-b`). A client-supplied value must never
# win — the route overwrites `apikey` with THIS.
ANON = "mock-anon-key"
# Values the CLIENT forges. They must not appear in anything the upstream saw.
CLIENT_AUTH = "Bearer client-supplied-token"
CLIENT_APKEY = "client-supplied-anon-key"

# This suite's PRIVATE D1 persist dir, set by the `stack` fixture. Isolation is
# not optional: the shared `.wrangler` state accumulates rows from every other
# auth suite forever, and this suite seeds a session row.
PERSIST: Path | None = None


def _wait(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def _d1_files() -> list[Path]:
    assert PERSIST is not None, "stack fixture must run first"
    return [p for p in PERSIST.glob("**/d1/**/*.sqlite") if p.name != "metadata.sqlite"]


def _d1_sqlite() -> Path:
    deadline = time.time() + 30
    while time.time() < deadline:
        files = _d1_files()
        if files:
            return max(files, key=lambda p: p.stat().st_mtime)
        time.sleep(0.3)
    raise RuntimeError(f"no D1 database sqlite appeared under {PERSIST}")


def _warm_d1() -> None:
    """Materialise the bound D1 file.

    `--d1 SESSIONS` only declares the binding: the SQLite file appears on the
    first D1 ACCESS, not at boot. A request carrying an unknown `__Host-session`
    IS a D1 read — it makes the route call `ensureSchemaTokenColumns` and then
    resolve nothing.
    """
    deadline = time.time() + 30
    while time.time() < deadline:
        req = urllib.request.Request(f"{APP}/api/sb/rest/v1/blog_posts?select=id")
        req.add_header("Cookie", "__Host-session=" + "0" * 64)
        with contextlib.suppress(Exception):
            urllib.request.urlopen(req, timeout=15).read()
        if _d1_files():
            return
        time.sleep(0.3)
    raise RuntimeError(f"no D1 database sqlite appeared under {PERSIST}")


def _seed() -> None:
    """Apply the auth migration, then insert ONE live admin session with a cached token."""
    con = sqlite3.connect(_d1_sqlite(), timeout=15)
    try:
        con.executescript(MIGRATION.read_text(encoding="utf-8"))
        now = int(time.time() * 1000)
        con.execute(
            "INSERT OR REPLACE INTO sessions "
            "(handle,user_id,refresh_token,revoked,created_at,expires_at,"
            " access_token,access_token_expires_at) "
            "VALUES (?,?,?,0,?,?,?,?)",
            (
                HANDLE,
                "user-admin",
                "mock-refresh",
                now,
                now + 400 * 24 * 3600 * 1000,
                ACCESS,
                now + 3600 * 1000,
            ),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture(scope="module")
def stack(_dashboard_dist_built, tmp_path_factory):
    # FAIL, do not skip: a skipped security suite is indistinguishable from a
    # passing one.
    require_toolchain()
    node = shutil.which("node")
    wrangler = shutil.which("wrangler")

    global APP_PORT, MOCK_PORT, APP, MOCK_URL, PERSIST
    claimed: set[int] = set()
    APP_PORT = pick_free_port(APP_PORT, claimed)
    MOCK_PORT = pick_free_port(MOCK_PORT, claimed)
    assert APP_PORT != MOCK_PORT
    APP = f"http://127.0.0.1:{APP_PORT}"
    MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"
    PERSIST = tmp_path_factory.mktemp("sb-proxy-d1")

    env = os.environ.copy()
    env["MOCK_PORT"] = str(MOCK_PORT)
    mock = subprocess.Popen(
        [node, str(MOCK)], cwd=str(MOCK.parent), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    assert _wait(MOCK_PORT), "mock failed to start"

    app = subprocess.Popen(
        [
            wrangler, "pages", "dev", "dist",
            "--port", str(APP_PORT), "--ip", "127.0.0.1",
            "--compatibility-date=2026-08-26",
            "--d1", "SESSIONS",
            "--persist-to", str(PERSIST),
            # DELIBERATELY a trailing slash (#3559 P3): it is a legal spelling of
            # SUPABASE_URL and used to break EVERY request (the base became `//`,
            # so `isAllowed` refused the `//rest/v1/...` that URL normalisation
            # produced). Keeping it here makes the whole suite a permanent guard
            # on the normalisation rather than a one-off case.
            "-b", f"SUPABASE_URL={MOCK_URL}/",
            "-b", f"SUPABASE_ANON_KEY={ANON}",
        ],
        cwd=str(DASHBOARD_DIR),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    if not _wait(APP_PORT):
        stop(app)
        stop(mock)
        pytest.fail("pages dev failed to start")
    time.sleep(2.5)
    _warm_d1()
    _seed()

    yield {"app": APP, "mock": MOCK_URL}

    for p in (app, mock):
        stop(p)


def _req(
    path: str,
    method: str = "GET",
    cookie: str | None = None,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> tuple[int, str, dict[str, str]]:
    req = urllib.request.Request(APP + path, method=method, data=data)
    if cookie:
        req.add_header("Cookie", cookie)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def _state() -> dict:
    with urllib.request.urlopen(f"{MOCK_URL}/__mock/state", timeout=15) as r:
        return json.loads(r.read().decode())


def _control(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{MOCK_URL}{path}", method="POST", data=json.dumps(payload).encode()
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _reset() -> None:
    _control("/__mock/reset", {})


def _set_admin(value: bool) -> None:
    _control("/__mock/admin", {"value": value})


def _set_upstream_fault(
    value: bool,
    target: str = "data",
    status: int = 500,
    body: dict | None = None,
) -> None:
    """Inject (or clear) an upstream fault.

    `target` selects the surface: `"data"` (the proxied call, #4178's default),
    `"admin"` (the `is_admin` RPC the gate consults) or `"all"`. The two are
    independently faultable so each of the Token Handler's 503 branches can be
    proven on its own rather than through the other's (#3559 review). `status`
    selects the injected status (default 500) so the 5xx and the non-5xx
    classification branches can each be exercised. `body` selects the upstream
    error BODY (default `{"error":"upstream_fault"}`) because the `is_admin`
    401/403 classification is BODY-dependent (#3559 P2-1).
    """
    payload = {"value": value, "target": target, "status": status}
    if body is not None:
        payload["body"] = body
    _control("/__mock/upstream-fault", payload)


def _seen(*kinds: str) -> list[dict]:
    return [s for s in _state()["seen"] if s["kind"] in kinds]


def test_anonymous_is_401_and_never_reaches_the_upstream(stack):
    """No `__Host-session` → OUR 401, and nothing is asked upstream.

    Our 401 means — and only ever means — "you are not signed in" (the /api/v1
    contract). The body is `not_signed_in`; `no_session` is the internal store
    reason, never an HTTP body on this route.
    """
    _reset()
    status, body, _ = _req("/api/sb/rest/v1/blog_posts?select=*")
    assert status == 401, f"an anonymous proxy call must be 401, got {status} {body}"
    assert json.loads(body)["error"] == "not_signed_in", body
    assert not _seen("is_admin", "blog_posts", "storage"), (
        "an anonymous call reached the upstream"
    )


def test_signed_in_non_admin_is_403_and_never_reaches_the_data_upstream(stack):
    """A signed-in NON-admin is refused with an explicit 403.

    The `is_admin()` RPC IS consulted (that is how the verdict is learned), but
    the data endpoint must not be — otherwise the admin check would be
    decorative.
    """
    _reset()
    _set_admin(False)
    try:
        status, body, _ = _req(
            "/api/sb/rest/v1/blog_posts?select=*", cookie=f"__Host-session={HANDLE}"
        )
    finally:
        _set_admin(True)

    assert status == 403, f"a signed-in non-admin must be 403, got {status} {body}"
    assert json.loads(body)["error"] == "not_admin", body
    assert not _seen("blog_posts", "storage"), (
        "a non-admin reached the data upstream — the admin check is not enforcing"
    )


def test_admin_reaches_the_upstream_with_a_server_minted_credential(stack):
    """The credential is attached SERVER-SIDE, and the client's own never arrives.

    The mock records the headers the upstream ACTUALLY saw, so this is observed,
    not assumed. Three properties:
      1. the upstream was reached (a proxy that answered 200 without forwarding
         would fail);
      2. the Authorization is the SERVER's minted bearer — not the client's;
      3. the client's `cookie`/`authorization`/`apikey` do NOT appear upstream.
    """
    _reset()
    status, body, _ = _req(
        "/api/sb/rest/v1/blog_posts?select=id,slug&status=eq.draft",
        cookie=f"__Host-session={HANDLE}",
        headers={"Authorization": CLIENT_AUTH, "apikey": CLIENT_APKEY},
    )
    assert status == 200, f"an admin must reach the upstream, got {status} {body}"

    posts = _seen("blog_posts")
    assert posts, "the proxy never reached the PostgREST upstream"
    last = posts[-1]
    assert last["method"] == "GET", last
    assert last["path"] == "/rest/v1/blog_posts", last
    assert last["search"] == "?select=id,slug&status=eq.draft", (
        f"the query string was not forwarded upstream: {last['search']!r}"
    )
    assert last["auth"] == f"Bearer {ACCESS}", (
        f"the upstream did not see the server-minted bearer: {last['auth']!r}"
    )
    assert last["apikey"] == ANON, (
        f"the upstream did not see the server anon key: {last['apikey']!r}"
    )

    # ⛔ The credential-never-reaches-upstream property. Serialize EVERYTHING the
    # mock recorded so an unforeseen field cannot smuggle a client value past a
    # field-by-field check.
    wire = json.dumps(last)
    assert "__Host-session" not in wire, "the session handle reached the upstream"
    assert CLIENT_AUTH not in wire, "the client's Authorization reached the upstream"
    assert CLIENT_APKEY not in wire, "the client's apikey reached the upstream"
    assert not last["cookie"], f"a cookie was forwarded upstream: {last['cookie']!r}"


def test_storage_request_is_proxied_with_the_server_credential(stack):
    """The `blog-images` bucket is inside the allowlist and rides the same seam."""
    _reset()
    status, body, _ = _req(
        "/api/sb/storage/v1/object/blog-images/draft/1712345-img.png",
        method="POST",
        cookie=f"__Host-session={HANDLE}",
        data=b"fake-image-bytes",
    )
    assert status == 200, f"a storage upload must be proxied, got {status} {body}"

    storage = _seen("storage")
    assert storage, "the proxy never reached the Storage upstream"
    last = storage[-1]
    assert last["method"] == "POST", last
    assert last["path"] == "/storage/v1/object/blog-images/draft/1712345-img.png", last
    assert last["auth"] == f"Bearer {ACCESS}", last
    assert last["apikey"] == ANON, last
    assert not last["cookie"], "the session cookie was forwarded to Storage"
    assert b"fake-image-bytes".decode() in last["body"], (
        "the upload body was not forwarded — the route must stream, not drop it"
    )


def test_path_outside_the_allowlist_is_403(stack):
    """The allowlist is a deliberate boundary, not a missing route.

    `other_table` is a real PostgREST surface on the same project; a general
    gateway would forward it. The route must refuse it — and must not ask the
    upstream anything for the data path.
    """
    _reset()
    status, body, _ = _req(
        "/api/sb/rest/v1/other_table?select=*", cookie=f"__Host-session={HANDLE}"
    )
    assert status == 403, f"a non-allowlisted path must be 403, got {status} {body}"
    assert json.loads(body)["error"] == "path_not_allowed", body
    assert not _seen("blog_posts", "storage"), (
        "a non-allowlisted path was forwarded upstream"
    )


def test_encoded_separator_is_refused_before_it_reaches_the_upstream(stack):
    """`%2f` / `%2e` in the wildcard must be refused, never handed upstream.

    URL normalisation does NOT decode `%2f`, so `/api/sb/rest/v1/..%2fadmin`
    stays under the prefix here and would be forwarded verbatim for the upstream
    to decode — a traversal we handed it. The route refuses it with 400 before
    any upstream call; every hostile path is asserted, not sampled.
    """
    _reset()
    for hostile in (
        "/api/sb/rest/v1%2fblog_posts",
        "/api/sb/rest/v1/..%2fadmin",
        "/api/sb/rest/v1%2e%2e%2fadmin",
        "/api/sb/rest/v1/blog_posts%5c..%5cadmin",
    ):
        status, body, _headers = _req(hostile, cookie=f"__Host-session={HANDLE}")
        assert status == 400, (
            f"{hostile} must be refused with 400 (encoded separator), got {status} {body}"
        )
        assert json.loads(body)["error"] == "invalid_path", body
    assert not _seen("blog_posts", "storage"), (
        "an encoded-separator path was forwarded upstream"
    )


def test_upstream_5xx_is_503_not_401(stack):
    """An upstream fault is 503 (retry), never 401 (signed out) — the #3485 class."""
    _reset()
    _set_upstream_fault(True)
    try:
        status, body, _ = _req(
            "/api/sb/rest/v1/blog_posts?select=*", cookie=f"__Host-session={HANDLE}"
        )
    finally:
        _set_upstream_fault(False)

    assert status == 503, (
        f"an upstream outage must be 503 (try again), never 401 (signed out) — "
        f"got {status} {body}"
    )
    payload = json.loads(body)
    assert payload["error"] == "upstream_unavailable", body
    assert payload["error"] != "not_signed_in", body


@pytest.mark.parametrize(
    "fault_status",
    [500, 404],
    ids=["5xx", "non-5xx"],
)
def test_admin_check_fault_is_503_not_403(stack, fault_status):
    """A fault on the ADMIN CHECK is 503 (retry), never 403 (not an admin).

    This is the branch #3559 fixed. The pre-fix `checkAdmin` already mapped a
    5xx/429 to `unavailable`; what #3559 broadened is the OTHER non-ok class —
    everything that is neither ok, nor 5xx/429, nor an access decision (401/403)
    is now `unavailable` too. That is the missing/renamed RPC (404), the rotated
    key, the PostgREST fault: a store misconfiguration that the pre-fix handler
    collapsed to `not_admin`, so it read as "you are signed in but lack access"
    and hid a store fault behind an access verdict, costing an hour to debug
    (#3485).

    BOTH status classes are asserted on purpose, and the non-5xx one is the
    discriminating case: the 5xx case passes against the pre-fix handler and so
    guards nothing (it would have passed before #3559 shipped), while the
    non-5xx case fails without the fix. Dropping either would lose coverage.

    This is NOT the data-upstream case above: the fault is injected on the
    `is_admin` RPC itself, so the gate refuses the request BEFORE the proxied
    call runs. That is what makes this the `checkAdmin` branch and not a
    duplicate — the data-branch marker is asserted absent below.
    """
    _reset()
    _set_upstream_fault(True, target="admin", status=fault_status)
    try:
        status, body, _ = _req(
            "/api/sb/rest/v1/blog_posts?select=*", cookie=f"__Host-session={HANDLE}"
        )
    finally:
        _set_upstream_fault(False)

    assert status == 503, (
        f"a {fault_status} fault on the admin check must be 503 (try again), never "
        f"403 (signed in but not an admin) — got {status} {body}"
    )
    assert status != 403, (
        f"a store fault must never read as an access decision — got {status} {body}"
    )
    payload = json.loads(body)
    assert payload["error"] == "upstream_unavailable", body
    assert payload["error"] != "not_admin", body
    # The DATA branch's marker: its presence would mean this response came from
    # the proxied call rather than from the gate under test.
    assert "upstream_status" not in payload, body
    assert _seen("is_admin"), "the fault was injected on a route the gate never reached"
    assert not _seen("blog_posts", "storage"), (
        "the request was proxied despite the admin check failing to resolve"
    )


@pytest.mark.parametrize(
    "status,body,expect_status,expect_error",
    [
        # The USER's bearer was rejected by PostgREST (the JWT family) → sign out.
        (401, {"code": "PGRST301", "message": "JWT expired"}, 401, "not_signed_in"),
        (403, {"code": "PGRST303", "message": "JWT claim validation failed"}, 401, "not_signed_in"),
        # The SERVICE's anon key was rejected by the gateway, or a permission
        # configuration fault → OUR problem, a 503. NONE of these may be read as
        # "you are not an admin" (403) or as a sign-out (401).
        (401, {"error": "Invalid API key"}, 503, "upstream_unavailable"),
        (401, {"message": "Missing or invalid credentials"}, 503, "upstream_unavailable"),
        (403, {"code": "42501", "message": "insufficient privileges"}, 503, "upstream_unavailable"),
    ],
    ids=["user-401", "user-403", "service-key-401", "gateway-plaintext", "config-403"],
)
def test_admin_check_rejection_is_classified_by_body(
    stack, status, body, expect_status, expect_error
):
    """A 401/403 from `is_admin` is AMBIGUOUS by STATUS — the BODY decides.

    The call carries the user's minted bearer AND the project anon key, so the
    SAME 401/403 comes from a rejected USER token (PostgREST `PGRST301`/`303`)
    or from a rotated/wrong `SUPABASE_ANON_KEY` (the gateway rejecting the
    SERVICE credential).

    Why this must not be status-only. Aligning this branch to the sibling
    gate's `unauthenticated` by STATUS ALONE turns a rotated anon key — a
    configuration fault — into a re-auth bounce, and mapping it to `not_admin`
    (the pre-fix behaviour) reads it as an access decision; the first breaks the
    #3485 property this PR established, the second is the lie #3559 fixed. So
    only a POSITIVE user-token signal is a sign-out; everything else stays 503.
    Same classifier as `admin/[[path]].ts::isAdmin`, so the two surfaces agree.

    The pre-fix handler mapped EVERY 401/403 to `not_admin` (403), and the new
    case `test_admin_check_fault_is_503_not_403` only parametrizes 5xx/non-5xx —
    this branch had NO coverage at all (#3559 review P2-1).
    """
    _reset()
    _set_upstream_fault(True, target="admin", status=status, body=body)
    try:
        got_status, got_body, _ = _req(
            "/api/sb/rest/v1/blog_posts?select=*", cookie=f"__Host-session={HANDLE}"
        )
    finally:
        _set_upstream_fault(False)

    assert got_status == expect_status, (
        f"is_admin {status} {body!r} must be {expect_status}, got {got_status} {got_body}"
    )
    assert json.loads(got_body)["error"] == expect_error, got_body
    assert got_status != 403, (
        "a 401/403 from the admin check must never read as `not_admin` — that is "
        f"the #3485 lie: got {got_status} {got_body}"
    )
    assert _seen("is_admin"), "the fault was injected on a route the gate never reached"
    assert not _seen("blog_posts", "storage"), (
        "the request was proxied despite the admin check failing to resolve"
    )


def test_unknown_handle_is_401(stack):
    """A dead handle IS a sign-out — the other side of the same distinction."""
    _reset()
    status, body, _ = _req(
        "/api/sb/rest/v1/blog_posts?select=*", cookie=f"__Host-session={UNKNOWN_HANDLE}"
    )
    assert status == 401, f"an unknown handle must be 401, got {status} {body}"
    assert json.loads(body)["error"] == "not_signed_in", body
    assert not _seen("blog_posts", "storage"), "a dead handle reached the upstream"
