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
tenant. The content width is CALIBRATED to the dogfood MEAN — measured
2026-10-17 in `graph-content-census.json` (`avg(size(content))` = **1,121.4 B**,
max 19,061 B, and 4,145/10,374 = **39.96%** of Points carry a 384-d vector) — and
the planted width is measured back and PRINTED — on BOTH bases, because they are
different populations: 1,121.4 B is the mean over the Points that CARRY content
(5,080 of 10,374 = 49.0%), while the mean over ALL Points is **549.1 B**
(5,696,946 B ÷ 10,374), which is what a per-NODE footprint needs. The plant
therefore NULLs content on 51% of rows, exactly as the dogfood graph does. ⚠️ **The dogfood TAIL (max 19,061 B, the TOAST regime) is
deliberately NOT planted**, so the per-tenant footprint this reports is a
**LOWER BOUND** for a tenant whose text hits the tail.

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
import time
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
    # FORWARD `ALTER TABLE public.point ADD COLUMN` (#2782's money value): the
    # row width this probe measures is the SHIPPED schema's, and a list behind
    # `record_projection.RECORD_MIGRATIONS` would measure five columns short.
    # Pinned by `tests/test_7950_capstone_probe.py`.
    "20261018000001_record_point_money_value_2782.sql",
]

#: §17.4.1's published rates, restated so the arithmetic below is auditable and
#: every figure it produces can be traced to one line of the doc.
RATE_DISK_GB_MONTH = 0.125  # Postgres General Purpose disk, per GB
RATE_IOPS_MONTH = 0.024  # provisioned IOPS beyond 3,000 included
INCLUDED_IOPS = 3000

#: The two tenant densities the docs quote: §17.4.3's 8 GB PRO cache density and
#: the 1,000-tenant Medium density. Both are DENSITY ASSUMPTIONS, not measurements;
#: the provisioned IOPS and the egress allowance are PROJECT-wide allowances, so
#: every per-tenant threshold must name the density it divides by.
DENSITIES = (40, 1_000)
RATE_EGRESS_GB = 0.09  # beyond 250 GB included
INCLUDED_EGRESS_GB = 250
INCLUDED_DISK_GB = 8  # per project, §17.4.1
BLOCK_BYTES = 8192  # Postgres page size

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
            "ON CONFLICT (id) DO NOTHING",
            (org, org, graph),
        )
        cur.execute(
            "INSERT INTO public.graphs (id, org_id, name, namespace) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (id) DO NOTHING",
            (graph, org, graph, graph),
        )
        cur.execute(
            "INSERT INTO public.node_identity (org_id, graph_id, node_label, node_key) "
            "SELECT %s, %s, 'Point', 'p-' || g FROM generate_series(1, %s) g "
            "ON CONFLICT DO NOTHING",
            (org, graph, nodes_per_tenant),
        )
        cur.execute(
            """
            INSERT INTO public.point
                (org_id, graph_id, node_key, node_label, id, content, status,
                 point_kind, embedding, created_at)
            SELECT %(org)s, %(graph)s, 'p-' || g, 'Point', 'p-' || g,
                   CASE WHEN g %% 100 < 49
                        THEN 'claim ' || g || ' ' || repeat('x', 1021 + (g %% 201))
                        ELSE NULL END,
                   'live', 'fact',
                   CASE WHEN g %% 5 < 2 THEN (
                        SELECT jsonb_agg(round(sin(g * 0.001 + j)::numeric, 6))
                          FROM generate_series(1, 384) j)
                   END,
                   '2026-10-17T00:00:00Z'
              FROM generate_series(1, %(n)s) g
            """,
            {"org": org, "graph": graph, "n": nodes_per_tenant},
        )
        print(f"planted tenant {org}: {nodes_per_tenant} points (40% with a 384-d vector)")
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
    return int(node.get("Shared Hit Blocks", 0)) + int(node.get("Shared Read Blocks", 0))


def measure(conn, tenants: int) -> dict:
    cur = conn.cursor()
    out: dict = {"tenants": tenants}

    # ── 1. bytes per record row, and the tenant's record footprint ───────────
    # The PLANTED content width, measured back — the docs cite THIS, not the
    # generator expression (the dogfood mean motivates the calibration; see the
    # module docstring — the 19 kB tail is deliberately NOT planted).
    cur.execute(
        "SELECT avg(length(content)), max(length(content)), "
        "       sum(coalesce(length(content), 0))::numeric / count(*), "
        "       avg(CASE WHEN content IS NOT NULL THEN 1.0 ELSE 0.0 END) "
        "FROM public.point WHERE org_id = %s AND graph_id = %s",
        ("rho7983-org-0", "rho7983-graph-0"),
    )
    avg_c, max_c, overall_c, carry = cur.fetchone()
    out["planted_content_bytes_avg"] = round(float(avg_c), 1)
    out["planted_content_bytes_max"] = int(max_c)
    out["planted_content_bytes_overall"] = round(float(overall_c), 1)
    out["planted_content_carry_share"] = round(float(carry), 4)
    cur.execute(
        "SELECT pg_column_size(t) FROM public.point t WHERE org_id = %s LIMIT 2000",
        ("rho7983-org-0",),
    )
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
    fetch_sql = "SELECT * FROM public.point WHERE org_id = %s AND graph_id = %s AND node_key = %s"
    probe_plans, egress = [], []
    for i in range(1, 201):
        key = f"p-{(i * 397) % STOCK_NODES_PER_TENANT + 1}"
        p = plan_of(cur, fetch_sql, (org, graph, key))
        probe_plans.append((blocks(p), float(p["Execution Time"])))
        cur.execute(
            "SELECT pg_column_size(t) FROM public.point t "
            "WHERE org_id = %s AND graph_id = %s AND node_key = %s",
            (org, graph, key),
        )
        egress.append(cur.fetchone()[0])
    out["miss_blocks_median"] = statistics.median(b for b, _ in probe_plans)
    out["miss_ms_median"] = round(statistics.median(m for _, m in probe_plans), 4)
    out["miss_bytes_median"] = statistics.median(egress)

    # ── 2b. the SAME miss, DETOASTED — what a real reader pays ───────────────
    # ⚠️ `EXPLAIN (ANALYZE)` on `SELECT *` sends its tuples to a dummy receiver,
    # which never runs an OUTPUT function — so a TOASTed jsonb column stays an
    # 18-byte POINTER and its TOAST heap is never read. `read_record_view` does
    # `SELECT *` and psycopg CONVERTS every column, so the real read detoasts.
    # `octet_length(t::text)` forces the same detoast server-side, and the wire
    # bytes are what egress is charged on.
    detoast_sql = (
        "SELECT octet_length(t::text) FROM public.point t "
        "WHERE org_id = %s AND graph_id = %s AND node_key = %s"
    )
    detoast_plans, wire = [], []
    for i in range(1, 201):
        key = f"p-{(i * 397) % STOCK_NODES_PER_TENANT + 1}"
        dp = plan_of(cur, detoast_sql, (org, graph, key))
        detoast_plans.append((blocks(dp), float(dp["Execution Time"])))
        cur.execute(detoast_sql, (org, graph, key))
        wire.append(cur.fetchone()[0])
    out["miss_blocks_detoast_median"] = statistics.median(b for b, _ in detoast_plans)
    # A MISS MIX is what egress and IOPS sum over, so the MEAN is the figure those
    # lines consume: 60% of a tenant's Points carry no vector, so the median
    # describes the common case and the mean describes the bill.
    out["miss_blocks_detoast_mean"] = round(statistics.fmean(b for b, _ in detoast_plans), 1)
    out["miss_blocks_detoast_max"] = max(b for b, _ in detoast_plans)
    out["miss_ms_detoast_median"] = round(statistics.median(m for _, m in detoast_plans), 4)
    out["miss_wire_bytes_median"] = statistics.median(wire)
    out["miss_wire_bytes_mean"] = round(statistics.fmean(wire), 1)
    out["miss_wire_bytes_max"] = max(wire)

    # The client-side cost of the REAL shape: fetch the row and convert it.
    t0 = time.perf_counter()
    for i in range(1, 51):
        key = f"p-{(i * 397) % STOCK_NODES_PER_TENANT + 1}"
        cur.execute(fetch_sql, (org, graph, key))
        cur.fetchall()
    out["miss_client_ms_median"] = round((time.perf_counter() - t0) / 50 * 1000, 4)

    # ── 3. a full-scope read: what a rebuild/replay pays ─────────────────────
    full = plan_of(
        cur, "SELECT * FROM public.point WHERE org_id = %s AND graph_id = %s", (org, graph)
    )
    out["full_scope_blocks"] = blocks(full)
    out["full_scope_ms"] = round(float(full["Execution Time"]), 2)
    out["full_scope_rows"] = int(full["Plan"]["Actual Rows"])

    full_d = plan_of(
        cur,
        "SELECT *, octet_length(t::text) FROM public.point t WHERE org_id = %s AND graph_id = %s",
        (org, graph),
    )
    out["full_scope_blocks_detoast"] = blocks(full_d)
    out["full_scope_ms_detoast"] = round(float(full_d["Execution Time"]), 2)
    return out


def arithmetic(m: dict) -> None:
    per_tenant_gb = m["bytes_per_tenant"] / 1e9
    disk_line = per_tenant_gb * RATE_DISK_GB_MONTH
    print("\n── measured ────────────────────────────────────────────────")
    # BOTH means, because they are different populations and the footprint needs
    # the OVERALL one: `avg()` skips NULLs, so quoting 1,121.4 B per NODE applies
    # the content-carrying mean to the ~51% of Points that carry none.
    print(
        f"planted content width          : {m['planted_content_bytes_avg']:.0f} B mean "
        f"over the {m['planted_content_carry_share']:.1%} of Points that CARRY "
        f"content, {m['planted_content_bytes_overall']:.0f} B over ALL of them "
        f"(dogfood: 1,121.4 B / 549.1 B at the same two bases)"
    )
    print(
        "  ⚠️ the dogfood's 19,061 B content TAIL is NOT planted — no TOAST regime "
        "— so the footprint below is a LOWER BOUND for a tenant that hits it"
    )
    print(
        f"record row width                : {m['row_bytes_median']:.0f} B median, "
        f"{m['row_bytes_mean']:.0f} B mean (n=2000 rows, 40% with a 384-d vector)"
    )
    print(
        f"record footprint per tenant     : {m['bytes_per_tenant']:,} B "
        f"= {per_tenant_gb:.4f} GB ({m['tenants']} tenants, "
        f"{STOCK_NODES_PER_TENANT:,} nodes each)"
    )
    per_node = m["bytes_per_tenant"] / STOCK_NODES_PER_TENANT
    print(
        "  by table (all tenants)        : "
        + ", ".join(f"{t} {b:,} B" for t, b in m["table_bytes"].items())
    )
    print(
        f"  per node on disk              : {per_node:,.0f} B "
        f"(heap + indexes + TOAST) — against §17.4.2's 1,672 B per RESIDENT "
        f"node in the CACHE"
    )
    print(
        f"keyed miss fetch                : {m['miss_blocks_detoast_median']:.0f} blocks "
        f"({m['miss_blocks_detoast_median'] * BLOCK_BYTES / 1024:.0f} KiB logical) · "
        f"{m['miss_ms_detoast_median']} ms server · {m['miss_client_ms_median']} ms end-to-end "
        f"· {m['miss_wire_bytes_median']:,.0f} B on the wire"
    )
    print(
        f"  (heap-only, NO detoast)       : {m['miss_blocks_median']:.0f} blocks · "
        f"{m['miss_ms_median']} ms — what `EXPLAIN (ANALYZE)` on `SELECT *` reports, "
        f"and it UNDERCOUNTS: the dummy receiver never runs an output function, so "
        f"the TOASTed vector stays an 18-B pointer"
    )
    print(
        f"full-scope read (rebuild)       : {m['full_scope_blocks_detoast']:,} blocks "
        f"({m['full_scope_blocks_detoast'] * BLOCK_BYTES / 1e6:.1f} MB logical) · "
        f"{m['full_scope_ms_detoast']} ms · {m['full_scope_rows']:,} rows"
    )
    print(
        f"  (heap-only, NO detoast)       : {m['full_scope_blocks']:,} blocks · "
        f"{m['full_scope_ms']} ms — the same undercount, "
        f"{m['full_scope_blocks_detoast'] / max(m['full_scope_blocks'], 1):.1f}x too small"
    )

    print("\n── the disk line (rho's denominator) ───────────────────────")
    print(
        f"disk per tenant per month       : ${disk_line:.4f} "
        f"({per_tenant_gb:.4f} GB x ${RATE_DISK_GB_MONTH}/GB)"
    )
    print(
        f"⚠️ the $8 GB-project allowance means the MARGINAL cost is $0 until a "
        f"project's record exceeds {INCLUDED_DISK_GB} GB "
        f"({INCLUDED_DISK_GB / per_tenant_gb:.0f} tenants at this stock)"
    )

    # ── IOPS: provisioned, so the binding quantity is the PEAK, not the total ─
    b = m["miss_blocks_detoast_mean"]
    print("\n── the IOPS line (provisioned: peak, not total) ────────────")
    print(
        f"1 cache miss                    : {b:.1f} BLOCKS/miss MEAN over the miss "
        f"mix ({m['miss_blocks_detoast_median']:.0f} median, "
        f"{m['miss_blocks_detoast_max']} max) = that many IOPS if every miss is a "
        f"cold page read"
    )
    # BOTH densities: the allowance is PROJECT-wide, so a per-tenant threshold has
    # to name the density it divides by. Quoting one density's IOPS next to the
    # other's egress is how a 40x error entered this report's first draft.
    for t in DENSITIES:
        ips = INCLUDED_IOPS / t
        print(
            f"3,000 included IOPS / {t:,} tenants = {ips:.2f} IOPS/tenant "
            f"=> {ips / b:.2f} cold misses/s/tenant ({ips / b * 3600:,.0f}/hour)"
        )
    print(
        f"=> at $0.024/IOPS/month, 1 extra provisioned IOPS costs "
        f"${RATE_IOPS_MONTH:.3f}/mo REGARDLESS of how many reads use it — the "
        f"line is a step, so rho_IOPS is $0 below the step"
    )

    print("\n── the egress line ─────────────────────────────────────────")
    included_misses = INCLUDED_EGRESS_GB * 1e9 / m["miss_wire_bytes_mean"]
    print(
        f"1 cache miss returns            : {m['miss_wire_bytes_mean']:,.0f} B mean "
        f"({m['miss_wire_bytes_median']:,} median, {m['miss_wire_bytes_max']:,} max) "
        f"= ${m['miss_wire_bytes_mean'] / 1e9 * RATE_EGRESS_GB:.8f} of egress"
    )
    print(
        f"250 GB included =               : {included_misses:,.0f} cold misses/month "
        f"PROJECT-WIDE — the allowance is not per tenant"
    )
    for t in DENSITIES:
        per_t = included_misses / t
        print(
            f"  ⇒ per tenant at {t:,} tenants  : {per_t:,.0f} misses/month "
            f"= {per_t / 30 / 86400:.3f}/s — the FIRST line to leave $0; each miss "
            f"above it adds ${m['miss_wire_bytes_mean'] / 1e9 * RATE_EGRESS_GB:.1e}"
        )

    print("\n── the compute line ────────────────────────────────────────")
    print(
        f"1 keyed miss                    : {m['miss_client_ms_median']} ms end-to-end "
        f"({m['miss_ms_detoast_median']} ms server)"
    )
    print(
        f"1 full-scope rebuild            : {m['full_scope_ms_detoast']} ms of one core "
        f"({m['full_scope_ms_detoast'] / 1000:.2f} CPU-seconds)"
    )
    # A rebuild a day is the cheapest plausible cadence: 30 x that, in CPU-hours.
    print(
        f"=> a DAILY rebuild/tenant       : "
        f"{m['full_scope_ms_detoast'] * 30 / 1000 / 3600:.4f} CPU-hours/month"
    )


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
