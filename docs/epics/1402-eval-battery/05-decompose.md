---
title: "Epic Decomposition — #1402 Agent-Reasoning Eval Battery"
type: decisions
domain: strategy
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-14
aboutSubjects: tortoise
aboutObjects: tortoise
extends: 04-plan.md (plan), 03-scope.md (scope + test-design #1404)
---

# Epic Decomposition — Agent-Reasoning Eval Battery (#1402)

**MECE status:** verified (3 ownership-clarity fixes applied; re-verified by reviewer — see below).

## Child Issues (dependency-ordered)

| # | Issue | Complexity | Depends on | Delivers (plan section) |
|---|---|---|---|---|
| 1406 | Harness core (runner/CLI/config/artifacts/determinism) | standard | — | §3/§5/§6; E2E-1.1-emission, 1.4, 1.5-aggregation, 7.1 |
| 1407 | Scenario corpus v1 (all Tier-1 + stream + adversarial packs, sealed golds) | standard | — | §4; E2E-1.1/1.2/1.3/2.1–2.5/3.4/3.5 |
| 1408 | Arm adapters (6 arms, isolation, ArmUnavailable) | standard | #1406 | §5 arms/; E2E-3.1, 3.6, 1.5-raise |
| 1409 | Tier-1 probes R1–R5 (scorers + AC gates) | complex | #1406, #1407, #1408, #1410 | §5 probes/; E2E-1.1/1.2/1.3 |
| 1410 | Judge validation gate (AB+BA/reliability/IRT/stress, drift) | standard | #1406 | §5 judge/; E2E-5.1/5.2 |
| 1411 | Tier-2 streams L1–L6 (trajectory instrumentation) | complex | #1406, #1407, #1408, #1409, #1410, #1413 | §5 streams/; E2E-2.1–2.6 |
| 1412 | Tier-3 differential D1–D4 | complex | #1407, #1408, #1409, #1410, #1411, #1413 | §5 differential/; E2E-3.1/3.3/3.4/3.5 |
| 1413 | Matched-recall pre-pass (K=5, symmetric trigger, INCONCLUSIVE) | standard | #1406, #1408 | §5 recall/; E2E-3.7 |
| 1414 | Parity leg (LongMemEval/LoCoMo/MemoryArena/MemoryAgentBench/ForgetEval) | standard | #1406, #1408, #1144 | §5 parity/; E2E-4.1 |
| 1415 | Report/verdict assembler + calibration (+ `battery report` subcommand) | complex | #1409, #1410, #1411, #1412, #1413 | §5 report/; E2E-3.2/6.1-fixtures/6.2/7.1 |
| 1416 | Battery execution + verdict report (real-run, claim doc) | complex | #1406..#1415, #1350, #1144 | scope item 7/9; E2E-6.1-real-run/7.2 |

**External deps:** #1350 (extractor v2 — graph input quality), #1144 (retrieval-eval baseline + runner infra), #1369 (LongMemEval ingestion wiring, relevant to #1414).

## Dependency Graph

```
#1406 (harness) ─┬─→ #1408 (arms) ──→ #1409 (probes) ──→ #1411 (streams) ──→ #1412 (diff) ──→ #1415 (report) ──→ #1416 (exec)
#1407 (corpus) ──┘    │               │                   │                  │
                      ├─→ #1413 (recall) ─────────────────┘                  │
                      ├─→ #1410 (judge) ──→ #1409 ───────────────────────────┘
                      └─→ #1414 (parity) ────────────────────────────────────────────┘
```

Acyclic; roots #1406/#1407 parallelizable; wave 2 = #1408/#1410/#1413 (depend only on #1406); #1416 is the single sink.

## MECE Verification Result

Reviewer verified: (1) **mutually exclusive** — recording/raise splits explicitly joint-labeled (#1406/#1408), rubric reuse is dependency not duplication, parity vs recall disjoint, corpus-vs-D4 data/logic split clean; (2) **collectively exhaustive** — all 9 scope items + plan §1–8 content owned by ≥1 issue; all 25 detailed E2Es mapped, no orphans; (3) **acyclic** with correct edges; (4) **parallelism** correct (roots + wave 2).

**3 fixes applied (then re-verified):**
1. E2E-6.1 ownership split — #1415 owns report mechanism (fixtures + E2E-6.2), #1416 owns real-run claim filing (invokes `report.assemble()`, not assembles).
2. CLI split — #1406 owns subcommand dispatch/exit codes; #1415 owns calibrate logic + added `battery report` subcommand (plan §6 contract updated).
3. Scenario-content ownership — #1407 corpus is the single owner of ALL scenario packs incl. stream-tier (6 families, 10 interdependent, wave variants, A/¬A sessions, drift scenarios, D3 feedback tasks).
