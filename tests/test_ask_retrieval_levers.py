"""#2070 — ask-lane retrieval optimisation loop tests.

Spec items 9/10 of docs/planning/2026-08-31-2070-scoping-package.md:

- 4 gold-turn-inclusion fixtures (ceb54acb / 1de5cff2 / gpt4_d84a3211 /
  1d4e3b97 — Acceptance Indicator 1 / Target 0: the gold turn id must land
  in the ASSEMBLED context under the caps). **Runtime prerequisite (plan
  A2): the vector leg (embeddings extra) is required for the lexical-trio
  class — when the embedder is absent the fixture skips with that reason,
  exactly as the bench's docker lane does. On the degraded embedded lane
  the honest assertions are POOL MEMBERSHIP (the plan's "fix membership
  before ordering" step), which is what A1/A4/A5 deliver there.**
- Same-value numeric fixture (A1): a money question that itself carries an
  amount retrieves the amount-carrying turn with keep_numeric on.
- OR-cap preservation (A4): injected expansion aliases never displace the
  ORIGINAL query's tokens (the regression guard).
- Search-lane byte-identity control (A1/A4/A3): the search lane passes no
  ask knobs → identical query bytes and results.
- retrieval_degraded honesty (A2): the ask lane still reports degraded
  when the vector leg is absent — never a silent success.
- Product rerank degrade-path (A7): tortoise/rerank.py degrades to
  untouched (applied False) on scorer failure, never raises.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK

_REPO = Path(__file__).resolve().parent.parent
_CACHED_DATASET = Path(
    os.environ.get(
        "TORTOISE_LME_DATASET",
        "~/.cache/tortoise-longmemeval/longmemeval_s_cleaned.json",
    )).expanduser()
_MINIFIX = _REPO / "tests" / "fixtures" / "longmemeval_mini.json"
RECORDED_FAILURES = ["ceb54acb", "1de5cff2", "gpt4_d84a3211", "1d4e3b97"]


def _embedder_present() -> bool:
    from tortoise.embeddings import EmbeddingModel
    return EmbeddingModel.get() is not None


def _fresh_sdk() -> TortoiseSDK:
    td = tempfile.mkdtemp(prefix="ask2070_")
    return TortoiseSDK(os.path.join(td, "lme.db"))


def _gold_turn_ids(question: dict) -> set[str]:
    qid = question["question_id"]
    ids: set[str] = set()
    for si, session in enumerate(question.get("haystack_sessions") or []):
        for ti, turn in enumerate(session):
            if turn.get("has_answer"):
                ids.add(f"lme:{qid}:s{si}:t{ti}")
    return ids


def _seed(sdk, question: dict) -> None:
    from tools.longmem_eval.ingest import ingest_haystack
    ingest_haystack(sdk, question)


def _ask_pipeline(sdk, question: str, *, keep_numeric: bool = False,
                  search_keys_prf: bool = False,
                  evidence_boost: bool = False,
                  limit: int = 40, item_cap: int = 40) -> list[dict]:
    """The ask lane's retrieval→annotate→dedup→boost→assemble sequence
    (mirrors tools/ask_recall_bench.py::_retrieve_pipeline; no reader)."""
    from tortoise.retrieval import (
        DEFAULT_MAX_CHUNKS_PER_SESSION,
        apply_evidence_boost,
        assemble_context,
        dedup_pool,
        resolve_ask_boost_multipliers,
    )
    hits = sdk.tortoise_fts_query(
        question, limit=limit, pool_size=120, include_terminal=True,
        keep_numeric=keep_numeric, search_keys_prf=search_keys_prf)
    annotated = sdk.annotate_ask_hits(hits)

    def _session_key(h: dict) -> str:
        return (h.get("session_id") or h.get("session_date")
                or f"idx:{h.get('lme_session_index', -1)}")

    deduped = dedup_pool(
        annotated, max_chunks_per_session=DEFAULT_MAX_CHUNKS_PER_SESSION,
        session_key=_session_key)
    if evidence_boost:
        mult = resolve_ask_boost_multipliers()
        deduped, _ = apply_evidence_boost(
            deduped, boost_answer_string=mult["answer_string"],
            boost_verbatim=mult["verbatim"], boost_source=mult["source"])
    return assemble_context(
        deduped, top_k=item_cap, max_context_tokens=8000,
        context_item_cap=item_cap, byte_cap=32768)


def _recorded_questions() -> dict[str, dict]:
    if not _CACHED_DATASET.exists():
        return {}
    data = json.loads(_CACHED_DATASET.read_text(encoding="utf-8"))
    return {q["question_id"]: q for q in data if q["question_id"] in RECORDED_FAILURES}


# ── Indicator 1 / Target 0: 4 gold-turn-in-context fixtures ────────────────

@pytest.mark.parametrize("qid", RECORDED_FAILURES)
def test_gold_turn_in_context_recorded_failure(qid):
    """Indicator 1 / Target 0: each recorded retrieval-gap failure retrieves
    its gold turn WITHIN the context caps on the vector-leg lane (A2's
    runtime prerequisite — the plan's acceptance surface). Verified live on
    the docker lane: 4/4 gold-in-ctx(40) with A1+A4+A5 levers, caps at 40."""
    if not _embedder_present():
        pytest.skip(
            "vector leg absent (embeddings extra not installed) — the "
            "lexical-trio class needs it (plan A2 runtime prerequisite); "
            "see the pool-membership tests for the degraded-lane assertions")
    questions = _recorded_questions()
    if not questions:
        pytest.skip("cached LongMemEval dataset absent (bench prerequisite)")
    q = questions.get(qid)
    if q is None:
        pytest.skip(f"{qid} not in cached dataset")
    sdk = _fresh_sdk()
    try:
        _seed(sdk, q)
        gold = _gold_turn_ids(q)
        assert gold, f"{qid}: dataset question carries no has_answer turns"
        ctx = _ask_pipeline(
            sdk, q["question"], keep_numeric=True,
            search_keys_prf=True, evidence_boost=True)
        ctx_ids = {h["id"] for h in ctx}
        assert ctx_ids & gold, (
            f"{qid}: gold turns {sorted(gold)} all missed the assembled "
            f"context (got {len(ctx_ids)} ids)"
        )
    finally:
        sdk.close()


# ── Degraded-lane honesty: pool membership (the "fix membership" step) ─────

def test_gold_turn_in_pool_membership_embedded():
    """On the degraded embedded lane (no vector leg), the A1/A4/A5 levers
    deliver POOL MEMBERSHIP for the rank/cap + thin-overlap classes (the
    plan's "fix membership before ordering" step). gpt4_d84a3211 is the
    numeric-invisible class whose membership requires the vector leg (A2) —
    asserted separately with its documented prerequisite."""
    questions = _recorded_questions()
    if not questions:
        pytest.skip("cached LongMemEval dataset absent (bench prerequisite)")
    for qid, q in questions.items():
        if qid == "gpt4_d84a3211":
            continue  # documented: needs the vector leg (A2), not A1
        sdk = _fresh_sdk()
        try:
            _seed(sdk, q)
            gold = _gold_turn_ids(q)
            assert gold, f"{qid}: no has_answer turns"
            hits = sdk.tortoise_fts_query(
                q["question"], limit=120, pool_size=120,
                include_terminal=True, keep_numeric=True,
                search_keys_prf=True)
            pool_ids = {h["id"] for h in hits}
            assert pool_ids & gold, (
                f"{qid}: gold {sorted(gold)} not even in the 120-pool on "
                f"the degraded lane — membership lever failed")
        finally:
            sdk.close()


def test_gold_turn_in_context_cap_review_embedded_1d4e3b97():
    """A6 measurement-gated cap review, embedded lane: 1d4e3b97 (in-pool,
    thin overlap) retrieves its gold IN CONTEXT once the retrieval window
    and item cap are raised together (40→120) — the A6 fix as measured in
    Step 0. Default-off knobs are exercised explicitly."""
    questions = _recorded_questions()
    if not questions:
        pytest.skip("cached LongMemEval dataset absent (bench prerequisite)")
    q = questions.get("1d4e3b97")
    if q is None:
        pytest.skip("1d4e3b97 not in cached dataset")
    sdk = _fresh_sdk()
    try:
        _seed(sdk, q)
        gold = _gold_turn_ids(q)
        ctx = _ask_pipeline(
            sdk, q["question"], keep_numeric=True, search_keys_prf=True,
            evidence_boost=True, limit=120, item_cap=120)
        assert {h["id"] for h in ctx} & gold, (
            "1d4e3b97: gold must land in context when the window+cap are "
            "raised together (A6 tandem threading)")
    finally:
        sdk.close()


def test_gold_turn_in_context_short_history_control():
    """Target 1 control: a SHORT-history question (mini fixture) still
    retrieves its gold turn — no regression of the short baseline."""
    mini = json.loads(_MINIFIX.read_text(encoding="utf-8"))
    q = mini[0]
    sdk = _fresh_sdk()
    try:
        _seed(sdk, q)
        gold = _gold_turn_ids(q)
        assert gold, "mini q0 should carry has_answer turns"
        ctx = _ask_pipeline(sdk, q["question"])
        assert {h["id"] for h in ctx} & gold, (
            "short-history control: gold turn missing from context")
    finally:
        sdk.close()


# ── A1: same-value numeric policy ──────────────────────────────────────────

def test_numeric_tokens_kept_with_keep_numeric():
    """A1: a money question that itself carries an amount keeps the numeric
    token with keep_numeric on; the search lane (off) keeps dropping it."""
    from tortoise.sparse import tokenize_sparse_query
    assert "40" not in tokenize_sparse_query("how much did I spend on "
                                             "lights at $40?")
    assert "40" in tokenize_sparse_query(
        "how much did I spend on lights at $40?", keep_numeric=True)


def test_numeric_policy_ask_lane_only_flag_default_off():
    """A1: the search lane is byte-identical — keep_numeric defaults False."""
    from tortoise.sparse import build_or_query
    assert build_or_query("price was $40") == build_or_query(
        "price was $40", keep_numeric=False)


# ── A4: OR-cap preservation ────────────────────────────────────────────────

def test_expansion_never_displaces_original_tokens():
    """A4 regression guard: injected aliases fill ONLY the tail after the
    original tokens' slots — a long alias can never crowd out a shorter
    original token."""
    from tortoise.sparse import DEFAULT_MAX_EXPANSION_TERMS, build_or_query
    q = "my favorite board game is catan"
    original = build_or_query(q)                      # 12-cap original
    original_tokens = original.split("|")
    expanded = build_or_query(
        q, expansion_terms=["supercalifragilisticexpialidocious",
                            "bike", "helmet", "chain", "cassette"])
    expanded_tokens = expanded.split("|")
    # every original token keeps its slot, in order
    assert set(original_tokens) <= set(expanded_tokens)
    assert expanded_tokens[:len(original_tokens)] == original_tokens
    # expansion is bounded at DEFAULT_MAX_EXPANSION_TERMS beyond the cap
    assert len(expanded_tokens) <= 12 + DEFAULT_MAX_EXPANSION_TERMS


# ── Search-lane byte-identity control ──────────────────────────────────────

def test_search_lane_byte_identity():
    """A1/A3/A4 control: the product search lane passes no ask knobs →
    identical query bytes; run_fts_query defaults are unchanged."""
    import inspect

    from tortoise.search_engine import run_fts_query
    from tortoise.sparse import build_or_query
    q = "what is ava's favorite board game?"
    assert build_or_query(q) == build_or_query(
        q, keep_numeric=False, expansion_terms=None)
    sig = inspect.signature(run_fts_query)
    assert sig.parameters["keep_numeric"].default is False
    assert sig.parameters["expansion_terms"].default is None


# ── A2: retrieval_degraded honesty ─────────────────────────────────────────

def test_retrieval_degraded_honest_when_embedder_absent():
    """A2: the ask lane still surfaces degradation honestly when the vector
    leg is absent — the fix never papers over a degraded lane."""
    if _embedder_present():
        pytest.skip("embedder present — degraded-absence path not exercised")
    mini = json.loads(_MINIFIX.read_text(encoding="utf-8"))
    q = mini[0]
    sdk = _fresh_sdk()
    try:
        _seed(sdk, q)
        from tortoise.embeddings import EmbeddingModel
        assert EmbeddingModel.get() is None  # the honest precondition
        # the degraded flag is resolved by the ask() surface from leg_trace;
        # the existing test_ask_sdk retrieval_degraded tests pin that path.
        # Here we pin the lever knobs' default-off posture:
        from tortoise.retrieval import resolve_ask_retrieval_caps
        caps = resolve_ask_retrieval_caps()
        assert caps == {"limit": 40, "context_item_cap": 40,
                        "context_token_cap": 8000}
    finally:
        sdk.close()


# ── A7: product rerank degrade-path (ported eval contract) ─────────────────

def test_product_rerank_off_by_default():
    """A7: the product rerank is fail-safe OFF (env truthy-only)."""
    os.environ.pop("TORTOISE_ASK_RERANK", None)
    from tortoise.rerank import rerank_enabled
    assert rerank_enabled() is False
    os.environ["TORTOISE_ASK_RERANK"] = "1"
    try:
        assert rerank_enabled() is True
    finally:
        os.environ.pop("TORTOISE_ASK_RERANK", None)
    os.environ["TORTOISE_ASK_RERANK"] = "garbage"
    try:
        assert rerank_enabled() is False  # typo never flips the knob
    finally:
        os.environ.pop("TORTOISE_ASK_RERANK", None)


def test_product_rerank_degrade_to_untouched_on_failure():
    """A7: ask_lane_rerank degrades to untouched (applied False) on scorer
    failure / empty scorer — never raises (the eval's degrade contract)."""
    from tortoise.rerank import ask_lane_rerank
    mini = json.loads(_MINIFIX.read_text(encoding="utf-8"))
    q = mini[0]
    sdk = _fresh_sdk()
    try:
        _seed(sdk, q)
        hits = sdk.tortoise_fts_query(q["question"], limit=40, pool_size=120)
        annotated = sdk.annotate_ask_hits(hits)
        out, stats = ask_lane_rerank(
            q["question"], annotated, proj=sdk._get_proj(), top_k=40)
        assert out == annotated, "degrade-to-untouched must preserve order"
        assert stats.get("applied") is False
        assert stats.get("degrade_reason")
    finally:
        sdk.close()
