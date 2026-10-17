---
title: "#7983 — the two magnitudes §17.4 needed: `f` and `rho`, measured"
type: evidence
domain: platform
doc_status: final
created: 2026-10-17
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#7983, #7954, #7950, STORAGE-ARCHITECTURE.md §17.4, Postgres record, FalkorDB cache"
---

# `#7983` — `f` and `rho`: what is measured, what is not, and the instrument in between

**Measured at:** `measure/7983-hot-fraction-and-rho@459f22912edf4893fb70144d42e97df389479ed5`
on **2026-10-17**. Everything below is measured on that tree; the code this lane adds
(`tortoise/read_ledger.py`, the one-line hook in `sdk.tortoise_fts_query`) is the only change.

**The design being measured:** `docs/architecture/STORAGE-ARCHITECTURE.md` **§17.4** — the two-store
cost model whose closed form is `1 / (f + (1 + rho) / ratio)`. It is complete except for two
magnitudes, and this issue is those two magnitudes.

> **Every quantity carries its base, its denominator, its sample and its date.** That is the rule
> §17.4.2 sets for this document's own figures (it has erred three times on denominators), and it
> applies to this evidence file too.

---

## Verdict, in one table

| quantity | measurable at `459f22912`? | what this lane did | measured value |
|---|---|---|---|
| **`f` — the hot fraction** | ⛔ **no instrument existed** | shipped the **read ledger** (`tortoise/read_ledger.py` + the one-funnel hook) | **no tenant value, and none is claimed.** The instrument's own exercise on the live self-hosted graph: **`f` = 2.07%** over 200 proxy queries — **a proxy mix, not a tenant** |
| **`rho` — the record's non-storage lines** | ⚠️ **partly** — the record store is not deployed, but its SCHEMA is committed and its read path is code | measured the unit costs on the committed record schema | **`rho` = 2.50** at the doc's 1,000-tenant density (**62.5** at its 40-tenant cache density) — the term that decides it is TENANT DENSITY, not any per-read cost |

---

## 1. The census — what the tree held BEFORE (this is the deliverable's first half)

`census-7983.log`, produced by `probes/census_7983.py --base 459f22912` (so the lane's own change
cannot appear in its own census; every grep uncapped):

| question | measured answer |
|---|---|
| a persisted per-key read fact in product source? | **no** — `hit_count` is `read_status`'s in-flight, per-request count; `last_reads` in `tools/longmem_eval/retrieve.py` is a local retry variable |
| a query-log / read-ledger TABLE? | **none** (`supabase/migrations` → 0 matches) |
| is the residency declaration a read fact? | **no** — its ONLY writer is `cache_projection.declare_residency`, so it records what the projection WROTE |
| a read-VOLUME meter? | **yes** — `metering_records.retrieval_*` (`#4489`): HOW MANY reads, never WHICH keys; a no-op on self-hosted unless a tally is armed |

**⇒ `f` was a policy variable (§2.0's "keep the limit in falkor high" IS `f = 1`), which is why
§17.4.3's table was a sweep.** The issue's own indicator — *"measured from the query surface"* — was
not achievable from the query surface; what was missing is a **persisted fact**, and now there is one.

## 2. `f` — the instrument, and the one reading it can honestly give today

`probes/ledger_probe.py` (read-only; it asserts the graph's node count is unchanged afterwards):

```
graph            tortoise (self-hosted, docker://:@localhost:16379/tortoise)
stock            10,435 keys / 21,065 nodes   (Point 10,374 · Source 60 · Event 1 · Subject/Object/Session 0)
read events      2,000   (200 queries × 10 results — a PROXY mix, built from the graph's own Point contents)
distinct read    216 keys  ⇒ f = 2.07%
never read       10,219 keys = 17.09 MB   (bytes attributed at §17.4.2's measured 1,672 B/resident node)
nodes before/after  21,065 / 21,065       (the probe wrote nothing)
```

⚠️ **This is an instrument exercise, not `f` for the cost model.** The mix is a proxy, the window is
one run, and the graph is the fleet's own map instance — not a tenant with a workload. What it
establishes is narrower and still useful: **the ledger records what a read actually touched, and the
zero-read mass is a number.** The tenant measurement needs a window over a real query mix, and
hosted capture is off by owner ruling (2026-10-14).

`ledger-probe-warnings.summary.txt` records the 96 product-level read-path warnings emitted during the
probe (a bounded relationship read timing out on the live instance) — all caught by the product, and
the answers were unaffected: 200 × 10 = exactly 2,000 read events.

## 3. `rho` — the unit costs, measured on the committed record schema

`probes/record_read_cost.py`, `rho-bench.log`: a throwaway `postgres:16` carrying the **committed**
record migrations (`2026101200000{1..4}_record_*.sql` + `20261014000001`), **two tenants** planted at
§17.4.2's stock (**89,701 nodes each**, so the per-tenant figure is the measured total ÷ 2 and not an
extrapolation), with the content width and vector share taken from the dogfood graph
(`graph-content-census.json`, measured 2026-10-17 at this same commit: `avg(size(content))` =
**1,121.4 B**, max 19,061 B; **4,145 / 10,374 = 39.96%** of Points carry a vector).

| measured | value | base |
|---|---|---|
| record row width | **1,633–1,639 B median / 2,296–2,304 B mean** | n = 2,000 rows per run, two runs |
| record footprint per tenant | **272,125,952 B = 0.2721 GB** ⇒ **3,034 B/node on disk** | heap + indexes + TOAST |
| keyed miss fetch | **4 logical blocks (32 KiB) · 1,656 B out · 0.0625–0.085 ms** | the record's own PK probe, 200 samples × two runs |
| full-scope read (rebuild) | **35,402 blocks (290 MB logical) · 288–483 ms** | `read_record_view`'s query shape, 89,701 rows, two runs |

**Blocks are exact; milliseconds are a range over the two runs in `rho-bench.log` (shared-CPU
container, default settings) — an accounting bench, not a vendor benchmark.**

```
disk      = $0.125 × 0.2721 GB               = $0.0340 /tenant/month   (MEASURED; the doc used $0.0187 from the CACHE's 0.1499 GB)
compute   = $60 ÷ 1,000                      = $0.060                  (density assumption)
plan      = $25 ÷ 1,000                      = $0.025                  (density assumption)
IOPS      = $0   (4 blocks/miss ⇒ the 3,000-included step is at 18.8 cold misses/s/tenant)
egress    = $0   (1,656 B/miss ⇒ the 250 GB allowance is 151M misses/month = 58/s)
rho       = (0.060 + 0.025) ÷ 0.0340         = 2.50
```

**Two findings the model needs:**

1. **The record's footprint is 1.81× the cache's for the SAME nodes** (0.2721 GB vs 0.1499 GB),
   because the record stores the 384-d vector as `jsonb` decimal text where the cache stores
   `vecf32` — the encoding gap §5.2 measured on the journal (~4×), now measured on the record. The
   planted vector used 6 decimal places (~9.5 chars/float) where §5.2 measured the journal's real
   encoding at 21.9 chars/float, so **0.2721 GB is a LOWER BOUND at that column type**.
2. **`rho` is dominated by TENANT DENSITY, not by any per-read cost.** At §17.4.3's 40-tenant
   density BOTH density-scaled lines move — compute `$60 ÷ 40 = $1.50` and plan `$25 ÷ 40 = $0.625`
   ⇒ **`rho` = (1.50 + 0.625) ÷ 0.0340 = 62.5** ⇒ break-even `f` **91.85%**; at 1,000 tenants the
   same two lines give **2.50** ⇒ **99.55%**. ⚠️ **The basis must be the same on both lines** —
   compute ÷ 40 with plan ÷ 1,000 mixes two densities and lands on a figure that belongs to neither.
   The coherent pair is 25× apart, and no per-read measurement can resolve it: 4 blocks and 60 µs
   per miss do not make a compute tier.

## 4. What is still unmeasured, and the seam it needs

- **`f` for a tenant**: needs a window over a real query mix. The ledger provides the mechanism;
  a deployed tenant with an on workload provides the sample. **Do not substitute a fresh ledger's
  first report** — "nobody has read anything since we started watching" is not "nothing is read".
- **the miss RATE** (and therefore the compute tier): the record serves no tenant today.
- **per-key `bytes`**: FalkorDB exposes no per-node byte figure, so the ledger's byte denominator is
  §17.4.2's measured unit, not each key's own width. The seam is the record's row widths
  (`pg_column_size`) registered as stock at projection time — **a schema-shaped change, deliberately
  not taken here.**

---

## Files

| file | what it is |
|---|---|
| `census-7983.log` | the before-census (instrument absence, dated, at the base commit) |
| `probes/census_7983.py` | reproduces it (`--base <sha>` measures the committed tree) |
| `probes/ledger_probe.py` · `ledger-probe.json` | the ledger exercised on the live self-hosted graph |
| `ledger-probe-warnings.summary.txt` | the probe's product-level warnings, summarised and attributed |
| `probes/record_read_cost.py` · `rho-bench.log` | `rho`'s unit costs on the committed record schema (two runs) |
| `probes/graph_content_census.py` · `graph-content-census.json` | the dogfood graph's shape: what the bench planted and why those widths |
