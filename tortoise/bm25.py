"""#8488 — Okapi BM25: the lexical scorer the read path never had.

Why this module exists (MEASURED, not assumed). The live lexical leg
(``search_engine.run_fts_query`` → RediSearch ``db.idx.fulltext.queryNodes``)
is documented as *"the BM25-style rank behavior the epic's scope prescribes"*
(``tortoise/sparse.py``), and the embedded fallback is documented as TF-IDF.
On the embedded lane (falkordblite, measured 2026-10-18 on this tree,
``a89120529``) it behaves like a per-matched-term count whose weight is NOT
driven by rarity and whose score is NOT driven by document length. Two
properties, two measurements, each reproducible with ``run_fts_query`` alone::

    # corpus: 8 x "release notes for sprint N", "zebra sighting report",
    #         "release zebra note"        query: 'release zebra'
    both 4.0 | rel0..rel7 2.0 | rare 2.0
    #  ^ the document holding ONLY the rare term earns exactly what each of
    #    the 8 documents holding ONLY the ubiquitous term earns, and loses
    #    the id tie-break — it ranks LAST of ten.

    # corpus: "release blocker identified" vs "release "*30 + "notes"
    #         query: 'release'
    SHORT(1x release) 2.0 | LONG(30x release) 2.0
    #  ^ 30 occurrences and one occurrence score identically: length and
    #    term frequency are invisible to the ranking.

The absolute values are not a clean per-term constant (a two-term match scored
3.0 on a 4-document corpus and 4.0 on a 10-document one), so this module does
NOT claim the shipped scorer is a bare term count. What it claims, and what
the two measurements above show, is that the two terms below are ABSENT — and
those are exactly the two terms this module supplies.

Okapi BM25 is the estimator that supplies exactly the two missing terms:

    idf(t)      = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))      [rarity]
    score(d, q) = Σ_t idf(t) · f(t,d)·(k1+1)
                           / (f(t,d) + k1·(1 - b + b·|d|/avgdl))
                  [term-frequency saturation ↑] [length normalisation ↑]

so a document carrying a RARE exact token outranks documents carrying a common
one (idf), and length is priced in (``b``). Note what BM25 does NOT do: with
the standard ``k1=1.2`` saturation a 30x repetition still outranks a single
occurrence — it is bounded (``k1+1``), not reversed. The shipped scorer's
failure is that it prices neither rarity nor length at all, not that it
over-prices repetition.

What this module DELIBERATELY does not decide
---------------------------------------------
* **The tokenizer.** ``tortoise/sparse.py`` is the recorded "ONE tokenizer both
  stacks import"; a second one here would let the two lexical stacks disagree
  about what a token IS. Every entry point takes the tokens from
  :func:`tortoise.sparse.tokenize_sparse_query`.
* **The numeric-token policy.** That policy (``keep_numeric``, default False)
  was the search lane's byte-identity control, and the owner REOPENED it
  (#8933 ruling, 2026-10-26) — for IDENTIFIER-shaped input only. So the
  identifier half is no longer pending, and it is not this module's: the
  shared tokenizer preserves an identifier by default (``ERR-4032`` ->
  ``err4032`` plus the adjacency phrase ``"err 4032"``), switched by
  ``TORTOISE_IDENTIFIER_TOKENS`` in ``tortoise/sparse.py`` — **not** here, and
  not by ``keep_numeric``. ``keep_numeric`` still governs ONLY bare all-digit
  tokens: ``keep_numeric=True`` makes a bare ``4032`` participate, ``False``
  (the shipped default) drops it. This module only THREADS the caller's
  choice; nothing here changes either default. The identifier control's own
  coverage is ``tests/test_identifier_tokens_8933.py``.
* **Which half of the identifier asymmetry a TEXT is.** ``tortoise/sparse.py``
  ships one deliberate asymmetry — the write side is the PRECISE half
  (``ERR-4032`` mints ``err4032``; the prose "sprint 2024" mints nothing) and
  the read side also accepts the space-typed shape a person types. This module
  tokenises BOTH a query and a CORPUS, so it must ask for the right half:
  :func:`bm25_scores` tokenises the query on the read side and each DOCUMENT
  on the WRITE side (:func:`bm25_tokens`' ``space_separated_identifiers``).
  Getting that wrong is not cosmetic — a read-side corpus mints
  ``sprint2024`` from the prose "sprint 2024 planning", which is exactly the
  token the write path refuses to store, so a prose document would score as
  if it held the code and the two lexical stacks would disagree about what a
  document says (#10203 review round 10, F1).
* **Whether the arm is on.** :func:`bm25_enabled` reads
  ``TORTOISE_BM25_LEXICAL``, which since #8488's owner ruling is an OPT-OUT
  (``BM25_DEFAULT_ENABLED = True``) rather than a fail-safe-OFF switch — the
  fail-safe-OFF posture belongs to while the arm was UNMEASURED, and the
  reasoning for moving it, plus the #10121 tie fix it depends on, is recorded
  on that constant.

The fusion BOUND (why :func:`resolve_lexical_weight` exists)
------------------------------------------------------------
``#1657`` measured that equal-weight RRF dilutes a strong leg, and its recorded
production default is ``{"vector": 1.5}`` — the bge-small dense leg leads.
``#7704`` records the sibling defect: an UNBOUNDED boost does not survive
fusion at all — the rank difference a fused list carries outweighs it, so the
outcome falls to an arbitrary id tie-break. Both lessons point the same way:
a leg's fusion weight has to be bounded BELOW the strongest leg's, or it
decides the order by itself. :data:`LEXICAL_WEIGHT_MAX` is that bound (the
lexical leg may never outweigh the dense leg), and
:func:`resolve_lexical_weight` is the ONE place it is applied — including to a
``TORTOISE_FUSION_WEIGHTS`` env override, which is ``json.loads``-ed and could
otherwise carry any number:

    # measured, on the boundary fixture in tests/test_bm25_lexical_8488.py
    rrf_fusion([dense, lexical], strategy_names=["dense", "lexical"],
               weights={"dense": 1.5, "lexical": 1.0})
      -> the dense leg's best hit first (its 1.5/61 outweighs a lexical
         also-ran's full-weight 1.0/61 plus its own weak dense contribution)
    weights={"dense": 1.5, "lexical": 50.0}      # the clamp dropped
      -> the lexical also-ran first; the read's actual answer falls below it
"""
from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence

from .env_truthy import env_flag

logger = logging.getLogger(__name__)

#: Okapi BM25 term-frequency saturation. 1.2 is the value the estimator's own
#: literature reports as the default (and what ``rank_bm25`` ships); it is a
#: parameter, not a tuned constant — nothing here has measured a better one for
#: this corpus, so the standard default is used rather than invented.
BM25_K1 = 1.2

#: Okapi BM25 length-normalisation strength (0 = none, 1 = full). 0.75 is the
#: standard default. This is the term whose ABSENCE the measurement above
#: exposes: without it a 31-token document and a 3-token document carrying the
#: same single occurrence score identically.
BM25_B = 0.75

#: The gate. Fail-safe OFF: the arm is only swapped in when an operator asks
#: for it AND (#8486) has a baseline to judge it against.
BM25_ENV = "TORTOISE_BM25_LEXICAL"

#: Fusion weight used for the lexical leg when the caller names none. It is
#: 1.0 because that is the weight ``rrf_fusion`` ALREADY gives a leg an
#: explicit weight map does not mention — so enabling the arm does not move the
#: recorded production default (``{"vector": 1.5}`` + implicit 1.0 for fts),
#: it only bounds a hostile override.
DEFAULT_LEXICAL_WEIGHT = 1.0

#: ⛔ THE BOUND (#7704 / #1657). The lexical leg may never carry more fusion
#: weight than the strongest leg it is fused with. Dropping this clamp is the
#: mutation that reds TWO tests —
#: ``TestLexicalWeightBound.test_weight_is_clamped_below_the_dense_leg`` (the
#: unit) and ``TestReadPath.test_lexical_weight_is_bounded_at_the_read_path``
#: (the read path) — both in ``tests/test_bm25_lexical_8488.py``, which also
#: pins that the pointer names tests that exist
#: (``TestMutationPointers.test_the_bound_points_at_real_tests``): with an
#: unbounded weight the lexical leg alone decides the order and the dense
#: leg's best hit — the read's actual answer — falls below a lexical also-ran.
#: A DISABLED dense leg (weight <= 0) is not the strongest leg, so it does not
#: lower this bound — see :func:`resolve_lexical_weight`.
LEXICAL_WEIGHT_MAX = 1.0


#: #8488 (owner ruling 2026-10-28): the arm is now ON by default and the
#: variable is an OPT-OUT. Fail-safe-OFF was right while the arm was unmeasured;
#: the owner moved it to default-ON on mechanism-level evidence (this module's
#: own tests) with the benchmark (#8486) still pending. ``=0`` / ``false`` /
#: ``no`` / ``off`` disables it, and an unset, blank or GARBAGE value keeps the
#: DEFAULT — a typo must never silently change which scorer a read uses.
#:
#: ⛔ ORDERING, now SATISFIED: enabling the arm was only correct ON TOP OF
#: the arm's own tie fix — ``bm25_rank`` used to tie ``(-score, id)`` ASCENDING
#: (oldest-first), which truncated the NEWEST match at ``limit``. #10121 / PR
#: #10150 carries that fix and it is PRESENT in this tree (``bm25_rank`` sorts
#: through ``search_engine.recency_tie_key``, newest-first, id last), so the
#: default may be ON. Do not re-tie ``bm25_rank`` oldest-first without also
#: reverting this default.
BM25_DEFAULT_ENABLED = True


def bm25_enabled() -> bool:
    """Is the in-process BM25 lexical arm enabled for this process?

    Default ON, opt-out via ``TORTOISE_BM25_LEXICAL=0``, resolved through
    :func:`tortoise.env_truthy.env_flag` — whose contract is exactly this case:
    unset/blank/garbage -> the DEFAULT (a typo never flips a knob), explicit
    truthy -> True, explicit falsy -> False.
    """
    return env_flag(BM25_ENV, BM25_DEFAULT_ENABLED)


def bm25_tokens(
    text: str, *, keep_numeric: bool = False,
    space_separated_identifiers: bool = True,
) -> list[str]:
    """Tokenise through the ONE shared tokenizer (``tortoise.sparse``).

    Imported lazily: ``sparse`` is dependency-free, but importing it at module
    scope would put this module in the import graph of anything that only wants
    :func:`resolve_lexical_weight`, and the fusion helper is deliberately
    usable without the scorer.

    ``space_separated_identifiers`` selects WHICH HALF of the #8933 identifier
    asymmetry applies, and it is about the KIND of text, not a tuning knob:

    * ``True`` (default) — a QUERY. The read side also accepts the space-typed
      shape a person types (``ERR 4032`` -> ``err4032``), and a spurious alias
      costs one extra OR term in one query.
    * ``False`` — INDEXED TEXT (a document). Only an explicit ``-``/``_``
      separator mints an alias, matching the write side exactly, because a
      document saying "sprint 2024" must never behave as though it stored
      ``sprint2024``. :func:`bm25_scores` passes this for every document.
    """
    from .sparse import tokenize_sparse_query

    return tokenize_sparse_query(
        text, keep_numeric=keep_numeric,
        space_separated_identifiers=space_separated_identifiers,
    )


def bm25_scores(
    query: str,
    docs: Sequence[tuple[str, str]],
    *,
    k1: float = BM25_K1,
    b: float = BM25_B,
    keep_numeric: bool = False,
) -> dict[str, float]:
    """Okapi BM25 score of every document for ``query``.

    Args:
        query: the raw query text.
        docs: ``[(doc_id, text), ...]`` — the corpus being scored. Duplicate
            ids are not merged; the LAST occurrence wins (callers pass graph
            rows, which are id-unique by construction).
        k1, b: the estimator's parameters (see the module docstring).
        keep_numeric: the caller's numeric-token policy, threaded verbatim to
            the shared tokenizer. Default False = the shipped search-lane
            policy; this function never decides it (#8933). It governs BARE
            NUMERIC tokens only — the identifier control is the tokenizer's
            (``TORTOISE_IDENTIFIER_TOKENS``) and rides both policies.

    The two halves are tokenised with the ASYMMETRY the write path defines: the
    QUERY takes the read side (a person's ``ERR 4032`` counts) and each
    DOCUMENT takes the WRITE side (only ``ERR-4032`` does). That is not a
    refinement — the document side is INDEXED TEXT, and a read-side corpus
    would mint ``sprint2024`` from the prose "sprint 2024 planning", which the
    write path deliberately does not store: a prose document would then score
    as if it held the code, the two lexical stacks would disagree about what a
    document says, and the bogus token would shift ``avgdl``/``df`` for every
    score in the corpus (#10203 review round 10, F1).

    Returns:
        ``{doc_id: score}`` for every document that shares at least one token
        with the query. Documents with no query token are ABSENT (not 0.0):
        "unscorable" and "scored zero" must stay distinguishable, because the
        fusion helper treats an absent id as "this leg has no opinion".

    Degenerate inputs are not errors: an empty query, a query of stopwords
    only, or an empty corpus all return ``{}`` — the single-token/stopword
    policy is the tokenizer's, and nothing here second-guesses it.
    """
    if not query or not docs:
        return {}
    # The QUERY takes the read side of the identifier asymmetry (:func:`bm25_tokens`'
    # default): the space-typed ``ERR 4032`` a person actually types counts.
    q_tokens = bm25_tokens(query, keep_numeric=keep_numeric)
    if not q_tokens:
        return {}
    #: Distinct query terms — a repeated term in the query is one scoring term
    #: (BM25 is defined over the term SET; summing per occurrence would let a
    #: caller inflate one term by repeating it, which is the query-side
    #: analogue of the unbounded-boost defect the module docstring cites).
    q_terms = list(dict.fromkeys(q_tokens))

    tokenised: list[tuple[str, list[str]]] = []
    doc_len_sum = 0
    for doc_id, text in docs:
        toks = bm25_tokens(
            text or "", keep_numeric=keep_numeric,
            space_separated_identifiers=False,
        )
        tokenised.append((doc_id, toks))
        doc_len_sum += len(toks)
    n_docs = len(tokenised)
    if n_docs == 0:
        return {}
    avgdl = (doc_len_sum / n_docs) or 1.0

    #: document frequency per query term, over the corpus as given.
    df: dict[str, int] = {t: 0 for t in q_terms}
    for _doc_id, toks in tokenised:
        present = set(toks)
        for term in q_terms:
            if term in present:
                df[term] += 1

    #: Lucene's non-negative idf variant (the ``1 +`` inside the log): a term
    #: present in EVERY document keeps a small positive weight instead of
    #: going negative, which is what keeps a common-token-only match from
    #: scoring BELOW an unscored document.
    idf = {
        t: math.log(1.0 + (n_docs - df[t] + 0.5) / (df[t] + 0.5))
        for t in q_terms
        if df[t] > 0
    }
    if not idf:
        return {}

    out: dict[str, float] = {}
    for doc_id, toks in tokenised:
        if not toks:
            continue
        counts: dict[str, int] = {}
        for tok in toks:
            if tok in idf:
                counts[tok] = counts.get(tok, 0) + 1
        if not counts:
            continue
        norm = k1 * (1.0 - b + b * (len(toks) / avgdl))
        score = 0.0
        for term, f in counts.items():
            score += idf[term] * (f * (k1 + 1.0)) / (f + norm)
        if score > 0.0:
            out[doc_id] = score
    return out


def bm25_rank(
    query: str,
    docs: Sequence[tuple[str, str]],
    *,
    limit: int | None = None,
    k1: float = BM25_K1,
    b: float = BM25_B,
    keep_numeric: bool = False,
    recency: Mapping[str, str] | None = None,
) -> list[tuple[str, float]]:
    """``[(doc_id, score), ...]`` ranked descending — the shape a leg returns.

    The sort key is ``(score, recency, doc_id)`` — all descending — not
    ``-score``: a score tie must not fall through to insertion order (#3019 —
    RRF is rank-based and an unstable tie makes the fused order a function of
    dict order), and it must not fall through to OLDEST-first either (#10121).

    ``recency`` is supplied by the caller because the arm cannot derive it from
    text: ``run_bm25_query`` projects the shared
    ``search_engine.recency_key_expr`` (the CASE — not a bare ``coalesce``,
    which would let an empty ``updatedAt`` suppress a valid ``createdAt``)
    alongside each document and hands it over. A doc missing from the map sorts
    as ``''`` — OLDEST — which is exactly how
    :func:`tortoise.search_engine.recency_tie_clause` treats an undated row.

    Why the direction matters (measured, #10121): an arm that ranks a tie
    oldest-first returns the newest match LAST, and with a ``limit`` it drops
    it: on a 21-row tie the just-created point was absent from
    ``run_bm25_query(limit=5)``. Sorting the corpus window newest-first and then
    ranking it oldest-first is how that survived the first pass at this fix.
    """
    from .search_engine import recency_tie_key

    scores = bm25_scores(query, docs, k1=k1, b=b, keep_numeric=keep_numeric)
    timestamp = recency or {}
    # The secondary keys come from the ONE Python-side twin of the cypher
    # clause (#10121 review): a hand-written tuple here was the third
    # implementation of the same rule.
    ranked = sorted(
        scores.items(),
        key=lambda kv: (kv[1], *recency_tie_key(timestamp.get(kv[0]), kv[0])),
        reverse=True,
    )
    if limit is not None:
        ranked = ranked[:max(0, limit)]
    return ranked


def resolve_lexical_weight(
    weight: object,
    dense_weight: object = 1.0,
) -> float:
    """Clamp a lexical fusion weight into ``[0, min(LEXICAL_WEIGHT_MAX, dense)]``.

    This is the bound the module docstring justifies (#7704 / #1657). It is
    applied to EVERY source of the number, including
    ``TORTOISE_FUSION_WEIGHTS`` (``json.loads`` accepts any float) and a kwarg
    caller, so there is one place a reviewer has to check.

    A NON-POSITIVE dense weight means the bound does NOT apply, not that the
    lexical weight is zero. ``TORTOISE_FUSION_WEIGHTS='{"vector": 0}'`` is the
    natural spelling of "the dense leg does not contribute", and it makes the
    lexical leg the strongest leg it is fused with — so the faithful cap is
    :data:`LEXICAL_WEIGHT_MAX`. Returning ``0.0`` there would zero BOTH legs
    (``rrf_fusion`` scores every candidate ``weight * 1/(k+rank)``), which
    silently collapses the whole fused read to a constant: every hit ties at
    0.0, the order degrades to id order, and a ``threshold > 0`` caller gets no
    rows at all (#8488 review P2). Pre-clamp, the same knob left the lexical
    leg at ``rrf_fusion``'s implicit 1.0 and produced a correct lexical-only
    ranking — so a zero cap would also be a REGRESSION, not a tightening.

    Degenerate inputs degrade to :data:`DEFAULT_LEXICAL_WEIGHT` rather than
    raising: a fusion weight is a tuning knob, and a read must never fail
    because a knob was misspelled. NaN/±inf are the #3019 class — they destroy
    the ORDER, not just the score — so they are replaced (and warned about)
    exactly like ``rrf_fusion``'s own guard does.
    """
    dense = _finite_or(dense_weight, 1.0)
    cap = LEXICAL_WEIGHT_MAX if dense <= 0.0 else min(LEXICAL_WEIGHT_MAX, dense)
    w = _finite_or(weight, DEFAULT_LEXICAL_WEIGHT)
    if w < 0.0:
        return 0.0
    return min(w, cap)


def _finite_or(value: object, fallback: float) -> float:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(f):
        logger.warning("non-finite fusion weight %r — using %s", value, fallback)
        return fallback
    return f
