# Tortoise memory (add this block to your project's CLAUDE.md)

> Tortoise gives this project persistent, cross-session memory. A memory digest
> is injected at session start (SessionStart hook). Use it, and contribute back.

## At session start
- If the injected digest contains relevant prior decisions/points, start from
  them instead of re-deriving.
- Call `mcp__tortoise__tortoise_suggest_entry_points(query)` before answering
  a question that may have been decided before.

## At session end
- `session-end.sh` (SessionEnd hook) automatically files this session as a
  memory Event (requires `TORTOISE_API_KEY`/`TORTOISE_API_URL`). If it isn't
  installed, file durable decisions manually via
  `mcp__tortoise__tortoise_create_point(kind="decision", ...)`.

## While working
- When you make a durable decision (architecture choice, API contract, naming,
  "why X over Y"), file it: `mcp__tortoise__tortoise_create_point(kind="decision", content=...)`.
- When you resolve a contradiction or update a belief, create an operator
  (`mcp__tortoise__tortoise_create_operator`) so confidence propagates.

## Rules
- Memory is a floor, not a ceiling — always answer from the current context too.
- Keep entries short and self-contained (they'll be injected in future sessions).
- Do NOT file transient state (task progress, TODOs). File durable knowledge.
