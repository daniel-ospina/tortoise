"""Verdict rule (issue #1415, spec §6 — pre-committed, no spin).

Outcomes:
- UNIQUE: ≥1 TRUE DIFFERENTIATOR (STRONG on a load-bearing axis,
  empirically won) AND no SERIOUS WEAKNESS (load-bearing WEAK without a
  documented mitigation path).
- MECHANISM-NOT-UNIQUE: no STRONG-on-load-bearing (≥1 STRUCTURAL) — the
  uniqueness claim is dropped until a new mechanism is built.
- WEAK-UNMITIGATED: a load-bearing WEAK lacks a mitigation path — the
  claim is gated until fixed and re-run shows it below the threshold.
- INCONCLUSIVE: matched-recall trigger fired AND subset <50% — claim does
  not ship; epic re-scopes the comparator.

Artifacts changed on non-UNIQUE outcomes: positioning copy,
product-success-eval claim section, graph-as-memory hypothesis annex.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VERDICTS = ("UNIQUE", "MECHANISM-NOT-UNIQUE", "WEAK-UNMITIGATED",
            "INCONCLUSIVE")

ARTIFACTS_CHANGED = (
    "positioning copy",
    "product-success-eval claim section",
    "graph-as-memory hypothesis annex",
)


@dataclass(frozen=True)
class Verdict:
    outcome: str
    differentiators: tuple[str, ...] = ()
    weaknesses: tuple[str, ...] = ()
    mitigation_paths: dict[str, str] = field(default_factory=dict)
    artifacts_changed: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome not in VERDICTS:
            raise ValueError(f"invalid verdict: {self.outcome!r}")


def _strong_on_load_bearing(classifications) -> list[str]:
    return [c.family for c in classifications
            if c.classification == "STRONG" and c.load_bearing
            and c.arm == "a4"]


def _load_bearing_weak(classifications) -> list[str]:
    return [c.family for c in classifications
            if c.classification == "WEAK" and c.load_bearing
            and c.arm == "a4"]


def _structural_present(classifications) -> bool:
    return any(c.classification == "STRUCTURAL" for c in classifications)


def decide_verdict(classifications,
                   mitigation_paths: dict[str, str],
                   matched_recall: dict[str, Any] | None = None,
                   ) -> Verdict:
    """Apply the pre-committed 4-branch verdict rule (E2E-3.2)."""
    # INCONCLUSIVE branch first: recall matching failed.
    if matched_recall:
        if (matched_recall.get("trigger_fired")
                and float(matched_recall.get("subset_pct", 1.0)) < 0.5):
            return Verdict(outcome="INCONCLUSIVE",
                           artifacts_changed=ARTIFACTS_CHANGED)

    diff = tuple(_strong_on_load_bearing(classifications))
    weak = tuple(_load_bearing_weak(classifications))
    unmitigated = tuple(w for w in weak if w not in mitigation_paths)

    if diff and not unmitigated:
        return Verdict(outcome="UNIQUE", differentiators=diff,
                       weaknesses=weak,
                       mitigation_paths={
                           k: v for k, v in mitigation_paths.items()
                           if k in weak},
                       artifacts_changed=())
    if unmitigated:
        return Verdict(outcome="WEAK-UNMITIGATED",
                       differentiators=diff, weaknesses=unmitigated,
                       mitigation_paths={
                           k: v for k, v in mitigation_paths.items()
                           if k in unmitigated},
                       artifacts_changed=ARTIFACTS_CHANGED)
    if _structural_present(classifications):
        return Verdict(outcome="MECHANISM-NOT-UNIQUE",
                       differentiators=diff, weaknesses=weak,
                       mitigation_paths=dict(mitigation_paths),
                       artifacts_changed=ARTIFACTS_CHANGED)
    # No differentiators AND no structural wins — no claim at all.
    return Verdict(outcome="MECHANISM-NOT-UNIQUE",
                   artifacts_changed=ARTIFACTS_CHANGED)
