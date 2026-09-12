"""#2740 truth-emission leg (hermetic) — R3 `confidences`/`outcomes` and R5
`update_correct_direction` on the real lane.

The real executor declares the arm's envelope scalars per turn, but the
scorer seam historically never read them back: every real R3/R5 episode
gapped on the truth fields its probe consumes and the family returned the
no-data sentinel (`insufficient_n`) with `values: []`. This module locks the
two honest legs that close what can be closed:

  1. ``derive_envelope_truth`` — the ARM's own resolved per-decision
     ``confidences``, read straight from the envelope's ``stated_confidence``
     log entries (no judge, no gold, no invention).
  2. ``derive_judged_truth`` — the JUDGED truth fields
     (``outcomes``/``outcome_correct``/``update_correct_direction``/
     ``coverage_subscore``) emitted ONLY when an injected truth judge returns
     an EVIDENCED ``TruthVerdict`` over the arm's DECLARED position vs the
     sealed gold. Both sides of the comparison must be present: with no
     declared position, or no sealed gold, the judge is never asked.

The negative locks are as load-bearing as the positive ones: with no judge,
an undecidable judge, a malformed/unevidenced verdict, a missing declared
position, or a missing gold, the field stays ABSENT and the episode
sentinels — never a fabricated ``0.0``. A real ``0.0`` verdict (judge says
"wrong") must stay a MEASURED 0.0, distinguishable from the sentinel. A
partially-judged family refuses its headline (a subset score is not a
measured family).

Hermetic: NO model/API calls. The judge is a scripted callable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.config.corpus import load_corpus
from battery.config.thresholds import ThresholdsConfig
from battery.probes.r3_calibration import R3CalibrationProbe
from battery.probes.r5_update import R5UpdateProbe
from battery.runner.emit import MANDATORY, validate_event_entry
from battery.runner.episode import EpisodeResult
from battery.runner.executor import Envelope, emitted_trace, envelope_events
from battery.runner.probe_scorer import ProbeScorer, expected_coverage_for

# ── scenario fixtures ─────────────────────────────────────────────────

_R3_YAML = {"scenarios": [{
    "id": "cal-mini", "tier": "probe", "family": "R3",
    "task_type": "calibration", "split": "train",
    "prompt": {"system": "sys", "question": "Will the deployment succeed?",
               "turns": [{"role": "user", "content": "staged deploy"}]},
    "gold": {"expected": "the deployment succeeds with no rollback"}}]}

_R5_YAML = {"scenarios": [{
    "id": "ret-mini", "tier": "probe", "family": "R5",
    "task_type": "retraction", "split": "train",
    "prompt": {"system": "sys", "question": "What caused the outage?",
               "turns": [{"role": "user", "content": "the upgrade did it"},
                         {"role": "user", "content": "retracted"}]},
    "gold": {"expected": "move to the retracted position with measured "
                         "confidence"},
    "retraction": {"claim": "the database upgrade caused the outage",
                   "supporting_evidence": "timestamps",
                   "retraction_event": "the log shows it finished first",
                   "k": 1}}]}


def _scenario(tmp_path, yaml_doc, sid):
    cfg = tmp_path / sid
    cfg.mkdir(parents=True, exist_ok=True)
    path = cfg / "corpus.yaml"
    path.write_text(yaml.safe_dump(yaml_doc), encoding="utf-8")
    return load_corpus(path)[0]


def _r3(tmp_path):
    return _scenario(tmp_path, _R3_YAML, "r3")


def _r5(tmp_path):
    return _scenario(tmp_path, _R5_YAML, "r5")


# ── event-log fixtures (schema-v1.1, registry-valid) ──────────────────


def _envelope_log(confs, positions=None) -> list[dict]:
    """A real-lane pre-derivation log: the MANDATORY envelope/state entries
    plus the payload-only declared ``position`` entries the #2740 executor
    emits. Every entry is registry-validated."""
    log: list[dict] = []
    for i, conf in enumerate(confs):
        for field, value in (
                ("stated_confidence", conf),
                ("stated_undecided", False),
                ("stated_defeat_conditions", [])):
            log.append({"type": "envelope", "event": "declared", "at": i,
                        "field": field, "payload": {"value": value}})
        pos = (positions or [])[i] if positions and i < len(positions) else None
        if pos is not None:
            log.append({"type": "envelope", "event": "declared", "at": i,
                        "payload": {"position": pos}})
    log.append({"type": "state_event", "event": "ep_snapshot", "at": 90,
                "field": "ep_outcome", "payload": {"value": "converged"}})
    log.append({"type": "state_event", "event": "decide_cycle_inc", "at": 91,
                "field": "decide_cycles", "payload": {"value": 3}})
    for entry in log:
        validate_event_entry(entry)
    return log


def _episode(sid: str, log: list[dict]) -> EpisodeResult:
    return EpisodeResult(scenario_id=sid, seed=1, arm="a0", turns=[],
                         event_log=log, run_mode="real")


def _scorer(probe, metric, judge=None):
    thresholds = ThresholdsConfig(cal_rows=((metric, "a0", 0.9),))
    return ProbeScorer(probe=probe, thresholds=thresholds, truth_judge=judge)


class _ScriptedJudge:
    """Scripted truth judge — records every query and returns a staged
    verdict (``TruthVerdict``/``None``, or a deliberately malformed value
    for the fail-closed locks). No model calls."""

    def __init__(self, verdicts):
        self._verdicts = dict(verdicts)
        self.queries = []

    def __call__(self, query):
        self.queries.append(query)
        return self._verdicts.get(query.kind)


def _v(value, support="judged against the sealed gold"):
    """Build an EVIDENCED truth verdict (#3100 review P2-a).

    The pre-fix leg accepted a bare ``bool``/``float``; the guard now
    requires the justification-carrying ``TruthVerdict``."""
    from battery.runner.probe_scorer import TruthVerdict
    return TruthVerdict(value, support)


# ── 1. the envelope position is emitted (pre-fix: dropped) ────────────


class TestEnvelopePositionEmission:
    def test_position_entry_emitted_and_registry_valid(self):
        events = envelope_events(Envelope(
            position="proceed with the rollout", stated_confidence=0.7,
            undecided=False, defeat_conditions=["data loss"]))
        for entry in events:
            validate_event_entry(entry)  # fails closed if malformed
        pos = [e for e in events if "position" in (e.get("payload") or {})]
        assert len(pos) == 1
        assert pos[0]["payload"]["position"] == "proceed with the rollout"
        assert "field" not in pos[0]  # payload-only: never a scalar channel

    def test_position_never_becomes_a_scalar(self):
        """The declared position rides in the log but is invisible to the
        probe scalar trace (trace keying is on `field`): prose can never be
        read as a number."""
        events = envelope_events(Envelope(
            position="0.99", stated_confidence=0.4, undecided=False,
            defeat_conditions=[]))
        trace = emitted_trace(events)
        assert "position" not in trace
        assert trace["stated_confidence"] == 0.4


# ── 2. `confidences` derived from the arm's own envelope scalars ───────


class TestConfidencesFromEnvelope:
    def test_confidences_derived_from_envelope(self):
        from battery.runner.probe_scorer import derive_envelope_truth
        log = _envelope_log([0.6, 0.5, 0.42])
        derive_envelope_truth(log, {"confidences"})
        got = [e for e in log if e.get("field") == "confidences"]
        assert len(got) == 1
        assert got[0]["type"] == "gold_store"
        assert got[0]["event"] == "expected"
        assert got[0]["payload"]["value"] == [0.42]  # resolved (final) decision

    def test_confidences_not_derived_when_not_expected(self):
        from battery.runner.probe_scorer import derive_envelope_truth
        log = _envelope_log([0.6, 0.5])
        derive_envelope_truth(log, {"outcomes"})
        assert not [e for e in log if e.get("field") == "confidences"]

    def test_no_envelope_confidence_stays_gapped(self):
        from battery.runner.probe_scorer import derive_envelope_truth
        log: list[dict] = []
        derive_envelope_truth(log, {"confidences"})
        assert log == []  # never a default

    def test_confidences_leg_is_idempotent(self):
        from battery.runner.probe_scorer import derive_envelope_truth
        log = _envelope_log([0.6, 0.5])
        derive_envelope_truth(log, {"confidences"})
        derive_envelope_truth(log, {"confidences"})
        assert len([e for e in log if e.get("field") == "confidences"]) == 1


# ── 3. judged truth: measured with a judge, sentinel without ──────────


class TestJudgedTruth:
    def test_r3_brier_measured_with_judge(self, tmp_path):
        """ACCEPTANCE (#2740): an R3 real episode whose log carries the
        envelope confidences and a judge verdict is MEASURED (a real brier
        number with n=1) instead of `insufficient_n`."""
        sc = _r3(tmp_path)
        log = _envelope_log([0.9, 0.5, 0.4],
                            ["upgrade caused it", "maybe", "it did not"])
        judge = _ScriptedJudge({"outcome_correct": _v(True)})  # arm was right
        scorer = _scorer(R3CalibrationProbe(), "brier", judge)
        ep = _episode(sc.id, log)
        assert ep.valid
        scorer.score(ep, sc)
        rec = scorer.last_record()
        assert rec is not None and rec.measured
        assert rec.value == pytest.approx((0.4 - 1.0) ** 2)  # final conf vs 1
        report = scorer.family_report()
        assert report["cells"]["brier"] == "measured"
        assert report["n"]["brier"] == 1
        assert judge.queries[0].position == "it did not"
        assert judge.queries[0].gold == \
            "the deployment succeeds with no rollback"

    def test_r5_measured_with_judge(self, tmp_path):
        """ACCEPTANCE (#2740): an R5 real episode with a judge verdict on the
        retraction produces a measured correct-direction cell (n=1)."""
        sc = _r5(tmp_path)
        log = _envelope_log([0.8, 0.2],
                            ["the upgrade caused it", "it did not"])
        judge = _ScriptedJudge({"update_correct_direction": _v(True)})
        scorer = _scorer(R5UpdateProbe(), "correct-direction-rate", judge)
        scorer.score(_episode(sc.id, log), sc)
        rec = scorer.last_record()
        assert rec is not None and rec.measured and rec.value == 1.0
        report = scorer.family_report()
        assert report["cells"]["correct-direction-rate"] == "measured"
        assert report["n"]["correct-direction-rate"] == 1
        # the judge saw the authored retraction metadata + declared position
        q = judge.queries[0]
        assert q.evidence["retraction"]["claim"] == \
            "the database upgrade caused the outage"
        assert q.position == "it did not"

    def test_negative_verdict_is_measured_zero_not_sentinel(self, tmp_path):
        """A judge that says 'wrong' is a MEASURED 0.0 — distinguishable from
        the no-data sentinel. (Fabrication would be the reverse.)"""
        sc = _r5(tmp_path)
        log = _envelope_log([0.8, 0.7], ["held", "held"])
        judge = _ScriptedJudge({"update_correct_direction": _v(False)})
        scorer = _scorer(R5UpdateProbe(), "correct-direction-rate", judge)
        scorer.score(_episode(sc.id, log), sc)
        rec = scorer.last_record()
        assert rec is not None and rec.measured and rec.value == 0.0
        assert scorer.family_report()["cells"]["correct-direction-rate"] == \
            "measured"

    def test_no_judge_keeps_sentinel_never_zero(self, tmp_path):
        """NEGATIVE LOCK: with no configured judge the judged fields are
        absent and both families keep `insufficient_n` with NO value — never
        a fabricated 0.0 from the arm's presence alone."""
        sc3 = _r3(tmp_path)
        log3 = _envelope_log([0.9, 0.5], ["a", "b"])
        scorer3 = _scorer(R3CalibrationProbe(), "brier")  # judge=None
        scorer3.score(_episode(sc3.id, log3), sc3)
        rec3 = scorer3.last_record()
        assert rec3 is not None and not rec3.measured and rec3.value is None
        assert scorer3.family_report()["cells"]["brier"] == "insufficient_n"
        assert not [e for e in log3 if e.get("field") == "outcomes"]
        # confidences IS emitted (the arm's own scalar) — the gap narrowed
        # from confidences+outcomes to outcomes only, and was not papered over
        assert [e for e in log3 if e.get("field") == "confidences"]

        sc5 = _r5(tmp_path)
        log5 = _envelope_log([0.8, 0.2], ["a", "b"])
        scorer5 = _scorer(R5UpdateProbe(), "correct-direction-rate")
        scorer5.score(_episode(sc5.id, log5), sc5)
        rec5 = scorer5.last_record()
        assert rec5 is not None and not rec5.measured and rec5.value is None
        assert scorer5.family_report()["cells"]["correct-direction-rate"] == \
            "insufficient_n"
        assert not [e for e in log5
                    if e.get("field") == "update_correct_direction"]

    def test_undecidable_judge_keeps_sentinel(self, tmp_path):
        """A judge returning None (genuinely undecidable) leaves the field
        absent -> sentinel, never a default."""
        sc = _r5(tmp_path)
        log = _envelope_log([0.8, 0.2], ["a", "b"])
        judge = _ScriptedJudge({"update_correct_direction": None})
        scorer = _scorer(R5UpdateProbe(), "correct-direction-rate", judge)
        scorer.score(_episode(sc.id, log), sc)
        rec = scorer.last_record()
        assert rec is not None and not rec.measured and rec.value is None
        assert not [e for e in log
                    if e.get("field") == "update_correct_direction"]

    def test_malformed_verdict_is_never_coerced(self, tmp_path):
        """A non-typed verdict ('yes', 1, {}) is NOT a judgment: the field
        stays absent (bool coercion must never manufacture a measured pass)."""
        sc = _r5(tmp_path)
        for bad in ("yes", 1, {"value": True}):
            log = _envelope_log([0.8, 0.2], ["a", "b"])
            judge = _ScriptedJudge({"update_correct_direction": bad})
            scorer = _scorer(R5UpdateProbe(), "correct-direction-rate", judge)
            scorer.score(_episode(sc.id, log), sc)
            assert not [e for e in log
                        if e.get("field") == "update_correct_direction"], bad

    def test_no_declared_position_never_judged(self, tmp_path):
        """P1 (#3100 review): the seam's contract is the arm's DECLARED
        position vs the sealed gold. With NO declared position the arm side
        of the comparison is missing, so the judge is NEVER asked and no
        value is emitted -> sentinel. (Pre-fix the judge was called with
        ``position=""`` and its typed verdict was emitted as a MEASURED R5
        cell with zero arm evidence.)"""
        sc = _r5(tmp_path)
        log = _envelope_log([0.8, 0.2])  # no position entries
        judge = _ScriptedJudge({"update_correct_direction": _v(True)})
        scorer = _scorer(R5UpdateProbe(), "correct-direction-rate", judge)
        scorer.score(_episode(sc.id, log), sc)
        assert judge.queries == []                        # no judge call
        assert not [e for e in log
                    if e.get("field") == "update_correct_direction"]
        rec = scorer.last_record()
        assert rec is not None and not rec.measured and rec.value is None
        assert scorer.family_report()["cells"]["correct-direction-rate"] == \
            "insufficient_n"

    def test_no_gold_never_judged(self, tmp_path):
        """P1 (#3100 review): with no SEALED GOLD there is nothing the arm's
        position can be correct against, so the judge is NEVER asked and no
        value is emitted. (Pre-fix ``gold=""`` was handed to the judge and
        its typed verdict became a measured cell.)"""
        from battery.runner.probe_scorer import derive_judged_truth

        class _NoGoldScenario:
            id = "no-gold"
            family = "R5"
            question = "What caused the outage?"

            def golds(self):
                return []

        log = _envelope_log([0.8], ["it did not"])
        judge = _ScriptedJudge({"update_correct_direction": _v(True)})
        derive_judged_truth(log, _NoGoldScenario(),
                            {"update_correct_direction"}, judge)
        assert judge.queries == []                        # no judge call
        assert not [e for e in log
                    if e.get("field") == "update_correct_direction"]

    def test_bare_bool_verdict_refused(self, tmp_path):
        """P2-a (#3100 review): a bare bool/float is an UN-EVIDENCED
        verdict — the leg refuses it. Only an evidenced ``TruthVerdict``
        (non-empty support) is a measurement. (Pre-fix the bare bool was
        accepted and emitted as a measured cell.)"""
        sc = _r5(tmp_path)
        log = _envelope_log([0.8, 0.2], ["a", "b"])
        judge = _ScriptedJudge({"update_correct_direction": True})
        scorer = _scorer(R5UpdateProbe(), "correct-direction-rate", judge)
        scorer.score(_episode(sc.id, log), sc)
        assert judge.queries and judge.queries[0].kind == \
            "update_correct_direction"                     # the judge WAS asked
        assert not [e for e in log
                    if e.get("field") == "update_correct_direction"]
        assert not scorer.last_record().measured

    def test_verdict_carries_its_justification(self, tmp_path):
        """P2-a (#3100 review): an evidenced verdict PERSISTS the evidence
        it relied on, so the measured cell is auditable in the log."""
        sc = _r5(tmp_path)
        log = _envelope_log([0.8, 0.2], ["a", "b"])
        judge = _ScriptedJudge({"update_correct_direction": _v(
            True, "final position matches the retracted cause")})
        scorer = _scorer(R5UpdateProbe(), "correct-direction-rate", judge)
        scorer.score(_episode(sc.id, log), sc)
        entry = next(e for e in log
                     if e.get("field") == "update_correct_direction")
        assert entry["payload"]["value"] is True
        assert entry["payload"]["support"] == \
            "final position matches the retracted cause"

    def test_empty_support_refused_at_construction(self):
        """P2-a (#3100 review): a verdict whose justification is empty
        cannot even be constructed — the hole is structural, not
        conventional."""
        from battery.runner.probe_scorer import TruthVerdict
        for empty in ("", "   ", None):
            with pytest.raises((ValueError, TypeError)):
                TruthVerdict(True, empty)

    def test_partial_judging_refuses_family_headline(self, tmp_path):
        """P2 (#3100 review): a judge resolving 1 of N attempted episodes is
        a SUBSET score — the family headline (primary cell) is refused
        (insufficient_n) while ``n`` stays the honest measured count.
        (Pre-fix the single verdict flipped ``cells[brier]`` to "measured"
        and the N-1 sentinelled episodes were reported as if covered.)"""
        sc = _r3(tmp_path)

        class _Once:
            def __init__(self):
                self.calls = 0

            def __call__(self, query):
                self.calls += 1
                return _v(True) if self.calls == 1 else None

        scorer = _scorer(R3CalibrationProbe(), "brier", _Once())
        scorer.score(
            _episode(sc.id, _envelope_log([0.9, 0.5], ["a", "b"])), sc)
        scorer.score(
            _episode(sc.id, _envelope_log([0.7, 0.6], ["c", "d"])), sc)
        rep = scorer.family_report()
        assert rep["n"]["brier"] == 1                     # honest count kept
        assert len(rep["values"]["brier"]) == 1           # the measured subset
        assert rep["cells"]["brier"] == "insufficient_n"   # partial -> refused

    def test_judged_truth_is_idempotent(self, tmp_path):
        from battery.runner.probe_scorer import derive_judged_truth
        sc = _r5(tmp_path)
        log = _envelope_log([0.8], ["x"])
        judge = _ScriptedJudge({"update_correct_direction": _v(True)})
        expected = {"update_correct_direction"}
        derive_judged_truth(log, sc, expected, judge)
        derive_judged_truth(log, sc, expected, judge)
        assert len([e for e in log
                    if e.get("field") == "update_correct_direction"]) == 1

    def test_r2_coverage_subscore_judged(self, tmp_path):
        from battery.probes.r2_coverage import R2CoverageProbe
        sc = _scenario(tmp_path, {"scenarios": [{
            "id": "d-r2", "tier": "probe", "family": "R2",
            "task_type": "decision", "split": "train",
            "prompt": {"system": "sys", "question": "q",
                       "turns": [{"role": "user", "content": "q"}]},
            "gold": {"expected": "do the thing"}}]}, "r2")
        log = _envelope_log([0.7], ["weigh both sides"])
        judge = _ScriptedJudge({"coverage_subscore": _v(0.75)})
        scorer = _scorer(R2CoverageProbe(), "coverage-subscore", judge)
        scorer.score(_episode(sc.id, log), sc)
        rec = scorer.last_record()
        assert rec is not None and rec.measured
        assert rec.value == pytest.approx(0.75)
        entry = next(e for e in log if e.get("field") == "coverage_subscore")
        assert entry["type"] == "judge_annotation"
        assert entry["payload"]["value"] == pytest.approx(0.75)


# ── 4. the expected set still owns the fields (no gate loosening) ──────


class TestExpectedSetUnchanged:
    def test_r3_r5_truth_terms_still_expected(self, tmp_path):
        r3 = expected_coverage_for(_r3(tmp_path), family="R3")
        assert {"confidences", "outcomes"} <= r3
        assert set(MANDATORY) <= r3
        r5 = expected_coverage_for(_r5(tmp_path), family="R5")
        assert "update_correct_direction" in r5
        assert set(MANDATORY) <= r5

    def test_end_to_end_artifact_gap_narrows_without_judge(self, tmp_path):
        """Without a judge the ARTIFACT still records the (now smaller)
        emitter gap and the family cell stays insufficient_n — the honesty
        gate is never loosened to make a family look measurable."""
        from battery.runner.artifacts import build_run_artifact
        sc = _r3(tmp_path)
        log = _envelope_log([0.9, 0.5], ["a", "b"])
        ep = _episode(sc.id, log)
        scorer = _scorer(R3CalibrationProbe(), "brier")  # no judge
        scorer.score(ep, sc)
        expected = expected_coverage_for(sc, family="R3",
                                         log=ep.event_log)
        expected |= set(MANDATORY)
        artifact = build_run_artifact(
            seed=1, arm="a0", scenario=sc, episode=ep, metric_values={},
            outcomes={}, ep_outcome="converged",
            excluded={"count": 0, "episode_ids": [], "reason": "none"},
            setup_info={}, provenance={}, python_hash_seed=0,
            model={"provider": "openrouter", "model_id": "m",
                   "temperature": 0.0},
            event_log=ep.event_log, expected=expected)
        assert artifact["emitter_gap"] == ["outcomes"]
