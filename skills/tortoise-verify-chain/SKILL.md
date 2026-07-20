---
name: tortoise-verify-chain
title: "tortoise-verify-chain"
doc_status: live
subjects.team: epistemic-team
created: 2026-07-18
description: Verify chain integrity across all product strategy gates. Runs verify_chain(), surfaces violations, and offers fix options.
type: capability
domain: capability
status: live
allowed-tools: mcp__tortoise__tortoise_check_structure, mcp__tortoise__tortoise_summarize_structure, mcp__tortoise__tortoise_create_point, mcp__tortoise__tortoise_query
---

# tortoise:verify-chain

Verify that the product strategy chain (JTBD → useCase → userJourney → workflow → requirement) has no violations.

## Steps

1. Call `tortoise_summarize_structure()` to get summary counts per gate.
2. Call `tortoise_check_structure()` to get detailed violations.
3. If clean: report "all chain integrity rules pass."
4. If violations found, for each violation:
   - Surface the affected Point ID and the rule that failed.
   - Offer to fix: create missing parent JTBD, add covered_use_cases, link requirement to workflow.
5. Optionally apply fixes via `tortoise_create_point` or other tools.

## Quality Gates

- **G1 (Static):** Verify that the graph context exists and has Points.
- **G2 (Semantic):** If violations are found, classify severity: P0 (orphan useCase without JTBD parent) vs P2 (missing optional metadata).

## Error Handling

- If `tortoise_check_structure` fails, report the error. Do not attempt fixes on a broken query.
