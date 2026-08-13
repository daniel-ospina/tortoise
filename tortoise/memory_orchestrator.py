"""Memory Orchestrator — cross-ontology query routing and merge.

Phase A: episodic + epistemic. Phase B: add semantic + docIndex (add 2 entries to dicts).
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from typing import Any

# ── Data ──────────────────────────────────────────────

ROUTING_TABLE_WRITE: dict[str, list[str]] = {
    "episodic":  ["EventRecorded", "SubjectAdded", "ActionPerformed"],
    "epistemic": ["PointAdded", "OperatorAdded", "PointRevised"],
    "semantic":  ["DocumentCreated", "SubjectAdded", "ObjectRegistered"],
    "docIndex":  ["DocumentCreated", "DocumentUpdated"],
}

ROUTING_TABLE_READ: dict[str, list[str]] = {
    "whatHappened": ["episodic"],
    "timeline":     ["episodic"],
    "beliefs":      ["epistemic"],
    "claimsAbout":  ["epistemic"],
    "entityLookup": ["semantic"],
    "findDocs":     ["docIndex"],
}

# ponytail: one Cypher template per ontology. Extend here for Phase B.
ONTOLOGY_CYPHER: dict[str, str] = {
    "episodic":  "MATCH (e:Event) RETURN e ORDER BY e.startedAt DESC LIMIT 50",
    "epistemic": "MATCH (p:Point) RETURN p ORDER BY p.createdAt DESC LIMIT 50",
    "semantic":  "MATCH (n) WHERE n:Subject OR n:Object RETURN n LIMIT 50",
    "docIndex":  "MATCH (d:Document) RETURN d ORDER BY d.createdAt DESC LIMIT 50",
}


@dataclass
class MergeResult:
    results: list[dict] = field(default_factory=list)
    byOntology: dict[str, list[dict]] = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)
    failedOntologies: list[str] = field(default_factory=list)

    @property
    def totalByOntology(self) -> dict[str, int]:
        return {ont: len(rows) for ont, rows in self.byOntology.items()}

    @property
    def mergedCount(self) -> int:
        return len(self.results)


class OrchestratorError(Exception):
    """All ontologies failed. Caller can catch specifically vs RuntimeError."""
    def __init__(self, failures: dict[str, str]):
        self.failures = failures
        super().__init__(f"All ontologies failed: {failures}")


# ── Routing ───────────────────────────────────────────

def routeWrite(eventType: str) -> list[str]:
    """Return ontologies that handle this event type."""
    return [ont for ont, types in ROUTING_TABLE_WRITE.items() if eventType in types]


def routeRead(patterns: list[str]) -> list[str]:
    """Return deduplicated union of ontologies for multiple patterns."""
    ontologies: set[str] = set()
    for p in patterns:
        ontologies.update(ROUTING_TABLE_READ.get(p, []))
    return list(ontologies)


# ── NL Translation ─────────────────────────────────────

# ponytail: keyword classifier. Replace with LLM dispatch when accuracy < 80%.
PATTERN_KEYWORDS: dict[str, list[str]] = {
    "whatHappened": ["what happened", "what did", "show events", "recent"],
    "timeline":     ["timeline", "chronology", "sequence", "history of"],
    "beliefs":      ["what do we believe", "what is our position", "current thinking"],
    "claimsAbout":  ["claims about", "what about", "tell me about", "decisions about"],
    "entityLookup": ["who is", "what is", "entity", "lookup"],
    "findDocs":     ["find docs", "documents about", "where is document", "show me docs"],
}


def translateNL(query: str) -> tuple[list[str], dict[str, str]]:
    """NL query → (patterns, filters). Keyword-based classifier.

    Returns:
        patterns: matched pattern names (empty if no keywords match)
        filters: always empty dict in Phase A (filter extraction deferred to Phase B)
    """
    q = query.lower()
    patterns: list[str] = []
    for pattern, keywords in PATTERN_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            patterns.append(pattern)
    return patterns, {}  # ponytail: filter extraction deferred to Phase B


# ── Dispatch ──────────────────────────────────────────

def dispatch(
    ontologies: list[str],
    db: Any,
    timeout: float = 5.0,
    cypherOverrides: dict[str, str] | None = None,
    cypherParams: dict | None = None,
) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Parallel dispatch to ontologies.

    Returns:
        (results, errors) — successful results keyed by ontology,
        errors keyed by ontology with error string.
    """
    cypherMap = {**ONTOLOGY_CYPHER, **(cypherOverrides or {})}
    results: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}

    if not ontologies:
        return results, errors

    import time
    deadline = time.monotonic() + timeout

    # #331: explicit executor (not `with`) — ThreadPoolExecutor.__exit__
    # calls shutdown(wait=True), which BLOCKS until every in-flight query
    # finishes. A hung ontology query would then defeat the per-future
    # deadline entirely (timeout was fake). wait=False lets the deadline
    # actually bound dispatch.
    #
    # Known trade-off (code-review r2): a timed-out query's worker thread
    # keeps running until the query returns — ThreadPoolExecutor workers
    # are non-daemon and concurrent.futures' atexit hook joins them, so a
    # query that NEVER returns delays interpreter shutdown. cancel_futures
    # drops queued-but-unstarted work; the FalkorDB client's socket timeout
    # (docker mode) is what bounds the running ones.
    ex = ThreadPoolExecutor(max_workers=len(ontologies))
    try:
        # Submit all futures in parallel, then wait per-future with timeout
        pending: dict[str, Future] = {}
        for ont in ontologies:
            if ont not in cypherMap:
                errors[ont] = "no Cypher template"
                continue
            pending[ont] = ex.submit(_queryOntology, db, ont, cypherMap[ont], cypherParams)

        for ont, f in pending.items():
            # #331 (review r5): clamp remaining to >= 0 instead of
            # shortcutting — f.result(timeout=0) returns an
            # already-completed future immediately, so a fast ontology
            # listed after a slow one keeps its result instead of being
            # misreported as a timeout (order-sensitive data loss).
            remaining = max(deadline - time.monotonic(), 0.0)
            try:
                results[ont] = f.result(timeout=remaining)
            except TimeoutError:
                errors[ont] = "timeout"
            except Exception as e:
                errors[ont] = str(e)
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    if not results and errors:
        raise OrchestratorError(errors)
    return results, errors


def _queryOntology(db: Any, ontology: str, cypher: str, params: dict | None = None) -> list[dict]:
    """Execute Cypher against ontology graph.

    Returns list of flat entity dicts with 'id', 'type', and properties.
    Connection/auth errors propagate to dispatch error handling.
    """
    g = db.select_graph(ontology)
    if params:
        rows = g.query(cypher, params=params).result_set
    else:
        rows = g.query(cypher).result_set
    if not rows:
        return []
    return [_parseNode(row[0]) for row in rows]


def _parseNode(node: Any) -> dict:
    """Parse FalkorDB Node → flat dict {id, type, ...properties}.

    FalkorDB 1.6+ returns Node objects with .id, .labels, .properties.
    Also handles raw list form [id, [labels], [[k,v],...]] for testing.
    Malformed node shapes (None, short lists, junk) degrade to a dict
    with unknown id/type instead of crashing the dispatch (#331).
    """
    if hasattr(node, 'properties'):
        # FalkorDB Node object
        props = dict(node.properties)
        props["id"] = str(node.id)
        props["type"] = node.labels[0] if node.labels else "unknown"
        return props
    # Dict-shaped node: preserve any existing keys — only fill id/type
    # when absent (code-review r2: unconditional overwrite corrupted
    # valid dict nodes into id='unknown').
    if isinstance(node, dict):
        props = dict(node)
        props.setdefault("id", "unknown")
        props.setdefault("type", "unknown")
        return props
    # Raw list form: [id, [labels], [[k, v], ...]]
    props: dict[str, Any] = {}
    if (isinstance(node, (list, tuple)) and len(node) >= 3
            and isinstance(node[2], (list, tuple))):
        for pair in node[2]:
            # Only well-formed [k, v] pairs with a hashable key survive;
            # anything else is malformed and skipped (e.g.
            # ['bad','pair','extra'], 'junk', [[...], v] — code-review r2:
            # an unhashable key raised TypeError mid-dispatch).
            if (isinstance(pair, (list, tuple)) and len(pair) == 2
                    and isinstance(pair[0], (str, int, float, bool))):
                props[pair[0]] = pair[1]
    node_id = (node[0] if isinstance(node, (list, tuple)) and len(node) >= 1
               else None)
    labels = (node[1] if isinstance(node, (list, tuple)) and len(node) >= 2
              else None)
    props["id"] = str(node_id) if node_id is not None else "unknown"
    props["type"] = (labels[0]
                     if isinstance(labels, (list, tuple)) and labels
                     else "unknown")
    return props


# ── Merge ─────────────────────────────────────────────

def merge(
    results: dict[str, list[dict]],
    errors: dict[str, str],
) -> MergeResult:
    """Union results with provenance tagging, entity dedup, conflict detection.

    Args:
        results: {ontology: [entity dicts]} from dispatch
        errors: {ontology: error string} from dispatch

    Returns:
        MergeResult with provenance-tagged, deduplicated results and failed ontologies
    """
    seen: dict[str, dict] = {}   # entity ID → merged result

    for ont, rows in results.items():
        for row in rows:
            eid = row.get("id")
            if not eid:
                continue
            if eid in seen:
                existing = seen[eid]
                existing["sourceOntologies"].append(ont)
                for key, val in row.items():
                    if key in ("id", "type"):
                        continue
                    if key in existing["properties"] and existing["properties"][key] != val:
                        existing["conflict"] = True
                        if existing["conflictDetail"] is None:
                            existing["conflictDetail"] = {}
                        if key not in existing["conflictDetail"]:
                            existing["conflictDetail"][key] = [existing["properties"][key]]
                        existing["conflictDetail"][key].append(val)
                    existing["properties"].setdefault(key, val)
            else:
                seen[eid] = {
                    "id": eid,
                    "type": row.get("type", _inferType(ont)),
                    "properties": {k: v for k, v in row.items() if k not in ("id", "type")},
                    "sourceOntologies": [ont],
                    "conflict": False,
                    "conflictDetail": None,
                }

    # Collect conflict entities
    conflictList = [v for v in seen.values() if v["conflict"]]

    return MergeResult(
        results=list(seen.values()),
        byOntology=results,
        conflicts=conflictList,
        failedOntologies=list(errors.keys()),
    )


def _inferType(ontology: str) -> str:
    """Infer entity type from ontology name (aligned with ONTOLOGY §2.2 kind tags)."""
    return {"episodic": "Event", "epistemic": "Point",
            "semantic": "Entity", "docIndex": "Document"}.get(ontology, "unknown")


def _entityAnchoredCypher(entityId: str, ontologies: list[str]) -> dict[str, str]:
    """Generate entity-anchored Cypher overrides for each ontology.

    Injects WHERE clause filtering to the given entityId into each ontology's
    default Cypher template.
    """
    import re
    overrides: dict[str, str] = {}
    for ont in ontologies:
        base = ONTOLOGY_CYPHER.get(ont)
        if not base:
            continue
        # Extract node variable from MATCH (var) or MATCH (var:Label)
        m = re.match(r'MATCH\s*\((\w+)', base)
        if m:
            var = m.group(1)
            cypher = base
            if "WHERE" in cypher:
                cypher = cypher.replace("WHERE ", f"WHERE {var}.id = $entityId AND ", 1)
            elif "RETURN" in cypher:
                cypher = cypher.replace("RETURN ", f"WHERE {var}.id = $entityId RETURN ", 1)
            else:
                cypher = cypher.replace(f"MATCH ({var}", f"MATCH ({var} {{id: $entityId}}")
            overrides[ont] = cypher
    return overrides


# ── Entry Point ───────────────────────────────────────

def crossOntologyQuery(
    query: str,
    db: Any,  # raw FalkorDB client (NOT FalkorProjection — callers pass projection.db)
    patterns: list[str] | None = None,
    timeout: float = 5.0,
    entityId: str | None = None,
) -> MergeResult:
    """Agent-facing entry point: NL query → integrated result.

    Modes:
      - NL mode (default): query → translateNL → route → dispatch
      - Structured mode: patterns provided → skips translateNL, routes directly

    Args:
        query: Natural language query (e.g. "what decisions about competitor X?")
        db: Raw FalkorDB client (NOT FalkorProjection wrapper)
        patterns: Optional explicit patterns (bypasses translateNL when provided)
        timeout: Per-ontology timeout in seconds
        entityId: Optional entity ID — when provided, injects WHERE clause 
                  filtering each sub-query to this entity (backward compatible)

    Returns:
        MergeResult with provenance-tagged, deduplicated results.
    """
    if patterns is None:
        patterns, _ = translateNL(query)

    if not patterns:
        # Unrecognised NL queries default to epistemic ("beliefs" pattern).
        # ponytail: return empty MergeResult instead when consumers need
        # to distinguish "no match" from "no data".
        patterns = ["beliefs"]

    ontologies = routeRead(patterns)
    if not ontologies:
        ontologies = ["epistemic"]

    cypherOverrides: dict[str, str] | None = None
    cypherParams: dict | None = None
    if entityId:
        cypherOverrides = _entityAnchoredCypher(entityId, ontologies)
        cypherParams = {"entityId": entityId}
    results, errors = dispatch(ontologies, db, timeout=timeout,
                               cypherOverrides=cypherOverrides,
                               cypherParams=cypherParams)
    return merge(results, errors)


# ── DomainRouter — dynamic orchestrator for N ontologies ──

class DomainRouter:
    """Wraps base 4-ontology routing with domain extension routing.

    Domain ontologies register query patterns + event types via the
    domain manifest (config/domain_manifest.yaml, loaded by domain_loader).
    Domain routing is merged with base routing — domain queries route to
    domain ontologies, unrecognised patterns fall back to base routing.

    Usage:
        router = DomainRouter()                     # auto-loads manifest
        router = DomainRouter(domains={...})        # explicit config
        result = router.crossDomainQuery(
            ["product-strategy"], "show me all JTBD", db
        )
    """

    def __init__(self, manifest_path: str | None = None,
                 domains: dict[str, "DomainRoutingConfig"] | None = None):
        # Base routing tables (always present)
        self._write_routing: dict[str, list[str]] = dict(ROUTING_TABLE_WRITE)
        self._read_routing: dict[str, list[str]] = dict(ROUTING_TABLE_READ)
        self._cypher_templates: dict[str, str] = dict(ONTOLOGY_CYPHER)
        self._timeouts: dict[str, float] = {}
        self._priorities: dict[str, int] = {}

        # Load domain config
        if domains is None and manifest_path is not None:
            from tortoise.domain_loader import load_manifest
            domains = load_manifest(manifest_path)
        elif domains is None:
            from tortoise.domain_loader import load_manifest
            domains = load_manifest()  # default path

        for key, cfg in (domains or {}).items():
            self.register_domain(key, cfg)

    def register_domain(self, key: str, cfg: "DomainRoutingConfig") -> None:
        """Register a domain ontology's routing configuration.

        Merges domain event types, query patterns, Cypher templates,
        timeouts, and priorities into the router.
        """
        # Write-side: event types → domain
        self._write_routing[key] = list(cfg.event_types)

        # Read-side: each query pattern maps to this domain
        for pattern in cfg.query_patterns:
            if pattern in self._read_routing:
                self._read_routing[pattern].append(key)
            else:
                self._read_routing[pattern] = [key]

        # Cypher template
        if cfg.cypher_template:
            self._cypher_templates[key] = cfg.cypher_template

        # Per-domain config
        if cfg.timeout:
            self._timeouts[key] = cfg.timeout
        if cfg.priority:
            self._priorities[key] = cfg.priority

    # ── Routing (merged base + domain) ────────────────

    def routeWrite(self, eventType: str) -> list[str]:
        """Return ontologies (base + domain) that handle this event type."""
        return [ont for ont, types in self._write_routing.items() if eventType in types]

    def routeRead(self, patterns: list[str]) -> list[str]:
        """Return deduplicated union of ontologies for multiple patterns."""
        ontologies: set[str] = set()
        for p in patterns:
            ontologies.update(self._read_routing.get(p, []))
        return sorted(ontologies, key=lambda o: self._priorities.get(o, 10))

    # ── Domain-specific query ─────────────────────────

    def crossDomainQuery(
        self,
        domains: list[str],
        query: str,
        db: Any,
        timeout: float | None = None,
    ) -> MergeResult:
        """Query one or more domain ontologies directly.

        Routes to domain-specific Cypher templates, bypassing NL translation.
        Unknown domains are silently skipped.

        Args:
            domains: Domain keys (e.g. ["product-strategy"])
            query: NL query (unused for dispatch; future: filter injection)
            db: FalkorDB client
            timeout: Per-ontology timeout (overrides per-domain config)

        Returns:
            MergeResult with domain-ontology results.
        """
        known = [d for d in domains if d in self._cypher_templates]
        if not known:
            return MergeResult()

        timeout_per = timeout or max(
            (self._timeouts.get(d, 5.0) for d in known), default=5.0
        )
        results, errors = dispatch(known, db, timeout=timeout_per,
                                   cypherOverrides=self._cypher_templates)
        return merge(results, errors)

    # ── Full cross-ontology query (base + domain) ─────

    def crossOntologyQuery(
        self,
        query: str,
        db: Any,
        patterns: list[str] | None = None,
        timeout: float = 5.0,
        entityId: str | None = None,
    ) -> MergeResult:
        """Agent-facing entry point: NL query → integrated result.

        Uses merged (base + domain) routing. Same semantics as the
        module-level crossOntologyQuery(), but with domain routing merged in.
        """
        if patterns is None:
            patterns, _ = translateNL(query)

        if not patterns:
            patterns = ["beliefs"]

        ontologies = self.routeRead(patterns)
        if not ontologies:
            ontologies = ["epistemic"]

        cypherOverrides: dict[str, str] | None = None
        cypherParams: dict | None = None
        if entityId:
            cypherOverrides = _entityAnchoredCypher(entityId, ontologies)
            cypherParams = {"entityId": entityId}
        results, errors = dispatch(ontologies, db, timeout=timeout,
                                   cypherOverrides={**self._cypher_templates,
                                                     **(cypherOverrides or {})},
                                   cypherParams=cypherParams)
        return merge(results, errors)


# ── Module-level crossDomainQuery (convenience) ───────

def crossDomainQuery(
    domains: list[str],
    query: str,
    db: Any,
    timeout: float | None = None,
) -> MergeResult:
    """Query domain ontologies directly (without full NL routing).

    Convenience wrapper that creates a DomainRouter and dispatches.
    For repeated use, instantiate DomainRouter directly.
    """
    router = DomainRouter()
    return router.crossDomainQuery(domains, query, db, timeout=timeout)
