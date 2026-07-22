"""State Machine Core — FSM, DAG scheduler, gate enforcer.

Composes with coordination.py (Card) and skill_declaration.py (Step).
Zero dependencies beyond stdlib + graphlib.
"""
from __future__ import annotations

import dataclasses
import enum
from collections.abc import Callable
from graphlib import TopologicalSorter
from typing import Any


# ── Step states ────────────────────────────────────────────
# StepState maps to canonical actionStatus field on Actions (§6).

class StepState(enum.Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


# Valid transitions: source → {targets}
STEP_TRANSITIONS: dict[StepState, set[StepState]] = {
    StepState.PENDING:   {StepState.READY},
    StepState.READY:     {StepState.RUNNING},
    StepState.RUNNING:   {StepState.REVIEWING, StepState.FAILED, StepState.BLOCKED},
    StepState.REVIEWING: {StepState.DONE, StepState.FAILED, StepState.BLOCKED},
    StepState.DONE:      set(),
    StepState.FAILED:    {StepState.READY},
    StepState.BLOCKED:   {StepState.READY},
}

# Terminal states (no further transitions)
# ponytail: FAILED/BLOCKED→READY, only DONE is truly terminal
_TERMINAL = {StepState.DONE}


# ── Guard predicate type ──────────────────────────────────────

# ponytail: tuple return over custom class — stdlib-native, no dataclass overhead
GuardPredicate = Callable[
    [StepState, dict[str, Any]],  # (current_state, context) →
    tuple[bool, str],              # (passed, reason)
]


# ── Transition ────────────────────────────────────────────────

@dataclasses.dataclass
class Transition:
    from_state: StepState
    to_state: StepState
    guards: list[GuardPredicate] = dataclasses.field(default_factory=list)


# ── StateMachine ──────────────────────────────────────────────

class StateMachine:
    """FSM for a single workflow step.

    Guards are evaluated in registration order. All must pass.
    Hooks fire pre-transition (before state change) and post-transition.
    """

    def __init__(self, step_id: str, state: StepState = StepState.PENDING):
        self.step_id = step_id
        self.state = state
        self.transitions: list[Transition] = []
        self._pre_hooks: list[Callable] = []
        self._post_hooks: list[Callable] = []
        self._build_transitions()

    def _build_transitions(self) -> None:
        for src, targets in STEP_TRANSITIONS.items():
            for tgt in targets:
                self.transitions.append(Transition(src, tgt))

    def on(self, event: str, callback: Callable) -> None:
        """Register a hook. event: 'pre_transition' | 'post_transition'.

        pre_transition callback: (step_id, from_state, to_state, ctx) → None
        post_transition callback: (step_id, old_state, new_state, ctx) → None
        """
        if event == "pre_transition":
            self._pre_hooks.append(callback)
        elif event == "post_transition":
            self._post_hooks.append(callback)
        else:
            raise ValueError(f"Unknown hook event: {event!r}")

    def step(self, target: StepState, ctx: dict[str, Any] | None = None) -> StepState:
        """Attempt transition after checking guards. Raises ValueError if blocked.

        Returns the new state on success.
        """
        ctx = ctx or {}
        t = self._find_transition(self.state, target)
        if t is None:
            raise ValueError(
                f"Invalid transition: {self.state.value} → {target.value}"
            )

        for guard in t.guards:
            passed, reason = guard(self.state, ctx)
            if not passed:
                raise ValueError(
                    f"Guard blocked {self.state.value} → {target.value}: {reason}"
                )

        for hook in self._pre_hooks:
            hook(self.step_id, self.state, target, ctx)

        old = self.state
        self.state = target

        for hook in self._post_hooks:
            hook(self.step_id, old, self.state, ctx)

        return self.state

    def _find_transition(self, from_: StepState, to_: StepState) -> Transition | None:
        for t in self.transitions:
            if t.from_state == from_ and t.to_state == to_:
                return t
        return None

    def add_guard(
        self, from_: StepState, to_: StepState, guard: GuardPredicate
    ) -> None:
        """Attach a guard predicate to a specific transition."""
        t = self._find_transition(from_, to_)
        if t is None:
            raise ValueError(
                f"No transition {from_.value} → {to_.value} to add guard to"
            )
        t.guards.append(guard)

    def is_terminal(self) -> bool:
        return self.state in _TERMINAL


# ── DAG Scheduler ─────────────────────────────────────────────

class DAGScheduler:
    """stdlib graphlib.TopologicalSorter wrapper for step DAGs.

    Steps are declared with dependency lists:
        steps = {"build": [], "test": ["build"], "deploy": ["test"]}
    eligible() returns steps whose dependencies are satisfied.
    """

    def __init__(self, steps: dict[str, list[str]]):
        self.steps = steps
        # ponytail: graphlib does topological sort + cycle detection in one call
        self._sorter: TopologicalSorter[str] = TopologicalSorter(steps)
        self._completed: set[str] = set()
        # Cache nodes returned by get_ready() — they stay eligible until done()
        self._ready_cache: set[str] = set()
        self._sorter.prepare()

    def eligible(self) -> list[str]:
        """Return step IDs that are ready to execute (all deps satisfied).

        Caches nodes across calls — a node stays eligible until mark_done().
        """
        new = set(self._sorter.get_ready())
        self._ready_cache |= new
        return sorted(self._ready_cache - self._completed)

    def mark_done(self, step_id: str) -> None:
        """Mark a step complete, unblocking its dependents.

        Automatically fetches eligible nodes if not yet surfaced.
        Raises ValueError if step_id not in ready cache (deps not yet satisfied).
        """
        if step_id not in self.steps:
            raise ValueError(f"Unknown step: {step_id!r}")
        # Surface any newly ready nodes (get_ready returns only nodes freed since last call)
        batch = set(self._sorter.get_ready())
        self._ready_cache |= batch
        if step_id not in self._ready_cache:
            raise ValueError(
                f"Step {step_id!r} is not ready — dependencies not satisfied"
            )
        if step_id not in self._completed:
            self._sorter.done(step_id)
            self._completed.add(step_id)

    def is_complete(self) -> bool:
        """All steps marked done."""
        return len(self._completed) == len(self.steps)


# ── Gate Enforcer ─────────────────────────────────────────────

class GateRegistry:
    """Register and evaluate gate predicates per step.

    Gates are pure functions: (state, context) → (passed, reason).
    All-must-pass composition.
    """

    def __init__(self):
        self._gates: dict[str, list[GuardPredicate]] = {}

    def register(self, step_id: str, guard: GuardPredicate) -> None:
        """Register a gate for a step. Multiple gates = all must pass."""
        self._gates.setdefault(step_id, []).append(guard)

    def evaluate_gates(
        self, step_id: str, state: StepState, ctx: dict[str, Any]
    ) -> list[tuple[bool, str]]:
        """Evaluate all registered gates for a step. Returns (passed, reason) per gate."""
        gates = self._gates.get(step_id, [])
        return [g(state, ctx) for g in gates]

    def all_pass(
        self, step_id: str, state: StepState, ctx: dict[str, Any]
    ) -> bool:
        """True if all registered gates pass for this step."""
        results = self.evaluate_gates(step_id, state, ctx)
        return all(passed for passed, _ in results)


# ── self-check ────────────────────────────────────────────────

if __name__ == "__main__":
    # ── FSM ──
    fsm = StateMachine("step-1")
    assert fsm.state == StepState.PENDING

    fsm.step(StepState.READY)
    assert fsm.state == StepState.READY

    fsm.step(StepState.RUNNING)
    assert fsm.state == StepState.RUNNING

    # Invalid transition raises
    try:
        fsm.step(StepState.PENDING)
        assert False, "expected ValueError"
    except ValueError:
        pass

    # ── Guards ──
    def intent_gate(_state: StepState, ctx: dict) -> tuple[bool, str]:
        has = bool(ctx.get("intent"))
        return (has, "intent required before behavior-changing transitions")

    fsm2 = StateMachine("step-2")
    fsm2.add_guard(StepState.PENDING, StepState.READY, intent_gate)

    try:
        fsm2.step(StepState.READY)
        assert False, "guard should block"
    except ValueError as e:
        assert "intent required" in str(e)

    fsm2.step(StepState.READY, {"intent": "yes"})
    assert fsm2.state == StepState.READY

    # ── Hooks ──
    events: list[str] = []

    def log_pre(sid: str, src: StepState, tgt: StepState, ctx: dict) -> None:
        events.append(f"pre:{src.value}→{tgt.value}")

    def log_post(sid: str, old: StepState, new: StepState, ctx: dict) -> None:
        events.append(f"post:{old.value}→{new.value}")

    fsm3 = StateMachine("step-3")
    fsm3.on("pre_transition", log_pre)
    fsm3.on("post_transition", log_post)
    fsm3.step(StepState.READY)
    assert "pre:pending→ready" in events
    assert "post:pending→ready" in events

    # ── Terminal check ──
    assert not fsm3.is_terminal()
    fsm3.step(StepState.RUNNING)
    fsm3.step(StepState.REVIEWING)
    fsm3.step(StepState.DONE)
    assert fsm3.is_terminal()

    # ── DAG Scheduler ──
    dag = DAGScheduler({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]})
    ready = dag.eligible()
    assert ready == ["a"], f"expected ['a'], got {ready}"
    dag.mark_done("a")

    ready2 = dag.eligible()
    assert set(ready2) == {"b", "c"}, f"expected {{b, c}}, got {ready2}"
    dag.mark_done("b")
    
    # c stays eligible until consumed
    ready3 = dag.eligible()
    assert ready3 == ["c"], f"expected ['c'], got {ready3}"
    dag.mark_done("c")

    ready4 = dag.eligible()
    assert ready4 == ["d"], f"expected ['d'], got {ready4}"
    dag.mark_done("d")
    assert dag.is_complete()

    # ── Gate Enforcer ──
    reg = GateRegistry()
    reg.register("step-x", lambda s, c: (True, "ok"))
    reg.register("step-x", lambda s, c: (False, "nope"))

    results = reg.evaluate_gates("step-x", StepState.PENDING, {})
    assert results == [(True, "ok"), (False, "nope")]
    assert not reg.all_pass("step-x", StepState.PENDING, {})

    # Unregistered step: no gates → all pass vacuously
    assert reg.all_pass("unknown-step", StepState.PENDING, {})

    print("✅ state_machine")
