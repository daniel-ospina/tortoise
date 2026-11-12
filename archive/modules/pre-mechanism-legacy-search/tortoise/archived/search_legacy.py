# Archived module `tortoise.archived.search_legacy` — REMOVED from the product surface (#11609).
#
# batch:   pre-mechanism-legacy-search
# reason:  pre-mechanism archive: shipped importable inside the package; moved out of the product (#11609 batch 0)
# source:  tortoise/archived/search_legacy.py @ f0da5505b38c8656c7ef54a91f91be05348a8fe9
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'module'
ARCHIVED_SYMBOL = 'tortoise.archived.search_legacy'
ARCHIVED_BATCH = 'pre-mechanism-legacy-search'
ARCHIVED_FROM = 'tortoise/archived/search_legacy.py'
ARCHIVED_AT = 'f0da5505b38c8656c7ef54a91f91be05348a8fe9'

# ── verbatim from tortoise/archived/search_legacy.py ──

"""ARCHIVED: Legacy search() implementation from before Phase 0 hybrid search (#7748).

Removed 2026-08-03. Replaced by tortoise_fts_query().
This code is preserved for reference only — do not import from here.
"""

# The search() method was removed from TortoiseSDK. 
# It performed in-memory TF-IDF / sentence-transformers search over all Points.
# 
# Migration:
#   sdk.search(q, kind=k, context=c) → sdk.tortoise_fts_query(q, kind=k, context=c)
#
# The new method returns SearchResult dicts with:
#   - RRF fusion of FTS + vector + structural indexes
#   - EP breakdown (confidence_mean, evidence, contention)
#   - match_source field
#   - Full-scan mode when query is None + context is set

# Original implementation (for reference):
#
# def search(self, query: str, kind: str | None = None,
#            context: str | None = None, *,
#            threshold: float = 0.3, limit: int = 10) -> list[dict]:
#     """Semantic/vector search over Points."""
#     if limit < 1:
#         raise ValueError(f"limit must be >= 1, got {limit}")
#     if not (0.0 <= threshold <= 1.0):
#         raise ValueError(f"threshold must be 0.0-1.0, got {threshold}")
#     from .embeddings import search_points
#     points = self.query(kind=kind, context=context)
#     return search_points(query, points, threshold=threshold, limit=limit)
