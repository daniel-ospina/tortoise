"""#2001 (W5) graph-held OnboardingState — docker-lane integration tests.

TestPatchRouting: PATCH key routing — server-owned 403, agent-step 422,
catalog-presented step edge, wire-compat preservation, team-created strip,
onboarding-complete accept-and-drop, mixed-key graph-failure 500.

TestPreserved409s: the router-adjacent already_registered 409 still 409
after the retarget.

TestBackfill: legacy jsonb → node backfill — absent-node complete-created,
re-run no-op, never clobbers a present node, never jsonb-false→complete,
dry-run, the graph-scripts wrapper end to end, and recompute grandfathered
first / config-write grandfathering.

TestCompletionWire: the completion wire — poisoned new-org negative, node
complete true, grandfathered node-absent fallback, poisoned-false guard,
grandfathered-first flow write, and accept-and-drop node-absent jsonb writer.

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


class TestPatchRouting:
    def test_server_owned_keys_403(self):
        tc, _org_id = _registered_client()
        try:
            for payload in ({"status": "complete"}, {"fork": "self"},
                            {"version": 2}, {"completed_steps": []},
                            {"compact": True},
                            {"last_decide_attempt": "failed"},
                            {"member_progress": {}},
                            {"fork_unsure_at": "2026-01-01T00:00:00+00:00"}):
                r = tc.patch("/v1/onboarding/state", json=payload)
                assert r.status_code == 403, payload
        finally:
            tc.__exit__(None, None, None)

    def test_agent_step_keys_422(self):
        tc, _org_id = _registered_client()
        try:
            for payload in ({"decide_completed": True},
                            {"harness_connected": True},
                            {"first_points_filed": True},
                            {"capture_disclosed": True},
                            {"connection_written": True},
                            {"org_named": True}):
                r = tc.patch("/v1/onboarding/state", json=payload)
                assert r.status_code == 422, payload
        finally:
            tc.__exit__(None, None, None)

    def test_catalog_presented_writes_step_edge(self):
        tc, _org_id = _registered_client()
        try:
            r = tc.patch("/v1/onboarding/state",
                         json={"catalog_presented": True})
            assert r.status_code == 200
            assert "catalog-presented" in r.json()["onboarding"]["completed_steps"]
        finally:
            tc.__exit__(None, None, None)

    def test_catalog_presented_false_is_noop(self):
        """False must NOT mark the catalog-presented step edge (only True does).
        No gate claim here: since #3913 `_GATE_BUILD` is
        `{harness-connected, first-points-filed}`, so nothing written under
        this id can complete any fork — the write still has to be correct; it
        is simply no longer load-bearing for completion."""
        tc, _org_id = _registered_client()
        try:
            r = tc.patch("/v1/onboarding/state",
                         json={"catalog_presented": False})
            assert r.status_code == 200
            assert "catalog-presented" not in r.json()["onboarding"]["completed_steps"]
        finally:
            tc.__exit__(None, None, None)

    def test_wire_compat_preserved(self):
        """underscore→hyphen translation still works (session_capture_receipt
        claude_desktop → claude-desktop key).

        #3681: the receipt key is SERVER-OWNED, so the PATCH is now REFUSED —
        but the refusal names the HYPHENATED state key (the translated form),
        which is exactly what proves the translation ran before the ownership
        check. A test that only asserted 200 would now be asserting the
        server-owned hole."""
        tc, _org_id = _registered_client()
        try:
            r = tc.patch("/v1/onboarding/state",
                         json={"session_capture_receipt_claude_desktop": "r1"})
            assert r.status_code == 403, r.text
            assert r.json()["detail"] == {
                "message": "server_owned_key",
                "keys": ["session_capture_receipt_claude-desktop"]}, r.text
        finally:
            tc.__exit__(None, None, None)

    def test_team_created_stripped(self):
        tc, _org_id = _registered_client()
        try:
            r = tc.patch("/v1/onboarding/state", json={"org_created": True})
            assert r.status_code == 200
            assert r.json()["onboarding"]["org_created"] is False  # server-authoritative
        finally:
            tc.__exit__(None, None, None)

    def test_onboarding_complete_accept_and_drop(self):
        """#1997 (W1): accept-and-drop — a client PATCH onboarding_complete
        on a NODE-PRESENT org is DROPPED (accepted 200; the echo is
        node-governed — the legacy jsonb flag is inert there)."""
        tc, org_id = _registered_client()
        try:
            from tortoise.hosted_api import _get_onboarding_state as _raw
            r = tc.patch("/v1/onboarding/state",
                         json={"onboarding_complete": True})
            assert r.status_code == 200
            # dropped → node governs (active, zero edges → not complete)
            assert r.json()["onboarding"]["onboarding_complete"] is False
            # the jsonb flag was never written
            assert _raw(org_id).get("onboarding_complete") is False
        finally:
            tc.__exit__(None, None, None)

    def test_mixed_key_patch_graph_failure_500(self):
        """jsonb-first graph-second: graph failure after jsonb success → 500
        fail-closed, retry-safe (no lost FLOW keys on retry)."""
        import tortoise.hosted_api as _ha
        tc, _org_id = _registered_client()
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
        tc, _org_id = _registered_client()
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
        org_id = f"bf{uuid.uuid4().hex[:10]}"
        proj = _make_sdk(namespace=org_id)._get_proj()
        res = onboarding_state.backfill_org(proj, org_id, True, dry_run=False)
        assert res["action"] == "created-complete"
        node = onboarding_state.read_onboarding_node(proj, org_id)
        assert node["status"] == "complete"
        assert "fork" not in node or node["fork"] is None  # read-time default

    def test_rerun_noop(self):
        import uuid
        org_id = f"bf2{uuid.uuid4().hex[:10]}"
        proj = _make_sdk(namespace=org_id)._get_proj()
        onboarding_state.backfill_org(proj, org_id, True, dry_run=False)
        res = onboarding_state.backfill_org(proj, org_id, True, dry_run=False)
        assert res["action"] == "skipped-node-present"

    def test_never_clobbers_node_present(self):
        import uuid
        org_id = f"bf3{uuid.uuid4().hex[:10]}"
        proj = _make_sdk(namespace=org_id)._get_proj()
        onboarding_state.ensure_onboarding_state_node(proj, org_id)
        onboarding_state.write_fork(proj, org_id, "build")
        res = onboarding_state.backfill_org(proj, org_id, True, dry_run=False)
        assert res["action"] == "skipped-node-present"
        node = onboarding_state.read_onboarding_node(proj, org_id)
        assert node["status"] == "active"  # untouched
        assert node.get("fork") == "build"

    def test_never_jsonb_false_to_complete(self):
        import uuid
        org_id = f"bf4{uuid.uuid4().hex[:10]}"
        proj = _make_sdk(namespace=org_id)._get_proj()
        res = onboarding_state.backfill_org(proj, org_id, False, dry_run=False)
        assert res["action"] == "skipped-not-complete"
        assert onboarding_state.read_onboarding_node(proj, org_id) is None

    def test_dry_run_no_write(self):
        import uuid
        org_id = f"bf5{uuid.uuid4().hex[:10]}"
        proj = _make_sdk(namespace=org_id)._get_proj()
        res = onboarding_state.backfill_org(proj, org_id, True, dry_run=True)
        assert res["action"] == "would-create-complete"
        assert onboarding_state.read_onboarding_node(proj, org_id) is None

    def test_wrapper_dry_run_and_apply(self):
        """graph-scripts wrapper: DRY-RUN default (no writes), --apply writes,
        re-run no-op — end to end on the registry lane."""
        import json as _json
        import subprocess
        import uuid

        # seed a grandfathered org: a Team node with legacy complete state
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK(namespace="registry")
        org_id = f"bfwrap{uuid.uuid4().hex[:8]}"
        sdk._get_registry().query(
            "CREATE (t:Team {id: $id, name: $name, graph_name: $gn, "
            "onboarding_state: $os})",
            params={"id": org_id, "name": f"bfw-{org_id[:6]}",
                    "gn": f"org_{org_id}",
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
            sdk._get_proj().db.select_graph(f"org_{org_id}"), org_id)
        assert node is None  # dry-run wrote nothing
        # APPLY
        r = subprocess.run([py, script, "--apply"],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        node = onboarding_state.read_onboarding_node(
            sdk._get_proj().db.select_graph(f"org_{org_id}"), org_id)
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
            sdk._get_proj().db.select_graph(f"org_{org_id}"), org_id)
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

    def test_recompute_config_write_keeps_grandfathering(self):
        """#3451: ``connection-written`` is a client-only trace, so the T7
        sweep must not count it as an AGENT step — a grandfathered org that
        filed only the config-write checkpoint must still be promoted to
        complete, not gate-evaluated (it has no decision and would stall).

        RED mutation: revert ``recompute_completion`` to ``s != "team-named"``
        → this org falls through to gate eval and returns "unchanged" instead
        of "complete-grandfathered".
        """
        import uuid
        t = f"rcw{uuid.uuid4().hex[:8]}"
        g = _make_sdk(namespace=t)._get_proj()
        onboarding_state.ensure_onboarding_state_node(g, t)
        onboarding_state.write_completed_step(g, t, "connection-written")
        assert onboarding_state.recompute_completion(g, t, True) == (
            "complete-grandfathered")
        assert onboarding_state.read_onboarding_node(g, t)["status"] == "complete"
        # a SERVER-OBSERVED act still ends the window (fail-closed)
        t2 = f"rcw{uuid.uuid4().hex[:8]}"
        g2 = _make_sdk(namespace=t2)._get_proj()
        onboarding_state.ensure_onboarding_state_node(g2, t2)
        onboarding_state.write_completed_step(g2, t2, "connection-written")
        onboarding_state.write_completed_step(g2, t2, "harness-connected")
        assert onboarding_state.recompute_completion(g2, t2, True) == "unchanged"
        assert onboarding_state.read_onboarding_node(g2, t2)["status"] == "active"


class TestCompletionWire:
    def test_poisoned_new_org_negative(self):
        """A new org's legacy flag is never trusted once the agent flow
        engages (node governs — the poisoned-TRUE the precedence kills).
        The flag is raw-written (post-W1 the PATCH surface accept-and-drops)."""
        tc, org_id = _registered_client()
        try:
            from tortoise.hosted_api import _get_onboarding_state as _raw
            from tortoise.hosted_api import _write_onboarding_state as _w
            st = _raw(org_id)
            st["onboarding_complete"] = True
            _w(org_id, st)
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "harness-connected"})
            st = tc.get("/v1/onboarding/state").json()["onboarding"]
            assert st["onboarding_complete"] is False  # edge → node governs
        finally:
            tc.__exit__(None, None, None)

    def test_node_complete_wire_true(self):
        tc, _org_id = _registered_client()
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
        org_id = f"gfwire{uuid.uuid4().hex[:8]}"
        proj = _make_sdk(namespace=org_id)._get_proj()
        # NO node — simulate by not creating it
        assert onboarding_state.read_onboarding_node(proj, org_id) is None
        st = _proj(org_id)
        assert st["onboarding_complete"] is False
        assert st["status"] == "active"

    def test_poisoned_false_guard(self):
        """The grandfathered-window guard (cycle-2 P1-1 fix): node present,
        active, ZERO agent edges, LEGACY jsonb true (raw-written — the PATCH
        surface accept-and-drops onboarding_complete post-W1) → wire TRUE (a
        legacy-wizard completer is never re-onboarded)."""
        tc, org_id = _registered_client()
        try:
            # seed the legacy jsonb flag via the RAW writer (the PATCH
            # surface accept-and-drops it on node-present orgs, #1997)
            from tortoise.hosted_api import _get_onboarding_state as _raw
            from tortoise.hosted_api import _write_onboarding_state as _w
            st = _raw(org_id)
            st["onboarding_complete"] = True
            _w(org_id, st)
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
        tc, org_id = _registered_client()
        try:
            # make this a grandfathered org: jsonb true (RAW-written — the
            # PATCH surface accept-and-drops onboarding_complete post-W1,
            # #1997), node PRESENT active with ZERO agent step edges (the
            # eager-init node)
            from tortoise.hosted_api import _get_onboarding_state as _raw
            from tortoise.hosted_api import _write_onboarding_state as _w
            st = _raw(org_id)
            st["onboarding_complete"] = True
            _w(org_id, st)
            st = tc.get("/v1/onboarding/state").json()["onboarding"]
            assert st["onboarding_complete"] is True
            assert st["status"] == "active"
            # now simulate a node-ABSENT grandfathered org (pre-backfill
            # window): drop the node, keep jsonb true, then first FLOW write.
            import tortoise.onboarding.state as _os2
            from tortoise.hosted_api import _make_sdk as _mk
            proj = _mk(namespace=org_id)._get_proj()
            _os2._run(proj,
                      f"MATCH (n:{_os2.ONBOARDING_NODE_LABEL} "
                      f"{{org_id: $oid}}) DETACH DELETE n",
                      {"oid": org_id})
            assert _os2.read_onboarding_node(proj, org_id) is None
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
        tc, org_id = _registered_client()
        try:
            # drop the node → node-absent grandfathered org
            import tortoise.onboarding.state as _os2
            from tortoise.hosted_api import _make_sdk as _mk
            proj = _mk(namespace=org_id)._get_proj()
            _os2._run(proj,
                      f"MATCH (n:{_os2.ONBOARDING_NODE_LABEL} "
                      f"{{org_id: $oid}}) DETACH DELETE n",
                      {"oid": org_id})
            assert _os2.read_onboarding_node(proj, org_id) is None
            r = tc.patch("/v1/onboarding/state",
                         json={"onboarding_complete": True})
            assert r.status_code == 200, r.text
            from tortoise.hosted_api import _get_onboarding_state as _raw
            assert _raw(org_id).get("onboarding_complete") is True
            # wire completes via the grandfathered window (no node, jsonb true)
            assert r.json()["onboarding"]["onboarding_complete"] is True
        finally:
            tc.__exit__(None, None, None)
