"""
Tortoise Graph Viz — FastAPI backend (FalkorDB live).

Available graphs (in Docker / OrbStack):
  - falkordb (localhost:16379) no auth → graph "tortoise" (WORK GRAPH — default, docker compose up)
  - falkordb (localhost:16379) no auth → graph "endometriosis_melasma" (medical research)
  - falkordb (127.0.0.1:6379) password=falkordb → graph "tortoise" (test/dev, 481 claims)

To switch graphs, set env vars:
  FALKORDB_HOST, FALKORDB_PORT, FALKORDB_USERNAME, FALKORDB_PASSWORD,
  FALKORDB_SSL (1 for rediss-style TLS), FALKORDB_GRAPH
"""
from __future__ import annotations

import random, math, zlib, sys
from collections import Counter
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from falkordb import FalkorDB
import os

# connection.py lives beside main.py; make the import work both as a script
# (python3 server/main.py) and as a module (uvicorn main:app).
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from connection import build_connection_kwargs, graph_name

app = FastAPI(title="Tortoise Graph Viz")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── FalkorDB connection (defaults work on any new machine via docker-compose) ──
# Env contract (see server/connection.py + .env.example):
#   FALKORDB_HOST / FALKORDB_PORT — connection endpoint
#   FALKORDB_USERNAME / FALKORDB_PASSWORD — FalkorDB Cloud + ACL-auth instances (#1079)
#   FALKORDB_SSL=1 — TLS (rediss-style endpoints) (#1079)
#   FALKORDB_GRAPH — graph to select
DB_GRAPH = graph_name()
CONTEXT_DEFAULT = "product-strategy"

# ── Agent discovery protocol ──
# Agents discover this backend at: http://localhost:8000/api/health
# Override with: export TORTOISE_HOST=http://host:port
# The /api/health endpoint returns which graph is active and all available graphs.

kwargs = build_connection_kwargs()
DB_HOST = kwargs["host"]
DB_PORT = kwargs["port"]
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


# ── Dynamic ontology types (#362) ─────────────────────────────────────
# Canonical kind vocabulary per ONTOLOGY v3.4 (docs/ONTOLOGY.md §4):
#   pointKind: statement, decision, vision, strategy, plan, goal, target,
#              observation, hypothesis, assessment (+ pack pointKinds)
#   objectKind: Project, WorkItem, document, tag, user, skill, tool, agent,
#               workflow, agreement, standard, other (+ pack objectKinds)
# Colors follow the app palette (constants.js C / FALLBACK_TYPES in
# useOntologyTypes.ts) so the graph stays visually consistent.
_CANONICAL_KINDS = [
    # pointKind (claims/decisions)
    ("statement",   "Statement",   "#c0caf5", "Claim or assertion in the belief graph", "file-text",  "core"),
    ("decision",    "Decision",    "#7aa2f7", "A recorded decision", "check-square", "core"),
    ("vision",      "Vision",      "#bb9af7", "Aspirational end-state description", "eye", "core"),
    ("strategy",    "Strategy",    "#9ece6a", "Strategic direction or approach", "compass", "core"),
    ("plan",        "Plan",        "#7dcfff", "Planned course of action", "calendar", "core"),
    ("goal",        "Goal",        "#e0af68", "A desired outcome", "target", "core"),
    ("target",      "Target",      "#ff9e64", "Quantified or dated objective", "crosshair", "core"),
    ("observation", "Observation", "#f7768e", "Empirical observation", "search", "core"),
    ("hypothesis",  "Hypothesis",  "#2ac3de", "Testable proposition", "flask", "core"),
    ("assessment",  "Assessment",  "#e0af68", "Agent source evaluation (ONTOLOGY v3.2 §5)", "scale", "core"),
    # objectKind (persistent things)
    ("Project",     "Project",     "#7aa2f7", "A project entity", "folder", "core"),
    ("WorkItem",    "Work Item",   "#9ece6a", "A unit of work", "list", "core"),
    ("document",    "Document",    "#c0caf5", "A document", "file-text", "core"),
    ("tag",         "Tag",         "#565f89", "Free-form label (TAGGED edge target)", "tag", "core"),
    ("user",        "User",        "#7dcfff", "A user", "user", "core"),
    ("skill",       "Skill",       "#bb9af7", "A skill (mechanism provenance)", "award", "core"),
    ("tool",        "Tool",        "#ff9e64", "A tool (mechanism provenance)", "wrench", "core"),
    ("agent",       "Agent",       "#f7768e", "An agent (mechanism provenance)", "bot", "core"),
    ("workflow",    "Workflow",    "#2ac3de", "A workflow (mechanism provenance)", "git-branch", "core"),
    ("agreement",   "Agreement",   "#e0af68", "A formal agreement", "handshake", "core"),
    ("standard",    "Standard",    "#c0caf5", "A standard or norm", "book-open", "core"),
    ("other",       "Other",       "#565f89", "Fallback object kind", "circle", "core"),
    # product-strategy expansion pack (ONTOLOGY §9 pack; default app context)
    ("customerSegment", "Customer Segment", "#7aa2f7", "A customer segment (product-strategy pack)", "users", "product-strategy"),
    ("jobToBeDone",     "Job to Be Done",   "#9ece6a", "A job to be done (product-strategy pack)", "briefcase", "product-strategy"),
    ("valueProposition","Value Proposition","#bb9af7", "A value proposition (product-strategy pack)", "gem", "product-strategy"),
    ("useCase",         "Use Case",         "#e0af68", "A use case (product-strategy pack)", "play-circle", "product-strategy"),
    ("feature",         "Feature",          "#ff9e64", "A feature (product-strategy pack)", "puzzle-piece", "product-strategy"),
    ("userJourney",     "User Journey",     "#7dcfff", "A user journey (product-strategy pack)", "map", "product-strategy"),
    ("requirement",     "Requirement",      "#f7768e", "A requirement (product-strategy pack)", "clipboard-check", "product-strategy"),
    # #531 canonical approval pattern + legacy event kind (ONTOLOGY §5)
    ("humanApproval",   "Human Approval",   "#9ece6a", "Human approval event/decision (#531 pattern)", "thumbs-up", "core"),
    ("event",           "Event",            "#7dcfff", "Legacy event pointKind", "zap", "core"),
]

# Deterministic palette for kinds discovered in the graph but absent from
# the canonical table (tenant-custom / expansion-pack kinds).
_EXTRA_PALETTE = ["#7aa2f7", "#9ece6a", "#bb9af7", "#e0af68", "#ff9e64",
                  "#7dcfff", "#c0caf5", "#f7768e", "#2ac3de", "#db4b4b"]

# Expansion-pack contexts advertised by the endpoint. Only contexts that
# can actually return data are advertised: core + product-strategy (canonical
# table) and tenant (discovered). development/marketing packs exist in the
# repo but have no canonical entries here yet — advertising them would ship
# dead filter tabs (#362 review P2).
_PACK_CONTEXTS = ["product-strategy"]


def _discover_graph_kinds() -> list[str]:
    """Return every kind value actually present in the live graph.

    Union across the six kind-bearing properties (ONTOLOGY §4.7: pointKind,
    subjectKind, objectKind, documentKind, eventKind, sourceKind). This is
    what makes tenant-custom ontologies work: any kind written via the SDK
    or CLI shows up here automatically.
    """
    kinds: set[str] = set()
    for prop in ("pointKind", "objectKind", "subjectKind",
                 "eventKind", "documentKind", "sourceKind"):
        try:
            rows = _g.query(
                f"MATCH (n) WHERE n.{prop} IS NOT NULL "
                f"RETURN DISTINCT n.{prop} LIMIT 500"
            ).result_set
            for row in rows:
                if row[0]:
                    kinds.add(str(row[0]))
        except Exception:
            continue
    return sorted(kinds)


@app.get("/api/ontology-types")
def get_ontology_types(context: str = "all"):
    """Return available kind values with labels, colors and metadata (#362).

    - Canonical core kinds come from ONTOLOGY v3.4 (static table above).
    - Kinds present in the live graph but not canonical are discovered
      dynamically (tenant/expansion-pack kinds) and assigned a stable
      color from the extra palette.
    - ``context`` filters to a pack ("core" / "product-strategy" / ...);
      "all" returns everything.
    """
    canonical = {k[0]: {
        "objectKind": k[0], "label": k[1], "color": k[2],
        "description": k[3], "icon": k[4], "context": k[5],
    } for k in _CANONICAL_KINDS}

    discovered = _discover_graph_kinds()
    types = list(canonical.values())
    for kind in discovered:
        if kind in canonical:
            continue
        # Kind-stable color: hash the kind name so inserting a new kind in
        # the graph never recolors existing kinds (#362 review P3).
        color = _EXTRA_PALETTE[zlib.crc32(kind.encode()) % len(_EXTRA_PALETTE)]
        types.append({
            "objectKind": kind, "label": kind.replace("_", " ").title(),
            "color": color, "description": "Discovered from graph data",
            "icon": None, "context": "tenant",
        })

    if context != "all":
        types = [t for t in types if t["context"] == context]

    return {
        "context": context,
        "contexts": ["core"] + _PACK_CONTEXTS + ["tenant"],
        "types": types,
        "total": len(types),
    }


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


# ── Hosted Platform: Tenant Provisioning (#7713) ────────────────────────
# /api/provision is called by the Supabase Edge Function tenant-provision
# after a new user signs up via OAuth or email/password.
# Authenticated via Supabase service role key.

import sys as _sys
from pathlib import Path as _Path
_TORTOISE_ROOT = _Path(__file__).resolve().parent.parent.parent.parent
if str(_TORTOISE_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_TORTOISE_ROOT))

SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
REGISTRY_GRAPH = os.environ.get("TORTOISE_REGISTRY_GRAPH", "registry")


def _provision_disabled() -> bool:
    """True when the tenant-provision writer must be disabled (#765).

    The Supabase Edge Function stopped calling /api/provision in #770 — it
    provisions via the atomic provision_team RPC (migration 0010) and only
    calls the FastAPI /internal/demo for the data plane. In Supabase
    control-plane mode (TORTOISE_CONTROL_PLANE=supabase or creds
    configured) this endpoint would be a registry WRITE — a violation of
    the zero-registry-writes cutover contract (plan Task 8: graph-viz
    /api/provision is migrated or DISABLED at cutover). Selfhost
    (TORTOISE_CONTROL_PLANE=registry / no creds) keeps the registry path.
    """
    try:
        from tortoise.supabase_control import is_supabase_enabled
        return is_supabase_enabled()
    except Exception:
        # FAIL-CLOSED (review P2, PR #874): if the mode check itself fails,
        # the endpoint stays DISABLED — the zero-registry-writes contract
        # prefers a 410 over an uncertain registry write.
        return True


class ProvisionRequest(BaseModel):
    team_name: str
    user_id: str


class ProvisionResponse(BaseModel):
    team_id: str
    team_name: str
    api_key: str
    graph_name: str


@app.post("/api/provision", response_model=ProvisionResponse)
def provision_tenant(body: ProvisionRequest, authorization: str | None = None):
    """Provision a new team + FalkorDB namespace + API key.

    Called by the Supabase Edge Function tenant-provision after user signup.
    Requires Supabase service role key for authentication.

    #765 (plan Task 8 writer inventory — graph-viz /api/provision):
    DISABLED when the Supabase control plane is active — the Edge Function
    provisions via the provision_team RPC since #770 and never calls this
    endpoint; in Supabase mode a registry write here would violate the
    zero-registry-writes cutover contract. Selfhost (registry control
    plane) keeps the historical behavior.
    """
    if _provision_disabled():
        raise HTTPException(
            410,
            "Provisioning now happens via the provision_team RPC "
            "(Edge Function → Supabase). /api/provision is disabled in "
            "Supabase control-plane mode (#765).",
        )
    # Auth: require service role key
    if not SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(500, "SUPABASE_SERVICE_ROLE_KEY not configured")
    auth_header = authorization or ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = ""
    if token != SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(401, "Unauthorized — service role key required")

    # Validate team name
    team_name = (body.team_name or "").strip()
    if not team_name:
        raise HTTPException(400, "team_name is required")

    # Import TortoiseSDK lazily to avoid circular issues at startup
    from tortoise.sdk import TortoiseSDK

    try:
        # Use registry namespace for team management
        sdk = TortoiseSDK(namespace=REGISTRY_GRAPH)
        result = sdk.team_create(team_name)
        return ProvisionResponse(
            team_id=result["id"],
            team_name=result["name"],
            api_key=result["api_key"],
            graph_name=result["graph_name"],
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        print(f"Provisioning failed for {team_name}: {e}", flush=True)
        raise HTTPException(500, f"Provisioning failed: {e}")


static_dir = _Path(__file__).resolve().parent.parent / "dist"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
