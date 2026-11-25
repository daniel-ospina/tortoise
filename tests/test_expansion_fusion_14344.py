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

import re
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


# ── The pair-condition PROSE pin, as a grader rather than as a one-off search, so
#    its non-vacuity is an assertion instead of a lab note (#14597 review P2). ──
#: The EXPRESSION alone — the right-hand side of the published condition, with no
#: relation attached. Kept SEPARATE from ``_PAIR_CONDITION`` on purpose: the
#: relation is graded PER OCCURRENCE (``_pair_condition_violations``), which is
#: only possible over a pattern that still matches a site whose relation was
#: flipped — a regex carrying ``r >`` simply stops matching it and reports nothing.
_PAIR_EXPRESSION = re.compile(
    r"\(\s*61\s*\+\s*h1\s*\)\s*\(\s*61\s*\+\s*h2\s*\)\s*/\s*"
    r"\(\s*122\s*\+\s*h1\s*\+\s*h2\s*\)\s*[-\u2212]\s*61")
#: The published condition INCLUDING its relation: group 1 is the relation
#: (``r >``), group 2 the expression.
#:
#: ⛔ THE RELATION IS PART OF THE FORM (#14597 re-review, P2-2). The refuted closed
#: form this pin exists for differed from the true one IN ITS DIRECTION, and the
#: pre-fix regex graded only the right-hand EXPRESSION — so a carrier could publish
#: ``r < (61+h1)(61+h2)/(122+h1+h2) - 61``, the reciprocal of what ``rrf_fusion``
#: does, and every row stayed green. Measured at ``26276a06aaeb``: flipping the
#: relation at ALL FIVE sites left ``_pair_condition_violations`` empty. Requiring
#: the relation here restores the shape row's claim; grading it per occurrence
#: below is what makes the claim survive a site whose relation was flipped.
_PAIR_CONDITION = re.compile(r"(r\s*>\s*)(" + _PAIR_EXPRESSION.pattern + r")")
#: How the TRUE relation is introduced: ``r >`` immediately before the expression.
#: Only the STRICT ``>`` is correct — at equality the two-term id TIES a lone
#: rank-1 (``2/122 == 1/61``) and loses below, so ``>=`` over-claims in the same
#: direction the refuted form under-claimed, and a missing relation states none of
#: it. Matched against a bounded prefix, which is the stated residual below.
_PAIR_RELATION_TRUE = re.compile(r"r\s*>\s*$")
#: How far back from an occurrence the true relation is looked for. The five sites
#: measured at ``26276a06aaeb`` spell it as ``r >`` IMMEDIATELY before the
#: expression (the widest gap is the backtick that closes ``r``), so 40 leaves room
#: for a re-worded connective without letting an earlier ``r >`` in the same clause
#: excuse a later flipped site.
_PAIR_RELATION_LOOKBEHIND = 40
_REFUTED_FORM = re.compile(
    r"r\s*<\s*\(\s*h1\s*\+\s*h2\s*[-\u2212]\s*61\s*\)\s*/\s*2")
_ONE_RANK_BOUND = re.compile(r"(<=|\u2264)\s*61")
#: The phrases a carrier uses to INTRODUCE the win condition. A clause carrying
#: one of them must carry the true pair form, because that is the clause a reader
#: takes the condition from — the two sites in ``expansion_merge`` and the one site
#: in each of the other three carriers, measured, not assumed. ``beats a
#: single-list`` is deliberately a PREFIX: the carriers spell the single-list side
#: "candidate at 0-based rank r" (the README and both docstrings) and "rank-`r`
#: term" (``docs/00_index.md``), so a trigger set matching only one spelling would
#: silently drop the other site from the grade (measured at ``36f61041``).
_WIN_ARITHMETIC = (
    "beats a single-list",
    "only while",
    "can pass an incumbent",
)
#: A carrier may QUOTE the refuted form only if it says, BESIDE THE QUOTE, that the
#: form is the refuted one. Deliberately narrow and explicit: the linter pattern
#: this copies (a `noqa` suppression carrying its rule code / `nosec` / `//nolint`)
#: puts the suppression ON the offending line and NAMES the rule rather than
#: blanket-silencing a file — Ruff's own ``unused-noqa`` (RUF100) exists because a
#: suppression that stops matching is itself a defect.
#:
#: ⛔ ``⛔`` IS NOT IN THIS SET, and that is a measured correction (#14597 review
#: P2-1): it is this repo's generic "careful" glyph, used on clauses that OPEN a
#: hard warning — including the clause in ``docs/00_index.md`` that opens with ⛔ and
#: STATES THE TRUE FORM. Having it in the set meant a refuted form spliced into that
#: very sentence was excused by the sentence's own opening glyph (reproduced: the
#: row stayed green). The set now names only a RETRACTION.
_CORRECTION = re.compile(
    r"\bfalse\b|\brefuted\b|\bwrong\b|\bdisagrees\b|\bcorrected\b"
    r"|\buntil now\b|\bno longer\b")
#: How close the correction marker must sit to the refuted quote it excuses, on
#: either side and WITHIN THE SAME CLAUSE (#14597 review P2-1).
#:
#: ⛔ MEASURED, and the number is the whole point. A CLAUSE-wide exemption was the
#: defect: ``expansion_merge``'s "What changes is the BOUND: pass-1 evidence can no
#: longer be displaced …" clause carries the word "no longer" **279 characters**
#: from its pair form, in a sense that has nothing to do with a retraction, and that
#: word laundered a refuted form spliced in at the sentence's own pair site —
#: reproduced at ``36f61041``: **0 violations, the row stayed green** while the first
#: site's identical splice reddened it. The three shipped corrected quotes sit at
#: **24, 31 and 67** characters from their nearest marker, so 200 keeps ~3x headroom
#: over the widest real one while excluding a word 279 away.
#:
#: The window is ALSO bounded by the clause (the slice below never crosses a
#: ``_CLAUSE_BOUNDARY``), because the ⛔ that opens the NEXT clause sits 41
#: characters past the FIRST pair form: a bare distance window would let the
#: following clause's marker excuse a splice here. And the number is not trusted on
#: its own — `test_the_pair_condition_pin_is_clause_scoped_not_a_presence_check`
#: mutates EVERY site in EVERY carrier and reds if any splice goes ungraded, so a
#: prose edit that moved a launderer inside the window would red that row instead of
#: silently re-opening the hole.
_CORRECTION_WINDOW = 200
#: Clause boundaries as the carriers actually punctuate them. The lookahead is
#: what keeps ``i.e.`` and ``e.g.`` from splitting a clause in half: a real
#: boundary here is followed by a capital, an open paren, a bold marker, a
#: backtick span or a ⛔.
_CLAUSE_BOUNDARY = re.compile(r"(?<=[.;])\s+(?=[A-Z(\u26d4*`])")


def _index_row() -> str:
    """The ``#14344`` row of ``docs/00_index.md`` — the FOURTH published site of the
    merge rule's condition, and the one the pre-fix ``_carriers()`` left ungraded
    while its docstring said "every" (#14597 review, P2-1). It restates the rule,
    quotes the refuted form, and repeats the "cannot re-publish it at the other
    site" claim about the pin itself.

    ⛔ EXTRACTED BY ITS OWN KEY, and the extraction ASSERTS it matched exactly one
    row: a re-keyed or deleted row reds HERE rather than leaving the carrier to
    grade an empty string — an empty text splits into no clauses, so every check in
    the row below would pass vacuously.
    """
    root = Path(__file__).resolve().parent.parent
    rows = [line for line in (root / "docs" / "00_index.md").read_text().splitlines()
            if line.startswith("| **`#14344`")]
    assert len(rows) == 1, (
        "docs/00_index.md holds "
        f"{len(rows)} rows keyed `#14344` — this carrier is extracted by that key, "
        "so it must match exactly one")
    return " ".join(rows[0].split())


def _carriers() -> dict:
    """
    Every PROSE carrier that publishes the merge rule's condition, keyed by the
    site a reader meets it at: the ``fuse_expansion_pass`` docstring, the one SDK
    docstring, the evidence README, and the ``docs/00_index.md`` row. Each is read
    from source/disk — never imported — so this stays a ``core``-surface property.

    ⛔ ``fuse_expansion_pass`` is a FUNCTION docstring, not the module's (#14597
    re-review, P3-NOTE): this docstring said "the module docstring" while the code
    reads ``expansion_merge.fuse_expansion_pass.__doc__``, and the module docstring
    carries no condition at all — so the sentence described prose the dict does not
    grade. Wording only; the extraction was right.

    ⛔ "EVERY" IS THE CLAIM THAT BROKE (#14597 re-review, P2-1): the docstring said
    it while the dict held three sites, and the fourth (``docs/00_index.md``, which
    publishes the condition AND repeated the "cannot re-publish it at the other
    site" claim) was ungraded — nothing would have noticed if it stopped being
    correct. It is graded now, so the sentence and the dict agree.

    Named EXCLUSIONS, stated rather than silent: ``tests/``' own comments (e.g.
    ``tests/test_entity_key_expansion.py:781`` repeats the formula) are the guard's
    own record, not a site a reader meets the rule at; and the PR body cannot be a
    carrier because it is not in the tree, so no test can read it.
    """
    root = Path(__file__).resolve().parent.parent
    return {
        "tortoise/expansion_merge.fuse_expansion_pass": " ".join(
            (expansion_merge.fuse_expansion_pass.__doc__ or "").split()),
        "sdk._entity_key_dense_pass": _half_docstrings()["_entity_key_dense_pass"],
        "docs/evidence/14344-expansion-fusion/README.md": " ".join(
            (root / "docs" / "evidence" / "14344-expansion-fusion"
             / "README.md").read_text().split()),
        "docs/00_index.md (#14344 row)": _index_row(),
    }


def _uncorrected_refuted(clause: str) -> list[int]:
    """Offsets of every refuted-form quote in this clause that has NO correction
    marker within ``_CORRECTION_WINDOW`` characters of it.

    Per OCCURRENCE, never per clause: the exemption is the prose analogue of
    ``noqa``-on-the-offending-line, so it has to be attached to the quote it
    excuses. Grading the clause as a whole is what let a word describing something
    else (``expansion_merge``'s "can no longer be displaced") excuse a splice 279
    characters away (#14597 review, P2-1).
    """
    out = []
    for match in _REFUTED_FORM.finditer(clause):
        lo = max(0, match.start() - _CORRECTION_WINDOW)
        hi = min(len(clause), match.end() + _CORRECTION_WINDOW)
        if not _CORRECTION.search(clause[lo:hi]):
            out.append(match.start())
    return out


def _pair_condition_violations(text: str) -> list[str]:
    """Every clause that states the two-term advantage without the true pair
    condition, and every refuted-form quote that is NOT marked as the refuted one.

    ⛔ CLAUSE-SCOPED ON PURPOSE (#14597 review P2). The first cut asserted
    ``pair_condition.search(whole_carrier_text)``, and a carrier that states the
    bound twice was satisfied by ONE surviving true occurrence — so the refuted
    form could be put back at the site a reader hits FIRST while the suite stayed
    green (reproduced at ``3c2d3464``: that carrier's own row stayed ``12 passed``).
    Grading per clause is what makes the pin about every SITE rather than about the
    carrier's existence.

    ⛔ AND CLAUSE-SCOPING WAS NOT ENOUGH (#14597 re-review, P2-1, measured at
    ``36f61041``): the exemption was read off the whole clause, so the SECOND pair
    site — inside a clause whose "can no longer be displaced" describes a BOUND, not
    a retraction — was ungraded in BOTH directions (splice a refuted form there, or
    delete the pair form there: **0 violations**). The exemption is therefore
    per-OCCURRENCE and distance-bounded (``_uncorrected_refuted``); the
    win-condition rule has no exemption at all, because a clause that introduces the
    condition must carry it, and every carrier satisfies that today (measured: every
    ``_WIN_ARITHMETIC`` clause carries the pair form).

    This function is also the anti-over-block proof: three of the four carriers
    QUOTE the refuted form today, each with its correction marker beside the quote,
    so a clean run exercises the exemption instead of proving the string absent
    everywhere.

    ⛔ AND PRESENCE OF THE EXPRESSION WAS NOT PRESENCE OF THE CLAIM (#14597
    re-review, P2-2, measured at ``26276a06aaeb``): every occurrence is now also
    graded for its RELATION, because a carrier could publish
    ``r < (61+h1)(61+h2)/(122+h1+h2) - 61`` — the reciprocal, i.e. the refuted
    form's error in the other direction — at all five sites with **0 violations**.

    Stated residual, not a silent one: a NEW site that states the bound while
    using neither a ``_WIN_ARITHMETIC`` phrase, the pair expression, nor the
    refuted form is not graded here (it re-words past the trigger set). Closing
    that fully would need a parser for the prose, and the measured defects — the
    refuted form returning at the headline, returning at a second site inside a
    "no longer" clause, and the TRUE expression published with the WRONG relation
    — are what this closes.
    """
    violations = []
    for clause in _CLAUSE_BOUNDARY.split(text):
        # ⛔ THE DIRECTION FIRST, per occurrence (#14597 re-review, P2-2). This is
        # the last dimension of the same sentence, and the load-bearing one: the
        # refuted form was wrong BECAUSE of its relation. Graded here rather than
        # only inside `_PAIR_CONDITION` because a clause-level presence check cannot
        # see a site whose relation was flipped — the pattern no longer matches, so
        # the clause would simply fall through to the win-phrase rule and a site
        # without one would go ungraded.
        reversed_pair = [
            match for match in _PAIR_EXPRESSION.finditer(clause)
            if not _PAIR_RELATION_TRUE.search(
                clause[max(0, match.start() - _PAIR_RELATION_LOOKBEHIND)
                       :match.start()])]
        if reversed_pair:
            start = reversed_pair[0].start()
            violations.append(
                "publishes the pair expression with a relation other than the true "
                "`r >` — the two-term id BEATS a worse-ranked single-list candidate, "
                "TIES it at equality and LOSES below, so `r <` is the reciprocal of "
                "what `rrf_fusion` does: "
                f"{clause[max(0, start - _PAIR_RELATION_LOOKBEHIND):][:180]!r}")
            continue
        uncorrected = _uncorrected_refuted(clause)
        if uncorrected:
            violations.append(
                "re-publishes the refuted closed form `r < (h1 + h2 - 61) / 2` "
                "without a correction marker beside the quote (within "
                f"{_CORRECTION_WINDOW} chars): {clause[uncorrected[0]:][:180]!r}")
            continue
        if _PAIR_CONDITION.search(clause):
            continue
        if any(phrase in clause for phrase in _WIN_ARITHMETIC):
            violations.append(
                "states the two-term advantage without the PAIR condition "
                "`r > (61+h1)(61+h2)/(122+h1+h2) - 61` in the same clause: "
                f"{clause[:180]!r}")
    return violations


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
    string instead of the arithmetic. That the form is the ENGINE's is
    `test_the_published_pair_condition_is_the_fusions_arithmetic`.

    ⛔ AND THE PIN ITSELF WAS A PRESENCE CHECK, which this row's own docstring
    claimed was not the case ("so a carrier cannot re-publish it") until the
    #14597 re-review MEASURED the opposite at ``3c2d3464``: the true form stated
    twice in one docstring meant one surviving occurrence satisfied the regex for
    the whole carrier, so the refuted form could be restored at the FIRST bullet —
    the sentence a reader hits first — with this row still green. The grade is
    therefore ``_pair_condition_violations``.

    ⛔ AND CLAUSE-SCOPING WAS STILL NOT SITE-SCOPING — the defect MOVED instead of
    going away, which the next round measured at ``36f61041`` (#14597 re-review,
    P2-1): the second pair site sits in a clause whose "can no longer be displaced"
    describes a BOUND, not a retraction, and the clause-wide exemption accepted that
    word as a correction, so a splice there produced **0 violations** (and deleting
    the pair form there, also 0). Two things close it: the exemption is now
    distance-bounded per OCCURRENCE (``_uncorrected_refuted``), and the fourth
    published site — the ``docs/00_index.md`` row, which this row's docstring had
    been claiming was graded — is now actually a carrier. Both are executed rather
    than asserted:
    `test_the_pair_condition_pin_is_clause_scoped_not_a_presence_check` mutates every
    occurrence in every carrier, and `test_the_index_row_is_graded_as_a_carrier_not_merely_claimed`
    grades the row's own reachability.

    ⛔ AND PRESENCE OF THE EXPRESSION WAS NOT PRESENCE OF THE CLAIM (#14597
    re-review, P2-2, measured at ``26276a06aaeb``): ``_PAIR_CONDITION`` matched only
    the right-hand expression, so the relation — the ONE token that carries the
    claim's meaning, and the one the refuted form got wrong — was inside no regex
    and inside no assertion. Flipping ``r >`` → ``r <`` at all five sites left
    ``_pair_condition_violations`` at **0 violations** and all fifteen rows green.
    The relation is now part of ``_PAIR_CONDITION`` AND graded per occurrence in
    ``_pair_condition_violations``, so this row's "the FORM is true" is the form
    INCLUDING its direction.
    """
    carriers = _carriers()
    unbounded = "outranks either pass alone"
    for where, text in carriers.items():
        assert unbounded not in text, (
            f"{where} still states {unbounded!r} — two terms beat a single-list "
            "rank only under the pair condition "
            "r > (61+h1)(61+h2)/(122+h1+h2) - 61")
        assert _ONE_RANK_BOUND.search(text), (
            f"{where} states the two-term advantage without its bound — the "
            "`<= 61` clause is what makes the SYMMETRIC half of the claim true")
        assert _PAIR_CONDITION.search(text), (
            f"{where} publishes the `<= 61` case without the PAIR condition "
            "`r > (61+h1)(61+h2)/(122+h1+h2) - 61` — the `<= 61` reading is the "
            "symmetric case only, and a shared id holding the TOP slot in either "
            "list beats every single-list term whatever its other rank (round 18, P2-1)")
        violations = _pair_condition_violations(text)
        assert not violations, \
            f"{where} — " + " | ".join(violations)
        # Coverage FLOOR, so a re-word cannot silently reduce this row back to the
        # per-carrier presence checks above without anyone noticing: the grader must
        # actually inspect at least one clause here.
        inspected = [
            clause for clause in _CLAUSE_BOUNDARY.split(text)
            if _REFUTED_FORM.search(clause)
            or any(phrase in clause for phrase in _WIN_ARITHMETIC)]
        assert inspected, (
            f"{where} no longer contains a clause this pin can grade — the prose "
            "was re-worded past `_WIN_ARITHMETIC` and past the refuted form, so the "
            "row would pass vacuously")


def test_the_pair_condition_pin_is_clause_scoped_not_a_presence_check():
    """Non-vacuity for the row above, EXECUTED rather than remembered, over EVERY
    site of EVERY carrier (#14597 re-review, P2, then P2-1, then P2-2).

    Three attacks, all measured rather than imagined:

    * at ``3c2d3464`` the pin was a whole-carrier presence check, so restoring the
      refuted form at the site a reader hits FIRST left the later paragraph to
      satisfy the regex and the suite stayed green;
    * at ``36f61041`` it was clause-scoped, and the defect MOVED rather than went
      away: the second pair site lives in a clause that also says "can no longer be
      displaced" — a BOUND change, not a retraction — and that word excused a
      splice there. Measured at that head: splice at the first site → 2 violations;
      splice at the second → **0**. Delete the pair form at the second site → 0.
    * at ``26276a06aaeb`` the pin graded the EXPRESSION and never the RELATION, so
      flipping ``r >`` → ``r <`` at every one of the five sites left
      ``_pair_condition_violations`` empty — the reciprocal of the engine published
      with all fifteen rows green. The relation is graded per occurrence now, and
      this row mutates it at every site.

    So this row now mutates every occurrence in every carrier — splice, and flip —
    and separately pins the MECHANISM of the second-site hole (the laundering word
    is still in that clause, still outside ``_CORRECTION_WINDOW``) rather than only
    its symptom.

    It also asserts the exemption is exercised rather than merely unused: three of
    the carriers really do quote the refuted form today, each with its marker
    beside the quote, and citing it that way is not a violation.
    """
    refuted_form = "r < (h1 + h2 - 61) / 2"
    for where, carrier in _carriers().items():
        assert not _pair_condition_violations(carrier), where

        # The exemption is LIVE: a quoted refuted form with its marker BESIDE it is
        # not a violation, and that is exercised here rather than proved absent.
        for clause in _CLAUSE_BOUNDARY.split(carrier):
            if _REFUTED_FORM.search(clause):
                assert _CORRECTION.search(clause), (
                    f"{where}: a clause quotes the refuted form with NO correction "
                    f"marker in it at all: {clause[:180]!r}")
                assert not _uncorrected_refuted(clause), (
                    f"{where}: a quoted refuted form has no marker within "
                    f"{_CORRECTION_WINDOW} chars of it — that is a violation, not "
                    f"an exemption: {clause[:180]!r}")

        occurrences = list(_PAIR_CONDITION.finditer(carrier))
        assert occurrences, f"{where} no longer states the true pair form"
        for index, match in enumerate(occurrences, start=1):
            mutated = (carrier[:match.start()] + refuted_form
                       + carrier[match.end():])
            violations = _pair_condition_violations(mutated)
            assert violations, (
                f"{where}: the refuted form restored at occurrence {index} of "
                f"{len(occurrences)} went UNGRADED — a splice there leaves the pin "
                "green, which is the defect this row exists for")
            assert any("refuted closed form" in v for v in violations), (
                f"{where}: occurrence {index} reddened for something other than "
                f"the splice: {violations}")

            # ⛔ AND THE DIRECTION, at the SAME site (#14597 re-review, P2-2). The
            # pin graded the expression and never the relation, so a carrier could
            # publish the RECIPROCAL of `rrf_fusion`'s arithmetic here and stay
            # green — the same defect class as the refuted form, which was wrong
            # precisely in its direction. Group 1 is the relation, group 2 the
            # expression, so the flip changes ONE character's meaning and nothing
            # else about the site.
            flipped = (carrier[:match.start()] + "r < " + match.group(2)
                       + carrier[match.end():])
            assert flipped != carrier, (
                f"{where}: the relation flip at occurrence {index} was a no-op — "
                "the pattern no longer carries the relation as its own group")
            reversed_violations = _pair_condition_violations(flipped)
            assert reversed_violations, (
                f"{where}: the relation flipped to `r <` at occurrence {index} of "
                f"{len(occurrences)} went UNGRADED — the carrier then publishes the "
                "reciprocal of the engine's condition and the pin cannot see it")
            assert any("relation other than the true" in v
                       for v in reversed_violations), (
                f"{where}: occurrence {index} reddened for something other than "
                f"the flipped relation: {reversed_violations}")

    # ⛔ THE SECOND-SITE MECHANISM, pinned so the window cannot silently widen: in
    # ``expansion_merge`` the clause holding the SECOND pair form also holds "no
    # longer" — a word that describes a BOUND, 279 chars from the quote — and the
    # clause-wide exemption read it as a correction. Assert the hole's ingredients
    # are still what they are: the word IS in that clause, and the splice at that
    # site IS graded, which is only possible because the marker is outside the
    # window. If the prose moves, this reds and the window must be re-measured —
    # rather than the hole re-opening quietly.
    doc = _carriers()["tortoise/expansion_merge.fuse_expansion_pass"]
    second_clause = next(c for c in _CLAUSE_BOUNDARY.split(doc)
                         if "can no longer be displaced" in c)
    pair = _PAIR_CONDITION.search(second_clause)
    assert pair, (
        "the second pair site left the 'can no longer be displaced' clause — "
        "re-measure _CORRECTION_WINDOW against wherever it is now, then update this "
        "row deliberately")
    distance = min(abs(m.start() - pair.start())
                   for m in _CORRECTION.finditer(second_clause))
    assert distance > _CORRECTION_WINDOW, (
        f"the nearest correction marker in the second-site clause is {distance} "
        f"chars from the quote, INSIDE _CORRECTION_WINDOW={_CORRECTION_WINDOW} — the "
        "clause-wide laundering hole is back, because that word now excuses a "
        "refuted form spliced in at the second site")


def test_the_index_row_is_graded_as_a_carrier_not_merely_claimed():
    """``docs/00_index.md`` publishes the condition and repeated the pin's own
    "cannot re-publish it at the other site" claim — and was NOT in ``_carriers()``
    (#14597 re-review, P2-1). A document that claims its own coverage is exactly the
    artifact that has to be read, or the claim is the only thing keeping it true.

    Reachability in BOTH directions, on the row itself: the row carries the true
    form and the ``<= 61`` half, the refuted form spliced at its pair site reds, and
    removing its ``<= 61`` half leaves the sibling row's per-carrier bound assert
    with nothing to match."""
    row = _index_row()
    assert _PAIR_CONDITION.search(row), (
        "the index row no longer publishes the true pair condition — it is not a "
        "carrier, so naming it in _carriers() would grade prose that is not there")
    assert _ONE_RANK_BOUND.search(row), (
        "the index row publishes the pair condition WITHOUT its symmetric `<= 61` "
        "half — the round-18 defect in the other direction")
    assert not _pair_condition_violations(row)

    match = _PAIR_CONDITION.search(row)
    spliced = row[:match.start()] + "r < (h1 + h2 - 61) / 2" + row[match.end():]
    assert _pair_condition_violations(spliced), (
        "the index row can re-publish the refuted form and go ungraded — it is not "
        "really a carrier")

    # The `<= 61` half is a separate assertion in the sibling row, so prove the cut
    # below is a real mutation of THIS artifact and not a no-op string replace.
    dropped = row.replace("`<= 61`-in-both", "the symmetric")
    assert dropped != row, (
        "the index row no longer spells its `<= 61` half as "
        "``\u0060<= 61\u0060-in-both`` — update this mutation, or it would assert "
        "nothing")
    assert not _ONE_RANK_BOUND.search(dropped), (
        "this mutation still matches the bound regex, so it does not reproduce the "
        "missing-half defect")


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
    below in both directions. The sibling row
    ``test_the_two_term_advantage_is_bounded_at_1based_61`` could not see it: that
    row grades the ENGINE at its own fixtures but never compares the published
    FORM with it, so the formula's error was invisible there. And the shape row
    above reads the form only for PRESENCE — it grades the string a carrier must
    contain, never the arithmetic that string claims — so this is the first
    assertion that compares the form with ``rrf_fusion`` itself. The two rows are
    not interchangeable, which the #14597 re-review measured: a carrier can satisfy
    the shape pin at one site while the bound is stated at another, and only this
    row can say the FORM is true while only the shape row can say every SITE
    carries it.

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
