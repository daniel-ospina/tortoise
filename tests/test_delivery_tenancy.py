"""C6 #2115 — delivery-shape tenancy + session_recording per-graph override.

Pins the epic §6.3/§6.4 contract on the AUTHORITATIVE ACL-OFF plane (embedded
redislite has no per-graph FalkorDB users — the app layer alone enforces):

  1. PATCH /v1/graphs/{graph_id} {recording} — set/clear the override on a
     custom + the DEFAULT graph (registry node prop), auth matrix
     (team:manage scoped key OK; legacy OK; graphs:read 403; deleg=0 403;
     session non-owner 403), 404 unknown, 422 bad body.
  2. E2E-6 recording override honored: a recording=false graph stores NO
     Session node (409 + absence); recording=true stores it (mock LLM);
     NULL inherits the team default; the override beats the team flag both
     directions (graph-true vs team-off, graph-off vs team-on).
  3. /v1/context + POST /v1/sessions land in the KEY's graph — no
     cross-graph bleed (E2E-2 sessions/context half); team-wide key keeps
     the default-graph flow (E2E-5 regression).
  4. MCP tortoise_session_capture carries the graph ContextVars — a
     graph-bound MCP key's session lands in ITS graph (C5 residual close).
  5. Supabase seam: set_graph_recording + graph_metadata default-row read
     (unit, FakeControlPlane).
  6. #2302 MCP surface: tortoise_graph_set_recording mirrors the REST PATCH
     (same shared helper + auth gate — team:manage or legacy full access;
     strict bool/null; 'default' + custom gids; 404 unknown) so the
     recording-off 409 can point callers at a REAL surface; the graph-layer
     409 copy names both the PATCH and the MCP tool (REST/MCP same text —
     shared _capture_session_impl).

Mirror helpers from tests/test_tenancy_spine.py (mint matrix + temp-db
patch/restore in ONE scope — cross-file pollution lesson).
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from tests.test_hosted_api import _patch_tortoise_sdk_init
from tortoise.auth import hash_api_key

_CONV = [{"role": "user", "content": "we decided to ship the memory capture"},
         {"role": "assistant", "content": "agree — the recording override"},
         {"role": "user", "content": "ok"}]


def _spine_env(tmp_path):
    """Seed a registry (temp embedded store) + pro team + default graph +
    one custom graph (g, namespace team_{tid}_{gid}) + one point in the
    DEFAULT graph (the cross-graph probe)."""
    import tortoise.hosted_api as ha_mod

    db_path = os.path.join(tmp_path, "spine.db")
    os.environ["TORTOISE_DB_PATH"] = db_path
    sdk = ha_mod._make_sdk(namespace="registry")
    tid = f"deliv-{abs(hash(str(tmp_path))) % 100000}"
    sdk._get_registry().query(
        "CREATE (t:Team {id:$id, tier:'pro', max_graphs:5, "
        "max_api_keys:20, graph_name: $gn})",
        params={"id": tid, "gn": f"team_{tid}"},
    )
    sdk._graph_create(tid, "default", kind="default")
    g = sdk._graph_create(tid, "deliv-g", kind="custom")
    default_sdk = ha_mod._make_sdk(namespace=tid)
    def_pt = default_sdk.create_point(
        "default-secret", content="default-secret")
    default_sdk.close()
    tc = TestClient(ha_mod.app)
    return sdk, tid, g, tc, def_pt["id"]


@pytest.fixture
def spine_env(tmp_path):
    import tortoise.hosted_api as ha_mod
    _orig_init = _patch_tortoise_sdk_init(
        os.path.join(str(tmp_path), "spine.db"))
    try:
        yield _spine_env(tmp_path)
    finally:
        from tests.test_hosted_api import _restore_tortoise_sdk_init
        _restore_tortoise_sdk_init(_orig_init)
        ha_mod.app.dependency_overrides.clear()
        os.environ.pop("TORTOISE_DB_PATH", None)


def _mint_key(sdk, team_id, *, scopes, graph_id=None, deleg=None):
    token = "tk_" + uuid.uuid4().hex
    sdk._get_registry().query(
        "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, "
        "key_prefix:$kp, created_by:'spine', graph_id:$gid, "
        "scopes:$scopes, delegation_depth:$dd})",
        params={"id": f"k-{uuid.uuid4().hex[:8]}", "tid": team_id,
                "kh": hash_api_key(token), "kp": token[:10],
                "gid": graph_id, "scopes": scopes, "dd": deleg},
    )
    return token


def _node_recording(sdk, tid, gid) -> bool | None:
    """Read the registry Graph node's recording prop (None when absent)."""
    rows = sdk._get_registry().query(
        "MATCH (g:Graph {id:$gid, team_id:$tid}) RETURN g.recording",
        params={"gid": gid, "tid": tid},
    ).result_set
    return rows[0][0] if rows else None


def _default_node_id(sdk, tid) -> str:
    rows = sdk._get_registry().query(
        "MATCH (g:Graph {team_id:$tid, kind:'default'}) RETURN g.id",
        params={"tid": tid},
    ).result_set
    return rows[0][0]


def _session_count(graph_name: str, tid: str) -> int:
    """Open the named graph directly and count Session nodes."""
    import tortoise.hosted_api as ha_mod
    sdk = ha_mod._make_sdk(graph_name=graph_name)
    try:
        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (s:Session) RETURN count(s)").result_set
        return int(rows[0][0]) if rows else 0
    finally:
        sdk.close()


# ── 1. PATCH contract (registry lane) ────────────────────────────────────

def test_patch_recording_set_clear_custom_graph(spine_env):
    """PATCH a custom graph's override true/false/null; null removes the
    prop (inherit)."""
    sdk, tid, g, tc, _def_pt = spine_env
    token = _mint_key(sdk, tid, scopes=["team:manage"])
    h = {"Authorization": f"Bearer {token}"}
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"recording": False}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"graph_id": g["graph_id"], "recording": False}
    assert _node_recording(sdk, tid, g["graph_id"]) is False
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"recording": True}, headers=h)
    assert r.status_code == 200, r.text
    assert _node_recording(sdk, tid, g["graph_id"]) is True
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"recording": None}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"graph_id": g["graph_id"], "recording": None}
    assert _node_recording(sdk, tid, g["graph_id"]) is None


def test_patch_recording_default_graph_settable(spine_env):
    """The DEFAULT graph (graph 0) is settable via the literal 'default' —
    the kind='default' node carries the override."""
    sdk, tid, _g, tc, _def_pt = spine_env
    token = _mint_key(sdk, tid, scopes=["team:manage"])
    h = {"Authorization": f"Bearer {token}"}
    r = tc.patch(f"/v1/graphs/default?team_id={tid}",
                 json={"recording": False}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"graph_id": "default", "recording": False}
    assert _node_recording(sdk, tid, _default_node_id(sdk, tid)) is False


def test_patch_recording_auth_matrix(spine_env):
    """graph:read-only key 403; deleg=0 minted key 403 (dependency); legacy
    full-access key OK; missing-scope 403 carries no error_code leak."""
    sdk, tid, g, tc, _def_pt = spine_env
    # graphs:read (no team:manage) → 403
    ro = _mint_key(sdk, tid, scopes=["graphs:read"])
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"recording": True},
                 headers={"Authorization": f"Bearer {ro}"})
    assert r.status_code == 403, r.text
    # deleg=0 minted (even with team:manage — the C2/C3 child policy never
    # stamps it, and get_current_team_session rejects deleg=0) → 403
    minted = _mint_key(sdk, tid, scopes=["team:manage"], deleg=0)
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"recording": True},
                 headers={"Authorization": f"Bearer {minted}"})
    assert r.status_code == 403, r.text
    # Legacy full-access (deleg NULL, scopes []) → 200
    legacy = _mint_key(sdk, tid, scopes=[])
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"recording": True},
                 headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 200, r.text


def test_patch_recording_unknown_graph_404_and_bad_body(spine_env):
    sdk, tid, _g, tc, _def_pt = spine_env
    token = _mint_key(sdk, tid, scopes=["team:manage"])
    h = {"Authorization": f"Bearer {token}"}
    r = tc.patch(f"/v1/graphs/g_doesnotexist?team_id={tid}",
                 json={"recording": True}, headers=h)
    assert r.status_code == 404, r.text
    r = tc.patch(f"/v1/graphs/g_doesnotexist?team_id={tid}",
                 json={}, headers=h)
    assert r.status_code == 422, r.text  # missing required field
    r = tc.patch(f"/v1/graphs/g_doesnotexist?team_id={tid}",
                 json={"recording": "yes"}, headers=h)
    assert r.status_code == 422, r.text  # no truthy string coercion


def test_patch_recording_session_non_owner_403(spine_env):
    """A session user who is NOT owner/admin in the team cannot patch."""
    _sdk, tid, g, tc, _def_pt = spine_env
    import tortoise.hosted_api as ha_mod
    from tests.test_hosted_api import TEST_TEAM
    ha_mod.app.dependency_overrides[ha_mod.get_current_team_session] = \
        lambda: dict(TEST_TEAM, team_id=tid, key_id=None,
                     session_user_id="not-owner", role="member")
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"recording": True})
    assert r.status_code == 403, r.text


# ── 2. E2E-6: recording override honored (Session node presence/absence) ──

def _capture(spine_env, monkeypatch, *, token, session_id, graph_ns):
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    _sdk, _tid, _g, tc, _def_pt = spine_env
    r = tc.post("/v1/sessions", json={
        "session_id": session_id,
        "conversation": _CONV,
    }, headers={"Authorization": f"Bearer {token}"})
    return r


def test_recording_false_graph_stores_no_session(spine_env, monkeypatch):
    """E2E-6: graph override false (team default ON) → 409 + NO Session
    node in that graph."""
    sdk, tid, g, tc, _def_pt = spine_env
    key = _mint_key(sdk, tid, scopes=["graphs:read", "graphs:write"],
                    graph_id=g["graph_id"])
    mgr = _mint_key(sdk, tid, scopes=["team:manage"])
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"recording": False},
                 headers={"Authorization": f"Bearer {mgr}"})
    assert r.status_code == 200, r.text
    r = _capture(spine_env, monkeypatch, token=key, session_id="s-off",
                 graph_ns=g["namespace"])
    assert r.status_code == 409, r.text
    assert _session_count(g["namespace"], tid) == 0


def test_recording_true_graph_stores_session(spine_env, monkeypatch):
    """E2E-6: graph override true → capture 200 + Session node in THAT
    graph (the default graph stays clean)."""
    sdk, tid, g, tc, _def_pt = spine_env
    key = _mint_key(sdk, tid, scopes=["graphs:read", "graphs:write"],
                    graph_id=g["graph_id"])
    mgr = _mint_key(sdk, tid, scopes=["team:manage"])
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"recording": True},
                 headers={"Authorization": f"Bearer {mgr}"})
    assert r.status_code == 200, r.text
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    r = tc.post("/v1/sessions", json={
        "session_id": "s-on", "conversation": _CONV,
    }, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    assert _session_count(g["namespace"], tid) == 1
    # Default graph (team_{tid}) got nothing — cross-graph bleed probe.
    assert _session_count(f"team_{tid}", tid) == 0


def test_recording_null_inherits_team_default(spine_env, monkeypatch):
    """E2E-6/R9: override NULL + team default ON → 200 (default-ON
    preserved); NULL + team OFF → 409."""
    sdk, tid, g, tc, _def_pt = spine_env
    key = _mint_key(sdk, tid, scopes=["graphs:read", "graphs:write"],
                    graph_id=g["graph_id"])
    mgr = _mint_key(sdk, tid, scopes=["team:manage"])
    # Explicit NULL override (inherit) — team state untouched (ON) → 200.
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"recording": None},
                 headers={"Authorization": f"Bearer {mgr}"})
    assert r.status_code == 200, r.text
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    r = tc.post("/v1/sessions", json={
        "session_id": "s-null-on", "conversation": _CONV,
    }, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    # Now flip the TEAM default OFF (dashboard toggle) — NULL override
    # inherits → 409.
    from tortoise.hosted_api import _update_onboarding_state
    _update_onboarding_state(tid, session_recording=False)
    r = tc.post("/v1/sessions", json={
        "session_id": "s-null-off", "conversation": _CONV,
    }, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 409, r.text


def test_team_off_master_kill_beats_graph_override(spine_env, monkeypatch):
    """R9 (round-1 decision c2): the #1927 team OFF is a MASTER KILL — a
    per-graph recording=true override NEVER re-enables a team that opted
    out (opt-out never silently re-enabled). Overrides may only RESTRICT
    when the team is ON."""
    sdk, tid, g, tc, _def_pt = spine_env
    key = _mint_key(sdk, tid, scopes=["graphs:read", "graphs:write"],
                    graph_id=g["graph_id"])
    mgr = _mint_key(sdk, tid, scopes=["team:manage"])
    from tortoise.hosted_api import _update_onboarding_state
    _update_onboarding_state(tid, session_recording=False)
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"recording": True},
                 headers={"Authorization": f"Bearer {mgr}"})
    assert r.status_code == 200, r.text
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    r = tc.post("/v1/sessions", json={
        "session_id": "s-team-off", "conversation": _CONV,
    }, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 409, r.text
    assert _session_count(g["namespace"], tid) == 0


def test_default_graph_override_true_team_off_409(spine_env, monkeypatch):
    """Round-1 P2: the DEFAULT graph's override is the same data plane as
    the dashboard toggle — override true must NOT defeat the team OFF."""
    sdk, tid, _g, tc, _def_pt = spine_env
    mgr = _mint_key(sdk, tid, scopes=["team:manage"])
    from tortoise.hosted_api import _update_onboarding_state
    _update_onboarding_state(tid, session_recording=False)
    r = tc.patch(f"/v1/graphs/default?team_id={tid}",
                 json={"recording": True},
                 headers={"Authorization": f"Bearer {mgr}"})
    assert r.status_code == 200, r.text
    wide = _mint_key(sdk, tid, scopes=["graphs:read", "graphs:write"])
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    r = tc.post("/v1/sessions", json={
        "session_id": "s-default-off", "conversation": _CONV,
    }, headers={"Authorization": f"Bearer {wide}"})
    assert r.status_code == 409, r.text


def test_tombstoned_graph_patch_404_and_capture_403(spine_env, monkeypatch):
    """Round-1 P2: a soft-deleted graph is not patchable (404) and a key on
    it cannot capture (fail closed) — no dead writes on tombstones."""
    sdk, tid, g, tc, _def_pt = spine_env
    # Registry lane tombstone via graph_delete.
    sdk.graph_delete(tid, g["graph_id"])
    mgr = _mint_key(sdk, tid, scopes=["team:manage"])
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"recording": True},
                 headers={"Authorization": f"Bearer {mgr}"})
    assert r.status_code == 404, r.text
    # A graph-bound key on the deleted graph fails closed at the recording
    # gate — never captures into the dead namespace. (Minted after the
    # delete: a genuinely pre-delete key is cascade-revoked and 401s at
    # auth before reaching the gate — the post-delete mint is the reachable
    # regression that exercises the gate's tombstone guard.)
    key = _mint_key(sdk, tid, scopes=["graphs:read", "graphs:write"],
                    graph_id=g["graph_id"])
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    r = tc.post("/v1/sessions", json={
        "session_id": "s-dead-graph", "conversation": _CONV,
    }, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403, r.text
    detail = r.json().get("detail")
    if isinstance(detail, dict):
        assert detail.get("error_code") == "GRAPH_NOT_FOUND", detail


def test_vanished_graph_capture_fails_closed(spine_env, monkeypatch):
    """A graph-bound key whose graph is GONE → 403 GRAPH_NOT_FOUND at the
    recording gate (never demoted to the team default's flag)."""
    sdk, tid, _g, tc, _def_pt = spine_env
    token = _mint_key(sdk, tid, scopes=["graphs:read", "graphs:write"],
                      graph_id="g_ghost_capture")
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    r = tc.post("/v1/sessions", json={
        "session_id": "s-ghost", "conversation": _CONV,
    }, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403, r.text
    detail = r.json().get("detail")
    if isinstance(detail, dict):
        assert detail.get("error_code") == "GRAPH_NOT_FOUND", detail


# ── 3. Context + sessions per-graph tenancy (E2E-2 half) ─────────────────

def test_context_graph_bound_key_scoped_digest(spine_env):
    """E2E-2/6: a graph-bound read key's /v1/context digest excludes the
    default graph's point; the team-wide key sees it (E2E-5)."""
    sdk, tid, g, tc, def_pt = spine_env
    key = _mint_key(sdk, tid, scopes=["graphs:read"], graph_id=g["graph_id"])
    r = tc.get("/v1/context", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    digest = r.json()
    assert def_pt not in str(digest), (
        "graph-bound context leaked the default graph's point")
    wide = _mint_key(sdk, tid, scopes=["graphs:read"])
    r = tc.get("/v1/context", headers={"Authorization": f"Bearer {wide}"})
    assert r.status_code == 200, r.text
    assert def_pt in str(r.json()), (
        "team-wide context must include the default graph (E2E-5)")


def test_sessions_land_in_key_graph(spine_env, monkeypatch):
    """Indicator 2: a graph-bound write key's session points land in ITS
    graph only — the default graph sees no Session."""
    sdk, tid, g, tc, _def_pt = spine_env
    key = _mint_key(sdk, tid, scopes=["graphs:read", "graphs:write"],
                    graph_id=g["graph_id"])
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    r = tc.post("/v1/sessions", json={
        "session_id": "s-own-graph", "conversation": _CONV,
    }, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    assert _session_count(g["namespace"], tid) == 1
    assert _session_count(f"team_{tid}", tid) == 0


# ── 4. MCP capture graph carry (C5 residual close) ───────────────────────

def test_mcp_capture_lands_in_bound_graph(tmp_path, monkeypatch):
    """D-C6-4: with the graph ContextVars set (the HTTP middleware shape),
    tortoise_session_capture files the Session into the BOUND graph — not
    the team default."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    db = str(tmp_path / "mcp.db")
    import tortoise.hosted_api as ha_mod
    _orig_init = _patch_tortoise_sdk_init(db)
    try:
        _run_mcp_graph_capture(tmp_path, db)
    finally:
        from tests.test_hosted_api import _restore_tortoise_sdk_init
        _restore_tortoise_sdk_init(_orig_init)
        ha_mod.app.dependency_overrides.clear()
        os.environ.pop("TORTOISE_DB_PATH", None)


def _run_mcp_graph_capture(tmp_path, db):
    import tortoise.hosted_api as _ha
    reg = _ha._make_sdk(namespace="registry")
    tid = "mcp-deliv"
    reg._get_registry().query(
        "CREATE (t:Team {id:$id, tier:'pro', max_graphs:5, "
        "max_api_keys:20, graph_name: $gn})",
        params={"id": tid, "gn": f"team_{tid}"},
    )
    reg._graph_create(tid, "default", kind="default")
    g = reg._graph_create(tid, "mcp-g", kind="custom")
    from tortoise.mcp_auth import (
        _current_graph_id,
        _current_graph_namespace,
        _current_legacy_full_access,
        _current_scopes,
        _current_team_id,
        _current_team_limits,
    )
    from tortoise.mcp_server import tortoise_session_capture
    toks = [
        _current_team_id.set(tid),
        _current_team_limits.set({"tier": "pro", "max_points": 100000}),
        _current_graph_id.set(g["graph_id"]),
        _current_graph_namespace.set(g["namespace"]),
        _current_scopes.set(["graphs:read", "graphs:write"]),
        _current_legacy_full_access.set(False),
    ]
    _ctx_vars = [_current_team_id, _current_team_limits,
                 _current_graph_id, _current_graph_namespace,
                 _current_scopes, _current_legacy_full_access]
    try:
        res = tortoise_session_capture(
            conversation=_CONV, harness="claude", session_id="s-mcp-g")
    finally:
        for var, tok in zip(_ctx_vars, toks, strict=True):
            var.reset(tok)
    assert not res.get("error"), res
    assert _session_count(g["namespace"], tid) == 1
    assert _session_count(f"team_{tid}", tid) == 0


# ── 5. Supabase seam unit (FakeControlPlane) ─────────────────────────────

def test_supabase_set_graph_recording_custom_and_default():
    """The supabase seam PATCHes custom rows; the default graph upserts a
    kind='default' row; NULL on a missing default row is a no-op (inherit);
    graph_metadata reads the default row."""
    from tests.fake_control_plane import FakeControlPlane
    from tortoise.supabase_control import graph_metadata, set_graph_recording
    cp = FakeControlPlane()
    cp.seed("teams", [{"id": "t1", "graph_name": "team_t1"}])
    cp.seed("graphs", [{
        "id": "g1", "team_id": "t1", "name": "acme", "kind": "custom",
        "namespace": "team_t1_g1", "status": "active", "recording": None,
    }])
    # Custom set → PATCH
    assert set_graph_recording(cp, "t1", "g1", False) is True
    rows = cp.query("graphs", select=["recording"],
                    filters=[("id", "eq", "g1")])
    assert rows[0]["recording"] is False
    # Custom clear → PATCH null
    assert set_graph_recording(cp, "t1", "g1", None) is True
    rows = cp.query("graphs", select=["recording"],
                    filters=[("id", "eq", "g1")])
    assert rows[0]["recording"] is None
    # Unknown custom → False
    assert set_graph_recording(cp, "t1", "g_zzz", True) is False
    # Default graph: NULL with no row → no-op True; set False → upsert
    assert set_graph_recording(cp, "t1", "default", None) is True
    assert set_graph_recording(cp, "t1", "default", False) is True
    rows = cp.query("graphs", select=["recording", "kind"],
                    filters=[("team_id", "eq", "t1"),
                             ("kind", "eq", "default")])
    assert len(rows) == 1 and rows[0]["recording"] is False
    # graph_metadata's derived default carries the override
    meta = graph_metadata(cp, "t1")
    default = next(m for m in meta if m["kind"] == "default")
    assert default["recording"] is False
    # Review P1: the upserted row carries the TEAM graph name as namespace
    # (graphs.namespace is NOT NULL in 20260901000001 — a null namespace
    # would 500 the real PostgREST INSERT; the fake doesn't enforce NOT
    # NULL so assert the payload explicitly).
    drow = cp.query("graphs", select=["namespace"],
                    filters=[("team_id", "eq", "t1"),
                             ("kind", "eq", "default")])
    assert drow and drow[0]["namespace"] == "team_t1", drow
    # Clearing restores inherit (None)
    assert set_graph_recording(cp, "t1", "default", None) is True
    meta = graph_metadata(cp, "t1")
    default = next(m for m in meta if m["kind"] == "default")
    assert default["recording"] is None


# ── 6. #2302: the recording-on MCP surface (mirror of REST PATCH) ─────────

def _run_with_mcp_ctx(tid, fn, *, scopes, legacy=False, graph=None,
                      max_points=100000):
    """Set the tenant-MCP resolution ContextVars around fn() (the shape
    TeamResolutionMiddleware installs per request) and reset after — a
    graph-bound key also carries graph_id/namespace (C5 #2114)."""
    from tortoise.mcp_auth import (
        _current_graph_id,
        _current_graph_namespace,
        _current_legacy_full_access,
        _current_scopes,
        _current_team_id,
        _current_team_limits,
    )
    toks = []

    def _push(var, val):
        toks.append((var, var.set(val)))

    _push(_current_team_id, tid)
    _push(_current_team_limits, {"tier": "pro", "max_points": max_points})
    if graph is not None:
        _push(_current_graph_id, graph["graph_id"])
        _push(_current_graph_namespace, graph["namespace"])
    _push(_current_scopes, scopes)
    _push(_current_legacy_full_access, legacy)
    try:
        return fn()
    finally:
        for var, tok in toks:
            var.reset(tok)


def _mcp_set_recording(recording, graph_id=None):
    from tortoise.mcp_server import tortoise_graph_set_recording
    return tortoise_graph_set_recording(recording=recording, graph_id=graph_id)


def test_mcp_graph_set_recording_sets_clears_and_defaults(spine_env):
    """The MCP tool mirrors REST PATCH semantics (shared write helper):
    true/false set the override, null clears it (inherit), the 'default'
    literal + custom gids resolve, and the write lands on the SAME node
    the REST PATCH leg writes (no surface drift)."""
    sdk, tid, g, tc, _def_pt = spine_env
    # team-wide manager key → explicit custom gid
    r = _run_with_mcp_ctx(
        tid, lambda: _mcp_set_recording(False, g["graph_id"]),
        scopes=["team:manage"])
    assert r == {"graph_id": g["graph_id"], "recording": False}, r
    assert _node_recording(sdk, tid, g["graph_id"]) is False
    # REST PATCH leg (same key class) writes the SAME node — drift check
    mgr = _mint_key(sdk, tid, scopes=["team:manage"])
    rc = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                  json={"recording": True},
                  headers={"Authorization": f"Bearer {mgr}"})
    assert rc.status_code == 200, rc.text
    assert _node_recording(sdk, tid, g["graph_id"]) is True
    # null clears → node prop gone (inherit team default)
    r = _run_with_mcp_ctx(
        tid, lambda: _mcp_set_recording(None, g["graph_id"]),
        scopes=["team:manage"])
    assert r == {"graph_id": g["graph_id"], "recording": None}, r
    assert _node_recording(sdk, tid, g["graph_id"]) is None
    # DEFAULT graph (graph 0) via the 'default' literal — settable like PATCH
    r = _run_with_mcp_ctx(
        tid, lambda: _mcp_set_recording(False, "default"),
        scopes=["team:manage"])
    assert r == {"graph_id": "default", "recording": False}, r
    assert _node_recording(sdk, tid, _default_node_id(sdk, tid)) is False


def test_mcp_graph_set_recording_implicit_bound_graph(spine_env):
    """A graph-bound key's implicit target is ITS OWN graph (the override
    the capture gate reads) — a bare call without graph_id never touches
    the team default graph."""
    sdk, tid, g, _tc, _def_pt = spine_env
    r = _run_with_mcp_ctx(
        tid, lambda: _mcp_set_recording(False),
        scopes=["graphs:write", "team:manage"], graph=g)
    assert r == {"graph_id": g["graph_id"], "recording": False}, r
    assert _node_recording(sdk, tid, g["graph_id"]) is False
    assert _node_recording(sdk, tid, _default_node_id(sdk, tid)) is None


def test_mcp_graph_set_recording_permission_matrix(spine_env):
    """#2302 permission gate mirrors the REST PATCH auth: a data-scoped
    (capture-role) key 403s — per-graph recording is team-management state;
    the legacy full-access class passes; a graph-bound key carrying
    team:manage (owner-minted) manages ANY graph incl. graph 0."""
    sdk, tid, g, _tc, _def_pt = spine_env
    # graph data scopes alone → 403, nothing written
    r = _run_with_mcp_ctx(
        tid, lambda: _mcp_set_recording(True, g["graph_id"]),
        scopes=["graphs:read", "graphs:write"])
    assert r.get("status") == 403, r
    assert "team:manage" in r.get("error", ""), r
    assert _node_recording(sdk, tid, g["graph_id"]) is None
    # legacy full-access class (deleg NULL, scopes []) → allowed
    r = _run_with_mcp_ctx(
        tid, lambda: _mcp_set_recording(True, g["graph_id"]),
        scopes=[], legacy=True)
    assert r == {"graph_id": g["graph_id"], "recording": True}, r
    # graph-bound + team:manage (owner-minted child policy) may manage the
    # DEFAULT graph too — explicit graph_id wins over the bound graph
    r = _run_with_mcp_ctx(
        tid, lambda: _mcp_set_recording(False, "default"),
        scopes=["graphs:write", "team:manage"], graph=g)
    assert r == {"graph_id": "default", "recording": False}, r
    assert _node_recording(sdk, tid, _default_node_id(sdk, tid)) is False


def test_mcp_graph_set_recording_unknown_graph_and_strict_body(spine_env):
    """Unknown gid → 404 (no dead write); a non-bool non-null recording →
    422 — the REST no-truthy-coercion rule survives the MCP boundary."""
    _sdk, tid, _g, _tc, _def_pt = spine_env
    r = _run_with_mcp_ctx(
        tid, lambda: _mcp_set_recording(True, "g_doesnotexist"),
        scopes=["team:manage"])
    assert r.get("status") == 404, r
    r = _run_with_mcp_ctx(
        tid, lambda: _mcp_set_recording("yes"),
        scopes=["team:manage"])
    assert r.get("status") == 422, r
    assert "recording must be true, false or null" in r.get("error", ""), r


def test_mcp_graph_set_recording_requires_hosted_mode():
    """Stdio/selfhost (no tenant team context) → honest hosted-mode error —
    never a silent local write (capture-tool parity)."""
    r = _mcp_set_recording(True, "default")
    assert "hosted mode" in r.get("error", ""), r


def test_capture_409_graph_layer_names_real_surfaces(spine_env, monkeypatch):
    """#2302: the graph-layer recording-off 409 routes callers to surfaces
    that EXIST — the REST PATCH AND the new MCP tool (interpolating the
    concrete graph id). REST and MCP surface the SAME text (shared
    _capture_session_impl — the S11 drift invariant), and neither surface
    writes a Session."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    sdk, tid, g, tc, _def_pt = spine_env
    mgr = _mint_key(sdk, tid, scopes=["team:manage"])
    rc = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                  json={"recording": False},
                  headers={"Authorization": f"Bearer {mgr}"})
    assert rc.status_code == 200, rc.text
    key = _mint_key(sdk, tid, scopes=["graphs:read", "graphs:write"],
                    graph_id=g["graph_id"])
    rest = tc.post("/v1/sessions", json={
        "session_id": "s-2302-copy", "conversation": _CONV,
    }, headers={"Authorization": f"Bearer {key}"})
    assert rest.status_code == 409, rest.text
    rest_detail = rest.json()["detail"]
    assert "PATCH /v1/graphs/" in rest_detail and \
        f"/v1/graphs/{g['graph_id']}" in rest_detail, rest_detail
    assert "tortoise_graph_set_recording" in rest_detail, rest_detail

    def _capture_mcp():
        from tortoise.mcp_server import tortoise_session_capture
        return tortoise_session_capture(conversation=_CONV, harness="pi",
                                        session_id="s-2302-copy-mcp")

    mcp_res = _run_with_mcp_ctx(
        tid, _capture_mcp,
        scopes=["graphs:read", "graphs:write"], graph=g)
    assert mcp_res.get("status") == 409, mcp_res
    assert mcp_res.get("error") == rest_detail, (
        "REST + MCP must surface the SAME graph-layer 409 text (shared "
        f"impl): REST={rest_detail!r} MCP={mcp_res!r}")
    assert _session_count(g["namespace"], tid) == 0


# ── 7. #2701: graph rename (PATCH {name}) — display-name-only ─────────────

def _node_name(sdk, tid, gid) -> str | None:
    """Read a registry Graph node's name prop (None when absent/unknown)."""
    rows = sdk._get_registry().query(
        "MATCH (g:Graph {id:$gid, team_id:$tid}) RETURN g.name",
        params={"gid": gid, "tid": tid},
    ).result_set
    return rows[0][0] if rows else None


def test_patch_rename_custom_graph(spine_env):
    """PATCH {name} renames a custom graph's DISPLAY name (registry node
    prop); the response echoes {graph_id, name}; the list reflects it."""
    sdk, tid, g, tc, _def_pt = spine_env
    token = _mint_key(sdk, tid, scopes=["team:manage"])
    h = {"Authorization": f"Bearer {token}"}
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"name": "  renamed-bot  "}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"graph_id": g["graph_id"], "name": "renamed-bot"}
    assert _node_name(sdk, tid, g["graph_id"]) == "renamed-bot"
    listed = {x["graph_id"]: x for x in sdk.graph_list(tid)}
    assert listed[g["graph_id"]]["name"] == "renamed-bot"
    # The rename is display-only: id/kind/namespace are untouched.
    assert listed[g["graph_id"]]["kind"] == "custom"
    assert listed[g["graph_id"]]["namespace"] == g["namespace"]


def test_patch_rename_default_graph_via_literal(spine_env):
    """The DEFAULT graph (graph 0) is renameable via the literal 'default':
    its kind='default' node's name changes, id/kind/namespace do not."""
    sdk, tid, _g, tc, _def_pt = spine_env
    token = _mint_key(sdk, tid, scopes=["team:manage"])
    h = {"Authorization": f"Bearer {token}"}
    node_id = _default_node_id(sdk, tid)
    ns_before = {x["graph_id"]: x for x in sdk.graph_list(tid)}[node_id]["namespace"]
    r = tc.patch(f"/v1/graphs/default?team_id={tid}",
                 json={"name": "My Memory"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"graph_id": "default", "name": "My Memory"}
    assert _node_name(sdk, tid, node_id) == "My Memory"
    listed = {x["graph_id"]: x for x in sdk.graph_list(tid)}
    assert listed[node_id]["name"] == "My Memory"
    assert listed[node_id]["kind"] == "default"
    # Namespace is the data-plane key — a rename must NEVER move it (the
    # report-10 review: assert the LISTED namespace itself, not a point
    # written through an unrelated namespace).
    assert listed[node_id]["namespace"] == ns_before
    assert ns_before


def test_patch_rename_conflict_409_and_idempotent_same_name(spine_env):
    """A live-name conflict 409s (create parity: tombstones don't squat);
    renaming a graph to its OWN current name is an idempotent 200."""
    sdk, tid, g, tc, _def_pt = spine_env
    g2 = sdk._graph_create(tid, "second-g", kind="custom")
    token = _mint_key(sdk, tid, scopes=["team:manage"])
    h = {"Authorization": f"Bearer {token}"}
    # g → g2's live name → 409
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"name": "second-g"}, headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "Graph name already exists"
    # default → the custom's live name → 409 too (the seam compares across
    # kinds — the default display name is part of the team's name space)
    r = tc.patch(f"/v1/graphs/default?team_id={tid}",
                 json={"name": "second-g"}, headers=h)
    assert r.status_code == 409, r.text
    # Same-name rename of g2 (unchanged) → 200 no-op
    r = tc.patch(f"/v1/graphs/{g2['graph_id']}?team_id={tid}",
                 json={"name": "second-g"}, headers=h)
    assert r.status_code == 200, r.text


def test_patch_rename_auth_matrix(spine_env):
    """graph:read-only key 403; deleg=0 minted key 403; legacy full-access
    key OK (mirror the recording PATCH auth class)."""
    sdk, tid, g, tc, _def_pt = spine_env
    ro = _mint_key(sdk, tid, scopes=["graphs:read"])
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"name": "nope"}, headers={"Authorization": f"Bearer {ro}"})
    assert r.status_code == 403, r.text
    minted = _mint_key(sdk, tid, scopes=["team:manage"], deleg=0)
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"name": "nope"},
                 headers={"Authorization": f"Bearer {minted}"})
    assert r.status_code == 403, r.text
    legacy = _mint_key(sdk, tid, scopes=[])
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"name": "legacy-ok"},
                 headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 200, r.text


def test_patch_rename_unknown_404_and_bad_body_422(spine_env):
    """Unknown graph 404; empty body, empty/whitespace/non-string names and
    truthy-string recording all 422."""
    sdk, tid, _g, tc, _def_pt = spine_env
    token = _mint_key(sdk, tid, scopes=["team:manage"])
    h = {"Authorization": f"Bearer {token}"}
    r = tc.patch(f"/v1/graphs/g_doesnotexist?team_id={tid}",
                 json={"name": "x"}, headers=h)
    assert r.status_code == 404, r.text
    r = tc.patch(f"/v1/graphs/g_doesnotexist?team_id={tid}", json={}, headers=h)
    assert r.status_code == 422, r.text  # at least one field required
    r = tc.patch(f"/v1/graphs/{_g['graph_id']}?team_id={tid}",
                 json={"name": ""}, headers=h)
    assert r.status_code == 422, r.text
    r = tc.patch(f"/v1/graphs/{_g['graph_id']}?team_id={tid}",
                 json={"name": "   "}, headers=h)
    assert r.status_code == 422, r.text
    r = tc.patch(f"/v1/graphs/{_g['graph_id']}?team_id={tid}",
                 json={"name": 42}, headers=h)
    assert r.status_code == 422, r.text
    # An explicitly-null name has no meaning (recording null = clear, name
    # null = nothing to do) — 422, never a silent 200 no-op.
    r = tc.patch(f"/v1/graphs/{_g['graph_id']}?team_id={tid}",
                 json={"name": None}, headers=h)
    assert r.status_code == 422, r.text
    r = tc.patch(f"/v1/graphs/{_g['graph_id']}?team_id={tid}",
                 json={"recording": "yes"}, headers=h)
    assert r.status_code == 422, r.text  # strict-bool preserved


def test_patch_rename_session_owner_ok_member_403(spine_env):
    """Session users: owner/admin may rename; a member cannot (the PATCH
    dual-auth dependency resolves the session face via Membership nodes)."""
    import tortoise.hosted_api as ha_mod
    from tests.test_hosted_api import TEST_TEAM
    sdk, tid, g, tc, _def_pt = spine_env
    # Registry lane: seed Membership nodes so the session face resolves.
    reg = sdk._get_registry()
    for uid, role in (("owner-1", "owner"), ("member-1", "member")):
        reg.query(
            "CREATE (m:Membership {user_id:$u, team_id:$tid,"
            " status:'active', role:$r})",
            params={"u": uid, "tid": tid, "r": role},
        )
    base = dict(TEST_TEAM, team_id=tid, key_id=None)
    ha_mod.app.dependency_overrides[ha_mod.get_current_team_session] = \
        lambda: dict(base, session_user_id="owner-1", role="owner")
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"name": "owner-renamed"})
    assert r.status_code == 200, r.text
    ha_mod.app.dependency_overrides[ha_mod.get_current_team_session] = \
        lambda: dict(base, session_user_id="member-1", role="member")
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"name": "member-nope"})
    assert r.status_code == 403, r.text


def test_patch_rename_and_recording_one_call(spine_env):
    """{name, recording} in ONE PATCH applies both; the response carries
    exactly the changed fields."""
    sdk, tid, g, tc, _def_pt = spine_env
    token = _mint_key(sdk, tid, scopes=["team:manage"])
    h = {"Authorization": f"Bearer {token}"}
    r = tc.patch(f"/v1/graphs/{g['graph_id']}?team_id={tid}",
                 json={"name": "dual", "recording": False}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"graph_id": g["graph_id"], "name": "dual",
                        "recording": False}
    assert _node_name(sdk, tid, g["graph_id"]) == "dual"
    assert _node_recording(sdk, tid, g["graph_id"]) is False


def test_supabase_set_graph_name_custom_and_default():
    """The supabase seam PATCHes custom rows; the default graph renames the
    kind='default' display row or upserts one (namespace = teams.graph_name
    — never the display name); graph_metadata surfaces the renamed name and
    coexists with the recording override."""
    from tests.fake_control_plane import FakeControlPlane
    from tortoise.supabase_control import (
        graph_metadata, set_graph_name, set_graph_recording,
    )
    cp = FakeControlPlane()
    cp.seed("teams", [{"id": "t1", "graph_name": "team_t1"}])
    cp.seed("graphs", [{
        "id": "g1", "team_id": "t1", "name": "acme", "kind": "custom",
        "namespace": "team_t1_g1", "status": "active", "recording": None,
    }])
    # Custom rename → PATCH
    assert set_graph_name(cp, "t1", "g1", "acme-renamed") is True
    rows = cp.query("graphs", select=["name"], filters=[("id", "eq", "g1")])
    assert rows[0]["name"] == "acme-renamed"
    # Unknown custom → False
    assert set_graph_name(cp, "t1", "g_zzz", "x") is False
    # Default graph with NO row → upsert kind='default' display row carrying
    # the new name + the TEAM graph name as namespace.
    assert set_graph_name(cp, "t1", "default", "My Memory") is True
    drow = cp.query("graphs", select=["name", "namespace", "kind"],
                    filters=[("team_id", "eq", "t1"),
                             ("kind", "eq", "default")])
    assert len(drow) == 1, drow
    assert drow[0]["name"] == "My Memory"
    assert drow[0]["namespace"] == "team_t1", drow
    meta = graph_metadata(cp, "t1")
    default = next(m for m in meta if m["kind"] == "default")
    assert default["name"] == "My Memory"
    assert default["namespace"] == "team_t1"
    # Recording override on the SAME display row (set_graph_recording reuse)
    # keeps the renamed row — one kind='default' row, both fields live.
    assert set_graph_recording(cp, "t1", "default", False) is True
    drow = cp.query("graphs", select=["name", "recording"],
                    filters=[("team_id", "eq", "t1"),
                             ("kind", "eq", "default")])
    assert len(drow) == 1
    assert drow[0]["name"] == "My Memory"
    assert drow[0]["recording"] is False
    meta = graph_metadata(cp, "t1")
    default = next(m for m in meta if m["kind"] == "default")
    assert default["name"] == "My Memory"
    assert default["recording"] is False
    # Re-rename of the existing default row → PATCH (row count stays 1)
    assert set_graph_name(cp, "t1", "default", "Renamed Again") is True
    drow = cp.query("graphs", select=["name"],
                    filters=[("team_id", "eq", "t1"),
                             ("kind", "eq", "default")])
    assert len(drow) == 1 and drow[0]["name"] == "Renamed Again"
    # No display row yet → graph_metadata falls back to the literal 'default'
    cp2 = FakeControlPlane()
    cp2.seed("teams", [{"id": "t2", "graph_name": "team_t2"}])
    meta = graph_metadata(cp2, "t2")
    default = next(m for m in meta if m["kind"] == "default")
    assert default["name"] == "default"


# ── 7b. #2701 Supabase-lane REST rename (hosted SOR — prod lane) ───────────
# The registry-lane §7 tests above exercise the embedded store; these cover
# the Supabase branch of the SAME endpoint/_apply_graph_rename core (kind
# resolution incl. the literal-clash 409, the default display-row upsert,
# graph_metadata name surfacing, and the DB-unique race → 409 mapping) —
# Supabase is the hosted production SOR (report-10 review P2: previously
# untested by any executing test). Harness mirrors tests/test_trash_lock.py.

_SB_TEAM = "t-rn-sb"
_SB_OWNER = "9f2c1a40-0000-4a00-8000-000000000270"


def _sb_seed(fake):
    fake.seed("teams", [{
        "id": _SB_TEAM, "graph_name": f"team_{_SB_TEAM}", "tier": "pro",
        "max_graphs": 5, "name": "Org", "status": "active",
    }])
    fake.seed("team_memberships", [{
        "id": "m-rn-1", "team_id": _SB_TEAM, "user_id": _SB_OWNER,
        "role": "owner", "status": "active",
    }])
    fake.seed("graphs", [{
        "id": "g-rn-custom1", "team_id": _SB_TEAM, "name": "acme",
        "kind": "custom", "namespace": f"team_{_SB_TEAM}_g-rn-custom1",
        "status": "active", "recording": None,
    }])


def _sb_env(monkeypatch, fake_cls=None):
    import tempfile as _tempfile
    from tests._http_fixtures import patched_tortoise_sdk
    from tests.fake_control_plane import FakeControlPlane
    from tests.test_export_delete import _enable_supabase
    import tortoise.hosted_api as ha_mod
    fake = (fake_cls or FakeControlPlane)(
        {"teams": [], "api_keys": [], "team_memberships": [],
         "invitations": [], "graphs": []})
    _sb_seed(fake)
    _enable_supabase(monkeypatch, fake)
    tmpdir = _tempfile.mkdtemp()
    patched = patched_tortoise_sdk(os.path.join(tmpdir, "sb.db"))
    patched.__enter__()
    tc = TestClient(ha_mod.app)
    tc.__enter__()
    ha_mod.app.dependency_overrides[ha_mod.get_current_team_session] = \
        lambda: {"team_id": _SB_TEAM, "key_id": None,
                 "session_user_id": _SB_OWNER, "role": "owner"}
    return tc, fake, (patched, ha_mod)


def _sb_teardown(tc, handles):
    from tests.test_export_delete import _close_seed_sdks
    patched, ha_mod = handles
    ha_mod.app.dependency_overrides.clear()
    try:
        tc.__exit__(None, None, None)
    finally:
        patched.__exit__(None, None, None)
        _close_seed_sdks()


def test_patch_rename_supabase_custom_row(monkeypatch):
    tc, fake, handles = _sb_env(monkeypatch)
    try:
        r = tc.patch(f"/v1/graphs/g-rn-custom1?team_id={_SB_TEAM}",
                     json={"name": "acme-renamed"})
        assert r.status_code == 200, r.text
        assert r.json() == {"graph_id": "g-rn-custom1", "name": "acme-renamed"}
        rows = fake.query("graphs", select=["name"],
                          filters=[("id", "eq", "g-rn-custom1")])
        assert rows[0]["name"] == "acme-renamed"
    finally:
        _sb_teardown(tc, handles)


def test_patch_rename_supabase_default_display_row_and_conflicts(monkeypatch):
    tc, fake, handles = _sb_env(monkeypatch)
    try:
        # Rename the derived default graph → a kind='default' display row
        # carrying the new name + the TEAM namespace.
        r = tc.patch(f"/v1/graphs/default?team_id={_SB_TEAM}",
                     json={"name": "My Memory"})
        assert r.status_code == 200, r.text
        drow = fake.query("graphs", select=["name", "namespace", "kind"],
                          filters=[("team_id", "eq", _SB_TEAM),
                                   ("kind", "eq", "default")])
        assert len(drow) == 1, drow
        assert drow[0]["name"] == "My Memory"
        assert drow[0]["namespace"] == f"team_{_SB_TEAM}", drow
        # A live-name conflict 409s (self-exclusion must treat the default
        # display row as graph_id 'default' — renaming it to its OWN name is
        # an idempotent 200, not a self-conflict).
        r = tc.patch(f"/v1/graphs/g-rn-custom1?team_id={_SB_TEAM}",
                     json={"name": "My Memory"})
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == "Graph name already exists"
        r = tc.patch(f"/v1/graphs/default?team_id={_SB_TEAM}",
                     json={"name": "My Memory"})
        assert r.status_code == 200, r.text
        # Unknown graph → 404.
        r = tc.patch(f"/v1/graphs/g_unknown?team_id={_SB_TEAM}",
                     json={"name": "x"})
        assert r.status_code == 404, r.text
    finally:
        _sb_teardown(tc, handles)


def test_patch_rename_supabase_unique_violation_maps_409_not_500(monkeypatch):
    """A cross-worker race past the pre-check surfaces PostgREST's unique
    violation as 409 (create-key parity) — never a raw 500."""
    from tests.fake_control_plane import FakeControlPlane

    class _Flat409(FakeControlPlane):
        def query(self, table, *a, **kw):
            if table == "graphs" and kw.get("method") == "PATCH":
                raise RuntimeError(
                    "Supabase control-plane query failed (graphs): HTTP 409")
            return super().query(table, *a, **kw)

    tc, _fake, handles = _sb_env(monkeypatch, fake_cls=_Flat409)
    try:
        r = tc.patch(f"/v1/graphs/g-rn-custom1?team_id={_SB_TEAM}",
                     json={"name": "raced"})
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == "Graph name already exists"
    finally:
        _sb_teardown(tc, handles)
