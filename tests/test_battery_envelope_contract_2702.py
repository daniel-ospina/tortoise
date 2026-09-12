"""#2702 — TVDE envelope contract for the ``bct`` / ``cal`` families
(hermetic, ZERO network/spend).

Two independent contract defects made those families unmeasurable on the
real lane. Both are locked here, pre-fix-failing:

1. ``bct`` (benign FP control, R1). The envelope had no decision class for
   a no-op turn — a benign control whose honest answer is "nothing to
   surface" had no machine-readable form, so it was parse-excluded
   (``no JSON envelope block`` / ``position is required``). AND the R1
   FP-control verdict (``false_positive``, the ONLY field the control cell
   consumes) had a registry slot, a probe consumer and a cal metric but NO
   producer — the executor's declared "Task 9 executor-owned" emission
   never landed, so the cell was structurally unreachable
   (``insufficient_n`` forever).

2. ``cal`` (calibration, R3). A calibration turn's answer is a confidence
   about the scenario question, not a narrative stance on every scaffold
   phase; the later ``challenge``/``deepen``/``revise``/``converge`` turns
   therefore emitted no position and killed the episode. The truth leg
   (``outcomes``) needs the #2740 judged-truth seam — this module REUSES it
   (injected scripted judge), it does not add a parallel mechanism.

Honesty is untouched: the envelope is still the only scalar channel (the
new class emits payload-only, invisible to ``trace_from_log``/probes),
prose is never mined, and every absent field still reads as the no-data
sentinel (``insufficient_n``) — never a fabricated ``0.0``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from battery.enums import ExitCode
from battery.runner.emit import validate_event_entry
from battery.runner.executor import (
    Envelope,
    emitted_trace,
    envelope_events,
    surfacing_event,
    validate_envelope,
)

# NOTE: ``control_verdict_event`` / ``control_verdict_from_events`` are the
# #2702 additions — imported INSIDE the tests that lock them, so a pre-fix
# revert of ``battery/runner/*`` yields per-test FAILURES (the contract
# defects) instead of a module-collection ImportError that hides them.
from battery.runner.probe_scorer import TruthVerdict
from battery.runner.run import RunConfig, run_battery

# ── hermetic config fixture ────────────────────────────────────────────

_BCT_SYSTEM = ("You advise a team. Track claims and notice conflicts; never "
               "silently drop an adopted position.")
_CAL_SYSTEM = ("You are a risk analyst. Judge how much each claim should "
               "move your confidence, then state a calibrated confidence.")


def _config_dir(tmp_path: Path) -> Path:
    """Yaml-only config dir (no sealed corpus.json -> the freshness gate
    no-ops). One benign ``bct`` control twin (contradiction family, NO
    planted ¬A pair) + one ``cal`` calibration scenario with PROSE gold
    (the real ``cal-*`` shape — no structured gold is authored)."""
    d = tmp_path / "cfg"
    d.mkdir(parents=True, exist_ok=True)
    corpus = {"scenarios": [
        {"id": "bct-901", "tier": "probe", "family": "R1",
         "task_type": "contradiction", "split": "train",
         "control_set": "bct", "matched_control_for": "ct-901",
         "prompt": {"system": _BCT_SYSTEM,
                    "question": "What should the team do next?",
                    "turns": [{"role": "user",
                               "content": "A follow-up trace confirms server "
                                          "A is the bottleneck."}]},
         # benign twin: a position is a fine answer, but so is a no-op.
         "gold": {"expected": "endorse the confirmed diagnosis"}},
        {"id": "cal-901", "tier": "probe", "family": "R3",
         "task_type": "calibration", "split": "train",
         "prompt": {"system": _CAL_SYSTEM,
                    "question": "Will signups exceed ten thousand?",
                    "turns": [{"role": "user",
                               "content": "The marketing team projects "
                                          "launch-week signups."}]},
         # PROSE gold — the real cal-* shape (#2702 review note).
         "gold": {"expected": "the launch week brings more than ten "
                              "thousand signups"},
         "evidence_tiers": [
             {"tier": "T1", "claim": "the waitlist is at nine thousand",
              "valence": "supports"},
             {"tier": "T4", "claim": "an analyst predicts a crowd",
              "valence": "undercuts"}]},
    ]}
    (d / "corpus.yaml").write_text(yaml.safe_dump(corpus), encoding="utf-8")
    (d / "thresholds.yaml").write_text(
        yaml.safe_dump({"determinism": {"epsilon": 1e-6},
                        "cal": {"surfaced-rate": {"a0": 0.9},
                                "false-positive-rate": {"a0": 0.05},
                                "brier": {"a0": 0.9}}}),
        encoding="utf-8")
    (d / "arms.yaml").write_text(yaml.safe_dump({"arms": [
        {"arm_id": "a0", "adapter": "battery.arms.a0_plain", "config": {},
         "price_per_1k_usd": 0.5, "expected_tokens_per_episode": 100,
         "model_pin": "deepseek/deepseek-v4-flash", "temperature": 0.0}]}),
        encoding="utf-8")
    (d / "budget.yaml").write_text(yaml.safe_dump(
        {"max_episodes": 1000, "max_estimated_cost_usd": 50.0}),
        encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    # resolve_pinned_model constructs the registry adapter (no network on
    # construction); a fake key keeps any env read hermetic.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")


class _Row:
    def __init__(self, ct: int = 20):
        self.completion_tokens = ct


class _ScriptedCallerBase:
    """Meter-protocol caller (the real lane refuses a caller without
    ``spent_usd`` + ``totals()``); ONE JSON envelope per call, no network."""

    model_id = "deepseek/deepseek-v4-flash"
    temperature = 0.0

    def __init__(self):
        self.rows: list[_Row] = []
        self.calls = 0
        self._spend_usd = 0.0001

    @property
    def spent_usd(self) -> float:
        return self._spend_usd

    def totals(self) -> dict:
        return {"calls": self.calls, "prompt_tokens": 0,
                "completion_tokens": sum(r.completion_tokens
                                         for r in self.rows),
                "cost_usd": round(self._spend_usd, 6)}

    def _envelope(self, prompt: str) -> dict:  # pragma: no cover - overridden
        raise NotImplementedError

    def call(self, *, prompt: str) -> str:
        self.calls += 1
        self.rows.append(_Row(20))
        return ("I weigh the evidence before answering.\n"
                + json.dumps(self._envelope(prompt)))


class _BctNoopCaller(_ScriptedCallerBase):
    """The benign-control answer shape: nothing to surface. Pre-#2702 this
    had no envelope form at all (prose-only -> episode excluded)."""

    def _envelope(self, prompt: str) -> dict:
        return {"no_position": True, "stated_confidence": 0.95,
                "undecided": False, "defeat_conditions": [],
                "intents": [], "citations": []}


class _BctFlaggingCaller(_ScriptedCallerBase):
    """A benign control the arm WRONGLY flags: it declares a surfacing
    intent on the benign surface. a0 has no store, so the write lands as
    ``intent_unfiled`` — the declaration is still the false positive."""

    def _envelope(self, prompt: str) -> dict:
        return {"position": "there is a conflict worth surfacing",
                "stated_confidence": 0.8, "undecided": False,
                "defeat_conditions": [], "intents": ["file_nand"],
                "citations": []}


class _CalCaller(_ScriptedCallerBase):
    """cal shape: a directional position + confidence on the FIRST turn
    (where the scenario question is answered), then the later scaffold
    phases honestly declare no new stance instead of fabricating one."""

    def _envelope(self, prompt: str) -> dict:
        if "ALIGN" in prompt:
            return {"position": "yes, signups will exceed ten thousand",
                    "stated_confidence": 0.9, "undecided": False,
                    "defeat_conditions": [], "intents": [],
                    "citations": []}
        return {"no_position": True, "stated_confidence": 0.9,
                "undecided": False, "defeat_conditions": [],
                "intents": [], "citations": []}


class _CalNoPositionCaller(_ScriptedCallerBase):
    """cal shape with NO substantive position ever declared — the honest
    'cannot be judged' case (keeps the sentinel, never a number)."""

    def _envelope(self, prompt: str) -> dict:
        return {"no_position": True, "stated_confidence": 0.9,
                "undecided": False, "defeat_conditions": [],
                "intents": [], "citations": []}


class _ScriptedJudge:
    """Scripted #2740 truth judge (the SAME seam; not a parallel one)."""

    def __init__(self, verdicts):
        self._verdicts = dict(verdicts)
        self.queries = []

    def __call__(self, query):
        self.queries.append(query)
        return self._verdicts.get(query.kind)


def _v(value, support="judged against the sealed gold"):
    return TruthVerdict(value, support)


def _run(tmp_path, caller_factory, *, judge=None, specs=(
        "battery.probes.r1_contradiction",
        "battery.probes.r3_calibration")):
    cfg = _config_dir(tmp_path)
    out = tmp_path / "out"
    code = run_battery(RunConfig(
        config_dir=cfg, out_dir=out, executor="real", arms=["a0"],
        caller_factory=caller_factory, truth_judge=judge, seed=1,
        scorer_specs=list(specs)), stdout=lambda _: None)
    assert code is ExitCode.OK
    attempt = sorted(out.iterdir())[0]
    return attempt


def _family(attempt: Path, family: str) -> dict:
    container = json.loads(
        (attempt / f"family_{family}.json").read_text(encoding="utf-8"))
    return container["arms"]["a0"]


# ── 1. envelope: the no-op decision class ─────────────────────────────


class TestNoPositionDecisionClass:
    def test_noop_class_valid_and_registry_emitted(self):
        env = validate_envelope({"no_position": True, "stated_confidence": 0.5,
                                 "undecided": False})
        assert env.no_position is True and env.position == ""
        events = envelope_events(env)
        for entry in events:
            validate_event_entry(entry)  # fails closed if malformed
        markers = [e for e in events if "no_position" in (e.get("payload") or {})]
        assert len(markers) == 1 and "field" not in markers[0]
        # a no-op turn emits NO position payload at all (never an empty one)
        assert not [e for e in events if "position" in (e.get("payload") or {})]

    def test_noop_class_carries_no_prose_and_no_intents(self):
        """Exactly one decision class per turn: the no-op class is a typed
        declaration, never a prose carrier and never a surfacing."""
        with pytest.raises(ValueError):
            validate_envelope({"no_position": True, "position": "sneak",
                               "stated_confidence": 0.5, "undecided": False})
        with pytest.raises(ValueError):
            validate_envelope({"no_position": True,
                               "intents": ["file_nand"],
                               "stated_confidence": 0.5, "undecided": False})

    def test_absent_position_without_the_class_is_still_rejected(self):
        """The load-bearing negative lock: a MISSING position must never be
        silently reinterpreted as a declaration the arm did not make."""
        with pytest.raises(ValueError):
            validate_envelope({"position": "  ", "stated_confidence": 0.5,
                               "undecided": False})
        with pytest.raises(ValueError):
            validate_envelope({"stated_confidence": 0.5, "undecided": False})
        with pytest.raises(TypeError):
            validate_envelope({"no_position": "yes",
                               "stated_confidence": 0.5, "undecided": False})

    def test_noop_class_is_invisible_to_the_scalar_trace(self):
        """The envelope stays the only scalar channel: the class marker is
        payload-only, so no probe can read it as a number."""
        trace = emitted_trace(envelope_events(Envelope(
            position="", stated_confidence=0.4, undecided=False,
            no_position=True)))
        assert "no_position" not in trace
        assert "position" not in trace
        assert trace["stated_confidence"] == 0.4

    def test_envelope_request_advertises_the_class(self):
        """The model must be TOLD the class exists — otherwise the contract
        extension is unreachable on the real lane."""
        from battery.runner.executor import tvde_prompt
        prompt = tvde_prompt("render", "align")
        assert "no_position" in prompt

    def test_noop_class_is_not_undecided(self):
        """The no-op class must not be conflated with epistemic
        undecidedness (which drives R3's honest-UNDEC branch)."""
        env = validate_envelope({"no_position": True,
                                 "stated_confidence": 0.5,
                                 "undecided": False})
        assert env.undecided is False


class TestDecisionClassListIsSingleSource:
    """Review P2-3: ``ENVELOPE_DECISION_CLASSES`` was dead code. It is now
    the SINGLE SOURCE ``validate_envelope`` enumerates, so the class list
    and the validator cannot drift: a class declared without a witness
    fails closed, and a witnessed class with no validator branch fails
    closed too."""

    def test_every_declared_class_has_a_witness(self):
        import battery.runner.executor as ex
        assert set(ex.ENVELOPE_DECISION_CLASSES) <= set(
            ex._DECISION_CLASS_WITNESS)

    def test_declared_class_without_witness_fails_closed(self, monkeypatch):
        import battery.runner.executor as ex
        from battery.exceptions import ConfigError
        monkeypatch.setattr(ex, "ENVELOPE_DECISION_CLASSES",
                            ("position", "no_position", "sentinel"))
        with pytest.raises(ConfigError):
            ex.validate_envelope({"position": "a real position",
                                  "stated_confidence": 0.5,
                                  "undecided": False})

    def test_witnessed_class_without_validator_fails_closed(self,
                                                           monkeypatch):
        import battery.runner.executor as ex
        from battery.exceptions import ConfigError
        monkeypatch.setattr(ex, "ENVELOPE_DECISION_CLASSES",
                            ("position", "no_position", "sentinel"))
        monkeypatch.setitem(ex._DECISION_CLASS_WITNESS, "sentinel",
                            lambda raw, position: raw.get("sentinel") is True)
        with pytest.raises(ConfigError):
            ex.validate_envelope({"sentinel": True,
                                  "stated_confidence": 0.5,
                                  "undecided": False})


# ── 2. the R1 FP-control verdict producer ─────────────────────────────


class TestControlVerdictDerivation:
    def test_surfacing_filed_reads_true(self):
        from battery.runner.executor import control_verdict_from_events
        events = [surfacing_event(within_turn=2, event_ref="ns:1")]
        assert control_verdict_from_events(events) is True

    def test_no_conflict_claim_reads_false_not_a_default(self):
        """A completed channel with no conflict claim at all reads False (a
        measured non-occurrence), while a declared-but-unfiled SURFACING
        intent still reads True — the false positive is the arm's claim,
        not the product's refusal to file it."""
        from battery.runner.executor import control_verdict_from_events
        quiet = [{"type": "state_event", "event": "intent_unfiled", "at": 1,
                  "payload": {"intent": "mitigate"}}]
        assert control_verdict_from_events(quiet) is False
        claimed = [{"type": "state_event", "event": "intent_unfiled",
                    "at": 1, "payload": {"intent": "file_nand"}}]
        assert control_verdict_from_events(claimed) is True

    def test_malformed_surfacing_never_coerced(self):
        """A non-bool payload must never coerce into a false positive."""
        from battery.runner.executor import control_verdict_from_events
        events = [{"type": "tool_event", "event": "file_nand", "at": 1,
                   "field": "contradiction_surfaced",
                   "payload": {"value": "yes", "event_ref": "ns:1"}}]
        assert control_verdict_from_events(events) is False

    def test_verdict_event_is_registry_valid(self):
        from battery.runner.executor import control_verdict_event
        ev = control_verdict_event(false_positive=True)
        validate_event_entry(ev)
        assert ev["type"] == "derived" and ev["event"] == "control_verdict"
        assert ev["field"] == "false_positive"
        assert ev["payload"]["value"] is True

    def test_verdict_event_rejects_truthy_non_bool(self):
        """The no-coercion contract (review P2-1): a non-bool verdict value
        must be REJECTED, never coerced. ``bool("false")`` is True and
        ``bool("")``/``bool(None)``/``bool(0)`` are False, so coercing a
        malformed channel value manufactures a measured verdict (a
        fabricated false positive, or a fabricated restraint) out of a
        value the arm never declared as a bool. The emitted payload is the
        exact bool it was handed, never a re-derived one."""
        from battery.runner.executor import control_verdict_event
        for bad in ("false", "yes", "", [], [0], {}, 0, 1, None):
            with pytest.raises(TypeError):
                control_verdict_event(false_positive=bad)
        assert control_verdict_event(
            false_positive=False)["payload"]["value"] is False
        assert control_verdict_event(
            false_positive=True)["payload"]["value"] is True


class _WriteFailingControlArm:
    """Real-mode a0-shaped arm whose decide WRITE channel is dead: the
    benign control still DECLARES a surfacing intent, but ``record`` raises
    ``ArmUnavailable`` — the episode never got the chance to file (or not
    file) anything."""

    arm_id = "a0"
    model_id = "deepseek/deepseek-v4-flash"  # real-mode (not mock-agent)
    temperature = 0.0

    def __init__(self, **config):
        self._config = config

    def setup_scenarios(self, scenarios):
        return None

    def retrieve(self, context):
        # one retrieved claim so the declare-write loop actually reaches
        # ``record`` (with an empty claim set the loop records the
        # declared intent as ``intent_unfiled``/empty-claims and never
        # touches the write channel).
        from battery.arms.base import Memory
        return [Memory(id="c1", content="server A is the bottleneck",
                       confidence=0.7, kind="claim")]

    def record(self, context, item):
        from battery.arms.base import ArmUnavailable
        raise ArmUnavailable("product write channel down")

    def isolation_namespace(self):
        return "write-failing-control"


class TestFailedControlNeverManufacturesRestraint:
    """HONESTY GUARD (review P2-2). A control episode whose write channel
    FAILED did not complete the claim loop, so it must leave
    ``false_positive`` ABSENT (family cell ``insufficient_n``) — NEVER
    ``false_positive=False``, which reads as "the arm correctly restrained
    itself": a fabricated virtue manufactured out of a channel that never
    ran. Locks the ``if not write_failed`` gate in the real executor
    against a future change that emits the verdict unconditionally."""

    def test_failed_control_write_absent_not_false(self, tmp_path,
                                                   monkeypatch):
        import battery.runner.run as run_mod
        from battery.runner.probe_scorer import _control_verdict

        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        monkeypatch.setattr(run_mod, "_resolve_arm",
                            lambda *a, **k: _WriteFailingControlArm())
        code = run_battery(RunConfig(
            config_dir=cfg, out_dir=out, executor="real", arms=["a0"],
            caller_factory=_BctFlaggingCaller, seed=1,
            scorer_specs=["battery.probes.r1_contradiction"]),
            stdout=lambda _: None)
        # a dead write channel excludes the episode (never a clean run)
        assert code is ExitCode.ARM_FAILED
        attempt = sorted(out.iterdir())[0]
        arts = [json.loads(p.read_text()) for p in attempt.glob("*.json")
                if p.name not in ("summary.json", "recall.json")
                and not p.name.startswith("family_")]
        bct = next(a for a in arts if a["scenario_id"] == "bct-901")
        assert bct["excluded"]["count"] == 1
        # the load-bearing negative: NO false_positive entry at all — a
        # False here would be the fabricated restraint verdict.
        assert [e for e in bct["event_log"]
                if e.get("field") == "false_positive"] == []
        assert _control_verdict(bct["event_log"]) is None
        # and the family still reports the no-data sentinel, never 0.0
        payload = _family(attempt, "R1")
        assert payload["cells"]["false-positive-rate"] == "insufficient_n"
        assert payload["values"]["false-positive-rate"] == []


# ── 3. bct end-to-end through the envelope ────────────────────────────


class TestBctMeasurableEndToEnd:
    def test_bct_control_episode_measured(self, tmp_path):
        """A benign ``bct`` control answering 'nothing to surface' through
        the new no-op class is (a) NOT excluded and (b) MEASURED on R1's
        FP-control cell (0.0 — correctly quiet), never ``insufficient_n``."""
        attempt = _run(tmp_path, _BctNoopCaller)
        arts = [json.loads(p.read_text()) for p in attempt.glob("*.json")
                if p.name not in ("summary.json", "recall.json")
                and not p.name.startswith("family_")]
        bct = next(a for a in arts if a["scenario_id"] == "bct-901")
        assert bct["excluded"]["count"] == 0  # the no-op class kept it alive
        payload = _family(attempt, "R1")
        assert payload["cells"]["false-positive-rate"] == "measured"
        assert payload["values"]["false-positive-rate"] == [0.0]
        # the control verdict is in the episode's own event log
        log = [e for e in bct["event_log"]
               if e.get("field") == "false_positive"]
        assert len(log) == 1 and log[0]["payload"]["value"] is False

    def test_bct_false_positive_measured_as_one(self, tmp_path):
        """A benign control the arm WRONGLY flags measures 1.0 on the
        FP-control cell — the false positive is the arm's declared
        conflict claim (here an unfiled ``intent_unfiled``), never
        prose-mined and never a defaulted pass."""
        attempt = _run(tmp_path, _BctFlaggingCaller)
        payload = _family(attempt, "R1")
        assert payload["cells"]["false-positive-rate"] == "measured"
        assert payload["values"]["false-positive-rate"] == [1.0]

    def test_planted_ct_episode_never_gets_a_control_verdict(self, tmp_path):
        """A planted ¬A scenario (ct-*) is NOT control-population — it must
        never receive an FP-control verdict."""
        cfg = _config_dir(tmp_path)
        doc = yaml.safe_load((cfg / "corpus.yaml").read_text())
        doc["scenarios"][0].pop("control_set", None)
        doc["scenarios"][0].pop("matched_control_for", None)
        doc["scenarios"][0]["planted_contradictions"] = [
            {"claim": "server A is the bottleneck",
             "counter_claim": "server A is not the bottleneck", "k": 3}]
        (cfg / "corpus.yaml").write_text(yaml.safe_dump(doc),
                                         encoding="utf-8")
        out = tmp_path / "out2"
        code = run_battery(RunConfig(
            config_dir=cfg, out_dir=out, executor="real", arms=["a0"],
            caller_factory=_BctNoopCaller, seed=1,
            scorer_specs=["battery.probes.r1_contradiction"]),
            stdout=lambda _: None)
        assert code is ExitCode.OK
        attempt = sorted(out.iterdir())[0]
        arts = [json.loads(p.read_text()) for p in attempt.glob("*.json")
                if p.name not in ("summary.json", "recall.json")
                and not p.name.startswith("family_")]
        assert not [e for e in arts[0]["event_log"]
                    if e.get("field") == "false_positive"]


# ── 4. cal end-to-end through the envelope ────────────────────────────


class TestCalMeasurableEndToEnd:
    def test_cal_episode_measured_with_the_2740_judge(self, tmp_path):
        """A ``cal`` episode — directional position on ALIGN, honest no-op
        on the later phases, PER-TURN confidence — is MEASURED: R3's brier
        is a real number from the envelope confidences + the #2740 judged
        outcome. No structured gold is authored; the prose gold is compared
        semantically by the judge (never mined by the arm/probe)."""
        judge = _ScriptedJudge({"outcome_correct": _v(True)})
        attempt = _run(tmp_path, _CalCaller, judge=judge)
        arts = [json.loads(p.read_text()) for p in attempt.glob("*.json")
                if p.name not in ("summary.json", "recall.json")
                and not p.name.startswith("family_")]
        cal = next(a for a in arts if a["scenario_id"] == "cal-901")
        assert cal["excluded"]["count"] == 0  # no-op later phases survive
        payload = _family(attempt, "R3")
        assert payload["cells"]["brier"] == "measured"
        assert payload["values"]["brier"] == [pytest.approx(0.01)]  # (0.9-1)²
        assert judge.queries[0].position == \
            "yes, signups will exceed ten thousand"
        assert judge.queries[0].gold == \
            "the launch week brings more than ten thousand signups"

    def test_cal_without_a_judge_stays_sentinel_never_zero(self, tmp_path):
        """No judge configured -> the judged truth field is absent and the
        family reports the no-data sentinel, never a fabricated 0.0."""
        attempt = _run(tmp_path, _CalCaller, judge=None)
        payload = _family(attempt, "R3")
        assert payload["cells"]["brier"] == "insufficient_n"
        assert payload["values"]["brier"] == []

    def test_cal_without_any_position_cannot_be_judged(self):
        """EXPLICIT UNMEASURABLE CASE: an arm that declares no substantive
        position on any turn leaves R3's `outcomes` unsourced. The judged
        leg is never asked, the field stays absent, the cell sentinels —
        an honest 'cannot be measured', never a number."""
        from battery.config.corpus import load_corpus
        from battery.config.thresholds import ThresholdsConfig
        from battery.probes.r3_calibration import R3CalibrationProbe
        from battery.runner.episode import EpisodeResult
        from battery.runner.probe_scorer import ProbeScorer

        scs = load_corpus("battery/config/corpus.yaml",
                          gold_base="battery/golds")
        cal = next(s for s in scs if s.id == "cal-002")
        log = []
        for i in range(4):
            for field, value in (("stated_confidence", 0.9),
                                 ("stated_undecided", False),
                                 ("stated_defeat_conditions", [])):
                log.append({"type": "envelope", "event": "declared", "at": i,
                            "field": field, "payload": {"value": value}})
            log.append({"type": "envelope", "event": "declared", "at": i,
                        "payload": {"no_position": True}})
        log.append({"type": "state_event", "event": "ep_snapshot", "at": 90,
                    "field": "ep_outcome", "payload": {"value": "converged"}})
        log.append({"type": "state_event", "event": "decide_cycle_inc",
                    "at": 91, "field": "decide_cycles",
                    "payload": {"value": 3}})
        for entry in log:
            validate_event_entry(entry)
        judge = _ScriptedJudge({"outcome_correct": _v(True)})
        scorer = ProbeScorer(
            probe=R3CalibrationProbe(),
            thresholds=ThresholdsConfig(cal_rows=(("brier", "a0", 0.9),)),
            truth_judge=judge)
        scorer.score(EpisodeResult(
            scenario_id=cal.id, seed=1, arm="a0", turns=[], event_log=log,
            run_mode="real"), cal)
        rec = scorer.last_record()
        assert rec is not None and not rec.measured and rec.value is None
        assert judge.queries == []  # never asked without an arm-side position
        assert scorer.family_report()["cells"]["brier"] == "insufficient_n"
