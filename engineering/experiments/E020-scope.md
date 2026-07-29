# E020 Scope

**Date:** 2026-07-29
**Pipeline:** experiment-workflow → Stage 5 — SCOPE

## Boundaries

### In Scope
- Measure IMPL edge transmission strength
- Compare current phi_impl against stronger variant
- Test chain lengths 1-3, source counts 1-5
- Mutual IMPL loop (A↔B)
- Determine whether phi_impl needs recalibration

### Out of Scope
- NAND edge behavior (tested in E018)
- Mitigation behavior (tested in E018)
- Source credibility tiers (tested in E018)
- Directed vs bidirectional EP (tested in E019)
- Production deployment of phi changes

## Complexity
| Domain | Rating |
|--------|--------|
| Architecture | standard — EP engine modification |
| Research | standard — mathematical validation |
