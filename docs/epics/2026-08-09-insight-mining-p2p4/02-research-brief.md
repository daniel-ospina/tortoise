---
title: "Epic Research Brief — #264 (re-scope): Mine Agent Conversations for Insights — Phases 2–4"
type: synthesis
domain: strategy
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-09
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Epic Research Brief — #264 (re-scope): Mine Agent Conversations for Insights — Phases 2–4

**Date:** 2026-08-09
**Status:** draft
**Research depth:** deep (epic scope)
**Domain:** engineering + ontology
**Decision context:** CONDITIONAL PROCEED (align, 01-align.md) — execution gated on Gate A (#320 STABLE) + Gate B (calibration milestone).

---

## 1. Problem Reframing (research protocol §1.2)

**[Users]** trying to **[recall what was decided and what entities exist across hundreds of agent sessions]** but **[each session is mined into isolated Points/Events with no entity backbone or cross-session consolidation]** which results in **[repeated decisions ("we already decided this"), duplicate entities, and a graph that can't answer "what changed over time" across sessions]**.

**5 Whys:**
1. Why mine conversations? → Sessions contain decisions/entities only agents saw once.
2. Why do insights vanish? → Each session's Points sit unconnected in the epistemic graph.
3. Why unconnected? → No shared Object nodes; entities are inline strings in `aboutEntities`, never resolved.
4. Why no shared Objects? → Phase 1 wrote extraction (Points+Events) but deferred entity extraction/dedup (Phase 2 non-goal of #320).
5. Why does that matter? → Graph queries answer "what exists" (Semantic) only if entities are first-class, deduplicated Objects.

**Alternative framings considered:**
- *HMW achieve cross-session recall without entity extraction?* → Search-only (rejected in align: doesn't consolidate), full-text (rejected: not graph-native).
- *HMW let the graph learn entities passively?* → #438 connection discovery (SEPARATE epic — graph-driven, not conversation-driven; boundary preserved).
- *HMW avoid dedup entirely?* → Accept duplicates; only viable at small scale, poisons semantic search at 4,190 sessions (rejected).

**Assumption mapping:**
- [unverified] LLM extraction precision ≥70% (Gate B pending)
- [unverified] Embedding-similarity dedup precision adequate at scale (Phase 2 risk)
- [validated] ONTOLOGY v3.2 about* edges are the wiring mechanism (§3.2, §3.5)
- [validated] Phase 1 scaffolding (extractor harness, EventRecorded, MCP) is reusable
- [unverified] EP propagation on draft Points excluded (align fix 3 — code gap confirmed below)

**Aporetic turn:** the interesting question is not "how do we extract entities" (solved pattern) but "how do we make extraction *safe* for a belief-propagation graph" — the nuclear risk is graph pollution, not extraction quality per se.

---

## 2. Internal Knowledge (codebase + docs)

### 2.1 Phase 1 delivered (reuse base)

| Component | What it does | Reuse for Phases 2–4 |
|---|---|---|
| `tortoise/mining.py` (ConversationMiner, 361L) | Transcript → extractor run → Points+Operators → derives meeting/decision/friction/milestone EventRecorded events → emits via log+projection | Event derivation pattern (cue words, dedup-by-content of friction events); `_emit_event`; `_extract_participants` |
| `tortoise/extractor.py` (975L) | `LLMExtractor`: deterministic utterance→point (segmenter owns identity/provenance; model only cleans content); `_RelationStage` extracts IMPL/NAND with provenance; `_DocumentPointStage` with `aboutEntities` + `confidence` per point; document mode (## sections) | **Entity extraction hook already in prompts** (`aboutEntities` list per point, extractor.py:342-347, 627-632) — currently wired via legacy `_create_about_edges` auto-detect path; Phase 2 needs deliberate Object creation instead |
| `tortoise/session_indexer.py` (569L) | `extract_metadata_with_llm` (summary, narrative_arc, keywords, topics, issues, prs, critical_decisions); TF-IDF keyword fallback; `_graph_entity_keywords` (matches graph Object/Subject names in content); `compute_session_embedding` (384-dim) | `_graph_entity_keywords` is a free rule-based entity-mention detector — reuse as dedup candidate pre-filter |
| `tortoise/sdk.py` | `ingest_corpus` AgentSession branch (file_hash dedup, keyword completeness signal, embedding, `_connect_issue_objects`); `create_event` with `aboutSubject/aboutObject/aboutPoint/aboutDocument` → `create_about_edge` wiring; `create_object`; `create_point(dedup=True)` by content_hash+pointKind; `supersede_point`/`invalidate_point` (CORRECTS) | `create_event` about* wiring is the Phase 4 edge primitive; `create_object` is the Phase 2 write primitive; point dedup=True is exact-hash only (not semantic) |
| MCP `tortoise_index_sessions` (mcp_server.py:961) | Index sessions → AgentSession Events | Batch trigger surface for Phase 2/4 pipelines |

### 2.2 Code gaps / drift discovered (feed the plan)

1. **`INSTANTIATES` is dead but still written/read.** ONTOLOGY v3.2 §3.9 removed it (#214, Action dissolved). Yet `_connect_issue_objects` (sdk.py:4340) and `session_indexer.py:555` still `MERGE (e)-[:INSTANTIATES]->(o)`; `ranking.py:172,245` reads it for session graph_boost; `security.py:84` still whitelists it. `valid_predicates` in projection/edges.py:253 does NOT include it. **Plan: replace Event→Object issue/PR wiring with `aboutObject` (or `references`), update ranking.py boost query, keep security whitelist in sync.**
2. **EP does NOT filter `status: draft` AND SDK #131 auto-promotes on first edge.** `TortoiseEP.run` (ep.py:447) propagates over all affected Points regardless of status; `_affected_claims`/`_affected_factors` never exclude drafts. Worse, the SDK lifecycle is **draft → live on first edge**: Points enter as `status: draft` (sdk.py:457) and are auto-promoted to `live` the moment any edge is created (`add_operator`/`create_about_edge` set `s.status='live'`, sdk.py:1010-1012, #131). Phase 4's core activity IS wiring about*/IMPL edges — so the draft-only-until-review mitigation fails at exactly that moment unless the plan pins: (a) extraction-created Points stay `status: draft` with no auto-promotion on extraction wiring, AND (b) EP excludes draft Points (or extraction does not auto-wire operator edges until review).
3. **No grounding-regression tooling.** `analyze.py` grounding is a PageRank ranking query, not a pre/post health snapshot. Gate B needs the ≤2% mean-grounding metric — calibration milestone must build the snapshot query.
4. **Legacy `_create_about_edges` default is Subject stubs** (projection/edges.py:69-111) — tries Subject→Object→Event→Document→Point, then `MERGE (s:Subject {name})` stub. For entity extraction we want **Object** resolution (Semantic "what exists"), so Phase 2 should use `create_object`/aboutObject path, not the legacy auto-detect.
5. **Point dedup is exact-hash only in `create_point(dedup=True)`** (matches content_hash+pointKind, sdk.py:415-419). Phase 2's "we already decided this" semantic dedup should **reuse the production `_semantic_dedup` two-tier pattern** (content-hash + embedding-cosine, sdk.py:2348) applied to extracted decision Points — extend it, don't reinvent.

### 2.3 What already exists for dedup (entity-level resolution NOT built — verified)

- `ingest_corpus` file_hash skip = ingest idempotency, not entity dedup.
- `_connect_issue_objects` creates issue/PR Objects with deterministic sha256-hash IDs when no numeric id — a **primitive hash-based entity resolution** already in production (only for issues/PRs).
- `_graph_entity_keywords` (session_indexer.py:122) = rule-based mention detection against existing graph names.
- **`checkpoint()` / `_semantic_dedup` (sdk.py:2272-2395) — production two-tier content dedup precedent:** content-hash tier 1 + embedding-cosine tier 2 (sentence-transformers with TF-IDF fallback, configurable threshold) against ALL existing checkpoint Points (cross-session, un-scoped). This is the exact machinery Phase 2's "we already decided this" content dedup needs — **extend/reuse, not build new**.
- **No semantic cross-session ENTITY-level resolution, no canonical-name resolution, no sameAs/equivalence edges built anywhere.** #438 (automated cross-domain connection discovery) is graph-driven IMPL/NAND discovery between existing Points — a SEPARATE epic. Boundary: #264 mines CONVERSATIONS (unstructured text → new Points/Objects); #438 discovers CONNECTIONS (existing graph → new edges). No overlap; both must stay distinct.

### 2.4 Temporal belief tracking (Phase 4)

- Points carry `validFrom`/`validTo` (§4.1) — temporal validity window exists but is never written by extraction.
- `supersede_point`/`invalidate_point` (§3.1) create CORRECTS + mark `outdated` — the belief-change mechanism.
- Session Events carry `startedAt` (indexing writes `now`, not the real session time — noted limitation).
- **No cross-session belief-change history exists** ("decision on date X contradicted on date Y"). Phase 4 needs: extracted Points with validFrom=session date; NAND between contradictory session Points; temporal query (belief over time).

---

## 3. External Findings (Perplexity — entity resolution / dedup patterns)

> Confidence: 2+ independent sources per cluster. ⚠️ emerging where single-source.

### 3.1 Resolution pipeline pattern (multi-source, High)
Pragmatic entity resolution for KG construction is a **short-circuit chain: exact match → fuzzy match → semantic (embedding) match**, each with thresholds; unmatched → new node. Duplicate detection is a SEPARATE decision from resolution (embedding similarity + context comparison, then merge/review/new-node). Sources: Connected Data World 5-step KG cleaning; Till Freitag entity-extraction blog; DecodingAI "keep your KG clean". **[HIGH — 3 sources]**

### 3.2 LLM reconciliation for uncertain matches (multi-source, Medium)
When embedding similarity is ambiguous (0.6–0.9 band), an LLM reconciliation pass decides same-entity vs distinct — cheaper and more accurate than pure thresholding. KGGen (arXiv 2502.09956) uses embeddings + BM25 retrieval + LLM dedup iteratively. Human-in-the-loop only for high-stakes uncertain matches. **[MEDIUM — 2 sources]**

### 3.3 Embedding dedup with thresholds (multi-source, High)
Llamaindex property-graph index and Mem0-class agent memory use text embeddings + word similarity for entity dedup with explicit thresholds: auto-merge below X, human review in band, new node above. **[HIGH — 3 sources]**

### 3.4 Temporal agent memory graphs (Medium)
Temporal KG architectures for agent memory (arXiv 2501.13956) timestamp entity/claim states and track evolution — validates Phase 4's temporal belief tracking as a first-class design (validFrom/validTo + supersession), not an afterthought. **[MEDIUM — 2 sources]**

### 3.5 Cost discipline for batch extraction (Medium)
Batch LLM extraction at scale: model tiering (cheap classifier → expensive extractor), fire-and-forget async, resumable progress. Already the Phase-1 architecture (two-tier) — Phase 2/4 should follow the same shape. **[MEDIUM — 2 sources]**

### 3.6 Adversarial findings (what fails in practice)
- Pure fuzzy matching (no semantic) produces unmanageable false merges at scale → semantic layer required for open-domain entities. [1 source — ⚠️ emerging]
- Auto-merge without review band pollutes the graph with wrong canonical names — the #438-adjacent failure mode; our draft-status gate is the correct mitigation. [2 sources — Medium]
- Embedding similarity alone conflates "mentions same string" with "is same entity" (polysemy) → need context comparison, not just string proximity. [2 sources — Medium]

---

## 4. Synthesized Approach (what to build)

### Phase 2 — Entity extraction & cross-session dedup
1. **Extraction:** Extend the Phase-1 extraction harness to a dedicated entity stage (reuse `_DocumentPointStage` pattern, LLM returns entities with canonical-name candidates + types mapped to `objectKind` vocab). Keep deterministic identity from segmenter; model cleans/supplies entity spans.
2. **Resolution (short-circuit chain):** exact (normalized string) → fuzzy (normalized edit distance, pre-filtered by `_graph_entity_keywords`-style mention detection) → semantic (embedding cosine, thresholds: <0.6 new node, 0.6–0.9 LLM reconciliation, >0.9 merge). v1 non-goal: perfect resolution — fuzzy+semantic is the stated v1.
3. **Write:** `create_object` (MERGE by canonical id — deterministic hash of canonical name, mirroring `_connect_issue_objects`), then `(Point)-[:aboutObject]->(Object)` + `(Event)-[:aboutObject]->(Object)` wiring. Content dedup ("we already decided this"): semantic similarity across extracted `decision`-kind Points, linked via IMPL or supersession after review — NOT auto-merge.
4. **Fix drift:** replace `INSTANTIATES` wiring with `aboutObject` (or `references`) in `_connect_issue_objects` + session_indexer.py; update ranking.py boost query to the new edge; sync security.py whitelist.

### Phase 4 — Cross-ontology integration
1. **about*/structural wiring:** use `create_event`'s existing about* props and `create_about_edge`; Event→Object `produces`/`uses` per §3.5 where the conversation shows artifact production (mandate's Phase-3 replacement — actions are events, not nodes).
2. **EP propagation on extracted Points (gated):** extracted Points created `status: draft`; draft excluded from EP (`ep.py` change); no auto-mitigation wiring from extraction; calibration gate before live promotion. EP runs on the extraction batch produce confidence only for reviewed/live Points.
3. **Temporal belief tracking:** extracted Points get `validFrom` = session date; NAND operators link contradictory session decisions; belief-over-time query surfaces "decided X on D1, contradicted on D2". Reuse supersede/CORRECTS for explicit replacement.

### Sequencing (from align): Phase 2 → Phase 4 EP-last; both gated on Gates A+B.

---

## 5. Source Confidence Summary

| Claim | Tier | Sources |
|---|---|---|
| Short-circuit resolution chain (exact→fuzzy→semantic) | High | 3 (ConnectedData, Freitag, DecodingAI) |
| LLM reconciliation in ambiguity band | Medium | 2 (KGGen arXiv, Freitag) |
| Embedding dedup thresholds (auto-merge/review/new) | High | 3 (Llamaindex, Mem0-class, ConnectedData) |
| Temporal KGs for agent memory | Medium | 2 (arXiv 2501.13956 + Neural Maze) |
| Batch cost discipline (tiered models, async, resumable) | Medium | 2 (Lenny's memory/Neo4j, KGGen) |
| Fuzzy-only matching fails at scale | Low ⚠️ single-source | 1 (ConnectedData) |
| Auto-merge pollutes; review band needed | Medium | 2 |
| Embedding polysemy (context needed) | Medium | 2 |

---

## 6. Open Questions (hypotheses, resolve in scope/plan)

1. [hypothesis] Embedding-dedup threshold calibration on real session corpus — need a labeled sample (calibration milestone).
2. [hypothesis] Whether `aboutObject` vs `references` is the right Event→issue/PR edge after INSTANTIATES removal — ontology §3.4/§3.2 vs §3.5 (decide in plan; default `aboutObject` per §3.2 many→many).
3. [hypothesis] Draft-status EP exclusion semantics: exclude draft Points as EP targets entirely, or allow them as sources? (Align fix 3 default: exclude entirely; verify in EP tests.)
4. [hypothesis] Session `startedAt` accuracy for temporal tracking — indexing writes ingest time, not session start; extraction must read real timestamps from frontmatter (session file header).

---

## 7. Required Evidence (for hypothesis claims)

- Calibration milestone: 50-session labeled sample → precision ≥70%, dedup thresholds calibrated, EP grounding ≤2% drift (Gate B).
- #416 volume gate outcome as input (not substitute) to calibration.
- #320 I3 completion (4,190 sessions indexed) before full-batch Phase 2 (Gate A).
