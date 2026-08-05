---
title: "Tortoise — Canonical Ontology v3.0"
type: data
domain: data
status: live
created: 2026-08-05
updated: 2026-08-05
ownedBy: epistemic-team
doc_status: live
---

# Tortoise — Canonical Ontology v3.0

> **Status:** LIVE — canonical. Co-located with the code it governs (tortoise repo).
> **Supersedes:** ONTOLOGY_v2.5.md (eldato repo, deprecated).
> **Convention:** camelCase throughout. `kind` = classification tag on an entity. `predicate` = named edge between entities.

---

## §1. Entity Types

Five core types (Action removed in v3.0).

| # | Type | ISO/PROV Mapping | Definition | Label |
|---|------|-------------------|------------|-------|
| 1 | **Subject** | `prov:Agent` / `org:Organization` / `foaf:Person` | Any entity that can act | Who acts |
| 2 | **Object** | `prov:Entity` / `schema:Thing` | Persistent things that exist, are produced, or are acted upon | What persists |
| 3 | **Point** | `prov:Entity` (specialized) | A node in the belief graph — claims, decisions, structural artifacts | What we believe |
| 4 | **Event** | `prov:Activity` (instantiated) / `schema:Event` | Temporal occurrence — the verb. Reified middle node: (Subject)-[performs]->(Event)-[produces]->(Object) | What happened |
| 5 | **Source** | `prov:Entity` (provenance) / `pav:Source` | Provenance anchor — where content was extracted from | Where it came from |

> **Action dissolved (v3.0):** The Action entity no longer exists. The verb ("deploy", "create") is an Event (eventKind, append-only, timestamped). The artifact ("deployment", "document") is an Object with status projected from its event stream. See §5.

---

## §2. Four-Ontology Model

Each layer answers a different question.

| Layer | Question | Entity | Notes |
|-------|----------|--------|-------|
| **Semantic** | Who/what exists? | Subject, Object, Document, Source | Nouns. Standing structural relations (owns, memberOf) via plain edges. |
| **Epistemic** | What do we believe and why? | Point, Operator (IMPL/NAND + label + EP confidence) | Operators ONLY here: Event→Point (outcome), Point→Point (belief). |
| **Episodic** | What happened when? | Event | Verbs. Reified middle node: (Subject)-[performs]->(Event)-[produces]->(Object). Append-only, timestamped. |
| ~~Procedural~~ | ~~Current state of work~~ | ~~Action~~ | **Dissolved.** Verb → Event. Artifact → Object. Status → projection of event stream. |

### Structural vs Epistemic Edges

| Edge | Type | Confidence | Example |
|------|------|-----------|---------|
| performs / produces / uses / owns / memberOf / authoredBy / ownedBy / managedBy | **Structural** (plain) | None (factual) | (User)-[performs]->(Event), (User)-[owns]->(Doc) |
| Event→Point, Point→Point | **Epistemic** (operator) | EP confidence | (Event:deployFailed)-[NAND]->(Point:"deploy succeeded") |

**Principle:** Operators connect only epistemic targets (Event→Point, Point→Point). Subjects connect via plain structural edges. Evaluations of subjects (expertise, reliability) are Statements (Points) with EP confidence — not edges. Reputation is derived at query time. Facts = confidence 1.0.

---

## §3. Edge Topology

### §3.1 Point ↔ Point (Epistemic — Operators)

| Predicate | Direction | Cardinality | Mechanism | Meaning |
|-----------|-----------|-------------|-----------|---------|
| `IMPL` | A → B | N-ary | Epistemic | A supports/implies B. Confidence via EP. |
| `NAND` | A ↔ B | N-ary | Epistemic | A contradicts B (symmetric propagation). |
| `hasPart` | A → B | N-ary | Structural via operator label | A contains B (parts/whole cascade). |

### §3.2 Point → Source (Provenance)

| Predicate | Direction | Meaning |
|-----------|-----------|---------|
| `extractedFrom` | Point → Source | This claim was extracted from this source. |
| `references` | Source → Entity | The source references this entity. |

### §3.3 Source → Entity (Provenance)

`(Point)-[:extractedFrom]->(Source)-[:references]->(Entity)` — layered provenance. Source carries `sourceKind` (T0-T4 credibility tier).

### §3.4 Subject → Event → Object (Procedural — v3.0)

| Predicate | Direction | Cardinality | ISO/PROV | Meaning |
|-----------|-----------|-------------|----------|---------|
| `performs` | Subject → Event | N-ary | `prov:wasAssociatedWith` | Who executed the event |
| `produces` | Event → Object | N-ary (1→many) | `prov:wasGeneratedBy` | Output artifact the event created |
| `uses` | Event → Object | N-ary | `prov:used` | Input the event consumed |
| `wasDerivedFrom` | Object → Object | N-ary | `prov:wasDerivedFrom` | Entity derivation (distinct from Source provenance) |

### §3.5 Subject ↔ Subject (Organisational)

| Predicate | Direction | Meaning |
|-----------|-----------|---------|
| `participatesIn` | Subject → Event | Subjects involved in an event |
| `memberOf` | Subject → Subject | Membership in team/group |
| `managedBy` | Entity → Subject | Operational responsibility (RACI Responsible) |
| `ownedBy` | Entity → Subject | Accountability, data boundary (RACI Accountable) |

### §3.6 Object ↔ Object

| Predicate | Direction | Meaning |
|-----------|-----------|---------|
| `wasDerivedFrom` | Object → Object | Derivation |
| `hasPart` / `partOf` | Object → Object | Composition |

### §3.7 Event Edges

| Predicate | Direction | Meaning |
|-----------|-----------|---------|
| `performs` (in) | Subject → Event | Actor |
| `produces` / `uses` | Event → Object | Output / input |
| `nextEvent` | Event → Event | Sequencing (Graphiti NextEpisode equivalent) — planned |
| `op: IMPL/NAND` | Event → Point | Outcome influence on belief (epistemic) |

---

## §4. Entity Metadata

### §4.1 Point

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `pointKind` | string | ✅ | statement, decision, vision, strategy, plan, goal, target, observation, hypothesis + pack pointKinds |
| `content` | string | ✅ | The claim text |
| `context` | string | — | Namespace context |
| `confidence` | float | — | EP posterior mean (0-1), computed |
| `is_operator` | bool | — | true for operator Points |

### §4.2 Subject

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `subjectKind` | string | ✅ | organization, team, role, legalPerson, naturalPerson, other |

### §4.3 Object

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `objectKind` | string | ✅ | Project, WorkItem, document, user, skill, tool, agent, workflow, agreement, standard, other + pack objectKinds |
| `status` | string | — | Projected from event stream (in_progress, completed, failed) — NOT stored truth |

### §4.4 Document

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `documentKind` | string | ✅ | research, reflectPostmortem, strategyDoc, visionDoc, planDoc, decisionDoc, meetingNotes, experimentResults, evidenceLog, handoff, transcript, roadmap, brief + pack documentKinds |

### §4.5 Event

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `eventKind` | string | ✅ | meeting, decision, experiment, deployment, review, friction, extraction, documentCreated, roleCreated, pointAdded + pack eventKinds |
| `eventId` | string | ✅ | Unique occurrence ID |
| `startedAt` / `endedAt` | ISO8601 | — | Temporal extent (on the Event, per PROV-O/OWL-Time) |

### §4.6 Source

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `sourceKind` | string | ✅ | T0-T4 credibility tier |
| `url` | string | ✅ | Provenance anchor |

---

## §5. Kind Vocabulary (v3.0)

### Object Kind Vocabulary

| Entity | kind field | Vocabulary |
|--------|-----------|------------|
| Object | `objectKind` | **Core:** Project, WorkItem, document, user, skill, tool, agent, workflow, agreement, standard, other. **Expansion packs add domain-specific kinds via subclassOf.** |

### Expansion Pack Kinds (moved from core)

| Old Core Kind | New Pack Kind | Pack |
|--------------|--------------|------|
| product | product-strategy:product | Product Strategy |
| customer | product-strategy:customer | Product Strategy |
| competitor | product-strategy:competitor | Product Strategy |
| epic | dev:epic (subclassOf Project) | Development |
| code, api, database, software, infrastructure, deployment, indicator | dev:* | Development |
| task | dev:issue or pm:task (subclassOf WorkItem) | Dev / PM |

---

## §6. Subclass Model

Packs declare subclasses of core kinds via manifest `subclassOf`:

```yaml
objectKinds:
  - epic
  - issue
subclassOf:
  epic: Project
  issue: WorkItem
```

At query time, `expand_kind("Project")` returns `["Project", "dev:epic"]`. Queries filter by `pointKind IN [...expanded...]`.

---

## §7. Equivalence Model

Packs declare equivalences between kinds across packs via `equivalentTo`:

```yaml
equivalentTo:
  issue: [pm:task]
```

Bidirectional: querying `dev:issue` also returns `pm:task`, and vice versa.

---

## §8. Semantic-Epistemic Edge Model

Every relationship operates on two layers through a single operator Point:

```
Semantic:   (Feature) ──[addresses]──→ (CustomerNeed)    ← operator.label
Epistemic:        ↑ IMPL, confidence: 0.85               ← EP propagation
Operator:      (op-123)                                   ← mitigation anchor
```

| Layer | Where | What |
|-------|-------|------|
| Semantic | operator.label | Domain verb: addresses, hasPart, opposes |
| Epistemic | IMPL/NAND edges | Confidence via EP (0-1 continuum) |
| Operator | Point (is_operator:true) | Mitigation target, evidence anchor |

### Semantic Types

| Type | Mechanism | Epistemic propagation | Semantic label direction | Example |
|------|-----------|------------|-------------------------|---------|
| hasPart | IMPL | Bidirectional cascade (parts↔whole) | bidirectional | Epic hasPart Issue |
| addresses | IMPL | Unidirectional (A supports B) | unidirectional | Feature addresses Need |
| opposes | NAND | Symmetric (A↔B) | declared by pack | Feature competesWith Competitor |

### Pack Relation Declarations

```yaml
relations:
  - predicate: decomposesInto
    mechanism: IMPL
    semantics: hasPart
    fromKind: dev:epic
    toKind: dev:issue
```

---

## §9. Expansion Pack Manifest Format

```yaml
namespace: dev
name: "Development"
ontology:
  extends: core
  objectKinds: [epic, issue, code]
  subclassOf: {epic: Project, issue: WorkItem}
  equivalentTo: {issue: [pm:task]}
  pointKinds: [requirement, bug]
  documentKinds: [architectureDoc, apiSpec]
  relations:
    - predicate: decomposesInto
      mechanism: IMPL
      semantics: hasPart
      fromKind: dev:epic
      toKind: dev:issue
  hierarchies:
    - path: "Epic → Issue"
```

---

## §10. Epistemic Recency Modulation

Evidence aging is **user-configurable with a light default** — NOT blunt time decay. Stable facts stay strong regardless of age. Interacts with sourceKind/credibility tier (T0 direct observation ages differently than T4 speculation). Never auto-deprecates old evidence.

---

## §11. Reputation (derived, not stored)

`compute_reputation(subject_id)` is a query-time primitive:

```
subject -[:performs]-> events → outcome operators (Event→Point IMPL/NAND)
  → aggregate success/failure, optionally weighted by recency
  → return reputation score
```

- Not stored (would go stale)
- Bridges procedural history to epistemic belief: "how much weight should this agent's claim carry?"

---

## §12. Migration

Existing Points with old core kinds are updated via `tortoise/migrate_kinds.py`:

```
product → product-strategy:product
epic → dev:epic
useCase → product-strategy:useCase
...
```

---

## §13. Relationship to Standards

| Standard | Alignment |
|----------|-----------|
| **PROV-O** | Subject→Agent, Event→Activity, Object→Entity. `performs`=wasAssociatedWith, `produces`=wasGeneratedBy, `uses`=used, `wasDerivedFrom`=wasDerivedFrom. |
| **Schema.org** | Event with startTime/endTime. Action pattern (agent/object/result) mapped to Event (performs/produces). |
| **OWL-Time** | Event is the temporal entity (startedAt/endedAt). |
| **RDF-star** | Operators are reified edges with metadata (label + confidence) — RDF-star-like reification for epistemic edges. |
