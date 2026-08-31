"""Unit tests for the canonical onboarding state module (T1, #2001 W5).

Pins (scope doc §4.1/§4.3, plan T1):
- canonical step list (6), card subset (4, ⊆ canonical), per-key semantics table
- fork-aware completion gate (self/build/compact, compact-first, fork=None→'self')
- validate_step_id
- set-once/LWW/server-owned semantics constants
- module is importable without hosted_api (no circular import)
"""
from __future__ import annotations

from tortoise.onboarding import state as onboarding_state
from tortoise.onboarding.state import (
    CARD_STEPS,
    FLOW_KEYS,
    FWW,
    LWW,
    MAP_MERGE,
    ONBOARDING_STEPS,
    PER_KEY_SEMANTICS,
    SERVER_OWNED,
    SET_ONCE,
    STATUS_ACTIVE,
    STATUS_COMPLETE,
    STEP_IDS,
    completion_gate_satisfied,
    validate_step_id,
)

# ── canonical list / card subset ─────────────────────────────

class TestCanonicalList:
    def test_six_canonical_steps_in_display_order(self):
        assert tuple(STEP_IDS) == (
            "team-named",
            "harness-connected",
            "first-points-filed",
            "decide-completed",
            "capture-disclosed",
            "catalog-presented",
        )

    def test_onboarding_steps_matches_step_ids(self):
        assert set(ONBOARDING_STEPS) == set(STEP_IDS)

    def test_card_subset_is_subset_of_canonical(self):
        assert set(CARD_STEPS) <= set(STEP_IDS)

    def test_card_subset_has_four_members(self):
        assert len(set(CARD_STEPS)) == 4

    def test_capture_disclosed_not_counted_in_card(self):
        # "capture-disclosed before decide must NOT render '4 of 4'" — the
        # disclosure is canonical but NEVER a counted card row.
        assert "capture-disclosed" not in CARD_STEPS

    def test_card_and_gate_steps_are_fork_aware(self):
        # decide/catalog are fork-exclusive card rows; neither renders for the
        # other fork.
        assert "decide-completed" in CARD_STEPS
        assert "catalog-presented" in CARD_STEPS


class TestStepValidation:
    def test_valid_step_accept(self):
        assert validate_step_id("harness-connected") is True

    def test_unknown_step_rejected(self):
        assert validate_step_id("decide-completed-fake") is False

    def test_empty_rejected(self):
        assert validate_step_id("") is False

    def test_fork_keys_are_not_steps(self):
        assert validate_step_id("fork") is False
        assert validate_step_id("status") is False


# ── per-key semantics table ──────────────────────────────────

class TestSemanticsTable:
    def test_step_edges_are_fww(self):
        for step in STEP_IDS:
            assert PER_KEY_SEMANTICS[step] == FWW

    def test_fork_compact_set_once(self):
        assert PER_KEY_SEMANTICS["fork"] == SET_ONCE
        assert PER_KEY_SEMANTICS["compact"] == SET_ONCE

    def test_last_decide_attempt_lww(self):
        assert PER_KEY_SEMANTICS["last_decide_attempt"] == LWW

    def test_status_version_server_owned(self):
        assert PER_KEY_SEMANTICS["status"] == SERVER_OWNED
        assert PER_KEY_SEMANTICS["version"] == SERVER_OWNED

    def test_member_progress_map_merge(self):
        assert PER_KEY_SEMANTICS["member_progress"] == MAP_MERGE

    def test_flow_keys_exact_set(self):
        assert {
            "fork", "status", "version", "completed_steps",
            "member_progress", "last_decide_attempt", "compact",
        } == FLOW_KEYS

    def test_onboarding_complete_is_not_flow(self):
        # legacy jsonb key until the T7 flip — never in the strip set.
        assert "onboarding_complete" not in FLOW_KEYS

    def test_status_constants(self):
        assert STATUS_ACTIVE == "active"
        assert STATUS_COMPLETE == "complete"


# ── completion gate ──────────────────────────────────────────

class TestCompletionGate:
    def test_self_all_steps_complete(self):
        done = {"team-named", "harness-connected", "first-points-filed",
                "decide-completed"}
        assert completion_gate_satisfied(done, "self", False) is True

    def test_self_missing_decide_incomplete(self):
        done = {"team-named", "harness-connected", "first-points-filed"}
        assert completion_gate_satisfied(done, "self", False) is False

    def test_build_uses_catalog_not_decide(self):
        done = {"harness-connected", "first-points-filed", "catalog-presented"}
        assert completion_gate_satisfied(done, "build", False) is True
        # decide alone can never complete a build fork
        done2 = {"harness-connected", "first-points-filed", "decide-completed"}
        assert completion_gate_satisfied(done2, "build", False) is False

    def test_compact_reduced_checklist(self):
        done = {"harness-connected", "first-points-filed"}
        assert completion_gate_satisfied(done, "self", True) is True
        # compact never needs decide/catalog/team-named
        assert completion_gate_satisfied(done, "build", True) is True

    def test_fork_none_defaults_self(self):
        done = {"team-named", "harness-connected", "first-points-filed",
                "decide-completed"}
        assert completion_gate_satisfied(done, None, False) is True
        missing = {"harness-connected", "first-points-filed", "decide-completed"}
        assert completion_gate_satisfied(missing, None, False) is False

    def test_compact_first_wins_over_fork(self):
        # compact + any fork → reduced checklist (compact-first)
        done = {"harness-connected", "first-points-filed"}
        assert completion_gate_satisfied(done, "self", True) is True
        assert completion_gate_satisfied(done, "build", True) is True

    def test_capture_disclosed_never_completes_alone(self):
        done = {"capture-disclosed"}
        assert completion_gate_satisfied(done, "self", False) is False

    def test_empty_steps_never_complete(self):
        assert completion_gate_satisfied(set(), "self", False) is False

    def test_unknown_fork_falls_back_to_self(self):
        done = {"team-named", "harness-connected", "first-points-filed",
                "decide-completed"}
        assert completion_gate_satisfied(done, "bogus", False) is True


# ── node-aware wire completion (T3/T7) ───────────────────────

class TestWireCompletion:
    def test_node_complete_true(self):
        assert onboarding_state.resolve_wire_completion(
            "complete", False, []) is True

    def test_grandfathered_window_guard(self):
        # node present, NOT complete, zero edges, jsonb true → wire true
        # (kills the poisoned-false window for legacy-wizard completers)
        assert onboarding_state.resolve_wire_completion(
            "active", True, []) is True

    def test_guard_self_terminating_on_first_edge(self):
        # first step edge → node governs (accepted one-way door)
        assert onboarding_state.resolve_wire_completion(
            "active", True, ["harness-connected"]) is False

    def test_node_absent_grandfathered_fallback(self):
        assert onboarding_state.resolve_wire_completion(None, True, []) is True
        assert onboarding_state.resolve_wire_completion(None, False, []) is False

    def test_active_with_edges_false(self):
        assert onboarding_state.resolve_wire_completion(
            "active", False, ["harness-connected"]) is False

    def test_node_status_wins_over_raw(self):
        assert onboarding_state.resolve_wire_completion(
            "complete", False, []) is True


class TestFlowShapes:
    def test_flow_defaults_shape(self):
        d = onboarding_state.flow_defaults()
        assert set(d) == {
            "fork", "status", "version", "completed_steps",
            "member_progress", "last_decide_attempt", "compact"}
        assert d["status"] == "active"
        assert d["version"] == 1
        assert d["completed_steps"] == []

    def test_flow_unavailable_markers(self):
        u = onboarding_state.flow_unavailable()
        assert set(u) == set(onboarding_state.flow_defaults())
        assert all(v == "unavailable" for v in u.values())

    def test_parse_member_progress(self):
        assert onboarding_state.parse_member_progress(
            '{"u1": ["harness-connected"]}') == {"u1": ["harness-connected"]}
        assert onboarding_state.parse_member_progress("not-json") == {}
        assert onboarding_state.parse_member_progress(None) == {}
        assert onboarding_state.parse_member_progress({"u1": []}) == {"u1": []}
        assert onboarding_state.parse_member_progress(42) == {}


# ── module import hygiene ────────────────────────────────────

class TestModuleHygiene:
    def test_no_hosted_api_import(self):
        # state.py must be importable without pulling hosted_api (circular).
        # In a shared pytest process hosted_api may already be loaded by other
        # tests, so assert the DELTA: importing state must not NEWLY import it.
        import sys
        before = set(sys.modules)
        import tortoise.onboarding.state as m
        newly = set(sys.modules) - before
        assert "tortoise.hosted_api" not in newly
        # AND scan the module's ACTUAL import statements (AST, not source
        # substring — formatting cannot break this) for hosted_api refs.
        import ast
        import inspect

        src = inspect.getsource(m)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.ImportFrom) and \
                    node.module and "hosted_api" in node.module:
                raise AssertionError(
                    f"state.py imports hosted_api via {node.module!r}")
            if isinstance(node, ast.Import):
                for a in node.names:
                    if "hosted_api" in a.name:
                        raise AssertionError(
                            f"state.py imports hosted_api via {a.name!r}")

    def test_js_card_subset_matches_canonical(self):
        """T6 parity: the dashboard's counted card steps (setupGuide.js
        SETUP_GUIDE_COUNTED) ⊆ the canonical Python STEP_IDS — the card can
        never drift from the server vocabulary."""
        import re
        from pathlib import Path
        js = Path(__file__).resolve().parent.parent / \
            "website/apps/dashboard/src/setupGuide.js"
        src = js.read_text()
        block = re.search(r"SETUP_GUIDE_COUNTED = Object\.freeze\(\[(.*?)\]",
                          src, re.S)
        assert block, "SETUP_GUIDE_COUNTED not found in setupGuide.js"
        ids = re.findall(r"'([a-z0-9-]+)'", block.group(1))
        assert len(ids) == 4
        assert set(ids) <= set(STEP_IDS)
        assert "capture-disclosed" not in ids
        assert "team-named" not in ids
