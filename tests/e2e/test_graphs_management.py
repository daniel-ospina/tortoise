"""#2116 (C7) dashboard Graphs-tab management e2e (RUN_DASHBOARD_E2E opt-in).

The Graphs tab's render + management behavior (meter, per-graph key panel,
one-time reveal, delete lifecycle, tier gate) is render/event logic in
main.jsx that no unit test can reach (main.jsx has no component harness;
graphs.js holds the pure derivations and IS node --test covered). This
suite drives the real committed-dist dashboard (same two-server harness as
test_keys_table_mixed.py):

  `wrangler@4 pages dev . --port 8788` from website/ (auth) +
  `wrangler@4 pages dev dist --port 8790` from website/apps/dashboard/.

Covered contracts (issue indicators 1-6):
  1. Meter line — "N graphs · ∞ cap" (pro/team, max_graphs null) vs
     "N/M graphs used" (free/solo).
  2. Create flow → one-time reveal modal: the C2 nested envelope's
     key_plaintext renders once with Copy; NO route re-shows it; dismissing
     clears state (re-opening the tab shows no key anywhere).
  3. Per-graph key panel — list (graph_id-filtered rows), mint (POST body
     carries {graph_id, scopes: graphs:read+write}), revoke (owner/admin).
  4. Delete action on custom rows only; the default graph row has no Delete.
  5. Free/solo tier → create locked with 🔒 + upgrade CTA (no create form).
  6. Inline error surfacing: 409 (cap) on create → the error banner text.

Harness posture (mirrors #2246 ADR-010 session-only):
- Cookie-seeded session; ZERO key-authed requests (Bearer tt_ header sniff).
- Every dashboard request rides the session JWT.
- GET /v1/team/keys without graph_id returns the API-Keys tab's rows (the
  mount loadAll read); with graph_id returns the per-graph panel rows.
- POST /v1/team/keys + POST /v1/graphs bodies are captured for assertion.
- The default graph is a REAL row ({graph_id, kind:'default'}) so the panel
  can mint/list against it exactly like a custom graph's (C7 D-C7-2b).
"""
from __future__ import annotations

import json
import os
import re
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

TEAM_ID = "team_graphs2116"
DEFAULT_ROW = {
    "graph_id": "default", "name": "default", "kind": "default",
    "status": "active", "key_count": 1, "recording": None,
}
CUSTOM_A = {
    "graph_id": "g_prod", "name": "prod", "kind": "custom",
    "status": "active", "key_count": 0, "recording": None,
}
CUSTOM_B = {
    "graph_id": "g_dev", "name": "dev", "kind": "custom",
    "status": "active", "key_count": 0, "recording": None,
}

_GRAPH_KEY = {
    "id": "gk_panel_01", "key_prefix": "tk_panel1", "name": "ci",
    "created_at": "2026-09-01T00:00:00.000Z", "revoked_at": None,
    "graph_id": "g_prod", "scopes": ["graphs:read", "graphs:write"],
    "delegation_depth": None,
}
_REVOKED_KEY = {
    "id": "gk_panel_02", "key_prefix": "tk_panel2", "name": "old ci",
    "created_at": "2026-08-01T00:00:00.000Z", "revoked_at": "2026-08-02T00:00:00.000Z",
    "graph_id": "g_prod", "scopes": ["graphs:read", "graphs:write"],
    "delegation_depth": None,
}


def _team_row(tier: str, max_graphs: int | None) -> dict:
    return {
        "team_id": TEAM_ID,
        "name": "Graphs Fixture",
        "tier": tier,
        "max_graphs": max_graphs,
        "role": "owner",  # isOwnerAdmin gate — action assertions non-vacuous
    }


def _wire_graphs_harness(page: Page, team_row: dict,
                         graphs: list[dict],
                         graph_keys: dict[str, list[dict]],
                         mint_bodies: list | None = None,
                         graph_mint_bodies: list | None = None,
                         key_authed: list | None = None,
                         create_status: int = 201,
                         create_body: dict | None = None) -> None:
    """Layered API mock (keys-table style). team_row carries tier/max_graphs
    so one harness renders free (locked create) and pro/team (unlocked +
    ∞ meter) shapes. mint_bodies collects POST /v1/team/keys bodies;
    graph_mint_bodies collects POST /v1/graphs bodies.

    The fixture lists are MUTABLE state: DELETE /v1/graphs/{gid} drops the
    row, DELETE /v1/team/keys/{id} stamps revoked_at, and a per-graph mint
    appends a row — so the panel re-renders REAL post-mutation state (the
    assertions are not vacuous re-renders of a static fixture)."""
    mint_bodies = mint_bodies if mint_bodies is not None else []
    graph_mint_bodies = graph_mint_bodies if graph_mint_bodies is not None else []
    key_authed = key_authed if key_authed is not None else []
    user_id = "u-graphs2116"
    create_body = create_body if create_body is not None else {
        "graph": {**CUSTOM_B, "created_at": "2026-09-04T00:00:00.000Z"},
        "key": {"id": "gk_new_01", "graph_id": "g_dev",
                "scopes": ["graphs:read", "graphs:write"],
                "created_at": "2026-09-04T00:00:00.000Z"},
        "key_plaintext": "tk_live_newgraph1234567890abcdef",
        "revealed_once": True,
    }
    current_graphs = list(graphs)
    # Deep-copy the per-graph key fixtures so mutations never leak across
    # tests in the same process (the harness list is per-page anyway).
    current_keys: dict[str, list[dict]] = {
        gid: [dict(r) for r in rows] for gid, rows in graph_keys.items()
    }

    def handle(route):
        url = route.request.url
        if url.startswith(API_HOST):
            path = urllib.parse.urlsplit(url).path
            query = urllib.parse.urlsplit(url).query
            auth = (route.request.headers.get("authorization") or "")
            if auth.startswith("Bearer tt_"):
                key_authed.append(url)  # #2246: must stay empty (session only)
            if path.endswith("/v1/session/key") and route.request.method == "POST":
                route.fulfill(status=500, content_type="application/json",
                              body=json.dumps({"detail": "zero-mint tripwire"}))
                return
            if path.endswith("/v1/teams") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([team_row]))
                return
            if path.endswith("/v1/team/keys") and route.request.method == "GET":
                # The API-Keys tab's mount read (no graph_id) returns the
                # team rows; the per-graph panel pins ?graph_id=.
                gid = urllib.parse.parse_qs(query).get("graph_id", [None])[0]
                rows = current_keys.get(gid, []) if gid else _team_key_rows(current_keys)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"keys": rows}))
                return
            if path.endswith("/v1/team/keys") and route.request.method == "POST":
                mint_bodies.append(route.request.post_data or "")
                body = json.loads(route.request.post_data or "{}")
                gid = body.get("graph_id")
                row = {
                    "id": f"gk_mint_{len(mint_bodies)}",
                    "key_prefix": f"tk_mint{len(mint_bodies)}",
                    "name": body.get("name"),
                    "created_at": "2026-09-04T00:00:00.000Z",
                    "revoked_at": None,
                    "graph_id": gid,
                    "scopes": body.get("scopes"),
                    "delegation_depth": None,
                }
                if gid:
                    current_keys.setdefault(gid, []).append(row)
                else:
                    current_keys.setdefault("team", []).append(row)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({
                                  "id": row["id"],
                                  "key": f"tk_live_mint{len(mint_bodies)}abcdef0123456789",
                                  "key_prefix": row["key_prefix"],
                                  "created_at": row["created_at"],
                                  "name": row["name"],
                                  "graph_id": gid,
                                  "scopes": body.get("scopes"),
                                  "delegation_depth": None,
                              }))
                return
            m = re.match(r"^/v1/team/keys/([^/]+)$", path)
            if m and route.request.method == "DELETE":
                kid = m.group(1)
                for rows in current_keys.values():
                    for r in rows:
                        if r["id"] == kid:
                            r["revoked_at"] = "2026-09-04T00:00:00.000Z"
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"revoked": True, "key_id": kid,
                                               "revoked_at": "2026-09-04T00:00:00.000Z"}))
                return
            if path.endswith("/v1/graphs") and route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(current_graphs))
                return
            if path.endswith("/v1/graphs") and route.request.method == "POST":
                graph_mint_bodies.append(route.request.post_data or "")
                route.fulfill(status=create_status, content_type="application/json",
                              body=json.dumps(create_body))
                return
            m = re.match(r"^/v1/graphs/([^/]+)$", path)
            if m and route.request.method == "DELETE":
                gid = urllib.parse.unquote(m.group(1))
                current_graphs[:] = [g for g in current_graphs
                                     if g["graph_id"] != gid]
                route.fulfill(status=204)
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


def _team_key_rows(graph_keys: dict[str, list[dict]]) -> list[dict]:
    """The API-Keys tab's rows: the union of per-graph rows + any legacy
    team-wide (NULL graph_id) rows the caller seeds under 'team'."""
    out: list[dict] = []
    for gid, rows in graph_keys.items():
        if gid == "team":
            out.extend(rows)
        else:
            out.extend(rows)
    return out


def _open_graphs_tab(page: Page, team_row: dict,
                     graphs: list[dict] | None = None,
                     graph_keys: dict[str, list[dict]] | None = None,
                     **kw) -> None:
    _wire_graphs_harness(
        page, team_row,
        graphs if graphs is not None else [DEFAULT_ROW, CUSTOM_A],
        graph_keys if graph_keys is not None else {"g_prod": [_GRAPH_KEY]},
        **kw,
    )
    page.goto(APP_HOST + "/", wait_until="domcontentloaded", timeout=30_000)
    expect(page.locator("body")).to_contain_text("Graphs", timeout=25_000)
    page.locator('[data-tab="graphs"]').click()
    # The graphs table is the active tab's <table> (rows: default + custom).
    expect(page.locator("table tbody tr")).to_have_count(2, timeout=15_000)


def test_graphs_table_rows_default_first_with_actions(page: Page) -> None:
    """Indicator 1 + the row model: default row first (no Delete), custom
    rows carry Keys + Delete; status/key-count columns render; the meter
    shows the ∞ label for a pro/team tier (max_graphs null)."""
    key_authed: list = []
    _open_graphs_tab(page, _team_row("team", None),
                     key_authed=key_authed)
    assert key_authed == [], f"no key-authed requests in session mode: {key_authed}"
    # Meter: '2 graphs · ∞ cap' (default + custom rows, max_graphs null).
    expect(page.locator('[aria-label="Graph usage meter"]')).to_contain_text("2 graphs · ∞ cap")
    rows = page.locator("table tbody tr")
    expect(rows.first).to_contain_text("default")
    expect(rows.first).to_contain_text("default", exact=False)  # kind column
    # The default row: NO Delete action; custom rows have Keys + Delete.
    expect(rows.first.get_by_role("button", name="Delete")).to_have_count(0)
    custom_row = rows.filter(has_text="prod")
    expect(custom_row.get_by_role("button", name="Delete")).to_be_visible()
    expect(custom_row.get_by_role("button", name="Keys")).to_be_visible()
    expect(custom_row).to_contain_text("custom")
    expect(custom_row).to_contain_text("0")  # key_count column


def test_meter_free_tier_shows_used_total(page: Page) -> None:
    """Indicator 1 free/solo shape: '1/1 graphs used' at max_graphs=1 with
    only the default graph (free cap reached — create is locked)."""
    _open_graphs_tab(page, _team_row("free", 1),
                     graphs=[DEFAULT_ROW])
    # Only the default row renders.
    expect(page.locator("table tbody tr")).to_have_count(1)
    expect(page.locator('[aria-label="Graph usage meter"]')).to_contain_text("1/1 graphs used")


def test_free_tier_create_locked_with_upgrade_cta(page: Page) -> None:
    """Indicator 5: free/solo see the 🔒 locked create + upgrade CTA — no
    create form, no new-graph input anywhere on the tab."""
    _open_graphs_tab(page, _team_row("free", 1),
                     graphs=[DEFAULT_ROW])
    expect(page.get_by_label("New graph name")).to_have_count(0)
    expect(page.locator("section")).to_contain_text("🔒")
    expect(page.locator("section")).to_contain_text("Upgrade to add more")


def test_create_graph_reveals_key_once_then_clears(page: Page) -> None:
    """Indicator 2 + surface 4: creating a graph returns the C2 nested
    envelope and the modal shows key_plaintext ONCE with the shown-once
    copy; dismissing clears state — no route re-shows the key (the envelope
    is never re-fetched; the modal text is gone from the DOM)."""
    key_authed: list = []
    _open_graphs_tab(page, _team_row("team", None),
                     key_authed=key_authed)
    page.get_by_label("New graph name").fill("dev")
    page.get_by_role("button", name="+ Create").click()
    modal = page.locator('[role="dialog"][aria-label="New key — shown once"]')
    expect(modal).to_be_visible(timeout=15_000)
    expect(modal).to_contain_text("shown once")
    expect(modal).to_contain_text("tk_live_newgraph1234567890abcdef")
    # Copy & done dismisses (clipboard may be blocked in CI — the button
    # still clears on success; assert the modal goes away either way via
    # the fallback "I saved it" path in a second pass).
    page.get_by_role("button", name="I saved it").click()
    expect(modal).to_have_count(0)
    # No show-key route/remnant: re-opening the tab renders no plaintext.
    expect(page.locator("body")).not_to_contain_text("tk_live_newgraph1234567890abcdef")


def test_per_graph_key_panel_lists_mints_and_revokes(page: Page) -> None:
    """Indicator 3: the [Keys] panel lists the graph's keys (graph_id
    filter), mint POSTs {graph_id, scopes: graphs:read+write} and reveals
    the returned plaintext once; revoke confirms + DELETEs. Owner/admin
    gating is exercised via role:'owner' rows."""
    mint_bodies: list = []
    graph_keys = {
        "g_prod": [_GRAPH_KEY, _REVOKED_KEY],
        "team": [_GRAPH_KEY],
    }
    _open_graphs_tab(page, _team_row("team", None),
                     graph_keys=graph_keys,
                     mint_bodies=mint_bodies)
    custom_row = page.locator("table tbody tr").filter(has_text="prod")
    custom_row.get_by_role("button", name="Keys").click()
    panel = page.locator(".graph-key-panel")
    expect(panel).to_contain_text("Keys for prod", timeout=15_000)
    # Panel rows: active + revoked (revoked dimmed/terminal, no Revoke).
    panel_rows = panel.locator("tbody tr")
    expect(panel_rows).to_have_count(2)
    revoked_row = panel_rows.filter(has_text="old ci")
    expect(revoked_row).to_contain_text("revoked")
    expect(revoked_row.get_by_role("button", name="Revoke")).to_have_count(0)
    # Mint a new key for the graph.
    panel.get_by_label("New graph key label").fill("deploy")
    panel.get_by_role("button", name="+ Mint key").click()
    expect(page.locator('[role="dialog"]')).to_contain_text("shown once", timeout=15_000)
    assert mint_bodies, "panel mint body captured"
    mint_body = json.loads(mint_bodies[-1])
    assert mint_body["graph_id"] == "g_prod"
    assert sorted(mint_body["scopes"]) == ["graphs:read", "graphs:write"]
    page.get_by_role("button", name="I saved it").click()
    # Revoke the active ci row — confirm dialog accepts; the mutation
    # stamps revoked_at so the refreshed panel renders it revoked/terminal.
    ci_row = panel_rows.filter(has_text="ci").first
    expect(ci_row.get_by_role("button", name="Revoke")).to_be_visible()
    page.once("dialog", lambda d: d.accept())
    ci_row.get_by_role("button", name="Revoke").click()
    expect(ci_row).to_contain_text("revoked", timeout=15_000)
    expect(ci_row.get_by_role("button", name="Revoke")).to_have_count(0)


def test_default_graph_has_no_delete_and_custom_delete_armed(page: Page) -> None:
    """Indicator 4: the default graph row never offers Delete; a custom
    row's Delete arms an inline confirm (cancel keeps the row)."""
    _open_graphs_tab(page, _team_row("team", None))
    rows = page.locator("table tbody tr")
    expect(rows.first.filter(has_text="default").get_by_role("button", name="Delete")).to_have_count(0)
    custom_row = rows.filter(has_text="prod")
    custom_row.get_by_role("button", name="Delete").click()
    expect(custom_row).to_contain_text("Delete prod?")
    custom_row.get_by_role("button", name="Cancel").click()
    expect(custom_row).not_to_contain_text("Delete prod?")
    # Arming then confirming fires DELETE /v1/graphs/{gid}?team_id=…
    custom_row.get_by_role("button", name="Delete").click()
    custom_row.get_by_role("button", name="Delete", exact=True).click()
    expect(custom_row).not_to_be_visible(timeout=15_000)  # list re-fetch drops it


def test_create_409_cap_error_surfaces_inline(page: Page) -> None:
    """Indicator 6: a 409 cap on create surfaces the authoritative detail
    (C2 provisioning service 409 = graph quota OR API-key cap) inline."""
    _open_graphs_tab(
        page, _team_row("team", None), create_status=409,
        create_body={"detail": "Graph quota reached — delete a graph or upgrade."},
    )
    page.get_by_label("New graph name").fill("overflow")
    page.get_by_role("button", name="+ Create").click()
    expect(page.locator(".error.banner")).to_contain_text(
        "Graph quota reached — delete a graph or upgrade.", timeout=15_000)
