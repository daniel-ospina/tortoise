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

import ipaddress
import json
import os
import re
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


# ── #2731/#2744: drive the LOCAL committed-dist preview, never prod ──
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
            "Start BOTH before running:\n"
            "  cd website/apps/dashboard && npx wrangler@4 pages dev dist --port 8790\n"
            "  cd website && npx wrangler@4 pages dev . --port 8788\n"
            "Unreachable:\n"
            + "\n".join(f"  - {f}" for f in failures),
            returncode=1,
        )


def _seed_local_session_cookie(page: Page, user_id: str,
                              session: dict | None = None,
                              parent_domain: bool = True) -> None:
    """Seed ``sb-tortoise-auth-token`` for the LOCAL preview origin (#2731).

    The browser never sends a ``.premiselabs.co``-domain cookie to
    ``127.0.0.1``, so the host-only loopback cookie is what the local app's
    mount gate actually reads (host-conditional ``domainAttr()``/``secureAttr()``
    in main.jsx make the loopback cookie domain-less and Secure-less by design).
    Seeding by ``url`` (not ``domain``) keeps IPv6 loopback (``[::1]``) usable —
    Playwright needs the bracketed form, which ``urlparse().hostname`` strips
    (#2731 review P2). The prod parent-domain cookie is seeded as well so any
    intercepted prod-origin subresource/redirect stays session-coherent — the
    DOCUMENT is always loaded from the local preview, never prod.

    #2744: ``session`` lets a caller seed a CUSTOM session shape (the sibling
    specs carried bespoke dicts — user_metadata/display_name/tier variants).
    When ``session`` is given, ``user_id`` is IGNORED (the dict's own
    ``user.id`` is what the cookie carries); otherwise the standard
    ``_session_json(user_id)`` is used. ``parent_domain`` (default True) seeds
    the ``.premiselabs.co`` cookie as well; pass False when the spec asserts a
    landing on the /auth page immediately after a session clear (a still-valid
    parent-domain session would make the intercepted ``/auth`` page's
    valid-session gate bounce straight back to the dashboard).
    """
    value = urllib.parse.quote(json.dumps(session if session is not None else _session_json(user_id)))
    cookies = [{"name": "sb-tortoise-auth-token", "value": value, "url": DASHBOARD_URL}]
    if parent_domain:
        cookies.append({"name": "sb-tortoise-auth-token", "value": value,
                        "domain": ".premiselabs.co", "path": "/"})
    page.context.add_cookies(cookies)


def _goto_local_dashboard(page: Page) -> None:
    """Load the app DOCUMENT from the local committed-dist preview (#2731)."""
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
                       team_row=None, billing_routes=False) -> None:
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
    base_team_row = {"team_id": "team_loop", "name": "Loop Test", "tier": "free",
                     "max_users": 5, "max_graphs": 5, "graph_size_cap": 10000,
                     "ops_allowance": 1000, "email": "loop@premise-labs.dev"}
    team_row = {**base_team_row, **team_row} if team_row else base_team_row

    def handle(route):
        url = route.request.url
        if url.startswith(API_HOST):
            # #1828: loadAll pins ?team_id= on overview reads — match on the
            # query-stripped path so /v1/team/keys?team_id=… still resolves.
            path = url.split("?", 1)[0]
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
            if path.endswith("/v1/teams") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([team_row]))
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
                              body=json.dumps(team_row))
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
    page.add_init_script("localStorage.setItem('tortoise_beta_access','1');")  # TEMP beta-gate unlock (#beta-gate)
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
