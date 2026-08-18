---
title: "Tortoise — Agent-Reasoning Eval Battery (draft)"
type: product
domain: product
doc_status: draft
created: 2026-08-14
subjects.team: epistemic-team
ownedBy: epistemic-team
extends: docs/epistemic-layer-eval-spec.md (engine), docs/product-success-eval.md (product), docs/research/2026-08-14-agentic-eval-landscape.md (external), docs/drafts/2026-08-12-graph-as-memory-hypothesis.md (hypothesis + E1–E3)
---

# Tortoise — Agent-Reasoning Eval Battery
## Tests to prove Tortoise improves *reasoning* (not just recall) — memory, learning, and reasoning deltas

> **Thesis being tested:** generic memory systems demonstrably help *remembering* but show weak gains on *reasoning* (arXiv 2604.20006; MemoryArena 2602.16313 shows LoCoMo-saturated agents fail agentic settings). Tortoise's claim is different: the epistemic graph is a *reasoning medium* (Decide skill, EP belief propagation, NAND/mitigation semantics, tiered credibility, contested surfacing, supersession). This battery is adversarial to the null hypothesis "Tortoise is just another memory system."
>
> **Null hypothesis (what we must be able to falsify):** Tortoise produces the same reasoning outcomes as a plain agent or a generic memory store, when recall is held constant.

---

## 0. Design principles (inherited + new)

1. **Delta principle** (product-success §0): every metric has a counterfactual arm. No arm = decorative.
2. **Reasoning-first metrics:** the load-bearing metrics are *reasoning outcomes* (contradiction surfaced, calibration, deliberation coverage, correct belief update), never raw recall, point counts, or retrieval stats. Recall is a *control variable*, not a success metric.
3. **Pseudo-evolution gate** (SEA-Eval, 2604.08988): longitudinal tests fail if memory accumulates but behavior doesn't change — measured as non-convergent token/step trajectory + strategy-reuse rate ≈ 0.
4. **Contamination controls** (Self-Improvements survey, 2607.13104): improvement measured on held-out / temporally-shifted tasks, not the tasks the agent already saw.
5. **Matched pairs, cross-over, blind grading, p<0.05** (product-success §1): inherited verbatim.
6. **Anti-anchoring is a tested failure mode, not an assumed feature** (ACL 2026 "How Memory Management Impacts LLM Agents"): "outputs track retrieved memory" is scored as a defect — outdated beliefs must be superseded, not persisted.
7. **Every test is deterministic where possible** (epistemic-layer §0): pinned seeds, fixed scenario builders, pre-registered rubrics.

**Three tiers:**
- **Tier 1 — Single-session reasoning probes** (no multi-session needed; the "tests without that" option): capability deltas within one session, graph-assisted agent vs plain agent.
- **Tier 2 — Longitudinal learning tests** (multi-session): does the agent get measurably better over a task stream?
- **Tier 3 — Differential** (the "we're different" claim): Tortoise vs no-memory, long-context stuffing, generic memory store, recall-RAG on the *same reasoning battery*.

---

## Tier 1 — Single-session reasoning probes

Design: scenario → two arms (graph-assisted vs plain, same model/temp/seed) → blind-graded reasoning rubric → delta. 20+ runs per probe. These do NOT require multi-session: they test whether the graph changes *how* an agent reasons in one sitting.

### R1 — Contradiction surfacing (mid-session)
- **Protocol:** agent works a decision scenario; mid-session, introduce a claim that logically contradicts its adopted position (scripted, pre-registered contradiction pairs).
- **What it measures:** does the agent detect + surface the conflict (file NAND, flag contested, change position *explicitly*) vs silently adopting the new claim or flip-flopping without noting the reversal?
- **Metrics:** surfaced-within-1-turn rate; explicit-resolution rate (supersede/mitigate with ledger entry); silent-flip-flop rate; false-positive rate on matched non-contradictory controls (≤5%, per product-success 1b).
- **Gates:** surfaced ≥ 90% · flip-flop ≤ 10% · false-positive ≤ 5%.
- **Anchor:** product-success 1b; P9/A6 (epistemic-layer); graph-as-memory E1 (state + why + how-got-here). Generic memory systems score ~0 on surfacing by construction — they store, they don't detect contradiction.

### R2 — Adversarial deliberation coverage
- **Protocol:** one decision scenario; compare final rationale (decision memo) produced by graph-assisted agent (Decide workflow) vs plain agent with equal time budget.
- **What it measures:** reasoning *depth* — number of counter-arguments considered, mitigations filed per support edge (truth-vs-relevance distinction made), gaps surfaced, options with both supporting and opposing evidence.
- **Metrics:** rubric-scored (LLM judge, pre-registered anchors): counter-argument coverage, mitigation-first behavior, "what could be wrong" specificity, sources beyond the first-pass canonical set. Delta = graph − plain.
- **Process-fidelity scope:** the "≥ 80% of decisions reach 3+ Challenge/Deepen cycles" clause is a **Tier-1 mechanism-capability gate only** (proves the Decide workflow fires); it is excluded from the Tier-3 contested verdict (AC-D1 counts R2's judged subscore only).
- **Gate:** graph ≥ 1.5× plain on adversarial-coverage subscore; ≥ 80% of decisions reach 3+ Challenge/Deepen cycles when graph is used (Decide-skill fidelity).
- **Anchor:** Decide skill CHALLENGE step; COLING 2025 dynamic-eval framework; 2607.05775 failure clusters (planning failures); graph-as-memory D5 (decisions carry rejected options + criteria).

### R3 — Epistemic calibration (honesty)
- **Protocol:** scenario battery where outcomes are knowable; agent states confidence with each claim/decision (plain arm: verbal confidence; graph arm: EP-derived confidence). Score against ground truth.
- **What it measures:** does the graph's confidence track reality *better* than the model's self-reported confidence? (overconfidence is a reasoning failure; generic memory doesn't touch it)
- **Metrics:** Brier score / calibration curves; overconfidence rate (claims >0.8 confidence that are wrong); **undecided-honesty rate** (when the graph is genuinely contested, does the agent say "undecided" instead of a confident number?).
- **Gate:** graph arm Brier ≤ plain arm − 0.05 [cal]; contested scenarios yield honest-undecided ≥ 80%, confident-wrong ≤ 10%.
- **Anchor:** epistemic-layer R7/AC8 (contested = surfaced, never scored); P8 honest non-convergence; 2607.05775 measurement-validity cluster.

### R4 — Defeat conditions ("what would change your mind")
- **Protocol:** after the agent adopts a position in the graph, ask: "what evidence would overturn this decision?" Grade whether the stated defeat conditions match the graph's actual NAND/mitigation structure (the *real* weakest links, not generic hedges).
- **What it measures:** counterfactual reasoning — can the agent identify its own decision's defeat conditions? The graph encodes them as NAND edges + mitigations.
- **Metrics:** precision of stated defeat conditions vs graph structure (≥ 70% of stated conditions correspond to real edges); completeness (≥ 1 real defeat condition found per decision).
- **Anchor:** Decide CONVERGE "what we still do not know"; mitigation semantics (truth vs relevance) in decide.py.

### R5 — Belief-update responsiveness (evidence arrives / retracts)
- **Protocol:** single session; evidence E supports position P; mid-session E is retracted/undercut (or stronger ¬E arrives). Does the agent's position move *correctly* — proportionally, not stubbornly, not erratically?
- **What it measures:** responsiveness to evidence change vs the flat-store failure mode (nothing moves) and the recency-wins failure mode (position flips entirely).
- **Metrics:** belief delta in response to evidence delta (monotone, bounded); undercut-vs-rebut distinction respected (attacking the inference ≠ asserting ¬claim); correct-position rate after retraction.
- **Gate:** position moves in correct direction ≥ 90%; over-reaction (full flip on weak evidence) ≤ 10%. (R5 is a *contested* leg: the flat-store "0% responsive" baseline is an empirical expectation about control failure modes, not a by-construction win — an LLM with in-context retraction can update; the contest is A4's proportional update vs in-context behavior.)
- **Anchor:** epistemic-layer P1/P3/R1 (rebut vs undercut); B1 (flat store 0% responsive — empirical expectation, verified in the differential, not a by-construction win); graph-as-memory E2 (lifecycle stress).

---

## Tier 2 — Longitudinal learning tests (multi-session)

Design: sequential task streams, fresh context each session, **the only difference between arms is the graph**. Metrics are trajectories, not snapshots.

### L1 — Interdependent-task stream (MemoryArena-style)
- **Protocol:** stream of 10+ tasks where later subtasks *depend on* information learned in earlier sessions (web-nav/planning/search/formal-reasoning settings, adapted from MemoryArena 2602.16313). Fresh conversation each session.
- **What it measures:** does memory get *used* to guide later actions (not just recalled)?
- **Metrics:** recall-before-re-derive (product-success 1a); correct-with-provenance (answer cites stored points + confidence); re-derivation tool-call count (≥ 5× fewer than control); time-to-correct (≤ 40% of control); overall task success on interdependent subtasks.
- **Gate:** composite success ≥ 0.85 on dependent subtasks with graph vs ≤ 0.5 without [cal]; **the LoCoMo check:** agents with strong long-context recall but no graph should still fail this stream (MemoryArena's finding) — that's the differential.
- **Anchor:** MemoryArena (interdependent-loop design); product-success 2-session continuity experiment; IFCMemoryBench (real-session variant); graph-as-memory E1.

### L2 — SEA-Eval sequential stream (pseudo-evolution gate)
- **Protocol:** repeated task *families* (5–8 families × 3+ repetitions each, interleaved across sessions, shuffled). Same families recur so reuse is possible; held-out family reserved.
- **What it measures:** genuine evolution vs pseudo-evolution.
- **Metrics (trajectories, not means):** Success Rate (SR) per repetition; **token consumption per task over repetitions** (must converge downward — the core SEA-Eval discriminator, 2604.08988); strategy-reuse rate (fraction of steps invoking a graph-retrieved strategy vs zero-shot reasoning); execution steps.
- **Gates:** SR stable-or-up across repetitions; token trajectory monotone-downward convergence (≥ 30% reduction by rep 3 [cal]); **pseudo-evolution FAIL** if tokens stay flat while graph grows; held-out family SR ≥ baseline (contamination control). ⚠️ Provisional: the trajectory-gate design is single-source (SEA-Eval 2604.08988) — corroboration sought in research; the gate stays, labeled provisional until then.
- **Anchor:** SEA-Eval SR+T primitives; 2607.13104 "process over time" eval rule; strategy-reuse diagnostics; graph-as-memory E3 (cost ledger — extraction vs read-time tokens).

### L3 — Reasoning-quality trajectory (the core claim)
- **Protocol:** the same decision-scenario battery repeated across the stream (Tier-1 probes as recurring checkpoints, plus harder held-out variants each wave).
- **What it measures:** does *reasoning quality itself* improve with accumulated graph history — not just speed/recall?
- **Metrics (per wave, blind LLM-judge rubric):** counter-argument coverage; calibration (Brier); contradiction-surfacing rate on planted contradictions; decision correctness. Trajectory slope across waves.
- **Gate:** reasoning-quality slope > 0 across ≥ 3 waves with graph; slope ≈ 0 for control (no-memory). This is the falsifiable test of "the agent gets smarter with live use."
- **Anchor:** 2607.13104 (improvement as process over time, held-out tasks); OPT-BENCH 2605.08904 (iterative improvement with feedback); LIGHT (ICLR 2026) as the long-horizon comparison.

### L4 — Cross-session contradiction accumulation
- **Protocol:** plant contradictions that only become *visible* given claims from multiple prior sessions (A in session 1, ¬A in session 5; surface when the agent queries/decides in session 6+).
- **What it measures:** does accumulated knowledge *create* new reasoning signals (contested claims) that a single session can't produce? Earlier-and-earlier surfacing as the graph grows.
- **Metrics:** contested-claims recall over time (G5 at the agent level); surfacing latency (sessions between ¬A arriving and the conflict being raised); correct resolution (supersede with provenance).
- **Gate:** 100% of cross-session contradictions surfaced by session N+1 [cal]; surfacing latency ↓ as graph density grows.
- **Anchor:** epistemic-layer G5/P9; MemoryArena interdependent tasks; product-success 1c (weeks-old memory still matters); graph-as-memory D1 (bi-temporal).

### L5 — Decision-drift resistance (weeks-long)
- **Protocol:** decision D made at t0 with full rationale in graph. Re-derive D fresh-context at t+7d, t+21d (≥ 10 interleaved sessions, ≥ 5 consolidation runs). Control arm: same decision, no graph (rationale lost).
- **What it measures:** consistency of judgment over time — the agent re-derives the *same* conclusion with the *same* reasons vs drifting.
- **Metrics:** decision consistency (same option chosen); rationale consistency (rubric: same criteria weighted, same counter-arguments); drift magnitude per t; hallucinated-rationale rate (control fabricates ~100% — product-success 1c's finding).
- **Gate:** graph arm decision-consistency ≥ 90%, rationale-consistency ≥ 80%; control drift ≥ 30% (calibration floor, product-success §2).
- **Anchor:** product-success 1a/1c; 2604.20006 task-dependence caveat (pick decision/workflow families where memory is load-bearing).

### L6 — Distillation fidelity
- **Protocol:** after N sessions in a domain, the graph holds consolidated summaries (epistemic-topic-summarization). Test: reasoning tasks answered from *distilled* graph alone vs from raw sessions vs from nothing.
- **What it measures:** consolidation preserves reasoning power (not just facts) — the "dream"/summarization layer must not flatten the reasoning chain (IMPL/NAND structure).
- **Metrics:** same Tier-1 reasoning rubric (R1–R5) scored on distilled-vs-raw arms; information-loss (contradiction pairs lost in distillation); reasoning-fidelity = distilled-score / raw-score.
- **Gate:** reasoning-fidelity ≥ 0.95 [cal]; no contradiction pair dropped below surfacing threshold after consolidation.
- **Anchor:** epistemic-layer G1 (contradiction recall) applied post-consolidation; "Maintainable Topic Documents" (2606.10677) 19.2-pt MemoryAgentBench gain (memory *organization* helps); SEA-Eval distillation-failure diagnostic; graph-as-memory §1.1 (summaries are derived projections, never the record).

---

## Tier 3 — Differential (Tortoise vs the field)

Design: **same battery, five arms**, recall controlled:
| Arm | Description |
|---|---|
| A0 control | Plain agent, no memory (fresh context every session) |
| A1 long-context | Everything stuffed into context window (the "1M-token" arm) |
| A2 generic memory | Key-value/semantic memory store (Mem0 — managed API, industry default) |
| A2b strongest comparator | Zep/Graphiti (temporal knowledge graph, bi-temporal facts, invalidation-not-deletion — architecturally closest to Tortoise; research brief §Tech Stack: "Zep must be an arm") |
| A3 recall-RAG | Retrieval-augmented transcripts/decisions (flat claims, no propagation) |
| A4 tortoise | Epistemic graph + Decide workflow (the treatment) |

### D1 — The reasoning battery sweep
- Run Tier-1 probes (R1–R5) × all five arms, single-session. Recall/retrieval metrics are *diagnostics only*.
- **Differentiation profile (no exclusions — owner decision 2026-08-14):** ALL probes (R1–R5; L1–L6; D2–D4) are scored on ALL arms and reported in a full delta profile. Every metric is classified: **STRONG** (Tortoise wins, delta ≥ [cal] threshold, empirically contested) / **STRUCTURAL** (Tortoise wins but by-construction — the graph's primitives firing; honest label: competitors could replicate the primitive) / **PARITY** (within ±[cal] threshold) / **WEAK** (comparator wins, delta ≥ [cal] threshold). Each metric carries a **load-bearing flag** — is the axis customer-visible (contradiction, staleness, calibration, decision consistency, improvement-over-time)?
- **Matched-recall definition (ex ante):** equal top-K factual retrieval F1 (K=5) on a factual probe subset of the scenario corpus, measured before the reasoning battery runs. **Symmetric trigger:** if ANY arm (A0–A4) falls ≥0.10 F1 short of the corpus-best factual retrieval, rerun on a recall-matched balanced subset; if that subset is <50% of the corpus, the differential verdict is **INCONCLUSIVE** (reported, not re-interpreted).
- **Verdict rule (owner decision 2026-08-14, replaces the ≥2-of-3 gate):** the verdict is a **differentiation profile** — every metric scored on every arm, no exclusions ("if we're better we want to know"). The "unique" claim ships when **≥1 TRUE DIFFERENTIATOR** (STRONG on a load-bearing axis — empirically won, not structural) **AND no SERIOUS WEAKNESS** (no load-bearing WEAK lacking a documented mitigation path). Structural wins are reported and count toward the profile, but cannot alone support "unique" (competitors could replicate the primitive). WEAKs each carry a mitigation path; the battery is re-runnable so "improve enough that weaknesses are not serious" is testable.

### D2 — The longitudinal sweep
- Run Tier-2 streams (L1–L4) × all arms (A2/A2b/A3 with their own memory backends).
- **Gate:** A4 shows the token-trajectory convergence + quality slope (L2/L3); A2/A2b/A3 show memory growth without behavior change (SEA-Eval pseudo-evolution) — pseudo-evolution spread threshold per AC-D2 (≥2× [cal]; literature reports up to 31.2×, ⚠️ single-source).
- **Bonus metric:** LongMemEval/LoCoMo scores per arm, to *show saturation parity* — proving the reasoning delta is not explained by raw recall. (Existing infra: feat/1144 retrieval-eval + longmemevl-runner.)

### D3 — Iterative feedback integration (OPT-BENCH-style)
- **Protocol:** loop — agent does task, receives structured feedback, task repeats in harder form. Measure fix-rate and improvement-per-iteration.
- **What it measures:** does the graph make feedback *integration* more effective (feedback filed as evidence → propagation → behavior change) vs in-context-only feedback?
- **Gate:** A4 fix-rate ≥ A0 by a calibrated margin across ≥ 5 iterations; per-iteration improvement monotone.
- **Anchor:** OPT-BENCH 2605.08904 (iterative self-optimization); SEAL co-evolution 2605.24426 (failure diagnoses driving policy improvement).

### D4 — Adversarial/robustness differential
- **Protocol:** hostile inputs where memory systems break: poisoned retrievals (2% injection, epistemic-layer A8), Sybil floods (100 weak vs 1 strong source, P7), echo-chamber rings (A3), flapping (A6), outdated-claim anchoring (the ACL 2026 finding).
- **Gate:** A4 rejects poisoned claims ≥ 80% at high confidence; rank ordering T0 > 10×T4 survives EP; anchored-but-superseded beliefs are abandoned, not persisted.
- **Anchor:** epistemic-layer A1–A8; 2607.05775 failure clusters; graph-as-memory E2 (lifecycle stress).

---

## 5. The evolving-test harness (SEAL-style, optional but recommended)

Once the battery stabilizes, wrap it in an **adaptive test generator**: an LLM judge observes each run's failure profile and generates *harder follow-on scenarios* targeting the revealed weakness class (missing counter-arguments → harder contradiction pairs; calibration drift → trickier evidence tiers). The test co-evolves with the agent — keeping the battery informative as the agent improves (SEAL 2605.24426 diagnosis→adaptation loop; AgenticEval 2509.26100 difficulty escalation; SEAL meta-judge 2605.30104 rank-adaptive rubrics). Guardrail: every generated scenario passes a pre-registered quality gate (executable verification, gold answer, no leakage) before entering the pool; human audit on a sample.

---

## 6. Acceptance summary (what "we have something unique" means)

| # | Criterion | Number |
|---|---|---|
| AC-R1 | Contradiction surfaced within 1 turn | ≥ 90%; flip-flop ≤ 10%; FP ≤ 5% |
| AC-R2 | Adversarial-coverage delta vs plain agent | ≥ 1.5× |
| AC-R3 | Calibration (Brier) delta | graph ≤ plain − 0.05 [cal] |
| AC-R4 | Defeat-condition precision vs graph structure | ≥ 70% |
| AC-R5 | Correct belief update on evidence change | ≥ 90% correct direction |
| AC-L1 | Interdependent-stream success | ≥ 0.85 vs ≤ 0.5 control [cal] |
| AC-L2 | Token trajectory convergence (pseudo-evolution gate) | ≥ 30% reduction by rep 3 [cal]; flat = FAIL — ⚠️ provisional: single-source (SEA-Eval 2604.08988), corroboration sought in research |
| AC-L3 | Reasoning-quality slope over waves | > 0 graph; ≈ 0 control |
| AC-L4 | Cross-session contradiction surfacing | 100% by session N+1 [cal] |
| AC-L5 | Decision consistency at t+21d | ≥ 90% (control drifts ≥ 30%) |
| AC-L6 | Distillation reasoning-fidelity | ≥ 0.95 [cal] |
| AC-D1 | Tortoise wins vs best comparator on each metric (full profile, no exclusions) | ≥1 TRUE DIFFERENTIATOR (STRONG on load-bearing axis, empirically won) AND 0 SERIOUS WEAKNESS (load-bearing WEAK without mitigation path); structural wins reported, not disqualifying, not sufficient |
| AC-D2 | Pseudo-evolution reproduced in A2/A3 | token spread ≥ 2× (lit: 31.2×) |
| AC-D3 | Feedback-integration fix-rate | A4 ≥ A0 by calibrated margin, monotone |
| AC-D4 | Poisoning/Sybil/anchoring robustness | ≥ 80% rejection; ordering survives EP |

**Verdict rule:** Tier 1 proves capability; Tier 2 proves *improvement*; Tier 3 proves *uniqueness*. The verdict is a **differentiation profile** — ALL metrics (R1–R5, L1–L6, D2–D4) scored on ALL arms, no exclusions (owner decision 2026-08-14: "if we're better we want to know"). Each metric classified STRONG / STRUCTURAL / PARITY / WEAK with a load-bearing flag. **Verdict outcomes (pre-committed):** (a) **UNIQUE** — ≥1 TRUE DIFFERENTIATOR (STRONG on a load-bearing axis) AND no SERIOUS WEAKNESS (load-bearing WEAK without mitigation path) → the "the graph improves reasoning; other memory systems don't" claim ships with the full profile as evidence; (b) **MECHANISM-NOT-UNIQUE** — only STRUCTURAL wins → uniqueness claim dropped from positioning permanently until a new mechanism is built and re-validated; retention positioning (Tier-1/2) survives independently; (c) **WEAK-UNMITIGATED** — a load-bearing WEAK lacks a mitigation path → claim gated until the weakness is fixed and a re-run shows it below the serious threshold; (d) **INCONCLUSIVE** — matched-recall regime failed → claim does not ship; epic re-scopes the comparator or reports "not demonstrated". The full profile is reported in ALL outcomes — the diagnostic value is the profile itself. Artifacts that change on non-UNIQUE outcomes: positioning copy, product-success-eval claim section, graph-as-memory hypothesis annex — recorded in the verdict report.

---

*Companion docs: `docs/epistemic-layer-eval-spec.md` (engine correctness), `docs/product-success-eval.md` (product delta framework), `docs/research/2026-08-14-agentic-eval-landscape.md` (external research), `docs/drafts/2026-08-12-graph-as-memory-hypothesis.md` (hypothesis + E1–E3).*
