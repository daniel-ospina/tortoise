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
import re
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
from bff_test_helpers import pick_free_port, require_toolchain

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MOCK = Path(__file__).resolve().parent / "mock_email_flows_supabase.mjs"

APP_PORT = int(os.environ.get("CSRF_TEST_APP_PORT", "9050"))
MOCK_PORT = int(os.environ.get("CSRF_TEST_MOCK_PORT", "9051"))

APP = ""      # assigned in the `stack` fixture
MOCK_URL = ""

MIGRATION = REPO_ROOT / "website" / "migrations" / "0001_auth_sessions.sql"
# A LIVE session the origin-only proxy cases carry. A cross-origin request can
# only be shown to have been refused BEFORE the upstream if it would have
# reached the upstream with the guard removed — and that needs a session these
# routes can resolve, not merely a well-formed cookie (a bogus handle stops at
# `readCookie`/`getAccessTokenForSession` and never forwards).
SESSION_COOKIE = "__Host-session"
SESSION_HANDLE = "e" * 64
# The cached access token the route mints/uses. Seeding it means the session
# never refreshes at GoTrue, so the only upstream calls the log can hold are the
# ones the guard is supposed to prevent.
SESSION_ACCESS = "mock-csrf-access-token"

# This suite's PRIVATE D1 persist dir, set by the `stack` fixture. Isolation is
# not optional: the shared `.wrangler` state accumulates rows from every other
# auth suite forever, and this suite seeds a session row.
PERSIST: Path | None = None

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


def _d1_files() -> list[Path]:
    assert PERSIST is not None, "stack fixture must run first"
    # The persist tree also holds `metadata.sqlite` (D1's index plus the
    # cache/observability stores); picking one of those seeds a database the
    # Worker never reads, which presents as a route 503, not a seeding error.
    return [
        p
        for p in PERSIST.glob("**/d1/**/*.sqlite")
        if p.name != "metadata.sqlite"
    ]


def _d1_sqlite() -> Path:
    deadline = time.time() + 30
    while time.time() < deadline:
        files = _d1_files()
        if files:
            return max(files, key=lambda p: p.stat().st_mtime)
        time.sleep(0.3)
    raise RuntimeError(f"no D1 database sqlite appeared under {PERSIST}")


def _warm_d1() -> None:
    """Materialise the bound D1 file (the SQLite file appears on first ACCESS)."""
    deadline = time.time() + 30
    while time.time() < deadline:
        req = urllib.request.Request(f"{APP}/api/sb/rest/v1/blog_posts?select=id")
        req.add_header("Cookie", f"{SESSION_COOKIE}=" + "0" * 64)
        with contextlib.suppress(Exception):
            urllib.request.urlopen(req, timeout=15).read()
        if _d1_files():
            return
        time.sleep(0.3)
    raise RuntimeError(f"no D1 database sqlite appeared under {PERSIST}")


def _seed() -> None:
    """Apply the auth migration, then insert ONE live session with a cached token."""
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
                SESSION_HANDLE,
                "user-csrf",
                "mock-refresh",
                now,
                now + 400 * 24 * 3600 * 1000,
                SESSION_ACCESS,
                now + 3600 * 1000,
            ),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    # FAIL, do not skip: a skipped security suite is indistinguishable from a
    # passing one in CI.
    require_toolchain()

    global APP_PORT, MOCK_PORT, APP, MOCK_URL, PERSIST
    claimed: set[int] = set()
    APP_PORT = pick_free_port(APP_PORT, claimed)
    MOCK_PORT = pick_free_port(MOCK_PORT, claimed)
    assert APP_PORT != MOCK_PORT
    APP = f"http://127.0.0.1:{APP_PORT}"
    MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"
    PERSIST = tmp_path_factory.mktemp("csrf-guard-d1")

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
            "--persist-to", str(PERSIST),
            "-b", f"SUPABASE_URL={MOCK_URL}",
            "-b", "SUPABASE_ANON_KEY=mock-anon-key",
            "-b", f"APP_ORIGIN={APP}",
            # #3529: `/api/v1` answers 503 `proxy_not_configured` BEFORE it
            # reaches the guard, so without this binding the route's origin-layer
            # cases would assert against that 503 and never exercise the guard at
            # all. The value points at the mock so the session-carrying positive
            # control can observe a forwarded request; the guard cases below are
            # refused (403) or refused by their own auth (401) before forwarding.
            "-b", f"API_ORIGIN={MOCK_URL}",
        ],
        cwd=str(DASHBOARD_DIR),
    )
    if not _wait(APP_PORT):
        app.stop()
        mock.stop()
        pytest.fail("pages dev failed to start")
    time.sleep(2.5)
    _warm_d1()
    _seed()

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
    # #3529 class 3 — the BFF *session* endpoint and the other session-gated
    # mutations. The guard was RETROFITTED onto these in the #4104 review (the
    # cookie is same-SITE, so a sibling subdomain's forged request arrives WITH
    # it attached) and nothing asserted it bit at any of them.
    ("POST", "/api/session", None),
    ("POST", "/api/provision", {}),
    # #3529 class 3 — the recovery confirmation MINTS a session, so the forge
    # needs no cookie at all and SameSite can never have protected it.
    ("POST", "/auth/confirm", {}),
    # #3529 class 3 — the blog-api Token Handler carries the full guard because
    # every one of its bodies is JSON.
    ("POST", "/blog/api/posts", {}),
]

# The two generic proxies carry the ORIGIN layer ONLY: they forward the caller's
# Content-Type verbatim (an arbitrary API body may not be JSON), so the
# media-type layer would answer 415 to a legitimate call. That layer is
# therefore the whole CSRF protection on them, and nothing asserted it bit on
# either route until #3529 — `guardOrigin` was added in the same #4104 review and
# left untested. Paths are spelled at the wildcard so one case covers the route.
ORIGIN_ONLY_ROUTES = [
    ("POST", "/api/v1/teams"),
    ("PUT", "/api/v1/teams/1"),
    ("POST", "/api/sb/rest/v1/blog_posts"),
    ("DELETE", "/api/sb/rest/v1/blog_posts?id=eq.1"),
]

#: The upstream path each origin-only route reaches on the FAR side of the guard.
#: `/api/v1` forwards straight to the data path, while `/api/sb` consults the
#: `is_admin` RPC before it proxies anything — so that RPC is the first (and, for
#: a legitimate session against this mock, the only) upstream call it makes.
PROXY_UPSTREAM = {
    "/api/v1/teams": "/v1/teams",
    "/api/v1/teams/1": "/v1/teams/1",
    "/api/sb/rest/v1/blog_posts": "/rest/v1/rpc/is_admin",
    "/api/sb/rest/v1/blog_posts?id=eq.1": "/rest/v1/rpc/is_admin",
}


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


# ---------------------------------------------------------------------------
# #3529 class 3 — the origin layer on the generic proxies
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method,path", ORIGIN_ONLY_ROUTES)
def test_origin_only_proxy_routes_refuse_a_cross_origin_state_change(stack, method, path):
    """A forged state change from a sibling subdomain must be refused, not proxied.

    `SameSite=Lax` is same-SITE, not same-origin: `tortoise.premiselabs.co` (or
    XSS there) reaches both proxies WITH the victim's cookie attached, so the
    cookie is not the protection — `guardOrigin` is, and on these two routes it
    is the ONLY layer. A refusal that still forwarded would be no refusal, so the
    upstream call log must also stay empty.

    The request carries a LIVE session (`SESSION_HANDLE`) on purpose. With no
    resolvable session both routes answer 401 at `readCookie` BEFORE ever
    forwarding, so an empty log would prove nothing about the guard — the
    assertion was unfalsifiable until the case carried a credential the routes
    would accept (see `test_origin_only_proxy_routes_forward_a_session_without_an_origin`,
    the positive control that shows this same log DOES record forwarded traffic).
    """
    _calls()  # reset the upstream log before the forged request
    req = urllib.request.Request(f"{APP}{path}", method=method, data=b"{}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Origin", EVIL_ORIGIN)
    req.add_header("Cookie", f"{SESSION_COOKIE}={SESSION_HANDLE}")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=30) as r:
            status, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode("utf-8", "replace")
    assert status == 403, (
        f"{method} {path} from {EVIL_ORIGIN} must be 403 — the origin layer is "
        f"these proxies' only CSRF protection, got {status} {body}"
    )
    assert json.loads(body)["error"] == "forbidden_origin", body
    reached = _calls()
    assert reached == [], (
        f"{method} {path} reached the upstream from {EVIL_ORIGIN} despite the "
        f"origin refusal: {reached}"
    )


@pytest.mark.parametrize("method,path", ORIGIN_ONLY_ROUTES)
def test_origin_only_proxy_routes_forward_a_session_without_an_origin(stack, method, path):
    """Positive control for the empty upstream log in the cross-origin case.

    With a live session and NO `Origin` the request passes the guard and reaches
    the upstream, so the mock records a call for this route family. Without this
    control an empty log on the forged request would be equally explained by the
    recorder never seeing proxy traffic at all — which is exactly how
    `assert _calls() == []` was decorative before (#3529 review). Both proxies are
    covered because they diverge: `/api/v1` forwards straight to the data path,
    `/api/sb` resolves `is_admin` first.
    """
    _calls()  # reset the upstream log before the legitimate request
    req = urllib.request.Request(f"{APP}{path}", method=method, data=b"{}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Cookie", f"{SESSION_COOKIE}={SESSION_HANDLE}")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=30) as r:
            status, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode("utf-8", "replace")
    recorded = [c.get("path") for c in _calls(reset=False)]
    assert PROXY_UPSTREAM[path] in recorded, (
        f"{method} {path} with a live session and no Origin did not reach "
        f"{PROXY_UPSTREAM[path]} — the recorder cannot observe proxy traffic, so "
        f"the empty-log assertion in "
        f"test_origin_only_proxy_routes_refuse_a_cross_origin_state_change is "
        f"vacuous. got {status} {body}; recorded {recorded}"
    )


@pytest.mark.parametrize("method,path", ORIGIN_ONLY_ROUTES)
def test_origin_only_proxy_routes_allow_an_absent_origin(stack, method, path):
    """The defence must not be a blanket reject.

    A browser cannot omit `Origin` on a cross-site form post (it would have sent
    one), and genuine non-browser callers omit it. Such a request must get PAST
    the guard and be refused by the route's own auth logic instead — 401 for the
    missing session cookie, never `forbidden_origin`.
    """
    req = urllib.request.Request(f"{APP}{path}", method=method, data=b"{}")
    req.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=30) as r:
            status, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode("utf-8", "replace")
    assert status == 401, (
        f"{method} {path} with no Origin and no session must be refused by the "
        f"route's own auth with 401, not by the CSRF guard: got {status} {body}"
    )
    assert json.loads(body)["error"] == "not_signed_in", body


# ---------------------------------------------------------------------------
# STRUCTURAL PARITY — every guarded route is a tested route (#3529 class 3)
# ---------------------------------------------------------------------------
# The behavioural cases above only cover the routes someone remembered to list,
# which is the exact shape of the #4104 defect: the guard was added to the
# session-gated mutations in review, and nothing mechanical would have noticed a
# route left out — or a route that quietly LOSES its guard in a later refactor.
# The scan derives the guarded-route set FROM THE SOURCE, and three assertions
# together make it EQUAL the set the cases here exercise, in BOTH directions:
#   source == GUARDED_ROUTE_PATTERNS          (test_every_guarded_route_...)
#   case paths ⊆ GUARDED_ROUTE_PATTERNS       (test_each_case_path_lands_...)
#   GUARDED_ROUTE_PATTERNS ⊆ case paths       (test_every_declared_guarded_pattern_...)
# Each is needed. With only the first two, a pattern that is declared and scanned
# but exercised by ZERO cases stays green — delete the case, keep the constant,
# and nothing reddens.
FUNCTIONS_DIR = REPO_ROOT / "website" / "apps" / "dashboard" / "functions"
# A CALL site, not a mention: the `import { guardOrigin } from "..."` line also
# contains the identifier, so a bare-name regex counts a route whose CALL was
# removed as still guarded. Requiring the opening paren is what makes the scan
# measure the guard actually running.
_GUARD_CALL_RE = re.compile(r"\bguard(?:StateChangingRequest|Origin)\s*\(")

#: The route PATTERN each guarded source file serves. A `[[path]]` catch-all
#: becomes `/*`.
GUARDED_ROUTE_PATTERNS = {
    "/auth/password",
    "/auth/signup",
    "/auth/api-key",
    "/auth/reset",
    "/auth/resend",
    "/auth/set-email",
    "/auth/update-password",
    "/auth/link",
    "/auth/confirm",
    "/api/profile",
    "/api/session",
    "/api/provision",
    "/blog/api/*",
    "/api/v1/*",
    "/api/sb/*",
}


def _guarded_route_patterns_from_source() -> set[str]:
    """Every route file that calls a CSRF guard, as a route pattern."""
    found: set[str] = set()
    for path in sorted(FUNCTIONS_DIR.rglob("*.ts")):
        rel = path.relative_to(FUNCTIONS_DIR)
        if rel.parts[0] == "_shared":
            continue  # the guard's own module — not a route
        if not _GUARD_CALL_RE.search(path.read_text(encoding="utf-8")):
            continue
        route = "/" + "/".join(rel.parts)[: -len(".ts")]
        found.add(route.replace("/[[path]]", "/*"))
    return found


def test_the_guard_scan_matches_call_sites_not_mentions() -> None:
    """`_GUARD_CALL_RE` must measure the guard RUNNING, not an import.

    Dropping `guardOrigin(request, env)` while leaving
    `import { guardOrigin } from "../../_shared/auth/csrf"` in place is
    precisely the refactor this scan exists to catch. A mention-matching regex
    counts that route as guarded and the parity test stays green.
    These two assertions lock the call-site semantics in. Pure source-level: no
    wrangler stack is needed.
    """
    assert _GUARD_CALL_RE.search(
        'import { guardOrigin } from "../../_shared/auth/csrf";'
    ) is None, "an import alone must not count as a guard call site"
    assert _GUARD_CALL_RE.search(
        'import { guardStateChangingRequest } from "../_shared/auth/csrf";'
    ) is None, "an import alone must not count as a guard call site"
    assert _GUARD_CALL_RE.search("const csrf = guardOrigin(request, env);"), (
        "a real guardOrigin(...) call must be detected"
    )
    assert _GUARD_CALL_RE.search(
        "const csrf = guardStateChangingRequest(ctx.request, ctx.env);"
    ), "a real guardStateChangingRequest(...) call must be detected"


def test_every_guarded_route_is_exercised_by_a_case_here() -> None:
    """The source side of the parity — this is `source == GUARDED_ROUTE_PATTERNS`.

    It is not on its own the bidirectional claim: it says nothing about the case
    set, which the two tests below pin (`case paths ⊆ patterns` and
    `patterns ⊆ case paths`). Together the three hold source == declared ==
    exercised.
    """
    found = _guarded_route_patterns_from_source()
    assert found == GUARDED_ROUTE_PATTERNS, (
        "the source's guarded-route set and the declared registry "
        "have drifted. A route that adds a CSRF guard, or one that LOSES its "
        "guard, must be reconciled deliberately (#3529 class 3).\n"
        f"  in source but not declared: {sorted(found - GUARDED_ROUTE_PATTERNS)}\n"
        f"  declared but not in source: {sorted(GUARDED_ROUTE_PATTERNS - found)}"
    )


def test_the_guarded_route_scan_is_not_vacuous() -> None:
    """A scan that finds nothing would make the parity test pass vacuously."""
    found = _guarded_route_patterns_from_source()
    assert len(found) >= 14, (
        f"the scan found only {len(found)} guarded routes ({sorted(found)}) — a "
        "moved directory or a broken glob makes the parity test meaningless"
    )


def _case_paths() -> set[str]:
    """Every route path a behavioural case in this module exercises."""
    return {p for _, p, _ in GUARDED_ROUTES} | {p for _, p in ORIGIN_ONLY_ROUTES}


def _pattern_matches(pattern: str, path: str) -> bool:
    """A case path lands on a declared pattern (`/*` covers the subtree)."""
    return path == pattern or (pattern.endswith("/*") and path.startswith(pattern[:-1]))


@pytest.mark.parametrize("path", sorted(_case_paths()))
def test_each_case_path_lands_on_a_declared_guarded_pattern(path: str) -> None:
    """Direction 1 — every case path matches a declared guarded pattern."""
    assert any(
        _pattern_matches(pattern, path) for pattern in GUARDED_ROUTE_PATTERNS
    ), f"{path} is exercised by a case but matches no declared guarded pattern"


@pytest.mark.parametrize("pattern", sorted(GUARDED_ROUTE_PATTERNS))
def test_every_declared_guarded_pattern_is_exercised_by_a_case(pattern: str) -> None:
    """Direction 2 — every declared pattern is matched by at least one case.

    Without this, the parity is one-directional: delete
    `("POST", "/api/session", None)` from `GUARDED_ROUTES` (leaving the source
    untouched) and the source scan still equals the registry, every remaining
    case path still lands on a declared pattern, and every structural test stays
    green — while `/api/session`'s guard call site is exercised by zero cases.
    That is the #4104 shape arrived at from the side direction 1 does not see.
    """
    assert any(
        _pattern_matches(pattern, path) for path in _case_paths()
    ), (
        f"{pattern} is declared guarded and found in the route source, but no "
        f"behavioural case exercises it — a guard with no case can be dropped "
        f"silently (#3529 class 3). Case paths present: {sorted(_case_paths())}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
