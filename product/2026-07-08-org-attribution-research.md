---
title: "Organizational Attribution in Epistemic Graphs — Research Brief"
type: product
domain: product
status: live
summary: "Research on how organizational knowledge management systems, agent memory platforms, and provenance standards handle attribution of knowledge to individuals and organizational units — informing Tortoise's speaker/affiliation model."
created: 2026-07-08
---

# Organizational Attribution in Epistemic Graphs — Research Brief

**Date:** 2026-07-08
**Context:** Designing the `speaker` + `affiliation` fields for Point in the Tortoise epistemic graph.

## Problem

Points need two-axis attribution:
- **Who** said it (Agent — human or AI)
- **On whose behalf** (organizational unit — Role, Team, or Organization)

The spectrum runs from solo vibecoders (one person, multiple products, no formal org) to structured holacracies (defined roles, team membership, Nancy→Mark role transitions). The model must handle all resolutions without forcing premature organizational structure.

## Findings

### 1. PROV-O: `actedOnBehalfOf` is the W3C standard

The canonical provenance ontology models exactly this split:
- `prov:Agent` performs an activity
- `prov:actedOnBehalfOf` — Agent A acts on behalf of Agent B; B retains responsibility
- Standard example: `:derek` `actedOnBehalfOf` `:national_newspaper_inc`

This is a delegation pattern, not direct attribution. The acting agent is recorded; the responsible agent is the organizational unit. Maps directly to `speaker` (acting Agent) + `affiliation` (responsible Actor).

### 2. Mem0: four-scope memory model

Mem0 tags every memory write with at least one of: `user_id`, `agent_id`, `run_id`, `app_id`/`org_id`. Key behaviors:
- Passing only `user_id` returns records where `org_id` is null (solo mode)
- Queries compose: "all memories for user X within org Y"
- At least one ID required per write

This is the closest production implementation of our model. Maps: `user_id` → `speaker`, `org_id` → `affiliation`.

### 3. Personal Knowledge Management: flat tagging, emergent structure

PKM systems (Obsidian, Roam, InfraNodus) have converged on flat, non-hierarchical tagging over folder hierarchies:
- "Start lazily, let patterns emerge from accumulated material"
- Tags should be easy to remember, concrete, and enable productive behavior
- Wiki-links over folders — notes belong to multiple categories simultaneously

Validates `affiliation` as a flat string (not a structured FK). Resolution can deepen over time without migration.

### 4. Agent memory: episodic vs semantic split

Cognitive science splits declarative memory into:
- **Episodic** — event-specific: "the user told me about the bug on Tuesday"
- **Semantic** — persistent: "the project uses PostgreSQL"

Agent systems mirror this:
- **Episodic** (`speaker`) — who said it in this moment. Session-scoped, ephemeral.
- **Semantic** (`affiliation`) — whose position is this. Cross-session, persistent.

Robust attribution requires separating these layers. Conflating them leads to misattribution of decisions and broken accountability chains.

### 5. Enterprise knowledge graphs: typed relationships over flat attribution

Enterprise KGs model ownership as explicit typed relationships (`owner of`, `managed by`, `participant in`) — not flat author metadata. Wiki-style flat attribution can't answer "who manages the team that owns this decision?" Typed relationships enable multi-hop reasoning.

### 6. KM research: individual vs organizational tension

Knowledge management literature documents a persistent tension: knowledge work happens at the individual level, but ownership and accountability exist at the organizational level. Organizations resolve this through rules and cross-functional teams — not any single data field. Don't try to collapse both axes into one.

## Decision

**`speaker` + `affiliation`** — two independent, optional fields on Point:

| Field | References | Resolution | Example |
|---|---|---|---|
| `speaker` | Agent (#4) | Always individual | `"daniel"`, `"deepseek-v4"` |
| `affiliation` | Role, Team, or Organization | Whatever you know | `"content-strategist"`, `"eldato:app-team"`, `"eldato"` |

- `affiliation` is a flat string with colon-separated resolution: `org:team:role`
- Queries compose: `affiliation="eldato"` → prefix matches all child resolutions
- When a role becomes a team, old strings remain valid as historical attribution
- Maps to PROV-O `actedOnBehalfOf` semantics

## References

- `docs/teams/organisation-design-team/data/ONTOLOGY.md` — canonical entity definitions
- `tortoise/README.md` — Point datatype (connormcmk/negation-game-explorations, tortoise-design)
- `docs/teams/epistemic-team/product/v1-planning/2026-07-07-source-operator-event.md` — proposed Source/Operator/Event entities
- Mem0 entity-scoped memory: `docs.mem0.ai/platform/features/entity-scoped-memory`
- PROV-O: `https://www.w3.org/TR/prov-o/`
