"""Shared sparse-retrieval query builder (R2, issue #1541).

The dual-stack normalization contract (test-design surface 11): the REAL
backend runs RediSearch FTS (``db.idx.fulltext.queryNodes``), the embedded
backend falls through to sklearn TF-IDF. Both stacks must apply the SAME
token-level tolerance and the SAME search_keys-aware index text — otherwise
the eval cannot tell "FTS ran and disagreed" from "the stacks are not
comparable". This module holds the ONE tokenizer both stacks import.

Why OR-union (not phrase fallback): the tolerance requirement is per-token.
A point whose content differs from the question by ONE token is zeroed by
RediSearch's default strict-AND semantics (all whitespace terms required);
OR-union + RediSearch's TFIDF scorer ranks docs by term-overlap, which is
the BM25-style rank behavior the epic's scope prescribes (issue #1541 D1/D2).
Single-token queries and stopword-only queries fall back to the raw string —
behavior byte-identical to the pre-R2 code path.

Escaping is structural: tokens are alnum-only, so RediSearch reserved
characters (``|`` included) cannot be injected into the query. The raw-query
fallback path can carry punctuation — acceptable: it is the *legacy* string,
and degenerate-input callers today already pass it verbatim.
"""
from __future__ import annotations

import re

#: Small English stopword set (issue #1541 D1): articles, be-verbs,
#: prepositions, question words, pronouns, and the did/do/does/have/has
#: auxiliary cluster. Kept deliberately SMALL — a generic 200-word list
#: would swallow domain vocabulary (e.g. "how" in "how-to" content is
#: meaningful); these are the tokens that never carry retrieval signal.
SPARSE_STOPWORDS = frozenset({
    # articles
    "a", "an", "the",
    # be-verbs
    "am", "is", "are", "was", "were", "be", "been", "being",
    # prepositions
    "of", "in", "on", "at", "to", "for", "with", "from", "by", "about",
    # question words
    "what", "when", "where", "who", "whom", "which", "how", "why",
    # pronouns
    "i", "you", "me", "my", "your", "we", "us", "our", "they", "them",
    "their", "he", "him", "his", "she", "her", "it", "its",
    # auxiliary / tense cluster
    "did", "do", "does", "have", "has", "had",
    # connectives
    "and", "or", "but", "not",
})

#: Split on anything that is not [a-z0-9] (case lowered first) — the
#: structural-escape property: tokens can never carry ``|``, ``(``, ``@``,
#: or any RediSearch reserved operator character.
_SPLIT_RE = re.compile(r"[^a-z0-9]+")

#: Default OR-term cap (breaker protection, #249). Bounds pathological
#: queries; a "one-token-drop" paraphrase loses nothing (the cap only bites
#: at >= 13-token questions, which the eval never produces).
DEFAULT_MAX_OR_TERMS = 12


def tokenize_sparse_query(query: str) -> list[str]:
    """Lowercase; split on non-alphanumeric; drop tokens <2 chars, all-digits,
    and stopwords. Deterministic order (source order).

    All-digits are dropped as numeric noise (a time like ``27:12`` contributes
    ``27``/``12`` — no retrieval signal), while mixed tokens like ``5k``
    survive (they are real aliases).
    """
    if not query:
        return []
    out: list[str] = []
    for tok in _SPLIT_RE.split(query.lower()):
        if len(tok) < 2:
            continue
        if tok.isdigit():
            continue
        if tok in SPARSE_STOPWORDS:
            continue
        out.append(tok)
    return out


def build_or_query(query: str, *, max_terms: int = DEFAULT_MAX_OR_TERMS) -> str:
    """OR-union query for ``db.idx.fulltext.queryNodes``.

    ``tokenize_sparse_query`` → sort by length DESC (specificity — a longer
    token is more discriminating, so it survives the cap first), cap at
    ``max_terms``, join with ``'|'`` (RediSearch OR). Degenerate inputs —
    empty, stopword-only, or a single surviving token — return the RAW query
    unchanged: byte-compatible with the pre-R2 passthrough and never an
    empty query string for ``queryNodes``.
    """
    tokens = tokenize_sparse_query(query)
    if len(tokens) <= 1:
        return query
    tokens.sort(key=len, reverse=True)  # stable — ties keep source order
    return "|".join(tokens[:max_terms])


def index_text(*parts) -> str:
    """Dual-stack index-text builder: ' '.join of non-empty parts.

    The indexed text is content ∪ search_keys — the TF-IDF fallback stacks
    (``fallback_snapshot`` and ``fallback_tfidf``) mirror the real backend's
    multi-field Point FTS index with it. Accepts ``str``, ``None``, and
    list/tuple parts: E3's pre-flatten graph values are arrays (and the
    eval ingest passes lists), so each element joins as its own chunk —
    ``index_text("a b", ["c d", "e"]) == "a b c d e"``.
    """
    chunks: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (list, tuple)):
            for item in part:
                s = str(item).strip()
                if s:
                    chunks.append(s)
            continue
        s = str(part).strip()
        if s:
            chunks.append(s)
    return " ".join(chunks)
