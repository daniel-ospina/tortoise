"""#2985 — the shared retrieval preflight + the retrieval provenance contract.

Background (verified, with evidence): the battery's real lanes silently ran
KEYWORD-ONLY retrieval for several runs. ``TortoiseCrMemory.recall`` →
``recall_state`` → ``tortoise_fts_query`` is HYBRID (FTS + vector + structural
RRF), but the local venv lacked ``sentence-transformers`` (the ``embeddings``
extra), so ``EmbeddingModel.get()`` returned None, ``_vec_reason =
"no_embedder"``, and the vector strategy was never submitted. Measured impact:
recall@20 0.80 FTS-only vs **1.00** hybrid on ``factconsolidation_sh_6k`` (0.44
vs 0.61 on ``mh_6k``).

This file pins the parts of the fix that live OUTSIDE the parity capability
gate #3005 owns (``tests/test_battery_parity_vector_guard.py``):

* the shared fail-closed preflight
  (``battery/runner/retrieval_preflight.py``) raises an ACTIONABLE refusal
  when the embedder is unavailable, passes when it is, and caches NOTHING (a
  later-fixed environment is observed on the very next call);
* the module is importable without ``sentence-transformers`` installed;
* ``merge_leg_trace`` — the single interpreter of the product's per-leg trace
  shape;
* the ARM path: ``A4TortoiseArm`` declares ``requires_hybrid_retrieval`` and
  the real runner skips it at arm-init with a recorded ``init_failure``
  naming the preflight (never an FTS-only a4 number), while the arm stays
  callable for hermetic/equivalence tests;
* artifact-level provenance: the observed ``retrieval_legs`` /
  ``retrieval_degraded`` reach the cell ``detail``, ``summary.json`` (run
  level) and ``parity_record.json`` (the CLI used to drop
  ``ExecutedCell.detail`` — #2919);
* ``recall_state(leg_trace=...)`` is a default-None byte-identical
  passthrough that records the legs actually run.

All hermetic: no keys, no network, no model download, no spend. The venv HAS
the ``embeddings`` extra, so "no embedder" cases monkeypatch the product's
singleton.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.arms.base import ArmUnavailable
from battery.enums import ExitCode
from battery.parity.executors import ExecutedCell, ExecutorUnavailable
from battery.parity.mabench import CrConfig, CrItem
from battery.parity.mabench_tortoise import (
    LANE_MOCK,
    LANE_REAL,
    _FakeCrMemory,
    run_cr_tortoise_lane,
)
from battery.runner.artifacts import build_summary
from battery.runner.retrieval_preflight import (
    HybridRetrievalUnavailable,
    hybrid_retrieval_available,
    merge_leg_trace,
    require_hybrid_retrieval,
)

HEADER = "Here is a list of facts:"
POOL_UNITS = (
    "0. Thomas Kyd was born in the city of London.",
    "1. The chairperson of Fatah is Mahmoud Abbas.",
    "306. Thomas Kyd was born in the city of Leeds.",
)
POOL = "\n".join((HEADER, *POOL_UNITS))

ITEMS = (
    CrItem(qa_pair_id="q1", config="c",
           question="Where was Thomas Kyd born?", accepted=("Leeds",)),
    CrItem(qa_pair_id="q2", config="c",
           question="Who chairs Fatah?", accepted=("Mahmoud Abbas",)),
)

_HEALTHY_TRACE = (
    {"leg": "fts", "ran": True, "degraded": False, "reason": "ok",
     "count": 3},
    {"leg": "vector", "ran": True, "degraded": False, "reason": "ok",
     "count": 2},
)


def _cr(monkeypatch) -> None:
    """Pin the pinned-benchmark config without touching pyarrow/network."""
    import battery.parity.mabench as mabench_mod
    monkeypatch.setattr(
        mabench_mod, "load_cr",
        lambda config, path=None: CrConfig(config=config, context=POOL,
                                           items=ITEMS))


class _OkCaller:
    """A reader that passes the real-reader gate (no key needed)."""

    last_prompt_tokens = 0
    last_completion_tokens = 0

    def call(self, *, prompt: str) -> str:
        return "Answer: unknown"


def _patch_embedder(monkeypatch, value) -> None:
    """Monkeypatch the product's embedder singleton accessor.

    The venv HAS the embeddings extra installed, so a "no embedder" test must
    monkeypatch rather than rely on the real absence (the extra is part of the
    canonical surface — python-ci installs ``.[test,embeddings]``).
    """
    import tortoise.embeddings as emb
    monkeypatch.setattr(emb.EmbeddingModel, "get",
                        classmethod(lambda cls, load_timeout=None: value))


# ── the shared preflight ────────────────────────────────────────────────

class TestPreflight:
    def test_passes_when_the_embedder_is_available(self, monkeypatch):
        _patch_embedder(monkeypatch, object())
        assert hybrid_retrieval_available() is True
        require_hybrid_retrieval()  # must not raise

    def test_refusal_names_the_actionable_fix(self, monkeypatch):
        _patch_embedder(monkeypatch, None)
        assert hybrid_retrieval_available() is False
        with pytest.raises(HybridRetrievalUnavailable) as ei:
            require_hybrid_retrieval()
        msg = str(ei.value)
        assert "embeddings" in msg, "must name the failing extra"
        assert "uv sync" in msg, "must name the install command"
        assert "--extra parity" in msg, (
            "the install command must name every extra — an explicit --extra "
            "list is EXACT and silently removes the others (#2985)")
        assert "KEYWORD-ONLY" in msg, "must say what would silently happen"

    def test_refusal_is_a_runtime_error_subclass(self, monkeypatch):
        """Callers that only catch Exception still fail closed."""
        _patch_embedder(monkeypatch, None)
        with pytest.raises(RuntimeError):
            require_hybrid_retrieval()

    def test_negative_is_never_cached_stickily(self, monkeypatch):
        """A later-fixed environment must be observed on the very next call —
        no second, stickier negative cache beside the product's 60s cooldown."""
        _patch_embedder(monkeypatch, None)
        assert hybrid_retrieval_available() is False
        _patch_embedder(monkeypatch, object())
        assert hybrid_retrieval_available() is True, (
            "the preflight cached a negative result — it would hide a "
            "later-fixed environment (#2985)")

    def test_positive_is_not_cached_either(self, monkeypatch):
        """A subsequently-broken environment is observed too."""
        _patch_embedder(monkeypatch, object())
        assert hybrid_retrieval_available() is True
        _patch_embedder(monkeypatch, None)
        assert hybrid_retrieval_available() is False

    def test_module_imports_without_sentence_transformers(self):
        """The import is lazy/guarded: the preflight module must import (and
        report unavailability) in an environment where sentence-transformers
        cannot be imported at all — subprocess, hermetic, no download."""
        root = str(Path(__file__).resolve().parent.parent)
        script = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {root!r})

            class _Block:
                def find_spec(self, name, path=None, target=None):
                    if name == "sentence_transformers" or name.startswith(
                            "sentence_transformers."):
                        raise ImportError("blocked by #2985 test")
                    return None

            sys.meta_path.insert(0, _Block())
            sys.modules.pop("sentence_transformers", None)

            from battery.runner import retrieval_preflight as pf
            print("available", pf.hybrid_retrieval_available())
            try:
                pf.require_hybrid_retrieval()
            except pf.HybridRetrievalUnavailable as e:
                print("raised", "embeddings" in str(e))
            else:
                print("raised", False)
            """
        )
        proc = subprocess.run([sys.executable, "-c", script],
                              capture_output=True, text=True, timeout=180)
        assert proc.returncode == 0, proc.stderr
        assert "available False" in proc.stdout, proc.stdout
        assert "raised True" in proc.stdout, proc.stdout


class TestMergeLegTrace:
    """The shared interpreter of the product's per-leg trace shape."""

    def test_union_of_legs_and_or_of_degraded(self):
        legs: list[str] = []
        assert merge_leg_trace(legs, [
            {"leg": "fts", "ran": True, "degraded": False, "reason": None},
            {"leg": "vector", "ran": False, "degraded": True,
             "reason": "no_embedder"},
        ]) is True
        assert legs == ["fts", "vector"]
        # second call: no duplicates, degraded only when observed
        assert merge_leg_trace(legs, [
            {"leg": "fts", "ran": True, "degraded": False},
        ]) is False
        assert legs == ["fts", "vector"]

    def test_malformed_entries_are_ignored_not_fatal(self):
        legs: list[str] = []
        assert merge_leg_trace(legs, [None, "nope", {"no_leg": 1}, 7]) is False
        assert legs == []

    def test_none_trace_is_empty(self):
        legs: list[str] = []
        assert merge_leg_trace(legs, None) is False
        assert legs == []


# ── artifact-level provenance: the cell detail ──────────────────────────

class _LeggedMemory(_FakeCrMemory):
    """The mock fake + #3005's capability-probe surface + #2985's observed
    per-recall leg record, so the lane can be driven end to end hermetically."""

    def __init__(self, observed, *, degraded=False, probe_trace=None):
        super().__init__()
        self.observed_retrieval_legs = list(observed)
        self.observed_retrieval_degraded = degraded
        self._probe_trace = (
            list(probe_trace) if probe_trace is not None
            else [dict(e) for e in _HEALTHY_TRACE])

    def retrieval_legs(self, question, k):  # the #3005 probe
        return [dict(e) for e in self._probe_trace]


class _ProbeOnlyMemory:
    """A real-lane memory that can PROVE hybrid at the gate but has NO
    per-recall observation surface — the fail-closed-on-the-label case."""

    def __init__(self):
        self.recalled: list[str] = []

    def ingest(self, texts):
        return len(texts)

    def recall(self, question, k):
        self.recalled.append(question)
        return [POOL_UNITS[0]]

    def retrieval_legs(self, question, k):
        return [dict(e) for e in _HEALTHY_TRACE]


class TestCellDetail:
    """`detail` carries the retrieval conditions that produced the number."""

    def _run(self, memory, **kw):
        caller = _OkCaller()
        return run_cr_tortoise_lane(
            ITEMS[:1], memory, caller, lane=kw.pop("lane", LANE_MOCK),
            config="c", context=POOL, **kw)

    def test_healthy_hybrid_legs_recorded(self):
        cell, _ = self._run(_LeggedMemory(["vector", "fts", "structural"]),
                            lane=LANE_REAL)
        assert cell.detail["retrieval_legs"] == ["fts", "structural", "vector"]
        assert cell.detail["retrieval_degraded"] is False

    def test_degraded_leg_recorded(self):
        cell, _ = self._run(_LeggedMemory(["fts"], degraded=True),
                            lane=LANE_REAL)
        assert cell.detail["retrieval_legs"] == ["fts"]
        assert cell.detail["retrieval_degraded"] is True

    def test_real_lane_with_no_observed_legs_is_degraded_by_default(self):
        """A real lane that observed no legs cannot attest that it ran hybrid
        — fail closed on the LABEL (the gate proves hybrid independently, but
        the run-level observation is what the label records)."""
        cell, _ = self._run(_ProbeOnlyMemory(), lane=LANE_REAL)
        assert cell.detail["retrieval_legs"] == []
        assert cell.detail["retrieval_degraded"] is True

    def test_mock_lane_without_observation_is_not_degraded(self):
        cell, _ = self._run(_FakeCrMemory(), lane=LANE_MOCK)
        assert cell.detail["retrieval_legs"] == []
        assert cell.detail["retrieval_degraded"] is False


# ── artifact-level provenance: summary.json (run level) ─────────────────

class TestRunSummary:
    """summary.json records the same retrieval conditions at run level."""

    def test_build_summary_carries_the_conditions(self):
        summary = build_summary(
            arms=[], exit_code=0, run_ids=[], artifacts=[], seed=1,
            timestamps={"written_utc": "x"},
            retrieval_legs=["fts", "vector", "structural"],
            retrieval_degraded=False)
        assert summary["run"]["retrieval_legs"] == [
            "fts", "vector", "structural"]
        assert summary["run"]["retrieval_degraded"] is False

    def test_build_summary_defaults_are_no_retrieval(self):
        summary = build_summary(
            arms=[], exit_code=0, run_ids=[], artifacts=[], seed=1,
            timestamps={"written_utc": "x"})
        assert summary["run"]["retrieval_legs"] == []
        assert summary["run"]["retrieval_degraded"] is False

    def test_refused_retrieval_arm_stamps_the_run_degraded(self, tmp_path,
                                                          monkeypatch):
        """A real a4 run in a keyword-only environment skips the arm and says
        so in the summary — it never publishes an FTS-only a4 number."""
        from battery.runner import run as run_mod
        cfg = _cfg_dir(tmp_path)
        monkeypatch.setattr(
            run_mod, "require_hybrid_retrieval",
            lambda: (_ for _ in ()).throw(
                HybridRetrievalUnavailable("no embedder here")))
        out = tmp_path / "out"
        code = run_mod.run_battery(
            run_mod.RunConfig(config_dir=cfg, arms=["a4"],
                              executor="real", out_dir=out),
            stdout=lambda _: None)
        assert code is ExitCode.ARM_FAILED
        attempt = sorted(out.iterdir())[0]
        summary = json.loads((attempt / "summary.json").read_text())
        assert summary["run"]["retrieval_degraded"] is True
        assert summary["run"]["retrieval_legs"] == []
        arm = summary["arms"][0]
        assert arm["arm_present"] is False
        assert "retrieval preflight" in arm["init_failure"]

    def test_mock_run_records_no_retrieval_conditions(self, tmp_path):
        from battery.runner import run as run_mod
        cfg = _cfg_dir(tmp_path, arm_id="mock",
                       adapter="battery.arms.mock", model_pin="mock-agent")
        out = tmp_path / "out"
        code = run_mod.run_battery(
            run_mod.RunConfig(config_dir=cfg, arms=["mock"], mock=True,
                              out_dir=out),
            stdout=lambda _: None)
        assert code is ExitCode.OK
        attempt = sorted(out.iterdir())[0]
        summary = json.loads((attempt / "summary.json").read_text())
        assert summary["run"]["retrieval_legs"] == []
        assert summary["run"]["retrieval_degraded"] is False


# ── the arm gate + the arm's own observation ────────────────────────────

class TestArmGate:
    def test_a4_declares_the_capability_flag(self):
        from battery.arms.a4_tortoise import A4TortoiseArm
        assert A4TortoiseArm.requires_hybrid_retrieval is True

    def test_mock_run_never_preflights(self, tmp_path, monkeypatch):
        """The mock lane reads no product retrieval — the gate must not run."""
        from battery.runner import run as run_mod
        cfg = _cfg_dir(tmp_path, arm_id="mock",
                       adapter="battery.arms.mock", model_pin="mock-agent")
        monkeypatch.setattr(
            run_mod, "require_hybrid_retrieval",
            lambda: (_ for _ in ()).throw(
                AssertionError("the mock lane must not preflight")))
        out = tmp_path / "out"
        code = run_mod.run_battery(
            run_mod.RunConfig(config_dir=cfg, arms=["mock"], mock=True,
                              out_dir=out),
            stdout=lambda _: None)
        assert code is ExitCode.OK

    def test_arm_stays_callable_when_the_extra_is_absent(self, monkeypatch):
        """The gate lives in the RUNNER, not the arm — hermetic/equivalence
        tests may still drive the arm with a fake SDK in a degraded env."""
        from battery.arms.a4_tortoise import A4TortoiseArm
        from battery.arms.base import AgentContext
        from battery.config.corpus import load_corpus

        corpus = (Path(__file__).resolve().parent.parent
                  / "battery" / "config" / "corpus.yaml")
        scenario = load_corpus(corpus)[0]
        arm = A4TortoiseArm()

        class _FakeSDK:
            def recall_state(self, query=None, *, kind=None, limit=10,
                             object_centric=True, leg_trace=None):
                if leg_trace is not None:
                    leg_trace.append(
                        {"leg": "fts", "ran": True, "degraded": False,
                         "reason": "ok", "count": 0})
                    leg_trace.append(
                        {"leg": "vector", "ran": False, "degraded": True,
                         "reason": "no_embedder", "count": 0})
                return []

        arm._sdk_by_id[scenario.id] = _FakeSDK()
        ctx = AgentContext(scenario=scenario, episode_seed=1,
                           prior_memories=(), user_message="q")
        assert arm.retrieve(ctx) == []
        # The arm OBSERVED the degraded leg — provenance without the gate.
        assert arm.observed_retrieval_legs == ["fts", "vector"]
        assert arm.observed_retrieval_degraded is True
        # ...and the real-mode refusal was NOT armed by this hermetic run.
        assert arm.require_observed_hybrid is False
        assert arm.observed_retrieval_gate is None


class TestArmObservedVectorRefusal:
    """#3005 P1 — the a4 gate for the AVAILABILITY-ONLY hole.

    ``require_hybrid_retrieval`` proves the embedder CAN load; it cannot see
    a leg that fails at QUERY time (``encode_failed`` / ``breaker_open``).
    The real runner arms ``require_observed_hybrid``, after which a read
    whose VECTOR leg did not RUN is refused — no FTS-only a4 number.
    Hermetic lanes never arm it, so the arm stays usable without the
    ``embeddings`` extra.
    """

    def _arm(self, *, vector_ran: bool, reason: str = "encode_failed",
             include_vector: bool = True):
        from battery.arms.a4_tortoise import A4TortoiseArm
        from battery.config.corpus import load_corpus

        corpus = (Path(__file__).resolve().parent.parent
                  / "battery" / "config" / "corpus.yaml")
        scenario = load_corpus(corpus)[0]
        arm = A4TortoiseArm()

        class _FakeSDK:
            def recall_state(self, query=None, *, kind=None, limit=10,
                             object_centric=True, leg_trace=None):
                if leg_trace is not None:
                    leg_trace.append(
                        {"leg": "fts", "ran": True, "degraded": False,
                         "reason": "ok", "count": 0})
                    if include_vector:
                        leg_trace.append(
                            {"leg": "vector", "ran": vector_ran,
                             "degraded": not vector_ran, "reason": reason,
                             "count": 0})
                return []

        arm._sdk_by_id[scenario.id] = _FakeSDK()
        return arm, scenario

    def _ctx(self, scenario):
        from battery.arms.base import AgentContext
        return AgentContext(scenario=scenario, episode_seed=1,
                            prior_memories=(), user_message="q")

    def test_query_time_leg_failure_is_refused_in_real_mode(self):
        """The embedder loaded (preflight passes) but the leg failed at
        query time — the exact #3005 P1 hole."""
        arm, scenario = self._arm(vector_ran=False, reason="encode_failed")
        arm.require_observed_hybrid = True
        with pytest.raises(ArmUnavailable) as ei:
            arm.retrieve(self._ctx(scenario))
        msg = str(ei.value)
        assert "capability gate FAILED" in msg
        assert "encode_failed" in msg, "the refusal names the real reason"
        assert "FTS-only" in msg
        assert arm.observed_retrieval_degraded is True
        gate = arm.observed_retrieval_gate
        assert gate["vector_leg"] is False
        assert gate["reason"] == "encode_failed"
        assert gate["legs_seen"] == ["fts", "vector"]

    def test_breaker_open_is_refused_with_that_reason(self):
        arm, scenario = self._arm(vector_ran=False, reason="breaker_open")
        arm.require_observed_hybrid = True
        with pytest.raises(ArmUnavailable) as ei:
            arm.retrieve(self._ctx(scenario))
        assert arm.observed_retrieval_gate["reason"] == "breaker_open"
        assert "breaker_open" in str(ei.value)

    def test_absent_vector_entry_is_refused_generically(self):
        arm, scenario = self._arm(vector_ran=False, include_vector=False)
        arm.require_observed_hybrid = True
        with pytest.raises(ArmUnavailable) as ei:
            arm.retrieve(self._ctx(scenario))
        assert arm.observed_retrieval_gate["reason"] == "vector_leg_absent"
        assert "vector_leg_absent" in str(ei.value)

    def test_healthy_hybrid_read_passes_in_real_mode(self):
        arm, scenario = self._arm(vector_ran=True, reason="ok")
        arm.require_observed_hybrid = True
        assert arm.retrieve(self._ctx(scenario)) == []
        assert arm.observed_retrieval_gate["vector_leg"] is True
        assert arm.observed_retrieval_degraded is False

    def test_hermetic_lane_is_never_refused(self):
        """The refusal is keyed on the RUNNER-armed flag, not on the mere
        absence of the vector leg — the equivalence/hermetic contract."""
        arm, scenario = self._arm(vector_ran=False, reason="no_embedder")
        assert arm.require_observed_hybrid is False
        assert arm.retrieve(self._ctx(scenario)) == []  # no raise
        assert arm.observed_retrieval_degraded is True

    def test_real_runner_arms_the_flag_and_refuses_the_run(self, tmp_path,
                                                           monkeypatch):
        """End-to-end, hermetic: the preflight PASSES (embedder available)
        but the query-time leg fails — the real run must refuse (exit 4, no
        a4 number) and stamp the run degraded, instead of publishing an
        FTS-only a4 score."""
        from battery.arms import a4_tortoise as a4
        from battery.runner import run as run_mod

        cfg = _cfg_dir(tmp_path)
        monkeypatch.setattr(run_mod, "require_hybrid_retrieval", lambda: None)
        armed: dict = {}

        class _FakeSDK:
            def recall_state(self, query=None, *, kind=None, limit=10,
                             object_centric=True, leg_trace=None):
                if leg_trace is not None:
                    leg_trace.append(
                        {"leg": "fts", "ran": True, "degraded": False,
                         "reason": "ok", "count": 0})
                    leg_trace.append(
                        {"leg": "vector", "ran": False, "degraded": True,
                         "reason": "encode_failed", "count": 0})
                return []

        def _setup(self, scenarios, **kw):
            armed["flag"] = self.require_observed_hybrid
            for sc in scenarios:
                self._sdk_by_id[sc.id] = _FakeSDK()

        monkeypatch.setattr(a4.A4TortoiseArm, "setup_scenarios", _setup)
        out = tmp_path / "out"
        code = run_mod.run_battery(
            run_mod.RunConfig(config_dir=cfg, arms=["a4"], executor="real",
                              out_dir=out), stdout=lambda _: None)
        assert armed["flag"] is True, (
            "the real runner must arm the observed-vector refusal")
        assert code is ExitCode.ARM_FAILED, (
            "a query-time leg failure must not publish an a4 number")
        summary = json.loads(
            (sorted(out.iterdir())[0] / "summary.json").read_text())
        assert summary["run"]["retrieval_degraded"] is True
        assert "vector" in summary["run"]["retrieval_legs"]

    def test_zero_observed_legs_fails_closed(self, tmp_path, monkeypatch):
        """#3005 P2 — a real hybrid arm that observed NO legs cannot attest
        hybrid: ``retrieval_legs: []`` + ``retrieval_degraded: false`` would
        be indistinguishable from a run with no retrieval arm at all."""
        from battery.arms import a4_tortoise as a4
        from battery.runner import run as run_mod

        cfg = _cfg_dir(tmp_path)
        monkeypatch.setattr(run_mod, "require_hybrid_retrieval", lambda: None)

        def _setup(self, scenarios, **kw):
            return None  # no reads will happen; observed legs stay empty

        def _no_read(self, ctx):
            raise ArmUnavailable("store down before any trace")

        monkeypatch.setattr(a4.A4TortoiseArm, "setup_scenarios", _setup)
        monkeypatch.setattr(a4.A4TortoiseArm, "retrieve", _no_read)
        out = tmp_path / "out"
        code = run_mod.run_battery(
            run_mod.RunConfig(config_dir=cfg, arms=["a4"], executor="real",
                              out_dir=out), stdout=lambda _: None)
        assert code is ExitCode.ARM_FAILED
        summary = json.loads(
            (sorted(out.iterdir())[0] / "summary.json").read_text())
        assert summary["run"]["retrieval_legs"] == []
        assert summary["run"]["retrieval_degraded"] is True, (
            "zero observed legs must fail closed, not read as 'no retrieval "
            "arm ran'")


# ── artifact-level provenance: parity_record.json ───────────────────────

class TestParityRecordPersistence:
    """#2985 — the cell `detail` conditions survive into parity_record.json."""

    def _cfg(self, tmp_path: Path) -> Path:
        from battery.parity.runner import methodology_hashes
        cfg = Path(__file__).resolve().parent.parent / "battery" / "config"
        tmp_cfg = tmp_path / "cfg"
        tmp_cfg.mkdir()
        shutil.copy(cfg / "arms.yaml", tmp_cfg / "arms.yaml")
        rp, jr, _ = methodology_hashes("default-reader",
                                       "longmemeval-official")
        (tmp_cfg / "parity_baseline.json").write_text(json.dumps(
            {"reader_prompt_hash": rp, "judge_rubric_id_hash": jr}))
        return tmp_cfg

    def test_record_carries_the_retrieval_conditions(self, tmp_path,
                                                     monkeypatch):
        import battery.cli as cli
        import battery.parity.executors as ex

        def _fake_executor(*, mock=False, limit=None, out_dir=None):
            return ExecutedCell(
                benchmark="memoryagentbench", accuracy=0.5,
                samples=2, lane="mock_tortoise",
                revision="memoryagentbench-cr@deadbeef",
                detail={"retrieval_legs": ["fts"],
                        "retrieval_degraded": True})

        def _unavailable(*, mock=False, limit=None, out_dir=None):
            raise ExecutorUnavailable("no released runner here")

        monkeypatch.setitem(ex.EXECUTORS, "memoryagentbench",
                            _fake_executor)
        # #3005 P1: the retrieved-context cell is now dispatched too — stub
        # it (this test is about the baseline cell's provenance) so it never
        # runs the real mock lane over the full pinned config.
        monkeypatch.setitem(ex.EXECUTORS, "memoryagentbench_tortoise",
                            _unavailable)
        # keep the test hermetic/fast: the released LongMemEval runner would
        # otherwise execute its committed mini fixture end to end (~50s)
        monkeypatch.setitem(ex.EXECUTORS, "longmemeval", _unavailable)
        rc = cli.main(["parity", "--config", str(self._cfg(tmp_path)),
                       "--out", str(tmp_path), "--execute", "--mock"])
        assert rc == 0
        record = json.loads((tmp_path / "parity_record.json").read_text())
        cell = record["benchmarks"]["memoryagentbench"]
        assert cell["retrieval_legs"] == ["fts"]
        assert cell["retrieval_degraded"] is True
        # a benchmark with no retrieval leg reports null, never a default
        assert record["benchmarks"]["longmemeval"]["retrieval_legs"] is None
        assert record["benchmarks"]["longmemeval"][
            "retrieval_degraded"] is None

    def test_gate_refusal_persists_observed_legs_and_degraded(self, tmp_path,
                                                              monkeypatch):
        """A refused real lane records the PROBE's observed legs plus the
        fail-closed degraded label — the number never wears real_tortoise."""
        import battery.cli as cli
        import battery.parity.executors as ex

        def _refuse(*, mock=False, limit=None, out_dir=None):
            err = ExecutorUnavailable("capability gate refused")
            err.capability_gate = {
                "gated": True, "lane": LANE_REAL, "vector_leg": False,
                "legs_seen": ["fts", "vector"], "reason": "no_embedder",
                "leg_trace": [
                    {"leg": "fts", "ran": True, "degraded": False,
                     "reason": "ok", "count": 1},
                    {"leg": "vector", "ran": False, "degraded": True,
                     "reason": "no_embedder", "count": 0},
                ]}
            raise err

        monkeypatch.setitem(ex.EXECUTORS, "memoryagentbench", _refuse)
        monkeypatch.setitem(ex.EXECUTORS, "memoryagentbench_tortoise", _refuse)
        monkeypatch.setitem(ex.EXECUTORS, "longmemeval", _refuse)
        rc = cli.main(["parity", "--config", str(self._cfg(tmp_path)),
                       "--out", str(tmp_path), "--execute", "--allow-spend"])
        assert rc == 0
        record = json.loads((tmp_path / "parity_record.json").read_text())
        cell = record["benchmarks"]["memoryagentbench"]
        assert cell["measured"] is False
        assert cell["retrieval_legs"] == ["fts", "vector"]
        assert cell["retrieval_degraded"] is True
        assert cell["capability_gate"]["reason"] == "no_embedder"


# ── the product surface must keep the trace contract ────────────────────

class TestRecallStateLegTrace:
    """#2985 — the recall_state passthrough.

    Default ``None`` is byte-identical (same rows, no trace required); a
    provided trace is forwarded to the underlying fts-query calls and records
    the legs that ran. The embedder is patched OFF so this is hermetic (no
    model load, no network) — the trace then records the ``no_embedder``
    vector leg, which is exactly the provenance a lane needs.
    """

    @pytest.fixture()
    def sdk(self, tmp_path, monkeypatch):
        _patch_embedder(monkeypatch, None)
        from tortoise.sdk import TortoiseSDK
        db = tmp_path / "legs.db"
        sdk = TortoiseSDK(
            db_path=str(db), graph_name="legs",
            event_log_path=str(tmp_path / "events" / "legs.jsonl"))
        for text in POOL_UNITS:
            sdk.create_point(kind="evidence", content=text, dedup=True,
                             credibility="medium")
        yield sdk
        sdk.close()

    def test_default_none_is_byte_identical(self, sdk):
        plain = sdk.recall_state(query="Where was Thomas Kyd born?", limit=5)
        traced: list[dict] = []
        with_trace = sdk.recall_state(query="Where was Thomas Kyd born?",
                                      limit=5, leg_trace=traced)
        assert with_trace == plain, (
            "a trace-provided call returned different rows — the passthrough "
            "is not byte-identical")
        assert plain, "the fixture point must be retrievable"

    def test_trace_records_the_legs_that_ran(self, sdk):
        traced: list[dict] = []
        sdk.recall_state(query="Where was Thomas Kyd born?", limit=5,
                         leg_trace=traced)
        assert traced, "the trace was not forwarded to the retrieval legs"
        for entry in traced:
            assert set(entry) >= {"leg", "ran", "degraded", "reason",
                                  "count"}
        assert "vector" in {e["leg"] for e in traced}
        # embedder mocked off ⇒ the dense leg is honestly recorded degraded
        assert any(e["leg"] == "vector" and e["degraded"] for e in traced)


def _cfg_dir(tmp_path: Path, *, arm_id: str = "a4",
             adapter: str = "battery.arms.a4_tortoise",
             model_pin: str = "deepseek/deepseek-v4-flash") -> Path:
    """Yaml-only config dir with a 2-scenario corpus + sized caps (the
    test_battery_run._config_dir pattern), with a real-pinned arm entry."""
    d = tmp_path / "cfg"
    d.mkdir(parents=True, exist_ok=True)
    golds = tmp_path / "golds"
    golds.mkdir(parents=True, exist_ok=True)
    (golds / "g.txt").write_text("gold", encoding="utf-8")
    sha = hashlib.sha256(b"gold").hexdigest()
    corpus = {"scenarios": [
        {"id": f"s{i}", "tier": "probe", "family": "f", "k": 1,
         "gold_ref": {"path": "g.txt", "sha256": sha}}
        for i in range(2)]}
    (d / "corpus.yaml").write_text(yaml.safe_dump(corpus), encoding="utf-8")
    (d / "thresholds.yaml").write_text(
        yaml.safe_dump({"determinism": {"epsilon": 1e-6}, "cal": {}}),
        encoding="utf-8")
    (d / "arms.yaml").write_text(yaml.safe_dump({"arms": [
        {"arm_id": arm_id, "adapter": adapter, "config": {},
         "price_per_1k_usd": 0.000168, "expected_tokens_per_episode": 26409,
         "model_pin": model_pin, "temperature": 0.0}]}), encoding="utf-8")
    (d / "budget.yaml").write_text(yaml.safe_dump(
        {"max_episodes": 1000, "max_estimated_cost_usd": 50.0}),
        encoding="utf-8")
    return d
