"""Probe→Scorer adapter + derive emission + no-data sentinel (issue #2284, T5).

Bridges the PROBE seam (``Probe.score(trace, gold, threshold)`` — probes
live in ``battery/probes/*``) onto the RUN seam (``Scorer.score(episode,
scenario, rubric_id=None)`` in battery/runner/scorers.py). Today
``resolve_scorer("battery.probes.r1_contradiction")`` crashes because a
probe module declares no ``Scorer`` attribute — this module resolves probe
modules into adapter instances instead.

Schema-v1.1 two-phase emitter gate (Task 1 P0 cycle-5), owned here for the
probe leg:

1. PRE-SCORING gate on the episode — only fields whose entries exist at
   episode end (pre-derivation kinds: envelope/state/behavioral — no
   derived/gold/judge). A gapped pre-scoring expected-coverage check
   returns the NO-DATA SENTINEL (``None`` -> ``insufficient_n`` cell): a
   probe never produces a measured value from an uncovered log (never a
   fabricated 0.0).
2. The DERIVE EMISSION PASS appends the derived/gold entries the episode's
   expected set owns (gold-store reads + Task-9-derived semantics land with
   the executor/judge legs); the FINAL coverage validation runs at artifact
   assembly over the post-derivation log (build_run_artifact computes the
   per-episode ``emitter_gap``).

Mock runs are never scored as real: a probe scorer on a mock episode always
records the sentinel (mock event logs are empty and never claimed real) and
its expected set is empty — mock never false-flags ``incomplete_emitter_gap``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from battery.exceptions import ConfigError
from battery.runner.emit import (
    FIELD_EMITTERS,
    MANDATORY,
    validate_emitter_coverage,
)
from battery.runner.scorers import ScorerResult

#: Kinds whose entries exist at episode end (phase-1 fields). derived /
#: gold_store / judge_annotation entries only exist AFTER the derive/judge
#: passes run at scoring time — they are never phase-1 expected.
_PRE_DERIVATION_KINDS = frozenset({"tool_event", "state_event", "envelope"})

#: family/task_type -> scenario-conditional expected fields (Task-1 SINGLE
#: RULE — gap only when the scorer seam put them in `expected` for THIS
#: episode). CONDITIONAL behavioral fields never sit here (absence = 0.0).
_FAMILY_CONDITIONAL: dict[str, frozenset[str]] = {
    # R3/R4/R5 calibration + R2 decision families read gold-store truth.
    "calibration": frozenset({"outcome_correct", "confidences", "outcomes"}),
    "decision": frozenset({"outcome_correct", "confidences", "outcomes"}),
    "decision_drift": frozenset({"outcome_correct", "confidences", "outcomes"}),
}


@dataclass(frozen=True)
class ProbeRecord:
    """One episode's probe outcome (family-report aggregation unit)."""

    family: str
    arm: str
    metric: str
    value: float | None = None   # None = no-data sentinel (insufficient_n)
    measured: bool = False
    valid: bool = True           # excluded (terminal-failure) episodes never
                                 # contribute measured cells


def expected_coverage_for(scenario, *, run_mode: str = "real") -> set[str]:
    """Per-episode expected set (MANDATORY x scenario/family-conditional per
    the Task-1 expectation rule — no arms.yaml capability term). Mock runs
    are neutral (empty expected -> gap empty). injection_turn is expected
    only when the scenario actually plants a ¬A k-turn (never bct twins)."""
    if run_mode != "real":
        return set()
    expected = set(MANDATORY)
    if getattr(scenario, "contradiction_pairs", ()):
        expected |= {"injection_turn"}
    expected |= set(_FAMILY_CONDITIONAL.get(getattr(scenario, "task_type", ""), ()))
    return expected


def phase1_expected(expected: set[str]) -> set[str]:
    """Phase-1 (pre-scoring) slice: only pre-derivation-emittable fields."""
    return {f for f in expected if FIELD_EMITTERS[f] in _PRE_DERIVATION_KINDS}


def trace_from_log(episode, scenario, log: list[dict]) -> dict[str, Any]:
    """The probe-consumed trace dict built from the episode + its
    schema-v1.1 log. Each registered entry contributes its payload scalar
    (value/count/k/variance/at_turn — first present wins); fields absent
    from the log stay absent so probe-side defaults (e.g. 999 for
    surfaced_within_turn, False for booleans) apply — absence is measured
    non-occurrence, never a gap."""
    trace: dict[str, Any] = {
        "scenario_id": episode.scenario_id,
        "seed": episode.seed,
        "arm": episode.arm,
    }
    if getattr(scenario, "k", None) is not None:
        trace["k"] = scenario.k
    for entry in log:
        field = entry.get("field")
        if not field:
            continue
        payload = entry.get("payload") or {}
        for key in ("value", "count", "k", "variance", "at_turn"):
            if key in payload:
                trace[field] = payload[key]
                break
    return trace


def derive_append(log: list[dict], scenario, expected: set[str]) -> None:
    """Derive emission pass (probe-scorer-owned leg; the judge leg is Task
    9's): append the expected derived/gold_store entries whose values the
    derive pass can produce. Gold-store text fields read the sealed golds;
    derived scalars (envelope-stance deltas, control verdicts, judge
    subscores) land with the Task 9 executor/judge legs — until then their
    absence against `expected` is an honest phase-2 emitter_gap, never a
    silent pass."""
    golds = scenario.golds()
    at = len(log)
    for field in sorted(expected):
        kind = FIELD_EMITTERS[field]
        if kind == "gold_store" and field == "real_defeat_conditions" and golds:
            log.append({"type": "gold_store", "event": "expected", "at": at,
                        "field": field, "payload": {"value": golds[0]}})
            at += 1
        # derived/judge kinds: derive semantics are executor-owned (Task 9)


class ProbeScorer:
    """Probe→Scorer adapter: probes implement ``score(trace, gold,
    threshold)``; the run seam is ``Scorer.score(episode, scenario,
    rubric_id=None)``. Returns the no-data sentinel (None ->
    ``insufficient_n`` cell) when the episode's pre-scoring expected-coverage
    check is gapped — never a measured value from an uncovered log."""

    def __init__(self, probe, thresholds):
        self.probe = probe
        self._thresholds = thresholds
        self.family = getattr(probe, "probe_id", None) or type(probe).__name__
        self._records: list[ProbeRecord] = []
        self._last: ProbeRecord | None = None

    # -- scorer-seam API ---------------------------------------------------
    def expected_coverage(self, scenario, *, run_mode: str = "real") -> set[str]:
        """The per-episode expected set the runner computes BEFORE scoring
        (scoring precedes artifact construction)."""
        return expected_coverage_for(scenario, run_mode=run_mode)

    def score(self, episode, scenario,
              rubric_id: str | None = None) -> ScorerResult:
        run_mode = getattr(episode, "run_mode", "mock")
        expected = expected_coverage_for(scenario, run_mode=run_mode)
        if run_mode != "real" or not episode.valid:
            # Mock is never scored as real: no real log exists, nothing to
            # derive or measure — the sentinel record feeds the
            # all-insufficient_n family cell (mock never false-flags a gap).
            # Excluded (terminal-failure) episodes likewise never produce
            # measured cells from a truncated trace.
            self._record(None, episode)
            return ScorerResult(metrics=())
        # Phase 1: pre-scoring gate over the pre-derivation log.
        uncovered = validate_emitter_coverage(
            episode.event_log, expected=phase1_expected(expected))
        if uncovered:
            self._record(None, episode)
            return ScorerResult(metrics=())
        # Derive emission: append expected derived/gold entries the derive
        # pass owns (mutates the episode log -> the artifact assembler's
        # phase-2 validation sees the POST-derivation log).
        derive_append(episode.event_log, scenario, expected)
        trace = trace_from_log(episode, scenario, episode.event_log)
        gold = scenario.golds()[0] if scenario.golds() else None
        threshold = self._threshold(episode)
        result = self.probe.score(trace, gold, threshold)
        self._record(result.value, episode, measured=True)
        return ScorerResult(metrics=())

    def _threshold(self, episode) -> float:
        from battery.probes.base import load_probe_thresholds
        return load_probe_thresholds(self._thresholds,
                                     self.probe.cal_metric, episode.arm,
                                     default=0.0)

    def _record(self, value: float | None, episode,
                measured: bool = False) -> None:
        rec = ProbeRecord(family=self.family, arm=episode.arm,
                          metric=self.probe.cal_metric, value=value,
                          measured=measured, valid=episode.valid)
        self._records.append(rec)
        self._last = rec

    def last_record(self) -> ProbeRecord | None:
        return self._last

    def family_report(self) -> dict | None:
        """Per-family JSON payload (pinned Task-5 schema: family, n, values:
        {metric: [v...]}, cells: {metric: measured|insufficient_n}).
        None when no episode was scored (the family was never attempted)."""
        if not self._records:
            return None
        arms = {r.arm for r in self._records}
        if len(arms) != 1:
            raise ConfigError(
                f"probe family {self.family!r} aggregation is single-arm in "
                f"this slice (records span {sorted(arms)}) — multi-arm probe "
                f"runs land with the Task 9 executor")
        arm = next(iter(arms))
        metric = self._records[0].metric
        measured = [r.value for r in self._records
                    if r.measured and r.valid]
        return {
            "family": self.family,
            "arm": arm,
            "n": len(measured),
            "values": {metric: [float(v) for v in measured]},
            "cells": {metric: ("measured" if measured
                               else "insufficient_n")},
        }


def resolve_probe_scorer(spec: str, thresholds) -> ProbeScorer:
    """Resolve a probe-module spec (e.g. ``battery.probes.r1_contradiction``
    or ``probes.r1_contradiction``) into a ProbeScorer adapter. The module
    must declare exactly one local ``*Probe`` class with a ``probe_id``."""
    import importlib
    module_name = spec if spec.startswith("battery") else f"battery.{spec}"
    try:
        mod = importlib.import_module(module_name)
    except Exception as e:
        raise ConfigError(f"cannot resolve probe scorer {spec!r}: {e}") from e
    candidates = [
        obj for obj in vars(mod).values()
        if isinstance(obj, type)
        and obj.__module__ == mod.__name__
        and obj.__name__.endswith("Probe")
        and getattr(obj, "probe_id", None)
    ]
    if len(candidates) != 1:
        raise ConfigError(
            f"cannot resolve probe scorer {spec!r}: expected exactly one "
            f"local *Probe class in {module_name}, found "
            f"{[c.__name__ for c in candidates]}")
    return ProbeScorer(probe=candidates[0](), thresholds=thresholds)
