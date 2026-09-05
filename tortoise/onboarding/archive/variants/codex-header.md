> **⛔ ARCHIVED — M8 (epic #1976, W2 #1998):** this onboarding prompt is SUPERSEDED by
> `tortoise/onboarding/SKILL.md` (the tortoise-onboarding skill) — the ONE live onboarding
> script. Kept for history + the A0 rollback path; never re-promote while the skill is live.
> Deployed copies (website/onboarding-prompt.md, onboarding/<harness>.md) are no longer staged.

# Tortoise Onboarding — Codex setup

> Harness variant of the canonical Tortoise onboarding prompt (epic #529).
> The question flow below is the canonical `AGENT_ONBOARDING.md` body —
> single source of truth; never fork it.

## How to use

1. After adding the Tortoise MCP server (`export TORTOISE_API_KEY=<your key>`
   then `codex mcp add tortoise --url https://api.premiselabs.co/mcp/
   --bearer-token-env-var TORTOISE_API_KEY`), paste this ENTIRE document into
   your Codex chat.
2. Answer the yes/no questions one at a time — the agent runs the setup and
   shows your memory digest at the end (under 5 minutes).

If the agent doesn't start the flow, paste: "Start Tortoise onboarding"

## Persistent alternative (no re-paste per session)

Prefer standing instructions over a one-time chat paste:

- Add this document to your project `AGENTS.md` — Codex reads AGENTS.md files
  before doing any work (global `~/.codex/AGENTS.md` works too).
- MCP config file alternative: instead of the CLI one-liner, add a
  `config.toml` snippet to `~/.codex/config.toml`:
  `[mcp_servers.tortoise]` with `url = "https://api.premiselabs.co/mcp/"` and
  `bearer_token_env_var = "TORTOISE_API_KEY"` (the env-var NAME — never the
  secret). If Tortoise fails to connect, the usual cause is a skipped export —
  the fix is: export TORTOISE_API_KEY=tt_your_key in your shell profile.
