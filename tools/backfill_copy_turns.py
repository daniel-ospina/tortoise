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
wired by ``CONTAINS`` to that same session. Anything the rule cannot place is
REPORTED under its own class and NOT copied: an unkeyable node filed into the
record is a row nothing can compare against, and an ambiguously-owned one would
be readable under a session it does not belong to.
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
_COPY_SESSIONS_QUERY = "MATCH (s:Session) RETURN s.id, properties(s)"

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
            anomalies["cross_wired"].append({"id": pid, "sessions": sorted(sids)})
            continue
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
    sessions = {str(sid): dict(props or {}) for sid, props in
                graph.query(_COPY_SESSIONS_QUERY).result_set}
    rows = list(census._paged_rows(graph, _COPY_TURNS_PAGE_QUERY, page_size))
    census._cross_check("copy turn", len(rows),
                        census._count(graph, census._TURNS_COUNT_QUERY)[1])

    by_session, anomalies = classify_copy(rows)
    stats = {
        "sessions": 0, "turns": 0, "positioned": 0, "empty_sessions": 0,
        "created_at_absent": [],
        "anomalies": {k: len(v) for k, v in anomalies.items() if v},
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
    from tortoise.record_writer import RecordWriteOutcome, write_records

    log = EventLog(journal_path)

    def emit(session_id: str, events: list[dict]):
        positions = [log.append(event) for event in events]
        first = positions[0] if positions else None
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
            conn=None, writer=None)
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

    import ulid as _ulid

    db, shown = census._connect(uri)
    census._assert_graph_exists(db, args.graph)
    graph = db.select_graph(args.graph)

    emit = (lambda sid, events: 0) if args.dry_run else _journal_emit(
        args.journal, dsn, args.org_id, args.graph_id)
    stats = copy_phase(graph, emit, event_id=lambda: str(_ulid.ULID()))
    stats["graph"] = shown
    stats["dry_run"] = args.dry_run
    if args.out:
        Path(args.out).write_text(json.dumps(stats, indent=2, sort_keys=True))
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
