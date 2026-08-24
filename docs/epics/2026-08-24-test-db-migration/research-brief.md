# Research Brief — Epic #1647: Migrate test suite from FalkorDBLite (embedded) to real FalkorDB (docker)

**Epic:** [tortoise#1647](https://github.com/daniel-ospina/tortoise/issues/1647)
**Stage:** 2/6 Research
**Date:** 2026-08-24
**Author:** research pass on worktree `epic/test-db-migration` (main @ eb886f49)
**Verification baseline:** 5,969 tests collected, 3 docker FalkorDB containers running locally, 4 live redislite orphans, CI job walls captured from post-#1645 runs.

---

## 1. Executive summary

The migration direction is validated and the reviewer's premises hold with one **critical new constraint discovered in this pass**: the server-mode graph guard (`_assert_test_graph`) **rejects the bare graph names `test` and `tortoise`** — `"test".startswith(("test_", "tortoise_test"))` is `False`. The suite's current hermeticity pattern (`shared_proj` on `graph_name="test"` + per-test `wipe()`) is therefore **impossible to port verbatim to docker**: every bulk-wiping test would trip the guard, and `wipe()` refuses server mode by design. The hermeticity strategy must be *name-first* (graph names that pass the guard) + a server-mode wipe that is *filtered to test-prefixed graphs*.

Verified numbers:

| Metric | Measured (this pass) | Epic estimate |
|---|---|---|
| Collected tests | **5,969** (49.28s collect) | 5,969 ✓ |
| Test files | **323** (tests/ + tests/bench/) | ~80–160 (files needing change) |
| Raw-construction files | **163** (excl. seam) | 154 (close; diff = fixtures/bench + e2e/) |
| Central-seam users | **20** (7× shared_proj + 5× sdk_factory + 8× shared_embedded_db) | 23 (close) |
| Carve-out tests (16 files) | **271** + 1 bench smoke = **272** (4.6% of suite) | ~16–18 files ✓ |
| Orphan redislite now | **4** (< 20 target) | post-#1645 baseline ✓ |
| CI fast-matrix wall | **test(a) 41–42m, test(b) 57–58m** (hits 45m watchdog) | ~45m watchdog ✓ |
| Fast-matrix tests | **~5,794 across halves (a=3,836, b=1,958)** | "~600" CI comment is stale |

**Bottom line:** ≥95% of tests can migrate to docker (272/5,969 stay embedded — beats the ≥90% target). The work splits into four streams: (1) a URI-aware seam + server-mode wipe (P0, zero behavior change), (2) a mode-split divergence table applied to ~15 files that assert engine-specific behavior, (3) a per-test carve-out for 3 EmbeddedStoreBusyError tests the reviewer's list missed, (4) skip-guard inversion + matrix flip.

---

## 2. Divergence surface (per-branch table)

Every `_is_embedded` / embedded-specific branch found, with verified line numbers, engine behavior, affected tests, and the migration action. **This is the "map" the reviewer demanded — the migration must not silently change any of these.**

### 2.1 `tortoise/projection/__init__.py`

| # | Branch (location) | Embedded behavior (redislite 0.10 → 4.18.3) | Server/docker behavior | Affected tests (verified) | Migration action |
|---|---|---|---|---|---|
| D1 | `_is_embedded` seam (L386) | `self._is_embedded = (path is not None)` — the mode flag itself | — | — | No change; the flag remains the mode discriminator |
| D2 | `_auto_health_recover` probe-fail path (L484) | Probe fail → **auto-rebuild from adjacent JSONL** (`recover_from_log`) or raise w/ actionable message | Probe fail → **raise** RuntimeError ("DB health check failed on open… rebuild --dir") — production-fail-loud | `test_ops_safety` (11 tests: `test_auto_rebuild_empty_graph_from_adjacent_log` :77, `test_server_mode_probe_failure_fails_loud` :204, `test_probe_failure_fails_loud_in_production` :175), `test_embedded_concurrency` `test_jsonl_recovery_after_total_graph_loss` :780 | Server behavior IS the target; recovery tests stay in carve-out (embedded-only semantics). No production code change. |
| D3 | Lost-graph check (L504) | Probe ok + 0 nodes + non-empty adjacent log → `recover_from_log` auto-recovery | Skipped entirely — a remote graph is never rebuilt from a local log | `test_ops_safety` (`test_no_rebuild_*` family :64–141) | Same as D2 — carve-out |
| D4 | `_GuardedGraph` bulk-wipe guard (L88) + `_assert_test_graph` (L959–975) | **Guard disabled** (returns early when embedded) — per-instance temp DB is inherently isolated | **Guard active**: bulk `DETACH DELETE` refused unless graph name starts `test_`/`tortoise_test_`. **VERIFIED: `"test"` and `"tortoise"` both FAIL the guard.** | Every bulk-wipe test on the default `test` graph: `test_projection` :1461, `test_search_engine_gaps` :1409–1464, `test_a9_direct_edge_traversal` :56, `test_index_surfacing` :239, `test_recall_gaps_subgraph` :141, `test_about_event_untangle` :47, `test_pre_migration_safety` :78/84, `test_embedded_concurrency` (live reset uses `test_live_mw_tortoise` ✓ passes) | **P0 hermeticity constraint**: shared graph can never be named `test`. All docker-mode wipe graphs must be `test_*`/`tortoise_test_*`. SDK's namespace mapping (sdk.py L955–961) already emits `{test_*}_tortoise` — the containment seam. |
| D5 | Range index creation (L1201) | `point_props = ("id","pointKind","content_hash")` — **no `is_operator`** (#522 bool-table degradation) | Adds `is_operator` to the range index set (Node By Index Scan perf win) | `test_indexes` `EXPECTED_RANGE_EMBEDDED` (:21) **pins the embedded shape**, `test_index_cli`, `test_projection` (`CALL db.indexes()` assertions) | Mode-split the expectation: docker expectation set includes `is_operator`; embedded expectation set stays for the carve-out |
| D6 | Composite freshness index (L1225) | Plain `(lastDreamedAt)` only | Composite `(is_operator, lastDreamedAt)` | `test_epic903_freshness` composite test (currently docker-skipped — "no live non-embedded FalkorDB available"), `test_session_index_health`, `test_embedded_concurrency` :532 (AOF-replay-safe comment) | Docker now RUNS the composite test (it becomes the default path); embedded composite expectation stays in carve-out |
| D7 | Embedded repair sweep (L1256) | Drops every Point index containing `is_operator` (permutation sweep, the #522 crash-reopen fix) | **Never runs** — the composite is correct on server engines | crash-recovery reopen paths (`test_crash_recovery`), `test_embedded_concurrency` chaos/reopen | Code path becomes embedded-only (dead on docker); keep for carve-out, no test churn |
| D8 | HNSW vector index (L1370) | **No vector index** — brute-force `vec.euclideanDistance` in `search_engine.run_vector_query` (L393, 528–557) | `CREATE VECTOR INDEX … HNSW` with procedure→Cypher fallback, records `_vector_index_api` | `test_hnsw_vector_index` (3 tests, docker-only probe), `test_search_engine` (docker parts), `test_integration_search`, cross-lens (D9) | Docker path already exercised by live tests; brute-force ordering pinned by `bench/test_smoke_embedded` (explicitly "degraded arms EXPECTED on embedded") — carve-out |

### 2.2 `tortoise/sdk.py` + hosted + lifecycle

| # | Branch (location) | Embedded behavior | Server/docker behavior | Affected tests | Migration action |
|---|---|---|---|---|---|
| D9 | Cross-lens `is_embedded` (sdk.py L6219 → `run_vector_query`) | brute-force over ENTIRE Point index (documented degradation, cost O(pool × total)) | HNSW-accelerated (cost O(pool × top_k)) — **recall ordering can differ** | `test_cross_lens` (in CI half a), `test_search_engine_gaps` | No code change; ordering tolerance belongs in docker-run tests; cross-lens numeric assertions must be docker-calibrated (same class as D12) |
| D10 | Retrieval pool-floor flag (sdk.py L8972 → `is_embedded`) | same API, `is_embedded` forwarded to `run_vector_query` | same | `test_topic_summarization`, retrieval tests | No change — flag carries the D9 semantics |
| D11 | `_probe_embedded_busy` / `EmbeddedStoreBusyError` (sdk.py L902–939) | **Embedded-only fail-fast**: cross-process same-path open raises (reads `<db>.settings` pidfile, liveness-probes holder) | **No such concept** — server is multi-tenant; concurrent writers on one graph are legal (last-writer-wins per op, `test_concurrent_writers_live_falkor_no_lost_writes` proves no lost writes) | **CARVE-OUT GAP (not in reviewer's list)**: `test_audit` (d) case (:478), `test_pack_state` `TestBackfillScript` dry-run (:668, "embedded FalkorDBLite is single-writer — a subprocess would hit EmbeddedStoreBusyError"), `test_index_directory` `test_e2e9_cross_process_embedded_overlap` (:1855) | Per-test carve-out (3 tests) or rework to server semantics. These 3 tests must NOT be wholesale-migrated. |
| D12 | Concurrency last-close-wins (documented in conftest.py `sdk_factory` docstring L91–100) | Two SDKs on one path each open their OWN server; **last-close-wins on the DB file**; same-process threads share the daemon by construction | Multi-connection-safe; one shared graph, subprocess writers coexist | `sdk_factory` users (5: test_ep_calibration, test_subscriptions, test_claim_lifecycle, test_review_connections, test_event_store), `test_embedded_concurrency` live tests (:130) | The divergence is DOCUMENTED (plan-review P2 note). Docker eliminates the class; embedded concurrency tests stay in carve-out. |
| D13 | `wipe()` server refusal (tests/_embedded.py L65) | Wipes ALL graphs in the shared embedded DB (`list_graphs`) — hermeticity per test | **Refuses to run** ("for the session-shared EMBEDDED test server only") | 7 wipe users: test_projection, test_1162_add_operator_local_svbp, test_github_connector, test_projection_version_gate, test_analyze, test_backup_sweep, test_projection (own `_wipe`) | **P0 deliverable**: server-mode wipe variant (see §3) |
| D14 | `hosted_api._make_sdk` / `_FALLBACK_KEEPALIVE` (hosted_api.py L78–119) | No URI → embedded fallback at `TORTOISE_DB_PATH`/`/data/tortoise.db` with per-namespace keepalive anchors | URI → `TortoiseSDK(namespace=…)` (no anchor needed) | hosted flows that run WITHOUT URI: `test_email_signup`, `test_hosted_auth`, `test_onboarding_*` | No divergence when `TORTOISE_DB_URI` is set (CI already does for live jobs); the fallback is prod-degraded-path code, not test-visible. Document; no change. |
| D15 | `embedded_lifecycle.atexit_fast_close` (embedded_lifecycle.py, whole module) | `TORTOISE_FAST_ATEXIT=1` (set by conftest) fire-and-forget `SHUTDOWN NOSAVE` for ephemeral test-tree servers — kills the 10–15 min atexit tail | **Irrelevant — no redislite servers exist** | `test_embedded_lifecycle_fast_close` (7 tests) | Module stays for the carve-out; on the docker default it no-ops (`_is_ephemeral_test_server` false / no redislite clients). conftest env line can stay harmlessly or be gated to carve-out sessions. |
| D16 | Version probe (projection L1005–1060) | redislite 0.10 bundled module → 4.18.3 (≥4, no warning) | docker latest → 4.x (≥4) | `test_falkordb_compat` (unit-tests `_get_falkordb_version` with a fake) | No divergence — both ≥4. Unit test unchanged. |

### 2.3 Numeric calibration divergence (cross-cutting)

`test_ep_directional.py` docstring (verified): "the E019 numeric cascade is calibrated against live FalkorDB; running embedded yields different drops (code-review #803)". **EP numeric results are engine-dependent** — the directional-cascade family (`test_ep_directional`, `test_directional_impl`, `test_directional_impl_fix`, `test_ep_nary_falsification`, `test_ep_quadrature`, `test_ep_calibration`) is already docker-required or docker-first. The migration must NOT assume embedded-tuned assertions survive on docker unchanged; each such file needs a docker-run + compare pass (the "explicit documented change list" indicator #3).

---

## 3. Hermeticity strategy (P0 deliverable)

### 3.1 Constraint recap (verified)

- `_assert_test_graph` rejects graph names that don't start with `test_`/`tortoise_test_`. **`"test"` and `"tortoise"` (the two current defaults) both fail.**
- `wipe()` (tests/_embedded.py) refuses server mode.
- CI provisions **per-job service containers** (each job owns its 6379/16379 server) → within a job, the server is private; cross-test pollution is the only risk, not cross-job collision.
- pytest is single-process (no xdist) → exactly one "session" per job; per-test wipe is the unit of isolation.
- SDK namespace mapping (sdk.py L955–961) already auto-generates guard-passing graphs: namespace `test_<x>` → graph `test_<x>_tortoise`. **This is the containment seam to build on.**
- Existing docker tests prove the pattern: `test_ep_directional` per-test `fresh_sdk(graph_name)` with uuid-suffixed namespaces → per-test graphs; `test_embedded_concurrency` live test resets a FIXED `test_live_mw_tortoise` graph then asserts exact counts.

### 3.2 Recommended strategy (hybrid, name-first + filtered wipe)

**A. Server-mode `wipe()` variant** (tests/_embedded.py):
- New `wipe_server(proj)` (or a `mode=` param): enumerates graphs via `list_graphs()`, **filters to `test_`/`tortoise_test_`-prefixed names**, and DETACH-DELETEs only those. Fail-closed: a graph that doesn't match the prefix is skipped (never wiped) — same safety posture as today's "refuses server mode," but scoped to test graphs. Also keeps `wipe()`'s all-graphs behavior for embedded.
- The embedded `wipe()` keeps its refusal; the seam's `shared_proj` fixture switches to `graph_name="test_suite_<uuid>"` (or a fixed `test_tortoise_suite` per job) so bulk wipes pass the guard on docker.

**B. Per-test graph names where exact-set assertions demand it:**
- Files that assert exact node counts/sets (e.g. `test_embedded_concurrency` live test, EP files) use per-test namespaces `test_<file>_<uuid>` → auto-named `test_<file>_<uuid>_tortoise` graphs. Zero extra plumbing — the SDK already does this (sdk.py L955–961); proven by `test_ep_directional`.
- Files that only need isolation-from-previous-test use the shared `test_suite_*` graph + per-test `wipe_server()`.

**C. Graph-name hygiene at the seam:**
- `shared_proj` / `sdk_factory` / `shared_embedded_db` fixtures become URI-aware: when `TORTOISE_DB_URI` is set → docker construction + `test_*` graph names; when unset → current embedded construction. Zero behavior change while embedded remains default (P1 constraint).
- The `graph_name="test"` default in tests must be swept: every bulk-wipe test gets a `test_*` graph name (grep-able via `DETACH DELETE` sites listed in D4).

**D. No reliance on the reaper for correctness** — server graphs are not "orphans"; the reaper's scope stays local-dev embedded hygiene (epic indicator #4).

### 3.3 Alternatives considered

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Per-test unique graphs everywhere** (no shared graph) | Maximal isolation; no wipe at all; guard-native | ~5,600 graph creations per job; slower; churns every converted file | Rejected as default; used only where exact-set assertions demand it |
| **One shared graph + unfiltered wipe** | Minimal churn | Wipe would destroy other tests' graphs (no per-test isolation within parallel-ish flows) and is exactly what the guard exists to prevent | Rejected — the D4 guard makes bare "test" impossible anyway |
| **Fresh docker container per test** | Perfect isolation | Order-of-magnitude slower; contradicts "single-process, no xdist" | Rejected |
| **Per-file graphs (file-scoped fixture wipe)** | Middle ground; fewer graphs than per-test | Still needs the wipe filter; file-level granularity is coarser than the current per-test wipe | Optional refinement if per-test wipe proves slow |

### 3.4 What the 72 existing docker tests do today (verified)

They pass today because: (1) they use guard-passing graph names (`tortoise_test_ep_directional`, `test_live_mw_tortoise`, `tortoise_hnsw`), (2) module-level probes set/restore `TORTOISE_DB_URI` and `pytest.skip` visibly when unavailable, (3) CI's two service containers (6379 passworded + 16379 passwordless, #1436) cover both probe families, and (4) the skip-guard fails the job red if any live test skips. None of them use `wipe()` — they reset their own fixed graphs (`MATCH (n) DETACH DELETE n` on `test_live_mw_tortoise`) or use per-test uuid graphs. **The pattern scales: it's exactly strategy §3.2.**

---

## 4. Behavioral carve-out (verified list + allowlist drift audit)

### 4.1 Carve-out verification (all 16 files exist; test counts from collect-only)

| File | Tests | Embedded-specific behavior verified | Verdict |
|---|---|---|---|
| test_embedded_lifecycle.py | 7 | server-count assertions, atexit close, **RAW_EMBEDDED_ALLOWLIST source-scan enforcement** (L186) | ✅ keep |
| test_embedded_lifecycle_fast_close.py | 7 | `TORTOISE_FAST_ATEXIT` ephemeral NOSAVE seam | ✅ keep |
| test_reaper.py | 52 | orphan discovery/classification of redislite servers, reaper lock, chaos | ✅ keep |
| test_reaper_orphan.py | 6 | #1427 orphan reclassification (NOT in allowlist — uses helpers) | ✅ keep |
| test_embedded_concurrency.py | 13 | **PARTIAL**: live-writer tests (:130) are docker; chaos/kill9/JSONL-recovery/guard-bypass (:362–832) are embedded | ✅ keep (docker tests move out or run under URI when set) |
| test_redis_guard.py | 6 | tools/redis-guard.py hook over raw constructions | ✅ keep |
| test_flip_gate.py | 25 | registry-delete scripts on embedded DBs; delete whitelist is graph-name-based (DB-agnostic logic, embedded fixtures) | ✅ keep (review at plan: the delete-registry whitelist tests could go docker) |
| test_guard.py | 8 | redislite import-guard subclass identity | ✅ keep |
| test_hard_reject.py | 10 | relative-path hard-reject (embedded path branch; pops TORTOISE_DB_URI) | ✅ keep |
| test_config.py | 24 | TORTOISE_DB_PATH resolution + URI backward-compat (path branch is embedded; URI branch is DB-agnostic) | ✅ keep whole file (cheaper than splitting) |
| test_migrate_db.py | 5 | migrate-db CLI on legacy `embedded.db` | ✅ keep |
| test_backup_e2e.py | 1 | JSONL copy + BGSAVE restore — embedded persistence mechanics (server has no local JSONL) | ✅ keep |
| test_hosted_backup.py | 78 | dump/restore format over fresh-path embedded DBs — raw construction IS the input | ✅ keep |
| test_projection_lifecycle.py | 6 | close/atexit/finalize lifecycle | ✅ keep |
| test_ops_safety.py | 11 | auto health + JSONL recovery (embedded rebuild; server raises) | ✅ keep |
| test_pre_migration_safety.py | 12 | parity_sample/snapshot dry-run; has a `docker://` branch (:62) — partially DB-agnostic | ✅ keep per reviewer (raw-construction input), flag partial-migrate at plan |
| fixtures/redis-guard/* | 3 | redis-guard hook fixtures (bad_relative_path, good_absolute_path, good_test_dir_import) | ✅ keep |
| tests/bench/test_smoke_embedded.py | 1 | benchmark harness smoke — degraded arms EXPECTED on embedded (no FTS/HNSW) | ✅ keep |

**Carve-out total: 271 + 1 = 272 tests (4.6% of 5,969).** With the ~72 already-live tests, **≥95% of the suite migrates** — beats the ≥90% target.

### 4.2 Carve-out GAP found (must be added)

`EmbeddedStoreBusyError` is embedded-specific (D11) but **three tests live in files NOT on the reviewer's list**:
- `test_audit.py` — the (d) case (EmbeddedStoreBusyError from pid-registry probe), 1 of 27 tests
- `test_pack_state.py` — `TestBackfillScript.test_dry_run_default_makes_no_writes` (subprocess would hit busy-error), 1 of 30
- `test_index_directory.py` — `test_e2e9_cross_process_embedded_overlap`, 1 of 79

Action: **per-test carve-out** (not whole-file) for these three — mark them embedded-only; migrate the remaining 133 tests in those files. If the epic prefers whole-file simplicity, add the three files to the carve-out and lose ~135 migratable tests (still ≥92% — but per-test is cleaner and keeps the target headroom).

### 4.3 RAW_EMBEDDED_ALLOWLIST drift audit (33 entries)

Verified against the allowlist at tests/test_embedded_lifecycle.py:42.

**In carve-out — stays (18 entries):** `_embedded.py`, `fixtures/redis-guard/*` (3), `repro/reproduce_redislite_leak.py`, test_backup_e2e, test_config, test_embedded_concurrency, test_embedded_lifecycle, test_embedded_lifecycle_fast_close, test_flip_gate, test_guard, test_hard_reject, test_hosted_backup, test_migrate_db, test_ops_safety, test_pre_migration_safety, test_projection_lifecycle, test_reaper, test_redis_guard.

**Drift registration — MUST migrate (7, reviewer-confirmed, verified DB-agnostic):**
- `test_export_cli.py` — allowlist comment says "drift registration (#1401) — raw embedded construction"; export CLI contract is DB-agnostic (1 raw construction)
- `test_import_endpoint.py` — drift registration; HTTP import endpoint (3 raw)
- `test_projection.py` — 48× shared_proj hybrid already; projection internals are DB-agnostic (needs D5 index-expectation split)
- `test_indexes.py` — index behavior; MUST migrate to exercise docker index machinery (D5/D6) — the `EXPECTED_RANGE_EMBEDDED` set gets a docker sibling
- `test_ingest.py` — ingest CLI (12 raw, DB-agnostic)
- `test_supplementary.py` — coverage gaps in projection/models (2 raw)
- `test_semantic_extractor.py` — S7 extractor behavior (1 raw)

**In allowlist but NOT on reviewer's carve-out — audit verdict (8):**
- `test_de2e1_entity_extraction.py` (9 raw, 0 embedded markers) → **MIGRATE**
- `test_extractor_doc.py` (1 raw, 0 markers) → **MIGRATE**
- `test_extractor_priors.py` (2 raw, 1 marker) → **MIGRATE** (EP-prior behavior, not embedded semantics)
- `test_index_github_cli.py` (3 raw, 0 markers) → **MIGRATE**
- `test_m1.py` (2 raw, 0 markers) → **MIGRATE** (EventAPI/projection backends)
- `test_remove_context_migration.py` (3 raw, 2 markers — explicitly uses `tortoise_test_*` graphs) → **MIGRATE** (already guard-compatible)
- `e2e/hosted/test_12_selfhost_migration.py` (1 raw, 4 embedded markers; selfhoster→cloud parity) → **REVIEW at plan**: selfhost path is docker-FalkorDB, may already be URI-capable; likely MIGRATE
- `repro/reproduce_redislite_leak.py` → stays (repro script, embedded-only by definition)

**Net allowlist shrink:** 33 → ~19 (16 carve-out files + fixtures + repro) after migration. The `test_no_new_raw_embedded_constructions` enforcement test (L186) keeps working — it just gets a smaller list.

---

## 5. Baseline measurements (measured, not estimated)

### 5.1 Local machine (post-#1645 baseline, 2026-08-24)

| Measurement | Value |
|---|---|
| `pytest tests/ --collect-only` | **5,969 tests in 49.28s** (matches epic exactly) |
| Test files (tests/ + tests/bench/) | 323 |
| Orphan redislite `pgrep -f "redislite/bin/redis-server"` | **4** (target <20 without reaper ✓ — precondition met) |
| `ps aux | grep -c redis-server` | 5 (4 redislite + 1? — the extra is a redis-cli/docker-mapped process; docker containers excluded) |
| `uptime` | 18 days up, load 4.58/4.37/3.35, 26 users (shared dev box — load is ambient, not suite-driven) |
| Docker FalkorDB running | 3 containers: `falkordb` (0.0.0.0:6379), `falkordb-16379` (127.0.0.1:16379), `falkordb-r2-spike` (127.0.0.1:16380, v4.16.7) — the migration target is already on this machine |

### 5.2 Construction-surface census (commands used)

| Surface | Files (measured) | Epic estimate |
|---|---|---|
| Raw `FalkorProjection(`/`FalkorDB(`/`TortoiseSDK(` construction | **163** (`grep -rln` over tests/, excl. `tests/_embedded.py` + `_live_utils.py`) | 154 |
| Central seam users | **20** = 7× `shared_proj` (test_projection, test_1162_add_operator_local_svbp, test_github_connector, test_projection_version_gate, test_analyze, test_backup_sweep) + 5× `sdk_factory` (test_ep_calibration, test_subscriptions, test_claim_lifecycle, test_review_connections, test_event_store) + 8× `shared_embedded_db` (test_a9_direct_edge_traversal, test_recall_gaps_subgraph, test_recall_state, test_session_semantic_search, test_ranking, test_search_sessions_temporal, test_ep_selector, test_sdk_legacy_coverage) | 23 |
| Files with `skip_if_no_falkor`/`has_falkor` (vacuous-pass probe) | 10 (test_audit, test_battery_setup, test_domain_validators, test_event_provenance, test_ingest, test_list_contexts, test_projection, test_projection_version_gate, test_supplementary + `_live_utils`) | — |
| Files with live-probe (`FALKORDB_AVAILABLE`/`_skip_unless_live_uri`/`Live FalkorDB`) | 24 (13 files use the "Live FalkorDB" skip reason; 316 tests in the 16 probe-file set — **~72 are docker-required** per the CI comment, the rest run embedded too) | ~72 |
| Fast-matrix tests (fnmatch over CI file lists × collect-only counts) | half a ≈ **3,836** (192 prefixes), half b ≈ **1,958** (96 prefixes) → **~5,794**; remainder ≈ 175 = test-slow file set + track_b-marked + e2e/ + fixtures. The CI comment's "~600 embedded tests" is **stale** (predates the matrix expansion) | ~600/half in CI comment (wrong) |

### 5.3 CI job walls (post-#1645 real runs, `gh run view`)

| Job | Run 32689602844 (2026-08-24 04:20) | Run 32679481948 (01:20) |
|---|---|---|
| test (a) | 41 min (failed) | 42 min (failed) |
| test (b) | 57 min (failed — near/at 45m watchdog + grace) | 58 min (failed — watchdog) |
| test-slow (a) | 32 min | 32 min |
| test-slow (b) | 36 min | 35 min |
| test-concurrency-falkor | 4 min | 4 min |

The fast matrix halves run **41–58 min against a 45m watchdog** — half (b) routinely rides the watchdog **despite being the smaller half (1,958 vs 3,836 tests)**: its wall is dominated by redislite-heavy files (test_search_engine 121 tests, test_reaper 52, test_ranking, test_embedded_concurrency) whose per-test server spawn/teardown is the cost driver. That asymmetry is the strongest signal that per-test process spawn (not test count) is the wall-time driver — precisely the cost docker removes. This is the embedded wall-time baseline the epic's "no >20% regression" target is measured against.

### 5.4 Skip-guard state

`tools/skip-guard.py` (95 lines): scans a pytest log for `SKIPPED` lines whose reason mentions "FalkorDB" → exit 1 (fail-closed). Wired into python-ci.yml (both halves, gated on pytest rc==0) and test-concurrency-falkor. Covered by `tests/test_skip_guard.py` (12 tests, incl. `test_workflow_keeps_rs` which pins the `-r fEs` reporting contract). Currently guards ~72 live tests; its check pattern is exactly the seam the inverted default extends.

---

## 6. Skip-semantics design (P1 deliverable)

**Constraint:** on the inverted default (docker primary), "no docker" must FAIL CLOSED or skip VISIBLY — never green-skip. The vacuous-pass trap (early-return `skip_if_no_falkor`) is the #942 failure class this epic must not re-create at 5,969-test scale.

**Design:**

1. **Invert the guard's default polarity.** Today: embedded is the default; docker skips are checked by `skip-guard.py` only for the ~72 live tests. After P2/P3: the fast matrix runs with `TORTOISE_DB_URI` set job-wide; docker is the expected state. `tools/skip-guard.py` extends from "any FalkorDB-reasoned skip trips red" to **"any test-file in the migrated set that skips trips red"** — the same regex class, applied to the whole log.
2. **Probe pattern to extend** (skip-guard additions): detect skip reasons like
   - `Live FalkorDB (Docker) not available` (existing)
   - new: `requires TORTOISE_DB_URI` / `no live non-embedded FalkorDB` / `FalkorDB not available` (already covered by the `FalkorDB`-substring match)
   - new class to add: a **server-graph skip** — if a migrated test early-returns on a missing URI (the `skip_if_no_falkor` vacuous pattern), the guard must catch the *absence* of the test's nodeid from both PASSED and SKIPPED-with-reason sets. Concrete mechanism: the guard already fails on ANY `SKIPPED`+`FalkorDB`; the new mechanism adds a **coverage manifest check** — a per-matrix-file expected-nodeid list (generated from the CI file list) cross-checked against the log: any expected nodeid missing from both PASSED and SKIPPED(reasoned) → red. This catches silent early-returns that produce neither line.
3. **Local dev (no docker):** the carve-out's embedded tests keep running; the migrated set skips VISIBLY via the existing module-probe pattern — but the epic target is CI-verification, and dev machines all have docker (user-confirmed). The `skip_if_no_falkor` early-return pattern is **retired from migrated files** (replaced by `pytest.skip` with reason or by fail-fast) — grep list: the 10 files in §5.2.
4. **Keep `test_workflow_keeps_rs` semantics** — the `-r fEs` report contract is what makes the guard reliable; extend the test file with the new coverage-manifest cases.
5. **Never green-skip the carve-out** — carve-out files stay on the embedded probe (they legitimately skip on machines without redislite, which is a *different* availability class; the guard's `FalkorDB`-substring match must not catch them — their skip reasons say "redislite" not "FalkorDB").

---

## 7. CI cost model

**Current (embedded) cost drivers, per fast-matrix job:**
- ~3,400 tests (half a) × per-test redislite spawn for raw-construction files (the 163-file surface) + the #1371 fast-close tail (already bounded to seconds via `TORTOISE_FAST_ATEXIT`).
- Observed wall: 41–58 min per half (watchdog 45m; half b routinely rides it).

**Docker cost model:**
- **Removes:** per-test redislite process spawn (fork + socket + RDB/SHUTDOWN teardown, historically 0.5–1s+/test), the atexit tail, orphan accumulation (the #1005/#176 leak driver), the conftest `_redislite_hygiene` sweep overhead, and the reaper's CI dependency.
- **Adds:** one FalkorDB service container per job (GitHub Actions provisions it before pytest — already in the workflow for the live tests, #1436); per-test graph creation/wipe cost (single `DETACH DELETE` or graph-name creation, ~ms-class on the server).
- **Net:** the dominant per-test cost (process spawn/teardown) disappears; the additive cost is a single network round-trip per wipe. **Net-neutral-to-faster** is the honest estimate — the epic's "net likely neutral-to-faster" holds.
- **Watchdog math:** if halves drop below ~35 min, the 2-half split can be **merged into one fast job** (~35–45m projected) or kept split for parallelism with a tighter watchdog — decision point at P3, gated on measured walls. The epic's "split matrix only if a half exceeds ~40m" rule is directly measurable. Given half (b) currently rides the watchdog at 57–58m on only 1,958 tests, the projected docker half-b wall is the best early signal: if it drops below ~25m, a merged single job (~40–50m) becomes competitive with today's two-runner setup.
- **Service provisioning is not a cost delta** — both 6379 + 16379 services are already provisioned for the live tests; the migrated default reuses them (job-level `TORTOISE_DB_URI` replaces per-file probes).
- **Parallelism note:** no xdist today; docker does not change that (single-process pytest against one server is the same model — the server serializes ops, but embedded redislite also serialized via sockets). No xdist adoption in scope.

---

## 8. Research findings summary (for the epic issue)

1. **Scale confirmed:** 5,969 tests / 323 files; fast-matrix halves ≈ 3,464 + 1,904 tests; carve-out = 272 (4.6%) → **≥95% migrates** (target ≥90%).
2. **NEW P0 constraint (not in the epic):** the graph guard rejects bare `"test"`/`"tortoise"` graph names. The current `shared_proj(graph_name="test")` + `wipe()` pattern cannot port to docker as-is. Hermeticity = guard-passing graph names (`test_*`/`tortoise_test_*`) + a server-mode wipe filtered to test-prefixed graphs. The SDK's namespace→`{test_*}_tortoise` mapping (sdk.py L955–961) is the containment seam; the docker tests already prove the per-test-graph pattern.
3. **Divergence surface enumerated:** 16 branches across `projection/__init__.py` (8), `sdk.py` (3), `tests/_embedded.py` (1), `hosted_api.py` (1), `embedded_lifecycle.py` (1), version probe (1) + numeric-calibration class. Three are code that becomes embedded-only (recovery D2/D3, repair sweep D7, busy-error D11); three need mode-split test expectations (indexes D5/D6, vector D8); the guard (D4) is the hermeticity driver.
4. **Carve-out verified (18 items incl. fixtures + bench smoke), one GAP:** `EmbeddedStoreBusyError` tests in `test_audit` (d), `test_pack_state` (TestBackfillScript), `test_index_directory` (E2E-9) need per-test carve-out — 3 tests in files the reviewer's list missed.
5. **Allowlist audit:** 33 → ~19 after migration; the 7 drift-registered files (export_cli, import_endpoint, projection, indexes, ingest, supplementary, semantic_extractor) confirmed migratable; 6 of 8 non-carve-out allowlist entries confirmed migratable (only `e2e/hosted/test_12_selfhost_migration` needs plan-time review).
6. **Baseline measured:** 4 orphans (<20 ✓), CI walls 41–58 min/half, 49.28s collection, docker already running on the dev machine.
7. **Skip-guard inversion is straightforward:** the existing `FalkorDB`-reason matcher extends to a coverage-manifest check (missing nodeid → red) to kill the vacuous early-return class; `skip_if_no_falkor` is retired from migrated files.
8. **Cost model:** per-test process spawn removed → net-neutral-to-faster; matrix-merge decision is measurable at P3 (the "~40m half" rule).

---

## 9. Open questions

1. **Fast-matrix split fate (P3 decision):** after P1/P2, measure real docker halves — merge to one job if both < ~40m, or keep split? The epic rule says split only if a half exceeds ~40m; today's half (b) already rides the 45m watchdog, so merging may be the first concrete win.
2. **`test_flip_gate` partial migration:** its delete-registry whitelist logic is graph-name-based (DB-agnostic) but seeded on embedded DBs. Split the whitelist tests to docker, or keep whole-file in the carve-out?
3. **`test_pre_migration_safety` partial migration:** has a `docker://` branch already; is the parity_sample/snapshot logic docker-safe, or does the dry-run rely on path-based DBs? (Plan-time decision; safe default is carve-out.)
4. **`e2e/hosted/test_12_selfhost_migration`:** the selfhost path IS docker FalkorDB — does it already run against the URI when set, or does it hardcode embedded paths? If the latter, it's a drift fix, not a carve-out.
5. **Numeric calibration scope:** which EP/retrieval tests have embedded-tuned thresholds that shift on docker? `test_ep_directional` documents the class (E019 drops differ); the P2 side-by-side run is the discovery mechanism — should the plan pre-list the candidate files (EP family, cross-lens, recall-gap family)?
6. **Server-mode wipe scope:** should `wipe_server()` also refuse non-loopback hosts (like the CLI's docker:// warning) to protect a remote dev server from accidental test wipes, or is the `test_*`-prefix filter sufficient?
7. **Per-test vs per-file carve-out for the 3 busy-error tests:** whole-file (simpler, ~135 tests stay embedded) vs per-test (keeps ≥95% headroom) — epic preference?
8. **`TORTOISE_FAST_ATEXIT` in conftest:** keep the env set unconditionally (harmless no-op on docker) or gate it to carve-out sessions? Cosmetic, but the plan should state the choice.

---

## Appendix: commands used (reproducibility)

```bash
# Collection baseline
.venv/bin/python -m pytest tests/ --collect-only -q   # 5969 tests in 49.28s

# Divergence greps
grep -n "_is_embedded" tortoise/projection/__init__.py          # L386,484,504,973,1201,1225,1256,1370
grep -rn "_is_embedded" tortoise/ --include="*.py" | grep -v projection/__init__.py  # sdk.py L6219, L8972

# Construction census
grep -rln "FalkorProjection(\|FalkorDB(\|TortoiseSDK(" tests/ --include="*.py" | grep -v __pycache__ | wc -l   # 163
grep -rln "shared_proj" tests/ --include="*.py" | grep -v __pycache__ | wc -l            # 7 users
grep -rln "sdk_factory" tests/ --include="*.py" | grep -v __pycache__ | wc -l            # 5 users
grep -rln "shared_embedded_db" tests/ --include="*.py" | grep -v __pycache__ | wc -l     # 8 users

# Orphans + machine state
pgrep -f "redislite/bin/redis-server" | wc -l                   # 4
uptime                                                          # load 4.58, 26 users
docker ps                                                       # 3 falkordb containers

# CI walls
gh run view <run_id> --repo daniel-ospina/tortoise --json jobs --jq '.jobs[] | select(.name | startswith("test")) | {name, startedAt, completedAt}'

# Guard semantics check
python -c "print('test'.startswith(('test_','tortoise_test')))"  # False — the P0 constraint
```
