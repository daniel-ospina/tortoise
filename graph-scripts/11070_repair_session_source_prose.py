#!/usr/bin/env python3
"""Operator path for #11070: clear the PRE-#10852 session ``:Source`` prose pair.

A capture that ran before #10852 wrote the transcript's first substantive
utterance into ``Source.summary`` and derived ``_searchText`` from it. Nothing
heals those nodes (#10852's own PR body states why), so this is the repair, and
it runs the SAME reconciliation the whole-journal replay engines run at the end
of their trailing sweep (``tortoise.session_source_prose_migration.
reconcile_session_source_prose``) — one implementation, so the operator path and
the REPLAY path cannot drift.

⛔ A REPLAY DOES NOT RESTORE THIS REPAIR. A replay engine holds no record handle,
so there the hook is PLAN-ONLY: it counts the resurrected population and writes
nothing (clearing there would un-index every pre-#10852 session irreversibly and
then report census 0 — a false PASS). After ANY whole-journal replay (``rebuild``,
``rebuild_all``, ``recover_from_log``, ``backup.restore``) RE-RUN this script with
``--record-dsn``. The journal is reconstruction + audit, never durability
(``tortoise/log.py``).

⚠️ THE CENSUS THIS SCRIPT PRINTS IS NOT #11070's STATED PREDICATE, and that is
the point: ``is_episodic = true AND (summary <> '' OR _searchText IS NOT NULL)``
can never reach zero, because ``is_episodic`` is stamped on DOCUMENTS (whose
``summary``/``_searchText`` are legitimate) and a POST-fix node carries a
non-empty topic lexicon by design. The predicate used here is the session-Source
IDENTITY plus the display-prefix WITNESS — see the module docstring.

Usage:
  # 1) census + plan, no writes (the default). ⚠️ NO RECORD SCOPE IS NEEDED:
  #    the census reads the CACHE, and consults the record ONLY if you give it a
  #    scope (then its "re-derived" count is a PLAN, not work done) —
  #    but the CACHE GRAPH still has to be named (see step 2), or the census counts
  #    a different graph and reports 0 candidates with confidence.
  uv run python graph-scripts/11070_repair_session_source_prose.py \\
      --graph org_acme_tortoise --dry-run

  # 2) repair, re-deriving each lexicon FROM THE RECORD (the only arm that
  #    needs the scope). ⛔ PASS --graph: without it the CACHE graph is
  #    `derive_uri_graph($TORTOISE_DB_URI)`, which on a hosted/multi-graph box is
  #    NOT the graph `--graph-id` names — the run reads one graph, usually finds
  #    0 candidates, prints `Repair complete.` and exits 0, over the tenant graph
  #    that still holds the population (#11152 review round 4). `--graph` is the
  #    CACHE graph; `--org-id`/`--graph-id` are the RECORD scope. On a
  #    single-graph self-host they often share a name — never assume it, and
  #    read the `graph:` line every run prints.
  uv run python graph-scripts/11070_repair_session_source_prose.py \\
      --repair --graph org_acme_tortoise \\
      --record-dsn "$TORTOISE_RECORD_DSN" \\
      --org-id org_acme --graph-id org_acme_tortoise

  # 3) verify: the census must be 0 — `--assert-clean` is what can FAIL
  #    (same `--graph` as the repair, or it verifies a DIFFERENT graph)
  uv run python graph-scripts/11070_repair_session_source_prose.py \\
      --graph org_acme_tortoise --dry-run --assert-clean || exit 1

⚠️ THE RECORD SCOPE IS NEVER GUESSED. ``--record-dsn`` on the WRITING arm
(``--repair``) requires ``--org-id`` and ``--graph-id``: a wrong (or defaulted)
scope is NOT an error at the record — the row-level security returns no rows, so
every session reads as "the record did not answer". The sibling verbs refuse to
derive a scope for exactly this reason (``tortoise/__main__.py``: "No default is
derived — a wrong guess opens an EMPTY graph"), and so does this one.

⚠️ AND THE REQUIREMENT SITS ON THE ARM THAT CAN DESTROY SOMETHING. A census never
consults the record for anything destructive, and ``--assert-clean`` is the
VERIFICATION step — a verification step that refuses to run because a DSN
happens to be exported is a verification that cannot fail. Requiring a scope on
every arm made steps 1 and 3 unrunnable on the very deployment they target
(``TORTOISE_RECORD_DSN`` is exported there, which is most boxes), and the only
escape the error offered was ``--clear-without-record`` — the flag that
authorises clearing. So on a census the record is simply NOT OPENED, and the run
says so on stderr.

⛔ PRESENCE IS NOT PROOF, so the writing arm also judges the ANSWER: if the
record was asked about N sessions and answered for NONE of them, that is what a
WRONG (or unreachable) scope looks like through row-level security — never an
error, always zero rows — and the run refuses to conclude. Nothing is destroyed
(a candidate the record cannot replace is LEFT ALONE by default), and the exit
is 3, not 1: "the census did not reach 0" is a different problem from "the
record answered for nobody, check your scope".

The clear-only arm (no record) is CORRECT for §9.1/D1 — the prose does not belong
on a node — but a replay engine's trailing sweep does NOT take it (that sweep is
plan-only and writes nothing), and it leaves the session un-findable through the
Source FTS index until a run WITH the record re-derives it. Because that arm is
destructive and not self-correcting (the second run finds no candidates), it
must be asked for BY NAME: ``--clear-without-record``. ⛔ That is enforced
PER NODE, not on whether a DSN string was supplied (#11152 review P2): a DSN
with an empty or wrong scope satisfies "a DSN exists" while the record answers
for NOBODY, and a gate keyed on the string alone would take the clear arm for
the whole population and still print ``Repair complete.``. A candidate the
record cannot replace is therefore LEFT ALONE by default, counted in
``left_alone``, and named on stderr.

Idempotent: a re-run finds no candidates.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"graph-scripts/11070_repair_session_source_prose.py requires Python "
        f">= 3.12 (got {sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python graph-scripts/11070_repair_session_source_prose.py`"
    )

import argparse
import json
import os
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tortoise.projection import FalkorProjection  # noqa: E402, RUF100
from tortoise.session_source_prose_migration import (  # noqa: E402, RUF100
    find_session_sources_carrying_prose,
    reconcile_session_source_prose,
    record_turn_reader,
)


def derive_uri_graph(uri: str) -> str:
    """The default graph a URI addresses — the same derivation sdk.py uses.

    ⛔ AN EMBEDDED/PATH URI HAS A NAMED DEFAULT, NOT AN EMPTY NAME. Returning
    ``""`` here (round 6 briefly did) is not "let the projection choose": the empty
    string is a REAL, distinct, empty FalkorDB graph, so `--db <path>` with no
    `--graph` read that graph instead of the `tortoise` graph `sdk._derive_graph_name`
    returns for an embedded checkout («``return uri_graph or "tortoise"``») — and
    `--dry-run --assert-clean` then exited 0 over an unrepaired graph it never
    opened (#11152 review round 7, MATERIAL false PASS).
    """
    if "://" not in uri:
        return "tortoise"
    return urlparse(uri).path.lstrip("/") or "tortoise"


def open_projection(uri: str, graph_name: str):
    """Open the projection for *uri* — a server URI, or an embedded DB path."""
    if "://" not in uri:
        # ⛔ `--graph` MUST REACH THE EMBEDDED ARM TOO. Dropping it here made
        # `--db <path> --graph X` open the DEFAULT embedded graph while the report
        # printed `graph: X` — a clean verdict over a graph the run never opened
        # (#11152 review round 6).
        # ⛔ `or "tortoise"` is the second half of the same guarantee: an empty
        # graph NAME is a real graph, so it must never reach the constructor.
        return FalkorProjection(uri, graph_name=graph_name or "tortoise",
                                allow_nonstandard_path=True)
    return FalkorProjection.from_uri(uri, graph_name=graph_name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="TORTOISE_DB_URI override (default: env)")
    ap.add_argument("--graph", default=None,
                    help="graph name (default: derived from the URI, like sdk.py)")
    ap.add_argument("--dry-run", action="store_true",
                    help="the DEFAULT — report the census and the plan only, no "
                         "writes. Accepted explicitly so the runbook's step-1 and "
                         "step-3 commands are runnable (the sibling "
                         "`4125_migrate_legacy_session_sources.py` takes it too)")
    ap.add_argument("--repair", action="store_true",
                    help="WRITE the repair (the default is a dry run)")
    ap.add_argument("--record-dsn", default=None,
                    help="record DSN — re-derive each lexicon from the RECORD "
                         "(default: env TORTOISE_RECORD_DSN; the inherited DSN "
                         "is not used when --clear-without-record is passed "
                         "WITHOUT a scope). ⛔ ABSENT IS NOT 'clear only': the "
                         "writing arm REFUSES without a DSN unless "
                         "--clear-without-record asks for the clear arm by name")
    ap.add_argument("--org-id", default=None,
                    help="tenant org id for the record read (REQUIRED with "
                         "--record-dsn on the WRITING arm — a scope is never "
                         "guessed; default: env TORTOISE_ORG_ID)")
    ap.add_argument("--graph-id", default=None,
                    help="record graph id (REQUIRED with --record-dsn on the "
                         "WRITING arm — the cache graph name is NOT the "
                         "record's scope)")
    ap.add_argument("--clear-without-record", action="store_true",
                    help="authorise CLEARING a candidate the record cannot "
                         "answer for. The RECORD IS STILL USED when you supply "
                         "one (typed --record-dsn, or a typed --org-id/"
                         "--graph-id with an exported TORTOISE_RECORD_DSN): "
                         "where it answers the pair is RE-DERIVED, and only the "
                         "nodes it cannot answer for are cleared. With NO record "
                         "scope at all it clears every candidate. Without this "
                         "flag such a candidate is REPORTED and LEFT ALONE. Not "
                         "required on a census (--dry-run), which never writes.")
    ap.add_argument("--assert-clean", action="store_true",
                    help="exit 1 if any candidate remains (dry run only) — the "
                         "step-3 verification; without it a dry run always exits 0")
    ap.add_argument("--json", action="store_true",
                    help="emit the machine-readable report on stdout")
    args = ap.parse_args()
    if args.repair and args.dry_run:
        ap.error("--repair and --dry-run are mutually exclusive")

    uri = args.db or os.environ.get("TORTOISE_DB_URI", "")
    if not uri:
        print("ERROR: no DB URI — pass --db or set TORTOISE_DB_URI", file=sys.stderr)
        return 1
    if args.graph is not None and not args.graph.strip():
        # ⛔ AN EMPTY NAME IS NOT "the default": it is a real, distinct graph — or,
        # on the served arm, a driver `TypeError` from `select_graph("")`. Refuse
        # it at the boundary instead of guessing either way (#11152 round 8).
        print("ERROR: --graph may not be empty. Omit it to use the graph the URI "
              "addresses, or name the cache graph explicitly.", file=sys.stderr)
        return 2
    graph_name = args.graph if args.graph is not None else derive_uri_graph(uri)

    read_transcript = None
    # `args.record_dsn is not None` (not truthiness) so `--record-dsn ""` can
    # suppress an exported DSN.
    explicit_dsn = args.record_dsn is not None
    record_dsn = (args.record_dsn if explicit_dsn
                  else os.environ.get("TORTOISE_RECORD_DSN", ""))
    # ⛔ ONLY THE INHERITED DSN IS DROPPED, AND ONLY WHEN NO SCOPE WAS TYPED.
    # `--clear-without-record` exists so the clear arm is reachable on a box that
    # EXPORTS a `TORTOISE_RECORD_DSN` the operator did not mean to use — it must
    # never discard a `--record-dsn` the operator TYPED (that made the safe
    # combination unreachable; #11152 round 2), and it must not discard the
    # inherited one either when the operator TYPES A SCOPE, because typing
    # `--org-id`/`--graph-id` is an explicit statement that the record IS to be
    # used: the flag then means "re-derive where it answers, clear where it does
    # not", which is what the flag's own help promises (#11152 round 8, P1).
    # Dropping the inherited DSN in that case opened no record at all and cleared
    # every candidate, including ones the record could have re-derived.
    typed_scope_here = bool((args.org_id or os.environ.get("TORTOISE_ORG_ID"))
                            and args.graph_id)
    drop_inherited = (args.clear_without_record and not explicit_dsn
                      and not typed_scope_here)
    if drop_inherited:
        record_dsn = ""
    org_id = args.org_id or os.environ.get("TORTOISE_ORG_ID", "")
    graph_id = args.graph_id
    record_conn = None
    # Replaced by the projection's OWN name once one is opened (see `report["graph"]`).
    actual_graph = graph_name
    scope_given = bool(org_id and graph_id)
    # ⛔ REFUSE, never degrade — AND ONLY WHERE THE RUN CAN DESTROY SOMETHING. A
    # missing scope reads as "the record did not answer" for EVERY session
    # (row-level security treats an absent GUC as no rows, #10000 review P0-1),
    # so a writing arm may not reach the record without one: the scope is never
    # derived from the cache graph name, and `--clear-without-record` is the
    # flag that authorises clearing.
    #
    # ⚠️ On a CENSUS it may not refuse: a census reads the cache, and
    # `--assert-clean` is the VERIFICATION step — requiring a scope there made
    # the runbook's step 1 and step 3 exit 2 on the very box they target
    # (`TORTOISE_RECORD_DSN` exported, which is most of them), and the escape
    # the message offered was the clearing flag. So the record is not opened on
    # that arm, and the run says so (#11152 review P2).
    if args.repair and record_dsn and not scope_given:
        print("ERROR: --record-dsn on the WRITING arm (--repair) requires BOTH "
              "--org-id and --graph-id (or TORTOISE_ORG_ID). The record scope is "
              "never derived from the cache graph name — a wrong scope reads as "
              "'the record did not answer' for every session.\n"
              "       To run the destructive clear-only arm deliberately, pass "
              "--clear-without-record and no --record-dsn.\n"
              "       A census needs no scope at all: without --repair the record "
              "is not consulted.", file=sys.stderr)
        return 2
    if args.repair and not record_dsn and not args.clear_without_record:
        print("ERROR: --repair with no --record-dsn clears the pair on every "
              "candidate and can never re-derive the lexicon. Pass --record-dsn "
              "+ --org-id + --graph-id, or --clear-without-record to accept the "
              "clear-only arm deliberately.", file=sys.stderr)
        return 2
    if record_dsn and not scope_given:
        # Only reachable on the census arm (the writing one was refused above).
        # An unscoped read returns zero rows under the record's row-level
        # security, which would mislabel every candidate `no_record`; the census
        # needs no record, so it is not consulted and the run says so.
        print("NOTE: no record scope (--org-id/--graph-id) — this is a census, "
              "which reads the CACHE only, so the record is NOT consulted and "
              "no candidate is re-derived. The writing arm (--repair) requires "
              "the scope.", file=sys.stderr)
        record_dsn = ""
    if record_dsn:
        # The record door is opened ONLY when a scope was supplied: the guard
        # above REFUSES a writing arm without one, and silences the DSN on a
        # census. A turn read on an unscoped connection is a ZERO-ROW read under
        # the record's row-level security (#10000 review P0-1), which would make
        # every session look like "the record did not answer".
        #
        # ⛔ THE DECLARED DOOR, never a raw driver dial. `open_record` resolves
        # the DSN through `record_writer.record_dsn` — the module that DECLARES
        # the arming surface — applies the connect + statement bounds, and
        # refuses a non-Postgres value as the misconfiguration it is. It also
        # takes the dependency the project actually declares
        # (`postgres = ["psycopg2-binary"]`): this repo has no `psycopg` (v3)
        # anywhere, so a raw `import psycopg` made this arm — the only one that
        # can re-derive a lexicon — raise ModuleNotFoundError on every
        # deployment (review, P1).
        from tortoise.record_serve import open_record

        record_conn = open_record(record_dsn)
        read_transcript = record_turn_reader(
            record_conn, org_id=org_id, graph_id=graph_id)

    proj = None
    try:
        proj = open_projection(uri, graph_name)
        actual_graph = getattr(proj, "graph_name", None) or graph_name
        if args.repair:
            report = reconcile_session_source_prose(
                proj.g, read_transcript=read_transcript, dry_run=False,
                clear_without_record=args.clear_without_record)
        else:
            # ONE census read: hand the reconciler what the detail print needs,
            # instead of scanning every Source twice.
            found = find_session_sources_carrying_prose(proj.g)
            report = reconcile_session_source_prose(
                proj.g, read_transcript=read_transcript, dry_run=True,
                found=found, clear_without_record=args.clear_without_record)
            report["candidate_detail"] = found
    finally:
        if proj is not None:
            proj.close()
        if record_conn is not None:
            record_conn.close()

    # The exit code is computed BEFORE the JSON branch: a `--json` run that
    # returned 0 first made both failure exits (`census_after != 0`,
    # `--assert-clean`) unreachable, which is the same "verification step that
    # cannot fail" defect fix 7 closed for the human-readable path. The
    # unproven-scope refusal is on that same list — a `--json` run must not be
    # able to report success over a refusal.
    #
    # ⛔ UNPROVEN SCOPE, derived from the report's own buckets: at least one
    # session was asked, and the record answered for NONE of them. Zero rows for
    # every session is what a WRONG or unreachable scope looks like through
    # row-level security — never an error — so this is a scope failure, not a
    # population, and it must not be reported as "the census did not reach 0".
    #
    # ⛔ THIS MUST NOT FIRE ON THE BY-NAME CLEAR ARM. With `--clear-without-
    # record` the operator has already said the record is not to be relied on,
    # and (since an EXPLICIT `--record-dsn` now survives that flag) the record can
    # still be opened and answer for nobody — at which point `no_record` means
    # "CLEARED", not "left alone". Refusing there printed "nothing was destroyed"
    # over a run that HAD cleared the population, and advised passing the very
    # flag the operator had already passed (#11152 review round 3, P2).
    asked = (len(report["repaired"]) + len(report.get("empty_sessions") or [])
             + len(report["no_record"]))
    scope_unproven = (read_transcript is not None and asked > 0
                      and len(report["no_record"]) == asked
                      and not args.clear_without_record)
    if not args.repair:
        scope_unproven = False
    report["scope_unproven"] = scope_unproven
    # ⛔ THE GRAPH THE RUN ACTUALLY OPENED, not the name it computed. The report
    # line is the operator's only check that `--graph` reached the projection, and
    # `--json` printed no such line at all — so a MACHINE-READABLE record of a
    # destructive run could not say which cache graph it wrote (#11152 r6).
    report["graph"] = actual_graph

    code = 0
    if scope_unproven:
        code = 3
    elif args.repair and report["census_after"]:
        code = 1
    if not args.repair and args.assert_clean and report["candidates"]:
        code = 1

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return code

    if scope_unproven:
        print(f"✗ REFUSED — the record answered for NONE of the {asked} session(s) "
              f"it was asked about, so the scope is UNPROVEN and NO repair was "
              f"reported as done. That is what a wrong (or unreachable) "
              f"--org-id/--graph-id looks like through row-level security: zero "
              f"rows, never an error.\n"
              f"  Check the scope against the record and re-run; nothing was "
              f"destroyed ({len(report.get('left_alone') or [])} candidate(s) "
              f"left alone). To clear without a lexicon DELIBERATELY, pass "
              f"--clear-without-record.", file=sys.stderr)
        return code

    print(f"graph: {actual_graph or '(URI default)'}   mode: "
          f"{'REPAIR' if args.repair else 'dry run'}")
    print(f"census BEFORE: {report['census_before']}")
    print(f"candidates: {len(report['candidates'])}  "
          f"re-derived from the record: {len(report['repaired'])}  "
          f"record spoke, 0 turns: {len(report.get('empty_sessions') or [])}  "
          f"record did not answer: {len(report['no_record'])}  "
          f"no session id: {len(report.get('no_session_id') or [])}  "
          f"UNADDRESSABLE (no url): {len(report.get('unaddressable') or [])}  "
          f"never asked (no reader): {len(report.get('no_reader') or [])}  "
          f"LEFT ALONE: {len(report.get('left_alone') or [])}")
    if report.get("left_alone"):
        # ⛔ Named, not silent. The destructive arm is deliberately NOT taken on
        # these, so the operator must see the count that the default refused and
        # the flag that would take it — a report that hides this reads as a
        # clean run that repaired less than it should have.
        print(f"⚠️ {len(report['left_alone'])} candidate(s) were NOT written: the "
              f"record could not supply a replacement, and clearing is "
              f"irreversible (a second run cannot find the node to re-derive "
              f"it). Re-run with --record-dsn (and --org-id/--graph-id) to "
              f"re-derive, or with --clear-without-record to take the "
              f"clear-only arm deliberately.", file=sys.stderr)
    for node in report.get("candidate_detail") or []:
        print(f"          {node['url']}: summary={node['summary_bytes']}B "
              f"_searchText={node['search_text_bytes']}B session={node['session_id']}")
    for url, note in (report.get("notes") or {}).items():
        print(f"          ⚠️ {url}: {note}")
    if args.repair:
        print(f"census AFTER:  {report['census_after']}")
        if report["census_after"]:
            print("⚠️ the census did not reach 0 — re-run --dry-run and read the "
                  "report before reporting the repair as done", file=sys.stderr)
            return 1
        print("\nRepair complete. Re-run --dry-run to confirm 0 candidates.")
    else:
        print("\nDry run — nothing written. Re-run with --repair to repair.")
        if args.assert_clean and report["candidates"]:
            # ⚠️ A verification step that cannot fail is not a verification step:
            # without this the runbook's step 3 (`--dry-run --assert-clean`)
            # passes over an unrepaired graph.
            print(f"✗ {len(report['candidates'])} candidate(s) remain — the census "
                  f"has NOT reached 0", file=sys.stderr)
            return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
