"""FastAPI app for Tortoise Hosted Platform.

Provides the internal /provision endpoint called by the Supabase
tenant-provision Edge Function, and will be extended with the full
multi-tenant REST API (issue #7717).

See: docs/epics/2026-08-03-tortoise-hosted-platform/04-plan.md §5, §6.1
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from tortoise.auth import hash_api_key
from tortoise.sdk import TortoiseSDK

_logger = logging.getLogger(__name__)

app = FastAPI(title="Tortoise Hosted API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://premiselabs.co", "https://app.premiselabs.co"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Internal auth key for Edge Function → API communication
_INTERNAL_KEY = os.environ.get("FASTAPI_INTERNAL_KEY", "")


def _check_internal(request: Request) -> None:
    """Verify internal auth — only Edge Functions call this."""
    if not _INTERNAL_KEY:
        return  # Dev mode — no auth
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != _INTERNAL_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/internal/provision")
async def provision_tenant(request: Request):
    """Provision a new team: create Team node + FalkorDB namespace + store API key.

    Called by the tenant-provision Supabase Edge Function on user signup.
    """
    _check_internal(request)

    body = await request.json()
    team_id = body.get("team_id")
    team_name = body.get("team_name")
    api_key_hash = body.get("api_key_hash")
    created_by = body.get("created_by")

    if not all([team_id, team_name, api_key_hash, created_by]):
        raise HTTPException(status_code=400, detail="Missing required fields")

    sdk = TortoiseSDK(namespace="registry")
    now = datetime.now(timezone.utc).isoformat()

    # Create Team node in registry graph
    sdk._get_proj().g.query(
        """
        CREATE (t:Team {
            id: $id, name: $name, tier: 'free',
            created_at: $now, backup_enabled: false,
            max_users: 1, max_teams: 1, max_graphs: 1
        })
        """,
        params={"id": team_id, "name": team_name, "now": now},
    )

    # Create APIKey node
    sdk._get_proj().g.query(
        """
        CREATE (k:APIKey {
            id: $id, team_id: $team_id, key_hash: $hash,
            key_prefix: $prefix, created_by: $created_by,
            created_at: $now
        })
        """,
        params={
            "id": _ulid(),
            "team_id": team_id,
            "hash": api_key_hash,
            "prefix": team_id[:8],
            "created_by": created_by,
            "now": now,
        },
    )

    # Provision FalkorDB namespace for the team
    graph_name = f"team_{team_name}"
    try:
        team_graph = sdk.db.select_graph(graph_name)
        team_graph.query(
            "CREATE (:TeamMeta {name: $name, created: $now})",
            params={"name": team_name, "now": now},
        )
    except Exception:
        # Roll back registry entry on namespace failure
        sdk._get_proj().g.query("MATCH (t:Team {id: $id}) DETACH DELETE t", params={"id": team_id})
        sdk._get_proj().g.query(
            "MATCH (k:APIKey {team_id: $id}) DETACH DELETE k", params={"id": team_id}
        )
        raise HTTPException(status_code=500, detail="Namespace provisioning failed")

    # Create Membership (creator is Owner)
    sdk._get_proj().g.query(
        """
        CREATE (m:Membership {
            id: $id, user_id: $user_id, team_id: $team_id,
            role: 'owner', joined_at: $now
        })
        """,
        params={
            "id": _ulid(),
            "user_id": created_by,
            "team_id": team_id,
            "now": now,
        },
    )

    return {"status": "provisioned", "team_id": team_id, "graph_name": graph_name}


def _ulid() -> str:
    """Generate a simple ULID-like identifier."""
    import uuid
    return uuid.uuid4().hex[:26]


@app.get("/health")
async def health():
    return {"status": "ok"}

# ── Phase 1a: Core Endpoints ──────────────────────────────────────


# ── Auth Dependency ────────────────────────────────────────────────

from fastapi import Depends, HTTPException, Request
from tortoise.auth import hash_api_key
from tortoise.sdk import TortoiseSDK

SKIP_AUTH = {"/health", "/docs", "/openapi.json"}

async def get_current_team(request: Request) -> dict:
    if request.url.path in SKIP_AUTH or request.url.path.startswith("/internal"):
        return {"team_id": None, "tier": "free", "key_id": None}
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = auth[7:]
    if not token.startswith("tt_"):
        raise HTTPException(status_code=401, detail="Invalid API key format")
    try:
        sdk = TortoiseSDK(namespace="registry")
        key_result = sdk._get_proj().g.query(
            "MATCH (k:APIKey {key_hash: $hash}) WHERE k.revoked_at IS NULL RETURN k.team_id, k.id",
            params={"hash": hash_api_key(token)},
        )
        if not key_result.result_set:
            raise HTTPException(status_code=401, detail="Invalid API key")
        team_id, key_id = key_result.result_set[0]
        team = sdk._get_proj().g.query(
            "MATCH (t:Team {id: $id}) RETURN t.tier, t.max_users, t.max_graphs, t.max_teams",
            params={"id": team_id},
        )
        tier, mu, mg, mt = team.result_set[0] if team.result_set else ("free", 1, 1, 1)
        request.state.team_id = team_id
        request.state.tier = tier or "free"
        return {"team_id": team_id, "key_id": key_id, "tier": tier or "free",
                "max_users": mu or 1, "max_graphs": mg or 1, "max_teams": mt or 1}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Auth error")




# ── Pydantic Models ───────────────────────────────────────────────

from pydantic import BaseModel, Field, field_validator


class CreatePointRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    kind: str = Field(default="statement")
    context: str | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, v: str) -> str:
        allowed = {"statement", "decision", "evidence", "observation", "hypothesis"}
        if v not in allowed:
            raise ValueError(f"kind must be one of {allowed}")
        return v


class PointResponse(BaseModel):
    id: str
    content: str
    kind: str
    context: str | None = None
    created_at: str | None = None


class TeamInfoResponse(BaseModel):
    team_id: str
    tier: str
    max_users: int
    max_graphs: int | None
    max_teams: int | None
    point_count: int = 0


class CreateKeyResponse(BaseModel):
    id: str
    key: str  # plaintext — shown once
    key_prefix: str
    created_at: str


class KeyListResponse(BaseModel):
    id: str
    key_prefix: str
    created_at: str | None
    last_used_at: str | None
    revoked_at: str | None


class ErrorResponse(BaseModel):
    error: dict


# ── Endpoints ─────────────────────────────────────────────────────

@app.post("/v1/points", response_model=PointResponse)
async def create_point(body: CreatePointRequest, team: dict = Depends(get_current_team)):
    """Create a Point in the team's graph."""
    sdk = TortoiseSDK(namespace=team["team_id"])
    result = sdk.create_point(
        content=body.content,
        kind=body.kind,
        context=body.context,
        tags=body.tags,
    )
    return {
        "id": result["id"],
        "content": result["content"],
        "kind": result.get("pointKind", result.get("kind", "")),
        "context": result.get("context"),
        "created_at": result.get("createdAt", result.get("created_at", "")),
    }


@app.get("/v1/points")
async def list_points(
    kind: str | None = None,
    context: str | None = None,
    limit: int = 50,
    team: dict = Depends(get_current_team),
):
    """Query Points in the team's graph."""
    sdk = TortoiseSDK(namespace=team["team_id"])
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $limit",
        params={"limit": limit},
    ).result_set
    results = [r[0] for r in rows]
    return {"points": results, "count": len(results)}


@app.get("/v1/team", response_model=TeamInfoResponse)
async def team_info(team: dict = Depends(get_current_team)):
    """Get current team info: tier, usage, limits."""
    sdk = TortoiseSDK(namespace=team["team_id"])
    # Count Points in default graph
    point_count = sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)"
    ).result_set[0][0]

    return TeamInfoResponse(
        team_id=team["team_id"],
        tier=team["tier"],
        max_users=team["max_users"],
        max_graphs=team["max_graphs"],
        max_teams=team["max_teams"],
        point_count=point_count,
    )


@app.post("/v1/team/keys", response_model=CreateKeyResponse)
async def create_api_key(team: dict = Depends(get_current_team)):
    """Generate a new API key for the team."""
    sdk = TortoiseSDK(namespace="registry")
    result = sdk.apikey_create(team_id=team["team_id"], created_by=team.get("key_id", team["team_id"]))
    return CreateKeyResponse(
        id=result["id"],
        key=result["key"],
        key_prefix=result.get("key_prefix", result["key"][:8]),
        created_at=result.get("created_at", ""),
    )


@app.get("/v1/team/keys")
async def list_api_keys(team: dict = Depends(get_current_team)):
    """List API keys for the team (hashes only — no plaintext)."""
    sdk = TortoiseSDK(namespace="registry")
    keys = sdk._get_proj().g.query(
        "MATCH (k:APIKey {team_id: $tid}) "
        "RETURN k.id, k.key_prefix, k.created_at, k.last_used_at, k.revoked_at "
        "ORDER BY k.created_at DESC",
        params={"tid": team["team_id"]},
    )
    return {
        "keys": [
            {
                "id": row[0],
                "key_prefix": row[1],
                "created_at": row[2],
                "last_used_at": row[3],
                "revoked_at": row[4],
            }
            for row in keys.result_set
        ]
    }
