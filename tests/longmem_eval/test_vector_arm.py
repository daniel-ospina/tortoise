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
import re
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import tortoise.embeddings as emb  # noqa: E402
import tortoise.search_engine as se  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402

from tools.longmem_eval import dataset as ds  # noqa: E402
from tools.longmem_eval import encode_cache  # noqa: E402
from tools.longmem_eval import retrieve  # noqa: E402
from tools.longmem_eval import run as runner  # noqa: E402
from tools.longmem_eval.ingest import ingest_haystack  # noqa: E402
from tools.longmem_eval.judge import MockJudge  # noqa: E402
from tools.longmem_eval.reader import MockReader  # noqa: E402
from tools.longmem_eval.retrieve import retrieve_for_question  # noqa: E402

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
        assert calls[0]["is_embedded"] is True
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

        def _hybrid(sdk_, query, limit):
            calls["hybrid"] += 1
            return []

        def _vector(sdk_, query, limit):
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

        def _hybrid(sdk_, query, limit):
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
    report = build_report(
        outcomes, dataset_id="x", split="s", reader_model="r", judge_model="j",
        extraction_approach="x", ks=(10,), top_k=10,
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
    real = emb.compute_embedding
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
    outcomes, report = runner.run_evaluation(
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

    outcomes, report = runner.run_evaluation(
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


# ── retrieval-only report shape ─────────────────────────────────────────────

def test_retrieval_only_report_shape(tmp_path):
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

    outcomes, report = runner.run_evaluation(
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

    sdk1, cleanup1 = runner._make_question_sdk(
        db_uri="docker://localhost:6379/bench",
        namespace=runner.question_graph_namespace("arctic-s", "query", "q1"),
        work_dir=None)
    sdk2, cleanup2 = runner._make_question_sdk(
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
                        lambda name, query_prompt=None: (
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
    artifact = runner.run_main([
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


def test_spot_check_requires_db_and_model(tmp_path):
    with pytest.raises(SystemExit):
        runner.run_main(["--spot-check", "--model", "arctic-s", "--mock",
                         "--data", str(MINI), "--limit", "1",
                         "--output", str(tmp_path / "o.json")])
    with pytest.raises(SystemExit):
        runner.run_main(["--db", "docker://x/y", "--spot-check", "--mock",
                         "--data", str(MINI), "--limit", "1",
                         "--output", str(tmp_path / "o.json")])
