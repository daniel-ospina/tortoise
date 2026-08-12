"""Shared live-only status predicate for EP factor extraction (#780).

Draft Points/operators must never contribute to live posteriors: extraction
(Phase 2) writes Points as ``status: draft``, and without a filter at
factor-extraction time EP would propagate drafts into live posteriors
(nuclear-risk mitigation R1/R7/R8, insight-mining epic §4.5).

The predicate is shared across ALL FOUR call sites so ``include_draft=True``
re-includes drafts identically everywhere:

- ``TortoiseEP._affected_claims`` / ``TortoiseEP._affected_factors`` (ep.py)
- ``extract_svbp_factors`` (projection/__init__.py — graph-wide SVBP path)
- ``_bfs_select_operators`` (analyze.py)
- ``_select_subgraph`` (sdk.py)
"""

from __future__ import annotations


def _live_only(clause: str, include_draft: bool = False) -> str:
    """Return a Cypher status predicate excluding draft nodes.

    Args:
        clause: alias-qualified status reference, e.g. ``"n.status"``.
        include_draft: when True, returns an empty fragment (no filtering) —
            the ``run(include_draft=True)`` escape hatch.

    Legacy nodes without a stored status are LIVE (the entity write path
    defaults ``coalesce($st, n.status, 'live')``), hence the NULL check.
    """
    if include_draft:
        return ""
    return f"({clause} IS NULL OR {clause} <> 'draft')"
