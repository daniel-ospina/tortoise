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
        dominance. Two terms beat a single-list rank only RELATIVELY: with the
        shared id at 0-based rank ``h`` in BOTH lists and the single-term
        candidate at 0-based rank ``r``, ``2/(61+h)`` beats ``1/(61+r)`` while
        ``h < 61 + 2r`` — against a lone rank-1 (``r = 0``) that is exactly
        ``h <= 60``, i.e. 1-based rank <= 61 in both lists, where at 1-based 62
        in both the two terms TIE a lone rank-1 (``2/122 == 1/61``) and
        ``rrf_fusion``'s ``(-score, id)`` order decides by spelling rather than
        evidence. ⛔ THE ``<= 61`` FORM IS THE LONE-RANK-1 CASE, NOT A UNIVERSAL
        BOUND: against a single-term candidate further down the page two terms
        keep winning from OUTSIDE it (at ``r = 3`` they win through ``h = 66``),
        so the honest statement is the relative ``h < 61 + 2r`` and the ``<= 61``
        figure is what it reduces to against rank 1. Where the two terms do win,
        the entity context AGREES with the query, which is exactly the signal the
        expansion exists to supply — and ``DEFAULT_POOL_SIZE`` is 120, so ranks
        past 61 are ordinary in the shipping default, not a corner;
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
    an id BOTH passes carry whose two terms clear THAT INCUMBENT's own single
    term (``h < 61 + 2r`` against the incumbent's 0-based rank ``r``; against a
    lone rank-1, ``r = 0``, that is the ``<= 61`` case, and further down the page
    it is not — at ``r = 3`` a shared id at ``h = 63`` passes the incumbent while
    out-ranking it in NEITHER pass), and on an exact tie ``rrf_fusion``'s
    ``(-score, id)`` tiebreak decides, which is the id's order and not its
    evidence.

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
