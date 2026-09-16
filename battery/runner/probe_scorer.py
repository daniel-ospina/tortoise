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

#2740 truth-emission leg (real lane). The real executor declares the arm's
envelope scalars per turn, but the scorer seam historically never read them
back, so R3 (``confidences``/``outcomes``) and R5
(``update_correct_direction``) always gapped and their families returned the
no-data sentinel. Two legs close what can be closed honestly:
``derive_envelope_truth`` emits the ARM's own resolved per-decision
``confidences`` (raw envelope scalar, no judge); ``derive_judged_truth``
emits the JUDGED truth fields (``outcomes``/``outcome_correct``/
``update_correct_direction``/``coverage_subscore``) ONLY when a configured
truth judge returns an EVIDENCED verdict over the arm's DECLARED position
vs the sealed gold (both sides required). No judge, no declared position,
no gold, an undecidable verdict, or a malformed/un-evidenced verdict leaves
the field absent -> the post-derive re-check sentinels the episode
(``insufficient_n``), never a fabricated ``0.0``. A partially-judged family
refuses its headline (``family_report``) rather than reporting a subset
score as measured.

R1 population split (PR #2341 review round 2, P2): contradiction-family
episodes split at the scorer seam by planted-pair presence — a scenario that
plants a ¬A pair (ct-*, ``contradiction_pairs`` non-empty) is the surfaced-rate
population; a contradiction-family scenario with NO planted pair (bct-* benign
FP surface twins) is an FP-CONTROL episode, routed to a distinct record that
is scored on the log-derived control verdict (``false_positive``) only. A
control episode is NEVER scored on the surfaced rule (no planted ¬A turn →
k=0 → an FP at a later turn would read as a surfaced-rate true negative, and
bct 0.0s in the surfaced-rate pool cap a flawless run at 15/21 < the 0.90
[cal] row). Control-verdict emission is executor-owned (Task 9): an absent
verdict is the no-data sentinel (``insufficient_n``), never a fabricated pass.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from battery.exceptions import ConfigError
from battery.runner.emit import (
    FIELD_EMITTERS,
    MANDATORY,
    SCENARIO_CONDITIONAL,
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
#: task_type alone could never express that. Terms are intersected with
#: SCENARIO_CONDITIONAL (round-4 P2 — every expected truth term is a
#: member of the emit-level constant, which is now LIVE). Fields no probe
#: consumes (outcome_correct, over_reacted, flip_flopped) are never
#: expected — requiring them would gap episodes no probe measures.
_FAMILY_TRUTH_FIELDS: dict[str, frozenset[str]] = {
    # R1 surfaced-rate reads CONDITIONAL tool_event/state fields only
    # (absence = measured non-occurrence, never a gap). false_positive is
    # R1's FP-CONTROL truth term: expected ONLY for CONTROL-population
    # benign-bct episodes whose derived control verdict was emitted (the
    # round-4 gate below) — planted ct episodes NEVER expect a verdict,
    # and a verdict-less control episode keeps the no-verdict sentinel.
    "R1": frozenset({"false_positive"}),
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


def episode_population(scenario) -> str:
    """Population a contradiction-domain episode belongs to (R1 seam):
    ``"planted"`` when the scenario plants a ¬A pair (contradiction_pairs
    non-empty — the surfaced-rate population); ``"control"`` for a
    contradiction-family scenario with NO planted pair (a bct-* benign FP
    surface twin / hermetic benign surrogate — never surfaced-scored).
    Non-contradiction scenarios are ``"planted"`` (the only cell their
    family measures). Preferring the planted-pairs discriminator means the
    run-path Scenario surface needs no control_set field."""
    if getattr(scenario, "contradiction_pairs", ()):
        return "planted"
    if getattr(scenario, "task_type", "") == "contradiction":
        return "control"
    return "planted"


def _control_verdict(log: list[dict]) -> bool | None:
    """The episode's FP-control verdict from the schema-v1.1 log: the
    derived ``false_positive`` entry (control_verdict subtype) carries an
    explicit BOOL payload value — True (the arm wrongly flagged the benign
    surface) or False (it correctly stayed quiet). None when no verdict
    entry with an explicit bool value exists: verdict emission is
    executor-owned (Task 9) — an absent verdict is the no-data sentinel,
    never a fabricated 0.0 pass. Round-4 P2: a verdict-less log that only
    CLAIMS a verdict (entry with no explicit value, or a non-bool value)
    is not a verdict — ``bool("yes")`` coercion must never fabricate a
    measured pass from a malformed claim."""
    for entry in log:
        if entry.get("field") == "false_positive":
            value = (entry.get("payload") or {}).get("value")
            if isinstance(value, bool):
                return value
    return None


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
                          family: str | None = None,
                          log: list[dict] | None = None) -> set[str]:
    """Per-episode expected set (MANDATORY x scenario/family-conditional per
    the Task-1 expectation rule — no arms.yaml capability term). Mock runs
    are neutral (empty expected -> gap empty). injection_turn is expected
    only when the scenario actually plants a ¬A k-turn (never bct twins).

    Round-4 P2 (SCENARIO_CONDITIONAL live): family truth terms are bounded
    by (family-truth ∩ SCENARIO_CONDITIONAL) — the emit-level constant is
    the single source for which scenario/family terms can ever be expected.
    The FP-control verdict term (``false_positive``) is added ONLY for
    CONTROL-population episodes of an FP-family (R1) whose derived control
    verdict is present in ``log`` (post-derivation phase-2 gate): once Task
    9 emits the bct verdict the gate verifies it is covered (fail-closed);
    a verdict-less control episode never expects it and keeps the no-data
    sentinel (insufficient_n), never a fabricated pass.

    ``log`` threads the post-execution event log (the runner computes the
    pre-scoring expected set from the executor's pre-derivation log; the
    probe scorer passes the episode log again so the FP gate can see a
    derived verdict). None (log-less callers) never adds the FP term.

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
        truth = (set(_FAMILY_TRUTH_FIELDS.get(family, ()))
                 & set(SCENARIO_CONDITIONAL))
        # false_positive is the FP-control verdict term — never part of a
        # PLANTED (or foreign-population) episode's expected set: a planted
        # ct episode has no control verdict to emit, so expecting one would
        # gap every planted episode at phase 2.
        expected |= (truth - {"false_positive"})
        if ("false_positive" in truth
                and episode_population(scenario) == "control"
                and log is not None
                and _control_verdict(log) is not None):
            # Control episode of an FP-family AND the derived verdict is
            # actually in the log (post-derivation): require it covered.
            expected |= {"false_positive"}
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


def _scenario_injection_turn(scenario) -> int | None:
    """Scenario-authored ¬A injection turn (k), pinned per planted pair
    (``ContradictionPair.injection_turn``). None when nothing usable is
    planted — a pair without a k is a scenario defect and the absence stays
    a gap, never a guessed turn."""
    for pair in getattr(scenario, "contradiction_pairs", ()) or ():
        k = getattr(pair, "injection_turn", None)
        if isinstance(k, int) and not isinstance(k, bool):
            return k
    return None


def derive_scenario_truth(log: list[dict], scenario) -> None:
    """Pre-expected derivation of SCENARIO-AUTHORED truth (issue #2740, R1
    slice): append the planted ¬A turn before ``expected_coverage_for``
    runs, so a planted episode is measured on R1's surfaced rule instead of
    falling to the no-data sentinel.

    ``injection_turn`` is authored scenario metadata, not arm behaviour —
    the harness knows k when the scenario is written, so emitting it at the
    scoring seam carries exactly the information the executor would have
    carried from the same source. It can never manufacture, hide or
    reinterpret an arm action. Emitted only for a scenario that actually
    plants a pair (never for a benign ``bct`` twin, whose expected set must
    not contain it), and never with a GUESSED turn when a pair carries no k
    (the absence stays an honest gap).

    NOT derived here: ``false_positive`` (the FP-control verdict). It is a
    claim about arm behaviour, and the derive pass cannot see the
    difference between "the arm stayed quiet" and "the tool channel never
    logged a conflict write" — deriving False would convert a missing
    emitter into a measured 0.0, which is exactly the silent pass the
    emitter gate exists to prevent. It stays executor-owned (the executor
    knows whether its own tool channel ran), and a verdict-less control
    keeps the no-verdict sentinel.

    Idempotent: a composite of several probe scorers runs the derive pass
    over the same episode log, so an entry already present is never
    duplicated.
    """
    if ((getattr(scenario, "contradiction_pairs", ()) or ())
            and not any(e.get("field") == "injection_turn" for e in log)):
        k = _scenario_injection_turn(scenario)
        if k is not None:
            log.append({"type": "state_event", "event": "injection_seen",
                        "at": len(log), "field": "injection_turn",
                        "payload": {"k": k}})


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
    for fld in sorted(expected):
        kind = FIELD_EMITTERS[fld]
        # Only list-typed structured gold is derive-emittable: a gold_store
        # field with a scalar/text gold needs Task-9 semantics (the derive
        # pass must never fabricate a typed list from a str repr).
        if (kind == "gold_store" and fld == "real_defeat_conditions"
                and isinstance(structured, list) and structured):
            log.append({"type": "gold_store", "event": "expected", "at": at,
                        "field": fld, "payload": {"value": list(structured)}})
            at += 1
        # other gold_store/derived/judge kinds: executor-owned (Task 9)


# ── #2740 truth-emission leg ───────────────────────────────────────────
# The real-lane arm already DECLARES its envelope scalars per turn, but the
# pre-#2740 scorer seam never read them back, so R3 (`confidences`,
# `outcomes`) and R5 (`update_correct_direction`) always gapped and the
# families returned the no-data sentinel. This leg closes what can be closed
# HONESTLY and leaves the rest gapped:
#
#   * `confidences` — the ARM's own scalar (the resolved per-decision
#     stated confidence), read straight from the envelope's
#     `stated_confidence` log entries. No judge, no gold, no invention:
#     provenance is the validated envelope entry itself.
#
#   * `outcomes` / `outcome_correct` / `update_correct_direction` /
#     `coverage_subscore` — per-decision CORRECTNESS / direction / coverage.
#     These compare the arm's free-text POSITION against the SEALED gold.
#     The arm cannot know them (gold is never rendered agent-side) and prose
#     matching is forbidden, so they are SEMANTIC comparisons: the sanctioned
#     comparator is the judge leg (#2740: "per-decision correctness is a
#     judged semantic ... they need the judge leg wired at the scoring seam,
#     not a derive"). The judge is an injectable seam so the leg is provably
#     hermetic in tests and fail-closed in production:
#       - judge present + EVIDENCED verdict -> emit the typed entry
#       - judge absent / None / malformed / un-evidenced -> emit NOTHING
#         (honest gap; the post-derive re-check turns it into
#         `insufficient_n`, NEVER 0.0).
# The judge reads the arm's DECLARED position (the payload-only envelope
# entry, never the raw turn prose), so the scalar channel contract holds.
#
# #3100 review P1: the comparison needs BOTH sides. With no declared
# position there is no arm-side evidence; with no sealed gold there is
# nothing to be correct against — either missing half means the judge is
# NEVER asked (pre-fix it was asked with `position=""`/`gold=""` and its
# typed verdict landed as a MEASURED cell).
# #3100 review P2-a: the verdict CARRIES its justification (`TruthVerdict`),
# so an un-evidenced bool/float is structurally impossible to emit.


@dataclass(frozen=True)
class TruthVerdict:
    """A judged truth verdict THAT CARRIES ITS JUSTIFICATION (#3100 P2-a).

    The honesty gate — a measurement must have evidence behind it — binds the
    judge as much as the emitter: a bare ``bool``/``float`` that cannot say
    WHAT it compared is an un-evidenced verdict, and emitting it as a
    MEASURED R2/R3/R5 cell is exactly the fabricated-verdict failure this leg
    exists to prevent. ``support`` is the judge's non-empty justification
    (the declared-position-vs-gold evidence it relied on); it is REFUSED at
    construction, so an un-evidenced verdict cannot exist, let alone be
    emitted. It is persisted on the emitted entry's payload so the
    measurement is auditable in the event log.

    ``value`` is the typed verdict: ``bool`` for correctness/direction,
    ``float`` in [0, 1] for the coverage rubric (range enforced by the leg).
    """

    value: bool | float
    support: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, (bool, int, float)):
            raise ValueError(
                f"truth verdict value must be a bool or a real number, got "
                f"{self.value!r}")
        if not isinstance(self.support, str) or not self.support.strip():
            raise ValueError(
                "truth verdict requires non-empty support — a measurement "
                "must carry the evidence it relied on")


@dataclass(frozen=True)
class TruthQuery:
    """One semantic truth comparison handed to the injectable truth judge.

    ``kind`` selects the field group: ``"outcome_correct"`` (R3 — is the
    arm's resolved decision correct against the sealed gold?),
    ``"update_correct_direction"`` (R5 — did the position move in the
    retraction's required direction?) or ``"coverage_subscore"`` (R2 — the
    gated coverage rubric). ``position`` is the arm's FINAL declared position
    from the envelope log; ``positions`` is the full ordered sequence (empty
    strings excluded). ``gold`` is the sealed gold text. ``evidence``
    carries the authored scenario metadata the comparison needs (e.g. the
    ``retraction`` block, the scenario question) — never the arm's prose.

    The judge MUST return an EVIDENCED ``TruthVerdict`` for its kind (bool
    for correctness/direction, float in [0,1] for coverage) or ``None`` when
    it cannot decide: ``None`` keeps the field absent, never a default. A
    bare scalar is refused (#3100 review P2-a) — a verdict that cannot say
    what it compared is not a measurement.
    """

    kind: str
    scenario_id: str
    position: str
    positions: tuple[str, ...]
    gold: str
    evidence: dict[str, Any] = field(default_factory=dict)


#: Injectable truth judge: TruthQuery -> TruthVerdict | None. A real run
#: wires a validated/metered judge (battery/judge/); a hermetic test injects
#: a fake. An absent judge means the judged fields stay gapped. A caller
#: that hands over ``JudgeClient`` in mock mode would be fabricating a
#: scored verdict from a validation-only scorer — the wiring owns that
#: (see RunConfig.truth_judge), the leg cannot detect it generically.
TruthJudge = Callable[["TruthQuery"], "TruthVerdict | None"]


def envelope_scalars(log: list[dict]) -> tuple[list[float], list[str]]:
    """The arm's declared envelope scalars, in emission order: the
    ``stated_confidence`` values and the payload-only ``position`` strings.
    Only entries the envelope channel actually emitted are returned — a log
    without them yields empty lists (an honest gap, never a default)."""
    confs: list[float] = []
    positions: list[str] = []
    for entry in log:
        payload = entry.get("payload") or {}
        if entry.get("field") == "stated_confidence":
            value = payload.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                confs.append(float(value))
        pos = payload.get("position")
        if isinstance(pos, str) and pos.strip():
            positions.append(pos.strip())
    return confs, positions


def _has(log: list[dict], field: str) -> bool:
    return any(e.get("field") == field for e in log)


def derive_envelope_truth(log: list[dict], expected: set[str]) -> None:
    """#2740 executor leg — emit the truth the ARM itself declares.

    ``confidences`` is the episode's resolved (final) per-decision stated
    confidence, read from the envelope's own ``stated_confidence`` entries.
    It is the arm's scalar, not gold: the registry pins the field's emitter
    kind to ``gold_store`` (the scoring-side emission surface), which is a
    contract fact, not a claim that a model stated it.

    Emitted ONLY when the field is expected and the log carries a real
    envelope confidence; a log with no envelope entry stays gapped (the
    sentinel path), never defaulted. Idempotent.
    """
    if "confidences" not in expected or _has(log, "confidences"):
        return
    confs, _ = envelope_scalars(log)
    if not confs:
        return
    log.append({"type": "gold_store", "event": "expected", "at": len(log),
                "field": "confidences", "payload": {"value": [confs[-1]]}})


def derive_judged_truth(log: list[dict], scenario, expected: set[str],
                        judge: TruthJudge | None) -> None:
    """#2740 judge leg — emit the judged truth fields when (and only when) a
    truth judge returns an EVIDENCED verdict.

    Fail-closed: ``judge is None`` emits nothing; a ``None`` / malformed
    return emits nothing; either way the post-derive coverage re-check in
    ``ProbeScorer.score`` turns the missing field into ``insufficient_n`` —
    never a fabricated ``0.0``. Idempotent per field. The judge reads the
    DECLARED envelope position (payload-only entries), never raw prose.

    Evidence gate (#3100 review P1): the comparison needs BOTH sides. With
    no declared position there is no arm-side evidence; with no sealed gold
    there is nothing to be correct against. Either missing half means the
    judge is NEVER asked and the fields stay absent -> sentinel (pre-fix the
    judge was asked with ``position=""``/``gold=""`` and a typed verdict
    became a MEASURED cell with zero arm evidence).
    """
    if judge is None:
        return
    _confs, positions = envelope_scalars(log)
    if not positions:
        return
    gold = scenario.golds()[0] if scenario.golds() else ""
    if not isinstance(gold, str) or not gold.strip():
        return
    evidence: dict[str, Any] = {
        "question": getattr(scenario, "question", ""),
    }
    retraction = getattr(scenario, "retraction", None)
    if retraction:
        evidence["retraction"] = dict(retraction)

    def _ask(kind: str) -> TruthVerdict | None:
        return judge(TruthQuery(
            kind=kind, scenario_id=scenario.id,
            position=positions[-1], positions=tuple(positions),
            gold=gold, evidence=evidence))

    if "outcomes" in expected and not _has(log, "outcomes"):
        verdict = _ask("outcome_correct")
        if isinstance(verdict, TruthVerdict) and isinstance(verdict.value, bool):
            log.append({"type": "gold_store", "event": "expected",
                        "at": len(log), "field": "outcomes",
                        "payload": {"value": [1 if verdict.value else 0],
                                    "support": verdict.support}})
            # Same verdict enables R3's confident-wrong diagnostic; the
            # registry pins it derived/correctness_delta. Emitted only as a
            # consequence of a real evidenced verdict.
            if not _has(log, "outcome_correct"):
                log.append({"type": "derived", "event": "correctness_delta",
                            "at": len(log), "field": "outcome_correct",
                            "payload": {"value": verdict.value,
                                        "support": verdict.support}})
    if ("update_correct_direction" in expected
            and not _has(log, "update_correct_direction")):
        verdict = _ask("update_correct_direction")
        if isinstance(verdict, TruthVerdict) and isinstance(verdict.value, bool):
            log.append({"type": "derived", "event": "direction_ok",
                        "at": len(log), "field": "update_correct_direction",
                        "payload": {"value": verdict.value,
                                    "support": verdict.support}})
    if "coverage_subscore" in expected and not _has(log, "coverage_subscore"):
        verdict = _ask("coverage_subscore")
        if (isinstance(verdict, TruthVerdict)
                and isinstance(verdict.value, (int, float))
                and not isinstance(verdict.value, bool)
                and 0.0 <= float(verdict.value) <= 1.0):
            log.append({"type": "judge_annotation", "event": "rubric_item",
                        "at": len(log), "field": "coverage_subscore",
                        "payload": {"value": float(verdict.value),
                                    "support": verdict.support}})


class ProbeScorer:
    """Probe→Scorer adapter: probes implement ``score(trace, gold,
    threshold)``; the run seam is ``Scorer.score(episode, scenario,
    rubric_id=None)``. Returns the no-data sentinel (None ->
    ``insufficient_n`` cell) when the episode's pre-scoring expected-coverage
    check is gapped — never a measured value from an uncovered log."""

    def __init__(self, probe, thresholds, *, truth_judge: TruthJudge | None = None):
        self.probe = probe
        self._thresholds = thresholds
        #: #2740 injectable semantic-truth comparator. ``None`` (default)
        #: keeps the judged fields gapped (the no-data sentinel) — a run
        #: whose judge is not configured never fabricates correctness.
        self._truth_judge = truth_judge
        self.family = getattr(probe, "probe_id", None) or type(probe).__name__
        self.is_probe = True
        #: FP-control population capability (R1's bct benign twins): a probe
        #: that declares support scores control episodes on its control
        #: verdict under a DISTINCT metric — never on the primary surfaced
        #: rule (control episodes have no planted ¬A turn to surface).
        self.supports_control = bool(getattr(
            probe, "supports_control_population", False))
        self._control_metric = getattr(probe, "control_cal_metric", None)
        self._records: list[ProbeRecord] = []
        self._last: ProbeRecord | None = None

    # -- population helpers ------------------------------------------------
    def _metric_for(self, scenario) -> str:
        """The family-report metric an episode's record belongs to: control
        episodes (benign no-planted-pair contradiction surfaces) record
        under the probe's FP-control metric when it supports the control
        population; everything else records the primary cal metric."""
        if (self.supports_control and self._control_metric
                and episode_population(scenario) == "control"):
            return self._control_metric
        return self.probe.cal_metric

    # -- scorer-seam API ---------------------------------------------------
    def expected_coverage(self, scenario, *, run_mode: str = "real",
                          log: list[dict] | None = None) -> set[str]:
        """The per-episode expected set the runner computes BEFORE scoring
        (scoring precedes artifact construction). The scored family is
        threaded through the seam (Task 5 acceptance) so the runner's
        expected set matches what this probe will actually require. ``log``
        (round-4 P2) carries the episode event log so the FP-control
        verdict term is expected only when the derived verdict was emitted."""
        return expected_coverage_for(scenario, run_mode=run_mode,
                                     family=self.family, log=log)

    def score(self, episode, scenario,
              rubric_id: str | None = None) -> ScorerResult:
        run_mode = getattr(episode, "run_mode", "mock")
        # Eligibility gate FIRST (PR #2341 review round 3, P2): a probe
        # scores ONLY its own family's episodes. Foreign-family episodes are
        # skipped entirely (no record, no sentinel, no expected terms) in
        # ANY lane — a mock (or excluded-real) run over a foreign-family
        # episode must never record a sentinel that claims the family was
        # attempted (the pre-fix order ran the mock/!valid sentinel branch
        # first, so an R1 probe over an R4 episode in a MOCK run recorded a
        # surfaced-rate sentinel and family_R1.json claimed R1 was tried
        # with zero real episodes).
        if scenario.family not in probe_domain(self.family):
            return ScorerResult(metrics=())
        if run_mode != "real" or not episode.valid:
            # Mock is never scored as real: no real log exists, nothing to
            # derive or measure — the sentinel record feeds the
            # all-insufficient_n family cell (mock never false-flags a gap).
            # Excluded (terminal-failure) episodes likewise never produce
            # measured cells from a truncated trace. The sentinel's metric
            # is population-aware so a controls-only run surfaces the
            # FP-control cell, never a phantom surfaced-rate cell.
            self._record(None, episode, metric=self._metric_for(scenario))
            return ScorerResult(metrics=())
        # #2740: the SCENARIO-AUTHORED injection turn must be in the log
        # before the expected set is built (it is a phase-1 state field, so
        # a planted episode gaps without it). Arm-behaviour verdicts are
        # never derived here — see derive_scenario_truth.
        derive_scenario_truth(episode.event_log, scenario)
        expected = expected_coverage_for(scenario, run_mode="real",
                                         family=self.family,
                                         log=episode.event_log)
        # Phase 1: pre-scoring gate over the pre-derivation log.
        uncovered = validate_emitter_coverage(
            episode.event_log, expected=phase1_expected(expected))
        if uncovered:
            self._record(None, episode, metric=self._metric_for(scenario))
            return ScorerResult(metrics=())
        # Derive emission: append expected derived/gold entries the derive
        # pass owns (mutates the episode log -> the artifact assembler's
        # phase-2 validation sees the POST-derivation log).
        derive_append(episode.event_log, scenario, expected)
        # #2740: emit what the arm itself declared through the envelope
        # (per-decision `confidences`) and what a configured truth judge can
        # verify against the sealed gold (`outcomes`,
        # `update_correct_direction`, `coverage_subscore`). Neither leg ever
        # defaults a value: an absent judge or an undecidable verdict leaves
        # the field missing for the post-derive re-check below.
        derive_envelope_truth(episode.event_log, expected)
        derive_judged_truth(episode.event_log, scenario, expected,
                            self._truth_judge)
        # Post-derive re-check over the FULL expected set (review gate):
        # if derive could not emit an expected truth field (e.g. R2/R3/R5
        # fields whose semantics land with the Task-9 judge/executor legs),
        # the no-data sentinel fires BEFORE the probe runs — a probe never
        # produces a measured value from a log where its consumed truth is
        # absent (no fabricated 0.0 / 1.0 brier from probe-side defaults).
        uncovered = validate_emitter_coverage(episode.event_log,
                                              expected=expected)
        if uncovered:
            self._record(None, episode, metric=self._metric_for(scenario))
            return ScorerResult(metrics=())
        # Population split (R1 review P2): a CONTROL episode (benign bct —
        # no planted pair) is scored on the log-derived FP verdict ONLY,
        # never on the surfaced rule (k=0 would score an FP at a later turn
        # as a surfaced-rate true negative and bct 0.0s would dilute the
        # planted surfaced-rate denominator).
        if episode_population(scenario) == "control":
            if not (self.supports_control and self._control_metric
                    and hasattr(self.probe, "score_control")):
                # In-domain control episode for a probe without the control
                # capability: nothing honest to measure — sentinel on the
                # primary metric (unreachable for today's R1 domain).
                self._record(None, episode)
                return ScorerResult(metrics=())
            verdict = _control_verdict(episode.event_log)
            if verdict is None:
                # Control-verdict emission is executor-owned (Task 9): an
                # absent verdict is the no-data sentinel (insufficient_n),
                # never a fabricated 0.0 / surfaced-rate value.
                self._record(None, episode, metric=self._control_metric)
                return ScorerResult(metrics=())
            trace = trace_from_log(episode, scenario, episode.event_log)
            result = self.probe.score_control(trace, threshold=0.0)
            self._record(result.value, episode, measured=True,
                         metric=self._control_metric)
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
                measured: bool = False, metric: str | None = None) -> None:
        rec = ProbeRecord(family=self.family, arm=episode.arm,
                          metric=metric or self.probe.cal_metric, value=value,
                          measured=measured, valid=episode.valid)
        self._records.append(rec)
        self._last = rec

    def last_record(self) -> ProbeRecord | None:
        return self._last

    def family_report(self) -> dict | None:
        """Per-family JSON payload (pinned Task-5 schema: family, primary,
        n, values: {metric: [v...]}, cells: {metric: measured|
        insufficient_n}). R1 may carry TWO cells (population split, PR
        #2341 review P2): the primary cal metric aggregates PLANTED
        episodes only; the FP-control metric (false-positive-rate)
        aggregates benign bct episodes that carried a control verdict
        (Task-9 executor-owned — absent verdicts keep the cell at
        insufficient_n).

        Partial-judging refusal (#3100 review P2): the PRIMARY cell reads
        ``insufficient_n`` whenever some attempted (valid) episode on that
        metric was sentinelled — a judge resolving 1 of N is a subset
        score, never a measured family headline. ``n``/``values`` still
        carry the honest measured subset; the refusal is the cell state.

        ``primary`` stamps the payload's headline cal metric (PR #2341
        review round 3, P2) — the family-level report value is the PRIMARY
        metric by construction; a consumer can refuse a payload whose only
        measured metric is NOT its declared primary (secondary-only-
        measured ⇒ same refusal as a two-measured payload) instead of
        silently promoting a control-metric mean to the family headline.
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
        # Per-metric grouping (record order) — a measured bct verdict never
        # pollutes the planted surfaced-rate cell and vice versa.
        metrics = list(dict.fromkeys(r.metric for r in self._records))
        values: dict[str, list[float]] = {
            m: [float(r.value) for r in self._records
                if r.metric == m and r.measured and r.valid]
            for m in metrics}
        counts = {m: len(values[m]) for m in metrics}
        # Partial-judging guard (#3100 review P2): a metric whose VALID
        # episodes were only partly judged is a SUBSET score. A judge that
        # resolves 1 of N leaves N-1 sentinelled; reporting the family
        # HEADLINE (the declared primary metric) as "measured" off that one
        # verdict would present a partial sample as a measured family. The
        # primary cell is refused (`insufficient_n`) until every attempted
        # episode is judged; `n`/`values` stay the honest measured subset.
        # Secondary/control cells (R1's FP-control) are ROUTED separately and
        # never become the family headline (battery/cli.py), so they keep
        # their existing semantics.
        attempted = {m: sum(1 for r in self._records
                            if r.metric == m and r.valid)
                     for m in metrics}
        primary = self.probe.cal_metric
        cells: dict[str, str] = {}
        for m in metrics:
            partial = m == primary and counts[m] < attempted[m]
            cells[m] = ("insufficient_n"
                        if (not values[m] or partial) else "measured")
        return {
            "family": self.family,
            #: Headline cal metric (RC2/P2): the family-level report value
            #: is the PRIMARY metric by construction.
            "primary": self.probe.cal_metric,
            "arm": arm,
            #: Per-metric measured counts (round-4 P2): the round-3
            #: top-level ``n`` SUMMED both populations (a two-cell R1
            #: payload's n mixed planted surfaced-rate episodes with bct
            #: FP controls). No reader consumed the numeric top-level n
            #: (checked round 4), so ``n`` is now the additive per-metric
            #: map; the primary-metric count is ``n[payload["primary"]]``.
            "n": counts,
            "values": values,
            "cells": cells,
        }


def resolve_probe_scorer(spec: str, thresholds,
                         *, truth_judge: TruthJudge | None = None,
                         ) -> ProbeScorer:
    """Resolve a probe-module spec (e.g. ``battery.probes.r1_contradiction``
    or ``probes.r1_contradiction``) into a ProbeScorer adapter. The module
    must declare exactly one local ``*Probe`` class with a ``probe_id``.

    ``truth_judge`` (#2740) is the injectable semantic comparator threaded
    onto the adapter; default ``None`` keeps the judged truth fields gapped
    (honest sentinel), never defaulted."""
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
    return ProbeScorer(probe=candidates[0](), thresholds=thresholds,
                       truth_judge=truth_judge)
