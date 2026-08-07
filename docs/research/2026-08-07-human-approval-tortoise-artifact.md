---
title: "Research: Human approval as tortoise artifact — Point, Event, or both?"
type: data
domain: data
doc_status: live
subjects.team: organisation-design-team
created: 2026-08-07
aboutObjects: tortoise, ontology, falkordb
---

# Research: Human approval as tortoise artifact — Point, Event, or both?

**Date:** 2026-08-07
**Status:** Converged — recommendation + filed implementation issue (tortoise#531, links #421)
**Type:** Ontology / epistemic-graph design research
**Issue:** daniel-ospina/tortoise#421 (Level: project, Complexity: standard)
**Downstream consumer:** tortoise#427 (Tortoise Planning skill — human approval gates at 7 planning steps); originating epic eldato#6870 (Agent State Machine, Path C)

---

## Problem

During the Agent State Machine epic planning (Path C: tortoise-enriched + formal decompose), human review happened informally (inline chat approval) with **no formal graph artifact recording that approval**. The tortoise graph cannot reason about *"what did the human approve and when?"* — which blocks the downstream Tortoise Planning skill (#427) that needs approval gates at every planning step, and blocks the epic's own "AI and human review gates" indicator.

**Reframed problem:** "A human planning stakeholder is trying to record an approval of a planning artifact so the graph can reason about what was approved, by whom, when, and what it strengthens, but there is no canonical artifact for it — Point and Event exist but no approved pattern — which results in approvals living outside the graph, invisible to belief propagation and to downstream skills."

**Domain classification:** Complicated. Ontology design + provenance standards + belief-propagation mechanics; multiple valid patterns exist in the wild (PROV-O, DecPROV, schema.org, event sourcing, Wikidata); requires synthesis across independent standards. Not Clear (no single canonical answer), not Complex (bounded scope, no emergent behavior).

---

## What We Have Internally

### Ontology v3.1 (docs/ONTOLOGY.md, canonical) — confirmed by reading the live doc

- **Five entity types:** Subject (`prov:Agent`), Object (`prov:Entity`), **Point** (`prov:Entity` specialized — claims/decisions/structural artifacts in the belief graph), **Event** (`prov:Activity` — reified middle node `(Subject)-[performs]->(Event)-[produces]->(Object)`), Source (provenance anchor).
- **Four-ontology model (§2):** Semantic (Subject/Object/Source), Epistemic (Point + IMPL/NAND operators with EP confidence), Episodic (Event — append-only, timestamped), Procedural (**status is derived, not stored** — projected at query time from the event stream).
- **§3.8 Event Edges:** `op: IMPL/NAND | Event → Point` — "Outcome influence on belief (epistemic)". Declared but **no producer exists** (see gap below).
- **§5 Core kind vocabularies:** pointKind: `statement, decision, vision, strategy, plan, goal, target, observation, hypothesis`; eventKind: `meeting, decision, experiment, deployment, review, friction, extraction, documentCreated, roleCreated, pointAdded, sessionCaptured`. Expansion-pack kinds live in pack manifests (`packs/*/manifest.yaml`).
- **§11 Reputation (derived, not stored):** `subject -[:performs]-> events → outcome operators (Event→Point IMPL/NAND) → aggregate → reputation score`.

### Code mechanics — confirmed by reading the code

- **`resolution-event` / `resolution-vector` Points seed the grounding activity vector** (`projection/grounding.py`): `a_i = 1.0` for `pointKind IN ['resolution-event','resolution-vector']`, non-operator; grounding solved as `g = (I − λM)⁻¹a` over IMPL/NAND adjacency. `compute_grounding()` auto-triggers on `add_point` when pointKind is `resolution-event` (`api.py:121`). Currently only the CLI ingest `--resolution` flag creates these (`ingest.py:569`).
- **EP supports directional IMPL** (`ep.py`): explicit `direction` flag on operators; unidirectional IMPL suppresses back-messages/amplification loops (`ep.py:174, 220-231`; wiki synthesis resolved "directional IMPL as default — information loss is semantically correct"). Evidence injection: `ep.run(op_ids, evidence={claim_id: (alpha, beta)})` sets `ep_alpha`/`ep_beta` on claims (`ep.py:416`).
- **`create_operator(op_type, source_id, target_ids, label, direction)`** (`sdk.py:719`): op_type must be IMPL/NAND/part-whole; creates operator Point + edges. ⚠️ **Validates sources as Points** — cannot create Event→Point operators today.
- **`compute_reputation(subject_id)`** (`sdk.py:3606`): queries `(s:Subject)-[:performs]->(e:Event)-[:IMPL|NAND]->(p:Point)` — **already expects Event→Point operator edges** and counts IMPL as reputation success, NAND as failure (Beta(1+impl, 1+nand)). **No eventKind filter.** (This creates a circularity risk — see Recommendation §5.)
- **`file_decision(options, evidence, choice)`** (`sdk.py:1175`) + MCP `tortoise_file_decision` (`mcp_server.py:474`): existing decision-filing pattern — decision Point + option Points + evidence Points with IMPL edges. Point-centric; no Event node.
- **`create_event(name, eventKind, ...)`** (`sdk.py:3326`): creates Event node (`eventStatus: "scheduled"`), wires `aboutSubject/aboutObject/aboutPoint/aboutDocument` edges.
- **Auto-mining pollutes `decision` kind:** extractor (`extractor.py:430`) auto-classifies Points as `pointKind: decision` from cue words ("decided", "chosen", "chose", "select", "will adopt"); mining (`mining.py:28-30, 162`) auto-derives `decision` **Events** from cue words ("decided", "confirm", "finalize", "settle", …). `decision` is therefore a mined kind, not an approval-recording kind.
- **Supersession:** `supersede_point(old, new)` (`sdk.py:448-485`) — CORRECTS edge, marks outdated, transfers edges. Available for approval revocation.
- **Kinds registry is descriptive, not restrictive** (`domain_loader.py`): "The system accepts any string — the registry is descriptive (used for warnings), not restrictive." New kinds are compatible.
- **No organisation-design pack exists** in `packs/` (current: dev, marketing, product-strategy, project-management). New approval kinds go in core vocab (ONTOLOGY §5) or a new pack.

### Prior epistemic memory
- Tortoise memory checkpoint: hosted API unavailable (no `TORTOISE_API_KEY`) — no prior claims retrieved. Graceful skip.

---

## External Findings

### RQ1: Point, Event, or both? → **BOTH** (Event-first; Point as the epistemic projection)

**[HIGH — 4+ independent source families]**

- **PROV-O / DecPROV:** decisions are modeled as `prov:Activity` (Events). The **Decision Provenance ontology (DecPROV)**, a specialization of PROV-O derived from the W3C Decision Ontology, models decisions as activities; it renamed the DO's `Option` class to **`Option Selection`** to reinforce its temporal, process nature. DecPROV is *data-driven only* — it records decisions already made (PROV has no templating for normative/future scenarios). Decision provenance literature frames accountability as exposing the "decision pipeline" — the chain of inputs to, and flow-on effects from, decisions and actions. (PROV-O spec, DecPROV repo, MODSIM 2017 paper, arxiv 1804.05741)
- **Event-first reification:** **[MEDIUM — 2 sources]** The "When Predicates Lie: SHACL, Reification, and the Event-First approach" analysis argues approvals, provenance, and temporality are better modeled by **reifying the event itself** rather than attaching metadata to the entity; graph-reification guidance likewise captures who/when/confidence via events. ⚠️ verify-when-available: no third independent source found yet — the argument is corroborated by RDF-star practice but not by a dedicated third analysis.
- **Schema.org:** **[MEDIUM — 2 sources]** `ApproveAction`, `ConfirmAction`, `EndorseAction`, `ReviewAction` are **Action subtypes** (agent + object + result roles) — approval as an action/event, not as a property of the thing approved. (schema.org docs + W3C Actions-in-schema.org drafts; note schema.org is itself a W3C Community Group product, so the two are not fully independent.)
- **Event sourcing:** decisions are recorded as immutable **domain events**; state is a projection derived by replay. Azure Architecture Center, eventsourcing docs, DDD guidance. Tortoise §2 already adopts this: "Status is derived, not stored."
- **The decisive constraint is internal:** tortoise's epistemic layer requires **Points** for belief — the grounding a-vector seeds from Points, and EP operates on Points via IMPL/NAND. **An Event alone cannot strengthen dependent claims.** The Event is the source of truth (provenance/authority); the Point is the epistemic projection (belief effects).

### RQ2: How PROV-O handles human decisions

**[HIGH]**

- `prov:Activity` for the decision act; `prov:wasAssociatedWith` (Activity→Agent responsibility); `prov:actedOnBehalfOf` (delegation); `prov:used` (inputs consumed, e.g., the artifact); `prov:wasGeneratedBy` (outcome produced); `prov:startedAtTime`/`prov:endedAtTime`.
- Tortoise already maps all of these (§12 of ONTOLOGY.md): Event=`prov:Activity`, `performs`=inverse of `wasAssociatedWith`, `uses`=`prov:used`, `produces`=`prov:wasGeneratedBy` inverse. **An approval Event is fully PROV-O-conformant with zero new edge types.**
- DecPROV simple ("one-hit") model: Question → DecisionMaking (Activity) → Answer. Tortoise analog: the approval gate is the Question; the approval Event is DecisionMaking; the decision Point is the Answer.

### RQ3: How approval should propagate through IMPL edges

**[HIGH — code-confirmed]**

- **Mechanism:** approval Point -[:IMPL {direction: unidirectional, label: "approvedBy"}]-> each approved claim Point. Unidirectional direction is critical — directional IMPL was resolved as the tortoise default (back-message information loss is semantically correct).
- **EP confidence:** run EP with evidence prior `{approval_point_id: (alpha_high, beta_low)}` → `ep.py` writes `ep_alpha`/`ep_beta` → confidence propagates through IMPL factors to dependent claims B, C, D. This is exactly "human approval strengthens dependent claims."
- **Grounding:** if the approval Point's kind is in the grounding seed set (`resolution-event`/`resolution-vector` today, or a new `humanApproval` kind added to the seed query), `a_i = 1.0` makes the approval a relevance anchor; `(I − λM)⁻¹a` spreads that relevance to dependents.
- Chain semantics for #427: approved customer profile IMPL→ use cases IMPL→ workflows IMPL→ requirements — later edits to an approved claim propagate through the same edges.

### RQ4: Should approval be a special operator type (APPROVED edge)? → **NO**

**[HIGH — ontology constraint + consistent external patterns]**

- Ontology §2: **operators connect only epistemic targets (Event→Point, Point→Point).** A third operator type (APPROVED) would require changes to EP, grounding, propagation, confidence computation, valid predicates, MCP tools, and every consumer — high blast radius for zero semantic gain.
- PROV/DecPROV: approval IS an activity, not an edge. Schema.org: ApproveAction is an Action, not a property.
- Reification argument: a bare APPROVED edge carries no reified metadata (who/when/why) — you still need the event.
- Human-vs-LLM distinguishability comes from **Event provenance** (approval Event `performs` the human Subject), not from a new edge type.
- If a labeled relation is ever needed, the semantic layer already supports it via `operator.label` on IMPL (e.g., `label: "approvedBy"`) — no new mechanism.

### RQ5: Composition with existing Event types (ONT-003 / §5)

**[HIGH — code-confirmed]**

- Core eventKind vocab: `meeting, decision, experiment, deployment, review, friction, extraction, documentCreated, roleCreated, pointAdded, sessionCaptured`.
- **`decision` is unsuitable:** it is auto-mined from conversation cue words (mining.py:162; extractor.py:430 also auto-tags Points as `decision`). Recording human approval gates under `decision` conflates **binding human approvals with mined conversational decisions** — exactly the provenance pollution this research must avoid.
- **`review` is unsuitable:** a review produces an opinion; an approval is a binding go/no-go that unlocks downstream reasoning.
- **Recommendation: a NEW `humanApproval` eventKind** (core-vocab addition or pack-declared). Clean query surface: "what did the human approve?" → `MATCH (s:Subject)-[:performs]->(e:Event {eventKind:'humanApproval'})-[:aboutPoint]->(p:Point)`. No collision with mined kinds; the approval act is always provenance-verifiable.

### RQ6: Wikidata / academic KG authority assertions

**[HIGH; one LOW note]**

- **Wikidata:** a statement = claim + references + rank. References are provenance anchors (`stated in (P248)`, `reference URL (P854)`); Wikidata "makes no assumptions about the correctness" of statements — only that they are reported with a source. **Authority lives at the reference level, not in a separate approval node.** Tortoise analog already exists: `(Point)-[:extractedFrom]->(Source)` (§3.3) — approval does not need a new authority artifact.
- **Human-in-the-loop KG validation literature** (CleanGraph; LLM+HITL triple validation): **[MEDIUM — 3 papers]** human judgment is a **process gate** during KG construction — validated triples are either incorporated or discarded; the validation act itself is typically not materialized as a graph artifact. This differs from tortoise's need (recording approvals of artifacts that already exist as Points to gate *downstream reasoning*), but confirms human validation is an event-like act, not an edge.
- **RDF-star / reification:** provenance, temporality, and approval metadata → reify. Tortoise already does RDF-star-like reification via operator Points (§12).
- ⚠️ **single-source (absence-of-evidence):** no direct academic treatment of "approval node vs event node" as an explicit modeling debate was found across Perplexity, Exa, and web searches. The recommendation extrapolates from PROV, DecPROV, schema.org, event sourcing, and HITL-validation literature. Verify-when-available: search "approval provenance ontology" / "endorsement node knowledge graph" when new sources appear.

---

## Contradictions

| # | Contradiction | Resolution |
|---|---|---|
| 1 | **PROV/DecPROV (decision = Activity)** vs **HITL-KG papers (human validation as external process, not materialized)** | Not a true contradiction. HITL papers concern KG *construction* validation (should this triple be accepted at all). Tortoise's question is recording approvals of *planning artifacts that already exist as Points* to gate downstream reasoning. Different scopes; both support Event-first for tortoise. |
| 2 | **Wikidata (claim-centric, references for authority, no approval node)** vs **schema.org (approval as Action)** | Wikidata models facts reported from sources (no workflow); schema.org models action verbs. Tortoise has BOTH layers (semantic claim + episodic event) — the two patterns compose rather than conflict. |
| 3 | **resolution-event reuse (zero code change)** vs **new `humanApproval` pointKind (clean semantics, 1-line grounding change)** | Implementation choice, not evidence-decidable. Verifier caught that reusing `decision` is invalid (auto-mined); reusing `resolution-event` conflates "ingestion completed" with "human approved". **New pointKind wins.** |

---

## Recommendation (converged)

### Pattern: "Approval = Event + decision Point + IMPL fan-out" — **BOTH, Event-first**

A human approval is recorded as **an Event (the occurrence — who approved what, when, with what authority) AND a linked Point (the epistemic claim — what belief change the approval asserts)**. The Event is the append-only source of truth (provenance, procedural status derivation); the Point is the epistemic projection that participates in belief propagation and grounding.

```
(:Subject {name: "Daniel"}) -[:performs]-> (:Event {eventKind: "humanApproval", startedAt: T})
    (:Event) -[:uses]-> (:Document {title: "Customer Profile CP-001"})        ← artifact approved
    (:Event) -[:aboutPoint]-> (:Point "CP-001 targets SMB segment")           ← claims approved
    (:Event) -[:produces]-> (:Point {pointKind: "humanApproval",
                                     content: "Approved: Customer Profile CP-001"})  ← decision Point
(:Point "Approved: CP-001") -[:IMPL {direction: unidirectional, label: "approvedBy"}]->
    (:Point "CP-001 targets SMB segment") → ... → dependent use-case/workflow/requirement Points
```

1. **Event** — new `eventKind: humanApproval`. Approver Subject `performs`; artifact Object/Document `uses`; approved claim Points `aboutPoint`; decision Point `produces`. Append-only, timestamped, PROV-O-conformant with zero new edges (RQ2). This is the authority artifact: "the human said yes, here, then."
2. **Point** — **new `pointKind: humanApproval`** (NOT `decision` — that kind is auto-mined by extractor/mining and would conflate approvals with mined decisions; and adding `decision` to the grounding seed set would turn every mined decision into a grounding anchor, diluting the seed set). Content: "Approved: <artifact>". `status: live`, `authoredBy: <approver>`. Seeds grounding if its kind is added to the grounding seed query (`grounding.py` — one-line extension of the `pointKind IN [...]` filter). Supersession for revocation: approval reversed → `supersede_point` (CORRECTS) marks the approval outdated.
3. **IMPL fan-out** — approval Point → (unidirectional IMPL) → each approved claim Point, then **EP with evidence prior** on the approval Point. Confidence lifts propagate to dependents B, C, D (RQ3). Unidirectional is required (directional IMPL is the resolved tortoise default).
4. **Procedural** — the artifact Object's status (`approved`) is **derived at query time** from the `humanApproval` event stream (ONTOLOGY §2: status is never stored). No stored status field.
5. **Reputation guard (required)** — `compute_reputation` (sdk.py:3606) counts Event→Point IMPL as success and reads the target Point's *current confidence*. A humanApproval event with IMPL edges would (a) inflate the approver's reputation from their own approvals and (b) create circular feedback (approval raises point confidence; reputation reads that confidence). **Design must exclude `eventKind: humanApproval` from reputation outcome aggregation** (or track approval *quality* via downstream falsification — deferred, out of scope).
6. **#427 integration** — each of the 7 Tortoise Planning gates (customer profile, use cases, workflows, requirements, options, decisions, architecture) produces exactly this pattern: one `humanApproval` event + one decision Point + IMPL to that step's artifact claims. The graph accumulates the approval chain and can answer "what has the human approved, what does it unlock, and what would break if an earlier approval were revoked."

### What this deliberately does NOT introduce
- ❌ No new operator type (APPROVED edge) — violates ontology §2; no semantic gain (RQ4).
- ❌ No reuse of `decision`/`review` kinds — auto-mined / non-binding (RQ5).
- ❌ No stored `approved` status on Objects — violates the derived-status principle (§2).
- ❌ No new authority entity — authority = Subject performing the Event + Source provenance chain (RQ6, Wikidata pattern).

---

## Implementation Issue Design (filed as tortoise#531)

**Level:** task | **Complexity:** standard | **Team:** organisation-design-team
**Depends on:** #421 (this research) | **Consumers:** #427 (Tortoise Planning skill), eldato#6870

**Deliverable:** `file_human_approval` — the SDK primitive + MCP tool + ontology registration + reputation guard that implements the approved pattern.

1. **SDK** — `sdk.py`: new `file_human_approval(approver_id, artifact_id, point_ids, decision_content=None)`:
   - Atomically: (a) create `Event` (`eventKind: "humanApproval"`, `startedAt` now, `performs` approver Subject, `uses` artifact Object/Document, `aboutPoint` each approved claim Point, `produces` the decision Point); (b) create decision `Point` (`pointKind: "humanApproval"`, content "Approved: <artifact>", `status: live`); (c) create **unidirectional** IMPL operator approval→each target (label `approvedBy`); (d) run EP with evidence prior `{approval_point: (10, 1)}` over the fan-out; (e) trigger `compute_grounding()`.
   - Return `{event_id, decision_point_id, impl_operator_ids, confidence_delta}`.
2. **Kinds registration** — add `humanApproval` to eventKind vocab and `humanApproval` to pointKind vocab (ONTOLOGY.md §5) + `domain_loader.py` `_BASE_KINDS`.
3. **Grounding seed** — `projection/grounding.py`: extend seed query to `pointKind IN ['resolution-event','resolution-vector','humanApproval']`.
4. **Reputation guard** — `compute_reputation`: exclude `eventKind: 'humanApproval'` from outcome aggregation (add eventKind filter to the two queries); document rationale.
5. **MCP** — `mcp_server.py`: `tortoise_file_human_approval` mirroring `tortoise_file_decision` (mcp_server.py:474).
6. **Docs** — ONTOLOGY.md: §3.8 note (humanApproval as canonical Event→Point pattern), §5 vocab additions, worked example.
7. **Tests** — `tests/`:
   - Approval creates Event + decision Point + unidirectional IMPL edges; dependent claims' confidence rises after EP run.
   - Grounding seeds from `humanApproval` pointKind.
   - `compute_reputation` unchanged by approval events (guard works).
   - **Mined `decision` points never seed grounding and never register as approvals** (protects against RQ5 pollution).
   - Revocation via `supersede_point` marks approval outdated and propagates.

---

## Open Questions

1. **Approval quality tracking** (deferred): should an approval's reputation effect be evaluated retroactively when the approved claims are later falsified (NAND)? Proposes an `approval-outcome` feedback loop — deliberately out of scope for the first implementation.
2. **Gate semantics for #427:** is an approval *required* before a step's claims go `live`, or does the skill just record approvals as they happen? (Skill-level decision; the graph primitive supports both.)
3. **Approval scope granularity:** approve artifact (whole Document) vs individual claim Points? Design supports both (`aboutPoint` fan-out); #427 should pick per-artifact with a representative claim set.
4. **Pack vs core:** should `humanApproval` kinds live in a new `organisation-design` pack (namespace-scoped) or core vocab? Core is simpler; pack is cleaner for team-scoped kinds. Recommended: core for v1 (matches `decision`/`review` placement), pack later if namespacing is needed.

---

## Source Confidence Summary

| Claim | Tier | Sources |
|---|---|---|
| Decision/approval = Activity in PROV-O / DecPROV | High | PROV-O spec; DecPROV repo; MODSIM 2017 paper; arxiv 1804.05741 |
| Event-first reification for approvals | Medium | SHACL substack analysis; TrustGraph reification guide ⚠️ verify-when-available |
| Schema.org ApproveAction as Action | Medium | schema.org docs; W3C Actions-in-schema.org drafts (not fully independent — same org) |
| Event sourcing: decisions as immutable events, state as projection | High | Azure Architecture Center; eventsourcing docs; DDD guides; tortoise §2 (internal) |
| Wikidata references as provenance anchors | High | Wikidata Glossary; Help:Sources; Help:Statements |
| HITL KG validation as external process gate | Medium | CleanGraph (arxiv 2405.03932); ScienceDirect paper; Zenodo paper |
| No-new-operator verdict | High | Tortoise ontology §2 (internal) + PROV + schema.org (consistent) |
| `decision` kind is auto-mined (pollution risk) | High (code-confirmed) | extractor.py:430; mining.py:28-30,162 |
| Reputation circularity risk from approval events | High (code-confirmed) | sdk.py:3606 compute_reputation (no eventKind filter) |
| No explicit "approval node vs event" modeling debate in literature | Low | absence-of-evidence across Perplexity + Exa + web searches ⚠️ verify-when-available |

**Internal code claims verified against the live checkout:** grounding seed query (grounding.py:59), EP evidence priors + directional IMPL (ep.py), create_operator Point-only validation (sdk.py:719), compute_reputation Event→Point query (sdk.py:3606), file_decision (sdk.py:1175, mcp_server.py:474), resolution-event creation (ingest.py:569, api.py:121), extractor decision auto-tagging (extractor.py:430), mining decision events (mining.py:162).

---

*Filed under: daniel-ospina/tortoise — research from issue #421. Implementation: tortoise#531.*
