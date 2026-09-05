"""Canonical tool registry — single source of truth for all Tortoise operations.

One ToolDefinition per SDK operation. Both MCP and REST surfaces derive their
registrations from this registry. HTTP_ALLOWED is derived — zero manual sync.
"""
from __future__ import annotations  # noqa: I001

from dataclasses import dataclass, replace, field  # noqa: F401
from typing import Any, Callable, Optional  # noqa: UP035

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
    handler_override: Optional[Callable] = None  # For raw-Cypher REST ops  # noqa: UP045
    rest_spec: Optional[RestSpec] = None         # For SDK-backed REST ops  # noqa: UP045
    group: str = "memory"        # Curation group (#523): memory|reasoning|graph|sessions|sources|journal|admin|onboarding
    hosted_only: bool = False    # #1935: register ONLY on the hosted surface
                                 # (deployment-gated — e.g. tortoise_pack_install;
                                 # self-host uses filesystem packs dir + CLI)


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
        name="tortoise_validate_domain",
        description="Validate a domain's ontology integrity (issue #405) — advisory, read-only. "
                    "Runs the domain's graph-surface rules (orphan useCase, dangling refs, "
                    "draft hygiene) and returns enriched, actionable violations "
                    "({rule, kind, ref, message, fix}) + drift warnings. Never modifies "
                    "the graph; violations are warnings, not blocks.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="validate_domain",
    ),
    ToolDefinition(
        name="tortoise_summarize_structure",
        description="Count points per Gate (by pointKind). Returns {gateN_*, total}.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="summarize_structure",
    ),
    ToolDefinition(
        name="tortoise_audit",
        description="Audit graph wiring quality — 8 checks: missing sourceKind "
                    "(point-level legacy + Source-level canonical), missing sourceDate, "
                    "superseded points without a CORRECTS edge, live IMPL/NAND edges "
                    "into superseded points, naive-IMPL contradiction heuristic, "
                    "low-confidence operators without mitigation, and legacy 'mitigates' "
                    "edges. Returns structured JSON: per-check counts (uncapped) + "
                    "capped samples + summary + exit_code (0 clean, 1 issues). "
                    "point_kinds: optional pointKind list to scope the audit.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="audit",
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
    # epic #902 A13 (#1051) — batch audit surface
    ToolDefinition(
        name="tortoise_list_batch",
        description="Audit one ingest bundle: the stamped artifacts (Points "
                    "created or adopted via dedup + direct edges) carrying the "
                    "given batch_id. Entities/sources are out of stamp scope; "
                    "editorial supersede artifacts are outside audit. "
                    "Completeness holds across rebuild_all.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="list_batch",
    ),
    ToolDefinition(
        name="tortoise_list_batches",
        description="Batch discovery — the most recent distinct ingest batch_ids "
                    "with their point/direct-edge counts (ordered by newest "
                    "stamp, capped at limit, default 20).",
        annotations=_ro(),
        http_policy=True,
        sdk_method="list_batches",
    ),
    ToolDefinition(
        name="tortoise_packs_list",
        description="List this team's active packs (#318): the shared pack catalog "
                    "joined with the tenant graph's PackInstall activation records. "
                    "Read-only; empty result when nothing is installed (existence "
                    "masking — another tenant's packs are never observable).",
        annotations=_ro(),
        http_policy=True,
        sdk_method="get_tenant_packs",  # pack_state helper, not an SDK method
    ),
    ToolDefinition(
        name="tortoise_pack_install",
        description="Install a custom expansion pack on the HOSTED surface (#1935): "
                    "validates against the shared registry + tenant policy "
                    "(reserved starter namespace, ontology-only v1), stores the "
                    "manifest in the tenant graph and activates it. Deployment- "
                    "gated: on self-host this is an actionable stub (use the "
                    "filesystem packs dir + tortoise pack CLI).",
        annotations=_rw(),  # C5 #2114 (re-review P2): MERGEs manifests/installs — a write
        http_policy=True,
        sdk_method="upsert_tenant_manifest",  # pack_manifest_store helper
        group="admin",
        hosted_only=True,
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
    # ── Ask answer surface (#1987 Task 8/9) ───────────────────────
    # #2013 PRODUCT-GATING: group="ask" (OWN group, OFF by default — see
    # GROUP_BY_NAME below) — an ask consumes LLM tokens + the per-minute
    # ask budget (search is LLM-free) —
    # documented in the tool description. Read-classified: NOT in
    # _QUOTA_GATED / WRITE_TOOL_NAMES (introspection green). ``AskRequest``
    # is referenced by STRING (the registry convention — no class import,
    # avoiding the tool_registry → hosted_api → mcp_server → tool_registry
    # cycle); the model lives in tortoise/schemas.py (P2-3).
    ToolDefinition(
        name="tortoise_ask",
        description="Answer a question about captured memory — one bounded retrieve-then-"
                    "read pass (an ANSWER, not ranked hits) with the full ask response "
                    "shape (answer, abstained, evidence, cost_estimate_usd, ...). COST "
                    "PROFILE: unlike tortoise_search (LLM-free), tortoise_ask consumes "
                    "LLM tokens against the team's per-minute ask budget (60/min) — "
                    "budget-exhausted calls return the structured quota_exceeded error.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="ask",
        rest_spec=RestSpec(method="POST", path="/v1/ask",
                           request_model="AskRequest"),
    ),
    ToolDefinition(
        name="tortoise_expand_relationships",
        description="Full relationship payload for ONE Point, incl. each related point's "
                    "content (the expand side of the #1353 list/expand split — search "
                    "returns bounded state entries; use this to read a neighbor's full "
                    "text on demand).",
        annotations=_ro(),
        http_policy=False,
        sdk_method="expand_relationships",
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
    # ── Phase-4 mining/promotion/dedup/timeline (#787) ────────────
    ToolDefinition(
        name="tortoise_mine_conversations",
        description="Mine agent conversations (single transcript or corpus "
                    "batch) into the graph — extraction, entity reification, "
                    "content dedup, temporal wiring, W-3 batch gate. Batch "
                    "per-file failures reported non-fatally in 'errors'. "
                    "WRITES to the graph; corpus mode walks the server "
                    "filesystem — stdio/CLI only (excluded from tenant HTTP).",
        annotations=_rw(),
        http_policy=False,
        sdk_method="mine_corpus",
    ),
    ToolDefinition(
        name="tortoise_list_dedup_candidates",
        description="Review queue for dedup/temporal candidates "
                    "(candidate_type=content|temporal|entity).",
        annotations=_ro(),
        http_policy=True,
        sdk_method="list_dedup_candidates",
    ),
    ToolDefinition(
        name="tortoise_approve_merge",
        description="Review a dedup/temporal candidate — action=merge|reject. "
                    "Wiring deferred to promotion for live priors. WRITES "
                    "review flags + wires edges (idempotent for repeats).",
        annotations=_idem(),
        http_policy=True,
        sdk_method="approve_merge",
    ),
    ToolDefinition(
        name="tortoise_promote_point",
        description="Reviewer-gated draft→live promotion — the only path a "
                    "draft extraction Point may go live (quarantine lock, "
                    "R16 operator promotion, deferred dedup/temporal wiring). "
                    "WRITES status + wires edges.",
        annotations=_rw(),
        http_policy=True,
        sdk_method="promote_point",
    ),
    ToolDefinition(
        name="tortoise_belief_timeline",
        description="Dated, ordered belief chain for a topic — decision "
                    "Points with validFrom, NAND/CORRECTS links, superseded "
                    "priors visible.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="belief_timeline",
    ),
    # ── EP Belief Propagation (#6908) ─────────────────────────────
    ToolDefinition(
        name="tortoise_compute_confidence",
        description="Compute confidence via EP belief propagation. "
                    "Returns {iterations, converged, confidences}. "
                    "#395: no-arg (no factors/anchors) runs LOCAL EP over "
                    "the dirty subgraph on stdio/embedded; over HTTP, the "
                    "no-arg path serves the graph-persisted dirty subgraph "
                    "(#1163) and only returns diagnostic "
                    "'no_dirty_state_http' when the graph is truly clean. "
                    "anchors+max_hops=None is "
                    "clamped to a bounded default over HTTP. "
                    "Calibration is required by default (#344): raises "
                    "CalibrationError on uncalibrated graphs. Pass "
                    "require_calibration=False to run on topology alone "
                    "(explicit opt-out).",
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
                    "Default: incremental dirty subgraph. Set full=True for whole-graph. "
                    "mode (epic 903): explicit strategy override "
                    "{\"local\", \"stale-first\", \"full\"} — wins over full; "
                    "budget: per-pass operator cap.",
        annotations=_rw(),
        # #329: whole-graph EP is CPU-heavy — tenant MCP surface excluded
        # (stdio/operator only). REST /v1/dream remains tenant-reachable but is
        # separately budgeted at MAX_DREAM_FULL_PER_HOUR (hosted_api).
        http_policy=False,
        sdk_method="dream",
        rest_spec=RestSpec(method="POST", path="/v1/dream"),
    ),
    ToolDefinition(
        name="tortoise_dream_health",
        description="Dream observability (epic 903-C7): zero-output "
                    "silent-death alarm verdict + health record (last pass, "
                    "coverage, failure rate, region_attempts, warm-start "
                    "savings). Embedded call-triggered evaluator.",
        annotations=_ro(),
        http_policy=False,
        sdk_method="dream_health_check",
        rest_spec=RestSpec(method="GET", path="/v1/dream/health"),
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
    ToolDefinition(
        name="tortoise_session_capture",
        description="File an agent session into the graph (epic #909 capture path): "
                    "turns become episodic Points, the conversation is LLM-extracted into "
                    "epistemic Points, and the Session links to subject/project entities. "
                    "#1927: session_recording is default-ON (ToS-covered) with an "
                    "optional off-switch — returns 409 when the team disabled capture; "
                    "402 at quota; 503 without an LLM provider; "
                    "422 for an invalid harness. session_id is the idempotency key (re-filing "
                    "the same id mints zero new nodes). Only call this when the team has "
                    "session recording enabled (the dashboard 'Memory sources > Agent sessions' "
                    "toggle); if the call fails, tell the user it wasn't filed and do NOT "
                    "retry. Requires hosted mode — stdio/self-hosted returns an honest error "
                    "(no local fallback that bypasses the capture pipeline).",
        annotations=_rw(),
        http_policy=True,
        # Custom handler in mcp_server.py — the REST /v1/sessions route stays
        # hand-written in hosted_api.py (raw-Cypher op, Gate 3 pre-step
        # decision); no rest_spec here so the router adapter never double-
        # registers the hand-written route.
        sdk_method="",
    ),
    ToolDefinition(
        name="tortoise_issue_insight",
        description="Return a compact 'there's more in the graph' insight for a would-be "
                    "issue — call BEFORE filing. Surfaces cross-session decisions / EP-tagged "
                    "claims matching the title (semantic stage) plus prior indexed issues for "
                    "the repo (repo= given). Fail-closed: empty graph -> no_prior_knowledge; "
                    "populated graph + unindexed repo -> repo_not_indexed.",
        annotations=_ro(),
        http_policy=True,
        sdk_method="issue_insight",
        rest_spec=RestSpec(method="GET", path="/v1/issue-insight"),
    ),
    # ── Excluded from HTTP ────────────────────────────────────────
    ToolDefinition(
        name="tortoise_ingest_corpus",
        description="DEPRECATED — use tortoise_index_files. Batch document ingestion — walk directory, parse YAML frontmatter "
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
                    "bundle items. Endpoint typing (#2062): operator-routed connections "
                    "(reify:true / mitigation / part-whole) accept plain-Point or Event "
                    "endpoints (by node id; eventId-only Events stay violations); "
                    "direct-edge connections (plain IMPL/NAND) are plain-Point-only. "
                    "granularity='bulk' (default) returns aggregated counts; "
                    "granularity='granular' returns per-item results. promotion_policy='gated' "
                    "(default) keeps points draft and never promotes connections (operator "
                    "path: promote_source=False via #780); under gated ANY effective "
                    "status other than 'draft' on a point item is REJECTED (INGEST_CONTRACT "
                    "row 9 — case variants, nested props={...}, and terminal statuses "
                    "included) — use promotion_policy='auto' or promote after ingest via "
                    "update_point(status='live'). promotion_policy='auto' preserves the #131 "
                    "promote-on-wire lifecycle (draft/null-status sources go live on first "
                    "edge; terminal sources are never resurrected; deduped connections never "
                    "retro-promote). Idempotent-ish: "
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
        name="tortoise_find_cross_lens_candidates",
        description="Cross-lens candidate discovery (READ-ONLY, #438 bring-your-own-agent): "
                    "surface unverified candidate pairs between Points from DIFFERENT "
                    "sources (cross-stream discovery over the vector index) with lens "
                    "pair, cosine similarity, point context, and dedup vs existing "
                    "operators. Payload carries a single #901 routing field "
                    "(truth|relevance) but stays NEUTRAL — no op_type hint; the "
                    "customer agent decides semantics and writes operators via the "
                    "normal API. Gated on registered sourceKind (any tier, D3); hard "
                    "cap 200 candidates/cycle (D4); top_k is hard-clamped to 100 so an "
                    "agent cannot inflate the per-cycle recall budget. Empty results "
                    "(not errors) when there is nothing to see (D8).",
        annotations=_ro(),
        http_policy=True,
        sdk_method="get_cross_lens_candidates",
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
        description="DEPRECATED — use tortoise_index_files. Index session .md files "
                    "as AgentSession Events. "
                    "EXCLUDED from tenant HTTP — walks server filesystem with user-supplied path.",
        annotations=_rw(),
        http_policy=False,
        sdk_method="index_sessions",
    ),
    ToolDefinition(
        name="tortoise_index_files",
        description="Index a corpus directory of .md files as Sources + Events/"
                    "Documents (the unified index path — replaces "
                    "tortoise_index_sessions + tortoise_ingest_corpus file semantics "
                    "for local corpora). Returns the honest summary (file_count, "
                    "indexed, updated, skipped, failed, aborted, ignored, errors[], "
                    "by_kind). Idempotent — re-runs converge to skipped. On a shared "
                    "graph, give each corpus a unique corpus_name (default = directory "
                    "basename) to avoid cross-corpus url collisions. "
                    "EXCLUDED from tenant HTTP — walks server filesystem with user-supplied path.",
        annotations=_rw(),
        http_policy=False,
        sdk_method="index_directory",
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
        name="tortoise_onboarding_seed",
        description=("File the two onboarding anchor Subjects (Organization/"
                     "organization + User/naturalPerson linked memberOf) — "
                     "interactive, ontology-precise (#1999 W3): call without "
                     "names to discover gaps/collisions, with user-confirmed "
                     "names to file."),
        annotations=_rw(),
        http_policy=True,
        sdk_method="",
        rest_spec=RestSpec(method="POST", path="/v1/onboarding/seed"),
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

    Registration is additive-only: entries whose name is ALREADY on the mcp
    instance (stale @mcp.tool() decorators, other registration paths) are
    skipped, never replaced — see register_all (#2204 dedupe).

    Usage (in mcp_server.py):
        adapter = FastMCPAdapter(mcp)
        adapter.register_all(TOOL_REGISTRY, _handler_map)
    """

    def __init__(self, mcp: Any) -> None:
        self._mcp = mcp

    def register_all(self, registry: list[ToolDefinition], handlers: dict[str, Callable]) -> None:
        """Register every registry entry as an MCP tool, skipping names that
        are already registered on the mcp instance.

        The skip (#2204 dedupe) covers stale @mcp.tool() decorator
        registrations and any other registration path that ran first —
        fastmcp's default on_duplicate="warn" would otherwise log
        "Component already exists" and REPLACE the existing tool at import
        time. Skipped entries are still served; they are simply served by
        whichever registration happened first.

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
            # #2204: skip names already registered on the mcp instance (stale
            # @mcp.tool() decorators / other registration paths). fastmcp's
            # default on_duplicate="warn" logs "Component already exists" and
            # REPLACES — double registration at import is pure noise, and
            # replacing a live tool is never intended when the first
            # registration came from an identical decorator. Sync existence
            # check via the local provider's component store (get_tool is
            # async; _components is keyed "tool:<name>@..." — scanning by
            # component name avoids the key format). getattr-guarded so an
            # upstream layout change degrades to a duplicate warning, never
            # a crash.
            _provider = getattr(self._mcp, "_local_provider", None)
            _components = getattr(_provider, "_components", None)
            if _components is not None:
                _dup = any(
                    getattr(c, "name", None) == entry.name
                    for c in _components.values()
                )
                if _dup:
                    continue
            tool = FunctionTool.from_function(
                handler,
                name=entry.name,
                description=entry.description,
                annotations=entry.annotations,
            )
            self._mcp.add_tool(tool)


# ── REST endpoint classification (Gate 3 pre-step, #454) ─────────
# 9 REST tool-ops classified for the FastAPIRouterAdapter:
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
# | GET  /v1/issue-insight   | YES         | tortoise_issue_insight | populated above |
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
#   ask       — the answer surface (#2013 PRODUCT-GATING): OFF by default —
#               excluded from the ungrouped hosted surface unless
#               TORTOISE_ENABLE_ASK=1; served only via an explicit
#               tool_group="ask" server

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
    # #2013 PRODUCT-GATING: the ask tool has its OWN group (no longer the
    # "memory" default) so the hosted surface can exclude it by default —
    # the READER ships (the eval's reader), the ask EXPOSURE is gated off
    # until the reader-model decision is made.
    "tortoise_ask": "ask",
    "tortoise_expand_relationships": "memory",
    "tortoise_recall": "memory",
    "tortoise_issue_insight": "memory",
    "tortoise_mine_conversations": "mining",
    "tortoise_list_dedup_candidates": "review",
    "tortoise_approve_merge": "review",
    "tortoise_promote_point": "review",
    "tortoise_belief_timeline": "memory",
    "tortoise_compute_confidence": "memory", "tortoise_get_confidence": "memory",
    "tortoise_set_point_baseline": "memory", "tortoise_calibrate_summary": "memory",
    "tortoise_suggest_entry_points": "memory",
    # reasoning
    "tortoise_check_structure": "reasoning", "tortoise_validate_domain": "reasoning",
    "tortoise_summarize_structure": "reasoning",
    "tortoise_overview": "reasoning",
    "tortoise_traverse": "reasoning", "tortoise_entity_profile": "reasoning",
    "tortoise_analyze": "reasoning", "tortoise_taxonomy": "reasoning",
    "tortoise_list_topics": "reasoning", "tortoise_provenance": "reasoning",
    "tortoise_stale": "reasoning", "tortoise_dream": "reasoning",
    "tortoise_review_connections": "reasoning",
    "tortoise_find_cross_lens_candidates": "reasoning",
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
    "tortoise_packs_list": "admin", "tortoise_pack_install": "admin",  # #1935
    "tortoise_events_poll": "sessions",  # #432 CDC/subscription — not a memory tool
    # #1727 (Task 13): the session-capture filing tool groups under
    # "sessions" (else it falls to the "memory" default and is filtered out
    # of sessions-group surfaces — pinned by test_session_tool_grouped_sessions).
    "tortoise_session_capture": "sessions",
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
    "tortoise_onboarding_seed": "onboarding",  # #1999 (W3)
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


# ── Builder capability catalog (#2004 W8 — epic #1976 WF-6/DM-5/I-7) ──────
# The pullable indexers+extractors catalog a BUILD-fork org sees once on the
# build path. R2-9: lives HERE (no new infra). Registry rows are the canonical
# builder-facing module names (the presented copy — the dashboard fallback
# mirrors them byte-for-byte); each row names the REAL code module(s) that
# implement the capability, and every named module carries the catalog note
# in its module docstring (W8b sweep — tests/test_capability_catalog.py
# TestModuleNoteInventory reds on any missed note).
#
# Mapping evidence (2026-09-02, repo-wide class search): session recording =
# TortoiseSDK.capture_session + the hosted POST /v1/sessions capture; session
# extraction = extractor.py (Extractor/LLMExtractor) + extractor_v2.py (5-stage
# pipeline) + session_indexer.py (session-file metadata); document indexing =
# ingest.py (corpus) + file_indexer.py (file identity) + session_indexer.py
# (session .md files).
#
# Deliberately OUTSIDE this sweep (legacy/internal seams, not builder-facing
# capabilities — no note added, keep the list honest if they resurface):
# mining.py (internal batch conversation-mining pipeline over recorded
# sessions), value_extractor.py (extractor-v1 production seam behind
# TORTOISE_EXTRACTOR=v1 in sdk.py), session_import/parsers.py (recorder-side
# importers feeding POST /v1/sessions). A future "extractor" catalog row must
# add each implementing module to its modules tuple + the note to its file.
#
# DELIBERATELY EXCLUDED: the GitHub source indexers (tortoise/indexer/
# github_indexer.py + github_docs.py). They are the SELF-use Settings
# memory-source surface (github_* toggles, epic #1976 scope boundary: webhook
# GitHub ingestion is out of epic scope) — build-fork orgs see THIS catalog,
# not GitHub toggles. If GitHub ingestion becomes a buildable module, add a
# row here + the note to those files.

CATALOG_NOTE = (
    "This module is referenced in the builder capability catalog (onboarding) — "
    "tortoise/tool_registry.py CAPABILITY_CATALOG. If you add or rename an "
    "extractor/indexer, update the catalog reference."
)


@dataclass(frozen=True)
class CatalogModule:
    """One builder-facing data-input module in the capability catalog.

    ``modules`` lists the real code module(s) implementing the capability —
    each file's module docstring carries :data:`CATALOG_NOTE` (W8b sweep;
    inventory test iterates this tuple). ``available=False`` marks a planned
    module (the registry's honest future entry).
    """
    name: str                       # canonical builder-facing name
    kind: str                       # "indexer" | "extractor"
    description: str                # builder-facing one-liner (presented copy)
    modules: tuple[str, ...]        # real module paths (W8b note homes)
    available: bool = True          # False = planned/future


CAPABILITY_CATALOG: tuple[CatalogModule, ...] = (
    CatalogModule(
        name="Session recorder",
        kind="indexer",
        description="Files agent conversations to the graph.",
        modules=("tortoise/sdk.py", "tortoise/hosted_api.py"),
    ),
    CatalogModule(
        name="Session extractor",
        kind="extractor",
        description=("Pulls decisions and findings out of recorded "
                     "sessions."),
        modules=("tortoise/extractor.py", "tortoise/extractor_v2.py",
                 "tortoise/session_indexer.py"),
    ),
    CatalogModule(
        name="Document indexer",
        kind="indexer",
        description="Indexes documents you point your agent at.",
        modules=("tortoise/ingest.py", "tortoise/file_indexer.py",
                 "tortoise/session_indexer.py"),
    ),
    CatalogModule(
        name="Document extractor",
        kind="extractor",
        description=("Extracts claims and decisions from indexed documents "
                     "(planned)."),
        modules=(),
        available=False,
    ),
)


def capability_catalog() -> list[dict]:
    """Pullable catalog rows — the GET /v1/capabilities payload (#2004 W8).

    Returns the canonical module list (presented once on the build path):
    ``[{name, kind, description, available}, ...]``. Names/kinds/descriptions
    are the source of truth the dashboard fallback mirrors — rename here AND
    in the module docstrings (W8b), never one without the other.
    """
    return [
        {"name": m.name, "kind": m.kind, "description": m.description,
         "available": m.available}
        for m in CAPABILITY_CATALOG
    ]
