"""#7704 — RRF must not turn a leg's TIE into a relevance difference (FIXED; pinned).

``rrf_fusion`` consumed the row's **position** (``1/(k + rank + 1)``), so when a
leg scored two documents identically, the *leg's own* tie order became a fusion
**relevance** difference between documents whose evidence is identical. On the
search legs that tie order is the leg's secondary sort on its own id field
(#3019) — ``url`` for source/document, ``eventId`` for events, and for points
``id``: a ULID on the create paths (chronological) but ``pt_<sha256>`` on the
extraction/commit paths (``commit_schema.point_content_id``), where the order is
content-hash and the two id spaces sort as blocks.

Measured on ``origin/main`` before the fix (at ``248940bf1``; the same quantity
the issue body measured at ``38c3b6ea5``)::

    rrf_fusion([[("b", 1.0), ("a", 1.0)], [("b", 1.0), ("a", 1.0)]],
               strategy_names=["fts", "vector"], weights={"vector": 1.5})
    # BEFORE -> {'b': 0.04098360655737705, 'a': 0.04032258064516129}  1.61% apart
    # NOW    -> {'a': 0.04065040650406504, 'b': 0.04065040650406504}  equal

and end-to-end on the W4-b twin fixture (embedded lane, dense leg LIVE), where
the content-identical twins tie on **both** legs::

    fts 1.0 == 1.0 ; vector 0.4121811... == 0.4121811...   (tie on every leg)
    BEFORE  fused rrf hot 0.03993856 vs calm 0.04058699  -> final 0.8936 vs 0.8950
    NOW     fused rrf hot 0.04026016 == calm 0.04026016 -> final +0.0175 = the boost

The fuse now ranks by the leg's own SCORE with ties AVERAGED (``RANK.AVG``), so
identical evidence yields an identical fused contribution and the post-fusion
W4-b contested boost (#2102) is the sole differentiator — it survives fusion
because there is no longer a manufactured gap to clear. **The boost's magnitude,
the clamp, and the #2102 E2E-1 wording are NOT touched here** (options 2a/2b/B on
#7704 stay the owner's); neither is #3019 (the legs still sort
``score DESC, id ASC`` and the fused output is still deterministic) nor #4210
(this is still weighted RRF — no score-based fusion was adopted).

What the tests below hold:

* ``test_identical_leg_scores_must_not_produce_different_fused_scores`` — the
  invariant. **Mutation that reds it: consume the position again**
  (``position_is_rank=True``, or enumerate the leg) → 0.0409836 vs 0.0403226.
* three guards the fix must not overshoot (a tie group does not displace a
  lower-ranked row, a leg that DID rank the rows apart is respected, a generator
  leg is not dropped) and one for the escape hatch itself
  (``position_is_rank`` keeps a position list byte-identical).

⛔ ``position_is_rank`` is not a licence to reintroduce the gap: it exists for a
leg that carries NO score signal, where position IS the rank — ``temporal_leg``
fuses ``[(id, 0.0), …]`` by design (#2976) and its arithmetic is byte-identical
either way. Two assertions elsewhere measured the pre-fix artifact and were
re-derived with the fix, not relaxed: ``test_ranking_w4b.py``'s page-bound test
(the level condition is now ZERO for equal-evidence twins, so the boost's
magnitude and the clamp no longer gate it) and ``test_bm25_lexical_8488.py``'s
read-path positional climb (which was the same phantom spread; the lexical
SCORE assertion beside it is untouched).
"""
from __future__ import annotations

from tortoise.search_engine import rrf_fusion

#: The recorded production balance the measured gap is produced under
#: (#1657: vector 1.5×, k=60) — the same weights the SDK passes.
_WEIGHTS = {"vector": 1.5}
_STRATEGIES = ["fts", "vector"]


def test_identical_leg_scores_must_not_produce_different_fused_scores():
    """Two documents identical on EVERY leg ⇒ identical fused relevance.

    Mutation: consume the leg position again (``position_is_rank=True``, or the
    pre-fix ``enumerate()``) → THIS test reds with 0.0409836 vs 0.0403226, a
    1.61% relative gap carrying zero evidence.
    """
    fused = rrf_fusion(
        [[("b", 1.0), ("a", 1.0)], [("b", 1.0), ("a", 1.0)]],
        strategy_names=_STRATEGIES,
        weights=_WEIGHTS,
    )
    assert fused["a"] == fused["b"], (
        "two rows the legs scored identically were given different fused "
        f"relevance: {fused}")


def test_a_positional_leg_keeps_position_as_its_rank():
    """``position_is_rank`` is the one escape hatch — and it must stay exact.

    A leg that carries no score signal (``temporal_leg`` fuses ``[(id, 0.0),
    …]``) means "the position is the rank" (#2976). Averaging that block would
    flatten the whole leg to one rank and hand its order to the id sort, which
    is the class of regression the temporal-leg suite guards; this is the unit
    form, and the mutation that reds it is dropping the flag at the temporal
    leg's call site (measured: ``test_temporal_leg_2976`` reds 3 tests then).
    """
    leg = [(pid, 0.0) for pid in ("p0", "p1", "p2")]
    positional = rrf_fusion([leg], strategy_names=["temporal"],
                            position_is_rank=True)
    assert list(positional) == ["p0", "p1", "p2"], positional
    assert positional["p0"] > positional["p1"] > positional["p2"], positional
    # …and the default (no flag) is the equal-evidence invariant, which is why
    # the flag has to be declared rather than inferred from constant scores.
    tied = rrf_fusion([leg], strategy_names=["temporal"])
    assert len(set(tied.values())) == 1, tied


def test_a_tie_group_does_not_displace_a_document_the_legs_ranked_lower():
    """A tie spreads only INSIDE the tied group — never past a lower row.

    True today and required of any remedy: ``z`` is scored below both tied rows
    on both legs, so it must stay last whether the tie is averaged, broken by
    id, or left alone. This is the half of the defect a fix must not overshoot.
    """
    fused = rrf_fusion(
        [[("b", 1.0), ("a", 1.0), ("z", 0.0)],
         [("b", 1.0), ("a", 1.0), ("z", 0.0)]],
        strategy_names=_STRATEGIES,
        weights=_WEIGHTS,
    )
    assert fused["z"] < min(fused["a"], fused["b"]), (
        f"a lower-ranked row overtook the tied pair: {fused}")
    assert list(fused)[-1] == "z", f"z must stay last: {list(fused)}"


def test_a_distinct_leg_order_is_still_respected():
    """A leg that DID distinguish the rows keeps its order (no over-flattening).

    Guards the opposite failure from the xfail pin: a remedy that collapses
    every leg to one block would satisfy 'identical evidence, identical score'
    while destroying ranking altogether.
    """
    fused = rrf_fusion(
        [[("top", 2.0), ("mid", 1.0), ("low", 0.0)]],
        strategy_names=["fts"],
        weights=None,
    )
    assert list(fused) == ["top", "mid", "low"], fused
    assert fused["top"] > fused["mid"] > fused["low"], fused


def test_a_generator_leg_is_not_dropped():
    """A leg may be an iterator; consuming it twice must not return ``{}``.

    The shipped loop enumerates a leg in ONE pass, so an iterator works today.
    Any remedy that materialises the leg to read its scores must materialise it
    ONCE — indexing an iterator and then zipping the same iterator returns an
    empty result, which is total, silent result loss.
    """
    gen = ((pid, sc) for pid, sc in [("b", 2.0), ("a", 1.0), ("z", 0.0)])
    fused = rrf_fusion([gen], strategy_names=["fts"], weights=None)
    assert list(fused) == ["b", "a", "z"], fused
