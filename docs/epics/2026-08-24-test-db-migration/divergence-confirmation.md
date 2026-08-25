---
title: "Divergence confirmation log — epic #1647 test-DB migration (D1–D16)"
type: data
domain: data
status: live
created: 2026-08-25
ownedBy: epistemic-team
subjects:
  team: epistemic-team
doc_status: live
aboutSubjects: epistemic-team
aboutObjects: FalkorProjection, TortoiseSDK
---

# Divergence confirmation log — epic #1647 (D1–D16)

> Canonical record of observed-vs-predicted divergence between the embedded
> (FalkorDBLite) and docker (FalkorDB) lanes, and the registry consumed by
> the P3 canary-streak classifier (`tools/testdb_canary_classify.py`).
> Task 8 Step 4 (P2) records the half-b confirmation; Task 9 Step 6 (P3)
> keeps it current. The plan's authoritative D1–D16 table is executable in
> `tests/test_divergence_conformance.py` (E2E-8); this file is the CI-facing
> log + classification registry.

## Status (P3, Task 9)

- P2 half-b confirmation: half-b DB-agnostic tests green on docker; the 3
  busy-error tests skip visibly (embedded_only marker, D-2=A); the 7 P2
  carve-out files ran embedded (exemption); wipe-heavy + live-writer
  surfaces confirmed docker-green via the Task 6 Step 3 pre-flight; the
  in-process prod-command call sites (test_domain_validators
  `_cmd_validate`, test_session_index_health `doctor`, test_cli_context
  `context`) confirmed docker-green (the redirect makes the CLI test the
  server lane with derived names — the desired outcome).
- P3 (this task): the full fast matrix + test-slow + track-b flip to docker;
  the 17-file carve-out runs embedded in the dedicated URI-unset job
  (E2E-4). E2E-8 conformance passes in BOTH modes (each D-branch asserts its
  documented lane). Zero unexpected divergences observed; zero = the P2 gate
  condition, re-verified at P3.
- The registry below is the classifier's expected-divergence set: a failing
  nodeid matching one of the prefixes is a DOCUMENTED D1–D16 table entry
  (bucket "divergence" — logged, streak preserved); any other failure breaks
  the canary streak (bucket "unexpected-divergence", reset to 0).

## Expected-divergence nodeids

# Format: `D#: <nodeid-prefix>` — one entry per line, parsed by the canary
# classifier. Prefixes are the E2E-8 conformance tests (parametrized over the
# `leg` fixture — both `[embedded]` and `[server]` legs of a D-branch match
# the prefix).
D1: tests/test_divergence_conformance.py::test_d1_is_embedded_seam_flag
D2: tests/test_divergence_conformance.py::test_d2_probe_failure_recovery
D3: tests/test_divergence_conformance.py::test_d3_lost_graph_recovery
D4: tests/test_divergence_conformance.py::test_d4_bulk_wipe_graph_guard
D5: tests/test_divergence_conformance.py::test_d5_range_index_identical
D6: tests/test_divergence_conformance.py::test_d6_freshness_composite_mode_split
D7: tests/test_divergence_conformance.py::test_d7_embedded_repair_sweep
D8: tests/test_divergence_conformance.py::test_d8_hnsw_vector_index
D9: tests/test_divergence_conformance.py::test_d9_cross_lens_calibration
D10: tests/test_divergence_conformance.py::test_d10_retrieval_pool_floor_flag
D11: tests/test_divergence_conformance.py::test_d11_busy_error_embedded_only
D12: tests/test_divergence_conformance.py::test_d12_concurrency_semantics
D13: tests/test_divergence_conformance.py::test_d13_wipe_server_refusal
D14: tests/test_divergence_conformance.py::test_d14_hosted_make_sdk_fallback
D15: tests/test_divergence_conformance.py::test_d15_atexit_fast_close
D16: tests/test_divergence_conformance.py::test_d16_version_probe

## Observed-vs-predicted record

| D# | Predicted (plan) | Observed (P2 half-b + P3) | Status |
|----|------------------|---------------------------|--------|
| D1 | `_is_embedded` seam discriminates path= vs URI | Confirmed both lanes | ✓ |
| D2 | probe-fail recovery embedded-only (auto-rebuild) | Confirmed (carve-out lane) | ✓ |
| D3 | lost-graph recovery embedded-only | Confirmed (carve-out lane) | ✓ |
| D4 | bulk-wipe guard embedded-disabled / server-refusing | Confirmed (D-4, `wipe_server`) | ✓ |
| D5 | range index identical both modes | Confirmed | ✓ |
| D6 | freshness composite index server-only | Confirmed (docker: composite; embedded: absent) | ✓ |
| D7 | repair sweep embedded-only code path | Confirmed (carve-out lane) | ✓ |
| D8 | HNSW vector index server-only | Confirmed (docker: HNSW; embedded: brute-force) | ✓ |
| D9 | cross-lens calibration mode-split | Confirmed via divergence pass (docker-calibrated expectations) | ✓ |
| D10 | pool-floor flag parity | Confirmed | ✓ |
| D11 | EmbeddedStoreBusyError embedded-only | Confirmed (3 busy-error tests skip visibly on docker, D-2) | ✓ |
| D12 | multi-tenant concurrency server-only | Confirmed (0 busy errors, live-writer tests) | ✓ |
| D13 | wipe() embedded-only refusal | Confirmed | ✓ |
| D14 | hosted_api embedded fallback | Confirmed | ✓ |
| D15 | TORTOISE_FAST_ATEXIT fast-close | Confirmed (no-op on docker — no embedded clients) | ✓ |
| D16 | version probe ≥ 4.x both engines | Confirmed | ✓ |

Zero unexpected divergences as of P3 (a violation of this table = P2/P3
blocker; the classifier enforces it on the docker lane).
