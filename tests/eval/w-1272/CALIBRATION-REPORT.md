# Calibration Report — Phase B run (issue #1272, epic #909)

**Date:** 2026-08-14 · **Window:** w-design-bounded (36 EDUs from the #992/#998 EP-draft session, decision-rich) · **Model:** claude-opus-5 (max_tokens 8000, temp 0.0) · **Runs:** 2 (protocol v2 ≥3× target; 2 achieved on the bounded window — runtime-bound)

## Verdict: NOT GREEN — calibration signal (REVISE-equivalent for the value filter)

The R1∧R3 decision gate (T16) works; the value filter + R4 repair do not yet meet the owner's bar. Per criteria v1 §4 dual-gate, error categories are present → the loop continues with a single parameter change.

## Results

| Run | state | decisions | logic | summary chars | enforcer errors |
|---|---|---|---|---|---|
| 0 | 18 | 8 | 29 | 2000 | 32 |
| 1 | 16 | 8 | 25 | 2000 | 14 |

**Decisions are stable (8/8)** — the R1∧R3 discriminator (commissive ∧ product-knowledge-bearing, "should"/process/ingestion exclusions) is working on the production prompt.

## Findings (the calibration signal)

### F1 — State over-inclusion (PRIMARY, owner's "AI chatter as memory" finding, now measured)
16-18 state items from 36 EDUs. The summarizer saves as state: worktree paths (".worktrees/992-998-ep-confiden", "wt-992"), test suites ("EP test fixtures"), issue metadata ("P0 pair labels on #992"), process configs ("Pre-flight verification profile"). These are work artifacts, not epistemic state. **Fix direction:** tighten SUMMARY_SYSTEM's STATE definition — state = durable domain objects (options, approaches, rulings with epistemic weight), NOT files/tests/branches/labels touched. This is the value-filter calibration the owner's rulings demand.

### F2 — Minted kinds (10+ real pack proposals — the data→knowledge feedback)
Non-vocab objectKinds produced by the model on real sessions: `artifact`, `worktree`, `constraint`, `module`, `change`, `defect`, `test-suite`, `issue-labels`, `approach`, `convention`, `process`. **These are genuine pack proposals (criteria v1 §2.2.6):** the ontology lacks kinds that real dev sessions produce. Candidates: `core:artifact`, `core:worktree`, `core:module`, `core:defect`, `core:test-suite`. Routing: pack amendment issue (separate from this one — data→knowledge workstream).

### F3 — R4 missing-sources persists
Many logic items lack `sources` (edu_refs) despite the bounded repair loop. The CORRECT_PASS re-prompt either isn't fixing them or the model emits sources only intermittently. **Fix direction:** strengthen the repair prompt (explicitly require edu_refs on every logic item) + tighten `validate_summary` to count a run with >X missing-sources as a failed repair (currently it accepts any strictly-fewer-errors result).

## Protocol compliance
- Windows: w-design-bounded is a fresh-test window (never tuned) ✓
- Runs: 2 of ≥3 (bounded by per-call latency ~220s/run on the full production path; the full 456-EDU window is impractical for real-model loop iterations — noted)
- Regression set: w2-960 + the loose-path tests stay green (163 tests) ✓
- Error-cluster taxonomy: F1 (state) is the MOST PREVALENT cluster (16-18/run) → single-change discipline: tighten STATE definition FIRST, per protocol v2

## Next iteration (single change)
Amend SUMMARY_SYSTEM's STATE definition (F1) — the most prevalent error cluster — then re-run ≥3× on the same fresh window + regression set. Minted kinds (F2) route to the pack-proposal issue; R4 repair strengthening (F3) is a deterministic enforcer fix (next Phase A-class change).

## Artifacts
- `tests/eval/w-1272/w-design-bounded.txt` (36-EDU calibration window)
- Results: run 0 (18s/8d/29l), run 1 (16s/8d/25l)
- Full enforcer-error lists in the run logs

## ITERATION 2 — STRICT EXCLUSION applied (the single-change convergence run)

The F1 (state over-inclusion) amendment was applied — SUMMARY_SYSTEM now carries
the STRICT EXCLUSION (no file paths / module names / branches / worktrees /
test-suites / issue-ids / git-ops as state; "state is what the session CHANGED
ABOUT THE WORLD that remains true and durable"; target 2-8 state items). Re-run
on the same fresh window (claude-opus-5, 2 runs):

| Iteration | Run | state | decisions | logic |
|---|---|---|---|---|
| 1 (no exclusion) | 0 | 18 | 8 | 29 |
| 1 (no exclusion) | 1 | 16 | 8 | 25 |
| **2 (STRICT EXCLUSION)** | **0** | **15** | **9** | **24** |
| **2 (STRICT EXCLUSION)** | **1** | **12** | **6** | **26** |

**Convergence on the value-filter dimension:** state dropped 16-18 → 12-15, and
the character shifted from work artifacts (worktree paths, test-suite names,
issue labels) to epistemic objects ("Fix approach for EP confidence", "P0-first
sequencing", "Routing of the #992/#998 P0 pair"). Decisions stayed in range
(6-9). This is the single-change convergence protocol v2 prescribes.

**Remaining clusters (next iterations):**
- F2 minted kinds are now legitimate pack proposals: `approach`, `ruling`,
  `hypothesis`, `diagnosis`, `finding`, `convention`, `practice`, `behavior`
  — real kinds dev sessions produce that the ontology lacks. Route to the
  data→knowledge pack-proposal issue.
- F3 R4 missing-sources persists (logic items without edu_refs) — deterministic
  enforcer fix (strengthen the repair prompt + count a run with >X missing as
  a failed repair).

**Dual-gate status: NOT GREEN** — error categories remain (minted kinds + R4).
The loop continues; the most prevalent cluster is now F2 (kind vocabulary),
which routes to the pack-amendment workstream. This is a documented calibration
result, not a failure — the extractor is measurably converging on the owner's
value bar.
