---
title: "Scoping — #2515: operationalize the agent-reasoning eval battery (layer-4 offense)"
type: decisions
issue: "#2515"
date: 2026-09-07
created: 2026-09-07
status: scoping
domain: product
doc_status: draft
subjects.team: epistemic-team
ownedBy: epistemic-team
extends: docs/agent-reasoning-eval-battery.md (the 2026-08-14 design draft), docs/benchmarks/comparison-systems.md (W7 row contract, §7 errata), docs/epistemic-layer-eval-spec.md (engine spec), docs/product-success-eval.md (delta principle), docs/epics/1402-eval-battery/04-plan.md + 05-decompose.md (incumbent epic)
---

# Scoping — #2515: agent-reasoning eval battery (layer-4 offense operationalization)

> **Scope method:** double-diamond problem convergence against the LIVE repo state
> (checked 2026-09-07). The decisive finding is in §0: the battery is already
> operationalized under epic #1402. This doc scopes #2515's genuine residual and
> files a disposition recommendation for the product owner. Children created:
> #2522, #2523, #2524, #2525.

---

## 0. Executive finding (READ FIRST) — the battery IS built; epic #1402 owns it

**#2515's premise ("draft exists, no issue/owner running the battery") is stale
relative to the repo.** Verified 2026-09-07:

| Fact | State | Evidence |
|---|---|---|
| Incumbent epic | **#1402 "Agent-reasoning eval battery — prove the graph improves reasoning" — OPEN** | Same three tiers, same R1–R5/L1–L6/D1–D4, same six arms, same AC-R1…AC-D4 + verdict outcomes as the #2515 draft (epic body + `docs/epics/1402-eval-battery/` 01–06) |
| Battery machinery | **SHIPPED** — all implementation children CLOSED: #1406 harness core, #1407 scenario corpus (sealed golds), #1408 six-arm adapters, #1409 Tier-1 probes R1–R5, #1410 judge gate, #1411 Tier-2 streams L1–L6, #1412 Tier-3 differential D1–D4, #1413 matched-recall pre-pass, #1414 parity leg, #1415 report/verdict assembler | `battery/` package on main: `runner/ arms/ probes/ streams/ differential/ recall/ report/ judge/ golds/` |
| Real-run execution | **IN FLIGHT (active same day as #2515)** — #1416 (full battery run + verdict → `docs/claims/agent-reasoning-verdict.md`) OPEN, blocked on the 2026-09-07 cluster #2284 (real-run measurement path), #2291 (A4 product-semantics + EP path), #2292 (rubric/model/budget) — all `scoped`/`planned`, research committed to main | issue states + `docs/research/2026-09-07-battery-grounding.md` |
| W7 publication | #2106 CLOSED (comparison-systems.md + errata discipline, 2026-09-05). Why-layer row still **PENDING**: #2100 closed 2026-09-04 but `tests/eval/why_suite/` has NO blessed baseline (`baselines/main.json` = empty template) and NO receipts dir. 500-Q row PENDING under #2105 (OPEN) | comparison-systems.md §0/§3.1; why_suite baseline state |
| Layer-4 feature substrate | #2349 temporal record-axis epic OPEN (cluster #2351/#2352/#2354); battery L4/L5 legs cannot fully run until its semantics land | §2 metric map, §4 |

**Consequence:** #2515 must NOT re-plan or rebuild the battery. Its scope collapses
to the *layer-4 offense tail* the #1402 decompose (hypothesis-era deps #1350/#1144/
#1369) does not own: **(i) publishing the why-layer numbers (#2522), (ii) a
pre-registered W7 row contract for the battery's differentiation profile (#2523),
(iii) sequencing the #2349-dependent temporal legs (#2524), (iv) comparator-arm
real-run readiness feeding #1416 (#2525).**

### Disposition options (product-owner decision — the highest-risk open question, §7)

- **(A) Repoint #2515** as the layer-4 OFFENSE epic: consumes #1402's #1416 verdict,
  owns the W7 fill trail (#2522/#2523) and the #2349 sequencing (#2524/#2525).
  Keeps the four-layer umbrella (#1509 L1, #2514 L2, #2513 L3, #2515 L4) coherent.
- **(B) Close #2515 as duplicate** of #1402 and re-file the four residual children
  under #1402. Cheapest; loses the layer-4 public-offense umbrella label.
- The children #2522–#2525 are needed under EITHER option — they are filed under
  #2515 now and re-point only if the owner picks (B).

---

## 1. Falsifiable null + delta arms (what is compared)

**Null to falsify (verbatim from the draft):** "Tortoise produces the same reasoning
outcomes as a plain agent or a generic memory store, when recall is held constant."
Falsifying it requires showing a reasoning-outcome delta, never a recall delta —
**recall (layer 3) is a control variable**, equalized by the matched-recall pre-pass
(`battery/recall/`, #1413): per-arm factual top-K retrieval F1 (K=5) with the
symmetric trigger (any arm ≥0.10 F1 short of corpus-best → rerun on a
recall-matched balanced subset; subset <50% of corpus → verdict **INCONCLUSIVE**,
reported not re-interpreted).

**The delta arms (six, shipped as adapters in `battery/arms/`):**
A0 plain no-memory (fresh context) · A1 long-context stuffing · A2 Mem0 generic
memory · A2b Zep/Graphiti temporal KG (architecturally closest) · A3 recall-RAG ·
**A4 Tortoise + Decide workflow (treatment)**. Every metric is scored on every arm;
verdict = differentiation profile (STRONG / STRUCTURAL / PARITY / WEAK × load-bearing
flag), pre-committed outcomes UNIQUE / MECHANISM-NOT-UNIQUE / WEAK-UNMITIGATED /
INCONCLUSIVE (draft §6; classifier shipped `battery/report/classify.py`).

---

## 2. Metric map — what is ALREADY measured vs what the battery ADDS

| Battery metric (draft) | Existing instrument | Measured today? | What the battery ADDS |
|---|---|---|---|
| R1 contradiction surfacing (agent surfaces + resolves mid-decision; flip-flop ≤10%, FP ≤5%) | Why-layer suite `tests/eval/why_suite/` (#2100, CLOSED): conflict-surfacing over the 40-point planted-conflict gold — grades the **surfaced why-block** (A11), not an agent | Suite machinery shipped; **numbers NOT yet published** (no baseline/receipt → #2522) | Agent-in-the-loop behavioral arm: file-NAND/explicit-resolution vs silent flip-flop, blind-graded from the transcript; matched non-contradictory control arm |
| R2 adversarial deliberation coverage (≥1.5× plain) | Nothing (Decide-workflow rubric is new) | No | Rubric is #2292 (in flight, real-run cluster); scorer shipped `battery/probes/r2_coverage.py` |
| R3 epistemic calibration (Brier, over/undecided-honesty) | Engine: eval-spec P8 honest non-convergence; extraction ECE (`tests/eval/metrics.py` `ece`); why_suite A4 A/B (contested-boost vs confidence-only, `a4_ab.py` — records NOT-measured + calibration gap today) | Engine/extraction level only; agent-level calibration curves vs knowable-outcome gold: no | Agent-level Brier/calibration over knowable-outcome scenarios; undecided-honesty ≥80% on contested (depends #2354 verdict vocabulary for the honest "undecided" label) |
| R4 defeat-condition precision (≥70% vs real edges) | Engine semantics exist (decide.py NAND/mitigation; eval-spec R2/R3) | No | Agent states defeat conditions; graded against actual graph NAND/mitigation structure |
| R5 belief-update responsiveness (correct direction ≥90%; over-reaction ≤10%) | Engine: eval-spec B1 (EP responsive ≥90%, flat store 0% — ENGINE property) | Engine level yes (property tests) | Agent-level: position moves correctly when evidence retracts mid-session — the contested leg (in-context LLM can update too); A4 proportional update vs in-context |
| L1 interdependent stream / L2 pseudo-evolution / L3 quality trajectory | W3 harness 7-metric snapshot (recall behavior, #2099 CLOSED — sealed); LongMemEval runner + #2105 500-Q (recall, OPEN); product-success §1 | Recall-side yes; reasoning trajectories no | Trajectory metrics (token convergence, quality slope) — scorers shipped `battery/streams/l1..l3`; real-run #1416 |
| L4 cross-session contradiction / L5 decision drift / L6 distillation | Engine: #1538 bi-temporal + #689 tombstones shipped; eval-spec G1/P6/R4 | Partial substrate only | Agent-level longitudinal legs; **L4/L5 gated on #2349 semantics** → #2524 |
| D1–D4 differential (vs field, adversarial) | W2 write-path rows, vendor rows (⚠️ not re-run) in comparison-systems.md | Comparator runs: none | Six-arm sweep at matched recall + poisoning/Sybil/anchoring robustness — #2525 readiness |

**Naming the delta precisely:** everything in `battery/` + the why-layer suite +
the W3 harness measures *either engine correctness or memory/recall behavior or a
surfaced-context artifact*. What NO shipped instrument measures today is the
**agent-level reasoning outcome with a counterfactual arm at matched recall** —
that is the battery's sole addition, and it is what #1402's #1416 real-run produces
and what #2522/#2523 publish.

---

## 3. Experiment designs per arm (harnesses/corpora; exists vs must be built)

| Arm/tier | Harness + corpus | Counterfactual arms | Build state |
|---|---|---|---|
| Tier-1 probes R1–R5 | `battery/probes/r{1..5}_*.py` + scenario corpus with sealed golds (#1407, CLOSED) | A0/A1/A3/A4 in-run; matched-recall gate `battery/recall/` | **Built**; A4 product-semantics/EP path is #2291 (in flight); rubric/model budget #2292 |
| Tier-2 streams L1–L3 | `battery/streams/l{1..3}_*.py` + interdependent/SEA-Eval corpora (#1407) | A2/A2b/A3 memory backends (D2 narrowed sweep) | **Built**; runnable in #1416 |
| Tier-2 L4/L5/L6 | `battery/streams/l4_cross_session.py`, `l5_drift.py`, `l6_distillation.py` | L5 control = no-graph drift arm; L4 self-referential (surfacing latency over graph growth) | **Built but L4/L5 gated**: L4 needs #2351/#2352 (+#2165 as-of); L5 likely runnable on #1538/#689 — split verified in **#2524** |
| Tier-3 D1–D4 | `battery/differential/d{1..4}_*.py` + adversarial graphs | Six arms; A2/A2b/A3 backends | **Built**; comparator real-backend readiness + matched-recall per-arm data = **#2525** |
| Parity leg | LongMemEval/LoCoMo/MemoryAgentBench/ForgetEval wiring (#1414 CLOSED) | Saturation-parity diagnostics | #2105 500-Q run OPEN (recall row) |

No new harness or corpus needs building for the offense claim — the four residual
children are publication, sequencing, and readiness work on shipped machinery.

---

## 4. Phased plan + complexity (repo fractal convention)

| Phase | Work | Issues | Complexity |
|---|---|---|---|
| P0 — Disposition | Owner decides §0 (A) vs (B); children re-pointed if (B) | #2515 comment | — |
| P1 — Publish the why-layer slice | Run + bless why_suite baseline/receipt; fill comparison-systems why-layer row (annotated fill, §7 discipline) | **#2522** | **standard** |
| P2 — Pre-register the offense row contract | Battery differentiation-profile row schema in comparison-systems.md (semantics, matched-recall citation, verdict look-up via `classify.py`, PENDING rows + annotated-fill rule) | **#2523** | **standard** |
| P3 — Temporal-leg sequencing | Runnable-now matrix (L5 vs L4), sealed DM-9 fixture/gold packs against the #2351/#2352 contract; recommendation to #1416 before its Tier-2 wave | **#2524** | **complex** |
| P4 — Comparator readiness | Arm run-matrix (A1/A2/A2b/A3), matched-recall F1 per runnable arm, A2b go/no-go + pre-registered reduced-arm consequence; feeds #1416 Tier-3 wave | **#2525** | **standard** |
| P5 — Consume #1416 verdict | #1416 real-run → verdict report; rows filled per #2523 contract; claim doc per pre-committed branches | #1416 (epic #1402) + #1419 capstone | complex (owner: #1402) |

Phases P1–P4 are independent of #1416's execution order except P3's and P4's
"before #1416's Tier-2/Tier-3 wave" handoffs — both must land ahead of those waves.

---

## 5. NON-goals / boundary statements

- **vs epic #1402 (incumbent battery):** #2515 does NOT rebuild, re-scope, or
  re-file battery machinery. Probes/streams/differential/arms/recall/report shipped
  (#1406–#1415 CLOSED); execution + verdict = #1416; measurement path/A4
  semantics/rubric-budget = #2284/#2291/#2292 (in flight). Anything those issues
  own is out of bounds for every child here. The residual tail (publication,
  sequencing, readiness) is needed under either disposition (§0).
- **vs #2349 temporal epic + cluster (#2351/#2352/#2354):** #2349 owns the
  RECORD-AXIS SEMANTICS (belief-as-of, asserted-vs-derived, evidence currency,
  uncertainty verdicts). The battery MEASURES agent reasoning outcomes; it never
  implements temporal semantics, and it never reports engine as-of correctness —
  eval-spec R4's flip ("was X believed at t1?") is #2349's correctness target, a
  DIFFERENT measurement from battery L5 (agent re-derives the same decision
  fresh-context). #2524 builds fixtures against the #2349 contract; it does not
  schedule or implement #2349 children, and #2349 has no dependency back onto it.
- **vs #1509 (extractor v3) / #2513 (retrieval surface) / #2514 (wire/operator
  correctness):** those epics improve layer-1/2/3 QUALITY (QA accuracy of answers
  over retrieved context, retrieval surface, operator-edge correctness). The
  battery never measures QA-accuracy, never optimizes retrieval, and never scores
  extraction. **Recall is the control variable** — a battery run pins corpus +
  retrieval config and re-runs the matched-recall gate (#1413) before every sweep;
  recall-improvement PRs from #1509/#2513/#2514 are never battery evidence, and
  mid-epic recall drift is a confound the #2523 row contract must cite (§2 trap:
  QA-acc ≠ R@k ≠ reasoning-outcome rates).
- **vs #2105 (LongMemEval 500-Q):** #2105 owns the recall-parity row. #2522 fills
  only the why-layer row; #2523 defines battery-row shape. Neither touches #2105.
- **vs W3 harness (#2099):** the 7-metric snapshot measures volunteering-memory
  BEHAVIOR (recall layer). The battery measures reasoning outcomes. The harness
  share is machinery only (runner/bless/receipt conventions inherited).

---

## 6. Children created (2026-09-07, issue-creation discipline: O/I/T + affiliation + domain-aware complexity)

| # | Title | Complexity | Phase |
|---|---|---|---|
| #2522 | feat(eval): publish why-layer baseline + receipt and fill comparison-systems why-layer row (W7 fill trail) | standard | P1 |
| #2523 | docs(benchmarks): pre-register battery differentiation-profile row contract in comparison-systems.md | standard | P2 |
| #2524 | feat(eval): sequence battery Tier-2 temporal legs (L4 cross-session, L5 drift) against epic #2349 — runnable-now split + sealed fixtures | complex | P3 |
| #2525 | feat(eval): comparator-arm real-run readiness — A1/A2/A2b/A3 run matrix, matched-recall data, A2b go/no-go (feeds #1416) | standard | P4 |

All: **Team:** epistemic-team · **Epic/Umbrella:** #2515 · fractal Level: task ·
four-layer context: layer-4 REASON, recall as control. Bodies name relates-to
#1402-cluster/#2349-cluster/#1509/#2100/#2106/#2105 and the do-not-duplicate
boundaries of §5.

---

## 7. Open questions for the product owner

1. **Disposition (§0): (A) repoint #2515 as the layer-4 offense epic, or (B) close
   #2515 as a duplicate of #1402 and re-file #2522–#2525 under it?** Highest-risk:
   proceeding under (A) while #1402's #1416 assumes (B) would fork the battery's
   execution ownership; the reverse strands the four children. Needs a decision
   before P1 executes.
2. **A2b Zep/Graphiti budget:** the draft mandates "Zep must be an arm," but vendor
   arms cost real API budget and their run feasibility is unverified (#2525). Is a
   reduced-arm differential with a pre-registered verdict constraint acceptable if
   A2b is out of budget?
3. **First-number posture:** repo convention publishes bad numbers on purpose. The
   first published battery/why-layer numbers may be low (like W2's 0.25/0.0) —
   confirm the offense claim tolerates a low first public row (#2522/#2523 scope).

---

*Companions: `docs/agent-reasoning-eval-battery.md` (design), `docs/epics/1402-eval-battery/` (incumbent epic), `docs/benchmarks/comparison-systems.md` (publication), `docs/epistemic-layer-eval-spec.md` (engine). Children: #2522 #2523 #2524 #2525.*
