---
title: "Tortoise — Canonical Ontology v3.0"
type: data
domain: data
status: live
created: 2026-07-20
updated: 2026-08-05
ownedBy: epistemic-team
doc_status: live
---

# Tortoise — Canonical Ontology v3.0

> **Status:** LIVE — canonical. Co-located with the code it governs (moved from eldato docs repo 2026-08-05, #7869).
> **v3.0 changes:** Action entity DISSOLVED → Event (verb) + Object (noun). Operators scoped to epistemic (Event→Point, Point→Point). See §5 for the four-ontology model.
> **Supersedes:** ONTOLOGY_v2.5.md (eldato repo), ONTOLOGY_v2.0, ONTOLOGY_UPDATE_v2.1.md.
> **Convention:** camelCase throughout. `kind` = classification tag on an entity. `predicate` = named edge between entities.

---

> ⛔ **HUMAN-APPROVAL GATE:** This document is the canonical ontology. Any edit requires explicit human approval — no automated or agent-driven changes. To propose changes: create a PR with the proposed diff and request review from organisation-design-team.

## §1. Entity Types

Six core types. Every entity in the system is exactly one of these.

| # | Type | ISO/PROV Mapping | Definition | Label |
|---|------|-------------------|------------|-------|
| 1 | **Subject** | `prov:Agent` / `org:Organization` / `foaf:Person` | Any entity that can perform an Action | Who acts |
| 2 | **Object** | `prov:Entity` / `schema:Thing` | Persistent things that exist, are produced, or are acted upon | What persists |
| 3 | **Point** | `prov:Entity` (specialized) | A node in the belief graph — carries claims, decisions, and structural artifacts | What we believe |
| 4 | **Event** | `prov:Activity` (instantiated) / `schema:Event` | Temporal occurrence — the verb. Reified middle node: (Subject)-[performs]->(Event)-[produces]->(Object) | What happened |
| 5 | **Source** | `prov:Entity` (provenance) / `pav:Source` | Provenance anchor — where content was extracted from | Where it came from |
| ~~6~~ | ~~**Action**~~ | ~~`prov:Activity`~~ | **DISSOLVED (v3.0).** Verb → Event. Artifact → Object. Status → projection of event stream. | ~~What happens~~ |

> **Action dissolved (v3.0):** The Action entity no longer exists. "deploy" is an Event (eventKind=deployment, append-only, timestamped). "deployment" is an Object (artifact with status projected from events). "Meeting" the class is an Event kind; "Strategy review July 20" is an Event instance.

> **`:Source` is new in v2.5.** Replaces direct `Point → Document` provenance with a layered model: `Point → Source → Entity`. See §2.3–§2.4.

### §1.1 Kind Tags

Every entity carries exactly one `kind` tag from its type-specific vocabulary. kind and format are orthogonal dimensions.

| Entity | kind field | Vocabulary |
|--------|-----------|------------|
| Subject | `subjectKind` | organization, team, role, legalPerson, naturalPerson, other |
| Object | `objectKind` | document, product, customer, competitor, user, skill, workflow, tool, agent, indicator, database, api, code, software, infrastructure, agreement, standard, epic, project, task, other |
| Action | `actionKind` | research, scope, plan, implement, verify, reflect, decompose, delegate, loop, brainstorm, decide, agree, meet, experiment, deploy, review, other |
| Point | `pointKind` | statement, decision, vision, strategy, plan, goal, target, observation, hypothesis |
| Event | `eventKind` | meeting, decision, experiment, deployment, review, friction, extraction, documentCreated, roleCreated, pointAdded |
| Document | `documentKind` | research, reflectPostmortem, strategyDoc, visionDoc, planDoc, decisionDoc, meetingNotes, experimentResults, evidenceLog, handoff, transcript, roadmap, brief |
| Source | `sourceKind` | slack_message, github_issue, document, meeting_transcript, linear_card |

> **All kind vocabularies are extensible via domain ontologies** (see §10). Customers may register additional `kind` values for any entity type without modifying this document. The values above are the core vocabulary.

> **Which Points participate in belief propagation?** Points with `pointKind: statement|decision|observation|hypothesis` carry `confidence` and participate in IMPL/NAND edges (Expectation Propagation). Points with structural `pointKind` values (from domain ontologies) do not — they are annotated via `about*` edges from Objects/Subjects instead. All Points share the same `:Point` node type.

> **Document is an Object** (`objectKind: document`) with its own `documentKind` sub-vocabulary. It inherits all Object fields.

> **Audit fix from v2.0:** `actionKind` `meeting` → `meet`. `eventKind` `negotiation` removed. `pointKind` added `observation`, `hypothesis`; `evidence` is NOT a separate kind — it is a `statement` with provenance. `documentKind` now explicit.

---

## §2. Edge Topology

Complete predicate catalog across all entity types. Canonical reference — supersedes v2.0 §7, v2.1 §3.

**Legend:** ✅ = Implemented | ⚠️ = Partial | ❌ = Spec only | 🆕 = New in v2.5

### §2.1 Point ↔ Point (Epistemic)

| Predicate | Direction | Cardinality | ISO/PROV | Impl | Meaning |
|-----------|-----------|-------------|----------|------|---------|
| `IMPL` | operator → supported | binary | — | ✅ | Logical support. "A supports B." |
| `NAND` | operator ↔ contradicted | binary (symmetric) | — | ✅ | Logical contradiction. "A contradicts B." |
| `hasPart` | parent → children | N-ary (1→many) | `dcterms:hasPart` | ⚠️ | Structural decomposition. "A is built from B, C, D." |

> **v2.1 rename:** `composedOf`, `decomposesInto`, `contains`, `wraps` → `hasPart`. Inverse: `partOf`.
> **Impl note:** `hasPart` mapping done in `projection.py:_create_edges()` for operator edges. Object/Action/Subject-level `hasPart` not yet implemented.

### §2.2 Point/Document/Event → Entity (Cross — Semantic ↔ Epistemic)

Per-type edges (chosen over single polymorphic edge — FalkorDB matrix-per-type architecture gives 3+ orders of magnitude advantage).

| Predicate | From → To | Cardinality | ISO/PROV | Impl | Meaning |
|-----------|-----------|-------------|----------|------|---------|
| `aboutSubject` | Point/Doc/Event → Subject | N-ary (many→many) | `schema:about` (typed) | ⚠️ | What Subject this describes |
| `aboutObject` | Point/Doc/Event → Object | N-ary (many→many) | `schema:about` (typed) | ⚠️ | What Object this describes |
| `aboutAction` | Point/Doc/Event → Action | N-ary (many→many) | `schema:about` (typed) | ❌ | What Action this describes |
| `aboutEvent` | Point/Doc → Event | N-ary (many→many) | `schema:about` (typed) | ❌ | What Event this describes. Event is a target only — Events don't describe other Events |
| `aboutPoint` | Event → Point | N-ary (many→many) | `schema:about` (typed) | ❌ | What Point this Event describes. Event-only edge — Points come from Events, not the reverse |
| `aboutDocument` | Event → Document | N-ary (many→many) | `schema:about` (typed) | ❌ | What Document this Event describes |

> **v2.1 migration:** `aboutEntities` property → per-type `about*` edges. `_create_about_edges()` handles Subject/Object auto-detect from legacy property. Action/Event/Point/Document edges not yet wired. `mentioned` (Event→Entity) replaced by these per-type edges for consistency.
> **ISO note:** `schema:about` is polymorphic; we split to per-type for graph performance.

### §2.3 Point → Source (Provenance)

| Predicate | Direction | Cardinality | ISO/PROV | Impl | Meaning |
|-----------|-----------|-------------|----------|------|---------|
| `extractedFrom` | Point → Source | N-ary (many→one) | `prov:wasDerivedFrom` | ✅ | This Point was extracted from this Source |

### §2.4 Source → Entity (Provenance)

| Predicate | Direction | Cardinality | ISO/PROV | Impl | Meaning |
|-----------|-----------|-------------|----------|------|---------|
| `references` | Source → Entity | N-ary (1→many) | `pav:importedFrom` / `dcterms:references` | ❌ | Source references this content entity |

> **Range:** Document, Event, Object, **or Action** (for `sourceKind: github_issue` / `sourceKind: linear_card` — these are Actions in GitHub Issues).
> **Cardinality:** 1:N. One Source may reference multiple entities (e.g., a meeting transcript referencing several Subjects and Objects). For sub-part relationships within a single entity, use `hasPart` on the parent entity.
> **Note:** "Entity" in the predicate table above is shorthand for the content-entity union: (Document | Event | Object | Action). Subject, Point, and Source are not valid targets — Subjects are referenced via `participatesIn` on Events, Points via `extractedFrom` (inverse direction), and Source chains via `hasPart`.

### §2.5 Subject → Action → Object (Procedural)

| Predicate | Direction | Cardinality | ISO/PROV | Impl | Meaning |
|-----------|-----------|-------------|----------|------|---------|
| `performs` | Subject → Action | N-ary (many→many) | `prov:wasAssociatedWith` (inverse) | ❌ | Who executed the action |
| `produces` | Action → Object | N-ary (1→many) | `prov:wasGeneratedBy` | ❌ | What the action created |
| `authoredBy` | Doc/Point/Object → Subject | N-ary (many→one) | `dc:creator` / `prov:wasAttributedTo` | ✅ | Who authored this entity |
| `ownedBy` | Entity → Subject | N-ary (many→one) | RACI `Accountable` | ❌ | Ultimate accountability. Defines data boundary — when a team spins out, ownedBy identifies all entities belonging to that team. Bidirectional between Subjects: a Role can own a Team |
| `managedBy` | Entity → Subject | N-ary (many→one) | RACI `Responsible` | ❌ | Operational responsibility. Gates who can edit entities or change lifecycle status. Bidirectional between Subjects: a Team manages a Role, a Role manages a Team |

> **`authoredBy`** is the merged predicate — replaces v2.0's separate `assertedBy` (Point), `authoredBy` (Document), `registeredBy` (Object). All three are the same relationship: a Subject is responsible for creating this entity.
> **`ownedBy`/`managedBy`** currently live as frontmatter properties in Documents. As graph edges, they enable governance: team spin-off (find all entities to transfer), access control (gate lifecycle changes by manager), and audit chains. Both are bidirectional between Subjects — a Role can own a Team, a Team can manage a Role. The `Entity → Subject` direction is consistent: the owned entity points to its owner.

> **Design considerations (from adversarial EP analysis):** Three risks identified for edge-based ownedBy/managedBy:
> 1. **Circular ownership** — if Role A owns Team B and Team B manages Role A, traversal loops. Mitigation: ownership graphs must be DAGs; circular edges rejected at write time.
> 2. **Migration cost** — existing property-based authoredBy/ownedBy must be migrated to edges. Mitigation: backfill as a one-time migration script, not gradual.
> 3. **Edge cases** — transitive ownership (does Team A transitively own what its Role owns?), deletion cascades (what happens when a managedBy edge is removed?), and default owner (who owns entities created before edges exist?). These are implementation concerns, not architectural blockers.

### §2.6 Subject → Subject (Organisational)

| Predicate | Direction | Cardinality | ISO/Standard | Impl | Meaning |
|-----------|-----------|-------------|-------------|------|---------|
| `partOf` | Team → Organization | N-ary (many→one) | `dcterms:isPartOf` | ❌ | Organizational hierarchy. Inverse of `hasPart` |
| `hasMember` | Team → Person | N-ary (1→many) | `org:hasMember` | ❌ | Membership |
| `holdsRole` | Person → Role | N-ary (many→many) | `org:holds` / `org:hasPost` | ❌ | Functional position |
| `reportsTo` | Role → Role | N-ary (many→one) | `org:reportsTo` | ❌ | Reporting chain |

### §2.7 Action ↔ Action

| Predicate | Direction | Cardinality | ISO/PROV | Impl | Meaning |
|-----------|-----------|-------------|----------|------|---------|
| `hasPart` | parent → children | N-ary (1→many) | `dcterms:hasPart` | ❌ | Decomposition. Was `decomposesInto`/`contains`/`wraps` |
| `dependsOn` | dependent → prerequisite | N-ary (many→many) | `prov:wasInformedBy` (ordering) | ❌ | Operational dependency |

### §2.8 Object ↔ Object

| Predicate | Direction | Cardinality | ISO/PROV | Impl | Meaning |
|-----------|-----------|-------------|----------|------|---------|
| `hasPart` | parent → children | N-ary (1→many) | `dc:hasPart` / `skos:broader` pattern | ❌ | Composition. E.g. `(Project)-[:hasPart {role: "source"}]->(Repository)` |
| `related` | ↔ symmetric | N-ary (many→many) | `skos:related` / `dcterms:relation` | ❌ | Generic association. When no more specific predicate fits. |
| `dependsOn` | dependent → prerequisite | N-ary (many→many) | `prov:wasInformedBy` | ❌ | Operational dependency. Drop `type` property from v2.0. |

### §2.9 Event Edges (Episodic)

| Predicate | Direction | Cardinality | ISO/PROV | Impl | Meaning |
|-----------|-----------|-------------|----------|------|---------|
| `hasPart` | parent → children | N-ary (1→many) | `dcterms:hasPart` | ❌ | Event hierarchy. E.g. meeting `hasPart` decisions made during it. Was `subEvents`/`childEvents` in v2.0. Inverse: `partOf` |
| `participatesIn` | Subject → Event | N-ary (many→many) | `schema:attendee` / `prov:wasAssociatedWith` | ❌ | Subject was present at this event |

> **`produces` on Event is intentionally absent.** The canonical path for "what did this Event produce?" is `Event -[:instantiates]-> Action -[:produces]-> Object` or `Event -[:instantiates]-> Action`, then inspect `Action.outputs`. Direct `Event → Point` would be redundant with the Action chain.

### §2.10 Event ↔ Action (Instantiation)

| Predicate | Direction | Cardinality | ISO/PROV | Impl | Meaning |
|-----------|-----------|-------------|----------|------|---------|
| `instantiates` | Event → Action | N-ary (many→one) | `prov:wasGeneratedBy` pattern | ❌ | This Event is a specific occurrence of this Action type |

> **Rationale:** §1 defines Event as "the specific instance of an Action." `instantiates` closes the Event↔Action traversal gap. The path for "what did this Event produce?" is `Event → instantiates → Action → produces → Object` (or inspect `Action.outputs`).
> **Inverse:** `hasInstance` (Action → Event). Not stored — use Cypher reverse traversal: `(a:Action)<-[:instantiates]-(e:Event)`.

---

## §3. Entity Metadata

Full field definitions per entity type. Canonical reference — supersedes GRAPH_ARCHITECTURE.md and METADATA_MATRIX.md.

**Legend:** `ISO` column maps to standard ontologies. `Impl` = implementation status in Tortoise/FalkorDB projection.

| Symbol | Meaning |
|--------|---------|
| ✅ | Implemented |
| ⚠️ | Partial |
| ❌ | Spec only |

### §3.1 Point

| # | Field | Type | Required | Values | ISO/PROV/DC | Impl | Description |
|---|-------|------|----------|--------|-------------|------|-------------|
| 1 | `id` | ULID | ✅ | — | `dc:identifier` | ✅ | Unique identifier |
| 2 | `content` | string | ✅ | — | `schema:text` | ✅ | The claim text |
| 3 | `pointKind` | string | ✅ | see §1.1 | — | ✅ | Classification tag |
| 4 | `context` | string | — | free text | — | ✅ | Annotation field (free text, not controlled) |
| 5 | `confidence` | float 0..1 | — | — | — | ⚠️ | Belief strength. Computed by `propagate_shock()` |
| 6 | `grounding` | float | — | — | — | ⚠️ | Connection to settled evidence. Computed by `compute_grounding()` |
| 7 | `knowledgeDomain` | string | — | product, growth, engineering, operations, capability, data, ux, legal, finance-accounting | — | ❌ | Knowledge domain |
| 8 | `pointStatus` | string | ✅ | draft, live, superseded, deprecated, broken, archived, resolved | `pav:status` | ✅ | Lifecycle. `draft`/`deprecated` inert for computation. `resolved` = evidence settled |
| 9 | `createdAt` | ISO8601 | ✅ | — | `dc:created` / `pav:createdOn` | ✅ | When created |
| 10 | `updatedAt` | ISO8601 | ✅ | — | `dc:modified` | ✅ | When last modified |
| 11 | `quote` | string | — | — | — | ❌ | Verbatim source text. JSONL-only, not projected to graph |
| 12 | `span` | (int, int) | — | — | — | ❌ | Character positions. JSONL-only, not projected to graph |
| 13 | `runId` | ULID | — | — | — | ❌ | Extraction batch identifier. JSONL-only, not projected to graph |

### §3.2 Subject

| # | Field | Type | Required | Values | ISO/PROV/DC | Impl | Description |
|---|-------|------|----------|--------|-------------|------|-------------|
| 1 | `id` | string | ✅ | — | `dc:identifier` | ❌ | Canonical identifier |
| 2 | `name` | string | ✅ | — | `foaf:name` / `schema:name` | ❌ | Human-readable name |
| 3 | `subjectKind` | string | ✅ | see §1.1 | `dcterms:type` | ❌ | Classification tag |
| 4 | `status` | string | — | draft, live, superseded, deprecated, broken, archived | `pav:status` | ❌ | Lifecycle. Shared vocabulary — typically `live`, `superseded` (merged), `deprecated` (phasing out), `archived` (former) |
| 5 | `createdAt` | ISO8601 | ✅ | — | `dc:created` | ❌ | When registered |
| 6 | `updatedAt` | ISO8601 | — | — | `dc:modified` | ❌ | When last modified |

> **Why no `subjectStatus`?** PROV-O, W3C ORG, and FOAF model agent existence temporally (`validFrom`/`validTo`) with termination events (`org:ChangeEvent`), not status enumerations. A simple shared `status` covers the operational need ("is this team active?") while Events capture the *how* and *why* of changes. Merged team → `status: superseded` + Event. Role vacancy → missing `holdsRole` edge, not a Role status.

### §3.3 Object

| # | Field | Type | Required | Values | ISO/PROV/DC | Impl | Description |
|---|-------|------|----------|--------|-------------|------|-------------|
| 1 | `id` | string | ✅ | — | `dc:identifier` | ❌ | Canonical identifier |
| 2 | `name` | string | ✅ | — | `schema:name` | ❌ | Human-readable name |
| 3 | `objectKind` | string | ✅ | see §1.1 | — | ❌ | Classification tag |
| 4 | `createdAt` | ISO8601 | ✅ | — | `dc:created` | ❌ | When created |
| 5 | `updatedAt` | ISO8601 | — | — | `dc:modified` | ❌ | When last modified |
| 6 | `authoredBy` | SubjectID | — | — | `dc:creator` | ❌ | Who registered/created this Object |
| 7 | `ownedBy` | SubjectID | — | — | RACI `Accountable` | ❌ | Ultimate accountability. Who answers for this Object |
| 8 | `managedBy` | SubjectID | — | — | RACI `Responsible` | ❌ | Operational responsibility. Who manages this Object |
| 9 | `knowledgeDomain` | string | — | product, growth, engineering, operations, capability, data, ux, legal, finance-accounting | — | ❌ | Knowledge domain |
| 10 | `status` | string | — | draft, live, superseded, deprecated, broken, archived | `pav:status` | ❌ | Lifecycle. Shared across all persistent entities |
| 11 | `format` | string | — | markdown, jsonl, yaml, cypher, null, other | `dc:format` | ❌ | Storage format |

### §3.4 Document

> Document extends Object (`objectKind: document`). Inherits all Object fields; only additions listed.

| # | Field | Type | Required | Values | ISO/PROV/DC | Impl | Description |
|---|-------|------|----------|--------|-------------|------|-------------|
| 1 | `title` | string | ✅ | — | `dc:title` | ❌ | Document title |
| 2 | `documentKind` | string | ✅ | see §1.1 | `bibo:Document` subclasses | ❌ | Document type |
| 3 | `knowledgeDomain` | string | — | product, growth, engineering, operations, capability, data, ux, legal, finance-accounting | — | ❌ | Knowledge domain. Inherited from Object |
| 4 | `status` | string | ✅ | draft, live, superseded, deprecated, broken, archived | `pav:status` | ❌ | Lifecycle. Shared across all persistent entities |
| 5 | `version` | string | — | — | `pav:version` | ❌ | Version number |
| 6 | `governingAgreement` | string | — | — | — | ❌ | Governing commitment (was `governedBy`) |

### §3.5 Action

| # | Field | Type | Required | Values | ISO/PROV/DC | Impl | Description |
|---|-------|------|----------|--------|-------------|------|-------------|
| 1 | `id` | string | ✅ | — | `dc:identifier` | ❌ | Canonical identifier |
| 2 | `name` | string | ✅ | — | `schema:name` | ❌ | Action name |
| 3 | `actionKind` | string | ✅ | see §1.1 | — | ❌ | Classification tag |
| 4 | `startedAt` | ISO8601 | — | — | `prov:startedAtTime` | ❌ | When action began |
| 5 | `endedAt` | ISO8601 | — | — | `prov:endedAtTime` | ❌ | When action ended |
| 6 | `actionStatus` | string | — | pending, inProgress, paused, blocked, completed, failed, dropped | — | ❌ | Workflow state |
| 7 | `inputs` | [EntityID] | — | — | `prov:used` | ❌ | Entities consumed by this action |
| 8 | `outputs` | [EntityID] | — | — | `prov:wasGeneratedBy` (inverse) | ❌ | Entities produced by this action |
| 9 | `location` | string | — | — | `schema:location` | ❌ | Where action occurred |
| 10 | `knowledgeDomain` | string | — | product, growth, engineering, operations, capability, data, ux, legal, finance-accounting | — | ❌ | Knowledge domain |
| 11 | `governingAgreement` | AgreementID | — | — | — | ❌ | Governing commitment |
| 12 | `createdAt` | ISO8601 | ✅ | — | `dc:created` | ❌ | When this Action record was created |
| 13 | `updatedAt` | ISO8601 | — | — | `dc:modified` | ❌ | When last modified. Actions are mutable records |

### §3.6 Event

| # | Field | Type | Required | Values | ISO/PROV/DC | Impl | Description |
|---|-------|------|----------|--------|-------------|------|-------------|
| 1 | `id` / `eventId` | ULID | ✅ | — | `dc:identifier` | ✅ | Canonical identifier |
| 2 | `name` | string | — | — | `schema:name` | ❌ | Human-readable name (e.g. "Strategy review Q3") |
| 3 | `eventKind` | string | ✅ | see §1.1 | — | ✅ | Classification tag |
| 4 | `object` | EntityID | — | — | `prov:used` | ✅ | What was acted on |
| 5 | `startedAt` | ISO8601 | — | — | `prov:startedAtTime` / `schema:startDate` | ✅ | When event began |
| 6 | `endedAt` | ISO8601 | — | — | `prov:endedAtTime` / `schema:endDate` | ✅ | When event ended |
| 7 | `partOf` | EventID | — | — | `dcterms:isPartOf` | ❌ | Containing event. Inverse of `hasPart`. Was `parentEvent` in v2.0 |
| 8 | `participants` | [SubjectID] | — | — | `schema:attendee` | ✅ | All Subjects involved |
| 9 | `eventStatus` | string | — | scheduled, confirmed, inProgress, completed, cancelled | `schema:eventStatus` | ❌ | Event lifecycle. schema.org aligned |
| 10 | `classificationLevel` | string | — | public, internal, confidential, restricted | `iso27001:classification` | ✅ | Data sensitivity |
| 11 | `format` | string | — | jsonl, cypher | `dc:format` | ✅ | Storage format |
| 12 | `location` | string | — | — | `schema:location` | ❌ | Where event occurred |
| 13 | `createdAt` | ISO8601 | ✅ | — | `dc:created` | ❌ | When this Event record was ingested. Distinct from `startedAt` (when the event occurred in the real world) |

> **Dual purpose:** Events serve as both (1) temporal gatherings with duration/participants, and (2) recorded statements — the PROV-O quad `(Agent, Activity, Entity, Time)` mapped to `(participants, instantiates→Action, object, startedAt)`.
> **`subject` on Event is dropped.** The canonical path for "who performed this Event?" is `Event → instantiates → Action ← performs ← Subject`. `participatesIn` captures all Subjects involved in the Event. Having `subject` as both a property and an edge was denormalized — PROV-O `wasAssociatedWith` is a relationship, not an attribute.

### §3.7 Source

> **New in v2.5.** Provenance anchor enabling single-index queries and idempotent ingestion.

| # | Field | Type | Required | Values | ISO/PROV/DC | Impl | Description |
|---|-------|------|----------|--------|-------------|------|-------------|
| 1 | `url` | string | ✅ | — | `dc:source` / `pav:retrievedFrom` | ⚠️ | Permalink back to original |
| 2 | `sourceKind` | string | ✅ | see §1.1 | — | ❌ | Source classification |
| 3 | `contentHash` | string | ✅ | SHA-256 | `premis:messageDigest` | ❌ | Idempotency anchor — skip re-extraction if unchanged |
| 4 | `title` | string | — | — | `dc:title` | ⚠️ | Human-readable label. Defaults to `url` in current impl |
| 5 | `ingestedAt` | ISO8601 | ✅ | — | `pav:importedOn` | ✅ | When Tortoise first saw this source |
| 6 | `updatedAt` | ISO8601 | — | — | `dc:modified` | ❌ | When source content last changed |
| 7 | `version` | int | — | — | `pav:version` | ❌ | Incremented on content change |
| 8 | `externalId` | string | — | — | `dc:identifier` (external) | ❌ | System-of-record ID (Slack ts, GitHub issue #) |
| 9 | `credibilityTier` | string | — | T0, T1, T2, T3, T4 | — | ❌ | EP credibility tier. Explicit T4 applies skeptical Beta(2,4). NULL means no inheritance (neutral Beta(1,1)). T0=gold (meta-analysis), T1=high (peer-reviewed), T2=medium (expert), T3=low (anecdotal), T4=unverified |

### §3.8 Cross-Entity Field Map

| Field | Point | Subject | Object | Document | Action | Event | Source |
|-------|-------|---------|--------|----------|--------|-------|--------|
| `id` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ (url) |
| kind tag | pointKind | subjectKind | objectKind | documentKind | actionKind | eventKind | sourceKind |
| `name`/`title` | — | ✅ name | ✅ name | ✅ title | ✅ name | ✅ name | ✅ title |
| `createdAt` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ ingestedAt |
| `updatedAt` | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| status field | pointStatus | status | status | status | actionStatus | eventStatus | — |
| `format` | — | — | ✅ | ✅ | — | ✅ | — |
| `aboutEntities` | ✅ (edge) | — | — | ✅ (edge) | — | ✅ (edge) | — |
| Responsibility | authoredBy | — | ✅ authoredBy | authoredBy | authoredBy | — | — |
| Ownership | ownedBy | ownedBy | ✅ ownedBy | ownedBy | ownedBy | — | — |
| Management | managedBy | managedBy | ✅ managedBy | managedBy | managedBy | — | — |
| Part/Whole | hasPart | hasPart | hasPart | hasPart | hasPart | hasPart, partOf | — |
| Temporal | validFrom/To | — | — | — | startedAt/endedAt | startedAt/endedAt | — |
| `credibilityTier` | — | — | — | — | — | — | ✅ |

---

## §4. Edge Status Summary

### Implemented ✅
- `IMPL`, `NAND` (Point↔Point logical)
- `hasPart` (Point↔Point operator edges, mapped from old op_types)
- `extractedFrom` (Point → Source)
- `authoredBy` (on Document; as property)
- `aboutSubject`, `aboutObject` (partial: auto-detect from legacy `aboutEntities` property)
- Source node creation + `extractedFrom` edge

### Partial ⚠️
- `aboutSubject`/`aboutObject`: only created from legacy `aboutEntities` property; no direct creation API
- `ownedBy`/`managedBy`: properties on Documents only, not graph edges
- Source node: `sourceKind` hardcoded to `'document'`; `contentHash` empty; `version`/`externalId` not populated; `references` edge not created
- Source `title`: defaults to `url`, structured title not set

### Spec Only ❌
- `instantiates` (Event → Action)
- `aboutAction`, `aboutEvent`, `aboutPoint`, `aboutDocument`
- `references` (Source → Entity)
- `performs`, `produces` (Subject ↔ Action ↔ Object chain)
- `ownedBy`, `managedBy` as graph edges
- `hasPart`, `related`, `dependsOn` on Objects
- `hasPart`, `dependsOn` on Actions
- `partOf`, `hasMember`, `holdsRole`, `reportsTo` on Subjects
- `participatesIn` on Events
- Full Source node properties (`contentHash`, `version`, `externalId`, `sourceKind` vocabulary)

---

## §5. Relationship to Existing Standards

Every predicate and entity is mapped to at least one established standard.

### §5.1 Standards Referenced

| Standard | Used For | Coverage |
|----------|----------|----------|
| **PROV-O** (W3C) | Entity, Activity, Agent; wasGeneratedBy, wasAttributedTo, wasDerivedFrom, used | §1 type system, §2 edges, Event/Action/Source |
| **Dublin Core** (DCMI) — `dc:` / `dcterms:` | creator, created, modified, title, identifier, format, source, relation, hasPart, references, type | §3 metadata fields, `authoredBy`, `related`, `hasPart`, `references` |
| **W3C ORG** | Organization, hasMember, holdsRole, reportsTo | §2.6 Subject hierarchy. `subOrganizationOf` unified under `partOf` |
| **schema.org** | name, description, about, subjectOf, location, startDate, endDate, attendee | §3 Event/Action fields, `about*` edges |
| **SKOS** (W3C) | related, broader, narrower | `related` (Object↔Object), `hasPart` hierarchy |
| **FOAF** | Person, Organization, member, name | §3.2 Subject, §2.6 membership |
| **PAV** (Provenance, Authoring & Versioning) | createdOn, createdBy, version, status, importedOn, importedFrom, retrievedFrom | §3.7 Source node, `status`, `version` |
| **BIBO** (Bibliographic Ontology) | Document subclasses | `documentKind` vocabulary |
| **ArchiMate** | composition, aggregation, serving, realization | `dependsOn`, `hasPart` patterns |
| **RACI** | Responsible (operational), Accountable (ultimate), Consulted, Informed | `managedBy` = Responsible, `ownedBy` = Accountable, `reportsTo` |
| **ISO 8601** | Date/time format | All temporal fields |
| **ISO 27001** | Information classification | `classificationLevel` on Event |
| **PREMIS** (Preservation Metadata) | messageDigest | `contentHash` on Source |

### §5.2 PROV-O Alignment Summary

| PROV-O Concept | El Dato Entity/Edge |
|---------------|---------------------|
| `prov:Entity` | Object, Point, Document, Source |
| `prov:Activity` | Action, Event |
| `prov:Agent` | Subject |
| `prov:wasGeneratedBy` | `produces` (Action→Object) |
| `prov:wasAttributedTo` | `authoredBy` |
| `prov:wasDerivedFrom` | `extractedFrom` (Point→Source) |
| `prov:used` | `inputs` (Action), `object` (Event) |
| `prov:wasAssociatedWith` | `performs` (Subject→Action), `participatesIn` (Subject→Event) |
| `prov:wasInformedBy` | `dependsOn` (Action↔Action — ordering dependency) |
| `prov:startedAtTime` / `prov:endedAtTime` | `startedAt` / `endedAt` |

---

## §6. Organisational Model

> **Teams are Subjects** (`subjectKind: team`). **Domains are a tagging system.** Specific team instances are registered in `operations/subjects/*.yaml`, not listed here — the ontology defines the *concept*, not the instances.

### §6.1 Team → Subject Mapping

A Team is a Subject with `subjectKind: team`. It is registered as a YAML file in `operations/subjects/<team-slug>.yaml` following the schema at `operations/subjects/_schema.md`. The ontology defines the entity type; the registry defines the instances.

### §6.2 Domains (Horizontal Tagging)

Domains are tags, not entities. Any Document, Point, Action, or Object can carry a `knowledgeDomain` tag. The canonical domain slugs:

| # | Domain | Slug | Focus |
|---|---|---|---|
| 1 | Product & Services | `product` | Product specs, strategy, competitive analysis |
| 2 | Data | `data` | Ontology, canonical schemas, data models |
| 3 | Engineering | `engineering` | Dev, auth, DB, deployment, platform, security |
| 4 | UX | `ux` | Design specs, journey maps, component catalog |
| 5 | Growth | `growth` | Marketing, SEO, content, CRM, analytics |
| 6 | Operations | `operations` | CI/CD, migrations, agent ops, skills pipeline |
| 7 | Legal & Compliance | `legal` | Legal research, compliance, data protection |
| 8 | Finance & Accounting | `finance-accounting` | Billing, spend analysis, forecasting |
| 9 | Org Development | `capability` | Workflows, skills, L&D, agent roles, team culture |

---

### §6.3 Subject Hierarchy & Role Scoping

**Teams can have sub-teams** via the universal `hasPart`/`partOf` predicates:
```
(platform-team:Subject {subjectKind: team})
  -[:hasPart]-> (app-team:Subject {subjectKind: team})
  -[:hasPart]-> (dmer-team:Subject {subjectKind: team})
```
No separate `subOrganizationOf` — `partOf` handles organizational hierarchy. Same pattern as W3C ORG's `org:subOrganizationOf` but unified under the canonical term.

**Roles are scoped to an Organization or Team** via `partOf`:
```
(eldato:Subject {subjectKind: organization})
  -[:hasPart]-> (design-team:Subject {subjectKind: team})
  -[:hasPart]-> (architect:Subject {subjectKind: role})
```
The `holdsRole` edge (Person → Role) captures who fills the position. The `partOf` chain captures where the position sits in the org chart. A Role can be scoped directly to an Organization (cross-cutting roles like "General Counsel") or to a Team (team-specific roles like "iOS Developer").

**Roles can become Teams.** A Role (`subjectKind: role`) that grows in scope can evolve into a Team (`subjectKind: team`). This is an organizational change Event, not a status transition:
```
# Before: role scoped to org
(org)-[:hasPart]->(security:Subject {subjectKind: role, status: live})

# Event: security function grows into a team
(growthEvent:Event {eventKind: decision})
  -[:instantiates]-> (orgChange:Action {actionKind: decide})
  -[:aboutSubject]-> (security)

# After: role superseded, new team created
(security) -> status: superseded
(org)-[:hasPart]->(security-team:Subject {subjectKind: team, status: live})
```

> **W3C ORG alignment:** `org:Post` (Role) exists within an `org:Organization` (or sub-Organization). Our `hasPart`/`partOf` chain replaces `org:hasSubOrganization` + `org:hasPost`. `holdsRole` corresponds to `org:holds` linking Person to Post.

---

## §7. Work Vocabulary

> **Source:** v2.0 §8. Actions are pipeline stages.

| Stage | actionKind | Produces |
|-------|-------------|----------|
| Align | decide | Point (`pointKind: decision`) |
| Research | research | Document (`documentKind: research`) |
| Scope | scope | Document (`documentKind: brief`) |
| Plan | plan | Document (`documentKind: planDoc`) + Point (`pointKind: plan`) |
| Decompose | decompose | Objects (`objectKind: epic`/`project`/`task`) |
| Implement | implement | Objects (`objectKind: task`, `actionStatus: completed`) + code |
| Verify | verify | Document (`documentKind: evidenceLog`) |
| Reflect | reflect | Document (`documentKind: reflectPostmortem`) + Event (`eventKind: friction`) |

### §7.1 Action Statuses (`actionStatus`)

`pending`, `inProgress`, `paused`, `blocked`, `completed`, `failed`, `dropped`

> **`paused` vs `blocked`:** `paused` = positive state, will resume. `blocked` = needs external intervention.

### §7.2 Entity Statuses

**Shared lifecycle (`status`):** `draft`, `live`, `superseded`, `deprecated`, `broken`, `archived` — applies to Document, Object, Subject.

**Point (`pointStatus`):** adds `resolved` (evidence settled).

**Action (`actionStatus`):** `pending`, `inProgress`, `paused`, `blocked`, `completed`, `failed`, `dropped` — workflow states.

**Event (`eventStatus`):** `scheduled`, `confirmed`, `inProgress`, `completed`, `cancelled` — schema.org EventStatus aligned.

---

## §8. Memory Levels (M0–M2)

> **Source:** v2.0 §9. Unchanged.

| Level | Scope | Storage | Gate |
|-------|-------|---------|------|
| M0 | Task learnings | `MEMORY.md`, plan doc `## Learnings` | Agent-autonomous |
| M1 | Domain knowledge | `wiki/`, ReflectDoc | Domain-gated |
| M2 | Cross-domain | FalkorDB, ADRs, strategy docs | Human-gated (PR) |

---

## §9. Systems of Record

Every entity has a *system of record* — the authoritative source where it is created, stored, and managed. The `Source` node type (§1) + `extractedFrom`/`references` edges (§2.3–§2.4) encode this as a provenance chain:

```
Entity ← extractedFrom ← Source → references → (Document | Event | Object | Action)
```

This means the system of record is **not a static table** — it's derived from the graph. Query "where did this Entity come from?" by traversing `extractedFrom` and inspecting `Source.sourceKind` + `Source.url`.

### §9.1 Default Systems (Entities Created Directly)

All entity types follow the same pattern: **JSONL is the source of truth; FalkorDB is the current-state projection.** The event log (`*.jsonl`) records every creation/mutation as an immutable event (`PointAdded`, `SubjectAdded`, `EventRecorded`, etc.). FalkorDB is rebuilt by replaying the log — same as `projection.py:fold(read_all())`.

| Entity Type | Source of Truth | Projection | Notes |
|-------------|----------------|------------|-------|
| Point | JSONL (`PointAdded`) | FalkorDB | Epistemic claims + operators |
| Subject | JSONL (`SubjectAdded`) | FalkorDB | May also be backed up in `operations/subjects/*.yaml` |
| Object | JSONL (`ObjectRegistered`) | FalkorDB | — |
| Document | JSONL (`DocumentCreated`) | FalkorDB | Also lives in `docs/` filesystem |
| Action | JSONL (via connector events) | FalkorDB | — |
| Event | JSONL (`EventRecorded`) | FalkorDB | Immutable temporal records |
| Source | Inline (created during Point ingestion) | FalkorDB | Derived from `extractedFrom` references |

### §9.2 Tenant-Configurable Systems

Entities ingested from connectors derive their system of record from the `Source` node's `sourceKind`. Each tenant configures which connectors provide which entity types — this is deployment configuration, not ontology:

```
# Per-tenant config (not in ontology). sourceKind values from §1.1 vocabulary.
systems_of_record:
  customer:       { sourceKind: document,      connector: crm-connector }
  user:           { sourceKind: document,      connector: database-connector }
  epic|task:      { sourceKind: github_issue,  connector: github }
  document:       { sourceKind: document,      path: docs/ }
```
> Tenants may register additional `sourceKind` values in their domain ontology (e.g., `hubspot`, `salesforce`, `supabase`). The core vocabulary (§1.1) defines the built-in types; tenants extend it.

The `Source.sourceKind` vocabulary (§1.1) is the bridge: when a connector ingests data, it creates `(:Source {sourceKind: "github_issue", url: "..."})` → `references` the Entity. The tenant config maps `sourceKind` values to their specific connector instances.

> **Why not list our tools here?** "HubSpot CRM for customers" is El Dato's deployment choice, not ontology. Another tenant might use Salesforce. The ontology defines the *mechanism* (Source nodes + provenance edges); tenant config defines the *mapping*. Same pattern as GitHub/Linear/Slack connectors in Tortoise.

---

## §10. Domain Ontologies

> **Principle:** Core ontology defines universal entity types. Domain ontologies extend with additional `kind` values, predicates, and entity subtypes. Customers may create their own domain ontologies or use the template ones provided.

### §10.1 Customer Domain Ontologies

Any customer can define a domain ontology by creating a markdown document that follows the domain ontology contract:

1. **Declare its base** — reference this document as the core ontology
2. **Register kind values** — list additional `pointKind`, `objectKind`, or other kind values (additive, not replacement)
3. **Map to core entities** — each new term specifies which core entity type it belongs to
4. **Define relationships** — new predicate types specific to the domain
5. **Stay compatible** — must not redefine core terms or conflict with other domain ontologies

Example: a healthcare customer might add `pointKind: diagnosis, treatment_plan, clinical_trial` and `predicate: contraindicates`.

### §10.2 Template Domain Ontologies

Pre-built domain ontologies provided as starting points. Customers can use them as-is, extend them, or replace them entirely.

| Domain | Status | Adds |
|--------|--------|------|
| Product Strategy | ✅ provided | pointKind: useCase, jobToBeDone, userJourney, workflow, requirement, policy, concept, issue |
| Marketing | template | pointKind: campaign, audience, channel |
| Finance | template | pointKind: budget, forecast, expense |
| People / HR | template | pointKind: competency, performance_review, hiring_req |

> **Template vs custom:** Template ontologies are starting points — customers are not locked into them. A customer can use the Product Strategy template, create their own Marketing ontology, and skip Finance entirely. The core ontology works without any domain extensions.

---

## §11. Migration from v2.0

| Change | Reason |
|--------|--------|
| `composedOf`/`decomposesInto`/`contains`/`wraps` → `hasPart` | Single canonical term for part/whole |
| `aboutEntities` property → `aboutSubject`/`aboutObject`/`aboutAction`/`aboutEvent` edges | FalkorDB matrix-per-type performance |
| `INPUT` edges removed | Redundant — Cypher handles reverse traversal |
| `:Source` node type added | Layered provenance — replaces direct Point→Document |
| `extractedFrom` redirects through Source | PROV-O qualified derivation pattern |
| `authoredBy` merged (`assertedBy` + `registeredBy` → `authoredBy`) | Single predicate for authorship |
| `related` added (Object↔Object) | `skos:related` — generic association |
| `participatesIn` (Event edge) added | Event entity completeness. `produces` on Event removed — use `Event → instantiates → Action → produces → Object` instead. `mentioned` replaced by `about*` edges on Event |
| `hasPart`/`partOf` expanded to Objects/Actions/Subjects/Events | Part/whole is universal, not Point-only |
| `subOrganizationOf` → `partOf` on Subject | Unified under canonical part/whole term |
| `ownedBy`/`managedBy` expanded to graph edges | Traversable responsibility chain |
| `dependsOn` type property dropped | Simplified — context determines dependency nature |
| `actionKind` `meeting` → `meet` | Verb form consistency |
| `eventKind` `negotiation` removed | Merged into `meeting` |
| `pointKind` added `observation`, `hypothesis` | Epistemic completeness. `evidence` is a `statement` with provenance |
| `governedBy` → `governingAgreement` | Clarify what governs (an Agreement). Added to Action as well as Document |
| `domain` → `knowledgeDomain` | Renamed. Added to Document, Point, Action |
| `docStatus` → `status` on Document | Unified lifecycle vocabulary. `doc` prefix dropped |
| Point `status` → `pointStatus` | Adds `resolved` (evidence-settled), `broken` (falsified), `archived` |
| `eventStatus` added on Event | Lifecycle: scheduled, confirmed, inProgress, completed, cancelled. schema.org EventStatus aligned |
| Action `status` → `actionStatus` | Renamed to distinguish from lifecycle `status`. Values unchanged |
| `status` added to Subject | Shared lifecycle vocabulary. Replaces proposed `subjectStatus`. PROV-O+ORG aligned via Events for termination |
| `subEvents`/`childEvents` → `hasPart` on Event | Unified under canonical part/whole term |
| New `context` field on Point | Free-text annotation for extractors |
| New Event fields: `participants`, `classificationLevel`, `format`, `location` | Event entity completeness |
| `instantiates` (Event → Action) added | Close the Action↔Event instance-of gap from §1 |
| v2.0 commitment edges (`HELD_BY`, `SATISFIES`, etc.) → future domain ontology | Deferred to Agreement/FalkorDB commitment system |
| v2.0 strategic chain (`creates`, `delegates`, `fulfills`) → future domain ontology | Deferred to Product Strategy domain ontology |

---

## §12. Implementation Epic

Tracks what remains to be built. See GitHub for current issues.

### Phase 1: Edge Wiring (projection.py)
- [ ] `aboutAction`, `aboutEvent`, `aboutPoint`, `aboutDocument` edge creation in `_create_about_edges()`
- [ ] `participatesIn` edge creation in `_upsert_event()`
- [ ] `related` edge creation API
- [ ] `hasPart` edge creation for Objects, Actions, Subjects (beyond Point↔Point)
- [ ] `ownedBy`, `managedBy` as graph edges (not just properties)

### Phase 2: Source Node Completion
- [ ] `sourceKind` vocabulary (not hardcoded `'document'`)
- [ ] `contentHash` computed and populated
- [ ] `references` edge created (Source → Document/Event/Object/Action)
- [ ] `version`, `externalId` populated
- [ ] Connectors (GitHub, Linear, Slack) emit proper Source nodes

### Phase 3: Entity Node Creation (per §3 field tables)
- [ ] Subject nodes: `id`, `name`, `subjectKind`, `status`, `createdAt`, `updatedAt` (§3.2)
- [ ] Object nodes: `id`, `name`, `objectKind`, `createdAt`, `updatedAt`, `authoredBy`, `ownedBy`, `status`, `knowledgeDomain`, `format` (§3.3)
- [ ] Action nodes: full metadata per §3.5 — `id`, `name`, `actionKind`, `startedAt`, `endedAt`, `actionStatus`, `inputs`, `outputs`, `knowledgeDomain`, `governingAgreement`, `createdAt`, `updatedAt`
- [ ] Document nodes: all Object fields + `title`, `documentKind`, `knowledgeDomain`, `status`, `version`, `governingAgreement` (§3.4)
- [ ] Event: `name`, `hasPart`/`partOf` hierarchy, `eventStatus`, `createdAt` (§3.6)

### Phase 4: Event & Action Edges
- [ ] `instantiates` (Event → Action) — §2.10
- [ ] Event `hasPart`/`partOf` — §2.9
- [ ] `partOf`, `hasMember`, `holdsRole`, `reportsTo` — §2.6

### Phase 5: Commitment Graph (future domain ontology)
- [ ] `HELD_BY`, `SATISFIES`, `DECOMPOSES_INTO`, `GOVERNS` — deferred from v2.0 §7.7
- [ ] `creates`, `delegates`, `fulfills` — deferred from v2.0 §7.8

---

> **This document supersedes:** ONTOLOGY.md (v2.0), ONTOLOGY_UPDATE_v2.1.md, GRAPH_ARCHITECTURE.md, METADATA_MATRIX.md. Once ratified, those files should be archived with `docStatus: superseded`.


---

## §5. Four-Ontology Model (v3.0)

Each layer answers a different question. The Procedural layer (Action) is dissolved.

| Layer | Question | Entity | Notes |
|-------|----------|--------|-------|
| **Semantic** | Who/what exists? | Subject, Object, Document, Source | Nouns. Standing structural relations (owns, memberOf) via plain edges. |
| **Epistemic** | What do we believe and why? | Point, Operator (IMPL/NAND + label + EP confidence) | Operators ONLY here: Event→Point (outcome), Point→Point (belief). |
| **Episodic** | What happened when? | Event | Verbs. Reified middle node: (Subject)-[performs]->(Event)-[produces]->(Object). Append-only, timestamped. |
| ~~Procedural~~ | ~~Current state of work~~ | ~~Action~~ | **Dissolved.** Verb → Event. Artifact → Object. Status → projection of event stream. |

### Structural vs Epistemic Edges

| Edge | Type | Confidence | Example |
|------|------|-----------|---------|
| performs / produces / uses / owns / memberOf | **Structural** (plain) | None (factual) | (User)-[performs]->(Event), (User)-[owns]->(Doc) |
| Event→Point, Point→Point | **Epistemic** (operator) | EP confidence | (Event:deployFailed)-[NAND]->(Point:"deploy succeeded") |

**Principle:** Operators connect only epistemic targets (Event→Point, Point→Point). Subjects connect via plain structural edges. Evaluations of subjects (expertise, reliability) are Statements (Points) with EP confidence — not edges. Reputation is derived at query time. Facts = confidence 1.0.

### Event Model

```
(Subject)-[:performs]->(Event)-[:produces]->(Object)
                     (Event)-[:uses]->(Object)          ← input (v3.0)
                     (Event)-[op: IMPL/NAND]->(Point)   ← outcome influence
```

- `performs`: subject → event (actor)
- `produces`: event → object (output artifact)
- `uses`: event → object (input consumed)
- Object→Object `wasDerivedFrom` (entity derivation, distinct from Source provenance)
- Event→Point operators: the event's outcome influences belief confidence

### Epistemic Recency Modulation

Evidence aging is **user-configurable with a light default** — NOT blunt time decay. Stable facts stay strong regardless of age. Interacts with sourceKind/credibility tier. Never auto-deprecates old evidence.

### Reputation (derived, not stored)

`compute_reputation(subject_id)` is a query-time primitive: traverse subject→performs→events→outcome operators (Event→Point IMPL/NAND), aggregate success/failure, optionally weighted by recency. Not stored (would go stale). Bridges procedural history to epistemic belief.

---
> Full v3.0 details: docs/ONTOLOGY_v3.0_proposal.md (pack manifest format, subclass/equivalence model, migration).
