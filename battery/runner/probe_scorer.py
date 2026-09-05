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

#: probe family -> scenario-family domain the probe may score. A probe
#: measures (and gaps) ONLY its own family's episodes; foreign-family
#: episodes are skipped entirely (no record — a foreign-family sentinel
#: must never pollute a family cell, and a foreign-family expected set
#: must never emit-gap an artifact the probe did not measure). Hermetic
#: fixtures label contradiction scenarios by task_type ("contradiction")
#: while the real corpus labels them "R1" — both are in-domain.
_FAMILY_DOMAINS: dict[str, frozenset[str]] = {
    "R1": frozenset({"R1", "contradiction"}),
    "R2": frozenset({"R2"}),
    "R3": frozenset({"R3"}),
    "R4": frozenset({"R4"}),
    "R5": frozenset({"R5"}),
}

#: probe family -> expected truth fields that family's probe actually
#: CONSUMES in score() and that are not CONDITIONAL behavioral fields
#: (Task-1 SINGLE RULE: gap only when the scorer seam put a field in
#: `expected` for THIS episode). This is the inverse of the old
#: task_type-keyed map: R2 and R4 are BOTH task_type=decision yet need
#: different truth terms, R3 spans calibration + loopy_contested, and
#: task_type alone could never express that. Fields no probe consumes
#: (outcome_correct, over_reacted, flip_flopped, false_positive) are
#: never expected — nothing downstream reads them, so requiring them
#: would gap episodes no probe measures (the old decision ->
#: {outcome_correct, confidences, outcomes} phantom always-gapped R2).
_FAMILY_TRUTH_FIELDS: dict[str, frozenset[str]] = {
    # R1 surfaced-rate reads CONDITIONAL tool_event/state fields only
    # (absence = measured non-occurrence, never a gap).
    "R1": frozenset(),
    # R2 coverage_subscore = judge_annotation (Task-9 judge leg; sibling B
    # rubric) — derive cannot emit it in phase 1, so a real R2 episode is
    # an honest insufficient/emitter-gap until the judge leg lands.
    "R2": frozenset({"coverage_subscore"}),
    # R3 brier reads per-decision confidences/outcomes (gold-store truth
    # the Task-9 derive leg reads from decision-point golds into the log).
    "R3": frozenset({"confidences", "outcomes"}),
    # R4 defeat-precision reads real_defeat_conditions — gold-store truth
    # derivable NOW from the scenario's structured (list-typed) gold.
    "R4": frozenset({"real_defeat_conditions"}),
    # R5 correct-direction reads the derived update verdict (Task-9
    # derived leg compares stated update vs the sealed retraction gold).
    "R5": frozenset({"update_correct_direction"}),
}


def probe_domain(family: str) -> frozenset[str]:
    """Scenario-family domain for a probe family (exact-label fallback for
    unknown probes — an unregistered probe only scores its own label)."""
    return _FAMILY_DOMAINS.get(family, frozenset({family}))


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


def expected_coverage_for(scenario, *, run_mode: str = "real",
                          family: str | None = None) -> set[str]:
    """Per-episode expected set (MANDATORY x scenario/family-conditional per
    the Task-1 expectation rule — no arms.yaml capability term). Mock runs
    are neutral (empty expected -> gap empty). injection_turn is expected
    only when the scenario actually plants a ¬A k-turn (never bct twins).

    ``family`` threads the SCORED probe family through the seam (Task 5
    acceptance): the episode's expected set is computed for the family that
    will measure it, not from the scenario's raw task_type (which cannot
    separate R2 from R4 — both decision — or R3's loopy_contested slice).
    Out-of-domain scenarios get MANDATORY-only coverage (a real episode's
    schema-v1.1 mandatory envelope/state fields are gated regardless of
    which probe measures it) with NO family truth terms."""
    if run_mode != "real":
        return set()
    expected = set(MANDATORY)
    if getattr(scenario, "contradiction_pairs", ()):
        expected |= {"injection_turn"}
    if family is not None and scenario.family in probe_domain(family):
        expected |= set(_FAMILY_TRUTH_FIELDS.get(family, ()))
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
    9's): append the expected gold_store entries whose values the derive
    pass can produce — the scenario's STRUCTURED gold (list-typed expected,
    e.g. R4's real defeat conditions), never a str-coerced repr. Gold text
    that carries no structure (calibration/retraction statements) is not
    derive-emittable; derived/judge kinds (coverage_subscore, confidences,
    outcomes, update_correct_direction, control verdicts) land with the
    Task 9 executor/judge legs — until then their absence against
    `expected` is an honest gap (the post-derive re-check in score() turns
    it into the no-data sentinel BEFORE the probe runs), never a silent
    pass and never a probe-side default measured as if real."""
    at = len(log)
    structured = getattr(scenario, "structured_gold", None)
    for field in sorted(expected):
        kind = FIELD_EMITTERS[field]
        # Only list-typed structured gold is derive-emittable: a gold_store
        # field with a scalar/text gold needs Task-9 semantics (the derive
        # pass must never fabricate a typed list from a str repr).
        if (kind == "gold_store" and field == "real_defeat_conditions"
                and isinstance(structured, list) and structured):
            log.append({"type": "gold_store", "event": "expected", "at": at,
                        "field": field, "payload": {"value": list(structured)}})
            at += 1
        # other gold_store/derived/judge kinds: executor-owned (Task 9)


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
        self.is_probe = True
        self._records: list[ProbeRecord] = []
        self._last: ProbeRecord | None = None

    # -- scorer-seam API ---------------------------------------------------
    def expected_coverage(self, scenario, *, run_mode: str = "real") -> set[str]:
        """The per-episode expected set the runner computes BEFORE scoring
        (scoring precedes artifact construction). The scored family is
        threaded through the seam (Task 5 acceptance) so the runner's
        expected set matches what this probe will actually require."""
        return expected_coverage_for(scenario, run_mode=run_mode,
                                     family=self.family)

    def score(self, episode, scenario,
              rubric_id: str | None = None) -> ScorerResult:
        run_mode = getattr(episode, "run_mode", "mock")
        if run_mode != "real" or not episode.valid:
            # Mock is never scored as real: no real log exists, nothing to
            # derive or measure — the sentinel record feeds the
            # all-insufficient_n family cell (mock never false-flags a gap).
            # Excluded (terminal-failure) episodes likewise never produce
            # measured cells from a truncated trace.
            self._record(None, episode)
            return ScorerResult(metrics=())
        # Eligibility gate: a probe scores ONLY its own family's episodes.
        # Foreign-family episodes are skipped entirely (no record, no
        # sentinel, no expected terms) — a probe never measures (or gaps)
        # an episode its family does not own, and a mixed tier-1 slice
        # cannot contaminate a family cell with foreign-family sentinels.
        if scenario.family not in probe_domain(self.family):
            return ScorerResult(metrics=())
        expected = expected_coverage_for(scenario, run_mode="real",
                                         family=self.family)
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
        # Post-derive re-check over the FULL expected set (review gate):
        # if derive could not emit an expected truth field (e.g. R2/R3/R5
        # fields whose semantics land with the Task-9 judge/executor legs),
        # the no-data sentinel fires BEFORE the probe runs — a probe never
        # produces a measured value from a log where its consumed truth is
        # absent (no fabricated 0.0 / 1.0 brier from probe-side defaults).
        uncovered = validate_emitter_coverage(episode.event_log,
                                              expected=expected)
        if uncovered:
            self._record(None, episode)
            return ScorerResult(metrics=())
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
