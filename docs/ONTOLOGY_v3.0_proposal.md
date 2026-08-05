# Ontology v3.0 — Proposed Changes (#7767)

**Status:** Proposed. Supersedes ONTOLOGY_v2.5.md §1.1 objectKind vocabulary.

---

## §1.1 Kind Tags — Object Kind Vocabulary (Revised)

| Entity | kind field | Vocabulary |
|--------|-----------|------------|
| Object | `objectKind` | **Core:** Project, WorkItem, document, user, skill, tool, agent, workflow, agreement, standard, other. **Expansion packs add domain-specific kinds via subclassOf.** |

### Core Object Kinds

| Kind | Description |
|------|-------------|
| **Project** | Work container. Universal. Dev: epic IS A Project. |
| **WorkItem** | Work unit. Universal. Can have sub-WorkItems. Dev: issue IS A WorkItem. PM: task IS A WorkItem. Marketing: content IS A WorkItem. Product: feature IS A WorkItem. |
| document | Universal — all domains produce documents |
| user, skill, tool, agent, workflow, agreement, standard, other | Universal |

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

## §1.2 Subclass Model

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

## §1.3 Equivalence Model

Packs declare equivalences between kinds across packs via `equivalentTo`:

```yaml
equivalentTo:
  issue: [pm:task]
```

Bidirectional: querying `dev:issue` also returns `pm:task`, and vice versa.

---

## §2. Semantic-Epistemic Edge Model

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

| Type | Mechanism | Propagation | Example |
|------|-----------|------------|---------|
| hasPart | IMPL | Bidirectional cascade (parts↔whole) | Epic hasPart Issue |
| addresses | IMPL | Unidirectional (A supports B) | Feature addresses Need |
| opposes | NAND | Unidirectional (A contradicts B) | Feature competesWith Competitor |

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

## §3. Expansion Pack Manifest Format

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

## §4. Migration

Existing Points with old core kinds are updated via `tortoise/migrate_kinds.py`:

```
product → product-strategy:product
epic → dev:epic
useCase → product-strategy:useCase
...
```
