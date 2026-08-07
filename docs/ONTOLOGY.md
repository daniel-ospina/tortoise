---
title: "Tortoise — Canonical Ontology v3.2"
type: data
domain: data
status: live
created: 2026-08-05
updated: 2026-08-07
ownedBy: epistemic-team
doc_status: live
---

# Tortoise — Canonical Ontology v3.2

> **Status:** LIVE — canonical. Co-located with the code it governs (tortoise repo).
> **Supersedes:** ONTOLOGY_v2.5.md (eldato repo, deprecated).
>
> **Changelog v3.2 (2026-08-07, issue #398 — Source credibility):**
> - §4.6/§3.4: Source `sourceKind` clarified — it is the extensible source TYPE
>   vocabulary (connectors write `github_issue`, `slack_message`, `linear_card`);
>   the T0–T4 credibility TIER is carried by `credibilityTier` (the property the
>   inheritance adapter reads). Tier-form values written into `sourceKind`
>   (`create_source(url, "T0")`) mirror to `credibilityTier` (dual-write).
> - §4.6: Source gains `reliability`/`reliabilityComponents`/`reliability_derived_at`
>   (documented derivation cache — see §11) and `sourceDate` (evidence-age clock).
> - §5: pointKind vocabulary gains `assessment` (agent source evaluations).
> - §10: recency-modulation decision log (per-field/per-sourceType decay deferred).
> **Convention:** camelCase throughout. `kind` = classification tag on an entity. `predicate` = named edge between entities.

---

## §1. Entity Types

Five core types.

| # | Type | ISO/PROV Mapping | Definition | Label |
|---|------|-------------------|------------|-------|
| 1 | **Subject** | `prov:Agent` / `org:Organization` / `foaf:Person` | Any entity that can act | Who acts |
| 2 | **Object** | `prov:Entity` / `schema:Thing` | Persistent things that exist, are produced, or are acted upon | What persists |
| 3 | **Point** | `prov:Entity` (specialized) | A node in the belief graph — claims, decisions, structural artifacts | What we believe |
| 4 | **Event** | `prov:Activity` (instantiated) / `schema:Event` | Temporal occurrence — the verb. Reified middle node: (Subject)-[performs]->(Event)-[produces]->(Object) | What happened |
| 5 | **Source** | `prov:Entity` (provenance) / `pav:Source` | Provenance anchor — where content was extracted from | Where it came from |

**Core subclass model (§6):** Document ⊂ Object (`objectKind: document`). Object has core subclasses (Project, WorkItem, document, tag, user, skill, tool, agent, workflow, agreement, standard). Subject has core subclasses (organization, team, role, legalPerson, naturalPerson, other). Expansion packs declare further subclasses via `subclassOf` (§9).

---

## §2. Four-Ontology Model

Each layer answers a different question. All four are live mechanisms.

| Layer | Question | Entity | How it works |
|-------|----------|--------|--------------|
| **Semantic** | Who/what exists? | Subject, Object (incl. Document), Source | Nouns. Standing structural relations (owns, memberOf, hasPart) via plain edges. |
| **Epistemic** | What do we believe and why? | Point, Operator (IMPL/NAND + label + EP confidence) | Operators connect epistemic targets (Event→Point, Point→Point). Belief strength = EP confidence, computed by propagation. |
| **Episodic** | What happened when? | Event | Verbs. Append-only, timestamped. Reified middle node: (Subject)-[performs]->(Event)-[produces]->(Object). |
| **Procedural** | What is the current state of work? | Event + projected status on Object | **Status is derived, not stored.** An Object's status (in_progress, completed, failed) is projected at query time from its event stream — the events are the truth, the status is a read-only projection. |

**Structural vs Epistemic Edges**

| Edge | Type | Confidence | Example |
|------|------|-----------|---------|
| performs / produces / uses / owns / memberOf / authoredBy / ownedBy / managedBy | **Structural** (plain) | None (factual) | (User)-[performs]->(Event), (User)-[owns]->(Doc) |
| Event→Point, Point→Point | **Epistemic** (operator) | EP confidence | (Event:deployFailed)-[NAND]->(Point:"deploy succeeded") |

**Principle:** Operators connect only epistemic targets (Event→Point, Point→Point). Subjects connect via plain structural edges. Evaluations of subjects (expertise, reliability) are Statements (Points) with EP confidence — not edges. Reputation is derived at query time. Facts = confidence 1.0.

---

## §3. Edge Topology

### §3.1 Point ↔ Point (Epistemic — Operators)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `IMPL` | Point → Point | default bidirectional; optional unidirectional | N-ary | Epistemic (EP confidence) | A supports/implies B. Direction is an explicit operator flag — **default bidirectional**, option to declare unidirectional (source→target only). Not inferred from label. |
| `NAND` | Point → Point | default bidirectional; optional unidirectional | N-ary | Epistemic (EP confidence) | A contradicts B. Same direction model as IMPL — default mutual contradiction (bidirectional), optional unidirectional. |
| `hasPart` | Point → Point | bidirectional (composition) | N-ary | Structural via operator label | A contains B (parts/whole cascade). |
| `CORRECTS` | Point → Point | unidirectional | 1→1 | — | New point **corrects/replaces** an outdated point (supersession). Marks target `outdated: true`; all edges transfer from old to new. Created by `supersede_point` / `invalidate_point` (sdk.py:448-485). |

> **Supersession semantics:** `CORRECTS` is the structural replacement edge. `supersede_point(old, new)` = mark old `outdated:true` + create `(new)-[:CORRECTS]->(old)` + transfer all old edges (IMPL/NAND/hasPart operators + structural edges) to new. `invalidate_point(id, corrected_by)` = mark outdated + CORRECTS only (no edge transfer). Old point retains only the CORRECTS edge as provenance.

> **Direction flag (code note):** operator direction is carried as an explicit flag on the operator Point. Current implementation (ep.py) derives bidirectionality from label (hasPart/partOf → bidirectional; else directional for IMPL; NAND always bidirectional) — this is being migrated to an explicit `direction` flag with default bidirectional. See follow-up issue.

### §3.2 Point ↔ Entity (Cross — Semantic ↔ Epistemic)

Per-type edges (chosen over single polymorphic edge — FalkorDB matrix-per-type architecture).

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `aboutSubject` | Point/Document/Event → Subject | unidirectional | many→many | `schema:about` (typed) | What Subject this describes |
| `aboutObject` | Point/Document/Event → Object | unidirectional | many→many | `schema:about` (typed) | What Object this describes |
| `aboutEvent` | Point/Document → Event | unidirectional | many→many | `schema:about` (typed) | What Event this describes. Event is a target only — Events don't describe other Events |
| `aboutPoint` | Event → Point | unidirectional | many→many | `schema:about` (typed) | What Point this Event describes. Event-only edge |
| `aboutDocument` | Event → Document | unidirectional | many→many | `schema:about` (typed) | What Document this Event describes |
| `TAGGED` | Point → Tag | unidirectional | many→many | `schema:keywords` | Free-form label on a Point. Tags are `:Tag` nodes (Object subclass, `objectKind: tag`) shared across Points via MERGE. Created by hosted-api point ingestion (hosted_api.py:695-787). ⚠️ **Write-only today — no tag-filter query surfaced yet** (see follow-up). |

> **Legacy:** `aboutEntities` property → per-type `about*` edges. `_create_about_edges()` auto-detects Subject/Object from the legacy property. `schema:about` is polymorphic; we split per-type for graph performance.

### §3.3 Point → Source (Provenance)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `extractedFrom` | Point → Source | unidirectional | many→1 | `pav:retrievedFrom` (inverse) | This claim was extracted from this source. One source backs many Points. |

### §3.4 Source → Entity (Provenance)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `references` | Source → Entity | unidirectional | 1→many | — | The source document links to / references this entity. Wired in the ingest path — `_upsert_document` links `(Source {url:doc_id})-[:references]->(Document {id:doc_id})` (#205). |

`(Point)-[:extractedFrom]->(Source)-[:references]->(Entity)` — layered provenance. Source carries `sourceKind` (extensible source TYPE vocabulary, e.g. `github_issue`, `slack_message`, `document`) and `credibilityTier` (T0-T4 credibility tier — see §4.6, #398).

### §3.5 Subject → Event → Object (Procedural)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `performs` | Subject → Event | unidirectional | N-ary | **`schema:agent` inverse** — schema.org's "direct performer or driver of the action", reversed (we go Agent→Activity) | X **did** this. The doing relation: subject executes the event. PROV has no Agent→Activity predicate (its `wasAssociatedWith` is Activity→Agent accountability); we name the performer-side verb ourselves, aligned to schema.org's performer concept. |
| `produces` | Event → Object | unidirectional | 1→many | `schema:result` (same direction) / `prov:wasGeneratedBy` inverse | Output artifact the event created |
| `uses` | Event → Object | unidirectional | N-ary | **`prov:used`** (W3C: Activity→Entity, direction-identical — canonical) / `schema:instrument` for mechanisms | Input the event consumed — **including the mechanism** (skill/tool/agent/workflow Object) that produced the output |
| `wasDerivedFrom` | Object → Object | unidirectional | N-ary | `prov:wasDerivedFrom` | Entity derivation (distinct from Source provenance) |

> **One edge, two names:** `uses` (graph predicate) = `prov:used` (PROV property). Same thing — present tense in our vocabulary, past tense in PROV's. `produces` = `schema:result` (Activity→Entity, matching direction); `prov:wasGeneratedBy` names the reverse (Entity→Activity).
>
> **Mechanism provenance ("how was it produced"):** the producing mechanism is a first-class Object linked via `uses` — `(Event)-[:uses]->(Object {objectKind: skill|tool|agent|workflow})`. The mechanism is therefore searchable and shared (finite skill set, not per-event). Mechanism *specifics* (version, model, config, pipeline hash) live in the immutable event-log record, reachable via the Event's `eventId` — they are NOT materialized as per-event graph nodes (avoids O(events) node growth at scale). Full lineage: `(Point)-[:extractedFrom]->(Source)-[:references]->(Object:document)<-[:produces]-(Event {eventId})` → log record. (The `references` hop is wired in the ingest path for Documents — see §3.4; entity-reference detection in connectors remains a follow-up.)

### §3.6 Subject ↔ Subject (Organisational)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `participatesIn` | Subject → Event | unidirectional | N-ary | `schema:attendee` | Subjects involved in an event (⚠️ spec-only — no producer yet, tracked in issue #7884) |
| `memberOf` | Subject → Subject | unidirectional | N-ary | `org:membership` | Membership in team/group/organization. **Canonical** — generalizable to teams, orgs, any hierarchy. |
| `managedBy` | Entity → Subject | unidirectional | N-ary | RACI Responsible | Operational responsibility |
| `ownedBy` | Entity → Subject | unidirectional | N-ary | RACI Accountable | Accountability, data boundary |

> **memberOf is canonical.** `get_org_structure` queries `memberOf` for membership (Subject→Subject, member→org). `holdsRole` is retained as a distinct concept (person→role is not membership). The legacy `hasMember` predicate (org→member) remains in valid_predicates for backward compatibility with existing graph edges.

### §3.7 Object ↔ Object

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `wasDerivedFrom` | Object → Object | unidirectional | N-ary | `prov:wasDerivedFrom` | Derivation |
| `hasPart` | Object → Object | bidirectional | N-ary | `dcterms:hasPart` | Composition. Inverse traversal (`<-[:hasPart]-`) covers "part of" — no separate `partOf` edge. |

### §3.8 Event Edges

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `performs` (in) | Subject → Event | unidirectional | N-ary | `schema:agent` inverse | Actor — who did it |
| `produces` | Event → Object | unidirectional | 1→many | `schema:result` | Output artifact |
| `uses` | Event → Object | unidirectional | N-ary | `prov:used` | Input consumed |
| `nextEvent` | Event → Event | unidirectional | 1→1 | — | Sequencing (Graphiti NextEpisode equivalent) — planned |
| `op: IMPL/NAND` | Event → Point | default bidirectional; optional unidirectional | N-ary | Epistemic | Outcome influence on belief (epistemic) |

> **#531 — canonical Event→Point pattern (`humanApproval`):** a human approval of a planning artifact is recorded as an Event (`eventKind: humanApproval`) + a decision Point (`pointKind: humanApproval`). The Event carries occurrence provenance (approver `performs`, artifact `uses`, claim `aboutPoint`, decision `produces`); the decision Point is a live epistemic claim that seeds the grounding a-vector and receives an EP evidence prior `Beta(10,1)` so dependent claims strengthen. Fan-out is `-[:IMPL {direction: "unidirectional", label: "approvedBy"}]->` per approved claim — deliberately unidirectional so claim weakness never back-propagates into the approval. No stored `approved` status on Objects — approval is derived from the event stream at query time. Worked example (`file_human_approval`, #531):
>
> ```
> (:Subject "Daniel")-[:performs]->(:Event {eventKind:"humanApproval", startedAt:T})
>   (:Event)-[:uses]->(:Document "Customer Profile CP-001")
>   (:Event)-[:aboutPoint]->(:Point "CP-001 targets SMB segment")
>   (:Event)-[:produces]->(:Point {pointKind:"humanApproval", content:"Approved: CP-001"})
> (:Point "Approved: CP-001")-[:IMPL {direction:unidirectional, label:"approvedBy"}]-> approved claim Points
> ```

### §3.9 Valid Predicate Vocabulary (code)

All structural edges must use one of (enforced in `_create_edge`):

```
performs, produces, uses, authoredBy, ownedBy, managedBy,
hasMember, holdsRole, memberOf, reportsTo,
participatesIn, hasPart, related, dependsOn, references,
wasDerivedFrom
wasDerivedFrom

> **#214 (2026-08-06):** `instantiates` removed — Event→Action legacy from v2.5;
> Action was dissolved in Ontology v3.0.
>
> **Vocabulary-only edges** (valid predicates with zero producers):
> `reportsTo` (org hierarchy, Subject→Subject), `related` (generic catch-all),
> `dependsOn` (pack-declared — dev:api dependsOn dev:database; used by `list_relations()`
> for kind expansion). All three remain valid for `create_edge()`.
```

Epistemic edges (operators): `IMPL`, `NAND` (+ semantic label). About edges: `aboutSubject`, `aboutObject`, `aboutEvent`, `aboutPoint`, `aboutDocument`.

---

## §4. Entity Metadata

> Columns: ISO/PROV/DC = standard alignment; Impl = implemented in current code (✅ = yes, ⚠️ = partial, ❌ = spec-only). Only the live field set is listed — legacy JSONL-only fields are dropped from the ontology.

### §4.1 Point

| Field | Type | Required | ISO/PROV/DC | Impl | Meaning |
|-------|------|----------|-------------|------|---------|
| `id` | ULID | ✅ | `dc:identifier` | ✅ | Unique identifier |
| `content` | string | ✅ | `schema:text` | ✅ | The claim text |
| `pointKind` | string | ✅ | — | ✅ | Classification tag: statement, decision, vision, strategy, plan, goal, target, observation, hypothesis + pack pointKinds |
| `is_operator` | bool | — | — | ✅ | true for operator Points |
| `op_type` | string | — | — | ✅ | IMPL / NAND (operator Points only) |
| `status` | string | — | `pav:status` | ✅ | Lifecycle: draft, live, superseded, deprecated, archived, resolved. draft/deprecated inert for computation |
| `confidence` | float 0..1 | — | — | ⚠️ | EP posterior mean, computed by propagation |
| `authoredBy` | SubjectID | — | `dc:creator` | ✅ | Who created the claim |
| `validFrom` / `validTo` | ISO8601 | — | — | ✅ | Temporal validity window |
| `createdAt` / `updatedAt` | ISO8601 | ✅ | `dc:created` / `dc:modified` | ✅ | Timestamps |
| `embedding` | vector | — | — | ✅ | Semantic embedding (FTS + vector search) |

### §4.2 Subject

| Field | Type | Required | ISO/PROV/DC | Impl | Meaning |
|-------|------|----------|-------------|------|---------|
| `id` | string | ✅ | `dc:identifier` | ✅ | Canonical identifier |
| `name` | string | ✅ | `foaf:name` / `schema:name` | ⚠️ | Human-readable name |
| `subjectKind` | string | ✅ | `dcterms:type` | ✅ | organization, team, role, legalPerson, naturalPerson, other |
| `status` | string | — | `pav:status` | ❌ | Lifecycle: draft, live, superseded, deprecated, archived — **planned, not yet implemented on Subject** (only `createdAt`/`subjectKind`/`embedding` are written by `_upsert_subject`) |
| `createdAt` | ISO8601 | ✅ | `dc:created` | ✅ | Timestamp (set ON CREATE) |
| `updatedAt` | ISO8601 | — | `dc:modified` | ❌ | **Not written by `_upsert_subject`** — planned follow-up |

> **Why no `subjectStatus`?** PROV-O, W3C ORG, and FOAF model agent existence temporally (`validFrom`/`validTo`) with termination events, not status enumerations. A simple shared `status` covers "is this team active?" while Events capture the how/why of changes.

### §4.3 Object

| Field | Type | Required | ISO/PROV/DC | Impl | Meaning |
|-------|------|----------|-------------|------|---------|
| `id` | string | ✅ | `dc:identifier` | ✅ | Canonical identifier |
| `name` | string | ✅ | `schema:name` | ⚠️ | Human-readable name (`_upsert_object` writes `title`; `name` aliased) |
| `objectKind` | string | ✅ | — | ✅ | Project, WorkItem, document, user, skill, tool, agent, workflow, agreement, standard, other + pack objectKinds |
| `title` | string | — | `dc:title` | ✅ | Display title (what `_upsert_object` actually stores) |
| `status` | string | — | `pav:status` | ❌ | Projected from event stream (in_progress, completed, failed) — **derived at query time, NOT stored** |
| `createdAt` | ISO8601 | ✅ | `dc:created` | ✅ | Timestamp (set ON CREATE) |
| `updatedAt` | ISO8601 | — | `dc:modified` | ❌ | **Not written by `_upsert_object`** — planned follow-up |

> **Responsibility fields (authoredBy / ownedBy / managedBy) are EDGES, not node properties** — see §3.5-3.6. `_upsert_object` does not store them as properties; they exist as graph edges to Subject nodes.

### §4.4 Document (subclass of Object)

> **Document is an Object** (`objectKind: document`) — a core subclass (§6). Inherits all Object fields; additions below. Graph label is `:Document` (matching `_upsert_document`'s `MERGE (d:Document {id:$id})`); the subclass relationship to Object is expressed via `objectKind: document`, not via a second graph label. Do not create a separate `:Object` label for Documents. `documentKind` is the subclass-of-Document vocabulary (BIBO-aligned).

| Field | Type | Required | ISO/PROV/DC | Impl | Meaning |
|-------|------|----------|-------------|------|---------|
| `objectKind` | string | ✅ | — | ✅ | `document` (conceptual subclass of Object — the `:Document` graph label implies it; not stored as a separate property) |
| `documentKind` | string | ✅ | `bibo:Document` subclasses | ✅ | research, reflectPostmortem, strategyDoc, visionDoc, planDoc, decisionDoc, meetingNotes, experimentResults, evidenceLog, handoff, transcript, roadmap, brief + pack documentKinds |
| `title` | string | — | `dc:title` | ✅ | Human-readable title |
| `format` | string | — | `dc:format` | ✅ | Storage format (markdown, jsonl, yaml, cypher) |
| `content` | string | — | `schema:text` | ✅ | Raw document text |
| `topics` | list[str] | — | — | ✅ | Searchable topic labels (session/discussion index). FTS-indexed metadata — NOT Points, never EP-participating |
| `summary` | string | — | — | ✅ | Story-arch summary (bullet "what was done" for captured sessions). Searchable via FTS |
| `doc_status` | string | — | `pav:status` | ✅ | Capture lifecycle: captured (metadata-only) / extracted (full analysis) / draft |
| `sessionId` | string | — | — | ✅ | Source session identifier (for `documentKind: transcript` captures) |
| `eventId` | string | — | — | ✅ | Link to the producing Event's log record — 1-hop audit to the mechanism snapshot |
| `sourcePath` | string | — | — | ✅ | Filesystem path to the original conversation file — agents can open/search it after finding the session |
| `_searchText` | string | — | — | ✅ | FTS index text = title + summary + topics (coalesce-null sentinels) |

### §4.5 Event

| Field | Type | Required | ISO/PROV/DC | Impl | Meaning |
|-------|------|----------|-------------|------|---------|
| `eventId` | ULID | ✅ | `dc:identifier` | ✅ | Unique occurrence ID |
| `eventKind` | string | ✅ | — | ✅ | meeting, decision, experiment, deployment, review, friction, extraction, documentCreated, roleCreated, pointAdded, sessionCaptured + pack eventKinds |
| `format` | string | — | `dc:format` | ✅ | Storage format (jsonl default, markdown) |
| `startedAt` / `endedAt` | ISO8601 | — | `prov:startedAtTime` / `schema:startDate` | ✅ | Temporal extent |
| `subject` | SubjectID | — | `prov:wasAssociatedWith` (inverse) | ✅ | Who performed the event (mirrors `performs` edge) |
| `object_name` / `object_type` | string | — | `prov:used` | ✅ | What was acted on / produced |
| `file_hash` | string | — | — | ✅ | SHA-256 of the ingested file's raw bytes — ingest idempotency anchor (`ingest_corpus` skips byte-identical re-ingests; written by the DocumentCreated/AgentSession ingest paths, #330) |

### §4.6 Source

| Field | Type | Required | ISO/PROV/DC | Impl | Meaning |
|-------|------|----------|-------------|------|---------|
| `url` | string | ✅ | `dc:source` / `pav:retrievedFrom` | ✅ | Permalink back to original |
| `sourceKind` | string | ✅ | — | ✅ | Extensible source TYPE vocabulary (github_issue, slack_message, linear_card, document...). Tier-form values (T0-T4) mirror to `credibilityTier` (dual-write, #398) |
| `credibilityTier` | string | — | — | ✅ | T0-T4 credibility tier — the property the inheritance adapter reads (v3.2) |
| `contentHash` | string | ✅ | `premis:messageDigest` | ✅ | Idempotency anchor — skip re-extraction if unchanged |
| `title` | string | — | `dc:title` | ⚠️ | Human-readable label. Defaults to url |
| `ingestedAt` | ISO8601 | ✅ | `pav:importedOn` | ✅ | When Tortoise first saw this source |
| `updatedAt` | ISO8601 | — | `dc:modified` | ✅ | Last modified (set ON MATCH by `_upsert_source`) |
| `externalId` | string | — | `dc:identifier` (external) | ⚠️ | System-of-record ID (Slack ts, GitHub issue #) |
| `sourceDate` | ISO8601 | — | `dc:date` | ⚠️ | Evidence-age clock for recency decay (falls back to `ingestedAt` — the pipeline-arrival proxy, #398) |
| `reliability` | float 0..1 | — | — | ⚠️ | DERIVED query-time projection (mean of the modulated Beta prior) — documented cache, never authoritative (v3.2, #398) |
| `reliabilityComponents` | JSON | — | — | ⚠️ | Cache metadata: tier, decay, factor, assessment_count, derivation time (#398) |
| `reliability_derived_at` | ISO8601 | — | — | ⚠️ | Cache freshness stamp (#398) |

### §4.7 Cross-Entity Field Map

| Field | Point | Subject | Object | Document | Event | Source |
|-------|-------|---------|--------|----------|-------|--------|
| `id` | ✅ | ✅ | ✅ | ✅ | ✅ eventId | ✅ url |
| kind tag | pointKind | subjectKind | objectKind | documentKind | eventKind | sourceKind |
| name/title | — | name | name | title | — | title |
| createdAt | ✅ | ✅ | ✅ | ✅ | ✅ startedAt | ✅ ingestedAt |
| updatedAt | ✅ | ❌ | ❌ | ✅ | — | ✅ |
| status | status | ❌ (planned) | ❌ (projected, not stored) | doc_status | — | — |
| responsibility | authoredBy | — | edge (§3.5) | — | — | — |
| ownership | — | — | edge (§3.5) | — | — | — |
| management | — | — | edge (§3.5) | — | — | — |
| format | — | — | — | format | format | — |
| aboutEdges | ✅ | — | ✅ | ✅ | ✅ | — |
| temporal | validFrom/To | — | — | — | startedAt/endedAt | — |

---

## §5. Core Kind Vocabulary

### Point Kind Vocabulary (core)

```
statement, decision, vision, strategy, plan, goal, target, observation, hypothesis,
humanApproval   # #531: decision Point for a filed human approval
```

### Object Kind Vocabulary (core)

```
Project, WorkItem, document, tag, user, skill, tool, agent, workflow, agreement, standard, other
```

### Event Kind Vocabulary (core)

```
meeting, decision, experiment, deployment, review, friction, extraction,
documentCreated, roleCreated, pointAdded, sessionCaptured, humanApproval  # #531
```

### Document Kind Vocabulary (core)

```
research, reflectPostmortem, strategyDoc, visionDoc, planDoc, decisionDoc,
meetingNotes, experimentResults, evidenceLog, handoff, transcript, roadmap, brief
```

### Subject Kind Vocabulary (core)

```
organization, team, role, legalPerson, naturalPerson, other
```

### Source Type Vocabulary (core) + Credibility Tier

```
T0 (meta-analysis), T1 (peer-reviewed), T2 (expert), T3 (anecdotal), T4 (unverified)
```

> **v3.2 (#398):** `sourceKind` is the extensible source TYPE vocabulary — pack-declared
> kinds (github_issue, slack_message, linear_card, document...) resolve to a tier ONLY
> via explicit registration (`register_source_kind_default`) or an explicit
> `credibilityTier` assignment; unknown kinds stay neutral (no inheritance). The
> T0–T4 tier semantics above live on `credibilityTier`. The Beta-prior mapping
> (T0=(10,1), T1=(5,1), T2=(3,1), T3=(2,1), T4=(1.1,1)) is the validated model
> (docs/ep-source-credibility-experiment.md §1.1).

> **Expansion-pack kinds live in the packs, not here.** Pack-declared kinds (dev:epic, product-strategy:product, etc.) are defined in their pack manifests (§9) and registered at load time via the pack registry. This file documents only the core vocabulary; it is not the home for pack kinds.

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

**Core subclasses (no pack needed):**

| Parent | Core subclasses |
|--------|-----------------|
| Object | Project, WorkItem, document, tag, user, skill, tool, agent, workflow, agreement, standard |
| Document (⊂ Object) | research, reflectPostmortem, strategyDoc, visionDoc, planDoc, decisionDoc, meetingNotes, experimentResults, evidenceLog, handoff, transcript, roadmap, brief |
| Subject | organization, team, role, legalPerson, naturalPerson |

> Document is the only Object subclass with its own metadata table (§4.4) — it inherits Object fields and adds documentKind vocabulary + capture fields. Other Object subclasses inherit Object fields verbatim.

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
| Operator | Point (is_operator:true) | Mitigation target |

### Semantic Types

| Type | Mechanism | Epistemic propagation | Semantic label direction | Example |
|------|-----------|------------|-------------------------|---------|
| hasPart | IMPL | Bidirectional cascade (parts↔whole) | bidirectional | Epic hasPart Issue |
| addresses | IMPL | Unidirectional (A supports B) | unidirectional | Feature addresses Need |
| supports | IMPL | Unidirectional (A supports B) | unidirectional | Evidence supports Claim (CLI default label for IMPL, `__main__.py:81`) |
| opposes | NAND | Bidirectional by default, optional unidirectional | declared by pack | Feature competesWith Competitor |

> **Direction is an explicit operator flag, default bidirectional.** The table above shows typical pack declarations; any pack may override the default with an explicit `direction: unidirectional` on the relation.

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

Packs are loaded via the pack registry (`PackRegistry`) at startup; their kinds extend the core vocabulary and are queryable via `expand_kind` / `equivalentTo`.

---

## §10. Epistemic Recency Modulation

Evidence aging is **user-configurable with a light default** — NOT blunt time decay. Stable facts stay strong regardless of age. Interacts with sourceKind/credibility tier (T0 direct observation ages differently than T4 speculation). Never auto-deprecates old evidence.

> **Decision log (v3.2, #398 open question):** temporal decay granularity = **deferred**
> (per-field / per-sourceType decay curves NOT shipped). Retained: the validated
> `0.95^years` modulation, T0-exempt, keyed on `sourceDate` else `ingestedAt`, recomputed
> per EP run via provenance-marked inherited baselines (`baseline_source='inherited'`,
> per-point time gate). Differentiated per-tier aging (§10 "ages differently") is a
> §10-implied follow-up; the extension point is the source-type registry's per-kind
> default/tier slot.

---

## §10.5 Cascading Invalidation (Claims 6–7 of the patent)

When an evidence source's confidence changes, downstream propositions that
depend on it through operator chains are **re-evaluated, not stored-flagged**.
Invalidation is a *derived* cascade — consistent with the ontology's
"status is derived, not stored" principle (§2, §11):

```
supersede_point / invalidate_point          (§3.1: mark old outdated:true,
                                            create (new)-[:CORRECTS]->(old))
   → _mark_dirty(affected)                  (sdk.py — dirty roots queued)
   → EP re-propagation                      (ep.py _affected_claims: reverse
                                            BFS through IMPL|NAND, max_hops=2)
   → re-persist confidence to affected      (dream.py runs EP on dirty roots)
   → contested-claim detection at query     (get_contested_claims(variance),
     time                                   ep.py — variance from persisted
                                            α/β; also surfaced per-result as
                                            ep.contested in search, #580)
```

**Design decisions (recorded for the patent filing):**

| Question | Decision |
|----------|----------|
| Ontology concept vs implementation detail? | **Derived behavior**, documented here; no new stored entity |
| Dedicated edge type (DEPENDS_ON)? | **No** — reverse traversal of IMPL/NAND operators is sufficient; a stored DEPENDS_ON edge would duplicate structure and drift |
| Representation of "potentially invalidated"? | **Elevated posterior variance** (v > 0.04 → contested), not a stored `pointStatus` — statuses are `{live, draft, outdated, archived}`; `outdated` is set only by explicit supersession, never auto-inferred |
| Interaction with CORRECTS? | CORRECTS is the *structural* replacement; cascading invalidation is the *belief-level* consequence — both fire from the same write (`supersede_point` → `_mark_dirty`) |

Direction-aware EP (§3.1, #86) is the prerequisite that makes reverse
traversal well-defined: IMPL is unidirectional (source→target), NAND
symmetric, hasPart bidirectional — `_affected_claims` follows these directions.

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
- **Derived values may be CACHED, never authoritative (v3.2, #398):** Source
  `reliability` is a write-through projection of the query-time derivation
  (recomputed on write events, consistency-checked on read, stamped with
  `reliability_derived_at`) — the derivation is the truth, the cache is a
  performance artifact.

---

## §12. Relationship to Standards

| Standard | Alignment |
|----------|-----------|
| **PROV-O** | Subject→Agent, Event→Activity, Object→Entity. `uses`=prov:used (Activity→Entity, direction-identical), `produces`=prov:wasGeneratedBy inverse, `wasDerivedFrom`=prov:wasDerivedFrom, `performs`=inverse of prov:wasAssociatedWith (PROV names Activity→Agent accountability; we name the Agent→Activity doing verb). |
| **Schema.org** | Event with startTime/endTime. Action pattern: `performs`=schema:agent inverse (the "direct performer or driver of the action"), `produces`=schema:result, `uses`=schema:instrument (mechanisms) / schema:input. |
| **BIBO** | Document subclasses — `documentKind` vocabulary. |
| **OWL-Time** | Event is the temporal entity (startedAt/endedAt). |
| **RDF-star** | Operators are reified edges with metadata (label + confidence) — RDF-star-like reification for epistemic edges. |
