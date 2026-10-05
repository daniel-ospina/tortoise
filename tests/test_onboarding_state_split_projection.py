"""#2001 (W5) graph-held OnboardingState — docker-lane integration tests.

TestProjection: the merged GET (FLOW + operational), graph-down degraded
read, graph-down checkpoint 503, and the orphan-read no-write contract.

TestJourneyLeg: the DE2E-1 docker-lane journey leg (register → node version=1
shape + onboards edge → Organization Subject).

TestOnboardsEdge (#1999 W3): the DM-1 node↔anchor link — org_subject_id
property + onboards edge written by the seed; idempotent on replay.

TestPostProvisionHook: _ensure_onboarding_node_after_provision creates the
node with compact derived from the caller's PRIOR memberships.

Runs in the docker lane (TORTOISE_DB_URI) — the graph writes land on the
real FalkorDB test matrix graph. URI-less runs (tier-2 embedded legs,
carve-out) SKIP at module level: these assertions exercise hosted
registry lanes whose eager-init Cypher + keyed-MERGE writers are
server-mode graph semantics (embedded redislite cannot satisfy them —
#1997 tier-2 regression).
"""
from __future__ import annotations

import os

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


def _read_node(org_id: str):
    return onboarding_state.read_onboarding_node(
        _make_sdk(namespace=org_id)._get_proj(), org_id)


def _completed(org_id: str):
    return onboarding_state.completed_steps(
        _make_sdk(namespace=org_id)._get_proj(), org_id)


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


class TestProjection:
    def test_merged_get_serves_flow_and_operational(self):
        tc, _org_id = _registered_client()
        try:
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "harness-connected"})
            tc.patch("/v1/onboarding/state", json={"demo_created": True})
            st = tc.get("/v1/onboarding/state").json()["onboarding"]
            assert "harness-connected" in st["completed_steps"]
            assert st["demo_created"] is True
            assert st["version"] == 1
            assert "fork" in st and "compact" in st
        finally:
            tc.__exit__(None, None, None)

    def test_graph_down_read_degraded(self, monkeypatch):
        """Graph-down merged GET → 200 with FLOW 'unavailable' markers
        (never fabricated defaults); operational keys still served."""
        import tortoise.hosted_api as _ha
        tc, _org_id = _registered_client()
        try:
            def _boom(*a, **k):
                raise RuntimeError("graph down")
            monkeypatch.setattr(_ha._os, "read_onboarding_node", _boom)
            r = tc.get("/v1/onboarding/state")
            assert r.status_code == 200
            st = r.json()["onboarding"]
            assert st["status"] == "unavailable"
            assert st["fork"] == "unavailable"
            assert "github_connected" in st  # operational keys intact
        finally:
            tc.__exit__(None, None, None)

    def test_graph_down_checkpoint_503(self, monkeypatch):
        """Checkpoint with the graph down → 503 BEFORE any write (fail-loud,
        retry-safe)."""
        import tortoise.hosted_api as _ha
        tc, _org_id = _registered_client()
        try:
            def _boom(*a, **k):
                raise RuntimeError("graph down")
            monkeypatch.setattr(_ha, "_graph_available", lambda t: False)
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "harness-connected"})
            assert r.status_code == 503
        finally:
            tc.__exit__(None, None, None)

    def test_orphan_read_no_write(self):
        """Graph up, node absent (orphan/grandfathered) → FLOW defaults
        served read-only — the read path never materializes a node."""
        import uuid

        from tortoise.hosted_api import _get_onboarding_projection as _proj
        org_id = f"orphan{uuid.uuid4().hex[:8]}"
        # ensure the team graph exists (register would) but NO node
        _make_sdk(namespace=org_id)._get_proj().db.list_graphs()
        st = _proj(org_id)
        assert st["fork"] is None
        assert st["status"] == "active"
        assert st["completed_steps"] == []
        # no node materialized by the read
        assert onboarding_state.read_onboarding_node(
            _make_sdk(namespace=org_id)._get_proj(), org_id) is None


class TestJourneyLeg:
    def test_de2e1_node_shape_and_onboards_edge(self):
        """DE2E-1 (docker-lane leg): register → node version=1 with the
        deterministic write set + team-named edge; the onboards edge →
        Organization Subject is assertable from W5's read surface (W3's
        seed-write contract is #1999's test)."""
        import uuid
        tc, org_id = _registered_client()
        try:
            node = _read_node(org_id)
            assert node is not None
            assert node["version"] == 1
            assert node["status"] == "active"
            steps = set(_completed(org_id))
            assert "team-named" in steps
            proj = _make_sdk(namespace=org_id)._get_proj()
            # W3-style seed write (the org Subject + onboards edge) — W5
            # asserts the read surface accepts it
            subject_oid = f"org-{uuid.uuid4().hex[:8]}"
            proj.query(
                "MATCH (n:OnboardingState {org_id: $oid}) "
                "MERGE (s:Subject {subjectKind: 'organization', org_id: $oid, "
                "name: $name}) "
                "MERGE (n)-[:onboards]->(s)",
                oid=org_id, name=subject_oid)
            res = proj.query(
                "MATCH (n:OnboardingState {org_id: $oid})-[:onboards]->"
                "(s:Subject {subjectKind: 'organization'}) RETURN s.org_id",
                oid=org_id)
            assert res.result_set and res.result_set[0][0] == org_id
        finally:
            tc.__exit__(None, None, None)


class TestOnboardsEdge:
    """#1999 (W3): the DM-1 node↔anchor link — org_subject_id property +
    onboards edge → Organization Subject, written by the seed (the Subject
    itself is created by the seed core first; this writer links).
    Idempotent: replay does not duplicate the edge (created-signal False)."""

    def _make_org_subject(self, org_id: str) -> str:
        import uuid
        sdk = _make_sdk(namespace=org_id)
        try:
            node = sdk.create_subject(
                f"org{uuid.uuid4().hex[:8]}", subjectKind="organization",
                org_id=org_id)
            return node["id"]
        finally:
            sdk.close()

    def test_write_sets_org_subject_id_and_edge(self):
        tc, org_id = _registered_client()
        try:
            proj = _make_sdk(namespace=org_id)._get_proj()
            sid = self._make_org_subject(org_id)
            res = onboarding_state.write_onboards_edge(proj, org_id, sid)
            assert res["created"] is True
            node = _read_node(org_id)
            assert node.get("org_subject_id") == sid
            r = proj.query(
                "MATCH (n:OnboardingState {org_id: $oid})-[:onboards]->"
                "(s:Subject) RETURN s.id, n.org_subject_id", oid=org_id)
            rows = r.result_set
            assert rows and rows[0][0] == rows[0][1] == sid
        finally:
            tc.__exit__(None, None, None)

    def test_replay_noop_same_subject(self):
        tc, org_id = _registered_client()
        try:
            proj = _make_sdk(namespace=org_id)._get_proj()
            sid = self._make_org_subject(org_id)
            r1 = onboarding_state.write_onboards_edge(proj, org_id, sid)
            r2 = onboarding_state.write_onboards_edge(proj, org_id, sid)
            assert r1["created"] is True
            assert r2["created"] is False
            assert r2["subject_id"] == sid
            r = proj.query(
                "MATCH (n:OnboardingState {org_id: $oid})-[:onboards]->"
                "(s:Subject) RETURN count(s)", oid=org_id)
            assert r.result_set[0][0] == 1
        finally:
            tc.__exit__(None, None, None)

    def test_subject_kind_is_never_object_statement(self):
        """B1 regression: the anchor linked by write_onboards_edge is a
        Subject — never Object/Statement."""
        tc, org_id = _registered_client()
        try:
            proj = _make_sdk(namespace=org_id)._get_proj()
            sid = self._make_org_subject(org_id)
            onboarding_state.write_onboards_edge(proj, org_id, sid)
            r = proj.query(
                "MATCH (n:OnboardingState {org_id: $oid})-[:onboards]->(s) "
                "RETURN labels(s)", oid=org_id)
            assert r.result_set
            labels = r.result_set[0][0]
            assert "Subject" in labels
        finally:
            tc.__exit__(None, None, None)


class TestPostProvisionHook:
    def test_hook_creates_node_with_compact(self):
        """_ensure_onboarding_node_after_provision (Supabase post-RPC hook)
        creates the node; compact derived from the caller's PRIOR memberships
        (the new team's membership is excluded)."""
        import uuid

        from tests.fake_control_plane import FakeControlPlane
        from tortoise.supabase_control import _ensure_onboarding_node_after_provision
        org_id = f"hook{uuid.uuid4().hex[:10]}"
        user_id = str(uuid.uuid4())
        fake = FakeControlPlane({"organizations": [], "org_memberships": [], "api_keys": []})
        fake.seed("org_memberships", [
            {"id": "m1", "org_id": f"prior{uuid.uuid4().hex[:10]}",
             "user_id": user_id, "role": "owner", "status": "active",
             "created_at": "2026-08-01T00:00:00Z"},
            {"id": "m2", "org_id": org_id, "user_id": user_id,
             "role": "owner", "status": "active",
             "created_at": "2026-08-02T00:00:00Z"},
        ])
        _ensure_onboarding_node_after_provision(
            fake, org_id, {"p_org_id": org_id, "p_user_id": user_id})
        node = _read_node(org_id)
        assert node is not None
        # prior memberships (excluding the new team) = 1 → compact
        assert node["compact"] is True
        assert node.get("fork") == "self"

    def test_hook_first_org_fork_none(self):
        """A creator with NO prior memberships gets fork=None (card asked)."""
        import uuid

        from tests.fake_control_plane import FakeControlPlane
        from tortoise.supabase_control import _ensure_onboarding_node_after_provision
        org_id = f"hook2{uuid.uuid4().hex[:10]}"
        user_id = str(uuid.uuid4())
        fake = FakeControlPlane({"organizations": [], "org_memberships": [], "api_keys": []})
        fake.seed("org_memberships", [{"id": "m1", "org_id": org_id,
                                        "user_id": user_id, "role": "owner",
                                        "status": "active",
                                        "created_at": "2026-08-01T00:00:00Z"}])
        _ensure_onboarding_node_after_provision(
            fake, org_id, {"p_org_id": org_id, "p_user_id": user_id})
        node = _read_node(org_id)
        assert node is not None
        assert node["compact"] is False
        assert "fork" not in node or node["fork"] is None
