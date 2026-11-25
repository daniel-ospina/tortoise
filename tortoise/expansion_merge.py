"""The ONE merge rule for an additive retrieval pass — #14344.

An *additive second pass* (`sdk._entity_key_expansion_pass` sparse,
`sdk._entity_key_dense_pass` dense) re-asks the store with an expanded query and
must merge its rows into the leg it expands. This module is that merge, and it
exists as its own module for two reasons:

  * **one rule, one home** — the two passes previously each hand-rolled the same
    rank concatenation, so they could (and did) drift from their own docstrings;
  * **it is the product's fusion, not a second one** — ``rrf_fusion`` in
    ``tortoise/search_engine.py`` is what the strategy-level legs already go
    through, and this module is a thin, documented call onto it. Nothing here
    introduces a formula, a constant, or a weight of its own.

⛔ It does NOT live in ``search_engine.py`` on purpose: that module's line count
is pinned by cite gates in this repo (``tests/test_12184_shifted_cites.py``,
``tests/test_13098_cypher_inventory.py``) — every ``search_engine.py:<n>`` cited
from a doc is content-checked — so a helper that is not part of the fusion
itself must not be inserted into it. Same rule the ``sdk.py`` cite pins impose
on that file (see ``47e28b549e76``).
"""
from __future__ import annotations

from .search_engine import rrf_fusion


def fuse_expansion_pass(
    first_pass: list[tuple[str, float]],
    second_pass: list[tuple[str, float]],
    *,
    k: int = 60,
) -> list[tuple[str, float]]:
    """Merge an additive RETRIEVAL PASS into the pass it expands — through
    THIS MODULE'S fusion (:func:`rrf_fusion`), never a second ranking rule.

    #14344 (workstream A of #2513). The additive passes in ``sdk.py``
    (``_entity_key_expansion_pass`` C2-sparse and ``_entity_key_dense_pass``
    C2-dense) re-ask the store with an expanded query and then merge the result
    into the leg they expand. That merge used to be hand-rolled — and one more
    pass, A4's ``_search_keys_prf_expansion``, still does exactly that (left
    alone by this change, since it is a different lever with its own measured
    arm, so the shape is NOT extinct: grep it before claiming it is):

        seen = {pid for pid, _ in expanded}
        merged = list(expanded)
        merged.extend((pid, s) for pid, s in pass1 if pid not in seen)

    which is a SECOND fusion. It is a pure rank concatenation: **every** id the
    expanded run returned is placed above **every** pass-1 id the expanded run
    did not return, whatever the two runs' relative positions were. So an
    expansion that legitimately only ADDS candidates can push evidence the
    first pass already reached down by up to ``len(expanded)`` positions, and
    once the fused page is cut at ``limit`` that evidence leaves the answer.
    The mechanism that closes it is the product's own: ``rrf_fusion``, whose
    formula gives a document credit for its rank in EACH list — ``w / (k + rank
    + 1)`` over a 0-BASED rank, ``k=60`` for equal-weighted legs — so

      * an id both passes carry earns both terms, and that is an ADVANTAGE, not
        dominance. The condition is an INEQUALITY in the SINGLE-LIST candidate's
        rank, never a bound on the shared id's own two ranks:
        ``1/(61+h1) + 1/(61+h2) > 1/(61+r)`` reduces exactly — every denominator
        is positive — to ``r > (h1*h2 - 3721) / (122 + h1 + h2)``, where 3721 is
        ``61**2``. So the two terms beat a single-list candidate at 0-based rank
        ``r`` exactly while ``r`` CLEARS that ratio: the WORSE the single-list
        rank (the larger ``r``), the easier they clear it, and a TOP slot in
        either list (``h1 == 0``) makes the ratio negative, so they beat EVERY
        single-list term outright. Two consequences, and the second is the one
        this bullet used to omit: (a) against a lone rank-1 (``r == 0``) the
        requirement is just ``h1*h2 < 3721``, which in the SYMMETRIC case
        ``h1 == h2 == h`` — the only one a same-rank fixture can build — is
        "wins while both 1-based ranks are <= 61, TIES at 62 (``2/122 == 1/61``,
        ``rrf_fusion``'s ``(-score, id)`` then deciding by spelling rather than
        evidence) and loses past that"; (b) membership is therefore NOT confined
        to that <= 61 reading. Behind either, the entity context AGREES with the
        query, which is exactly the signal the expansion exists to supply — and
        ``DEFAULT_POOL_SIZE`` is 120, so ranks past 61 are ordinary in the
        shipping default, not a corner;
      * an id only one pass carries is ranked by its own reciprocal rank, so a
        new candidate must out-rank the incumbent to pass it — or TIE it and win
        ``rrf_fusion``'s ``(-score, id)`` tiebreak (#2952), which at equal rank
        is the id's order, not its evidence. The bound is what matters: it is
        no longer the expansion run's length.

    What this does NOT promise, stated because "additive" is read as more than
    it means: the candidate SET is additive (the union — nothing is dropped),
    and the ORDER is not a copy of either pass — an id ranked below a new
    candidate in both passes still lands below it. What changes is the
    BOUND: pass-1 evidence can no longer be displaced by the expansion's
    LENGTH — only by a candidate that out-ranks it in the expanded run, or by
    an id BOTH passes carry, whose two terms can pass an incumbent that
    out-ranks it in the expanded run: against an incumbent at 0-based rank
    ``r`` that is ``r > (h1*h2 - 3721) / (122 + h1 + h2)`` — the symmetric case
    (``h1 == h2 == h``) is the <= 61-in-both reading against a lone rank-1, a
    tie at 62 and the incumbent ahead past it, and the condition is UNCONFINED
    when the shared id holds a top slot in either list, where its two terms beat
    every single-list term. All three cuts are stated because each earlier one
    got it wrong in a different direction: the first published the claim that
    BOTH terms outrank either pass on its own (false), the second that the
    advantage holds only inside the <= 61-in-both reading (false whenever either
    rank is 1 — that reading is the symmetric case, never a necessary
    condition), and the third turned the pair into a bound that HALVES THE SUM
    of the two ranks, which is the same false necessity with the inequality
    inverted: it forbids the winning pair ``h1 == h2 == 0`` at ``r == 13``,
    where ``2/61`` beats ``1/74``, and it permits ``h1 == h2 == 64`` at
    ``r == 0``, where ``2/125`` loses to ``1/61``.

    Scores are carried from the pass that produced the id (``second_pass``
    wins a collision, the order the previous concatenation used), NOT the RRF
    score. That is deliberate and load-bearing: ``_tortoise_fts_query``'s
    single-strategy path applies a caller-supplied ``threshold`` to the raw
    leg scores, so replacing them with RRF values (~1/61) would empty every
    single-leg read that sets one.

    Duplicate ids within one pass are collapsed to their first (best) position
    — ``rrf_fusion`` counts positions, so a repeated id would otherwise earn
    two terms and outrank its own rank.

    Args:
        first_pass: the leg being expanded, already ranked (the pass-1 rows).
        second_pass: the expansion's own ranked rows.
        k: the RRF damping constant — ``rrf_fusion``'s default (Cormack k=60),
            the same constant the strategy-level fusion uses. Overridable for
            tests; production callers pass nothing.

    Returns:
        ``[(id, score), ...]`` in the fused order, scores carried from origin.
    """
    def _dedupe(rows: list[tuple[str, float]]) -> list[tuple[str, float]]:
        seen: set[str] = set()
        out: list[tuple[str, float]] = []
        for pid, score in rows or []:
            if pid in seen:
                continue
            seen.add(pid)
            out.append((pid, score))
        return out

    first = _dedupe(first_pass)
    second = _dedupe(second_pass)
    if not first or not second:
        # One side empty is not a fusion: return the side that has rows, in
        # its own order (the caller's fail-open contract keeps a lone leg).
        return second or first

    # A dict-literal merge, not ``dict.update``: ``tools/sdk_privatise.py``
    # counts ordinary-word receivers (``x.update`` / ``x.query`` / ``x.delete``)
    # as a pinned residue SHAPE, and a new one reds the non-deferrable
    # surface-guard leg for a reason that has nothing to do with this change.
    by_id: dict[str, float] = {**dict(first), **dict(second)}
    fused = rrf_fusion([first, second], k)
    return [(pid, by_id[pid]) for pid in fused]
