> ## ⛔ PARTIALLY RETRACTED — READ THIS FIRST (2026-09-25)
>
> **Two sections of this document do NOT apply, by owner ruling the same day it was written:**
>
> 1. **The per-node framing is retired.** The cost model's unit is **bytes**, not nodes: *"we shouldn't measure 'per node' but … MB/GB for postgres, MB for graph, and LLM usage for extraction."* Any node count × a per-node constant is not the model. **The graph unit is a direct MB reading** (`GRAPH.MEMORY USAGE` — per-graph, hence per-org), not a derived per-node figure.
> 2. **The pricing arithmetic does not apply.** Owner: *"I am not asking you to calibrate our pricing, we'll do that later."* The per-GB floors, the "loss-making at the ceiling" figures and the rate arguments below are **out of scope** — do not use them to set a rate.
>
> **What still stands below:** the three-unit mapping, the `GRAPH.MEMORY USAGE` accuracy caveats, the raw-store finding, the overage gap, and the records that must move.
>
> **The authoritative home for this work is the filed issue, not this document.** Treat this as working notes; the issue carries the ruled model.

---
title: "Scoping — the cost model: three units, tiers gate features, overage"
type: scoping
domain: platform
doc_status: draft
created: 2026-09-25
updated: 2026-09-25
subjects.team: epistemic-team
issue: "#5013"
aboutSubjects: Tortoise cost model (raw storage, graph storage, LLM extraction)
aboutObjects: product/pricing.json, tortoise/quota.py, tortoise/metering.py, tests/test_document_source_gold.py, STORAGE-ARCHITECTURE.md
---

# Scoping — the three-unit cost model

**Status:** scoping only. **No price change, no cap change, no code change.**
**Branch:** `scope/cost-model-meters` · **Base:** `6c48cf561` (`main`, 2026-09-25)

**Governing direction (owner, 2026-09-25T19:05Z, on #5013 and #5088):** three cost units —
postgres/raw bytes, graph bytes, LLM extraction — tiers gate **features**, and usage above the tier
allowance is **purchased as overage, never an upgrade wall**.

**Method:** `issue-scoping` v5.1 double diamond. Evidence rule applied throughout — every number
below is checked against the primary artifact (file:line), **not** against a comment or a doc's
paraphrase of another doc. Where the architecture docs and the code disagree, that is recorded as a
**finding**, not resolved by preference.

---

## 0. Inputs and pre-flight

| input | value |
|---|---|
| Collision pre-flight `#5088` | **CLEAN** (exit 0, 7/7 surfaces) |
| Collision pre-flight `#5013` | **COLLISION** (exit 1) — 13 hits, **all marked `(weak)`**: prose cross-references (not closing refs), merged PRs (immutable history), and `worktree/d10-ontology` + branch `docs/5013-document-is-source` (the ONTOLOGY doc lane). **No hit touches the cost model or `docs/scoping/`.** The one open PR, **#5127** (`feat/5026-retire-document-label`), edits `tortoise/quota.py` + `tortoise/projection/entities.py` — files this scoping **must not** and does not edit. Recorded, not bypassed. |
| Parallel-work checkpoint C1 | `CLEAR  no-board-skip` (no board session) |
| Test suite | **not run** — box saturated (per task constraint) |

**The two decisions this scoping works under, and their precedence:** D10 (#5013 — a document is a
`:Source`) and D11 (`STORAGE-ARCHITECTURE.md` §2.0 — stay on hosted FalkorDB; optimisation
deferred). The cost model does not reopen either.

---

## 1. Problem diamond

### 1.1 Problem-diverge — alternative framings

The owner's framing is **"three cost units; tiers gate features; overage, never an upgrade wall."**
Four alternative framings were tested against the primary sources.

| # | framing | what it would make true | verdict |
|---|---|---|---|
| **F1** | **"we are missing three meters"** | the problem is instrumentation; add a byte reader per unit and keep the caps | **partly true, and insufficient** — no byte source of truth *exists* even to read: `GRAPH.MEMORY USAGE` is called **nowhere** (grep, any `.py`/`.sh`: zero hits), and the raw store has **no table and no bucket** (§3.1). Also the node cap is a ceiling, so this framing cannot say whether the tiers are solvent. |
| **F2** | **"the declared cost basis is wrong"** | `bytes_per_node: 1024` understates, fix the constant | **true and load-bearing** — measured blends are ≈2,750 B/node (resident) and ≈5,600 B/node (cap-counted) — but **the constant has no code reader**: `bytes_per_node` and `falkordb_per_gb` occur only in `product/pricing.json:20-21`, `tests/e2e/hosted/fixtures/pricing-e2e.json:19-20`, and the #4333 research brief — no `*.py`/`*.js`/`*.ts` reads either key. **Nothing computes the proxy.** Fixing a dead constant changes no outcome; the gap is that the cost model is *declared, not computed*. |
| **F3** | **"the placement is wrong"** — raw text is in the graph | the "raw" unit is really a graph unit, charged at 584× the raw rate | **true, and the largest single defect available.** `Projection._upsert_document` writes `d.content` (`tortoise/projection/entities.py:1866`) and `_upsert_point_props` writes `n.content` (`:726`), so verbatim turn/document text is **RAM-resident at $73/GiB-mo** while the intended raw store costs **$0.125/GB-mo** — a **584×** difference. §9.1/§9.3/D30 already rule the text *out* of the graph; the code has not moved. |
| **F4** | **"read side: the meter that matters is egress"** | cap flow, not stock | **true as a gap, but it collides with a recorded decision** — see §4.4 and the contradiction test. `pricing.json:17` records **`reads_free: true`**. Egress is a read-side cost. |

### 1.2 Problem-converge — confirmed problem

> **Confirmed problem.** Tortoise has **no byte source of truth for either storage unit**, its only
> declared cost basis (`cost_basis`) is dead config that understates the measured per-node cost by
> ≈2.7–5.5×, its raw text is stored in the most expensive tier (the graph) instead of the raw tier
> (584× cheaper), and its single existing overage mechanism is **configured, displayed and logged —
> never enforced and not purchasable**. The three-unit model cannot be capped until a byte meter
> exists per unit *and* the placement defect (F3) is fixed, or the graph unit will keep absorbing
> bytes that belong to the raw unit.

**Why this framing, and why not F1:** F1 describes the symptom (no meters). The root is that the
cost basis is **declarative** — `cost_basis` is a constant pair with no consumer — and the placement
defect means even a correct graph meter would measure the wrong thing.

**Rejected alternatives, with the condition under which each would have won:**
- **F1 alone** would win if a byte reader already existed and only the caps were missing. It does not:
  zero call sites, no raw store.
- **F2 alone** would win if the constant were consumed. It is not; the constant is inert.
- **F4 alone** would win if storage were cheap and flow expensive. The opposite holds: graph storage
  is the dominant line (584× the raw rate) and there is **no eviction** (§2.1) — an idle tenant costs
  full rent, which makes *stock* the honest unit. **The owner's choice of storage as the unit is
  correct in shape** — it is the number that does not exist yet.

**Assumptions, tagged:**
| assumption | status |
|---|---|
| the graph is one graph per org, so per-tenant bytes are readable | `[validated]` — `quota._make_sdk(namespace=org_id)` (`tortoise/quota.py:70`; called org-scoped at `:644`), and §4 states the FalkorDB arrangement is "one account, many graphs" |
| the invoice is on provisioned RAM, not dataset size | `[validated]` — §2.1, §5, §16 |
| `GRAPH.MEMORY USAGE` is available on our plan | `[unverified]` — documented; never called by us |
| the 75% density limit is a *per-graph* limit | `[validated]` as stated (§2.1); the **aggregate** density across many graphs on one instance is **not documented** |

**Falsification check.** This definition is wrong if either of: (a) a byte meter exists in a surface I
did not grep (it would show as a `GRAPH.MEMORY` call or a `pg_column_size`/`sum(size(…))` query —
neither exists); (b) `bytes_per_node` has a **code** consumer outside `product/pricing.json` (it does
not). *(A third candidate — a real invoice showing idle tenants are not billed full rent — tests the
**unit choice** (stock vs flow), not this definition; it is carried as §7 item 1.)*

---

## 2. Solution diamond

### 2.1 Solution-diverge — three distinct approaches

| | **S-A: measure-at-the-boundary** | **S-B: measure-at-the-instance** | **S-C: allocate-by-model** |
|---|---|---|---|
| graph unit | `GRAPH.MEMORY USAGE` per tenant graph, read periodically | provisioned instance RAM ÷ number of graphs, or a linear model from node/edge/vector counts | `nodes × measured_bytes_per_class` + `edges × b` + `vectors × 2.05 KB`, from a versioned coefficient table |
| raw unit | `pg_column_size`/`sum(size(bytea))` per org on RLS-scoped raw rows | bytes written at ingest, recorded on the ledger | estimated from source count × mean source size |
| egress | bytes served per request, attributed via the org on the answer path | n/a (billed to us, allocated by share) | n/a (modelled) |
| accuracy | vendor's sampling estimate (`SAMPLES`) | ±unknown, but never a false precision | false precision by construction |
| cost to build | medium — one reader per store + a period sweep | low for the graph half, but the raw/egress halves still need S-A | low to write, high to keep honest |
| failure mode | **quoting a sampling estimate as exact** | **under-recovering the tail** (share hides the tenant that actually got big) | **a number that drifts from reality and no one notices** |

### 2.2 Solution-converge — chosen: **S-A, with S-B's discipline on the accuracy claim**

**Chosen because it is the only approach whose number is a measurement rather than an allocation or
a model.** The owner is capping and pricing on this number; a modelled number (S-C) is a
*reconstruction* of a cost the vendor already tells us, and it would drift silently — the same
failure class as `bytes_per_node` today (a declared constant that matches no running regime).
S-B's share-of-instance figure is the *rent allocation*, not the tenant's usage: it would let one
growing tenant hide inside a cohort average and under-recover exactly the tail that overage exists
to bill.

**S-B's discipline is kept, not discarded:** we may not report a precision `GRAPH.MEMORY USAGE` does
not have (§3.2). The published figure therefore carries the estimate qualifier and a sampling basis,
and the *cap* is set with headroom rather than at the measured value.

**Rejected, with the condition for revisit:**
- **S-C** is revisited if `GRAPH.MEMORY USAGE` turns out to be unavailable on the Cloud plan
  (→ we would have to model, and the model must be calibrated to a probe).
- **S-B** is revisited for *capacity planning* (not billing) — the rent has to be provisioned
  somewhere, and §2.1 leaves density as the only lever.

---

## 3. The three units: what is measured today, and where the number comes from

### 3.1 Unit 1 — postgres / raw storage per MB/GB: **NO METER AND NO STORE**

| question | evidence |
|---|---|
| where do raw bytes live **today**? | **In the graph.** `Projection._upsert_document` writes `d.content` (`tortoise/projection/entities.py:1866`); `_upsert_point_props` writes `n.content` (`tortoise/projection/entities.py:726`). Verbatim turn text and indexed-document text are graph node properties. |
| where should they live? | Supabase storage / Postgres, referenced by the `:Source` — §9.1 (D1 narrative), §9.3 (D30 "raw data can be hosted by us"), §9.5 Q3 (`content` ⛔ LEAVES THE GRAPH). |
| does the store exist? | **No.** `supabase/migrations/` holds **44** migration files; the **only** storage bucket declared is `blog-images` (`supabase/migrations/20260827000001_blog_cms.sql:195-221`). There is **no** raw-source table and **no** raw-source bucket. |
| is a meter present? | **No.** No `pg_column_size`, no `sum(size(...))`, no object-store size read anywhere in `tortoise/`. |
| what rate would apply? | Supabase database storage **$0.125/GB-month**; object storage **$0.0213/GB-month** (§5, line 261). |
| **status** | ⛔ **no meter, no store** — the genuinely missing unit. |

**Attribution (how it would work):** tenancy is decided as **one database, tenant-scoped rows**
(§4) — so attribution is a per-org predicate over RLS-scoped raw rows, not a per-project lookup. The
org is already the only FK-enforced key to spend (`cohort_cost.py:43-44`), so the same key works.

**The placement finding (F3), quantified.** Raw text sitting in the graph is charged at the graph
rate. The two rates differ by **73 / 0.125 = 584×**. Measured text in the live graph is **≈16 MB of
the 141 MB** (§2.2). So the *placement* defect is small in absolute bytes today and is the cheapest
fix available per byte — but it must land **before** the graph meter, or the graph meter will bill
raw bytes at 584× and the raw meter will read zero for the same bytes.

### 3.2 Unit 2 — graph storage per MB/GB: **NO BYTE METER; A COUNT METER; A DEAD CONSTANT**

| question | evidence |
|---|---|
| is there a byte meter? | **No.** `GRAPH.MEMORY USAGE` is referenced in `docs/` (`STORAGE-ARCHITECTURE.md` §2.1 `:72` and §12.1 `:653`; the measured read is *discussed* at §12.1b and the open decision at §12.1c) and is **called by no code** — grep over `*.py`/`*.sh` returns zero hits. |
| is the declared proxy computed? | **No.** `billing.cost_basis = {falkordb_per_gb: 73.0, bytes_per_node: 1024, dedup: content_hash}` (`product/pricing.json:19-22`). `bytes_per_node` and `falkordb_per_gb` have **no consumer** — the only other occurrences are `tests/e2e/hosted/fixtures/pricing-e2e.json:19-20`. The proxy `max_graph_nodes × bytes_per_node × falkordb_per_gb` is **arithmetic nobody runs.** |
| what IS metered? | A **count**: `quota._count_resource("points")` = non-episodic `Point` + `Object` + `Subject` (`tortoise/quota.py:672-688`), gated by `max_points` / `graph_size_cap` (`tortoise/billing.py:481-482`; `tortoise/quota.py:409`). |
| what would a real meter read? | STORAGE-ARCHITECTURE §12.1 (`:653-665`; identical table in the #4333 research brief §3.5), 2026-09-24, `SAMPLES 100`: `total_graph_sz_mb` **141**; `indices_sz_mb` **45**; `Point` attrs **50**; `Object` **13**; `Event` **9**; `GraphEvent` **11**; `Source`+matrices+edges+misc **≈13**. Stored vectors ≈50 MB + the HNSW share of the 45 MB index. |
| **status** | ⚠️ **a count proxy, not bytes — and the proxy's only constant is inert.** |

### 3.3 Unit 3 — LLM consumption (extraction): **MEASURED — BUT NOT BY `write_ops`**

This is the direction's own open question (*"confirm extraction ops are what is counted"*). **Verified: they are not.**

| meter | what it counts | call site | where the number comes from |
|---|---|---|---|
| `write_ops` | **write operations on the commit / MCP write path** | `metering.record_write_ops` (`tortoise/metering.py:465`), called from `hosted_api.py:4812` and `mcp_server.py:1005` | an integer incremented per write (`metering_increment`), with `nodes_written` as the value-first driver |
| `capture_cost_usd` | **the provider-authoritative dollar cost of one capture extraction** (`#3359`) | `metering.record_capture_usage` (`tortoise/metering.py:861`), fed by `_capture_cost_props` (`hosted_api.py:4841-4846`, def `:23425`) | the provider's reported charge, rolled up by `extractor_v2._rollup_llm` |
| `ask_cost_usd` | the serving-lane dollar cost of an ask | `record_ask_usage` (`ask_lane.py:695-699`) | the serving lane's rates (`#2069`) |
| the ledger | `SUM(ask_cost_usd + capture_cost_usd)` per org per metering window | `metering.get_cohort_spend_usd` (`tortoise/metering.py:944`; registry sum at `:1006-1007`, Supabase sum via `supabase_control.metering_cohort_spend`) | durable `metering_records` row; column added by migration `20260917000001_metering_capture_cost.sql` |
| tier allowance side | `included_write_ops_per_month` per tier | `product/pricing.json:30,50,71,95`; read at `metering._ops_allowance` (`:421`) | pricing.json |

**⇒ The extraction unit already has a *better* meter than the direction assumed:** it is measured in
**dollars** (provider-reported), on the durable per-org ledger, so it needs no new unit — it needs
the *allowance and overage* wired to `capture_cost_usd` rather than to `write_ops`. `capture_calls`
is recorded alongside as the denominator that keeps a genuinely-free capture distinguishable from one
whose cost was never measured (`metering.py:875-877`) — the honest unit is **dollars, with a call
count as the integrity check**, not "operations".

**One open question for the owner:** "extraction **operations**" reads as a *count*; what is measured
is *dollars*. The two behave differently under a cap (a count is gameable by cheap models; dollars
self-adjust). Recorded in §7.

### 3.4 Summary — the three units and their meter status

| unit | meter today | source of truth | status |
|---|---|---|---|
| **postgres / raw bytes** | **none** | would be `pg_column_size`/object size per org | ⛔ **no meter, no store** |
| **graph bytes** | **none** — a count proxy + an inert constant | would be `GRAPH.MEMORY USAGE` per tenant graph | ⚠️ **count proxy only** |
| **LLM extraction** | ✅ **`capture_cost_usd` + `capture_calls`** (dollars, provider-reported) — **not** `write_ops` | `metering_records` ledger | ✅ **measured; allowance unwired** |

**The biggest gap:** **there is no byte source of truth for either storage unit.** The raw store does
not exist; `GRAPH.MEMORY USAGE` is never called; and the only declared cost basis is dead config
understating the measured per-node cost by ≈2.7–5.5×. Everything downstream (caps, overage, the
per-GB rate) is blocked on that single missing measurement.

---

## 4. Answers to the asked questions

### 4.1 Is `node_count × bytes_per_node(1024)` defensible? **No.**

Against the **measured** graph (STORAGE-ARCHITECTURE §12.1 `:653-665`, 141 MB total):

| basis | bytes/node | vs declared 1,024 B |
|---|---|---|
| declared (`product/pricing.json:21`) | 1,024 B | — |
| resident labelled nodes (51,012) | **≈2,750 B** | **2.7× low** |
| the storage doc's ≈3 KB working figure | ≈3,000 B | **2.9× low** |
| cap-counted nodes (24,965) | **≈5,600 B** | **5.5× low** |
| isolated marginal probe — **props-only, no index** | ≈356 B | declared is 2.9× *high* here |
| isolated marginal probe — **props + one embedding** | ≈4,701 B | a **marginal floor**, not a blend |

⚠️ **The probe figures and the blends are not the same quantity and must not be interchanged.** The
4,701 B figure is a marginal `used_memory` delta in a fresh instance **with no index built**; it
cannot be multiplied by the embedding count — if it were, 33,580 embeddings would occupy ≈158 MB,
more than the whole 141 MB graph. **The honest statement:** the declared constant matches no running
regime, and the defensible number is a **blended, denominator-attached** figure (≈2,750 B resident /
≈5,600 B cap-counted), never a marginal probe.

**What measuring it for real would cost, and the honest accuracy claim.** The read is one command —
`GRAPH.MEMORY USAGE` with `SAMPLES` — per tenant graph per period. It reports a real field
breakdown (`indices_sz_mb`, `amortized_node_attributes_by_label_sz_mb`, `label_matrices_sz_mb`, …)
but, as §2.1 measures: it **samples** (`SAMPLES`, default 100, max 10,000) and *averages*, so it is
an **estimate**; and it **excludes per-graph and Redis-key overhead**, so it is a **floor** on the
RAM a graph actually occupies.

> **The honest accuracy claim, and the meter must not exceed it: "the tenant's graph is estimated at
> N MB (±10%, sampling-based; SAMPLES=100), excluding per-graph and key overhead."** Never "N MB".

⚠️ **A meter that reports a precision it cannot have is the failure this section exists to prevent:**
a sampling average from 100 samples in a graph with 51,012 labelled nodes must not be displayed to a
customer as an exact figure, and a 1-MB-grained cap would be false precision. The cap must carry
headroom (see §4.3's provisioning factor) and the display must carry the qualifier.

### 4.2 The per-GB pricing math

**The rent is on provisioned instance RAM, not on our bytes** — $0.10/GB-hour ≈ **$73/GB-month**
(§2.1, §5). **No eviction** (§2.1): an idle tenant costs full rent. ⇒ a *request* meter would
under-recover an idle tenant, so **storage is the correct unit**; but per-tenant bytes **do not sum
to the machine**, so the per-MB rate must exceed the raw rate.

Factors, each named:

| factor | value | source |
|---|---|---|
| raw rent | $73.00 /GiB-month | §2.1 |
| **① density limit** — a graph may occupy ≤75% of instance RAM ⇒ 1 GB of tenant data needs 1/0.75 GB provisioned | **×1.333** | §2.1 line 61 |
| **② unattributed overhead** — `GRAPH.MEMORY USAGE` excludes per-graph + Redis-key overhead, so machine RAM > Σ(graph sizes) ⇒ roll up for the un-attributed share | **×1.176 (at 15% overhead)** | §2.1 line 72 |
| **③ provisioning headroom** — the machine cannot run at Σ(tenant bytes); tenants grow between meter reads, indexes rebuild, writes spike | **×1.6–2.0 (policy)** | not measured — a provisioner decision (§7) |

**Resulting rate floor and range — decimal MB throughout** (1 GiB = 1,073.7418 MB, so the rate is
converted to `$/MB` and applied to the same decimal-MB byte columns used below):

| scenario | markup | rate /GiB-mo | **rate /MB-mo** |
|---|---|---|---|
| ① only (the **floor** — no cap may price below this) | 1.33× | $97.33 | **$0.09065** |
| ① + ② | 1.57× | $114.51 | $0.10665 |
| ① + ② + ③ (2× headroom) | 3.14× | $229.02 | $0.21329 |

**⇒ The hard constraint on any cap: pricing below $0.09065/MB makes it systematically under-recover
on every byte, before any LLM cost.** The three rows are the candidate envelope.

**⚠️ Owner-routed, not decided here.** *Which* point in that envelope to price at — and therefore
what the byte allowances are — is a **pricing decision**: the #4333 research brief §8 already routes
the `cost_basis` replacement to the owner, and D11 defers pricing. This scoping supplies the
constraint and the arithmetic; **it does not pick the rate.** Candidate points for the owner to
choose from: **$0.0907** (floor), **$0.1088** (1.2×), **$0.1496** (1.65×), **$0.2133** (①+②+③).

**The correction is not the rate alone — it is the byte allowance.** At the measured cap-counted
blend (5,600 B/node), the **existing node caps already cost more than the tiers' prices** before any
LLM cost:

| tier | `max_graph_nodes` | bytes at 5,600 B/node | storage rent @ floor ($0.09065/MB) | tier price | margin |
|---|---|---|---|---|---|
| free | 10,000 | 56 MB | $5.08 | $0 | **−$5.08** |
| solo | 25,000 | 140 MB | $12.69 | $9 | **−$3.69** |
| pro | 100,000 | 560 MB | $50.76 | $25 | **−$25.76** |
| team | 600,000 | 3,360 MB | $304.58 | $149 | **−$155.58** |

⚠️ **These are ceiling costs, not average costs** — the caps are maxima, so most tenants cost far
less. The finding is that a **maximal** tenant is loss-making at every paid tier, and overage is
precisely what maximal tenants buy — so the overage rate is the lever that decides whether the tail
recovers. **Break-even byte allowances at today's prices** (nodes = bytes ÷ 5,600 B):

| tier | price | break-even bytes | = nodes @5,600 B | % of its current cap |
|---|---|---|---|---|
| solo | $9 | 99.3 MB | ≈17,700 | 71% |
| pro | $25 | 275.8 MB | ≈49,200 | 49% |
| team | $149 | 1,643.7 MB | ≈293,500 | 49% |

**Why the tiers look solvent today:** at the **declared** 1,024 B/node the same caps cost $0.93 /
$2.32 / $9.28 / $55.70 — every **paid** tier is positive (free is a loss leader at either basis). **The declared constant is the only reason the tier prices
reconcile.** That is the mechanism behind "the caps systematically under-recover": the caps are not
wrong in isolation, but they are expressed in a unit whose conversion constant is 5.5× too small.

**Cross-check against the decided node overage (~$2/10k, #4331).** At the declared 1,024 B/node this
recovers **215%** of the floor cost; at the doc's ≈3 KB working figure **74%**; at the measured
5,600 B/node **39%** — i.e. it under-recovers ≈2.5× at the measured blend **and is coherent only
under the retired constant**. ⚠️ *The $2/10k figure
itself is cited from the #4333 research brief §8; #4331 is CLOSED and its body was not re-read in
this pass — recorded as a partially-verified input (§7).*

### 4.3 Unit 3 — where the extraction allowance should attach

Today `included_write_ops_per_month` is the only allowance and it attaches to `write_ops`, which is
the **write** count, not extraction (§3.3). The extraction meter (`capture_cost_usd`) exists and is
already per-org per-window, so the evidence points to a **dollar allowance** per tier, with overage
as the increment. This is a change of *which field the cap reads*, not a new meter — the direction's
"✅ essentially exists" holds for the meter and not for the wiring. **The allowance *level* per tier
is owner-routed (§4.2); only the attachment point is a scoping conclusion.**

### 4.4 The raw/postgres meter — and **egress**

**Where raw bytes live today:** in the graph (§3.1). **Where they must live:** the raw store
(D30/§9.1), which does not exist yet. **Attribution:** per-org over RLS-scoped rows in the one shared
database (§4) — the same `org_id` key the spend ledger already uses.

**Egress is not optional, and §9.4 is why.** The design sends the source's **verbatim text at answer
time** — *"the link is the mechanism that gets the evidence to the model"* (§9.4), evidenced at
15.9 / 22.0 pts verbatim-over-extracted (`EXTRACTOR-V4-ARCHITECTURE.md:102,152`). So **every answer
is a download**, and it recurs per answer while storage is paid once. Capping stored megabytes while
leaving egress uncapped "caps the smaller half" (owner, on #5013) — confirmed: **egress is the
recurring half**, storage the one-time half.

**⚠️ The contradiction test — run first, and it FIRES.**
`product/pricing.json:17` records **`reads_free: true`** (an owner-confirmed pricing field,
`owner_confirmed: 2026-08-07`). **Egress is a read-side cost.** Capping and selling egress is
therefore a charge on reads — metering 1 GB leaving the building and pricing overage on it is a read
charge whatever the pricing grain. **This is a genuine collision between two owner statements, and
this scoping does not resolve it by preference:**

| statement | when | where |
|---|---|---|
| **`reads_free: true`** | 2026-08-07 | `product/pricing.json:17` (recorded decision) |
| **"Raw storage must include EGRESS, not only bytes … Capping stored megabytes while leaving egress unmetered caps the smaller half."** | 2026-09-25 | the owner's own comment on #5013 (recorded direction) |

**Route (per the standing rule): a reopen, argued with the evidence — not a quiet adoption.** The
evidence for the reopen is in this document: because the answer sends the source's **verbatim text**
(§9.4), egress **recurs per answer** while storage is paid once — so an egress cap covers the *tail of
the meter*, not a rounding error. **Owner decision required (USER QUESTIONS format):**

> **Context.** We cap storage, but the design's own answer path downloads the verbatim source every
time a claim is served — so the recurring cost is the flow, not the stock.
> **Option A — reopen `reads_free` for the flow half, with an `OVERRIDES:` line on the pricing
decision.** Reads stay free *per call*; the aggregate volume of a tenant's own stored bytes is
metered and purchasable as overage.
> **Option B — leave `reads_free` untouched and cap storage only.** The recurring half stays
uncapped; the cap recovers the one-time half and nothing more.
> **Analysis.** A is a narrower promise than `reads_free` reads as written, and it needs the
`OVERRIDES:` marker so the ruling reads as intentional at the point someone next asserts "reads are
free". B costs nothing to build and under-recovers by design.
> **Recommendation.** **A** — the flow is the recurring half and the owner's own direction says to
cap it — but it changes a recorded pricing decision, so it is the owner's to make, not this
scoping's.

**Design shape (independent of that choice): egress is a SEPARATE line from stored bytes, inside the
same overage mechanism** — the reasons hold under either option:
1. **They are dimensionally different.** GB stored and GB transferred cannot be added into one
   number; a single figure would hide which half a tenant hit and would claim a precision the meter
   does not have.
2. **The rates differ**: egress **$0.09/GB** (uncached) vs storage **$0.125/GB-month** — egress is
   **72%** of a storage GB-month. If one number is ever required, the honest conversion is
   **1 GB egressed = 0.72 GB-month stored**; the default is two lines.
3. **The owner's model already supports it**: "the tiers are per-features, extra usage is overage" —
   per-unit overage is the shape, so two units with two overage prices is the model, not an
   exception to it.

*Sources for the egress rate: Supabase pricing/docs (`supabase.com/docs/guides/platform/manage-your-usage/egress` — $0.09/GB uncached, $0.03/GB cached; 250 GB included). The repo's own §5.1 and §15 record egress as **not modelled and not measured** — so the rate is external and the volume is ours to measure.*

### 4.5 Overage — what exists vs what a customer needs to buy it

**What exists (all verified):**
- the API: `has_overage(tier)`, `overage_price_per_10k()`, `overage_tiers()` (`tortoise/pricing.py:85-96`);
- the config: `overage_unit: "write_ops"`, `overage_price_per_10k: 5.0`, `overage_tiers: [solo, pro, team]`, `reads_free: true` (`product/pricing.json:10-17`);
- **display**: `get_current_usage` computes `overage_cost_usd` for `/v1/team` (`metering.py:1078-1082`, `:1124-1127`);
- **threshold logging**: 80% / 100% of the allowance, logged, deduped per window (`metering.py:571-620`);
- **one enforced cost cap, and it is not overage**: the cohort LLM-spend ceiling
  (`tortoise/cohort_cost.py`) — fail-closed 402, **armed by env, off by default**, refusal code
  `cohort_cost_cap` deliberately distinct from `quota_exceeded` *so a caller is not sent to buy a
  bigger plan that cannot lift it* (`cohort_cost.py:145` + docstring). That is the correct precedent
  for "never an upgrade wall".

**What is missing for a customer to actually purchase overage without upgrading:**
| # | missing | evidence |
|---|---|---|
| 1 | **An enforcement point to purchase *from*.** The allowance column `ops_allowance` is **selected into the org row but consumed by no product code** — written by `billing.apply_limits` (`:481`), `sdk.py:16235` and the provisioning RPC, and selected by `supabase_control._ORG_BASE_SELECT` (`:108`) / `org_by_id` (`:1014`), but **never compared against a count at admission** (the `metering._ops_allowance` hits at `:587/1063/1074/1119` read the *pricing constant*, not the column). So there is no refusal, and nothing to buy past. | selected, never consumed |
| 2 | **A Stripe price for overage.** `PriceCatalog` validates the 4-tier catalog *shape* — known tier, both intervals, `price_` prefix, and the 20% annual discount (`billing.py:82-152`) — but **admits any subset** of tiers; there is **no metered price id** and no usage-record push (`usage_record` / `meter billing`: zero hits). | `billing.py` |
| 3 | **A purchase path.** Only subscription checkout (`create_checkout_session`) and the customer portal exist — no overage purchase, no auto-top-up. | `billing.py:255-380` |
| 4 | **Storage / egress overage units.** `overage_unit` is a single string, `"write_ops"`; there is no `bytes` or `egress` unit. | `pricing.json:10` |
| 5 | **Refusal copy that is not an upgrade wall.** The only plan-cap refusal says *"Upgrade your plan to increase it."* (`quota.py:761` for documents, `:790` for the generic path) — the exact wall the direction forbids. | `quota.py:761,790` |

**⇒ Answer: overage is CONFIGURED, DISPLAYED and LOGGED — it is enforced nowhere and purchasable
nowhere.** The one enforced cap (cohort LLM spend) was built with the correct no-upgrade-wall
semantics; that is the pattern to generalise.

---

## 5. The records that must move

**Nothing may land the new meters while a merged test still asserts the retired model.**

| # | record | what it currently says | what must change | evidence |
|---|---|---|---|---|
| 1 | **Q4 on #5013** ("the `documents` cap: KEEP IT, RE-POINT IT AT `:Source`") | a document count is a cost unit | **superseded** — under the byte+extraction model no document count is a cost unit; `/v1/index/docs` is gated by the storage + extraction allowance | `STORAGE-ARCHITECTURE.md:477-486`; owner comment 2026-09-25T19:05:46Z |
| 2 | **#5088 Trap-3 row** | records "keep and re-point" | **superseded** — same reason | #5088 comment 2 (Trap 3) + owner comment 2026-09-25T19:05:50Z |
| 3 | **`tests/test_document_source_gold.py` on `main`** | `CANON = "sourceKind='document'"` (`:41`) as **the** `documents`-cap predicate, with `documentKind IS NOT NULL…` named `REJECTED_LEAK` (`:42`) and `COALESCE(documentKind,'') <> 'transcript'` named `REJECTED_OVERCOUNT` (`:43`) | **its canon no longer applies** — correct or retire it in the same pass that lands the new meters, or `main` re-asserts a retired model | `tests/test_document_source_gold.py:41-49`; landed by PR #5137 (`1844bc6f8`) |
| 4 | **⚠️ NEW FINDING — a three-way divergence, not the two-way one on the record** | the issue records a *two*-way conflict (code implements B; the gold test says C). **It is three-way, and the code implements the form the test names REJECTED.** | record it; the predicate question dissolves with the cap, but the divergence must not be silently dropped | code: `tortoise/quota.py:653-656` counts `MATCH (d:Document) WHERE COALESCE(d.documentKind, '') <> 'transcript'` — **the same `COALESCE(documentKind,'') <> 'transcript'` discriminator as the gold test's `REJECTED_OVERCOUNT`** (`:43`), but over the **`:Document`** label where the test's candidates bind a **`:Source`** node. Gold test CANON is `sourceKind='document'` (`:41`). #5013's measurement comment recommends `documentKind IS NOT NULL AND <> 'transcript'` (the gold test's `REJECTED_LEAK`). **Three artifacts, three different predicates, and the code implements none of the three the test canonically names — its discriminator matches `REJECTED_OVERCOUNT`, but binds `:Document` where the test binds `:Source`.** |
| 5 | **`product/pricing.json:19-22` `billing.cost_basis`** | declares `bytes_per_node: 1024` (5.5× low) + `falkordb_per_gb: 73.0`, with **no consumer** | replace with a real meter basis; **the replacement is an owner-routed pricing input** (the #4333 brief §8 already routes it) | `product/pricing.json:19-22` |
| 6 | **`tortoise/billing.py:456` GAP-B docstring** (`max_points := max_graph_nodes`) | the node count **is** the graph-cost unit | retired by the node-count → bytes change | `billing.py:456,481-482`; `quota.py:679` |

---

## 6. Wiring check

| touch point | type | covered by | status |
|---|---|---|---|
| tenant graph byte read (`GRAPH.MEMORY USAGE`) | external service | new meter (this scope) | ⚠️ open |
| raw store + bucket/table | data store | **does not exist** — new | ⚠️ open |
| raw-byte per-org query (`pg_column_size`) | data store | new | ⚠️ open |
| egress attribution (bytes served per answer) | API / cross-cutting | new; org context exists on the answer path | ⚠️ open |
| `metering_records` ledger + increment RPC | data store | exists — `20260917000001`, `20260918000001` | ✅ |
| per-unit allowance columns | data store | `ops_allowance` exists (write-only); needs byte + dollar columns | ⚠️ partial |
| pricing config (`overage_unit`, `cost_basis`) | config | exists, wrong unit (`write_ops`) + dead constants | ⚠️ partial |
| Stripe metered price + usage records | external service | **missing** | ⚠️ open |
| refusal contract (402 / `ERR_QUOTA`) | API | exists (`quota_refusal_payload`, `#4614`) — but copy is an upgrade wall (`quota.py:761,790`) | ⚠️ partial |
| cohort LLM spend cap | cross-cutting | exists, env-armed, off by default | ✅ (precedent) |
| `tests/test_document_source_gold.py` | test | asserts the retired model — §5 row 3 | ⚠️ **blocking** |
| dashboard usage display | UI | `/v1/team` returns write-ops only | ⚠️ open |
| **MCP / SDK surface** + `config/surface-manifest.yml` | API contract | new units and overage change what MCP/SDK expose; `mcp_server.py` already carries the `_quota_gated` write gate. Repo HARD RULE: **Daniel's approval before any surface change**; `tools/surface_manifest.py check` is a drift control, **not** the approval | ⚠️ **approval required** |
| `quota.resolve_org_limits` fail-soft additive ladder + a migration for the new columns | data store / migration | new allowance columns must land through the documented ladder (`quota.py:396-410`, `_ORG_ADDITIVE_*`) or they hard-fail on a schema one migration behind (§7 item 12); no such migration exists | ⚠️ open |
| `/v1/team` response schema + checked-in OpenAPI | API contract | currently write-ops only (`metering.get_current_usage`); adding units is a response-schema change | ⚠️ open |
| alert / incident surface for the new caps | cross-cutting | the cohort cap's precedent is an `AlertStore` incident + operator alert (`cohort_cost.py`); a new refusal must name its sink | ⚠️ open |

**Wiring gaps that block completion:** the ⚠️-marked rows above (the meters + the raw store, the
MCP/SDK surface approval, the allowance-column migration, the `/v1/team`+OpenAPI shape, the alert
sink, the Stripe metered price, the per-unit allowance columns, the pricing config unit, the refusal
copy, and the dashboard display) — plus the **blocking test** in §5 row 3. The UI row needs a **UX gate** before implementation
(a new customer-facing surface: the raw-byte and graph-byte units, the extraction-allowance unit,
the separate egress line, and an "estimated (±)" qualifier on the graph figure) — routed to `ux-design-review`, not decided here.

---

## 7. Risks and unknowns I could not close

Stated as unknowns, not smoothed over.

1. **No invoice exists.** M2 (§15) is unmeasured — there is no real FalkorDB bill to reconcile the
   per-GB rate against. The rate arithmetic is derived from the published $0.10/GB-hour only.
2. **`GRAPH.MEMORY USAGE` availability and behaviour on our plan is unverified.** We have never
   called it; its `SAMPLES` parameter, its cost, and whether it is permitted on Cloud are all
   unconfirmed.
3. **The accuracy of the estimate is unbounded above.** §2.1 says it averages; it does not state a
   variance. "±10%" in §4.1 is an assumption, not a measurement — a meter must not publish it until
   measured.
4. **The overhead factor (②) is unmeasured.** How much provisioned RAM the machine holds beyond
   Σ(graph sizes) — per-graph overhead, Redis keys, the journal — is unknown; 15% is a placeholder.
5. **The provisioning factor (③) is a policy, not a number.** It depends on growth between meter
   reads and on index rebuilding, neither of which is measured.
6. **The 75% density limit is per-graph; the aggregate limit across many graphs on one instance is
   not documented.** The real density ceiling could be materially worse than 75%.
7. **Egress: no volume measurement exists** (§5.1, §15). Today egress is ≈0 *because* the raw text is
   in the graph — the whole egress line appears only once the raw store moves out.
8. **The raw store's byte source of truth is undefined** (table vs bucket; `pg_column_size` vs object
   size; compression; the narrative/turn split).
9. **The journal is a fourth growing cost line and is not in the model.** §5.1: it "grows at or near
   the rate of the derived layer" and **no measurement exists**; `GraphEvent` is 11 MB in the memory
   estimate but is **not** in the `graph_size` count — the two must be reconciled before the graph
   meter is trusted.
10. **"extraction operations" (count) vs `capture_cost_usd` (dollars) is unresolved.** The direction
    says operations; what exists is dollars + a call count. A count is gameable by a cheaper model; a
    dollar cap is not. **Owner decision needed.**
11. **The $2/10k node overage figure is partially verified** — cited from the #4333 research brief §8;
    #4331 is CLOSED and was not re-read in this pass.
12. **The graph/staging split**: `max_graph_nodes` is enforced through `max_points`/`graph_size_cap`
    and `billing.py`'s `max_points := …` — moving to bytes means the *enforcement* column changes
    too, and that column is read by `quota.resolve_org_limits` under a documented fail-soft additive
    ladder (`quota.py:396-410`). A new column must land through that same ladder or it will hard-fail
    on a schema one migration behind.
13. **`reads_free: true` vs an egress allowance is an OPEN COLLISION, put to the owner (§4.4).**
    Option A reopens the recorded decision and needs an `OVERRIDES:` line; Option B does not. This
    scoping recommends A but **decides nothing** — and the rate (§4.2) and the cap level are
    owner-routed with it.

---

## 8. What this scoping is not

- It is **not** a pricing decision: §4.2 supplies a **floor and an envelope**, and **declines to pick
  the rate**; the byte allowances are owner-routed with it (the #4333 brief §8 already routes
  `cost_basis`).
- It does **not** reopen **D11** (stay on hosted FalkorDB) or **O2** (no volume target / recall
  floor). Where a recommendation touches a recorded decision — `reads_free` (§4.4) — it is raised as
  an **owner question with an `OVERRIDES:` remedy**, not adopted.
- It is **not** a proposal to change the engine or shrink the connection layer (`OVERRIDES:` on
  #4333).
- It is **not** an implementation plan. That is `writing-plans`, after this scope is approved.
- It ran **no tests** and made no code change: the deliverable is this document.

---

*Evidence rule: every file:line above was read at base `6c48cf561`. Where a figure comes from a
research brief rather than a primary artifact, it is marked as such (§4.2, §7 item 11).*
