---
title: "Tortoise Graph Viz"
type: readme
domain: epistemic
doc_status: live
created: 2026-07-25
updated: 2026-07-28
---

# Tortoise Graph Viz

Interactive force-directed visualization of the Tortoise epistemic knowledge graph.

## Graphs Available

| Name | Location | Port | Auth | Size |
|---|---|---|---|---|
| **Work graph** (default) | `falkordb-personal` Docker | `localhost:16379` | none | ~8.7k claims, ~13k edges |
| Test/dev | `falkordb` Docker | `127.0.0.1:6379` | `falkordb` | 481 claims |
| Medical research | `falkordb-personal` Docker | `localhost:16379` | none | `endometriosis_melasma` |

**Agents:** Call `GET /api/health` to discover the active graph and all available graphs.

## Quick Start (new machine)

```bash
cd apps/graph-viz

# 1. Start FalkorDB (Docker required)
docker compose up -d

# 2. Start backend (auto-retries until FalkorDB is ready)
python3 server/main.py &

# 3. Start frontend
npx vite --host --port 5173

# → http://localhost:5173
# → Health check: http://localhost:8000/api/health
```

No config needed — defaults to `localhost:16379` which is what `docker compose up` starts. Agents discover the graph by calling `GET localhost:8000/api/health`.

## Switching Graphs

Set env vars or edit `server/main.py`:

```bash
# Work graph (default — docker compose up)
FALKORDB_HOST=localhost FALKORDB_PORT=16379 python3 server/main.py

# Legacy remote instance (100.123.148.23 still uses old container)
# FALKORDB_HOST=100.123.148.23 FALKORDB_PORT=6380 python3 server/main.py

# Test instance
FALKORDB_HOST=127.0.0.1 FALKORDB_PORT=6379 FALKORDB_PASSWORD=falkordb python3 server/main.py

# FalkorDB Cloud / ACL-auth instance (#1079) — username + optional TLS
FALKORDB_HOST=<cloud-host> FALKORDB_PORT=<port> FALKORDB_USERNAME=tortoise FALKORDB_PASSWORD=<secret> FALKORDB_SSL=1 python3 server/main.py
```

See `.env.example` for all options (`FALKORDB_USERNAME`, `FALKORDB_SSL`, `FALKORDB_GRAPH`).

## API

| Endpoint | Description |
|---|---|
| `GET /api/health` | Graph discovery — which graph is active + all available graphs |
| `GET /api/graph?limit=N` | Get N nodes and their edges |
| `GET /api/search?q=...` | Search nodes by content |
| `POST /api/points` | Create a new claim |
| `POST /api/edges` | Create an edge (IMPL or NAND) |
| `GET /api/sources` | List available data sources |

## Frontend

- `src/App.jsx` — Single React component with ForceGraph2D
- `src/index.css` — Global styles (#root uses 100% width)
- Node colors by community, edges green (IMPL) / red (NAND)
- Confidence badges on nodes (green >70%, yellow 40-70%, red <40%)
- EP ▶ button runs belief propagation
- Right-click on canvas to add new claims at specific positions

