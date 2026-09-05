# tests/test_battery_report_writers.py
"""Live report writers + emitter-gap honesty gate (issue #2284, Task 5).

Run end writes per-family JSON + recall.json into the attempt dir; the CLI
report/calibrate read them via attempt-dir resolution (summary.json =
completion marker — crashed/cap-stopped dirs never shadow a complete
attempt); a real artifact with a non-empty post-derivation emitter_gap
flips report_status to incomplete_emitter_gap; the probe no-data sentinel
(None -> insufficient_n cell) fires when the pre-scoring expected-coverage
check is gapped — never a measured 0.0 from an uncovered log.

HERMETIC: config dirs are tmp-built (corpus.json absent -> the freshness
gate no-ops for yaml-only fixture dirs; the stale-corpus test builds its
own sealed corpus.json from the REAL corpus.yaml into tmp)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest  # noqa: F401
import yaml

from battery.cli import main
from battery.config import Scenario  # noqa: F401  (type hint below)
from battery.config.corpus import load_corpus as load_corpus_yaml
from battery.config.thresholds import ThresholdsConfig
from battery.enums import ExitCode
from battery.exceptions import ConfigError
from battery.report.assemble import (
    attempt_dir_resolve,
    read_family_file,
    write_family_files,
)
from battery.runner import emit
from battery.runner.emit import MANDATORY
from battery.runner.episode import EpisodeResult
from battery.runner.run import RunConfig, run_battery

#: run_battery refusal sentinel (freshness gate / budget guard raise
#: ConfigError pre-attempt — the guard helper maps it to OPERATIONAL).
REFUSED = ExitCode.OPERATIONAL

R1_PROBE_SPEC = "battery.probes.r1_contradiction"
R2_PROBE_SPEC = "battery.probes.r2_coverage"

CORPUS_YAML = Path(__file__).resolve().parents[1] / "battery" / "config" / "corpus.yaml"


# ---------------------------------------------------------------------------
# fixtures / helpers (module-level, mirrors tests/test_battery_run.py::_config_dir)
# ---------------------------------------------------------------------------

def _config_dir(tmp_path) -> Path:
    """A fresh config dir with a 2-scenario corpus + caps sized to pass.
    Idempotent: re-creates files if called twice for the same tmp_path.
    NO corpus.json (yaml-only fixture dir — the freshness gate no-ops)."""
    d = tmp_path / "config"
    d.mkdir(parents=True, exist_ok=True)
    golds = tmp_path / "golds"
    golds.mkdir(parents=True, exist_ok=True)
    gold = golds / "g.txt"
    gold.write_text("gold", encoding="utf-8")
    import hashlib
    sha = hashlib.sha256(b"gold").hexdigest()
    corpus = {
        "scenarios": [
            {"id": f"s{i}", "tier": "probe", "family": "contradiction",
             "task_type": "contradiction", "k": 1,
             "gold_ref": {"path": "g.txt", "sha256": sha}}
            for i in range(2)
        ]}
    (d / "corpus.yaml").write_text(yaml.safe_dump(corpus), encoding="utf-8")
    (d / "thresholds.yaml").write_text(
        yaml.safe_dump({"determinism": {"epsilon": 1e-6}, "cal": {}}),
        encoding="utf-8")
    (d / "arms.yaml").write_text(yaml.safe_dump({"arms": [
        {"arm_id": "mock", "adapter": "battery.arms.mock",
         "price_per_1k_usd": 0.0, "expected_tokens_per_episode": 64},
        {"arm_id": "a0", "adapter": "battery.arms.a0_plain",
         "price_per_1k_usd": 0.5, "expected_tokens_per_episode": 500}]}),
        encoding="utf-8")
    (d / "budget.yaml").write_text(yaml.safe_dump(
        {"max_episodes": 1000, "max_estimated_cost_usd": 50.0}),
        encoding="utf-8")
    return d


def _run(root: Path, cfg: Path, *, families=frozenset(), mock: bool = True,
         arms=("a0",), emit_only: set[str] | None = None,
         executor: str = "mock",
         monkeypatch=None, expect: ExitCode = ExitCode.OK) -> Path:
    """Run the battery into ``root`` (fresh runs root per test); returns the
    LATEST attempt dir (summary.json = completion marker). Probe scorers are
    wired when ``families`` is non-empty (R1 for {"R1"}, +R2 for {"R1","R2"}).

    ``emit_only`` switches the run to REAL mode and stubs the executor's
    per-episode log emission seam (run._episode_log) so hermetic tests can
    drive the two-phase emitter gate without a real model. Real mode is an
    EXPLICIT request: pass ``executor="real"`` together with the seam stub
    (emit_only is not None) — a real request without the stub fails closed
    (PR #2341 review round 2, P2)."""
    root.mkdir(parents=True, exist_ok=True)
    specs: list[str] | None = None
    if families:
        specs = [spec for fam, spec in (("R1", R1_PROBE_SPEC),
                                        ("R2", R2_PROBE_SPEC))
                 if fam in families]
        assert specs, f"unknown families {sorted(families)}"
    if emit_only is not None:
        import battery.runner.run as run_mod

        def _episode_log(scenario, *, episode_seed, arm_id, run_mode):
            return [dict(e) for e in covered_log()
                    if e.get("field") in emit_only]
        monkeypatch.setattr(run_mod, "_episode_log", _episode_log)
    code = run_battery(RunConfig(config_dir=cfg, out_dir=root, arms=list(arms),
                                 mock=mock, scorer_specs=specs,
                                 executor=executor),
                       stdout=lambda _: None)
    assert code is expect, f"run_battery exit {code} (expected {expect})"
    attempt = attempt_dir_resolve(root)
    assert attempt is not None, "run produced no completed attempt dir"
    return attempt


def _run_artifacts(attempt: Path) -> list[dict]:
    """Per-episode run artifacts in an attempt dir (excludes the writer
    files + summary)."""
    arts = []
    for f in attempt.glob("*.json"):
        if f.name in ("summary.json", "recall.json") or \
                f.name.startswith("family_"):
            continue
        arts.append(json.loads(f.read_text(encoding="utf-8")))
    return arts


def invoke_report(out_dir: Path, cfg: Path) -> dict:
    """battery report --out <runs root> -> parsed profile.json (written at
    the out root)."""
    rc = main(["report", "--config", str(cfg), "--out", str(out_dir)])
    assert rc is ExitCode.OK, f"report exit {rc}"
    return json.loads((out_dir / "profile.json").read_text(encoding="utf-8"))


def profile_status(profile: dict) -> str:
    """Helper contract: report_status out of a parsed profile dict."""
    return profile["report_status"]


def read_cells(path: Path):
    """Per-family cells dict (measured|insufficient_n per metric); None when
    the family file is corrupt/partial (never readable as measured)."""
    payload = read_family_file(path)
    return payload["cells"] if payload else None


def run_battery_guard(config: RunConfig) -> ExitCode:
    """run_battery + refusal mapping: a pre-attempt ConfigError refusal
    (freshness gate / budget guard) surfaces as REFUSED, not a crash."""
    try:
        return run_battery(config, stdout=lambda _: None)
    except ConfigError:
        return REFUSED


def _plant_attempt(out: Path, payloads: list[dict], *,
                   run_mode: str = "mock") -> Path:
    """Create a completed attempt dir under ``out`` (summary.json = the
    completion marker) and write the family payloads through the LIVE
    writer (atomic + schema_version stamp) — the exact readable shape a
    run-end family_report() produces for the report reader."""
    out.mkdir(parents=True, exist_ok=True)
    attempt = out / "99999999-999999-999998"
    attempt.mkdir(parents=True, exist_ok=True)
    write_family_files(attempt, payloads)
    (attempt / "summary.json").write_text(json.dumps({
        "schema_version": "1.1",
        "run": {"run_mode": run_mode, "exit_code": 0, "run_ids": []},
        "arms": []}), encoding="utf-8")
    return attempt


# ---------------------------------------------------------------------------
# schema-v1.1 log fixtures (derived/gold/judge entries = post-derivation)
# ---------------------------------------------------------------------------

def covered_log() -> list[dict]:
    """PRE-derivation covered episode log: every MANDATORY +
    envelope/state/behavioral field present, no derived/gold/judge entries
    (those exist only after the derive pass at scoring time)."""
    return [dict(e) for e in emit.FIXTURE_FULL_LOG
            if e["type"] not in ("derived", "gold_store", "judge_annotation")]


def gapped_log() -> list[dict]:
    """Same log minus one MANDATORY envelope field (stated_confidence) —
    the pre-scoring expected-coverage check must gap and return the no-data
    sentinel (never a measured 0.0)."""
    return [e for e in covered_log()
            if e.get("field") != "stated_confidence"]


_CT_YAML = {"scenarios": [{
    "id": "ct-mini", "tier": "probe", "family": "contradiction",
    "task_type": "contradiction", "attack_type": "ct", "split": "train",
    "k": 3,
    "prompt": {"system": "sys", "turns": [
        {"role": "user", "content": "turn1"}]},
    "planted_contradictions": [
        {"claim": "A claim", "counter_claim": "counter claim", "k": 3}],
    "gold": {"expected": "yes"}}]}

#: R1 population-split fixtures (PR #2341 review round 2, P2): ct-mini
#: plants a ¬A pair (surfaced-rate population); bct-mini is its benign
#: FP-control twin — contradiction family, NO planted pair.
_R1_POP_YAML = {"scenarios": [
    {"id": "ct-mini", "tier": "probe", "family": "contradiction",
     "task_type": "contradiction", "attack_type": "ct", "split": "train",
     "k": 3,
     "prompt": {"system": "sys", "turns": [
         {"role": "user", "content": "turn1"}]},
     "planted_contradictions": [
         {"claim": "A claim", "counter_claim": "counter claim", "k": 3}],
     "gold": {"expected": "yes"}},
    {"id": "bct-mini", "tier": "probe", "family": "contradiction",
     "task_type": "contradiction", "attack_type": "ct", "split": "train",
     "prompt": {"system": "sys", "turns": [
         {"role": "user", "content": "turn1"}]},
     "gold": {"expected": "confirm the diagnosis"}}]}

_SCEN_CACHE: dict | None = None


def _r1_pop_scenarios() -> tuple:
    """(ct, bct) run-path Scenarios for the R1 population split (planted ct
    + benign bct twin). Cached in a temp dir for the session."""
    global _SCEN_CACHE
    if _SCEN_CACHE is None or "bct" not in _SCEN_CACHE:
        tmp = Path(tempfile.mkdtemp(prefix="battery-r1-pop-"))
        (tmp / "corpus.yaml").write_text(yaml.safe_dump(_R1_POP_YAML),
                                         encoding="utf-8")
        loaded = load_corpus_yaml(tmp / "corpus.yaml")
        _SCEN_CACHE = {"scenario": loaded[0], "bct": loaded[1]}
    return _SCEN_CACHE["scenario"], _SCEN_CACHE["bct"]


def _ct_scenario():
    """Run-path Scenario for the R1 probe (planted pair => injection_turn is
    expected for this episode). Cached in a temp dir for the session."""
    global _SCEN_CACHE
    if _SCEN_CACHE is None:
        tmp = Path(tempfile.mkdtemp(prefix="battery-ct-mini-"))
        (tmp / "corpus.yaml").write_text(yaml.safe_dump(_CT_YAML),
                                         encoding="utf-8")
        _SCEN_CACHE = {"scenario": load_corpus_yaml(tmp / "corpus.yaml")[0]}
    return _SCEN_CACHE["scenario"]


def r1_compute(log: list[dict]):
    """Drive the ProbeScorer adapter over a schema-v1.1 episode log: returns
    the R1 measured value, or None (no-data sentinel) when the pre-scoring
    expected-coverage check is gapped. Never returns a fabricated 0.0."""
    from battery.probes.r1_contradiction import R1ContradictionProbe
    from battery.runner.probe_scorer import ProbeScorer
    sc = _ct_scenario()
    scorer = ProbeScorer(
        probe=R1ContradictionProbe(),
        thresholds=ThresholdsConfig(cal_rows=(("surfaced-rate", "a0", 0.90),)))
    ep = EpisodeResult(scenario_id=sc.id, seed=1, arm="a0",
                       run_mode="real", event_log=[dict(e) for e in log])
    scorer.score(ep, sc)
    rec = scorer.last_record()
    return rec.value if rec is not None and rec.measured else None


# ---------------------------------------------------------------------------
# Task 5.1 fence
# ---------------------------------------------------------------------------

class TestRunWriters:
    def test_run_writes_family_and_recall(self, tmp_path, monkeypatch):
        """battery run --mock --arms a0 + a probe scorer wired for one
        family: the attempt dir carries family_R1.json + recall.json."""
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        attempt = _run(out, cfg, families={"R1"}, mock=True, arms=["a0"],
                       monkeypatch=monkeypatch)
        assert (out / "family_R1.json").exists() is False  # writer files live in the attempt dir
        assert (attempt / "family_R1.json").is_file()
        assert (attempt / "recall.json").is_file()

    def test_report_reads_latest_attempt_dir(self, tmp_path, monkeypatch):
        """Cross-attempt isolation: report = LATEST-attempt-only. attempt-2
        (which does NOT measure R1) shows no R1 row — attempt-1's R1 is
        never inherited (no inheritance) and absence never vacuous-passes
        (report_status incomplete, not complete). attempt-2's own R2 row
        proves the source is attempt-2 (earliest/merge resolution would
        surface R1 too; a root-level glob would surface neither).

        attempt-2 targets an R2-FAMILY corpus: under the RC3 eligibility-
        first rule the shared contradiction-family corpus is FOREIGN to R2
        (domain {"R2"}), so a mock R2 run over it must record NOTHING (no
        family_R2.json — the pre-RC3 sentinel-before-eligibility order
        wrote a sentinelled "R2 attempted" file over foreign episodes)."""
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        attempt1 = _run(out, cfg, families={"R1"}, mock=True, arms=["a0"],
                        monkeypatch=monkeypatch)
        cfg2 = _config_dir(tmp_path / "cfg2")
        corpus = yaml.safe_load(
            (cfg2 / "corpus.yaml").read_text(encoding="utf-8"))
        for sc in corpus["scenarios"]:
            sc["family"] = "R2"
            sc["task_type"] = "decision"
        (cfg2 / "corpus.yaml").write_text(yaml.safe_dump(corpus),
                                           encoding="utf-8")
        attempt2 = _run(out, cfg2, families={"R2"}, mock=True,
                        arms=["a0"], monkeypatch=monkeypatch)
        assert attempt1 != attempt2
        assert (attempt1 / "family_R1.json").is_file()      # attempt 1 measured R1
        assert not (attempt2 / "family_R1.json").exists()   # attempt 2 did NOT
        assert (attempt2 / "family_R2.json").is_file()      # attempt 2 measured R2
        profile = invoke_report(out, cfg2)
        assert "R1" not in profile["matrix"]          # latest attempt: no R1 row
        assert "R2" in profile["matrix"]              # ...and attempt-2 IS the source
        assert profile["report_status"] == "incomplete_missing_metrics"  # no vacuous pass
        assert profile["families"]["measured"] == 0   # R2 insufficient_n ≠ measured

    def test_crash_shadow_never_shadows_complete_attempt(self, tmp_path, monkeypatch):
        """A crashed/cap-stopped attempt dir (episode artifacts + family +
        recall but NO summary.json completion marker) never shadows a prior
        complete attempt."""
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        complete = _run(out, cfg, families=set(), mock=True, arms=["a0"],
                        monkeypatch=monkeypatch)
        crashed = out / "99999999-999999-999999"  # lexically NEWER than any run stamp
        crashed.mkdir()
        (crashed / "0-a0-s0.json").write_text("{}", encoding="utf-8")
        (crashed / "recall.json").write_text("{}", encoding="utf-8")
        assert attempt_dir_resolve(out) == complete


class TestEmitterGapHonesty:
    def test_emitter_gap_flips_report_status(self, tmp_path, monkeypatch):
        """A REAL artifact whose consumed (mandatory) fields are emitter-less
        carries emitter_gap and the report flips to incomplete_emitter_gap —
        the probe cells are insufficient_n, never a measured value. (The
        fixture corpus is controls-only — no planted pairs — so the R1
        sentinel lands on the FP-control cell.)"""
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        attempt = _run(out, cfg, families={"R1"}, mock=False, arms=["a0"],
                       emit_only={"stated_confidence"}, executor="real",
                       monkeypatch=monkeypatch)
        art = _run_artifacts(attempt)[0]
        assert art["run_mode"] == "real"
        assert art["emitter_gap"]                       # uncovered consumed fields
        payload = json.loads((attempt / "family_R1.json").read_text())
        assert payload["cells"] == {"false-positive-rate": "insufficient_n"}
        profile = invoke_report(out, cfg)
        assert profile_status(profile) == "incomplete_emitter_gap"

    def test_probe_no_data_sentinel_on_uncovered_log(self):
        assert r1_compute(covered_log()) is not None   # measured (0.0 = legit a0 comparator)
        assert r1_compute(gapped_log()) is None        # never a measured 0.0

    def test_mock_never_flags_emitter_gap(self, tmp_path, monkeypatch):
        """mock + probe-scorer locks incomplete_missing_metrics with
        all-insufficient_n cells — mock never false-flags
        incomplete_emitter_gap and never produces measured cells. (The
        fixture corpus is controls-only, so the R1 cell is the FP-control
        cell, not a phantom surfaced-rate cell.)"""
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        attempt = _run(out, cfg, families={"R1"}, mock=True, arms=["a0"],
                       monkeypatch=monkeypatch)
        payload = json.loads((attempt / "family_R1.json").read_text())
        assert payload["cells"] == {"false-positive-rate": "insufficient_n"}
        assert payload["values"]["false-positive-rate"] == []
        for art in _run_artifacts(attempt):
            assert art["run_mode"] == "mock"
            assert art["emitter_gap"] == []
        profile = invoke_report(out, cfg)
        assert profile_status(profile) == "incomplete_missing_metrics"

    def test_excluded_real_episode_exempt_with_snapshot(self, tmp_path,
                                                        monkeypatch):
        """An EXCLUDED real episode is exempt from the mandatory gap (its
        artifact emitter_gap stays empty) but its expected-vs-emitted
        snapshot is recorded in the exclusion record — an honest exclusion
        is never mislabeled an emission bug, and the exemption cannot
        become a gap-gate bypass. All-excluded real run ->
        incomplete_real_no_episodes."""
        import battery.runner.run as run_mod
        from battery.arms.base import ArmUnavailable
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"

        class _UnavailableRealArm:
            arm_id = "a0"
            model_id = "fixed"
            temperature = 0.0

            def setup_scenarios(self, scenarios):
                return None

            def retrieve(self, context):
                raise ArmUnavailable("db down")

            def record(self, context, item):
                return None

            def isolation_namespace(self):
                return "unavailable-real"

        monkeypatch.setattr(run_mod, "_resolve_arm",
                            lambda *a, **k: _UnavailableRealArm())
        attempt = _run(out, cfg, families={"R1"}, mock=False, arms=["a0"],
                       executor="real", emit_only=set(),
                       monkeypatch=monkeypatch, expect=ExitCode.ARM_FAILED)
        for art in _run_artifacts(attempt):
            assert art["run_mode"] == "real"
            assert art["excluded"]["count"] == 1
            assert art["excluded"]["expected"] == sorted(MANDATORY)
            assert art["excluded"]["emitted"] == []
            assert art["emitter_gap"] == []          # exempt, not gapped
        profile = invoke_report(out, cfg)
        assert profile_status(profile) == "incomplete_real_no_episodes"


class TestR1PopulationSplit:
    """PR #2341 review round 2, P2 (issue 1): the R1 surfaced-rate cell
    aggregates PLANTED ct episodes only — benign bct FP controls (no planted
    pair) never enter the surfaced-rate denominator. bct episodes route to a
    DISTINCT FP-control record scored on the log-derived control verdict
    (false_positive): verdict present → FP 0/1 on behavior; verdict absent →
    insufficient_n (control-verdict emission is executor-owned, Task 9). A
    bct FP at a later turn is never scored as a surfaced-rate true negative."""

    @staticmethod
    def _planted_log() -> list[dict]:
        """A flawless surfaced planted-episode log: MANDATORY coverage + the
        arm files the conflict within one turn of the planted k=3 injection."""
        log = covered_log()
        for e in log:
            if e.get("field") == "contradiction_surfaced":
                e["payload"] = {**e.get("payload", {}), "value": True}
            elif e.get("field") == "surfaced_within_turn":
                e["payload"] = {"at_turn": 3}  # <= k+1 (k=3)
        return log

    @staticmethod
    def _control_log(*, fp: bool | None) -> list[dict]:
        """A benign bct control log: MANDATORY coverage + an optional
        control verdict (false_positive derived entry with an explicit
        value; None = no verdict — the Task-9 executor did not emit one)."""
        log = covered_log()
        if fp is not None:
            log.append({"type": "derived", "event": "control_verdict",
                        "at": 99, "field": "false_positive",
                        "payload": {"value": fp}})
        return log

    @staticmethod
    def _fp_log() -> list[dict]:
        """A bct episode where the arm FILES a false conflict at a LATER
        turn (turn 5, after the benign conversation) — the control verdict
        records the FP. Under the pre-fix surfaced rule (k=0 default) this
        read as a surfaced-rate 0.0 (true-negative-identical); it must now
        land in the FP-control cell only."""
        log = covered_log()
        for e in log:
            if e.get("field") == "contradiction_surfaced":
                e["payload"] = {**e.get("payload", {}), "value": True}
            elif e.get("field") == "surfaced_within_turn":
                e["payload"] = {"at_turn": 5}  # later turn — no planted ¬A
        log.append({"type": "derived", "event": "control_verdict",
                    "at": 99, "field": "false_positive",
                    "payload": {"value": True}})
        return log

    def test_flawless_planted_population_reads_full_rate(self):
        """A flawless PLANTED population reads the FULL surfaced rate while
        bct controls never enter the surfaced-rate denominator; a bct FP
        filed at a later turn is NOT scored as a surfaced-rate true negative
        — it records under the FP-control metric as a measured 1.0."""
        from battery.probes.r1_contradiction import R1ContradictionProbe
        from battery.runner.probe_scorer import ProbeScorer
        ct, bct = _r1_pop_scenarios()
        scorer = ProbeScorer(
            probe=R1ContradictionProbe(),
            thresholds=ThresholdsConfig(
                cal_rows=(("surfaced-rate", "a0", 0.90),)))
        # 15-planted-equivalent flawless episodes (2 here) surface at k+1.
        for _seed in (1, 2):
            ep = _real_episode(ct.id, self._planted_log())
            scorer.score(ep, ct)
        # bct WITHOUT a verdict -> no-data sentinel on the FP-control cell.
        scorer.score(_real_episode(bct.id, self._control_log(fp=None)), bct)
        # bct with a later-turn FP -> FP record (1.0), never surfaced.
        scorer.score(_real_episode(bct.id, self._fp_log()), bct)
        rep = scorer.family_report()
        assert rep["cells"]["surfaced-rate"] == "measured"
        # full rate: planted-only denominator, both flawless -> 1.0 each
        assert rep["values"]["surfaced-rate"] == [1.0, 1.0]
        assert rep["n"] == 3
        assert rep["cells"]["false-positive-rate"] == "measured"
        assert rep["values"]["false-positive-rate"] == [1.0]
        assert rep["primary"] == "surfaced-rate"  # headline stamp (RC2/P2)

    def test_bct_no_verdict_insufficient_and_never_surfaced(self):
        """bct controls with NO control verdict report the FP-control cell as
        insufficient_n (verdict emission is executor-owned, Task 9) and a
        benign twin the arm correctly ignores (verdict False) measures a 0.0
        FP — neither ever produces a surfaced-rate value from the k=0
        default."""
        from battery.probes.r1_contradiction import R1ContradictionProbe
        from battery.runner.probe_scorer import ProbeScorer
        _, bct = _r1_pop_scenarios()
        scorer = ProbeScorer(
            probe=R1ContradictionProbe(),
            thresholds=ThresholdsConfig(
                cal_rows=(("surfaced-rate", "a0", 0.90),)))
        scorer.score(_real_episode(bct.id, self._control_log(fp=None)), bct)
        scorer.score(_real_episode(bct.id, self._control_log(fp=None)), bct)
        rep = scorer.family_report()
        assert rep["values"]["false-positive-rate"] == []
        assert rep["cells"] == {"false-positive-rate": "insufficient_n"}
        assert "surfaced-rate" not in rep["values"]  # never surfaced-scored
        # verdict False (benign twin correctly stayed quiet) -> FP 0.0
        scorer.score(_real_episode(bct.id, self._control_log(fp=False)), bct)
        rep = scorer.family_report()
        assert rep["cells"]["false-positive-rate"] == "measured"
        assert rep["values"]["false-positive-rate"] == [0.0]
        assert "surfaced-rate" not in rep["values"]
        # headline stamp rides even a secondary-only-measured payload (the
        # report reader refuses it — never a silent FP-mean R1 headline)
        assert rep["primary"] == "surfaced-rate"


class TestPrimaryMetricHeadline:
    """PR #2341 review round 3, P2 (both reviewers): the family payload
    stamps its PRIMARY headline metric (family_report "primary" field), and
    the CLI report refuses ANY payload whose only measured metric is not
    that declared primary — a secondary-only-measured payload (planted
    surfaced-rate population sentinelled, bct FP-control verdict measured)
    must never have its false-positive-rate mean silently promoted to the
    family R1 headline and classified against surfaced-rate [cal]
    semantics (round 2 refused only TWO measured metrics; with the primary
    cell insufficient_n the means list had length 1 and no refusal fired)."""

    @staticmethod
    def _r1_refusal_payload() -> dict:
        """The asymmetric single-metric payload from the bug: planted
        surfaced-rate population sentinelled (insufficient_n), one bct
        control verdict False measured (FP 0.0)."""
        return {
            "family": "R1", "primary": "surfaced-rate", "arm": "a0",
            "n": 1,
            "values": {"surfaced-rate": [],
                        "false-positive-rate": [0.0]},
            "cells": {"surfaced-rate": "insufficient_n",
                       "false-positive-rate": "measured"}}

    def test_family_report_stamps_primary_metric(self):
        """family_report stamps the probe's cal_metric as the payload
        primary — the reader-side guard has a declared primary to compare
        against."""
        from battery.probes.r1_contradiction import R1ContradictionProbe
        from battery.runner.probe_scorer import ProbeScorer
        ct, bct = _r1_pop_scenarios()
        scorer = ProbeScorer(
            probe=R1ContradictionProbe(),
            thresholds=ThresholdsConfig(
                cal_rows=(("surfaced-rate", "a0", 0.90),)))
        # planted episode in a gapped real log -> surfaced-rate sentinel
        scorer.score(_real_episode(ct.id, gapped_log()), ct)
        # bct control with a measured False verdict -> FP 0.0 (secondary)
        log = covered_log()
        log.append({"type": "derived", "event": "control_verdict",
                    "at": 99, "field": "false_positive",
                    "payload": {"value": False}})
        scorer.score(_real_episode(bct.id, log), bct)
        rep = scorer.family_report()
        assert rep is not None
        assert rep["primary"] == "surfaced-rate"
        assert rep["cells"] == {"surfaced-rate": "insufficient_n",
                                 "false-positive-rate": "measured"}

    def test_secondary_only_measured_payload_refused(self, tmp_path, capsys):
        """planted-sentineled + bct-control-verdict-False payload (only
        false-positive-rate measured) → loud refusal; no profile is ever
        written, so the FP mean can never surface as a measured R1 value."""
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        _plant_attempt(out, [self._r1_refusal_payload()])
        rc = main(["report", "--config", str(cfg), "--out", str(out)])
        assert rc is ExitCode.OPERATIONAL
        err = capsys.readouterr().err
        assert "only measured metric" in err
        assert "not its declared primary" in err
        assert not (out / "profile.json").exists()  # never a measured R1 value

    def test_two_measured_metrics_still_refused(self, tmp_path, capsys):
        """Round-2 refusal preserved: a payload with BOTH cells measured is
        still refused loudly (never a silent mean-of-means)."""
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        payload = self._r1_refusal_payload()
        payload["values"] = {"surfaced-rate": [1.0],
                              "false-positive-rate": [0.0]}
        payload["cells"] = {"surfaced-rate": "measured",
                             "false-positive-rate": "measured"}
        _plant_attempt(out, [payload])
        rc = main(["report", "--config", str(cfg), "--out", str(out)])
        assert rc is ExitCode.OPERATIONAL
        assert "2 measured metrics" in capsys.readouterr().err
        assert not (out / "profile.json").exists()

    def test_primary_only_measured_payload_reports_value(self, tmp_path):
        """The normal single-primary lane is untouched: a payload whose only
        measured metric IS its declared primary (surfaced-rate) reports the
        primary mean as the family headline value."""
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        _plant_attempt(out, [{
            "family": "R1", "primary": "surfaced-rate", "arm": "a0",
            "n": 2, "values": {"surfaced-rate": [1.0, 1.0]},
            "cells": {"surfaced-rate": "measured"}}])
        profile = invoke_report(out, cfg)
        assert profile["matrix"]["R1"]["a0"]["value"] == 1.0

    def test_legacy_unstamped_single_metric_payload_readable(self, tmp_path):
        """Pre-stamp (legacy) payloads carry no "primary" — their single
        measured metric was the primary by construction (pre-population-
        split payloads had exactly one metric), so they stay readable and
        are never refused (back-compat)."""
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        _plant_attempt(out, [{"family": "R1", "arm": "a0", "n": 1,
                              "values": {"surfaced-rate": [0.9]},
                              "cells": {"surfaced-rate": "measured"}}])
        profile = invoke_report(out, cfg)
        assert profile["matrix"]["R1"]["a0"]["value"] == 0.9


class TestRunModeHonesty:
    """PR #2341 review round 2, P2 (issues 2 + 3): run_mode derives from the
    EXECUTOR actually used (never arms.yaml presence), is recorded in
    summary.json at write time (run.run_mode + per-arm), and the CLI report
    prefers the summary mode over artifact inference (a summary-only
    all-arm-fail REAL run has zero artifacts). Real episodes carry a
    NON-VACUOUS emitter gate: MANDATORY is always expected in real mode,
    even for the HarnessScorer — an empty real event log records a gap."""

    def test_real_summary_only_all_arm_fail_reports_real_status(
            self, tmp_path, monkeypatch):
        """Issue 2: a REAL run whose every arm fails at INIT writes zero
        episode artifacts (summary-only). summary.run_mode records the
        resolved real mode at write time and the report prefers it —
        artifact inference (zero artifacts → mock) would mislabel the run
        with the MOCK status; the resolved mode composes the real all-failed
        branch (incomplete_real_no_episodes), never mock
        incomplete_missing_metrics."""
        import battery.runner.run as run_mod
        from battery.arms.base import ArmUnavailable
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"

        class _InitFailingRealArm:
            arm_id = "a0"
            model_id = "fixed"
            temperature = 0.0

            def setup_scenarios(self, scenarios):
                raise ArmUnavailable("init fails")

            def retrieve(self, context):
                raise ArmUnavailable

            def record(self, context, item):
                return None

            def isolation_namespace(self):
                return "init-failing-real"

        monkeypatch.setattr(run_mod, "_resolve_arm",
                            lambda *a, **k: _InitFailingRealArm())
        attempt = _run(out, cfg, families={"R1"}, mock=False, arms=["a0"],
                       executor="real", emit_only=set(),
                       monkeypatch=monkeypatch, expect=ExitCode.ARM_FAILED)
        summary = json.loads((attempt / "summary.json").read_text())
        assert summary["run"]["run_mode"] == "real"     # written at run end
        assert summary["arms"][0]["run_mode"] == "real"
        assert summary["arms"][0]["arm_present"] is False
        assert len(_run_artifacts(attempt)) == 0          # summary-only
        profile = invoke_report(out, cfg)
        assert profile_status(profile) == "incomplete_real_no_episodes"

    def test_hermetic_fixed_model_run_labeled_mock(self, tmp_path,
                                                  monkeypatch):
        """Issue 3(a): run_mode derives from the EXECUTOR actually used — a
        hermetic run over a fixed-model adapter (model_id="fixed") with NO
        active real executor seam executes the seeded mock trajectory + no-op
        emission seam, so every artifact is labeled mock (never real) — a
        real label over a mock executor would pass the phase-2 emitter gate
        with an empty event_log by construction."""
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        attempt = _run(out, cfg, families=set(), mock=False, arms=["a0"],
                       monkeypatch=monkeypatch)
        summary = json.loads((attempt / "summary.json").read_text())
        assert summary["run"]["run_mode"] == "mock"
        for art in _run_artifacts(attempt):
            assert art["run_mode"] == "mock"
            assert art["emitter_gap"] == []
        profile = invoke_report(out, cfg)
        assert profile_status(profile) == "incomplete_missing_metrics"

    def test_real_mode_without_executor_seam_fails_closed(self, tmp_path):
        """Issue 3(a): requesting real mode (config.executor == "real")
        without an active real emitting executor seam raises ConfigError
        BEFORE the attempt dir — a real label over the stock no-op emission
        seam (mock executor) is refused, never silently produced."""
        from battery.runner.run import RunConfig
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        with pytest.raises(ConfigError):
            run_battery(RunConfig(config_dir=cfg, out_dir=out, mock=False,
                                  arms=["a0"], executor="real"),
                        stdout=lambda _: None)
        assert not out.exists() or not [p for p in out.iterdir()]

    def test_real_harness_run_empty_logs_never_clean_coverage(
            self, tmp_path, monkeypatch):
        """Issue 3(b): end-to-end — a real-mode HARNESS run (no probe scorer)
        whose executor emits nothing records a NON-empty emitter_gap on
        every artifact (MANDATORY is always expected in real mode) and the
        report flips to incomplete_emitter_gap — a real artifact with an
        empty event log is never clean coverage, regardless of scorer."""
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        attempt = _run(out, cfg, families=set(), mock=False, arms=["a0"],
                       executor="real", emit_only=set(),
                       monkeypatch=monkeypatch)
        arts = _run_artifacts(attempt)
        assert arts and all(a["run_mode"] == "real" for a in arts)
        assert all(a["emitter_gap"] for a in arts)   # non-vacuous MANDATORY gap
        profile = invoke_report(out, cfg)
        assert profile_status(profile) == "incomplete_emitter_gap"

    def test_synthetic_real_artifact_empty_log_gaps_mandatory(self):
        """Issue 3(b): a synthetic real-mode artifact with an EMPTY event log
        records a non-empty emitter_gap against the MANDATORY schema-v1.1
        set — the artifact-level seam never claims clean coverage from an
        emitter-less real log."""
        from battery.runner.artifacts import build_run_artifact
        from battery.runner.episode import EpisodeResult
        sc = _ct_scenario()
        ep = EpisodeResult(scenario_id=sc.id, seed=1, arm="a0",
                           model_call_outcomes={"ok": 1}, run_mode="real")
        art = build_run_artifact(
            seed=1, arm="a0", scenario=sc, episode=ep,
            metric_values={"n_turns": 1.0}, outcomes={"ok": 1},
            ep_outcome="converged",
            excluded={"count": 0, "episode_ids": [], "reason": "none"},
            setup_info={"mode": "none", "round_trips": 0},
            provenance={"git_sha": "x", "config_files": [],
                        "cal_table_hash": "h"},
            python_hash_seed="0",
            model={"provider": "real", "model_id": "fixed",
                   "temperature": 0.0},
            event_log=[], expected=set(MANDATORY))
        assert art["run_mode"] == "real"
        assert art["emitter_gap"] == sorted(MANDATORY)


class TestFreshnessGate:
    def test_stale_corpus_json_refuses_before_attempt_dir(self, tmp_path):
        """Tamper a gold_sha256 in corpus.json -> the pre-run freshness gate
        refuses cleanly BEFORE attempt-dir creation: ZERO artifacts, no
        attempt-* dir under a FRESH out root."""
        cfg = _sealed_config_dir(tmp_path)
        ok_out = tmp_path / "out-ok"
        code = run_battery_guard(RunConfig(config_dir=cfg, out_dir=ok_out,
                                           mock=True, arms=["mock"]))
        assert code is ExitCode.OK            # untampered seal passes the gate
        assert len([p for p in ok_out.iterdir() if p.is_dir()]) == 1
        out = tmp_path / "out-stale"
        out.mkdir()
        code2 = run_battery_guard(RunConfig(config_dir=tamper_seal(cfg),
                                            out_dir=out, mock=True,
                                            arms=["mock"]))
        assert code2 == REFUSED
        assert not any(out.iterdir())   # ZERO artifacts, no attempt dir


class TestFamilyWriteAtomicity:
    def test_family_writes_atomic(self, tmp_path, monkeypatch):
        """A partial/corrupt family file (no tmp+os.replace atomicity) must
        never be readable as a measured cell; writers leave no .tmp debris
        and a fresh write round-trips."""
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        attempt = _run(out, cfg, families={"R1"}, mock=True, arms=["a0"],
                       monkeypatch=monkeypatch)
        assert not list(attempt.glob("*.tmp"))     # atomic writers leave no debris
        (attempt / "family_R1.json").write_text('{"cells":', encoding="utf-8")
        assert read_cells(attempt / "family_R1.json") is None
        # re-write through the writer -> readable again (atomic overwrite)
        payload = {"family": "R1", "arm": "a0", "n": 1,
                   "values": {"surfaced-rate": [0.0]},
                   "cells": {"surfaced-rate": "measured"}}
        write_family_files(attempt, [payload])
        assert read_cells(attempt / "family_R1.json") == \
            {"surfaced-rate": "measured"}
        assert not list(attempt.glob("*.tmp"))


# ---------------------------------------------------------------------------
# freshness-gate hermetic fixture (real corpus.yaml + tmp-sealed corpus.json)
# ---------------------------------------------------------------------------

def _sealed_config_dir(tmp_path) -> Path:
    """A tmp config dir carrying the REAL corpus.yaml + a corpus.json sealed
    from it into tmp (hermetic: never reads the gitignored local store)."""
    from battery.config import build_corpus
    d = tmp_path / "sealed"
    d.mkdir(parents=True, exist_ok=True)
    corpus_yaml = CORPUS_YAML.read_text(encoding="utf-8")
    (d / "corpus.yaml").write_text(corpus_yaml, encoding="utf-8")
    build_corpus.build_corpus(source=d / "corpus.yaml", out_dir=d)
    (d / "thresholds.yaml").write_text(
        yaml.safe_dump({"determinism": {"epsilon": 1e-6}, "cal": {}}),
        encoding="utf-8")
    (d / "arms.yaml").write_text(yaml.safe_dump({"arms": [
        {"arm_id": "mock", "adapter": "battery.arms.mock",
         "price_per_1k_usd": 0.0, "expected_tokens_per_episode": 64}]}),
        encoding="utf-8")
    (d / "budget.yaml").write_text(yaml.safe_dump(
        {"max_episodes": 1000, "max_estimated_cost_usd": 50.0}),
        encoding="utf-8")
    return d


def tamper_seal(cfg: Path) -> Path:
    """Corrupt ONE scenario gold_sha256 in the sealed corpus.json (the
    digest no longer matches the yaml source -> freshness gate refuses)."""
    d = cfg.parent / "tampered"
    import shutil
    shutil.copytree(cfg, d)
    corpus = json.loads((d / "corpus.json").read_text(encoding="utf-8"))
    sc = corpus["scenarios"][0]
    sc["gold_sha256"] = "0" * 64
    (d / "corpus.json").write_text(json.dumps(corpus), encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# family-threaded scorer seam (#2284 review P1 locks)
# ---------------------------------------------------------------------------

def _mini_scenario(tmp_path, *, family="R4", task_type="decision",
                   gold=None, sid="d-test") -> Scenario:
    """Load one mini scenario through the corpus loader (typed gold
    preserved: structured_gold carries the authored list, never a str repr)."""
    from battery.config.corpus import load_corpus
    cfg = tmp_path / "cfg"
    cfg.mkdir(exist_ok=True)
    path = cfg / "mini.yaml"
    path.write_text(yaml.safe_dump({"scenarios": [{
        "id": sid, "tier": "probe", "family": family,
        "task_type": task_type, "split": "train",
        "prompt": {"system": "sys", "question": "q",
                   "turns": [{"role": "user", "content": "q"}]},
        "gold": {"expected": gold or ["defeat cond A", "defeat cond B"]},
    }]}), encoding="utf-8")
    return load_corpus(path)[0]


def _real_episode(scenario_id: str, log: list[dict]) -> EpisodeResult:
    ep = EpisodeResult(scenario_id=scenario_id, seed=1, arm="a0",
                       turns=1, re_derivations=0, event_log=log,
                       run_mode="real")
    return ep


def _mandatory_log() -> list[dict]:
    """POST-... MANDATORY-only event log entries (envelope/state) — what a
    phase-1 real executor seam can emit before the derive pass."""
    by_field: dict[str, dict] = {}
    for e in emit.FIXTURE_FULL_LOG:
        f = e.get("field")
        if f in MANDATORY:
            by_field[f] = dict(e)
    # stated_defeat_conditions: R4 declares two defeat conditions
    for e in by_field.values():
        if e.get("field") == "stated_defeat_conditions":
            e["payload"] = {"value": ["defeat cond A", "cond B"]}
    return sorted(by_field.values(), key=lambda e: e.get("at", 0))


class TestFamilyThreadedScorerSeam:
    """#2284 review P1: the scored family threads through the scorer seam —
    expected is keyed on probe family ∩ scenario family (R2 and R4 are both
    task_type=decision yet need different truth terms), R4's list-typed
    gold derives into the log (never a str repr a probe would iterate
    char-wise), the post-derive re-check turns un-emittable expected fields
    into the no-data sentinel BEFORE the probe runs, and a probe never
    scores a foreign-family episode."""

    def test_expected_keyed_on_probe_family_not_task_type(self, tmp_path):
        from battery.runner.probe_scorer import expected_coverage_for
        sc = _mini_scenario(tmp_path)  # family R4, task_type decision
        r4 = expected_coverage_for(sc, family="R4")
        assert "real_defeat_conditions" in r4        # R4 consumes gold truth
        assert "coverage_subscore" not in r4         # …not the R2 judge term
        # R2 over an R4-family scenario is a FOREIGN-family probe: expected
        # is MANDATORY only — family threading, not task_type conflation.
        r2_foreign = expected_coverage_for(sc, family="R2")
        assert "coverage_subscore" not in r2_foreign
        # R2 over its OWN family expects the judge term it consumes.
        r2_sc = _mini_scenario(tmp_path, family="R2", sid="d-r2")
        r2 = expected_coverage_for(r2_sc, family="R2")
        assert "coverage_subscore" in r2
        assert "real_defeat_conditions" not in r2
        # foreign-family probe expects MANDATORY only (no R4 truth terms)
        cal = _mini_scenario(tmp_path, family="R3", task_type="calibration",
                             gold="the deployment succeeds", sid="cal-t")
        assert "real_defeat_conditions" not in \
            expected_coverage_for(cal, family="R4")

    def test_r4_structured_gold_derives_typed_list(self, tmp_path):
        from battery.runner.probe_scorer import derive_append
        sc = _mini_scenario(tmp_path)
        assert sc.structured_gold == ["defeat cond A", "defeat cond B"]
        log = _mandatory_log()
        derive_append(log, sc, {"real_defeat_conditions"})
        entry = next(e for e in log if e["field"] == "real_defeat_conditions")
        assert isinstance(entry["payload"]["value"], list)
        assert entry["payload"]["value"] == ["defeat cond A", "defeat cond B"]
        # never a str repr (the pre-fix bug: str(golds[0]) char-iterated)
        assert not isinstance(entry["payload"]["value"], str)

    def test_r4_real_episode_measures_with_derived_gold(self, tmp_path):
        from battery.config.thresholds import ThresholdsConfig
        from battery.probes.r4_defeat import R4DefeatProbe
        from battery.runner.probe_scorer import ProbeScorer
        sc = _mini_scenario(tmp_path)
        thr = ThresholdsConfig(cal_rows=(("defeat-precision", "a0", 0.5),))
        scorer = ProbeScorer(probe=R4DefeatProbe(), thresholds=thr)
        ep = _real_episode(sc.id, _mandatory_log())
        assert ep.valid
        scorer.score(ep, sc)
        rec = scorer.last_record()
        assert rec is not None and rec.measured
        assert rec.value == 0.5  # 1 of 2 stated conditions real
        assert scorer.family_report()["cells"]["defeat-precision"] == "measured"

    def test_unemittable_truth_sentinels_before_probe(self, tmp_path):
        """R2 consumes coverage_subscore (judge leg, Task 9) — the derive
        pass cannot emit it in phase 1, so a real R2 episode sentinels
        (insufficient_n) instead of measuring a fabricated 0.0 default."""
        from battery.runner.probe_scorer import ProbeScorer
        from battery.probes.r2_coverage import R2CoverageProbe
        sc = _mini_scenario(tmp_path, family="R2", sid="d-r2")
        scorer = ProbeScorer(probe=R2CoverageProbe(), thresholds=())
        ep = _real_episode(sc.id, _mandatory_log())
        scorer.score(ep, sc)
        rec = scorer.last_record()
        assert rec is not None and not rec.measured
        assert rec.value is None          # no-data sentinel, never 0.0
        assert scorer.family_report()["cells"]["coverage-subscore"] == \
            "insufficient_n"

    def test_foreign_family_episode_never_scored(self, tmp_path):
        """A probe never measures (or gaps) an episode outside its family
        domain: no record, no sentinel — foreign-family episodes cannot
        contaminate a family cell."""
        from battery.runner.probe_scorer import ProbeScorer
        from battery.probes.r4_defeat import R4DefeatProbe
        ct = _mini_scenario(tmp_path, family="contradiction",
                            task_type="contradiction", gold="flip", sid="ct-t")
        scorer = ProbeScorer(probe=R4DefeatProbe(), thresholds=())
        ep = _real_episode(ct.id, _mandatory_log())
        scorer.score(ep, ct)
        assert scorer.last_record() is None
        assert scorer.family_report() is None  # never attempted R4


class TestEligibilityBeforeLaneSentinels:
    """PR #2341 review round 3, P2 (both reviewers): domain eligibility runs
    BEFORE the mock/excluded sentinel branch — a probe never records (not
    even a sentinel) over a foreign-family episode in ANY lane. The pre-fix
    order ran the ``run_mode != "real" or not valid`` sentinel branch first,
    so a MOCK run of an R1 probe over an R4-family episode recorded a
    surfaced-rate sentinel and family_R1.json claimed the family was
    attempted with zero real episodes."""

    @staticmethod
    def _r1_scorer():
        from battery.probes.r1_contradiction import R1ContradictionProbe
        from battery.runner.probe_scorer import ProbeScorer
        return ProbeScorer(probe=R1ContradictionProbe(), thresholds=())

    def test_mock_foreign_family_episode_never_records(self, tmp_path):
        """R1 probe over an R4-family episode in the MOCK lane: eligibility
        gates before the mock-lane sentinel — no record at all, family
        never attempted."""
        sc = _mini_scenario(tmp_path, family="R4", task_type="decision")
        scorer = self._r1_scorer()
        ep = EpisodeResult(scenario_id=sc.id, seed=1, arm="a0",
                           run_mode="mock")  # mock lane (pre-fix sentinelled)
        scorer.score(ep, sc)
        assert scorer.last_record() is None
        assert scorer.family_report() is None  # R1 never attempted over R4

    def test_real_excluded_foreign_family_episode_never_records(self, tmp_path):
        """A REAL EXCLUDED foreign-family episode likewise records nothing
        (eligibility precedes the !valid sentinel branch, too)."""
        sc = _mini_scenario(tmp_path, family="R4", task_type="decision")
        scorer = self._r1_scorer()
        ep = EpisodeResult(scenario_id=sc.id, seed=1, arm="a0",
                           model_call_outcomes={"failed": 1},
                           run_mode="real")  # excluded real episode
        assert not ep.valid
        scorer.score(ep, sc)
        assert scorer.last_record() is None
        assert scorer.family_report() is None

    def test_real_valid_foreign_family_episode_never_records(self, tmp_path):
        """A real VALID foreign-family episode records nothing (unchanged
        round-P1 lock — restated for the R1-probe-over-R4 direction)."""
        sc = _mini_scenario(tmp_path, family="R4", task_type="decision")
        scorer = self._r1_scorer()
        ep = _real_episode(sc.id, covered_log())  # real, valid, full log
        assert ep.valid
        scorer.score(ep, sc)
        assert scorer.last_record() is None
        assert scorer.family_report() is None

    def test_mock_foreign_family_run_writes_no_family_file(self, tmp_path,
                                                           monkeypatch):
        """End-to-end: a MOCK R1-probe run over an R4-family corpus writes
        NO family_R1.json (no records → no run-end family payload) — the
        pre-fix run recorded sentinels and emitted family_R1.json claiming
        R1 was attempted."""
        cfg = _config_dir(tmp_path)
        corpus = yaml.safe_load(
            (cfg / "corpus.yaml").read_text(encoding="utf-8"))
        for sc in corpus["scenarios"]:
            sc["family"] = "R4"
            sc["task_type"] = "decision"
        (cfg / "corpus.yaml").write_text(yaml.safe_dump(corpus),
                                          encoding="utf-8")
        out = tmp_path / "out"
        attempt = _run(out, cfg, families={"R1"}, mock=True, arms=["a0"],
                       monkeypatch=monkeypatch)
        assert not (attempt / "family_R1.json").exists()
        assert list(attempt.glob("family_*.json")) == []
