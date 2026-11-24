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
    against a single-term candidate at 0-based rank ``r``'s ``1/(61+r)``, so the
    comparison is decided by ``h < 61 + 2r`` — a RELATIVE bound. Against a lone
    rank-1 (``r = 0``) it reduces to ``h <= 60``, i.e. 1-based rank <= 61 in both
    lists, where at 62 in both the two terms TIE a lone rank-1 and ``(-score, id)``
    decides by spelling. ``DEFAULT_POOL_SIZE`` is 120 (``tortoise/retrieval.py``),
    so ranks past 61 are ordinary in the shipping default — not a corner.

    ⛔ THE FIRST FIX FOR THIS FINDING SHIPPED A FALSE GENERALIZATION AND THIS ARM
    GRADED IT AS REQUIRED TEXT (#14396 review, round 18, P2): it required the
    ``<= 61`` clause and nothing else, so all three carriers said two terms beat
    "any single-list rank" inside that window. They do not — `2/(61+63)` beats
    `1/(61+3)`, so a shared id at 1-based 64 passes a single-term candidate at
    1-based 4 while out-ranking it in NEITHER list. Both halves are graded here:
    the relative form must be stated (``61 + 2r``) AND the ``<= 61`` form must
    survive as what it is, the lone-rank-1 case.

    Non-vacuity (measured, not asserted): on the pre-fix bytes at `6e616a16`
    every carrier states the ``<= 61`` half and none states ``61 + 2r``, so this
    arm REDS there naming the carrier — the shape a docstring regression takes.
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
    lone_rank_one = re.compile(r"(<=|\u2264)\s*61")
    relative = re.compile(r"61\s*\+\s*2r")
    for where, text in carriers.items():
        assert unbounded not in text, (
            f"{where} still states {unbounded!r} — two terms beat a single-list "
            "rank only while h < 61 + 2r (0-based); the <= 61 window is the "
            "lone-rank-1 case")
        assert relative.search(text), (
            f"{where} states the two-term advantage without its RELATIVE bound — "
            "`h < 61 + 2r` is what makes the claim true; the `<= 61 in BOTH "
            "lists` clause alone is the lone-rank-1 special case and was graded "
            "here as if it were universal (#14396 review, round 18, P2)")
        assert lone_rank_one.search(text), (
            f"{where} dropped the lone-rank-1 case — at 1-based 62 in both "
            "lists the two terms TIE a lone rank-1, which is the half a reader "
            "needs to see beside the relative bound")


def test_the_two_term_bound_is_relative_to_the_rank_it_is_compared_against():
    """The arithmetic behind ``h < 61 + 2r``, pinned against the fusion itself.

    The prose row above grades that the RELATIVE bound is *stated*; this one grades
    that it is *true*, so a formula change cannot leave the three docstrings telling
    a reader something the engine no longer does. ``rrf_fusion`` sums
    ``w / (k + rank + 1)`` over a 0-BASED ``rank`` with ``k = 60``, so a shared id at
    0-based rank ``h`` in BOTH lists scores ``2/(61+h)`` against a single-term
    candidate at 0-based rank ``r``'s ``1/(61+r)``:

      * ``h < 61 + 2r`` — the two terms win;
      * ``h == 61 + 2r`` — EXACT tie, decided by ``(-score, id)``;
      * ``h > 61 + 2r`` — the single-term candidate wins.

    ⛔ THE ``r = 3`` ROWS ARE THE POINT: ``h = 63`` is OUTSIDE the <= 61 window and
    still beats a single-term candidate, and it out-ranks that candidate in NEITHER
    list — the case the universal "<= 61" sentence denied (#14396 review, P2).

    Non-vacuity: the ``h = 63`` row REDS on any implementation that made membership
    dominate by a fixed window (it asserts the shared id LEADS), and the ``h = 67``
    row REDS on one that let membership dominate unconditionally (it asserts the
    candidate LEADS). The tie row pins the boundary to the engine's own arithmetic
    rather than to a remembered fraction.
    """
    def _pair_at(h, r):
        # ``shared`` at 0-based rank ``h`` in BOTH lists; ``newcomer`` at 0-based
        # rank ``r`` of the expansion and nowhere else — exactly ONE term.
        assert r < h, "the fixture needs the newcomer above the shared id"
        pass1 = [(f"a{i}", 1.0 - i / 100000.0) for i in range(h)] + [("shared", 0.5)]
        expanded = (
            [(f"c{i}", 1.0 - i / 100000.0) for i in range(r)]
            + [("newcomer", 9.0)]
            + [(f"b{i}", 1.0 - i / 100000.0) for i in range(h - 1 - r)]
            + [("shared", 0.4)])
        # The fixture is a MEASUREMENT, not a comment: assert the ranks it claims.
        assert _ids(pass1).index("shared") == h
        assert _ids(expanded).index("shared") == h
        assert _ids(expanded).index("newcomer") == r
        assert "newcomer" not in _ids(pass1)
        return pass1, expanded

    for h, r in (
            (60, 0),   # the <= 61 case: two terms win
            (63, 3),   # OUTSIDE the window, and the shared id out-ranks NOTHING
            (66, 3),   # h == 61 + 2r - 1: the last h where the two terms win
            (62, 1),   # h == 61 + 2r - 1 at r = 1
    ):
        assert h < 61 + 2 * r, (h, r)
        pass1, expanded = _pair_at(h, r)
        ids = _ids(fuse_expansion_pass(pass1, expanded))
        assert ids.index("shared") < ids.index("newcomer"), (
            f"h={h}, r={r}: h < 61 + 2r, so the two terms must LEAD — got {ids}")

    for h, r in (
            (61, 0),   # 1-based 62 vs a lone rank-1: the exact TIE
            (63, 1),   # h == 61 + 2r at r = 1
            (67, 3),   # h == 61 + 2r at r = 3
    ):
        pass1, expanded = _pair_at(h, r)
        fused = rrf_fusion([pass1, expanded], 60)
        assert fused["shared"] == fused["newcomer"], (
            f"h={h}, r={r}: h == 61 + 2r must TIE exactly, which is what the prose "
            f"says — shared={fused['shared']!r} newcomer={fused['newcomer']!r}")

    pass1, expanded = _pair_at(68, 3)   # 68 > 61 + 6: past the tie, the candidate leads
    fused = rrf_fusion([pass1, expanded], 60)
    assert fused["newcomer"] > fused["shared"], (
        "past h = 61 + 2r the single-term candidate must lead outright; a two-term "
        "id does NOT outrank either pass alone")
    ids = _ids(fuse_expansion_pass(pass1, expanded))
    assert ids.index("newcomer") < ids.index("shared"), (
        f"past the relative bound the better single-list rank wins — got {ids}")


def test_the_two_term_advantage_is_bounded_at_1based_61():
    """The ARITHMETIC behind the bound, pinned against the fusion itself.

    The prose row above grades that the bound is *stated*; this one grades that
    it is *true*, so a formula change cannot leave the docstrings telling a
    reader something the engine no longer does. ``rrf_fusion`` sums
    ``w / (k + rank + 1)`` over a 0-BASED ``rank`` with ``k = 60``
    (``search_engine.py:5075``), so a shared id at 0-based rank ``h`` in BOTH
    lists scores ``2/(61+h)`` against a single-term rank-0 candidate's
    ``1/61``:

      * ``h <= 60`` (1-based <= 61) — the two terms win;
      * ``h == 61`` (1-based 62) — EXACT tie (``2/122 == 1/61``), decided by
        ``(-score, id)``, i.e. the id's spelling and not its evidence;
      * ``h >= 62`` — the better single-list rank wins.

    Non-vacuity (measured, then reverted): flipping ``k + rank + 1`` to a
    1-based ``k + rank`` in ``search_engine.py`` reds the ``h = 60`` row
    (``shared`` lands at index 2, asserted < 1). The ``h >= 62`` row reds on any
    implementation that made membership dominate, and the ``h == 61`` row reds
    on one that let a tie fall to insertion order.
    """
    def _pair(h):
        # ``shared`` at 0-based rank ``h`` in BOTH lists; ``newcomer`` at rank 0
        # of the expansion and nowhere else, so it earns exactly ONE term.
        pass1 = [(f"a{i}", 1.0 - i / 1000) for i in range(h)] + [("shared", 0.5)]
        expanded = ([("newcomer", 9.0)]
                    + [(f"b{i}", 1.0 - i / 1000) for i in range(h - 1)]
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
