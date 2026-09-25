"""#4653 — `rebuild_all` must not reset the `:GraphEventMeta` event-log watermark.

The wipe in `rebuild_all` is an unconditional `MATCH (n) DETACH DELETE n`, which
destroys `:GraphEventMeta` — the per-graph allocator (`last_seq`) handed to
`event_store.next_seq`. No replay pass recreates it (`:GraphEvent` rows are not
replayed at all — #4664, unfixed), so before this change the next emit MERGEs a
fresh counter at `1` and hands out a `seq` the graph already issued. Because
`read_after` is `seq > cursor`, EVERY consumer holding a cursor at or above the
restart silently under-counts — the identical harm
`hosted_backup._restore_event_meta` (#3902) prevents on the backup/restore path.

`last_seq` is not re-derivable: the JSONL journal carries no `seq` (and
`_emit_event` writes `:GraphEvent` rows with no JSONL record for payload-only
emits), so the high-water mark exists only in the wiped node. It is therefore
CARRIED in the durable #2943 pre-wipe sidecar and re-established post-replay with
the `_restore_event_meta` monotonicity guard (`max(carried, max(replayed seq))`);
`first_seq` stays re-derived (`min`, or `last_seq + 1` when the log is empty).

Lane: either. The fixtures build the SDK the same way the embedded carve-out
suite does, and the bulk-wipe guard passes on the redirected `test_*_tortoise`
graph too, so this file needs no `carve_out` classification.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pytest

from tortoise import event_store
from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK

# ── harness ──────────────────────────────────────────────────────────────────


def _mk_sdk(tmp_path, name="wm.db", namespace=None):
    events = tmp_path / "events"
    events.mkdir(parents=True, exist_ok=True)
    sdk = TortoiseSDK(
        db_path=str(tmp_path / name),
        # A `test_`-prefixed namespace keeps the bulk-wipe guard passing under
        # the docker redirect (graph name becomes `test_..._tortoise`).
        namespace=namespace or f"test_wm_{os.urandom(4).hex()}",
        event_log_path=str(events / "events.jsonl"),
    )
    return sdk, events


def _meta(sdk):
    """`(last_seq, first_seq)` of the counter node, or None when absent."""
    rows = sdk._get_proj().g.query(
        "MATCH (m:GraphEventMeta) RETURN m.last_seq, m.first_seq").result_set
    return tuple(rows[0]) if rows else None


def _seqs(sdk) -> list[int]:
    rows = sdk._get_proj().g.query(
        "MATCH (e:GraphEvent) RETURN e.seq ORDER BY e.seq").result_set
    return [r[0] for r in rows]


def _sidecar_path(events_dir) -> str:
    from tortoise.projection import prewipe_snapshot_path
    return prewipe_snapshot_path(str(events_dir))


def _plant(path, payload: dict) -> None:
    from tortoise.projection import _write_prewipe_snapshot
    _write_prewipe_snapshot(str(path), payload)


def _sidecar_payload(**overrides) -> dict:
    """A valid sidecar payload at the CURRENT version; overrides whole sections."""
    from tortoise.projection import _PREWIPE_SNAPSHOT_VERSION
    payload = {
        "version": _PREWIPE_SNAPSHOT_VERSION,
        "created_at": "2026-01-01T00:00:00Z",
        "synthetic_events": [],
        "batch_snapshot": [],
        "batch_point_links": [],
        "session_snapshot": [],
        "session_point_links": [],
        "config_snapshot": [],
        "event_meta": [],
    }
    payload.update(overrides)
    return payload


def _seen_points(sdk) -> int:
    return int(sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)").result_set[0][0])


# ── 1. the defect: the watermark survives a full rebuild ─────────────────────


def test_rebuild_all_rederives_event_meta_watermark(tmp_path):
    """The headline pin (issue #4653, I1).

    FAILING VALUE: after the rebuild, `:GraphEventMeta.last_seq` is 3 (carried)
    and `first_seq` is 4 (`_refresh_first_seq`'s empty-log contract), so the
    next `next_seq` is 4 — pre-fix it was `1`, colliding with the `seq` 1 the
    pre-rebuild graph had already handed out.

    REACHABLE IN THE FIXTURE: three real points are created through the SDK
    emit path (`_emit_event` → `ensure_event_schema` → `next_seq` →
    `append_event`), so seqs 1..3 AND the counter node genuinely exist before
    the wipe — asserted below, not assumed.
    """
    sdk, events = _mk_sdk(tmp_path)
    for i in range(3):
        sdk.create_point("statement", f"pre-rebuild {i}")
    proj = sdk._get_proj()

    assert _seqs(sdk) == [1, 2, 3]
    assert _meta(sdk) == (3, 1), (
        "the fixture must really start from a 3-event watermark, or the pin "
        "below tests nothing")
    # A real subscriber, caught up at the last event BEFORE the rebuild.
    parked = sdk.events_poll(after=None)["next_cursor"]

    counts = proj.rebuild_all(str(events))
    assert counts["nodes"] == 3, "the replay must actually restore the points"
    assert _seqs(sdk) == [], (
        "#4664 context: the rebuild does NOT replay :GraphEvent rows — the "
        "watermark is the only thing that can carry the allocator forward")

    assert _meta(sdk) == (3, 4), (
        "#4653: the carried high-water mark must survive the wipe, and "
        "first_seq must say the stream was truncated (last_seq + 1)")

    # The allocator continues ABOVE every seq already handed out.
    assert event_store.next_seq(proj) == 4, (
        "#4653/I1: next_seq must continue at max(previous seq) + 1, not restart "
        "at 1 and collide with replayed seqs")
    proj.g.query("MATCH (m:GraphEventMeta) SET m.last_seq = 3")

    # …and the concrete consumer harm is gone AT THE DELIVERY SEAM (not merely
    # at `read_after`). The pre-rebuild graph handed out seqs 1..3 and the
    # rebuild destroyed those rows (#4664), so the parked cursor is truthfully
    # EXPIRED (410 "replay from tail") rather than silently reading []
    # forever — and a tail poll resumes at seq 4. Pre-fix the fresh stream
    # restarted at 1, so `read_after(3)` returned [] and NOTHING told the
    # subscriber: it starved for good with its cursor still "valid".
    sdk.create_point("statement", "post-rebuild")
    assert _seqs(sdk) == [4]
    delivered = event_store.read_after(proj, 3)
    assert [e["seq"] for e in delivered] == [4], (
        "#4653: read_after must never hand the parked cursor [] while a NEW "
        "event occupies seq 1 — that was the silent under-count")
    with pytest.raises(ValueError, match="cursor expired"):
        sdk.events_poll(after=parked)
    assert [e["seq"] for e in sdk.events_poll(after=None)["events"]] == [4], (
        "the subscriber re-syncs from the tail onto the POST-rebuild stream")


def test_rebuild_all_never_moves_the_watermark_backward_on_retry(tmp_path):
    """A LOWER fresh capture must never overwrite the carried high-water mark.

    FAILING VALUE: the union of a leftover `last_seq = 500` with a fresh live
    capture of `3` keeps `500`, so the retry restores 500 and the next seq is
    501. Under a fresh-wins union (the rule this section first shipped with)
    the union keeps 3 — the allocator moves BACKWARD and re-issues seqs the
    pre-interruption graph already handed out, which is the #4653 harm
    reintroduced by the repair.

    REACHABLE IN THE FIXTURE: the sidecar is exactly what an interrupted
    rebuild writes (`event_meta` carrying 500), and the live counter is
    genuinely LOWER (3, from three real emits) — the post-wipe-reset shape the
    recovery path really produces when the app emits before the retry. The
    replayed log cannot correct it either: `:GraphEvent` rows are not replayed
    (#4664), so `max(:GraphEvent.seq)` is null after the wipe.
    """
    sdk, events = _mk_sdk(tmp_path)
    for i in range(3):
        sdk.create_point("statement", f"post-wipe emit {i}")
    assert _meta(sdk) == (3, 1), "the live capture must really be the LOWER one"
    _plant(Path(_sidecar_path(events)),
           _sidecar_payload(event_meta=[{"last_seq": 500}]))

    sdk._get_proj().rebuild_all(str(events))

    assert _meta(sdk) == (500, 501), (
        "#4653: the carried high-water mark must win over a lower post-wipe "
        "capture — a fresh-wins union re-issues seqs 3.. and starves every "
        "subscriber parked above them")
    assert event_store.next_seq(sdk._get_proj()) == 501


def test_rebuild_all_keeps_the_fresh_capture_when_it_is_higher(tmp_path):
    """The other half of the max: a NEWER (higher) live counter still wins.

    FAILING VALUE: a leftover carrying 1 and a live counter at 3 restore 3
    (next seq 4). A leftover-wins union keeps 1 and would re-issue seqs 1..3.
    Note what this ordering cannot detect: with the leftover LOW, fresh-wins and
    max agree, so a regression to fresh-wins passes here —
    `test_rebuild_all_never_moves_the_watermark_backward_on_retry` is that pin.

    REACHABLE: three real emits drive the live counter to 3, and the planted
    sidecar carries the lower value a pre-emit rescue file would.
    """
    sdk, events = _mk_sdk(tmp_path)
    for i in range(3):
        sdk.create_point("statement", f"live {i}")
    assert _meta(sdk) == (3, 1)
    _plant(Path(_sidecar_path(events)),
           _sidecar_payload(event_meta=[{"last_seq": 1}]))

    sdk._get_proj().rebuild_all(str(events))

    assert _meta(sdk) == (3, 4)


def test_event_meta_union_takes_the_maximum_of_both_legs():
    """The singleton section's merge rule, pinned directly.

    FAILING VALUE: every union of a `last_seq` pair yields the MAXIMUM, in
    either argument order, and a multi-entry (hand-planted) section collapses
    to its maximum rather than its last element. A presence-wins merge fails
    both directions.

    REACHABLE: `_union_prewipe_snapshot` is the exact function the recovery
    path calls, driven here with the two sections it receives.
    """
    from tortoise.projection import _SNAPSHOT_SECTIONS, _union_prewipe_snapshot

    def _sec(**kw):
        return {**{s: [] for s in _SNAPSHOT_SECTIONS}, **kw}

    low = _sec(event_meta=[{"last_seq": 1}])
    high = _sec(event_meta=[{"last_seq": 500}])
    empty = _sec()

    def merged(left, right):
        return _union_prewipe_snapshot(left, right)["event_meta"]

    assert merged(high, low) == [{"last_seq": 500}]
    assert merged(low, high) == [{"last_seq": 500}]
    assert merged(empty, high) == [{"last_seq": 500}]
    assert merged(high, empty) == [{"last_seq": 500}]
    assert merged(None, empty) == []
    planted = _sec(event_meta=[{"last_seq": 2}, {"last_seq": 9}])
    assert merged(planted, empty) == [{"last_seq": 9}], (
        "a hand-planted multi-entry section must collapse to its MAXIMUM, not "
        "to its last element")


def test_rebuild_journal_only_engine_keeps_watermark(tmp_path):
    """The sibling engine: `rebuild(log)` wipes the same node.

    FAILING VALUE: `next_seq` returns 2 after a journal-only rebuild of a
    one-event graph (pre-fix: 1).

    REACHABLE: the journal is the one the SDK just wrote for the point created
    above, and `rebuild()` replays it, so the graph is non-empty afterwards.
    """
    sdk, events = _mk_sdk(tmp_path)
    sdk.create_point("statement", "only event")
    proj = sdk._get_proj()
    assert _meta(sdk) == (1, 1)

    proj.rebuild(EventLog(str(events / "events.jsonl")))

    assert _seen_points(sdk) == 1
    assert _meta(sdk) == (1, 2)
    assert event_store.next_seq(proj) == 2


# ── 2. crash-safety: the mark is carried durably, not just in memory ─────────


def test_rebuild_all_watermark_survives_interrupted_rebuild_retry(tmp_path):
    """A rebuild that died mid-replay must not lose the allocator on retry.

    FAILING VALUE: with the live graph already empty (the interrupted run's
    wipe landed) and a leftover sidecar carrying `last_seq = 7`, the retry
    restores 7 and the next seq is 8 — pre-fix the retry saw no counter and
    restarted at 1 (the in-memory-only fix would do the same).

    REACHABLE IN THE FIXTURE: the sidecar is planted exactly as the interrupted
    run would have written it (`_write_prewipe_snapshot`), and the live graph is
    genuinely counter-free because nothing was ever emitted into it.
    """
    sdk, events = _mk_sdk(tmp_path)
    assert _meta(sdk) is None, "the live graph must be the post-wipe, empty one"
    _plant(Path(_sidecar_path(events)),
           _sidecar_payload(event_meta=[{"last_seq": 7}]))

    sdk._get_proj().rebuild_all(str(events))

    assert _meta(sdk) == (7, 8), (
        "#4653: the durable carrier is the only record of the allocator once "
        "the wipe lands — dropping it resets every subscriber's cursor")
    assert event_store.next_seq(sdk._get_proj()) == 8


def test_rebuild_all_carries_the_watermark_in_the_prewipe_sidecar(
        tmp_path, monkeypatch):
    """The mark must be IN the sidecar, not merely read in memory.

    FAILING VALUE: the written payload's `event_meta` section is
    `[{"last_seq": 3}]` — a fix that only reads the live graph before the wipe
    writes no such section, and then a crash between the write and the replay
    loses the allocator permanently.

    REACHABLE: the sidecar is written because the new `event_meta` section is
    itself non-empty (`snapshot_pending` is `any(merged[section] for section in
    _SNAPSHOT_SECTIONS)`), NOT because of the Points: they are created through
    the SDK emit path, so replaying them would rebuild them and
    `synthetic_events` stays empty.
    """
    from tortoise import projection as pr

    written: list[dict] = []
    real = pr._write_prewipe_snapshot

    def spy(path, payload):
        written.append(payload)
        return real(path, payload)

    monkeypatch.setattr(pr, "_write_prewipe_snapshot", spy)

    sdk, events = _mk_sdk(tmp_path)
    for i in range(3):
        sdk.create_point("statement", f"pre {i}")
    sdk._get_proj().rebuild_all(str(events))

    assert written, "no pre-wipe sidecar was written — the spy missed"
    assert written[0]["event_meta"] == [{"last_seq": 3}], (
        "#4653: the high-water mark must ride the durable pre-wipe sidecar")


# ── 3. the guard rails: absence, monotonicity, back-compat ───────────────────


def test_rebuild_all_without_events_leaves_counter_absent(tmp_path):
    """A graph that never emitted must not gain a counter from a rebuild.

    FAILING VALUE: `:GraphEventMeta` is absent after the rebuild and the first
    `next_seq` creates it at 1 — inventing `last_seq = 0` in the empty case
    would instead persist a counter node on a never-emitting graph (and make
    "never emitted" indistinguishable from "emitted seq 0").

    REACHABLE: a fresh SDK on a fresh tmp_path with an empty event dir; the
    rebuild replays zero events.
    """
    sdk, events = _mk_sdk(tmp_path)
    counts = sdk._get_proj().rebuild_all(str(events))
    assert counts["events"] == 0
    assert _meta(sdk) is None
    assert event_store.next_seq(sdk._get_proj()) == 1


def test_reestablish_watermark_never_moves_below_the_replayed_log(tmp_path):
    """The `_restore_event_meta` monotonicity guard, pinned directly.

    FAILING VALUE: `reestablish_watermark(proj, 5)` on a graph holding a
    `:GraphEvent` at seq 99 returns 99 and writes `last_seq = 99` — a restore
    that trusted the carried value alone would write 5 and hand out a colliding
    seq. This leg cannot be driven end-to-end today because the replay never
    recreates `:GraphEvent` rows (#4664); it is the shape the replay WILL have,
    so the guard is pinned at the unit seam.

    REACHABLE: the `:GraphEvent` node is written directly, pre-wipe, which is
    exactly what a replay of a restored stream produces.
    """
    from tortoise.event_store import reestablish_watermark

    sdk, _events = _mk_sdk(tmp_path)
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (e:GraphEvent {seq: 99, ts: 'x', type: 'PointAdded', "
        "event_id: 'e1', payload: '{}'})")

    assert reestablish_watermark(proj, 5) == 99
    assert _meta(sdk) == (99, 99), (
        "first_seq is re-derived from the surviving log (min = 99)")


def test_reestablish_watermark_first_seq_is_last_plus_one_on_empty_log(tmp_path):
    """Empty log + carried mark → `first_seq = last_seq + 1`.

    FAILING VALUE: a restore that copied a stale `first_seq` (say 1) would tell
    `events_poll` that events below the mark are still retained, so a subscriber
    parked there reads `[]` forever instead of the truthful 410. The empty-log
    floor is `_refresh_first_seq`'s own contract.

    REACHABLE: the graph is empty at that point by construction — nothing was
    emitted into it — and the `None` leg is driven against a SECOND, fresh graph
    so the counter this test just created cannot answer for it.
    """
    from tortoise.event_store import reestablish_watermark

    sdk, _events = _mk_sdk(tmp_path)
    proj = sdk._get_proj()
    assert reestablish_watermark(proj, 12) == 12
    assert _meta(sdk) == (12, 13)
    # Second call, on a graph with no counter at all: nothing is invented. (The
    # call above left a counter in `proj`, so this needs a fresh graph.)
    sdk2, _events2 = _mk_sdk(tmp_path, name="wm-empty.db")
    assert _meta(sdk2) is None
    assert reestablish_watermark(sdk2._get_proj(), None) is None, (
        "no carried mark, no replayed event and no live counter → no counter "
        "is invented")


def test_v2_leftover_without_event_meta_is_readable(tmp_path):
    """Back-compat: a sidecar written before #4653 carries no `event_meta`.

    FAILING VALUE: the loader accepts version 2 and reads its absent section as
    empty (`data.get(key, [])`) — so an older rescue file is not refused, and it
    cannot fabricate a watermark.

    REACHABLE: the payload is stamped `version: 2` explicitly, the shape every
    #2814-era build wrote.
    """
    from tortoise.projection import _load_prewipe_snapshot

    path = tmp_path / "v2.json"
    payload = _sidecar_payload(version=2, batch_snapshot=[{"id": "b1"}])
    payload.pop("event_meta")
    _plant(path, payload)

    loaded = _load_prewipe_snapshot(str(path))
    assert loaded is not None and loaded["version"] == 2


def test_v2_leftover_rebuild_reports_the_lost_watermark(tmp_path, caplog):
    """A legacy rescue file cannot carry the mark — and the reset is SURFACED.

    FAILING VALUE: recovering from a version-2 sidecar leaves the counter
    ABSENT (`next_seq` → 1, the pre-fix reset) and logs an ERROR naming that
    consequence. The loader must still ACCEPT the file — it is the only record
    of the graph-only nodes it holds — but silence would make an undisclosed
    reset indistinguishable from a healthy rebuild, which is the class of
    defect #4653 is.

    REACHABLE: the payload is a genuine v2 file with no `event_meta` key at
    all, and the live graph is genuinely counter-free (nothing was ever
    emitted) — one of the states the recovery path can be in, and the one in
    which the mark is lost. The same branch is reached by a current-version
    sidecar with an EMPTY section, because the file records only its
    capture-time state and so cannot say whether a counter ever existed.
    """
    from tortoise.projection import _load_prewipe_snapshot

    sdk, events = _mk_sdk(tmp_path)
    payload = _sidecar_payload(version=2, batch_snapshot=[{"id": "b1"}])
    payload.pop("event_meta")
    path = Path(_sidecar_path(events))
    _plant(path, payload)
    loaded = _load_prewipe_snapshot(str(path))
    assert loaded is not None and loaded["version"] == 2

    with caplog.at_level(logging.ERROR):
        sdk._get_proj().rebuild_all(str(events))

    assert _meta(sdk) is None, "a legacy sidecar cannot fabricate a counter"
    assert event_store.next_seq(sdk._get_proj()) == 1
    assert any("NO event-log watermark" in r.getMessage()
               for r in caplog.records), (
        "the legacy-sidecar reset must be an ERROR naming its consequence — "
        "not a silent restart at 1")


# ── 3b. the two post-wipe failure paths must DEGRADE, never raise ────────────


def test_rebuild_all_refuses_before_the_wipe_when_capture_fails(
        tmp_path, monkeypatch):
    """A failed watermark read aborts BEFORE the destructive wipe.

    FAILING VALUE: `rebuild_all` raises and the graph is untouched (same Point
    count, same counter). Without the `capture_failed` leg a failed read falls
    through to the wipe and the allocator resets with no durable record — the
    #2943 rule that a graph which cannot answer a read is not evidence the
    wipe is safe.

    REACHABLE: `_capture_event_meta` is monkeypatched to raise, which is the
    heavy-OOM/timeout read the gate exists for.
    """
    from tortoise import projection as pr

    sdk, events = _mk_sdk(tmp_path)
    sdk.create_point("statement", "keep me")
    proj = sdk._get_proj()
    before = _seen_points(sdk)
    assert _meta(sdk) == (1, 1)

    def boom(_proj):
        raise RuntimeError("simulated unreadable :GraphEventMeta")

    monkeypatch.setattr(pr, "_capture_event_meta", boom)
    with pytest.raises(RuntimeError, match="graph wipe"):
        proj.rebuild_all(str(events))

    assert _seen_points(sdk) == before, "the wipe must not have run"
    assert _meta(sdk) == (1, 1), "the counter must be untouched"


def test_rebuild_all_degrades_when_the_watermark_restore_fails(
        tmp_path, monkeypatch, caplog):
    """A post-wipe restore failure must not turn a completed rebuild into a raise.

    FAILING VALUE: `rebuild_all` still returns its counts and the graph is
    rebuilt (the replay is not rolled back), and the consequence is logged as
    an ERROR. Propagating instead would make the caller see a failed rebuild
    for a graph that is in fact rebuilt — and would leave the operator with no
    "the allocator may have reset" signal (#2943 "no loss without proof").

    REACHABLE: `reestablish_watermark` is monkeypatched to raise, which is the
    driver failure the degraded leg exists for.
    """
    sdk, events = _mk_sdk(tmp_path)
    sdk.create_point("statement", "keep me")
    proj = sdk._get_proj()

    def boom(_proj, _carried):
        raise RuntimeError("simulated watermark write failure")

    monkeypatch.setattr(event_store, "reestablish_watermark", boom)
    with caplog.at_level(logging.ERROR):
        counts = proj.rebuild_all(str(events))

    assert counts["nodes"] == 1, "the rebuild itself completed"
    assert _seen_points(sdk) == 1
    assert any("could not re-establish" in r.getMessage()
               for r in caplog.records), (
        "a lost watermark must be reported, not swallowed")


def test_rebuild_journal_only_degrades_when_the_watermark_restore_fails(
        tmp_path, monkeypatch, caplog):
    """The same rule on the journal-only engine (`rebuild(log)`).

    FAILING VALUE: `rebuild` completes and returns normally despite the
    restore raising, with the consequence logged as an ERROR.

    REACHABLE: one real event in the journal, and the restore monkeypatched to
    raise after the wipe.
    """
    sdk, events = _mk_sdk(tmp_path)
    sdk.create_point("statement", "keep me")
    proj = sdk._get_proj()

    def boom(_proj, _carried):
        raise RuntimeError("simulated watermark write failure")

    monkeypatch.setattr(event_store, "reestablish_watermark", boom)
    with caplog.at_level(logging.ERROR):
        proj.rebuild(EventLog(str(events / "events.jsonl")))

    assert _seen_points(sdk) == 1
    assert any("could not re-establish" in r.getMessage()
               for r in caplog.records)


def test_reestablish_watermark_refuses_an_out_of_domain_carry(tmp_path):
    """The sink-side half of the integer-domain guard.

    FAILING VALUE: a carried value at or above the domain ceiling is REFUSED
    (ValueError) and, when refused, the writer never runs — above `MAX_SEQ` the
    `seq` range index stops comparing exactly, so those events are silently
    undeliverable (`read_after` is `seq > cursor`), and a negative counter can
    never satisfy that comparison at all.

    REACHABLE: the value is passed straight to the function, exactly as a
    hand-planted sidecar that slipped past a future validator would.
    """
    from tortoise.event_store import MAX_SEQ, reestablish_watermark

    sdk, _events = _mk_sdk(tmp_path)
    proj = sdk._get_proj()
    for bad in (MAX_SEQ + 1, 2 ** 53, 2 ** 63 - 1, 10 ** 30, -1):
        with pytest.raises(ValueError, match="integer"):
            reestablish_watermark(proj, bad)
    assert _meta(sdk) is None, "no counter is written from a refused carry"
    assert reestablish_watermark(proj, MAX_SEQ) == MAX_SEQ
    assert _meta(sdk) == (MAX_SEQ, MAX_SEQ + 1)


def test_reestablish_watermark_bounds_the_replayed_seq_too(tmp_path):
    """The bound must apply to the FINAL value, not only to the carry.

    FAILING VALUE: with a replayed `:GraphEvent` at seq INT64_MAX and a
    perfectly legal carried value of 5, `reestablish_watermark` raises
    (`ValueError`) instead of writing INT64_MAX. Checking only the carry lets
    the same failure in through the other input — the monotonicity guard
    promotes the replayed seq above the carry after the check has already
    passed (found by review).

    REACHABLE: the `:GraphEvent` row is written directly, which is what a
    replay of a corrupted/restored stream produces; the `max(e.seq)` read is
    the function's own input.
    """
    from tortoise.event_store import reestablish_watermark

    sdk, _events = _mk_sdk(tmp_path)
    proj = sdk._get_proj()
    proj.g.query(
        f"CREATE (e:GraphEvent {{seq: {2 ** 63 - 1}, ts: 'x', "
        "type: 'PointAdded', event_id: 'e1', payload: '{}'})")

    with pytest.raises(ValueError, match="integer"):
        reestablish_watermark(proj, 5)
    assert _meta(sdk) is None, "no out-of-domain counter is written"


def test_reestablish_watermark_raises_the_first_seq_floor_on_a_live_counter(
        tmp_path):
    """A live counter's LOW ``first_seq`` must be raised, not preserved.

    FAILING VALUE: a counter bumped by `next_seq` to `last_seq = 5` while
    holding `first_seq = 1` (its `ON CREATE` value) is raised to
    `first_seq = 6` when `reestablish_watermark(proj, 3)` runs, so a subscriber
    parked under the floor gets the truthful 410 instead of reading `[]`
    forever. A write that preserves any existing counter's `first_seq`
    leaves 1 in place (found by review: the previous `ON MATCH` clause only
    assigned it when `m.last_seq < $last`).

    REACHABLE: the counter is created and bumped through the real allocator
    (`next_seq`), which is the only way the low `first_seq` arises, and the
    live value is above the proposed carry so the `last_seq` leg is a no-op.
    """
    from tortoise.event_store import reestablish_watermark

    sdk, _events = _mk_sdk(tmp_path)
    proj = sdk._get_proj()
    for _ in range(5):
        event_store.next_seq(proj)
    assert _meta(sdk) == (5, 1), \
        "the allocator really leaves first_seq at its ON CREATE value"

    assert reestablish_watermark(proj, 3) == 5
    assert _meta(sdk) == (5, 6), (
        "the floor must be raised above the truncated stream — preserving 1 "
        "would starve a subscriber parked under it")


def test_reestablish_watermark_refuses_a_counter_corrupted_mid_write(tmp_path):
    """The post-write guard: a value that only goes out of domain mid-write.

    FAILING VALUE: the value the write actually STORED is checked, so a counter
    that a concurrent writer pushed out of domain between the reads and the
    `MERGE` raises (`ValueError`) instead of being reported as a repaired
    watermark. Without that leg the function returns a stored value its own
    contract forbids in silence (found by review; the branch is a TOCTOU backstop
    and cannot be driven deterministically without simulating the race).

    REACHABLE: the write query is answered with the corrupt stored value, which
    is exactly what the concurrent writer would have left behind; every other
    query is delegated to the real graph.
    """
    from tortoise.event_store import MAX_SEQ, reestablish_watermark

    sdk, _events = _mk_sdk(tmp_path)
    proj = sdk._get_proj()
    real_g = proj.g

    class _Racing:
        def query(self, cypher, params=None):
            if "MERGE (m:GraphEventMeta)" in cypher:
                return type("R", (), {"result_set": [[MAX_SEQ + 1]]})()
            return real_g.query(cypher, params=params)

    proj.g = _Racing()
    with pytest.raises(ValueError, match="stored"):
        reestablish_watermark(proj, 5)


def test_capture_watermark_refuses_a_corrupt_live_counter(tmp_path):
    """The capture must not write a sidecar its own loader would refuse.

    FAILING VALUE: `capture_watermark` raises (`ValueError`) on a live
    counter outside the domain, and `rebuild_all` therefore aborts BEFORE the
    wipe (the Point survives). Capturing it instead would break the
    writer-⊆-loader invariant the rescue file depends on: the next rebuild
    would refuse to read the very file this build wrote, losing the watermark
    with no carrier at all (found by review).

    REACHABLE: the counter node is set to INT64_MAX directly, which is exactly
    what an unbounded `hosted_backup` restore of an untrusted dump leaves
    behind.
    """
    from tortoise.event_store import capture_watermark

    sdk, events = _mk_sdk(tmp_path)
    sdk.create_point("statement", "keep me")
    proj = sdk._get_proj()
    proj.g.query(
        f"MATCH (m:GraphEventMeta) SET m.last_seq = {2 ** 63 - 1}")

    with pytest.raises(ValueError, match="domain"):
        capture_watermark(proj)
    with pytest.raises(RuntimeError, match="graph wipe"):
        proj.rebuild_all(str(events))
    assert _seen_points(sdk) == 1, "the wipe must not have run"


def test_reestablish_watermark_write_is_monotone_against_a_live_counter(
        tmp_path):
    """The write itself must never LOWER a live counter.

    FAILING VALUE: with a live counter at 12 and a proposed 5, the restored
    value is 12 (the maximum), not 5. An unconditional
    `SET m.last_seq = $last` writes 5 — a backward move that hands the next
    emitter a `seq` already in use, which is the collision this function
    exists to prevent. It is reachable because `next_seq` is an atomic
    in-graph counter and this leg runs on the LIVE graph after the wipe, so an
    emitter can bump it between the read above and this write.

    REACHABLE: the counter is driven to 12 in the graph (exactly what a
    concurrent emitter's `MERGE ... SET m.last_seq = m.last_seq + 1` leaves
    behind) before the lower proposal is applied.
    """
    from tortoise.event_store import reestablish_watermark

    sdk, _events = _mk_sdk(tmp_path)
    proj = sdk._get_proj()
    assert reestablish_watermark(proj, 10) == 10
    proj.g.query("MATCH (m:GraphEventMeta) SET m.last_seq = 12")

    assert reestablish_watermark(proj, 5) == 12, (
        "the write must take the MAXIMUM of the live counter and the carried "
        "value, and report what is actually stored")
    assert _meta(sdk)[0] == 12


def test_event_meta_section_and_validator_are_wired():
    """The section cannot silently fall out of the sidecar plumbing.

    FAILING VALUE: `event_meta` is in `_SNAPSHOT_SECTIONS`, has an entry check,
    and the read/write version is 3 (so a pre-#4653 binary REFUSES a v3 file
    instead of ignoring the section and wiping) — removing any one of these
    silently de-carries the watermark.

    REACHABLE: pure import-level assertions on the module constants.
    """
    from tortoise.projection import (
        _PREWIPE_SNAPSHOT_READABLE_VERSIONS,
        _PREWIPE_SNAPSHOT_VERSION,
        _SNAPSHOT_ENTRY_CHECK,
        _SNAPSHOT_SECTIONS,
        _validate_event_meta_entry,
    )

    assert "event_meta" in _SNAPSHOT_SECTIONS
    assert _SNAPSHOT_ENTRY_CHECK["event_meta"] is _validate_event_meta_entry
    assert _PREWIPE_SNAPSHOT_VERSION == 3
    assert set(_PREWIPE_SNAPSHOT_READABLE_VERSIONS) >= {1, 2, 3}
    # The union's return literal is hand-written per section, so pin its key
    # set against the tuple: a section added to `_SNAPSHOT_SECTIONS` and
    # forgotten in the literal would be silently dropped from the merged
    # snapshot the write payload and the restore legs both read.
    from tortoise.projection import _union_prewipe_snapshot
    assert set(_union_prewipe_snapshot(
        None, {s: [] for s in _SNAPSHOT_SECTIONS})) == set(_SNAPSHOT_SECTIONS)
    # The type check is load-bearing: a string `last_seq` is silently DROPPED by
    # `_event_meta_last_seq` (which keeps only ints), so a section that carried
    # a real mark would look empty and the counter would never be restored.
    for bad in ({"last_seq": "3"}, {"last_seq": True}, {"last_seq": -1},
                {"last_seq": None}, {}, "3"):
        assert _validate_event_meta_entry(bad) is not None, bad
    assert _validate_event_meta_entry({"last_seq": 0}) is None
    assert _validate_event_meta_entry({"last_seq": 3}) is None
    # A malformed EXTRA property must be refused too — the validator is the
    # FIRST gate at the untrusted boundary (the write also refuses an
    # out-of-domain value, and `capture_watermark` refuses an out-of-domain live
    # counter), and it caps the counter's magnitude there. Above `MAX_SEQ` the
    # `seq` range index stops comparing exactly, so those events are silently
    # undeliverable — and the ceiling is below 2**53 for exactly that reason.
    from tortoise.event_store import MAX_SEQ
    assert MAX_SEQ < 2 ** 53, (
        "the seq domain must stay inside the range the double-precision index "
        "compares exactly, or events above it are silently undeliverable")
    assert _validate_event_meta_entry({"last_seq": 3, "extra": "ok"}) is None
    assert _validate_event_meta_entry(
        {"last_seq": 3, "extra": {"nested": 1}}) is not None
    for bad_seq in (MAX_SEQ + 1, 2 ** 53, 2 ** 63 - 1, 2 ** 63, 10 ** 30):
        assert _validate_event_meta_entry({"last_seq": bad_seq}) is not None, (
            bad_seq)
    assert _validate_event_meta_entry({"last_seq": MAX_SEQ}) is None


# ── 4. the declaration (I2) and the reverse pin (I3) ─────────────────────────

_DOC = (Path(__file__).resolve().parent.parent
        / "docs" / "durability-posture.md")


def _doc_block(name: str) -> str:
    """The text between `<!-- config-registry:<name> -->` and the next `:end`."""
    doc = _DOC.read_text(encoding="utf-8")
    open_tag = f"<!-- config-registry:{name} -->"
    assert open_tag in doc, f"docs/durability-posture.md has no {open_tag}"
    body = doc.split(open_tag, 1)[1]
    assert "<!-- config-registry:end -->" in body, (
        f"the {open_tag} block is unterminated")
    return body.split("<!-- config-registry:end -->", 1)[0]


def test_watermark_declared_in_the_durability_posture():
    """I2: the class has a DECLARED disposition, not an inference.

    FAILING VALUE: `:GraphEventMeta` appears in the `watermark` block with its
    restore rule and #4653, and NOT in `unenrolled` (the block that means "no
    vehicle in this change").

    REACHABLE: the doc is the repo's canonical durability authority and the
    block is a literal in it.
    """
    block = _doc_block("watermark")
    labels = set(re.findall(r"`:(\w+)`", block))
    assert "GraphEventMeta" in labels, (
        "the carried-high-water-mark block must name :GraphEventMeta")
    assert "#4653" in block
    assert "last_seq" in block
    unenrolled = _doc_block("unenrolled")
    assert "GraphEventMeta" not in unenrolled, (
        "#4653 put a vehicle behind the class — it is no longer 'unenrolled'")


def test_export_skipped_classes_must_be_declared():
    """I3: the reverse pin. Export-skip must never be read as a durability
    statement again.

    FAILING VALUE: every label in `_EXPORT_SKIP_LABELS` appears in one of the
    doc's declared blocks. The trap this exists to close is not the set itself
    but the INFERENCE `_EXPORT_SKIP_LABELS` membership ⇒ "internal bookkeeping,
    therefore not load-bearing" — which is exactly how `:GraphEventMeta`'s
    durability was missed. The FORWARD direction (`preserved ⇒ exported`) is
    deliberately NOT asserted: a declared-load-bearing class may be absent from
    the export-skip set, so only "export-skipped ⇒ declared" holds.

    REACHABLE: `hosted_api._EXPORT_SKIP_LABELS` is the live set the defect was
    inferred from.
    """
    from tortoise import hosted_api

    declared: set[str] = set()
    for block in ("preserved", "not-preserved", "unenrolled", "watermark"):
        declared |= set(re.findall(r"`:(\w+)", _doc_block(block)))
    missing = sorted(set(hosted_api._EXPORT_SKIP_LABELS) - declared)
    assert not missing, (
        f"{missing} are export-skipped AND load-bearing but declared nowhere "
        f"in docs/durability-posture.md — an export-skip list is not a "
        f"durability classification (#4653/I3)")
    # The concrete instance the pin exists for.
    assert "GraphEventMeta" in declared
