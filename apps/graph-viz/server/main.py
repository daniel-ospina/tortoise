"""
Tortoise Graph Viz — FastAPI backend (FalkorDB live).

Available graphs (in Docker / OrbStack):
  - falkordb (localhost:16379) no auth → graph "tortoise" (WORK GRAPH — default, docker compose up)
  - falkordb (localhost:16379) no auth → graph "endometriosis_melasma" (medical research)
  - falkordb (127.0.0.1:6379) password=falkordb → graph "tortoise" (test/dev, 481 claims)

To switch graphs, set env vars:
  FALKORDB_HOST, FALKORDB_PORT, FALKORDB_PASSWORD, FALKORDB_GRAPH
"""
from __future__ import annotations

import random, math
from collections import Counter
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from falkordb import FalkorDB
import os

app = FastAPI(title="Tortoise Graph Viz")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── FalkorDB connection (defaults work on any new machine via docker-compose) ──
DB_HOST = os.environ.get("FALKORDB_HOST", "localhost")
DB_PORT = int(os.environ.get("FALKORDB_PORT", "16379"))
DB_PASSWORD = os.environ.get("FALKORDB_PASSWORD", None)
DB_GRAPH = os.environ.get("FALKORDB_GRAPH", "tortoise")
CONTEXT_DEFAULT = "product-strategy"

# ── Agent discovery protocol ──
# Agents discover this backend at: http://localhost:8000/api/health
# Override with: export TORTOISE_HOST=http://host:port
# The /api/health endpoint returns which graph is active and all available graphs.

kwargs = {"host": DB_HOST, "port": DB_PORT}
if DB_PASSWORD: kwargs["password"] = DB_PASSWORD
import time
for attempt in range(1, 31):  # retry for ~30s
    try:
        _db = FalkorDB(**kwargs)
        _g = _db.select_graph(DB_GRAPH)
        _g.query("RETURN 1")
        print(f"Connected to FalkorDB at {DB_HOST}:{DB_PORT} (graph: {DB_GRAPH})", flush=True)
        break
    except Exception as e:
        if attempt == 30:
            print(f"❌ FalkorDB unreachable after 30s. Is it running? docker compose up -d", flush=True)
            print(f"   Trying {DB_HOST}:{DB_PORT} — {e}", flush=True)
            import sys; sys.exit(1)
        print(f"Waiting for FalkorDB ({DB_HOST}:{DB_PORT})... attempt {attempt}/30", flush=True)
        time.sleep(1)

# ── Global cache (computed once) ──
_global_confidence = {}  # node_id -> confidence percentile (0-1)
_global_mit_count = 0
_total_nodes = 0
_total_edges = 0
_cache_built = False


def _build_cache():
    global _global_confidence, _global_mit_count, _total_nodes, _total_edges, _cache_built
    if _cache_built:
        return

    try:
        _total_nodes = _g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
    except:
        _total_nodes = 0
    try:
        _total_edges = _g.query("MATCH ()-[r:RELATION]->() RETURN count(r)").result_set[0][0]
    except:
        _total_edges = 0

    # Compute global degree centrality
    degree = Counter()
    try:
        # Sample approach: batch query all edges
        results = _g.query(
            "MATCH (a:Point)-[r:RELATION]-(b:Point) RETURN a.id, count(r) ORDER BY count(r) DESC LIMIT 5000"
        ).result_set
        for row in results:
            degree[row[0]] = row[1]
    except:
        pass

    if degree:
        degs = sorted(degree.values())
        n = len(degs)
        for pid, deg in degree.items():
            pct = sum(1 for d in degs if d <= deg) / n if n > 0 else 0.5
            _global_confidence[pid] = round(0.15 + pct * 0.85, 2)

    # Synthetic mitigations: ~5% of the top NAND edges by degree
    try:
        nand_raw = _g.query(
            "MATCH (a:Point)-[r:RELATION {type:'NAND'}]->(b:Point) RETURN a.id, b.id LIMIT 2000"
        ).result_set
        _global_mit_count = max(5, len(nand_raw) // 15)
    except:
        _global_mit_count = 0

    _cache_built = True
    print(f"Cache built: {_total_nodes} nodes, {_total_edges} edges, {len(_global_confidence)} confidence scores, {_global_mit_count} mitigations")


_build_cache()


def _query_graph(limit=200, only_ids=None):
    if only_ids and len(only_ids) > 0:
        ids = list(only_ids)[:500]
        ids_str = ", ".join(f"'{i}'" for i in ids)
        try:
            nodes_raw = _g.query(
                f"MATCH (n:Point) WHERE n.id IN [{ids_str}] RETURN n.id, n.content, n.context"
            ).result_set
            edges_raw = _g.query(
                f"MATCH (a:Point)-[r:RELATION]->(b:Point) WHERE a.id IN [{ids_str}] AND b.id IN [{ids_str}] RETURN a.id, b.id, r.type"
            ).result_set
        except Exception as e:
            print(f"Query error: {e}")
            nodes_raw, edges_raw = [], []
    else:
        try:
            nodes_raw = _g.query(
                "MATCH (n:Point) OPTIONAL MATCH (n)-[r:RELATION]-() "
                "RETURN n.id, n.content, n.context, count(r) as deg ORDER BY deg DESC LIMIT %d" % limit
            ).result_set
            top_ids = [r[0] for r in nodes_raw]
            ids_str = ", ".join(f"'{i}'" for i in top_ids)
            edges_raw = _g.query(
                f"MATCH (a:Point)-[r:RELATION]->(b:Point) WHERE a.id IN [{ids_str}] AND b.id IN [{ids_str}] RETURN a.id, b.id, r.type"
            ).result_set
        except Exception as e:
            print(f"Query error: {e}")
            nodes_raw, edges_raw = [], []

    # Build nodes with global confidence
    nodes = []
    for row in nodes_raw:
        pid = row[0]
        nodes.append({
            "id": pid,
            "content": (row[1] or "")[:500],
            "context": row[2] or "",
            "confidence": _global_confidence.get(pid, 0.3),
        })

    # Build edges with mitigations
    edges = []
    reasons = ['partial overlap', 'context mismatch', 'temporal decay', 'source dispute', 'scope limitation']
    mit_idx = 0
    for row in edges_raw:
        etype = row[2]
        mits = []
        if etype == "NAND" and mit_idx < _global_mit_count:
            # Assign mitigation to every Nth NAND edge
            if mit_idx % 8 == 0:
                mits = [{"id": f"mit-{row[0][:8]}", "content": random.choice(reasons), "strength": round(random.uniform(0.2, 0.7), 2)}]
            mit_idx += 1
        edges.append({
            "id": f"{row[0]}-{row[1]}",
            "source": row[0],
            "target": row[1],
            "type": etype,
            "confidence": (_global_confidence.get(row[0], 0.3) + _global_confidence.get(row[1], 0.3)) / 2,
            "mitigations": mits,
        })

    return {"nodes": nodes, "edges": edges}


class PointCreate(BaseModel):
    content: str
    context: str = ""


class EdgeCreate(BaseModel):
    source: str
    target: str
    type: str


@app.get("/api/graph")
def get_graph(limit: int = 200):
    data = _query_graph(limit)
    data["total_nodes"] = _total_nodes
    data["total_edges"] = _total_edges
    data["total_mitigations"] = _global_mit_count
    return data


@app.get("/api/graph/neighborhood/{node_id}")
def get_neighborhood(node_id: str, depth: int = 1):
    visited = {node_id}
    frontier = {node_id}
    for _ in range(depth):
        ids_str = ", ".join(f"'{i}'" for i in frontier)
        try:
            neighbors = _g.query(
                f"MATCH (a:Point)-[r:RELATION]-(b:Point) WHERE a.id IN [{ids_str}] RETURN DISTINCT b.id"
            ).result_set
        except:
            neighbors = []
        new_ids = {r[0] for r in neighbors} - visited
        visited |= new_ids
        frontier = new_ids
    data = _query_graph(only_ids=visited)
    data["total_nodes"] = _total_nodes
    data["total_edges"] = _total_edges
    data["total_mitigations"] = _global_mit_count
    data["center"] = node_id
    return data


@app.get("/api/search")
def search_nodes(q: str, limit: int = 20):
    try:
        results = _g.query(
            "MATCH (n:Point) WHERE n.content CONTAINS '%s' RETURN n.id, n.content LIMIT %d" % (q.replace("'", "\\'"), limit)
        ).result_set
    except:
        results = []
    return {"results": [{"id": r[0], "content": (r[1] or "")[:200]} for r in results]}


@app.post("/api/points")
def add_point(body: PointCreate):
    import uuid
    pid = f"user-{uuid.uuid4().hex[:12]}"
    c = body.content.replace("'", "\\'")[:500]
    ctx = (body.context or "").replace("'", "\\'")[:200]
    _g.query("CREATE (:Point {id:'%s', content:'%s', context:'%s'})" % (pid, c, ctx))
    global _total_nodes
    _total_nodes += 1
    return {"id": pid}


@app.delete("/api/points/{point_id}")
def delete_point(point_id: str):
    if point_id.startswith("user-"):
        _g.query("MATCH (n:Point {id:'%s'}) DETACH DELETE n" % point_id)
        global _total_nodes
        _total_nodes -= 1
        return {"deleted": point_id}
    raise HTTPException(403, "Can only delete user-created points")


@app.post("/api/edges")
def add_edge(body: EdgeCreate):
    import uuid
    eid = f"user-edge-{uuid.uuid4().hex[:12]}"
    q = "MATCH (a:Point {id:'%s'}), (b:Point {id:'%s'}) CREATE (a)-[:RELATION {type:'%s', id:'%s'}]->(b)" % (
        body.source, body.target, body.type, eid)
    _g.query(q)
    global _total_edges
    _total_edges += 1
    return {"id": eid}


@app.delete("/api/edges/{edge_id}")
def delete_edge(edge_id: str):
    if edge_id.startswith("user-edge-"):
        _g.query("MATCH ()-[r:RELATION {id:'%s'}]->() DELETE r" % edge_id)
        global _total_edges
        _total_edges -= 1
        return {"deleted": edge_id}
    raise HTTPException(403, "Can only delete user-created edges")


@app.get("/api/sources")
def get_sources():
    try:
        raw = _g.query("MATCH (n:Point) WHERE n.context IS NOT NULL RETURN n.context, count(*) ORDER BY count(*) DESC LIMIT 50").result_set
    except:
        raw = []
    return {"sources": [{"name": (r[0] or "")[:80], "count": r[1], "enabled": True} for r in raw]}


@app.get("/api/health")
def health():
    """
    Health check with ontology_status + graph discovery.

    Frontend polls this on mount. Tab auto-enables when ontology_status === "ok".
    Manual override via ?view=ontology bypasses check.

    Agents discover graphs via: GET localhost:8000/api/health
    Override with: export TORTOISE_HOST=http://host:port
    """
    falkordb_connected = False
    ontology_status = "error"
    try:
        g = _db.select_graph(DB_GRAPH)
        g.ro_query("RETURN 1")
        falkordb_connected = True
        # Verify ontology-tree query works
        g.ro_query("MATCH (n:Point:Object {context: $ctx}) RETURN count(n) LIMIT 1",
                   {"ctx": CONTEXT_DEFAULT})
        ontology_status = "ok"
    except Exception as e:
        print(f"Health check warning: {e}", flush=True)

    return {
        "status": "ok" if falkordb_connected else "error",
        "ontology_status": ontology_status,
        "falkordb_connected": falkordb_connected,
        "graph": DB_GRAPH,
        "total_nodes": _total_nodes,
        "total_edges": _total_edges,
        "available_graphs": {
            "work": {"host": "localhost", "port": 16379, "graph": "tortoise", "notes": "Main work graph (docker compose up)"},
            "medical": {"host": "localhost", "port": 16379, "graph": "endometriosis_melasma", "notes": "Medical research graph"},
            "test": {"host": "127.0.0.1", "port": 6379, "graph": "tortoise", "notes": "Test/dev instance"},
        },
    }


static_dir = __import__('pathlib').Path(__file__).resolve().parent.parent / "dist"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
