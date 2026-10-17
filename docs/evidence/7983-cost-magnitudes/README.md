---
title: "#7983 — the two magnitudes §17.4 needed: `f` and `rho` — what is measured, what is not"
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
(`tortoise/read_ledger.py` plus the `_ledger_record` hook at `query`, `paginated_query`, `get_point`
and `query_points_by_tag`, the FTS funnel recording inline — five read paths; graph expansion and
three direct-Cypher readers are NOT hooked, see #8877) is the only change.

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
| **`f` — the hot fraction** | ⛔ **no instrument existed** | shipped the **read ledger** (`tortoise/read_ledger.py`, hooked into five row-returning read paths) | **no tenant value, and none is claimed.** The instrument's own exercise on the live self-hosted graph: **`f` = 8.64%** over 200 proxy queries — **a proxy mix, not a tenant** |
| **`rho` — the record's non-storage lines** | ⚠️ **partly** — the record store is not deployed, but its SCHEMA is committed and its read path is code | measured the unit costs on the committed record schema | **`rho` = 3.54** at the doc's 1,000-tenant density (**88.5** at its 40-tenant cache density) — ⚠️ **the disk line is measured, the compute+plan lines are the doc's DENSITY ASSUMPTIONS**, so `rho` is an assumption over a measurement, and only the disk line is a value |

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
stock            10,457 keys / 21,110 nodes   (Point 10,396 · Source 60 · Event 1 · Subject/Object/Session 0)
read events      2,000   (200 queries × 10 results — a PROXY mix, sampled at random from a 2,000-row pool)
distinct read    904 keys  ⇒ f = 8.64%
never read       9,553 keys = 15.97 MB   (bytes attributed at §17.4.2's measured 1,672 B/resident node)
nodes before/after  21,110 / 21,110       (the probe wrote nothing)
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
(`graph-content-census.json`, measured 2026-10-17 at this same commit: **4,145 / 10,374 = 39.96%** of
Points carry a vector; content is **1,121.4 B over the 49.0% of Points that carry any**
(5,696,946 ÷ 1,121.4 = 5,080 of 10,374) and **549.1 B over all of them**). ⚠️ **Two populations, and
the footprint needs the second**: `avg()` skips NULLs, so applying the carrying mean to every node
would overstate the row by its dominant column.

| measured | value | base |
|---|---|---|
| planted content width | **1,133 B** over the **49.0%** that carry content · **555 B over ALL** | calibrated to the dogfood's two means (1,121.4 / 549.1) and measured back. ⚠️ The dogfood **19,061 B tail is NOT planted** (no TOAST regime) ⇒ the footprint is a **LOWER BOUND** for a tenant that hits it |
| record row width | **1,263–1,266 B median / 1,421–1,423 B mean** | n = 2,000 rows per run, two runs |
| record footprint per tenant | **191,643,648 B = 0.1916 GB** ⇒ **2,136 B/node on disk** (**1.28×** the cache's 0.1499 GB) | heap + indexes + TOAST |
| keyed miss fetch | **4 logical blocks (32 KiB) · 1,286 B out · 0.030–0.092 ms** | the record's own PK probe, 200 samples × two runs |
| full-scope read (rebuild) | **7,962 blocks (65.2 MB logical) · 34.7–105.8 ms** | `read_record_view`'s query shape, 89,701 rows, two runs |

**Blocks are exact; milliseconds are a range over the two runs in `rho-bench.log` (shared-CPU
container, default settings) — an accounting bench, not a vendor benchmark.**

```
disk      = $0.125 × 0.1916 GB               = $0.0240 /tenant/month   (MEASURED GB × the plan's AVERAGE rate; the plan includes 8 GB/project ≈ 41.8 tenants of this stock, so the MARGINAL byte is $0 until then — rho is an average-cost ratio. The doc used $0.0187 from the CACHE's 0.1499 GB)
compute   = $60 ÷ 1,000                      = $0.060                  (density assumption)
plan      = $25 ÷ 1,000                      = $0.025                  (density assumption)
IOPS      = $0   (4 blocks/miss ⇒ the step is 18.75 cold misses/s/tenant at 40 tenants, 0.75 at 1,000)
egress    = $0   ONLY below the project's 250 GB allowance: 1,286 B/miss ⇒ 194,476,857 misses/month SHARED by the tenants (the log divides by the UNROUNDED 1,285.5 B median, which the row above rounds to 1,286 B: 250 GB ÷ 194,476,857 = 1,285.5 B)
                  ⇒ 1.9 misses/s/tenant at 40 tenants, 0.075 at 1,000 — the FIRST line to leave $0; each miss above it adds $1.2e-7
                  of egress and RAISES rho
rho       = (0.060 + 0.025) ÷ 0.0240         = 3.54

ratio     = cache line ÷ record disk line, in DOLLARS (the closed form's input)
            (the division uses the UNROUNDED lines, so it re-derives from the log)
            1 GB STARTUP cache, 1,000 tenants : $14.5948 ÷ $0.023955 = 609 ⇒ the split pays BELOW f = 99.25%; f=1 → 0.993×
            8 GB PRO cache,   40 tenants      : $ 8.7469 ÷ $0.023955 = 365 ⇒ the split pays BELOW f = 75.5%;  f=1 → 0.803×
            (a LOWER break-even is the HARDER bar: at 40 tenants a quarter of the cache must be cold, at 1,000 tenants 0.75%)
```

**Two findings the model needs:**

1. **The record's footprint is 1.28× the cache's for the SAME node COUNT** (0.1916 GB vs 0.1499 GB),
   because the record stores the 384-d vector as `jsonb` decimal text where the cache stores
   `vecf32` — the encoding gap §5.2 measured on the journal (~4×), now measured on the record.
   ⚠️ **Indicative, not a per-node ratio:** the bench plants 89,701 `:Point`s while the cache's
   1,672 B/node is a blend over the live graph's real mix (~half `GraphEvent`). The closed form uses
   dollars on both lines and never divides one node count by the other. The
   planted vector used 6 decimal places (~9.5 chars/float) where §5.2 measured the journal's real
   encoding at 21.9 chars/float, so **0.1916 GB is a LOWER BOUND at that column type**.
2. **`rho` is dominated by TENANT DENSITY, not by any per-read cost.** At §17.4.3's 40-tenant
   density BOTH density-scaled lines move — compute `$60 ÷ 40 = $1.50` and plan `$25 ÷ 40 = $0.625`
   ⇒ **`rho` = (1.50 + 0.625) ÷ 0.0240 = 88.5**; at 1,000 tenants the same two lines give **3.54**.
   ⚠️ **Three bases, not two** — a density, its own cache tier, and the measured disk line. Pairing
   each density with ITS cache rate gives `ratio` **609** (1 GB STARTUP, 1,000 tenants) and **365**
   (8 GB PRO, 40 tenants); `multiplier = 1/(f + (1+rho)/ratio)` then gives:
   **`f` below 99.25% ⇒ the split pays at 1,000 tenants** (only 0.75% of the cache must be cold) and
   **`f` below 75.5% at 40 tenants** (a quarter of it must be) — the 40-tenant case is the HARDER one.
   `f = 1` is 0.993× / 0.803× in both, so at today's keep-everything policy nothing is saved.
   (Dividing the measured DISK LINE — the only part of `rho` that is a value, since compute and plan are density assumptions — by §17.4.4's pre-measurement `778.7` mixes a price with a quantity:
   778.7 is a RATE ratio, valid only while the record occupies the cache's own 0.1499 GB.) The
   coherent pair is 25× apart and no per-read measurement can resolve it: 4 blocks and 30–92 µs
   per miss do not make a compute tier. ⚠️ **The egress allowance is shared by the project**, so it
   holds only up to 1.9 misses/s/tenant at 40 tenants (0.075 at 1,000) — a real mix can exceed that
   and raise `rho`. **Neither a floor nor a ceiling:** the encoding bound and the miss-rate bound
   point opposite ways, and the density moves it 25×; the figures are quotable only with their bases.

## 4. What is still unmeasured, and the seam it needs

- **`f` for a tenant**: needs a window over a real query mix. The ledger provides the mechanism;
  a deployed tenant with an on workload provides the sample. **Do not substitute a fresh ledger's
  first report** — "nobody has read anything since we started watching" is not "nothing is read".
- **the miss RATE** (and therefore the compute tier): the record serves no tenant today.
- **per-key `bytes`**: FalkorDB exposes no per-node byte figure, so the ledger's byte denominator is
  §17.4.2's measured unit, not each key's own width. The seam is the record's row widths
  (`pg_column_size`) registered as stock at projection time — **a schema-shaped change, deliberately
  not taken here.**
- **stock registration by the product**: the ledger learns a key exists only when a caller says so
  (today, the census probe). Enabling `TORTOISE_READ_LEDGER` without a stock census makes every read
  `unregistered` and leaves `f` without a denominator.
- **three read paths the ledger cannot count**, all biasing `f` LOW (so they flatter the split):
  **graph expansion** (`recall_subgraph` returns expander nodes, not keyed lookups); **the state and
  traversal paths** (`traverse`, `recall_state`'s related-point/object readers,
  `get_historical_knowledge`) plus the FTS funnel's candidate POOL (only the returned rows are
  counted); and **the `Subject`/`Object`/`Source` legs of the FTS funnel**, whose rows are
  `SearchResult.to_dict()` and carry `id`/`eventId`/`sourcePath` — never `name`/`url` — so they land in
  `unresolved` rather than in a joinable coordinate. All three are **#8877**.

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
