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
  BOOTSTRAP rows NEVER render (isManagedKey excludes created_via===
  'bootstrap' — #2426: an EXPIRING DURABLE (created_via provisioned/recovery/
  NULL with expires_at set) IS a product key and renders with its Expires
  state (mint-time lifetime, market presets).

#2246 session-only additions:
  - The mount stored-key probe is DELETED: a localStorage-seeded residue
    (the old #2167 "held durable" seed) is NEVER adopted — the session mount
    purges the slot once and every dashboard request rides the session JWT.
    Harness header-sniffing asserts ZERO "Bearer tt_" Authorization headers
    anywhere (Indicator 2 / "no key-authed request fires").
  - Rotate is exercised on a NON-held durable row and MUST NOT rewrite
    localStorage (no held install — the replacement is shown once only).

#2476 additions (Last used column):
  - Row 1 (the active_ctl positive control) carries a now-relative
    last_used_at (2h ago — mid-bucket, so the relative label is stable for a
    wide window) -> its Last used cell shows "2 hr ago" + an absolute-date
    title tooltip; every other row stays last_used_at None -> plain "Never"
    text (never span.dim — #2426: the status cell's dim identifies
    'disabled', and the disabled row 2's dim assertion would strict-mode
    double-match a Never-in-dim cell).

Harness (pinned in scope doc §S5 — "own layered route handler; gate.py's 4
empty-keys tests untouched"):
- Same two-server harness as test_session_login_flow.py / test_dashboard_gate.py:
  `wrangler@4 pages dev . --port 8788` from website/ (auth) +
  `wrangler@4 pages dev dist --port 8790` from website/apps/dashboard/.
- #2731: the app DOCUMENT is loaded from the LOCAL preview (DASHBOARD_URL,
  :8790), never the prod origin — the route handler is no longer load-bearing
  for the document. API_HOST is intercepted and AUTH_HOST is rewritten to
  :8788; the APP_HOST -> :8790 rewrite stays as a defensive fallback (no
  request in this module originates from the prod app origin).
- Host-only loopback session cookie (sb-tortoise-auth-token for 127.0.0.1) +
  the prod parent-domain cookie so intercepted prod-origin paths stay coherent.
  #2246: the mount NEVER probes and NEVER mints; POST /v1/session/key is a
  loud-500 zero-mint tripwire.
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
import re
import urllib.parse
from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_session_login_flow import (
    API_HOST,
    APP_HOST,
    AUTH_HOST,
    DASHBOARD_URL,
    _goto_local_dashboard,
    _preflight_local_servers,
    _proxy_body,
    _seed_local_session_cookie,
)

if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("dashboard e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

AUTH_ORIGIN = os.environ.get("DASHBOARD_AUTH_BASE", "http://127.0.0.1:8788")


@pytest.fixture(scope="module", autouse=True)
def _local_preview_servers() -> None:
    """#2731: fail fast (one clear error) when :8788/:8790 are not serving."""
    _preflight_local_servers()


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
    "expiring": "tt_0e0d0c0b",        # row 9: provisioned + future expires_at — renders (#2426)
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
        # 1. provisioned, enabled -> active + full actions (positive control).
        #    #2476: also the ONE used row — a now-relative last_used_at so the
        #    Last used cell renders a live relative label (the remaining rows
        #    stay never-used -> plain 'Never').
        _key_row("key_mixed_01", p["active_ctl"], "ci runner",
                 created_via="provisioned", last_used_at=_last_used_2h_ago()),
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
        # 9. provisioned + expires_at (future mint-time lifetime) -> a #2426
        #    expiring DURABLE — renders with an Expires state + full actions
        #    (a 30d key IS a product key; bootstrap-exclusion is the ONLY
        #    hidden class post-#2426).
        _key_row("key_mixed_09", p["expiring"], "expiring",
                 created_via="provisioned",
                 expires_at=_future_expiry_30d()),
        # 10. bootstrap, expired, !revoked -> NEVER a managed row.
        _key_row("key_mixed_10", p["boot_expired"], None,
                 created_via="bootstrap", expires_at=_EXPIRY_24H),
        # 11. created_via ABSENT (stale-cache shape), non-held -> active
        #     (accepted limitation pinned in the DOM).
        _absent_via_legacy(),
    ]


def _future_expiry_30d() -> str:
    """#2426: a stable FUTURE expires_at for the expiring-durable fixture row
    (fixed dates would decay — the Expires cell is wall-clock-relative)."""
    from datetime import UTC, datetime, timedelta
    return (datetime.now(UTC) + timedelta(days=30)).isoformat()


def _last_used_2h_ago() -> str:
    """#2476: a now-relative last_used_at for the used-row fixture — 2h back
    sits mid-bucket for formatRelativeTime (>= 1h -> "N hr ago", so the label
    stays "2 hr ago" until the stamp ages past 3h — a CI run is seconds, not
    hours). Fixed dates would decay the same way #2426's expires_at does."""
    from datetime import UTC, datetime, timedelta
    return (datetime.now(UTC) - timedelta(hours=2)).isoformat()


def _absent_via_legacy() -> dict:
    row = _key_row("key_mixed_11", PREFIXES["absent_via"], "stale shape")
    row.pop("created_via")
    return row


def _wire_mixed_harness(page: Page, keys: list[dict], mint_calls: list | None = None,
                        key_authed: list | None = None,
                        team_row: dict | None = None) -> None:
    """Cookie-seeded session + layered api mock (gate.py style, §S5): teams
    rows with role:'owner', a localStorage-seeded LEGACY_RESIDUE that the
    mount PURGES (never probed/adopted — #2246), GET /v1/team/keys returns
    the mixed fixture. POST /v1/session/key is a loud 500 + counter — the
    #2167 zero-mint tripwire. key_authed collects any request whose
    Authorization is a Bearer tt_ key (must stay empty — session JWT only).
    team_row overrides the /v1/team(s) payload (#3136: dashboard_key_login
    ON/OFF render proof)."""
    user_id = "u-mixed2178"
    row = team_row if team_row is not None else TEAM_ROW
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
                              body=json.dumps([row]))
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
                              body=json.dumps(row))
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
    _seed_local_session_cookie(page, user_id)
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
    _goto_local_dashboard(page)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=25_000)
    page.locator('[data-tab="keys"]').click()
    # The keys table is the only <table> in the active tab's DOM (other tab
    # sections don't render when inactive).
    expect(page.locator("tbody tr")).to_have_count(8, timeout=15_000)


def test_off_state_copy_never_nags(page: Page) -> None:
    """#3136 (render proof): a team whose dashboard_key_login is false reads
    the consequence line on the API Keys tab and NEVER the disable
    recommendation (the pre-fix defect). Positive control below."""
    off = {**TEAM_ROW, "dashboard_key_login": False}
    _wire_mixed_harness(page, _mixed_keys_fixture(), team_row=off)
    _goto_local_dashboard(page)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=25_000)
    page.locator('[data-tab="keys"]').click()
    expect(page.locator("body")).to_contain_text("API key dashboard login", timeout=15_000)
    expect(page.locator("body")).to_contain_text("disabled ✓")
    expect(page.locator("body")).not_to_contain_text("We recommend disabling")
    expect(page.locator("body")).to_contain_text("Your API key can no longer sign in")


def test_on_state_copy_still_recommends(page: Page) -> None:
    """#3136 positive control: while dashboard_key_login is not false (the
    agent-signup cohort) the recommendation still renders — the fix gates the
    copy, it does not delete the nudge."""
    on = {**TEAM_ROW, "dashboard_key_login": True}
    _wire_mixed_harness(page, _mixed_keys_fixture(), team_row=on)
    _goto_local_dashboard(page)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=25_000)
    page.locator('[data-tab="keys"]').click()
    expect(page.locator("body")).to_contain_text("We recommend disabling", timeout=15_000)
    expect(page.locator("body")).not_to_contain_text("Your API key can no longer sign in")


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
    set (rotate + toggle + trash + rename); bootstrap rows NEVER
    render (created_via==='bootstrap'); the #2426 expiring durable row
    RENDERS with its Expires state; NO "in use by this dashboard" note
    anywhere; no banned vocabulary."""
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

    # — #2476: the USED row's Last used cell (Created | Last used | Expires
    #   | Status — cell 4 of 7) shows the formatted relative label with an
    #   absolute-date title tooltip. The stamp is now-relative 2h (mid hour-
    #   bucket), so the label is exactly "2 hr ago" for the whole CI run.
    used_lu = ctl.locator("td").nth(3)
    expect(used_lu).to_have_text("2 hr ago")
    expect(used_lu.locator("span")).to_have_attribute("title", re.compile(r"^Last used "))

    # — Row 2: disabled is "disabled", NOT "active" (the #2166 lie) —
    dis = page.locator("tbody tr", has_text=PREFIXES["disabled"])
    # #2476: span.dim stays EXCLUSIVE to the disabled status — the Last used
    # cell's 'Never' is plain text (a Never-in-dim cell would double-match
    # this strict-mode assertion, the #2426 e2e lesson).
    expect(dis.locator("span.dim")).to_have_count(1)
    expect(dis.locator("span.dim")).to_contain_text("disabled")
    expect(dis).not_to_contain_text("active")
    # #2476: never-used -> the Last used cell reads plain "Never", no span.
    expect(dis.locator("td").nth(3)).to_have_text("Never")
    expect(dis.locator("td").nth(3).locator("span")).to_have_count(0)
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

    # — Uniformity invariant: EXACTLY 7 Rotate affordances — one per
    #   non-revoked durable row (rows 1,2,3,4,5,9,11; revoked row 8
    #   terminal; bootstrap rows never render) —
    expect(page.locator(".key-rotate")).to_have_count(7)

    # — #2426: the expiring durable renders (prefix visible) with an
    #   Expires cell; bootstrap rows NEVER render (prefix-scoped) —
    expiring_row = page.locator("tbody tr", has_text=PREFIXES["expiring"])
    expect(page.locator("code", has_text=PREFIXES["expiring"])).to_have_count(1)
    # #2476: the expiring row is ALSO never-used — its Last used cell reads
    # plain "Never" while the Expires cell carries the future date (a row-
    # scoped bare "Never" grep would be ambiguous here; the cell pin is not).
    expect(expiring_row.locator("td").nth(3)).to_have_text("Never")
    # The new column's header renders exactly once (Created | Last used |
    # Expires | Status order is pinned by the client unit tripwire).
    expect(page.locator("thead th", has_text="Last used")).to_have_count(1)
    for never in (PREFIXES["boot_active"], PREFIXES["boot_swept"],
                  PREFIXES["boot_expired"]):
        expect(page.locator("code", has_text=never)).to_have_count(0)

    # No empty-state row (the fixture has 8 durable rows).
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
    _seed_local_session_cookie(page, "u-rot2229")
    # #2246: legacy residue seeded (the former "held" seed) — the mount
    # purges it; rotate must NEVER re-install anything into the slot.
    page.add_init_script(f"localStorage.setItem('tortoise_api_key', '{LEGACY_RESIDUE}');")

    _goto_local_dashboard(page)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=25_000)
    page.locator('[data-tab="keys"]').click()
    expect(page.locator("tbody tr")).to_have_count(8, timeout=15_000)

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


def test_two_team_session_only_backups_pin_selected_team(page: Page) -> None:
    """#2167 F2 + #3136 (the plan's step-10 two-team CI case — structurally
    invisible to a single-team suite): with ZERO keys (no stored durable, no
    mint), a multi-membership user whose SELECTED team ≠ first membership
    sees the SELECTED team's backup data on the Graphs tab. The session-mode
    /backups call must pin ?team_id=<selected> (rule 2) — the pre-#2167 shape
    team-scoped by the KEY header, so a zero-key + non-default-team session
    silently rendered the first membership's backups (server /backups →
    ungated → resolves memberships[0] without the param). Zero POST
    /v1/session/key throughout."""
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
                # #3136: distinct per-team PER-GRAPH manifests — the Graphs
                # "Last backup" cell renders the SELECTED team's stamp, so
                # wrong-team data is VISIBLE. 3h vs 5h keeps the relative
                # label ("N hr ago") stable for the whole run.
                hours = 3 if tid == "team_b" else 5
                stamp = datetime.now(UTC) - timedelta(hours=hours)
                rows = [{
                    "backup_id": f"{tid}/default/bk", "graph_id": "default",
                    "created_at": stamp.isoformat().replace("+00:00", "Z"),
                    "node_count": 1, "edge_count": 0,
                }]
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
            if path.endswith("/v1/graphs"):
                # #3136: one default graph row so the Last-backup column has
                # a row to render against.
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([{"graph_id": "default", "name": "default",
                                                "kind": "default", "status": "active",
                                                "key_count": 0}]))
                return
            if path.endswith("/v1/team/alerts"):
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
    _seed_local_session_cookie(page, "u-two-team")
    _goto_local_dashboard(page)
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
    # UI check: open the Graphs tab — the Last backup cell reflects team B's
    # per-graph manifest (3 hr ago), never Alpha's (5 hr ago). #3136: the
    # team-wide BackupsCard left the API Keys tab.
    page.locator('[data-tab="graphs"]').click()
    expect(page.locator("body")).to_contain_text("Last backup", timeout=15_000)
    default_row = page.locator("tbody tr").first
    expect(default_row).to_contain_text("3 hr ago", timeout=15_000)
    expect(default_row).not_to_contain_text("5 hr ago")
    assert mint_calls == [], f"zero-mint tripwire: POST /v1/session/key fired: {mint_calls}"


def _managed_row(kid: str, prefix: str, name: str | None) -> dict:
    """Durable (provisioned) key row shape the keys table renders (mirrors
    the mixed fixture rows: created_via='provisioned', no expires_at → the
    row is manageable: toggle/rename/trash all render for owner role)."""
    return {"id": kid, "key_prefix": prefix, "created_at": "2026-09-04T00:00:00Z",
            "last_used_at": None, "revoked_at": None, "enabled": True,
            "name": name, "created_via": "provisioned", "expires_at": None}


def test_two_team_key_writes_pin_selected_team(page: Page) -> None:
    """#2230 F10 (CI home — the #2167 step-10 two-team harness): revoke/
    rename/toggle while the SELECTED team ≠ first membership must pin
    ?team_id=<selected> on DELETE/PATCH /v1/team/keys/{id} (the rule-4
    carve-out: #2167 pinned create/list; revoke/rename/toggle sent no pin,
    so the session server — memberships[0] resolution — 403'd revoking the
    non-first team's key and a PATCH pin was silently ignored server-side).

    Each team's mock rows are distinct and writes resolve ONLY under the
    pinned team (a dropped/wrong pin → 404, fail-loud — the URL assertions
    below cannot false-pass). Zero POST /v1/session/key throughout."""
    import re as _re
    team_a = {"team_id": "team_a", "team_name": "Alpha", "tier": "free",
              "role": "owner", "anon": False}
    team_b = {"team_id": "team_b", "team_name": "Bravo", "tier": "free",
              "role": "owner", "anon": False}
    keys_rows: dict = {
        "team_a": [_managed_row("key_a1", "tt_alpha01", "alpha-ci")],
        "team_b": [_managed_row("key_b1", "tt_bravo01", None)],
    }
    key_writes: list = []
    mint_calls: list = []

    def handle(route):
        url = route.request.url
        if url.startswith(API_HOST):
            path = urllib.parse.urlsplit(url).path
            method = route.request.method
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            tid = (qs.get("team_id") or ["team_a"])[0]
            if path.endswith("/v1/session/key") and method == "POST":
                mint_calls.append(route.request.post_data or "")
                route.fulfill(status=500, content_type="application/json",
                              body=json.dumps({"detail": "loud 500 — #2167 zero-mint tripwire"}))
                return
            if path.endswith("/v1/teams") and method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([team_a, team_b]))
                return
            if path.endswith("/v1/onboarding/state") and method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"onboarding": {"onboarding_complete": True}}))
                return
            if path.endswith("/v1/user/identity") and method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"methods": [], "login_methods": 0,
                                                "banner": {"show": False}}))
                return
            if path.endswith("/v1/team/keys") and method in ("GET", "POST"):
                if method == "POST":
                    route.fulfill(status=500, content_type="application/json",
                                  body=json.dumps({"detail": "no mint in this test"}))
                    return
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"keys": keys_rows.get(tid, [])}))
                return
            if _re.match(r"^/v1/team/keys/[^/]+$", path) and method in ("PATCH", "DELETE"):
                # #2230: key writes resolve ONLY under the pinned team — a
                # dropped/wrong ?team_id= 404s (fail-loud: the test's URL
                # assertions cannot false-pass). The row mutates the team's
                # list so the post-revoke loadAll refetch is truthful.
                kid = path.rsplit("/", 1)[1]
                key_writes.append({"method": method, "url": url})
                team_rows = keys_rows.get(tid, [])
                row = next((x for x in team_rows if x["id"] == kid), None)
                if row is None:
                    route.fulfill(status=404, content_type="application/json",
                                  body=json.dumps({"detail": "API key not found"}))
                    return
                if method == "PATCH":
                    body = json.loads(route.request.post_data or "{}")
                    if "enabled" in body:
                        row["enabled"] = body.get("enabled")
                    if "name" in body:
                        row["name"] = body.get("name")
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps(
                                      {"key_id": kid,
                                       **{k: v for k, v in body.items()
                                          if v is not None}}))
                    return
                team_rows.remove(row)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"revoked": True, "key_id": kid}))
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
                t = team_b if tid == "team_b" else team_a
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(t))
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
    _seed_local_session_cookie(page, "u-key-writes")
    _goto_local_dashboard(page)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=25_000)
    # Select Bravo (≠ first membership Alpha) — session-only (zero keys held)
    page.get_by_role("button", name=_re.compile(r"Account menu")).click()
    with page.expect_response(lambda r: "/v1/team/keys" in r.url and "team_id=team_b" in r.url,
                              timeout=15000):
        page.locator(".account-menu").get_by_role("button", name="Bravo").click()
    expect(page.locator("body")).to_contain_text("Bravo", timeout=15_000)
    page.locator('[data-tab="keys"]').click()
    # Bravo's row is the ONLY row (per-team truth — Alpha's key never renders)
    expect(page.locator("tbody tr")).to_have_count(1, timeout=15_000)
    brow = page.locator("tbody tr", has_text="tt_bravo01")
    expect(brow.locator(".key-toggle")).to_have_attribute("aria-checked", "true")
    expect(brow.locator(".key-rename")).to_be_visible()
    expect(brow.locator(".key-trash")).to_be_visible()

    # ── rename (PATCH {name}) — must pin ?team_id=team_b ──
    with page.expect_response(lambda r: r.request.method == "PATCH"
                              and "/v1/team/keys/" in r.url
                              and "team_id=team_b" in r.url,
                              timeout=15000):
        brow.locator(".key-rename").click()
        brow.locator(".key-name-input").fill("bravo-prod")
        brow.locator(".key-name-input").press("Enter")
    expect(brow).to_contain_text("bravo-prod", timeout=10_000)

    # ── toggle off (PATCH {enabled:false}) — must pin ?team_id=team_b ──
    with page.expect_response(lambda r: r.request.method == "PATCH"
                              and "/v1/team/keys/" in r.url
                              and "team_id=team_b" in r.url,
                              timeout=15000):
        brow.locator(".key-toggle").click()
    expect(brow.locator(".key-toggle")).to_have_attribute("aria-checked", "false", timeout=10_000)
    expect(brow).to_contain_text("disabled", timeout=10_000)

    # ── revoke (DELETE) — must pin ?team_id=team_b; confirm accepted ──
    page.once("dialog", lambda d: d.accept())
    with page.expect_response(lambda r: r.request.method == "DELETE"
                              and "/v1/team/keys/" in r.url
                              and "team_id=team_b" in r.url,
                              timeout=15000):
        brow.locator(".key-trash").click()
    # the revoked row is gone from Bravo's table (loadAll refetch)
    expect(page.locator("tbody tr", has_text="tt_bravo01")).to_have_count(0, timeout=10_000)

    # Every write pinned the SELECTED team (rename + toggle + revoke = 3)
    assert len(key_writes) == 3, f"expected 3 key writes, got {key_writes}"
    for w in key_writes:
        assert "team_id=team_b" in w["url"], f"key {w['method']} must pin team_b: {w['url']}"
    assert keys_rows["team_b"] == [], "Bravo's key must be revoked (removed from the team rows)"
    assert keys_rows["team_a"] == [_managed_row("key_a1", "tt_alpha01", "alpha-ci")], \
        "Alpha's rows must be untouched by writes pinned to Bravo"
    assert mint_calls == [], f"zero-mint tripwire: POST /v1/session/key fired: {mint_calls}"
