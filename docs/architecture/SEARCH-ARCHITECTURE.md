---
title: "Search architecture — where the vector index lives, and what the graph engine keeps"
type: architecture
domain: platform
doc_status: live
created: 2026-11-07
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Search architecture — where the vector index lives, and what the graph engine keeps

**Decision (owner, 2026-11-07):**

1. **The vector index and the embedding payload move to Postgres** (`pgvector`). This **resolves V1**
   (`#4997` / `STORAGE-ARCHITECTURE.md` §14.3): the vector index does **not** live in FalkorDB.
2. **The text index stays in FalkorDB for now.** Moving it to a Postgres ranker is a *measured*
   regression (§6.2), so it is deferred behind a measurement, not adopted on a hunch.
3. **Traversal, writes and EP stay in FalkorDB.** The Cypher surface — an order-of-magnitude **~1,900 statements**, an estimate rather than a census — is untouched.
4. **The graph stays complete for private-beta and becomes a bounded window only after M2 measures it**, sized by the tier's cost budget — §1.5, §7. The cost during private-beta is **knowingly above** the 30 % target (§8).

**The product goal:** *"a high quality search system with manageable cost, speed matters but is not the
top criterion"* (owner). Every ordering below follows that sentence.

**Sibling documents:** `STORAGE-ARCHITECTURE.md` owns authority per layer (§2.0/§3) and the cost model
(§17.4); `CONSUMPTION-MODEL.md` owns metering; **this document owns where each search leg executes, and
what the graph engine holds.**

---

## 1. The method ruling that made this decidable

**We have no dataset.** The only figures available come from dogfooding runs of **superseded product
versions**. Owner ruling, 2026-11-07: *"it doesn't matter if 17% is 10 or 30%, what matters is the
engineering principles. Then we'll measure and optimise after beta launch."*

⇒ **No decision below rests on a measured proportion of the graph.** The share a hot cache would hold is
an **output** we measure from real tenants, never an input. An earlier draft sized the split from an
estimated "structure ≈ 17% of the graph"; that figure was a subtraction across three measurements and two
different tenants, taken from a total the vendor's own field list shows is incomplete (FalkorDB's `GRAPH.MEMORY USAGE`
readout is blind to the HNSW `vectorset` module — `#5331`, `docs/evidence/10000-turns-bytes/README.md` — so it
under-reports the vector bytes, while `INFO memory`'s `used_memory` counts them; the two instruments are not
interchangeable and a figure from one is not comparable to a figure from the other). A published FalkorDB breakdown
puts non-index structure at **41 %**. **Our own share is unmeasured** — the earlier 17 % figure is deleted, the 41 % is a vendor-published breakdown of a *different* population (a graph without our label mix), and **no range we could state would be ours**. It is not an input to anything here.

**⚠️ PROVENANCE — the EXTERNAL figures this document leans on.** Each of the following is a **third-party published measurement, not ours and not reproduced here**: the **41 %** non-index structure; the **0.0015 nDCG@10** spread at 381,559 vectors; the **0-of-10** filtered-ANN result and its **0.975/0.847** relax-order recall; the **8–23 nDCG@10** Postgres-FTS-vs-BM25 gap; the cache studies behind §7's policy table (**62.4 %** naive-pinning miss vs **60.2 %** LRU, and 1-/2-hop closure prefetch raising hit ratio); and the **4.9–5.9×** rebuild-peak range; and the **~54 %** hit rate the best trace-aware policy reached for 2-hop expansions. They are cited by RESULT — their specific citations are **owed before this document is quoted as measurement evidence**, and the fix is to attach them, not to drop the claims. **Anything that reads as *our* measurement names its pin inline**, and a figure with neither a citation nor a pin is not evidence yet.

---

## 1.5 The phases, named — because "beta" was doing two jobs

The owner's two constraints are both in force and they sequence differently:

> *"we won't launch until all this is done"* · *"then we'll measure and optimise after beta launch"*

So the phases are separate, and each carries its own graph state:

| phase | who is using it | graph state | what is decided there |
|---|---|---|---|
| **pre-release** (now → private-beta invite) | nobody external; dogfood + internal | **complete** | build the architecture and the guardrails; measure M1, M4 |
| **private-beta** (the upcoming release) | invited customers | **complete** | the product is live; cost per tenant is above the 30 % target and accepted as a beta-scale cost (§8). M2, M3, M5 measure here |
| **post-measurement** | the same customers | **windowed** | the window is sized, armed and tuned **on real data**, per *"measure and optimise after beta launch"* |

⛔ **What that sequencing obliges — and it is the part that is easy to get wrong:** the machinery that makes
a windowed graph *safe* must ship **before** private-beta even though demotion is **off**. Otherwise
"optimise after launch" becomes a risky change made in front of live customers, and they experience it as a
quality change. **Live before private-beta:**

1. the **residency marker armed** (§7) so any answer computed over an incomplete view is *labelled* — the
   machinery exists and is currently **off** (`TORTOISE_RESIDENCY`);
2. the **seed-materialisation path** (§7), so a walk can start from a claim that is not resident;
3. the **rebuild headroom** measured and reserved (§7) — the #1 production risk;
4. the **shortfall assertion** on the vector leg (§6.1), without which a beta customer can silently receive
   fewer results than exist;
5. the **`null`-not-0.5 rule** for absent belief state (§6.3) — a live defect today, independent of windows.

**Cost during private-beta, plainly:** a complete graph is **$8.75/tenant** against a **$5.70** budget —
**154 % of target**. A knowing, bounded overspend on a small, known tenant count, taken because the
alternative (arming a window nobody has measured) risks the quality we rank first.

---

## 2. First principles — what a hot copy is actually for

A hot copy can buy only three things:

1. **Lookup by key** — a B-tree answers in microseconds. A cache buys nothing.
2. **Find by similarity** — needs an index. Postgres holds it on disk, and the operating system keeps the
   hot pages in memory **as part of the database we already run**. A separately rented RAM index buys a
   *guarantee*, not a capability.
3. **Walk the connections** — the only genuine candidate, and **not because the data is big**: a walk is
   *dependent steps*, so the win is keeping the whole walk inside one engine, **not** keeping bytes in RAM.

**"Inside one engine" ≠ "in RAM".** That is why the vector index can move to Postgres without losing the
walk: the walk stays a graph query in FalkorDB, reading nodes and edges, and only the *similarity
candidates* come from Postgres. Two systems are already in the path either way.

**The price multiple decides the rest** (§17.4.1; vendor pages verified 2026-10-10): FalkorDB rents
**provisioned RAM at $43.75/GB-month on the 8 GB tier ($73 on the 1 GB STARTUP tier)**, and a graph may occupy
**75 %** of it ⇒ **$58.33 per GB of graph data at the 8 GB tier** (the 1 GB tier's equivalent is $97.33 — the 778.7×
`STORAGE-ARCHITECTURE.md` §17.4 prints; 584× is the pure RATE ratio there). Postgres disk is **$0.125/GB**.
**~467× at the 8 GB tier.** An embedding's *bytes* are the same size in either store — which is
the point, not a defence: at these rates **every** byte of payload is 467× dearer in RAM, so the payload moves
with the index and is the bulk of the saving, not a rounding error beside it.

**And the vector move is quality-neutral on the evidence.** At 381,559 vectors — inside our range —
independent measurement puts pgvector/Qdrant/OpenSearch **within 0.0015 nDCG@10 of exact search**; the
benchmark's own conclusion is *"choose the engine for operations, not for quality."* No published case
shows a RAM-resident index returning better answers at this scale.

---

## 3. What runs where, after this decision

| job | home | change |
|---|---|---|
| authoritative state (claims, entities, connections, operators, text, embeddings, turns, narrative) | **Postgres** | none — already the record |
| **vector search** — ANN over embeddings | **Postgres** (`pgvector` + HNSW) | **MOVES** — was `db.idx.vector.queryNodes` (2 executing sites) |
| **text search** — BM25-family over 7 fields / 5 labels | **FalkorDB** (`db.idx.fulltext`, TF-IDF) | **stays** — see §6.2; the move is deferred behind M3 |
| **multi-hop traversal** (2-hop epistemic minimum: claim → operator → claim) | **FalkorDB** (Cypher) | none |
| **writes, projections, backup/restore** | **FalkorDB** (Cypher) | none |
| **EP state and computation** | state on graph nodes; **arithmetic in Python** | none — the EP-scalar carve-out stands (§6.3) |

**⚠️ Nothing about the epistemic or entity layer moves — it is already the record, and it stays there.**
`public.point` (claims **and** operator Points, `is_operator`), `public.relation` (every edge, including
`IMPL`/`NAND`), `public.object`, `public.subject`, `public.source`, `public.event` and `public.session`
are all Postgres tables, and Postgres is their authority (§4). What the graph holds is a **derived copy**
of that structure, and it keeps holding one — because the walk, the operator mediation and EP all run over
it. **This decision moves the vectors and *nothing else***: the structural copy is retained (complete for
private-beta, then windowed), which is exactly why the Cypher surface — an order-of-magnitude estimate, see the decision bullet — is untouched.

**The executing surface of the vector move is small and that is why it is tractable:** two
`db.idx.vector.queryNodes` calls (both argument orders), the vector-index creation, and the ranking code
behind them. Everything else in the search path is unchanged.

**What the move costs, stated so it is not discovered late:**

- `public.point/source/event.embedding` is **`jsonb`** today, and **`pgvector` appears in 0 of 79
  migrations** — so this is a **column-type migration plus a full index build and a backfill** of existing
  rows, not a query edit.
- **A walk starts from seeds.** Under a bounded window (§7) a seed may not be resident, so the walk must
  **materialise its seeds before traversing** — a write on the query path — priced in §7 with the rebuild
  headroom that makes it survivable, which §1.5 ranks as the #1 production risk.

---

## 4. Authority (unchanged, cited not restated)

`STORAGE-ARCHITECTURE.md` §2.0/§3: **Postgres is the system of record for every layer.** FalkorDB is a
derived cache, never authoritative; deleting it loses nothing recoverable from Postgres — with EP's
recomputed scalars as the one declared exception. Nothing here changes authority.

---

## 5. What "high quality search" means here, ranked

| # | property | why it outranks what follows |
|---|---|---|
| **Q1** | **Traversal is complete and correct** — a walk sees every connection that exists, and EP is computed over a closed component | a truncated graph does not return a worse answer, it returns a **different** one: a missing node silently becomes a prior (`coalesce(ep_alpha, 1.0)`) |
| **Q2** | **Nothing is silently invisible** — a search finds a claim or says it could not look | a shortfall reporting success is the failure class §17.3 exists to forbid |
| **Q3** | **Both retrieval legs work** — lexical and semantic, on all searchable labels | see §6.2: the lexical leg is the one a move would damage |
| **Q4** | **Latency**, measured | matters; never outranks a wrong or missing answer |
| **Q5** | **Cost inside the 30 % rule** | a hard ceiling, and the last discriminator |

---

## 6. The three things that can go wrong, and what each requires

### 6.1 Filtered vector recall — the top quality risk (move required, guard required)

Every tenant's vectors share one index, filtered by `org_id`/`graph_id` — the archetypal **correlated
filter** for HNSW post-filtering, where measurement elsewhere shows **0 rows of 10 returned at 0.1 %
selectivity**, both at stock settings and with `iterative_scan` tuned; relaxed-order scanning recovers
0.975 at 100k vectors but **0.847 at 1M**.

**Required before private-beta (§1.5):** a **per-tenant-size filter strategy** (exact scan for small tenants using the
record's existing `(org_id, graph_id)` index, partial index for the few large ones, partitioning above),
and a **shortfall assertion in the read path** — a tenant holding ≥ k rows must never quietly receive
fewer. A recall-only test passes while the product under-returns.

### 6.2 Text ranking — why the text index does NOT move yet

`ts_rank`/`ts_rank_cd` have **no IDF and no length normalisation**; independent measurement at 381k
vectors puts Postgres full-text **8–23 nDCG@10 points behind BM25**, with its hybrid scoring **below
vector-only** on all three datasets. FalkorDB, by contrast, pins **TF-IDF on absolute score magnitudes** —
so a move invalidates every absolute threshold and the recorded fusion weights (`{"vector": 1.5}` +
implicit 1.0), and `to_tsvector` cannot reproduce the product's tokenizer behaviour (`ERR-4032` →
`err4032` **plus** the adjacency phrase, `#8933`).

**Therefore:** the text index **stays** until **M3** measures a BM25-capable Postgres option (extensions
are candidates, not assumptions) with **score distributions**, not only ranks, plus the identifier
fixture. Moving it opportunistically would trade a known cost win for a measured quality loss — the
opposite of the ordering in §5.

### 6.3 EP state — the carve-out stands, and the fix is not to store it

`ep_*`, `confidence`, `posterior_*` and `baseline_set` are **recomputed from the structure and never stored
as authority**; the record schema carries **no column** for them and its `chk_point_props_no_derived`
`CHECK` **refuses them by name** (`supabase/migrations/20261012000003_record_epistemic.sql:128-130`, whose
table comment restates the carve-out). ⚠️ **The one narrowed exception, stated so this section does not
overstate the carve-out:** `baseline_source` / `baseline_alpha` / `baseline_beta` **are** columns —
PRIMARY when `baseline_source = 'set-by-author'`, because an author-set prior is a non-reconstructible
input rather than a derived value (the `#7955` correction to §17.5; `STORAGE-ARCHITECTURE.md:2052` says
the carve-out is **not** reopened by it). That ruling stands and nothing here reverses it.

**Which is why the retention unit must be a CLOSED component (§7):** a closed subgraph has no missing
boundary, so recomputing EP for it is **exact**, not approximate. Retention policy and the EP carve-out
therefore point the same way — and no schema change is needed.

**One live defect must be fixed independently of all of this, and it is already in the code.** A claim
with **no persisted α/β answers the neutral Beta(1,1) mean 0.5** (`search_engine.py:280` and `:339` state
the rule; `sdk.py:37208` repeats it) **and the ranker coalesces the value** — `coalesce(n.confidence, …)`
at `ranking.py:795` and `coalesce(n.confidence, 0.5)` at `ranking.py:1873`. `ingest.py:206` names the
same construct as *"the silent `coalesce(n.confidence, 0.5)` default"*. So **missing belief state returns
a plausible number instead of an error**, and it is indistinguishable from a computed one at the point
where ordering happens. **Rule: absent state is `null` with `ep_state: "absent"`, never 0.5** — a value
the ranker refuses to order on.

---

## 7. The window — what the graph holds once it must be bounded

**Why the window exists at all:** a *complete* graph costs **$8.75/tenant** against a **$5.70** budget
(30 % of $19). Density does not fix it — `$58.33` is already the DENSEST tier's per-GB rate, and density cannot
shrink a tenant's own graph bytes; it only spreads the *fixed* lines. **The window is what makes the pricing work at scale.**

⚠️ **Where the $5.70 comes from, because the document it amends records a different ceiling.** `STORAGE-ARCHITECTURE.md` §1
records the product constraint as *"a $19 price with < $9 total cost per user"*, and its M2 row prices against a
"$9 → $19 budget"; the **30 %-of-price** target here is the owner's 2026-11-07 sizing rule for the **storage line**,
and it is **strictly tighter** than that ceiling ($5.70 < $9). Both are quoted so neither is mistaken for the other:
against the total-cost ceiling a complete graph is **~97 % of it**, against the storage target it is **154 %** — and
the target is what governs the window. **The target's own recorded home is owed**; until it is recorded, this section
and §2.0's marker cite it as an owner ruling of that date rather than as a derived figure.

**⚠️ Why it is nonetheless NOT armed at private-beta (§1.5):** it is unmeasured. The bounded-cache
literature measures latency and hit rate, not answer quality, and the best trace-aware policy reached ~54 %
hit rate for 2-hop expansions — so arming it on a guess is exactly the quality risk this document ranks
first. Private-beta therefore runs **complete** and **above the 30 % target, knowingly** (154 %, bounded by
a small known tenant count), and the window is armed when M2 says it is safe. **The guardrails that make it
safe ship before private-beta regardless**, so the eventual switch is a configuration change with measured
justification, not a redesign in front of customers.

**The unit of retention is a CLOSED component, not a node.** A retained hub whose neighbours were evicted
pays for the node and still cannot complete the walk. Retaining closed neighbourhoods (1–2 hops) also
makes EP recomputation exact (§6.3).

**The policy, as researched:**

| rule | why |
|---|---|
| **retain closed components / 2-hop closures** | closure is what makes a walk and an EP recompute correct; measured cache studies show 1-/2-hop closure prefetch raises hit ratio for **every** policy tested |
| **cap the pin: count, bytes, AND a degree ceiling** | unconditional hub pinning is a *measured* anti-pattern ("they can thrash each other"; naive pinning scored 62.4 % miss vs LRU at 60.2 %). Degree is **U-shaped** — helpful mid-tail, harmful at the head; a single huge hub can eat a 77 MB window |
| **window-local touch counters + admission control** | recency beats static degree ranking, and adaptive policies beat every static one measured; eviction must be budget-triggered, not per-request, or the cycle "demote a leaf → the next query needs it → re-materialise → evict another leaf" becomes a storm. ⚠️ **The counter is WINDOW-LOCAL cache state — a different artifact from `tortoise/read_ledger.py`.** That ledger is TENANT-WIDE and is explicitly not a product input: `STORAGE-ARCHITECTURE.md` §17.4.6 states that reading it for any product decision *"would be a violation"* (it exists to measure `f`). Wiring it as the eviction signal is exactly the violation it names. |
| **reserve ≈6× the window in provisioned RAM for rebuild peaks** | **third-party measurement — citation owed (§1)**: re-materialising into FalkorDB peaks at **4.9–5.9× the resting size** — a 77 MB window can transiently demand ~380–455 MB, and the engine has **no per-graph memory cap**. This is the #1 production risk. Do not shard the window into many small graphs (`NODE_CREATION_BUFFER` costs 16 384 slots each) |
| **a walk materialises its seeds first** | search returns ids from Postgres; a non-resident seed must be written into the graph before traversing — a write on the query path, which is why the headroom above is mandatory |
| **label what was truncated** | a confidence within 2 hops of a truncated boundary is **refused**; beyond that it may be returned as degraded. ⚠️ **The two reason strings this rule needs are OWED, not shipped** — `degraded: "insufficient_graph"` / `degraded: "truncated_boundary"` appear nowhere in the tree at this head, and `STORAGE-ARCHITECTURE.md` §17.2 records `degraded` as one of four terms whose ONE home, `tortoise/status_vocabulary.py`, forbids a fifth, so the *reason* has to attach to `degraded` rather than mint a term. **Required before private-beta (§1.5).** What DOES exist is the marker's on/off mechanism (`residency.py`, `not-hot`, `TORTOISE_RESIDENCY`) and it is currently OFF |

⚠️ **The honesty item:** the bounded-cache literature measures **latency and hit rate, not answer
quality**, and the best trace-aware policy reached ~54 % hit rate for 2-hop expansions. **Whether a
windowed graph harms retrieval quality is unmeasured anywhere** — which is why §9 measures it before the
window is trusted, and why **private-beta** runs the graph **complete** (§1.5).

---

## 8. Cost

Rates per §17.4.1 (vendor pages, **verified 2026-10-10 — re-check before a published tier**): FalkorDB
$43.75/GB-month provisioned at the 8 GB tier (**$58.33 per GB of graph data** after the 75 % rule);
Postgres disk $0.125/GB; plan $25 + compute $60 per project.

| | complete graph (private-beta, §1.5) | windowed (post-measurement, §1.5) |
|---|---|---|
| graph per solo tenant | ~150 MB ⇒ **$8.75** | ~77 MB ⇒ **$4.49** |
| Postgres storage | **not counted here** — the payload lands here | **dominant line at PROJECT scale** (multi-GB across tenants; ~0.19 GB/tenant measured, `STORAGE-ARCHITECTURE.md` §17.4.6 — still cents) |
| vs the $5.70 budget | **154 %** — above, and known | **79 %** — inside |

⚠️ **The basis, because the two columns do not count the same components.** The two numbers do not have the same standing, and the
table is the one place that must say so. **150 MB is an ESTIMATE from `GRAPH.MEMORY USAGE`** — a sampling instrument, not an exact
allocation (`STORAGE-ARCHITECTURE.md` §12.1c: *"quote it as an estimate"*), and one §1 says under-reports the vector
bytes — so treat it as a dated estimate, not a bound.
**77 MB is the window's TARGET, not a measurement**: no windowed graph exists, and this document arms the window only
after M2 measures it (§1.5, §7), so it is the size the budget implies, and M2 is what decides it. **⚠️ The private-beta figure is an estimate whose error runs BOTH ways**, and neither direction is
priced here: the instrument under-reports the vector bytes (so $8.75 can be too LOW), while the ~50 MB of payload and
its index leave FalkorDB when they move (so it can be too HIGH). It is the number to replace with M2/M4, not a bound —
the payload's arrival belongs in the Postgres line, which this table marks as not counted rather than as negligible. The window (79 % of target) is what
makes the pricing work; the move is what reduces both the RAM multiple and the bytes it applies to.

⚠️ **The Postgres side is not free of unknowns, only of cost:** under both states the dominant unmeasured
lines are **compute tier, provisioned IOPS and egress**, driven by the **miss rate** — which nobody has
measured (§17.4.6). The table is the storage lines and says so.

---

## 9. Measurement plan

| # | measurement | decides |
|---|---|---|
| **M1** | vector move: recall@10 vs exact ground truth, p95, **and the shortfall rate at 1 % / 10 % / 50 % selectivity** | whether the move ships — a recall-only test is not sufficient (§6.1) |
| **M2** | **retrieval quality under a windowed vs complete graph**, on a fixed query set | the gate that ARMS §7 (post-measurement) — the literature has no answer |
| **M3** | text ranking: a BM25-capable Postgres option vs the current TF-IDF, **score distributions** + the `#8933` identifier fixture | whether the text index ever moves (§6.2) |
| **M4** | rebuild peak memory and latency per tenant at the real window size | the 6× headroom (§7), the #1 risk |
| **M5** | the **miss rate** per tenant | the compute/IOPS/egress tier |

---

## 10. Review gate — closed

Two independent adversarial reviews (memory competitors / state of practice; engineering claims) both
returned **STANDS WITH CHANGES**; neither found a case where a RAM-resident index returns better answers
at 10⁴–10⁶ vectors. A third and fourth pass validated the retention policy (`SOUND WITH CHANGES`) and the
EP-state question, and their corrections are carried: closed-component retention, capped pinning with a
degree ceiling, window-local touch counters, 6× rebuild headroom, `null`-not-0.5, and the measured
`4.9–5.9×` rebuild peak (third-party, §1). The corrections that *changed the decision* are recorded in §1 (no ratio as an
input), §6.2 (text does not move) and §6.3 (EP state is not stored).

**What would reopen this decision:** M2 showing a windowed graph loses retrieval quality beyond an
accepted bound; or M1 failing the filtered-recall bar; or a measured vendor/cost change that makes a
complete graph affordable.

---

## 11. Out of scope

Tier prices and overage values; the compute/IOPS/egress tier; retention (`#3853`); the session cap
(`#4010`); the `record_only` tier's scope beyond `:Point` (`#10017`); **the implementation order** —
deliberately last, per the owner: canonical docs first.
