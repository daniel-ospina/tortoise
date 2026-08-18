---
title: "Epic Scope — #1402 Agent-Reasoning Eval Battery"
type: decisions
domain: product
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-14
aboutSubjects: tortoise
aboutObjects: tortoise
extends: 01-align.md (decision), 02-research-brief.md (research), docs/agent-reasoning-eval-battery.md (battery spec)
---

# Epic Scope — Agent-Reasoning Eval Battery (#1402)

### Axis Research Notes

> **Findings date:** 2026-08-14 · **Provision:** granular axis queries (≤4) deduped against the brief — the boundary questions below are covered by brief sections; only the internal-harness question was researched (codebase, not web).

| Axis (expected rating) | Boundary question | Covered by / finding |
|---|---|---|
| Architecture (high) | Harness design: how episodes run, arms abstracted, trajectories logged | Brief §Workflow Pattern Research (5 canonical workflows) + §Tech Stack Research (arms, runners) + **internal finding:** `tools/longmem_eval/` already provides the runner pattern (run/ingest/reader/judge/retrieve/report) plus `tools/judge_harness.py`, `tools/kappa.py`, `tools/calibration_harness.py` — the battery extends these, it does not build a new harness from scratch |
| Research (high) | Probe design, calibration, corpus | Brief §UX Pattern Research (judge validation) + §Workflow Pattern Research (per-probe anchors) + battery spec R1–R5/L1–L6 protocols |
| Ontology (low) | Does the battery change the graph schema? | No — battery reads/writes existing Points/operators; no schema change (justified skip, battery spec + ONTOLOGY unchanged) |

## Scope Boundaries

### In Scope
1. **Battery harness extension** — a runner (extending `tools/longmem_eval/` patterns) that executes Tier-1 probes, Tier-2 streams, and Tier-3 sweeps with pinned seeds, trajectory logging (steps, tokens, re-derivations), and JSON report emission per scenario.
2. **Tier-1 reasoning probes (R1–R5)** — five single-session probes with pre-registered rubrics + calibrated thresholds: contradiction surfacing, adversarial coverage, epistemic calibration (Brier), defeat conditions, belief-update responsiveness.
3. **Judge validation gate** — per-rubric LLM-judge validation before any scoring (AB+BA position-bias tests, chance-corrected reliability, IRT diagnosis, stress tests per brief §UX), plus the kappa/min-signal tooling already in `tools/`.
4. **Tier-2 longitudinal streams (L1–L6)** — interdependent-task stream (MemoryArena-style), SEA-Eval sequential stream with pseudo-evolution gate (⚠️ provisional, single-source), reasoning-quality trajectory waves, cross-session contradiction accumulation, decision-drift resistance, distillation fidelity.
5. **Tier-3 differential sweep** — five arms (A0 no-memory, A1 long-context stuffing, A2 Mem0, A2b **Zep/Graphiti**, A3 recall-RAG, A4 Tortoise) × same battery, matched-recall protocol (ex-ante top-K factual F1 K=5, symmetric trigger, INCONCLUSIVE branch).
6. **Benchmark parity leg** — LongMemEval, LoCoMo, MemoryArena, MemoryAgentBench per arm via existing #1144 infra + released runners (mem0ai harness, MemoryAgentBench repo); staleness/drift probes (ForgetEval-class) added to Tier-3.
7. **Verdict report + claim doc** — full differentiation profile (all metrics × all arms, STRONG/STRUCTURAL/PARITY/WEAK classification with load-bearing flags), verdict outcome (UNIQUE / MECHANISM-NOT-UNIQUE / WEAK-UNMITIGATED / INCONCLUSIVE), per-weakness mitigation paths, re-run loop, artifacts-changed list; filed to docs/.
8. **Threshold calibration run** — [cal] thresholds re-locked on the real engine per epistemic-layer §1/§8.3 discipline (calibration mode prints, never silently re-tunes).
9. **Weakness-mitigation re-run loop** — for each load-bearing WEAK with a mitigation path, the battery is re-runnable after mitigation so "improve enough that weaknesses are not serious" is measured, not asserted.

### Out of Scope
- **Adaptive/evolving test generator** (SEAL-style) — the pattern is researched; the generator is deferred to a follow-up issue/phase AFTER the battery produces a stable verdict (running the generator before the battery is validated risks confounding the verdict).
- **Public/commercial benchmark product** — no packaging of the battery for external sale (align alt-5).
- **New extraction work** — graph input quality is #1350/#909's domain; the battery consumes the product pipeline as-is (epic dependencies).
- **Product UI/UX changes** — the battery is internal tooling; no user-facing surfaces.
- **Marketing/positioning changes** — the claim goes public only per the reclassification trigger, after the verdict (align fix-4).

### Boundary Rationale
The cut is **claim-gated**: everything needed to produce a falsifiable verdict on the uniqueness claim is in scope; everything that depends on or extends that verdict (adaptive generator, public benchmark, positioning) is out. Harness work reuses existing eval infra rather than building new — the battery is an extension of #1144's runner, not a parallel system.

## Customer Value Map

| Scoped Capability | User-Visible Value |
|---|---|
| Tier-1 probes | The team learns *in one session* whether the graph changes how an agent reasons (contradiction surfaced, deliberation depth, calibration, correct belief updates) — the capability claim, measurable today |
| Judge validation gate | Decision-makers can trust the verdicts — rubrics proven reliable before they score anything |
| Tier-2 longitudinal streams | The team learns whether the agent *gets better with live use* — the compounding-memory promise, measured as a trajectory not a snapshot |
| Tier-3 differential sweep | The team learns whether the improvement is *unique to Tortoise* or just memory-in-general — the falsification the market can't answer for us |
| Benchmark parity leg | Buyers' standard questions (recall@K, staleness, LongMemEval/LoCoMo) answered from our own product — parity evidence the sales story needs |
| Verdict report + claim doc | A full, honest profile of where the graph is better, where it's not, and which weaknesses are serious — the falsification-accepting evidence that converts, or the improvement targets that redirect |
| Threshold calibration | All numbers in the claim are reproducible on the shipped engine — no tuned-once-in-a-drawer results |

## Complexity Ratings

| Axis | Rating | Rationale |
|------|--------|-----------|
| UX | low | Internal tooling; minimal surfaces (JSON reports, verdict doc) — no product UI |
| Architecture | high | Harness extension + 5–6 differential arms + 3 benchmark integrations + trajectory instrumentation + matched-recall protocol |
| Ontology | low | No schema changes — battery reads/writes existing Points/operators |
| Accessibility | low | N/A — no user-facing surfaces |

## High-Level E2E Test Cases

### E2E-1: Tier-1 probe battery produces verdicts
**Given:** a calibrated scenario corpus and the five probe protocols (R1–R5) with pre-registered rubrics
**When:** the harness runs the battery on the Tortoise arm and the plain-agent arm (matched pairs, pinned seeds)
**Then:** each probe emits a per-scenario result with the AC-R1…AC-R5 metric values
**And:** thresholds are [cal]-locked and printed, not silently tuned

### E2E-2: Longitudinal stream detects genuine vs pseudo-evolution
**Given:** a sequential task stream of repeated families (5–8 families × 3+ reps, held-out family reserved)
**When:** the stream runs across sessions with fresh context and the graph as the only difference
**Then:** token/step trajectories are emitted per family (SR + tokens + strategy-reuse)
**And:** the pseudo-evolution gate fires (flat tokens while graph grows = FAIL, ⚠️ provisional label)

### E2E-3: Differential sweep renders the differentiation profile
**Given:** five arms (A0/A1/A2/A2b/A3/A4) and the matched-recall protocol (top-K factual F1 K=5, symmetric trigger)
**When:** recall is matched ex-ante and the same battery runs on every arm
**Then:** every probe (R1–R5, L1–L6, D2–D4) is scored for all arms with no exclusions
**And:** each metric is classified STRONG/STRUCTURAL/PARITY/WEAK with a load-bearing flag, and the verdict outcome (UNIQUE / MECHANISM-NOT-UNIQUE / WEAK-UNMITIGATED / INCONCLUSIVE) is produced per the pre-committed rule (≥1 true differentiator AND no serious weakness)

### E2E-4: Benchmark parity leg runs on released benchmarks
**Given:** LongMemEval, LoCoMo, MemoryArena, MemoryAgentBench runners and the arm adapters
**When:** the parity leg executes per arm
**Then:** recall/staleness results are emitted per benchmark with methodology unchanged
**And:** results are cross-referenced against published baselines (saturation context shown)

### E2E-5: Judge validation precedes scoring
**Given:** the judge validation gate (AB+BA position-bias, chance-corrected reliability, IRT, stress tests)
**When:** any rubric is about to be used for scoring
**Then:** the rubric is either validated (passes reliability thresholds) or blocked from scoring
**And:** validation results are recorded in the run artifact

### E2E-6: Verdict report is a full profile, falsification-accepting, and filed
**Given:** all tiers executed and recorded
**When:** the report is assembled
**Then:** the AC table (AC-R1…AC-D4) is populated with measured values for every metric on every arm
**And:** the verdict outcome (UNIQUE / MECHANISM-NOT-UNIQUE / WEAK-UNMITIGATED / INCONCLUSIVE) is stated with per-weakness mitigation paths and the artifacts-changed list, filed to docs/

### E2E-7: Runs are deterministic and re-runnable
**Given:** pinned seeds and fixed scenario builders
**When:** the same battery run is repeated
**Then:** results reproduce within tolerance (no seed drift)
**And:** calibration mode exists for re-locking thresholds

## Epic Scope Ready for Review

**Scope:** 9 in-scope deliverables (harness extension, probes, judge gate, streams, differential sweep, parity leg, verdict report, calibration, weakness-mitigation re-run loop); 4 explicitly deferred (adaptive generator, public benchmark, extraction, marketing).
**Customer value map:** 7 capabilities mapped, all user-visible outcomes.
**E2E test cases:** 7 drafted (behavioral, UI-independent).
**Complexity:** UX low · Architecture high · Ontology low · Accessibility low.

Review the scope boundaries, customer value map, and E2E test cases. Reply "proceed" to continue to detailed planning, or give feedback.
