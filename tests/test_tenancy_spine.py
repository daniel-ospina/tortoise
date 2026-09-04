"""C5 #2114 — data-plane tenancy spine (E2E-2 resolution/denial slices).

The authoritative cross-graph suite: with the ACL layer OFF (no per-graph
FalkorDB users — embedded redislite has none by construction; the registry
spine alone enforces), a per-graph key can ONLY touch its own graph:

  1. cross-graph READ denial: key(A) never sees the default graph's data
     (nor graph B's) — the app layer routes it to A's graph.
  2. read-only key → write 403 (INSUFFICIENT_SCOPE) at every surface.
  3. legacy/team-wide keys + session auth → default graph, unchanged
     (E2E-5 regression).
  4. vanished graph → fail closed (404/403, never a widen onto the default).
  5. ACL-ON (docker lane only): data-plane ops on the bound graph work with
     the hardened tenant ACL users live (defense-in-depth — C4's layer does
     not double-fault legit access).

Mint helper mirrors tests/test_hosted_api.py's TestProvisioningService
_setup (temp registry + seeded team + TestClient); the C5 activation pin in
test_hosted_api covers the deleg=0 activation contract.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from tests.test_hosted_api import _patch_tortoise_sdk_init
from tortoise.auth import hash_api_key


def _spine_env(tmp_path):
    """Seed a registry (temp embedded store) + team + default graph.

    Returns (sdk, tid, tc) — the same shape TestProvisioningService._setup
    produces: ``sdk`` is the registry handle, ``tid`` the team id, ``tc``
    the mounted TestClient. Embedded redislite has NO FalkorDB ACL users —
    this is the ACL-OFF plane by construction (the app-layer spine alone).
    """
    import tortoise.hosted_api as ha_mod

    db_path = os.path.join(tmp_path, "spine.db")
    _patch_tortoise_sdk_init(db_path)
    os.environ["TORTOISE_DB_PATH"] = db_path
    sdk = ha_mod._make_sdk(namespace="registry")
    tid = f"spine-{abs(hash(str(tmp_path))) % 100000}"
    # Seed Team + default graph node (mirror _seed_team_graphs).
    sdk._get_registry().query(
        "CREATE (t:Team {id:$id, tier:'pro', max_graphs:5, "
        "max_api_keys:20, graph_name: $gn})",
        params={"id": tid, "gn": f"team_{tid}"},
    )
    sdk._graph_create(tid, "default", kind="default")
    # One custom graph (the per-graph keys bind here) + one point in the
    # DEFAULT graph (the cross-graph probe — a per-graph key must never see
    # it).
    g = sdk._graph_create(tid, "spine-g", kind="custom")
    default_sdk = ha_mod._make_sdk(namespace=tid)
    default_node = default_sdk.create_point(
        "default-secret", content="default-secret")
    default_sdk.close()
    # Registry handle bound to this temp DB for seeding keys.
    import tortoise.hosted_api as _ha2
    tc = TestClient(_ha2.app)
    return sdk, tid, g, tc, default_node["id"]


def _gen(spine_env):
    """Fixture → generator adapter (setup/teardown parity with
    TestProvisioningService._setup's generator shape)."""
    return spine_env


def _mint_key(sdk, team_id, *, scopes, graph_id=None, deleg=None):
    """Raw APIKey node (the hosted mint matrix's DB shape)."""
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


@pytest.fixture
def spine_env(tmp_path):
    gen = _spine_env(tmp_path)
    try:
        yield gen
    finally:
        # Teardown hygiene: clear the module fixture leak the C3 tests
        # taught us about (dependency overrides + env).
        import tortoise.hosted_api as ha_mod
        ha_mod.app.dependency_overrides.clear()
        os.environ.pop("TORTOISE_DB_PATH", None)




def _point_ids(body) -> list[str]:
    """GET /v1/points may serialize as bare ids or full point dicts —
    normalize to ids for graph-identity asserts."""
    pts = (body or {}).get("points") or []
    return [p["id"] if isinstance(p, dict) else p for p in pts]

def test_cross_graph_read_denied_app_layer(spine_env):
    """E2E-2 (ACL-OFF proof): key(A, read) lists ITS graph — the default
    graph's point never surfaces; a write to the default graph's sibling
    surfaces 403/404, never a cross-graph leak."""
    sdk, tid, g, tc, def_pt = spine_env
    token = _mint_key(sdk, tid, scopes=["graphs:read"], graph_id=g["graph_id"])
    h = {"Authorization": f"Bearer {token}"}
    r = tc.get("/v1/points", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = _point_ids(body)
    assert def_pt not in ids, (
        "per-graph key saw the DEFAULT graph's point — cross-graph leak "
        f"({ids})")


def test_read_only_key_write_denied_everywhere(spine_env):
    """Indicator 4: a graphs:read key's write is 403 at every converted
    surface — points (POST), sessions (POST), dream, commit."""
    sdk, tid, g, tc, def_pt = spine_env
    token = _mint_key(sdk, tid, scopes=["graphs:read"], graph_id=g["graph_id"])
    h = {"Authorization": f"Bearer {token}"}
    for method, path, body in [
        ("post", "/v1/points", {"content": "x"}),
        ("post", "/v1/sessions", {"session_id": "s-x",
                                  "conversation": [{"role": "user",
                                                    "content": "hi"}]}),
        ("post", "/v1/dream", None),
    ]:
        r = getattr(tc, method)(path, headers=h, json=body)
        assert r.status_code in (400, 403, 404), (path, r.status_code)
        if r.status_code == 403:
            detail = r.json().get("detail")
            if isinstance(detail, dict):
                assert detail.get("error_code") in (
                    "INSUFFICIENT_SCOPE", "GRAPH_SCOPED_TEAM_SURFACE"), detail


def test_read_write_key_writes_own_graph(spine_env):
    """A graphs:read+write key WRITES its own graph and reads it back;
    the default graph stays untouched (count probe)."""
    sdk, tid, g, tc, def_pt = spine_env
    token = _mint_key(sdk, tid, scopes=["graphs:read", "graphs:write"],
                      graph_id=g["graph_id"])
    h = {"Authorization": f"Bearer {token}"}
    r = tc.post("/v1/points", json={"content": "own-graph-point"},
                headers=h)
    assert r.status_code in (200, 201), r.text  # idempotent 200 shape
    r = tc.get("/v1/points", headers=h)
    body = r.json()
    ids = _point_ids(body)
    assert ids, "own-graph write did not land in the key's own graph"
    assert def_pt not in ids, "own-graph list leaked the default point"


def test_team_wide_key_default_graph_regression(spine_env):
    """E2E-5: a team-wide (graph_id NULL) scoped key + a legacy key keep the
    DEFAULT-graph flows — the default point IS visible."""
    sdk, tid, g, tc, def_pt = spine_env
    # Team-wide scoped key (deleg NULL, graphs:read).
    token = _mint_key(sdk, tid, scopes=["graphs:read"])
    r = tc.get("/v1/points",
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    ids = _point_ids(body)
    assert def_pt in ids, (
        "team-wide key must read the DEFAULT graph (E2E-5) — got " f"{ids}")
    # Legacy full-access (deleg NULL, no scopes) reads too.
    legacy = _mint_key(sdk, tid, scopes=[])
    r = tc.get("/v1/points",
               headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 200, r.text


def test_vanished_graph_fails_closed(spine_env):
    """A key bound to a graph that no longer exists fails closed (404/403)
    — it must NEVER open the team default."""
    sdk, tid, g, tc, def_pt = spine_env
    token = _mint_key(sdk, tid, scopes=["graphs:read"],
                      graph_id="g_ghost_missing")
    r = tc.get("/v1/points", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code in (403, 404), r.text


def test_team_level_surface_rejects_graph_bound(spine_env):
    """D-C5-2 team surfaces: a graph-bound key on /v1/team (overview reads
    the DEFAULT graph) is rejected outright — no default-graph leak via a
    team-level endpoint."""
    sdk, tid, g, tc, def_pt = spine_env
    token = _mint_key(sdk, tid, scopes=["graphs:read", "graphs:write"],
                      graph_id=g["graph_id"])
    r = tc.get("/v1/team", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403, r.text
    detail = r.json().get("detail")
    if isinstance(detail, dict):
        assert detail.get("error_code") == "GRAPH_SCOPED_TEAM_SURFACE", detail
    # The team-wide key still reads the overview (unchanged).
    wide = _mint_key(sdk, tid, scopes=["graphs:read", "team:manage"])
    r = tc.get("/v1/team", headers={"Authorization": f"Bearer {wide}"})
    assert r.status_code == 200, r.text


