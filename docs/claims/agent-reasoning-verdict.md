# Agent-Reasoning Eval Battery — verdict report (E2E-6.1, issue #1416)

**Run date:** 2026-09-10 · **Harness:** `main` `493143070` · **Attempt:** `/tmp/run1416-a0q/20260910-<ts>/`
**Command:** `battery run --tier 1 --arms a0 --executor real --seed 7 --scorer battery.probes.r1_contradiction --scorer battery.probes.r3_calibration --scorer battery.probes.r4_defeat --scorer battery.probes.r5_update`
**Run mode:** real (`deepseek/deepseek-v4-flash`, temp 0) · **Spend:** 1.071727 USD · **Exclusions:** 5/78 (6.4 %) · **Exit code:** 0

---

## 1. Verdict

| field | value |
|---|---|
| `verdict.outcome` | **MECHANISM-NOT-UNIQUE** — *the rule's fallback branch, NOT a substantive finding (see §2)* |
| `report_status` | **`incomplete_emitter_gap`** — 1 of 14 families measured |
| `differentiators` | none |
| `weaknesses` | none |
| `mitigation_paths` | none recorded |
| `artifacts_changed` | positioning copy · product-success-eval claim section · graph-as-memory hypothesis annex |

**The uniqueness claim does NOT ship.** Per the pre-committed rule (`docs/agent-reasoning-eval-battery.md` §6) a report whose `report_status` is incomplete blocks claim shipping, and per the plan's `report_status` contract the profile is reported in all outcomes — the diagnostic value here *is* the profile and its documented gaps.

> ⚠️ **Read `MECHANISM-NOT-UNIQUE` as "no data", not as "the mechanism is not unique."** The verdict function has no NO-DATA outcome; with zero measured load-bearing families and zero structural wins it falls through to `MECHANISM-NOT-UNIQUE`. The measurement never happened: R1/R3/R5 are unmeasurable on the real lane until #2740 lands. This hazard (a no-data profile rendering as a substantive branch) is recorded as a follow-up; the `report_status` field is the honest carrier.

## 2. Why there is no measurement — measured root cause, not an estimate

`family_R1.json` (`surfaced-rate`, `false-positive-rate`), `family_R3.json` (`brier`) and `family_R5.json` (`correct-direction-rate`) each report `insufficient_n` with **empty** value lists; per-episode `metric_values` is `{}` for all 78 episodes. The per-episode `emitter_gap` explains exactly which truth fields are missing:

| episodes | gap | blocks |
|---|---|---|
| 15 | `["injection_turn"]` | R1 surfaced-rate |
| 22 | `["confidences", "outcomes"]` | R3 brier |
| 10 | `["update_correct_direction"]` | R5 correct-direction-rate |
| 31 | none (truth fields conditional / not applicable to that episode class) | — |

These fields are the **Task-9 executor/derive leg**, which is not implemented. The harness behaves correctly and honestly: `battery/runner/probe_scorer.py` turns a gapped expected-coverage check into the no-data sentinel rather than scoring an uncovered log, and its own contract states the absence "is an honest gap … never a silent pass and never a probe-side default measured as if real". Filed as **#2740** with the field-by-field sources (`injection_turn` = scenario-authored ¬A turn; `false_positive` = log-derived control verdict; `confidences`/`outcomes` = per-decision confidence vs sealed gold outcome — judge-leg semantics; `update_correct_direction` = stated update vs sealed retraction gold).

## 3. Metric families × arms (all 14, honest status)

| family | a0 (control) | a4 (product arm) | status |
|---|---|---|---|
| R1 contradiction surfacing | `insufficient_n` | not run | blocked by #2740 (`injection_turn`, `false_positive`) |
| R2 coverage | not scored | not run | judge-gated (rubric leg) |
| R3 calibration (Brier) | `insufficient_n` | not run | blocked by #2740 (`confidences`, `outcomes`) |
| R4 defeat precision | **measured, n=5, all 0.0** | not run | only derive-emittable family today |
| R5 update direction | `insufficient_n` | not run | blocked by #2740 (`update_correct_direction`) |
| L1–L6 (Tier-2 stream) | not run | not run | requires the Tier-2 temporal legs |
| D2–D4 (Tier-3 differential) | not run | not run | requires comparator arms + measured families |

`families_measured / families_expected = 1 / 14` → `report_status = incomplete_emitter_gap`.

**Matched-recall:** **not evaluated.** `battery/recall/matcher.py` (`match_recall`) has no production call site, so the INCONCLUSIVE branch has no producer (#2525, indicator-2). It is not claimed either way.

## 4. What the run *did* establish (the harness leg)

The E2E-1.1 real leg exists to prove the battery runs the **real** product path end-to-end, honestly, on real model calls. It does:

- 78 real episodes executed (write + read + envelope + EP read-out), 73 valid, `exit_code 0`, spend metered and inside cap (`budget_stopped: false`).
- **Exclusion rate 6.4 %** (5/78) against the E2E-1.1 <5 % target — improved from **43.6 %** (34 excluded / 44 valid of 78) on the pre-fix harness by three fixes: robust envelope extraction (#2697), the context-bearing per-turn corrective repair (#2717), and the measured 480 s episode deadline (#2721). 34 turns were recovered by corrective repair.
- Residual exclusions, both understood: `cal-010`, `cal-012` — the episode did not yield a conforming envelope (`envelope.position is required and non-empty`) and excluded on the schema gate; the excluded path records a single synthetic FAILED turn, so the artifact does **not** evidence how many model attempts were made or what the model wrote — the envelope contract simply has no representation for "no position" (tracked on #2702); `lp-001`, `lp-006`, `lp-011` — long-prompt episodes exceeding 480 s while valid `lp` episodes measured 80–302 s, i.e. a genuinely slow tail rather than a hang (the deadline class, whose measured basis is #2721).

## 5. Falsification branches (pre-committed, spec §6)

| branch | applies here? |
|---|---|
| **UNIQUE** (≥1 STRONG load-bearing, no serious weakness) | No — nothing measured |
| **MECHANISM-NOT-UNIQUE** (only STRUCTURAL wins) | Returned by the rule as a **fallback**, void of evidence (§1 warning) |
| **WEAK-UNMITIGATED** (load-bearing WEAK without mitigation) | No — nothing measured |
| **INCONCLUSIVE** (matched-recall regime failed) | Not evaluated (§3) |

## 6. Blockers and next steps, in order

1. **#2740** — implement the derive/gold truth-emission leg so R1/R3/R5 measure on the real lane.
2. Re-run the tier-1 leg **`--arms a4,a0`** at that harness (the profile then carries real numbers for both arms). The a4 leg was deliberately **not** run here: at this harness it would be equally no-data, so it would buy no measurement.
3. `battery report` → verdict → re-file this document per the branch that the measured profile supports.
4. **#2702** — the `cal`/`bct` empty-position envelope contract (the long-prompt deadline class belongs to the #2721 lane).
5. **#2525** — matched-recall pre-pass so the INCONCLUSIVE branch has a producer.

## 7. Reproduction

```bash
export TORTOISE_TEST_CARVE_OUT=1 OPENROUTER_API_KEY=...
python -m battery.cli run --tier 1 --arms a0 --executor real --seed 7 \
  --scorer battery.probes.r1_contradiction --scorer battery.probes.r3_calibration \
  --scorer battery.probes.r4_defeat --scorer battery.probes.r5_update \
  --out /tmp/run1416-a0q
python -m battery.cli report --out /tmp/run1416-a0q
```

---

*Related: #1416 (this report), #2740 (derive/gold emission leg), #2702 (envelope contract v bct/cal families), #2525 (matched-recall pre-pass), #2717 / #2721 / #2697 (harness fixes that moved the exclusion rate 43.6 % → 6.4 %).*
