# Scope Brief — Epic #1647: Migrate test suite from FalkorDBLite (embedded) to real FalkorDB (docker)

**Epic:** [tortoise#1647](https://github.com/daniel-ospina/tortoise/issues/1647)
**Stage:** 3/6 Scope — **HUMAN GATE**
**Date:** 2026-08-24
**Inputs:** Research Brief (verified 2026-08-24) + Epic #1647 + current-repo inspection (worktree `epic/test-db-migration`)
**Status:** pending human approval

---

## 1. Scope summary — the phased plan at a glance

The migration is a **phased strangler rollout** mandated by the strategy review. Each phase has a measurable gate; no phase flips the default until the previous phase's gate is green. The single hard rule throughout: **embedded remains the default until a phase explicitly inverts it**, and **no docker flip ever produces a green-skip** (the #942 vacuity class at 6,837-test scale).

| Phase | Name | Default DB | Ships | Gate |
|---|---|---|---|---|
| **P1** | Seam + hermeticity (zero-change) | **embedded** (unchanged) | URI-aware seam (`shared_proj`/`sdk_factory`/`shared_embedded_db`), server-mode `wipe_server()`, class-level URI-aware redirect (inert), graph-name sweep, divergence-expectation split (indexes/vector) | Full embedded suite green, **zero behavior change** (diff-verified) |
| **P2** | One-half flip | **half b → docker**, half a stays embedded | Job-level `TORTOISE_DB_URI` on half b, skip-guard coverage manifest (half b), raw-construction redirect activation | Half b green on docker; side-by-side divergence table confirmed; half a still green embedded |
| **P3** | Both halves | **docker** (both halves) | Flip half a, skip-guard manifest covers both halves, fast-matrix wall measured vs baseline | Full fast matrix green on docker; wall ≤ 20% of baseline; 0 divergence flake in 5+ runs |
| **P4** | Allowlist/reaper shrink | docker (default) | `RAW_EMBEDDED_ALLOWLIST` 34→~21, reaper demoted to local-dev hygiene, orphan-assert relaxed for docker halves | Carve-out (342) still green on embedded; allowlist enforcement test passes with shrunk list; orphan count < 20 without scheduled reaper |

**Verification baseline (measured, research-brief §5):** 6,837 collected tests (40.60s collect) · fast-matrix halves a=2,606 (151 files) / b=2,654 (150 files) · carve-out **342 tests** (17 files + 3 busy-error per-test = 5.0% of suite) → **≈95% migrates** (target ≥90%) · CI walls a=41–42m, b=57–58m (half b rides the 45/55m watchdog — already marginal) · post-#1645 orphan baseline **4** (<20 precondition ✓) · docker services already provisioned in CI (falkordb 6379 passworded + falkordb-legacy 16379 passwordless, #1436).

**What ships overall (end state):**
1. Default `pytest` runs against `TORTOISE_DB_URI` (docker FalkorDB) for DB-agnostic tests; embedded remains ONLY for the behavioral carve-out (342 tests).
2. Zero unexpected docker-vs-embedded divergence; an explicit documented change list (research-brief §2, D1–D16) is enforced by mode-split test expectations, not by accident.
3. The reaper's scope shrinks to local-dev embedded hygiene; no CI/dev dependency on the reaper for correctness.
4. Missing docker fails closed or skips visibly (extended skip-guard); never green-skip.

---

## 2. Phase definitions

### Phase 1 — Seam + hermeticity (zero behavior change)

**Goal:** build every mechanism the flip needs, with **embedded still the default and the suite provably unchanged**. The only test-visible changes in P1 are expectations that were already wrong on docker (index-shape assertions get docker siblings; nothing embedded changes).

#### In scope
- **URI-aware seam (tests/_embedded.py + tests/conftest.py):**
  - `shared_proj`, `sdk_factory` (conftest.py:83), `shared_embedded_db` become URI-aware: when `TORTOISE_DB_URI` is set → docker construction with guard-passing graph names; unset → today's exact embedded construction.
  - `shared_proj` default graph name `"test"` → guard-passing `test_suite_<job-uuid>` when in docker mode (embedded mode keeps `"test"` — zero change for the embedded default).
  - `_redislite_hygiene` session sweeps: gate to sessions that actually created embedded servers (no-op when docker default) — behavior-neutral today (embedded default still runs them).
- **Server-mode wipe variant `wipe_server(proj)` (tests/_embedded.py):** enumerates graphs via `list_graphs()`, **filters to `test_`/`tortoise_test_`-prefixed names only**, DETACH-DELETEs those. Fail-closed: non-test-prefixed graphs are skipped, never wiped. The existing `wipe()` keeps its server-mode refusal unchanged (embedded all-graphs wipe untouched).
- **Class-level URI-aware redirect (tortoise/projection/__init__.py):** `FalkorProjection(path=...)` redirects to the server when `TORTOISE_DB_URI` (supported scheme) is set — see §4. **Inert in P1 because the default run does not set `TORTOISE_DB_URI`.** Built, unit-tested, dormant.
- **Graph-name sweep:** every bulk-wipe / exact-set test file that constructs on the bare `"test"` graph (35 `graph_name="test"` sites, 93 no-graph-name raw sites) gets a guard-passing name. Embedded mode is unaffected (guard is disabled embedded); the sweep is a name-only change with a red-herring check that no bulk-wipe test still targets `"test"`/`"tortoise"` in server mode.
- **Divergence-expectation split (the documented change list, research §2):**
  - D5/D6 — `test_indexes.py`: `EXPECTED_RANGE_EMBEDDED` gets a docker sibling; the composite `(is_operator, lastDreamedAt)` assertion (D6) becomes docker-only; do **not** add `is_operator` to the D5 range set (#522 guard).
  - D8 — vector ordering: brute-force ordering stays pinned by the carve-out (`bench/test_smoke_embedded`); HNSW assertions stay docker-only.
  - D9/cross-lens numeric calibration: docker-calibrated expectations added; embedded expectations untouched (carve-out or mode-split).
- **CI:** *no matrix/service change in P1.* Optionally add a CI step that asserts the redirect is inert (run a representative embedded file with `TORTOISE_DB_URI` unset and confirm embedded construction path is taken — cheap regression guard).

#### Out of scope (P1)
- No matrix flip; no job-level `TORTOISE_DB_URI`; no default inversion.
- No changes to carve-out files (lifecycle/reaper/busy-error/recovery) — they keep running embedded exactly as today.
- No `RAW_EMBEDDED_ALLOWLIST` changes (P4).
- No reaper changes (P4).
- No changes to `sdk.py` namespace→graph mapping (already correct, sdk.py L1115–1123) or `hosted_api` fallback (D14, documented no-op).

#### Acceptance criteria (P1)
1. Full embedded suite (6,837 tests) green — **same as pre-P1 baseline**; no test file changes behavior in embedded mode (name-only sweep; index-split only adds docker siblings).
2. `wipe_server()` unit-tested: wipes only `test_*` graphs, refuses/skips non-test graphs, refuses non-loopback hosts (per research Q6 decision).
3. Class-level redirect unit-tested: with `TORTOISE_DB_URI` unset → embedded path identical to today (assert `_is_embedded is True`); with URI set → server path with derived `test_*` graph name.
4. Zero redislite orphans attributable to P1 changes (baseline 4 preserved).
5. CI: both halves green, wall within 20% of baseline (41–42m / 57–58m).

#### Risks + mitigations
| Risk | Mitigation |
|---|---|
| The name-only graph sweep accidentally changes embedded behavior (graph-name is observable in embedded mode via `graph_name` attribute) | Sweep is mechanical (name replacement); embedded tests asserting `graph_name == "test"` are grep'd and updated consciously; the P1 diff review checks embedded-mode assertions explicitly |
| `wipe_server()` misclassifies a real graph as test-prefixed | Prefix filter is exact (`test_`/`tortoise_test_`); fail-closed default (skip, never wipe); non-loopback refusal per decision D-4 |
| Redirect has a latent prod impact (a path construction in prod code when URI set) | Scope the redirect to `path=` constructions only (decision D-1, recommended option a); guard still refuses non-test bulk-wipes in server mode — fail-closed |
| Index-split churn breaks docker tests that already pass | The docker siblings are additive; existing docker-only assertions unchanged; `test_indexes` runs both expectation sets in the mode they belong to |

---

### Phase 2 — One-half flip (side-by-side divergence discovery)

**Goal:** run ONE fast-matrix half on docker against the other half embedded, so every divergence the research table predicted is **observed and confirmed on real CI** before the default inverts. Half **b** is the flip candidate: it is the redislite-heavy half (test_search_engine 121, test_reaper 52, test_ranking, test_embedded_concurrency) whose wall (57–58m) already rides the watchdog — the strongest signal that per-test spawn is the cost driver docker removes.

#### In scope
- **CI half-b flip:** matrix include for `half: b` sets job-level `TORTOISE_DB_URI: docker://:falkordb@localhost:6379` (passworded service, already provisioned). The existing `falkordb` + `falkordb-legacy` service block is unchanged.
- **Redirect activation:** with job-level URI set, the class-level redirect (P1) fires for every raw `path=` construction in half-b files → all half-b DB-agnostic tests now run against docker automatically. This is the **P2-flip blocker resolution** — without the redirect, ~half of half-b's raw constructions would land on graph `"tortoise"` and raise on first bulk-wipe (safe but red).
- **Skip-guard extension (coverage manifest):** `tools/skip-guard.py` gains a **per-matrix-half expected-nodeid manifest** (generated from `tools/ci_selection.py` half lists × collect-only): any nodeid in the manifest missing from BOTH `PASSED` and `SKIPPED(reasoned)` → **red**. **Scope-review M2:** the manifest needs a PASSED-nodeid source — the pinned `-r fEs` summary (asserted by test_skip_guard.py test_workflow_keeps_rs) emits NO PASSED lines; the manifest must collect nodeids from junitxml or `-v` (pick in plan) and test_workflow_keeps_rs must be updated. Also `test_missing_log_is_not_a_failure` pins missing-log → exit 0 — under the manifest model a missing log = every expected nodeid absent → must be RED on migrated halves; that test flips. This kills the vacuous early-return class (`skip_if_no_falkor` retired from migrated half-b files, replaced by visible `pytest.skip` with reason or fail-fast). Existing `FalkorDB`-reason matcher stays.
- **Divergence confirmation pass:** a side-by-side comparison of half-b results (docker) vs the research table (§2) — each predicted divergence (D2/D3 recovery carve-out, D5/D6 index expectations, D8 vector ordering, D9 numeric calibration, D12 concurrency) is checked against the actual run; unexpected divergences become P2 blockers.
- **Carve-out per-test marking:** the 3 busy-error tests (`test_audit` (d), `test_pack_state` TestBackfillScript dry-run, `test_index_directory` E2E-9) are marked embedded-only (per decision D-2, recommended).

- **Carve-out file exemption (scope-review H1 — REQUIRED):** 6 carve-out files ride half b (`test_embedded_lifecycle_fast_close`, `test_redis_guard`, `test_guard`, `test_config`, `test_ops_safety`, `test_pre_migration_safety`). With job-level URI set, the class-level redirect would flip them to docker and break their embedded-specific assertions (recovery tests raise instead of auto-rebuild; atexit-seam asserts find no redislite server). Fix: a **per-file redirect exemption** — `TORTOISE_TEST_NO_REDIRECT=<comma-separated file stems>` honored by the class-level redirect (a file in the set keeps `path=` embedded construction even when URI is set). The 6 carve-out files in half b are added at P2; the 3 busy-error PER-TEST carve-outs use the D-2 embedded-only MARKER (skip visibly on docker, pass on embedded — E2E-3 / P2 AC5), a skip mechanism DISTINCT from the per-file redirect exemption; TORTOISE_TEST_NO_REDIRECT stays file-stem-only. ALTERNATIVE (rejected for P2): matrix re-partition to move carve-out files out of half b — requires selector/manifest + drift-guard updates (ci_selection pins halves fail-closed) — deferred to P4 if the exemption proves fragile.

#### Out of scope (P2)
- No half-a flip (stays embedded — the control arm).
- No allowlist shrink, no reaper demotion (P4).
- No test-slow / e2e / track-b changes — they are not in the fast matrix and follow in P3/P4.
- No xdist adoption; no matrix merge (P3/P4 decision).

#### Acceptance criteria (P2)
1. Half b green on docker with job-level URI; **no FalkorDB-reasoned skips** (skip-guard red otherwise).
2. Half a (embedded control) green — same as P1.
3. Observed divergences match the research table exactly; **zero unexpected** divergence (each unexpected one is a P2 blocker, fixed before P3).
4. Half-b wall measured: expected drop from 57–58m (watchdog-marginal) toward ≤ ~40m (docker removes spawn cost); recorded as the P3 merge-decision input.
5. The 3 busy-error tests skip visibly (embedded-only marker) on the docker half and pass on the embedded half — never green-skip.

#### Risks + mitigations
| Risk | Mitigation |
|---|---|
| A half-b file's assertions are embedded-calibrated and break on docker (EP numeric cascades, recall ordering) | The divergence-confirmation pass is the gate; docker-calibrated expectations are added in P2 (same class as D9); the documented change list absorbs them, not silent fixes |
| Raw constructions with explicit non-test `graph_name` trip the guard at scale | Redirect honors explicit `graph_name`; non-test names fail closed on bulk-wipe only — the sweep in P1 already moved test files to `test_*` names; any straggler is a loud, greppable error, not a silent collision |
| Missing docker service → whole half b fails/skips | Skip-guard coverage manifest → red (fail-closed). Never green-skip by construction |
| Half-b wall does not improve (docker graph creation cost offsets spawn savings) | Measured at P2 gate; if wall ≥ embedded baseline +20%, P3 is blocked pending investigation (graph-name reuse / wipe cost tuning) |

---

### Phase 3 — Both halves + default inversion

**Goal:** the fast matrix runs entirely on docker; embedded runs only the carve-out.

#### In scope
- **Flip half a** to job-level `TORTOISE_DB_URI` (same as half b in P2). Both halves now docker.
- **Skip-guard manifest extended to both halves** (expected-nodeid set per half, same mechanism as P2).
- **`TORTOISE_FAST_ATEXIT` + `_redislite_hygiene` gating:** on docker halves, the conftest env line and hygiene sweeps become no-ops (gated on whether any embedded server was actually created) — cosmetic but removes the redislite dependency from the default path.
- **Orphan-assert step re-targeted:** docker halves expect ~0 redislite orphans (assertion flips from "bound the leak" to "no leak on docker"; carve-out-only suites keep the bounded assertion).
- **Matrix-merge decision (decision D-3):** measured half walls at P2/P3 gate → merge to a single fast job if both halves < ~40m (epic rule: "split matrix only if a half exceeds ~40m"), or keep the 2-half split with a tightened watchdog. Default recommendation: **keep split through P3** (side-by-side divergence confidence), decide merge at P4.
- **Non-fast surfaces (test-slow, track_b, e2e/, fixtures):** flip to docker default in this phase (same URI mechanics, no per-file probes), with the same skip-guard manifest coverage. Carve-out files (342 tests incl. fixtures/redis-guard + bench smoke) remain embedded-only.

#### Out of scope (P3)
- No allowlist shrink (P4). No reaper demotion (P4).
- No embedded-path removal — the embedded engine stays fully supported (carve-out + prod fallback).

#### Acceptance criteria (P3)
1. **Full fast matrix (both halves) green on docker services**; half walls within 20% of embedded baseline (half a ≤ ~50m; half b must clear the 55m watchdog — target ≤ ~45m).
2. Skip-guard manifest passes with **zero FalkorDB-reasoned skips and zero missing nodeids** on both halves.
3. **0 flaky failures attributable to docker-vs-embedded divergence in 5+ consecutive CI runs** (epic indicator #2).
4. Orphan assert on docker halves: ~0 redislite orphans.
5. Carve-out suite (342 tests) still green on embedded (run in CI: a dedicated embedded-only job or the carve-out files in a URI-unset job).

#### Risks + mitigations
| Risk | Mitigation |
|---|---|
| Hidden cross-file state on the shared docker graph (a file wipes another's data) | Hermeticity mechanics (§5): per-test graph names for exact-set files, filtered `wipe_server()` for shared-graph files; the guard makes bare-`test` wipes impossible |
| Divergence flakes at 5,000+ test scale (ordering, timing, concurrency) | The research table is the approved divergence list; any flake is triaged against it — documented divergence = tolerated, unexpected = blocker |
| CI service instability (falkordb container flake) | Services already health-checked (`redis-cli ping`); skip-guard flips red on skip; a service failure is a visible infra failure, not a silent green |
| Wall regression from graph creation/wipe at 5,000 tests | Measured at P2; if wipe-per-test proves slow, the per-file-graph refinement (research §3.3) is the approved fallback |

---

### Phase 4 — Allowlist/reaper shrink

**Goal:** delete the debt — the drift registry shrinks, the reaper is demoted, the migration's end state is explicit.

#### In scope
- **`RAW_EMBEDDED_ALLOWLIST` shrink 34 → ~21:** the 7 drift-registered files migrate (already docker-verified DB-agnostic: test_export_cli, test_import_endpoint, test_projection, test_indexes, test_ingest, test_supplementary, test_semantic_extractor) + the 6 non-carve-out entries migrate (test_de2e1_entity_extraction, test_extractor_doc, test_extractor_priors, test_index_github_cli, test_m1, test_remove_context_migration); `e2e/hosted/test_12_selfhost_migration` reviewed (selfhost path IS docker FalkorDB — likely a drift fix); `repro/reproduce_redislite_leak.py` + fixtures + 16 carve-out files stay. The `test_no_new_raw_embedded_constructions` enforcement test (test_embedded_lifecycle.py:186) keeps working against the smaller list.
- **Reaper demotion:** scheduled reaper scope narrows to local-dev embedded hygiene; CI loses its correctness dependency on it (docker halves produce no orphans). Reaper code stays (embedded carve-out + dev machines still spawn redislite servers) but is no longer a CI-correctness gate.
- **Documentation:** the migration's divergence change list (D1–D16) is filed as the canonical reference for "what legitimately differs docker vs embedded" (epic indicator #3).
- **Post-#1645 orphan verification:** run the default suite on a dev machine WITHOUT the scheduled reaper; assert orphan count stays < 20 (epic indicator #2 — the precondition, re-measured at end state).

#### Out of scope (P4)
- No removal of the embedded engine itself (carve-out, dev fallback, prod `TORTOISE_DB_PATH` mode all stay).
- No changes to the 342-test behavioral carve-out content (their behavior is the point).
- No xdist, no test-count reduction.

#### Acceptance criteria (P4)
1. Allowlist enforcement test passes with the shrunk list (~21 entries); the 13 migrated files run on docker with no embedded markers.
2. Default `pytest` run requires `TORTOISE_DB_URI`; embedded-only carve-out is the sole embedded surface.
3. **Orphan count < 20 on a dev machine without the scheduled reaper** (end-state re-measure of the 4-orphan baseline).
4. Epic indicators all green: ≥90% docker (measured ≈95%), 0 divergence flake in 5+ CI runs, orphan <20, fast-matrix wall ≤ 20% baseline regression.

#### Risks + mitigations
| Risk | Mitigation |
|---|---|
| A drift-registered file is less DB-agnostic than the audit believed | P2/P3 already ran these files on docker (they're in the matrix); the P4 move is a registry update, not a first run |
| Reaper demotion strands dev-machine orphans | Local hygiene still sweeps (conftest `_redislite_hygiene` runs for embedded sessions); the reaper cron stays for dev boxes — only CI's correctness dependency is removed |
| Allowlist enforcement test (L186) breaks on the smaller list | The test reads the list from source; the shrink is a list edit + the enforcement test asserts no NEW constructions — passes once the list matches reality |

---

## 3. High-level E2E tests (end-state verification — runnable at the end)

Each E2E is runnable in the final state and gates a specific phase. Marked `[docker]`, `[embedded]`, or `[both]`.

### E2E-1 — DB-agnostic round-trip is identical docker vs embedded
- **Scenario:** a point create → search round-trip (the simplest DB-agnostic op) yields identical results on both backends.
- **Setup:** parametrized over `TORTOISE_DB_URI` set (docker) and unset (embedded); same test body; guard-passing graph name in both modes (name differs by mode, semantics identical).
- **Assertion:** point id, content_hash, search hit list, and vector/brute-force results identical where the divergence table says identical (D1/D5-identical paths); where the table documents divergence (D6 composite, D8 ordering), each side asserts its documented expectation.
- **Gates:** P1 (mechanism works, embedded unchanged) → P2 (docker half proves it).

### E2E-2 — Bulk-wipe runs on docker without tripping the graph guard
- **Scenario:** a test file that wipes its graph between tests runs green on docker.
- **Setup:** shared-graph tier (`test_suite_<uuid>` via URI-aware `shared_proj`) + per-test `wipe_server()`; graph name passes `_assert_test_graph`.
- **Assertion:** no `RuntimeError` from the guard; the graph is empty at each test start (exact count asserts pass); a control test proves a bare-`test` wipe still raises (guard intact).
- **Gates:** P1 (wipe_server unit) → P2 (docker half runs wipe-heavy files).

### E2E-3 — Concurrency: multi-tenant semantics, no EmbeddedStoreBusyError
- **Scenario:** a cross-process / multi-SDK concurrency test runs on docker and never raises `EmbeddedStoreBusyError`; concurrent writers on one graph coexist (last-writer-wins per op, no lost writes).
- **Setup:** the live-writer portion of `test_embedded_concurrency` (:130) + `test_concurrent_writers_live_falkor_no_lost_writes` under job-level URI; the 3 busy-error tests remain embedded-marked (skip visibly on docker, pass on embedded).
- **Assertion:** 0 busy errors; all concurrent writes present; the documented D11/D12 divergence holds (multi-tenant on docker, single-writer busy-error on embedded).
- **Gates:** P2 (docker half proves no busy errors) → P3 (both halves).

### E2E-4 — Carve-out (342 tests) still passes on embedded
- **Scenario:** the behavioral carve-out — lifecycle (7+7), reaper (52+6), ops-safety/recovery (11), guard/hard-reject/redis-guard, config, migrate/backup, projection-lifecycle, hosted-backup (78), pre-migration-safety, bench smoke — runs green on embedded, unchanged by the migration.
- **Setup:** a CI job (or local run) with `TORTOISE_DB_URI` unset running exactly the carve-out file set; skip-guard exempts them from the docker manifest (their skips are redislite-availability class, not FalkorDB-availability — research §6.5).
- **Assertion:** all 342 pass; recovery auto-rebuild (D2/D3) and busy-error (D11) semantics verified embedded-only. **Scope-review M1:** the assertion is NOT "no file references TORTOISE_DB_URI" — 7 carve-out files legitimately reference it for their live/URI branches (test_config, test_embedded_concurrency, test_reaper, test_hard_reject, test_flip_gate, test_pre_migration_safety, test_migrate_db). The correct assertion: no carve-out file's EMBEDDED path depends on `TORTOISE_DB_URI` being set.
- **Gates:** P1 (untouched) → P4 (post-shrink, registry consistency).

### E2E-5 — Fast matrix (both halves) green on docker; wall within 20% of baseline
- **Scenario:** the default `pytest` fast matrix runs against the CI docker services and completes green.
- **Setup:** job-level `TORTOISE_DB_URI` on both halves; falkordb (6379) + falkordb-legacy (16379) services; skip-guard coverage manifest active.
- **Assertion:** both halves pass; half a ≤ ~50m and half b clears the 55m watchdog with margin (target ≤ ~45m) — i.e., no >20% regression vs the embedded baseline (41–42m / 57–58m); wall recorded for the P3/P4 merge decision.
- **Gates:** P2 (half b only) → P3 (both halves, full gate).

### E2E-6 — Missing docker → fail-closed / visible skip, never green-skip
- **Scenario:** simulate a docker outage (service removed, URI unset) and confirm the run cannot go silently green.
- **Setup:** run the migrated set without `TORTOISE_DB_URI`; skip-guard scans the log.
- **Assertion:** every migrated test either fails loudly (connect error) or skips with a `FalkorDB`-reason → skip-guard exits 1 (job red); no nodeid vanishes from both PASSED and SKIPPED (coverage-manifest check trips red); the carve-out (embedded) still passes — a *different* availability class, guard-exempt.
- **Scope-review M3 — backend-identity tripwire:** the coverage manifest only catches nodeids that VANISH (skip/early-return), NOT nodeids that silently PASS on the wrong backend (redirect inert + embedded succeeds → job green). The "fails loudly" guarantee needs a backend-identity check on migrated halves: a conftest-level assertion (sampled `_is_embedded is False` on docker halves) OR hard URI gating in migrated files. The existing `_live_utils` skip class is excluded from the guard and cannot serve as the tripwire.
- **Gates:** P2 (manifest on half b) → P3 (both halves).

### E2E-7 — Zero redislite orphans on docker halves; bounded on carve-out
- **Scenario:** after a full docker-matrix run, no redislite orphan servers accumulate.
- **Setup:** the existing `Assert no redislite orphans` CI step, re-targeted: docker halves expect ~0; carve-out/embedded jobs keep the bounded (<20) assertion.
- **Assertion:** docker halves: 0 orphans (post-run `pgrep -f "redislite/bin/redis-server"`); the conftest `_redislite_hygiene` end-sweep logs no action needed on docker.
- **Gates:** P3 (flip complete) → P4 (reaper demotion — CI no longer depends on it).

### E2E-8 — Divergence change-list conformance (the documented change list)
- **Scenario:** each D1–D16 divergence behaves exactly as documented — no silent engine differences beyond the list.
- **Setup:** a conformance test file that asserts the divergence table: D2/D3 recovery auto-rebuild raises on docker (carve-out-only on embedded); D6 composite index exists on docker and not embedded; D8 HNSW index created on docker, brute-force ordering on embedded (bench smoke); D11 busy-error embedded-only; D12 multi-tenant on docker; D14 hosted fallback untouched.
- **Assertion:** all conformance asserts pass in both modes; the file is the executable version of the epic indicator #3 change list.
- **Gates:** P1 (expectations split) → P2 (side-by-side confirmation) → P3 (both modes enforced).

---

## 4. The class-level URI-aware redirect design (P2-flip blocker)

**Problem:** ~93 raw `FalkorProjection(path=...)` constructions in tests pass no `graph_name`; on docker they'd land on the server default `"tortoise"` — which **fails `_assert_test_graph`** (bare `tortoise` is not `test_*`/`tortoise_test_*`) on the first bulk-wipe. That's the *safe* failure direction (raise, not silent collision) but it means every such file must redirect. Per-file URI branches across ~150 files is churn and drift; the research verdict is a **class-level redirect — one place**.

### Mechanism (one place: `FalkorProjection.__init__`, the `path is not None` branch)

```
if path is not None:
    uri = os.environ.get("TORTOISE_DB_URI")
    if uri and is_db_uri(uri):                     # docker://, redis://, rediss://
        # REDIRECT: construct server-mode from the URI (same code path as from_uri)
        #   - _is_embedded = False  (server mode; guard ACTIVE)
        #   - host/port/user/pass parsed from the URI (from_uri semantics)
        #   - graph_name = caller's explicit graph_name  OR  derived test name (§4.1)
        #   - skip the embedded-only branches (AOF, redislite FalkorDB subclass,
        #     resolve_db_path) entirely
    else:
        # today's embedded construction, byte-for-byte
```

- **Inertness in P1:** the default run does not set `TORTOISE_DB_URI` → the redirect never fires → the embedded path is **identical** to today. This is verified by a unit test (URI unset → `_is_embedded is True`, embedded subclass used).
- **Activation:** fires exactly when `TORTOISE_DB_URI` (supported scheme) is set — the same condition under which `TortoiseSDK` already switches to `from_uri` (sdk.py `_get_proj`, `self._db_uri is not None`). The class-level redirect makes **raw path-constructions symmetric with SDK behavior**: URI wins.
- **Explicit `host=` constructions** are already server mode — no redirect needed, untouched.
- **No-arg `FalkorProjection()`** resolves via `resolve_db_path()` today (embedded canonical); redirect scope decision is **D-1** (recommended: `path=` only in P2; extend to no-arg in P3 only if the sweep finds no-arg constructions in the migrated set).

### 4.1 Graph-name derivation

| Case | Derived name | Why |
|---|---|---|
| Caller passed explicit `graph_name` | honor it verbatim | explicit intent wins; guard still refuses non-test bulk-wipes (fail-closed) |
| No `graph_name`, path given | `test_<stem>_<hash8(path)>` | **guard-passing** (`test_` prefix); unique per fresh tmp_path (mirrors embedded per-path isolation); readable stem + collision-safe hash. Example: `FalkorProjection("/tmp/x/g.db")` → `test_g_a1b2c3d4` |
| No `graph_name`, no path (no-arg, if D-1 option b) | URI's graph or `"tortoise"` | prod-graph semantics; guard refuses bulk-wipe (correct — a no-arg prod construction is not a test graph) |

The derivation is **deterministic** (same path → same graph within a run), **unique** across fresh tmp_paths, and **grep-able** (`test_<stem>_` prefix in every constructed projection). It reuses the SDK's namespace mapping (`test_<x>` → `test_<x>_tortoise`, sdk.py L1115–1123) for namespace-aware SDK constructions — the two mechanisms compose.

### 4.2 Guard interaction

- Derived names always pass `_assert_test_graph` → bulk-wipes run on docker for migrated tests.
- An explicit non-test `graph_name` in a migrated test is a **loud, greppable failure** (guard raises on bulk-wipe) — the sweep in P1 already renames these; any straggler fails visibly, never collides silently.
- `wipe()` (embedded all-graphs) keeps its server refusal; migrated code uses `wipe_server()` (§5). The guard and the wipe filter are the two independent hermeticity walls.

---

## 5. Hermeticity mechanics (phase-2 default)

**Constraint recap (verified):** the guard rejects bare `test`/`tortoise`; `wipe()` refuses server mode; CI jobs each own a private service container (cross-job collision impossible); pytest is single-process (one session per job); the SDK already maps `test_<ns>` → `test_<ns>_tortoise`.

**Two tiers, chosen per file's assertion style:**

| Tier | Graph naming | Isolation unit | Files |
|---|---|---|---|
| **Shared-graph** | `test_suite_<job-uuid>` (seam fixtures: `shared_proj`/`shared_embedded_db` in docker mode) | per-test `wipe_server()` — filtered DETACH DELETE of `test_*`/`tortoise_test_*` graphs | 20 seam users: test_projection, test_1162_add_operator_local_svbp, test_github_connector, test_projection_version_gate, test_analyze, test_backup_sweep, test_ep_calibration, test_subscriptions, test_claim_lifecycle, test_review_connections, test_event_store, test_a9_direct_edge_traversal, test_recall_gaps_subgraph, test_recall_state, test_session_semantic_search, test_ranking, test_search_sessions_temporal, test_ep_selector, test_sdk_legacy_coverage + sdk_factory family |
| **Per-test graph** | `test_<file>_<uuid>` namespaces (SDK auto-maps to `test_<file>_<uuid>_tortoise`) | per-test unique graph — no wipe needed | exact-set/count assertions: EP family (test_ep_directional, test_directional_*, test_ep_nary_falsification, test_ep_quadrature, test_ep_calibration), test_embedded_concurrency live-writer tests (fixed `test_live_mw_tortoise` + uuid graphs), recall-gap/ranking files that assert exact node sets |

**Raw-construction files (the ~93):** route through the class-level redirect (§4) — their derived `test_<stem>_<hash8>` names give per-tmp_path isolation, matching today's per-path embedded semantics. Files that already use the seam inherit tier 1.

**The 3 busy-error tests** (test_audit (d), test_pack_state TestBackfillScript, test_index_directory E2E-9): embedded-only marker (decision D-2) — they assert the embedded single-writer failure class and must NOT run on docker.

**`wipe_server()` contract:** enumerate `list_graphs()` → keep only `test_`/`tortoise_test_`-prefixed → DETACH DELETE those → skip everything else (fail-closed). Non-loopback hosts refused (decision D-4, protecting a remote dev server). The embedded `wipe()` is untouched.

**No reaper reliance:** server graphs are not orphans; the reaper's scope stays local-dev embedded hygiene (epic indicator #4).

---

## 6. Skip-guard inversion design

**Constraint:** on the inverted default, missing docker must fail closed or skip visibly — never green-skip (the #942 vacuity class at 6,837-test scale).

1. **Invert the guard's default polarity.** Today: embedded is default; docker-skip detection covers only the ~72 live tests. After P2/P3: the fast matrix sets `TORTOISE_DB_URI` job-wide; docker is the *expected* state. `tools/skip-guard.py` extends from "any FalkorDB-reasoned skip trips red" to **"any skip in the migrated set trips red"** — same regex class applied to the whole log.
2. **Coverage-manifest check (the vacuity killer).** Per matrix half, a manifest of expected nodeids (generated from `tools/ci_selection.py` half lists × `--collect-only`). The guard cross-checks the log: any expected nodeid missing from both `PASSED` and `SKIPPED(reasoned)` → **red**. This catches the silent early-return that produces *neither* line (the `skip_if_no_falkor` vacuous pattern).
3. **Retire `skip_if_no_falkor` from migrated files.** The 10 files using it (test_audit, test_battery_setup, test_domain_validators, test_event_provenance, test_ingest, test_list_contexts, test_projection, test_projection_version_gate, test_supplementary + `_live_utils`) switch to visible `pytest.skip(reason=...)` or fail-fast. `_live_utils._skip_unless_live_uri` (the *intentional* URI-gate, reason contains "FalkorDB") stays — the guard's `_live_utils.py` exclusion (skip-guard.py `find_violations`) keeps that class green where it's meant to skip (non-falkor surfaces).
4. **Carve-out exemption.** Carve-out files legitimately skip on machines without redislite — their skip reasons say "redislite"/embedded, not "FalkorDB"; the guard's substring matcher must not catch them (research §6.5). Explicit carve-out nodeid sets are excluded from the manifest.
5. **Keep `test_workflow_keeps_rs` semantics** — the `-r fEs` report contract is what makes the guard reliable; extend `tests/test_skip_guard.py` with the new coverage-manifest cases (missing nodeid, vacuous early-return, carve-out exemption, live-utils exclusion).

**Per-phase wiring:** P1 — no skip-guard change (embedded default). P2 — manifest + fail-closed for half b only. P3 — both halves + non-fast surfaces; carve-out exemption wired. P4 — manifest shrinks with the allowlist (the 13 newly-migrated files join the docker set).

---

## 7. CI changes per phase

| Phase | Services | Matrix | Env | skip-guard | Orphan assert |
|---|---|---|---|---|---|
| **P1** | unchanged (falkordb 6379 + falkordb-legacy 16379 already provisioned, #1436) | unchanged | no job-level URI | unchanged | unchanged |
| **P2** | unchanged | half b include gains `TORTOISE_DB_URI: docker://:falkordb@localhost:6379` (half a unchanged) | job-level URI on half b only | manifest for half b + fail-closed | half b expects ~0 redislite orphans; half a unchanged |
| **P3** | unchanged | both halves set job-level URI; non-fast surfaces (test-slow, track_b, e2e/, fixtures) follow | job-level URI on all fast halves | manifest for both halves + non-fast; carve-out exemption | docker halves expect ~0; carve-out jobs keep <20 |
| **P4** | unchanged | optional matrix merge per D-3 (single fast job if both halves < ~40m) | URI is the default | manifest shrinks with allowlist | docker default ~0; dev-machine orphan check (<20 without reaper) re-measured |

Watchdog: embedded half b already rides the 45/55m watchdog — the docker flip is expected to buy the margin; the merge decision (D-3) uses the measured P2/P3 walls.

---

## 8. Open decisions for the human gate

> The human approves or redirects scope. Four decisions below are explicitly requested; each has a recommended default so the gate can proceed with "approve with recommendations" if desired.

### D-1 — Class-level redirect scope
**Options:**
- **(a) `path=` only (recommended)** — redirect fires only for `FalkorProjection(path=...)` when `TORTOISE_DB_URI` (supported scheme) is set. Narrowest prod surface; matches the research brief's wording ("`FalkorProjection(db_path=...)` must redirect"); covers all ~93 test raw constructions (they pass paths).
- (b) `path=` + no-arg — "URI wins" fully (no-arg would also redirect); consistent with sdk.py behavior but changes no-arg prod semantics when URI is set (today no-arg = embedded canonical path).
- (c) env-gated flag (`TORTOISE_TEST_REDIRECT=1`) — most explicit, zero prod surface, but adds a knob and a second condition to keep in sync.

**Recommendation: (a).** Proceed with (a) unless the human wants full URI-wins semantics.

### D-2 — The 3 busy-error tests: per-test carve-out vs whole-file
- **(a) Per-test carve-out (recommended)** — mark `test_audit` (d), `test_pack_state` TestBackfillScript dry-run, `test_index_directory` E2E-9 embedded-only; migrate the remaining 133 tests in those files. Keeps ≈95% migration headroom (342 carve-out stays).
- (b) Whole-file — add the 3 files to the carve-out (~135 more tests stay embedded → ≈92% migrates). Simpler, fewer markers, less headroom.

**Recommendation: (a).**

### D-3 — Matrix split timing
- **(a) Keep the 2-half split through P3; decide merge at P4 on measured walls (recommended)** — preserves the side-by-side divergence confidence of P2 and the epic's "split only if a half exceeds ~40m" rule; the merge becomes a measured, post-migration optimization.
- (b) Commit to merging to a single fast job at P3 if both halves < ~40m — fewer CI minutes but loses the second runner's parallelism and flips two things (default + matrix shape) at once.

**Recommendation: (a).** The P2 half-b wall (expected 57–58m → ≤ ~40m) is the decisive data point.

### D-4 — `wipe_server()` host protection
- **(a) Refuse non-loopback hosts (recommended)** — mirror the CLI's `docker://` warning; a test cannot wipe a remote dev/shared server even if it's `test_`-prefixed. Slight CI-local-only restriction (CI services are loopback).
- (b) Prefix filter only — simpler; accepts any host.

**Recommendation: (a).**

---

## 9. What the plan must carry forward (non-gate hooks)

- **The divergence table (research §2, D1–D16)** is the approved change list; E2E-8 makes it executable.
- **Test-design surface map** (Test-Design Gate) maps the 5 verification surfaces (SDK graph ops, concurrency, hermeticity, lifecycle carve-out, CI matrix) to the E2E set above.
- **Sub-decisions deferred to plan** (research §9, not human gates): `test_flip_gate` partial migration; `test_pre_migration_safety` partial (has a `docker://` branch already); `e2e/hosted/test_12_selfhost_migration` drift-fix vs carve-out; `TORTOISE_FAST_ATEXIT` env gating (cosmetic); numeric-calibration candidate pre-list (EP family, cross-lens, recall-gap family).
- **P1 gate diff-verification:** the "zero behavior change" claim is verified by a diff review of embedded-mode assertions, not by trust.

---

## Appendix — baseline & sources (verified)

| Metric | Value | Source |
|---|---|---|
| Collected tests | 6,837 (40.60s collect) | research-brief §1/§5 |
| Test files | 323 | research-brief §1 |
| Fast-matrix halves | a=2,606 (151 files) / b=2,654 (150 files) | research-brief §5.2 |
| Carve-out | 342 tests (17 files + 3 per-test = 5.0%) | research-brief §4 |
| Migration share | ≈95% (target ≥90%) | research-brief §1 |
| Raw-construction files | 163 (93 no-graph-name sites counted in-repo) | research-brief §5.2 + this pass |
| Orphan baseline (post-#1645) | 4 (< 20 precondition ✓) | research-brief §5.1 |
| CI walls | a=41–42m, b=57–58m (b rides watchdog) | research-brief §5.3 |
| Guard semantics | `"test"`/`"tortoise"` FAIL `_assert_test_graph` | verified this pass (projection/__init__.py L980–1003) |
| Redirect seam point | `FalkorProjection.__init__` path branch (L303+) | verified this pass |
| SDK namespace map | `test_<ns>` → `test_<ns>_tortoise` (sdk.py L1115–1123) | verified this pass |
| Skip-guard | fail-closed on `FalkorDB`-reasoned SKIPPED (tools/skip-guard.py) | verified this pass |
