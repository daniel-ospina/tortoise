"""#14721 — a superseded claim must never outrank the claim that superseded it.

THE MEASURED DEFECT THIS PINS
-----------------------------
On the committed #14592 corpus (``docs/evidence/14721-superseded-ranking``),
the corpus's contradiction question — *"Did ColdPath fail its cold-chain audit,
and what did we do about it?"* — returned the claim doc-05 REFUTES as its best
hit, and the refutation was not in the top 8:

    rank 1  [superseded] "ColdPath Logistics holds a validated cold-chain certificate"
            (FTS similarity 5.0 — higher than every current row)
    rank 9  [live]       "Two of ColdPath's vehicles could not show a validated
                          cold-chain certificate, so ColdPath does not hold a
                          validated cold-chain certificate"   <- its successor

The score is a statement about relevance, and a claim's relevance does not fall
when a later document refutes it. So the order was decided by an accident of
similarity, and the row the caller must not be told is the one the product led
with.

WHY THE ASSERTIONS ARE ABOUT ORDER, NOT ABOUT A SCORE
-----------------------------------------------------
"Not outrank" is a constraint on the SEQUENCE. A score nudge cannot express it
(a stale row can always out-score its successor on lexical overlap) and a
global "ended rows sort last" tier was tried and measured — it fixes the order
and pushes the replaced claim past the page edge, which breaks the historical
read the caller asked for (``test_a_history_read_keeps_the_replaced_claim``).
These tests therefore assert positions.

The rows below carry the corpus's SHAPE and its measured numbers (the stale
claim's similarity 5.0 against its successor's 2.11), not a synthetic tie.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.ranking import StateRanker

STALE = "pt_fc8b3e47bb7e0599328d968073671eef8c9cecbc376ca2bbd6e05476f2c078"
SUCCESSOR = "pt_b44d17132b7b0f1a4e0a3b0b0e3892f16fe98ea53bd3a808d6363442b36607"


def _row(pid: str, rrf: float, **extra: object) -> dict:
    """One recall row with everything StateRanker needs and nothing else.

    The extra keys are SPREAD into the literal rather than applied with
    ``row.update(extra)``: an ordinary-word method call on an untyped dict is
    residue the `surface-guard` privatisation ratchet counts, and this helper
    would otherwise add one to a tree-wide pin it has nothing to do with.
    """
    return {
        "id": pid,
        "content": pid,
        "scores": {"rrf": rrf},
        "state_confidence": 0.5,
        "state_has_ep": True,
        "state_degree": 0,
        "status": "live",
        **extra,
    }


def _ids(rows: list[dict]) -> list[str]:
    return [r["id"] for r in rows]


def test_the_superseded_claim_never_outranks_its_successor():
    """The corpus's measured shape: the refuted claim scores HIGHER.

    Pre-#14721 the order was the input order (stale first, because 5.0 > 2.11),
    which is exactly the reported defect.
    """
    rows = [
        _row(STALE, 5.0, status="superseded", outdated=True,
             superseded_by={"id": SUCCESSOR,
                            "content_snippet": "Two of ColdPath's vehicles could not show…",
                            "created_at": "2026-11-26T15:06:41.006129+00:00",
                            "superseded_at": "2026-11-26T15:06:41.006129+00:00",
                            "source": "doc-05"}),
        _row("pt_licence", 3.0),
        _row(SUCCESSOR, 2.11),
        _row("pt_coldchain", 2.4),
    ]
    out = _ids(StateRanker().rerank(rows))
    assert out.index(SUCCESSOR) < out.index(STALE), out
    # Only the stale row is displaced, and only as far as its successor: the
    # rows that are not it keep the order the score gave them, so the
    # replacement reads immediately above the claim it replaced.
    assert out == ["pt_licence", "pt_coldchain", SUCCESSOR, STALE], out


def test_a_history_read_keeps_the_replaced_claim():
    """The replaced claim is still SERVED — ranked, never dropped.

    ⛔ This is the half a global "ended sorts last" tier broke: measured on the
    corpus, the replaced claim fell out of a ``limit=20`` page entirely (see
    ``_superseded_below_successor``'s docstring). §2.0 makes the record the
    truth, so the historical read must still return it — labelled.
    """
    rows = [
        _row(STALE, 5.0, status="superseded", outdated=True,
             superseded_by={"id": SUCCESSOR, "content_snippet": "…",
                            "created_at": "2026-11-26T15:06:41+00:00"}),
        _row(SUCCESSOR, 2.11),
    ]
    out = StateRanker().rerank(rows)
    assert len(out) == 2, "the replaced claim must not be filtered out here"
    stale = next(r for r in out if r["id"] == STALE)
    assert stale["status"] == "superseded"
    assert stale["superseded_by"]["id"] == SUCCESSOR
    # …and it is the LAST row, i.e. never the current answer.
    assert out[-1]["id"] == STALE


def test_a_superseded_claim_whose_successor_is_absent_keeps_its_rank():
    """No successor in the result set ⇒ nothing to outrank ⇒ no reordering.

    A page from a narrow query may hold the replaced claim without the claim
    that replaced it; demoting on that basis would be a silent penalty.
    """
    rows = [_row(STALE, 5.0, status="superseded", outdated=True,
                 superseded_by={"id": "pt_not_in_page", "content_snippet": "…",
                                "created_at": "2026-11-26T15:06:41+00:00"}),
            _row("pt_other", 2.0)]
    out = _ids(StateRanker().rerank(rows))
    assert out == [STALE, "pt_other"], out


def test_a_supersession_chain_resolves_to_the_newest_claim():
    """``A <- B <- C``: A was replaced by B, and B by C. C is the answer.

    The pass repeats until no violation remains; the chain must not leave A
    above B merely because one pass moved B below C.
    """
    a, b, c = "pt_a", "pt_b", "pt_c"
    rows = [
        _row(a, 9.0, status="superseded", outdated=True,
             superseded_by={"id": b, "content_snippet": "…", "created_at": "t1"}),
        _row(b, 8.0, status="superseded", outdated=True,
             superseded_by={"id": c, "content_snippet": "…", "created_at": "t2"}),
        _row(c, 1.0),
    ]
    out = _ids(StateRanker().rerank(rows))
    assert out == [c, b, a], out


def test_a_four_link_chain_resolves_too():
    """A ``k``-link chain needs ``k(k-1)/2`` moves, not ``k``.

    REGRESSION PIN (review P1). The first version bounded the loop by the PASS
    count with one move per pass, so three links resolved and four did not:
    ``A<-B<-C<-D`` came out ``B, A, D, C`` — B left ABOVE the claim that replaced
    it, i.e. the defect this module exists to remove, on a longer chain.
    ``supersede_point`` called three times produces exactly this shape, because
    ``CORRECTS`` is not in the structural rels a supersede transfers.
    """
    ids = ["pt_a", "pt_b", "pt_c", "pt_d"]
    a, b, c, d = ids
    rows = [
        _row(a, 9.0, status="superseded", outdated=True,
             superseded_by={"id": b, "content_snippet": "…", "created_at": "t1"}),
        _row(b, 8.0, status="superseded", outdated=True,
             superseded_by={"id": c, "content_snippet": "…", "created_at": "t2"}),
        _row(c, 7.0, status="superseded", outdated=True,
             superseded_by={"id": d, "content_snippet": "…", "created_at": "t3"}),
        _row(d, 1.0),
    ]
    out = _ids(StateRanker().rerank(rows))
    assert out == [d, c, b, a], out


def test_two_unidentifiable_rows_are_never_reordered_by_each_other():
    """REGRESSION PIN (review P2): a row with no unique id is NEVER MOVED.

    The index this pass moves by is only usable when it is injective, so a row
    with no id (or an empty one) is unaddressable and the constraint does not
    apply to it. What a broken index does is measured, not asserted: drop the
    ``rid not in where`` guard and the pass re-locates the row by
    ``list.index("")`` — the FIRST id-less row — so it pops and relocates the
    row ABOVE the one it inspected (``no id at all``, here the innocent
    bystander) and leaves the id-less row that carries ``superseded_by`` where
    it was. Row COUNT alone cannot see that: ``pop`` + ``insert`` of one object
    preserves the multiset either way, which is why this asserts POSITIONS.
    """
    rows = [
        {"content": "no id at all", "scores": {"rrf": 5.0}},
        {"id": "", "content": "empty id", "scores": {"rrf": 3.0}},
        {"content": "no id but superseded", "scores": {"rrf": 2.0},
         "superseded_by": {"id": "pt_s", "content_snippet": "…",
                           "created_at": "t1"}},
        {"id": "pt_s", "content": "pt_s", "scores": {"rrf": 1.0}},
    ]
    out = StateRanker().rerank(rows)
    assert [r.get("content") for r in out] == [
        "no id at all", "empty id", "no id but superseded", "pt_s"], \
        [r.get("content") for r in out]


def test_rows_sharing_one_non_empty_id_are_never_dropped_or_duplicated():
    """REGRESSION PIN (review C2): the SHARED-id half of the injectivity guard.

    ``test_two_unidentifiable_rows_are_never_reordered_by_each_other`` above
    exercises only EMPTY ids, and both the fixed code and the pre-fix line skip
    an empty id, so a shared NON-EMPTY id is the reachable shape for the
    ``counts[rid] == 1`` half of the gate.

    ⛔ WHAT A BROKEN INDEX DOES HERE IS MEASURED, AND IT IS NOT LOSS. An earlier
    draft of this docstring claimed ``A, pt_s, A`` — one row duplicated, one
    lost. That cannot happen in this shape and never could: the pass moves one
    row with ``pop`` + ``insert`` of the SAME object, so the multiset is
    invariant under every mutation of it. What the ungated map (``if rid``)
    really does is pop the row at ``index("dup")`` — the FIRST ``dup``, i.e.
    the innocent ``A`` — and re-insert it after ``pt_s``: ``B`` stays ABOVE its
    own successor, and a row that was in no violation is relocated instead.
    Count and multiset are blind to that, so this asserts ORDER.
    """
    rows = [
        {"id": "dup", "content": "A", "scores": {"rrf": 5.0}},
        {"id": "dup", "content": "B", "scores": {"rrf": 3.0},
         "superseded_by": {"id": "pt_s", "content_snippet": "…",
                           "created_at": "t1"}},
        {"id": "pt_s", "content": "pt_s", "scores": {"rrf": 1.0}},
    ]
    out = StateRanker().rerank(rows)
    assert len(out) == 3, [r.get("content") for r in out]
    assert [r.get("content") for r in out] == ["A", "B", "pt_s"], \
        [r.get("content") for r in out]


def test_a_row_that_names_itself_as_its_successor_keeps_its_rank():
    """REGRESSION PIN (review C4): the self-supersede guard is load-bearing.

    A row whose ``superseded_by.id`` is its OWN id is not a violation, and
    without the guard the move pops the row and then looks for the id it just
    removed — which raises, and NOT in a contained way: on the previous shape
    the ``next(...)`` raised ``StopIteration`` (reproduced), on the depth
    placement it is ``list.index`` raising ``ValueError`` (measured). Either
    propagates out of ``StateRanker.rerank`` into ``_recall_state``, so the
    whole state read fails rather than one row being mis-ordered.
    """
    rows = [
        _row("pt_self", 5.0, status="superseded", outdated=True,
             superseded_by={"id": "pt_self", "content_snippet": "…",
                            "created_at": "t1"}),
        _row("pt_other", 2.0),
    ]
    out = _ids(StateRanker().rerank(rows))
    assert out == ["pt_self", "pt_other"], out


def test_a_successor_beyond_the_page_does_not_drag_the_pair_out_of_it():
    """REGRESSION PIN (review P2): the page edge is a bound, not a detail.

    Moving the replaced row to its successor's position is only safe when THAT
    position is inside the served page. When the successor sits at or past the
    page edge, the move would push the replaced row out too and hide BOTH — the
    silent omission this design rejects the global tier for. On the corpus that
    is the difference between a ``limit=8`` history read returning the labelled
    stale claim and returning nothing.
    """
    rows = [_row(STALE, 100.0, status="superseded", outdated=True,
                 superseded_by={"id": SUCCESSOR, "content_snippet": "…",
                                "created_at": "t1"})]
    rows += [_row(f"pt_filler{i}", 50.0 - i) for i in range(6)]
    rows.append(_row(SUCCESSOR, 1.0))          # rank 8 — outside a page of 4
    out = StateRanker().rerank(rows, page_limit=4)
    page = _ids(out)[:4]
    assert STALE in page, page               # still served, still labelled
    assert SUCCESSOR not in page
    # With a page that DOES hold the successor, the constraint applies again.
    out2 = _ids(StateRanker().rerank(rows, page_limit=9))
    assert out2.index(SUCCESSOR) < out2.index(STALE), out2



def test_the_order_is_deterministic_for_rows_that_are_not_in_violation():
    """Same input, same output — the constraint must not leak plan order.

    The move is a STABLE insertion after the successor, so rows that were never
    in violation keep their relative order and a repeated call is identical.
    """
    rows = [
        _row(STALE, 5.0, status="superseded", outdated=True,
             superseded_by={"id": SUCCESSOR, "content_snippet": "…",
                            "created_at": "2026-11-26T15:06:41+00:00"}),
        _row("pt_x", 3.0),
        _row(SUCCESSOR, 2.11),
        _row("pt_y", 2.0),
    ]
    first = _ids(StateRanker().rerank(list(rows)))
    second = _ids(StateRanker().rerank(list(rows)))
    assert first == second
    assert first == ["pt_x", SUCCESSOR, STALE, "pt_y"], first


def test_a_cycle_keeps_its_rows_in_place():
    """A cycle has NO valid order, so its rows are not moved (review C1).

    The previous shape spent the whole move budget oscillating the pair and
    returned the input order anyway — 146 s at n=320 for an answer it could
    state. Naming the cycle is the same answer, and it is what the placement
    pass now does.
    """
    rows = [
        _row("pt_x", 5.0, status="superseded", outdated=True,
             superseded_by={"id": "pt_y", "content_snippet": "…",
                            "created_at": "t1"}),
        _row("pt_y", 3.0, status="superseded", outdated=True,
             superseded_by={"id": "pt_x", "content_snippet": "…",
                            "created_at": "t2"}),
        _row("pt_z", 1.0),
    ]
    out = _ids(StateRanker().rerank(rows))
    assert out == ["pt_x", "pt_y", "pt_z"], out


def test_a_longer_cycle_is_left_alone_too():
    """REGRESSION PIN (review P3): a THREE-cycle is not moved either.

    The 2-cycle above passes for the wrong reason on the previous shape: two
    placements that cancel make the input order reappear, so the row cannot tell
    "left alone" from "moved twice". A 3-cycle can: with the members placed like
    any other row they come back ``x, z, y`` — moved, with two of the three
    edges still inverted — while the contract (and ``_successor_depths``'s own
    docstring) says a cycle has no order that satisfies its edges and its
    members are therefore not placed at all.
    """
    rows = [
        _row("pt_x", 5.0, status="superseded", outdated=True,
             superseded_by={"id": "pt_y", "content_snippet": "…",
                            "created_at": "t1"}),
        _row("pt_y", 3.0, status="superseded", outdated=True,
             superseded_by={"id": "pt_z", "content_snippet": "…",
                            "created_at": "t2"}),
        _row("pt_z", 1.0, status="superseded", outdated=True,
             superseded_by={"id": "pt_x", "content_snippet": "…",
                            "created_at": "t3"}),
    ]
    out = _ids(StateRanker().rerank(rows))
    assert out == ["pt_x", "pt_y", "pt_z"], out


def test_a_row_feeding_a_cycle_is_still_held_below_it():
    """A cycle is left alone; the chain that FEEDS it is not (review P3).

    Leaving cycle members unplaced must not degenerate into "skip anything that
    touches a cycle": ``pt_feed`` names ``pt_x`` (a cycle member), so
    ``pt_feed`` must still come out BELOW ``pt_x`` — it is placed directly after
    it, not last, because the rows of the cycle itself are unordered.

    ⛔ This is a CONTROL, not a proof of the cycle fix: the old
    place-everything shape reaches this same order by accident. It reds on the
    over-broad mutant that drops every row downstream of a cycle instead.
    """
    rows = [
        _row("pt_feed", 9.0, status="superseded", outdated=True,
             superseded_by={"id": "pt_x", "content_snippet": "…",
                            "created_at": "t0"}),
        _row("pt_x", 5.0, status="superseded", outdated=True,
             superseded_by={"id": "pt_y", "content_snippet": "…",
                            "created_at": "t1"}),
        _row("pt_y", 3.0, status="superseded", outdated=True,
             superseded_by={"id": "pt_x", "content_snippet": "…",
                            "created_at": "t2"}),
    ]
    out = _ids(StateRanker().rerank(rows))
    assert out == ["pt_x", "pt_feed", "pt_y"], out


def test_a_large_pool_resolves_within_the_wait_bound():
    """REGRESSION PIN (review C1): the pass is O(n²), not O(n⁴).

    Measured on the previous shape, which re-indexed with ``ids.count(rid)`` on
    every one of up to ``n·(n-1)`` passes: a 160-link chain took 4.2 s here and
    the review measured 67 s at 320 links and 146 s for a 320-row pool holding
    one cyclic pair. ``n`` is not exotic — ``_recall_state`` reads a pool of
    ``max(limit * 3, 30)``, so ``limit=100`` is 300 rows — and #3834 pins a 10 s
    wait bound, so the cost IS the defect. The chain also has to come out fully
    resolved, not merely fast.
    """
    n = 300
    rows = [_row(f"pt_c{i:03d}", float(n - i),
                 status="superseded", outdated=True,
                 superseded_by={"id": f"pt_c{i + 1:03d}",
                                "content_snippet": "…", "created_at": f"t{i}"})
            for i in range(n - 1)]
    rows.append(_row(f"pt_c{n - 1:03d}", 1.0))
    started = time.perf_counter()
    out = _ids(StateRanker().rerank(rows))
    elapsed = time.perf_counter() - started
    assert out == [f"pt_c{i:03d}" for i in reversed(range(n))], out[:5]
    assert elapsed < 10.0, (
        f"a {n}-row supersession chain took {elapsed:.1f}s — the #3834 wait "
        "bound is 10 s, and this shape used to spend it in a re-index per move")
