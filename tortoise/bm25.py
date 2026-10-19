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
  is a RECORDED control — the search lane stays byte-identical — and its
  reopen is the owner's call (#8933). This module only THREADS the caller's
  choice: ``keep_numeric=True`` makes ``ERR-4032`` contribute its ``4032``
  segment, ``False`` (the shipped default) keeps today's behaviour. Nothing
  here changes the default.
* **Whether the arm is on.** :func:`bm25_enabled` reads
  ``TORTOISE_BM25_LEXICAL`` and the read path only swaps the lexical scorer
  when it is explicitly enabled (fail-safe OFF, the same posture as
  ``TORTOISE_ASK_RERANK``). The swap is a retrieval-behaviour change and #8486
  has not yet produced a baseline to measure it against.

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
import os
from collections.abc import Sequence

from .env_truthy import is_truthy

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


def bm25_enabled() -> bool:
    """Is the in-process BM25 lexical arm enabled for this process?

    Fail-safe OFF (unset/blank/garbage → False) via the recorded truthy
    contract in :mod:`tortoise.env_truthy` — the same spellings every other
    lever in this tree accepts.
    """
    return is_truthy(os.environ.get(BM25_ENV))


def bm25_tokens(text: str, *, keep_numeric: bool = False) -> list[str]:
    """Tokenise through the ONE shared tokenizer (``tortoise.sparse``).

    Imported lazily: ``sparse`` is dependency-free, but importing it at module
    scope would put this module in the import graph of anything that only wants
    :func:`resolve_lexical_weight`, and the fusion helper is deliberately
    usable without the scorer.
    """
    from .sparse import tokenize_sparse_query

    return tokenize_sparse_query(text, keep_numeric=keep_numeric)


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
            policy; this function never decides it (#8933).

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
        toks = bm25_tokens(text or "", keep_numeric=keep_numeric)
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
) -> list[tuple[str, float]]:
    """``[(doc_id, score), ...]`` ranked descending — the shape a leg returns.

    The sort key is ``(-score, doc_id)``, not ``-score``: a score tie must not
    fall through to insertion order (#3019 — every other leg in
    ``search_engine`` makes the same pair, because RRF is rank-based and an
    unstable tie makes the fused order a function of dict order).
    """
    scores = bm25_scores(query, docs, k1=k1, b=b, keep_numeric=keep_numeric)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
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
