"""W3-b why-suite benchmark: REAL seeded runs over the shared E2E-1 corpus.

The why-suite's CI-gate lane: seeds the shared 40-point planted-conflict
corpus on a hermetic graph, assembles the canonical §3.1.4 why-blocks via
the REAL W4 assembly (``tortoise.why.assemble_why_blocks``), and grades the
four why-questions from the surfaced context ALONE (A11).

Assertions cover the properties the issue owns:

* E2E-7 numbers: conflict-surfacing rate >= 0.95 AND dig-deeper navigation
  accuracy >= 0.95 over the full planted corpus from the surfaced context;
  support-chain + trade-off sufficiency measured; the clean false-positive
  arm is 0 (clean points never invent contradictions);
* A11: graded from the canonical why-block only (the grader row never
  touches the graph);
* judge pin: the run's pin is the pinned ``judge_why_suite_v1`` (prompt
  hash asserted in the pre-step) and the receipt validates with per-point
  rows;
* determinism: two full runs produce byte-identical metrics;
* can-fail: a synthetic sub-floor baseline (conflict-surfacing 0.94 live
  under the >= 0.95 bar) ⇒ REGRESSION with origin gate_regression — the
  property that FAILS CI when the assembly cannot answer;
* jointly-pinned seeding drift: re-deriving W4-a's REAL E2E-1 seeding on a
  hermetic graph produces the same planted content + composition this
  suite's manifest pins (seed → planted point drift cannot ship silently).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

from eval.why_suite import corpus, judge, runner, schema, seeding  # noqa: E402

pytestmark = pytest.mark.timeout(900)


@pytest.fixture()
def m2_env():
    """The deterministic-lane posture env for the test body (restored at
    teardown — process-global env mutation must not leak into sibling
    modules in the same worker)."""
    saved = os.environ.get("TORTOISE_SESSION_EXTRACTOR")
    os.environ["TORTOISE_SESSION_EXTRACTOR"] = "m2"
    yield
    if saved is None:
        os.environ.pop("TORTOISE_SESSION_EXTRACTOR", None)
    else:
        os.environ["TORTOISE_SESSION_EXTRACTOR"] = saved


def _fresh_sdk():
    from tortoise.sdk import TortoiseSDK

    nonce = os.urandom(4).hex()
    tmp = Path(tempfile.mkdtemp(prefix="w3b_int_")) / f"{nonce}.db"
    sdk = TortoiseSDK(db_path=str(tmp), namespace=f"test_w3b_{nonce}")
    import contextlib

    with contextlib.suppress(Exception):
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    return sdk


def _run() -> dict:
    return runner.run_benchmark()


def test_full_corpus_grades_e2e7_bars_from_surfaced_context(m2_env):
    """The E2E-7 gate over the FULL planted corpus: every one of the 40
    planted points grades; conflict-surfacing >= 0.95 and dig-deeper
    navigation >= 0.95 from the canonical why-block ALONE; support-chain +
    trade-off sufficiency measured; 0 clean false positives."""
    report = _run()
    assert report["run_status"] == "completed", report.get("log")
    assert (report.get("resolved_config") or {}).get("extractor_posture") == "m2"
    assert report["verdict"] == schema.VERDICT_INCONCLUSIVE  # first-run pending
    assert report["failure_origin"] is None
    assert set(report["metrics"]) == schema.METRIC_VALUES
    metrics = report["metrics"]
    # E2E-7 targets (the deterministic lane is at the bar from day one).
    assert metrics["conflict_surfacing_rate"] >= schema.CONFLICT_SURFACING_FLOOR
    assert metrics["dig_deeper_navigation_accuracy"] >= schema.DIG_DEEPER_NAV_FLOOR
    assert metrics["false_positive_rate"] <= schema.FALSE_POSITIVE_TOLERANCE
    # Honest sufficiency measures (not gated, recorded).
    assert metrics["support_chain_sufficiency"] == 1.0
    assert metrics["tradeoff_sufficiency"] == 1.0
    # Every gold entry has a graded row (no planted point silently skipped).
    assert len(report["point_results"]) == len(corpus.gold_doc()["entries"])
    assert {r["topic"] for r in report["point_results"]} == {
        e["point_id"] for e in corpus.gold_doc()["entries"]
    }
    # Per-row detail: no missing surfaces on the 30 conflicted + clean arm.
    assert all(
        r["conflict_surfaced"] is True for r in report["point_results"] if r["expected_conflict"]
    )
    assert all(r["nav_correct"] == r["nav_total"] for r in report["point_results"])
    assert all(r["false_positive"] is False for r in report["point_results"])
    assert all(r["support_sufficient"] is True for r in report["point_results"])
    # Judge pin: the pinned protocol + the pre-step hash assertion.
    assert report["judge_pin"] == judge.judge_pin()
    assert "judge_why_suite_v1:" in report["judge_pin"]
    # A11 note present (graded from surfaced context only).
    assert any("surfaced context ONLY (A11)" in n for n in report["notes"])
    # Receipt validates with per-point rows (evidentiality).
    receipt = runner.build_receipt(report)
    assert runner.validate_receipt(receipt) == []
    assert receipt["judge_pin"] == judge.judge_pin()
    assert len(receipt["point_results"]) == 40
    assert receipt["corpus_hash"] == corpus.compute_fixtures_hash()
    # The A4 eval-phase arm ran and recorded its result (never gating).
    assert report["a4_result"] is not None
    assert report["a4_result"]["measured"] is False  # honest gap (see notes)
    assert any("A4 A/B" in n for n in report["notes"])


def test_run_determinism_and_canfail_standing_bars(m2_env, tmp_path, monkeypatch):
    """1. two full runs produce byte-identical metrics;
    2. the gate can FAIL: against an AT-TARGET committed baseline the real
    run passes (reproduction at the bar), but a degraded W4 assembly —
    ``assemble_why_blocks`` returning nothing (the empty-context grade, the
    shape an assembly that cannot answer produces) — trips the standing
    >= 0.95 bars ⇒ REGRESSION with origin gate_regression (the can-fail
    property that FAILS CI until the assembly answers the corpus)."""
    report1 = _run()
    assert report1["run_status"] == "completed"
    report2 = _run()
    assert report2["run_status"] == "completed"
    assert report2["metrics"] == report1["metrics"]

    root = _tmp_corpus(tmp_path)
    pending = corpus.load_baseline(root, posture="m2")
    assert pending["fixtures_hash"] == report1["corpus_hash"]
    at_target = {
        "schema_version": 1,
        "fixtures_hash": report1["corpus_hash"],
        "judge_pin": judge.judge_pin(),
        "config": dict(pending["config"]),
        "justification": "synthetic at-target baseline (integration test)",
        "metrics": report1["metrics"],
        "history": [],
    }
    assert schema.validate_baseline(at_target) == []
    (root / "baselines" / "m2.json").write_text(json.dumps(at_target, indent=2))

    # Probe A: reproduction at the bar — the real run passes the committed
    # at-target snapshot.
    report_ok = runner.run_benchmark(root=root)
    assert report_ok["run_status"] == "completed"
    assert report_ok["verdict"] == schema.VERDICT_PASS

    # Probe B: a degraded assembly (empty surfaced context everywhere) can
    # never pass — the standing bars trip REGRESSION (the A11 gate).
    import tortoise.why as why_mod

    monkeypatch.setattr(why_mod, "assemble_why_blocks", lambda proj, ids: {})
    report_bad = runner.run_benchmark(root=root)
    assert report_bad["run_status"] == "completed"
    assert report_bad["metrics"]["conflict_surfacing_rate"] < schema.CONFLICT_SURFACING_FLOOR
    assert report_bad["verdict"] == schema.VERDICT_REGRESSION
    assert report_bad["failure_origin"] == "gate_regression"
    receipt = runner.build_receipt(report_bad)
    assert runner.validate_receipt(receipt) == []
    assert receipt["verdict"] == schema.VERDICT_REGRESSION
    assert receipt["failure_origin"] == "gate_regression"


def _tmp_corpus(tmp_path: Path) -> Path:
    dst = tmp_path / "corpus"
    shutil.copytree(
        corpus.WHY_DIR,
        dst,
        ignore=shutil.ignore_patterns(
            "receipts",
            "__pycache__",
            "test_*.py",
            "README.md",
            "__init__.py",
            "judge.py",
            "generate_corpus.py",
        ),
    )
    return dst


def test_judge_pin_drift_fails_preflight(m2_env, tmp_path, monkeypatch):
    """The pre-step asserts the pinned prompt: a drifted prompt file fails
    the run as judge_pin_mismatch (never a silent compare under a different
    protocol)."""
    root = _tmp_corpus(tmp_path)
    # Point the judge module at the corpus copy's prompt (the pre-step must
    # guard the artifact the RUN would grade under, not the installed one).
    monkeypatch.setattr(judge, "JUDGE_PROMPT_PATH", Path(root) / "judge_why_suite_v1.txt")
    judge_path = Path(root) / "judge_why_suite_v1.txt"
    judge_path.write_text("DRIFTED PROTOCOL\n", encoding="utf-8")
    report = runner.run_benchmark(root=root)
    assert report["run_status"] == "failed"
    assert report["failure_origin"] == "judge_pin_mismatch"
    assert any("judge pin" in line for line in report["log"])


def test_corpus_hash_mismatch_is_inconclusive(m2_env, tmp_path):
    """A gold-only edit changes fixtures_hash ⇒ the run cannot compare vs
    the committed baseline (inconclusive — never a rubber-stamp pass)."""
    root = _tmp_corpus(tmp_path)
    gold_path = root / "gold" / "why_suite.gold.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    gold["seed"] = 43
    gold_path.write_text(json.dumps(gold, indent=2) + "\n", encoding="utf-8")
    report = runner.run_benchmark(root=root)
    assert report["run_status"] == "failed"
    assert report["failure_origin"] == "hash_mismatch"
    assert any("corpus drift" in line for line in report["log"])


# ── Jointly-pinned seeding drift gate (W4-a E2E-1 ↔ this suite) ───────────


def _all_point_contents(sdk) -> set[str]:
    rows = (
        sdk._get_proj()
        .g.query(
            "MATCH (n:Point) WHERE (n.is_operator = false OR n.is_operator IS NULL) "
            "RETURN n.content"
        )
        .result_set
    )
    return {row[0] for row in rows}


def test_seeding_matches_w4a_e2e1_planting(m2_env):
    """The joint pin: this suite's deterministic planting (manifest-derived
    topics + content templates) reproduces W4-a's REAL E2E-1 seeding on a
    hermetic graph — identical planted point-content sets + identical
    family composition.  A W4-a seeding change (renamed/added topics) that
    outruns this manifest fails HERE (seed → planted point drift), never as
    a silent denominator change."""
    import test_w4_why_enrichment as w4a

    w4a_sdk = _fresh_sdk()
    mine_sdk = _fresh_sdk()
    try:
        w4a_corpus = w4a._seed_e2e1_corpus(w4a_sdk)
        mine = seeding.seed_why_corpus(mine_sdk)
        # Composition equality (the 30/10 + subset denominators).  W4-a's
        # seed returns {conflicted, p9, decision, superseded, clean} where
        # conflicted = p9 + decision + superseded + plain; my seed breaks the
        # same corpus into the five family lists.
        mine_families = {k: len(v) for k, v in mine["graded"].items()}
        for family in ("p9", "decision", "superseded", "clean"):
            assert mine_families[family] == len(w4a_corpus[family]), (
                f"family {family}: mine {mine_families[family]} != W4-a {len(w4a_corpus[family])}"
            )
        mine_conflicted = sum(mine_families[f] for f in ("p9", "decision", "superseded", "plain"))
        assert mine_conflicted == len(w4a_corpus["conflicted"]), (
            f"conflicted composition drifted: mine {mine_conflicted} "
            f"!= W4-a {len(w4a_corpus['conflicted'])}"
        )
        assert mine_families["clean"] == len(w4a_corpus["clean"])
        # Content-set equality across the whole planted graph (the role
        # templates mirror W4-a's content strings exactly — a renamed
        # template outruns the manifest here).
        assert _all_point_contents(mine_sdk) == _all_point_contents(w4a_sdk), (
            "my seeding's planted content differs from W4-a's E2E-1 corpus"
        )
    finally:
        w4a_sdk.close()
        mine_sdk.close()


def test_manifest_topics_cover_w4a_families(m2_env):
    """The manifest's deterministic topic lists are the graded corpus: every
    W4-a-seeded family list maps 1:1 onto the manifest's topic keys via the
    planted content templates (point id drift cannot outrun the manifest —
    the gold's dig_deeper_targets resolve against THESE topics)."""
    manifest = corpus.load_manifest()
    gold = corpus.gold_doc()
    assert {e["point_id"] for e in gold["entries"]} == {
        t for lst in manifest["topics"].values() for t in lst
    }
    for family, count in (
        ("p9", 10),
        ("decision", 5),
        ("superseded", 5),
        ("plain", 10),
        ("clean", 10),
    ):
        assert len(manifest["topics"][family]) == count
        for topic in manifest["topics"][family]:
            assert topic.startswith(f"{family}-topic-")
