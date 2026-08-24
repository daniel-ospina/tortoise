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

## Key decisions (from Align + Research)

1. **Direction validated** — migrate to docker FalkorDB as default; embedded only for the behavioral carve-out (342 tests, ≈95% migrates).
2. **Scale corrected** — 6,837 collected tests; fast-matrix halves 2,606+2,654 (151/150 files).
3. **Hermeticity is P0** — `_assert_test_graph` rejects bare `test`/`tortoise`; ~80 no-graph-name raw constructions need a class-level URI-aware redirect (P2-flip blocker).
4. **Divergence surface enumerated** — 16 branches (recovery, guard, index composite D6, HNSW, concurrency, busy-error) with per-branch test impact.
5. **Phased strangler rollout** — seam → one-half flip → both halves → allowlist/reaper shrink.
6. **#1645 fixed the reaper, not the leak sources** — orphan baseline (4) is the migration precondition, met.
