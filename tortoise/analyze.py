"""tortoise_analyze — template-based NL→Cypher graph analysis.

Single MCP tool: tortoise_analyze(question) → natural language answer.
Template Cypher + LLM for intent classification and result synthesis.
No LLM-Cypher generation (rejected: 61% accuracy, 40% silent errors).
"""
from __future__ import annotations

import json, os, re
from typing import Any

from .live import _live_only


# ═══════════════════════════════════════════════════════════════════
# Template Registry
# ═══════════════════════════════════════════════════════════════════

TEMPLATES: dict[str, dict] = {
    "disagreement": {
        "triggers": ["disagree", "conflict", "oppose", "contradict", "tension", "contested"],
        "description": "Find NAND conflicts between high-confidence claims",
        "subgraph_vars": ["a", "b"],
        "cypher": """
            MATCH (a:Point)<-[:NAND]-(op:Point {op_type:\"NAND\"})-[:NAND]->(b:Point)
            WHERE a.id <> b.id
              AND a.is_operator = false
              AND b.is_operator = false
              AND coalesce(a.confidence, 0.5) > 0.45
              AND coalesce(b.confidence, 0.5) > 0.45
            RETURN a.id, a.content, coalesce(a.confidence,0.5) as a_conf,
                   b.id, b.content, coalesce(b.confidence,0.5) as b_conf
            ORDER BY a_conf DESC LIMIT $limit
        """,
        "format": lambda rows: _format_pairs(rows, "conflict"),
    },
    "strongest_for": {
        "triggers": ["strongest", "best argument", "most support", "evidence for", "supports"],
        "description": "Find claims supporting a target via IMPL chains, ranked by confidence",
        "subgraph_vars": ["supporter"],
        "cypher": """
            MATCH (supporter:Point)<-[:IMPL]-(op:Point {op_type:\"IMPL\"})-[:IMPL]->(target:Point)
            WHERE target.id <> supporter.id
              AND target.content CONTAINS $entity
              AND coalesce(supporter.confidence, 0.5) > 0.0
            RETURN supporter.id, supporter.content, coalesce(supporter.confidence,0.5) as conf
            ORDER BY conf DESC LIMIT $limit
        """,
        "format": lambda rows: _format_ranked(rows, "supporting"),
    },
    "counter_arguments": {
        "triggers": ["counter", "against", "objection", "contradicts", "opposes", "attack"],
        "description": "Find claims that contradict a target via NAND edges",
        "subgraph_vars": ["opponent"],
        "cypher": """
            MATCH (target:Point)<-[:NAND]-(op:Point {op_type:\"NAND\"})-[:NAND]->(opponent:Point)
            WHERE target.id <> opponent.id
              AND target.content CONTAINS $entity
              AND opponent.is_operator = false
            RETURN opponent.id, opponent.content, coalesce(opponent.confidence,0.5) as conf
            ORDER BY conf DESC LIMIT $limit
        """,
        "format": lambda rows: _format_ranked(rows, "counter"),
    },
    "consensus": {
        "triggers": ["consensus", "agree", "shared", "common ground", "everyone believes"],
        "description": "Find high-confidence claims with no NAND attacks (consensus)",
        "subgraph_vars": ["c"],
        "cypher": """
            MATCH (c:Point)
            WHERE c.is_operator = false
              AND coalesce(c.confidence, 0.5) > 0.6
              AND NOT (c)<-[:NAND]-(:Point {op_type:\"NAND\"})
            RETURN c.id, c.content, coalesce(c.confidence,0.5) as conf
            ORDER BY conf DESC LIMIT $limit
        """,
        "format": lambda rows: _format_ranked(rows, "consensus"),
    },
    "most_uncertain": {
        "triggers": ["uncertain", "weakest", "least sure", "unknown", "unsure", "low confidence"],
        "description": "Find claims with lowest EP confidence or highest variance",
        "subgraph_vars": ["c"],
        "cypher": """
            MATCH (c:Point)
            WHERE c.is_operator = false
              AND (c.posterior_alpha IS NOT NULL OR c.ep_alpha IS NOT NULL)
            WITH c, coalesce(c.posterior_alpha, c.ep_alpha, 1.0) AS a,
                 coalesce(c.posterior_beta, c.ep_beta, 1.0) AS b
            WITH c, a, b, (a * b) / 
               ((a + b)^2 * (a + b + 1)) as variance
            RETURN c.id, c.content, coalesce(c.confidence,0.5) as conf, variance
            ORDER BY variance DESC LIMIT $limit
        """,
        "format": lambda rows: _format_uncertain(rows),
    },
    "evidence_chain": {
        "triggers": ["evidence for", "supports", "basis", "foundation", "backing", "chain"],
        "description": "Trace IMPL support chain back to root evidence for a claim",
        "subgraph_vars": ["evidence"],
        "cypher": """
            MATCH (evidence:Point)<-[:IMPL]-(op:Point {op_type:\"IMPL\"})-[:IMPL]->(target:Point)
            WHERE target.id <> evidence.id
              AND target.content CONTAINS $entity
              AND evidence.is_operator = false
            RETURN evidence.id, evidence.content, coalesce(evidence.confidence,0.5) as conf
            ORDER BY conf DESC LIMIT $limit
        """,
        "format": lambda rows: _format_chain(rows),
    },
    "trends": {
        "triggers": ["changed", "trend", "evolved", "over time", "history", "how has"],
        "description": "Show confidence changes by comparing node properties over time",
        "subgraph_vars": ["c"],
        "cypher": """
            MATCH (c:Point)
            WHERE c.content CONTAINS $entity
              AND (c.posterior_alpha IS NOT NULL OR c.ep_alpha IS NOT NULL)
            RETURN c.id, c.content, coalesce(c.confidence,0.5) as conf,
                   coalesce(c.posterior_alpha, c.ep_alpha, 1.0) as a,
                   coalesce(c.posterior_beta, c.ep_beta, 1.0) as b,
                   c.createdAt
            ORDER BY c.createdAt DESC LIMIT $limit
        """,
        "format": lambda rows: _format_timeline(rows),
    },
    "grounding": {
        "triggers": ["grounded", "central", "important", "key", "pivotal", "core"],
        "description": "Find most central claims by PageRank grounding score",
        "subgraph_vars": ["c"],
        "cypher": """
            MATCH (c:Point)
            WHERE c.is_operator = false
              AND c.grounding IS NOT NULL
            RETURN c.id, c.content, c.grounding
            ORDER BY c.grounding DESC LIMIT $limit
        """,
        "format": lambda rows: _format_ranked(rows, "grounding"),
    },
}


# ═══════════════════════════════════════════════════════════════════
# Formatters
# ═══════════════════════════════════════════════════════════════════

def _format_pairs(rows: list, label: str) -> str:
    if not rows:
        return f"No {label}s found."
    lines = [f"{len(rows)} {label}(s) found:"]
    for r in rows[:10]:
        lines.append(f"  • \"{r[1][:80]}\" ({r[2]:.2f}) ⇎ \"{r[4][:80]}\" ({r[5]:.2f})")
    return "\n".join(lines)


def _format_ranked(rows: list, label: str) -> str:
    if not rows:
        return f"No {label} claims found."
    lines = [f"Top {label} claims:"]
    for i, r in enumerate(rows[:10], 1):
        try:
            conf = float(r[2])
            content = r[1][:80] if r[1] is not None else ""
        except (TypeError, ValueError, IndexError):
            conf = None  # non-numeric/missing confidence → degrade, don't crash
            content = (r[1][:80] if len(r) > 1 and r[1] is not None else "")
        val = f"  {i}. \"{content}\"" + (f" (confidence: {conf:.2f})" if conf is not None else "")
        lines.append(val)
    return "\n".join(lines)


def _format_uncertain(rows: list) -> str:
    if not rows:
        return "No uncertain claims found."
    lines = ["Most uncertain claims (highest variance):"]
    for i, r in enumerate(rows[:10], 1):
        lines.append(f"  {i}. \"{r[1][:80]}\" (conf: {r[2]:.2f}, var: {r[3]:.4f})")
    return "\n".join(lines)


def _format_chain(rows: list) -> str:
    if not rows:
        return "No evidence chain found."
    lines = ["Evidence chain (ordered by confidence):"]
    for r in rows[:10]:
        # evidence_chain template returns 3 columns: id, content, conf
        try:
            conf = float(r[2])
            conf_txt = f" (conf: {conf:.2f})"
        except (TypeError, ValueError, IndexError):
            conf_txt = ""  # non-numeric/missing confidence → degrade, don't crash
        content = r[1][:80] if len(r) > 1 and r[1] is not None else ""
        lines.append(f"  \"{content}\"{conf_txt}")
    return "\n".join(lines)


def _format_timeline(rows: list) -> str:
    if not rows:
        return "No timeline data found."
    lines = ["Confidence timeline:"]
    for r in rows[:10]:
        ts = r[5][:10] if len(r) > 5 and r[5] else "?"
        lines.append(f"  [{ts}] \"{r[1][:80]}\" (α={r[3]:.1f}, β={r[4]:.1f}, conf={r[2]:.2f})")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# Classification
# ═══════════════════════════════════════════════════════════════════

def classify(question: str) -> tuple[str, dict] | None:
    """Keyword match question to template pattern. Returns (name, params) or None."""
    q = question.lower()
    for name, tmpl in TEMPLATES.items():
        for trigger in tmpl["triggers"]:
            if trigger in q:
                # Extract entity parameter
                entity = _extract_entity(q, trigger)
                return name, {"entity": entity, "limit": 20}
    return None


def _extract_entity(question: str, trigger: str) -> str:
    """Extract entity name from question by removing trigger words and common phrases."""
    # Simple heuristic: remove the trigger and common surrounding words
    q = question.lower()
    # Remove common phrases
    for phrase in ["what is the", "what are the", "show me", "find", "tell me about",
                    "what claims", "which claims", "where is the", "how has"]:
        q = q.replace(phrase, "")
    # Remove the trigger word
    q = q.replace(trigger, "")
    # Remove common connector words
    for word in ["about", "for", "of", "the", "in", "on", "to", "that", "is", "are"]:
        q = re.sub(rf'\b{word}\b', '', q)
    # Clean up
    entity = ' '.join(q.split()).strip()
    return entity if entity else ""


# ═══════════════════════════════════════════════════════════════════
# LLM Integration (optional — keyword match handles 80% of queries)
# ═══════════════════════════════════════════════════════════════════

# #329: provider-key pairing — a key is ONLY ever sent to the provider that
# issued it. (The old code used `OPENAI_API_KEY or DEEPSEEK_API_KEY` and always
# POSTed to api.deepseek.com — the OpenAI key was exfiltrated to DeepSeek.)
_LLM_PROVIDERS: dict[str, tuple[str, str]] = {
    "DEEPSEEK_API_KEY": ("https://api.deepseek.com/v1/chat/completions", "deepseek-chat"),
    "OPENAI_API_KEY": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
}
# Priority order when multiple keys are set (deepseek first — historical default).
_LLM_PROVIDER_PRIORITY: tuple[str, ...] = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY")


def llm_classify(question: str) -> tuple[str, dict] | None:
    """Use LLM to classify intent + extract params when keyword match fails.

    #329: picks the provider whose OWN env key is set; an empty-string key is
    treated as unset. The chosen key is only sent to its own provider's URL.
    Returns None (keyword-only fallback) when no key is set or the provider
    request fails — a provider outage never falls through to another provider
    (that would leak the wrong key too).
    """
    import urllib.request
    api_key = None
    provider_url = None
    provider_model = None
    for env_name in _LLM_PROVIDER_PRIORITY:
        candidate = os.environ.get(env_name)
        if candidate:  # non-empty string
            api_key, (provider_url, provider_model) = candidate, _LLM_PROVIDERS[env_name]
            break
    if api_key is None:
        return None  # fall back to keyword only

    try:
        body = json.dumps({
            "model": provider_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": LLM_PROMPT},
                         {"role": "user", "content": question}],
        }).encode()
        req = urllib.request.Request(
            provider_url,
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            result = json.loads(data["choices"][0]["message"]["content"])
            return result.get("pattern"), result.get("params", {})
    except Exception:
        return None


LLM_PROMPT = """You classify questions about an epistemic graph. Output JSON:
{"pattern": "<name>", "params": {"entity": "...", "limit": 20}}

Available patterns:
- disagreement: "where is the disagreement/conflict?"
- strongest_for: "what supports/strongest evidence for X?"
- counter_arguments: "what contradicts/attacks X?"
- consensus: "what do we agree on?"
- most_uncertain: "what are we least sure about?"
- evidence_chain: "trace the evidence for X"
- trends: "how has X changed over time?"
- grounding: "what are the most central/important claims?"

Only output JSON. No other text."""


# ═══════════════════════════════════════════════════════════════════
# Subgraph Filter Injection
# ═══════════════════════════════════════════════════════════════════

def _inject_subgraph_filter(cypher: str, vars: list[str],
                            ids: set[str]) -> str:
    """Inject ID filter into Cypher WHERE clause for entity-scoped analysis."""
    if not vars:
        return cypher
    id_list = json.dumps(list(ids))
    filters = ' AND '.join(f'{v}.id IN {id_list}' for v in vars)
    return cypher.replace('WHERE ', f'WHERE {filters} AND ', 1)


# ═══════════════════════════════════════════════════════════════════
# Main API
# ═══════════════════════════════════════════════════════════════════

def _bfs_select_operators(proj, anchors: list[str], max_hops: int = 1,
                          rel_filter: str = "IMPL|NAND",
                          direction: str = "both",
                          include_draft: bool = False) -> set[str]:
    """BFS subgraph selection from anchor Points — returns set of operator Point IDs.

    Shared helper for both compute_confidence and tortoise_analyze.
    Expands from anchor Points along operator edges (IMPL|NAND) for max_hops hops.
    IMPL edges respect direction; NAND always traversed bidirectionally.
    Capped at 200 operator IDs.

    With include_draft=False (default, #780): draft anchors, draft operators
    and draft frontier points are excluded — the shared live-only filter at
    the analyze-path call site of the four-factor-extraction set.
    """
    import logging
    _log = logging.getLogger(__name__)
    collected: set[str] = set()
    frontier: set[str] = set(anchors)
    visited: set[str] = set(anchors)
    rel_types = set(rel_filter.split("|"))

    live_op = f"AND {_live_only('op.status', include_draft)}" if not include_draft else ""
    live_t = f"AND {_live_only('target.status', include_draft)}" if not include_draft else ""
    live_p = f"AND {_live_only('p.status', include_draft)}" if not include_draft else ""

    if not include_draft and frontier:
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids AND n.status = 'draft' RETURN n.id",
            params={"ids": list(frontier)},
        ).result_set
        draft_anchors = {r[0] for r in rows}
        if draft_anchors:
            frontier -= draft_anchors
            visited -= draft_anchors

    for hop in range(max_hops):
        if not frontier:
            break
        new_ops: set[str] = set()
        new_points: set[str] = set()
        frontier_list = list(frontier)

        for rel in rel_types:
            if rel not in ("IMPL", "NAND"):
                continue

            # NAND is always bidirectional; IMPL respects direction flag
            if rel == "NAND":
                dirs = ("incoming", "outgoing")
            elif direction == "both":
                dirs = ("incoming", "outgoing")
            else:
                dirs = (direction,)

            for d in dirs:
                if d == "incoming":
                    rows = proj.g.query(
                        f"MATCH (op:Point {{is_operator:true}})-[:{rel}]->(p:Point) "
                        f"WHERE p.id IN $frontier {live_op} "
                        "RETURN DISTINCT op.id",
                        params={"frontier": frontier_list},
                    ).result_set
                    for (op_id,) in rows:
                        new_ops.add(op_id)
                        new_points.add(op_id)
                else:  # outgoing
                    rows = proj.g.query(
                        f"MATCH (op:Point {{is_operator:true}})-[:{rel}]->(target:Point) "
                        f"WHERE op.id IN $frontier {live_op} {live_t} "
                        "RETURN DISTINCT target.id",
                        params={"frontier": frontier_list},
                    ).result_set
                    for (target_id,) in rows:
                        new_points.add(target_id)

        collected |= new_ops

        if len(collected) > 200:
            _log.warning(
                "BFS selector: collected %d operators, truncating to 200.",
                len(collected),
            )
            # Deterministic truncation — set iteration order is non-deterministic
            # (hash randomization); sort by ID for reproducible selection.
            collected = set(sorted(collected)[:200])
            break

        # Expand to new frontier points from collected operators
        if new_ops and hop < max_hops - 1:
            ops_list = list(new_ops)
            rows = proj.g.query(
                "MATCH (op:Point {is_operator:true})-[r:IMPL|NAND]->(p:Point) "
                f"WHERE op.id IN $ops {live_p} "
                "RETURN DISTINCT p.id",
                params={"ops": ops_list},
            ).result_set
            for (p_id,) in rows:
                if p_id not in visited:
                    new_points.add(p_id)

        frontier = new_points - visited
        visited |= new_points

    return collected


def analyze(question: str, proj=None, *,
            entity_subgraph_ids: set[str] | None = None,
            anchor_ids: list[str] | None = None,
            max_hops: int = 1,
            rel_filter: str = "IMPL|NAND",
            direction: str = "both",
            use_llm: bool = True) -> dict[str, Any]:
    """Answer a natural language question about the Tortoise graph.

    Args:
        question: Natural language question
        proj: FalkorProjection instance (optional — for Cypher execution)
        entity_subgraph_ids: Pre-filter results to these Point IDs (entity-scoped analysis)
        anchor_ids: list of Point IDs for BFS subgraph selection (new; alternative to entity_subgraph_ids)
        anchor_ids: list of Point IDs for BFS subgraph selection (new; alternative to entity_subgraph_ids)
        max_hops: BFS expansion depth when using anchor_ids (default 1)
        rel_filter: edge types for BFS — "IMPL", "NAND", or "IMPL|NAND" (default)
        direction: IMPL edge traversal direction — "incoming", "outgoing", or "both" (default)
        use_llm: Fall back to LLM if keyword match fails

    Returns:
        {"answer": "...", "raw": [...], "pattern": "...", "query": "..."}
    """
    # BFS subgraph selection from anchor IDs
    if anchor_ids is not None and proj is not None and entity_subgraph_ids is None:
        entity_subgraph_ids = _bfs_select_operators(
            proj, anchor_ids, max_hops=max_hops,
            rel_filter=rel_filter, direction=direction,
        )
    # 1. Classify intent
    result = classify(question)
    if result is None and use_llm:
        result = llm_classify(question)
    if result is None:
        return {"answer": "I couldn't understand that question. Try asking about: "
                          "disagreement, strongest claims, counter-arguments, consensus, "
                          "uncertainty, evidence chains, trends, or grounding.",
                "raw": [], "pattern": None, "query": None}

    pattern_name, params = result
    tmpl = TEMPLATES.get(pattern_name)
    if tmpl is None:
        # #329: pattern_name comes from the LLM (untrusted) — never echo it raw
        return {"answer": "Unknown pattern requested.", "raw": [], "pattern": None, "query": None}

    # 2. Execute Cypher
    cypher = tmpl["cypher"]
    if entity_subgraph_ids is not None:
        cypher = _inject_subgraph_filter(cypher, tmpl.get("subgraph_vars", []),
                                          entity_subgraph_ids)

    rows = []
    if proj is not None:
        try:
            rows = proj.g.query(cypher, params=params).result_set
        except Exception as e:
            # #329: redact — tenants must not see raw DB errors or query text
            from .security import redact_error
            return {"answer": f"Query error: {redact_error(e)}", "raw": [], "pattern": pattern_name, "query": None}

    # 3. Format (wrapped — a malformed value must degrade, never crash the
    # whole analyze surface; security: redacted, no raw internals)
    formatter = tmpl.get("format")
    try:
        if formatter:
            answer = formatter(rows)
        else:
            answer = f"Found {len(rows)} results."
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(
            "analyze formatter failed for pattern %s", pattern_name)
        from .security import redact_error
        answer = f"Formatting error: {redact_error(e)}"

    return {
        "answer": answer,
        "raw": [[str(v) for v in row] for row in rows[:20]],
        "pattern": pattern_name,
        "query": cypher,
    }


# ═══════════════════════════════════════════════════════════════════
# Gate B Tooling (epic-264 #779) — mean grounding + drift snapshot
# ═══════════════════════════════════════════════════════════════════

# Mean-absolute drift ceiling. MUST stay 0.02 — EpSafeCommit's
# max_grounding_drift (#785 seam) consumes this contract; tests pin the value.
MAX_GROUNDING_DRIFT = 0.02
# Max single-point absolute delta ceiling (R12: mean-absolute can mask
# per-point flips — the ≤5% max single-point target from the #779 issue).
MAX_POINT_DRIFT = 0.05


def _resolve_proj(proj):
    """Resolve a projection when None (lazy import — sdk/analyze are
    mutually imported at call sites, never at module load)."""
    if proj is not None:
        return proj
    from tortoise.sdk import TortoiseSDK
    return TortoiseSDK()._get_proj()


def mean_grounding(proj=None) -> float:
    """Mean over ``confidence`` of live non-operator Points (DE2E-4, #779).

    Gate B snapshot metric: sample = full live Point set, mean of the
    ``confidence`` property. Live semantics follow the #780 shared
    ``_live_only`` filter — legacy NULL-status Points are LIVE
    (``coalesce($st, n.status, 'live')`` write default), ``status: draft``
    Points are excluded. Operator Points (``is_operator: true``) are
    excluded. An empty live set returns 0.0.

    Missing-confidence extension (DELIBERATE, beyond the pinned DE2E-4
    formula): a live Point with no ``confidence`` contributes 0.5 to the
    mean (``coalesce(p.confidence, 0.5)`` — repo-wide NULL-confidence
    convention). The DE2E-4 plan formula assumes every Point carries a
    confidence; this implementation deliberately extends it so a
    confidence-less Point degrades the mean toward neutral 0.5 instead of
    erroring or being silently dropped (which would skew the mean upward).
    Asserted in ``test_mean_grounding_null_confidence_imputed_zero_five``.

    Consumed by #785's EpSafeCommit seam (resolves
    ``tortoise.analyze.mean_grounding``) for the pre/post batch check.
    """
    proj = _resolve_proj(proj)
    rows = proj.g.query(
        "MATCH (p:Point) "
        f"WHERE (p.is_operator IS NULL OR p.is_operator = false) AND {_live_only('p.status')} "
        "RETURN coalesce(p.confidence, 0.5)"
    ).result_set
    if not rows:
        return 0.0
    return sum(float(r[0]) for r in rows) / len(rows)


def grounding_snapshot(proj=None) -> dict:
    """Pre/post batch grounding sample (Gate B tooling).

    Returns per-point confidences so BOTH ceilings can be checked: the
    ≤2% mean-absolute ceiling (``MAX_GROUNDING_DRIFT``) and the ≤5% max
    single-point absolute delta (``MAX_POINT_DRIFT``, R12).

    Returns:
        {"count": int, "mean": float, "points": {id: confidence},
         "sampled_at": ISO-8601 UTC}
    """
    proj = _resolve_proj(proj)
    rows = proj.g.query(
        "MATCH (p:Point) "
        f"WHERE (p.is_operator IS NULL OR p.is_operator = false) AND {_live_only('p.status')} "
        "RETURN p.id, coalesce(p.confidence, 0.5)"
    ).result_set
    points = {r[0]: float(r[1]) for r in rows}
    mean = sum(points.values()) / len(points) if points else 0.0
    from datetime import datetime, timezone
    return {
        "count": len(points),
        "mean": mean,
        "points": points,
        "sampled_at": datetime.now(timezone.utc).isoformat(),
    }


def grounding_drift(pre: dict, post: dict, *,
                    max_mean_drift: float = MAX_GROUNDING_DRIFT,
                    max_point_drift: float = MAX_POINT_DRIFT) -> dict:
    """Compare pre/post batch grounding snapshots (Gate B drift check).

    Ceilings: ≤2% mean absolute (matches EpSafeCommit's
    ``max_grounding_drift=0.02`` — the #785 seam) and ≤5% max single-point
    absolute delta (R12 — a mean-absolute check alone can mask per-point
    flips). Both the per-point deltas AND the mean term are computed over
    the ID intersection (``common``) only: a Point created or deleted
    between snapshots is new/removed content, not a regression, and must
    not fail the check — a set-size change must not shift the mean past
    the ≤2% ceiling either. ``mean_abs_delta`` is the intersection-scoped
    delta (ignores pre/post ``mean`` fields; recomputed from ``points``).

    Returns:
        {"passed": bool, "mean_abs_delta": float,
         "max_point_abs_delta": float, "mean_ceiling": float,
         "point_ceiling": float, "pre_count": int, "post_count": int,
         "overlap": int} — plus "reason": "no_common_points" when the ID
        intersection is empty (review round 2: a total replacement must
        FAIL CLOSED, never vacuously pass).
    """
    pre_points = pre.get("points", {})
    post_points = post.get("points", {})
    # Means are recomputed over the ID intersection only — a set-size
    # change (created/deleted Points) must not shift the mean past the
    # ceiling (review P2: full-set means can drift on add/remove alone).
    common = set(pre_points) & set(post_points)
    if not common:
        # Zero shared Point ids (total replacement): both recomputed means
        # are 0.0, so the ceilings would trivially pass — a vacuous pass
        # (pre 0.90 → post 0.30 reported True before this fix). Fail closed
        # with an explicit signal instead of a silent 0.0-delta pass.
        return {
            "passed": False,
            "reason": "no_common_points",
            "overlap": 0,
            "mean_abs_delta": 0.0,
            "max_point_abs_delta": 0.0,
            "mean_ceiling": max_mean_drift,
            "point_ceiling": max_point_drift,
            "pre_count": pre.get("count", 0),
            "post_count": post.get("count", 0),
        }
    pre_mean = sum(float(pre_points[pid]) for pid in common) / len(common)
    post_mean = sum(float(post_points[pid]) for pid in common) / len(common)
    mean_abs_delta = abs(post_mean - pre_mean)
    max_point_abs_delta = max(
        (abs(float(post_points[pid]) - float(pre_points[pid])) for pid in common),
        default=0.0,
    )
    passed = (
        mean_abs_delta <= max_mean_drift
        and max_point_abs_delta <= max_point_drift
    )
    return {
        "passed": passed,
        "mean_abs_delta": mean_abs_delta,
        "max_point_abs_delta": max_point_abs_delta,
        "mean_ceiling": max_mean_drift,
        "point_ceiling": max_point_drift,
        "pre_count": pre.get("count", 0),
        "post_count": post.get("count", 0),
        "overlap": len(common),
    }
