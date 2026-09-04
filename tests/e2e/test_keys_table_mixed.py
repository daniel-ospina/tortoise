"""#2166/#2178 keys-table mixed-fixture render e2e (RUN_DASHBOARD_E2E opt-in).

The API Keys table's render behavior (#2166, merged PR #2175) is a render
ternary in main.jsx that no unit test can reach (main.jsx has no component
harness) and no existing dashboard e2e exercised — the shared harness mocks
GET /v1/team/keys with `{"keys": []}` (gate.py `_mock_bootstrap_200`), so the
bug class #2166 fixed had no regression pin. This is the first mixed-table
dashboard fixture (scope doc §S5, AC5 follow-up #2178):

  DURABLE rows render (the only rows a user can act on):
    - provisioned active → "active" + full actions (positive control)
    - provisioned disabled → "disabled" — NOT "active" (the #2166 lie)
    - recovery held key (the dashboard's own durable credential — #2167:
      localStorage-seeded + adopted via the mount probe, no mint) → "active" + the visible
      "in use by this dashboard" note, toggle/trash suppressed, rename kept
    - recovery non-live residual → active + actionable (known limitation,
      provenance needs server data)
    - legacy NULL created_via → active + actionable
    - provisioned revoked → truthful inline "revoked" (never hidden, never
      "active")
    - absent created_via (stale-cache shape) → active + actionable
  BOOTSTRAP / EXPIRING rows NEVER render (isManagedKey excludes
  created_via==='bootstrap' OR any expires_at row — the shipped predicate):
    - bootstrap active (future expiry), bootstrap swept-revoked,
      bootstrap expired, provisioned-with-expiry: all absent from the DOM.
      No "Temporary access keys" zone exists in the shipped DOM (supersede
      banner — do not assert one).

Harness (pinned in scope doc §S5 — "own layered route handler; gate.py's 4
empty-keys tests untouched"):
- Same two-server harness as test_session_login_flow.py / test_dashboard_gate.py:
  `wrangler@4 pages dev . --port 8788` from website/ (auth) +
  `wrangler@4 pages dev dist --port 8790` from website/apps/dashboard/.
- Cookie-seeded session (sb-tortoise-auth-token on .premiselabs.co) →
  app.premiselabs.co (proxied to :8790). #2167: the mount NEVER mints — the
  HELD durable is localStorage-seeded and adopted via the mount's key-lane
  probe (GET /v1/team → 200 TEAM_ROW); POST /v1/session/key is a loud-500
  zero-mint tripwire. isActiveKey fires only on the fixture row whose
  key_prefix is its slice(0,10) — the load-bearing unique prefixes.
- Mocked /v1/teams rows carry role:'owner' (no existing dashboard e2e mock
  supplies role → isOwnerAdmin would be false → every action assertion
  vacuous).
- Fixture rows carry UNIQUE key_prefixes — a second row sharing the held
  prefix (tt_live_re) would double-fire isActiveKey and demote the positive
  control's action assertions.

Zone-scoped by prefix; body-level greps: no ephemeral/durable/
session-credential user-facing strings (the shipped DOM renders none).
"""
from __future__ import annotations

import json
import os
import urllib.parse

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_session_login_flow import (
    API_HOST,
    APP_HOST,
    AUTH_HOST,
    DASHBOARD_URL,
    _proxy_body,
    _session_json,
)

if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("dashboard e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

AUTH_ORIGIN = os.environ.get("DASHBOARD_AUTH_BASE", "http://127.0.0.1:8788")

TEAM_ID = "team_mixed"
TEAM_ROW = {
    "team_id": TEAM_ID,
    "name": "Mixed Fixture",
    "tier": "free",
    "anon": False,
    # #2166: the keys-table action cells are isOwnerAdmin-gated (myRole from
    # the /v1/teams rows) — no existing dashboard e2e mock supplies role, so
    # this suite MUST (scope §S5): without it every toggle/trash/rename
    # assertion is vacuous.
    "role": "owner",
}

# The held durable credential: plaintext seeded into localStorage at mount
# (adopted by the #2167 mount probe). isActiveKey compares
# key_prefix === plaintext.slice(0,10) → fixture row must carry the prefix
# `tt_live_re`; every OTHER row needs a distinct prefix or it would
# double-fire isActiveKey (and the positive control would lose its actions).
HELD_KEY = "tt_live_recovery_key_abcdef0123456789"
HELD_PREFIX = HELD_KEY[:10]  # tt_live_re

# Neutral hex-suffix prefixes — never embed banned user-facing vocabulary.
PREFIXES = {
    "active_ctl": "tt_0a1b2c3d",      # row 1: provisioned enabled (positive control)
    "disabled": "tt_0e1f2a3b",        # row 2: provisioned disabled
    "held": HELD_PREFIX,              # row 3: recovery, live (dashboard's own key)
    "recovery_resid": "tt_0c1d2e3f",  # row 4: recovery, non-live residual
    "legacy_null": "tt_01020304",     # row 5: NULL created_via legacy durable
    "boot_active": "tt_0b1c2d3e",     # row 6: bootstrap, active (never renders)
    "boot_swept": "tt_0f0e0d0c",      # row 7: bootstrap, revoked (never renders)
    "revoked": "tt_0d0c0b0a",         # row 8: provisioned revoked (renders inline)
    "expiring": "tt_0e0d0c0b",        # row 9: provisioned + expires_at (never renders)
    "boot_expired": "tt_03040506",    # row 10: bootstrap, expired (never renders)
    "absent_via": "tt_04050607",      # row 11: created_via absent (stale-cache shape)
}

_EXPIRY_24H = "2026-08-02T00:00:00.000Z"  # bootstrap lifetime = created+24h
_SWEEP_REVOKED_AT = "2026-08-02T06:00:00.000Z"  # reconcile sweeps post-expiry


def _key_row(key_id: str, prefix: str, name: str | None, **kw) -> dict:
    """A server-shaped api_keys row (list_api_keys serialization: hashes
    only, no plaintext; additive created_via/expires_at, per #1708 D7)."""
    row = {
        "id": key_id,
        "key_prefix": prefix,
        "created_at": "2026-08-01T00:00:00.000Z",
        "last_used_at": None,
        "revoked_at": None,
        "enabled": True,
        "name": name,
        "created_via": None,
        "expires_at": None,
    }
    row.update(kw)
    return row


def _mixed_keys_fixture() -> list[dict]:
    """§S5 mixed fixture — every row a UNIQUE key_prefix (load-bearing)."""
    p = PREFIXES
    return [
        # 1. provisioned, enabled → active + full actions (positive control)
        _key_row("key_mixed_01", p["active_ctl"], "ci runner",
                 created_via="provisioned"),
        # 2. provisioned, disabled → truthful "disabled", toggle off + rename/trash
        _key_row("key_mixed_02", p["disabled"], "staging",
                 created_via="provisioned", enabled=False),
        # 3. recovery, held prefix → "active" + visible in-use note; the
        #    dashboard's own durable credential (probe-adopted from the slot).
        _key_row("key_mixed_03", p["held"], "dashboard (held)",
                 created_via="recovery"),
        # 4. recovery, non-live → active + actionable (residual-durable
        #    limitation: provenance needs server data — separate issue).
        _key_row("key_mixed_04", p["recovery_resid"], "recovery leftover",
                 created_via="recovery"),
        # 5. legacy registry key (NULL created_via, no expiry) → active.
        _key_row("key_mixed_05", p["legacy_null"], "legacy",
                 created_via=None),
        # 6. bootstrap, active (24h lifetime, minted 2026-08-01) → NEVER a
        #    managed row.
        _key_row("key_mixed_06", p["boot_active"], None,
                 created_via="bootstrap", expires_at=_EXPIRY_24H),
        # 7. bootstrap, revoked AND expired (sweep semantics — reconcile
        #    sweeps post-expiry) → NEVER a managed row (recovery-cap rotation
        #    can also revoke a not-yet-expired bootstrap — sweep is the
        #    dominant producer, not the only).
        _key_row("key_mixed_07", p["boot_swept"], None,
                 created_via="bootstrap", expires_at=_EXPIRY_24H,
                 revoked_at=_SWEEP_REVOKED_AT),
        # 8. provisioned, revoked → durable revocation stays INLINE (truthful
        #    "revoked", no actions) — never hidden, never "active". The
        #    shipped DOM does not render the revoked_at date (predicate only,
        #    L5566-5576) — visibility + truthful label is what #2166 shipped.
        _key_row("key_mixed_08", p["revoked"], "old ci",
                 created_via="provisioned",
                 revoked_at="2026-08-03T00:00:00.000Z"),
        # 9. provisioned + expires_at (synthetic-so-far — bootstrap is the
        #    only expires_at producer today; dates mirror the 24h shape) →
        #    EXCLUDED by the shipped isManagedKey (any expires_at row). The
        #    scope's pre-ship "Expired" status was superseded (SHIPPED-DESIGN
        #    SUPERSEDE banner).
        _key_row("key_mixed_09", p["expiring"], "expiring",
                 created_via="provisioned", expires_at=_EXPIRY_24H),
        # 10. bootstrap, expired, !revoked (reconcile lag) → NEVER a managed row.
        _key_row("key_mixed_10", p["boot_expired"], None,
                 created_via="bootstrap", expires_at=_EXPIRY_24H),
        # 11. created_via ABSENT (stale-cache shape), non-held → active
        #     (accepted limitation pinned in the DOM; unheld stale bootstrap
        #     rows can show as active — retired fallback, accepted). The key
        #     is DROPPED, not null — the stale shape predates #1708 D7.
        _absent_via_legacy(),
    ]


def _absent_via_legacy() -> dict:
    row = _key_row("key_mixed_11", PREFIXES["absent_via"], "stale shape")
    row.pop("created_via")
    return row


def _wire_mixed_harness(page: Page, keys: list[dict], mint_calls: list | None = None) -> None:
    """Cookie-seeded session + layered api mock (gate.py style, §S5): teams
    rows with role:'owner', a localStorage-seeded HELD_KEY adopted via the
    mount probe (GET /v1/team with the key's Bearer → 200 TEAM_ROW → 5b
    adopt — #2167: the mount never mints), GET /v1/team/keys returns the
    mixed fixture. POST /v1/session/key is a loud 500 + counter — the
    #2167 zero-mint tripwire (no dashboard interaction may mint)."""
    user_id = "u-mixed2178"
    mint_calls = mint_calls if mint_calls is not None else []

    def handle(route):
        url = route.request.url
        if url.startswith(API_HOST):
            # #1828: loadAll pins ?team_id= on overview reads — match on the
            # query-stripped path so /v1/team/keys?team_id=… still resolves.
            path = urllib.parse.urlsplit(url).path
            if path.endswith("/v1/session/key") and route.request.method == "POST":
                # #2167 zero-mint tripwire: no dashboard interaction may issue
                # POST /v1/session/key (the endpoint stays for recovery +
                # non-dashboard consumers, but the mount never calls it).
                mint_calls.append(route.request.post_data or "")
                route.fulfill(status=500, content_type="application/json",
                              body=json.dumps({"detail": "loud 500 — #2167 zero-mint tripwire"}))
                return
            if path.endswith("/v1/teams") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([TEAM_ROW]))
                return
            if path.endswith("/v1/team/keys"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"keys": keys}))
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
                # #2167: this 200 also answers the MOUNT PROBE (the raw
                # key-authed GET /v1/team) → the seeded HELD_KEY is adopted.
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(TEAM_ROW))
                return
            # Everything else (graphs/members/alerts/…) — deterministic 401
            # so the app shell renders without a real network round trip.
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
    page.context.add_cookies([{
        "name": "sb-tortoise-auth-token",
        "value": urllib.parse.quote(json.dumps(_session_json(user_id))),
        "domain": ".premiselabs.co", "path": "/",
    }])
    # #2167: the held durable arrives via localStorage (the post-#2211
    # durable-only world) — the mount probe adopts it; the mint route that
    # used to supply it is gone.
    page.add_init_script(f"localStorage.setItem('tortoise_api_key', '{HELD_KEY}');")


def _open_keys_tab(page: Page, mint_calls: list | None = None) -> None:
    """Boot the dashboard shell (localStorage-seeded durable adopted via the
    mount probe) and open the API Keys tab — the fixture rows load at mount
    (loadAll rides the session JWT, #1828) and render on tab activation."""
    _wire_mixed_harness(page, _mixed_keys_fixture(), mint_calls=mint_calls)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=25_000)
    page.locator('[data-tab="keys"]').click()
    # The keys table is the only <table> in the active tab's DOM (BackupsCard
    # is a div card; other tab sections don't render when inactive).
    expect(page.locator("tbody tr")).to_have_count(7, timeout=15_000)


def test_zero_session_key_posts_on_boot_and_keys_tab(page: Page) -> None:
    """#2167 F1/F6 (CI home): a fresh session login + keys-tab open issues
    ZERO POST /v1/session/key — the mount probe adopts the seeded durable
    and every render rides the session JWT. The route is a loud 500 + counter
    so a regression mint fails the journey instead of silently passing."""
    mint_calls: list = []
    _open_keys_tab(page, mint_calls=mint_calls)
    assert mint_calls == [], f"zero-mint tripwire: POST /v1/session/key fired: {mint_calls}"
    # The probe-adopted held key renders as the dashboard's durable row
    # (in-use suppression) — the durable-only world's mount path.
    held = page.locator("tbody tr", has_text=HELD_PREFIX)
    expect(held.locator("span.live")).to_contain_text("active")
    expect(held).to_contain_text("in use by this dashboard")


def test_mixed_table_shows_only_durable_rows_with_truthful_statuses(page: Page) -> None:
    """Durable rows render with truthful statuses + the right action surface;
    bootstrap/expiring rows NEVER render; no banned vocabulary on the page."""
    _open_keys_tab(page)

    # ── Positive control (row 1): provisioned active → full actions ──
    ctl = page.locator("tbody tr", has_text=PREFIXES["active_ctl"])
    expect(ctl.locator("span.live")).to_contain_text("active")
    expect(ctl.locator(".key-toggle")).to_have_attribute("aria-checked", "true")
    expect(ctl.locator(".key-toggle")).to_have_attribute("data-on", "true")
    expect(ctl.locator(".key-trash")).to_be_visible()
    expect(ctl.locator(".key-rename")).to_be_visible()

    # ── Row 2: disabled is "disabled", NOT "active" (the #2166 lie) ──
    dis = page.locator("tbody tr", has_text=PREFIXES["disabled"])
    expect(dis.locator("span.dim")).to_contain_text("disabled")
    expect(dis).not_to_contain_text("active")
    expect(dis.locator(".key-toggle")).to_have_attribute("aria-checked", "false")
    expect(dis.locator(".key-toggle")).to_have_attribute("data-on", "false")
    # A disabled durable key stays manageable (toggle back on / rename /
    # revoke) — never a ghost row.
    expect(dis.locator(".key-trash")).to_be_visible()
    expect(dis.locator(".key-rename")).to_be_visible()

    # ── Row 3: the held durable key — visible in-use note, no toggle/trash,
    #    rename kept (the #2166 P2 disclosure, not a hover title) ──
    held = page.locator("tbody tr", has_text=HELD_PREFIX)
    expect(held.locator("span.live")).to_contain_text("active")
    expect(held).to_contain_text(
        "in use by this dashboard — to rotate, create a new key and revoke this one")
    expect(held.locator(".key-toggle")).to_have_count(0)
    expect(held.locator(".key-trash")).to_have_count(0)
    expect(held.locator(".key-rename")).to_be_visible()

    # ── Row 4: recovery non-live residual → active + actionable ──
    res = page.locator("tbody tr", has_text=PREFIXES["recovery_resid"])
    expect(res.locator("span.live")).to_contain_text("active")
    expect(res.locator(".key-toggle")).to_be_visible()
    expect(res.locator(".key-trash")).to_be_visible()

    # ── Row 5: legacy NULL created_via → active + actionable ──
    leg = page.locator("tbody tr", has_text=PREFIXES["legacy_null"])
    expect(leg.locator("span.live")).to_contain_text("active")
    expect(leg.locator(".key-trash")).to_be_visible()

    # ── Row 8: durable revocation stays inline + truthful ("revoked", never
    #    "active", no actions — revoked rows are terminal) ──
    rev = page.locator("tbody tr", has_text=PREFIXES["revoked"])
    expect(rev.locator("span.revoked")).to_contain_text("revoked")
    expect(rev).not_to_contain_text("active")
    expect(rev.locator(".key-toggle")).to_have_count(0)
    expect(rev.locator(".key-trash")).to_have_count(0)
    expect(rev.locator(".key-rename")).to_have_count(0)

    # ── Row 11: absent created_via (stale-cache shape) → active ──
    stale = page.locator("tbody tr", has_text=PREFIXES["absent_via"])
    expect(stale.locator("span.live")).to_contain_text("active")
    expect(stale.locator(".key-trash")).to_be_visible()

    # ── Bootstrap / expiring rows NEVER render (prefix-scoped) ──
    # 6 bootstrap active · 7 bootstrap swept-revoked · 9 provisioned+
    # expires_at · 10 bootstrap expired — all excluded by isManagedKey.
    for never in (PREFIXES["boot_active"], PREFIXES["boot_swept"],
                  PREFIXES["expiring"], PREFIXES["boot_expired"]):
        expect(page.locator("code", has_text=never)).to_have_count(0)

    # No empty-state row (the fixture has 7 durable rows).
    expect(page.locator("tbody", has_text="No keys yet.")).to_have_count(0)

    # ── Banned user-facing vocabulary (scope §S5 body greps) ──
    body = page.locator("body")
    expect(body).not_to_contain_text("ephemeral")
    expect(body).not_to_contain_text("durable")
    expect(body).not_to_contain_text("session credential")


def test_held_key_prefix_is_unique_in_fixture(page: Page) -> None:
    """Load-bearing invariant (scope §S5): the held prefix (tt_live_re,
    slice(0,10) of the minted plaintext) must appear on EXACTLY one fixture
    row — a second match would double-fire isActiveKey and demote the
    positive control's action assertions. Pure data check, no navigation."""
    prefixes = [k["key_prefix"] for k in _mixed_keys_fixture()]
    assert prefixes.count(HELD_PREFIX) == 1, prefixes
    # Every fixture row carries a unique prefix (row-scoped assertions rely
    # on it — no prefix may be a strict substring of another either, or the
    # has_text row selectors would match two rows).
    for p in prefixes:
        assert prefixes.count(p) == 1, f"duplicate prefix {p}"
    for a in prefixes:
        for b in prefixes:
            if a != b:
                assert a not in b, f"prefix {a} is a substring of {b}"
    # The fixture shape is the shipped predicate's full domain: every row has
    # exactly one of the three server lanes, and bootstrap/expiry rows are
    # the only non-managed ones (keeps the render test honest to the model).
    managed = [k for k in _mixed_keys_fixture()
               if not (k.get("created_via") == "bootstrap" or k.get("expires_at"))]
    assert len(managed) == 7, f"expected 7 durable rows, got {len(managed)}"


def test_bootstrap_rows_never_render_a_session_zone(page: Page) -> None:
    """#2166 amendment (supersede): auto-minted session credentials are never
    rendered — not as rows AND not as a labeled zone. The shipped DOM has no
    'Temporary access keys' section, no swept bin, no filter toggle."""
    _open_keys_tab(page)
    body = page.locator("body")
    expect(body).not_to_contain_text("Temporary access keys")
    expect(body).not_to_contain_text("Show expired temporary keys")
    expect(body).not_to_contain_text("Hide expired temporary keys")
    # The 7 managed rows are the ONLY rows (the table never degrades into a
    # mixed managed+session listing).
    expect(page.locator("tbody tr")).to_have_count(7)
    # And the keys state holds 11 rows server-side — the client is filtering,
    # not the mock (the fixture itself is the 11-row §S5 matrix).
    assert len(_mixed_keys_fixture()) == 11


def test_two_team_session_only_backups_pin_selected_team(page: Page) -> None:
    """#2167 F2 (the plan's step-10 two-team CI case — structurally invisible
    to a single-team suite): with ZERO keys (no stored durable, no mint), a
    multi-membership user whose SELECTED team ≠ first membership sees the
    SELECTED team's Backups data. The session-mode /backups call must pin
    ?team_id=<selected> (rule 2) — the pre-#2167 shape team-scoped by the KEY
    header, so a zero-key + non-default-team session silently rendered the
    first membership's backups (server /backups → ungated → resolves
    memberships[0] without the param). Zero POST /v1/session/key throughout."""
    import re as _re
    # NOTE: the shell reads t.team_name (main.jsx) — `name` alone renders
    # empty (identity.py fixture convention); team_name drives the switcher.
    team_a = {"team_id": "team_a", "team_name": "Alpha", "tier": "free",
              "role": "owner", "anon": False}
    team_b = {"team_id": "team_b", "team_name": "Bravo", "tier": "free",
              "role": "owner", "anon": False}
    backup_reads: list = []
    mint_calls: list = []

    def handle(route):
        url = route.request.url
        if url.startswith(API_HOST):
            path = urllib.parse.urlsplit(url).path
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            tid = (qs.get("team_id") or ["team_a"])[0]
            if path.endswith("/v1/session/key") and route.request.method == "POST":
                mint_calls.append(route.request.post_data or "")
                route.fulfill(status=500, content_type="application/json",
                              body=json.dumps({"detail": "loud 500 — #2167 zero-mint tripwire"}))
                return
            if path.endswith("/v1/teams") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([team_a, team_b]))
                return
            if path.endswith("/v1/onboarding/state") and route.request.method == "GET":
                # onboarded → the shell stays in the dashboard (no wizard)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"onboarding": {"onboarding_complete": True}}))
                return
            if path.endswith("/v1/user/identity") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"methods": [], "login_methods": 0,
                                                "banner": {"show": False}}))
                return
            if path.endswith("/backups"):
                backup_reads.append(tid)
                # distinct per-team payloads — wrong-team data is VISIBLE
                rows = [{"id": "bk-b1"}, {"id": "bk-b2"}] if tid == "team_b" else [{"id": "bk-a1"}]
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"backups": rows}))
                return
            if path.endswith("/v1/team/keys"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"keys": []}))
                return
            if path.endswith("/v1/sessions"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"sessions": []}))
                return
            if path.endswith("/v1/team") or path.endswith("/v1/team/"):
                t = team_b if tid == "team_b" else team_a
                route.fulfill(status=200, content_type="application/json", body=json.dumps(t))
                return
            if path.endswith("/v1/graphs") or path.endswith("/v1/team/alerts"):
                route.fulfill(status=200, content_type="application/json", body="[]")
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
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
    page.context.add_cookies([{
        "name": "sb-tortoise-auth-token",
        "value": urllib.parse.quote(json.dumps(_session_json("u-two-team"))),
        "domain": ".premiselabs.co", "path": "/",
    }])
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=25_000)
    # Switch to Bravo (≠ first membership) via the account menu — session-only
    # (zero keys held: the switch adopts nothing and mints nothing).
    # expect_response pumps the Playwright sync event loop while waiting —
    # the ?team_id= pin must reach the API (a dropped pin cannot false-pass).
    page.get_by_role("button", name=_re.compile(r"Account menu")).click()
    with page.expect_response(lambda r: "/backups" in r.url and "team_id=team_b" in r.url,
                              timeout=15000):
        page.locator(".account-menu").get_by_role("button", name="Bravo").click()
    # The Backups read after the switch must pin team_b (rule 2).
    expect(page.locator("body")).to_contain_text("Bravo", timeout=15_000)
    assert "team_b" in backup_reads, f"/backups must pin ?team_id=team_b after the switch: {backup_reads}"
    # UI check: open the API Keys tab (the relocated BackupsCard) — the
    # count reflects team B's payload, never Alpha's.
    page.locator('[data-tab="keys"]').click()
    expect(page.locator("body")).to_contain_text("Backups", timeout=15_000)
    assert mint_calls == [], f"zero-mint tripwire: POST /v1/session/key fired: {mint_calls}"
