# Tortoise Experiments

## Experiment Registry

| ID | Question | Date | Status | Key Finding |
|----|----------|------|--------|-------------|
| E013 | Does operator extraction work? | 2026-07-08 | ✅ Done | Extraction noise is high — graph filtering needed |
| E014 | Known operators: does the graph mechanism work? | 2026-07-09 | ✅ Done | Both arms 100% — task too easy |
| E016 | Known operators vs raw memory: harder task | 2026-07-10 | ⚠️ Inconclusive | Clean propositions need no organizing — see [findings](./E016-findings.md) |
| E017 | Sequential doc processing + graph construction | 2026-07-10 | 🔧 Designing | Pipeline stages 1-5 done. Reference graph designed but not built |
| E018 | EP source credibility — Beta priors + log-scale aggregation | 2026-07-29 | ⏸️ Pre-registered | [Pre-registration](./E018-preregistration.md). [Design](./E018-design.md). [Scope](./E018-scope.md). Full design at [docs](../../docs/ep-source-credibility-experiment.md). 40 tests |

## Design Pattern

```
docs/teams/epistemic-team/engineering/experiments/
  index.md                 ← this file
  E016-findings.md         ← E016 analysis
  E017-align.md            ← Stage 1: strategy alignment
  E017-research.md         ← Stage 2: research synthesis
  E017-preregistration.md  ← Stage 3: pre-registration ⛔
  E017-design.md           ← Stage 4: experiment design
  E017-scope.md            ← Stage 5: boundaries + E2E tests
  E018-preregistration.md  ← Stage 3: pre-registration ⛔
  E018-design.md           ← Stage 4: experiment design
  E018-scope.md            ← Stage 5: boundaries + E2E tests

  ../../docs/ep-source-credibility-experiment.md  ← E018 detailed design doc

tortoise/experiments/E0XX/  ← harness code (not docs)
```
