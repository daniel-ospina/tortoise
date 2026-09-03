> **⛔ ARCHIVED — M8 (epic #1976, W2 #1998):** this onboarding prompt is SUPERSEDED by
> `tortoise/onboarding/SKILL.md` (the tortoise-onboarding skill) — the ONE live onboarding
> script. Kept for history + the A0 rollback path; never re-promote while the skill is live.
> Deployed copies (website/onboarding-prompt.md, onboarding/<harness>.md) are no longer staged.

# Tortoise Onboarding — Pi setup

> Harness variant of the canonical Tortoise onboarding prompt (epic #529).
> The question flow below is the canonical `AGENT_ONBOARDING.md` body —
> single source of truth; never fork it.

## How to use

1. Append this ENTIRE document to your project `AGENTS.md` (or to
   `~/.pi/agent/AGENTS.md` for all projects).
2. Start your next pi session — AGENTS.md is auto-loaded and the paired
   `.mcp.json` config is resolved, so onboarding starts automatically with no
   paste into chat. Then answer the yes/no questions one at a time.

Notes: the paired Block A config is a `.mcp.json` entry using
`${TORTOISE_API_KEY}` env expansion (export the variable first — no literal
key on disk). If a `.mcp.json` already exists, MERGE the `tortoise` entry into
its `mcpServers` object — do not append a second file. MCP support in pi is
provided by the agent-infra `mcp-client` extension; if `mcp__tortoise__*`
tools don't appear, install/bootstrap it from
https://github.com/daniel-ospina/agent-infra (extensions/mcp-client) and
restart pi.
