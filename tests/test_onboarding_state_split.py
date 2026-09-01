"""#2001 (W5) graph-held OnboardingState — docker-lane integration tests.

TestNodeInit (T2): every provision path initializes the OnboardingState node
(eager, same statement as TeamMeta, or the post-RPC hook / write-time
create-on-write seam); concurrent inits converge to one node; export/restore
preserves fork + completed_steps; OnboardingState/OnboardingStep are NEVER
added to _EXPORT_SKIP_LABELS (backup-safe, scope pin 17).

Runs in the docker lane (TORTOISE_DB_URI) — the graph writes land on the
real FalkorDB test matrix graph. URI-less runs (tier-2 embedded legs,
carve-out) SKIP at module level: these assertions exercise hosted
registry lanes whose eager-init Cypher + keyed-MERGE writers are
server-mode graph semantics (embedded redislite cannot satisfy them —
#1997 tier-2 regression).
"""
from __future__ import annotations

import os
import sys
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


def _read_node(team_id: str):
    return onboarding_state.read_onboarding_node(
        _make_sdk(namespace=team_id)._get_proj(), team_id)


def _completed(team_id: str):
    return onboarding_state.completed_steps(
        _make_sdk(namespace=team_id)._get_proj(), team_id)


@pytest.fixture
def client():
    """Registry-mode TestClient on the docker lane (env URI). No override —
    register/_create lanes exercise their real graph writes."""
    with TestClient(app) as tc:
        yield tc


class TestNodeInit:
    def test_register_creates_node_with_team_named_edge(self, client):
        """Register (registry lane) → OnboardingState node initialized in the
        SAME statement as TeamMeta with the deterministic write set."""
        import uuid
        email = f"w5-{uuid.uuid4().hex[:10]}@example.com"
        r = client.post("/v1/register", json={"email": email,
                                              "password": "password123"})
        assert r.status_code == 200, r.text
        team_id = r.json()["team_id"]
        node = _read_node(team_id)
        assert node is not None
        assert node["org_id"] == team_id
        assert node["status"] == "active"
        assert node["version"] == 1
        assert node["compact"] is False
        assert "fork" not in node or node["fork"] is None  # first org → card asked once
        assert "team-named" in _completed(team_id)

    def test_sdk_team_create_inits_node(self):
        """SDK lane (CI-visible): sdk.team_create → node exists in team_{name}."""
        import uuid
        name = f"w5sdk{uuid.uuid4().hex[:8]}"
        sdk = TortoiseSDK(namespace="registry")
        team = sdk.team_create(name)
        try:
            node = onboarding_state.read_onboarding_node(
                sdk._get_proj().db.select_graph(f"team_{name}"), team["id"])
            assert node is not None
            assert node["status"] == "active"
            assert "team-named" in onboarding_state.completed_steps(
                sdk._get_proj().db.select_graph(f"team_{name}"), team["id"])
        finally:
            sdk.close()

    def test_second_team_compact_and_fork_inheritance(self):
        """Second org for the same creator → compact=True (prior memberships)
        and fork inherited from the earliest prior org ('self' fallback when
        the prior org has no fork)."""
        import uuid
        sdk = TortoiseSDK(namespace="registry")
        try:
            first = sdk.team_create(f"w5a{uuid.uuid4().hex[:8]}")
            user_id = f"user-{uuid.uuid4().hex[:8]}"
            sdk.membership_create(first["id"], user_id, "owner")
            second = sdk.team_create(f"w5b{uuid.uuid4().hex[:8]}",
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
            first = sdk.team_create(f"w5c{uuid.uuid4().hex[:8]}")
            user_id = f"user-{uuid.uuid4().hex[:8]}"
            sdk.membership_create(first["id"], user_id, "owner")
            # creator answers the fork card on the FIRST org → 'build'
            onboarding_state.write_fork(
                sdk._get_proj().db.select_graph(first["graph_name"]),
                first["id"], "build")
            second = sdk.team_create(f"w5d{uuid.uuid4().hex[:8]}",
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
        team_id = f"tp{uuid.uuid4().hex[:12]}"
        r = client.post("/internal/provision",
                        headers={"Authorization": "Bearer test-internal-key"},
                        json={"team_id": team_id, "team_name": f"TP {team_id[:6]}",
                              "api_key_hash": "x" * 64, "created_by": "tp-user"})
        assert r.status_code == 200, r.text
        node = _read_node(team_id)
        assert node is not None
        assert node["status"] == "active"
        assert "team-named" in _completed(team_id)

    def test_concurrent_init_single_node(self):
        """Concurrent keyed-MERGE inits on the SAME org → exactly one node
        and one edge (per-graph write serialization + idempotent MERGE)."""
        import uuid

        from tortoise.hosted_api import _make_sdk as _ms
        team_id = f"cc{uuid.uuid4().hex[:12]}"
        sdk = _ms(namespace=team_id)
        proj = sdk._get_proj()
        errs: list[Exception] = []

        def _worker():
            try:
                onboarding_state.ensure_onboarding_state_node(proj, team_id)
                onboarding_state.write_completed_step(proj, team_id,
                                                      "harness-connected")
            except Exception as exc:  # pragma: no cover
                errs.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errs
        node = onboarding_state.read_onboarding_node(proj, team_id)
        assert node is not None
        steps = onboarding_state.completed_steps(proj, team_id)
        assert steps.count("harness-connected") == 1

    def test_create_on_write_seam(self):
        """An absent-node org self-heals on the FIRST FLOW write — the write
        path creates the node (never the read path)."""
        import uuid
        team_id = f"cow{uuid.uuid4().hex[:12]}"
        proj = _make_sdk(namespace=team_id)._get_proj()
        assert onboarding_state.read_onboarding_node(proj, team_id) is None
        res = onboarding_state.write_completed_step(proj, team_id,
                                                    "harness-connected")
        assert res["created"] is True
        node = onboarding_state.read_onboarding_node(proj, team_id)
        assert node is not None
        assert node["status"] == "active"
        assert "harness-connected" in onboarding_state.completed_steps(
            proj, team_id)
        # set-once fork on the same node
        outcome = onboarding_state.write_fork(proj, team_id, "self")
        assert outcome == "set"

    def test_mirror_status_from_jsonb_one_directional(self):
        """create-on-write mirrors jsonb onboarding_complete → 'complete'
        (one-directional; never jsonb-false → complete; never clobbers)."""
        import uuid
        team_id = f"mir{uuid.uuid4().hex[:12]}"
        proj = _make_sdk(namespace=team_id)._get_proj()
        onboarding_state.ensure_onboarding_state_node(
            proj, team_id, status_from_mirror=True)
        node = onboarding_state.read_onboarding_node(proj, team_id)
        assert node["status"] == "complete"
        # mirror=False on an existing complete node never clobbers
        onboarding_state.ensure_onboarding_state_node(
            proj, team_id, status_from_mirror=False)
        assert onboarding_state.read_onboarding_node(proj, team_id)["status"] == "complete"

    def test_status_monotonic(self):
        import uuid
        team_id = f"mon{uuid.uuid4().hex[:12]}"
        proj = _make_sdk(namespace=team_id)._get_proj()
        onboarding_state.ensure_onboarding_state_node(proj, team_id)
        onboarding_state.write_status(proj, team_id, "complete")
        # complete can never regress to active
        onboarding_state.write_status(proj, team_id, "active")
        assert onboarding_state.read_onboarding_node(proj, team_id)["status"] == "complete"

    def test_backup_roundtrip_preserves_flow(self):
        """Export → restore round-trip: fork + completed_steps survive; the
        node + step labels are exported by default (NOT in _EXPORT_SKIP_LABELS)."""
        import uuid

        from tortoise import hosted_backup
        from tortoise.hosted_api import _is_export_skip_node
        # the labels must not be skip-labelled (backup-safe pin)
        assert _is_export_skip_node(["OnboardingState"], {}) is False
        assert _is_export_skip_node(["OnboardingStep"], {}) is False
        team_id = f"bk{uuid.uuid4().hex[:12]}"
        sdk = _make_sdk(namespace=team_id)
        proj = sdk._get_proj()
        g = proj.db.select_graph(f"team_{team_id}")
        onboarding_state.ensure_onboarding_state_node(g, team_id)
        onboarding_state.write_fork(g, team_id, "build")
        onboarding_state.write_completed_step(g, team_id, "harness-connected")
        onboarding_state.write_completed_step(g, team_id, "first-points-filed")
        dump = hosted_backup.dump_graph(g, graph_name=f"team_{team_id}")
        node_labels = {tuple(n["labels"]) for n in dump["nodes"]}
        assert ("OnboardingState",) in node_labels
        assert ("OnboardingStep",) in node_labels
        # wipe + restore
        g.query("MATCH (n) DETACH DELETE n")
        hosted_backup.restore_graph(g, dump)
        node = onboarding_state.read_onboarding_node(g, team_id)
        assert node is not None
        assert node.get("fork") == "build"
        steps = set(onboarding_state.completed_steps(g, team_id))
        assert {"harness-connected", "first-points-filed"} <= steps


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
    return tc, r.json()["team_id"]


class TestWriter:
    def test_flow_keys_never_in_jsonb(self):
        """FLOW keys routed through the router never round-trip into jsonb
        (registration-split negative — the jsonb store has no FLOW keys even
        after FLOW writes)."""
        from tortoise.hosted_api import _get_onboarding_state as _raw
        tc, team_id = _registered_client()
        try:
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "harness-connected"})
            tc.post("/v1/onboarding/state/checkpoint", json={"fork": "self"})
            raw = _raw(team_id)
            for k in onboarding_state.FLOW_KEYS:
                assert k not in raw, f"FLOW key {k} leaked into jsonb"
            for step in onboarding_state.STEP_IDS:
                assert step not in raw
        finally:
            tc.__exit__(None, None, None)

    def test_operational_keys_still_round_trip(self):
        tc, _team_id = _registered_client()
        try:
            r = tc.patch("/v1/onboarding/state",
                         json={"prompt_pasted": True})
            assert r.status_code == 200
            assert r.json()["onboarding"]["prompt_pasted"] is True
        finally:
            tc.__exit__(None, None, None)

    def test_unknown_key_dropped_fail_closed(self):
        tc, _team_id = _registered_client()
        try:
            r = tc.patch("/v1/onboarding/state", json={"bogus_key": 1})
            assert r.status_code == 200  # unknown → dropped, never default-to-FLOW
            assert "bogus_key" not in r.json()["onboarding"]
        finally:
            tc.__exit__(None, None, None)

    def test_write_strips_flow_defensively(self):
        from tortoise.hosted_api import _get_onboarding_state as _raw
        from tortoise.hosted_api import _write_onboarding_state as _w
        tc, team_id = _registered_client()
        try:
            state = dict(_raw(team_id))
            state["fork"] = "self"
            state["harness-connected"] = True
            _w(team_id, state)
            after = _raw(team_id)
            assert "fork" not in after
            assert "harness-connected" not in after
        finally:
            tc.__exit__(None, None, None)


class TestCheckpoint:
    def test_created_and_noop_signals(self):
        tc, _team_id = _registered_client()
        try:
            r1 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"step": "harness-connected"})
            assert r1.status_code == 200
            assert r1.json()["created_steps"] == ["harness-connected"]
            assert r1.json()["noop_steps"] == []
            r2 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"step": "harness-connected"})
            assert r2.json()["created_steps"] == []
            assert r2.json()["noop_steps"] == ["harness-connected"]
        finally:
            tc.__exit__(None, None, None)

    def test_unknown_step_422(self):
        tc, _team_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "bogus-step"})
            assert r.status_code == 422
        finally:
            tc.__exit__(None, None, None)

    def test_team_named_not_checkpointable(self):
        tc, _team_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "team-named"})
            assert r.status_code == 422
        finally:
            tc.__exit__(None, None, None)

    def test_fork_set_once_contract(self):
        tc, _team_id = _registered_client()
        try:
            r1 = tc.post("/v1/onboarding/state/checkpoint", json={"fork": "self"})
            assert r1.status_code == 200
            assert r1.json()["onboarding"]["fork"] == "self"
            r2 = tc.post("/v1/onboarding/state/checkpoint", json={"fork": "self"})
            assert r2.status_code == 200  # same-value replay
            r3 = tc.post("/v1/onboarding/state/checkpoint", json={"fork": "build"})
            assert r3.status_code == 409  # changed → conflict
        finally:
            tc.__exit__(None, None, None)

    def test_compact_set_once_contract(self):
        """compact is computed eagerly (prior memberships); a checkpoint
        compact write on an init'd org is set-once: same-value 200, changed
        409. On a PRE-init node (no compact property) the first write sets."""
        tc, _team_id = _registered_client()
        try:
            # init'd org: compact=False is already set → changing → 409
            r1 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"compact": True})
            assert r1.status_code == 409
            r2 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"compact": False})
            assert r2.status_code == 200  # same-value replay
        finally:
            tc.__exit__(None, None, None)

        # pre-init node (created before the eager statement shipped): the
        # first write SETS (create-on-write, byte-identical defaults)
        import uuid

        from tortoise.hosted_api import _make_sdk as _ms
        tid = f"preinit{uuid.uuid4().hex[:8]}"
        proj = _ms(namespace=tid)._get_proj()
        proj.query("CREATE (:OnboardingState {org_id: $o})", o=tid)
        outcome = onboarding_state.write_compact(proj, tid, True)
        assert outcome == "set"
        node = onboarding_state.read_onboarding_node(proj, tid)
        assert node["compact"] is True

    def test_status_server_owned_403(self):
        tc, _team_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"status": "complete"})
            assert r.status_code == 403
        finally:
            tc.__exit__(None, None, None)

    def test_last_decide_attempt_lww_conditional(self):
        """'failed' is SKIPPED once decide-completed exists (dismissal alone
        never completes; failed never un-completes)."""
        tc, _team_id = _registered_client()
        try:
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"last_decide_attempt": "dismissed"})
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "harness-connected"})
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "first-points-filed"})
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "decide-completed"})
            assert r.json()["onboarding"]["status"] == "complete"
            r2 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"last_decide_attempt": "failed"})
            assert r2.status_code == 200
            # pin the PRESERVED value (dismissal survives; failed never
            # un-completes) — the old `!= "failed"` passed even if the field
            # were silently dropped/reset, masking a data-loss regression.
            assert r2.json()["onboarding"]["last_decide_attempt"] == "dismissed"
            assert r2.json()["onboarding"]["status"] == "complete"
        finally:
            tc.__exit__(None, None, None)

    def test_member_progress_key_auth_non_uuid_403(self):
        """Key-authed (no session user) member_progress requires a UUID
        user_id (no cross-user forgery)."""
        tc, _team_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"member_progress": {"not-a-uuid": []}})
            assert r.status_code == 403
        finally:
            tc.__exit__(None, None, None)

    def test_last_decide_attempt_invalid_422(self):
        """Invalid last_decide_attempt values are client errors (422), not
        500s — sibling ops validate at the boundary."""
        tc, _team_id = _registered_client()
        try:
            for bad in ("postponed", "completed", 42, ""):
                r = tc.post("/v1/onboarding/state/checkpoint",
                            json={"last_decide_attempt": bad})
                assert r.status_code == 422, (bad, r.status_code)
        finally:
            tc.__exit__(None, None, None)

    def test_member_progress_valid_steps(self):
        import uuid
        tc, _team_id = _registered_client()
        try:
            uid = str(uuid.uuid4())
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"member_progress": {
                            uid: ["harness-connected"]}})
            assert r.status_code == 200
            assert r.json()["onboarding"]["member_progress"][uid] == [
                "harness-connected"]
        finally:
            tc.__exit__(None, None, None)

    def test_member_progress_map_merge_preserves_other_users(self):
        """MAP_MERGE semantic: a second user's write preserves the first
        user's entry — a clobbering regression (replacing the whole map)
        must fail this test."""
        import uuid
        tc, _team_id = _registered_client()
        try:
            u1 = str(uuid.uuid4())
            u2 = str(uuid.uuid4())
            r1 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"member_progress": {
                             u1: ["harness-connected"]}})
            assert r1.status_code == 200
            r2 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"member_progress": {
                             u2: ["decide-completed"]}})
            assert r2.status_code == 200
            mp = r2.json()["onboarding"]["member_progress"]
            assert mp[u1] == ["harness-connected"]  # preserved, not clobbered
            assert mp[u2] == ["decide-completed"]
        finally:
            tc.__exit__(None, None, None)

    def test_member_progress_invalid_steps_422(self):
        """Step-value validation: non-canonical step ids and non-list values
        are rejected with 422 invalid_member_progress — no partial write."""
        import uuid
        tc, _team_id = _registered_client()
        try:
            uid = str(uuid.uuid4())
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"member_progress": {
                            uid: ["bogus-step"]}})
            assert r.status_code == 422
            r2 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"member_progress": {
                             uid: "harness-connected"}})
            assert r2.status_code == 422
        finally:
            tc.__exit__(None, None, None)

    def test_two_ops_400(self):
        tc, _team_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "capture-disclosed", "fork": "self"})
            assert r.status_code == 400
        finally:
            tc.__exit__(None, None, None)

    def test_extra_forbid(self):
        tc, _team_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "capture-disclosed", "bogus": 1})
            assert r.status_code == 422
        finally:
            tc.__exit__(None, None, None)

    def test_self_journey_gate_completes(self):
        """Full self-fork journey → gate eval → status complete + wire true
        (monotonic: complete can never regress)."""
        tc, _team_id = _registered_client()
        try:
            tc.post("/v1/onboarding/state/checkpoint", json={"fork": "self"})
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "harness-connected"})
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "first-points-filed"})
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "decide-completed"})
            body = r.json()["onboarding"]
            assert body["status"] == "complete"
            assert body["onboarding_complete"] is True
            assert body["version"] == 1
            # monotonic — a later noop write cannot regress
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "harness-connected"})
            g = tc.get("/v1/onboarding/state").json()["onboarding"]
            assert g["status"] == "complete"
            assert g["onboarding_complete"] is True
        finally:
            tc.__exit__(None, None, None)

    def test_build_journey_uses_catalog(self):
        tc, _team_id = _registered_client()
        try:
            tc.post("/v1/onboarding/state/checkpoint", json={"fork": "build"})
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "harness-connected"})
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "first-points-filed"})
            # decide does NOT complete a build org
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "decide-completed"})
            assert r.json()["onboarding"]["status"] == "active"
            # catalog-presented completes it
            r2 = tc.patch("/v1/onboarding/state",
                          json={"catalog_presented": True})
            assert r2.json()["onboarding"]["status"] == "complete"
        finally:
            tc.__exit__(None, None, None)

    def test_checkpoint_cross_org_isolation(self):
        """Cross-org forgery is impossible — the team comes from the auth
        context; a second team's key cannot write the first team's state
        (200, routed to ITS OWN node, not team A's)."""
        import uuid
        tc, team_a = _registered_client()
        try:
            r = tc.post("/v1/register",
                        json={"email": f"x{uuid.uuid4().hex[:8]}@example.com",
                              "password": "password123"})
            tc.headers.update({"Authorization": f"Bearer {r.json()['api_key']}"})
            # the second key writes ITS OWN team's node, never team A's
            r2 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"step": "harness-connected"})
            assert r2.status_code == 200
            from tortoise.hosted_api import _get_onboarding_projection as _proj
            # team A untouched
            assert "harness-connected" not in _proj(team_a)["completed_steps"]
            # AND the step DID land on team B — a silent-drop regression
            # (200 with no write anywhere) must not pass.
            team_b = r.json()["team_id"]
            assert "harness-connected" in _proj(team_b)["completed_steps"]
        finally:
            tc.__exit__(None, None, None)


class TestPatchRouting:
    def test_server_owned_keys_403(self):
        tc, _team_id = _registered_client()
        try:
            for payload in ({"status": "complete"}, {"fork": "self"},
                            {"version": 2}, {"completed_steps": []},
                            {"compact": True},
                            {"last_decide_attempt": "failed"},
                            {"member_progress": {}}):
                r = tc.patch("/v1/onboarding/state", json=payload)
                assert r.status_code == 403, payload
        finally:
            tc.__exit__(None, None, None)

    def test_agent_step_keys_422(self):
        tc, _team_id = _registered_client()
        try:
            for payload in ({"decide_completed": True},
                            {"harness_connected": True},
                            {"first_points_filed": True},
                            {"capture_disclosed": True},
                            {"team_named": True}):
                r = tc.patch("/v1/onboarding/state", json=payload)
                assert r.status_code == 422, payload
        finally:
            tc.__exit__(None, None, None)

    def test_catalog_presented_writes_step_edge(self):
        tc, _team_id = _registered_client()
        try:
            r = tc.patch("/v1/onboarding/state",
                         json={"catalog_presented": True})
            assert r.status_code == 200
            assert "catalog-presented" in r.json()["onboarding"]["completed_steps"]
        finally:
            tc.__exit__(None, None, None)

    def test_catalog_presented_false_is_noop(self):
        """False must NOT mark the catalog-presented step edge (only True
        does) — a non-None-but-False value flipping the build gate was the
        review-found bug this pins."""
        tc, _team_id = _registered_client()
        try:
            r = tc.patch("/v1/onboarding/state",
                         json={"catalog_presented": False})
            assert r.status_code == 200
            assert "catalog-presented" not in r.json()["onboarding"]["completed_steps"]
        finally:
            tc.__exit__(None, None, None)

    def test_wire_compat_preserved(self):
        """underscore→hyphen translation still works (session_capture_receipt
        claude_desktop → claude-desktop key)."""
        tc, _team_id = _registered_client()
        try:
            r = tc.patch("/v1/onboarding/state",
                         json={"session_capture_receipt_claude_desktop": "r1"})
            assert r.status_code == 200
            assert r.json()["onboarding"]["session_capture_receipt_claude-desktop"] == "r1"
        finally:
            tc.__exit__(None, None, None)

    def test_team_created_stripped(self):
        tc, _team_id = _registered_client()
        try:
            r = tc.patch("/v1/onboarding/state", json={"team_created": True})
            assert r.status_code == 200
            assert r.json()["onboarding"]["team_created"] is False  # server-authoritative
        finally:
            tc.__exit__(None, None, None)

    def test_onboarding_complete_accept_and_drop(self):
        """#1997 (W1): accept-and-drop — a client PATCH onboarding_complete
        on a NODE-PRESENT org is DROPPED (accepted 200; the echo is
        node-governed — the legacy jsonb flag is inert there)."""
        tc, team_id = _registered_client()
        try:
            from tortoise.hosted_api import _get_onboarding_state as _raw
            r = tc.patch("/v1/onboarding/state",
                         json={"onboarding_complete": True})
            assert r.status_code == 200
            # dropped → node governs (active, zero edges → not complete)
            assert r.json()["onboarding"]["onboarding_complete"] is False
            # the jsonb flag was never written
            assert _raw(team_id).get("onboarding_complete") is False
        finally:
            tc.__exit__(None, None, None)

    def test_mixed_key_patch_graph_failure_500(self):
        """jsonb-first graph-second: graph failure after jsonb success → 500
        fail-closed, retry-safe (no lost FLOW keys on retry)."""
        import tortoise.hosted_api as _ha
        tc, _team_id = _registered_client()
        try:
            orig = _ha._os.write_completed_step
            def _boom(*a, **k):
                raise RuntimeError("graph down")
            _ha._os.write_completed_step = _boom
            try:
                r = tc.patch("/v1/onboarding/state",
                             json={"prompt_pasted": True,
                                   "catalog_presented": True})
                assert r.status_code == 500
            finally:
                _ha._os.write_completed_step = orig
            # jsonb side persisted; retry converges
            r2 = tc.patch("/v1/onboarding/state",
                          json={"catalog_presented": True})
            assert r2.status_code == 200
            assert r2.json()["onboarding"]["prompt_pasted"] is True
            assert "catalog-presented" in r2.json()["onboarding"]["completed_steps"]
        finally:
            tc.__exit__(None, None, None)


class TestPreserved409s:
    def test_already_registered_409_preserved(self):
        """already_registered (the router-adjacent 409) still 409 after the
        router retarget. Dup-name + sub-team re-entry + session-recording-off
        409s are covered by their existing suites (endpoints / teams / demo)
        and enumerated in scope pin 9."""
        tc, _team_id = _registered_client()
        try:
            _ = tc.post("/v1/register",
                        json={"email": "dup409@example.com",
                              "password": "password123"})
            r2 = tc.post("/v1/register",
                         json={"email": "dup409@example.com",
                               "password": "password123"})
            assert r2.status_code == 409
        finally:
            tc.__exit__(None, None, None)


class TestBackfill:
    def test_absent_node_complete_created(self):
        import uuid
        team_id = f"bf{uuid.uuid4().hex[:10]}"
        proj = _make_sdk(namespace=team_id)._get_proj()
        res = onboarding_state.backfill_org(proj, team_id, True, dry_run=False)
        assert res["action"] == "created-complete"
        node = onboarding_state.read_onboarding_node(proj, team_id)
        assert node["status"] == "complete"
        assert "fork" not in node or node["fork"] is None  # read-time default

    def test_rerun_noop(self):
        import uuid
        team_id = f"bf2{uuid.uuid4().hex[:10]}"
        proj = _make_sdk(namespace=team_id)._get_proj()
        onboarding_state.backfill_org(proj, team_id, True, dry_run=False)
        res = onboarding_state.backfill_org(proj, team_id, True, dry_run=False)
        assert res["action"] == "skipped-node-present"

    def test_never_clobbers_node_present(self):
        import uuid
        team_id = f"bf3{uuid.uuid4().hex[:10]}"
        proj = _make_sdk(namespace=team_id)._get_proj()
        onboarding_state.ensure_onboarding_state_node(proj, team_id)
        onboarding_state.write_fork(proj, team_id, "build")
        res = onboarding_state.backfill_org(proj, team_id, True, dry_run=False)
        assert res["action"] == "skipped-node-present"
        node = onboarding_state.read_onboarding_node(proj, team_id)
        assert node["status"] == "active"  # untouched
        assert node.get("fork") == "build"

    def test_never_jsonb_false_to_complete(self):
        import uuid
        team_id = f"bf4{uuid.uuid4().hex[:10]}"
        proj = _make_sdk(namespace=team_id)._get_proj()
        res = onboarding_state.backfill_org(proj, team_id, False, dry_run=False)
        assert res["action"] == "skipped-not-complete"
        assert onboarding_state.read_onboarding_node(proj, team_id) is None

    def test_dry_run_no_write(self):
        import uuid
        team_id = f"bf5{uuid.uuid4().hex[:10]}"
        proj = _make_sdk(namespace=team_id)._get_proj()
        res = onboarding_state.backfill_org(proj, team_id, True, dry_run=True)
        assert res["action"] == "would-create-complete"
        assert onboarding_state.read_onboarding_node(proj, team_id) is None

    def test_wrapper_dry_run_and_apply(self):
        """graph-scripts wrapper: DRY-RUN default (no writes), --apply writes,
        re-run no-op — end to end on the registry lane."""
        import json as _json
        import subprocess
        import uuid

        # seed a grandfathered org: a Team node with legacy complete state
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK(namespace="registry")
        team_id = f"bfwrap{uuid.uuid4().hex[:8]}"
        sdk._get_registry().query(
            "CREATE (t:Team {id: $id, name: $name, graph_name: $gn, "
            "onboarding_state: $os})",
            params={"id": team_id, "name": f"bfw-{team_id[:6]}",
                    "gn": f"team_{team_id}",
                    "os": _json.dumps({"onboarding_complete": True,
                                        "github_connected": True})})
        env = dict(os.environ)
        py = sys.executable
        script = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "graph-scripts",
            "backfill_onboarding_state.py")
        # DRY-RUN
        r = subprocess.run([py, script, "--limit", "0"],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        node = onboarding_state.read_onboarding_node(
            sdk._get_proj().db.select_graph(f"team_{team_id}"), team_id)
        assert node is None  # dry-run wrote nothing
        # APPLY
        r = subprocess.run([py, script, "--apply"],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        node = onboarding_state.read_onboarding_node(
            sdk._get_proj().db.select_graph(f"team_{team_id}"), team_id)
        assert node is not None
        assert node["status"] == "complete"
        # re-run no-op — pin the COUNTED outcome, not the always-present label:
        # the summary line prints "skipped-node-present N" even at N=0, so a
        # lost-idempotency regression (re-creating nodes) would pass the old
        # substring check. "created 0" is the honest no-op signal.
        r = subprocess.run([py, script, "--apply"],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        assert "created 0" in r.stdout, r.stdout
        assert "skipped-node-present" in r.stdout, r.stdout
        # and the node property set is unchanged by the re-run
        node2 = onboarding_state.read_onboarding_node(
            sdk._get_proj().db.select_graph(f"team_{team_id}"), team_id)
        assert node2 == node

    def test_recompute_grandfathered_first(self):
        """Recompute sweep: grandfathered branch runs BEFORE gate eval — a
        zero-agent-edge org with a legacy complete flag is promoted (never
        re-onboarded); an edge-bearing org is gate-evaluated; complete never
        regresses."""
        import uuid
        # org A: no edges + legacy complete → grandfathered complete
        ta = f"rc{uuid.uuid4().hex[:8]}"
        ga = _make_sdk(namespace=ta)._get_proj()
        onboarding_state.ensure_onboarding_state_node(ga, ta)
        assert onboarding_state.recompute_completion(ga, ta, True) == "complete-grandfathered"
        assert onboarding_state.read_onboarding_node(ga, ta)["status"] == "complete"
        # monotonic: never regresses
        assert onboarding_state.recompute_completion(ga, ta, True) == "unchanged-already-complete"
        # org B: full self gate via edges → gate-complete
        tb = f"rc{uuid.uuid4().hex[:8]}"
        gb = _make_sdk(namespace=tb)._get_proj()
        onboarding_state.ensure_onboarding_state_node(gb, tb)
        onboarding_state.write_fork(gb, tb, "self")
        for step in ("harness-connected", "first-points-filed",
                     "decide-completed"):
            onboarding_state.write_completed_step(gb, tb, step)
        assert onboarding_state.recompute_completion(gb, tb, False) == "complete-gate"
        # org C: incomplete stays active
        tc = f"rc{uuid.uuid4().hex[:8]}"
        gc = _make_sdk(namespace=tc)._get_proj()
        onboarding_state.ensure_onboarding_state_node(gc, tc)
        assert onboarding_state.recompute_completion(gc, tc, False) == "unchanged"
        assert onboarding_state.read_onboarding_node(gc, tc)["status"] == "active"


class TestCompletionWire:
    def test_poisoned_new_org_negative(self):
        """A new org's legacy flag is never trusted once the agent flow
        engages (node governs — the poisoned-TRUE the precedence kills).
        The flag is raw-written (post-W1 the PATCH surface accept-and-drops)."""
        tc, team_id = _registered_client()
        try:
            from tortoise.hosted_api import _get_onboarding_state as _raw
            from tortoise.hosted_api import _write_onboarding_state as _w
            st = _raw(team_id)
            st["onboarding_complete"] = True
            _w(team_id, st)
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "harness-connected"})
            st = tc.get("/v1/onboarding/state").json()["onboarding"]
            assert st["onboarding_complete"] is False  # edge → node governs
        finally:
            tc.__exit__(None, None, None)

    def test_node_complete_wire_true(self):
        tc, _team_id = _registered_client()
        try:
            for step in ("harness-connected", "first-points-filed",
                         "decide-completed"):
                tc.post("/v1/onboarding/state/checkpoint", json={"step": step})
            st = tc.get("/v1/onboarding/state").json()["onboarding"]
            assert st["status"] == "complete"
            assert st["onboarding_complete"] is True
        finally:
            tc.__exit__(None, None, None)

    def test_grandfathered_node_absent_fallback(self):
        """No node (pre-backfill grandfathered) + jsonb true → wire true;
        jsonb false → false."""
        import uuid

        from tortoise.hosted_api import _get_onboarding_projection as _proj
        team_id = f"gfwire{uuid.uuid4().hex[:8]}"
        proj = _make_sdk(namespace=team_id)._get_proj()
        # NO node — simulate by not creating it
        assert onboarding_state.read_onboarding_node(proj, team_id) is None
        st = _proj(team_id)
        assert st["onboarding_complete"] is False
        assert st["status"] == "active"

    def test_poisoned_false_guard(self):
        """The grandfathered-window guard (cycle-2 P1-1 fix): node present,
        active, ZERO agent edges, LEGACY jsonb true (raw-written — the PATCH
        surface accept-and-drops onboarding_complete post-W1) → wire TRUE (a
        legacy-wizard completer is never re-onboarded)."""
        tc, team_id = _registered_client()
        try:
            # seed the legacy jsonb flag via the RAW writer (the PATCH
            # surface accept-and-drops it on node-present orgs, #1997)
            from tortoise.hosted_api import _get_onboarding_state as _raw
            from tortoise.hosted_api import _write_onboarding_state as _w
            st = _raw(team_id)
            st["onboarding_complete"] = True
            _w(team_id, st)
            r = tc.get("/v1/onboarding/state")
            st = r.json()["onboarding"]
            assert st["onboarding_complete"] is True
            assert st["status"] == "active"
        finally:
            tc.__exit__(None, None, None)

    def test_grandfathered_first_flow_write_never_reonboards(self):
        """P1 regression (review-found): a legacy-wizard-completed org's FIRST
        agent step write must NOT flip the wire to incomplete. The jsonb
        onboarding_complete=true flag seeds the create-on-write node's status
        from the mirror — without it, the node materializes 'active', the
        zero-edge guard disables, and the org is re-onboarded."""
        tc, team_id = _registered_client()
        try:
            # make this a grandfathered org: jsonb true (RAW-written — the
            # PATCH surface accept-and-drops onboarding_complete post-W1,
            # #1997), node PRESENT active with ZERO agent step edges (the
            # eager-init node)
            from tortoise.hosted_api import _get_onboarding_state as _raw
            from tortoise.hosted_api import _write_onboarding_state as _w
            st = _raw(team_id)
            st["onboarding_complete"] = True
            _w(team_id, st)
            st = tc.get("/v1/onboarding/state").json()["onboarding"]
            assert st["onboarding_complete"] is True
            assert st["status"] == "active"
            # now simulate a node-ABSENT grandfathered org (pre-backfill
            # window): drop the node, keep jsonb true, then first FLOW write.
            import tortoise.onboarding.state as _os2
            from tortoise.hosted_api import _make_sdk as _mk
            proj = _mk(namespace=team_id)._get_proj()
            _os2._run(proj,
                      f"MATCH (n:{_os2.ONBOARDING_NODE_LABEL} "
                      f"{{org_id: $oid}}) DETACH DELETE n",
                      {"oid": team_id})
            assert _os2.read_onboarding_node(proj, team_id) is None
            # FIRST agent FLOW write — the create-on-write seam must seed
            # status from the jsonb mirror (never re-onboard the org).
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "harness-connected"})
            assert r.status_code == 200, r.text
            body = r.json()["onboarding"]
            assert body["onboarding_complete"] is True, body
            assert body["status"] == "complete"
        finally:
            tc.__exit__(None, None, None)

    def test_accept_and_drop_node_absent_keeps_jsonb_writer(self):
        """#1997 (W1): node-ABSENT (grandfathered pre-backfill) orgs keep
        the legacy jsonb writer — a client PATCH onboarding_complete still
        lands in jsonb (their fallback until backfill)."""
        tc, team_id = _registered_client()
        try:
            # drop the node → node-absent grandfathered org
            import tortoise.onboarding.state as _os2
            from tortoise.hosted_api import _make_sdk as _mk
            proj = _mk(namespace=team_id)._get_proj()
            _os2._run(proj,
                      f"MATCH (n:{_os2.ONBOARDING_NODE_LABEL} "
                      f"{{org_id: $oid}}) DETACH DELETE n",
                      {"oid": team_id})
            assert _os2.read_onboarding_node(proj, team_id) is None
            r = tc.patch("/v1/onboarding/state",
                         json={"onboarding_complete": True})
            assert r.status_code == 200, r.text
            from tortoise.hosted_api import _get_onboarding_state as _raw
            assert _raw(team_id).get("onboarding_complete") is True
            # wire completes via the grandfathered window (no node, jsonb true)
            assert r.json()["onboarding"]["onboarding_complete"] is True
        finally:
            tc.__exit__(None, None, None)


class TestProjection:
    def test_merged_get_serves_flow_and_operational(self):
        tc, _team_id = _registered_client()
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
        tc, _team_id = _registered_client()
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
        tc, _team_id = _registered_client()
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
        team_id = f"orphan{uuid.uuid4().hex[:8]}"
        # ensure the team graph exists (register would) but NO node
        _make_sdk(namespace=team_id)._get_proj().db.list_graphs()
        st = _proj(team_id)
        assert st["fork"] is None
        assert st["status"] == "active"
        assert st["completed_steps"] == []
        # no node materialized by the read
        assert onboarding_state.read_onboarding_node(
            _make_sdk(namespace=team_id)._get_proj(), team_id) is None


class TestJourneyLeg:
    def test_de2e1_node_shape_and_onboards_edge(self):
        """DE2E-1 (docker-lane leg): register → node version=1 with the
        deterministic write set + team-named edge; the onboards edge →
        Organization Subject is assertable from W5's read surface (W3's
        seed-write contract is #1999's test)."""
        import uuid
        tc, team_id = _registered_client()
        try:
            node = _read_node(team_id)
            assert node is not None
            assert node["version"] == 1
            assert node["status"] == "active"
            steps = set(_completed(team_id))
            assert "team-named" in steps
            proj = _make_sdk(namespace=team_id)._get_proj()
            # W3-style seed write (the org Subject + onboards edge) — W5
            # asserts the read surface accepts it
            org_id = f"org-{uuid.uuid4().hex[:8]}"
            proj.query(
                "MATCH (n:OnboardingState {org_id: $oid}) "
                "MERGE (s:Subject {subjectKind: 'organization', org_id: $oid, "
                "name: $name}) "
                "MERGE (n)-[:onboards]->(s)",
                oid=team_id, name=org_id)
            res = proj.query(
                "MATCH (n:OnboardingState {org_id: $oid})-[:onboards]->"
                "(s:Subject {subjectKind: 'organization'}) RETURN s.org_id",
                oid=team_id)
            assert res.result_set and res.result_set[0][0] == team_id
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
        team_id = f"hook{uuid.uuid4().hex[:10]}"
        user_id = str(uuid.uuid4())
        fake = FakeControlPlane({"teams": [], "team_memberships": [], "api_keys": []})
        fake.seed("team_memberships", [
            {"id": "m1", "team_id": f"prior{uuid.uuid4().hex[:10]}",
             "user_id": user_id, "role": "owner", "status": "active",
             "created_at": "2026-08-01T00:00:00Z"},
            {"id": "m2", "team_id": team_id, "user_id": user_id,
             "role": "owner", "status": "active",
             "created_at": "2026-08-02T00:00:00Z"},
        ])
        _ensure_onboarding_node_after_provision(
            fake, team_id, {"p_team_id": team_id, "p_user_id": user_id})
        node = _read_node(team_id)
        assert node is not None
        # prior memberships (excluding the new team) = 1 → compact
        assert node["compact"] is True
        assert node.get("fork") == "self"

    def test_hook_first_org_fork_none(self):
        """A creator with NO prior memberships gets fork=None (card asked)."""
        import uuid

        from tests.fake_control_plane import FakeControlPlane
        from tortoise.supabase_control import _ensure_onboarding_node_after_provision
        team_id = f"hook2{uuid.uuid4().hex[:10]}"
        user_id = str(uuid.uuid4())
        fake = FakeControlPlane({"teams": [], "team_memberships": [], "api_keys": []})
        fake.seed("team_memberships", [{"id": "m1", "team_id": team_id,
                                        "user_id": user_id, "role": "owner",
                                        "status": "active",
                                        "created_at": "2026-08-01T00:00:00Z"}])
        _ensure_onboarding_node_after_provision(
            fake, team_id, {"p_team_id": team_id, "p_user_id": user_id})
        node = _read_node(team_id)
        assert node is not None
        assert node["compact"] is False
        assert "fork" not in node or node["fork"] is None
