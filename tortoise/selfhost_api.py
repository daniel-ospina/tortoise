"""Self-host REST surface (#525).

Single-tenant REST API for the self-host daemon, aligned with the registry
RestSpec paths (tortoise/tool_registry.py — single source of truth for both
MCP and REST surfaces). Auth follows selfhost auth_mode: `static` (Bearer
TORTOISE_API_KEY, hmac compare) or `none` (loopback-bound eval — the startup
guard in selfhost.py refuses non-loopback none-mode).

Operations mirror the MCP tools (namespace="selfhost"): create/query points,
hybrid search, EP dreaming.
"""
from __future__ import annotations

import hmac
import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from tortoise.domain_loader import known_kinds

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


class CreatePointRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    kind: str = Field(default="statement")
    tags: list[str] = Field(default_factory=list)
    dedup: bool = Field(default=True)

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, v: str) -> str:
        if v not in known_kinds():
            raise ValueError(f"kind must be one of {sorted(known_kinds())}")
        return v

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, v: list[str]) -> list[str]:
        for t in v:
            if not t or len(t) > 200:
                raise ValueError("each tag must be 1-200 characters")
            if any(ch in t for ch in "\n\r\t"):
                raise ValueError("tags cannot contain newlines or tabs")
        return v


class PointResponse(BaseModel):
    id: str
    content: str
    kind: str
    created_at: str | None = None


def _get_api_key() -> str | None:
    return os.environ.get("TORTOISE_API_KEY")


def _require_key(authorization: str | None = Header(default=None)) -> None:
    """Static-key auth when auth_mode=static; no-op in none mode (loopback)."""
    api_key = _get_api_key()
    if not api_key:
        return  # auth_mode=none — loopback-guarded at startup
    if (
        not authorization
        or not authorization.startswith("Bearer ")
        or not hmac.compare_digest(authorization[7:].encode(), api_key.encode())
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _sdk():
    from tortoise.sdk import TortoiseSDK
    return TortoiseSDK(namespace="selfhost")


def _point_out(result: dict) -> dict:
    return {
        "id": result.get("id", ""),
        "content": result.get("content", ""),
        "kind": result.get("pointKind", result.get("kind", "")),
        "created_at": result.get("createdAt", result.get("created_at")),
    }


@router.post("/points", response_model=PointResponse, dependencies=[Depends(_require_key)])
async def create_point(body: CreatePointRequest):
    """Create a Point in the self-host graph (registry: POST /v1/points)."""
    sdk = _sdk()
    try:
        result = sdk.create_point(
            content=body.content,
            kind=body.kind,
            tags=body.tags,
            dedup=body.dedup,
        )
    except Exception as e:  # noqa: BLE001
        _logger.exception("selfhost create_point failed")
        raise HTTPException(status_code=500, detail=str(e)[:200])
    return _point_out(result)


@router.get("/points", response_model=list[PointResponse], dependencies=[Depends(_require_key)])
async def list_points(
    kind: str | None = None,
    tag: str | None = None,
    limit: int = Query(50, ge=1, le=1000),
):
    """Query Points in the self-host graph (optional kind/tag filters)."""
    if kind and kind not in known_kinds():
        raise HTTPException(status_code=400, detail=f"kind must be one of {sorted(known_kinds())}")
    sdk = _sdk()
    proj = sdk._get_proj()
    conditions = ["(n.is_operator IS NULL OR n.is_operator = false)"]
    params: dict = {"limit": limit}
    if kind:
        conditions.append("n.pointKind = $kind")
        params["kind"] = kind
    tag_clause = ""
    if tag:
        tag_clause = "-[:TAGGED]->(t:Tag {name:$tag})"
        params["tag"] = tag
    query = (
        f"MATCH (n:Point){tag_clause} WHERE "
        + " AND ".join(conditions)
        + " RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $limit"
    )
    rows = proj.g.query(query, params=params).result_set
    results = []
    for r in rows:
        d = dict(r[0])
        if "pointKind" in d:
            d["kind"] = d.pop("pointKind")
        results.append(d)
    return results


@router.get("/points/{point_id}", response_model=PointResponse, dependencies=[Depends(_require_key)])
async def get_point(point_id: str):
    """Get a single Point by id."""
    sdk = _sdk()
    result = sdk.get_point(point_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Point not found")
    return _point_out(result)


@router.get("/search", response_model=list[PointResponse], dependencies=[Depends(_require_key)])
async def search(q: str, limit: int = Query(10, ge=1, le=100)):
    """Hybrid search (registry: GET /v1/search)."""
    sdk = _sdk()
    try:
        results = sdk.query(text=q, limit=limit)
    except Exception as e:  # noqa: BLE001
        _logger.exception("selfhost search failed")
        raise HTTPException(status_code=500, detail=str(e)[:200])
    if results:
        return [_point_out(r) if isinstance(r, dict) else r for r in results]
    # FTS unavailable (embedded FalkorDBLite has no FTS index) — fall back to
    # substring CONTAINS so self-host eval still gets working search.
    proj = sdk._get_proj()
    query = (
        "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "AND toLower(n.content) CONTAINS toLower($q) "
        "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $limit"
    )
    rows = proj.g.query(query, params={"q": q, "limit": limit}).result_set
    return [_point_out(dict(r[0])) for r in rows]


@router.post("/dream", dependencies=[Depends(_require_key)])
async def dream(full: bool = False):
    """Trigger EP stabilization (registry: POST /v1/dream).

    Incremental (default) or full=True whole-graph. Mirrors hosted_api.
    """
    sdk = _sdk()
    try:
        result = sdk.dream(full=full) if full else sdk.dream(dirty_only=True)
        return {"status": "ok", "result": result}
    except Exception as e:  # noqa: BLE001
        _logger.exception("selfhost dream failed")
        raise HTTPException(status_code=500, detail=str(e)[:200])
