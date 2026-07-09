# Proposal: Source, Operator, and Event entity classes

> **Status:** Ratified — 2026-07-08
> **Date:** 2026-07-07 (proposed), 2026-07-08 (ratified)

## Problem

ONTOLOGY.md is missing three entity classes needed for the collapsed FalkorDB architecture:

1. **Source** — where knowledge comes from. Currently Evidence (#18) is "Source document, data point, or observation" — conflating the container with the epistemic role.
2. **Operator** — NAND/IMPL between Points. Only mentioned in Point's definition, not first-class.
3. **Event** — episodic events. Current §2.6 Events are only work tasks (Epic/Project/Task), not the broader events the epistemic layer needs.

## Proposed additions

### Source (#28)

| Field | Notes |
|-------|-------|
| `locator` | Type-specific path (filepath, URL, channel/ts). Agent infers retrieval tool from format. |
| `title` | Display + semantic matching |
| `summary` | 1-2 sentence description |
| `tags` | Domain is in tags, not a separate field |
| `created_at` | Temporal filtering |
| `speaker` | Who authored/said it — personal, not organizational |
| `affiliation` | Role/Team/Org ref — on whose behalf |

Document (#16) is a Source subtype (locator = filepath). Transcripts and Slack messages are also Sources with different locator formats. `type` deferred until ambiguity demands it.

For embedding vs filtering: see `embedding-retrieval.md`.

### Operator (#29)

Operators ARE Points (#19) with an additional `operator` struct: `{op_type: NAND|IMPL, inputs: [PointId...]}`. Relevance = operator over plain points. Mitigation = operator where any input IS an operator (derived, never stored). Follows tortoise convention.

### Event (#30)

Any episodic occurrence — broader than work tasks. All stored in the same append-only JSONL event log as tortoise points. Projected as Event nodes in FalkorDB.

Subtypes: point_added, operator_revised, source_ingested, decision_made, campaign_launched, experiment_run, task_completed, content_published.

### Clarified: Evidence (#18)

Evidence IS a Source with an epistemic role — supports or contradicts a Point via graph edge, not a field on Evidence.

### Unchanged: Point (#19)

Already has `speaker` (Agent) and `affiliation` (Role/Team/Org). No changes.

## Decision (2026-07-08): Edge semantics — deterministic gates + factor graph BP

Adopted Tortoise's deterministic NAND/IMPL operator gates over the old probabilistic `supports`/`contradicts` edges. Decision record in Connor's repo at `tortoise/docs/probabilistic-scoring-design.md` (private — `connormcmk/negation-game-explorations`, tortoise-design branch). Reasoning:

- NAND/IMPL are ternary (A, B, relevance r) — old binary edges can't express relevance gating
- Factor graph + loopy belief propagation (PGMax) produces real marginal probabilities per point, not ordinal confidence scores
- QBAF (closest to the old model) was rejected: scores aren't real probabilities, no n-ary hyperedge support, no mitigation recursion
- BP fixed points = Bethe free energy stationary points (proven; Yedidia 2001)

⚠️ This decision lives in Connor's private repo. Consider copying the design doc to `epistemic-team/` if the repo moves.

## Related

- `embedding-retrieval.md` — what gets embedded vs filtered per entity type
- `tortoise/README.md` — point/operator datatypes (Connor's design)
- `2026-07-09-v1-model-architecture.md` — V1 model routing + write-cost synthesis
