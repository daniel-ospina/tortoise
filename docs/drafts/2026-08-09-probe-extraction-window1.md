---
title: "Probe Extraction — Window #1 (system view, independent)"
type: probe-extraction
domain: operations
doc_status: probe
created: 2026-08-09
ownedBy: epistemic-team
governingAgreement: "#753, #312"
extractor_version: "value@0.1.0-draft (probe run)"
source: "pi session 019fe4bd-d84f-7eed-ae34-ccc6edaa30a2 (daniel-ospina, tortoise repo)"
window: "full session — infra verification → issues/pipeline → NAND fix → design exploration → capture architecture"
purpose: "EXTRACTOR PROBE — what the R1-R8-driven system emits, produced independently of the gold. Comparison target: the gold window (sections A-G of 2026-08-09-gold-window-capture-architecture.md)."
---

> **Probe extraction.** Produced by applying the mining-system requirements (R1-R8)
> to the 2026-08-09 session. Not written to match the gold; where the rubric forces
> a different output, the divergence is deliberate and noted in §8. Schema conforms
> to R8 Layer-1: typed stream, closed kind vocabulary (near-misses flagged, never
> LLM-minted kinds), every item carries `source_ref` (R4), atomic content (R2).
> Confidence rubric: 0.9+ explicit unambiguous; 0.7-0.9 clear, hedged/second-hand;
> 0.5-0.7 implied/inferred; <0.5 speculative (tagged only).

---

## 1. decisions[] — commitments made (commissives), atomic

| # | Decision (single commitment) | Conf | Why valuable | source_ref |
| --- | --- | --- | --- | --- |
| D1 | Regex/text-first extraction is **rejected** as the extraction direction; **value-first** (ontology defines value; extract-nothing is first-class success) becomes the direction. Regex stays only as the always-works fallback baseline. | 0.95 | Core product pivot — defines the mining system's entire shape | S0 (session); S1 (pipeline spec, design principles 1-3) |
| D2 | **NAND defaults to bidirectional** (mutual contradiction); an agent may declare `direction: unidirectional` for a directed attack | 0.95 | Final operator-semantics ruling; determines belief-propagation behavior on attack edges | S0 (NAND discussion); #807 |
| D3 | **DeepSeek V4 Flash** ($0.14/M in, $0.28/M out) is the LLM provider for the hosted/managed extraction option | 0.9 | Cost decision; margin-safe vs 4-10x pricier alternatives | S2 (Decisions recorded); S9 (DeepSeek pricing) |
| D4 | **Capture architecture: local intelligence, remote graph** — extraction runs on the user's machine with THEIR key/model; hosted stores derived content only | 0.95 | Architecture ruling; resolves key-storage, privacy, and break-even in one move | S2 (product-owner decision banner) |
| D5a | **Pricing tiers are feature baselines**, not usage limits | 0.95 | Pricing model — what tiers mean | S5 (owner correction banner); S4 |
| D5b | **Usage is metered separately, on top of tiers** | 0.95 | Pricing model — revenue mechanism | S5; S4 |
| D5c | **No capture caps** — more capture drives usage revenue + tier progression | 0.95 | Pricing model — reverses the F3 caps proposal | S5 (owner correction, "F3 REJECTED"); S4 |
| D6 | **Implicit-premise ("warrant") generation is deferred from the default extraction path** — at most a user-opted toggle, always tagged derived/low-confidence | 0.9 | Safety ruling: inventing the unstated "because" is a guess, research-grade, no ground truth | S1 (S4 stage, UPDATE banner); S2 (trade-offs) |
| D7 | **Three-layer capture model**: Event (`agentSession` occurrence) → Source (content: summary + story arc) → Points (derived knowledge) | 0.9 | Ontology model for capture; occurrence/content/epistemic separation | S3 (three layers, "validated against KG best practice") |
| D8 | **No per-turn nodes** — the raw conversation is ONE Source node, not per-message points | 0.95 | Node-economics ruling; kills the amplification failure mode at the substrate | S3 (owner ruling, "RESOLVED"); S4 (F1) |
| D9a | **Raw-transcript upload is an explicit OPT-IN**, not the default | 0.9 | Privacy posture; makes "conversation never leaves" the default guarantee | S2 (opt-in paths table) |
| D9b | **Managed-key extraction is an explicit OPT-IN** (non-dev affordance, our Flash key, metered) | 0.9 | Privacy posture + product affordance for non-devs | S2 (opt-in paths table) |

> **R3 routing note:** the session's "validate the rubric on 2 windows before the
> 30-hour labeling investment" is a **process/governance decision** — routed to
> #753/#312 (evaluation plan), NOT emitted as a graph point. See §7 item 1.

---

## 2. events[] — occurred (not decisions)

| # | Event | Meaning | source_ref |
| --- | --- | --- | --- |
| E1 | agent-infra domain links repaired — premise-labs dependency repinned to 0.1.0, symlinks fixed | Infra maintenance performed | S0 (session start segment) |
| E2 | DMeer bootstrapped | Infra maintenance performed | S0 |
| E3 | pi skills re-synced | Infra maintenance performed | S0 |
| E4 | #312 deltas shipped (SDK `capture_session`, extraction modes, cloud mode, dashboard child) | Issue progress; redundant with issue-completion events on #312 | S0; #312 |
| E5 | NAND semantics measurement runs executed (EP-engine property tests + reviewer measurement) | Occurrence that produced evidence claims C2-C5 | S0 (NAND fix segment); #807 |

---

## 3. claims[] — asserted beliefs (stative/measured), with provenance

| # | Claim | Conf | Source (Source node) | Links |
| --- | --- | --- | --- | --- |
| C1 | Regex extraction measured **~88% noise, ~160 nodes/turn, ~16k nodes/session** (median ~98-turn session) vs the 25k solo cap | 0.95 | S15a `spike/session_extraction_demo.py` + real session logs (sourceKind: analysis/measurement) | IMPL → D1 |
| C2 | Symmetric NAND behaved as an **"agreement coupling"** — in some configurations a strong attacker RAISED the target (0.524→0.614) | 0.9 | S15b EP-engine measurement run | NAND → D2 (attacks the symmetric default; directed opt-in is the mitigation) |
| C3 | φ_directed = exp(w·ca)·φ_nand is **NOT message-equivalent** on the target — the exp(w·ca) factor sits inside the marginalization integral (up to 17.6% difference) | 0.95 | S15c reviewer measurement | IMPL → D2 |
| C4 | Directed NAND gives the attacker **no back-pressure** — attacker immune, target drops (measured 0.856→0.856 / target down) | 0.95 | S15d property tests | IMPL → D2 |
| C5 | Mutual-contradiction coupling is **weak** in the engine (+0.0024) — the contested detector (variance>0.04) cannot fire on genuine mutual contradictions | 0.9 | S15e measured residual, #807 | gates the contradiction-surfacing feature (IMPL → surfacing work item) |
| C6 | GLM-4.6 ($0.60/M in, $2.20/M out) and Qwen3-Max ($1.20/M in, $6.00/M out) are **4-10× more expensive** than DeepSeek Flash for extraction | 0.95 | S10 (Z.ai pricing page), S11 (Alibaba Qwen pricing page) — external Sources | IMPL → D3 |
| C7 | Value-first extraction collapses storage from **~$730/mo to ~$1/mo per heavy user** (10M nodes → ~13k nodes/mo) | 0.9 | S4 cost analysis | IMPL → B1, D5c |
| C8 | Local extraction yields **85-96% gross margin** on the default path — solo stops being a loss leader | 0.85 | S5 economics review | IMPL → D4 |
| C9 | The session quota counter (`_count_resource("sessions")`) **counts ALL nodes** — a ~25-node commit would 402 after ~40 captures. Still open; must ship with the commit endpoint | 0.95 | S15f code-verified bug (quota.py) | IMPL → (commit-endpoint work item) — blocker |
| C10 | Extraction **node amplification is the unit-economics killer** — not LLM cost (tiny at Flash rates) | 0.9 | S4/S5 cost analysis | IMPL → D1 |
| C11 | Full argument-tree reconstruction from dialogue is **~67 F1** — a research ceiling, not production | 0.9 | S12 argument-mining research (DialAM-2024) | IMPL → D6 |
| C12 | LLM self-reported confidence is **uncalibrated** (extraction precision ~59-73% while models emit mean ~0.8) — calibrate; ECE ≤0.10 target | 0.85 | S14 confidence-calibration research | conditions eval framework |
| C13 | **Schema/ontology-first extraction is the field-recommended practice** (KAG, Neo4j, OntoGPT/SPIRES) | 0.9 | S13 KG research | IMPL → D1 |
| C14 | Implicit-premise (warrant) reconstruction is **research-grade with no ground truth** — auto-generated premises are guesses | 0.9 | S12 argument-mining research | IMPL → D6 |
| C15 | The **"we never see your conversations" privacy claim is NOT yet true in code** — the merged cloud-mode (#131) posts raw conversation | 0.95 | S15g code audit (launch blocker) | IMPL → D9a |
| C16 | Issue #131's raw-conversation cloud mode **must be reworked to derived-writes** before it becomes default-on | 0.9 | S2 architecture analysis | IMPL → D9a |
| C17 | Devs are the target customer and are **finicky about models** — BYOK is the right default for extraction | 0.85 | S2 product reasoning | IMPL → D4 |
| C18 | **Research sources behind design claims must be indexed as Source nodes** (provenance traceability) even when full content is not extracted — currently a gap in the graph | 0.8 | S0 (provenance discussion); S13/S12/S14 referenced-but-not-indexed reports | IMPL → (source-indexing work item, R7) |
| B1 | Removing warrants from the default path makes capture cost (**~$0.011**) **< write-op revenue ($0.02)** — break-even without pricing change | 0.85 | S4/S5 economics analysis | D6 IMPL → B1 |
| B2 | Per-session LLM cost **scales with window count**: median $0.028-0.032; p90 $0.063; p95 $0.092 (2-3× median) — the fixed 5-window table understates p90+ | 0.9 | S5 computed cost model († percentiles flagged for telemetry) | IMPL → D6 (warrant discipline is the cost lever) |
| B3 | $5/10k write-ops ≈ **$0.0275/capture ≈ marginal LLM cost** (~90% coverage) — honest overage unit; a $5/1k-captures unit would be 1/6 of marginal cost (margin hole) | 0.85 | S16 pricing.json + S5 cost model | IMPL → D5b |

---

## 4. entities[] — typed by pack vocab (R6: read + soft-enforce; no minted kinds)

Pack vocab read from `packs/*/manifest.yaml` + ONTOLOGY §5 (core). Near-misses → warn + hold/pack-proposal (soft enforcement, not block).

### Product-strategy pack (objectKinds: product, feature, customer, competitor, customerSegment, market)

| Kind | Name | Flag |
| --- | --- | --- |
| product-strategy:product | Tortoise | ✓ |
| product-strategy:feature | value-first extraction (D1) | ✓ (the pivot is a feature, not a product object) |
| product-strategy:feature | capture architecture: local intelligence / remote graph (D4) | ✓ |
| product-strategy:feature | managed-key extraction affordance (D9b) | ✓ |
| product-strategy:feature | raw-transcript upload affordance (D9a) | ✓ |
| product-strategy:useCase | agent memory / session capture | ⚠ near-miss: `useCase` is a pointKind in manifest, used here as entity kind — pack-mapping item (R6 in scope) |
| product-strategy:userJourney | dev onboarding: configure local extraction (BYOK flow) | ⚠ near-miss: `userJourney` is a pointKind; kind-role mismatch — pack-mapping item |

### Core + dev pack (ONTOLOGY §5; dev manifest)

| Kind | Name | Flag |
| --- | --- | --- |
| core:workflow | capture → local extraction → derived commit → graph (pipeline) | ✓ (core objectKind) |
| dev:requirement | "we never see your conversations" (privacy guarantee) | ⚠ near-miss: `requirement` is a dev pointKind, not objectKind |
| core:other → pack proposal | three-layer model (D7) | ⚠ `architecture` NOT in vocab; nearest core:other; propose `architecture` kind via pack-mapping work |
| core:other → pack proposal | DeepSeek V4 Flash (chosen), GLM-4.6, Qwen3-Max (rejected on cost) | ⚠ `model` NOT in vocab; nearest dev:software / core:other; propose `model` kind |
| dev:software | tortoise repo, agent-infra repo, premise-labs repo, DMeer | ⚠ near-miss: `repo` not a kind; dev:software used |
| dev:issue | #753 (design review), #312 (hosted capture), #795/#807 (NAND), #131 (cloud mode) | ✓ |
| core subjectKind:team | epistemic-team | ✓ |
| core:other → pack proposal | NAND (bidirectional default / directed opt-in), IMPL | ⚠ `operator` NOT in vocab; propose `operator` kind |
| core:standard | ONTOLOGY v3.4 | ⚠ near-miss: ontology → core:standard |
| eventKind (proposed) | agentSession | ⚠ not in ONTOLOGY §5 eventKind list (sessionCaptured is); in-session extension — flag for ontology amendment |

---

## 5. relations[] — IMPL / NAND between kept points

Convention: IMPL = support (evidence→decision; decision→entailed consequence). Quote-backed in S0.

| From | To | Type | Why |
| --- | --- | --- | --- |
| C1 (regex 88% noise) | D1 (value-first) | IMPL | measured evidence for the pivot |
| C10 (amplifier is the killer) | D1 | IMPL | cost analysis points at the amplifier → value-first fixes by construction |
| C13 (ontology-first is field practice) | D1 | IMPL | external validation of the direction |
| C3 (φ_directed not message-equivalent) | D2 (bidirectional default) | IMPL | directed formula is broken → symmetric default |
| C4 (directed = no back-pressure) | D2 | IMPL | directed is exploitable → bidirectional default, directed only as declared opt-in |
| C2 (agreement-coupling inversion) | D2 | **NAND** | inversion finding ATTACKS the symmetric default; ruling kept bidirectional anyway, mitigation = directed opt-in |
| C6 (model costs 4-10×) | D3 (Flash) | IMPL | evidence for model choice |
| C8 (85-96% GM local) | D4 (local intelligence) | IMPL | economics support the architecture ruling |
| C17 (devs finicky → BYOK) | D4 | IMPL | customer reasoning supports the ruling |
| C15 (privacy claim false in code) | D9a (raw upload opt-in) | IMPL | privacy gap → opt-in posture |
| C16 (#131 must become derived-writes) | D9a | IMPL | consequence of the opt-in posture |
| C14 (warrants = guesses) | D6 (warrant deferral) | IMPL | safety evidence |
| C11 (67 F1 research ceiling) | D6 | IMPL | capability evidence |
| B2 (p90 cost 2-3× median) | D6 | IMPL | cost pressure → warrant discipline (1 typical / 3 hard) |
| D6 (warrant deferral) | B1 (break-even) | IMPL | the commitment entails the economics (cost cut → break-even, no pricing change) |
| C7 (storage $730→$1) | B1 | IMPL | storage collapse supports break-even |
| C7 | D5c (no capture caps) | IMPL | collapsed volume makes no-caps affordable |
| B3 (write-ops unit honest) | D5b (usage metered) | IMPL | overage unit integrity supports metered-usage model |
| C9 (quota counter bug) | (commit endpoint work item) | IMPL | blocker |
| C18 (sources not indexed) | (source-indexing work item) | IMPL | R7 gap → process fix |

---

## 6. sources[] — indexed as Source nodes (R7)

| ID | Source | sourceKind | Credibility tier |
| --- | --- | --- | --- |
| S0 | agentSession `019fe4bd-d84f-7eed-ae34-ccc6edaa30a2` (the conversation) | agentSession | n/a (primary) |
| S1 | docs/drafts/2026-08-09-value-first-extraction-pipeline.md | architectureDoc | internal |
| S2 | docs/drafts/2026-08-09-capture-architecture-local-intelligence.md | architectureDoc | internal |
| S3 | docs/drafts/2026-08-09-state-epistemic-separation.md | architectureDoc | internal |
| S4 | docs/drafts/2026-08-09-mvp-scope-economics.md | planDoc (exploration) | internal |
| S5 | docs/drafts/2026-08-09-economics-guardrails.md | experimentResults | internal |
| S6 | docs/drafts/2026-08-09-extraction-quality-evaluation.md | experimentResults | internal |
| S7 | docs/drafts/2026-08-09-product-success-evaluation.md | strategyDoc | internal |
| S9 | DeepSeek pricing page (API rates) | evidenceLog (external web) | vendor-primary |
| S10 | Z.ai pricing page (GLM-4.6) | evidenceLog (external web) | vendor-primary |
| S11 | Alibaba Cloud Qwen pricing page (Qwen3-Max) | evidenceLog (external web) | vendor-primary |
| S12 | DialAM-2024 argument-mining research | research | peer-reviewed |
| S13 | KG best-practice research (KAG, Neo4j, OntoGPT/SPIRES, GAF/SEM, MeetGraph, context graphs) | research | secondary |
| S14 | Confidence-calibration research reports | research | secondary |
| S15a-g | Code/measurement artifacts: spike/session_extraction_demo.py; EP-engine measurement run; reviewer measurement; property tests; measured residual (#807); quota.py; hosted_api.py audit | code / analysis / measurement | internal primary |
| S16 | product/pricing.json | evidenceLog | internal primary |

---

## 7. nothing[] — explicitly rejected, with logged reasons

| # | What | Why rejected (logged) |
| --- | --- | --- |
| 1 | **Process decision: "validate the rubric on 2 windows before the 30-hour labeling investment"** | **R3** — process/governance decision, NOT an epistemic point. Routed to the work item: #753/#312 evaluation plan. Logged reason: sequencing/QA commitment about the labeling effort, not product knowledge. |
| 2 | F3 caps analysis (pro 2,500→1,000/mo; team 15,000→5,000/mo) | Superseded: owner ruling D5c rejected capture caps wholesale. Analysis of a rejected proposal; load-bearing part (cost side tiny) kept as C7/C10. |
| 3 | Stash/rebase/worktree recovery incident | Process friction, no durable knowledge value. |
| 4 | Verifier/sub-agent dispatch churn, gate pass/fail noise | Operational mechanics. |
| 5 | PR numbers, test counts, commit hashes | Transient state, not knowledge. |
| 6 | Regex-pattern inventory details | Implementation detail — conclusion kept as C1, mechanism not kept. |
| 7 | Evaluation-spec thresholds (A1-A22, κ targets, SLO tables, keep-ratio bands) | Draft spec proposals pending #753 review — not commitments. Artifact S6 is the indexed Source; re-extract when the gate approves. |
| 8 | MVP-scope enumeration (2 point kinds, 6 edge types, 12 instruments, guardrail values) | Exploration draft explicitly marked "do NOT implement from this" — proposals, not commitments. Artifact S4 indexed. |
| 9 | Doc-creation of the 8 drafts | Artifacts self-index as documentKind Sources (S1-S7); creation is operational (event-log `documentCreated`), not knowledge. |
| 10 | "Extract-nothing has no established benchmark anywhere" | Supporting rationale inside an artifact (S6), not durable product knowledge about tracked objects. |
| 11 | Solo-tier loss-leader economics ($12.29 COGS vs $9) + alarm thresholds | Economic guardrail detail owned by S5; not a commitment made in-session (exploration doc). |
| 12 | Latency SLOs (capture p95 ≤1.5s, extraction ≤5min) | Spec proposal pending review (see 7). |

---

## 8. Notable independent choices (system-vs-gold divergence notes — for the comparison)

Where the rubric forced a different output than the gold window:

1. **R3 (process decisions):** gold lists "validate on 2 windows first" as decision D10 (with a process note); the system emits NO decision point — it routes to #753/#312 and logs the reason (§7.1). This is the R3 contract behavior.
2. **R1 (decisions vs claims):** gold D6b ("break-even via the cost cut, no pricing change") — the system finds no pricing *commitment* in the session ("Decide with real telemetry" is the actual stance) and emits the economics as claims B1/B2/B3 with IMPL edges. The warrant deferral (D6) is the only commitment.
3. **R6 (pack vocab, no minted kinds):** gold's entity table uses out-of-vocab kinds (`Object:model`, `Object:repo`, `Object:operator`, `Object:architecture`, `Object:ontology`). The system refuses to mint kinds: nearest in-vocab kind + near-miss flag + pack-proposal hold (§4). `model`/`architecture`/`operator` become explicit pack-mapping items.
4. **Events layer:** gold E1 groups all infra repairs; system emits atomic occurrences E1-E3. System adds E5 (measurement runs) as the occurrence producing C2-C5 — events record the runs, claims record the results.
5. **Economic claims:** system keeps B2 (p90 cost scaling — a real measured risk) and B3 (overage unit integrity) as load-bearing claims the gold folded away; rejects the rest of the guardrail tables (§7.7-7.12).
6. **Agreement with gold (for the delta table):** D1-D9 split per R2 (incl. D5a/b/c, D7/D8, D9a/D9b), C1-C17 substance, E4-as-event, the "keep evidence, not tables" posture (C6 kept, not buried), and the §7 rejection classes all match in substance.

---

*Probe output — independent of the gold. Compare against sections A-G of the gold window.*
