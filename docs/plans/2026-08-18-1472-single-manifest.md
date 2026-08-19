---
title: "Single Source of Truth for Fast-Gate File Lists — Kill Dual-Manifest Drift + Auto-Register"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-18
subjects.team: epistemic-team
aboutSubjects: tortoise-ci, tiered-test-selection
aboutObjects: ci-workflow, ci-surfaces, manifest-integrity, push-matrix, auto-register
---

<!-- research-path: issue-scoping comment on #1472 (bounded double-diamond, full-diamond-verify 1 cycle) -->

# Single Source of Truth for Fast-Gate File Lists — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make `config/ci-surfaces.yml` the SINGLE source of truth for the fast-gate file lists — eliminate the dual-manifest drift class where a test file registered in the manifest but missing from the workflow's hardcoded `matrix.files` SILENTLY never runs on push (issue #1472 indicators a/b/c).

**Team:** epistemic-team
**Role:** (none set)

**Key empirical facts (verified 2026-08-18 against origin/main):** 100 of 299 manifest-classified top-level test files are in NO push leg — not in `matrix.files` half a (96), half b (85), `slow_files` (21, a valid push leg via `test-slow`), or env-broken (`tests/test_agent_signup.py`). The drift includes `test_ci_selection.py` itself, `test_skip_guard.py`, `test_abuse.py`, `test_canonical.py`, and the 10-file `test_epic903_*` family. The #1262 integrity gate stays green because those files ARE classified — the gate is one-directional. The halves are hand-maintained and only the manifest is gate-checked; the `ENV_BROKEN_FILES` env var in the workflow is referenced nowhere (purely documentary). The 4 bench files (`tests/bench/test_*`) run in half b but are not manifest-classified (they are `tests/bench/*`, not top-level `test_*.py`).

**Architecture:** The push matrix is DERIVED from the manifest at workflow-build time. `tools/ci_selection.py` gains a `push_legs()` partition (fast = classified − slow − env-broken, parity-split into halves a/b, + manifest `push_extra` for the bench files) and a `--emit-push-matrix` CLI mode; the workflow's `changes` job emits `matrix_a`/`matrix_b` outputs consumed by the test job's `matrix.include` via `fromJSON`. `--integrity` gains two new fail-closed checks: (1) every classified file in EXACTLY one push leg (`leg_coverage_issues`) and (2) the workflow's matrix rows still come from the derivation boundary, never re-hardcoded lists (`workflow_matrix_issues`). Auto-registration (#1429) becomes fully automatic: a file registered in the manifest is by construction in the derived matrix — no manual matrix edit, and `--register` reports the leg each file lands in. SELECTION_FN_VERSION → 1.2.0. No new deps (PyYAML proven on the runner).

### Pattern Research

> **Findings date:** 2026-08-18
> **Gate skipped** — zero third-party deps (PyYAML is already loaded by `tools/ci_selection.py` in the changes job on every event). In-repo precedent: the #1371 slow-files flow already proves "workflow consumes the selector's manifest-derived output" (test-slow builds its list from `changes.outputs.slow_files`); the `changes` job already emits JSON outputs consumed downstream (`test_files`, `surfaces`); `fromJSON` in matrix include is the standard Actions pattern for dynamic matrices.

### Integration Surface Map

The derivation boundary is the one cross-file contract: `--emit-push-matrix` output shape (`{"half_a": [names…], "half_b": [names…]}` with `.py`-less names) ↔ `changes.outputs.matrix_a/matrix_b` ↔ `matrix.include.files`. The run step's `tests/$f.py` construction, `changes.outputs.test_files/full/slow_files`, the `python-ci-gate` job id, and branch protection are untouched. `select()`'s output contract is unchanged. The audit artifact (`selection.json`) must not be clobbered by the new CLI mode.

### Journey Test Map

n/a — no user-facing journeys. Verification = `--integrity` clean, `--emit-push-matrix` deterministic and matching the manifest partition, `pytest tests/test_ci_selection.py` green, `bash -n`/YAML parse of the workflow, and the push matrix (asserted equal to the derived halves in CI by `workflow_matrix_issues`).

**Tech Stack:** Python 3.12 (PyYAML), GitHub Actions (YAML + `fromJSON` matrix include).

---

## Task 1: Add the reverse-drift check + derivation to `tools/ci_selection.py`

**Intent:** the manifest becomes the single source for the push matrix, and `--integrity` fails closed on BOTH drift surfaces (unlisted files AND files absent from / duplicated across the executed legs AND workflow re-hardcoding).

**Acceptance:**
- New constant `ENV_BROKEN_FILES = {"test_agent_signup.py"}` (top-level names; the `tests/e2e` directory is excluded by construction — `unlisted_tests` only globs top-level).
- `push_legs(manifest)` returns `{"half_a": [...], "half_b": [...], "slow": [...], "env_broken": [...]}`: fast = sorted distinct classified files − slow − env-broken; `half_a = fast[0::2]`, `half_b = fast[1::2]`; `push_extra` (manifest key, `.py` names) appended to half b. Half names are `.py`-less (workflow format).
- `leg_coverage_issues(manifest)` returns a list: every classified file in exactly one of {half_a ∪ half_b ∪ slow ∪ env_broken}; leg overlaps flagged (fast∩slow, fast∩env-broken, slow∩env-broken); env-broken names must be classified; push_extra entries must not be classified top-level files.
- `workflow_matrix_issues(workflow_path, manifest)` returns a list: parses `python-ci.yml`; every `jobs.test.strategy.matrix.include[].files` must start with `${{ fromJSON(needs.changes.outputs.matrix_` (a/b); `ENV_BROKEN_FILES` must not be defined in `env`.
- `--integrity` = `unlisted_tests` + `slow_file_issues` + `leg_coverage_issues` + `workflow_matrix_issues`.
- `--emit-push-matrix` CLI mode prints `{"half_a": [...], "half_b": [...]}` (`.py`-less, sorted) and early-returns before the audit-artifact write.
- `--register` (and `--dry-run`) report each added file's push leg (half a/b by parity position).
- `SELECTION_FN_VERSION` → `1.2.0`.

**Files:** `tools/ci_selection.py`

## Task 2: Derive `matrix.files` in `.github/workflows/python-ci.yml`

**Intent:** the hardcoded 181-name halves disappear; the push matrix is emitted by the selector so registration in the manifest is sufficient (indicator c).

**Acceptance:**
- `changes` job gains a step `Emit push matrix` (always runs, both events) writing `matrix_a`/`matrix_b` outputs from `--emit-push-matrix`.
- `test` job `strategy.matrix.include` becomes two rows: `{half: a, files: ${{ fromJSON(needs.changes.outputs.matrix_a) }} }` and `{half: b, files: ...matrix_b}`; the hardcoded `files: >-` lists are deleted.
- `ENV_BROKEN_FILES` env var removed (replaced by a comment pointing at `tools/ci_selection.py`).
- Run-step file construction (`tests/$f.py`) unchanged; header comments updated to describe the derivation.

**Files:** `.github/workflows/python-ci.yml`

## Task 3: Add `push_extra` to `config/ci-surfaces.yml`

**Intent:** the 4 bench files (unclassifiable as top-level surfaces) stay in the manifest — the single file list.

**Acceptance:** top-level key `push_extra:` lists `bench/test_bench_core.py`, `bench/test_degradation_chain.py`, `bench/test_roundrobin.py`, `bench/test_smoke_embedded.py` (`.py` names, matching manifest convention) with a comment; no other manifest change.

**Files:** `config/ci-surfaces.yml`

## Task 4: New unit tests in `tests/test_ci_selection.py`

**Intent:** the reverse-drift gate is proven both ways (clean manifest passes; each drift class fires) and the derivation is deterministic and workflow-format-compatible.

**Acceptance:** new cases: clean real manifest → `leg_coverage_issues` empty and every classified file covered; file in no leg → fires; file in slow AND fast → fires; env-broken name unclassified → fires; workflow row with a hardcoded list → `workflow_matrix_issues` fires; derived workflow rows → clean; `--emit-push-matrix` output deterministic, sorted, `.py`-less, and equal to the `push_legs` partition; register reports the file's leg. All existing 17 tests stay green.

**Files:** `tests/test_ci_selection.py`

---

## Rejected Alternatives (documented in the scoping comment)

1. **Check-only + auto-add to halves (no derivation):** keeps two sources of truth; the 100-file gap must be hand-inserted into the halves (same runtime outcome, more churn); the manual-registration treadmill persists for new files.
2. **Check parses the halves back out of the workflow YAML (no derivation):** fixes the gate but leaves the dual-manifest; new files still need a matrix edit; indicator (c) unmet.
3. **Keep bench files hardcoded in the workflow:** leaves a third list; `push_extra` in the manifest is strictly cleaner.

**Runtime-risk note (monitor):** the 100 drift files were part of the pre-#880 full-fast suite (~277 files ≈ 28m pytest session, single job); parity halves (~139/138) extrapolate to ~14-16m/half, comfortably under the 45m in-step watchdog with `TORTOISE_FAST_ATEXIT=1` bounding the teardown tail. If a half approaches the cap post-merge, the `slow_files` rebalance knob is now gate-protected (the #880 mechanism). Watch the first 3 pushes post-merge.

**Follow-ups (not absorbed):** per-file runtime measurement of the re-included 100 files (if the watchdog fires post-merge, rebalance via `slow_files`); `tests/e2e` remains outside this gate by design (covered by welcome-e2e-monitor + ci.yml's legal-e2e job).
