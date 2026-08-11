# HANDOVER — merge the Tortoise tooling-consolidation PRs

You are picking up the final step of the Tortoise MCP/SDK tooling consolidation (epic #888).
The previous session built all the work but predates the task-heartbeat fix (#176/#177), so its
sub-agents kept getting killed at the silence threshold. This session has the fix, so your
sub-agents will not be killed while working.

## Repo
`/Users/danielospina/Documents/GitHub/tortoise` (GitHub `daniel-ospina/tortoise`). gh CLI is
authenticated. If GraphQL is rate-limited, use REST (`gh api -X ...`). GH_TOKEN can be read via
`gh auth token`.

## Context: what was built
The tooling surface was consolidated from 69 MCP tools to ~19 primitives (design doc PR #912,
ontology v3.5 reification rule PR #910). All six implementation waves were built and PR'd. One
already merged (W6 review_connections = #933). The remaining six are OPEN and need merging.

## The six open PRs to merge (all implement the 19-tool surface)
| PR | Wave | Branch | Base |
|---|---|---|---|
| #907 | W1 recall Wave A (state mode) | feat/898-recall-state | main |
| #918 | W1 recall Wave B (gaps+subgraph) | feat/898-recall-modes | feat/898-recall-state |
| #920 | W5 EP operator-less edges | feat/888-ep-operatorless | main |
| #922 | W2 write/revise consolidation | feat/888-write-consolidation | main |
| #927 | W3 orient/get consolidation | feat/888-orient-consolidation | main |
| #932 | W4 ingest bulk | feat/888-ingest-bulk | main |

All six touch `tortoise/sdk.py`, `tortoise/mcp_server.py`, `tortoise/tool_registry.py`, so they
must be merged SEQUENTIALLY with rebase + conflict resolution. Main keeps advancing with other
agents' work, so re-check `git ls-remote origin main` before starting.

## Merge order (do exactly this)
1. **#907** (recall Wave A) — merge first; it's the base for #918.
2. **#918** — after #907 merges, rebase feat/898-recall-modes onto main (or re-point base to
   main via `gh api -X PATCH repos/daniel-ospina/tortoise/pulls/918 -f base=main`), then merge.
3. **#920** (EP operator-less) — rebase onto main, merge.
4. **#922** (write/revise) — rebase onto main, merge.
5. **#927** (orient/get) — rebase onto main, merge.
6. **#932** (ingest bulk) — rebase onto main, merge.

## Method per merge
- Work in a scratch worktree: `git -C <repo> worktree add <path> <branch>` (if the worktree guard
  blocks, prefix `env AGENT_ALLOW_MAIN_EDITS=1`). Note: many worktrees already exist (wt-recall-a,
  wt-recall-b, wt-w2, wt-w3, wt-w4, wt-w5, etc.) — reuse or create fresh.
- Fetch origin main, rebase the PR branch onto origin/main, resolve conflicts.
- **Conflict rule: these are ADDITIVE tool additions. KEEP ALL tools from both sides** — never drop
  a tool. Conflicts are mostly in tool-registration lists / dispatch tables.
- After resolving conflicts, run the PR's test file(s) + `tests/test_mcp_server.py` + relevant
  `tests/test_sdk*.py` to confirm nothing broke. Use `-p no:randomly --timeout=120` to avoid
  order-flakiness. Do NOT run the full suite.
- Push the rebased branch, then merge via REST:
  `gh api -X PUT repos/daniel-ospina/tortoise/pulls/<N>/merge -f merge_method=squash`.
- If a PR conflicts hopelessly, stop it, note why, continue to the next.

## Known caveats
- One test in W3 compared `health.uptime` at two call times (time-varying) — already fixed to
  exclude time-varying fields. If you see an uptime-related failure, that's the flake, not product.
- W2 has one order-sensitive test (test_about_edge_via_create_edge) under pytest-randomly — use
  `-p no:randomly`.
- The EP operator-less change (#920) is additive; operator-mediated EP must stay unchanged.

## Done when
All six PRs merged to main, and a final `git ls-remote origin main` shows the last merge. Then
report: each PR merged (sha) + tests run + conflicts resolved.
