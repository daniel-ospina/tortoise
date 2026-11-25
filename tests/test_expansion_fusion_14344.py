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

# The tie tolerance for the float comparison in
# `test_the_published_pair_condition_is_the_fusions_arithmetic`: identical values
# compare exactly, and the narrowest real margin on that sweep is ~5e-5, so this
# only ever absorbs summation noise.
_TIE_EPS = 1e-15


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
    condition is a PAIR of ranks — ``r > (61+h1)(61+h2)/(122+h1+h2) - 61``, a TIE
    at equality and a loss below it — never a bound on one rank. Its SYMMETRIC
    case (``h1 == h2 == h``, the only one a same-rank fixture builds) wins while
    both 1-based ranks are <= 61, TIES a lone rank-1 at 62 and loses past it; its
    TOP-SLOT case is unconfined. ``DEFAULT_POOL_SIZE``
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

    ⛔ AND ROUND 18 STILL PUBLISHED A FALSE CLOSED FORM, which is why this row now
    names the ONE true one (#14597 review, P2-1): the sentence it graded said
    ``r < (h1 + h2 - 61) / 2``, wrong in both directions, and the regex pinned that
    string instead of the arithmetic. The closed form is now graded positively AND
    the refuted one negatively, so a carrier cannot re-publish it; that the form is
    the ENGINE's is `test_the_published_pair_condition_is_the_fusions_arithmetic`.
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
    # is a pair of ranks, so a carrier that states only the symmetric `<= 61` case
    # publishes it as a NECESSARY condition and is false whenever either rank is 1
    # — and round 18's own closed form was false too, so the form itself is now
    # graded (see the arithmetic row) rather than only its presence. Both the ASCII
    # and the typographic minus are accepted, since the README uses U+2212.
    pair_condition = re.compile(
        r"\(\s*61\s*\+\s*h1\s*\)\s*\(\s*61\s*\+\s*h2\s*\)\s*/\s*"
        r"\(\s*122\s*\+\s*h1\s*\+\s*h2\s*\)\s*[-\u2212]\s*61")
    # ⚠️ The REFUTED form `r < (h1 + h2 - 61) / 2` is deliberately NOT pinned
    # negatively: every carrier QUOTES it to say it was wrong, and a regex would
    # have to tell a correction from a re-publication — exactly the fail-closed
    # over-block this repo does not add gates for. The TRUTH of the form is
    # graded by `test_the_published_pair_condition_is_the_fusions_arithmetic`
    # below, which is the instrument that would have caught round 18's error.
    for where, text in carriers.items():
        assert unbounded not in text, (
            f"{where} still states {unbounded!r} — two terms beat a single-list "
            "rank only under the pair condition "
            "r > (61+h1)(61+h2)/(122+h1+h2) - 61")
        assert bound.search(text), (
            f"{where} states the two-term advantage without its bound — the "
            "`<= 61` clause is what makes the SYMMETRIC half of the claim true")
        assert pair_condition.search(text), (
            f"{where} publishes the `<= 61` case without the PAIR condition "
            "`r > (61+h1)(61+h2)/(122+h1+h2) - 61` — the `<= 61` reading is the "
            "symmetric case only, and a shared id holding the TOP slot in either "
            "list beats every single-list term whatever its other rank (round 18, P2-1)")


def test_the_published_pair_condition_is_the_fusions_arithmetic():
    """The pair condition the carriers PUBLISH, graded numerically against
    ``rrf_fusion`` over the ASYMMETRIC interior (#14597 review, P2-1).

    ``rrf_fusion`` sums ``w / (k + rank + 1)`` over a 0-BASED ``rank`` with
    ``k = 60``, so a shared id at ``(h1, h2)`` scores ``1/(61+h1) + 1/(61+h2)``
    against a single-list candidate at ``r``'s ``1/(61+r)``. Solving

        ``1/(61+h1) + 1/(61+h2) > 1/(61+r)``   (``A = 61+h1``, ``B = 61+h2``)

    gives ``r > A*B/(A+B) - 61``, i.e. the shared id beats the WORSE-ranked
    incumbents — a TIE at equality and a loss below it. An earlier cut published
    ``r < (h1 + h2 - 61) / 2``, which disagrees with the engine on the sweep
    below in both directions, and the sibling row
    ``test_the_two_term_advantage_is_bounded_at_1based_61`` could not see it
    because every fixture there is symmetric or top-slot — the two regions where
    the two forms happen to agree.

    Rational arithmetic decides the published bound (``Fraction``) and
    ``rrf_fusion`` itself decides the engine, so the equality case is decided by
    the VALUE and never by rounding. Non-vacuity: the named fixtures at the top are
    RED against the published-before form, and that is asserted here rather than
    remembered (the old form is recomputed, not quoted).
    """
    from fractions import Fraction

    def bound(h1: int, h2: int) -> Fraction:
        return Fraction((61 + h1) * (61 + h2), 122 + h1 + h2) - 61

    def published(h1: int, h2: int, r: int) -> str:
        b = bound(h1, h2)
        return "win" if r > b else ("tie" if r == b else "loss")

    def published_before(h1: int, h2: int, r: int) -> str:
        # The refuted form, recomputed so its disagreement is MEASURED.
        b = Fraction(h1 + h2 - 61, 2)
        return "win" if r < b else ("tie" if r == b else "loss")

    def _at(rank: int, pid: str) -> list[tuple[str, float]]:
        # A ranked list whose `rank` is `pid`. The pads neither list competes for
        # are inert: a pad earns a term, and only "shared"/"single" are read.
        return [(f"pad{i}", 1.0) if i != rank else (pid, 1.0)
                for i in range(rank + 1)]

    def engine(h1: int, h2: int, r: int) -> str:
        # THREE lists on purpose: the shared id earns exactly TWO terms and the
        # single-list candidate ONE, each the 0-based `w / (k + rank + 1)` the
        # prose quotes — run through the repo's OWN ``rrf_fusion`` at `k = 60`,
        # never a replica of its arithmetic (that is what the refuted form got
        # away with: it was checked against a paraphrase instead).
        fused = rrf_fusion(
            [_at(h1, "shared"), _at(h2, "shared"), _at(r, "single")], 60)
        if fused["shared"] > fused["single"] + _TIE_EPS:
            return "win"
        if fused["single"] > fused["shared"] + _TIE_EPS:
            return "loss"
        return "tie"

    # The fixtures that falsified the refuted form, one per failure mode:
    #   (0, 0, 0)     — top slot in both lists: `2/61 > 1/61`, yet it said "loss"
    #   (61, 61, 0)   — the exact tie `2/122 == 1/61`; it said "win"
    #   (61, 61, 100) — a WORSE-ranked incumbent is beaten; it said "loss"
    #   (62, 62, 0)   — the test's own `beyond 1-based 62` case; it said "win"
    #   (62, 100, 0)  — asymmetric interior, same false positive
    #   (1, 201, 100) — asymmetric interior, false negative of the other sign
    named = {
        (0, 0, 0): "win",
        (61, 61, 0): "tie",
        (61, 61, 100): "win",
        (62, 62, 0): "loss",
        (62, 100, 0): "loss",
        (1, 201, 100): "win",
    }
    for (h1, h2, r), want in named.items():
        assert published(h1, h2, r) == want, (
            f"(h1, h2, r) = ({h1}, {h2}, {r}): the published bound "
            f"r > {bound(h1, h2)} must read {want!r}")
        assert engine(h1, h2, r) == want, (
            f"(h1, h2, r) = ({h1}, {h2}, {r}): rrf_fusion's own scores must read "
            f"{want!r} — got {engine(h1, h2, r)!r}")
        assert published_before(h1, h2, r) != want, (
            f"(h1, h2, r) = ({h1}, {h2}, {r}) does NOT falsify the refuted "
            "r < (h1 + h2 - 61) / 2 — pick another fixture or this row has "
            "lost its non-vacuity")

    examined = 0
    # Asymmetric interior pairs, the region the symmetric rows cannot build:
    # (61, 61) and (62, 62) straddle the tie, and (62, 100) / (1, 201) put the
    # false positive and the false negative of the refuted form on the same sweep.
    for h1, h2 in ((0, 0), (0, 1), (61, 61), (62, 62), (62, 100), (100, 5),
                   (1, 201), (5, 200), (200, 0)):
        for r in range(0, 120):
            examined += 1
            got, want = engine(h1, h2, r), published(h1, h2, r)
            assert got == want, (
                f"(h1, h2, r) = ({h1}, {h2}, {r}): the published bound "
                f"r > {bound(h1, h2)} reads {want!r} but rrf_fusion reads "
                f"{got!r} — the prose and the engine have drifted apart")
    assert examined >= 1000, f"the sweep must stay broad, got {examined} points"


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
