# Epic: Migrate test suite from FalkorDBLite (embedded) to real FalkorDB (docker)

**Issue:** [tortoise#1647](https://github.com/daniel-ospina/tortoise/issues/1647)
**Status:** RESEARCH (Stage 2/6)
**Team:** epistemic-team
**Started:** 2026-08-24

## Pipeline status

| Stage | Status | Gate | Artifact |
|-------|--------|------|----------|
| 1. Align | ✅ DONE | verifier (REDIRECT applied) | Strategy Alignment Decision |
| 2. Research | 🔄 IN PROGRESS | verifier | research-brief.md |
| 3. Scope | ⏳ | human | scope-brief.md + E2E |
| 4. Plan | ⏳ | human | plan.md |
| 5. Decompose | ⏳ | verifier | child issues + wiring |
| 6. Verify | ⏳ | verifier | verification-proof.md |

## Stage artifacts

- `research-brief.md` — divergence surface + hermeticity + carve-out + baseline (pending)
- `scope-brief.md` — scope + high-level E2E (pending)
- `plan.md` — 8-substep implementation plan (pending)
- `test-design.md` — integration-surface map (Test-Design Gate, pending)

## Key decisions (from Align)

1. **Direction validated** — migrate to docker FalkorDB as default; embedded only for the behavioral carve-out.
2. **Scale corrected** — 5,969 collected tests (not 2,500); 80-160 files (not 60).
3. **Hermeticity is P0** — `wipe()` refuses server mode; graph-isolation strategy must be designed before the flip.
4. **Divergence surface is a research deliverable** — the 7+ `_is_embedded` branches enumerated with per-branch test impact.
5. **Phased strangler rollout** — seam → one-half flip → both halves → allowlist/reaper shrink.
6. **#1645 fixed the reaper, not the leak sources** — orphan baseline is a migration precondition.
