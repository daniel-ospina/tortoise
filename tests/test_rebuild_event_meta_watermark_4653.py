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

import os
import re
from pathlib import Path

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

    # …and the concrete consumer harm is gone: a subscriber parked at cursor 3
    # receives the next event instead of silently reading [] forever.
    sdk.create_point("statement", "post-rebuild")
    assert _seqs(sdk) == [4]
    delivered = event_store.read_after(proj, 3)
    assert [e["seq"] for e in delivered] == [4], (
        "#4653: a subscriber that polled to cursor 3 before the rebuild must "
        "still be delivered the next event — pre-fix it got [] because the "
        "fresh stream restarted at 1")


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

    REACHABLE: the sidecar is written because the graph has three Points
    (a non-empty `synthetic_events` section triggers `snapshot_pending`).
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
    emitted into it.
    """
    from tortoise.event_store import reestablish_watermark

    sdk, _events = _mk_sdk(tmp_path)
    proj = sdk._get_proj()
    assert reestablish_watermark(proj, 12) == 12
    assert _meta(sdk) == (12, 13)
    assert reestablish_watermark(proj, None) is None, (
        "no carried mark and no replayed event → no counter is invented")


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
    # The type check is load-bearing: a string `last_seq` would win `max()`.
    for bad in ({"last_seq": "3"}, {"last_seq": True}, {"last_seq": -1},
                {"last_seq": None}, {}, "3"):
        assert _validate_event_meta_entry(bad) is not None, bad
    assert _validate_event_meta_entry({"last_seq": 0}) is None
    assert _validate_event_meta_entry({"last_seq": 3}) is None


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
