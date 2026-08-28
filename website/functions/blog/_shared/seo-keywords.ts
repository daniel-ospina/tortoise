// Tortoise blog SEO keyword map — single source of truth for the AI SEO
// generator (#1861) and future content-pipeline automation.
//
// Port of ElDato `supabase/functions/_shared/seo-keywords.ts` (methodology only —
// keywords are Tortoise-domain-specific, see issue #1862).
//
// Contract (issue #1862 + #1861 scoping):
//   - Keys = the starter tag taxonomy (12 tags, documented in
//     docs/research/2026-08-28-tortoise-blog-keywords/research.md §1).
//   - Values = ACTIONABLE keyword arrays only. Tiers, volumes, difficulty and
//     source confidence live in the master doc — do NOT duplicate them here
//     (single source of truth for generation).
//   - Tags are free-form text[] on blog_posts (≤10 per post, no migration) —
//     this module defines the canonical starter vocabulary the generator keys on.
//   - Content-driven fallback: the generator's buildPrompt falls back to
//     content-derived keywords when a post's tags have no entry here.
//   - English-only v1 (blog epic deferred i18n).
//
// Usage in a Pages Function:
//   import { TAG_KEYWORDS } from "../_shared/seo-keywords";
//   const kw = tags.flatMap((t) => TAG_KEYWORDS[t] ?? []); // content fallback when []
//
// ZERO-DEPENDENCY (plain TS, no imports) — matches the repo's Pages Functions
// pattern (no bundling, underscore-prefixed helpers are not routed).

export type TagKeywords = Record<string, string[]>;

export const TAG_KEYWORDS: TagKeywords = {
  // Umbrella category: memory systems for AI agents across sessions.
  "agent-memory": [
    "long-term memory for ai agents",
    "what is agent memory",
    "ai agent memory",
    "memory for llm agents",
    "llm agent memory",
    "how to build agent memory",
    "agent memory architecture",
    "rag vs agent memory",
    "agent memory framework",
    "open source agent memory",
    "agent memory benchmark",
    "agent memory comparison",
    "persistent memory for agents",
    "memory for autonomous agents",
    "ai memory system",
    "agents forget context",
    // value-prop layer (§4.13): the conceded pain + outcome language
    "context rot",
    "context rot ai agents",
    "memory drift",
    "ai agent memory drift",
    "why ai agents forget",
    "why do agents forget",
    "agent gets dumber over time",
    "ai agents get worse over time",
    "agent autonomy",
    "autonomous agent memory",
    "increase agent autonomy",
    "smarter ai agents",
    "make ai agents smarter",
    "improve agent performance with memory",
    "self-evolving agents",
    "self-improving agents",
  ],

  // Tortoise core differentiator: memory as BELIEF — claims, evidence,
  // confidence, contradiction. Strategic tier (category creation): near-zero
  // current volume, near-zero competition, high intent.
  "epistemic-memory": [
    "epistemic memory",
    "epistemic memory ai",
    "epistemic memory vs semantic memory",
    "what is epistemic memory",
    "belief graph",
    "belief graph database",
    "knowledge graph with confidence",
    "memory that tracks belief",
    "agent memory with confidence",
    "epistemic knowledge graph",
    "claims and evidence memory",
    // value-prop layer (§4.13): the learning/stays-current promise
    "stale ai memory",
    "summarization drift",
    "memory that learns",
    "self-evolving agent memory",
    "self-correcting memory",
    "agents learn from experience",
    "agent improves over time",
    "continual learning agents",
  ],

  // Graph-structured memory: knowledge graphs, graph DBs, GraphRAG, graph vs vector.
  "knowledge-graph": [
    "knowledge graph for ai agents",
    "knowledge graph rag",
    "graph rag",
    "graphrag vs rag",
    "knowledge graph vs vector database",
    "graph database vs vector database",
    "graph based agent memory",
    "knowledge graph memory",
    "graph memory for agents",
    "temporal knowledge graph",
    "knowledge graph for llms",
    "graph database for agent memory",
    "knowledge graph tutorial",
    "multi-hop reasoning",
  ],

  // Fact/concept memory (Tulving's semantic system).
  "semantic-memory": [
    "semantic memory ai",
    "semantic memory vs episodic memory",
    "semantic memory for agents",
    "semantic memory llm",
    "what is semantic memory",
    "fact memory ai",
    "knowledge base for agents",
    "semantic knowledge graph",
  ],

  // Event/timeline memory (Tulving's episodic system) — sessions, occurrences.
  "episodic-memory": [
    "episodic memory ai",
    "episodic memory vs semantic memory",
    "episodic memory for agents",
    "event log for agents",
    "what is episodic memory",
    "session memory ai",
    "conversation history for agents",
  ],

  // Model Context Protocol — servers, memory servers, tool integration.
  // Head MCP terms skipped (protocol-owned SERP); intersection terms only.
  "mcp": [
    "mcp memory server",
    "mcp server for agents",
    "mcp knowledge graph",
    "mcp server memory",
    "mcp memory",
    "mcp vs api",
    "how to build an mcp server",
    "mcp server tutorial",
  ],

  // Running Tortoise on your own infra — Docker, FalkorDB, privacy/compliance.
  "self-hosting": [
    "self-hosted agent memory",
    "self-hosted ai memory",
    "falkordb",
    "falkordb vs neo4j",
    "what is falkordb",
    "run ai memory locally",
    "local llm memory",
    "docker agent memory",
    "self-hosted rag",
  ],

  // Getting memory back — hybrid search, RAG, vector search, multi-hop.
  "retrieval": [
    "hybrid search",
    "vector search vs hybrid search",
    "hybrid retrieval",
    "reciprocal rank fusion",
    "semantic search for agents",
    "multi-hop retrieval",
    "context engineering",
    "retrieval for llm agents",
    "vector database for agents",
    "agent context retrieval",
    "why agents hallucinate",
  ],

  // The engine: EP (expectation propagation), confidence, uncertainty.
  "belief-propagation": [
    "belief propagation",
    "expectation propagation",
    "belief propagation graph",
    "confidence score ai",
    "uncertainty in knowledge graphs",
    "probabilistic knowledge graph",
    "evidence propagation",
    "belief propagation for agents",
    "expectation propagation agents",
    "confidence propagation",
  ],

  // How memory gets written — session capture, conversation indexing, mining.
  "sessions": [
    "session capture ai",
    "conversation mining",
    "agent session logging",
    "mine conversations for insights",
    "episodic memory capture",
    "meeting memory ai",
  ],

  // Landscape/comparison content — Mem0 vs Graphiti vs Letta vs Cognee vs Tortoise.
  "memory-systems": [
    "mem0 vs letta",
    "mem0 vs zep",
    "mem0 vs graphiti",
    "letta vs zep",
    "zep vs graphiti",
    "cognee vs mem0",
    "letta vs mem0",
    "agent memory tools",
    "best agent memory",
    "agent memory platforms",
    "open source agent memory comparison",
    "memory layer for agents",
  ],

  // Where memory came from — source attribution, auditability, trust.
  "provenance": [
    "ai provenance",
    "provenance for ai agents",
    "source attribution ai",
    "traceable ai memory",
    "auditable ai memory",
    "where did the ai get that",
    "agent memory audit trail",
    "ai memory privacy",
    "data provenance ai",
    "trust in ai agents",
    "explainable ai memory",
  ],
};
