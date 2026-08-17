"""run.py --model/--query-prompt parameterization + provenance (#1349 T3).

The in-repo hard tier (#1144 eval runner) and the E2E-8 latency benchmark
(benchmarks/run_report.py) become 4-model surfaces:

- run.py --model must invoke embedder_probe.inject_model BEFORE _query_vecs
  computes query vectors (spy on inject_model; probe state faked so no model
  download is required); --query-prompt threads through to the injection;
  provenance records embedding_model from the probe state — never the
  hardcoded "all-MiniLM-L6-v2" literal.
- benchmarks/run_report.py --model injects before the measurement and its
  provenance reads the probe-recorded model id instead of the :639 literal
  (the EMBEDDING_MODEL constant does not exist until T9/PR2).
- Real-model integration (all-MiniLM-L6-v2, cached locally) verifies the
  full path end-to-end: the injected model IS what _query_vecs encodes with
  (synthetic_query_vectors False). Skipped under HF_HUB_OFFLINE when not
  cached.

Probe state shape (tools/embedder_probe.py get_state): {name, hf_id,
resolved_revision, query_prompt, dim} — provenance records ``hf_id`` (the
model id proper, matching the field's pre-existing value semantics).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests.eval.retrieval import run as run_module  # noqa: E402
from tests.eval.retrieval.run import run_eval  # noqa: E402

ARCTIC_S_HF = "snowflake/snowflake-arctic-embed-s"
MINILM_HF = "sentence-transformers/all-MiniLM-L6-v2"
HARDCODED_LITERAL = "all-MiniLM-L6-v2"


def _has_embedded() -> bool:
    try:
        import redislite.falkordb_client  # noqa: F401
        from tortoise.projection import FalkorProjection  # noqa: F401
        return True
    except Exception:
        return False


def _minilm_cached() -> bool:
    """True when all-MiniLM-L6-v2 loads offline (HF cache present).

    Checks the exact HF id the probe injects (MINILM_HF) so a cache-key
    mismatch degrades to a safe SKIP rather than a real-model FAIL."""
    try:
        from sentence_transformers import SentenceTransformer
        SentenceTransformer(MINILM_HF, local_files_only=True)
        return True
    except Exception:
        return False


def _fake_state(name: str) -> dict:
    hf = {"arctic-s": ARCTIC_S_HF, "minilm": MINILM_HF}.get(name, f"hf/{name}")
    return {
        "name": name,
        "hf_id": hf,
        "resolved_revision": None,
        "query_prompt": None,
        "dim": 384,
    }


def _args(db_path: str, **overrides):
    class _Args:
        db = db_path
        corpus_size = 250
        seed = 42
        limit = 50
        out = None
        pool_out = None
        judge_labels = None
        baseline = None
        no_seed_corpus = False
        rebuild_queries = False
        quiet = True
        model = None
        query_prompt = None
    a = _Args()
    for k, v in overrides.items():
        setattr(a, k, v)
    return a


def _bench_args(db_path: str, **overrides):
    class _BArgs:
        db = db_path
        corpus_size = 200
        samples = 3
        seed = 42
        warmup_iters = 1
        warmup_max_iters = 4
        out = None
        no_seed_corpus = False
        skip_e2e = True
        quiet = True
        model = None
    a = _BArgs()
    for k, v in overrides.items():
        setattr(a, k, v)
    return a


# ── run.py parameterization ────────────────────────────────────────────────

@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_model_flag_injects_before_query_vecs_and_records_provenance(tmp_path):
    """--model must inject the candidate BEFORE _query_vecs runs (the injected
    singleton is exactly what _query_vecs encodes with), and provenance must
    record the probe-recorded model id — never the hardcoded literal."""
    calls: list[str] = []
    real_query_vecs = run_module._query_vecs

    def _fake_inject(name, query_prompt=None):
        calls.append("inject")
        return _fake_state(name)

    def _spy_query_vecs(oracle, queries, seed):
        calls.append("query_vecs")
        return real_query_vecs(oracle, queries, seed)

    with patch.object(run_module, "inject_model", side_effect=_fake_inject), \
         patch.object(run_module, "_query_vecs", side_effect=_spy_query_vecs), \
         patch("tools.embedder_probe.get_state", return_value=_fake_state("arctic-s")):
        report = run_eval(_args(str(tmp_path / "eval-model.db"), model="arctic-s"))

    assert calls == ["inject", "query_vecs"], \
        f"injection must precede query-vector computation, got order {calls}"
    assert report["provenance"]["embedding_model"] == ARCTIC_S_HF
    assert report["provenance"]["embedding_model"] != HARDCODED_LITERAL


def test_query_prompt_threads_to_inject_model():
    """--query-prompt must reach inject_model (arctic vendor-config
    prompt_name='query' seam)."""
    with patch.object(run_module, "inject_model",
                      return_value=_fake_state("arctic-s")) as m:
        run_module._inject_probe_model(
            _args("unused.db", model="arctic-s", query_prompt="query"))
    m.assert_called_once_with("arctic-s", query_prompt="query")


def test_no_model_skips_injection():
    """Without --model the probe is never invoked (default model behavior)."""
    with patch.object(run_module, "inject_model") as m:
        run_module._inject_probe_model(_args("unused.db"))
    m.assert_not_called()


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_model_injection_failure_aborts_run(tmp_path):
    """--model HARD-FAIL contract: a candidate that cannot load must abort
    the run (EmbedderProbeError propagates) — never a silent degrade to
    synthetic query vectors."""
    from tools.embedder_probe import EmbedderProbeError

    with patch.object(run_module, "inject_model",
                      side_effect=EmbedderProbeError("candidate unavailable")):
        with pytest.raises(EmbedderProbeError):
            run_eval(_args(str(tmp_path / "eval-fail.db"), model="bge-small"))


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
@pytest.mark.skipif(not _minilm_cached(),
                    reason="all-MiniLM-L6-v2 not in HF cache (HF_HUB_OFFLINE in CI)")
def test_real_minilm_injection_end_to_end(tmp_path):
    """Real cached-MiniLM injection: provenance records the injected model id
    AND the query vectors come from the injected model (synthetic flag off) —
    the swap genuinely reached _query_vecs."""
    from tools import embedder_probe

    embedder_probe.reset()
    try:
        report = run_eval(_args(str(tmp_path / "eval-minilm.db"), model="minilm"))
        assert report["provenance"]["embedding_model"] == MINILM_HF
        assert report["provenance"]["synthetic_query_vectors"] is False
    finally:
        embedder_probe.reset()


# ── benchmarks/run_report.py provenance ────────────────────────────────────

@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_benchmark_provenance_uses_injected_model_not_literal(tmp_path):
    """After --model arctic-s injection (probe mocked — arctic-s is not
    cached locally), the report's embedding_model equals the injected model
    id, NOT the hardcoded "all-MiniLM-L6-v2" literal (:639)."""
    from benchmarks.run_report import run_benchmark

    with patch("tools.embedder_probe.inject_model",
               return_value=_fake_state("arctic-s")), \
         patch("tools.embedder_probe.get_state",
               return_value=_fake_state("arctic-s")):
        report = run_benchmark(
            _bench_args(str(tmp_path / "bench-arctic.db"), model="arctic-s"))

    assert report["provenance"]["embedding_model"] == ARCTIC_S_HF
    assert report["provenance"]["embedding_model"] != HARDCODED_LITERAL


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
@pytest.mark.skipif(not _minilm_cached(),
                    reason="all-MiniLM-L6-v2 not in HF cache (HF_HUB_OFFLINE in CI)")
def test_benchmark_real_minilm_provenance(tmp_path):
    """Real cached-MiniLM injection through the benchmark: provenance reads
    the probe-recorded model id (real swap), not the literal."""
    from benchmarks.run_report import run_benchmark
    from tools import embedder_probe

    embedder_probe.reset()
    try:
        report = run_benchmark(
            _bench_args(str(tmp_path / "bench-minilm.db"), model="minilm"))
        assert report["provenance"]["embedding_model"] == MINILM_HF
        assert report["provenance"]["synthetic_vectors"] is False
    finally:
        embedder_probe.reset()
