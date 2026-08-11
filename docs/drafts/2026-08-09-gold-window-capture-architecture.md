---
title: "Gold Window Draft v2 — 2026-08-09 capture-architecture session"
type: gold-window
domain: operations
doc_status: draft
created: 2026-08-09
updated: 2026-08-09 (v2 — owner review incorporated)
ownedBy: epistemic-team
governingAgreement: "#753, #312"
extractor_version: "value@0.1.0-draft"
source: "pi session 019fe4bd-d84f-7eed-ae34-ccc6edaa30a2 (daniel-ospina, tortoise repo)"
window: "full session — infra verification → issues/pipeline → NAND fix → design exploration → capture architecture"
---

> **Gold window draft v2.** Owner review incorporated: decisions split where lumped,
> events separated from decisions, claims carry ontology-based source provenance,
> entities typed per the product-strategy pack, cost evidence kept, and a source-
> indexing gap surfaced. Still a DRAFT — review/edit continues.

# Gold Extraction — 2026-08-09 session

## A. Decisions (commitments made)

| # | Decision | Confidence | Why valuable | Provenance |
|---|---|---|---|---|
| D1 | **Regex text-first extraction is rejected** — value-first (the ontology defines what's valuable; the extractor may return nothing) is the direction | 0.95 | The core product pivot | supported by C1 (measured noise) |
| D2 | **NAND defaults to bidirectional** (logical mutual); an agent may declare `direction: unidirectional` for a directed attack | 0.95 | Final operator semantics ruling | NAND discussion; #807 |
| D3 | **LLM provider for hosted/managed extraction: DeepSeek V4 Flash** ($0.14/M in, $0.28/M out) | 0.9 | Cost decision; margin-safe | supported by C6 (model cost evidence) |
| D4 | **Capture architecture: local intelligence, remote graph** — extraction runs on the user's machine with THEIR key/model; hosted stores derived content only | 0.95 | The architecture ruling; resolves key-storage, privacy, break-even | capture architecture doc |
| D5a | **Pricing tiers are feature baselines** — not usage limits | 0.95 | Pricing model | pricing discussion |
| D5b | **Usage is metered separately, on top of tiers** | 0.95 | Pricing model | pricing discussion |
| D5c | **No capture caps** — more capture drives usage revenue + tier progression | 0.95 | Pricing model | pricing discussion |
| D6a | **Implicit-premise ("warrant") generation is deferred** from default extraction — inventing the unstated "because" is a guess, research-grade, no ground truth | 0.9 | Safety ruling (see C9) | break-even discussion |
| D6b | **Break-even on mining via the cost cut** — removing warrants from the default path makes capture cost (~$0.011) < write-op revenue ($0.02), no pricing change | 0.85 | Economics ruling | supported by C7/C8 |
| D7 | **Three-layer model**: Event (`agentSession`) = occurrence; Source = content (summary + story arc); Points = derived knowledge | 0.9 | Ontology model for capture | event-node research |
| D8 | **No per-turn nodes** — the raw conversation is ONE Source node, not per-message points | 0.95 | Node-economics ruling | turn-points discussion |
| D9a | **Raw-transcript upload is an explicit OPT-IN** (cross-device full recall), not default | 0.9 | Privacy posture | capture architecture |
| D9b | **Managed-key extraction is an explicit OPT-IN** (non-dev affordance, our Flash key, metered) | 0.9 | Privacy posture + product | capture architecture |
| D10 | **Evaluation gated on the gold set first** — validate the rubric on 2 windows before the 30-hour labeling investment | 0.9 | Process decision — **recorded on #753/#312** (work item), not just here | evaluation discussion |

> **Process-decision note (D10):** process decisions attach to the work item (issue/epic),
> not only to the extracted knowledge. D10 lives on #753.

## B. Events (occurred — not decisions)

| # | Event | Meaning |
|---|---|---|
| E1 | Repaired agent-infra domain links (premise-labs repinned 0.1.0, symlinks fixed; DMeer bootstrapped; pi skills re-synced) | Infra maintenance performed |
| E2 | #312 deltas shipped (SDK capture_session, extraction modes, cloud mode, dashboard child) | Issue progress — redundant with issue-completion events on #312 |

## C. Claims (asserted beliefs worth saving)

| # | Claim | Confidence | Source (ontology: Source node) | Link |
|---|---|---|---|---|
| C1 | Regex extraction measured at ~88% noise, ~160 nodes/turn, ~16k nodes/session vs the 25k solo cap | 0.95 | Source `spike/session_extraction_demo.py` + real session logs (sourceKind: analysis/measurement) | IMPL → D1 (evidence for the pivot) |
| C2 | The symmetric NAND potential behaved as an "agreement coupling" — in some configurations a strong attacker RAISED the target (0.524→0.614) | 0.9 | EP engine measurement (tests/measurement run) | → D2 |
| C3 | φ_directed = exp(w·ca)·φ_nand is NOT message-equivalent on the target — the exp(w·ca) factor sits inside the marginalization integral (up to 17.6% difference) | 0.95 | reviewer measurement | → D2 |
| C4 | Directed NAND (explicit unidirectional) gives the attacker no back-pressure — attacker immune, target drops (measured 0.856→0.856 / target down) | 0.95 | property tests | → D2 |
| C5 | Mutual-contradiction coupling is weak in the engine (+0.0024) — the contested detector (variance>0.04) cannot fire on genuine mutual contradictions | 0.9 | measured residual, #807 | gates the surfacing feature |
| C6 | GLM-4.6 ($0.60/M in, $2.20/M out) and Qwen3-Max ($1.20/M in, $6.00/M out) are 4–10× more expensive than DeepSeek Flash for extraction | 0.95 | vendor pricing (source: Z.ai / Alibaba pricing pages) | IMPL → D3 (evidence for Flash) |
| C7 | Value-first extraction collapses storage from ~$730/mo to ~$1/mo per heavy user (10M nodes → ~13k nodes/mo) | 0.9 | cost analysis | → D6b |
| C8 | Local extraction yields 85–96% gross margin on the default path (solo stops being a loss leader) | 0.85 | economics review | → D6b, D4 |
| C9 | The session quota counter (`_count_resource("sessions")`) counts ALL nodes — a ~25-node commit would 402 after ~40 captures | 0.95 | code-verified bug (quota.py) — **still open, must ship with the commit endpoint** | blocker |
| C10 | Extraction node amplification is the unit-economics killer — not the LLM cost (tiny at Flash rates) | 0.9 | cost analysis | → D6b |
| C11 | Full argument-tree reconstruction from dialogue is ~67 F1 — a research ceiling, not production | 0.9 | argument-mining research (DialAM-2024) | → D6a |
| C12 | LLM self-reported confidence is uncalibrated — use for gating, calibrate against review (ECE ≤0.10 target) | 0.85 | KG research | eval framework |
| C13 | Schema/ontology-first extraction is the field-recommended practice (KAG, Neo4j, OntoGPT/SPIRES) | 0.9 | KG research | → D1 |
| C14 | Implicit-premise (warrant) reconstruction is research-grade with no ground truth — auto-generated premises are guesses | 0.9 | argument-mining research | IMPL → D6a |
| C15 | The "we never see your conversations" privacy claim is NOT yet true in code — the merged cloud-mode posts raw conversation | 0.95 | code audit (launch blocker) | → D9a |
| C16 | Issue #131's raw-conversation cloud mode must be reworked to derived-writes before it becomes default-on | 0.9 | architecture decision | → D9a |
| C17 | Devs are the target customer and are finicky about models — BYOK is the right default for extraction | 0.85 | product reasoning | → D4 |
| C18 | **Research sources must be indexed as Source nodes in the graph** — the two research reports' full content isn't extracted, but the graph should index the sources (Source node + extractedFrom) so provenance is traceable. Currently a massive gap. | 0.95 | owner review — process gap | applies to all research-derived claims |

## D. Entities

### Product-strategy pack logic (useCase → feature → userJourney → workflow → requirement → architecture)
| Kind | Name | Notes |
|---|---|---|
| Object:product | Tortoise | the product |
| Object:useCase | agent memory / session capture | what the product serves |
| Object:feature | value-first extraction | the pivot feature (D1) — **typed as a feature, not an architecture decision** |
| Object:feature | capture architecture (local intelligence / remote graph) | the architecture ruling as a feature decision (D4) |
| Object:userJourney | dev onboarding: configure local extraction | the BYOK flow |
| Object:workflow | capture → local extraction → derived commit → graph | the pipeline |
| Object:requirement | "we never see your conversations" | the privacy requirement |
| Object:architecture | three-layer model (Event/Source/Points) | D7 |

### World entities
| Kind | Name |
|---|---|
| Object:repo | tortoise, agent-infra, premise-labs, DMeer |
| Object:model | DeepSeek V4 Flash (chosen), GLM-4.6, Qwen3-Max (rejected for cost) |
| Object:ontology | ONTOLOGY v3.4 |
| Object:event-kind | agentSession (capture) |
| Object:operator | NAND (bidirectional default / directed opt-in), IMPL |
| Object:issue | #753 (design review), #312 (hosted capture), #795/#807 (NAND), #131 (cloud mode) |
| Subject:team | epistemic-team |

## E. Relations (IMPL / NAND)

| From | To | Type | Why |
|---|---|---|---|
| C1 (regex 88% noise) | D1 (value-first) | IMPL | evidence for the pivot |
| C2 (NAND inversion) | D2 (bidirectional + directed opt-in) | IMPL | evidence for the ruling |
| C14 (warrants = guesses) | D6a (warrants deferred) | IMPL | evidence for deferral |
| C6 (model costs) | D3 (Flash) | IMPL | evidence for model choice |
| C7 + C8 (costs collapse, GM) | D6b (break-even) | IMPL | economics |
| D4 (local extraction) | C8 (85-96% GM) | IMPL | local → margin |
| C9 (sessions quota bug) | (commit endpoint) | IMPL | blocker |
| C15 (privacy claim false) | D9a (opt-in only) | IMPL | privacy gap → opt-in posture |
| C18 (sources not indexed) | (graph must index sources) | IMPL | gap → process fix |
| D2 (bidirectional default) | C2 (inversion finding) | NAND | the ruling overrides the earlier directed-default as the DEFAULT (directed retained as opt-in) |

## F. Explicitly NOT extracted (the value gate's "nothing")

| What | Why skipped |
|---|---|
| Verifier/sub-agent dispatch churn, gate pass/fail noise | Operational mechanics, no knowledge value |
| PR numbers, test counts, commit hashes | Transient state, not knowledge |
| The stash/rebase/worktree recovery incident | Process friction, no durable value |
| The detailed regex-pattern inventory | Implementation detail — the CONCLUSION (C1) is kept, not the mechanism |
| Most sub-agent orchestration details | Noise |

> **Kept as evidence (was "nothing", owner correction):** the GLM/Qwen token-cost tables → now C6
> (evidence attached to the DeepSeek decision). **Never discard evidence that supports a kept
> decision — connect it as a source/claim instead.**

## G. Review notes for the owner (v2 changes from your review)

1. **D1 → Event E1** (was a decision, is an event "repaired a thing")
2. **D6 split into 3** (tiers = features / usage metered / no caps)
3. **D7 split into 2** (warrant deferral / break-even mechanism) — with "warrant" explained in plain language
4. **D10 split into 2** (raw upload opt-in / managed key opt-in)
5. **D11 → process decision, recorded on #753/#312** (not just here)
6. **D12 → Event E2** (redundant with issue-completion events)
7. **C1 + all claims now carry ontology-based Source provenance** (sourceKind + link)
8. **Entities typed per the product-strategy pack** (useCase → feature → userJourney → workflow → requirement → architecture) — "value-first extraction" and "capture architecture" are features/decisions, not product objects
9. **C6 added**: the GLM/Qwen cost evidence kept + connected to D3
10. **C18 added**: the source-indexing gap — research sources must become Source nodes
