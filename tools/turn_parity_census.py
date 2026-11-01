#!/usr/bin/env python3
"""The #10017 purge gate: an EXACT per-session parity census, graph vs record.

Read-only on BOTH stores. It never deletes, never writes, never issues a
`DELETE`/`DROP` of any kind — a purge executed from this tool is a bug, not a
feature. What it does is answer the ONE question #10017 gates the destructive
half on, per session, exactly:

    For every turn node the graph holds, does the record hold a row with the
    SAME key and the SAME transcript-bearing fields — so that deleting the
    graph copy loses nothing?

WHY ANOTHER CENSUS (`record_reconcile` exists)
==============================================
`record_reconcile` compares the record against the CACHE projection over node
KEYS, for drift observation (§17.2). This tool compares the GRAPH's turn nodes
against the RECORD's turn rows, FIELD BY FIELD, as a purge gate (#10017): key
equality alone would certify a purge over a record holding the right ids with
the WRONG text — the exact incident shape the issue exists to prevent. The two
instruments share a posture (refuse when a side cannot be read) and nothing
else.

THE POPULATION (what the purge would touch, and how it is identified)
=====================================================================
A **turn node** is the shape the capture writer mints
(`sdk._TURN_WRITE_CYPHER` / `_capture_turn_id`, the ONE derivation #3551):

    (:Point {id: '<session_id>_t<decimal index>', pointKind: 'event',
             is_episodic: true, is_operator: false, speaker, status,
             createdAt, content, content_hash, embedding?})
        <-[:CONTAINS]- (:Session {id: <session_id>})

The identification is STRUCTURAL — `is_episodic = true` AND `pointKind =
'event'` AND `id == session_id + '_t' + <all-decimal suffix>` for a
`CONTAINS`-wired `:Session` — the same rule `sdk._capture_turn_ids` applies
(suffix `isdecimal`, never prefix alone, so the `sec` vs `sec_t0` cross-match
cannot admit another session's turns). No timestamp is needed to date a node:
on an ARMED deployment the writer stops minting turn nodes (#10000), so turn
nodes present in an armed target's graph are pre-cutover by construction — and
the census does not need to know their age, only whether the record holds an
equivalent copy. Nodes that look like turns but fail any leg of the shape are
NOT purge candidates: they are reported as anomalies (orphans, non-decimal
suffixes, cross-wirings, `is_operator` flags) so nothing is silently invisible
to the gate — a census that quietly excluded what it could not classify would
be a number an owner decides on while looking at a smaller graph than exists.

THE READ IS THE DELETE SET, NOT JUST THE TURN SHAPE (#10017's own predicate)
===========================================================================
The purge predicate #10017 records — in its body, in its completion test and in
the `delete`-phase recipe on the issue — is `MATCH (t:Point) WHERE
is_episodic = true`, with **no** kind filter. `is_episodic` is NOT co-extensive
with "turn" in this repo: `graph-scripts/backfill_is_episodic.py` stamps it on
every `(s:Session)-[:CONTAINS]->(p:Point)` (its own docstring: "capture turn +
regex-extracted points"), and `create_point` / `update_point` accept the flag
on any point kind. So the graph leg READS every `is_episodic = true` node —
the delete set itself — and a node that is episodic but not turn-shaped gets
its own anomaly class (`non_turn_point_kind`), reported BY ID and refusing the
census. Reading only `pointKind = 'event'` would make the gate's population a
strict SUBSET of the set the purge deletes: those nodes would be in neither the
comparison nor the anomaly partition, which is exactly the "a smaller graph
than exists" failure above. The turn SHAPE still decides the purge set (what
`would_delete` counts); the wider READ decides what the census may call CLEAN.

**An anomaly REFUSES the census (exit 2).** Excluding a node from the purge set
is not the same as certifying it: the recorded sweep (`is_episodic = true`)
deletes those nodes too, and the census cannot claim parity over a node it
never compared. So `CLEAN` means *measured the whole
population and it all matched* — never *measured part of it and the part
matched*. The alternative (a distinct `CLEAN_WITH_ANOMALIES` exit) would put
the decision of what each anomaly class means back on whoever reads the exit
code; the operator resolves the anomaly, re-runs, and gets a real 0.

TOTALITY OF THE GRAPH READ (why the graph leg is paged — #5062's class)
======================================================================
FalkorDB applies a server-global `RESULTSET_SIZE` (default **10,000**) to a
query's result set, silently — it truncates, it does not error. The population
this gate exists for is the issue's own figure, **~20,000 turn nodes**, i.e.
twice the cap; a single unpaged `RETURN` would compare at most half of it and
report `CLEAN` over the rest. The graph leg is therefore keyset-paged on
`id(t)` (`>= $cursor`, cursor advanced to `last + 1` — the idiom and its
v6-core caveats are `tortoise/hosted_backup.py`'s `_paged_rows`, reused here
rather than re-derived), and the paged read is cross-checked against the
store's OWN aggregate `count(t)`, which is ONE row and therefore cap-immune
(`hosted_backup._independent_counts`, #4515). A shortfall REFUSES with exit 3:
the read is untrusted, so an absence is not established. Each page returns ONE
row per turn node (`collect(DISTINCT s.id)` for the wirings), so a page
boundary can never split a node's rows and lose a wiring.

THE PARITY DEFINITION (field by field — ambiguity here is how a purge becomes
an incident)
=============================================================================
For every in-population graph turn node, a record row (`public.point`,
`node_key = t.id`, `is_episodic IS TRUE`) must exist, and:

    COMPARED EXACTLY           graph                     record
    ─────────────────────────────────────────────────────────────
    key                        t.id                      node_key
    text (framed, as stored)   t.content                 content
    speaker                    t.speaker                 speaker
    kind                       t.pointKind ('event')     point_kind
    status                     t.status                  status
    createdAt                  t.createdAt               created_at
    embedding (when the graph holds one)  t.embedding    embedding

    `is_episodic` is the population PREDICATE on both sides (the graph leg
    reads `is_episodic = true`, the record leg filters `is_episodic IS TRUE`),
    so comparing it would be vacuous: it is not in `_EXACT_FIELDS` and must not
    be listed as a compared field here.

    ALLOWED TO DIFFER, WITH THE REASON
    ─────────────────────────────────────────────────────────────
    updatedAt                  t.updatedAt               (absent)  — the turn
                               snapshot (`PointAdded.point`) carries no
                               updatedAt, so record `updated_at` is NULL by
                               construction; it is write bookkeeping, not
                               transcript content.
    content_hash               t.content_hash            (no column) — DERIVED,
                               a pure function of content (#2795 D2), refused
                               by the record schema and recomputed on rebuild.
    is_operator                false                     (NULL)      — always
                               false on turns; a turn node carrying true is an
                               anomaly, not a parity field.

    EMBEDDING RULE. The graph stores `vecf32` (IEEE-754 binary32); the record
    stores the journal's float list as jsonb (binary64). Same length, and each
    record component equal to the graph's after rounding THROUGH binary32 —
    the storage the graph itself applies — is parity. `graph NULL + record
    present` PASSES (the record holds a superset; the purge loses nothing);
    `graph present + record absent/different` FAILS (the vector would be lost).

    COUNT RULE. Parity is `record ⊇ graph` per key with field equality — NOT
    count equality. A session captured before the cutover and APPENDED to
    after it holds graph nodes for the old window and record rows for the
    whole stream; deleting the graph copies loses nothing. Count equality is
    reported per session as information (it holds exactly when the session
    was never written post-cutover). The issue's "same count" precondition is
    the pre-cutover-frozen case of this rule.

    NO COUNTERPART. A graph turn node with NO record row FAILS the session —
    the answer is a backfill from the graph into the record (#10017's own
    ordering), never a delete. A record row with no graph node is expected
    (post-#10000 captures) and passes.

THE PASS CRITERION AND THE EXIT CODES
=====================================
    0  measured AND parity clean — every session holding in-population graph
       turn nodes has an exact record superset. The gate is OPEN (the purge
       may proceed through the issue's remaining ordering: scratch-graph
       restore proof, journaled delete, verify reads, byte census).
    1  usage / IO error (bad flags, unparseable args, unreadable --json path).
    2  parity FAIL — at least one session is missing record rows or holds a
       field mismatch, OR the graph holds a turn-shaped node the census could
       not place in the compared population (see the anomaly rule above).
       The gate REFUSES; the problems are listed per node and per field. Run
       the backfill / resolve the anomaly, then re-run this census.
    3  a side could not be READ (graph unreachable, record unreachable,
       tenant scope refused, graph read short of the store's own count). An
       absence is NOT established — the census refuses to certify parity
       over a side it could not see, or over part of one. This is the
       P0-shaped hole (#10024): an unscoped record read returns ZERO rows and
       would "pass" vacuously, so this tool reads through
       `record_projection.scope_connection` — the one prologue that verifies
       the tenant GUC stuck AND that the role cannot bypass RLS — and treats
       its refusal as exit 3, never as "no rows".

THE NEGATIVE ARM (how we know the gate refuses)
===============================================
A gate observed only passing is decoration. What CI pins, in
`tests/test_10017_turn_parity_census.py`, is the comparison core and the CLI
contract: a missing record row, a mismatched field, a lost/length-changed/
component-different vector and every anomaly class each REFUSE with the node
and field NAMED, over the two dictionaries the store legs produce — built by
hand, not read from live stores. A census that could not refuse those is
broken at the core, and that is what these cases hold.

The live end-to-end demonstration (a real capture, the real journal replayed
into a throwaway record, exit 0, then ONE record row removed and exit 2) is a
lane-run artifact on throwaway stores, NOT a CI test, so nothing here should
be read as proof that the live pair was measured. The STORE LEGS are pinned
separately and without stores — `read_record_turns`' scoped door, its SQL
column set and the refusal it raises are asserted against a stubbed
connection, and `read_graph_turns`' paging/count/classification against a fake
`g` — because an unpinned door is how an unscoped read (#10024) would certify
vacuously while every core case stayed green.

Nothing in this tool may be weakened to make a census pass.

USAGE
=====
    python tools/turn_parity_census.py \
        --uri 'docker://:@127.0.0.1:16398/tortoise_test_matrix' \
        --record-dsn 'postgresql://tortoise_record_app:...@host/db' \
        --org-id <org> --graph-id <graph> [--json out.json] [--sample 5] \
        [--expect-empty]

    --uri            the FalkorDB URI of the TARGET graph (or --db-path for
                     an embedded graph). Read-only queries only.
    --record-dsn     defaults to TORTOISE_RECORD_DSN. Must connect as a role
                     the tenant scope accepts (the app role) — a superuser/
                     owner DSN is refused with exit 3, because RLS would be
                     off and the read would mean less than it appears to.
    --org-id/--graph-id   the record tenant pair the deployment's writes file
                     under (env fallbacks TORTOISE_RECORD_ORG_ID /
                     TORTOISE_RECORD_GRAPH_ID). Never guessed: a census filed
                     under the wrong tenant compares the graph against an
                     empty record and must fail loudly (it does — every graph
                     node becomes missing_in_record), not silently.
    --expect-empty   allow a graph holding ZERO turn-shaped nodes to certify.
                     Without it that is exit 3: a drained graph and a wrong
                     --graph-name / --db-path are indistinguishable from the
                     read side, and the census does not guess which it saw.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first with an UNATTRIBUTED error.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/turn_parity_census.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/turn_parity_census.py`"
    )

import argparse
import contextlib
import json
import os
import re
import struct
from collections.abc import Iterable
from pathlib import Path

#: The all-decimal suffix rule (`sdk._capture_turn_ids` — `isdecimal`, never
#: prefix alone, so `sec` cannot claim `sec_t0_t3` and a `²`-class suffix is
#: excluded rather than unparseable).
_TURN_ID_RE = re.compile(r"^(?P<sid>.+)_t(?P<idx>[0-9]+)$")

#: Keyset page size for the graph leg. Must sit well UNDER FalkorDB's
#: `RESULTSET_SIZE` (default 10_000) so a page is never truncated; the
#: independent aggregate count below is the load-bearing guard (#5062's class).
_GRAPH_PAGE_SIZE = 1000

#: ONE row per episodic node: `collect(DISTINCT s.id)` collapses the
#: `CONTAINS` wirings, so a page boundary can never split a node's rows (with
#: a row-per-wiring shape, a node spanning the boundary would lose a wiring and
#: mis-classify a cross-wiring as a clean single-session turn). OPTIONAL MATCH
#: keeps EVERY episodic node in the result — including orphans (no CONTAINS
#: wiring) and cross-wirings — because a census that drops what it cannot
#: classify reports a smaller graph than exists. The predicate is the RECORDED
#: delete predicate (`is_episodic = true`, #10017), NOT the writer's narrower
#: turn shape: `pointKind` is read as a field and classified in Python
#: (`non_turn_point_kind`), so no node in the delete set is outside the read.
#: Paged on `id(t)` with the `>= $cursor` / `last + 1` idiom of
#: `hosted_backup._paged_rows`.
_GRAPH_EPISODIC_CYPHER = (
    "MATCH (t:Point) WHERE id(t) >= $cursor "
    "  AND t.is_episodic = true "
    "OPTIONAL MATCH (s:Session)-[:CONTAINS]->(t) "
    "RETURN id(t), t.id, t.content, t.speaker, t.status, t.createdAt, "
    "       t.pointKind, t.embedding, t.is_operator, collect(DISTINCT s.id) "
    "ORDER BY id(t) LIMIT $batch"
)

#: The STORE's own count — ONE row, so RESULTSET_SIZE cannot truncate it
#: (#4233/#4515). The reference the paged read must not fall short of: a
#: truncated page set is silence, not an error, so without this the census
#: would compare part of the population and call the whole of it clean. Same
#: predicate as the page query, or the cross-check would measure a different
#: set than the read.
_GRAPH_EPISODIC_COUNT_CYPHER = (
    "MATCH (t:Point) WHERE t.is_episodic = true "
    "RETURN count(t)"
)

#: The NARROWED read: the SAME row shape and the same `is_episodic` predicate,
#: selected by an exact `t.id` set. Exists so a destructive caller re-compares a
#: session's parity FIELDS at the moment of its delete (#10483 review, P2-1)
#: through this module's own read and comparison. Unpaged because its id set is
#: the caller's approved plan — and the store's own count is still compared
#: against the rows seen, because an unpaged narrow read is subject to
#: RESULTSET_SIZE exactly as a page is: a silently short one would report a
#: drifted turn as absent from the graph, or as never having drifted.
_GRAPH_EPISODIC_IDS_CYPHER = (
    "MATCH (t:Point) WHERE t.id IN $ids AND t.is_episodic = true "
    "OPTIONAL MATCH (s:Session)-[:CONTAINS]->(t) "
    "RETURN id(t), t.id, t.content, t.speaker, t.status, t.createdAt, "
    "       t.pointKind, t.embedding, t.is_operator, collect(DISTINCT s.id) "
    "ORDER BY id(t)"
)
_GRAPH_EPISODIC_IDS_COUNT_CYPHER = (
    "MATCH (t:Point) WHERE t.id IN $ids AND t.is_episodic = true "
    "RETURN count(t)"
)

#: The record census query's column set — ONE declaration, because the column
#: names are also the contract `read_record_turns` CHECKS against the cursor's
#: own `description`: a renamed/absent `public.point` column would otherwise
#: silently yield zero comparable rows and certify vacuously (the #10024 shape,
#: arriving through the record's schema instead of its scope).
_RECORD_TURNS_COLUMNS = ("node_key", "content", "speaker", "status",
                         "created_at", "embedding", "point_kind",
                         "is_episodic")

#: The record census query — the same predicate family `record_read._turns`
#: uses, corpus-wide: tenant-scoped, `is_episodic IS TRUE`, turn keys only
#: (the anchored shape is re-derived in Python, one rule for both stores).
#: The SELECT list is DERIVED from `_RECORD_TURNS_COLUMNS` and must stay that
#: way: a hand-spelled copy sitting below this one shadowed it (last assignment
#: wins), so the executing statement was no longer the one built from the
#: contract — editing the tuple would have left the query behind, selecting
#: columns the census no longer names. One declaration, and it is the binding.
_RECORD_TURNS_SQL = (
    "SELECT " + ", ".join(_RECORD_TURNS_COLUMNS) + " "
    "FROM public.point "
    "WHERE org_id = %s AND graph_id = %s AND is_episodic IS TRUE"
)

#: Fields compared exactly, as `(graph prop, record column, human name)`.
_EXACT_FIELDS = (
    ("content", "content", "text"),
    ("speaker", "speaker", "speaker"),
    ("pointKind", "point_kind", "pointKind"),
    ("status", "status", "status"),
    ("createdAt", "created_at", "createdAt"),
)


class CensusError(Exception):
    """A side could not be read — never a parity verdict (exit 3)."""


def _f32(value: float) -> float:
    """Round `value` through IEEE-754 binary32 — the graph's `vecf32` storage."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _embedding_parity(graph_emb, record_emb) -> tuple[bool, str]:
    """The embedding rule stated in the module docstring, as one verdict."""
    if graph_emb is None:
        return True, ""  # record superset: nothing on the graph is lost
    if record_emb is None:
        return False, "graph holds a vector, record holds none"
    if not isinstance(record_emb, (list, tuple)):
        return False, f"record embedding is {type(record_emb).__name__}, not a list"
    if len(record_emb) != len(graph_emb):
        return False, (f"vector length differs (graph {len(graph_emb)}, "
                       f"record {len(record_emb)})")
    for i, (g, r) in enumerate(zip(graph_emb, record_emb, strict=False)):
        try:
            record_component = _f32(float(r))
        except (TypeError, ValueError, OverflowError, struct.error):
            # `struct.pack('<f', 1e300)` raises OverflowError, and `float()`
            # raises it for an integer too large for binary64 — a field-VALUE
            # problem, so it is a MISMATCH (exit 2), never a crash that a
            # caller reads as a flag mistake (exit 1).
            return False, (f"component {i} is not a finite binary32 value "
                           f"in the record")
        if record_component != float(g):
            return False, f"component {i} differs after binary32 rounding"
    return True, ""


def _graph_vector(tid: str, embedding) -> list | None:
    """The graph's stored vector as floats — or a REFUSAL (``CensusError``).

    A `vecf32` read-back is NOT contractually a `list`: the repo's own sanitizer
    (`tortoise/embeddings.py::stamp_journal_embedding`, line 153-158, #5004
    round-3)
    accepts ANY non-string iterable precisely because "a graph read-back
    (`t.embedding` after a `vecf32` write, #5004 round-3) can arrive as a numpy
    array or a driver vector type". Accepting only `list`/`tuple` here refused
    those containers as unclassifiable (exit 3) — which means the census could
    never certify, and this gate could never open, on exactly the graphs where
    vector parity is the thing at risk. So the acceptance rule is the
    sanitizer's: refuse `str`/`bytes` (a sequence of CHARACTERS), `dict` (not a
    sequence of numbers) and anything not iterable at all; `list(...)`
    everything else. A component that still cannot become a float is a refusal,
    never a silent "no vector".
    """
    if embedding is None:
        return None
    if isinstance(embedding, (str, bytes, dict)):
        raise CensusError(
            f"turn {tid}: the graph embedding is "
            f"{type(embedding).__name__}, not a sequence of numbers — it "
            "cannot be classified, and an unclassifiable vector is a refusal, "
            "never a silent 'no vector'")
    try:
        items = list(embedding)
    except Exception as exc:
        # Any exception, not just `TypeError`: a container whose `__iter__`
        # raises anything else would otherwise escape as a traceback, i.e. the
        # exit-1 "usage/IO" code, where the contract is "unclassifiable side ->
        # exit 3". `BaseException` (KeyboardInterrupt/SystemExit) still
        # propagates.
        raise CensusError(
            f"turn {tid}: the graph embedding is "
            f"{type(embedding).__name__}, not a sequence — it cannot be "
            "classified, and an unclassifiable vector is a refusal, never a "
            "silent 'no vector'") from exc
    try:
        return [float(v) for v in items]
    except (TypeError, ValueError, OverflowError) as exc:
        # `OverflowError` for an integer past binary64's range — the record
        # leg already catches it (`_embedding_parity`); a value the leg
        # cannot classify is a refusal on BOTH sides, never a traceback a
        # caller reads as the exit-1 "bad flags" code.
        raise CensusError(
            f"turn {tid}: the graph embedding holds a non-numeric "
            f"component ({exc}) — an absence is NOT established") from exc


def _graph_rows(g) -> list:
    """Every row of the episodic query, keyset-paged on `id(t)`. READ-ONLY.

    A single unpaged `RETURN` is the #5062/#4515 shape: FalkorDB applies a
    server-global `RESULTSET_SIZE` (default 10_000) SILENTLY, and the
    population this gate exists for is ~20_000 turn nodes — so the read would
    stop half way and the census would certify the half it saw. The aggregate
    cross-check in :func:`read_graph_turns` is the second half of the guard.
    """
    cursor = 0
    rows: list = []
    while True:
        page = g.query(
            _GRAPH_EPISODIC_CYPHER,
            params={"cursor": cursor, "batch": _GRAPH_PAGE_SIZE},
        ).result_set
        if not page:
            return rows
        rows.extend(page)
        cursor = int(page[-1][0]) + 1
        if len(page) < _GRAPH_PAGE_SIZE:
            return rows


def read_graph_turns(uri: str | None, db_path: str | None,
                     graph_name: str | None, *,
                     node_ids: Iterable[str] | None = None,
                     g=None) -> dict:
    """The graph leg: every EPISODIC node (the delete set), classified.

    READ-ONLY. Returns ``{"nodes": {id: node-dict}, "unidentifiable": [...]}``.
    The read population is `is_episodic = true` — the predicate #10017 records
    for the purge — so a node that is episodic but not turn-shaped still comes
    back and is classified (`non_turn_point_kind`) instead of being invisible
    to a gate that would delete it. Raises :class:`CensusError` when the graph
    cannot be reached or answered, when the paged read comes back SHORT of the
    store's own aggregate count (RESULTSET_SIZE truncation — an absence would
    not be established), or when a row cannot be classified (an unparseable
    vector is a refusal, never a silent "no vector").

    ``node_ids`` narrows the read to an exact `t.id` set, the same shape and the
    same predicate, and exists so a DESTRUCTIVE caller can re-compare a
    session's parity FIELDS at the moment of its delete (#10483 review, P2-1)
    — through this module's one read and one comparison, never a second
    spelling of either. `read_record_turns(node_keys=…)` is the mirror on the
    record side; a re-verify that only re-checked KEY PRESENCE left the class
    this gate refuses at plan time (a field rewritten after the census) open at
    delete time. An empty `node_ids` reads nothing and answers nothing.

    ``g`` is an already-bound guarded write handle to read through, so a caller
    that is about to DELETE from a graph compares the store it deletes from
    (the `FalkorProjection` it already holds) rather than a second connection
    it hopes addresses the same one. `None` derives it from
    `uri`/`db_path`/`graph_name` exactly as before.
    """
    ids: list[str] | None = None
    if node_ids is not None:
        ids = [str(i) for i in node_ids]
        if not ids:
            return {"nodes": {}, "unidentifiable": []}
    if g is None:
        try:
            from tortoise.projection import FalkorProjection
        except Exception as exc:  # pragma: no cover - import env problem
            raise CensusError(
                f"tortoise import failed ({type(exc).__name__}: {exc})") from exc
        try:
            if uri:
                proj = FalkorProjection.from_uri(uri, graph_name=graph_name)
            else:
                # `--graph-name` is a TARGET selector, not a URI-only flag: the
                # embedded store selects the graph by name too. Dropping it
                # here censused the default `tortoise` graph while the operator
                # asked for another — a wrong-target read that still reported a
                # verdict.
                proj = FalkorProjection(db_path,
                                        graph_name=graph_name or "tortoise")
        except Exception as exc:
            raise CensusError(
                f"the graph could not be read ({type(exc).__name__}: {exc}) — "
                "an absence is NOT established") from exc
        g = proj.g
    try:
        if ids is None:
            rows = _graph_rows(g)
            total = int(g.query(_GRAPH_EPISODIC_COUNT_CYPHER).result_set[0][0])
        else:
            rows = g.query(_GRAPH_EPISODIC_IDS_CYPHER,
                           params={"ids": ids}).result_set
            # The same aggregate cross-check the paged read gets: a narrowed
            # read is still subject to RESULTSET_SIZE, and a silently short one
            # would report a turn as ABSENT FROM THE GRAPH (or as never having
            # drifted) on a truncated read.
            total = int(g.query(_GRAPH_EPISODIC_IDS_COUNT_CYPHER,
                                params={"ids": ids}).result_set[0][0])
    except CensusError:
        raise
    except Exception as exc:
        raise CensusError(
            f"the graph could not be read ({type(exc).__name__}: {exc}) — "
            "an absence is NOT established") from exc

    nodes, unidentifiable = _classify_graph_rows(rows)

    # Every node the store counts is either classified or reported as
    # unidentifiable; a shortfall beyond that is a torn/truncated read.
    seen = len(nodes) + len(unidentifiable)
    if seen < total:
        raise CensusError(
            f"the graph read returned {seen} but the store counts {total} "
            "episodic node(s) — the read was TRUNCATED (RESULTSET_SIZE) or "
            "torn. Refusing to certify parity over a population it did not "
            "see: an absence is NOT established")
    return {"nodes": nodes, "unidentifiable": unidentifiable}


def _classify_graph_rows(rows) -> tuple[dict, list[dict]]:
    """The graph rows as `{id: node-dict}` plus the unkeyable ones.

    ONE spelling of the row shape, shared by the full and the narrowed read: a
    second copy would drift from `_GRAPH_EPISODIC_CYPHER`'s column order and
    silently compare a session against the wrong fields.
    """
    nodes: dict[str, dict] = {}
    unidentifiable: list[dict] = []
    for row in rows or []:
        try:
            (_inner_id, tid, content, speaker, status, created_at, point_kind,
             embedding, is_operator, session_ids) = row[:10]
        except (TypeError, ValueError) as exc:
            raise CensusError(
                f"the graph returned a row of unexpected shape ({exc}) — "
                "an absence is NOT established") from exc
        if not isinstance(tid, str):
            # NOT dropped: a node with no usable key is still a node the purge
            # would delete, so it is reported under its own class instead of
            # vanishing from both `nodes` and `anomalies` — and instead of
            # masquerading as the TRUNCATED-read diagnosis the count guard
            # would otherwise produce.
            unidentifiable.append({"id": str(_inner_id),
                                   "id_type": type(tid).__name__})
            continue
        vec = _graph_vector(tid, embedding)
        entry = {
            "id": tid, "content": content, "speaker": speaker,
            "status": status, "createdAt": created_at,
            "pointKind": point_kind, "is_operator": is_operator,
            "embedding": vec,
            "sessions": set(),
        }
        for sid in session_ids or ():
            if sid is not None:
                entry["sessions"].add(sid)
        prior = nodes.get(tid)
        if prior is None:
            nodes[tid] = entry
        else:
            prior["sessions"] |= entry["sessions"]

    return nodes, unidentifiable


def read_record_turns(record_dsn: str, org_id: str,
                      graph_id: str,
                      node_keys: Iterable[str] | None = None) -> dict[str, dict]:
    """The record leg: turn rows for the tenant, keyed by `node_key`.

    The connection goes through the repo's own doors — ``record_serve.
    open_record`` (bounded, statement_timeout) then ``record_projection.
    scope_connection`` (the R6 tenant prologue that verifies the GUC stuck and
    REFUSES a role that can bypass RLS). An unscoped read is the #10024 P0:
    it returns zero rows and would certify vacuously; this leg cannot take
    that shape — the scope either holds or the census exits 3.

    ``node_keys`` narrows the read to an exact key set, and exists so a
    destructive caller can re-verify a SESSION's rows immediately before it
    deletes that session (#10483 review, P0 TOCTOU) **through this leg**
    rather than through a second statement of the record query. `None` (the
    census's own call) reads the whole tenant, unchanged.
    """
    # ⛔ AN EMPTY KEY SET IS AN EMPTY READ, AND IT COSTS NOTHING — not even a
    # connection. This decision sat BELOW `open_record` and returned past the
    # `try/finally` that closes it, so the one supported input that reads
    # nothing leaked a bounded record connection every time it was called
    # (#10483 review round 4, P2). Hoisted above the open it needs: with no
    # connection there is nothing to close, nothing to tenant-scope, and no
    # schema to assert about a page of rows that will never be fetched.
    # Normalised ONCE here, so a generator argument cannot be consumed by the
    # emptiness test and then read back as empty by the query.
    keys: list[str] | None = None
    if node_keys is not None:
        keys = [str(k) for k in node_keys]
        if not keys:
            return {"rows_by_key": {}, "non_turn_shaped": 0}
    try:
        import psycopg2  # noqa: F401 - availability probe, record's own driver
    except ImportError as exc:
        raise CensusError(
            f"psycopg2 is not installed ({exc}) — install the 'postgres' "
            "extra; the record cannot be read, so an absence is NOT "
            "established") from exc
    try:
        from tortoise import record_projection, record_serve
    except Exception as exc:  # an unimportable record leg is an unreadable side
        raise CensusError(
            f"the record's modules could not be imported "
            f"({type(exc).__name__}: {exc}) — the record cannot be read, so "
            "an absence is NOT established") from exc
    try:
        conn = record_serve.open_record(record_dsn)
    except Exception as exc:
        raise CensusError(
            f"the record could not be opened ({type(exc).__name__}: {exc}) — "
            "an absence is NOT established") from exc
    sql, params = _RECORD_TURNS_SQL, (org_id, graph_id)
    if keys is not None:
        sql = _RECORD_TURNS_SQL + " AND node_key = ANY(%s)"
        params = (org_id, graph_id, keys)
    try:
        cursor = record_projection.scope_connection(conn, org_id)
        cursor.execute(sql, params)
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
    except Exception as exc:
        raise CensusError(
            f"the record could not be read ({type(exc).__name__}: {exc}) — "
            "an absence is NOT established") from exc
    finally:
        with contextlib.suppress(Exception):  # best-effort close
            conn.close()

    # The schema contract: a renamed or dropped `public.point` column would
    # leave every row unkeyed, i.e. an EMPTY comparison that certifies parity
    # vacuously — the #10024 shape arriving through the record's schema instead
    # of its scope. Refuse (exit 3) instead of reading it as "no rows".
    missing = [c for c in _RECORD_TURNS_COLUMNS if c not in columns]
    if missing:
        raise CensusError(
            f"the record query did not return {', '.join(missing)} — the "
            "`public.point` schema the census depends on has moved, so an "
            "absence is NOT established (got: "
            f"{', '.join(str(c) for c in columns)})")

    rows_by_key: dict[str, dict] = {}
    non_turn_shaped = 0
    for row in rows:
        rec = dict(zip(columns, row, strict=False))
        key = rec.get("node_key")
        if not isinstance(key, str) or _TURN_ID_RE.match(key) is None:
            non_turn_shaped += 1
            continue
        rows_by_key[key] = rec
    return {"rows_by_key": rows_by_key, "non_turn_shaped": non_turn_shaped}


def census(graph: dict, record: dict, *, sample: int) -> dict:
    """The comparison and the classification. Pure: no I/O, no verdict I/O."""
    nodes, rows = graph["nodes"], record["rows_by_key"]
    unidentifiable = graph.get("unidentifiable") or []

    # The anomaly partition: turn-shaped nodes the deterministic id space
    # cannot place are NEVER purge candidates, and NEVER invisible — each is
    # reported under the class that excluded it.
    anomalies: dict[str, list[dict]] = {
        "non_decimal_suffix": [], "orphan": [], "cross_wired": [],
        "operator_flagged": [], "session_mismatch": [],
        "non_turn_point_kind": [], "non_string_id": [],
    }
    # A node the read could not key is still a node the purge would delete, so
    # it is REPORTED (under its own class) rather than vanishing between
    # `nodes` and `anomalies`. `read_graph_turns` carries them separately
    # because they have no `t.id` to be a dict key.
    for entry in graph.get("unidentifiable") or ():
        anomalies["non_string_id"].append(entry)
    for tid, node in sorted(nodes.items()):
        if node.get("pointKind") != "event":
            # IN THE DELETE SET (`is_episodic = true`, #10017's predicate) but
            # NOT a turn: `graph-scripts/backfill_is_episodic.py` stamps the
            # flag on regex-extracted points too, and `create_point`/
            # `update_point` accept it on any kind. Nothing compared it, so a
            # sweep driven by the recorded predicate would delete it on this
            # gate's word — its own anomaly class, by id, refusing the census.
            anomalies["non_turn_point_kind"].append(
                {"id": tid, "pointKind": node.get("pointKind")})
            continue
        if node.get("is_operator"):
            anomalies["operator_flagged"].append(
                {"id": tid, "is_operator": node["is_operator"]})
        derived = _TURN_ID_RE.match(tid)
        if derived is None:
            anomalies["non_decimal_suffix"].append({"id": tid})
        sids = node["sessions"]
        if not sids:
            anomalies["orphan"].append({
                "id": tid,
                "derived_session": derived.group("sid") if derived else None,
            })
        elif len(sids) > 1:
            anomalies["cross_wired"].append(
                {"id": tid, "sessions": sorted(sids)})
        elif derived is not None and next(iter(sids)) != derived.group("sid"):
            # Wired to a Session the id does not name — the capture writer
            # never produces this (it MERGEs CONTAINS from the true session),
            # so an external writer or a rename did. Excluded from the purge
            # set and REPORTED, never silently dropped from the census.
            anomalies["session_mismatch"].append({
                "id": tid, "wired_session": sorted(sids)[0],
                "derived_session": derived.group("sid"),
            })

    sessions: dict[str, dict] = {}
    for tid, node in sorted(nodes.items()):
        sids = node["sessions"]
        # In-population: wired to exactly one Session whose id + '_t<digits>'
        # IS this node's id (the `_capture_turn_ids` shape rule, one session),
        # and NOT flagged `is_operator` (the docstring: "a turn node carrying
        # true is an anomaly, not a parity field" — counting it as purgeable
        # while the anomaly line says it is excluded is a self-contradiction
        # in one report).
        in_population = False
        if (len(sids) == 1 and not node.get("is_operator")
                and node.get("pointKind") == "event"):
            sid = next(iter(sids))
            m = _TURN_ID_RE.match(tid)
            in_population = m is not None and m.group("sid") == sid
        if in_population:
            sessions.setdefault(sid, {"nodes": {}})["nodes"][tid] = node

    # Grouped ONCE: the per-session record count used to re-scan every record
    # row per session (O(sessions x rows) regex matches after the whole tenant
    # was already materialised). Same numbers, no quadratic term.
    rows_by_session: dict[str, int] = {}
    for key in rows:
        match = _TURN_ID_RE.match(key)
        if match is not None:
            rows_by_session[match.group("sid")] = (
                rows_by_session.get(match.group("sid"), 0) + 1)

    clean: list[dict] = []
    failed: list[dict] = []
    for sid in sorted(sessions):
        problems: list[dict] = []
        for tid, node in sorted(sessions[sid]["nodes"].items()):
            rec = rows.get(tid)
            if rec is None:
                problems.append({
                    "node": tid, "field": "key",
                    "problem": "missing_in_record",
                    "graph": {"id": tid},
                    "record": None,
                })
                continue
            for gprop, rcol, human in _EXACT_FIELDS:
                gval, rval = node.get(gprop), rec.get(rcol)
                if gval is None and rval is None:
                    continue
                if gval != rval:
                    problems.append({
                        "node": tid, "field": human,
                        "problem": "field_mismatch",
                        "graph": gval, "record": rval,
                    })
            ok, why = _embedding_parity(node.get("embedding"),
                                        rec.get("embedding"))
            if not ok:
                problems.append({
                    "node": tid, "field": "embedding",
                    "problem": "field_mismatch", "detail": why,
                })
        record_rows_for_session = rows_by_session.get(sid, 0)
        graph_count = len(sessions[sid]["nodes"])
        record_count = record_rows_for_session
        verdict = {"session": sid, "graph_turn_nodes": graph_count,
                   "record_turn_rows": record_count,
                   "counts_equal": graph_count == record_count}
        if problems:
            verdict["problems"] = problems
            failed.append(verdict)
        else:
            clean.append(verdict)

    # `would_delete_*` is the PURGE SET among CLEAN sessions — what the gate
    # would actually permit. `held_back_*` is the mirror: turn nodes in a
    # REFUSED session, which are in the shape-derived purge population but are
    # NOT eligible (nothing compared them), so a REFUSED run cannot read as
    # "there is nothing to delete".
    would_delete = sum(v["graph_turn_nodes"] for v in clean)
    held_back = sum(v["graph_turn_nodes"] for v in failed)
    sample_out = []
    for v in clean:
        sids_nodes = sorted(sessions[v["session"]]["nodes"])
        sample_out.append({
            "session": v["session"], "count": v["graph_turn_nodes"],
            "sample": [
                {"id": tid,
                 "speaker": sessions[v["session"]]["nodes"][tid].get("speaker"),
                 "createdAt": sessions[v["session"]]["nodes"][tid].get("createdAt"),
                 # A 60-CHARACTER HEAD of the transcript, deliberately — the
                 # operator's eyeball comparison for a sampled turn. It is not
                 # "an identifying field" and this artifact is not
                 # content-free: the FAILURE evidence above (`graph`/`record`
                 # values) carries the full text of a mismatched turn, which a
                 # parity gate cannot do its job without. The whole transcript
                 # is never emitted for any turn.
                 "content_head": (str(
                     sessions[v["session"]]["nodes"][tid].get("content")
                     or "")[:60])}
                for tid in sids_nodes[:sample]],
        })

    anomaly_ids = sorted({a["id"] for entries in anomalies.values()
                          for a in entries})
    return {
        "population": {
            # The read population is the DELETE SET (`is_episodic = true`).
            # `turn_shaped_nodes` is the TURN SHAPE inside it — NOT the purge
            # set: a turn-shaped node can still be excluded by the anomaly
            # partition (orphan, cross_wired, session_mismatch,
            # operator_flagged), and the suite pins that divergence. The purge
            # set is `dry_run.would_delete_turn_nodes` plus
            # `dry_run.held_back_turn_nodes`.
            # The DELETE SET, so it counts every node the read returned —
            # including the ones with no usable key, which carry no `t.id` to
            # be a dict key. Counting `nodes` alone understated the population
            # the label names (an unkeyable node is still in the sweep).
            "episodic_graph_nodes": len(nodes) + len(unidentifiable),
            "turn_shaped_nodes": sum(1 for n in nodes.values()
                                     if n.get("pointKind") == "event"),
            "sessions_with_graph_turns": len(sessions),
            "outside_population": len(anomaly_ids),
        },
        "record": {"turn_rows_total": len(rows),
                   "episodic_rows_not_turn_shaped":
                       record["non_turn_shaped"]},
        "parity": {
            "clean_sessions": clean,
            "failed_sessions": failed,
        },
        "anomalies": anomalies,
        "dry_run": {
            "would_delete_turn_nodes": would_delete,
            "held_back_turn_nodes": held_back,
            "blocked_sessions": [v["session"] for v in failed],
            "per_session_sample": sample_out,
            "note": "READ-ONLY dry run: this tool never deletes. The purge is "
                    "a separate, explicitly-gated operation (#10017 ordering).",
            "would_delete_ids": [tid for v in clean
                                 for tid in sorted(
                                     sessions[v["session"]]["nodes"])],
        },
        # CLEAN = measured the WHOLE population and it all matched. An anomaly
        # is a turn-shaped node the census could not place, so a shape-based
        # sweep would delete a node nothing was compared against: that is a
        # refusal (exit 2), not a footnote on a pass.
        "verdict": ("CLEAN" if not failed and not anomaly_ids else "REFUSED"),
        "refused_for": (["parity"] if failed else []) +
                       (["outside_population"] if anomaly_ids else []),
    }


class _UsageParser(argparse.ArgumentParser):
    """Usage errors exit ``1`` — ``2`` is reserved for a REFUSED census.

    argparse's default is ``2``, which would make "you forgot --record-dsn"
    indistinguishable from "the record is missing turns" to any caller keying
    on the exit code (the same rule ``tools/graph_census.py`` states).
    """

    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = _UsageParser(
        prog="turn_parity_census",
        description="The #10017 purge gate: exact per-session parity census, "
                    "graph turn nodes vs record turn rows. Read-only.",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--uri", help="FalkorDB URI of the target graph")
    src.add_argument("--db-path", help="embedded graph path instead of a URI")
    parser.add_argument("--graph-name", default=None,
                        help="the graph inside the store (default: the URI's, "
                             "or 'tortoise' for --db-path); applies to BOTH "
                             "legs")
    parser.add_argument("--record-dsn", default=None,
                        help="record DSN (default: TORTOISE_RECORD_DSN)")
    parser.add_argument("--org-id", default=os.environ.get(
        "TORTOISE_RECORD_ORG_ID", ""), help="record tenant org")
    parser.add_argument("--graph-id", default=os.environ.get(
        "TORTOISE_RECORD_GRAPH_ID", ""), help="record tenant graph")
    parser.add_argument("--json", default=None,
                        help="write the full report to this path")
    parser.add_argument("--sample", type=int, default=5,
                        help="sample size per session in the dry-run report")
    parser.add_argument("--expect-empty", action="store_true",
                        help="allow a graph holding ZERO is_episodic nodes to "
                             "certify; without it that is exit 3 (a drained "
                             "graph and a wrong --graph-name look identical "
                             "from here)")
    args = parser.parse_args(argv)

    if args.sample < 0:
        parser.error("--sample must be >= 0")

    record_dsn = args.record_dsn or os.environ.get("TORTOISE_RECORD_DSN", "")
    if not record_dsn:
        sys.stderr.write("turn_parity_census: no record DSN — pass "
                         "--record-dsn or set TORTOISE_RECORD_DSN. The record "
                         "cannot be read, so an absence is NOT established.\n")
        return 1
    if not args.org_id or not args.graph_id:
        sys.stderr.write(
            "turn_parity_census: --org-id/--graph-id (or "
            "TORTOISE_RECORD_ORG_ID/TORTOISE_RECORD_GRAPH_ID) are required — "
            "the census must be filed under the deployment's real tenant, "
            "never a guess.\n")
        return 1

    try:
        graph = read_graph_turns(args.uri, args.db_path, args.graph_name)
        record = read_record_turns(record_dsn, args.org_id, args.graph_id)
    except CensusError as exc:
        sys.stderr.write(f"turn_parity_census: {exc}\n")
        return 3

    # Zero episodic nodes is not evidence of parity — it is the same answer a
    # wrong --graph-name, or a --db-path typo that opens a fresh empty DB, gives
    # (`FalkorProjection.from_uri` does not verify the graph exists). The record
    # axis refuses a wrong tenant loudly; the graph axis must refuse a wrong
    # target too, so an empty graph is exit 3 unless the operator says the
    # draining is expected (#10017's post-purge re-run).
    # ... and `unidentifiable` is part of that population: a graph whose
    # episodic nodes ALL came back without a usable `t.id` is not an EMPTY
    # graph, and reporting it as "ZERO is_episodic nodes" would be factually
    # wrong about the target. Those nodes are `non_string_id` anomalies, which
    # REFUSE the census below (exit 2) — the accurate verdict, and a message
    # the operator can act on.
    if (not graph["nodes"] and not graph.get("unidentifiable")
            and not args.expect_empty):
        sys.stderr.write(
            "turn_parity_census: the graph holds ZERO is_episodic nodes — an "
            "absence is NOT established (a wrong --graph-name / --db-path is "
            "indistinguishable from a drained graph). Pass --expect-empty if "
            "the graph is known to hold no turn nodes.\n")
        return 3

    report = census(graph, record, sample=args.sample)
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8")

    pop = report["population"]
    print(f"population : {pop['episodic_graph_nodes']} episodic graph node(s) "
          f"(the delete set), {pop['turn_shaped_nodes']} of them turn-shaped, "
          f"across {pop['sessions_with_graph_turns']} session(s)")
    print(f"record     : {report['record']['turn_rows_total']} turn rows "
          f"({report['record']['episodic_rows_not_turn_shaped']} episodic "
          f"rows not turn-shaped)")
    for cls, entries in report["anomalies"].items():
        if entries:
            print(f"anomaly    : {len(entries)} {cls} "
                  f"(OUTSIDE the compared population — reported, never "
                  f"deleted by this tool)")
            for entry in entries[:5]:
                print(f"  OUTSIDE  {cls}: {entry['id']}")
    print(f"parity     : {len(report['parity']['clean_sessions'])} session(s) "
          f"CLEAN, {len(report['parity']['failed_sessions'])} FAILED")
    for v in report["parity"]["failed_sessions"]:
        head = ", ".join(f"{p['node']}:{p['field']}({p['problem']})"
                         for p in v["problems"][:3])
        more = "" if len(v["problems"]) <= 3 else f" +{len(v['problems'])-3} more"
        print(f"  REFUSED  session {v['session']}: {head}{more}")
    print(f"dry run    : would delete {report['dry_run']['would_delete_turn_nodes']} "
          f"turn node(s), in {len(report['parity']['clean_sessions'])} CLEAN "
          f"session(s) (READ-ONLY — this tool never deletes)")
    if report["dry_run"]["held_back_turn_nodes"]:
        print(f"held back  : {report['dry_run']['held_back_turn_nodes']} turn "
              f"node(s) in {len(report['parity']['failed_sessions'])} REFUSED "
              f"session(s) are in the delete set but NOT eligible — nothing "
              f"compared them")
    if report.get("refused_for"):
        print(f"refused    : {', '.join(report['refused_for'])}")
    print(f"verdict    : {report['verdict']}")

    return 0 if report["verdict"] == "CLEAN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
