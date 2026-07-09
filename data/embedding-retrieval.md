---
title: "Embedding & Retrieval Specification"
type: data
domain: data
status: seedling
tags: [embedding, retrieval, falkordb, tortoise, hybrid-search]
summary: "Defines the embedding strategy and hybrid query patterns for the collapsed FalkorDB graph (tortoise epistemic + episodic + doc index)"
created: 2026-07-07
---

# Embedding & Retrieval Specification

> **Version:** 0.1.0 — draft
> **Status:** 🌱 Seedling — active design
> **Depends on:** ONTOLOGY.md (entity classes), tortoise/README.md (point/operator datatypes)

## 1. Three-tier retrieval model

Every entity type splits its fields across three tiers: embedded (vector search), filtered (Cypher WHERE), and on-demand (filesystem retrieval).

| | Embedded (384-dim vector) | Filtered (Cypher WHERE) | On demand (filesystem) |
|---|---|---|---|
| **Source** | title, summary, tags | created_at, speaker, affiliation | Full content via `locator` |
| **Doc** (Source subtype) | (same as Source) | (same as Source) | Full markdown body |
| **Point** | content, context | speaker, affiliation, created_at | ❌ Self-contained |
| **Operator** (Point subtype) | (same as Point) | + op_type (NAND/IMPL) | ❌ Graph structure |
| **Event** | description | event_type, created_at, actor | ❌ Self-contained |

### Tier rationale

- **Embedded:** Fields that carry semantic meaning. Title + summary tell you what a Source is about; content + context tell you what a Point claims. These go into the 384-dim vector for similarity search.
- **Filtered:** Fields that are structural, temporal, or categorical. Low-cardinality values (op_type, event_type) or ranges (created_at) are better handled as Cypher WHERE clauses — embedding them dilutes vector precision.
- **On demand:** Full document bodies stay in the filesystem. The graph stores a `locator`; an agent reads `type` from the locator structure to choose the right retrieval tool. This keeps the graph lean and avoids embedding noise from boilerplate/navigation/chrome text.

## 2. Embedding model

**Model:** `sentence-transformers/all-MiniLM-L6-v2` via fastembed (local, 384-dim, zero cost).

**Unified space:** All entity types embed into the same 384-dim vector space. A query vector matches against Sources, Points, and Events simultaneously — the label filter narrows the result set, not the embedding.

**Field concatenation for embedding:**

| Entity type | Embedded fields | Concatenation |
|---|---|---|
| Source / Doc | title + summary + tags | `"{title}. {summary}. Tags: {tags}"` |
| Point | content + context | `"[{context}] {content}"` |
| Operator | (same as Point) | (same as Point — operator structure is graph-native) |
| Event | description | `"{description}"` |

## 3. Hybrid query patterns

### Doc retrieval

```cypher
// "Find engineering docs about authentication from Q2"
CALL db.idx.vector.queryNodes('Doc', 'embedding', $query_vec, 20)
YIELD node
MATCH (node)
WHERE node.affiliation = 'eldato-app-team'
  AND 'engineering' IN node.tags
  AND node.created_at >= '2026-04-01'
RETURN node.title, node.summary, node.locator
```

### Point retrieval across sources

```cypher
// "What claims exist about database performance?"
CALL db.idx.vector.queryNodes('Point', 'embedding', $query_vec, 20)
YIELD node
MATCH (node)-[:EXTRACTED_FROM]->(s:Source)
RETURN node.content, node.context, s.title, s.locator
```

### Epistemic conflict resolution

```cypher
// "What evidence exists near this disputed claim?"
MATCH (p:Point {id: $point_id})
CALL db.idx.vector.queryNodes('Point', 'embedding', p.embedding, 10)
YIELD node
MATCH (node)-[:EXTRACTED_FROM]->(s:Source)
WHERE node.id <> p.id
RETURN node, s
```

### Episodic timeline

```cypher
// "What happened in Q2 around marketing campaigns?"
MATCH (e:Event)
WHERE e.event_type = 'campaign_launched'
  AND e.created_at >= '2026-04-01'
  AND e.created_at < '2026-07-01'
RETURN e.description, e.created_at, e.actor
ORDER BY e.created_at DESC
```

## 4. Scoring

Hybrid scoring combines vector similarity with graph connectivity:

```
score = vector_similarity × 0.7 + graph_connectivity × 0.3
```

Where `graph_connectivity` is the number of `[:EXTRACTED_FROM]` or `[:INPUT]` edges radiating from the node — well-connected nodes rank higher. Weights are initial defaults; tune empirically via `tortoise-eval`.

## 5. Design decisions

| Decision | Rationale |
|----------|-----------|
| Unified vector space across all types | Single query matches Sources, Points, and Events. Type filtering happens post-retrieval via labels |
| `op_type` and `event_type` filtered, not embedded | 2-8 values each — embedding them adds noise, not signal |
| `domain` lives in `tags`, not a separate field | Semantic domain is already captured in tags; filtering on it is a tag match, not a separate property |
| `speaker` not `team` | Attribution is personal (who said it), affiliation is organizational (on whose behalf) |
| `type` field on Source deferred | Agent infers retrieval tool from locator structure. Add `type` when ambiguity demands it |
| 384-dim (not larger) | Matches MemPalace + Graphiti embedder. Sufficient for semantic matching; larger dims increase cost without proportional gain at our scale |
## 6. Doc ingestion strategy

### Source format

Doc metadata is extracted from Schema B frontmatter — deployed across all 877 docs in `docs/teams/`. No LLM needed for ingestion; frontmatter is structured YAML.

| Frontmatter field | FalkorDB tier | Usage |
|---|---|---|
| `title` | Embedded | Semantic matching |
| `summary` | Embedded | Semantic matching |
| `tags` | Embedded | Domain + keyword matching |
| `created` | Filtered | Temporal WHERE clause |
| `subjects.team` | Filtered | Attribution/affiliation WHERE clause |
| `actions.produces` | Not in graph | Agent workflow metadata only |

### Ingestion pipeline

Markdown file -> parse YAML frontmatter -> populate Doc node, then concatenate title+summary+tags, run through fastembed (384-dim), store vector on Doc node.

**Key properties:**
- **Zero-LLM** — frontmatter is structured, no extraction needed
- **Idempotent** — re-running on the same file overwrites its node (keyed by locator)
- **Stateless** — reads frontmatter, not git state or timestamps
- **Batch-friendly** — all 877 docs process in a single pass

### Content-hash optimization

For production use (not first-time seeding), store a content hash alongside each Doc node to skip unchanged files on re-ingestion:



This turns a full re-ingestion (877 docs, ~30s) into a delta-only pass (3 changed docs, <1s). The hash is stored as a node property, not embedded — it is a dedup key, not searchable content.


### Relationship to tortoise

Docs are Sources per ONTOLOGY.md. The ingestion pipeline populates Doc nodes (Source subtype). Tortoise extraction runs *after* — consuming the doc body from the locator to extract Points and Operators. The doc metadata in the graph helps agents decide *which* docs to extract from, not how to extract.

---

*See `../product/v1-planning/2026-07-09-v1-model-architecture.md` for the V1 model routing decision (DeepSeek V4 Flash for extraction) and GitHub source ingestion spec.*
