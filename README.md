---
title: "Tortoise — Semantic + Epistemic + Episodic + Procedural Graph Engine"
type: readme
domain: epistemic
status: live
created: 2026-07-24
updated: 2026-08-10
---

# Tortoise

A graph engine for agent memory: claims are **Points**, relationships are **edges**, and belief scores are computed by propagating evidence through the graph (EP — Evidence Propagation).

Tortoise runs **as a service** — self-hosted or hosted — and your tools **connect** to it over MCP (Model Context Protocol). You don't import it into your application; you run it and point your agent at it, the way you'd run MongoDB and connect a driver.

A product of [Premise Labs](https://premiselabs.co).

## Quickstart — Install → Connect → Query

### 1. Install

New to Tortoise? Choose a path:

- **Hosted (managed)** — no install, just connect your agent: [docs/quickstart-cloud.md](docs/quickstart-cloud.md)
- **Self-hosted (run it yourself)** — requires **Python ≥ 3.12**:

  ```bash
  git clone https://github.com/daniel-ospina/tortoise.git && cd tortoise
  pip install -e .                         # or: pip install -e '.[embeddings]' for vector search
  # or straight from GitHub (no clone):
  pip install git+https://github.com/daniel-ospina/tortoise.git
  ```

Operator/infra (deploying and maintaining the daemon): [docs/infra-runbook.md](docs/infra-runbook.md).

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

`pip install tortoise-graph` gives you the **SDK** — a driver for local development, scripting, and power-user access against a running daemon (or embedded mode for experiments). Tortoise is a service: the SDK connects, it doesn't replace the server. (The package is published as `tortoise-graph` on PyPI — the bare `tortoise` name is squatted by an unrelated library, #258.)

```bash
pip install tortoise-graph   # or: pip install 'tortoise-graph[embeddings]' for vector search
# from source: pip install -e .
python -m tortoise.selfhost # run the daemon locally (see env table below)
```

## Self-host configuration

| Env var | Default | Purpose |
|---|---|---|
| `TORTOISE_DB_URI` | — | Durable FalkorDB connection string (recommended) |
| `TORTOISE_DB_PATH` | `/data/tortoise.db` | Embedded FalkorDBLite eval path (AOF-durable to ≤1s since #915; delete the db + `<db>-appendonlydir` to reset) |
| `TORTOISE_API_KEY` | unset | Set → `auth_mode=static` (Bearer key); unset → `auth_mode=none` — ⚠️ a non-localhost bind with no key exposes an unauthenticated engine |
| `TORTOISE_HOST` / `TORTOISE_PORT` | `127.0.0.1` / `8000` | Daemon bind |
| `TORTOISE_RATE_LIMIT` | `100` | Requests per minute per IP (MCP SSE bursts ≈ 5–10 req/call) |
| `TORTOISE_ALLOWED_ORIGINS` | `http://localhost:8000` | CORS allowlist (comma-separated) |
| `TORTOISE_TOOL_GROUP` | unset | Role-scoped MCP surface (#523) — e.g. `memory` exposes only memory tools (tool-selection accuracy degrades past ~20 tools; groups: memory, reasoning, graph, sessions, sources, journal, admin, onboarding) |

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
- `website/` — the hosted product's landing pages + dashboard (deploys to Cloudflare Pages)
- `docs/ONTOLOGY.md` — **canonical ontology v3.4** (co-located with the code it governs)
- `tests/` — test suite

## Canonical ontology

`docs/ONTOLOGY.md` is the single source of truth for the entity model (Point, Subject, Object, Event, Source), edge topology (IMPL/NAND/structural/about*), kind vocabularies, and EP semantics. It is **canonical** — product gaps are filed as issues, never added to the ontology as roadmap detail.

## Repository layout

**`daniel-ospina/tortoise` (this repo)** is the **full product** — graph runtime, SDK, MCP server, self-host daemon + docker compose, Fly.io deployment config (`fly.toml`), Dockerfile.selfhost, embedded reaper, Supabase edge functions (`supabase/functions/tenant-provision/`), backup pipeline, and the hosted dashboard (`website/` dir → Cloudflare Pages). This is the canonical source of truth. If you want to deploy Tortoise (self-host or hosted), this is the only repo you need.

**`daniel-ospina/premise-labs`** is a **partial copy** of the tortoise tree used as an **SDK-import surface** by dependent repos (notably `daniel-ospina/swarm`, which sets `PYTHONPATH` to import `tortoise/projection.py`, `tortoise/ids.py`, `tortoise/pipeline_cli.py`, and `config/` from it). It is **not** the full product — it lacks `docker-compose.yml`, `Dockerfile.selfhost`, `tortoise/embedded_reaper.py`, `tortoise/selfhost.py`, `fly.toml`, and `supabase/functions/tenant-provision/`. It also carries an outdated `.env.example` (port `:6379` in the URI example vs `FALKORDB_PORT=16379`; this repo uses `:16379` consistently).

**Guidance:** if you clone `premise-labs` for swarm imports, also clone `tortoise` for deployment infrastructure. `premise-labs` is SDK-only — it cannot run a Tortoise server. (#761)

## Repo map & issue routing

File issues in the repo that owns the code:

| Repo | Owns | File issues for |
|---|---|---|
| **daniel-ospina/tortoise** (this repo) | Tortoise product: SDK, MCP, hosted API, graph engine, ontology, deployment infra | Tortoise product bugs, features, ontology gaps |
| **daniel-ospina/agent-infra** | Agent infrastructure: Pi extensions, skills, commit-workflow, CI gates, review-enforcer | Skill/pipeline/extension/CI work |
| **daniel-ospina/premise-labs** | Premise Labs internal ops: meetings recorder, CRM (Twenty), bridge scripts, health checks; also an SDK-import surface for the swarm (partial tortoise tree — see above) | Ops tooling, CRM, meeting pipeline |
| **daniel-ospina/eldato** | El Dato app (eldato.com.mx): scanner, webapp, deals/offers, notifications, ads, SEO | El Dato product work |

**Rule of thumb:** if the issue is about Tortoise code (this repo's `tortoise/` or `website/` dirs), file it here. If it's about agent tooling, file in agent-infra. If it's about Premise Labs ops (meetings/CRM), file in premise-labs.
