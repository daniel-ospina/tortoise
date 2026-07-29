# E019 Scope

**Date:** 2026-07-29
**Pipeline:** experiment-workflow → Stage 5 — SCOPE

## Boundaries

### In Scope
- Measure bidirectional vs directed EP on the shared-conclusion graph
- 6 test cases covering 1 and 3 shared conclusions, T4/T2/T0 B sources
- Modify EP engine to support directed-only mode (isolated code change)
- Compare C2 and B confidence across modes
- Validate all 4 hypotheses

### Out of Scope
- Full EP parameter tuning (damping, quadrature)
- Multi-hop chain propagation analysis
- Performance comparison between modes
- Production EP mode switching (this is experimental only)
- NAND edge behavior (already tested in E018)

## E2E Tests
1. **Directed mode correctness:** EP converges in directed mode for all topologies
2. **Bidirectional baseline:** Current EP behavior is reproduced
3. **Cascade measurement:** C2 drop is measurable in bidirectional, absent in directed
4. **Anchoring effect:** B's source strength reduces cascade magnitude

## Complexity
| Domain | Rating |
|--------|--------|
| Architecture | standard — EP engine modification |
| Ontology | low — no schema changes |
| Research | standard — mathematical validation |

## Dependencies
- E018 (EP source credibility) — uses same EP engine, test infrastructure
- `tortoise/ep.py` — requires one-line modification for directed mode
