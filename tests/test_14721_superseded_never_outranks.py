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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.ranking import StateRanker  # noqa: E402

STALE = "pt_fc8b3e47bb7e0599328d968073671eef8c9cecbc376ca2bbd6e05476f2c078"
SUCCESSOR = "pt_b44d17132b7b0f1a4e0a3b0b0e3892f16fe98ea53bd3a808d6363442b36607"


def _row(pid: str, rrf: float, **extra) -> dict:
    """One recall row with everything StateRanker needs and nothing else."""
    row = {
        "id": pid,
        "content": pid,
        "scores": {"rrf": rrf},
        "state_confidence": 0.5,
        "state_has_ep": True,
        "state_degree": 0,
        "status": "live",
    }
    row.update(extra)
    return row


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
