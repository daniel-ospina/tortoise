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

## R4 — Every claim and decision cites its source (provenance chain)

**Review input:** *"C1: what's the source? use our ontology"* + *"R4/5 refinement: maybe the source is just an agentSession, and the table is the claim, which then connects to another claim. The question is where did that data come from — let's try to always connect claims to the source (the source is when it's external to us, or can just be generated in an internal conversation — but even a decision needs a source)."*

**Requirement:** The provenance model is a **chain**, not a binary:

```
(Claim/Decision Point) -[:extractedFrom]-> (Source: agentSession | external artifact)
(Claim) -[:IMPL]-> (Decision)
(agentSession Source) -[:references]-> (external Source)     §3.4
```

- **The agentSession is itself a Source.** Every claim and every decision extracted from a conversation cites it via `extractedFrom → Source {sourceKind: "agentSession"}`. Even a decision needs a source — decisions are made in a conversation; the conversation is the source.
- **A stated fact is a CLAIM, not a Source.** The cost table *as asserted in the conversation* ("GLM is $0.60/M") is an asserted belief → a claim Point. Claims connect to claims (IMPL: evidence claim → decision claim). It is NOT raw evidence to bury in a Source.
- **Raw data is a SOURCE.** The artifact behind the fact (the pricing page, the paper) is a Source node. The session references it (`references` edge) or the claim's provenance resolves through it. External-to-us artifacts get their own Source nodes with `sourceKind` + `credibilityTier`.
- **The system must always answer: "where did that data come from?"** Every claim/decision has a resolvable `extractedFrom` → at minimum the agentSession Source, through to any external artifact.

## R5 — (merged into R4) — see above. Stated facts = claims; raw data = sources; provenance chain connects them.

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

## R9 — Mitigate relations: the relevance reduction on argument edges (owner correction)

**Review input:** *"Mitigations are one of the primary forms argument graphs get built. In
your analysis you're both under-identifying them and the document misses them."* +
the canonical example (X IMPL Option A "cheap"; Z MITIGATES the X→A connection "we can
raise the price"; Y IMPL Z "customers aren't price-sensitive").

**Requirement:** The extractor MUST emit **MITIGATES** relations — the ontology's
`mitigate_operator` semantics (how-to-use-tortoise skill table):

- **NAND ≠ MITIGATE.** NAND attacks the claim's truth (Correctness — "this claim is
  FALSE"); **MITIGATE reduces the EDGE's relevance** (Relevance — "this claim is TRUE but
  matters LESS than it seems", confidence reduction on the IMPL connection, bias 0.10-0.50).
- **MITIGATES targets an OPERATOR edge (the IMPL connection), not a point.** The premise
  stays true; its weight as an argument drops. (Maps to the research's undercut: attack
  the inference link, not the claim.)
- The extractor's relation stream is therefore **IMPL / NAND / MITIGATES** (the primary
  argument-graph operations), with MITIGATES emitted when the conversation tempers the
  relevance of an argument ("we can raise the price", "weaker than it sounds", "less
  important given X").
- Supporting evidence for a mitigation is a normal claim IMPL-ing the mitigation
  (the canonical example's Y → Z).
- **This is a PRIMARY extraction target, not an afterthought** — undercuts are how
  argument graphs get built; under-identifying them makes the mined graph structurally
  incomplete.

**Test case (canonical, from the owner):** X "it's cheap" IMPL Option A; Z "we can raise
the price" MITIGATES [X→A]; Y "customers aren't price-sensitive" IMPL Z. The extractor
must emit all three, with MITIGATES on the edge.

## Requirements traceability

| Req | Owner review source | Framing status |
|---|---|---|
| R1 decisions ≠ events | D1, D12 | framed |
| R2 atomic decisions | D6, D7, D10 | framed |
| R3 process decisions → work item | D11 | framed |
| R4/R5 provenance chain (claims cite sources; agentSession is a Source; stated facts = claims, raw data = sources) | C1 + R4/5 refinement | framed |
| R6 pack-typed entities + soft enforcement + pack mapping in scope | entities comments | framed (expanded) |
| R7 sources indexed as nodes | bibliographies comment | framed |
| R8 testability contract (2 layers) | meta comment + pass/fail question | framed |
| R9 mitigate relations (edge relevance; NAND≠MITIGATE) | owner correction: mitigations under-identified | framed |

## Open framing questions (for epic research/scoping, NOT to resolve here)

0. **R9 mitigation extraction** — the extraction cues for MITIGATES (relevance-tempering
   language vs NAND truth-attack language) and the bias/range assignment — research
   question for the plan stage (feeds the classification model).

1. R1: event vs decision classification thresholds and trigger cues — research question.
2. R3: process-decision representation — drop+route vs tagged — scoping decision.
3. R6: pack manifest schema for extractor-readable business logic — research + design (the mapping work).
4. R6: enforcement levels per error class (warn/retry/block) — scoping decision.
5. R8: per-requirement semantic thresholds and probe-set construction — scoping decision (feeds the gold set).
