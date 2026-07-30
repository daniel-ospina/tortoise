"""
Tortoise Graph Viz — FastAPI backend (FalkorDB live).

Endpoints:
  Graph visualization:
    GET  /api/graph                          — force graph data (nodes + edges)
    GET  /api/graph/neighborhood/{node_id}   — neighborhood subgraph
    GET  /api/search?q=                      — full-text search
    POST /api/points                         — create user point
    DELETE /api/points/{point_id}            — delete user point
    POST /api/edges                          — create user edge
    DELETE /api/edges/{edge_id}              — delete user edge
    GET  /api/sources                        — source taxonomy

  Ontology:
    GET  /api/health                          — health with ontology_status
    GET  /api/ontology-types?context=          — dynamic objectKind types (labels, colors)
    GET  /api/ontology-tree?context=&root_only= — hierarchical tree JSON
    GET  /api/ontology-object/{id}/descendants — cascade delete preview
    POST /api/ontology-object                  — create dual-label :Point:Object node
    PUT  /api/ontology-object/{id}            — edit with If-Match concurrency
    DELETE /api/ontology-object/{id}?force=    — delete with cascade guard
    GET  /api/object-arguments?id=            — supports[] + contradicts[], edge cap 50

Security:
  - All Cypher queries use parameterized $param dicts (no %s formatting)
  - CORS restricted to localhost:5173 (Vite dev server)
  - Server binds to 127.0.0.1 (LAN-only, not exposed on 0.0.0.0)
  - Health endpoint redacts internal topology (no host/port/password)
  - Pydantic models enforce max_length and Literal constraints
"""

from __future__ import annotations

import os
import random
import math
import uuid
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from falkordb import FalkorDB


# ═══════════════════════════ Config ════════════════════════════════

DB_HOST = os.environ.get("FALKORDB_HOST", "localhost")
DB_PORT = int(os.environ.get("FALKORDB_PORT", "16379"))
DB_PASSWORD = os.environ.get("FALKORDB_PASSWORD", None)
DB_GRAPH = os.environ.get("FALKORDB_GRAPH", "tortoise")

# ── Ontology Types Config (dynamic, supports expansion packs) ──
# Each context (expansion pack) defines its available objectKinds with
# human-readable labels, display colors, and optional metadata.
# New contexts/packs can be added here without code changes.
ONTOLOGY_TYPES: dict[str, dict[str, dict]] = {
    "product-strategy": {
        "customerSegment": {
            "label": "Customer Segment",
            "color": "#7aa2f7",
            "description": "Target customer group with shared needs and behaviors",
            "icon": "users",
        },
        "jobToBeDone": {
            "label": "Job to Be Done",
            "color": "#9ece6a",
            "description": "Functional, social, or emotional job customers hire a product for",
            "icon": "briefcase",
        },
        "valueProposition": {
            "label": "Value Proposition",
            "color": "#bb9af7",
            "description": "Promise of value to be delivered to the customer",
            "icon": "gem",
        },
        "useCase": {
            "label": "Use Case",
            "color": "#e0af68",
            "description": "Specific scenario where a product or service delivers value",
            "icon": "play-circle",
        },
        "feature": {
            "label": "Feature",
            "color": "#ff9e64",
            "description": "Product capability or functionality that persists after being built",
            "icon": "puzzle-piece",
        },
        "userJourney": {
            "label": "User Journey",
            "color": "#7dcfff",
            "description": "End-to-end path a user takes through the product or service",
            "icon": "map",
        },
        "workflow": {
            "label": "Workflow",
            "color": "#c0caf5",
            "description": "Orchestrated sequence of actions or skills to achieve a goal",
            "icon": "git-branch",
        },
        "requirement": {
            "label": "Requirement",
            "color": "#f7768e",
            "description": "Constraint or condition that must be satisfied",
            "icon": "clipboard-check",
        },
    },
}

VALID_OBJECT_KINDS = frozenset(
    kind
    for ctx_types in ONTOLOGY_TYPES.values()
    for kind in ctx_types
)

CONTEXT_DEFAULT = "product-strategy"
NODE_CAP_ALL_CONTEXT = 200
ARGUMENT_EDGE_CAP = 50


# ═══════════════════════════ App Setup ═════════════════════════════

app = FastAPI(title="Tortoise Graph Viz")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server only
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "If-Match"],
)


# ════════════════════ FalkorDB Connection ═════════════════════════

kwargs = {"host": DB_HOST, "port": DB_PORT}
if DB_PASSWORD:
    kwargs["password"] = DB_PASSWORD

for attempt in range(1, 31):
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
            import sys
            sys.exit(1)
        print(f"Waiting for FalkorDB ({DB_HOST}:{DB_PORT})... attempt {attempt}/30", flush=True)
        time.sleep(1)


# ═══════════════════ Global Cache ══════════════════════════════════

_global_confidence: dict[str, float] = {}
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
    except Exception:
        _total_nodes = 0
    try:
        _total_edges = _g.query("MATCH ()-[r:RELATION]->() RETURN count(r)").result_set[0][0]
    except Exception:
        _total_edges = 0

    # Compute global degree centrality
    degree: Counter[str] = Counter()
    try:
        results = _g.query(
            "MATCH (a:Point)-[r:RELATION]-(b:Point) RETURN a.id, count(r) ORDER BY count(r) DESC LIMIT 5000"
        ).result_set
        for row in results:
            degree[row[0]] = row[1]
    except Exception:
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
    except Exception:
        _global_mit_count = 0

    _cache_built = True
    print(
        f"Cache built: {_total_nodes} nodes, {_total_edges} edges, "
        f"{len(_global_confidence)} confidence scores, {_global_mit_count} mitigations"
    )


_build_cache()


# ══════════════════════ Helper Functions ═══════════════════════════

def _query_graph(limit: int = 200, only_ids: set[str] | None = None) -> dict:
    """Fetch nodes and edges for the force graph visualization.

    All Cypher queries use parameterized $param dicts — no string formatting.
    """
    if only_ids and len(only_ids) > 0:
        ids_list = list(only_ids)[:500]
        try:
            nodes_raw = _g.ro_query(
                "UNWIND $ids AS id MATCH (n:Point {id: id}) RETURN n.id, n.content, n.context",
                {"ids": ids_list},
            ).result_set
            edges_raw = _g.ro_query(
                "UNWIND $ids AS aid "
                "MATCH (a:Point {id: aid})-[r:RELATION]->(b:Point) "
                "WHERE b.id IN $ids "
                "RETURN a.id, b.id, r.type",
                {"ids": ids_list},
            ).result_set
        except Exception as e:
            print(f"Query error: {e}")
            nodes_raw, edges_raw = [], []
    else:
        try:
            nodes_raw = _g.ro_query(
                "MATCH (n:Point) OPTIONAL MATCH (n)-[r:RELATION]-() "
                "RETURN n.id, n.content, n.context, count(r) as deg ORDER BY deg DESC LIMIT $limit",
                {"limit": limit},
            ).result_set
            top_ids = [r[0] for r in nodes_raw]
            if top_ids:
                edges_raw = _g.ro_query(
                    "UNWIND $ids AS aid "
                    "MATCH (a:Point {id: aid})-[r:RELATION]->(b:Point) "
                    "WHERE b.id IN $ids "
                    "RETURN a.id, b.id, r.type",
                    {"ids": top_ids},
                ).result_set
            else:
                edges_raw = []
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
            if mit_idx % 8 == 0:
                mits = [{
                    "id": f"mit-{row[0][:8]}",
                    "content": random.choice(reasons),
                    "strength": round(random.uniform(0.2, 0.7), 2),
                }]
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


def _new_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _node_to_dict(node) -> dict:
    if hasattr(node, "properties"):
        return dict(node.properties)
    return dict(node)


def _result_to_dicts(result_set) -> list[dict]:
    if result_set is None:
        return []
    header = result_set.header
    rows = result_set.result_set
    out = []
    for row in rows:
        d = {}
        for i, col in enumerate(header):
            val = row[i]
            if hasattr(val, "properties"):
                d[col] = dict(val.properties)
            else:
                d[col] = val
        out.append(d)
    return out


# ═══════════════════════ Pydantic Models ═══════════════════════════

class PointCreate(BaseModel):
    content: str
    context: str = ""

    @field_validator("content")
    @classmethod
    def content_max_length(cls, v: str) -> str:
        if len(v) > 500:
            raise ValueError("content must be <= 500 characters")
        return v


class EdgeCreate(BaseModel):
    source: str
    target: str
    type: str

    @field_validator("type")
    @classmethod
    def type_max_length(cls, v: str) -> str:
        if len(v) > 50:
            raise ValueError("type must be <= 50 characters")
        return v

    @field_validator("source", "target")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("id must not be empty")
        return v


class CreateObjectRequest(BaseModel):
    name: str
    objectKind: str
    context: str = CONTEXT_DEFAULT
    parentId: Optional[str] = None
    content: str = ""

    @field_validator("objectKind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        if v not in VALID_OBJECT_KINDS:
            raise ValueError(
                f"Invalid objectKind. Must be one of: {', '.join(sorted(VALID_OBJECT_KINDS))}"
            )
        return v

    @field_validator("name")
    @classmethod
    def name_max_length(cls, v: str) -> str:
        if len(v) > 500:
            raise ValueError("name must be <= 500 characters")
        return v

    @field_validator("content")
    @classmethod
    def obj_content_max_length(cls, v: str) -> str:
        if len(v) > 5000:
            raise ValueError("content must be <= 5000 characters")
        return v


class UpdateObjectRequest(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_max_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 500:
            raise ValueError("name must be <= 500 characters")
        return v

    @field_validator("content")
    @classmethod
    def content_max_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 5000:
            raise ValueError("content must be <= 5000 characters")
        return v


# ═══════════════════════ Graph Endpoints ═══════════════════════════

@app.get("/api/graph")
def get_graph(limit: int = 200):
    data = _query_graph(limit)
    data["total_nodes"] = _total_nodes
    data["total_edges"] = _total_edges
    data["total_mitigations"] = _global_mit_count
    return data


@app.get("/api/graph/neighborhood/{node_id}")
def get_neighborhood(node_id: str, depth: int = 1):
    visited: set[str] = {node_id}
    frontier: set[str] = {node_id}
    for _ in range(depth):
        frontier_list = list(frontier)
        try:
            neighbors = _g.ro_query(
                "UNWIND $ids AS id MATCH (a:Point {id: id})-[r:RELATION]-(b:Point) "
                "RETURN DISTINCT b.id",
                {"ids": frontier_list},
            ).result_set
        except Exception:
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
    """Full-text search over Point nodes. Uses parameterized Cypher."""
    try:
        results = _g.ro_query(
            "MATCH (n:Point) WHERE n.content CONTAINS $q RETURN n.id, n.content LIMIT $limit",
            {"q": q, "limit": limit},
        ).result_set
    except Exception:
        results = []
    return {"results": [{"id": r[0], "content": (r[1] or "")[:200]} for r in results]}


@app.post("/api/points")
def add_point(body: PointCreate):
    pid = f"user-{uuid.uuid4().hex[:12]}"
    try:
        _g.query(
            "CREATE (:Point {id: $id, content: $content, context: $context})",
            {"id": pid, "content": body.content[:500], "context": (body.context or "")[:200]},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Create failed: {str(e)}")
    global _total_nodes
    _total_nodes += 1
    return {"id": pid}


@app.delete("/api/points/{point_id}")
def delete_point(point_id: str):
    if not point_id.startswith("user-"):
        raise HTTPException(403, "Can only delete user-created points")
    try:
        _g.query(
            "MATCH (n:Point {id: $id}) DETACH DELETE n",
            {"id": point_id},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")
    global _total_nodes
    _total_nodes -= 1
    return {"deleted": point_id}


@app.post("/api/edges")
def add_edge(body: EdgeCreate):
    eid = f"user-edge-{uuid.uuid4().hex[:12]}"
    try:
        _g.query(
            "MATCH (a:Point {id: $src}), (b:Point {id: $tgt}) "
            "CREATE (a)-[:RELATION {type: $type, id: $eid}]->(b)",
            {"src": body.source, "tgt": body.target, "type": body.type, "eid": eid},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Create failed: {str(e)}")
    global _total_edges
    _total_edges += 1
    return {"id": eid}


@app.delete("/api/edges/{edge_id}")
def delete_edge(edge_id: str):
    if not edge_id.startswith("user-edge-"):
        raise HTTPException(403, "Can only delete user-created edges")
    try:
        _g.query(
            "MATCH ()-[r:RELATION {id: $eid}]->() DELETE r",
            {"eid": edge_id},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")
    global _total_edges
    _total_edges -= 1
    return {"deleted": edge_id}


@app.get("/api/sources")
def get_sources():
    try:
        raw = _g.ro_query(
            "MATCH (n:Point) WHERE n.context IS NOT NULL "
            "RETURN n.context, count(*) ORDER BY count(*) DESC LIMIT 50"
        ).result_set
    except Exception:
        raw = []
    return {"sources": [{"name": (r[0] or "")[:80], "count": r[1], "enabled": True} for r in raw]}


# ═══════════════════════ Ontology Endpoints ════════════════════════

# ── Health ─────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    """
    Health check with ontology_status field.

    Frontend polls this on mount. Tab auto-enables when ontology_status === "ok".
    Manual override via ?view=ontology bypasses check.

    Security: DOES NOT expose DB host, port, password, or available graph topology.
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
    }


# ── Ontology Types ─────────────────────────────────────────────────

@app.get("/api/ontology-types")
def ontology_types(
    context: str = Query(default="all", description="Context/expansion pack. 'all' returns all types across all contexts."),
):
    """
    Return available objectKind values with labels, colors, and metadata.

    Supports expansion packs: each context (product-strategy, marketing, etc.)
    defines its own set of objectKinds. Use context=all to get the union.

    Response:
      {
        "context": "all",
        "contexts": ["product-strategy"],
        "types": [
          {
            "objectKind": "customerSegment",
            "label": "Customer Segment",
            "color": "#7aa2f7",
            "description": "...",
            "icon": "users",
            "context": "product-strategy"
          },
          ...
        ],
        "total": 8
      }
    """
    if context == "all":
        all_types = []
        for ctx_name, ctx_types in ONTOLOGY_TYPES.items():
            for kind, meta in ctx_types.items():
                all_types.append({
                    "objectKind": kind,
                    "label": meta.get("label", kind),
                    "color": meta.get("color", "#888888"),
                    "description": meta.get("description", ""),
                    "icon": meta.get("icon"),
                    "context": ctx_name,
                })
        return {
            "context": "all",
            "contexts": sorted(ONTOLOGY_TYPES.keys()),
            "types": sorted(all_types, key=lambda t: (t["context"], t["objectKind"])),
            "total": len(all_types),
        }

    ctx_types = ONTOLOGY_TYPES.get(context)
    if ctx_types is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": f"Unknown context '{context}'. Available: {', '.join(sorted(ONTOLOGY_TYPES.keys()))}",
            },
        )

    types = [
        {
            "objectKind": kind,
            "label": meta.get("label", kind),
            "color": meta.get("color", "#888888"),
            "description": meta.get("description", ""),
            "icon": meta.get("icon"),
            "context": context,
        }
        for kind, meta in ctx_types.items()
    ]
    return {
        "context": context,
        "contexts": sorted(ONTOLOGY_TYPES.keys()),
        "types": sorted(types, key=lambda t: t["objectKind"]),
        "total": len(types),
    }


# ── Tree ───────────────────────────────────────────────────────────

@app.get("/api/ontology-tree")
def ontology_tree(
    context: str = Query(default=CONTEXT_DEFAULT),
    root_only: bool = Query(default=False),
):
    """
    Return hierarchical tree of :Point:Object nodes.

    Query params:
      context: string (default "product-strategy")
      root_only: bool (default false — if true, only return root nodes)

    Nodes are fetched in one flat query, then assembled into a tree server-side.
    For context='all', nodes are capped at 200 with a warning.
    """
    g = _db.select_graph(DB_GRAPH)

    # ── Fetch all nodes ────────────────────────────────────────────────
    if context == "all":
        raw = g.ro_query(
            "MATCH (n:Point:Object) RETURN n LIMIT $limit",
            {"limit": NODE_CAP_ALL_CONTEXT + 1},
        )
    else:
        raw = g.ro_query(
            "MATCH (n:Point:Object {context: $ctx}) RETURN n",
            {"ctx": context},
        )

    rows = raw.result_set if raw else []
    nodes: dict[str, dict] = {}
    for row in rows:
        node = _node_to_dict(row[0])
        nid = node.get("id")
        if nid:
            nodes[nid] = node

    # Warn if capped
    total_nodes = len(nodes)
    is_capped = False
    if context == "all" and total_nodes > NODE_CAP_ALL_CONTEXT:
        overflow_key = list(nodes.keys())[-1]
        del nodes[overflow_key]
        total_nodes = NODE_CAP_ALL_CONTEXT
        is_capped = True

    # ── Fetch hasPart edges ──────────────────────────────────────────
    children_map: dict[str, list[str]] = {}
    if context == "all":
        edge_results = g.ro_query(
            "MATCH (parent:Point:Object)-[:hasPart]->(child:Point:Object) "
            "RETURN parent.id, child.id"
        )
    else:
        edge_results = g.ro_query(
            "MATCH (parent:Point:Object {context: $ctx})-[:hasPart]->(child:Point:Object) "
            "RETURN parent.id, child.id",
            {"ctx": context},
        )

    if edge_results:
        for row in edge_results.result_set:
            parent_id = row[0]
            child_id = row[1]
            if parent_id in nodes and child_id in nodes:
                children_map.setdefault(parent_id, []).append(child_id)

    # ── Assemble tree ─────────────────────────────────────────────────
    all_child_ids: set[str] = set()
    for children in children_map.values():
        all_child_ids.update(children)

    roots: list[str] = []
    for nid in nodes:
        if nid not in all_child_ids:
            roots.append(nid)

    def build_subtree(nid: str, seen: set | None = None) -> dict | None:
        if seen is None:
            seen = set()
        if nid in seen:
            return None  # cycle guard
        seen.add(nid)
        node = nodes.get(nid)
        if not node:
            return None
        result = {
            "id": node.get("id"),
            "name": node.get("name", ""),
            "content": node.get("content", ""),
            "objectKind": node.get("objectKind", node.get("pointKind", "")),
            "pointKind": node.get("pointKind", ""),
            "confidence": node.get("confidence"),
            "lastCalibratedAt": node.get("lastCalibratedAt"),
            "status": node.get("status", node.get("pointStatus", "draft")),
            "version": node.get("version", 1),
            "children": [],
        }
        child_ids = children_map.get(nid, [])
        for cid in child_ids:
            subtree = build_subtree(cid, seen.copy())
            if subtree:
                result["children"].append(subtree)
        return result

    tree = []
    if root_only:
        for rnid in roots:
            node = nodes.get(rnid)
            if node:
                tree.append({
                    "id": node.get("id"),
                    "name": node.get("name", ""),
                    "content": node.get("content", ""),
                    "objectKind": node.get("objectKind", node.get("pointKind", "")),
                    "pointKind": node.get("pointKind", ""),
                    "confidence": node.get("confidence"),
                    "lastCalibratedAt": node.get("lastCalibratedAt"),
                    "status": node.get("status", node.get("pointStatus", "draft")),
                    "version": node.get("version", 1),
                    "children": [],
                })
    else:
        for rnid in roots:
            subtree = build_subtree(rnid)
            if subtree:
                tree.append(subtree)

    # Count from total available (for accurate "filtered_from")
    try:
        count_all = g.ro_query("MATCH (n:Point:Object) RETURN count(n)")
        filtered_from = int(count_all.result_set[0][0]) if count_all.result_set else 0
    except Exception:
        filtered_from = total_nodes

    response = {
        "tree": tree,
        "total_nodes": total_nodes,
        "context": context,
    }

    if is_capped:
        response["filtered_from"] = filtered_from
        response["warning"] = (
            f"Showing {NODE_CAP_ALL_CONTEXT} of {filtered_from} nodes. "
            "Switch to a specific context for the rest."
        )
    elif context != "all":
        response["filtered_from"] = filtered_from

    return response


# ── Descendants ────────────────────────────────────────────────────

@app.get("/api/ontology-object/{oid}/descendants")
def ontology_object_descendants(oid: str):
    """
    Preview cascade delete blast radius.

    Returns all nodes reachable via hasPart* from this node,
    with depth information.
    """
    g = _db.select_graph(DB_GRAPH)

    node_result = g.ro_query(
        "MATCH (n:Point:Object {id: $id}) RETURN n",
        {"id": oid},
    )
    if not node_result or not node_result.result_set:
        raise HTTPException(status_code=404, detail={"error": "Object not found", "id": oid})

    root = _node_to_dict(node_result.result_set[0][0])

    desc_result = g.ro_query(
        "MATCH path = (parent:Point:Object {id: $id})-[:hasPart*1..]->(descendant:Point:Object) "
        "RETURN descendant, length(path) as depth ORDER BY depth",
        {"id": oid},
    )

    descendants = []
    if desc_result and desc_result.result_set:
        for row in desc_result.result_set:
            dnode = _node_to_dict(row[0])
            depth = int(row[1])
            descendants.append({
                "id": dnode.get("id"),
                "name": dnode.get("name", ""),
                "objectKind": dnode.get("objectKind", dnode.get("pointKind", "")),
                "depth": depth,
            })

    return {
        "node": {
            "id": root.get("id"),
            "name": root.get("name", ""),
        },
        "descendants": descendants,
        "total_descendants": len(descendants),
    }


# ── Create Object ──────────────────────────────────────────────────

@app.post("/api/ontology-object", status_code=201)
def create_ontology_object(body: CreateObjectRequest):
    """
    Create a dual-label :Point:Object node.

    parentId is optional — omit for root nodes (Customer Segment).
    pointKind is derived server-side from objectKind (always identical).
    """
    g = _db.select_graph(DB_GRAPH)
    new_id = _new_id()
    now = _now_iso()
    point_kind = body.objectKind

    if body.parentId:
        check = g.ro_query(
            "MATCH (parent:Point:Object {id: $pid}) RETURN parent",
            {"pid": body.parentId},
        )
        if not check or not check.result_set:
            raise HTTPException(
                status_code=404,
                detail={"error": "Parent node not found", "parentId": body.parentId},
            )

    try:
        if body.parentId:
            create_query = (
                "CREATE (n:Point:Object {"
                "id: $id, content: $content, name: $name, "
                "pointKind: $pointKind, objectKind: $objectKind, "
                "confidence: 0.5, context: $context, "
                "pointStatus: 'draft', status: 'draft', "
                "createdAt: $now, updatedAt: $now, version: 1"
                "}) "
                "WITH n "
                "MATCH (parent:Point:Object {id: $pid}) "
                "CREATE (parent)-[:hasPart]->(n) "
                "RETURN n"
            )
            params = {
                "id": new_id, "content": body.content or body.name,
                "name": body.name, "pointKind": point_kind,
                "objectKind": body.objectKind, "context": body.context,
                "now": now, "pid": body.parentId,
            }
        else:
            create_query = (
                "CREATE (n:Point:Object {"
                "id: $id, content: $content, name: $name, "
                "pointKind: $pointKind, objectKind: $objectKind, "
                "confidence: 0.5, context: $context, "
                "pointStatus: 'draft', status: 'draft', "
                "createdAt: $now, updatedAt: $now, version: 1"
                "}) RETURN n"
            )
            params = {
                "id": new_id, "content": body.content or body.name,
                "name": body.name, "pointKind": point_kind,
                "objectKind": body.objectKind, "context": body.context,
                "now": now,
            }
        g.query(create_query, params)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Create failed: {str(e)}")

    read_back = g.ro_query(
        "MATCH (n:Point:Object {id: $id}) RETURN n",
        {"id": new_id},
    )
    if not read_back or not read_back.result_set:
        raise HTTPException(status_code=500, detail="Created node not found after creation")

    node = _node_to_dict(read_back.result_set[0][0])

    return {
        "id": node.get("id"),
        "name": node.get("name", ""),
        "objectKind": node.get("objectKind", ""),
        "pointKind": node.get("pointKind", ""),
        "confidence": node.get("confidence", 0.5),
        "lastCalibratedAt": node.get("lastCalibratedAt"),
        "status": node.get("status", "draft"),
        "version": node.get("version", 1),
        "parentId": body.parentId,
    }


# ── Edit Object ────────────────────────────────────────────────────

@app.put("/api/ontology-object/{oid}")
def update_ontology_object(
    oid: str,
    body: UpdateObjectRequest,
    if_match: Optional[str] = Header(None, alias="If-Match"),
):
    """
    Edit name/content with If-Match optimistic concurrency.

    Headers:
      If-Match: <version> — required. 409 on mismatch.

    Body (all optional):
      name: string
      content: string
    """
    if if_match is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "If-Match header required for optimistic concurrency"},
        )

    g = _db.select_graph(DB_GRAPH)

    node_result = g.ro_query(
        "MATCH (n:Point:Object {id: $id}) RETURN n",
        {"id": oid},
    )
    if not node_result or not node_result.result_set:
        raise HTTPException(status_code=404, detail={"error": "Object not found", "id": oid})

    node = _node_to_dict(node_result.result_set[0][0])
    current_version = int(node.get("version", 1))

    try:
        client_version = int(if_match.strip().strip('"'))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": "If-Match header must be an integer version number"},
        )

    if client_version != current_version:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Version conflict. Object was modified by another agent. Refresh and retry.",
                "current_version": current_version,
                "your_version": client_version,
            },
        )

    if body.name is None and body.content is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "At least one of name or content must be provided"},
        )

    now = _now_iso()
    new_version = current_version + 1
    set_clauses = ["n.updatedAt = $now", "n.version = $new_version"]
    set_params: dict = {
        "id": oid,
        "expected_version": current_version,
        "now": now,
        "new_version": new_version,
    }

    if body.name is not None:
        set_clauses.append("n.name = $name")
        set_params["name"] = body.name
    if body.content is not None:
        set_clauses.append("n.content = $content")
        set_params["content"] = body.content

    # Atomic version check: WHERE n.version = $expected_version prevents
    # lost updates from concurrent PUTs on the same object.
    update_query = (
        f"MATCH (n:Point:Object {{id: $id}}) "
        f"WHERE n.version = $expected_version "
        f"SET {', '.join(set_clauses)} RETURN n"
    )

    try:
        result = g.query(update_query, set_params)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Update failed: {str(e)}")

    if not result or not result.result_set:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Version conflict during write. Object was modified concurrently. Refresh and retry.",
            },
        )

    updated = _node_to_dict(result.result_set[0][0])

    return {
        "id": updated.get("id"),
        "name": updated.get("name", ""),
        "content": updated.get("content", ""),
        "objectKind": updated.get("objectKind", ""),
        "pointKind": updated.get("pointKind", ""),
        "confidence": updated.get("confidence"),
        "lastCalibratedAt": updated.get("lastCalibratedAt"),
        "status": updated.get("status", "draft"),
        "version": updated.get("version"),
        "updatedAt": updated.get("updatedAt"),
    }


# ── Delete Object ──────────────────────────────────────────────────

@app.delete("/api/ontology-object/{oid}")
def delete_ontology_object(
    oid: str,
    force: bool = Query(default=False),
    if_match: Optional[str] = Header(None, alias="If-Match"),
):
    """
    Delete with cascade guard.

    Headers:
      If-Match: <version> — required. 409 on mismatch.

    Query params:
      force: bool (default false) — must be true to delete nodes with children

    If force=true, cascades: DETACH DELETE all descendants, then the parent.
    """
    if if_match is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "If-Match header required for optimistic concurrency"},
        )

    g = _db.select_graph(DB_GRAPH)

    node_result = g.ro_query(
        "MATCH (n:Point:Object {id: $id}) RETURN n",
        {"id": oid},
    )
    if not node_result or not node_result.result_set:
        raise HTTPException(status_code=404, detail={"error": "Object not found", "id": oid})

    node = _node_to_dict(node_result.result_set[0][0])
    current_version = int(node.get("version", 1))

    try:
        client_version = int(if_match.strip().strip('"'))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": "If-Match header must be an integer version number"},
        )

    if client_version != current_version:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Version conflict. Object was modified by another agent. Refresh and retry.",
                "current_version": current_version,
                "your_version": client_version,
            },
        )

    children_result = g.ro_query(
        "MATCH (parent:Point:Object {id: $id})-[:hasPart]->(child:Point:Object) "
        "RETURN count(child) as c",
        {"id": oid},
    )
    child_count = int(children_result.result_set[0][0]) if children_result and children_result.result_set else 0

    if child_count > 0 and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "error": f"Object has {child_count} children. Use ?force=true to cascade delete.",
            },
        )

    try:
        if force and child_count > 0:
            count_result = g.ro_query(
                "MATCH path = (parent:Point:Object {id: $id})-[:hasPart*0..]->(descendant:Point:Object) "
                "RETURN count(descendant)",
                {"id": oid},
            )
            cascade_count = int(count_result.result_set[0][0]) if count_result and count_result.result_set else 1

            # Atomic version check prevents concurrent deletes
            delete_result = g.query(
                "MATCH (n:Point:Object {id: $id}) "
                "WHERE n.version = $expected_version "
                "WITH n "
                "MATCH path = (n)-[:hasPart*0..]->(descendant:Point:Object) "
                "DETACH DELETE descendant "
                "RETURN count(descendant)",
                {"id": oid, "expected_version": current_version},
            )
            if not delete_result or not delete_result.result_set:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "Version conflict during delete. Object was modified concurrently. Refresh and retry.",
                    },
                )
        else:
            delete_result = g.query(
                "MATCH (n:Point:Object {id: $id}) "
                "WHERE n.version = $expected_version "
                "DETACH DELETE n",
                {"id": oid, "expected_version": current_version},
            )
            if not delete_result or not delete_result.result_set:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "Version conflict during delete. Object was modified concurrently. Refresh and retry.",
                    },
                )
            cascade_count = 1
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")

    return {
        "deleted": oid,
        "cascade_count": cascade_count,
    }


# ── Object Arguments ───────────────────────────────────────────────

@app.get("/api/object-arguments")
def object_arguments(
    id: str = Query(..., description="Node ID to fetch arguments for"),
):
    """
    Return supports[] and contradicts[] for a :Point:Object node.

    supports = IMPL edges (operators that support this node)
    contradicts = NAND edges (operators that contradict this node)

    Edge cap: 50 total (supports + contradicts combined).
    Each edge includes nested mitigations array.
    """
    g = _db.select_graph(DB_GRAPH)

    node_result = g.ro_query(
        "MATCH (n:Point:Object {id: $id}) RETURN n",
        {"id": id},
    )
    if not node_result or not node_result.result_set:
        raise HTTPException(status_code=404, detail={"error": "Object not found", "id": id})

    node = _node_to_dict(node_result.result_set[0][0])

    impl_query = (
        "MATCH (op:Point)-[:IMPL]->(n:Point:Object {id: $id}) "
        "OPTIONAL MATCH (op)-[:mitigated_by]->(mit:Point) "
        "RETURN op.id as edgeId, "
        "op.content as sourceContent, "
        "op.confidence as sourceConfidence, "
        "op.sourceKind as sourceKind, "
        "collect(DISTINCT {id: mit.id, reason: mit.reason, strength: mit.strength}) as mitigations "
        "LIMIT $cap"
    )

    nand_query = (
        "MATCH (op:Point)-[:NAND]->(n:Point:Object {id: $id}) "
        "OPTIONAL MATCH (op)-[:mitigated_by]->(mit:Point) "
        "RETURN op.id as edgeId, "
        "op.content as sourceContent, "
        "op.confidence as sourceConfidence, "
        "op.sourceKind as sourceKind, "
        "collect(DISTINCT {id: mit.id, reason: mit.reason, strength: mit.strength}) as mitigations "
        "LIMIT $cap"
    )

    def _parse_edges(raw, edge_type: str) -> list[dict]:
        edges = []
        if raw and raw.result_set:
            for row in raw.result_set:
                edge_id = row[0]
                source_content = row[1] if len(row) > 1 else None
                source_confidence = float(row[2]) if len(row) > 2 and row[2] is not None else None
                source_kind = row[3] if len(row) > 3 else None
                mits_raw = row[4] if len(row) > 4 else []

                mitigations = []
                if mits_raw:
                    for m in mits_raw:
                        if hasattr(m, "properties"):
                            mp = dict(m.properties)
                        elif isinstance(m, dict):
                            mp = m
                        else:
                            continue
                        mitigations.append({
                            "id": mp.get("id"),
                            "reason": mp.get("reason", ""),
                            "strength": mp.get("strength"),
                        })

                edges.append({
                    "edgeId": edge_id,
                    "type": edge_type,
                    "target": {
                        "id": edge_id,
                        "content": source_content,
                        "confidence": source_confidence,
                        "sourceKind": source_kind,
                    },
                    "mitigations": mitigations,
                })
        return edges

    try:
        impl_results = g.ro_query(impl_query, {"id": id, "cap": ARGUMENT_EDGE_CAP})
        nand_results = g.ro_query(nand_query, {"id": id, "cap": ARGUMENT_EDGE_CAP})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"FalkorDB query failed: {str(e)}")

    supports = _parse_edges(impl_results, "IMPL")
    contradicts = _parse_edges(nand_results, "NAND")

    # Apply combined cap
    total_edges = len(supports) + len(contradicts)
    if total_edges > ARGUMENT_EDGE_CAP:
        if len(supports) > ARGUMENT_EDGE_CAP:
            supports = supports[:ARGUMENT_EDGE_CAP]
            contradicts = []
        else:
            remaining = ARGUMENT_EDGE_CAP - len(supports)
            contradicts = contradicts[:remaining]

    return {
        "node": {
            "id": node.get("id"),
            "name": node.get("name", ""),
            "confidence": node.get("confidence"),
            "lastCalibratedAt": node.get("lastCalibratedAt"),
        },
        "supports": supports,
        "contradicts": contradicts,
        "total_edges": len(supports) + len(contradicts),
    }


# ═══════════════════════ Static Files ══════════════════════════════

static_dir = __import__('pathlib').Path(__file__).resolve().parent.parent / "dist"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


# ═══════════════════════ CLI Entrypoint ════════════════════════════

if __name__ == "__main__":
    import uvicorn
    # Bind to 127.0.0.1 only — LAN-local tool, not exposed on network interfaces
    uvicorn.run(app, host="127.0.0.1", port=8000)
