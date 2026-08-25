# Divergence Change List — Epic #1647 Test-DB Migration (D1–D16)

> **Executable spec:** `tests/test_divergence_conformance.py` (E2E-8) — every
> row below is a runnable test parametrized over BOTH lanes (embedded
> FalkorDBLite / docker FalkorDB) with mode-split assertions.
>
> **P4 filing (epic #1647 Task 10 Step 3):** the "Observed" columns are filled
> from the P2 divergence-confirmation pass (plan Task 8 Step 4 — recorded in
> `docs/epics/2026-08-24-test-db-migration/divergence-confirmation.md`) and
> the P3 CI runs. Zero unexpected divergences observed on either lane — each
> D-branch asserted its documented lane in both modes (E2E-8 passes on
> embedded AND docker). An unexpected divergence (observed ≠ expected on
> either lane) is a P2/P3/P4 blocker and resets the canary streak
> (tools/testdb_canary_classify.py).

| # | Branch (source) | Embedded expectation | Server expectation | Conformance test | Observed embedded | Observed server | Match? |
|---|---|---|---|---|---|---|---|
| D1 | `_is_embedded` seam (projection/__init__.py L612) | `_is_embedded is True` (path=) | `_is_embedded is False` (redirect/URI) | `test_d1_is_embedded_seam_flag` | Confirmed (P2/P3 — both lanes) | Confirmed (P2/P3 — both lanes) | ✓ |
| D2 | `_auto_health_recover` probe-fail (L701) | Auto-rebuild from adjacent JSONL (`recover_from_log`) | Raise RuntimeError ("health check failed on open") — fail loud | `test_d2_probe_failure_recovery` | Confirmed (carve-out lane) | Confirmed | ✓ |
| D3 | Lost-graph check (L519) | 0 nodes + adjacent log → auto-recovery | Skipped — a remote graph is never rebuilt from a local log | `test_d3_lost_graph_recovery` | Confirmed (carve-out lane) | Confirmed | ✓ |
| D4 | `_GuardedGraph` bulk-wipe guard (L70/L1219) | Guard disabled (per-instance temp DB) | Refuses bulk DETACH DELETE on non-test graphs | `test_d4_bulk_wipe_graph_guard` | Confirmed (D-4, `wipe_server` refusal) | Confirmed | ✓ |
| D5 | Range index creation (L1214-1224) | `Point {id, pointKind, content_hash}` — is_operator absent (#522) | IDENTICAL range set — is_operator served by the D6 composite's leftmost prefix, never a D5 single index | `test_d5_range_index_identical` + `test_indexes.py::EXPECTED_RANGE_DOCKER` | Confirmed | Confirmed | ✓ |
| D6 | Composite freshness index (L1244-1257) | Plain `(lastDreamedAt)` only — composite is #522-unsafe | Composite `(is_operator, lastDreamedAt)` | `test_d6_freshness_composite_mode_split` + `test_indexes.py::test_docker_lane_index_shape` | Confirmed (embedded: composite absent) | Confirmed (docker: composite) | ✓ |
| D7 | Embedded repair sweep (L1308) | Drops every Point index containing is_operator on reopen (#522 fix) | Never runs — the composite is correct and survives re-init | `test_d7_embedded_repair_sweep` | Confirmed (carve-out lane) | Confirmed | ✓ |
| D8 | HNSW vector index (L1497) | No vector index; brute-force `vec.euclideanDistance` ordering (EXACT — pinned by bench smoke) | `CREATE VECTOR INDEX … HNSW` (`_vector_index_api` recorded); index-backed queries | `test_d8_hnsw_vector_index` | Confirmed (embedded: brute-force) | Confirmed (docker: HNSW) | ✓ |
| D9 | Cross-lens `is_embedded` (sdk.py L6766/6784 → `run_vector_query`) | Brute-force over ENTIRE Point index — recall EXACT | HNSW-accelerated — recall ordering CAN differ (small-graph seed agrees); calibrated cosine band | `test_d9_cross_lens_calibration` + `test_cross_lens.py::test_docker_lane_cross_lens_calibrated` | Confirmed | Confirmed (docker-calibrated expectations) | ✓ |
| D10 | Retrieval pool-floor flag (sdk.py L9616) | `is_embedded` forwarded to `run_vector_query` — same API | Same API, HNSW leg | `test_d10_retrieval_pool_floor_flag` | Confirmed | Confirmed | ✓ |
| D11 | `_probe_embedded_busy` / `EmbeddedStoreBusyError` (sdk.py L1058) | Live foreign holder → `EmbeddedStoreBusyError` (fail-fast) | No such concept — concurrent writers on one graph are legal | `test_d11_busy_error_embedded_only` | Confirmed (3 busy-error tests skip visibly on docker, D-2) | Confirmed (visible skip, embedded_only marker) | ✓ |
| D12 | Concurrency last-close-wins (conftest sdk_factory docstring) | Same-process SDKs share the daemon; subprocess class is last-close-wins | Multi-connection-safe; one shared graph, writers coexist | `test_d12_concurrency_semantics` | Confirmed | Confirmed (0 busy errors, live-writer tests) | ✓ |
| D13 | `wipe()` server refusal (tests/_embedded.py L56) | `wipe()` wipes all embedded graphs | `wipe()` refuses; `wipe_server()` test-prefix-filtered, scope-limited | `test_d13_wipe_server_refusal` | Confirmed | Confirmed | ✓ |
| D14 | `hosted_api._make_sdk` fallback (hosted_api.py L78-119) | No URI → embedded fallback at `TORTOISE_DB_PATH` (keepalive-anchored) | URI → `TortoiseSDK(namespace=…)` server mode | `test_d14_hosted_make_sdk_fallback` | Confirmed | Confirmed | ✓ |
| D15 | `atexit_fast_close` (embedded_lifecycle.py) | `TORTOISE_FAST_ATEXIT=1` fire-and-forget SHUTDOWN NOSAVE on ephemeral test-tree servers | No redislite servers — no-op (caller falls through to close) | `test_d15_atexit_fast_close` | Confirmed | Confirmed (no-op on docker — no embedded clients) | ✓ |
| D16 | Version probe (projection/__init__.py L1005-1060) | None on this stack (redislite MODULE LIST is dict-shaped — probe can't parse) — None ≠ failure, engine-probed | ≥ 4.x (4.16.7 observed) | `test_d16_version_probe` | Confirmed (None or ≥ 4.x, engine-probed) | Confirmed (≥ 4.x) | ✓ |

## Mode-split expectations shipped with this task (P1)

- `tests/test_indexes.py`: `EXPECTED_RANGE_DOCKER` (D5 sibling — identical
  range sets) + `EXPECTED_POINT_COMPOSITE_DOCKER` (D6) + the docker-gated
  `test_docker_lane_index_shape`. `is_operator` is deliberately NOT added to
  the D5 range set on either lane (the #522 regression guard, verified
  `_ensure_indexes` L1214-1224).
- `tests/test_cross_lens.py`: `test_docker_lane_cross_lens_calibrated` — the
  docker-calibrated similarity band for the SDK cross-lens surface (D9).
- `tests/test_divergence_conformance.py`: the executable D1–D16 spec (E2E-8).
- `tests/test_uri_env_mutations_declared.py`: the conformance file's env
  control declared in `DELIBERATE_URI_MUTATIONS` (cycle-8 P2-13 — each leg
  controls `TORTOISE_DB_URI` explicitly, the same pattern as E2E-1).

## Documented divergences from the research brief (code reality wins)

1. **D16 embedded version probe returns None, not (4, 18, 3).** The brief's
   claim was that redislite's bundled module reports 4.18.3 through the probe;
   in the current stack `MODULE LIST` returns dict-shaped entries and the
   probe's list-index parse skips them → `_falkordb_version is None`. None is
   NOT a failure — index creation probes the engine directly (the `< 4` gate
   is skipped) — so the conformance expectation is `None or >= 4.x`.
2. **The plan's `DELIBERATE_URI_MUTATIONS` routing table lives in
   `tests/test_uri_env_mutations_declared.py`** (not `test_markers.py`) —
   that is where this task's legs are declared.

## P4 additions (epic #1647 Task 10)

3. **Allowlist composition:** the plan's "~21" target predates the 6 seam
   test files Tasks 1-9 added (test_redirect_seam, test_wipe_server,
   test_tripwire, test_derived_names, test_round_trip_parity,
   test_divergence_conformance — their construction IS the test input, so
   they stay); the actual P4 list is **28**. `test_search_engine`,
   `test_backfill_embeddings_force` (fixed lane-agnostically) and
   `test_indexes` (2 tests fixed below) left the allowlist — their
   constructions are docker-able.
4. **e2e/hosted/test_12_selfhost_migration (Task 10 Step 2 carve-out
   decision):** NOT docker-migratable — the parity journey's source graph
   must be a LOCAL file the `tortoise export` CLI subprocess reads; a
   redirect would silently flip it to the server and void the parity
   assertions. It runs only in the URI-less hosted-e2e lane and stays
   allowlisted as an embedded-by-design surface.
5. **The source-scan enforcement test
   (`test_no_new_raw_embedded_constructions`) is re-keyed at P4** to the
   embedded surface: it flags raw `Redislite(` bypasses anywhere and
   carve-out files missing from the allowlist. Migrated docker-lane files'
   `FalkorProjection(`/`FalkorDB(` constructions redirect under URI + the P4
   URI-required enforcement fails URI-less non-carve-out runs, so they can
   no longer spawn embedded servers in CI.
6. **P4 docker-lane verification fixes (the plan's "13 files were green on
   docker — registry update, not a first run" premise was FALSE; no CI runs
   existed on the branch, so these were latent-red).** The P4 docker smoke
   of every leaving file found and fixed 12 red tests:
   - **CLI/local-file contract (test_export_cli, test_import_endpoint):**
     the harness's DB is a LOCAL file by design (the `tortoise export` CLI
     reads `--db`; the import-endpoint harness patches TortoiseSDK onto one
     temp file). The redirect splits seed (server, derived graph) from read
     (local file) → empty parity. Fixed with a module-level
     `TORTOISE_DB_URI` pop; both files stay allowlisted (embedded-file
     contract, divergence from the plan's leave-list).
   - **test_backfill_embeddings_force:** the script scanned the literal
     `tortoise` graph while the redirect derived `test_<stem>_<hash12>` → 0
     rows. Fixed lane-agnostically (`_run_main` injects `--graph
     proj.graph_name` — the projection's actual name on both lanes).
   - **test_indexes::test_navigation_parity_real_graph:** hardcoded the
     `tortoise` graph name in entityProfile/tortoise_traverse; the redirect
     derives per-path names → KeyError. Fixed to query `p.graph_name`.
   - **test_indexes::test_embedded_reopen_false_equality_correct:** the D7
     repair path is embedded-only (a PRE-#522 stale index cannot exist on
     docker — the D6 composite already indexes is_operator). Marked
     `embedded_only` (D-2=A mechanism: visible skip on docker).
   - **test_projection::test_retract_missing_point_noop:** docker's
     `_ensure_indexes` writes a `(:Meta{key:"point_fts_v2"})` marker for
     its FTS index (embedded skips FTS) → `MATCH (n)` counted 1. Fixed to
     count `:Point` nodes (the no-op contract is about Points; D5-D8
     index-machinery family, docker-calibrated expectation).
