---
title: "Tortoise Hosted Quickstart"
type: guide
domain: epistemic
doc_status: live
created: 2026-08-08
aboutSubjects: tortoise
aboutObjects: tortoise-cloud, tortoise-mcp, tortoise-rest
---

# Tortoise Hosted Quickstart

Tortoise is a graph engine for agent memory: claims are **Points**, relationships are **edges**, and belief scores are computed by propagating evidence through the graph. The hosted service at **api.premiselabs.co** runs it for you — no install, no ops. You connect your agent over MCP and start filing decisions and observations.

Want to run it yourself instead? See [quickstart-selfhosted.md](quickstart-selfhosted.md).

## 1. Sign up and get your API key

1. Go to **https://tortoise.premiselabs.co/signup** (Supabase sign-up — email or social login).
2. After signup, the welcome page shows your API key (starts with `tt_`) — copy it right away. It's shown **once** (like `POST /v1/team/keys`), so store it somewhere safe; if you lose it, create a new one via `POST /v1/team/keys`.

## 2. Connect your agent (MCP, streamable-http)

The hosted endpoint is `https://api.premiselabs.co/mcp`, and it only speaks **streamable-http** — that's the only correct hosted pattern. Auth is a Bearer header with your API key.

Add this to your client's `.mcp.json` (Claude Code, Cursor, and most MCP clients read this file):

```json
{
  "mcpServers": {
    "tortoise": {
      "type": "streamable-http",
      "url": "https://api.premiselabs.co/mcp",
      "headers": {
        "Authorization": "Bearer tt_YOUR_KEY"
      }
    }
  }
}
```

**Codex** instead:

```bash
codex mcp add tortoise --url https://api.premiselabs.co/mcp --bearer-token-env-var TORTOISE_API_KEY
# then export TORTOISE_API_KEY=tt_YOUR_KEY in your shell
```

Restart your client. You should now have Tortoise's tools (create points, query the graph, run evidence propagation, capture sessions).

Rate limit: **100 requests/min per key**.

## 3. Use the CLI (optional, for scripting)

```bash
pip install git+https://github.com/daniel-ospina/tortoise.git

tortoise init --api-key tt_YOUR_KEY    # connects this directory to Tortoise Cloud
```

⚠️ `tortoise init --api-key` saves the config (a `.tortoise` file with your key) to the **current directory** — run the other CLI commands from that same directory.

Smoke test:

```bash
tortoise team info       # shows your team and usage
```

Everyday commands:

```bash
tortoise create-point "The API keys are stored client-side" --kind statement
tortoise session capture --file transcript.txt     # file your agent sessions
tortoise session list                               # what's been captured
tortoise context                                    # memory digest for session-start hooks
```

## 4. Use the REST API

Base URL `https://api.premiselabs.co`, header `Authorization: Bearer tt_YOUR_KEY` on every request.

| Method & path | Purpose |
|---|---|
| `POST /v1/points` | Create a Point |
| `GET /v1/points` | List Points |
| `GET /v1/points/{id}` | Fetch one Point |
| `GET /v1/search?q=<query>` | Search the graph |
| `GET /v1/team` | Team info and usage |
| `POST /v1/sessions` | Create / capture a session |
| `GET /v1/sessions` | List sessions |
| `GET /v1/context` | Memory digest for the current team |
| `POST /v1/team/keys` | Create an API key |
| `GET /v1/team/keys` | List API keys |
| `DELETE /v1/team/keys/{key_id}` | Revoke an API key |

```bash
curl -s https://api.premiselabs.co/v1/team \
  -H "Authorization: Bearer tt_YOUR_KEY"
```

## 5. Manage API keys

- **Create:** `POST /v1/team/keys` — the plaintext key is shown **once** in the response; store it immediately.
- **List:** `GET /v1/team/keys` — see all keys for the team (plaintext is not returned again).
- **Revoke:** `DELETE /v1/team/keys/{key_id}` — instantly invalidates that key. Losing a key? Revoke it and create a replacement.
