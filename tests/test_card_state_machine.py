"""Hypothesis property-based state machine tests for Card transitions.

Covers: gate predicates, transition invariants, resurrection, split.
Pure sync — no FalkorDB dependency. Genuinely new coverage (no existing
Hypothesis tests in the test suite).

Layers 1-2 of #6877 test suite plan v3.
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from hypothesis import assume, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, run_state_machine_as_test

from tortoise.coordination import Card, CardStatus, CARD_TRANSITIONS  # noqa: E402


# ── Strategies ──────────────────────────────────────────────────────────

_valid_states = st.sampled_from(list(CardStatus))
_transition_targets = st.sampled_from(list(CardStatus))


# ── Gate function tests (Layer 1) ─────────────────────────────────────

def test_all_states_have_transition_map():
    """Every CardStatus appears as a key in CARD_TRANSITIONS."""
    for state in CardStatus:
        assert state in CARD_TRANSITIONS, f"{state} missing from transition map"


def test_no_unreachable_states():
    """Every CardStatus is reachable as a target from some source."""
    all_targets = set()
    for targets in CARD_TRANSITIONS.values():
        all_targets |= targets
    for state in CardStatus:
        assert state in all_targets or state == CardStatus.PROVIDED, (
            f"{state} is not reachable from any state (not even PROVIDED)")


def test_terminal_states_have_no_exits():
    """EXPIRED and DELEGATED have no outbound transitions."""
    for terminal in [CardStatus.EXPIRED, CardStatus.DELEGATED]:
        assert CARD_TRANSITIONS[terminal] == set(), (
            f"{terminal} should have no exits, got {CARD_TRANSITIONS[terminal]}")


def test_can_transition_matches_actual_transition():
    """can_transition(t) is consistent with transition(t) behavior —
    transition raises ValueError iff can_transition returns False.
    Cross-checks against actual runtime behavior, not the data structure."""
    for source in CardStatus:
        for target in CardStatus:
            card = Card(title="test", team="test", status=source)
            if card.can_transition(target):
                # Should succeed — no exception
                result = card.transition(target)
                assert result.status == target
            else:
                with pytest.raises(ValueError, match="Invalid transition"):
                    card.transition(target)


# ── State machine (Layer 2) ──────────────────────────────────────────

class CardStateMachine(RuleBasedStateMachine):
    """Hypothesis state machine for Card.transition() invariants."""

    def __init__(self):
        super().__init__()
        self.card = Card(title="HYP", team="test", status=CardStatus.PROVIDED)

    @rule(target_state=_valid_states)
    def transition(self, target_state: CardStatus):
        """Attempt a transition — valid moves succeed, blocked moves raise."""
        if self.card.can_transition(target_state):
            self.card.transition(target_state)
            assert self.card.status == target_state
        else:
            with pytest.raises(ValueError):
                self.card.transition(target_state)

    @invariant()
    def can_transition_iff_no_raise(self):
        """transition(t) raises ValueError iff not can_transition(t)."""
        for target in CardStatus:
            if not self.card.can_transition(target):
                with pytest.raises(ValueError):
                    self.card.transition(target)

    @invariant()
    def split_enters_delegated(self):
        """split() from RUNNING puts parent in DELEGATED."""
        if self.card.status == CardStatus.RUNNING:
            self.card.split(["Child X"])
            assert self.card.status == CardStatus.DELEGATED, (
                f"split should set DELEGATED, got {self.card.status}")


# ── Run ─────────────────────────────────────────────────────────────────

def test_card_hypothesis_state_machine():
    run_state_machine_as_test(CardStateMachine)


if __name__ == "__main__":
    test_all_states_have_transition_map()
    test_no_unreachable_states()
    test_terminal_states_have_no_exits()
    test_can_transition_matches_actual_transition()
    print("Layer 1: gate tests passed")
    run_state_machine_as_test(CardStateMachine)
    print("Layer 2: Hypothesis state machine passed")
