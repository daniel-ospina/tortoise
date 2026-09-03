> **⛔ ARCHIVED — M8 (epic #1976, W2 #1998):** this onboarding prompt is SUPERSEDED by
> `tortoise/onboarding/SKILL.md` (the tortoise-onboarding skill) — the ONE live onboarding
> script. Kept for history + the A0 rollback path; never re-promote while the skill is live.
> Deployed copies (website/onboarding-prompt.md, onboarding/<harness>.md) are no longer staged.

# Tortoise Onboarding — Claude Code setup

> Harness variant of the canonical Tortoise onboarding prompt (epic #529).
> The question flow below is the canonical `AGENT_ONBOARDING.md` body —
> single source of truth; never fork it.

## How to use

1. After adding the Tortoise MCP server (the `claude mcp add` command from the
   welcome page), paste this ENTIRE document into your Claude Code chat.
2. Answer the yes/no questions one at a time — the agent runs the setup and
   shows your memory digest at the end (under 5 minutes).

If the agent doesn't start the flow, paste: "Start Tortoise onboarding"

## Persistent alternative (no re-paste per session)

Prefer standing instructions over a one-time chat paste:

- Add this document to your project `CLAUDE.md` (Claude Code auto-loads
  `CLAUDE.md`, NOT `AGENTS.md` — if your repo uses `AGENTS.md`, bridge it with
  an `@AGENTS.md` import line per the Claude Code memory docs).
- MCP config file alternative: instead of the CLI one-liner, add a
  project-scope `.mcp.json` entry — `"type": "http"` (a `url` without `type`
  is a config error and the server is skipped), with `${VAR}` env expansion in
  `url`/`headers` so no literal key is committed:
  `{"mcpServers": {"tortoise": {"type": "http", "url": "https://api.premiselabs.co/mcp/", "headers": {"Authorization": "Bearer ${TORTOISE_API_KEY}"}}}}`.
  Note: project-scope servers require a one-time approval on first use.
