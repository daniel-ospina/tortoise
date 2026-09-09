"""#2683 (Slice A, epic #2080) — evidence-package assembly hermetic tests.

HERMETIC tests (no graph, no model, no IO): the product pure function
``tortoise/retrieval.py::package_evidence_pool`` collapses a distilled
point's OWN source raw chunks/turns into ONE package entry (point + ≤ one
verbatim ref), dedups cross-item near-dupe statements restating the same
fact to one slot, and orders the surviving packages value/verbatim-marked
first with relevance order preserved within a tier. The docker-lane half
(the eval arm markers on ``retrieve_for_question`` outcomes + the SDK ask
lane knob default) is exercised by the existing docker suites
(test_retrieval.py / test_eval_* / test_ask_retrieval_levers.py) — this
file runs the IDENTICAL pure logic offline (the single-source-of-truth
split that evidence.py establishes for the M6 marks).

Design contract under test (docs/scoping/2026-09-09-evidence-assembly-wave.md
§5 Slice A):

  * (a) one-fact-one-slot — a point + its source chunks/turns (content
        containing the anchored quote, same session, or the recorded
        ``source_turn_id``) collapse to ≤ 1 + max_verbatim entries,
  * (b) cross-item near-dupe dedup — two statements restating the SAME
        fact (exact / ≥0.9 near-verbatim content, or same source turn +
        overlapping quotes) dedupe to one slot BEFORE the window fill,
  * (c) ordering — exact-value/verbatim-marked packages first, relevance
        (pool order) preserved within a tier,
  * (d) OFF parity — the eval retrieval seam adds the arm as a tri-state
        kwarg defaulting to None (env-gated fail-safe OFF) and the ask
        lane defaults the knob OFF; the OFF path never invokes packaging
        (byte-identical),
  * (e) no-dupe no-regression — on a duplicate-free pool with no value
        marks the ON path returns the input BYTE-IDENTICAL (same ids,
        same order) — flipping the arm ON cannot regress single-evidence
        questions,
  * value safety — a same-frame DIFFERENT-VALUE claim ("cost 300" vs
        "cost 400") never collapses (the aggregation numerator Slice B
        protects),
  * the package is a SUBSET of the pool (no synthesis) and the pool
        recall surface is unchanged by construction (the caller measures
        recall over the pool; packaging only shapes the reader window).
"""
from __future__ import annotations

import inspect

import pytest

from tortoise.retrieval import (
    DEFAULT_PACKAGE_MAX_VERBATIM,
    DEFAULT_PACKAGE_SAME_TURN_OVERLAP,
    DEFAULT_PACKAGE_VERBATIM_OVERLAP,
    _pkg_norm,
    _pkg_tokens,
    ask_env_bool,
    package_evidence_pool,
)

# ── fixture helpers (the annotated pool hit shapes the eval/ask feed) ─────


def _point(pid: str, content: str, *, session: str = "s0",
           quote: str = "", source_turn: str | None = None,
           date: str = "2025-06-10", kind: str = "statement") -> dict:
    """A distilled/statement point (the eval's EXTRACTION_POINT_KIND)."""
    h = {
        "id": pid, "content": content, "point_kind": kind,
        "session_id": session, "session_date": date, "quote": quote,
        "source_turn_id": source_turn or "", "has_answer": False,
    }
    return h


def _chunk(cid: str, lines: list[str], *, session: str = "s0") -> dict:
    """A raw transcript chunk (pointKind session-transcript) — its content
    holds the windowed verbatim turns as ``Role: …`` lines."""
    return {
        "id": cid, "point_kind": "session-transcript",
        "content": "\n".join(lines), "session_id": session,
        "session_date": "2025-06-10",
    }


def _turn(tid: str, role: str, text: str, *, session: str = "s0") -> dict:
    """A source turn point (kind event, ``[role] text`` content — the shape
    the deterministic leg writes turns as)."""
    return {
        "id": tid, "point_kind": "event", "content": f"[{role}] {text}",
        "session_id": session, "session_date": "2025-06-10",
    }


def _marks_for(*, source=(), verbatim=(), raw_chunk=(), answer_string=()):
    """A mark provider keyed on hit ids (the product's injectable mark
    contract: source_session / verbatim / raw_chunk / answer_string)."""
    def _fn(h: dict) -> dict[str, bool]:
        i = h["id"]
        return {
            "source_session": i in source,
            "verbatim": i in verbatim,
            "raw_chunk": i in raw_chunk,
            "answer_string": i in answer_string,
        }
    return _fn


# ── (a) one-fact-one-slot: point + own source chunks/turns collapse ───────

def test_point_collapses_its_own_source_chunks_and_turns_to_one_slot():
    """A distilled point + its own raw chunks (content containing the quote)
    + its source turns (id == source_turn_id / content containing the quote)
    collapse into ONE package — the point + at most ONE verbatim source ref
    (2 window entries for 6 pool items)."""
    quote = "I bought the tea set from cousin Rachel for 300 dollars"
    pool = [
        _point("pt:1",
               "I bought the tea set from cousin Rachel for 300 dollars on "
               "the trip to the market",
               quote=quote, source_turn="lme:0:t5"),
        # two raw chunks whose windowed transcript CONTAINS the quote
        _chunk("lme:0:c2", ["User: So I bought the tea set from cousin "
                            "Rachel for 300 dollars yesterday",
                            "Assistant: That sounds like a good deal."]),
        _chunk("lme:0:c3", ["User: I bought the tea set from cousin Rachel "
                            "for 300 dollars, yeah",
                            "Assistant: Nice."]),
        # the recorded source turn (id == source_turn_id)
        _turn("lme:0:t5", "user", quote),
        # another turn containing the quote (same session)
        _turn("lme:0:t6", "user", "Yes, I bought the tea set from cousin "
                                  "Rachel for 300 dollars."),
    ]
    packaged, stats = package_evidence_pool(pool)
    assert [h["id"] for h in packaged] == ["pt:1", "lme:0:c2"]
    assert stats["pool_items"] == 5
    assert stats["package_items"] == 2
    assert stats["packages"] == 1
    assert stats["collapsed_duplicates"] == 3   # c3 + t5 + t6 dropped
    assert stats["verbatim_refs_kept"] == 1     # c2 kept as the one ref
    # each package renders only its own pool hits — no synthesized content
    for h in packaged:
        assert h["content"]


def test_point_with_no_quote_never_collapses_sources():
    """A point WITHOUT a verbatim quote has no provenance to collapse onto
    (no anchored text to contain) — its same-session chunks stay standalone
    (own-source collapse is quote-gated, deterministic)."""
    pool = [
        _point("pt:1", "cousin Rachel sold me a tea set", quote=""),
        _chunk("lme:0:c2", ["User: cousin Rachel sold me a tea set"]),
    ]
    packaged, stats = package_evidence_pool(pool)
    assert len(packaged) == 2
    assert stats["packages"] == 2
    assert stats["collapsed_duplicates"] == 0


def test_own_source_collapse_is_session_scoped():
    """A chunk from a DIFFERENT session is never a point's own source — the
    quote-containment leg requires the same session bucket (two sessions can
    restate the same fact; each keeps its own verbatim evidence)."""
    quote = "the trip to the lake cost 200 dollars"
    pool = [
        _point("pt:1", "I paid for the trip to the lake, 200 dollars total",
               quote=quote, source_turn="lme:0:t3", session="sA"),
        # same quote text, but in session B (a distinct memory)
        _chunk("lme:1:c1", ["User: the trip to the lake cost 200 dollars"],
               session="sB"),
    ]
    packaged, stats = package_evidence_pool(pool)
    assert len(packaged) == 2
    assert stats["packages"] == 2
    assert stats["collapsed_duplicates"] == 0


# ── (b) cross-item near-dupe dedup (before the window fill) ───────────────

def test_two_points_restating_same_fact_collapse_to_one_slot():
    """Two distilled points restating the SAME fact (one an exact restatement,
    one a near-verbatim padded restatement) dedupe to ONE slot — the earlier
    (higher-relevance) anchor survives."""
    pool = [
        _point("pt:1",
               "cousin Rachel sold us the tea set for 300 dollars"),
        _point("pt:2",
               "cousin Rachel sold us the tea set for 300 dollars, and it "
               "was a bargain"),
    ]
    packaged, stats = package_evidence_pool(pool)
    assert [h["id"] for h in packaged] == ["pt:1"]
    assert stats["collapsed_duplicates"] == 1


def test_same_source_turn_overlapping_quotes_collapse():
    """Two extractions of ONE utterance — the same source turn + overlapping
    verbatim quotes — are duplicate extractions and collapse (the same quote
    span means the same fact, even when the surrounding content differs)."""
    pool = [
        _point("pt:1",
               "the tea set ended up costing three hundred dollars",
               quote="cost 300 dollars", source_turn="lme:0:t5"),
        _point("pt:2",
               "cousin Rachel said the tea set would be around three hundred",
               quote="the tea set cost 300 dollars", source_turn="lme:0:t5"),
    ]
    packaged, stats = package_evidence_pool(pool)
    assert [h["id"] for h in packaged] == ["pt:1"]
    assert stats["collapsed_duplicates"] == 1


def test_same_frame_different_value_claims_never_collapse():
    """Value safety: two same-frame DIFFERENT-VALUE claims ("cost 300" vs
    "cost 400" — the aggregation numerator Slice B protects) share ~0.875
    content and carry distinct quotes → they survive as their own slots."""
    pool = [
        _point("pt:1", "the tea set cost 300 dollars",
               quote="cost 300 dollars", source_turn="lme:0:t5"),
        _point("pt:2", "the tea set cost 400 dollars",
               quote="cost 400 dollars", source_turn="lme:0:t5"),
    ]
    packaged, stats = package_evidence_pool(pool)
    assert [h["id"] for h in packaged] == ["pt:1", "pt:2"]
    assert stats["collapsed_duplicates"] == 0
    # the same-frame boundary is pinned ABOVE the same-turn collapse floor
    # (0.875 < 0.9) and BELOW it on quotes (2/3 tokens ≈ 0.667 < 0.75) —
    # the thresholds are what keep different values distinct.
    assert DEFAULT_PACKAGE_VERBATIM_OVERLAP == 0.9
    assert DEFAULT_PACKAGE_SAME_TURN_OVERLAP == 0.75


def test_cross_session_near_verbatim_restatement_collapses():
    """Session-agnostic content collapse: two sessions restating the SAME
    claim near-word-for-word are duplicates of ONE fact for the reader
    window (the window is the scarce resource); exact content equality is
    always the same fact across sessions."""
    pool = [
        _point("pt:1", "I will paint the wall a light gray",
               session="sA"),
        _point("pt:2", "I will paint the wall a light gray, I decided",
               session="sB"),
    ]
    packaged, stats = package_evidence_pool(pool)
    assert [h["id"] for h in packaged] == ["pt:1"]
    assert stats["collapsed_duplicates"] == 1


def test_standalone_duplicate_chunk_content_dedups_to_container():
    """Pass-2 standalone verbatim dedup: a raw chunk whose verbatim text is
    CONTAINED in a later, longer chunk (overlapping windows) dedups — the
    CONTAINER (longest verbatim text) wins regardless of pool arrival order,
    so a contained turn never starves the reader of context."""
    pool = [
        _turn("lme:0:t4", "user",
              "I bought the tea set from cousin Rachel"),
        _chunk("lme:0:c7", ["User: I bought the tea set from cousin Rachel "
                            "for 300 dollars",
                            "Assistant: Great choice."]),
    ]
    packaged, stats = package_evidence_pool(pool)
    assert [h["id"] for h in packaged] == ["lme:0:c7"]
    assert stats["collapsed_duplicates"] == 1


# ── (c) ordering: value/verbatim-marked packages first ────────────────────

def test_verbatim_marked_point_leads_window_over_higher_ranked_unmarked():
    """A verbatim-marked point ranks into the window FIRST even when it sits
    BELOW an unmarked point in pool order (the #1763/#1945 precise classes
    lead; relevance order preserved within each tier)."""
    pool = [
        _point("pt:unmarked", "some unrelated filler about cooking pasta"),
        _point("pt:verbatim", "I bought the tea set from cousin Rachel for "
                              "300 dollars"),
    ]
    packaged, _ = package_evidence_pool(
        pool, mark_for=_marks_for(verbatim={"pt:verbatim"}))
    assert [h["id"] for h in packaged] == ["pt:verbatim", "pt:unmarked"]


def test_answer_string_class_orders_within_marked_tier():
    """Marked packages (all value tiers) keep pool relevance order between
    themselves; unmarked packages follow as a second tier."""
    pool = [
        _point("pt:low-verbatim", "the tea set from cousin Rachel cost 300",
               quote="cost 300 dollars"),
        _point("pt:unmarked", "filler about the garden fence"),
        _point("pt:answer", "cousin Rachel sold me the tea set for three "
                            "hundred dollars"),
    ]
    packaged, _ = package_evidence_pool(
        pool, mark_for=_marks_for(verbatim={"pt:low-verbatim"},
                                  answer_string={"pt:answer"}))
    assert [h["id"] for h in packaged] == [
        "pt:low-verbatim", "pt:answer", "pt:unmarked"]


def test_source_chunk_mark_orders_chunk_above_unmarked_point():
    """A raw-chunk-marked package leads the window (the eval marks raw
    chunks with ``raw_chunk`` — the #1945 precise source class)."""
    pool = [
        _point("pt:1", "unrelated filler"),
        _chunk("lme:0:c9", ["User: I bought the tea set from cousin "
                            "Rachel for 300 dollars"]),
    ]
    packaged, _ = package_evidence_pool(
        pool, mark_for=_marks_for(raw_chunk={"lme:0:c9"}))
    assert [h["id"] for h in packaged] == ["lme:0:c9", "pt:1"]


# ── (d) OFF parity: tri-state fail-safe OFF on both seams ─────────────────

def test_eval_retrieval_seam_off_by_default_signature():
    """The eval retrieval seam (``retrieve_for_question``) adds the arm as a
    tri-state kwarg defaulting to None (env-gated, fail-safe OFF) — the
    off-path never calls packaging (byte-identical default; the docker
    suites prove equality over a live graph)."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    sig = inspect.signature(retrieve_for_question)
    assert sig.parameters["evidence_assembly"].default is None


def test_ask_lane_knob_failsafe_off_by_default():
    """The SDK ask lane reads ``TORTOISE_ASK_EVIDENCE_ASSEMBLY`` with a
    False default (fail-safe) — unset/garbage → OFF, only explicit truthy
    arms; a typo never flips the knob."""
    import os
    for val in (None, "", "garbage", "0", "false"):
        if val is None:
            os.environ.pop("TORTOISE_ASK_EVIDENCE_ASSEMBLY", None)
        else:
            os.environ["TORTOISE_ASK_EVIDENCE_ASSEMBLY"] = val
        try:
            assert ask_env_bool("TORTOISE_ASK_EVIDENCE_ASSEMBLY",
                                False) is False
        finally:
            os.environ.pop("TORTOISE_ASK_EVIDENCE_ASSEMBLY", None)
    os.environ["TORTOISE_ASK_EVIDENCE_ASSEMBLY"] = "1"
    try:
        assert ask_env_bool("TORTOISE_ASK_EVIDENCE_ASSEMBLY", False) is True
    finally:
        os.environ.pop("TORTOISE_ASK_EVIDENCE_ASSEMBLY", None)


# ── (e) no-dupe no-regression: the ON path is byte-identical when there is
#      nothing to package ──────────────────────────────────────────────────

def test_no_duplicate_no_value_marks_returns_byte_identical_pool():
    """A duplicate-free pool with no value marks passes through BYTE-
    IDENTICAL (same ids, same order, same dicts) — flipping the arm ON
    cannot regress single-evidence questions (the hermetic no-regression
    proof that would license a default-ON decision)."""
    pool = [
        _point("pt:1", "the road bike repairs cost 300 dollars",
               session="sA", date="2025-06-01"),
        _point("pt:2", "cousin Rachel sold me a tea set for 400 dollars",
               session="sB", date="2025-06-03"),
        _point("pt:3", "the fence needs repainting this summer",
               session="sC", date="2025-06-05"),
        _chunk("lme:1:c2", ["User: the road bike repairs cost 300 dollars"],
               session="sA"),
        _chunk("lme:2:c2", ["User: cousin Rachel sold me a tea set"],
               session="sB"),
    ]
    packaged, stats = package_evidence_pool(pool, mark_for=None)
    assert [h["id"] for h in packaged] == [h["id"] for h in pool]
    assert packaged == pool
    assert stats["collapsed_duplicates"] == 0
    assert stats["verbatim_refs_kept"] == 0
    assert stats["package_items"] == stats["pool_items"]


def test_membership_is_a_subset_of_the_pool():
    """Semantics contract: every packaged hit is a POOL hit (identity +
    content) — the package is a dedup/order of the pool, never a synthesis;
    a caller's pool dicts are never mutated (deep-equal preserved)."""
    pool = [
        _point("pt:1", "cousin Rachel sold us the tea set for 300 dollars"),
        _point("pt:2", "cousin Rachel sold us the tea set for 300 dollars, "
                       "and it was a bargain"),
        _chunk("lme:0:c2", ["User: I bought the tea set from cousin Rachel "
                            "for 300 dollars"]),
    ]
    # pt:2 collapses into pt:1's package; the chunk is NOT pt:1's own source
    # (no quote on pt:1) → standalone.
    packaged, _ = package_evidence_pool(pool, mark_for=None)
    by_id = {h["id"]: h for h in pool}
    for h in packaged:
        assert h["id"] in by_id
        assert h["content"] == by_id[h["id"]]["content"]
    assert [h["id"] for h in packaged] == ["pt:1", "lme:0:c2"]


# ── boundary + documented constants ───────────────────────────────────────

def test_max_verbatim_zero_keeps_point_only():
    """max_verbatim=0 keeps the point WITHOUT any source ref (the scope's
    tightest one-slot bound); the ref is dropped, never lost from the pool
    recall surface (recall is measured over the pool by the caller)."""
    quote = "the trip to the lake cost 200 dollars"
    pool = [
        _point("pt:1", "the lake trip cost 200 dollars", quote=quote,
               source_turn="lme:0:t3"),
        _turn("lme:0:t3", "user", quote),
    ]
    packaged, stats = package_evidence_pool(pool, max_verbatim=0)
    assert [h["id"] for h in packaged] == ["pt:1"]
    assert stats["verbatim_refs_kept"] == 0
    assert stats["collapsed_duplicates"] == 1


def test_max_verbatim_rejects_negative():
    with pytest.raises(ValueError):
        package_evidence_pool([], max_verbatim=-1)


def test_package_bounds_documented():
    assert DEFAULT_PACKAGE_MAX_VERBATIM == 1
    # near-verbatim floor above the same-frame different-value boundary
    assert DEFAULT_PACKAGE_VERBATIM_OVERLAP > DEFAULT_PACKAGE_SAME_TURN_OVERLAP


# ── P1/P2 #2687 review regressions: value-safety at PRODUCTION LENGTH ──────

_LONG_300 = (
    "cousin rachel offered the antique tea set for 300 dollars and i thought "
    "that was a fair price for such a nice set of china passed down from "
    "my grandmother")
_LONG_400 = (
    "cousin rachel offered the antique tea set for 400 dollars and i thought "
    "that was a fair price for such a nice set of china passed down from "
    "my grandmother")


def test_production_length_different_values_never_collapse():
    """P1 (#2687 review) regression: on PRODUCTION-length frames (20-35
    tokens) the content-overlap ratio alone merges 300 vs 400 (the ratio
    guard's (n-1)/n math only protects toy frames). The fact-critical
    differing-token guard must refuse the merge regardless of length."""
    pool = [
        _point("pt:1", _LONG_300, quote="300 dollars"),
        _point("pt:2", _LONG_400, quote="400 dollars"),
    ]
    packaged, stats = package_evidence_pool(pool)
    assert [h["id"] for h in packaged] == ["pt:1", "pt:2"]
    assert stats["collapsed_duplicates"] == 0


def test_currency_symbols_are_semantic_content():
    """P2 (#2687 review): £300, $300 and 300 are DIFFERENT amounts — the
    punctuation strip must not erase the currency symbol into byte-equal
    tokens."""
    assert _pkg_norm("charged 300 dollars") != _pkg_norm("charged £300")
    assert _pkg_norm("charged $300") != _pkg_norm("charged £300")
    pool = [
        _point("pt:1", "the shop charged $300 for the set"),
        _point("pt:2", "the shop charged £300 for the set"),
    ]
    packaged, stats = package_evidence_pool(pool)
    assert [h["id"] for h in packaged] == ["pt:1", "pt:2"]
    assert stats["collapsed_duplicates"] == 0


def test_negation_is_not_stripped_from_facts():
    """P2 (#2687 review): "did not cost 300" is the NEGATION of "cost 300"
    — a belief and its negation must never share one slot (the stopword set
    must not erase not/no)."""
    assert "not" in _pkg_tokens("the set did not cost 300 dollars")
    pool = [
        _point("pt:1", "the tea set cost 300 dollars and i was happy"),
        _point("pt:2", "the tea set did not cost 300 dollars we paid less"),
    ]
    packaged, stats = package_evidence_pool(pool)
    assert [h["id"] for h in packaged] == ["pt:1", "pt:2"]
    assert stats["collapsed_duplicates"] == 0


def test_same_turn_quotes_differing_in_value_never_collapse():
    """P1 (#2687 review): the same-source-turn quote leg must also refuse a
    value flip — two quotes from one turn that differ in the amount are
    different claims even when the spans overlap ≥ 0.75."""
    pool = [
        _point("pt:1", "she offered the set for 300", quote="set for 300",
               source_turn="lme:0:t9"),
        _point("pt:2", "she offered the set for 400", quote="set for 400",
               source_turn="lme:0:t9"),
    ]
    packaged, stats = package_evidence_pool(pool)
    assert [h["id"] for h in packaged] == ["pt:1", "pt:2"]
    assert stats["collapsed_duplicates"] == 0


def test_unit_words_differing_never_collapse():
    """P1 (#2687 review): same frame differing in the UNIT ("5 dollars" vs
    "5 euros") is a different fact."""
    pool = [
        _point("pt:1", "the coffee cost 5 dollars each at the shop"),
        _point("pt:2", "the coffee cost 5 euros each at the shop"),
    ]
    packaged, stats = package_evidence_pool(pool)
    assert [h["id"] for h in packaged] == ["pt:1", "pt:2"]
    assert stats["collapsed_duplicates"] == 0
