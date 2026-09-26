"""Unit tests for the canonical onboarding state module (T1, #2001 W5).

Pins (scope doc §4.1/§4.3, plan T1):
- canonical step list (7), card subset (3, ⊆ canonical), per-key semantics table
- fork-aware completion gate (self/build/compact, compact-first, fork=None→'self')
- validate_step_id
- #3451 restart_pending derivation (both directions)
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
    resolve_wire_completion,
    restart_pending,
    validate_step_id,
)

# ── canonical list / card subset ─────────────────────────────

class TestCanonicalList:
    def test_seven_canonical_steps_in_display_order(self):
        assert tuple(STEP_IDS) == (
            "team-named",
            "connection-written",
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

    def test_card_subset_has_three_members(self):
        assert len(set(CARD_STEPS)) == 3

    def test_capture_disclosed_not_counted_in_card(self):
        # "capture-disclosed before decide must NOT render '4 of 4'" — the
        # disclosure is canonical but NEVER a counted card row.
        assert "capture-disclosed" not in CARD_STEPS

    def test_card_and_gate_steps_are_fork_aware(self):
        # decide is the self-fork card row; catalog-presented is NOT a counted
        # row (#3913 — the build fork renders no extra row).
        assert "decide-completed" in CARD_STEPS
        assert "catalog-presented" not in CARD_STEPS

    def test_connection_written_is_canonical_but_not_a_counted_row(self):
        # #3451: the config-write trace is canonical (accepted + recorded) but
        # NOT a completion-relevant card row — the gates are unchanged, so it
        # must not change any fork's N-of-M.
        assert "connection-written" in STEP_IDS
        assert "connection-written" not in CARD_STEPS


class TestRestartPending:
    """#3451: the config-write trace, and the restart-pending condition
    DERIVED from it — never a second stored field (no FLOW key, no jsonb key).

    Both directions are the point: a restart-pending install must be
    distinguishable from one whose config write never happened.
    """

    def test_written_but_not_verified_is_restart_pending(self):
        assert restart_pending(["team-named", "connection-written"]) is True

    def test_no_config_written_is_never_restart_pending(self):
        # the abandoned install — the direction that must never be reported
        # as waiting for a restart
        assert restart_pending(["team-named"]) is False
        assert restart_pending([]) is False

    def test_verified_connection_is_not_restart_pending(self):
        assert restart_pending(
            ["team-named", "connection-written", "harness-connected"]) is False
        # a server-observed connection alone is never 'pending'
        assert restart_pending(["harness-connected"]) is False

    def test_unavailable_marker_is_not_a_step_set(self):
        # a graph-down read serves the literal 'unavailable' string; the helper
        # must not string-scan it into a fabricated verdict.
        # RED mutation: drop the isinstance guard → the `None` assert below
        # raises TypeError. (The string assert stays False either way:
        # set("unavailable") holds characters, not step ids.)
        assert restart_pending("unavailable") is False
        assert restart_pending(None) is False

    def test_new_step_is_in_no_completion_gate(self):
        # the #3913 owner ruling stands: completion is unchanged by the new
        # step — it is never required, and it never completes anything alone
        # RED mutation: add "connection-written" to _GATE_SELF/_GATE_BUILD →
        # the FIRST assert (the full set) drops to False. The "alone" assert
        # stays False either way — the gate needs the other steps regardless.
        full = {"team-named", "harness-connected", "first-points-filed",
                "decide-completed"}
        for fork in ("self", "build"):
            assert completion_gate_satisfied(full, fork, False) is True
            assert completion_gate_satisfied(
                full | {"connection-written"}, fork, False) is True
            assert completion_gate_satisfied(
                {"connection-written"}, fork, False) is False

    def test_config_write_never_closes_the_grandfathered_window(self):
        """#3451: ``connection-written`` is a CLIENT-only trace, so it must not
        terminate the grandfathered-window guard — that window closes on the
        first SERVER-OBSERVED act (#3913). A grandfathered org (node present,
        status active, legacy jsonb complete=true) that follows §3 must stay
        complete on the wire; only a real observed act may flip it.

        RED mutation: drop 'connection-written' from ``_NON_AGENT_STEPS`` → the
        second assert flips False, i.e. the config checkpoint alone regresses a
        legitimately grandfathered org to incomplete before the restart.
        """
        gf = ["team-named"]
        assert resolve_wire_completion("active", True, gf) is True
        assert resolve_wire_completion(
            "active", True, [*gf, "connection-written"]) is True
        # a SERVER-OBSERVED act still closes the window (fail-closed)
        assert resolve_wire_completion(
            "active", True, [*gf, "harness-connected"]) is False

    def test_read_only_grandfathered_mirror_agrees_on_the_config_write(self):
        """#3451: ``_legacy_grandfathered`` is the read-only MIRROR of the same
        grandfathered branch (the #3912 false-completion repair path reads it).
        It must agree with ``resolve_wire_completion`` about ``connection-written``,
        or the repair path can judge a legitimately grandfathered org falsely
        complete and REGRESS its status.

        RED mutation: revert ``_legacy_grandfathered`` to ``s != "team-named"``
        → the second assert flips False.
        """
        assert onboarding_state._legacy_grandfathered(True, ["team-named"]) is True
        assert onboarding_state._legacy_grandfathered(
            True, ["team-named", "connection-written"]) is True
        # a SERVER-OBSERVED act still closes the window
        assert onboarding_state._legacy_grandfathered(
            True, ["team-named", "harness-connected"]) is False


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

    def test_fork_unsure_at_lww(self):
        # #2407: the deferral record is LWW — a repeat "not sure yet" answer
        # re-stamps, never a set-once 409 (the set-once contract belongs to
        # the fork VALUE, which stays None until an explicit self/build pick).
        assert PER_KEY_SEMANTICS["fork_unsure_at"] == LWW

    def test_status_version_server_owned(self):
        assert PER_KEY_SEMANTICS["status"] == SERVER_OWNED
        assert PER_KEY_SEMANTICS["version"] == SERVER_OWNED

    def test_member_progress_map_merge(self):
        assert PER_KEY_SEMANTICS["member_progress"] == MAP_MERGE

    def test_flow_keys_exact_set(self):
        assert {
            "fork", "status", "version", "completed_steps",
            "member_progress", "last_decide_attempt", "compact",
            "fork_unsure_at",
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

    def test_build_completes_on_the_two_observed_acts(self):
        # #3913 (owner ruling 2026-09-20): the build fork completes on the two
        # acts the server OBSERVES — a harness reached the server and a first
        # point was filed. `catalog-presented` ("Review the catalog") is NO
        # LONGER required. RED on origin/main.
        done = {"harness-connected", "first-points-filed"}
        assert completion_gate_satisfied(done, "build", False) is True

    def test_build_fail_closed_when_either_observed_act_missing(self):
        # fail-closed: one of the two observed acts is not enough.
        assert completion_gate_satisfied(
            {"harness-connected"}, "build", False) is False
        assert completion_gate_satisfied(
            {"first-points-filed"}, "build", False) is False
        assert completion_gate_satisfied(set(), "build", False) is False

    def test_catalog_presented_never_required_and_never_blocks(self):
        # the id stays ACCEPTED (existing orgs carry it in completed_steps),
        # but the gate never requires it: absent and present both complete.
        base = {"harness-connected", "first-points-filed"}
        assert completion_gate_satisfied(base, "build", False) is True
        assert completion_gate_satisfied(
            base | {"catalog-presented"}, "build", False) is True
        # catalog alone never completes a build fork
        assert completion_gate_satisfied(
            {"catalog-presented"}, "build", False) is False

    def test_build_decide_alone_never_completes(self):
        # decide remains a SELF-fork row — it can never complete a build fork.
        assert completion_gate_satisfied(
            {"decide-completed"}, "build", False) is False

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

    def test_fork_none_with_unsure_never_auto_closes_as_self(self):
        # #2407: "not sure yet — decide later" recorded (fork still None) —
        # the gate must NOT evaluate on the read-time self default, even with
        # the FULL self checklist done. Onboarding stays open until the org
        # answers the fork (the Setup-guide fork row is the blocker).
        self_done = {"team-named", "harness-connected", "first-points-filed",
                     "decide-completed"}
        assert completion_gate_satisfied(
            self_done, None, False, fork_unsure_at=True) is False

    def test_unsure_without_checklist_incomplete(self):
        done = {"harness-connected"}
        assert completion_gate_satisfied(
            done, None, False, fork_unsure_at=True) is False

    def test_unsure_with_unknown_fork_still_unsatisfied(self):
        self_done = {"team-named", "harness-connected", "first-points-filed",
                     "decide-completed"}
        assert completion_gate_satisfied(
            self_done, "bogus", False, fork_unsure_at=True) is False

    def test_persisted_fork_wins_over_stale_unsure_marker(self):
        # #2407 invariant: fork_unsure_at is meaningful only while fork IS
        # NULL — a persisted fork evaluates its own gate regardless of the
        # marker (the checkpoint clears it on fork-set, but reads never trust
        # it).
        done = {"harness-connected", "first-points-filed"}
        assert completion_gate_satisfied(
            done, "build", False, fork_unsure_at=True) is True

    def test_compact_first_ignores_unsure_marker(self):
        # compact-first: a compact org never sees the fork card (the server
        # refuses to record unsure for compact orgs) and needs only the
        # reduced checklist — an unsure marker can never block a compact org.
        done = {"harness-connected", "first-points-filed"}
        assert completion_gate_satisfied(
            done, None, True, fork_unsure_at=True) is True

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
            "member_progress", "last_decide_attempt", "compact",
            "fork_unsure_at"}
        assert d["status"] == "active"
        assert d["version"] == 1
        assert d["completed_steps"] == []
        assert d["fork_unsure_at"] is None

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
        assert len(ids) == 3
        assert set(ids) <= set(STEP_IDS)
        assert "capture-disclosed" not in ids
        assert "team-named" not in ids
        # #3913: the build fork renders no catalog row — the id stays canonical
        # (accepted) but is never a counted card step.
        assert "catalog-presented" not in ids
