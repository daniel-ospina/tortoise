# Scoping — #1005 redislite server leak recurs after #176 stopgap

**Tier:** standard | **Date:** 2026-08-12 | **Status:** scoping → implementation

## Phase 1 — problem-diverge (alternatives explored)

| Framing | Strength | Weakness |
|---|---|---|
| F1 (issue): per-test fixtures construct embedded servers and never close them | matches observed cwd=pytest-tmp evidence; 30 files | symptom-level — doesn't explain why finalize/close paths that exist don't fire |
| F2: lifecycle gap — `FalkorDB` re-export + `TortoiseSDK` lack weakref.finalize/context-manager (only `FalkorProjection` has it) | root-cause candidate; deterministic close prevents orphans at source | doesn't clean up the 358 already leaked |
| F3: reaper exists (#176) but is not wired anywhere + its classification PROTECTS path-based servers (the dominant leak class) + serial probes make it unusable at scale (timed out >300s on 358 servers) | explains why the count stays high despite the reaper existing | reaper-only fix leaves leak rate unchanged |
| F4: option C — shared TORTOISE_DB_URI (docker://) | eliminates embedded entirely | requires Docker in all dev/CI environments; not universal |

**Adversarial check (reverse framing):** "The leak is not from tests at all" — evidence against: leaked server cwd = pytest tmp dirs (issue body lsof evidence), leak sources correlated with concurrent suite runs. Validated as test-driven; but **concurrent multi-worktree suites** (5+ in our batch) multiply the rate — also true for swarm.
**Pre-mortem:** fix ships, leak returns if (a) classification still protects pytest-tmp path servers after pytest removes their DB dirs (socket dir remains, redis.config dbfilename → protected), (b) reaper too slow to run at session end, (c) new test files add raw `FalkorDB(` constructions with no lifecycle.

## Phase 2 — problem-converge (confirmed)

**Confirmed problem:** Embedded redislite servers are created per-test (30 files; fresh tmp paths) with non-deterministic client lifecycle (FalkorDB/SDK lack finalize), the #176 reaper exists but is (i) never wired into the test session, (ii) classifies path-based servers under ephemeral pytest tmp trees as protected even when their DB dir is gone and they have 0 clients, and (iii) too slow (serial 2s-timeout probes) to sweep hundreds. Result: unbounded orphan accumulation + synchronized bgsave storms.

**Confidence: 85.** Falsification: if wiring the reaper + lifecycle + classification produces >10 orphans after a full suite, definition is wrong.

## Phase 4 — solution-diverge

| Approach | Files | Tradeoffs |
|---|---|---|
| S1: Wire reaper into conftest (session start/end sweeps) + parallel probes + pytest-tmp classification (dir-gone-or-ephemeral + 0 clients → candidate) | tests/conftest.py, tortoise/embedded_reaper.py | Fixes existing orphans + bounded steady-state; classification extension must stay fail-closed |
| S2: Lifecycle root-cause (option D): weakref.finalize + context managers on `FalkorDB` re-export and `TortoiseSDK`; session-finalizer gc.collect + close | tortoise/__init__.py, tortoise/sdk.py, tests/conftest.py | Reduces leak RATE at source; doesn't remove existing orphans |
| S3: Option A audit — convert 30 test files to session-shared DB | 30 test files | Removes per-test servers entirely; large risky refactor; "last-close wins" sharing hazard documented in conftest |
| S4: Reaper-only (S1 without classification change) | conftest + reaper | Cannot reap the dominant source (path-based under pytest tmp) — indicator 2 unmet |

## Phase 5 — solution-converge

**Pick: S1 + S2 together** (reaper wiring+speed+classification extension AND lifecycle hardening), with S3 filed as a follow-up issue (not absorbed — independent refactor; S1+S2 meet all three indicators without it).

Rationale: S1+S2 attack both sides (existing orphans + future leak rate) with bounded risk; S1's classification extension is the key enabler (dominant source is path-based under ephemeral tmp trees — currently protected). S3's 30-file refactor has real semantic risk (shared-path last-close-wins) → separate issue. S4 alone fails indicator 2.

**Rejected:** S4 (can't reap dominant source); S3 now (risk, size — file follow-up).

## Implementation plan (TDD)

1. **embedded_reaper.py — classification extension**: in `_classify_dir`/`_classify`, add ephemeral-tree signal: registry `dir` missing from disk OR under an ephemeral test tree (`pytest-of-*`, `tortoise_test_*`, `tmp*` under tempdir) + 0 clients + uptime ≥ cooldown → `candidate`. Keep all existing protections (named dbfilenames in user dirs, unknown patterns). Unit tests in tests/test_reaper.py.
2. **embedded_reaper.py — speed**: parallel probe pool (ThreadPoolExecutor, ~8 workers) for `_active_client_count`; keep fail-closed semantics; add `--jobs`; sweep stays correct under concurrency (records pre-scanned).
3. **conftest.py — wire sweeps**: autouse session fixture: session-start sweep (reap leftovers from prior runs, bounded batch, non-fatal) + session-finalizer (gc.collect(), sweep with 0-client check) → indicator 1 (~0 after full run).
4. **Lifecycle (root cause)**: `weakref.finalize` + `__enter__/__exit__` on `tortoise.FalkorDB` subclass; `weakref.finalize` on `TortoiseSDK` calling `close()` (guard idempotency); verify `FalkorProjection` finalize already present (it is).
5. **Tests**: reaper classification unit tests (ephemeral-tree candidate, user-path protected, dir-gone candidate), finalize/context-manager tests, conftest sweep smoke test (dry-run path), leak soak: run `tests/test_projection.py tests/test_flip_gate.py tests/test_sdk_ep.py` → count orphans before/after (expect ~0 after session end).
6. **Full suite** run; count orphans post-suite.

## Verification checklist (issue indicators)

| Indicator | How verified |
|---|---|
| ~0 orphaned redislite procs after full suite (today: 435) | soak + full suite, `pgrep -f redislite/bin/redis-server \| wc -l` before/after |
| No per-test servers in pytest tmp dirs | same count + lsof cwd spot-check |
| Load < 2× baseline, no bgsave storms | process-count bounded; observe load during run |

## Wiring check

| Touch point | Covered by |
|---|---|
| tests/conftest.py (all suites) | step 3 |
| CI (python-ci.yml, e2e jobs) | same conftest wiring; no new deps |
| CLI (`tortoise` / reaper manual use) | step 1-2 keep CLI working |
| Other worktrees' suites | reaper protects live-client servers — safe under concurrency |
