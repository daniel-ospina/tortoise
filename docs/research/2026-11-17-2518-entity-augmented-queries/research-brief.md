---
title: "Entity/fact-augmented query expansion, measured against the class it was dispatched to attack (#2518)"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-17
updated: 2026-11-17
subjects.team: epistemic-team
issue: "#2518"
aboutSubjects: capture-time link-before-create prior, entity/fact-augmented query expansion, sparse OR-query construction, `_`-composite OR terms
aboutObjects: "#11901 candidate-recall instrument, #12295 multi-key blocking, #12360 underscore composite tail, epic #12186 lever A4, SEARCH-ARCHITECTURE.md V1, STORAGE-ARCHITECTURE.md §12.1c / §14.3 V3"
---

# Entity/fact-augmented query expansion, measured against the class it was dispatched to attack

**Issue:** [#2518](https://github.com/daniel-ospina/tortoise/issues/2518) · **Parent epic:**
[#2513](https://github.com/daniel-ospina/tortoise/issues/2513) · **Epic whose inventory row this
closes or re-states:** [#12186](https://github.com/daniel-ospina/tortoise/issues/12186) lever **A4** ·
**Harness:** `tools/candidate_recall_11901.py` (reused, not rebuilt) · **Code head:** `8762578f8c70`
(product code; the REPEAT arm ran at `ade889b487d4` and the composite-off arm at `edf7959b8e37`,
both docs/test-only commits) · **Measured:** 2026-11-17, on the `cmux` cell.

**Verdict in one paragraph.** The dispatch names a class — *"9.3 % of real nouns (28 of 300) are not
found at ANY window"* — and asks this lane to attack it with additive entity/fact-augmented
expansion. **The class is empty at this head in the production read shape, and it was emptied by two
deterministic levers that are already on `main`, not by expansion:** with the hybrid read **and** the
shipped `_`-composite tail, **`miss_absent_at_max_k` = 0/300 at every k** and recall@3 is
**298/300 = 99.3 %**; with the lexical window alone and the tail on, **0/300 absent**; with the tail
**off** — the pre-`#12360` shape the dispatch's baseline was taken on — **20/300 absent hybrid** and
**82/300 absent lexical**. The two levers divide the work cleanly: the **dense leg removes 62 of the
82** (the mention *is* the stored name, so its own name vector ranks it in) and the **`_`-composite
OR tail removes the remaining 20 hybrid / all 82 lexical**. Expansion is therefore not measurable on
this class — and the mechanism `#2518` specifies (`_entity_key_alias_harvest` → `search_keys`) acts on
the **point** leg while the instrument's arm measures the **object** leg. **No product code is changed
by this lane**; the deliverables are the measurement, the characterisation, and the two decisions the
numbers support (§6). The `TORTOISE_ENTITY_KEY_DENSE` / `entity_key_expansion` switches keep their
shipped posture (OFF / caller-gated).

---

## 0. Preflight — what actually ran

**Search tool (rung resolved):**

- **Rung 1** `mcp__seo-intelligence__perplexity_search` — **not reachable from this agent**: the lane's
  tool surface exposes no MCP client (`mcp_load` is not present), so the rung cannot be loaded.
- **Rung 2** `web_search` — **not exposed** to this agent either.
- **Rung 3 + the credentialed Perplexity API directly**, model named explicitly on every request
  (`"model": "sonar"`; never `sonar-deep-research`, never `sonar-reasoning-pro`), key read from
  `/home/cmux/keys.env` at call time. **Queries: 2 attempted / 2 returned.** No 429, no truncation.
  The lane's earlier work on this bench (`#12295`, `#12360`) hit `Unknown MCP server
  'seo-intelligence'` and `PERPLEXITY_API_KEY not set`; on this cell the key is present in the shell
  environment and the direct route works.

**Graph (Step 0.0b):** `~/.pi/agent/scripts/tortoise-graph.sh search` → **`GRAPH-UNREACHABLE(TimeoutError)`**
on query 1, then `GRAPH-TOOL-ERROR(tortoise_search): the server's wait budget … exceeded` on query 2.
Per the skill's own rule a wait-budget failure means **the query did not run**, so the disposition is
`unread — no query ran`, **not** "nothing held": **2 queries attempted / 0 returned.** The raw REST
recipe in the skill could not be used as written either — it is routed through `zsh`, and **`zsh` is
not installed on this cell** (`zsh: command not found`); the wrapper was used instead, and
`TORTOISE_MCP_API_KEY` *is* visible to this bash shell (the skill's "bash sees neither variable" note
is stale here).

**Mandate preflight, reported rather than papered over (agent-infra#3377):** two files the harness
expects a lane to read do not exist on this box and have no provisioning path —
`/home/cmux/.pi/agent/state/LANE-BRIEF-PREAMBLE.md` and
`/home/cmux/.pi/agent/state/TORTOISE-GRAPH-BRIEF.md`. The dispatch H's own text was used as the
authority; the skill table in `AGENTS.md` that orders a lane to read the graph brief **could not be
obeyed**. This is named here because the report should not claim a read that did not happen.

**Box, stated because the brief asks for a load-stated box:** 16 cores, 31 GB RAM; load average
**33.2** at start, **4.4** when the hybrid arm finished; each run's wall clock is in its artifact
(`corpus.elapsed_s` for the harvest; the hybrid sweep dominated the run). No OOM kill, no signal.

---

## 1. Pass 1 — the internal canon: what a query is built from today

**The measured path, end to end (all cited by symbol, never by line — this tree moves).**

| step | where | what it does |
|---|---|---|
| derive | `tortoise/extractor_v2.py::_derive_queries` | one `object` query per extracted entity name, plus `event`/`point` queries from event/point content and story topics. **No `subject` query is emitted**, and the `object` queries are the ones `resolve_entities` is handed. |
| sanitise | `extractor_v2._sanitise_query_text` → `_candidate_query_text` | strip, drop `word:`, strip punctuation, collapse whitespace, cut to 160. The **stored** and **run** forms are the same string — `_query_key` keys on it. |
| look up | `extractor_v2._fts_rows` → `sdk.tortoise_fts_query(entity_type="object", limit=3, include_terminal=True, _record_reads=False, _valid_time_rerank=False)` | the fused read. |
| build the OR union | `tortoise/sparse.py::build_or_query` | `tokenize_sparse_query` → sort by length DESC → cap at `DEFAULT_MAX_OR_TERMS` → join with `\|` (RediSearch OR). **Additive tails, in order:** the A4 PRF expansion tail (`expansion_terms`, ≤ 8 terms), the `#8933` identifier aliases + adjacency phrases, and the `#12360` `_`-preserving composite runs (`underscore_run_terms`). Degenerate inputs (empty / stopword-only / single token) fall back to a backslash-escaped **literal**. |
| fuse | `tortoise/search_engine.py::degradation_chain` → `rrf_fusion` | the fts / vector / structural legs run concurrently under one deadline; RRF fuses by rank with `{"vector": 1.5}` and the lexical weight bounded below it. |
| accumulate | `extractor_v2.search_graph` | **every** derived query's rows land in ONE id-keyed dict — so the candidate set is the union over the derived object queries, never `limit`. |
| bind | `extractor_v2._find_existing_entity` | normalised-name equality (`_norm`) plus, since `#12295`, the comparison-side fold (`entity_blocking.attach_matches`). |

**What a query is built from today:** the mention's own sanitised text → its content tokens → plus
three *additive, deterministic* tails (PRF aliases when the ask lane arms them, `#8933` identifier
aliases/phrases, `#12360` `_` composites). **Nothing from the graph.** No entity, no fact, no
`search_keys`, no ontology term is consulted when the capture prior asks its question.

**What an expansion would additionally build it from, and where that already exists in code.**
`#2518`'s own mechanism is **built and merged** — `PR #2557` (sparse) and `PR #11459` (dense parity):

| piece | seam | what it consumes |
|---|---|---|
| the harvest (shared by both halves) | `sdk.SDK._entity_key_alias_harvest` | **step 1** an Object-name FTS anchor resolution over the query text (≤ `_ENTITY_ANCHOR_CANDIDATES` = 20 fetched, ≤ `_ENTITY_ANCHOR_LIMIT` = 3 kept, stable `(score desc, id asc)`); **step 2** ONE batched Cypher harvest — each anchor's `name` **plus the `search_keys` of every `:Point` linked `-[:aboutObject]->` to it, across all sessions** (the `aboutObject` spine join, `ONTOLOGY.md` §2.2's typed `schema:about` edge). Pool sorted, deduped, capped at `_ENTITY_ALIAS_MAX` = 200 (≤ `_ENTITY_ALIAS_PER_ANCHOR` = 64 per anchor). |
| the sparse half | `sdk.SDK._entity_key_expansion_pass` | `run_fts_query(..., expansion_terms=aliases)` — **one** extra fts pass; merged additively (expanded rows first, pass-1-only ids appended; no id dropped). |
| the dense half | `sdk.SDK._entity_key_dense_pass` | **one** extra embed of a composed query (≤ `_ENTITY_DENSE_ALIAS_CHARS` = 240 chars from ≤ `_ENTITY_DENSE_ALIAS_TERMS` = 32 alias terms) → **one** extra vector-leg query, merged additively. Armed separately by `TORTOISE_ENTITY_KEY_DENSE` (default OFF). |

Cost bound, stated as a bound and not a measurement: **per read**, ≤ 1 anchor FTS + 1 batched
harvest + ≤ 1 extra fts pass + ≤ 1 extra embed + ≤ 1 extra vector-leg query, all fail-open (every
failure keeps the original legs byte-identically; the pool is capped before tokenisation). **No new
writer embeds anything** — the mechanism reads `search_keys`, which E3 (`#1535`) already writes, and
`aboutObject`, which the capture spine already writes. **No register entry is needed and none was
made**; no MCP/SDK surface is touched.

**⛔ What the code says that the dispatch's premise does not: the mechanism is POINT-only by design.**
`_entity_key_alias_harvest` harvests **`:Point`** `search_keys`, and the caller arms both halves only
under `entity_type == "point"` (`sdk.tortoise_fts_query`: `if (entity_key_expansion and entity_type ==
"point" …)`). The reason is in that call site's own comment: expanding an `object`/`event`/`operator`
arm would merge **point** ids into an object top-k. The eval instrument's arm 1 asks
`sdk.tortoise_fts_query(..., entity_type="object")` — the **object** leg. **So the shipped expansion
cannot move the metric the dispatch names, by construction, not by degree.**

### Contradiction test — nothing adopted over a decision

- **V1 / PR `#11481`** (where the vectors live) — untouched: nothing here moves, adds or removes a
  vector store.
- **§14.3 V3 / `#11901` / `#13108`** (no vector index) — untouched: **no index is created**, and the
  harness's own `#4997` note fires on every hybrid run (`vector search … is unindexed for
  label(s) ['Event','Object','Source','Subject']`, `tortoise/projection/__init__.py:20091`) which this
  lane notes and does not act on; it is emitted to the run's log, not carried into the artifact.
- **Owner ruling 2026-11-10** (`:Object`/`:Subject` keep their embeddings; removal refused) —
  untouched: no pass stops writing or removes an embedding, and the ruling is **not stretched** to
  `:Event`/`:Source`/`:Document`.
- **`#11395` KEEP ruling, `#8487`'s capture-path ordering ruling** — untouched.
- **Ontology vocabulary** — the expansion consumes `aboutObject` and `search_keys` and **invents no
  predicate and no term**: it is additive OR terms drawn from names and E3 aliases already in the
  graph, which is `ONTOLOGY.md` §2.2's own vocabulary.

---

## 2. Pass 2 — best practice: the measured lift, and the measured precision cost

Convergence across the sources, with the numbers each one carries:

| finding | source | what it says numerically |
|---|---|---|
| **Fact-augmented key expansion has NO published ablation of its own.** | LongMemEval, arXiv **2410.10813** (the issue's own citation) | The paper proposes fact-augmented key expansion + time-aware query expansion for indexing. Its reported gains are at the **framework** level; the result set contains **no recall@k delta and no expansion-cost breakdown** attributable to key expansion alone. ⚠️ So the `+9.4 % recall@k` the issue quotes as *"direction of record, not a promise"* is **not a measured ablation** — it is not a number this lane can build a falsifier on. |
| **Entity-anchored expansion measured a catastrophic recall LOSS when fused naïvely.** | implementation-level evaluation surfaced by the search (LongMemEval-S, k=5) | plain hybrid **93.19 %** strict `recall-all@5` → **54.89 %** with equal-weight expansion — **−38.30 points**. Not a peer-reviewed paper, and it conflates its weighting scheme with "expansion" generally — which is exactly why it is quoted as a **risk**, not an effect size. It is also the closest analogue to our own two-lane (post-expansion fused with the shipped leg) shape. |
| **Lexical expansion can help when the vocabulary differs — at a real per-query price.** | cross-encoder query-expansion report (surfaced) | NQ **Recall@100 0.32 → 0.47** (+15 pts) and another setting **0.70 → 0.83** (+13 pts); cost **43.5 ms/query** (MiniLM) to **409.6 ms/query** (BGE) for a 128-candidate pass on an A100. That is reranker-driven expansion, not entity keys, and its cost is 1–2 orders above our 300 ms E2E budget. |
| **Progressive expansion reports large relative gains.** | ProQE (surfaced) | up to **37 %** average MRR / Recall@1 improvement over non-expansion baselines across four datasets; **no absolute per-query embedding count or wrong-entity rate** in the material. |
| **PRF/RM3/RAG-Fusion: deep recall up, early precision down, ~2× or m× the retrievals, and it cannot recover what the first pool missed.** | RM3/Rocchio and RAG-Fusion literature (surfaced) | RM3 QLD → RM3, AP@1000: **0.221→0.292** (AP88-89), **0.183→0.200** (Robust04), **0.152→0.159** (WT10G), **0.329→0.337** (DL-20). Optimised PRF: **18–35 % MAP** over the original query on TREC corpora. Cost: **two retrieval passes** (sparse) or **m+1 searches + m query embeddings** (multi-query), with one RAG-Fusion implementation estimating **$0.06 / 1 000 queries** for rewrites and **≈1.5 s** added pre-search latency. Failure mode: **query drift** when the initial pool is bad — TREC Tip-of-the-Tongue 2025: only **32 %** of target documents were in the top-100 BM25 pool, and RM3 **"consistently degraded"** there. Verbatim bottom line from the material: *"Candidate-pool recall is the hard ceiling. If the target is absent from the initial top-k, RM3 cannot learn useful terms from that target."* |

**What converges, and what it means for us.**

1. **Expansion is not a fix for an absent candidate — it is a fix for a vocabulary mismatch inside a
   pool that already contains the target.** Every source that measures recall gains also states the
   ceiling: expansion re-weights and re-terms a query; it does not synthesize a document. **Our
   `absent_at_any_k` class is exactly the ceiling case**, which is why the deterministic
   query↔index spelling repair (`#12360`) moved it and expansion was never going to.
2. **The precision risk is measured and large** (the −38.3 pt analogue; the drift results; the
   abstract's own "expansion that pulls in the wrong entity"). Our mechanism is *additive* (no id is
   dropped, the original tokens keep their OR slots), which removes the mechanical "replaced the good
   query" failure — but **not** the fusion failure: `rrf_fusion` is rank-based, and the expanded pass
   is merged *first*, so an expanded leg can demote a correct pass-1 hit. That risk is stated in
   `_entity_key_dense_pass`'s own docstring and is **unmeasured**.
3. **The cost convergence is "one or two extra passes, plus one embed per dense pass"** — which is
   where our bound already sits (`≤1` extra fts, `≤1` embed, `≤1` extra vector query per read). The
   sources that buy their gains spend **1.5 s** or **43–410 ms** per query; our whole E2E budget is
   300 ms, so a *multi-term* expansion design is out of budget by construction, and a single bounded
   extra pass is the only shape that fits.

---

## 3. The measurement — the class, before any change

**Harness:** `tools/candidate_recall_11901.py` (unchanged; the same tool that produced the `#11901`
and `#12295` artifacts), `--population 1000 --sample 300 --captures 20`, corpus
`/home/cmux/.tortoise/session-events`. This box's `HOME` is `/home/cmux`, so that path IS the
harness's `--sessions-dir` default; §8 passes it explicitly so the corpus is part of the recipe rather
than an accident of `HOME`.

⚠️ **`corpus.files: 14` is the artifact's recorded input, and it is NOT a property of the directory a
reader sees.** Two readings of the same path on this box, made in the same hour while this PR was being
reviewed, disagree, and both are recorded here rather than silently resolved:

| reading | `*.jsonl` | range | newest member's birth |
|---|---|---|---|
| the artifacts (`corpus.files`) | **14** | — | — |
| re-measured for this brief, `03:10Z` and again `03:23Z` | **22** | 2026-10-27 … 2026-11-17 | `2026-11-17 00:07:44.424Z` |
| a parallel lane on the same box, `~03:22Z` | **14** | 2026-11-04 … 2026-11-17 | `2026-11-17 00:02:44Z` |

**The 22-reading is the reproducible one** (`ls -1 ~/.tortoise/session-events/*.jsonl | wc -l` → 22 at
both timestamps, `stat -c '%w'` on the newest member → `00:07:44.424Z`, and the directory's own `mtime`
is that same nanosecond), and a directory's `mtime` moves on every create, unlink and rename — so **no
entry can have appeared after `00:07:44Z`**, which is *before* the first run (`01:53:53Z`). The
14-readings are therefore not reachable from this directory at this path, and **this document does not
claim to know which input the artifacts read**; that question is open, not settled in either direction.
What follows for a reader: the corpus is live and per-box, so re-running §8 today reads a **different**
corpus, and every number below is its own artifact's recorded input — never a statement about the
capture corpus in general.
⚠️ The artifacts' own `issue` / `measured_for` fields
read `11901` / `12295`: those are the harness's hardcoded provenance
(`tools/candidate_recall_11901.py:2042-2045`), naming the runs it was written for, not this one.
⚠️ **Reading files outside the repo** — this is
the declared corpus the instrument exists to measure; nothing else outside the checkout was read.

**Pairing — stated as the artifacts show it, because the obvious inference is WRONG.** The corpus is
live and moved between every run (`harvested_distinct` definite
`123 927 → 123 938 → 123 961 → 123 981 → 124 057`; `corpus.input.lines` `10 746 → 10 761`). The
`arms.blocking.index_stats` **counts** are identical in all five runs (`members: 1000`, `blocks: 8454`,
`key_memberships: 11067`, `max_block_size: 102`) — but a count identity is **not** a member identity:
`corpus.population_digest` is a sha256 over the population's **names** in their deterministic order
(`tools/candidate_recall_11901.py`: `pop_digest = sha256("\n".join(r["name"] for r in pop))`, over the
`build_population` order that breaks ties on the name), so it can only agree when the name *set*
agrees — and **it differs in all five runs**:

| run | `corpus.population_digest` | `corpus.input.lines` |
|---|---|---|
| lexical, composite ON | `2323ddf78bc1b8dd` | 10 746 |
| lexical, composite OFF | `7c08a430787815ba` | 10 747 |
| hybrid, HEAD | `eec5fcf19b5bf617` | 10 748 |
| hybrid, HEAD — REPEAT | `81c6ff3d506a1d2e` | 10 750 |
| hybrid, composite OFF | `cd275d71459fb39a` | 10 761 |

⛔ **So the ON/OFF comparisons below are BETWEEN RUNS on a drifting corpus, not a paired ablation**,
and each lever's share in §3.3 is the size of a between-run difference rather than a controlled
contrast. What makes those differences readable at all is measured, not assumed: the two runs whose
only difference is the drift (`measurement-hybrid.json`, digest `eec5fcf1…`, vs
`measurement-hybrid-repeat.json`, digest `81c6ff3d…` — 2 corpus lines and 20 harvested names apart)
return the **identical row in every cell** (`in_window` 298/299/300/300, `miss_absent_at_max_k` 0,
`miss_past_k` 2/1/0/0, `rank_sum` 331/335/341/341). Drift of that size moved **no cell at all** in the
arm that carries the headline, while the ON/OFF deltas are 82 → 0, 20 → 0 and 82 → 20. **The residual
is named in §7**: the frozen-population five-arm re-run that would make the contrast genuinely paired
was NOT run, so read the lever shares as between-run differences an order of magnitude larger than
the one measured instance of drift sensitivity — not as a paired ablation.

| run | artifact | retrieval shape | `window_modes` |
|---|---|---|---|
| **hybrid, HEAD** | `measurement-hybrid.json` | fused read, dense leg live | **`hybrid`: 4372, `keyword_only`: 0** (`uniform_windows: true`) |
| **hybrid, HEAD — REPEAT** | `measurement-hybrid-repeat.json` | same, independent second run 6 min later | `hybrid`: 4372, `keyword_only`: 0 (`uniform_windows: true`) |
| **hybrid, composite OFF** | `measurement-hybrid-composite-off.json` | fused read, dense leg live, `TORTOISE_UNDERSCORE_TERMS=0` | `hybrid`: 4372 / `keyword_only`: 0 |
| **lexical, composite ON** | `measurement-keyword-only-composite-on.json` | lexical window only | `keyword_only`: 4372 |
| **lexical, composite OFF** | `measurement-keyword-only-composite-off.json` | lexical window only, `TORTOISE_UNDERSCORE_TERMS=0` | `keyword_only`: 4372 |

### 3.1 ARM 1 (`in_window` / `miss_past_k` / `miss_absent_at_max_k` / `rows_zero`), per k

#### HYBRID — the production read, HEAD (`measurement-hybrid.json`)

| k | `in_window` | recall@k | `miss_past_k` | **`miss_absent_at_max_k`** | `rows_zero` | `rank_sum` |
|---|---|---|---|---|---|---|
| **3** | **298/300** | **99.3 %** | 2 | **0** | 0 | 331 |
| 5 | 299/300 | 99.7 % | 1 | **0** | 0 | 335 |
| 10 | 300/300 | 100 % | 0 | **0** | 0 | 341 |
| 15 | 300/300 | 100 % | 0 | **0** | 0 | 341 |

By class at k=3 → k=15: definite **178 → 180** (of 180), ident **90 → 90** (of 90), path **30 → 30**
(of 30).

**Stability, measured rather than assumed:** an independent REPEAT of this exact run 6 minutes later
(`measurement-hybrid-repeat.json`, corpus `123 961` → `123 981` definite names) returns the **same
row for every cell** — `in_window` **298 / 299 / 300 / 300**, `miss_absent_at_max_k` **0**,
`miss_past_k` **2 / 1 / 0 / 0**, `rank_sum` **331 / 335 / 341 / 341**, and the same per-class cells
(definite 178→180, ident 90→90, path 30→30). The 0-absent result is not a one-run artefact.

#### KEYWORD-ONLY, composite ON (HEAD's lexical shape)

| k | `in_window` | recall@k | `miss_past_k` | **`miss_absent_at_max_k`** | `rows_zero` |
|---|---|---|---|---|---|
| **3** | 262/300 | 87.3 % | 38 | **0** | 0 |
| 5 | 278/300 | 92.7 % | 22 | **0** | 0 |
| 10 | 293/300 | 97.7 % | 7 | **0** | 0 |
| 15 | **300/300** | **100 %** | 0 | **0** | 0 |

By class at k=3 → k=15: definite **158 → 180**, ident **79 → 90**, path **25 → 30**.

#### HYBRID with the composite OFF — the production shape WITHOUT `#12360`

| k | `in_window` | `miss_past_k` | **`miss_absent_at_max_k`** | `rows_zero` |
|---|---|---|---|---|
| **3** | 246 | 34 | **20** | 0 |
| 5 | 256 | 24 | **20** | 0 |
| 10 | 267 | 13 | **20** | 0 |
| 15 | 280 | 0 | **20** | 0 |

By class at k=3 → k=15: definite **178 → 180** (of 180), ident **40 → 70** (of 90), path **28 → 30**
(of 30).

#### KEYWORD-ONLY, composite OFF — the pre-`#12360` shape the dispatch's baseline was taken on

| k | `in_window` | recall@k | `miss_past_k` | **`miss_absent_at_max_k`** | `rows_zero` |
|---|---|---|---|---|---|
| **3** | 184/300 | 61.3 % | 34 | **82** | 8 |
| 5 | 196/300 | 65.3 % | 22 | **82** | 8 |
| 10 | 209/300 | 69.7 % | 9 | **82** | 8 |
| 15 | 218/300 | 72.7 % | 0 | **82** | 8 |

By class at k=3 → k=15: definite **158 → 180**, ident **14 → 15**, path **12 → 23**.

### 3.2 The characterisation the dispatch asked for — "no candidate at all, or a candidate under a name the derivation never produces?"

**Both, and they are the same defect seen from two ends: the query never emits the term the index
holds.**

- **No candidate at all — `rows_zero`: 8 of the 82** in the composite-OFF run, all `ident`:
  `derived_invalidation`, `lease_until`, `longmem_eval`, `mcp_load`, `org_id`, `pull_request`,
  `safe_load`, `search_engine`. The leg returned **zero rows at every k**. Why: RediSearch indexes
  `web_search` as **one** term (`_` is a word character), while our tokenizer splits it into
  `web` + `search` and emits `search|web` — a query whose terms **the index does not hold**. The
  measured emitted bytes:

  | name | emitted OR query, composite OFF | emitted OR query, composite ON |
  |---|---|---|
  | `web_search` | `search\|web` | `search\|web\|web_search` |
  | `lease_until` | `lease\|until` | `lease\|until\|lease_until` |
  | `hosted_api` | `hosted\|api` | `hosted\|api\|hosted_api` |
  | `document_sha256` | `document\|sha256` | `document\|sha256\|document_sha256` |

- **A candidate exists under a spelling the derivation never produces — the rest of the 82.** The
  node is in the graph, with exactly that name; the window has rows — just none of them the
  canonical, because every row that the split-token query *can* reach is a different name
  (`ident` **75 of 90 absent**, `path` **7 of 30 absent**). Paths fail for the same reason when the
  file stem carries `_` (`tortoise/embed_writer_register.py`: the composite is `embed_writer_register`
  — a path entry with no `_` was never affected, which is why only 7 of 30 were absent).
- **The class is not a ranking or width problem.** `miss_absent_at_max_k` is **constant across
  k = 3, 5, 10, 15 (82 → 82 → 82 → 82)**: widening cannot reach a node whose index term no query
  emits. It is also not the *query's* length: `past_k` at k=3 is 34 in the same run and falls to 0 by
  k=15 — the two classes move independently, exactly as `#11901` §3/§4 said they would.
- **What fixed it is deterministic and already shipped**: `#12360`'s `_`-preserving composite OR tail
  (`tortoise/sparse.py::underscore_run_terms`, appended by `build_or_query`, default ON behind
  `TORTOISE_UNDERSCORE_TERMS`). Off → on: **absent 82 → 0**, recall@15 **218 → 300**, recall@3
  **184 → 262**. That is the same lever `#12360` measured at **0.7133 → 0.9633** on its own pinned
  population; this run reproduces its direction on a corpus harvested 2026-11-17 and reports the
  complete decomposition.

### 3.3 Who closes the class — the dense leg 62, the composite 20, together 82 → 0

Crossing the two axes (dense leg on/off × composite tail on/off) across the four runs tabulated above
decomposes the whole `absent_at_any_k` class **82 → 0**. ⚠️ Each cell is that run's OWN measurement,
and the four runs are NOT one population (§3's pairing note: every run's `population_digest` differs);
each lever's share below is therefore the size of a between-run difference — large relative to the one
measured instance of drift sensitivity, and stated as such rather than as a paired contrast:

| shape | `absent_at_any_k` | recall@3 | recall@15 |
|---|---|---|---|
| lexical only, composite OFF | **82** | 184/300 = 61.3 % | 218/300 = 72.7 % |
| **hybrid**, composite OFF | **20** | 246/300 = 82.0 % | 280/300 = 93.3 % |
| lexical only, composite ON | **0** | 262/300 = 87.3 % | 300/300 = 100 % |
| **hybrid, HEAD** (composite ON) | **0** | **298/300 = 99.3 %** | 300/300 = 100 % |

- **The dense leg alone removes 62 of the 82 across those runs** (82 → 20): the mention **is** the
  stored name, so its embedding is the node's own name vector and the vector leg ranks it into the
  window — which is why the class is invisible in the production read at HEAD.
- **The `_`-composite tail removes the remaining 20 in the hybrid shape** (20 → 0) and **all 82 in the
  lexical shape** (82 → 0). Both are deterministic, both are already shipped, neither is an expansion.
- **Neither lever trades absent misses for past-k misses.** Total k=3 misses fall across the four
  cells **116 → 54 → 38 → 2** and recall@3 rises monotonically (**61.3 → 82.0 → 87.3 → 99.3 %**). The
  `past_k` component reads **34 → 34 → 38 → 2**: it ticks UP by 4 in the lexical-ON cell precisely
  because the composite makes 82 previously-unreachable names reachable (so a few land between rank 4
  and 15 instead of nowhere), and the dense leg then collects them — which is the opposite of the
  "expansion floods the window" failure.

### 3.4 The dispatch's baseline is not reproducible at this head — and the nearest measured state is named

The dispatch says the measurement to beat is **28 absent / recall@15 88 % / recall@3 75 %**. Measured
here, on the same instrument: **0** (hybrid and lexical-on), **20** (hybrid, composite off) and **82**
(lexical, composite off). **No configuration on this box produced 28.** The nearest measured state is
**hybrid with the composite tail off** — *20 absent / 93.3 % @15 / 82.0 % @3* — i.e. the **same
regime** as the dispatch's figures, which is consistent with (but does not prove) that baseline having
been taken on a hybrid read **before `#12360` landed**, or on a corpus where some `_` runs were not yet
covered. ⛔ **Stated as a non-reproduction with the bracketing states named, never as an error in the
dispatch**: the corpus is live, the artifacts are dated (`#11901` reads 81 absent, `#12295` reads 19 on
its own corpus), and **no committed artifact on `main` holds a 28**.

---

## 4. Why additive entity/fact expansion is NOT the next lever on this class

Three independent reasons, each checkable without a new measurement:

1. **Arithmetic.** `miss_absent_at_max_k` is **0** in the production shape. An additive lever can only
   add candidates; adding candidates to a set that already contains the canonical at rank ≤ 3 for
   298/300 names cannot reduce a count that is already 0. The falsifier *"`absent_at_any_k` falls"* is
   therefore **unsatisfiable**, and any report of a fall would be a fabricated baseline.
2. **Structural (this is the decisive one).** The `#2518` mechanism harvests **`:Point`**
   `search_keys` over the **`aboutObject`** spine and is armed **only for `entity_type == "point"`**
   (`sdk.tortoise_fts_query`'s own gate, `tortoise/sdk.py:43269`). The instrument's arm 1 is the **`object`** leg
   (`_fts_rows(entity_type="object")`). The expansion cannot touch the leg this metric measures —
   not "has little effect", **cannot reach it**.
3. **External convergence.** Expansion recovers *vocabulary mismatch inside a pool that already holds
   the target*; its documented ceiling is the candidate pool (§2, verbatim), and its documented
   precision cost when fused naïvely is a **−38.3 pt** recall loss (§2). The class this dispatch names
   is the ceiling case, and it was closed by making the **query** emit the term the index holds — a
   deterministic repair, not an expansion.

**What would still be a fair test of expansion, and is NOT this lane's:** the **point** leg of the
capture prior (where `search_keys` actually live), measured with the `#2518` switch armed. The
`longmem_eval` harness (`tools/longmem_eval/run.py`, `retrieve.py`) already threads
`entity_key_expansion` and its `TORTOISE_ENTITY_KEY_EXPANSION` env, which is the only production-shaped
surface that reaches the mechanism. The `#11901` instrument cannot, and neither can its arms without
inventing a point corpus (declared, not done).

---

## 5. The falsifiers, answered one by one

| falsifier (dispatch) | verdict | output |
|---|---|---|
| the class is characterised **before** the change, with examples | **MET** | §3.2: 82 = ident 75 + path 7; `rows_zero` 8/82 with the named zero-row set; emitted-query evidence (`search\|web` vs the index's `web_search`); constant across k = 3/5/10/15. |
| **`absent_at_any_k` falls** | **UNSATISFIABLE — no change attempted** | At this head: **0** (hybrid, 4372 uniform hybrid windows; **0** lexical with the shipped composite), and the pre-`#12360` controls read **20** (hybrid) / **82** (lexical). There is no non-zero baseline at this head to fall from, so the falsifier is reported as not applicable rather than gamed. |
| **`past_k` does not rise** | **MET trivially — no change** | HEAD: hybrid `past_k` **2 → 1 → 0 → 0** (k=3/5/10/15); lexical-on **38 → 22 → 7 → 0**; lexical-off **34 → 22 → 9 → 0**. Nothing shipped, so nothing moved. |
| **extra embeds/queries counted and bounded** | **MET as a code bound; NOT exercised** | Per read, at most: **1** anchor FTS (≤ 20 fetched / ≤ 3 kept) + **1** batched `aboutObject` harvest (≤ 200 aliases, ≤ 64/anchor) + **1** extra fts pass + **1** extra embed (≤ 240 chars from ≤ 32 terms) + **1** extra vector-leg query. Fail-open on every path. **The eval counts 0 of them** because its arm never reaches the mechanism — stated rather than quoted as a measured cost. |
| **recall reported with its `window_modes` split, on a load-stated box** | **MET** | §3.1: hybrid **4372/0** (`uniform_windows: true`), lexical **0/4372** (twice). Box: 16 cores / 31 GB, load **33.2 → 4.4**; the hybrid arm's `#4997` unindexed-label warning is emitted by the read path on every hybrid run (`tortoise/projection/__init__.py:20091`) — it goes to the run's log and is **not** a field in the JSON artifact, which records the retrieval mode instead (`retrieval.note`). |
| **`uv run pytest tests/test_candidate_recall_11901.py -q` green** | **MET** | run through the lane seam — `uv run python tools/test_lane.py run -- tests/test_candidate_recall_11901.py -q` → **`56 passed in 8.91s`**, exit 0. The suite is hermetic (no graph, no corpus), so this pins the harness's reasoning surface, not the measured numbers. Lane-local log: `WORKTREE-REPORT.md` (gitignored, so the result is restated here rather than left only there). |
| **no new index, no surface change** | **MET** | No `CREATE INDEX`, no vector index, no change to `TOOL_REGISTRY` or to a public `TortoiseSDK` method. `git diff` for this lane is **docs + artifacts only**. |

---

## 6. The decisions these numbers support

**D1 — Do not ship an expansion lever for this class.** The mechanism stays exactly as shipped:
`entity_key_expansion` a caller-gated kwarg (default `False`) and `TORTOISE_ENTITY_KEY_DENSE` a
separate default-OFF switch. Arming either on the capture path would be an **unmeasured** lever
against a metric that is already saturated, and the external evidence says a naïve fusion of an
expanded leg can cost tens of points of recall. That is the same posture `#1745`'s fail-safe exists to
keep.

**D2 — The open work on `#2518` is not expansion; it is the point leg's own measurement, and the
epic's A4 row should be restated.** `#12186`'s A4 row reads *"widen the window + pass the levers we
already own (`time_aware`, entity-key expansion) — **partly BUILT**, `#2518` has the switch with no
producer"*. After this measurement the accurate statement is: **the switch has a producer for the point
leg (it is simply not armed in production), the window is closed (`#11901` measured it), and the
capture prior's object leg is saturated at HEAD (298/300 @3 hybrid, 0 absent)**. The lever that moved
the class is `#12360`'s composite tail, and the remaining lever on the *object* leg is the
`past_k`-at-k=3 residue (**2 of 300** hybrid; **38 of 300** lexical-only), which is a ranking question,
not an expansion one.

**D3 — Nothing is re-opened.** V1, §12.1c, §14.3 V3, the 2026-11-10 KEEP ruling and `#8487` stand
(§1, contradiction test).

---

## 7. What this document does NOT claim

- **Not a production census.** The population is a stratified 300-name sample of a live capture
  corpus (the artifacts record `corpus.files: 14`; the same path is re-measured at **22** in §3, and
  which input the artifacts read is **open**, not settled), and `/home/cmux/.tortoise/session-events`
  is not the population a hosted graph holds. Every number here measures the mechanism on a real name
  distribution, never production.
- **Not a hybrid-vs-lexical comparison as an effect size, and NOT a paired ablation.** The five runs
do **not** share a population: `corpus.population_digest` differs in every run (§3's table). The
`index_stats` counts agree, which is a count identity and not a member identity — reading it as
"same 300 names" was the first draft's error, and it is corrected here rather than left standing.
The corpus is live and the harvest drifted by 130 names (123 927 → 124 057) across the five
harvests. The only measured instance of that drift's effect on these cells is the hybrid REPEAT
(different digest, identical row in every cell), which is why the class deltas are read as real —
but the **frozen-population five-arm re-run** (one `--sessions-dir` snapshot, all five arms against
it) is the control that would make the contrast paired, and **it was not run**.
- **Not a measurement of the entity-key expansion.** It was **not exercised** by any arm here (§4.2:
  the mechanism is point-only; the instrument measures the object leg). Its cost bound is quoted from
  code and is **not** a measured latency.
- **Not a claim that the dispatch's 28 is wrong.** It is **not reproducible on this box** (§3.4); the
  two measured states bracket it (0 and 82) and the separating mechanism is named. No artifact on
  `main` holds a 28.
- **Not a claim about `#11901`'s own artifacts.** Their `miss_absent_at_max_k` (81 hybrid / 106
  keyword-only) predate `#12295` and `#12360` and describe a different, older corpus.
- **No LongMemEval number.** `docs/benchmarks/longmemeval-500q/run-report.md` stays `PENDING`.
- **Not a precision measurement.** The mechanism carries no false-merge arm, and this lane added none;
  the external −38.3 pt figure is a risk, not our number.

---

## 8. Reproduce

```bash
# 1. the lane's OWN container, and its four seams (never the ambient TORTOISE_DB_URI)
uri="$(uv run python tools/test_lane.py uri)" || exit 1
eval "$uri"
# 2. the embeddings extra — WITHOUT it the fused read degrades to keyword-only
uv sync --extra embeddings --extra parity
# 3. the measurement (NOT via `test_lane.py run`, which executes pytest).
#    `--sessions-dir` is explicit so the corpus is part of the recipe: on this box it
#    happens to equal the default (`$HOME/.tortoise/session-events`); on a box whose
#    HOME is not the capture user's, the default is empty and the harness exits
#    "no session events".
export HF_HUB_OFFLINE=1 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
uv run python tools/candidate_recall_11901.py \
    --sessions-dir /home/cmux/.tortoise/session-events \
    --population 1000 --sample 300 --captures 20 --out /tmp/m-hybrid.json
# 4. the pre-#12360 control (the shape the dispatch's baseline was taken on)
TORTOISE_UNDERSCORE_TERMS=0 uv run python tools/candidate_recall_11901.py \
    --sessions-dir /home/cmux/.tortoise/session-events \
    --population 1000 --sample 300 --captures 20 --out /tmp/m-composite-off.json
```

**Retrieval shape:** the hybrid arm needs the embedder to load **inside the run** — one keyword-only
run happened first on this box because `_has_embedder()` returned `False` in that process while the
same call in the same venv returns `True` (a transient import/load failure; the artifact labels itself
`keyword-only` and the label is a floor, not a certificate — `#11901` §2.1 says the same). The committed
hybrid artifact is one whose `retrieval.window_modes` is **uniformly hybrid**, so the shape is
certified by the strict per-window predicate (`search_engine.require_hybrid_read`), not by
`has_embedder`.

**What is pinned by tests:** the harness's structural facts and the two pre-existing artifacts
(`tests/test_candidate_recall_11901.py`). ⛔ The five artifacts in this directory are **not** pinned by
that suite — they are a dated measurement of a live corpus, and a pin would rot exactly as the corpus
does. (The suite pins the two `#11901` artifacts, which live in
`docs/research/2026-11-12-11901-candidate-recall/`, never these.) The numbers the dispatch can
re-check are the decompositions above, and each is re-derivable from the committed JSON — the per-k
cells with `jq '.arms.exact'`, the class split with `jq '.arms.exact_by_class'`, and the zero-row set
with `jq '[.arms.exact_misses[] | select(.returned==0)]'`. The emitted-query bytes in §3.2 are the one
exception: they are reproduced by **calling** `sparse.build_or_query` (under
`TORTOISE_UNDERSCORE_TERMS=1` and `=0`), not stored in any artifact.
