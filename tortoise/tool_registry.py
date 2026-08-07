"""Canonical tool registry — single source of truth for all Tortoise operations.

One ToolDefinition per SDK operation. Both MCP and REST surfaces derive their
registrations from this registry. HTTP_ALLOWED is derived — zero manual sync.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from mcp.types import ToolAnnotations


@dataclass(frozen=True)
class RestSpec:
    """REST route metadata for SDK-backed operations."""
    method: str          # "GET" | "POST" | "DELETE"
    path: str            # "/v1/points" etc.
    request_model: type | None = None  # Pydantic request model
    response_model: type | None = None  # Pydantic response model
    status_code: int = 200


@dataclass(frozen=True)
class ToolDefinition:
    """One entry per SDK-exposed operation."""
    name: str                    # e.g. "tortoise_create_point"
    description: str             # Docstring for MCP + OpenAPI
    annotations: ToolAnnotations  # readOnlyHint, destructiveHint, idempotentHint
    http_policy: bool            # True = exposed on HTTP surfaces
    sdk_method: str              # Attribute name on TortoiseSDK, e.g. "create_point"
    handler_override: Optional[Callable] = None  # For raw-Cypher REST ops
    rest_spec: Optional[RestSpec] = None         # For SDK-backed REST ops


# ── Shorthand constructors ────────────────────────────────────────

def _ro() -> ToolAnnotations:
    return ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False)


def _rw() -> ToolAnnotations:
    return ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)


def _idem() -> ToolAnnotations:
    return ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)


# ── Registry Entries ────────────────────────────────────────────
# Maintained in the same order as mcp_server.py for diffability.
# New tools: add one entry here → both surfaces pick it up.

TOOL_REGISTRY: list[ToolDefinition] = [
    # ── Core CRUD ─────────────────────────────────────────────────
    ToolDefinition(
        name="tortoise_create_point",
        description="Create a Point node (statement, decision, vision, hypothesis, etc.). "
                    "dedup=True (default): idempotent — returns existing Point if content matches.",
        annotations=_idem(),
        http_policy=True,
        sdk_method="create_point",
        rest_spec=RestSpec(method="POST", path="/v1/points",
                           request_model="CreatePointRequest",
                           response_model="PointResponse"),
    ),
    ToolDefinition(
        name="tortoise_query",
        description="Query points by pointKind and/or property filters. "
                    "When text is provided, routes through tortoise_fts_query() for hybrid search.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="query",
    ),
    ToolDefinition(
        name="tortoise_paginated_query",
        description="Query points with SKIP/LIMIT pagination. Returns {results, total, hasMore}.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="paginated_query",
    ),
    ToolDefinition(
        name="tortoise_check_structure",
        description="Check Gate 0→4 chain integrity (orphans, dangling refs).",
        annotations=_ro(),
        http_policy=True,
        sdk_method="check_structure",
    ),
    ToolDefinition(
        name="tortoise_summarize_structure",
        description="Count points per Gate (by pointKind). Returns {gateN_*, total}.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="summarize_structure",
    ),
    ToolDefinition(
        name="tortoise_list_pointkinds",
        description="List all pointKinds present in the graph with counts. What EXISTS.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="list_pointkinds",
    ),
    ToolDefinition(
        name="tortoise_list_sources",
        description="List all Sources with point counts. Where data came FROM.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="list_sources",
    ),
    ToolDefinition(
        name="tortoise_list_namespaces",
        description="List installed pack namespaces.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="list_namespaces",
    ),
    # ── Tags (#215) ───────────────────────────────────────────────
    ToolDefinition(
        name="tortoise_list_tags",
        description="List all Tag names with count of tagged Points. Where tags are USED.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="list_tags",
    ),
    ToolDefinition(
        name="tortoise_query_points_by_tag",
        description="Return Points connected to a Tag via TAGGED edge.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="query_points_by_tag",
    ),
    # ── Point accessors ───────────────────────────────────────────
    ToolDefinition(
        name="tortoise_get_point",
        description="Get a single Point by ID. Returns all properties, or empty dict.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="get_point",
    ),
    ToolDefinition(
        name="tortoise_suggest_entry_points",
        description="Entity resolution — NL query → matching entities from the graph.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="suggest_entry_points",
    ),
    # ── Semantic Search (#6990) ───────────────────────────────────
    ToolDefinition(
        name="tortoise_search",
        description="Hybrid search with RRF fusion + EP annotation. "
                    "Full-scan mode: omit query, set kind → all Points of kind.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="tortoise_fts_query",
        rest_spec=RestSpec(method="GET", path="/v1/search"),
    ),
    # ── EP Belief Propagation (#6908) ─────────────────────────────
    ToolDefinition(
        name="tortoise_compute_confidence",
        description="Compute confidence via EP belief propagation. "
                    "Returns {iterations, converged, confidences}.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="compute_confidence",
    ),
    ToolDefinition(
        name="tortoise_set_point_baseline",
        description="Set Beta prior evidence for a claim.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="set_point_baseline",
    ),
    ToolDefinition(
        name="tortoise_get_confidence",
        description="Get EP confidence for a claim: {mean, variance, alpha, beta}.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="get_confidence",
    ),
    ToolDefinition(
        name="tortoise_calibrate_summary",
        description="Audit graph calibration state. Returns per-point guidance.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="calibrate_summary",
    ),
    ToolDefinition(
        name="tortoise_dream",
        description="Run EP stabilization (dreaming, #85). "
                    "Default: incremental dirty subgraph. Set full=True for whole-graph.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="dream",
        rest_spec=RestSpec(method="POST", path="/v1/dream"),
    ),
    # ── Updates ───────────────────────────────────────────────────
    ToolDefinition(
        name="tortoise_update_point",
        description="Update properties on a Point. Safe — modifies one Point only.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="update_point",
    ),
    # ── Operators ─────────────────────────────────────────────────
    ToolDefinition(
        name="tortoise_create_operator",
        description="Create an operator connecting Points. "
                    "op_type: IMPL, NAND, composedOf, decomposesInto, contains, wraps.",
        annotations=_idem(),
        http_policy=True,
        sdk_method="create_operator",
    ),
    ToolDefinition(
        name="tortoise_annotate_operator",
        description="Annotate an operator Point with structured epistemic dimensions "
                    "(bias, precision, consistency, directness).",
        annotations=_rw(),
        http_policy=True,
        sdk_method="annotate_operator",
    ),
    ToolDefinition(
        name="tortoise_get_operator",
        description="Get an operator Point by ID. Returns all properties including "
                    "annotation dimensions.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="get_point",  # get_operator uses get_point + is_operator check
    ),
    ToolDefinition(
        name="tortoise_mitigate_operator",
        description="Create a mitigation Point that modulates an operator's edge strength. "
                    "Idempotent — second call updates existing mitigation.",
        annotations=_idem(),
        http_policy=True,
        sdk_method="mitigate_operator",
    ),
    # ── Decisions ─────────────────────────────────────────────────
    ToolDefinition(
        name="tortoise_file_decision",
        description="File a simple decision directly to the graph. "
                    "Creates decision + options + evidence + IMPL edges atomically.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="file_decision",
    ),
    # ── Deletion / Invalidation ───────────────────────────────────
    ToolDefinition(
        name="tortoise_delete_point",
        description="Delete a Point. DESTRUCTIVE — requires human confirmation. Cannot be undone.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="delete_point_wrapped",
    ),
    ToolDefinition(
        name="tortoise_invalidate",
        description="Mark a Point outdated with a CORRECTS edge from the correcting Point.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="invalidate_point",
    ),
    ToolDefinition(
        name="tortoise_supersede",
        description="Atomically replace old Point with new — CORRECTS edge + outdated flag. "
                    "Equivalent to invalidate(old_id, new_id).",
        annotations=_rw(),
        http_policy=True,
        sdk_method="supersede_point",
    ),
    # ── Navigation (#6962, #6963, #6964) ──────────────────────────
    ToolDefinition(
        name="tortoise_entity_profile",
        description="Entity-centric traversal — BFS from entity node, categorize connected nodes. "
                    "Returns {entity, connected: {points, documents, events, subjects, objects}}.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="entity_profile",  # navigation.entityProfile — not a direct SDK method
    ),
    ToolDefinition(
        name="tortoise_traverse",
        description="Multi-hop graph traversal from entity following ALL relationship types. "
                    "Returns {entity, nodes: [{node, relationship, depth}]}.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="traverse",  # navigation.tortoise_traverse — not a direct SDK method
    ),
    # ── P0 Group 3: Checkpoint, Diary, Status, Ingest ─────────────
    ToolDefinition(
        name="tortoise_checkpoint",
        description="Session batch save — two-tier dedup (content hash + embedding similarity). "
                    "Returns {filed: N, duplicates: M}.",
        annotations=_idem(),
        http_policy=True,
        sdk_method="checkpoint",
    ),
    ToolDefinition(
        name="tortoise_diary_write",
        description="Write an agent diary entry (AAAK format suggested). "
                    "Creates a Point with pointKind=diary, authoredBy=agent.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="diary_write",
    ),
    ToolDefinition(
        name="tortoise_diary_read",
        description="Read recent diary entries for an agent, newest first.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="diary_read",
    ),
    ToolDefinition(
        name="tortoise_list_graphs",
        description="List all graph names in the database. Useful for namespace discovery.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="list_graphs",
    ),
    ToolDefinition(
        name="tortoise_status",
        description="Graph health + entity counts + FalkorDB connectivity. "
                    "Returns {connected, counts, total_entities}.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="status",
    ),
    ToolDefinition(
        name="tortoise_health",
        description="Health check + basic metrics: graph_size, last_ingest, error_count, uptime.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="health",  # monitoring.metrics — not a direct SDK method
    ),
    ToolDefinition(
        name="tortoise_session_context",
        description="Return 'what happened last session' — diary entries, recent Points, "
                    "Events, confidence changes.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="session_context",
        rest_spec=RestSpec(method="GET", path="/v1/context"),
    ),
    # ── Excluded from HTTP ────────────────────────────────────────
    ToolDefinition(
        name="tortoise_ingest_corpus",
        description="Batch document ingestion — walk directory, parse YAML frontmatter "
                    "from .md files, create/update Document nodes. "
                    "EXCLUDED from tenant HTTP — walks server filesystem with user-supplied path.",
        annotations=_rw(),
        http_policy=False,
        sdk_method="ingest_corpus",
    ),
    # ── Taxonomy ──────────────────────────────────────────────────
    ToolDefinition(
        name="tortoise_taxonomy",
        description="Count entities by node label. "
                    "Returns {Point: N, Event: N, Subject: N, Object: N, Document: N}.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="taxonomy",
    ),
    ToolDefinition(
        name="tortoise_list_topics",
        description="entityProfile lite for an entity. "
                    "Returns {id, pointKind, neighbors, neighborCounts}.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="list_topics",
    ),
    # ── Graph Analysis ────────────────────────────────────────────
    ToolDefinition(
        name="tortoise_analyze",
        description="Answer natural language questions about the Tortoise epistemic graph. "
                    "Ask things like: 'where is the disagreement?' 'what supports claim X?'",
        annotations=_ro(),
        http_policy=True,
        sdk_method="analyze",  # analyze.analyze — not a direct SDK method
    ),
    # ── P1-3: Staleness Detection ─────────────────────────────────
    ToolDefinition(
        name="tortoise_stale",
        description="Find Points not updated in N days. Returns {stale, count, cutoff, limit}.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="stale_points",
    ),
    ToolDefinition(
        name="tortoise_provenance",
        description="Provenance chain — 'Who decided this?' "
                    "Follows authoredBy → Subject → delegation.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="provenance",
    ),
    # ── Multi-tenancy (#7001) ─────────────────────────────────────
    ToolDefinition(
        name="tortoise_team_create",
        description="Create isolated team graph via FalkorDB select_graph. "
                    "EXCLUDED from tenant HTTP — provisioning belongs to "
                    "/internal/provision behind FASTAPI_INTERNAL_KEY.",
        annotations=_rw(),
        http_policy=False,
        sdk_method="team_create",
    ),
    # ── Entity CRUD (ONTOLOGY v2.5) ───────────────────────────────
    ToolDefinition(
        name="tortoise_create_subject",
        description="Create a Subject node (team, role, organization, person).",
        annotations=_rw(),
        http_policy=True,
        sdk_method="create_subject",
    ),
    ToolDefinition(
        name="tortoise_create_object",
        description="Create an Object node (product, customer, skill, etc.).",
        annotations=_rw(),
        http_policy=True,
        sdk_method="create_object",
    ),
    ToolDefinition(
        name="tortoise_create_event",
        description="Create an Event node (meeting, decision, deployment, etc.).",
        annotations=_rw(),
        http_policy=True,
        sdk_method="create_event",
    ),
    ToolDefinition(
        name="tortoise_get_events",
        description="Get recent Events, optionally filtered by eventKind (e.g. 'AgentSession').",
        annotations=_ro(),
        http_policy=True,
        sdk_method="get_events",
    ),
    ToolDefinition(
        name="tortoise_get_session",
        description="Get a single agent session Event by session_id.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="get_session",
    ),
    ToolDefinition(
        name="tortoise_index_sessions",
        description="Index session .md files as AgentSession Events. "
                    "EXCLUDED from tenant HTTP — walks server filesystem with user-supplied path.",
        annotations=_rw(),
        http_policy=False,
        sdk_method="index_sessions",
    ),
    ToolDefinition(
        name="tortoise_search_sessions",
        description="Search indexed agent sessions. Returns Events with narrative_arc snippets.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="search_sessions",
    ),
    ToolDefinition(
        name="tortoise_create_document",
        description="Create a Document node (research, planDoc, meetingNotes, etc.).",
        annotations=_idem(),
        http_policy=True,
        sdk_method="create_document",
    ),
    ToolDefinition(
        name="tortoise_create_source",
        description="Create a Source node for provenance (document, web, db, etc.). "
                    "Sources track content origin — url is the permalink key.",
        annotations=_idem(),
        http_policy=True,
        sdk_method="create_source",
    ),
    ToolDefinition(
        name="tortoise_get_entity",
        description="Get any entity by ID, eventId, or url.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="get_entity",
    ),
    ToolDefinition(
        name="tortoise_update_entity",
        description="Update any entity's properties.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="update_entity",
    ),
    ToolDefinition(
        name="tortoise_delete_entity",
        description="Delete any entity by ID.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="delete_entity",
    ),
    ToolDefinition(
        name="tortoise_create_edge",
        description="Create an edge between two entities. "
                    "Predicate: performs, produces, ownedBy, managedBy, etc.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="create_edge",  # proj.create_edge — not a direct SDK method
    ),
    ToolDefinition(
        name="tortoise_get_governance",
        description="Get all entities owned by a Subject.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="get_owned_entities",
    ),
    ToolDefinition(
        name="tortoise_backfill_v25",
        description="Backfill database to ONTOLOGY v2.5 schema. "
                    "EXCLUDED from tenant HTTP — schema-level migration (operator-only).",
        annotations=_rw(),
        http_policy=False,
        sdk_method="backfill_v25",
    ),
    # ── Onboarding tools (#498/#499/#500) ─────────────────────────
    ToolDefinition(
        name="tortoise_onboarding_demo_create",
        description="Create the demo epistemic graph (4 layers) for this team. Idempotent (Q4).",
        annotations=_idem(),
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py
        rest_spec=RestSpec(method="POST", path="/v1/demo"),
    ),
    ToolDefinition(
        name="tortoise_onboarding_state",
        description="Return this team's onboarding progress (Q6 verification).",
        annotations=_ro(),
        http_policy=True,
        sdk_method="",
        rest_spec=RestSpec(method="GET", path="/v1/onboarding/state"),
    ),
    ToolDefinition(
        name="tortoise_onboarding_session_recording",
        description="Toggle automatic session recording for this team (Q3).",
        annotations=_rw(),
        http_policy=True,
        sdk_method="",
        rest_spec=RestSpec(method="POST", path="/v1/onboarding/session-recording"),
    ),
    ToolDefinition(
        name="tortoise_onboarding_github_connect",
        description="Initiate GitHub OAuth — returns authorize URL + CSRF state (Q1).",
        annotations=_rw(),
        http_policy=True,
        sdk_method="",
        rest_spec=RestSpec(method="POST", path="/v1/onboarding/github/connect"),
    ),
    ToolDefinition(
        name="tortoise_onboarding_github_status",
        description="Return GitHub connection status for this team (Q1 verify).",
        annotations=_ro(),
        http_policy=True,
        sdk_method="",
        rest_spec=RestSpec(method="GET", path="/v1/onboarding/github/status"),
    ),

]


# ── Derived sets ─────────────────────────────────────────────────

def get_http_allowed() -> frozenset[str]:
    """Derive HTTP_ALLOWED from registry — zero manual sync."""
    return frozenset(t.name for t in TOOL_REGISTRY if t.http_policy)


# ── Adapters ────────────────────────────────────────────────────

class FastMCPAdapter:
    """Register all TOOL_REGISTRY entries on a FastMCP instance via add_tool().

    Replaces the 58 @mcp.tool() decorators with a single adapter call.
    Tool function bodies remain in mcp_server.py as module-level callables;
    this adapter wraps each via FunctionTool.from_function() and registers
    them through mcp.add_tool().

    Usage (in mcp_server.py):
        adapter = FastMCPAdapter(mcp)
        adapter.register_all(TOOL_REGISTRY, _handler_map)
    """

    def __init__(self, mcp: Any) -> None:
        self._mcp = mcp

    def register_all(self, registry: list[ToolDefinition], handlers: dict[str, Callable]) -> None:
        """Register every registry entry as an MCP tool.

        Args:
            registry: TOOL_REGISTRY list of ToolDefinition entries.
            handlers: dict mapping tool_name → callable function body.
        """
        from fastmcp.tools import FunctionTool

        missing = [e.name for e in registry if e.name not in handlers]
        if missing:
            import logging
            logging.getLogger(__name__).warning(
                "FastMCPAdapter: %d registry entries have no handler — skipped: %s",
                len(missing), ", ".join(sorted(missing)),
            )
        for entry in registry:
            handler = handlers.get(entry.name)
            if handler is None:
                continue
            tool = FunctionTool.from_function(
                handler,
                name=entry.name,
                description=entry.description,
                annotations=entry.annotations,
            )
            self._mcp.add_tool(tool)


# ── REST endpoint classification (Gate 3 pre-step, #454) ─────────
# 8 REST tool-ops classified for the FastAPIRouterAdapter:
#
# | REST endpoint            | SDK-backed? | Registry entry      | RestSpec            |
# |--------------------------|-------------|---------------------|---------------------|
# | POST /v1/points          | YES         | tortoise_create_point | populated above    |
# | GET  /v1/points          | RAW CYPHER  | (no SDK method)     | — extract list_points |
# | GET  /v1/points/{id}     | RAW CYPHER  | (differs from sdk.get_point) | — extract |
# | POST /v1/dream           | YES         | tortoise_dream      | populated above    |
# | GET  /v1/search          | YES         | tortoise_search     | populated above    |
# | POST /v1/sessions        | RAW CYPHER  | (content_hash dedup bug — filed as tortoise#490) | — extract capture_session |
# | GET  /v1/sessions        | RAW CYPHER  | (no SDK method)     | — extract list_sessions |
# | GET  /v1/context         | YES         | tortoise_session_context | populated above |
#
# Raw-Cypher ops are NOT added to the registry (no SDK method to register).
# Gate 3 pre-step: extract SDK methods for list_points/get_point_by_id/
# capture_session/list_sessions OR keep them hand-written in hosted_api.py
# and document the drift (control-plane-style). capture_session dedup bug
# tracked as tortoise#490.
#
# Control-plane endpoints (/internal/*, /v1/team*, /v1/team/keys) stay
# hand-written — out of registry scope per the epic scope guard.


class FastAPIRouterAdapter:
    """Register REST tool-ops from the registry onto a FastAPI APIRouter.

    Reads entries with rest_spec populated and registers routes. Surface
    policies (audit logging, team limits, dream enqueue) stay in the route
    handlers — the adapter only wires registration.

    Raw-Cypher ops (list_points, get_point, capture_session, list_sessions)
    have NO sdk_method — they are registered via handler_override with their
    existing hosted_api handler functions, OR kept hand-written in
    hosted_api.py with drift documented (Gate 3 pre-step decision).
    """

    def __init__(self, router: Any) -> None:
        self._router = router

    def register_all(self, registry: list[ToolDefinition],
                     handlers: dict[str, Callable]) -> None:
        """Register every rest_spec entry as a route.

        Args:
            registry: TOOL_REGISTRY entries.
            handlers: dict mapping tool_name → FastAPI route handler callable.
        """
        for entry in registry:
            if entry.rest_spec is None:
                continue
            handler = handlers.get(entry.name)
            if handler is None:
                continue
            self._router.add_api_route(
                entry.rest_spec.path,
                handler,
                methods=[entry.rest_spec.method],
                response_model=entry.rest_spec.response_model,
                name=entry.name,
            )
