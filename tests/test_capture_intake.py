"""#14342 — the capture intake queue: accept, never refuse, never lose.

The rules under test are the four invariants in `tortoise/capture_intake.py`'s
module docstring. They are tested HERE, without a server, because each one is a
property of the queue rather than of the route:

1. assume-accepted means recorded (an unwritten row raises, so the door cannot
   answer 2xx on it);
2. no depth ceiling ever drops work;
3. a claimed item is never lost by the claim (a dead worker's lease is reclaimed);
4. exhaustion is not deletion (a `failed` item stays listable).
"""

from __future__ import annotations

import pytest

from tortoise.capture_intake import (
    CAPTURE_INTAKE_LABEL,
    CAPTURE_INTAKE_MAX_ATTEMPTS,
    CaptureIntake,
    CaptureIntakeItem,
    GraphIntakeStore,
    IntakeState,
    IntakeStoreUnavailable,
    MemoryIntakeStore,
    item_id_for,
)


class _Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _queue(**kw) -> tuple[CaptureIntake, MemoryIntakeStore, _Clock]:
    clock = _Clock()
    store = MemoryIntakeStore()
    q = CaptureIntake(store=store, now=clock, **kw)
    return q, store, clock


# ── 1. assume-accepted means recorded ──────────────────────────────────────

def test_accept_returns_after_the_row_exists_and_never_swallows_a_write_failure():
    """A store that cannot write must RAISE, so the door answers 503 rather
    than acknowledging a turn it did not keep.

    This is the #10479 direction: the client prunes its spool entry on any 2xx,
    so an accepted-but-unwritten row is a destroyed conversation.
    """
    class _BrokenStore(MemoryIntakeStore):
        def put(self, item: CaptureIntakeItem) -> None:
            raise IntakeStoreUnavailable("disk full")

    q = CaptureIntake(store=_BrokenStore(), now=_Clock())
    with pytest.raises(IntakeStoreUnavailable):
        q.accept(org_id="o1", graph="g1", session_id="s1",
                 payload={"conversation": [{"role": "user", "content": "hi"}]})
    assert q.counters.accepted == 0, "a failed write must not be counted as accepted"


def test_the_stored_row_carries_the_payload_verbatim_and_no_credential():
    """The drain re-enters the capture path, so the payload must be the request
    body byte-for-byte; and no credential may ever be copied into the record."""
    q, store, _ = _queue()
    body = {
        "session_id": "s1",
        "harness": "pi",
        "conversation": [{"role": "user", "content": "the fact"}],
    }
    acc = q.accept(org_id="o1", graph="g1", session_id="s1", payload=body)
    row = store.rows()[0]
    assert row.payload == body
    assert acc.session_id == "s1"
    assert acc.as_payload()["status"] == "queued"
    flat = repr(row.as_row()).lower()
    for forbidden in ("api_key", "apikey", "bearer", "password", "token"):
        assert forbidden not in flat, (
            f"the intake row must not carry {forbidden!r} — the drain "
            "re-resolves the tenant instead of copying a credential")


# ── 2. depth is pacing, never refusal ──────────────────────────────────────

def test_acceptance_continues_past_the_depth_ceiling_and_says_so():
    """⛔ A depth ceiling that refuses is the defect this epic exists to remove.

    At and past the ceiling the queue must keep accepting, mark `over_depth`
    (so an operator sees a stuck drain), and keep every item.
    """
    q, store, _ = _queue(depth_ceiling=3)
    acceptances = [
        q.accept(org_id="o1", graph=None, session_id=f"s{i}",
                 payload={"conversation": []})
        for i in range(10)
    ]
    assert len(store.rows(state=IntakeState.QUEUED.value)) == 10
    assert acceptances[1].over_depth is False, "below the ceiling is not over"
    assert acceptances[2].over_depth is True, (
        "AT the ceiling (depth == 3) it must be published — `stats()` uses the "
        "same `>=` so the two surfaces cannot disagree")
    assert all(a.depth is not None for a in acceptances)
    assert q.stats()["over_depth"] is True


def test_accept_still_returns_when_the_depth_read_fails():
    """The count is observability; the ROW is the acceptance. A store whose
    count failed must not fail an accept — that would refuse work for a metric."""
    class _BadCountStore(MemoryIntakeStore):
        def depth(self):
            raise IntakeStoreUnavailable("count timeout")

    q = CaptureIntake(store=_BadCountStore(), now=_Clock())
    acc = q.accept(org_id="o1", graph=None, session_id="s1",
                   payload={"conversation": []})
    assert acc.item_id
    assert acc.depth is None and acc.position is None
    assert acc.over_depth is False


# ── 3. FIFO, and a claim is a lease ────────────────────────────────────────

def test_claim_is_fifo_so_the_head_falls_monotonically():
    """D1's capstone is a head that reaches zero. A LIFO intake would starve the
    oldest turn forever, so ordering is part of the contract, not a detail."""
    q, _, clock = _queue()
    ids = []
    for i in range(5):
        ids.append(q.accept(org_id="o1", graph=None, session_id=f"s{i}",
                            payload={"n": i}).item_id)
        clock.advance(0.01)
    claimed = [q.next_item().item_id for _ in range(5)]
    assert claimed == ids


def test_item_ids_sort_by_arrival():
    first = item_id_for(1_800_000_000.0)
    second = item_id_for(1_800_000_000.5)
    assert first < second


def test_a_claimed_item_is_not_handed_to_a_second_drain():
    q, _, _ = _queue()
    q.accept(org_id="o1", graph=None, session_id="s1", payload={})
    assert q.next_item() is not None
    assert q.next_item() is None, "a second claim of the same item is lost work ×2"


def test_release_requeues_with_backoff_and_the_item_is_not_reclaimable_early():
    """A failed pass must not spin the drain: the retry is spaced out."""
    q, _store, clock = _queue()
    q.accept(org_id="o1", graph=None, session_id="s1", payload={})
    item = q.next_item()
    state = q.fail(item, "graph hiccup")
    assert state == IntakeState.QUEUED.value
    assert q.next_item() is None, "released item must respect its backoff"
    clock.advance(60)
    again = q.next_item()
    assert again is not None and again.attempts == 2


def test_reclaim_stale_returns_a_dead_workers_item_to_the_queue():
    """⛔ The mid-flight-kill rule. A worker that dies between claim and complete
    leaves the row `extracting`; nothing that queries `queued` would ever see it
    again — an accepted turn silently lost. The lease reclaim is what makes the
    kill survivable.
    """
    clock = _Clock()
    rows: list = []
    seen: list[dict] = []

    def query(cypher: str, params: dict | None = None) -> list:
        seen.append({"cypher": cypher, **dict(params or {})})
        if "DETACH DELETE" in cypher:
            rows[:] = [r for r in rows if r[0] != params["item_id"]]
            return []
        if "count(i)" in cypher and "claimed_at" in cypher:
            n = sum(1 for r in rows
                    if r[7] == params["extracting"]
                    and r[9] is not None and r[9] <= params["cutoff"])
            for r in rows:
                if (r[7] == params["extracting"] and r[9] is not None
                        and r[9] <= params["cutoff"]):
                    r[7] = params["queued"]
                    r[9] = None
                    r[10] = None
            return [[n]]
        if "ORDER BY i.item_id ASC LIMIT 1" in cypher:
            for r in rows:
                if r[7] == params["queued"]:
                    r[7] = params["extracting"]
                    r[9] = params["now"]
                    r[6] = int(r[6] or 0) + 1
                    return [list(r)]
            return []
        return []

    store = GraphIntakeStore(query)
    item = CaptureIntakeItem(item_id=item_id_for(clock()), org_id="o1",
                             graph=None, session_id="s1", payload={"a": 1},
                             enqueued_at=clock(), attempts=0, state="queued")
    rows.append([item.item_id, item.org_id, item.graph, item.session_id,
                 '{"a":1}', item.enqueued_at, 0, "queued", None, None, None])

    store.put(item)
    claimed = store.claim_next(clock())
    assert claimed is not None
    # The worker dies here: no complete(), no release().
    assert store.claim_next(clock()) is None, "still claimed — nothing to hand out"
    clock.advance(901)
    assert store.reclaim_stale(clock()) == 1
    again = store.claim_next(clock())
    assert again is not None and again.item_id == item.item_id, (
        "a reclaimed item must be handed out again — otherwise the accepted "
        "turn is gone")
    assert again.attempts == 2, "the second claim counts as a second attempt"


# ── 4. exhaustion is not deletion ──────────────────────────────────────────

def test_exhausted_item_is_parked_failed_and_stays_listable():
    q, store, clock = _queue()
    q.accept(org_id="o1", graph=None, session_id="s1", payload={})
    for _ in range(CAPTURE_INTAKE_MAX_ATTEMPTS):
        item = q.next_item()
        assert item is not None
        state = q.fail(item, "still broken")
        clock.advance(3600)  # past any backoff the requeue armed
    assert state == IntakeState.FAILED.value
    parked = store.rows(state=IntakeState.FAILED.value)
    assert len(parked) == 1 and parked[0].last_error == "still broken"
    assert store.depth() == 0, "a failed item is not queued work any more"
    assert q.next_item() is None, "nor may it be drained again"
    assert q.stats()["failed"] == 1


def test_finish_deletes_and_counts_only_after_a_terminal_outcome():
    q, store, _ = _queue()
    q.accept(org_id="o1", graph=None, session_id="s1", payload={})
    item = q.next_item()
    q.finish(item)
    assert store.rows() == []
    assert q.stats()["completed"] == 1


# ── the label is what makes the queue findable ─────────────────────────────

def test_the_queue_label_is_a_single_constant_used_by_every_statement():
    """A second spelling of the label is how a row becomes invisible to the
    query that was supposed to drain it."""
    assert CAPTURE_INTAKE_LABEL == "CaptureIntake"
    from tortoise import capture_intake as mod

    cyphers = [
        mod._CLAIM_CYPHER, mod._PUT_CYPHER, mod._COMPLETE_CYPHER,
        mod._RELEASE_CYPHER, mod._DEPTH_CYPHER, mod._OLDEST_CYPHER,
        mod._ROWS_CYPHER, mod._RECLAIM_STALE_CYPHER, mod._SESSION_CYPHER,
    ]
    for cypher in cyphers:
        assert f":{CAPTURE_INTAKE_LABEL}" in cypher


# ── #14707 review P3: `position` is the depth AHEAD of the item ────────────

def test_position_is_the_depth_ahead_of_the_item_not_including_it():
    """`position` was read AFTER `put()`, so it included the item itself.

    D2 publishes `position` as "the queue depth ahead of it" — the only honest
    ETA we have — and an empty queue's first item answered `position: 1` with
    `depth: 1`. Both numbers are now read from the same pre-put depth.
    """
    q, _store, _ = _queue()
    positions = [
        q.accept(org_id="o1", graph=None, session_id=f"s{i}",
                 payload={}).position
        for i in range(3)
    ]
    assert positions == [0, 1, 2], (
        f"positions must be the depth AHEAD of each item (got {positions})")
    acc = q.accept(org_id="o1", graph=None, session_id="s3", payload={})
    assert acc.position == 3 and acc.depth == 4, (
        "`position` is ahead-of-it; `depth` counts it (got "
        f"position={acc.position} depth={acc.depth})")


# ── #14707 review P1/P2: a transient fault must not spend an attempt ───────

def test_defer_requeues_without_charging_an_attempt():
    """⛔ A CONDITION is not the item's fault.

    `fail()` exists for the attempt bound; `defer()` is for capacity, a
    same-session capture in flight, our own store answering 503. Counting those
    against `CAPTURE_INTAKE_MAX_ATTEMPTS` turns a busy box into a `failed` row,
    i.e. a lost turn.
    """
    q, store, clock = _queue()
    q.accept(org_id="o1", graph=None, session_id="s1", payload={})
    for _ in range(CAPTURE_INTAKE_MAX_ATTEMPTS + 2):
        item = q.next_item()
        assert item is not None, "a deferred item must stay claimable"
        assert item.attempts == 1, (
            "the claim's attempt must be REFUNDED by the defer — otherwise "
            f"repeated transients exhaust the item (attempts={item.attempts})")
        state = q.defer(item, "capacity")
        assert state == IntakeState.QUEUED.value
        clock.advance(3600)
    assert store.rows(state=IntakeState.FAILED.value) == []
    assert q.stats()["failed"] == 0


def test_park_fails_immediately_on_a_verdict_that_cannot_change():
    """A 402/409/400 verdict does not become answerable by waiting."""
    q, store, _clock = _queue()
    q.accept(org_id="o1", graph=None, session_id="s1", payload={})
    item = q.next_item()
    assert q.park(item, "402 quota") == IntakeState.FAILED.value
    parked = store.rows(state=IntakeState.FAILED.value)
    assert len(parked) == 1
    assert parked[0].last_error == "402 quota"
    assert parked[0].attempts == 1, "parking does not refund — it was a real try"
    assert q.stats()["failed"] == 1


# ── #14707 review P2: the 202's status address reads the queue ─────────────

def test_queued_for_session_answers_the_live_row_and_ignores_a_parked_one():
    q, store, _clock = _queue()
    q.accept(org_id="o1", graph=None, session_id="s1", payload={})
    q.accept(org_id="o1", graph=None, session_id="s2", payload={})
    found = store.queued_for_session("s1")
    assert found is not None and found.session_id == "s1"
    assert store.queued_for_session("nope") is None
    # a PARKED row is not "queued": the status address must not claim a turn is
    # still coming when it has been given up on.
    item = q.next_item()
    q.park(item, "402 quota")
    assert store.queued_for_session(item.session_id) is None
