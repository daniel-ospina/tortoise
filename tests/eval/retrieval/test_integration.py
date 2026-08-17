"""End-to-end runner smoke on embedded FalkorDBLite (#1144).

Warn-only semantics (mirrors tests/bench/test_smoke_embedded.py): embedded
numbers are NOT prod-parity (no FTS/HNSW; FTS flaky, structural empty
without kind) — the assertions target harness correctness: report shape,
all strategies present, metrics + CIs + provenance, judge-label path, and
the gate-vs-baseline path. The strategy columns on embedded are documented
as environment artifacts, never asserted for absolute quality.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(REPO_ROOT))

from tests.eval.retrieval.run import STRATEGIES, run_eval, write_outputs  # noqa: E402


def _has_embedded() -> bool:
    try:
        import redislite.falkordb_client  # noqa: F401
        from tortoise.projection import FalkorProjection  # noqa: F401
        return True
    except Exception:
        return False


def _args(db_path: str, **overrides):
    class _Args:
        db = db_path
        corpus_size = 400
        seed = 42
        limit = 50
        depth = None
        k_sweep = False
        graph_ranker_arm = False
        corpus_variant = "plain"
        stub_projection = False
        out = None
        pool_out = None
        judge_labels = None
        baseline = None
        no_seed_corpus = False
        rebuild_queries = False
        quiet = True
    a = _Args()
    for k, v in overrides.items():
        setattr(a, k, v)
    return a


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_runner_report_shape_and_gate(tmp_path):
    db1 = str(tmp_path / "eval1.db")
    db2 = str(tmp_path / "eval2.db")
    report1 = run_eval(_args(db1))
    out1 = write_outputs(report1, str(tmp_path / "r1.json"))

    assert report1["schema_version"] == 2  # #1348 SCHEMA v2 (fused k=60 alias retained)
    assert report1["issue"] == "1144"
    assert report1["provenance"]["db_mode"] == "embedded-falkordblite"
    assert report1["provenance"]["corpus"]["n_points"] > 0
    assert report1["oracle"]["n_queries"] == 100
    assert report1["oracle"]["tiers"] == {"easy": 50, "medium": 30, "hard": 20}
    assert report1["authored"]["n_queries"] == 50
    assert report1["authored"]["labels_status"] == "pending"

    # #1348: default run is shape-identical to v1 (no fused_rerank unless the
    # arm is ON; k_sweep/population_counts sections present; strategies ==
    # per_query keys for the arm-OFF case).
    assert "fused_rerank" not in report1["strategies"]
    assert report1["population_counts"]
    assert report1["rerank_verdict"] is None
    perq = report1["per_query"]
    assert set(report1["strategies"]) == set(next(iter(perq.values())))

    # All strategies present with all four metrics + CIs.
    for strat in report1["strategies"]:
        m = report1["metrics"][strat]
        for metric in ("ndcg@10", "p@5", "r@10", "mrr"):
            assert metric in m and "value" in m[metric] and "ci" in m[metric]
        assert 0.0 <= m["ndcg@10"]["value"] <= 1.0

    # by_tier present for easy/medium/hard.
    assert set(report1["by_tier"]) == {"easy", "medium", "hard"}
    # paired deltas vs fused present for every single strategy.
    assert set(report1["paired_vs_fused"]) == {"fts", "vector", "structural", "tfidf"}
    # per_query covers the 100 oracle queries.
    assert len(report1["per_query"]) == 100

    # Gate path: run again against the baseline report → SHIP (same code).
    report2 = run_eval(_args(db2, baseline=out1))
    gate = report2["gate_vs_baseline"]
    assert gate is not None
    assert gate["verdict"] in ("SHIP", "WARN", "BLOCK")
    assert "ndcg_delta_points" in gate and "p5_delta_points" in gate


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_report_is_regenerable(tmp_path):
    """Baseline lock (review P2.6): the report is REGENERABLE — the same
    args on a fresh DB reproduce the identical metrics, per-query scores,
    tier breakdown, paired deltas, oracle meta, and authored meta
    (provenance timestamp/git_sha/host naturally differ). This is what
    makes the committed baseline a trustworthy "where we stand" anchor
    for the gate."""
    r1 = run_eval(_args(str(tmp_path / "regen1.db"), corpus_size=300))
    r2 = run_eval(_args(str(tmp_path / "regen2.db"), corpus_size=300))
    for key in ("metrics", "per_query", "by_tier", "paired_vs_fused",
                "oracle", "authored", "authored_metrics", "seed",
                "corpus_target"):
        assert r1[key] == r2[key], f"report {key} not regenerable (differs across runs)"


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_baseline_report_carries_embedded_caveats():
    """Baseline lock (review P2.6): the committed baseline must document
    the embedded-environment caveats (FTS/structural degrade, vector
    brute-force, synthetic query vectors) and the R@10 oracle-denominator
    note, so nobody reads embedded numbers as prod-parity quality."""
    baseline = json.loads(
        (Path(REPO_ROOT) / "tests/eval/retrieval/baseline"
         / "baseline-embedded-2026-08-17.json").read_text()
    )
    assert baseline["provenance"]["db_mode"] == "embedded-falkordblite"
    assert "embedded_engine" in baseline["provenance"]
    joined_notes = " ".join(baseline.get("notes", [])).lower()
    for caveat in ("fts", "structural", "brute-force", "synthetic",
                   "not quality statements", "authoritative"):
        assert caveat in joined_notes, f"baseline missing caveat: {caveat}"
    assert "grade-2 target set" in joined_notes
    assert baseline["provenance"]["corpus"]["n_points"] > 0


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_runner_pool_emission_and_judge_labels(tmp_path):
    db = str(tmp_path / "eval-pool.db")
    pool_path = str(tmp_path / "pool.json")
    report = run_eval(_args(db, pool_out=pool_path))
    pool = json.loads(Path(pool_path).read_text())
    assert pool["schema_version"] == 1
    assert len(pool["queries"]) == 50  # authored queries only
    for q in pool["queries"]:
        assert q["query"]
        # Top-50/strategy/query, deduped → ≤ 5*50 points per query.
        assert 1 <= len(q["points"]) <= 5 * 50
        assert all("id" in p and "content" in p for p in q["points"])
    assert report["authored_pool"] == pool_path

    # Re-run with adjudicated labels → authored metrics computed.
    labels = {
        q["id"]: {p["id"]: (2 if i < len(q["points"]) // 3 else 1)
                  for i, p in enumerate(q["points"])}
        for q in pool["queries"]
    }
    labels_path = str(tmp_path / "labels.json")
    Path(labels_path).write_text(json.dumps(labels))
    report2 = run_eval(_args(db, judge_labels=labels_path))
    assert report2["authored"]["labels_status"] == "adjudicated"
    assert report2["authored"]["n_labeled"] == 50
    for strat in STRATEGIES:
        assert "ndcg@10" in report2["authored_metrics"][strat]


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_runner_fused_is_not_garbage_on_embedded(tmp_path):
    """The fused arm must carry real signal even on embedded (vector is the
    only contributing strategy there) — above the chance base rate."""
    db = str(tmp_path / "eval-signal.db")
    report = run_eval(_args(db))
    ndcg = report["metrics"]["fused"]["ndcg@10"]["value"]
    p5 = report["metrics"]["fused"]["p@5"]["value"]
    # Chance nDCG on a 24-topic corpus is ~0.04-0.13; measured signal must
    # be far above it (the oracle separates topics).
    assert ndcg > 0.3, f"fused nDCG {ndcg:.3f} — no measurable signal"
    assert p5 > 0.3, f"fused P@5 {p5:.3f} — no measurable signal"
    # And the oracle still bites: fused P@5 < 1.0 (hard queries pull it down).
    assert p5 < 1.0
