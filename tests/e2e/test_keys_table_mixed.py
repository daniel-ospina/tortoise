"""#2166/#2178/#2246 keys-table mixed-fixture render e2e (RUN_DASHBOARD_E2E opt-in).

The API Keys table's render behavior (#2166, merged PR #2175) is a render
ternary in main.jsx that no unit test can reach (main.jsx has no component
harness) and no existing dashboard e2e exercised — the shared harness mocks
GET /v1/team/keys with `{"keys": []}` (gate.py `_mock_bootstrap_200`), so the
bug class #2166 fixed had no regression pin. This is the mixed-table
dashboard fixture (scope doc §S5, AC5 follow-up #2178):

  DURABLE rows render uniform (#2246 — ADR-010 session-only): the browser
  never holds an API key, so NO row is "in use by this dashboard" and NO row
  is rotate-only/delete-suppressed. Every durable (non-revoked) row carries
  the SAME owner action set — Rotate + toggle + trash + rename (#2229's
  held-row-only Rotate scope dies with the held key):
    - provisioned active -> "active" + full actions (positive control)
    - provisioned disabled -> "disabled" — NOT "active" (the #2166 lie)
    - recovery durable (incl. the legacy localStorage-seeded residue that
      used to be "the held key") -> active + FULL actions — uniform rows
    - recovery non-live residual -> active + actionable
    - legacy NULL created_via -> active + actionable
    - provisioned revoked -> truthful inline "revoked" (never hidden, never
      "active", terminal — no actions)
    - absent created_via (stale-cache shape) -> active + actionable
  BOOTSTRAP / EXPIRING rows NEVER render (isManagedKey excludes
  created_via==='bootstrap' OR any expires_at row — the shipped predicate).

#2246 session-only additions:
  - The mount stored-key probe is DELETED: a localStorage-seeded residue
    (the old #2167 "held durable" seed) is NEVER adopted — the session mount
    purges the slot once and every dashboard request rides the session JWT.
    Harness header-sniffing asserts ZERO "Bearer tt_" Authorization headers
    anywhere (Indicator 2 / "no key-authed request fires").
  - Rotate is exercised on a NON-held durable row and MUST NOT rewrite
    localStorage (no held install — the replacement is shown once only).

Harness (pinned in scope doc §S5 — "own layered route handler; gate.py's 4
empty-keys tests untouched"):
- Same two-server harness as test_session_login_flow.py / test_dashboard_gate.py:
  `wrangler@4 pages dev . --port 8788` from website/ (auth) +
  `wrangler@4 pages dev dist --port 8790` from website/apps/dashboard/.
- Cookie-seeded session (sb-tortoise-auth-token on .premiselabs.co) ->
  app.premiselabs.co (proxied to :8790). #2246: the mount NEVER probes and
  NEVER mints; POST /v1/session/key is a loud-500 zero-mint tripwire.
- Mocked /v1/teams rows carry role:'owner' (no existing dashboard e2e mock
  supplies role -> isOwnerAdmin would be false -> every action assertion
  vacuous).
- Fixture rows carry UNIQUE key_prefixes — the row-scoped selectors depend on
  prefix uniqueness (a second row sharing a prefix would double-match).

Zone-scoped by prefix; body-level greps: no ephemeral/durable/
session-credential user-facing strings (the shipped DOM renders none); no
"in use by this dashboard" note remains.
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
    # this suite MUST (scope §S5): without it every toggle/trash/rename/rotate
    # assertion is vacuous.
    "role": "owner",
}

# #2246: the legacy localStorage residue that used to be "the held durable"
# (adopted via the #2167 mount probe). The probe is deleted — the residue is
# seeded so the suite PROVES it is ignored + purged, and row 3 (the fixture
# row carrying this prefix) renders as a plain uniform durable row.
LEGACY_RESIDUE = "tt_live_recovery_key_abcdef0123456789"
RESIDUE_PREFIX = LEGACY_RESIDUE[:10]  # tt_live_re

# #2229/#2246 rotate-flow constants: the mocked replacement mint returns this
# plaintext + a row whose key_prefix is its slice(0,10). Rotate is now a
# uniform row action — exercised on the row that USED to be "held" (row 3) to
# prove it rotates like any other durable.
ROT_HELD_ID = "key_mixed_03"  # the fixture row formerly known as "held"
ROT_NEW_HELD = "tt_rot_new_abcdef0123456789"
ROT_NEW_PREFIX = ROT_NEW_HELD[:10]  # tt_rot_new

# Neutral hex-suffix prefixes — never embed banned user-facing vocabulary.
PREFIXES = {
    "active_ctl": "tt_0a1b2c3d",      # row 1: provisioned enabled (positive control)
    "disabled": "tt_0e1f2a3b",        # row 2: provisioned disabled
    "residue": RESIDUE_PREFIX,        # row 3: recovery durable w/ legacy residue prefix
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
        # 1. provisioned, enabled -> active + full actions (positive control)
        _key_row("key_mixed_01", p["active_ctl"], "ci runner",
                 created_via="provisioned"),
        # 2. provisioned, disabled -> truthful "disabled", toggle off + actions
        _key_row("key_mixed_02", p["disabled"], "staging",
                 created_via="provisioned", enabled=False),
        # 3. recovery durable carrying the legacy residue prefix -> plain
        #    uniform row (#2246: nothing is "held" anymore).
        _key_row("key_mixed_03", p["residue"], "residue row",
                 created_via="recovery"),
        # 4. recovery, non-live -> active + actionable.
        _key_row("key_mixed_04", p["recovery_resid"], "recovery leftover",
                 created_via="recovery"),
        # 5. legacy registry key (NULL created_via, no expiry) -> active.
        _key_row("key_mixed_05", p["legacy_null"], "legacy",
                 created_via=None),
        # 6. bootstrap, active (24h lifetime, minted 2026-08-01) -> NEVER a
        #    managed row.
        _key_row("key_mixed_06", p["boot_active"], None,
                 created_via="bootstrap", expires_at=_EXPIRY_24H),
        # 7. bootstrap, revoked AND expired (sweep semantics) -> NEVER a
        #    managed row.
        _key_row("key_mixed_07", p["boot_swept"], None,
                 created_via="bootstrap", expires_at=_EXPIRY_24H,
                 revoked_at=_SWEEP_REVOKED_AT),
        # 8. provisioned, revoked -> durable revocation stays INLINE (truthful
        #    "revoked", terminal — no actions).
        _key_row("key_mixed_08", p["revoked"], "old ci",
                 created_via="provisioned",
                 revoked_at="2026-08-03T00:00:00.000Z"),
        # 9. provisioned + expires_at -> EXCLUDED by isManagedKey.
        _key_row("key_mixed_09", p["expiring"], "expiring",
                 created_via="provisioned", expires_at=_EXPIRY_24H),
        # 10. bootstrap, expired, !revoked -> NEVER a managed row.
        _key_row("key_mixed_10", p["boot_expired"], None,
                 created_via="bootstrap", expires_at=_EXPIRY_24H),
        # 11. created_via ABSENT (stale-cache shape), non-held -> active
        #     (accepted limitation pinned in the DOM).
        _absent_via_legacy(),
    ]


def _absent_via_legacy() -> dict:
    row = _key_row("key_mixed_11", PREFIXES["absent_via"], "stale shape")
    row.pop("created_via")
    return row


def _wire_mixed_harness(page: Page, keys: list[dict], mint_calls: list | None = None,
                        key_authed: list | None = None) -> None:
    """Cookie-seeded session + layered api mock (gate.py style, §S5): teams
    rows with role:'owner', a localStorage-seeded LEGACY_RESIDUE that the
    mount PURGES (never probed/adopted — #2246), GET /v1/team/keys returns
    the mixed fixture. POST /v1/session/key is a loud 500 + counter — the
    #2167 zero-mint tripwire. key_authed collects any request whose
    Authorization is a Bearer tt_ key (must stay empty — session JWT only)."""
    user_id = "u-mixed2178"
    mint_calls = mint_calls if mint_calls is not None else []
    key_authed = key_authed if key_authed is not None else []

    def handle(route):
        url = route.request.url
        if url.startswith(API_HOST):
            # #1828: loadAll pins ?team_id= on overview reads — match on the
            # path so /v1/team/keys?team_id=… still resolves.
            path = urllib.parse.urlsplit(url).path
            auth = (route.request.headers.get("authorization") or "")
            if auth.startswith("Bearer tt_"):
                # #2246: NO key-authed request may fire in session mode —
                # every read/management call rides the session JWT.
                key_authed.append(url)
            if path.endswith("/v1/session/key") and route.request.method == "POST":
                # #2167 zero-mint tripwire: no dashboard interaction may issue
                # POST /v1/session/key (the endpoint stays for recovery +
                # non-dashboard consumers, but the dashboard never calls it).
                mint_calls.append(route.request.post_data or "")
                route.fulfill(status=500, content_type="application/json",
                              body=json.dumps({"detail": "loud 500 — #2167 zero-mint tripwire"}))
                return
            if path.endswith("/v1/teams") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([TEAM_ROW]))
                return
            if path.endswith("/v1/team/keys") and route.request.method == "GET":
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
                # #2246: this answers completeLogin's SESSION read — the
                # key-lane probe leg is deleted.
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
    # #2246: legacy residue seeded — the session mount purges it; the suite
    # asserts the purge + zero adoption (never probed, never held).
    page.add_init_script(f"localStorage.setItem('tortoise_api_key', '{LEGACY_RESIDUE}');")


def _open_keys_tab(page: Page, mint_calls: list | None = None,
                   key_authed: list | None = None) -> None:
    """Boot the dashboard shell (legacy residue purged at the session mount)
    and open the API Keys tab — the fixture rows load at mount (loadAll rides
    the session JWT, #1828) and render on tab activation."""
    _wire_mixed_harness(page, _mixed_keys_fixture(), mint_calls=mint_calls,
                        key_authed=key_authed)
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=25_000)
    page.locator('[data-tab="keys"]').click()
    # The keys table is the only <table> in the active tab's DOM (BackupsCard
    # is a div card; other tab sections don't render when inactive).
    expect(page.locator("tbody tr")).to_have_count(7, timeout=15_000)


def test_zero_session_key_posts_and_zero_key_authed_requests(page: Page) -> None:
    """#2167 F1/F6 + #2246 (CI home): a fresh session login + keys-tab open
    issues ZERO POST /v1/session/key AND ZERO key-authed requests — the mount
    never probes/mints, the localStorage residue is purged (never adopted),
    and every render rides the session JWT. The route is a loud 500 + counter
    so a regression mint fails the journey instead of silently passing."""
    mint_calls: list = []
    key_authed: list = []
    _open_keys_tab(page, mint_calls=mint_calls, key_authed=key_authed)
    assert mint_calls == [], f"zero-mint tripwire: POST /v1/session/key fired: {mint_calls}"
    assert key_authed == [], f"#2246: key-authed requests must not fire in session mode: {key_authed}"
    # The session mount purged the residue once (no adoption, no held row).
    assert page.evaluate("localStorage.getItem('tortoise_api_key')") is None
    # Row 3 (the former "held" row) renders as a plain uniform durable row.
    row3 = page.locator("tbody tr", has_text=RESIDUE_PREFIX)
    expect(row3.locator("span.live")).to_contain_text("active")
    expect(row3.locator(".key-toggle")).to_be_visible()
    expect(row3.locator(".key-trash")).to_be_visible()


def test_mixed_table_shows_only_durable_rows_with_truthful_statuses(page: Page) -> None:
    """Durable rows render with truthful statuses + the SAME uniform action
    set (rotate + toggle + trash + rename); bootstrap/expiring rows NEVER
    render; NO "in use by this dashboard" note anywhere; no banned
    vocabulary."""
    _open_keys_tab(page)

    # — Positive control (row 1): provisioned active -> full actions —
    ctl = page.locator("tbody tr", has_text=PREFIXES["active_ctl"])
    expect(ctl.locator("span.live")).to_contain_text("active")
    expect(ctl.locator(".key-toggle")).to_have_attribute("aria-checked", "true")
    expect(ctl.locator(".key-toggle")).to_have_attribute("data-on", "true")
    expect(ctl.locator(".key-trash")).to_be_visible()
    expect(ctl.locator(".key-rename")).to_be_visible()
    # #2246 (uniform rows): Rotate renders on EVERY durable row now — no
    # held row exists to be rotate-only (#2229's scope dies with held state).
    expect(ctl.locator(".key-rotate")).to_be_visible()

    # — Row 2: disabled is "disabled", NOT "active" (the #2166 lie) —
    dis = page.locator("tbody tr", has_text=PREFIXES["disabled"])
    expect(dis.locator("span.dim")).to_contain_text("disabled")
    expect(dis).not_to_contain_text("active")
    expect(dis.locator(".key-toggle")).to_have_attribute("aria-checked", "false")
    expect(dis.locator(".key-toggle")).to_have_attribute("data-on", "false")
    # A disabled durable key stays manageable (toggle back on / rename /
    # revoke / rotate) — never a ghost row.
    expect(dis.locator(".key-trash")).to_be_visible()
    expect(dis.locator(".key-rename")).to_be_visible()
    expect(dis.locator(".key-rotate")).to_be_visible()

    # — Row 3: the former "held" (recovery) row -> UNIFORM (no in-use note,
    #   no rotate-only suppression — #2246) —
    row3 = page.locator("tbody tr", has_text=RESIDUE_PREFIX)
    expect(row3.locator("span.live")).to_contain_text("active")
    expect(row3).not_to_contain_text("in use by this dashboard")
    expect(row3.locator(".key-toggle")).to_be_visible()
    expect(row3.locator(".key-trash")).to_be_visible()
    expect(row3.locator(".key-rotate")).to_be_visible()
    expect(row3.locator(".key-rename")).to_be_visible()

    # — Row 4: recovery non-live residual -> active + actionable —
    res = page.locator("tbody tr", has_text=PREFIXES["recovery_resid"])
    expect(res.locator("span.live")).to_contain_text("active")
    expect(res.locator(".key-toggle")).to_be_visible()
    expect(res.locator(".key-trash")).to_be_visible()
    expect(res.locator(".key-rotate")).to_be_visible()

    # — Row 5: legacy NULL created_via -> active + actionable —
    leg = page.locator("tbody tr", has_text=PREFIXES["legacy_null"])
    expect(leg.locator("span.live")).to_contain_text("active")
    expect(leg.locator(".key-trash")).to_be_visible()
    expect(leg.locator(".key-rotate")).to_be_visible()

    # — Row 8: durable revocation stays inline + truthful ("revoked", never
    #   "active", terminal — no actions at all) —
    rev = page.locator("tbody tr", has_text=PREFIXES["revoked"])
    expect(rev.locator("span.revoked")).to_contain_text("revoked")
    expect(rev).not_to_contain_text("active")
    expect(rev.locator(".key-toggle")).to_have_count(0)
    expect(rev.locator(".key-trash")).to_have_count(0)
    expect(rev.locator(".key-rename")).to_have_count(0)
    expect(rev.locator(".key-rotate")).to_have_count(0)

    # — Row 11: absent created_via (stale-cache shape) -> active —
    stale = page.locator("tbody tr", has_text=PREFIXES["absent_via"])
    expect(stale.locator("span.live")).to_contain_text("active")
    expect(stale.locator(".key-trash")).to_be_visible()

    # — Uniformity invariant: EXACTLY 6 Rotate affordances — one per
    #   non-revoked durable row (rows 1,2,3,4,5,11; revoked row 8 terminal;
    #   bootstrap/expiring rows never render) —
    expect(page.locator(".key-rotate")).to_have_count(6)

    # — Bootstrap / expiring rows NEVER render (prefix-scoped) —
    for never in (PREFIXES["boot_active"], PREFIXES["boot_swept"],
                  PREFIXES["expiring"], PREFIXES["boot_expired"]):
        expect(page.locator("code", has_text=never)).to_have_count(0)

    # No empty-state row (the fixture has 7 durable rows).
    expect(page.locator("tbody", has_text="No keys yet.")).to_have_count(0)

    # — Banned user-facing vocabulary + the gone held-row note —
    body = page.locator("body")
    expect(body).not_to_contain_text("in use by this dashboard")
    expect(body).not_to_contain_text("ephemeral")
    expect(body).not_to_contain_text("durable")
    expect(body).not_to_contain_text("session credential")


def test_rotate_durable_key_replaces_in_place_without_holding(page: Page) -> None:
    """#2229/#2246: the uniform Rotate action on a NON-held durable row — one
    click + confirm -> the replacement is minted FIRST (POST /v1/team/keys,
    old row's label carried over), the old key is revoked (DELETE
    /v1/team/keys/{id}), the replacement is shown once (never installed —
    #2246: localStorage is NOT rewritten), and the old row re-renders
    truthful "revoked" with NO actions. Zero POST /v1/session/key and zero
    key-authed requests.

    Stateful harness (the shared _wire_mixed_harness serves a STATIC keys
    list — this flow mutates it): the mint handler appends the replacement
    row + returns its plaintext; the DELETE handler stamps revoked_at on the
    rotated row; the final loadAll re-reads the mutated list."""
    keys = _mixed_keys_fixture()
    session_mints: list = []
    key_authed: list = []
    minted_bodies: list = []
    order: list = []  # #2229: pin mint-before-revoke ordering

    def handle(route):
        url = route.request.url
        if url.startswith(API_HOST):
            path = urllib.parse.urlsplit(url).path
            method = route.request.method
            auth = (route.request.headers.get("authorization") or "")
            if auth.startswith("Bearer tt_"):
                key_authed.append(url)
            if path.endswith("/v1/session/key") and method == "POST":
                # #2167 zero-mint tripwire — rotate must never mint a session key.
                session_mints.append(route.request.post_data or "")
                route.fulfill(status=500, content_type="application/json",
                              body=json.dumps({"detail": "loud 500 — #2167 zero-mint tripwire"}))
                return
            if path.endswith("/v1/team/keys") and method == "POST":
                # The rotate replacement mint. #2229: label carry-over.
                order.append("mint")
                minted_bodies.append(route.request.post_data or "")
                row = _key_row("key_rot_2229", ROT_NEW_PREFIX,
                               "residue row", created_via="provisioned")
                keys.append(row)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"id": row["id"],
                                               "api_key": ROT_NEW_HELD,
                                               "key_prefix": row["key_prefix"]}))
                return
            if path.endswith(f"/v1/team/keys/{ROT_HELD_ID}") and method == "DELETE":
                order.append("delete")
                for k in keys:
                    if k["id"] == ROT_HELD_ID:
                        k["revoked_at"] = "2026-08-03T12:00:00.000Z"
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"revoked": True, "key_id": ROT_HELD_ID}))
                return
            if path.endswith("/v1/teams") and method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([TEAM_ROW]))
                return
            if path.endswith("/v1/team/keys") and method == "GET":
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
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(TEAM_ROW))
                return
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
        "value": urllib.parse.quote(json.dumps(_session_json("u-rot2229"))),
        "domain": ".premiselabs.co", "path": "/",
    }])
    # #2246: legacy residue seeded (the former "held" seed) — the mount
    # purges it; rotate must NEVER re-install anything into the slot.
    page.add_init_script(f"localStorage.setItem('tortoise_api_key', '{LEGACY_RESIDUE}');")

    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=25_000)
    page.locator('[data-tab="keys"]').click()
    expect(page.locator("tbody tr")).to_have_count(7, timeout=15_000)

    # Rotate row 3 (the durable formerly known as "held") — a uniform action.
    row3 = page.locator("tbody tr", has_text=RESIDUE_PREFIX)
    expect(row3.locator(".key-rotate")).to_be_visible()
    # Native confirm() (regenerateKey) — Playwright must register a handler
    # BEFORE the click or the dialog auto-dismisses and the flow aborts.
    confirm_msgs: list = []
    page.on("dialog", lambda d: (confirm_msgs.append(d.message), d.accept()))
    row3.locator(".key-rotate").click()
    # #2246 (PM-1): the confirm names the row (name · prefix · created) so a
    # one-click rotate never silently kills an unidentifiable agent key.
    assert confirm_msgs and "Rotate residue row" in confirm_msgs[0], confirm_msgs
    assert RESIDUE_PREFIX in confirm_msgs[0], confirm_msgs
    # The replacement is shown once (never installed into localStorage).
    expect(page.locator(".new-key")).to_contain_text("Your new key (shown once)", timeout=15_000)
    expect(page.locator(".new-key code.key-value")).to_have_text(ROT_NEW_HELD, timeout=15_000)
    # The rotated row re-renders truthful revoked + terminal.
    expect(row3.locator("span.revoked")).to_contain_text("revoked", timeout=15_000)
    expect(row3.locator(".key-toggle")).to_have_count(0)
    expect(row3.locator(".key-trash")).to_have_count(0)
    expect(row3.locator(".key-rotate")).to_have_count(0)
    # The replacement row appeared (provisioned active, full actions).
    newrow = page.locator("tbody tr", has_text=ROT_NEW_PREFIX)
    expect(newrow.locator("span.live")).to_contain_text("active", timeout=15_000)
    expect(newrow.locator(".key-rotate")).to_be_visible()
    expect(newrow.locator(".key-trash")).to_be_visible()
    # Mint fired before revoke (#2229 ordering) and carried the label over.
    assert order == ["mint", "delete"], f"rotate ordering: {order}"
    assert minted_bodies and '"residue row"' in minted_bodies[0], minted_bodies
    # #2246: nothing was ever installed into the slot — no held install, no
    # re-persist; the new key material exists only in the one-time reveal.
    slot = page.evaluate("localStorage.getItem('tortoise_api_key')")
    assert slot is None, f"#2246: rotate must never install the replacement, got {slot!r}"
    assert session_mints == [], f"zero-mint tripwire: {session_mints}"
    assert key_authed == [], f"#2246: key-authed requests must not fire: {key_authed}"
