"""#10538: a date-RANGE question must widen the candidate set by session.

The spread-evidence class (53% of epic #2513's misses) is a question whose
evidence spans N sessions while one-shot top-k RRF clusters on the single most
similar session. :mod:`tortoise.temporal_leg` already has the mechanism that
fixes this — ``temporal_leg_order`` phase 2 buckets the promotable
candidates by ``(session_date, session_id)`` and round-robins across buckets —
but it is gated on **comparison anchors** (``if not relevant: return []``), so
a question that names a DATE WINDOW instead ("between 2023-04-10 and
2023-04-20") is never spread: the window is derived by
``detect_time_constraint`` and used only to FILTER (``_apply_time_window``,
which narrows the pool and cannot widen it).

These tests pin the widened decision: when no anchor targets the pool but a
date window does, the window IS the question's own vocabulary, and the spread
runs inside it. Without a window the leg stays inert exactly as before.
"""

from __future__ import annotations

from tortoise.temporal_leg import temporal_leg_fusion_order, temporal_leg_order

#: Session A is the semantically nearest one and supplies SIX consecutive top
#: ranks; sessions B and C hold the rest of the evidence further down. This is
#: the measured failure shape: recall_all@5 = 0 because top-5 is all A.
SPREAD_POOL = [
    {"id": "a1", "session_id": "sA", "session_date": "2023-04-11",
     "content": "user adopted a puppy named Biscuit"},
    {"id": "a2", "session_id": "sA", "session_date": "2023-04-11",
     "content": "the puppy was a rescue from the shelter"},
    {"id": "a3", "session_id": "sA", "session_date": "2023-04-11",
     "content": "puppy food is the kibble from the vet"},
    {"id": "a4", "session_id": "sA", "session_date": "2023-04-11",
     "content": "the puppy sleeps in the kitchen"},
    {"id": "a5", "session_id": "sA", "session_date": "2023-04-11",
     "content": "puppy training starts next week"},
    {"id": "a6", "session_id": "sA", "session_date": "2023-04-11",
     "content": "the puppy chewed a slipper"},
    {"id": "b1", "session_id": "sB", "session_date": "2023-04-14",
     "content": "the puppy got its second vaccination"},
    {"id": "c1", "session_id": "sC", "session_date": "2023-04-19",
     "content": "the puppy graduated from its first class"},
]


def test_a_date_window_spreads_without_anchors():
    """The pre-fix leg is INERT here (no anchors, spread-only refuses), so
    top-5 is 5 pieces of session A and recall_all@5 is 0 even though sessions
    B and C are in the pool. With the window, the picks must reach the sessions
    the reader does NOT already hold: the bucket of ``candidates[head]`` (the
    session at the promotion boundary) is excluded, because re-emitting it is a
    structural no-op (review cycle 3, #10549)."""
    order = temporal_leg_order(
        SPREAD_POOL, anchors=(), limit=6, head=0,
        date_window=("2023-04-10", "2023-04-20"))
    sessions = {c["session_id"] for c in SPREAD_POOL if c["id"] in order}
    assert sessions == {"sB", "sC"}, (
        f"the window spread must reach the sessions the reader lacks, "
        f"got {sessions} from picks {order}")


def test_the_window_is_respected_and_out_of_window_is_not_promoted():
    """A window is a target, not a licence: a candidate dated outside it is
    never promoted by the window path (the spread stays inside the question's
    own range), and one of the same session further out is not picked."""
    pool = [
        {"id": "a1", "session_id": "sA", "session_date": "2023-04-11",
         "content": "alpha"},
        {"id": "a2", "session_id": "sA", "session_date": "2023-04-11",
         "content": "alpha"},
        {"id": "far", "session_id": "sZ", "session_date": "2022-01-01",
         "content": "alpha"},
        {"id": "in", "session_id": "sB", "session_date": "2023-04-18",
         "content": "alpha"},
        {"id": "a_far", "session_id": "sA", "session_date": "2023-04-30",
         "content": "alpha"},
    ]
    order = temporal_leg_order(
        pool, anchors=(), limit=4, head=0,
        date_window=("2023-04-10", "2023-04-20"))
    assert "in" in order
    assert "far" not in order
    # a SAME-session candidate outside the window is also never picked
    assert "a_far" not in order


def test_an_unclustered_pool_is_not_reordered_by_a_window():
    """The window is a target only where the evidence is STARVED. On a pool
    whose visible slots already span distinct sessions, promoting a pick would
    displace a correct rank-(k-1) gold — the −3.3 pt shape #2976 measured. The
    clustering condition is what makes the window path a target instead of a
    tautology (after ``_apply_time_window`` every remaining hit is in-window)."""
    pool = [
        {"id": "x1", "session_id": "sA", "session_date": "2023-04-11",
         "content": "alpha"},
        {"id": "x2", "session_id": "sB", "session_date": "2023-04-12",
         "content": "alpha"},
        {"id": "x3", "session_id": "sC", "session_date": "2023-04-13",
         "content": "alpha"},
        {"id": "x4", "session_id": "sD", "session_date": "2023-04-14",
         "content": "alpha"},
    ]
    assert temporal_leg_order(
        pool, anchors=(), limit=4, head=0,
        date_window=("2023-04-10", "2023-04-20")) == []


def test_the_producer_slash_date_shape_compares_correctly():
    """Both sides must resolve through the ONE calendar resolver
    (``tortoise.coverage_loop._calendar_date``) — the ingest producer writes
    ``2023/04/10 (Mon) 17:50`` (#3011), and a raw-string predicate silently
    fails on it (#9182: ``"/"`` > ``"-"`` at index 4, so an ISO upper bound
    rejects every slash-shaped date). A string-comparing implementation passes
    every other test in this file and fails this one."""
    pool = [
        {"id": "s1", "session_id": "sA", "session_date": "2023/04/11 (Tue) 09:00",
         "content": "alpha"},
        {"id": "s2", "session_id": "sA", "session_date": "2023/04/12 (Wed) 10:00",
         "content": "alpha"},
        {"id": "slash", "session_id": "sB", "session_date": "2023/04/18 (Tue) 17:50",
         "content": "alpha"},
    ]
    order = temporal_leg_order(
        pool, anchors=(), limit=3, head=0,
        date_window=("2023-04-10", "2023-04-20"))
    assert "slash" in order, (
        "a slash-shaped session date must be resolved to its calendar day and "
        f"proven in-window, got picks {order}")


def test_a_two_slot_sample_cannot_claim_clustering():
    """Review cycle 2 (#10549): with a 2-slot visible sample, ``max(counts)*2 >=
    len(visible)`` is VACUOUSLY true, so a budget-sized sample made the guard
    inert and a four-distinct-session pool promoted — the untargeted promotion
    #2976 measured. A sample below three slots cannot distinguish clustered
    from spread and must decline."""
    pool = [
        {"id": "y1", "session_id": "sA", "session_date": "2023-04-11",
         "content": "alpha"},
        {"id": "y2", "session_id": "sB", "session_date": "2023-04-12",
         "content": "alpha"},
        {"id": "y3", "session_id": "sC", "session_date": "2023-04-13",
         "content": "alpha"},
        {"id": "y4", "session_id": "sD", "session_date": "2023-04-14",
         "content": "alpha"},
    ]
    # default limit is 2 (the promotion budget) — the degenerate sample
    assert temporal_leg_order(
        pool, anchors=(), date_window=("2023-04-10", "2023-04-20")) == []


def test_the_fused_caller_actually_moves_the_order():
    """The PRODUCTION geometry: ``ceiling = window - limit`` and the picks fuse
    AFTER ``candidates[:ceiling]``. Review cycle 3 (#10549): the earlier version
    of this test passed on a leg that moved NOTHING — its picks were re-emitted
    ids already in the head slice. The fused order must actually CHANGE, the
    pick's index must strictly DECREASE, and a pick must never be a session the
    reader already holds at the ceiling."""
    pool = [
        {"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-11",
         "content": "alpha"} for i in range(14)
    ] + [
        {"id": "b1", "session_id": "sB", "session_date": "2023-04-14",
         "content": "alpha"},
    ]
    base = [c["id"] for c in pool]
    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=12, date_window=("2023-04-10", "2023-04-20"))
    assert picks == ["b1"], picks
    assert order != base, "the windowed pick moved nothing — a no-op reported as applied"
    assert order.index("b1") < base.index("b1")


def test_the_conservative_limit_one_arm_is_not_a_no_op():
    """``TORTOISE_LME_TEMPORAL_LEG_LIMIT=1`` is the documented conservative
    arm. Before review cycle 3 its single pick was always the id already
    sitting at the promotion ceiling, so the fused order was byte-identical
    while the stats reported ``applied`` with ``picks=1``."""
    pool = [
        {"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-11",
         "content": "alpha"} for i in range(14)
    ] + [
        {"id": "b1", "session_id": "sB", "session_date": "2023-04-14",
         "content": "alpha"},
    ]
    base = [c["id"] for c in pool]
    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=12, limit=1,
        date_window=("2023-04-10", "2023-04-20"))
    assert picks == ["b1"], picks
    assert order != base, "limit=1 must lift a NEW session, not re-emit the ceiling id"


def test_the_head_placement_arm_declines_on_an_unclustered_window():
    """Cycle 2 finding #1 named the ``placement=\"head\"`` arm specifically (it
    sets ``head_excl = 0``, so the gate sees ``head == 0``): with the real
    window in hand it must still decline on a well-spread visible slice. This
    is the only path that exercises ``elif window > 0``."""
    pool = [
        {"id": f"x{i}", "session_id": f"s{i}", "session_date": "2023-04-11",
         "content": "alpha"} for i in range(2)
    ] + [
        {"id": "p2", "session_id": "sP", "session_date": "2023-04-12",
         "content": "alpha"},
        {"id": "q2", "session_id": "sQ", "session_date": "2023-04-13",
         "content": "alpha"},
    ]
    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=4, limit=1, placement="head",
        date_window=("2023-04-10", "2023-04-20"))
    assert picks == [], picks
    assert order == [c["id"] for c in pool]


def test_the_fused_caller_declines_on_an_unclustered_window():
    """Same production geometry, but the visible window already spans distinct
    sessions — the spread must not fire and the fused order must be untouched."""
    pool = [
        {"id": f"x{i}", "session_id": f"s{i}", "session_date": "2023-04-11",
         "content": "alpha"} for i in range(12)
    ]
    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=12, date_window=("2023-04-10", "2023-04-20"))
    assert picks == []
    assert order == [c["id"] for c in pool]


def test_no_anchors_and_no_window_stays_inert():
    """The recorded #2976 finding — a spread-only leg over arbitrary deep pool
    items cost 3.3 pts of evidence-turn recall@12 — must survive: absent BOTH
    a targeting anchor and a date window, the leg returns nothing."""
    assert temporal_leg_order(SPREAD_POOL, anchors=()) == []
    assert temporal_leg_order(
        SPREAD_POOL, anchors=(), date_window=None) == []


def test_the_reader_visible_head_is_never_disturbed_by_the_window():
    """``head`` excludes the reader-visible prefix from both phases; the window
    path must honour it. With the first 6 candidates excluded, ``b1`` is the
    candidate AT the promotion boundary — its bucket is the session the reader
    already has there, so it is never re-emitted (a structural no-op, review
    cycle 3) and the pick is the one genuinely new session, ``c1``."""
    order = temporal_leg_order(
        SPREAD_POOL, anchors=(), limit=6, head=6,
        date_window=("2023-04-10", "2023-04-20"))
    assert order == ["c1"], order
    head_ids = {c["id"] for c in SPREAD_POOL[:6]}
    assert not head_ids & set(order)
