# Synthetic Docs v1 — Nexus Analytics Scenario

**Status:** Converged (2 cycles, 4 reviewers, zero issues)

## Overview

10 internal documents (~7K words, ~45K chars) from the fictional B2B SaaS company "Nexus Analytics," covering a strategic decision point: pivot to enterprise or persevere with mid-market PLG.

## Reference Graph

63 claims (31 core + 32 detail) across 9 domains: Product, Market, Financial, Team, Investors, Narrative, Methodology, Projections, Causal.

12 logical operators (NAND/AND/OR primitives). Reference conclusion: **PIVOT (80% confidence)**.

Stored in: `../E017/convergence.py` (ENRICHED_CLAIMS dict).

## Documents

| Doc | Author | Role | Words |
|-----|--------|------|-------|
| D1 | Emily Nakamura | Head of Product | 800 |
| D2 | James Chen | VP Sales | 896 |
| D3 | David Okonkwo | CFO | 554 |
| D4 | James Chen | VP Sales | 546 |
| D5 | Sarah Kim | CTO | 594 |
| D6 | Amara Osei | CEO (private notes) | 749 |
| D7 | Priya Sharma | Head of CS | 653 |
| D8 | External consultant | Enterprise readiness | 325 |
| D9 | David Okonkwo | CFO | 725 |
| D10 | Amara Osei | CEO (strategic) | 1,235 |

## Fidelity

Verified via 4-reviewer convergence loop (deepseek-pro × 3 + claude-sonnet):
- 630 claim-document pairs all present
- Zero novel facts
- Zero contradictions

## Use

Pass these documents to agents and ask: "Should Nexus Analytics pivot to enterprise or persevere with mid-market?" Compare their answers, confidence, and token usage against the reference.

## Transformation Notes

- Documents were generated FROM the enriched graph, then verified with binary fidelity checklist
- Methodology: MDBench (seed-knowledge-first, binary checklist, core/detail split)
- Convergence loop: multi-model review cycles until zero issues across all models
- Key finding: claude-sonnet catches contradictions/framing; deepseek-pro catches numerical gaps

## See Also

- Enriched graph: `../E017/convergence.py`
- Fidelity checklist: `../E017/fidelity_checklist.json`
- Convergence log: `../E017/convergence_log.jsonl`
- Experiment design: `../E017-preregistration.md`
