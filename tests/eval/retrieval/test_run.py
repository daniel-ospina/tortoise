"""run.py --model/--query-prompt parameterization + provenance (#1349 T3).

The in-repo hard tier (#1144 eval runner) and the E2E-8 latency benchmark
(benchmarks/run_report.py) become 4-model surfaces:

- run.py --model must invoke embedder_probe.inject_model BEFORE _query_vecs
  computes query vectors (spy on inject_model; probe state faked so no model
  download is required); --query-prompt threads through to the injection;
  provenance records embedding_model from the probe state — never the
  default model id (EMBEDDING_MODEL).
- benchmarks/run_report.py --model injects before the measurement and its
  provenance reads the probe-recorded model id; not-injected provenance
  reads the EMBEDDING_MODEL constant (T9 re-pointed it — PR1's
  :639 DEFAULT_MODEL_ID literal was pre-constant).
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
from tortoise.embeddings import EMBEDDING_MODEL  # noqa: E402

pytestmark = pytest.mark.timeout(600)  # real-embedder tests load bge-small (~57s load) — #1349 swap

ARCTIC_S_HF = "snowflake/snowflake-arctic-embed-s"
MINILM_HF = "sentence-transformers/all-MiniLM-L6-v2"
#: The default model id — injected-candidate provenance must NEVER report it
#: (re-pointed to the EMBEDDING_MODEL constant by #1349 T9 so the assertion
#: tracks rotations).
DEFAULT_LITERAL = EMBEDDING_MODEL


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
        query_prompt = None
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

    def _spy_query_vecs(oracle, queries, seed, **kwargs):
        calls.append("query_vecs")
        return real_query_vecs(oracle, queries, seed, **kwargs)

    with patch.object(run_module, "inject_model", side_effect=_fake_inject), \
         patch.object(run_module, "_query_vecs", side_effect=_spy_query_vecs), \
         patch("tools.embedder_probe.get_state", return_value=_fake_state("arctic-s")):
        report = run_eval(_args(str(tmp_path / "eval-model.db"), model="arctic-s"))

    assert calls == ["inject", "query_vecs"], \
        f"injection must precede query-vector computation, got order {calls}"
    assert report["provenance"]["embedding_model"] == ARCTIC_S_HF
    assert report["provenance"]["embedding_model"] != DEFAULT_LITERAL


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


def test_eval_tfidf_arm_pins_the_sparse_leg(monkeypatch):
    """#2678: the ``tfidf`` arm must pass ``force_sparse=True``.

    The arm is the sparse strategy by definition, but the default scorer
    resolves the embedder and becomes a second semantic arm whenever one is
    cached — silently duplicating ``vector`` and diverging from the
    token-overlap arm the oracle tests prove. Hermetic: the graph and the
    strategy chain are stubbed, so this pins the CALL CONTRACT, not a
    retrieval result.
    """
    from tortoise import search_engine

    seen: dict[str, object] = {}

    def _fallback(query, points, limit=10, *, force_sparse=False):
        seen["force_sparse"] = force_sparse
        seen["limit"] = limit
        return []

    monkeypatch.setattr(search_engine, "degradation_chain", lambda *a, **k: {})
    monkeypatch.setattr(search_engine, "fallback_tfidf", _fallback)

    ranked = run_module.retrieve_per_strategy(
        None, "alpha", None, None, [], True, 7, depth=7)

    assert seen["force_sparse"] is True, (
        "the eval's tfidf arm must pin the sparse leg — a cached embedder "
        "would otherwise turn it into a second semantic arm (#2678)"
    )
    assert seen["limit"] == 7
    assert ranked["tfidf"] == []


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_model_injection_failure_aborts_run(tmp_path):
    """--model HARD-FAIL contract: a candidate that cannot load must abort
    the run (EmbedderProbeError propagates) — never a silent degrade to
    synthetic query vectors."""
    from tools.embedder_probe import EmbedderProbeError

    with (patch.object(run_module, "inject_model",
                       side_effect=EmbedderProbeError("candidate unavailable")),
          pytest.raises(EmbedderProbeError)):
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
    id, NOT the default model id (EMBEDDING_MODEL)."""
    from benchmarks.run_report import run_benchmark

    with patch("tools.embedder_probe.inject_model",
               return_value=_fake_state("arctic-s")), \
         patch("tools.embedder_probe.get_state",
               return_value=_fake_state("arctic-s")):
        report = run_benchmark(
            _bench_args(str(tmp_path / "bench-arctic.db"), model="arctic-s"))

    assert report["provenance"]["embedding_model"] == ARCTIC_S_HF
    assert report["provenance"]["embedding_model"] != DEFAULT_LITERAL


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


# ── T3 P2 fixes: clean argparse errors + warm-stale provenance truth ───────

def test_unknown_model_is_clean_argparse_error(capsys):
    """Fix 1 (T3 P2): run.py --model <unknown> must fail at argparse (exit 2,
    invalid choice listing the valid candidates) — never a raw KeyError
    traceback from the probe dict lookup."""
    with pytest.raises(SystemExit) as exc:
        run_module.main(["--db", "unused.db", "--model", "not-a-model",
                         "--quiet"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice" in err
    assert "not-a-model" in err
    assert "arctic-s" in err, "argparse error must list the valid choices"


def test_unknown_benchmark_model_is_clean_argparse_error(capsys):
    """Fix 1 (T3 P2): benchmarks/run_report.py --model <unknown> fails at
    argparse the same way — parity with run.py."""
    from benchmarks.run_report import main as bench_main

    with pytest.raises(SystemExit) as exc:
        bench_main(["--db", "unused.db", "--model", "not-a-model",
                    "--quiet"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice" in err
    assert "not-a-model" in err
    assert "arctic-s" in err, "argparse error must list the valid choices"


def test_query_prompt_without_model_is_argparse_error(capsys):
    """Fix 2 (T3 P2): --query-prompt without --model is a silent no-op today
    (_inject_probe_model returns early) — it must be a clean argparse error."""
    with pytest.raises(SystemExit) as exc:
        run_module.main(["--db", "unused.db", "--query-prompt", "query",
                         "--quiet"])
    assert exc.value.code == 2
    assert "--query-prompt requires --model" in capsys.readouterr().err


def test_warm_stale_provenance_reports_probe_state_when_loaded():
    """Fix 3 (T3 P2): not-injected + loaded must report the probe-recorded
    hf_id when probe state exists (warm in-process re-run — the loaded
    singleton may be a previously-injected candidate, so DEFAULT_MODEL_ID
    would be a lie), else the default id."""

    with patch("tools.embedder_probe.get_state",
               return_value=_fake_state("arctic-s")):
        # Loaded but NOT injected this run: probe state wins (truthful).
        assert run_module._resolved_embedding_model(
            use_model=True, injected=False) == ARCTIC_S_HF
    with patch("tools.embedder_probe.get_state", return_value=None):
        # No probe state → the only model _load resolves without the probe:
        # the EMBEDDING_MODEL constant (#1349 T9 re-points provenance to it).
        assert run_module._resolved_embedding_model(
            use_model=True, injected=False) == EMBEDDING_MODEL
        # Injected but no state (can't happen — inject HARD-FAILs): unavailable.
        assert run_module._resolved_embedding_model(
            use_model=False, injected=True) == "unavailable"
    # Degraded: no model at all.
    assert run_module._resolved_embedding_model(
        use_model=False, injected=False) == "unavailable"


def test_benchmark_warm_stale_provenance_reports_probe_state_when_loaded():
    """Fix 3 (T3 P2): same warm-stale truthfulness in benchmarks/run_report.py
    provenance."""
    from benchmarks.run_report import _resolved_embedding_model

    with patch("tools.embedder_probe.get_state",
               return_value=_fake_state("arctic-s")):
        assert _resolved_embedding_model(
            use_model=True, injected=False) == ARCTIC_S_HF
    with patch("tools.embedder_probe.get_state", return_value=None):
        assert _resolved_embedding_model(
            use_model=True, injected=False) == EMBEDDING_MODEL
        assert _resolved_embedding_model(
            use_model=False, injected=True) == "unavailable"


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_benchmark_query_prompt_threads_to_inject_model_and_provenance(tmp_path):
    """Fix 4 (T3 P2): run_report.py --query-prompt must reach inject_model
    (so in-path E2E-8 encodes carry the vendor prompt prefix — parity with
    run.py) and be recorded in provenance when set."""
    from benchmarks.run_report import run_benchmark

    calls: list[tuple] = []

    def _fake_inject(name, query_prompt=None, load_timeout=None):
        calls.append((name, query_prompt))
        return _fake_state(name)

    with patch("tools.embedder_probe.inject_model",
               side_effect=_fake_inject), \
         patch("tools.embedder_probe.get_state",
               return_value=_fake_state("arctic-s")):
        report = run_benchmark(_bench_args(
            str(tmp_path / "bench-qp.db"),
            model="arctic-s", query_prompt="query"))

    assert calls == [("arctic-s", "query")], \
        "--query-prompt must thread to inject_model"
    assert report["provenance"]["query_prompt"] == "query"


def test_benchmark_query_prompt_without_model_is_argparse_error(capsys):
    """Fix 4 (T3 P2) parity: run_report.py --query-prompt without --model is
    the same silent no-op — clean argparse error like run.py."""
    from benchmarks.run_report import main as bench_main

    with pytest.raises(SystemExit) as exc:
        bench_main(["--db", "unused.db", "--query-prompt", "query",
                    "--quiet"])
    assert exc.value.code == 2
    assert "--query-prompt requires --model" in capsys.readouterr().err


# ── the ONE-SPACE invariant (the vector/fused arm's soundness) ─────────────
#
# Measured 2026-11-08 on unmodified code, canonical dev env
# (`uv sync --extra embeddings`): the query side encoded with the ambient bge
# model while the seeded corpus kept the oracle's SYNTHETIC topic-centroid
# embeddings — two different spaces. vector nDCG@10 collapsed 0.855 -> 0.108,
# fused 0.835 -> 0.462, and the pre-registered gate reported BLOCK
# (ΔnDCG -37.3 points) on a tree nobody had changed. The tests below pin the
# three halves of the fix: the space is chosen from the CORPUS, the corpus is
# re-embedded when the space is the model's, and the gate refuses a
# cross-space comparison instead of calling it a regression.

class _StubModel:
    """Deterministic 384-d stand-in embedder — no download, no HF cache."""

    def __init__(self, calls: list | None = None):
        self.calls = calls if calls is not None else []

    def encode(self, texts):
        import hashlib
        import math

        from tortoise.embeddings import EMBEDDING_DIM
        out = []
        for t in texts:
            self.calls.append(t)
            raw = hashlib.sha256(t.encode()).digest()
            vec = [raw[i % len(raw)] / 255.0 + 1e-6 for i in range(EMBEDDING_DIM)]
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


def test_gate_refuses_a_cross_space_comparison():
    """A model-space run vs a synthetic-space baseline measures the
    INSTRUMENT, not the change — INCOMPARABLE, never a BLOCK."""
    import random

    per_query = {"q1": {"fused": {"ndcg@10": 0.90, "p@5": 0.90}}}
    baseline = {"q1": {"fused": {"ndcg@10": 0.10, "p@5": 0.10}}}

    cross = run_module._gate_vs_baseline(
        per_query, baseline, random.Random(0),
        space=run_module.EMBED_SPACE_MODEL,
        baseline_space=run_module.EMBED_SPACE_SYNTHETIC,
    )
    assert cross["verdict"] == "INCOMPARABLE"
    assert "re-baseline" in cross["reason"].lower()
    assert cross["space"] == run_module.EMBED_SPACE_MODEL
    assert cross["baseline_space"] == run_module.EMBED_SPACE_SYNTHETIC

    same = run_module._gate_vs_baseline(
        per_query, baseline, random.Random(0),
        space=run_module.EMBED_SPACE_MODEL,
        baseline_space=run_module.EMBED_SPACE_MODEL,
    )
    assert same["verdict"] == "SHIP", \
        "same-space runs must reach the pre-registered gate, not a refusal"

    # no space information at all (legacy callers) keeps the old behaviour
    blind = run_module._gate_vs_baseline(per_query, baseline, random.Random(0))
    assert blind["verdict"] == "SHIP"

    # `unknown` on EITHER side refuses, including unknown-vs-unknown: two
    # absences of the same fact are not the same space. Without this, a
    # --no-seed-corpus run on a centroid db gated against a --no-seed-corpus
    # run on a model-embedded db scores as a comparable pair (false PASS).
    for space, base_space in ((run_module.EMBED_SPACE_UNKNOWN,
                               run_module.EMBED_SPACE_UNKNOWN),
                              (run_module.EMBED_SPACE_UNKNOWN,
                               run_module.EMBED_SPACE_MODEL),
                              (run_module.EMBED_SPACE_SYNTHETIC,
                               run_module.EMBED_SPACE_UNKNOWN)):
        refusal = run_module._gate_vs_baseline(
            per_query, baseline, random.Random(0),
            space=space, baseline_space=base_space,
        )
        assert refusal["verdict"] == "INCOMPARABLE", \
            f"{space} vs {base_space} must refuse, not score"
        assert "could not be established" in refusal["reason"], \
            "the refusal must name the unknown space as its reason"


def test_gate_refuses_a_different_model_in_the_model_space():
    """bge and arctic are different encoders, so a `--model <candidate>` run
    gated against a baseline measured with another model would report the SWAP
    as the delta — on unmodified code. Same space KIND, not the same space."""
    import random

    per_query = {"q1": {"fused": {"ndcg@10": 0.90, "p@5": 0.90}}}
    baseline = {"q1": {"fused": {"ndcg@10": 0.10, "p@5": 0.10}}}
    model, base_model = run_module.EMBED_SPACE_MODEL, run_module.EMBED_SPACE_MODEL

    cross_model = run_module._gate_vs_baseline(
        per_query, baseline, random.Random(0),
        space=model, baseline_space=base_model,
        model_id="Snowflake/snowflake-arctic-embed-s",
        baseline_model_id="BAAI/bge-small-en-v1.5",
    )
    assert cross_model["verdict"] == "INCOMPARABLE"
    assert "model swap" in cross_model["reason"], \
        "the refusal must name the swap as the thing the delta would measure"
    assert cross_model["embedding_model"] == "Snowflake/snowflake-arctic-embed-s"
    assert cross_model["baseline_embedding_model"] == "BAAI/bge-small-en-v1.5"

    same_model = run_module._gate_vs_baseline(
        per_query, baseline, random.Random(0),
        space=model, baseline_space=base_model,
        model_id="BAAI/bge-small-en-v1.5",
        baseline_model_id="BAAI/bge-small-en-v1.5",
    )
    assert same_model["verdict"] == "SHIP", \
        "one model, one space: the pre-registered gate must still run"

    # legacy baselines record no model id — a missing identity is not a
    # conflict, so the comparison keeps working (fail-open on ABSENCE only)
    legacy = run_module._gate_vs_baseline(
        per_query, baseline, random.Random(0),
        space=model, baseline_space=base_model,
        model_id="BAAI/bge-small-en-v1.5",
    )
    assert legacy["verdict"] == "SHIP"


def test_baseline_embedding_space_reads_legacy_baselines():
    """Baselines that predate the field; their provenance still answers — and
    a pre-fix baseline WITH an embedder is `unknown`, not `model`."""
    legacy = {"schema_version": 1, "provenance": {"timestamp_utc": "2026-08-17"}}
    assert (run_module._baseline_embedding_space(legacy)
            == run_module.EMBED_SPACE_SYNTHETIC), \
        "no embedding_model recorded == no embedder == the synthetic space"
    assert (run_module._baseline_embedding_space(
        {"provenance": {"embedding_model": "unavailable"}})
        == run_module.EMBED_SPACE_SYNTHETIC)
    # A schema-2 baseline written BEFORE this fix carries `embedding_model` but
    # no declared space, and that run re-embedded nothing: it measured a MIXED
    # corpus-synthetic / query-model arm. Labelling it `model` would compare a
    # fixed run against it as comparable and report the instrument change as a
    # ~+70-point SHIP; `unknown` makes the gate refuse instead.
    assert (run_module._baseline_embedding_space(
        {"schema_version": 2,
         "provenance": {"embedding_model": "BAAI/bge-small-en-v1.5"}})
        == run_module.EMBED_SPACE_UNKNOWN), \
        "a pre-fix baseline with an embedder is mixed-space, in no single space"
    declared = {"provenance": {"corpus_embedding_space": "model",
                               "embedding_model": "unavailable"}}
    assert run_module._baseline_embedding_space(declared) == run_module.EMBED_SPACE_MODEL, \
        "an explicit declaration wins over the derived legacy reading"


def test_query_vecs_never_borrow_the_model_in_the_synthetic_space():
    """The regression itself: a synthetic corpus must NOT get model-encoded
    queries, however loadable the ambient model is."""
    oracle = run_module.build_topic_oracle(42)
    queries = [{"id": "q001", "query": "pricing tiers enterprise"}]
    stub = _StubModel()

    with patch.object(run_module, "_loaded_embedding_model", return_value=stub):
        vecs, use_model = run_module._query_vecs(
            oracle, queries, 42, space=run_module.EMBED_SPACE_SYNTHETIC,
        )

    assert use_model is False
    assert stub.calls == [], "model.encode must never run in the synthetic space"
    assert vecs["q001"] == oracle.query_vector_for("pricing tiers enterprise", 42)


def test_query_vecs_use_the_model_in_the_model_space():
    oracle = run_module.build_topic_oracle(42)
    queries = [{"id": "q001", "query": "pricing tiers enterprise"}]
    stub = _StubModel()

    with patch.object(run_module, "_loaded_embedding_model", return_value=stub):
        vecs, use_model = run_module._query_vecs(
            oracle, queries, 42, space=run_module.EMBED_SPACE_MODEL,
        )

    assert use_model is True
    assert stub.calls == ["pricing tiers enterprise"]
    assert vecs["q001"] == stub.encode(["pricing tiers enterprise"])[0]


def test_corpus_embedding_space_follows_the_corpus_not_the_environment():
    with patch.object(run_module, "_loaded_embedding_model", return_value=None):
        assert (run_module._corpus_embedding_space(seeded_synthetic=True)[0]
                == run_module.EMBED_SPACE_SYNTHETIC)
        # --no-seed-corpus: the graph's vectors were NOT written by this run, so
        # the space cannot be established at all — never silently assumed (a
        # centroid corpus graded against model queries is the original defect,
        # and an eval db keeps its centroids across runs)
        assert (run_module._corpus_embedding_space(seeded_synthetic=False)[0]
                == run_module.EMBED_SPACE_UNKNOWN)
    stub = _StubModel()
    with patch.object(run_module, "_loaded_embedding_model", return_value=stub):
        assert (run_module._corpus_embedding_space(seeded_synthetic=True)
                == (run_module.EMBED_SPACE_MODEL, stub))
        # ...and a LOADED model does not make an existing corpus's space known:
        # `--no-seed-corpus` means the vectors on disk were written by someone
        # else, so the answer stays `unknown` (and no re-embed of a point list
        # this run never seeded).
        assert (run_module._corpus_embedding_space(seeded_synthetic=False)[0]
                == run_module.EMBED_SPACE_UNKNOWN), \
            "an ambient model must not launder an unverified corpus's space"


def test_query_vecs_propagate_a_model_encode_failure():
    """In the model space the corpus is ALREADY re-embedded (`_reembed_corpus`
    runs first), so a query-encode failure must propagate: falling back to
    oracle vectors here rebuilds the two-space arm while provenance still says
    'model' (and a half-encoded arm is worse than no verdict)."""
    oracle = run_module.build_topic_oracle(42)
    queries = [{"id": "q001", "query": "pricing tiers enterprise"},
               {"id": "q002", "query": "pricing tiers team"}]

    class _FailingModel:
        def encode(self, texts):
            raise RuntimeError("model call stalled")

    with (patch.object(run_module, "_loaded_embedding_model",
                       return_value=_FailingModel()),
          pytest.raises(RuntimeError, match="stalled")):
        run_module._query_vecs(
            oracle, queries, 42, space=run_module.EMBED_SPACE_MODEL,
        )


def test_query_vecs_encode_nothing_in_the_synthetic_space():
    """The synthetic space has no model to encode with — the queries come from
    the oracle, and no encode is attempted at all (the old "degrade the whole
    arm on the first failure" branch was unreachable and is gone)."""
    oracle = run_module.build_topic_oracle(42)
    queries = [{"id": "q001", "query": "pricing tiers enterprise"}]

    class _ExplodingModel:
        calls = 0

        def encode(self, texts):
            type(self).calls += 1
            raise AssertionError("encode must never be attempted here")

    with patch.object(run_module, "_loaded_embedding_model",
                      return_value=_ExplodingModel()):
        vecs, use_model = run_module._query_vecs(
            oracle, queries, 42, space=run_module.EMBED_SPACE_SYNTHETIC,
        )

    assert use_model is False
    assert _ExplodingModel.calls == 0
    assert vecs["q001"] == oracle.query_vector_for("pricing tiers enterprise", 42)


class _PromptAwareStub(_StubModel):
    """A stand-in embedder that models sentence-transformers' PROMPT rules.

    ST applies a CONSTRUCTOR-level ``default_prompt_name`` to every
    ``encode()`` call whose caller passes neither ``prompt`` nor
    ``prompt_name`` — document text included. That inheritance is the whole of
    F4, so a stub that ignored it would make the assertion vacuous.
    """

    def __init__(self, default_prompt_name: str | None = "query"):
        super().__init__()
        self.default_prompt_name = default_prompt_name
        self.default_prompt = None
        self.applied: list[str | None] = []   # prompt actually applied, per encode

    def encode(self, texts, prompt_name=None, prompt=None):
        if prompt is None and prompt_name is None:
            prompt = self.default_prompt
            prompt_name = self.default_prompt_name
        applied = prompt if prompt is not None else (
            f"<{prompt_name}>" if prompt_name is not None else None)
        self.applied.append(applied)
        return super().encode(texts)


class _RecordingGraph:
    """Minimal graph double: records the Cypher params, reports rows written."""

    def __init__(self):
        self.rows: list[list[dict]] = []

    def query(self, _cypher, params=None):
        self.rows.append(params["batch"])

        class _Result:
            def __init__(self, n):
                self.result_set = [[n]]
        return _Result(len(params["batch"]))


def test_reembed_corpus_encodes_documents_without_the_query_prompt():
    """F4: the corpus is DOCUMENTS, so it must be encoded the way the product's
    writer encodes documents — with no prompt — even though the injected
    candidate carries the vendor's query instruction as its constructor
    default (which EVERY encode() would otherwise inherit)."""
    stub = _PromptAwareStub(default_prompt_name="query")
    points = [{"id": "p001", "content": "alpha beta gamma"},
              {"id": "p002", "content": "delta epsilon"}]

    written = run_module._reembed_corpus(_RecordingGraph(), points, stub)

    assert written == 2
    assert stub.applied == [None], (
        "the corpus is document text: inheriting the constructor's query prompt "
        f"would store a vector shape the product never writes (applied={stub.applied})"
    )
    assert stub.default_prompt_name == "query", \
        "the query side must keep the instruction it was injected for"


def test_reembed_corpus_uses_the_production_text_composition():
    """F4 (second half): production encodes `_truncate_for_embedding(content, 512)`, so
    the corpus re-embed must compose the SAME text — a 600-word document encoded
    whole is a different vector from the one the product stores."""
    from tortoise.embeddings import _truncate_for_embedding

    long_content = " ".join(f"w{i}" for i in range(600))
    points = [{"id": "p001", "content": long_content},
              {"id": "p002", "content": "short document"}]
    stub = _PromptAwareStub(default_prompt_name=None)

    run_module._reembed_corpus(_RecordingGraph(), points, stub)

    assert stub.calls == [_truncate_for_embedding(long_content, 512),
                          "short document"], \
        "the corpus must be encoded through the product's text composition"
    assert len(stub.calls[0].split()) == 512, "the 512-word cap must apply"


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_run_reembeds_the_seeded_corpus_into_the_query_space(tmp_path):
    """Whole-run: model space ⇒ the corpus is re-encoded, and provenance
    states both facts (the re-embed count and the space)."""
    stub = _StubModel()
    with patch.object(run_module, "_loaded_embedding_model", return_value=stub):
        report = run_eval(_args(str(tmp_path / "eval-space.db"), corpus_size=60))

    prov = report["provenance"]
    assert prov["corpus_embedding_space"] == run_module.EMBED_SPACE_MODEL
    assert prov["synthetic_query_vectors"] is False
    assert prov["corpus_reembedded"] == report["seeded_points"] > 0, \
        "every seeded point must be re-encoded, or the corpus stays in the old space"
    assert stub.calls, "the model must actually encode the corpus"
    # ⛔ No `assert prov["corpus"]["embeddings_hash"]` here: the field is
    # present whether or not the fingerprint was taken before the re-embed, so
    # it stays green under the ordering mutant its comment claimed to catch.
    # The ordering is asserted where it can fail —
    # `test_the_fingerprint_is_taken_after_the_reembed` (F5).


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_run_without_a_model_stays_in_the_synthetic_space(tmp_path):
    """No model ⇒ documented degraded fallback: synthetic queries AND the
    seeded centroid corpus — one space, nothing re-encoded."""
    with patch.object(run_module, "_loaded_embedding_model", return_value=None):
        report = run_eval(_args(str(tmp_path / "eval-synth.db"), corpus_size=60))

    prov = report["provenance"]
    assert prov["corpus_embedding_space"] == run_module.EMBED_SPACE_SYNTHETIC
    assert prov["synthetic_query_vectors"] is True
    assert prov["corpus_reembedded"] == 0


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_the_fingerprint_is_taken_after_the_reembed(tmp_path):
    """The corpus fingerprint HASHES the stored vectors, so the two spaces must
    read differently — otherwise provenance cannot detect the space change it
    exists to detect, and `corpus_embedding_space` is the only witness."""
    with patch.object(run_module, "_loaded_embedding_model", return_value=None):
        synthetic = run_eval(_args(str(tmp_path / "eval-fp-synth.db"), corpus_size=60))
    with patch.object(run_module, "_loaded_embedding_model", return_value=_StubModel()):
        model = run_eval(_args(str(tmp_path / "eval-fp-model.db"), corpus_size=60))

    h_synth = synthetic["provenance"]["corpus"]["embeddings_hash"]
    h_model = model["provenance"]["corpus"]["embeddings_hash"]
    assert h_synth and h_model, "both fingerprints must hash the stored vectors"
    assert h_synth != h_model, (
        "identical embeddings_hash across the two spaces means the fingerprint "
        "was taken BEFORE the re-embed"
    )


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_run_reusing_existing_corpus_claims_no_space(tmp_path):
    """--no-seed-corpus + a LOADED model: the vectors already in the graph were
    not written by this run, so the space is `unknown` and nothing is re-encoded
    (re-encoding the freshly GENERATED point list would MERGE points this run
    never seeded into the graph). The gate then refuses instead of gating an
    unverified corpus against a model-space baseline."""
    db = str(tmp_path / "eval-reuse.db")
    stub = _StubModel()
    with patch.object(run_module, "_loaded_embedding_model", return_value=None):
        first = run_eval(_args(db, corpus_size=60))
    assert first["provenance"]["corpus_embedding_space"] == run_module.EMBED_SPACE_SYNTHETIC

    with patch.object(run_module, "_loaded_embedding_model", return_value=stub):
        reused = run_eval(_args(db, corpus_size=60, no_seed_corpus=True))

    prov = reused["provenance"]
    assert prov["corpus_embedding_space"] == run_module.EMBED_SPACE_UNKNOWN, (
        "an ambient model must not launder the space of a corpus this run did "
        "not write"
    )
    assert prov["corpus_reembedded"] == 0, \
        "the no-seed path must not re-encode (it would MERGE unseeded points)"
    assert stub.calls == [], "nothing to encode in the unknown space"

    # ...and gating it against the committed MODEL-space baseline must REFUSE
    # (INCOMPARABLE), never report the instrument difference as a quality delta.
    baseline_path = (Path(__file__).resolve().parent / "baseline"
                     / "baseline-embedded-model-2026-11-08.json")
    with patch.object(run_module, "_loaded_embedding_model", return_value=stub):
        gated = run_eval(_args(db, corpus_size=60, no_seed_corpus=True,
                               baseline=str(baseline_path)))
    gate = gated["gate_vs_baseline"]
    assert gate is not None, "the baseline must reach the gate"
    assert gate["verdict"] == "INCOMPARABLE", (
        "an unverified corpus must never be scored as comparable against a "
        "model-space baseline"
    )
    assert gate["space"] == run_module.EMBED_SPACE_UNKNOWN
    assert "could not be established" in gate["reason"], (
        "the refusal must name the unknown space, and it holds even when BOTH "
        "sides say 'unknown' — two absences of the same fact are not the same "
        "space"
    )


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_run_does_not_claim_a_space_over_corpus_points_it_did_not_rewrite(tmp_path):
    """The one-space rule covers the WHOLE index, not only the ids this run
    rewrote (review F1 at c2748ad48fb1).

    Seeding is `UNWIND … MERGE` and the re-embed overwrites only the ids this
    run generated, so point ids are a POSITIONAL prefix: a smaller run over an
    existing db re-embeds `p-0..p-(n-1)` and leaves the tail of the previous,
    larger corpus in its OLD space — while provenance said ``model`` and the
    gate scored the mixed index against a clean model-space baseline. That is
    the exact failure this PR exists to remove, wearing the authority of the
    fix that declares the invariant (reproduced here via the README's fixed
    quickstart db path, scaled down after a larger run).
    """
    db = str(tmp_path / "eval-partial.db")
    with patch.object(run_module, "_loaded_embedding_model", return_value=None):
        full = run_eval(_args(db, corpus_size=60))
    assert full["provenance"]["corpus_embedding_space"] == run_module.EMBED_SPACE_SYNTHETIC

    stub = _StubModel()
    with patch.object(run_module, "_loaded_embedding_model", return_value=stub):
        partial = run_eval(_args(db, corpus_size=40))

    prov = partial["provenance"]
    assert prov["corpus_reembedded"] == partial["seeded_points"] == 40
    assert prov["corpus"]["n_points"] > prov["corpus_reembedded"], (
        "the fixture must leave points in the graph that this run did NOT rewrite "
        "— otherwise there is no uncovered population to detect"
    )
    assert prov["corpus_embedding_space"] == run_module.EMBED_SPACE_UNKNOWN, (
        "20 leftover points keep their centroid vectors while this run "
        "re-embedded 40 into the model space — the index is MIXED, so no single "
        "space can be claimed for it"
    )
    assert prov["corpus_points_unverified"] == (
        prov["corpus"]["n_points"] - prov["corpus_reembedded"]
    ), "provenance must carry the size of the population it could not vouch for"

    # ...and the gate must REFUSE the mixed index instead of scoring it as a
    # comparable model-space run (INCOMPARABLE, naming the space).
    baseline_path = (Path(__file__).resolve().parent / "baseline"
                     / "baseline-embedded-model-2026-11-08.json")
    with patch.object(run_module, "_loaded_embedding_model", return_value=stub):
        gated = run_eval(_args(db, corpus_size=40, baseline=str(baseline_path)))
    gate = gated["gate_vs_baseline"]
    assert gate is not None, "the baseline must reach the gate"
    assert gate["verdict"] == "INCOMPARABLE", (
        "a mixed index must never be scored against a clean model-space "
        "baseline: the delta would be the un-rewritten tail, reported as a "
        "quality statement"
    )
    assert gate["space"] == run_module.EMBED_SPACE_UNKNOWN

    # Positive control: a run that DOES cover the whole graph still claims the
    # space — otherwise "unknown whenever the db is not empty" would pass this
    # test while making every reused eval db useless.
    with patch.object(run_module, "_loaded_embedding_model", return_value=_StubModel()):
        covering = run_eval(_args(db, corpus_size=60))
    covering_prov = covering["provenance"]
    assert covering_prov["corpus_points_unverified"] == 0
    assert covering_prov["corpus_embedding_space"] == run_module.EMBED_SPACE_MODEL, (
        "re-running at a corpus size that covers the whole existing population "
        "rewrites every vector into one space, so `model` is a fact"
    )


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_run_gating_another_model_refuses_instead_of_scoring(tmp_path):
    """End-to-end: a model-space run gated against a baseline measured with a
    DIFFERENT model is refused, not scored — otherwise the delta is the model
    swap (the #1349 workflow), reported as a quality verdict on unmodified
    code. The remedy is a baseline measured with the candidate."""
    import json as _json

    base = _json.loads((Path(__file__).resolve().parent / "baseline"
                        / "baseline-embedded-model-2026-11-08.json").read_text())
    base["provenance"]["embedding_model"] = "Snowflake/snowflake-arctic-embed-s"
    alt_baseline = tmp_path / "baseline-other-model.json"
    alt_baseline.write_text(_json.dumps(base))

    with patch.object(run_module, "_loaded_embedding_model", return_value=_StubModel()):
        report = run_eval(_args(str(tmp_path / "eval-model-id.db"), corpus_size=60,
                                baseline=str(alt_baseline)))

    prov = report["provenance"]
    assert prov["corpus_embedding_space"] == run_module.EMBED_SPACE_MODEL
    gate = report["gate_vs_baseline"]
    assert gate is not None
    assert gate["verdict"] == "INCOMPARABLE", (
        "the same space KIND under two encoders is not the same space"
    )
    assert gate["embedding_model"] == prov["embedding_model"]
    assert gate["baseline_embedding_model"] == "Snowflake/snowflake-arctic-embed-s"
    assert gate["embedding_model"] != gate["baseline_embedding_model"]

    # ...and the SAME model (the committed baseline, unmodified) still gates.
    with patch.object(run_module, "_loaded_embedding_model", return_value=_StubModel()):
        same = run_eval(_args(
            str(tmp_path / "eval-model-same.db"), corpus_size=60,
            baseline=str(Path(__file__).resolve().parent / "baseline"
                         / "baseline-embedded-model-2026-11-08.json"),
        ))
    assert same["gate_vs_baseline"]["verdict"] in ("SHIP", "WARN", "BLOCK"), (
        "a matching model id must reach the pre-registered gate, not a refusal: "
        + str(same["gate_vs_baseline"])
    )
