"""Product tests for ``tortoise/retrieval.py`` + ``tortoise/retry.py``.

The retrieval-quality logic moved from the LongMemEval harness
(tools/longmem_eval/retrieve.py / errors.py) into the PRODUCT by
fix/invert-retrieval-to-product (docs/audit/2026-08-29-product-cohesion.md
retrieval PARTIAL section): pool-depth resolution, rank-interleaved context
assembly, the evidence-mark boost, and the retry predicate. These tests are
the ported eval tests — the same behavioral contracts, imported from the
product home (the eval's thin-caller tests in tests/test_longmem_runner.py
exercise the identical logic through the re-exports).

Covers:
  * resolve_pool_size — the product-owned pool depth (default 120,
    #1947/G2): exact override / env / baked floor semantics for BOTH the
    SDK caller (exact=True) and the eval caller (exact=False).
  * dedup_pool / is_raw_chunk — per-session chunk caps (C5: 3).
  * assemble_context / render_context / estimate_tokens — the C1 (#1745)
    budget-capped rank-interleaved reader context + the token alignment
    invariant.
  * apply_evidence_boost — the C2 (#1745) / #1945 position-ceiling rank
    promotion over a ``mark_for`` provider (default: stored-has_answer →
    source class).
  * tortoise/retry.py — the #1806 transport-only retry predicate + bounded
    jittered retry loop.
"""
from __future__ import annotations

import errno
import urllib.error

import pytest
import redis.exceptions as redis_exc
import requests

from tortoise.retrieval import (
    DEFAULT_CONTEXT_ITEM_CAP,
    DEFAULT_CONTEXT_TOKEN_CAP,
    DEFAULT_EVIDENCE_BOOST_ANSWER_STRING,
    DEFAULT_EVIDENCE_BOOST_SOURCE,
    DEFAULT_EVIDENCE_BOOST_VERBATIM,
    DEFAULT_MAX_CHUNKS_PER_SESSION,
    DEFAULT_POOL_SIZE,
    TOKEN_ESTIMATOR,
    apply_evidence_boost,
    assemble_context,
    dedup_pool,
    estimate_tokens,
    is_raw_chunk,
    render_context,
    resolve_pool_size,
)
from tortoise.retry import (
    WriteStageRetriesExhausted,
    call_with_predicate,
    retryable_transient,
)

# ── pool depth (resolve_pool_size, #1947 / audit G2) ──────────────────────


def test_resolve_pool_size_default_is_120():
    """The product OWNS the pool-depth number: the default is 120 (the
    #1947 deepened depth, up from the historical limit*2 = 20 at limit=10).
    The caller's floor (e.g. limit*2 for the SDK, max(ks) for the eval) is
    always honored on the env/default path."""
    assert DEFAULT_POOL_SIZE == 120
    # SDK caller: floor = limit*2
    assert resolve_pool_size(10, default=DEFAULT_POOL_SIZE) == 120
    assert resolve_pool_size(100) == 120  # max(100*2? no — caller passes floor)
    assert resolve_pool_size(200, default=DEFAULT_POOL_SIZE) == 200
    # eval caller: floor = max(ks)
    assert resolve_pool_size(20, default=DEFAULT_POOL_SIZE, exact=False) == 120
    assert resolve_pool_size(200, default=DEFAULT_POOL_SIZE, exact=False) == 200


def test_resolve_pool_size_env_knob():
    """env (``env_name``) beats the default; garbage/blank/<1 fall back to
    the default; the resolved value is clamped to [1, 10000]."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv("TORTOISE_POOL_TEST", "80")
        assert resolve_pool_size(10, env_name="TORTOISE_POOL_TEST",
                                 default=120) == 80
        assert resolve_pool_size(10, env_name="TORTOISE_POOL_TEST",
                                 default=120, exact=False) == 80
        monkeypatch.setenv("TORTOISE_POOL_TEST", "banana")
        assert resolve_pool_size(10, env_name="TORTOISE_POOL_TEST",
                                 default=120) == 120
        monkeypatch.setenv("TORTOISE_POOL_TEST", "0")
        assert resolve_pool_size(10, env_name="TORTOISE_POOL_TEST",
                                 default=120) == 120
        monkeypatch.setenv("TORTOISE_POOL_TEST", "20000")
        assert resolve_pool_size(10, env_name="TORTOISE_POOL_TEST",
                                 default=120) == 10000
        monkeypatch.delenv("TORTOISE_POOL_TEST")
        assert resolve_pool_size(10, env_name="TORTOISE_POOL_TEST",
                                 default=120) == 120
    finally:
        monkeypatch.undo()


def test_resolve_pool_size_exact_override_semantics():
    """SDK contract (exact=True): an explicit ``pool_size`` is EXACT — a
    value below the floor LOWERS the pool (never floored back up); the
    env/default path applies max(resolved, floor). Eval contract
    (exact=False): the caller's floor is ALWAYS honored — a knob below the
    deepest recall horizon cannot truncate the measured pool."""
    # SDK: pool_size below limit*2 lowers exactly
    assert resolve_pool_size(10, pool_size=4, exact=True) == 4
    assert resolve_pool_size(10, pool_size=12, exact=True) == 12
    # SDK: env path floors
    assert resolve_pool_size(10, pool_size=None, default=120, exact=True) == 120
    # SDK: env BELOW limit*2 never lowers below limit*2 (floor-only
    # contract — max(limit*2, env)); env replaces the baked default
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv("TORTOISE_POOL_TEST", "4")
        assert resolve_pool_size(10, env_name="TORTOISE_POOL_TEST",
                                 default=120, exact=True) == 10
        monkeypatch.setenv("TORTOISE_POOL_TEST", "300")
        assert resolve_pool_size(10, env_name="TORTOISE_POOL_TEST",
                                 default=120, exact=True) == 300
    finally:
        monkeypatch.undo()
    # eval: pool_size is floored by max(ks)
    assert resolve_pool_size(20, pool_size=10, exact=False) == 20
    assert resolve_pool_size(20, pool_size=240, exact=False) == 240
    # eval: env knob floored by max(ks)
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv("TORTOISE_POOL_TEST", "10")
        assert resolve_pool_size(20, env_name="TORTOISE_POOL_TEST",
                                 default=120, exact=False) == 20
    finally:
        monkeypatch.undo()


# ── pool assembly: is_raw_chunk / dedup_pool (C5 #1745, R1 #1540) ─────────


def test_is_raw_chunk_recognizes_transcript():
    assert is_raw_chunk({"point_kind": "session-transcript"}) is True
    assert is_raw_chunk({"point_kind": "statement"}) is False
    assert is_raw_chunk({"point_kind": ""}) is False


def test_dedup_pool_per_session_cap():
    """Per-session chunk cap (rank order): at most ``max_chunks_per_session``
    raw chunks per session; points/turn points are never capped; distinct
    sessions never share a bucket (missing session_id → lme_session_index
    bucket, no ``-1`` collapse)."""
    hits = [
        {"id": "a0", "content": "a", "point_kind": "session-transcript",
         "session_id": "sess-a", "lme_session_index": -1},
        {"id": "a1", "content": "b", "point_kind": "session-transcript",
         "session_id": "sess-a", "lme_session_index": -1},
        {"id": "a2", "content": "c", "point_kind": "session-transcript",
         "session_id": "sess-a", "lme_session_index": -1},
        {"id": "b0", "content": "d", "point_kind": "session-transcript",
         "session_id": "sess-b", "lme_session_index": -1},
        {"id": "b1", "content": "e", "point_kind": "session-transcript",
         "session_id": "sess-b", "lme_session_index": -1},
        {"id": "b2", "content": "f", "point_kind": "session-transcript",
         "session_id": "sess-b", "lme_session_index": -1},
    ]
    pool = dedup_pool(hits, max_chunks_per_session=2)
    assert [h["id"] for h in pool] == ["a0", "a1", "b0", "b1"]
    # missing session_id entirely → bucket by lme_session_index (distinct
    # indices are distinct buckets, never one shared idx:-1)
    hits2 = [
        {"id": "x0", "content": "a", "point_kind": "session-transcript",
         "session_id": "", "lme_session_index": 0},
        {"id": "x1", "content": "b", "point_kind": "session-transcript",
         "session_id": "", "lme_session_index": 0},
        {"id": "x2", "content": "c", "point_kind": "session-transcript",
         "session_id": "", "lme_session_index": 0},
        {"id": "y0", "content": "d", "point_kind": "session-transcript",
         "session_id": "", "lme_session_index": 1},
        {"id": "y1", "content": "e", "point_kind": "session-transcript",
         "session_id": "", "lme_session_index": 1},
    ]
    pool2 = dedup_pool(hits2, max_chunks_per_session=2)
    assert [h["id"] for h in pool2] == ["x0", "x1", "y0", "y1"]
    # C5 (#1745): the default cap is 3 (2 -> 3 — only 4/854 sessions have
    # >2 marked chunks, so the budget cost is bounded)
    assert DEFAULT_MAX_CHUNKS_PER_SESSION == 3
    with pytest.raises(ValueError):
        dedup_pool(hits, max_chunks_per_session=0)


# ── context assembly (C1 #1745): assemble_context / render_context ────────


def test_context_interleaves_points_and_chunks_by_rank():
    """C1 (#1745): rank-interleaved assembly — a chunk ranked above a
    point in the pool appears BEFORE that point in the context (the
    historical points-first partition is deliberately reversed: it starved
    the chunk leg whenever the pool had >= top_k points)."""
    pool = [
        {"id": "chunk1", "content": "chunk number one here",
         "point_kind": "session-transcript", "lme_session_index": 0,
         "session_date": ""},
        {"id": "pt1", "content": "point one", "point_kind": "statement",
         "lme_session_index": 0},
        {"id": "chunk2", "content": "chunk number two here",
         "point_kind": "session-transcript", "lme_session_index": 1,
         "session_date": ""},
        {"id": "pt2", "content": "point two", "point_kind": "statement",
         "lme_session_index": 1},
    ]
    ctx = assemble_context(pool, top_k=20, max_context_tokens=10**6)
    assert [h["id"] for h in ctx] == ["chunk1", "pt1", "chunk2", "pt2"]
    # top_k bounds the rank-interleaved list
    ctx2 = assemble_context(pool, top_k=2, max_context_tokens=10**6)
    assert [h["id"] for h in ctx2] == ["chunk1", "pt1"]


def test_context_item_cap_and_token_budget():
    """C1 (#1745): with a 60-item pool the context fills to
    min(context_item_cap, budget-selected) — the item cap binds FIRST when
    the pool is under the token budget; a tight budget shows the token
    budget binding within the cap."""
    pool = [{"id": f"p{i}", "content": f"point number {i} details",
             "point_kind": "statement", "lme_session_index": i % 3}
            for i in range(60)]
    # item cap binds: budget never binds (10**6), 60-item pool → exactly 40
    ctx = assemble_context(pool, top_k=20, max_context_tokens=10**6,
                           context_item_cap=DEFAULT_CONTEXT_ITEM_CAP)
    assert len(ctx) == DEFAULT_CONTEXT_ITEM_CAP == 40
    assert [h["id"] for h in ctx] == [f"p{i}" for i in range(40)]
    # token budget binds within the cap: fewer than 40 items, tokens <= cap
    ctx2 = assemble_context(pool, top_k=20, max_context_tokens=50,
                            context_item_cap=40)
    assert 0 < len(ctx2) < 40
    assert estimate_tokens(render_context(ctx2)) <= 50
    # boundary guards
    with pytest.raises(ValueError):
        assemble_context(pool, top_k=20, max_context_tokens=0)
    with pytest.raises(ValueError):
        assemble_context(pool, top_k=20, max_context_tokens=10**6,
                         context_item_cap=0)


def test_context_token_budget_enforced():
    """Cap below the full pool → context_tokens ≤ cap; the truncated tail
    follows rank order (interleaved — a chunk can survive while a later
    point is dropped, C1 #1745)."""
    pool = [
        {"id": "chunk1", "content": "chunk number one here",
         "point_kind": "session-transcript", "lme_session_index": 0,
         "session_date": ""},
        {"id": "pt1", "content": "point one", "point_kind": "statement",
         "lme_session_index": 0},
        {"id": "chunk2", "content": "chunk number two here",
         "point_kind": "session-transcript", "lme_session_index": 1,
         "session_date": ""},
        {"id": "pt2", "content": "point two", "point_kind": "statement",
         "lme_session_index": 1},
    ]
    # budget 12: chunk1 ([session 0] + 4 words) + pt1 fit; the later tail
    # is truncated by the budget in rank order
    ctx = assemble_context(pool, top_k=20, max_context_tokens=12)
    assert estimate_tokens(render_context(ctx)) <= 12
    assert [h["id"] for h in ctx] == ["chunk1", "pt1"]
    # tight budget 5: the leading chunk is oversized for the budget and
    # SKIPPED (skip-not-starve); the next-ranked point fits
    ctx2 = assemble_context(pool, top_k=20, max_context_tokens=5)
    assert estimate_tokens(render_context(ctx2)) <= 5
    assert [h["id"] for h in ctx2] == ["pt1"]


def test_context_oversized_hit_skips_not_starves():
    """Skip-and-continue (R1 #1540): a mid-rank hit whose own cost exceeds
    the cap is DROPPED; later hits still append (no starvation)."""
    pool = [
        {"id": "pt1", "content": "point one", "point_kind": "statement",
         "lme_session_index": 0},
        {"id": "huge", "content": " ".join(["word"] * 5000),
         "point_kind": "statement", "lme_session_index": 0},
        {"id": "pt2", "content": "point two", "point_kind": "statement",
         "lme_session_index": 0},
    ]
    ctx = assemble_context(pool, top_k=20, max_context_tokens=100)
    ids = [h["id"] for h in ctx]
    assert "huge" not in ids
    assert "pt1" in ids and "pt2" in ids  # later hits still selected
    assert len(ctx) == 2


def test_context_reader_alignment_invariant():
    """The alignment invariant (R1 #1540): the assembly's budget accounting
    (raw whitespace words + the once-prepended date header, 1.1 markup ONCE)
    equals ``estimate_tokens(render_context(...))`` exactly — no per-block
    int drift — so a caller's ``context_tokens`` always matches what the
    reader consumed."""
    pool = [
        {"id": "chunk1", "content": "chunk number one here",
         "point_kind": "session-transcript", "lme_session_index": 0,
         "session_date": "2025-06-10"},
        {"id": "pt1", "content": "point one", "point_kind": "statement",
         "lme_session_index": 0},
        {"id": "pt2", "content": "point two", "point_kind": "statement",
         "lme_session_index": 1},
        {"id": "chunk2", "content": "chunk number two here",
         "point_kind": "session-transcript", "lme_session_index": 1,
         "session_date": "2025-06-14"},
    ]
    question_date = "2025-06-15"
    selected = assemble_context(pool, top_k=20, max_context_tokens=10**6,
                                question_date=question_date)
    text = render_context(selected, question_date=question_date)
    # the assembly selected every block under a huge budget
    assert [h["id"] for h in selected] == [h["id"] for h in pool]
    # the estimator on the rendered text reproduces the assembly's words
    # + header accounting (the exact invariant the retrieval path asserts:
    # context_tokens == estimate_tokens(render_context(...)))
    header_words = len(f"Current Date: {question_date}".split())
    block_words = sum(len(render_context([h]).split()) for h in selected)
    assert estimate_tokens(text) == int((header_words + block_words) * 1.1)
    assert text.startswith("Current Date: 2025-06-15")


def test_render_context_without_dates_is_backward_compatible():
    """render_context without a question_date renders the same blocks with
    no header (backward-compatible with pre-TR readers)."""
    hits = [
        {"id": "a", "content": "alpha", "point_kind": "statement",
         "lme_session_index": 0},
        {"id": "b", "content": "beta", "point_kind": "statement",
         "lme_session_index": 1},
    ]
    text = render_context(hits)
    assert "[session 0] alpha" in text
    assert "[session 1] beta" in text
    assert "Current Date:" not in text


def test_render_context_annotates_superseded_and_superseding_hits():
    """#1367: promoted supersession state renders [SUPERSEDED BY] /
    [SUPERSEDES] markers so a reader discounts superseded claims."""
    hits = [
        {"id": "old", "content": "old claim",
         "point_kind": "statement", "lme_session_index": 0,
         "superseded_by": {"content_snippet": "the new claim"}},
        {"id": "new", "content": "the new claim",
         "point_kind": "statement", "lme_session_index": 1,
         "supersedes": [{"content_snippet": "the old claim"}]},
        {"id": "plain", "content": "plain claim",
         "point_kind": "statement", "lme_session_index": 2},
    ]
    text = render_context(hits)
    assert "[SUPERSEDED BY: the new claim]" in text
    assert "[SUPERSEDES: the old claim]" in text
    assert text.count("[SUPERSEDED BY:") == 1
    assert text.count("[SUPERSEDES:") == 1


# ── evidence-mark boost (C2 #1745 / #1945) ────────────────────────────────


def _marks_for(*, source=(), verbatim=(), raw_chunk=(), answer_string=()):
    """A ``mark_for`` provider keyed on hit ids (the product's injectable
    mark contract: ``{"source_session", "verbatim", "raw_chunk",
    "answer_string"}`` bools)."""
    def _fn(h: dict) -> dict[str, bool]:
        i = h["id"]
        return {
            "source_session": i in source,
            "verbatim": i in verbatim,
            "raw_chunk": i in raw_chunk,
            "answer_string": i in answer_string,
        }
    return _fn


def _pool(n_fillers: int = 24, *, marked_at: int | None = 24) -> list[dict]:
    pool = [
        {"id": f"fill{i}", "content": f"unrelated filler chat {i}",
         "session_id": "s0", "point_kind": "statement",
         "lme_session_index": 0, "session_date": "2025-06-10",
         "quote": "", "has_answer": False}
        for i in range(n_fillers)
    ]
    if marked_at is not None:
        pool.append({
            "id": "evidence-pt",
            "content": "I painted the wall a light gray",
            "session_id": "s1", "point_kind": "statement",
            "lme_session_index": 1, "session_date": "2025-06-14",
            "quote": "", "has_answer": True,
        })
    return pool


def test_boost_promotes_marked_hits():
    """C2 (#1745): a marked point at pool rank 25 surfaces into the top-20
    AFTER the boost; unmarked hits at the same rank do not. The boost is a
    stable rank offset, not an RRF-score multiplier."""
    pool = _pool(24)  # evidence-pt at index 24
    assert pool[24]["id"] == "evidence-pt"
    pre = assemble_context(pool, top_k=20, max_context_tokens=10**6)
    assert "evidence-pt" not in [h["id"] for h in pre]
    # verbatim mark (full boost x1.5 — the eval fixture's quote carries the
    # answer turn) surfaces the point into top-20 from rank 25
    boosted, stats = apply_evidence_boost(
        pool, mark_for=_marks_for(verbatim={"evidence-pt"}))
    assert stats["applied"] is True
    assert stats["marks_census"]["verbatim"] == 1
    assert stats["pre_boost_ranked_ids"] == [h["id"] for h in pool]
    assert "evidence-pt" in [h["id"] for h in boosted[:20]]
    post = assemble_context(boosted, top_k=20, max_context_tokens=10**6)
    assert "evidence-pt" in [h["id"] for h in post]
    # unmarked relative order preserved: the fillers keep their order
    fill_ids = [h["id"] for h in boosted if h["id"].startswith("fill")]
    assert fill_ids == [f"fill{i}" for i in range(24)]


def test_boost_class_priority_answer_string_strongest():
    """#1945: the answer-string mark (d, #1763) is a FIRST-CLASS boost
    class — the strongest signal, so its multiplier is >= verbatim's. A
    point carrying the gold answer starting BELOW a verbatim-only point
    must outrank it post-boost."""
    assert DEFAULT_EVIDENCE_BOOST_ANSWER_STRING >= DEFAULT_EVIDENCE_BOOST_VERBATIM
    pool = [
        {"id": f"fill{i}", "content": f"unrelated filler chat {i}",
         "session_id": "s0", "point_kind": "statement",
         "lme_session_index": 0, "session_date": "2025-06-10",
         "quote": "", "has_answer": False}
        for i in range(20)
    ]
    pool.append({"id": "verbatim-pt",
                 "content": "She described the shade she used",
                 "session_id": "s1", "point_kind": "statement",
                 "lme_session_index": 1, "session_date": "2025-06-14",
                 "quote": "I painted the wall a light gray",
                 "has_answer": True})
    pool.append({"id": "answer-pt",
                 "content": "The final color was cerulean",
                 "session_id": "s1", "point_kind": "statement",
                 "lme_session_index": 1, "session_date": "2025-06-14",
                 "quote": "", "has_answer": True})
    boosted, stats = apply_evidence_boost(
        pool, mark_for=_marks_for(verbatim={"verbatim-pt"},
                                  answer_string={"answer-pt"}))
    assert stats["marks_census"]["answer_string"] == 1
    assert stats["marks_census"]["verbatim"] == 1
    assert stats["boost_answer_string"] == DEFAULT_EVIDENCE_BOOST_ANSWER_STRING
    ids = [h["id"] for h in boosted]
    a_pos, v_pos = ids.index("answer-pt"), ids.index("verbatim-pt")
    # the answer-string point started BELOW the verbatim point (21 vs 20)
    # but the stronger multiplier outranks it post-boost
    assert a_pos < v_pos
    assert a_pos < 21 and v_pos < 20
    # stronger class moves farther (bounded by the position ceiling)
    assert (21 - a_pos) > (20 - v_pos) >= 1


def test_boost_answer_string_knob_honored():
    """#1945: the answer-string boost class is knob-exposed
    (``boost_answer_string``) — a stronger multiplier moves the class
    farther (still position-ceiling bounded)."""
    pool = _pool(20, marked_at=None)
    pool.append({"id": "answer-pt",
                 "content": "The final color was cerulean",
                 "session_id": "s1", "point_kind": "statement",
                 "lme_session_index": 1, "session_date": "2025-06-14",
                 "quote": "", "has_answer": True})
    boosted, stats = apply_evidence_boost(
        pool, mark_for=_marks_for(answer_string={"answer-pt"}),
        boost_answer_string=4.0)
    assert stats["boost_answer_string"] == 4.0
    a_pos = [h["id"] for h in boosted].index("answer-pt")
    assert a_pos < 11  # 20 / 4.0 = 5 scaled priority -> strictly above x2.0


def test_boost_no_marked_point_displacement():
    """P1-1c (C2 #1745): boosted CHUNKS do not push marked POINTS out of
    top-20 — the boost is additive to evidence, not a redistribution
    between evidence classes."""
    pool = []
    for i in range(15):  # 15 source-only marked points at ranks 0..14
        pool.append({"id": f"mark-pt{i}",
                     "content": f"wall painting note {i} about the decor",
                     "session_id": "s1", "point_kind": "statement",
                     "lme_session_index": 1, "session_date": "2025-06-14",
                     "quote": "", "has_answer": True})
    for i in range(10):  # 10 verbatim-marked chunks at ranks 15..24
        pool.append({"id": f"ev-chunk{i}",
                     "content": "I painted the wall a light gray",
                     "session_id": "s1", "point_kind": "session-transcript",
                     "lme_session_index": 1, "session_date": "2025-06-14",
                     "quote": "I painted the wall a light gray",
                     "has_answer": True})
    source_ids = {f"mark-pt{i}" for i in range(15)}
    chunk_ids = {f"ev-chunk{i}" for i in range(10)}
    boosted, stats = apply_evidence_boost(
        pool, mark_for=_marks_for(source=source_ids, raw_chunk=chunk_ids))
    assert stats["marks_census"]["source_session"] == 15
    assert stats["marks_census"]["raw_chunk"] == 10
    top20 = {h["id"] for h in boosted[:20]}
    # every marked POINT stays in top-20 (boosted chunks cannot
    # redistribute evidence out of the reader's reach)
    assert all(f"mark-pt{i}" in top20 for i in range(15))
    # the verbatim chunks DID move up (the boost is not a no-op)
    assert "ev-chunk0" in top20


def test_boost_boundary_point_not_displaced():
    """Review F2 (C2 #1745) boundary fixture: 19 unmarked fillers + a
    source-marked point at index 19 (in top-20 pre-boost) + 5 verbatim
    chunks at indices 20-24 (higher factor). The old plain ascending sort
    demoted the point 19 -> 22 (out of top-20, evidence_recall@20 dropped
    1 -> 0 with the boost ON); the position-ceiling promotion must keep the
    point at a position <= 19 while STILL surfacing at least one verbatim
    chunk into top-20 (the boost is not a no-op)."""
    pool = [
        {"id": f"fill{i}", "content": f"unrelated filler chat {i}",
         "session_id": "s0", "point_kind": "statement",
         "lme_session_index": 0, "session_date": "2025-06-10",
         "quote": "", "has_answer": False}
        for i in range(19)
    ]
    pool.append({"id": "source-pt",
                 "content": "The wall painting took all day",
                 "session_id": "s1", "point_kind": "statement",
                 "lme_session_index": 1, "session_date": "2025-06-14",
                 "quote": "", "has_answer": True})
    for i in range(5):
        pool.append({"id": f"chunk{20 + i}",
                     "content": "I painted the wall a light gray",
                     "session_id": "s1", "point_kind": "session-transcript",
                     "lme_session_index": 1, "session_date": "2025-06-14",
                     "quote": "I painted the wall a light gray",
                     "has_answer": True})
    assert pool[19]["id"] == "source-pt"
    chunk_ids = {f"chunk{20 + i}" for i in range(5)}
    boosted, stats = apply_evidence_boost(
        pool, mark_for=_marks_for(source={"source-pt"}, raw_chunk=chunk_ids))
    assert stats["marks_census"]["raw_chunk"] == 5
    ids = [h["id"] for h in boosted]
    pos = ids.index("source-pt")
    # (a) the point is never demoted below its original pool index
    assert pos <= 19
    assert "source-pt" in ids[:20]
    # (b) the boost is not a no-op: at least one chunk entered top-20
    assert any(f"chunk{20 + i}" in ids[:20] for i in range(5))


def test_boost_stored_mark_fallback_source_class():
    """Review F11 (C2 #1745): with ``mark_for`` None, a STORED
    ``has_answer`` hit falls back to the SOURCE-class factor (conservative
    — never unboosted: the stored mark is still evidence the extractor
    wrote) and moves up past an identical unmarked control."""
    pool = [
        {"id": f"fill{i}", "content": f"unrelated filler chat {i}",
         "session_id": "s0", "point_kind": "statement",
         "lme_session_index": 0, "session_date": "2025-06-10",
         "quote": "", "has_answer": False}
        for i in range(20)
    ]
    pool.append({"id": "stored-pt",
                 "content": "unrelated filler chat stored",
                 "session_id": "s0", "point_kind": "statement",
                 "lme_session_index": 0, "session_date": "2025-06-10",
                 "quote": "", "has_answer": True})
    pool.append({"id": "control-pt",
                 "content": "unrelated filler chat control",
                 "session_id": "s0", "point_kind": "statement",
                 "lme_session_index": 0, "session_date": "2025-06-10",
                 "quote": "", "has_answer": False})
    boosted, stats = apply_evidence_boost(pool)  # mark_for=None (stored)
    # the product's stored-mark fallback treats the stored has_answer as
    # source-class evidence — census counts it, the factor is the reduced
    # source boost (never unboosted: the stored mark is still evidence)
    assert stats["marks_census"] == {"source_session": 1, "verbatim": 0,
                                      "raw_chunk": 0, "answer_string": 0}
    ids = [h["id"] for h in boosted]
    stored_pos, control_pos = ids.index("stored-pt"), ids.index("control-pt")
    # source-class fallback: moved up (never demoted below original index)
    # and outranks the identical unmarked control that does not move.
    assert stored_pos <= 20
    assert stored_pos < 20  # moved up, not just held
    assert control_pos == 21  # unmarked control did not move
    assert stored_pos < control_pos


def test_boost_rejects_invalid_multipliers():
    """C2 (review P1-2 + F9): a boost factor < 1.0 or NON-FINITE is
    rejected at the function boundary — 0.0 would ZeroDivide the rank
    scaling, a negative factor would silently invert the pool order, and
    NaN/Inf would poison every sort key (NaN passes the < 1.0 comparison;
    inf zeroes every key) — review F9 pins the isfinite guard."""
    pool = _pool(5)
    with pytest.raises(ValueError, match=r"must be >= 1.0"):
        apply_evidence_boost(pool, boost_verbatim=0.0)
    with pytest.raises(ValueError, match=r"must be >= 1.0"):
        apply_evidence_boost(pool, boost_source=-1.0)
    with pytest.raises(ValueError, match=r"must be >= 1.0"):
        apply_evidence_boost(pool, boost_verbatim=float("nan"))
    with pytest.raises(ValueError, match=r"must be >= 1.0"):
        apply_evidence_boost(pool, boost_source=float("inf"))
    # #1945: the answer-string class gets the same boundary guard
    with pytest.raises(ValueError, match=r"must be >= 1.0"):
        apply_evidence_boost(pool, boost_answer_string=0.0)
    with pytest.raises(ValueError, match=r"must be >= 1.0"):
        apply_evidence_boost(pool, boost_answer_string=float("nan"))


def test_boost_constants_and_estimator_documented():
    """The product-owned defaults are the eval-benchmarked numbers (the
    inversion contract: the harness measures the product's values)."""
    assert DEFAULT_CONTEXT_ITEM_CAP == 40
    assert DEFAULT_CONTEXT_TOKEN_CAP == 8000
    assert DEFAULT_EVIDENCE_BOOST_VERBATIM == 1.5
    assert DEFAULT_EVIDENCE_BOOST_SOURCE == 1.15
    assert TOKEN_ESTIMATOR == "whitespace-tokens + 10% markup allowance"


# ── retry predicate + loop (tortoise/retry.py, #1806) ─────────────────────


def test_retryable_transient_predicate_matrix():
    """Pinned predicate matrix: redis TimeoutError/ConnectionError → True;
    network OSError with transport errnos → True; HTTPError classes →
    False (checked before URLError/OSError); requests/urllib provider
    network errors → True; socket-origin builtin TimeoutError → True;
    bare builtin TimeoutError → False; MISCONF ResponseError → True,
    WRONGTYPE → False; deterministic OSErrors (ENOENT/EACCES) → False;
    parse-class → False."""
    # redis transport failures (the verified write-path loss mechanism)
    assert retryable_transient(redis_exc.TimeoutError("stall")) is True
    assert retryable_transient(redis_exc.ConnectionError("stall")) is True
    # redis ResponseError: MISCONF (AOF/disk-full write refusal) bounded-
    # retried; unrelated messages never
    assert retryable_transient(redis_exc.ResponseError(
        "MISCONF Errors writing to the AOF file: No space left on device")) is True
    assert retryable_transient(redis_exc.ResponseError(
        "MISCONF Redis is configured to save RDB snapshots, but it is "
        "currently unable to persist to disk")) is True
    assert retryable_transient(redis_exc.ResponseError(
        "WRONGTYPE Operation against a key holding the wrong kind of value")) is False

    # HTTPError classes EXCLUDED FIRST (HTTPError IS-A URLError IS-A OSError)
    for code in (401, 429, 500):
        try:
            raise urllib.error.HTTPError(
                url="http://x", code=code, msg="err", hdrs=None, fp=None)
        except urllib.error.HTTPError as e:
            assert retryable_transient(e) is False
    assert retryable_transient(requests.HTTPError("boom")) is False

    # requests/urllib provider-network errors (ingest-site transients)
    assert retryable_transient(requests.exceptions.Timeout("t")) is True
    assert retryable_transient(requests.exceptions.ConnectTimeout("t")) is True
    assert retryable_transient(requests.exceptions.ReadTimeout("t")) is True
    assert retryable_transient(requests.exceptions.ConnectionError("c")) is True
    assert retryable_transient(urllib.error.URLError("r")) is True

    # network OSError narrowed to transport errnos
    for e in (errno.ECONNRESET, errno.ETIMEDOUT, errno.EHOSTUNREACH,
              errno.ENETUNREACH, errno.EPIPE, errno.ECONNREFUSED,
              errno.ECONNABORTED, errno.ENETDOWN):
        exc = OSError(e, "net")
        assert retryable_transient(exc) is True, errno.errorcode.get(e)
    # deterministic-bug OSErrors are NEVER retried (P2-Q)
    assert retryable_transient(FileNotFoundError("no")) is False
    assert retryable_transient(PermissionError("no")) is False
    assert retryable_transient(OSError(errno.ENOENT, "no")) is False
    assert retryable_transient(OSError(errno.EACCES, "no")) is False

    # socket-origin builtin TimeoutError → True; bare local → False (P1-7)
    try:
        raise TimeoutError("op") from TimeoutError()
    except TimeoutError as e:
        assert retryable_transient(e) is True
    assert retryable_transient(TimeoutError("local deadline")) is False
    assert retryable_transient(ValueError("parse")) is False
    assert retryable_transient(KeyError("parse")) is False
    assert retryable_transient(TypeError("parse")) is False


def test_call_with_predicate_retry_loop():
    """Parse/structural/fatal errors are NEVER retried (attempts == 1,
    propagates unchanged); predicate-true transients retry and recover;
    exhaustion raises the sentinel with .original + __cause__; the
    disarmed (marker_armed=False) exhaustion re-raises the ORIGINAL
    exception unwrapped."""
    calls: list[int] = []

    def _parse_fail():
        calls.append(1)
        raise ValueError("parse")

    with pytest.raises(ValueError):
        call_with_predicate(_parse_fail, predicate=retryable_transient,
                            retries=2, what="t")
    assert len(calls) == 1  # never retried

    def _transient_once():
        calls.append(1)
        if len(calls) == 1:
            raise redis_exc.TimeoutError("stall")
        return "ok"

    assert call_with_predicate(_transient_once, predicate=retryable_transient,
                               retries=2, what="t") == "ok"
    assert len(calls) == 2

    def _always_transient():
        raise redis_exc.TimeoutError("stall")

    with pytest.raises(WriteStageRetriesExhausted) as ei:
        call_with_predicate(_always_transient, predicate=retryable_transient,
                            retries=1, what="t")
    sentinel = ei.value
    assert isinstance(sentinel.original, redis_exc.TimeoutError)
    assert sentinel.__cause__ is sentinel.original

    # exhaustion burns exactly retries+1 attempts; on_retry fires per retry
    calls = []
    retried = []

    def _always_transient2():
        calls.append(1)
        raise redis_exc.TimeoutError("stall")

    with pytest.raises(WriteStageRetriesExhausted):
        call_with_predicate(
            _always_transient2, predicate=retryable_transient,
            retries=2, what="t", on_retry=lambda e: retried.append(e))
    assert len(calls) == 3  # 1 initial + 2 retries
    assert len(retried) == 2  # on_retry fired before each retry sleep

    # marker_armed=False (resume re-attempt): the exhausted re-raise is the
    # ORIGINAL exception, unwrapped — no sentinel, no R2 marker.
    with pytest.raises(redis_exc.TimeoutError):
        call_with_predicate(_always_transient, predicate=retryable_transient,
                            retries=1, what="t", marker_armed=False)


def test_retry_import_identity():
    """The eval harness re-exports the SAME product objects (the inversion
    contract: no parallel eval copy)."""
    from tools.longmem_eval import errors as eval_errors
    assert eval_errors.retryable_transient is retryable_transient
    assert eval_errors.call_with_predicate is call_with_predicate
    assert eval_errors.WriteStageRetriesExhausted is WriteStageRetriesExhausted
    from tools.longmem_eval import retrieve as eval_retrieve
    assert eval_retrieve._apply_evidence_boost is apply_evidence_boost
    assert eval_retrieve._assemble_context is assemble_context
    assert eval_retrieve._dedup_pool is dedup_pool
    assert eval_retrieve._estimate_tokens is estimate_tokens
    assert eval_retrieve._is_raw_chunk is is_raw_chunk
    assert eval_retrieve.render_context is render_context
    assert eval_retrieve.DEFAULT_POOL_SIZE is DEFAULT_POOL_SIZE
