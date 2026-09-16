# Supermemory

> Agent memory + retrieval platform. **"Memory is not RAG"** — a two-phase ingest where a
> custom "learning" model consolidates documents into a **derived** fact graph plus an
> always-injected **Profile**. Bootstrap profile added 2026-09-11 (was mentioned in
> `_analysis.md`/`zep.md` but had no registry entry). Confidence is marked per claim.

## 1. Overview

Supermemory (`supermemoryai`) is an agent-memory + retrieval system built around a **vector-graph
database**. Ingest is two-phase: (1) chunk → *contextual chunking* → embed → index (document
`status: done` = chunks searchable); (2) **"dreaming"** — a second pass by their **custom learning
model** that merges, arranges and links content into **graph memories** (facts, updates,
relations, time). The homepage frames the model as **`learner-1`** learning "for users, tasks, and
tenants". Retrieved memories/profiles are injected into the model context in real time.
[Source: supermemory.ai/docs/concepts/how-it-works · github.com/supermemoryai/supermemory · MEDIUM]

## 2. Product Type

Managed agent-memory platform (vector-graph DB + search/traversal API + profiles). Positions as
**memory + retrieval in one system** ("Memory is not RAG").

## 3. Positioning & Messaging

- "Memory is not RAG."
- The repo states it **extracts facts from conversations** and handles **temporal changes,
  contradictions, and automatic forgetting**. [Source: GitHub README · MEDIUM]
- Self-reported: *"#1 on every major AI memory benchmark"* (LongMemEval/LoCoMo/ConvoMem),
  "95 % Recall@15", "99.4 % context reduction" — **no protocol or methodology published**.
  [**self-reported; LOW confidence**]

## 4. Target Audience

Developers wiring long-lived memory into agents/apps; multi-tenant builders (container-tag
isolation is a first-class concept).

## 5. Business Model & Pricing

Hosted API; **Search and traversal** is a paid feature ("semantic search and graph traversal
across stored content in one call"). Re-ingesting a document under the same `customId` drives
**diff-billing updates** (only the delta is re-processed/billed). ⚠️ Exact tier prices not
captured in this profile — verify against `supermemory.ai/pricing` before quoting. [MEDIUM]

## 6. Product & Features

| Capability | Detail |
|---|---|
| Ingest | Two-phase: chunks (raw grounding) then **dreaming** → graph memories. `dreaming: "dynamic"` (production default) groups related documents so memories form from coherent units; `"instant"` dreams each document alone. |
| Storage | "Fact-based **temporal graph**" with vector + FTS + graph built in. |
| Outputs | Per document: **chunks**, **memories** (derived facts), and a **Profile** — "a sample of memories, static + dynamic summary for **always-on context**". |
| Context injection | Profiles are injected **every turn without re-searching**; memories/chunks are fetched on demand via the Search API. |
| Isolation | `containerTag` = user/tenant hard boundary. |
| Update semantics | Re-ingest with the same `customId` → diff-billing update. **No public documentation of DELETE/invalidation semantics** beyond update-on-reingest. |

[Sources: supermemory.ai/docs/concepts/how-it-works · /docs/quickstart · /docs/integrations/opencode · MEDIUM]

## 7. Go-to-Market & Acquisition

Developer-first: docs-led, MCP/agent-framework integrations, open-source repo presence.
⚠️ GTM detail not captured in this bootstrap profile.

## 8. Traction & Scale

⚠️ Not captured (stars/downloads/funding). Verify before citing.

## 9. Online Presence & Content

Docs site + GitHub (`supermemoryai/supermemory`) + benchmark claim in the README. ⚠️ Partial.

## 10. Community & Ecosystem

⚠️ Not captured in this bootstrap profile.

## 11. Customer Sentiment

⚠️ Not captured. Search the profile's own benchmark claim is **unsourced**, which is itself a
sentiment/falsifiability signal worth tracking.

## Notes & Sources

**Why it matters to Tortoise (mechanism read):**
- **The "dreaming" step is a distillation step.** Its outputs (memories, Profile) are *derived*
  facts — so the **dated evidence is consolidated away**. This is the same shape as Mem0/Letta
  profiles, and the opposite of a validity-preserving design (Zep's `valid_at`/`invalid_at`,
  Tortoise's supersession). In-repo brief (`2026-09-09-competitor-memory-architecture.md`):
  *"distilled-fact consolidation trades away the dated evidence Tortoise needs for
  temporal/state questions."*
- Relevant to **#2976** (temporal): supermemory *claims* to handle "temporal changes" and a
  "temporal graph", but the retrieval-side temporal mechanism is **not published** — unlike
  Hindsight's TEMPR, which names a dedicated temporal search leg. Treat the temporal claim as
  unverified.
- Retrieval-side selection (how many memories, which, at what budget) is **undocumented** —
  the same opacity that made #2985/#2952 (silent degraded retrieval) invisible.

**Confidence:** bootstrap profile (2026-09-11) from in-repo research + docs search. Sections 7–11
are **incomplete** and must be finished (or the profile marked partial in `_index.md`) before any
external/pitch use. **No published benchmark number from this vendor should be cited** — the
headline claim has no methodology.

*Created: 2026-09-11 · Bootstrap: research-gap fill for the #2952/#2976 decisions.*
