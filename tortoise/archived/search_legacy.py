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
