---
title: "Competitor: Mem0"
type: competitive-research
domain: product
status: seedling
summary: "Mem0 — LLM memory layer with user profiles. Flat memory, no epistemic structure."
created: 2026-07-09
---

# Mem0

> **Status:** Research draft

## What they do
LLM memory layer that stores user preferences, facts, and conversation history. Designed as a drop-in memory backend for AI agents. Provides user profiles that persist across sessions.

## Architecture
- Vector store + optional graph (Pro tier)
- 2-call LLM extraction loop (extract facts → store)
- No temporal model — overwrites facts rather than versioning them
- No ontology — flat key-value memory

## Weaknesses (our edge)
- **No relevance model** — facts are asserted, not contested
- **No belief propagation** — no mechanism for "why should I believe this?"
- **Overwrites facts** — no temporal validity, no contradiction handling
- **User-centric** — designed for personalization, not organizational knowledge
- 49% LongMemEval score on graph variant

## Pricing
Free tier: 1000 memories. Pro: usage-based.

## Relevance to us
Closest in spirit to what we are building (LLM memory), but flat. Our tortoise operators make relevance contestable — Mem0 has no equivalent.
