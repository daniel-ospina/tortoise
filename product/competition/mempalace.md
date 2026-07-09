---
title: "Competitor: MemPalace"
type: competitive-research
domain: product
status: seedling
summary: "MemPalace — agent diary + knowledge graph + semantic search. Our current internal tool, assessed as a competitor for the modular product."
created: 2026-07-09
---

# MemPalace

> **Status:** Research draft — also our current internal tool

## What they do
Agent-native memory palace: semantic search over past conversations, knowledge graph with temporal validity, agent diaries, cross-session continuity. ChromaDB backend, local-first.

## Architecture
- ChromaDB for vector search
- SQLite for KG triples with `valid_from`/`valid_to`
- AAAK diary format for agent journals
- Mine command for ingesting codebases/docs
- Wings/rooms/tunnels for organizing memories

## Strengths
- **Agent-native** — designed for AI agents, not humans
- **Temporal validity** — facts have time windows
- **Cross-session** — auto-save hooks, wake-up summaries
- **Proven compression** — 500K+ drawers at scale
- **Local-first** — embedded, no cloud dependency

## Weaknesses (our edge)
- **No relevance model** — edges exist or do not exist, no contestability
- **No belief propagation** — KG is declarative, not epistemic
- **No operator logic** — no NAND/IMPL structure
- **Capture-first** — designed for archiving agent sessions, not reasoning over them
- **No extraction pipeline** — facts are added manually via `mempalace_kg_add`

## Role in our stack
MemPalace stays as our **capture + archive** layer — verbatim session storage. The epistemic core replaces its KG and retrieval functions. The two are complementary, not competitive: MemPalace archives, we reason.
