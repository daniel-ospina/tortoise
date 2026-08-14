"""Smoke test: the #316 benchmark harness runs end-to-end on embedded
FalkorDBLite (small corpus, warn-only semantics).

Warn-only per scoping task 4 ("CI smoke small corpus, warn-only +
fail-on-harness-error"): degraded strategy arms are EXPECTED on embedded
(no FTS/HNSW — FTS degrades, vector falls back to brute-force), so the smoke
only fails on harness-level errors: an exception, an INVALIDATED arm
(environmental failure), or a missing report. Real numbers require Docker
FalkorDB >= 4.x — see benchmarks/README.md.

Deliberately NOT using the shared-projection fixture (tests/_embedded.py):
the harness owns its corpus and DB lifecycle.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.bench


def _has_embedded() -> bool:
    try:
        import redislite.falkordb_client  # noqa: F401
        from tortoise.projection import FalkorProjection  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_harness_runs_end_to_end_on_embedded(tmp_path):
    from benchmarks.run_report import run_benchmark

    db_path = str(tmp_path / "bench-smoke.db")
    # run_benchmark owns the SDK lifecycle (opens + closes internally).
    report = run_benchmark(_smoke_args(db_path))

    # Harness-level assertions (fail-on-harness-error).
    assert report["schema_version"] == 1
    assert report["issue"] == "316"
    assert report["provenance"]["db_mode"] == "embedded-falkordblite"
    assert report["corpus_target"] == 200
    assert report["provenance"]["corpus"]["n_points"] > 0
    # Index state is engine-dependent provenance (this embedded build
    # auto-creates FTS but not HNSW) — assert keys, not exact values.
    assert set(report["provenance"]["indexes"]) == {"fts", "vector"}

    # Every strategy arm produced a censored/elevated column pair, and no arm
    # was invalidated (warn-only: degraded-fast is OK on embedded).
    for arm in ("fts", "vector", "hybrid", "tfidf"):
        col = report["arms"][arm]
        assert "censored" in col and "elevated" in col
        assert col["censored"]["invalidated"] is False
        assert col["elevated"]["invalidated"] is False

    # E2E arm ran with a verdict (achieved/cap-dominated/tail/inconclusive are
    # all valid smoke outcomes — embedded numbers are NOT prod-parity;
    # inconclusive = cap-dominated column where the healthy minority cannot
    # support a band verdict).
    e2e = report["arms"]["e2e"]
    assert e2e["verdict"] in ("achieved", "cap-dominated", "tail", "inconclusive")
    assert "headroom_ms_317" in e2e
    assert "cold_start_ms" in report and "e2e" in report["cold_start_ms"]


def _smoke_args(db_path: str):
    class _Args:
        db = db_path
        corpus_size = 200
        samples = 5
        seed = 42
        warmup_iters = 2
        warmup_max_iters = 6
        out = None
        no_seed_corpus = False
        skip_e2e = False
        quiet = True
    return _Args()
