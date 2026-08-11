---
title: "Mining System Requirements v1 — from owner review (2026-08-09)"
type: requirements
domain: operations
doc_status: draft
created: 2026-08-09
ownedBy: epistemic-team
governingAgreement: "#753, #312"
---

> **These are REQUIREMENTS for the value-first mining system, derived from the product
> owner's review of the gold-window draft.** Each requirement is traced to the review
> comment that produced it. The system is NON-DETERMINISTIC — these requirements define
> the *behavior contract*; testing is distributional (precision/recall over labeled
> windows), not exact-match. A gold window is a rubric definition, not an oracle.

---

## R1 — Decisions are not Events

**Review input:** *"D1: it's not a decision. It's just an event 'repaired a thing'"* … *"D12: not a decision, just an event, might be redundant with an issue-completion event."*

**Requirement:** The extractor MUST distinguish **epistemic decisions** (commitments made: "we decided to X", "we will X") from **episodic events** (things that happened: "repaired X", "shipped X", "fixed X").

- A decision becomes a **Point** (pointKind `decision`): durable, propagates confidence, queryable as belief.
- An event becomes an **Event node** (occurrence): recorded, linked to what it produced, but NOT a belief with confidence.
- Trigger distinction: "did / repaired / fixed / shipped / completed" → event; "decided / will / should / chose / we're going with" → decision. The classifier must NOT stamp every "we did X" as a decision.
- Output shape: the mining system emits a typed stream `{decisions[], events[], claims[], entities[]}` — layer-correct from the start.

## R2 — One point, one decision (atomicity)

**Review input:** *"D6: it's not one decision, more like 3 that shouldn't be lumped together"* … *"D7: 'warrants deferred' + 'break-even via cost cut' — looks like trying to put too much into a single point"* … *"D10: sounds like 2 decisions."*

**Requirement:** Each extracted decision Point MUST be **atomic — a single commitment**.

- Compound statements ("tiers are feature baselines AND usage is metered AND no capture caps") MUST be split into separate Points.
- Rationale/mechanism must not be fused into the decision point (the *what* and the *why-it-pays-for-itself* are different decisions if they're independently made).
- Splitting is the extractor's job at classification time — do not post-process compound points.
- Acceptance proxy: per-window, the number of distinct commitments the extractor emits approximates the number a careful human would list (measured distributionally, not exactly).

## R3 — Process decisions attach to the work item, not the graph

**Review input:** *"D11: ok but it's a process decision and those need to be attached to the right place (the epic/issue/work item)."*

**Requirement:** The extractor MUST distinguish **product-knowledge decisions** from **process/governance decisions** ("validate the rubric on 2 windows first", "record this on the issue").

- Process decisions are NOT emitted as epistemic Points. At most, they surface as a tagged note (e.g., pointKind or a `process: true` flag, `status: draft`, not propagated) — and the system's routing records them on the relevant work item where an integration exists.
- The ontology's `pointKind` vocabulary should include or allow a process variant, OR the extractor drops them with a logged reason. Decide in design; default = drop from graph, route to work item.

## R4 — Every claim cites its source via the ontology

**Review input:** *"C1: what's the source? we need to use our ontology when possible."*

**Requirement:** Every extracted claim MUST carry ontology-based source provenance: `(Point)-[:extractedFrom]->(Source)` with the Source carrying `sourceKind` (measurement, analysis, paper, decision-record, conversation) and `credibilityTier`.

- The extractor emits the source reference with each claim (what source + span), not just free text.
- Measured findings ("regex = 88% noise") reference the specific measurement/analysis source, not a vague "analysis".
- Source node creation is part of the mining write (per the capture architecture: Source = content node with summary; claims extractFrom it).

## R5 — Evidence attaches to decisions; never discard supporting evidence

**Review input:** *"Specific token-cost tables for GLM/Qwen — this was a valuable point (ideally connected to the source) to keep connected to the decision."*

**Requirement:** Evidence that supports a kept decision MUST be preserved and linked (IMPL: evidence → decision), not dropped as "noise."

- Cost comparisons, measurements, and research findings are evidence claims that support decisions — they get Points (or source-cited claims) with IMPL edges to the decisions they justify.
- The "nothing" list must never include *evidence for a kept decision*. The value gate's selectivity applies to noise, not to supporting material.
- Consequence: the value brief's "NEW/REVISES/CONNECTS/RESOLVES" gate must classify evidence-for-a-decision as CONNECTS (keep), not DROP.

## R6 — Entities are typed by the pack ontology, not free-form

**Review input:** *"Object:product are not identifying the product (tortoise) but are more like decisions about architecture… 'value-first extraction' no idea what that is"* … *"missing everything related to the product-strategy expansion pack which should give us the logic for usecase-feature-user journey-workflow-requirement-architecture."*

**Requirement:** Entity classification is a **closed vocabulary** from the active pack(s):

- "value-first extraction" and "capture architecture (local/remote)" are **features** — not product objects, not free-form labels.
- The product-strategy pack provides the kind chain: `useCase → feature → userJourney → workflow → requirement → architecture`. Extracted entities MUST be assigned one of these kinds when the content is product/strategy; the pack's objectKinds otherwise.
- Never let the LLM mint kinds (SPIRES lesson). Unknown entity → `other` → hold/review → pack proposal (the 3-strike loop), not a new kind.

## R7 — Sources are indexed as Source nodes, always

**Review input:** *"what happened with adding sources to the graph (not the full data but at least indexing the source so we know where data came from)… currently this is a massive gap, our graph shouldn't have that."*

**Requirement:** Research reports, papers, and other source artifacts MUST be indexed as **Source nodes** in the graph — even when their full content is not extracted.

- The graph must never contain a claim whose source is untraceable: `extractedFrom` must always resolve to a Source node.
- Source indexing is a first-class mining output (create the Source node + metadata: sourceKind, title, url/ref, tier), separate from content extraction.
- This closes the provenance gap: "where did this claim come from" is always answerable, for every claim, at every tier.

## R8 — The system is stochastic; tests are distributional

**Review input:** *"editing the doc is kind of pointless because the system is non-deterministic."*

**Requirement:** The gold set defines the **rubric**, not an oracle. Testing is distributional:

- Precision/recall/F1 over labeled windows with fuzzy matching (embedding similarity ≥0.90 + kind match), never exact-string.
- Behavioral contract tests (the requirements above as pass/fail probes): given a crafted window with decisions+events, does the extractor classify layer-correct (R1)? split compounds (R2)? route process decisions (R3)? cite sources (R4)? preserve evidence (R5)? use pack kinds (R6)? index sources (R7)?
- These behavioral tests are deterministic-enough (property-style) and run in CI alongside the distributional eval.

---

## Requirements traceability

| Requirement | Owner review source | Design doc section |
|---|---|---|
| R1 decisions ≠ events | D1, D12 comments | pipeline: classification stage (layer-correct output) |
| R2 atomic decisions | D6, D7, D10 comments | pipeline: value gate + extraction prompt (atomicity instruction) |
| R3 process decisions → work item | D11 comment | pipeline: process-decision routing |
| R4 source provenance via ontology | C1 comment | pipeline: provenance contract (Source + extractedFrom) |
| R5 evidence preserved + linked | cost-tables comment | pipeline: CONNECTS gate + IMPL evidence edges |
| R6 pack-typed entities | entities comments | pipeline: entity classification (closed vocab) |
| R7 sources indexed as nodes | bibliographies comment | pipeline: Source indexing output |
| R8 stochastic, distributional tests | meta comment | evaluation: fuzzy matching + behavioral contract tests |

## Open design decisions (to resolve in the design pass)

1. Process-decision representation: drop-from-graph+route-to-work-item vs `process: true` tagged Point (R3).
2. Event vs decision classification threshold: what trigger cues + what confidence (R1).
3. Evidence claims: always Points, or source-cited claims only when they exceed a value floor (R5 vs extract-nothing).
4. Source indexing: automatic for any referenced artifact vs explicit (R7).
