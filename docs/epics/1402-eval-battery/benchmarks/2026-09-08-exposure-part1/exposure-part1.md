# Exposure Part 1 — 2026-09-08 (issue #2284 Task 8)

Budget-guarded REAL smoke (decision/contradiction/calibration/retraction ×
a0/a4) + judge-leg agreement + hermetic liveness/oracle legs. Purpose:
retire the 4 executor risks on measured data BEFORE Task 9's executor
transport (plan Task 8). All spend inside the budget.yaml probe sub-cap
($3.00) + judge reserve ($1.50); receipts committed beside this file.

## Receipts
- `probe_manifest.json` — pinned model block (deepseek/deepseek-v4-flash,
  openrouter, temp 0 — decision a) + per-call usage
- `probe_tokens.json` — deliberation per-episode token table (n=8)
- `records.json` — judge-leg validation record (rubric r2-coverage)
- `transcripts/` — sample per-episode real deliberation transcripts

## 4-risk verdicts

### Risk 1 — R1 vacuity delta (does the instrument see surfaced-vs-not?)
**RETIRED (hermetic oracle leg + part-2 smoke pending).** The real probe
scorers register a planted effect at MAX strength (surfaced-rate 1.0,
flip-flop 0.0, FP 0.0) AND register absence as the 0.0 floor — never
vacuous (`battery/exposure/oracle.py`, locked in
tests/test_battery_exposure_oracle.py). The full surfaced-vs-not real
differential (agent-filed NAND per episode) is Task-9 executor work; the
instrument side of the risk is retired.

### Risk 2 — EP reachability (agent-filed NAND moves compute_confidence)
**RETIRED (measured on the REAL product path).** Synthetic EP smoke graph
(ct-001 seed_mode): agent files a TRUE NAND (two high-cred contradictory
evidence points + closed-set NAND through the product `arm.record`) →
the target's EP posterior moved **0.75 → 0.6802 (Δ −0.0698)**, magnitude
≥ the [cal] ep-variance row **0.04** (read from thresholds.yaml, never a
literal); ep outcome contested/converged, affected_count 2; the moved
value is visible on the NEXT product retrieve. Locked in
tests/test_battery_exposure_liveness.py (`battery/exposure/liveness.py`).

### Risk 3 — judge signal (arm-neutral rubric items discriminate)
**RETIRED (real two-model validation on this smoke's deliberation
text).** Judge pair claude-opus-5 × qwen3-235b-a22b (temp 0) over the 8
exposure renders on the validated r2-coverage rubric:
**retest 1.00 · raw agreement 0.943 · Gwet AC1 0.928 · κ 0.718** (both
bars cleared on this pool), gold ✓, stress all green. Judgment spend
metered against the judge reserve and stopped on it (fail-closed).

### Risk 4 — token economics (arms.yaml measured, not the 800-guess)
**RETIRED (measured).** 4-scenario × a0/a4 real smoke (32 calls, pinned
model temp 0): deliberation per-episode **p95 7,518 · mean 3,821**
(n=8; usage 5,541 prompt + 25,029 completion tokens; model spend
**$0.029**). arms.yaml a0/a4 `expected_tokens_per_episode` re-locked to
**26409** = 3× headroom over the LARGER of the two measured p95s
(#2292 probe 8,803 and this exposure 7,518 → 7,518×3 = 22,554 ≤ 26,409),
via the SAME token_table_hash machinery. a1/a2/a2b/a3 keep
`TBD(EXPOSURE)` — those arms were NOT real-run in exposure (vendor
integration is Task-9/10 executor territory); never a stale guess
presented as measured.

## Envelope field-fill (part-1 slice)
6/6 episodes emit an envelope (position_clear 6/6); position revision
3/6 on the family mix — the real deliberation scaffold produces the
fields R1/R3 envelope semantics read. Full envelope schema + trace
emission is Task-9 (executor v1) territory.

## Budget
Model spend $0.029 (probe sub-cap $3.00) + judge leg ~$0.50 (reserve
$1.50). Mid-run dollar-cap stop verified hermetic (a single over-budget
episode is never silently completed — battery/exposure/smoke.py, locked).

## Gate
All 4 risks retired → Task 9 (executor v1 — TVDE probe tier R1–R5, a0
first) may start per plan Task 8 Step 6.
