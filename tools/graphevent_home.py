#!/usr/bin/env python
"""Is the record already a home for the `:GraphEvent` change feed? — `#13094`.

THE QUESTION, and why it is a tool rather than a paragraph. `#13094`'s own body
says the move *may be a feed re-pointing* because "a record home very likely
exists already", and then states the question that must be answered FIRST:

    **Does `public.journal_event` carry every field `poll_events` serves, with a
    stable cursor, field for field?**

A cursor that moves by one is a silent subscriber gap, so the issue requires the
match to be "verified on real data before a re-point". Prose cannot do that. This
verb can, and it writes nothing to either store (see "READ-ONLY" below).

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

⛔ **`covered` IS THE HARDER ANSWER TO REACH, ON PURPOSE.** Three things that
would otherwise read as coverage are named deficits: a type the record carries
under a DIFFERENT name (the hard-delete lane writes `PointRetracted` to the store
and `EntityMutated` to the journal, `sdk.py:3801`) is reported as
``mapped_types`` and makes the verdict ``not_covered``, because its row coverage
is not comparable — the two payloads are different shapes; an EMPTY feed on
either side produces **no verdict at all** (exit 2), never a vacuous ``covered``;
and a partial read produces **no verdict** rather than a verdict over the part it
managed to read.

⛔ THE VERDICT IS NOT A LICENCE TO RE-POINT. Even at ``covered`` this verb cannot
see the two refusals `#13083` §6.3 established from the code, which are properties
of the PRODUCER and not of any tenant's data: `_emit_event` skips the journal
entirely when a payload-only emit carries no `point`/`id`/`turns` (`sdk.py:16204`),
and the hard-delete lane deliberately writes `PointRetracted` to the store and
`EntityMutated` to the journal (`sdk.py:3786-3801`, "two stores, one meaning
each"). ``covered`` on a tenant therefore means "this tenant's history happens to
be reproducible", never "the producers agree".

READ-ONLY, AND THAT IS A MECHANISM HERE, NOT A PROMISE. A Cypher read against a
graph the store does not hold CREATES it — measured in this repo
(`tortoise/projection/__init__.py`, #11113: a plain `MATCH (n) RETURN 1 LIMIT 1`
"ADDED it to `GRAPH.LIST`"). So both verbs require an EXPLICIT ``--graph`` (there
is no default graph name: defaulting to ``tortoise`` would mint an empty,
sweep-unowned graph on every multi-tenant server, and then compare it) and both
REFUSE a name `GRAPH.LIST` does not carry.

EXIT CODES (the repo's convention — a read that could not run is NOT a verdict):

==  =========================================================================
0   ``covered`` — the record's row multiset covers the graph's, per type
1   ``not_covered`` — a produced answer, as a failure, so a caller can gate on it
2   could not run: no graph URI, no record DSN, ``psycopg2`` absent, a graph the
    server does not hold, an EMPTY feed on either side, a feed that could not be
    paged to the end, or a store that refused the read. Never read as ``covered``
==

Usage:

    # what the graph holds, shape by shape (read-only; --graph is required)
    uv run python tools/graphevent_home.py residency \
        --graph-uri "$TORTOISE_DB_URI" --graph g_311d79c684de4d0e8953d189ed

    # the decisive comparison, against a real tenant's record
    uv run python tools/graphevent_home.py superset \
        --graph-uri "$TORTOISE_DB_URI" --graph g_311d79c684de4d0e8953d189ed \
        --record-dsn "$TORTOISE_RECORD_DSN" --org-id org_x \
        --graph-id g_311d79c684de4d0e8953d189ed

    # the reader census this PR's table was cut from
    uv run python tools/graphevent_home.py readers
"""

from __future__ import annotations

import sys

# ⛔ #5128 / #14181 — THE INTERPRETER FLOOR, BEFORE EVERY OTHER IMPORT.
# This tool is a `tools/` ENTRY POINT, and the suite enforces the floor on every
# such file (`tests/test_entry_point_python_guard.py`, `_guard_index`), which is
# why the guard is here even though THIS module's product imports
# (`tortoise.record_projection`, `falkordb`, `psycopg2`) are all deferred into
# functions: on an old interpreter they would fail at CALL time, deep inside a
# reader, naming neither this file nor the floor. Refuse first, and say which.
#
# EXIT 2, NOT 1: this file's own `EXIT CODES` table above reserves 1 for the
# PRODUCED `not_covered` verdict and 2 for "could not run" — and an interpreter
# below the floor is exactly "could not run". `SystemExit(<message>)` would wear
# status 1 and be read downstream as "the record is short" on a run that never
# read it, the same collision `tests/test_13094_graphevent_home.py` pins for a
# missing `psycopg2`. So the message goes to stderr and the code is the spare 2
# (#4053's shape), pinned per-file in `REFUSAL_CODES`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        "tools/graphevent_home.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/graphevent_home.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import hashlib
import json
import os
from collections import Counter
from collections.abc import Mapping
from typing import Any

#: ── THE READER CENSUS ─────────────────────────────────────────────────────
#:
#: Every statement in `tortoise/` that READS or MAINTAINS the `:GraphEvent`
#: label — including the three `ensure_event_schema` DDL statements, because a
#: re-home has to recreate the schema they describe (the `event_id` dedupe index
#: and the `seq` range index `read_after` scans), plus the two whole-graph export
#: scans that carry the label WITHOUT naming it. That is the inclusion rule,
#: stated rather than implied: **statements that name the label, plus the two
#: statements that carry it by being label-agnostic and would therefore carry it
#: into (or drop it from) a new home.**
#:
#: ⚠️ It is the table `#13094`'s PR carries, made checkable:
#: `tests/test_13094_graphevent_home.py` asserts every `snippets` entry still
#: appears in its `file` (whitespace-collapsed, so a re-indent is not a false red
#: while a reword is a real one) and `min_occurrences` where one file holds the
#: same statement twice. ⛔ WHERE THAT WORKS AND WHERE IT DOES NOT, because the
#: two cases differ and only one is a proof: the `event_store.py` dedupe pair is
#: the ONLY two occurrences of its text in that file, so deleting one reds the
#: census; the two export scans are byte-identical to two OTHER occurrences in
#: `hosted_backup.py` (four in total), so there `min_occurrences: 2` proves the
#: file still carries the statement twice and NOT that both censused sites
#: survive — the `line` is the address for those two, and the residual is named
#: on the rows themselves.
#:
#: CLASSIFICATION — the three classes, and what each one can justify:
#:
#:   ``serving``      — a query whose RESULT a user-facing answer is made of. A
#:                      serving reader needing a graph capability (traversal,
#:                      adjacency, fulltext, EP walk, vector) is what keeps a
#:                      shape resident. **There are NONE for `:GraphEvent`.**
#:   ``maintenance``  — the store's own bookkeeping: allocate, append, purge,
#:                      watermark, back up, and the schema those depend on. It
#:                      needs an ORDERED SCAN or an AGGREGATE, which is SQL-shaped;
#:                      moving the row does not delete the operation, it re-points
#:                      it.
#:   ``test``         — a fixture or assertion. It constrains the move (it pins
#:                      the contract) but it is not a reason to keep a byte of RAM.
READERS: tuple[dict[str, Any], ...] = (
    {
        "file": "tortoise/event_store.py",
        "line": "992",
        "reader": "read_after",
        "snippets": ("MATCH (e:GraphEvent) WHERE e.seq > $after",
                     "RETURN properties(e) ORDER BY e.seq ASC LIMIT $limit"),
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
        "min_occurrences": 2,
        "needs": "key existence on event_id",
        "class": "maintenance",
        "why": "a dedupe probe before the CREATE; a unique index answers it. The "
               "same statement is also `_event_row_exists` below, so this row "
               "requires BOTH occurrences.",
    },
    {
        "file": "tortoise/event_store.py",
        "line": "816",
        "reader": "append_event (the write)",
        "snippets": ("CREATE (e:GraphEvent {seq:$seq, ts:$ts, type:$type, ",
                     "event_id:$event_id, payload:$payload})"),
        "needs": "an insert",
        "class": "maintenance",
        "why": "the store's only writer.",
    },
    {
        "file": "tortoise/event_store.py",
        "line": "410",
        "reader": "ensure_event_schema (event_id index)",
        "snippets": ("CREATE INDEX FOR (n:GraphEvent) ON (n.event_id)",),
        "needs": "DDL: an exact-match index",
        "class": "maintenance",
        "why": "one of the three schema statements `ensure_event_schema` issues — "
               "the schema a re-home must recreate, which is why it is censused and "
               "not assumed.",
    },
    {
        "file": "tortoise/event_store.py",
        "line": "415",
        "reader": "ensure_event_schema (unique constraint)",
        "snippets": ("GRAPH.CONSTRAINT CREATE tortoise_ge_uid UNIQUE NODE "
                     "GraphEvent", "PROPERTIES 1 event_id"),
        "needs": "DDL: a uniqueness constraint on event_id",
        "class": "maintenance",
        "why": "the belt behind the dedupe pre-check (the app-side probe is the "
               "suspenders). ⚠️ Issued through `g.query()` inside a `try/except` "
               "that only logs, so on a server without the verb it is REFUSED and "
               "production runs without it.",
    },
    {
        "file": "tortoise/event_store.py",
        "line": "421",
        "reader": "ensure_event_schema (seq index)",
        "snippets": ("CREATE INDEX FOR (n:GraphEvent) ON (n.seq)",),
        "needs": "DDL: a range index",
        "class": "maintenance",
        "why": "the index `read_after`'s ordered scan plans against.",
    },
    {
        "file": "tortoise/event_store.py",
        "line": "879",
        "reader": "_event_row_exists",
        "snippets": ("MATCH (e:GraphEvent {event_id:$id}) RETURN count(e)",),
        "min_occurrences": 2,
        "needs": "key existence",
        "class": "maintenance",
        "why": "#9208's loss discriminator: asks whether a failed append landed. "
               "Shares its statement text with the dedupe probe above, which is why "
               "the two rows both require TWO occurrences in the file.",
    },
    {
        "file": "tortoise/event_store.py",
        "line": "1029",
        "reader": "purge_expired",
        "snippets": ("MATCH (n:GraphEvent) WHERE n.ts < $cutoff",
                     "WITH n LIMIT 10000 DETACH DELETE n RETURN count(*)"),
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
                     "DETACH DELETE n RETURN count(*)"),
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
        "reader": "whole-graph export (dump side)",
        "snippets": ("MATCH (n) RETURN labels(n), properties(n)",),
        "min_occurrences": 2,
        "needs": "a full label-agnostic dump",
        "class": "maintenance",
        "why": "the export carries `:GraphEvent` rows because it carries everything; "
               "it names no label and needs no capability. ⚠️ It is censused because "
               "a re-home must decide what the export does with the shape, not "
               "because it reads one. ⚠️ This statement occurs FOUR times in the file "
               "and the two censused sites are byte-identical, so `min_occurrences` "
               "proves only that the file still carries at least two of them — it "
               "cannot prove BOTH censused sites survive (a named residual; the "
               "`line` is the address).",
    },
    {
        "file": "tortoise/hosted_backup.py",
        "line": "1534",
        "reader": "whole-graph export (restore-side scan)",
        "snippets": ("MATCH (n) RETURN labels(n), properties(n)",),
        "min_occurrences": 2,
        "needs": "a full label-agnostic dump",
        "class": "maintenance",
        "why": "the second whole-graph scan (the restore's node-count verification): "
               "the same statement shape as the line above, so both rows require two "
               "occurrences — see the residual noted on that row.",
    },
)

#: The event types the store's DEFAULT contract writes (`sdk._GRAPH_EVENT_TYPES`),
#: restated here ONLY to name them in the report; the test asserts this set EQUALS
#: the one `sdk` declares, so it cannot drift.
GRAPH_EVENT_TYPES = frozenset({
    "PointAdded", "OperatorAdded", "PointRetracted", "PointSuperseded",
    "OperatorAnnotated", "PointPromoted", "OperatorPromoted", "DedupeRecorded",
    "DedupeRejected", "ObjectSuperseded", "PointInvalidated",
})

#: Store event type → the journal record type that carries the SAME meaning, where
#: the two vocabularies are known to differ.
#:
#: ⛔ A MAPPED TYPE IS NOT COVERAGE. `PointRetracted` (store) and `EntityMutated`
#: (journal) are the same removal announced to two surfaces with **different
#: payload shapes** (`sdk.py:3786-3801`: *"Two stores, one meaning each"*), so the
#: payload digests can never be compared across that pair. The mapping exists so
#: the report says "the record carries this event under another name, and its ROW
#: coverage is not comparable" instead of the false "the record has no such type",
#: and a mapped type still makes the verdict `not_covered` — the conservative
#: direction, because "we cannot compare it" is not "it is covered".
TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "PointRetracted": ("EntityMutated",),
}

RECORD_TABLE = "journal_event"


class Unpageable(RuntimeError):
    """A feed that cannot be paged to the end — no verdict is produced.

    Raised rather than returning a partial list: a verdict computed over a
    silently truncation-shaped read is the false PASS this verb exists to
    prevent. The cases named at the call site are a fractional `seq` (which the
    repo has measured: `webhook_delivery.py` records a hand-written `3.5`) and a
    boundary group larger than one page.
    """


class RecordUnavailable(RuntimeError):
    """The record could not be reached or read — no verdict is produced.

    ⛔ NOT a `SystemExit`: `main()` returns 1 for a produced `not_covered`, and
    `SystemExit` derives from `BaseException`, so a guard written as
    `except Exception` would let it escape and leave the process exiting 1 — the
    `not_covered` code — for a record that was never opened.
    """


# ── the graph side ─────────────────────────────────────────────────────────

def _graph_connection(uri: str):
    from falkordb import FalkorDB

    return FalkorDB.from_url(uri.replace("docker://", "redis://"))


def _known_graphs(db) -> list[str]:
    """The graph names the server holds. Used to REFUSE a name it does not.

    ⛔ This is not politeness: a read against an absent graph CREATES it (#11113,
    measured), so a verb that "just looks" at a name the server never had leaves
    an empty graph behind — on a multi-tenant box, one no sweep owns.
    """
    try:
        listed = db.connection.execute_command("GRAPH.LIST")
    except Exception as exc:
        raise Unpageable(
            f"the graph list could not be read ({type(exc).__name__}: {exc}), so "
            f"an absent graph cannot be told from a mistyped name — and reading "
            f"one would CREATE it") from exc
    return [str(name) for name in listed]


def _require_graph(db, graph: str) -> None:
    known = _known_graphs(db)
    if graph not in known:
        raise Unpageable(
            f"the server does not hold a graph named {graph!r} (it holds "
            f"{len(known)}: {sorted(known)[:8]}{'…' if len(known) > 8 else ''}). "
            f"Refusing rather than reading it — a Cypher read against an absent "
            f"graph CREATES it, which would make this read-only verb write")


def _graph_rows(db, graph: str, batch: int = 2000) -> list[dict[str, Any]]:
    """Every `:GraphEvent` row, as `{seq, type, event_id, payload}`.

    Paged by `seq`, and the paging is defensive on purpose, because BOTH shapes
    that break a keyset read exist in this store:

    * `seq` is NOT unique — only `event_id` is (`event_store.py:415`; the `seq`
      index at `:421` is plain). A boundary group with a repeated `seq` would be
      SKIPPED by a strict `seq > last` cursor, i.e. rows silently missing from a
      verdict that then says `covered`. So the cursor is COMPOSITE —
      `(seq, event_id)` with `seq > a OR (seq = a AND event_id > e)` — and the
      sort is `seq, event_id`, so a duplicated `seq` is walked one `event_id` at a
      time instead of being re-fetched as an unadvanceable page.
    * `seq` is not guaranteed to be an integer. `webhook_delivery.py` measured a
      hand-written `3.5`, and an `int()` cursor would never advance past it — an
      unbounded loop. The cursor keeps the value the server returned, so `> 3.5`
      advances.
    * a row with NO `event_id` cannot take part in that cursor, so a page whose
      LAST row has none is a NAMED refusal: silently stopping there would be a
      partial read reported as a verdict. Rows are deduped on
      `(seq, type, payload-digest)` as defense in depth.
    """
    # ⛔ THE REFUSAL LIVES HERE, not in a verb: `residency` and `superset` both
    # call this function, and a guard installed in one of them is how a
    # read-only verb mints a graph through the other (review round 2, P1).
    _require_graph(db, graph)
    g = db.select_graph(graph)
    rows: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    after: Any = None
    last_id: str = ""
    while True:
        if after is None:
            page = g.query(
                "MATCH (e:GraphEvent) RETURN e.seq, e.type, e.event_id, e.payload "
                "ORDER BY e.seq ASC, e.event_id ASC LIMIT $limit",
                params={"limit": batch},
            ).result_set
        else:
            page = g.query(
                "MATCH (e:GraphEvent) "
                "WHERE e.seq > $after OR (e.seq = $after AND e.event_id > $last_id) "
                "RETURN e.seq, e.type, e.event_id, e.payload "
                "ORDER BY e.seq ASC, e.event_id ASC LIMIT $limit",
                params={"after": after, "last_id": last_id, "limit": batch},
            ).result_set
        if not page:
            # ⛔ `break`, never `return`: the completeness counts below are the
            # ONLY thing that can tell "the feed ends here" from "the cursor can
            # no longer advance" (round-2 review, P1), and an early return skipped
            # them.
            break
        added = 0
        for seq, type_, event_id, payload in page:
            # ⛔ `event_id` BELONGS IN THE KEY: two rows with the same seq,
            # type and payload but distinct event_ids are two rows of the
            # multiset, and merging them undercounts the graph side (review
            # round 2, P2).
            key = (seq, event_id, type_, _digest(payload))
            if key in seen:
                continue
            seen.add(key)
            rows.append({"seq": seq, "type": str(type_), "event_id": event_id,
                         "payload": payload})
            added += 1
        cursor_seq, _, cursor_id = page[-1][0], page[-1][1], page[-1][2]
        if not isinstance(cursor_id, str) or not cursor_id:
            raise Unpageable(
                f"the row at seq {cursor_seq!r} carries no `event_id`, so the "
                f"composite cursor cannot advance past it and the rest of the feed "
                f"cannot be read. A partial read is not a verdict, so none is "
                f"produced — repair the row (append_event always writes one) or "
                f"read the feed with `read_after`, which pages on `seq` alone.")
        if not added:
            raise Unpageable(
                f"the feed could not be paged past seq {cursor_seq!r}/"
                f"{cursor_id!r}: this page added no new row. A partial read is not "
                f"a verdict, so none is produced")
        after, last_id = cursor_seq, cursor_id

    # ⛔ THE WALK MUST PROVE IT REACHED THE END (review round 2, P1). A cursor
    # cannot see three shapes the store admits, and each makes an empty page
    # indistinguishable from the end: a NULL `seq` (never satisfies `seq > $after`),
    # a `seq` stored as a STRING (the engine orders strings before numbers, so a
    # numeric cursor matches no row), and a duplicated (seq, event_id) pair beyond
    # a page boundary. Counting first makes the failure a REFUSAL instead of a
    # shortened read that the verdict would happily call `covered`.
    null_seq = g.query(
        "MATCH (e:GraphEvent) WHERE e.seq IS NULL RETURN count(e)").result_set[0][0]
    if int(null_seq or 0):
        raise Unpageable(
            f"{int(null_seq)} `:GraphEvent` row(s) carry no `seq`, so they can never "
            f"be reached by a `seq` cursor and the feed cannot be read completely. "
            f"A partial read is not a verdict, so none is produced — repair the "
            f"row(s) (`append_event` always writes a `seq`).")
    total = int(g.query("MATCH (e:GraphEvent) RETURN count(e)").result_set[0][0] or 0)
    if total != len(rows):
        raise Unpageable(
            f"the feed holds {total} row(s) but the cursor reached {len(rows)}: a "
            f"`seq` stored as a string, or a duplicated (seq, event_id) pair past a "
            f"page boundary, makes the remaining rows unreachable. A partial read "
            f"is not a verdict, so none is produced.")
    return rows


def residency(graph_uri: str, graph: str) -> dict[str, Any]:
    """What the graph holds, and what share of it this one shape is."""
    db = _graph_connection(graph_uri)
    # `_graph_rows` refuses an unknown graph (and that refusal covers every verb);
    # this read happens first, so the same check is made here too.
    _require_graph(db, graph)
    g = db.select_graph(graph)
    totals = g.query(
        "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS n "
        "ORDER BY n DESC").result_set
    labels = {str(label): int(n) for label, n in totals}
    total = sum(labels.values())
    events = _graph_rows(db, graph)
    payload_bytes = sum(len((row["payload"] or "").encode()) for row in events)
    usage: dict[str, Any] = {}
    try:
        flat = db.connection.execute_command("GRAPH.MEMORY", "USAGE", graph)
        usage = dict(zip(flat[0::2], flat[1::2], strict=True))
    except Exception as exc:  # a server without the verb is not a fault
        usage = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "graph": graph,
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
        import psycopg2
    except ModuleNotFoundError as exc:  # pragma: no cover — environment, not code
        raise RecordUnavailable(
            "psycopg2 is not installed, so the record side cannot be read: "
            "`uv sync --extra postgres` (or `pip install psycopg2-binary`). "
            "NO VERDICT was produced.") from exc
    conn = psycopg2.connect(record_dsn)
    from tortoise.record_projection import scope_connection

    scope_connection(conn, org_id)
    return conn


def record_rows(conn: Any, org_id: str, graph_id: str,
                batch: int = 2000) -> list[dict[str, Any]]:
    """Every `journal_event` row for this scope, paged by `seq` (a PRIMARY KEY)."""
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

    ⚠️ An EMPTY graph side returns ``no_verdict``, not ``covered``: an empty
    multiset is a subset of everything, so the naive arithmetic would answer
    `covered` for a feed that had been purged, for a mistyped graph name, or for
    a store that answered a read with nothing — the vacuous PASS this verb must
    never emit.
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
    mapped_types: list[str] = []
    for type_, counter in sorted(graph_by_type.items()):
        available = journal_by_type.get(type_)
        if available is None:
            aliases = [alias for alias in TYPE_ALIASES.get(type_, ())
                       if alias in journal_by_type]
            if aliases:
                # The record carries this event under another name; the payload
                # shapes differ, so ROW coverage is not comparable (see
                # TYPE_ALIASES). A mapped type is a named deficit, never coverage.
                mapped_types.append(type_)
                per_type[type_] = {"graph_rows": sum(counter.values()),
                                   "record_has_type": False,
                                   "record_has_alias_type": aliases,
                                   "row_coverage": "not_comparable"}
                continue
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
    covered = missing_rows == 0 and not missing_types and not mapped_types
    # ⛔ The empty arm comes FIRST: an empty multiset is a subset of everything, so
    # `covered` would be arithmetically true for a purged feed, a mistyped graph
    # name, or a store that answered with nothing — the vacuous PASS.
    verdict = ("no_verdict" if (not graph_rows or not journal_rows)
               else ("covered" if covered else "not_covered"))
    return {
        "verdict": verdict,
        "graph_events": len(graph_rows),
        "record_events": len(journal_rows),
        "missing_rows": missing_rows,
        "missing_types": missing_types,
        "mapped_types": mapped_types,
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


#: Cypher shapes that only a graph engine can answer. A row carrying one of
#: these is NOT a `record_only` candidate, whichever class it was filed under.
GRAPH_CAPABILITY_CYPHER = (
    "-[*",                # a variable-length traversal
    "*1..", "*..",        # a bounded / open hop count
    "shortestpath",       # path search
    "call db.idx.fulltext", "db.idx.fulltext",
    "call db.idx.vector", "db.idx.vector",
)

#: The audit's own vocabulary (`#13083` §2) — a graph engine's four capabilities
#: plus EP state — for a row whose author describes the need in words instead.
GRAPH_CAPABILITY_WORDS = (
    "traversal", "adjacency", "fulltext", "vector", "ep walk",
    "variable-length",
)


def graph_capability(row: Mapping[str, Any]) -> str | None:
    """The graph capability this reader needs, or `None` — the ONE predicate.

    ⛔ It reads the row's Cypher (`snippets`) as well as its prose. A prose-only
    check cannot see the reader it exists to catch: a row whose `snippets` carry
    `MATCH (e:GraphEvent)-[*1..2]->(n)` and whose `needs`/`why` are worded around
    the vocabulary passes every prose check while needing a full traversal. The
    snippet is where a capability actually lives, so it is what is read (#14164
    review, finding 3 — reproduced: such a row filed as `maintenance` left every
    pin green while this doc promised the opposite).
    """
    prose = f"{row.get('needs', '')} {row.get('why', '')}".lower()
    for word in GRAPH_CAPABILITY_WORDS:
        if word in prose:
            return word
    for snippet in row.get("snippets", ()):
        haystack = snippet.lower()
        for shape in GRAPH_CAPABILITY_CYPHER:
            if shape in haystack:
                return f"cypher:{shape}"
    return None


def readers_report() -> dict[str, Any]:
    """The reader census, with the class counts — the PR's table, as data."""
    counts = Counter(reader["class"] for reader in READERS)
    # Every row, at every class — not only the `serving` ones. A capability
    # reader misfiled as `maintenance` is exactly the case this must surface,
    # and a filter on `class` cannot see it by construction.
    return {
        "readers": list(READERS),
        "counts": dict(counts),
        "graph_capability_readers": [
            {"file": r["file"], "line": r["line"], "reader": r["reader"],
             "capability": capability}
            for r in READERS if (capability := graph_capability(r))
        ],
        "verdict": ("no reader needs a graph capability — every reader is a "
                    "key lookup, an ordered range scan, an aggregate, a predicate "
                    "delete, or the schema those three depend on"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="verb", required=True)

    sub.add_parser("readers", help="the :GraphEvent reader census, as JSON")

    p_res = sub.add_parser("residency", help="what the graph holds (read-only)")
    p_res.add_argument("--graph-uri", default=os.environ.get("TORTOISE_DB_URI"))
    # ⛔ REQUIRED, with no default: see the module docstring — a read against an
    # absent graph CREATES it, and a default name would mint an empty one on every
    # server that does not have it.
    p_res.add_argument("--graph", required=True)

    p_sup = sub.add_parser("superset", help="is the record a superset of the feed?")
    p_sup.add_argument("--graph-uri", default=os.environ.get("TORTOISE_DB_URI"))
    p_sup.add_argument("--graph", required=True)
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
        try:
            print(json.dumps(residency(args.graph_uri, args.graph), indent=2))
        except Exception as exc:  # a refused read is not a reading
            print(f"the graph could not be read ({type(exc).__name__}: {exc}). "
                  "NO VERDICT was produced.", file=sys.stderr)
            return 2
        return 0

    if not args.record_dsn:
        print("no record DSN: pass --record-dsn or set TORTOISE_RECORD_DSN. "
              "NO VERDICT was produced.", file=sys.stderr)
        return 2
    # ⛔ A STORE THAT REFUSED IS NOT A VERDICT (#9551's contract, and this verb's
    # own reason for existing): an uncaught exception here would leave `main()`
    # with Python's default exit 1 — the SAME code `not_covered` uses — so a
    # caller gating on the exit would read "the record is short" from a record it
    # never managed to read. Exit 2 says what happened. `RecordUnavailable` is a
    # `RuntimeError` for the same reason (a `SystemExit` would escape this arm).
    try:
        conn = _record_connection(args.record_dsn, args.org_id)
        try:
            journal = record_rows(conn, args.org_id, args.graph_id)
        finally:
            conn.close()
        graph_rows_ = _graph_rows(_graph_connection(args.graph_uri), args.graph)
    except Exception as exc:  # a store that refused is not a verdict (see above)
        print(f"a store could not be read ({type(exc).__name__}: {exc}). "
              "NO VERDICT was produced.", file=sys.stderr)
        return 2
    verdict = superset(graph_rows_, journal)
    verdict["org_id"] = args.org_id
    verdict["graph_id"] = args.graph_id
    verdict["graph"] = args.graph
    print(json.dumps(verdict, indent=2))
    if verdict["verdict"] == "covered":
        return 0
    if verdict["verdict"] == "no_verdict":
        print("the graph holds no `:GraphEvent` rows, so there is nothing to "
              "compare — an empty or purged feed is NOT `covered`. NO VERDICT "
              "was produced.", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
