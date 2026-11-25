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
and `EntityMutated` to the journal, `sdk.py:3831`) is reported as
``mapped_types`` and makes the verdict ``not_covered``, because its row coverage
is not comparable — the two payloads are different shapes; an EMPTY feed on
either side produces **no verdict at all** (exit 2), never a vacuous ``covered``;
and a partial read produces **no verdict** rather than a verdict over the part it
managed to read.

⛔ THE VERDICT IS NOT A LICENCE TO RE-POINT. Even at ``covered`` this verb cannot
see the two refusals `#13083` established from the code, which are properties
of the PRODUCER and not of any tenant's data: `_emit_event` skips the journal
entirely when a payload-only emit carries no `point`/`id`/`turns`
(`sdk.py:16258-16259`), and the hard-delete lane deliberately writes `PointRetracted` to the store and
`EntityMutated` to the journal (`sdk.py:3815-3831`, "two stores, one meaning
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
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any

#: ⛔ THIS IS THE WRITE GATE'S GRAMMAR, ONE BRANCH WIDER — copied rather than
#: imported, and the copy is held to the original by a test, because the two
#: cannot share a single pass. The canonical spelling is
#: `tortoise/projection/__init__.py`'s `_GATE_LITERAL_OR_COMMENT_RE`:
#: `'…' | "…" | //… | /*…*/`. Two of its three possible treatments of a
#: backtick-quoted identifier are wrong HERE — KEEPING it (the gate's own choice)
#: leaves a `//` inside `` `a//b` `` to fire the `//` branch and swallow the rest of
#: the line, pattern and arms included; DELETING it (the sibling regex's choice)
#: kills the `` CALL `algo.pageRank` `` arm the round-8 control row exists to pin.
#: So a backtick branch is added that substitutes the identifier's CONTENT, and it
#: must live in the SAME alternation: alone, a later pass cannot prevent a comment
#: from opening inside text an earlier pass already consumed.
#: ⚠️ Measured, both directions: `MATCH (n) WHERE n.`a//b` = 1 MATCH (e)-[r:T]-(p)`
#: returned `None` under the canonical regex alone (the `//` ate the pattern) and
#: `` CALL `algo.pageRank`(…) `` returns `call algo` here and would return `None`
#: under a deleting stripper. `tests/test_13094_graphevent_home.py` pins the
#: agreement of the two grammars on every non-backtick input, so a change to the
#: gate's spelling reds here rather than drifting silently.
_LITERAL_COMMENT_OR_IDENT_RE = re.compile(
    r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`([^`]*)`|//[^\n]*|/\*.*?\*/",
    re.S,
)


def _strip_noise(statement: str) -> str:
    """Drop string literals, comments and backtick quoting — the ONE pre-pass.

    Backtick quoting is UNWRAPPED, not deleted: the identifier's name survives as
    text (`` `algo.pageRank` `` -> `algo.pageRank`) while the quoting cannot start a
    comment. See `_LITERAL_COMMENT_OR_IDENT_RE` for the measurement that forces the
    backtick branch.
    """
    return _LITERAL_COMMENT_OR_IDENT_RE.sub(
        lambda match: match.group(1) or "", statement
    )

#: ── THE READER CENSUS ─────────────────────────────────────────────────────
#:
#: Every SITE in `tortoise/` at which a statement READS or MAINTAINS the
#: `:GraphEvent` label — including the three `ensure_event_schema` DDL statements,
#: because a re-home has to recreate the schema they describe (the `event_id` dedupe
#: index and the `seq` range index `read_after` scans), plus the sites of the
#: label-agnostic whole-graph scan in the RESTORE path, which carries the label
#: WITHOUT naming it. That is the inclusion rule, stated rather than implied: **one
#: row per censused site, because a re-home must reason about each site and two
#: sites can share one statement's text: the sites whose statement names the label,
#: plus the sites of the label-agnostic scan, which would therefore carry the label
#: into (or drop it from) a new home.** ⚠️ The UNIT is the site, not the statement
#: text (#14183 review round 11, verified): that scan's text occurs FOUR times in
#: `hosted_backup.py` — the two restore decisions censused here (`:1333` pre-check,
#: `:1534` node-count verification), the live read inside
#: `_restore_into_temp_verify_swap` (`:3603`) and a docstring mention — and those
#: two rows pin the EXACT count in `occurrences`, so the third CODE occurrence is
#: covered by that pin rather than by a row of its own.
#:
#: ⚠️ It is the table `#13094`'s PR carries, made checkable:
#: `tests/test_13094_graphevent_home.py` asserts every `snippets` entry still
#: appears in its `file` (whitespace-collapsed, so a re-indent is not a false red
#: while a reword is a real one) and `min_occurrences` where one file holds the
#: same statement twice. ⛔ WHERE THAT WORKS AND WHERE IT DOES NOT: the
#: `event_store.py` dedupe pair is the ONLY two occurrences of its text in that
#: file, so deleting one reds the census; the two restore-side scans share their
#: text with two more occurrences in `hosted_backup.py` (four in total), so those
#: rows pin the EXACT count in `occurrences` — deleting ANY of the four reds, which
#: a bare `min_occurrences` floor did NOT do (measured: deleting both censused
#: sites left two and stayed green). What no pin can do is say WHICH occurrence a
#: row addresses: the `line` is that address and nothing grades it.
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
        "why": "⛔ THE SUSPENDERS, not the belt — the row said both, in one "
               "breath, until #14183 review round 10 measured which layer holds. "
               "`ensure_event_schema` issues it through `g.query()`, which cannot "
               "parse a module command, so the constraint is never created in "
               "production: the app-side dedupe pre-check in `event_store.py` is "
               "the belt that is actually there, and the store admits a duplicated "
               "`(seq, event_id)` row if that probe goes. ⚠️ `event_store.py:795-798` "
               "still states the reverse and is tracked as #14212 (a product-code "
               "comment, out of this census's files). ⛔ THE `missing supporting exact-match index` THIS "
               "ROW ONCE RECORDED WAS A SCRIPT DEFECT, NOT A SERVER LIMIT (#14183 "
               "review round 9, measured): the measurement put `tortoise_ge_uid` in "
               "the GRAPH-KEY slot of the module command, so it ran against a "
               "freshly minted graph with no `event_id` index at all. Correctly "
               "formed against a graph WITH that index the command is ACCEPTED "
               "(`PENDING`) on `falkordb-server:v4.20.6`, empty and 20k-node graphs "
               "alike — there is no asynchronous-index race to allow for.",
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
               "not this shape's reader — a statement whose pattern names only a SIBLING "
               "label is that label's reader, not this shape's.",
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
        "reader": "whole-graph scan (restore's dirty-target pre-check)",
        "snippets": ("MATCH (n) RETURN labels(n), properties(n)",),
        "min_occurrences": 2,
        "occurrences": 4,
        "needs": "a full label-agnostic read of the target graph",
        "class": "maintenance",
        "why": "this is `restore_graph`'s pre-check (`hosted_backup.py:1333`, inside "
               "`def restore_graph` at `:1098`), NOT the export: it refuses a dirty target "
               "before the rebuild, so a re-home must keep it reading every node whatever "
               "shape it carries. ⚠️ It is censused because the restore must decide what "
               "it does with a `:GraphEvent` node, not because it reads one. ⚠️ NEITHER "
               "EXPORT-SIDE READER IS IN THIS CENSUS: the paged dump's node statement is "
               "`_DUMP_NODE_PAGE_QUERY` (`hosted_backup.py:454`, consumed by `dump_graph` "
               "at `:647`), and the export endpoint's own whole-graph scan is "
               "`hosted_api.py:36591` (`_export_graph_snapshot`; the `"
               "\"GraphEvent\" in labels` branch is `:36621` and the append `:36629` — a "
               "SERVING reader under this census's own class rule, so the `serving: 1` "
               "count in this file excludes it). No row carries either (#14204). ⚠️ This "
               "statement occurs FOUR times in `hosted_backup.py` — `:1333`, `:1534`, the "
               "live read at `:3603` (`_restore_into_temp_verify_swap`) and a docstring "
               "mention at `:3442` — and the exact count is pinned, so deleting any of "
               "them reds. What the pin cannot do is say WHICH occurrence a row addresses: "
               "the `line` is that address and nothing grades it.",
    },
    {
        "file": "tortoise/hosted_backup.py",
        "line": "1534",
        "reader": "whole-graph scan (restore's node-count verification)",
        "snippets": ("MATCH (n) RETURN labels(n), properties(n)",),
        "min_occurrences": 2,
        "occurrences": 4,
        "needs": "a full label-agnostic read of the graph",
        "class": "maintenance",
        "why": "the second restore-side scan (the restore's `actual_nodes` verification, "
               "`hosted_backup.py:1533-1534`): "
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
#: payload shapes** (`sdk.py:3815-3831`: *"Two stores, one meaning each"*), so the
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
    """The graph-side handle, with the product's BOUNDED read policy (#3065/#8118).

    ``falkordb.FalkorDB`` defaults ``socket_timeout=None`` and hands that to
    redis-py explicitly, which OVERRIDES redis-py's own default — so a raw
    client built without the kwarg is not "5s by default", it is UNBOUNDED.
    Against a stalled engine (the #2969 defrag loop, #2838's MERGE lock) the
    read parks in ``recv()`` forever: alive, 0% CPU, no error, no exit — on a
    verb whose whole contract is "read the feed and produce a verdict".

    ⛔ This site landed without the bound (#14164) and reddened
    ``tests/test_bounded_raw_clients_3065.py``'s tool-surface guard, which is
    the systemic half of #8118: the bound is not a product-package privilege,
    and a new raw client in ``tools/`` must carry it BEFORE it ships. The
    resolver, not a literal, so the package's knobs
    (``TORTOISE_DB_SOCKET_TIMEOUT`` / ``TORTOISE_DB_SOCKET_CONNECT_TIMEOUT``)
    reach this client too.
    """
    from falkordb import FalkorDB

    from tortoise.projection import _bounded_server_socket_kwargs

    return FalkorDB.from_url(
        uri.replace("docker://", "redis://"),
        **_bounded_server_socket_kwargs(),
    )


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
    * a row with no STRING `event_id` — absent, or a non-string that cannot be
      compared with a string cursor (the same mixed-type ordering trap the
      end-proof guards) — cannot take part in that cursor, so a page whose
      LAST row has none is a NAMED refusal: silently stopping there would be a
      partial read reported as a verdict.

    Rows are deduped on `(seq, event_id, type, payload-digest)` as defense in
    depth — `event_id` is IN the key because two rows sharing seq/type/payload
    but carrying distinct `event_id`s are two rows of the multiset, and merging
    them would undercount the graph side.

    ⚠️ WITH THE COMPLETENESS COUNT, a dedupe HIT cannot produce a deduped
    reading: `total` counts NODES while `len(rows)` counts DISTINCT KEYS, so any
    hit makes `len(rows) < total` and the walk REFUSES (`Unpageable`) rather than
    returning a silently merged list. That includes the genuinely duplicated
    `(seq, event_id, type, payload)` row this store admits where the uniqueness
    constraint never exists (production issues it through `g.query()`, which cannot
    parse the module command): it is refused as a short read, never merged away. So
    the dedupe's only remaining effect is to keep `len(rows)` meaning "distinct
    keys" — it must not be described as, and must not be trusted to perform, a
    merge.
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
                f"the row at seq {cursor_seq!r} carries no STRING `event_id` — it is "
                f"absent, or it is not a string and so cannot be compared with a "
                f"string cursor — so the composite cursor cannot advance past it and "
                f"the rest of the feed cannot be read. A partial read is not a "
                f"verdict, so none is produced — repair the row (append_event always "
                f"writes one) or read the feed with `read_after`, which pages on "
                f"`seq` alone.")
        if not added:
            raise Unpageable(
                f"the feed could not be paged past seq {cursor_seq!r}/"
                f"{cursor_id!r}: this page added no new row. A partial read is not "
                f"a verdict, so none is produced")
        after, last_id = cursor_seq, cursor_id

    # ⛔ THE WALK MUST PROVE IT REACHED THE END (review round 2, P1). A cursor
    # cannot see three shapes the store admits, and each makes an empty page
    # indistinguishable from the end: a NULL `seq` (never satisfies `seq > $after`),
    # a `seq` stored as a STRING (the engine orders strings before numbers, so the
    # page that ends the STRING block puts a STRING on the cursor, and `seq >
    # $after` then matches no NUMERIC row — every row sorting after the block is
    # unreachable; measured on the lane, `ORDER BY v ASC` gives `'10', 5, 20`,
    # `v > '10'` returns nothing and `v > 5` returns `20`, so it is the STRING
    # cursor that strands and NOT a numeric one), and a duplicated (seq, event_id)
    # pair beyond a page boundary. Counting first makes the failure a REFUSAL
    # instead of a shortened read that the verdict would happily call `covered`.
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
    (`#13083`: `PointRetracted` on the graph side is `EntityMutated` in the
    journal) and a type-level absence is the failure a subscriber cannot see.

    ⚠️ An EMPTY side returns ``no_verdict``, not ``covered`` — and that is BOTH
    sides, not only the graph's: an empty multiset is a subset of everything, so
    the naive arithmetic would answer `covered` for a feed that had been purged,
    for a mistyped graph name, or for a store that answered a read with nothing
    — and equally for a RECORD that holds none of this tenant's rows, which is
    what a mistyped `--org-id`/`--graph-id` or a never-armed scope looks like
    against a graph that holds thousands. The vacuous PASS this verb must never
    emit, from either direction. Which side was empty is carried by
    ``graph_events`` / ``record_events`` so the caller can say WHICH.
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
            "EVEN AT `covered` THE PRODUCERS DISAGREE (#13083): a "
            "payload-only emit writes no journal record at all (`sdk.py` "
            "`_emit_event`), and the hard-delete lane writes `PointRetracted` to "
            "the store and `EntityMutated` to the journal. A tenant-level "
            "`covered` says this tenant's history is reproducible; it is not "
            "evidence that a re-point would not drop future events."),
    }


#: Cypher shapes that only a graph engine can answer. A row carrying one of
#: these is NOT a `record_only` candidate, whichever class it was filed under.
#:
#: ⛔ The ADJACENCY arm is the one that matters most and is the easiest to miss.
#: The `#13083` census (its document was deleted on 2026-11-23, `#11081` audit row B2 — the
#: passage is in git history) defined "adjacency" and the EP walk
#: by **fixed-length** patterns — `(new:Point {id:$n})-[r]->(t)`, `NOT (n)--()`,
#: `MATCH (a)-[r:IMPL|NAND]-(b)` — so a variable-length-only detector (`-[*`) sees
#: neither, and a one-hop reader filed as `maintenance` would pass. Measured at
#: #14164 review round 2: six capability-bearing shapes returned `None` under a
#: `-[*`-only arm, including the audit's own EP walk verbatim.
#:
#: ⛔ Matched against a WHITESPACE- AND BACKTICK-STRIPPED snippet, so `- [r]->` and
#: `-[r]->` are one shape rather than two, and `` CALL `algo.pageRank`(…) `` is the
#: same shape as `CALL algo.pageRank(…)` — the engine accepts all three spellings
#: (this repo's own write gate documents the backticked procedure name as valid
#: Cypher; `tortoise/projection/__init__.py:3572` carries the explicit `` `? ``
#: in `_CALL_PROC_RE`, and `:3594` states that a backtick-quoted name is valid).
#:
#: ⛔ THE BACKTICK IS THE FALSE-PASS DIRECTION, which is why it is STRIPPED rather
#: than declared away (`#14183` round-8 review, P2/P3). A contiguous arm sees
#: neither `- [r] -` nor a quoted procedure name, so a capability reader could
#: space or backtick its way past every prose check and the census's derived
#: verdict would read "no reader needs a graph capability" beside a real one —
#: the single failure this guard exists to prevent. Measured before the strip:
#: `CALL `algo.pageRank`(null, null)` returned `None`.
#:
#: ⛔ THE COMMENT IS THE SAME FALSE-PASS DIRECTION, one spelling further out, and it
#: was OPEN until round 12 (`#14183` review, P2). Cypher accepts a comment BETWEEN
#: the `-` and the `[`, so `MATCH (e)-/* hop */[r:TOUCHED]-(p)` carries neither
#: `-[` nor `]->` nor `--` contiguously: measured, `graph_capability` returned
#: `None` for both `-/*hop*/[r:T]-` and the line-comment form `-//hop\n[r:T]-`,
#: while the same pattern re-spaced (`- [r] -`, the round-8 row) was caught. The
#: engine executes both comment spellings, and this repo already strips comments
#: for exactly this reason elsewhere (`tortoise/projection/__init__.py`'s
#: `_GATE_LITERAL_OR_COMMENT_RE`). So the snippet is COMMENT- and LITERAL-stripped
#: BEFORE the whitespace/backtick strip — by `_strip_noise` above, which is that
#: grammar PLUS the backtick branch it cannot express (see that regex's block for
#: why a `//` inside `` `a//b` `` makes a straight reuse a hole of its own).
#: ⚠️ String literals go too: a `//` inside `'https://…'` would otherwise open a
#: line comment and swallow every arm after it on that line.
_NO_WS = re.compile(r"[\s`]+")
GRAPH_CAPABILITY_CYPHER = (
    "-[*",                # a variable-length traversal
    "*]",                 # a TYPED variable-length pattern, `-[r:NEXT*]->`
    "*1..", "*..",        # a bounded / open hop count
    "-[",                  # a fixed-length relationship pattern (adjacency)
    "]->",                  # a hop written with its type side last, `-[r:T]->`
    # ⛔ AN UNTYPED HOP IS ANCHORED ON ITS NODES, NOT ON THE DASHES. `--` and `<-`
    # alone over-match in the FALSE-BLOCK direction once the snippet is
    # whitespace-stripped and backtick-quoted identifiers are unwrapped (#14183
    # rounds 13-14, P3): `$after - -1` becomes `--1` (a unary minus), `e.ts < -1`
    # becomes `s<-1` (a comparison), and `` n.`a--b` `` / `` n.`a<-b` `` unwrap to a
    # property NAME containing the punctuation. None of the three walks anything,
    # and a row misfiled as capability-bearing publishes the OPPOSITE verdict and
    # reds the census's own "no reader needs a graph capability" pin over a reader
    # that walks nothing.
    # ⚠️ Cypher always parenthesises the node to the LEFT of an inbound hop, so
    # anchoring on that closing paren keeps every real spelling — `(a)<--(b)` and
    # `(a)<-[r:T]-(b)` (also caught by `-[`) — and drops the two shapes that made
    # the bare `<-` a false-block: a comparison whose LEFT operand is not a
    # parenthesised node (`e.ts < -1` → `s<-1`) and a property NAME carrying the
    # punctuation (`` n.`a<-b` `` → `a<-b`).
    # ⛔ AN UNBRACKETED HOP IS NOT A SPELLING, so it is not why anything here is
    # anchored (#14183 round-17 review, P1 — correcting an earlier note that cited
    # `(a)<-r-(b)` as a lost spelling). Measured on the PINNED ENGINE
    # (`falkordb-server:v4.20.6`, `tools/test_lane.py:324`: the image the CI gate
    # runs), `MATCH (a)<-r-(b)` , `(a)-r-(b)` , `(a)-r->(b)` and `(a)-r:T-(b)` are
    # ALL PARSE ERRORS (`Invalid input 'r': expected '-' or '['`), while
    # `-[r]-` / `-[r:T]->` execute. A spelling the engine refuses cannot be a
    # reader's shape, so it gets no arm: an arm for it could only ever
    # false-positive, which is the `size((` argument one arm over.
    # ⛔ WHAT THE ANCHOR DOES *NOT* GUARANTEE, stated rather than implied (#14183
    # round-16 review, P3): the note that used to sit here said a false shape
    # carries "a property or digit in that position rather than a `)`", which is
    # not exhaustive — a PARENTHESISED arithmetic operand ends in `)` too, so
    # `(n.ts - n.start) < -5` and `(x) < -1` still strip to `)<-` and are filed
    # as capability readers. ⚠️ THE RESIDUAL IS ACCEPTED, NOT CLOSED: it is the
    # fail-CLOSED direction (a row that walks nothing is refused; nothing that
    # walks is smuggled past), no row in this tree reaches it, and closing it needs
    # an OPERAND-AWARE exclusion — "nothing that is an expression follows the `<-`"
    # — which is a second definition of "a hop" beside this substring list (a
    # "not followed by a digit" test does not close it: `(e.x) < -e.y` strips to
    # `)<-` with no digit anywhere after it). The sibling residual for
    # the `[` form is `RETURN [1,2][0] - [3,4][0]` (`-[`), named for the same
    # reason. Same anchor rule for the outbound forms below.
    ")<-",                 # an INBOUND hop, `(a)<--(b)`
    ")--(", ")-->",          # an UNDIRECTED / directed node-to-node hop
    # ⛔ `exists {` WAS AN ARM AND IS GONE (#14183 round-16 review, P3) — the SAME
    # argument as `size((` below, one arm over. First-match ordering puts `-[`,
    # `]->`, `)<-`, `)--(` and `)-->` BEFORE it, so every EDGE-bearing existence
    # predicate over an EXECUTABLE hop is decided earlier
    # (`EXISTS { (e)-[:TOUCHED]->(:Point) }` → `-[`, `EXISTS { (e)--(p) }` → `)--(`,
    # `EXISTS { (e)<--(p) }` → `)<-`), and the only shape left that reaches an
    # `exists {` arm is a NODE-existence test (`EXISTS { (n:Point) }`), which needs
    # no graph capability at all — while the comment here claimed "a
    # pattern-existence predicate over an EDGE". The two halves could not both be
    # true, and a row misfiled by it makes `readers_report()` publish "reader(s)
    # NEED a graph capability" over a predicate that walks nothing (measured:
    # node-existence → `cypher:exists {` before this change, `None` after; the
    # edge-existence spellings above are decided by their own hop arms). An arm
    # nothing can reach that only ever false-positives is not a spare.
    # ⚠️ A round-17 review proposed arms for the UNBRACKETED forms (`->(` and a
    # pattern for `)-ident-(`) and they were TRIED AND REVERTED: they are parse
    # errors on the pinned engine (see the block above), so they detect nothing a
    # reader can execute while the pattern false-positives on
    # `RETURN (total) - n - (subtotal)`.
    # ⛔ `size((` WAS AN ARM AND IS GONE (#14183 round-13 review, P3): it contributed
    # ZERO detection — every real adjacency aggregate carries a relationship inside
    # it, so `-[` or the hop arms decide first — while `RETURN size((collect(n)))`
    # (a legitimate grouping paren, no relationship anywhere) was misfiled as an
    # adjacency aggregate. The TEST MODULE's `ARMS_NO_SNIPPET_CAN_DECIDE`
    # (`tests/test_13094_graphevent_home.py`) had already said as much, and an arm
    # that only ever false-positives is not an arm. `size((` is gone from that set
    # too, so the partition pin still holds: an arm nothing can decide is not a
    # spare, it is a false-positive surface.
    "shortestpath",       # path search
    "call algo", "apoc.",  # a graph algorithm / path procedure
    "call db.idx.fulltext", "db.idx.fulltext",
    "call db.idx.vector", "db.idx.vector",
)

#: The audit's own vocabulary (the `#13083` census) — a graph engine's four capabilities
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
        # ⛔ WHITESPACE IS NOT A BYPASS. Every arm is a contiguous substring, so
        # `MATCH (e:GraphEvent) - [r:T]-> (p)` — spacing the engine ACCEPTS — looked
        # like no capability at all (#14183 review, non-blocking 6, measured). Both
        # sides are whitespace-stripped before matching, so a re-spaced pattern is
        # the same pattern.
        # ⛔ A COMMENT IS NOT A BYPASS EITHER (round 12). `-/* hop */[r:T]-` and
        # `-//hop\n[r:T]-` are engine-accepted and carry no contiguous arm, so the
        # snippet is stripped of comments, string literals and backtick quoting
        # FIRST — see `_LITERAL_COMMENT_OR_IDENT_RE` above for the measurement,
        # including the `//`-inside-a-backtick-identifier case that the canonical
        # write-gate regex alone does not cover.
        haystack = _NO_WS.sub("", _strip_noise(snippet.lower()))
        for shape in GRAPH_CAPABILITY_CYPHER:
            if _NO_WS.sub("", shape) in haystack:
                return f"cypher:{shape}"
    return None


def readers_report() -> dict[str, Any]:
    """The reader census, with the class counts — the PR's table, as data."""
    counts = Counter(reader["class"] for reader in READERS)
    # Every row, at every class — not only the `serving` ones. A capability
    # reader misfiled as `maintenance` is exactly the case this must surface,
    # and a filter on `class` cannot see it by construction.
    capability_readers = [
        {"file": r["file"], "line": r["line"], "reader": r["reader"],
         "capability": capability}
        for r in READERS if (capability := graph_capability(r))
    ]
    return {
        "readers": list(READERS),
        "counts": dict(counts),
        "graph_capability_readers": capability_readers,
        # ⛔ DERIVED, never a literal. A fixed sentence here would print a
        # reassuring "no reader needs a graph capability" in the same JSON as a
        # non-empty `graph_capability_readers` — and the evidence doc sources its
        # table from this verb, so the artifact would contradict itself.
        "verdict": (
            "no reader needs a graph capability — every reader is a "
            "key lookup, an ordered range scan, an aggregate, a predicate "
            "delete, or the schema those three depend on"
            if not capability_readers else
            f"{len(capability_readers)} reader(s) NEED a graph capability, so "
            "the shape is NOT a record_only candidate — re-open #13094's "
            "verdict: " + ", ".join(
                f"{r['reader']} ({r['capability']})" for r in capability_readers)
        ),
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
        # ⛔ THE GRAPH REFUSAL COMES FIRST (review round 3, P2): `_graph_rows`
        # runs `_require_graph`, one `GRAPH.LIST` command, while `record_rows` is
        # a full paginated scan of the tenant's journal. Reading the record first
        # meant a mistyped `--graph` paid for the whole journal BEFORE being
        # refused — and it made the ordering assertion in
        # `test_the_superset_verb_refuses_an_unknown_graph_too` a claim the code
        # contradicted.
        graph_rows_ = _graph_rows(_graph_connection(args.graph_uri), args.graph)
        conn = _record_connection(args.record_dsn, args.org_id)
        try:
            journal = record_rows(conn, args.org_id, args.graph_id)
        finally:
            conn.close()
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
        # ⛔ SAY WHICH SIDE IS EMPTY (review round 3, P2). The verb now refuses an
        # empty RECORD as well as an empty graph, and a fixed "the graph holds no
        # rows" sentence over a graph holding thousands sends the operator to
        # backfill the store that is fine — the same misdirection the evidence
        # doc forbids in the other direction.
        if not verdict["graph_events"]:
            print("the graph holds no `:GraphEvent` rows, so there is nothing to "
                  "compare — an empty or purged feed is NOT `covered`. NO VERDICT "
                  "was produced.", file=sys.stderr)
        else:
            print(f"the RECORD holds no rows for this scope while the graph "
                  f"holds {verdict['graph_events']} `:GraphEvent` row(s), so "
                  f"there is nothing to compare — a mistyped `--org-id`/"
                  f"`--graph-id`, a never-armed scope, or a record that lost "
                  f"this tenant is NOT `covered`. NO VERDICT was produced.",
                  file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
