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

# ══════════════════════════════════════════════════════════════════════════
# #2901 — THE canonical Point-status partition. ONE declaration, imported by
# every reader that must skip non-current Points.
#
# Do NOT re-declare a status set at a call site. A hand-written subset is how
# ``outdated`` was omitted from three reader filters (#2901: github_indexer,
# audit_beta_gate, 1714_dedup_observation) and a superseded/outdated claim was
# then served as the CURRENT statement. Import ``TERMINAL_EXCLUDED_STATUSES``
# from HERE instead.
#
# live.py is the LEAF status module: ``tortoise/sdk.py`` imports it (sdk.py:
# ``from .live import TERMINAL_EXCLUDED_STATUSES``), so the module can be
# imported by every consumer without a cycle. Because it is below sdk.py it
# cannot import the full vocabulary (``POINT_STATUS_VALUES``, canonical in
# ``tortoise/sdk.py``) at import time. The partition below is therefore held
# to that vocabulary by ``tests/test_terminal_status_vocabulary.py``, which
# DERIVES the expected terminal set as
# ``POINT_STATUS_VALUES - CURRENT_POINT_STATUS_VALUES`` and asserts equality —
# so adding a status to the vocabulary without classifying it here REDs that
# test instead of silently defaulting to "current" on every read surface.
# (A parity test, not import-time coercion, is the guard: the alternative —
# declaring POINT_STATUS_VALUES here — would need sdk.py to stop declaring it,
# and a circular import makes the reverse direction impossible.)
# ══════════════════════════════════════════════════════════════════════════

#: The non-terminal members of the Point vocabulary — a Point in one of these
#: is CURRENT (draft is not-yet-published, not dead).
CURRENT_POINT_STATUS_VALUES = frozenset({"draft", "live"})

#: The vocabulary's terminal members (ontology §5: retracted/superseded are
#: terminal; ``outdated`` is the legacy supersede/invalidate status;
#: ``archived`` is reserved — no v1 write path).
TERMINAL_STATUS_VALUES = frozenset(
    {"retracted", "superseded", "outdated", "archived"})

#: ``deprecated`` is deliberately NOT in ``POINT_STATUS_VALUES``: no SDK/API
#: write path emits it (only direct graph writes / legacy assessment paths
#: ever did — see tests/test_lifecycle_guards.py, which asserts its absence),
#: so it is not a legal create-time status. It IS present in legacy graphs and
#: must never be served as current, so it joins the EXCLUSION set below but
#: not ``TERMINAL_STATUS_VALUES``. #2901 inverse-shape ruling: the vocabulary
#: is right (no writer) and the recall_state test that sets ``n.status =
#: 'deprecated'`` directly is a legitimate simulation of legacy graph data.
LEGACY_NON_CURRENT_STATUS_VALUES = frozenset({"deprecated"})

#: Every status a read surface (FTS/vector/structural, EP factor extraction,
#: recall_state, the SDK query paths, the indexers) must treat as NOT current.
#: ``None``/absent status is separately LIVE (legacy nodes — see
#: ``_terminal_excluded``'s NULL handling).
TERMINAL_EXCLUDED_STATUSES = (
    TERMINAL_STATUS_VALUES | LEGACY_NON_CURRENT_STATUS_VALUES)


def _terminal_excluded(clause: str) -> str:
    """Cypher predicate: the node's status is NOT terminal AND its legacy
    ``outdated`` flag is not true.

    ``clause`` is an alias-qualified status reference (``"n.status"``); the
    flag lives on the same alias (``"n.outdated"``). ``outdated=true`` is a
    second, flag-based dead marker (``invalidate_point`` sets the flag and
    leaves status untouched) so both must be excluded. Legacy nodes without a
    stored status are LIVE (the entity write path defaults
    ``coalesce($st, n.status, 'live')``), hence the NULL check.
    """
    alias = clause.split(".", 1)[0] if "." in clause else clause
    flag = f"{alias}.outdated"
    chain = " AND ".join(f"{clause} <> '{s}'" for s in sorted(TERMINAL_EXCLUDED_STATUSES))
    return (f"(({clause} IS NULL OR ({chain})) "
            f"AND coalesce({flag}, false) = false)")


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
