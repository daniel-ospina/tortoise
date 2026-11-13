# Archived mcp-tool `tortoise_analyze` — REMOVED from the product surface (#11609).
#
# batch:   b1-removed-no-destination
# reason:  no destination in the approved 27-tool surface; no replacement (owner ruling, #11609)
# source:  tortoise/tool_registry.py @ b85194d6bcb15b80a8cc07d2e6a076d7e8af8456
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'mcp-tool'
ARCHIVED_SYMBOL = 'tortoise_analyze'
ARCHIVED_BATCH = 'b1-removed-no-destination'
ARCHIVED_FROM = 'tortoise/tool_registry.py'
ARCHIVED_AT = 'b85194d6bcb15b80a8cc07d2e6a076d7e8af8456'

# ── verbatim from tortoise/tool_registry.py ──

    ToolDefinition(
        name="tortoise_analyze",
        id="surface.analyze",
        description="Answer natural language questions about the Tortoise epistemic graph. "
                    "Ask things like: 'where is the disagreement?' 'what supports claim X?'",
        risk="read",
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py (calls analyze.analyze)
    ),

# ── verbatim from tortoise/mcp_server.py ──

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
    — plus, with the W4 enrichment flag ON (epic #2080), an additive ``why``
    array of canonical why-blocks (support_chain/ep/conflicts/supersession/
    assessment/tradeoffs/dig_deeper) for the ids surfaced in ``raw`` (zero-LLM,
    bounded batch reads; absent with the flag OFF — byte-identical). #11928:
    the anchors are the four epistemic labels, not ``:Point`` only — a goal
    (``:Object``) or a reading (``:Event``) gets a block, and ``assessment``
    names the derived judgement that decided its belief.
    """
    from tortoise.analyze import analyze
    from tortoise.navigation import entityProfile

    anchor_ids = _parse(anchor_ids)

    entity_subgraph_ids = None
    if entityId:
        try:
            proj = _get_org_sdk()._get_proj()
            # #236: HTTP mode must use the org graph, NOT the hardcoded
            # "tortoise" graph — that hardcode bypasses org isolation via
            # db.select_graph() (cross-tenant read). Stdio keeps "tortoise".
            # #9340: the SELECTED graph wins over the workspace default, or
            # the anchors come from a different graph than the analysis runs on.
            gname = (_dispatch_graph_name()
                     if _transport_mode.get() == "http" else "tortoise")
            profile = entityProfile(proj.db, gname, entityId, hops=2)
            ids = {entityId}
            for category in profile.get("connected", {}).values():
                for node in category:
                    if node.get("id"):
                        ids.add(node["id"])
            entity_subgraph_ids = ids
        except Exception:
            pass  # fall back to full-graph analysis

    # #329: bound paid outbound LLM calls per org per minute — beyond budget
    # the tool degrades to keyword-only classification.
    use_llm = _analyze_llm_budget_available()
    result = _safe(analyze, question, _get_org_sdk()._get_proj(),
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
    # W4 (#2101): additive why-layer enrichment — the additive keys ride the
    # ``why`` entries for the epistemic ids surfaced in ``raw`` (flag-gated;
    # absent with the flag OFF — the response stays byte-identical). #11928:
    # the anchors are the four labels, not ``:Point`` only — do NOT narrow them
    # back to "the Point ids", which is the #7927 partial-widening shape.
    # Zero-LLM, bounded batch reads, fail-open (no key on assembly error).
    if isinstance(result, dict) and result.get("raw"):
        try:
            from tortoise.why import (  # noqa: I001
                assemble_why_blocks, point_ids_in_raw, w4_enrichment_enabled,
            )
            if not w4_enrichment_enabled():
                return result
            _proj = _get_org_sdk()._get_proj()
            _ids = point_ids_in_raw(result["raw"])[:20]
            if _ids:
                _blocks = assemble_why_blocks(_proj, _ids)
                result["why"] = [b for pid, b in _blocks.items()
                                  if pid in _ids]
        except Exception as e:  # noqa: BLE001, RUF100 — fail-open, never break the turn
            _log.warning("W4 why-layer enrichment failed (analyze): %s", e)
            pass
    return result
