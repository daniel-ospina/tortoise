---
title: "Tortoise Self-Hosted Quickstart"
type: guide
domain: epistemic
doc_status: live
created: 2026-08-08
aboutSubjects: tortoise
aboutObjects: tortoise-cli, tortoise-mcp
---

# Tortoise Self-Hosted Quickstart

Tortoise is a graph engine for agent memory: claims are **Points**, relationships are **edges**, and belief scores are computed by propagating evidence through the graph. You run Tortoise as a service and your tools connect to it over MCP — like running MongoDB and connecting a driver.

This guide gets you from zero to a running local MCP server in about 5 minutes. The default path needs **no Docker**.

Prefer a managed server? See [quickstart-cloud.md](quickstart-cloud.md) — sign up, paste an API key, done. Operator/infra notes (deploying, upgrading, backing up the daemon itself): [infra-runbook.md](infra-runbook.md).

## Prerequisites

- **Python ≥ 3.12** — check with `python3 --version`
- **Git** — check with `git --version`

## 1. Install

Pick one:

```bash
# From source (clone)
git clone https://github.com/daniel-ospina/tortoise.git
cd tortoise
pip install -e .

# Or straight from GitHub (no clone)
pip install git+https://github.com/daniel-ospina/tortoise.git
```

- **Never** run `pip install tortoise` — the name is taken on PyPI by an unrelated turtle-graphics package. This project is published as `tortoise-graph`.
- Optional embeddings (vector search): `pip install -e '.[embeddings]'`.
- Use a venv: `python3 -m venv .venv && source .venv/bin/activate`. On Homebrew and Ubuntu, PEP 668 blocks bare `pip` installs into the system Python ("externally-managed-environment") — the venv sidesteps that.

## 2. Choose a database

### Option A — Embedded (no Docker, recommended to start)

`tortoise init` auto-creates `~/.tortoise/tortoise.db` using **falkordblite** (a self-contained, SQLite-backed FalkorDB). Nothing to run, nothing to manage — the CLI handles it.

### Option B — Docker (FalkorDB server) *(requires Docker)*

```bash
docker run -d --name tortoise-falkordb -p 16379:6379 falkordb/falkordb:latest
```

Point Tortoise at it with `TORTOISE_DB_URI=docker://:@localhost:16379/tortoise`.

The passwordless URI is **canonical** — don't add a password unless you started the container with `--requirepass <password>` (then use `docker://:<password>@localhost:16379/tortoise`).

## 3. Set the DB environment variable

| Env var | Used with | Example |
|---|---|---|
| `TORTOISE_DB_URI` | Docker | `docker://:@localhost:16379/tortoise` |
| `TORTOISE_DB_PATH` | Embedded | `~/.tortoise/tortoise.db` (the default) |

⚠️ Set these in your **MCP client's `env` block** (step 5) — **not** in a `.env` file. A repo-root `.env` is only auto-loaded for editable installs; the CLI reads the process environment only.

## 4. Create your first graph

```bash
tortoise init          # interactive — creates the graph, writes a welcome Point
tortoise init --yes    # same, no prompts (auto-indexes the repo you're inside, if any)
```

To index an existing repo's markdown files:

```bash
tortoise index github https://github.com/your/repo --db <path-or-uri>
```

(Do **not** use `tortoise onboard` — it crashes in embedded-only mode; tracked as #705.)

> ⚠️ **Known issue on this release:** `tortoise index github` fails with `ModuleNotFoundError: No module named 'tortoise.extraction_pipeline'` — the markdown extraction pipeline it calls was removed from the codebase and the command hasn't been rewired. Until it's fixed, add points through your agent once it's connected (step 5) via the `tortoise_create_point` MCP tool, or use the hosted CLI (`tortoise create-point`, see quickstart-cloud.md).

## 5. Connect your agent (MCP, stdio)

Add a `tortoise` server to your MCP client's config (`.mcp.json` for Claude Code / Cursor, or the equivalent for your client):

```json
{
  "mcpServers": {
    "tortoise": {
      "command": "python3",
      "args": ["-m", "tortoise.mcp_server"],
      "cwd": "/abs/path/to/tortoise",
      "env": {
        "PYTHONPATH": "/abs/path/to/tortoise",
        "TORTOISE_DB_PATH": "~/.tortoise/tortoise.db"
      }
    }
  }
}
```

- If you installed with `pip install git+...` (no clone), drop `cwd` and `PYTHONPATH` — the package is importable from anywhere.
- Inside a venv, `python3` must be the venv's interpreter (e.g. `/abs/path/to/tortoise/.venv/bin/python3`), not a different system Python.
- ⚠️ `tortoise serve` hard-exits unless `TORTOISE_DB_URI` or `TORTOISE_DB_PATH` is set — the config above always sets one.

### Dev-mode note: don't set TORTOISE_API_KEY for stdio

The stdio transport can't carry auth tokens. If `TORTOISE_API_KEY` is set, every MCP tool rejects your calls — and without `TORTOISE_SECRET_PEPPER` the server crashes at import. Leave both unset for local stdio.

**Authenticated local MCP (optional):** for real auth over localhost, use the HTTP server with tenant keys:

```bash
tortoise key create                 # prints a tt_... key once
tortoise serve --http --auth tenant # streamable-http on http://127.0.0.1:8000/mcp
```

Point your client at `http://127.0.0.1:8000/mcp` with header `Authorization: Bearer tt_<key>`.

> ℹ️ HTTP tenant mode uses a fresh `team_{id}` namespace — data you wrote over stdio stays in the `tortoise` graph. They're separate namespaces.

## 6. Verify and back up

```bash
tortoise doctor                                        # health check
tortoise backup --db ~/.tortoise/tortoise.db           # snapshot → backups/<timestamp>/
```

## Troubleshooting

- **`pip` refuses to install ("externally-managed-environment")** — you're on Homebrew/Ubuntu system Python. Create and activate a venv first (step 1).
- **MCP client can't find the module / tools never load** — the `python3` in the MCP config is a different interpreter than the venv you installed into. Use the venv's absolute path.
- **`tortoise serve` exits immediately with "Neither TORTOISE_DB_URI nor TORTOISE_DB_PATH is set"** — set one in the MCP client's `env` block (step 5).
- **Port 16379 already in use** — another container/process is on it. Map a different host port (`-p 16380:6379`) and use `docker://:@localhost:16380/tortoise`.
- **Upgrading** — clone: `git pull && pip install -e .`; direct install: `pip install -U git+https://github.com/daniel-ospina/tortoise.git`.
- **`tortoise doctor` shows a Docker ❌** — expected in embedded-only mode (no container running). It also trips a known `'Namespace' object has no attribute 'path'` error in its graph-health check; the remaining checks still run.
