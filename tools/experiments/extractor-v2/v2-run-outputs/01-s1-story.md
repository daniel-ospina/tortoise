Based on the conversation, here is the narrative summary:

**State Change:** The P0 CI-red issues (#992, #998) are now resolved and merged via PR #1004.

The root cause was confirmed: the draft-filter introduced in #943 silently excluded operator inputs with `status="draft"`, causing degenerate operators to produce zero-confidence outputs without any diagnostic.

The fix had two parts: (1) migrating 11 EP tests to create `status="live"` points (preserving production semantics), and (2) adding a non-silent warning that names the operator and every input's status when a draft-strip degenerates an operator.

**Epistemic Logic:**
- **IMPL (supports):** The fix approach was chosen over the alternative (changing production draft-filter semantics) because preserving production behavior is a durable constraint — the draft-filter is correct for production, tests were simply using the wrong status.

- **NAND (undermines):** The review revealed two P2 findings that would have blocked the merge gate: (1) the diagnostic's id-truncation (`cid[:8]`) is useless because point IDs share a timestamp prefix for ~2-month windows, making all labels identical; (2) `test_context_free_produces_consistent_ranking` in the migrated file was still using draft points, creating a vacuous pass — the exact failure mode the PR was fixing, left dead in the same file.

- **MITIGATES (tempers):** The environment's extreme I/O contention (load 175-238, multiple wedged full-suite runs from other agents) caused subagent stalls and timeouts, but the fix was verified through targeted test runs that proved all 11 originally-red tests pass post-rebase.

**Durable Beliefs:**
- The draft-filter's silent degeneration is a known failure mode (#780) that must be diagnosed, not silenced.

- Point IDs are ULID-style (`<hex-timestamp>-<uuid12>`), so `cid[:8]` is a timestamp prefix shared by all points in a ~2-month window — insufficient for disambiguation.

- Concurrent test DB writes from multiple agents cause FalkorDBLite file-lock contention, wedging pytest processes in uninterruptible I/O wait.

- The text-first pattern (emit text before any tool calls) beats the subagent stall on this environment.

**Remaining Open Work (post-P0 merge):**
- P1: #981 CLI config drift (blocks copy-paste onboarding)
- Mission: #969 harness clickthrough capstone
- Mission: #979 hosted E2E suite implementation (spec #980 merged)
- P2: Fix the two review findings (id-truncation diagnostic, vacuous test migration) — these are now the only remaining work from the P0 pair.

## Summary of State Changes

**Primary finding: The P0 CI-red blocker was resolved externally.** While I was shipping a fix for the degraded EP tests causing a CI-red state on main, another agent/process on the same machine independently discovered and shipped the same solution: PR #1000, built from the P0 subagent's dead worktree branch (same commit messages, co-author credit).

This merged to main at 11:53Z, restoring main's Python CI to green (`completed/success`).

The CI-red condition that was blocking all PRs is now resolved.

**My PR #1004 carries the residual delta:** two improvements genuinely missing from main:
1.

**Durable state change in ep.py warning format:** labels changed from `cid[:8]` (indistinguishable for multi-subagent runs with same ms-timestamp prefix) to `cid.split('-')[-1]` (the UUID suffix), at the direction of reviewer P2 (conf 85).

This is d
2.

**Durable state change in test_decide.py:** one test (`test_context_free_produces_consistent_ranking`) was vacuous — its assertion block was gated behind a `None` guard that always skipped the guard body.

This was found by a reviewer (P2 conf 95) and fixed.

This test is still broken on main.

**Epistemic state:**
- **Supports:** The fix approach was optimal (11 migrations × `status="live"` + non-silent degenerate-operator exclusion warning) — the fact that the dead subagent's same commits shipped independently via #1000 confirms this was the correct fix.

- **Undermines (artifacts):** The `cid[:8]` label format in #1000 is now on main with the exact flaw flagged in review (P2 conf 85).

This is a known minor regression in main's warning output quality, unaddressed.

- **Mitigates:** The vacuous test fix is genuinely absent from main, so #1004 provides marginal but real test-rigor improvement that won't be caught by other means.

## Environment Issue (Durable Belief)

**New durable belief about the agent infrastructure:** All tool-using subagents stall at the 300s parent-turn bound under load in this environment.

The stall is consistent (pattern: alive, working, but never delivers a first message within the 300s window).

Believed cause: I/O saturation from 5+ concurrent agent sessions running full test suites (load reached 230).

The no-tools inline prompt pattern defeats the stall.

Expect intermittent orphaned test processes from subagents killed mid-finish.

This affects all future subagent-dependent workflows.

**User directed action:** File an issue in `agent-infra` for this subagent stall pattern.