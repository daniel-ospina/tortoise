---
title: "Split test-slow into Parallel Duration-Balanced Legs (#1471)"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-18
subjects.team: epistemic-team
aboutSubjects: tortoise-ci
aboutObjects: python-ci, test-slow, slow-files, ci-matrix, critical-path
---

<!-- research-path: issue-scoping comment on #1471 (bounded double-diamond, 1 verifier per gate) -->

# Split test-slow into Parallel Duration-Balanced Legs — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Cut the main-push critical path — the serial 36-38m `test-slow` job (75m watchdog, 90m cap) — by splitting its 21 files (config/ci-surfaces.yml `slow_files:`) into two parallel duration-balanced legs. Indicators (from #1471): (a) main-push wall-clock for test-slow drops ~40-50%; (b) both legs stay under the 75m watchdog on loaded runners; (c) all 21 slow files still run, just in parallel.

**Key empirical facts (verified 2026-08-19):** CI run 32241846147 (main): test-slow = 2264.48s (37:44) of pytest time, the last python-ci job to finish (test (a) pytest ended 10:21, test (b) 10:50, test-slow 10:57:59). Per-file durations measured locally 2026-08-19 with `--durations=0` (see split table). The `test` job already uses the exact matrix pattern this plan mirrors (#880): two parallel halves, fail-fast: false, committed per-half file lists, per-job watchdog + heartbeat + guaranteed tail.

**Architecture:** Single workflow file changed (`.github/workflows/python-ci.yml`). `test-slow` becomes a 2-leg matrix (job id kept: `test-slow` — branch protection's required check is `python-ci-gate`, which aggregates via `needs.*.result`; matrix jobs aggregate automatically, so the gate needs zero changes). No new deps. Every kept block is verbatim in-repo precedent from the same file's `test` job (survived #798/#880/#964/#1371).

### Pattern Research

> **Findings date:** 2026-08-19
> **Gate skipped** — zero third-party deps (PyYAML + coreutils timeout/stdbuf are ubuntu-latest-provisioned and already proven in python-ci.yml's `changes` and `test` jobs). In-repo precedent is complete: the `test` job's matrix + watchdog/heartbeat/tail blocks (#798/#880/#964/#1371) are battle-tested with documented failure modes in the file header. Justified skip per writing-plans skip rules.

### Integration Surface Map

| Surface | Type | Test/Verify Layer |
|---------|------|-------------------|
| GitHub Actions YAML validity | config | yaml.safe_load parse + `bash -n` on run blocks (local) |
| python-ci-gate aggregation | CI | needs.test-slow.result aggregates matrix legs — join check unchanged |
| Drift guard (union legs == config slow_files) | CI | step fails closed; validated locally before push |
| tests/test_skip_guard.py::test_workflow_keeps_rs | CI | fast job's "45m" line unchanged — legs use 75m (no match) |
| tests/test_ci_selection.py | CI | untouched (selector/config unchanged) |

## Tasks

### T1 — Convert test-slow to a 2-leg matrix

- Add `strategy: {fail-fast: false, matrix: {half: [a, b], include: [...]}}` to `test-slow`, with per-leg committed file lists (duration-balanced, order preserved from config).
- Add `name: test-slow (${{ matrix.half }})`.
- Update the job's header comment (serial → parallel legs, measured split).
- Keep all setup steps verbatim (checkout, setup-python, install, orphan-reap, HF cache, HF pre-cache).

### T2 — Per-leg run step + drift guard

- Run step: build `FILES` from `${{ matrix.files }}` (`tests/$f.py`), keep the #1371 empty-guard, and the #964 heartbeat + 75m watchdog + guaranteed tail + WATCHDOG banner verbatim.
- Add a drift-guard step before the run step: parse `python-ci.yml` matrix include + `config/ci-surfaces.yml slow_files`, assert union(legs) == slow_files, fail closed otherwise (a new slow file can never silently drop out of CI).

### T3 — Validation

- `yaml.safe_load` the workflow (parse check) + extract run blocks and `bash -n` them (run-step syntax check).
- Confirm the union of both leg lists == all 21 config slow_files (nothing dropped).
- Confirm `test_workflow_keeps_rs` still matches the fast job's 45m line (legs use 75m).
- Confirm `python-ci-gate` needs list unchanged (matrix aggregation).

### T4 — Commit-workflow

- Branch `fix/1471-split-test-slow` (already created in worktree), commit via `git commit -F`, DRAFT PR, code-review gate (config focus), VGATE, mark ready. Do NOT merge (per directive).

## Verification Plan

1. Local: parse workflow YAML; `bash -n` each leg's run block; drift-guard script run locally against the worktree (union check) — must print "legs cover exactly 21 slow files".
2. CI: PR runs python-ci on tier-1 (workflow change touches `.github/workflows/` — verify tier selection picks it up; if the PR's changed paths don't trigger the python jobs, rely on the push-to-branch runs and gate aggregation review).
3. Evidence in PR: measured per-file durations table + estimated leg times (each ≈ 18.5m / 18.3m pytest — the <20m target is pytest time; with ~5m setup the job wall is ≈ 23-24m, a ~36-45% critical-path cut vs 37:44 serial).

## Acceptance Criteria

- [ ] `test-slow` runs as two parallel matrix legs, each with watchdog (75m) + heartbeat + guaranteed-summary tail.
- [ ] All 21 config slow_files run exactly once across the two legs (drift guard enforces union == config, fail-closed).
- [ ] `python-ci-gate` aggregates both legs without changes (needs.test-slow.result handles matrix).
- [ ] Leg pytest times each < ~20m on a normal runner (est. from measurements), vs 37:44 serial.
- [ ] No slow-file coverage lost (zero dropped files).

## Runtime Prerequisites

- GitHub Actions ubuntu-latest runners (same as current job) — no new services.
- HF embedding-model cache + pre-cache steps unchanged (best-effort download, TF-IDF fallback).
