---
title: "Research Synthesis — Agentic Reasoning Eval & Self-Improvement Landscape"
type: synthesis
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-14
aboutSubjects: tortoise
aboutObjects: tortoise
---
# Research Synthesis: Evaluation & Tests for Agentic Reasoning, Live Self-Improvement, and Evolving Tests

**Date:** 2026-08-14 · **Depth:** Deep (multi-angle, ~12 external queries + paper deep-dives)

## What We Have Internally
- `docs/epistemic-layer-eval-spec.md` (v1): EP-engine correctness spec — property tests (P1–P10), graph quality (G1–G8), endpoint scenarios (R1–R8), adversarial (A1–A8), baselines (B0–B2). This tests the *engine* (belief propagation), not the agent-using-the-engine.
- `docs/product-success-eval.md` (draft v1): "Did the memory matter?" framework — delta principle, matched-pairs cross-over, **2-session continuity experiment** (S1 decision → S2 follow-on, only difference = the graph), 21-day answer-from-memory test (1c). This is the internal counterpart of what the user asked about.
- Memory system (tortoise-memory.mjs) offline — no prior epistemic claims retrievable (skipped per graceful degradation).

## External Landscape (organized by theme)

### A. Standard agentic-reasoning evals (single-session; the "tests without multi-session" option)
**[Confidence: Medium]** — benchmark descriptions corroborated across survey 2504.19678, marktechpost, tessl.io, benchlm.ai, philschmid compendium (multiple independent sources); saturation/GAIA~90% figures are industry-reported, not formally measured here (⚠️ verify when needed).
| Benchmark | What it tests | Status/gaps (2026) |
|---|---|---|
| AgentBench | Broad agent behavior across 8 environments (OS, DB, KG, card games, household, shopping, browsing) | Classic, but saturating |
| GAIA | Multi-step tool use + reasoning; human-verifiable answers | "General assistant"; saturated (frontier ~90%) |
| τ-bench (tau-bench) | Reliability in tool-enabled *conversational* workflows (long-horizon, multi-turn, user-in-loop) | Current standard for tool reliability; single-session |
| Terminal-Bench 2.0 | Real sandboxed CLI: planning, execution, recovery, env config | Software-agent standard; single-session |
| OSWorld 2.0 / Verified | Full desktop GUI control, long-horizon workflows | Computer-use standard; long-horizon but no cross-session memory |
| SWE-bench (family) | Real GitHub issue resolution | Contamination concerns repeatedly flagged |
| BFCL V3/V4 | Tool/function calling (multi-turn in V3) | Used as SEAL co-evolution's in-distribution testbed |

Sources: "From LLM Reasoning to Autonomous AI Agents" survey (2504.19678), marktechpost "Top 7 benchmarks that actually matter for agentic reasoning" (2026-04), tessl.io benchmark roundup, benchlm.ai agentic leaderboard (2026-08), philschmid/ai-agent-benchmark-compendium.

**Gaps (flagged by multiple 2026 sources):** short-horizon bias; single-run scores miss inconsistency (pass@k-style reliability needed); static benchmarks overstate capability (contamination, gaming); success rate alone creates a "capability illusion."

### B. THE CORE: evaluating whether live use improves an agent (self-improvement evals)
**[Confidence: Medium overall; SEA-Eval metrics Medium ⚠️ single-source; SALM numbers Medium ⚠️ single-source (verified against abstract); OPT-BENCH/AgenticEval/CoEvolve Low ⚠️ single-source]**

> ⚠️ **SEAL naming collision:** three distinct works share the acronym SEAL — co-evolution (2605.24426), Self-Adapting Language Models (2506.10943), and the meta-judge tournament protocol (2605.30104). They are unrelated; do not conflate.

**Framing (most current, July 2026):** *"Self-Improvements in Modern Agentic Systems: A Survey"* (arXiv 2607.13104, Jilin/KAUST/Schmidhuber). An agent = foundation model + **operational scaffold** (prompts, memory, tools, control logic). Self-improvement = a self-induced **update operator** committing updates to model params OR scaffold components. **Key eval recommendation: improvement must be treated as a process over time — use held-out or temporally-shifted tasks and track regressions/safety across iterations.** (i.e., static before/after scores are not enough.)

**SEA-Eval** (arXiv 2604.08988, 2026-04) ⚠️ single-source — the first benchmark *specifically for self-evolving agents*; the most directly relevant thing found. Formally defines the Self-Evolving Agent (SEA) and the "Evolutionary Flywheel" (act → distill → commit → reuse). **Two primary metrics:**
- **Success Rate (SR)** — baseline task completion (minimum reliability guarantee).
- **Token Consumption trajectory (T)** — the *core signal*: genuine evolution ⇒ execution overhead decreases monotonically as task frequency increases (reuse of distilled strategies replaces zero-shot reasoning). If T does not converge across a sequential task stream, the agent is in **"pseudo-evolution"** — memory grows but doesn't change behavior. Finding: under identical SR, token consumption differs up to 31.2× across frameworks; SR alone is a "capability illusion."
- Diagnostics: **strategy-reuse rate** (fraction of steps that invoke a retrieved strategy vs zero-shot reasoning), **distillation failure** (memory cardinality grows but overhead doesn't drop), **retrieval failure** (relevant entries exist but aren't retrieved).

**SEAL — Synergistic Co-Evolution of Agents and Learning Environments** (arXiv 2605.24426, 2026-05) ⚠️ single-source — likely close to the user's "agent games / figure out the most suited tests / it evolves" memory. Closed-loop co-evolution: collects on-policy trajectories under executable verification, **diagnoses failed rollouts into turn-level failure labels**, then uses those diagnoses to adapt BOTH the training-time environment/interface (tool-affordance cues, constraint info, recovery feedback) AND the policy (diagnosis-guided advantage reweighting). +8.25 to +26.25 avg points with only 400 samples, positive OOD transfer. The environment itself evolves to fit the agent's revealed failures — "the tests adapt."

**Self-Adapting Language Models (SEAL)** (arXiv 2506.10943, NeurIPS 2025) — weight-level: model generates its own synthetic data + optimization directives ("self-edits"), applied via LoRA, RL loop rewards downstream improvement. SQuAD no-context 33.5% → 47.0% (verified against paper abstract). ⚠️ single-source (one paper); an instance of test-time training / meta-learning.

**Other self-improvement evals:**
- **OPT-BENCH / OPT-Agent** (arXiv 2605.08904) ⚠️ single-source: tests *iterative self-optimization*; models handle continuous feedback better than discrete combinatorial reasoning.
- **AgenticEval / SafeEvalAgent** (2509.26100, ACL Findings 2026) ⚠️ single-source: multi-agent self-evolving *safety* eval — one agent runs tests, another refines future tests to increase difficulty over iterations.
- **A Multi-Agent Framework for Dynamic LLM Evaluation** (COLING 2025) ⚠️ single-source: self-evolving benchmark that dynamically reframes instances to stay informative as models improve.
- **CoEvolve** (ACL 2026) ⚠️ single-source: agent-data mutual evolution — feedback from rollouts guides task synthesis.
- **Survey of Self-Evolving Agents** (arXiv 2507.21046, Princeton et al.) [Medium]: eval goals = **Adaptivity, Retention, Generalization, Efficiency, Safety**; eval can have a temporal dimension (short-horizon adaptive vs long-horizon lifelong); distinguishes intra-test-time vs inter-test-time adaptation.

### C. Multi-session evals (the "requires multiple agent sessions" option)
**[Confidence: Medium overall; MemoryArena High (paper + independent memory survey 2603.07670 corroborate the gap); IFCMemoryBench stats Low ⚠️ single-source; anchoring finding Low ⚠️ single-source]**

**MemoryArena** (arXiv 2602.16313, 2026-02, Stanford/UCSD/Choi/Pentland) — *the flagship 2026 multi-session benchmark.* A unified gym for **Memory-Agent-Environment loops**: human-crafted tasks with *explicitly interdependent subtasks* — agents must learn from earlier actions/feedback (distill into memory), then use that memory to guide later actions. Four settings: web navigation, preference-constrained planning, progressive information searching, sequential formal reasoning. **Key finding: agents near-saturated on LoCoMo (long-context recall) perform poorly in agentic multi-session settings** — exposing a gap in memory evals that only test recall. (Directly validates the "memory must change behavior, not just recall" thesis in `docs/product-success-eval.md`.)

**IFCMemoryBench** (KDD Eval Workshop 2026) ⚠️ single-source: 143 multi-session tasks across 19 projects, 4,016 prior sessions — human-validated long-term memory for coding agents (verify when a second source is available).

**Recall-style memory benchmarks (single-session *assessment*, multi-session *history*):** LongMemEval(-S) (500 multi-session dialogues; recall/integration/temporal reasoning), LoCoMo (up to 35 sessions), BEAM, MemBench, MemoryAgentBench (2k tasks), MemoryBench (778). These test memory *contents*, not agentic use — MemoryArena's critique applies. Contextualized by the survey "Memory for Autonomous LLM Agents" (arXiv 2603.07670, 2026-03) [Medium], which compares these benchmarks, notes RAG-augmented LLMs still lag humans on temporal/causal dynamics, and independently flags LoCoMo + MemoryArena as the relevant multi-session/agentic memory evals.

**Evidence on "does memory improve agent performance" — MIXED (important adversarial finding):**
- **Supporting:** Memory-R1 (ACL 2026) — consistent gains on LoCoMo/LongMemEval, especially multi-hop, temporal, multi-session questions. "Maintainable Topic Documents" (arXiv 2606.10677) — 19.2-pt gain on MemoryAgentBench. LIGHT (ICLR 2026) — 3.5–12.7% gains as dialogues lengthen.
- **Skeptical:** "Benchmarking Long-Term Memory for Personalized Agents" (arXiv 2604.20006) — only *marginal* gains overall; **strong task dependence: clear gains for remembering, weaker for recommending, poor on reasoning.** ⚠️ This directly bears on "should an agent using it live improve its intelligence": the honest answer is *it depends on the task family*; reasoning improvements from memory alone are the weakest category in the literature.
- "How Memory Management Impacts LLM Agents" (ACL 2026): outputs often track similar retrieved memory records (anchoring risk — memory can anchor, not just inform).

### D. Agent games / arenas (the "agent games" memory)
**[Confidence: Low ⚠️ all single-source] — each arena/game finding comes from one paper; verify when second sources appear. EvoEval's 828-problem figure was cross-checked against the project page + PapersWithCode (Medium).**
- **MindGames** (arXiv 2605.29512, 2026-05) ⚠️: live arena built on TextArena — online matchmaking, TrueSkill ratings, full trajectory logging; measures social deduction, deception, strategic reasoning in repeated self-play. Rating *changes over time* = improvement signal.
- **CATArena** (2026) ⚠️: tournament-style, open-ended board/card games, designed to **avoid score saturation** and support continuous evaluation of learning ability + strategy coding.
- **Board Game Arena** (arXiv 2508.03368) ⚠️: LLM-vs-LLM + self-play; measures decision optimality, reward, reasoning coherence, error rates (not just win rate).
- **CoNL** (ICML 2026) ⚠️: multi-agent self-play where models critique/revise each other with **diagnostic rewards** to measure generation + judging capability, no external judges.
- **SEAL meta-judge** (arXiv 2605.30104) ⚠️: seeded-elimination tournament + an LLM meta-judge that *generates rank-adaptive checklist items* that get finer each round — the rubric/tests themselves evolve to extract signal from saturated benchmarks.
- **EvoEval** (OpenAI, GitHub) [Medium]: evolving coding benchmarks — LLM transforms HumanEval into 828 harder problems (Difficult, Creative, Subtle, Combine, Tool Use). The prototype of "tests that evolve."

### E. Rigor / adversarial (what not to trust)
**[Confidence: Medium overall; individual rigor papers ⚠️ single-source each, but they converge on the same failure themes]**
- "Establishing Best Practices for Building Rigorous Agentic Benchmarks" (arXiv 2507.02825) ⚠️: benchmark-methodology — scoring schemes, environment interaction, validation requirements.
- "A Synthesis of Tool-Use, Planning, and Reasoning Failures" (arXiv 2607.05775, 2026-07) ⚠️: six failure clusters incl. **measurement validity problems** — an entire failure category is about evals measuring the wrong thing.
- 2026 industry reviews (kili-technology, agora-intelligence) [Medium]: contamination (SWE-bench repeatedly cited), gaming, short-horizon bias, divergence between benchmark success and production complexity, "solution leakage" concerns.
- IBM "A Survey on Evaluation of LLM-based Agents" (ACL 2026) ⚠️: gaps in cost-efficiency, safety, robustness, fine-grained scalable methods.

## Confidence / Contradictions
| Claim | Tier | Sources |
|---|---|---|
| Static single-session agent benchmarks saturate and overstate capability (contamination/gaming) | High | survey 2504.19678, kili, agora, agentmarketcap, 2607.05775, IBM survey |
| SEA-Eval: token-consumption trajectory (not SR) discriminates genuine vs pseudo-evolution | Medium | SEA-Eval paper only ⚠️ single-source, but the *phenomenon* (memory that doesn't change behavior) is corroborated by MemoryArena's LoCoMo-saturation finding |
| Long-term memory improves multi-hop/temporal/multi-session performance | Medium | Memory-R1 + LIGHT + topic-docs (all supporting) vs 2604.20006 (skeptical) — flagged as contradiction |
| Memory improves *reasoning* | Low ⚠️ | 2604.20006 shows poor reasoning gains; no strong counter-evidence for reasoning specifically |
| Agents saturated on recall-style memory benchmarks fail agentic multi-session settings | High | MemoryArena (primary) + memory survey 2603.07670 |
| Co-evolving tests/environments (SEAL) produce large gains | Medium ⚠️ | SEAL paper (single-source, low-resource 400-sample setting; +8–26pts); verify when independent replications appear |

## Recommendation (for the Tortoise context)
1. **For "does live use improve the agent's intelligence":** the field's answer is **longitudinal, behavior-delta measurement** — not static scoring. Adopt the SEA-Eval instrument set as the product-success battery's second arm: success rate + **token-consumption trajectory across a sequential task stream** + strategy-reuse rate. This is the only family of metrics that can distinguish "memory genuinely reused" from "memory accumulates but doesn't matter" — precisely the pseudo-evolution failure your product-success spec's "delta principle" targets. Your existing 2-session continuity experiment is the right shape; SEA-Eval's sequential-stream design (many repeated task families, not one pair) strengthens it into a growth curve.
2. **Multi-session option:** MemoryArena's interdependent-subtask design is the closest external match to your 2-session experiment — cite it as external validation; IFCMemoryBench shows the real-session-dump variant (19 projects, 4,016 sessions) which maps to your weekly real-session G7 job.
3. **Single-session option (no multi-session):** τ-bench (tool-reliability in conversational workflows) + GAIA (multi-step reasoning) + Terminal-Bench (software) cover the standard; but for *your* claim, single-session evals cannot demonstrate improvement — they can only demonstrate capability level.
4. **"Tests that evolve":** the user's memory maps to SEAL co-evolution (agent+env co-evolve via failure diagnoses), EvoEval (evolving benchmarks), AgenticEval (tests get harder each iteration), and the SEAL meta-judge (self-improving rubrics). A Tortoise-specific variant: use an LLM judge that generates harder follow-on questions from the agent's failure profile — the eval itself learns, keeping the test informative as the agent improves.
5. **Adversarial gates to respect:** contamination controls (temporally-shifted/held-out tasks per 2607.13104), matched-pairs cross-over (already in product-success spec), false-positive gates (already in 1b), and the memory-can-anchor finding (ACL 2026) — treat "answers track retrieved memory" as a failure mode to test, not just a feature.

## File locations
- Full synthesis: `docs/research/2026-08-14-agentic-eval-landscape.md` (tortoise repo)
- Working copy: `/tmp/agentic-eval-research-synthesis.md`

## Open questions needing human decision
1. Do you want the eval to prove *capability* (does the agent reason well) or *improvement* (does the agent get better with live use)? SEA-Eval-style longitudinal metrics are the only current answer for the latter.
2. Is the target task family reasoning-heavy? The literature (2604.20006) warns reasoning is where memory helps least — pick task families where recall→reuse is observable (decisions, workflows, preferences) OR design the test to isolate what memory adds.
3. Budget: arena-style (MindGames/TextArena) gives competitive ELO improvement curves but heavy infra; a synthetic sequential-stream battery (SEA-Eval style) is lighter and closer to your existing harness.

## Raw Notes

- [2026-08-14] Primary evidence ledger: this synthesis was compiled from ~12 external queries (Perplexity sonar + Exa) with per-claim confidence tiers recorded inline (High/Medium ⚠️ single-source). Source categories: academic (arXiv/ACL/ICLR/ICML), practitioner (benchlm.ai, tessl.io, kili-technology, agora-intelligence, agentmarketcap), official benchmark pages (tau-bench, arcprize, memoryarena.github.io, seaeval.github.io), and repo-owned eval lineage (#1144/#1369/#1350/#909). Fresh-context verifier confirmed no hallucinated citations (2026-08-14). Epic-level raw notes and the assumptions register live in `docs/epics/1402-eval-battery/02-research-brief.md`; this file is the PRIOR_RESEARCH source for downstream stages.
