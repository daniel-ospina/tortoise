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

This guide gets you from zero to a running server in about 5 minutes. **The recommended path uses Docker** (durable, multi-writer safe); a no-Docker embedded path is available for single-agent eval only.

Prefer a managed server? See [quickstart-cloud.md](quickstart-cloud.md) — sign up, paste an API key, done. Operator/infra notes (deploying, upgrading, backing up the daemon itself): [infra-runbook.md](infra-runbook.md).

## Prerequisites

- **Python ≥ 3.12** — check with `python3 --version` (only needed for the pip/embedded path; the Docker path needs no local Python)
- **Docker** — check with `docker --version` (recommended path)
- **Git** — check with `git --version`

## Which path?

| You are… | Use… |
|---|---|
| One agent, experimenting / laptop eval | **Embedded (Option C)** — single-writer, eval only |
| A team / multiple agents / production | **Docker (Option A or B)** — durable multi-writer |
| Zero-ops | [Hosted Cloud](quickstart-cloud.md) |

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

### Option A — Docker compose (recommended, durable)

The repo ships a complete reference topology (`docker-compose.yml`): the daemon plus a **FalkorDB sidecar** with AOF on, a named volume, a healthcheck, and `TORTOISE_DB_URI` wired. This is the durable multi-writer path — safe for teams and multiple agents writing concurrently.

```bash
docker compose up -d          # daemon on http://localhost:8000 (MCP at /mcp)
```

- The sidecar is bound to `127.0.0.1:6379` (loopback only) so host-side tools can reach it with `TORTOISE_DB_URI=docker://:falkordb@localhost:6379/tortoise`.
- Set a strong `TORTOISE_API_KEY` in `docker-compose.yml` before exposing beyond localhost.

### Option B — Bare container (durable variant) *(requires Docker)*

Same image, standalone — useful when you don't want the full compose stack:

```bash
docker run -d --name tortoise-falkordb -p 127.0.0.1:6379:6379 \
  -e REDIS_ARGS="--requirepass falkordb --appendonly yes" \
  falkordb/falkordb-server:latest
```

Point Tortoise at it with `TORTOISE_DB_URI=docker://:falkordb@localhost:6379/tortoise`.

⚠️ Auth/AOF go via the **`REDIS_ARGS` env var** — the falkordb image entrypoint ignores command-line args. A bare `--requirepass` as a run arg silently starts a passwordless sidecar.

### Option C — Embedded (single-agent eval only, no Docker)

`tortoise init` auto-creates `~/.tortoise/tortoise.db` using **falkordblite** (a self-contained, SQLite-backed FalkorDB). Nothing to run, nothing to manage — the CLI handles it.

> ⚠️ **Embedded FalkorDBLite is SINGLE-WRITER / EVAL ONLY.** Concurrent writers (multiple agents) lose data on this engine. Fine for one agent evaluating Tortoise; for a team or production use Option A/B or Cloud.

## 3. Set the DB environment variable

| Env var | Used with | Example |
|---|---|---|
| `TORTOISE_DB_URI` | Docker (Option A/B) | `docker://:falkordb@localhost:6379/tortoise` |
| `TORTOISE_DB_PATH` | Embedded (Option C) | `~/.tortoise/tortoise.db` (the default) |

⚠️ Set these in your **MCP client's `env` block** (step 5) — **not** in a `.env` file. A repo-root `.env` is only auto-loaded for editable installs; the CLI reads the process environment only. (With the compose daemon, the URI is already wired inside compose — host-side clients only need it if they talk to the sidecar directly.)

## 4. Create your first graph

```bash
tortoise init          # interactive — creates the graph, writes a welcome Point
tortoise init --yes    # same, no prompts (auto-indexes the repo you're inside, if any)
```

`tortoise init` resolves the DB target from `TORTOISE_DB_URI` (Docker) or `TORTOISE_DB_PATH` (embedded); the embedded success line labels itself single-writer eval only.

To index an existing repo's markdown files:

```bash
tortoise index github https://github.com/your/repo --db <path-or-uri>
```

`index github` clones the repo (or accepts a local path), extracts deterministically with offline mock models, and writes Points/Operators to the graph — idempotent across runs. For richer LLM-based extraction, use the standalone ingest CLI instead — `tortoise-ingest transcript.txt --db <path-or-uri>` (or `python -m tortoise.ingest`). It ingests a transcript file, requires `--db`, and defaults to offline mock models; pass `--point-model`/`--relation-model` (e.g. `ollama:llama3.2:3b`) to use a real LLM. `tortoise onboard` runs the full init → index → demo → doctor flow and passes the same resolved DB target to each step, so it works in embedded-only mode too (it used to crash; fixed in #705).

## 5. Connect your agent (MCP)

### Docker path (recommended) — connect to the daemon

The compose daemon serves MCP at `http://localhost:8000/mcp`:

```bash
claude mcp add tortoise http://localhost:8000/mcp
```

Or add to `.mcp.json`:

```json
{
  "mcpServers": {
    "tortoise": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

### No-Docker path (single-agent eval) — stdio

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

- This runs **embedded FalkorDBLite — single-writer, eval only**. A single agent is fine; two or more MCP clients sharing the embedded DB are concurrent writers and lose data. For multiple agents use the Docker path above.
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

> ℹ️ `serve --http --auth tenant` on an **embedded** DB is single-agent eval only — a durable team deployment uses Docker (Option A/B) or Cloud. (Compose users: the daemon already serves `/mcp` with auth via `TORTOISE_API_KEY`.)
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
- **Port 6379 already in use** — another container/process (local redis, another compose stack) is on it. Remap the sidecar: `-p 127.0.0.1:16380:6379` (Option B) or edit the compose `ports:` entry, then use `docker://:falkordb@localhost:16380/tortoise`.
- **Upgrading** — clone: `git pull && pip install -e .`; direct install: `pip install -U git+https://github.com/daniel-ospina/tortoise.git`.
- **`tortoise doctor` shows a Docker ❌** — expected in embedded-only mode (no container running). The graph-health check runs against the resolved DB target; `doctor --db <uri|path>` and `doctor --path` explicitly target a specific DB, and the bare `doctor` invocation works without extra flags.
