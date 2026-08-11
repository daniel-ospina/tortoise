---
title: "Mining System Requirements v2 — from owner review (2026-08-09)"
type: requirements
domain: operations
doc_status: draft
created: 2026-08-09
updated: 2026-08-09 (v2 — R5 corrected, R6 expanded, R8 testability contract)
ownedBy: epistemic-team
governingAgreement: "#753, #312"
epic: value-first mining system (TBD — filed via epic pipeline)
---

> **These are REQUIREMENTS for the value-first mining system, derived from the product
> owner's review.** This is a FRAMING document — the basis for epic research, scoping,
> and planning. It deliberately does NOT specify solutions. The system is stochastic;
> requirements define the *behavior contract*; testability is defined in R8.

---

## R1 — Decisions are not Events

**Review input:** *"D1: it's not a decision. It's just an event 'repaired a thing'"* … *"D12: not a decision, just an event."*

**Requirement:** The extractor MUST distinguish **epistemic decisions** (commitments made) from **episodic events** (things that happened).

- Decision → Point (`decision`): durable, propagates confidence.
- Event → Event node (occurrence): recorded, linked to what it produced, not a belief.
- "did / repaired / fixed / shipped / completed" → event; "decided / will / should / chose" → decision. "We did X" is NOT a decision.
- Output is layer-correct from the start: a typed stream `{decisions[], events[], claims[], entities[]}`.

## R2 — One point, one decision (atomicity)

**Review input:** *"D6: more like 3 that shouldn't be lumped together."*

**Requirement:** Each decision Point is **atomic — a single commitment.** Compound statements split. The *what* and the *why-it-pays-for-itself* are different decisions when independently made. Splitting happens at classification time.

## R3 — Process decisions attach to the work item, not the graph

**Review input:** *"D11: attach to the right place (the epic/issue/work item)."*

**Requirement:** The extractor MUST distinguish **product-knowledge decisions** from **process/governance decisions**. Process decisions are NOT epistemic Points; they route to the work item (issue/epic) where an integration exists, or are dropped with a logged reason.

## R4 — Evidence is a SOURCE, not a Point; every belief cites its source

**Review input:** *"C1: what's the source? use our ontology"* + *"R5 correction: evidence (the raw data that supports a belief) is most likely a SOURCE, and we have a specific setup for that in our ontology."*

**Requirement:** Evidence — the raw data supporting a belief — lives in **Source nodes**, linked by the ontology's provenance structure: `(Point)-[:extractedFrom]->(Source)` (Source carries `sourceKind`, `credibilityTier`). **Evidence is NEVER minted as a Point.** The epistemic layer holds beliefs; Sources hold the raw data behind them.

- Every extracted claim/decision MUST cite the Source it came from (reference + span). `extractedFrom` must always resolve to a Source node.
- "Keeping the cost tables connected to the Flash decision" = **index the cost data as a Source and link the decision to it** — not create a claim Point for the raw numbers.
- Consequence: R4 and R5 are the SAME requirement (the reviewer flagged the conflation risk). The rule: **never pollute the epistemic layer with raw evidence-as-Points; evidence belongs in Sources.**

## R5 — (merged into R4) — see above. Evidence = Source, linked via ontology provenance.

## R6 — Entities are typed by the expansion packs' business logic (read + softly enforced); pack mapping is in scope

**Review input:** *"Object:product are not identifying the product… 'value-first extraction' no idea what that is"* + *"missing the product-strategy expansion pack logic (usecase-feature-user journey-workflow-requirement-architecture)"* + *"we need the mechanism to READ (and enforce — not 100%, too hard a gate frustrates agents, but quite strongly) the expansion packs' business logic; the packs might not currently have that logic properly mapped, so we need to figure out how it should be mapped — that becomes part of the scope."*

**Requirement:**

- **Read:** Entity/kind classification reads the expansion packs' business logic (the pack manifests' kind chain — e.g., product-strategy: useCase → feature → userJourney → workflow → requirement → architecture). The packs define what kinds exist and their relationships; the extractor classifies INTO that vocabulary. Never free-form kinds, never LLM-minted kinds.
- **Enforce, softly:** enforcement is strong but NOT a 100% hard gate. Too-hard gates frustrate agents. Near-misses warn/flag/retry; only truly out-of-schema hazards block. The exact enforcement levels (warn vs retry vs block, and the thresholds) are a scoping decision.
- **Pack mapping is in scope:** the expansion packs may NOT currently encode their business logic in a form the extractor can read for this purpose. Figuring out how packs should be mapped (the manifest schema, kind-relation representation, per-pack extraction-active vocabularies) is PART of the epic scope — research + design + pack-schema work, not assumed solved.

## R7 — Sources are indexed as Source nodes, always

**Review input:** *"what happened with adding sources to the graph… at least indexing the source so we know where data came from — currently a massive gap."*

**Requirement:** Research reports, papers, and source artifacts MUST be indexed as **Source nodes** — even when full content is not extracted. The graph must never contain a claim whose source is untraceable. Source indexing is a first-class mining output.

## R8 — The system is stochastic; testability is a two-layer contract

**Review input:** *"editing the doc is pointless, the system is non-deterministic"* + *"how can behavioral contract tests determine a pass/fail?"*

**Requirement:** Testing has two layers with different determinism:

- **Layer 1 — CONTRACT (deterministic, binary pass/fail):** the output must conform to the typed-stream schema (`{decisions[], events[], claims[], entities[]}`); every item carries required fields (kind ∈ closed vocabulary, source_ref present, atomic single commitment). **Schema validation is deterministic** — the LLM either emitted a valid-shaped stream or it didn't. Non-conforming output → retry once → fail with reason. This is a genuine pass/fail gate.
- **Layer 2 — SEMANTIC (statistical, threshold pass/fail):** semantic correctness (is "we fixed X" classified as event? is the split atomic? is the right kind chosen?) is measured as a RATE over N trials/windows and compared to a threshold (e.g., layer-correct rate ≥0.90 on a probe set, measured over multiple runs). Pass/fail = threshold on a measured rate — honest about stochasticity, never a single-run assertion.
- The two layers are explicit: contract gates are CI blockers (shape), semantic thresholds are eval gates (quality). Both defined per-requirement in the epic's scoping stage.
- Reliability levers that raise determinism (few-shot exemplars, structured-output constraints, consistency checks) are DESIGN considerations for the plan — not requirements.

---

## Requirements traceability

| Req | Owner review source | Framing status |
|---|---|---|
| R1 decisions ≠ events | D1, D12 | framed |
| R2 atomic decisions | D6, D7, D10 | framed |
| R3 process decisions → work item | D11 | framed |
| R4 evidence = Source, provenance | C1 + R5 correction | framed (R5 merged) |
| R6 pack-typed entities + soft enforcement + pack mapping in scope | entities comments | framed (expanded) |
| R7 sources indexed as nodes | bibliographies comment | framed |
| R8 testability contract (2 layers) | meta comment + pass/fail question | framed |

## Open framing questions (for epic research/scoping, NOT to resolve here)

1. R1: event vs decision classification thresholds and trigger cues — research question.
2. R3: process-decision representation — drop+route vs tagged — scoping decision.
3. R6: pack manifest schema for extractor-readable business logic — research + design (the mapping work).
4. R6: enforcement levels per error class (warn/retry/block) — scoping decision.
5. R8: per-requirement semantic thresholds and probe-set construction — scoping decision (feeds the gold set).
