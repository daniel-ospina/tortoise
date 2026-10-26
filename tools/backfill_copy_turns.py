"""#10017 phase 2 — COPY the legacy graph turns into the record.

The owner approved copy → verify → delete (2026-10-26). Three tools, one
population rule, no two of them allowed to disagree about what a turn is:

* ``tools/backfill_graph_turns.py`` — the **census** (read-only): how many turns
  per session, and what is anomalous. Landed, #10031.
* this module — the **copy** (additive, re-runnable): file each accepted turn
  into the record, positioned by the graph's own journal.
* ``tools/turn_parity_census.py`` — the **parity gate** (read-only): compares
  graph against record, per session, including embeddings, and classifies what
  may NOT be deleted. It is the authority for the DELETE decision; this phase
  only makes a comparison possible.

⛔ Why this is not `_emit_event`: that also writes the graph ``:GraphEvent``
store (``tortoise/sdk.py``, #432), so a copy routed through it would mint new
nodes into the very graph being emptied. The events here are plain dicts handed
to the record seam — the record is a CONSUMER of those dicts, so it is never
re-derived or INSERTed by hand.

⛔ Why the copy must not report success on a write it did not commit: the record
seam *never* commits. ``record_writer``'s own contract (module docstring, and
the live door at ``write_live_record``) is that the caller commits the
connection the seam returns. A copy that ignores the outcome and the connection
writes nothing, prints the number of turns it "copied", and exits 0 — the exact
silent-success shape that would let a delete step run against an empty record.

Population: identical to the census — an id of ``<session_id>_t<ascii digits>``
wired by ``CONTAINS`` to a session. Anything the rule cannot place is REPORTED
under its own class and NOT copied: an unkeyable node filed into the record is a
row nothing can compare against. A point wired to SEVERAL sessions is copied
under the session its id derives (which is what the census counts), and
reported, because ``tools/turn_parity_census.py`` fails any session that has a
graph turn and no record row — a node this phase refused to copy would fail that
gate permanently, with no way to satisfy it.

Honest bounds, stated rather than implied:

* Memory: turn payloads (embeddings included) are materialised for the whole
graph before the emit loop, so peak cost scales with the tenant — hundreds of
MB at the largest production graph (6,432 turns), not a constant.
* Not atomic across sessions: one record connection per session means a
  per-session transaction, which is the right re-runnable boundary but is NOT a
  single atomic copy. An interrupted run leaves earlier sessions copied.
* The graph's own journal is the graph's REPLAY source. Appending ``PointAdded``
  to it means a later ``rebuild()`` re-creates the turns this migration deletes
  from the graph. That is a design consequence recorded on #10017, not a
  side effect to be discovered later.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backfill_graph_turns as census  # same directory: ONE rule for "a turn"

#: Same predicate as the census's turn read, but carrying the payload — the copy
#: needs the properties, and it is paged for the same reason the census is.
_COPY_TURNS_PAGE_QUERY = (
    "MATCH (s:Session)-[:CONTAINS]->(p:Point) WHERE p.is_episodic = true "
    "AND id(p) >= $cursor "
    "WITH p, collect(s.id) AS sids "
    "RETURN id(p), p.id, properties(p), sids "
    "ORDER BY id(p) LIMIT $batch"
)
#: `properties(s)` and not three named fields: the session node carries the
#: capture provenance (`harness`, `capture_lane`, `model`, `machine_id`,
#: `source`, `actor_user_id`, `client_captured_at[_source]`,
#: `capture_redactions`), and once the graph is pruned this is the only place
#: it can still come from. A null `capture_redactions` is a MEANINGFUL value
#: ("no secret was ever redacted", #4911) — dropping the key would make the
#: record unable to falsify that reading.
#: PAGED for the same reason every other read here is: `RESULTSET_SIZE`
#: truncates a non-aggregate read silently, so an unpaged session read would
#: drop provenance on a large tenant and stop reporting `created_at_absent`
#: (the `sid in sessions` test would simply be false).
_COPY_SESSIONS_PAGE_QUERY = (
    "MATCH (s:Session) WHERE id(s) >= $cursor "
    "RETURN id(s), s.id, properties(s) "
    "ORDER BY id(s) LIMIT $batch"
)

#: Session properties the record stores as COLUMNS. Anything else rides in
#: `props` (the record's own split does that in `_split_properties`), so this
#: list is only the set whose meaning is the column's, not a whitelist.
_SESSION_FIELDS = (
    "created_at", "harness", "capture_lane", "model", "machine_id", "source",
    "actor_user_id", "client_captured_at", "client_captured_at_source",
    "capture_redactions", "turn_count", "turn_max_index",
)
#: Columns the copy OWNS — a graph property may never override these.
_SESSION_DERIVED = ("turn_count", "turn_max_index")


class CopyRefused(RuntimeError):
    """A copy that must not proceed, or whose write did not land."""


def classify_copy(rows) -> tuple[dict, dict]:
    """``(by_session, anomalies)`` from ``(internal_id, pid, props, sids)`` rows.

    ``by_session`` is ``{session_id: {index: props}}`` — index keyed, because the
    record's ``node_key`` IS ``<session>_t<index>`` and a duplicate index would
    collide on the record's primary key rather than fail loudly.
    """
    by_session: dict[str, dict[int, dict]] = {}
    anomalies: dict[str, list] = {
        "off_shape": [], "orphan": [], "cross_wired": [],
        "session_mismatch": [], "duplicate_index": [],
        "not_point_kind_event": [],
    }
    # `orphan` cannot occur through this phase's read (it starts at
    # `(s:Session)-[:CONTAINS]->`), but the class is kept and reported with its
    # zero so the artifact can tell "0 orphans" from "not measured". The
    # census's own `orphan_turn_ids` is where an unwired turn is counted.
    for _internal_id, pid, props, sids in rows:
        props = dict(props or {})
        pid = "" if pid is None else str(pid)
        sids = [str(s) for s in (sids or ())]
        parsed = census._TURN_ID_RE.match(pid)
        index = census._int_or_none(parsed.group("index")) if parsed else None
        if parsed is None or index is None:
            anomalies["off_shape"].append(pid)
            continue
        derived = parsed.group("session")
        if not sids:
            anomalies["orphan"].append(pid)
            continue
        if len(sids) > 1:
            # COPIED under the derived session and reported: the census counts
            # this point as that session's turn, and the parity gate fails any
            # session holding a graph turn with no record row — refusing to
            # copy it would fail that gate forever, with nothing able to fix it.
            anomalies["cross_wired"].append({"id": pid, "sessions": sorted(sids)})
        if sids[0] != derived:
            anomalies["session_mismatch"].append(
                {"id": pid, "wired_session": sids[0], "derived_session": derived})
            continue
        if props.get("pointKind") != "event":
            # COPIED and reported: the id space places it and the record's turn
            # read does not require the kind, but the parity gate excludes these
            # from its purge set — so the divergence is measured here instead of
            # this phase quietly claiming a different population.
            anomalies["not_point_kind_event"].append(pid)
        bucket = by_session.setdefault(derived, {})
        if index in bucket:
            anomalies["duplicate_index"].append(
                {"session": derived, "index": index, "id": pid})
            continue
        bucket[index] = props
    return by_session, anomalies


def build_session_event(session_id: str, props: dict, *, turn_count: int,
                        max_index: int, event_id: str) -> dict:
    """``SessionRecorded`` — the record row's source, and the sweep's mark."""
    event = {
        "event_id": event_id, "type": "SessionRecorded",
        "initiated_by": "backfill", "projection_version": 2,
        "id": session_id, "is_episodic": True,
    }
    for field in _SESSION_FIELDS:
        if field in props and field not in _SESSION_DERIVED:
            event[field] = props[field]
    event.setdefault("created_at", None)  # PRESENT and null, never "now"
    # The ATTACHED count, never the node's declared `turn_count`: the census
    # measured 21 sessions declaring fewer turns than they carry (declared=6,
    # attached=12), so re-asserting the declared number would file a value the
    # copy itself disproves. `turn_max_index` is the id-space high-water mark
    # the sweep needs (#10000) — not the window length.
    event["turn_count"] = turn_count
    event["turn_max_index"] = max_index
    return event


def build_turn_event(session_id: str, props: dict, *, event_id: str) -> dict:
    """``PointAdded`` for one turn, carrying its ``CONTAINS`` session linkage."""
    snap = {k: v for k, v in props.items() if k != "content_hash"}
    # Presence is ownership (#5004): an absent key lets replay invent a vector.
    snap.setdefault("embedding", None)
    return {
        "event_id": event_id, "type": "PointAdded",
        "initiated_by": "backfill", "projection_version": 2,
        "id": props["id"], "point": snap, "contains_session": session_id,
    }


def copy_phase(graph, emit, *, page_size: int | None = None, event_id) -> dict:
    """File every accepted turn into the record. Additive, re-runnable.

    ``emit(session_id, events)`` is the only side effect; it returns the journal
    position of ``events[0]`` or ``None`` when no position could be proven, and
    it RAISES when the record write did not land. Re-running is safe: the
    record's ``point`` key is ``(org_id, graph_id, node_key)``.
    """
    if page_size is None:
        page_size = census._PAGE_SIZE
    session_rows = list(census._paged_rows(graph, _COPY_SESSIONS_PAGE_QUERY,
                                           page_size))
    census._cross_check(":Session", len(session_rows),
                        census._count(graph, census._SESSIONS_COUNT_QUERY)[0])
    sessions = {str(sid): dict(props or {})
                for _internal, sid, props in session_rows}
    rows = list(census._paged_rows(graph, _COPY_TURNS_PAGE_QUERY, page_size))
    census._cross_check("copy turn", len(rows),
                        census._count(graph, census._TURNS_COUNT_QUERY)[1])

    by_session, anomalies = classify_copy(rows)
    stats = {
        "sessions": 0, "turns": 0, "positioned": 0, "empty_sessions": 0,
        "created_at_absent": [],
        # EVERY class, zeros included: a subset cannot distinguish "0" from
        # "not measured", which is how a silent gap reads as a clean result.
        "anomalies": {k: len(v) for k, v in anomalies.items()},
    }
    # EVERY session, not just the ones with turns: a session with none still has
    # provenance, and a delete step that touches Session nodes would lose it
    # under a copy that only ever saw turn-bearing sessions.
    for sid in sorted(set(sessions) | set(by_session)):
        bucket = by_session.get(sid, {})
        indexes = sorted(bucket)
        props = sessions.get(sid, {})
        if props.get("created_at") is None and sid in sessions:
            stats["created_at_absent"].append(sid)
        events = [build_session_event(
            sid, props, turn_count=len(indexes),
            max_index=indexes[-1] if indexes else -1, event_id=event_id())]
        events += [build_turn_event(sid, bucket[i], event_id=event_id())
                   for i in indexes]
        if emit(sid, events) is not None:
            stats["positioned"] += 1
        stats["sessions"] += 1
        stats["turns"] += len(indexes)
        if not indexes:
            stats["empty_sessions"] += 1
    return stats


def _journal_emit(journal_path: str, dsn: str, org_id: str, graph_id: str):
    """The production ``emit``: journal position, then the record, committed.

    The position comes from the journal append and the record's rows are
    numbered by it — a record row with no position is the drift #9190 closed.
    The record seam does NOT commit, so this does, and it REFUSES when nothing
    landed rather than returning a position for a write that was rolled back.
    """
    from tortoise.log import EventLog
    from tortoise.record_writer import RecordWriteOutcome, RecordWriter, write_records

    # The journal must already EXIST: `EventLog` on a mistyped or fresh path
    # ordinals from 1, and `journal_event` absorbs the collision with
    # `ON CONFLICT DO NOTHING` — so every position row would be silently
    # discarded while the record came to believe the real journal's seqs were
    # consumed. Fail closed instead of guessing which file is the graph's.
    journal = Path(journal_path)
    if not journal.is_file() or journal.stat().st_size == 0:
        raise CopyRefused(
            f"{journal_path}: not an existing, non-empty journal — positions are "
            "per (org_id, graph_id), and a fresh file would collide with live "
            "history and be swallowed by ON CONFLICT")

    log = EventLog(journal_path)
    # The DSN is passed EXPLICITLY. It used to be accepted and dropped, so a
    # non-default `--dsn-env` wrote into whatever `TORTOISE_RECORD_DSN` named —
    # a different database, exit 0.
    writer = RecordWriter(dsn=dsn)

    def emit(session_id: str, events: list[dict]):
        positions = [log.append(event) for event in events]
        first = positions[0] if positions else None
        if None in positions and first is not None:
            # A None mid-segment is an UNPROVEN position (#9190), not an
            # interleaved writer — the remedy is different, so the message is.
            raise CopyRefused(
                f"{session_id}: the journal could not prove a position for every "
                "event; filing the rest would number them against someone "
                "else's records")
        if first is not None and positions != list(range(first, first + len(positions))):
            # `write_records` numbers ONE CONTIGUOUS segment, and the appends
            # take the journal lock one at a time — a concurrent live writer
            # would interleave, and `journal_event`'s ON CONFLICT would keep the
            # peer's event under our point rows.
            raise CopyRefused(
                f"{session_id}: journal positions {positions[:3]}… are not "
                "contiguous — a live writer interleaved with the copy; re-run "
                "with the writer stopped")
        write = write_records(
            events, org_id=org_id, graph_id=graph_id,
            # No proven position ⇒ the row must NOT be filed with an invented
            # one (#9190): `position=False` leaves it unwritten instead.
            position=first is not None, first_seq=first if first is not None else 1,
            conn=None, writer=writer)
        write = _commit_or_refuse(write, RecordWriteOutcome, session_id)
        return first

    return emit


def _commit_or_refuse(write, applied, session_id: str):
    """Commit the seam's connection, and refuse a write that did not land."""
    connection = getattr(write, "connection", None)
    try:
        if getattr(write, "outcome", None) is not applied.APPLIED:
            raise CopyRefused(
                f"{session_id}: the record is not armed / refused the write "
                f"(outcome={getattr(write, 'outcome', None)}) — nothing was copied")
        if not getattr(write, "statements", 0):
            raise CopyRefused(
                f"{session_id}: the record accepted the call but wrote 0 "
                "statements — nothing was copied")
        if connection is not None:
            connection.commit()
    finally:
        if connection is not None:
            # close must never mask a write failure that is already propagating
            with contextlib.suppress(Exception):
                connection.close()
    return write


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="#10017 — copy the legacy graph turns into the record.")
    ap.add_argument("--graph", required=True)
    ap.add_argument("--uri-env", default="FALKORDB_CLOUD_URI")
    ap.add_argument("--org-id", required=True,
                    help="the RECORD tenant's org — never inferred from a name")
    ap.add_argument("--graph-id", required=True,
                    help="the RECORD tenant's graph id — never inferred")
    ap.add_argument("--journal", required=True,
                    help="the GRAPH'S OWN journal (its seq space is per "
                         "(org_id, graph_id); a fresh file would collide with "
                         "live history and leave the copy out of rebuild)")
    ap.add_argument("--dsn-env", default="TORTOISE_RECORD_DSN")
    ap.add_argument("--dry-run", action="store_true",
                    help="measure and report, write nothing")
    ap.add_argument("--out", help="write the summary JSON here")
    args = ap.parse_args(argv)

    uri = os.environ.get(args.uri_env)
    if not uri:
        raise SystemExit(f"${args.uri_env} is not set (the graph to read)")
    dsn = os.environ.get(args.dsn_env)
    if not dsn and not args.dry_run:
        raise SystemExit(f"${args.dsn_env} is not set — refuse to copy into "
                         "nothing (use --dry-run to measure only)")

    # `tortoise.ids.ulid` is the repo's ONE mint — `ulid` is not a dependency,
    # so `import ulid` made every non-dry-run invocation die before connecting.
    from tortoise.ids import ulid

    db, shown = census._connect(uri)
    census._assert_graph_exists(db, args.graph)
    graph = db.select_graph(args.graph)

    emit = (lambda sid, events: 0) if args.dry_run else _journal_emit(
        args.journal, dsn, args.org_id, args.graph_id)
    stats = copy_phase(graph, emit, event_id=ulid)
    stats["graph"] = shown
    stats["dry_run"] = args.dry_run
    if args.out:
        Path(args.out).write_text(json.dumps(stats, indent=2, sort_keys=True))
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
