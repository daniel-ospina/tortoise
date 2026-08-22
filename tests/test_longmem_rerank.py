"""R6 (#1545, epic #1509) — cross-encoder rerank + MMR diversity tests.

Unit (pure MMR math, env-gate parsing, scorer degradation/TTL, fake-scorer
ordering, similarity fallbacks) + integration (retrieve_for_question rerank
on/off/pool-only/degraded/score-failure/empty-pool/all-one-session, leg-mix
partition, heterogeneous-run aggregation, checkpoint resume three-valued) +
CLI smoke (flags thread-through, fail-fast, boundary values, off-report
zero-keys). The REAL cross-encoder is never loaded in CI — the fake scorer
is injected, and the real-model test self-skips unless
TORTOISE_LME_RUN_REAL_MODEL=1 (follow-up env only). Runs fully offline.
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval import rerank  # noqa: E402, RUF100
from tools.longmem_eval.ingest import ingest_haystack  # noqa: E402, RUF100
from tools.longmem_eval.retrieve import (  # noqa: E402, RUF100
    retrieve_for_question,
)
from tools.longmem_eval.run import (  # noqa: E402, RUF100
    CheckpointStaleError,
    _build_fingerprint,  # noqa: E402, RUF100
    _resolve_rerank,
    run_evaluation,
    run_main,
)

MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"


def _mini() -> list[dict]:
    return json.loads(MINI.read_text(encoding="utf-8"))


def _fresh_sdk(tmp_path):
    from tortoise.sdk import TortoiseSDK
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    return TortoiseSDK(str(tmp_path / "lme.db"))


@pytest.fixture(autouse=True)
def _reset_rerank_state():
    """Clear the scorer caches before/after EVERY test — a TTL test or a
    failed real-model import must not leak a cached failure into the next
    test (the module globals are the real get_scorer state)."""
    rerank._scorer_cache.clear()
    rerank._fail_cache.clear()
    from tortoise.embeddings import EmbeddingModel
    EmbeddingModel._reset()
    yield
    rerank._scorer_cache.clear()
    rerank._fail_cache.clear()
    EmbeddingModel._reset()


def _inject_fake(monkeypatch, scorer=None):
    """Inject the deterministic FakeScorer (or a custom scorer) through the
    get_scorer seam — the real ~90MB model NEVER enters CI (Gate 7)."""
    fake = scorer if scorer is not None else rerank.FakeScorer()
    monkeypatch.setattr(rerank, "get_scorer", lambda model=None: (fake, ""))


def _trusted_audit() -> dict:
    from tools.longmem_eval.dataset_audit import audit_dataset
    return audit_dataset([{
        "question_id": "q-audit",
        "haystack_session_ids": ["s0"],
        "answer_session_ids": ["s0"],
        "haystack_sessions": [[
            {"role": "user", "content": "x", "has_answer": True}]],
    }])


# ── Task 1: MMR core + scorer (unit) ───────────────────────────────────────

def test_mmr_select_promotes_relevant_over_near_duplicate():
    # 3 candidates: d0 exact evidence, d1 near-duplicate (high sim to d0),
    # d2 unrelated
    scores = {0: 0.95, 1: 0.90, 2: 0.10}
    sims = {(0, 1): 0.98, (0, 2): 0.2, (1, 2): 0.2}
    sessions = {0: "s1", 1: "s1", 2: "s2"}
    selected, dropped = rerank.mmr_select(
        scores, sims, sessions, top_k=2, per_session_cap=1, lambda_=0.7)
    assert selected == [0, 2] and 1 in dropped  # near-duplicate suppressed


def test_mmr_select_enforces_per_session_cap():
    scores = {i: 1.0 - i * 0.01 for i in range(6)}  # 3 chunks × 2 sessions
    sessions = {i: f"s{i // 3}" for i in range(6)}
    selected, _ = rerank.mmr_select(
        scores, {}, sessions, top_k=6, per_session_cap=2, lambda_=0.7)
    counts = Counter(sessions[i] for i in selected)
    assert max(counts.values()) <= 2 and len(selected) == 4  # pool dries


def test_mmr_select_validation():
    with pytest.raises(ValueError):
        rerank.mmr_select({}, {}, {}, top_k=5, per_session_cap=0, lambda_=0.7)
    with pytest.raises(ValueError):
        rerank.mmr_select({}, {}, {}, top_k=5, per_session_cap=2, lambda_=1.5)


def test_mmr_select_missing_session_key_defined():
    # contract: a candidate absent from `sessions` is its own group (never
    # KeyError, never silently capped with ""-group hits)
    scores = {0: 0.9, 1: 0.8, 2: 0.7}
    sessions = {0: "s1"}                     # 1 and 2 have NO session
    selected, _ = rerank.mmr_select(scores, {}, sessions, top_k=3,
                                    per_session_cap=1, lambda_=0.7)
    assert len(selected) == 3                # un-capped: all three survive


def test_mmr_select_boundary_values_accepted():
    # cap=1, λ=0.0, λ=1.0 all accepted (boundary values — D7)
    scores = {0: 0.9, 1: 0.8}
    sessions = {0: "s1", 1: "s2"}
    for cap, lam in ((1, 0.7), (2, 0.0), (2, 1.0)):
        sel, _ = rerank.mmr_select(scores, {}, sessions, top_k=2,
                                   per_session_cap=cap, lambda_=lam)
        assert len(sel) == 2


def test_rerank_enabled_gate(monkeypatch):
    monkeypatch.delenv("TORTOISE_LME_RERANK", raising=False)  # hermetic
    assert rerank.rerank_enabled(True) and not rerank.rerank_enabled(False)
    assert not rerank.rerank_enabled(None)          # env unset → off (fail-safe)
    with monkeypatch.context() as m:
        m.setenv("TORTOISE_LME_RERANK", "1")
        assert rerank.rerank_enabled(None)          # env=1 flips the DEFAULT on
    monkeypatch.setenv("TORTOISE_LME_RERANK", "TRUE")
    assert rerank.rerank_enabled(None)
    monkeypatch.setenv("TORTOISE_LME_RERANK", "yes")
    assert rerank.rerank_enabled(None)
    monkeypatch.setenv("TORTOISE_LME_RERANK", "2")  # non-truthy → off
    assert not rerank.rerank_enabled(None)


def test_cross_encoder_scorer_degrades_with_ttl(monkeypatch):
    # load failures are cached with a short TTL (no hub hammering on a
    # persistent outage), then retried after expiry; successes are cached
    # permanently (D8b)
    calls = {"n": 0}

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient network error")
        return object()

    monkeypatch.setattr(rerank, "CrossEncoderScorer", _flaky)
    clock = {"t": 0.0}
    _real = rerank._NOW                       # capture before patching
    monkeypatch.setattr(rerank, "_NOW", lambda: clock["t"])
    sc1, reason1 = rerank.get_scorer("whatever-model")
    assert sc1 is None and reason1
    # inside the TTL window: NOT re-attempted (constructor count stays 1)
    sc2, _ = rerank.get_scorer("whatever-model")
    assert sc2 is None and calls["n"] == 1
    # after TTL expiry (clock advanced): re-attempted and cached
    clock["t"] = _real() + 120.0
    sc3, reason3 = rerank.get_scorer("whatever-model")
    assert sc3 is not None and not reason3 and calls["n"] == 2


def test_fake_scorer_orders_exact_over_near_duplicate():
    sc = rerank.FakeScorer()
    scores = sc.score("when is the gym session", [
        "gym at 5pm every weekday",                    # 1/5 = 0.20
        "gym at 5pm every weekday (paraphrase chunk)",  # 1/7 ≈ 0.14
        "user runs on weekends",                       # 0/4 = 0.00
    ])
    assert scores[0] > scores[1] > scores[2]


def test_pair_sim_scale_consistent_and_fallback():
    nan = float("nan")
    # NaN / wrong-dim / missing embeddings → Jaccard fallback, never a crash
    assert rerank._pair_sim("a b c", "b c d", [0.1, nan, 0.3], [0.1, 0.2, 0.3]) == 0.5
    assert rerank._pair_sim("a b c", "b c d", [0.1, 0.2, 0.3], [0.1, 0.2]) == 0.5
    assert rerank._pair_sim("a b c", "b c d", None, None) == 0.5
    assert rerank._pair_sim("", "", None, None) == 0.0     # zero/zero union
    assert rerank._pair_sim("x", "x", [1.0, 0.0], [1.0, 0.0]) == 1.0  # cos=1


def test_mixed_embedding_availability_scale_consistent():
    # half the pool embedded, half not → every sim finite and in [0,1] (D7)
    emb = {"a": [1.0, 0.0], "b": [0.0, 1.0]}
    texts = ["alpha beta", "beta gamma", "gamma delta", "delta epsilon"]
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            s = rerank._pair_sim(
                texts[i], texts[j],
                emb.get(f"t{i}"), emb.get(f"t{j}"))
            assert math.isfinite(s) and 0.0 <= s <= 1.0


def test_env_int_clamp_garbage_and_negative(monkeypatch):
    # retrieve-layer clamp: garbage/negative → default, never a crash (the
    # run-level raw-env fail-fast is a separate layer — test_env_parse...)
    monkeypatch.setenv("TORTOISE_LME_RERANK_POOL", "abc")
    assert rerank._env_int("TORTOISE_LME_RERANK_POOL", 40) == 40
    monkeypatch.setenv("TORTOISE_LME_RERANK_POOL", "-5")
    assert rerank._env_int("TORTOISE_LME_RERANK_POOL", 40) == 40
    monkeypatch.setenv("TORTOISE_LME_RERANK_POOL", "0")
    assert rerank._env_int("TORTOISE_LME_RERANK_POOL", 40) == 40
    monkeypatch.setenv("TORTOISE_LME_RERANK_CAP", "7")
    assert rerank._env_int("TORTOISE_LME_RERANK_CAP", 2) == 7
    monkeypatch.setenv("TORTOISE_LME_RERANK_LAMBDA", "1.5")
    assert rerank._env_float("TORTOISE_LME_RERANK_LAMBDA", 0.7) == 0.7
    monkeypatch.setenv("TORTOISE_LME_RERANK_LAMBDA", "0.0")
    assert rerank._env_float("TORTOISE_LME_RERANK_LAMBDA", 0.7) == 0.0


# ── Task 2: retrieve_for_question integration ──────────────────────────────

def test_retrieve_rerank_off_is_byte_identical(tmp_path, monkeypatch):
    # env-hermetic: a leaked TORTOISE_LME_RERANK must not flip the baseline
    # arm on
    monkeypatch.delenv("TORTOISE_LME_RERANK", raising=False)
    sdk = _fresh_sdk(tmp_path)
    q = _mini()[0]
    try:
        ingest_haystack(sdk, q)
        base = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20)
        off = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    rerank=False)
        def _drop(d):
            return {k: v for k, v in d.items() if k != "retrieval_latency_ms"}
        assert _drop(off) == _drop(base)     # content byte-identity (wall-
                                             # clock latency excluded)
        assert "rerank_pass" not in base     # no R6 keys off-path (D2)
        assert "rerank_latency_ms" not in base
        assert "rerank" not in base["match_source_counts"]
    finally:
        sdk.close()


def test_retrieve_pool_only_arm(tmp_path):
    """--rerank off --rerank-pool 40 (OQ5): deeper pool, baseline ordering,
    no rerank keys; reader sees top_k (reader/context invariant)."""
    sdk = _fresh_sdk(tmp_path)
    q = _mini()[0]
    try:
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    rerank=False, rerank_pool=40)
        pool_len = len(ret["hits"])          # mini q0: 4 points — invariant
        assert ret["context_point_count"] == pool_len == min(4, 20)
        assert "rerank_pass" not in ret
        assert "rerank_latency_ms" not in ret
        assert "rerank" not in ret["match_source_counts"]
    finally:
        sdk.close()


def test_retrieve_rerank_orders_and_caps(tmp_path, monkeypatch):
    _inject_fake(monkeypatch)
    sdk = _fresh_sdk(tmp_path)
    q = _mini()[0]                            # 4-hit pool, 1 session (s1)
    try:
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    rerank=True, rerank_pool=40,
                                    per_session_cap=2, mmr_lambda=0.7)
        assert ret["rerank_pass"]["applied"] is True
        assert "rerank_latency_ms" in ret
        # full applied-path contract (selected-only return): the hits ARE
        # the reader's context — length <= top_k, tokens over exactly the hits
        assert len(ret["hits"]) <= 20
        assert ret["context_point_count"] == len(ret["hits"]) == \
            ret["rerank_pass"]["selected_count"]
        assert ret["rerank_pass"]["pool_size"] == \
            ret["rerank_pass"]["selected_count"] + ret["rerank_pass"]["dropped"] \
            == 4
        assert ret["context_tokens"] > 0
        cap = Counter(h["session_id"] for h in ret["hits"] if h["session_id"])
        assert max(cap.values(), default=0) <= 2   # E2E-10 cap
        for h in ret["hits"]:
            assert h["match_source"] in (
                "fts", "vector", "structural", "rrf", "tfidf")
        # recall-retention guard: evidence must SURVIVE the rerank
        # (precision stage != recall stage — the reranker must not drop the
        # answer)
        assert ret["session_recall@k"]["5"] >= 0.5
        assert any(h.get("has_answer") for h in ret["hits"][:5])
    finally:
        sdk.close()


def test_retrieve_rerank_score_failure_degrades(tmp_path, monkeypatch):
    class _Boom:
        def score(self, query, contents):
            raise RuntimeError("predict exploded mid-run")

    _inject_fake(monkeypatch, scorer=_Boom())
    sdk = _fresh_sdk(tmp_path)
    q = _mini()[0]
    try:
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    rerank=True)
        assert ret["rerank_pass"]["applied"] is False
        assert "exploded" in ret["rerank_pass"]["degrade_reason"]
        pool_len = len(ret["hits"])
        assert ret["context_point_count"] == pool_len == min(4, 20)
        assert ret["rerank_pass"]["dropped"] == 0
        assert "pool_recall@k" not in ret["rerank_pass"]
    finally:
        sdk.close()


def test_scorer_length_mismatch_degrades(tmp_path, monkeypatch):
    class _Short:
        def score(self, query, contents):
            return [0.5] * (len(contents) - 1)   # one short — D8c boundary

    _inject_fake(monkeypatch, scorer=_Short())
    sdk = _fresh_sdk(tmp_path)
    q = _mini()[0]
    try:
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    rerank=True)
        assert ret["rerank_pass"]["applied"] is False
        assert "length-mismatched" in ret["rerank_pass"]["degrade_reason"]
        assert ret["context_point_count"] == len(ret["hits"]) == 4
    finally:
        sdk.close()


def test_retrieve_rerank_empty_pool(tmp_path, monkeypatch):
    _inject_fake(monkeypatch)
    sdk = _fresh_sdk(tmp_path)                     # empty graph, no ingest
    try:
        ret = retrieve_for_question(sdk, _mini()[0], ks=(5,), top_k=20,
                                    rerank=True)
        assert ret["rerank_pass"]["applied"] is True
        assert ret["rerank_pass"]["pool_size"] == 0
        assert ret["context_point_count"] == 0
    finally:
        sdk.close()


def test_retrieve_rerank_single_hit_pool_unit():
    """A literal 1-hit pool: selected == 1, no crash (rerank_hits level —
    the retrieve-level honest-count contract is covered by the cap tests)."""
    hits = [{"id": "h0", "content": "the gym is at 5pm",
             "session_id": "s1", "match_source": "fts"}]
    selected, stats = rerank.rerank_hits(
        "when is the gym", hits, scorer=rerank.FakeScorer(), proj=None,
        top_k=20, per_session_cap=2, lambda_=0.7)
    assert stats["applied"] is True
    assert selected[0]["id"] == "h0" and stats["selected_count"] == 1
    assert stats["dropped"] == 0 and stats["max_session_chunks"] == 1


def test_retrieve_rerank_all_one_session(tmp_path, monkeypatch):
    """Single-session corpus: the cap can suppress the only evidence — the
    honest selected_count is reported, never an implied top_k (S27)."""
    _inject_fake(monkeypatch)
    sdk = _fresh_sdk(tmp_path)
    q = _mini()[2]                        # mini_tr_003: 1 session, 2-hit pool
    try:
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20, rerank=True,
                                    per_session_cap=2, mmr_lambda=0.7)
        assert ret["rerank_pass"]["applied"] is True
        assert ret["rerank_pass"]["selected_count"] == 2
        assert ret["context_point_count"] == len(ret["hits"]) == 2
        # cap=1 suppresses the second hit of the only session
        ret1 = retrieve_for_question(sdk, q, ks=(5,), top_k=20, rerank=True,
                                     per_session_cap=1, mmr_lambda=0.7)
        assert ret1["rerank_pass"]["selected_count"] == 1
        assert ret1["context_point_count"] == len(ret1["hits"]) == 1
    finally:
        sdk.close()


def test_none_content_is_scored_zero_no_degrade():
    """A hit with content=None truncates to "" and scores 0.0 — applied
    stays True; NO degrade from None-content alone (D7)."""
    hits = [{"id": "h0", "content": None, "session_id": "s1",
             "match_source": "fts"},
            {"id": "h1", "content": "the gym at 5pm", "session_id": "s2",
             "match_source": "fts"}]
    selected, stats = rerank.rerank_hits(
        "when is the gym", hits, scorer=rerank.FakeScorer(), proj=None,
        top_k=20, per_session_cap=2, lambda_=0.7)
    assert stats["applied"] is True
    assert stats["selected_count"] == 2     # the None-content hit survives
    assert selected[0]["id"] == "h1"        # ... but ranks below the match


def test_long_transcript_truncation_effective():
    """>2048-char transcript: the content actually passed to the scorer is
    <= RERANK_TRUNCATE_CHARS (truncation lives in rerank_hits, D7 — the
    FakeScorer does not truncate itself)."""
    captured = {}

    class _Recording:
        def score(self, query, contents):
            captured["contents"] = list(contents)
            return [0.5] * len(contents)

    long_text = ("the gym at 5pm " * 500)   # ~8500 chars
    hits = [{"id": "h0", "content": long_text, "session_id": "s1",
             "match_source": "fts"}]
    rerank.rerank_hits("when is the gym", hits, scorer=_Recording(),
                       proj=None, top_k=20, per_session_cap=2, lambda_=0.7)
    assert len(captured["contents"]) == 1
    assert len(captured["contents"][0]) <= rerank.RERANK_TRUNCATE_CHARS


def test_non_ascii_query_and_transcript():
    hits = [{"id": "h0", "content": "今天天气很好 ☀️ 去公园跑步",
             "session_id": "s1", "match_source": "fts"},
            {"id": "h1", "content": "明天会下雨 🌧️ 在家读书",
             "session_id": "s2", "match_source": "fts"}]
    selected, stats = rerank.rerank_hits(
        "今天 天气", hits, scorer=rerank.FakeScorer(), proj=None,
        top_k=20, per_session_cap=2, lambda_=0.7)
    assert stats["applied"] is True and len(selected) == 2
    for s in stats:
        assert not isinstance(s, float) or math.isfinite(s)


def test_embedding_roundtrip_and_corrupt_blob(tmp_path):
    """vecf32 round-trip is float32-approximate; a corrupt (non-list) blob
    → absent from the fetch → Jaccard fallback, never an exception (the
    sdk.py fragile-serialization note)."""
    import numpy as np
    sdk = _fresh_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        emb = [0.1, 0.2, 0.3, 0.4]
        proj.g.query("CREATE (n:Point {id:'p1'}) SET n.embedding = vecf32($e)",
                     params={"e": emb})
        rows = proj.g.query(
            "MATCH (n:Point {id:'p1'}) RETURN n.embedding").result_set
        got = rows[0][0]
        assert isinstance(got, list)
        assert np.allclose(np.asarray(got, dtype=np.float64),
                           np.asarray(emb, dtype=np.float64), atol=1e-6)
        # corrupt blob: a string embedding property is filtered by the fetch
        proj.g.query("CREATE (n:Point {id:'p3'}) "
                     "SET n.embedding = 'corrupt-blob'")
        fetched = rerank._fetch_embeddings(proj, ["p1", "p3"])
        assert "p1" in fetched and "p3" not in fetched
        # wrong-dimension pair → Jaccard fallback
        proj.g.query("CREATE (n:Point {id:'p4'}) SET n.embedding = vecf32($e)",
                     params={"e": [0.1, 0.2]})
        fetched4 = rerank._fetch_embeddings(proj, ["p1", "p4"])
        s = rerank._pair_sim("a b c", "a b c", fetched4["p1"], fetched4["p4"])
        assert s == 1.0                      # Jaccard of identical text
    finally:
        sdk.close()


def test_nan_embedding_falls_back():
    """A valid-length embedding containing NaN → per-pair Jaccard fallback,
    every sim finite in [0,1] (NaN must never silently zero the MMR
    selection)."""
    nan = float("nan")
    texts = ["alpha beta", "beta gamma"]
    for t in texts:
        for u in texts:
            s = rerank._pair_sim(t, u, [0.1, nan, 0.3], [0.1, 0.2, 0.3])
            assert math.isfinite(s) and 0.0 <= s <= 1.0


def test_retrieve_rerank_degraded_pool_depth(tmp_path, monkeypatch):
    """Synthetic >40-point corpus, max(ks)=40 > top_k=20: a scorer-None
    question yields exactly top_k hits (20) — the baseline-pool fallback +
    truncation observable; an applied question yields
    rerank_pass["pool_size"] == 40 with selected-only hits (D3)."""
    sdk = _fresh_sdk(tmp_path)
    q = {
        "question_id": "synth_q1",
        "question_type": "single-session-user",
        "question": "what is the widget status",
        "answer": "42",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["synth-s0"],
        "haystack_dates": ["2025-06-15"],
        "haystack_sessions": [[{"role": "user", "content": "x"}]],
        "answer_session_ids": ["synth-s0"],
    }
    try:
        for i in range(45):
            sdk.create_point(
                "point", f"the widget status is {i} and the serial is {i}",
                id=f"synth:q1:t{i}", pointKind="event",
                session_id="synth-s0", lme_session_index=0,
                lme_question_id="synth_q1", has_answer=False)
        # degraded: scorer None → baseline pool truncated to top_k (20)
        monkeypatch.setattr(rerank, "get_scorer",
                            lambda model=None: (None, "load failed: boom"))
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20, 40), top_k=20,
                                    rerank=True)
        assert len(ret["hits"]) == 20
        assert ret["context_point_count"] == 20
        assert ret["rerank_pass"]["applied"] is False
        # applied: deep pool observable through pool_size (never hits length)
        _inject_fake(monkeypatch)
        ret2 = retrieve_for_question(sdk, q, ks=(5, 10, 20, 40), top_k=20,
                                     rerank=True, rerank_pool=40,
                                     per_session_cap=2, mmr_lambda=0.7)
        assert ret2["rerank_pass"]["applied"] is True
        assert ret2["rerank_pass"]["pool_size"] == 40
        assert len(ret2["hits"]) == ret2["rerank_pass"]["selected_count"] <= 20
        assert ret2["rerank_pass"]["selected_count"] <= 2  # 1 session, cap 2
    finally:
        sdk.close()


def test_retrieve_rerank_leg_mix_partition(tmp_path, monkeypatch):
    """D6/Task 4 (gated on M7 — present): the leg-mix `rerank` bucket counts
    selection-loss only; legs + dropped == pool_size as a partition (never an
    overlay). Off-path emits no bucket and is identical to baseline."""
    _inject_fake(monkeypatch)
    sdk = _fresh_sdk(tmp_path)
    q = _mini()[0]
    try:
        ingest_haystack(sdk, q)
        base = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    rerank=True, rerank_pool=40,
                                    per_session_cap=2, mmr_lambda=0.7)
        lm = ret["match_source_counts"]
        assert lm["rerank"] == ret["rerank_pass"]["dropped"]
        assert sum(lm.values()) == ret["rerank_pass"]["pool_size"]
        # provenance legs are untouched (rerank is additive, never a rewrite)
        for leg in lm:
            if leg != "rerank":
                assert leg in ("fts", "vector", "structural", "rrf", "tfidf")
        # off-path: no bucket + identical leg-mix
        assert "rerank" not in base["match_source_counts"]
        assert base["match_source_counts"] == \
            retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                  rerank=False)["match_source_counts"]
    finally:
        sdk.close()


def test_retrieve_rerank_flags_stamped(tmp_path, monkeypatch):
    """reranked/mmr_promoted overlay flags land on selected hits (D6) — an
    overlay metric, never a leg bucket."""
    _inject_fake(monkeypatch)
    sdk = _fresh_sdk(tmp_path)
    q = _mini()[0]
    try:
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    rerank=True, rerank_pool=40,
                                    per_session_cap=2, mmr_lambda=0.7)
        flags = [h.get("reranked", False) for h in ret["hits"]]
        assert any(flags)                     # the mechanism moved something
        moved = ret["rerank_pass"]["moved"]
        assert moved == sum(1 for f in flags if f)
        assert all(h["match_source"] for h in ret["hits"])  # provenance kept
    finally:
        sdk.close()


# ── Task 3: CLI thread-through + fail-fast ─────────────────────────────────

def _run_cli(tmp_path, *extra, monkeypatch=None):
    if monkeypatch is not None:
        _inject_fake(monkeypatch)
    out = tmp_path / "report.json"
    report = run_main(["--data", str(MINI), "--limit", "5", "--split", "s",
                       "--mock", "--output", str(out), *extra])
    return report, out


def test_cli_rerank_smoke(tmp_path, monkeypatch):
    _inject_fake(monkeypatch)
    out = tmp_path / "report.json"
    report = run_main(["--data", str(MINI), "--limit", "5", "--split", "s",
                       "--mock", "--rerank", "--output", str(out)])
    rr = report["rerank"]
    assert rr["enabled"] is True
    assert rr["model"] == rerank.RERANK_MODEL_DEFAULT
    assert all(o["rerank_pass"]["applied"] is True
               for o in report["outcomes"])
    assert "rerank" in report["latency_ms"]            # printed in summary
    assert report["retrieval"]["rerank"]["max_session_chunks_max"] <= 2.0
    # every question's hits are the reader's context (selected-only)
    assert all(o["context_point_count"] ==
               o["rerank_pass"]["selected_count"] for o in report["outcomes"])


def test_cli_rerank_off_report_has_no_rerank_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("TORTOISE_LME_RERANK", raising=False)  # hermetic
    calls = {"n": 0}

    def _spy(model=None):
        calls["n"] += 1
        return (rerank.FakeScorer(), "")

    monkeypatch.setattr(rerank, "get_scorer", _spy)
    monkeypatch.setenv("TORTOISE_LME_RERANK", "1")  # leaked env + explicit
    out = tmp_path / "report.json"                  # --no-rerank: the
    report = run_main(["--data", str(MINI), "--limit", "5", "--split", "s",
                       "--mock", "--no-rerank", "--output", str(out)])
    assert "rerank" not in report
    assert "rerank" not in report["latency_ms"]     # zero-keys contract covers
    assert "rerank" not in report["retrieval"]      # ALL report surfaces
    assert "rerank" not in report["methodology"]["retrieval"]
    assert all("rerank_pass" not in o for o in report["outcomes"])
    assert calls["n"] == 0  # --no-rerank BEATS leaked env (tri-state wiring)
                            # and the baseline never resolves the scorer


def test_cli_rerank_invalid_lambda_fails_fast(tmp_path, capsys):
    with pytest.raises(SystemExit):
        run_main(["--data", str(MINI), "--limit", "5", "--split", "s",
                  "--mock", "--rerank", "--rerank-lambda", "1.5",
                  "--output", str(tmp_path / "r.json")])
    with pytest.raises(SystemExit):
        run_main(["--data", str(MINI), "--limit", "5", "--split", "s",
                  "--mock", "--rerank", "--rerank-cap", "0",
                  "--output", str(tmp_path / "r.json")])
    with pytest.raises(SystemExit):
        run_main(["--data", str(MINI), "--limit", "5", "--split", "s",
                  "--mock", "--rerank", "--rerank-pool", "0",
                  "--output", str(tmp_path / "r.json")])
    # no checkpoint written, no questions executed


def test_cli_rerank_boundary_values_accepted(tmp_path, monkeypatch):
    _inject_fake(monkeypatch)
    report = run_main(["--data", str(MINI), "--limit", "1", "--split", "s",
                       "--mock", "--rerank", "--rerank-cap", "1",
                       "--rerank-lambda", "0.0",
                       "--output", str(tmp_path / "r2.json")])
    assert report["rerank"]["per_session_cap"] == 1
    assert report["rerank"]["lambda_"] == 0.0


def test_cli_rerank_all_degraded(tmp_path, monkeypatch):
    """get_scorer always (None, reason) → zero failures, degraded_n == n,
    applied_fraction == 0.0, model_load_ms recorded once, prewarmed reflects
    the outcome — a pre-warm failure must NOT disable the run (D8b)."""
    monkeypatch.setattr(rerank, "get_scorer",
                        lambda model=None: (None, "load failed: outage"))
    out = tmp_path / "report.json"
    report = run_main(["--data", str(MINI), "--limit", "5", "--split", "s",
                       "--mock", "--rerank", "--output", str(out)])
    rr = report["rerank"]
    assert rr["degraded_n"] == 5
    assert rr["applied_fraction"] == 0.0
    assert rr["prewarmed"] is False
    assert rr["model_load_ms"] is not None
    assert report["n_failed"] == 0              # degrade, not failure
    assert rr["sample_reasons"]                # reasons recorded, not silent


def test_env_out_of_range_fails_fast(tmp_path, monkeypatch):
    """Parseable-but-out-of-range env values fail fast (SystemExit) before
    any question executes — the env path must not bypass the CLI fail-fast
    (effective-config validation in run_main)."""
    monkeypatch.setenv("TORTOISE_LME_RERANK_CAP", "0")
    with pytest.raises(SystemExit):
        run_main(["--data", str(MINI), "--limit", "1", "--split", "s",
                  "--mock", "--rerank"])
    monkeypatch.delenv("TORTOISE_LME_RERANK_CAP", raising=False)
    monkeypatch.setenv("TORTOISE_LME_RERANK_LAMBDA", "1.5")
    with pytest.raises(SystemExit):
        run_main(["--data", str(MINI), "--limit", "1", "--split", "s",
                  "--mock", "--rerank"])
    monkeypatch.delenv("TORTOISE_LME_RERANK_LAMBDA", raising=False)
    monkeypatch.setenv("TORTOISE_LME_RERANK_POOL", "0")
    with pytest.raises(SystemExit):
        run_main(["--data", str(MINI), "--limit", "1", "--split", "s",
                  "--mock", "--rerank"])


def test_env_parse_garbage_and_negative(tmp_path, monkeypatch):
    """Unparseable/negative env values clamp at the retrieve layer (the
    per-question safety net), not fail-fast at the run level."""
    monkeypatch.setenv("TORTOISE_LME_RERANK_CAP", "abc")
    monkeypatch.setenv("TORTOISE_LME_RERANK_POOL", "-5")
    monkeypatch.setenv("TORTOISE_LME_RERANK_LAMBDA", "garbage")
    _inject_fake(monkeypatch)
    sdk = _fresh_sdk(tmp_path)
    q = _mini()[0]
    try:
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    rerank=True)   # no explicit cap/lambda
        assert ret["rerank_pass"]["applied"] is True
        assert ret["rerank_pass"]["per_session_cap"] == 2   # default clamped
        assert ret["rerank_pass"]["lambda_"] == 0.7
    finally:
        sdk.close()


def test_effective_pool_recorded_truthfully(tmp_path, monkeypatch):
    """--rerank --rerank-pool 40 --k 5,10,20,50 → max(ks)=50 overrides the
    pool; the measured arm is labeled with the EFFECTIVE applied pool."""
    _inject_fake(monkeypatch)
    out = tmp_path / "report.json"
    report = run_main(["--data", str(MINI), "--limit", "5", "--split", "s",
                       "--mock", "--rerank", "--rerank-pool", "40",
                       "--k", "5,10,20,50", "--output", str(out)])
    assert report["rerank"]["pool_size"] == 50


def test_runner_env_hermeticity(tmp_path, monkeypatch):
    """With TORTOISE_LME_RERANK=1 exported, the DEFAULT path switches on —
    pins the precedence contract. The fake scorer is injected FIRST so the
    env-flipped call never touches the real ~90MB model (Gate 7)."""
    _inject_fake(monkeypatch)
    monkeypatch.setenv("TORTOISE_LME_RERANK", "1")
    sdk = _fresh_sdk(tmp_path)
    q = _mini()[0]
    try:
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20)
        assert "rerank_pass" in ret
        assert ret["rerank_pass"]["applied"] is True
    finally:
        sdk.close()


# ── Task 5: aggregation + checkpoint (three-valued D9) ─────────────────────

def test_retrieve_rerank_heterogeneous_run(tmp_path, monkeypatch):
    """A run mixing applied + degraded questions: degraded_n / applied_fraction
    / sample_reasons / pool_recall_mean@k over carriers only — degraded
    questions are in NEITHER failures NOR the applied set (partial-failure
    family)."""
    calls = {"n": 0}

    def _flaky(model=None):
        calls["n"] += 1
        if calls["n"] in (2, 4):               # questions 2 and 4 of the 5
            return (None, "load failed: flaky outage")
        return (rerank.FakeScorer(), "")

    monkeypatch.setattr(rerank, "get_scorer", _flaky)
    from tools.longmem_eval.judge import MockJudge
    from tools.longmem_eval.reader import MockReader
    outcomes, report = run_evaluation(
        _mini(), reader=MockReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
        rerank=True, rerank_pool=40, per_session_cap=2, mmr_lambda=0.7,
    )
    assert len(outcomes) == 5
    rr = report["rerank"]
    assert rr["degraded_n"] == 2
    assert rr["applied_fraction"] == 0.6
    assert any("flaky outage" in r for r in rr["sample_reasons"])
    assert rr["pool_recall_mean@k"]["session"]["5"] > 0  # carriers only
    degraded_qids = {o["question_id"] for o in outcomes
                     if not (o.get("rerank_pass") or {}).get("applied")}
    assert len(degraded_qids) == 2
    assert not (degraded_qids & {f["question_id"] for f in report["failures"]})
    applied_qids = {o["question_id"] for o in outcomes
                    if (o.get("rerank_pass") or {}).get("applied")}
    assert len(applied_qids) == 3 and not (degraded_qids & applied_qids)


def _write_checkpoint(path, fingerprint, outcomes=(), failures=()):
    path.write_text(json.dumps({
        "fingerprint": fingerprint, "outcomes": list(outcomes),
        "failures": list(failures)}, indent=2), encoding="utf-8")


def _run_eval(tmp_path, *, rerank=False, rerank_pool=None, ks=(5,),
              checkpoint=None, dataset_fingerprint="deadbeefcafe1234",
              cap=2, lam=0.7, **kw):
    from tools.longmem_eval.judge import MockJudge
    from tools.longmem_eval.reader import MockReader
    return run_evaluation(
        _mini()[:2], reader=MockReader(), judge=MockJudge(),
        ks=ks, top_k=5, split="s", work_dir=str(tmp_path),
        checkpoint=checkpoint, rerank=rerank, rerank_pool=rerank_pool,
        per_session_cap=cap, mmr_lambda=lam,
        dataset_fingerprint=dataset_fingerprint,
        **kw)


def test_checkpoint_resume_baseline_roundtrip(tmp_path, monkeypatch):
    """(b/d) equal config → allowed: a baseline checkpoint resumes cleanly."""
    cp = str(tmp_path / "lme-state.json")
    out1, _ = _run_eval(tmp_path, checkpoint=cp)
    assert len(out1) == 2
    out2, _ = _run_eval(tmp_path, checkpoint=cp)
    assert {o["question_id"] for o in out2} == \
        {o["question_id"] for o in out1}
    saved = json.loads(Path(cp).read_text(encoding="utf-8"))
    assert saved["fingerprint"]["rerank"]["enabled"] is False
    assert saved["fingerprint"]["rerank"]["pool_size"] == 5  # max(ks)


def test_checkpoint_resume_refuses_rerank_flip(tmp_path, monkeypatch):
    """(c) stored rerank-off resumed with --rerank → REFUSED before any
    question executes (blend hazard — Gate 8)."""
    cp = str(tmp_path / "lme-state.json")
    _run_eval(tmp_path, checkpoint=cp)                      # baseline
    with pytest.raises(CheckpointStaleError, match="rerank"):
        _run_eval(tmp_path, checkpoint=cp, rerank=True, cap=2, lam=0.7)


def test_checkpoint_resume_refuses_rerank_on_resumed_off(tmp_path, monkeypatch):
    """(c2) stored rerank-on resumed with off → REFUSED."""
    _inject_fake(monkeypatch)
    cp = str(tmp_path / "lme-state.json")
    _run_eval(tmp_path, checkpoint=cp, rerank=True, cap=2, lam=0.7)
    with pytest.raises(CheckpointStaleError, match="rerank"):
        _run_eval(tmp_path, checkpoint=cp)


def test_checkpoint_resume_same_rerank_config_allowed(tmp_path, monkeypatch):
    """(d) same rerank config (incl. pool/cap/λ) → ALLOWED."""
    _inject_fake(monkeypatch)
    cp = str(tmp_path / "lme-state.json")
    kw = dict(rerank=True, rerank_pool=40, cap=3, lam=0.5)
    out1, _ = _run_eval(tmp_path, checkpoint=cp, **kw)
    assert len(out1) == 2
    out2, _ = _run_eval(tmp_path, checkpoint=cp, **kw)
    assert len(out2) == 2                     # resumed (skipped, not re-run)


def test_checkpoint_resume_refuses_pool_change_rerank_off(tmp_path, monkeypatch):
    """(e) pool-only flag change (rerank_pool 20→40 with rerank off) →
    REFUSED — a pool-20↔pool-40 blend must not slip through."""
    cp = str(tmp_path / "lme-state.json")
    _run_eval(tmp_path, checkpoint=cp, rerank_pool=20)
    with pytest.raises(CheckpointStaleError, match="rerank"):
        _run_eval(tmp_path, checkpoint=cp, rerank_pool=40)


def test_checkpoint_resume_refuses_baseline_vs_pool_only(tmp_path, monkeypatch):
    """(f) baseline NO-FLAG checkpoint (records applied pool max(ks)=5)
    resumed with --rerank-pool 40 rerank-off → REFUSED — the
    applied-pool-vs-nominal-env-default hole (D9's rerank_pool_applied)."""
    cp = str(tmp_path / "lme-state.json")
    _run_eval(tmp_path, checkpoint=cp)                      # no pool flag
    with pytest.raises(CheckpointStaleError, match="rerank"):
        _run_eval(tmp_path, checkpoint=cp, rerank_pool=40)


def test_checkpoint_resume_pre_r6_refused(tmp_path):
    """(a) a stale pre-R6 checkpoint (fingerprint without the rerank key) is
    refused — M7's fingerprint gate is live, so D9(a) (baseline resume of a
    pre-R6 checkpoint) no longer applies per the plan's own note."""
    fp = _build_fingerprint(
        reader_model="mock-reader", judge_model="mock-judge", ks=(5,),
        top_k=5, split="s", ingest_mode="deterministic", extractor_model=None,
        max_retries=3, dataset_fingerprint="deadbeefcafe1234",
        rerank_config={"enabled": False, "model": None, "lambda_": None,
                       "per_session_cap": None, "pool_size": 5})
    del fp["rerank"]                            # genuinely pre-R6
    cp = tmp_path / "pre-r6.json"
    _write_checkpoint(cp, fp)
    with pytest.raises(CheckpointStaleError, match="rerank"):
        _run_eval(tmp_path, checkpoint=str(cp))


def test_resolve_rerank_baseline_records_max_ks(tmp_path):
    """A plain baseline run records applied pool max(ks) — never the nominal
    env default 40 (D9's rerank_pool_applied label)."""
    rr = _resolve_rerank(rerank=None, rerank_model=None, rerank_pool=None,
                         per_session_cap=None, mmr_lambda=None, max_k=20)
    assert rr["rerank_on"] is False
    assert rr["config"]["pool_size"] == 20
    assert rr["config"]["enabled"] is False
    rr2 = _resolve_rerank(rerank=True, rerank_model=None, rerank_pool=None,
                          per_session_cap=None, mmr_lambda=None, max_k=20)
    assert rr2["rerank_on"] is True
    assert rr2["config"]["pool_size"] == 40
    assert rr2["config"]["per_session_cap"] == 2


# ── concurrency (OQ6: shared cross-encoder across workers) ─────────────────

def test_get_scorer_concurrent_construction(monkeypatch):
    """N threads on a cache miss → EXACTLY ONE construction (the lock is
    held across construction — no double ~90MB download); all threads get
    the same instance."""
    calls = {"n": 0}

    class _Slow:
        def __init__(self, model, max_length=512):
            calls["n"] += 1
            time.sleep(0.05)

    monkeypatch.setattr(rerank, "CrossEncoderScorer", _Slow)
    results = []
    lock = threading.Lock()

    def _worker():
        sc, reason = rerank.get_scorer("test-concurrent-model")
        with lock:
            results.append((sc, reason))

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls["n"] == 1
    assert all(sc is not None and not reason for sc, reason in results)
    assert len({id(sc) for sc, _ in results}) == 1


def test_workers_rerank_smoke(tmp_path, monkeypatch):
    """workers=4 via the EXISTING ThreadPoolExecutor dispatch — exercises
    the real get_scorer cache + per-instance score lock with a slow fake
    constructor (NOT the monkeypatched get_scorer seam); every outcome
    applied, per-qid equality with the sequential run."""
    calls = {"n": 0}

    class _SlowFake(rerank.CrossEncoderScorer):
        def __init__(self, model, max_length=512):
            calls["n"] += 1
            time.sleep(0.02)
            self._inner = rerank.FakeScorer()
            self._lock = threading.Lock()

        def score(self, query, contents):
            return self._inner.score(query, contents)

    monkeypatch.setattr(rerank, "CrossEncoderScorer", _SlowFake)
    from tools.longmem_eval.judge import MockJudge
    from tools.longmem_eval.reader import MockReader
    base_kw = dict(reader=MockReader(), judge=MockJudge(), ks=(5,), top_k=5,
                   split="s", work_dir=str(tmp_path),
                   rerank=True, rerank_pool=40, per_session_cap=2,
                   mmr_lambda=0.7)
    seq, _ = run_evaluation(_mini()[:4], workers=1, **base_kw)
    par, _ = run_evaluation(_mini()[:4], workers=4, **base_kw)
    assert len(par) == 4
    assert all(o["rerank_pass"]["applied"] is True for o in par)
    assert calls["n"] == 1                    # one construction, shared cache
    by_qid = {o["question_id"]: o for o in seq}
    for o in par:
        assert o["rerank_pass"] == by_qid[o["question_id"]]["rerank_pass"]


# ── real model (opt-in, follow-up env only — never in CI) ──────────────────

@pytest.mark.slow
def test_real_cross_encoder_loads_and_orders(tmp_path):
    """Real model sanity. OPT-IN: skips unless TORTOISE_LME_RUN_REAL_MODEL=1
    (set in the follow-up-run environment, where the model is pre-warmed
    anyway). Never downloads ~90MB in CI — the fast gate does not set it."""
    if not os.environ.get("TORTOISE_LME_RUN_REAL_MODEL"):
        pytest.skip("real cross-encoder is opt-in (TORTOISE_LME_RUN_REAL_MODEL=1)")
    sc, reason = rerank.get_scorer("cross-encoder/ms-marco-MiniLM-L6-v2")
    assert sc is not None, reason
    s = sc.score("when is the gym session",
                 ["gym at 5pm", "user runs weekends"])
    assert s[0] > s[1]
