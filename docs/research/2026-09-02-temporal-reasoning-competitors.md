# Research — Temporal Reasoning in Competitor Agent-Memory Systems

**Date:** 2026-09-02 · **Mode:** external competitor + general research (web, read-only)
**Problem context:** Tortoise LongMemEval-S overall 0.62; non-temporal 0.875; temporal-reasoning 0/13. Retrieval ranks individual memory points but doesn't assemble multi-event evidence. Competitors tout ~85+.
**Note on prior research:** the referenced `2026-08-29-reader-answer-surface-competitors.md` does not exist in repo. Closest in-repo prior: `docs/research/2026-08-24-1657-retrieval-levers/research.md` (R5 temporal retrieval stack: recency_boost, time-window filters, temporal-intent gating on TR category) and `docs/plans/2026-08-27-1785-session-recall.md`. Tortoise ontology is **state-centric** (`docs/ONTOLOGY.md` §2, v3.7): graph stores STATE (Objects + lifecycle + derived confidence) + POINTS (logic) + EVENTS (timeline). "Status is derived, not stored… events are the truth, status is a read-only projection" at query time. Bi-temporal capture (`capturedAt`, transaction time). Supersession edges exist (`promoted/deprecated/superseded`, CORRECTS).

---

## ⚠️ Benchmark-number caveat (read first)

Vendor-reported LongMemEval/LoCoMo numbers are **not comparable across vendors and not reproducible**:

- Mem0 self-reports LongMemEval overall **93.4%** (vs Zep 71.2%, in Mem0's own blog) and **94.4% @ top_200** for its temporal-reasoning build (mem0.ai/blog/introducing-temporal-reasoning-in-mem0).
- An independent third-party eval (Vectorize, 2026) reports **Mem0 49.0% vs Zep 63.8%** on LongMemEval (vectorize.io/articles/mem0-vs-zep).
- Zep's own paper claims DMR **94.8% vs MemGPT 93.4%** and LongMemEval accuracy improvements "up to 18.5%" over baselines (arXiv:2501.13956) — but Zep later **published a correction** of its own earlier LoCoMo result, landing at **75.14% ± 0.17** (blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/).
- Digital Applied's roundup: "the only clearly independent number found was Mem0 at 49.0 vs a self-reported 94.4"; Letta has no published LongMemEval score (digitalapplied.com/blog/open-source-agent-memory-mem0-letta-zep-compared).

**[HIGH]** — 3+ independent sources converge on: (a) self-reported numbers differ from independent evals by 30–45 pts; (b) no vendor runs the same subset/protocol; (c) "~85" marketing claims should be treated as upper-bound self-reports. Also note: our LongMemEval-**S** (short-conv split) is not the same protocol as vendor LongMemEval reports — deltas are not apples-to-apples.

---

## Competitor temporal approaches (per-system)

### Mem0 — write-time temporal metadata + additive rerank (closest to "annotate, don't rebuild")

Architecture (mem0.ai/blog/introducing-temporal-reasoning-in-mem0; mem0.ai/blog/the-token-efficient-memory-algorithm-now-has-temporal-reasoning; arXiv:2504.19413):

- **Write side:** after normal extraction, a *separate* temporal pass reads each memory + conversation + date and returns structured metadata: when it happened, ongoing/completed, precision, and **memory type** — one of 7: `event, state, plan, relationship, preference, absence` (+ timeless facts). **[HIGH]**
- **State key:** ongoing facts (states/relationships/preferences) carry a **`state_key`** — a stable identifier linking every memory about the same evolving fact for one user. When a new state takes over, the old one's **`event_end` is set automatically**. Nothing is deleted; history stays queryable. **[HIGH]** (vendor blog + docs; mechanism visible in blog API examples)
- **Read side:** queries classified by **temporal intent** (current_state, historical_range, duration_state, upcoming, soft_recency…) with **no extra LLM call**. Intent is NOT used to filter the candidate pool — only as an **additive rerank score** on retrieved candidates. "Semantic relevance always dominates"; pre-filtering would "silently drop memories with imprecise or missing dates." **[HIGH]**
- **Async:** temporal enrichment runs as background patch; writes return immediately; search degrades gracefully until metadata lands. **[HIGH]**
- **Self-reported deltas (their build):** LoCoMo 86.1→92.5 overall (+6.7 on temporal q's); LongMemEval 90.4→94.4 @ top_200; **multi-session questions 82.0→93.2 @ top_50**; disclosed **open-domain dip −1.9 pts**; p50 latency +1ms but **p95 +~198ms**. **[MEDIUM]** — vendor-reported, internally consistent, and they disclose the regression category, which is rare.
- **Honest admission:** "Knowledge-update remains the hardest category for an additive memory architecture" — older semantically-similar facts still rank near newer ones; they shipped separate "memory decay" to fight stale-memory pressure. **[HIGH]**

Pattern: **(b)-leaning annotation** — they do NOT project a single current state; they keep dated instances linked by state_key and use intent-aware reranking to surface the right instance. Explicitly *not* deletion/replacement.

### Zep / Graphiti — bi-temporal property graph (see deep-dive below)

Pattern: **(c) graph traversal over dated facts**, with edge-level validity windows, LLM-extracted at write time, invalidation-on-contradiction, plus entity summaries (projected-state artifact). **[HIGH]**

### Letta (MemGPT) — hierarchical memory, no temporal layer

- Memory tiers: core memory (LLM-edited JSON blocks — effectively hand-maintained state), recall memory (conversation history), archival memory (passages w/ semantic search) (letta.com/blog/agent-memory/; arXiv:2310.08560). **[HIGH]**
- Archival-memory search API exposes only **`start_datetime`/`end_datetime` filters + tags + created_at** on passages — i.e., raw-time filters on chunks, **no event/state typing, no validity windows, no temporal metadata** (docs.letta.com/api/resources/agents/subresources/passages/methods/search/). **[HIGH]**
- Temporal correctness therefore depends on the LLM reading raw recalled conversation + core-memory blocks. Third-party summary: "temporal retrieval is not a built-in feature in the compared setup" (vectorize.io/articles/letta-vs-langchain-memory). **[MEDIUM]**
- Pattern: **(a) raw evidence + LLM**, with **(b)-ish core-memory blocks** the agent itself edits. No dedicated temporal machinery.

### LangMem (LangChain)

- Memory via LangGraph persistent store: namespaced memory items, tool-based extraction, background memory manager after conversations (langchain-ai.github.io/langmem/; langchain.com/blog/langmem-sdk-launch). **[MEDIUM]**
- No published temporal reasoning layer, no time-indexed fact invalidation found; timestamps exist on store entries but no temporal scoring/intent layer. **[MEDIUM]** (absence of evidence — check repo before relying on this claim)
- Pattern: **(a)** with store-level namespacing.

### Cognee

- Poly-store pipeline (graph + vector + relational) for knowledge-graph memory; `remember` writes to session or permanent graph memory (github.com/topoteretes/cognee; cognee.ai/open-source-memory-frameworks-llm-agents). **[MEDIUM]**
- Survey notes episodic memory "needs temporal modeling and source evidence" — i.e., temporal modeling is a **documented limitation/absence**, not a feature (graphlit.com/blog/survey-of-ai-agent-memory-frameworks). **[LOW→MEDIUM]** single survey source.
- Pattern: graph-structured, but no temporal-first design evident.

### Others worth one line

- **AutoMem / temporal-rag (emmimal) / Re3** (from in-repo 1657 research): recency-weighted retrieval with intent-gated application and validity-filtering are the practitioner pattern — already partially shipped in Tortoise R5. Not re-surveyed here (covered in 1657 research.md).
- **Vectorize/MarkTechPost family surveys** place "temporal KG memory (Zep/Graphiti)" vs "vector memory" vs "event logs" as the three families, with LongMemEval temporal as the differentiator (vectorize.io/articles/best-ai-agent-memory-systems; marktechpost.com/2025/11/10/comparing-memory-systems-for-llm-agents-vector-graph-and-event-logs/). **[MEDIUM]**

---

## Graphiti temporal KG deep-dive (closest competitor pattern)

**Representation** (help.getzep.com/graph-overview; help.getzep.com/how-graph-creation-works; github.com/getzep/graphiti; neo4j.com/blog/developer/graphiti-knowledge-graph-memory/; arXiv:2501.13956):

- Property graph, three node types: **Entity nodes**, **Entity edges (facts)**, **Episodic nodes (raw provenance)**. Facts live **on edges**, not as reified nodes. **[HIGH]**
- **Bi-temporal edge model:** every fact edge carries four timestamps — `valid_from`, `valid_to` (valid/assertion time) and `observed`, `recorded` (ingestion/provenance time) per Zep docs ("valid time and ingestion/provenance time"). Neo4j blog describes `t_valid`/`t_invalid` validity intervals. **[HIGH]** (vendors use slightly different names across docs/paper — same bi-temporal idea; exact 4-field naming is Zep-docs-side)
- **Write pipeline (6 steps, LLM-guided per step):** (1) extract entities → (2) extract relationships/facts → (3) **"Date facts": a separate model pass assigns temporal bounds `valid_at`/`invalid_at` using the episode's event time as reference** → (4) entity resolution (under-merge over over-merge) → (5) **fact resolution: duplicates merge; contradictions invalidate the older fact rather than leaving conflicting truths side by side** → (6) entity summaries updated from new evidence. Episodes remain as ground-truth provenance. **[HIGH]**
- **Retrieval:** hybrid semantic (embedding) + keyword (BM25) + **graph traversal (BFS from matched entities)**, fused; edge validity enables "what's true now vs what was true at time t". Episodes are a first-class search scope, not a fallback. **[HIGH]**
- **"What changed between t1 and t2":** answered by querying edge validity windows — an edge whose valid_from/invalid_at bounds fall in [t1,t2] is a change; invalidation preserves the old edge so both states are queryable. Zep docs: "updates or invalidates outdated knowledge instead of deleting it." There is no published dedicated "diff two snapshots" API; the capability is temporal filtering over validity intervals (per Zep "searching the graph" + temporal filters) plus **entity summaries** that absorb deltas. **[MEDIUM]** — "diff" is *possible* via the model but I found no first-class diff endpoint documented; treat as inference from the data model.
- **Multi-hop temporal evidence assembly:** search starts from query-matched entities, traverses edges, with temporal constraints applied to edge validity; communities/clusters (GraphRAG-style) exist in the full Zep product (community subgraphs per emergentmind summary). **[MEDIUM]**

**What this means for us:** Graphiti's time story is **extraction-time LLM dating + edge-validity invalidation + provenance**, not query-time reasoning over raw events. The costly part (an LLM date pass per fact) happens at write. Multi-event ordering questions are answered by the *reader* LLM over retrieved dated facts — Graphiti's retrieval job is to hand back the dated fact set with correct valid windows.

---

## Proven patterns in research (academic + practitioner)

1. **Recency-weighted, intent-gated retrieval** — Generative Agents' memory stream: score = recency (exponential decay) + importance + relevance (arXiv:2304.03442); practitioner variants apply recency **only when query expresses temporal intent** and add a relevance floor so freshness can't surface an irrelevant memory (AutoMem/temporal-rag/Re3 — see in-repo 1657 research.md). **[HIGH]**
2. **Time-aware retrieval with explicit time parsing beats plain RAG on diachronic questions** — TA-RAG (time-sensitive retriever over temporal expressions; ADQAB benchmark; disabling time filtering measurably degrades) (arXiv:2507.22917). **[HIGH]**
3. **Temporal graph modeling of the corpus** — TG-RAG models corpus as a bi-level temporal graph with time-aware retrieval + incremental updates (ECT-QA benchmark) (arXiv:2510.13590). **[MEDIUM]**
4. **Agentic/iterative retrieval for temporal KG QA** — TempAgent (time-aware ReAct) outperforms naive RAG and ReAct-RAG on temporal KG QA; still weak on fine-grained temporal cases (NAACL 2025 Findings, aclanthology.org/2025.findings-naacl.334). **[MEDIUM]**
5. **Retrieval failure is the dominant error source** — ChronoQA (Nature Sci Data 2025): failure to retrieve correct evidence is the largest error source; **relative time expressions** ("two weeks ago") are especially hard (nature.com/articles/s41597-025-06098-y). Directly relevant to our 0/13. **[HIGH]**
6. **Memory-management systems** — MemGPT: OS-style hierarchy where the LLM manages what's in context (arXiv:2310.08560); LoCoMo and LongMemEval established temporal + multi-hop as the standard memory categories (cited across mem0/zep/graphlit surveys). **[HIGH]**
7. **Event-centric memory + state projection**: Mem0's state_key/event_end and Graphiti's entity summaries + invalidation are the two production implementations of "keep history, mark supersession"; **neither deletes history, neither stores a single overwritten current value**. "Current state" is always resolved at read time from dated instances (mem0) or edge validity (graphiti). **[HIGH]**

---

## Adversarial findings (what fails)

1. **No temporal/version awareness → stale blends** — plain RAG-as-memory has no temporal or version awareness, retrieves stale facts, and can blend contradictory versions into one wrong answer (sentra.app/articles/why-rag-fails). **[MEDIUM]** LlamaIndex failure checklist documents session/cache breaks and answers changing across days (developers.llamaindex.ai/python/framework/optimizing/rag_failure_mode_checklist/). **[MEDIUM]**
2. **Knowledge-update / contradiction is the hardest category everywhere** — Mem0's own admission that KU remains hardest for additive architectures (older similar facts compete with newer) and their "memory decay" patch (mem0.ai/blog/introducing-temporal-reasoning-in-mem0). Graphiti handles contradiction by invalidation at write time — but that invalidation is **LLM-judgment-dependent** (a model pass decides what contradicts what); wrong merges/invalidations are hard to undo (Zep: "a wrong merge corrupts identity in ways that are hard to undo"). **[MEDIUM]**
3. **Failure taxonomy** — MemFail: memory systems fail via retrieval failure AND reasoning failure, incl. missing contradictory updates and preserving outdated facts (arXiv:2605.26667). **[LOW→MEDIUM]** (single arXiv source; number looks future-dated — verify before strong claims)
4. **Relative-time and multi-event questions are the hard tail** — ChronoQA: relative expressions hardest; retrieval failure largest error class (nature.com/articles/s41597-025-06098-y). Multi-session evidence *combination* (not single-fact lookup) is precisely where Mem0 reports its biggest uplift from temporal metadata (+11.2 pts @ top_50) — i.e., **even additive-metadata systems only fix multi-event assembly when the right dated instances both land in the pool**. **[MEDIUM→HIGH]**
5. **Is "maintained updated state" (our vision) real or research ideal?** Evidence from production systems:
   - No major system stores a **single authoritative current state per entity** that gets overwritten on each event. Letta's core-memory blocks are the closest (agent-maintained), and they are famously lossy/fragile (the agent decides what to keep). **[MEDIUM]**
   - The dominant production pattern is **append-only history + explicit supersession markers + read-time resolution** (Mem0 state_key/event_end; Graphiti validity windows + invalidation). That IS "maintain updated state," but implemented as *annotated history*, not *replacement*. **[HIGH]**
   - Staleness/contradiction risk is real in all additive systems (finding #2); the fix direction across vendors is read-time reranking + decay, not write-time consolidation. **[MEDIUM]**
   - Tortoise's ontology (events as truth; status derived at query time from event stream) is **already the production pattern** — the gap is that Tortoise *retrieval* doesn't yet use its own temporal structure to assemble multi-event evidence. **[HIGH — internal]**

---

## Implications for our design (ranked options)

Tortoise already owns the hard asset competitors bolt on later: **bi-temporal events with startedAt/endedAt, valid_from/valid_to, supersession (CORRECTS/superseded_by), and an EP graph**. The 0/13 is an assembly gap at retrieval/read, not a capture gap. Options ranked by evidence strength × fit:

1. **[READ-PATH] Event-assembly reader over the existing graph (top pick).** When temporal intent is detected (R5 machinery exists, `question_type == temporal-reasoning`), retrieve the *entity+event neighborhood* (events touching matched subjects/objects, sorted by startedAt) rather than top-k isolated points, and hand the reader a **timeline block** (ordered event list w/ dates) to reason over. Evidence: ChronoQA — retrieval failure is the dominant error; TA-RAG — time-filtered retrieval beats plain RAG on diachronic QA; Mem0's multi-session uplift comes precisely from getting dated instances co-retrieved. This converts "which came first / days between" from a retrieval problem into a *reader problem* the LLM can solve with ordered evidence. Cost: new query path + timeline renderer; no schema change. Confidence: HIGH.
2. **[READ-PATH] Query-time state projection for current-state/KU questions.** Tortoise's ontology already defines "status projected at query time from event stream" for Objects — extend the same projection to *any* entity's evolving attributes (latest city/job/preference) by walking the event/supersession chain and rendering "as of now / as of {date}" state. Evidence: Mem0's temporal query modes (current_state) + state_key resolution; Graphiti entity summaries; Tortoise P6/R4 bi-temporal requirements already spec "was A believed at t1". Distinguish from option 1: this answers "what's the latest status," option 1 answers "order/distance between events"; both are read-path, orthogonal, share the temporal-intent gate. Confidence: HIGH (design already half-speced in ontology).
3. **[WRITE-PATH] Event/state typing at extraction (mem0-style metadata).** Add a per-event classification (event/state/plan/preference/relationship/absence + ongoing/completed + precision) at ingest so the read path can target types. Evidence: mem0's temporal pass is write-time, additive, async; it's their whole mechanism. For Tortoise this is cheaper than it was for mem0 (Event nodes already typed by eventKind) — likely a metadata enrichment on existing extraction, and it unlocks type-aware retrieval ("plans" for upcoming, "states" for current). But it's the *largest* change for the *smallest* measured category gain in mem0's own data at our surface (their biggest gains were multi-session at deep cutoffs). Ranked 3rd because our 0/13 is about ordering evidence that already exists in the graph. Confidence: MEDIUM.
4. **[HYBRID] Recency/importance/validity rerank knobs on the existing pool** — the R5 stack already ships recency_boost + window filters; extend temporal intent to KU category (in-repo 1657 lever-2 already identifies this) and add validity-aware ranking (prefer non-superseded points for "now" queries). Cheap, mostly in-repo; expected bounded gain (1657: +1–2 pts). This is table stakes, not the fix for 0/13. Confidence: HIGH (in-repo evidence).
5. **[EVAL-DISCIPLINE] Stop comparing to vendor ~85 claims.** Independent eval shows 49–64% for the two loudest vendors on comparable protocols; our 0.62 overall with 0.875 non-temporal is credible and arguably *above* the honest field on non-temporal. Reframe success: temporal sub-score improvement against a *locally re-implemented* mem0/Graphiti-style baseline on LongMemEval-S, not against marketing. Confidence: HIGH.

**Sequencing suggestion:** 1 (assembly reader) → 2 (projection) are the evidence-backed core; 4 rides along (already in flight via R5); 3 only if 1+2 leave a residual gap that traceable type metadata would close. 5 is a communication/lab discipline item.

---

## Raw notes

- Searches run (all Perplexity/web): Graphiti temporal KG architecture; Mem0 temporal reasoning; Letta/MemGPT memory hierarchy; TA/temporal RAG academic; agent-memory framework surveys; memory RAG failure modes; Cognee/LangMem temporal; generative agents; LongMemEval results cross-check.
- Fetched full text: help.getzep.com/graph-overview, help.getzep.com/how-graph-creation-works, github.com/getzep/graphiti (README), mem0.ai temporal-reasoning blog, arXiv:2501.13956 abstract.
- Graphiti date pass quote (docs): "Step 3. Date facts — A separate model pass assigns temporal bounds when the source supports them (valid_at / invalid_at), using the episode's event time as reference."
- Graphiti contradiction handling quote (docs): "duplicates merge, and contradictions invalidate older facts rather than leaving conflicting truths side by side."
- Mem0 KU admission quote: "Knowledge-update remains the hardest category for an additive memory architecture… older semantically similar facts can still appear near newer facts."
- Number sanity: Mem0 self LongMemEval 93.4/94.4 vs independent 49.0; Zep paper DMR 94.8, LoCoMo self-corrected 75.14±0.17. Zep paper's LongMemEval "+18.5% accuracy improvement" is vs an unspecified baseline implementation (likely naive RAG), not vs MemGPT.
- Open question for next phase: does our LongMemEval-S harness's TR pool already contain all 13 questions' evidence events (recall-sufficient, assembly-failing) or is evidence missing at retrieval? That determines whether option 1 is a renderer fix or needs pool widening too. Check `tools/longmem_eval/retrieve.py` R5 handling for TR.
- Did not verify: LangMem internals for temporal metadata (absence claim), MemFail paper details (future-dated arXiv id, single source), Cognee temporal limitations beyond one survey. Tagged accordingly.
- Related in-repo: `docs/research/2026-08-24-1657-retrieval-levers/research.md` (L2 lever = extend temporal intent to KU — confirms option 4), `docs/ONTOLOGY.md` §2 state-centric model (options 1–2 are pure read-path instantiations of the existing ontology), `docs/epistemic-layer-eval-spec.md` P6/R4 bi-temporal requirements.
