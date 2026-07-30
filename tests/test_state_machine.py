"""Tests for state_machine — FSM, DAG scheduler, gate enforcer.

Covers: transitions, guards, hooks, DAG topology, gate composition.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.state_machine import (
    StepState,
    StateMachine,
    DAGScheduler,
    GateRegistry,
)


# ── FSM ──────────────────────────────────────────────────────

class TestStateMachine:
    def test_initial_state(self):
        fsm = StateMachine("s1")
        assert fsm.state == StepState.PENDING
        assert not fsm.is_terminal()

    def test_valid_transitions(self):
        fsm = StateMachine("s1")
        fsm.step(StepState.READY)
        assert fsm.state == StepState.READY
        fsm.step(StepState.RUNNING)
        assert fsm.state == StepState.RUNNING
        fsm.step(StepState.REVIEWING)
        assert fsm.state == StepState.REVIEWING
        fsm.step(StepState.DONE)
        assert fsm.state == StepState.DONE
        assert fsm.is_terminal()

    def test_running_to_failed(self):
        fsm = StateMachine("s1")
        fsm.step(StepState.READY)
        fsm.step(StepState.RUNNING)
        fsm.step(StepState.FAILED)
        assert fsm.state == StepState.FAILED
        # FAILED→READY is valid (resurrection), so not terminal
        assert not fsm.is_terminal()

    def test_running_to_blocked(self):
        fsm = StateMachine("s1")
        fsm.step(StepState.READY)
        fsm.step(StepState.RUNNING)
        fsm.step(StepState.BLOCKED)
        assert fsm.state == StepState.BLOCKED
        # BLOCKED→READY is valid (resurrection), so not terminal
        assert not fsm.is_terminal()

    def test_resurrection_from_failed(self):
        fsm = StateMachine("s1")
        fsm.step(StepState.READY)
        fsm.step(StepState.RUNNING)
        fsm.step(StepState.FAILED)
        fsm.step(StepState.READY)  # resurrection
        assert fsm.state == StepState.READY

    def test_resurrection_from_blocked(self):
        fsm = StateMachine("s1")
        fsm.step(StepState.READY)
        fsm.step(StepState.RUNNING)
        fsm.step(StepState.BLOCKED)
        fsm.step(StepState.READY)  # resurrection
        assert fsm.state == StepState.READY

    def test_invalid_transition_raises(self):
        fsm = StateMachine("s1")
        # PENDING → RUNNING is invalid (must go through READY)
        with pytest.raises(ValueError, match="Invalid transition"):
            fsm.step(StepState.RUNNING)

    def test_done_is_terminal_no_exits(self):
        fsm = StateMachine("s1")
        fsm.step(StepState.READY)
        fsm.step(StepState.RUNNING)
        fsm.step(StepState.REVIEWING)
        fsm.step(StepState.DONE)
        with pytest.raises(ValueError, match="Invalid transition"):
            fsm.step(StepState.RUNNING)

    def test_guard_blocks_transition(self):
        def blk(_s, _c):
            return (False, "nope")

        fsm = StateMachine("s1")
        fsm.add_guard(StepState.PENDING, StepState.READY, blk)
        with pytest.raises(ValueError, match="nope"):
            fsm.step(StepState.READY)

    def test_guard_passes_with_context(self):
        def needs_key(_s, ctx):
            return (ctx.get("key") == 42, "need key=42")

        fsm = StateMachine("s1")
        fsm.add_guard(StepState.PENDING, StepState.READY, needs_key)

        # Fails without context
        with pytest.raises(ValueError, match="need key"):
            fsm.step(StepState.READY)

        # Passes with correct context
        fsm.step(StepState.READY, {"key": 42})
        assert fsm.state == StepState.READY

    def test_multiple_guards_all_must_pass(self):
        def g1(_s, _c):
            return (True, "ok1")

        def g2(_s, _c):
            return (False, "fail2")

        fsm = StateMachine("s1")
        fsm.add_guard(StepState.PENDING, StepState.READY, g1)
        fsm.add_guard(StepState.PENDING, StepState.READY, g2)
        with pytest.raises(ValueError, match="fail2"):
            fsm.step(StepState.READY)

    def test_pre_hook(self):
        calls = []

        def hook(sid, src, tgt, ctx):
            calls.append((sid, src, tgt))

        fsm = StateMachine("s1")
        fsm.on("pre_transition", hook)
        fsm.step(StepState.READY)
        assert calls == [("s1", StepState.PENDING, StepState.READY)]

    def test_post_hook(self):
        calls = []

        def hook(sid, old, new, ctx):
            calls.append((sid, old, new))

        fsm = StateMachine("s1")
        fsm.on("post_transition", hook)
        fsm.step(StepState.READY)
        assert calls == [("s1", StepState.PENDING, StepState.READY)]

    def test_hooks_fire_in_order(self):
        order = []

        def pre(sid, src, tgt, ctx):
            order.append("pre")

        def post(sid, old, new, ctx):
            order.append("post")

        fsm = StateMachine("s1")
        fsm.on("pre_transition", pre)
        fsm.on("post_transition", post)
        fsm.step(StepState.READY)
        assert order == ["pre", "post"]

    def test_add_guard_nonexistent_transition(self):
        fsm = StateMachine("s1")
        with pytest.raises(ValueError, match="No transition"):
            fsm.add_guard(StepState.PENDING, StepState.RUNNING, lambda s, c: (True, "ok"))

    def test_unknown_hook_event_raises(self):
        fsm = StateMachine("s1")
        with pytest.raises(ValueError, match="Unknown hook event"):
            fsm.on("bogus_event", lambda *a: None)

    def test_step_returns_new_state(self):
        """step() returns the new StepState after a successful transition."""
        fsm = StateMachine("s1")
        result = fsm.step(StepState.READY)
        assert result == StepState.READY
        assert isinstance(result, StepState)

    def test_failed_only_resurrects_to_ready(self):
        """FAILED → any state other than READY must raise ValueError."""
        fsm = StateMachine("s1")
        fsm.step(StepState.READY)
        fsm.step(StepState.RUNNING)
        fsm.step(StepState.FAILED)
        # Only READY is valid from FAILED
        with pytest.raises(ValueError, match="Invalid transition"):
            fsm.step(StepState.RUNNING)
        with pytest.raises(ValueError, match="Invalid transition"):
            fsm.step(StepState.DONE)
        with pytest.raises(ValueError, match="Invalid transition"):
            fsm.step(StepState.REVIEWING)

    def test_hook_exception_propagates(self):
        """Exception in pre_hook propagates — transition does NOT complete."""
        class HookBoom(Exception):
            pass

        def explode(_sid, _src, _tgt, _ctx):
            raise HookBoom("boom")

        fsm = StateMachine("s1")
        fsm.on("pre_transition", explode)
        with pytest.raises(HookBoom):
            fsm.step(StepState.READY)
        # State never changed
        assert fsm.state == StepState.PENDING


# ── DAG Scheduler ────────────────────────────────────────────

class TestDAGScheduler:
    def test_linear_dag(self):
        dag = DAGScheduler({"a": [], "b": ["a"], "c": ["b"]})
        assert dag.eligible() == ["a"]
        dag.mark_done("a")
        assert dag.eligible() == ["b"]
        dag.mark_done("b")
        assert dag.eligible() == ["c"]
        dag.mark_done("c")
        assert dag.is_complete()

    def test_fork_join(self):
        dag = DAGScheduler({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]})
        assert dag.eligible() == ["a"]
        dag.mark_done("a")
        assert set(dag.eligible()) == {"b", "c"}
        dag.mark_done("b")
        assert dag.eligible() == ["c"]  # c still pending, d blocked by c
        dag.mark_done("c")
        assert dag.eligible() == ["d"]
        dag.mark_done("d")
        assert dag.is_complete()

    def test_independent_parallel(self):
        dag = DAGScheduler({"a": [], "b": [], "c": []})
        assert set(dag.eligible()) == {"a", "b", "c"}

    def test_nothing_ready_when_blocked(self):
        dag = DAGScheduler({"a": ["b"], "b": ["c"], "c": []})
        assert dag.eligible() == ["c"]
        assert not dag.is_complete()

    def test_is_complete_false_midway(self):
        dag = DAGScheduler({"a": [], "b": ["a"]})
        assert not dag.is_complete()
        dag.mark_done("a")
        assert not dag.is_complete()
        dag.mark_done("b")
        assert dag.is_complete()

    def test_empty_dag(self):
        dag = DAGScheduler({})
        assert dag.eligible() == []
        assert dag.is_complete()

    def test_mark_done_unknown_step(self):
        dag = DAGScheduler({"a": []})
        with pytest.raises(ValueError, match="Unknown step"):
            dag.mark_done("nonexistent")

    def test_mark_done_twice_idempotent(self):
        """Calling mark_done twice on the same node is a no-op, not an error."""
        dag = DAGScheduler({"a": [], "b": ["a"]})
        dag.mark_done("a")
        dag.mark_done("a")  # second call — should not raise, not corrupt
        assert dag.eligible() == ["b"]
        dag.mark_done("b")
        dag.mark_done("b")  # idempotent on terminal too
        assert dag.is_complete()

    def test_mark_done_non_ready_is_noop(self):
        """mark_done on a step whose deps aren't satisfied is silently ignored
        — it does NOT enter _completed. (#49 fix)"""
        dag = DAGScheduler({"a": ["b"], "b": []})
        dag.mark_done("a")  # a depends on b, but b not done yet
        assert "a" not in dag._completed
        assert not dag.is_complete()
        # Normal flow still works
        dag.mark_done("b")
        assert dag.eligible() == ["a"]
        dag.mark_done("a")
        assert dag.is_complete()


# ── Gate Enforcer ────────────────────────────────────────────

class TestGateRegistry:
    def test_no_gates_all_pass(self):
        reg = GateRegistry()
        assert reg.all_pass("step-x", StepState.PENDING, {})

    def test_single_gate(self):
        reg = GateRegistry()
        reg.register("s1", lambda s, c: (True, "ok"))
        assert reg.all_pass("s1", StepState.PENDING, {})

    def test_single_gate_fails(self):
        reg = GateRegistry()
        reg.register("s1", lambda s, c: (False, "nope"))
        assert not reg.all_pass("s1", StepState.PENDING, {})

    def test_all_must_pass_composition(self):
        reg = GateRegistry()
        reg.register("s1", lambda s, c: (True, "ok"))
        reg.register("s1", lambda s, c: (False, "fail"))
        assert not reg.all_pass("s1", StepState.PENDING, {})

    def test_evaluate_returns_all_results(self):
        reg = GateRegistry()
        reg.register("s1", lambda s, c: (True, "a"))
        reg.register("s1", lambda s, c: (False, "b"))
        results = reg.evaluate_gates("s1", StepState.PENDING, {})
        assert results == [(True, "a"), (False, "b")]

    def test_multiple_steps_independent(self):
        reg = GateRegistry()
        reg.register("s1", lambda s, c: (False, "blocked"))
        reg.register("s2", lambda s, c: (True, "free"))
        assert not reg.all_pass("s1", StepState.PENDING, {})
        assert reg.all_pass("s2", StepState.PENDING, {})

    def test_evaluate_unknown_step_returns_empty(self):
        """evaluate_gates for a step with no registered gates returns empty list."""
        reg = GateRegistry()
        results = reg.evaluate_gates("unknown", StepState.PENDING, {})
        assert results == []
        assert reg.all_pass("unknown", StepState.PENDING, {})


# ── Self-check runner ───────────────────────────────────────

def test_state_machine_self_check():
    """Run module as __main__ in-process to cover self-check assertions."""
    import runpy
    from pathlib import Path

    mod = Path(__file__).resolve().parents[1] / "tortoise" / "state_machine.py"
    runpy.run_path(str(mod), run_name="__main__")
