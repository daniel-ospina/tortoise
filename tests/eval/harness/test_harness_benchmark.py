"""W3 harness integration: REAL-seam replay over the committed corpus.

The W3 harness (issue #2099, epic #2080) CI-gate lane: replays the
committed Cat-34-style corpus through the REAL product seams on hermetic
per-cell graphs and grades the written/recalled surface with the mechanical
graders:

* store-file render → REAL ``session_import`` parser round-trip (parser is
  the seam, not a fixture reader),
* SDK ``capture_session`` (the real capture implementation shared with
  hosted) over the real Falkor projection — docker lane (TORTOISE_DB_URI)
  or embedded, exactly like every migrated docker-lane test,
* the offline M2 echo extractor seam (TORTOISE_SESSION_LLM_MOCK=1 +
  TORTOISE_SESSION_EXTRACTOR=m2) — the deterministic posture the hermetic
  harness specifies; CI replays are byte-reproducible,
* recall through the REAL ``recall_state`` (continuity readers grade what
  the reader cell actually surfaces),
* the NULL reflex (the graded reflex seam lands with the W4 delivery issue)
  — kta/push numbers are the honest pre-W4 baseline.

Assertions cover the properties the issue owns:

* S1: every session replays and EMITS; graded-today suites (write_back /
  continuity / isolation) grade REAL numbers from the written graph +
  recall transcript (write_back fidelity 1.0, continuity recall 1.0 on the
  echo lane);
* E2E-4 (this issue's OWN pass gate): source isolation = 0 on the clean
  replay — and a MISROUTED replay (both teams forced into one cell) is
  DETECTED as violations ⇒ REGRESSION;
* determinism: two full-corpus replays on fresh hermetic graphs produce
  byte-identical metrics;
* can-fail: a synthetic graded-reflex baseline (kta 0.00 bar live) the
  null-reflex run cannot meet ⇒ REGRESSION with origin gate_regression —
  the property that FAILS CI until the W4 reflex lands and re-blesses;
* the committed m2.json baseline is PASS-at-reproduction (the deterministic
  lane's gate is reproduction, exactly like W2-b's m2 lane).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

from eval.harness import corpus, runner, schema  # noqa: E402

pytestmark = pytest.mark.timeout(900)

EXTRACTOR_MOCK = {"TORTOISE_SESSION_LLM_MOCK": "1", "TORTOISE_SESSION_EXTRACTOR": "m2"}


@pytest.fixture()
def sdk_factory():
    """Yields a factory that mints ONE fresh hermetic graph per call (docker
    lane: a unique server graph via the redirect seam; embedded lane: a
    transient file).  Every minted SDK is wiped + closed at teardown.  The
    m2 mock posture env is set for the test and RESTORED at teardown
    (review round-1 P2: process-global env mutation must not leak into
    sibling test modules in the same worker)."""
    from tortoise.sdk import TortoiseSDK

    saved_env = {
        key: os.environ.get(key) for key in
        ("TORTOISE_SESSION_EXTRACTOR", "TORTOISE_SESSION_LLM_MOCK")
    }
    os.environ.update(EXTRACTOR_MOCK)
    created: list[tuple] = []

    def _make() -> TortoiseSDK:
        nonce = uuid.uuid4().hex[:8]
        tmp = Path(tempfile.mkdtemp(prefix="w3h_int_")) / f"{nonce}.db"
        sdk = TortoiseSDK(db_path=str(tmp), namespace=f"test_w3h_{nonce}")
        created.append((sdk, tmp.parent))
        return sdk

    yield _make
    import contextlib

    for sdk, _dir in created:
        with contextlib.suppress(Exception):
            sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
        with contextlib.suppress(Exception):
            sdk.close()
    for _sdk, _dir in created:
        with contextlib.suppress(Exception):
            shutil.rmtree(_dir, ignore_errors=True)
    # Restore the ambient env (the mock posture was test-scoped).
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _tmp_corpus(tmp_path: Path) -> Path:
    dst = tmp_path / "corpus"
    shutil.copytree(corpus.HARNESS_DIR, dst, ignore=shutil.ignore_patterns(
        "receipts", "__pycache__", "test_*.py"))
    return dst


def _run_all(sdk_factory, root=corpus.HARNESS_DIR) -> dict:
    """One full-corpus replay on fresh hermetic per-cell graphs."""
    return runner.run_benchmark(root=root)


def test_harness_replay_grades_real_surfaces(sdk_factory):
    """S1 + grading-surface smoke over the REAL pipeline: every session
    replays + emits through the real capture path; the graded-today suites
    read REAL numbers from the written graph + recall transcript; the
    deterministic M2 lane reproduces its committed m2.json ⇒ PASS."""
    report = _run_all(sdk_factory)
    assert report["run_status"] == "completed", report.get("log")
    assert (report.get("resolved_config") or {}).get("extractor_posture") == "m2"
    assert report["verdict"] == schema.VERDICT_PASS, report.get("log")
    assert report["failure_origin"] is None
    assert set(report["metrics"]) == schema.METRIC_VALUES
    # Graded-today surface: the echo lane writes content + provenance and
    # recall surfaces the planted decisions.
    assert report["metrics"]["write_back_fidelity"] == 1.0
    assert report["metrics"]["continuity_recall"] == 1.0
    # E2E-4 (this issue's own gate): source isolation = 0.
    assert report["metrics"]["source_isolation_violations"] == 0
    # Null-reflex honesty: kta failure is total (nothing injected yet) and
    # the run's notes NAME the failure class.
    assert report["metrics"]["know_to_ask_failure_rate"] == 1.0
    assert any("no-reflex" in n for n in report["notes"])
    # Every session emitted (never skipped) and every capture was ok — the
    # BPRE gate corpus excludes the pinned holdout (frozen for the W4
    # reflex), which the run's notes state.
    expected = set(corpus.session_ids()) - set(corpus.holdout_ids())
    seen = {r["session_id"] for r in report["session_results"]}
    assert seen == expected
    assert any("BPRE mode" in n for n in report["notes"])
    for result in report["session_results"]:
        assert result["emitted"] is True
        assert result["capture_ok"] is True
    # Receipt validates (the publish artifact).
    receipt = runner.build_receipt(report)
    assert runner.validate_receipt(receipt) == []
    assert receipt["judge_pin"] == runner.JUDGE_PIN
    assert receipt["corpus_hash"] == corpus.compute_fixtures_hash()


def test_harness_replay_determinism_and_graded_reflex_gate(sdk_factory, tmp_path):
    """Determinism + the can-fail gate on real replay data:

    1. two full-corpus replays produce byte-identical metrics;
    2. a synthetic graded-reflex baseline (kta 0.00 bar LIVE) that the
       null-reflex run cannot meet ⇒ REGRESSION with origin
       ``gate_regression`` — the property that FAILS CI until the W4
       reflex lands and re-blesses the lane to reflex=graded."""
    report1 = _run_all(sdk_factory)
    assert report1["run_status"] == "completed"
    report2 = _run_all(sdk_factory)
    assert report2["run_status"] == "completed"
    assert report2["metrics"] == report1["metrics"]

    root = _tmp_corpus(tmp_path)
    pending = corpus.load_baseline(root, posture="m2")
    assert pending["fixtures_hash"] == report1["corpus_hash"]
    fixture_config = dict(corpus.BASELINE_CONFIG)
    fixture_config["extractor_posture"] = "m2"
    # config.reflex stays null (config parity with the run's resolved config
    # — a graded-reflex baseline is a config mismatch => inconclusive, which
    # is itself the protocol guard under test in the hermetic schema tests).
    # The can-fail probe: commit the AT-TARGET kta snapshot (0.00, the shape
    # the W4 graded-reflex re-bless will commit) — the null-reflex lane runs
    # at failure 1.0 > 0.00 => REGRESSION, pure directional (bars off).
    fixture_baseline = {
        "schema_version": 1,
        "fixtures_hash": report1["corpus_hash"],
        "judge_pin": runner.JUDGE_PIN,
        "config": fixture_config,
        "justification": "synthetic at-target baseline (integration test)",
        "metrics": {
            "know_to_ask_failure_rate": 0.0,
            "false_fire_rate": 0.0,
            "push_precision": 1.0,
            "push_recall": 1.0,
            "write_back_fidelity": 1.0,
            "continuity_recall": 1.0,
            "source_isolation_violations": 0,
        },
        "history": [],
    }
    assert schema.validate_baseline(fixture_baseline) == []
    (root / "baselines" / "m2.json").write_text(json.dumps(fixture_baseline, indent=2))

    report3 = runner.run_benchmark(root=root)
    assert report3["run_status"] == "completed"
    assert report3["verdict"] == schema.VERDICT_REGRESSION
    assert report3["failure_origin"] == "gate_regression"
    receipt = runner.build_receipt(report3)
    assert runner.validate_receipt(receipt) == []
    assert receipt["verdict"] == schema.VERDICT_REGRESSION
    assert receipt["failure_origin"] == "gate_regression"


def test_full_mode_includes_pinned_holdout(sdk_factory):
    """--full (mode=full) replays the WHOLE corpus including the pinned
    holdout (the W4 reflex's frozen evaluation set); BPRE excludes it.  The
    holdout membership is never seed-derived — the run's session set is a
    pure function of mode."""
    report_bpre = _run_all(sdk_factory)
    assert report_bpre["run_status"] == "completed"
    assert set(report_bpre["resolved_config"]) >= {"mode", "holdout_excluded"}
    assert report_bpre["resolved_config"]["mode"] == "BPRE"
    report_full = runner.run_benchmark(config={"mode": "full"})
    assert report_full["run_status"] == "completed"
    bpre_sids = {r["session_id"] for r in report_bpre["session_results"]}
    full_sids = {r["session_id"] for r in report_full["session_results"]}
    assert bpre_sids == full_sids - set(corpus.holdout_ids())
    assert full_sids == set(corpus.session_ids())
    assert corpus.holdout_ids()


def test_isolation_gate_catches_misrouted_teams(sdk_factory):
    """E2E-4 negative: force both teams into ONE cell (a misrouted rig) and
    the isolation grader DETECTS the cross-team leak ⇒ REGRESSION.  The
    clean replay is violations=0; routing is the graded seam.

    Review round-1 P1 upgrade: the misroute is PARTIAL and CROSS-SUITE —
    only team B's WRITE_BACK session (iso_wb_b_atlas, the Atlas note) lands
    in team A's cell.  The per-gold teams map (Mercury anchors only) would
    miss this leak; the UNION other-team anchor vocabulary (Atlas content
    included) is what catches it."""
    from eval.harness import runner as runner_module

    real_cell_key = runner_module._cell_key

    def _misroute_wb_b(session_id, fixture, gold):
        if fixture.get("suite") == "write_back" and fixture.get("team") == "team_b":
            return "team_team_a"  # only the Atlas write-back leaks across
        return real_cell_key(session_id, fixture, gold)

    report_clean = _run_all(sdk_factory)
    assert report_clean["metrics"]["source_isolation_violations"] == 0

    runner_module._cell_key = _misroute_wb_b
    try:
        report = runner_module.run_benchmark()
    finally:
        runner_module._cell_key = real_cell_key
    assert report["run_status"] == "completed"
    assert report["metrics"]["source_isolation_violations"] > 0
    assert report["verdict"] == schema.VERDICT_REGRESSION
    assert report["failure_origin"] == "gate_regression"


def test_run_dropping_a_suite_fails_loud(sdk_factory):
    """Review round-1 P1/C: a run whose session set drops gate-corpus
    suites reads those metrics at their vacuous FLOOR — which is now WORST
    (minimize rates collapse to 1.0), so the run REGRESSES vs the committed
    baseline instead of passing.  The session_ids dodge cannot turn a
    dropped suite into a green."""
    wb_only = [s for s in corpus.session_ids()
               if corpus.load_fixture(s).get("suite") == "write_back"]
    report = runner.run_benchmark(session_ids=wb_only)
    assert report["run_status"] == "completed"
    # Dropped kta/false-fire/push/continuity suites sit at worst ⇒ the run
    # cannot pass the committed baseline (kta 1.0 == committed, ff 1.0 >
    # committed 0.0 ⇒ REGRESSION).
    assert report["metrics"]["know_to_ask_failure_rate"] == 1.0
    assert report["metrics"]["false_fire_rate"] == 1.0
    assert report["verdict"] == schema.VERDICT_REGRESSION
