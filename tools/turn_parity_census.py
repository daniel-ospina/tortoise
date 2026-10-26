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
    episodic                   t.is_episodic (true)      is_episodic
    status                     t.status                  status
    createdAt                  t.createdAt               created_at
    embedding (when the graph holds one)  t.embedding    embedding

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
       field mismatch. The gate REFUSES; the problems are listed per node and
       per field. Run the backfill, then re-run this census.
    3  a side could not be READ (graph unreachable, record unreachable,
       tenant scope refused). An absence is NOT established — the census
       refuses to certify parity over a side it could not see. This is the
       P0-shaped hole (#10024): an unscoped record read returns ZERO rows and
       would "pass" vacuously, so this tool reads through
       `record_projection.scope_connection` — the one prologue that verifies
       the tenant GUC stuck AND that the role cannot bypass RLS — and treats
       its refusal as exit 3, never as "no rows".

THE NEGATIVE ARM (how we know the gate refuses)
===============================================
A gate observed only passing is decoration. The demonstration lives in
`tests/test_10017_turn_parity_census.py`: it builds a throwaway graph+record
pair, replays the real journal into the record, asserts exit 0, then removes
ONE turn row from the throwaway record and asserts exit 2 with the missing
key named — and tampers one content and asserts the field mismatch is named.
Nothing in this tool may be weakened to make a census pass.

USAGE
=====
    python tools/turn_parity_census.py \
        --uri 'docker://:@127.0.0.1:16398/tortoise_test_matrix' \
        --record-dsn 'postgresql://tortoise_record_app:...@host/db' \
        --org-id <org> --graph-id <graph> [--json out.json] [--sample 5]

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
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import struct
import sys
from pathlib import Path

# #5128: refuse a <3.12 interpreter before the imports below.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    sys.stderr.write("turn_parity_census: Python >= 3.12 required "
                     f"(running {sys.version.split()[0]})\n")
    raise SystemExit(1)

#: The all-decimal suffix rule (`sdk._capture_turn_ids` — `isdecimal`, never
#: prefix alone, so `sec` cannot claim `sec_t0_t3` and a `²`-class suffix is
#: excluded rather than unparseable).
_TURN_ID_RE = re.compile(r"^(?P<sid>.+)_t(?P<idx>[0-9]+)$")

#: The graph census query. OPTIONAL MATCH keeps EVERY turn-shaped node in the
#: result — including orphans (no CONTAINS wiring) and cross-wirings — because
#: a census that drops what it cannot classify reports a smaller graph than
#: exists. `is_episodic = true AND pointKind = 'event'` is the writer's own
#: turn predicate; the id shape is re-derived in Python where the suffix rule
#: lives.
_GRAPH_TURNS_CYPHER = (
    "MATCH (t:Point) "
    "WHERE t.is_episodic = true AND t.pointKind = 'event' "
    "OPTIONAL MATCH (s:Session)-[:CONTAINS]->(t) "
    "RETURN t.id, t.content, t.speaker, t.status, t.createdAt, "
    "       t.pointKind, t.embedding, t.is_operator, s.id"
)

#: The record census query — the same predicate family `record_read._turns`
#: uses, corpus-wide: tenant-scoped, `is_episodic IS TRUE`, turn keys only
#: (the anchored shape is re-derived in Python, one rule for both stores).
_RECORD_TURNS_SQL = (
    "SELECT node_key, content, speaker, status, created_at, embedding, "
    "       point_kind, is_episodic "
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
            if _f32(float(r)) != float(g):
                return False, f"component {i} differs after binary32 rounding"
        except (TypeError, ValueError):
            return False, f"component {i} is not numeric in the record"
    return True, ""


def read_graph_turns(uri: str | None, db_path: str | None,
                     graph_name: str | None) -> dict[str, dict]:
    """The graph leg: every turn-shaped node, classified. READ-ONLY.

    Returns ``{"nodes": {id: node-dict}, "anomalies": {class: [entry]}}``.
    Raises :class:`CensusError` when the graph cannot be reached or answered.
    """
    try:
        from tortoise.projection import FalkorProjection
    except Exception as exc:  # pragma: no cover - import env problem
        raise CensusError(
            f"tortoise import failed ({type(exc).__name__}: {exc})") from exc
    try:
        if uri:
            proj = FalkorProjection.from_uri(uri, graph_name=graph_name)
        else:
            proj = FalkorProjection(db_path)
        rows = proj.g.query(_GRAPH_TURNS_CYPHER).result_set
    except Exception as exc:
        raise CensusError(
            f"the graph could not be read ({type(exc).__name__}: {exc}) — "
            "an absence is NOT established") from exc

    nodes: dict[str, dict] = {}
    for row in rows or []:
        (tid, content, speaker, status, created_at, point_kind, embedding,
         is_operator, sid) = row[:9]
        if not isinstance(tid, str):
            continue  # not identifiable; the shape rule is str-only by design
        entry = {
            "id": tid, "content": content, "speaker": speaker,
            "status": status, "createdAt": created_at,
            "pointKind": point_kind, "is_operator": is_operator,
            "embedding": ([float(v) for v in embedding]
                          if isinstance(embedding, (list, tuple)) else None),
            "sessions": set(),
        }
        if sid is not None:
            entry["sessions"].add(sid)
        prior = nodes.get(tid)
        if prior is None:
            nodes[tid] = entry
        else:
            prior["sessions"] |= entry["sessions"]
    return {"nodes": nodes}


def read_record_turns(record_dsn: str, org_id: str,
                      graph_id: str) -> dict[str, dict]:
    """The record leg: every turn row for the tenant, grouped by session.

    The connection goes through the repo's own doors — ``record_serve.
    open_record`` (bounded, statement_timeout) then ``record_projection.
    scope_connection`` (the R6 tenant prologue that verifies the GUC stuck and
    REFUSES a role that can bypass RLS). An unscoped read is the #10024 P0:
    it returns zero rows and would certify vacuously; this leg cannot take
    that shape — the scope either holds or the census exits 3.
    """
    try:
        import psycopg2  # noqa: F401 - availability probe, record's own driver
    except ImportError as exc:
        raise CensusError(
            f"psycopg2 is not installed ({exc}) — install the 'postgres' "
            "extra; the record cannot be read, so an absence is NOT "
            "established") from exc
    from tortoise import record_projection, record_serve
    try:
        conn = record_serve.open_record(record_dsn)
    except Exception as exc:
        raise CensusError(
            f"the record could not be opened ({type(exc).__name__}: {exc}) — "
            "an absence is NOT established") from exc
    try:
        cursor = record_projection.scope_connection(conn, org_id)
        cursor.execute(_RECORD_TURNS_SQL, (org_id, graph_id))
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
    except Exception as exc:
        raise CensusError(
            f"the record could not be read ({type(exc).__name__}: {exc}) — "
            "an absence is NOT established") from exc
    finally:
        with contextlib.suppress(Exception):  # best-effort close
            conn.close()

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

    # The anomaly partition: turn-shaped nodes the deterministic id space
    # cannot place are NEVER purge candidates, and NEVER invisible — each is
    # reported under the class that excluded it.
    anomalies: dict[str, list[dict]] = {
        "non_decimal_suffix": [], "orphan": [], "cross_wired": [],
        "operator_flagged": [], "session_mismatch": [],
    }
    for tid, node in sorted(nodes.items()):
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
        # IS this node's id (the `_capture_turn_ids` shape rule, one session).
        in_population = False
        if len(sids) == 1:
            sid = next(iter(sids))
            m = _TURN_ID_RE.match(tid)
            in_population = m is not None and m.group("sid") == sid
        if in_population:
            sessions.setdefault(sid, {"nodes": {}})["nodes"][tid] = node

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
        record_rows_for_session = {
            k for k in rows
            if (m := _TURN_ID_RE.match(k)) is not None and m.group("sid") == sid}
        graph_count = len(sessions[sid]["nodes"])
        record_count = len(record_rows_for_session)
        verdict = {"session": sid, "graph_turn_nodes": graph_count,
                   "record_turn_rows": record_count,
                   "counts_equal": graph_count == record_count}
        if problems:
            verdict["problems"] = problems
            failed.append(verdict)
        else:
            clean.append(verdict)

    would_delete = sum(v["graph_turn_nodes"] for v in clean)
    sample_out = []
    for v in clean:
        sids_nodes = sorted(sessions[v["session"]]["nodes"])
        sample_out.append({
            "session": v["session"], "count": v["graph_turn_nodes"],
            "sample": [
                {"id": tid,
                 "speaker": sessions[v["session"]]["nodes"][tid].get("speaker"),
                 "createdAt": sessions[v["session"]]["nodes"][tid].get("createdAt"),
                 "content_head": (str(
                     sessions[v["session"]]["nodes"][tid].get("content")
                     or "")[:60])}
                for tid in sids_nodes[:sample]],
        })

    return {
        "population": {
            "turn_shaped_nodes": len(nodes),
            "sessions_with_graph_turns": len(sessions),
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
            "blocked_sessions": [v["session"] for v in failed],
            "per_session_sample": sample_out,
            "note": "READ-ONLY dry run: this tool never deletes. The purge is "
                    "a separate, explicitly-gated operation (#10017 ordering).",
        },
        "verdict": "CLEAN" if not failed else "REFUSED",
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
                        help="the graph inside the store (default: the URI's)")
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

    report = census(graph, record, sample=args.sample)
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8")

    pop = report["population"]
    print(f"population : {pop['turn_shaped_nodes']} turn-shaped graph nodes "
          f"across {pop['sessions_with_graph_turns']} session(s)")
    print(f"record     : {report['record']['turn_rows_total']} turn rows "
          f"({report['record']['episodic_rows_not_turn_shaped']} episodic "
          f"rows not turn-shaped)")
    for cls, entries in report["anomalies"].items():
        if entries:
            print(f"anomaly    : {len(entries)} {cls} "
                  f"(excluded from the purge set, reported, not deleted)")
    print(f"parity     : {len(report['parity']['clean_sessions'])} session(s) "
          f"CLEAN, {len(report['parity']['failed_sessions'])} FAILED")
    for v in report["parity"]["failed_sessions"]:
        head = ", ".join(f"{p['node']}:{p['field']}({p['problem']})"
                         for p in v["problems"][:3])
        more = "" if len(v["problems"]) <= 3 else f" +{len(v['problems'])-3} more"
        print(f"  REFUSED  session {v['session']}: {head}{more}")
    print(f"dry run    : would delete {report['dry_run']['would_delete_turn_nodes']} "
          f"turn node(s) across {len(report['parity']['clean_sessions'])} session(s) "
          f"(READ-ONLY — this tool never deletes)")
    print(f"verdict    : {report['verdict']}")

    return 0 if report["verdict"] == "CLEAN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
