"""Shared live-only status predicate for EP factor extraction (#780, #2422).

Draft Points/operators must never contribute to live posteriors: extraction
(Phase 2) writes Points as ``status: draft``, and without a filter at
factor-extraction time EP would propagate drafts into live posteriors
(nuclear-risk mitigation R1/R7/R8, insight-mining epic §4.5).

Terminal Points (``retracted`` / ``superseded`` / ``archived`` / ``outdated``
status, or the legacy ``outdated=true`` flag that ``invalidate_point`` writes
without touching status) must NEVER contribute — a dead claim's ghost must
not vote (eval-spec P6.3; ontology §5 terminal states; #2422). The terminal
exclusion is UNCONDITIONAL: ``include_draft=True`` re-includes DRAFTS only,
never terminal points.

The predicate is shared across ALL FOUR call sites so ``include_draft=True``
re-includes drafts identically everywhere:

- ``TortoiseEP._affected_claims`` / ``TortoiseEP._affected_factors`` (ep.py)
- ``extract_svbp_factors`` (projection/__init__.py — graph-wide SVBP path)
- ``_bfs_select_operators`` (analyze.py)
- ``_select_subgraph`` (sdk.py)
"""

from __future__ import annotations

# Terminal statuses — a Point in any of these is dead for EP factor
# extraction (ontology §5: retracted/superseded are terminal; outdated is the
# legacy flag-status supersede/invalidate write; archived is reserved;
# deprecated is written by legacy/assessment paths and already excluded from
# every read surface — search_engine + recall_state — so EP must not let it
# vote either). Mirrors the read-surface vocabulary
# (search_engine.TERMINAL_EXCLUDED_STATUSES).
TERMINAL_EXCLUDED_STATUSES = frozenset(
    {"retracted", "superseded", "outdated", "archived", "deprecated"})


def _terminal_excluded(clause: str,
                       excluded=TERMINAL_EXCLUDED_STATUSES,
                       *,
                       include_outdated_flag: bool = True) -> str:
    """Cypher predicate: the node's status is NOT terminal AND (unless
    ``include_outdated_flag=False``) its legacy ``outdated`` flag is not true.

    ``clause`` is an alias-qualified status reference (``"n.status"``); the
    flag lives on the same alias (``"n.outdated"``). ``outdated=true`` is a
    second, flag-based dead marker (``invalidate_point`` sets the flag and
    leaves status untouched) so both must be excluded. Legacy nodes without a
    stored status are LIVE (the entity write path defaults
    ``coalesce($st, n.status, 'live')``), hence the NULL check.

    #2977: Objects have no ``outdated`` concept, so the Object lanes pass
    ``include_outdated_flag=False`` — otherwise an ``outdated=true`` OBJECT is
    hidden even though no Object writer can set that flag. #2490: this is the
    single composer used by the FOUR READ-SURFACE exclusion clauses.

    Scope of that claim, stated because the wording overshot it twice: the
    positive-direction TWIN (``_terminal_expression``, ``_alive_flag``,
    ``is_terminal_status``) shares the same vocabulary and is NOT this
    function; it takes no ``include_outdated_flag`` carve-out. And four further
    EXCLUSION-direction compositions exist elsewhere (``sdk.py`` ep-dirty
    sweep, ``sdk.py`` per-id re-mark, ``indexer/github_indexer.py``, ``sdk.py``
    delete) — all four are ``MATCH (n:Point)``-GATED and can never be reached by
    an ``:Object`` node, so they are registered for the vocabulary-drift class
    only, NOT as Object-reachable sites. The one genuinely Object-reachable
    divergent reader is the ``_ask_d8_decoration_unavailable`` reader — see
    follow-up (j). So the accurate claim is: **this is the single composer used
    by the four READ-SURFACE exclusion clauses — not the only exclusion
    composition in the repo.**
    """
    if not excluded:
        return ""                      # audit/full-scan opt-in
    alias = clause.split(".", 1)[0] if "." in clause else clause
    chain = " AND ".join(f"{clause} <> '{s}'" for s in sorted(excluded))
    expr = f"(({clause} IS NULL OR ({chain}))"
    if include_outdated_flag:
        expr += f" AND coalesce({alias}.outdated, false) = false"
    return expr + ")"


# #2490 (terminal posterior freeze): a terminalized claim's posterior pins at
# its pre-terminal value (the 0.904 repro) unless the terminalizing WRITER
# neutralizes it — decay to vacuity atomically with the status/flag write.
# ep_alpha/ep_beta are deliberately NOT decayed: they are the persisted prior
# history and the SOLE recovery vector should a terminal state ever be reversed
# (no unsupersede path exists today). Decay is UNIFORM across #2421 Case-1
# restatement and Case-2 correction (the old claim is terminal either way; the
# successor recomputes independently). Defined HERE (live.py is a leaf — sdk.py
# imports live.py and projection/entities.py can import it without a cycle via
# sdk.py:31's `from .projection import`).
def decay_clause(alias: str) -> str:
    """Cypher SET fragment decaying a terminalizing claim to vacuity.

    Appends ``{alias}.confidence=0.5, {alias}.posterior_alpha=1.0,
    {alias}.posterior_beta=1.0`` to a SET clause — crash-atomic with the
    status/flag write it rides (single statement). ``alias`` is the node
    variable (``"n"`` for retract/supersede/invalidate/folds, ``"p"`` for
    assess_source's older-assessment SET). Reading a decayed claim back:
    coalesce(posterior_alpha, ep_alpha, 1.0) = 1.0 and confidence = 0.5 —
    the vacuous Beta(1,1) posterior mean.
    """
    return (f"{alias}.confidence=0.5, {alias}.posterior_alpha=1.0, "
            f"{alias}.posterior_beta=1.0")


def _terminal_expression(clause: str) -> str:
    """Cypher boolean expression: TRUE when the node is terminal.

    Composes the SAME vocabulary as ``_terminal_excluded`` (status in
    TERMINAL_EXCLUDED_STATUSES OR the legacy ``outdated=true`` flag) but in
    the POSITIVE direction, usable in RETURN projections, CASE conditions and
    WHERE OR-chains. ``_terminal_excluded`` is a keep-filter whose
    NULL-status handling (legacy nodes = live) means its bare negation is
    safe to use here: legacy nodes with no status are NOT terminal.
    """
    alias = clause.split(".", 1)[0] if "." in clause else clause
    members = " OR ".join(
        f"{alias}.status = '{s}'" for s in sorted(TERMINAL_EXCLUDED_STATUSES))
    return (f"(({alias}.status IS NOT NULL AND ({members})) "
            f"OR coalesce({alias}.outdated, false) = true)")


def _alive_flag(clause: str) -> str:
    """Cypher projection boolean: TRUE when the node is NOT terminal.

    CASE-wrapped inverse of ``_terminal_expression`` — bare boolean chains
    can be rejected in RETURN projection positions, so the WHERE-in-CALL
    adapter keeps the shared composition well-formed as an expression
    (``_terminal_expression`` itself is the same composition already wrapped
    in a single parenthesized boolean, safe to negate).
    """
    return f"(CASE WHEN {_terminal_expression(clause)} THEN false ELSE true END)"


def is_terminal_status(status, outdated: bool = False) -> bool:
    """Python mirror of the Cypher terminal predicate (status in the
    terminal vocab OR the legacy ``outdated=true`` flag). NULL/absent status
    is LIVE (the entity write path defaults ``coalesce($st, n.status,
    'live')``) — mirrors ``_terminal_excluded``'s NULL handling."""
    return (status or "") in TERMINAL_EXCLUDED_STATUSES or bool(outdated)


def _live_only(clause: str, include_draft: bool = False) -> str:
    """Return a Cypher predicate excluding draft AND terminal nodes.

    Args:
        clause: alias-qualified status reference, e.g. ``"n.status"``.
        include_draft: when True, drafts are re-included (the
            ``run(include_draft=True)`` escape hatch) — terminal nodes are
            NEVER re-included (#2422).

    Legacy nodes without a stored status are LIVE (the entity write path
    defaults ``coalesce($st, n.status, 'live')``), hence the NULL checks.
    """
    terminal = _terminal_excluded(clause)
    if include_draft:
        return terminal
    return f"(({clause} IS NULL OR {clause} <> 'draft') AND {terminal})"
