"""Task 3 tests — durable SDK event emission to :GraphEvent nodes.

Uses the shared sdk_factory fixture (tests/conftest.py, Task 1). No org_id
property anywhere — the graph namespace IS the team partition (plan-review P2).
"""
import json

import pytest

from tortoise import event_store


def _events(proj):
    # plan-review P2: no org_id property — the graph namespace IS the partition
    rows = proj.g.query(
        "MATCH (e:GraphEvent) RETURN properties(e) ORDER BY e.seq").result_set
    return [r[0] for r in rows]


def test_create_point_emits_graph_event(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "hello events")
    proj = sdk._get_proj()
    evs = _events(proj)
    assert len(evs) == 1
    assert evs[0]["type"] == "PointAdded"
    assert evs[0]["event_id"]
    assert json.loads(evs[0]["payload"])["id"] == p["id"]
    assert evs[0]["seq"] == 1


def test_dedup_create_does_not_emit(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    sdk.create_point("statement", "dedup me", dedup=True)
    sdk.create_point("statement", "dedup me", dedup=True)  # returns existing
    assert len(_events(sdk._get_proj())) == 1


def test_append_duplicate_event_id_rejected(sdk_factory, tmp_path):
    """plan-review P1: duplicate event_id append is rejected/dedup'd at the
    storage layer — append_event catches the constraint violation, logs, and
    skips (no-op). event_id is a server-side ULID; a collision is a retry
    artifact, never legitimate data."""
    from tortoise import event_store
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    event_store.ensure_event_schema(proj)
    seq1 = event_store.next_seq(proj)
    event_store.append_event(proj, seq1, "PointAdded", {"id": "p1"}, "evt-dup")
    n1 = len(_events(proj))
    seq2 = event_store.next_seq(proj)  # counter still advances
    event_store.append_event(proj, seq2, "PointAdded", {"id": "p1"}, "evt-dup")
    assert len(_events(proj)) == n1     # second append skipped (no-op)
    assert seq2 == seq1 + 1


def test_read_dedups_duplicate_event_ids(sdk_factory, tmp_path):
    """plan-review P1: two :GraphEvent nodes with the SAME event_id written
    directly via Cypher (bypassing append_event) → read_after returns one.
    (final-verification P2) The unique constraint is deliberately NOT
    installed here — a schema'd graph rejects the second duplicate CREATE
    (that rejection path is covered by test_append_duplicate_event_id_rejected
    and ensure_event_schema). This test pins the READ-side dedup contract on a
    constraint-free graph: seed dupes first, then read_after dedups."""
    from tortoise import event_store
    sdk = sdk_factory(tmp_path, ensure_schema=False)
    proj = sdk._get_proj()
    # NOTE: no create_point call — constraint must not be installed in this test
    proj.g.query(
        "CREATE (e:GraphEvent {seq: 90, ts: $ts, type: 'PointAdded', "
        "payload: $pl, event_id: 'dup-1'})", params={"ts": "x", "pl": "{}"})
    proj.g.query(
        "CREATE (e:GraphEvent {seq: 91, ts: $ts, type: 'PointAdded', "
        "payload: $pl, event_id: 'dup-1'})", params={"ts": "x", "pl": "{}"})
    evs = event_store.read_after(proj, 0, limit=100)  # raw node properties
    by_id = [e for e in evs if e.get("event_id") == "dup-1"]
    assert len(by_id) == 1  # deduped at read


def test_all_mutations_emit(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    s = sdk.create_point("statement", "src")
    t = sdk.create_point("statement", "tgt")
    op = sdk.create_operator("IMPL", s["id"], [t["id"]])
    sdk.retract_point(t["id"])
    old = sdk.create_point("statement", "old")
    new = sdk.create_point("statement", "new")
    sdk.supersede_point(old["id"], new["id"])
    # #2488/#2498: the invalidate emitter needs its OWN pair now — a repeat
    # invalidate on the superseded `old` is rejected by the shared lifecycle
    # guard (outdated=true is itself terminal).
    inv_old = sdk.create_point("statement", "inv-old")
    inv_new = sdk.create_point("statement", "inv-new")
    sdk.invalidate_point(inv_old["id"], inv_new["id"])
    sdk.annotate_operator(op["id"], 0.5, 0.5, 0.5, 0.5)
    types = [e["type"] for e in _events(sdk._get_proj())]
    assert types.count("PointAdded") == 6  # src, tgt, old, new, inv-old, inv-new
    assert "OperatorAdded" in types and "PointRetracted" in types
    assert "PointSuperseded" in types and "OperatorAnnotated" in types
    assert "PointInvalidated" in types  # #2488: invalidate_point emits


def test_content_edit_emits_nothing(sdk_factory, tmp_path):
    """plan-review P1: update_point non-status edits do NOT emit."""
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "before")
    sdk.update_point(p["id"], content="after")
    assert [e["type"] for e in _events(sdk._get_proj())] == ["PointAdded"]


def test_event_nodes_have_zero_relationships(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    sdk.create_point("statement", "isolated")
    proj = sdk._get_proj()
    assert proj.g.query("MATCH (e:GraphEvent)-[r]-() RETURN count(r)").result_set[0][0] == 0


def test_event_nodes_invisible_to_domain_queries(sdk_factory, tmp_path):
    """plan-review P2: get_point, search, and domain-label counts exclude events."""
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "visible")
    proj = sdk._get_proj()
    ev = _events(proj)[0]
    assert proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 1
    assert len(sdk.query()) == 1
    assert sdk.get_point(ev["event_id"]) == {}  # {} per get_point missing contract
    hits = sdk.tortoise_fts_query("visible", limit=50)  # search scans Points only
    hit_ids = {h["id"] for h in hits}
    assert p["id"] in hit_ids
    assert ev["event_id"] not in hit_ids
    domain = proj.g.query(
        "MATCH (n) WHERE NOT (n:GraphEvent) AND NOT (n:GraphEventMeta) "
        "AND NOT (n:EpMeta) AND NOT (n:Meta) RETURN count(n)"
    ).result_set[0][0]
    assert domain == 1  # only the Point; event + EpMeta/Meta bookkeeping nodes excluded


def test_seq_is_monotonic_under_concurrency(sdk_factory, tmp_path):
    """P1 (code-review): the previous version was VACUOUS — sdk_factory mints a
    fresh db per call, so 8 workers wrote to 8 isolated graphs and the final
    assertion ran on an empty graph (trivially true). Here we assert every
    worker's per-graph seqs are strictly monotonic+unique AND the run produced
    events at all (non-vacuous). TRUE cross-worker atomicity on ONE shared
    graph needs a live FalkorDB (TORTOISE_DB_URI=docker://...) — the embedded
    redislite client is not multi-connection-safe (documented on sdk_factory).

    #915: SDK construction is serialized (a lock) — 8 simultaneous embedded
    redislite server starts with AOF init can trip the 10s start window
    (Connection refused on the unix socket). The per-worker WRITES stay
    concurrent; only the server boots serialize, which is not what this test
    measures.
    """
    import threading
    errors = []
    per_worker: list[list[int]] = []
    _sdk_lock = threading.Lock()

    def worker(i):
        try:
            with _sdk_lock:
                s = sdk_factory(tmp_path)
            s.create_point("statement", f"c{i}")
            per_worker.append([e["seq"] for e in _events(s._get_proj())])
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    assert per_worker, "no worker produced events — test would be vacuous"
    for seqs in per_worker:
        assert sorted(seqs) == seqs and len(set(seqs)) == len(seqs),             f"non-monotonic or duplicate seqs: {seqs}"


def test_seq_is_monotonic_under_concurrency_live_falkor():
    """TRUE cross-worker atomicity on ONE shared live graph (#942).

    The embedded sibling test documents that real cross-worker atomicity
    needs a live FalkorDB — this is that path, run by CI's
    test-concurrency-falkor job (TORTOISE_DB_URI=docker://...). Skips
    visibly everywhere else. Deliberately does NOT use sdk_factory (it mints
    isolated embedded files — the vacuous pattern this test replaces).
    """
    import os  # noqa: I001
    import threading

    from _live_utils import _skip_unless_live_uri
    from tortoise.projection import FalkorProjection
    from tortoise.sdk import TortoiseSDK

    _skip_unless_live_uri()
    uri = os.environ["TORTOISE_DB_URI"]

    # Reset the shared graph (test-prefixed name passes _assert_test_graph).
    proj = FalkorProjection.from_uri(uri, graph_name="test_live_seq_tortoise")
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.close()

    # Warm-up: install the event schema + seq counter deterministically
    # before the threads race (next_seq is a server-side atomic MERGE).
    warm = TortoiseSDK(namespace="test_live_seq")
    assert warm._db_uri, "live test must resolve the URI backend (not embedded)"
    warm.create_point("statement", "warmup")
    warm.close()

    errors, per_worker = [], []

    def worker(i):
        try:
            s = TortoiseSDK(namespace="test_live_seq")
            s.create_point("statement", f"live-c{i}")
            per_worker.append([e["seq"] for e in _events(s._get_proj())])
            s.close()
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    union = sorted({seq for seqs in per_worker for seq in seqs})
    assert union == list(range(1, len(union) + 1)), (
        f"global seqs not contiguous 1..N — lost or duplicate seqs: {union}")
    assert len(union) >= 8, f"expected >= 8 events (warmup + 8 workers), got {len(union)}"


def test_purge_expired_removes_old_events(sdk_factory, tmp_path):
    """Task 7: events older than retention_days are purged (ts cutoff)."""
    from datetime import datetime, timedelta, timezone  # noqa: I001
    from tortoise import event_store
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    event_store.ensure_event_schema(proj)
    now = datetime.now(timezone.utc)  # noqa: UP017
    old = (now - timedelta(days=31)).isoformat()
    fresh = (now - timedelta(days=1)).isoformat()
    event_store.append_event(proj, 1, "PointAdded", {"id": "a"}, "ev-old", ts=old)
    event_store.append_event(proj, 2, "PointAdded", {"id": "b"}, "ev-fresh", ts=fresh)
    deleted = event_store.purge_expired(proj, retention_days=30)
    assert deleted == 1
    evs = event_store.read_after(proj, 0)
    assert [e["event_id"] for e in evs] == ["ev-fresh"]


def test_purge_overflow_caps_per_team(sdk_factory, tmp_path):
    """Task 7: size cap drops the OLDEST events (by seq)."""
    from tortoise import event_store
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    event_store.ensure_event_schema(proj)
    for i in range(1, 4):
        event_store.append_event(proj, i, "PointAdded", {"id": str(i)}, f"ev-{i}")
    deleted = event_store.purge_overflow(proj, max_events=2)
    assert deleted == 1
    evs = event_store.read_after(proj, 0)
    assert [e["seq"] for e in evs] == [2, 3]  # oldest (seq 1) dropped


def test_purged_seq_cursor_expires(sdk_factory, tmp_path):
    """Task 7: after purge, a cursor pointing at a purged seq → expired (410)."""
    from datetime import datetime, timedelta, timezone  # noqa: I001
    from tortoise import event_store
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    now = datetime.now(timezone.utc)  # noqa: UP017
    event_store.ensure_event_schema(proj)
    event_store.next_seq(proj)  # creates GraphEventMeta (first_seq=1)
    # 1. a FRESH event is pollable → cursor points at seq 1
    event_store.append_event(proj, 1, "PointAdded", {"id": "a"}, "ev-1", ts=now.isoformat())
    stale = sdk.poll_events()["next_cursor"]
    # 2. time passes: backdate the event past retention, purge it (refreshes
    #    the first_seq watermark to last_seq+1 = 2)
    proj.g.query("MATCH (e:GraphEvent) SET e.ts = $old_ts",
                 params={"old_ts": (now - timedelta(days=31)).isoformat()})
    deleted = event_store.purge_expired(proj, retention_days=30)
    assert deleted == 1
    # 3. the stale cursor (seq 1) is now below the watermark → expired
    with pytest.raises(ValueError, match="cursor expired"):
        sdk.poll_events(after=stale)


# ── #5379: the seq allocator has a ceiling ─────────────────────────────────
#
# `next_seq` used to be an unbounded `m.last_seq + 1`. Both ways out of the
# event-store integer domain are UNDELIVERABLE through `read_after`'s
# `seq > cursor`, i.e. the #4653 harm (an event that exists and is never
# delivered, with no 410 to tell the subscriber):
#
# * Above `MAX_SEQ` (2**53 - 1) the `seq` RANGE index compares in double
#   precision, so a cursor that lands just below such a seq skips it silently —
#   `2**53 + 1` rounds to `2**53` and fails `seq > 2**53`.
# * At INT64_MAX the bump wrapped NEGATIVE, and no accepted cursor can ever
#   match a negative seq.
#
# The tests below pin the refusal at the allocator (not a capped-but-returned
# value, which would reuse a seq already issued), the delivery property that
# motivated it — walked page-by-page with the lossy index INSTALLED, since the
# index is what makes the read lossy — and the watermark arithmetic that the
# same out-of-domain counter feeds.


def _counter(proj):
    """`(last_seq, first_seq)` of the :GraphEventMeta node, or None when absent."""
    rows = proj.g.query(
        "MATCH (m:GraphEventMeta) RETURN m.last_seq, m.first_seq").result_set
    return tuple(rows[0]) if rows else None


def _set_counter(proj, last_seq, first_seq=1):
    """Seed the allocator directly — the shape a live/restored counter has.

    The seed is ASSERTED immediately: the embedded engine silently drops some
    extreme overwrites (a negative target onto a property holding INT64_MAX),
    so an ignored setup write must fail as a SETUP error here rather than
    surface later as a confusing assertion about the code under test.
    """
    proj.g.query(
        "MERGE (m:GraphEventMeta) SET m.last_seq = $last, m.first_seq = $first",
        params={"last": last_seq, "first": first_seq})
    seeded = _counter(proj)
    assert seeded is not None and seeded[0] == last_seq, (
        f"the counter seed did not land: asked for last_seq={last_seq!r}, the "
        f"engine holds {seeded!r} — a seed the engine silently ignores is a "
        f"setup failure, not a result")


def _deliver_all(proj, page=1, max_pages=50):
    """Walk the delivery cursor exactly as a subscriber does — one page at a
    time via `read_after`, following `next_cursor` (the last seq of the page).

    A page size of 1 is what makes the walk see the index loss: a single wide
    page (`limit=100`) jumps the cursor to the LAST seq and never re-queries
    across the boundary, so the skipped row is invisible.
    """
    from tortoise import event_store as _es

    seqs: list[int] = []
    cursor = 0
    for _ in range(max_pages):
        evs = _es.read_after(proj, cursor, limit=page)
        if not evs:
            return seqs
        seqs.extend(e["seq"] for e in evs)
        cursor = evs[-1]["seq"]
    raise AssertionError("the delivery walk never drained — cursor did not advance")


def test_next_seq_refuses_at_the_ceiling(sdk_factory, tmp_path):
    """#5379: `MAX_SEQ` is the last legal seq; the next call REFUSES.

    FAILING VALUE: the counter at `MAX_SEQ` was bumped to `MAX_SEQ + 1` and
    returned, i.e. the allocator minted the first seq the `seq` range index
    cannot compare exactly. Capping the stored value while still returning it
    is not a fix — the emitter would be handed a seq the store already issued
    (#4653).
    """
    from tortoise.event_store import MAX_SEQ

    sdk = sdk_factory(tmp_path, ensure_schema=True)
    proj = sdk._get_proj()
    _set_counter(proj, MAX_SEQ - 1)
    assert event_store.next_seq(proj) == MAX_SEQ, (
        "a counter at MAX_SEQ - 1 must still hand out MAX_SEQ — it is the last "
        "exactly-comparable seq in the domain")
    with pytest.raises(ValueError, match="exhausted"):
        event_store.next_seq(proj)
    assert _counter(proj) == (MAX_SEQ, 1), (
        "a refusal must leave the allocator where it was — pre-fix this call "
        "wrote and returned MAX_SEQ + 1 (undeliverable through the index)")


def test_next_seq_refuses_a_counter_at_int64_max_instead_of_wrapping(
        sdk_factory, tmp_path):
    """#5379: the bump at INT64_MAX wrapped NEGATIVE rather than refusing.

    FAILING VALUE: `MAX_INT64 + 1` in the graph wrapped to `-9223372036854775808`
    and became the next event's seq. No cursor the codec accepts is negative
    (`_decode_cursor` rejects one), so `seq > cursor` can never deliver it and
    the counter could never recover on its own — it stayed negative and kept
    handing out negative seqs.
    """
    from tortoise.event_store import MAX_INT64

    sdk = sdk_factory(tmp_path, ensure_schema=True)
    proj = sdk._get_proj()
    _set_counter(proj, MAX_INT64)
    with pytest.raises(ValueError, match="exhausted"):
        event_store.next_seq(proj)
    assert _counter(proj) == (MAX_INT64, 1), (
        "the counter must be left at INT64_MAX — pre-fix the bump moved it to "
        "INT64_MIN")


def test_next_seq_refuses_a_negative_counter(sdk_factory, tmp_path):
    """#5379: a counter already below the domain is refused, not bumped.

    REACHABLE: this is exactly what the pre-fix wrap left on disk (see the
    INT64_MAX test above). Bumping it would keep issuing negative seqs, which
    is the same undeliverable shape as the wrap itself; re-seeding it here
    could re-issue a seq an existing :GraphEvent already uses.
    """
    sdk = sdk_factory(tmp_path, ensure_schema=True)
    proj = sdk._get_proj()
    _set_counter(proj, -(2 ** 63))
    with pytest.raises(ValueError, match="exhausted"):
        event_store.next_seq(proj)
    assert _counter(proj) == (-(2 ** 63), 1), "a refusal must not move it"


def test_every_allocated_seq_survives_the_indexed_delivery_walk(
        sdk_factory, tmp_path):
    """#5379 end-to-end: every seq the allocator HANDS OUT is delivered.

    Walks the cursor one page at a time with the `seq` RANGE index installed
    (`ensure_schema=True`) — the index is what makes the read lossy above
    2**53. FAILING VALUE / pre-fix: the allocator hands out `MAX_SEQ`, `2**53`
    and `2**53 + 1`; the walk delivers the first two, SKIPS `2**53 + 1` (it
    rounds to `2**53` as a double, failing `seq > 2**53`), and the subscriber
    reads `[]` forever while `read_after(2**53 - 1)` still returns the row.
    Post-fix the allocator refuses the seq above `MAX_SEQ`, so the walk
    delivers everything it issued.
    """
    from tortoise.event_store import MAX_SEQ

    sdk = sdk_factory(tmp_path, ensure_schema=True)
    proj = sdk._get_proj()
    _set_counter(proj, MAX_SEQ - 1)

    issued: list[int] = []
    refusals = 0
    for _ in range(4):  # 3 past the counter's start: the ceiling must bite
        try:
            seq = event_store.next_seq(proj)
        except ValueError:
            refusals += 1
            continue
        issued.append(seq)
        event_store.append_event(
            proj, seq, "PointAdded", {"id": f"p{seq}"}, f"ev-{seq}")

    delivered = _deliver_all(proj)
    assert delivered == issued, (
        f"the indexed delivery walk dropped {sorted(set(issued) - set(delivered))} "
        f"of the seqs the allocator issued ({issued} issued, {delivered} "
        f"delivered) — an event that exists and is never delivered")
    assert issued and max(issued) <= MAX_SEQ, (
        f"the allocator must never issue a seq above MAX_SEQ (issued {issued})")
    assert refusals >= 1, (
        "the ceiling must be reached and refused — pre-fix all 4 calls were "
        "handed a seq")


def test_seq_range_index_skips_an_exact_int_above_2_pow_53(sdk_factory, tmp_path):
    """The ENGINE behaviour ``MAX_SEQ`` exists for — a tripwire, not our code.

    ``MAX_SEQ`` rests on numeric range comparison being done in DOUBLE
    PRECISION: `2**53 + 1` is not representable as a double (it rounds to
    `2**53`), so a comparison against `2**53` cannot see that row. That is why
    the ceiling is ``2**53 - 1`` rather than "whenever an int64 runs out".

    The premise is pinned in the form that is INDEPENDENT of the planner and of
    the engine build: the comparison is forced to doubles explicitly
    (``toFloat(e.seq) > $after`` with a float bound) instead of relying on the
    engine choosing its `seq` RANGE index for an int-vs-int predicate. The
    int-vs-int form is engine- and platform-dependent — some bundled/embedded
    builds compare int64 exactly and return the row at `2**53 + 1` while the
    production image drops it — so a red there would misdirect a maintainer to
    "revisit MAX_SEQ" for what is an environment artefact (#5379 review). What
    this DOES catch is the premise itself becoming false: an engine whose
    numeric comparison is exact would return the row even as a double, and then
    ``MAX_SEQ`` should be re-derived.
    """
    from tortoise.event_store import MAX_SEQ

    sdk = sdk_factory(tmp_path, ensure_schema=True)
    proj = sdk._get_proj()
    base = 2 ** 53
    assert base > MAX_SEQ, "the ceiling must sit below the first lossy seq"
    # Engine-independent part of the premise: 2**53 + 1 is not a double.
    assert float(base + 1) == float(base), (
        "2**53 + 1 must not be representable as a double")
    for seq in (base, base + 1, base + 2):
        proj.g.query("CREATE (:GraphEvent {seq:$s, event_id:$e})",
                     params={"s": seq, "e": f"ev-{seq}"})
    got = [r[0] for r in proj.g.query(
        "MATCH (e:GraphEvent) WHERE toFloat(e.seq) > $after "
        "RETURN e.seq ORDER BY e.seq ASC",
        params={"after": float(base)}).result_set]
    assert got == [base + 2], (
        f"the engine's double-precision range comparison no longer drops the "
        f"row at 2**53 + 1 (got {got}) — re-derive whether MAX_SEQ is still "
        f"needed")


def test_refresh_first_seq_saturates_a_counter_at_int64_max(sdk_factory, tmp_path):
    """#5379: the INT64_MAX (wrapped) counter must not wrap the purge floor.

    FAILING VALUE: `_refresh_first_seq` computed `m.last_seq + 1` in the graph,
    so a counter at INT64_MAX (what an exhausted allocator looks like on disk)
    wrote `first_seq = -9223372036854775808`. `poll_events`'s
    `after_seq < first_seq` guard can then never fire — no accepted cursor is
    below a negative floor — so a subscriber parked above a truncated stream
    read `[]` forever instead of getting the truthful 410.

    Each out-of-domain counter gets its OWN fresh graph: the embedded engine
    silently DROPS a negative overwrite of a property holding INT64_MAX (the
    seed would not land), and an ignored setup write must not surface as a
    confusing assertion about `_refresh_first_seq`.
    """
    from tortoise.event_store import MAX_INT64, _refresh_first_seq

    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    _set_counter(proj, MAX_INT64)
    _refresh_first_seq(proj)
    assert _counter(proj) == (MAX_INT64, MAX_INT64), (
        "the empty-log floor saturates at the top of the first_seq domain "
        "(MAX_INT64) — never a wrapped negative")


def test_refresh_first_seq_saturates_a_negative_counter(sdk_factory, tmp_path):
    """#5379: a counter below the domain saturates the floor, never 0.

    A floor of 0 cannot fire `poll_events`' expiry guard at all — every
    reachable cursor is `>= 1` (`after_seq == 0` is the "from the start"
    sentinel that SKIPS the check), so `1 < 0` is false and the subscriber
    parked above the truncated stream reads `[]` forever with no 410. The
    allocator REFUSES a negative counter, so its next write is unreachable and
    EVERY reachable cursor is genuinely expired: the floor saturates above the
    whole cursor range, exactly as the wrap branch does.
    """
    from tortoise.event_store import MAX_INT64, _refresh_first_seq

    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    _set_counter(proj, -(2 ** 63))
    _refresh_first_seq(proj)
    assert _counter(proj) == (-(2 ** 63), MAX_INT64), (
        "a counter below the domain saturates the floor above every reachable "
        "cursor — a floor of 0 would leave the 410 guard unable to fire")


def test_negative_counter_floor_still_expires_a_cursor(sdk_factory, tmp_path):
    """#5379 review: the saturated negative-counter floor answers 410.

    Pins the DELIVERY contract, not just the number: a subscriber whose cursor
    sits above the truncated stream must get `cursor expired` (410) rather than
    `[]` forever — the #4653 harm the change exists to remove. FAILING VALUE
    on a floor of 0: `poll_events` returns `{"events": []}` where the 410 is
    required.
    """
    from tortoise.event_store import MAX_INT64, _refresh_first_seq

    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    _set_counter(proj, -(2 ** 63))
    _refresh_first_seq(proj)
    assert _counter(proj) == (-(2 ** 63), MAX_INT64), "seed must land"

    with pytest.raises(ValueError, match="cursor expired"):
        sdk.poll_events(after=sdk._encode_cursor(5))
    with pytest.raises(ValueError, match="cursor expired"):
        sdk.poll_events(after=sdk._encode_cursor(2 ** 60))
    # The `after=None` sentinel stays exempt (it reads from the start).
    assert sdk.poll_events()["events"] == []


def test_refresh_first_seq_keeps_a_floor_above_max_seq(sdk_factory, tmp_path):
    """#5379: the normal contract is unchanged, including above MAX_SEQ.

    The last legal write leaves a floor of `MAX_SEQ + 1` — `first_seq` is
    legitimately ABOVE `MAX_SEQ` (MAX_SEQ's own docstring) and is bounded only
    by `MAX_INT64`.
    """
    from tortoise.event_store import MAX_SEQ, _refresh_first_seq

    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    _set_counter(proj, MAX_SEQ)
    _refresh_first_seq(proj)
    assert _counter(proj) == (MAX_SEQ, MAX_SEQ + 1)


def test_exhausted_allocator_never_reuses_a_seq_on_emit(sdk_factory, tmp_path):
    """#5379: at the ceiling the emit FAILS rather than re-issuing a seq.

    `_emit_event` treats the graph store as best-effort, so the mutation still
    succeeds — but NO graph event may be appended and the counter must not
    move: a capped seq handed back to the emitter would reuse an ordering key
    the store already issued (#4653's collision), and reusing it is exactly
    what a `CASE` that saturates `last_seq` would do on every later emit.
    """
    from tortoise.event_store import MAX_SEQ

    sdk = sdk_factory(tmp_path, ensure_schema=True)
    sdk.create_point("statement", "before the ceiling")
    proj = sdk._get_proj()
    before = [(e["seq"], e["event_id"]) for e in event_store.read_after(proj, 0)]
    assert before, "the fixture must really emit an event"

    _set_counter(proj, MAX_SEQ)
    sdk.create_point("statement", "at the ceiling")  # mutation continues

    assert [(e["seq"], e["event_id"])
            for e in event_store.read_after(proj, 0)] == before, (
        "no graph event may be appended when the allocator refuses")
    assert _counter(proj) == (MAX_SEQ, 1), "the refusal must not move the counter"


def test_next_seq_ceiling_is_atomic_across_workers_live_falkor():
    """#5379: concurrent emitters split the remaining domain exactly.

    The grant decision must be in the SAME query as the bump. A read-then-bump
    implementation lets two workers read `MAX_SEQ - 1` and both bump, so the
    second writes `MAX_SEQ + 1` — the first undeliverable seq, and the exact
    race this ceiling exists to remove. Starts 8 workers at `MAX_SEQ - 3` and
    asserts exactly 3 DISTINCT grants, 5 refusals, and a counter that never
    left the domain. Runs in CI's `test-concurrency-falkor` job, which
    provisions the live URI and selects this nodeid explicitly (see
    `.github/workflows/python-ci.yml`) — skips visibly everywhere else, since
    the embedded redislite client is not multi-connection-safe (see
    sdk_factory).
    """
    import os  # noqa: I001
    import threading

    from _live_utils import _skip_unless_live_uri
    from tortoise.event_store import MAX_SEQ
    from tortoise.projection import FalkorProjection
    from tortoise.sdk import TortoiseSDK

    _skip_unless_live_uri()
    uri = os.environ["TORTOISE_DB_URI"]
    namespace = "test_seq_ceiling"

    # Seed the counter deterministically before the workers race.
    proj = FalkorProjection.from_uri(uri, graph_name=f"{namespace}_tortoise")
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.g.query(
        "MERGE (m:GraphEventMeta) SET m.last_seq = $last, m.first_seq = 1",
        params={"last": MAX_SEQ - 3})
    proj.close()

    granted: list[int] = []
    refused: list[int] = []
    errors: list[str] = []

    def worker(i):
        try:
            s = TortoiseSDK(namespace=namespace)
            try:
                granted.append(event_store.next_seq(s._get_proj()))
            finally:
                s.close()
        except ValueError:
            refused.append(i)
        except Exception as e:  # pragma: no cover
            errors.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert not errors, errors
    assert sorted(granted) == [MAX_SEQ - 2, MAX_SEQ - 1, MAX_SEQ], (
        f"the 3 remaining seqs must be handed out once each, contiguously — "
        f"got {sorted(granted)}")
    assert len(refused) == 5, (
        f"the other 5 workers must be refused, not handed a duplicate — "
        f"granted {sorted(granted)}, refused {len(refused)}")
    assert len(set(granted)) == len(granted), "no seq may be issued twice"
