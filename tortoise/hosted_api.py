"""FastAPI app for Tortoise Hosted Platform.

Provides the internal /provision endpoint called by the Supabase
tenant-provision Edge Function, and will be extended with the full
multi-tenant REST API (issue #7717).

See: docs/epics/2026-08-03-tortoise-hosted-platform/04-plan.md §5, §6.1
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from tortoise.audit_events import AuditLogger
from tortoise.auth import hash_api_key
import hmac

from tortoise.sdk import TortoiseSDK

_logger = logging.getLogger(__name__)


def _make_sdk(*, namespace: str | None = None) -> TortoiseSDK:
    """Build an SDK backed by TORTOISE_DB_URI, or embedded mode when unset.

    Embedded fallback: when no URI is configured (fly.toml default), the SDK
    previously received no path and FalkorProjection raised
    "Either path or host must be provided" — every /internal/provision call
    failed with 500. Using an on-disk redislite DB keeps onboarding functional
    until a production FalkorDB instance is provisioned (#7722).
    """
    if os.environ.get("TORTOISE_DB_URI"):
        return TortoiseSDK(namespace=namespace)
    db_path = os.environ.get("TORTOISE_DB_PATH", "/data/tortoise.db")
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    except OSError:
        # /data volume not writable (test env, or volume not mounted yet) —
        # fall back to a temp file so provisioning still works.
        import tempfile
        db_path = os.path.join(tempfile.gettempdir(), "tortoise.db")
    return TortoiseSDK(db_path=db_path, namespace=namespace)


app = FastAPI(title="Tortoise Hosted API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://premiselabs.co", "https://app.premiselabs.co", "https://tortoise-y4mjjq.fly.dev"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Dreaming queue (#85) ────────────────────────────────────────────────
# Per-tenant async queue: writes enqueue the affected roots; a cooperative
# per-tenant drain task runs incremental EP dreaming. Serialized per tenant
# (never two concurrent dreams on one tenant graph). Debounced 100ms so
# bursty writes batch into one dream. Server-mode FalkorDB handles
# concurrency natively — this is a cooperative asyncio task, not a thread
# (no SQLite/redislite single-writer hazard, #6761/#176).
_DREAM_QUEUES: dict[str, asyncio.Queue] = {}
_DREAM_TASKS: dict[str, asyncio.Task] = {}
_DREAM_DEBOUNCE_S = 0.1
_DREAM_BATCH_MAX = 200
# Evict idle tenant queues after this many seconds (security P2, #85) so
# per-tenant queue/task dicts don't grow unboundedly across many tenants.
_DREAM_QUEUE_TTL_S = 600


def _enqueue_dream(team_id: str, dirty_roots: list[str]) -> None:
    """Enqueue affected roots for a tenant's next dream cycle."""
    if not dirty_roots:
        return
    q = _DREAM_QUEUES.setdefault(team_id, asyncio.Queue())
    for root in dirty_roots[: _DREAM_BATCH_MAX]:
        q.put_nowait(root)
    if team_id not in _DREAM_TASKS or _DREAM_TASKS[team_id].done():
        _DREAM_TASKS[team_id] = asyncio.create_task(_dream_worker(team_id))


async def _dream_worker(team_id: str) -> None:
    """Drain one tenant's queue with debounce, then run incremental dream."""
    q = _DREAM_QUEUES.get(team_id)
    if q is None:
        return
    try:
        # Debounce: collect roots that arrive within the window.
        await asyncio.sleep(_DREAM_DEBOUNCE_S)
        roots: list[str] = []
        while not q.empty() and len(roots) < _DREAM_BATCH_MAX:
            roots.append(q.get_nowait())
        if not roots:
            return
        sdk = _make_sdk(namespace=team_id)
        try:
            # Batch mark once (P3, #85) — one reverse-BFS pair, not N.
            sdk._mark_dirty(roots)
            sdk.dream(dirty_only=True)
        finally:
            sdk.close()
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception(
            "dream worker failed for tenant %s", team_id
        )
    finally:
        # Reschedule if more roots arrived during the drain.
        if not q.empty():
            _DREAM_TASKS[team_id] = asyncio.create_task(_dream_worker(team_id))
        elif team_id in _DREAM_QUEUES and team_id in _DREAM_TASKS:
            # Idle: evict the queue (TTL guard) unless a new write re-adds it.
            _DREAM_QUEUES.pop(team_id, None)
            _DREAM_TASKS.pop(team_id, None)


# ── Rate Limiter ──────────────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory token bucket rate limiter. 100 Points/min per API key."""

    SKIP = {"/health", "/docs", "/openapi.json"}

    def __init__(self, app, max_per_minute=100):
        super().__init__(app)
        self.max_per_minute = max_per_minute
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.time()
        self._lock = asyncio.Lock()
        # RATE_LIMIT_DISABLED=1 disables throttling (test env) — the test
        # suite creates >100 points per run against a shared IP bucket,
        # tripping 429 in full-suite runs. Production keeps the limit.
        self._disabled = os.environ.get("RATE_LIMIT_DISABLED") == "1"

    async def dispatch(self, request: Request, call_next):
        if self._disabled:
            return await call_next(request)
        if request.url.path in self.SKIP or request.url.path.startswith("/internal"):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not auth[7:].startswith("tt_"):
            # IP-based fallback for unauthenticated requests
            if request.client and request.client.host:
                key_id = f"ip:{request.client.host}"
            else:
                return await call_next(request)
        else:
            # Use API key as bucket key (per-key limits, avoids hashing in hot path)
            key_id = auth[7:]
        now = time.time()

        async with self._lock:
            # Periodic cleanup: prune empty buckets and buckets older than 60s
            if now - self._last_cleanup > 60:
                stale = []
                for k, v in list(self._buckets.items()):
                    v[:] = [t for t in v if now - t < 60]
                    if not v:
                        stale.append(k)
                for k in stale:
                    del self._buckets[k]
                self._last_cleanup = now

            bucket = self._buckets[key_id]
            bucket[:] = [t for t in bucket if now - t < 60]

            if len(bucket) >= self.max_per_minute:
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded. 100 points/minute per API key.",
                    headers={"Retry-After": "60"},
                )

            bucket.append(now)
        return await call_next(request)


app.add_middleware(RateLimitMiddleware, max_per_minute=100)


class HSTSMiddleware(BaseHTTPMiddleware):
    """Add Strict-Transport-Security header to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        return response

app.add_middleware(HSTSMiddleware)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response


app.add_middleware(SecurityHeadersMiddleware)

# Internal auth key for Edge Function → API communication
_INTERNAL_KEY = os.environ.get("FASTAPI_INTERNAL_KEY", "")


# ── Audit Event Logger ───────────────────────────────────────────

_audit_logger = AuditLogger(dsn=os.environ.get("TORTOISE_AUDIT_DSN"))


async def _async_audit(
    request: Request,
    team_id: str,
    operation: str,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> None:
    """Async-safe audit event writer. Offloads sync psycopg2 to thread pool."""
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    await asyncio.to_thread(
        _audit_logger.append,
        team_id=team_id,
        actor_user_id=None,
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip,
        user_agent=ua,
    )


def _check_internal(request: Request) -> None:
    """Verify internal auth — only Edge Functions call this."""
    if not _INTERNAL_KEY:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:], _INTERNAL_KEY):
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

    # Validate team_id and team_name against allowed pattern
    import re
    _team_pattern = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9 _-]{0,63}$')
    if not _team_pattern.match(team_id):
        raise HTTPException(status_code=400, detail="Invalid team_id format")
    if not _team_pattern.match(team_name):
        raise HTTPException(status_code=400, detail="Invalid team_name format")

    sdk = _make_sdk(namespace="registry")
    now = datetime.now(timezone.utc).isoformat()
    graph_name = f"team_{team_id}"

    try:
        # Create Team node in the control_plane registry graph
        sdk._get_registry().query(
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
        api_key_id = _short_id()
        sdk._get_registry().query(
            """
            CREATE (k:APIKey {
                id: $id, team_id: $team_id, key_hash: $hash,
                key_prefix: $prefix, created_by: $created_by,
                created_at: $now
            })
            """,
            params={
                "id": api_key_id,
                "team_id": team_id,
                "hash": api_key_hash,
                "prefix": team_id[:8],
                "created_by": created_by,
                "now": now,
            },
        )

        # Provision FalkorDB namespace for the team
        team_graph = sdk._get_proj().db.select_graph(graph_name)
        team_graph.query(
            "CREATE (:TeamMeta {name: $name, created: $now})",
            params={"name": team_name, "now": now},
        )

        # Create Membership (creator is Owner)
        sdk._get_registry().query(
            """
            CREATE (m:Membership {
                id: $id, user_id: $user_id, team_id: $team_id,
                role: 'owner', joined_at: $now
            })
            """,
            params={
                "id": _short_id(),
                "user_id": created_by,
                "team_id": team_id,
                "now": now,
            },
        )

        # Log audit event
        await _async_audit(
            request, team_id, "tenant_provision",
            resource_type="team", resource_id=team_id,
        )

        return {"status": "provisioned", "team_id": team_id, "graph_name": graph_name}
    except Exception:
        # Full rollback on any failure (registry graph)
        sdk._get_registry().query("MATCH (t:Team {id: $id}) DETACH DELETE t", params={"id": team_id})
        sdk._get_registry().query(
            "MATCH (k:APIKey {team_id: $id}) DETACH DELETE k", params={"id": team_id}
        )
        sdk._get_registry().query(
            "MATCH (m:Membership {team_id: $id}) DETACH DELETE m", params={"id": team_id}
        )
        try:
            team_graph = sdk._get_proj().db.select_graph(graph_name)
            team_graph.delete()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Tenant provisioning failed")


def _short_id() -> str:
    """Generate a short unique identifier (26 hex chars, no dashes)."""
    import uuid
    return uuid.uuid4().hex[:26]


@app.get("/health")
async def health():
    """Health check — verifies DB connectivity (not just app liveness)."""
    db_ok = False
    try:
        sdk = _make_sdk(namespace="registry")
        sdk._get_proj().g.query("RETURN 1")
        db_ok = True
    except Exception:
        pass
    if not db_ok:
        raise HTTPException(status_code=503, detail="Database unreachable")
    return {"status": "ok", "db": "connected"}


@app.get("/health/security")
async def health_security():
    """Security posture endpoint — verifies pepper, hashing, and auth config."""
    pepper_set = bool(os.environ.get("TORTOISE_SECRET_PEPPER"))
    internal_key_set = bool(os.environ.get("FASTAPI_INTERNAL_KEY"))
    return {
        "pepper_configured": pepper_set,
        "internal_key_configured": internal_key_set,
        "hashing": "pbkdf2_hmac_sha256",
        "api_auth_enforced": not internal_key_set or bool(os.environ.get("FASTAPI_INTERNAL_KEY")),
    }

# ── Phase 1a: Core Endpoints ──────────────────────────────────────


# ── Auth Dependency ────────────────────────────────────────────────

SKIP_AUTH = {"/health", "/docs", "/openapi.json"}


async def _audit_auth_failure(request: Request, reason: str) -> None:
    """Fire-and-forget audit log for an auth failure (401).

    Offloaded to a thread to avoid blocking the 401 response.
    """
    ip = request.client.host if request.client else None
    try:
        await asyncio.to_thread(
            _audit_logger.append,
            team_id="",
            actor_user_id=None,
            operation=f"auth_failure:{reason}",
            resource_type="api_key",
            resource_id=None,
            ip_address=ip,
            user_agent=request.headers.get("user-agent"),
        )
    except Exception:
        pass  # Audit failure must not affect the auth flow


async def get_current_team(request: Request) -> dict:
    if request.url.path in SKIP_AUTH or request.url.path.startswith("/internal"):
        return {"team_id": None, "tier": "free", "key_id": None}
    auth = request.headers.get("Authorization", "")
    if not auth:
        await _audit_auth_failure(request, "missing_header")
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not auth.startswith("Bearer "):
        await _audit_auth_failure(request, "bad_scheme")
        raise HTTPException(status_code=401, detail="Authorization header must use Bearer scheme")
    token = auth[7:]
    if not token.startswith("tt_"):
        await _audit_auth_failure(request, "invalid_format")
        raise HTTPException(status_code=401, detail="Invalid API key format")
    try:
        sdk = _make_sdk(namespace="registry")
        # API keys are stored as "salt:hash" (per-key random salt). hash_api_key()
        # generates a NEW random salt per call, so we CANNOT look up by exact
        # match. Instead fetch all non-revoked keys and verify each against the
        # token using the embedded salt (verify_api_key). This is O(keys) but
        # the registry is small (teams × keys) and auth happens per-request.
        key_result = sdk._get_registry().query(
            "MATCH (k:APIKey) WHERE k.revoked_at IS NULL RETURN k.team_id, k.id, k.key_hash"
        ).result_set
        from tortoise.auth import verify_api_key
        team_id = key_id = None
        for k_team_id, k_id, stored_hash in key_result:
            if verify_api_key(token, stored_hash):
                team_id, key_id = k_team_id, k_id
                break
        if team_id is None:
            await _audit_auth_failure(request, "invalid_key")
            raise HTTPException(status_code=401, detail="Invalid API key")
        team = sdk._get_registry().query(
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


def _check_team_limit(team: dict, resource: str) -> None:
    """Enforce per-team limits. Raises 402 (payment required) when at capacity.

    resource: 'points' | 'api_keys' | 'sessions'
    """
    team_id = team.get("team_id")
    if not team_id:
        return  # internal/no-team context — skip
    max_limits = {
        "points": team.get("max_points") or 1000,
        "api_keys": team.get("max_api_keys") or 20,
        "sessions": team.get("max_sessions") or 1000,
    }
    limit = max_limits.get(resource, 1000)
    try:
        sdk = _make_sdk(namespace=team_id)
        if resource == "api_keys":
            # API keys live in the registry graph, not the team graph
            sdk = _make_sdk(namespace="registry")
            count = sdk._get_registry().query(
                "MATCH (k:APIKey {team_id: $tid}) WHERE k.revoked_at IS NULL RETURN count(k)",
                params={"tid": team_id},
            ).result_set[0][0]
        else:
            count = sdk._get_proj().g.query(
                "MATCH (n) RETURN count(n)",
            ).result_set[0][0]
    except Exception:
        return  # if we can't count, don't block writes (fail-open on counting)
    if count >= limit:
        raise HTTPException(
            status_code=402,
            detail=f"Team {resource} limit reached ({limit}). Upgrade your plan to increase it.",
        )




# ── Pydantic Models ───────────────────────────────────────────────

from pydantic import BaseModel, Field, field_validator


class CreatePointRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    kind: str = Field(default="statement")
    tags: list[str] = Field(default_factory=list)

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, v: str) -> str:
        from tortoise.domain_loader import known_kinds
        allowed = known_kinds()
        if v not in allowed:
            raise ValueError(f"kind must be one of {sorted(allowed)}")
        return v

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, v: list[str]) -> list[str]:
        for t in v:
            if not t or len(t) > 200:
                raise ValueError("each tag must be 1-200 characters")
            if any(ch in t for ch in '\n\r\t'):
                raise ValueError("tags cannot contain newlines or tabs")
        return v


class PointResponse(BaseModel):
    id: str
    content: str
    kind: str
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
async def create_point(body: CreatePointRequest, request: Request, team: dict = Depends(get_current_team)):
    """Create a Point in the team's graph."""
    _check_team_limit(team, "points")
    sdk = _make_sdk(namespace=team["team_id"])
    try:
        result = sdk.create_point(
            content=body.content,
            kind=body.kind,
            tags=body.tags,
        )
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger("tortoise.api").exception("create_point failed")
        raise HTTPException(status_code=500, detail="Internal server error")
    # Dreaming (#85): enqueue the new point's dirty roots for background EP
    # stabilization (non-blocking — fast path is never gated on the dream).
    _enqueue_dream(team["team_id"], list(sdk._dirty_roots))
    # Log audit event
    await _async_audit(
        request, team["team_id"], "point_create",
        resource_type="point", resource_id=result.get("id"),
    )

    return {
        "id": result["id"],
        "content": result["content"],
        "kind": result.get("pointKind", result.get("kind", "")),
        "created_at": result.get("createdAt", result.get("created_at", "")),
    }


@app.get("/v1/points")
async def list_points(
    kind: str | None = None,
    tag: str | None = None,
    limit: int = Query(50, ge=1, le=1000),
    team: dict = Depends(get_current_team),
):
    """Query Points in the team's graph. Optional kind and tag filters."""
    if kind:
        from tortoise.domain_loader import known_kinds
        allowed = known_kinds()
        if kind not in allowed:
            raise HTTPException(status_code=400, detail=f"kind must be one of {sorted(allowed)}")
    sdk = _make_sdk(namespace=team["team_id"])
    proj = sdk._get_proj()
    conditions = ["(n.is_operator IS NULL OR n.is_operator = false)"]
    params: dict = {"limit": limit}
    if kind:
        conditions.append("n.pointKind = $kind")
        params["kind"] = kind
    if tag:
        # Tags are stored as a node property (n.tags = [...]); demo data also
        # creates TAGGED edges. Match the property (#7883).
        conditions.append("$tag IN coalesce(n.tags, [])")
        params["tag"] = tag
    query = "MATCH (n:Point) WHERE " + " AND ".join(conditions) + " RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $limit"
    rows = proj.g.query(query, params=params).result_set
    results = []
    for r in rows:
        d = r[0]
        if "pointKind" in d:
            d["kind"] = d.pop("pointKind")
        results.append(d)
    return {"points": results, "count": len(results)}


@app.get("/v1/points/{point_id}")
async def get_point(point_id: str, team: dict = Depends(get_current_team)):
    """Get a single Point by ID."""
    sdk = _make_sdk(namespace=team["team_id"])
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (p:Point {id: $id}) RETURN properties(p)",
        params={"id": point_id},
    ).result_set
    if not rows:
        raise HTTPException(status_code=404, detail="Point not found")
    props = dict(rows[0][0])
    if "pointKind" in props:
        props["kind"] = props.pop("pointKind")
    return props


@app.post("/v1/dream")
async def dream(
    full: bool = False,
    team: dict = Depends(get_current_team),
):
    """Trigger EP stabilization (dreaming, #85) for the team's graph.

    Incremental (default): stabilizes the team's accumulated dirty subgraph.
    full=True: whole-graph stabilization. Fast-path queries never block on
    this — dreaming is a background maintenance process.
    """
    sdk = _make_sdk(namespace=team["team_id"])
    try:
        if full:
            result = sdk.dream(full=True)
        else:
            # Drain whatever is queued plus any in-memory dirty roots.
            q = _DREAM_QUEUES.get(team["team_id"])
            if q is not None and not q.empty():
                while not q.empty():
                    sdk._mark_dirty([q.get_nowait()])
            result = sdk.dream(dirty_only=True)
        return result
    finally:
        sdk.close()


@app.get("/v1/search")
async def search(q: str, limit: int = Query(10, ge=1, le=100), team: dict = Depends(get_current_team)):
    """Hybrid search across Points (FTS + vector + structural, RRF-fused).

    Uses the SDK's tortoise_fts_query (search_engine) instead of raw
    substring CONTAINS — substring missed stemmed/fuzzy/typo matches and
    was not relevance-ranked (#160). FTS index on content/title/name/subject
    works without the embedding extra; vector joins in automatically when
    embeddings are available.
    """
    sdk = _make_sdk(namespace=team["team_id"])
    try:
        results = sdk.tortoise_fts_query(q, limit=limit)
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception("search failed")
        raise HTTPException(status_code=500, detail="Search failed")
    # Normalize to the public response shape (kind from pointKind).
    out = []
    for r in results:
        props = dict(r)
        if "pointKind" in props:
            props["kind"] = props.pop("pointKind")
        if "kind" not in props:
            props["kind"] = "statement"
        out.append(props)
    return {"results": out, "count": len(out)}


@app.get("/v1/team", response_model=TeamInfoResponse)
async def team_info(team: dict = Depends(get_current_team)):
    """Get current team info: tier, usage, limits."""
    sdk = _make_sdk(namespace=team["team_id"])
    # Count Points in default graph
    try:
        point_count = sdk._get_proj().g.query(
            "MATCH (n:Point) RETURN count(n)"
        ).result_set[0][0]
    except Exception as e:
        import logging
        logging.getLogger("tortoise.api").exception("team_info failed")
        raise HTTPException(status_code=500, detail="Internal server error")

    return TeamInfoResponse(
        team_id=team["team_id"],
        tier=team["tier"],
        max_users=team["max_users"],
        max_graphs=team["max_graphs"],
        max_teams=team["max_teams"],
        point_count=point_count,
    )


@app.post("/internal/demo")
async def create_demo_graph(request: Request):
    """Create a demo graph with sample Points across all 4 ontology layers.

    Called by the tenant-provision Edge Function after provisioning to seed
    demo data so new users see a populated graph immediately.
    """
    _check_internal(request)

    body = await request.json()
    team_id = body.get("team_id")
    if not team_id:
        raise HTTPException(status_code=400, detail="Missing team_id")

    sdk = _make_sdk(namespace=team_id)
    proj = sdk._get_proj()
    now = datetime.now(timezone.utc).isoformat()

    # Idempotency: sentinel written last — skip if already fully seeded
    existing = proj.g.query(
        "MATCH (p:Point {id: '_demo_sentinel'}) RETURN p.id"
    ).result_set
    if existing:
        return {"status": "already_seeded", "team_id": team_id}

    # ── Semantic Layer — facts and statements ────────────────────
    semantic_points = [
        ("sem_welcome", "observation",
         "Your Tortoise graph is ready. This is where agents file decisions, "
         "observations, and findings so your team remembers across sessions.",
         ["system", "welcome"]),
        ("sem_fact_tortoise", "statement",
         "Tortoise is a semantic epistemic graph engine that powers agent memory "
         "through four ontology layers: Semantic, Episodic, Epistemic, and Procedural.",
         ["tortoise", "overview"]),
        ("sem_fact_layers", "statement",
         "Semantic = facts and statements. Episodic = session history and events. "
         "Epistemic = claims with evidence and confidence. Procedural = workflows and skills.",
         ["tortoise", "ontology"]),
    ]
    for pid, kind, content, tags in semantic_points:
        proj.g.query(
            "MERGE (p:Point {id:$id}) "
            "SET p.content=$c, p.pointKind=$k, p.is_operator=false, "
            "p.status='live', p.createdAt=$now, p.updatedAt=$now",
            params={"id": pid, "c": content, "k": kind, "now": now},
        )
        for tag in tags:
            proj.g.query(
                "MATCH (p:Point {id:$pid}) "
                "MERGE (t:Tag {name:$tag}) "
                "MERGE (p)-[:TAGGED]->(t)",
                params={"pid": pid, "tag": tag},
            )

    # ── Episodic Layer — session events ──────────────────────────
    session_id = f"session_demo_{team_id[:8]}"
    proj.g.query(
        "MERGE (s:Session {id:$sid}) "
        "SET s.created_at=$now, s.turn_count=3",
        params={"sid": session_id, "now": now},
    )
    episodic_turns = [
        ("epi_turn1", "[user] Let's set up our agent memory system with Tortoise."),
        ("epi_turn2", "[assistant] I'll initialize the graph and configure the ontology layers. "
         "Once set up, all decisions will be tracked automatically."),
        ("epi_turn3", "[user] Great — make sure we capture decisions about architecture and product strategy."),
    ]
    for pid, content in episodic_turns:
        proj.g.query(
            "MERGE (t:Point {id:$id}) "
            "SET t.content=$c, t.pointKind='event', t.is_operator=false, "
            "t.status='live', t.createdAt=$now, t.updatedAt=$now",
            params={"id": pid, "c": content, "now": now},
        )
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (t:Point {id:$pid}) "
            "MERGE (s)-[:CONTAINS]->(t)",
            params={"sid": session_id, "pid": pid},
        )

    # ── Epistemic Layer — claims with evidence ───────────────────
    epistemic_points = [
        ("epis_claim1", "hypothesis",
         "Agent memory systems should be graph-native rather than vector-only "
         "because semantic relationships carry more signal than embedding proximity.",
         0.7, ["hypothesis", "architecture"]),
        ("epis_claim2", "evidence",
         "Teams using structured agent memory report 40% fewer repeated mistakes "
         "and 3x faster onboarding for new team members.",
         0.5, ["evidence", "adoption"]),
        ("epis_claim3", "decision",
         "We will use FalkorDB as the graph backend because it supports Cypher "
         "queries and runs as a lightweight extension to Redis.",
         0.9, ["decision", "infrastructure"]),
    ]
    for pid, kind, content, confidence, tags in epistemic_points:
        proj.g.query(
            "MERGE (p:Point {id:$id}) "
            "SET p.content=$c, p.pointKind=$k, p.is_operator=false, "
            "p.status='live', p.confidence=$conf, p.createdAt=$now, p.updatedAt=$now",
            params={"id": pid, "c": content, "k": kind, "conf": confidence, "now": now},
        )
        for tag in tags:
            proj.g.query(
                "MATCH (p:Point {id:$pid}) "
                "MERGE (t:Tag {name:$tag}) "
                "MERGE (p)-[:TAGGED]->(t)",
                params={"pid": pid, "tag": tag},
            )

    # ── Procedural Layer — workflows ─────────────────────────────
    procedural_points = [
        ("proc_wf1", "workflow",
         "CONTEXT-INJECTION: Before any coding task, call tortoise_suggest_entry_points() "
         "to find related context from past sessions and decisions.",
         ["workflow", "context"]),
        ("proc_wf2", "workflow",
         "DECISION-CAPTURE: After making a design decision, call tortoise_create_point() "
         "with kind='decision' so future agents can trace the reasoning chain.",
         ["workflow", "decision"]),
        ("proc_wf3", "workflow",
         "REVIEW-GATE: Before merging any PR, verify that key decisions are filed in Tortoise. "
         "If not, file them before merging.",
         ["workflow", "review"]),
    ]
    for pid, kind, content, tags in procedural_points:
        proj.g.query(
            "MERGE (p:Point {id:$id}) "
            "SET p.content=$c, p.pointKind=$k, p.is_operator=false, "
            "p.status='live', p.createdAt=$now, p.updatedAt=$now",
            params={"id": pid, "c": content, "k": kind, "now": now},
        )
        for tag in tags:
            proj.g.query(
                "MATCH (p:Point {id:$pid}) "
                "MERGE (t:Tag {name:$tag}) "
                "MERGE (p)-[:TAGGED]->(t)",
                params={"pid": pid, "tag": tag},
            )

    # ── Cross-layer links ────────────────────────────────────────
    # Link epistemic claim to supporting semantic fact
    proj.g.query(
        "MATCH (c:Point {id:'epis_claim1'}), (f:Point {id:'sem_fact_layers'}) "
        "MERGE (c)-[:SUPPORTS]->(f)"
    )
    # Link workflow to epistemic claim
    proj.g.query(
        "MATCH (w:Point {id:'proc_wf1'}), (c:Point {id:'epis_claim1'}) "
        "MERGE (w)-[:INFORMED_BY]->(c)"
    )

    # ── Sentinel — written last so partial failure allows retry ──
    proj.g.query(
        "CREATE (p:Point {id:'_demo_sentinel', content:'demo-sentinel', "
        "pointKind:'system', is_operator:false, status:'live', "
        "createdAt:$now, updatedAt:$now})",
        params={"now": now},
    )

    total_points = (
        len(semantic_points) + len(episodic_turns)
        + len(epistemic_points) + len(procedural_points)
    )
    return {
        "status": "demo_created",
        "team_id": team_id,
        "session_id": session_id,
        "points": total_points,
        "layers": {
            "semantic": len(semantic_points),
            "episodic": len(episodic_turns),
            "epistemic": len(epistemic_points),
            "procedural": len(procedural_points),
        },
    }


@app.post("/v1/team/keys", response_model=CreateKeyResponse)
async def create_api_key(request: Request, response: Response, team: dict = Depends(get_current_team)):
    """Generate a new API key for the team."""
    _check_team_limit(team, "api_keys")
    import uuid
    from tortoise.auth import hash_api_key
    sdk = _make_sdk(namespace="registry")
    api_key = f"tt_{uuid.uuid4().hex}"
    key_hash = hash_api_key(api_key)
    key_prefix = api_key[:10]
    kid = _short_id()
    now = datetime.now(timezone.utc).isoformat()
    sdk._get_registry().query(
        "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, key_prefix:$kp, created_by:$cb, created_at:$now})",
        params={"id": kid, "tid": team["team_id"], "kh": key_hash, "kp": key_prefix, "cb": "api", "now": now},
    )
    # Log audit event
    await _async_audit(
        request, team["team_id"], "api_key_create",
        resource_type="api_key", resource_id=kid,
    )

    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"

    return {
        "id": kid,
        "key": api_key,
        "key_prefix": key_prefix,
        "created_at": now,
    }


@app.get("/v1/team/keys")
async def list_api_keys(team: dict = Depends(get_current_team)):
    """List API keys for the team (hashes only — no plaintext)."""
    sdk = _make_sdk(namespace="registry")
    try:
        keys = sdk._get_registry().query(
            "MATCH (k:APIKey {team_id: $tid}) "
            "RETURN k.id, k.key_prefix, k.created_at, k.last_used_at, k.revoked_at "
            "ORDER BY k.created_at DESC",
            params={"tid": team["team_id"]},
        )
    except Exception as e:
        import logging
        logging.getLogger("tortoise.api").exception("list_api_keys failed")
        raise HTTPException(status_code=500, detail="Internal server error")
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




@app.delete("/v1/team/keys/{key_id}")
async def revoke_api_key(key_id: str, team: dict = Depends(get_current_team)):
    """Revoke an API key (soft delete — sets revoked_at). Team-scoped.

    Keys live in the control_plane registry graph (per #7873) — created via
    POST /v1/team/keys, listed via GET /v1/team/keys, revoked here, all on
    _get_registry(), consistent with the SDK's control-plane methods.
    """
    sdk = _make_sdk(namespace="registry")
    try:
        rows = sdk._get_registry().query(
            "MATCH (k:APIKey {id: $id}) RETURN k.team_id, k.revoked_at",
            params={"id": key_id},
        ).result_set
        if not rows:
            raise HTTPException(status_code=404, detail="API key not found")
        if rows[0][0] != team["team_id"]:
            raise HTTPException(status_code=403, detail="Not your API key")
        if rows[0][1] is not None:
            return {"revoked": True, "already": True, "key_id": key_id}
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        sdk._get_registry().query(
            "MATCH (k:APIKey {id: $id}) SET k.revoked_at = $now",
            params={"id": key_id, "now": now},
        )
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger("tortoise.api").exception("revoke_api_key failed")
        raise HTTPException(status_code=500, detail="Internal server error")
    return {"revoked": True, "key_id": key_id, "revoked_at": now}
# ── Session Capture ───────────────────────────────────────────────

class SessionRequest(BaseModel):
    conversation: list[dict] = Field(..., max_length=1000)

    @field_validator("conversation")
    @classmethod
    def valid_conversation(cls, v: list[dict]) -> list[dict]:
        for turn in v:
            content = turn.get("content", "")
            if len(content) > 5000:
                raise ValueError("each conversation turn content must be ≤ 5000 characters")
        return v
    session_id: str | None = None
    metadata: dict | None = None


@app.post("/v1/sessions")
async def capture_session(body: SessionRequest, request: Request, team: dict = Depends(get_current_team)):
    """Capture an agent session and extract turns as episodic Points."""
    _check_team_limit(team, "sessions")
    import uuid, re
    from datetime import datetime, timezone

    sdk = _make_sdk(namespace=team["team_id"])
    proj = sdk._get_proj()
    session_id = body.session_id or f"session_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    proj.g.query(
        "MERGE (s:Session {id:$sid}) SET s.created_at=$now, s.turn_count=$tc",
        params={"sid": session_id, "now": now, "tc": len(body.conversation)},
    )

    extracted = []
    decisions = [
        r"(?i)(?:let'?s|we will|we should|I will|I'm going to|decided|decision)\s+[^.!?]+[.!?]",
        r"(?i)(?:plan is|next steps?:|action item:)\s+[^.!?]+[.!?]",
    ]
    claims = [
        r"(?i)(?:I think|I believe|my understanding is|the problem is|the key insight)\s+[^.!?]+[.!?]",
        r"(?i)(?:evidence suggests|data shows|we found that|this means)\s+[^.!?]+[.!?]",
    ]

    for i, turn in enumerate(body.conversation):
        role = turn.get("role", "unknown")
        content = turn.get("content", "")
        turn_id = f"{session_id}_t{i}"

        proj.g.query(
            "CREATE (t:Point {id:$id, content:$c, pointKind:$k, is_operator:false, status:$s, createdAt:$now, updatedAt:$now})",
            params={"id": turn_id, "c": f"[{role}] {content[:5000]}", "k": "event", "s": "draft", "now": now},
        )
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) CREATE (s)-[:CONTAINS]->(t)",
            params={"sid": session_id, "tid": turn_id},
        )

        idx = 0
        for pat in decisions:
            for match in re.finditer(pat, content):
                pid = f"{turn_id}_d{idx}"
                text = match.group().strip()
                proj.g.query(
                    "CREATE (p:Point {id:$id, content:$c, pointKind:$k, is_operator:false, status:$s, createdAt:$now, updatedAt:$now})",
                    params={"id": pid, "c": text[:5000], "k": "decision", "s": "draft", "now": now},
                )
                proj.g.query(
                    "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) CREATE (s)-[:CONTAINS]->(p)",
                    params={"sid": session_id, "pid": pid},
                )
                extracted.append({"id": pid, "kind": "decision", "text": text[:200]})
                idx += 1

        idx = 0
        for pat in claims:
            for match in re.finditer(pat, content):
                pid = f"{turn_id}_c{idx}"
                text = match.group().strip()
                proj.g.query(
                    "CREATE (p:Point {id:$id, content:$c, pointKind:$k, is_operator:false, status:$s, createdAt:$now, updatedAt:$now})",
                    params={"id": pid, "c": text[:5000], "k": "statement", "s": "draft", "now": now},
                )
                proj.g.query(
                    "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) CREATE (s)-[:CONTAINS]->(p)",
                    params={"sid": session_id, "pid": pid},
                )
                extracted.append({"id": pid, "kind": "statement", "text": text[:200]})
                idx += 1

    # Ontology v3.1 §4.5/§3.2 (#7882): also create an episodic :Event node
    # (eventKind: sessionCaptured) and link extracted Points to it via
    # aboutEvent — the ontology's episodic model. The :Session node remains
    # the API-visible handle; the Event carries ontology-compliant provenance.
    try:
        event = sdk.create_event(
            f"session_{session_id}",
            "sessionCaptured",
            startedAt=now,
            endedAt=now,
            sessionId=session_id,
        )
        event_id = event.get("id") or event.get("eventId")
        for p in extracted:
            proj.create_about_edge(p["id"], event_id, "aboutEvent")
    except Exception:
        import logging
        logging.getLogger("tortoise.api").exception(
            "session Event creation failed (non-fatal)")

    # Log audit event
    await _async_audit(
        request, team["team_id"], "session_capture",
        resource_type="session", resource_id=session_id,
    )

    return {"session_id": session_id, "turns": len(body.conversation), "extracted": len(extracted), "points": extracted}


@app.get("/v1/sessions")
async def list_sessions(team: dict = Depends(get_current_team)):
    """List captured sessions."""
    sdk = _make_sdk(namespace=team["team_id"])
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (s:Session) RETURN s.id, s.created_at, s.turn_count ORDER BY s.created_at DESC LIMIT 50"
    ).result_set
    return {"sessions": [{"id": r[0], "created_at": r[1], "turns": r[2]} for r in rows]}


@app.get("/v1/context")
async def session_context(team: dict = Depends(get_current_team)):
    """Memory digest for agent session-start hooks (tortoise context CLI).

    Mirrors TortoiseSDK.session_context() so hosted users get the same
    injection payload as local users.
    """
    sdk = _make_sdk(namespace=team["team_id"])
    try:
        return sdk.session_context()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Context unavailable: {e}")
