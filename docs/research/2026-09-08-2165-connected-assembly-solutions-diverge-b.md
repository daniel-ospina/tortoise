# Solution Diverge — Agent B · #2165 Connected Assembly (what/when/why)

**Date:** 2026-09-08 · **Mode:** solution-diverge (no winner — 3 distinct architectures)
**Role contract:** generate architectures independently; if any overlaps another agent's set, differentiate on architecture. No winner picked.
**Problem (human-approved):** deterministic (zero-added-LLM) assembler on the product ask lane. Question → resolve subject(s) → walk connected subgraph (Object state/lifecycle + dated Events + aboutObject-linked Points w/ EP + provenance) → render ONE dense structured block (state + timeline + evidence) → existing single reader LLM. Flag-off additive (byte-identical existing path when off/unrouted). Acceptance = synthetic multi-session graph assembly tests + no regression on point-only retrieval. NOT the 13-Q measurement lane (#2578).
**Evidence base:** `docs/research/2026-09-08-2165-connected-assembly-synthesis.md` (constraints 1–9), the three `2026-09-08-*` research passes, `docs/product/answer-surface.md`, `docs/ONTOLOGY.md`, `gh issue view 2165` + comments, targeted code reads (verified inline as **[V]** = verified in source on this worktree).

---

## Verified code facts every architecture must respect (read before options)

- **[V] `ask()` order** — sdk.py `ask()`: validation → retrieval (`tortoise_fts_query`, `entity_type="point"` only, `include_terminal=True`) → `annotate_ask_hits` → `dedup_pool` (per-session cap 3) → A5 evidence boost → A7 rerank (off) → `assemble_context` (8k-token estimate / 32 KiB byte caps, whole-hit drop) → **`detect_question_type` runs AFTER assembly** (sdk.py ~12812) → `system_prompt_for(qtype)` picks only a prompt fragment. Type never gates retrieval today.
- **[V] `detect_question_type`** (tortoise/reader.py) — regex-only, ordered TR→KU→MS→SSP→None; compare/ordering/interval shapes are only partially covered (the `between…and…` TR rule; no "which came first"/"earlier than"/"days between" rules). Type → prompt fragment only.
- **[V] ask retrieval is point-only** — the lane never queries Object/Event nodes, never traverses aboutObject (search_engine.py has zero aboutObject refs; `_ABOUT_TYPES` in sdk.py:13325 is a different surface). SearchResult `entity_type="object"` decoration exists (sdk.py ~12121 returns id/name/objectKind/status/supersededBy) but ask never uses it.
- **[V] Timeline/state primitives already stored**: Points carry `when` (occurrence anchor), `validFrom/validTo/expiredAt`, status, `search_keys`; Objects carry `name`, `objectKind`, `status` (fold cache of ObjectRegistered/ObjectSuperseded), `supersededBy`; Events key on **`eventId`** (not `id`), carry `startedAt/endedAt`, `subject`, `eventKind`. Supersession folds land via `apply_supersessions` → `ObjectSuperseded` + `_fold_object_superseded` (commit_ops.py:297+). `belief_timeline` (sdk.py:4986) is the narrow precedent: decision Points aboutObject-connected to a topic, ordered by validFrom, CORRECTS re-attach of superseded priors.
- **[V] `SubgraphExpander`** (tortoise/ranking.py:1054+) is **blind BFS** over ALL edge types (`completeness="full"`), id-keyed on `n.id` (Event nodes key on eventId → event seeds need an id-field mode), with a max_nodes cap and NO date/validity/relevance logic, NO typed slice budgets. R4 `expand_structural_hops` expands only IMPL|NAND 1–2 hops.
- **[V] C2 `_entity_key_expansion_pass`** (sdk.py ~12390) is the in-tree precedent for **subject resolution via the Object-name FTS spine** (`run_fts_query(entity_type="object")`) + a batched aboutObject key harvest. Gated additive, fail-open. Only exists for the eval entity-key arm (#2518) — the product lanes do not run it by default.
- **[V] Embedded/no-FTS posture** — FTS+vector index creation is FalkorDB ≥ 4.x (`projection/__init__.py` ~2140; below-4 logs "FTS and vector indexes will be skipped"); the degraded fallback inside `tortoise_fts_query` is **Point-only** (`query and entity_type == "point"` → TF-IDF snapshot/legacy; any non-point entity_type with empty raw_results returns `[]`). An Object-name FTS resolution path is therefore docker/server-only in the worst case; embedded needs an exact-name/id-keyed path (index reads work everywhere) or label-CONTAINS/vector fallback. Vector search on embedded is brute-force but works for any entity type with embeddings.
- **[V] Evaluator machinery already separate** — tools/longmem_eval/retrieve.py has its own point+event union for TR questions, `detect_time_constraint` (D5 "between…and…"/"ago"), `_apply_time_window`, per-session caps (rerank cap 2), `tr_top_k=12` flood control. Eval does NOT run the product ask lane's pipeline for retrieval; it re-exports the product reader prompt (drift-free reader, different retrieval).
- **[V] Answer-surface contract** (answer-surface.md) — 12-field response; `evidence` ≤ 32 KiB / ≤ 8000 est. tokens enforced at assembly; `question_type` closed enum (any other value → 400); `question_date` resolved default server-now-UTC; abstention is the reader's decision; superseded points included with `[SUPERSEDED BY]` markers (include_terminal=True) today. W4 why-layer (#2101) is the precedent for **additive flag-off keys** (`why` absent unless env flag ON).

---

## OPTION B1 — Standalone shape-router + new Connected-Assembly Pass (CAP); block REPLACES pool on fired shapes

**Name:** "Two-lane routed read: CAP branch" (pre-router architecture).

### Description
A **new deterministic shape classifier lives BEFORE retrieval** (not inside the qtype enum — a standalone pre-router with its own closed shape set) and, when it fires one of the assembler-owned shapes, `ask()` takes a **separate retrieval+render branch**: subject resolution → typed connected walk → one dense block that **replaces** the point pool as the reader's evidence. When it does not fire (or the flag is off), the existing point-only path is **byte-identical** (retrieval never started, so nothing downstream changes). `question_type` on the wire stays the existing enum — the pre-router is internal, so the answer-surface contract is untouched. Eval consumes the same CAP through the SDK (the v2-lane retrieve path calls one shared function), so assembly is tested once on the synthetic multi-session graph and re-used by both lanes.

### Files touched
- New `tortoise/assembly.py` (classifier + resolver + walker + renderer) — the only new module; all other edits are call-site wiring.
- `tortoise/sdk.py` `ask()` — a pre-retrieval branch: after validation, run the shape classifier; if fired and `TORTOISE_ASK_CONNECTED_ASSEMBLY` (or product default ON), run CAP and skip steps 2–4 (retrieval/dedup/boost/rerank); reuse step 5's qtype detection and step 6's reader unchanged; evidence = CAP block.
- `tortoise/reader.py` — no change to `detect_question_type`/enum; optionally add a connected-mode reader fragment later (not required v1).
- `tortoise/retrieval.py` — reuse `_validity_marker`, `estimate_tokens_ask`, the 32 KiB byte-cap discipline (import, not fork).
- `tools/longmem_eval/retrieve.py` — eval v2-lane arms CAP for its synthetic multi-session runs (env-gated), replacing the ad-hoc point+event union when the graph has Object structure.
- New `tests/test_assembly_*.py` — synthetic multi-session graph fixtures (v2-lane shape: Objects + aboutObject + dated Events + Points w/ EP), unit + integration; point-only regression harness (existing ask tests must pass byte-identical with flag OFF).

### Architecture
1. **Pre-router** (regex + closed-form morphology, ~zero cost): shapes = `current-state` ("what is the status/state of X now"), `ordering` ("which came first / earlier than / before-after with two subjects"), `interval` ("days between / how long between"), `date-lookup` ("what happened on D", "status on D"). Compare/ordering morphology is template-detectable (TEQUILA/TempQuestions lineage — decomposition doc §1). Anything else (incl. preferences, single-session, vague) → no fire → byte-identical legacy ask.
2. **Subject resolution** (constraint 7, recall-first): deterministic only — Object-name FTS (`run_fts_query(entity_type="object")`) capped, exact-name/id match first, `search_keys` aliases second; multi-candidate resolution keeps ALL plausible Objects with match-confidence tags (over-resolution is the evidence-backed safer failure); **LLM fallback reserved for a documented follow-up**, never v1 (keeps the zero-LLM hot path hard).
3. **Typed walk** (constraint 4 — never blind BFS): per resolved Object, depth ≤ 2 over typed edges only: `(Point|Event)-[:aboutObject]->(Object)`, `(Event)-[:produces|uses]->(Object)`, and the supersession/CORRECTS chain on Points + Object `supersededBy`. IMPL/NAND operators only when the shape asks why (evidence) and the walker follows the epistemic chain **in-chain-first** (GraphRAG-local pattern), hub-damped (Mem0 shape) per-Object.
4. **Slice builder** — three typed slices with independent caps under ONE hard token budget (~8–12k total, constraint 5): **state** (Object status/lifecycle as-of question_date; superseded excluded by default, shown with explicit markers in the history line), **timeline** (dated spine: Events + Points dated by `when`, fully date-sorted, ISO dates; both halves of a compare by construction — the classifier guarantees per-shape slice selection), **evidence** (aboutObject points with EP confidence + provenance quote, top-by-EP/recency, verbatim not summarized, constraint 5).
5. **Render** — one dense block reusing `_validity_marker`-style annotations, under the existing byte/token caps; hand to the SAME reader call (`system_prompt_for(qtype)`); qtype for a fired shape maps to the closest existing fragment (TR/KU) or None (generic) v1.

### Risks
- **Classifier precision is load-bearing**: a wrong fire routes a preference question into a state block. Mitigate: fired-shape + "no resolvable Object → fall back to legacy lane" (resolution failure must degrade to the existing path, never to an empty answer).
- **Replacing the pool loses the point-only recall complement** the RRF lane provides for fuzzy questions that also carry the shape markers. Mitigate: only replace for shapes where the deterministic walk is the answer (current-state/ordering/interval); keep the option to append the top legacy hits below the block behind a knob.
- Two retrieval implementations to maintain (legacy RRF + CAP) — a real surface cost; the pre-router keeps them from interleaving.
- Fired but subject-less graphs (deterministic eval lane writes zero Objects) → CAP must no-op to legacy, silently (measured lane is #2578's problem, not this one, but the no-op guard is the same).

### Tradeoffs
+ Cleanest byte-identical guarantee (retrieval not even started on the legacy path when unrouted; flag-off is trivial).
+ Zero-LLM hot path is airtight (no classifier LLM, no resolution LLM).
+ Block placement clarity: reader sees exactly ONE assembled structure for the shapes that need it.
− New classifier surface to tune (false-fire risk is a reader-quality risk).
− Duplicated retrieval engines live side by side.
− Only fires on detectable morphology — the assembler's value is unavailable to a non-morphological question that would still benefit from connected evidence (v1 scoping choice).

### Best-fit if
The team wants the strongest possible backward-compat story and the assembly surface narrowly scoped to the template-detectable temporal/state shapes (ordering/compare/interval/current-state/date-lookup) with eval sharing the exact product function. **Differentiator: pre-retrieval standalone router + pool-replacing branch + new typed walker module.**

### Constraints 1–9 compliance
1 ✅ fully date-sorted ISO spine · 2 ✅ state-then-evidence two layers · 3 ✅ supersession explicit, validity math on status at render · 4 ✅ shape→slice relevance gate + typed edges ≤2 hops, hub-damped, in-chain-first — no blind BFS · 5 ✅ per-slice caps under one hard budget; verbatim evidence · 6 ✅ compare/ordering/interval are first-class classifier shapes; both subjects fetched; date math out of reader (ordering precomputed by the sorter; interval diffs precomputed) · 7 ✅ recall-first resolution, confidence tags, multi-candidate admit, NO LLM fallback in v1 (documented follow-up) · 8 ✅ shared CAP = eval + product assembly tested once; evidence admission measurable · 9 ✅ no type-blind BFS, no embedding gate on graph-certain hits, no community summaries, no LLM consolidation.

---

## OPTION B2 — Fold-time Lifecycle Projection Store; read = pure function of (question_date, snapshot) — no per-query walk

**Name:** "Snapshot-diff assembler" (precompute architecture).

### Description
Move the assembly's graph work to **write/fold time**: extend the existing Object fold machinery (#2242) to maintain a **per-Object materialized lifecycle snapshot** (folded status sequence + validity windows + dated spine rows + evidence point ids). The read path becomes an **id-keyed lookup + pure render**: resolve the subject from the question deterministically, read the snapshot, and render state+timeline+evidence as a pure function of `(question_date, snapshot)` with interval math and zero BFS, zero FTS dependency, zero embedding gate. Because snapshots are keyed by Object id and stored on the Object node (or a sibling Meta/index record), the path **works identically on embedded and docker** — it never needs the docker-only Object FTS or per-query graph traversal. This is the only option of the three where "assembly" is not a retrieval event at all — it is a cache read + a date computation.

### Files touched
- `tortoise/projection/__init__.py` or new `tortoise/assembly_snapshot.py` — snapshot schema + maintenance.
- Fold writers: `commit_ops.py` (`apply_supersessions`/`_fold_object_superseded`), Object create path (`ObjectRegistered`) — each fold/register updates the Object's snapshot (single writer discipline: fold code already serializes Object lifecycle).
- `tortoise/sdk.py` `ask()` — pre-retrieval subject-name/id check against the snapshot store; when present and the question carries a state/temporal shape → snapshot render path (replaces pool, same reader call). Legacy otherwise.
- `tortoise/retrieval.py` — reuse caps/marker rendering.
- Eval `tools/longmem_eval/retrieve.py` — v2-lane reads the same snapshot store for the synthetic multi-session graph (its Objects get snapshots at ingest).
- Tests: fold-time snapshot unit tests (register/supersede/re-open sequences), read-time pure-function render tests, embedded-mode integration (the no-FTS claim is directly testable), point-only regression (flag-off).

### Architecture
1. **Snapshot schema (per Object, structured, NOT an LLM summary — constraint 9):** `{object_id, name, object_kind, states: [{status, valid_from, valid_to|open, superseded_by_id, folded_at}], spine: [{event_id|point_id, kind, date, source}], evidence_ids: [point ids with EP], updated_at}`. The **spine source-of-truth is resolved at fold time** by the fold code itself (it sees the ObjectRegistered/ObjectSuperseded events + the session's dated events), eliminating the read-time "which spine is canonical" question (the scout's timeline-spine tension dissolves because the canonical merge happened at write where the lane's own events are visible).
2. **Date precedence is fold-time normalized** — each spine row carries ONE `date`: `startedAt` on the Event when present, else Point `when`, else the fold's `createdAt`/session anchor. Relative dates never stored unresolved (Mem0 write-time pattern; decomposition doc §3).
3. **Read = pure function** — subject resolution (exact Object id from name/search_keys deterministically; docker gets the FTS spine as an optional resolver, embedded gets exact-name index read — both id-keyed after resolution); then snapshot fetch (id key) → validity-window math (`states` filtered by open-window/as-of containment; superseded default-excluded, history retained as-of) → spine rows date-sorted → evidence ids annotated with EP (`annotate_ep_batch` id-keyed — works on embedded) + provenance quote → render under the same byte/token caps.
4. **As-of semantics** fall out of interval math over `states` rows; point-in-time restore = filter the same rows (Graphiti interval-containment pattern) — no new machinery.
5. **Budget** is static by construction: per-slice caps truncate the snapshot's stored lists (whole-row drop), never a mid-row split.

### Risks
- **Dual source of truth** — the snapshot can drift from the live graph if any Object-lifecycle writer bypasses the fold (residual classes documented in ONTOLOGY §4.3 durability notes: unjournaled/legacy producers, EventAPI `add_object` under non-canonical ids, delete→recreate resurrection). Mitigate: snapshot is a **cache derived from the fold**, rebuilt idempotently on read-miss (lazy materialization: read the fold source once and write the snapshot) — same cache doctrine as Object.status (§11).
- **Fold coverage gaps** — lanes that write points/events WITHOUT lifecycle folds leave stale or empty snapshots. Mitigate: snapshot update is also triggered by the point/event write choke points that already wire aboutObject, and read-miss falls back to a **one-time fold-derived build** (deterministic, same code path).
- Precompute adds write-path latency (bounded: per-fold row append, no LLM, no re-embedding).
- Older graphs (pre-#2194 journals) lack folds → snapshots built on demand from whatever the fold can see; honest absence beats silent wrong state.

### Tradeoffs
+ **Cheapest and most deterministic read path of the three**: no BFS, no FTS dependency, no per-query walk — p95 dominated by one id-keyed read + EP annotation; embedded-native.
+ "As-of"/point-in-time restore is nearly free (interval math over stored rows).
+ Write-time canonicalization settles spine/date precedence once, for every consumer.
− Write-path coupling: the fold code (already intricate — #2193/#2194/#2309 hardening history) grows a second responsibility.
− Drift/staleness machinery adds invariants to defend (read-miss rebuild, fold trigger coverage).
− Not a "connected" discovery mechanism: questions that need an Object the question never names can't be served by snapshot reads alone (needs the resolver + optionally the legacy pool complement).

### Best-fit if
The team values a hard deterministic p95 envelope and embedded parity over graph-walk elegance, accepts write-side maintenance, and wants as-of restore for free (record-axis #2349 contract: whole-graph-state-as-of reuses the same snapshot store per topic). **Differentiator: the ONLY option with zero per-query graph walk; assembly cost moved to the fold; pure-function render; embedded-no-FTS native by construction.**

### Constraints 1–9 compliance
1 ✅ date-sorted ISO rows at render (sort is a pure function) · 2 ✅ state rows + dated spine = the two-layer convergent shape · 3 ✅ supersession is the fold's own product (ObjectSuperseded rows), explicit, validity-windowed, open-window = active (Mem0/Graphiti rule) · 4 ✅ no walk at all → no BFS poison; per-Object snapshot IS in-chain priority; question gate = shape + resolver presence · 5 ✅ static per-slice caps over stored rows, verbatim evidence, budget well under the refusal band · 6 ✅ both halves of a compare = two snapshot reads (still deterministic); ordering/diffs precomputed at render; relative dates resolved at fold · 7 ✅ recall-first resolver (exact name/alias → candidates tagged); no LLM resolution v1 · 8 ✅ eval + product read the same store; admission measurable per snapshot slice · 9 ✅ no blind BFS, no embedding gate, no community summaries (snapshot is raw fold-derived structure, explicitly NOT an LLM rollup — the anti-pattern register is about LLM/community precompute, which this is not), no LLM consolidation at write (append-only folds preserved).

---

## OPTION B3 — Reuse lane: retrieval-first re-route + typed `SubgraphExpander` mode + additive Evidence-Pack section

**Name:** "Pool-seeded AEP append" (minimal-touch / surgical architecture).

### Description
Keep `ask()`'s **existing order and existing retrieval** (steps 2–4 untouched, byte-identical). After the pool is assembled, a **graph-presence gate** asks: do the retrieved points share aboutObject edges to ≥ 1 Object, with ≥ 2 distinct dated events/points for that Object? If yes (and the flag is on), run the EXISTING `SubgraphExpander` in a new **typed + dated expansion mode** seeded by the aboutObject Objects discovered from the pool, and emit a **machine-readable Assembled Evidence Pack (AEP) object** rendered as an **additive section ABOVE the existing point evidence** (the legacy pool stays below — the reader sees the connected block first, then the rank-ordered points; lost-in-the-middle ordering, constraint 5 §graphrag-5). No new subject resolver (subjects come from the pool's own aboutObject joins — works without Object FTS, embedded-safe: point retrieval + id-keyed Object reads), no pre-retrieval classifier (the gate is graph structure, not morphology). The AEP object is the **shared consumer contract** (axis idea 1): the same dict the product renderer consumes is returned to eval/recall callers for the evidential admission measurement and future surfaces.

### Files touched
- `tortoise/ranking.py` — extend `SubgraphExpander` with `edge_types`/`rel_pattern` override (already parameterized via `rel_pattern`), an **eventId-aware id-field mode** (Event nodes key on eventId, not id — verified), and optional date/validity filters; keep default behavior byte-identical (all existing callers untouched).
- `tortoise/assembly.py` (small) — pool→Object gate, AEP struct, slice renderer reusing `retrieval._validity_marker`/caps.
- `tortoise/sdk.py` `ask()` — after step 4 (assembly), a new step 4.5: run the presence gate; if fired, expand + render AEP, **prepend** to `evidence` (the byte caps now cover block + pool together — the additive section must fit the SAME 8k/32 KiB envelope, so the pool's own budget shrinks by the block's cost when fired); flag-off default per product decision.
- `tools/longmem_eval/retrieve.py` — eval consumes the AEP object directly (does NOT need to re-render; measures per-slice admission on the synthetic graph).
- Tests: SubgraphExpander typed-mode unit tests (old callers byte-identical), presence-gate integration, additive-budget test (block + pool under one cap), point-only regression with flag off.

### Architecture
1. **Presence gate (the relevance gate, constraint 4)** — pool points (already top-k by RRF+boost) → one batched Cypher: their `-[:aboutObject]->(Object)` fan-out; keep Objects with ≥ 2 dated items in-pool (or 1 Object + supersededBy chain). **No gate fires → zero change to the existing evidence** (byte-identical). This makes near-relevant distractor admission structurally unlikely: assembly only triggers when the rank-ordered pool already proves multi-event connected structure for a subject — the pool is the gate.
2. **Typed expansion** — extend `SubgraphExpander.expand(seeds=object_ids, rel_pattern=":aboutObject|:produces|:uses", ...)` — a direction I verified is a one-line parameter today (`rel_pattern` already exists for core mode; adding about*/produces/uses is the same mechanism), plus eventId-mode neighbor matching for the Event spine, plus a date filter (only rows within [question_date − δ, question_date] for current-state; full for as-of/history).
3. **AEP object** — `{subject_objects:[{id,name,status,superseded_by,validity}], state_slice, timeline_slice (sorted dated rows), evidence_slice (point ids + EP + quote + supersession markers), meta:{gate_fired, slices_truncated, item_counts}}` — deterministic, zero LLM. The product renderer and the eval admission measurer consume the SAME dict (one renderer, multiple consumers — axis idea 1 fully realized).
4. **Block placement = additive prefix** — rendered under the shared `render_context` budget mechanics; the existing point hits below preserve the retrieval complement (the both-not-either posture and the W4 additive precedent: an additive key/section is the established flag-off pattern on this surface).
5. **Eval** — retrieves the AEP on the synthetic graph and scores per-slice evidence admission directly against the AEP slices; no re-implementation.

### Risks
- **Additive budget coupling** — the block competes with the pool for the SAME 8k/32 KiB envelope; on fired questions the pool's admitted point count shrinks (the exact admission-geometry risk from the issue's lane-1 discussion, inverted: we're displacing rank-ordered evidence with structure). Mitigate: slice caps tuned on the synthetic graph; measure both block-admission AND pool-admission; keep a knob to disable the append and place the AEP as the sole evidence (Option B1 posture) later.
- **Presence gate is recall-limited by the pool** — a needed connected Object that the RRF pool missed never seeds expansion (Mem0 pool-gatekeeper anti-pattern, in a milder form). Mitigate: the gate can add ONE bounded second fetch of the found Objects' aboutObject points (id-keyed, not similarity-gated — graph-certain evidence must not be embedding-starved, constraint 9 §9.2), which is the honest middle ground.
- Extending `SubgraphExpander` risks regressing the #898 UC3 recall surface → typed mode must be opt-in with default behavior byte-identical (already the parameter shape).
- The reader now sees a heterogeneous evidence (structure + rank list) — ordering within the reader prompt matters (structure first per lost-in-the-middle).

### Tradeoffs
+ Smallest diff of the three: no new retrieval engine, no pre-retrieval router, no write-side machinery; one new module + a parameterized walker extension.
+ Embedding/embedded-safe by construction: no Object FTS dependency (subjects discovered via the point pool; enrichment is id-keyed).
+ The AEP object makes the "one renderer, multiple consumers" axis real with negligible extra surface — eval gets a direct admission artifact.
− Gate is pool-dependent → weaker both-halves guarantee than B1/B2 for compare questions (only as good as the point pool's recall of both subjects).
− Additive evidence is longer per token than a pure assembled block (structure + rank list both present).
− Keep-open tension with the answer-surface "dense structured block" goal: the block is a PREFIX here, not THE evidence.

### Best-fit if
The team wants minimal blast radius, byte-identical default guarantees by construction (not by router tuning), embedded parity without new resolver machinery, and a shared machine-readable artifact for eval admission measurement. **Differentiator: reuses the existing retrieval order + the existing walker in a typed mode; assembly trigger = graph presence in the pool (structure-gated, not morphology-gated); block is additive and an eval-consumable AEP object.**

### Constraints 1–9 compliance
1 ✅ date-sorted ISO spine inside the timeline slice (renderer sorts; reader never orders) · 2 ✅ state header + dated evidence slices · 3 ✅ superseded excluded from state slice by default; `[SUPERSEDED BY]` markers ride through on evidence (existing D8 decoration reused); Object `supersededBy` rendered in the state slice · 4 ✅ relevance gate = pool presence (assembly only when the top-ranked pool already shows multi-event connected structure); typed expansion ≤ 1 structural hop (aboutObject) + supersession chain; hub fan-out capped per Object · 5 ✅ AEP + pool under the single existing 8k/32 KiB envelope with per-slice caps; whole-row drops · 6 ⚠️ partial — compare both-halves is pool-recall-dependent (mitigated by the one id-keyed aboutObject back-fetch); ordering/interval diffs still precomputed in the renderer where both halves land · 7 ✅ no standalone resolver (subject = pool-discovered Objects, each tagged with in-pool evidence count = a confidence proxy); no LLM resolution · 8 ✅ eval consumes the AEP object directly (per-slice admission) · 9 ✅ no blind BFS (typed rel_pattern + gate), graph-certain aboutObject back-fetch is never embedding-gated, no summaries, no LLM consolidation.

---

## Cross-option tension map (how the three resolve the scout's open questions)

| Open question | B1 (CAP branch) | B2 (snapshot) | B3 (AEP append) |
|---|---|---|---|
| Router surface | Standalone pre-router (new shapes, pre-retrieval) | Shape gate + snapshot presence (resolver check) | No router — structure gate post-retrieval |
| qtype enum | Unchanged (internal router) | Unchanged | Unchanged |
| Block placement | Replaces pool on fired shapes | Replaces pool on snapshot hit | Additive prefix over the retained pool |
| Timeline spine SOURCE | Event aboutObject spine (walker merges) | Fold-time canonical (events+when coalesced at write) | Pool-dated points + Event session join (annotate_ask_hits shape) |
| Date precedence | startedAt > when > createdAt (at render) | startedAt > when > createdAt (normalized at fold) | session_date/startedAt[:10] + validFrom/To markers already on hits |
| Relevance gate | shape→slice map | snapshot membership + as-of window | pool presence (≥2 dated items per Object) |
| Validity semantics | explicit markers + open-window active | interval math over stored rows | D8 markers + Object supersededBy |
| Subject resolution | Object-name FTS + aliases, multi-candidate tags | exact id/name index read | pool aboutObject joins (no FTS) |
| LLM fallback v1 | None (documented follow-up) | None | None |
| Embedded/no-FTS | Needs name-index read fallback (docker FTS optional) | Native (id-keyed only) | Native (point FTS + id reads) |
| EP/provenance | annotate_ep_batch on evidence slice | annotate_ep_batch on stored ids | rides existing hit decoration + id-keyed EP |
| Eval consumption | Shared CAP function | Shared snapshot store | Shared AEP object |

All three keep the reader call count at exactly ONE (zero added LLM calls), stay under the answer-surface evidence caps, and preserve a byte-identical legacy path when their gate does not fire / the flag is off.

---

## Source index (code verified on this worktree)
- `tortoise/sdk.py` — `ask()` ~12606 (order: retrieval→annotate→dedup→boost→rerank→assemble→qtype at ~12812); `tortoise_fts_query` ~11498 (entity_type default "point"; decoration for object entity_type ~12121); `annotate_ask_hits` ~11418 (Event startedAt join → session_date/speaker); `_entity_key_expansion_pass` ~12390 (Object-name FTS anchor resolution precedent); `belief_timeline` 4986; Object fold path `commit_ops.py:297+` (`apply_supersessions`/`ObjectSuperseded`/`_fold_object_superseded`); Event keying `sdk.py:11011` (`MERGE (e:Event {eventId:...})`).
- `tortoise/reader.py` — `detect_question_type` 397 (TR→KU→MS→SSP→None; fragments only), `system_prompt_for` 340.
- `tortoise/search_engine.py` — zero aboutObject refs; `degradation_chain` 960; `run_fts_query` 316 (index-missing degrade); `expand_structural_hops` 833 (IMPL|NAND only).
- `tortoise/ranking.py` — `SubgraphExpander` 1054 (`rel_pattern` parameter exists; blind BFS default; `_neighbors` id-keyed on `n.id`), event signals id-keyed on `eventId` 433.
- `tortoise/retrieval.py` — `assemble_context`/`render_context`/`_validity_marker`/`_render_block` 398–581 (32 KiB byte cap, whole-hit drop); `dedup_pool` 287 (per-session cap).
- `tortoise/projection/__init__.py` — FTS/vector index creation ≥ 4.x ~2140–2270; version probe ~1790.
- `tools/longmem_eval/retrieve.py` — TR point+event union, `detect_time_constraint` 364, `tr_top_k`, rerank cap 2, per-session caps.
- `docs/ONTOLOGY.md` §3.5/§3.8/§4.1/§4.3/§4.5 — Object status/supersededBy fold cache; Event eventId/startedAt; Point when/validFrom/validTo/expiredAt/quote/search_keys.
- `docs/product/answer-surface.md` — 12-field contract, evidence caps, question_type enum, W4 additive precedent.
