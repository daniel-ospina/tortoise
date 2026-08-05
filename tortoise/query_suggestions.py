"""Query suggestions for silent-empty query results (#49 task 1.4).

Provides Levenshtein-based "did you mean?" suggestions for misspelled
kind/namespace names, and a distinct hint when the kind is valid but
simply has 0 points in the graph.

No external dependencies — Levenshtein is implemented inline.
"""
from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

# Max edit distance for typo suggestions (<= 2 = common typos)
_MAX_DISTANCE = 2


# ── Levenshtein (edit distance) ────────────────────────────────────────

def levenshtein(s: str, t: str) -> int:
    """Compute Levenshtein edit distance between two strings.

    Standard dynamic-programming implementation — insert, delete, substitute.
    """
    if len(s) < len(t):
        s, t = t, s
    # s is the longer string
    m, n = len(s), len(t)
    # Two-row DP — O(m*n) time, O(n) space
    prev = list(range(n + 1))
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        curr[0] = i
        for j in range(1, n + 1):
            cost = 0 if s[i - 1] == t[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,       # delete
                curr[j - 1] + 1,   # insert
                prev[j - 1] + cost  # substitute
            )
        prev, curr = curr, prev
    return prev[n]


# ── Suggestion engine ──────────────────────────────────────────────────

def suggest_kind(query_kind: str, known_kinds: list[str],
                 max_suggestions: int = 3) -> list[str]:
    """Return closest known kinds by Levenshtein distance to a misspelled kind.

    Only suggestions within edit distance <= 2 (typographical).
    Sorted by distance, then alphabetically.

    Args:
        query_kind: The kind the user typed (potentially misspelled).
        known_kinds: All known kind names (from pack registry).
        max_suggestions: Maximum number of suggestions to return.

    Returns:
        List of matching kind names, or empty list if no close match.
    """
    # Handle namespace-prefixed kinds (e.g., "product-strategy:featuer")
    if ":" in query_kind:
        ns, local = query_kind.split(":", 1)
        # Filter known kinds to same namespace
        ns_prefix = f"{ns}:"
        candidates = [k for k in known_kinds if k.startswith(ns_prefix)]
        # Compare against the local part
        scored = []
        for candidate in candidates:
            _cns, clocal = candidate.split(":", 1)
            dist = levenshtein(local, clocal)
            if dist <= _MAX_DISTANCE:
                scored.append((dist, candidate))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [s[1] for s in scored[:max_suggestions]]

    # Bare kind — compare against both local parts and full prefixed forms
    scored = []
    for candidate in known_kinds:
        if ":" in candidate:
            _ns, clocal = candidate.split(":", 1)
            dist_local = levenshtein(query_kind, clocal)
            dist_full = levenshtein(query_kind, candidate)
            dist = min(dist_local, dist_full)
        else:
            dist = levenshtein(query_kind, candidate)
        if dist <= _MAX_DISTANCE:
            scored.append((dist, candidate))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [s[1] for s in scored[:max_suggestions]]


def _collect_known_kinds() -> list[str]:
    """Collect all pointKinds + objectKinds from canonical vocabulary + packs.

    Returns both prefixed (namespace:kind) and bare (kind) forms so that
    suggest_kind can match against either representation.
    """
    try:
        from .pack_registry import (
            PackRegistry,
            CANONICAL_POINT_KINDS,
            CANONICAL_OBJECT_KINDS,
        )
        from pathlib import Path as _Path
        packs_dir = _Path(__file__).resolve().parent.parent / "packs"
        registry = PackRegistry(packs_dir)
        registry.load_all()
        kinds: list[str] = []
        # Canonical base vocabulary (what CAN exist by default)
        kinds.extend(sorted(CANONICAL_POINT_KINDS))
        kinds.extend(sorted(CANONICAL_OBJECT_KINDS))
        # Pack kinds
        all_kinds = registry.list_all_kinds()
        for kind_field in ("pointKinds", "objectKinds"):
            for k in all_kinds.get(kind_field, []):
                kinds.append(k)
                if ":" in k:
                    # Also add bare form for matching
                    kinds.append(k.split(":", 1)[1])
        # Deduplicate
        return sorted(set(kinds))
    except Exception:
        _logger.warning("Failed to load pack registry for query suggestions", exc_info=True)
        return []


def compute_suggestion(kind: str) -> str | None:
    """Compute a suggestion/hint for a kind that returned 0 results.

    Two cases:
    1. Kind IS a valid registered kind → hint that it exists but has 0 points
    2. Kind is NOT valid → "did you mean" Levenshtein suggestions

    Returns a suggestion string, or None if no suggestion can be made.
    """
    known = _collect_known_kinds()
    if not known:
        return None

    # Check if the kind is a valid registered kind
    # Match against both full prefixed forms and bare forms
    is_valid = kind in known
    if not is_valid and ":" not in kind:
        # bare kind — check if any prefixed form matches
        for k in known:
            if ":" in k and k.split(":", 1)[1] == kind:
                is_valid = True
                break

    if is_valid:
        # Valid kind, but 0 points — give a hint (not did-you-mean)
        return (
            f"kind '{kind}' is valid but has 0 points in the graph. "
            "Try list_pointkinds() to see kinds present in the graph, "
            "or list_sources() to browse by provenance."
        )

    # Not valid — try Levenshtein suggestions
    suggestions = suggest_kind(kind, known)
    if suggestions:
        quoted = ", ".join(f"'{s}'" for s in suggestions)
        return f"kind '{kind}' not found. Did you mean: {quoted}?"

    return None


# ── SDK wrapper ────────────────────────────────────────────────────────

def query_with_suggestions(query_fn, kind: str | None = None,
                           context: str | None = None,
                           **filters) -> dict:
    """Call query_fn (e.g., sdk.query) and attach suggestion for silent-empty results.

    Returns:
        dict with keys:
        - results: the query results list
        - suggestion: (only present when results empty and kind provided)
          a hint or "did you mean" string
    """
    results = query_fn(kind, context, **filters)

    if results:
        return {"results": results}

    if not kind:
        return {"results": results}

    suggestion = compute_suggestion(kind)
    if suggestion:
        _logger.warning("Query suggestion: %s", suggestion)
        return {"results": results, "suggestion": suggestion}
    return {"results": results}
