---
title: "Epic Research Brief — #1402 Agent-Reasoning Eval Battery"
type: synthesis
domain: strategy
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-14
aboutSubjects: tortoise
aboutObjects: tortoise
extends: docs/research/2026-08-14-agentic-eval-landscape.md (prior landscape research — PRIOR_RESEARCH dedup source)
---

## Epic Research Brief — Agent-Reasoning Eval Battery (#1402)

> PRIOR_RESEARCH dedup: the broad landscape (12+ queries: agentic evals, memory benchmarks, self-evolution, adaptive evals, adversarial limitations) is already filed in `docs/research/2026-08-14-agentic-eval-landscape.md` and the draft battery `docs/agent-reasoning-eval-battery.md`. This brief adds the epic-specific axes (Strategy, UX, Workflow, Tech Stack) and the research-gap queries (differential arm selection, judge reliability, benchmark-driven purchasing). Broad queries were NOT re-run.

### Strategy Context

- **Benchmark-driven purchasing — partially validated.** Enterprise buyers in financial services, healthcare, and customer operations explicitly evaluate **memory persistence and staleness handling** as procurement criteria (agentmarketcap 2026 state-of-agent-memory report); vendor guidance recommends asking for **third-party benchmark results (recall@10), data-persistence SLAs, TCO, and stack integration** (sparkco 2026). Conclusion: the market IS benchmark-literate, but the buyer's checklist is recall/staleness/persistence — **no standard benchmark exists for the reasoning axis**. That is both the risk (unvalidatable claim until we build the battery) and the opportunity (a measured reasoning delta is a differentiator no competitor publishes).
- **2026 memory-benchmark divergence.** New benchmarks (ForgetEval, Memora + FAMA, AMB) test deletion/drift/stale-memory handling, not just recall — the field is moving toward exactly the axes Tortoise's supersession/bi-temporal design targets (llms3 2026 roundup). The battery's Tier-3 parity leg should include a staleness/drift probe to align with where the market is heading.
- **Positioning risk:** if the battery falsifies the uniqueness claim, positioning falls back to "memory+reasoning aid" — the retention story (Tier-2) survives independently (align fix-3).

### UX Pattern Research

- **Judge-based scoring is the established protocol but has documented failure modes we must calibrate against** (⚠️ this is the research-stage correction to the battery's "blind LLM-judge rubrics" assumption):
  - Position bias is real — paired AB+BA tests and chance-corrected reliability reporting are now the expected standard (arXiv 2606.19544).
  - **Rubric verification in agentic settings shows LOW reliability on task/tool-use rubrics** (arXiv 2606.29920) — our R2 adversarial-coverage rubric is exactly the task/tool class. Mitigation: judge validation per rubric before deployment (IRT-based judge diagnosis arXiv 2602.00521; stress tests for label flips, verbosity bias, stochastic stability arXiv 2603.05399).
  - Rubric *design* by LLMs shows strong inter-model reliability ("Learning to Judge: LLMs Designing and Applying Evaluation Rubrics", Findings of EACL 2026, aclanthology 2026.findings-eacl.335) — supports the SEAL-style adaptive-rubric generator, provided the generated rubrics pass the same validation.
- **Pre-registration is the anti-Goodhart pattern** (product-success §1, best-practices 2507.02825): rubrics + thresholds locked before runs; calibration mode prints measured deltas without asserting ([cal] discipline, epistemic-layer §8.3).
- **Leaderboard presentation patterns** (benchlm.ai agentic leaderboard, tau-bench.ai): verdict reports in the industry read as per-scenario pass rates + delta + environment. Our verdict report should follow that shape so the claim is comparable.

### Workflow Pattern Research

- **Longitudinal eval workflow:** SEA-Eval's sequential task stream (repeated families, SR + token-trajectory + strategy-reuse) is the canonical workflow for the improvement claim; pseudo-evolution detection = token trajectory non-convergence (2604.08988, ⚠️ single-source).
- **Interdependent-loop workflow:** MemoryArena's Memory-Agent-Environment loop (learn in earlier sessions, use in later) is the canonical multi-session workflow (2602.16313).
- **Differential workflow:** same battery × arms, matched recall (ex-ante top-K factual F1, symmetric trigger per align fix-2) — the matched-recall protocol is the anti-confound control.
- **Iterative-feedback workflow:** OPT-BENCH's fix-rate-over-iterations pattern (2605.08904) for the feedback-integration probe (D3).
- **Adaptive-test workflow:** SEAL co-evolution's diagnosis→adaptation loop (2605.24426) and AgenticEval's difficulty escalation (2509.26100) define the evolving-test harness; guardrail = pre-registered quality gate per generated scenario.

### Tech Stack Research

- **Differential arm selection (concrete):**
  - **A2 generic memory: Mem0** (managed API, 4-layer memory: conversation/session/user/organizational; easiest setup — good "industry default" comparator) — datapace/aigentlab 2026.
  - **A2b strongest comparator: Zep/Graphiti** (temporal knowledge graph, bi-temporal facts, invalidation-not-deletion — architecturally closest to Tortoise; internal graph-as-memory research already shows Zep beats MemGPT on DMR/LongMemEval and its staleness mechanism is the n26modi head-to-head winner). **Zep must be an arm** — if Tortoise only beats Mem0 but not Zep, the "unique" claim is weak. Consider making the sweep A2 = Mem0, A2b = Zep.
  - **A1 long-context:** 1M-token-context model stuffing (the MemoryArena-falsified baseline).
  - **A3 recall-RAG:** flat claim store over vector index (ChromaDB-class) — the "no propagation" control.
  - Arm adapter cost is low (all have APIs/SDKs); the Zep adapter is the only one needing a graph-shaped integration.
- **Benchmark runners (released, verified this research):**
  - mem0ai/memory-benchmarks harness — runs LoCoMo, LongMemEval, BEAM locally (team infra #1144 already targets LongMemEval).
  - MemoryAgentBench (HUST-AI-HYZ repo, code released).
  - MemoryArena — dataset on HuggingFace (memoryarena.github.io, HF loading examples) — ⚠️ verify eval-script completeness at scope stage (settings adaptation required: web-nav/planning/search/formal-reasoning).
  - SEA-Eval — dataset on HF + eval scripts (LeaperOvO/SEA-Eval, seaeval.github.io) — ⚠️ "released upon publication" status to re-verify at scope stage.
  - EvoAgentBench (evermind-ai) + SEAGym — optional additional self-evolution evals.
- **Staleness/drift probes:** new 2026 benchmarks (ForgetEval, Memora+FAMA, AMB) target deletion/drift/stale-handling — candidates for the Tier-3 parity leg beyond LongMemEval/LoCoMo.

### Assumptions Register

| Assumption | Confidence | Source | Validation Plan |
|---|---|---|---|
| The graph's reasoning contribution is measurable at agent level by our probes | medium | 2604.20006 task-dependence finding (reasoning = hardest axis) | Probes designed to catch a null; calibrate on pilot runs before full battery |
| Matched-recall meaningfulness (decision-relevant claims, not just facts) | medium | Align review P2-2 | Ex-ante definition (top-K factual F1 K=5, symmetric trigger, INCONCLUSIVE branch) |
| Blind LLM-judge rubrics score reasoning outcomes reliably | **medium → ⚠️ riskier for task/tool rubrics** | arXiv 2606.29920 (low reliability on task/tool rubrics); 2606.19544 (position bias) | Per-rubric judge validation before deployment: AB+BA position-bias tests, chance-corrected reliability, IRT diagnosis, stress tests |
| Benchmark-driven purchasing (market buys on benchmarks) | medium | agentmarketcap/sparkco 2026 (persistence/staleness/recall@10 criteria) | Verify at verdict time: does the reasoning delta map to buyer-visible claims (staleness, drift, contradiction) |
| Near-term customer workloads exercise the reasoning pathway | low | Align review P2-6a | Scenario-to-workload mapping during scope; mismatch = finding, not failure |
| AC-L2 token-trajectory gate (SEA-Eval) holds beyond its single source | low ⚠️ single-source | 2604.08988 only | Corroboration search in scope; gate stays, labeled provisional |
| Zep/Graphiti is the strongest realistic comparator | high | graph-as-memory research (n26modi head-to-head, Zep beats MemGPT/LongMemEval) | Include Zep arm in differential sweep |
| Battery completes before claim goes public | medium | Align review | Reclassification trigger: reasoning positioning in launch messaging ⇒ Do-now |
| Run cost within existing eval budget | high | Align fix-5 (2–4 engineer-weeks, ~500–1,000 episodes, 1144 infra) | Track against #1144 budget at scope |

## Raw Notes

- [2026-08-14] Mem0/Zep/LangMem comparison: Mem0 = managed, 4 layers (conversation/session/user/org), easiest API setup; Zep = temporal knowledge graph (enterprise-strong); LangMem = LangGraph-native library; Letta = agent-OS class. Common architecture: extract salient facts → dedupe → optional graph → store across vector/graph/KV. Sources: datapace.ai, aigentlab.tech, maidul-haque blog, dev.to — 4 independent vendor/analysis sources (Medium tier).
- [2026-08-14] Judge reliability: chance-corrected reliability + AB+BA position-bias tests now expected (2606.19544); **agentic rubric verification LOW reliability on task/tool rubrics** (2606.29920); IRT separates intrinsic consistency from human alignment (2602.00521); stress tests: label flips, paraphrase invariance, verbosity bias, stochastic stability (2603.05399); LLM rubric *design* shows strong inter-model reliability ("Learning to Judge: LLMs Designing and Applying Evaluation Rubrics", Findings of EACL 2026, aclanthology 2026.findings-eacl.335, retrieved 2026-08-14). ⚠️ Action: per-rubric judge validation gate in the battery harness.
- [2026-08-14] Enterprise procurement: buyers evaluate memory persistence + staleness handling (agentmarketcap 2026-04); vendor guidance: third-party recall@10 benchmarks, persistence SLAs, TCO, stack integration (sparkco 2026). Benchmark divergence: ForgetEval, Memora+FAMA, AMB test deletion/drift/stale-handling (llms3 2026-07). Partially validates benchmark-driven purchasing on recall/staleness axes; reasoning axis unmeasured by market.
- [2026-08-14] PRIOR_RESEARCH: broad landscape deduped to docs/research/2026-08-14-agentic-eval-landscape.md (12+ queries incl. adversarial; verified by fresh-context reviewer — no hallucinated citations).
