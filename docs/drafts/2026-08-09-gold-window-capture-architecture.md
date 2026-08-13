---
title: "Gold Window Draft — 2026-08-09 capture-architecture session"
type: operations
domain: operations
doc_status: draft
created: 2026-08-09
ownedBy: epistemic-team
governingAgreement: "#753, #312"
extractor_version: "value@0.1.0-draft"  # gold standard for the value-first rubric
source: "pi session 019fe4bd-d84f-7eed-ae34-ccc6edaa30a2 (daniel-ospina, tortoise repo)"
window: "full session — infra verification → issues/pipeline → NAND fix → design exploration → capture architecture"
---

> **This is a DRAFT gold window for the extraction rubric.** It answers: *what SHOULD a
> value-first extractor save from this conversation?* Review and edit — mark anything
> I got wrong, missed, or over-included. The "nothing" sections are as important as the
> kept items.

# Gold Extraction — 2026-08-09 session

## A. Decisions (the epistemic gold — commitments made)

| # | Decision | Confidence | Why it's valuable | Provenance |
|---|---|---|---|---|
| D1 | **Agent-infra domains verified + fixed**: premise-labs repinned to 0.1.0 with repaired symlinks; DMeer bootstrapped as a linked domain; pi skills copy re-synced (3 stale files) | 0.95 | Infra state change that unblocks all repos | early session (verify domains) |
| D2 | **Regex text-first extraction is rejected** — value-first (ontology defines value; extractor may return nothing) is the direction | 0.95 | The core product pivot; everything downstream | regex-88%-noise finding |
| D3 | **NAND defaults to bidirectional** (logical mutual); an agent may declare `direction: unidirectional` for a directed attack | 0.95 | Final operator semantics ruling (reverted the earlier directed-default) | NAND discussion |
| D4 | **LLM provider for hosted/managed extraction: DeepSeek V4 Flash** ($0.14/M in, $0.28/M out) | 0.9 | Cost decision; 4-10x cheaper than GLM/Qwen, margin-safe | cost analysis |
| D5 | **Capture architecture: local intelligence, remote graph** — extraction runs on the user's machine with THEIR key/model; hosted stores derived content only | 0.95 | The architecture ruling; resolves key-storage, privacy, break-even | capture architecture |
| D6 | **No capture caps** — tiers = feature baselines; usage metered separately; more capture = more usage revenue + tier progression | 0.95 | Pricing model ruling | pricing discussion |
| D7 | **Break-even on mining via cost cut: warrants/implicit-premise generation deferred** from the default path | 0.85 | Cost + safety ruling (warrants = invented reasoning, research-grade) | break-even discussion |
| D8 | **Three-layer model**: Event (`agentSession`) = occurrence; Source = content (summary + story arc); Points = derived knowledge | 0.9 | Ontology model for capture | event-node research |
| D9 | **No per-turn nodes** — the raw conversation is ONE Source node, not per-message points | 0.95 | Node-economics ruling | turn-points discussion |
| D10 | **Raw-transcript upload + managed-key extraction are explicit OPT-INS**, not defaults | 0.9 | Privacy posture | capture architecture |
| D11 | **Evaluation gated on the gold set first** — validate the rubric on 2 windows before 30 hours of labeling | 0.9 | Process decision | evaluation discussion |
| D12 | **#312 deltas shipped**: SDK capture_session (4+5), extraction modes (1), cloud mode (2, agent-infra #131), dashboard child (3) | 0.9 | Product progress state | issue execution |

## B. Claims (asserted beliefs worth saving)

| # | Claim | Confidence | Provenance |
|---|---|---|---|
| C1 | Regex extraction measured at ~88% noise, ~160 nodes/turn, ~16k nodes/session vs the 25k solo cap | 0.95 | measured via real code on real sessions |
| C2 | The symmetric NAND potential behaved as an "agreement coupling" — in some configurations a strong attacker RAISED the target (0.524→0.614) | 0.9 | measured on the real engine (later mitigated by the bidirectional-default/directed-opt-in ruling) |
| C3 | φ_directed = exp(w·ca)·φ_nand is NOT message-equivalent on the target — the exp(w·ca) factor sits inside the marginalization integral (up to 17.6% difference) | 0.95 | reviewer-measured; corrected a false comment |
| C4 | Directed NAND (explicit unidirectional) gives the attacker no back-pressure — attacker immune, target drops (measured 0.856→0.856 / target down) | 0.95 | property tests |
| C5 | Mutual-contradiction coupling is weak in the engine (+0.0024) — the contested detector (variance>0.04) cannot fire on genuine mutual contradictions | 0.9 | measured residual, documented in #807 |
| C6 | Value-first extraction collapses storage from ~$730/mo to ~$1/mo per heavy user (10M nodes → ~13k nodes/mo) | 0.9 | cost analysis |
| C7 | Local extraction yields 85-96% gross margin on the default path (solo stops being a loss leader) | 0.85 | economics review |
| C8 | The session quota counter (`_count_resource("sessions")`) counts ALL nodes — a ~25-node commit would 402 after ~40 captures | 0.95 | code-verified bug, still open |
| C9 | Extraction node amplification is the unit-economics killer — not the LLM cost (which is tiny at Flash rates) | 0.9 | cost analysis |
| C10 | Full argument-tree reconstruction from dialogue is ~67 F1 — a research ceiling, not production | 0.9 | argument-mining research |
| C11 | LLM self-reported confidence is uncalibrated — use for gating, calibrate against review (ECE ≤0.10 target) | 0.85 | KG research |
| C12 | Schema/ontology-first extraction is the field-recommended practice (KAG, Neo4j, OntoGPT/SPIRES) | 0.9 | KG research |
| C13 | Implicit-premise (warrant) reconstruction is research-grade with no ground truth — auto-generated premises are guesses | 0.9 | argument-mining research |
| C14 | The "we never see your conversations" privacy claim is NOT yet true in code — the merged cloud-mode posts raw conversation | 0.95 | code audit (launch blocker) |
| C15 | Issue #131's raw-conversation cloud mode must be reworked to derived-writes before it becomes default-on | 0.9 | architecture decision |
| C16 | Devs are the target customer and are finicky about models — BYOK is the right default for extraction | 0.85 | product reasoning |

## C. Entities

| Kind | Name | Notes |
|---|---|---|
| Object:repo | tortoise | the product repo |
| Object:repo | agent-infra | shared agent infrastructure |
| Object:repo | premise-labs | linked consumer |
| Object:repo | DMeer | bootstrapped as linked domain |
| Object:model | DeepSeek V4 Flash | extraction provider (D4) |
| Object:model | GLM-4.6 / Qwen3-Max | evaluated, rejected (cost) |
| Object:product | value-first extraction | the direction (D2) |
| Object:product | capture architecture (local/remote) | D5 |
| Object:operator | NAND | bidirectional default, directed opt-in (D3) |
| Object:operator | IMPL | support |
| Object:ontology | ONTOLOGY v3.4 | canonical |
| Object:event-kind | agentSession | capture event kind (D8) |
| Object:issue | #753 | design review gate |
| Object:issue | #312 | hosted capture |
| Object:issue | #795 / #807 | NAND fixes |
| Subject:team | epistemic-team | owner |

## D. Relations (IMPL / NAND)

| From | To | Type | Why |
|---|---|---|---|
| C1 (regex 88% noise) | D2 (value-first) | IMPL | evidence for the pivot |
| C2 (NAND inversion measured) | D3 (bidirectional default + directed opt-in) | IMPL | evidence for the ruling |
| C13 (warrants = guesses) | D7 (warrants deferred) | IMPL | evidence for deferral |
| D5 (local extraction) | C7 (85-96% GM) | IMPL | local → margin |
| C6 (value-first volumes) | D9 (no per-turn nodes) | IMPL | volumes make caps safe |
| C8 (sessions quota bug) | (open fix) | IMPL | blocker for the commit endpoint |
| C14 (privacy claim false in code) | D10 (opt-ins only) | IMPL | privacy gap → opt-in posture |
| D7 (warrants deferred) | C13 (research-grade) | IMPL | deferral justified |
| C5 (mutual contradiction weak) | (surfacing feature gated) | IMPL | flagship feature can't fire yet |
| D3 (bidirectional default) | C2 (inversion) | NAND | the ruling overrides the earlier directed-default finding as the DEFAULT (directed retained as opt-in) |

## E. Explicitly NOT extracted (the value gate's "nothing")

| What | Why skipped |
|---|---|
| Verifier/sub-agent dispatch churn, gate pass/fail noise | Operational mechanics, no knowledge value |
| PR numbers, test counts, commit hashes | Transient state, not knowledge |
| The stash/rebase/worktree recovery incident | Process friction, no durable value |
| The detailed regex-pattern inventory (specific patterns) | Implementation detail — the CONCLUSION (88% noise) is kept, not the mechanism |
| Specific token-cost tables for GLM/Qwen | Decision-relevant only as the comparison; the decision (D4) is kept |
| The two research reports' full bibliographies | Reference material, not graph knowledge |
| Most sub-agent orchestration details | Noise |

## F. Review notes for the owner

1. **Edit freely** — add/remove decisions, claims, entities, relations; adjust confidences.
2. **The "nothing" list matters** — does it match your sense of what's worth discarding?
3. **This becomes gold window #1** for the extraction rubric (tests/extraction_eval/gold/) once you approve it.
4. **The judge-model comparison** (the 2-window validation) can then run on this + one more.
