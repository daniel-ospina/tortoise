"""#2001 (W5) graph-held OnboardingState — docker-lane integration tests.

TestCheckpoint: the checkpoint endpoint contract — created/no-op signals,
unknown-step 422, team-named rejection, set-once fork/compact, server-owned
status 403, last-decide-attempt LWW, member-progress auth/shape/merge,
two-ops and extra-forbid 400s, self/build journey completion, restart-pending
both directions, cross-org isolation.

TestForkUnsureAt (#2407): the "not sure yet — decide later" contract — the
unsure record persists a server-stamped fork_unsure_at WITHOUT consuming the
set-once fork; a later explicit pick is a fresh fork write (never a 409);
completion NEVER auto-closes an unsure org as 'self'.

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


class TestCheckpoint:
    def test_created_and_noop_signals(self):
        tc, _org_id = _registered_client()
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
        tc, _org_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "bogus-step"})
            assert r.status_code == 422
        finally:
            tc.__exit__(None, None, None)

    def test_team_named_not_checkpointable(self):
        tc, _org_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "team-named"})
            assert r.status_code == 422
        finally:
            tc.__exit__(None, None, None)

    def test_fork_set_once_contract(self):
        tc, _org_id = _registered_client()
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
        tc, _org_id = _registered_client()
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
        tc, _org_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"status": "complete"})
            assert r.status_code == 403
        finally:
            tc.__exit__(None, None, None)

    def test_last_decide_attempt_lww_conditional(self):
        """Epic DM-1 contract: decide-completed (success) CLEARS the
        attempt to null; ANY later attempt write ('failed' OR 'dismissed')
        is SKIPPED once decide-completed exists (a completed decide never
        re-gains an attempt marker — dismissal alone never completes;
        failed never un-completes; retry reachability is pre-completion)."""
        tc, _org_id = _registered_client()
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
            # success clears the attempt (epic DM-1: success clears to null)
            assert r.json()["onboarding"]["last_decide_attempt"] is None
            r2 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"last_decide_attempt": "failed"})
            assert r2.status_code == 200
            # post-complete attempt writes are skipped — the field stays
            # cleared and the decide stays complete (no silent regression,
            # no data-loss: the old test pinned dismissed-survives which
            # contradicted the epic DM-1 success-clears-to-null pin).
            assert r2.json()["onboarding"]["last_decide_attempt"] is None
            assert r2.json()["onboarding"]["status"] == "complete"
            # 'dismissed' post-complete is equally skipped
            r3 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"last_decide_attempt": "dismissed"})
            assert r3.json()["onboarding"]["last_decide_attempt"] is None
            assert r3.json()["onboarding"]["status"] == "complete"
        finally:
            tc.__exit__(None, None, None)

    def test_member_progress_key_auth_non_uuid_403(self):
        """Key-authed (no session user) member_progress requires a UUID
        user_id (no cross-user forgery)."""
        tc, _org_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"member_progress": {"not-a-uuid": []}})
            assert r.status_code == 403
        finally:
            tc.__exit__(None, None, None)

    def test_last_decide_attempt_invalid_422(self):
        """Invalid last_decide_attempt values are client errors (422), not
        500s — sibling ops validate at the boundary."""
        tc, _org_id = _registered_client()
        try:
            for bad in ("postponed", "completed", 42, ""):
                r = tc.post("/v1/onboarding/state/checkpoint",
                            json={"last_decide_attempt": bad})
                assert r.status_code == 422, (bad, r.status_code)
        finally:
            tc.__exit__(None, None, None)

    def test_member_progress_valid_steps(self):
        import uuid
        tc, _org_id = _registered_client()
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
        tc, _org_id = _registered_client()
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
        tc, _org_id = _registered_client()
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
        tc, _org_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "capture-disclosed", "fork": "self"})
            assert r.status_code == 400
        finally:
            tc.__exit__(None, None, None)

    def test_extra_forbid(self):
        tc, _org_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "capture-disclosed", "bogus": 1})
            assert r.status_code == 422
        finally:
            tc.__exit__(None, None, None)

    def test_self_journey_gate_completes(self):
        """Full self-fork journey → gate eval → status complete + wire true
        (monotonic: complete can never regress)."""
        tc, _org_id = _registered_client()
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

    def test_build_journey_completes_on_the_two_observed_acts(self):
        tc, _org_id = _registered_client()
        try:
            tc.post("/v1/onboarding/state/checkpoint", json={"fork": "build"})
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "harness-connected"})
            # #3913: ONE observed act is not enough (fail-closed)
            r0 = tc.get("/v1/onboarding/state")
            assert r0.json()["onboarding"]["status"] == "active"
            # decide does NOT affect a build org — asserted while the org is
            # still ACTIVE (a `complete` assert after first-points-filed
            # completed it is a tautology).
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "decide-completed"})
            assert r.json()["onboarding"]["status"] == "active"
            # the second observed act completes it — no catalog needed
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "first-points-filed"})
            r = tc.get("/v1/onboarding/state")
            assert r.json()["onboarding"]["status"] == "complete"
            # catalog-presented stays accepted (optional record, never a gate)
            r2 = tc.patch("/v1/onboarding/state",
                          json={"catalog_presented": True})
            assert r2.json()["onboarding"]["status"] == "complete"
        finally:
            tc.__exit__(None, None, None)

    def test_restart_pending_is_served_on_the_wire_both_directions(self):
        """#3451: the DERIVED restart-pending condition must reach the WIRE.

        The projection is nested under ``onboarding`` behind
        ``response_model=OnboardingStateResponse``, so a response-model change
        could silently drop the key while every function-level pin stayed
        green. This EXECUTES the real GET route end to end.

        Both directions are load-bearing:
        - a fresh org (no config written) → the wire says ``False``. An
          abandoned install must never read as waiting for a restart, and a
          MISSING key is a failure too (``is False``, never truthiness).
        - after the agent records ``connection-written`` → ``True``.
        - after ``harness-connected`` is verified → back to ``False``.
        """
        tc, _org_id = _registered_client()
        try:
            def _wire():
                r = tc.get("/v1/onboarding/state")
                assert r.status_code == 200, r.text
                return r.json()["onboarding"]

            fresh = _wire()
            assert "restart_pending" in fresh, fresh
            assert fresh["restart_pending"] is False, fresh

            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"step": "connection-written"})
            assert r.status_code == 200, r.text
            assert _wire()["restart_pending"] is True

            tc.post("/v1/onboarding/state/checkpoint",
                    json={"step": "harness-connected"})
            assert _wire()["restart_pending"] is False
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
            team_b = r.json()["org_id"]
            assert "harness-connected" in _proj(team_b)["completed_steps"]
        finally:
            tc.__exit__(None, None, None)


class TestForkUnsureAt:
    """#2407 "not sure yet — decide later" (Data Model 1) endpoint contract.

    Pins: the unsure record persists a server-stamped fork_unsure_at WITHOUT
    consuming the set-once fork (fork stays None → the fork card keeps asking);
    a later explicit self/build pick is a fresh fork write (200, never a 409);
    completion NEVER auto-closes an unsure org as 'self' (the full self
    checklist leaves it active until the fork is answered); repeat unsure
    picks re-stamp (200); an unsure record after a fork is a 409 (never
    asked / already answered).
    """

    def test_unsure_records_signal_fork_stays_unset(self):
        tc, org_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"fork_unsure_at": True})
            assert r.status_code == 200, r.text
            body = r.json()["onboarding"]
            assert body["fork"] is None  # set-once fork NOT consumed
            at = body["fork_unsure_at"]
            assert isinstance(at, str) and "T" in at  # server-stamped ISO
            node = _read_node(org_id)
            assert node["fork_unsure_at"] == at
            assert "fork" not in node or node["fork"] is None
        finally:
            tc.__exit__(None, None, None)

    def test_unsure_repeat_re_stamps_never_409(self):
        tc, _org_id = _registered_client()
        try:
            r1 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"fork_unsure_at": True})
            assert r1.status_code == 200, r1.text
            r2 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"fork_unsure_at": True})
            assert r2.status_code == 200, r2.text
            body = r2.json()["onboarding"]
            assert body["fork"] is None
            assert isinstance(body["fork_unsure_at"], str)
        finally:
            tc.__exit__(None, None, None)

    def test_later_explicit_fork_no_409_and_clears_marker(self):
        """Indicator-4 pin: an unsure org answers the fork LATER — the pick is
        a fresh set-once write (200, never a 409) and clears the marker."""
        tc, _org_id = _registered_client()
        try:
            r0 = tc.post("/v1/onboarding/state/checkpoint",
                         json={"fork_unsure_at": True})
            assert r0.status_code == 200, r0.text
            for fork in ("self", "build"):
                tc2, org_id2 = _registered_client()
                try:
                    tc2.post("/v1/onboarding/state/checkpoint",
                             json={"fork_unsure_at": True})
                    r = tc2.post("/v1/onboarding/state/checkpoint",
                                 json={"fork": fork})
                    assert r.status_code == 200, r.text
                    body = r.json()["onboarding"]
                    assert body["fork"] == fork
                    assert body["fork_unsure_at"] is None  # cleared on fork-set
                    node = _read_node(org_id2)
                    assert "fork_unsure_at" not in node or \
                        node["fork_unsure_at"] is None
                finally:
                    tc2.__exit__(None, None, None)
        finally:
            tc.__exit__(None, None, None)

    def test_fork_unsure_is_never_a_fork_value(self):
        """Model-1 pin: the fork write path rejects 'unsure' (422) — the
        deferral lives in fork_unsure_at, never in the fork value."""
        tc, _org_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"fork": "unsure"})
            assert r.status_code == 422
        finally:
            tc.__exit__(None, None, None)

    def test_unsure_false_422(self):
        """fork_unsure_at is a MARKER op — only true is meaningful; a false
        body is a client error (422), never a silent no-op."""
        tc, _org_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"fork_unsure_at": False})
            assert r.status_code == 422
        finally:
            tc.__exit__(None, None, None)

    def test_unsure_with_fork_is_two_ops_400(self):
        tc, _org_id = _registered_client()
        try:
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"fork_unsure_at": True, "fork": "self"})
            assert r.status_code == 400
        finally:
            tc.__exit__(None, None, None)

    def test_unsure_after_fork_set_is_conflict_409(self):
        """An unsure record after the org already answered is contradictory
        (the fork card no longer renders) → 409 fork_already_set."""
        tc, _org_id = _registered_client()
        try:
            tc.post("/v1/onboarding/state/checkpoint", json={"fork": "build"})
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"fork_unsure_at": True})
            assert r.status_code == 409
        finally:
            tc.__exit__(None, None, None)

    def test_fork_set_once_still_applies_after_unsure(self):
        """Set-once semantics resume once a fork lands: a changed fork after
        an unsure-then-self sequence is still a 409."""
        tc, _org_id = _registered_client()
        try:
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"fork_unsure_at": True})
            tc.post("/v1/onboarding/state/checkpoint", json={"fork": "self"})
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"fork": "build"})
            assert r.status_code == 409
        finally:
            tc.__exit__(None, None, None)

    def test_unsure_full_self_checklist_never_auto_closes(self):
        """Indicator-3 pin: an unsure org completing the ENTIRE self
        checklist stays active — onboarding must NOT auto-close it as 'self'
        while the fork question is open (the Setup-guide fork row stays)."""
        tc, _org_id = _registered_client()
        try:
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"fork_unsure_at": True})
            for step in ("harness-connected", "first-points-filed",
                         "decide-completed"):
                tc.post("/v1/onboarding/state/checkpoint", json={"step": step})
            st = tc.get("/v1/onboarding/state").json()["onboarding"]
            assert st["fork"] is None
            assert st["status"] == "active"  # never auto-closed as self
            assert st["onboarding_complete"] is False
        finally:
            tc.__exit__(None, None, None)

    def test_unsure_then_self_answering_later_completes(self):
        """Answering the fork LATER (after the self steps already landed)
        unlocks completion — the explicit pick is a fork write (200) that
        clears the marker and evals the (now evaluable) self gate."""
        tc, _org_id = _registered_client()
        try:
            tc.post("/v1/onboarding/state/checkpoint",
                    json={"fork_unsure_at": True})
            for step in ("harness-connected", "first-points-filed",
                         "decide-completed"):
                tc.post("/v1/onboarding/state/checkpoint", json={"step": step})
            r = tc.post("/v1/onboarding/state/checkpoint",
                        json={"fork": "self"})
            assert r.status_code == 200, r.text
            body = r.json()["onboarding"]
            assert body["fork"] == "self"
            assert body["fork_unsure_at"] is None
            assert body["status"] == "complete"
            assert body["onboarding_complete"] is True
        finally:
            tc.__exit__(None, None, None)

    def test_patch_fork_unsure_at_is_server_owned_403(self):
        """fork_unsure_at is a checkpoint-only (server-stamped) FLOW key — a
        stray PATCH is rejected loudly, never silently dropped."""
        tc, _org_id = _registered_client()
        try:
            r = tc.patch("/v1/onboarding/state",
                         json={"fork_unsure_at": "2026-01-01T00:00:00+00:00"})
            assert r.status_code == 403
        finally:
            tc.__exit__(None, None, None)

    def test_writer_conflict_on_compact_and_fork_set(self):
        """Graph-level writer contract: 'conflict' when the org is compact
        (never asked the fork card) or already forked; 'recorded' otherwise;
        the marker never lands on a forked/compact node."""
        import uuid

        from tortoise.hosted_api import _make_sdk as _ms
        # compact org → conflict (compact orgs never see the fork card)
        tc = f"fu-compact-{uuid.uuid4().hex[:8]}"
        gc = _ms(namespace=tc)._get_proj()
        onboarding_state.ensure_onboarding_state_node(gc, tc, compact=True)
        assert onboarding_state.write_fork_unsure_at(
            gc, tc, "2026-01-01T00:00:00+00:00") == "conflict"
        assert onboarding_state.read_onboarding_node(gc, tc).get(
            "fork_unsure_at") is None
        # forked org → conflict
        tf = f"fu-forked-{uuid.uuid4().hex[:8]}"
        gf = _ms(namespace=tf)._get_proj()
        onboarding_state.write_fork(gf, tf, "self")
        assert onboarding_state.write_fork_unsure_at(
            gf, tf, "2026-01-01T00:00:00+00:00") == "conflict"
        # fresh org → recorded
        tr = f"fu-fresh-{uuid.uuid4().hex[:8]}"
        gr = _ms(namespace=tr)._get_proj()
        assert onboarding_state.write_fork_unsure_at(
            gr, tr, "2026-01-01T00:00:00+00:00") == "recorded"
        assert onboarding_state.read_onboarding_node(gr, tr).get(
            "fork_unsure_at") == "2026-01-01T00:00:00+00:00"
        # invalid timestamp rejected (never written)
        try:
            onboarding_state.write_fork_unsure_at(gr, tr, "")
            raise AssertionError("empty timestamp must be rejected")
        except ValueError:
            pass

    def test_recompute_unsure_org_stays_active(self):
        """T7 recompute sweep: an unsure org with the full self checklist is
        NOT gate-promoted (complete must not regress, but the gate must also
        refuse to promote on the self default while the fork is open)."""
        import uuid

        from tortoise.hosted_api import _make_sdk as _ms
        tid = f"fu-rc-{uuid.uuid4().hex[:8]}"
        g = _ms(namespace=tid)._get_proj()
        onboarding_state.ensure_onboarding_state_node(g, tid)
        onboarding_state.write_fork_unsure_at(g, tid, "2026-01-01T00:00:00+00:00")
        for step in ("harness-connected", "first-points-filed",
                     "decide-completed"):
            onboarding_state.write_completed_step(g, tid, step)
        assert onboarding_state.recompute_completion(
            g, tid, False) == "unchanged"
        assert onboarding_state.read_onboarding_node(g, tid)["status"] == "active"
