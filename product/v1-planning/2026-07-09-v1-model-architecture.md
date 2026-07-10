---
title: "V1 Model Architecture — Synthesis"
type: product
domain: product
status: seedling
tags: [v1, model-routing, extraction, embedding, write-cost]
summary: "Synthesizes model routing decisions for V1: one LLM call, one embedding model, two source ingestion paths. Distills write-cost research to V1-relevant strategies already present in Tortoise design."
created: 2026-07-09
---

# V1 Model Architecture — Synthesis

> **What this is:** A synthesis of the model routing, write-cost, and ingestion decisions for V1. Distills the 8-strategy write-cost research to what's actually needed given the V1 architecture. Action items for folding into `embedding-retrieval.md` and `v1-strategy-2026-07-09.md`.
> **Depends on:** `v1-strategy-2026-07-09.md`, `embedding-retrieval.md`, `source-operator-event.md`, `MEMORY_TYPES.md`, Tortoise `README.md` + `BUILD_PLAN.md` (Connor's repo).

---

## 1. The V1 Model Surface

From `v1-strategy-2026-07-09.md` §2, the core has three interfaces that touch models:

| Interface | What it does | V1 model | Rationale |
|-----------|-------------|----------|-----------|
| **Embedding** | `embed(texts) -> ndarray` | `all-MiniLM-L6-v2` via fastembed | Already specified in `embedding-retrieval.md` §2. 384-dim, CPU, zero cost. |
| **LLM (extraction)** | `extract(segment, frame) -> {points, operators}` | DeepSeek V4 Flash via OpenRouter | Tortoise `models.py` already supports this via `OpenAICompatModel`. One call per batch. Batch-first (session-end), latency irrelevant. ~$0.001-0.005/batch. |
| **LLM (everything else)** | N/A | **None** | See §2 below. |

**That's it.** Two models. One of them is already decided. The other is a config value on an existing adapter.

---

## 2. Everything That Doesn't Need an LLM

| Operation | Why zero-LLM | Where specified |
|-----------|-------------|-----------------|
| **Doc ingestion** | Frontmatter is structured YAML. Parse → populate Doc node → embed title+summary+tags. | `embedding-retrieval.md` §6 |
| **GitHub source ingestion** | GitHub API returns structured data. Title → summary. Labels → tags. Author → speaker. URL → locator. | To add (see §3) |
| **Event log + projection** | Pure data operations. Append JSONL → fold into FalkorDB. | Tortoise `BUILD_PLAN.md` Piece 1 |
| **Vector search** | FalkorDB native. `CALL db.idx.vector.queryNodes()`. | `embedding-retrieval.md` §3 |
| **Deduplication** | Content hash + context matching. Tortoise `idempotency.py` design: same `content` + same `context` → merge candidate. | Tortoise `README.md` (idempotency section) |
| **Epistemic conflict detection** | Cypher query. "Find points near this disputed point." No model needed. | `embedding-retrieval.md` §3 (conflict resolution pattern) |
| **Belief propagation** | PGMax factor graph over NAND/IMPL edges. Deterministic computation, not LLM. | `source-operator-event.md` §Decision (ratified 2026-07-08). Design doc in Connor's repo: `tortoise/docs/probabilistic-scoring-design.md`. |

---

## 3. Source Ingestion Paths

V1 has two ingestion paths, both zero-LLM:

### 3a. Internal docs (already specified)

`embedding-retrieval.md` §6: YAML frontmatter → parse → populate Doc node → embed title+summary+tags. Content-hash optimization for delta-only re-ingestion. All 877 docs in `docs/teams/`.

### 3b. GitHub sources (to add)

GitHub sources (issues, PRs, external repo READMEs) need a parallel ingestion path. Same Source entity, different extraction:

| GitHub API field | Maps to Source field | Extraction |
|-----------------|---------------------|------------|
| `title` | `title` + `summary` | Direct (title serves double duty) |
| `body` | On-demand via `locator` | Direct |
| `user.login` | `speaker` | Direct |
| `labels[].name` | `tags` | Direct (map label → tag) |
| `created_at` | `created_at` | Direct |
| `html_url` | `locator` | Direct (format: `github:<owner>/<repo>#<number>`) |
| `state` + `state_reason` | Filtered (Cypher WHERE) | Direct |
| `assignee.login` | `affiliation` | Direct |

**Summary strategy:** Use the issue/PR title as the summary. Well-maintained repo titles ("Implement epistemic layer on Graphiti") are descriptive enough for semantic search. The embedding model handles paraphrasing — "belief propagation graph memory" matches "epistemic layer" without a generated summary. Add LLM summarization in V2 when search quality data justifies the cost.

**Content hash:** Store `sha(github_url + updated_at)` as the dedup key. Re-ingestion is a delta check: if the hash matches, skip.

**Locator format:**
```
github:daniel-ospina/eldato#5808           # issue
github:daniel-ospina/eldato#5854           # PR
github:connormcmk/negation-game-explorations/blob/main/README.md  # file
```

Agent reads `locator` → parses format → chooses retrieval tool (GitHub API, `gh issue view`, raw file fetch).

**To add to `embedding-retrieval.md`:** A new subsection after §6 ("§6.1 — GitHub source ingestion") with the mapping table above.

---

## 4. Write-Cost Strategies — What's Already in the Architecture

The 8 write-cost strategies from research, mapped to what Tortoise + our docs already cover:

| Strategy | Already handled? | Where |
|----------|-----------------|-------|
| **1. Local model** | ✅ Embedding: local MiniLM. Extraction: cloud (config-driven, swappable). | `embedding-retrieval.md` §2, Tortoise `models.py` |
| **2. Batch writes** | ✅ Tortoise `idempotency.py`: batch mode uses `content_hash(document)`. V1 is batch-first. | Tortoise `BUILD_PLAN.md` |
| **3. Small model extraction** | 🔧 V2 consideration. Tortoise `OpenAICompatModel` supports local Ollama — config change, not code. Need NAND/IMPL accuracy testing on 3B-7B models before enabling. | Not in V1 scope |
| **4. Dedup before store** | ✅ Content + context dedup via `idempotency.py`. Provenance list per point — new sources add to list, not duplicate points. | Tortoise `README.md` (idempotency, merge key) |
| **5. Extract only new** | ✅ Cursor tracking per source. Stream mode: `key = (stream_id, offset_range)` with committed cursor. | Tortoise `BUILD_PLAN.md` (idempotency) |
| **6. Per-source filtering** | 🔧 Pre-filter for chat noise. Add to Tortoise extractor config as an optional `pre_filter` hook. Skip messages <20 chars, no named entities, no decision verbs. Catches ~60-70% of procedural noise. | Add to `v1-strategy-2026-07-09.md` §3 (V1 scope) or Tortoise `BUILD_PLAN.md` Piece 2 |
| **7. Summarization** | ❌ Not in V1. Tortoise extracts from raw transcripts, not summaries. AAAK diary compression exists separately in MemPalace. | N/A for V1 |
| **8. Amortization** | 📣 Pitch angle, not architecture. | Comms only |

**One thing to add to V1:** pre-filter for chat noise (§6). A lightweight check before the segment reaches the extractor:

```python
def is_memory_worthy(segment: str) -> bool:
    if len(segment) < 20:
        return False
    if not any(detector.has_named_entities(segment), detector.has_decision_verbs(segment)):
        return False
    return True
```

This is cheap (regex + word list, no LLM) and prevents wasted extraction calls on procedural noise.

---

## 5. What Gets Added to Which Document

| Document | Add |
|----------|-----|
| **`embedding-retrieval.md`** | §7: Extraction model decision (DeepSeek V4 Flash, rationale). §6.1: GitHub source ingestion (field mapping, locator format, summary strategy). |
| **`v1-strategy-2026-07-09.md`** | §2 (modular boundaries): Add model decisions to swappable interfaces table. Current impl column already shows DeepSeek V4 Flash for LLM — confirm and document rationale. |
| **New or appended to embedding-retrieval** | §8: Write-cost strategy mapping — what's already covered, what's V2. Pre-filter for chat noise if implementing now. |
| **Tortoise `BUILD_PLAN.md`** | Nothing — already covers batch/stream idempotency, cursor tracking, dedup. Model routing is already config-driven via `OpenAICompatModel`. |

---

## 6. What V1 Does NOT Need

| Not needed | Why |
|-----------|-----|
| Multi-tier model routing (local vs cloud per operation) | One LLM call in the entire pipeline. One embedding model. No routing logic. |
| Hardware detection / model selection per machine | Cloud extraction makes hardware irrelevant. Embedding runs on CPU everywhere. |
| Live streaming extraction | V1 is batch-first (v1-strategy §3). Toggle comes in V2. |
| LLM-based summarization | Not in the extraction pipeline. AAAK diary exists separately. GitHub sources use titles. |
| Belief revision LLM | Replaced by PGMax factor graph (deterministic). `source-operator-event.md` decision 2026-07-08. |
| Summarization-before-extraction | Tortoise extracts from raw transcripts. No summarization step. |

---

## 7. The Model Architecture in One Sentence

**Embedding is local and free; extraction is one cloud call per batch; everything else is graph queries or structured data — no other models exist in V1.**

---

*See `MEMORY_TYPES.md` for the 5-type memory taxonomy this architecture implements (semantic, episodic, epistemic, procedural, working).*
*See `source-operator-event.md` for the entity classes (Source #28, Operator #29, Event #30) and the NAND/IMPL + PGMax decision.*
*See `v1-strategy-2026-07-09.md` for product scope and competitive positioning.*
*See `embedding-retrieval.md` for the 3-tier retrieval model and hybrid query patterns.*
*See `2026-07-09-v2-integration-map.md` for the V2 data source and application landscape.*
