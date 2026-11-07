#!/usr/bin/env python3
"""The LIVE record-write door's per-record cost, measured — #9558 (R10).

WHY THIS FILE EXISTS. #9558's target is *"a recorded before/after measurement of
records/second"*, not an assertion that pooling exists. A before/after is only a
measurement if ONE instrument produces both numbers, so this script takes
``--tree`` and runs the identical code path against whatever tree it is pointed
at:

    python probe_record_write_cost.py --tree /path/to/tortoise --dsn postgresql://…
    python probe_record_write_cost.py --tree .worktrees/child-9558 --dsn postgresql://…

WHAT IT DRIVES. ``record_writer.write_live_record`` — the production door the
three journal-append sites call, and the one shape that pays the FULL per-record
cost: it opens its own connection, materializes the plan, commits, and releases
the connection, once per journal record. (``write_records``/``RecordWriter.write``
with an injected ``conn=`` does none of the open/close, so measuring those would
measure a path that does not exist in production.)

WHAT IT COUNTS, AND WHY EACH COUNT IS NOT A PROXY.

* ``connect_calls`` — wraps ``psycopg2.connect``. This is the number the issue's
  target names (*"connection setup no longer appears per record in a profile"*).
  It is corroborated server-side by the wrapper shell's ``connection
  authorized`` count from the Postgres log, which no client-side edit can move.
* ``table_columns_calls`` / ``refused_props_calls`` — the two migration reads
  ``replay_plan`` performs per call. The issue's target is *"called once per
  process/refresh, not once per record"*, so the call count IS the indicator.
* per-record wall time, and records/second derived from it. ``--warmup`` calls
  are discarded so the first record's one-off costs (driver import, column-map
  cold read) are not smeared across the sample.

Every write asserts ``APPLIED``: a refused or failed write is skipped work, and
timing it would report a fast door that stored nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

#: The record's application role (`20261025000001_record_tenancy_role_and_guc.sql`).
#: `replay()` refuses a connection whose `current_user` can bypass RLS, so the
#: measuring connection must be this role — a superuser DSN would red the write
#: for a reason that has nothing to do with cost.
RECORD_APP_ROLE = "tortoise_record_app"

#: The minimum Supabase-shaped substrate the record migrations need: the three
#: roles they GRANT to, and the two control-plane tables their tenancy FKs point
#: at. Byte-identical to `_PREAMBLE` in
#: `tests/test_9078_record_written_by_production_path.py`, so the database this
#: probe measures is the SHAPE the record tests measure on — a different
#: substrate would make the two numbers incomparable. Duplicated rather than
#: imported because this probe must run against a tree whose tests directory may
#: be at a different commit than the tree whose dependencies are installed.
PREAMBLE = """
DO $$ BEGIN
  CREATE ROLE anon NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE ROLE authenticated NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE ROLE service_role NOLOGIN BYPASSRLS;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
CREATE TABLE IF NOT EXISTS public.organizations (
    id text PRIMARY KEY,
    name text NOT NULL,
    graph_name text
);
CREATE TABLE IF NOT EXISTS public.graphs (
    id text PRIMARY KEY,
    org_id text NOT NULL REFERENCES public.organizations(id),
    name text NOT NULL,
    namespace text,
    UNIQUE (org_id, id)
);
"""


def app_role_dsn(dsn: str) -> str:
    """`dsn` opening with `current_user` = the record's application role.

    `options=-c role=…` is libpq's connect-time `SET ROLE`. `quote_via=quote`
    because libpq does NOT decode `+` as a space (`urlencode`'s default would
    hand the backend `-c+role=…` and the CONNECT itself would fail).
    """
    parts = urlsplit(dsn)
    query = parse_qs(parts.query)
    query["options"] = [f"-c role={RECORD_APP_ROLE}"]
    return urlunsplit(
        parts._replace(query=urlencode(query, doseq=True, quote_via=quote)))


def point_record(index: int) -> dict:
    """One `PointAdded` journal record — the smallest faithful live write."""
    return {
        "type": "PointAdded",
        "point": {
            "id": f"pt_9558_{index:04d}_{uuid.uuid4().hex[:8]}",
            "pointKind": "event",
            "content": f"the record write measured pass {index}",
            "status": "live",
            "confidence": 0.8,
            "c_cal": 0.7,
        },
        "ts": "2026-11-07T00:00:00Z",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", default=".",
                        help="the checkout whose `tortoise` package is measured")
    parser.add_argument("--dsn", required=True,
                        help="superuser DSN to a Postgres (migrations applied here)")
    parser.add_argument("--records", type=int, default=60,
                        help="timed records (after warmup)")
    parser.add_argument("--warmup", type=int, default=5,
                        help="discarded leading records")
    parser.add_argument("--json", action="store_true",
                        help="emit one JSON object (machine comparison)")
    args = parser.parse_args()

    tree = Path(args.tree).resolve()
    if not (tree / "tortoise" / "record_writer.py").exists():
        print(f"not a tortoise checkout: {tree}", file=sys.stderr)
        return 2
    # The MEASURED tree must be the imported one, and nothing else.
    sys.path.insert(0, str(tree))

    import psycopg2

    from tortoise import record_projection as rp
    from tortoise import record_writer as rw

    assert Path(rw.__file__).resolve().is_relative_to(tree), (
        f"the imported seam is {rw.__file__}, not the --tree under test ({tree})")

    # ---- schema -----------------------------------------------------------
    admin = psycopg2.connect(args.dsn)
    admin.autocommit = True
    migrations = tree / "supabase" / "migrations"
    with admin.cursor() as cur:
        cur.execute("SELECT to_regclass('public.journal_event') IS NOT NULL")
        migrated = bool(cur.fetchone()[0])
    if not migrated:
        with admin.cursor() as cur:
            cur.execute(PREAMBLE)
        for name in rp.RECORD_MIGRATIONS:
            path = migrations / name
            if path.exists():
                with admin.cursor() as cur:
                    cur.execute(path.read_text(encoding="utf-8"))
    org, graph = "org_9558", "graph_9558"
    with admin.cursor() as cur:
        cur.execute("INSERT INTO public.organizations (id, name) VALUES (%s, %s) "
                    "ON CONFLICT (id) DO NOTHING", (org, org))
        cur.execute("INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s) "
                    "ON CONFLICT (id) DO NOTHING", (graph, org, graph))
        for table in ("event", "object", "subject", "point", "relation",
                      "session", "source"):
            cur.execute(f"DELETE FROM public.{table} WHERE org_id=%s AND graph_id=%s",
                        (org, graph))
    admin.close()

    # ---- instrumentation --------------------------------------------------
    counts = {"connect_calls": 0, "table_columns_calls": 0,
              "refused_props_calls": 0}
    real_connect = psycopg2.connect
    real_table_columns = rp.table_columns
    real_refused = rp.refused_props_names

    def counting_connect(*a, **kw):
        counts["connect_calls"] += 1
        return real_connect(*a, **kw)

    def counting_table_columns(*a, **kw):
        counts["table_columns_calls"] += 1
        return real_table_columns(*a, **kw)

    def counting_refused(*a, **kw):
        counts["refused_props_calls"] += 1
        return real_refused(*a, **kw)

    psycopg2.connect = counting_connect
    rp.table_columns = counting_table_columns
    rp.refused_props_names = counting_refused

    os.environ[rw.RECORD_DSN_ENV] = app_role_dsn(args.dsn)
    tenant = (org, graph)

    # ---- the migration read, isolated ------------------------------------
    # `table_columns()` on its own: the half of the per-record cost #9558 calls
    # out as "a fresh migration read per record". Measured separately so the
    # pooled-connection half and the column-map half are attributable.
    column_read_ms: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        rp.table_columns()
        column_read_ms.append((time.perf_counter() - started) * 1000.0)

    # ---- the write path ---------------------------------------------------
    def one_write(index: int) -> float:
        started = time.perf_counter()
        write = rw.write_live_record(point_record(index), seq=index + 1,
                                     tenant=tenant)
        elapsed = (time.perf_counter() - started) * 1000.0
        if write is None or write.outcome is not rw.RecordWriteOutcome.APPLIED:
            raise SystemExit(
                f"write {index} did not apply: {write!r} — timing a refused "
                "write measures a door that stored nothing")
        return elapsed

    for i in range(args.warmup):
        one_write(i)

    before_connect = counts["connect_calls"]
    before_tc = counts["table_columns_calls"]
    before_rp = counts["refused_props_calls"]

    samples: list[float] = []
    wall_start = time.perf_counter()
    for i in range(args.records):
        samples.append(one_write(args.warmup + i))
    wall = time.perf_counter() - wall_start

    result = {
        "tree": str(tree),
        "head": _head(tree),
        "records": args.records,
        "warmup": args.warmup,
        "wall_s": round(wall, 4),
        "records_per_second": round(args.records / wall, 2),
        "per_record_ms": {
            "mean": round(statistics.fmean(samples), 3),
            "median": round(statistics.median(samples), 3),
            "p95": round(sorted(samples)[max(0, int(len(samples) * 0.95) - 1)], 3),
            "min": round(min(samples), 3),
            "max": round(max(samples), 3),
        },
        "per_timed_record": {
            "connect_calls": counts["connect_calls"] - before_connect,
            "table_columns_calls": counts["table_columns_calls"] - before_tc,
            "refused_props_calls": counts["refused_props_calls"] - before_rp,
        },
        "table_columns_isolated_ms": {
            "mean": round(statistics.fmean(column_read_ms), 3),
            "median": round(statistics.median(column_read_ms), 3),
        },
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"tree                     {result['tree']}  ({result['head']})")
        print(f"records                  {result['records']} (+{result['warmup']} warmup)")
        print(f"wall                     {result['wall_s']} s")
        print(f"records/second           {result['records_per_second']}")
        print(f"per record (ms)          mean {result['per_record_ms']['mean']}"
              f"  median {result['per_record_ms']['median']}"
              f"  p95 {result['per_record_ms']['p95']}")
        per = result["per_timed_record"]
        print(f"connect() per record     {per['connect_calls'] / args.records:.2f}")
        print(f"table_columns() / record {per['table_columns_calls'] / args.records:.2f}")
        print(f"refused_props() / record {per['refused_props_calls'] / args.records:.2f}")
        print(f"table_columns isolated   mean "
              f"{result['table_columns_isolated_ms']['mean']} ms")
    return 0


def _head(tree: Path) -> str:
    """The tree's HEAD sha, for the record (never a guess: 'unknown' if absent)."""
    try:
        import subprocess
        return subprocess.run(["git", "-C", str(tree), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
