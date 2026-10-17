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

**Measured on:** `2026-10-17`, on branch `measure/7983-hot-fraction-and-rho` — the rho bench and
both ledger probes were run from the lane's own commits (the ledger instrument does not exist at the
base commit, so the base `459f22912` is the tree the numbers are recorded INTO, not the tree they were
produced on; each file below names its script and the log it produced). The code this lane adds
(`tortoise/read_ledger.py` plus the `_ledger_record` hook; §17.4.6 names which read paths record — this file does not restate that list; the SDK's own population scans and
CANDIDATE-pool reads pass `_record_reads=False` — one `recall_gaps()` call would otherwise record the whole stock —
and `get_point` is deliberately NOT hooked because the write paths read through it; see the named
uncounted set below) is the only change.

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
| **`f` — the hot fraction** | ⛔ **no instrument existed** | shipped the **read ledger** (`tortoise/read_ledger.py`, hooked into the caller-facing row-returning read sites (`get_point` deliberately excluded — the write paths read through it)) | **no tenant value, and none is claimed.** The instrument's own exercise on the live self-hosted graph: **`f` = 8.64%** over 200 proxy queries — **a proxy mix, not a tenant** |
| **`rho` — the record's non-storage lines** | ⚠️ **partly** — the record store is not deployed, but its SCHEMA is committed and its read path is code | measured the unit costs on the committed record schema | **`rho` = 3.55** at 1,000 tenants/project (**88.7** at 40, **710** at 5) — ⚠️ **the disk line is measured, the compute+plan lines are the doc's DENSITY ASSUMPTIONS**, so `rho` is an assumption over a measurement, and only the disk line is a value |

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

```text
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
| keyed miss fetch | **5.2 blocks MEAN** (4 median, 7 max) · **2,312 B mean on the wire** (1,306 median, 5,408 max) · **0.048–0.053 ms** server, 0.81–1.07 ms end-to-end | the record's own PK probe, 200 samples × two runs, DETOASTED (what a real reader pays) |
| full-scope read (rebuild) | **115,605 blocks (947.0 MB logical) · 2,629–2,694 ms** | `read_record_view`'s query shape, DETOASTED, for ONE table (`public.point`, 89,701 rows, two runs) — a **per-table FLOOR**: a real rebuild walks `node_identity` plus five label tables and `relation` too |
| ⛔ same reads WITHOUT the detoast | 4 blocks · 0.029–0.108 ms; 7,962 blocks · 65.2 MB · 33.8–142.1 ms | `SELECT *` under `EXPLAIN (ANALYZE)` alone — **14.5× too few blocks and 18.5–80× too little CPU** for a rebuild, recorded so no later reader "re-measures" a cheaper number |

**Blocks are exact; milliseconds are a range over the two runs in `rho-bench.log` (shared-CPU
container, default settings) — an accounting bench, not a vendor benchmark.**

```text
disk      = $0.125 × 0.1916 GB               = $0.0240 /tenant/month   (MEASURED GB × the plan's AVERAGE rate; the plan includes 8 GB/project ≈ 41.8 tenants of this stock, so the MARGINAL byte is $0 until then — rho is an average-cost ratio. The doc used $0.0187 from the CACHE's 0.1499 GB)
compute   = $60 ÷ 1,000                      = $0.060                  (density assumption)
plan      = $25 ÷ 1,000                      = $0.025                  (density assumption)
IOPS      = $0   (5.2 blocks/mmiss MEAN ⇒ the step is 14.4 cold misses/s/tenant at 40 tenants, 0.58 at 1,000)
egress    = $0   ONLY below the project's 250 GB allowance: 2,312 B/miss MEAN ⇒ 108,154,878 misses/month SHARED by the tenants
                  ⇒ 1.04 misses/s/tenant at 40 tenants (3,755/hour), 0.042 at 1,000 (150/hour) — the FIRST line to leave $0; each miss above it adds $2.1e-7
                  of egress and RAISES rho
rho       = (0.060 + 0.025) ÷ 0.023955       = 3.55

ratio     = cache line ÷ record disk line, in DOLLARS (the closed form's input)
            (the division uses the UNROUNDED lines, so it re-derives from the log)
            5 tenants, 1 GB STARTUP           : $14.5948 ÷ $0.023955 = 609 ⇒ NO break-even (rho = 710); f=1 → 0.462×
            40 tenants, 8 GB PRO              : $8.7469  ÷ $0.023955 = 365 ⇒ pays BELOW f = 75.4%;  f=1 → 0.803×
            1,000 tenants, 8 GB PRO (25x40, ONE project): $8.7469 ÷ $0.023955 = 365 ⇒ pays BELOW f = 98.75%; f=1 → 0.988×
            (a LOWER break-even is the HARDER bar: at 40 tenants a quarter of the cache must be cold,
             at 1,000 tenants 1.25%, and at 5 tenants there is no break-even at all)
```

**Two findings the model needs:**

1. **The record's footprint is 1.28× the cache's for the SAME node COUNT** (0.1916 GB vs 0.1499 GB),
   because the record stores the 384-d vector as `jsonb` decimal text where the cache stores
   `vecf32` — the encoding gap §5.2 measured on the journal (~4×), now measured on the record.
   ⚠️ **Indicative, not a per-node ratio:** the bench plants 89,701 `:Point`s while the cache's
   1,672 B/node is a blend over the live graph's real mix (~half `GraphEvent`). ⛔ **And it is an
   input, not just a sanity check:** `ratio` = `C ÷ D` puts the 0.1499 GB mixed-label cache against
   the Points-only 0.1916 GB record, so this population mismatch is inside 609 / 365 and every
   break-even below. Direction unknown (a real mix adds cheaper `GraphEvent` nodes ⇒ smaller `D` ⇒
   higher `ratio` ⇒ a HIGHER break-even, i.e. an EASIER bar; the vector lower bound pushes the other
   way, and that is why the pair is called two-sided rather than directional). The
   planted vector used 6 decimal places (~9.5 chars/float) where §5.2 measured the journal's real
   encoding at 21.9 chars/float, so **0.1916 GB is a LOWER BOUND at that column type**.
2. **`rho` is dominated by TENANT DENSITY, not by any per-read cost.** At §17.4.3's 40-tenant
   density BOTH density-scaled lines move — compute `$60 ÷ 40 = $1.50` and plan `$25 ÷ 40 = $0.625`
   ⇒ **`rho` = (1.50 + 0.625) ÷ 0.023955 = 88.7**; at 1,000 tenants the same two lines give **3.55**, and at 5
   tenants/project **710** — where the split cannot pay at any `f`.
   ⚠️ **Three bases, not two** — a density, its own cache tier, and the measured disk line. Pairing
   each density with ITS OWN packing tier gives `ratio` **609** (5 tenants, 1 GB STARTUP) and **365**
   (40 or 1,000 tenants, 8 GB PRO — 1,000 tenants is 25 cache instances of 40, all in ONE project); `multiplier = 1/(f + (1+rho)/ratio)` then gives:
   **`f` below 98.75% ⇒ the split pays at 1,000 tenants** (only 1.25% of the cache must be cold), below
   **75.4% at 40 tenants** (a quarter must be cold — the HARDER case), and **never at 5 tenants** on
   1 GB STARTUP, where `(1+rho)/ratio` exceeds 1 and no hot fraction makes it pay.
   `f = 1` is 0.988× / 0.803× / 0.462×, so at today's keep-everything policy nothing is saved.
   (Dividing the measured DISK LINE — the only part of `rho` that is a value, since compute and plan are density assumptions — by §17.4.4's pre-measurement `778.7` mixes a price with a quantity:
   778.7 is a RATE ratio, valid only while the record occupies the cache's own 0.1499 GB.) The
   coherent pair is 25× apart and no per-read measurement can resolve it: 5.2 blocks and 48–53 µs
   per miss do not make a compute tier. ⚠️ **The egress allowance is shared by the project**, so it
   holds only up to 1.04 misses/s/tenant at 40 tenants (0.042 at 1,000) — a real mix can exceed that
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
- **read paths the ledger cannot count** — a NAMED set, deliberately not claimed exhaustive (an
  exhaustive list would date the moment a reader is added): **`recall_subgraph`** (graph expansion:
  expander nodes, not keyed lookups); **`diary_read`, `session_context`, `list_dedup_candidates`,
  `list_drafts`, `belief_timeline`, `get_org_structure`** (caller-facing `RETURN properties(n)`
  readers — FOUR of these six are registered MCP tools (`tortoise_diary_read`,
  `tortoise_session_context`, `tortoise_list_dedup_candidates`, `tortoise_belief_timeline`); several
  return `Point.id`, so their rows WOULD be joinable and only the missing hook excludes them); **`list_sources`** (a `Source` projection
  returning `url`/`sourceKind`/count — not a `properties(n)` reader, and the single most joinable row
  shape of the set); **the state and traversal paths** (`traverse`, `recall_state`'s related-point/
  object readers, `get_historical_knowledge`); **a POOL read of the FTS funnel** — its internal pool
  callers opt out (`_record_reads=False`) and record the slice they SERVE — `recall_state` its
  Point rows, as every hooked path does (§17.4.6 names them) — but a caller that hands the funnel a `limit` far above what it
  surfaces is still a pool read, and `recall_state`'s OBJECT leg is uncounted: its rows carry `id`,
  while an Object's key is `name`, so recording them could only emit `unresolved` (reported as a row with no
  value for the label's contract field, which for a successful Object read would be a mis-statement); and **the
  `Subject`/`Object`/`Source` legs of the FTS funnel**, whose rows are `SearchResult.to_dict()` and
  carry `id`/`eventId`/`sourcePath` — never `name`/`url` — so they land in `unresolved` rather than in
  a joinable coordinate. **Every one of them leaves a read out of the numerator, so every one biases
  `f` LOW — flattering the split**; the byte-unit hole above is the only **TWO-SIDED** one. All are
  **#8877**.

---

## Files

| file | what it is |
|---|---|
| `census-7983.log` | the before-census (instrument absence, dated, at the base commit) |
| `probes/census_7983.py` | reproduces it (`--base <sha>` measures the committed tree) |
| `probes/ledger_probe.py` · `ledger-probe.json` | the ledger exercised on the live self-hosted graph (random draw — the quoted reading) |
| `probes/ledger_probe.py` · `ledger-probe-storage-order.json` | the same probe with `--sample storage-order`: **2.07%** against 8.64%, so the sampling effect is reproducible rather than asserted |
| `ledger-probe-warnings.summary.txt` | the probe's product-level warnings, summarised and attributed |
| `probes/record_read_cost.py` · `rho-bench.log` | `rho`'s unit costs on the committed record schema (two runs) |
| `probes/graph_content_census.py` · `graph-content-census.json` | the dogfood graph's shape: what the bench planted and why those widths |
