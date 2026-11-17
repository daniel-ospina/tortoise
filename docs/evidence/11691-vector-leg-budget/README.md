---
title: "The record's write-side vector maintenance, priced (#11691)"
type: evidence
domain: platform
doc_status: live
created: 2026-11-17
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutSubjects: search
aboutObjects: "#11691, #11642, #4051, pgvector, public.point, product/pricing.json"
---

# #11691 — what the generated column and the HNSW indexes cost on EVERY record write

`supabase/migrations/20261109000001_record_embedding_vector.sql` (`#11642`,
`#4051` V1) added a `GENERATED ALWAYS … STORED` `embedding_vec vector(384)` column
to `public.point` / `public.source` / `public.event` and three HNSW indexes.
**That cost is paid on every record write from the day the migration is applied —
armed or not.** It is not gated on `TORTOISE_RECORD_VECTOR_LEG`, on the cutover,
or on anything an operator can switch off, and until `#11691` it had never been
priced: `docs/evidence/11642-vector-move/README.md` prices the BUILD (4.0–4.2 s
for the index at 20k rows, 6.9 s for the generated-column rewrite) and nothing
about the ongoing line.

This file is that line. Every number below is a field in
[`measurement.json`](./measurement.json), produced by
[`tools/record_vector_write_cost_11691.py`](../../../tools/record_vector_write_cost_11691.py);
re-derive rather than trust — the run command is at the bottom.

```text
date       2026-11-17T03:19:15Z
server     PostgreSQL 16.16 (Debian 16.16-1.pgdg12+1), pgvector 0.8.7
image      pgvector/pgvector:pg16 (throwaway container, one run)
corpus     50 centroids + gaussian noise, 384-d, STORED AS THE RECORD STORES IT
           (`jsonb` — 3,608 B of text/row on average, `corpus.text_bytes_per_row_mean`
           = 3,607.6 over `_corpus(50, seed=11691)`, not pgvector's 1,536 B)
shapes     point_plain      the record's columns, no generated column, no index
           point_gen_only   + `embedding_vec` GENERATED … STORED   (no index)
           point            + `embedding_vec` and the HNSW index   (= production)
           ⚠️ `… INCLUDING GENERATED` copies the GENERATED expression but NOT the
           primary key, so `point` carries `point_pkey` and the two controls carry
           no index at all — every index figure below is net of that split (§1.5)
host       macOS-27.0 arm64, loadavg 45.7 / 45.3 / 50.4 at the start
```

⛔ **TWO METHOD CHOICES THAT DECIDE WHETHER THE NUMBERS MEAN ANYTHING**, both
because this box runs a fleet:

1. **Every timing is the statement's OWN execution time**, read from
   `EXPLAIN (ANALYZE, FORMAT JSON)` → `Execution Time`. Not client wall clock: a
   measured `SELECT 1` round trip on this box is ~6 ms of pure scheduling, and the
   `COMMIT` fsync is the storage layer's cost, not the index's.
2. **The three shapes are INTERLEAVED and the deltas are PAIRED per round**: each
   round writes a small batch into all three, the round's median per shape is
   taken, and the reported delta is a percentile of *(shape − next-cheaper shape)*
   over those rounds. A sequential run would attribute a load spike to whichever
   table was being measured when it landed. The unpaired percentile differences are
   in the artifact too, next to the plain shape's own p95 — the honest reading of
   the unpaired p95 is "≈68 ms of which ≈3 ms is this box's noise floor".

## 1. ms added per record write

| shape | p50 at 1,200 rows | p50 at 20,220 rows (production-sized) |
|---|---|---|
| `point_plain` — no vector column, no index | 0.293 ms | 0.272 ms |
| `point_gen_only` — generated column, no index | 0.667 ms | 0.611 ms |
| `point` — generated column **+ HNSW** (production) | 3.006 ms | 6.090 ms |

**Paired attribution** (median over interleaved rounds; the load-robust figure):

| added cost | at 1,200 rows | at 20,220 rows |
|---|---|---|
| the generated column (`record_embedding_vector` + the stored vector) | **+0.375 ms** | **+0.303 ms** |
| the HNSW maintenance | **+2.367 ms** | **+3.721 ms** |
| **both** (p50) | **+2.758 ms** | **+3.987 ms** |
| both (p95) | +6.168 ms | +23.483 ms |

⚠️ **Read the p50 as the cost and the p95 as an upper bound, not as a latency.**
The plain insert's own p95 on this host is 3.0–3.2 ms against a 0.27 ms p50 — the
tail is scheduler preemption on a box at load ~48, not insert work. The paired p95
is reported because the column belongs in the record; it is not evidence of a
6–23 ms insert.

**HNSW's insert cost grows with the graph it inserts into**, which is why both
populations are shown: +2.37 ms into a 1,200-row graph, +3.72 ms into a 20,220-row
one. Production is the second column.

## 2. Bytes per 1,000 writes

Measured as size deltas over exactly the writes the timings above describe:

| component | bytes per 1,000 writes | per write |
|---|---|---|
| the HNSW index (`idx_point_embedding_vec_hnsw`) | **2,054,827** | 2,055 B |
| the primary key's own index (`point_pkey`) | **75,093** | 75 B |
| the generated column's payload (vector + heap tuple) | **1,945,600** | 1,946 B |
| **total** | **4,075,520** | 4,076 B |

⛔ **THE THREE ROWS ARE SPLIT BECAUSE THE CONTROLS DO NOT CARRY A PRIMARY KEY**
(#11691 review F2). `point_gen_only` / `point_plain` are derived with `LIKE …`,
which copies no primary key, so `point` holds `point_pkey` (90,112 B at 1,200
writes) and the controls hold no index at all: a single "all index bytes of
`point` minus the controls' zero" figure is **2,129,920 B/1,000** and charges the
pkey's growth to the HNSW index. The HNSW row above is therefore read from that
index's OWN byte count in `measurement.json`'s `indexes` map, and
`primary_key_bytes_per_1000_writes` reports the remainder — the total is
unchanged, only its attribution is now auditable
(`2,054,826.7 + 75,093.3 = 2,129,920.0`).

The index figure reproduces as a STOCK too, which is the cross-check that matters:
built to **20,000 rows** the index is **40,968,192 B = 39.07 MiB**, and `#11642`
independently measured **39 MB** for the same index at the same base — two
measurements, one number.

## 3. At current production volume, in `product/pricing.json`'s units

Production, 2026-11-16 (`#11691`): **33,671** `public.point` rows, 27,813 carrying
`embedding_vec`. Sizes below are the measured bytes/row × that row count (an HNSW
graph's size is linear in rows at fixed `M`; the derivation is a field in the
artifact, with its inputs). ⚠️ **27,813 is a QUOTED INPUT, not a figure this
measurement derived** — it is the live record's own count from the issue and
appears in no artifact field, because nothing here depends on it (the
extrapolation uses the total row count). The derived figures are 33,671 and the
bytes below.

| quantity at 33,671 rows | value |
|---|---|
| the vector payload + index (total relation) | **233.4 MB** |
| the indexes, all (`idx_point_embedding_vec_hnsw` + `point_pkey`) | **70.8 MB** |
| of which the HNSW index's own bytes | **69.0 MB** |
| of which `point_pkey` | **1.9 MB** |
| monthly, at `postgres_storage.cost_basis_usd_per_gb_month` (**$0.125/GB-month**) | **$0.029/month** |
| monthly, at the customer-facing `overage_price_usd_per_gb_month` (**$0.30/GB-month**) | **$0.070/month** |
| the indexes, all, cost basis | **$0.0089/month** |

**The FLOW**, at the write volumes `pricing.json` itself publishes per tier
(`included_write_ops_per_month`), priced the same two ways:

| tier | writes/month | $/month (cost basis, $0.125/GB-mo) | $/month (overage, $0.30/GB-mo) |
|---|---|---|---|
| anon | 1,000 | $0.0005 | $0.0012 |
| free / solo | 10,000 | $0.0051 | $0.0122 |
| pro | 50,000 | $0.0255 | $0.0611 |
| team | 200,000 | $0.1019 | $0.2445 |

**The TIME**, in the unit `pricing.json` bills on — `overage_unit: write_ops`,
$5.00 per 10,000 operations. The index does **not** change the number of write ops
billed (the price is per operation, not per millisecond); what it consumes is
capacity. At the measured **+3.987 ms p50 per write**:

| tier | writes/month | extra DB time per month |
|---|---|---|
| free / solo | 10,000 | **39.9 s** |
| pro | 50,000 | **199 s (3.3 min)** |
| team | 200,000 | **797 s (13.3 min)** |

## 4. What this does NOT measure — named, not implied

- **`source` and `event` writes.** The migration gives all three tables the column
  and an index; only `point` is priced here, because `point` is the population the
  issue measured (33,671 rows). `source` is ~512 rows on the live record and
  `event` is not the per-capture hot path; a lane that needs those deltas re-runs
  this harness against `public.source`/`public.event` (the DDL is applied to all
  three stand-ins already).
- **The full record schema.** The stand-ins carry the columns a write touches, not
  the RLS policies, triggers and foreign keys around them. Every absolute figure is
  therefore a LOWER bound on the real write, identically for all three shapes — so
  the DELTAS are the features' own cost, and the absolute p50s are not a
  production-latency claim.
- **The read side.** `#11642` prices the ANN read; this file prices the write.
- **The backfill.** A generated column needs no backfill statement (`#11642` §2:
  6.9 s for the rewrite itself at 20k rows), so there is nothing separate to price.

## 5. Re-run it

```bash
uv run --with psycopg2-binary python tools/record_vector_write_cost_11691.py \
    --json-out docs/evidence/11691-vector-leg-budget/measurement.json

# a RATE in product/pricing.json moved and the measurement did not? The derived
# half (deltas, production scale, dollars) re-derives from the committed artifact
# WITHOUT re-taking the 35-minute measurement:
uv run --with psycopg2-binary python tools/record_vector_write_cost_11691.py \
    --recompute docs/evidence/11691-vector-leg-budget/measurement.json
```

It starts its own throwaway `pgvector/pgvector:pg16`, applies the migration's own
`ALTER TABLE … ADD COLUMN` and `CREATE INDEX` statements **verbatim** (extracted
from the file, never retyped), derives its two controls from the real shape with
`CREATE TABLE … (LIKE point INCLUDING GENERATED)`, and removes the container. On a
loaded box it takes ~35 minutes; `--scale-rows` lowers the scale phase,
`--dsn` points it at a Postgres you already have instead. The per-round samples are
NOT kept in the artifact (they are bulk), so `--recompute` carries the paired
deltas through unchanged and re-derives everything downstream of them — which is
exactly the half a rate change moves.

⚠️ One trap found the hard way and now pinned in the harness's comment: a **bare
`LIKE` copies neither default nor generation expressions**, so the first version's
"generated column" control was a plain always-NULL `vector(384)` column — and it
reported the generated column as costing **0 ms and 0 bytes**. `INCLUDING
GENERATED` is what makes the control a control.
