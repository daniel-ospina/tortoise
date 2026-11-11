---
title: "Search architecture — where the vector index lives, and what the graph engine keeps"
type: engineering
domain: platform
doc_status: live
created: 2026-11-07
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutSubjects: search
aboutObjects: tortoise
---

# Search architecture — where the vector index lives, and what the graph engine keeps

**Decision (owner, 2026-11-07)** — items 1–3 are the owner's; **item 4 is the phase correction recorded in `#4051`
comment [`6714119028`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6714119028)** (§1.5), because
comment [`6713838871`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6713838871)'s own item 4 said
*"before launch":*

1. **The vector index and the embedding payload move to Postgres** (`pgvector`). This **resolves V1**
   (`#4997` / `STORAGE-ARCHITECTURE.md` §14.3): the vector index does **not** live in FalkorDB.
2. **The text index stays in FalkorDB for now.** Moving it to a Postgres ranker is a *measured*
   regression (§6.2), so it is deferred behind a measurement, not adopted on a hunch.
3. **Traversal, writes and EP stay in FalkorDB.** The Cypher surface — an order-of-magnitude **~1,900 statements**, an estimate rather than a census — is untouched.
4. **The graph stays complete for private-beta and becomes a bounded window only after M2 measures it**, sized by the tier's cost budget — §1.5, §7. The cost during private-beta is **knowingly above** the 30 % target (§8).

**The product goal, as this document records it:** a high quality search system with manageable cost, where
speed matters but is **not** the top criterion. That ranking is **this document's rationale, not a quoted
owner ruling** (no artefact carries the sentence), and every ordering below follows it; §5 states it as Q1–Q5
so a reader can disagree with the ranking explicitly rather than inherit it.

**Sibling documents:** `STORAGE-ARCHITECTURE.md` owns authority per layer (§2.0/§3) and the cost model
(§17.4); `CONSUMPTION-MODEL.md` owns metering; **this document owns where each search leg executes, and
what the graph engine holds.**

---

## 1. The method ruling that made this decidable

**We have no dataset.** The only figures available come from dogfooding runs of **superseded product
versions**. Owner ruling, 2026-11-07: *"it doesn't matter if 17% is 10 or 30%, what matters is the
engineering principles. Then we'll measure and optimise after beta launch."* — relayed in `#4051` comment
[`6713312036`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6713312036), which records
it as *"The owner's ruling"*; ⚠️ that comment's **v1 scope** (search AND traversal in Postgres, no hot cache)
was withdrawn by comment
[`6713838871`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6713838871) — the method
ruling quoted here was not, and §1.5's phase split is recorded in comment
[`6714119028`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6714119028).

⇒ **No decision below rests on a measured proportion of the graph.** The share a hot cache would hold is
an **output** we measure from real tenants, never an input. **No proportion of OUR graph is a valid input:** the
vendor's own field list shows the readout is incomplete (FalkorDB's `GRAPH.MEMORY USAGE` is blind to the HNSW
`vectorset` module — `#5331`, `docs/evidence/10000-turns-bytes/README.md` — so it under-reports the vector bytes,
while `INFO memory`'s `used_memory` counts them; the two instruments are not interchangeable, and a figure from
one is not comparable to a figure from the other), so a proportion computed from one is a subtraction across
instruments and tenants rather than a measurement. A published FalkorDB breakdown puts non-index structure at
**41 %**, and it is a vendor-published breakdown of a *different* population (a graph without our label mix), so
**no range we could state would be ours**. It is not an input to anything here.

**⚠️ PROVENANCE — the EXTERNAL figures this document leans on.** Each of the following is a **third-party published measurement, not ours and not reproduced here**: the **41 %** non-index structure; the **0.0015 nDCG@10** spread at 381,559 vectors; the **0-of-10** filtered-ANN result and its **0.975/0.847** relax-order recall; the **8–23 nDCG@10** Postgres-FTS-vs-BM25 gap; the cache studies behind §7's policy table (**62.4 %** naive-pinning miss vs **60.2 %** LRU, and 1-/2-hop closure prefetch raising hit ratio); the **4.9–5.9×** rebuild-peak range; and the **~54 %** hit rate the best trace-aware policy reached for 2-hop expansions; the engine's **no per-graph
memory cap** and its **`NODE_CREATION_BUFFER` of 16 384 slots** (§7's #1-risk row). They are cited by RESULT — their specific citations are **owed before this document is quoted as measurement evidence**, and the fix is to attach them, not to drop the claims. **Anything that reads as *our* measurement names its pin inline**, and a figure with neither a citation nor a pin is not evidence yet.

---

## 1.5 The phases, named — because "beta" was doing two jobs

The two constraints that sequence the work differ in kind, and only the second is quoted:

> *"measure and optimise after beta launch"* — the phase split this section implements is recorded in `#4051`
> comment [`6714119028`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6714119028). The
> header's four-part decision is comment
> [`6713838871`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6713838871) **items 1–3**, with
> **item 4** being that phase correction — because `6713838871`'s own item 4 said *"a budget-derived window
> **before launch**"*, which `6714119028` supersedes. The four-part decision is **not** `6713312036`, which
> `6713838871` supersedes.

and, as **this document's** sequencing obligation rather than a quotation: **nothing ships to private-beta until
the guardrails in §1.5 are in place**, because a window armed without them is a redesign performed in front of
customers.

So the phases are separate, and each carries its own graph state:

| phase | who is using it | graph state | what is decided there |
|---|---|---|---|
| **pre-release** (now → private-beta invite) | nobody external; dogfood + internal | **complete** | build the architecture and the guardrails; measure §9's M1, M4 |
| **private-beta** (the upcoming release) | invited customers | **complete** | the product is live; cost per tenant is above the 30 % target and accepted as a beta-scale cost (§8). §9's M2, M3, M5 measure here |
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
5. the **`null`-not-0.5 rule** for absent belief state (§6.3): absent state is `null` with
   `ep_state: "absent"`, never 0.5. The per-surface sweep named in §6.3 is part of this item;
6. the **two truncation reason strings** (§7) — `degraded: "insufficient_graph"` / `degraded:
   "truncated_boundary"` — because item 1 only makes an incomplete answer *labelled*, not *labelled with a
   reason a caller can branch on*.

**Cost during private-beta, plainly:** a complete graph costs **\$11.01/tenant** at the 40-tenants-per-instance
density — RAM \$8.75 + shared project line \$2.13 + record \$0.13 (§8.1) — against a \$5.70 storage budget, i.e.
193 % of it — the honest per-tenant total (comment [`6714119028`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6714119028), the phase
split, states 154 % as the beta cost; on this section's own basis that figure is the **RAM line alone** — §8.1's *"the
same, RAM line alone"* row — against the 193 % total). ⚠️ **It is also above the product's older recorded constraint** (*"a \$19 price with < \$9 total
cost per user"*, `STORAGE-ARCHITECTURE.md` §1), because \$11.01 > \$9 — and at beta-scale density it is higher
still (a 1 GB instance holds ~5 complete solo tenants at the §8.1 basis of 200 MB provisioned each, so the RAM
line alone is ~\$14.60 at the 1 GB tier's \$73). **This is a knowing,
bounded overspend on a small, known tenant count**, taken because the alternative — arming a window nobody has
measured — risks the quality this document ranks first. §7 records both ceilings so neither is mistaken for
the other.

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
candidates* come from Postgres. Two systems **will be** in the path either way — on the read side once the
vector move lands, and on the write side already.

**The price multiple decides the rest** (§17.4.1; vendor pages verified 2026-10-10): FalkorDB rents
**provisioned RAM at \$43.75/GB-month on the 8 GB tier (\$73 on the 1 GB STARTUP tier)**, and a graph may occupy
**75 %** of it ⇒ **\$58.33 per GB of graph data at the 8 GB tier** (the 1 GB tier's equivalent is \$97.33 — the 778.7×
`STORAGE-ARCHITECTURE.md` §17.4 prints; 584× is the pure RATE ratio there). Postgres disk is **\$0.125/GB**.
**~467× at the 8 GB tier.** An embedding's *bytes* are the same size in either store — which is
the point, not a defence: at these rates **every** byte of payload is 467× dearer in RAM, so the payload moves
with the index and is the bulk of the saving, not a rounding error beside it.

**And the vector move is quality-neutral on the evidence.** At 381,559 vectors — inside our range —
independent measurement puts pgvector/Qdrant/OpenSearch **within 0.0015 nDCG@10 of exact search**; the
benchmark's own conclusion is *"choose the engine for operations, not for quality."* — a third-party
conclusion, carried in §1's PROVENANCE list with its citation owed. No published case
shows a RAM-resident index returning better answers at this scale.

---

## 3. What runs where, after this decision

| job | home | change |
|---|---|---|
| authoritative state (claims, entities, connections, operators, text, embeddings, turns, narrative) | **Postgres** | none — already the record |
| **vector search** — ANN over embeddings | **Postgres** (`pgvector` + HNSW) | **MOVES** — was `db.idx.vector.queryNodes` (2 executing sites) |
| **text search** — over 7 fields / 5 labels | **FalkorDB** (`db.idx.fulltext` — RediSearch's TF-IDF scorer, documented as BM25-style behaviour in `tortoise/sparse.py`) | **stays** — see §6.2; the move is deferred behind M3 |
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

- `public.point/source/event.embedding` is **`jsonb`** and **stays `jsonb`**, and **`pgvector` appears in 1 of 81
  migrations** (`20261109000001_record_embedding_vector.sql`, `#11642`) — so this WAS a full index build and a
  backfill, not a query edit. ⛔ **UPDATE AT THIS HEAD (`#11642`, measured on pgvector 0.8.7 / Postgres 16.15):**
  the payload column is deliberately NOT converted — a `jsonb → vector(384)` type change would rewrite the
  record's write and read seams and could not represent the caller-supplied embeddings of other widths that
  PR #3018 stores verbatim. The ANN-indexed half is a **GENERATED `embedding_vec vector(384)`** beside it.
  Measured: backfill 20,000 rows **6.9 s**; HNSW build at `maintenance_work_mem` = 64 MB **4.0–4.2 s**
  (index 39 MB); the same build at 4 MB **37.4 s** with pgvector's "no longer fits into maintenance_work_mem"
  warning — it COMPLETES, so the build degrades in time and never into an OOM; top-10 parity against exact
  scan on a clustered 20k corpus **25/25 probes identical**; a cold logical `pg_dump`/`pg_restore` of the
  20k vectors including the index **10.9 s**, against the **436.5 s** untuned / **191.8 s** tuned `vectorset`
  rebuild this replaces (`#7846`, which also deadlocked the instance). **§6.1's shortfall assertion is now
  load-bearing in a second way:** a filtered ANN under pgvector's DEFAULT `hnsw.iterative_scan = off` returns
  **0 rows where 152 match** (measured, `LIMIT 500`); `strict_order` returns 150. `tortoise/record_vector.py`
  sets it explicitly and asserts the shortfall.
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

`ts_rank`/`ts_rank_cd` have **no IDF and no length normalisation**; independent measurement
puts Postgres full-text **8–23 nDCG@10 points behind BM25**, with its hybrid scoring **below
vector-only** on all three datasets.

**What this does and does not claim about our own scorer.** The FalkorDB leg is RediSearch's **TF-IDF**
scorer, whose behaviour `tortoise/sparse.py` documents as *"the BM25-style rank behavior the epic's scope
prescribes"*. ⚠️ **The 8–23-point gap is measured against a BM25 implementation, and how far our hosted
`db.idx.fulltext` leg sits from that baseline is *unmeasured* — that is what M3's score distributions are
for.** So this document does **not** claim our leg *is* the comparator. The repo's own measurement of a lexical
leg (`tortoise/bm25.py`,
`#8488`) found **neither rarity nor length** priced into the scoring, measured on the **embedded
(falkordblite) lane** — not on the hosted `db.idx.fulltext` leg, which is unmeasured here. What the move
decisively changes is the **order** the fusion consumes, not the score scale: `rrf_fusion` is RANK-based
(`RRF(d) = Σ w_i / (k + rank_i)`, `search_engine.py:3021` — and it DISCARDS the leg's own score outright,
`for rank, (pid, _score) in enumerate(ranked)`, `:3091-3093`), so the `{"vector": 1.5}` + implicit 1.0
weights are multipliers on a rank and a rescale leaves them untouched, and on the multi-leg arm a caller's
`threshold` is compared against the **fused** score (`sdk.py:38445`), never against the leg's own.
⚠️ The magnitude does enter ONE arm, and it is stated because a later lane would otherwise be surprised
by it: when a single leg answers or the read is a full scan, a caller's `threshold` IS compared against
that **leg's raw score** (`sdk.py:38401`; bounded `0.0–1.0` at `:37470`, shipped default `0.0` = filter
off) — there a rescale changes which rows clear the floor. Either way a different scorer **re-ranks** the
lexical leg, and that re-ordering — not a lost scale — is what M3's score distributions must measure.
Independently of all of it, `to_tsvector` cannot reproduce the product's tokenizer behaviour
(`ERR-4032` → `err4032` **plus** the adjacency phrase, `#8933`).

**A separate leg the move does not touch:** the in-memory sklearn **TF-IDF** fallback (`tortoise/sparse.py` — what `sparse.py` calls "the embedded
backend", a different leg from the falkordblite lane named above) is outside `db.idx.fulltext` entirely, so "the text index stays" says nothing about it.

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
input rather than a derived value (the `#7955` correction to `STORAGE-ARCHITECTURE.md` **§17.5**, paragraph
“`baseline_source` is NOT unconditionally DERIVED”, which says the carve-out is **not** reopened by it).
That ruling stands and nothing here reverses it.

**Which is why the window's unit of RESIDENCY must be a CLOSED component (§7):** a closed subgraph has no missing
boundary, so recomputing EP for it is **exact**, not approximate. Residency and the EP carve-out
therefore point the same way — and no schema change is needed.

**Why the rule exists.** A claim
with **no persisted belief** used to answer the neutral Beta(1,1) mean 0.5, and the ranker coalesced the
value to it. It was indistinguishable from a computed one at the point where ordering happens. The rule
that settles it — **absent state is `null` with `ep_state: "absent"`, never 0.5** — applies
on every read/rank surface: `annotate_ep_batch`, the canonical `why` `ep` block (`tortoise/why.py`),
`GraphRanker`, `StateRanker`/`recall_ranking`, `GapsRanker`/`gaps_ranking`, `SubgraphExpander`, and the
event/object aggregate reads; the `analyze` templates refuse an absent claim rather than admitting it at a
coalesced 0.5. The vocabulary is declared ONCE (`search_engine.EP_STATE_*` +
`ep_has_belief_cypher`/`ep_state_cypher`/`ep_state`/`ep_state_resolve`), and
`tests/test_11702_absent_belief_state.py` pins the property across every one of those surfaces, in both
directions (a *measured* 0.5 keeps its 0.5).

**How the rankers honour it, stated exactly** — because "null" alone under-specifies an ORDER:

- **absent rows sort AFTER every row that has a belief** (`recall_ranking.belief_informed_order` is
  `false` on them). A multiplicative gate cannot express "absent" as a factor — `conf**exp` ranges over
  `(0, 1]`, so the multiplicative identity a missing factor would use is that axis's **maximum**, which
  would rank "nothing measured this" above a claim measured at 0.9. The refusal is therefore a sort
  TIER, not a fabricated factor.
- **`GraphRanker`'s absent row drops the belief TERM and keeps the remaining term's WEIGHT**
  (`0.6·inst_norm` / `0.5·connectivity`), never the unweighted term — which would let absence (0.75)
  outscore a measured 0.5 (0.65), the same defect pointed the other way.
- **`min_confidence`**: an absent claim fails any floor above `0.0` and is kept by the `0.0` default.
  `navigation.py`'s `confidenceMin` and the notification gate (`volunteer._eligible`) follow the same
  rule — the gate is stricter, requiring a belief at **every** floor: you do not interrupt about a
  claim you have no belief about. **`confidenceMin` applies to EVERY family the profile lines up,
  not only to Points**: an Object / Event / Subject neighbour (or a Source with no `documentKind`)
  has no persisted belief either, so it is excluded by any floor above `0.0` — where the pre-#11702
  `node.get("confidence", 0.5)` admitted it at any floor ≤ 0.5. That is this rule applied
  *uniformly*, not a divergence from it: restoring the `0.5` "because it is not a Point" would be the
  same fabrication one family over. Pinned (non-Point arms, including a non-Point that DOES carry a
  `confidence` mirror and must still clear the floor) in
  `tests/test_11702_absent_belief_state.py::test_entity_profile_floor_reads_a_posterior_only_belief`.
- **PRESENCE is a property of the value's operands, not of a column existing.** A belief exists when the
  mirror (`n.confidence`) is set, **or** both coalesced operands are set and `α + β > 0`. So a stored
  `(0, 0)` pair (Beta(0, 0) is not a distribution) and a HALF pair (`posterior_beta` with no α, whose
  mean the `coalesce(…, 1.0)` default used to invent as 1/4) are both **absent**. Without this rule one
  claim got four answers — `0.5` in the `ep` block, `None` in `recall_ranking`, `absent` in
  `gaps_ranking`, and **`NaN`** from `graph_ranking`'s unguarded `α/(α+β)` division, serialised onto the
  wire as a bare `NaN` token.
- **The one stated exception**, recorded on #11702
  ([decision comment](https://github.com/daniel-ospina/tortoise/issues/11702#issuecomment-6781252637)):
  `sdk.get_confidence` / `TortoiseEP.compute_confidence` still answer the Beta(1,1) prior mean under an
  explicit `require_calibration=False` — that is the EP read, not a read/rank surface. Consequence, so
  nobody "fixes" it later: `get_confidence` and `search` disagree for an ABSENT claim by design, and
  #2206's agreement indicator holds only for the population that has a belief.

---

## 7. The window — what the graph holds once it must be bounded

**Why the window exists at all:** a *complete* graph costs **\$11.01/tenant** against a **\$5.70** budget
(30 % of \$19) — **193 %, and its RAM line alone is \$8.75 (154 %)** (§8.1; the RAM line is NOT the tenant's
cost, which §8.1 says explicitly). Density does not fix it — `$58.33` is already the DENSEST tier's per-GB rate, and density cannot
shrink a tenant's own graph bytes; it only spreads the *fixed* lines. **The window is what makes the pricing work at scale.**

⚠️ **Where the \$5.70 comes from, because the document it amends records a different ceiling.** `STORAGE-ARCHITECTURE.md` §1
records the product constraint as *"a \$19 price with < \$9 total cost per user"*, and its M2 row prices against a
"\$9 → \$19 budget"; the **30 %-of-price** target here is the owner's 2026-11-07 sizing rule for the
**storage AND shared-project lines together** (the owner's words cover falkor and postgres in one budget, §8),
and it is **strictly tighter** than that ceiling (\$5.70 < \$9). Both are quoted so neither is mistaken for the other:
against the older **\$9 total-cost ceiling** a complete graph's **RAM line** is **~97 % of it** and its **total is ~122 %** (above it), while against the **\$5.70 storage target** the RAM line is **154 %** and the total **193 %** — and
the target is what governs the window. **The rule's recorded home is §8**, which quotes the owner's own words
(`#4051` comment [`6712622492`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6712622492))
and derives every threshold from them — so the target is no longer an unhomed figure. §2.0's marker states the
same two figures (the §8.1 total of \$11.01 against the \$5.70 target) rather than citing this section; the
derivation itself is §8 below. ⚠️ The owner's words bound **solo** (30 % of \$19 = \$5.70); carrying the rule to pro and team
is this document's generalisation, recorded in the same thread (comment
[`6712718679`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6712718679)), and the
thresholds for those two tiers are therefore a derivation, not a quotation.

**⚠️ Why it is nonetheless NOT armed at private-beta (§1.5):** it is unmeasured. The bounded-cache
literature measures latency and hit rate, not answer quality, and the best trace-aware policy reached ~54 %
hit rate for 2-hop expansions — so arming it on a guess is exactly the quality risk this document ranks
first. Private-beta therefore runs **complete** and **above the 30 % target, knowingly** — **193 % of it on the
total per-tenant basis, §8.1** (the RAM line alone is 154 %, and it is also above the older <\$9 total-cost
ceiling) — bounded by a small known tenant count, and the window is armed when M2 says it is safe. **The guardrails that make it
safe ship before private-beta regardless**, so the eventual switch is a configuration change with measured
justification, not a redesign in front of customers.

⭐ **What the §12.1c decision buys that the window cannot buy itself: a per-tenant memory cap becomes
expressible.** A cap needs eviction, and eviction needs a demoted subgraph to come back cheaply — the
`DUMP` → `DEL` → `RESTORE` cycle. While the HNSW index lived *in the graph*, that cycle was
**instance-blocking**: the restore **rebuilds the index on the serving thread**, so the whole engine stops
answering for the rebuild's duration, with no progress line and no error — 30 000 nodes, index-free
`RESTORE` **0.17 s** (worst bystander `PING` **53.3 ms**) against index-bearing **2.62 s** (bystander
**2 497.7 ms**, i.e. the outage IS the restore), and at 90 000 nodes it outlived a 10 s client read bound
(`docs/evidence/7846-indexed-graph-restore/README.md` §3, §3a, §4a; `#7846`). ⚠️ **Read that record, not the
`#7846` issue body** — the body's "permanent wedge" does **not** reproduce (its first harness's graph carried
no populated index, so it measured nothing), the index **is** carried inside the graph key's serialized
payload (a restored shard is not vector-blind), and the block is avoidable by dropping the index **before**
the `DUMP` (**0.069 s** / **69.4 ms** bystander at 20 000 nodes, against **2.955 s** / **2 899.4 ms** raw).
With the index and its payload in Postgres (§12.1c), a graph dump has **no vector index to rebuild at all**,
so that whole class of block is gone rather than mitigated — ⚠️ measured for the VECTOR index; whether a
fulltext or range index blocks a restore the same way is not measured in that record. **So §7's window is a
scheduling question, not a feasibility one — *for the vector index***, which is the only index the decision
removes. ⚠️ **The window KEEPS the fulltext index in the graph** (decision item 2, §6.2), so the index that
stays is exactly the one that record did not measure: if a kept `db.idx.fulltext` — or a range — index blocks
a restore the way the vector index did, then the window is a feasibility question again, and that leg is
**M4's** to settle rather than this document's to assume. The residual cost is exactly what §1.5 item 3
already reserves: re-materialising a window still peaks at **4.9–5.9×** its resting size (the policy table below),
and that peak is a graph-memory peak, not an index one.

**The unit of RESIDENCY is a CLOSED component, not a node.** A pinned hub whose neighbours were evicted
pays for the node and still cannot complete the walk. Pinning closed neighbourhoods (1–2 hops) also
makes EP recomputation exact (§6.3).

⚠️ **This window is RESIDENCY, not retention, and the distinction is load-bearing.** Demoting a subgraph
removes it from the *cache*; it stays in the record. Nothing a customer keeps is deleted, no copy is
dropped, and `#3853` — *"No default window, of any length. … there is no default retention period on user
content"* (`docs/retention-and-deletion.md` §"A different axis: the product-usage record (#3843)", its
*"No default window, of any length"* bullet; the general rule it rests on is §"The promise": *"R1 forbids
retaining user **content** for any length"*) — is untouched: §4
records that `FalkorDB` is a derived cache, never authoritative, so §11 excludes retention because there
is **none here**. Read §7 as sizing what is *resident*, never as a TTL on what is *kept* — a per-node TTL
is the bound `docs/architecture/CONSUMPTION-MODEL.md` forbids as *"any bound that shrinks what a customer
may keep, however it is denominated"*, and it is not this.

**The policy, as researched:**

| rule | why |
|---|---|
| **retain closed components / 2-hop closures** | closure is what makes a walk and an EP recompute correct; measured cache studies show 1-/2-hop closure prefetch raises hit ratio for **every** policy tested |
| **cap the pin: count, bytes, AND a degree ceiling** | unconditional hub pinning is a *measured* anti-pattern ("they can thrash each other"; naive pinning scored 62.4 % miss vs LRU at 60.2 %). Degree is **U-shaped** — helpful mid-tail, harmful at the head; a single huge hub can eat a 77 MB window |
| **window-local touch counters + admission control** | recency beats static degree ranking, and adaptive policies beat every static one measured; eviction must be budget-triggered, not per-request, or the cycle "demote a leaf → the next query needs it → re-materialise → evict another leaf" becomes a storm. ⚠️ **The counter is WINDOW-LOCAL cache state — a different artifact from `tortoise/read_ledger.py`.** That ledger is TENANT-WIDE and is explicitly not a product input: `STORAGE-ARCHITECTURE.md` §17.4.6 states that reading it for any product decision *"would be a violation"* (it exists to measure `f`). Wiring it as the eviction signal is exactly the violation it names. |
| **reserve ≈6× the window in provisioned RAM for rebuild peaks** | **third-party measurement — citation owed (§1)**: re-materialising into FalkorDB peaks at **4.9–5.9× the resting size** — a 77 MB window can transiently demand ~377–454 MB, and the engine has **no per-graph memory cap** (**vendor-internal claim — citation owed (§1)**). This is the #1 production risk. Do not shard the window into many small graphs (`NODE_CREATION_BUFFER` costs 16 384 slots each **— vendor-internal, citation owed (§1)**) |
| **a walk materialises its seeds first** | search returns ids from Postgres; a non-resident seed must be written into the graph before traversing — a write on the query path, which is why the headroom above is mandatory |
| **label what was truncated** | a confidence within 2 hops of a truncated boundary is **refused**; beyond that it may be returned as degraded. ⚠️ **The two reason strings this rule needs are OWED, not shipped, and each names its own outcome:** the degraded RETURN carries `degraded: "truncated_boundary"`, and the REFUSAL carries `degraded: "insufficient_graph"` (the resident graph cannot support the claim at all). Neither string appears anywhere in the tree at this head, and `STORAGE-ARCHITECTURE.md` §17.3 records `degraded` as one of four terms whose ONE home, `tortoise/status_vocabulary.py`, forbids a fifth, so the *reason* has to attach to `degraded` rather than mint a term. **Required before private-beta (§1.5).** What DOES exist is the marker's on/off mechanism (`residency.py`, `not-hot`, `TORTOISE_RESIDENCY`) and it is currently OFF |

⚠️ **The honesty item:** the bounded-cache literature measures **latency and hit rate, not answer
quality**, and the best trace-aware policy reached ~54 % hit rate for 2-hop expansions. **Whether a
windowed graph harms retrieval quality is unmeasured anywhere** — which is why §9 measures it before the
window is trusted, and why **private-beta** runs the graph **complete** (§1.5).

---

## 8. Cost — per tier, derived from the 30 % rule

Rates per §17.4.1 (vendor pages, **verified 2026-10-10 — re-check before a published tier; a rate move moves
every number here proportionally**): FalkorDB **\$43.75/GB-month provisioned** at the 8 GB tier, and a graph
may occupy **75 %** of it ⇒ **\$58.33 per GB of graph data**; Postgres disk **\$0.125/GB**; plan `$25` +
compute `$60` per project.

**The rule, in the owner's own words** (`#4051` comment
[`6712622492`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6712622492), 2026-11-07):

> *"i will put the price tier of the solo to \$19 and 30% of that is our per-month cost budget. So we need to
> include falkor and postgres in that. there's overage after they exceed a threhsold, but for now we want to
> set the trheshold fo psotgres and falkor so cobined they don't use up more than 0.3\*19 per month"*

So every threshold below is **derived, not chosen**, and the derivation is one line — because only one of the
two stores is expensive. ⚠️ **Read the scope honestly:** the owner's ruling names **solo** and gives one number,
\$5.70 = 30 % of \$19. Applying `0.30 × price` to **pro and team** is **this document's generalisation**, recorded
in the same thread (comment
[`6712718679`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6712718679)) rather than
spoken by the owner — ⚠️ and read that comment for what it does, not for what it claims: it *applies* the rule
(`30 % of that tier's price`) to pro and team while calling the general form *“the owner's”*. The **derivation of
the pro and team rows is this document's**, and a reader who needs authority for those two numbers has it only as
the rule's form carried to another price, not as a ruling on pro or team.

For a tier priced `P`, with a record allowance of 1 GB, the threshold must be solved *together with density*,
since the shared project line is `$85 ÷ tenants`:

```text
provisioned MB per tenant  p = (0.30·P − $0.125) ÷ ($43.75/1000 + $85/8000)
graph window               = 0.75 · p                       # a graph may occupy 75 % of provisioned RAM
tenants per 8 GB instance   = 8000 ÷ p                      # this also sets the shared project line
```

| tier | price | 30 % budget | provisioned / tenant | **window target** | tenants per 8 GB instance | RAM \$ | shared \$ | record \$ | **total** | **% of price** |
|---|---|---|---|---|---|---|---|---|---|---|
| **solo** | \$19 | \$5.70 | 103 MB | **≈77 MB** | 78 | \$4.49 | \$1.09 | \$0.13 | **\$5.71** | **30.0 %** |
| **pro** | \$25 | \$7.50 | 136 MB | ≈102 MB | 59 | \$5.93 | \$1.44 | \$0.13 | **\$7.50** | **30.0 %** |
| **team** | \$149 | \$44.70 | 820 MB | ≈615 MB | 9.76 | \$35.87 | \$8.71 | \$0.13 | **\$44.71** | **30.0 %** |
| free / anon | \$0 | **\$0** | — | **0** | — | — | — | — | — | funded from paid margin, or no cache |

⚠️ **The basis, because the two earlier figures in this section do not count the same components.** Every
cell above is a **TOTAL per tenant** — RAM **plus** the shared project line **plus** the record — because a
per-byte cost compared against the whole budget is an error this document has already had to fix. The
**window target** column is a **budget-derived target, not a measurement**: no windowed graph exists, and the
window is armed only after M2 (§1.5, §7). And **the 150 MB graph figure those targets scale from is an
ESTIMATE** from `GRAPH.MEMORY USAGE` — a sampling instrument — so it is a dated estimate, not a bound. ⚠️ **Its
known omissions bias ONE way:** `STORAGE-ARCHITECTURE.md` §2.1 records that it is blind to the HNSW `vectorset`
(measured 8.5×–21.7× under-report on the index share, ~2× on the total) and excludes per-graph/Redis-key
overhead, so the real graph is larger than measured; only the SAMPLING error itself runs both ways.

**The record allowance is not a trade — it is a rounding choice.** The record is **467× cheaper per byte**
(\$58.33 ÷ \$0.125 = 466.7), so **1 GB costs \$0.125 — printed as \$0.13 — i.e. 2.2 % of solo's \$5.70 budget — and displaces only ≈2 MB of
window.** Set it by what the product wants to promise and state it beside the window.

### 8.1 The two states, on the same basis

| | **complete graph (private-beta, §1.5)** | **windowed (post-measurement, §1.5)** |
|---|---|---|
| graph per solo tenant | ~150 MB — **an estimate**, see the basis note above | ~77 MB — the **target** the formula above yields |
| provisioned per tenant | 200 MB | 103 MB |
| tenants per 8 GB instance | 40 | 78 |
| RAM + shared + record | \$8.75 + \$2.13 + \$0.13 = **\$11.01** | \$4.49 + \$1.09 + \$0.13 = **\$5.71** |
| vs the \$5.70 budget | **193 %** — above, and accepted knowingly | **100 %** — the budget, to the cent after rounding (the unrounded model is \$5.70; the printed cells round up by \$0.01) |
| the same, RAM line alone | \$8.75 = **154 %** of the budget | \$4.49 = **79 %** of it |

⛔ **The last row is the one that misleads, and it is here deliberately.** The **RAM line alone** already
exceeds the whole budget for a complete graph (154 %), and the earlier text quoted that as if it were the
tenant's cost; the honest figure is the **total, 193 %**. Both are printed so the weaker claim cannot be
mistaken for the stronger one.

⚠️ **And the Postgres side is not free of unknowns, only of cost:** under both states the dominant
**unmeasured** lines are compute tier, provisioned IOPS and egress, driven by the **miss rate** — which
nobody has measured (§17.4.6). Everything above is the **storage and shared-project** lines and says so —
the `shared $` column is `plan $25 + compute $60` per project divided by tenants, where $60 is an **assumed**
compute line and the **tier choice itself is unmeasured**: a higher compute tier raises that column and the
total with it.

⚠️ **These are derived thresholds, not published prices.** `product/pricing.json` is the price artifact of
record and currently carries **solo = \$9**, so nothing here is live until that changes.

---

## 9. Measurement plan

⛔ **This document's M1–M5 are ITS OWN set, and are not `STORAGE-ARCHITECTURE.md` §15's M1–M9:** there M2 is
the invoice, M3 the junk share, M4 self-hosting and M5 the (now answered) location question. A bare `M4` in §1.5
means *rebuild peak* here and *self-hosting* there, so resolve every M-reference against the document that
prints it.

| # | measurement | decides |
|---|---|---|
| **M1** | vector move: recall@10 vs exact ground truth, p95, **and the shortfall rate at 1 % / 10 % / 50 % selectivity** | whether the move ships — a recall-only test is not sufficient (§6.1) |
| **M2** | **retrieval quality under a windowed vs complete graph**, on a fixed query set | the gate that ARMS §7 (post-measurement) — the literature has no answer |
| **M3** | text ranking: a BM25-capable Postgres option vs the current `db.idx.fulltext` scores, **score distributions** + the `#8933` identifier fixture | whether the text index ever moves (§6.2) |
| **M4** | rebuild peak memory and latency per tenant at the real window size, **and whether a KEPT `db.idx.fulltext` or range index blocks a restore the way the removed vector index did (§7)** | the 6× headroom (§7), the #1 risk — and whether §7's window is a feasibility question after all |
| **M5** | the **miss rate** per tenant (a DIFFERENT M5 from `STORAGE-ARCHITECTURE.md` §15's, which asks where the vector index lives and was answered 2026-11-07) | the compute/IOPS/egress tier |

---

## 10. What would reopen this decision

The review record (four passes; verdicts and corrections) lives on `#4051`; the corrections that *changed the
decision* are stated where they bite — §1 (no ratio of our own graph is an input), §6.2 (text does not move) and
§6.3 (EP state is not stored).

**What would reopen this decision:** M2 showing a windowed graph loses retrieval quality beyond an
accepted bound; or M1 failing the filtered-recall bar; or a measured vendor/cost change that makes a
complete graph affordable.

---

## 11. Out of scope

Tier prices and overage values; the compute/IOPS/egress tier; retention (`#3853` — §7's window is
*residency*, not retention: nothing is deleted); the session cap
(`#4010`); the `record_only` tier's scope beyond `:Point` (`#10017`); **the implementation order** —
deliberately last, and **this document's** sequencing choice, not a quoted ruling: canonical docs first, so a
lane never implements against a superseded architecture. (Its shape matches the phase correction in `#4051`
comment [`6714119028`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6714119028).)
