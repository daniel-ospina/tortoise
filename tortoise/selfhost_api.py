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
from tortoise.schemas import AskRequest  # noqa: E402 — the /v1/ask body (#1987 Task 9, imported from the shared constant layer)

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


def _require_key(authorization: str | None = Header(default=None)) -> None:
    """Static-key auth when auth_mode=static; allow in none mode (loopback).

    Contract (code-review P2, #525): when no key is configured the daemon is
    in auth_mode=none — the startup guard in selfhost.py refuses none-mode on
    non-loopback binds, so allow here is loopback-scoped only. This router is
    daemon-scoped; do not mount it in other apps without an explicit key.
    Reads the key per request (env may be set by config/CLI before serving).
    """
    api_key = os.environ.get("TORTOISE_API_KEY")
    if not api_key:
        return  # auth_mode=none — loopback-guarded at startup
    if (
        not authorization
        or not authorization.startswith("Bearer ")
        or not hmac.compare_digest(authorization[7:].encode(), api_key.encode())
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")


# #1475: per-request SDKs are closed-on-GC, which would shut down the
# embedded redislite server between requests (create → list → dead socket).
# Pin ONE SDK per embedded DB path as the server's liveness anchor (mirrors
# hosted_api._FALLBACK_KEEPALIVE); fresh per-request SDKs share the pinned
# server via the same path. Keyed by the resolved DB path (which must be
# threaded into the request SDK too — see _sdk) because selfhost tests
# reload the module per-test with a fresh path; a single "selfhost" key
# would pin the FIRST test's server and orphan every later one.
_SELFHOST_KEEPALIVE: dict[str, TortoiseSDK] = {}  # noqa: F821


def _resolve_embedded_db_path() -> str:
    """Resolve the embedded DB path, mirroring hosted_api._make_sdk:
    TORTOISE_DB_PATH, else /data/tortoise.db with a tempdir fallback when
    /data is not writable (test env / bare daemon run). The anchor AND the
    per-request SDK must agree on this path or the anchor pins a stray
    server while requests close-on-GC the real one (the #1475 regression
    silently persists)."""
    db_path = os.environ.get("TORTOISE_DB_PATH", "/data/tortoise.db")
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    except OSError:
        import tempfile
        db_path = os.path.join(tempfile.gettempdir(), "tortoise.db")
    return db_path


def _sdk():
    from tortoise.sdk import TortoiseSDK
    if not os.environ.get("TORTOISE_DB_URI"):
        # Embedded mode: hold the server alive across requests (see above).
        db_path = _resolve_embedded_db_path()
        anchor = _SELFHOST_KEEPALIVE.get(db_path)
        if anchor is None:
            anchor = TortoiseSDK(db_path=db_path, namespace="selfhost")
            try:  # noqa: SIM105
                anchor._get_proj()  # eager: hold the connection so the server survives
            except Exception:
                pass
            _SELFHOST_KEEPALIVE.setdefault(db_path, anchor)
        elif anchor._proj is None:
            # Self-heal (mirrors hosted_api): anchor stored unconnected
            # (transient failure) — retry once so keepalive is not off
            # permanently for this path.
            try:  # noqa: SIM105
                anchor._get_proj()
            except Exception:
                pass
        # Thread the SAME resolved path into the request SDK so anchor and
        # request share one server (path mismatch would defeat keepalive).
        return TortoiseSDK(db_path=db_path, namespace="selfhost")
    return TortoiseSDK(namespace="selfhost")


def _point_out(result: dict) -> dict:
    # Handles raw-property dicts (pointKind/createdAt), registry REST models
    # (kind/created_at), and FTS SearchResult.to_dict() (point_kind — code-review
    # P2-1, #525).
    return {
        "id": result.get("id", ""),
        "content": result.get("content", ""),
        "kind": result.get("pointKind", result.get("kind", result.get("point_kind", ""))),
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
    except Exception as e:  # noqa: BLE001, F841, RUF100
        _logger.exception("selfhost create_point failed")
        raise HTTPException(status_code=500, detail="Internal error")  # noqa: B904
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
    conditions = ["n.is_operator = false"]
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
    try:
        rows = proj.g.query(query, params=params).result_set
    except Exception as e:  # noqa: BLE001, F841, RUF100
        _logger.exception("selfhost list_points failed")
        raise HTTPException(status_code=500, detail="Internal error")  # noqa: B904
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
    if not result:  # get_point returns {} for a missing id (code-review P2)
        raise HTTPException(status_code=404, detail="Point not found")
    return _point_out(result)


@router.get("/search", response_model=list[PointResponse], dependencies=[Depends(_require_key)])
async def search(q: str, limit: int = Query(10, ge=1, le=100)):
    """Hybrid search (registry: GET /v1/search)."""
    sdk = _sdk()
    try:
        # Registry-aligned hybrid search (tortoise_search → tortoise_fts_query):
        # FTS/vector/RRF with an in-memory TF-IDF degradation path — works on
        # remote FalkorDB AND embedded eval (code-review P1, #525).
        results = sdk.tortoise_fts_query(query=q, limit=limit)
        return [_point_out(r) if isinstance(r, dict) else r for r in results]
    except Exception as e:  # noqa: BLE001, F841, RUF100
        _logger.exception("selfhost search failed")
        # Defensive CONTAINS fallback (FTS genuinely unavailable).
        try:
            proj = sdk._get_proj()
            query = (
                "MATCH (n:Point) WHERE n.is_operator = false "
                "AND toLower(n.content) CONTAINS toLower($q) "
                "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $limit"
            )
            rows = proj.g.query(query, params={"q": q, "limit": limit}).result_set
            return [_point_out(dict(r[0])) for r in rows]
        except Exception as e2:  # noqa: BLE001, RUF100
            _logger.exception("selfhost search fallback failed")
            raise HTTPException(status_code=500, detail="Internal error") from e2


@router.get("/topics/{topic}/summary", dependencies=[Depends(_require_key)])
async def topic_summary(
    topic: str,
    max_seeds: int = Query(50, ge=1, le=200),
    max_hops: int = Query(1, ge=0, le=3),
    include_relationships: bool = Query(True),
):
    """Epistemic topic summarization — settled vs contested structure (#592).

    GET /v1/topics/{topic}/summary

    Returns the epistemic structure for a topic: significant/settled claims,
    contested claims, disputed NAND pairs, and argument topology.

    Classification uses EP posterior variance (persisted posterior (posterior_alpha/beta, falling back to ep_alpha/beta priors)):
    - significant: confidence_mean >= 0.7 AND variance < 0.01
    - contested: variance > 0.04 (destabilized posterior)
    - disputed pairs: NAND-connected where both have variance > 0.02
    """
    sdk = _sdk()
    try:
        result = sdk.topic_summarize(
            topic,
            max_seeds=max_seeds,
            max_hops=max_hops,
            include_relationships=include_relationships,
        )
        return result
    except Exception as e:  # noqa: BLE001, RUF100
        _logger.exception("selfhost topic summary failed")
        raise HTTPException(status_code=500, detail="Internal error") from e


@router.post("/dream", dependencies=[Depends(_require_key)])
async def dream(full: bool = False, mode: str | None = None,
                budget: int | None = None):
    """Trigger EP stabilization (registry: POST /v1/dream).

    Epic 903-C8 (#1246): forwards mode/budget transparently (selfhost has
    no #329 bucket — the per-pass operator budget bounds stale-first
    passes).

    Incremental (default) or full=True whole-graph. Mirrors hosted_api.
    """
    sdk = _sdk()
    try:
        if mode is not None:
            result = sdk.dream(mode=mode, budget=budget)
        elif full:
            result = sdk.dream(full=True)
        else:
            result = sdk.dream(dirty_only=True)
        return {"status": "ok", "result": result}
    except Exception as e:  # noqa: BLE001, F841, RUF100
        _logger.exception("selfhost dream failed")
        raise HTTPException(status_code=500, detail="Internal error")  # noqa: B904


@router.post("/ask", dependencies=[Depends(_require_key)])
async def ask_question(body: AskRequest):  # noqa: B008
    """Self-host answer surface — REST parity with hosted /v1/ask (#1987
    Task 9): the LOCAL SDK lane (no team registry, NO budget — unmetered,
    ZERO metering records: ``team_id=None`` flows through the ``not team_id``
    exemption), bounded by the SAME ``run_ask_bounded`` wrapper (Semaphore(8)
    + 60s → 504 discipline) via ``team_id=None`` (P2-4). Errors mirror the
    hosted vocabulary via the path-scoped handler on ``selfhost.app``:
    502 ``reader_unavailable`` / ``retrieval_unavailable``, 504 ``timeout``,
    400 canonical codes from the SHARED ``AskRequest`` validators (identical
    input-boundary behavior to hosted — P2-8)."""
    from tortoise.exceptions import (  # noqa: I001
        AskReaderUnavailable,
        AskRetrievalUnavailable,
        AskValidationError,
    )
    from tortoise.quota import (  # noqa: I001
        AskBoundedTimeoutError,
        run_ask_bounded,
    )
    sdk = _sdk()
    try:
        return await run_ask_bounded(
            sdk.ask, None, body.question,
            question_type=body.question_type,
            question_date=body.question_date,
            _sdk_team_id=None,
        )
    except AskValidationError as e:
        raise HTTPException(status_code=400, detail=e.code) from e
    except AskBoundedTimeoutError:
        raise HTTPException(status_code=504, detail="timeout") from None
    except AskReaderUnavailable:
        raise HTTPException(status_code=502,
                            detail="reader_unavailable") from None
    except AskRetrievalUnavailable:
        raise HTTPException(status_code=502,
                            detail="retrieval_unavailable") from None
    except Exception as e:  # noqa: BLE001, RUF100
        _logger.exception("selfhost ask failed")
        raise HTTPException(status_code=502, detail="reader_unavailable") from e
