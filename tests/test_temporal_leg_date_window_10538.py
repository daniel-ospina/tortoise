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


def test_a_pick_never_re_adds_a_session_the_visible_head_already_holds():
    """Review cycle 5 (#10549), the MEASURED production geometry: ``window=12,
    limit=2`` ⇒ ``ceiling=10``, and the head slice holds ``sA``×8 + ``sB``×2.
    Excluding only the bucket AT the ceiling (``b3``/``sB``) spent the second
    half of the budget re-adding ``sA`` — the session owning 8 of the 10
    visible slots, i.e. the one the reader already has — and EVICTED ``b3``
    (``sB``, a DIFFERENT session) from the 12-window: the inverse of the
    unit's objective.

    Review cycle 7 found the SAME shape one slot further along: the exclusion
    stopped at the promotion ceiling (``candidates[:head + 1]`` = ``[:11]``)
    while the reader sees ``candidates[:12]``, so the pick this geometry
    produced was ``c1`` — ``pool[11]``, an id already inside the visible
    window. Every bucket within reach here (``b3``/``sB`` at 10, ``c1``/``sC``
    at 11, ``a8``/``sA`` at 12) is one the reader already holds, so the honest
    verdict is that this pool has nothing new to promote: the arm must go
    INERT rather than report ``applied`` for a reorder that widens nothing.
    Pre-fix this probe yields ``picks == ['c1']`` and a reordered window.
    """
    pool = (
        [{"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-11",
          "content": "alpha"} for i in range(8)]
        + [{"id": "b1", "session_id": "sB", "session_date": "2023-04-14",
            "content": "alpha"},
           {"id": "b2", "session_id": "sB", "session_date": "2023-04-14",
            "content": "alpha"},
           {"id": "b3", "session_id": "sB", "session_date": "2023-04-14",
            "content": "alpha"},
           {"id": "c1", "session_id": "sC", "session_date": "2023-04-19",
            "content": "alpha"},
           {"id": "a8", "session_id": "sA", "session_date": "2023-04-11",
            "content": "alpha"}]
    )
    session_of = {c["id"]: c["session_id"] for c in pool}
    base = [c["id"] for c in pool]
    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=12, limit=2,
        date_window=("2023-04-10", "2023-04-20"))
    # Every bucket the reader's 12-window holds is out of reach, so there is
    # nothing to promote. Pre-fix the exclusion stopped at index 11 and this
    # spent the budget on ``c1`` (``pool[11]``, already visible).
    assert picks == [], (
        f"a pick was spent on an id the reader's window already holds: "
        f"{[(p, session_of[p]) for p in picks]}")
    assert order == base, (
        f"an inert window leg reordered the pool: {order}")
    # ...and the slot the ceiling-only slice would have spent must not evict a
    # DIFFERENT session from the window either.
    assert order.index("b3") < 12, (
        f"a pick evicted b3/sB from the reader's window: {order}")


def test_the_budget_is_spent_below_the_reader_window_not_on_visible_ids():
    """Review cycle 7 (#10549), the case where a genuinely NEW session exists:
    ``window=12, limit=2`` ⇒ ``ceiling=10``, head = ``sA``×10, ``b1``/``sB`` at
    10, ``c1``/``sC`` at 11, ``d1``/``sD`` at 12, ``e1``/``sE`` at 13.

    ``b1`` and ``c1`` are both inside the reader's window (indices 10 and 11),
    so neither buys a session the reader does not already have; ``d1`` and
    ``e1`` are the only net-new buckets. A ceiling-only exclusion
    (``candidates[:head + 1]`` = ``[:11]``) reaches neither ``c1``'s bucket nor
    the visible-window bound, so it halves the budget: measured
    ``picks == ['c1', 'd1']`` — one wasted pick, one of two net-new sessions
    delivered. Excluding the reader WINDOW (``candidates[:window]``) gives
    ``['d1', 'e1']``: the full budget, spent on two new sessions."""
    pool = (
        [{"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-11",
          "content": "alpha"} for i in range(10)]
        + [{"id": "b1", "session_id": "sB", "session_date": "2023-04-12",
            "content": "alpha"},
           {"id": "c1", "session_id": "sC", "session_date": "2023-04-13",
            "content": "alpha"},
           {"id": "d1", "session_id": "sD", "session_date": "2023-04-14",
            "content": "alpha"},
           {"id": "e1", "session_id": "sE", "session_date": "2023-04-15",
            "content": "alpha"}]
    )
    bucket_of = {c["id"]: (c["session_date"], c["session_id"]) for c in pool}
    window = 12
    seen_buckets = {bucket_of[c["id"]] for c in pool[:window]}
    seen_ids = {c["id"] for c in pool[:window]}

    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=window, limit=2,
        date_window=("2023-04-10", "2023-04-20"))

    assert picks == ["d1", "e1"], (
        f"the budget was spent on a bucket the reader already holds: {picks}")
    # The invariant the code comment states, stated at the window bound.
    assert not (set(picks) & seen_ids), (
        f"a pick re-emitted an id inside the reader's window: {picks}")
    assert not ({bucket_of[p] for p in picks} & seen_buckets), (
        f"a pick re-emitted a bucket the reader already holds: "
        f"{[(p, bucket_of[p]) for p in picks]}")
    assert order != [c["id"] for c in pool], (
        "the windowed arm reported picks but moved nothing")


def test_the_head_placement_arm_declines_on_an_unclustered_window():
    """Cycle 2 finding #1 named the ``placement=\"head\"`` arm specifically (it
    sets ``head_excl = 0``, so the gate sees ``head == 0``): the CLUSTERING
    sample must then be the caller's real ``window``, not the promotion
    ``limit``.

    ⚠️ Review cycle 5 finding: the earlier version of this test could not fail
    if the ``elif window > 0`` branch were wrong — ``limit=1`` made the
    fallback sample one slot, so the two samples agreed and the ``< 3`` guard
    declined either way. This pool separates them: ``candidates[:3]``
    (``sA``×2 + ``sB``) IS clustered while ``candidates[:9]`` is NOT, so a
    branch that read the (caller-clamped) budget instead of the window would
    FIRE here. The budget must be ≥ 3 for the two samples to differ at all:
    ``effective_promotion_budget(9, 3) == 3``."""
    pool = [
        {"id": "x0", "session_id": "sA", "session_date": "2023-04-11",
         "content": "alpha"},
        {"id": "x1", "session_id": "sA", "session_date": "2023-04-11",
         "content": "alpha"},
        {"id": "x2", "session_id": "sB", "session_date": "2023-04-12",
         "content": "alpha"},
        {"id": "x3", "session_id": "sC", "session_date": "2023-04-13",
         "content": "alpha"},
        {"id": "x4", "session_id": "sD", "session_date": "2023-04-14",
         "content": "alpha"},
        {"id": "x5", "session_id": "sE", "session_date": "2023-04-15",
         "content": "alpha"},
        {"id": "x6", "session_id": "sF", "session_date": "2023-04-16",
         "content": "alpha"},
        {"id": "x7", "session_id": "sG", "session_date": "2023-04-17",
         "content": "alpha"},
        {"id": "x8", "session_id": "sH", "session_date": "2023-04-18",
         "content": "alpha"},
    ]
    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=9, limit=3, placement="head",
        date_window=("2023-04-10", "2023-04-20"))
    assert picks == [], (
        f"the gate read the budget-sized sample instead of the window: {picks}")
    assert order == [c["id"] for c in pool]


def test_the_head_placement_arm_fires_on_a_clustered_window():
    """The positive control for the test above: the SAME arm, on a pool whose
    window sample IS clustered, must promote a new session — and it reaches
    that verdict through ``elif window > 0`` (``head == 0``), so a branch that
    read ``candidates[:max(limit, 1)]`` instead of the window declines and this
    test fails. Together the pair pins the sample the gate reads."""
    pool = (
        [{"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-11",
          "content": "alpha"} for i in range(4)]
        + [{"id": "b1", "session_id": "sB", "session_date": "2023-04-14",
            "content": "alpha"}]
    )
    base = [c["id"] for c in pool]
    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=4, placement="head",
        date_window=("2023-04-10", "2023-04-20"))
    assert picks == ["b1"], picks
    assert order.index("b1") < base.index("b1"), order


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


def test_the_exclusion_is_bucket_scoped_not_session_scoped():
    """Review cycle 6 (#10549): the exclusion comment claimed "every pick is a
    session the reader does NOT already have", but ``held_buckets`` keys on
    ``(session_date, session_id)`` — a BUCKET — so the guarantee is
    bucket-level. The divergence is reachable and benign: the producer writes
    one ``session_date`` per session, yet a hit carrying a different raw date
    string for the same session is a DIFFERENT bucket.

    This test pins the actual granularity, so the comment cannot drift back to
    a session-level claim: the held head is ``sA`` at ``2023-04-11``, the pick
    is ``aX`` — the SAME session ``sA`` at ``2023-04-19``, a distinct bucket —
    and it must be promotable while ``a4`` (the held bucket itself) must not.
    A session-keyed exclusion fails here."""
    pool = [
        {"id": "a1", "session_id": "sA", "session_date": "2023-04-11",
         "content": "alpha"},
        {"id": "a2", "session_id": "sA", "session_date": "2023-04-11",
         "content": "alpha"},
        {"id": "a3", "session_id": "sA", "session_date": "2023-04-11",
         "content": "alpha"},
        {"id": "a4", "session_id": "sA", "session_date": "2023-04-11",
         "content": "alpha"},
        {"id": "aX", "session_id": "sA", "session_date": "2023-04-19",
         "content": "alpha"},
        {"id": "b1", "session_id": "sB", "session_date": "2023-04-14",
         "content": "alpha"},
    ]
    picks = temporal_leg_order(
        pool, anchors=(), limit=2, head=3,
        date_window=("2023-04-10", "2023-04-20"))
    assert "a4" not in picks, (
        f"the bucket the reader already holds was re-emitted: {picks}")
    assert "aX" in picks, (
        f"a different BUCKET of the same session must stay promotable — the "
        f"exclusion is (session_date, session_id)-scoped, not session-scoped: "
        f"{picks}")


def test_the_clustering_guard_samples_the_reader_window_not_the_ceiling():
    """Review cycle 8 (#10549), the BLOCKING defect: ``_session_clustered``
    sampled ``candidates[:head]``, and ``head`` is the caller's PROMOTION
    CEILING (``window - limit``), not the reader's window — so at the shipped
    geometry it read 10 of the 12 visible slots and omitted exactly the
    ``limit`` slots the promotion displaces. The guard could therefore only
    OVER-fire, and the pick evicted a reader-visible slot.

    This pool separates the two samples. One session owns 5 of the first 10
    slots; indices 10 and 11 are two other sessions and index 12 a third:

    * ceiling sample ``candidates[:10]`` → ``5*2 = 10 >= 10`` ⇒ **clustered**
    * the documented rule over the visible 12 → ``5*2 = 10 < 12`` ⇒ **declined**

    Pre-fix the arm fires here and promotes ``deep`` (index 12), which evicts
    ``t11`` — a reader-visible slot — from the 12-window. The spread is
    defined as the remedy for a window that STARVES the other sessions, and 5
    of 12 is not that window, so the honest verdict is inert.
    """
    pool = (
        [{"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-11",
          "content": "alpha"} for i in range(5)]
        + [{"id": f"t{i}", "session_id": f"sT{i}",
            "session_date": "2023-04-12", "content": "beta"} for i in range(5)]
        + [{"id": "t10", "session_id": "sT10", "session_date": "2023-04-13",
            "content": "beta"},
           {"id": "t11", "session_id": "sT11", "session_date": "2023-04-14",
            "content": "beta"},
           {"id": "deep", "session_id": "sD", "session_date": "2023-04-15",
            "content": "gamma"}]
    )
    base = [c["id"] for c in pool]
    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=12, limit=2,
        date_window=("2023-04-10", "2023-04-20"))
    assert picks == [], (
        f"the guard fired on a window that is not clustered (5 of 12 slots): "
        f"{picks}")
    assert order == base, f"an unclustered window reordered the pool: {order}"
    # ...and the reader-visible slot the ceiling-sample bug evicted stays put.
    assert order.index("t11") < 12, (
        f"a pick evicted t11 from the reader's window: {order}")


def test_the_clustering_guard_can_fire_at_a_small_window():
    """The MIRROR symptom of the same sample bug. At ``window=3`` the ceiling
    is 2, so a ``[:head]`` sample is a 2-slot sample — below the ``< 3`` floor
    — and the guard declined UNCONDITIONALLY: the arm could never widen
    anything at a 3-wide window, whatever the pool looked like. The three
    visible slots are what must be judged, and there one session holds all
    three, so the new session IS promoted.

    Pre-fix this returns ``[]``; the assertions below are the positive control
    that the window sample did not simply make the guard never fire."""
    pool = (
        [{"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-11",
          "content": "alpha"} for i in range(3)]
        + [{"id": "b1", "session_id": "sB", "session_date": "2023-04-12",
            "content": "alpha"}]
    )
    base = [c["id"] for c in pool]
    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=3, limit=1,
        date_window=("2023-04-10", "2023-04-20"))
    assert picks == ["b1"], (
        f"a 3-wide window whose 3 slots are one session is clustered, and the "
        f"new session must be promoted: {picks}")
    assert order != base, "the windowed pick moved nothing"
    assert order.index("b1") < base.index("b1"), order


def test_absent_session_ids_are_not_a_session_for_the_clustering_guard():
    """A hit with NO ``session_id`` is not a session, so a window full of them
    is not CLUSTERED — and the arm must stay inert there.

    The guard groups by ``str(c.get("session_id") or "")``, so before this pin
    every unattributed hit collapsed into ONE bucket and "most hits carry no
    session at all" satisfied "one session owns half the visible slots". The
    wired path is exactly where that is reachable: ``_annotate_hits`` defaults
    ``session_id`` to ``""``, so a pool whose hits lose attribution reads as
    maximally clustered. Spending the budget there displaces an in-window pick
    for a spread the evidence does not support — the over-fire shape review
    cycles 5/7/8 kept removing from this arm.

    Pre-fix this returns ``picks == ['b1', 'c1']`` (measured, mutant run);
    post-fix the pick set is empty and the order is untouched, because the only
    real sessions (``sB``, ``sC`` — one slot each) are nowhere near a majority.
    """
    pool = (
        [{"id": f"x{i}", "session_id": "", "session_date": "2023-04-11",
          "content": "alpha"} for i in range(6)]
        + [{"id": f"y{i}", "session_id": "", "session_date": "2023-04-12",
            "content": "alpha"} for i in range(3)]
        + [{"id": "b1", "session_id": "sB", "session_date": "2023-04-13",
            "content": "alpha"},
           {"id": "c1", "session_id": "sC", "session_date": "2023-04-14",
            "content": "alpha"},
           {"id": "d1", "session_id": "", "session_date": "2023-04-15",
            "content": "alpha"}]
    )
    base = [c["id"] for c in pool]
    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=8, limit=2,
        date_window=("2023-04-10", "2023-04-20"))
    assert picks == [], (
        f"unattributed hits were counted as one 'session' and the arm fired on "
        f"a window with no real clustering: {picks}")
    assert order == base, "the arm reordered a window it should have declined"

    # Positive control: the SAME pool with real attribution IS clustered, and
    # the arm fires — the guard was narrowed, not disabled.
    attributed = [
        {**c, "session_id": ("sA" if c["session_id"] == "" else c["session_id"])}
        for c in pool
    ]
    order2, picks2 = temporal_leg_fusion_order(
        attributed, anchors=(), window=8, limit=2,
        date_window=("2023-04-10", "2023-04-20"))
    assert picks2, "the guard no longer fires on a genuinely clustered window"
    assert order2 != base, "the windowed pick moved nothing on the control pool"
