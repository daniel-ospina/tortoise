"""TORT-MCP-001: MCP server wrapping TortoiseSDK. Stdio transport, ~10 tools."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from tortoise.auth import is_dev_mode as _is_dev_mode
from tortoise.sdk import TortoiseSDK
from tortoise import monitoring

_log = logging.getLogger(__name__)


def _load_dotenv(path: str | None = None) -> None:
    """Tiny .env loader — repo-root .env, KEY=VALUE lines, no new deps.

    Only sets environment keys that are empty/unset, so an explicit
    TORTOISE_DB_URI in the process env always wins. Mirrors the hosted
    entrypoint philosophy: the DB target must be explicit, never accidental.
    """
    if path is None:
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", ".env"
        )
    if not os.path.exists(path):
        return
    try:
        for raw in Path(path).read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            # Strip inline comments (whitespace + '#') — python-dotenv behavior.
            # A bare '#' inside an unquoted value is preserved (passwords).
            value = value.strip().split(" #", 1)[0].strip().strip("\"'")
            if key and not os.environ.get(key):
                os.environ[key] = value
    except OSError as e:
        _log.debug("Could not read .env (%s): %s — continuing without it", path, e)


if "pytest" not in sys.modules:
    _load_dotenv()  # resolve TORTOISE_DB_URI from repo-root .env (skipped under pytest)


# ── Safety annotations ───────────────────────────────────────────
# readOnlyHint=true: agent auto-approves, no confirmation needed
# destructiveHint=true: agent MUST get human confirmation
# idempotentHint=true: repeated calls have no extra side effects

mcp = FastMCP("tortoise")

# Resolve SDK connection from TORTOISE_DB_URI env var
_db_uri = os.environ.get("TORTOISE_DB_URI", "")
if _db_uri.startswith(("docker://", "redis://", "rediss://")):
    from tortoise.projection import FalkorProjection
    import time, sys
    sdk = TortoiseSDK()
    # Retry Docker connection 3x with backoff; exit on exhaustion (#25 P3a, #32)
    for attempt in range(3):
        try:
            sdk._proj = FalkorProjection.from_uri(_db_uri)
            if attempt > 0:
                _log.warning("Docker connection succeeded on attempt %d", attempt + 1)
            break
        except Exception as e:
            if attempt < 2:
                _log.warning("Docker connection attempt %d failed: %s — retrying in 2s", attempt + 1, e)
                time.sleep(2)
            else:
                _log.error("Docker connection failed after 3 attempts. Set TORTOISE_DB_URI or ensure FalkorDB is running.")
                sys.exit(1)
elif _db_uri:
    # File path — use Lite mode
    sdk = TortoiseSDK(db_path=_db_uri)
else:
    sdk = TortoiseSDK()

# Announce auth mode at startup
if _is_dev_mode():
    _log.warning("TORTOISE_API_KEY not set — running in dev mode (no auth)")


def _safe(fn, *args, **kwargs):
    """Call fn; return error dict on exception instead of raising.

    Auth (#7395): In production mode (TORTOISE_API_KEY set), stdio MCP
    transport cannot carry HTTP Bearer tokens — all operations are
    rejected. Use the authenticated HTTP endpoint instead.
    Dev mode (no key) is always unlocked.
    """
    if not _is_dev_mode():
        return {
            "error": (
                "Authentication required. The MCP stdio transport cannot "
                "carry auth tokens. Use an authenticated HTTP endpoint "
                "(tortoise health-server) with Authorization: Bearer <key> "
                "header, or unset TORTOISE_API_KEY for dev mode."
            )
        }
    try:
        result = fn(*args, **kwargs)
        return result
    except Exception as e:
        monitoring.record_error()
        msg = str(e)
        # Sanitize: strip hostnames, ports, passwords from error messages (#43)
        import re
        msg = re.sub(r'://[^@]*@', '://***@', msg)  # password in URI
        msg = re.sub(r'(host=|at |to )[\w.-]+(:\d+)?', r'\1***', msg)  # host:port
        return {"error": msg}


def _parse(v: Any) -> Any:
    """Parse JSON string inputs from LLM agents into native Python types.

    FastMCP strict-typed schemas reject JSON strings for list/dict params.
    LLM agents naturally emit JSON strings. This bridges the gap.
    """
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return v
    return v


@mcp.tool()
def tortoise_create_point(kind: str, content: str,
                          authoredBy: str | None = None,
                          props: Any = None,
                          dedup: bool = True) -> dict:
    """Create a Point node (statement, decision, vision, hypothesis, etc.).

    dedup=True (default): idempotent — returns existing Point if content matches.
    dedup=False: force-create even if content is identical.

    → See /skill:tortoise-graph-reasoning for pointKind guidance:
      evidence is a role (not a kind), use Source for provenance.
    """
    props = _parse(props)
    merged = dict(props or {})
    if authoredBy:
        merged["authoredBy"] = authoredBy
    merged["dedup"] = dedup
    return _safe(sdk.create_point, kind, content, **merged)


@mcp.tool()
def tortoise_query(kind: str | None = None,
                   filters: Any = None,
                   text: str | None = None,
                   order_by: str | None = None,
                   min_confidence: float | None = None,
                   entity_type: str = "point",
                   limit: int = 100) -> list[dict] | dict:
    """Query points by pointKind and/or property filters.

    When text is provided, routes through tortoise_fts_query() for hybrid search.
    When text is None, uses existing structural query.
    entity_type: 'point' (default), 'event', 'subject', 'document', 'object', 'operator', or 'source'.

    When results are empty and a kind filter was provided, a 'suggestion'
    key is added to the response with a hint or "did you mean" suggestion.
    """
    filters = _parse(filters)
    if text:
        return _safe(sdk.tortoise_fts_query, text, kind=kind,
                     entity_type=entity_type, limit=limit,
                     min_confidence=min_confidence or 0.0,
                     order_by=order_by or "relevance")
    result = _safe(sdk.query, kind, **(filters or {}))
    # If empty results and a kind filter was provided, attach suggestion
    if isinstance(result, list) and len(result) == 0 and kind is not None:
        from tortoise.query_suggestions import compute_suggestion
        suggestion = compute_suggestion(kind)
        if suggestion:
            return {"results": result, "suggestion": suggestion}
    return result


@mcp.tool()
def tortoise_paginated_query(kind: str | None = None,
                             skip: int = 0, limit: int = 20,
                             filters: Any = None) -> dict:
    """Query points with SKIP/LIMIT pagination. Returns {results, total, hasMore}."""
    filters = _parse(filters)
    return _safe(sdk.paginated_query, kind, skip=skip, limit=limit,
                 **(filters or {}))


@mcp.tool()
def tortoise_check_structure() -> list[dict]:
    """Check Gate 0→4 chain integrity (orphans, dangling refs)."""
    return _safe(sdk.check_structure)


@mcp.tool()
def tortoise_summarize_structure() -> dict:
    """Count points per Gate (by pointKind). Returns {gateN_*, total}."""
    return _safe(sdk.summarize_structure)


@mcp.tool()
def tortoise_list_pointkinds() -> list[dict]:
    """List all pointKinds present in the graph with counts. What EXISTS."""
    return _safe(sdk.list_pointkinds)


@mcp.tool()
def tortoise_list_sources() -> list[dict]:
    """List all Sources with point counts. Where data came FROM."""
    return _safe(sdk.list_sources)


@mcp.tool()
def tortoise_list_namespaces() -> list[dict]:
    """List installed pack namespaces."""
    return _safe(sdk.list_namespaces)


@mcp.tool()
def tortoise_get_point(id: str) -> dict:
    """Get a single Point by ID. Returns all properties, or empty dict."""
    return _safe(sdk.get_point, id)


# ── Entity Resolution (GAP-01 #6987) ──────────────────────────

@mcp.tool()
def tortoise_suggest_entry_points(query: str, limit: int = 5,
                                  kind_filter: str | None = None) -> list[dict]:
    """Entity resolution — NL query → matching entities from the graph.

    Uses hybrid search (tortoise_fts_query) for semantic entity resolution.
    Falls back to string match (CONTAINS) if hybrid search unavailable.
    Returns [{id, name, kind, confidence}] sorted by confidence DESC.
    """
    try:
        results = _safe(sdk.tortoise_fts_query, query, kind=kind_filter, limit=limit)
        if isinstance(results, list) and results and "error" not in results[0]:
            return [{"id": r["id"], "name": r.get("content", ""),
                     "kind": r.get("point_kind", ""),
                     "confidence": round(
                         0.5 * r.get("scores", {}).get("rrf", 0.0) +
                         0.5 * r.get("ep", {}).get("confidence_mean", 0.0), 4)}
                    for r in results]
    except Exception:
        pass
    return _safe(sdk.suggest_entry_points, query, limit=limit, kind_filter=kind_filter)


# ── Semantic Search (#6990) ────────────────────────────────────

@mcp.tool()
def tortoise_search(query: str | None = None, kind: str | None = None,
                    threshold: float = 0.0, limit: int = 10,
                    min_confidence: float = 0.0,
                    order_by: str = "relevance",
                    entity_type: str = "point") -> list[dict]:
    """Hybrid search with RRF fusion + EP annotation.

    entity_type: 'point' (default), 'event', 'subject', 'document', 'object', 'operator', or 'source'.
    Full-scan mode: omit query, set kind → all Points of kind.
    Best-match mode: provide query → RRF fusion of FTS + vector + structural.

    Point results annotated with EP breakdown (confidence_mean + evidence + contention).
    min_confidence defaults to 0.0 (no filter).

    Note: threshold default changed from 0.3 (Phase 0 semantic search) to 0.0.
    RRF scores are rank-based (0.01-0.05 range typical), not cosine similarity (0-1).
    Use threshold > 0 to filter out very weak matches; the old 0.3 default would
    reject nearly all RRF results. (#20)
    """
    return _safe(sdk.tortoise_fts_query, query, kind=kind,
                 threshold=threshold, limit=limit,
                 entity_type=entity_type,
                 min_confidence=min_confidence, order_by=order_by)


# ── EP Belief Propagation (#6908) ────────────────────────────────

@mcp.tool()
def tortoise_compute_confidence(factors: Any = None,
                    evidence: Any = None,
                    anchors: Any = None,
                    max_hops: int = 1,
                    rel_filter: str = "IMPL|NAND",
                    direction: str = "both",
                    require_calibration: bool = False) -> dict:
    """Compute confidence via EP belief propagation. Returns {iterations, converged, confidences}.

    Pass anchors=[point_ids] for BFS subgraph selection.
    Pass require_calibration=True to gate on calibration state.
    max_hops: BFS depth from anchors (default 1).
    rel_filter: edge types — "IMPL", "NAND", or "IMPL|NAND" (default).
    direction: IMPL traversal — "incoming", "outgoing", or "both" (default).
    """
    factors = _parse(factors)
    evidence = _parse(evidence)
    anchors = _parse(anchors)
    return _safe(sdk.compute_confidence, factors, evidence,
                 anchors=anchors,
                 max_hops=max_hops, rel_filter=rel_filter,
                 direction=direction,
                 require_calibration=require_calibration)


@mcp.tool()
def tortoise_set_point_baseline(claim_id: str, alpha: float, beta: float) -> dict:
    """Set Beta prior evidence for a claim."""
    return _safe(sdk.set_point_baseline, claim_id, alpha, beta)


@mcp.tool()
def tortoise_get_confidence(claim_id: str) -> dict:
    """Get EP confidence for a claim: {mean, variance, alpha, beta}."""
    return _safe(sdk.get_confidence, claim_id)


@mcp.tool()
def tortoise_calibrate_summary() -> list[dict]:
    """Audit graph calibration state. Returns per-point guidance."""
    return _safe(sdk.calibrate_summary)


@mcp.tool()
def tortoise_update_point(id: str, props: Any) -> dict:
    """Update properties on a Point. Safe — modifies one Point only."""
    props = _parse(props)
    return _safe(sdk.update_point, id, **(props or {}))

@mcp.tool()
def tortoise_create_operator(op_type: str, source_id: str, target_ids: Any) -> dict:
    """Create an operator connecting Points.
    
    op_type: 'IMPL' (A supports B), 'NAND' (A contradicts B),
             'composedOf'/'decomposesInto'/'contains'/'wraps' → stored as hasPart edge.
    source_id: source/parent Point ID.
    target_ids: target/child Point IDs (1 for IMPL/NAND, N for part/whole).

    → See /skill:tortoise-graph-reasoning for proper usage:
      annotation, mitigation, NAND constraints, veracity vs implication.
    """
    target_ids = _parse(target_ids)
    return _safe(sdk.create_operator, op_type, source_id, target_ids)


@mcp.tool()
def tortoise_annotate_operator(id: str, bias: float, precision: float,
                                consistency: float, directness: float) -> dict:
    """Annotate an operator Point with structured epistemic dimensions.

    bias: 0-1 — hidden stake beyond stated position.
    precision: 0-1 — how narrow/well-defined the relevance claim is.
    consistency: 0-1 — stability across contexts.
    directness: 0-1 — how directly source bears on target.
    """
    return _safe(sdk.annotate_operator, id, bias, precision, consistency, directness)


@mcp.tool()
def tortoise_get_operator(id: str) -> dict:
    """Get an operator Point by ID. Returns all properties including annotation dimensions.
    Raises error if the Point is not an operator."""
    point = _safe(sdk.get_point, id)
    if isinstance(point, dict) and point and not point.get("is_operator"):
        return {"error": f"Point {id!r} is not an operator"}
    return point


@mcp.tool()
def tortoise_mitigate_operator(id: str, reason: str, strength: float = 0.5) -> dict:
    """Create a mitigation Point that modulates an operator's edge strength.

    reason: Why the edge is weaker than it appears.
    strength: 0-1 — 0=fully neutralized, 1=fully intact (default 0.5).
    Idempotent — second call updates existing mitigation.
    """
    return _safe(sdk.mitigate_operator, id, reason, strength)


@mcp.tool()
def tortoise_file_decision(options: Any, evidence: Any,
                           choice: int) -> dict:
    """File a simple decision directly to the graph.

    Creates decision + options + evidence + IMPL edges atomically.
    For low-stakes decisions where the answer is clear — no EP,
    no calibration, no research cycles. Under 5 graph operations.

    options: list of option descriptions (e.g. ["JSON", "YAML", "TOML"])
    evidence: list of evidence statements supporting the choice
    choice: 0-indexed option index (e.g. 0 = JSON)

    Returns {decision_id, option_ids: [...], evidence_ids: [...]}.
    """
    options = _parse(options)
    evidence = _parse(evidence)
    return _safe(sdk.file_decision, options, evidence, choice)


@mcp.tool()
def tortoise_delete_point(id: str) -> dict:
    """Delete a Point. DESTRUCTIVE — requires human confirmation. Cannot be undone."""
    return _safe(sdk.delete_point_wrapped, id)


@mcp.tool()
def tortoise_invalidate(id: str, corrected_by_id: str) -> dict:
    """Mark a Point outdated with a CORRECTS edge from the correcting Point.

    The `corrected_by_id` point CORRECTS the invalidated point.
    Returns {invalidated, id, corrected_by}.
    """
    return _safe(sdk.invalidate_point, id, corrected_by_id)


@mcp.tool()
def tortoise_supersede(old_id: str, new_id: str) -> dict:
    """Atomically replace old Point with new — CORRECTS edge + outdated flag.

    Equivalent to invalidate(old_id, new_id) — marks old outdated,
    creates CORRECTS edge from new to old.
    Returns {invalidated, id, corrected_by}.
    """
    return _safe(sdk.supersede_point, old_id, new_id)



# ── Navigation (#6962, #6963, #6964) ─────────────────────────────

@mcp.tool()
def tortoise_entity_profile(entity_id: str, hops: int = 2,
                             graph_name: str = "tortoise",
                             pointKind: str | None = None,
                             confidenceMin: float | None = None) -> dict:
    """Entity-centric traversal — BFS from entity node, categorize connected nodes.

    Returns {entity: {...}, connected: {points, documents, events, subjects, objects}}.
    Optional filters: pointKind, confidenceMin.
    """
    from tortoise.navigation import entityProfile
    proj = sdk._get_proj()
    return _safe(entityProfile, proj.db, graph_name, entity_id,
                  hops=hops, pointKind=pointKind, confidenceMin=confidenceMin)


@mcp.tool()
def tortoise_traverse(entity_id: str, max_hops: int = 2,
                       graph_name: str = "tortoise") -> dict:
    """Multi-hop graph traversal from entity following ALL relationship types.

    Returns {entity: {...}, nodes: [{node, relationship, depth}, ...]}.
    """
    from tortoise.navigation import tortoise_traverse as _traverse
    proj = sdk._get_proj()
    return _safe(_traverse, proj.db, graph_name, entity_id, max_hops)


def main():
    monitoring.register(sdk)
    if not os.environ.get("TORTOISE_DB_URI"):
        if os.environ.get("TORTOISE_ALLOW_EMBEDDED") == "1":
            _log.warning(
                "TORTOISE_DB_URI unset — running embedded (empty graph). "
                "This is a test-only escape hatch; agents will read/write an empty DB."
            )
        else:
            _log.error(
                "TORTOISE_DB_URI is not set. MCP would silently connect to an empty "
                "embedded DB. Set TORTOISE_DB_URI in the environment or in .env "
                "(repo root) to your local FalkorDB instance "
                "(e.g. docker://:@localhost:16379/tortoise), then restart. "
                "See .env.example. Override with TORTOISE_ALLOW_EMBEDDED=1 (test only)."
            )
            sys.exit(1)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

# ── P0 Group 3: Checkpoint, Diary, Status, Ingest ──────────────

@mcp.tool()
def tortoise_checkpoint(items: Any,
                        agent_name: str = "checkpoint",
                        threshold: float = 0.95) -> dict:
    """Session batch save — two-tier dedup (content hash + embedding similarity).

    items: [{wing, room, content}, ...]
    agent_name: name for provenance events (default: "checkpoint")
    threshold: cosine similarity for semantic dedup (0.0-1.0).
               Set to 1.0 to disable semantic dedup (hash-only).
    Returns {filed: N, duplicates: M}.
    """
    items = _parse(items)
    return _safe(sdk.checkpoint, items,
                 agent_name=agent_name, threshold=threshold)


@mcp.tool()
def tortoise_diary_write(agent_name: str, entry: str,
                         topic: str | None = None,
                         wing: str | None = None) -> dict:
    """Write an agent diary entry (AAAK format suggested).
    Creates a Point with pointKind=diary, authoredBy=agent.
    """
    return _safe(sdk.diary_write, agent_name, entry, topic=topic, wing=wing)


@mcp.tool()
def tortoise_diary_read(agent_name: str, last_n: int = 10,
                        wing: str | None = None) -> list[dict]:
    """Read recent diary entries for an agent, newest first."""
    return _safe(sdk.diary_read, agent_name, last_n, wing=wing)


@mcp.tool()
def tortoise_list_graphs() -> list[str]:
    """List all graph names in the database. Useful for namespace discovery."""
    return _safe(sdk.list_graphs)


@mcp.tool()
def tortoise_status() -> dict:
    """Graph health + entity counts + FalkorDB connectivity.
    Returns {connected, counts: {Point, Event, ...}, total_entities}.
    """
    return _safe(sdk.status)


@mcp.tool()
def tortoise_health() -> dict:
    """Health check + basic metrics: graph_size, last_ingest, error_count, uptime."""
    return monitoring.metrics()


@mcp.tool()
def tortoise_session_context() -> dict:
    """Return 'what happened last session' — diary entries, recent Points, Events, confidence changes.
    Returns {no_prior_sessions, diary_entries, recent_points, recent_events, confidence_changes}.
    """
    return _safe(sdk.session_context)


@mcp.tool()
def tortoise_ingest_corpus(directory: str) -> dict:
    """Batch document ingestion — walk directory, parse YAML frontmatter
    from .md files, create/update Document nodes.
    Returns {ingested, updated, skipped}.
    """
    return _safe(sdk.ingest_corpus, directory)

# ── Taxonomy ─────────────────────────────────────────────────

@mcp.tool()
def tortoise_taxonomy() -> dict[str, int]:
    """Count entities by node label. Returns {Point: N, Event: N, Subject: N, Object: N, Document: N}."""
    return _safe(sdk.taxonomy)


@mcp.tool()
def tortoise_list_topics(entity_id: str) -> dict:
    """entityProfile lite for an entity. Returns {id, pointKind, neighbors, neighborCounts}."""
    return _safe(sdk.list_topics, entity_id)


# ── Graph Analysis ──────────────────────────────────────────────

@mcp.tool()
def tortoise_analyze(question: str,
                    entityId: str | None = None,
                    anchor_ids: Any = None,
                    max_hops: int = 1,
                    rel_filter: str = "IMPL|NAND",
                    direction: str = "both") -> dict:
    """Answer natural language questions about the Tortoise epistemic graph.

    Ask things like: "where is the disagreement?" "what supports claim X?"
    "what are we most uncertain about?" "show me the evidence chain for Y."

    Optional entityId scopes the analysis to a specific entity's subgraph.
    Optional anchor_ids (list of Point IDs) scopes via BFS subgraph selection.
    max_hops: BFS depth from anchors (default 1).
    rel_filter: edge types — "IMPL", "NAND", or "IMPL|NAND" (default).
    direction: IMPL traversal — "incoming", "outgoing", or "both" (default).
    Returns {"answer": "...", "raw": [...], "pattern": "...", "query": "..."}
    """
    from tortoise.analyze import analyze
    from tortoise.navigation import entityProfile

    anchor_ids = _parse(anchor_ids)

    entity_subgraph_ids = None
    if entityId:
        try:
            proj = sdk._get_proj()
            profile = entityProfile(proj.db, "tortoise", entityId, hops=2)
            ids = {entityId}
            for category in profile.get("connected", {}).values():
                for node in category:
                    if node.get("id"):
                        ids.add(node["id"])
            entity_subgraph_ids = ids
        except Exception:
            pass  # fall back to full-graph analysis

    return analyze(question, sdk._get_proj(),
                   entity_subgraph_ids=entity_subgraph_ids,
                   anchor_ids=anchor_ids,
                   max_hops=max_hops,
                   rel_filter=rel_filter,
                   direction=direction)


# ── P1-3: Staleness Detection ─────────────────────────────────

@mcp.tool()
def tortoise_stale(days: int = 30, limit: int = 50) -> dict:
    """Find Points not updated in N days. Returns {stale, count, cutoff, limit}."""
    return _safe(sdk.stale_points, days=days, limit=limit)


@mcp.tool()
def tortoise_provenance(point_id: str) -> dict:
    """Provenance chain — "Who decided this?" Follows authoredBy → Subject → delegation."""
    return _safe(sdk.provenance, point_id)


# ── Multi-tenancy (#7001) ────────────────────────────────────

@mcp.tool()
def tortoise_team_create(name: str) -> dict:
    """Create isolated team graph via FalkorDB select_graph.
    Generates a per-team API key. Returns {name, graph_name, api_key, id}.
    destructiveHint=true — creates persistent resources.
    idempotentHint=false — duplicate team names raise an error.
    """
    return _safe(sdk.team_create, name)


# ── Entity CRUD (ONTOLOGY v2.5) ───────────────────────────────

@mcp.tool()
def tortoise_create_subject(name: str, subjectKind: str, props: Any = None) -> dict:
    """Create a Subject node (team, role, organization, person)."""
    props = _parse(props)
    return _safe(sdk.create_subject, name, subjectKind, **(props or {}))

@mcp.tool()
def tortoise_create_object(name: str, objectKind: str, props: Any = None) -> dict:
    """Create an Object node (product, customer, skill, etc.)."""
    props = _parse(props)
    return _safe(sdk.create_object, name, objectKind, **(props or {}))

@mcp.tool()
def tortoise_create_event(name: str, eventKind: str, props: Any = None) -> dict:
    """Create an Event node (meeting, decision, deployment, etc.)."""
    props = _parse(props)
    return _safe(sdk.create_event, name, eventKind, **(props or {}))

@mcp.tool()
def tortoise_create_document(title: str, documentKind: str, props: Any = None) -> dict:
    """Create a Document node (research, planDoc, meetingNotes, etc.)."""
    props = _parse(props)
    return _safe(sdk.create_document, title, documentKind, **(props or {}))

@mcp.tool()
def tortoise_create_source(url: str, sourceKind: str, props: Any = None) -> dict:
    """Create a Source node for provenance (document, web, db, etc.).

    Sources track content origin — url is the permalink key.
    Points link to Sources via extractedFrom edge (Ontology v2.5).
    """
    props = _parse(props)
    return _safe(sdk.create_source, url, sourceKind, **(props or {}))

@mcp.tool()
def tortoise_get_entity(id: str) -> dict:
    """Get any entity by ID, eventId, or url."""
    return _safe(sdk.get_entity, id)

@mcp.tool()
def tortoise_update_entity(id: str, props: Any = None) -> dict:
    """Update any entity's properties."""
    props = _parse(props)
    return _safe(sdk.update_entity, id, **(props or {}))

@mcp.tool()
def tortoise_delete_entity(id: str) -> bool:
    """Delete any entity by ID."""
    return _safe(sdk.delete_entity, id)

@mcp.tool()
def tortoise_create_edge(source_id: str, target_id: str, predicate: str) -> bool:
    """Create an edge between two entities. Predicate: performs, produces, ownedBy, managedBy, etc."""
    return _safe(sdk._get_proj().create_edge, source_id, target_id, predicate)

@mcp.tool()
def tortoise_get_governance(subject_id: str) -> list:
    """Get all entities owned by a Subject."""
    return _safe(sdk.get_owned_entities, subject_id)

@mcp.tool()
def tortoise_backfill_v25(dry_run: bool = True) -> dict:
    """Backfill database to ONTOLOGY v2.5 schema."""
    return _safe(sdk.backfill_v25, dry_run=dry_run)
