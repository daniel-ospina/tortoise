"""Canonical tool registry — single source of truth for all Tortoise operations.

One ToolDefinition per SDK operation. Both MCP and REST surfaces derive their
registrations from this registry. HTTP_ALLOWED is derived — zero manual sync.
"""
from __future__ import annotations

from dataclasses import dataclass, replace, field
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
    group: str = "memory"        # Curation group (#523): memory|reasoning|graph|sessions|sources|journal|admin|onboarding


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
        description="Query points by pointKind and/or property filters — structural exact-match "
                    "retrieval for known shapes (Epic #888: paginated_query + query_points_by_tag "
                    "merged in). Pagination via offset=/limit= (or 1-based page=); tag= filters "
                    "Points by TAGGED edge; include_retracted=True surfaces tombstones. For "
                    "semantic relevance use tortoise_search; for a single known ID use "
                    "tortoise_get_point.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="query",
    ),
    ToolDefinition(
        name="tortoise_paginated_query",
        description="DEPRECATED (Epic #888) — thin alias for tortoise_query(offset=, limit=). "
                    "Kept for one release; will be removed in the next release — migrate now.",
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
        description="DEPRECATED (Epic #888) — thin alias for tortoise_query(tag=). "
                    "Kept for one release; will be removed in the next release — migrate now.",
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
        description="Hybrid semantic search — FTS + vector + structural RRF fusion with EP "
                    "confidence annotation. Use when matching by MEANING (natural-language "
                    "query); for exact structural/filter queries use tortoise_query. "
                    "Full-scan mode: omit query, set kind → all Points of kind.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="tortoise_fts_query",
        rest_spec=RestSpec(method="GET", path="/v1/search"),
    ),
    ToolDefinition(
        name="tortoise_recall",
        description="Epistemic recall — four intents via mode (preset + override). "
                    "mode='state' (UC1): current high-confidence state — "
                    "multiplicative confidence gate "
                    "(score = relevance^a × confidence^b × (1 + w_c·centrality)), "
                    "excludes superseded/deprecated/retracted by default "
                    "(include_superseded=True brings them back), object-centric "
                    "(Objects + their Points ranked together), surfaces the most "
                    "important arguments (operators), high-contention NANDs and "
                    "mitigations, and flags contested claims with counter-evidence "
                    "attached (never rank-penalized). mode='gaps' (UC2): "
                    "load-bearing but under-supported claims — graph-structure "
                    "query (score = load/(1+support); load = outgoing IMPL+NAND, "
                    "support = incoming IMPL + Source edges; reads IMPL/NAND "
                    "operator-mediated or direct per the reification rule); needs "
                    "query (topic scope) or kind (population scan). mode='subgraph' "
                    "(UC3): complete connected subgraph for a seed/topic — "
                    "completeness-optimized (high recall, precision secondary), "
                    "returns {nodes, edges, stats}; needs seed (node id, Source url, "
                    "or topic text); depth 1-5, completeness core|full. "
                    "mode='custom': raw params, full control.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="recall_state",
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
        # #329: whole-graph EP is CPU-heavy — tenant MCP surface excluded
        # (stdio/operator only). REST /v1/dream remains tenant-reachable but is
        # separately budgeted at MAX_DREAM_FULL_PER_HOUR (hosted_api).
        http_policy=False,
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
    ToolDefinition(
        name="tortoise_file_human_approval",
        description="File a human approval of a planning artifact to the graph. "
                    "Creates Event (eventKind: humanApproval) + decision Point "
                    "(pointKind: humanApproval) + unidirectional IMPL fan-out "
                    "(label approvedBy) so dependent claims strengthen.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="file_human_approval",
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
                    "transfer_edges=True (default): full supersede — all edges move from "
                    "old to new. transfer_edges=False: invalidate behavior — outdated flag "
                    "+ CORRECTS edge only, no edge transfer.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="supersede",
    ),
    # ── Subscriptions / claim lifecycle (#432) ─────────────────────
    ToolDefinition(
        name="tortoise_events_poll",
        description="Poll graph/claim events after an opaque cursor (at-least-once). "
                    "Returns {events, next_cursor}. Event types: PointAdded, "
                    "OperatorAdded, PointRetracted, PointSuperseded, OperatorAnnotated.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="events_poll",
    ),
    ToolDefinition(
        name="tortoise_retract_point",
        description="Tombstone-retract a Point — status='retracted' (point stays "
                    "in graph, excluded from default surfaces). Terminal; cannot "
                    "retract operators or already-terminal points.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="retract_point",
    ),
    # ── Navigation (#6962, #6963, #6964) ──────────────────────────
    ToolDefinition(
        name="tortoise_entity_profile",
        description="Multi-hop BFS from an entity with optional filters (pointKind, "
                    "confidenceMin) — full neighborhood categorized by node type. Use for deep "
                    "entity analysis; for a fast neighbor list use tortoise_list_topics.",
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
    ToolDefinition(
        name="tortoise_ingest",
        description="Heterogeneous bulk write (epic #888 W4) — one call writes points + "
                    "entities + sources + connections coherently (nodes first, then "
                    "connections). Connections carrying 'operator' (IMPL/NAND) create "
                    "operator Points per the reification rule (v3.5 §8); connections "
                    "carrying 'relation' stay plain structural edges. Local refs address "
                    "bundle items. granularity='bulk' (default) returns aggregated counts; "
                    "granularity='granular' returns per-item results. promotion_policy='gated' "
                    "(default) keeps points draft and never promotes connections (operator "
                    "path: promote_source=False via #780); promotion_policy='auto' preserves "
                    "the #131 promote-on-wire lifecycle (source points go live on first edge). "
                    "Idempotent-ish: "
                    "points dedup by content hash + kind, sources by url, operators by "
                    "input set.",
        annotations=_idem(),
        http_policy=True,
        sdk_method="ingest",
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
        description="Fast one-hop neighbor enumeration for an entity — quick discovery and "
                    "navigation. Use for shallow context; for multi-hop filtered BFS use "
                    "tortoise_entity_profile.",
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
        name="tortoise_review_connections",
        description="Review graph connections (READ-ONLY — never mutates the graph). "
                    "Hygiene counterpart to connect: mode=add surfaces related-but-missing "
                    "connections as suggestions {from, to, suggested_relation, reason, "
                    "similarity} (nudge, don't enforce); mode=prune flags illogical/stale "
                    "IMPL/NAND connections {from, to, relation, issue, suggested_action, "
                    "detail} with issue in (contradictory, stale, contested) and action in "
                    "(review, prune, re-point); mode=both runs both and returns "
                    "{add: [...], prune: [...]}. Optional scope (topic text or Point id) "
                    "narrows the candidate pool.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="review_connections",
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
                    "Sources track content origin — url is the permalink key. "
                    "tier (T0-T4 / legacy alias) stores the credibility tier; "
                    "sourceDate is the evidence-age clock.",
        annotations=_idem(),
        http_policy=True,
        sdk_method="create_source",
    ),
    ToolDefinition(
        name="tortoise_get_source_reliability",
        description="Derive a Source's reliability (0-1) — query-time, "
                    "cache-consistency-checked. NOTE: refreshes the reliability "
                    "cache on the Source node (write-through projection), so not read-only.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="get_source_reliability",
    ),
    ToolDefinition(
        name="tortoise_assess_source",
        description="Record an agent's assessment of a Source (0-1 score + rationale). "
                    "Creates a pointKind='assessment' Statement Point; latest per "
                    "(url, assessor) wins; weighted by assessor reputation.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="assess_source",
    ),
    ToolDefinition(
        name="tortoise_set_source_tier",
        description="Set (or change) a Source's credibility tier (T0-T4). "
                    "Non-destructive — never overwrites sourceKind type strings.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="set_source_tier",
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
        name="tortoise_create_entity",
        description="Create an entity — type: subject|object|event|document. "
                    "Event entities wire about* edges from aboutSubject/aboutObject/"
                    "aboutPoint/aboutDocument props. Returns {node, nudges} — nudges "
                    "suggest IMPL/NAND/mitigate connections to related Points (advisory).",
        annotations=_rw(),
        http_policy=True,
        sdk_method="create_entity",
    ),
    ToolDefinition(
        name="tortoise_update",
        description="Update a Point OR entity by id. Points get point-lifecycle semantics "
                    "(draft→live promote via status, version increment for Point:Object, "
                    "status validation); entities get a plain property update.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="update",
    ),
    ToolDefinition(
        name="tortoise_delete",
        description="Delete a Point or entity by id. DESTRUCTIVE — requires human "
                    "confirmation. Cannot be undone.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="delete",
    ),
    ToolDefinition(
        name="tortoise_operator_action",
        description="Consolidated operator write action — action=mitigate|annotate. "
                    "mitigate: reason + strength (0-1) — creates/updates the mitigation "
                    "Point (idempotent). annotate: bias/precision/consistency/directness "
                    "(0-1).",
        annotations=_rw(),
        http_policy=True,
        sdk_method="operator_action",
    ),
    ToolDefinition(
        name="tortoise_create_edge",
        description="Create a typed structural edge between two entities. "
                    "Relation: performs, produces, uses, memberOf, ownedBy, managedBy, "
                    "about*, related, dependsOn, etc. Operator-less per the reification "
                    "rule (v3.5 §8) — lazy promotion via operator_action when mitigation "
                    "is needed. Returns {edge, created, nudges}.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="create_edge",
    ),
    ToolDefinition(
        name="tortoise_get_governance",
        description="Get all entities owned by a Subject.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="get_owned_entities",
    ),
    # ── Orient / Direct consolidation (epic #888 W3) ────────────────
    ToolDefinition(
        name="tortoise_overview",
        description="Graph orientation in one call — consolidates the list_*/status/health/"
                    "taxonomy/structure zoo. section: taxonomy|structure|structure_check|"
                    "pointkinds|tags|sources|namespaces|graphs|topics|health|status|stale. "
                    "Omit section → compact combined summary. topics requires entity_id.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py (dispatches to legacy tools)
    ),
    ToolDefinition(
        name="tortoise_get",
        description="Fetch a node by id — consolidates get_point/get_entity/get_operator/"
                    "get_events/get_session/get_governance. type: point|operator|entity|"
                    "event|session|events|governance. Omitted type → auto-detect by id "
                    "lookup (id|eventId|url, then session_id/sessionId).",
        annotations=_ro(),
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py (dispatches to legacy tools)
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
        name="tortoise_onboarding_github_index",
        description="Start background GitHub indexing of an org's issues/PRs (Q2).",
        annotations=_rw(),
        http_policy=True,
        sdk_method="",
        rest_spec=RestSpec(method="POST", path="/v1/index/github"),
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


# ── Tool curation groups (#523) ──────────────────────────────────
# Role-scoped grouping so agents can be served a curated subset (tool-selection
# accuracy degrades past ~20 tools). Groups:
#   memory    — point CRUD, queries, confidence, search
#   reasoning — structure checks, EP, analysis, taxonomy, provenance
#   graph     — operators, entities, events, edges
#   sessions  — session context, indexing, search
#   sources   — sources, documents, corpus ingestion
#   journal   — checkpoints, diary, decisions, approvals
#   admin     — status, health, teams, governance, migrations
#   onboarding — hosted onboarding flows

GROUP_BY_NAME: dict[str, str] = {
    # memory
    "tortoise_create_point": "memory", "tortoise_update_point": "memory",
    "tortoise_update": "memory",
    "tortoise_get_point": "memory", "tortoise_query": "memory",
    "tortoise_paginated_query": "memory", "tortoise_query_points_by_tag": "memory",
    "tortoise_delete_point": "memory", "tortoise_delete": "memory",
    "tortoise_supersede": "memory",
    "tortoise_invalidate": "memory", "tortoise_retract_point": "memory",
    "tortoise_list_tags": "memory",
    "tortoise_list_pointkinds": "memory", "tortoise_search": "memory",
    "tortoise_recall": "memory",
    "tortoise_compute_confidence": "memory", "tortoise_get_confidence": "memory",
    "tortoise_set_point_baseline": "memory", "tortoise_calibrate_summary": "memory",
    "tortoise_suggest_entry_points": "memory",
    # reasoning
    "tortoise_check_structure": "reasoning", "tortoise_summarize_structure": "reasoning",
    "tortoise_overview": "reasoning",
    "tortoise_traverse": "reasoning", "tortoise_entity_profile": "reasoning",
    "tortoise_analyze": "reasoning", "tortoise_taxonomy": "reasoning",
    "tortoise_list_topics": "reasoning", "tortoise_provenance": "reasoning",
    "tortoise_stale": "reasoning", "tortoise_dream": "reasoning",
    "tortoise_review_connections": "reasoning",
    # graph
    "tortoise_create_operator": "graph", "tortoise_annotate_operator": "graph",
    "tortoise_get_operator": "graph", "tortoise_mitigate_operator": "graph",
    "tortoise_operator_action": "graph",
    "tortoise_get": "graph",
    "tortoise_create_subject": "graph", "tortoise_create_object": "graph",
    "tortoise_create_event": "graph", "tortoise_create_entity": "graph",
    "tortoise_get_events": "graph",
    "tortoise_create_edge": "graph", "tortoise_get_entity": "graph",
    "tortoise_update_entity": "graph", "tortoise_delete_entity": "graph",
    "tortoise_list_sources": "sources", "tortoise_create_source": "sources",
    "tortoise_create_document": "sources", "tortoise_ingest_corpus": "sources",
    "tortoise_ingest": "graph",
    # sessions
    "tortoise_session_context": "sessions", "tortoise_get_session": "sessions",
    "tortoise_index_sessions": "sessions", "tortoise_search_sessions": "sessions",
    "tortoise_list_graphs": "sessions", "tortoise_list_namespaces": "sessions",
    "tortoise_events_poll": "sessions",  # #432 CDC/subscription — not a memory tool
    # journal
    "tortoise_checkpoint": "journal", "tortoise_diary_write": "journal",
    "tortoise_diary_read": "journal", "tortoise_file_decision": "journal",
    "tortoise_file_human_approval": "journal",
    # admin
    "tortoise_status": "admin", "tortoise_health": "admin",
    "tortoise_team_create": "admin", "tortoise_get_governance": "admin",
    "tortoise_backfill_v25": "admin",
    # onboarding
    "tortoise_onboarding_demo_create": "onboarding", "tortoise_onboarding_state": "onboarding",
    "tortoise_onboarding_session_recording": "onboarding",
    "tortoise_onboarding_github_connect": "onboarding",
    "tortoise_onboarding_github_index": "onboarding",
    "tortoise_onboarding_github_status": "onboarding",
}


def _apply_groups() -> list[ToolDefinition]:
    """Return the registry with curation groups assigned (frozen dataclass)."""
    out = []
    for t in TOOL_REGISTRY:
        out.append(replace(t, group=GROUP_BY_NAME.get(t.name, "memory")))
    return out


TOOL_REGISTRY = _apply_groups()


def tools_by_group(group: str) -> list[ToolDefinition]:
    """Tools in a curation group (e.g. "memory" — role-scoped server surface)."""
    return [t for t in TOOL_REGISTRY if t.group == group]


def tool_groups() -> dict[str, list[str]]:
    """Group name → tool names (for docs / surface introspection)."""
    out: dict[str, list[str]] = {}
    for t in TOOL_REGISTRY:
        out.setdefault(t.group, []).append(t.name)
    return out
