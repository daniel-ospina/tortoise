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
  # 1) census + plan, no writes (the default)
  uv run python graph-scripts/11070_repair_session_source_prose.py --dry-run

  # 2) repair, re-deriving each lexicon FROM THE RECORD
  uv run python graph-scripts/11070_repair_session_source_prose.py \\
      --repair --record-dsn "$TORTOISE_RECORD_DSN" \\
      --org-id org_acme --graph-id org_acme_tortoise

  # 3) verify: the census must be 0 — `--assert-clean` is what can FAIL
  uv run python graph-scripts/11070_repair_session_source_prose.py \
      --dry-run --assert-clean || exit 1

⚠️ THE RECORD SCOPE IS NEVER GUESSED. ``--record-dsn`` requires ``--org-id`` and
``--graph-id``: a wrong (or defaulted) scope is NOT an error at the record — the
row-level security returns no rows, so every session reads as "the record did not
answer" and the run would take the destructive clear-only arm, print
``Repair complete.`` and exit 0. The sibling verbs refuse to derive a scope for
exactly this reason (``tortoise/__main__.py``: "No default is derived — a wrong
guess opens an EMPTY graph"), and so does this one.

The clear-only arm (no record) is CORRECT for §9.1/D1 — the prose does not belong
on a node — but a replay engine's trailing sweep does NOT take it (that sweep is
plan-only and writes nothing), and it leaves the session un-findable through the
Source FTS index until a run WITH the record re-derives it. Because that arm is destructive and not self-correcting (the second
run finds no candidates), it must be asked for BY NAME: ``--clear-without-record``.

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
    """The default graph a URI addresses — the same derivation sdk.py uses."""
    if "://" not in uri:
        return ""
    return urlparse(uri).path.lstrip("/") or "tortoise"


def open_projection(uri: str, graph_name: str):
    """Open the projection for *uri* — a server URI, or an embedded DB path."""
    if "://" not in uri:
        return FalkorProjection(uri, allow_nonstandard_path=True)
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
                         "(default: env TORTOISE_RECORD_DSN; absent = clear only)")
    ap.add_argument("--org-id", default=None,
                    help="tenant org id for the record read (REQUIRED with "
                         "--record-dsn — a scope is never guessed)")
    ap.add_argument("--graph-id", default=None,
                    help="record graph id (REQUIRED with --record-dsn — the "
                         "cache graph name is NOT the record's scope)")
    ap.add_argument("--clear-without-record", action="store_true",
                    help="allow the destructive clear-only arm (no lexicon "
                         "re-derived). Required when no record scope is given.")
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
    graph_name = args.graph if args.graph is not None else derive_uri_graph(uri)

    read_transcript = None
    # `args.record_dsn is not None` (not truthiness) so `--record-dsn ""` can
    # suppress an exported DSN, and `--clear-without-record` WINS over the env —
    # otherwise the arm the operator asked for is unreachable on a box that
    # exports `TORTOISE_RECORD_DSN`, which is most of them.
    record_dsn = (args.record_dsn if args.record_dsn is not None
                  else os.environ.get("TORTOISE_RECORD_DSN", ""))
    if args.clear_without_record:
        record_dsn = ""
    org_id = args.org_id or os.environ.get("TORTOISE_ORG_ID", "")
    graph_id = args.graph_id
    record_conn = None
    # ⛔ REFUSE, never degrade. A missing scope reads as "the record did not
    # answer" for EVERY session (row-level security treats an absent GUC as no
    # rows, #10000 review P0-1), which silently turns the whole run into the
    # destructive clear-only arm and exits 0. A wrong scope is indistinguishable
    # from a right one, so an unproven scope may not reach the record at all.
    if record_dsn and not (org_id and graph_id):
        print("ERROR: --record-dsn requires BOTH --org-id and --graph-id (or "
              "TORTOISE_ORG_ID). The record scope is never derived from the "
              "cache graph name — a wrong scope reads as 'the record did not "
              "answer' for every session and silently clears every node.\n"
              "       To run the destructive clear-only arm deliberately, pass "
              "--clear-without-record and no --record-dsn.", file=sys.stderr)
        return 2
    if args.repair and not record_dsn and not args.clear_without_record:
        print("ERROR: --repair with no --record-dsn clears the pair on every "
              "candidate and can never re-derive the lexicon. Pass --record-dsn "
              "+ --org-id + --graph-id, or --clear-without-record to accept the "
              "clear-only arm deliberately.", file=sys.stderr)
        return 2
    if record_dsn:
        # The record door is opened ONLY when the operator supplied a scope: a
        # turn read on an unscoped connection is a ZERO-ROW read under the
        # record's row-level security (#10000 review P0-1), which would make
        # every session look like "the record did not answer" and silently turn
        # the whole run into the clear-only arm.
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
        if args.repair:
            report = reconcile_session_source_prose(
                proj.g, read_transcript=read_transcript, dry_run=False)
        else:
            # ONE census read: hand the reconciler what the detail print needs,
            # instead of scanning every Source twice.
            found = find_session_sources_carrying_prose(proj.g)
            report = reconcile_session_source_prose(
                proj.g, read_transcript=read_transcript, dry_run=True,
                found=found)
            report["candidate_detail"] = found
    finally:
        if proj is not None:
            proj.close()
        if record_conn is not None:
            record_conn.close()

    # The exit code is computed BEFORE the JSON branch: a `--json` run that
    # returned 0 first made both failure exits (`census_after != 0`,
    # `--assert-clean`) unreachable, which is the same "verification step that
    # cannot fail" defect fix 7 closed for the human-readable path.
    code = 0
    if args.repair and report["census_after"]:
        code = 1
    if not args.repair and args.assert_clean and report["candidates"]:
        code = 1

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return code

    print(f"graph: {graph_name or '(URI default)'}   mode: "
          f"{'REPAIR' if args.repair else 'dry run'}")
    print(f"census BEFORE: {report['census_before']}")
    print(f"candidates: {len(report['candidates'])}  "
          f"re-derived from the record: {len(report['repaired'])}  "
          f"record spoke, 0 turns: {len(report.get('empty_sessions') or [])}  "
          f"record did not answer: {len(report['no_record'])}")
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
