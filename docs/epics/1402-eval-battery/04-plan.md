---
title: "Epic Plan — #1402 Agent-Reasoning Eval Battery"
type: decisions
domain: strategy
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-14
aboutSubjects: tortoise
aboutObjects: tortoise
extends: 01-align.md, 02-research-brief.md, 03-scope.md (incl. Integration Surface Map, test-design #1404)
---

# Epic Plan — Agent-Reasoning Eval Battery (#1402)

**Test-design issue:** #1404 (integration-surface map S1–S8 — every section below carries its surfaces)

**Review status:** rewritten after 4-reviewer gate (substeps 1–6: 1 P1 + 7 P2; e2e-coverage: 1 P0 + 3 P1 + 3 P2; e2e-reproducibility: 4 P1 + 3 P2; test-quality: 2 P0 + 4 P1 + 1 P2 — §7 subtotal: 3 P0 + 11 P1 + 7 P2; grand total incl. substeps 1–6: 3 P0 + 12 P1 + 14 P2; coherence review §8: 3 P2 fixed after the rewrite). All fixed in this revision — D2–D4 first-class, L3–L6 + D2–D4 detailed E2Es, failure-path + negative-case tests, reproducibility pins, enum/schema consistency. **Human gate #2 (plan approval):** approved by owner 2026-08-14 ("go") — plan is the approved basis for decomposition.

---

## 1. User Journeys

Personas: **PO** (product owner — verdict consumer, positioning decisions), **Engineer** (builds/runs battery, fixes weaknesses), **Analyst** (designs probes, interprets profile, calibrates).

| Journey | Persona | Entry | Steps | Exit | In-scope coverage |
|---|---|---|---|---|---|
| J1 Run the battery | Engineer | Have corpus + harness config | Configure arms → run Tier-1 probes → run Tier-2 streams → run Tier-3 sweep | Run artifacts (JSON reports per scenario) | 1,2,4,5,6,8 |
| J2 Read the verdict | PO | Verdict report exists | See profile (STRONG/STRUCTURAL/PARITY/WEAK per metric) → see verdict outcome (all 4 states) → see mitigation paths | Positioning decision made or gated | 7 |
| J3 Mitigate a weakness | Engineer | Verdict shows load-bearing WEAK | Pick mitigation path → implement → re-run battery → confirm WEAK → PARITY/STRONG | Updated profile | 9 (re-run loop) |
| J4 Validate a rubric | Analyst | New/changed rubric | Run judge-validation gate (AB+BA, reliability, IRT, stress) → gate passes or blocks; record validation in run artifact | Rubric locked for scoring | 3 |
| J5 Calibrate thresholds | Analyst | Engine change (EP/weights) | Run calibration mode → print deltas → re-lock [cal] table (reviewable change) | Thresholds re-locked, no silent tuning | 8 |
| J6 Onboard an arm | Engineer | New memory backend to compare | Write arm adapter → isolation check (contamination test) → matched-recall probe → add to sweep | Arm included in differential | 5 |
| J7 Run parity leg | Engineer | Runner + dataset versions pinned | Run LongMemEval/LoCoMo/MemoryArena/MemoryAgentBench per arm | Parity table with saturation context | 6 |

**J↔W mapping (explicit):** J1↔W1 · J2↔W4 · J3↔W5 · J4↔W3 · J5↔W6 · J6↔W2+W1 · J7↔W7.

Edge cases covered: empty corpus (harness refuses, CLI exit 5, §6); arm API down (episode flagged, never silently skipped — E2E-1.5); judge gate fail (scoring blocked — E2E-5.1); recall-match failure (INCONCLUSIVE driven — E2E-3.7); non-converged EP (honest UNDEC — E2E-1.3); arm isolation breach (run-level error — E2E-3.6).

## 2. Workflows

| Workflow | Steps | Automation | Manual trigger | Surfaces |
|---|---|---|---|---|
| W1 Battery execution | corpus load (refuse if empty) → arm isolation init → Tier-1 probes (matched pairs) → Tier-2 streams → Tier-3 sweep → artifacts | Full run automation, pinned seeds | Launch command | S1–S4, S7, S8 |
| W2 Matched-recall pre-pass | factual probe subset → top-K F1 (K=5) per arm → symmetric trigger (any arm ≥0.10 F1 below corpus-best) → balanced subset or INCONCLUSIVE (<50%) | Automatic before reasoning battery | None (pre-committed) | S4, S5, S8 |
| W3 Judge validation | per-rubric AB+BA (p<0.05), chance-corrected reliability (≥0.7), IRT item-infit (0.7–1.3), stress set (single-anchor, all-identical anchors, contradictory anchors) → pass/block; validation record → run artifact | Automatic gate before scoring; mid-stream re-validation on rubric change | Analyst triggers on rubric change | S6 |
| W4 Verdict assembly | aggregate profile → classify metrics → apply verdict rule (all 4 branches) → mitigation paths → artifacts list; missing metric → report-incomplete flag (never fabricated) | Automatic from run artifacts | PO sign-off on claim wording | S6, S7 |
| W5 Weakness mitigation loop | pick WEAK → engineer implements → re-run battery → compare profile | Re-run automated | Engineer per weakness | S1–S8 |
| W6 Calibration | engine change → calibration mode prints deltas → re-lock [cal] table | Calibration mode | Analyst on engine change | S2, S8 |
| W7 Parity leg | run released benchmarks per arm (pinned versions; refuse on mismatch) → parity table | Runner automation, pinned versions | Engineer on demand | S5 |

Failure modes documented per workflow: W1 LLM fallback (flagged episode, excluded + counted), empty corpus (refuse, exit 5); W2 partial recall match (INCONCLUSIVE — driven branch E2E-3.7); W3 judge drift (re-validate mid-stream, E2E-5.2); W4 missing metrics (report incomplete, not fabricated — E2E-6.2); W5 mitigation ineffective (weakness stays, verdict stays gated); W6 threshold drift (reviewable table only); **W7 parity leg: dataset version mismatch → refuse to run (no silent upgrade); judge contract drift → re-pin; gold-answer leakage → sealed store (S5 flags)**.

## 3. Prototype (markdown — non-GUI epic)

```
battery/                          # new top-level package (extends tools/longmem_eval patterns, Python)
├── cli.py                        # subcommands: run | parity | calibrate | validate-judge (S8)
├── config/                       # corpus.yaml, thresholds.yaml ([cal] table), arms.yaml, budget.yaml
├── runner/                       # episode executor + trajectory logger + seed pinning (S7)
├── arms/                         # S4
│   ├── base.py                   # ArmAdapter protocol (isolation contract)
│   ├── a0_plain.py  a1_longctx.py  a2_mem0.py  a2b_zep.py  a3_rag.py  a4_tortoise.py
├── probes/                       # Tier-1 R1–R5 (S1–S3, S6)
├── streams/                      # Tier-2 L1–L6 trajectory instrumentation (S1–S3, S6)
├── differential/                 # Tier-3 D1–D4: sweep, longitudinal-spread, feedback-loop, adversarial (S4, S6)
├── judge/                        # judge client + validation gate (AB+BA, reliability, IRT, stress) (S6)
├── recall/                       # matched-recall pre-pass (top-K factual F1, symmetric trigger) (S4)
├── parity/                       # longmem_eval wrapper, locomo wrapper, memoryarena loader, memoryagentbench loader, forgeteval staleness probe (S5)
└── report/                       # profile assembler + verdict rule + mitigation paths (S7)
```

Verdict report shape (the PO-facing artifact) — non-UNIQUE state shown (MECHANISM-NOT-UNIQUE):

```
Profile: metric × arm delta matrix (all 14 families: R1–R5, L1–L6, D2–D4)
  R1  surfaced-rate      A4 0.92 | A2 0.00 | A0 0.00    → STRUCTURAL (mechanism)
  R2  coverage subscore  A4 0.71 | A2 0.44 | A0 0.42    → STRONG (contested, load-bearing)
  R3  Brier              A4 0.18 | A2 0.27 | A0 0.29    → STRONG (load-bearing)
  R4  defeat-condition   A4 0.80 | A2 n/a  | A0 n/a     → STRUCTURAL (mechanism)
  R5  update-correct     A4 0.82 | A2 0.55 | A0 0.57    → PARITY
  L1  interdependent     A4 0.88 | A2 0.61 | A0 0.49    → STRONG (load-bearing)
  L2  token trajectory   A4 conv  | A2 flat | A0 n/a    → STRONG (⚠️ provisional gate)
  L3  quality slope      A4 +0.09 | A2 +0.01 | A0 0.00  → STRONG (load-bearing)
  L4  cross-session      A4 1.00  | A2 0.40 | A0 0.00   → STRONG (load-bearing)
  L5  decision-drift     A4 0.91  | A2 0.72 | A0 0.58   → STRONG (load-bearing)
  L6  distillation       A4 0.96  | A2 n/a  | A0 n/a    → STRONG (load-bearing)
  D2  pseudo-evol spread A4 conv  | A2 3.4× | A2b 2.1× | A3 2.8×  → STRONG
  D3  feedback fix-rate  A4 0.66  | A0 0.41             → STRONG (load-bearing)
  D4  adversarial        A4 0.88  | A2 0.21 | A0 0.13   → STRONG (load-bearing)
Verdict: MECHANISM-NOT-UNIQUE (no empirically-contested STRONG on load-bearing axis)
Differentiators: none contested — R1/R4 structural only
Weaknesses: R5 PARITY (mitigation: EP damping re-calibration, re-run in loop)
Matched recall: F1 0.91 all arms (K=5, corpus-best 0.92, no trigger)
Artifacts changed on non-UNIQUE: positioning copy, product-success-eval claim section, graph-as-memory annex
```

**Gate:** prototype matches journeys; all report states represented (UNIQUE / MECHANISM-NOT-UNIQUE / WEAK-UNMITIGATED / INCONCLUSIVE); all 14 metric families in the matrix; non-GUI so design-system N/A.

## 4. Data Model

> **Data Model Research Notes (justified skip, re-anchored):** no new DB schema — the battery reads/writes the existing Tortoise graph via SDK and ONTOLOGY is unchanged (scope Ontology axis research + battery spec; complexity Ontology = low). The three JSON artifact schemas below are designed here (plan-level decisions, not research-covered). No external queries fired (issue #231 D11 justified-skip: scope Ontology axis + battery spec §0).

No graph-schema changes. Three new artifact schemas (files, not DB):

| Entity | Fields | Constraint |
|---|---|---|
| `scenario.json` (corpus) | id, tier (probe/stream/differential), family, arm-compatible prompt pack, gold answer(s), planted-contradiction pairs (R1/L4) **with injection-turn field k**, evidence-tier scripts (R3), attack_type (D4: poisoned/sybil/echo_chamber/flapping/anchoring), task_type | id unique; gold answers sealed in a gitignored store (no reader leakage, S5/S8); corpus split: train/waves/held-out (L2/L3 contamination control); k pinned per scenario |
| `run_artifact.json` (per scenario) | run_id, seed, arm, scenario_id, episode trace (turns, tool calls, tokens), metric values, model-call outcomes enum, ep_outcome (converged | non_converged | undec), timestamps | run_id = seed+arm+scenario; **model-call outcomes ∈ {ok, rate_limited, timeout, fallback_cached, failed}** — fallback/failed episodes excluded from aggregates, reported as count (S3 flag); ep_outcome recorded per S2 (honest UNDEC never fabricated) |
| `profile.json` (verdict) | matrix: metric (all 14: R1–R5, L1–L6, D2–D4) × arm {value, delta, classification, load_bearing}, verdict {outcome, differentiators, weaknesses, mitigation_paths, artifacts_changed}, matched_recall {f1_by_arm, trigger_fired, subset_pct}, report_status (complete | incomplete_missing_metrics) | classification ∈ {STRONG, STRUCTURAL, PARITY, WEAK}; verdict ∈ {UNIQUE, MECHANISM-NOT-UNIQUE, WEAK-UNMITIGATED, INCONCLUSIVE}; report_status incomplete blocks claim shipping |

Tortoise graph usage (S1/S2): R1/R4/R5 probes write/read via SDK (create_point/create_operator/mitigate/supersede/compute_confidence); L-streams accumulate across sessions in a per-arm Tortoise namespace (isolation, S4).

**Gate:** model supports all workflows (W1–W7 map to scenario/run_artifact/profile); integrity constraints at schema level (enums incl. `failed` + `non_converged`/`undec`, sealed golds, k pinned); no RLS needed (internal tooling).

## 5. Architecture

> **Architecture Research Notes (justified skip, re-anchored):** the brief's §Workflow Pattern Research (5 canonical workflows) + §Tech Stack Research (arms, runners) + **§UX Pattern Research (judge-validation methodology: AB+BA, reliability, IRT, stress)** + the scope-doc Integration Surface Map (S1–S8) cover the architecture decisions. No external queries fired (issue #231 D11 justified-skip).

```
battery/                     # new top-level package (alongside tools/longmem_eval, reuses its runner/judge patterns)
├── cli.py                   # subcommands: run | parity | calibrate | validate-judge (S8)
├── config/                  # corpus.yaml, thresholds.yaml ([cal] table), arms.yaml, budget.yaml (S8)
├── runner/                  # episode executor + trajectory logger + seed pinning (S7)
├── arms/                    # ArmAdapter protocol + 6 implementations (S4)
├── probes/                  # R1–R5 modules; each: scenario pack + scorer (S6) + gate wiring
├── streams/                 # L1–L6 modules; trajectory instrumentation (tokens, steps, strategy-reuse)
├── differential/            # D1 sweep, D2 longitudinal-spread (reuses streams on A2/A2b/A3), D3 feedback-loop, D4 adversarial pack (S4, S6)
├── judge/                   # judge client + validation gate (AB+BA, reliability, IRT, stress) (S6)
├── recall/                  # matched-recall pre-pass (top-K factual F1, symmetric trigger) (S4)
├── parity/                  # longmem_eval wrapper, locomo wrapper, memoryarena loader, memoryagentbench loader, forgeteval staleness probe (S5)
└── report/                  # profile assembler + verdict rule (all 4 branches) + mitigation paths (S7)
```

**Component boundaries:**
- **Arms are sealed adapters.** Each arm implements `ArmAdapter.retrieve(context) -> memories` + `ArmAdapter.record(context, item)`; the harness never reaches into an arm's internals. Isolation contract: per-arm namespace (Tortoise per-arm context; vendor per-project keys). Cross-arm contamination is a run-level error (S4 race-flag; detection test E2E-3.6).
- **Judges are gated, never raw.** All LLM-judge scoring flows through `judge/gate.py`; a rubric with no valid validation record blocks scoring (S6 conditional-guard flag). Validation records persist to run artifacts (E2E-5.1).
- **Trajectories are the source of truth for longitudinal metrics.** L2/L3/D2 metrics compute from `run_artifact.json` trajectory fields only — no re-inference at report time (determinism, S7).
- **Recall matching is a pre-pass, not a post-hoc filter.** `recall/` runs before the reasoning battery; its outcome (matched | INCONCLUSIVE) is recorded in profile.json and immutable per run (W2).
- **Failure isolation:** arm API failures (S4 timeout/429/503) mark the episode `fallback_cached` or `failed` — excluded from aggregates with a count reported; the battery never silently retries a failed episode into the data (S3 flag; injection test E2E-1.5).
- **Honest EP outcomes:** `compute_confidence` non-convergence / contested variance records `ep_outcome=non_converged|undec` in run_artifact; the R3 scorer counts honest-UNDEC toward AC-R3, never fabricates a confidence (S2 flag; E2E-1.3).
- **Determinism:** all arms run same model, temperature 0, pinned seeds; EP runs pinned per epistemic-layer §0.

**Deployment:** internal tooling, runs locally/CI (no new services). Compute budget: ~500–1,000 episodes ≈ within #1144 eval budget (align fix-5). Docker tier for FalkorDB when available; FalkorDBLite hermetic tier otherwise (epistemic-layer §0.3).

**Gate:** boundaries clean (sealed adapters, gated judges, pre-pass recall, honest EP); interfaces well-defined (ArmAdapter/Scorer/Report contracts); failure modes addressed (silent-fallback, arm isolation, judge gating, INCONCLUSIVE, EP-UNDEC). ✓

## 6. Interfaces

| Interface | Contract | Error responses | Versioning |
|---|---|---|---|
| `ArmAdapter` | `retrieve(context: AgentContext) -> list[Memory]`; `record(context, item)`; `isolation_namespace() -> str` | raise `ArmUnavailable` (timeout/429/503) — never return partial memories silently | Protocol v1; new arms implement to spec |
| `Scorer` | `score(episode_trace, rubric_id) -> Score{metric, value, evidence}` via gated judge | `JudgeGateBlocked` if rubric unvalidated; `ScoreUnavailable` on judge failure (episode excluded, counted); records `ep_outcome` for R3 | Rubric JSON schema v1 (anchors + items) |
| `recall.match()` | `match(corpus, arms) -> {f1_by_arm, trigger_fired, subset_pct}` | INCONCLUSIVE result object (not exception) — the verdict branch, not a crash | Pre-registered; result immutable per run |
| `report.assemble()` | `assemble(run_artifacts, thresholds) -> Profile` (all 14 metric families) | missing metrics → report_status=incomplete (never fabricated) | Profile schema v1 |
| CLI | `battery run --tier 1|2|3 --arms ...`; `battery parity`; `battery calibrate --print`; `battery validate-judge --rubric <id>`; `battery report` (profile + verdict assembly; logic #1415, dispatch #1406) | exit codes: 0 ok, 2 gate-blocked, 3 inconclusive, 4 arm-failed, **5 empty-corpus (refuse to start)** | Subcommand flags versioned in help |
| `parity/` wrappers | pinned dataset version per runner (enumerated: LongMemEval commit X, LoCoMo vY, MemoryArena HF rev Z, MemoryAgentBench rev W — exact values locked at implementation); judge contract pinned | version mismatch → refuse to run (no silent upgrade) | Dataset version in run_artifact |

Error-responses defined for every interface; contract-first — child issues implement to these signatures (test-design S1–S8 map). Contracts referenced by constant/enum name (schema enums §4), not literal strings, to survive renames (test-quality P2-6).

**Gate:** contracts complete; error responses defined (no silent fallbacks anywhere); versioning clear (protocol v1, pinned datasets incl. empty-corpus exit 5). ✓

## 7. Detailed E2E Test Cases

Fleshes out the 7 high-level E2Es from scope into executable scenarios (setup / steps / assert with pinned numbers). Thresholds are [cal]-locked and read from `thresholds.yaml`; assertions reference schema enums (§4) not literals.

### Tier 1 (scope E2E-1)

**E2E-1.1 — Tier-1 battery produces gate values, not just emission**
**Setup:** corpus v1 (≥60 scenarios: 20 decision, 15 contradiction-pair with pinned k=5, 15 calibration-with-known-outcome, 10 retraction); thresholds [cal]-locked; arms A4 + A0; seed S; temp 0.
**Steps:** `battery run --tier 1 --arms a4,a0`.
**Assert:** per-scenario `run_artifact.json` exists with run_id = seed+arm+scenario; **each probe's value is checked against its AC gate** (R1: surfaced ≥90%, flip-flop ≤10%, FP ≤5%; R2: coverage subscore delta ≥1.5× vs A0 AND Tier-1 mechanism gate ≥80% of decisions reach 3+ Challenge/Deepen cycles; R3: Brier ≤ A0 − 0.05 AND honest-undecided ≥80% AND confident-wrong ≤10%; R4: defeat-condition precision ≥70% AND ≥1 real defeat condition per decision; R5: correct-direction ≥90%, over-reaction ≤10%); thresholds read from thresholds.yaml (behavioral boundary assertion, not filename coupling); zero fallback/failed episodes or count reported <5%.

**E2E-1.2 — Contradiction pair fires (R1)**
**Setup:** the 15 contradiction scenarios, k=5 fixed (injection-turn field), N ≥ 20 runs (15 × ≥2 seeds).
**Assert:** A4 surfaces conflict within 1 turn of turn-k introduction ≥90% of runs; silent flip-flop ≤10%; false-positive ≤5% on matched non-contradictory controls.

**E2E-1.3 — EP non-convergence → honest UNDEC (R3 negative branch)**
**Setup:** loopy/contested graph scenarios (odd NAND triangle, balanced contradiction per epistemic-layer P8/P9).
**Assert:** run_artifact records `ep_outcome ∈ {non_converged, undec}`; agent states "undecided" ≥80% of contested episodes; confident-wrong ≤10% (AC-R3); no fabricated confidence value anywhere.

**E2E-1.4 — Empty corpus guard**
**Setup:** corpus with 0 scenarios.
**Assert:** harness refuses to start, CLI exit 5, no profile.json fabricated.

**E2E-1.5 — Arm failure injection (negative path)**
**Setup:** healthy corpus; inject `ArmUnavailable` (timeout/429/503) on a subset of episodes (e.g., 15%).
**Assert:** affected episodes marked `fallback_cached`/`failed`; excluded from metric aggregates; count reported in artifact; verdict/profile still assembles without corruption; no silent retry into data.

### Tier 2 (scope E2E-2)

**E2E-2.1 — Pseudo-evolution detection (L2)**
**Setup:** 6 families × 3 reps, held-out family; fresh context per session; A4 vs A0; baseline defined = A4's own SR on that family from the prior wave (stored in run_artifact).
**Assert:** A4 token trajectory monotone-downward by rep 3 (≥30% reduction, [cal]); strategy-reuse rate >0 and rising; held-out family SR ≥ A4 prior-wave SR on that family (contamination control); A4 tokens flat while graph grows → pseudo-evolution FAIL reported (⚠️ provisional per single-source label).

**E2E-2.2 — Interdependent subtasks (L1)**
**Setup:** MemoryArena-style stream: 10 tasks, later tasks depend on earlier sessions' decisions.
**Assert:** composite success ≥0.85 (A4) vs ≤0.5 (A0) [cal]; recall-before-re-derive ≥90%; re-derivation tool calls ≥5× fewer than control; **provenance criterion (defined): ≥1 stored point id appears verbatim in the answer AND a confidence value ∈ [0,1] present per cited point** (reuses recall matcher).

**E2E-2.3 — Reasoning-quality trajectory (L3 — the core claim)**
**Setup:** waves split (train/waves/held-out); Tier-1 probes as recurring checkpoints across ≥3 waves; harder held-out variants per wave.
**Assert:** A4 reasoning-quality slope > 0 across ≥3 waves (rubric-scored: counter-argument coverage, Brier, contradiction-surfacing rate, decision correctness); A0 slope ≈ 0 (±[cal] tolerance); held-out wave variants clear the contamination control (AC-L3).

**E2E-2.4 — Cross-session contradiction accumulation (L4)**
**Setup:** plant A in session 1, ¬A in session 5, decision/query in session 6+.
**Assert:** conflict surfaced by session N+1 (100%, AC-L4); surfacing latency ↓ as graph density grows; resolution via supersede with provenance.

**E2E-2.5 — Decision-drift resistance (L5)**
**Setup:** decision D at t0; re-derive fresh-context at simulated t+7d, t+21d (≥10 interleaved sessions, ≥5 consolidation runs); A4 vs A0.
**Assert:** A4 decision-consistency ≥90%, rationale-consistency ≥80% (rubric: same criteria, same counter-arguments); A0 drift ≥30% (calibration floor, product-success §2); hallucinated-rationale rate ≤10% A4 vs ~100% control.

**E2E-2.6 — Distillation fidelity (L6)**
**Setup:** after N sessions, distilled-graph arm vs raw-sessions arm; Tier-1 rubric (R1–R5) scored on both.
**Assert:** reasoning-fidelity = distilled-score / raw-score ≥ 0.95 [cal]; no contradiction pair dropped below surfacing threshold after consolidation (G1 applied post-consolidation).

### Tier 3 (scope E2E-3)

**E2E-3.1 — Differential sweep renders the full profile**
**Setup:** 6 arms × same battery; matched-recall pre-pass (K=5 factual F1); thresholds [cal]-locked.
**Assert:** profile.json matrix covers **all 14 metric families (R1–R5, L1–L6, D2–D4)** × every arm; each classified STRONG/STRUCTURAL/PARITY/WEAK with load-bearing flag; matched_recall.f1_by_arm + trigger status recorded.

**E2E-3.2 — Verdict rule — all four branches asserted**
**Assert (from profile.json):** UNIQUE iff ≥1 STRONG-on-load-bearing AND 0 load-bearing WEAK without mitigation path; MECHANISM-NOT-UNIQUE iff no STRONG-on-load-bearing (≥1 STRUCTURAL); WEAK-UNMITIGATED iff load-bearing WEAK without mitigation path; INCONCLUSIVE iff recall trigger fired AND subset <50%. Each branch constructible from a profile fixture. **R2 mechanism-gate subscore (≥80% reach 3+ cycles) reported as process-fidelity diagnostic; excluded from verdict classification (AC-D1 counts R2's judged subscore only — spec §R2).**

**E2E-3.3 — D2 longitudinal spread**
**Setup:** Tier-2 streams (L1/L2) on A2/A2b/A3 with their own backends; A4 same streams.
**Assert:** A4 token trajectory converges downward; A2/A2b/A3 show memory growth without behavior change — pseudo-evolution spread ≥2× [cal] vs A4 (AC-D2; lit 31.2× ⚠️ single-source).

**E2E-3.4 — D3 feedback integration (OPT-BENCH-style)**
**Setup:** loop: task → structured feedback → harder repeat, ≥5 iterations; A4 vs A0.
**Assert:** A4 fix-rate ≥ A0 by calibrated margin (AC-D3); per-iteration improvement monotone; feedback filed as evidence → propagation → behavior change visible in trajectory.

**E2E-3.5 — D4 adversarial differential**
**Setup:** hostile-input pack (attack_type field): 2% poisoned retrievals, Sybil floods (100 T4 vs 1 T0), echo-chamber rings, flapping, outdated-claim anchoring; all arms.
**Assert:** A4 rejects poisoned claims ≥80% at high confidence (AC-D4); T0 > 10×T4 ordering survives EP; anchored-but-superseded beliefs abandoned not persisted; comparators' rejection rates reported (expected low).

**E2E-3.6 — Arm isolation breach detection**
**Setup:** share memory state between two arm namespaces mid-run (engineered contamination).
**Assert:** contamination detected; run flagged as run-level error; affected deltas excluded; no silent inclusion (S4 flag).

**E2E-3.7 — INCONCLUSIVE driven branch**
**Setup:** arms with divergent recall F1 (one arm ≥0.10 F1 below corpus-best → trigger fires; balanced subset <50%).
**Assert:** profile.json carries matched_recall.trigger_fired=true, subset_pct<50%; verdict=INCONCLUSIVE; report does not fabricate classifications; claim-not-shipped consequence documented (AC-D1 outcome d).

### Parity + gates (scope E2E-4, E2E-5)

**E2E-4.1 — Parity leg on released benchmarks**
**Setup:** LongMemEval (commit pinned at implementation), LoCoMo (vY), MemoryArena (HF dataset+rev pinned), MemoryAgentBench (rev pinned), **ForgetEval-class staleness/drift probe (pinned rev — scope in-scope #6)** — exact values recorded in run_artifact; 6 arms; **methodology-unchanged check: judge rubric id + reader prompt hash identical to the #1144 baseline record (stored hash, not "unchanged" prose)**.
**Assert:** per-benchmark parity table incl. staleness/drift probe (supersession-vs-stale answers, per research brief Strategy Context); runner refuses to run on version mismatch (interface §6); saturation context cross-referenced to published baselines.

**E2E-5.1 — Judge validation gate**
**Setup:** rubric R2 without a validation record.
**Assert:** scoring attempt raises JudgeGateBlocked (behavioral: scoring blocked); AB+BA position bias p<0.05, chance-corrected reliability ≥0.7, **IRT item-infit in [0.7, 1.3]**, **stress set passes: single-anchor rubric, all-identical anchors, contradictory anchors each with explicit pass criterion** → rubric locked; failing rubric blocks scoring (both guard sides); **validation record (p-values, reliability, IRT, stress outcomes) persisted in run_artifact referenced by rubric id**.

**E2E-5.2 — Judge drift mid-stream**
**Setup:** rubric changed mid-long-stream (e.g., anchors modified at wave 2).
**Assert:** re-validation gate fires; rubric re-blocks or re-locks before further scoring; episodes scored under the stale rubric flagged (S6 drift flag).

### Verdict + determinism (scope E2E-6, E2E-7)

**E2E-6.1 — Verdict report filed with falsification branch**
**Setup:** profile.json from a completed E2E-3.1/3.2 run (or fixture); falsification-branch texts pre-committed at `docs/agent-reasoning-eval-battery.md` §6 (spec).
**Steps:** `battery report` → assert file at docs/<name>.md.
**Assert:** AC table lists **all 14 metric families × every arm with measured values** (or report_status=incomplete); outcome + differentiators + weaknesses + mitigation paths; artifacts_changed enumerated (positioning copy, product-success-eval claim section, graph-as-memory annex) per the pre-committed branch.

**E2E-6.2 — Missing-metrics incomplete branch**
**Setup:** drop one scorer (e.g., R3) from a run.
**Assert:** report_status=incomplete_missing_metrics; no fabricated values; claim shipping blocked (report_status gate).

**E2E-7.1 — Determinism**
**Setup:** same run twice, seed S.
**Assert:** metric values identical within tolerance |Δ| ≤ 1e-6 (per-metric epsilon in thresholds.yaml; compared across the two run_artifact.json files); calibration mode prints deltas without asserting (re-lock is a reviewable table change).

**E2E-7.2 — Weakness mitigation loop (scope item #9, J3/W5)**
**Setup:** verdict WEAK-UNMITIGATED on R5 (fixture); mitigation path documented.
**Steps:** engineer implements mitigation → re-run battery.
**Assert:** R5 reclassified (PARITY/STRONG) OR the weakness stays WEAK with the verdict still gated — no silent flip of the verdict rule.

**Parent→child map (explicit, §8 reference):** E2E-1←{1.1,1.2,1.3,1.4,1.5} · E2E-2←{2.1,2.2,2.3,2.4,2.5,2.6} · E2E-3←{3.1,3.2,3.3,3.4,3.5,3.6,3.7} · E2E-4←{4.1} · E2E-5←{5.1,5.2} · E2E-6←{6.1,6.2} · E2E-7←{7.1,7.2}.

## 8. Coherence Review + Risk Analysis

**Cross-substep consistency checks:**
- Journeys J1–J7 ↔ Workflows W1–W7 (explicit J↔W map, §1) ↔ sections 4–6 (each workflow's surfaces resolve to interface contracts) ✓
- E2E detailed (25 scenarios) ↔ scope high-level E2E-1…7 via the parent→child map above (full coverage, no orphans) ✓
- AC coverage: all 14 metric families (R1–R5, L1–L6, D2–D4) + the D1 verdict-rule AC mapped to ≥1 detailed E2E (R1: 1.1/1.2; R2: 1.1; R3: 1.1/1.3; R4: 1.1; R5: 1.1; L1: 2.2; L2: 2.1; L3: 2.3; L4: 2.4; L5: 2.5; L6: 2.6; D1: 3.1/3.2; D2: 3.3; D3: 3.4; D4: 3.5) ✓
- Data model enums (STRONG/STRUCTURAL/PARITY/WEAK; UNIQUE/MECHANISM-NOT-UNIQUE/WEAK-UNMITIGATED/INCONCLUSIVE; {ok,rate_limited,timeout,fallback_cached,failed}; ep_outcome) ↔ verdict rule in battery spec §6 ↔ align fix-1/fix-3 ↔ scope E2E-3/6 ✓
- Matched-recall (K=5, symmetric trigger ≥0.10-F1-below-corpus-best, <50% → INCONCLUSIVE) identical across align, spec, scope, plan §2/§6 ✓
- Arm set A0/A1/A2/A2b/A3/A4 identical across spec arm table, scope, plan §3/§5 ✓
- [cal] discipline (print-don't-tune) in spec §8.3, scope §8, plan W6/E2E-7.1 ✓
- Test-design surfaces S1–S8 referenced in every section that touches them ✓ (issue #1404)
- D-modules present in prototype (§3), architecture (§5), data model (§4 attack_type), E2E (3.3–3.5) — P1-1 closed ✓

**Risks:**

| Risk | Severity | Mitigation |
|---|---|---|
| LLM-judge reliability on task/tool rubrics (2606.29920) | High | Judge validation gate (E2E-5.1) with pinned IRT/stress criteria; pre-registered rubrics; mid-stream drift re-validation (E2E-5.2) |
| Silent LLM fallbacks corrupt deltas | High | Model-call outcome enum incl. failed; fallback/failed episodes excluded + counted (S3 flag, E2E-1.5) |
| Arm isolation breach (memory contamination) | High | Sealed adapters + per-arm namespace; engineered contamination test (E2E-3.6); run-level error |
| Corpus leakage (gold answers to reader) | High | Sealed gold store; waves/held-out splits; pinned dataset versions (S5); reader-prompt hash check (E2E-4.1) |
| Falsification outcome (claim fails) | Medium | Pre-committed branches (MECHANISM-NOT-UNIQUE / WEAK-UNMITIGATED / INCONCLUSIVE); retention story independent |
| Recall mismatch → INCONCLUSIVE | Medium | Symmetric trigger pre-committed; driven test (E2E-3.7); re-scope comparator branch defined |
| Token-trajectory gate single-source (SEA-Eval) | Medium | ⚠️ provisional label; corroboration sought; gate stays but flagged |
| Compute cost (500–1,000 episodes) | Medium | Batch scenario setup (N+1 flag); within #1144 budget; budget.yaml guard |
| EP calibration discipline violated | Medium | [cal] table reviewable-only; calibration mode prints (W6); ep_outcome honest-UNDEC (E2E-1.3) |
| Report fabricated on missing data | Medium | report_status=incomplete gate (E2E-6.2); never fabricated |

**Improvement opportunities:** (1) after a stable UNIQUE verdict, the deferred adaptive-test generator (SEAL-style) reuses the harness; (2) the profile report doubles as the sales evidence artifact — presentation polish deferred to the claim doc; (3) parity-leg staleness probes (ForgetEval-class) extend S5 without architecture change; (4) D3 feedback loop can seed the adaptive-test generator later.

**Gate (FINAL):** plan internally consistent (mappings verified above); all 14 metric families + D1 verdict-rule AC covered by detailed E2Es; risks identified + mitigated; ready for decomposition (epic-decompose). Test-design #1404 recorded.
