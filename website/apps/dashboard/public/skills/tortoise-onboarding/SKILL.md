---
name: tortoise-onboarding
description: "Connect your agent to Tortoise — 3 steps: add config, reload, write your first point. That's it."
domain: capability
type: Workflow
status: live
tags: [tortoise, onboarding, mcp, connect]
summary: "Connect any agent to Tortoise in 3 steps: add the MCP config, reload, and write a point — onboarding auto-completes when the agent makes its first real graph write."
created: 2026-09-02
updated: 2026-09-08
allowed-tools: read write bash
---

# Tortoise Onboarding — 3 steps

> **⛔ This is the single live onboarding script.** Previous versions are
> archived under `tortoise/onboarding/archive/`.

Connect your agent to Tortoise. Three steps, one config line, done.

## How it works

1. You add one config line to your agent's MCP config (`.mcp.json` for Pi,
   `claude mcp add` for Claude Code, etc.)
2. You reload / restart your agent
3. You make your first graph write (create a point, file a decision) — the
   server auto-completes the remaining onboarding steps

No state machines. No ceremony. No "go back to the dashboard." The server
detects when onboarding is incomplete and an agent makes a real write, then
auto-files `harness-connected`, `first-points-filed`, and `decide-completed`
and flips status to complete. Once onboarding completes, the onboarding tools
retire from your agent's tool list automatically.

## The config

Pick your agent below. The config is one MCP server pointing at
`https://api.premiselabs.co/mcp/` with your API key as a Bearer token.

### Pi

Create or merge `.mcp.json` in your project:

```json
{ "mcpServers": { "tortoise": { "type": "http", "url": "https://api.premiselabs.co/mcp/", "headers": { "Authorization": "Bearer ${TORTOISE_API_KEY}" } } } }
```

Pi's mcp-client expands plain `${TORTOISE_API_KEY}` (no `env:` prefix).
Set `TORTOISE_API_KEY` in your shell profile (`~/.zshrc` or `~/.bashrc`).

After saving the config, **run `/reload` in Pi** (or restart Pi) — tortoise
connects eagerly at startup. Then call `tortoise_health` to verify.

### Claude Code

```bash
claude mcp add --transport http tortoise https://api.premiselabs.co/mcp/ \
  --header "Authorization: Bearer ${TORTOISE_API_KEY}"
```

`$TORTOISE_API_KEY` must be exported in your shell profile.

### Cursor

Create/merge `.cursor/mcp.json`:

```json
{ "mcpServers": { "tortoise": { "type": "http", "url": "https://api.premiselabs.co/mcp/", "headers": { "Authorization": "Bearer ${env:TORTOISE_API_KEY}" } } } }
```

Set `TORTOISE_API_KEY` in your environment (Cursor settings or shell profile).

### Codex CLI

```bash
export TORTOISE_API_KEY=<key>
codex mcp add tortoise --url https://api.premiselabs.co/mcp/ --bearer-token-env-var TORTOISE_API_KEY
```

Persist the export in your shell profile.

### Claude Desktop

Open `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) and
merge:

```json
{ "mcpServers": { "tortoise": { "type": "http", "url": "https://api.premiselabs.co/mcp/", "headers": { "Authorization": "Bearer <TORTOISE_API_KEY>" } } } }
```

Restart Claude Desktop. Keep the file private — the key is a literal.

### Claude Web

claude.ai > Settings > Connectors > Add custom connector, name it "Tortoise".
Server URL: `https://api.premiselabs.co/mcp/`; Request headers (advanced):
`Authorization: Bearer <TORTOISE_API_KEY>`

## Verify

After connecting, call `tortoise_health`. It should return the graph
reachable and your organization context.

Then **make your first write** — call `tortoise_create_point` to file a
statement, or `tortoise_file_decision` to record a decision. The server
auto-completes onboarding on the first successful write.

That's it. You're set up.