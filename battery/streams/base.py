"""Stream base (issue #1411, plan §5 streams/).

A stream consumes MULTI-SESSION episode traces (per-session run_artifacts:
tokens, steps, strategy-reuse, decide_cycles, provenance citations,
decision/rationale strings) and emits a TRAJECTORY metric with an AC gate.
Trajectories are the source of truth — metrics compute from the traces
only, never re-inferred at report time (determinism, plan §5).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class StreamResult:
    """One stream's longitudinal metric."""

    stream_id: str
    metric: str
    value: float
    passed: bool
    threshold: float
    trajectory: tuple[float, ...] = ()
    evidence: tuple[str, ...] = ()


class Stream(Protocol):
    """A Tier-2 stream: score a session sequence against the AC gate."""

    stream_id: str
    metric: str

    def score(self, sessions: list[dict[str, Any]],
              golds: dict[str, str] | None,
              threshold: float) -> StreamResult: ...
