"""#1511 loop-regression e2e — the API-key login loop across BOTH origins.

Harness (pinned in the #1511 plan Task 7):
- Serve the site root (the /auth page) with `wrangler@4 pages dev . --port 8788`
  from website/.
- Serve the dashboard dist with `wrangler@4 pages dev dist --port 8790`
  from website/apps/dashboard/.
- #2744: every DOCUMENT load ORIGINATES from the LOCAL preview (`:8790`
  dashboard, `:8788` auth); a prod-origin URL is used only as an explicitly
  ASSERTED redirect target (`test_no_cookie_dashboard_redirects_to_auth`),
  whose content the route handler serves from :8788. The prod hosts stay
  intercepted to rewrite app-emitted prod-origin redirects/subresources back
  to the preview: `https://tortoise.premiselabs.co/**` → the :8788 server,
  `https://app.premiselabs.co/**` → the :8790 server. On the loopback origin
  the local /auth page writes a host-only session cookie that the loopback
  dashboard reads (`domainAttr()`/`secureAttr()` are host-conditional);
  ``_seed_local_session_cookie`` PRE-SEEDS the prod parent-domain
  `.premiselabs.co` cookie (unless ``parent_domain=False``) only so intercepted
  prod-origin redirects/subresources stay session-coherent.
- The exchange (`POST https://api.premiselabs.co/v1/session/login`) is mocked;
  `https://api.premiselabs.co/**` catches the dashboard's other API calls with
  a benign 401 so the app shell renders deterministically.
- Opt-in: RUN_DASHBOARD_E2E=1 (mirrors RUN_LEGAL_E2E).

Flows (the user's #1511 acceptance):
(a) paste tt_ key on /auth → exchange 200 → session cookie written →
    dashboard renders (the loop WORKS).
(b) no cookie → dashboard instantly redirects to /auth.
(c) anon-team exchange error (403 ANON_TEAM_NO_OWNER) → tt_claim_pending set
    → redirected to the LOCAL dashboard ?claim=1 → claim-paste shows.
"""
from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("dashboard e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

ROOT = Path(__file__).resolve().parent.parent.parent

AUTH_ORIGIN = os.environ.get("DASHBOARD_AUTH_BASE", "http://127.0.0.1:8788")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8790/")

AUTH_HOST = "https://tortoise.premiselabs.co"
APP_HOST = "https://app.premiselabs.co"
API_HOST = "https://api.premiselabs.co"

# ── #3501/#4054 BFF session seam ────────────────────────────────────────────
# The dashboard's session is an OPAQUE handle in the HttpOnly `__Host-session`
# cookie, validated SERVER-SIDE by `/api/session` against the D1 `sessions`
# table. The legacy JS-readable `sb-tortoise-auth-token` is ignored by the gate
# (the dashboard gate no longer reads any client-held token), so seeding it alone made the
# app answer 401 and bounce every suite to /auth. These specs therefore seed the
# row the gate actually reads, in the D1 the `:8790` preview serves.
SESSION_COOKIE = "__Host-session"
DASHBOARD_DIR = ROOT / "website" / "apps" / "dashboard"
AUTH_MIGRATION = ROOT / "website" / "migrations" / "0001_auth_sessions.sql"
# The dashboard calls its OWN origin (main.jsx: `const API_BASE = '/api'`), so
# the app's API namespace is the `/api/` PATH — matched on any origin, because
# the mutation probe serves a copy of the bundle from an ephemeral port. The
# absolute upstream host is still accepted: intercepted prod-origin
# subresources use it, and matching neither is why the suites' fixture rows
# stopped arriving after the BFF move.
GATE_PATH = "/api/session"


def _is_bff_api(url: str) -> bool:
    """True when ``url`` belongs to the API surface the harness answers.

    The harness mocks the app's API namespace, not the proxy's routing table: a
    render suite stubs whatever the CLIENT requests, so it stays correct when
    the client's path shape changes. The SESSION GATE is deliberately excluded
    (``/api/session``) — it must stay REAL, because the D1-seeded
    `__Host-session` handle is exactly what these fixtures prove works.
    """
    if url.startswith(API_HOST):
        return True
    path = urllib.parse.urlsplit(url).path
    return path.startswith("/api/") and path != GATE_PATH


def _bff_path(url: str) -> str:
    """The API path in the shape the mock branches match (``/v1/...``, ``/backups``).

    The migrated dashboard requests the SAME-ORIGIN namespace
    (``/api/v1/organizations``); the branches were written against the
    pre-#4054 absolute shape (``https://api.premiselabs.co/v1/organizations``),
    where the client's ``/api`` prefix did not exist. Stripping that one prefix
    makes both shapes match the SAME branches — instead of rewriting every
    anchored ``^/v1/...`` regex, which is how a missed anchor silently falls
    through to the deterministic 401.
    """
    path = urllib.parse.urlsplit(url).path
    if path.startswith("/api/"):
        return path[len("/api"):]
    return path


def _local_d1_files() -> list[Path]:
    """Local D1 database files (never `metadata.sqlite` — Miniflare's own index)."""
    return [
        p for p in DASHBOARD_DIR.glob(".wrangler/state/v3/d1/**/*.sqlite")
        if p.name != "metadata.sqlite"
    ]


def _warm_local_d1(timeout: float = 30.0) -> None:
    """Force Miniflare to MATERIALISE the bound D1 database file.

    `--d1 SESSIONS` only declares the binding: the SQLite file appears on the
    first D1 ACCESS, not at boot. Globbing for it first (the obvious order)
    therefore works only on a machine where an earlier run already created it,
    and times out on a clean CI runner. One `/api/session` read with an unknown
    handle IS a D1 access — it creates the file on the way. Its status is not
    the point and depends on the starting state: 503 while the `sessions` table
    does not exist yet (the clean-runner case this warm-up exists for), 401 once
    it does.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        req = urllib.request.Request(
            DASHBOARD_URL.rstrip("/") + "/api/session",
            headers={"Cookie": f"{SESSION_COOKIE}={'0' * 64}"},
        )
        with contextlib.suppress(Exception):
            urllib.request.urlopen(req, timeout=10).read()
        if _local_d1_files():
            return
        time.sleep(0.3)
    raise RuntimeError(
        f"no D1 database sqlite appeared under {DASHBOARD_DIR} — is the "
        "dashboard preview running with `--d1 SESSIONS`?"
    )


def _local_d1_sqlite() -> Path:
    """The D1 database file the `:8790` preview's Functions actually read.

    The workflow boots `wrangler pages dev dist --d1 SESSIONS` from
    `website/apps/dashboard`, so Miniflare's local state lives under that
    project's `.wrangler/state/v3/d1/`. The tree ALSO holds `metadata.sqlite`
    (D1's index plus the cache/observability stores) — seeding one of those
    writes a database the Worker never opens, and the route then 503s
    (`ALTER TABLE sessions` on a DB with no such table) rather than reporting a
    bad seed.
    """
    deadline = time.time() + 30
    while time.time() < deadline:
        files = _local_d1_files()
        if files:
            return max(files, key=lambda p: p.stat().st_mtime)
        time.sleep(0.3)
    raise RuntimeError(
        f"no D1 database sqlite appeared under {DASHBOARD_DIR} — is the "
        "dashboard preview running with `--d1 SESSIONS`?"
    )


def _seed_bff_session(user_id: str) -> str:
    """Create the schema + a LIVE cached-token session row; return the handle.

    A cached `access_token` keeps `getAccessTokenForSession` off the network (it
    only refreshes when the cached value is inside the skew window), so the gate
    resolves without any Supabase binding — which is exactly the contract the
    seed is here to satisfy: a handle that the store positively recognises.
    """
    handle = secrets.token_hex(32)
    now = int(time.time() * 1000)
    _warm_local_d1()
    con = sqlite3.connect(_local_d1_sqlite(), timeout=15)
    try:
        con.executescript(AUTH_MIGRATION.read_text(encoding="utf-8"))
        con.execute(
            "INSERT OR REPLACE INTO sessions "
            "(handle,user_id,refresh_token,revoked,created_at,expires_at,"
            " access_token,access_token_expires_at) VALUES (?,?,?,?,?,?,?,?)",
            (handle, user_id, "seed-refresh-token", 0, now, now + 86_400_000,
             "seed-access-token", now + 3_600_000),
        )
        con.commit()
    finally:
        con.close()
    return handle


@pytest.fixture(scope="module", autouse=True)
def _local_preview_servers() -> None:
    """#2731/#2744: fail fast (ONE clear error) when :8788/:8790 are not
    serving. Registered here too so the flow module's own tests get the same
    guard as the two CI specs — without it a missing preview makes the route
    handlers fall through to production and every failure misdiagnoses as an
    app-behavior regression."""
    _preflight_local_servers()


def _session_json(user_id: str = "loop-user") -> dict:
    return {
        "access_token": "loop-fake-access-token",
        "refresh_token": "loop-fake-refresh-token",
        "expires_in": 3600,
        "expires_at": int(time.time()) + 3600,
        "token_type": "bearer",
        "user": {"id": user_id, "email": "loop@premise-labs.dev",
                 "app_metadata": {}, "user_metadata": {}},
    }


def _proxy_body(route, local_url: str, page: Page) -> None:
    """Proxy a local server response with the correct content type (a wrong
    MIME refuses script execution in Chrome)."""
    ctype = "text/html"
    if local_url.endswith(".js"):
        ctype = "application/javascript"
    elif local_url.endswith(".css"):
        ctype = "text/css"
    elif local_url.endswith(".json"):
        ctype = "application/json"
    elif local_url.endswith(".png") or local_url.endswith(".ico"):
        ctype = "image/png"
    resp = page.request.get(local_url)
    route.fulfill(status=resp.status, content_type=ctype, body=resp.body())


# ── #2731/#2744: drive the LOCAL built-dist preview, never prod ──
# The dashboard specs used to navigate the DOCUMENT to the prod origins and
# rely on the ``page.route`` proxy to serve local content under them. When the
# proxy path failed, the request fell through to production and every
# assertion misreported as an app-behavior failure. As of #2744 every
# dashboard/auth DOCUMENT originates from the local preview; the route
# handlers stay for intercepted prod hosts (API_HOST stubs; the AUTH_HOST ->
# :8788 rewrite is required for the app-emitted prod-origin /auth bounce, e.g.
# the dashboard's hardcoded ``https://tortoise.premiselabs.co/auth`` logout
# target; the APP_HOST -> :8790 rewrite is a DEFENSIVE fallback — no migrated
# spec originates a prod-app-origin request).


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """#2731 (review P2): never follow a redirect in the preflight — a local
    preview that 3xx-bounces off-box must not pass a redirect-following check."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _is_local_preview_host(host: str) -> bool:
    """True only for ``localhost`` or a **loopback** IP literal.

    Loopback-only on purpose (#2731 review P2): a guard whose job is to reject
    non-local origins must not accept a DNS name like ``10.evil.com`` (prefix
    match), a LAN host (``10.0.0.5``, ``192.168.1.1``), or the cloud-metadata
    address (``169.254.169.254``). ``urlparse().hostname`` strips the brackets
    off IPv6 literals, so ``::1`` arrives unbracketed here.
    """
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _preflight_local_servers() -> None:
    """Fail fast — ONE clear error — when the local preview servers are not
    serving (#2731). Without this, a missing :8788/:8790 preview makes the
    route handlers fall through to production and every test misdiagnoses as
    an app-behavior failure. Called from a module-scoped autouse fixture in
    every migrated dashboard/auth spec (and the #2731 flow module).

    ``pytest.exit`` (not ``pytest.fail``) is deliberate: a module-scoped autouse
    fixture that fails is re-raised once per collected test, so ``fail`` would
    print the same error 16 times. ``exit`` aborts the session with a single
    message (#2731 review P2)."""
    failures: list[str] = []
    opener = urllib.request.build_opener(_NoRedirect)
    for label, url in (("auth", AUTH_ORIGIN + "/"), ("dashboard", DASHBOARD_URL)):
        try:
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname or ""
        except ValueError:
            # Malformed authority (e.g. an unbalanced IPv6 bracket) — this must
            # not escape as a per-test ValueError; it is a preflight failure.
            failures.append(f"{label} {url} -> unparseable URL")
            continue
        if parsed.scheme not in ("http", "https"):
            failures.append(
                f"{label} {url} -> unsupported scheme {parsed.scheme!r} "
                "(only http/https previews are probed)")
            continue
        if not _is_local_preview_host(host):
            failures.append(
                f"{label} {url} -> non-loopback host {host!r} — refusing to "
                "drive a non-local origin")
            continue
        try:
            with opener.open(url, timeout=10) as resp:
                if resp.status != 200:
                    failures.append(f"{label} {url} -> HTTP {resp.status}")
        except Exception as exc:  # any error means the preview is not serving
            failures.append(f"{label} {url} -> {type(exc).__name__}: {exc}")
    if failures:
        pytest.exit(
            "dashboard e2e: local preview server(s) unreachable — this suite "
            "drives the LOCAL wrangler previews, never production (#2731). "
            "Start BOTH before running (dist/ is a build artifact since "
            "#3775 — build it first or :8790 serves a missing/stale bundle):\n"
            "  cd website/apps/dashboard && npm ci && npm run build\n"
            "  cd website/apps/dashboard && npx wrangler@4 pages dev dist --port 8790\n"
            "  cd website && npx wrangler@4 pages dev . --port 8788\n"
            "Unreachable:\n"
            + "\n".join(f"  - {f}" for f in failures),
            returncode=1,
        )


def _seed_local_session_cookie(page: Page, user_id: str,
                              session: dict | None = None,
                              parent_domain: bool = True) -> None:
    """Seed a LIVE BFF session for the LOCAL preview origin (#2731, #4054).

    What the gate actually requires (read off the real code): an HttpOnly
    `__Host-session=<64-hex handle>` cookie whose handle has a row in the D1
    `sessions` table with `revoked = 0` and a future `expires_at`
    (`functions/api/session.ts` -> `getSession`). This helper writes that row
    into the preview's local D1 and adds the cookie, so `/api/session` answers
    200 and the shell renders.

    The legacy JS-readable `sb-tortoise-auth-token` is STILL seeded on purpose:
    the app must ignore it, and several specs assert the legacy residue has no
    effect. Seeding it is what keeps those assertions meaningful.

    Seeding by ``url`` (not ``domain``) keeps IPv6 loopback (``[::1]``) usable —
    Playwright needs the bracketed form, which ``urlparse().hostname`` strips
    (#2731 review P2). `__Host-` forbids a ``Domain`` attribute, so the BFF
    cookie is host-only by construction; the prod parent-domain legacy cookie is
    still seeded (unless ``parent_domain=False``) so intercepted prod-origin
    subresources stay coherent. Plain-HTTP loopback is fine: Chromium treats
    ``http://127.0.0.1`` as a trustworthy origin and SENDS ``Secure`` cookies
    there. The BFF cookie itself cannot go through ``add_cookies`` (see the
    CDP call below); only the legacy cookie uses it.

    #2744: ``session`` lets a caller seed a CUSTOM session shape (the sibling
    specs carried bespoke dicts — user_metadata/display_name/tier variants).
    When ``session`` is given, ``user_id`` is IGNORED (the dict's own
    ``user.id`` is what the cookie carries); otherwise the standard
    ``_session_json(user_id)`` is used.
    """
    payload = session if session is not None else _session_json(user_id)
    value = urllib.parse.quote(json.dumps(payload))
    bff_user_id = (payload.get("user") or {}).get("id") or user_id
    handle = _seed_bff_session(bff_user_id)
    # `__Host-` rules: Secure, Path=/, NO Domain — enforced by Chromium's cookie
    # store. Playwright's `context.add_cookies` CANNOT set it here: for a plain
    # http URL it force-clears `secure` (verified: a `secure: True` cookie comes
    # back `secure: False`), and a `__Host-` cookie without Secure is rejected
    # outright — so `add_cookies` fails with "Invalid cookie fields" on the
    # loopback preview. CDP's Network.setCookie honours the flag, and Chromium
    # treats `http://127.0.0.1` as a trustworthy origin, so the cookie is stored
    # (Secure, HttpOnly) and sent — no TLS needed.
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.setCookie", {
        "name": SESSION_COOKIE,
        "value": handle,
        "url": DASHBOARD_URL,
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "sameSite": "Lax",
    })
    cookies = [
        {"name": "sb-tortoise-auth-token", "value": value, "url": DASHBOARD_URL},
    ]
    if parent_domain:
        cookies.append({"name": "sb-tortoise-auth-token", "value": value,
                        "domain": ".premiselabs.co", "path": "/"})
    page.context.add_cookies(cookies)


def _goto_local_dashboard(page: Page) -> None:
    """Load the app DOCUMENT from the local built-dist preview (#2731)."""
    page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30_000)
    # #2744: positive evidence in the run log that the DOCUMENT came from the
    # local preview. Print the TARGET (not page.url — a gate redirect can land
    # before this line, and the asserted prod redirect targets are deliberate).
    print(f"[#2744 local-preview] dashboard document ← {DASHBOARD_URL}")


def _goto_local_auth(page: Page) -> None:
    """Load the /auth DOCUMENT from the local site preview (#2744).

    Mirrors :func:`_goto_local_dashboard`: the prod-origin AUTH page is no
    longer used as a document origin. The page is served by the local
    ``wrangler pages dev`` site preview (``AUTH_ORIGIN`` + ``/auth``), and
    ``window.__AUTH_BASE_URL`` is pointed at the same loopback origin so any
    redirect target the page computes stays local (prod-origin redirects that
    a test explicitly ASSERTS keep their own ``__AUTH_BASE_URL``).
    """
    page.add_init_script(f"window.__AUTH_BASE_URL = {json.dumps(AUTH_ORIGIN)};")
    # #2744: keep the post-login redirect on loopback — the local auth page's
    # session write is host-only for 127.0.0.1, so a redirect to the prod app
    # origin would lose it. The seam defaults to the prod origin when unset
    # (no prod behavior change). json.dumps — never manual quoting: the URL is
    # env-derived and a quote would break the injected JS.
    page.add_init_script(
        f"window.__DASHBOARD_BASE_URL = {json.dumps(DASHBOARD_URL.rstrip('/'))};")
    page.goto(AUTH_ORIGIN + "/auth", wait_until="domcontentloaded", timeout=30_000)
    print(f"[#2744 local-preview] auth document ← {AUTH_ORIGIN}/auth")


def _wire_prod_domains(page: Page, exchange_body=None, exchange_status=200,
                       exchange_ctype: str = "application/json",
                       org_row=None, billing_routes=False) -> None:
    """Simulate the prod domains: tortoise → :8788 (auth site),
    app → :8790 (dashboard), api → mocked exchange + a deterministic
    session/team surface so the dashboard app shell renders after a
    successful exchange (the loop: cookie bridges origins → gate passes
    # -> mount effect renders session-only on the JWT (#2167: no mint), no
    #    redirect).

    #1623: ``team_row`` overrides MERGE with the base row (callers pass only
    the fields they want to change — e.g. subscription_status/billing
    fields for the Billing page); ``billing_routes`` adds mocked POST
    /v1/billing/checkout + /v1/billing/portal handlers (returning
    {checkout_url}/{portal_url}) so Upgrade/Manage CTAs resolve instead of
    hitting the 401 fallback.
    """
    base_team_row = {"org_id": "team_loop", "name": "Loop Test", "tier": "free",
                     "max_users": 5, "max_graphs": 5, "graph_size_cap": 10000,
                     "ops_allowance": 1000, "email": "loop@premise-labs.dev"}
    org_row = {**base_team_row, **org_row} if org_row else base_team_row

    def handle(route):
        url = route.request.url
        if _is_bff_api(url):
            # #1828: loadAll pins ?org_id= on overview reads — match on the
            # query-stripped path so /v1/team/keys?org_id=… still resolves.
            path = _bff_path(url)
            if url.endswith("/v1/session/login") and route.request.method == "POST":
                route.fulfill(status=exchange_status,
                              content_type=exchange_ctype,
                              body=json.dumps(exchange_body or {}))
                return
            if path.endswith("/v1/session/key") and route.request.method == "POST":
                # #2167: the dashboard never mints a bootstrap key — the old
                # session-key mint mock (tt_loop_minted_key) is gone. Loud 500
                # so a regression mint fails the loop journey loudly.
                route.fulfill(status=500, content_type="application/json",
                              body=json.dumps({"detail": "#2167 zero-mint tripwire"}))
                return
            if url.endswith("/v1/billing/checkout") and route.request.method == "POST" and billing_routes:
                # #1623: capture the body so tests can assert the price_id.
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"checkout_url": "https://checkout.stripe.com/c/pay/test_123"}))
                return
            if url.endswith("/v1/billing/portal") and route.request.method == "POST" and billing_routes:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"portal_url": "https://billing.stripe.com/p/session/test_123"}))
                return
            if path.endswith("/v1/organizations") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([org_row]))
                return
            if path.endswith("/v1/team/keys"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"keys": []}))
                return
            if path.endswith("/v1/sessions"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"sessions": []}))
                return
            if path.endswith("/backups"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"backups": []}))
                return
            if path.endswith("/v1/team") or path.endswith("/v1/team/"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(org_row))
                return
            # Everything else the dashboard calls — a deterministic 401 so the
            # app shell renders without a real network round trip.
            route.fulfill(status=401, content_type="application/json",
                          body=json.dumps({"detail": "unauthorized"}))
            return
        if url.startswith(AUTH_HOST):
            local = AUTH_ORIGIN + url[len(AUTH_HOST):]
            _proxy_body(route, local, page)
            return
        if url.startswith(APP_HOST):
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            _proxy_body(route, local, page)
            return
        route.continue_()

    page.route("**/*", handle)


def _open_auth(page: Page) -> None:
    # #2744: the /auth DOCUMENT is loaded from the local site preview, never
    # the prod auth origin.
    _goto_local_auth(page)
    # The auth page must NOT bounce (no session) — the four options are visible.
    expect(page.locator("#btn-apikey")).to_be_visible(timeout=15_000)


def _submit_api_key(page: Page, key: str) -> None:
    _open_auth(page)
    page.locator("#btn-apikey").click()
    expect(page.locator("#apikey-modal")).to_be_visible()
    page.locator("#apikey-input").fill(key)
    page.locator("#apikey-form").evaluate(
        "(f) => f.dispatchEvent(new Event('submit', {cancelable: true}))")


def test_api_key_login_writes_cookie_and_dashboard_renders(page: Page) -> None:
    """Flow (a): paste tt_ key → exchange 200 → the loopback session cookie is
    written → the LOCAL dashboard (127.0.0.1:8790) renders.

    #2744: both documents are loopback — the auth page writes a host-only
    127.0.0.1 cookie and the __DASHBOARD_BASE_URL seam keeps the post-exchange
    redirect on the same origin (a prod-app redirect would drop the cookie)."""
    _wire_prod_domains(page, exchange_body=_session_json())
    _submit_api_key(page, "tt_loop_key_abcdef0123456789")
    # The dashboard loads (redirect after the exchange).
    expect(page).to_have_url(re.compile(r"^" + re.escape(DASHBOARD_URL)), timeout=20_000)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=20_000)


def test_no_cookie_dashboard_redirects_to_auth(page: Page) -> None:
    """Flow (b): no session cookie → the dashboard instantly redirects to the
    /auth page (the gate emits the ABSOLUTE target on the app origin).

    #2744: the DASHBOARD document is loaded from the local preview; the
    redirect TARGET is the prod auth origin this test explicitly asserts (the
    issue's "keep the prod-origin redirect target only when the test asserts
    it" carve-out) — the route handler serves the intercepted /auth locally."""
    _wire_prod_domains(page)
    page.add_init_script(
        f"window.__AUTH_BASE_URL = '{AUTH_HOST}';")
    _goto_local_dashboard(page)
    expect(page).to_have_url(re.compile(rf"^{re.escape(AUTH_HOST)}/auth"), timeout=15_000)


def test_anon_team_error_funnels_to_claim(page: Page) -> None:
    """Flow (c): a 403 ANON_TEAM_NO_OWNER from the exchange sets
    tt_claim_pending and redirects to the LOCAL dashboard ?claim=1 — the
    claim-paste screen shows (D2 funnel, no raw key cross-origin)."""
    _wire_prod_domains(page, exchange_status=403,
                       exchange_body={"detail": {"error_code": "ANON_TEAM_NO_OWNER",
                                                 "message": "unclaimed"}})
    _submit_api_key(page, "tt_anon_key_abcdef0123456789")
    expect(page).to_have_url(re.compile(r"^" + re.escape(DASHBOARD_URL) + r"\?claim=1"), timeout=20_000)
    # W1 (#1997 team→Organization rename): the claim-paste card heading is
    # 'Claim your organization' (main.jsx protect-banner).
    expect(page.locator("body")).to_contain_text("Claim your organization", timeout=20_000)
