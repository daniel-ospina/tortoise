---
title: "Research brief — node volume + true per-node storage cost, against D11 (#4333)"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-24
updated: 2026-09-24
subjects.team: epistemic-team
issue: "#4333"
aboutSubjects: Tortoise graph node volume and hosted FalkorDB storage cost
aboutObjects: STORAGE-ARCHITECTURE.md, D11 hosted FalkorDB, cost_basis, node cap, #4331 overage
---

# Node volume + the true per-node storage cost — the measured picture, against D11

**Issue:** [#4333](https://github.com/daniel-ospina/tortoise/issues/4333) · **Track:** research/economics (the storage workstream owner)
**Date:** 2026-09-24 · **Lane:** A (research) · **Status:** investigation — **measurement only. No price change, no cap change, no migration.**
**Feeds:** [#4331](https://github.com/daniel-ospina/tortoise/issues/4331) (node overage), `product/pricing.json.cost_basis`, the GX unit-economics model, E4 [#5090](https://github.com/daniel-ospina/tortoise/issues/5090) (the vector layer).

> ⛔ **This brief reads the cost model against the measurements. It does not re-open it.**
> **D11 (owner, 2026-09-24): stay on hosted FalkorDB; the optimisation is deferred.** The `OVERRIDES:` ruling on this issue keeps the connection layer. **Caps and pricing are explicitly deferred by the owner** — so any change to `product/pricing.json` is routed to the owner (§8), never proposed here.

---

## 0. Research preflight

**Search tool: `web_search` with `model="sonar"` — rung 2.** Rung 1 attempted and unavailable: `mcp_load seo-intelligence` → `Unknown MCP server 'seo-intelligence'`. Model named explicitly on every call; no `deep-research`/`reasoning-pro` used. **5 external queries**, rate-limited once (HTTP 429) and re-run serially.

**Graph access:** hosted MCP via `Bearer $TORTOISE_MCP_API_KEY`; org/team counters via `GET $TORTOISE_API_URL/v1/team`. All live reads 2026-09-24. ⚠️ **The committed `.mcp.json` references `${TORTOISE_API_KEY}`, which is unset here** (the key present is `TORTOISE_MCP_API_KEY`) — a config gap **filed as [#5153](https://github.com/daniel-ospina/tortoise/issues/5153)**; the reads used the credential that exists.

**Citation pin:** every code locator below was re-read at **HEAD `c4580b083`** (2026-09-24) immediately before filing.

---

## 1. Step 0 — reframing the question

The issue asks which of **three causes** is operating: **(a) writing the wrong things, (b) writing too many things, (c) a per-node cost that can be optimised.** The honest answer from the measurement below is **all three are present, and they are separable** — but only **(c)** changes the *price of a node*, **(b)** changes how *fast the cap is reached*, and **(a)** changes *what the graph is worth at all*. Conflating them is how a volume target gets gamed (you can hit a node count by writing nothing).

**What the deadline is not:** the issue's original trigger — *"a Free org's 10k cap in ~2 days"* — has since been **re-measured** (two live points on the same day: ~14,969 nodes added in one day against a cap raised by 15,000 in the same window). So the *rate* is real; the *cause* is what is unmeasured.

**⚠️ Corrections the measurements force, recorded before any verdict:**
- **"5.6 KB per quota node" is denominator-dependent.** 140 MB ÷ 25,000 capped nodes = 5.6 KB, but the numerator includes ~21,214 **quota-free** episodic turn Points plus Events, Sources, scaffolding and the whole index. **140 MB ÷ 51,012 labelled resident nodes ≈ 2.75 KB.** Both are true; they answer different questions. (`STORAGE-ARCHITECTURE.md` §13 already corrects this; the brief keeps both explicitly.)
- **`GRAPH.MEMORY USAGE` is a sampling estimate** (`SAMPLES`, default 100) — the 141 MB is a good number, not an exact one.

---

## 2. The contradiction test — run FIRST

| candidate / claim | contradicts? | verdict |
|---|---|---|
| **Change the engine** (migrate off hosted FalkorDB) | ✅ **YES — D11** (*"stay on hosted FalkorDB; optimisation is deferred"*) | ⛔ **REFUSED. Do not re-open as "shall we migrate to Postgres?"** (§14.2). The two-store model (D30) already puts raw outside the graph. |
| **Set a volume target / recall floor** | ✅ **YES — O2, withdrawn by the owner** (*"we're not optimising stupidly for a number… this is not a corporate OKR setting exercise"*) | ⛔ **REFUSED.** No node target, no recall floor. |
| **Propose a price / cap change** | ✅ **YES — owner, explicitly deferred** (*"we need to fix the underlying problem… then update the user-facing side after"*) | ⛔ **REFUSED on this issue.** Routed to the owner (§8). |
| **Delete or derive the connection layer to save money** | ✅ **YES — the `OVERRIDES:` ruling on #4333** (*"the connections ARE the product and the pitch"*) | ⛔ **REFUSED.** The cost is solved by *where* the layer is stored, never by shrinking it. |
| **Measure the real per-node cost and replace the declared `bytes_per_node` constant** | ❌ **No** — but the *replacement* is a pricing input | **MEASURE (done below); the constant change is an OWNER QUESTION, not an adoption.** |
| **Adopt the `EMBED` gate's existing declared policy to stop embedding identifier-only text** | ❌ **No** — it enforces a declaration that already exists (pack `memory_granularity`), decided 2026-09-23 | **ADOPT** — but the *candidate rows kept/dropped* are Stage-1 material for the owner (§6.1). |
| **A more resolute storage layer (Postgres adjacency + pgvector), later** | ⚠️ **Deferred, not decided** — D11 defers it; `#3998` is its home | **DEFERRED — a reopen of D11's revisit trigger, not an adoption.** |

**Adoption gate:** `adopt (measure-and-record the real bytes/node; enforce the EMBED gate's declared policy) | refuse (engine change, volume target, price/cap change, shrinking the connection layer) | owner question (updating cost_basis) | deferred (the storage-layer migration, per D11)`.

---

## 3. What is measured — the numbers, shown

### 3.1 Node composition (live, 2026-09-24) **[HIGH — direct read]**

`tortoise_overview` on the production graph:

| label | count |
|---|---|
| `Point` | **38,311** |
| `Event` | **4,833** |
| `Object` | **7,867** |
| `Subject` | **1** |
| `Document` | **0** |
| **labelled total (`graph_size`)** | **51,012** |

`Point` by `pointKind`:

| pointKind | count | class |
|---|---|---|
| `event` | **21,214** | episodic session/turn records — **quota-free** |
| `statement` | **14,856** | extracted knowledge — **capped** |
| `checkpoint-item` | 8 | provider checkpoints |

`operators` (IMPL/NAND Points): **2,233**.

### 3.2 The four counting sets — and their live values **[HIGH — direct read, arithmetic verified]**

This is the issue's core finding, now confirmed against today's graph:

| set | definition (code) | live value |
|---|---|---|
| **CAP** | non-episodic `Point` **+ `Object` + `Subject`** — `WHERE (n:Point AND (n.is_episodic IS NULL OR n.is_episodic = false)) OR n:Object OR n:Subject` | **24,965** |
| **DISPLAY ("Memories")** | `MATCH (n:Point) WHERE NOT n.id IN $demo_ids RETURN count(n)` — `Point` only, demo-excluded | **38,309** |
| **RESIDENT** | the labelled set that actually consumes RAM | **51,012** |
| **BUDGET** | `MAX_VALUE_POINTS_PER_SESSION = {soft:15, hard:25, ceiling:50}` net-new non-episodic per commit | 15 / 25 / 50 |

*Locators at `c4580b083`: cap predicate `tortoise/quota.py:605` (constant `:109`); display count `tortoise/hosted_api.py:6413`; budget constants `tortoise/quota.py:109`, enforced in `tortoise/commit_schema.py:95-97`.*

**⇒ Verified arithmetically: 38,311 − 21,214 (episodic) + 7,867 (Object) + 1 (Subject) = 24,965 = the cap counter, exactly.** The cap meter and the resident set differ by **51,012 : 24,965 = 2.04 : 1** — RAM carries **2× the entities the cap counts**.

### 3.3 Measured claims-per-source distribution — indicator 1, answered **[HIGH — direct read, n = 2,227]**

`tortoise_overview(section="sources")`, 2,227 sources (all `sourceKind: agentSession`), Points linked per source:

| statistic | value | boundary example (real source url) |
|---|---|---|
| min | 0 | `session:b2798a22-fe41-426c-8d71-9915faccab86` |
| **p50** | **0** | same |
| **p90** | **20** | `session:427b9750-55d0-40f0-aed3-7ab59835d92d` |
| **p99** | **37** | `session:f0e72daa-a556-4c2e-99de-bd45bcee02b8` |
| max | 64 | `session:0674c80a-6f01-451b-bfaa-653b0c28fa0d` |
| mean | **6.5** | — |
| Source-linked Points total | **14,567** (= the measured `extractedFrom` edge count) | — |

**⚠️ Read this precisely:** this is **claims linked to a Source per session** — the `extractedFrom` leg — **not** total nodes per session. `Point` total is 38,311 against 14,567 Source-linked, so **~23,744 Points are not Source-linked** (episodic turn Points, operator Points, and minted points with no `extractedFrom`).

**On the budget:** the max of 64 exceeds the 50 `ceiling` — but this is a **cumulative per-source** count and the budget is a **per-commit** delta. **Whether the ceiling is enforced cumulatively is not settled by this read.** That is a measurement, not a defect claim.

### 3.4 Measured per-node cost — the declared constant vs the real one **[HIGH for the probe; the graph-wide blend is MEDIUM ⚠️ emerging]**

Two independent measurements exist, both already recorded on the issue:

| shape | measured cost | how |
|---|---|---|
| a **properties-only** Point, no embedding | **~356 B / node** | isolated disposable FalkorDB container, 1,000-node batches, `INFO memory used_memory` **delta** (2026-09-21) |
| a Point **carrying a 384-dim `vecf32` embedding** | **~4,701 B / node** (measured total for the props+embedding batch ⇒ the **vector adds ≈ 4,345 B**) | same run |
| **`product/pricing.json` declares** | **1,024 B** | `billing.cost_basis.bytes_per_node` |

⚠️ **The issue body recorded this as *"~5,057 B (356 props + 4,701 vector)"* — that adds the base props twice.** The probe measured `props-only: 355.6 B/node` and `props+embedding: 4,700.9 B/node`, so the *embedded node's own total* is **≈ 4,701 B**, and the vector's marginal delta is `4,701 − 356 ≈` **4,345 B**. The corrected pair is used throughout.

**⚠️ MEASUREMENT-COMPATIBILITY SPOTLIGHT — the 4,701 B figure and the doc's 1.50 KB stored / 2.05 KB resident figure are NOT the same quantity, and the two must not be interchanged.**
- `4,701 B` is the **marginal `used_memory` delta of one embedded node** in a **fresh, small instance** — an allocation-level measurement that includes the node's own overhead, with **no vector index built**. It is a **floor for marginal growth**, not an amortised per-vector cost.
- `STORAGE-ARCHITECTURE.md` §12.1 gives **1.50 KB stored** (the raw `vecf32` payload) and **2.05 KB resident** (payload + its share of HNSW).
- **The check that settles it:** if 4,701 B/node were the resident cost, 33,580 embeddings would occupy **~158 MB — more than the whole 141 MB graph**, and `Point` attributes alone (≈20,900 embedded Points — **derived from the counts**, 33,580 total − 7,859 `:Object` − 4,798 `:Event` — × 4,701 B ≈ 98 MB) would exceed the measured **50 MB `Point` attribute** line in §3.5. **So the 4,701 B figure cannot be multiplied by the embedding count.** The graph-wide number must come from the measured estimate (§3.5), which is why the pricing table below uses denominators, not the probe.

**Graph-wide blend** (the number a pricing model actually needs) — 141 MB over the counted set, at **$73/GiB**. *(The vendor quotes "/GB". This table treats that as "/GiB", i.e. MiB-based; reading it literally as 10⁹ B would change the figures by **≈7 %** — 73 × 1.0737 = $78.4/GiB — so every $/10k row below is ≈7 % low under the literal reading. The convention is chosen explicitly here so the numbers can be reproduced.)*

| denominator | bytes/node | B per 10k nodes | $/10k nodes/mo |
|---|---|---|---|
| declared `bytes_per_node` (1,024 B) | 1,024 B | 10.24 MB | **$0.70** |
| **resident labelled nodes (51,012)** | **≈ 2,750 B** | 27.5 MB | **$1.87** |
| the storage doc's ≈3 KB working figure | 3,000 B | 30.0 MB | **$2.04** |
| **cap-counted nodes (24,965)** | **≈ 5,600 B** | 56.0 MB | **$3.81** |

⚠️ **The declared 1,024 B matches no state the system runs in.** Read the two ratios with their denominators attached, because the two figures are different *kinds* of number: **5,600 B** is a **graph-wide blend on the cap denominator** (141 MB ÷ 24,965 cap-counted nodes), while **356 B** is a **marginal isolated-probe** figure (one properties-only Point, no index, in a fresh instance). So: the declared constant is **≈5× below the cap-denominator blend** (1,024 vs 5,600 B), and **≈2.9× above the marginal probe** (1,024 vs 356 B) — *not* two ratios over the same quantity. **The measured range is ≈$1.87–$3.81 / 10k nodes/mo**, and the **decided overage basis (≈$2 / 10k)** sits at the **low end** of it — a fact for the owner, not a recommendation.

### 3.5 Memory composition (measured estimate, 2026-09-24) **[MEDIUM]** ⚠️ emerging — a sampling estimate, and the index attribution is unresolved

`GRAPH.MEMORY USAGE` (SAMPLES 100) on the live graph:

| | MB | of the graph |
|---|---|---|
| **`total_graph_sz_mb`** | **141** | 100 % |
| `indices_sz_mb` (**all** indexes) | **45** | 32 % |
| `Point` node attributes | 50 | 35 % |
| `Object` node attributes | 13 | 9 % |
| `Event` node attributes | 9 | 6 % |
| `GraphEvent` | 11 | 8 % |
| `Source` · matrices · edges · misc | ~13 | 9 % |

**Stored vector properties ≈ 50 MB** (Point ~31 + Object ~11.8 + Event ~7.2 at 1.5 KB each) **plus the HNSW portion of the 45 MB index.** ⚠️ The index total includes every fulltext index too, so the honest statement is **"around half the graph is the vector class"** — a range, not a point.

**The cost model, settled (§16.1):** FalkorDB Cloud bills **provisioned instance RAM** ($0.10/GB-hour ≈ **$73/GiB/mo**), the whole graph must be in RAM (≤ 75 % of instance), and **there is no per-graph eviction on Cloud** — the only documented removal is `GRAPH.DELETE`. **An idle tenant costs exactly what an active one costs.**

---

## 4. Where the volume actually is — the evidence-first verdict **[MEDIUM]** ⚠️ emerging (class rows shown; the volume attribution is the doc's estimate)

Per the owner's standing rule, **the rows come first, the verdict after.**

### 4.1 `:Object` — 12 real rows (of 7,859 enumerated; full set in the companion brief)

| objectKind | name |
|---|---|
| `core:standard` | `M` |
| `core:document` | `note` |
| `core:document` | `§7.7` |
| `core:tool` | `REF_RE` |
| `core:tool` | `clauses()` |
| `dev:risk` | `the # unmeasured row` |
| `dev:incident` | `the #1005 leaked-redislite-servers incident` |
| `core:document` | `the #1214 scoping draft` |
| `dev:incident` | `the #1250 defect` |
| `dev:incident` | `the #1261 merge rail` |
| `agent-ops:rule` | `the "these two must move together" contract` |
| `core:document` | `the plan doc anchor` |

**Verdict:** **`:Object` is 7,867 nodes — 31.5 % of the 24,965 capped set** — and **4,900 of 7,859 enumerated (62.3 %) are named `the <X>`**, a definite description rather than a name. **`Object` also carries `~11.8 MB` of embeddings with no vector index behind them** (§3.5; `#4997`).

### 4.2 `Point{pointKind:event}` — 18 real rows (the episodic class)

*(Quoted content, truncated; markdown markup stripped for table rendering. The **id column carries the full row id**, `<session-uuid>_t<N>` — the `_t<N>` turn suffix is what makes a row individually addressable; the source session alone does not.)*

| id (full) | content (truncated) |
|---|---|
| `ac338d28-2e18-4542-acc6-f450adb985c1_t0` | [user] You are spawned in the worktree /Users/danielospina/…/agent-infra/.wor… |
| `ac338d28-2e18-4542-acc6-f450adb985c1_t1` | [assistant] Item 1 failed (3 tests). Continuing with items 2–5. |
| `ac338d28-2e18-4542-acc6-f450adb985c1_t2` | [assistant] Item 2 ✓ exit 0; Item 3 ✓ 60000; Item 4 identical before/after. Now item 5. |
| `ac338d28-2e18-4542-acc6-f450adb985c1_t3` | [assistant] 3 tests fail in test 30. Let me reproduce one failing case in isolation (temp co… |
| `ac338d28-2e18-4542-acc6-f450adb985c1_t5` | [assistant] Proof A ✓ (parent fail-open exit 0; current exit 1 with cap-drift). Now Proof B. |
| `ac338d28-2e18-4542-acc6-f450adb985c1_t6` | [assistant] Proof B ✓. Now Proof C — locate test 25's heredoc. |
| `ac338d28-2e18-4542-acc6-f450adb985c1_t8` | [assistant] All proofs pass. Cleaning up temps and confirming repo untouched. |
| `ac338d28-2e18-4542-acc6-f450adb985c1_t9` | [assistant] Verification report  Repo state note: worktree is dirty at start and end — M d… |
| `1b7e34f8-265d-46a0-b46f-033918037757_t0` | [user] You are clearing a hosted deploy freeze for the Tortoise beta. This is a bounded prod… |
| `1b7e34f8-265d-46a0-b46f-033918037757_t2` | [assistant] Before state captured. Now checking openapi.json before, then dispatching step 1. |
| `1b7e34f8-265d-46a0-b46f-033918037757_t3` | [assistant] Before baseline confirmed (112 paths, no /v1/organizations). Dispatching step 1. |
| `1b7e34f8-265d-46a0-b46f-033918037757_t4` | [assistant] Dispatched supabase-deploy.yml → run 35136229640. Polling. |
| `1b7e34f8-265d-46a0-b46f-033918037757_t6` | [assistant] ✓ supabase-deploy.yml → success. Immediately dispatching step 3 (deploy-hosted.y… |
| `1b7e34f8-265d-46a0-b46f-033918037757_t7` | [assistant] deploy-hosted.yml dispatched → run 35136316946. Polling. |
| `1b7e34f8-265d-46a0-b46f-033918037757_t8` | [assistant] packaging-smoke ✓; deploy-api in progress (this is the job containing the drift … |
| `1b7e34f8-265d-46a0-b46f-033918037757_t9` | [assistant] Check migration drift (fail-closed) passed (was the blocking step). Fly deploy i… |
| `1b7e34f8-265d-46a0-b46f-033918037757_t5` | [assistant] Run 35136229640 in progress on 6d707851. Continuing to poll. |
| `1b7e34f8-265d-46a0-b46f-033918037757_t1` | [assistant] I'll execute this bounded deploy operation. First, capturing the "before" state,… |

**Verdict:** **`pointKind:event` is 21,214 nodes — 55 % of all Points — and is quota-free while fully RAM-resident.** These are episodic session/turn records, and the 18 rows above are turn-by-turn procedural records — run ids, job names, status words — a *qualitative* reading of that shape, and **it is not the same test as §6.1a's predicate**: the two predicates tested there are **proposition-shaped** (they ask whether a sentence states a durable proposition) and are not designed to catch a turn record, which is why both returned **0/40** on this class. So "several rows are identifier-only" is a hand reading, **not a measured drop rate** — the measured statement is *"no tested predicate drops any of the 40 sampled event rows"* (see §6.1a for the reconciliation). **Every one of them attempted an embedding at creation: there is no `is_episodic` guard on the embedding call in `create_point`** (`sdk.py:2989`; the encode is `sdk.py:3260-3267`). *(The issue's own citation, `sdk.py:2686`, is stale at `c4580b083` and points at an unrelated helper.)*

### 4.3 `:Event` label — 16 real rows (a measurement gap, shown)

The search surface exposes `content` from `n.subject`/`n.name`. All 16 sampled rows returned **empty content**, with `eventKind: decision`:

| id (truncated) | eventKind | content |
|---|---|---|
| `ev_bad7e734619279e25597568cbf2a966` | `decision` | *(empty)* |
| `ev_8f217b41c12e7684c6ba0a8a70661e9` | `decision` | *(empty)* |
| `ev_d0aadedc6fa8f811e497e11609bcbdd` | `decision` | *(empty)* |
| `ev_879523665ec1bdc3348bfeb9afd88e4` | `decision` | *(empty)* |
| `ev_06ace8ba64d8eec3cafbf5b6356e874` | `decision` | *(empty)* |
| `ev_034d927f0d754868996b0c3dcfff4bd` | `decision` | *(empty)* |
| `ev_fbafefd3c2f4d49eb8b46b549b2a99d` | `decision` | *(empty)* |
| `ev_d5c0b35d509bc3d0c286d8ba5488e80` | `decision` | *(empty)* |
| `ev_61463137f79759a0c261e398bc3645e` | `decision` | *(empty)* |
| `ev_9746be0ad31d40b6530e7337b19283c` | `decision` | *(empty)* |
| `ev_2e19a10dd2333d15f0156e7b62a4282` | `decision` | *(empty)* |
| `ev_7aa0db6c164a728017c249423bc5362` | `decision` | *(empty)* |
| `ev_71b6aad040f6d0166bd30f9420625c8` | `decision` | *(empty)* |
| `ev_4d5248e5f82fa060500376cacee771b` | `decision` | *(empty)* |
| `ev_a5bc32e8b5b6955c1e0b694c21d8ed9` | `decision` | *(empty)* |
| `ev_9fbeb75b3a186fb78b31cbc7d4d8343` | `decision` | *(empty)* |

**Verdict — stated as a gap, not a defect:** the search surface **cannot show what text an `:Event` embedded.** The Event writer *does* embed a derived `event_content` for every `eventKind != "meeting"` (`projection/entities.py:1938-1951`), and §12.1b measured **4,798 `:Event` embeddings, unindexed**. So the class is paid for and unindexed, **and its embedded text is not visible through the surface a reader would use to judge it.** That is a **measurement to close**, not a claim.

### 4.4 The three causes, separated

- **(a) writing the wrong things** — present: **62.3 % of `:Object` names begin with `the `** (a definite description rather than a name). This is a *mention class*, not a synonym for "non-referential": the sampled rows include identifier-carrying definite descriptions (`the #1250 defect`, `the #1005 leaked-redislite-servers incident`), so most are non-referential but the class is not uniform. The owner's position (*"less noise is good"*, §16.3) already governs.
- **(b) writing too many things** — present: the capped set is **2.04× smaller than the resident set**, Objects are 31.5 % of the capped set, and the largest class of all (episodic turns, 55 % of Points) is exempt from the cap entirely.
- **(c) per-node cost** — present and **measured**: the declared constant matches no running regime (§3.4); the vector class ≈ half the graph (§3.5).

---

## 5. External findings (context for the cost model — not new ground)

### X1 — RAM-resident graph cost is a provisioning problem, and the field's answer is *eviction* **[HIGH — 3+ independent]**
Multiple sources converge that memory-first graph systems tie cost to the dataset held in RAM, and that keeping graph topology + vector indexes resident for inactive tenants creates provisioning pressure. **Zep's own engineering blog states they evict idle graphs to object storage** to reduce memory spend.
*Sources:* Zep engineering — *Why We Built a Graph Database Service for Agent Memory*; HydraDB *Multi-Tenant AI Agent Database*; Memgraph reviews (in-memory datasets must fit in RAM); Exasol in-memory failure-mode summary.

**⇒ For us:** this is the external statement of §3.5's "no eviction on Cloud" fact. **The capability Zep describes is exactly the FalkorDB Enterprise-only offload** that §16.1 says is the strongest argument for self-hosting later — **deferred by D11, and not a proposal here.**

### X2 — Per-vector HNSW memory has a published formula, and ours matches it **[HIGH — 3+ independent]**
The field's sizing formula is `1.1 × (4d + 8M)` bytes/vector — OpenSearch, AWS, and ScyllaDB all publish equivalent forms; Milvus shows graph overhead separate from raw vectors; the layer-0 `maxM0 = 2M` doubling is documented.
*Sources:* OpenSearch docs (k-NN memory-optimized types); AWS Big Data Blog (k-NN at billion scale); ScyllaDB vector-search sizing; Milvus *Index Explained*; HNSW architecture write-ups.

**⇒ For us:** with d = 384, M = 16 → **1,830 B ≈ 1.79 KB/vector** of HNSW overhead. **But our own measurement is BELOW that, so the formula is an upper bound here, not a confirmation:** 1,830 B × 33,580 = **61.4 MB (58.6 MiB)**, which exceeds the *entire* 45 MB `indices_sz_mb` — and that 45 MB also contains every fulltext index, not just HNSW. So the measured total **bounds our whole index at ≤1.34 KB/vector** (`45 × 10⁶ B ÷ 33,580 ≈ 1,340 B` — decimal MB, the same basis as §3.5 and the storage doc's 33,580 × 1,536 B ≈ 51.6 MB), below the formula's HNSW-only 1.79 KB. The layer-0 doubling and `M` in our engine's configuration are not pinned by this read, so the honest statement is: **the published formula predicts ≈1.79 KB/vector; our measured index total is ≤1.34 KB/vector across *all* indexes — the formula over-predicts for our configuration and must not be substituted for the measured number.** (Note the same non-identity: the storage doc's **2.05 KB resident** = 1.50 KB payload + **0.55 KB** HNSW share, itself far below the formula's 1.79 KB HNSW-only term — the three figures are not on one basis.)

### X3 — Quantization is a real lever, but *not on hosted FalkorDB* **[HIGH — 3+ independent]**
Scalar (int8) quantization gives **~4× compression with <1 % recall@10 loss**; binary gives up to 32× at 30–40 % recall loss unless rescaled. **But this requires an engine that stores the vectors in a quantized form** — our vectors are `vecf32` inside FalkorDB.
*Sources:* Qdrant scalar/binary quantization docs + TurboQuant article; HuggingFace *Binary and Scalar Embedding Quantization*; Featherstore quantization guide.

**⇒ For us:** quantization is **not applicable today** — it becomes a lever only if V1 (`#4997`/§12.1c) moves the vectors to a store that supports it. **Recorded so it is not re-derived as an available optimisation.**

### X4 — The category charges for the *flow*, not the *stock* **[MEDIUM — 2 independent categories]**
Surveyed agent-memory pricing meters **adds and retrievals** (Mem0: memory adds/retrievals) or **credits** (Zep: ~2 credits per 700 bytes of episode content) — **no surveyed product prices stored bytes**.
*Sources:* Mem0 pricing; Zep pricing summaries (toolradar, maximem TCO table); Atlan agent-memory frameworks overview; Vectorize best-agent-memory-systems.

**⇒ For us:** this corroborates the storage doc's finding that *"the category charges for the flow and gives away the stock"* — and it means **our fixed-subscription + growing-stock shape is the outlier, not the norm.** That is a *structural* observation for the owner, not a pricing proposal.

### X5 — ⚠️ ADVERSARIAL: vendor per-node figures are workload-benchmark noise, not a cost basis **[MEDIUM — 2 independent categories]**
FalkorDB's published comparisons quote whole-instance RAM (100 MB vs 600 MB; 496 MB vs 2,668 MB) on **their own datasets** — there is **no published per-node memory figure** from any vendor, and the numbers vary by 1–2 orders of magnitude across their own benchmark pages (6 MB–17 MB in some tests, ~500 MB in others).
*Sources:* FalkorDB vendor comparison pages and benchmark site; Atlan 2026 independent benchmark summary.

**⇒ For us:** **our own measurement is the only available cost basis** (as §12.1d concluded for latency). A vendor or third-party figure must not be substituted for the measured 2.75–5.6 KB range.

---

## 6. Ranked optimisations on the current stack (write-path levers only)

**No item below changes a price or a cap.** Savings are stated as *bytes released*, and each names the surface it touches. **This list holds the two write-path levers (§6.1–§6.2) and nothing else.** Deployment and engine-hosting items are deliberately **not** ranked here — they are recorded and deferred as **§6.6 (per D11)**, so this section cannot be read as proposing a migration.

### 6.1 EMBED gate — stop embedding identifier-only text *(highest measured leverage on the dominant cost class)* **[MEDIUM]** ⚠️ emerging — the policy is decided; the saving is unmeasured
- **What:** enforce the declared policy at a second point — *"a row earns a vector if its embedded text carries meaning that its identifiers do not"* (`STORAGE-ARCHITECTURE.md` §12.2a, **already DECIDED 2026-09-23**).
- **Write path:** the embedding submission step (`sdk.py:2989` `create_point`; encode at `:3260-3267`) + the extractor's `EMBED` decision (the missing step G2 in the extractor doc).
- **Expected saving:** the vector class is **≈ half the 141 MB graph** (§3.5). Removing the identifier-only subset shrinks the largest measured class; **the exact fraction is Stage-1 material, not yet measured** (§6.1a).
- **Contradiction check:** none — it *enforces* an existing declaration.

#### 6.1a Stage-1 sample (owner review required before any implementation)

Per the owner's standing **small-sample-first** method, two candidate identifier-only predicates were applied **by hand** over **80 real rows** (40 `statement` Points, 40 `event` turn Points, read live 2026-09-24), and the Object class was tested separately.

**Result — measured, and it REFRAMES the lever:**

| class | sample | would-DROP (strict predicate) | would-DROP (loose heuristic) |
|---|---|---|---|
| `statement` Points | 40 | **0** | **2** (5 %) |
| `pointKind:event` Points | 40 | **0** | 0 |
| `:Object` (full set) | 7,859 | — | **4,900 (62.3 %)** — the definite-description class |
| `:Event` label | 4,833 (measured 4,798 embedded) | — | text not visible in the read surface (§4.3) |

**⇒ The identifier-only class is NOT concentrated in extracted statements** — a statement is a proposition by construction, so the strict predicate drops **0/40** and even a generous heuristic drops only 5 %.

**⚠️ Reconciliation — the event class is a *measured zero* and a *qualitative reading*, and the two must not be conflated.** Both predicates returned **0/40** on `pointKind:event`, yet §4.2 hand-reads those rows as identifier-heavy turn records. These are not in conflict once the predicates' shape is stated: **both are proposition-shaped** — they ask whether a row states a durable proposition — and a turn record (a run id, a job name, a status word) is not shaped that way, so the predicate cannot fire on it. **The measured fact is *"no tested predicate drops any of the 40 sampled event rows"*; "the class the rule names lives in the episodic class" is an inference from §4.2's reading, not a measured drop rate.** Making it measurable is the Stage-1 deliverable, and until then the episodic share is **unquantified**. The one *measured* large class remains `:Object` (**4,900 / 7,859 = 62.3 %** definite descriptions, no description field) — which is what §12.2's own table says (`Object{"the plan doc"}` → no vector).

**The 2 looser-heuristic rows the owner must rule on — these are the boundary:**

| verdict | real row (id truncated) | content |
|---|---|---|
| **DROP? (loose)** | `pt_024b3622bb6aeb57f9ebda426` | `.github/workflows/python-ci.yml is byte-identical to origin/main, so the change adds no CI cost.` |
| **DROP? (loose)** | `pt_c2437a2c83e728a67243213ad` | `tests/_html_links.py:174-183 marks the same CDATA comparison as the pre-fix result.` |
| keep | `pt_2ebe00ecade8689d7aaab97e1` | `The older test_… can be deleted without losing coverage; the redundancy …` |
| keep | `pt_21bec7dfd034ed4168b032df6` | `The _support_cdata docstring's example contradicts the code it documents…` |
| keep | `pt_84740f65a58245835c1e4c935` | `Every cross-reference in the diff resolves and agrees: #4111, #4118 including its comments, #3970…` |
| keep | `pt_93041c979db1152c1a9b2244e` | `The signup duplicate-id fix ships six id-uniqueness tests, not the four the PR body claims.` |
| keep | `pt_1fa7550d632bf9f1a3d75dae7` | `The worktree was left unmodified during the review.` |
| keep | `pt_d40974b6fcac5bd57d6c2e0f2` | `The full guard suite returns 166 passed, not the 149 passed the PR body claims.` |
| keep | `pt_c572bc5bda3b3af1c0f48fc4d` | `The PR body must not be blanked if it carries the review record.` |
| keep | `pt_6a98799b87e22eec83a0ee3e7` | `The _support_cdata override, the fix for #4118 divergence 2, is exercised by no test…` |

**⚠️ Both `DROP?` rows are *file/line facts whose identifiers carry the meaning* — and a decision could turn on them.** That is precisely `#4899`'s known false-positive class (*"a bare `#\d{3,}` in a working note is discardable; a PR number a decision turned on is not"*). **Nothing is implemented until the owner has reviewed input and output rows, and the expected saving is therefore UNMEASURED.**

### 6.2 Write less of the *capped* class — the extractor's selection gate **[MEDIUM]** ⚠️ emerging — target, not a measurement
- **What:** S2.2 VET + `DISCARD` ([#4899](https://github.com/daniel-ospina/tortoise/issues/4899)) on survivors, and the missing **S0a/S0b source dedup** (whole-narrative saves).
- **Write path:** `extractor_v2.py`; `search_graph`/`resolve_entities`.
- **Expected saving:** the storage doc's own honest figures — **~2× mechanical; ~10× is a target, not a measurement** (§6.1). **Not a cost estimate; do not quote it as one.**
- **Contradiction check:** none; it is the in-flight extractor work (E1).

### 6.5 ⛔ Explicitly NOT levers on the current stack **[HIGH]**
- **Quantization** — not applicable to `vecf32` on hosted FalkorDB (X3).
- **Per-tenant index partitioning** — fails at ~2,100 tenants (lock exhaustion) and is unusable at 500; **withdrawn, not available** (`#12.1a`).
- **Deriving/deleting `aboutObject`** — refused by the `OVERRIDES:` ruling; it also breaks two public surfaces.

### 6.6 Deferred — recorded, NOT proposed (per D11) **[HIGH for the record; no action here]**

These two items sit **outside the ranked list above** because each is either an ops/deployment action or an engine-hosting change, and neither is a write-path lever. They are recorded so the picture is complete; **this brief proposes neither.**
- **Densify — pack more graphs onto one instance** (ops/deployment; ≤ 75 % of instance RAM). *The only lever that lowers per-tenant cost without dropping anything.* Saving is proportional to achieved density and needs the FalkorDB tenancy model, **not measured here**. It is not an engine change, so D11 does not block it — but it is not a write-path optimisation either.
- **The storage layer — the deferred lever is *self-hosting first*.** D11's named revisit trigger → price **self-hosted FalkorDB (SSPLv1, same engine, no rewrite)** before any engine change. The Postgres+pgvector direction is `#3998`/D30's, deferred. *Expected saving if ever taken:* removes the $73/GiB rent; gated on ops burden and the restore drill (`#3895`). ✅ **This is D11's own wording — recorded, not proposed.**

---

## 7. What is measured vs what is not

| question | status |
|---|---|
| node composition by class | ✅ measured (§3.1) |
| the four counting sets and their live values | ✅ measured (§3.2) |
| claims-per-source distribution (p50/p90/p99) | ✅ measured (§3.3) — the *reconciled* metric is p50 0 / p90 20 / p99 37 |
| bytes per embedded node, **marginal, isolated probe** | ✅ measured (§3.4) — 4,701 B `used_memory` delta for a props+embedding node (floor, no index; **not** a graph-wide blend) |
| graph-wide blended bytes/node | ✅ derived from a measured estimate (§3.4) — ~2.75 KB, denominator-dependent |
| memory composition and the vector share | ✅ measured estimate (§3.5) |
| **the actual FalkorDB invoice at our footprint (M2)** | ❌ **not measured** — no real invoice exists yet |
| **the share of embeddings on identifier-only rows** | ❌ not measured — the EMBED gate's saving (Stage 1, §6.1a) |
| **whether the value budget is enforced cumulatively per session** | ❌ not settled by §3.3 |
| **our own on-disk edge size** | ❌ estimated only (`STORAGE-ARCHITECTURE.md` §11.1) |
| **`GraphEvent` residency** | ⚠️ `graph_size` (51,012) equals the taxonomy sum, so GraphEvent is not in that count; its 11 MB is from the memory estimate. **Reconcile before sizing.** |
| **the journal's growth (the missing cost line)** | ❌ **no measurement exists** — and §3 of the storage doc requires it to carry the full derived payload |
| **egress + IOPS** | ❌ **not modelled, not measured** |

---

## 8. Owner questions (a decision, not research)

1. **`product/pricing.json` → `billing.cost_basis.bytes_per_node`** currently declares **1,024 B**, which matches no measured regime (graph-wide blended ≈ 2,750 B; the isolated marginal probe ≈ 4,701 B for a props+embedding node / 356 B keyword-only; **the probe figure is a marginal measurement and is not a blended cost**). **Replacing it is a pricing input, and pricing is deferred by the owner.** The measurement is recorded here; **the change is the owner's to route** (and `#4331`'s decided ~$2/10k sits at the low end of the measured range).
2. **Which denominator is *the* node for pricing?** the cap set (24,965), the displayed set (38,309), or the resident set (51,012)? The metered quantity and the costed quantity are different sets by 2×. **This is the single largest ambiguity in the cost model** and it is a policy choice.
3. **The episodic class is RAM-resident and uncapped** (21,214 turn Points). **Quota-free is not cost-free.** Whether the user-facing meter should reflect residency is a pricing decision, deferred.
4. **D11's revisit trigger** — no action; recorded so it is not re-opened early.

---

## 9. Source confidence summary

| claim | tier | sources |
|---|---|---|
| The four counting sets differ; cap = 24,965, displayed = 38,309, resident = 51,012 | **HIGH** | live reads, arithmetic verified (§3.2) |
| An embedded node costs ~4,701 B of **marginal `used_memory`** in an isolated fresh instance (the vector adds ≈4,345 B over the 356 B props-only node); this is an allocation-level floor, **not** the graph-wide per-vector cost (≈1.5 KB stored / 2.05 KB resident) | **HIGH** | independent container measurement + reconciliation (§3.4) |
| The declared 1,024 B matches no running regime | **HIGH** | §3.4 |
| The vector class ≈ half the graph | **MEDIUM** ⚠️ emerging | measured estimate, index attribution unresolved (§3.5) |
| RAM-resident graphs need provisioning; the field's answer is eviction | **HIGH** | X1 (3+) |
| HNSW per-vector memory ≈ `1.1(4d + 8M)` | **HIGH** | X2 (3+) |
| Quantization is unavailable on hosted FalkorDB | **HIGH** | X3 (3+) |
| Agent-memory category charges flow, not stock | **MEDIUM** ⚠️ emerging | X4 (2) |
| Vendor per-node figures are benchmark noise | **MEDIUM** ⚠️ emerging | X5 (2) |

**Adoption gate line:** `Adoption gate: adopt (record the measured bytes/node; enforce the EMBED gate's declared policy) | refuse (engine change — D11; volume target — O2; price/cap change — owner-deferred; shrinking the connection layer — OVERRIDES) | owner question (cost_basis replacement, and the metered-vs-costed node set) | deferred (the storage-layer migration, per D11's revisit trigger)`.

---

*Investigation only. This brief proposes no price change, no cap change, and no migration. Every class claim is preceded by real rows or a named measurement, and every recommendation names the decision it does or does not contradict.*
