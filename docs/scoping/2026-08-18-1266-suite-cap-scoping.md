---
title: "Scoping: fast test suite exceeds watchdog cap — rebalance (a)/(b) halves + wire halves integrity"
type: decisions
domain: ci
doc_status: live
created: 2026-08-18
ownedBy: platform
---
# Scoping: #1266 — fast test suite exceeds watchdog cap (test (a)/(b))

> **Issue:** #1266 · **Status:** scoped 2026-08-18 (direction locked below) · **Related:** #798 (watchdog family), #880 (halves split + slow bar), #1021/#1008 (tiered selection), #1262 (manifest-integrity gate), #1371 (slow_files single source + kill-aware orphan assert), #1436 (falkordb provisioning + skip guard)

## O/I/T

- **Objective:** the `test` (a)/(b) matrix legs (required pre-merge gate) must fit the in-step watchdog with margin, so the #1266 failure mode (SIGKILL at the cap, exit 137, orphan assert fires, false red) can never re-appear even on loaded/degraded runners. The halves must also stop drifting from `config/ci-surfaces.yml` (the #1260/#1270 drift class, which #1262 only closed for the manifest, not for the halves).
- **Indicators:**
  1. Both fast legs complete with `passed` counts on a clean runner in **≤ ~27 min** (well under the 45m watchdog → margin for runner variance).
  2. `python3 tools/ci_selection.py --integrity` fails if: a `slow_files` entry leaks into a half, a half entry is unclassified in the manifest, a half entry is a dead file, or the halves tilt beyond ±3 files.
  3. New slow files land in `config/ci-surfaces.yml slow_files:` and flow to `test-slow` without touching the halves (already wired via the selector's `slow_files` output).
- **Targets:** fast legs ≤ ~27 min each on clean runner (measured), halves count-balanced, integrity gate extended + unit-tested, CI run green (modulo pre-existing #647 failures).

## Context

**Measured breakdown (run 32172189636, main 695afbc5, 2026-08-18):**

| leg | files | pytest time | result |
|---|---|---|---|
| test (a) | 96 | **1999s (33:19)** | 14 failed (all pre-existing #647), 3 errors |
| test (b) | 85 | 1273s (21:12) | passed (skip-guard flagged a skip) |
| test-slow | 21 | 2276s (37:55) | 1 pre-existing failure |

- Half (a) is **12 minutes longer** than half (b) and **3:19 over the original 30m cap**; the 45m watchdog (#1251) currently masks it but the documented runner variance (32/36/40/46m boundaries, #880 header) leaves no margin.
- The over-budget cause is exactly what the issue names: the **merged ingest/index epic suites landed in half (a)** — `test_ingest_mode` (single test 53s), `test_index_cli` (~80s across tests), `test_index_github_cli`, `test_flip_gate` (~40s), `test_joint_index_ingest`, `test_ingest_*`, `test_index_restore/surfacing`, `test_github_indexer`, `test_semantic_extractor`, `test_file_indexer`, `test_crash_recovery*`…
- **Drift:** the halves are a hand-curated second source of truth. 109 manifest fast files (epic903, battery, de2e, wiring_phase01, a9, longmem, falkordb_compat…) are in `ci-surfaces.yml` but in **no** half → they silently never run in the full matrix (push/schedule). #1262's gate only checks manifest coverage, not halves↔manifest consistency.

## Decisions

1. **Direction = issue option (a) rebalance + (c) integrity wiring.** Options (b) raise cap and (d) orphan tolerance are already shipped (#1251 30→45m; #1371 kill-aware assert) — they mask but do not fix.
2. **Trim:** every half file whose measured total ≥ the #880 documented >60s bar moves to `config/ci-surfaces.yml slow_files:` → it runs in `test-slow` (measured 37:55 vs its 75m watchdog — headroom exists). Keeps the documented bar honest instead of an ad-hoc cut.
3. **Rebalance:** remaining half (a) files move to half (b) until the legs are even by measured runtime (target ≤ ~27 min each, count-parity ±3 enforced by the new gate).
4. **Integrity wiring (tested in `tests/test_ci_selection.py`):** extend `--integrity` to parse the workflow's matrix halves and fail-closed on: slow-file leak into a half; half file unclassified in the manifest; dead half entry (missing file); leg imbalance > ±3 files. Manifest-fast-file-absent-from-halves stays a **warning** (closing it would push 109 more files into the fast gate and blow the cap — the coverage hole is tracked separately; tier-2 PR selection still covers those files).
5. **Out of scope:** the 109-file coverage hole (separate issue), raising the cap further, runner queue waits.

## Approach (slices)

- **S1 — Measure:** local timing pass on the fast halves (per-file, `--durations=0` + aggregation) → the authoritative per-file table for trim/rebalance. CI `--durations` tails as cross-check.
- **S2 — Trim + rebalance:** edit the two `files:` blocks in `.github/workflows/python-ci.yml` and the `slow_files:` list in `config/ci-surfaces.yml` from the S1 table.
- **S3 — Integrity:** add `workflow_halves` parsing + checks to `tools/ci_selection.py`, unit tests (leak / unclassified / dead / imbalance / warning), wire nothing new in CI (the `--integrity` step already runs unconditionally).
- **S4 — Verify:** `uv run pytest tests/test_ci_selection.py tests/bench/test_roundrobin.py -q` + `python3 tools/ci_selection.py --integrity`; push; PR against main (not draft).

## Complexity

- Engineering: **low** — config + one tool + tests; no runtime code.
- Ontology: none.

## Risks / mitigations

- **Machine variance:** rebalance target leaves ≥ 15 min of watchdog margin on clean runs; the 45m cap + #880 variance history bounds worst case.
- **Test-slow growth:** trimming adds ≤ 10-15 min to test-slow (37:55 → ~50m) — still far under its 75m watchdog; the selector already emits `slow_files` to the test-slow job, no wiring change needed.
- **Gate false-positives:** imbalance tolerance ±3 files so normal maintenance doesn't trip it; dead-file check only applies to `tests/` + `bench/` paths.
