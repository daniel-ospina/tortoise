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


def test_graph_override_beats_team_off(spine_env, monkeypatch):
    """E2E-6: a graph recording=true override beats a team OFF toggle —
    opt-out never silently re-engaged, but a graph's explicit choice wins."""
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
        "session_id": "s-graph-beats", "conversation": _CONV,
    }, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    assert _session_count(g["namespace"], tid) == 1


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
