"""#3806 — ship-test instrument, browser-executing half.

This module runs the instrument's three assertions in a REAL browser against
the deployment's OWN client bundle (the freshly built ``dist``), with the API
boundary stubbed at HTTP so the run is deterministic and CI-runnable. The
per-deploy live walk against the deployed app is
``tools/ship_test_onboarding.py`` (no stubs); the fast guard tests for the
connection-claim probe are ``tests/test_ship_test_onboarding.py``.

WHAT IT EXECUTES (never a source scan):

1. ``test_front_door_reaches_signup_in_a_clean_browser`` — the REAL
   ``website/signup.html`` served as ``/auth``, loaded in a CLEAN context (no
   session, no beta-gate flag). The signup CTA must be the element a
   top-of-stack click lands on (``document.elementFromPoint``), which is the
   #3781 overlay class a source scan cannot see.
2. ``test_no_surface_claims_a_connection_before_the_server_observed_one`` —
   the REAL built dashboard in a real browser, walked to the wizard's final
   screen with the server projection recording NO ``harness-connected`` edge.
   The screen must state the observed negative, and the instrument's ``judge``
   must pass.
3. ``test_reentry_card_never_claims_a_connection_before_the_first_memory`` —
   the pre-first-point surface a brand-new user actually sees (no Overview
   grid yet) makes no connection claim.
4. ``test_overview_shows_not_connected_when_the_projection_has_no_observed_edge``
   / ``test_connection_appears_once_the_server_projection_observed_it`` — the
   real Overview grid with/without the observed edge ("No connection observed yet" /
   "Connected ✓"), both through ``judge``.
5. ``test_overview_reports_unavailable_when_the_state_read_fails`` — a failed
   read renders the honest unavailable card, and ``judge`` still passes.
6. ``test_client_never_asserts_harness_connected_on_the_wire`` — the whole
   wizard walk with the API boundary recording every checkpoint POST AND every
   state PATCH: the client must issue NO ``harness-connected`` write, observed
   on the wire rather than inferred from text. A positive control (the fork
   checkpoint) proves the capture channel was live.
7. ``test_guard_reds_on_the_defect_and_greens_on_the_pristine_bundle`` —
   RED/GREEN mutation evidence: a locally-served copy of the deployment's OWN
   bundle with the connection derivation flipped to always-connected must make
   the instrument's negative assertion FAIL; the pristine bundle must pass,
   proving the guard is not vacuously green.

Opt-in: ``RUN_DASHBOARD_E2E=1`` (repo convention). This module starts the local
previews itself when they are not already serving, so it needs no wrangler boot
step; the dashboard server serves a built ``dist`` (run ``npm run build`` in
``website/apps/dashboard`` first, as CI does). Set ``SHIP_TEST_DIST`` to point
it at a different ``dist`` directory.

Run::

    RUN_DASHBOARD_E2E=1 TORTOISE_TEST_CARVE_OUT=1 \
      python -m pytest tests/e2e/test_ship_test_onboarding.py -v
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import re
import shutil
import socket
import tempfile
import threading
import urllib.parse
from pathlib import Path
from typing import ClassVar

import pytest
from playwright.sync_api import Page, expect

# Gate BEFORE the sibling-module imports: those modules re-run their own
# module-level skip, which would otherwise report a misleading skip reason.
if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("ship-test e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

from tests.e2e.test_dashboard_onboarding import (
    DURABLE_ROW,
    _goto_local_dashboard,
    _walk_to_connect,
    _wire,
)
from tests.e2e.test_session_login_flow import (
    APP_HOST,
    AUTH_HOST,
    AUTH_ORIGIN,
    DASHBOARD_URL,
    _bff_path,
    _is_bff_api,
    _preflight_local_servers,
    _seed_local_session_cookie,
    _session_json,
)
from tools.ship_test_onboarding import (
    ABSENT,
    CONNECTED,
    NOT_CONNECTED,
    UNAVAILABLE,
    claims_connection,
    front_door_probe,
    judge,
    read_connection_surface,
    server_observed,
)

REPO = Path(__file__).resolve().parent.parent.parent
SITE_DIR = REPO / "website"
DIST_DIR = Path(os.environ.get("SHIP_TEST_DIST", REPO / "website/apps/dashboard/dist"))

# The honest negative projection: the org exists, the wizard ran, but the server
# has observed NO agent write — no `harness-connected` edge.
UNOBSERVED_PROJECTION = {
    "org_id": "team_o", "status": "active", "onboarding_complete": False,
    "completed_steps": ["team-named"],
}
# A server-observed connection (a real MCP write auto-files the edge).
OBSERVED_PROJECTION = {
    "org_id": "team_o", "status": "active", "onboarding_complete": False,
    "completed_steps": ["team-named", "harness-connected"],
}
# The Overview's calm grid (the connection card's only home) renders once the
# org has a memory — the pre-first-point state is the re-entry card instead.
POINT_COUNT_WITH_OVERVIEW = 1


# ── local preview servers ───────────────────────────────────────────────────
# The repo's other dashboard specs boot wrangler by hand. This module starts a
# tiny static pair instead — still serving the deployment's OWN files, but
# deterministic and port-adjustable, which is what lets the mutation test point
# the dashboard server at a mutated COPY of the real bundle.
class _Handler(http.server.SimpleHTTPRequestHandler):
    routes: ClassVar[dict[str, str]] = {}
    # Same-origin BFF routes the app calls while it boots. A static server's 404
    # is `unavailable`, not `signed-out`, so the client gate cannot act on it;
    # `/api/session` must answer a real STATUS (the app-origin handlers below
    # carry it) or a clean browser renders the retry card instead of the page
    # under test.
    bff: ClassVar[dict[str, tuple[int, dict]]] = {}

    def __init__(self, *a, directory=None, **kw):
        super().__init__(*a, directory=directory, **kw)

    def do_GET(self):  # SimpleHTTPRequestHandler's own spelling
        route = self.bff.get(urllib.parse.urlparse(self.path).path)
        if route is None:
            super().do_GET()
            return
        status, payload = route
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def translate_path(self, path: str) -> str:
        target = self.routes.get(urllib.parse.urlparse(path).path)
        if target:
            return str(Path(self.directory) / target)
        return super().translate_path(path)

    def log_message(self, *a):  # keep the test output clean
        pass


class _AuthHandler(_Handler):
    # #4054: the auth surface is the APP origin's — `/auth` is the moved
    # `signup.html` (served from the dashboard dist, not the marketing site),
    # and the page's session probe asks `/api/session`. A clean browser must get
    # a 401 there ("not signed in"), never a 404 ("store unreachable") — the
    # latter would render the retry card and hide the signup CTA this suite
    # asserts is reachable.
    routes: ClassVar[dict[str, str]] = {"/auth": "signup.html"}
    bff: ClassVar[dict[str, tuple[int, dict]]] = {
        "/api/session": (401, {"error": "not_signed_in"}),
    }


def _port_serving(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _serve(port: int, directory: Path, handler) -> http.server.ThreadingHTTPServer | None:
    if _port_serving(port):
        return None  # an existing preview (wrangler or a sibling) owns the port
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _serve_ephemeral(directory: Path, handler) -> tuple[http.server.ThreadingHTTPServer, str]:
    """Serve `directory` on a free port THIS module owns.

    The front-door assertion must not depend on whatever else holds :8788:
    the port is shared with sibling specs and a local wrangler, and a foreign
    server there (a sibling lane's dashboard preview) made this test read the
    wrong site. An owned ephemeral server removes that coupling entirely.
    """
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


# The auth site URL this module serves itself (set by `_preview_servers`).
OWN_AUTH_URL = ""


@pytest.fixture(scope="module", autouse=True)
def _preview_servers():
    """Serve ``website/`` and the dashboard dist, then run the repo's preflight
    so a missing dashboard preview fails fast with one clear error."""
    global OWN_AUTH_URL
    if not (DIST_DIR / "index.html").exists():
        pytest.exit(
            f"ship-test e2e: no built dashboard at {DIST_DIR} — run "
            "`cd website/apps/dashboard && npm run build` first (CI does this).",
            returncode=1,
        )
    started = [
        s for s in (
            # The dashboard preview on the harness port (started only when free;
            # CI's wrangler already serves DIST_DIR there).
            _serve(int(urllib.parse.urlparse(DASHBOARD_URL).port or 8790), DIST_DIR,
                   functools.partial(_Handler, directory=str(DIST_DIR))),
            # Also fill the auth port when free, for the shared wizard helpers.
            _serve(int(urllib.parse.urlparse(AUTH_ORIGIN).port or 8788), SITE_DIR,
                   functools.partial(_AuthHandler, directory=str(SITE_DIR))),
        ) if s is not None
    ]
    # ... and ALWAYS serve the auth site on a port this module owns, so the
    # front-door assertion is independent of the shared port's occupant.
    # #4054: the auth page moved to the dashboard project, so the owned server
    # serves DIST_DIR (where `/auth` -> signup.html actually lives) — serving
    # `website/` would 404 the CTA the assertion exists to find.
    own_srv, OWN_AUTH_URL = _serve_ephemeral(
        DIST_DIR, functools.partial(_AuthHandler, directory=str(DIST_DIR)))
    started.append(own_srv)
    _preflight_local_servers()
    yield
    for s in started:
        s.shutdown()
        s.server_close()


# ── the API boundary (server truth, stubbed at HTTP) ────────────────────────
def _api_route(route, projection: dict | None, point_count: int,
               state_status: int = 200) -> bool:
    """Answer the dashboard's API calls from `projection` + a team row. Returns
    True when handled. Unknown API paths get a deterministic 401 so the app
    shell renders without a real network round trip."""
    url = route.request.url
    if not _is_bff_api(url):
        return False
    path = _bff_path(url)
    if path.endswith("/v1/onboarding/state") and route.request.method == "GET":
        if state_status != 200:
            route.fulfill(status=state_status, content_type="application/json",
                          body=json.dumps({"detail": "state read failed"}))
            return True
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"onboarding": projection}))
        return True
    if path.endswith("/v1/organizations"):
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps([{"org_id": "team_o", "name": "Ship Test", "role": "owner"}]))
        return True
    if path.endswith("/v1/team") or path.endswith("/v1/team/"):
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"org_id": "team_o", "name": "Ship Test", "tier": "free",
                                       "graph_ready": True, "point_count": point_count,
                                       "subscription_status": "active"}))
        return True
    if path.endswith("/v1/team/keys"):
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"keys": []}))
        return True
    if path.endswith("/v1/sessions") or path.endswith("/backups"):
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"sessions": [], "backups": []}))
        return True
    route.fulfill(status=401, content_type="application/json",
                  body=json.dumps({"detail": "unauthorized"}))
    return True


def _wire_ship(page: Page, projection: dict | None, *,
               point_count: int = POINT_COUNT_WITH_OVERVIEW,
               state_status: int = 200) -> None:
    def handle(route):
        if _api_route(route, projection, point_count, state_status):
            return
        url = route.request.url
        if url.startswith(AUTH_HOST) or url.startswith(APP_HOST):
            local = (AUTH_ORIGIN if url.startswith(AUTH_HOST) else DASHBOARD_URL).rstrip("/") \
                + url[len(AUTH_HOST if url.startswith(AUTH_HOST) else APP_HOST):]
            resp = page.request.get(local)
            ctype = "application/javascript" if local.endswith(".js") else "text/html"
            route.fulfill(status=resp.status, content_type=ctype, body=resp.body())
            return
        route.continue_()

    page.route("**/*", handle)


def _seed_ship_session(page: Page, user_id: str, url: str | None = None) -> None:
    if url is None:
        _seed_local_session_cookie(page, user_id, _session_json(user_id))
        return
    # An ephemeral-port base needs its own host-only cookie.
    page.context.add_cookies([{
        "name": "sb-tortoise-auth-token",
        "value": urllib.parse.quote(json.dumps(_session_json(user_id))),
        "url": url,
    }])


def _settle_overview(page: Page) -> str:
    """Wait for the connection surface, then resolve it with the instrument's
    own reader (the code the live walk ships)."""
    expect(page.locator('[aria-label="Connection status"]').first).to_be_visible(timeout=20_000)
    return read_connection_surface(page)


# ── 1. the front door, in a clean browser ───────────────────────────────────
def test_front_door_reaches_signup_in_a_clean_browser(page: Page) -> None:
    """Assertion 1. A brand-new visitor's browser (no session, no beta-gate
    flag) must reach a signup CTA that a click actually lands on. The #3781
    regression is a full-viewport overlay that returns ``elementFromPoint``
    itself — invisible to every local test, caught here."""
    page.goto(OWN_AUTH_URL.rstrip("/") + "/auth", wait_until="domcontentloaded", timeout=30_000)
    # Clean-context witnesses — the retired gate appeared as a CLASS overlay
    # backed by a localStorage flag, so an id-only check (#beta-gate) is a
    # false PASS (the repo's attribute-agnostic pin exists for this reason).
    assert page.locator("[class*=beta-gate]").count() == 0, "a beta-gate overlay is back on /auth"
    assert page.evaluate("() => localStorage.getItem('tortoise_beta_access')") is None, \
        "the beta-gate localStorage flag is pre-set on a clean browser"
    assert not any(c["name"].startswith("sb-") and c["name"].endswith("-auth-token")
                   for c in page.context.cookies()), "a session was pre-seeded into a clean browser"
    probe = front_door_probe(page)
    assert probe.get("found"), "the signup CTA (#btn-email) must exist on /auth"
    assert probe["hittable"], (
        f"#3806 assertion 1: the signup CTA is covered — a click lands on "
        f"{probe['top']!r}, not the button (the #3781 overlay class)")
    page.click("#btn-email", timeout=5_000)
    expect(page.locator("#email-modal")).to_be_visible(timeout=5_000)


# ── 2. the honest negative, on the wizard's final screen ────────────────────
def test_no_surface_claims_a_connection_before_the_server_observed_one(page: Page) -> None:
    """Assertion 3, negative direction. The wizard's final screen is the
    surface a just-finished user sees; with no server-observed edge it must
    state the observation, never a connection — and the WHOLE page must carry
    no connected claim (not just the two nodes this test reads)."""
    _seed_ship_session(page, "u-ship-neg")
    _wire(page, role="owner", onboarding_projection=UNOBSERVED_PROJECTION)
    _walk_to_connect(page)
    page.get_by_role("button", name="I've set it up — Continue →").click()
    expect(page.locator("div.done")).to_be_visible(timeout=10_000)

    ui = read_connection_surface(page)
    assert ui == NOT_CONNECTED, (
        f"the final screen resolved to {ui!r}, not the honest negative "
        "(ABSENT here means the walk reached no decidable surface)")
    assert claims_connection(page.inner_text("body")) is False, \
        "a connection claim leaked onto the page with nothing observed"
    verdict = judge(ui, UNOBSERVED_PROJECTION)
    assert verdict.ok, f"#3806 assertion 3 (negative): {verdict.detail}"


# ── 3. the pre-first-point surface a brand-new user sees ────────────────────
def test_reentry_card_never_claims_a_connection_before_the_first_memory(page: Page) -> None:
    """With zero memories the Overview grid is not rendered at all — the
    re-entry card is. That surface must make no connection claim, and the
    instrument must not credit it as a measured honest-negative (its reader
    returns ABSENT, which `expected_surface=False` accepts)."""
    _seed_ship_session(page, "u-ship-reentry")
    _wire_ship(page, UNOBSERVED_PROJECTION, point_count=0)
    _goto_local_dashboard(page)
    expect(page.locator("body")).to_contain_text("Continue setting up", timeout=20_000)
    assert claims_connection(page.inner_text("body")) is False, \
        "the pre-first-memory surface claimed a connection"
    assert read_connection_surface(page) is ABSENT, \
        "no connection surface should resolve on the pre-first-memory Overview"
    assert judge(ABSENT, UNOBSERVED_PROJECTION, expected_surface=False).ok is True


# ── 4. the Overview's own negative and positive ─────────────────────────────
def test_overview_shows_not_connected_when_the_projection_has_no_observed_edge(page: Page) -> None:
    """The real Overview grid (the calm 3-element card) with the server
    projection recording no `harness-connected` edge: the connection card must
    resolve to "No connection observed yet" and the probe must pass."""
    _seed_ship_session(page, "u-ship-ov-neg")
    _wire_ship(page, UNOBSERVED_PROJECTION)
    _goto_local_dashboard(page)
    ui = _settle_overview(page)
    assert ui == NOT_CONNECTED, f"the Overview card resolved to {ui!r}"
    assert claims_connection(page.inner_text("body")) is False, \
        "a connection claim leaked onto the Overview's negative page"
    assert judge(ui, UNOBSERVED_PROJECTION).ok


def test_connection_appears_once_the_server_projection_observed_it(page: Page) -> None:
    """Assertion 3, positive direction — the same client, a projection the
    server marked observed."""
    _seed_ship_session(page, "u-ship-ov-pos")
    _wire_ship(page, OBSERVED_PROJECTION)
    _goto_local_dashboard(page)
    ui = _settle_overview(page)
    assert ui == CONNECTED, f"the server observed the connection; the card resolved to {ui!r}"
    assert judge(ui, OBSERVED_PROJECTION).ok


def test_overview_reports_unavailable_when_the_state_read_fails(page: Page) -> None:
    """A failed ``/v1/onboarding/state`` read must render the honest unavailable
    card (never a fabricated Connected / No connection observed yet), and the guard must
    still pass: unavailable over an unreadable server state is not a claim."""
    _seed_ship_session(page, "u-ship-ov-err")
    _wire_ship(page, None, state_status=500)
    _goto_local_dashboard(page)
    ui = _settle_overview(page)
    assert ui == UNAVAILABLE, f"a failed read resolved to {ui!r}, not the honest unavailable"
    assert judge(ui, None).ok


# ── 5. the client never asserts the connection, observed on the wire ────────
def test_client_never_asserts_harness_connected_on_the_wire(page: Page) -> None:
    """The whole wizard walk with every state-writing request recorded. The
    client may POST only the fork checkpoint (or its `fork_unsure_at` marker) and
    must NEVER write the `harness-connected` step — that edge is the server's
    observation, not the user's click (#3428/#2937). #3913: nor may it write the
    `catalog-presented` step, which it used to render-mark. Observed on the
    wire, not scanned from text."""
    _seed_ship_session(page, "u-ship-wire")
    cap = _wire(page, role="owner", key_rows=[DURABLE_ROW],
                onboarding_projection=UNOBSERVED_PROJECTION)
    _walk_to_connect(page)
    page.get_by_role("button", name="I've set it up — Continue →").click()
    expect(page.locator("div.done")).to_be_visible(timeout=10_000)

    # POSITIVE CONTROL — the capture channel was live: the fork click wrote a
    # checkpoint. Without this, a silently dead capture would make the negative
    # assertion below vacuous (all([]) == []).
    assert any("fork" in json.dumps(c) for c in cap["checkpoint"]), (
        "the checkpoint capture recorded nothing — the wire assertion would be "
        f"vacuous: {cap['checkpoint']}")
    # BOTH state-writing channels, any body shape.
    state_writes = cap["checkpoint"] + cap["state"]
    assert not any("harness-connected" in json.dumps(c) for c in state_writes), \
        f"#3806: the client asserted the connection on the wire: {state_writes}"
    assert server_observed(UNOBSERVED_PROJECTION) is False


# ── 6. RED/GREEN mutation evidence, on the deployment's OWN bundle ──────────
# The guard must go RED on its defect and stay GREEN under a behaviour-identical
# reformat. This mutates a COPY of the real deployed bundle (never the source)
# and serves it, so the executed code is the real client with exactly one
# behavioural change: the connection OBSERVATION phrase flipped to the positive
# one. #3724 made that phrase a single shared constant (the Overview card and
# the wizard step-3 heading read the same source), so the bundle carries it
# ONCE — the anchor is that one literal, and flipping it flips the negative arm
# the probe measures.
_MUTATION_FROM = '"No connection observed yet"'
_MUTATION_TO = '"Connected ✓"'


def _entry_bundle(dist: Path) -> Path:
    """The bundle `dist/index.html` actually loads — a stale leftover
    `assets/index-*.js` (the repo carries one) must not be picked instead."""
    m = re.search(r"assets/(index-[A-Za-z0-9_\-]+\.js)",
                  (dist / "index.html").read_text(encoding="utf-8"))
    assert m, f"dist/index.html at {dist} references no entry bundle"
    js = dist / "assets" / m.group(1)
    assert js.exists(), f"dist/index.html references a missing bundle: {m.group(1)}"
    return js


def _mutated_dist() -> Path:
    """A copy of the real dist with the connection OBSERVATION phrase flipped —
    the exact defect class (#3806): a client that claims a connection the server
    did not observe.

    #3724 made the phrase ONE shared constant (the Overview card and the wizard
    step-3 heading read the same source), so the anchor replaces a single bundle
    literal. The flip is bundle-wide by construction; the MUTATION PROBE's
    verdict is Overview-scoped (it measures the connection card only)."""
    tmp = Path(tempfile.mkdtemp(prefix="ship-test-dist-"))
    shutil.copytree(DIST_DIR, tmp, dirs_exist_ok=True)
    js = _entry_bundle(tmp)
    src = js.read_text(encoding="utf-8")
    # The anchor must be UNIQUE: if the phrase is ever duplicated — a second
    # hand-maintained use of the same literal — `str.replace` would flip EVERY
    # occurrence, and the probe could no longer attribute the observed change to
    # the one connection card it measures. Fail loudly and make the
    # anchor/strategy move with the copy.
    n = src.count(_MUTATION_FROM)
    assert n == 1, (
        f"the mutation anchor {_MUTATION_FROM!r} must appear exactly once in "
        f"{js.name} (found {n}) — n == 0 means either the shipped copy was "
        "reworded (update the anchor to the current literal) or this dist is "
        "stale; n > 1 means the same literal is written twice, so the flip "
        "would land on unknown consumers")
    js.write_text(src.replace(_MUTATION_FROM, _MUTATION_TO), encoding="utf-8")
    return tmp


def _serve_dist(dist: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    srv = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Handler, directory=str(dist)))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


def _probe_overview_claim(page: Page, base: str, projection: dict) -> tuple[str, object]:
    """Load the bundle at `base`, seed a session, and read the connection card
    through the instrument's OWN reader."""
    def handle(route):
        url = route.request.url
        # `base` is a PLAIN static server over a mutated COPY of dist (no
        # Functions, no D1), so the real session gate cannot run there — it is
        # answered directly. The gate's own contract belongs to
        # test_session_login_flow / tests/e2e/auth, not to this mutation probe.
        if urllib.parse.urlsplit(url).path == "/api/session":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"user": {"id": "u-ship-mut",
                                                    "email": "u-ship-mut@premise-labs.dev"}}))
            return
        if _api_route(route, projection, POINT_COUNT_WITH_OVERVIEW):
            return
        route.continue_()

    page.route("**/*", handle)
    page.goto(base, wait_until="domcontentloaded", timeout=30_000)
    ui = _settle_overview(page)
    return ui, judge(ui, projection)


def test_guard_reds_on_the_defect_and_greens_on_the_pristine_bundle(browser) -> None:
    """Execute the guard against the REAL bundle twice: pristine must be GREEN
    (behaviour-identical), the defect-injected copy must be RED."""
    # The GREEN leg must run the SAME bundle the RED leg copies — an external
    # preview on :8790 (a sibling spec's wrangler boot) would otherwise make the
    # two legs exercise different clients.
    entry = _entry_bundle(DIST_DIR)
    probe_ctx = browser.new_context()
    try:
        resp = probe_ctx.request.get(DASHBOARD_URL + "assets/" + entry.name)
        assert resp.status == 200, (
            f"the module preview on {DASHBOARD_URL} is not serving DIST_DIR's entry "
            f"bundle {entry.name} ({resp.status}) — GREEN would not describe the "
            "same client the RED leg mutates")
    finally:
        probe_ctx.close()

    ctx = browser.new_context()
    try:
        ui, verdict = _probe_overview_claim(ctx.new_page(), DASHBOARD_URL, UNOBSERVED_PROJECTION)
        assert ui == NOT_CONNECTED, f"pristine bundle resolved to {ui!r}"
        assert verdict.ok is True, f"the pristine bundle must be GREEN — {verdict.detail}"
    finally:
        ctx.close()

    dist = _mutated_dist()
    srv, base = _serve_dist(dist)
    ctx = browser.new_context()
    try:
        ui, verdict = _probe_overview_claim(ctx.new_page(), base, UNOBSERVED_PROJECTION)
        assert ui == CONNECTED, (
            "the injected defect did not reach the DOM — the mutation must change "
            "behaviour or this test proves nothing")
        assert verdict.ok is False, (
            "a client that claims an unobserved connection must be RED — the guard "
            f"is vacuous (ui={ui!r})")
    finally:
        ctx.close()
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(dist, ignore_errors=True)
