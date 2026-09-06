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
