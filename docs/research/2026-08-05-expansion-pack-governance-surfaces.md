---
title: "Research: Expansion Pack Business Logic — Operationalization Surfaces"
type: engineering
domain: platform
doc_status: live
subjects.team: organisation-design-team
created: 2026-08-05
aboutObjects: tortoise, expansion-packs, falkordb
---

# Research: Expansion Pack Business Logic — Operationalization Surfaces

**Date:** 2026-08-05
**Status:** Decisions recorded (5/5 agreed)
**Type:** Architecture / methodology research

---

## Problem

Expansion packs declare domain ontology (kinds, relations, hierarchies) but do not
*operationalize* the domain business logic: nothing enforces the "pre-mandated"
relationships documented in the README, warns when an agent bypasses a middle step
in a domain hierarchy (e.g., Feature → Requirement without UserJourney → Workflow),
or nudges agents toward domain best practices.

The operator rework (#7801/#86, semantic-epistemic edge model with `label`) introduced
the predicate hook needed for write-time validation — the `label` field is currently
free-form and unchecked against pack-declared relations.

**Reframed problem:** "Agents (and SDK callers) writing to the Tortoise graph are
trying to record domain knowledge, but the pack manifests that encode domain
structure are advisory, which results in structurally-sloppy graphs that drift from
domain best practice."

## What We Have Internally

- **Pack registry** (`tortoise/pack_registry.py`) — declarative YAML manifests
  (`packs/*/manifest.yaml`) with namespaced kinds, `subclassOf`, `equivalentTo`,
  `relations` (predicate, fromKind, toKind, mechanism, semantics, cardinality),
  `hierarchies` (UI-only today), connectors, tools. Validation runs at *manifest
  load time* only.
- **`expand_kind()` / `list_relations()`** — pack-aware search expansion + queryable
  relation catalog. No write-path integration.
- **`create_operator()`** (SDK, ~line 470) — validates op_type and Point existence;
  does NOT check kind-pair against declared relations, cardinality, or hierarchy
  bypass. `label` (domain verb) is free-form.
- **MCP server** (`tortoise/mcp_server.py`) — ~40 tools; `tortoise_create_operator`
  mirrors SDK. No pre-flight validation tool.
- **Skills** — `how-to-use-tortoise` (generic graph-write safety), `tortoise-file-finding`.
  No per-pack procedural guidance.
- **Apps** — `apps/dashboard`, `apps/graph-viz` (governance/review surfaces exist,
  no violations feed).
- README documents intent: *"The relationships are pre-mandated — not arbitrary"*
  — aspiration only.

## External Findings

### Ontology constraint enforcement (KG world)
- SHACL/OWL validation with **transactional rollback** is the mature pattern
  (Neo4j n10s: `validateTransaction` before commit; APOC triggers).
  Inference must be materialized before validation when relying on derived classes.
- FalkorDB's own agent-memory guidance: schema constraints "prevent duplicate nodes
  and enforce edge types."

### Agent guardrails (agent world)
- Framework-level hooks (**BeforeToolCallEvent**) validate rules and cancel calls
  *before execution* — "rules that LLMs cannot bypass" require hooks in the execution
  path, not advisory instructions. [Medium — consistent across OpenAI Agents SDK,
  Strands, stackai guidance]
- Guardrail best practice: strict schema validation, fail closed on errors, keep
  checks simple/fast/separated by concern.

### Skills vs MCP vs subagents (surface selection)
- Consensus framing: **Skills = procedural knowledge (how), MCP = capability/access
  (connections), Subagents = isolation**. Start with a skill; add MCP when a
  capability is needed. [High — multiple independent guides agree]

### Agent memory / graph schema lessons
- "Agent memory is only as good as its schema": if the LLM invents structure,
  everything degrades to generic labels (`Topic`, `RELATES_TO`).
- Rigid schemas break as domains evolve; too-loose schemas make graphs overly
  connected. Schema evolution + synchronization drift are recurring failure modes.

### Adversarial / over-constraint
- **Format-Constraint Coupling in KG Construction** (arXiv 2605.21974): constraints
  applied rigidly can amplify errors — "extraction refusal," entity inflation.
- KG-RAG industrial research: unenforced schema constraints cause constraint-blind
  traversal and degraded retrieval.
- KG repair (ESWC 2024) is a real production governance need — schemas drift.

## Recommendation — Layered Defense (4 surfaces, 1 declarative source)

| Layer | Surface | Job | Mechanism |
|-------|---------|-----|-----------|
| 0 | **Manifest v2** | Declarative constraints | `requiredPath` (mandated hierarchy traversal + severity), per-relation `severity`; `hierarchies` gains enforcement semantics (today: UI-only) |
| 1 | **SDK write-time validation** | Guarantee | Hook inside `create_operator()` (and `create_edge`) consulting pack registry: BLOCK undeclared kind-pairs / mechanism mismatch / cardinality violation; WARN hierarchy bypass (write + return structured warnings + log violation event) |
| 2 | **MCP pre-flight tool + pack skills** | Nudge | `tortoise_check_link(source_id, target_id, predicate)` dry-run before write; per-pack skill (generated skeleton from manifest + hand-written methodology section) |
| 3 | **Governance app** | Human loop | Violations log + repair queue + override approval in dashboard |

**Why not a single surface:**
- MCP-tool-only → agents can skip tools; zero guarantee (guardrail literature).
- Skill-only → probabilistic compliance; skills reduce but don't eliminate errors.
- Enforcement-only → over-strict schemas cause extraction refusal / info loss
  (adversarial research) — hence warn-not-block default for hierarchy bypasses.
- App-only → reactive; bad edges exist before review.

**Key architectural decision:** enforcement lives in the **SDK** (shared by MCP,
graph-scripts, connectors, CLI) — the MCP server must not be the only enforcement
point, because it is not the only write surface.

## Decisions Recorded (2026-08-05)

1. **Severity policy:** hierarchy bypasses default to `warn`; `block` is per-pack opt-in.
2. **WARN semantics:** write + return structured warnings + log a violation event
   (event feeds the governance app).
3. **Pack skills:** hybrid — generated skeleton from manifest + hand-written
   methodology section.
4. **Tiering:** `warn` free at all tiers; full governance (violations app, overrides,
   kind lifecycle) tiered later — not this work.
5. **Bypass detection scope:** start with adjacency + one-hop checks (cheap); strict
   multi-hop path-traversal later if needed.

## Open Questions (deferred)

- Multi-hop path traversal cost/benefit for `requiredPath` enforcement (deferred by D5).
- Where the violation-event log lives (event log vs dedicated store) — implementation detail.
- Manifest v2 schema versioning (kind lifecycle governance — tiered, per D4).

## Source Confidence Summary

| Claim | Tier | Sources |
|-------|------|---------|
| Layered defense is the right pattern; no single surface suffices | High | Internal codebase + KG literature (SHACL/rollback) + guardrail literature + skills-vs-MCP consensus (4 categories) |
| Enforcement must live in SDK, not MCP-only | High | Guardrail research (execution-path hooks) + internal write-surface analysis (2 categories) |
| Warn-not-block default for bypasses | Medium | Adversarial KG research (format-constraint coupling) + agent-memory schema research (2 sources) |
| `label` field is the write-validation predicate hook | High | Internal direct evidence (create_operator + list_relations) |
| Manifest v2 `requiredPath`/`severity` keeps logic declarative | Medium | Internal manifest design + SHACL declarative-shape pattern (2 sources) |
