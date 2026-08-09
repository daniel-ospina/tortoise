"""TORT-MCP-001: MCP server wrapping TortoiseSDK. Stdio transport, ~10 tools."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from tortoise.auth import is_dev_mode as _is_dev_mode
from tortoise.sdk import TortoiseSDK
from tortoise import monitoring
from tortoise.mcp_auth import (_current_team_id, _transport_mode, _get_team_sdk,
                               HTTP_ALLOWED, ERR_EXCLUDED)

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
            # python-dotenv semantics: quoted values are literal (no inline
            # comment stripping); unquoted values strip inline comments
            # (whitespace + '#'). A bare '#' in an unquoted value is preserved.
            value = value.strip()
            if value[:1] in ('"', "'") and value[-1:] == value[:1]:
                value = value[1:-1]
            else:
                value = value.split(" #", 1)[0].strip()
            # Only fill keys that are absent — never override an explicitly
            # set (even empty) environment variable.
            if key and key not in os.environ:
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

# ── Lazy SDK initialization (#451) ─────────────────────────────────
# sdk is None at import time — _get_sdk() lazily resolves and connects
# on first call. Prevents import-time network I/O (3x retry + sys.exit)
# in environments without a live FalkorDB server.
_sdk = None
sdk = None  # module-level override point (test swap pattern: mcp_mod.sdk = test_sdk)


def _get_sdk():
    """Lazily resolve TORTOISE_DB_URI, connect, and return TortoiseSDK.

    Cached after first successful call. URI branches + 3x Docker retry
    + sys.exit(1) on exhaustion are preserved exactly — but deferred
    from import time to first tool call (or first call to main()).
    The module-level ``sdk`` attribute acts as an override (set by
    test_enumeration_surfaces.py swap pattern) — when non-None it is
    returned directly, bypassing lazy init.

    Error surface: exceptions here (connection failure, sys.exit on retry
    exhaustion) propagate BEFORE _safe() wrapping in tool bodies (call
    arguments are evaluated first). In normal operation main() calls
    _get_sdk() before mcp.run(), so failures surface at server startup —
    equivalent to the pre-#451 import-time behavior. Only callers that
    invoke mcp.run() directly without main() see an unwrapped error.

    Reset semantics: restoring ``sdk = None`` after a test swap falls
    through to the CACHED _sdk instance — it does not re-connect. Set
    both ``sdk`` and ``_sdk`` to None to force re-initialization.
    """
    global _sdk
    # Module-level sdk override (test swap pattern) takes priority
    if sdk is not None:
        return sdk
    if _sdk is not None:
        return _sdk

    _db_uri = os.environ.get("TORTOISE_DB_URI", "")
    if _db_uri.startswith(("docker://", "redis://", "rediss://")):
        from tortoise.projection import FalkorProjection
        import time as _time
        _sdk = TortoiseSDK()
        # Retry Docker connection 3x with backoff; exit on exhaustion (#25 P3a, #32)
        for attempt in range(3):
            try:
                _sdk._proj = FalkorProjection.from_uri(_db_uri)
                if attempt > 0:
                    _log.warning("Docker connection succeeded on attempt %d", attempt + 1)
                break
            except Exception as e:
                if attempt < 2:
                    _log.warning("Docker connection attempt %d failed: %s — retrying in 2s", attempt + 1, e)
                    _time.sleep(2)
                else:
                    _log.error("Docker connection failed after 3 attempts. Set TORTOISE_DB_URI or ensure FalkorDB is running.")
                    sys.exit(1)
    elif _db_uri:
        # File path — use Lite mode (backward compat: bare non-docker URI).
        # resolve_db_path() rejects relative paths + applies canonical default.
        from tortoise.config import resolve_db_path as _resolve_db_path
        _sdk = TortoiseSDK(db_path=_resolve_db_path(_db_uri))
    else:
        # No URI: default to canonical embedded path via resolve_db_path()
        from tortoise.config import resolve_db_path as _resolve_db_path
        _sdk = TortoiseSDK(db_path=_resolve_db_path())
    return _sdk

# Announce auth mode at startup
if _is_dev_mode():
    _log.warning("TORTOISE_API_KEY not set — running in dev mode (no auth)")


# #329: node/edge-creating MCP write tools that MUST be quota-gated. Completeness
# is enforced by an introspective test (tests/test_mcp_http.py) that scans every
# HTTP_ALLOWED tool body for node/edge-creating SDK calls and asserts membership.
# New node/edge-creating tools MUST be added here.
_QUOTA_GATED: frozenset[str] = frozenset({
    "tortoise_create_point", "tortoise_create_operator", "tortoise_create_event",
    "tortoise_create_subject", "tortoise_create_object", "tortoise_create_document",
    "tortoise_create_source", "tortoise_checkpoint", "tortoise_file_decision",
    "tortoise_update_entity", "tortoise_update_point", "tortoise_diary_write",
    "tortoise_mitigate_operator",
    # edge-creating tools — edge growth is the same graph-flood family
    "tortoise_create_edge", "tortoise_supersede", "tortoise_invalidate",
    # delegates to hosted_api._seed_demo_graph (creates the 4-layer demo graph)
    "tortoise_onboarding_demo_create",
    # #684: node-creating tools that were missed in the original #329 audit
    "tortoise_file_human_approval",  # creates Event + decision Point + IMPL edges
    "tortoise_assess_source",        # creates assessment Point
})


# #329: per-team per-minute LLM-call budget for tortoise_analyze (operator LLM
# keys back outbound calls; the rate limiter alone is not the bound).
_ANALYZE_LLM_BUDGET: dict[str, list[float]] = {}


def _analyze_llm_budget_available() -> bool:
    """True if this team still has analyze LLM budget this minute (HTTP only).

    Beyond budget the tool degrades to keyword-only classification (no paid
    outbound call). Stdio (no team context) is not budgeted.
    """
    import time as _t
    from tortoise.mcp_auth import _current_team_id
    from tortoise.quota import MAX_ANALYZE_LLM_PER_MIN
    team_id = _current_team_id.get()
    if not team_id:
        return True  # stdio/operator — no team budget accounting
    now_ts = _t.time()
    bucket = _ANALYZE_LLM_BUDGET.setdefault(team_id, [])
    bucket[:] = [ts for ts in bucket if now_ts - ts < 60]
    # prune -> check -> append (never pop between check and append — that
    # orphans the appended timestamp and silently disables the budget)
    if len(bucket) >= MAX_ANALYZE_LLM_PER_MIN:
        return False
    bucket.append(now_ts)
    return True


def _enforce_quota(resource: str = "points") -> None:
    """#329: fail-closed team quota pre-write for MCP write tools.

    HTTP mode: limits come from the middleware-resolved ContextVar (same
    limits REST sees); fallback resolves from the registry. Stdio mode
    (no team context) → skip — operator/trusted (batch caps still apply).
    """
    from tortoise.mcp_auth import _current_team_id, _current_team_limits
    from tortoise.quota import enforce_team_limit, resolve_team_limits
    team_id = _current_team_id.get()
    if not team_id:
        return  # stdio/operator — no team context
    limits = _current_team_limits.get()
    if limits is None:
        limits = resolve_team_limits(team_id)
    # Count on the SAME team SDK the tool writes to (identical connection),
    # so the count and the write can never target different databases.
    enforce_team_limit(limits, resource, sdk=_get_team_sdk())


def _quota_gated(fn, resource: str = "points"):
    """Wrap a bound SDK method with a pre-write quota check.

    Preserves the bound-callable style (_safe(_get_team_sdk().name, ...)):
    the quota check runs INSIDE _safe's try so errors surface as structured
    error dicts (see _safe's QuotaExceededError/QuotaCheckError mapping).
    """
    def _gated(*args, **kwargs):
        _enforce_quota(resource)
        return fn(*args, **kwargs)
    return _gated


def _safe(fn, *args, **kwargs):
    """Call fn; return error dict on exception instead of raising.

    #329: QuotaExceededError → {"error", "code": ERR_QUOTA}; QuotaCheckError
    → {"error", "code": ERR_QUOTA_SERVER} (fail-closed counting).

    Transport-aware auth gate (#236). Fail-closed: if _transport_mode is None
    (unset/misconfigured) ALL operations reject. HTTP mode trusts transport-level
    auth (TeamResolutionMiddleware 401'd pre-dispatch). Stdio mode keeps the
    dev-mode gate. NEVER depends on is_dev_mode() alone — it returns True in
    hosted production (TORTOISE_API_KEY unset), which would silently bypass auth.
    """
    mode = _transport_mode.get()
    if mode is None:
        return {
            "error": (
                "Authentication required. MCP transport mode not initialized."
            )
        }
    if mode == "http":
        pass  # auth enforced at transport (TeamResolutionMiddleware)
    elif mode == "stdio":
        if not _is_dev_mode():
            return {
                "error": (
                    "Authentication required. The MCP stdio transport cannot "
                    "carry auth tokens. Use an authenticated HTTP endpoint "
                    "(tortoise health-server) with Authorization: Bearer <key> "
                    "header, or unset TORTOISE_API_KEY for dev mode."
                )
            }
    else:
        # Unknown transport mode — fail-closed (code-review fix)
        return {"error": f"Unknown MCP transport mode: {mode!r}"}
    try:
        result = fn(*args, **kwargs)
        return result
    except Exception as e:
        monitoring.record_error()
        from tortoise.quota import QuotaCheckError, QuotaExceededError
        if isinstance(e, QuotaExceededError):
            return {"error": str(e), "code": ERR_QUOTA}
        if isinstance(e, QuotaCheckError):
            return {"error": str(e), "code": ERR_QUOTA_SERVER}
        msg = str(e)
        # Sanitize: strip hostnames, ports, passwords from error messages (#43)
        import re
        msg = re.sub(r'://[^@]*@', '://***@', msg)  # password in URI
        msg = re.sub(r'(host=|at |to )[\w.-]+(:\d+)?', r'\1***', msg)  # host:port
        return {"error": msg}


def _scrub_analyze_answer(answer: str) -> str:
    """#329: boundary scrub for analyze() answers — strip common internals.

    analyze() already redacts its own error paths; this is defense-in-depth
    against future regressions (paths, hostnames, credentials).
    """
    import re
    answer = re.sub(r"://[^@\s]*@", "://***@", answer)
    answer = re.sub(r"(?P<pre>[/\\])\w+\.(?:db|jsonl|log)(?=[\"'\s,)])", r"\g<pre>***", answer)
    return answer[:2000]


# #329: quota error codes (registered alongside the other ERR_* in mcp_auth).
ERR_QUOTA = -32006
ERR_QUOTA_SERVER = -32007


def _http_excluded_error() -> dict:
    """#236: JSON-RPC error for tools excluded from the tenant HTTP surface (D4)."""
    return {
        "jsonrpc": "2.0",
        "error": {
            "code": ERR_EXCLUDED,
            "message": "This tool is not available over HTTP. "
                        "Use the hosted REST API or stdio MCP.",
        },
        "id": None,
    }


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
    # #329 tag batch cap + value validation
    from tortoise.quota import MAX_TAGS_PER_POINT
    tags = merged.get("tags") or []
    if isinstance(tags, list):
        if len(tags) > MAX_TAGS_PER_POINT:
            return {"error": f"tags exceed the cap ({MAX_TAGS_PER_POINT})", "code": ERR_QUOTA}
        for t in tags:
            if not isinstance(t, str) or not t.strip() or len(t) > 200:
                return {"error": f"invalid tag value: {t!r} (must be a non-empty string ≤ 200 chars)"}
    merged["dedup"] = dedup
    return _safe(_quota_gated(_get_team_sdk().create_point, "points"), kind, content, **merged)


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
        return _safe(_get_team_sdk().tortoise_fts_query, text, kind=kind,
                     entity_type=entity_type, limit=limit,
                     min_confidence=min_confidence or 0.0,
                     order_by=order_by or "relevance")
    result = _safe(_get_team_sdk().query, kind, **(filters or {}))
    # If empty results and a kind filter was provided, attach suggestion
    if isinstance(result, list) and len(result) == 0 and kind is not None:
        from tortoise.query_suggestions import compute_suggestion
        suggestion = compute_suggestion(kind)
        if suggestion:
            return {"results": result, "suggestion": suggestion}
    return result


def tortoise_paginated_query(kind: str | None = None,
                             skip: int = 0, limit: int = 20,
                             filters: Any = None) -> dict:
    """Query points with SKIP/LIMIT pagination. Returns {results, total, hasMore}."""
    filters = _parse(filters)
    return _safe(_get_team_sdk().paginated_query, kind, skip=skip, limit=limit,
                 **(filters or {}))


def tortoise_check_structure() -> list[dict]:
    """Check Gate 0→4 chain integrity (orphans, dangling refs)."""
    return _safe(_get_team_sdk().check_structure)


def tortoise_summarize_structure() -> dict:
    """Count points per Gate (by pointKind). Returns {gateN_*, total}."""
    return _safe(_get_team_sdk().summarize_structure)


def tortoise_list_pointkinds() -> list[dict]:
    """List all pointKinds present in the graph with counts. What EXISTS."""
    return _safe(_get_team_sdk().list_pointkinds)


def tortoise_list_sources() -> list[dict]:
    """List all Sources with point counts. Where data came FROM."""
    return _safe(_get_team_sdk().list_sources)


def tortoise_list_namespaces() -> list[dict]:
    """List installed pack namespaces."""
    return _safe(_get_team_sdk().list_namespaces)


def tortoise_list_tags() -> list[dict]:
    """List all Tag names with count of tagged Points. Where tags are USED."""
    return _safe(_get_team_sdk().list_tags)


def tortoise_query_points_by_tag(tag: str) -> list[dict]:
    """Return Points connected to a Tag via TAGGED edge."""
    return _safe(_get_team_sdk().query_points_by_tag, tag)


def tortoise_get_point(id: str) -> dict:
    """Get a single Point by ID. Returns all properties, or empty dict."""
    return _safe(_get_team_sdk().get_point, id)


# ── Entity Resolution (GAP-01 #6987) ──────────────────────────

def tortoise_suggest_entry_points(query: str, limit: int = 5,
                                  kind_filter: str | None = None) -> list[dict]:
    """Entity resolution — NL query → matching entities from the graph.

    Uses hybrid search (tortoise_fts_query) for semantic entity resolution.
    Falls back to string match (CONTAINS) if hybrid search unavailable.
    Returns [{id, name, kind, confidence}] sorted by confidence DESC.
    """
    try:
        results = _safe(_get_team_sdk().tortoise_fts_query, query, kind=kind_filter, limit=limit)
        if isinstance(results, list) and results and "error" not in results[0]:
            return [{"id": r["id"], "name": r.get("content", ""),
                     "kind": r.get("point_kind", ""),
                     "confidence": round(
                         0.5 * r.get("scores", {}).get("rrf", 0.0) +
                         0.5 * r.get("ep", {}).get("confidence_mean", 0.0), 4)}
                    for r in results]
    except Exception:
        pass
    return _safe(_get_team_sdk().suggest_entry_points, query, limit=limit, kind_filter=kind_filter)


# ── Semantic Search (#6990) ────────────────────────────────────

def tortoise_search(query: str | None = None, kind: str | None = None,
                    threshold: float = 0.0, limit: int = 10,
                    min_confidence: float = 0.0,
                    order_by: str = "relevance",
                    entity_type: str = "point") -> list[dict]:
    """Hybrid search with RRF fusion + EP annotation.

    entity_type: 'point' (default), 'event', 'subject', 'document', 'object', 'operator', or 'source'.
    Full-scan mode: omit query, set kind → all Points of kind.
    Best-match mode: provide query → RRF fusion of FTS + vector + structural.

    Point results annotated with EP breakdown (confidence_mean + variance + contested + contention).
    min_confidence defaults to 0.0 (no filter).

    order_by (#25, #560):
      - 'relevance' (default): pure RRF fusion order (FTS + vector + structural).
      - 'confidence': sort by the PERSISTED EP confidence (n.confidence), not the
        structural edge ratio.
      - 'graph': graph-informed rerank — weighted fusion of similarity +
        persisted EP confidence + operator connectivity + 30-day recency decay
        (tortoise.ranking.GraphRanker). Results annotated with a
        'graph_ranking' breakdown {similarity, graph_boost, recency_boost,
        final_score, variance, contested}.

    Contestation is surfaced, never scored: contested claims carry
    ep.contested=true + ep.variance (real EP posterior variance from persisted
    α/β) but are ranked exactly like any other claim with the same confidence
    (#580/#583).

    Note: threshold default changed from 0.3 (Phase 0 semantic search) to 0.0.
    RRF scores are rank-based (0.01-0.05 range typical), not cosine similarity (0-1).
    Use threshold > 0 to filter out very weak matches; the old 0.3 default would
    reject nearly all RRF results. (#20)
    """
    return _safe(_get_team_sdk().tortoise_fts_query, query, kind=kind,
                 threshold=threshold, limit=limit,
                 entity_type=entity_type,
                 min_confidence=min_confidence, order_by=order_by)


# ── EP Belief Propagation (#6908) ────────────────────────────────

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
    return _safe(_get_team_sdk().compute_confidence, factors, evidence,
                 anchors=anchors,
                 max_hops=max_hops, rel_filter=rel_filter,
                 direction=direction,
                 require_calibration=require_calibration)


def tortoise_set_point_baseline(claim_id: str, alpha: float, beta: float) -> dict:
    """Set Beta prior evidence for a claim."""
    return _safe(_get_team_sdk().set_point_baseline, claim_id, alpha, beta)


def tortoise_get_confidence(claim_id: str) -> dict:
    """Get EP confidence for a claim: {mean, variance, alpha, beta}."""
    return _safe(_get_team_sdk().get_confidence, claim_id)


def tortoise_calibrate_summary() -> list[dict]:
    """Audit graph calibration state. Returns per-point guidance."""
    return _safe(_get_team_sdk().calibrate_summary)


def tortoise_dream(full: bool = False, dirty_only: bool = True,
                   max_hops: int = 2) -> dict:
    """Run EP stabilization (dreaming, #85).

    Stabilizes confidence values after batch writes without an explicit
    compute_confidence call. Default: dreams the accumulated dirty subgraph
    (incremental). Set full=True for whole-graph stabilization.

    #329: EXCLUDED from tenant HTTP — whole-graph EP is CPU-heavy
    (operator/stdio only; REST /v1/dream is separately budgeted).
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    return _safe(_get_team_sdk().dream, dirty_only=dirty_only, full=full,
                 max_hops=max_hops)


def tortoise_update_point(id: str, props: Any) -> dict:
    """Update properties on a Point. Safe — modifies one Point only."""
    props = _parse(props)
    return _safe(_quota_gated(_get_team_sdk().update_point, "points"), id, **(props or {}))

def tortoise_create_operator(op_type: str, source_id: str, target_ids: Any,
                              direction: str = "bidirectional") -> dict:
    """Create an operator connecting Points.
    
    op_type: 'IMPL' (A supports B), 'NAND' (A contradicts B),
             'composedOf'/'decomposesInto'/'contains'/'wraps' → stored as hasPart edge.
    source_id: source/parent Point ID.
    target_ids: target/child Point IDs (1 for IMPL/NAND, N for part/whole).
    direction: 'bidirectional' (default) or 'unidirectional' — EP propagation direction.

    → See /skill:tortoise-graph-reasoning for proper usage:
      annotation, mitigation, NAND constraints, veracity vs implication.
    """
    target_ids = _parse(target_ids)
    # #329 batch cap on operator target fan-out
    from tortoise.quota import MAX_OPERATOR_TARGETS
    if isinstance(target_ids, list) and len(target_ids) > MAX_OPERATOR_TARGETS:
        return {"error": f"create_operator target_ids exceed the cap ({MAX_OPERATOR_TARGETS})",
                "code": ERR_QUOTA}
    return _safe(_quota_gated(_get_team_sdk().create_operator, "points"), op_type, source_id, target_ids,
                 direction=direction)


def tortoise_annotate_operator(id: str, bias: float, precision: float,
                                consistency: float, directness: float) -> dict:
    """Annotate an operator Point with structured epistemic dimensions.

    bias: 0-1 — hidden stake beyond stated position.
    precision: 0-1 — how narrow/well-defined the relevance claim is.
    consistency: 0-1 — stability across contexts.
    directness: 0-1 — how directly source bears on target.
    """
    return _safe(_get_team_sdk().annotate_operator, id, bias, precision, consistency, directness)


def tortoise_get_operator(id: str) -> dict:
    """Get an operator Point by ID. Returns all properties including annotation dimensions.
    Raises error if the Point is not an operator."""
    point = _safe(_get_team_sdk().get_point, id)
    if isinstance(point, dict) and point and not point.get("is_operator"):
        return {"error": f"Point {id!r} is not an operator"}
    return point


def tortoise_mitigate_operator(id: str, reason: str, strength: float = 0.5) -> dict:
    """Create a mitigation Point that modulates an operator's edge strength.

    reason: Why the edge is weaker than it appears.
    strength: 0-1 — 0=fully neutralized, 1=fully intact (default 0.5).
    Idempotent — second call updates existing mitigation.
    """
    return _safe(_quota_gated(_get_team_sdk().mitigate_operator, "points"), id, reason, strength)


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
    # #329 batch caps
    from tortoise.quota import MAX_FILE_DECISION_EVIDENCE, MAX_FILE_DECISION_OPTIONS
    if isinstance(options, list) and len(options) > MAX_FILE_DECISION_OPTIONS:
        return {"error": f"file_decision options exceed the cap ({MAX_FILE_DECISION_OPTIONS})",
                "code": ERR_QUOTA}
    if isinstance(evidence, list) and len(evidence) > MAX_FILE_DECISION_EVIDENCE:
        return {"error": f"file_decision evidence exceeds the cap ({MAX_FILE_DECISION_EVIDENCE})",
                "code": ERR_QUOTA}
    return _safe(_quota_gated(_get_team_sdk().file_decision, "points"), options, evidence, choice)


def tortoise_file_human_approval(approver_id: str, artifact_id: str,
                                 point_ids: Any,
                                 decision_content: str | None = None) -> dict:
    """File a human approval of a planning artifact to the graph (#531).

    Records an Event (eventKind: humanApproval) with full provenance
    (approver, artifact, approved claims), creates a decision Point
    (pointKind: humanApproval) that seeds grounding and carries an EP
    evidence prior, and fans out unidirectional IMPL edges (label
    approvedBy) from the approval Point to the approved claim Points so
    dependent claims strengthen.

    approver_id: Subject id of the human approving
    artifact_id: Object/Document id of the artifact being approved
    point_ids: claim Point ids being approved
    decision_content: optional content override for the decision Point

    Returns {event_id, decision_point_id, impl_operator_ids, confidence_delta}.
    """
    point_ids = _parse(point_ids)
    return _safe(_quota_gated(_get_team_sdk().file_human_approval, "points"),
                 approver_id, artifact_id, point_ids, decision_content)


def tortoise_delete_point(id: str) -> dict:
    """Delete a Point. DESTRUCTIVE — requires human confirmation. Cannot be undone."""
    return _safe(_get_team_sdk().delete_point_wrapped, id)


def tortoise_invalidate(id: str, corrected_by_id: str) -> dict:
    """Mark a Point outdated with a CORRECTS edge from the correcting Point.

    The `corrected_by_id` point CORRECTS the invalidated point.
    Returns {invalidated, id, corrected_by}.
    """
    return _safe(_quota_gated(_get_team_sdk().invalidate_point, "points"), id, corrected_by_id)


def tortoise_supersede(old_id: str, new_id: str) -> dict:
    """Atomically replace old Point with new — CORRECTS edge + outdated flag.

    Equivalent to invalidate(old_id, new_id) — marks old outdated,
    creates CORRECTS edge from new to old.
    Returns {invalidated, id, corrected_by}.
    """
    return _safe(_quota_gated(_get_team_sdk().supersede_point, "points"), old_id, new_id)



# ── Navigation (#6962, #6963, #6964) ─────────────────────────────

def tortoise_entity_profile(entity_id: str, hops: int = 2,
                             graph_name: str = "tortoise",
                             pointKind: str | None = None,
                             confidenceMin: float | None = None) -> dict:
    """Entity-centric traversal — BFS from entity node, categorize connected nodes.

    Returns {entity: {...}, connected: {points, documents, events, subjects, objects}}.
    Optional filters: pointKind, confidenceMin.
    """
    from tortoise.navigation import entityProfile
    proj = _get_team_sdk()._get_proj()
    # #236: HTTP mode ignores user-supplied graph_name — team graph authoritative
    # (cross-tenant injection guard). Stdio mode honors it (operator use).
    if _transport_mode.get() == "http":
        graph_name = f"team_{_current_team_id.get()}"
    return _safe(entityProfile, proj.db, graph_name, entity_id,
                  hops=hops, pointKind=pointKind, confidenceMin=confidenceMin)


def tortoise_traverse(entity_id: str, max_hops: int = 2,
                       graph_name: str = "tortoise") -> dict:
    """Multi-hop graph traversal from entity following ALL relationship types.

    Returns {entity: {...}, nodes: [{node, relationship, depth}, ...]}.
    """
    from tortoise.navigation import tortoise_traverse as _traverse
    proj = _get_team_sdk()._get_proj()
    # #236: HTTP mode ignores user-supplied graph_name (cross-tenant guard)
    if _transport_mode.get() == "http":
        graph_name = f"team_{_current_team_id.get()}"
    return _safe(_traverse, proj.db, graph_name, entity_id, max_hops)


def main():
    _transport_mode.set("stdio")
    monitoring.register(_get_sdk())
    uri = os.environ.get("TORTOISE_DB_URI")
    db_path = os.environ.get("TORTOISE_DB_PATH")
    if not uri and not db_path:
        if os.environ.get("TORTOISE_ALLOW_EMBEDDED") == "1":
            _log.warning(
                "Neither TORTOISE_DB_URI nor TORTOISE_DB_PATH set — running "
                "embedded (empty graph). Test-only escape hatch."
            )
        else:
            _log.error(
                "Neither TORTOISE_DB_URI nor TORTOISE_DB_PATH is set. MCP would "
                "silently connect to an empty embedded DB. Set TORTOISE_DB_URI "
                "(docker://...) or TORTOISE_DB_PATH (canonical embedded path) in "
                "the environment or .env, then restart. "
                "Override with TORTOISE_ALLOW_EMBEDDED=1 (test only)."
            )
            sys.exit(1)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

# ── P0 Group 3: Checkpoint, Diary, Status, Ingest ──────────────

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
    # #329 batch cap: a single checkpoint call must not create unbounded nodes
    from tortoise.quota import MAX_CHECKPOINT_ITEMS
    if isinstance(items, list) and len(items) > MAX_CHECKPOINT_ITEMS:
        return {"error": f"checkpoint items exceed the batch cap ({MAX_CHECKPOINT_ITEMS})",
                "code": ERR_QUOTA}
    return _safe(_quota_gated(_get_team_sdk().checkpoint, "points"), items,
                 agent_name=agent_name, threshold=threshold)


def tortoise_diary_write(agent_name: str, entry: str,
                         topic: str | None = None,
                         wing: str | None = None) -> dict:
    """Write an agent diary entry (AAAK format suggested).
    Creates a Point with pointKind=diary, authoredBy=agent.
    """
    return _safe(_quota_gated(_get_team_sdk().diary_write, "points"), agent_name, entry, topic=topic, wing=wing)


def tortoise_diary_read(agent_name: str, last_n: int = 10,
                        wing: str | None = None) -> list[dict]:
    """Read recent diary entries for an agent, newest first."""
    return _safe(_get_team_sdk().diary_read, agent_name, last_n, wing=wing)


def tortoise_list_graphs() -> list[str]:
    """List graph names. HTTP: only the calling team's own graphs (exact
    team_{team_id} equality — no cross-tenant enumeration). Stdio: full list
    (operator context)."""
    graphs = _safe(_get_team_sdk().list_graphs)
    if not isinstance(graphs, list):
        return graphs
    if _transport_mode.get() == "http":
        from tortoise.mcp_auth import _current_team_id
        team_id = _current_team_id.get()
        own = f"team_{team_id}" if team_id else None
        return [g for g in graphs if own is not None and g == own]
    return graphs


def tortoise_status() -> dict:
    """Graph health + entity counts + FalkorDB connectivity.
    Returns {connected, counts: {Point, Event, ...}, total_entities}.
    """
    return _safe(_get_team_sdk().status)


def tortoise_health() -> dict:
    """Health check + basic metrics: graph_size, last_ingest, error_count, uptime."""
    # #236: route through _safe() so every tool is gated (defense-in-depth;
    # reachable only post-auth over HTTP).
    return _safe(monitoring.metrics)


def tortoise_session_context() -> dict:
    """Return 'what happened last session' — diary entries, recent Points, Events, confidence changes.
    Returns {no_prior_sessions, diary_entries, recent_points, recent_events, confidence_changes}.
    """
    return _safe(_get_team_sdk().session_context)


def tortoise_ingest_corpus(directory: str) -> dict:
    """Batch document ingestion — walk directory, parse YAML frontmatter
    from .md files, create/update Document nodes.
    Returns {ingested, updated, skipped}.

    #236: EXCLUDED from tenant HTTP — walks server filesystem with a
    user-supplied path (path-traversal vector). Stdio-only.
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    return _safe(_get_team_sdk().ingest_corpus, directory)

# ── Taxonomy ─────────────────────────────────────────────────

def tortoise_taxonomy() -> dict[str, int]:
    """Count entities by node label. Returns {Point: N, Event: N, Subject: N, Object: N, Document: N}."""
    return _safe(_get_team_sdk().taxonomy)


def tortoise_list_topics(entity_id: str) -> dict:
    """entityProfile lite for an entity. Returns {id, pointKind, neighbors, neighborCounts}."""
    return _safe(_get_team_sdk().list_topics, entity_id)


# ── Graph Analysis ──────────────────────────────────────────────

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
            proj = _get_team_sdk()._get_proj()
            # #236: HTTP mode must use the team graph, NOT the hardcoded
            # "tortoise" graph — that hardcode bypasses team isolation via
            # db.select_graph() (cross-tenant read). Stdio keeps "tortoise".
            gname = f"team_{_current_team_id.get()}" if _transport_mode.get() == "http" else "tortoise"
            profile = entityProfile(proj.db, gname, entityId, hops=2)
            ids = {entityId}
            for category in profile.get("connected", {}).values():
                for node in category:
                    if node.get("id"):
                        ids.add(node["id"])
            entity_subgraph_ids = ids
        except Exception:
            pass  # fall back to full-graph analysis

    # #329: bound paid outbound LLM calls per team per minute — beyond budget
    # the tool degrades to keyword-only classification.
    use_llm = _analyze_llm_budget_available()
    result = _safe(analyze, question, _get_team_sdk()._get_proj(),
                   entity_subgraph_ids=entity_subgraph_ids,
                   anchor_ids=anchor_ids,
                   max_hops=max_hops,
                   rel_filter=rel_filter,
                   direction=direction,
                   use_llm=use_llm)
    # #329 defense-in-depth: analyze() self-redacts, but scrub the answer at the
    # boundary too in case a future error path leaks internals.
    if isinstance(result, dict) and isinstance(result.get("answer"), str):
        result["answer"] = _scrub_analyze_answer(result["answer"])
    return result


# ── P1-3: Staleness Detection ─────────────────────────────────

def tortoise_stale(days: int = 30, limit: int = 50) -> dict:
    """Find Points not updated in N days. Returns {stale, count, cutoff, limit}."""
    return _safe(_get_team_sdk().stale_points, days=days, limit=limit)


def tortoise_provenance(point_id: str) -> dict:
    """Provenance chain — "Who decided this?" Follows authoredBy → Subject → delegation."""
    return _safe(_get_team_sdk().provenance, point_id)


# ── Multi-tenancy (#7001) ────────────────────────────────────

def tortoise_team_create(name: str) -> dict:
    """Create isolated team graph via FalkorDB select_graph.
    Generates a per-team API key. Returns {name, graph_name, api_key, id}.
    destructiveHint=true — creates persistent resources.
    idempotentHint=false — duplicate team names raise an error.

    #236: EXCLUDED from tenant HTTP — provisioning belongs to
    /internal/provision behind FASTAPI_INTERNAL_KEY (privilege boundary).
    Stdio-only.
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    return _safe(_get_team_sdk().team_create, name)


# ── Entity CRUD (ONTOLOGY v2.5) ───────────────────────────────

def tortoise_create_subject(name: str, subjectKind: str, props: Any = None) -> dict:
    """Create a Subject node (team, role, organization, person)."""
    props = _parse(props)
    return _safe(_quota_gated(_get_team_sdk().create_subject, "points"), name, subjectKind, **(props or {}))

def tortoise_create_object(name: str, objectKind: str, props: Any = None) -> dict:
    """Create an Object node (product, customer, skill, etc.)."""
    props = _parse(props)
    return _safe(_quota_gated(_get_team_sdk().create_object, "points"), name, objectKind, **(props or {}))

def tortoise_create_event(name: str, eventKind: str, props: Any = None) -> dict:
    """Create an Event node (meeting, decision, deployment, etc.)."""
    props = _parse(props)
    return _safe(_quota_gated(_get_team_sdk().create_event, "points"), name, eventKind, **(props or {}))


def tortoise_get_events(eventKind: str | None = None, limit: int = 20) -> list[dict]:
    """Get recent Events, optionally filtered by eventKind (e.g. 'AgentSession')."""
    return _safe(_get_team_sdk().get_events, eventKind=eventKind, limit=limit)

def tortoise_get_session(session_id: str) -> dict:
    """Get a single agent session Event by session_id."""
    return _safe(_get_team_sdk().get_session, session_id)

def tortoise_index_sessions(directory: str, extract_metadata: bool = True, llm_model: str | None = None) -> dict:
    """Index session .md files as AgentSession Events. Returns {ingested, updated, skipped, failed, errors}.

    #236: EXCLUDED from tenant HTTP — walks server filesystem with a
    user-supplied path (path-traversal vector, same as ingest_corpus). Stdio-only.
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    if not os.path.isdir(directory):
        return {"error": f"Directory not found: {directory!r}. Provide a valid path to a directory containing .md session files."}
    return _safe(_get_team_sdk().index_sessions, directory, extract_metadata=extract_metadata, llm_model=llm_model)

def tortoise_search_sessions(query: str, agent: str | None = None, topics: Any = None,
                             after: str | None = None, before: str | None = None,
                             limit: int = 10, offset: int = 0) -> list[dict]:
    """Search indexed agent sessions. Returns Events with narrative_arc snippets.

    after/before bound the search to sessions whose startedAt falls in
    [after, before] (inclusive). Accept ISO-8601 strings (e.g.
    '2026-07-01T00:00:00Z' or '2026-07-31T23:59:59+00:00'); values are
    normalized to UTC. Sessions without startedAt are excluded when a bound
    is set.
    """
    topics = _parse(topics)
    if isinstance(topics, str):
        topics_list = [t.strip() for t in topics.split(",") if t.strip()]
    elif isinstance(topics, list):
        topics_list = topics
    else:
        topics_list = None
    return _safe(_get_team_sdk().search_sessions, query, agent=agent, topics=topics_list,
                 after=after, before=before, limit=limit, offset=offset)

def tortoise_create_document(title: str, documentKind: str, props: Any = None) -> dict:
    """Create a Document node (research, planDoc, meetingNotes, etc.)."""
    props = _parse(props)
    return _safe(_quota_gated(_get_team_sdk().create_document, "points"), title, documentKind, **(props or {}))

@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
def tortoise_create_source(url: str, sourceKind: str, tier: str | None = None,
                           sourceDate: str | None = None, props: Any = None) -> dict:
    """Create a Source node for provenance (document, web, db, etc.).

    Sources track content origin — url is the permalink key. Points link to
    Sources via extractedFrom edge (Ontology v2.5). ``tier`` (T0-T4) stores the
    credibility tier on ``credibilityTier`` (dual-write with tier-form
    sourceKind); ``sourceDate`` is the evidence-age clock for recency decay.
    """
    props = _parse(props) or {}
    # tier/sourceDate are first-class kwargs (#398) — pop from props if a legacy
    # caller passed them there (kwarg wins; avoids TypeError on splat).
    props.pop("tier", None)
    props.pop("sourceDate", None)
    return _safe(_quota_gated(_get_team_sdk().create_source, "points"), url, sourceKind,
                 tier=tier, sourceDate=sourceDate, **props)


@mcp.tool()
def tortoise_get_source_reliability(url: str) -> dict:
    """Derive a Source's reliability (0-1) — query-time, cache-consistency-checked.

    Reliability is the mean of the same modulated prior EP uses as base weight
    (tier + recency decay + reputation-weighted agent assessments). Untiered +
    unassessed → None. NOTE: refreshes the documented reliability cache on the
    Source node (write-through projection), so this tool is not read-only.
    """
    return _safe(_get_team_sdk().get_source_reliability, url)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def tortoise_assess_source(url: str, assessor: str, score: float,
                           rationale: str) -> dict:
    """Record an agent's assessment of a Source (0-1 score + rationale).

    Creates a pointKind='assessment' Statement Point (ontology §2 — evaluations
    are Points, not edges). Latest assessment per (url, assessor) wins; older
    are marked outdated. Weighted by the assessor's reputation snapshot
    (compute_reputation at write time). Feeds the source's reliability factor
    (clamped [0.1, 2.0]).
    """
    return _safe(_quota_gated(_get_team_sdk().assess_source, "points"),
                 url, assessor, score, rationale)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def tortoise_set_source_tier(url: str, tier: str) -> dict:
    """Set (or change) a Source's credibility tier (T0-T4). Non-destructive.

    Writes credibilityTier only — never overwrites sourceKind type strings.
    Dirty-marks the inheritance gate + clears the reliability cache so EP and
    reliability reads reflect the new tier promptly.
    """
    return _safe(_get_team_sdk().set_source_tier, url, tier)

def tortoise_get_entity(id: str) -> dict:
    """Get any entity by ID, eventId, or url."""
    return _safe(_get_team_sdk().get_entity, id)

def tortoise_update_entity(id: str, props: Any = None) -> dict:
    """Update any entity's properties."""
    props = _parse(props)
    return _safe(_quota_gated(_get_team_sdk().update_entity, "points"), id, **(props or {}))

def tortoise_delete_entity(id: str) -> bool:
    """Delete any entity by ID."""
    return _safe(_get_team_sdk().delete_entity, id)

def tortoise_create_edge(source_id: str, target_id: str, predicate: str) -> bool:
    """Create an edge between two entities. Predicate: performs, produces, ownedBy, managedBy, etc."""
    return _safe(_quota_gated(_get_team_sdk()._get_proj().create_edge, "points"), source_id, target_id, predicate)

def tortoise_get_governance(subject_id: str) -> list:
    """Get all entities owned by a Subject."""
    return _safe(_get_team_sdk().get_owned_entities, subject_id)

def tortoise_backfill_v25(dry_run: bool = True) -> dict:
    """Backfill database to ONTOLOGY v2.5 schema.

    #236: EXCLUDED from tenant HTTP — schema-level migration (operator-only).
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    return _safe(_get_team_sdk().backfill_v25, dry_run=dry_run)


# ── Tool Registry Adapter (#454) ────────────────────────────────
# Replaces @mcp.tool() decorators with programmatic registration.
# Function bodies remain module-level callables; the adapter wraps each
# via FunctionTool.from_function() and registers them on the shared mcp.
# Must execute AFTER all tool function definitions (at module bottom).
from tortoise.tool_registry import TOOL_REGISTRY, FastMCPAdapter

_adapter = FastMCPAdapter(mcp)
_adapter.register_all(TOOL_REGISTRY, {
    t.name: globals()[t.name]
    for t in TOOL_REGISTRY
    if t.name in globals()
})



# ── Onboarding MCP tools (#498/#499/#500) ───────────────────────
# Wrappers for the hosted onboarding flow. These call the team-scoped SDK
# directly (same pattern as all tools) — the REST endpoints in hosted_api.py
# expose the same operations to the welcome page.

def _onboarding_state() -> dict:
    """Read this team's onboarding progress from the registry Team node."""
    from tortoise.hosted_api import _get_onboarding_state as _read_state
    team_id = _current_team_id.get()
    if team_id is None:
        return {"error": "No team context (HTTP mode required)"}
    return _read_state(team_id)


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
def tortoise_onboarding_demo_create() -> dict:
    """Create the demo epistemic graph (4 layers) for this team. Idempotent.

    Q4 — 'Create a demo graph?' — shows what Tortoise memory looks like.
    """
    from tortoise.hosted_api import _seed_demo_graph
    team_id = _current_team_id.get()
    if team_id is None:
        return {"error": "No team context (HTTP mode required)"}
    # #329: demo graph creation creates nodes — quota-gate it
    _enforce_quota("points")
    result = _seed_demo_graph(team_id)
    # Auto-update onboarding state
    try:
        from tortoise.hosted_api import _update_onboarding_state
        _update_onboarding_state(team_id, demo_created=True)
    except Exception:
        pass
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def tortoise_onboarding_state() -> dict:
    """Return this team's onboarding progress (Q6 verification step)."""
    return _onboarding_state()


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def tortoise_onboarding_session_recording(enabled: bool) -> dict:
    """Toggle automatic session recording for this team (Q3)."""
    team_id = _current_team_id.get()
    if team_id is None:
        return {"error": "No team context (HTTP mode required)"}
    from tortoise.hosted_api import _update_onboarding_state
    state = _update_onboarding_state(team_id, session_recording=enabled)
    return {"onboarding": state}


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def tortoise_onboarding_github_connect(org: str | None = None) -> dict:
    """Initiate GitHub OAuth — returns the authorize URL + CSRF state (Q1)."""
    team_id = _current_team_id.get()
    if team_id is None:
        return {"error": "No team context (HTTP mode required)"}
    import secrets
    from urllib.parse import urlencode
    import os as _os
    client_id = _os.environ.get("GITHUB_CLIENT_ID")
    if not client_id:
        return {"error": "GitHub OAuth not configured"}
    state = secrets.token_urlsafe(24)
    # Store CSRF state so the callback can validate it (P2 review fix) —
    # must be visible to the REST callback handler in the same process.
    import time as _time
    from tortoise.hosted_api import _GITHUB_STATES
    _GITHUB_STATES[state] = {"team_id": team_id, "org": org or team_id,
                             "created_at": _time.time()}
    callback = _os.environ.get("GITHUB_CALLBACK_URL",
                               "https://api.premiselabs.co/v1/onboarding/github/callback")
    params = {"client_id": client_id, "redirect_uri": callback,
              "scope": "repo", "state": state}
    auth_url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    return {"auth_url": auth_url, "state": state}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def tortoise_onboarding_github_status() -> dict:
    """Return GitHub connection status for this team (Q1 verify)."""
    team_id = _current_team_id.get()
    if team_id is None:
        return {"error": "No team context (HTTP mode required)"}
    sdk = _get_team_sdk()
    try:
        reg = sdk._get_registry().query(
            "MATCH (t:Team {id: $id}) RETURN t.github_token_enc, t.github_org",
            params={"id": team_id}).result_set
    except Exception:
        return {"connected": False, "org": None, "repos_count": None}
    if not reg or not reg[0][0]:
        return {"connected": False, "org": None, "repos_count": None}
    return {"connected": True, "org": reg[0][1], "repos_count": None}


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def tortoise_onboarding_github_index(org: str, repo: str | None = None) -> dict:
    """Start background GitHub indexing of an org's issues/PRs (Q2).

    Returns {job_id, status} — poll via the REST endpoint or check
    onboarding state for github_indexed.
    """
    team_id = _current_team_id.get()
    if team_id is None:
        return {"error": "No team context (HTTP mode required)"}
    import secrets as _secrets
    import asyncio as _asyncio
    from tortoise.hosted_api import _INDEX_JOBS, _run_indexing, _make_sdk as _ha_make_sdk
    sdk = _ha_make_sdk(namespace="registry")
    try:
        rows = sdk._get_registry().query(
            "MATCH (t:Team {id: $id}) RETURN t.github_token_enc",
            params={"id": team_id}).result_set
    except Exception:
        return {"error": "Registry unavailable"}
    if not rows or not rows[0][0]:
        return {"error": "GitHub not connected. Run tortoise_onboarding_github_connect first."}
    job_id = _secrets.token_hex(8)
    _INDEX_JOBS[job_id] = {"status": "started", "progress": 0,
                           "points_created": 0, "error": None,
                           "team_id": team_id,
                           "created_at": _asyncio.get_event_loop().time()}
    try:
        _asyncio.get_event_loop().create_task(
            _run_indexing(job_id, team_id, org, repo))
    except RuntimeError:
        return {"error": "No running event loop"}
    return {"job_id": job_id, "status": "started"}


# ── HTTP Streamable transport (#236) ─────────────────────────────

def create_http_app(*, allowed_origins: list[str] | None = None,
                    allowed_hosts: list[str] | None = None,
                    rate_limit: int = 100,
                    _registry_sdk=None,
                    auth_mode: Literal["tenant", "static", "none"] = "tenant",
                    api_key: str | None = None,
                    tool_group: str | None = None) -> Any:
    """Configured Streamable HTTP app for the hosted platform (#236).

    Mounted at /mcp on the existing FastAPI app. Auth + rate limiting +
    security headers + body-size caps live INSIDE this app's middleware
    stack — the parent FastAPI app.mount() does NOT propagate its own
    middleware to mounted sub-apps (verified Starlette behavior).

    auth_mode (additive, default "tenant" = hosted byte-identical):
      "tenant" → TeamResolutionMiddleware (registry Bearer tt_ keys)
      "static" → StaticKeyMiddleware (single TORTOISE_API_KEY, self-host LAN)
      "none"   → no auth middleware (localhost-bound self-host eval)

    tool_group: optional curation-group filter (#523) — role-scoped server
      (e.g. "memory" exposes only memory tools to the agent).

    path="/": the app is mounted at /mcp on the parent FastAPI app, which
    strips the mount prefix before dispatching to this sub-app — so routes
    must live at / (parent /mcp → sub-app /). The GET /mcp metadata route
    is registered on the shared module-level mcp instance — safe for stdio
    (route unused) and coexists with the POST/DELETE streamable-http route.
    """
    from starlette.middleware import Middleware
    from starlette.responses import JSONResponse
    from tortoise.mcp_auth import (MCPRateLimitMiddleware,
                                   SecurityHeadersMiddleware,
                                   RequestBodySizeMiddleware)
    from fastmcp.server.transforms import Transform

    # auth_mode middleware selection. TeamResolutionMiddleware (tenant mode) is
    # imported here but only ever INSTANTIATED in the tenant branch — static/none
    # modes never construct it, and hosted_api is only ever lazily imported when
    # a tenant token is verified (mcp_auth delegates via function-level import).
    auth_mw = None
    transport_mw = None
    group_mw = None
    if tool_group:
        from tortoise.mcp_auth import ToolGroupMiddleware
        group_mw = Middleware(ToolGroupMiddleware, tool_group=tool_group)
    if auth_mode == "tenant":
        from tortoise.mcp_auth import TeamResolutionMiddleware
        auth_mw = Middleware(TeamResolutionMiddleware, registry_sdk=_registry_sdk)
    elif auth_mode == "static":
        from tortoise.mcp_auth import StaticKeyMiddleware
        auth_mw = Middleware(StaticKeyMiddleware, api_key=api_key)
        from tortoise.mcp_auth import TransportModeMiddleware
        transport_mw = Middleware(TransportModeMiddleware)
    elif auth_mode == "none":
        from tortoise.mcp_auth import TransportModeMiddleware
        transport_mw = Middleware(TransportModeMiddleware)

    class _HTTPToolFilter(Transform):
        """Hide HTTP-excluded tools from tools/list (D4) + optional curation
        group scoping (#523).

        The excluded tools (team_create/backfill_v25/ingest_corpus) remain
        registered on the shared module-level mcp instance for stdio, but are
        filtered out of the HTTP tool listing so tenants can't discover them.
        When tool_group is set, only that group's tools are listed — role-
        scoped servers keep the agent's tool-selection surface under ~20.
        """
        async def list_tools(self, tools):
            from tortoise.mcp_auth import _tool_group
            group = _tool_group.get()
            if group:
                from tortoise.tool_registry import GROUP_BY_NAME
                return [t for t in tools
                        if t.name in HTTP_ALLOWED
                        and GROUP_BY_NAME.get(t.name) == group]
            return [t for t in tools if t.name in HTTP_ALLOWED]

    # Guard against transform accumulation: create_http_app() is called at
    # hosted_api import AND in every test fixture — each call would append a
    # new _HTTPToolFilter to the shared module-level mcp instance (code-review
    # P2 fix). Register once.
    if not getattr(mcp, "_http_tool_filter_registered", False):
        mcp.add_transform(_HTTPToolFilter())
        mcp._http_tool_filter_registered = True

    @mcp.custom_route("/", methods=["GET"])
    async def mcp_metadata(request):
        return JSONResponse({"status": "ok", "protocol": "mcp",
                             "transport": "streamable-http",
                             "endpoint": "/mcp"})

    middleware = [
        Middleware(SecurityHeadersMiddleware),
        Middleware(RequestBodySizeMiddleware),
    ]
    if auth_mw is not None and auth_mode == "tenant":
        # Original position: tenant auth sits between body-size and rate-limit
        # (byte-identical to pre-auth_mode hosted stack).
        middleware.append(auth_mw)
    middleware.append(Middleware(MCPRateLimitMiddleware, max_per_minute=rate_limit))
    if auth_mw is not None and auth_mode != "tenant":
        # Static mode: rate limiter sits OUTSIDE auth so failed-key attempts are
        # throttled (code-review P1 — unlimited brute force on a user-chosen key).
        middleware.append(auth_mw)
    if transport_mw is not None:
        # Innermost — runs after auth validated, right before the app:
        # initializes the transport-mode ContextVars selfhost tools need.
        middleware.append(transport_mw)
    if group_mw is not None:
        # Sets the curation-group ContextVar for the tools/list transform.
        middleware.append(group_mw)

    return mcp.http_app(
        transport="streamable-http",
        stateless_http=True,
        host_origin_protection=True,
        allowed_origins=allowed_origins or [],
        allowed_hosts=allowed_hosts or [],
        path="/",
        middleware=middleware,
    )
