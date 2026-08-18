---
title: "Scoping — Issue #1436: live-FalkorDB-required tests silently SKIPPED in fast-suite"
type: engineering
domain: capability
doc_status: live
ownedBy: epistemic-team
aboutSubjects: tortoise
created: 2026-08-18
---

# Scoping — #1436 chore(ci): live-FalkorDB-required tests silently SKIPPED in fast-suite

**Date:** 2026-08-18 · **Branch:** `chore/1436-loud-skips` · **Issue:** #1436 · **Complexity:** standard

## Problem (verified)

16 test files probe for a live FalkorDB at module load and silently `skip` when it's
unreachable (`FALKORDB_AVAILABLE` pattern, e.g. `tests/test_search_engine.py:14-28`):

- **7 module-level** `pytestmark = pytest.mark.skipif(not FALKORDB_AVAILABLE, ...)`:
  test_decide, test_directional_impl_fix, test_directional_impl, test_ep_directional,
  test_issue94_annotate_ep_batch, test_namespace_uri_mode, test_topic_summarization
- **9 per-test/mixed**: test_epic903_freshness, test_hnsw_vector_index, test_indexes,
  test_ingest, test_integration_search, test_mcp_server, test_search_engine_gaps,
  test_search_engine, test_session_capture_e2e

The fast-suite `test` matrix job (`.github/workflows/python-ci.yml`) runs 13 of these
files (3 — test_indexes/test_ingest/test_search_engine_gaps — are already in `slow_files`)
with **no falkordb service container** → probes fail → whole skip. The 2026-08-18 05:56
main run: **1453 passed, 72 skipped, 0 failed** — the skip set includes test_ep_directional,
so the #943 draft-default EP regression class (#1382) went undetected for days: CI was
"green" while EP tests never ran.

**Verified locally** (this machine has live FalkorDB on :6379): running the 16 files
against live FalkorDB on `main` produces **real failures** — test_ep_directional 7 failed
(EP drops 0.0000, the #1382 draft-filter regression; the fix branch `fix/1382-ep-local-env`
passes 14/14 on the same server), plus live-only drift in test_directional_impl_fix (4),
test_directional_impl (3), test_integration_search (`tortoise_search()` no longer accepts
`traversal_path` — 4), test_search_engine (5), test_mcp_server (1), test_session_capture_e2e (1).
Local EmbeddedStoreBusyError noise (shared ~/.tortoise/tortoise.db held by other sessions)
is a local-only artifact, not CI-relevant (fresh runners).

## Decision

Fix per issue's "Option (a) + (b)": **provision the falkordb service in the fast-suite
`test` matrix job so the live tests actually RUN, and add a fail-closed skip guard**
(modeled on `test-concurrency-falkor`'s, but extracted to a tested tool) so any future
live-skip regression flips the job RED instead of green.

- **Service contract** (covers BOTH probe URI families):
  - `falkordb` on **6379** with `--requirepass falkordb`
    (`docker://:falkordb@localhost:6379/...` family: test_ep_directional,
    test_directional_impl{,_fix}, test_mcp_server, test_namespace_uri_mode, test_indexes,
    test_search_engine_gaps)
  - `falkordb-legacy` on **16379** passwordless
    (`docker://:@localhost:16379/...` family: test_search_engine, test_ingest,
    test_session_capture_e2e, and the 16379 candidates in test_hnsw_vector_index /
    test_integration_search / test_indexes / test_search_engine_gaps)
  - Note: `test-concurrency-falkor`'s claim that test-slow "provisions falkordb" is
    **incorrect** — the only `services:` block in python-ci.yml is in
    test-concurrency-falkor. Option (c) as literally proposed would still skip in test-slow.
- **Skip guard**: new `tools/skip-guard.py` parses the pytest log; any `SKIPPED` line whose
  reason mentions "FalkorDB" (both pytest formats) → exit 1 with the skipped set listed.
  Runs as a separate `if: always()` step gated on pytest exit code 0 (the silent-green
  failure mode is exactly "pytest green + live tests skipped"). Unit-tested in
  `tests/test_skip_guard.py` (registered in ci-surfaces.yml → --integrity gate).
- No `TORTOISE_DB_URI` at job level (would break the ~600 embedded tests — probes manage
  their own env); no probe changes needed — the two-port service contract matches every
  probe family.

## Expected consequences (intended, per issue O/I/T)

- Next main push/schedule run: fast-suite live tests RUN (0 skipped) → the #1382 EP
  regression class + the live-only drift above surface as REAL failures → CI goes red
  until follow-up fixes (fix/1382-ep-local-env is branch-ready) land. This is the point
  of the chore: fail-closed, not silent-green.
- PR (tier-2) runs: the guard only asserts on files actually selected; unchanged behavior
  for embedded-only surfaces.

## Out of scope

- Fixing the live-test regressions themselves (test_ep_directional #1382 class,
  traversal_path drift, etc.) — they belong to their own issues; CI catching them is the
  deliverable here.
- test-concurrency-falkor's inline shell guard (left as-is; same semantics).
