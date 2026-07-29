# E018 Scope

**Date:** 2026-07-29
**Pipeline:** experiment-workflow → Stage 5 — SCOPE

## Boundaries

### In Scope
- Beta prior mapping validation (T0-T4)
- Log-scale aggregation formula correctness
- EP propagation across 3 graph topologies (chain, loopy single, loopy multi)
- Monotonicity: more sources → higher confidence
- Anti-Sybil: 1M T4 < 2 T0
- Non-dilution: weak sources don't pull down strong ones
- NAND contradiction behavior
- Mitigation behavior
- 40 automated tests in `tests/test_ep_sources.py`

### Out of Scope
- Edge annotation dimensions (archived)
- Production FalkorDB testing (uses FalkorDBLite)
- Performance benchmarking
- Real-world source data (uses synthetic test data)
- EP parameter tuning (damping, quadrature points)
- Multi-hop propagation beyond 2 hops
- Source extraction pipeline testing (uses direct SDK calls)

## E2E Tests (high-level)

1. **Full pipeline:** Source creation → extraction → calibration → EP → confidence check. Validates end-to-end flow.
2. **Regression guard:** Run all 40 tests. Any failure blocks merge.
3. **Documentation:** Experiment design doc at `docs/ep-source-credibility-experiment.md`

## Complexity
| Domain | Rating |
|--------|--------|
| Architecture | low — single test file, no new systems |
| Ontology | low — no schema changes |
| Research | standard — mathematical validation |
