---
title: "Round 3 — the four falsifications re-tested against OUR tree, and how the top performers actually build the signal (#12186)"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-12
subjects.team: epistemic-team
issue: "#12186"
aboutSubjects: Tortoise entity resolution (S3 RESOLVE / dedup queue), candidate generation, text ranking
aboutObjects: research-brief.md (rounds 1–2), SEARCH-ARCHITECTURE.md §6.2, STORAGE-ARCHITECTURE.md §12.2c, #11901, #12244
---

# Round 3 — the four falsifications re-tested against OUR tree, and how the top performers actually build the signal

**Lane:** `research/12186-round3-verify` · **Type:** research only — no code, no architecture doc edited, no re-measurement of `#11901`
**Pinned at:** `f33f5e2c3215c94c6ad27a19bf07173f8439e505` (`origin/main`, 2026-11-12)
**Predecessors:** round 1 `#12189` (merged) + comment `6828533944`; round 2 PR `#12270` + comment `6837406765` (branch `research/12186-round2-depth`, verified at `9828250732`).

> ⛔ **Every locator below was re-measured at `f33f5e2c3215`, not inherited — with one declared exception.**
> Round 2's own §7.0 makes this a discipline — *"Locators drift, and a pin is not a citation"* (round-2 branch
> `research-brief.md`, `:393`) — and it is worth doing: **round 2's locators all hold at this
> head, and so do the four this round added** (`entity_resolver.py:739`/`:801`, `entity_identity.py:355`/`:417`)
> (checked individually — `entity_resolver.py` `:207`/`:536`/`:547`/`:624`/`:694`/`:739`/`:801`/`:809`,
> `entity_identity.py` `:355`/`:378`/`:384`/`:417`/`:499`/`:589`/`:594`, `search_engine.py`
> `:2986`/`:3103`/`:4492`, `sparse.py` `:13-14`, `projection/edges.py` `:717-730`) — while
> **`SEARCH-ARCHITECTURE.md` §6.2's do not**: its `search_engine.py` pins have drifted ~1,390 lines (`:3021` →
> measured `:4411`) and its `sdk.py` pins ~2,825 (`:38401` → measured `:41226`). **A pin in a research doc must
> name its head; a pin in an architecture doc must be refreshed by that doc's changelog.**
> ⛔ **The exception, declared: round 2's `research-brief.md` §7 is NOT on `origin/main` at this head** — it lives
> on `origin/research/12186-round2-depth` (956 lines, PR `#12270`). Any §7 citation below names the branch and
> makes no claim that it resolves here; a bare line number into that section would be unverifiable from this tree
> and would shift again once both PRs merge.

---

## 8.0 Method, rung taken, queries, and what was read

**Internal pass first.** `#12186` + comments `6828533944` / `6837406765`; `#11901` (measurement of record: recall@3
61.3 %, @15 73.0 %, 27 % unreachable at any k); `#11395` comment `6781921075` (the KEEP ruling — never reopened);
`#2730`; `#12184`; `#2518`; `#2513`; `#4211` (substrate census, **cited not re-measured**); `#12244`;
`SEARCH-ARCHITECTURE.md` §6.2/§7; `STORAGE-ARCHITECTURE.md` §12.2c/§14.3 V3/§17.4.6.

**Rung taken: 1 — `mcp__seo-intelligence__perplexity_search` (~$0.005/query). It answered; rung 2 was never
needed.** Four queries:

| # | query | used for |
|---|---|---|
| 1 | `Graphiti NODE_DEDUP_CANDIDATE_LIMIT NODE_DEDUP_COSINE_MIN_SCORE dedup source code` | locating the Zep constants (then **read from source**, not from the snippet) |
| 2 | `Hindsight agent memory github repository python package hindsight-all recall four networks graph traversal source code` | locating `vectorize-io/hindsight` (the paper names it; the repo is where the mechanism is) |
| 3 | `Neo4j Graph Data Science NodeSimilarity Jaccard over neighbor set entity resolution structural similarity documentation` | the general shared-neighbour pair-scoring answer |
| 4 | — *(Zep `main`, Hindsight `main`, Mem0 `main`, Mastra `main`, FalkorDB docs fetched directly)* | every code claim below |

**Read from the source trees** (not blog prose), at these heads, all fetched this round:

| system | files read | how |
|---|---|---|
| **Zep / Graphiti** | `graphiti_core/utils/maintenance/dedup_helpers.py` (400 L, **every line**), `.../node_operations.py` (1,295 L, the dedup region), `graphiti_core/prompts/dedupe_nodes.py` (248 L, **every line**) | `raw.githubusercontent.com/getzep/graphiti/main/…` |
| **Hindsight** | `hindsight_api/engine/memories/pg/link_expansion.py` (444 L), `engine/db/ops_postgresql.py` (the entity CTE), `engine/search/fusion.py` (176 L, **every line**), `engine/search/reranking.py` (588 L, scoring region), `engine/search/graph_retrieval.py` | `raw.githubusercontent.com/vectorize-io/hindsight/main/…` |
| **Mem0** | `mem0/memory/main.py` (3,916 L, add-path + search-path regions), `mem0/utils/scoring.py` (139 L, **every line**), `mem0/utils/entity_extraction.py` (772 L, header + function list) | `raw.githubusercontent.com/mem0ai/mem0/main/…` |
| **Mastra OM** | `packages/memory/src/processors/observational-memory/ARCHITECTURE.md` (432 L), `observational-memory.ts` (4,723 L, grepped), `anchor-ids.ts`, `observation-utils.ts` | `raw.githubusercontent.com/mastra-ai/mastra/main/…` |
| **FalkorDB** | `docs.falkordb.com/cypher/indexing/fulltext-index` — fetched and text-extracted **this round** | self-verified, not inherited |

**Not consulted:** any live tenant. No graph census was possible (no reachable tenant serves
`:Object`/`:Subject` populations), so every population number below is either a committed artifact or is
**cited from `#4211`** — and labelled as cited.

---

## 8.1 HALF A — the four falsifications, re-tested

### 8.1.1 `resolved_from` retires the node in every reader ⇒ "link without merge" is a merge under another label

**Verdict: PARTLY — CONFIRMED on identity, ⛔ OVERTURNED on retrieval.**

Round 2 said *"retires the node in **every** reader"*. That is true of the identity/resolution readers and
**false of every retrieval reader**, and the difference matters because it is the difference between "a redirect
is a worse merge" and "a redirect is a merge **plus** a visible duplicate".

**The identity readers — nine reads, eight of which retire the carrier:**

| reader | line at `f33f5e2c3215` | what it does |
|---|---|---|
| the dedup scan's universe | `entity_resolver.py:199` `_ENTITY_READ`, filter at `:207` | `AND n.resolved_from IS NULL` — a carrier is never a survivor and is never re-offered as a candidate |
| the by-name resolver | `entity_resolver.py:611` (the `$k IN n.aliases` arm), hop at `:624` | matches the carrier by its own `canonical_name`, then `follow_resolved_from` **returns the survivor** |
| the write-side union | `entity_identity.py:466` `_live_holders_same_entity` → `_collapse_resolved_holders` (`:350`, hop `:384`) | collapses each carrier onto its survivor and counts DISTINCT ids |
| the create verb | `entity_identity.py:593-594` (`resolve_or_mint`) | a write of the variant lands on the **survivor** |
| the fold-name resolver | `entity_identity.py:621` `resolve_fold_name`, collapse at `:729` | collapses carriers before resolving a folded name |
| the id resolver | `entity_identity.py:1102` `resolve_entity_id`, collapse at `:1173` | collapses carriers itself — its own comment at `:1164-1172` is about this exact disagreement |
| the role-holder resolver | `entity_resolver.py:878` `resolve_role_holder`, hop at `:933` | *"A merged-away carrier is not the holder any more (same rule as the write path): hop to the entity the merge recorded"* |
| the ANY-status / un-collapsed read | `entity_identity.py:410` `_name_holder_ids_same_entity` | deliberately does **not** collapse (a held name must not read as unheld) |
| the onboarding seed's anchor collapse | `onboarding/seed.py:271` `_collapse_merged_anchor_rows`, hop at `:307` (`target = cur.get("resolved_from")`) | collapses merged-away carriers onto their survivor, docstring *"**any status**"* — one of the eight that retire |

**The retrieval readers — none of them knows the property exists.** Measured counts at this head:

```text
tortoise/search_engine.py   resolved_from=0   dedup_reviewed=0
tortoise/ranking.py         resolved_from=0   dedup_reviewed=0
tortoise/session_link.py    resolved_from=0   dedup_reviewed=0
tortoise/assembly.py        resolved_from=0   dedup_reviewed=0
```

⚠️ **`sdk.py` is deliberately not in that table, because it does mention `resolved_from` — four lines across
three sites, and all three sites are off the read path:** a docstring (`:24321`), the `approve_merge` verdict echo (`:24485-24486`), and the
`exists_by_name` guard comment (`:27847-27852`). **None of them is a retrieval filter**; the module's retrieval
legs reach the graph through `search_engine`'s legs. Stated so a reviewer who greps `sdk.py` does not read the
table as a claim about the whole tree.

Their only entity-family filter is **`status`** — `_status_vocab_for` (`search_engine.py:98-138`), the label list
`_STATUS_FILTERED_LABELS` (`:148`), applied ahead of `ORDER BY`/`LIMIT` on the FTS leg (`:2973-2980`, cypher
`:2986-2997`). And **`apply_entity_merge` writes no `status`** — its only candidate props are
`resolved_from`, `dedup_reviewed='merge'`, `reviewed` (`entity_resolver.py:797-805`), plus `canonical_name` when
absent; the survivor gets `aliases` (`:784`, `:792`). The carrier's `name` is never rewritten.

⇒ **A merged-away carrier is still search-visible and still ranked.** A `resolved_from` write is therefore
*neither* the link band *nor* a clean merge: it is **one identity in the resolver and two documents in
retrieval**.

**What this changes for the child cut.** Nothing about N-4's dispatch readiness — it is still blocked on
`SAME_AS` (§8.1.1b). It **adds a defect** that is bigger than the band, recorded in §8.2 as **D-1**.

#### 8.1.1b — "Is there a way to point at a canonical node WITHOUT retiring it?"

**Yes — exactly one, it is one line, and it is not a link band.** Ranked by cost:

| route | exists? | what it writes | does the candidate stay live? | verdict |
|---|---|---|---|---|
| **`aliases[]` on the SURVIVOR only** | ✅ **yes** — `entity_resolver.py:784` builds `alias_list`, `:792` writes it; read back at `entity_resolver.py:611` and `entity_identity.py:843` | one list on one node; **nothing on the candidate** | ✅ yes — `resolved_from IS NULL`, so it stays a dedup candidate (`:207`) and stays search-visible | **the only real "point without retiring".** ⚠️ **Cost, traced:** the write path's union now sees TWO live holders of that folded name (candidate by `name`, survivor by alias) → `_live_holders_same_entity` returns 2 → `refuse_if_ambiguous` (`entity_identity.py:1003`) raises `AmbiguousEntityName`. So an alias-link converts a silent auto-resolve into a **routed refusal** |
| **`supersededBy` / `supersededAt`** — an existing name-keyed successor pointer on both `:Object` and `:Subject` (`assembly.py:2580` reads all three together) | ✅ yes | a pointer + a lifecycle status | ⛔ **no** — it is a *lifecycle* transition, and `_refuse_bare_lifecycle_carrier` (`sdk.py:5447`) refuses a carrier that lacks `status='superseded'` | **not a pointer without retiring — it is the retirement.** Cited here because it is the reason "`aliases[]` is the only one" is *earned* rather than lucky: the other name-keyed pointer in the tree is guarded into being a status change |
| `SAME_AS` / `sameAs` edge | ⛔ **no** — `_VALID_EDGE_PREDICATES` (`projection/edges.py:717-730`) has no `sameAs`; `grep -rni sameas tortoise/` → **0 files** | — | — | still the `#2730` owner question |
| a new property read by a new reader | trivially | — | ✅ | **inert until the reader exists** — which is precisely §8.1.2's failure mode. Not a route, a to-do |

⛔ **And the distinction is the whole point, so state it plainly:** `aliases[]` records **addressability**, not a
**verdict**. "This spelling names that entity" is not "these two nodes are probably one thing, and here is the
evidence". A link band must record the *pair* and the *reason*; `aliases[]` records neither, and it cannot be read
back as one (there is no inverse: given an alias you cannot recover which variant it came from, or when, or on
what score). **So the answer to the brief's question is: yes, a non-retiring pointer exists — and restoring
round 1's N-4 with it is not possible, because what N-4 needs is the verdict, not the pointer.**

### 8.1.2 `dedup_reviewed='link'` is read by nothing *value-aware*

**Verdict: CONFIRMED for *value-aware* reads — but ⛔ CORRECTED on two of round 2's supporting claims, in the direction that costs
work.** Round 2 said *"read by nothing"* and this lane's first draft added *"written by nothing"*. **The read
half is right for every value-aware reader, and wrong for the one reader that is value-agnostic — and the write
half is wrong outright.**

**Every read, at this head** — `dedup_reviewed` occurs **21** times in `tortoise/` across four files; every site that
**tests or writes the VALUE** is below. The other nine are the property declaration (`_placement_table.json:189`),
an echo of the scanned row (`entity_resolver.py:273`), a second write-side key selector (`review_verdicts.py:513`,
the same selector as `:382`), and comments/docstrings (`sdk.py:24087`, `:24321`, `:24324`; `review_verdicts.py:52`;
`entity_resolver.py:687`, `:739`):

| site | line | value tested |
|---|---|---|
| `entity_resolver.py` — the scan read | `:211` | `coalesce(n.dedup_reviewed, '')` |
| `entity_resolver.py` — `_pair_reviewed` | `:216`, `:226` | `'merge'` (`:227-228`), `'reject'` (`:229`) — and the reject arm is **set-valued**, matched against `dedup_rejected_targets` (`:230-250`), not a scalar |
| `entity_resolver.py` — the ladder's skip | `:694` | `== 'merge'` |
| `sdk.py` — the content arm | `:23664`, `:23674` | `== 'merge'` |
| ⛔ **`sdk.py` — the CONTENT review queue** | **`:24256-24257`** | **`WHERE n.dedup_candidate = true AND n.dedup_reviewed IS NULL`** — **value-agnostic** |
| `sdk.py` — the queue's own echo | `:24274` | echoes the value, tests nothing |
| `sdk.py` — the verdict echo | `:24471` | reads the value, then **does** test it: `:24482` (early return on the same verdict) and `:24487` (refuses to overwrite a `'merge'`) — a value-aware read the earlier phrasing understated |
| `review_verdicts.py` | `:382` | *selects which key to write* (`temporal_reviewed` vs `dedup_reviewed`) — a write-side selector, not a read of the value |

**Every write:**

| site | line | value |
|---|---|---|
| `entity_resolver.py:802` | `apply_entity_merge` | `'merge'` |
| `sdk.py:24415` | content arm, `$a` | whatever `approve_merge` accepted |
| `sdk.py:24574` | entity reject | `'reject'` |
| `review_verdicts.py:516` | the close verdict | `'close'` |
| ⛔ **`sdk.py:51584 update_entity(id, **props)`** | the generic entity/point update door | **any string the caller passes** — it reaches `SET n += $props` (`:6499`, `:6526`, `:6688`), and `dedup_reviewed` is a declared point property (`tortoise/_placement_table.json:189`) |

#### Two corrections, both material

1. ⛔ **The vocabulary is NOT closed.** The two verdict *verbs* offer `{merge, reject, close}` and `approve_merge`
   refuses anything else (`sdk.py:24350` `if action not in ("merge", "reject")`), but that constrains one
   argument of one method, not the property. `update_entity(id, dedup_reviewed="link")` is one call, and the
   record side is unconstrained too — `supabase/migrations/20261012000003_record_epistemic.sql:106` is a bare
   `dedup_reviewed text,` with **no `CHECK`** — and no neighbouring column carries one either (`:102-111`:
   `dedup_method`, `dedup_similarity`, `dedup_target_id`, `dedup_reviewed`, `temporal_*` are all bare; the nearest
   column-level `CHECK` is `node_label` at `:54`, and `:123-130` are table-level constraints, none of which names
   `dedup_reviewed`). **So the write is not missing. Only
   the value-aware read is.**
2. ⛔ **`'link'` is not inert — it silently DEQUEUES.** The content review queue filters
   `n.dedup_reviewed IS NULL` (`sdk.py:24256-24257`), so **any** non-NULL value removes the row from the review
   surface with **no verdict applied**. An unexpected value is therefore *acted on* (a lost review nobody counts),
   not ignored — and the entity queue disagrees with the content queue about unknown values
   (`_pair_reviewed` tests only `'merge'`/`'reject'`, `:226-230`), so the same stored string has two different
   meanings depending on the label.

`grep -rn "'link'\|\"link\"" --include=*.py tortoise/` → **0 matches** as a *verdict value*; `tests/` → **26**, none
of them a `dedup_reviewed` value (an HTML VOID set at `tests/e2e/test_legal_pages.py:424`, `os.link` monkeypatches,
`tmp_path / "link"` at `tests/test_reaper.py:621`). Outside this document, the only `"link"` occurrences in `docs/`
are that same unrelated void-element set, restated **three** times in
`docs/plans/2026-08-08-657-legal-pages-plan.md:194`, `:271`, `:450`; round 2's own hypothesis lives on the
round-2 branch `research-brief.md` §7 ~`:830`, **not** in this tree's `docs/`.

⇒ **Corrected conclusion, and it is a *narrower* one:** a `'link'` band needs **a reader that means "linked"** —
not a write (one `update_entity` call) and not a new field. And it inherits a hazard the envelope does not show:
until that reader exists, writing the value **removes the candidate from review**. Round 2's *"a merge under a
different label"* stands; its *"read by nothing"* framing does not — this is state that bites.

### 8.1.3 FalkorDB exposes no `scorer`; our FTS is TF-IDF by the vendor's own definition; `rrf_fusion` discards the score so the scorer acts only through `LIMIT`

**Verdict: CONFIRMED on the first two, ⛔ PARTLY on the third — and the third contradicts our own architecture
doc, not just round 2.**

**(a) No `scorer` — self-verified this round from the vendor's page.** Extracted verbatim:

> "In addition to yielding matching nodes, full-text index scans will return the score of each node. **This is the
> TF-IDF score of the node**, which is informed by how many times the search terms appear in the node and how
> closely grouped they are." — [docs.falkordb.com/cypher/indexing/fulltext-index](https://docs.falkordb.com/cypher/indexing/fulltext-index)

and in its own FAQ, *"The score reflects **TF-IDF** relevance ranking"*, and *"Returns relevance scores (TF-IDF)
for ranking results"*. **The option surface the page actually names is five things — index-level `Language` and
`Stopwords`; field-level `Weight`, `Nostem`, `Phonetic`. There is no `scorer`.** Query surface:
`CALL db.idx.fulltext.queryNodes('Label', 'search term')` — a bare string, no options map.

**(b) Our index is created with no `OPTIONS` block.** `_create_fulltext_index` (`projection/__init__.py:20755`)
tries two forms (`:20785-20788`): `CREATE FULLTEXT INDEX FOR (n:{label}) ON ({fields_expr})`, then
`CALL db.idx.fulltext.createNodeIndex('{label}', {...})`. Neither carries `OPTIONS`. The declared entity index
fields are `("Subject", ["name"])` and `("Object", ["name"])` (`projection/__init__.py:6819`, `:6823`) — the
detail that makes §8.2's D-1 bite.

**(c) `sparse.py`'s "BM25-style" is a comment error, confirmed.** `sparse.py:13-14` verbatim: *"OR-union +
RediSearch's TFIDF scorer ranks docs by term-overlap, which is the BM25-style rank behavior the epic's scope
prescribes (issue #1541 D1/D2)."* `bm25.py:5` quotes it back. The epic's row title — "BM25 instead of TF-IDF" —
is literally accurate.

**(d) ⛔ OVERTURNED: round 2's rule — *"the TF-IDF score never reaches the answer as a score: it decides which
rows survive the LIMIT"* — is not what our code or our doc says.** It acts in
**three** places, and `SEARCH-ARCHITECTURE.md` §6.2 (`:246-260`) already names the third:

1. **`LIMIT` selection** — the FTS leg is `ORDER BY score DESC, <recency tie> LIMIT $limit`
   (`search_engine.py:2992-2994`). Confirmed.
2. **`rrf_fusion` discards it on the multi-leg arm** — `search_engine.py:4411` (def), the binding
   `for rank, (pid, _score) in enumerate(ranked)` at `:4492`, rationale at `:4520`. Confirmed verbatim.
3. ⛔ **The single-leg / full-scan arm keeps the raw leg score as the output score AND as a floor** —
   `sdk.py:41223-41226`:

   ```python
   if is_full_scan or len(raw_results) == 1:
       strat_name, ranked = next(iter(raw_results.items()))
       # Apply threshold filter (score floor)
       fused = {pid: score for pid, score in ranked if score >= threshold}
   ```

   On that arm the dict's insertion order is the leg's TF-IDF order, and it **is** the final order — ⚠️ **with
   one condition: `recency_boost == 0.0`,** which is the shipped default. When a caller sets `recency_field` and
   `recency_boost > 0`, the branch at `sdk.py:41294-41309` multiplies the fused score by a recency factor **and
   re-sorts** on `(score, recency)`, so the leg's order no longer survives. `threshold` is compared against the
   **raw** score either way. `threshold`'s shipped default is `0.0` (`sdk.py:39852`), so the floor is inert by
   default; it is a live path all the same, and a rescale changes which rows clear it.

   §6.2 says exactly this (`:257-260`, *"when a single leg answers or the read is a full scan, a caller's
   `threshold` IS compared against that **leg's raw score**"*). Round 2's *"it decides which rows survive the
   `LIMIT`"* dropped it.

**Two further corrections, for the record — neither is a correction to round 2:**

- **"§6.2 `:268` is the only site in the repo that says it correctly" was this lane's own first-draft claim, not
  round 2's — and it is wrong: there are two sites.** Round 2's §7.1 table already cites §6.2 at `:163`,
  `:241-246` and `:268-269`, and states *"the section table restates it at `:163`"* (round-2 branch
  `research-brief.md`, `:436`), so `:163` was never missed. `:163` also says TF-IDF
  (*"`db.idx.fulltext` — RediSearch's TF-IDF scorer, documented as BM25-style behaviour in `tortoise/sparse.py`"*).
  Both are correct in substance; both point at `sparse.py` as the source of the wrong claim.
- ⛔ **§6.2's own locators are stale at `f33f5e2c3215`** — it cites `search_engine.py:3021` / `:3091-3093` and
  `sdk.py:38401` / `:38445` / `:37470`. Measured now: `rrf_fusion` **`:4411`**, the discard **`:4492`**, the
  single-leg floor **`:41226`**, the `threshold` default **`:39852`**. A lane that trusts §6.2's pins will land on
  the wrong lines. **This is a doc-drift finding, not a re-scope: §6.2's content is right and must not be edited
  by this lane** — the pins are what needs refreshing, and that is `SEARCH-ARCHITECTURE-CHANGELOG.md`'s job.

#### What M3 must measure for a BM25-vs-TF-IDF question to be *answerable at all*

The epic's row (`#12186`) and `SEARCH-ARCHITECTURE.md:517` say *"score distributions + the `#8933` identifier
fixture"*. Given (d), **a distributions-only M3 under-reads and a rank-only M3 over-reads.** Six requirements:

1. **Both distributions, same corpus, same query set, same head.** A rank delta alone cannot be attributed to the
   scorer (§6.2's own 8–23 nDCG@10 is a *rank* gap measured elsewhere, between other implementations).
2. **The FTS leg in isolation** — `run_fts_query` with the vector leg off. On the multi-leg arm the score is
   discarded (`:4492`), so **no hybrid measurement can say anything about the scorer**.
3. **Recall@k of the leg, not only nDCG.** The scorer acts on *selection* (`:2992-2994`). A scorer that ranks
   better but admits fewer relevant rows at `K` is a **regression** for a 3-deep window — `#11901` measured
   61.3 % @3, so `K ∈ {3, 5, 10, 15}`, not a top-10-only number.
4. **The single-leg floor arm stated explicitly** — record whether `threshold` was held at `0.0` and why that is
   the shipped configuration; if it was not, the measurement is of a different read path.
5. **The `#8933` identifier fixture** — unchanged, and it is a **correctness** gate, not a quality one:
   `to_tsvector` cannot reproduce `ERR-4032 → err4032` **plus** the adjacency phrase.
6. **A stated tie rule.** Both scorers must be read through the same `recency_tie_clause`
   (`search_engine.py:2993`), or the comparison is measuring tie order.

**Contradiction test:** no recorded decision is reversed. The **text-index** ruling gates the **index location** —
*"the text index **stays** until **M3** measures a BM25-capable Postgres option"* (`SEARCH-ARCHITECTURE.md:268`) — and
M3 *is* that ruling's own named measurement (`:517`), pre-authorised inside `#12186`. ⚠️ **Not V1**: V1 is the
***vector*-index** question (`STORAGE-ARCHITECTURE.md:1581`), resolved by the pgvector move (`SEARCH-ARCHITECTURE.md:25`).
Requirements 3–4 narrow M3; they do not move its scope, and they add no new gate.

### 8.1.4 Zep's Jaccard is over 3-gram NAME shingles with an LSH candidate universe, and reads no edge

**Verdict: CONFIRMED, verbatim, from `main` — with one refinement round 2 got right and one it did not state.**

| claim | source at `getzep/graphiti@main` |
|---|---|
| 3-gram shingles over the **name**, whitespace-stripped | `dedup_helpers.py:89-95` — `cleaned = normalized_name.replace(' ', '')` … `{cleaned[i:i+3] for i in range(len(cleaned)-2)}` |
| the fuzzy fold keeps alphanumerics + apostrophes | `:46-50` `_normalize_name_for_fuzzy`: `re.sub(r"[^a-z0-9' ]", ' ', …)` |
| Jaccard over the **shingle sets** | `:132-141` `_jaccard_similarity(a: set[str], b: set[str])` |
| threshold 0.9, 32 MinHash permutations, band 4 | `:34-36` `_FUZZY_JACCARD_THRESHOLD = 0.9`, `_MINHASH_PERMUTATIONS = 32`, `_MINHASH_BAND_SIZE = 4` |
| candidate universe = **LSH band buckets only** | built at `:310` (`lsh_buckets[(band_index, band)].append(candidate.uuid)`, inside `_build_candidate_indexes` `:293`), consumed at `:360-361` (`candidate_ids.update(indexes.lsh_buckets.get((band_index, band), []))`) — and the Jaccard loop runs over `candidate_ids` **only** |
| **no edge read anywhere in the module** | confirmed by reading all 400 lines: the only inputs are `candidate.name`, `.uuid`, `.labels`, `.attributes`, `.created_at`. Zero relationship access |

**The refinement round 2 got right, worth keeping visible** — `dedup_helpers.py:328-330` (the docstring of
`_resolve_with_similarity`; `:336-340` is the code that implements it, not the quote), verbatim:

> "Exact normalized-name matching runs first for *all* names regardless of length or entropy. The entropy gate
> only guards the fuzzy (MinHash/LSH) path where short or low-entropy names produce unreliable shingle sets."

So the entropy gate is **narrower than "a gate on fuzzy matching"**: exact-normalised matching and
identity-property matching are never gated. `_has_high_entropy` (`:80-86`) is `if len(name) < 6 AND
token_count < 2: return False`, otherwise `entropy >= 1.5` — an OR-to-pass length test, AND-ed with Shannon
entropy over non-space characters (`:53-77`).

**And the assertion round 2 did not make, which the child cut needs:** the resolution ORDER is deterministic-first
and LLM-last, per node — `resolve_extracted_nodes` (`node_operations.py:816-873`) runs, per extracted node:
**identity properties** (`:850`) → **exact/fuzzy** (`:861`) → else **LLM** (`:872`, batched at `:874+`). An
ambiguous exact name does *not* go to the heuristic — it is queued for the LLM directly
(`dedup_helpers.py:344-349`). **The precision property is that a cheap rung that answers ends the question.**

### 8.1.5 The question that matters: does any system score a **PAIR** on shared graph neighbours?

**Verdict: YES — two, and one is production code whose SQL I read.**

**(a) Hindsight's graph arm is literally a shared-neighbour pair score.** Module header
(`link_expansion.py:1-20`), verbatim:

> "1. Entity links — query-time self-join through `unit_entities`. **Score = number of distinct shared entities
> between the seed set and each candidate, computed via `COUNT(DISTINCT entity_id)`.** Uses a LATERAL per-entity
> cap (`graph_per_entity_limit`, default 200) to prevent high-fanout entities from exploding the self-join
> intermediate rows."

The SQL, `ops_postgresql.py:1069-1109`:

```sql
seed_entities AS (
    SELECT DISTINCT ue.entity_id FROM {ue_table} ue WHERE ue.unit_id = ANY($1::uuid[])
),
entity_expanded AS (
    SELECT {memory_unit_columns("mu", indent=23)},
           COUNT(DISTINCT se.entity_id)::float AS score,
           'entity'::text AS source
    FROM seed_entities se
    CROSS JOIN LATERAL (
        SELECT ue_target.unit_id FROM {ue_table} ue_target
        WHERE ue_target.entity_id = se.entity_id
          AND ue_target.unit_id != ALL($1::uuid[])
          AND EXISTS ( ... fact_type + window ... )
        ORDER BY ue_target.unit_id DESC
        LIMIT {per_entity_limit}
    ) t
    JOIN {mu_table} mu ON mu.id = t.unit_id
    GROUP BY mu.id ORDER BY score DESC LIMIT $3
)
```

and the Python squashes the count into `[0,1]` — `link_expansion.py:249`:
`entity_scores[fact_id] = math.tanh(row["score"] * 0.5)` (1 shared entity → 0.46, 2 → 0.76, 3 → 0.91, per the
comment above it). Its three arms are combined **additively** into `[0,3]` (`:246-270`).

**(b) Neo4j GDS `NodeSimilarity` is the library primitive for exactly this** —
[Jaccard / Overlap / Cosine over neighbour sets](https://neo4j.com/docs/graph-data-science/current/algorithms/node-similarity/),
*"Two nodes are considered similar if they share many of the same neighbors"*, with `degreeCutoff` /
`similarityCutoff` / `topK` / `topN` as the bounding knobs and **O(n³) time, O(n²) space** stated in the doc.

**(c) What it needs that we do not have — and it is not the shape.** Hindsight needs a populated
memory↔entity join (`unit_entities`); ours is an edge (`(Point)-[:aboutObject]->(:Object)`) and the shape is
already exactly right — a reverse hop is one Cypher `MATCH`. What we lack is **fill**: `#4211`'s census measured
**484 Points, 484 edges, all `CONTAINS`; `aboutObject` 0, `IMPL` 0, `NAND` 0** (cited from `#4211`; not
re-measured here — no reachable tenant).

**(d) And the second thing we lack is the BOUND.** Both implementations cap fan-out before they score
(`per_entity_limit` 200; `degreeCutoff`). We would need it more than they do, because our `:Object` nodes are
minted `MERGE (o:Object {name:$name})` — a bare definite description is a natural hub, and an uncapped self-join
over one hub entity is quadratic over the whole label.

**⇒ B2 re-expresses a third time, and this is the settled form:** the graph's contribution to candidate
generation is a **shared-entity co-occurrence count over a seed set, capped per entity** — Hindsight's shape —
and it is **gated on a populated `aboutObject` edge**, which is `#4211`'s substrate, not a ranking change. It is
**not** a pairwise similarity score for the verdict, and **not** Jaccard over typed edges. Those two readings are
both dead.

---

## 8.2 D-1 — the defect this round found, which no inventory lists

**A completed merge leaves BOTH nodes search-visible, and the capture path can pick the retired carrier as its
resolution target.**

The chain, all at `f33f5e2c3215`:

1. `apply_entity_merge` stamps `resolved_from` / `dedup_reviewed='merge'` / `reviewed` and **no `status`**
   (`entity_resolver.py:797-805`); the carrier's `name` is untouched (`canonical_name` is only *filled*, never
   rewritten — `:799-805`).
2. `:Object` and `:Subject` are full-text indexed **by `name` alone** (`projection/__init__.py:6823`, `:6819`).
3. The FTS leg filters **`status`** and nothing else (`search_engine.py:148`, `:2973-2980`, cypher `:2986-2997`).
   ⇒ a search for the merged-away spelling **still returns the carrier**; the survivor joins it only where its own
   `name` shares query tokens with that spelling, because the FTS fields are `name` alone
   (`projection/__init__.py:6819`, `:6823`) and `aliases` — though written (`entity_resolver.py:792`) — is in
   neither field list.
4. **The capture path does not consult the resolver at all.** S3's entity prior set is built by `_fts_rows`
   (`extractor_v2.py:2887`) → `sdk.tortoise_fts_query(entity_type="object"|"subject", limit=3,
   include_terminal=True, …)` (`:2906-2907`, `:2908-2911`). Note `include_terminal=True` — the leg explicitly
   opts *back into* a wider status view, and **nothing opts back out of `resolved_from`**.
5. `_find_existing_entity` (`extractor_v2.py:5769-5788`) then matches by folded name over that row set and
   returns `exact[0]` — and `:6022-6026` binds `resolved[name] = {"id": str(ex.get("id"))}`. Nothing collapses
   through `resolved_from`.
   ⛔ **Correction to this lane's first draft, which said `exact[0]` is "the higher-scoring one". It is not.**
   The rows are accumulated into an **id-keyed dict** (`extractor_v2.py:3076-3081`) and re-listed by
   `.values()` (`:3104-3106` in the degraded branch, `:3118-3120` in the normal one); the code's own comment states it — *"S3's rows are accumulated into id-keyed
   dicts by `search_graph` (**order DISCARDED**)"* (`:2913-2914`). So `exact[0]` is first-**seen across the
   query sequence**, not highest-scoring. And inside one query the two candidates **tie** on score (same folded
   name, same `name`-only index), so their order is decided by `recency_tie_clause` (`search_engine.py:2993`),
   which is **newest-first**. ⇒ **the tie rule predicts the CARRIER is preferentially picked**, not the survivor —
   so D-1 is plausibly *worse* than the neutral statement, for a reason this lane did not originally give.

⇒ Two consequences, both measured-by-reading and **neither measured-by-running**:

- **A mention can bind to a node the merge already retired.** The writer would have hopped to the survivor
  (`entity_identity.py:593-594`); the *prior* path cannot, because it picks an id straight out of FTS rows.
- **A candidate slot can be spent on a retired carrier.** ⛔ **Correction to this lane's first draft, which said
  "one of the three slots … up to a third of the window `#11901` measured at 61.3 %": that conflated two different
  windows and is withdrawn.** `limit=3` is **per FTS query** (`extractor_v2.py:3076`), and the S3 prior set is the
  **union over the whole query budget**, keyed by id (`:3076-3081`) — so the prior set is not `#11901`'s recall@3
  window and its size is not 3. What survives is the qualitative claim and nothing more: **a dead twin occupies a
  slot in some query's 3-row window that a live node could have occupied**, and no counter counts it. **The
  magnitude is unmeasured** — it needs the census in §8.7 item 1.

**This is the `#12120` root at a site no issue names.** It is the same reader/writer disagreement the repo's own
comment rails against — `entity_identity.py:360-361`: *"ONE implementation, because the create path and the read
path must not disagree about who holds a name (review F2)"*.

**Attribution, per the discipline that a peer filing is a process defect.** `#12244` covers the same root at the
**about-attachment (write-side edge) sites** — three of them, with acceptance scoped to *"each of the three sites
maps matched holders through `_collapse_resolved_holders` before attaching"*. It does **not** name this site, and
its fix would not reach it: this is not an attachment site, it is the **prior-set / candidate** path, and its
consequence is a wrong resolution target and a wasted slot rather than a duplicate edge. **Recorded as evidence
there, not filed as a peer** (§8.6).

**Falsifier (one query, no build):** after `apply_entity_merge(candidate=X, survivor=Y)`, call
`tortoise_fts_query(<X's folded name>, entity_type="object", limit=3, include_terminal=True)` and check whether
`X.id` is in the rows. If it is not, D-1 is wrong and this section is dropped.

**Contradiction test:** **no recorded decision reaches this work.** The **text-index** ruling
(`SEARCH-ARCHITECTURE.md:268`) gates the index location and **V1** gates the ***vector*** index
(`STORAGE-ARCHITECTURE.md:1581`); §12.2/§12.2c gate
what earns a vector; §14.3 V3 + the KEEP ruling gate the embeddings' existence; §2.0 gates the record/cache split;
the D-block decisions and §7/§17.4.6 gate the read ledger. D-1 is a completeness defect in the merge's read side.

---

## 8.3 HALF B — how they get the signal, at code level

For each: **fields read · thresholds · band/step count · prompt shape · score combination**, then **which of our
substrates it would consume** and **a one-line applicability verdict**.

### 8.3.1 Zep / Graphiti — `getzep/graphiti@main`

| element | code |
|---|---|
| **pipeline** | `resolve_extracted_nodes` (`node_operations.py:816`) → candidate collection (`:827`) → per-node deterministic resolution (`:850` identity-properties, `:861` exact/fuzzy) → batched LLM (`:872`+) |
| **three candidate sources, unioned** | identity-property lookup (`_collect_identity_property_candidate_nodes`), exact-normalised name (`_collect_exact_name_candidate_nodes`), semantic cosine (`:622-623`) |
| **thresholds** | `NODE_DEDUP_CANDIDATE_LIMIT = 15` (`:67`), `NODE_DEDUP_COSINE_MIN_SCORE = 0.6` (`:68`); fuzzy Jaccard `0.9`, 32 perms, band 4 (`dedup_helpers.py:34-36`); entropy `>= 1.5` (`:31`) over names ≥ 6 chars **or** ≥ 2 tokens (`:80-86`) |
| **band count** | **four rungs, in order**, per node: identity-props → exact-normalised → MinHash/LSH fuzzy → LLM. First rung that answers ends the question; ambiguity at rung 2 does **not** fall to rung 3, it falls to the LLM (`dedup_helpers.py:344-349`) |
| **prompt shape** | `prompts/dedupe_nodes.py` — system: *"NEVER fabricate entity names or mark distinct entities as duplicates"*; user blocks `<PREVIOUS MESSAGES>`, `<CURRENT MESSAGE>`, `<ENTITIES>`, `<EXISTING ENTITIES>`, each JSON. Output model `NodeResolutions{entity_resolutions:[NodeDuplicate{id, name, duplicate_candidate_id}]}`, **`-1` = new** (`:26-39`). Exact-count contract: *"Your response MUST include EXACTLY N resolutions with IDs 0 through N−1"* (`:154` — the `Task:` header above it is `:152-153`; `:146-152` is the duplicate-criteria block, a different instruction) |
| **what the adjudicator can SEE** | `existing_nodes_context` (`node_operations.py:703-712`) = `{**candidate.attributes, candidate_id, name, entity_types, summary[:120]}`. **No neighbours, no degree, no edges. Zero topology.** |
| **batching** | `MAX_NODES = 30` (`:66`); dedupe output capped `min(max_tokens or 16384, 16384)` (`:736`) |
| **model routing** | `ModelSize.small` for attribute extraction (`:1064`) and summaries (`:1243`); the **dedupe verdict call passes no `model_size`** (`:737-745`) → default model. ⚠️ **This is Graphiti's own model-size router, not Jev** — Jev is ours (TypeSafe). ⛔ **Correction to this lane's first draft: it called the wrong phrase "Jev routing" and attributed it to round 2 — and "Jev" appears in neither round.** Measured: `grep -c "Jev" research-brief.md` is **0** at this head **and** **0** on `origin/research/12186-round2-depth`, and `git grep` finds no `Jev` in `research-brief.md` on either branch — the only `Jev` under this directory is the sentence you are reading. The phrase is this lane's own; the substantive correction stands — the dedupe verdict call passes no `model_size` (`:737-745`), so it takes the default model. |
| **how `summary` is produced** | a separate small-model call (`:1230-1260`), truncated `MAX_SUMMARY_CHARS`, then `[:120]` again at prompt time |

**Our substrates:** `name` → `name`; `entity_types` → the label (`:Object`/`:Subject`) plus `object_kind`;
`summary` → **`title`** (our only second text field on an entity); `created_at` → `createdAt`; **`attributes`
(typed identity properties) → we have nothing.**

**Verdict: directly applicable at three of four rungs** — exact-normalised fold, the entropy gate, and
MinHash/LSH blocking are all pure functions of `name` + `canonical_name`, which we have
(`entity_resolver.normalize_entity_name`). The **identity-property rung needs a field we lack.** And the
adjudicator-payload finding cuts against round 2's own recommendation: *Zep does not put topology in the
adjudicator*, so enriching our B1 payload with `title` / `object_kind` / neighbour names is an **extension beyond
the surveyed field** — defensible from ER practice (§8.3.5), not copied from Zep.

### 8.3.2 Hindsight — `vectorize-io/hindsight@main`

| element | code |
|---|---|
| **four parallel arms** | semantic vector; BM25 keyword; **graph link-expansion**; temporal filter. Merged per README/paper and by construction in `fusion.py` (`source_names = ["semantic", "bm25", "graph", "temporal"]`, `:56`) |
| **per-arm cap BEFORE fusion** | `cap_per_source(results, cap)` (`fusion.py:8-26`) — *"so that one over-expanding backend cannot crowd out the others when the merged pool is later trimmed to the reranker's global candidate budget"* |
| **fusion** | **RRF** (`fusion.py:29-109`, `score += 1.0/(k+rank)`, k=60) **and an alternative `interleave_fusion`** (`:112-176`) — see below |
| **what the graph arm queries** | bounded semantic **seeds** (`GRAPH_SEED_LIMIT`, `DEFAULT_GRAPH_SEED_MIN_SIMILARITY`) → three expansions over precomputed links: **entity co-occurrence** (`COUNT(DISTINCT entity_id)`, capped `per_entity_limit` 200), **semantic kNN built at insert time**, **causal** (`causes`/`caused_by`/`enables`/`prevents`, score `weight + 1.0`) — `link_expansion.py:1-20`, SQL `ops_postgresql.py:1069-1109` |
| **intra-arm combination** | additive `entity + semantic + causal ∈ [0,3]`, entity mapped `tanh(count × 0.5)` (`link_expansion.py:246-270`) |
| **rerank model** | cross-encoder, default **`hindsight-ai/hs-rerank-1`** (`reranking.py:312-318`) |
| **post-rerank combination** | **multiplicative**, on the normalised CE score: `combined = CE × recency_boost × temporal_boost × proof_count_boost`, with α = 0.2 / 0.2 / 0.1 → ±10 % / ±10 % / ±5 % (`reranking.py:179-310`). `proof_norm = 0.5 + log(proof_count)/10` clamped to [0,1] — **neutral at 0.5, so the multiplier is 1.0 when the signal is absent** |
| **a documented fallback worth copying** | if the reranker is a passthrough (slim deployments), it seeds CE from **RRF rank** in `[0.1, 1.0]` so the boosts do not become a pure recency sort (`reranking.py:236-256`) |

**⭐ The lever nobody has listed: `interleave_fusion`, and its stated reason is *our* failure mode.**
`fusion.py:112-130`, verbatim:

> "RRF scores a doc by the *sum* of its reciprocal ranks across arms, so a result that is #1 in one arm but
> absent/low in the others gets averaged down. **That is exactly the consolidation-dedup failure mode: the
> near-identical existing observation (the "twin" to merge into) is semantic rank #1, yet shares no source-fact
> graph link and little lexical overlap, so RRF drops it below the recall budget cutoff and the LLM never sees
> it → creates a duplicate.**"

That is the noun matcher's exact shape: the right existing entity at similarity rank 1, with weak lexical and
zero graph support, diluted out of a small window by RRF. ⛔ **`grep -rn interleave tortoise/` → 50 hits**, and
the honest reading of them is narrower than "we have it" and wider than "we don't": only **4** of the 50 are in
`tortoise/retrieval.py` (`:17-18` / `:2225` — *"Budget-capped, **rank-interleaved** reader context (C1 #1745)"*);
the other 46 are concurrency/write-interleaving comments across 25 files (`hosted_api.py` 11, `log.py` 5,
`vector_write.py` 3, `sdk.py` 2, …). The 4 are an interleave of **assembled context blocks**, not of
**retrieval arms**. **No arm-level interleave
exists** (`rrf_fusion` is the only fusion in `search_engine.py`), and the two are different mechanisms that a
lane must not conflate: assembly interleave decides *what order the reader sees*, fusion interleave decides
*which candidates survive the cut*.

**Our substrates:** `embedding` → seeds; `aboutObject`/`aboutSubject` → the entity arm's join; **we have no
precomputed kNN link table and no causal links at all**; we *do* have RRF (`rrf_fusion`) and a built cross-encoder
(`rerank.py::CrossEncoderScorer`, BUILT-OFF) — so Hindsight's **order** (arms → RRF/interleave → cross-encoder →
multiplicative boosts) is the same order `C1` + `A6` already need.

**Verdict: the fusion order and the arm-level interleave alternative are directly applicable** (no new field,
no index move) — ⚠️ **but only on the dedup/noun candidate pool, and that scope is part of the verdict, not a
footnote.** The recorded owner decision of 2026-08-25 makes `{"vector": 1.5}` weighted RRF the **production
default** for `tortoise_fts_query` (`sdk.py:41233-41237`); replacing that fusion on the product search path would
**reverse a recorded decision**, which is a reopen at that decision's home, not an adoption here. **M-12 is
scoped to the pool.** **The graph arm needs a field we lack** — a populated entity join — and additionally needs
a fan-out cap.

### 8.3.3 Mem0 — `mem0ai/mem0@main`

| element | code |
|---|---|
| **how the entity collection is BUILT at `add()`** | `extract_entities(text)` — **deterministic spaCy NER + heuristics, no LLM** (`entity_extraction.py`: `_add_ner_candidates` `:498`, `_add_technical_identifier_candidates` `:528`, `_add_proper_name_candidates` `:543`, `_add_quoted_candidates` `:581`, `_add_topic_phrase_candidates` `:590`, `_resolve_candidates` `:705`; the pipeline calling them in order is `:743-748`). Then `_upsert_entity` (`main.py:605`, exact-normalised match at `:610`): **exact normalised-text match first** (`_existing_entities_by_text`, `_normalize_entity_text`), else a **semantic `top_k=1` match at `score >= 0.95`** (`:614-621` — `top_k=1` at `:617`, the gate at `:621`), else insert. A match appends `memory_id` to `linked_memory_ids` (`:624-629`) |
| **search-side pool** | `internal_limit = max(limit*4, 60)` (`main.py:1655`, again at `:3344`); semantic, keyword, entity searches all sized to that |
| **candidate pool is SEMANTIC-ONLY** | `main.py:1674-1684`, comment *"Step 7: Build candidate set from semantic results"* — the BM25 arm scores rows already in that pool; so does the entity arm. **A keyword-only or entity-only hit cannot enter** |
| **BM25 normalisation** | query-length-adaptive **sigmoid**: midpoint 5 / 7 / 9 / 10 / 12, steepness 0.7 / 0.6 / 0.5 / 0.5 / 0.5 by term count (`scoring.py:16-40`), `1/(1+exp(−s·(x−m)))` (`:43-54`) |
| **the entity boost, exactly** | `main.py:1747-1827`: ≤ 8 deduped query entities → entity store `search(top_k=500)` → skip `similarity < 0.5` → `num_linked = max(len(linked_memory_ids), 1)` → **`memory_count_weight = 1/(1 + 0.001·(num_linked−1)²)`** → `boost = similarity × ENTITY_BOOST_WEIGHT × memory_count_weight`, `ENTITY_BOOST_WEIGHT = 0.5` (`scoring.py:57`) → taken as **`max`** per memory, never summed |
| **score combination** | `score_and_rank` (`scoring.py:60-139`): `combined = min((semantic + bm25 + entity_boost) / max_possible, 1.0)`, `max_possible` 1.0 / 1.5 / 2.0 / 2.5 by which signals are active (`:97-101`) — **additive normalised, not RRF** |
| **the gate** | ⛔ *"Threshold gates the semantic score BEFORE combining — candidates below the threshold are excluded even if BM25/entity would boost them"* (`scoring.py:74-75`, code `:111-112`) |

**The one piece worth copying verbatim:** `memory_count_weight` (`:1815-1817`) — an **inverse-quadratic damping
on how many memories an entity links to**. A hub entity must not boost everything it touches; this is Math-1 in
one line and it is the same idea as IDF, applied to the entity axis rather than the term axis.

**Our substrates:** `content` + `search_keys` → BM25; `embedding` → semantic; `aboutObject` → the entity boost, if
populated; entity `name` + `title` → the entity record's `data`; the reverse `linked_memory_ids` list is a Cypher
reverse hop we already have the shape for.

**Verdict: the SIGNAL is directly applicable, the POOL CONSTRUCTION is not.** Copying Mem0's pool would cap our
recall problem exactly where `#11901` measured it (81/300 absent even at k=15). Copy the boost, keep our union
pool — which is what round 2's **M-11** already says; this round adds the damping formula and the exact gate
position, which M-11 did not have.

### 8.3.4 Mastra observational memory — the counter-case: **no similarity-based retrieval step**

| element | code |
|---|---|
| **there is no retrieval step** | `observational-memory.ts` is 4,723 lines; the **only** occurrence of `embed`/`cosine`/`similarity`/`vector` in it is `:620` — `this.retrievalSearch = typeof config.retrieval === 'object' && Boolean(config.retrieval.vector)`, a passthrough to a *separate optional* tool, not OM's own |
| **what it does instead** | an Observer and a Reflector maintain **one `activeObservations` string** plus a **timestamp cursor** `record.lastObservedAt`; the actor sees observations at the start of the window and live messages at the end (`ARCHITECTURE.md:10-40` — Observer/Reflector defined at `:10-11`, the record fields at `:22-24`) |
| **the only identifier structure** | **anchors** — `[O1]`, `[O1-N2]` line ids in the observation log (`anchor-ids.ts:1-12`). Addressability of *observation lines*, not entity identity |
| **activation** | a prefix operation — `swapBufferedToActive` takes `chunks.slice(0, n)` and moves the cursor to the last activated chunk's `lastObservedAt` (`ARCHITECTURE.md:33-37` — `:76-82` is the activation *steps list*, which names `swapBufferedToActive` at `:80` but neither the slice nor the cursor move) |
| **time-scoped read** | `getObservationsAsOf(activeObservations, asOf)` splits the log on `--- message boundary (<iso8601>) ---` markers and keeps chunks whose boundary date ≤ `asOf` (`observation-utils.ts:1-40`) |

**Our substrates: none of the entity ones.** No `name`, no vector, no edge.

**Verdict: not applicable to candidate generation, and it should not be** — it is a **context-construction**
strategy, not a retrieval one. Its relevance to this epic is as the bound, not the answer, which is why our own
`STORAGE-ARCHITECTURE.md` §12.2b already carries the same direction (*"write less, and write things worth
keeping"*). **Adopting it would reverse none of the records listed at the end of §8.5** — which is exactly why it is worth carrying as a
*scope* note and not as a lever.

⛔ **Two overclaims this lane made on first draft, withdrawn** (raised by review, verified here):

1. **"no ranking at all" → "no similarity-based retrieval step".** The evidence — that the only
   `embed`/`cosine`/`similarity`/`vector` occurrence in 4,723 lines is `:620` — **cannot** establish "no ranking":
   a recency or position ordering contains none of those tokens, and OM's own activation *is* a recency ordering
   (`chunks.slice(0, n)` + a `lastObservedAt` cursor, `ARCHITECTURE.md:37`). The supportable claim is the
   narrower one: **the identity/candidate-generation problem never arises, because OM never asks a query.**
2. **"the strongest no-retrieval result on the benchmark" is not a claim this round established.** The figures
   (84.23–94.87 % on `longmemeval_s`) are **inherited from round 1's §5.1**, which read them from
   `mastra.ai/research/observational-memory`; §8.7 item 5 already declares they were **not re-read this round**.
   It is stated here as *round 1's finding, on a vendor page, not re-measured by us* — and it is **not** a reason
   to re-prioritise, which is round 1 §5.1's own explicit caution.

### 8.3.5 The cross-cutting correction this round adds

Round 2's cross-cutting finding — *"at the top of the board, entity identity is DECLARED or DERIVED, not
MATCHED"* — survives, and the code adds its mechanism: **the four systems differ in *where* they spend the
identity question, and none of them spends it on topology.**

| system | where the identity decision happens | topology consulted |
|---|---|---|
| Zep | before persistence, four rungs, LLM last | **never** (adjudicator sees `name`/`type`/`summary[:120]`/attributes) |
| Mem0 | at `add()`, deterministic NER + a 0.95 exact/semantic upsert; **nothing is merged** | **never** (a boost, not a verdict) |
| Hindsight | at retain (entity resolution) — and at **query** time via shared-entity counting | ✅ **at retrieval**, as `COUNT(DISTINCT entity_id)`, capped |
| Mastra | nowhere — no entity layer | n/a |
| *Neo4j GDS* | n/a (a library) | ✅ **pairs scored on neighbour sets**, O(n³) |

⇒ **The only place in the surveyed field where topology is a *signal* is Hindsight's expansion arm and the GDS
library — and both use it to *generate or rank candidates*, never to *decide identity*.** That is the shape B2
should take, and it is the shape §8.1.5 already settled.

---

## 8.4 New levers this round names (M-12 … M-14)

| # | lever | why it is not in any inventory | falsifier |
|---|---|---|---|
| **M-12 ⭐** | **Interleave fusion for the dedup/noun candidate pool** — `interleave_fusion` (`fusion.py:112-176`), Hindsight's own stated fix for the *consolidation-dedup* dilution RRF causes | the inventory has no fusion-order lever at all; our `rrf_fusion` is the diluted shape, and Hindsight documents the failure in the exact words that describe ours | an interleaved pool does not lift candidate recall@3 on the `#11901` population over `rrf_fusion` → the dilution is not our bottleneck and the lever is dropped. ⛔ **Contradiction: the recorded 2026-08-25 production default `{"vector": 1.5}` weighted RRF (`sdk.py:41233-41237`) IS a recorded decision this would reverse on the product search path — so M-12 is a candidate for the *dedup candidate pool only*, never for `tortoise_fts_query`'s product path, and shipping it globally would be a reopen** |
| **M-13 ⭐** | **A per-entity fan-out cap, before any shared-neighbour score** — `per_entity_limit` 200 (`link_expansion.py:10`), `degreeCutoff` (GDS) | nobody has proposed the cap because nobody has proposed the join; but our `:Object` is minted `MERGE (o:Object {name:$name})`, so a bare definite description is a hub and an uncapped self-join is quadratic over the label | the largest entity's fan-out on a real graph is < 50 → the cap is decoration, ship the score without it |
| **M-14** | **The `memory_count_weight` damping, copied exactly** — `1/(1 + 0.001·(n−1)²)` (`main.py:1816`; `num_linked` is `:1815`) | M-11 names "entity boost" but not its damping, and without it an entity linked to thousands of memories boosts all of them; the formula is the difference between a signal and a wash | removing the damping changes no ordering on the `#11901` population → the hub-collision it prevents does not occur here |
| **D-1** | *(defect, not a lever)* the merge's read-side incompleteness — §8.2 | — | §8.2's one-query falsifier |

---

## 8.5 Contradiction test — one row per finding

| finding | recorded decision it would reverse | verdict |
|---|---|---|
| §8.1.1 `resolved_from` is a merge in identity and a duplicate in retrieval | **no recorded decision reaches this work** | statement about code |
| §8.1.1b `aliases[]` is the only non-retiring pointer, and pointer ≠ link | **no recorded decision reaches this work** — it informs the `#2730` owner question, it does not answer it | evidence for that home |
| §8.1.2 `'link'` is read by nothing *value-aware* — and it IS writable, which is worse than unread (this is §8.1.2's own verdict, **not** the retracted "neither written nor read") | **no recorded decision reaches this work** | statement about code |
| §8.1.3 M3's six requirements | **none reversed** — the **text-index** ruling (`SEARCH-ARCHITECTURE.md:268`) gates the index *location*; M3 **is** that ruling's own named measurement (`:517`), pre-authorised inside `#12186`. ⚠️ **Not V1**, which is the *vector*-index question (`STORAGE-ARCHITECTURE.md:1581`); this only narrows M3's metric set | **obeyed** |
| §8.1.4 Zep: name shingles, no edge, deterministic-first | **no recorded decision reaches this work** | statement about a third party |
| §8.1.5 shared-neighbour scoring is Hindsight's shape, gated on `aboutObject` fill | **no recorded decision reaches this work.** It moves nothing about the vector (§12.2c), the index (V1), the embeddings (V3/KEEP) or the record/cache split (§2.0) | candidate, gated on `#4211`'s substrate |
| §8.2 D-1 the merge does not de-duplicate retrieval | **no recorded decision reaches this work** | **defect** |
| §8.3.1 Zep's adjudicator carries no topology | **no recorded decision reaches this work** | corrects round 2's *"context in the adjudicator is free"* — it is free to *add*, and it is an extension, not a copy |
| §8.3.2 interleave fusion (M-12) | ⛔ **YES, on the product search path**: the owner decision 2026-08-25 making `{"vector": 1.5}` weighted RRF the production default (`sdk.py:41233-41237`). **M-12 is therefore scoped to the dedup candidate pool; applying it to `tortoise_fts_query` would be a reopen, argued at that decision's home** | **scoped down** |
| §8.3.3 Mem0's pool vs its signal | **no recorded decision reaches this work** | M-11 already carries it |
| §8.3.4 Mastra: no **similarity-based** retrieval step (the narrower claim — see §8.3.4's two withdrawals) | **it touches none of the records listed at the end of this section**; the D block's suspension (§7/§17.4.6) is untouched | scope note only |
| §8.3.5 topology never decides identity in the field | **no recorded decision reaches this work** | supports B1 staying a *verdict*, not a similarity threshold |

**Not reversed, and checked explicitly:** the **KEEP ruling** (`#11395`), **V1**, the **text-index-stays-until-M3**
ruling (`SEARCH-ARCHITECTURE.md:268`), **§12.2/§12.2c**, **§14.3 V3**,
**§2.0**, the **no-eviction / projection-window** rule, and **§7 + §17.4.6's ban on the read ledger as an
admission-control signal**. This round proposes nothing that touches any of them, and §8.2's D-1 is a completeness
fix on the merge, not a change to what is stored.

---

## 8.6 What changes for `#12186`

1. **Round 2's `resolved_from` conclusion survives; its wording does not.** "Retires the node in every reader" →
   **"retires it in every identity read that decides *which entity a name lands on*, and in none of the retrieval
   readers."** ⛔ That is **eight of the nine** (see §8.1.1's table): the ninth, `_name_holder_ids_same_entity`
   (`entity_identity.py:410`), deliberately does **not** collapse, because a held name must not read as unheld —
   so "every identity reader" would overstate by one. N-4's dispatch readiness is
   unchanged (still blocked on `SAME_AS`), and the redirect is now a *worse* substitute than round 2 said, because
   it also leaves a visible duplicate.
2. **`dedup_reviewed='link'` is worse than inert, and the fix is a READER, not a writer.** The write is one
   `update_entity` call (`sdk.py:51584`, unconstrained — see §8.1.2), so "nothing writes it" is false; and the
   content review queue filters `dedup_reviewed IS NULL` (`:24256-24257`), so writing the value **silently removes
   the candidate from review with no verdict applied**. Record both — the second is a hazard, and it is the reason
   a `'link'` band must ship its reader in the same change as its writer.
3. **M3 gains four requirements, loses none** (§8.1.3): isolate the leg, measure recall@k not only nDCG, state the
   `threshold` arm, state the tie rule. The epic's DoD line (`SEARCH-ARCHITECTURE.md:517`) should gain "the FTS
   leg in isolation, recall@k" and its pins refreshed — **the pins, not the content**.
4. **D-1 is dispatches-worthy and is not in any child.** It belongs on `#12244` as a comment (same root `#12120`,
   a site `#12244` does not name) and, if the owner wants a lane, as a fourth site in `#12244`'s acceptance list —
   **not as a peer issue.**
5. **B2 settles a third time, and this time on code:** shared-entity co-occurrence over a seed set, **capped per
   entity**, gated on `aboutObject` fill (`#4211`). Not pairwise typed-edge Jaccard (round 1, dead); not an
   overlap score for the verdict (round 2, dead).
6. **A3 stays closed**, and round 2's *"context belongs in the adjudicator, and it is free"* becomes sharper: it is
   **free to add and it is an extension** — Zep's adjudicator sees no topology at all, so no surveyed system
   establishes that adding it helps. The evidence for it is ER practice (§8.3.5), not a comparable.
7. **Three new levers, each cheap and each measured elsewhere:** **M-12** (arm-level interleave fusion,
   **pool-scoped** — see §8.3.2's verdict and §8.5's contradiction row), **M-13** (a per-entity fan-out cap, a
   precondition of any neighbour score), and **M-14** (Mem0's `memory_count_weight` damping, folded into M-11).

---

## 8.7 What this round could NOT establish — declared

1. **No graph census.** `Object.title` fill rate (**M-8**), `resolved_from` chain lengths / cluster sizes
   (**M-10**), the `aboutObject` edge count (**D-1** and every B2 reading), and **D-1's own falsifier** all need a
   tenant. `#4211`'s census is **cited, not re-measured**.
2. **D-1 is a code-path reading, not a run.** The chain is complete and every hop is pinned, but no test executed
   it. Its falsifier is one query (§8.2) and a lane should run it before treating D-1 as a P0.
3. **`aliases[]`-as-link is traced, not exercised.** I followed the union to `refuse_if_ambiguous`
   (`entity_identity.py:1003`) and read the refusal; I did not run a write against a linked pair.
4. **Zep's LLM dedupe quality is still unmeasured anywhere** — no isolated number exists in its repo or paper.
   Unchanged from round 2, and it is the number B1 most needs.
5. **Every performance figure is its vendor's or its paper's**, on its own harness: Mem0's 2026 numbers
   ([mem0.ai](https://mem0.ai/blog/state-of-ai-agent-memory-2026)) are self-reported and the vendor states the
   managed platform carries *"proprietary optimizations not available in the open-source SDK"*; Hindsight's 83.6 %
   (20B) / 91.4 % (Gemini-3 Pro) are from [its ACL 2026 demo](https://aclanthology.org/2026.acl-demo.27/); Mastra's
   84.23–94.87 % are inherited from round 1's §5.1 and were **not re-read** this round. **None is comparable to
   `#11901`'s 61.3 % / 73.0 %**, which is candidate recall@k on *our* corpus.
6. **`#8486` (the BM25 arm's latency) is still unexecuted**, so M-7 has no number. Not this lane's to take.
7. **`#11901` was not re-measured**, per the brief. Every use of 61.3 / 73.0 / 27 % is a citation.
8. **Mastra's retrieval passthrough at `observational-memory.ts:620` was read, not traced** — I did not follow
   `config.retrieval.vector` into the tool it names, so "OM has no retrieval of its own" is asserted for OM's
   module and not for every configuration Mastra ships.
9. **⛔ The four external sources are pinned to floating `main` refs, not to a commit sha** —
   `getzep/graphiti@main`, `vectorize-io/hindsight@main`, `mem0ai/mem0@main`, `mastra-ai/mastra/main`. This
   document's own rule (`:27`) is that a pin must name its head; these do not, **and the cost is already realised
   for one of them**: at `vectorize-io/hindsight@main` today, `hindsight_api/engine/search/fusion.py`,
   `hindsight_api/engine/memories/pg/link_expansion.py`, `engine/search/reranking.py` and
   `engine/db/ops_postgresql.py` all return **404** (`hindsight-api/` now
   holds only `README.md` and `pyproject.toml`), so **M-12's sole source (`fusion.py:112-176`) and §8.1.5's SQL
   (`ops_postgresql.py:1069-1109`) cannot be re-derived from `main`** — they are recorded as read on 2026-11-12,
   and a later lane must either locate the commit or re-read the moved tree. The other three still resolve and
   their load-bearing claims were re-checked: Zep 400 L with `_FUZZY_JACCARD_THRESHOLD = 0.9` `:34`, 32 perms
   `:35`, band 4 `:36`, entropy 1.5 `:31`; Mem0 `main.py` 3,916 L with `memory_count_weight` `:1815-1817` and
   `ENTITY_BOOST_WEIGHT` `scoring.py:57`; Mastra `observational-memory.ts` 4,723 L. **Record a sha at read time;
   this round did not.**

---

**Refs:** `#12186` (epic) · `#12189` (round 1) · `#12270` (round 2 PR) · `#11901` (measurement of record) ·
`#11395` (KEEP ruling) · `#2730` · `#4211` (substrate census) · `#8486` · `#12244` / `#12120` (D-1's root) ·
`#12184` · `#2518` · `#2513` · `SEARCH-ARCHITECTURE.md` §6.2/§7 · `STORAGE-ARCHITECTURE.md` §12.2b/§12.2c/§14.3/§17.4.6
