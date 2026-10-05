"""#2001 (W5) graph-held OnboardingState — docker-lane integration tests.

TestNodeInit (T2): every provision path initializes the OnboardingState node
(eager, same statement as TeamMeta, or the post-RPC hook / write-time
create-on-write seam); concurrent inits converge to one node; export/restore
preserves fork + completed_steps; OnboardingState/OnboardingStep are NEVER
added to _EXPORT_SKIP_LABELS (backup-safe, scope pin 17).

TestWriter: FLOW keys routed through the router never round-trip into the
jsonb store (registration-split negative); operational keys still do; an
unknown key is dropped fail-closed.

Runs in the docker lane (TORTOISE_DB_URI) — the graph writes land on the
real FalkorDB test matrix graph. URI-less runs (tier-2 embedded legs,
carve-out) SKIP at module level: these assertions exercise hosted
registry lanes whose eager-init Cypher + keyed-MERGE writers are
server-mode graph semantics (embedded redislite cannot satisfy them —
#1997 tier-2 regression).
"""
from __future__ import annotations

import os
import threading

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")

import pytest

# docker-lane gate (epic #1647 P4 / #1997): URI-less embedded legs cannot
# run these server-mode graph assertions — skip cleanly instead of failing
# (the full-matrix docker half + local docker runs still exercise them).
from tortoise.config import is_db_uri as _is_db_uri

if not _is_db_uri(os.environ.get("TORTOISE_DB_URI")):
    pytest.skip("docker-lane onboarding state tests require TORTOISE_DB_URI "
                "(tier-2 embedded legs skip)", allow_module_level=True)

from fastapi.testclient import TestClient

from tortoise.hosted_api import _make_sdk, app
from tortoise.onboarding import state as onboarding_state
from tortoise.sdk import TortoiseSDK


def _read_node(org_id: str):
    return onboarding_state.read_onboarding_node(
        _make_sdk(namespace=org_id)._get_proj(), org_id)


def _completed(org_id: str):
    return onboarding_state.completed_steps(
        _make_sdk(namespace=org_id)._get_proj(), org_id)


@pytest.fixture
def client():
    """Registry-mode TestClient on the docker lane (env URI). No override —
    register/_create lanes exercise their real graph writes."""
    with TestClient(app) as tc:
        yield tc


def _registered_client():
    """TestClient + a freshly registered team's key (registry lane)."""
    import uuid
    tc = TestClient(app)
    tc.__enter__()
    email = f"w5t4-{uuid.uuid4().hex[:10]}@example.com"
    r = tc.post("/v1/register", json={"email": email,
                                      "password": "password123"})
    assert r.status_code == 200, r.text
    tc.headers.update({"Authorization": f"Bearer {r.json()['api_key']}"})
    return tc, r.json()["org_id"]


class TestNodeInit:
    def test_register_creates_node_with_team_named_edge(self, client):
        """Register (registry lane) → OnboardingState node initialized in the
        SAME statement as TeamMeta with the deterministic write set."""
        import uuid
        email = f"w5-{uuid.uuid4().hex[:10]}@example.com"
        r = client.post("/v1/register", json={"email": email,
                                              "password": "password123"})
        assert r.status_code == 200, r.text
        org_id = r.json()["org_id"]
        node = _read_node(org_id)
        assert node is not None
        assert node["org_id"] == org_id
        assert node["status"] == "active"
        assert node["version"] == 1
        assert node["compact"] is False
        assert "fork" not in node or node["fork"] is None  # first org → card asked once
        assert "team-named" in _completed(org_id)

    def test_sdk_team_create_inits_node(self):
        """SDK lane (CI-visible): sdk.team_create → node exists in org_{name}."""
        import uuid
        name = f"w5sdk{uuid.uuid4().hex[:8]}"
        sdk = TortoiseSDK(namespace="registry")
        team = sdk.org_create(name)
        try:
            node = onboarding_state.read_onboarding_node(
                sdk._get_proj().db.select_graph(f"org_{name}"), team["id"])
            assert node is not None
            assert node["status"] == "active"
            assert "team-named" in onboarding_state.completed_steps(
                sdk._get_proj().db.select_graph(f"org_{name}"), team["id"])
        finally:
            sdk.close()

    def test_second_team_compact_and_fork_inheritance(self):
        """Second org for the same creator → compact=True (prior memberships)
        and fork inherited from the earliest prior org ('self' fallback when
        the prior org has no fork)."""
        import uuid
        sdk = TortoiseSDK(namespace="registry")
        try:
            first = sdk.org_create(f"w5a{uuid.uuid4().hex[:8]}")
            user_id = f"user-{uuid.uuid4().hex[:8]}"
            sdk.membership_create(first["id"], user_id, "owner")
            second = sdk.org_create(f"w5b{uuid.uuid4().hex[:8]}",
                                     owner_user_id=user_id)
            node = onboarding_state.read_onboarding_node(
                sdk._get_proj().db.select_graph(second["graph_name"]),
                second["id"])
            assert node is not None
            assert node["compact"] is True
            assert node.get("fork") == "self"  # first org fork None → fallback
        finally:
            sdk.close()

    def test_fork_inherited_from_prior_org(self):
        """A build-first org inherits 'build' (never re-asks the fork card)."""
        import uuid
        sdk = TortoiseSDK(namespace="registry")
        try:
            first = sdk.org_create(f"w5c{uuid.uuid4().hex[:8]}")
            user_id = f"user-{uuid.uuid4().hex[:8]}"
            sdk.membership_create(first["id"], user_id, "owner")
            # creator answers the fork card on the FIRST org → 'build'
            onboarding_state.write_fork(
                sdk._get_proj().db.select_graph(first["graph_name"]),
                first["id"], "build")
            second = sdk.org_create(f"w5d{uuid.uuid4().hex[:8]}",
                                     owner_user_id=user_id)
            node = onboarding_state.read_onboarding_node(
                sdk._get_proj().db.select_graph(second["graph_name"]),
                second["id"])
            assert node is not None
            assert node.get("fork") == "build"
        finally:
            sdk.close()

    def test_provision_tenant_inits_node(self, client, monkeypatch):
        """/internal/provision (selfhost lane) → node initialized."""
        import uuid
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", "test-internal-key")
        org_id = f"tp{uuid.uuid4().hex[:12]}"
        r = client.post("/internal/provision",
                        headers={"Authorization": "Bearer test-internal-key"},
                        json={"org_id": org_id, "org_name": f"TP {org_id[:6]}",
                              "api_key_hash": "x" * 64, "created_by": "tp-user"})
        assert r.status_code == 200, r.text
        node = _read_node(org_id)
        assert node is not None
        assert node["status"] == "active"
        assert "team-named" in _completed(org_id)

    def test_concurrent_init_single_node(self):
        """Concurrent keyed-MERGE inits on the SAME org → exactly one node
        and one edge (per-graph write serialization + idempotent MERGE)."""
        import uuid

        from tortoise.hosted_api import _make_sdk as _ms
        org_id = f"cc{uuid.uuid4().hex[:12]}"
        sdk = _ms(namespace=org_id)
        proj = sdk._get_proj()
        errs: list[Exception] = []

        def _worker():
            try:
                onboarding_state.ensure_onboarding_state_node(proj, org_id)
                onboarding_state.write_completed_step(proj, org_id,
                                                      "harness-connected")
            except Exception as exc:  # pragma: no cover
                errs.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errs
        node = onboarding_state.read_onboarding_node(proj, org_id)
        assert node is not None
        steps = onboarding_state.completed_steps(proj, org_id)
        assert steps.count("harness-connected") == 1

    def test_create_on_write_seam(self):
        """An absent-node org self-heals on the FIRST FLOW write — the write
        path creates the node (never the read path)."""
        import uuid
        org_id = f"cow{uuid.uuid4().hex[:12]}"
        proj = _make_sdk(namespace=org_id)._get_proj()
        assert onboarding_state.read_onboarding_node(proj, org_id) is None
        res = onboarding_state.write_completed_step(proj, org_id,
                                                    "harness-connected")
        assert res["created"] is True
        node = onboarding_state.read_onboarding_node(proj, org_id)
        assert node is not None
        assert node["status"] == "active"
        assert "harness-connected" in onboarding_state.completed_steps(
            proj, org_id)
        # set-once fork on the same node
        outcome = onboarding_state.write_fork(proj, org_id, "self")
        assert outcome == "set"

    def test_mirror_status_from_jsonb_one_directional(self):
        """create-on-write mirrors jsonb onboarding_complete → 'complete'
        (one-directional; never jsonb-false → complete; never clobbers)."""
        import uuid
        org_id = f"mir{uuid.uuid4().hex[:12]}"
        proj = _make_sdk(namespace=org_id)._get_proj()
        onboarding_state.ensure_onboarding_state_node(
            proj, org_id, status_from_mirror=True)
        node = onboarding_state.read_onboarding_node(proj, org_id)
        assert node["status"] == "complete"
        # mirror=False on an existing complete node never clobbers
        onboarding_state.ensure_onboarding_state_node(
            proj, org_id, status_from_mirror=False)
        assert onboarding_state.read_onboarding_node(proj, org_id)["status"] == "complete"

    def test_status_monotonic(self):
        import uuid
        org_id = f"mon{uuid.uuid4().hex[:12]}"
        proj = _make_sdk(namespace=org_id)._get_proj()
        onboarding_state.ensure_onboarding_state_node(proj, org_id)
        onboarding_state.write_status(proj, org_id, "complete")
        # complete can never regress to active
        onboarding_state.write_status(proj, org_id, "active")
        assert onboarding_state.read_onboarding_node(proj, org_id)["status"] == "complete"

    def test_backup_roundtrip_preserves_flow(self):
        """Export → restore round-trip: fork + completed_steps survive; the
        node + step labels are exported by default (NOT in _EXPORT_SKIP_LABELS)."""
        import uuid

        from tortoise import hosted_backup
        from tortoise.hosted_api import _is_export_skip_node
        # the labels must not be skip-labelled (backup-safe pin)
        assert _is_export_skip_node(["OnboardingState"], {}) is False
        assert _is_export_skip_node(["OnboardingStep"], {}) is False
        org_id = f"bk{uuid.uuid4().hex[:12]}"
        sdk = _make_sdk(namespace=org_id)
        proj = sdk._get_proj()
        g = proj.db.select_graph(f"org_{org_id}")
        onboarding_state.ensure_onboarding_state_node(g, org_id)
        onboarding_state.write_fork(g, org_id, "build")
        onboarding_state.write_completed_step(g, org_id, "harness-connected")
        onboarding_state.write_completed_step(g, org_id, "first-points-filed")
        dump = hosted_backup.dump_graph(g, graph_name=f"org_{org_id}")
        node_labels = {tuple(n["labels"]) for n in dump["nodes"]}
        assert ("OnboardingState",) in node_labels
        assert ("OnboardingStep",) in node_labels
        # wipe + restore
        g.query("MATCH (n) DETACH DELETE n")
        hosted_backup.restore_graph(g, dump)
        node = onboarding_state.read_onboarding_node(g, org_id)
        assert node is not None
        assert node.get("fork") == "build"
        steps = set(onboarding_state.completed_steps(g, org_id))
        assert {"harness-connected", "first-points-filed"} <= steps


class TestWriter:
    def test_flow_keys_never_in_jsonb(self):
        """FLOW keys routed through the router never round-trip into jsonb
        (registration-split negative — the jsonb store has no FLOW keys even
        after FLOW writes)."""
        from tortoise.hosted_api import _get_onboarding_state as _raw
        tc, org_id = _registered_client()
        try:
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "harness-connected"})
            tc.post("/v1/onboarding/state/checkpoint", json={"fork": "self"})
            raw = _raw(org_id)
            for k in onboarding_state.FLOW_KEYS:
                assert k not in raw, f"FLOW key {k} leaked into jsonb"
            for step in onboarding_state.STEP_IDS:
                assert step not in raw
        finally:
            tc.__exit__(None, None, None)

    def test_operational_keys_still_round_trip(self):
        tc, _org_id = _registered_client()
        try:
            r = tc.patch("/v1/onboarding/state",
                         json={"prompt_pasted": True})
            assert r.status_code == 200
            assert r.json()["onboarding"]["prompt_pasted"] is True
        finally:
            tc.__exit__(None, None, None)

    def test_unknown_key_dropped_fail_closed(self):
        tc, _org_id = _registered_client()
        try:
            r = tc.patch("/v1/onboarding/state", json={"bogus_key": 1})
            assert r.status_code == 200  # unknown → dropped, never default-to-FLOW
            assert "bogus_key" not in r.json()["onboarding"]
        finally:
            tc.__exit__(None, None, None)

    def test_write_strips_flow_defensively(self):
        from tortoise.hosted_api import _get_onboarding_state as _raw
        from tortoise.hosted_api import _write_onboarding_state as _w
        tc, org_id = _registered_client()
        try:
            state = dict(_raw(org_id))
            state["fork"] = "self"
            state["harness-connected"] = True
            _w(org_id, state)
            after = _raw(org_id)
            assert "fork" not in after
            assert "harness-connected" not in after
        finally:
            tc.__exit__(None, None, None)
