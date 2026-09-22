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
byte-identical to the pre-R2 code path for inputs WITHOUT RediSearch-special
characters (escaped via :func:`escape_redisearch_literal`, see below).

Escaping is structural: tokens are alnum-only, so RediSearch reserved
characters (``|`` included) cannot be injected into the OR-union path. The
raw-query fallback path can carry punctuation — that path now backslash-
escapes RediSearch's query-syntax special characters (``escape_redisearch_literal``,
issue #1791) so a degenerate input like ``10:00`` parses instead of raising
"RediSearch: Syntax error at offset 2" and tripping the FTS circuit breaker.
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

#: A4 (#2070): the ADDITIVE query-expansion budget — how many pseudo-
#: relevance-feedback tokens (harvested from the retrieved pool's
#: ``search_keys``) may be appended BEYOND the original OR-term cap on the
#: ask lane. Bounded raise only (12 + 8 = 20 max): the expansion fills ONLY
#: the tail after the original tokens' slots are reserved (see
#: :func:`build_or_query`), so a long injected alias can never displace a
#: shorter ORIGINAL query token (the regression guard — naive appending lets
#: longer aliases crowd out original tokens and SHRINK original recall).
DEFAULT_MAX_EXPANSION_TERMS = 8

#: RediSearch fulltext-query special characters (issue #1791). Backslash is
#: RediSearch's own literal-escape mechanism — prefixing one of these makes
#: it a literal character instead of query syntax. Set = documented query
#: operators/field syntax (``|`` OR, ``-`` NOT, ``*`` wildcard, ``@`` field
#: modifier, ``:`` field separator, ``( )`` grouping, ``[ ]`` range,
#: ``{ }`` tag/filter, ``~`` optional/fuzzy, ``%`` fuzzy, ``"`` phrase,
#: ``< > =`` range/KNN, ``$`` params, ``\`` escape, ``,`` tag separator)
#: + the empirically crash-inducing ``;`` (attribute clause separator).
#: Verified against a live FalkorDB server: EVERY escaped form parses clean,
#: while the unescaped forms raise ``RediSearch: Syntax error at offset N``
#: (``10:00`` → offset 2 — the #1791 revalidation-log signature; ``@speed``
#: → offset 0; ``{urgent}`` → offset 0).
_REDI_SEARCH_SPECIAL = re.compile(r"[\\,;:\-|@~()\[\]{}<>=*%\"$]")


def escape_redisearch_literal(term: str) -> str:
    """Backslash-escape RediSearch query-syntax special chars in ``term``.

    RediSearch's documented literal-escape is a backslash prefix. Applied to
    the raw-query fallback of :func:`build_or_query` so degenerate inputs
    (≤1 surviving token) that carry punctuation can no longer raise
    "Syntax error at offset N" inside ``db.idx.fulltext.queryNodes`` and
    trip the FTS circuit breaker (issue #1791). Strict no-op for terms with
    no special chars — clean queries pass through byte-identical.
    """
    return _REDI_SEARCH_SPECIAL.sub(r"\\\g<0>", term)


def tokenize_sparse_query(query: str, *, keep_numeric: bool = False) -> list[str]:
    """Lowercase; split on non-alphanumeric; drop tokens <2 chars, all-digits,
    and stopwords. Deterministic order (source order).

    All-digits are dropped as numeric noise (a time like ``27:12`` contributes
    ``27``/``12`` — no retrieval signal), while mixed tokens like ``5k``
    survive (they are real aliases).

    A1 (#2070): ``keep_numeric=True`` KEEPS all-digit tokens — the ask-lane
    numeric-token policy. A money/quantity question that itself carries an
    amount ("how much did I spend on lights at $40?") gets retrieval signal
    from the ``40`` instead of dropping it as noise, so SAME-VALUE dollar
    turns become retrievable. Default False = the search lane stays
    byte-identical (numeric tokens still dropped). Honest scope: this fixes
    SAME-VALUE money questions only — a question with NO numeric tokens is
    untouched by this flag (e.g. the #2070 ``gpt4_d84a3211`` sum question,
    whose answer is a SUM no turn contains; that class is served by the
    vector leg / ``search_keys`` expansion instead).
    """
    if not query:
        return []
    out: list[str] = []
    for tok in _SPLIT_RE.split(query.lower()):
        if len(tok) < 2:
            continue
        if tok.isdigit() and not keep_numeric:
            continue
        if tok in SPARSE_STOPWORDS:
            continue
        out.append(tok)
    return out


def expansion_tokens(
    aliases, *,
    reserved: set[str] | None = None,
    max_terms: int = DEFAULT_MAX_EXPANSION_TERMS,
) -> list[str]:
    """A4 (#2070): build the pseudo-relevance-feedback expansion token list
    from a retrieved pool's ``search_keys`` aliases.

    Additive-only query expansion: aliases are tokenized with the ask lane's
    numeric policy (``keep_numeric=True`` — a ``"bike $40"`` alias
    contributes ``40``), stopwords/min-length tokens dropped, tokens already
    present in the ORIGINAL query's token set (``reserved``) excluded (an
    alias can never duplicate or displace an original token), deduped,
    sorted by length DESC (specificity — same rule as the OR-union cap), and
    bounded at ``max_terms`` (``DEFAULT_MAX_EXPANSION_TERMS`` = 8): the
    second FTS pass's total OR budget is ``DEFAULT_MAX_OR_TERMS + 8`` at
    most, and the original query's tokens ALWAYS keep their slots first.
    Accepts a flat string, ``None``, or an iterable of strings (the extractor
    writes ``search_keys`` as a flat space-joined STRING after
    ``_flatten_search_keys_prop``; raw graph values may still be arrays).
    """
    if not aliases:
        return []
    chunks = ([aliases] if isinstance(aliases, str)
              else [str(a) for a in aliases if a])
    reserved = reserved or set()
    seen: set[str] = set()
    out: list[str] = []
    for chunk in chunks:
        for tok in tokenize_sparse_query(chunk, keep_numeric=True):
            if tok in reserved or tok in seen:
                continue
            seen.add(tok)
            out.append(tok)
    out.sort(key=len, reverse=True)  # specificity, stable ties
    return out[:max_terms]


def build_or_query(
    query: str,
    *,
    max_terms: int = DEFAULT_MAX_OR_TERMS,
    keep_numeric: bool = False,
    expansion_terms: list[str] | None = None,
) -> str:
    """OR-union query for ``db.idx.fulltext.queryNodes``.

    ``tokenize_sparse_query`` → sort by length DESC (specificity — a longer
    token is more discriminating, so it survives the cap first), cap at
    ``max_terms``, join with ``'|'`` (RediSearch OR). ``keep_numeric``
    (A1 #2070): the ask-lane numeric-token policy — threaded to the
    tokenizer; default False keeps the search lane byte-identical.
    ``expansion_terms`` (A4 #2070): ADDITIVE pseudo-relevance-feedback
    expansion — original query tokens are capped at ``max_terms`` FIRST
    (their slots are reserved), then the expansion tail fills ONLY the
    remaining budget up to ``DEFAULT_MAX_EXPANSION_TERMS`` extra terms
    (bounded raise: 12 + 8 = 20 max). Option (i)+(ii) of the scoping
    package: reserve-original-slots (a longer injected alias can NEVER
    displace a shorter ORIGINAL query token — the regression guard) + a
    bounded max-terms raise on the ask lane only (the search lane passes
    nothing → byte-identical). A separate AND-joined OR-group (iii) was
    rejected: RediSearch treats space-separated groups as implicit AND, so
    a doc would have to match BOTH the original AND the expansion group —
    an intersection that can SHRINK original recall instead of expanding
    it. Degenerate inputs —
    empty, stopword-only, or a single surviving token — return the RAW query
    with RediSearch-special characters backslash-escaped
    (:func:`escape_redisearch_literal`): byte-compatible with the pre-R2
    passthrough for clean inputs, and never an empty query string for
    ``queryNodes`` from a NON-empty input. #1791: the escape is what keeps a
    degenerate input like ``10:00`` (all-digit tokens → 0 surviving) from
    raising "Syntax error at offset N" and tripping the FTS circuit breaker.
    Note: operator-shaped single-token queries (``meet*``, ``-x``, ``~x``)
    change semantics on the raw fallback — the escaped form is a LITERAL
    (``meet\\*``), not a RediSearch operator, so those shapes intentionally
    drop FTS recall (the index never contains the literal operator text).
    Deliberate, version-safe tradeoff: the blanket 21-char escape set is a
    superset of the empirically crash-inducing chars (10:00→offset 2,
    @speed→offset 0, {urgent}→offset 0, 100%→offset 3, [x]→offset 0,
    ;→offset 4 — #1791 revalidation log); a future engine dialect could
    reject others, so escaping all of them is dialect-safe.
    ``max_terms=0`` would produce an empty OR-union (the #1791 crash class —
    "Syntax error at offset 0"); NEGATIVE caps are treated as degenerate
    inputs (escaped-raw) rather than the ``tokens[:-1]`` slice behavior they
    previously triggered.
    """
    tokens = tokenize_sparse_query(query, keep_numeric=keep_numeric)
    if len(tokens) <= 1 or max_terms < 1:
        return escape_redisearch_literal(query)
    tokens.sort(key=len, reverse=True)  # stable — ties keep source order
    original = tokens[:max_terms]
    if not expansion_terms:
        return "|".join(original)
    extra = expansion_tokens(
        expansion_terms, reserved=set(original))
    if not extra:
        return "|".join(original)
    return "|".join(original + extra)


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
