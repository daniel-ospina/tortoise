---
title: "DRAFT — State/Epistemic Separation + Constraint Regime (exploration, NOT approved design)"
type: draft
domain: engineering
doc_status: draft
created: 2026-08-09
ownedBy: epistemic-team
governingAgreement: "#753 (review gate)"
---

> ⚠️ **EXPLORATION DRAFT.** Filed per product owner direction: explore and unpack, do NOT implement from this. See issue #753 for the review gate.

# State/Epistemic Separation + Constraint Regime

Two-layer model (product owner's framing): **semantic STATE layer** = entities from the expansion packs; **pure EPISTEMIC layer** = Points that opine/connect/reinforce/negate the state, forming networky graphs over it. Constrained scope is desired.

## The budget that drives everything

The failure mode is arithmetic: regex/text-first extraction ≈ 88% noise at ~160 nodes/turn (measured) → ~16k nodes/median session vs the 25k solo cap. Every design decision exists to keep one number in bounds: **a real session produces 10–25 new nodes, hard-capped at 40; zero is a valid answer.**

| Artifact | Target / session | Hard cap / session |
|---|---|---|
| State entities created (not resolved) | 2–6 | 10 |
| Events emitted (incl. session event) | 2–4 | 8 |
| Epistemic Points extracted | 2–4 | 6 |
| Operators (IMPL/NAND) | 1–4 | ≤1.5×points (≤8) |
| Cross-session operators | 0–1 | 2 |
| **New nodes total** | **10–25** | **40** |

## The three layers (validated against KG best practice, 2026-08-09)

1. **OCCURRENCE — Event node**: the agent session happened (eventKind **`agentSession`** — agent-human/agent-agent; human-to-human is a different kind: `conversation`/`meeting`; §4.5 + §3.5 Subject→Event→Object). Bi-temporal startedAt/endedAt (valid) + capturedAt (transaction); content-addressed ID; participants with roles (n-ary, the hyperedge). Immutable.
2. **CONTENT — Source node**: what was said (sourceKind `conversation`, summary + story arc; §4.6). Mutable/rewritable artifact.
3. **EPISTEMIC — Points**: derived claims/decisions.

Linking: `Subject -performs-> Event -produces-> Source`; `Points -extractedFrom-> Source` (canonical); `Points -aboutEvent-> Event` (shortcut); Source `references` Entities (§3.4). Rationale (research): occurrence ≠ content — a mutable Source must not carry the immutable occurrence identity; the Event enables cross-event-kind timelines, n-ary participant queries, PROV conformance (wasGeneratedBy requires an Activity node), and the context-graph "decision events" layer.

## Semantic state layer

- Pack `objectKinds` + `subclassOf` become a **kind tag on one label** (e.g. `:Object {objectKind: "pm:issue"}`), not a new graph label per kind — schema evolution stays cheap.
- Pack relations become schema-validated structural edges with fromKind/toKind/cardinality.
- **Key amendment**: entity↔entity relations are plain structural facts (no operator Point per relation — kills node count); IMPL/NAND attach only to Points. "competesWith between Feature and Competitor" is a fact; "we will win" is a belief (a Point).
- Entity lifecycle: extract (typed candidates, must have quoted evidence span) → resolve (exact match → merge; fuzzy → provisional + review; NO auto-merge) → apply via events (event-first, projection-second, bi-temporal validFrom/validTo).

## Epistemic layer

- Point = content + pointKind + status + EP-derived confidence + provenance (extractedFrom Source, authoredBy, span).
- `statement` stays but is **condition-gated**: only extracted when a genuine argument unit (asserts support/attack, a quantified fact about state, or a stance). Bare restatements are not extracted.
- Points reference state via typed `aboutObject`/`aboutSubject` edges (ONTOLOGY §3.2). Resolution quality modulates prior (exact → full; fuzzy → ×0.7; unresolvable → free-text + review).
- Points connect via IMPL (support, unidirectional) / NAND (attack; bidirectional by default — mutual; agent may declare directed/unidirectional); cross-session capped at 2, never auto-wired mitigations.

## The constraint regime

(a) **Bounded kinds** — per pack ≤12 objectKinds, ≤8 pointKinds, ≤12 eventKinds; enforced at manifest load.
(b) **Bounded relations** — per pack ≤8 relations; extraction never emits a dangling edge (both endpoints must exist).
(c) **Bounded point surface** — per pack ≤3 extraction-active pointKinds; core extraction-active = {decision, observation, statement-conditioned}; vision/strategy/goal are NOT conversation-extraction targets.
(d) **Enforcement (4 surfaces)** — strict-mode prompt (closed kind/predicate lists, extract-nothing explicit, constrained JSON) → parse-time validation (unknown kind: retry once → hold; never silently drop) → SDK write-time validation (block undeclared predicates; warn+write declared-predicate-undeclared-pair; hold `other`-typed entities) → calibration gate (weak tier-scaled priors; batch runs pass the ≥70% precision gate).
(e) **Out-of-schema policy** — undeclared predicate: BLOCK; unknown kind: retry→hold; untyped pair: WARN+write; `other` entity: hold→review (pack proposal); fuzzy match: provisional+review; **empty extraction: valid, first-class, unpunished.**

## Layering contract

- Epistemic MAY: reference state (about* edges), opine (NAND attacks the claim, never the entity), propose state changes as **suggested events** ("beliefs don't mutate state; they propose events"), trigger invalidation via reverse about* cascade.
- Epistemic may NOT: change entity identity, write state properties directly, attach operators to entities, delete state (invalidation over deletion).

## Risks

State pollution → evidence-span requirement + provisional status + review. Epistemic noise → hard caps + extraction-active whitelist + contested-flagging + calibration. Schema brittleness → versioned packs + vocab-evolution loop (other-typed entities feed pack proposals). EP degradation → operators only between Points; no auto-mitigations.

## Worked example (pm pack)

Transcript: "Moving issue #42 into sprint 14... DB migration #42 depends on isn't done... e2e suite is the blocker, fails 40% of CI... we decided to stub auth in e2e... the stub masks real failures. Decision: keep #42, fix the stub first."

→ ~5 state entities (2 resolved, 3 created), 3 events, 3 epistemic Points (decision + observation + statement), edges (partOfSprint, dependsOn, blocks, IMPL, NAND, about*) — **~10 new nodes, ~14 edges** (vs ~16k with the regex path). Target zone, ~16× fewer nodes.

## Open questions

- §8 ontology amendment (entity-entity relations no longer operator Points) — sequence with direction-flag migration.
- Point kind `argument` — only after measurement shows condition-gated `statement` under-delivers.
- Chunking for >150-turn sessions (chunk → extract → merge-dup within session).
- **Episodic substrate (RESOLVED by product owner, 2026-08-09): NO per-turn nodes.** The raw conversation is ONE Source node (sourceKind `conversation`, §4.6) with an indexed summary + story arc; derived Points/Objects connect via extractedFrom → Source (§3.3) and Source references → Entity (§3.4). Node count per session = 1 Source + ~10-25 knowledge nodes.
