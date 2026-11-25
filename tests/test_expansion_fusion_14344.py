"""#14344 (workstream A of #2513) — the additive second pass is FUSED, not
concatenated.

The unit under test is :func:`tortoise.expansion_merge.fuse_expansion_pass`, the
one merge rule the C2 entity/fact key expansion's two halves now share
(``sdk._entity_key_expansion_pass`` sparse, ``sdk._entity_key_dense_pass``
dense). It is pure — no graph, no embedder, no network — so it needs no
`TORTOISE_DB_URI`; ⚠️ that is a property of the FILE, not a claim about CI: it is
registered on `core` in `config/ci-surfaces.yml` (so the docker shards run it),
NOT in the `carve_out:` job's own list, which executes only its declared set.

**What the previous rule was, because every assertion below is written against
it.** The merge used to be::

    seen = {pid for pid, _ in expanded}
    merged = list(expanded)
    merged.extend((pid, s) for pid, s in pass1 if pid not in seen)

A pure rank concatenation. Two consequences, both measured in this file:

  (a) an id the expansion did not repeat was placed BELOW every expansion id,
      whatever the two runs' relative ranks — so pass-1 evidence could be
      displaced by the expansion's LENGTH;
  (b) an expansion-only id was placed ABOVE an id BOTH passes carried when the
      expansion ranked it first — so agreement between the query and the entity
      context was worth less than being new.

The fusion reverses both: ``rrf_fusion`` sums a reciprocal-rank term per list,
so agreement earns two terms and displacement has a rank bound instead of a
length bound. The tests below pin (a) and (b) as the DISCRIMINATORS — each one
fails under the concatenation.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import expansion_merge
from tortoise.expansion_merge import fuse_expansion_pass
from tortoise.search_engine import rrf_fusion


# ── The concatenation this replaced, kept ONLY as the counterfactual the
#    assertions below are written against. Never call it from product code. ──
def _old_concatenation(pass1, expanded):
    seen = {pid for pid, _score in expanded}
    merged = list(expanded)
    merged.extend((pid, s) for pid, s in pass1 if pid not in seen)
    return merged


def _ids(rows):
    return [pid for pid, _score in rows]


def test_pass1_evidence_is_not_displaced_by_the_expansion_length():
    """(a) THE MEASURED DEFECT. A pass-1 id the expansion does not repeat is
    displaced by exactly ``len(expanded)`` positions under the concatenation,
    and by at most the number of expansion ids that out-rank it under the
    fusion — here 0, because it holds pass 1's top slot and the expansion's top
    slot is an EQUAL-ranked newcomer (the fusion ties them and breaks on the id,
    deliberately, #2952)."""
    pass1 = [("gold", 0.9), ("runner-up", 0.5)]
    expanded = [(f"new{i:02d}", 9.0 - i) for i in range(20)]

    old = _old_concatenation(pass1, expanded)
    new = fuse_expansion_pass(pass1, expanded)

    assert _ids(old).index("gold") == 20, (
        "the counterfactual must be the displacement, or this test proves "
        "nothing: the concatenation puts the 20 expansion ids above it")
    assert _ids(new).index("gold") <= 1, (
        "the fusion bounds the displacement by RANK, not by how many rows the "
        "expansion happened to return")
    # …and the pass-1-only tail survives either way (the set was never the bug)
    assert "runner-up" in _ids(new)


def test_agreement_between_the_two_passes_outranks_being_new():
    """(b) An id BOTH passes carry beats an id only the expansion carries, even
    when the expansion ranks the newcomer first. Under the concatenation the
    newcomer won, unconditionally."""
    pass1 = [("incumbent", 0.9)]
    expanded = [("newcomer", 5.0), ("incumbent", 4.0)]

    old = _ids(_old_concatenation(pass1, expanded))
    new = _ids(fuse_expansion_pass(pass1, expanded))

    assert old.index("newcomer") < old.index("incumbent"), (
        "counterfactual: the concatenation promoted the newcomer")
    assert new.index("incumbent") < new.index("newcomer"), (
        "a candidate that BOTH the query and the entity context ranked must "
        "outrank one only the expansion ranked")


def test_scores_are_carried_from_origin_not_the_rrf_score():
    """The single-strategy path in ``_tortoise_fts_query`` filters on the raw
    leg score (``score >= threshold``). Replacing those with RRF values
    (~1/61) would empty every single-leg read that sets a threshold, so the
    helper reorders and never rescales. An id in both keeps the EXPANSION's
    score — the value the concatenation handed to the filter."""
    pass1 = [("a", 0.93), ("b", 0.44)]
    expanded = [("b", 3.5), ("c", 2.5)]
    scores = dict(fuse_expansion_pass(pass1, expanded))
    assert scores == {"a": 0.93, "b": 3.5, "c": 2.5}
    assert all(s > 0.4 for s in scores.values()), (
        "no id may be re-scaled into the RRF range")


def test_the_merge_is_a_union_never_a_drop():
    """The additive guarantee, at the boundary: every id from either pass is
    present exactly once, and the pass-1-only ids keep their relative order."""
    pass1 = [(f"p{i}", 1.0 - i / 100) for i in range(10)]
    expanded = [(f"x{i}", 1.0 - i / 100) for i in range(10)]
    merged = fuse_expansion_pass(pass1, expanded)
    ids = _ids(merged)
    assert set(ids) == {f"p{i}" for i in range(10)} | {f"x{i}" for i in range(10)}
    assert len(ids) == len(set(ids)), "no duplicate may survive the fusion"
    p_only = [pid for pid in ids if pid.startswith("p")]
    assert p_only == [f"p{i}" for i in range(10)], (
        "pass-1-only ids are ranked among themselves by the fusion, which is "
        "monotone in their pass-1 rank for equal membership — their ORIGINAL "
        "relative order")


def test_repeated_id_within_one_pass_cannot_earn_two_terms():
    """``rrf_fusion`` consumes POSITION, so a duplicated row would otherwise
    earn two terms and outrank its own rank.

    ⛔ THE FIXTURE IS THE DISCRIMINATOR (review round 1, P2): an earlier version
    used a newcomer whose id sorts AFTER the duplicate, so the doubled terms
    could not change the order and the test passed with the dedupe deleted. The
    newcomer here sorts BEFORE it (``aaa`` < ``dup``), so the tie is the only
    thing that can put the newcomer first — delete the dedupe and the OLD order
    returns, with ``dup`` leading on two terms."""
    pass1 = [("dup", 0.9), ("dup", 0.9), ("other", 0.1)]
    expanded = [("aaa", 1.0), ("bbb", 0.9)]
    merged = fuse_expansion_pass(pass1, expanded)
    assert dict(merged)["dup"] == 0.9
    deduped = [("dup", 0.9), ("other", 0.1)]
    assert _ids(merged) == _ids(fuse_expansion_pass(deduped, expanded)), (
        "a repeated row must not earn a second fusion term: the order must be "
        "the one the collapsed list produces")
    # …and the collapsed position is what puts the newcomer level with it:
    # without the dedupe ``dup`` scores two terms (0.0323) and leads outright.
    undeduped = list(rrf_fusion([pass1, expanded]))
    assert _ids(merged)[0] == "aaa" and undeduped[0] == "dup", (
        "the fixture must be able to distinguish a doubled term, or this test "
        f"proves nothing (merged={_ids(merged)}, undeduped={undeduped})")


def test_one_empty_side_returns_the_other_unchanged(monkeypatch):
    """Fail-open shape: the helper is only reached with two non-empty passes in
    production, but a lone side must pass through in its own order rather than
    being fused against nothing.

    ⛔ THE DISCRIMINATOR IS THAT THE FUSION IS NEVER REACHED (review round 6,
    P3-b). Returning the same rows in the same order is exactly what the FUSED
    path also does when one side is empty — ``rrf_fusion`` consumes POSITION, so
    with an empty second list it reproduces the first list's own ranks — so the
    RETURN VALUES alone cannot distinguish the guard, and the earlier version of
    this test was green with the guard deleted. Patching ``rrf_fusion`` to raise
    is what makes deleting ``expansion_merge.py``'s empty-side arm red here."""
    pass1 = [("a", 0.9), ("b", 0.8)]
    assert fuse_expansion_pass(pass1, []) == pass1
    assert fuse_expansion_pass([], pass1) == pass1
    assert fuse_expansion_pass([], []) == []

    def _must_not_be_called(*_args, **_kwargs):
        raise AssertionError(
            "a lone side must not be fused against nothing — the empty-side arm "
            "must short-circuit BEFORE the fusion")

    monkeypatch.setattr(expansion_merge, "rrf_fusion", _must_not_be_called)
    assert fuse_expansion_pass(pass1, []) == pass1
    assert fuse_expansion_pass([], pass1) == pass1


def test_no_new_expansion_order_when_the_expansion_adds_nothing():
    """When the expansion repeats pass 1 (nothing new), the leg's order is pass
    1's — the fusion must not shuffle a leg it did not enrich."""
    pass1 = [(f"p{i}", 1.0 - i / 100) for i in range(6)]
    assert _ids(fuse_expansion_pass(pass1, list(pass1))) == _ids(pass1)


def test_pure_function_of_the_two_lists_and_of_nothing_else():
    """#2952's rule, inherited: the merged order is a pure function of the leg
    CONTENTS, so the same read truncates to the same page.

    ⛔ THE TWO DISCRIMINATORS, added in review round 6 (P3-a) because comparing
    four calls to EACH OTHER is vacuous — any deterministic implementation passes
    that, including the concatenation this PR removed and ``reversed(fused)``,
    since the mutant applies to all four calls:

      (i) the exact ORDER is PINNED, not merely repeated. The two legs here are
          disjoint, so every id earns exactly one term; ids at equal rank tie and
          ``(-score, id)`` puts the ``p`` before the ``x``. That pin reds BOTH the
          restored concatenation (every ``x`` first) and ``reversed(fused)``.
          ⛔ It does NOT red on a SWAP of the two lists, and no assertion here
          can: equal-weight RRF is ``Σ 1/(k+rank+1)``, symmetric in the list
          index, so list order cannot change the fused order (measured — a
          ``rrf_fusion([second, first], k)`` mutant leaves all 9 rows GREEN,
          review round 11 P3). What the two lists DO determine is the SCORE
          PRECEDENCE for an id both passes carry, and that is pinned by
          ``test_scores_are_carried_from_origin_not_the_rrf_score`` instead
          (its ``{**dict(second), **dict(first)}`` mutant reds);
      (ii) the caller's leg lists must come back untouched — a merge that sorts
           or consumes a caller list in place reds here."""
    pass1 = [(f"p{i}", 1.0 - i / 100) for i in range(7)]
    expanded = [(f"x{i}", 1.0 - i / 100) for i in range(7)]
    pass1_before, expanded_before = list(pass1), list(expanded)

    first = _ids(fuse_expansion_pass(pass1, expanded))

    assert first == [pid for i in range(7) for pid in (f"p{i}", f"x{i}")], (
        "the fused order must be the rank order with the (-score, id) tiebreak "
        f"resolving each equal rank — got {first}")
    assert pass1 == pass1_before and expanded == expanded_before, (
        "the merge must not reorder or consume the caller's own leg lists in "
        "place — a sort/pop mutant reds here")
    for _ in range(3):
        assert _ids(fuse_expansion_pass(list(pass1), list(expanded))) == first


# ── The docstrings are part of the change: #14344 exists BECAUSE the two halves
#    hand-rolled the same merge and drifted from their own prose. ──
def _half_docstrings() -> dict:
    """Both halves' docstrings, read from SOURCE — no SDK import, no DB, so this
    stays a property of the ``core`` surface file it lives in."""
    import ast

    src = (Path(__file__).resolve().parent.parent / "tortoise" / "sdk.py").read_text()
    found = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name in (
                "_entity_key_expansion_pass", "_entity_key_dense_pass"):
            found[node.name] = " ".join((ast.get_docstring(node) or "").split())
    return found


def test_both_halves_describe_the_fusion_never_the_removed_concatenation():
    """The two halves must state the rule that SHIPS. Each names the fusion, and
    neither still asserts the concatenation's order — the dense half said "the
    expanded run comes first, the pass-1-only ids follow in their original
    order" three lines above the paragraph that negates it, and "identical to
    the sparse half's" was false in the other direction too (#14344 review)."""
    docs = _half_docstrings()
    assert set(docs) == {"_entity_key_expansion_pass", "_entity_key_dense_pass"}
    stale = (
        "identical to the sparse half's",
        "the expanded run comes first",
        "pass-1-only ids follow in their original order",
    )
    for name, doc in docs.items():
        assert "fuse_expansion_pass" in doc, f"{name} does not name the fusion"
        for phrase in stale:
            assert phrase not in doc, f"{name} still states the removed merge: {phrase!r}"


def test_the_two_term_advantage_is_published_with_its_bound_not_as_dominance():
    """#14396 review (P2-1): "an id both passes carry OUTRANKS either pass alone"
    is a guarantee this fusion does not give, and it was stated in four places.

    ``rrf_fusion`` scores a shared id ``1/(61+h1) + 1/(61+h2)`` (0-based ranks)
    against a single-list candidate at 0-based rank ``r``'s ``1/(61+r)``, so the
    condition is an INEQUALITY in ``r`` — ``r > (h1*h2 - 3721) / (122 + h1 + h2)``
    (3721 is ``61**2``) — never a bound on one rank of the shared id. Its
    SYMMETRIC case against a lone rank-1 (``h1 == h2 == h``, the only one a
    same-rank fixture builds) wins while both 1-based ranks are <= 61, TIES at 62
    and loses past it; its TOP-SLOT case is unconfined. ``DEFAULT_POOL_SIZE``
    is 120 (``tortoise/retrieval.py``), so ranks past 61 are ordinary in the
    shipping default — not a corner.

    The prose is load-bearing here: it is what tells a reader how the lever
    behaves, and the lever's adoption is the sealed A/B's decision (§3, #2516),
    so an unbounded "agreement dominates" is how the A/B's expectation gets
    mis-designed. This row is the non-vacuity proof for the fix: both carriers
    stated the unbounded claim before it.

    ⛔ BOTH HALVES OF THE CONDITION ARE GRADED, because round 18 found this row
    grading only one of them — the ``<= 61 in BOTH lists`` half is the SYMMETRIC
    case, and a sentence that published it as a necessary condition was false in
    the other direction. The arithmetic row below carries the asymmetric
    (``h1 != h2``) case that falsifies it; this row carries the wording.

    ⛔ AND THE PAIR FORM ITSELF IS GRADED AGAINST THE ENGINE, because the FIRST
    fix for round 18 published ``r < (h1 + h2 - 61) / 2`` — the same false
    necessity with the inequality inverted. It forbade a winning pair
    (``h1 == h2 == 0`` at ``r == 13``: ``2/61`` beats ``1/74``) and permitted a
    losing one (``h1 == h2 == 64`` at ``r == 0``: ``2/125`` loses to ``1/61``),
    i.e. it was wrong on 21 769 of 28 420 sampled (h1, h2, r) triples. The
    carriers now state the exact reduction, and this row REJECTS the inverted
    form so a re-cut cannot quietly restore it.

    ⛔ AND THE RELATION ITSELF IS GRADED, not only the operands — the gap that let
    that first fix ship. `pair_condition` alone certified a carrier that stated
    `(h1*h2 - 3721) / (122 + h1 + h2)` with the inequality INVERTED, so a third
    re-cut could flip `r >` to `r <` and stay green. Both directions are asserted
    per carrier: the relation that goes with that pair form is `r >`.
    """
    import re

    root = Path(__file__).resolve().parent.parent
    carriers = {
        "tortoise/expansion_merge.fuse_expansion_pass": " ".join(
            (expansion_merge.fuse_expansion_pass.__doc__ or "").split()),
        "sdk._entity_key_dense_pass": _half_docstrings()["_entity_key_dense_pass"],
        "docs/evidence/14344-expansion-fusion/README.md": " ".join(
            (root / "docs" / "evidence" / "14344-expansion-fusion"
             / "README.md").read_text().split()),
    }
    unbounded = "outranks either pass alone"
    bound = re.compile(r"(<=|\u2264)\s*61")
    # ⛔ The POSITIVE half round 18 found missing from every carrier: the condition
    # is an INEQUALITY in the single-list rank, `r > (h1*h2 - 3721) / (122 + h1 +
    # h2)`, so a carrier that states only the symmetric `<= 61` case publishes it
    # as a NECESSARY condition and is false whenever either rank is 1. Graded
    # positively because enumerating the false phrasings is what let the mirror
    # error through twice; both the ASCII and the typographic minus are accepted,
    # since the README uses U+2212, and so is the `61**2` / `61\u00b2` spelling of 3721.
    pair_condition = re.compile(r"h1\s*[*\u00b7]\s*h2\s*[-\u2212]\s*(?:3721|61\s*(?:\*\*|\u00b2|\^)\s*2?)")
    # ⛔ The INVERTED form the first round-18 fix published. It is a false
    # necessity (see the docstring), so its presence is a regression, not a
    # stylistic difference.
    inverted = re.compile(r"\(\s*h1\s*\+\s*h2\s*[-\u2212]\s*61\s*\)\s*/\s*2")
    # ⛔ THE OPERATOR IS THE HALF THAT WAS STILL UNGRADED, and it is the half that
    # actually shipped wrong — TWICE. Round 18's first fix carried the right two
    # operands with the relation INVERTED (`r <`), and this row was GREEN over it,
    # because `pair_condition` below grades the pair form's OPERANDS and nothing
    # grades which way the inequality points. The arithmetic points ONE way: the
    # worse the single-list rank (the larger `r`), the EASIER a two-term candidate
    # clears it, so the relation is `r > (<pair form>)` — and the mirrored
    # `r < (<pair form>)` is the same false necessity as the `(h1 + h2 - 61)/2`
    # form, restated with the right operands. Graded in both directions because a
    # positive-only test is what let this through.
    pair_args = (r"h1\s*[*\u00b7]\s*h2\s*[-\u2212]\s*"
                 r"(?:3721|61\s*(?:\*\*|\u00b2|\^)\s*2?)")
    pair_direction = re.compile(r"\br\s*>\s*[(\s]*" + pair_args)
    mirrored_direction = re.compile(r"\br\s*<\s*[(\s]*" + pair_args)
    for where, text in carriers.items():
        assert unbounded not in text, (
            f"{where} still states {unbounded!r} — two terms beat a single-list "
            "rank only under the pair condition r > (h1*h2 - 3721) / (122 + h1 + h2)")
        assert bound.search(text), (
            f"{where} states the two-term advantage without its bound — the "
            "`<= 61` clause is what makes the SYMMETRIC half of the claim true")
        assert pair_condition.search(text), (
            f"{where} publishes the `<= 61` case without the PAIR condition "
            "`r > (h1*h2 - 3721) / (122 + h1 + h2)` — the `<= 61` reading is the "
            "symmetric case only, and a shared id holding the TOP slot in either "
            "list beats every single-list term whatever its other rank (round 18, P2-1)")
        assert not inverted.search(text), (
            f"{where} publishes the INVERTED pair condition `r < (h1 + h2 - 61) / 2`, "
            "which is false in both directions: `h1 == h2 == 0` at `r == 13` WINS "
            "(2/61 > 1/74) and `h1 == h2 == 64` at `r == 0` LOSES (2/125 < 1/61). "
            "The exact reduction is `r > (h1*h2 - 3721) / (122 + h1 + h2)`")
        assert pair_direction.search(text), (
            f"{where} states the pair form `(h1*h2 - 3721) / (122 + h1 + h2)` "
            "without the relation that goes with it — the condition is "
            "`r > (h1*h2 - 3721) / (122 + h1 + h2)`, an inequality in the "
            "SINGLE-LIST candidate's rank (a worse rank clears it more easily). "
            "Round 18's first fix stated these operands with `r <`, which is false "
            "in both directions: `h1 == h2 == 0` at `r == 13` WINS (2/61 > 1/74) "
            "and `h1 == h2 == 64` at `r == 0` LOSES (2/125 < 1/61).")
        assert not mirrored_direction.search(text), (
            f"{where} states the MIRRORED pair condition `r < (h1*h2 - 3721) / "
            "(122 + h1 + h2)` — the right operands with the relation inverted, "
            "i.e. the same false necessity the `(h1 + h2 - 61)/2` form published. "
            "A worse single-list rank clears the pair MORE easily, so the "
            "relation is `r >`.")


def test_the_two_term_advantage_is_bounded_at_1based_61():
    """The ARITHMETIC behind the bound, pinned against the fusion itself.

    The prose row above grades that the pair condition is *stated*; this one
    grades that it is *true*, so a formula change cannot leave the docstrings
    telling a reader something the engine no longer does. ``rrf_fusion`` sums
    ``w / (k + rank + 1)`` over a 0-BASED ``rank`` with ``k = 60``
    (``search_engine.py:5075``), so a shared id at 0-based rank ``h`` in BOTH
    lists scores ``2/(61+h)`` against a single-term rank-0 candidate's
    ``1/61``:

      * ``h <= 60`` (1-based <= 61) — the two terms win;
      * ``h == 61`` (1-based 62) — EXACT tie (``2/122 == 1/61``), decided by
        ``(-score, id)``, i.e. the id's spelling and not its evidence;
      * ``h >= 62`` — the better single-list rank wins.

    ⛔ THAT IS THE **SYMMETRIC** CASE ONLY, AND ROUND 18 FOUND THE DIFFERENCE
    MATTERS (P2-1). Every row above puts the shared id at the SAME rank in both
    lists, so the row could not see the top-slot case at all — and a shared id at
    rank 0 in one list beats EVERY single-list candidate outright, whatever its
    rank in the other. The asymmetric leg below pins that (``h1 = 1``,
    ``h2 = 201``, i.e. 1-based 2 and 202) against a ``newcomer`` at 0-based rank
    0, which is exactly the fixture that falsified the "only while <= 61 in BOTH
    lists" sentence this PR shipped while fixing its predecessor.

    Non-vacuity (measured, then reverted): flipping ``k + rank + 1`` to a
    1-based ``k + rank`` in ``search_engine.py`` reds the ``h = 60`` row
    (``shared`` lands at index 2, asserted < 1). The ``h >= 62`` row reds on any
    implementation that made membership dominate, the ``h == 61`` row reds on one
    that let a tie fall to insertion order, and the ASYMMETRIC row reds on any
    engine that counted membership once rather than once per list.
    """
    def _pair(h, h_other=None):
        # ``shared`` at 0-based rank ``h`` in pass 1 and ``h_other`` (default: the
        # same) in the expansion; ``newcomer`` at rank 0 of the expansion and
        # nowhere else, so it earns exactly ONE term.
        h2 = h if h_other is None else h_other
        pass1 = [(f"a{i}", 1.0 - i / 1000) for i in range(h)] + [("shared", 0.5)]
        expanded = ([("newcomer", 9.0)]
                    + [(f"b{i}", 1.0 - i / 1000) for i in range(h2 - 1)]
                    + [("shared", 0.4)])
        return pass1, expanded

    # h = 60 (1-based 61): the two terms still win.
    pass1, expanded = _pair(60)
    ids = _ids(fuse_expansion_pass(pass1, expanded))
    assert ids.index("shared") < ids.index("newcomer"), (
        "at 1-based 61 in both lists the two-term id must still lead")

    # h = 61 (1-based 62): an EXACT tie — the id decides, not the evidence.
    pass1, expanded = _pair(61)
    fused = rrf_fusion([pass1, expanded], 60)
    assert fused["shared"] == fused["newcomer"], (
        "at 1-based 62 the two scores must TIE exactly, which is what the "
        f"prose says — shared={fused['shared']!r} newcomer={fused['newcomer']!r}")
    ids = _ids(fuse_expansion_pass(pass1, expanded))
    assert ids.index("newcomer") < ids.index("shared"), (
        "a tie is broken by (-score, id) — the id's own spelling ('newcomer' "
        f"< 'shared'), never the evidence — got {ids}")

    # h = 62: the single-list rank-0 candidate wins — membership is NOT dominant.
    pass1, expanded = _pair(62)
    ids = _ids(fuse_expansion_pass(pass1, expanded))
    assert ids.index("newcomer") < ids.index("shared"), (
        "beyond 1-based 62 the better single-list rank must win: an id both "
        "passes carry does NOT outrank either pass alone")

    # ⛔ ASYMMETRIC (round 18, P2-1): ``shared`` at 0-based rank 1 in pass 1 and
    # 201 in the expansion — 1-based 2 and 202, so NOTHING here is `<= 61 in both`
    # — still beats a ``newcomer`` at rank 0. 1/62 + 1/262 = 0.0199458 > 1/61 =
    # 0.0163934. This is the leg the symmetric rows cannot build, and it is the
    # published "only while BOTH are <= 61" sentence's counterexample.
    pass1, expanded = _pair(1, 201)
    fused = rrf_fusion([pass1, expanded], 60)
    assert fused["shared"] == 1 / 62 + 1 / 262, (
        f"the shared id must earn BOTH terms at (h1, h2) = (1, 201) — got "
        f"{fused['shared']!r} against {1 / 62 + 1 / 262!r}")
    assert fused["shared"] > fused["newcomer"], (
        "a shared id whose LOW rank is 1 must beat the single-term rank-0 "
        f"candidate even at 1-based 202 in the other list — got shared="
        f"{fused['shared']!r} newcomer={fused['newcomer']!r}")
    ids = _ids(fuse_expansion_pass(pass1, expanded))
    assert ids.index("shared") < ids.index("newcomer"), (
        f"the asymmetric pair must LEAD the fused order — got {ids}")

    # ⛔ ...and the same asymmetry is NOT a licence to claim dominance: at
    # (h1, h2) = (64, 64) two terms lose to a rank-0 newcomer, which is why the
    # prose states a PAIR condition and not a top-slot guarantee either.
    pass1, expanded = _pair(64)
    ids = _ids(fuse_expansion_pass(pass1, expanded))
    assert ids.index("newcomer") < ids.index("shared"), (
        f"at 1-based 65 in both lists the single-term rank-0 candidate must "
        f"lead — got {ids}")


def test_the_published_pair_condition_is_the_engines_own_inequality():
    """The pair condition, swept against ``rrf_fusion`` over a grid — not sampled.

    ⛔ THIS IS THE ROW THE ROUND-18 FIX NEEDED AND DID NOT HAVE. Round 18 was
    right that ``<= 61 in BOTH lists`` is not a necessary condition, and its fix
    published the replacement ``r < (h1 + h2 - 61) / 2`` — which is the SAME
    false necessity with the inequality INVERTED. Checked against this module's
    own ``rrf_fusion`` (``w / (k + rank + 1)``, ``k = 60``, 0-based rank,
    ``search_engine.py``) it is wrong on 21 769 of 28 420 sampled ``(h1, h2, r)``
    triples. Two hand-checkable extremes:

      * ``h1 == h2 == 0``, ``r == 13`` — the two terms score ``2/61 == 0.032787``
        against ``1/74 == 0.013514`` and WIN, yet the inverted form predicts a
        loss (``13 < -30.5`` is false);
      * ``h1 == h2 == 64``, ``r == 0`` — they score ``2/125 == 0.016000``
        against ``1/61 == 0.016393`` and LOSE, yet the inverted form predicts a
        win (``0 < 33.5`` is true).

    The exact reduction of ``1/(61+h1) + 1/(61+h2) > 1/(61+r)`` is
    ``r > (h1*h2 - 3721) / (122 + h1 + h2)`` (3721 is ``61**2``; every
    denominator is positive, so the step is an equivalence). This row asserts
    the published closed form against the engine on a grid, so a re-cut that
    republishes either false reading — necessity on the shared ranks, or the
    inverted inequality — reds HERE, on the arithmetic, not only in the prose.

    Non-vacuity (measured, then reverted): with the carriers' OLD
    ``r < (h1 + h2 - 61) / 2`` substituted for the published form below, this
    row fails on the very first grid cell ``(0, 0, 7)``; with the exact form it
    passes over the whole grid. It also reds on any engine whose membership is
    counted once rather than once per list, since the grid includes pairs where
    only the second term flips the outcome.
    """
    def _published(h1, h2, r):
        # The carriers' condition, transcribed from `fuse_expansion_pass`'s
        # docstring. Kept as arithmetic so the docstring and the engine are
        # compared on the same footing — a prose edit alone cannot move it.
        return r > (h1 * h2 - 3721) / (122 + h1 + h2)

    def _build(h1, h2, r):
        """``shared`` at 0-based h1 in pass 1 and h2 in the expansion;
        ``newcomer`` at 0-based r in pass 1 only, so it earns exactly ONE term."""
        width = max(h1, r) + 1
        first = [f"p{i}" for i in range(width)]
        first[h1] = "shared"
        first[r] = "newcomer"
        second = [f"e{i}" for i in range(h2 + 1)]
        second[h2] = "shared"
        return [(x, 1.0) for x in first], [(x, 1.0) for x in second]

    checked = 0
    for h1 in range(0, 141, 7):
        for h2 in range(0, 141, 11):
            for r in range(0, 141, 9):
                if r == h1:
                    continue  # one row cannot hold both ids at one index
                fused = rrf_fusion(list(_build(h1, h2, r)), 60)
                actual = fused["shared"] > fused["newcomer"]
                assert fused["shared"] == 1 / (61 + h1) + 1 / (61 + h2), (
                    f"the shared id must earn BOTH terms at (h1, h2) = ({h1}, "
                    f"{h2}) — got {fused['shared']!r}")
                assert fused["newcomer"] == 1 / (61 + r), (
                    f"the newcomer must earn exactly ONE term at r = {r} — got "
                    f"{fused['newcomer']!r}")
                assert actual == _published(h1, h2, r), (
                    f"the published pair condition disagrees with the engine at "
                    f"(h1, h2, r) = ({h1}, {h2}, {r}): shared={fused['shared']!r} "
                    f"newcomer={fused['newcomer']!r} says {actual}, the carriers' "
                    f"`r > (h1*h2 - 3721) / (122 + h1 + h2)` says "
                    f"{_published(h1, h2, r)} — re-derive the carriers, do not "
                    "re-word them")
                checked += 1
    assert checked > 200, f"the sweep must actually sweep — only {checked} cells"

    # The two cells that separate the exact form from the inverted one, asserted
    # directly so the failure names a reader-checkable example rather than a grid
    # coordinate.
    fused = rrf_fusion(list(_build(0, 0, 13)), 60)
    assert fused["shared"] > fused["newcomer"], (
        "h1 == h2 == 0 at r == 13 must WIN for the two terms (2/61 > 1/74) — the "
        "inverted `r < (h1 + h2 - 61) / 2` forbids this winning pair")
    fused = rrf_fusion(list(_build(64, 64, 0)), 60)
    assert fused["newcomer"] > fused["shared"], (
        "h1 == h2 == 64 at r == 0 must LOSE for the two terms (2/125 < 1/61) — "
        "the inverted `r < (h1 + h2 - 61) / 2` permits this losing pair")


def test_the_dense_fixture_pin_does_not_restate_the_symmetric_case_as_necessary():
    """Round 18's P2-1 residue, in the file that review did not enumerate.

    ``tests/test_entity_key_expansion.py``'s dense-fixture comment said "two
    terms beat a single-list rank only inside the <=61-in-both bound" — the
    SYMMETRIC case published as a NECESSARY condition, i.e. the same false
    generalization round 18 blocked in the three carriers, surviving in a fifth
    place because the sentence was copied rather than cited. The authoritative
    statement therefore lives ONCE (``expansion_merge.fuse_expansion_pass``'s
    docstring, graded by ``test_the_two_term_advantage_is_published_with_its_bound_not_as_dominance``
    above), and this arm pins the copy's absence instead of adding a guard over
    new prose.

    Non-vacuity (measured at ``3ce7407dfddd``, the head this arm was added for):
    with the pre-fix comment restored on that tree this REDS naming the phrase;
    against the fixed comment it is green.
    """
    src = (Path(__file__).resolve().parent / "test_entity_key_expansion.py").read_text()
    stale = "only inside the <=61-in-both bound"
    assert stale not in src, (
        "the dense-fixture comment restates the two-term advantage as a NECESSARY "
        f"condition ({stale!r}) — that reading is the SYMMETRIC case only, and the "
        "PAIR condition is stated once, in `expansion_merge.fuse_expansion_pass`'s "
        "docstring (round 18, P2-1). Re-word the copy to cite the carrier.")
