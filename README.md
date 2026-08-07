---
title: "Tortoise — Semantic + Epistemic + Episodic + Procedural Graph Engine"
type: readme
domain: epistemic
status: live
created: 2026-07-24
updated: 2026-08-07
---

# Tortoise

A graph engine for agent memory: claims are **Points**, relationships are **edges**, and belief scores are computed by propagating evidence through the graph (EP — Evidence Propagation).

Tortoise runs **as a service** — self-hosted or hosted — and your tools **connect** to it over MCP (Model Context Protocol). You don't import it into your application; you run it and point your agent at it, the way you'd run MongoDB and connect a driver.

A product of [Premise Labs](https://premiselabs.co).

## Quickstart — Install → Connect → Query

### 1. Install

Pick one:

| Path | How | Best for |
|---|---|---|
| **Hosted** | Sign up at [tortoise.premiselabs.co](https://api.premiselabs.co) (free tier available), get an API key on the welcome page | Teams that want a managed server; zero ops |
| **Self-host (eval)** | `docker run -p 8000:8000 ghcr.io/daniel-ospina/tortoise-selfhost` | Solo devs trying it locally (embedded DB — **not durable**, for eval only) |
| **Self-host (durable)** | `docker compose up -d` — daemon + FalkorDB sidecar (AOF on, backups) | Production self-hosting; data locality and trust |

> ⚠️ Embedded mode (plain `docker run`) is for evaluation only — it is not durable. For real data use `docker compose` or point the daemon at a FalkorDB with `TORTOISE_DB_URI`. See [License & FAQ](#license--faq) and [docs/infra-runbook.md](docs/infra-runbook.md).

### 2. Connect

Point your agent at Tortoise over MCP:

```bash
# Hosted
claude mcp add tortoise https://api.premiselabs.co/mcp
# or self-hosted
claude mcp add tortoise http://localhost:8000/mcp
```

```bash
# Codex
codex mcp add tortoise http://localhost:8000/mcp --bearer-token-env-var TORTOISE_API_KEY
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

### 3. Query

Your agent now has Tortoise's tools — create points, query the graph, check belief structure, run evidence propagation. See the [MCP tool surface](tortoise/tool_registry.py) for the full registry (58 tools) and the [REST reference](tortoise/tool_registry.py) (as it lands).

## SDK for local dev / scripting

`pip install tortoise` gives you the **SDK** — a driver for local development, scripting, and power-user access against a running daemon (or embedded mode for experiments). Tortoise is a service: the SDK connects, it doesn't replace the server.

```bash
pip install -e .            # or: pip install 'tortoise[embeddings]' for vector search
python -m tortoise.selfhost # run the daemon locally (see env table below)
```

## Self-host configuration

| Env var | Default | Purpose |
|---|---|---|
| `TORTOISE_DB_URI` | — | Durable FalkorDB connection string (recommended) |
| `TORTOISE_DB_PATH` | `/data/tortoise.db` | Embedded FalkorDBLite eval path (⚠️ not durable) |
| `TORTOISE_API_KEY` | unset | Set → `auth_mode=static` (Bearer key); unset → `auth_mode=none` — ⚠️ a non-localhost bind with no key exposes an unauthenticated engine |
| `TORTOISE_HOST` / `TORTOISE_PORT` | `127.0.0.1` / `8000` | Daemon bind |
| `TORTOISE_RATE_LIMIT` | `100` | Requests per minute per IP (MCP SSE bursts ≈ 5–10 req/call) |
| `TORTOISE_ALLOWED_ORIGINS` | `http://localhost:8000` | CORS allowlist (comma-separated) |

Also: `tortoise-serve http [--host] [--port] [--api-key]` (flags override env), and `tortoise-serve` (stdio MCP) for scripting.

## License & FAQ

Tortoise is **Business Source License 1.1** — see [LICENSE](LICENSE) and the [license notes](docs/license-notes.md) (clause-by-clause precedent + audit).

- **Self-hosted:** free production use for organizations under **US $5,000,000** annual revenue (trailing 12 months); above that, a commercial license is required.
- **Hosted (api.premiselabs.co):** a separate commercial product with a **free tier** — not covered by the BSL grant.
- **MIT products are never blocked:** connect over MCP/REST and you never import Tortoise — the license boundary sits at the network, so your distribution stays clean.
- **MPL 2.0 conversion:** every version converts to Mozilla Public License 2.0 (file-level copyleft — enterprise-safe) four years after publication.
- **Can't offer Tortoise as a service:** the grant never permits reselling Tortoise (or a substantially similar product) to third parties as a hosted/managed service.

## What's here

- `tortoise/` — the SDK, MCP server, projection, search engine, backup/restore, and the self-host daemon (`tortoise/selfhost.py`)
- `integrations/` — thin connectors that talk to Tortoise over MCP (not SDK imports)
- `premise-labs/` — the hosted product's landing pages + dashboard (deploys to Cloudflare Pages)
- `docs/ONTOLOGY.md` — **canonical ontology v3.1** (co-located with the code it governs)
- `tests/` — test suite

## Canonical ontology

`docs/ONTOLOGY.md` is the single source of truth for the entity model (Point, Subject, Object, Event, Source), edge topology (IMPL/NAND/structural/about*), kind vocabularies, and EP semantics. It is **canonical** — product gaps are filed as issues, never added to the ontology as roadmap detail.

## Repo map & issue routing

File issues in the repo that owns the code:

| Repo | Owns | File issues for |
|---|---|---|
| **daniel-ospina/tortoise** (this repo) | Tortoise product: SDK, MCP, hosted API, graph engine, ontology | Tortoise product bugs, features, ontology gaps |
| **daniel-ospina/agent-infra** | Agent infrastructure: Pi extensions, skills, commit-workflow, CI gates, review-enforcer | Skill/pipeline/extension/CI work |
| **daniel-ospina/premise-labs** | Premise Labs internal ops: meetings recorder, CRM (Twenty), bridge scripts, health checks | Ops tooling, CRM, meeting pipeline |
| **daniel-ospina/eldato** | El Dato app (eldato.com.mx): scanner, webapp, deals/offers, notifications, ads, SEO | El Dato product work |

**Rule of thumb:** if the issue is about Tortoise code (this repo's `tortoise/` or `premise-labs/` dirs), file it here. If it's about agent tooling, file in agent-infra. If it's about Premise Labs ops (meetings/CRM), file in premise-labs.
