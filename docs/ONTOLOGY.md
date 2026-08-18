---
title: "Tortoise — Canonical Ontology v3.6"
type: data
domain: data
status: live
created: 2026-08-05
updated: 2026-08-11
ownedBy: epistemic-team
doc_status: live
---

# Tortoise — Canonical Ontology v3.6

> **Status:** LIVE — canonical. Co-located with the code it governs (tortoise repo).
> **Supersedes:** ONTOLOGY_v2.5.md (eldato repo, deprecated).
>
> **Changelog v3.8 (2026-08-13, issue #388 — connector Source nodes):**
> - §3.4: connector events (GitHub/Linear/Slack poll + webhook + entity paths)
>   now materialize Source nodes at the projection choke point (`_upsert_event`,
>   projection/entities.py) — `(Source {url})-[:references]->(Event {eventId})`
>   (+ `(Source)-[:references]->(Object {id})` on the GitHub entity path via an
>   explicit `sourceObjectId` field). Gate fires only on a registered connector
>   `sourceKind` or an explicit `sourceUrl` — never on bare `source` (mining
>   events stay excluded). `sourceKind` is set on CREATE only (#398 never-
>   overwrite contract: an existing Source's kind is authoritative on re-MERGE);
>   re-materialization does not bump `version` (idempotent re-poll).
> - §3.4/§5: `sourceKind` vocabulary gains `github_pr` (PR events — previously
>   mislabeled `github_issue`) and `linear_cycle` (cycles — previously mislabeled
>   `linear_card`); both register neutral in SOURCE_KIND_DEFAULTS (no EP
>   inheritance change).
> - §3.4: `Source.url` may be a container-scope string when no per-entity URL
>   exists (`slack:{channel}` on permalink failure, `linear:{team_key}` for
>   cycles) — deliberate non-URL fallback keying.
>
> **Changelog v3.6 (2026-08-11, epic #909 slice 3 — 13 ontology amendments, issue #948):**
> Registration only — no new design (plan §4.3, numbered exactly 13):
> 1. §4.5/§5: eventKind `AgentSession` (EXACT code spelling — capital A, sdk.py/session_indexer.py) + `sessionCaptured` declared an alias of the same concept — both remain valid kinds, no migration.
> 2. §4.5: `capturedAt` field (transaction time — bi-temporal capture).
> 3. §4.5: content-addressed Event ID (deterministic MERGE anchor for the agentSession Event).
> 4. §4.4: `story_arc` field registered (summary = short, story_arc = arc continuation).
> 5. §3.1: NAND direction policy — extraction-emitted NANDs default `unidirectional`; `bidirectional` only for explicit mutual restatement (SDK creation default stays bidirectional, #807).
> 6. §4.6/§5: `provenance_spans` Source property + `sourceKind: agentSession` value (credibility-tier inheritance keyed on sourceKind, #398).
> 7. §3.9: `mitigated_by` predicate registered (existing `mitigate_operator` edge — currently unregistered).
> 8. §3.4: `references` target extended — Source allowed (producer extension `link_source_to_entity`).
> 9. §5: `pointKind: event` registered (episodic turn Points — regex capture path, sdk.py:924).
> 10. §4.1: content-addressed Point ids (`pt_<sha>`) sanctioned as an id form (ULID preference retained).
> 11. §4.1: `c_cal` (calibrated confidence) + stored `quote` (≤200 chars provenance quote) registered.
> 12. §4.3: `passes_frequency_gate` registered on Object (S5 gate-result flag).
> 13. §4.1/§4.5/§4.6: `is_episodic` registered on Point/Event/Source — plus the `:Session` capture node (§4.5 note) (quota exemption discriminator; NOT on Object/Document — plan §4.3 #13 entity set, review P2 PR #973).
>
> **Changelog v3.5 (2026-08-11, epic #898 — reification rule):**
> - §8: Reification rule added — an edge carries an operator only when it needs
>   mitigation (or is a Point↔Point support/contradict). Structural edges stay
>   plain and carry confidence as an edge attribute. IMPL/NAND may be direct
>   Point→Point (operator-less). Direction lives on the operator node when
>   present, else on the edge. EP note: operator-less edges read direction from
>   the edge and initialize edge messages directly.
>
> **Changelog v3.4 (2026-08-10, issue #690 — status vocabulary reconciliation):**
> - §5: Point status vocabulary upgraded from narrative note to canonical table
>   (six statuses: draft, live, retracted, superseded, outdated, archived).
>   Every status has a defined write path, allowed transitions, and EP semantics.
>   Parity decision: SDK + EventAPI + CLI share a single vocabulary
>   (`POINT_STATUS_VALUES` in `sdk.py`). `challenged` remains a derived
>   condition, not a stored status.
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
> > **Changelog v3.7 (2026-08-12, core hypothesis — state-centric model):**
> - §2: state-centric model block — the graph stores STATE (Objects + lifecycle
>   + derived confidence) + POINTS (the logic) + EVENTS (timeline incl.
>   decision-as-event); decisions are NOT first-class Points.
> - §5 (v3.8): extraction point kind = `statement` ONLY; `observation` removed
>   (anything can be called one) and `hypothesis` folded into confidence
>   semantics (a conjecture is a low-confidence statement) — both join the
>   legacy write kinds.
> - §5 (v3.7): Point kinds — extraction write kinds = statement/observation/hypothesis;
>   decision/vision/strategy/plan/goal/target/humanApproval/event marked LEGACY
>   (write-compat only). Object kinds gain the commitment-state family
>   (strategy/plan/goal/target). Event kinds gain `occurrence` + `turn`; the
>   decision-as-event semantics documented.
> - Pack-mapping item: product-strategy option pointKinds (useCase/userJourney/
>   jobToBeDone/valueProposition) → objectKinds.

**Convention:** camelCase throughout. `kind` = classification tag on an entity. `predicate` = named edge between entities. Capture-path/pipeline fields keep their code spelling (snake_case) — e.g. `doc_status`, `file_hash`, `is_episodic`, `c_cal`, `story_arc`, `passes_frequency_gate`, `provenance_spans` (v3.6, #909).

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
| **Epistemic** | What do we believe and why? | Point, Operator (IMPL/NAND + label + EP confidence) | Operators connect epistemic targets (Event→Point, Point→Event, Point→Point). Belief strength = EP confidence, computed by propagation. **Point→Event operators are recorded argumentation annotations — write-only in v1, no EP propagation; decision semantics remain on the Event timeline; decisions stay non-first-class Points.** |
| **Episodic** | What happened when? | Event | Verbs. Append-only, timestamped. Reified middle node: (Subject)-[performs]->(Event)-[produces]->(Object). |
| **Procedural** | What is the current state of work? | Event + projected status on Object | **Status is derived, not stored.** An Object's status (in_progress, completed, failed) is projected at query time from its event stream — the events are the truth, the status is a read-only projection. |

> **State-centric model (core hypothesis, 2026-08-12 — the graph stores STATE, not decisions):**
> The record is three layers. **State** — Objects/options carry their lifecycle
> (promoted/deprecated/superseded — the Episodic layer's events are the truth;
> status is a read-only projection) and their **confidence** (derived from the
> attached Points). **Points** — the logic: statements (pointKind `statement` —
> the only extraction point kind; hypothesis folded into confidence) connected
> to the state they argue about (aboutObject); IMPL/NAND/MITIGATES among them
> move the object's confidence. **Events** — what happened, for
> context: occurrences AND the **decision-as-event** (eventKind `decision`,
> aboutObject → the object(s) it resolved). The graph says *"this state is
> based on these reasons"* — never *"this decision was made because of these
> reasons"*. The decision dimension stays queryable as a timeline (events),
> but decisions are NOT first-class Points. Point kinds `decision`/`vision`/
> `strategy`/`plan`/`goal`/`target`/`humanApproval`/`event` are LEGACY write
> kinds (§5) — extraction emits `statement` Points only, Event nodes, and
> lifecycle writes on Objects. State confidence is derived at
> read time from the attached Points' EP confidence (§11) — never stored
> independently on the Object.

**Structural vs Epistemic Edges**

| Edge | Type | Confidence | Example |
|------|------|-----------|---------|
| performs / produces / uses / owns / memberOf / authoredBy / ownedBy / managedBy | **Structural** (plain) | None (factual) | (User)-[performs]->(Event), (User)-[owns]->(Doc) |
| Event→Point, Point→Event, Point→Point | **Epistemic** (operator) | EP confidence | (Event:deployFailed)-[NAND]->(Point:"deploy succeeded") · (Point:"argument for X")-[IMPL]->(Event:decision-on-X) — the latter write-only in v1 (argumentation annotation; no EP propagation; the decision stays an Event, never a first-class Point) |

**Principle:** Operators connect only epistemic targets (Event→Point, Point→Event, Point→Point). Subjects connect via plain structural edges. Evaluations of subjects (expertise, reliability) are Statements (Points) with EP confidence — not edges. Reputation is derived at query time. Facts = confidence 1.0.

---

## §3. Edge Topology

### §3.1 Point ↔ Point (Epistemic — Operators)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `IMPL` | Point → Point | default bidirectional; optional unidirectional | N-ary | Epistemic (EP confidence) | A supports/implies B. Direction is an explicit operator flag — **default bidirectional**, option to declare unidirectional (source→target only). Not inferred from label. |
| `NAND` | Point → Point | default bidirectional; optional unidirectional | N-ary | Epistemic (EP confidence) | A contradicts B (logically mutual — "A and B can't both be true"). Default bidirectional; an agent may declare `unidirectional` for a directed attack (attacker's truth penalizes the target, no back-pressure — #753). **Extraction-emitted NANDs default `unidirectional`** — see extraction policy in the direction-flag note below (#909 §4.3 #5). |
| `hasPart` | Point → Point | bidirectional (composition) | N-ary | Structural via operator label | A contains B (parts/whole cascade). |
| `CORRECTS` | Point → Point | unidirectional | 1→1 | — | New point **corrects/replaces** an outdated point (supersession). Marks target `outdated: true`; all edges transfer from old to new. Created by `supersede_point` / `invalidate_point` (sdk.py:448-485). |

> **Supersession semantics:** `CORRECTS` is the structural replacement edge. `supersede_point(old, new)` = mark old `outdated:true` + create `(new)-[:CORRECTS]->(old)` + transfer all old edges (IMPL/NAND/hasPart operators + structural edges) to new. `invalidate_point(id, corrected_by)` = mark outdated + CORRECTS only (no edge transfer). Old point retains only the CORRECTS edge as provenance.

> **Direction flag (code note):** operator direction is an explicit flag on the operator Point. Creation default is **bidirectional** for all op types (#753 — NAND is logically mutual; `unidirectional` is the agent-declared directed attack). Pre-migration operators lacking the property are read as bidirectional (legacy semantics preserved).
>
> **Edge properties (IMPL/NAND — EP message state, epic 903):** these are **graph-persisted** belief-propagation messages written by `TortoiseEP._flush_cache` and read back by `_load_cache` (warm-start seed, 903-C4). They are load-bearing graph state — documented here so they are not treated as throwaway cache:
>
> | Property | Type | Written by | Meaning |
> |----------|------|-----------|---------|
> | `msg_alpha` / `msg_beta` | float | `TortoiseEP._flush_cache` (ep.py) | Forward EP message natural parameters, operator→claim slot `(op_id, claim_id, rel_type)` |
> | `back_msg_alpha` / `back_msg_beta` | float | `TortoiseEP._flush_cache` (ep.py) | Backward EP message natural parameters — separate slot for bidirectional / operator-less edges (the `back_msg_*` pair on the same edge) |
>
> **Warm-start note (903-C4):** `run(warm_start=True)` loads these graph-persisted messages as seed and skips updates whose delta ≤ fixed threshold γ; the fast path (`compute_confidence`) runs `warm_start=False` and never touches γ-skip state.
>
> **Extraction NAND direction policy (epic #909 §4.3 #5 / research addendum §1 — pipeline spec):** the EXTRACTOR explicitly sets direction per this policy; the SDK creation default stays `bidirectional` (#807 — API-user path):
> - **New-claim-attacks-existing-claim → `unidirectional`** (directed): "you now claim ¬D against D" is an attack on an existing belief — the new claim attacks the old. This is the common, measured-correct case (the one that makes contradiction surfacing work; `nand_precision` A11 measures it).
> - **Mutual restatement → `bidirectional`**: when both claims are asserted together as mutually exclusive (e.g., the conversation itself declares "A and B can't both be true").
> - **Default for extraction-emitted NANDs: `unidirectional`** — extraction is always asserting something NEW against something EXISTING; mutual is the rare explicit case.

### §3.2 Point ↔ Entity (Cross — Semantic ↔ Epistemic)

Per-type edges (chosen over single polymorphic edge — FalkorDB matrix-per-type architecture).

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `aboutSubject` | Point/Document/Event → Subject | unidirectional | many→many | `schema:about` (typed) | What Subject this describes |
| `aboutObject` | Point/Document/Event → Object | unidirectional | many→many | `schema:about` (typed) | What Object this describes |
| `aboutEvent` | Point/Document → Event | unidirectional | many→many | `schema:about` (typed) | What Event this describes. Event is a target only — Events don't describe other Events |
| `aboutPoint` | Event → Point | unidirectional | many→many | `schema:about` (typed) | What Point this Event describes. Event-only edge |
| `aboutDocument` | Event → Document | unidirectional | many→many | `schema:about` (typed) | What Document this Event describes |
| `aboutSource` | Point/Document/Event → Source | unidirectional | many→many | `schema:about` (typed) | What Source this describes (e.g., an evaluation of a source). Creatable via `create_edge` (#391); may coexist with `extractedFrom` when the claim's text was also retrieved from that source |
| `aboutAction` | Point → Point (legacy) | unidirectional | many→many | `schema:about` (typed) | Legacy predicate retained for pre-v3.0 Action edges (Action entity dissolved in v3.0). No automatic producer — creatable only via explicit `create_edge` (#391); endpoints are whatever the resolver finds |
| `TAGGED` | Point → Tag | unidirectional | many→many | `schema:keywords` | Free-form label on a Point. Tags are `:Tag` nodes (Object subclass, `objectKind: tag`) shared across Points via MERGE. Created by hosted-api point ingestion (hosted_api.py:695-787). ⚠️ **Write-only today — no tag-filter query surfaced yet** (see follow-up). |

> **Legacy:** `aboutEntities` property → per-type `about*` edges. `_create_about_edges()` auto-detects Subject/Object from the legacy property. `schema:about` is polymorphic; we split per-type for graph performance.

> **`aboutEvent` is content-only (#1417):** the edge means "this Point/Document describes this Event" — never "this Point was produced by this Event". Capture-path provenance (a Point produced from a session/meeting) lives on the Point's **`eventId` property** (the provenance surface, stamped by `capture_session`, hosted `/v1/sessions`, and the mining path), with the full chain `(Point)-[:extractedFrom]->(Source)-[:references]->(Event)` (§3.3/§3.4). Pre-#1417 `aboutEvent`-as-provenance edges remain readable and are semantically reinterpreted (no migration); new writes must not mint them.

### §3.3 Point → Source (Provenance)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `extractedFrom` | Point → Source | unidirectional | many→1 | `pav:retrievedFrom` (inverse) | This claim was extracted from this source. One source backs many Points. |

### §3.4 Source → Entity (Provenance)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `references` | Source → Document/Event/Object/Source | unidirectional | 1→many | — | The source links to / references this entity — target may be a Document, Event, Object, **or another Source** (producer extension in `link_source_to_entity`, #909 §4.3 #8 — previously validated only Document/Event/Object). Wired in the ingest path — `_upsert_document` links `(Source {url:doc_id})-[:references]->(Document {id:doc_id})` (#205); external artifacts referenced in a captured conversation become external Source nodes that the session Source `references` (referential chain, #909). |

`(Point)-[:extractedFrom]->(Source)-[:references]->(Entity)` — layered provenance. Source carries `sourceKind` (extensible source TYPE vocabulary, e.g. `github_issue`, `github_pr`, `linear_card`, `linear_cycle`, `slack_message`, `document`) and `credibilityTier` (T0-T4 credibility tier — see §4.6, #398).

Connector entities (GitHub/Linear/Slack) get Source nodes at the projection choke point: `_upsert_event` (projection/entities.py) materializes `(Source {url})-[:references]->(Event {eventId})` from connector event metadata (`sourceKind` + per-entity `sourceUrl`) — #388. The gate fires only on a registered connector `sourceKind` or an explicit `sourceUrl` (never on bare `source` — mining events stay excluded); `sourceKind` is set on CREATE only — a pre-existing Source's kind is authoritative (#398 never-overwrite contract) — and re-materialization does not bump `version` (no churn on re-poll). When no per-entity URL exists, `Source.url` falls back to a container-scope string (`slack:{channel}`, `linear:{team_key}`) — the reference still resolves; the key is just coarser than a permalink. The GitHub entity path additionally wires `(Source)-[:references]->(Object {id})` via an explicit `sourceObjectId` event field (`event.object` is never used as an Object key — it is the entity title on poll/webhook paths).

### §3.5 Subject → Event → Object (Procedural)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `performs` | Subject → Event | unidirectional | N-ary | **`schema:agent` inverse** — schema.org's "direct performer or driver of the action", reversed (we go Agent→Activity) | X **did** this. The doing relation: subject executes the event. PROV has no Agent→Activity predicate (its `wasAssociatedWith` is Activity→Agent accountability); we name the performer-side verb ourselves, aligned to schema.org's performer concept. |
| `produces` | Event → Object | unidirectional | 1→many | `schema:result` (same direction) / `prov:wasGeneratedBy` inverse | Output artifact the event created |
| `uses` | Event → Object | unidirectional | N-ary | **`prov:used`** (W3C: Activity→Entity, direction-identical — canonical) / `schema:instrument` for mechanisms | Input the event consumed — **including the mechanism** (skill/tool/agent/workflow Object) that produced the output |
| `wasDerivedFrom` | Object → Object | unidirectional | N-ary | `prov:wasDerivedFrom` | Entity derivation (distinct from Source provenance) |

> **One edge, two names:** `uses` (graph predicate) = `prov:used` (PROV property). Same thing — present tense in our vocabulary, past tense in PROV's. `produces` = `schema:result` (Activity→Entity, matching direction); `prov:wasGeneratedBy` names the reverse (Entity→Activity).
>
> **Mechanism provenance ("how was it produced"):** the producing mechanism is a first-class Object linked via `uses` — `(Event)-[:uses]->(Object {objectKind: skill|tool|agent|workflow})`. The mechanism is therefore searchable and shared (finite skill set, not per-event). Mechanism *specifics* (version, model, config, pipeline hash) live in the immutable event-log record, reachable via the Event's `eventId` — they are NOT materialized as per-event graph nodes (avoids O(events) node growth at scale). Full lineage: `(Point)-[:extractedFrom]->(Source)-[:references]->(Object:document)<-[:produces]-(Event {eventId})` → log record. (The `references` hop is wired in the ingest path for Documents and — since #388 — for connector entities at the `_upsert_event` choke point — see §3.4.)

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
| `op: IMPL/NAND` | Event → Point, Point → Event | default bidirectional; optional unidirectional | N-ary | Epistemic | Outcome influence on belief (epistemic); Point→Event direction = argumentation annotation, write-only in v1 (no EP propagation) |

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

Epistemic edges (operators): `IMPL`, `NAND` (+ semantic label).

Mitigation edge: `mitigated_by` — Point → Point (operator → mitigation Point), written by `mitigate_operator` (sdk.py:1613): `(op:Point {is_operator:true})-[:mitigated_by]->(m:Point)`, with the mitigation Point back-linking `-[:IMPL]->` the operator (#909 §4.3 #7 — registered; previously unregistered).

About edges: `aboutSubject`, `aboutObject`, `aboutEvent`, `aboutPoint`, `aboutDocument`, `aboutSource` (Point/Document/Event → Source), `aboutAction` (legacy).

---

## §4. Entity Metadata

> Columns: ISO/PROV/DC = standard alignment; Impl = implemented in current code (✅ = yes, ⚠️ = partial, ❌ = spec-only). Only the live field set is listed — legacy JSONL-only fields are dropped from the ontology.

### §4.1 Point

| Field | Type | Required | ISO/PROV/DC | Impl | Meaning |
|-------|------|----------|-------------|------|---------|
| `id` | ULID | ✅ | `dc:identifier` | ✅ | Unique identifier — ULID preferred (`create_point`); content-addressed `pt_<sha>` ids are a **sanctioned id form** (deterministic — the commit endpoint's idempotency anchor, #909 §4.3 #10) |
| `content` | string | ✅ | `schema:text` | ✅ | The claim text |
| `pointKind` | string | ✅ | — | ✅ | Classification tag — extraction writes `statement` (option B); decision/vision/strategy/plan/goal/target/observation/hypothesis/humanApproval/event are legacy write kinds (§5) + pack pointKinds |
| `is_operator` | bool | — | — | ✅ | true for operator Points |
| `op_type` | string | — | — | ✅ | IMPL / NAND (operator Points only) |
| `status` | string | — | `pav:status` | ✅ | Lifecycle: draft, live, retracted, superseded, outdated, archived (#432). **challenged is a derived condition** (presence of a NAND operator edge on a live point), not a stored status (§5). draft inert for computation; retracted/superseded/archived are terminal |
| `confidence` | float 0..1 | — | — | ⚠️ | EP posterior mean, computed by propagation |
| `c_cal` | float 0..1 | — | — | ❌ | Calibrated confidence — calibrated counterpart to the EP posterior `confidence` (registered #909 §4.3 #11; written by the calibrated pipeline, slice 5+) |
| `quote` | string ≤200 | — | — | ⚠️ | Provenance quote — the source text this claim was drawn from; payload-level metadata today (SDK extraction path / EventAPI `provenance()` payloads — extractor.py, api.py), stored Point property per #909 §4.3 #11 (secret-scanned) |
| `authoredBy` | SubjectID | — | `dc:creator` | ✅ | Who created the claim |
| `validFrom` / `validTo` | ISO8601 | — | — | ✅ | Temporal validity window |
| `createdAt` / `updatedAt` | ISO8601 | ✅ | `dc:created` / `dc:modified` | ✅ | Timestamps |
| `lastDreamedAt` | ISO8601 UTC | — | — | ✅ | Freshness stamp — timestamp of the last EP write-back that **converged** on this claim (epic 903). NULL = never dreamed — **ranks STALEST** in the stale-first scheduler (first-deploy/legacy/crash-mid-pass graphs drain across passes). Non-operator claims only (operators excluded from ranking/stamping). Written **atomically with `confidence`** in the dream write-back (single UNWIND — the write-back's own fields lastDreamedAt+updatedAt are all-or-nothing; `confidence` is also flushed independently by `ep.run`'s `_flush_cache`, per the epic plan's redundancy note); failed/non-converged runs never update it; operator-less claims get a trivial stamp via the scan path. Composite index `:Point(is_operator, lastDreamedAt)` created idempotently at init on docker/server FalkorDB; embedded (redislite) gets plain `:Point(lastDreamedAt)` only — an `is_operator` composite is #522-unsafe on embedded (stale bool type table across reopen) |
| `embedding` | vector | — | — | ✅ | Semantic embedding (FTS + vector search) |
| `speaker` | string | — | — | ✅ | Role tag on episodic turn Points (user/assistant/…) — written by SDK `capture_session` (delta 5), not by hosted capture |
| `is_episodic` | bool | — | — | ❌ | Quota exemption discriminator — true on episodic turn Points from the regex capture path (the `points` branch counts non-episodic only, #909 §4.3 #13/§4.4; legacy nodes lack the flag — one-query backfill migration ships with #947) |

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
| `passes_frequency_gate` | bool | — | — | ❌ | S5 frequency-gate result flag — false entities are still written, flagged (registered #909 §4.3 #12; planned for the capture path, slice 5+) |
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
| `story_arc` | string | — | — | ❌ | Arc continuation of the captured session — `summary` = short ("what was done"), `story_arc` = the full arc continuation (registered #909 §4.3 #4; planned for the capture path, slice 5+) |
| `doc_status` | string | — | `pav:status` | ✅ | Capture lifecycle: captured (metadata-only) / extracted (full analysis) / draft |
| `sessionId` | string | — | — | ✅ | Source session identifier (for `documentKind: transcript` captures) |
| `eventId` | string | — | — | ✅ | Link to the producing Event's log record — 1-hop audit to the mechanism snapshot |
| `sourcePath` | string | — | — | ✅ | Filesystem path to the original conversation file — agents can open/search it after finding the session |
| `_searchText` | string | — | — | ✅ | FTS index text = title + summary + topics (coalesce-null sentinels) |

### §4.5 Event

| Field | Type | Required | ISO/PROV/DC | Impl | Meaning |
|-------|------|----------|-------------|------|---------|
| `eventId` | ULID / content-addressed | ✅ | `dc:identifier` | ✅ | Unique occurrence ID — ULID by default; the **agentSession Event uses a content-addressed form** (hash of session_id + captured_at — deterministic MERGE anchor, #909 §4.3 #3) |
| `eventKind` | string | ✅ | — | ✅ | meeting, decision, experiment, deployment, review, friction, extraction, documentCreated, roleCreated, pointAdded, sessionCaptured, AgentSession + pack eventKinds |
| `format` | string | — | `dc:format` | ✅ | Storage format (jsonl default, markdown) |
| `startedAt` / `endedAt` | ISO8601 | — | `prov:startedAtTime` / `schema:startDate` | ✅ | Temporal extent |
| `capturedAt` | ISO8601 | — | — | ❌ | Transaction time of the capture — bi-temporal complement to `startedAt`/`endedAt` (valid time). Registered #909 §4.3 #2; written by the agentSession capture path (endpoint payload field, slice 5) |
| `subject` | SubjectID | — | `prov:wasAssociatedWith` (inverse) | ✅ | Who performed the event (mirrors `performs` edge) |
| `object_name` / `object_type` | string | — | `prov:used` | ✅ | What was acted on / produced |
| `file_hash` | string | — | — | ✅ | SHA-256 of the file's UTF-8 text content (text-mode, universal newlines — CRLF-immune; #330/#900) |
| `is_episodic` | bool | — | — | ❌ | Quota exemption discriminator — true on capture-path episodic Events (registered #909 §4.3 #13; planned for the capture path, slice 5+) |

> **agentSession Event (#909 §4.3 #1):** the canonical eventKind for session-capture Events is **`AgentSession`** — EXACT code spelling (capital A; sdk.py `ingest_corpus`, session_indexer.py); `sessionCaptured` (the core kind still written by the regex capture path) is an **alias of the same concept** — both remain valid kinds, **no migration**. The capture graph's `:Session` node (session container, `CONTAINS` → turn Points) also carries `is_episodic: true` — the quota `sessions` branch counts `MATCH (s:Session)` (plan §4.4, slice 2).

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
| `provenance_spans` | JSON | — | — | ❌ | Window spans derived from the capture path's `provenance_refs` (plan-defined, #909 §4.3 #6; written by the capture path, slice 5+) |
| `is_episodic` | bool | — | — | ❌ | Quota exemption discriminator — true on the session Source (registered #909 §4.3 #13; planned for the capture path, slice 5+) |
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
| is_episodic | ✅ | — | ❌ | — | ✅ | ✅ |
| passes_frequency_gate | — | — | ❌ | ❌ (inherits Object) | — | — |

---

## §5. Core Kind Vocabulary

### Point Kind Vocabulary (core)

```
statement    # the LOGIC layer — THE extraction write kind (state-centric, option B 2026-08-12)
decision, vision, strategy, plan, goal, target, humanApproval, event   # LEGACY write kinds (write-compat only)
```
> **State-centric alignment (2026-08-12, option B):** Points are the LOGIC layer
> only, and the logic is one kind: **`statement`** — the asserted belief.
> `hypothesis` is FOLDED INTO CONFIDENCE semantics (a conjecture is a
> low-confidence statement); `observation` is removed (anything can be called
> one). `decision`/`humanApproval` are TIMELINE kinds → Event nodes (eventKind
> `decision`/`humanApproval`); `vision`/`strategy`/`plan`/`goal`/`target` are
> STATE kinds → Object kinds (commitment-state family, below); `event` is
> removed (issue #1013 — episodic records are Event nodes with eventKind
> `occurrence`/`turn`). The legacy kinds remain valid write kinds for
> compatibility; extraction emits `statement` only.

### Object Kind Vocabulary (core)

```
Project, WorkItem, document, tag, user, skill, tool, agent, workflow, agreement, standard, other,
strategy, plan, goal, target    # commitment-state family (state-centric, 2026-08-12) — states that
                                # commitments produce; carry lifecycle + derived confidence
```
> **State-centric alignment (2026-08-12):** `strategy`/`plan`/`goal`/`target`
> are STATE objects (superseded when a new commitment lands — the old strategy
> is deprecated, the new one promoted). Pack pointKinds used as options
> (product-strategy: useCase, userJourney, jobToBeDone, valueProposition) are
> OPTION/STATE kinds — pack-mapping item: promote to objectKinds (near-miss
> convention until the pack amendment).

### Event Kind Vocabulary (core)

```
meeting, decision, experiment, deployment, review, friction, extraction,
documentCreated, roleCreated, pointAdded, sessionCaptured, AgentSession, humanApproval,  # #531
occurrence, turn    # state-centric (2026-08-12): occurrence = generic extracted occurrence;
                    # turn = capture turn records (replaces pointKind 'event', issue #1013)
```
> **State-centric alignment (2026-08-12):** eventKind `decision` is the
> TIMELINE record of a commitment (the resolution is expressed as lifecycle
> writes on the state objects); `occurrence` covers extracted happenings;
> `turn` covers capture turn records. The Episodic layer is the truth for
> lifecycle: Object status is projected from its event stream (§2).

> **#909 §4.3 #1:** `AgentSession` (EXACT code spelling — capital A; sdk.py `ingest_corpus`/session_indexer.py) is the canonical kind for session-capture Events; `sessionCaptured` (the core kind written by the regex capture path) is an **alias of the same concept** — both remain valid kinds, **no migration**.

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
> kinds (github_issue, github_pr, linear_card, linear_cycle, slack_message, document...)
> resolve to a tier ONLY via explicit registration (`register_source_kind_default`) or
> an explicit `credibilityTier` assignment; unknown kinds stay neutral (no inheritance).
> Connector kinds register explicitly neutral in SOURCE_KIND_DEFAULTS
> (source_credibility.py) — connector Source materialization (#388) therefore never
> alters EP inheritance. The T0–T4 tier semantics above live on `credibilityTier`. The
> Beta-prior mapping (T0=(10,1), T1=(5,1), T2=(3,1), T3=(2,1), T4=(1.1,1)) is the
> validated model (docs/ep-source-credibility-experiment.md §1.1).

> **Expansion-pack kinds live in the packs, not here.** Pack-declared kinds (dev:epic, product-strategy:product, etc.) are defined in their pack manifests (§9) and registered at load time via the pack registry. This file documents only the core vocabulary; it is not the home for pack kinds.

> **#909 §4.3 #6:** `sourceKind: agentSession` is a registered source-type VALUE (the four-node capture model's session Source — the provenance bridge — carries it; the value belongs to the extensible sourceKind vocabulary above, alongside github_issue/slack_message/linear_card/…). Credibility-tier inheritance is keyed on **sourceKind** (#398): the tier resolves via the kind's registered tier default (`register_source_kind_default`) or an explicit `credibilityTier` assignment; unregistered kinds stay neutral (no inheritance).

### Point Status Vocabulary (canonical, #432/#690)

| Status | Kind | Write path | Transitions to | Notes |
|--------|------|------------|----------------|-------|
| `draft` | initial | `create_point`, `EventAPI._point` | `live` | Inert for EP computation; promoted on first operator edge |
| `live` | active | `create_operator` (auto-promote source), `update_point` (status='live') | `retracted`, `superseded` | Full EP participation |
| `retracted` | terminal | `retract_point`, `EventAPI.retract_point` | *(none)* | Tombstone — stays in graph, `get_point` returns, `query`/`paginated_query` exclude by default |
| `superseded` | terminal | `supersede_point` (sets alongside `outdated:true`) | *(none)* | Structural replacement via CORRECTS edge + edge transfer |
| `outdated` | legacy flag | `invalidate_point`, `supersede_point` (legacy flag) | `retracted` | Back-compat boolean; co-exists with `status` |
| `archived` | terminal (reserved) | *(no v1 SDK write path)* | *(none)* | Reserved for future lifecycle operations |

> **`challenged` is NOT a state** — it is a DERIVED condition emerging from the presence of a NAND operator edge on a live point, queryable as such:
> ```cypher
> MATCH (p:Point {status:'live'})<-[:NAND]-(:Point {is_operator:true}) RETURN p
> ```
>
> **Tombstone contract:** Retracted points stay in the graph (`get_point` returns them with `status='retracted'`). Default query surfaces (`query`, `paginated_query`) exclude them; pass `include_retracted=True` or an explicit `status='retracted'` filter to surface them. Deletion via `delete_point` hard-deletes (no tombstone).
>
> **Parity decision (#690):** SDK + EventAPI + CLI share a single status vocabulary (`POINT_STATUS_VALUES` in `sdk.py`). EventAPI births `draft` (same as SDK); CLI backfill promotes NULL-status legacy points to `live` (migration-only, not a drift). The `:GraphEvent` label is RESERVED for the #432 change-log stream (`{seq, ts, type, payload, event_id}`, zero relationships — graph islands) — distinct from the `:Event` ontology entity with `eventId` (§3.4). See docs/event-catalog.md.

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

A relationship operates on two layers — **semantic** (relation type) and **epistemic**
(confidence / contradiction). It carries an operator **only when it needs one**.

#### Reification rule — when an edge gets an operator

**An edge carries an operator iff it needs mitigation, or is an epistemic
support/contradict between Points and/or Events (Point↔Point, Event→Point,
Point→Event).** All other edges stay plain and carry confidence as an
edge attribute.

| Edge | Operator? | Confidence |
|---|---|---|
| Point↔Point support / contradict (IMPL/NAND) | **Yes** | EP over the IMPL/NAND edge |
| Any edge needing mitigation (+/− relevance) | **Yes** — mitigations attach to the operator | EP over IMPL/NAND |
| Structural edge without mitigation (about\*, performs/produces/uses, memberOf/ownedBy, provenance) | **No** — plain edge | confidence edge attribute |

- **Operator-less propagation:** an IMPL/NAND edge may be direct Point→Point
  (no operator); EP propagates over it the same way.
- **Direction:** `bidirectional` (default) / `unidirectional`. Lives on the
  operator node when present, else on the edge. EP reads the operator node
  first, falls back to the edge.
- **Lazy promotion:** a plain edge gains an operator only when mitigation
  becomes needed.
- **EP:** for operator-less edges, EP reads direction from the edge and
  initializes the edge message directly (operator-mediated edges compute
  messages on operator update).

Operator-mediated case (support/contradict, with mitigation anchor):

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
| opposes | NAND | Bidirectional by default, optional unidirectional (directed attack) | declared by pack | Feature competesWith Competitor |

> **Direction is an explicit operator flag, default bidirectional.** The table above shows typical pack declarations; a pack (or agent) may declare `direction: unidirectional` for a directed attack.

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
symmetric by default (directed `unidirectional` NANDs traverse one-way
per §3.1 — extraction-emitted NANDs default directed, #909), hasPart
bidirectional — `_affected_claims` follows these directions.

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
