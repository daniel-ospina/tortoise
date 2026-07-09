---
title: "Competitor: LangChain Memory"
type: competitive-research
domain: product
status: seedling
summary: "LangChain Memory — conversation buffer and summary wrappers. Thin integration layer, no standalone product."
created: 2026-07-09
---

# LangChain Memory

> **Status:** Research draft

## What they do
Memory classes for LangChain agents: ConversationBufferMemory, ConversationSummaryMemory, VectorStoreRetrieverMemory. Wrappers around existing stores (vector DBs, chat history). Not a standalone product.

## Architecture
- Thin abstraction over storage backends
- Conversation buffer: stores raw messages
- Summary memory: LLM-summarized conversation history
- Vector store: embedding-based retrieval over past conversations
- No graph, no entities, no temporal model

## Weaknesses (our edge)
- **Not a product** — integration glue, not a memory system
- **No entity extraction** — raw text or LLM summaries only
- **No structure** — no points, operators, or relations
- **No epistemic layer** — cannot answer "why should I believe this?"
- **Stateless by default** — memory resets per session unless explicitly persisted

## Relevance to us
LangChain Memory is what people reach for first. Our core would replace it entirely — same integration pattern (drop-in memory for agents) but with an actual epistemic graph underneath.
