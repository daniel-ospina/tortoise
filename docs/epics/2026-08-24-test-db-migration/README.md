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
