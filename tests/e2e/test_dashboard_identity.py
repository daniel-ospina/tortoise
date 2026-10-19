"""#1765 dashboard identity surface e2e (RUN_DASHBOARD_E2E opt-in).

Harness (#2809): the SAME two-origin BFF harness the other wired dashboard
specs use — two `wrangler pages dev` previews (site root + dashboard dist)
and a **server-held** session (`__Host-session` handle + a row in the
preview's local D1), NOT the legacy client cookie. Migrated here from the
retired `sb-tortoise-auth-token` seed: after #3501 the app holds no session
client-side, so a seeded cookie can never authenticate and every spec in this
file dead-ended on `/auth`. The API is mocked at the **app origin** (#4054),
not `api.premiselabs.co`, and prod-origin subresources are proxied to the
local previews so an intercepted redirect stays coherent.

Flows:
1. Single-method inventory → recovery banner shows; CTA lands on Profile.
2. Two-method inventory → no banner.
3. linking_available=false → promise-free banner (fail-closed, never
   promises add-methods that can't work).
4. ?link_flow= OAuth return → link-commit POST fires, lands on Profile,
   param stripped.
5. <768px → no horizontal scroll (375px precedent, test_legal_pages.py).
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("dashboard e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

from tests.e2e.auth.bff_test_helpers import (
    d1_sqlite_files,
    dashboard_persist_args,
    ensure_dashboard_dist,
    pick_free_port,
    stop,
    wait_for_port,
)
from tests.e2e.test_session_login_flow import (
    APP_HOST,
    AUTH_HOST,
    AUTH_ORIGIN,
    DASHBOARD_URL,
    _bff_path,
    _is_bff_api,
    _proxy_body,
    _seed_local_session_cookie,
)

ROOT = Path(__file__).resolve().parent.parent.parent
DASHBOARD_DIR = ROOT / "website" / "apps" / "dashboard"
SUPABASE_MOCK = ROOT / "tests" / "e2e" / "auth" / "mock_supabase.mjs"


def _jwt(sub: str) -> str:
    """A JWT-shaped access token whose payload carries ``sub``.

    The BFF's `/api/session` decorates the profile by calling the provider's
    `/auth/v1/user` with this token as the bearer, and the local mock derives
    the id/email/display name from the payload's `sub` (it does not verify the
    signature). An OPAQUE seed token — what the other migrated specs use —
    resolves the gate but leaves the profile undecorated, which is exactly the
    state the identity block has to be tested OUT of.
    """
    def enc(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{enc({'alg': 'none', 'typ': 'JWT'})}.{enc({'sub': sub})}.e2e-signature"


@pytest.fixture(scope="module", autouse=True)
def _identity_stack() -> None:
    """A Supabase-BOUND dashboard preview, started here (#2809).

    The identity surface renders `display_name`/`email` from the BFF's decorated
    profile (`functions/api/session.ts` → `/auth/v1/user`). A preview with no
    `SUPABASE_URL` binding answers `/api/session` without either field, so the
    chrome falls back to the ORGANIZATION name and every assertion in this file
    would be testing the fallback instead of the identity block.

    Self-contained on purpose: the spec spawns the mock provider and the preview
    it needs, on ports it picks, so it cannot silently adopt a stale server built
    from different code (the failure mode that made two sibling suites share
    ports and test the wrong build).
    """
    ensure_dashboard_dist()
    mock_port = pick_free_port(9050)
    app_port = pick_free_port(mock_port + 1, taken={mock_port})
    app_url = f"http://127.0.0.1:{app_port}"
    mock_url = f"http://127.0.0.1:{mock_port}"

    node = shutil.which("node")
    wrangler = shutil.which("wrangler")
    if not node or not wrangler:
        # `pytest.exit`, not `pytest.fail`: this is a module-scoped autouse
        # fixture, so a `fail` would be repeated once per test (22 errors for
        # one missing tool). Same precedent as
        # `test_session_login_flow.py`'s server fixture.
        pytest.exit("node + wrangler are required for the dashboard identity e2e "
                    "(the suite must not silently skip: it is the only browser-level "
                    "coverage of the account-menu identity block)", returncode=1)

    mock_env = os.environ.copy()
    mock_env["MOCK_PORT"] = str(mock_port)

    # ONE guard for the whole setup phase, starting at the FIRST spawn: both
    # children are their own process group (`start_new_session=True`), so an
    # interrupt during either readiness wait — the exact moment a human or the
    # job's step timeout interrupts a hanging preview — would otherwise leave
    # them running with nothing to reap them.
    mock = None
    app = None
    try:
        mock = subprocess.Popen([node, str(SUPABASE_MOCK)], env=mock_env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        if not wait_for_port(mock_port):
            pytest.fail(f"the Supabase mock never listened on {mock_port}")

        app = subprocess.Popen(
            [wrangler, "pages", "dev", "dist",
             "--port", str(app_port), "--ip", "127.0.0.1",
             # The previews the job itself boots pin this date; a preview that
             # does not can be refused by the bundled workerd ("compatibility
             # date past the runtime's max"), and the spec would then be
             # asserting against different runtime behaviour than CI's.
             "--compatibility-date=2024-09-23",
             "--d1", "SESSIONS",
             # State root pinned to the dir the seed's D1 glob reads
             # (`d1_sqlite_files()`), so the row it writes and the row the
             # preview serves are the same database wherever the run happens.
             *dashboard_persist_args(),
             "-b", f"SUPABASE_URL={mock_url}",
             "-b", "SUPABASE_ANON_KEY=mock-anon-key",
             "-b", f"APP_ORIGIN={app_url}"],
            cwd=str(DASHBOARD_DIR),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if not wait_for_port(app_port):
            pytest.fail(f"wrangler pages dev never listened on {app_port}")

        # Materialise the preview's D1 BEFORE the first seed. `_seed_bff_session`
        # waits for a sqlite to appear, but its budget starts from the seed and
        # it polls the SHARED `test_session_login_flow.DASHBOARD_URL` (which this
        # module does not rebind), so on a clean checkout the first test would
        # spend 30 s waiting on the wrong server and die naming a preview this
        # fixture never started.
        #
        # TWO conditions, and "a sqlite exists somewhere" is neither: every
        # earlier spec in the job seeds through the SAME `.wrangler/state` tree,
        # so that check is already true before this preview proves anything.
        #
        # 1. THIS preview answers `/api/session` with a routing answer (401 for a
        #    bogus cookie) rather than a 5xx — a preview booted without `--d1
        #    SESSIONS` answers 503, and would otherwise surface as 22 confusing
        #    auth failures instead of naming the binding.
        # 2. a session sqlite exists under the pinned state root, because that is
        #    what `_seed_bff_session` waits for and writes its row into.
        warm_deadline = time.monotonic() + 30
        warmed = False
        status: int | None = None
        while time.monotonic() < warm_deadline:
            try:
                # 401 is the expected answer — the store answered, which is the
                # point. Transports can also drop the first request while the
                # function bundler is still settling, hence the retry.
                status = urllib.request.urlopen(
                    urllib.request.Request(
                        f"{app_url}/api/session",
                        headers={"Cookie": "__Host-session=" + "0" * 64},
                    ),
                    timeout=10,
                ).status
            except urllib.error.HTTPError as exc:
                status = exc.code
            except Exception:
                status = None
            if status in (200, 401, 403) and d1_sqlite_files(DASHBOARD_DIR):
                warmed = True
                break
            time.sleep(0.5)
        if not warmed:
            pytest.fail(
                f"the identity preview on {app_url} is not serving the session "
                f"store (last status {status!r}; a 5xx here usually means it is "
                "booting without `--d1 SESSIONS`), or no D1 sqlite appeared under "
                f"{DASHBOARD_DIR / '.wrangler'}"
            )
    except BaseException:
        if app is not None:
            stop(app)
        if mock is not None:
            stop(mock)
        raise

    globals()["DASHBOARD_URL"] = app_url + "/"
    globals()["STACK_MOCK_URL"] = mock_url
    try:
        yield
    finally:
        stop(app)
        stop(mock)


def _session(user_id: str = "u-e2e-identity", display_name: str = "danielospinabotero") -> dict:
    """Supabase session JSON for the parent-domain cookie (mirrors
    test_dashboard_gate's _session).

    ``display_name=None`` omits ``user_metadata`` from the SEEDED session, which
    is now only the legacy residue the app must ignore: the rendered name comes
    from ``/api/session`` → ``/auth/v1/user``. The email-prefix fallback branch is
    produced by the mock's ``noDisplayName`` mode instead (see
    ``test_account_menu_email_prefix_fallback``).
    """
    now = datetime.now(UTC)
    user = {
        "id": user_id,
        "aud": "authenticated",
        "email": "identity-e2e@premise-labs.dev",
        "email_confirmed_at": "2026-08-01T00:00:00Z",
        "app_metadata": {"providers": ["github"]},
        "identities": [],
    }
    if display_name is not None:
        user["user_metadata"] = {"display_name": display_name}
    return {
        "access_token": "fake-access-token-e2e",
        "refresh_token": "fake-refresh-token",
        "expires_in": 3600,
        "expires_at": int((now + timedelta(hours=1)).timestamp()),
        "token_type": "bearer",
        "user": user,
    }


def _seed(page: Page, display_name: str | None = "danielospinabotero") -> None:
    """Seed a LIVE BFF session (#2809).

    Writes the `__Host-session` handle + its D1 row (and, deliberately, the
    legacy `sb-tortoise-auth-token` residue the app must ignore) via the shared
    helper. The legacy-only cookie this used to write is no longer a session at
    all since #3501, which is why the whole file dead-ended on `/auth`.
    """
    payload = _session(display_name=display_name)
    sub = payload["user"]["id"]
    # `origin=` is load-bearing: without it the cookie is scoped to the SHARED
    # `test_session_login_flow.DASHBOARD_URL`, not to the preview this module
    # boots. That works today only because cookies are host-scoped and the port
    # differs — so a `DASHBOARD_URL` override with a different HOST would send
    # every request back to `/auth`, the exact failure this file exists to test.
    _seed_local_session_cookie(page, sub, payload, access_token=_jwt(sub),
                               origin=globals()["DASHBOARD_URL"])


def _inventory(login_methods: int = 1, linking: bool = True) -> dict:
    methods = [{"id": "id-gh-e2e", "provider": "github", "provider_id": "gh-e2e",
                "email_confirmed_at": "2026-08-01T00:00:00Z"}]
    if login_methods >= 2:
        methods.append({"id": "id-gl-e2e", "provider": "google", "provider_id": "gl-e2e",
                        "email_confirmed_at": "2026-08-01T00:00:00Z"})
    if login_methods >= 3:
        methods.append({"id": "id-ms-e2e", "provider": "microsoft", "provider_id": "ms-e2e",
                        "email_confirmed_at": "2026-08-01T00:00:00Z"})
    return {
        "methods": methods,
        "has_password": False, "email_method": True,
        "login_methods": login_methods, "keys_tier": 0,
        "banner": {"show": login_methods <= 1},
        "linking_available": linking,
        "email": "identity-e2e@premise-labs.dev",
        "email_confirmed_at": "2026-08-01T00:00:00Z",
        "last_sign_in_at": datetime.now(UTC).isoformat(),
        "reauth_required": False,
    }


def _wire(page: Page, *, inv: dict | None = None,
          commit_calls: list = None, inv_factory=None,  # noqa: RUF013
          teams: list | None = None, team_reads: list | None = None,
          mint_calls: list | None = None,
          keys_rows: dict | None = None, created_keys: list | None = None) -> None:
    """Intercept api.premiselabs.co — mock the identity endpoints + the
    shell's /v1/organizations (the mount NEVER mints a bootstrap key since #2167 —
    POST /v1/session/key is a loud-500 zero-mint tripwire). ``inv_factory``
    (callable) lets a test flip the inventory response AFTER a mutation (e.g.
    post-commit refetch shows banner gone). ``teams`` (list of team dicts)
    drives the team-scoped responses; /v1/team returns the matching team by
    ?org_id= (#1874 two-team regression). ``team_reads`` (list) records every
    /v1/team org_id the app requested — pin the post-switch read so a
    dropped ?org_id= pin cannot false-pass. ``mint_calls`` (list) records any
    POST /v1/session/key (loud-500). ``keys_rows`` (dict org_id → key rows)
    + ``created_keys`` (list) add the durable-keys surface for #2167 F9
    (mintKey POST /v1/team/keys?org_id= + GET per team)."""

    if teams is None:
        # NOTE: the shell reads t.org_name (main.jsx:452) — `name` renders empty.
        teams = [{"org_id": "team_e2e", "org_name": "E2E", "tier": "free"}]

    def team_for(org_id: str) -> dict | None:
        # #1874 test-review P1: FAIL LOUDLY on an unknown/missing org_id
        # (404) instead of silently falling back to teams[0] — a dropped
        # ?org_id= pin must surface, not mask.
        return next((t for t in teams if t["org_id"] == org_id), None)

    def inventory_payload():
        if inv_factory is not None:
            return inv_factory()
        return inv if inv is not None else _inventory()

    def handle(route):
        url = route.request.url
        # #1874 + #1828: the shell pins /v1/team?org_id=… (multi-membership
        # resolution) — matchers must be query-tolerant or every team-scoped
        # read 404s (pre-existing breakage from #1828, fixed here).
        path = _bff_path(url)
        method = route.request.method
        if not _is_bff_api(url):
            # #2809/#2744: prod-origin subresources (the /auth document,
            # script chunks) are proxied to the LOCAL previews — falling
            # through to production would make every run depend on the
            # deployed host, which is exactly what this seam broke on.
            if url.startswith(AUTH_HOST):
                _proxy_body(route, AUTH_ORIGIN + url[len(AUTH_HOST):], page)
                return
            if url.startswith(APP_HOST):
                _proxy_body(route, DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):], page)
                return
            route.continue_()
            return
        if path.endswith("/v1/onboarding/state") and method == "GET":
            # #1877/#1885: the shell calls this FIRST (re-fired per #1847).
            # Returning test users are ONBOARDED — onboarding_complete:true
            # keeps the shell in the normal dashboard state (a falsy
            # onboarding dict re-triggers the setup wizard, no dashboard
            # mint, blob "No team").
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"onboarding": {"onboarding_complete": True}}))
            return
        if path.endswith("/v1/user/identity") and method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(inventory_payload()))
            return
        if path.endswith("/v1/user/identity/link-intent") and method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"intent_ref": "e2e-ref.abcdef",
                                           "expires_in": 120, "provider": "github"}))
            return
        if path.endswith("/v1/user/identity/link-commit") and method == "POST":
            if commit_calls is not None:
                commit_calls.append(route.request.post_data or "")
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"linked": True, "already": False,
                                           "provider": "github", "adoption_signal": False}))
            return
        if path.endswith("/v1/user/identity/unlink") and method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"unlinked": True, "remaining_login_methods": 1}))
            return
        if path.endswith("/v1/user/identity/resend-confirmation") and method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"sent": True}))
            return
        if path.endswith("/v1/organizations") and method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(teams))
            return
        if path.endswith("/v1/team") and method == "GET":
            # #1874: key by the ?org_id= query the app pins (main.jsx
            # loadTeam) — 404 on unknown ids (fail-loud, no fallback).
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            tid = (qs.get("org_id") or ["team_e2e"])[0]
            if team_reads is not None:
                team_reads.append(tid)
            t = team_for(tid)
            if t is None:
                route.fulfill(status=404, content_type="application/json", body="{}")
                return
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({**t, "anon": False}))
            return
        if path.endswith("/v1/session/key") and method == "POST":
            # #2167 zero-mint tripwire: the dashboard never mints a bootstrap
            # key (mount/switch/revoke callers deleted) — loud 500 + counter.
            if mint_calls is not None:
                mint_calls.append(url)
            route.fulfill(status=500, content_type="application/json",
                          body=json.dumps({"detail": "#2167 zero-mint tripwire"}))
            return
        if path.endswith("/v1/team/keys"):
            qs2 = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            ktid = (qs2.get("org_id") or ["team_e2e"])[0]
            if method == "POST":
                # #2167 F9: mintKey's session-mode create pins ?org_id=
                # (rule 4) and returns the plaintext; the row joins the
                # team's GET list so loadAll renders it.
                if created_keys is not None:
                    created_keys.append({"url": url, "body": route.request.post_data or ""})
                plain = "tt_created_abcdef0123456789"
                row = {"id": "key_created_1", "key_prefix": plain[:10],
                       "created_at": "2026-09-04T00:00:00Z", "last_used_at": None,
                       "revoked_at": None, "enabled": True, "name": None,
                       "created_via": "provisioned", "expires_at": None}
                if keys_rows is not None:
                    keys_rows.setdefault(ktid, []).append(row)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"key": plain, "key_prefix": plain[:10]}))
                return
            rows = (keys_rows or {}).get(ktid, [])
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"keys": rows}))
            return
        if path.endswith("/v1/sessions") or path.endswith("/backups"):
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"sessions": [], "backups": []}))
            return
        if "/v1/organizations/" in path and path.endswith("/members") and method == "GET":
            # members tab: one owner row (the seeded user)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps([{"user_id": "u-e2e-identity",
                                            "role": "owner", "status": "active",
                                            "email": "identity-e2e@premise-labs.dev"}]))
            return
        route.fulfill(status=404, content_type="application/json", body="{}")

    page.route("**/*", handle)


def _mock_fault(**faults) -> None:
    """Set a mock-provider fault (JSON body to the mock's own control route).

    Used for the profile SHAPES the provider can return — e.g. a user who never
    set a display name (#2809) — which route interception cannot express because
    the decoration happens worker-side, not in the browser.
    """
    import urllib.request

    req = urllib.request.Request(
        (globals()["STACK_MOCK_URL"] + "/__mock/fault"),
        data=json.dumps(faults).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.loads(res.read())


def _open_account_menu(page: Page) -> None:
    # #1874: blob aria-label is `Account menu — {team}` (regex match)
    page.get_by_role("button", name=re.compile(r"Account menu")).click()


def _open_profile_via_menu(page: Page) -> None:
    # #1874: menu entry (scoped to .account-menu — avoids strict-mode
    # double-match while the nav tab still exists during the transition)
    page.locator(".account-menu").get_by_role("button", name="Profile").click()


def test_account_menu_identity_block_single_team(page: Page):
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1))
    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    # #2809: the identity block renders the BFF-DECORATED profile
    # (`/api/session` → provider `/auth/v1/user`), which the mock derives from
    # the seeded token's `sub`. It used to assert a hardcoded display name the
    # legacy cookie carried; after #3501 the name can only come from the
    # provider, so the assertion follows that seam instead of the old fixture.
    expect(page.locator(".account-identity-name")).to_have_text("Display u-e2e-identity")
    expect(page.locator(".account-identity-email")).to_have_text("u-e2e-identity@example.test")
    # #2494: tier badge is on the Organization block, NOT the identity block
    expect(page.locator(".account-identity .tier-badge")).to_have_count(0)
    expect(page.locator(".account-menu .tier-badge")).to_have_count(1)
    # #4336: the badge renders the DISPLAY label, never the raw tier key.
    expect(page.locator(".account-menu .tier-badge")).to_have_text("Free")
    # #4336: the header tier badge routes through the same label map
    # (catches a re-lowercased display name). #4639: the header control is no
    # longer a marketing <a> — it is an upgrade button, or a plain badge when
    # the deployment has no checkout price id. Scope to the header's DIRECT
    # child: the open account menu's org badge is a nested `.tier-badge` too
    # (`header .tier-badge` matched two elements).
    expect(page.locator("header > .tier-badge")).to_contain_text("Free tier")
    expect(page.locator(".account-menu").get_by_role("button", name="Profile")).to_be_visible()
    expect(page.locator(".account-menu").get_by_role("button", name="Log out")).to_be_visible()
    expect(page.locator(".account-menu").get_by_text("Switch organization")).to_have_count(0)


def test_account_menu_email_prefix_fallback(page: Page):
    """#1874: a user who never set a display name → identity block shows the
    email prefix (`u-e2e-identity@example.test` → `u-e2e-identity`), not the
    organization name.

    #2809: the "no display name" SHAPE is the provider's to produce — a seeded
    session dict cannot express it, because the decoration comes from
    `/auth/v1/user`. The mock's `noDisplayName` mode is that shape.
    """
    _mock_fault(noDisplayName=True)
    try:
        _seed(page, display_name=None)
        _wire(page, inv=_inventory(login_methods=1))
        page.goto(DASHBOARD_URL)
        _open_account_menu(page)
        expect(page.locator(".account-identity-name")).to_have_text("u-e2e-identity")
        expect(page.locator(".account-identity-email")).to_have_text("u-e2e-identity@example.test")
        expect(page.locator(".account-identity-name")).not_to_have_text("E2E")
    finally:
        _mock_fault(noDisplayName=False)


def test_account_menu_multi_team_switch(page: Page):
    """#1874 regression guard: two-team switch list renders, aria-current on
    the active team, AND the post-switch /v1/team read is pinned to the new
    org_id (a dropped ?org_id= pin must not false-pass)."""
    _seed(page)
    teams = [
        {"org_id": "team_a", "org_name": "Alpha", "tier": "free"},
        {"org_id": "team_b", "org_name": "Bravo", "tier": "free"},
    ]
    team_reads: list = []
    _wire(page, inv=_inventory(login_methods=1), teams=teams, team_reads=team_reads)
    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    menu = page.locator(".account-menu")
    expect(menu.get_by_text("Switch organization")).to_be_visible()
    # #2809: scope to the SWITCH BUTTONS — the org row renders the active
    # name too, so a bare text locator matches twice (strict-mode violation)
    # the day the two blocks coexist, which is the shipped DOM.
    expect(menu.get_by_role("button", name="Alpha", exact=True)).to_be_visible()
    expect(menu.get_by_role("button", name="Bravo", exact=True)).to_be_visible()
    # active team carries aria-current
    expect(menu.locator("button[aria-current='true']")).to_contain_text("Alpha")
    # switch to Bravo → active moves AND the data layer re-reads team_b
    # switch to Bravo — the ?org_id= pin must reach the API. expect_response
    # pumps the Playwright sync event loop while waiting (a Python sleep-poll
    # would block the message pump and stall the mock's response — the
    # observed 15s "stall" was exactly that artifact).
    with page.expect_response(lambda r: "/v1/team" in r.url and "org_id=team_b" in r.url,
                              timeout=15000):
        menu.get_by_role("button", name="Bravo").click()
    assert "team_b" in team_reads, f"?org_id= pin must reach the API: {team_reads}"
    _open_account_menu(page)
    expect(page.locator(".account-menu").locator("button[aria-current='true']")).to_contain_text("Bravo")


def test_members_heading_and_nav(page: Page):
    """#1874: no Profile button in nav; the Members tab renders its section.

    #2809: the heading is `Members` (main.jsx `<h2>Members</h2>`) — this
    assertion still read "Team members", one of the copy-sweep strings the
    team→organization rename left behind. Asserting the shipped string is what
    keeps the spec honest; it is not a relaxation (the heading must exist).
    """
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1))
    page.goto(DASHBOARD_URL)
    expect(page.locator("nav").get_by_role("button", name="Profile")).to_have_count(0)
    page.get_by_role("button", name="Members").click()
    expect(page.get_by_role("heading", name="Members", exact=True)).to_be_visible()


def test_billing_team_context(page: Page):
    """#1876: Billing names its team; multi-team can switch in-tab AND the
    plan data re-hydrates (pinned via team_reads + the Builder-plan badge)."""
    _seed(page)
    teams = [
        {"org_id": "team_a", "org_name": "Alpha", "tier": "free",
         "subscription_status": None, "write_ops_used": 0, "write_ops_limit": 10000},
        {"org_id": "team_b", "org_name": "Bravo", "tier": "pro",
         "subscription_status": "active", "write_ops_used": 100, "write_ops_limit": 50000},
    ]
    team_reads: list = []
    _wire(page, inv=_inventory(login_methods=1), teams=teams, team_reads=team_reads)
    page.goto(DASHBOARD_URL)
    page.get_by_role("button", name="Billing").click()
    expect(page.get_by_role("heading", name="Billing — Alpha")).to_be_visible()
    expect(page.get_by_text("free plan")).to_be_visible()
    # #2809: the control's aria-label is "Billing organization"
    # (main.jsx `billing-team-select`) — "Billing team" was the pre-rename copy.
    select = page.get_by_label("Billing organization")
    expect(select).to_be_visible()
    expect(select.locator("option")).to_have_count(2)
    # switch — the ?org_id= pin must reach the API and the card re-renders
    with page.expect_response(lambda r: "/v1/team" in r.url and "org_id=team_b" in r.url,
                              timeout=15000):
        select.select_option("team_b")
    assert "team_b" in team_reads
    expect(page.get_by_role("heading", name="Billing — Bravo")).to_be_visible()
    expect(page.get_by_text("Builder plan")).to_be_visible()
    expect(page.get_by_text("100", exact=True)).to_be_visible()  # write_ops_used re-hydrated


def test_billing_team_context_single_team(page: Page):
    """#1876 single-team: team name shown, no empty selector."""
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1))
    page.goto(DASHBOARD_URL)
    page.get_by_role("button", name="Billing").click()
    expect(page.get_by_role("heading", name="Billing — E2E")).to_be_visible()
    # #2809: the selector's aria-label is "Billing organization"; asserting the
    # retired string "Billing team" was vacuous — it matched nothing, so the
    # count was 0 whether or not an empty selector rendered.
    expect(page.get_by_label("Billing organization")).to_have_count(0)


def test_recovery_banner_shows_and_routes_to_profile(page: Page):
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1))
    page.goto(DASHBOARD_URL)
    banner = page.get_by_role("region", name="Account recovery")
    expect(banner).to_be_visible()
    banner.get_by_role("button", name="Add a login method").click()
    expect(page.get_by_role("heading", name="Login methods")).to_be_visible()


def test_no_banner_for_two_methods(page: Page):
    _seed(page)
    _wire(page, inv=_inventory(login_methods=2))
    page.goto(DASHBOARD_URL)
    expect(page.get_by_role("region", name="Account recovery")).to_have_count(0)


def test_promise_free_banner_when_linking_off(page: Page):
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1, linking=False))
    page.goto(DASHBOARD_URL)
    banner = page.get_by_role("region", name="Account recovery")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("hello@premiselabs.co")  # promise-free copy


def test_link_commit_fires_on_oauth_return(page: Page):
    _seed(page)
    commits: list = []
    state = {"methods": 1}
    _wire(page, inv=_inventory(login_methods=1), commit_calls=commits,
          inv_factory=lambda: _inventory(login_methods=state["methods"]))
    page.goto(f"{DASHBOARD_URL}?link_flow=e2e-ref.abcdef")
    expect(page.get_by_role("heading", name="Login methods")).to_be_visible()
    assert len(commits) == 1, "link-commit must fire on the OAuth return"
    assert "e2e-ref.abcdef" in commits[0], f"link-commit must carry the intent_ref: {commits}"
    # the ?link_flow= param must be stripped (review P3)
    # #2809: the OAuth return lands on the Profile ROUTE (`#/profile`) — the
    # pre-#4054 assertion expected the bare URL because the flow ended on the
    # root document. What matters (and what was reviewed) is that the
    # `?link_flow=` param is STRIPPED, not which route it settles on.
    expect(page).to_have_url(re.compile(re.escape(DASHBOARD_URL.rstrip("/")) + r"/#/profile$"))


def test_no_horizontal_scroll_narrow_viewport(page: Page):
    _seed(page)
    # two-team fixture with an ACTIVE subscription on the current team so the
    # Billing header row renders BOTH the team select and the Manage button
    # at 375px (review c2 P2: a single free team renders neither).
    teams = [
        {"org_id": "team_a", "org_name": "Alpha", "tier": "pro",
         "subscription_status": "active", "write_ops_used": 100, "write_ops_limit": 50000},
        {"org_id": "team_b", "org_name": "Bravo", "tier": "pro",
         "subscription_status": "active"},
    ]
    _wire(page, inv=_inventory(login_methods=1), teams=teams)
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(DASHBOARD_URL)
    scroll_w = page.evaluate("document.documentElement.scrollWidth")
    client_w = page.evaluate("document.documentElement.clientWidth")
    assert scroll_w <= client_w + 1, f"horizontal overflow: {scroll_w} > {client_w}"
    # #1874 test-review P2: the restructured menu is the overflow-risk surface
    # — re-check with the account menu OPEN (pinned visible so a no-op click
    # cannot silently re-test the closed layout).
    _open_account_menu(page)
    expect(page.locator(".account-menu")).to_be_visible()
    scroll_w = page.evaluate("document.documentElement.scrollWidth")
    client_w = page.evaluate("document.documentElement.clientWidth")
    assert scroll_w <= client_w + 1, f"horizontal overflow with menu open: {scroll_w} > {client_w}"
    # #1876: the Billing header row (team select + Manage button) wraps via
    # .billing .row flex-wrap — re-check with Billing open.
    # #2809: close the menu FIRST. While it is open its overlay sits over the
    # header cluster at 375px and legitimately intercepts the nav click
    # (`.account-avatar` from `.header-cluster` intercepts pointer events), so
    # the click retried to timeout. Escape is the menu's own close path
    # (main.jsx:2675), which is what a user at this width would reach for.
    page.keyboard.press("Escape")
    expect(page.locator(".account-menu")).to_have_count(0)
    page.get_by_role("button", name="Billing").click()
    scroll_w = page.evaluate("document.documentElement.scrollWidth")
    client_w = page.evaluate("document.documentElement.clientWidth")
    assert scroll_w <= client_w + 1, f"horizontal overflow on Billing: {scroll_w} > {client_w}"


def test_change_email_requires_reauth(page: Page):
    """#1765 plan-review P1-1: changing email via the add-email+password path
    (unconfirmed email) MUST be gated by the ReauthDialog — a stale session
    must not reach updateUser (stolen-session ATO chain). (The reauth
    staleness MECHANISM itself is unit-tested in identity.test.js; here we
    verify the gate fires on the change-email path.)"""
    _seed(page)
    inv = _inventory(login_methods=1)
    inv["email_confirmed_at"] = None  # unconfirmed email → change-email path
    _wire(page, inv=inv)
    page.goto(DASHBOARD_URL)
    # Profile → menu entry (nav tab removed in #1874)
    _open_account_menu(page)
    _open_profile_via_menu(page)
    page.get_by_role("button", name="Connect email and password").click()
    page.get_by_label("Email").fill("new@premise-labs.dev")
    page.get_by_label("Password").fill("hunter22")
    page.get_by_role("button", name="Connect email & password").click()
    # the ReauthDialog must gate (no direct updateUser)
    expect(page.get_by_role("dialog", name="Confirm it's you")).to_be_visible()


def test_unlink_confirm_flow(page: Page):
    """Journey step 5: Remove shows the confirm dialog NAMING the provider
    (recorded, not blindly accepted), and the post-unlink refetch drops the
    method — Remove buttons go 3 → 2 and the unlink POST body carries the
    identity row id."""
    _seed(page)
    dialogs: list = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    unlink_calls: list = []
    state = {"methods": 3}

    def _inv():
        return _inventory(login_methods=state["methods"])

    _wire(page, inv=_inventory(login_methods=3), inv_factory=_inv)
    # re-route unlink to capture + decrement — NARROW pattern so this handler
    # only intercepts the unlink POST (a **/* re-route shadows the _wire mock
    # and its continue_() fails the api requests — handler-chain gotcha).
    def handle(route):
        if route.request.method == "POST":
            unlink_calls.append(route.request.post_data or "")
            state["methods"] = 2
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"unlinked": True, "remaining_login_methods": 2}))
            return
        route.continue_()
    page.route("**/v1/user/identity/unlink*", handle)

    page.goto(DASHBOARD_URL)
    # Profile → menu entry (nav tab removed in #1874)
    _open_account_menu(page)
    _open_profile_via_menu(page)
    # 3 methods → 3 Remove buttons
    expect(page.get_by_role("button", name="Remove", exact=True)).to_have_count(3)
    page.get_by_role("button", name="Remove", exact=True).first.click()
    # the confirm dialog named the provider (regression guard: no silent unlink)
    assert len(dialogs) == 1 and "github" in dialogs[0], f"unexpected dialogs: {dialogs}"
    # the unlink POST fired with the identity row id
    assert unlink_calls and "id-gh-e2e" in unlink_calls[0], f"unlink body: {unlink_calls}"
    # post-refetch: 2 methods → 2 Remove buttons (the drop is observable)
    expect(page.get_by_role("button", name="Remove", exact=True)).to_have_count(2)
    assert len(unlink_calls) == 1, "unlink POST must fire after confirm"


def test_create_team_success(page: Page):
    """#1877: the menu entry is visible for a SINGLE-team user; the dialog
    creates a team and the dashboard switches to it."""
    _seed(page)
    teams = [{"org_id": "team_e2e", "org_name": "E2E", "tier": "free"}]
    _wire(page, inv=_inventory(login_methods=1), teams=teams)

    def handle_create(route):
        # Tight path match (the loadTeams GET + the create POST); NEVER
        # continue_() — the handler-chain fall-through hangs in this
        # Playwright build (the #1874 gotcha). /v1/organizations/{id}/members etc.
        # don't match the exact path and fall to _wire directly.
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/v1/organizations"):
            if route.request.method == "POST":
                name = (json.loads(route.request.post_data or "{}").get("name") or "newteam")
                teams.append({"org_id": "team_new", "org_name": name, "tier": "free"})
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"org_id": "team_new", "graph_name": "team_new",
                                               "tier": "free", "name": name}))
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(teams))
            return
        route.continue_()
    page.route("**/v1/organizations", handle_create)

    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    menu = page.locator(".account-menu")
    expect(menu.get_by_role("button", name="+ Create new organization")).to_be_visible()
    expect(menu.get_by_text("Switch organization")).to_have_count(0)  # single-team: no switch label
    menu.get_by_role("button", name="+ Create new organization").click(force=True)  # menu closes + dialog opens → unmounts
    # W1 (#1997) + #2494: the create dialog renders as 'Create a new
    # organization' with an 'Organization name' input. Since #2789 the dialog
    # is named by its own heading (aria-labelledby → the name-mode <h3>, which
    # reads 'Create a new organization'), so this name still resolves in name
    # mode; limit/purchase mode announce their own headings.
    expect(page.get_by_role("dialog", name="Create a new organization")).to_be_visible()
    # special characters (not spaces) are rejected — inline error, no POST
    page.get_by_label("Organization name").fill("bad@name!")
    page.locator(".modal .btn-primary").click(force=True)
    # NOTE: the client copy is "Invalid organization name — …" (the team→
    # organization rename). The assertion said "Invalid team name" and had been
    # stale/latent-red on main; corrected here while touching this file.
    expect(page.locator(".modal")).to_contain_text("Invalid organization name", timeout=10000)
    page.get_by_label("Organization name").fill("good name with spaces")
    page.locator(".modal .btn-primary").click(force=True)  # busy-state re-render detaches the name-changed button
    # the dashboard switches to the new team (the blob shows its name)
    expect(page.get_by_role("button", name=re.compile(r"Account menu"))).to_contain_text("good name with spaces", timeout=15000)


def test_create_team_free_capped_gate(page: Page):
    """#1877/#2789: a 402 from POST /v1/organizations surfaces the gated dialog. The
    server drives it with a STRUCTURED detail (`one_free_org_limit`) — the
    three-option gate (#2789) — never by string-matching the message.

    The fixture's team row carries no `role`, so the client pre-check does not
    short-circuit here: this exercises the server-driven path.
    """
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1))

    def handle_create(route):
        # Same tight-path + no-continue pattern as test_create_team_success
        # (the handler-chain fall-through hangs in this Playwright build).
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/v1/organizations"):
            if route.request.method == "POST":
                route.fulfill(status=402, content_type="application/json",
                              body=json.dumps({"detail": {
                                  "code": "one_free_org_limit",
                                  "message": "You can only have one free organization",
                                  "org_id": "team_e2e"}}))
            else:
                # default single-team fixture shape (this test uses _wire's default)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([{"org_id": "team_e2e", "org_name": "E2E",
                                                "tier": "free"}]))
            return
        route.continue_()
    page.route("**/v1/organizations", handle_create)

    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    page.locator(".account-menu").get_by_role("button", name="+ Create new organization").click(force=True)  # menu closes → unmounts
    page.get_by_label("Organization name").fill("blocked")
    page.locator(".modal .btn-primary").click(force=True)  # busy-state re-render
    # #2789: the limit mode NAMES the dialog from its own heading
    # (`aria-labelledby`), so a screen reader hears the gate — not the generic
    # "Create a new organization" of the name mode.
    dialog = page.get_by_role("dialog", name="You can only have one free organization")
    # #2789: the server's structured code selects the three-option gate.
    expect(dialog).to_contain_text("You can only have one free organization", timeout=15000)
    expect(dialog).to_contain_text(
        "Individual users can create one organization. To create another, purchase a subscription for it — or upgrade your current organization")
    expect(dialog.get_by_role("button", name="Purchase subscription for a new organization")).to_be_visible()
    expect(dialog.get_by_role("button", name="Upgrade current organization")).to_be_visible()
    expect(dialog.get_by_role("button", name="Cancel")).to_be_visible()
    # "Upgrade current organization" lands on the Billing tab (the org the
    # server's detail named).
    dialog.get_by_role("button", name="Upgrade current organization").click()
    expect(page.get_by_role("heading", name=re.compile(r"Billing"))).to_be_visible()


def test_create_team_pre_checked_at_cap(page: Page):
    """#2789: the account-menu item PRE-CHECKS the entitlement from the teams
    list (role + subscription_status) and opens the three-option dialog
    immediately — the user never types a name and gets rejected.

    No POST /v1/organizations is stubbed/allowed here: reaching the dialog without one
    IS the assertion.
    """
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1), teams=[
        {"org_id": "team_e2e", "org_name": "E2E", "tier": "free",
         "role": "owner", "subscription_status": None,
         # #2789: the plan chooser reads the server-resolved catalog from the
         # current team's /v1/team payload (ids never hardcoded client-side).
         "checkout_price_ids": {"solo": "price_100soloM", "pro": "price_200proMM"}},
    ])
    posted: list = []

    def handle_create(route):
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/v1/organizations"):
            if route.request.method == "POST":
                posted.append(route.request.url)
                route.fulfill(status=500, content_type="application/json",
                              body=json.dumps({"detail": "must not be called"}))
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([{"org_id": "team_e2e", "org_name": "E2E",
                                                "tier": "free", "role": "owner",
                                                "subscription_status": None,
                                                "checkout_price_ids": {"solo": "price_100soloM",
                                                                        "pro": "price_200proMM"}}]))
            return
        route.continue_()
    page.route("**/v1/organizations", handle_create)

    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    page.locator(".account-menu").get_by_role("button", name="+ Create new organization").click(force=True)
    dialog = page.get_by_role("dialog", name="You can only have one free organization")
    expect(dialog).to_contain_text("You can only have one free organization", timeout=15000)
    # The name input is NOT shown — no rejection-after-typing flow.
    expect(dialog.get_by_label("Organization name")).to_have_count(0)
    expect(dialog.get_by_role("button", name="Purchase subscription for a new organization")).to_be_visible()
    assert posted == [], "the pre-check must not POST /v1/organizations"
    # The third option opens the paid-new-org flow (name + plan), not a checkout.
    dialog.get_by_role("button", name="Purchase subscription for a new organization").click()
    dialog = page.get_by_role("dialog", name="Purchase a subscription for a new organization")
    expect(dialog.get_by_label("Organization name")).to_be_visible()
    expect(dialog.get_by_role("button", name="Continue to checkout")).to_be_visible()
    # paid plan chooser is server-resolved (no hardcoded Stripe ids)
    expect(dialog.get_by_role("button", name="Builder · $25/mo")).to_be_visible()
    expect(dialog.get_by_role("button", name="Solo · $9/mo")).to_be_visible()


def test_account_menu_two_sections(page: Page):
    """#2494: menu renders two labeled sections with correct item membership;
    tier badge absent from identity block, present in org section."""
    _seed(page)
    teams = [
        {"org_id": "team_a", "org_name": "Alpha", "tier": "free"},
        {"org_id": "team_b", "org_name": "Bravo", "tier": "free"},
    ]
    _wire(page, inv=_inventory(login_methods=1), teams=teams)
    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    menu = page.locator(".account-menu")
    # section labels visible
    expect(menu.get_by_text("Personal Account")).to_be_visible()
    expect(menu.get_by_text("Organization", exact=True)).to_be_visible()
    # personal section: identity + Profile + Log out grouped
    expect(menu.get_by_role("button", name="Profile")).to_be_visible()
    expect(menu.get_by_role("button", name="Log out")).to_be_visible()
    # tier badge NOT in identity block, IS in org section
    expect(page.locator(".account-identity .tier-badge")).to_have_count(0)
    expect(page.locator(".account-menu .tier-badge")).to_have_count(1)
    # #4336: the badge renders the DISPLAY label, never the raw tier key.
    expect(page.locator(".account-menu .tier-badge")).to_have_text("Free")
    # #4336: the header tier badge routes through the same label map
    # (catches a re-lowercased display name). #4639: see
    # test_account_menu_identity_block_single_team — the header control is no
    # longer a marketing <a>, and the selector is scoped to the DIRECT child
    # (the open account menu carries a nested `.tier-badge`).
    expect(page.locator("header > .tier-badge")).to_contain_text("Free tier")


def test_account_menu_org_block_single_team(page: Page):
    """#2494: single-team users see org name + tier badge in Organization
    section; no switch list; Create new org is unconditional."""
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1))
    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    menu = page.locator(".account-menu")
    # org section visible with team name + tier
    expect(menu.get_by_text("Organization", exact=True)).to_be_visible()
    # #2809: scope to the ORG ROW's own class. A bare `get_by_text("E2E")` is a
    # case-insensitive SUBSTRING match in Playwright, so it also matches the
    # decorated identity block (`u-e2e-identity@example.test`) as soon as the
    # profile renders — a strict-mode violation, not a missing element.
    expect(menu.locator(".account-org-name")).to_have_text("E2E")
    expect(menu.get_by_text("Switch organization")).to_have_count(0)
    expect(menu.get_by_role("button", name="+ Create new organization")).to_be_visible()


def test_pending_invites_accept_lands_on_team(page: Page):
    """#1875 review P2: accept-from-list lands on the team (switchTeam)."""
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1))
    invites = [{"invitation_id": "inv-1", "org_id": "team_e2e", "org_name": "Bravo",
                "role": "member", "inviter_email": "owner@example.com", "expires_at": None}]

    def handle(route):
        url = route.request.url
        path = url.split("?", 1)[0]
        if path.endswith("/v1/invites/pending") and route.request.method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"invites": invites}))
            return
        if "/accept" in path and route.request.method == "POST":
            invites.clear()
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"org_id": "team_e2e", "role": "member"}))
            return
        route.continue_()
    page.route("**/v1/invites/pending**", handle)

    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    page.locator(".account-menu").get_by_role("button", name="Accept").click()
    # the dashboard switches to the invited team (the blob shows E2E) and the
    # menu closes (account-menu no longer visible)
    expect(page.get_by_role("button", name=re.compile(r"Account menu"))).to_contain_text("E2E", timeout=15000)
    expect(page.locator(".account-menu")).to_have_count(0)


def test_pending_invites_in_menu(page: Page):
    """#1875: pending invites render in the account menu; Decline removes
    them; empty state hides the section."""
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1))
    invites = [{"invitation_id": "inv-1", "org_id": "team_e2e", "org_name": "Bravo",
                "role": "member", "inviter_email": "owner@example.com", "expires_at": None}]

    def handle(route):
        url = route.request.url
        path = url.split("?", 1)[0]
        if path.endswith("/v1/invites/pending") and route.request.method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"invites": invites}))
            return
        if "/accept" in path and route.request.method == "POST":
            invites.clear()  # consumed
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"org_id": "team_e2e", "role": "member"}))
            return
        if path.endswith("/v1/invites/pending") is False and \
                "/v1/invites/pending/" in path and route.request.method == "DELETE":
            invites.clear()
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"revoked": True}))
            return
        route.continue_()
    page.route("**/v1/invites/pending**", handle)

    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    menu = page.locator(".account-menu")
    expect(menu).to_contain_text("Invites")
    expect(menu).to_contain_text("Bravo")
    # Decline removes the invite + the section hides
    menu.get_by_role("button", name="Decline").click()
    expect(menu.get_by_text("Invites")).to_have_count(0)
    expect(menu.get_by_text("Bravo")).to_have_count(0)


_TWO_TEAMS = [
    {"org_id": "team_a", "org_name": "Alpha", "tier": "free", "role": "owner"},
    {"org_id": "team_b", "org_name": "Bravo", "tier": "free", "role": "owner"},
]


def _switch_to(page: Page, org_name: str) -> None:
    """#1874-style team switch through the account menu."""
    _open_account_menu(page)
    page.locator(".account-menu").get_by_role("button", name=org_name).click()


def test_durable_create_on_selected_team_lands_on_selected_team(page: Page) -> None:
    """#2167 F9: a durable key created while the selected team ≠ first
    membership lands on the SELECTED team — mintKey's session-mode POST
    /v1/team/keys pins ?org_id=<selected> (rule 4; server honors it
    membership-checked). Zero POST /v1/session/key (the old mount/switch
    bootstrap-mint callers are deleted)."""
    _seed(page)
    mint_calls: list = []
    created_keys: list = []
    keys_rows: dict = {"team_a": [], "team_b": []}
    team_reads: list = []
    _wire(page, inv=_inventory(login_methods=1), teams=_TWO_TEAMS,
          mint_calls=mint_calls, created_keys=created_keys,
          keys_rows=keys_rows, team_reads=team_reads)
    page.goto(DASHBOARD_URL)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=20_000)
    # select Bravo (≠ first membership Alpha) — session-only (zero keys)
    _switch_to(page, "Bravo")
    expect(page.get_by_role("button", name=re.compile(r"Account menu"))).to_contain_text("Bravo", timeout=15_000)
    # open the API Keys tab and create a durable key
    page.locator('[data-tab="keys"]').click()
    expect(page.get_by_role("button", name="+ New key")).to_be_visible(timeout=10_000)
    # #2809: "+ New key" opens the key dialog (form stage); the POST fires from
    # its "Create key" primary (main.jsx:10181-10186). The spec used to assert
    # the POST right after opening the button, which was true only while the
    # button minted directly — a dialog-race, not a mint failure.
    page.get_by_role("button", name="+ New key").click()
    # A sync-API route handler only runs while a Playwright command is pumping,
    # so the click must be WAITED ON: without this the assert below can read
    # `created_keys` before the handler has been dispatched into.
    with page.expect_response(
        lambda r: r.request.method == "POST" and "/v1/team/keys" in r.url,
        timeout=15_000,
    ):
        page.get_by_role("button", name="Create key").click()
    # the POST carried ?org_id=team_b (rule 4) and the shown-once plaintext
    # card renders
    assert created_keys, "createKey POST must fire"
    # #2809: the shown-once REVEAL is asserted by `test_keys_table_mixed.py`
    # (`:1132`–`:1170`: the dialog's heading is exactly "New API key" and the
    # empty-`.key-value` regression is pinned there). This spec's subject is
    # #2167 F9 — WHICH team the create lands on — so it asserts the durable row
    # and the org pin below and leaves the reveal markup to the spec that owns
    # it. (The old `.new-key .key-value` locator here was a stale container
    # class; waiting on it told us nothing about the mint, which had succeeded.)
    post_url = created_keys[-1]["url"]
    assert "org_id=team_b" in post_url, f"mintKey must pin the SELECTED team: {post_url}"
    # the keys table row appears under the selected team (loadAll refetch)
    expect(page.locator("tbody tr", has_text="tt_created")).to_have_count(1, timeout=10_000)
    assert mint_calls == [], f"zero-mint: POST /v1/session/key fired: {mint_calls}"


def test_session_only_team_selection_does_not_survive_reload(page: Page) -> None:
    """#2167 F6b: a session-only team selection does NOT survive reload — the
    mount re-pins to the slot key's team (a valid durable present) else the
    first selectable membership (rule 3 reload consequence, accepted: no
    persisted last-selected-team). With zero keys the reload lands Alpha
    (first membership) even though Bravo was selected pre-reload."""
    _seed(page)
    team_reads: list = []
    _wire(page, inv=_inventory(login_methods=1), teams=_TWO_TEAMS, team_reads=team_reads)
    page.goto(DASHBOARD_URL)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=20_000)
    # session-only switch to Bravo
    _switch_to(page, "Bravo")
    expect(page.get_by_role("button", name=re.compile(r"Account menu"))).to_contain_text("Bravo", timeout=15_000)
    assert "team_b" in team_reads, f"post-switch reads must pin team_b: {team_reads}"
    # reload: the session-only selection is NOT persisted — the mount lands
    # the first selectable membership (Alpha)
    reads_before_reload = len(team_reads)
    with page.expect_response(lambda r: "/v1/team" in r.url and "org_id=team_a" in r.url,
                              timeout=20000):
        page.reload(wait_until="domcontentloaded", timeout=30_000)
    expect(page.get_by_role("button", name=re.compile(r"Account menu"))).to_contain_text("Alpha", timeout=20_000)
    # the reload added a NEW post-reload read pinned to team_a (a plain
    # membership check on the pre-reload reads — which already contain team_a
    # from the mount — could false-pass)
    new_reads = team_reads[reads_before_reload:]
    assert "team_a" in new_reads, f"reload must re-read the first selectable team: {team_reads}"
