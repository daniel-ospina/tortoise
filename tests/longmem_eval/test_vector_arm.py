"""Task 2 (#1349) — LongMemEval vector-only arm + FalkorDB mode.

Covers the vector arm of the embedder-selection harness:
  * ``vector_search`` — empty-graph MODEL_ENCODE_FAILED abort, elevated
    timeout, never ``tortoise_fts_query``, breaker-open marker,
  * nDCG@10 (binary gains, log₂(i+2), IDCG all-evidence-first capped 10,
    zero-evidence → 0.0) + P@10/P@5,
  * ``--retriever vector|hybrid`` selection,
  * encode cache — model-keyed ``sha256(model_id + prompt_name + text)``,
    disk-persisted, intercepts the ingest path,
  * checkpoint — atomic temp+rename, truncation-resume re-runs just the
    corrupt question, cross-surface/cross-model key isolation, stale
    #1144-era format unreadable,
  * ``--retrieval-only`` report shape (accuracy None, methodology note),
  * dataset digest-pin — tampered/truncated cache re-downloads or errors,
  * ``--db`` mode — per-(question, model) graph namespace isolation
    (probe-level, no live FalkorDB required).

Runs fully offline (mini fixture, mocked reader/judge, fake embeddings).
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import tortoise.embeddings as emb
import tortoise.search_engine as se
from tools.longmem_eval import dataset as ds
from tools.longmem_eval import (
    encode_cache,
    retrieve,
)
from tools.longmem_eval import run as runner
from tools.longmem_eval.ingest import ingest_haystack
from tools.longmem_eval.judge import MockJudge
from tools.longmem_eval.reader import MockReader
from tools.longmem_eval.retrieve import retrieve_for_question
from tortoise.model_adapters import (
    MODELS,
    DeepSeekDirectModel,
    OpenRouterModel,
    RotatingModel,
    RoutingModel,
    VeniceModel,
    build_extractor_model,
)
from tortoise.sdk import TortoiseSDK

MINI = Path(__file__).parent.parent / "fixtures" / "longmemeval_mini.json"

_FAKE_DIM = 32
_TOKEN_RE = re.compile(r"[a-z0-9']+")


def _mini() -> list[dict]:
    return json.loads(MINI.read_text(encoding="utf-8"))


def _fresh_sdk(tmp_path) -> TortoiseSDK:
    return TortoiseSDK(str(tmp_path / "lme.db"))


def _fake_vec(text: str) -> list[float]:
    """Deterministic token-overlap embedding (stable across processes)."""
    dims: dict[str, float] = {}
    for tok in _TOKEN_RE.findall((text or "").lower()):
        dims[tok] = dims.get(tok, 0.0) + 1.0
    vec = [0.0] * _FAKE_DIM
    for tok, c in dims.items():
        vec[zlib.crc32(tok.encode("utf-8")) % _FAKE_DIM] += c
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class _FakeModel:
    """Stand-in for the EmbeddingModel singleton (injected-model contract)."""

    def encode(self, texts: list[str], **kwargs):
        return np.array([_fake_vec(t) for t in texts], dtype=np.float32)


@pytest.fixture(autouse=True)
def _reset_breakers():
    se.reset_circuit_breakers()
    yield
    se.reset_circuit_breakers()


@pytest.fixture()
def fake_embeddings(monkeypatch):
    """Deterministic embedding path: ingest + query encode via _fake_vec."""
    monkeypatch.setattr(emb, "compute_embedding",
                        lambda content, max_tokens=512: _fake_vec(content))
    monkeypatch.setattr(emb.EmbeddingModel, "get", lambda: _FakeModel())


# ── nDCG@10 / P@10 / P@5 (hand-computed binary-gain) ────────────────────────

def test_ndcg_binary_gain_hand_computed():
    # DCG@10 for [hit, hit, miss, miss, miss...] with 2 evidence turns:
    #   dcg = 1/log2(3) + 1/log2(4) ≈ 1.1309
    #   idcg = 1/log2(2) + 1/log2(3) ≈ 1.6309
    #   ndcg ≈ 0.6934
    ranked = [f"p{i}" for i in range(10)]
    ndcg = retrieve.ndcg_at_k(ranked, {"p1", "p2"}, k=10)
    assert ndcg == pytest.approx(1.13093 / 1.63093, rel=1e-4)
    assert ndcg == pytest.approx(0.69343, abs=1e-4)

    # all evidence turns retrieved first → ndcg = 1.0
    assert retrieve.ndcg_at_k(list(range(20)), set(range(20)), k=10) == 1.0
    assert retrieve.ndcg_at_k(list(range(5)), set(range(5)), k=10) == 1.0

    # zero evidence turns → 0.0 (included in the mean by the report)
    assert retrieve.ndcg_at_k(list(range(10)), set(), k=10) == 0.0


def test_ndcg_ideal_dcg_for_gt10_evidence_approx_4_54():
    # IDCG@10 with >10 evidence turns = Σ_{i=0..9} 1/log₂(i+2) ≈ 4.5435 —
    # the plan pins ≈ 4.54 for the all-evidence-first case.
    idcg = retrieve.dcg_at_k([1.0] * 10, k=10)
    assert idcg == pytest.approx(4.5435, abs=1e-3)


def test_precision_at_10_and_5():
    ranked = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    # 2 evidence turns in top-10 AND top-5 (a at rank 1, d at rank 4)
    assert retrieve.precision_at_k(ranked, {"a", "d"}, k=10) == pytest.approx(0.2)
    assert retrieve.precision_at_k(ranked, {"a", "d"}, k=5) == pytest.approx(0.4)
    # 3 in top-10 (ranks 6-8), 0 in top-5 → P@10 = 0.3, P@5 = 0.0
    assert retrieve.precision_at_k(ranked, {"f", "g", "h"}, k=10) == pytest.approx(0.3)
    assert retrieve.precision_at_k(ranked, {"f", "g", "h"}, k=5) == pytest.approx(0.0)
    # zero evidence → 0.0
    assert retrieve.precision_at_k(ranked, set(), k=10) == 0.0


# ── vector_search: empty-graph abort / elevated timeout / no fts ────────────

def test_vector_search_empty_graph_raises_model_encode_failed(tmp_path, monkeypatch):
    """A graph with zero embedding-bearing points must abort with the
    MODEL_ENCODE_FAILED class — never silently return empty recall."""
    monkeypatch.setattr(emb, "compute_embedding", lambda content, max_tokens=512: None)
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, _mini()[0])  # points written with NULL embedding
        with pytest.raises(retrieve.ModelEncodeFailedError):
            retrieve.vector_search(sdk, "some query", 10)
    finally:
        sdk.close()


def test_run_main_empty_graph_exits_with_distinct_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(emb, "compute_embedding", lambda content, max_tokens=512: None)
    with pytest.raises(SystemExit) as ei:
        runner.run_main([
            "--data", str(MINI), "--limit", "1", "--split", "s", "--mock",
            "--retriever", "vector", "--output", str(tmp_path / "o.json"),
            "--work-dir", str(tmp_path),
        ])
    assert ei.value.code == retrieve.MODEL_ENCODE_FAILED_EXIT
    err = capsys.readouterr().err
    assert "MODEL_ENCODE_FAILED" in err


def test_vector_search_swallowed_failure_raises_breaker_open(tmp_path, monkeypatch, fake_embeddings):
    """run_vector_query SWALLOWS infra failures — on timeout/connection/query
    error it records a breaker failure and returns [] (never raises).
    vector_search must detect the mid-call breaker bump and raise
    VectorBreakerOpenError — an empty result is indistinguishable from a
    legit no-hit and must never be reported as recall 0."""
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, _mini()[0])

        def _swallow(graph, query_vec, limit, **kwargs):
            se._breaker("vector").record_failure()  # what the swallow path does
            return []

        monkeypatch.setattr(se, "run_vector_query", _swallow)
        with pytest.raises(retrieve.VectorBreakerOpenError):
            retrieve.vector_search(sdk, "Ava board game", 10)
    finally:
        sdk.close()


def test_vector_search_tripping_swallow_raises_breaker_open(tmp_path, monkeypatch, fake_embeddings):
    """The TRIPPING call itself (the 3rd consecutive swallow) is caught too:
    Q1/Q2 bump _fails, Q3 trips the breaker and returns [] — still must route
    through breaker-open accounting, not recall 0."""
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, _mini()[0])

        def _trip(graph, query_vec, limit, **kwargs):
            se._breaker("vector").record_failure()  # 3rd → OPEN
            return []

        monkeypatch.setattr(se, "run_vector_query", _trip)
        b = se._breaker("vector")
        b.record_failure()
        b.record_failure()  # pre-seed 2 failures; the call is the tripping one
        assert not b.is_open()
        with pytest.raises(retrieve.VectorBreakerOpenError):
            retrieve.vector_search(sdk, "Ava board game", 10)
    finally:
        sdk.close()


def test_midrun_breaker_failure_marks_breaker_open_not_recall_zero(tmp_path, monkeypatch, fake_embeddings):
    """Gate-level: a swallowed run_vector_query failure mid-run must route the
    question through breaker_open dropped-accounting — NEVER a silent recall-0
    into the primary means."""
    b = se._breaker("vector")
    assert b._fails == 0

    def _swallow(graph, query_vec, limit, **kwargs):
        b.record_failure()
        return []

    monkeypatch.setattr(se, "run_vector_query", _swallow)
    outcomes, report = runner.run_evaluation(
        _mini()[:2], reader=MockReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
        retriever="vector",
    )
    assert len(outcomes) == 2
    assert all(o["breaker_open"] is True for o in outcomes)
    assert all(o.get("dropped_reason") == "breaker_open" for o in outcomes)
    # not counted as recall 0 in the means denominator
    assert report["n_dropped"] == 2
    assert report["dropped"]["breaker_open"] == 2
    assert report["n_questions"] == 0


def test_vector_search_passes_elevated_timeout(tmp_path, monkeypatch, fake_embeddings):
    """timeout_ms=5000 must reach run_vector_query (default 500ms + breaker
    would trip on large haystacks and return [] which reads as recall 0)."""
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, _mini()[0])
        calls: list[dict] = []

        def _recorder(graph, query_vec, limit, **kwargs):
            calls.append({"limit": limit, **kwargs})
            return [("lme:q:s0:t0", 0.9)]

        monkeypatch.setattr(se, "run_vector_query", _recorder)
        hits = retrieve.vector_search(sdk, "Ava board game", 10)
        assert len(calls) == 1
        assert calls[0]["timeout_ms"] == retrieve.VECTOR_TIMEOUT_MS == 5000
        # Epic #1647 (PR #1684 CI-fix): is_embedded reflects the lane —
        # embedded lane True, docker lane False (the redirect's
        # server-mode flag). The timeout + api + limit contract is
        # lane-independent.
        _uri = os.environ.get("TORTOISE_DB_URI")
        assert calls[0]["is_embedded"] == (_uri is None), (
            f"is_embedded={calls[0]['is_embedded']} must match the lane")
        # embedded lane: no vector-index API (brute-force); docker lane: the
        # server exposes the cypher vector API (R2/D8 divergence)
        if _uri:
            assert calls[0]["vector_index_api"] == "cypher"
        else:
            assert calls[0]["vector_index_api"] is None
        assert calls[0]["limit"] == 10
        assert hits == [("lme:q:s0:t0", 0.9)]
    finally:
        sdk.close()


def test_vector_arm_never_calls_tortoise_fts_query(tmp_path, monkeypatch, fake_embeddings):
    """The vector arm must NEVER route through hybrid RRF (tortoise_fts_query)."""
    def _boom(*a, **k):
        raise AssertionError("tortoise_fts_query must not be called by the vector arm")

    monkeypatch.setattr(TortoiseSDK, "tortoise_fts_query", _boom)
    sdk = _fresh_sdk(tmp_path)
    try:
        q = _mini()[0]
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5, 10), top_k=10, retriever="vector")
        assert ret["retriever"] == "vector"
        assert ret["hits"], "vector retrieval should return ranked hits"
        assert "ndcg@10" in ret and "p@10" in ret and "p@5" in ret
        assert ret["ranked_ids"] == [h["id"] for h in ret["hits"]]
        assert isinstance(ret["evidence_turn_matches"], list)
        # nDCG is internally consistent with the ranked ids it was computed from
        ev = {
            f"lme:{q['question_id']}:s{si}:t{ti}"
            for si, session in enumerate(q.get("haystack_sessions") or [])
            for ti, turn in enumerate(session)
            if turn.get("has_answer")
        }
        assert ret["ndcg@10"] == pytest.approx(
            retrieve.ndcg_at_k(ret["ranked_ids"], ev, k=10))
        assert 0.0 <= ret["ndcg@10"] <= 1.0
    finally:
        sdk.close()


# ── retriever selection ─────────────────────────────────────────────────────

def test_retriever_flag_selects_vector_vs_hybrid(tmp_path, monkeypatch, fake_embeddings):
    sdk = _fresh_sdk(tmp_path)
    try:
        q = _mini()[0]
        ingest_haystack(sdk, q)
        calls = {"hybrid": 0, "vector": 0}

        def _hybrid(sdk_, query, **kwargs):
            calls["hybrid"] += 1
            return []

        def _vector(sdk_, query, **kwargs):
            calls["vector"] += 1
            return []

        monkeypatch.setattr(retrieve, "hybrid_search", _hybrid)
        monkeypatch.setattr(retrieve, "vector_search", _vector)

        retrieve_for_question(sdk, q, ks=(5,), top_k=5, retriever="hybrid")
        assert calls == {"hybrid": 1, "vector": 0}

        retrieve_for_question(sdk, q, ks=(5,), top_k=5, retriever="vector")
        assert calls == {"hybrid": 1, "vector": 1}
    finally:
        sdk.close()


def test_retriever_default_is_hybrid(tmp_path, monkeypatch, fake_embeddings):
    sdk = _fresh_sdk(tmp_path)
    try:
        q = _mini()[0]
        ingest_haystack(sdk, q)
        calls = {"hybrid": 0, "vector": 0}

        def _hybrid(sdk_, query, **kwargs):
            calls["hybrid"] += 1
            return []

        monkeypatch.setattr(retrieve, "hybrid_search", _hybrid)
        monkeypatch.setattr(retrieve, "vector_search",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no")))
        retrieve_for_question(sdk, q, ks=(5,), top_k=5)  # no retriever kwarg
        assert calls == {"hybrid": 1, "vector": 0}
    finally:
        sdk.close()


# ── breaker-open: marker + dropped accounting (never recall 0) ─────────────

def test_breaker_open_outcome_marked_not_recall_zero(tmp_path, monkeypatch, fake_embeddings):
    b = se._breaker("vector")
    b.record_failure()
    b.record_failure()
    b.record_failure()  # fail_threshold=3 → OPEN
    assert b.is_open()

    outcomes, report = runner.run_evaluation(
        _mini()[:2], reader=MockReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
        retriever="vector",
    )
    assert len(outcomes) == 2
    assert all(o["breaker_open"] is True for o in outcomes)
    assert all(o.get("dropped_reason") == "breaker_open" for o in outcomes)
    # dropped-question accounting: count surfaced, excluded from means
    assert report["n_dropped"] == 2
    assert report["dropped"]["n"] == 2
    assert report["dropped"]["breaker_open"] == 2
    # not counted as recall 0 in the means denominator
    assert report["n_questions"] == 0


def test_breaker_open_excluded_from_report_means():
    """A mixed outcome set: the dropped question must not pull means down."""
    from tools.longmem_eval.report import build_report

    def _outcome(qid, *, breaker_open=False, recall=0.5, ndcg=0.4):
        return {
            "question_id": qid, "question_type": "single-session-user",
            "question_date": "", "label": True, "hypothesis": "h",
            "session_recall@k": {"10": recall}, "turn_recall@k": {"10": recall},
            "ndcg@10": ndcg, "p@10": 0.3, "p@5": 0.2, "ranked_ids": ["a"],
            "evidence_turn_matches": ["a"], "retriever": "vector",
            "context_tokens": 10, "context_point_count": 1,
            "retrieval_latency_ms": 5.0, "reader_latency_ms": 1.0,
            "judge_latency_ms": 1.0, "total_ms": 7.0,
            "breaker_open": breaker_open, "dropped_reason": "breaker_open" if breaker_open else None,
        }

    outcomes = [
        _outcome("q1", recall=0.8, ndcg=0.7),
        _outcome("q2", recall=0.4, ndcg=0.3),
        _outcome("q3", breaker_open=True),
    ]
    # E2E-3 Precondition 2 (origin/main): recall numbers require a TRUSTED
    # dataset recall-semantics audit record. This test exercises report
    # AGGREGATION (breaker-open exclusion), not the audit logic — pass a
    # minimal trusted record (the audit itself is covered by its own suite).
    from tools.longmem_eval.dataset_audit import TRUSTED_VERDICT
    trusted_audit = {
        "verdict": TRUSTED_VERDICT,
        "n": len(outcomes),
        "fields": {"answer_session_ids": "present", "answer_turn": "absent",
                   "has_answer": "present"},
        "violations": 0,
    }
    report = build_report(
        outcomes, dataset_id="x", split="s", reader_model="r", judge_model="j",
        extraction_approach="x", ks=(10,), top_k=10,
        dataset_semantics_audit=trusted_audit,
    )
    assert report["n_questions"] == 2
    assert report["n_dropped"] == 1
    assert report["retrieval"]["turn_recall@k"]["10"] == pytest.approx(0.6)
    assert report["retrieval"]["ndcg@10"] == pytest.approx(0.5)
    assert report["accuracy"]["overall"] == 1.0  # dropped has no label influence


# ── encode cache: model keying + disk persistence + ingest interception ─────

def test_encode_cache_key_includes_model_id(tmp_path):
    p = tmp_path / "c.json"
    c_a = encode_cache.EncodeCache(p, model_id="model-a", prompt_name=None)
    c_b = encode_cache.EncodeCache(p, model_id="model-b", prompt_name=None)
    c_aq = encode_cache.EncodeCache(p, model_id="model-a", prompt_name="query")
    text = "the same text"
    assert c_a.key_for(text) != c_b.key_for(text)      # model_id in the key
    assert c_a.key_for(text) != c_aq.key_for(text)     # prompt_name in the key
    assert c_a.key_for(text) == c_a.key_for(text)      # deterministic
    assert len(c_a.key_for(text)) == 64                # sha256 hex


def test_encode_cache_disk_persistence(tmp_path):
    p = encode_cache.cache_path_for(tmp_path, "m", None)
    c = encode_cache.EncodeCache(p, model_id="m", prompt_name=None)
    c.put("hello world", [0.1, 0.2, 0.3])
    c.put("second", [1.0, 2.0])
    c.save()

    # a fresh instance over the same path sees the persisted entries
    c2 = encode_cache.EncodeCache(p, model_id="m", prompt_name=None)
    assert c2.get("hello world") == [0.1, 0.2, 0.3]
    assert c2.get("second") == [1.0, 2.0]
    assert c2.get("unseen") is None

    # namespaced-by-config: another model's cache is a separate file
    p2 = encode_cache.cache_path_for(tmp_path, model="other-model", prompt=None)
    assert p2 != p
    c3 = encode_cache.EncodeCache(p2, model_id="other-model", prompt_name=None)
    assert c3.get("hello world") is None


def test_encode_cache_intercepts_ingest_and_reuses(tmp_path, monkeypatch):
    """The cache must intercept the ingest-time encode path (the 5-10×
    cross-question redundancy) and survive across instances (crash-safe)."""
    _real = emb.compute_embedding
    calls = {"n": 0}

    def _counting(content, max_tokens=512):
        calls["n"] += 1
        return _fake_vec(content)

    monkeypatch.setattr(emb, "compute_embedding", _counting)
    cache = encode_cache.EncodeCache(tmp_path / "c.json", model_id="m")

    q = _mini()[0]
    with cache.active():
        sdk = _fresh_sdk(tmp_path)
        try:
            ingest_haystack(sdk, q)
            first = calls["n"]
            assert first > 0
            ingest_haystack(sdk, q)  # idempotent → no new encodes
            assert calls["n"] == first
        finally:
            sdk.close()
    assert emb.compute_embedding is _counting  # restored after deactivation

    # disk: a fresh cache instance serves the same content without re-encoding
    calls["n"] = 0
    cache2 = encode_cache.EncodeCache(tmp_path / "c.json", model_id="m")
    with cache2.active():
        sdk = _fresh_sdk(tmp_path)
        try:
            ingest_haystack(sdk, q)
            assert calls["n"] == 0  # every text served from the disk cache
        finally:
            sdk.close()


def test_encode_query_routes_through_active_cache(tmp_path):
    cache = encode_cache.EncodeCache(tmp_path / "c.json", model_id="m")
    with cache.active():
        v = encode_cache.encode_query(_FakeModel(), "Ava favorite board game")
        assert v == _fake_vec("Ava favorite board game")
        assert cache.get("Ava favorite board game") is not None
    # outside an active cache, encode_query encodes directly
    v2 = encode_cache.encode_query(_FakeModel(), "direct")
    assert v2 == _fake_vec("direct")


# ── checkpoint: atomicity / truncation-resume / cross-surface isolation ─────

def _minimal_outcome(qid: str, **over) -> dict:
    o = {
        "question_id": qid, "question_type": "single-session-user",
        "question_date": "", "label": True, "hypothesis": "h",
        "session_recall@k": {"5": 1.0}, "turn_recall@k": {"5": 1.0},
        "context_tokens": 10, "context_point_count": 2,
        "retrieval_latency_ms": 1.0, "reader_latency_ms": 1.0,
        "judge_latency_ms": 1.0, "total_ms": 2.0,
    }
    o.update(over)
    return o


def test_checkpoint_atomic_write_and_format(tmp_path):
    cp = tmp_path / "state.json"
    outcomes, _report = runner.run_evaluation(
        _mini()[:2], reader=MockReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path), checkpoint=str(cp),
    )
    assert len(outcomes) == 2
    assert cp.is_file()
    # atomic temp-file-then-rename → no .tmp residue
    assert not cp.with_name(cp.name + ".tmp").exists()
    data = json.loads(cp.read_text(encoding="utf-8"))
    assert data["format"] == runner.CHECKPOINT_FORMAT
    assert data["run_key"] == "embedded__hybrid__default__default"
    assert data["surface"] == "embedded" and data["retriever"] == "hybrid"


def test_checkpoint_truncated_outcome_reruns_just_that_question(tmp_path, capsys):
    cp = tmp_path / "state.json"
    key = "embedded__hybrid__default__default"
    good = _minimal_outcome("mini_ie_user_001", MARKER="keep-me")
    truncated = {"question_id": "mini_msr_002"}  # corrupt record
    runner._save_checkpoint(str(cp), [good, truncated], [],
                            run_key=key, surface="embedded", retriever="hybrid",
                            model=None, prompt=None)

    outcomes, _report = runner.run_evaluation(
        _mini()[:2], reader=MockReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path), checkpoint=str(cp),
    )
    by_id = {o["question_id"]: o for o in outcomes}
    # the valid record was resumed as-is (never re-encoded), the truncated one
    # was re-run with a warning — never silently dropped from the denominator.
    assert by_id["mini_ie_user_001"]["MARKER"] == "keep-me"
    assert "turn_recall@k" in by_id["mini_msr_002"]
    assert by_id["mini_msr_002"].get("MARKER") is None
    assert "truncated" in capsys.readouterr().err.lower() or \
        "corrupt" in capsys.readouterr().err.lower()


def test_checkpoint_write_failure_surfaces_error(tmp_path, monkeypatch):
    """An ENOSPC/OSError on the atomic checkpoint rename must surface as an
    error — never silently drop the question from the denominator."""
    import os as _os

    cp = tmp_path / "state.json"

    def _enospc(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(_os, "replace", _enospc)
    with pytest.raises(OSError):
        runner.run_evaluation(
            _mini()[:1], reader=None, judge=None, ks=(5,), top_k=5, split="s",
            work_dir=str(tmp_path), checkpoint=str(cp), retrieval_only=True,
        )


def test_checkpoint_cross_surface_and_model_isolation(tmp_path, capsys):
    """A `--db` HNSW run must never resume against an embedded-mode brute-force
    checkpoint ({surface}__ in the key); stale #1144-era checkpoints are
    unreadable, not misread."""
    cp = tmp_path / "state.json"
    outcome = _minimal_outcome("mini_ie_user_001")
    runner._save_checkpoint(
        str(cp), [outcome], [],
        run_key="embedded__vector__minilm__default", surface="embedded",
        retriever="vector", model="minilm", prompt=None)

    # same key → resumes
    done, _ = runner._load_checkpoint(str(cp), run_key="embedded__vector__minilm__default")
    assert "mini_ie_user_001" in done

    # surface differs → refused (brute-force checkpoint must never serve HNSW)
    done, _ = runner._load_checkpoint(str(cp), run_key="hnsw__vector__minilm__default")
    assert done == {}

    # model differs → refused (no cross-model resume collision)
    done, _ = runner._load_checkpoint(str(cp), run_key="embedded__vector__arctic-s__default")
    assert done == {}

    # stale #1144-era checkpoint (no format field) → unreadable, not misread
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps({"outcomes": [outcome]}), encoding="utf-8")
    done, _ = runner._load_checkpoint(str(stale), run_key="embedded__vector__minilm__default")
    assert done == {}

    # corrupt file → warn, never crash
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{definitely not json", encoding="utf-8")
    done, _ = runner._load_checkpoint(str(corrupt), run_key="embedded__vector__minilm__default")
    assert done == {}
    assert "warning" in capsys.readouterr().err.lower() or \
        len(capsys.readouterr().err) > 0


def test_checkpoint_key_shape():
    assert runner.checkpoint_key("hnsw", "vector", "arctic-s", "query") == \
        "hnsw__vector__arctic-s__query"
    assert runner.checkpoint_key("embedded", "hybrid", None, None) == \
        "embedded__hybrid__default__default"


def test_model_id_precedence_ladder():
    """The ``_model_id`` attr-resolution ladder: None → None; a truthy
    ``.model_id`` beats ``.id``; falsy attributes never win (fall through);
    repr is the last resort — pinned as repr, not str, via sentinels. Case
    names ride the assertion messages for failure attribution."""
    from types import SimpleNamespace as _NS

    class _SentinelRepr:
        """repr-fallback probe: distinct ``__str__``/``__repr__`` sentinels so
        a str fallback (or a bare ``object()`` oracle that re-derives the same
        0x... address) cannot false-pass."""

        def __init__(self, **attrs):
            self.__dict__.update(attrs)

        def __str__(self):
            return "STR-sentinel"

        def __repr__(self):
            return "<REPR-sentinel>"

    # One named case per attr-resolution clause of ``_model_id``. Attr-only
    # stubs are data (SimpleNamespace); repr-fallback probes are sentinel
    # instances so the last resort is pinned as repr, not str.
    cases = [
        ("no-model", None, None),
        ("model_id-attr-wins", _NS(model_id="registry/deepseek-v4-flash"),
         "registry/deepseek-v4-flash"),
        ("id-only-falls-through", _NS(id="deepseek/deepseek-v4-flash"),
         "deepseek/deepseek-v4-flash"),
        ("model_id-beats-id", _NS(model_id="registry/win", id="loser"),
         "registry/win"),
        ("falsy-model_id-falls-to-id",
         _NS(model_id="", id="deepseek/deepseek-v4-flash"),
         "deepseek/deepseek-v4-flash"),
        ("none-model_id-falls-to-id",
         _NS(model_id=None, id="deepseek/deepseek-v4-flash"),
         "deepseek/deepseek-v4-flash"),
        ("model_id-beats-empty-id", _NS(model_id="registry/win", id=""),
         "registry/win"),
        ("model_id-beats-none-id", _NS(model_id="registry/win", id=None),
         "registry/win"),
        ("whitespace-model_id-is-truthy", _NS(model_id=" "), " "),
        ("whitespace-id-is-truthy", _NS(model_id="", id=" "), " "),
        ("falsy-model_id-absent-id-repr", _SentinelRepr(model_id=""),
         "<REPR-sentinel>"),
        ("none-model_id-absent-id-repr", _SentinelRepr(model_id=None),
         "<REPR-sentinel>"),
        ("no-attrs-repr-fallback", _SentinelRepr(), "<REPR-sentinel>"),
        ("none-id-repr-fallback", _SentinelRepr(id=None), "<REPR-sentinel>"),
        ("empty-id-repr-fallback", _SentinelRepr(id=""), "<REPR-sentinel>"),
        ("both-falsy-none-empty-repr", _SentinelRepr(model_id=None, id=""),
         "<REPR-sentinel>"),
        ("both-falsy-empty-none-repr", _SentinelRepr(model_id="", id=None),
         "<REPR-sentinel>"),
        ("both-empty-empty-repr", _SentinelRepr(model_id="", id=""),
         "<REPR-sentinel>"),
        ("both-none-none-repr", _SentinelRepr(model_id=None, id=None),
         "<REPR-sentinel>"),
    ]
    for name, model, expected in cases:
        assert runner._model_id(model) == expected, f"case [{name}]"


def test_model_id_fingerprint_uses_wire_id_not_repr():
    """M4 #1732: model adapters expose ``.id`` (the API-facing wire id), not
    ``.model_id`` — the old repr fallback embedded a memory address
    (``<DeepSeekDirectModel object at 0x...>``), making the fingerprint
    non-deterministic across processes and refusing every checkpoint resume
    (CheckpointStaleError on ``extractor_model`` even with identical git_sha).

    The literal wire-id equality is the #1732 guard: an address-bearing repr
    can never equal the stable wire id. Because two fresh instances of the
    same model are pinned to the SAME literal, identical fingerprints across
    instances/runs follow — the resume-determinism contract.

    Construction is offline (requests.Session() only, no API calls); built
    inside the try so every constructed session is closed in the finally even
    if a later constructor raises."""
    specs = [
        (OpenRouterModel, "deepseek/deepseek-v4-flash"),
        (DeepSeekDirectModel, "deepseek-chat"),
        (VeniceModel, "deepseek-v4-flash"),
    ]
    adapters: list = []
    expected: list = []
    try:
        for cls, wire_id in specs:
            adapters.append(cls(wire_id))
            adapters.append(cls(wire_id))  # determinism twin
            expected += [wire_id, wire_id]
        for model, wire_id in zip(adapters, expected, strict=True):
            assert runner._model_id(model) == wire_id
    finally:
        for model in adapters:
            model.close()


def test_model_id_fingerprint_deterministic_at_composition_layer():
    """M4 #1732, composition layer: ``_build_fingerprint`` embeds
    ``_model_id(extractor_model)`` in the dict that is serialized to the
    checkpoint and compared on resume (CheckpointStaleError on mismatch).
    Fresh instances of the same model must produce IDENTICAL fingerprints (a
    repr fallback embedding a per-instance 0x... address would differ and
    refuse the resume); the field must also discriminate between models (the
    cross-model resume refusal) and propagate None for retrieval-only runs."""
    kw = dict(
        reader_model="r", judge_model="j", ks=(5,), top_k=5, split="s",
        ingest_mode="embedded", max_retries=1, dataset_fingerprint="x",
        rerank_config={},
    )

    # retrieval-only runs carry no extractor → None propagates as None
    assert runner._build_fingerprint(extractor_model=None, **kw)["extractor_model"] is None

    adapters: list = []
    try:
        adapters.append(OpenRouterModel("deepseek/deepseek-v4-flash"))
        adapters.append(OpenRouterModel("deepseek/deepseek-v4-flash"))  # determinism twin
        adapters.append(OpenRouterModel("deepseek/deepseek-v4-pro"))  # same class, different id
        adapters.append(DeepSeekDirectModel("deepseek-chat"))  # different class
        adapters.append(DeepSeekDirectModel("deepseek/deepseek-v4-flash"))  # diff class, SAME wire id

        fp1 = runner._build_fingerprint(extractor_model=adapters[0], **kw)
        fp2 = runner._build_fingerprint(extractor_model=adapters[1], **kw)
        assert fp1 == fp2
        assert fp1["extractor_model"] == "deepseek/deepseek-v4-flash"

        # the discriminator must discriminate at both granularities — same
        # class with a different wire id, and a different class — and
        # ``extractor_model`` must be the ONLY differing field (no unrelated
        # nondeterminism leaks into the fingerprint)
        fp3 = runner._build_fingerprint(extractor_model=adapters[2], **kw)
        fp4 = runner._build_fingerprint(extractor_model=adapters[3], **kw)
        assert fp3["extractor_model"] == "deepseek/deepseek-v4-pro"
        assert fp4["extractor_model"] == "deepseek-chat"
        assert runner._fingerprint_diffs(fp1, fp3) == ["extractor_model"]
        assert runner._fingerprint_diffs(fp1, fp4) == ["extractor_model"]

        # The fingerprint identity is wire-id-only by design (the #1732 fix):
        # the SAME wire id across provider classes yields the same fingerprint
        # — the provider is a routing detail, not part of the effective run
        # config, so a cross-provider resume is deliberately accepted. Pin the
        # contract so a future change to class-based identity is explicit.
        fp5 = runner._build_fingerprint(extractor_model=adapters[4], **kw)
        assert fp5 == fp1
    finally:
        for model in adapters:
            model.close()


def _pin_extractor_env(monkeypatch, *, keys: tuple[str, ...] = (),
                       provider: str | None = None) -> None:
    """Pin the extractor-router env to a deterministic provider set:
    ``keys`` selects which of deepseek/openrouter/venice are "configured"
    (env presence only — offline) and ``provider`` optionally sets an
    explicit TORTOISE_EXTRACTOR_PROVIDER. 0 keys → the lenient
    single-OpenRouter fallback (RoutingModel, 1 member); deepseek+openrouter
    → RoutingModel (primary deepseek-direct + openrouter fallback — the
    unset-env production default); all 3 → RotatingModel; an explicit
    openrouter primary + deepseek fallback key → RoutingModel with a
    NON-alphabetical primary (pins the (primary, fallback) join order)."""
    for var in ("TORTOISE_EXTRACTOR_PROVIDER", "TORTOISE_EXTRACT_MODEL",
                "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "VENICE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    if provider is not None:
        monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", provider)
    if "deepseek" in keys:
        monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    if "openrouter" in keys:
        monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    if "venice" in keys:
        monkeypatch.setenv("VENICE_API_KEY", "x")


def test_model_id_wrapper_path_stable_no_address(monkeypatch):
    """M7 #1739, Gap 1: the DEFAULT CLI extractor path (``--extractor-model``
    unset → ``build_extractor_model()``) returns RoutingModel (1-2
    providers) or RotatingModel (3+) — neither exposes ``.model_id`` nor
    ``.id``, so the pre-fix ``_model_id`` fell through to ``repr(model)`` →
    ``<...RoutingModel object at 0x...>``: every cross-process resume via
    the default path raised CheckpointStaleError even with identical
    git_sha (the #1549 baseline protocol's "run it in slices" path).

    Post-fix ``_model_id`` composes the member adapters structurally
    (``provider:wire-id``, joined by ``+``): deterministic, never
    address-bearing, IDENTICAL across fresh instances. Pinned: 1-provider
    RoutingModel (no keys — emits the BARE member fingerprint, comparable
    to the equivalent bare MODELS entry), 2-provider RoutingModel
    (deepseek+openrouter: primary deepseek-direct + openrouter fallback,
    shape-prefixed ``routing:``) AND its non-alphabetical mirror (explicit
    openrouter primary — discriminates the (primary, fallback) join order
    from a sorted join), 3-provider RotatingModel (members SORTED by
    provider, shape-prefixed ``rotating:`` — pool order is a routing
    detail, so the joined literal is exact-pinnable), plus tuned wrappers
    (max_tokens, temperature) proving the member tuning suffix rides the
    wrapper composition. Constructing adapters is offline
    (requests.Session only)."""
    shapes = [
        dict(keys=(), max_tokens=None, temperature=0.0,
             cls=RoutingModel,
             expected="deepseek/deepseek-v4-flash"),
        dict(keys=("deepseek", "openrouter"), max_tokens=None,
             temperature=0.0, cls=RoutingModel,
             expected=("routing:deepseek-direct:deepseek-chat"
                       "+openrouter:deepseek/deepseek-v4-flash")),
        dict(provider="openrouter", keys=("deepseek", "openrouter"),
             max_tokens=None, temperature=0.0, cls=RoutingModel,
             # non-alphabetical primary: a sorted-join regression would flip
             # the members (deepseek-direct < openrouter), changing this
             # literal — order IS effective config for RoutingModel
             expected=("routing:openrouter:deepseek/deepseek-v4-flash"
                       "+deepseek-direct:deepseek-chat")),
        dict(keys=("deepseek", "openrouter", "venice"), max_tokens=None,
             temperature=0.0, cls=RotatingModel,
             expected=("rotating:deepseek-direct:deepseek-chat"
                       "+openrouter:deepseek/deepseek-v4-flash"
                       "+venice:deepseek-v4-flash")),
        # tuning rides the wrapper composition too (member suffixes) — a
        # tuned run-level build (any non-default max_tokens/temperature)
        # must fingerprint differently from the uncapped one (the ingest_v2
        # session-worker factory forwards the resolved max_tokens/
        # temperature; the unset path is UNCAPPED — see _build_cli_extractor_model)
        dict(keys=(), max_tokens=500, temperature=0.0, cls=RoutingModel,
             expected="deepseek/deepseek-v4-flash|max_tokens=500"),
        dict(keys=(), max_tokens=500, temperature=0.5, cls=RoutingModel,
             expected=("deepseek/deepseek-v4-flash"
                       "|max_tokens=500|temperature=0.5")),
    ]
    for shape in shapes:
        _pin_extractor_env(monkeypatch, keys=shape["keys"],
                           provider=shape.get("provider"))
        adapters = []
        try:
            adapters.append(build_extractor_model(
                max_tokens=shape["max_tokens"],
                temperature=shape["temperature"]))
            adapters.append(build_extractor_model(
                max_tokens=shape["max_tokens"],
                temperature=shape["temperature"]))
            a, b = adapters
            fp_a = runner._model_id(a)
            fp_b = runner._model_id(b)
            assert isinstance(a, shape["cls"]), (
                f"keys={shape['keys']}: wrong wrapper class")
            assert fp_a == fp_b, (
                f"keys={shape['keys']}: not stable across instances")
            assert "0x" not in fp_a, (
                f"keys={shape['keys']}: address-bearing repr leaked: {fp_a!r}")
            assert fp_a == shape["expected"], (
                f"keys={shape['keys']}: {fp_a!r}")
        finally:
            for m in adapters:
                close = getattr(m, "close", None)
                if close is not None:
                    close()
                else:  # RoutingModel has no close — close the inner adapters
                    for inner in (getattr(m, "primary", None),
                                  getattr(m, "fallback", None)):
                        if inner is not None:
                            inner.close()


def test_build_cli_extractor_model_fingerprints_serving_config(monkeypatch):
    """M7 #1739 / #1742: ``_build_cli_extractor_model`` builds the model a
    CLI run will actually serve AND fingerprint — the effective-config
    contract holds on every CLI path shape: explicit registry model at
    session_workers=1 (registry tuning applies), unset → the uncapped
    router wrapper at session_workers=1, and the worker-FACTORY config at
    session_workers>1. The factory can express only max_tokens/temperature
    — expressible tuning is passed THROUGH (a spec'd run at sw>1
    fingerprints identically to sw=1: the same effective config), entries
    with inexpressible knobs (thinking_budget/disable_reasoning) are
    REFUSED loudly, and the M5 unknown-spec gate applies on both paths
    (a typo at sw>1 fails fast instead of passing a garbage wire id)."""
    _pin_extractor_env(monkeypatch, keys=())
    adapters = []
    try:
        # session_workers=1 + explicit spec → registry model (tuning applies)
        adapters.append(runner._build_cli_extractor_model(
            spec="deepseek-v4-pro", session_workers=1))
        assert runner._model_id(adapters[-1]) == (
            "deepseek/deepseek-v4-pro|max_tokens=500")
        # session_workers=1 + unset → uncapped router wrapper (1-lane bare)
        adapters.append(runner._build_cli_extractor_model(
            spec=None, session_workers=1))
        assert runner._model_id(adapters[-1]) == "deepseek/deepseek-v4-flash"
        # session_workers>1 + spec: expressible tuning is served THROUGH the
        # factory — identical fingerprint to session_workers=1 (the same
        # effective config, so a sw toggle resumes cleanly)
        adapters.append(runner._build_cli_extractor_model(
            spec="deepseek-v4-pro", session_workers=4))
        assert runner._model_id(adapters[-1]) == (
            "deepseek/deepseek-v4-pro|max_tokens=500")
        assert runner._model_id(adapters[-1]) == runner._model_id(adapters[0])
        # session_workers>1 + unset → the worker-factory config: UNCAPPED,
        # matching the session_workers=1 owner decision — the SAME
        # fingerprint (an unset sw toggle keeps the same effective config)
        adapters.append(runner._build_cli_extractor_model(
            spec=None, session_workers=4))
        assert runner._model_id(adapters[-1]) == "deepseek/deepseek-v4-flash"
        assert runner._model_id(adapters[-1]) == runner._model_id(adapters[1])
        # session_workers>1 + direct spec: the RESOLVED wire id is used — the
        # registry key is never a valid wire id; the _REGISTRY_KEY_TO_ID remap
        # gives every lane a valid id ('deepseek-flash-direct' →
        # 'deepseek/deepseek-chat' — the OpenRouter lane needs the prefixed
        # id; bare ids 404 there; 'solar-pro4' → its entry id
        # 'upstage/solar-pro4'). The lane set differs from session_workers=1
        # (direct-only DeepSeekDirectModel vs the router wrapper), so the
        # fingerprints legitimately DIFFER — a toggle is REFUSED (safe).
        adapters.append(runner._build_cli_extractor_model(
            spec="deepseek-flash-direct", session_workers=4))
        assert runner._model_id(adapters[-1]) == "deepseek/deepseek-chat"
        adapters.append(runner._build_cli_extractor_model(
            spec="deepseek-flash-direct", session_workers=1))
        assert runner._model_id(adapters[-1]) == "deepseek-chat"
        assert runner._model_id(adapters[-2]) != runner._model_id(adapters[-1])
        adapters.append(runner._build_cli_extractor_model(
            spec="solar-pro4", session_workers=4))
        assert runner._model_id(adapters[-1]) == "upstage/solar-pro4"
        # MULTI-LANE env: a spec'd sw toggle is provider-routed at sw>1 (the
        # router composition) vs the bare registry adapter at sw=1 → the
        # fingerprints differ → the toggle is REFUSED (documented safe
        # direction; the fingerprint records what each path serves)
        _pin_extractor_env(monkeypatch, keys=("deepseek", "openrouter"))
        adapters.append(runner._build_cli_extractor_model(
            spec="deepseek-v4-pro", session_workers=1))
        assert runner._model_id(adapters[-1]) == (
            "deepseek/deepseek-v4-pro|max_tokens=500")
        adapters.append(runner._build_cli_extractor_model(
            spec="deepseek-v4-pro", session_workers=4))
        assert runner._model_id(adapters[-1]) == (
            "routing:deepseek-direct:deepseek-v4-pro|max_tokens=500"
            "+openrouter:deepseek/deepseek-v4-pro|max_tokens=500")
        assert runner._model_id(adapters[-2]) != runner._model_id(adapters[-1])
        # the M5 pinning guard applies on BOTH paths (fail fast, never a
        # garbage wire id in the checkpoint)
        with pytest.raises(SystemExit):
            runner._build_cli_extractor_model(spec="nope", session_workers=1)
        with pytest.raises(SystemExit):
            runner._build_cli_extractor_model(spec="nope", session_workers=4)
        # inexpressible tuning at session_workers>1 → loud refusal (never a
        # silent reasoning-on flip)
        with pytest.raises(SystemExit):
            runner._build_cli_extractor_model(
                spec="deepseek-v4-pro-noreason", session_workers=4)
    finally:
        for m in adapters:
            close = getattr(m, "close", None)
            if close is not None:
                close()
            else:  # RoutingModel has no close — close the inner adapters
                for inner in (getattr(m, "primary", None),
                              getattr(m, "fallback", None)):
                    if inner is not None:
                        inner.close()


def test_model_id_wrapper_shape_discriminates_routing_vs_rotating():
    """M7 #1739 (code-review hardening): a failover RoutingModel and a
    rotation pool over the SAME members are different effective configs —
    the shape-prefixed composition (``routing:`` vs ``rotating:``) must
    keep them apart (a plain ``provider:wire-id`` join would fingerprint
    them identically and silently accept a cross-shape resume). Also pins
    the single-lane rule: a 1-member wrapper emits the bare member
    fingerprint, so the default path compares against the equivalent bare
    MODELS entry (the #1732 single-adapter contract)."""
    adapters = []
    try:
        adapters.append(OpenRouterModel("deepseek/deepseek-v4-flash"))
        adapters.append(DeepSeekDirectModel("deepseek-chat"))
        a, b = adapters
        routing = RoutingModel(a, b)
        rotating = RotatingModel([a, b])
        fp_routing = runner._model_id(routing)
        fp_rotating = runner._model_id(rotating)
        assert fp_routing == (
            "routing:openrouter:deepseek/deepseek-v4-flash"
            "+deepseek-direct:deepseek-chat")
        assert fp_rotating == (
            "rotating:deepseek-direct:deepseek-chat"
            "+openrouter:deepseek/deepseek-v4-flash")
        assert fp_routing != fp_rotating
        # single-lane wrapper → bare member fingerprint
        single = RoutingModel(a, None)
        assert runner._model_id(single) == "deepseek/deepseek-v4-flash"
        assert runner._model_id(single) == runner._model_id(a)
        # max_tokens=0 is a REAL cap (not the uncapped None default) — it
        # must ride the fingerprint (pins the is-not-None omission rule)
        capped = OpenRouterModel("deepseek/deepseek-v4-flash", max_tokens=0)
        uncapped = OpenRouterModel("deepseek/deepseek-v4-flash")
        assert runner._model_id(capped) == (
            "deepseek/deepseek-v4-flash|max_tokens=0")
        assert runner._model_id(uncapped) == "deepseek/deepseek-v4-flash"
        assert runner._model_id(capped) != runner._model_id(uncapped)
        # memberless wrapper (defensive guard) → None, never an empty string
        empty_routing = object.__new__(RoutingModel)
        empty_routing.primary = None
        empty_routing.fallback = None
        empty_rotating = object.__new__(RotatingModel)
        empty_rotating.providers = []
        assert runner._model_id(empty_routing) is None
        assert runner._model_id(empty_rotating) is None
    finally:
        for m in adapters:
            m.close()


def test_model_id_tuning_variants_discriminate():
    """M7 #1739, Gap 2: three MODELS registry entries construct the SAME
    wire id (``deepseek/deepseek-v4-pro``) with DIFFERENT tuning:
    ``deepseek-v4-pro`` (max_tokens=500), ``deepseek-v4-pro-xhigh``
    (max_tokens=500, temperature=0.0), ``deepseek-v4-pro-noreason``
    (max_tokens=8000, disable_reasoning=True — reasoning OFF). The pre-fix
    fingerprint was wire-id-only: identical fingerprints → a reasoning-ON
    checkpoint silently resumed by reasoning-OFF ``-noreason`` — the "wrong
    fix silently reuses mismatched-config results" hazard. Post-fix:
    non-default tuning rides the fingerprint, so ``-xhigh`` and
    ``-noreason`` differ; identical tuning (base vs ``-xhigh`` are the same
    effective config) stays equal. Also pins the THIRD tuning knob
    (``thinking_budget`` — deepseek-r1-xhigh) and the default-tuning
    omission branch for the pro wire-id family (bare id, no suffix — the
    #1732 bare-wire-id contract)."""
    adapters = []
    try:
        adapters.append(MODELS["deepseek-v4-pro-xhigh"]())
        adapters.append(MODELS["deepseek-v4-pro-noreason"]())
        adapters.append(MODELS["deepseek-v4-pro"]())
        adapters.append(MODELS["deepseek-v4-pro-noreason"]())
        adapters.append(MODELS["deepseek-r1-xhigh"]())
        adapters.append(OpenRouterModel("deepseek/deepseek-v4-pro"))
        adapters.append(OpenRouterModel("deepseek/deepseek-v4-pro",
                                        max_tokens=500, temperature=0.5))
        (xhigh, noreason, base, twin, r1_xhigh, default_pro,
         warm) = adapters
        fp_xhigh = runner._model_id(xhigh)
        fp_noreason = runner._model_id(noreason)
        assert fp_xhigh == "deepseek/deepseek-v4-pro|max_tokens=500"
        assert fp_noreason == (
            "deepseek/deepseek-v4-pro|max_tokens=8000|disable_reasoning=True")
        assert fp_xhigh != fp_noreason
        # base and -xhigh are the same effective config → same fingerprint
        assert runner._model_id(base) == fp_xhigh
        # determinism twin (the resume-acceptance contract)
        assert runner._model_id(twin) == fp_noreason
        # the third tuning knob: thinking_budget rides the fingerprint
        assert runner._model_id(r1_xhigh) == (
            "deepseek/deepseek-r1-0528|max_tokens=500|thinking_budget=2000")
        # default tuning → bare wire id, no suffix (the omission branch)
        assert runner._model_id(default_pro) == "deepseek/deepseek-v4-pro"
        # the fourth knob: non-default temperature rides the fingerprint —
        # same wire id + max_tokens as -xhigh, different temperature
        assert runner._model_id(warm) == (
            "deepseek/deepseek-v4-pro|max_tokens=500|temperature=0.5")
        assert runner._model_id(warm) != fp_xhigh
    finally:
        for m in adapters:
            m.close()


def test_checkpoint_resume_gate_accepts_same_model_fresh_instance(tmp_path):
    """M4 #1732, end-to-end: the checkpoint resume gate must accept a fresh
    instance of the SAME extractor model (identical fingerprint) and refuse
    a different model — CheckpointStaleError naming ``extractor_model``. This
    is the outcome the bug actually broke: every resume was refused even with
    identical git_sha because the fingerprint embedded a per-instance
    0x... address."""
    kw = dict(
        reader_model="r", judge_model="j", ks=(5,), top_k=5, split="s",
        ingest_mode="embedded", max_retries=1, dataset_fingerprint="x",
        rerank_config={},
    )
    cp = tmp_path / "state.json"
    adapters: list = []
    try:
        adapters.append(OpenRouterModel("deepseek/deepseek-v4-flash"))  # a
        adapters.append(OpenRouterModel("deepseek/deepseek-v4-flash"))  # b: fresh, same model
        adapters.append(DeepSeekDirectModel("deepseek-chat"))  # c: different class
        adapters.append(OpenRouterModel("deepseek/deepseek-v4-pro"))  # d: same class, diff id
        adapters.append(DeepSeekDirectModel("deepseek/deepseek-v4-flash"))  # e: diff class, SAME wire id
        a, b, c, d, e = adapters
        runner._save_checkpoint(
            str(cp), [_minimal_outcome("q1")], [],
            fingerprint=runner._build_fingerprint(extractor_model=a, **kw))
        # resume with a fresh instance of the same model → accepted
        done, _ = runner._load_checkpoint(
            str(cp),
            expected_fingerprint=runner._build_fingerprint(
                extractor_model=b, **kw))
        assert "q1" in done
        # a different model is refused, naming extractor_model as the ONLY
        # differing field (a superset diff list fails the exact-match regex)
        with pytest.raises(runner.CheckpointStaleError,
                           match=r"differs on \['extractor_model'\]"):
            runner._load_checkpoint(
                str(cp),
                expected_fingerprint=runner._build_fingerprint(
                    extractor_model=c, **kw))
        # same class but different wire id → also refused, same field
        with pytest.raises(runner.CheckpointStaleError,
                           match=r"differs on \['extractor_model'\]"):
            runner._load_checkpoint(
                str(cp),
                expected_fingerprint=runner._build_fingerprint(
                    extractor_model=d, **kw))
        # multi-field drift → the gate names ALL differing fields (proves the
        # exact-match superset guard: a second differing field must surface)
        multi = runner._build_fingerprint(
            extractor_model=c, **dict(kw, reader_model="other-reader"))
        with pytest.raises(runner.CheckpointStaleError,
                           match=r"differs on \['extractor_model', 'reader_model'\]"):
            runner._load_checkpoint(str(cp), expected_fingerprint=multi)
        # extractor ↔ None crossover: the checkpoint run_key does not encode
        # extractor_model, so the fingerprint gate alone guards these — both
        # directions must be refused
        with pytest.raises(runner.CheckpointStaleError,
                           match=r"differs on \['extractor_model'\]"):
            runner._load_checkpoint(
                str(cp),
                expected_fingerprint=runner._build_fingerprint(
                    extractor_model=None, **kw))
        # the historical #1732 residue: a checkpoint written by the broken
        # binary whose stored extractor_model is an address-bearing repr must
        # be refused against a fresh wire-id fingerprint (the migration path)
        cp_residue = tmp_path / "residue.json"
        residue_fp = dict(runner._build_fingerprint(extractor_model=a, **kw))
        residue_fp["extractor_model"] = "<OpenRouterModel object at 0x7f1234abcd>"
        runner._save_checkpoint(
            str(cp_residue), [_minimal_outcome("q1")], [],
            fingerprint=residue_fp)
        with pytest.raises(runner.CheckpointStaleError,
                           match=r"differs on \['extractor_model'\]"):
            runner._load_checkpoint(
                str(cp_residue),
                expected_fingerprint=runner._build_fingerprint(
                    extractor_model=b, **kw))
        # deliberate cross-provider acceptance (wire-id-only identity): the
        # SAME wire id across provider classes resumes — provider is not part
        # of the effective-config contract (pinned at composition in
        # test_model_id_fingerprint_deterministic_at_composition_layer)
        done, _ = runner._load_checkpoint(
            str(cp),
            expected_fingerprint=runner._build_fingerprint(
                extractor_model=e, **kw))
        assert "q1" in done
        # retrieval-only (None↔None) resume path: accepted, not refused
        cp_none = tmp_path / "none.json"
        runner._save_checkpoint(
            str(cp_none), [_minimal_outcome("q2")], [],
            fingerprint=runner._build_fingerprint(extractor_model=None, **kw))
        done, _ = runner._load_checkpoint(
            str(cp_none),
            expected_fingerprint=runner._build_fingerprint(
                extractor_model=None, **kw))
        assert "q2" in done
        with pytest.raises(runner.CheckpointStaleError,
                           match=r"differs on \['extractor_model'\]"):
            runner._load_checkpoint(
                str(cp_none),
                expected_fingerprint=runner._build_fingerprint(
                    extractor_model=a, **kw))
    finally:
        for model in adapters:
            model.close()


def test_checkpoint_resume_refuses_cross_tuning(tmp_path):
    """M7 #1739, Gap 2 integration: a checkpoint written with
    ``--extractor-model deepseek-v4-pro-xhigh`` (reasoning-ON, max_tokens=
    500) REFUSES resume under ``deepseek-v4-pro-noreason`` (reasoning-OFF,
    max_tokens=8000) — CheckpointStaleError naming ``extractor_model`` —
    and a fresh same-tuning instance resumes. The wire id is IDENTICAL on
    both sides: only the tuning discriminator can refuse (the exact
    cross-tuning hazard the issue calls out — silently reusing
    mismatched-config results). The default-tuning ↔ tuned directions are
    also refused (every distinct tuning fingerprints differently)."""
    kw = dict(
        reader_model="r", judge_model="j", ks=(5,), top_k=5, split="s",
        ingest_mode="embedded", max_retries=1, dataset_fingerprint="x",
        rerank_config={},
    )
    cp = tmp_path / "state.json"
    adapters = []
    try:
        adapters.append(MODELS["deepseek-v4-pro-xhigh"]())
        adapters.append(MODELS["deepseek-v4-pro-xhigh"]())
        adapters.append(MODELS["deepseek-v4-pro-noreason"]())
        adapters.append(OpenRouterModel("deepseek/deepseek-v4-pro"))
        xhigh, xhigh_twin, noreason, default_pro = adapters
        runner._save_checkpoint(
            str(cp), [_minimal_outcome("q1")], [],
            fingerprint=runner._build_fingerprint(
                extractor_model=xhigh, **kw))
        # fresh same-tuning instance → accepted
        done, _ = runner._load_checkpoint(
            str(cp),
            expected_fingerprint=runner._build_fingerprint(
                extractor_model=xhigh_twin, **kw))
        assert "q1" in done
        # cross-tuning (same wire id!) → refused, naming extractor_model
        with pytest.raises(runner.CheckpointStaleError,
                           match=r"differs on \['extractor_model'\]"):
            runner._load_checkpoint(
                str(cp),
                expected_fingerprint=runner._build_fingerprint(
                    extractor_model=noreason, **kw))
        # default tuning → also refused against the -xhigh checkpoint
        with pytest.raises(runner.CheckpointStaleError,
                           match=r"differs on \['extractor_model'\]"):
            runner._load_checkpoint(
                str(cp),
                expected_fingerprint=runner._build_fingerprint(
                    extractor_model=default_pro, **kw))
    finally:
        for m in adapters:
            m.close()


def test_checkpoint_wrapper_path_resume_same_config(tmp_path, monkeypatch):
    """M7 #1739, Gap 1 integration: the DEFAULT wrapper path
    (``build_extractor_model()``) must be checkpoint-resumable — a fresh
    wrapper instance with the same effective config resumes (the #1549
    "run it in slices" protocol); a different extractor wire id is refused
    naming ``extractor_model``. Exercised at ALL THREE provider shapes the
    default path can produce (1/2-provider RoutingModel, 3-provider
    RotatingModel — the #1549 pilot default) PLUS the wrapper cross-tuning
    direction: two wrappers differing ONLY in max_tokens must refuse each
    other's checkpoints (the member tuning suffix discriminates on the
    wrapper path, mirroring test_checkpoint_resume_refuses_cross_tuning).
    Pre-fix every fresh instance embedded a distinct 0x... repr → all
    resumes refused even with identical git_sha."""
    kw = dict(
        reader_model="r", judge_model="j", ks=(5,), top_k=5, split="s",
        ingest_mode="embedded", max_retries=1, dataset_fingerprint="x",
        rerank_config={},
    )
    for keys in ((), ("deepseek", "openrouter"),
                 ("deepseek", "openrouter", "venice")):
        _pin_extractor_env(monkeypatch, keys=keys)
        cp = tmp_path / f"state-{len(keys)}.json"
        adapters = []
        try:
            adapters.append(build_extractor_model(
                max_tokens=None, temperature=0.0))
            adapters.append(build_extractor_model(
                max_tokens=None, temperature=0.0))  # fresh, same config
            adapters.append(build_extractor_model(
                model_id="deepseek/deepseek-v4-pro",
                max_tokens=None, temperature=0.0))  # diff wire id
            a, b, c = adapters
            runner._save_checkpoint(
                str(cp), [_minimal_outcome("q1")], [],
                fingerprint=runner._build_fingerprint(
                    extractor_model=a, **kw))
            # fresh wrapper, same effective config → accepted
            done, _ = runner._load_checkpoint(
                str(cp),
                expected_fingerprint=runner._build_fingerprint(
                    extractor_model=b, **kw))
            assert "q1" in done, f"keys={keys}"
            # different wire id → refused, naming extractor_model
            with pytest.raises(runner.CheckpointStaleError,
                               match=r"differs on \['extractor_model'\]"):
                runner._load_checkpoint(
                    str(cp),
                    expected_fingerprint=runner._build_fingerprint(
                        extractor_model=c, **kw))
        finally:
            for m in adapters:
                close = getattr(m, "close", None)
                if close is not None:
                    close()
                else:  # RoutingModel has no close — close the inner adapters
                    for inner in (getattr(m, "primary", None),
                                  getattr(m, "fallback", None)):
                        if inner is not None:
                            inner.close()
    # wrapper cross-tuning: same wire id + provider set, different max_tokens
    # → refused (only the member tuning suffix discriminates)
    _pin_extractor_env(monkeypatch, keys=())
    cp = tmp_path / "state-tuning.json"
    adapters = []
    try:
        adapters.append(build_extractor_model(
            max_tokens=500, temperature=0.0))
        adapters.append(build_extractor_model(
            max_tokens=500, temperature=0.0))  # fresh, same tuning
        adapters.append(build_extractor_model(
            max_tokens=8000, temperature=0.0))  # diff tuning
        a, b, c = adapters
        runner._save_checkpoint(
            str(cp), [_minimal_outcome("q1")], [],
            fingerprint=runner._build_fingerprint(extractor_model=a, **kw))
        done, _ = runner._load_checkpoint(
            str(cp),
            expected_fingerprint=runner._build_fingerprint(
                extractor_model=b, **kw))
        assert "q1" in done
        with pytest.raises(runner.CheckpointStaleError,
                           match=r"differs on \['extractor_model'\]"):
            runner._load_checkpoint(
                str(cp),
                expected_fingerprint=runner._build_fingerprint(
                    extractor_model=c, **kw))
    finally:
        for m in adapters:
            close = getattr(m, "close", None)
            if close is not None:
                close()
            else:  # RoutingModel has no close — close the inner adapters
                for inner in (getattr(m, "primary", None),
                              getattr(m, "fallback", None)):
                    if inner is not None:
                        inner.close()


def test_run_evaluation_resume_accepts_fresh_same_model_extractor(tmp_path):
    """M4 #1732, runner seam: ``run_evaluation`` wires
    ``_build_fingerprint(extractor_model=...)`` → ``_load_checkpoint(
    checkpoint, fingerprint, run_key=...)`` → ``_save_checkpoint(...,
    fingerprint)`` (run.py 824-834/1081). A second invocation with a FRESH
    instance of the same extractor model must resume — the checkpointed qid
    is skipped in ``_run_one`` (reader NOT called again) and no
    CheckpointStaleError raised; a different model must be refused at the
    gate. ``extractor_model`` feeds only the fingerprint inside
    ``run_evaluation`` — no API calls — so the real adapter is offline-safe."""
    cp = tmp_path / "state.json"
    reader_calls = {"n": 0}

    class _CountingReader(MockReader):
        def answer(self, *args, **kwargs):
            reader_calls["n"] += 1
            return super().answer(*args, **kwargs)

    reader = _CountingReader()
    adapters: list = []
    try:
        adapters.append(OpenRouterModel("deepseek/deepseek-v4-flash"))
        adapters.append(OpenRouterModel("deepseek/deepseek-v4-flash"))  # fresh, same model
        adapters.append(DeepSeekDirectModel("deepseek-chat"))  # different model
        a, b, c = adapters

        out1, _ = runner.run_evaluation(
            _mini()[:1], reader=reader, judge=MockJudge(),
            ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
            checkpoint=str(cp), extractor_model=a)
        assert len(out1) == 1
        assert reader_calls["n"] == 1  # run 1 processed q1

        # fresh instance of the same model → resume accepted: q1 is reused
        # from the checkpoint, so the reader is NOT invoked again (a silent
        # re-run would push the count to 2 and fail this)
        out2, _ = runner.run_evaluation(
            _mini()[:1], reader=reader, judge=MockJudge(),
            ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
            checkpoint=str(cp), extractor_model=b)
        assert [o["question_id"] for o in out2] == \
            [o["question_id"] for o in out1]
        assert reader_calls["n"] == 1  # no re-run — q1 came from the checkpoint

        # different model → refused at the gate, naming the field
        with pytest.raises(runner.CheckpointStaleError,
                           match=r"differs on \['extractor_model'\]"):
            runner.run_evaluation(
                _mini()[:1], reader=reader, judge=MockJudge(),
                ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
                checkpoint=str(cp), extractor_model=c)
    finally:
        for model in adapters:
            model.close()


# ── retrieval-only report shape ─────────────────────────────────────────────

def test_retrieval_only_report_shape(tmp_path, fake_embeddings):
    outcomes, report = runner.run_evaluation(
        _mini(), reader=None, judge=None, ks=(5, 10), top_k=10, split="s",
        work_dir=str(tmp_path), retriever="vector", retrieval_only=True,
    )
    assert len(outcomes) == 5
    assert report["n_questions"] == 5
    # no bogus accuracy from unset labels
    assert report["accuracy"] is None
    assert report["methodology"]["retrieval_only"] is True
    assert report["methodology"]["retriever"] == "vector"
    # retrieval output is still present
    assert report["retrieval"]["turn_recall@k"]["10"] >= 0.0
    # outcomes carry no labels/hypotheses
    assert all(o["label"] is None and o["hypothesis"] is None for o in outcomes)


def test_retrieval_only_never_calls_reader_or_judge(tmp_path):
    class _Boom:
        model_id = "boom"

        def answer(self, **kw):
            raise AssertionError("reader must not run in retrieval-only mode")

    outcomes, _report = runner.run_evaluation(
        _mini()[:1], reader=_Boom(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
        retrieval_only=True,
    )
    assert len(outcomes) == 1
    assert outcomes[0]["label"] is None


# ── dataset digest-pin ──────────────────────────────────────────────────────

def test_dataset_digest_tampered_cache_redownloads(monkeypatch, tmp_path):
    import urllib.request

    good_payload = json.dumps([{"question_id": "mini_x"}]).encode("utf-8")
    pin = ds._sha256_bytes(good_payload)
    monkeypatch.setitem(ds.SPLIT_DIGESTS, "s", pin)  # fixture-scoped pin

    cache = tmp_path / "cache"
    cache.mkdir()
    tampered = cache / ds.SPLIT_FILES["s"]
    tampered.write_bytes(good_payload[:-10])  # truncated cache
    monkeypatch.setenv("TORTOISE_LME_CACHE_DIR", str(cache))

    class _FakeResp:
        def __init__(self):
            self._payload = good_payload

        def read(self, n):
            data, self._payload = self._payload[:n], self._payload[n:]
            return data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=120: _FakeResp())

    instances = ds.load_dataset("s", limit=1)  # tampered → re-download
    assert instances == [{"question_id": "mini_x"}]
    # the tampered cache was replaced by the verified re-download
    assert ds._sha256_bytes(tampered.read_bytes()) == pin


def test_dataset_digest_mismatch_errors_when_download_disabled(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    bad = cache / ds.SPLIT_FILES["s"]
    bad.write_text('[{"question_id": "tampered"}]', encoding="utf-8")  # valid JSON, wrong digest
    monkeypatch.setenv("TORTOISE_LME_CACHE_DIR", str(cache))

    with pytest.raises(ds.DatasetDigestError):
        ds.load_dataset("s", limit=1, download=False)


def test_dataset_pins_are_recorded():
    # the pins must exist for every split (the S pin was verified against the
    # local authentic 277MB file AND the official HF LFS etag)
    assert set(ds.SPLIT_DIGESTS) == set(ds.SPLIT_FILES)
    assert len(ds.SPLIT_DIGESTS["s"]) == 64
    assert len(ds.SPLIT_DIGESTS["m"]) == 64
    assert len(ds.SPLIT_DIGESTS["oracle"]) == 64


# ── --db mode: per-(question, model) graph isolation (probe-level) ──────────

def test_question_graph_namespace_isolation():
    """A winner-vs-control spot-check's second run must NEVER find the first
    model's ids — the HNSW index is global per graph, so the graph itself
    must be distinct per (question, model-run)."""
    ns = runner.question_graph_namespace
    # distinct per model
    assert ns("arctic-s", "query", "mini_ie_user_001") != \
        ns("minilm", "query", "mini_ie_user_001")
    # distinct per question
    assert ns("arctic-s", "query", "mini_ie_user_001") != \
        ns("arctic-s", "query", "mini_msr_002")
    # distinct per prompt
    assert ns("arctic-s", "query", "mini_ie_user_001") != \
        ns("arctic-s", None, "mini_ie_user_001")
    # deterministic
    assert ns("arctic-s", "query", "mini_ie_user_001") == \
        ns("arctic-s", "query", "mini_ie_user_001")
    # valid as an SDK namespace (alnum/underscore/hyphen, ≤64 chars)
    import re as _re
    for v in (ns("arctic-s", "query", "mini_ie_user_001"),
              ns("minilm", None, "mini_abs_005_abs")):
        assert _re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$", v), v


def test_db_mode_sdk_gets_per_question_namespace(monkeypatch, tmp_path):
    """Probe-level: with --db, the per-question SDK is constructed against a
    distinct namespace (mirrors TortoiseSDK(namespace=...) on the URI)."""
    seen: list[str] = []

    class _FakeSDK:
        def __init__(self, db_path=None, *, namespace=None):
            seen.append(namespace)
            self._namespace = namespace

        def close(self):
            pass

    monkeypatch.setattr(runner, "TortoiseSDK", _FakeSDK)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://localhost:6379/bench")

    _sdk1, cleanup1 = runner._make_question_sdk(
        db_uri="docker://localhost:6379/bench",
        namespace=runner.question_graph_namespace("arctic-s", "query", "q1"),
        work_dir=None)
    _sdk2, cleanup2 = runner._make_question_sdk(
        db_uri="docker://localhost:6379/bench",
        namespace=runner.question_graph_namespace("minilm", "query", "q1"),
        work_dir=None)
    cleanup1()
    cleanup2()
    assert seen == [
        runner.question_graph_namespace("arctic-s", "query", "q1"),
        runner.question_graph_namespace("minilm", "query", "q1"),
    ]
    assert seen[0] != seen[1]


def test_db_mode_requires_uri(tmp_path):
    with pytest.raises(SystemExit):
        runner.run_main(["--db", "/tmp/not-a-uri.db", "--mock",
                         "--data", str(MINI), "--limit", "1",
                         "--output", str(tmp_path / "o.json")])


# ── --spot-check: named paired artifact ─────────────────────────────────────

def test_spot_check_emits_paired_artifact(tmp_path, monkeypatch, fake_embeddings):
    """The HNSW spot-check is a named reproducible producer: one pass runs
    winner AND control, emitting ONE paired artifact at the pinned path with
    {cleared, n, metric_deltas} — consumed by gate_1349.py."""
    injected: list[str] = []
    monkeypatch.setattr(runner, "inject_model",
                        lambda name, query_prompt=None, load_timeout=None: (
                            injected.append(name),
                            {"name": name, "hf_id": f"hf/{name}",
                             "resolved_revision": None, "query_prompt": query_prompt,
                             "dim": 384})[1])

    class _FakeG:
        def query(self, cypher, params=None):
            if "embedding IS NOT NULL" in cypher and "count(n)" in cypher:
                return _R([[50]])
            return _R([])

    class _R:
        def __init__(self, rows):
            self.result_set = rows

    class _FakeProj:
        _is_embedded = False
        _vector_index_api = None

        def __init__(self, g):
            self.g = g

    class _FakeSDK:
        def __init__(self, db_path=None, *, namespace=None):
            self._namespace = namespace
            self._proj = _FakeProj(_FakeG())

        def _get_proj(self):
            return self._proj

        def create_point(self, *a, **k):
            return {"id": k.get("id", "x")}

        def create_event(self, *a, **k):
            return {}

        def close(self):
            pass

    monkeypatch.setattr(runner, "TortoiseSDK", _FakeSDK)
    monkeypatch.setattr(runner, "SPOTCHECK_ARTIFACT_DIR", tmp_path)

    out = tmp_path / "report.json"
    _artifact = runner.run_main([
        "--db", "docker://localhost:6379/bench", "--spot-check",
        "--model", "arctic-s", "--retriever", "vector",
        "--data", str(MINI), "--limit", "2", "--split", "s", "--mock",
        "--work-dir", str(tmp_path), "--cache-dir", str(tmp_path / "cache"),
        "--output", str(out),
    ])
    # winner AND control both ran (one pass)
    assert injected == ["arctic-s", "minilm"]

    path = runner.spotcheck_artifact_path("arctic-s")
    assert path.parent == tmp_path
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["winner"] == "arctic-s"
    assert data["control"] == "minilm"
    assert data["surface"] == "hnsw"
    assert data["n"] == 2
    assert set(data["metric_deltas"]) == {"turn_recall@10", "ndcg@10"}
    assert isinstance(data["cleared"], bool)
    for m in data["metric_deltas"].values():
        assert "mean_delta" in m and "n" in m
    # full-set contract: the mock CONTROL arm trips the vector breaker
    # (every outcome breaker_open) — the artifact must still cover the FULL
    # question set: full-length deltas with None sentinels + dropped_qids
    # (never a silent shrink to an empty paired subset), n = full count.
    assert data["dropped_qids"] == ["mini_ie_user_001", "mini_msr_002"]
    assert all(len(m["deltas"]) == data["n"]
               for m in data["metric_deltas"].values())
    assert all(all(d is None for d in m["deltas"])
               for m in data["metric_deltas"].values())
    assert all(m["n"] == 0 for m in data["metric_deltas"].values())


def _spotcheck_outcome(qid, *, breaker_open=False, tr10=0.5, ndcg=0.4) -> dict:
    return {
        "question_id": qid, "breaker_open": breaker_open,
        "turn_recall@k": {"10": tr10}, "ndcg@10": ndcg,
    }


def test_spotcheck_artifact_dropped_question_sentinel():
    """Contract fix (#1349 gate): the spot-check artifact covers the FULL
    question set. A question breaker_open in one arm is DROPPED from the
    paired computation but keeps its slot as a None sentinel in the
    full-length per-metric deltas and is listed in dropped_qids — so the
    gate can distinguish "present, delta computed" from "dropped", and
    n = the full count (never the shrinking intersection)."""
    results = {
        "arctic-s": {
            "q1": _spotcheck_outcome("q1", tr10=0.9, ndcg=0.8),
            "q2": _spotcheck_outcome("q2", tr10=0.7, ndcg=0.6),
            "q3": _spotcheck_outcome("q3", tr10=0.5, ndcg=0.4),
        },
        "minilm": {
            "q1": _spotcheck_outcome("q1", tr10=0.8, ndcg=0.7),
            # q2 is breaker_open in the CONTROL arm → dropped
            "q2": _spotcheck_outcome("q2", breaker_open=True),
            "q3": _spotcheck_outcome("q3", tr10=0.1, ndcg=0.0),
        },
    }
    art = runner._build_spotcheck_artifact("arctic-s", "minilm", results,
                                           ks=(10,))
    assert art["n"] == 3                          # FULL count, not paired subset
    assert art["dropped_qids"] == ["q2"]
    for m in ("turn_recall@10", "ndcg@10"):
        entry = art["metric_deltas"][m]
        # None sentinel at q2, paired deltas at q1/q3 (float-exact)
        assert entry["deltas"][0] == pytest.approx(0.1)
        assert entry["deltas"][1] is None
        assert entry["deltas"][2] == pytest.approx(0.4)
        assert entry["n"] == 2                     # non-dropped only
        # p/mean over the 2 valid deltas — identical to a direct summary
        ref = runner._delta_summary([0.1, 0.4])
        assert entry["mean_delta"] == ref["mean_delta"]
        assert entry["one_sided_p"] == ref["one_sided_p"]


def test_spotcheck_artifact_absent_question_dropped():
    """A question ABSENT from one arm entirely is also dropped (not a
    silent intersection shrink): full set = the UNION of both arms, and the
    missing qid keeps its None sentinel + dropped_qids entry."""
    results = {
        "arctic-s": {
            "q1": _spotcheck_outcome("q1", tr10=0.9, ndcg=0.8),
            "q3": _spotcheck_outcome("q3", tr10=0.5, ndcg=0.4),
        },
        "minilm": {
            "q1": _spotcheck_outcome("q1", tr10=0.8, ndcg=0.7),
            # q3 absent from the control arm entirely
        },
    }
    art = runner._build_spotcheck_artifact("arctic-s", "minilm", results,
                                           ks=(10,))
    assert art["n"] == 2                          # union of both arms
    assert art["dropped_qids"] == ["q3"]
    for m in ("turn_recall@10", "ndcg@10"):
        entry = art["metric_deltas"][m]
        assert entry["deltas"][0] == pytest.approx(0.1)  # q1 paired delta
        assert entry["deltas"][1] is None                # q3 dropped
        assert entry["n"] == 1


def test_spot_check_requires_db_and_model(tmp_path):
    with pytest.raises(SystemExit):
        runner.run_main(["--spot-check", "--model", "arctic-s", "--mock",
                         "--data", str(MINI), "--limit", "1",
                         "--output", str(tmp_path / "o.json")])
    with pytest.raises(SystemExit):
        runner.run_main(["--db", "docker://x/y", "--spot-check", "--mock",
                         "--data", str(MINI), "--limit", "1",
                         "--output", str(tmp_path / "o.json")])


def test_spot_check_rejects_control_model_as_winner(tmp_path, capsys):
    """--spot-check --model minilm is a no-op producer: winner==control makes
    every metric delta 0. Rejected at the parser gate — never run."""
    with pytest.raises(SystemExit) as ei:
        runner.run_main(["--db", "docker://x/y", "--spot-check",
                         "--model", "minilm", "--retriever", "vector",
                         "--mock", "--data", str(MINI), "--limit", "1",
                         "--output", str(tmp_path / "o.json")])
    # argparse error() exits with status 2 (code may be the (2, msg) tuple)
    code = ei.value.code
    assert code == 2 or (isinstance(code, tuple) and code[0] == 2)
    assert "non-control winner" in capsys.readouterr().err


def test_db_uri_env_restored_after_cleanup(tmp_path, monkeypatch):
    """--db env-scoping (issue #1349 test isolation): an explicit --db must
    WIN over a stale pre-existing TORTOISE_DB_URI (a stale shell URI would
    otherwise redirect the spot-check to the wrong server), and the env
    must be restored on exit so later no-path SDK constructions in the same
    process never inherit the URI (the runner runs repeatedly in-process)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    assert os.environ.get("TORTOISE_DB_URI") is None

    # No pre-existing env: --db sets it, cleanup unsets it
    with runner._temporary_env_var("TORTOISE_DB_URI", "docker://localhost:6379/bench"):
        assert os.environ.get("TORTOISE_DB_URI") == "docker://localhost:6379/bench"
    assert os.environ.get("TORTOISE_DB_URI") is None

    # Stale pre-existing env: explicit --db WINS while scoped, then restores
    monkeypatch.setenv("TORTOISE_DB_URI", "redis://elsewhere:6379")
    with runner._temporary_env_var("TORTOISE_DB_URI", "docker://localhost:6379/bench"):
        assert os.environ.get("TORTOISE_DB_URI") == "docker://localhost:6379/bench"
    assert os.environ.get("TORTOISE_DB_URI") == "redis://elsewhere:6379"

    # Nested/no-op case: restore is idempotent
    with runner._temporary_env_var("TORTOISE_DB_URI", "docker://localhost:6379/bench"):
        with runner._temporary_env_var("TORTOISE_DB_URI", "redis://a:6379"):
            assert os.environ.get("TORTOISE_DB_URI") == "redis://a:6379"
        assert os.environ.get("TORTOISE_DB_URI") == "docker://localhost:6379/bench"
    assert os.environ.get("TORTOISE_DB_URI") == "redis://elsewhere:6379"
