#!/usr/bin/env python3
"""#7983 — measure `rho`'s per-read magnitudes on the REAL record schema.

`rho` is *the record's non-storage lines (compute + IOPS + egress) relative to
its disk line* (STORAGE-ARCHITECTURE.md §17.4.4). §17.4 prices those lines and
leaves their MAGNITUDE unmeasured, which is why every hybrid total in the doc is
a `≥`. This probe measures the magnitudes that a bench can measure, on the
committed record schema (`supabase/migrations/2026101200000{1..4}_record_*.sql`)
and the committed record read shape (`cache_projection.read_record_view`):

  * bytes per RECORD ROW, per label           -> the disk line's true unit
  * LOGICAL BLOCKS per keyed miss fetch       -> IOPS per cache miss
  * LOGICAL BLOCKS + ms per full-scope read   -> IOPS + CPU per rebuild
  * ms per keyed miss fetch                   -> CPU per miss

WHAT THIS CANNOT MEASURE, stated up front: the record's READ RATE (misses per
tenant per month). That is a workload fact about a tenant, and no tenant is
serving the Postgres record today — the store is not deployed (§17.4.6). So the
probe reports unit costs and the RATE AT WHICH each line becomes binding, not a
single `rho`.

Shape of the planted tenant: `S` = the §17.4.2 stock, 89,701 resident nodes /
tenant, with the content width MEASURED on the dogfood graph
(`MATCH (n:Point) RETURN avg(size(n.content))` = 1,121 B, max 19,061 B,
`count(n.embedding)`/`count(n)` = 4,145/10,374 = 40.0% carry a vector) —
measured 2026-10-17 against `tortoise@459f22912`.

Run (throwaway container; the DSN is not a product endpoint):

    docker run -d --name rho7983-pg -e POSTGRES_PASSWORD=rho -e POSTGRES_DB=record \
        -p 127.0.0.1:15499:5432 postgres:16
    uv run --with psycopg2-binary python \
        docs/evidence/7983-cost-magnitudes/probes/record_read_cost.py
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[4]
MIG_DIR = ROOT / "supabase/migrations"
MIGRATIONS = [
    "20261012000001_record_event_stream.sql",
    "20261012000002_record_semantic.sql",
    "20261012000003_record_epistemic.sql",
    "20261012000004_record_procedural.sql",
    "20261014000001_record_subject_props_no_derived.sql",
]

#: §17.4.1's published rates, restated so the arithmetic below is auditable and
#: every figure it produces can be traced to one line of the doc.
RATE_DISK_GB_MONTH = 0.125      # Postgres General Purpose disk, per GB
RATE_IOPS_MONTH = 0.024         # provisioned IOPS beyond 3,000 included
INCLUDED_IOPS = 3000
RATE_EGRESS_GB = 0.09           # beyond 250 GB included
INCLUDED_EGRESS_GB = 250
INCLUDED_DISK_GB = 8            # per project, §17.4.1
BLOCK_BYTES = 8192              # Postgres page size

#: §17.4.2's stock, per tenant.
STOCK_NODES_PER_TENANT = 89_701

BOOTSTRAP = """
DO $$ BEGIN CREATE ROLE anon NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE authenticated NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE service_role NOLOGIN BYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role;
CREATE TABLE IF NOT EXISTS public.organizations (
  id text PRIMARY KEY, name text NOT NULL, graph_name text);
CREATE TABLE IF NOT EXISTS public.graphs (
  id text PRIMARY KEY, org_id text NOT NULL REFERENCES public.organizations(id),
  name text NOT NULL, namespace text);
"""


def bootstrap(conn) -> None:
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(BOOTSTRAP)
    # The record migrations are plain additive DDL with CREATE POLICY, which
    # Postgres has no `IF NOT EXISTS` for — so a second run would fail on an
    # already-migrated database. Apply them only to a fresh one.
    cur.execute("SELECT to_regclass('public.point') IS NOT NULL")
    if cur.fetchone()[0]:
        print("record schema already present — migrations not re-applied")
        conn.autocommit = False
        return
    for name in MIGRATIONS:
        cur.execute((MIG_DIR / name).read_text())
    conn.autocommit = False


def plant(conn, tenants: int, nodes_per_tenant: int) -> None:
    """Plant `tenants` tenants at the §17.4.2 stock, on the committed schema.

    Content and vector widths come from the dogfood graph (module docstring).
    The vector is written as a 384-element jsonb array — the record's own column
    type — at the measured 40.0% carrying rate.
    """
    cur = conn.cursor()
    for t in range(tenants):
        org, graph = f"rho7983-org-{t}", f"rho7983-graph-{t}"
        cur.execute(
            "INSERT INTO public.organizations (id, name, graph_name) VALUES (%s,%s,%s) "
            "ON CONFLICT (id) DO NOTHING", (org, org, graph))
        cur.execute(
            "INSERT INTO public.graphs (id, org_id, name, namespace) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (id) DO NOTHING", (graph, org, graph, graph))
        cur.execute(
            "INSERT INTO public.node_identity (org_id, graph_id, node_label, node_key) "
            "SELECT %s, %s, 'Point', 'p-' || g FROM generate_series(1, %s) g "
            "ON CONFLICT DO NOTHING", (org, graph, nodes_per_tenant))
        cur.execute(
            """
            INSERT INTO public.point
                (org_id, graph_id, node_key, node_label, id, content, status,
                 point_kind, embedding, created_at)
            SELECT %(org)s, %(graph)s, 'p-' || g, 'Point', 'p-' || g,
                   'claim ' || g || ' ' ||
                     repeat('lorem ipsum dolor sit amet consectetur ', 30) ||
                     repeat('x', (g %% 400)),
                   'live', 'fact',
                   CASE WHEN g %% 5 < 2 THEN (
                        SELECT jsonb_agg(round(sin(g * 0.001 + j)::numeric, 6))
                          FROM generate_series(1, 384) j)
                   END,
                   '2026-10-17T00:00:00Z'
              FROM generate_series(1, %(n)s) g
            """,
            {"org": org, "graph": graph, "n": nodes_per_tenant})
        print(f"planted tenant {org}: {nodes_per_tenant} points "
              f"(40% with a 384-d vector)")
        cur.execute("ANALYZE public.point")
    conn.commit()


def plan_of(cur, sql: str, params: tuple) -> dict:
    cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params)
    doc = cur.fetchone()[0]
    return doc[0] if isinstance(doc, list) else doc


def blocks(plan: dict) -> int:
    """LOGICAL blocks the statement touched: `hit + read`.

    Hit + read (not read alone) is the IOPS demand: a page served from
    `shared_buffers` is still one page the read path had to address, and on a
    cold miss the same page is a physical read. `FORMAT JSON` nests the buffer
    counters inside the top `Plan` node.
    """
    node = plan.get("Plan", {})
    return (int(node.get("Shared Hit Blocks", 0))
            + int(node.get("Shared Read Blocks", 0)))


def measure(conn, tenants: int) -> dict:
    cur = conn.cursor()
    out: dict = {"tenants": tenants}

    # ── 1. bytes per record row, and the tenant's record footprint ───────────
    cur.execute("SELECT pg_column_size(t) FROM public.point t "
                "WHERE org_id = %s LIMIT 2000", ("rho7983-org-0",))
    widths = [r[0] for r in cur.fetchall()]
    out["row_bytes_median"] = statistics.median(widths)
    out["row_bytes_mean"] = round(statistics.fmean(widths), 1)
    sizes = {}
    for table in ("node_identity", "point", "relation"):
        cur.execute(f"SELECT pg_total_relation_size('public.{table}')")
        sizes[table] = cur.fetchone()[0]
    out["table_bytes"] = sizes
    out["db_bytes"] = sum(sizes.values())
    out["bytes_per_tenant"] = round(out["db_bytes"] / tenants)

    org, graph = "rho7983-org-0", "rho7983-graph-0"

    # ── 2. one keyed miss fetch: the record-side read of ONE node ────────────
    fetch_sql = ("SELECT * FROM public.point "
                 "WHERE org_id = %s AND graph_id = %s AND node_key = %s")
    probe_plans, egress = [], []
    for i in range(1, 201):
        key = f"p-{(i * 397) % STOCK_NODES_PER_TENANT + 1}"
        p = plan_of(cur, fetch_sql, (org, graph, key))
        probe_plans.append((blocks(p), float(p["Execution Time"])))
        cur.execute("SELECT pg_column_size(t) FROM public.point t "
                    "WHERE org_id = %s AND graph_id = %s AND node_key = %s",
                    (org, graph, key))
        egress.append(cur.fetchone()[0])
    out["miss_blocks_median"] = statistics.median(b for b, _ in probe_plans)
    out["miss_ms_median"] = round(statistics.median(m for _, m in probe_plans), 4)
    out["miss_bytes_median"] = statistics.median(egress)

    # ── 3. a full-scope read: what a rebuild/replay pays ─────────────────────
    full = plan_of(cur, "SELECT * FROM public.point WHERE org_id = %s AND graph_id = %s",
                   (org, graph))
    out["full_scope_blocks"] = blocks(full)
    out["full_scope_ms"] = round(float(full["Execution Time"]), 2)
    out["full_scope_rows"] = int(full["Plan"]["Actual Rows"])
    return out


def arithmetic(m: dict) -> None:
    per_tenant_gb = m["bytes_per_tenant"] / 1e9
    disk_line = per_tenant_gb * RATE_DISK_GB_MONTH
    print("\n── measured ────────────────────────────────────────────────")
    print(f"record row width                : {m['row_bytes_median']:.0f} B median, "
          f"{m['row_bytes_mean']:.0f} B mean (n=2000 rows, 40% with a 384-d vector)")
    print(f"record footprint per tenant     : {m['bytes_per_tenant']:,} B "
          f"= {per_tenant_gb:.4f} GB ({m['tenants']} tenants, "
          f"{STOCK_NODES_PER_TENANT:,} nodes each)")
    per_node = m["bytes_per_tenant"] / STOCK_NODES_PER_TENANT
    print("  by table (all tenants)        : " + ", ".join(
        f"{t} {b:,} B" for t, b in m["table_bytes"].items()))
    print(f"  per node on disk              : {per_node:,.0f} B "
          f"(heap + indexes + TOAST) — against §17.4.2's 1,672 B per RESIDENT "
          f"node in the CACHE")
    print(f"keyed miss fetch                : {m['miss_blocks_median']:.0f} blocks "
          f"({m['miss_blocks_median'] * BLOCK_BYTES / 1024:.0f} KiB logical) · "
          f"{m['miss_ms_median']} ms · {m['miss_bytes_median']:.0f} B returned")
    print(f"full-scope read (rebuild)       : {m['full_scope_blocks']:,} blocks "
          f"({m['full_scope_blocks'] * BLOCK_BYTES / 1e6:.1f} MB logical) · "
          f"{m['full_scope_ms']} ms · {m['full_scope_rows']:,} rows")

    print("\n── the disk line (rho's denominator) ───────────────────────")
    print(f"disk per tenant per month       : ${disk_line:.4f} "
          f"({per_tenant_gb:.4f} GB x ${RATE_DISK_GB_MONTH}/GB)")
    print(f"⚠️ the $8 GB-project allowance means the MARGINAL cost is $0 until a "
          f"project's record exceeds {INCLUDED_DISK_GB} GB "
          f"({INCLUDED_DISK_GB / per_tenant_gb:.0f} tenants at this stock)")

    # ── IOPS: provisioned, so the binding quantity is the PEAK, not the total ─
    b = m["miss_blocks_median"]
    print("\n── the IOPS line (provisioned: peak, not total) ────────────")
    print(f"1 cache miss                    : {b:.0f} IOPS if the page must be read")
    print("3,000 included IOPS shared by 40 tenants (the §17.4.3 8 GB PRO "
          "density) = 75 IOPS/tenant")
    print(f"=> IOPS saturates at            : {75 / b:.1f} cold misses/second/tenant "
          f"= {75 / b * 3600:,.0f}/hour")
    print(f"=> at $0.024/IOPS/month, 1 extra provisioned IOPS costs "
          f"${RATE_IOPS_MONTH:.3f}/mo REGARDLESS of how many reads use it — the "
          f"line is a step, so rho_IOPS is $0 below the step")

    print("\n── the egress line ─────────────────────────────────────────")
    included_misses = INCLUDED_EGRESS_GB * 1e9 / m["miss_bytes_median"]
    print(f"1 cache miss returns            : {m['miss_bytes_median']:.0f} B "
          f"= ${m['miss_bytes_median'] / 1e9 * RATE_EGRESS_GB:.8f} of egress")
    print(f"250 GB included =               : {included_misses:,.0f} cold misses/month "
          f"= {included_misses / 30 / 86400:.0f}/second before egress costs anything")

    print("\n── the compute line ────────────────────────────────────────")
    print(f"1 keyed miss                    : {m['miss_ms_median']} ms")
    print(f"1 full-scope rebuild            : {m['full_scope_ms']} ms of one core "
          f"({m['full_scope_ms'] / 1000:.2f} CPU-seconds)")
    # A rebuild a day is the cheapest plausible cadence: 30 x that, in CPU-hours.
    print(f"=> a DAILY rebuild/tenant       : "
          f"{m['full_scope_ms'] * 30 / 1000 / 3600:.4f} CPU-hours/month")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default="postgresql://postgres:rho@127.0.0.1:15499/record")
    ap.add_argument("--tenants", type=int, default=2)
    ap.add_argument("--nodes-per-tenant", type=int, default=STOCK_NODES_PER_TENANT)
    ap.add_argument("--skip-plant", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    conn = psycopg2.connect(a.dsn)
    try:
        bootstrap(conn)
        if not a.skip_plant:
            plant(conn, a.tenants, a.nodes_per_tenant)
        m = measure(conn, a.tenants)
    finally:
        conn.close()
    if a.json:
        print(json.dumps(m, indent=2, default=str))
    else:
        arithmetic(m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
