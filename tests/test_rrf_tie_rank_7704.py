"""#7704 — RRF turns a leg's TIE into a relevance difference (MEASURED; ruling OPEN).

``rrf_fusion`` ranks **positionally** (``1/(k + rank + 1)``), so when a leg
scores two documents identically, the *leg's own* tie order becomes a fusion
**relevance** difference between documents whose evidence is identical. On the
search legs that tie order is the leg's secondary sort on its own id field
(#3019) — ``url`` for source/document, ``eventId`` for events, and for points
``id``: a ULID on the create paths (chronological) but ``pt_<sha256>`` on the
extraction/commit paths (``commit_schema.point_content_id``), where the order is
content-hash and the two id spaces sort as blocks.

Measured on ``origin/main`` at ``248940bf1`` (this file's red run; the same
quantity the issue body measured at ``38c3b6ea5``)::

    rrf_fusion([[("b", 1.0), ("a", 1.0)], [("b", 1.0), ("a", 1.0)]],
               strategy_names=["fts", "vector"], weights={"vector": 1.5})
    # -> {'b': 0.04098360655737705, 'a': 0.04032258064516129}   1.61% apart

and end-to-end on the W4-b twin fixture (embedded lane, dense leg LIVE,
``HF_HUB_OFFLINE=1``), where the content-identical twins tie on **both** legs::

    fts 1.0 == 1.0 ; vector 0.4121811... == 0.4121811...   (tie on every leg)
    fused rrf   hot 0.03993856  vs  calm 0.04058699        (id tie-break)
    final       hot 0.8936      vs  calm 0.8950            calm first

so on every page this fixture exercised (``limit`` 3, 4, 10, 40) the contested
twin loses to its uncontested twin because of the tie-break, not because of the
contested signal. That is a page-scoped OUTCOME, not a property of the
mechanism above: the fused gap becomes a final-score gap only through
``GraphRanker``'s min-max normalization of the PAGE, so whether the boost
clears it depends on the caller's ``limit`` (the page-scoped accounting is the
``W4_CONTESTED_BOOST`` rationale in ``tortoise/ranking.py``; this file pins the
fusion-level cause, not a page outcome). The **single-leg** path does not fuse
(``sdk.py``'s ``len(raw_results) == 1`` shortcut), so tied rows keep the same
score there — the two paths disagree about identical evidence.

⛔ **What this file is, and is NOT.** The defect is measured; *what to do about
it* is an OPEN owner ruling on #7704. The recorded option set is the rationale
on ``W4_CONTESTED_BOOST`` (``tortoise/ranking.py``) — do not re-list it here:
two of its members decide OTHER questions and are NOT #7704 choices, i.e.
replacing rank-based fusion with score-based fusion is the OPEN OWNER RULING on
#4210, and tie-breaking on the contested signal reverses #3019's id tie-break
that ``temporal_leg`` consumes positionally (#2976). Nothing here decides any
of it: the pin below is ``xfail(strict=True, raises=AssertionError)`` so the
shipped behaviour is EXECUTABLE and cannot drift silently, while the suite stays
green.

When the ruling lands, this file is the switch that forces the follow-through:

* **tie-averaged ranks (or any fix)** — the xfail test XPASSes, ``strict=True``
  reds the build, and whoever lands the fix removes the marker. The three green
  guards below then hold the fix to its invariants (a generator leg is not
  swallowed, a leg that DID rank the rows apart is still respected, and a tie
  group does not displace a row the legs ranked lower).

  ⛔ **The blast radius is larger than this file — MEASURED, not guessed.** Run
  against a rank-averaging prototype of ``rrf_fusion``, this area's suite reds in
  exactly five places, one of which is a real contract and not a stale assertion:

  * ``tests/test_temporal_leg_2976.py::test_empty_temporal_leg_leaves_rrf_order_byte_identical``,
    ``::test_zero_weight_temporal_leg_is_a_no_op_on_membership_and_order`` and
    ``::test_placement_head_is_the_counterfactual_and_tail_is_the_default`` —
    ``temporal_leg`` fuses ``[(id, 0.0), …]`` and encodes rank by LIST POSITION
    (#2976), so score-equality tie detection collapses that leg to one block and
    the order falls through to the id sort. **The constraint, not just the red:**
    a tie-averaged remedy MUST leave a leg's GIVEN order untouched when an equal
    score means "position is the rank" — repair the temporal leg first, not after.
  * ``tests/test_bm25_lexical_8488.py::TestReadPath::test_bm25_arm_lifts_the_record_in_the_shipped_read``
    — its "strict rank improvement" is partly the phantom spread among four
    dense-tied rows (see #7704's 2026-10-20 round); re-derive that assertion from
    the lexical leg's own rank/score.
  * ``tests/test_ranking_w4b.py::test_integration_boost_requirement_is_bounded_by_the_page``
    — its ``{0.0, 1.0}`` similarity span (contested twin at ``0``) assumes the id
    tie-break hands the lower-ULID twin rank 0; with averaged ranks
    ``_min_max_normalize`` returns the all-equal midpoint ``0.5`` for both.
    NOTE: the first assertion to fail there is the BARE ``== {0.0, 1.0}`` check,
    which carries no message at all; the "FIXTURE PREMISE BROKEN … regenerate the
    fixture" text sits on the NEXT assertion and is never reached.

  **Do not relax any of them.** Which of these the remedy diff actually RUNS
  differs, and one of the three can miss it: ``test_temporal_leg_2976.py`` is
  registered under the ``sdk`` surface only, so a
  ``tortoise/search_engine.py``-only remedy diff (which selects ``core``) never
  runs it — the temporal-leg reds then surface later, on an unrelated PR.
  ``test_bm25_lexical_8488.py`` is registered under ``core`` AND ``carve_out``,
  so it DOES run in the embedded carve-out leg of the same remedy PR, and its
  red appears where the remedy can see it.
* **accept-and-restate** — delete the xfail pin together with the #2102 E2E-1
  restatement. Deleting it is a deliberate act, never a silent green.
"""
from __future__ import annotations

import pytest

from tortoise.search_engine import rrf_fusion

#: The recorded production balance the measured gap is produced under
#: (#1657: vector 1.5×, k=60) — the same weights the SDK passes.
_WEIGHTS = {"vector": 1.5}
_STRATEGIES = ["fts", "vector"]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "#7704: fusion gives two documents id-tied on EVERY leg different "
        "relevance (1.61% apart) — the shipped behaviour is the defect; the "
        "remedy is an OPEN owner ruling, so this reds again the moment a fix "
        "lands (remove the marker then). See the module docstring."
    ),
)
def test_identical_leg_scores_must_not_produce_different_fused_scores():
    """Two documents identical on EVERY leg ⇒ identical fused relevance.

    Mutation: enumerate() the leg positionally (what ships today) → this reds
    with 0.0409836 vs 0.0403226, a 1.61% relative gap carrying zero evidence.
    """
    fused = rrf_fusion(
        [[("b", 1.0), ("a", 1.0)], [("b", 1.0), ("a", 1.0)]],
        strategy_names=_STRATEGIES,
        weights=_WEIGHTS,
    )
    assert fused["a"] == fused["b"], (
        "two rows the legs scored identically were given different fused "
        f"relevance: {fused}")


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
