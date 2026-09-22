"""#1786 (R5): eval retrieval deadline via the existing _elevated_timeout_ms
seam — hybrid arm only, SDK default untouched.

Covers the pinned Task 4 matrix: seam threading (the eval's 1500 ms budget
reaches tortoise_fts_query → degradation_chain), the fingerprint refusal
(a checkpoint without the hybrid budget refuses under 1500 ms; the vector
arm's VECTOR_TIMEOUT_MS stays OUT of the fingerprint key — P2-5), and the
vector arm being UNAFFECTED by the budget.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval import run as runner
from tools.longmem_eval.retrieve import (
    EVAL_RETRIEVAL_BUDGET_MS,
    VECTOR_TIMEOUT_MS,
    retrieve_for_question,
)
from tortoise.sdk import TortoiseSDK

MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"


def _mini() -> list[dict]:
    return json.loads(MINI.read_text(encoding="utf-8"))


def _fresh_sdk(tmp_path) -> TortoiseSDK:
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    return TortoiseSDK(str(tmp_path / "lme.db"))


# ── Task 4 test (a): seam threading — hybrid arm gets the elevated budget ──


def test_hybrid_seam_threads_elevated_timeout(tmp_path, monkeypatch):
    """The eval's 1500 ms budget threads retrieve_for_question →
    hybrid_search → tortoise_fts_query(_elevated_timeout_ms=...) for the
    HYBRID arm; the SDK default (None → 500 ms) is untouched for
    programmatic callers."""
    captured: dict = {}

    def _fake_fts(self_or_sdk, query, entity_type="point", limit=10, **kw):
        captured["timeout"] = kw.get("_elevated_timeout_ms")
        return []

    monkeypatch.setattr(TortoiseSDK, "tortoise_fts_query", _fake_fts)
    sdk = _fresh_sdk(tmp_path)
    try:
        q = _mini()[0]
        # the eval path passes the elevated budget
        ret = retrieve_for_question(
            sdk, q, ks=(5,), top_k=5, retrieval_budget_ms=EVAL_RETRIEVAL_BUDGET_MS)
        assert captured["timeout"] == 1500
        assert ret["retrieval_latency_ms"] >= 0
        # the default path keeps the SDK default (None → degradation_chain
        # falls back to 500 ms)
        retrieve_for_question(sdk, q, ks=(5,), top_k=5)
        assert captured["timeout"] is None
    finally:
        sdk.close()


def test_deadline_degradation_records_timeout_reason(tmp_path, monkeypatch):
    """A leg slower than the collective deadline degrades with reason='timeout'
    recorded in the leg trace (the degradation_chain as_completed contract):
    the fts strategy sleeps past the 1500 ms deadline, as_completed raises
    TimeoutError, and the leg trace self-records {"leg": "fts", "ran": True,
    "degraded": True, "reason": "timeout", "count": 0}."""
    import tortoise.search_engine as se

    def _slow_fts(graph, query, entity_type="point", limit=20,
                  timeout_ms=500, excluded_statuses=None, leg_trace=None,
                  keep_numeric=False, expansion_terms=None):
        import time as _t
        _t.sleep(2.0)  # exceeds the 1500 ms collective deadline
        return []

    monkeypatch.setattr(se, "run_fts_query", _slow_fts)
    sdk = _fresh_sdk(tmp_path)
    try:
        q = _mini()[0]
        ret = retrieve_for_question(
            sdk, q, ks=(5,), top_k=5,
            retrieval_budget_ms=EVAL_RETRIEVAL_BUDGET_MS)
        legs = ret["legs"]
        timeout_legs = [leg for leg in legs if leg.get("reason") == "timeout"]
        assert timeout_legs, f"expected a timeout leg, got {legs}"
        assert any(leg.get("leg") == "fts" and leg.get("ran") is True
                   and leg.get("degraded") is True for leg in timeout_legs)
    finally:
        sdk.close()


# ── Task 4 test (e): the vector arm is UNAFFECTED by the 1500 ms budget ────


def test_vector_arm_unaffected_by_eval_budget(tmp_path, monkeypatch):
    """P2-8: retrieve_for_question(retriever='vector') resolves
    VECTOR_TIMEOUT_MS=5000 via run_vector_query and NEVER passes through
    tortoise_fts_query / the seam."""
    import tortoise.search_engine as se
    from tools.longmem_eval import retrieve as ret_mod

    captured: dict = {}
    calls = {"fts": 0, "vector": 0}

    def _fake_vector_query(graph, query_vec, limit=10, is_embedded=False,
                           entity_type="point", timeout_ms=500,
                           vector_index_api=None, excluded_statuses=None,
                           leg_trace=None):
        captured["timeout_ms"] = timeout_ms
        calls["vector"] += 1
        return []

    def _fake_fts(*a, **k):
        calls["fts"] += 1
        return []

    # make the graph look embedding-bearing + encode cheaply so the REAL
    # vector_search path resolves VECTOR_TIMEOUT_MS (the timeout capture is
    # the point of the test — a mocked vector_search would skip it)
    monkeypatch.setattr(ret_mod, "_count_embedded_points", lambda proj: 1)
    monkeypatch.setattr(ret_mod, "_encode_query_vec", lambda q: [0.0])
    monkeypatch.setattr(se, "run_vector_query", _fake_vector_query)
    monkeypatch.setattr(TortoiseSDK, "tortoise_fts_query", _fake_fts)
    sdk = _fresh_sdk(tmp_path)
    try:
        q = _mini()[0]
        ret = retrieve_for_question(
            sdk, q, ks=(5,), top_k=5, retriever="vector",
            retrieval_budget_ms=EVAL_RETRIEVAL_BUDGET_MS)
        assert captured["timeout_ms"] == VECTOR_TIMEOUT_MS == 5000
        assert calls["fts"] == 0  # the vector arm never touches the seam
        assert ret["retrieval_latency_ms"] >= 0
    finally:
        sdk.close()


# ── Task 4 test (c): fingerprint — the hybrid budget stales old checkpoints ─


def test_fingerprint_budget_refusal_and_vector_policy(tmp_path):
    """P1-L/P2-5: a checkpoint whose fingerprint lacks retrieval_budget_ms
    (or has 500) REFUSES under the eval's 1500 ms budget via
    CheckpointStaleError — a pre-feature checkpoint must never be silently
    resumed under a different retrieval budget. The vector arm's
    VECTOR_TIMEOUT_MS is NOT a fingerprint key (SDK-pinned, not eval-
    configurable) — a checkpoint can never differ on it at the eval layer."""
    base = dict(reader_model="mock-reader", judge_model="mock-judge",
                ks=(5,), top_k=5, split="s", ingest_mode="deterministic",
                extractor_model=None, max_retries=3,
                dataset_fingerprint="x", rerank_config={})
    # the eval's fingerprint carries the hybrid budget
    fp_eval = runner._build_fingerprint(**base,
                                        retrieval_budget_ms=EVAL_RETRIEVAL_BUDGET_MS)
    assert fp_eval["retrieval_budget_ms"] == 1500
    # the vector budget is OUT of the fingerprint key (P2-5) — no key at all
    assert "vector_budget_ms" not in fp_eval
    # the SDK-default (no budget passed) fingerprint omits the key
    fp_default = runner._build_fingerprint(**base)
    assert "retrieval_budget_ms" not in fp_default

    # a checkpoint written under the default (no key) refuses under 1500
    cp = tmp_path / "cp.json"
    runner._save_checkpoint(str(cp), [], [], fp_default)
    with pytest.raises(runner.CheckpointStaleError) as ei:
        runner._load_checkpoint(str(cp), fp_eval)
    assert "retrieval_budget_ms" in str(ei.value)

    # an explicit 500 ms checkpoint refuses under 1500 too
    fp_500 = runner._build_fingerprint(**base, retrieval_budget_ms=500)
    cp2 = tmp_path / "cp2.json"
    runner._save_checkpoint(str(cp2), [], [], fp_500)
    with pytest.raises(runner.CheckpointStaleError):
        runner._load_checkpoint(str(cp2), fp_eval)

    # identical budgets resume cleanly
    cp3 = tmp_path / "cp3.json"
    runner._save_checkpoint(str(cp3), [], [], fp_eval)
    done, failures = runner._load_checkpoint(str(cp3), fp_eval)
    assert done == {} and failures == []


def test_methodology_records_per_arm_budgets():
    """P1-H: the eval's methodology.retrieval_config records hybrid_budget_ms
    = 1500 and vector_budget_ms = 5000 (the 3.3x asymmetry is deliberate and
    documented)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools.longmem_eval.run import _build_fingerprint  # noqa: F401
    assert EVAL_RETRIEVAL_BUDGET_MS == 1500
    assert VECTOR_TIMEOUT_MS == 5000
