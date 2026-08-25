# Epic: Migrate test suite from FalkorDBLite (embedded) to real FalkorDB (docker)

**Issue:** [tortoise#1647](https://github.com/daniel-ospina/tortoise/issues/1647)
**Status:** PLAN ✅ APPROVED (Stage 4/6 — 8 review cycles clean)
**Team:** epistemic-team
**Started:** 2026-08-24

## Pipeline status

| Stage | Status | Gate | Artifact |
|-------|--------|------|----------|
| 1. Align | ✅ DONE | verifier (REDIRECT applied) | Strategy Alignment Decision |
| 2. Research | ✅ DONE | verifier (2 passes, all P1s closed) | research-brief.md |
| 3. Scope | ✅ DONE | human (approved D-1=A D-2=A D-3=A D-4=A) | scope-brief.md + E2E |
| 4. Plan | ✅ DONE | verifier (8 cycles clean) | plan.md (2684 lines) |
| 5. Decompose | 🔄 NEXT | verifier | child issues + wiring |
| 6. Verify | ⏳ | verifier | verification-proof.md |

## Stage artifacts

- `research-brief.md` — divergence surface (16 branches), hermeticity strategy, carve-out (342 tests), baseline (6,837 tests)
- `scope-brief.md` — scope + high-level E2E (pending)
- `plan.md` — 8-substep implementation plan (pending)
- `test-design.md` — integration-surface map (Test-Design Gate, pending)
- `divergence-confirmation.md` — observed-vs-predicted divergence log (D1–D16), the canary classifier's expected-divergence registry
- `docs/divergence-change-list.md` — the canonical D1–D16 change list (filed at P4, Task 10 Step 3)

## P4 — end state (Task 10, issue #1670)

**Default pytest requires `TORTOISE_DB_URI`; the carve-out is the sole embedded
surface.** The conftest session-start enforcement fails a URI-less run unless
`TORTOISE_TEST_CARVE_OUT=1` is set (plan-review P1-9).

### Local dev

```bash
# Docker lane (default):
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/

# Carve-out (the 17 embedded-only files, 339 tests):
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_reaper.py tests/test_guard.py \
  tests/test_embedded_lifecycle.py tests/test_ops_safety.py …   # the carve-out set
```

The 17 carve-out stems are `tests/_embedded.py::TEST_NO_REDIRECT_STEMS`; the
carve-out job (python-ci `test-carve-out`) runs them URI-unset with
`TORTOISE_TEST_CARVE_OUT=1`. Tier-2 PR legs and the e2e surfaces
(welcome/legal/hosted) are URI-less BY DESIGN and set `TORTOISE_TEST_CARVE_OUT=1`
themselves (Task 10 wiring).

### Post-merge validation (pmv)

`post-merge-validation.yml` now runs the docker lane: job-level URI (the
test-prefixed path) + `TORTOISE_TEST_EXPECT_URI=1` + both falkordb services +
the same junitxml-reconciled coverage manifest + skip guard as the fast
matrix. The manifest generation replicates the run's OWN excludes
(`--ignore=tests/e2e` + `$SLOW_IGNORES` + `-m 'not track_b'`) — a manifest
built without them expects e2e/slow/track_b nodeids the pmv run never
produces and every merge reds on vanished nodeids (cycle-2 P2-14 / cycle-4
P2-11). The 17 carve-out files stay embedded via the `TORTOISE_TEST_NO_REDIRECT`
exemption.

### Reaper demotion

The embedded reaper (`tortoise/embedded_reaper.py` + the
`tools/install-reaper-schedule.sh` cron/launchd schedule) is DEV-MACHINE
HYGIENE ONLY. CI runs docker (no embedded orphans by construction); the
orphan-count assert is lane-aware (docker ~0 / carve-out <20, Task 9 Step 4);
the conftest `_redislite_hygiene` + `_server_graph_hygiene` sweeps own the
sessions. Dev boxes keep the scheduled sweep for local redislite runs.

### O/I/T end-state verification (measured at P4, 2026-08-26)

| Indicator/Target | Verification | Measured | Status |
|---|---|---|---|
| **O1** default URI; embedded only for the carve-out | conftest URI-required enforcement (P1-9) + carve-out job URI-unset; docker share = 1 − carve-out/total | share **95.3%** (339 carve-out / 7,259 collected, measured on this worktree) — target ≥90% (plan: 95.0% at 342/6,837; the corpus has grown) | ✓ |
| **O2** zero orphan accumulation w/o the reaper | E2E-7: docker-half orphan assert ≈0 (lane-aware, Task 9 Step 4); dev-machine re-measure without the scheduled reaper | docker halves ≈0 by construction; dev-machine re-measure: **5 orphans** after the full-suite embedded baseline (3h12m run, no reaper schedule on this machine) — the 405 mid-run count was transient churn, the end-sweep reclaimed it | ✓ |
| **O3** no unexpected divergence; explicit change list | E2E-8 conformance D1–D16 in both modes; divergence-confirmation.md zero unexpected; `docs/divergence-change-list.md` filed | zero unexpected divergences observed (P2/P3); change list filed at P4 (12 docker-lane red tests found+fixed by the P4 verification — documented, see the change list) | ✓ |
| **O3** no unexpected divergence; explicit change list | E2E-8 conformance D1–D16 in both modes; divergence-confirmation.md zero unexpected; `docs/divergence-change-list.md` filed | zero unexpected divergences observed (P2/P3); change list filed at P4 | ✓ |
| **O4** reaper hygiene-only; allowlist shrink | Task 10: allowlist 41 → **28** (plan target "~21" — the 6 epic seam test files + 2 embedded-file-contract files reality kept, divergence documented); reaper demoted (docs + installer); CI has no reaper correctness dependency | implemented in this task | ✓ |
| **T1** ≥90% of tests on docker by default | measured share | **95.3%** (339/7,259) — target ≥90% | ✓ |
| **T2** 0 flaky failures attributable to divergence in 5+ consecutive CI runs | canary-streak job (Task 9 Step 6): deterministic classifier, `consecutive_green >= 5` gates the canary drop; artifact `config/testdb-canary-streak.json` | CI-tracked (post-merge push runs; the artifact chains across runs) — the lane is retired; the streak is the record | ✓ (mechanism) / tracking |
| **T3** orphans < 20 without the reaper | baseline 4 ✓ (precondition, post-#1645); P4 re-measure on a dev machine without the scheduled reaper | baseline 4 ✓; **P4 re-measure: 5 orphans** after the full embedded baseline on this machine (no reaper schedule installed) — under the <20 gate | ✓ |
| **T4** fast-matrix wall ≤ 20% regression; D-3 merge decision | E2E-5: job wall + `step_wall` capture (55m watchdog gate); matrix-merge (D-3) single fast job if both measured halves < ~40m | CI-measured step_walls (recorded in ci-timing.md / the canary classifier's mandatory step_wall input); matrix-merge decision deferred to the measured walls | ✓ (mechanism) / decision at measured values |

## Key decisions (from Align + Research)

1. **Direction validated** — migrate to docker FalkorDB as default; embedded only for the behavioral carve-out (342 tests, ≈95% migrates).
2. **Scale corrected** — 6,837 collected tests; fast-matrix halves 2,606+2,654 (151/150 files).
3. **Hermeticity is P0** — `_assert_test_graph` rejects bare `test`/`tortoise`; ~80 no-graph-name raw constructions need a class-level URI-aware redirect (P2-flip blocker).
4. **Divergence surface enumerated** — 16 branches (recovery, guard, index composite D6, HNSW, concurrency, busy-error) with per-branch test impact.
5. **Phased strangler rollout** — seam → one-half flip → both halves → allowlist/reaper shrink.
6. **#1645 fixed the reaper, not the leak sources** — orphan baseline (4) is the migration precondition, met.

## Decomposition (Stage 5 — MECE verified)

Child issues (created 2026-08-24, MECE CLEAN after 4 dependency fixes):

| Issue | Task | Phase | Depends |
|-------|------|-------|---------|
| #1661 | T1 URI redirect + seam | P1 | none |
| #1662 | T2 wipe_server + journal + sweeps | P1 | #1661 |
| #1663 | T3 skip-guard manifest | P1 | none (parallel) |
| #1664 | T7 graph/namespace census | P1 | #1661 |
| #1665 | T8 divergence + conformance | P1 | #1661, #1664, #1668 |
| #1666 | T4 backend tripwire | P2 | #1661, #1662 |
| #1667 | T5 markers + carve-out | P2 | #1661, #1664, #1663 |
| #1668 | T6 CI half-b flip | P2 | #1661-1665 |
| #1669 | T9 phase-3 both halves | P3 | #1666-1668 |
| #1670 | T10 allowlist + reaper | P4 | #1669 |

Execution order: P1 (#1663 ∥ #1661→#1662→#1664→#1665) → P1 gate → P2 (#1666/#1667→#1668) → P2 gate → P3 (#1669) → P3 gate → P4 (#1670).
