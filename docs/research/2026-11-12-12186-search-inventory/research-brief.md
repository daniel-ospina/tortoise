# Search epics inventory — validated against the top agent-memory systems (#12186)

**Lane:** `research/12186-search-inventory` · **Type:** research only, no implementation
**Question:** does the epic's inventory (A1–A6, B1–B2, C1–C3, D1–D4) match what the best-performing
agent-memory systems actually do, and what does the epic not see?

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
| **D1** entity-level hot/cold, per-tenant RAM budget | bounded-cache policy literature (in-repo §7 table); **best trace-aware policy reached ~54 % hit rate for 2-hop expansions** | **unmeasured for answer quality** — *"whether a windowed graph harms retrieval quality is unmeasured anywhere"* (§7) | ✅ **YES, in two ways.** (a) §7: the window is **residency, not retention**, and *"a per-node TTL is the bound CONSUMPTION-MODEL.md forbids"*; (b) hosted FalkorDB has **no eviction *as far as we can establish*** — `STORAGE-ARCHITECTURE.md:188` records that per-graph eviction EXISTS (LRU idle threshold, evict interval, load-evict budget, reload from `/data/offload`, surviving restart) but is documented **only for FalkorDB Enterprise (self-managed)** and is *"not exposed on Cloud as far as we can establish"*, flagging confirmation with the vendor as *"the single most valuable thing to confirm"* (`:1619`) — so the unit must be a **projection-time window** — which the epic already states correctly | large (a new projection path + the §1.5 guardrails) | **NEW, and BLOCKED on a measurement that cannot currently be taken** — see D2 |
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
| **D1 + D2 + D4** | **SUSPEND the block; do not sequence it** | Three independent blocks, each recorded: (a) **D2's signal is forbidden** (§7 + §17.4.6: reading the ledger for a product decision *"would be a violation"*); (b) **the window's only beneficiary metric `f` cannot be measured** — §15 **M8** (`STORAGE-ARCHITECTURE.md:1601`): *"The measurement is not taken and cannot be, today … no tenant serves one (hosted capture off by owner ruling 2026-10-14)"*; (c) **private-beta runs complete by decision** (§1.5), and the window is armed on M2 *"when M2 says it is safe"* — M2 is unmeasured. **Nothing in D can be dispatched to a lane today.** D3's marker half is real and `residency.py` exists; keep D3 as the only D item with a buildable shape. |
| **B2 as framed** | **RE-EXPRESS** | The premise (*"neighbour overlap over typed edges is the highest-precision feature available"*) is **not what any surveyed system does** — Zep's Jaccard is over **name shingles** for blocking. And our failing store has **0 typed epistemic edges** (`#4211` census). Re-express as: *"name-shingle Jaccard blocking (M-2) now; typed-edge overlap only once `aboutObject` exists on capture turns — which is `#3664`'s substrate question."* |
| **A3 as framed ("name + context")** | **RE-EXPRESS to Zep's SHAPE — but as SEQUENCED work, not a shortcut** | `Object.description` and `Object.summary` are **0 rows** (§12.2c `:1317`), so "name+context" embeds the empty string. Zep's precedent is **`name_embedding`, 1024-d, of the name alone** (`:1298`), i.e. the **RESOLUTION KEY** job (`:1313`). ⛔ **That shape does not exempt the work from the sequencing:** §12.2c's objection is to indexing 7,859 names of which 62.3 % are bare definite descriptions, and it applies to **BOTH jobs a vector could do** — entry key and resolution key (`:1319`, `:1303`) — because the fix is the extractor making *"names stop being near-synonymous"* (`:1327`); `:1330` then rules the entity-vector work **DOWNSTREAM of the extractor work, not parallel to it**. The KEEP ruling (`#11395` comment `6781921075`) restates it unchanged: *"fix the names, then the vector earns its keep."* So the re-expression is Zep's shape applied to the **ruled, sequenced** work — it is **not** buildable today, and measuring the resolution key over the current name population would measure exactly the property the sequencing exists to fix. |
| **C2 as framed ("we run TF-IDF")** | **CORRECT, keep the M3 gate** | We run BM25 on the embedded lane (`BM25_DEFAULT_ENABLED = True`). The open question is the **index move**, which §6.2 already scopes. Drop the "build BM25" reading — **a lane dispatched on the epic's text would duplicate `tortoise/bm25.py`.** |
| **widen the window (A4's headline)** | **DROP from A4's headline; the paired decision stays OPEN** | `#11901` measured the window and **declined neither** — its DoD re-tick reads *"the window and a verdict over the supplied candidates decided together — **not decided, and deliberately not changed**"* (`comment 6826507139`). What the measurement does say: +30.2 % of misses recovered, **0.0 %** of the variant class moved, and 15 *narrows* us (our effective list is 40.4). So the epic's "widen the window" is the wrong first noun, **and the window question is still un-owned**: no child in §6 carries the paired decision (N-4 takes the verdict half only). |

---

## 5. What this lane could NOT establish (declared, never skipped)

1. **No search engine AT AUTHORING TIME** (rungs 1–2 failed, §0), so **Mem0's April-2026 algorithm was not
   read** — its vendor blog returned **HTTP 404** on the only path this pass could construct. Mem0's numbers
   here are its **arXiv paper** (LoCoMo **66.88**, 68.44 with graph memory, +26 % over OpenAI, graph memory
   **+2 %** over base) plus its own published **2026 report** (**LoCoMo 92.5**, **94.4 LongMemEval**,
   ~6,900 tokens/query) — ⚠️ **both vendor self-reports, neither
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
4. **The one measurement only production data can settle: `f`** (§15 **M8**, `STORAGE-ARCHITECTURE.md:1601`) and the **production
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
| **N-4 — the verdict's three bands + hard vetoes + the closure guard** (B1 + M-4) | ⛔ **FIRST: `SAME_AS` is not writable** (`edges.py:717`) and the 2730 brief records it as an **owner question** (`:241`) — so the three-band half's **`SAME_AS`-edge form** cannot dispatch until that ontology decision lands, and a **redirect form is not a substitute** — `resolved_from` is the merge pointer in every reader (§7.7 item 4), so it builds a merge under a different label; the vetoes and the closure guard can in either case. then extend `resolve_entities`' existing two-phase to the ladder's rung 4: merge / link (`SAME_AS`) / new, with grounded-evidence gating (§X4), **and cross-group merge re-verification** (arXiv:2607.26298 lesson 3) as an acceptance criterion, not a follow-up | **F1** no verdict in the mid band ever changes a disposition vs today's exact-only rung → the band earns nothing; **F2** any `SAME_AS` chain of length ≥ 2 that re-verification does not catch → the guard is inert and B1 cannot ship |
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

---

# §7. Round 2 — the primaries round 1 could not read, and the three disputes settled

**Lane:** `research/12186-round2-depth` · verified at **`9828250732`** (round 1 pinned `1f28d107cf`)
· research only, no implementation. This section **extends** §1–§6; it does not replace them, and where
it falsifies a round-1 claim the falsified claim is named so the two can be read together.

## 7.0 Rung taken, queries, and one thing that changed

**Rung 1 worked this time.** `mcp__seo-intelligence__perplexity_search` was force-loaded and answered on
this cell; round 1's cell reported `Unknown MCP server 'seo-intelligence'` (§0). Rung 2 was never needed.

Queries run (rung 1, `perplexity_search`, ~$0.005 each): (1) `FalkorDB db.idx.fulltext.queryNodes score
TFIDF BM25 scorer fulltext index`; (2) `RediSearch FT.SEARCH default SCORER TFIDF BM25 TFIDF.DOCNORM
documentation`; (3) `FalkorDB fulltext index "scorer" option BM25 support CREATE FULLTEXT INDEX OPTIONS
language stopwords`; (4) `Mem0 new memory algorithm April 2026 arXiv single-pass ADD-only extraction entity
linking`; (5) `Hindsight agent memory four retrieval arms cross-encoder entity temporal graph LongMemEval
open source`; (6) `Cognee memory graph "cognify" entity resolution deduplication how it works docs`;
(7) `Honcho plastic labs memory architecture "deriver" peer representation theory of mind how it works`;
(8) `entity resolution merge chain transitive closure error rollback audit trail record linkage reversible
merge best practice`; (9) `Supermemory architecture how it works memory graph entity extraction docs`.

Primary sources fetched (`web_fetch` / raw): `docs.falkordb.com/cypher/indexing/fulltext-index`;
`getzep/graphiti` → `dedup_helpers.py`, `node_operations.py`, `prompts/dedupe_nodes.py`, `nodes.py`;
`mem0ai/mem0` → `memory/main.py`, `utils/scoring.py`, `utils/entity_extraction.py`, `README.md`;
`arxiv.org/html/2512.12818v1` (Hindsight/TEMPR full text, §4.2.3–§4.2.4); the four ER sources named in
§7.7 (`erikevenson`, `archtin`, `granveo`, `writeitai/remember-stack`); Mem0's
2026 report (`mem0.ai/blog`); `docs.cognee.ai/python-api/cognify`; `honcho.dev/docs/v3/…`;
`supermemory.ai/docs/…`.

**Research-skill PREFLIGHT lines** (the skill's required slots — §0 states round 1's, as a table):

`Search tool:` rung 1 — `mcp__seo-intelligence__perplexity_search` (~$0.005/query; the MCP search tool
exposes no model parameter, so none can be named) — queries: **9 attempted / 9 returned**; rung 2 not used;
rung 3 (`web_fetch`, free) for every primary source above.
`Graph:` **0 hit(s) over 0 queries — unread — no query ran** | wrote: **not written — write-back owed, no
write attempted** (no reachable tenant for these populations, §7.9 item 1; `/v1/search` never issued) →
nothing observed, and a lane with a reachable tenant still **must** mint point + `IMPL → #12186` + NAND edges
| failed: **none (no query attempted)**.

The slot above is the skill's own vocabulary, not a substitute: `unread — no query ran` is the honest value
for a box that cannot reach the graph, and it is **not** `nothing held` — the skill refuses that value at any
query count when no query ran, so a zero here says nothing about what the graph holds.

⚠️ **Locators drift, and a pin is not a citation.** Round 1's `sdk.py:39588-39606` for the BM25 arm's wiring
is **`:40740-40758`** at this head — the file grew **1,184** lines in one day of merges (`1f28d107cf` =
51,075 → `9828250732` = 52,259; every anchor in this region moves **+1,152** — `_prf_supersedes_the_arm`
`39585 → 40737`, the `is_embedded` gate `39588 → 40740`, the block's close `39606 → 40758` — and the arm's
enclosing block is `:40806-40854`). Every locator below
is re-derived at `9828250732`; a lane reading this doc at a later head must re-derive, not copy.

## 7.1 C2 SETTLED — the product lane runs TF-IDF, that is the vendor's own name for it, and BM25 is one gate away

### The vendor says TF-IDF, and the API has no scorer to change it

FalkorDB's full-text documentation states the returned score's identity directly
([docs.falkordb.com/cypher/indexing/fulltext-index](https://docs.falkordb.com/cypher/indexing/fulltext-index)):

> "In addition to yielding matching nodes, full-text index scans will return the score of each node. **This
> is the TF-IDF score of the node**, which is informed by how many times the search terms appear in the node
> and how closely grouped they are."

and in its own FAQ on the same page: *"The score reflects **TF-IDF** relevance ranking."* The indexing
overview repeats it once more (*"scoring based on TF-IDF"*,
[docs.falkordb.com/cypher/indexing](https://docs.falkordb.com/cypher/indexing)).

**The configuration surface carries no scorer.** The documented index options are `language` and `stopwords`
(index-level) and `weight` / `nostem` / `phonetic` (field-level); the query surface is
`db.idx.fulltext.queryNodes(label, string)` — *a string*, no options map
([docs.falkordb.com/cypher/procedures](https://docs.falkordb.com/cypher/procedures)). RediSearch itself
does expose `SCORER` (`BM25STD` is the current default — the page titles that section *"BM25STD (default)"* and
records that *"the BM25 scorer was renamed BM25STD in Redis Open Source 8.4. BM25 is deprecated"*,
[redis.io/.../scoring](https://redis.io/docs/latest/develop/ai/search-and-query/advanced-concepts/scoring/)),
but **FalkorDB does not surface that parameter to Cypher** — so BM25 is not reachable inside our text index
by any documented route.

**And our code passes no options at all.** `_create_fulltext_index`
(`tortoise/projection/__init__.py:20755`; the two forms at `:20786-20787`) issues either
`CREATE FULLTEXT INDEX FOR (n:{label}) ON ({fields_expr})` or the legacy procedure, with **no `OPTIONS`
block** (`:20785-20788`) — so the index is on vendor defaults: default language, default stopwords, TF-IDF.

### Our own tree says three different things, and one of them is a comment error

| site | claim | verdict |
|---|---|---|
| `tortoise/sparse.py:13-14` | RediSearch's *"TFIDF scorer"* ranks by term-overlap, *"which is the BM25-style rank behavior the epic's scope prescribes"* | ⛔ **wrong on the mapping.** TF-IDF is not BM25-style: it has **no document-length normalisation** (that is Redis's separate `TFIDF.DOCNORM`), and its idf is the vendor's own documented `log2(1 + total_docs / docs_with_term(term))` rather than BM25's probabilistic form (`ln(1 + (N−n+0.5)/(n+0.5))`, whose `+1` inside the log is what keeps the score non-negative). So a length-heavy profile scores differently under the two. The vendor calls it TF-IDF; so should we. |
| `tortoise/bm25.py:7-10` | quotes `sparse.py` (`bm25.py:5`, citing `sparse.py:13-14`), then reports the **measurement**: on the embedded (falkordblite) lane the shipped scorer *"behaves like a per-matched-term count whose weight is NOT driven by rarity and whose score is NOT driven by document length"* | ✅ the measurement is the authority for **that lane only**: the *embedded* scorer prices **neither idf nor length**. ⚠️ It does **not** characterise the hosted `db.idx.fulltext` leg — `SEARCH-ARCHITECTURE.md:250-251` itself records that leg as unmeasured — so the hosted leg's idf/length behaviour is an **open measurement**, not a finding of this row. |
| `SEARCH-ARCHITECTURE.md:§6.2` (`:163`, `:241-246`, `:268-269`) | *"The FalkorDB leg is RediSearch's TF-IDF scorer"* (`:244`; the section table restates it at `:163`), the 8–23 nDCG@10 gap is *"measured against a BM25 implementation"* (`:246`), **how far our hosted leg sits from that baseline is unmeasured** (`:246`), and *"the text index **stays** until **M3** measures…"* (`:268-269`) | ✅ **correct, and more careful than its own epic row** |

⇒ **The epic's C2 row title, "BM25 instead of TF-IDF", is literally accurate for the product lane.** Round 1
corrected the status (which was right) but also proposed dropping the **"build BM25"** reading — its §4 C2
row's own words: *"Drop the "build BM25" reading — a lane dispatched on the epic's text would duplicate
`tortoise/bm25.py`"* — on the grounds that *"we run BM25 on the embedded lane"*. That grounding is true
**only on the embedded lane**, and it is not the lane the product runs. ⚠️ Round 1's *"the title reads as a
gap to fill"* and this section's *"the title is literally accurate"* answer different questions (what a lane
would **do**, vs what the title **describes**), so they are compatible: keep the title, correct the instinct.

### The bound round 1 stated but never explained: the arm is embedded-only for a latency reason, not a decision

`tortoise/search_engine.py:3103-3107`, verbatim:

> "…`search_keys` into Python and tokenises all of them on the caller's thread, OUTSIDE
> `degradation_chain`'s `as_completed` deadline (only the driver-side query timeout bounds it). **That is
> why the arm is gated off, is embedded-lane-only, and why its latency must be measured (#8486) before it is
> enabled anywhere.**"

with `DEFAULT_BM25_CORPUS_CAP = 20000` (`:3108`) and the arm gated on `is_embedded`
(`tortoise/sdk.py:40641`, `:40740`; `BM25_DEFAULT_ENABLED = True` at `tortoise/bm25.py:166`,
`run_bm25_query` at `search_engine.py:3198`). So the hosted wall is **a ≤20 000-row corpus read tokenised
in Python off the deadline**, not a decision that hosted should stay TF-IDF. `#8486` — the issue the code
names as this latency's gate — is **OPEN and never executed** (`gh issue view 8486`: *"PENDING — never
executed; no comparable number exists"*, which is its **title**), and `bm25.py`'s own constant records that
the owner armed the arm on mechanism-level evidence **with #8486 still pending** (`bm25.py:152-165`).

⚠️ **But `#8486`'s own scope cannot answer this question, and that is a finding, not a footnote.** Its body
is the **LongMemEval-S end-to-end QA benchmark** — `recall_all@5` (retrieval metric) plus **judged QA
accuracy** on a 500-question run — and it contains **no `BM25` mention and no `latency` mention** (0 hits
for each, over the body at this head; the one `corpus` hit is the dataset seal). Nothing in it varies
`DEFAULT_BM25_CORPUS_CAP` or the label's row count, so a completed run would report **answer accuracy**,
never a p95 of the corpus read. **The measurement the arm's own precondition demands is therefore
unscoped** — recorded on `#8486` as a scope question (`comment 6837663346`), not assumed to be covered by
it. M-7 inherits that: its gate is *a number nobody has scoped*, not a scheduled benchmark.

### The mechanism nobody had stated: the scorer acts on RECALL, not on order

The FTS leg does `ORDER BY score DESC … LIMIT $limit` (`search_engine.py:2993-2994`), and `rrf_fusion`
**throws the leg's score away** — the binding is literally `for rank, (pid, _score) in enumerate(ranked)`
(`search_engine.py:4492`), and the function's own comment states the rule: *"fusion consumes leg
POSITIONS"* (`:4520`). §6.2 states the same rule (`SEARCH-ARCHITECTURE.md:252-254`) but carries the **stale pins**
`search_engine.py:3021` / `:3091-3093` — at this head the fusion function opens at `:4411` and the binding is
`:4492`, so a reader must not chase those two numbers. So the
TF-IDF score never reaches the answer *as a score*: **it decides which rows survive the LIMIT.** That makes
C2 a **candidate-generation (recall) lever**, not a ranking lever — i.e. it belongs to §A of the epic, and
**M3 must measure the FTS leg's recall@k in isolation**, not only its score distribution. §6.2's word is
"re-ranks"; the sharper, actionable word is **"re-selects"**.

### Contradiction test

**No recorded decision reaches "measure the hosted corpus-read latency".** V1 gates the **index location**
(`SEARCH-ARCHITECTURE.md:§6.2`) and an in-process leg does not move the index. ⚠️ But broadly arming a
lexical arm on the product read path is exactly what M3's measurement would have to judge, so this is
declared as **`#8486`'s own question** and must not be armed on a lane's judgement — see M-7 in §7.8.

## 7.2 B2 SETTLED — name shingles, and the highest-precision non-name signal is not an overlap score

### Zep's Jaccard is over NAME SHINGLES. Confirmed from the source.

[`graphiti_core/utils/maintenance/dedup_helpers.py`](https://github.com/getzep/graphiti/blob/main/graphiti_core/utils/maintenance/dedup_helpers.py):

```python
def _shingles(normalized_name: str) -> set[str]:
    """Create 3-gram shingles from the normalized name for MinHash calculations."""
    cleaned = normalized_name.replace(' ', '')
    ...
    return {cleaned[i : i + 3] for i in range(len(cleaned) - 2)}

def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Return the Jaccard similarity between two shingle sets, ..."""
```

(`:89-96`, `:132-142`), with `_FUZZY_JACCARD_THRESHOLD = 0.9`, `_MINHASH_PERMUTATIONS = 32`,
`_MINHASH_BAND_SIZE = 4` (`:31-37`). The candidate universe for the comparison is **the LSH band buckets**
(`:357-368`), not the graph. **No edge is read anywhere in this module.** ✅ Round 1's B2 claim stands.

**The ordering is the precision property, and round 1 under-stated it.** `_resolve_with_similarity`
(`:321-380`) runs: exact-normalised (always, `:336-344`) → **if more than one node shares the normalised
name, escalate straight to the LLM and never run fuzzy** (`:345-350`) → entropy gate (`:351-354`) → LSH
fuzzy (`:356-380`, candidate universe from the LSH buckets at `:359-361`). So an **ambiguous** exact name is a *deliberate* LLM case; the fuzzy arm only ever sees
the residual.

### The highest-precision non-name signal in systems that do **entity linking** (with or without a traversable graph) is **candidate generation**, not overlap

Two mechanisms, both read from source or the paper, and neither is a pairwise neighbour-overlap feature:

**(a) Mem0, April 2026 — entity linking as a per-document BOOST.** During `add()`, entities are extracted and
stored in a parallel collection `{collection}_entities`; at search, query entities are embedded and searched
against it, and each matched entity **adds** to the score of every memory it links
(`mem0/memory/main.py::_compute_entity_boosts`):

```python
num_linked = max(len(linked_memory_ids), 1)
memory_count_weight = 1.0 / (1.0 + 0.001 * ((num_linked - 1) ** 2))
boost = similarity * ENTITY_BOOST_WEIGHT * memory_count_weight
```

with `ENTITY_BOOST_WEIGHT = 0.5` (`mem0/utils/scoring.py:57`) and the fusion **additive and normalised**,
not RRF: `combined = min(raw_combined / max_possible, 1.0)` — the docstring at `mem0/utils/scoring.py:72`
states the form **without** the clamp, the **executable** `:118-119` applies it; `:94-101` is the
`max_possible` accumulation. The
entity extractor is **deterministic spaCy-NER + heuristics**, not an LLM call
(`mem0/utils/entity_extraction.py`: `_add_ner_candidates`, `_add_proper_name_candidates`,
`_add_quoted_candidates`, `_add_topic_phrase_candidates`, `_resolve_candidates`).

⛔ **And Mem0's lexical and entity arms are RANKERS, not candidate generators:** Step 7 of
`_search_vector_store` builds the candidate set from `semantic_results` only (`mem0/memory/main.py:1680-1691`) and
`score_and_rank` re-scores that pool. A BM25-only or entity-only match that the dense leg did not return
**cannot enter the result set at all**. That is a deliberate trade (cheap, precise) with a recall cost —
⚠️ **copy the signal, not the pool construction**, because our measured ceiling is a recall problem
(`#11901`: 81 of 300 absent even at k=15).

**(b) Hindsight — the graph is one of four parallel arms, and the fusion order is RRF → cross-encoder.**
TEMPR's recall runs semantic vector, BM25, **entity/causal/temporal graph traversal**, and an explicit
temporal filter **in parallel**, fuses by reciprocal rank fusion, then **reranks the fused candidates with a
neural cross-encoder** ([ACL 2026 demo abstract](https://aclanthology.org/2026.acl-demo.27/);
[arXiv:2512.12818 **full text** §4.2.3](https://arxiv.org/html/2512.12818v1) *"Reciprocal Rank Fusion
(RRF)"* → [§4.2.4](https://arxiv.org/html/2512.12818v1) *"Neural Cross-Encoder Reranking"* — the four
channels are the four subsections of **§4.2.2**, and the paper's own order is RRF **then** cross-encoder).
⚠️ **Neither abstract carries the ordering** — the **ACL demo** abstract states the four *networks* and the
83.6 %/83.2 %/91.4 % figures, the **arXiv** abstract states four networks and 83.6 %/91.4 % (no 83.2 %, no
Gemini-3 Pro) — so the ordering has to be cited to §4.2, not to either abstract and not to a third-party
summary of them. Edges are **weighted
by decay or similarity** to bias traversal. Two things follow for us: **(i)** the graph's job is to *produce
candidates a vector leg cannot reach*, not to score pairs; **(ii)** the cross-encoder sits **after** fusion —
which is the order C1 and A6 need, and which our `rrf_fusion` + `ask_lane_rerank` already implement in the
ask path.

### ⚠️ CORRECTION to round 1's absolute

Round 1 wrote *"no surveyed system reports typed-edge neighbourhood overlap as a ranking feature"*. That is
true of the **memory systems** surveyed, and **false of ER practice**, which recommends exactly it:
*"Add graph structural similarity to your score — it is free and it is strong"*
([archtin.com/blog/entity-resolution-in-knowledge-graphs](https://archtin.com/blog/entity-resolution-in-knowledge-graphs)).
The narrow, defensible claim is: **no memory system does it; ER practice does, on a graph that has typed
edges.** Our failing store has none (`#4211`'s census on the frozen store: **484 Points, 484 edges, ALL
`CONTAINS`; `aboutObject` 0, `IMPL` 0, `NAND` 0**, read at `#4211`), so the lever is real and its substrate
is absent — which is round 1's conclusion, reached on a claim it had stated too broadly.

**Contradiction test:** no recorded decision reaches this work.

## 7.3 A3 SETTLED — the field exists (`title`), the vector is already name-only, and context belongs in the adjudicator

### `:Object` carries `title`, not just `name`

`tortoise/projection/entities.py`: `title` is in the `:Object` property list (`:1902`), written
`o.title=coalesce($title, '')` (`:5069`) and merged `o.title=coalesce($title, o.title)` (`:5087`).
`STORAGE-ARCHITECTURE.md:1317` says *"Its only text fields are `name` and `title`"* — which round 1 read as
"no context field", but **`title` is a context field**. What is unknown is its **fill rate**, and the
`coalesce($title, '')` write makes an absent title indistinguishable from an empty one at the row. **One
census settles it** (`MATCH (o:Object) WHERE o.title IS NOT NULL AND o.title <> '' RETURN count(*)`) — and
that count does not exist anywhere in this repo. See **M-8**.

### Our `:Object` vector is ALREADY Zep's shape

`_upsert_object` computes the embedding from the **name alone** —
`embedding = encode_for_row(name, self.required_embedding_dim)` (`:5013-5020`, comment *"Compute embedding
from name (#7845)"*), behind the `#5008` embed gate. Zep does exactly the same thing:

```python
class EntityNode(Node):
    name_embedding: list[float] | None = Field(default=None, description='embedding of the name')
    summary: str = Field(description='regional summary of surrounding edges', default_factory=str)
    …  # `attributes` field (nodes.py:502-504), irrelevant here

    async def generate_name_embedding(self, embedder: EmbedderClient):
        …  # `start = time()` / timing around the embedder call, elided
        text = self.name.replace('\n', ' ')
        self.name_embedding = await embedder.create(input_data=[text])
```

([`graphiti_core/nodes.py:499-511`](https://github.com/getzep/graphiti/blob/main/graphiti_core/nodes.py).) ⭐ **Zep separates the embedded key from the adjudication context**:
`summary` is a *regional summary of surrounding edges*, and it is rendered into the dedupe prompt's
`EXISTING ENTITIES` JSON (`graphiti_core/prompts/dedupe_nodes.py`) — **never embedded**.

### The minimal viable version, for a graph whose entities have no description field

1. **Keep the vector name-only.** It is the **resolution key** job (§12.2c `:1313`), and it is what both we
   and Zep already do. Putting context in the vector would answer *"does this entity's neighbourhood look
   like that entity's"*, which is not the question a dedupe rung asks.
2. **Put the context in the adjudicator payload** — one-hop neighbour names, `object_kind`, and `title`
   where non-empty. That is **B1's work, not A3's**, and it is free: the candidates are already assembled.
3. **Take the `title` census** (M-8) to learn whether (2) has a field at all or only topology.

⚠️ **This narrows A3 to almost nothing and moves its value into B1.** It does not reopen the KEEP ruling
(`#11395` `6781921075`), does not touch §12.2c's sequencing (the entity vector still waits on the extractor
for the *index* half), and does not contradict §12.2/§12.2c — an entity vector already exists and is not
being added to.

**Contradiction test:** no recorded decision reaches "put neighbour names in the adjudicator payload". The
KEEP ruling and §12.2c are **obeyed**, not reversed: nothing here removes, stops writing, or newly indexes a
vector.

## 7.4 CONFIRMED — round 1 was right, and here is the primary source

| round-1 claim | status at `9828250732` |
|---|---|
| **C1** built and wired, ask-lane only, default OFF | ✅ `tortoise/ask_lane.py:630-631` (`from tortoise.rerank import ask_lane_rerank`); `tortoise/rerank.py:66` `ASK_RERANK_ENV = "TORTOISE_ASK_RERANK"`, `:78` fail-safe-OFF gate. No noun-path caller exists. |
| **A5** `GraphRanker` two-branch dispatch sends non-`event` types to point signals | ✅ `tortoise/ranking.py:926-928`, inside `_fetch_signals` declared `:914`, `class GraphRanker` at `:677`. `class StateRanker` at `:1206`; `_fetch_object_signals` at `:1773`; the only caller `:1337` is **inside `StateRanker`**. Round 1's §6.1 correction stands; the round-1 **comment**'s `:766-768`/`:1233` were wrong. |
| **A2** an alias rung exists but has no production caller | ✅ `aliases[]` is read by `find_live_holder` (`entity_resolver.py:610-611`, declared `:580`), whose only call site is `:650` inside `resolve_entity_name` (`:632`). `apply_entity_merge` writes it (`:792`). |
| **M-1** Zep's entropy gate is an **AND**, not an OR, and does not cover our bare-definite-description class | ✅ `_has_high_entropy` (`dedup_helpers.py:80-86`): `if len(normalized_name) < _MIN_NAME_LENGTH and token_count < _MIN_TOKEN_COUNT: return False` — both needed to refuse; then `return _name_entropy(...) >= _NAME_ENTROPY_THRESHOLD`. |
| **M-2** MinHash/LSH over name shingles, 3-gram, 32 perms, band 4, Jaccard ≥ 0.9 | ✅ `dedup_helpers.py:31-37, 89-144, 293-318, 321-380`. |
| **B2** Zep's Jaccard is **blocking**, not ranking | ✅ it resolves deterministically before any LLM sees the residual (`:329-380` then `node_operations.py:660+`). |
| **D2** the read ledger must not be an admission-control signal | ✅ recorded, and **not re-litigated here** — §7 (`SEARCH-ARCHITECTURE.md`) + §17.4.6. Nothing in this section proposes it. |
| **M-4** transitive closure is the merge hazard | ✅ confirmed **externally twice**; see §7.7, which also finds the hazard is *half-built* in our tree rather than absent. |

## 7.5 CORRECTED — round-1 claims this round falsifies

| # | round-1 claim | the falsification | what it changes |
|---|---|---|---|
| **1** | *(round-1 **comment** only)* "Zep unions **FOUR** candidate sources" | `node_operations.py:_collect_candidate_nodes` (`:456-484`) unions **three**: identity-property (`:463-467`), exact-normalised name (`:468`), semantic search (`:469`) — the union itself is `:480`. LSH is a **separate, earlier, deterministic** stage. | ✅ Round 1's **doc** already corrected this (§2 A2). The comment's FOUR was never re-derived and should not be quoted. |
| **2** | "C2 — drop the 'build BM25' reading; the only open question is whether the **text index** moves" | The product lane has **no BM25 leg at all** and the vendor API **cannot express one** (§7.1). The embedded-only limit is a **latency** reason, not a decision, and its gate (`#8486`) is open. | A **second, cheaper route to BM25 ranking** exists that does not move the index (M-7). It does not overturn M3; it makes M3's *quality* half reachable before M3's *migration* half. |
| **3** | "**No** surveyed system reports typed-edge neighbourhood overlap as a ranking feature" | True of memory systems; **ER practice recommends it** (archtin: *"add graph structural similarity to your score"*). | The claim narrows to *"no memory system; ER practice does, on a graph that has typed edges"* — and our substrate census (`#4211`) is what kills it here, not the absence of precedent. |
| **4** | A3: *"name + context has **no context to embed**"* | `Object.title` exists and is written (`entities.py:1902`, `:5069`). The **fill rate** is unmeasured; `description`/`summary` = 0 rows is a different statistic. | A3 becomes **one census** away from an answer, and its remaining work moves to B1. |
| **5** | *(round-2 brief premise, and the epic's phrasing)* `#11901` carries an **explicit DECLINE** of widening the window | The record says *"**not decided, and deliberately not changed**"*, and the DoD box is **unchecked** (`#11901` comment `6826507139`). The first comment's *"do not widen now"* was **superseded** by that comment. | Round 1 stated it correctly (*"declined neither"*). ⛔ **The window question is still UN-OWNED** — no child carries the paired decision — and a lane reading "declined" would think it closed. |
| **6** | A2: *"419 real surface variants"* — ⚠️ **not a round-1 quotation: this wording appears in neither round 1's doc nor the epic's body** (which says *"0 of 419 spelling variants"*). It is a **wording guard**, kept in this table so the distinction is not lost, not a falsification of anything round 1 wrote. | The population is **GENERATED** from real names, not observed (`#11901` comment `6836274599`, the relay). | Any sentence that calls the 419 a population of *real* variants overstates it; round 1's own word (`variants`) is correct. |
| **7** | *(brief premise, not a round-1 quotation — §5 item 1 already keeps the two citations apart: the **arXiv paper** is 2025/ECAI, the **2026 report** is the April-2026 numbers)* Mem0's April-2026 algorithm is documented by **an arXiv paper** | There is **no April-2026 paper**. `arXiv:2504.19413` is **2025** (ECAI 2025) and **predates** the April-2026 algorithm; Mem0's own page says so explicitly: *"this report also covers Mem0's April 2026 … update, which **post-dates** the original ECAI 2025 paper and isn't reflected in its published numbers"* ([mem0.ai/blog/state-of-ai-agent-memory-2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026)). | Cite the **README + blog + code** for the April-2026 algorithm, and the **2025 paper** only for what the 2025 paper measured. The two must not be merged into one citation. |

## 7.6 EXPANDED — the performers round 1 never read, to mechanism level

**Mem0 (April 2026) — read from the [README](https://github.com/mem0ai/mem0/blob/main/README.md), the
vendor's [2026 report](https://mem0.ai/blog/state-of-ai-agent-memory-2026), and the code
([`mem0/memory/main.py`](https://github.com/mem0ai/mem0/blob/main/mem0/memory/main.py),
[`mem0/utils/scoring.py`](https://github.com/mem0ai/mem0/blob/main/mem0/utils/scoring.py),
[`mem0/utils/entity_extraction.py`](https://github.com/mem0ai/mem0/blob/main/mem0/utils/entity_extraction.py)).** Single-pass
**ADD-only** extraction: one LLM call, **no UPDATE/DELETE** — *"Memories accumulate; nothing is overwritten"*.
Agent-generated facts are first-class. Entity linking into `{collection}_entities`. Multi-signal retrieval
= semantic + **BM25** + entity matching, **additive-normalised** (not RRF), over a **semantic-only candidate
pool**. ⭐ **And it deleted its graph**: *"we replaced external graph store support with built-in entity
linking … The `relations` field from prior versions is gone. Entity relationships now influence retrieval
ranking but **cannot be traversed directly**"* — a stated regression for graph-interface users, traded for
deployment simplicity. Reported (⚠️ **vendor self-report**; the managed platform *"includes proprietary
optimizations not available in the open-source SDK"*): **LoCoMo 71.4 → 92.5**, **LongMemEval 67.8 → 94.4**,
**~6.8–7.0 K tokens per retrieval call** — where `71.4`/`67.8` are **Mem0's own previous algorithm**, the
baseline column of the *same* table (not OpenAI and not full-context; the 2025 paper's OpenAI comparison is
a different measurement). ⚠️ **The two citations stay split, per §7.5 #7:** the **README's `Old`/`New` table**
carries these deltas (the *2026* report carries the new scores and ~6,900 tokens/query); `arXiv:2504.19413`
(2025) **does not** — Mem0 states the algorithm post-dates it.
→ **Lesson for us:** the strongest evidence in the field that **entity-keyed boosting without traversable
edges** buys most of the graph's retrieval value — which is an argument for B2-as-*candidate-generation* and
against B2-as-*edge-overlap-score*.

**Cognee — read from its docs
([docs.cognee.ai/python-api/cognify](https://docs.cognee.ai/python-api/cognify),
[cognee.ai/grounding-ai-memory](https://www.cognee.ai/grounding-ai-memory)) and its
[changelog](https://docs.cognee.ai/changelog).** Identity is a **function of the name**, per the changelog
verbatim: *"Entity ids change: they are now derived from `Entity.id_for(node.name)` rather than
`Entity.id_for(node.id)`, so two chunks mentioning the same entity name converge on one node, while several
distinct nodes sharing a name inside a single extracted graph keep deterministic chunk-scoped ids instead of
collapsing together"* — i.e. same-name nodes inside one extracted graph get **chunk-scoped** ids rather than
merging. ⚠️ The sentence this brief first quoted in quotation marks (*identity derived from the `name`,
lowercased, spaces → underscores, apostrophes stripped*) is **a paraphrase**, not a quotation: it composes
`_normalize_identity_value`'s docstring (`cognee/infrastructure/engine/models/DataPoint.py:188`, *"Normalize
a single identity value (lower-case, spaces→_, strip apostrophes)"*) with `id_for`'s behaviour (`:200-216`).
It is cited there, not quoted from the docs.
Optional **OWL ontology validation** canonicalises LLM-derived names to
URI-derived forms via `difflib.get_close_matches()` at a **0.80** cutoff and injects `rdfs:subClassOf` /
`owl:ObjectProperty` structure. An opt-in `PROVENANCE_TRACKING` audit ledger appends *"an audit-ledger entry
for every document, chunk, entity, and relationship this run produced"*
([docs.cognee.ai/python-api/cognify](https://docs.cognee.ai/python-api/cognify), off by default).
⚠️ Its own documented migration hazard ([changelog](https://docs.cognee.ai/changelog)): *"Entity nodes written
by earlier runs keep their old id-derived ids, so re-cognifying data that is already in the graph can create
a second node for an entity that is already there — re-run the affected datasets from scratch if you need ids
to line up."*
→ **Lesson for us:** Cognee makes duplicates **structurally impossible** by folding identity into the id.
We did the opposite deliberately — `#3590` landed `merge_key = "id"` — so similarity-based candidate finding
is the only mechanism left (which is exactly the KEEP ruling's premise). ⛔ **Not a candidate: it would
reverse `#3590`.** It is evidence for *why our problem exists*, not a fix for it.

**Honcho — read from its [README](https://github.com/plastic-labs/honcho/blob/main/README.md),
[`CLAUDE.md`](https://github.com/plastic-labs/honcho/blob/main/CLAUDE.md) and
[reasoning docs](https://honcho.dev/docs/v3/documentation/core-concepts/reasoning).** Entities are **declared, not extracted**:
"peers" (humans and agents) are first-class records, and there is no name-matching rung anywhere. Storage
is PostgreSQL + pgvector; representations live in **collections keyed by `(observer, observed)` peer
pairs**, which is how it models per-observer knowledge asymmetry. A background **Deriver** is *"a single LLM
call per batch using structured output, not an agentic tool loop"*; a **Dreamer** consolidates conclusions
into reasoning trees on a schedule; retrieval includes **hybrid BM25 + vector** search.
→ **Lesson for us:** for the entity-resolution problem, Honcho is a *null case* — it has no duplicate-noun
class because it never mints an entity from prose. It is only usable as evidence that resolution is a
**write-path** decision, not a read-path one.

**Supermemory — read from its docs
([supermemory.ai/docs/concepts/how-it-works](https://supermemory.ai/docs/concepts/how-it-works),
[supermemory.ai/docs/concepts/graph-memory](https://supermemory.ai/docs/concepts/graph-memory)).** The knowledge graph is **facts on facts, not entity-relation-entity
triples** (*"not classic entity–relation–entity triples you maintain by hand"*): nodes are memory entries;
edges are `updates` / `extends` / `derives`. A second phase, **"dreaming"**, forms memories from *coherent
units* rather than per-write (`dreaming: "dynamic"` by default; `"instant"` for demos) — i.e. deferred
consolidation, like Letta's sleep-time compute. Forgetting **excludes memories from search while preserving
their records** — its own words: *"Forgotten memories are excluded from search results but preserved"*
([supermemory.ai/docs/recall/memory-operations](https://supermemory.ai/docs/recall/memory-operations)).
Retrieval is **three documented options, not one composite**: *"`searchMode: "hybrid"` is the default"*
(memories + document chunks), `rerank: "order" | "aggregate"` re-scores the result set, and
`include: { related: true }` adds graph-edge expansion
([supermemory.ai/docs/recall/search](https://supermemory.ai/docs/recall/search)).
→ **Lesson for us:** two of the "graph" performers (Mem0, Supermemory) have **no entity nodes in the
retrieval graph at all**. Entity resolution is not a prerequisite for a top score; it is a prerequisite for
*our* semantics (identity, supersession, EP over a closed component).

**Letta/MemGPT and Mastra:** round 1's §5.1 readings — Mastra =
[mastra.ai/research/observational-memory](https://mastra.ai/research/observational-memory), Letta =
[letta.com/blog/sleep-time-compute](https://letta.com/blog/sleep-time-compute) — are **inherited, not
re-verified this lane** — declared
in §7.9. Mastra's observability route remains the counter-case for the whole retrieval-centric inventory;
Hindsight (§7.2, item (b); 91.4 % LongMemEval with Gemini-3 Pro per its ACL demo paper —
[aclanthology.org/2026.acl-demo.27](https://aclanthology.org/2026.acl-demo.27/), which also carries the
83.6 % figure for its own open-source 20B model) is the counter-case to
*"Mem0 deleted its graph because graphs don't help"*.

### The cross-cutting finding, which no single row shows

**At the top of the board, entity identity is DECLARED or DERIVED, not MATCHED.** Mastra: no entity layer.
Honcho: peers are records. Mem0 (Apr 2026): ADD-only, nothing merged. Supermemory: facts, not entities.
Cognee: `id = f(name)` by construction. **The only surveyed system that runs a real fuzzy entity matcher
(Zep) resolves the mention onto an existing node *before persistence*, so no persisted identity link can
chain.** Our system does persist a redirect pointer — which is *more* recoverable than Zep's and *more*
exposed than Zep's to the transitive hazard. §7.7 is that trade, priced.

## 7.7 Merge chains and reversibility — what the field does, and what we already have

### The hazard, stated externally (two independent sources, not our record)

- Connected-components clustering *"is transitive by construction"*, so **"A matches B and B matches C puts
  A and C in the same entity even where A and C were compared and rejected. The pairwise model's own verdict
  on the A-C pair is discarded."** And the cost is super-linear: *"a single false-positive edge between two
  otherwise-correct clusters of 50 records produces one 100-record 'customer', and the badness of the outcome
  scales with the size of what it joined, not with the number of mistakes."*
  ([erikevenson.github.io/architect/patterns/entity-resolution](https://erikevenson.github.io/architect/patterns/entity-resolution/))
- *"Pairwise decisions are not transitive … Naive union-find over all matched pairs is how you get one giant
  blob node containing 40,000 'entities' — the classic transitive-closure explosion."*
  ([archtin.com](https://archtin.com/blog/entity-resolution-in-knowledge-graphs))
- *"Record **which derived claims, embeddings, summaries, and permissions depended on the old cluster** so
  they can be invalidated"* — ([granveo.com](https://www.granveo.com/blog/resolve-entities-without-corrupting-knowledge-graph))

### The controls the field actually uses

Sources, so each row is attributable: **erikevenson** =
[erikevenson.github.io/architect/patterns/entity-resolution](https://erikevenson.github.io/architect/patterns/entity-resolution/)
· **archtin** = [archtin.com/blog/entity-resolution-in-knowledge-graphs](https://archtin.com/blog/entity-resolution-in-knowledge-graphs)
· **granveo** = [granveo.com/blog/resolve-entities-without-corrupting-knowledge-graph](https://www.granveo.com/blog/resolve-entities-without-corrupting-knowledge-graph)
· **writeitai** = [github.com/writeitai/remember-stack — entity_registry.md](https://github.com/writeitai/remember-stack/blob/main/plan/analysis/entity_registry.md)
(⚠️ a **third-party design note**, not a vendor primary — its Senzing/Wikidata characterisations are quoted
from it, not verified against Senzing's or Wikidata's own documentation this lane).

| control | source | do we have it? |
|---|---|---|
| **Maximum cluster size**, anything above it routed to **review**, not published | erikevenson; archtin (*"a cluster of 500 people is a bug, not a discovery"*) | ⛔ **NO** |
| **Blast-radius rule** — never auto-merge above a degree/evidence threshold | writeitai/remember-stack; granveo | ⛔ **NO** |
| **Persisted no-merge assertions**, honoured by the clustering step so an un-merge is not a treadmill | erikevenson (*"a steward's 'these are not the same' must be persisted"*) | ✅ **YES** — `dedup_target_id` / `dedup_rejected_targets` are read by `_ENTITY_READ` (`entity_resolver.py:203-214`) |
| **Merge = redirect, not rewrite** (Wikidata QIDs never reused; a dead ID resolves to the survivor forever) | writeitai; and the SAME_AS pattern | ✅ **YES** — see below |
| **Un-merge is deleting one edge** and re-clustering, not a recovery exercise | archtin (*"Unmerging is deleting one `SAME_AS` edge and re-clustering"*) | ✅ **structurally yes**; ⛔ **but nothing in-tree issues it** — `rg 'resolved_from'` over `tortoise/` finds **one write** (`apply_entity_merge`) and **reads only** (the `_ENTITY_READ` predicate at `:199-214`, `follow_resolved_from` with its **four** call sites — `entity_resolver.py:624` (inside `find_live_holder`) and `:933` (the `holdsRole` holder path), plus `entity_identity.py:384` and `:594` — and the independent chain walk in `tortoise/onboarding/seed.py:306-315`); there is **no clear/REMOVE/reverse path at all** |
| **`score`, `method`, `decidedAt` recorded ON the link** so *"why are these one customer"* is one query | archtin (*`SET r.score=$score, r.method=$method, r.decidedAt=datetime()`*); "Senzing — every merge records *why*" is **writeitai's** characterisation, quoted not verified | ⛔ **NO — `method`/`similarity` reach the return value only (`entity_resolver.py:809-811`); `score`/`decidedAt` exist nowhere** |
| **Append-only decision rows + pre-merge membership snapshot** (what makes un-merge possible) | writeitai; granveo | ⚠️ **partly** — the journal gives replay (`derived == replay(journal)`), but there is no pre-merge *membership* snapshot |
| **Cluster-size distribution** as a health metric (*"emerging giant cluster = over-merge in progress"*) | writeitai | ⛔ **NO** |
| **Three outcomes** — same / different / **unresolved** | granveo | ✅ the review queue + `dedup_reviewed` exist |

### What our tree already has — verified, and better than most of the field

`apply_entity_merge` (`tortoise/entity_resolver.py:728-812`) is a **non-destructive, journal-backable
redirect**, not a collapse: the survivor gains the candidate's folded name in `aliases[]`, the candidate is
stamped `resolved_from` + `dedup_reviewed='merge'` + `reviewed`, and — verbatim from its docstring —
*"Both nodes and all their edges survive."* Both writes route through `update_entity`, which journals an
`EntityMutated` record, *"so `derived == replay(journal)` holds for the merge: a rebuild restores the alias
and the resolution pointer."* `follow_resolved_from` (`:536-577`) walks the pointer chain to a **fixpoint
with a cycle guard** (`seen`), and `_ENTITY_READ` excludes `resolved_from IS NOT NULL` so a retired carrier
is never re-elected as a survivor. ⚠️ **And the redirect is load-bearing beyond the resolver:** `entity_identity.py`
follows it on the identity path too (`:378-384`, `:593-594`), so a chain that is wrong is not a
resolver-only defect — it is walked by identity reads.

⭐ **This is the Wikidata shape** — a merge is a redirect, the pre-merge records survive, and un-merge is
"remove the pointer" — and it is **more reversible than Zep's**, which needs no reversal because it never
persists a merge.

### What we do NOT have, and it is cheap

1. **No evidence on the link.** `apply_entity_merge` **returns** `method` and `similarity` (`:808-810`) but
   **writes neither** — the persisted properties are `resolved_from`, `dedup_reviewed`, `reviewed`, and
   `canonical_name` when it was empty. So *"why are these one entity"* is **not answerable after the fact**,
   and the caller's `method`/`similarity` live only in whatever the caller logged. → **M-9**.
2. **No blast-radius or chain-length bound.** A merge is accepted on a pairwise verdict; nothing checks the
   **closure** it creates. `follow_resolved_from` silently collapses an arbitrarily long chain to its
   fixpoint instead of reporting that a chain existed. → **M-10**. This is the field's #1 control and we have
   none of it, which makes round 1's M-4 finding *more* urgent, not less: the danger here is not an
   irreversible destructive merge (we do not have one) — it is an **unaudited, unbounded redirect chain**.
3. **A doc/code divergence a reviewer will trip on:** `follow_resolved_from`'s docstring says *"A **single
   hop**, cycle-guarded by the `seen` set"* (`:547`) while the body is a `while True` loop that keeps
   following (`:556-577`). The code is right; the sentence is wrong, and it is the sentence a reviewer reads
   before deciding whether a chain can exist.
4. **No `SAME_AS` predicate** — unchanged from round 1: `_VALID_EDGE_PREDICATES`
   (`tortoise/projection/edges.py:717-726`) carries no `sameAs`. So B1's third band still cannot be written
   as an edge; the only in-tree carrier of that link is **this redirect** — but ⚠️ **the redirect IS the
   merge, not a link.** (§7.7's own control table already records *“Merge = redirect, not rewrite”*.) A
   `resolved_from` write retires the node in every reader: `_ENTITY_READ` excludes it
   (`entity_resolver.py:207`), `find_live_holder` hops it (`:624`), and `entity_identity` collapses it on the
   identity path (`:589-594`). `dedup_reviewed='link'` would be **read by nothing** — the tree reads only
   `'merge'` (`entity_resolver.py:227`, `:694`, `sdk.py:23664`, `:23674`) and `'reject'` (`entity_resolver.py:229`, `sdk.py:24557`) — so the write
   would be an unlabelled merge, not a link-without-merge band. ⛔ **N-4's dispatch readiness is therefore
   unchanged**: the mid band still needs the `SAME_AS` edge (the 2730 owner question) or a distinct read path
   for a linked-not-merged pair. Declare that, do not assume a redirect buys it —
   the 2730 brief's owner question (`:241`) is an ontology **`scope` property on `:Object`**, with `sameAs`
   named there as one of three identity primitives (`isPlaceholder`/`sameAs`/`scope`). A `resolved_from`
   redirect needs no ontology decision — which is exactly why it cannot be the link band: the decision the
   band is blocked on is the one that would make a *link* distinguishable from a *merge* in the first place.

**Contradiction test:** nothing here reverses the KEEP ruling, §12.2c, V1 or §2.0. M-9/M-10 obey the
non-destructive-merge rule (`#2730` §6.7.3, `#783` J-2) rather than revising it.

## 7.8 The levers no inventory lists yet

### M-7 ⭐ **Arm the BM25 leg on the HOSTED lane — the non-index route to C2**

The in-process Okapi BM25 leg exists, is default-ON, and is gated to `is_embedded` for a stated
**latency** reason (a ≤`DEFAULT_BM25_CORPUS_CAP` = 20 000-row corpus read tokenised in Python outside
`degradation_chain`'s deadline — `search_engine.py:3103-3108`). Its own recorded precondition for wider
arming is **`#8486`**, which is OPEN, **never executed**, and — §7.1 — **not scoped to this number**
(the issue is the LongMemEval-S QA benchmark).
**Why it matters:** it reaches BM25 *ranking* on the product lane **without moving the text index**, so it
does not touch V1's decision and does not wait on M3's migration. It is the cheapest path to the quality half
of C2, and the inventory has no row for it because C2 was read as "the index move".
**Falsifier:** the corpus read's p95 on a production-sized label exceeds the read deadline (or the row count
exceeds the cap on the labels that matter) → the lever is unaffordable and M3's index move is the only route.
⛔ **Do not arm it on a lane's judgement.** A lane may **measure**, not decide — but ⚠️ **`#8486` is not that
measurement** (§7.1, `#8486` `comment 6837663346`): the gate has to be the corpus-read p95, and no scoped
artifact holds it yet. An arming decision taken on "`#8486` is still open" without that number is the same
fail-open the gate exists to prevent.

### M-8 ⭐ **The `title` census** — one query that decides whether A3 has a context field at all

`MATCH (o:Object) WHERE o.title IS NOT NULL AND o.title <> '' RETURN count(*)` (+ the same over `:Subject`).
**Why:** §7.3 shows `title` is the only candidate context field on the label, and its fill rate is unknown
because the writer `coalesce`s to `''`. **No artifact in this repo holds this number.** Until it exists,
"name + context" is neither buildable nor refutable.
**Falsifier:** near-zero non-empty → `title` is a dead slot, A3's context arm reduces to topology (neighbour
names), and the extractor supplies the only real context. Cost: one query per graph.

### M-9 **Stamp the merge link** — `method`, `similarity`, `decidedAt` (and the actor, where one exists)

`apply_entity_merge` already receives `method` and `similarity` and throws them away; `decidedAt` (and the
actor, where one exists) is **not computed anywhere in the function** — `update_entity`'s own write is the
natural timestamp. Three properties on the redirect make
*"why are these one entity"* a one-query answer (the field's own test, archtin/Senzing) and make an un-merge
auditable. Cost: ~5 lines in a function that already validates its inputs.
**Falsifier:** a reviewer cannot show a case where the stamp changes a decision → it is decoration; drop it.

### M-10 ⭐ **Blast-radius / chain-length guard on the merge closure**

Before an `apply_entity_merge` is accepted, compute the closure it joins (both endpoints' existing chains and
fan-in) and **refuse or route to review** above N — and *report* the chain length that `follow_resolved_from`
would walk rather than collapsing it silently. This is the single control the ER literature names as the
transitive-closure mitigation, and §7.7 shows we have none of it.
**Falsifier:** no `resolved_from` chain longer than 1 exists on any real graph → the guard is inert, keep the
reporting half and drop the refusal. ⚠️ Cannot be measured on this box (§7.9).

### M-11 **Entity-boost over the UNION candidate set** — Mem0's signal without Mem0's pool

Mem0's entity boost is a good signal applied to a **narrow** pool (semantic-only, `mem0/memory/main.py:1680-1691`). Ours
would be a **per-document additive boost** over `rrf_fusion`'s union — so a BM25-only or entity-only match can
still surface. ⚠️ **Deliberately NOT Mem0's construction:** copying the pool would deepen the recall problem
`#11901` measured, and §7.2's ⛔ warns exactly there.
**Falsifier:** the boost does not change ranking on the `#11901` fixture beyond its own weight →
`ENTITY_BOOST_WEIGHT` is doing the work and the entity collection is not earned.

## 7.9 What this lane could NOT establish (declared, never skipped)

1. **No graph census was possible.** This box serves no tenant for these populations, so `Object.title` fill
   rate (M-8), `resolved_from` chain lengths and cluster sizes (M-10) are **declared and unmeasured**. Every
   one of them is one Cypher query away on a graph that serves them.
2. **`#8486` is unexecuted, and it is not scoped to the number M-7 needs.** The hosted BM25 corpus-read
   latency has **no number**, and `#8486` — the issue the code names as its gate — is the LongMemEval-S QA
   benchmark (0 `BM25`/`latency` mentions in its body, §7.1), so running it would not produce one. This lane
   did not run it (that is a benchmark, not a research read) and **cannot close the gap by citing it**. The
   scope question is recorded on `#8486` (`comment 6837663346`).
3. **There is no April-2026 Mem0 paper** (§7.5 #7). The April-2026 algorithm is documented by README/blog/code
   and is **not** reflected in `arXiv:2504.19413`; Mem0 says so itself. Any citation saying otherwise is
   wrong.
4. **Every performance figure in §7.6 is its vendor's**, on its own harness: Mem0 (LoCoMo 92.5 / LongMemEval
   94.4 — and *"proprietary optimizations not available in the open-source SDK"*), Hindsight (83.6 % @20B,
   91.4 % with Gemini-3 Pro — ACL demo paper + vendor board), Mastra (round 1's reading). None is comparable
   to `#11901`'s 61.3 % / 73.0 %, which is **candidate recall@k on our capture corpus**.
5. **Letta/MemGPT and Mastra are inherited from round 1's §5.1**, not re-verified at this head. Cognee,
   Honcho, Supermemory and Mem0 were read to **primary sources** this lane (vendor docs, repo code, README).
   **Hindsight is a split reading and is not claimed as primary throughout**: the arXiv **abstract** + the
   ACL demo abstract were read, **and** the paper's full text §4.2.3–§4.2.4 for the RRF → cross-encoder
   order; the headline figures are **the abstracts'**, not a re-run — 83.6 % @20B and 91.4 % are in both, the
   **83.2 %** is the ACL demo abstract's only (which is also where "with Gemini-3 Pro" is spelled out). ⚠️ A
   summary site (`emergentmind`) was in the first pass of this claim and is **not** a
   primary source — §7.2 now cites §4.2 directly, and no claim in §7 rests on the summary.
6. **The 8–23 nDCG@10 Postgres-full-text-vs-BM25 gap** is inherited from `SEARCH-ARCHITECTURE.md:§6.2`; this
   lane did not re-read the underlying measurement. It does not need to: §7.1's C2 resolution does not rest
   on it.
7. **`#4211`'s census (484 Points / all `CONTAINS` / `aboutObject` 0)** is cited from `#4211` itself, not
   re-measured here.
8. **Zep's LLM dedupe quality is unmeasured anywhere** — no isolated number exists, in its repo or its paper.

## 7.10 Contradiction test — one row per finding in §7

| finding | recorded decision it would reverse | verdict |
|---|---|---|
| C2: product lane is TF-IDF; vendor has no scorer option | none — it **confirms** `SEARCH-ARCHITECTURE.md:§6.2` | ✅ candidate (informational; it sharpens M3) |
| C2: the scorer acts on **recall** (LIMIT) not order (RRF discards score) | none — M3's scope already says "score distributions"; this adds a metric | ✅ candidate (it narrows M3 to a measurable question) |
| **M-7** arm the BM25 leg on hosted | **V1 does not reach it** (V1 gates the index *location*); the code names `#8486` as the latency precondition | ⚠️ candidate **only through the corpus-read p95** — which `#8486`, as scoped, does not produce (§7.1) — a lane may measure, not arm |
| B2: Zep's Jaccard is name shingles | none | ✅ confirmed, not adopted |
| B2: the graph's job is candidate generation / per-doc boost | none | ✅ candidate |
| **A3: context belongs in the adjudicator, not the vector** | **obeys** §12.2c (`:1319`, `:1330`) and the KEEP ruling (`#11395` `6781921075`); adds no vector, removes none | ✅ candidate |
| **M-8** `title` census | none | ✅ candidate (a measurement, not a change) |
| **M-9** stamp `method`/`similarity`/`decidedAt` on the merge redirect | none — obeys `#2730` §6.7.3 / `#783` J-2 (non-destructive merge) | ✅ candidate |
| **M-10** blast-radius guard | none — it **strengthens** the KEEP ruling's purpose | ✅ candidate |
| **M-11** entity boost over the union | none | ✅ candidate |
| **B1's link band as a `resolved_from` redirect, not a `SAME_AS` edge** (§7.7 item 4) | none — but it reverses nothing and **delivers nothing either**: `resolved_from` is the merge pointer in every reader (`entity_resolver.py:207`, `:624`, `entity_identity.py:589-594`) | ⛔ **not a candidate as stated** — a `resolved_from` write is the merge, so it cannot carry the link band |
| Cognee's `id = f(name)` identity | ⛔ **reverses `#3590`** (`merge_key = "id"`) and the KEEP ruling's premise | ⛔ **not a candidate** — evidence for a reopen only, argued at `#3590` |
| Supermemory/Honcho/Mastra "no entity graph" | ⛔ would reverse §12.2c's entity-vector work and the KEEP ruling | ⛔ **not a candidate** — and note two of the three are *repository*-shaped systems, not identity-bearing ones |
| "Widen the window" | `#11901`'s record says *not decided*; a wider window without a verdict is X7's known regression | ⛔ **not a candidate** until the paired decision is taken |
| Any use of `read_ledger` as an admission-control signal | ⛔ §7 + §17.4.6 | ⛔ **not a candidate** — nothing here proposes it |
| Row eviction / hot-cold row tiering | ⛔ hosted FalkorDB shows no eviction **as far as this repo can establish**; the unit is a projection-time window (`STORAGE-ARCHITECTURE.md:188` — per-graph LRU offload is documented for **Enterprise self-managed** and *"not exposed on Cloud as far as we can establish"*) | ⛔ **not a candidate** |

---

**Refs:** `#12186` (epic) · `#12189` (round 1) · `#11901` (measurement of record; DoD comment
`6826507139`) · `#11395` (KEEP ruling, `6781921075`) · `#4211` (census) · `#8486` (BM25 latency benchmark,
OPEN) · `#2518` · `#2513` · `#12184` · `#3590` (`merge_key = id`) · `SEARCH-ARCHITECTURE.md` §6.2/§7 ·
`STORAGE-ARCHITECTURE.md` §12.2c/§14.3 V3/§17.4.6 · `docs/research/2026-09-24-2730-entity-attachment/research-brief.md`
