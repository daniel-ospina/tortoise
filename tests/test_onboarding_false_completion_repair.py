"""#3912 — false `decide-completed` completions: repair + guard.

#3784 made the onboarding writer fail-closed, but an org that received the
edge from the old auto-complete (`_maybe_onboarding_auto_complete` filed
`decide-completed` on ANY successful point write and wrote `status = complete`
directly) keeps both facts forever: `COMPLETED_STEP` edges are
first-write-wins and `status` is monotonic. These tests pin the remediation
path and — above all — that it NEVER touches a TRUE completion.

Runs in the docker lane (TORTOISE_DB_URI): the assertions exercise the real
keyed-MERGE / DELETE Cypher. URI-less legs SKIP at module level (same
convention as test_onboarding_state_split.py).
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY",
                      "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")

import pytest

from tortoise.config import is_db_uri as _is_db_uri

if not _is_db_uri(os.environ.get("TORTOISE_DB_URI")):
    pytest.skip("docker-lane #3912 repair tests require TORTOISE_DB_URI "
                "(tier-2 embedded legs skip)", allow_module_level=True)

from tortoise.hosted_api import _make_sdk
from tortoise.onboarding import state as _os


def _new_org() -> str:
    return f"t3912-{uuid.uuid4().hex[:10]}"


def _sdk(org_id: str):
    return _make_sdk(namespace=org_id)


def _seed(org_id: str, *, steps: tuple[str, ...] = (
        "team-named", "harness-connected", "first-points-filed",
        "decide-completed"), status: str = _os.STATUS_COMPLETE,
        fork: str | None = None, compact: bool = False):
    """An org whose OnboardingState holds the given steps + status."""
    sdk = _sdk(org_id)
    proj = sdk._get_proj()
    _os.ensure_onboarding_state_node(proj, org_id, fork=fork, compact=compact)
    for step in steps:
        _os.write_completed_step(proj, org_id, step)
    if status:
        # Seed the server-owned status directly (write_status is monotonic;
        # the seed needs the pre-repair state).
        proj.query(
            f"MATCH (n:{_os.ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
            "SET n.status = $status",
            org_id=org_id, status=status)
    return sdk, proj


# ── decision evidence (the falseness signal) ──────────────────

class TestDecisionEvidence:
    def test_plain_statement_is_not_decision_evidence(self):
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("statement", "a first note, no decision")
        ev = _os.decision_evidence(proj, org)
        assert ev["decision_evidenced"] is False
        assert ev["decision_points"] == 0

    def test_decision_point_is_evidence(self):
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("decision", "chose JSON")
        ev = _os.decision_evidence(proj, org)
        assert ev["decision_evidenced"] is True

    def test_ep_decide_shape_is_evidence_without_a_decision_point(self):
        """#3916: `skills/tortoise-decide` writes option/criterion/evidence
        points and NO `decision` point. A repair gate that only looked for
        `decision` would classify that real decide as FALSE and delete it."""
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("option", "Option A")
        sdk.create_point("criterion", "adoption cost")
        sdk.create_point("evidence", "benchmark result")
        ev = _os.decision_evidence(proj, org)
        assert ev["decision_points"] == 3
        assert ev["decision_evidenced"] is True


# ── the falseness verdict ─────────────────────────────────────

class TestFindFalse:
    def test_false_when_edge_has_no_decision_behind_it(self):
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("statement", "the only point in the whole graph")
        finding = _os.find_false_decide_completion(proj, org)
        assert finding["verdict"] == "false"
        assert finding["false"] is True
        assert finding["completed_steps"] == [
            "decide-completed", "first-points-filed", "harness-connected",
            "team-named"]

    def test_true_when_a_decision_exists(self):
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("decision", "chose JSON")
        assert _os.find_false_decide_completion(proj, org)["verdict"] == "true"

    def test_no_edge_is_not_a_repair_target(self):
        org = _new_org()
        _seed(org, steps=("team-named", "harness-connected"),
              status=_os.STATUS_ACTIVE)
        assert _os.find_false_decide_completion(
            _sdk(org)._get_proj(), org)["verdict"] == "no-edge"

    def test_absent_node_is_not_a_repair_target(self):
        org = _new_org()
        assert _os.find_false_decide_completion(
            _sdk(org)._get_proj(), org)["verdict"] == "absent"


# ── the repair ────────────────────────────────────────────────

class TestRepair:
    def test_dry_run_is_read_only(self):
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("statement", "no decision")
        res = _os.repair_false_decide_completion(proj, org, apply=False)
        assert res["action"] == "would-repair"
        assert "decide-completed" in _os.completed_steps(proj, org)
        assert _os.read_onboarding_node(proj, org)["status"] == _os.STATUS_COMPLETE

    def test_repair_removes_the_false_edge_and_regresses_the_status(self):
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("statement", "no decision")
        res = _os.repair_false_decide_completion(proj, org, apply=True)
        assert res["action"] == "repaired"
        assert res["edge_removed"] is True
        assert res["status_regressed"] is True
        assert res["still_complete_without_the_edge"] is False
        steps = _os.completed_steps(proj, org)
        assert "decide-completed" not in steps
        # the org keeps the steps it really earned
        assert {"team-named", "harness-connected", "first-points-filed"} <= set(steps)
        node = _os.read_onboarding_node(proj, org)
        assert node["status"] == _os.STATUS_ACTIVE
        # the served verdict is now honest
        assert _os.resolve_wire_completion(
            node["status"], False, steps) is False

    def test_repair_is_inspectable_on_the_node(self):
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("statement", "no decision")
        _os.repair_false_decide_completion(
            proj, org, apply=True, at="2026-01-01T00:00:00Z")
        node = _os.read_onboarding_node(proj, org)
        assert node["decide_completed_removed_at"] == "2026-01-01T00:00:00Z"
        assert node["decide_completed_removed_reason"] == _os.REPAIR_REASON_FALSE_DECIDE
        assert node["status_regressed_at"] == "2026-01-01T00:00:00Z"
        assert node["status_regressed_from"] == _os.STATUS_COMPLETE

    def test_repair_prunes_the_orphaned_step_node(self):
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("statement", "no decision")
        _os.repair_false_decide_completion(proj, org, apply=True)
        rows = proj.query(
            f"MATCH (s:{_os.ONBOARDING_STEP_LABEL} "
            "{org_id: $org_id, step_id: 'decide-completed'}) RETURN count(s)",
            org_id=org).result_set
        assert rows[0][0] == 0

    def test_repair_is_idempotent(self):
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("statement", "no decision")
        assert _os.repair_false_decide_completion(
            proj, org, apply=True)["action"] == "repaired"
        assert _os.repair_false_decide_completion(
            proj, org, apply=True)["action"] == "skipped-no-edge"

    def test_zero_agent_step_org_is_left_alone(self):
        """A zero-AGENT-step org is left alone (the grandfathered branch may
        still grant it completion — unknown jsonb is fail-closed)."""
        org = _new_org()
        _seed(org, steps=("team-named",), status=_os.STATUS_COMPLETE)
        proj = _sdk(org)._get_proj()
        assert _os.find_false_decide_completion(proj, org)["verdict"] == \
            "no-edge"
        res = _os.repair_false_decide_completion(proj, org, apply=True)
        assert res["action"] == "skipped-no-edge"
        assert _os.read_onboarding_node(proj, org)["status"] == _os.STATUS_COMPLETE

    def test_missing_compact_without_the_compact_gate_is_unconfirmable(self):
        """A missing `compact` only excuses a completion the COMPACT gate could
        have granted. With the compact gate unsatisfied too, the absent flag
        explains nothing — the node must be RED, not silently GREEN."""
        org = _new_org()
        _seed(org, steps=("team-named", "harness-connected"),
              status=_os.STATUS_COMPLETE)
        proj = _sdk(org)._get_proj()
        proj.query(
            f"MATCH (n:{_os.ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
            "REMOVE n.compact", org_id=org)
        assert _os.find_false_decide_completion(
            proj, org)["verdict"] == "unconfirmable"

    def test_no_edge_with_decision_evidence_is_not_unconfirmable(self):
        """An org that really decided but lost the edge must NOT sit RED
        forever — its own evidence proves the completion."""
        org = _new_org()
        sdk, proj = _seed(org, steps=("team-named", "harness-connected",
                                      "first-points-filed"),
                          status=_os.STATUS_COMPLETE)
        sdk.create_point("decision", "chose JSON")
        assert _os.find_false_decide_completion(proj, org)["verdict"] == \
            "no-edge"

    def test_missing_compact_is_never_a_repair_target(self):
        """`write_fork`/`write_fork_unsure_at` create the node WITHOUT
        `compact`; the read gate defaults it to false, but the repair must not
        turn that read default into a regression."""
        org = _new_org()
        _seed(org, steps=("team-named", "harness-connected",
                          "first-points-filed"), status=_os.STATUS_COMPLETE)
        proj = _sdk(org)._get_proj()
        proj.query(
            f"MATCH (n:{_os.ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
            "REMOVE n.compact", org_id=org)
        assert _os.find_false_decide_completion(
            proj, org)["verdict"] == "no-edge"

    def test_missing_compact_repair_removes_the_edge_but_never_regresses(self):
        """The REPAIR-side `_compact_unknown` term: a missing-compact node with
        a FALSE decide edge still gets the edge removed, but must never have
        its status regressed (it may be a genuinely compact org)."""
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("statement", "no decision")
        proj.query(
            f"MATCH (n:{_os.ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
            "REMOVE n.compact", org_id=org)
        res = _os.repair_false_decide_completion(proj, org, apply=True)
        assert res["action"] == "repaired"
        assert res["edge_removed"] is True
        assert res["status_regressed"] is False
        assert _os.read_onboarding_node(
            proj, org)["status"] == _os.STATUS_COMPLETE
        assert _os.find_false_decide_completion(
            proj, org)["verdict"] == "no-edge"

    def test_unattributable_status_is_unconfirmable_not_repaired(self):
        """Status complete, agent steps present, no decide edge, no repair
        stamp, gate unsatisfied — a lost edge we did not cause. Reported, never
        silently repaired, and never silently green."""
        org = _new_org()
        _seed(org, steps=("team-named", "harness-connected",
                          "first-points-filed"), status=_os.STATUS_COMPLETE)
        proj = _sdk(org)._get_proj()
        assert _os.find_false_decide_completion(proj, org)["verdict"] == \
            "unconfirmable"
        assert _os.repair_false_decide_completion(
            proj, org, apply=True)["action"] == "skipped-unconfirmable"
        assert _os.read_onboarding_node(proj, org)["status"] == _os.STATUS_COMPLETE

    def test_compact_org_keeps_its_earned_status(self):
        """`decide-completed` is NOT in the compact gate, and the pre-#3784
        writer filed it on EVERY org — so a legitimately complete compact org
        carries a spurious decide edge. Removing the edge is right; regressing
        its status is not (the org is genuinely complete)."""
        org = _new_org()
        sdk, proj = _seed(org, compact=True)
        sdk.create_point("statement", "no decision")
        res = _os.repair_false_decide_completion(proj, org, apply=True)
        assert res["action"] == "repaired"
        assert res["edge_removed"] is True
        assert res["still_complete_without_the_edge"] is True
        assert res["status_regressed"] is False
        assert "decide-completed" not in _os.completed_steps(proj, org)
        assert _os.read_onboarding_node(
            proj, org)["status"] == _os.STATUS_COMPLETE

    def test_build_org_keeps_its_earned_status(self):
        """The build gate needs the two observed acts, never decide-completed
        (#3913) — so removing the spurious decide edge cannot regress it."""
        org = _new_org()
        sdk, proj = _seed(org, fork="build", steps=(
            "team-named", "harness-connected", "first-points-filed",
            "catalog-presented", "decide-completed"))
        sdk.create_point("statement", "no decision")
        res = _os.repair_false_decide_completion(proj, org, apply=True)
        assert res["still_complete_without_the_edge"] is True
        assert res["status_regressed"] is False
        assert _os.read_onboarding_node(
            proj, org)["status"] == _os.STATUS_COMPLETE

    def test_grandfathered_org_keeps_its_status(self):
        """`resolve_wire_completion` grants completion to a node with zero
        AGENT step edges + legacy jsonb true. Unknown jsonb is fail-closed."""
        org = _new_org()
        sdk, proj = _seed(org, steps=("team-named", "decide-completed"))
        sdk.create_point("statement", "no decision")
        res = _os.repair_false_decide_completion(proj, org, apply=True)
        assert res["still_complete_without_the_edge"] is True
        assert res["status_regressed"] is False
        assert _os.read_onboarding_node(
            proj, org)["status"] == _os.STATUS_COMPLETE

    def test_known_non_grandfathered_org_is_regressed(self):
        org = _new_org()
        sdk, proj = _seed(org, steps=("team-named", "decide-completed"))
        sdk.create_point("statement", "no decision")
        res = _os.repair_false_decide_completion(
            proj, org, apply=True, legacy_complete=False)
        assert res["status_regressed"] is True
        assert _os.read_onboarding_node(proj, org)["status"] == _os.STATUS_ACTIVE


class TestHalfRepairIsRetrySafe:
    """A crash between the edge removal and the status regression must leave a
    state the guard SEES and a re-run FINISHES — otherwise the org is served
    complete with no edge left to point at and `--check` reports GREEN."""

    @staticmethod
    def _half_repair(org, proj):
        _os.remove_decide_completed_edge(
            proj, org, reason=_os.REPAIR_REASON_FALSE_DECIDE)

    def test_half_repaired_state_is_still_reported_false(self):
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("statement", "no decision")
        self._half_repair(org, proj)
        finding = _os.find_false_decide_completion(proj, org)
        assert finding["verdict"] == "false", (
            "a half-repaired org was reported no-edge — the guard would go "
            "GREEN over an org still served complete")
        assert finding["half_repaired"] is True
        assert finding["has_decide_edge"] is False

    def test_rerunning_a_half_repair_finishes_it(self):
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("statement", "no decision")
        self._half_repair(org, proj)
        res = _os.repair_false_decide_completion(proj, org, apply=True)
        assert res["action"] == "repaired"
        assert res["edge_removed"] is False  # already gone
        assert res["status_regressed"] is True
        assert _os.read_onboarding_node(proj, org)["status"] == _os.STATUS_ACTIVE

    def test_an_unstamped_deleted_edge_is_reported_unconfirmable(self):
        """The DELETE→stamp sub-window: an edge deleted WITHOUT the stamp must
        still be a repair target, or the org is served complete with no edge
        and the guard reports GREEN. The detector is gate-based, not
        stamp-based. (The stamp is written ahead of the delete so our own
        writes cannot open this window; this pins that an interrupted or
        hostile delete is still caught.)"""
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("statement", "no decision")
        proj.query(
            f"MATCH (n:{_os.ONBOARDING_NODE_LABEL} {{org_id: $org_id}})"
            f"-[e:{_os.COMPLETED_STEP_EDGE}]->"
            f"(s:{_os.ONBOARDING_STEP_LABEL} "
            "{org_id: $org_id, step_id: 'decide-completed'}) DELETE e",
            org_id=org)
        finding = _os.find_false_decide_completion(proj, org)
        assert finding["verdict"] == "unconfirmable"
        assert finding["half_repaired"] is False  # no stamp — not our repair
        # never silently repaired, and never silently green
        res = _os.repair_false_decide_completion(proj, org, apply=True)
        assert res["action"] == "skipped-unconfirmable"
        assert _os.read_onboarding_node(proj, org)["status"] == _os.STATUS_COMPLETE

    def test_stamp_precedes_the_delete(self):
        """Write-ahead: the stamp lands BEFORE the DELETE. Fault-injected —
        asserting the end state would pass with either order."""
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("statement", "no decision")

        class _DeleteBoom:
            """Proxy that blows up on the DELETE, after the stamp."""

            def __init__(self, inner):
                self._inner = inner

            def query(self, cypher, **params):
                if "DELETE e" in cypher:
                    raise RuntimeError("injected: crash before the delete")
                return self._inner.query(cypher, **params)

        with pytest.raises(RuntimeError):
            _os.remove_decide_completed_edge(
                _DeleteBoom(proj), org, reason="r", at="T")
        # the stamp is already there; the edge is not yet gone
        node = _os.read_onboarding_node(proj, org)
        assert node["decide_completed_removed_at"] == "T"
        assert "decide-completed" in _os.completed_steps(proj, org)
        # and the org is still a repair target, so the retry finishes it
        assert _os.find_false_decide_completion(
            proj, org)["verdict"] == "false"
        res = _os.repair_false_decide_completion(proj, org, apply=True)
        assert res["action"] == "repaired"
        assert "decide-completed" not in _os.completed_steps(proj, org)

    def test_retry_finishes_an_interrupted_prune(self):
        """A crash between the DELETE and the step-node prune leaves the
        orphan node; the retry must clean it up."""
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("statement", "no decision")
        _os.remove_decide_completed_edge(proj, org, reason="r")
        _os.ensure_onboarding_state_node(proj, org)
        # recreate the orphan deterministically
        proj.query(
            f"MERGE (s:{_os.ONBOARDING_STEP_LABEL} "
            "{org_id: $org_id, step_id: 'decide-completed'})", org_id=org)
        _os.repair_false_decide_completion(proj, org, apply=True)
        rows = proj.query(
            f"MATCH (s:{_os.ONBOARDING_STEP_LABEL} "
            "{org_id: $org_id, step_id: 'decide-completed'}) RETURN count(s)",
            org_id=org).result_set
        assert rows[0][0] == 0

    def test_a_gate_satisfied_half_repair_is_not_reported_false(self):
        """A legitimate compact completion must not stay a permanent guard-red."""
        org = _new_org()
        _seed(org, compact=True)
        proj = _sdk(org)._get_proj()
        self._half_repair(org, proj)
        assert _os.find_false_decide_completion(
            proj, org)["verdict"] == "no-edge"

    def test_grandfathered_half_repair_converges(self):
        """Guard and repair must AGREE on the grandfathered class: the repair
        deliberately leaves the status (jsonb may still grant completion), so
        the guard must not keep reporting it as a repair target forever."""
        org = _new_org()
        sdk, proj = _seed(org, steps=("team-named", "decide-completed"))
        sdk.create_point("statement", "no decision")
        res = _os.repair_false_decide_completion(proj, org, apply=True)
        assert res["still_complete_without_the_edge"] is True
        assert res["status_regressed"] is False
        after = _os.find_false_decide_completion(proj, org)
        assert after["verdict"] == "no-edge", (
            "guard and repair disagree — --check can never go green here")
        assert after["half_repaired"] is False


# ── a TRUE completion is untouchable ──────────────────────────

class TestTrueCompletionUntouched:
    @pytest.mark.parametrize("evidence_kind", [
        "decision", "humanApproval", "option", "criterion", "evidence"])
    def test_true_completion_is_never_repaired(self, evidence_kind):
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point(evidence_kind, "evidence of a real decision")
        res = _os.repair_false_decide_completion(proj, org, apply=True)
        assert res["action"] == "skipped-true", res
        assert "decide-completed" in _os.completed_steps(proj, org)
        node = _os.read_onboarding_node(proj, org)
        assert node["status"] == _os.STATUS_COMPLETE
        assert "decide_completed_removed_at" not in node
        assert "status_regressed_at" not in node

    def test_ep_decide_org_is_untouched(self):
        """The #3916 shape — a real `tortoise-decide` run — is NOT repaired."""
        org = _new_org()
        sdk, proj = _seed(org)
        sdk.create_point("option", "Option A")
        sdk.create_point("criterion", "adoption cost")
        res = _os.repair_false_decide_completion(proj, org, apply=True)
        assert res["action"] == "skipped-true"
        assert "decide-completed" in _os.completed_steps(proj, org)


# ── the monotonic guard is unchanged ──────────────────────────

class TestMonotonicityPreserved:
    def test_write_status_still_cannot_regress(self):
        """The repair is a SEPARATE audited path; the normal writer keeps its
        monotonic contract (a grandfathered org is never re-onboarded)."""
        org = _new_org()
        _sdk, proj = _seed(org)
        _os.write_status(proj, org, _os.STATUS_ACTIVE)
        assert _os.read_onboarding_node(proj, org)["status"] == _os.STATUS_COMPLETE

    def test_regress_status_refuses_any_target_but_active(self):
        org = _new_org()
        _sdk, proj = _seed(org)
        with pytest.raises(ValueError):
            _os.regress_status(proj, org, reason="x", to=_os.STATUS_COMPLETE)

    def test_regress_status_is_a_noop_when_not_complete(self):
        org = _new_org()
        _seed(org, steps=("team-named",), status=_os.STATUS_ACTIVE)
        assert _os.regress_status(
            _sdk(org)._get_proj(), org, reason="x") is False

    def test_regress_status_requires_the_repair_stamp(self):
        """Reachable only behind a removal — not a free-standing status reset."""
        org = _new_org()
        _sdk, proj = _seed(org)  # complete, but never repaired
        assert _os.regress_status(proj, org, reason="x") is False
        assert _os.read_onboarding_node(
            proj, org)["status"] == _os.STATUS_COMPLETE


# ── the store-wide guard (pure parts) ─────────────────────────

class TestGuardHelpers:
    def _guard(self):
        import importlib.util
        from pathlib import Path
        path = (Path(__file__).resolve().parent.parent / "graph-scripts"
                / "repair_false_onboarding_completion.py")
        spec = importlib.util.spec_from_file_location("_repair3912", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_read_target_resolution(self):
        g = self._guard()
        listed = {"org_a", "team_a", "team_b"}
        assert g._read_target("org_a", listed) == "org_a"
        assert g._read_target("team_a", listed) == "org_a"
        assert g._read_target("team_b", listed) == "team_b"
        # neither tenant name listed (registry/maintenance graphs) → no target
        assert g._read_target("registry_x", listed) is None

    def test_read_target_never_resolves_a_scoped_graph(self):
        """A scoped name (`org_<tid>_<gid>`) is never a read target. Treating
        it as one put it in `known` and excused the scoped graph from the
        `_has_scoped_graphs` skip — so its own false edge would be repaired."""
        g = self._guard()
        listed = {"org_a", "org_a_g1", "team_a"}
        assert g._read_target("org_a_g1", listed) is None
        # and the org is therefore skipped, not repaired
        assert g._has_scoped_graphs("a", listed, None) is True

    def test_remote_uri_detection(self):
        g = self._guard()
        assert g._is_remote("docker://:falkordb@localhost:6379/x") is False
        assert g._is_remote("redis://127.0.0.1:6379") is False
        assert g._is_remote("rediss://u:p@host.example.com:50317") is True
        assert g._is_remote("") is False

    def test_scoped_graph_detection(self):
        """An org with a non-default graph is SKIPPED, never repaired: a
        decision filed through a graph-scoped key lives outside the read
        target, so the evidence scan would call it FALSE and delete a real
        completion. Legacy (`team_`) and custom namespaces are covered."""
        g = self._guard()
        listed = {"org_aaa", "org_aaa_g1", "org_bbb",
                  "team_ccc", "team_ccc_g1", "custom_ns_ccc_v2"}
        assert g._has_scoped_graphs("aaa", listed, "org_aaa") is True
        assert g._has_scoped_graphs("bbb", listed, "org_bbb") is False
        assert g._has_scoped_graphs("ccc", listed, "team_ccc") is True
        # the org's own tenant graph is not "scoped"
        assert g._has_scoped_graphs(
            "aaa", {"org_aaa", "team_aaa"}, "org_aaa") is False

    def test_registry_cross_check_keys_on_namespace_not_display_name(self):
        """`graph_list` rows carry the DB graph name in `namespace`; the
        default graph's `name` is the display string "default". Keying on
        `name` made EVERY normally-provisioned org look scoped, turning
        `--apply` into a silent no-op. No registry node is created for the
        org in the raw graph list here, so this exercises the registry path."""
        g = self._guard()
        org = _new_org()
        listed = {f"org_{org}"}  # only the default graph exists
        reg = _make_sdk(namespace="registry")
        reg._get_registry().query(
            "MERGE (x:Graph {org_id: $tid, graph_id: 'default'}) "
            "SET x.name = 'default', x.namespace = $ns, x.kind = 'default'",
            params={"tid": org, "ns": f"org_{org}"})
        # a default graph is NOT scoped — keying on `name` would say it is
        assert g._has_scoped_graphs(org, listed, f"org_{org}") is False
        # a custom graph IS scoped, even when its DB name is not in the raw list
        reg._get_registry().query(
            "MERGE (x:Graph {org_id: $tid, graph_id: 'g2'}) "
            "SET x.name = 'My Analytics', x.namespace = $ns, x.kind = 'custom'",
            params={"tid": org, "ns": f"org_{org}_g2"})
        assert g._has_scoped_graphs(org, listed, f"org_{org}") is True

    def test_unreadable_registry_is_scoped_fail_closed(self, monkeypatch):
        """A registry read failure must SKIP (never repair) — the unsafe
        direction returns the false substring verdict and could delete a real
        completion living in a namespace we could not enumerate."""
        g = self._guard()
        from tortoise.sdk import TortoiseSDK

        def _boom(self, org_id):
            raise RuntimeError("registry down")

        monkeypatch.setattr(TortoiseSDK, "graph_list", _boom)
        assert g._has_scoped_graphs("zzz", {"org_zzz"}, "org_zzz") is True

    def test_check_is_store_wide_and_exit_codes_are_honest(self, monkeypatch):
        """`--check` ignores `--org` (a narrowed guard would report GREEN over
        a false edge elsewhere), and the 0/1/3 arithmetic is the contract."""
        import sys
        g = self._guard()
        seen: dict = {}
        rows: list = []

        def fake_sweep(org_filter=None):
            seen["org_filter"] = org_filter
            return rows

        monkeypatch.setattr(g, "sweep", fake_sweep)
        monkeypatch.setattr(sys, "argv", ["prog", "--check", "--org", "y"])
        assert g.main() == 0
        assert seen["org_filter"] is None

        rows[:] = [{"graph": "org_x", "org_id": "x", "verdict": "false",
                    "served": True}]
        assert g.main() == 1

        rows[:] = [{"graph": "org_z", "org_id": "z",
                    "verdict": "scoped-graphs", "skipped_reason": "x"}]
        assert g.main() == 3

    def test_apply_never_opens_a_graph_for_a_non_false_row(self, monkeypatch):
        """Only `false` rows reach a write. `true` stays untouched,
        `unconfirmable`/`scoped-graphs` are skipped, and no projection is even
        constructed for them."""
        g = self._guard()
        opened: list = []
        monkeypatch.setattr(g, "_proj_for",
                            lambda name: (opened.append(name), (None, None))[1])
        out = g._apply([
            {"graph": "org_t", "org_id": "t", "verdict": "true"},
            {"graph": "org_u", "org_id": "u", "verdict": "unconfirmable"},
            {"graph": "org_s", "org_id": "s", "verdict": "scoped-graphs"},
        ])
        assert [r["action"] for r in out] == [
            "skipped-true", "skipped-unconfirmable", "skipped-scoped-graphs"]
        assert opened == []

    def test_event_kind_namespacing(self):
        assert _os._event_kind_is_decision("decision") is True
        assert _os._event_kind_is_decision("core:decision") is True
        assert _os._event_kind_is_decision("decision:made") is False
        assert _os._event_kind_is_decision("review") is False
