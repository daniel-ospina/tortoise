# Search epics inventory — validated against the top agent-memory systems (#12186)

**Lane:** `research/12186-search-inventory` · **Type:** research only, no implementation
**Question:** does the epic's inventory (A1–A6, B1–B2, C1–C3, D1–D4) match what the best-performing
agent-memory systems actually do, and what does the epic not see?

> **This file is round 1 (§0–§6).** Round 2 appends **§7** (PR `#12270`). Round 3 lives in its own file,
> **`round-3-verification.md`**, because it *re-tests* rounds 1–2 rather than extending them: it re-measures every
> falsification against the tree at `f33f5e2c3215`, and it found a defect no inventory contains —
> **D-1: every *identity* reader honours a completed merge and no *retrieval* reader knows it happened, so the
> retired carrier stays in the search results alongside its survivor.** Round 3 does **not** reverse round 2's
> conclusion that a `resolved_from` redirect is a merge under another label; it strengthens it (the redirect gives
> up the link band's semantics *and* fails to deduplicate retrieval).
> **Read round 3 alongside this file, not instead of it.**

---

## 0. Method, and what failed

**Internal pass first.** `#12186`, `#11901` (incl. both measurement comments + the correction), `#11395`
comment `6781921075` (the KEEP ruling), `#2518`, `#2513`, `#4211`, `#2730` (CLOSED), `#12184`,
`docs/research/2026-09-24-2730-entity-attachment/research-brief.md` (§3–§6),
`docs/architecture/SEARCH-ARCHITECTURE.md` §6.2/§7/§9, `docs/architecture/STORAGE-ARCHITECTURE.md`
§12.2c/§14.3 V3/§17.4.6, `docs/benchmarks/comparison-systems.md`, and the code itself.

**Local Tortoise graph: NOT consulted.** The Mac's `docker://:@localhost:16379/tortoise` is not reachable
from this cell, and no live tenant serves the populations these questions need. Every number below is
either this repo's committed artifact or an external primary source — none is a graph read by this lane.

**Research rung, and the two failures (declared, not skipped):**

| rung | tool | outcome |
|---|---|---|
| 1 | `mcp__seo-intelligence__perplexity_search` | **UNAVAILABLE** — `mcp_catalog` lists no `seo-intelligence` server; `mcp_load` → `Unknown MCP server 'seo-intelligence'` |
| 2 | `web_search` (`model="sonar"`, named explicitly) | **UNAVAILABLE to the authoring pass** — `PERPLEXITY_API_KEY not set. Set it via PERPLEXITY_API_KEY env var, AGENT_MCP_ENV_PATH (path to .env file), or $AGENT_INFRA_PATH/../.env`; neither `AGENT_MCP_ENV_PATH` nor `$AGENT_INFRA_PATH/../.env` existed (`AGENT_INFRA_PATH=/home/cmux/agent-infra`). ⛔ **RE-RUN AT THIS HEAD: AVAILABLE** — the key resolves in a current session and the rung returns results. **§5.1 is the research that unblocked, and it retires the "largest hole" this document previously declared** |
| 3 | `web_fetch` (primary sources) | **USED** — the brief's own preferred path. Every external claim below is a fetched primary source unless labelled otherwise |

**Consequence at authoring time, stated honestly:** with no search engine, source *discovery* was limited to
URLs reachable by construction (arXiv abs pages, GitHub raw files, known vendor blog paths), and guessing at
a vendor's path is not discovery. Both mastra.ai paths this pass guessed returned **HTTP 404**
(`/blog/how-observational-memory-reached-sota-on-beam`, `/blog/memory-benchmark`); the posts in fact live at
`/research/observational-memory` and `/blog/observational-memory`, which the authoring pass could not reach
without a search rung. **§5.1 re-runs rung 2, reads those primary sources, and closes the gap.** §5 is kept
as the record of what the authoring pass could not establish — **not as a limit on the reader.**

---

## 1. The finding that changes the epic: levers the epic lists are already built, and the one it omits is the one that matters

Verified against the tree at `1f28d107cf`, not against the epic's status column.

| # | epic says | code says | evidence |
|---|---|---|---|
| **C2** | `BLOCKED-ON-MEASUREMENT`, scoped to *"measure a BM25-capable Postgres option (extensions) against the current `db.idx.fulltext` scores"* — ⚠️ **that scope IS the index move, so the STATUS is correct; what misleads is the row's TITLE, "BM25 instead of TF-IDF"** | **BUILT AND ON BY DEFAULT** (embedded lane). A full Okapi BM25 (`idf` + `tf` saturation + length normalisation) exists and is **armed by default** since the owner's 2026-10-28 ruling | `tortoise/bm25.py:166` `BM25_DEFAULT_ENABLED = True`; `bm25.py:152` records the ruling and its ordering precondition (#10121 tie-fix); wired `tortoise/sdk.py:39588-39600`; leg `tortoise/search_engine.py:3198` `run_bm25_query` |
| **A4** | *"partly BUILT — `#2518` has the switch with no producer"* | **the producer exists** — sparse half **and** dense half, default OFF behind the `#2513` sealed A/B gate | `tortoise/sdk.py:41408` `_entity_key_expansion_pass`, `:41466` `_entity_key_dense_pass`, harvest shared; call site `:40034-40072` |
| **C1** | `BUILT-OFF` — *"not wired into the noun/search path"* | **BUILT and wired into the ASK lane**, default OFF; correctly absent from the noun path | `tortoise/ask_lane.py:630` `ask_lane_rerank`; `tortoise/rerank.py:77` `rerank_enabled` (fail-safe OFF, `TORTOISE_ASK_RERANK`); degrade-to-unreranked at `:484` (gate off) and `:497-498` (score exception — degrade, never raise) |

**The bound on C2 that must travel with it, so this is not oversold:** the BM25 arm runs **only on the
embedded lane** (`sdk.py:39588` `is_embedded`), and it **stands down** whenever a later lexical pass will
rewrite the same slot (`_prf_supersedes_the_arm`, `:39585-39606`). On the **hosted** product lane the lexical
leg is still the graph's fulltext scorer. So the epic's premise is right about the product and wrong about the
mechanism we already own: *"we are two steps from where the field is"* — the epic's own words — is **one** step on the embedded lane and the
M3 question is narrower than "do we have BM25" — we do; the open question is whether the **text index** moves
to Postgres. That is exactly what `SEARCH-ARCHITECTURE.md` §6.2 already says (`:268`), so M3 needs no
re-scoping — **but the C2 row's TITLE, "BM25 instead of TF-IDF", reads as a gap to fill and would send a lane to
build a second BM25. The row's STATUS is correct; do not "fix" it.**

**⚠️ These three are not the whole class — §2 adds two more of the same kind.** **A1** (fold the stored side) is **BUILT since `#12047` for four of its five named components** — case, articles, `-`↔space and unicode all fold — **but `path basename` does NOT**, and that is the component `#11901` measures at **0.0 % attach** on 30 path pairs (see A1's row). The fold rides `canonical_name` at the funnel every entity door meets, and `#12047`'s review N8/P0 (`docs_miner.py:543-550`) forbids a **second** stored key — so the fix belongs in the comparison, never in a new key. **A2** (alias / multi-key blocking) is **PARTLY BUILT** — an `aliases[]` store, an alias rung and a character-prefix `block_key` all exist — ⚠️ **but neither the alias rung nor `block_key` runs on the CAPTURE path** (the alias rung is reachable only through `resolve_entity_name`, which no production path calls; `block_key` only inside `entity_candidates`). Read the
§2 A1/A2 rows **before** dispatching on either. And note the one row above where the epic is already right:
**C1** the epic marks **ALREADY BUILT** with *"do not re-implement"* — that status is correct, and the
correction is only that the wiring is ask-path-only, not that it exists.

### 1.1 The lever the inventory omits entirely: the referentiality gate

`tortoise/referentiality.py` (#11021) exists **specifically to fix the class this epic's A3 and B1 circle
around** — the 62.3 % bare-definite-description population (`referentiality.py:11-16`, citing the measured
4,900 of 7,859). It is the cue stage that removes pronouns, deictics and discourse-generic heads *before*
they mint an `:Object`, and it hands the rest to the `#5087` S2 decision seam untouched.

**It is default OFF, and that is a recorded decision** (`referentiality.py:65-70`): `#5087` plan R1 says
*"measure on the 25 labelled rows plus the 12 most-referenced rows … before arming; fail-open to entity until
measured"*, and **that measurement does not exist** (`#10996` owns the 25-row precision number, the 12-row arm
is the issue's own). Its entry point is `route_mentions` (`extractor_v2.py:12081`), which removes held
surfaces from `entities[]` by name and strips them from the claim/event slots.

**Why this is the epic's most expensive omission:** it is **built**, its blocker is **37 rows of labelling**
(not a quarter of engineering, not a model, not an index), and it addresses the same population the epic
plans to attack from three expensive angles (A1 fold, A3 context embedding, B1 verdict). A lane reading the
inventory in its current shape would re-derive it.

---

## 2. What the top performers do — the inventory, validated row by row

Status legend (verified, not inherited): **NEW** · **BUILT-OFF** · **BUILT-MISWIRED** ·
**BLOCKED-ON-MEASUREMENT** · **BUILT-ON**.

### A. Candidate generation

| # | what the top performers do (named + URL) | gain they report | contradicts a decision? | cost | our status (verified) |
|---|---|---|---|---|---|
| **A1** fold the stored side | **Zep/Graphiti does not fold the stored name.** It builds a **second key at index time** — `_normalize_name_for_fuzzy` strips everything outside `[a-z0-9' ]` (`dedup_helpers.py:46-51`) — and indexes that. The stored `name` is never mutated. (`github.com/getzep/graphiti`, `graphiti_core/utils/maintenance/dedup_helpers.py`) | no isolated gain reported for the fold alone; it is a **blocking key**, not a scored lever | **no recorded decision** — but the *shape* the epic proposes (fold the stored side) is contradicted by the convergent implementation shape (add a key, never rewrite) | ≈0 (pure function, one extra key per node) | **BUILT since `#12047` — and the spec must NOT add a key.** The stored fold already exists: `normalize_entity_name` (NFKC → casefold → drop leading article → non-word runs → whitespace) rides `canonical_name` (`entity_resolver.py:136-176`), filled at the funnel **every entity door meets** (`sdk.py:44396-44410`, `setdefault`), and indexed for equality (`sdk.py:5211-5243`); `aliases[]` alongside it is written by approved merges (`entity_resolver.py:736`, write at `:792`). ⛔ **`#12047`'s review N8/P0 forbids a second key space in one property** — `docs_miner.py:543-550`: *"Passing nothing leaves the fold to the ONE writer, which is the invariant ('one fold, one key')"*. So what is genuinely absent is Zep's **fuzzy** (punctuation-stripped) key, and it must be added by **REUSING `normalize_entity_name` / `block_key`**, never by minting a third stored key beside `canonical_name` and `aliases[]`. ⚠️ **A `fuzzy` rung also already exists** — `difflib.SequenceMatcher` ratio ≥ `REVIEW 0.60` → **review queue** (`entity_resolver.py:54-58`) — so the absent thing is not fuzzy matching but Zep's **shingle-Jaccard with an entropy gate** form of it. ⛔ **And one of A1's five named components is genuinely absent: `path basename`.** `normalize_entity_name('tests/test_x.py')` → `tests test x py` while `test_x.py` → `test x py`, so a path and its basename never compare equal — and `#11901` measures that class at **0.0 % attach** on **30 path pairs** (`comment 6825791142`). **No child in §6 scopes it** (N-2 is only the fuzzy comparison key), so it would leave the inventory silently if A1 is read as wholly built. The remaining real gap is narrower than "we lack the fold": capture matching reads the **raw `name`** in `extractor_v2.resolve_entities`, which is why `#11901` measured 0.0 % attach on 419 variants (`#11901` comment `6826507139`, "Next, in order" item 2: *"Separator fold in the identity comparison — never in `_norm`"*). |
| **A2** alias / multi-key blocking, measure **pair completeness** | **Zep unions THREE candidate sources — identity-property key, exact-normalised-name key, and semantic search — while the MinHash/LSH shingle arm is a SEPARATE, EARLIER, DETERMINISTIC stage, not a fourth key in that union**: `_collect_candidate_nodes` merges exactly those three (`node_operations.py:463`, `:468`, `:469`, unioned at `:480`); `_build_candidate_indexes` (`dedup_helpers.py:293-318`) builds `lsh_buckets`, and `_resolve_with_similarity` (`:321-380`) settles a match deterministically at `_FUZZY_JACCARD_THRESHOLD = 0.9` (`:34`) **before any LLM sees it**. The ORDERING is where the precision lives: the cheap rungs resolve what they can, and the adjudicator only ever sees the residual. ER literature agrees blocking is *the* bounding mechanism and pair completeness is the metric (Papadakis et al., arXiv 1905.06167, in-repo §X1) | not reported as a score delta anywhere; it is an **architecture**, and GraphRAG has **no** variant resolution at all (in-repo §X8) | **no recorded decision** | cheap and deterministic; the LSH index is the only new structure | **PARTLY BUILT — and "confirmed twice" was a FALSE NEGATIVE.** ⚠️ Searching for a `hasAlias` **edge predicate** cannot see a **property-based** alias store, and one exists: the alias rung's query is `n.canonical_name = $k OR (typeof(n.aliases) = 'List' AND $k IN n.aliases)` (`entity_resolver.py:610-611`, inside `find_live_holder` `:580`), with `aliases[]` written by approved merges (`:736`/`:792`). ⛔ **But that rung has NO production caller:** `find_live_holder`'s only call site is `:650` inside `resolve_entity_name` (`:632`), which nothing outside this module calls — and the capture matcher reads neither `canonical_name` nor `aliases[]`. **So on the CAPTURE path aliasing is inert too**, not merely un-indexed. A character-prefix blocker also already exists — `block_key`, the first `MIN_BLOCK_CHARS` characters (`:185-187`). So what is actually missing is **name-shingle LSH blocking, measured for pair completeness** — not aliasing or blocking as such. What genuinely has no hits is an LSH/shingle index (`rg -i -e jaccard -e minhash -e lsh tortoise/` → 5 hits, all prose or MMR's Jaccard token-overlap fallback at `rerank.py:333` — none is an LSH index). |
| **A3** embed name + context (vector in Postgres) | Every comparable embeds **name + description**: Neo4j GraphRAG (name+description), GraphRAG (entity description), LightRAG `entities_vdb`, Zep `name_embedding` (**the name alone**, 1024-d). In-repo §12.2c table, HIGH confidence, 6+ systems | none isolates the entity vector's contribution to answer quality — **no ablation exists** (in-repo §12.2c: *"no measured ablation isolates an entity vector's contribution"*) | **§12.2c sequences the entity vector behind the extractor** — ⚠️ **for BOTH jobs, not just the description one** (`:1319` "BOTH jobs a vector could do"; `:1303` names them: entry key + resolution key), because the fix is the extractor making *"names stop being near-synonymous"* (`:1327`) and `:1330` rules it *"DOWNSTREAM of the extractor work, not parallel to it"*; §14.3 V3's *carry* half is KEEP (owner, `#11395` `6781921075`, which restates the sequencing as *"fix the names, then the vector earns its keep"*) — so this is **not** a reopen, it is the ruled work **and it stays sequenced** | **584×** rate ratio, graph RAM vs record disk for the same bytes (§17.4.4 `:1894`); measured record disk **$0.0240**/tenant/month at 0.1916 GB (§17.4.6 `:2043`) | **NEW, and BLOCKED on a field that does not exist**: `Object.description` → **0 rows**, `Object.summary` → **0 rows** (measured 2026-09-24, §12.2c). "Name + context" has **no context to embed** until the extractor supplies one. **Zep's precedent is the SHAPE to apply to the ruled work — not an escape from the sequencing** (§12.2c sequences the entity vector behind the extractor for **both** jobs, §4's A3 row): it embeds the name alone. |
| **A4** widen the window + pass the levers we own | **Zep: 15 candidates at cosine ≥ 0.6, then a verdict** (`NODE_DEDUP_CANDIDATE_LIMIT = 15`, `NODE_DEDUP_COSINE_MIN_SCORE = 0.6`, `node_operations.py:67-68`) — a **candidate-list length downstream of a similarity floor**, never a raw top-k | no isolated number; the LongMemEval gain (+18.5 %) is reported for the whole architecture (arXiv:2501.13956) | **no decision reached** — and `#11901` **refutes the lever**: widening 3→15 recovers 35 of 116 misses (30.2 %) while the matcher binds **0.0 %** of the 419-variant class at **every** width, and the effective candidate list is already **mean 40.4** (not 3) | 0 to widen; a wider window **without** a verdict is the recorded X7 regression | **BUILT-OFF, not "switch with no producer"** — `_entity_key_expansion_pass`/`_entity_key_dense_pass` `sdk.py:41408`/`:41466`. The window itself: `#11901` measured it and **took no decision** — its DoD re-tick reads *"not decided, and deliberately not changed"* (`comment 6826507139`); see §4. |
| **A5** GraphRanker per-label dispatch | no comparable system has this defect class to copy — it is **ours alone**. Zep dispatches by entity type through typed Pydantic models, which is why the shape cannot occur there | n/a | no decision | a small refactor + a test | **BUILT-MISWIRED — verified at `tortoise/ranking.py:926-928`** (inside `GraphRanker._fetch_signals`, declared `:914`): two branches, everything that is not `event` silently receives point signals. `StateRanker._fetch_object_signals` is at **`:1773`** (`StateRanker` declared `:1206`) and is unreachable from `GraphRanker`. `#12184` is substantively accurate; **its locators have drifted** — see §6.1. |
| **A6** session-hierarchy candidate expansion | HippoRAG (PPR), TiMem (ancestor propagation), Gar/TopoRAG — the in-repo census (`#4211`) finds four independent passes converging on **deterministic graph expansion fused in rank space** | in-repo `#4211`: a one-hop `CONTAINS` walk reaches **8/8** gold turns across all 5 window-miss questions | no decision | 2.2–12.1 ms measured, 0 LLM calls (`#4211`) | **NEW, and correctly specified already** — `#4211` carries a census, a design and four falsifiers. **Do not duplicate.** |

### B. Decision layer

| # | what the top performers do | gain they report | contradicts? | cost | our status |
|---|---|---|---|---|---|
| **B1** verdict over supplied candidates, three bands + hard vetoes | **Zep: the union of THREE candidate sources goes to an LLM dedupe prompt with structured output** (the LSH/fuzzy arm having already resolved the high-confidence cases deterministically — `dedup_helpers.py:321-380`) (`NodeDuplicate` / `NodeResolutions`, `node_operations.py:36`, `dedupe_nodes` prompt family) — i.e. SELECT-over-supplied-list, exactly in-repo §X2. **ER-in-practice (arXiv:2607.26298) lesson 2: *"precision and recall need separate fixes, not a shared threshold — precision needs hard rule-based vetoes, recall needs more diverse candidate retrieval"*** | arXiv:2607.26298 reports the *lesson*, not a delta; Zep reports no isolated dedupe number | **no decision** — and `#2730`'s ladder rung 4 is the recorded home | 1 model call per capture run (bounded, temperature 0.0); it already exists as phase 2 | **partly BUILT**: `resolve_entities` (`extractor_v2.py:5938`) is already two-phase — deterministic rung, then **one LLM SELECT call** validated against the candidate table, with `new`/`held` **disjoint** (#11653). Missing vs. §X2/§X4: (a) the candidate set is **unscoped and unranked**; (b) **no grounded-evidence requirement** (a returned id + the narrative quote — §X4 says self-reported probability must never auto-link in the mid band); (c) **no hard vetoes**; (d) **no third band** (link-without-merge via `SAME_AS`). ⛔ **And `SAME_AS` does not exist:** it is in no allowlist — `tortoise/projection/edges.py:717` `_VALID_EDGE_PREDICATES` has no `sameAs`/`hasAlias`/`alias` — and the 2730 brief (read in §0) records the identity primitives as an **"OWNER QUESTION, not an adoption"** (`docs/research/2026-09-24-2730-entity-attachment/research-brief.md:241`). So B1's third band is **gated on an ontology decision this brief cannot take**; see N-4. |
| **B2** edges as the strongest signal — neighbour overlap (Jaccard over typed edges) | **Zep's Jaccard is over NAME SHINGLES, not graph neighbourhood** (`_jaccard_similarity`, 3-gram, `_FUZZY_JACCARD_THRESHOLD = 0.9`, `dedup_helpers.py:89-144,132-141,321-380`) — and it is a **blocking** signal, not a ranking feature. **No surveyed system reports typed-edge neighbourhood overlap as a ranking feature.** | none found | **no decision** | — | **NEW, and it has NO SUBSTRATE where the failure is measured.** `#4211`'s census on the frozen store: **484 Points, 484 edges, ALL `CONTAINS`; `aboutObject` 0, `IMPL` 0, `NAND` 0.** An edge-overlap feature over typed epistemic edges would compute on **zero edges** on the store the epic exists to fix. |

### C. Ranking

| # | what the top performers do | gain | contradicts? | cost | our status |
|---|---|---|---|---|---|
| **C1** cross-encoder rerank | MemPalace's held-out hybrid v4 adds a **Haiku reranker** (in-repo `comparison-systems.md` §3.3, vendor claim); Mastra's counter-case reaches the top **without** an entity graph — ⛔ **but also without a reranker** (§5.1: *"No per-turn dynamic retrieval"*), so it does **not** show that a strong reranker substitutes for graph structure; it is evidence for a **third** route (observation-log context construction). MMR + cross-encoder is the standard pair *where a reranker is used at all* | MemPalace: 96.6 % raw → 98.4 % held-out with the reranker (⚠️ vendor, not re-run by us) | no decision | one model forward pass per candidate; latency, not money | **BUILT in the ASK path only, default OFF — and there is NO noun-path rerank.** The only call site is `ask_lane.py:631` (`ask_lane_rerank`); `TORTOISE_ASK_RERANK` is default OFF and degrades to the unreranked pool — so the noun path has no switch to arm, and "built-off in the noun path" is the epic's framing, not the code's. **Do not re-implement** (epic is right about this). |
| **C2** BM25 instead of TF-IDF | every comparable runs a real BM25 or better; **gbrain's own numbers: keyword/BM25 19.8 % vs hybrid+expansion 97.6 %** on LongMemEval `_s` (in-repo §3.3, ⚠️ vendor, any-hit variant) — the single most dramatic lexical-vs-hybrid gap in the repo's record | see left | **V1 §6.2**: the text index **stays** in the graph until **M3** measures a BM25-capable Postgres option — so the index move is gated, and only that | in-process BM25 ≈ 0 marginal; the index move is a migration | **BUILT-ON (embedded lane) / open (hosted)**: `BM25_DEFAULT_ENABLED = True`, `bm25.py:166`. ⚠️ **The epic's STATUS is correct** — its C2 scope is the Postgres index move, which is exactly what M3 gates; what misleads is the row's TITLE, "BM25 instead of TF-IDF". M3 remains open and correctly scoped. |
| **C3** SPLADE / learned sparse | SPLADE (arXiv:2107.05720) is a **trained** sparse-expansion model: *"trained end-to-end in a single stage"*, competitive with dense. **No top LongMemEval performer surveyed uses it** — Zep, Mem0, Agent Zero, Human-Inspired and the counter-case are all hybrid-dense + graph/lexical, none learned-sparse | SPLADE's own paper reports parity-with-dense on MS MARCO-class benchmarks, **not** LongMemEval | **V1 §6.2** gates it behind M3 — and the owner has **pre-authorised** it (epic's SPLADE decision) | a model to host + train/serve, on top of two lexical stacks we already run | **NEW, BLOCKED-ON-MEASUREMENT (M3), correctly in-epic.** ⚠️ **`rg -i 'splade' docs/ tortoise/ tools/` → 2 hits, both in this file**: the leg has **no pre-registration anywhere**, so a lane dispatched to it would be inventing its own falsifier. |

### D. Storage tiering

| # | what they do | gain | contradicts? | cost | our status |
|---|---|---|---|---|---|
| **D1** entity-level hot/cold, per-tenant RAM budget | bounded-cache policy literature (in-repo §7 table); **best trace-aware policy reached ~54 % hit rate for 2-hop expansions** | **unmeasured for answer quality** — *"whether a windowed graph harms retrieval quality is unmeasured anywhere"* (§7) | ✅ **YES, in two ways.** (a) §7: the window is **residency, not retention**, and *"a per-node TTL is the bound CONSUMPTION-MODEL.md forbids"*; (b) hosted FalkorDB has **no eviction *as far as we can establish*** — `STORAGE-ARCHITECTURE.md:188` records that per-graph eviction EXISTS (LRU idle threshold, evict interval, load-evict budget, reload from `/data/offload`, surviving restart) but is documented **only for FalkorDB Enterprise (self-managed)** and is *"not exposed on Cloud as far as we can establish"*, flagging confirmation with the vendor as *"the single most valuable thing to confirm"* (`:1617`) — so the unit must be a **projection-time window** — which the epic already states correctly | large (a new projection path + the §1.5 guardrails) | **NEW, and BLOCKED on a measurement that cannot currently be taken** — see D2 |
| **D2** the hot-list instrument (`read_ledger.py`) | — | — | ⛔ **YES, and this is the sharpest contradiction in the epic.** `SEARCH-ARCHITECTURE.md` §7: *"That ledger is TENANT-WIDE and is explicitly not a product input: §17.4.6 states that reading it for any product decision … **would be a violation**"*. §17.4.6 §3 deviation: *"it would be a violation if any product decision read it"* | — | **BUILT — but UNUSABLE for D1.** The signal must be **window-local** counters (§7), a different artifact. **The epic's D1→D2 link proposes a recorded violation.** |
| **D3** seed materialisation | — | — | **required before private-beta** (§7) | a write on the query path | **NEW**; the marker half exists (`tortoise/residency.py`, `TORTOISE_RESIDENCY`, default OFF) but the **two reason strings are OWED** (§7: `truncated_boundary`, `insufficient_graph` — *"neither string appears anywhere in the tree at this head"*) |
| **D4** windowing armed only on evidence | — | — | **DEFERRED on M2** by decision; private-beta runs **complete** (§1.5) | — | **BLOCKED, and further than the epic thinks** — see §4 |

---

## 3. Levers we are MISSING — the point of the exercise

Ordered by (evidence strength × our exposure).

### M-1 ⭐ **A name-entropy / length gate on fuzzy matching** — Zep has it, we have nothing

`_has_high_entropy` (`dedup_helpers.py:53-87,80`) refuses MinHash/Jaccard matching for names that are
**short or low-entropy**: `_NAME_ENTROPY_THRESHOLD = 1.5`, `_MIN_NAME_LENGTH = 6`, `_MIN_TOKEN_COUNT = 2`
(`:31-33`). Short/repetitive names *"signal we should defer resolution to the LLM instead of trusting fuzzy
similarity"*. Exact-name matching still runs for **all** names; only the fuzzy path is gated.
URL: `github.com/getzep/graphiti/blob/main/graphiti_core/utils/maintenance/dedup_helpers.py`

**Why it matters to us specifically — and two corrections to the obvious reading.** 62.3 % of our `:Object`
names are bare definite descriptions, and `"the plan doc"` / `"the play doc"` is the pair that worries us.
⚠️ **But the gate does not cover that population, and that pair would not auto-link with or without it.**
`_has_high_entropy` (`dedup_helpers.py:80-87`) rejects a name only when it is **both** under 6 characters **and**
under 2 tokens, and otherwise requires Shannon entropy ≥ **1.5** (`:31`); `"the plan doc"` is 12 characters /
3 tokens at entropy ≈3.3, so it **passes** — as does every `the <noun>` name in that 62.3 %. On Zep's own
arithmetic the pair scores Jaccard **0.455** over 3-gram shingles, far under the **0.9** that arm requires; and
under our existing rung — which compares the **folded** names (`plan doc` / `play doc`) — `difflib` scores it **0.875**,
4.5 points under the **0.92** auto-merge threshold
(`entity_resolver.py:92-94`). So the entropy gate is **not** the precision half of A1/A2 for the
bare-definite-description class — what it actually removes is very short, low-entropy surface forms. **The
precision half for OUR population has to be built, not adopted**: that is a real gap the inventory does not
have either, and N-2's **F2** (“any auto-link on a low-entropy name”) cannot fire on the class named here,
because those names are not low-entropy. Cost ≈ 15 lines.

### M-2 ⭐ **MinHash/LSH blocking over name shingles** — a blocker FTS structurally cannot be

Zep: 3-gram shingles → 32 MinHash permutations → band size 4 → LSH buckets → Jaccard ≥ **0.9**
(`dedup_helpers.py:31-37, 89-144, 293-314`). Sublinear in the candidate set. ⚠️ **But the 0.9 threshold is
tight, and the example given for it is wrong:** `"release-notes"` vs `"notes releas"` scores Jaccard **0.583**
over exactly those shingles, so this arm **rejects** it. What the shingle key buys is not transposition
tolerance at 0.9 but **sublinearity** (LSH banding) at a precision-safe threshold; transposition and typo
proximity are the job of our `difflib` rung (≥ 0.60, review queue) and the semantic rung.

**Why it matters:** the tokenisation class is real, but two claims here need correcting. (a) **`search_graph`/FTS is not our only blocker — but the other one is NOT on this path.** A character-prefix blocker exists (`entity_resolver.block_key`, `:185-187`), yet it runs only inside `entity_candidates` (`:394`), the ambiguity-pair **review surface**; the capture matcher `extractor_v2._find_existing_entity` (`:5762-5781`) compares `_norm(name)` over the search table and never calls `block_key`. So the capture-path blocker set really is FTS alone. (b) ⚠️ **21.1 % and 30.0 % are the SUPERSEDED keyword-only figures**, not the product's read — `#11901` comment `6826507139` supersedes them, and on the hybrid read the same classes move **+25.6** (identifiers) and **+63.3** (paths) points — ⚠️ **`+63.3`, not the `+63.4` that comment prints: `#11901`'s correction comment `6826725103` fixes it (`29/30 − 10/30 = 63.333`; the `+63.4` came from subtracting two *rounded* percentages).** ⛔ **Do not add those deltas to the keyword-only bases to compose a hybrid percentage** — the same comment is explicit that *"the class deltas are between-run, not paired"*, so `21.1 + 25.6` and `30.0 + 63.3` are numbers **no artifact holds**. The identifier class's **mechanism is not established** — stated in `6825791142`'s DoD item (b) (*"a 5-node probe with `keep_numeric` toggled reproduces none of it, so nothing is diagnosed here"*), a caveat about mechanism rather than one of that comment's superseded figures. So this lever rests on a weaker premise than the row implies: paths are largely fixed on the product's read, and identifiers are half-fixed with an undiagnosed cause.

### M-3 **Identity-property blocking** — a second, cheaper key

Zep `_identity_key_for_node` / `_resolve_with_identity_properties` (`dedup_helpers.py:207-291`) matches on a
**declared identity property** before any similarity. ER-in-practice converges (arXiv:2607.26298 lesson 1:
*"no single matching algorithm wins everywhere … letting an automatic bake-off pick the winner"*).

**Why it matters:** it is the cheapest rung and it is **exact**, so it moves precision with no ambiguity — and
it is not in the ladder at all (§6 of the 2730 brief has scoped-exact → alias → embedding → adjudication →
review queue; nothing between alias and embedding).

### M-4 ⛔ **Cross-group merge re-verification (transitive-closure guard)** — the P0 the epic's B1 would introduce

arXiv:2607.26298 lesson 3, verbatim: *"One false-positive link can silently merge unrelated entities —
assuming 'A matches B' and 'B matches C' implies 'A matches C' lets a single bad link chain hundreds of
records together, so every cross-group merge must be actively re-verified."*

**Why this is urgent, not theoretical:** B1 proposes **three bands, one of which is "link with `SAME_AS`"** — ⛔ **but `SAME_AS` is not a writable predicate in this tree** (`tortoise/projection/edges.py:717`), and the 2730 brief records it as an **OWNER QUESTION, not an adoption** (`:241`). So the three-band ladder is **blocked on an ontology decision** before it is blocked on engineering, and N-4 must be read with that gate in front of it.
That is precisely the construct that produces merge chains — and no surveyed system we can read ships it
without re-verification. The 2730 brief's §X3 (*false merges are invisible and hard to undo*) is the same
finding from the other end. **B1 without M-4 is the mechanism by which one bad verdict becomes a
hundred-duplicate graph — the exact failure the KEEP ruling (`#11395`) exists to prevent.** This must be B1's
own acceptance criterion, not a follow-up.

### M-5 **Intent gate / per-query leg routing** — a top scorer's latency lever

Agent Zero Memory (arXiv:2608.29606, **95.60 % LongMemEval / 93.60 % LoCoMo** — *"a new state of the art …
improving over the strongest prior systems by +0.73 and +1.10 points"*): three parallel memory systems, and
*"a retrieval turn runs an **intent gate** (so self-contained turns add no latency), a source router, and
three concurrent agentic searches"*. The **accuracy-cost-latency frontier** is its headline: *"accuracy varies
by only 3.4 points while per-query cost varies by ~30×."*

**Why it matters:** it is the strongest evidence that at the top of the leaderboard, **cost is the lever and
accuracy is saturated** (+0.73 points over the previous best). We have `classify_query` (which turns every leg
on) — the inverse of an intent gate. **The epic has no cost/latency row anywhere.**

### M-6 ⚠️ **The write path, not the read path** — the counter-evidence the epic should carry

HaluMem write-path extraction recall: **Mem0 42.9 %, Supermemory 41.5 %**, against read-side self-reports of
90 %+ (in-repo `comparison-systems.md` §2/§3.3). Human-Inspired Memory (arXiv:2605.08538): dedup-based
consolidation = **97.2 % retention precision, 58 % store reduction, +21.8 pp**, but on LongMemEval at a 200K
budget **70.1 % vs 71.2 % raw** — *no read-side gain*; its win is the S-tier preference class (+13.3 pp).

**Why it matters:** it bounds the epic's own claim. Every lever in A–D is read-side. The write side is where the
measured collapse is: HaluMem puts **Mem0 at 42.9 %** and **Supermemory at 41.5 %** write-path extraction recall
(in-repo `comparison-systems.md:249-250`), i.e. **57.1 % and 58.5 % of updates missed** — ⚠️ **two systems, and both UNDER 60 %**, so cite those two figures rather than the wider claim. The only in-repo statement of the wider claim is `#11395`'s `OVERRIDES` line (`comment 6781921075`), which says *"five systems missing > 60 % of updates"* but carries **no per-system figures**, and the HaluMem paper's own table was not read by this lane — so **the five-system count has no per-system artifact behind it: correct it at the `OVERRIDES` line, or cite the two figures with that line amended.** That collapse is the reason `#11395`'s `OVERRIDES` line exists. **A capstone
that only reports recall@k will not show whether the epic earned its keep if the nouns were never written
correctly.** This is already recorded twice in-repo and the epic contains neither pointer.

---

## 4. Levers we should DROP or RE-EXPRESS

| lever | verdict | why |
|---|---|---|
| **D1 + D2 + D4** | **SUSPEND the block; do not sequence it** | Three independent blocks, each recorded: (a) **D2's signal is forbidden** (§7 + §17.4.6: reading the ledger for a product decision *"would be a violation"*); (b) **the window's only beneficiary metric `f` cannot be measured** — §15 **M8** (`STORAGE-ARCHITECTURE.md:1599`): *"The measurement is not taken and cannot be, today … no tenant serves a window (hosted capture off by owner ruling 2026-10-14)"*; (c) **private-beta runs complete by decision** (§1.5), and the window is armed on M2 *"when M2 says it is safe"* — M2 is unmeasured. **Nothing in D can be dispatched to a lane today.** D3's marker half is real and `residency.py` exists; keep D3 as the only D item with a buildable shape. |
| **B2 as framed** | **RE-EXPRESS** | The premise (*"neighbour overlap over typed edges is the highest-precision feature available"*) is **not what any surveyed system does** — Zep's Jaccard is over **name shingles** for blocking. And our failing store has **0 typed epistemic edges** (`#4211` census). Re-express as: *"name-shingle Jaccard blocking (M-2) now; typed-edge overlap only once `aboutObject` exists on capture turns — which is `#3664`'s substrate question."* |
| **A3 as framed ("name + context")** | **RE-EXPRESS to Zep's SHAPE — but as SEQUENCED work, not a shortcut** | `Object.description` and `Object.summary` are **0 rows** (§12.2c `:1317`), so "name+context" embeds the empty string. Zep's precedent is **`name_embedding`, 1024-d, of the name alone** (`:1298`), i.e. the **RESOLUTION KEY** job (`:1313`). ⛔ **That shape does not exempt the work from the sequencing:** §12.2c's objection is to indexing 7,859 names of which 62.3 % are bare definite descriptions, and it applies to **BOTH jobs a vector could do** — entry key and resolution key (`:1319`, `:1303`) — because the fix is the extractor making *"names stop being near-synonymous"* (`:1327`); `:1330` then rules the entity-vector work **DOWNSTREAM of the extractor work, not parallel to it**. The KEEP ruling (`#11395` comment `6781921075`) restates it unchanged: *"fix the names, then the vector earns its keep."* So the re-expression is Zep's shape applied to the **ruled, sequenced** work — it is **not** buildable today, and measuring the resolution key over the current name population would measure exactly the property the sequencing exists to fix. |
| **C2 as framed ("we run TF-IDF")** | **CORRECT, keep the M3 gate** | We run BM25 on the embedded lane (`BM25_DEFAULT_ENABLED = True`). The open question is the **index move**, which §6.2 already scopes. Drop the "build BM25" reading — **a lane dispatched on the epic's text would duplicate `tortoise/bm25.py`.** |
| **widen the window (A4's headline)** | **DROP from A4's headline; the paired decision stays OPEN** | `#11901` measured the window and **declined neither** — its DoD re-tick reads *"the window and a verdict over the supplied candidates decided together — **not decided, and deliberately not changed**"* (`comment 6826507139`). What the measurement does say: +30.2 % of misses recovered, **0.0 %** of the variant class moved, and 15 *narrows* us (our effective list is 40.4). So the epic's "widen the window" is the wrong first noun, **and the window question is still un-owned**: no child in §6 carries the paired decision (N-4 takes the verdict half only). |

---

## 5. What this lane could NOT establish (declared, never skipped)

1. **No search engine AT AUTHORING TIME** (rungs 1–2 failed, §0), so **Mem0's April-2026 algorithm was not
   read** — its vendor blog returned **HTTP 404** on the only path this pass could construct. Mem0's numbers
   here are its **arXiv paper** (LoCoMo 92.5, +26 % over OpenAI, graph memory **+2 %** over base) plus its own
   published 2026 report (**94.4 LongMemEval, ~6,900 tokens/query**) — ⚠️ **both vendor self-reports, neither
   independently re-run by us.** ⛔ **Mastra is no longer in this list:** §5.1 read its primary source and
   verified the 94.87 % figure against it.
2. **Cognee and Honcho remain unread — and this is now the largest hole.** §5.1 read **Mastra** and
   **Letta/MemGPT** to primary sources, and added **Supermemory**'s numbers from Mastra's cross-system
   leaderboard (per-row provenance is not stated there, and **Mastra is a competitor on that same board** — so
   *not re-run by us*, not an independent measurement). **Cognee** (an
extract → "cognify" → load graph pipeline) and **Honcho** (implicit user representation) were still not read
   to a primary source. **Do not treat their absence from §2/§3 as evidence they have nothing to teach** — it
   is an unread surface.
3. **Every gain figure in §2 is someone else's, on a different harness.** We have **no LongMemEval number of
   our own** — `docs/benchmarks/comparison-systems.md` §0: `recall_all@5` is **PENDING — #2105, no number
   exists**, and the repo's own cautionary case is a 97.6 % headline that was any-hit (gbrain's erratum,
   §3.3). Zep's +18.5 % is reported for a whole architecture on its own harness; Mem0's is LoCoMo with an
   LLM judge. **None is comparable to `#11901`'s 61.3 %/73.0 %** — that is a **candidate recall@k on our own
   capture corpus**, a different quantity from answer accuracy.
4. **The one measurement only production data can settle: `f`** (§15 **M8**, `STORAGE-ARCHITECTURE.md:1599`) and the **production
   duplicate-noun rate** (`#11901` comment `6826507139`: *"phase 1 measured, production rate NOT measured"* — phase 2,
   the live LLM resolver, *"is live and is instructed to resolve exactly this class"*). Both are blocked on a
   tenant workload nobody serves.
5. **No ablation exists, anywhere, isolating an entity vector's contribution** to multi-hop answer quality
   (§12.2c records the same absence). So A3's gain is **framework consensus, not measurement** — and the epic
   should say so rather than implying otherwise.
6. **`hasAlias` searched, not exhaustively:** `rg 'hasAlias'` over `*.py|*.yml|*.json` → 0. A predicate
   declared in a non-matching file form would be missed.

### 5.1 Follow-up pass at this head: rung 2 available, and the route the inventory does not weigh

Re-run at this head (`4d39a575c44a`, after the authoring pass): `web_search` (`sonar`) returns results, and
both primary sources this brief previously declared casualties resolve. **What follows is read from them,
not from `#11395`'s second-hand record.**

**Mastra, Observational Memory — `mastra.ai/research/observational-memory` (primary).** Two background agents,
an **Observer** and a **Reflector**, maintain a dense **observation log** that *replaces raw message history as
it grows*; the context window is observations at the start and live messages at the end. The load-bearing
property for this epic, quoted because it is the entire point:

> *"No per-turn dynamic retrieval based on the user prompt. No dynamic injection. Just a stable log of what
> happened."* — resting on its own contrast: *"Most memory systems change the prompt every turn by injecting
> dynamically retrieved context; OM doesn't."*

Scores on `longmemeval_s` (500 questions, ~57 M tokens, ~50 sessions per question), same page: **94.87 %** with
gpt-5-mini, **93.27 %** with gemini-3-pro-preview, **84.23 %** with gpt-4o (the official benchmark model). The
page calls 94.87 % *"the highest score ever recorded on this benchmark"* — ⛔ **that is the vendor's claim and it
is superseded: §3 M-5 already records Agent Zero Memory at 95.60 % (`arXiv:2608.29606`), and the +0.73 in M-5's
own quote is precisely the gap over this number.** With gpt-4o, OM beats the oracle (82.4 % — a configuration
handed only the 1–3 conversations containing the answer) by 2 points, ⚠️ **a delta Mastra itself calls "small
enough to be within run variance".**

**The lever this names, which the inventory does not contain.** OM is the strongest **no-retrieval** result on
this benchmark: it reaches the top band **without a retrieval step at all**, by moving the work to *context
construction* — compress history into dense observations so that *"what do we retrieve"* never gets asked.
Every item in the inventory (A1–A6, B1–B2, C1–C3) **assumes** the retrieval step and improves it.

⛔ **Read that against §3 M-5, not instead of it.** The outright top scorer on LongMemEval is **Agent Zero
Memory at 95.60 %**, and it is **retrieval-heavy by construction** — an intent gate, a source router, and three
concurrent agentic searches (`arXiv:2608.29606`). So the two results **bound a choice** rather than one
settling it: at the top end retrieval still wins, and the no-retrieval route lands within **0.73 points** of it.
What §5.1 establishes is a **gap in the inventory, not a verdict against it**: the epic lists only
retrieval-side levers and so under-weights a route that reaches near-parity without them — it does **not**
license the stronger claim that the ranker is the wrong investment, and a lane re-prioritising on §5.1 alone
would be over-reading this section.

⚠️ **Its harness is Mastra's and the result is a vendor self-report** — a **direction with a citation, not a
measured gain on our corpus**; §6.1's discipline applies to it exactly as to every number above. The same
direction is argued from our own side in `STORAGE-ARCHITECTURE.md` §12.2b (`:1280`): *"levers 1 and 2 are the
same work — write less, and write things worth keeping."*

**Letta/MemGPT — `letta.com/blog/sleep-time-compute` (primary).** MemGPT's tiers are core memory (editable
blocks held in context), archival memory (a searchable store) and recall memory (full history); **MemGPT 2.0
adds a sleep-time agent** that runs during idle time, rewriting the memory state and turning *"raw context"*
into *"learned context"* before the user asks. The same shape from the other side: the lever is
**consolidation during downtime** — again, not a ranker.

**Supermemory — numbers as compiled on Mastra's cross-system leaderboard** (⚠️ **not an independent
measurement**: that page states no per-row provenance for the competitor rows, and Mastra itself occupies three
of the top six (ranks 1, 2 and 4) — treat these as *not re-run by us*): **85.20 %** (gemini-3-pro), **84.60 %** (gpt-5), **81.60 %**
(gpt-4o) on the same `longmemeval_s`
basis. For scale on that same board: Hindsight 91.40 % (gemini-3-pro), EmergenceMem Internal 86.00 % (gpt-4o) — ⚠️ **which Mastra footnotes as an *"Internal"* configuration that is *"not publicly reproducible"***, a closed implementation that also uses *"multi-step reranking (slower, more expensive)"*; its public Simple configuration is 82.40 %. **The highest *openly reproducible* score on the official benchmark model is OM's own 84.23 %.**

---

## 6. The cut — proposed children, each with its falsifier

**Amend, do not duplicate:** `#2518` (A4 — built; restate as *"arm and measure"*), `#4211` (A6 — already
carries a census, a design and four falsifiers; leave it), `#12184` (A5 — accurate as filed), `#11901` (the
measurement of record — add this brief's corrections as a comment, do not re-open), `#2513` (parent context).

| proposed child | one-line scope | falsifier it carries |
|---|---|---|
| **N-1 — arm and measure the referentiality gate** (the omitted lever) | run `#5087` R1's measurement: 25 labelled rows + the 12 most-referenced rows a name-pattern rule would delete; then arm `TORTOISE_REFERENTIALITY` and re-measure the duplicate-noun rate on the same 419-pair fixture `#11901` used | **F1** the gate does not move the duplicate rate on the 419-pair fixture → leave it off and close with the number; **F2** it drops a must-keep referent on the 37 rows (`the admin-merge rail` is the named trap, `referentiality.py:38-49`) → the cue is wrong and the class belongs to the seam |
| **N-2 — the comparison-side fuzzy key + entropy gate** (A1 corrected, + M-1) | reuse the EXISTING fold — `entity_resolver.normalize_entity_name` and `block_key` — and derive only Zep's **fuzzy** (punctuation-stripped) comparison key from it (never rewrite `name`), plus `_has_high_entropy`'s gate on which matcher may fire; reuse `DEFAULT_THRESHOLD = 0.72`, never a second constant. ⛔ **Never mint a third stored key beside `canonical_name` and `aliases[]`** — `#12047`'s review N8/P0 (`docs_miner.py:543-550`) records that as "a second key space in one property", against the "one fold, one key" invariant | **F1** the fuzzy key does not bind the 419-variant class beyond the separator fold `#11901` measured (100 % of in-window separator, 0 % of the 179 article pairs) → the fold is the whole win and the shingle index is not earned; **F2** any auto-link on a low-entropy name → the gate is mis-set |
| **N-3 — multi-key blocking with pair completeness** (A2 + M-2 + M-3) | union of blocking keys (identity property, exact-normalised, **an alias read** — see A2: `aliases[]` exists but `find_live_holder` has no production caller, so wiring it is part of this key union — name-shingle LSH) with **pair completeness measured** as the metric; report reduction ratio alongside it — ⛔ **and keep Zep's ORDERING: a fuzzy match above the Jaccard threshold resolves deterministically BEFORE the LLM adjudicator (`dedup_helpers.py:321-380`), which is the precision property, not an optimisation** | **F1** pair completeness does not rise above the **current CAPTURE-path blocker set** — FTS via `_find_existing_entity` (`extractor_v2.py:5762-5781`), which does **not** call `block_key` — on the `#11901` population → no blocker needed; **F2** pair *quality* (precision of the union) falls below that same set → the extra keys are noise |
| **N-4 — the verdict's three bands + hard vetoes + the closure guard** (B1 + M-4) | ⛔ **FIRST: `SAME_AS` is not writable** (`edges.py:717`) and the 2730 brief records it as an **owner question** (`:241`) — so the three-band half **cannot dispatch until that ontology decision lands**; the vetoes and the closure guard can. then extend `resolve_entities`' existing two-phase to the ladder's rung 4: merge / link (`SAME_AS`) / new, with grounded-evidence gating (§X4), **and cross-group merge re-verification** (arXiv:2607.26298 lesson 3) as an acceptance criterion, not a follow-up | **F1** no verdict in the mid band ever changes a disposition vs today's exact-only rung → the band earns nothing; **F2** any `SAME_AS` chain of length ≥ 2 that re-verification does not catch → the guard is inert and B1 cannot ship |
| **N-5 — pre-register the SPLADE leg** (C3, before M3) | write the falsifier the leg currently does not have: what number, on which corpus, would make SPLADE worth a hosted trained model on top of `bm25.py` + the fulltext leg | **F1** M3 fails → close the leg with the evidence (owner-pre-authorised); **F2** M3 passes but SPLADE does not beat `bm25.py`'s arm (already on) → the model is not earned even though the index move is |
| **N-6 — drop or re-express the D block** | a decision comment, not a build: record that D1/D2/D4 cannot be dispatched (D2's signal is a recorded violation; `f` is unmeasurable; private-beta runs complete), and keep D3 alone | **F1** an owner reading of `f` becomes available → reopen D1 on that evidence; until then, no lane |

**The one I would dispatch first:** **N-1.** Its blocker is 37 rows of labelling, its lever is already
written, and it attacks the population (62.3 % bare descriptions) that A1, A3 and B1 each plan to reach
expensively.

### 6.1 Correction to a citation this brief inherited, found by verifying it

This brief first repeated `#12184`'s locators (`tortoise/ranking.py:766-768` for the two-branch dispatch,
`:1233` for `StateRanker._fetch_object_signals`) as "verified". Re-read at `1f28d107cf`:

- the dispatch is **`:926-928`**, inside `GraphRanker._fetch_signals` (declared `:914`, class at `:677`);
- `StateRanker._fetch_object_signals` is **`:1773`** (`StateRanker` declared `:1206`);
- `:766-768` is the `signals_answered` bookkeeping inside `GraphRanker.rerank` (the `_fetch_signals` call is
  `:764`; the contested-boost block is `:769-772`) — a *third* site, neither the dispatch nor the boost block.

**The defect `#12184` describes is real and reproduces at this head** — only the line numbers drifted.
Recorded because the discipline is the point: *a number on a published surface that no committed artifact
holds* is the class `#11901`'s **correction comment** (`6826725103`, *"Correction to the figures in the comment above"*) caught one of — it is also where `+63.4 → +63.3` is fixed — and re-deriving it costs a
lane a file-wide search.
