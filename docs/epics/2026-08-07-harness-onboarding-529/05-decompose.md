---
title: "Epic Decompose — Epic #529: Harness-specific onboarding variants"
type: decisions
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-11
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Epic #529 Decomposition Record

**Date:** 2026-08-11
**Skill:** epic-decompose (MECE-first) + issue-creation (O/I/T, affiliation, domain-aware complexity)
**Input:** approved plan (`04-plan.md`) — Task Breakdown table

## Child Issues Created (gh batch, 5 calls)

| Issue | Task | Title | Complexity | Depends on |
|-------|------|-------|-----------|------------|
| #965 | TD | test-design: #529 harness onboarding variants — integration-surface map | micro | none |
| #966 | A | feat(529): onboarding variant artifacts + drift-proof deploy staging | standard | none |
| #967 | B | feat(529): welcome page harness-optimal blocks + copy attribution | standard | #966 (URL convention) |
| #968 | C | test(529): harness variant E2E verification — Pi automated + Claude/Codex CLI + Cursor structural | standard | #966, #967 |
| #969 | D | capstone: clickthrough verification for #529 harness onboarding variants | standard | #966, #967, #968 |

All issues: Team epistemic-team; O/I/T + verification checklists derived from the #965 surface map; fractal fields populated; dependency placeholders replaced with real numbers at creation.

## Review Gates

- **Per-issue coherence + MECE (combined fresh-context dispatch):** MECE CLEAN — file ownership disjoint (#966: variants/staging/workflow/tests; #967: welcome.html/hosted_api.py/tests; #968/#969: verification docs only; #965: docs-only), collectively exhaustive vs the task table, acyclic chain #966→#967→#968→#969, A∥B parallel-safe (contract-first URL convention).
- **One coherence P2 found & fixed:** analytics harness enum {claude,codex,cursor,pi} vs artifact slugs {claude-code,codex,cursor,pi} had no documented mapping → fixed in plan Substep 4 (welcome.html owns the single mapping) + risks table + T3 assertion. Also recorded as a comment on #967 (contract owner).

## Coordination with Parallel Work

- **PR #970 (branch feat/529-harness-onboarding, separate agent, same epic):** verifies/fixes per-harness MCP config shapes for the SELF-HOSTED surface (`website/self-hosted.html`, `tortoise/__main__.py` `_harness_stdio_config`, `tests/test_harness_mcp_config.py`). Complementary, zero file overlap with #966–#969 (hosted welcome-page variants). Their verified hosted shapes (claude `type: streamable-http` accepted, cursor url+headers no type) are consistent with this plan's research brief §3.
- **Worktree incident (this session):** the original wt-529 worktree was re-purposed externally mid-session (branch swapped under this session; uncommitted planning docs destroyed once). Work re-established in a dedicated worktree `wt-529-variants` on branch `feat/529-onboarding-variants` @ origin/main (661222d); docs committed immediately to survive.

## Execution Order

1. #966 (A) — no deps.
2. #967 (B) — after A's URL convention lands (or parallel against the frozen contract).
3. #968 (C) — after A+B.
4. #969 (D capstone) — after deploy; gates epic completion.
