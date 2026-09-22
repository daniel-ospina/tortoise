# Public evals posture — how this battery is presented and defended

> Status: epic #1402 in progress (#2284 Phase-1 + #2291 shipped; #2292 rubric
> validation VALIDATED 2026-09-08; real-run numbers land with #1416).
> Pattern reference: **gbrain-evals** (`garrytan/gbrain-evals` — public,
> reproducible, commit-pinned benchmark repo for a personal-memory agent
> stack) plus the open eval-harness canon (lm-evaluation-harness,
> based-evaluation-harness). This doc maps what gbrain does publicly to
> what this repo already ships and what #1416 adds, so the battery can be
> presented publicly and defended when the real-run numbers land.

## The gbrain pattern (what makes its evals defensible in public)

1. **Public harness + committed receipts.** The suite runs on your own
   machine from a commit hash; every scored row carries a dated receipt
   under `docs/benchmarks/<date>-<name>/`.
2. **Corpus + sealed answers.** Gold answers live in a separate store the
   system under test never sees (no peeking, no cheating).
3. **Falsifiable gates + hermetic CI.** Each number has a committed
   pass/fail bar; the hermetic form runs in CI on every PR; "a benchmark
   that can't fail can't force a fix."
4. **Bad numbers published next to good ones.** The honest 0.075 default
   stays in the README beside the tuned 0.582; known weaknesses are
   scoped, never hidden.
5. **Anti-gaming built into the harness.** Sealed keys at the boundary,
   pinned judge versions, seeded randomization, tolerance bands from
   repeated runs, exact-SHA dependency pinning.
6. **Adversarial self-audit published in full.** gbrain audited its own
   suite with 35 agents (239 findings, 236 fixed) and published the whole
   report; claims survive because they are scoped to what the machinery
   can prove.
7. **Plain-English scorecard.** A table mapping each area → what it
   measures → the bar → shipping status, with per-row report links and a
   sources+caveats doc for every competitor comparison.

## What this repo already ships (mapped to the pattern)

| gbrain property | Tortoise battery equivalent | Where |
|---|---|---|
| Public harness + committed receipts | `battery/` + `docs/plans/` per issue; judge records committed | `docs/epics/1402-eval-battery/benchmarks/2026-09-08-r2-pre-exposure/records.json` (+ probe manifest/tokens) |
| Corpus + sealed answers | `config/corpus.yaml`/`corpus.json` committed; gold in a sealed `GoldStore`; reader projection asserted gold-free (`assert_no_gold`) | `battery/config/corpus.py`, `corpus_loader.py` |
| Falsifiable gates | thresholds.yaml `[cal]` rows + `cal_table_hash` + reviewable re-locks; E2E gates (surfaced ≥90% etc.); judge validation battery (retest/gold/stress/IRT) | `battery/config/thresholds.yaml`, `battery/judge/gate.py` |
| Hermetic CI | carve-out embedded lane (`TORTOISE_TEST_CARVE_OUT=1`) + docker lane + ci-surfaces manifest; battery family ~371 tests | `config/ci-surfaces.yml`, AGENTS.md testing |
| Bad numbers published | engine-diagnostic-undec-unreachable note (#2291); κ-paradox documented (owner decision A); PrecisionMembench-style caveats when measured | plan docs, validation records, this file |
| Anti-gaming | protocol hash (seed/model/temp/schema/tool-surface); determinism tolerance rows from measured two-run deltas; judge model+temp pinned; seeded scenario order; `model_pin` pre-flight refusal | `battery/parity/runner.py`, `battery/config/arms.yaml`, `battery/runner/run.py` |
| Adversarial self-audit | parent-enforced code-review ceremony + second-model gate per PR; per-arm source-level lane audit (zero raw Cypher on product path); audit tests per surface | AGENTS.md skills; `tests/test_battery_lane_matrix.py` |
| Plain-English scorecard | docs/agent-reasoning-eval-battery.md spec + fixture matrix + per-epic reports | `docs/`, `docs/epics/1402-eval-battery/` |

## Judge-reliability decision (defensible in public)

The R2 coverage rubric is scored by an LLM judge. Before scoring anything
we validate the judge on REAL deliberation text. 2026-09-08 measured run
(record committed above): judge retest self-consistency **1.00 (8/8)**,
raw two-model agreement **po 0.886**, **Gwet's AC1 0.849** (two frontier
models, temp 0), gold-anchor agreement ≥ 0.8, stress all-green. Cohen's
kappa reads 0.54 — but on a pool that is ~86% "yes" (real agents mostly
deliberate well) kappa is capped by the skewed-marginal paradox
(Feinstein–Cicchetti 1990; Gwet 2008); AC1 is the paradox-resistant
coefficient and is the real-text bar (owner decision A, documented + test
frozen in `tests/test_battery_validation_real.py`). The per-item IRT
"fit" leg is recorded but not gating at probe corpus size (~3 renders per
item is under-powered for Rasch misfit detection); it re-arms at the
#2284 Task-8 exposure pool. This is the "we publish the number we are not
proud of next to the one we are" discipline: the honest kappa and its
explanation sit beside the passing AC1.

## What #1416 (real run + verdict) adds for public presentation

- A committed real-run receipt under `docs/epics/1402-eval-battery/
  benchmarks/<date>-<real-run>/`: run manifest (pinned model/temp/seed,
  schema/event version, tool surface — the parity `protocol_hash` inputs),
  per-arm episode results, thresholds comparisons, verdict + reasoning.
- The plain-English scorecard row for R1–R5 with per-arm bars vs measured.
- Competitor comparisons (if published) cite sources + caveats in the
  research/grounding docs, never bare claims.
- The 35-agent-audit analogue: the adversarial audit of the battery's own
  gates (find-bugs/code-review ceremonies, lane audit) documented
  alongside the shipped numbers.

Every number above is reproducible: same branch/commit + pinned model +
seed + `protocol_hash` produces the same result shape (determinism
tolerances from measured two-run deltas), and the hermetic form of every
gate runs in CI on every PR.
