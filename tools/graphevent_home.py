#!/usr/bin/env python
"""Is the record already a home for the `:GraphEvent` change feed? — `#13094`.

THE QUESTION, and why it is a tool rather than a paragraph. `#13094`'s own body
says the move *may be a feed re-pointing* because "a record home very likely
exists already", and then states the question that must be answered FIRST:

    **Does `public.journal_event` carry every field `poll_events` serves, with a
    stable cursor, field for field?**

A cursor that moves by one is a silent subscriber gap, so the issue requires the
match to be "verified on real data before a re-point". Prose cannot do that. This
verb can, and it is READ-ONLY on both stores.

WHAT IT COMPARES. Both stores hold one row per change-feed event, but they are
NOT the same coordinate: `:GraphEvent.seq` is allocated by the change feed's own
counter (`:GraphEventMeta.last_seq`, `event_store.next_seq`) while
`journal_event.seq` is the REPLAY journal's ordinal (`#12894`'s allocator —
`supabase/migrations/20261116000001_record_cursor_allocator.sql` states the two
are different). So the comparison is by **(event type, payload digest)**, as
multisets per type, and the verdict is:

* ``covered`` — every graph row's (type, payload) multiset is present in the
  record, and every graph event type exists in the record's vocabulary;
* ``not_covered`` — with the deficit named per type: how many rows are absent and
  whether the TYPE is missing entirely (a type absent from the record is the
  worse failure: a subscriber sees a stream that is complete-looking and short).

⛔ THE VERDICT IS NOT A LICENCE TO RE-POINT. Even at ``covered`` this verb cannot
see the two refusals `#13083` §6.3 established from the code, which are properties
of the PRODUCER and not of any tenant's data: `_emit_event` skips the journal
entirely when a payload-only emit carries no `point`/`id`/`turns` (`sdk.py:16195`),
and the hard-delete lane deliberately writes `PointRetracted` to the store and
`EntityMutated` to the journal (`sdk.py:3786-3801`, "two stores, one meaning
each"). ``covered`` on a tenant therefore means "this tenant's history happens to
be reproducible", never "the producers agree".

EXIT CODES (the repo's convention — a read that could not run is NOT a verdict):

==  =========================================================================
0   the verdict was produced (``covered`` OR ``not_covered`` — both are answers)
1   not covered — the same answer, as a failure, for a caller that wants a gate
2   could not run: no graph URI, no record DSN, ``psycopg2`` absent, a store
    that refused the read. Never read as ``covered``.
==

Usage:

    # what the graph holds, shape by shape (read-only)
    uv run python tools/graphevent_home.py residency --graph-uri "$TORTOISE_DB_URI"

    # the decisive comparison, against a real tenant's record
    uv run python tools/graphevent_home.py superset --graph-uri "$TORTOISE_DB_URI" \
        --record-dsn "$TORTOISE_RECORD_DSN" --org-id org_x --graph-id g_xxx

    # the reader census this PR's table was cut from
    uv run python tools/graphevent_home.py readers
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from typing import Any

#: ── THE READER CENSUS ─────────────────────────────────────────────────────
#:
#: Every statement in `tortoise/` that names the `:GraphEvent` label, with what
#: it needs and how it is classified. This is the table `#13094`'s PR carries,
#: made checkable: `tests/test_13094_graphevent_home.py` asserts every `snippet`
#: below still appears in its `file`, so a reader added or reworded goes RED here
#: instead of drifting out of an evidence document.
#:
#: CLASSIFICATION — the three classes, and what each one can justify:
#:
#:   ``serving``      — a query whose RESULT a user-facing answer is made of. A
#:                      serving reader needing a graph capability (traversal,
#:                      adjacency, fulltext, EP walk, vector) is what keeps a
#:                      shape resident. **There are NONE for `:GraphEvent`.**
#:   ``maintenance``  — the store's own bookkeeping: allocate, append, purge,
#:                      watermark, back up. It needs an ORDERED SCAN or an
#:                      AGGREGATE, which is SQL-shaped; moving the row does not
#:                      delete the operation, it re-points it.
#:   ``test``         — a fixture or assertion. It constrains the move (it pins
#:                      the contract) but it is not a reason to keep a byte of RAM.
#:
#: ⚠️ `snippets` is a TUPLE of required substrings, matched with whitespace
#: collapsed. It is a tuple because several of these statements are written as
#: CONCATENATED Python string literals (`"MATCH …" + where + " "` /
#: `"RETURN …"`), so no single substring spans the whole statement in the source.
#: Collapsed matching means a re-indent does not red the census while a reword
#: does; the `line` beside each entry is an ADDRESS AT THIS HEAD and is
#: re-derived, never trusted.
READERS: tuple[dict[str, Any], ...] = (
    {
        "file": "tortoise/event_store.py",
        "line": "992",
        "reader": "read_after",
        "snippets": ("MATCH (e:GraphEvent) WHERE e.seq > $after",
                     "RETURN properties(e) ORDER BY e.seq ASC LIMIT $limit",),
        "needs": "ordered range scan on seq, optional type filter, LIMIT + dedupe",
        "class": "serving",
        "why": "this is the ONLY read that produces feed rows: `poll_events`, "
               "webhook_delivery.dispatch_pending, why_snapshot and change_feed all "
               "page through it. SQL-shaped (an indexed range scan), not a graph "
               "capability.",
    },
    {
        "file": "tortoise/event_store.py",
        "line": "801",
        "reader": "append_event (dedupe pre-check)",
        "snippets": ("MATCH (e:GraphEvent {event_id:$id}) RETURN count(e)",),
        "needs": "key existence on event_id",
        "class": "maintenance",
        "why": "a dedupe probe before the CREATE; a unique index answers it.",
    },
    {
        "file": "tortoise/event_store.py",
        "line": "816",
        "reader": "append_event (the write)",
        "snippets": ("CREATE (e:GraphEvent {seq:$seq, ts:$ts, type:$type, ",
                     "event_id:$event_id, payload:$payload})",),
        "needs": "an insert",
        "class": "maintenance",
        "why": "the store's only writer.",
    },
    {
        "file": "tortoise/event_store.py",
        "line": "879",
        "reader": "_event_row_exists",
        "snippets": ("MATCH (e:GraphEvent {event_id:$id}) RETURN count(e)",),
        "needs": "key existence",
        "class": "maintenance",
        "why": "#9208's loss discriminator: asks whether a failed append landed.",
    },
    {
        "file": "tortoise/event_store.py",
        "line": "1029",
        "reader": "purge_expired",
        "snippets": ("MATCH (n:GraphEvent) WHERE n.ts < $cutoff",
                     "WITH n LIMIT 10000 DETACH DELETE n RETURN count(*)",),
        "needs": "predicate delete",
        "class": "maintenance",
        "why": "retention by policy (#13084 owns the policy); SQL-shaped.",
    },
    {
        "file": "tortoise/event_store.py",
        "line": "1043",
        "reader": "purge_overflow (count)",
        "snippets": ("MATCH (n:GraphEvent) RETURN count(n)",),
        "needs": "a whole-label count",
        "class": "maintenance",
        "why": "the overflow path's total, counted before it decides the excess.",
    },
    {
        "file": "tortoise/event_store.py",
        "line": "1053",
        "reader": "purge_overflow (delete)",
        "snippets": ("MATCH (n:GraphEvent) WITH n ORDER BY n.seq ASC LIMIT $overflow",
                     "DETACH DELETE n RETURN count(*)",),
        "needs": "ordered, limited delete",
        "class": "maintenance",
        "why": "size-cap purge, same shape as `purge_expired`.",
    },
    {
        "file": "tortoise/event_store.py",
        "line": "645",
        "reader": "capture_watermark",
        "snippets": ("MATCH (e:GraphEvent) RETURN max(e.seq), min(e.seq)",),
        "needs": "aggregate (max, min)",
        "class": "maintenance",
        "why": "the pre-wipe capture; aggregates only.",
    },
    {
        "file": "tortoise/event_store.py",
        "line": "1104",
        "reader": "_refresh_first_seq",
        "snippets": ("OPTIONAL MATCH (e:GraphEvent)",),
        "needs": "aggregate (min) computed inside an RMW on :GraphEventMeta",
        "class": "maintenance",
        "why": "the purge floor; note the RMW itself is on the SIBLING label and is "
               "not this shape's reader (§2.7 of the audit draws that line).",
    },
    {
        "file": "tortoise/webhook_delivery.py",
        "line": "1049",
        "reader": "_head_seq fallback",
        "snippets": ("MATCH (e:GraphEvent) RETURN max(e.seq)",),
        "needs": "aggregate (max)",
        "class": "maintenance",
        "why": "the fallback when `:GraphEventMeta` is absent; a fallback, not a "
               "capability — the same number is otherwise read off the meta node.",
    },
    {
        "file": "tortoise/hosted_backup.py",
        "line": "892",
        "reader": "backup footprint",
        "snippets": ("MATCH (e:GraphEvent) RETURN max(e.seq), min(e.seq)",),
        "needs": "aggregate (max, min)",
        "class": "maintenance",
        "why": "reported beside the backup's other counts.",
    },
    {
        "file": "tortoise/hosted_backup.py",
        "line": "1333",
        "reader": "whole-graph export (nodes)",
        "snippets": ("MATCH (n) RETURN labels(n), properties(n)",),
        "needs": "a full label-agnostic dump",
        "class": "maintenance",
        "why": "the export carries `:GraphEvent` rows because it carries everything; "
               "it names no label and needs no capability.",
    },
    {
        "file": "tortoise/hosted_backup.py",
        "line": "1534",
        "reader": "whole-graph export (restore-side scan)",
        "snippets": ("MATCH (n) RETURN labels(n), properties(n)",),
        "needs": "a full label-agnostic dump",
        "class": "maintenance",
        "why": "the second whole-graph scan (#13083 §2.6 measured both): the same "
               "statement shape as the line above, in the restore path.",
    },
)

#: The event types the store's DEFAULT contract writes (`sdk._GRAPH_EVENT_TYPES`),
#: restated here ONLY to name them in the report; `tests/test_13094_graphevent_home.py`
#: asserts this set EQUALS the one `sdk` declares, so it cannot drift.
GRAPH_EVENT_TYPES = frozenset({
    "PointAdded", "OperatorAdded", "PointRetracted", "PointSuperseded",
    "OperatorAnnotated", "PointPromoted", "OperatorPromoted", "DedupeRecorded",
    "DedupeRejected", "ObjectSuperseded", "PointInvalidated",
})

RECORD_TABLE = "journal_event"


# ── the graph side ─────────────────────────────────────────────────────────

def _graph_connection(uri: str):
    from falkordb import FalkorDB

    return FalkorDB.from_url(uri.replace("docker://", "redis://"))


def _graph_rows(db, graph: str | None, batch: int = 2000) -> list[dict[str, Any]]:
    """Every `:GraphEvent` row, as `{seq, type, event_id, payload}`.

    Paged by `seq` rather than fetched in one statement: a tenant with 72,488
    events (the largest legacy graph, `#13083` §1) must not be materialised in
    one reply. `ORDER BY seq` is the same order `read_after` serves, so the
    digest multiset below is order-independent by construction anyway.
    """
    g = db.select_graph(graph) if graph else db.select_graph("tortoise")
    rows: list[dict[str, Any]] = []
    after = 0
    while True:
        page = g.query(
            "MATCH (e:GraphEvent) WHERE e.seq > $after "
            "RETURN e.seq, e.type, e.event_id, e.payload ORDER BY e.seq ASC "
            "LIMIT $limit",
            params={"after": after, "limit": batch},
        ).result_set
        if not page:
            return rows
        for seq, type_, event_id, payload in page:
            rows.append({"seq": int(seq), "type": str(type_), "event_id": event_id,
                         "payload": payload})
        after = int(page[-1][0])


def residency(graph_uri: str, graph: str | None) -> dict[str, Any]:
    """What the graph holds, and what share of it this one shape is."""
    db = _graph_connection(graph_uri)
    g = db.select_graph(graph) if graph else db.select_graph("tortoise")
    totals = g.query(
        "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS n "
        "ORDER BY n DESC").result_set
    labels = {str(label): int(n) for label, n in totals}
    total = sum(labels.values())
    events = _graph_rows(db, graph)
    payload_bytes = sum(len((row["payload"] or "").encode()) for row in events)
    usage: dict[str, Any] = {}
    try:
        flat = db.connection.execute_command(
            "GRAPH.MEMORY", "USAGE", graph or "tortoise")
        usage = dict(zip(flat[0::2], flat[1::2], strict=True))
    except Exception as exc:  # noqa: BLE001 — a server without the verb is not a fault
        usage = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "labels": labels,
        "nodes_total": total,
        "graph_event_nodes": len(events),
        "graph_event_share_of_nodes": (
            round(len(events) / total, 4) if total else None),
        "graph_event_payload_bytes": payload_bytes,
        "graph_event_payload_bytes_per_node": (
            round(payload_bytes / len(events), 1) if events else None),
        "types": dict(Counter(row["type"] for row in events)),
        "graph_memory_usage": usage,
        "per_node_bytes_source": (
            "docs/evidence/13094-graphevent/README.md — the measured marginal "
            "slope (557.4 B/node at an 88 B payload, 597.2 B/node at 190 B), "
            "NOT re-measured here: `INFO memory` is process-wide, so a tenant "
            "graph cannot attribute it to one label."),
    }


# ── the record side ────────────────────────────────────────────────────────

def _record_connection(record_dsn: str, org_id: str) -> Any:
    try:
        import psycopg2  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # pragma: no cover — environment, not code
        raise SystemExit(
            "psycopg2 is not installed, so the record side cannot be read: "
            "`uv sync --extra postgres` (or `pip install psycopg2-binary`). "
            "NO VERDICT was produced.") from exc
    conn = psycopg2.connect(record_dsn)
    from tortoise.record_projection import scope_connection

    scope_connection(conn, org_id)
    return conn


def record_rows(conn: Any, org_id: str, graph_id: str,
                batch: int = 2000) -> list[dict[str, Any]]:
    """Every `journal_event` row for this scope."""
    rows: list[dict[str, Any]] = []
    last = 0
    cur = conn.cursor()
    while True:
        cur.execute(
            f"SELECT seq, event_type, payload FROM public.{RECORD_TABLE} "
            "WHERE org_id = %s AND graph_id = %s AND seq > %s "
            "ORDER BY seq ASC LIMIT %s",
            (org_id, graph_id, last, batch))
        page = cur.fetchall()
        if not page:
            return rows
        for seq, event_type, payload in page:
            rows.append({"seq": int(seq), "type": str(event_type),
                         "payload": payload})
        last = int(page[-1][0])


def _digest(payload: Any) -> str:
    """A stable digest of a payload, whatever type each store hands back.

    The graph side returns the payload as the STRING `append_event` stored
    (`json.dumps(..., ensure_ascii=False)`); the record side returns a parsed
    `jsonb` value. Digesting the NORMALISED JSON (sorted keys, no whitespace) is
    what makes those two comparable — digesting the raw string would report every
    row as a miss for a formatting difference, which is the false-negative this
    verb must never emit.
    """
    if isinstance(payload, (str, bytes, bytearray)):
        try:
            value = json.loads(payload)
        except (TypeError, ValueError):
            value = payload.decode() if isinstance(payload, (bytes, bytearray)) else payload
    else:
        value = payload  # already parsed (jsonb)
    normalised = json.dumps(value, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, default=str)
    return hashlib.sha256(normalised.encode()).hexdigest()


def superset(graph_rows: list[dict[str, Any]],
             journal_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The verdict: is the RECORD's row multiset a superset of the graph's?

    Per event type, because the two stores' vocabularies are allowed to differ
    (`#13083` §6.3: `PointRetracted` on the graph side is `EntityMutated` in the
    journal) and a type-level absence is the failure a subscriber cannot see.
    """
    graph_by_type: dict[str, Counter] = {}
    for row in graph_rows:
        graph_by_type.setdefault(row["type"], Counter())[_digest(row["payload"])] += 1
    journal_by_type: dict[str, Counter] = {}
    for row in journal_rows:
        journal_by_type.setdefault(row["type"], Counter())[_digest(row["payload"])] += 1

    per_type: dict[str, Any] = {}
    missing_rows = 0
    missing_types: list[str] = []
    for type_, counter in sorted(graph_by_type.items()):
        available = journal_by_type.get(type_)
        if available is None:
            missing_types.append(type_)
            missing_rows += sum(counter.values())
            per_type[type_] = {"graph_rows": sum(counter.values()), "matched": 0,
                               "unmatched": sum(counter.values()),
                               "record_has_type": False}
            continue
        matched = sum(min(count, available[digest])
                      for digest, count in counter.items())
        unmatched = sum(counter.values()) - matched
        missing_rows += unmatched
        per_type[type_] = {"graph_rows": sum(counter.values()), "matched": matched,
                           "unmatched": unmatched, "record_has_type": True}
    record_only_types = sorted(set(journal_by_type) - set(graph_by_type))
    return {
        "verdict": "covered" if missing_rows == 0 else "not_covered",
        "graph_events": len(graph_rows),
        "record_events": len(journal_rows),
        "missing_rows": missing_rows,
        "missing_types": missing_types,
        "record_only_types": record_only_types,
        "per_type": per_type,
        "caveat": (
            "EVEN AT `covered` THE PRODUCERS DISAGREE (#13083 §6.3): a "
            "payload-only emit writes no journal record at all (`sdk.py` "
            "`_emit_event`), and the hard-delete lane writes `PointRetracted` to "
            "the store and `EntityMutated` to the journal. A tenant-level "
            "`covered` says this tenant's history is reproducible; it is not "
            "evidence that a re-point would not drop future events."),
    }


def readers_report() -> dict[str, Any]:
    """The reader census, with the class counts — the PR's table, as data."""
    counts = Counter(reader["class"] for reader in READERS)
    return {
        "readers": list(READERS),
        "counts": dict(counts),
        "graph_capability_readers": [r for r in READERS
                                     if r["class"] == "serving"
                                     and "graph" in r["needs"]],
        "verdict": ("no reader needs a graph capability — every reader is a "
                    "key lookup, an ordered range scan, an aggregate or a "
                    "predicate delete"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="verb", required=True)

    sub.add_parser("readers", help="the :GraphEvent reader census, as JSON")

    p_res = sub.add_parser("residency", help="what the graph holds (read-only)")
    p_res.add_argument("--graph-uri", default=os.environ.get("TORTOISE_DB_URI"))
    p_res.add_argument("--graph", default=None)

    p_sup = sub.add_parser("superset", help="is the record a superset of the feed?")
    p_sup.add_argument("--graph-uri", default=os.environ.get("TORTOISE_DB_URI"))
    p_sup.add_argument("--graph", default=None)
    p_sup.add_argument("--record-dsn", default=os.environ.get("TORTOISE_RECORD_DSN"))
    p_sup.add_argument("--org-id", required=True)
    p_sup.add_argument("--graph-id", required=True)

    args = parser.parse_args(argv)

    if args.verb == "readers":
        print(json.dumps(readers_report(), indent=2))
        return 0

    if not args.graph_uri:
        print("no graph URI: pass --graph-uri or set TORTOISE_DB_URI. "
              "NO VERDICT was produced.", file=sys.stderr)
        return 2

    if args.verb == "residency":
        print(json.dumps(residency(args.graph_uri, args.graph), indent=2))
        return 0

    if not args.record_dsn:
        print("no record DSN: pass --record-dsn or set TORTOISE_RECORD_DSN. "
              "NO VERDICT was produced.", file=sys.stderr)
        return 2
    conn = _record_connection(args.record_dsn, args.org_id)
    try:
        journal = record_rows(conn, args.org_id, args.graph_id)
    finally:
        conn.close()
    graph_rows_ = _graph_rows(_graph_connection(args.graph_uri), args.graph)
    verdict = superset(graph_rows_, journal)
    verdict["org_id"] = args.org_id
    verdict["graph_id"] = args.graph_id
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["verdict"] == "covered" else 1


if __name__ == "__main__":
    raise SystemExit(main())
