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

import importlib.util
import json
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
    # ⛔ THE FIXTURE MUST NOT ALREADY BE IN SORTED ORDER (#14642, measured). It
    # used to be `p0..p6`, which is exactly what `list.sort()` produces, so the
    # `sort()` mutant the arm below names was a NO-OP: it left every row green,
    # including the in-place arm, because sorting an already-sorted list changes
    # neither the caller's list nor the fused order. ONE OUT-OF-ORDER PAIR is
    # what makes the mutation observable — `sorted()` moves `p0` back above
    # `p1`, which reds BOTH the expected order below and the in-place arm. The
    # precondition is asserted rather than assumed, for the same reason: a
    # fixture quietly restored to ascending order would re-inert the arm.
    pass1 = [(f"p{i}", 1.0 - i / 100) for i in (1, 0, 2, 3, 4, 5, 6)]
    expanded = [(f"x{i}", 1.0 - i / 100) for i in range(7)]
    pass1_before, expanded_before = list(pass1), list(expanded)
    assert pass1 != sorted(pass1), (
        "the pass-1 fixture is already in sorted order, so a `sort()` mutant is "
        "undetectable here — one out-of-order pair is what makes this arm "
        "discriminating")

    first = _ids(fuse_expansion_pass(pass1, expanded))

    # Every p/x pair sits at the same rank, so each pair TIES on its RRF term and
    # `(-score, id)` decides it — `p` before `x`, at every rank, including the
    # swapped one.
    assert first == ["p1", "x0", "p0", "x1", "p2", "x2", "p3", "x3",
                     "p4", "x4", "p5", "x5", "p6", "x6"], (
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
_PAIR_CONDITION = re.compile(
    r"\(\s*61\s*\+\s*h1\s*\)\s*\(\s*61\s*\+\s*h2\s*\)\s*/\s*"
    r"\(\s*122\s*\+\s*h1\s*\+\s*h2\s*\)\s*[-\u2212]\s*61")
_REFUTED_FORM = re.compile(
    r"r\s*<\s*\(\s*h1\s*\+\s*h2\s*[-\u2212]\s*61\s*\)\s*/\s*2")
_ONE_RANK_BOUND = re.compile(r"(<=|\u2264)\s*61")
#: The phrases a carrier uses to INTRODUCE the win condition. A clause carrying
#: one of them must carry the true pair form, because that is the clause a reader
#: takes the condition from — the two sites in ``expansion_merge`` and the one site
#: in each of the other two carriers, measured, not assumed.
_WIN_ARITHMETIC = (
    "beats a single-list candidate",
    "only while",
    "can pass an incumbent",
)
#: A clause may QUOTE the refuted form only if it also says, in that clause, that
#: the form is the refuted one. Deliberately narrow and explicit: the linter
#: pattern this copies (a `noqa` suppression carrying its rule code / `nosec` /
#: `//nolint`) puts the suppression ON the offending line and NAMES the rule rather
#: than blanket-silencing a file — Ruff's own ``unused-noqa`` (RUF100) exists
#: because a suppression that stops matching is itself a defect.
_CORRECTION = re.compile(
    r"\u26d4|\bfalse\b|\brefuted\b|\bwrong\b|\bdisagrees\b|\bcorrected\b"
    r"|\buntil now\b|\bno longer\b")
#: Clause boundaries as the carriers actually punctuate them. The lookahead is
#: what keeps ``i.e.`` and ``e.g.`` from splitting a clause in half: a real
#: boundary here is followed by a capital, an open paren, a bold marker, a
#: backtick span or a ⛔.
_CLAUSE_BOUNDARY = re.compile(r"(?<=[.;])\s+(?=[A-Z(\u26d4*`])")


def _carriers() -> dict:
    """Every carrier that publishes the merge rule's condition, keyed by the
    site a reader finds it at. The README and the SDK docstring are read from
    disk/source, never imported, so this stays a ``core``-surface property."""
    root = Path(__file__).resolve().parent.parent
    return {
        "tortoise/expansion_merge.fuse_expansion_pass": " ".join(
            (expansion_merge.fuse_expansion_pass.__doc__ or "").split()),
        "sdk._entity_key_dense_pass": _half_docstrings()["_entity_key_dense_pass"],
        "docs/evidence/14344-expansion-fusion/README.md": " ".join(
            (root / "docs" / "evidence" / "14344-expansion-fusion"
             / "README.md").read_text().split()),
    }


def _pair_condition_violations(text: str) -> list[str]:
    """Every CLAUSE that states the two-term advantage without the true pair
    condition, and every clause that re-publishes the refuted closed form without
    marking it as the refuted one.

    ⛔ CLAUSE-SCOPED ON PURPOSE (#14597 review P2). The first cut asserted
    ``pair_condition.search(whole_carrier_text)``, and a carrier that states the
    bound twice was satisfied by ONE surviving true occurrence — so the refuted
    form could be put back at the site a reader hits FIRST while the suite stayed
    green (reproduced at ``3c2d3464``: that carrier's own row stayed ``12 passed``).
    Grading per clause is what makes the pin about every SITE rather than about the
    carrier's existence.

    The one exemption is co-located, and this function is also the anti-over-block
    proof: all three carriers QUOTE the refuted form today, and each quote carries
    its correction marker in the same clause, so a clean run exercises the
    exemption instead of proving the string absent everywhere.

    Stated residual, not a silent one: a NEW site that states the bound while
    using neither a ``_WIN_ARITHMETIC`` phrase nor the refuted form is not graded
    here (it re-words past the trigger set). Closing that fully would need a
    parser for the prose, and the measured defect — the refuted form returning at
    the headline — is what this closes.
    """
    violations = []
    for clause in _CLAUSE_BOUNDARY.split(text):
        corrective = bool(_CORRECTION.search(clause))
        if _REFUTED_FORM.search(clause) and not corrective:
            violations.append(
                "re-publishes the refuted closed form `r < (h1 + h2 - 61) / 2` "
                f"without saying it is the refuted one: {clause[:180]!r}")
        if corrective or _PAIR_CONDITION.search(clause):
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
    therefore ``_pair_condition_violations``, CLAUSE-scoped in both directions, and
    `test_the_pair_condition_pin_is_clause_scoped_not_a_presence_check` is the
    executed proof that the same restoration now reds.
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
    """Non-vacuity for the row above, EXECUTED rather than remembered (#14597
    re-review, P2).

    The attack it reproduces is the one that was MEASURED at ``3c2d3464``: restore
    the refuted closed form at the site a reader hits FIRST, leave the later
    paragraph (which still states the true form) untouched, and the suite stayed
    green because the pin was a whole-carrier presence check. The mutation below
    does exactly that mechanically — it replaces the FIRST occurrence of the true
    form and keeps the second — so it asserts the old pin would STILL be satisfied
    (``_PAIR_CONDITION.search`` on the mutated carrier) and the new one is not.

    It also asserts the exemption is exercised rather than merely unused: the
    carrier really does quote the refuted form today, and quoting it with its
    correction marker is not a violation.
    """
    doc = _carriers()["tortoise/expansion_merge.fuse_expansion_pass"]
    assert not _pair_condition_violations(doc)

    # The exemption is LIVE: this carrier quotes the refuted form, in a clause that
    # names it as the refuted one, and that is not a violation.
    quoted = [clause for clause in _CLAUSE_BOUNDARY.split(doc)
              if _REFUTED_FORM.search(clause)]
    assert quoted, (
        "the carrier no longer quotes the refuted form — this row would then not "
        "exercise the co-located-correction exemption at all")
    assert all(_CORRECTION.search(clause) for clause in quoted), (
        "a correction clause lost its correction marker: "
        + " | ".join(c[:180] for c in quoted if not _CORRECTION.search(c)))

    first = _PAIR_CONDITION.search(doc)
    assert first, "the carrier no longer states the true pair form"
    mutated = doc[:first.start()] + "r < (h1 + h2 - 61) / 2" + doc[first.end():]
    assert _PAIR_CONDITION.search(mutated), (
        "the mutation removed EVERY true occurrence — it would not reproduce the "
        "headline-site attack, which leaves the later paragraph intact")
    violations = _pair_condition_violations(mutated)
    assert violations, (
        "the refuted form restored at the carrier's FIRST site went ungraded — the "
        "pin is a presence check again, which is the defect this row exists for")
    assert any("refuted closed form" in v for v in violations), violations


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


# ── The committed RECEIPT, and the table that quotes it (#14619 review F3) ──
# ⛔ WHY THIS SECTION EXISTS. `tools/measure_expansion_merge_14344.py` writes the
# artifact, and BOTH `docs/` and `tools/` are in `NON_PYTHON_PREFIXES`, so a
# re-run with a changed `SHAPES`/`KS`/`TRIALS_PER_SHAPE` — or any edit to the tool
# — re-staled the published record with nothing to notice. Measured at the
# reviewed head: `grep -c merge-rule-measurement` over this file,
# `tests/test_entity_key_expansion.py`, `tests/test_ci_selection.py` and
# `config/ci-surfaces.yml` returned `0 0 0 0`. The numbers were CORRECT then,
# which is exactly the problem: an unguarded correct number rots in silence.
_ROOT = Path(__file__).resolve().parent.parent
_RECEIPT = _ROOT / "docs" / "evidence" / "14344-expansion-fusion" / "merge-rule-measurement.json"
_README = _ROOT / "docs" / "evidence" / "14344-expansion-fusion" / "README.md"
_MEASUREMENT_TOOL = _ROOT / "tools" / "measure_expansion_merge_14344.py"

#: §2's four columns in the order the README publishes them, mapped to the
#: receipt's `summary` field names. The ORDER is load-bearing: a cell compared
#: against the wrong field could pass on a coincidence of magnitudes.
_MEASUREMENT_COLUMNS = (
    "pass1_retained_mean",
    "pass1_only_retained_mean",
    "new_admitted_mean",
    "pass1_rank0_position_mean",
)

#: A §2 row: its `k` cell, then the four old→new cells.
_TABLE_ROW = re.compile(r"^\|\s*(?P<k>\d+)\s*\|(?P<cells>.+)\|\s*$", re.MULTILINE)
_NUMBERS = re.compile(r"\d+\.\d+")


def _measurement_tool():
    """`tools/measure_expansion_merge_14344.py`, loaded by PATH.

    By path because `tools/` is not a package, and through `importlib` rather
    than `sys.path` so this cannot shadow an installed module. The tool inserts
    the repo root on `sys.path` itself at import time.
    """
    spec = importlib.util.spec_from_file_location(
        "_measure_expansion_merge_14344_under_test", _MEASUREMENT_TOOL)
    assert spec is not None and spec.loader is not None, (
        f"could not build an import spec for {_MEASUREMENT_TOOL} — the tool the "
        "committed receipt rests on is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _published_measurement_rows(text: str) -> dict[int, list[tuple[float, float]]]:
    """§2's four `(old, new)` pairs per `k`, read out of the README's prose.

    A row that does not yield exactly two decimals per cell is SKIPPED rather
    than guessed at, so the caller's row-count assertion is what reports a table
    this parser can no longer read — never a silently empty comparison.
    """
    rows: dict[int, list[tuple[float, float]]] = {}
    for match in _TABLE_ROW.finditer(text):
        cells = [cell.strip() for cell in match.group("cells").split("|")]
        if len(cells) != len(_MEASUREMENT_COLUMNS):
            continue
        pairs: list[tuple[float, float]] = []
        for cell in cells:
            numbers = _NUMBERS.findall(cell.replace("**", ""))
            if len(numbers) != 2:
                pairs = []
                break
            pairs.append((float(numbers[0]), float(numbers[1])))
        if len(pairs) == len(_MEASUREMENT_COLUMNS):
            rows[int(match.group("k"))] = pairs
    return rows


def _measurement_table_violations(receipt: dict, text: str) -> list[str]:
    """Every §2 cell that disagrees with the receipt it quotes, and every `k` the
    table stops publishing. Both arms of every cell are graded, at the two
    decimals the table states."""
    rows = _published_measurement_rows(text)
    summary = receipt["summary"]
    violations: list[str] = []
    for k in receipt["ks"]:
        if k not in rows:
            violations.append(f"§2 publishes no row for k={k}")
            continue
        for field, (old, new) in zip(_MEASUREMENT_COLUMNS, rows[k], strict=True):
            for arm, stated in (("old", old), ("new", new)):
                actual = summary[f"k={k}"][field][arm]
                if f"{actual:.2f}" != f"{stated:.2f}":
                    violations.append(
                        f"k={k} {field}[{arm}]: the table says {stated:.2f}, the "
                        f"receipt says {actual:.4f}")
    return violations


def _shift_first_new_value(text: str, k: int) -> str:
    """A MUTANT: bump `k`'s first column's NEW value by 0.01 in the table.

    Derived from the parsed row rather than from a hardcoded substring, so it
    keeps working when the table is re-derived — and it raises rather than
    returning the text unchanged, because a no-op mutant would make the
    non-vacuity leg below vacuous itself.
    """
    for match in _TABLE_ROW.finditer(text):
        if int(match.group("k")) != k:
            continue
        cells = match.group("cells")
        numbers = list(_NUMBERS.finditer(cells))
        assert len(numbers) >= 2, f"k={k}'s row carries fewer than two decimals: {cells!r}"
        target = numbers[1]
        bumped = f"{float(target.group()) + 0.01:.2f}"
        return (text[:match.start("cells")] + cells[:target.start()] + bumped
                + cells[target.end():])
    raise AssertionError(f"§2 publishes no row for k={k} — nothing to mutate")


def test_the_committed_measurement_is_the_tools_own_output():
    """#14619 review F3 (P2): the artifact had NO pin — nothing re-derived it.

    Compared as the SERIALIZATION the tool's own `main()` writes
    (``json.dumps(receipt, indent=2, sort_keys=True) + "\\n"``), not as a subset
    of fields: a changed key set, a moved seed, a re-ordered shape list or a
    re-run that produces different means all red here. That is the difference
    between "the artifact exists" and "the artifact is what this tool produces
    at this head".
    """
    receipt = _measurement_tool().measure()
    expected = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    assert _RECEIPT.read_text(encoding="utf-8") == expected, (
        "the committed receipt is not what the committed tool produces at this "
        "head. Re-run `TORTOISE_TEST_CARVE_OUT=1 uv run python "
        "tools/measure_expansion_merge_14344.py` and commit the artifact WITH the "
        "tool change — never edit either one alone, which is how this record "
        "could re-stale with every pin green")


def test_the_published_measurement_table_is_the_committed_receipt():
    """#14619 review F3 (P2): §2's table is prose over the artifact, and nothing
    read it — the numbers were correct at the reviewed head, which is precisely
    why an unguarded copy of them was going to rot.

    Graded CELL BY CELL (both arms, all four columns, both `k`) against the
    receipt, and the receipt is itself pinned to the tool's output by
    ``test_the_committed_measurement_is_the_tools_own_output`` — so the chain
    README → receipt → tool is closed end to end rather than at one link.

    ⛔ NON-VACUITY IS EXECUTED, not asserted, on the three shapes a real drift
    takes: one README cell moved, one receipt value moved, and a row deleted
    from the table. Each must be REPORTED by the same helper this test calls on
    the clean pair.
    """
    receipt = json.loads(_RECEIPT.read_text(encoding="utf-8"))
    text = _README.read_text(encoding="utf-8")

    rows = _published_measurement_rows(text)
    assert len(rows) == len(receipt["ks"]), (
        f"§2 publishes {len(rows)} readable row(s) for {len(receipt['ks'])} `k` "
        f"values — a table this parser cannot read grades NOTHING, so read "
        f"`_published_measurement_rows`'s contract before trusting a green run: "
        f"{rows!r}")

    violations = _measurement_table_violations(receipt, text)
    assert not violations, (
        "§2's measured table disagrees with the receipt it quotes — re-derive the "
        "row with `tools/measure_expansion_merge_14344.py` rather than editing "
        f"the prose: {violations}")

    # (1) a README cell moved by one hundredth.
    shifted = _shift_first_new_value(text, receipt["ks"][0])
    assert shifted != text, "the cell mutant did not change the table"
    assert _measurement_table_violations(receipt, shifted), (
        "a §2 cell shifted by 0.01 went UNREPORTED — the table pin is decoration")

    # (2) a RECEIPT value moved (the direction a re-run/parameter edit arrives).
    moved = json.loads(json.dumps(receipt))
    moved["summary"]["k=10"]["new_admitted_mean"]["new"] += 0.5
    assert _measurement_table_violations(moved, text), (
        "a receipt value moved by 0.5 went UNREPORTED against the table")

    # (3) a row deleted from the table.
    dropped = "\n".join(
        line for line in text.splitlines()
        if not line.startswith(f"| {receipt['ks'][-1]} |"))
    assert len(_published_measurement_rows(dropped)) == len(rows) - 1, (
        "the dropped-row mutant removed no row — it would prove nothing")
    dropped_violations = _measurement_table_violations(receipt, dropped)
    assert dropped_violations, (
        "a deleted §2 row went UNREPORTED — a table that publishes fewer `k` "
        "values than the receipt holds must red")


def _counters_measure_aggregates(module) -> set[str]:
    """The counter names `measure()` ACTUALLY accumulates, read off a one-trial
    run rather than hardcoded — the point is to mirror the aggregation, not to
    restate it. `n` is dropped: it counts trials, it is not a measurement."""
    receipt = module.measure(trials_per_shape=1)
    shape = next(iter(receipt["per_shape"].values()))
    return set(next(iter(shape["old"].values()))) - {"n"}


def test_the_instrument_reports_only_the_counters_it_aggregates():
    """#14619 review (the non-blocking observation, taken as a defect):
    `_score_rule` returned `pass1_only_rows_in_head` and
    `expansion_only_rows_in_head`, cell-for-cell duplicates of
    `pass1_only_retained` / `new_admitted`, and `measure()` consumed NEITHER.

    Why that is worth a pin rather than a shrug: this tool's whole output is a
    set of counters, so a counter no aggregation reads is indistinguishable, to a
    reader of the function, from one that was measured and reported — and a
    duplicated counter also invites a future edit to move ONE of the pair and
    leave the other stating the old number.

    Two arms, both derived rather than hardcoded:
      (a) `_score_rule`'s key set == what `measure()` aggregates;
      (b) the committed receipt's own cell key set carries nothing outside that
          set — the artifact cannot gain a counter the tool does not aggregate.

    ⛔ NON-VACUITY: on the pre-fix code `_score_rule` returned SIX keys against
    the four `measure()` consumes, so arm (a) reds (measured: deleting the two
    keys is what this commit does, and the pre-fix tool reproduced the committed
    receipt byte-for-byte, i.e. the deletion moved no number).
    """
    module = _measurement_tool()
    consumed = _counters_measure_aggregates(module)
    assert len(consumed) >= 3, (
        f"`measure()` aggregated only {sorted(consumed)} — a set this small means "
        "the tool stopped accumulating, not that the instrument is clean")

    merged = [("a", 1.0), ("b", 0.9), ("c", 0.8)]
    reported = set(module._score_rule(merged, {"a", "b"}, {"b", "c"}, 2, "a"))
    assert reported == consumed, (
        f"`_score_rule` reports {sorted(reported)} but `measure()` aggregates "
        f"only {sorted(consumed)}. A counter nothing aggregates reads as "
        f"measured without being reported: {sorted(reported - consumed)}")

    receipt = json.loads(_RECEIPT.read_text(encoding="utf-8"))
    shape = next(iter(receipt["per_shape"].values()))
    published = set(next(iter(shape["old"].values()))) - {"n"}
    assert not published - consumed, (
        "the committed receipt publishes counters the tool does not aggregate — "
        f"the artifact and the instrument have drifted: {sorted(published - consumed)}")
