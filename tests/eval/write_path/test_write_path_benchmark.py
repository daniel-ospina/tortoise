"""W2-b benchmark integration: REAL write-path replay over the planted corpus.

The benchmark-first CI-gate lane (issue #2098, epic #2080 W2-b): replays the
committed planted-gold corpus through the REAL session→graph write path on a
hermetic graph and grades the written graph with the mechanical judges.

Replay seam (deterministic, hermetic, offline — the CI-reproducible posture):

* store-file render → REAL ``session_import`` parser round-trip (the parser
  is the seam, not a fixture reader),
* SDK ``capture_session`` (the real capture implementation shared with
  hosted) over the real Falkor projection — docker lane (TORTOISE_DB_URI) or
  embedded, exactly like every migrated docker-lane test,
* the offline M2 extractor seam (TORTOISE_SESSION_LLM_MOCK=1 +
  TORTOISE_SESSION_EXTRACTOR=m2) — the pinned BPRE extractor posture the
  plan's hermetic harness specifies (env-key stripping + one-DB-per-run §6.5);
  a content-preserving deterministic extractor, so CI replays are
  byte-reproducible,
* dream EP pass (require_calibration=False — the eval's EP-update step).

Assertions cover the can-fail gate properties the issue owns:

* S1: every planted session replays through the real pipeline and EMITS
  (sessions_emitting == 1.0; a session that silently produced no memory
  points is a runner error, never dropped);
* the grading surface reads the REAL written graph (memory points carry the
  session's stamped provenance — provenance_accuracy == 1.0 on the echo
  lane; strict survival reflects the EP pass state honestly);
* the verbatim control lane grades 1.0 (corpus/grader soundness);
* compare semantics on real data: vs the pending baseline ⇒ inconclusive;
  vs a baseline made from an identical replay ⇒ pass (deterministic); and a
  stripped-provenance regression ⇒ REGRESSION — the property that FAILS CI
  when the write path regresses (the epic's S5 negative).
"""
from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

from tests.eval.write_path import corpus, runner, schema  # noqa: E402
from tests.eval.write_path.judge import JUDGE_PIN_MECHANICAL  # noqa: E402

pytestmark = pytest.mark.timeout(900)

# The deterministic BPRE replay posture (hermetic harness §6.5).
EXTRACTOR_MOCK = {"TORTOISE_SESSION_LLM_MOCK": "1", "TORTOISE_SESSION_EXTRACTOR": "m2"}


@pytest.fixture()
def sdk_factory(monkeypatch):
    """Yields a factory that mints ONE fresh hermetic graph per call (docker
    lane: a unique server graph via the redirect seam; embedded lane: a
    transient redislite file).  Every minted SDK is wiped + closed at test
    teardown — capture re-runs are NOT idempotent at the extraction layer
    (each capture mints a fresh sessionCaptured Event), so each benchmark
    run needs its own graph."""
    from tortoise.sdk import TortoiseSDK

    # #2183 regression (main CI red 2026-09-04, fixed here): this was a raw
    # os.environ.update(EXTRACTOR_MOCK) with NO restore — the mock-extractor
    # env leaked process-wide for every test after the first sdk_factory use,
    # hijacking the extraction lane and breaking
    # test_pack_manifest_store_extraction.py (tenant pack kinds never reached
    # the S1/S2 prompts) whenever the benchmark module ran before it in the
    # same pytest process (the test (b) leg). monkeypatch.setenv auto-restores
    # at test teardown — env stays set for this test's duration only.
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    created: list[tuple] = []

    def _make() -> TortoiseSDK:
        nonce = uuid.uuid4().hex[:8]
        tmp = Path(tempfile.mkdtemp(prefix="w2b_int_")) / f"{nonce}.db"
        sdk = TortoiseSDK(db_path=str(tmp), namespace=f"test_w2b_{nonce}")
        created.append((sdk, tmp.parent))
        return sdk

    yield _make
    for sdk, _dir in created:
        try:
            sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
        except Exception:  # noqa: BLE001
            pass
        try:
            sdk.close()
        except Exception:  # noqa: BLE001
            pass
    for _sdk, _dir in created:
        try:
            shutil.rmtree(_dir, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


def _tmp_corpus(tmp_path: Path) -> Path:
    dst = tmp_path / "corpus"
    shutil.copytree(corpus.WRITE_PATH_DIR, dst, ignore=shutil.ignore_patterns(
        "runs", "__pycache__", "test_*.py"))
    return dst


def _run_all(sdk_factory, root=corpus.WRITE_PATH_DIR) -> dict:
    """One full corpus replay on a FRESH hermetic graph (the BPRE lane)."""
    sdk = sdk_factory()
    return runner.run_benchmark(root=root, sdk=sdk)


def test_bpre_lane_full_corpus_replay_emits_and_grades(sdk_factory):
    """S1 + grading-surface smoke over the REAL pipeline: every planted
    session emits through the real capture path and the mechanical graders
    read the real written graph.

    REVIEW-FIX (PR #2183 findings 1+4): the smoke runs the deterministic
    M2 echo lane and compares against the lane's OWN committed m2.json
    baseline (posture-matched) — verdict PASS on a clean replay (the echo
    lane is byte-reproducible, so a healthy write path reproduces its
    committed numbers exactly). A write-path regression (provenance strip,
    parser silent-skip) moves the metrics ⇒ REGRESSION ⇒ the can-fail CI
    gate. The M2 lane is NEVER compared against the LLM-lane main.json
    (cross-posture compare is a config mismatch ⇒ inconclusive — the
    posture guard)."""
    report = _run_all(sdk_factory)
    assert report["run_status"] == "completed", report.get("log")
    assert (report.get("resolved_config") or {}).get("extractor_posture") == "m2"
    # The deterministic lane reproduces its committed m2.json numbers ⇒ PASS.
    assert report["verdict"] == schema.VERDICT_PASS, report.get("log")
    assert report["failure_origin"] is None
    assert set(report["metrics"]) == schema.METRIC_VALUES
    # 100% sessions-emitting invariant (S1: no session silently produces no
    # memory points through the real pipeline).
    assert report["metrics"]["sessions_emitting"] == 1.0
    # The echo lane retains content: macro survival is measured > 0, and the
    # stamped provenance surface is intact (eventId on every extracted point).
    assert report["metrics"]["salient_unit_survival_macro"] > 0.0
    assert report["metrics"]["salient_unit_survival_macro"] <= 1.0
    assert report["metrics"]["provenance_accuracy"] == 1.0
    # Every session contributed a graded gold + memory points + control 1.0.
    seen_sessions = {r["session_id"] for r in report["session_results"]}
    assert seen_sessions == set(corpus.session_ids())
    for result in report["session_results"]:
        assert result["gold_total_units"] > 0
        assert result["memory_point_count"] >= 1
        assert result["control_macro_survived"] == result["control_macro_total"]
        assert result["capture_ok"] is True
    # The receipt validates (the publish artifact).
    receipt = runner.build_receipt(report)
    assert runner.validate_receipt(receipt) == []
    assert receipt["judge_pin"] == JUDGE_PIN_MECHANICAL
    assert receipt["corpus_hash"] == corpus.compute_fixtures_hash()


def test_bpre_lane_determinism_and_provenance_regression_fails(sdk_factory, tmp_path):
    """S5 can-fail gate semantics on real replay data:

    1. determinism — two full-corpus replays on fresh hermetic graphs
       produce byte-identical metrics (the standing CI invariant that makes
       a committed baseline comparable);
    2. a run vs a committed baseline it cannot meet ⇒ REGRESSION with origin
       ``gate_regression`` — the can-fail property;
    3. a stripped-provenance regression on the SAME replay is
       provenance-SHAPED: provenance accuracy + strict survival collapse
       while macro content retention is untouched — the write-path
       regression the gate must catch even when content retention looks
       fine.

    The regression-EXERCISING baseline here is a synthetic schema-valid
    fixture (all-1.0 targets, leakage 0) written into a byte-identical tmp
    corpus — synthetic because the real committed m2.json is PASS-at-
    reproduction (the deterministic echo lane reproduces it byte-identically,
    which is the gate's comparison target). The synthetic all-1.0 target is
    the can-fail probe: the echo lane cannot meet it (observed leakage 11 > 0
    committed), so verdict REGRESSION fires — the gate machinery under test.
    The real committed baselines: m2.json (deterministic echo lane, leakage
    11 structural — its gate is determinism/reproduction, not the product
    leakage bar) and main.json (product lane, published by the real-LLM lane
    with justification per the fix-wave protocol)."""
    report1 = _run_all(sdk_factory)
    assert report1["run_status"] == "completed"
    metrics1 = report1["metrics"]
    # Byte-identical reproduction on a fresh hermetic graph.
    report2 = _run_all(sdk_factory)
    assert report2["run_status"] == "completed"
    assert report2["metrics"] == metrics1

    # A committed baseline the echo lane cannot meet (leakage 0 < observed;
    # macro/strict 1.0 > observed) → the run REGRESSES against it.
    root = _tmp_corpus(tmp_path)
    pending = corpus.load_baseline(root, posture="m2")
    assert pending["fixtures_hash"] == report1["corpus_hash"]
    # REVIEW-FIX (posture): the synthetic gate baseline belongs to the m2
    # lane (the run under test is the deterministic echo lane) — written to
    # the m2 posture file with posture-m2 config.
    fixture_config = dict(corpus.BASELINE_CONFIG)
    fixture_config["extractor_posture"] = "m2"
    fixture_baseline = {
        "schema_version": 1,
        "fixtures_hash": report1["corpus_hash"],
        "judge_pin": JUDGE_PIN_MECHANICAL,
        "config": fixture_config,
        "justification": "synthetic fixture baseline (integration test)",
        "metrics": {"salient_unit_survival_macro": 1.0,
                    "salient_unit_survival_strict": 1.0,
                    "distractor_leakage_per_run": 0,
                    "sessions_emitting": 1.0,
                    "quote_fidelity": 1.0,
                    "provenance_accuracy": 1.0},
        "history": [],
    }
    assert schema.validate_baseline(fixture_baseline) == []
    (root / "baselines" / "m2.json").write_text(json.dumps(fixture_baseline, indent=2))

    report3 = _run_all(sdk_factory, root=root)
    assert report3["run_status"] == "completed"
    assert report3["verdict"] == schema.VERDICT_REGRESSION
    assert report3["failure_origin"] == "gate_regression"

    # Regression simulation: the write path stops stamping provenance (the
    # epic's stripped-provenance negative). The runner's snapshot layer is
    # the seam — strip eventId/extractedFrom so the graded points carry no
    # provenance, exactly as a provenance-stripping write path would appear.
    from tests.eval.write_path import runner as runner_module

    real_snapshot = runner_module.snapshot_session

    def _stripped_snapshot(sdk, session_id: str) -> dict:
        snap = real_snapshot(sdk, session_id)
        for point in snap["points"]:
            point["provenance_present"] = False
            point["event_id"] = None
        return snap

    runner_module.snapshot_session = _stripped_snapshot
    try:
        report4 = runner_module.run_benchmark(root=root, sdk=sdk_factory())
    finally:
        runner_module.snapshot_session = real_snapshot
    assert report4["run_status"] == "completed"
    assert report4["verdict"] == schema.VERDICT_REGRESSION
    assert report4["failure_origin"] == "gate_regression"
    # The regression is provenance-shaped: strict + provenance accuracy
    # collapse while content retention (macro) is untouched.
    assert report4["metrics"]["provenance_accuracy"] == 0.0
    assert report4["metrics"]["salient_unit_survival_strict"] == 0.0
    assert report4["metrics"]["salient_unit_survival_macro"] == report3["metrics"]["salient_unit_survival_macro"]
    # The regression run's receipt records the gate origin (never a silent
    # pass; skipped never counts as pass).
    receipt = runner.build_receipt(report4)
    assert runner.validate_receipt(receipt) == []
    assert receipt["verdict"] == schema.VERDICT_REGRESSION
    assert receipt["failure_origin"] == "gate_regression"


def test_bpre_lane_config_mismatch_is_inconclusive(sdk_factory, tmp_path):
    """Config/hash drift ⇒ inconclusive, never a rubber-stamp (E2E-2)."""
    report1 = _run_all(sdk_factory)
    assert report1["run_status"] == "completed"
    root = _tmp_corpus(tmp_path)
    pending = corpus.load_baseline(root)
    drifted = {
        **dict(corpus.BASELINE_CONFIG),
        "mode": "full",  # a resolved-config drift
    }
    verdict = schema.compare_run(
        report1["metrics"], pending,
        resolved_config=drifted,
        run_fixtures_hash=report1["corpus_hash"],
    )
    assert verdict == schema.VERDICT_INCONCLUSIVE
    # Hash drift (gold-only edit) is also inconclusive at the schema level.
    gold_path = root / "gold" / "wp01_quarry_debug.gold.json"
    gold = json.loads(gold_path.read_text())
    gold["scenario"] = gold["scenario"] + "x"
    gold_path.write_text(json.dumps(gold))
    drifted_hash = corpus.compute_fixtures_hash(root)
    verdict2 = schema.compare_run(
        report1["metrics"], pending,
        resolved_config=report1["resolved_config"],
        run_fixtures_hash=drifted_hash,
    )
    assert verdict2 == schema.VERDICT_INCONCLUSIVE
