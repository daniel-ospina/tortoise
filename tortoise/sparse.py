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

from .env_truthy import env_flag

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

#: #8933: the identifier-preservation switch. The owner RULING on #8933
#: reopened the #2070 ``keep_numeric=False`` byte-identity control for
#: IDENTIFIER-SHAPED queries only, so the mechanism ships ON; the variable is
#: the fail-safe kill switch the ruling asks for, so a batch whose aggregate
#: retrieval disappoints can be BISECTED without a revert. Resolved through
#: :func:`tortoise.env_truthy.env_flag` (unset/blank/garbage -> the default), so
#: a typo can never silently turn it on OR off; ``0``/``false``/``no``/``off``
#: disables it.
IDENTIFIER_ENV = "TORTOISE_IDENTIFIER_TOKENS"

#: The conservative identifier GRAMMAR (#8933 ruling, risk 1: "Start
#: conservative — multi-segment codes (``ERR-4032``, ``INV-2024-0091``) and
#: widen only on measurement; do not ship a broadened pattern speculatively").
#:
#: A span is a candidate when it STARTS with a letter and carries at least one
#: ``-``/``_`` separator followed by another alphanumeric segment. That is
#: deliberately narrower than "letters + separator + digits": ``.``, ``/`` and
#: ``#`` are NOT separators yet (version strings, paths and issue refs are the
#: widening step, measured separately), and a span that starts with a digit
#: (``2024-01-15``, ``10:00``) is never a candidate, so dates and times keep the
#: #2070 numeric-noise treatment.
_IDENTIFIER_SPAN_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+(?![A-Za-z0-9])"
)

#: Segment separators INSIDE a candidate span. A SPACE counts: the ruling
#: requires that the alias "matches however the person typed it — dashes,
#: spaces, or none", so ``ERR-4032`` and ``ERR 4032`` normalise to the SAME
#: form. This is a NORMALISER rule, not a DETECTION rule: what counts as a
#: candidate span is decided by the extractors below (and the write side is the
#: conservative half — see :func:`identifier_aliases`).
#:
#: A TAB counts too, and for a structural reason: **this class must split on
#: every whitespace ``_SPACED_PAIR_RE`` admits** (``[ \t]+``). #10203 review
#: round 11 (P2) found the two had drifted — the extractor accepted ``[ \t]+``
#: while this class had no tab — so ``_identifier_segments("ERR\t4032")``
#: returned ONE segment, :func:`identifier_alias` bailed at
#: ``len(segments) < 2``, and the extractor's own candidate was silently thrown
#: away. The measured consequence: ``deploy invoice<TAB>2024`` produced the OR
#: union ``invoice|deploy``, i.e. the IDENTICAL query for 2024 and 2023 — the
#: #8933 defect (two codes one digit apart are one query) re-entering through
#: the one whitespace spelling the read side's regex exists to accept, and the
#: shape a code pasted out of a table or a spreadsheet actually has. The
#: extractor's separator alphabet and this set must not drift again: widen the
#: extractor, and widen this with it.
_IDENTIFIER_SEP_RE = re.compile(r"[-_ \t]")

#: READ-SIDE-ONLY extraction of the space-typed shape: a letters token, one or
#: more spaces, then one or more all-digit code segments of at least two digits
#: (``ERR 4032``, ``capstone 9664``, ``INV 2024 0091``). This is the shape the
#: ruling MEASURED at rank 1 of 16 (the adjacency phrase ``"capstone 9664"``),
#: so the read side must recognise it even though the WRITE side does not mint
#: aliases from prose (a document saying "sprint 2024" must not spend index
#: space on ``sprint2024``; a user typing a code into one query may, and its
#: noise is bounded to that query). The letters side may not be a stopword
#: (``is 27:12`` is prose, not a code), and the digit run may not be the HEAD of a
#: longer punctuated span (``date 2024-01-15`` is a DATE — its ``2024`` is
#: followed by ``-``, so that span keeps the #2070 numeric-noise treatment).
#:
#: ``[ \t]+`` admits a TAB after the letters segment, because a code pasted out
#: of a table or a spreadsheet is tab-separated and the index splits on
#: non-alphanumerics anyway (TAB is a separator to it exactly as SPACE is).
#: **Whatever this regex admits, :data:`_IDENTIFIER_SEP_RE` MUST split on** —
#: an extractor that yields a span the normaliser cannot divide produces no
#: alias and no phrase, i.e. the candidate is silently discarded. #10203 review
#: round 11 (P2) found exactly that drift (``[ \t]+`` here, ``[-_ ]`` there).
#:
#: #10203 review round 7 (F3): the guard was ``[-_]`` only, so a TIME or a
#: DECIMAL minted an alias and an adjacency phrase — ``meeting 27:12`` gave
#: ``meeting27`` + ``"meeting 27"`` and ``spent 27.5 hours`` gave ``spent27`` +
#: ``"spent 27"``. That is the ruling's ``27:12``/``$40`` class coming back
#: through a side door (both the ruling and this PR's body say those contribute
#: NONE), and the phrase then prefers the WRONG document: ``"meeting 27"``
#: matches "meeting 27 covers the rollout" while the document actually about the
#: 27:12 meeting (tokens ``27, 12, meeting``) does not. Widened to every
#: separator that makes the run the head of a bigger token, plus ``%`` (a
#: percentage is one quantity, not a code).
#:
#: The guard is ``<punct><digit>`` rather than a bare ``<punct>`` ON PURPOSE: a
#: sentence-final ``deploy ERR 4032.`` must keep minting ``err4032``. Only a
#: punctuation mark that CONTINUES into more digits is disqualifying.
#: #10203 review round 6 (P2): the letters segment uses the WRITE side's OWN
#: alphabet (``[A-Za-z][A-Za-z0-9]*``), not ``[A-Za-z]{2,}``. The narrower form
#: disagreed with ``_IDENTIFIER_SPAN_RE`` on every one-character prefix —
#: ``X-4032`` minted the alias ``x4032`` while ``X 4032`` returned NOTHING (it
#: fell to the degenerate escaped-RAW branch) — which breaks the ruling's
#: explicit promise that the alias matches "however the person typed it —
#: dashes, spaces, or none". The read side is allowed to be the PERMISSIVE
#: half, so the fix widens IT rather than narrowing what the write side mints.
_SPACED_PAIR_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"([A-Za-z][A-Za-z0-9]*(?:[ \t]+\d{2,}(?![-_:.,%/]\d|%))+)"
    r"(?![A-Za-z0-9])"
)


def _spaced_pair_spans(text: str) -> list[str]:
    """Space-typed identifier candidates (read side only), stopwords excluded.

    The stopword test is on the FIRST segment (the letters half) — ``is 27:12``
    is prose whose second half happens to survive the splitter, and it must not
    mint an alias.
    """
    out: list[str] = []
    for match in _SPACED_PAIR_RE.finditer(text):
        span = match.group(1)
        first = _IDENTIFIER_SEP_RE.split(span)[0].lower()
        if first in SPARSE_STOPWORDS:
            continue
        out.append(span)
    return out

#: Bounds. A candidate whose normalised form would exceed the length, or whose
#: span carries more segments, is not an identifier we index — this is what
#: keeps a pathological input from minting a giant single index token.
MAX_IDENTIFIER_LEN = 48
MAX_IDENTIFIER_SEGMENTS = 6

#: The discriminator test (the reason this whole module change exists): at
#: least one segment must be ALL-DIGITS and at least two digits long. A span
#: with no digit segment (``state-of-the-art``, ``self-hosted``) is prose, not
#: a code, and a one-digit segment (``A-1``) is too weak to be a code. This is
#: also what keeps ``COVID-19``-shaped misfires cheap: one alias token, no
#: bare numeric noise (the ``19`` never becomes its own OR term).
MIN_DIGIT_SEGMENT_LEN = 2

#: How many alias tokens ONE Point may carry (bounds index growth; the write
#: path is idempotent, so this is a ceiling, never a churn).
MAX_ALIASES_PER_POINT = 8


def identifier_tokens_enabled() -> bool:
    """Is identifier preservation on for this process? (#8933)

    Default ON (the ruling reopened the byte-identity control); the env var is
    the bisect switch, not the feature flag. See :data:`IDENTIFIER_ENV`.
    """
    return env_flag(IDENTIFIER_ENV, True)


def _identifier_segments(span: str) -> list[str]:
    """The alphanumeric segments of a candidate span, in source order."""
    return [s for s in _IDENTIFIER_SEP_RE.split(span) if s]


def identifier_alias(span: str) -> str | None:
    """#8933 — **THE ONE NORMALISER**. ``ERR-4032`` -> ``err4032``.

    This function is the load-bearing invariant of the ruling: the SAME
    normaliser must run on the WRITE path (the alias written into the indexed
    text) and the READ path (the alias emitted as a query term). If the two
    sides ever derive different forms, every identifier query silently returns
    nothing and the failure reads as "search is bad" rather than "the halves
    disagree" (#8933 ruling, Stage 2). So both sides call THIS function and
    nothing else — never a re-implementation, never a regex copy.

    Why the separators are DROPPED rather than kept: the graph's fulltext
    index splits indexed text on non-alphanumerics, so a term that still
    carried ``-`` could never match anything (measured on #8933 — the escaped
    composite ``capstone\\-9664`` returns ZERO rows). The concatenated form is
    a single alphanumeric token that the index stores whole, on both stacks.

    Returns ``None`` when the span is not identifier-shaped under the
    conservative grammar (:data:`_IDENTIFIER_SPAN_RE` + the digit-segment
    requirement + the bounds), i.e. when preserving it would only add noise.
    """
    if not span or len(span) > MAX_IDENTIFIER_LEN:
        return None
    segments = _identifier_segments(span)
    if len(segments) < 2 or len(segments) > MAX_IDENTIFIER_SEGMENTS:
        return None
    has_letters = any(any(c.isalpha() for c in s) for s in segments)
    has_code_digits = any(
        s.isascii() and s.isdigit() and len(s) >= MIN_DIGIT_SEGMENT_LEN
        for s in segments
    )
    if not (has_letters and has_code_digits):
        return None
    alias = "".join(segments).lower()
    if not (alias.isascii() and alias.isalnum()):
        return None
    return alias


def identifier_aliases(text: str, *, space_separated: bool = True) -> list[str]:
    """#8933: the normalised alias terms a text implies, deduped, source order.

    Used by BOTH halves of the mechanism: the write path appends these to the
    Point's ``search_keys`` (the alias channel already in the ``Point``
    fulltext field set), and the read path emits them as OR terms. One
    extractor, one normaliser, so the two cannot drift by construction.

    ``space_separated`` is the ONE deliberate asymmetry, and it is about
    PRECISION, not about form:

    * ``True`` (the read path, the default) also accepts ``ERR 4032`` — the
      space-typed shape the ruling measured, and the shape a person actually
      types into a search box. Its cost is bounded: a spurious alias is one
      extra OR term in ONE query.
    * ``False`` (the WRITE path) accepts only an explicit ``-``/``_``
      separator. Prose must never mint index aliases — a document saying
      "sprint 2024" would otherwise store ``sprint2024`` forever.

    Both sides derive the FORM through :func:`identifier_alias`, so
    ``ERR-4032``, ``ERR 4032`` and ``err4032`` all meet at the same token.
    """
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    spans = [m.group(0) for m in _IDENTIFIER_SPAN_RE.finditer(text)]
    if space_separated:
        spans += _spaced_pair_spans(text)
    for span in spans:
        alias = identifier_alias(span)
        if alias and alias not in seen:
            seen.add(alias)
            out.append(alias)
            if len(out) >= MAX_ALIASES_PER_POINT:
                break
    return out


def identifier_phrase_terms(text: str, *, space_separated: bool = True) -> list[str]:
    """#8933 Stage 1: RediSearch ADJACENCY phrases for identifier spans.

    ``ERR-4032`` becomes the phrase ``"err 4032"`` — an adjacency constraint
    over the two terms the index ALREADY stores, with no new token and no new
    indexed text, so it works on records written before this change existed.
    Measured on #8933: the just-written exact record goes from 16th of 16 to
    1st of 16.

    It is the SPACE form of the identifier that makes this the narrow shape:
    the index always stores the segments as separate adjacent terms, so the
    phrase fires on ``ERR-4032``, ``ERR 4032`` and ``ERR4032`` documents alike
    — while a document that merely CONTAINS the digits somewhere else does not
    match at all.

    Note what it is NOT: it does not admit a bare numeric OR term, so ``27:12``
    and ``$40`` keep the #2070 noise treatment — the digits appear only inside
    a phrase that also requires the letters beside them.

    A segment shorter than two characters is dropped from the phrase rather
    than approximated (both this module and the index discard 1-char tokens,
    so a phrase over one could never match): the alias term still carries the
    discrimination on the write side.
    """
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    spans = [m.group(0) for m in _IDENTIFIER_SPAN_RE.finditer(text)]
    if space_separated:
        spans += _spaced_pair_spans(text)
    for span in spans:
        if identifier_alias(span) is None:
            continue
        segments = [s.lower() for s in _identifier_segments(span)]
        if any(len(s) < 2 or not (s.isascii() and s.isalnum()) for s in segments):
            continue
        phrase = '"' + " ".join(segments) + '"'
        if phrase not in seen:
            seen.add(phrase)
            out.append(phrase)
            # Capped for the same reason the aliases are (:data:`MAX_ALIASES_PER_POINT`):
            # ``build_or_query`` appends this tail after the original tokens and
            # the expansion tail, so an uncapped phrase list stops the OR budget
            # being a bound at all — a query with 3,000 identifier spans minted
            # 3,000 adjacency constraints, a query string proportional to its
            # input, and an over-limit query is recorded as an FTS FAILURE
            # (``_breaker_record("fts", False)``), i.e. it degrades later searches
            # rather than only failing this one. The bound is already more
            # discrimination than one query can carry; the tail is a raise, not
            # a rewrite.
            if len(out) >= MAX_ALIASES_PER_POINT:
                break
    return out


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


def tokenize_sparse_query(
    query: str, *, keep_numeric: bool = False, keep_identifiers: bool = True,
    space_separated_identifiers: bool = True,
) -> list[str]:
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

    #8933 (owner ruling, 2026-10-26 — the reopen of the #2070 byte-identity
    control): an IDENTIFIER-SHAPED span also contributes its normalised alias
    (``ERR-4032`` -> ``err4032``), appended AFTER the plain tokens so it can
    never displace one. This is not a numeric-token policy change: ``keep_numeric``
    still governs bare all-digit tokens exactly as before, and the alias is a
    COMPOSITE (letters + digits), which is the whole point — it is the term the
    write path also stores, so query and index agree. Off via
    ``TORTOISE_IDENTIFIER_TOKENS=0`` (:func:`identifier_tokens_enabled`).

    ``space_separated_identifiers=False`` asks for the **WRITE side** of that
    asymmetry (:func:`identifier_aliases`' ``space_separated=False``) while
    still emitting the plain tokens: pass it when the text being tokenised is
    INDEXED TEXT, not a query (#10203 review round 10, F1). The default is the
    read side because most callers tokenise a query, and the read side is the
    superset — so this parameter only ever TIGHTENS. Why it has to exist at
    all: ``tortoise.bm25`` tokensises the CORPUS with this function, and a
    read-side default there would mint ``sprint2024`` from the prose
    "sprint 2024 planning" — the exact token the write side deliberately
    refuses (``projection/entities.py``, ``point_identifier_aliases``) — making
    a prose document indistinguishable from an ``sprint-2024`` one and shifting
    every score in the corpus through an inflated ``avgdl``/``df``.
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
    # #8933: identifier aliases ride AFTER the plain tokens (never displacing
    # one), and only when the mechanism is on. ``keep_identifiers=False`` is
    # what ``build_or_query`` asks for when it fills the CAPPED original slots:
    # an alias must never consume one of those, or a long alias could push a
    # shorter original token out of the OR cap (the A4 regression guard).
    if keep_identifiers and identifier_tokens_enabled():
        seen = set(out)
        for alias in identifier_aliases(
                query, space_separated=space_separated_identifiers):
            if alias not in seen:
                seen.add(alias)
                out.append(alias)
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

    ⛔ The chunks are INDEXED TEXT, so they take the **WRITE side** of the
    #8933 identifier asymmetry (``space_separated_identifiers=False``) — the
    same half ``tortoise.bm25`` passes for its corpus, the SDK's
    ``_warn_point_name_not_lexically_indexed`` for ``indexed_tokens``, and
    ``projection.entities.point_identifier_aliases`` for the stored alias.
    The default (read side) is the permissive half that mints a composite from
    a bare ``<word> <2+ digits>`` pair, which is a QUERY shape the #8933 ruling
    widened for what a person types — never for text that already sits in the
    index. Minting it here changed the SECOND FTS pass's bytes for questions
    with no identifier shape at all: the base-graph pool's ``couch ikea 800
    dollars`` harvested ``ikea800`` and displaced a golden row, breaking the
    flag-OFF byte-identity golden (``tests/test_assembly_sdk.py``, CI shard
    ``test (c)``). Restoring the write side keeps this call site silent on prose
    while an ``ERR-4032``-shaped chunk still contributes its write-side alias.
    """
    if not aliases:
        return []
    chunks = ([aliases] if isinstance(aliases, str)
              else [str(a) for a in aliases if a])
    reserved = reserved or set()
    seen: set[str] = set()
    out: list[str] = []
    for chunk in chunks:
        for tok in tokenize_sparse_query(
                chunk, keep_numeric=True, space_separated_identifiers=False):
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

    #8933 (owner ruling, 2026-10-26): when the identifier mechanism is on, two
    kinds of term ride the TAIL, after the original tokens and after the
    expansion tail — the normalised ALIAS terms (``err4032``, from
    :func:`tokenize_sparse_query`) and the ADJACENCY PHRASES
    (``"err 4032"``, from :func:`identifier_phrase_terms`). Both are ADDITIVE
    OR alternatives: they can only raise a record's score, never remove a
    term, so a query that matched before still matches. The phrase is what
    makes the fix work on records written BEFORE the write-side alias existed.
    Each tail is capped at ``MAX_ALIASES_PER_POINT``, so the OR budget stays a
    BOUND (originals ≤ ``max_terms`` + expansion ≤ 8 + 8 aliases + 8 phrases)
    rather than growing with the query's input.
    """
    # #8933: the identifier terms are computed SEPARATELY from the capped
    # original set, so they can never consume one of its slots — the A4
    # reserved-slot guard survives unchanged (a long alias cannot displace a
    # shorter ORIGINAL query token).
    identifiers_on = identifier_tokens_enabled()
    aliases = identifier_aliases(query) if identifiers_on else []
    phrases = identifier_phrase_terms(query) if identifiers_on else []
    tokens = tokenize_sparse_query(
        query, keep_numeric=keep_numeric, keep_identifiers=False)
    # The degenerate test counts the identifier terms too: without them,
    # an identifier-ONLY query (`ERR-4032` -> one surviving token `err`) would
    # take the escaped-RAW branch and match NOTHING (measured on #8933: the
    # index stores `err` and `4032` separately, so the composite literal is not
    # a term the index holds).
    #
    # #10203 review P3-1: the predicate must be "no identifier term survived",
    # not "the total term count is <= 1". `identifier_phrase_terms` DROPS a span
    # carrying a 1-char segment (:300), so for `A-4032` the alias is the ONLY
    # term that survives — `len(tokens) + len(aliases) + len(phrases) == 0 + 1 +
    # 0 == 1` took the raw branch and returned the literal `A\-4032`, discarding
    # `a4032` and matching nothing. Counting the plain tokens separately keeps
    # the pre-#8933 degenerate behaviour exactly (`not aliases and not phrases`
    # makes it `len(tokens) <= 1`) while never taking the raw branch while an
    # identifier term exists.
    if max_terms < 1 or (len(tokens) <= 1 and not aliases and not phrases):
        return escape_redisearch_literal(query)
    tokens.sort(key=len, reverse=True)  # stable — ties keep source order
    original = tokens[:max_terms]
    terms = list(original)
    if expansion_terms:
        extra = expansion_tokens(
            expansion_terms, reserved=set(original))
        if extra:
            terms = original + extra
    # #8933 Stage 1 + Stage 2 terms ride LAST — additive OR alternatives only.
    # An alias can only add a match (the write path stores the same token); a
    # PHRASE adds an OR alternative that only a document holding the segments
    # ADJACENT can match, so it raises the exact record's score on records
    # already indexed — a query that matched before still matches.
    present = set(terms)
    for term in aliases + phrases:
        if term not in present:
            present.add(term)
            terms.append(term)
    return "|".join(terms)


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
