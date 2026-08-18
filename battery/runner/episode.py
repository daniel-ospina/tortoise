"""Episode execution — EpisodeResult dataclass + trajectory accumulation.

EpisodeResult is the scorer-consumed shape (scope DD3/DD4): mirrors the
run_artifact episode_trace fields + scenario_id/seed/arm/outcomes/ep_surface,
schema-test locked like the artifact. #1409's scorers consume this shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from battery.enums import EpOutcome, ModelCallOutcome


@dataclass
class TurnRecord:
    """One recorded agent turn (real model call, never fabricated)."""

    turn: int
    role: str
    content: str
    tool_calls: int = 0
    tokens: int = 0
    model_call_outcome: ModelCallOutcome = ModelCallOutcome.OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "role": self.role,
            "tool_calls": self.tool_calls,
            "tokens": self.tokens,
            "model_call_outcome": self.model_call_outcome.value,
            "content": self.content,
        }


@dataclass
class EpisodeResult:
    """One executed episode (scorer input + artifact source of truth)."""

    scenario_id: str
    seed: int
    arm: str
    turns: list[TurnRecord] = field(default_factory=list)
    re_derivations: int = 0
    ep_outcome: EpOutcome = EpOutcome.CONVERGED
    ep_surface: dict[str, Any] = field(default_factory=dict)
    model_call_outcomes: dict[str, int] = field(default_factory=dict)
    excluded_reason: str | None = None
    #: Scorer-produced metric values (populated by the runner after scoring;
    #: written to the artifact's metric_values — trace-derived, cannot drift).
    metric_values: dict[str, float] = field(default_factory=dict)

    @property
    def n_turns(self) -> int:
        return len(self.turns)

    @property
    def n_tool_calls(self) -> int:
        return sum(t.tool_calls for t in self.turns)

    @property
    def total_tokens(self) -> int:
        return sum(t.tokens for t in self.turns)

    def terminal_outcome(self) -> ModelCallOutcome | None:
        """The worst terminal per-call outcome (None when all ok).
        Only outcomes with a positive count are considered."""
        order = [ModelCallOutcome.FAILED, ModelCallOutcome.FALLBACK_CACHED,
                 ModelCallOutcome.TIMEOUT, ModelCallOutcome.RATE_LIMITED]
        seen = {ModelCallOutcome(k) for k, v in self.model_call_outcomes.items()
                if v > 0}
        for o in order:
            if o in seen:
                return o
        return None

    @property
    def valid(self) -> bool:
        """Only all-ok episodes are valid (any terminal non-ok → excluded)."""
        return self.terminal_outcome() is None

    def to_artifact_trace(self) -> dict[str, Any]:
        return {
            "turns": [t.to_dict() for t in self.turns],
            "n_turns": self.n_turns,
            "n_tool_calls": self.n_tool_calls,
            "re_derivations": self.re_derivations,
            "total_tokens": self.total_tokens,
        }


class EpisodeTracker:
    """Accumulates turn records for one episode."""

    def __init__(self) -> None:
        self.turns: list[TurnRecord] = []

    def add_turn(self, *, role: str, content: str, tool_calls: int = 0,
                 tokens: int = 0,
                 outcome: ModelCallOutcome = ModelCallOutcome.OK) -> None:
        self.turns.append(TurnRecord(
            turn=len(self.turns) + 1, role=role, content=content,
            tool_calls=tool_calls, tokens=tokens, model_call_outcome=outcome))
