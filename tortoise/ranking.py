"""Graph-informed ranking for Tortoise search results (#25).

Search results are ranked by text similarity alone (RRF fusion of FTS /
vector / structural). This module adds Tortoise's structural differentiator —
graph topology — as a composable ranking signal:

    final_score = α · normalize(similarity) + β · graph_boost + γ · recency_decay

* graph_boost for Points uses the persisted EP confidence (``n.confidence``,
  written by ``compute_confidence``) and operator connectivity (incident
  IMPL/NAND edge count).
* graph_boost for Events/Sessions uses aboutObject edge count (Objects
  referenced = reusable knowledge) plus the mean EP confidence of Points the
  session produced, per docs/scoping-7769-graph-informed-ranking.md.
* recency_decay is exponential with a configurable half-life (default 30 days).

Design notes (from the scoping doc):
- GraphRanker is a standalone module, unit-testable without a live graph
  (``projection=None`` → only signals embedded in the result dicts are used).
- Weights are configurable at construction; defaults α=0.5, β=0.35, γ=0.15.
- Missing signals degrade to neutral, never to a penalty — uncalibrated
  Points get a graph boost of 0.0 and missing timestamps get neutral recency.
- aboutObject / PRODUCES edges may be absent on older graphs; the queries
  are OPTIONAL MATCH so the boost degrades to 0.4·confidence gracefully.
"""
from __future__ import annotations  # noqa: I001

import logging
import math
import re
from datetime import datetime, timezone

from .search_engine import (  # noqa: E402, RUF100
    _beta_variance,
    _exclude_status_clause,
    CONTESTED_VARIANCE_THRESHOLD,
    ep_measured_cypher,  # #3276: has_ep == EP measured (baseline prior != measured)
)
from .live import (  # #2490: terminal rows are never contested; #2598: the draft predicate
    epistemic_disjunction,  # #7927: traversal-position widening
    epistemic_identity,  # #7927: the two-key identity, declared ONCE in live.py
    epistemic_labels_of,  # #7927: present-label resolution (resolved ONCE per rerank)
    epistemic_sweep,  # #7927: id-anchored present-label sweep
    is_draft_status,
    is_terminal_status,
)


logger = logging.getLogger(__name__)


#: #2598 — the ``confidence_source`` value for a DRAFT whose belief EP has not
#: measured. A draft is EP-INERT (``live._live_only`` excludes it from EVERY
#: factor-extraction call site), so no posterior is computed for it and there is
#: no measurement to report. ``"neutral"`` (the #898 fallback for an uncalibrated
#: LIVE claim) is the wrong word here: it asserts a measurement verdict about a
#: row that is not in the belief network at all, and it let a draft's fabricated
#: Beta(1,1) mean of 0.5 read as graded belief — and OUTRANK a genuinely measured
#: live claim whose posterior is below 0.5 (#2598's repro).
UNCOMPUTED_CONFIDENCE_SOURCE = "uncomputed"


def _w4_flag_on() -> bool:
    """Resolve the W4 enrichment flag for the ranking boost (same env seam
    as why.py's w4_enrichment_enabled — imported lazily to avoid a hard
    ranking→why dependency at import time)."""
    try:
        from .why import w4_enrichment_enabled

        return w4_enrichment_enabled()
    except Exception:  # pragma: no cover — defensive
        return False


# Default signal weights — must sum to 1.0.
DEFAULT_SIMILARITY_WEIGHT = 0.5
DEFAULT_GRAPH_BOOST_WEIGHT = 0.35
DEFAULT_RECENCY_WEIGHT = 0.15
DEFAULT_HALF_LIFE_DAYS = 30.0

# W4-b (issue #2102): the bounded contested-relevance boost, in graph-boost
# space (weighted by graph_boost_weight in the final score).  Small by
# design — it tips relevance-matched contested claims ABOVE their
# uncontested twins (E2E-1) while the twins' FUSED relevance difference stays
# BELOW the boost's final-score contribution
# (W4_CONTESTED_BOOST × graph_boost_weight = 0.0175); it does not reorder the
# tail.  Strictly > 0 so a boost never lowers a score.
#
# THAT CONDITION IS NOT GUARANTEED ON A FUSED READ (#7704).  Content-identical
# twins tie on EVERY leg, and each leg's own ``ORDER BY score DESC,
# node.<id> ASC`` tie-break (#3019) hands the lower-ULID twin rank 0.  RRF is
# rank-based (``1/(k + rank + 1)``), so the post-fusion min-max normalization
# converts that ONE-rank difference into a relevance gap that grows as the
# pool shrinks — measured on the shipped hybrid path, as the RELEVANCE gap the
# boost must clear (so the comparison with the 0.0175 boost effect is
# like-for-like): 0.0189 of final score on the 4-row E2E-1 fixture (just above
# the 0.0175 boost, so the contested twin ranks BELOW its twin), 0.5 on a
# 3-row pool (there the twins occupy both ends of the normalization span; the
# final-score residual once the shipped boost is applied is 0.5 - 0.0175 =
# 0.4825, which is the delta a reader sees in the payload).  The guarantee
# therefore holds
# on a SINGLE-LEG read (where both twins score identically and the boost is
# the sole differentiator), and on a fused read only while the fused relevance
# delta stays under the boost effect.  Sizing the boost, changing the fusion
# tie semantics, or restating the promise is a product decision — tracked on
# #7704, not decided here.
#
# AND THE RANKER'S POOL IS THE PAGE, NOT THE CORPUS (#7704, re-measured
# 2026-10-15).  ``tortoise_fts_query`` truncates the fused candidate list to
# ``limit`` (``result_ids[:limit]``, before ``rerank`` normalizes it), so the
# fused relevance delta the boost must clear is a function of the caller's
# ``limit`` — the promise's truth is a property of the READ SHAPE.  Same
# graph, same query, 404 points, dense leg live: the contested twin ranks
# BELOW its twin at limit=4 (delta 0.0157) and at the shipped default limit=10
# (delta 0.0013), and ABOVE it at limit>=40 (delta -0.0041), the gap shrinking
# as the page grows.  On the fixture with 40 filler rows (a 44-point graph) it
# ranks below at every limit 2..40 (+0.0056 at limit=40).  The exact page delta is
# a property of the page's own similarity span, so filler composition moves it (a
# 404-point variant with different filler rows measures a SMALLER requirement at
# limit=10, small enough that the contested twin is already first there); the
# MECHANISM is what is stable, not any one fixture's number.
#
# WHAT A LARGER BOOST WOULD AND WOULD NOT BUY (#7704, corrected 2026-10-18).
# The required increment is BOUNDED, not unbounded: ``_min_max_normalize`` maps
# a page into [0, 1], so every term of the level condition between the twins
#
#   cb_req = (similarity_weight * d_sim + graph_boost_weight * d_gb
#             + recency_weight * d_rec) / graph_boost_weight    (d = calm - hot)
#
# is bounded by its own weight, and ``cb_req`` SATURATES at
# ``(similarity_weight + graph_boost_weight * |d_gb| + recency_weight)
# / graph_boost_weight`` — 2.857 in the worst case, where the twins' baseline
# ``graph_boost`` differs maximally (each row's is in [0, 1]).  The 1.857 the
# page-bound band pins is that saturation at ``d_gb == 0`` — the ceiling is
# monotone in ``|d_gb|``, so a NEGATIVE ``d_gb`` gives a LARGER one, not that
# one — and 1.4286 is the equal-recency case with ``d_gb == 0``: the case the
# measured pages here exhibit, because the E2E-1 twins share a baseline
# ``graph_boost`` (identical operator connectivity).  That premise is what
# makes the flat 1.4286 figures below legitimate, so the test pins it by name
# rather than assuming it.
# A wider CONFIDENCE gap moves ``d_gb``, and the requirement moves WITH it.  On
# the 2-row page (``d_sim`` = 1, equal recency) the formula above is
# ``cb_req = 1.4286 + d_gb``, so ``d_gb`` >= 0 puts the requirement at or above
# 1.4286 — NOT 1.857, which it only passes once ``d_gb`` >= 0.4286 (a 0.857
# confidence gap at equal connectivity).  Measured end-to-end: a maximal gap,
# calm 1.0 / contested 0.0, gives ``d_gb`` = 0.5 and puts the 2-row requirement
# at 1.4286 + 0.5 = 1.9286.  Nor is the sign guaranteed: the contested twin is
# the one carrying the counter-claim edge, i.e. the one with the HIGHER
# connectivity, which pushes ``d_gb`` the other way — the fixture has to mint a
# parity NAND onto the calm twin to zero it, and without that calm falls to
# degree 0, ``d_gb`` = -0.25, and the 2-row requirement drops to 1.1786.  So
# 1.857 is the CEILING at ``d_gb == 0`` and 1.4286 is the requirement at that
# parity; 1.857 is never a floor (at ``d_gb == 0`` the requirement is 1.4286,
# and it passes 1.857 only once ``d_gb`` >= 0.4286), while 1.4286 is one only
# while ``d_gb`` >= 0 — ``1.4286 + d_gb`` exceeds it for every positive
# ``d_gb`` — and only 2.857 bounds every page.  The conclusion below is
# unaffected: a larger ``d_gb`` only moves the requirement UP, never down.
# A 2-row page is the worst PAGE because a page cannot hold fewer than the two
# rows being ordered: it is a maximum, not the first term of a series.  Measured
# on the bare 4-row E2E-1 fixture (dense leg live, re-measured 2026-10-18):
# required total ``cb`` 1.4286 on a 2-row page (built from the fused rows — the
# SDK's own ``limit=2`` read does not hold both twins), still 1.4286 at
# ``limit=3`` (the twins occupy both ends of the span, so a third row buys
# nothing), and 0.0540 at ``limit>=4`` on that 4-row fixture (where every page
# size at or above 4 is its whole page) — the shipped default 10 included.  The
# requirement FALLS as the page grows and the largest value it ever takes is
# the 2-row one.
#   * A modest raise therefore DOES repair the shipped default read: on that
#     fixture the limit=10 ordering flips at ``cb`` >= 0.05415 — on the 1e-4
#     grid the shipped boost uses that is ``cb`` >= 0.0542 (0.0541 still ties).
#     The requirement itself is 0.0540153, so the flip sits TWO ``graph_boost``
#     grid steps above it, not one: ``graph_boost`` is rounded to 4 dp BEFORE it
#     is weighted (``round(min(1.0, graph_boost + cb), 4)``), so the requirement
#     lands on cell 0.7540, the next cell 0.7541 still ties once ``final_score``
#     is rounded again, and the flip starts at cell 0.7542.  Remedy (2) is a real
#     magnitude choice with a page-dependent, partial effect — not an
#     impossibility.  Measured: ``cb`` = 0.06 (> 0.0542) ranks the contested twin
#     FIRST on the shipped default page, where the shipped 0.05 leaves it
#     second.  Clearing #2102's own E2E-1 pre-assertion
#     (relevance delta <= 0.05 in similarity space) takes
#     ``cb > 0.5 * 0.05 / 0.35`` = 0.0714.
#   * What no ``cb`` inside the invariant can buy is the SMALL pages: the clamp
#     ``min(1.0, graph_boost + cb)`` grants at most ``1.0 - graph_boost`` of
#     headroom (0.3 on that fixture, where both twins' baseline graph_boost is
#     0.7) against the 1.4286 requirement.  Measured: cb=2.0 still ranks the
#     contested twin second on a 2-row page (final 0.5000 vs 0.8950, its graph
#     term pinned at the 1.0 clamp) — the CLAMP binding, not an unbounded
#     requirement.  Covering every page with one value therefore needs the
#     documented [0, 1] graph_boost invariant raised, which is a
#     retrieval-semantics decision and not a tuning value.
#
# Every listed remedy is a PRODUCT decision, which is why this is reported
# rather than enacted: (1) boost inside fusion = score-based fusion, the open
# owner ruling on #4210; (2) raise the boost = a magnitude choice that repairs
# the default page and an invariant change that repairs the small ones (above);
# (3) tie on the contested signal = reverses #3019's id tie-break, which
# ``temporal_leg.py`` consumes positionally; (4) restate the promise = the
# recorded #2102 E2E-1 indicator, also the owner's.  Pinned by
# ``tests/test_ranking_w4b.py::test_integration_boost_requirement_is_bounded_by_the_page``.
W4_CONTESTED_BOOST = 0.05
# Significant-token floor: query/counter-claim overlap needs tokens ≥ this
# many alphanumerics (1-3 char function words and subword noise never count;
# a >=4-char generic word DOES count — the floor is lexical, not
# stopword-filtered, a documented tradeoff for zero-vocabulary zero-cost
# relevance).
W4_RELEVANCE_TOKEN_MIN_LEN = 4


def _significant_tokens(text: str) -> set[str]:
    """Casefolded alphanumeric tokens ≥ W4_RELEVANCE_TOKEN_MIN_LEN."""
    return {
        tok for tok in re.findall(r"[a-z0-9]+", (text or "").casefold())
        if len(tok) >= W4_RELEVANCE_TOKEN_MIN_LEN
    }


def _query_touches_conflict(query: str, counter_claims: list[str]) -> bool:
    """Relevance signal (issue #2102 indicator 2): the query shares >= 2
    significant tokens (>= W4_RELEVANCE_TOKEN_MIN_LEN alnum) with the
    NAND-side counter-claim of the contested edge.  Purely lexical — no
    embedding call, no per-result query.  The >= 2 threshold keeps generic
    single-token overlap ("data", "study", "claim") from firing the gate;
    a counter-claim is conflict-RELEVANT only when the query and the
    contradiction share real vocabulary."""
    if not query or not counter_claims:
        return False
    query_tokens = _significant_tokens(query)
    if not query_tokens:
        return False
    for cc in counter_claims:
        overlap = query_tokens & _significant_tokens(cc)
        if len(overlap) >= 2:
            return True
    return False


def resolve_contested_relevance(
    projection,
    contested_ids: list[str],
    query: str,
) -> dict[str, float]:
    """ONE batch read (S8 N+1 guard): contested id → whether the query is
    conflict-relevant.  Returns {pid: W4_CONTESTED_BOOST} for contested
    points whose NAND-side counter-claim shares >= 2 significant tokens
    with the query.  Never per-result queries; never raises (defensive).

    The counter-claim resolution mirrors why.py's canonical conflict
    shape: a NANDer ``c`` NANDs INTO the point — either a direct
    operator-less statement or a NAND operator whose counterargument is its
    INPUT source at idx 0 (the NAND edge into the target sits at idx > 0).
    Mirroring why.py also means: terminal-status counter-claims are
    excluded (``_exclude_status_clause('c')``), each contested point's
    claims are severity-ranked (high-severity first — a NANDer whose
    persisted EP mean >= 0.6 is a strong dispute) then id-ordered, and
    budget-capped at why.py's W4_MAX_CONFLICTS — the SAME subset the
    canonical why-layer would surface (a boost never fires on a conflict
    the why-layer wouldn't show, and never misses one it would).

    #7927 KNOWN DIVERGENCE, deliberate: this read admits all four epistemic
    labels (§7813) while ``why._CONFLICTS_CYPHER`` is still ``:Point``-only, so
    a non-Point conflict CAN fire the boost without the why-layer rendering it.
    The subset agreement above therefore holds for ``:Point`` conflicts only;
    widening the why-layer is the residual recorded on #7927 (it is a separate
    read surface, not one of this issue's six).

    The whole read + row-processing is fail-open — ranking must never break
    from enrichment (non-str raw-graph content is coerced, never raised)."""
    if not contested_ids or not query:
        return {}
    try:
        try:  # single source of truth: the why-layer UI budget
            from .why import W4_MAX_CONFLICTS as _claim_budget
        except Exception:  # pragma: no cover — defensive
            _claim_budget = 2
        rows = epistemic_sweep(
            projection.g,
            "MATCH (n:{label}) WHERE n.id IN $ids "
            "AND (n.is_operator = false OR n.is_operator IS NULL) "
            "AND n.op_type IS NULL "
            # #6976: load-bearing `WITH n` — without it FalkorDB 6.0.0 drops the
            # id predicate at the re-binding MATCH below (foreign rows).
            "WITH n "
            "MATCH (c)-[r:NAND]->(n) "
            "OPTIONAL MATCH (src)-[ri:INPUT]->(c) "
            # #7927: ``src`` is a TRAVERSAL position reached from ``c`` — a
            # NAND operator's idx-0 counter-argument may itself be a
            # :Subject/:Object/:Event belief.
            f"  WHERE {epistemic_disjunction('src')} "
            "WITH n, c, r, src, ri "
            # #7927: identity is ``coalesce(id, eventId)``, NOT a bare ``id``.
            # A legacy / out-of-tree ``:Event`` may be keyed on ``eventId``
            # only, and then ``c.id <> n.id`` evaluates NULL and DROPS the row
            # (and a bare ``coalesce(src.id, c.id)`` returns NULL, so the
            # caller's ``if not pid or not claim_id: continue`` drops it again)
            # — the W4 boost would silently never fire for a keyless-Event
            # counter-claim. The same identity ``ep.get_contested_claims`` and
            # the audit wiring checks use; all three widened in this change, so
            # they must not disagree on how a node is addressed.
            # The CONTESTED-vs-NANDer comparison uses the INTERNAL node id,
            # which is unique. The coalesced identity is NOT: ``id`` is a
            # tested-colliding property (``_event_guarded_merge``'s suffix
            # branch leaves ``id`` at the candidate while ``eventId`` takes the
            # suffix, so two distinct Events both coalesce to the candidate),
            # and a value collision silently DROPS a genuine counter-claim —
            # measured: a real non-self NAND returned ``{}``.
            f"WHERE id(c) <> id(n) "
            # The SELF-REFERENCE guard: skip the comparison only when ``src``
            # has NO identity at all (an unkeyed optional match). Written with
            # ``OR`` across the two keys it is dead for every ordinary source —
            # a :Point/:Subject/:Object carries ``id`` and no ``eventId``, so
            # ``src.eventId IS NULL`` was TRUE and the row was admitted, letting
            # a self-referential NAND (``create_operator('NAND', p, [p])`` is
            # accepted) boost the contested point by its OWN text — measured:
            # ``{'self-x': 0.05}`` where the pre-widening guard returned ``{}``.
            # Self-reference guard: ``src`` is an OPTIONAL match, so it is
            # skipped only when it matched nothing. Compared on the internal
            # id for the same reason as above — the earlier two-key form was
            # both dead (``OR`` across the keys) and collision-blind.
            f"AND (id(src) IS NULL OR id(src) <> id(n)) AND ("
            "  (c.is_operator = true AND r.idx > 0 AND ri.idx = 0) OR "
            "  ((c.is_operator = false OR c.is_operator IS NULL) "
            "   AND c.op_type IS NULL)) "
            # #7927: ``c`` is the NANDer — the counter-claim is epistemic too.
            f"AND {epistemic_disjunction('c')} "
            "AND " + _exclude_status_clause("c") + " "
            f"RETURN {epistemic_identity('n')}, "
            f"  coalesce({epistemic_identity('src')}, "
            f"           {epistemic_identity('c')}), "
            # The INTERNAL id of whichever node supplied the claim, for the
            # per-node dedupe below. The coalesced identity above is a DISPLAY
            # value and is NOT unique (see ``live.EPISTEMIC_ID_KEYS``) — keying
            # a dict on it overwrote a distinct counter-claim that happened to
            # collide, so its text never reached the conflict test and the
            # boost silently did not fire (#7927 review).
            "  coalesce(id(src), id(c)), "
            # #7927: the counter-claim TEXT must come from the node that
            # actually carries it. A :Subject/:Object stores ``name``/``title``
            # (never ``content``), so the old chain fell through to
            # ``c.label`` — the NANDer's own type token — and the relevance
            # test could never match: the boost silently never fired for
            # exactly the labels this widening added.
            "  coalesce(src.content, src.name, src.title, c.label, "
            "           c.content, c.name, c.title, ''), "
            "  coalesce(src.posterior_alpha, src.ep_alpha, c.posterior_alpha, c.ep_alpha, 1.0), "
            "  coalesce(src.posterior_beta, src.ep_beta, c.posterior_beta, c.ep_beta, 1.0)",
            {"ids": contested_ids},
        )
        # Severity-then-id selection mirrors why.py's _assemble_conflicts:
        # a NANDer with persisted EP mean >= 0.6 is a high-severity dispute
        # and ranks above medium ones; ids break the tie deterministically
        # (Cypher row order never decides the budgeted subset).
        pending: dict[str, dict[int, dict]] = {}
        for pid, claim_id, claim_nid, content, a, b in rows:
            if not pid or not claim_id:
                continue
            a_f, b_f = float(a), float(b)
            s = a_f + b_f
            mean = (a_f / s) if s > 0 else 0.5
            # Keyed on the INTERNAL node id, so two distinct counter-claims that
            # coalesce to one identity do not overwrite each other.
            # COMMUTATIVE and TOTAL, not last-wins. Two rows can share one
            # ``claim_nid`` with different ``mean``/``content`` (when ``src``
            # stores no EP the alpha/beta chain falls through to the NANDer's),
            # AND with different ``claim_id`` (``claim_nid`` is then ``id(src)``
            # while ``claim_id`` falls through to the NANDer's). ``claim_id`` is
            # the sort key's second component, so a first-seen tie there decided
            # the budgeted subset — measured: 12 of 24 row orders boosted, 12
            # did not.
            #
            # The tie-break is the SMALLER ``str(claim_id)``, deliberately: the
            # larger one sorts the node AFTER unrelated claims and can push it
            # out of the budget, so a node's own alternative claim ids must not
            # be able to demote it. ``str()`` on both sides because the id is a
            # raw property value and a mixed-type comparison (``12345`` vs
            # ``"12345"``) raises inside the outer handler, which would silently
            # return ``{}`` for the whole read.
            slot = pending.setdefault(pid, {})
            candidate = {
                "content": str(content or ""),
                "mean": mean,
                "claim_id": claim_id,
                "nid": claim_nid,
            }
            previous = slot.get(claim_nid)
            if previous is None:
                slot[claim_nid] = candidate
            else:
                cand_rank = (candidate["mean"], candidate["content"])
                prev_rank = (previous["mean"], previous["content"])
                if cand_rank > prev_rank or (
                        cand_rank == prev_rank
                        and str(candidate["claim_id"])
                        < str(previous["claim_id"])):
                    slot[claim_nid] = candidate
        boosts: dict[str, float] = {}
        for pid, claims in pending.items():
            chosen = sorted(
                claims.values(),
                # ``claim_id`` is NOT unique (see live.EPISTEMIC_ID_KEYS), so
                # the internal id is the tiebreak — without it the budgeted
                # subset, and therefore the boost, was decided by Cypher row
                # order (measured: the same three-claim tie group boosted
                # forward and did not boost reversed).
                # ``str()`` on the claim id as well: a mixed-type pair (an int
                # id beside a string one) makes a raw comparison raise inside
                # the handler below, which would return ``{}`` for the whole
                # read — measured on a graph with both.
                key=lambda c: (0 if c["mean"] >= 0.6 else 1,
                               str(c["claim_id"]), c["nid"]))
            texts = [c["content"] for c in chosen[:_claim_budget]]
            if _query_touches_conflict(query, texts):
                boosts[pid] = W4_CONTESTED_BOOST
        return boosts
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("contested-relevance read failed: %s", e)
        return {}


def recency_decay(age_days: float, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> float:
    """Exponential recency decay: e^(-λ·age) with λ = ln(2) / half_life.

    age_days <= 0 → 1.0 (fresh). Missing/unknown age should be passed as 0.0
    by callers that want a neutral (no demotion) recency signal.
    """
    if age_days <= 0:
        return 1.0
    lam = math.log(2) / half_life_days
    return math.exp(-lam * age_days)


def _min_max_normalize(values: list[float], lo: float = 0.0, hi: float = 1.0) -> list[float]:
    """Min-max normalize a list into [lo, hi]. All-equal → midpoint."""
    if not values:
        return []
    vmin, vmax = min(values), max(values)
    span = vmax - vmin
    if span <= 1e-12:
        return [(lo + hi) / 2.0] * len(values)
    return [lo + (v - vmin) / span * (hi - lo) for v in values]


def _parse_iso(value) -> datetime | None:
    """Parse an ISO-8601 string/datetime to an aware UTC datetime, else None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)  # noqa: UP017
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)  # noqa: UP017
    except (ValueError, TypeError):
        return None


class GraphRanker:
    """Re-rank search results using graph topology signals.

    Composable into any ranking surface (``tortoise_fts_query`` with
    ``order_by="graph"``, ``suggest_entry_points``, future surfaces). Each
    signal is independently configurable; all weights are constructor args.

    ``projection`` may be None for pure unit testing — graph signals are then
    read from the result dicts themselves (keys ``graph_confidence``,
    ``graph_degree``, ``graph_about_objects``, ``createdAt``/``startedAt``).
    """

    def __init__(
        self,
        projection=None, *,
        similarity_weight: float = DEFAULT_SIMILARITY_WEIGHT,
        graph_boost_weight: float = DEFAULT_GRAPH_BOOST_WEIGHT,
        recency_weight: float = DEFAULT_RECENCY_WEIGHT,
        recency_half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
        use_degree: bool = True,
        now=None,
    ):
        total = similarity_weight + graph_boost_weight + recency_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"weights must sum to 1.0, got {total:.4f} "
                f"({similarity_weight} + {graph_boost_weight} + {recency_weight})"
            )
        self.projection = projection
        self.similarity_weight = similarity_weight
        self.graph_boost_weight = graph_boost_weight
        self.recency_weight = recency_weight
        self.recency_half_life_days = recency_half_life_days
        # #1348: use_degree=False isolates the CONFIDENCE contribution (ablation
        # arm — degree term neutralized so the graph_boost is confidence-only).
        self.use_degree = use_degree
        # #2952: recency decay is INTENTIONAL product behaviour (γ·e^(-λ·age),
        # 30-day half-life), so the fix is not to delete it but to make the
        # reference time it measures against EXPLICIT and injectable. A caller
        # replaying a fixed store (eval lane, reproducibility audit) passes a
        # pinned ``now`` and gets a ranking that is byte-identical across a
        # wall-clock jump; the default stays the live UTC clock (unchanged
        # behaviour for every existing caller).
        self._now = now if now is not None else (
            lambda: datetime.now(timezone.utc))  # noqa: UP017

    # ── Public API ────────────────────────────────────────────────────────

    def rerank(
        self,
        results: list[dict],
        *,
        entity_type: str = "point",
        query: str | None = None,
    ) -> list[dict]:
        """Re-rank results, each annotated with a ``graph_ranking`` breakdown.

        Each result dict needs at least ``id`` and a similarity-like field
        (``scores.rrf``, else ``similarity``, else ``confidence``). Returns the
        list sorted by final_score descending; input dicts are NOT mutated —
        annotated copies are returned.

        W4-b (issue #2102): ``query`` is OPTIONAL.  When a query is present
        AND the W4 flag is on, contested points whose conflict is
        query-relevant receive a bounded graph-boost increment
        (``w4_contested_boost``) — flag-off or no-query runs are
        byte-identical to the pre-W4-b ranking (R11 golden-ordering
        regression)."""
        if not results:
            return results

        ids = [r.get("id") for r in results if r.get("id")]
        signals = self._fetch_signals(ids, entity_type) if self.projection is not None else {}
        contested_boosts: dict[str, float] = {}
        if query and self.projection is not None and _w4_flag_on():
            contested_boosts = resolve_contested_relevance(
                self.projection,
                [pid for pid, sig in signals.items() if sig.get("contested")],
                query,
            )

        similarities = []
        for r in results:
            sim = _similarity_of(r)
            similarities.append(sim)

        norm_sims = _min_max_normalize(similarities)
        annotated: list[dict] = []
        for r, norm_sim in zip(results, norm_sims):  # noqa: B905
            sig = signals.get(r.get("id"), {})
            graph_boost = self.graph_boost(r, sig)
            cb = contested_boosts.get(r.get("id"), 0.0)
            if cb:
                # W4-b: bounded relevance-gated contested boost, folded into
                # the graph term (flag-off/irrelevant ⇒ 0 ⇒ unchanged).  The
                # clamp keeps the documented [0, 1] graph_boost invariant even
                # when a maxed structural boost meets the W4-b increment.
                graph_boost = round(min(1.0, graph_boost + cb), 4)
            recency = self.recency_boost(r, sig)
            final = (
                self.similarity_weight * norm_sim
                + self.graph_boost_weight * graph_boost
                + self.recency_weight * recency
            )
            copy = dict(r)
            copy["graph_ranking"] = {
                "similarity": round(norm_sim, 4),
                "graph_boost": round(graph_boost, 4),
                "recency_boost": round(recency, 4),
                "final_score": round(final, 4),
            }
            if cb:
                copy["graph_ranking"]["w4_contested_boost"] = round(cb, 4)
            sig = signals.get(r.get("id"), {})
            if "variance" in sig:
                copy["graph_ranking"]["variance"] = round(sig["variance"], 6)
                copy["graph_ranking"]["contested"] = sig.get("contested", False)
            annotated.append(copy)

        annotated.sort(key=lambda r: r["graph_ranking"]["final_score"], reverse=True)
        return annotated

    # ── Signal computation ────────────────────────────────────────────────

    def graph_boost(self, result: dict, signals: dict) -> float:
        """Graph-informed boost in [0, 1] for one result.

        Points: 0.5·persisted EP confidence + 0.5·operator connectivity
        (normalized incident IMPL/NAND edge count).
        Events/Sessions: 0.6·aboutObject count (Objects referenced) +
        0.4·mean EP confidence of produced Points.

        Contestation is deliberately NOT used as a blanket ranking penalty:
        a contested claim is a claim the agent should KNOW is contested, not
        one that is silently ranked lower.  W4-b (issue #2102) adds the
        narrow scored exception — a bounded boost when the CONFLICT is
        relevant to the query (see ``rerank(query=...)``), flag-gated and
        byte-identical when off.  Ranking stays about relevance and graph
        structure; epistemic honesty is surfaced always, scored only on
        conflict-relevant queries under the W4 flag.
        """
        if signals:
            confidence = signals.get("confidence", 0.0)
            degree = signals.get("degree", 0)
            about_objects = signals.get("about_objects", 0)
            if about_objects > 0 or signals.get("is_event"):
                # Events/Sessions: 0.6·aboutObject count (Objects referenced).
                inst_norm = 1.0 - 1.0 / (1.0 + about_objects)
                return round(0.6 * inst_norm + 0.4 * confidence, 4)
            if self.use_degree:
                # Points: 0.5·persisted EP confidence + 0.5·operator
                # connectivity (normalized incident IMPL/NAND edge count).
                connectivity = 1.0 - 1.0 / (1.0 + (degree or 0))
                return round(0.5 * confidence + 0.5 * connectivity, 4)
            # #1348 ablation: degree neutralized — boost is confidence-only
            # (use_degree=False, the confidence-only ablation arm).
            return round(confidence, 4)
        # No graph data — degrade to neutral 0.0 (never a penalty).
        return 0.0

    def recency_boost(self, result: dict, signals: dict) -> float:
        """Exponential recency decay from createdAt/startedAt; missing → 1.0.

        The reference time is ``self._now`` (#2952) — the live UTC clock by
        default, or the caller's pinned anchor for a reproducible replay.
        """
        ts = signals.get("created") or result.get("createdAt") or result.get("startedAt")
        dt = _parse_iso(ts)
        if dt is None:
            return 1.0  # unknown age — neutral, no demotion
        age_days = max(0.0, (self._now() - dt).total_seconds() / 86400.0)
        return round(recency_decay(age_days, self.recency_half_life_days), 4)

    # ── Graph queries ─────────────────────────────────────────────────────

    def _fetch_signals(self, ids: list[str], entity_type: str) -> dict[str, dict]:
        """Batch-fetch graph signals for the given ids. Returns {id: {…}}."""
        if not ids:
            return {}
        try:
            if entity_type == "event":
                return self._fetch_event_signals(ids)
            return self._fetch_point_signals(ids)
        except Exception as e:  # pragma: no cover — defensive
            logger.warning("GraphRanker signal fetch failed: %s", e)
            return {}

    def _fetch_point_signals(self, ids: list[str]) -> dict[str, dict]:
        # Post-#2206/#2286 parity: a point's confidence is the belief MEAN
        # α/(α+β) of the coalesced posterior→prior columns
        # (coalesce(n.posterior_alpha, n.ep_alpha, 1.0) / ... — the same
        # canonical read annotate_ep_batch / get_confidence / why /
        # StateRanker use). n.confidence (the EP-flush mirror of that mean)
        # is preferred when present — it is byte-identical for flushed
        # claims and preserves pre-#852 legacy graphs that carry only the
        # mirror — but when it is ABSENT the read must fall through to the
        # canonical coalesce mean, NOT to the neutral 0.5. #2262
        # mass-produced the exposing class: every decide part is born with a
        # persisted ep_alpha/ep_beta prior (system default → 0.75) and NO
        # n.confidence until the first EP flush — the pre-fix coalesce fell
        # to 0.5 and ranked a 0.75-prior claim as unmeasured while every
        # other surface read 0.75.
        cypher = (
            "MATCH (n:{label}) WHERE n.id IN $ids "
            # #7927: label-swept — a :Subject/:Object/:Event belief carries the
            # same persisted EP prior and degree as a Point (the OPERATOR side
            # stays :Point: every operator is a Point, `create_operator`).
            "OPTIONAL MATCH (n)-[r:IMPL|NAND]-(:Point {is_operator: true}) "
            "WITH n, count(r) AS degree "
            "RETURN n.id, coalesce(n.confidence, "
            "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0) "
            "  / (coalesce(n.posterior_alpha, n.ep_alpha, 1.0) + coalesce(n.posterior_beta, n.ep_beta, 1.0)), "
            "  0.5) AS conf, degree, n.createdAt AS created, "
            "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0) AS alpha, coalesce(n.posterior_beta, n.ep_beta, 1.0) AS beta, "
            # #2490: aligned with StateRanker/GapsRanker — has_ep rides the
            # status/outdated columns for the Python-side terminal gate.
            # #3276: has_ep == EP measured (ep_measured_cypher) — a #2199
            # baseline prior alone is prior-only, NOT measured.
            f"  {ep_measured_cypher('n')} AS has_ep, "
            "  n.status, coalesce(n.outdated, false)"
        )
        rows = epistemic_sweep(
            self.projection.g, cypher, {"ids": ids})
        out = {}
        for row in rows:
            pid = row[0]
            variance = _beta_variance(float(row[4]), float(row[5]))
            has_ep = bool(row[6])
            # #2490: terminal rows (status in the terminal vocab OR the legacy
            # outdated flag) never surface as measured/contested EP. The len
            # guard tolerates test doubles mirroring the pre-#2490 row shape
            # (the live query always returns the status/outdated columns).
            if len(row) > 8 and is_terminal_status(row[7], bool(row[8])):
                has_ep = False
            out[pid] = {
                "confidence": float(row[1]),
                "degree": int(row[2]),
                "created": row[3],
                "variance": variance,
                # Uncalibrated (no persisted α/β) is NOT contested.
                "contested": has_ep and variance > CONTESTED_VARIANCE_THRESHOLD,
                "is_event": False,
            }
        return out

    def _fetch_event_signals(self, ids: list[str]) -> dict[str, dict]:
        cypher = (
            "MATCH (e:Event) WHERE e.eventId IN $ids "
            "OPTIONAL MATCH (e)-[:aboutObject]->(o:Object) "
            "WITH e, count(o) AS about_objects "
            "OPTIONAL MATCH (e)-[:PRODUCES]->(p:Point) "
            "WITH e, about_objects, avg(p.confidence) AS avg_conf "
            "RETURN e.eventId, coalesce(avg_conf, 0.5) AS conf, "
            "       about_objects, e.startedAt AS created"
        )
        rows = self.projection.g.query(cypher, params={"ids": ids}).result_set
        out = {}
        for row in rows:
            eid = row[0]
            out[eid] = {
                "confidence": float(row[1]),
                "about_objects": int(row[2]),
                "created": row[3],
                "is_event": True,
            }
        return out


# ── StateRanker (recall UC1, #898 Wave A) ──────────────────────────────
# UC1 "state" recall ranks relevance ⊗ epistemic confidence with a
# MULTIPLICATIVE gate (option (b), confirmed in the #898 design-decision
# comment):
#
#     base   = relevance_norm^a × confidence^b
#     score  = base × (1 + w_c × centrality_norm)
#
# * relevance_norm = min-max normalized RRF fused score (within the result
#   set) — the fusion already happened upstream (search_engine.rrf_fusion).
# * confidence = the EP posterior MEAN α/(α+β) from the persisted
#   posterior_alpha/beta (falling back to ep_alpha/beta) — the same belief
#   signal the order_by="confidence" path reads (GraphRanker prefers the
#   n.confidence flush-mirror when present and falls back to the identical
#   coalesce-column mean otherwise, so StateRanker and GraphRanker can never
#   disagree on a point's belief).
#   Uncalibrated points (no persisted α/β → Beta(1,1)) fall back to a
#   documented neutral 0.5: absence of measurement is NOT evidence against.
#   NOTE: EpBreakdown.confidence_mean is deliberately NOT used — post-#2206
#   it carries the SAME belief mean (α/(α+β) over the same coalesce read,
#   rounded 4dp), so reading the coalesced columns directly is a
#   single-source-read optimization (n.confidence is only the EP-flush
#   mirror of that mean — absent until a flush — not an independent
#   source), not a semantics split. contention/evidence on the breakdown
#   remain the structural edge-ratio family — never beliefs.
# * centrality_norm = min-max normalized degree centrality (incident
#   IMPL/NAND + about* edge count, within the result set). w_c ≈ 0.10 is
#   deliberately WEAK and subordinate to confidence: with w_c=0.10 the whole
#   centrality boost moves a score by at most +10%, so a confidence gap of
#   >9% always dominates — a high-centrality low-confidence claim can never
#   outrank a higher-confidence one.
#
# Contestation stays an ORTHOGONAL FLAG (never a demoter) — same philosophy
# as GraphRanker: a contradicted claim with significant support is surfaced
# (contested:true + counter-evidence attached by the caller), not buried.

# Default exponents/weights for the multiplicative gate. Tunable per call
# via the StateRanker constructor (a/b/w_c) and recall_state() kwargs.
DEFAULT_RELEVANCE_EXP = 1.0
DEFAULT_CONFIDENCE_EXP = 1.0
DEFAULT_CENTRALITY_WEIGHT = 0.10
# Neutral confidence for uncalibrated points: the Beta(1,1) prior mean.
NEUTRAL_CONFIDENCE = 0.5
# About-edge family counted as degree centrality (mirrors supersede_point's
# structural_rels about* set; extractedFrom/wasDerivedFrom are provenance,
# not topical connectedness).
ABOUT_EDGE_TYPES = (
    "aboutSubject", "aboutObject", "aboutAction",
    "aboutEvent", "aboutPoint", "aboutDocument",
)


class StateRanker:
    """UC1 "state" recall ranking — multiplicative confidence gate (#898).

    score = relevance_norm^a × confidence^b × (1 + w_c × centrality_norm)

    Low confidence HARD-SUPPRESSES regardless of relevance (that is the
    point of the multiplicative gate): a claim with confidence 0 scores 0
    no matter how relevant it is. High relevance alone cannot rescue a
    low-confidence claim, because the two multiply rather than add.

    ``projection`` may be None for pure unit testing — graph signals are
    then read from the result dicts themselves (keys ``state_confidence``,
    ``state_degree``, ``state_contested``, ``state_variance``,
    ``state_has_ep``). This mirrors GraphRanker's embedded-signal contract.

    Mixed entity lists are supported: each result dict may carry an
    ``entity_type`` key ("point"/"object") that routes its signal fetch;
    objects get confidence from the mean EP posterior of the Points about
    them (via about* edges). The rerank() ``entity_type`` argument remains
    the default for items without the key.
    """

    def __init__(
        self,
        projection=None, *,
        relevance_exp: float = DEFAULT_RELEVANCE_EXP,
        confidence_exp: float = DEFAULT_CONFIDENCE_EXP,
        centrality_weight: float = DEFAULT_CENTRALITY_WEIGHT,
    ):
        if relevance_exp <= 0:
            raise ValueError(f"relevance_exp must be > 0, got {relevance_exp}")
        if confidence_exp <= 0:
            raise ValueError(f"confidence_exp must be > 0, got {confidence_exp}")
        if not 0.0 <= centrality_weight <= 1.0:
            raise ValueError(
                f"centrality_weight must be 0-1, got {centrality_weight}")
        self.projection = projection
        self.relevance_exp = relevance_exp
        self.confidence_exp = confidence_exp
        self.centrality_weight = centrality_weight

    # ── Public API ────────────────────────────────────────────────────

    def rerank(
        self,
        results: list[dict],
        *,
        entity_type: str = "point",
        query: str | None = None,
    ) -> list[dict]:
        """Re-rank results with the multiplicative gate.

        Each result needs at least ``id`` and a relevance-like field
        (``scores.rrf``, else ``similarity``, else ``confidence`` — same
        extraction as GraphRanker). Returns the list sorted by final score
        descending; input dicts are NOT mutated — annotated copies are
        returned with a ``recall_ranking`` breakdown:

        ``{relevance_norm, confidence, confidence_source, centrality_norm,
          base_score, final_score, degree, variance, contested}`` plus, on a
        DRAFT row only, ``draft: True`` (see #2598 below).

        #2598 — DRAFT HONESTY. A draft is EP-inert (``live._live_only``), so no
        belief is computed for it. Three consequences, all keyed off the row's
        own ``status`` (or the batch-read signal, which reads the same column):

        1. ``confidence`` is ``None`` and ``confidence_source`` is
           ``"uncomputed"`` when EP has not measured the claim — a draft is
           never displayed with the fabricated neutral 0.5 (#2598: "drafts are
           EP-inert — don't fake measurement"). A draft that DOES carry a
           persisted posterior (demoted after a real EP run) keeps its measured
           number: the number is real, only the row's staging is the news.
        2. the explicit ``draft`` key is emitted (only when true, so live hits
           stay byte-identical), so a consumer can badge without knowing the
           status vocabulary.
        3. drafts sort AFTER every live row. Ordering is
           ``(is_draft, -final_score)`` — an EXPLICIT status tiebreaker, not a
           side effect of the numbers. Today the question is answered by
           whichever fabricated 0.5 happens to win: a draft's neutral 0.5 and a
           contested live claim's measured 0.5 are the SAME number, and at equal
           relevance a draft OUTRANKS a measured live claim at 0.29 (#2598
           repro). Neither is an acceptable basis for the order.

        Live rows are unaffected: no ``draft`` key, the same numeric
        ``confidence``, the same score, and the same relative order (live rows
        keep their stable relative ordering under the new sort key).

        W4-b (issue #2102): ``query`` is OPTIONAL.  When a query is present
        AND the W4 flag is on, contested POINTS whose conflict is
        query-relevant receive a bounded multiplicative boost
        (``w4_contested_boost``) — flag-off or no-query runs are
        byte-identical to the pre-W4-b ranking (R11 golden-ordering
        regression)."""
        if not results:
            return results

        # Per-item entity routing (mixed point/object lists from recall_state).
        by_entity: dict[str, list[dict]] = {}
        for r in results:
            et = r.get("entity_type", entity_type)
            by_entity.setdefault(et, []).append(r)
        ids_by_entity = {et: [r.get("id") for r in rs if r.get("id")] for et, rs in by_entity.items()}
        signals: dict[str, dict] = {}
        if self.projection is not None:
            for et, ids in ids_by_entity.items():
                try:
                    if et == "object":
                        signals.update(self._fetch_object_signals(ids))
                    else:
                        signals.update(self._fetch_point_signals(ids))
                except Exception as e:  # pragma: no cover — defensive
                    logger.warning("StateRanker signal fetch failed (%s): %s", et, e)
        else:
            signals = self._embedded_signals(results)
        # W4-b contested-relevance resolution (ONE batch read, S8): only
        # POINT results can be contested (objects aggregate means); the
        # embedded (projection=None) unit path has no graph to resolve
        # counter-claims, so the boost is inert there.
        contested_boosts: dict[str, float] = {}
        if query and self.projection is not None and _w4_flag_on():
            contested_boosts = resolve_contested_relevance(
                self.projection,
                [pid for pid, sig in signals.items() if sig.get("contested")],
                query,
            )

        # 1. relevance_norm: min-max within the result set.
        relevances = [_similarity_of(r) for r in results]
        norm_rel = _min_max_normalize(relevances)

        # 2. confidence + centrality per result (graph signal or embedded).
        ann: list[dict] = []
        for r, norm_r in zip(results, norm_rel):  # noqa: B905
            sig = signals.get(r.get("id"), {})
            confidence, source = self._confidence_of(r, sig)
            # #2598: the row's STAGED state, read from the batch signal when the
            # graph was queried and from the row itself otherwise (the embedded
            # unit path, or an id the signal query did not return). Both read
            # the same ``n.status`` column, so the two cannot disagree on a
            # graph-backed read.
            draft = sig.get("draft")
            if draft is None:
                draft = is_draft_status(r.get("status"))
            draft = bool(draft)
            # #2598: EP has not measured this claim → there is no belief to
            # report. Only a DRAFT is gated here; an uncalibrated LIVE claim
            # keeps the documented #898 neutral 0.5 + ``confidence_source:
            # "neutral"`` disclosure (a recorded decision this change does not
            # reopen).
            uncomputed = draft and not self._has_ep(r, sig)
            degree = int(sig.get("degree", 0))
            base = (norm_r ** self.relevance_exp) * (confidence ** self.confidence_exp)
            copy = dict(r)
            # ``object``-valued: #2598 makes ``confidence`` ``float | None``
            # (null == "no belief computed"), so the literal's value types no
            # longer have a single numeric kind.
            ranking: dict[str, object] = {
                "relevance_norm": round(norm_r, 6),
                "confidence": None if uncomputed else round(confidence, 6),
                "confidence_source": (
                    UNCOMPUTED_CONFIDENCE_SOURCE if uncomputed else source),
                "degree": degree,
                "centrality_norm": 0.0,  # filled after the min-max pass
                "base_score": round(base, 6),
                "final_score": round(base, 6),  # updated with centrality below
            }
            if draft:
                # Additive, emitted only when true (the #1353 D8 / #2294 rule):
                # every live hit stays byte-identical.
                ranking["draft"] = True
            copy["recall_ranking"] = ranking
            if "variance" in sig:
                copy["recall_ranking"]["variance"] = round(sig["variance"], 6)
                copy["recall_ranking"]["contested"] = sig.get("contested", False)
            ann.append(copy)

        # 3. centrality_norm: min-max degree within the result set, then the
        #    weak multiplicative boost. All-equal degree (e.g. every result
        #    isolated) → norm 0.0: no differentiation → no boost (unlike
        #    _min_max_normalize's midpoint, which would uniformly inflate).
        degrees = [a["recall_ranking"]["degree"] for a in ann]
        if degrees and (max(degrees) - min(degrees) > 1e-12):
            norm_deg = _min_max_normalize(degrees)
        else:
            norm_deg = [0.0] * len(degrees)
        for a, nd in zip(ann, norm_deg):  # noqa: B905
            a["recall_ranking"]["centrality_norm"] = round(nd, 6)
            base = a["recall_ranking"]["base_score"]
            cb = contested_boosts.get(a.get("id"), 0.0)
            if cb:
                # W4-b: bounded relevance-gated contested boost as a weak
                # multiplicative factor (flag-off/irrelevant ⇒ absent ⇒ the
                # pre-W4-b final score is untouched).
                a["recall_ranking"]["w4_contested_boost"] = round(cb, 4)
            a["recall_ranking"]["final_score"] = round(
                base * (1.0 + self.centrality_weight * nd) * (1.0 + cb), 6)

        ann.sort(key=lambda r: (
            1 if r["recall_ranking"].get("draft") else 0,
            -r["recall_ranking"]["final_score"],
        ))
        return ann

    # ── Signal computation ────────────────────────────────────────────

    def _has_ep(self, result: dict, signals: dict) -> bool:
        """Whether EP has MEASURED this claim (#3276 semantics).

        The graph signal (``has_ep``) wins when the batch read returned the id;
        the embedded ``state_has_ep`` is the projection=None unit-path fallback.
        Extracted so :meth:`_confidence_of` and #2598's uncomputed-draft gate
        cannot drift on the same question.
        """
        has_ep = signals.get("has_ep")
        if has_ep is None:
            has_ep = bool(result.get("state_has_ep", False))
        return bool(has_ep)

    def _confidence_of(self, result: dict, signals: dict) -> tuple[float, str]:
        """Confidence (posterior mean) + source label for one result.

        Uncalibrated points (no persisted α/β) fall back to the documented
        neutral 0.5 (Beta(1,1) prior mean) — absence of measurement is NOT
        low support. Source is "posterior" when EP actually measured the
        claim, "neutral" otherwise.
        """
        has_ep = self._has_ep(result, signals)
        if "confidence" in signals:
            conf = float(signals["confidence"])
            return conf, ("posterior" if has_ep else "neutral")
        # Embedded fallback (projection=None unit tests).
        embedded = result.get("state_confidence")
        if isinstance(embedded, (int, float)):
            return float(embedded), result.get("state_confidence_source", "posterior")
        return NEUTRAL_CONFIDENCE, "neutral"

    def _fetch_point_signals(self, ids: list[str]) -> dict[str, dict]:
        """Batch-fetch state signals for Points: posterior mean, degree
        centrality (incident IMPL/NAND + about*), variance, contested."""
        if not ids:
            return {}
        cypher = (
            "MATCH (n:{label}) WHERE n.id IN $ids "
            "OPTIONAL MATCH (n)-[r:IMPL|NAND]-() "
            "WITH n, count(r) AS ep_degree "
            "OPTIONAL MATCH (n)-[a:aboutSubject|aboutObject|aboutAction|"
            "aboutEvent|aboutPoint|aboutDocument]->() "
            "WITH n, ep_degree, count(a) AS about_degree "
            "RETURN n.id, "
            "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0) AS alpha, "
            "  coalesce(n.posterior_beta, n.ep_beta, 1.0) AS beta, "
            f"  {ep_measured_cypher('n')} AS has_ep, "
            "  ep_degree + about_degree AS degree, "
            "  n.status, coalesce(n.outdated, false)"
        )
        rows = epistemic_sweep(
            self.projection.g, cypher, {"ids": ids})
        out = {}
        for row in rows:
            pid, alpha, beta, has_ep, degree = (
                row[0], float(row[1]), float(row[2]), bool(row[3]), int(row[4]))
            # #2490: terminal rows never surface as measured/contested EP (len
            # guard — see GraphRanker). #2598: the len guard also covers the
            # test fakes' 5-tuple row shape, so an absent status column reads
            # as "not a draft" rather than raising.
            status = row[5] if len(row) > 5 else ""
            if len(row) > 6 and is_terminal_status(row[5], bool(row[6])):
                has_ep = False
            variance = _beta_variance(alpha, beta)
            out[pid] = {
                "confidence": round(alpha / (alpha + beta), 6) if (alpha + beta) > 0 else NEUTRAL_CONFIDENCE,
                "has_ep": has_ep,
                "degree": degree,
                "variance": variance,
                # #2598: the staged state rides the SAME batch read EP already
                # needs — no extra query on the recall path.
                "status": status or "",
                "draft": is_draft_status(status),
                # Uncalibrated (no persisted α/β) is NOT contested — unmeasured.
                "contested": has_ep and variance > CONTESTED_VARIANCE_THRESHOLD,
            }
        return out

    def _fetch_object_signals(self, ids: list[str]) -> dict[str, dict]:
        """Batch-fetch state signals for Objects: confidence = MEAN EP
        posterior of the Points about them (via about* edges); degree =
        incident about* edge count. Objects with no about-Points degrade to
        neutral 0.5 (no measurement ≠ low support)."""
        if not ids:
            return {}
        about_types = "|".join(ABOUT_EDGE_TYPES)
        rows = self.projection.g.query(
            "MATCH (o:Object) WHERE o.id IN $ids "
            # #7927: ``p`` is a TRAVERSAL position — the belief an Object is
            # measured by may be any epistemic label, not only a Point.
            "OPTIONAL MATCH (p)-[a:" + about_types + "]->(o) "
            f"  WHERE {epistemic_disjunction('p')} "
            # ``pids`` feeds ``_fetch_point_signals`` — an ID-ANCHORED Point
            # read — so a belief with no ``id`` (a keyless legacy :Event) would
            # be a ``None`` in that list: the edge counted toward ``degree``
            # while its posterior vanished, i.e. a measured Object reported as
            # unmeasured with a phantom degree (measured: degree 1, has_ep
            # False). Held to what the pre-widening predicate admitted rather
            # than coalescing, because the value would be re-anchored as an
            # ``id`` it is not (#8538; #7927 review).
            "  AND p.id IS NOT NULL "
            "WITH o, count(a) AS about_degree, collect(DISTINCT p.id) AS pids "
            "RETURN o.id, about_degree, pids",
            params={"ids": ids},
        ).result_set
        # Batch-fetch posterior params for all about-Points (single query).
        all_pids = sorted({pid for row in rows for pid in row[2] if pid})
        point_sigs = self._fetch_point_signals(all_pids) if all_pids else {}
        out = {}
        for row in rows:
            oid, about_degree, pids = row[0], int(row[1]), row[2]
            confs = [point_sigs[p]["confidence"] for p in pids if p in point_sigs]
            mean = sum(confs) / len(confs) if confs else NEUTRAL_CONFIDENCE
            out[oid] = {
                "confidence": round(mean, 6),
                "has_ep": len(confs) > 0,
                "degree": about_degree,
                "variance": 0.0,
                "contested": False,
                # #2598: the Object status family has NO ``draft`` member
                # (``commit_ops.OBJECT_TERMINAL_STATUSES``), so an Object can
                # never be a staged row — declared explicitly rather than left
                # absent so the rerank gate reads one shape for both families.
                "status": "",
                "draft": False,
            }
        return out

    def _embedded_signals(self, results: list[dict]) -> dict[str, dict]:
        """Read state signals embedded in result dicts (projection=None).

        Keys: state_confidence, state_has_ep, state_degree, state_variance,
        state_contested. Absent keys degrade to neutral, never a penalty.
        """
        out = {}
        for r in results:
            rid = r.get("id")
            if not rid:
                continue
            sig = {}
            if isinstance(r.get("state_confidence"), (int, float)):
                sig["confidence"] = float(r["state_confidence"])
            sig["has_ep"] = bool(r.get("state_has_ep", False))
            sig["degree"] = int(r.get("state_degree", 0))
            if isinstance(r.get("state_variance"), (int, float)):
                sig["variance"] = float(r["state_variance"])
                sig["contested"] = bool(r.get("state_contested", False))
            # #2598: the staged state, read from the row's own status column
            # (this path exists precisely because there is no graph to query).
            sig["status"] = r.get("status") or ""
            sig["draft"] = is_draft_status(sig["status"])
            out[rid] = sig
        return out


# ── GapsRanker (recall UC2, #898 Wave B) ──────────────────────────────
# UC2 "gaps" recall surfaces the weak links of a reasoning cycle: claims that
# are LOAD-BEARING (the graph leans on them — they provide confidence to
# others via IMPL, or actively attack via a strong NAND) but are themselves
# POORLY SUPPORTED (few incoming IMPL, no Source, low confidence). This is a
# graph-STRUCTURE query (epistemic load vs epistemic support), not semantic
# similarity:
#
#     load    = outgoing IMPL + outgoing NAND edge count
#     support = incoming IMPL + extractedFrom→Source edge count
#     score   = load / (1 + support)          # "load high AND support low"
#
# Reification rule (ontology v3.5 §8): operator-less IMPL/NAND edges may
# exist, so load/support read IMPL/NAND edges WHETHER operator-mediated or
# direct:
#   * operator-mediated: the operator Point carries IMPL/NAND edges to its
#     inputs with ``idx`` (0 = source, >=1 = target) — an edge with idx=0
#     INTO n means n is the operator's source (n implies/attacks the
#     targets → load); an IMPL edge with idx>=1 INTO n means n is a target
#     (n is supported → support).
#   * direct (reification): a bare (s)-[:IMPL]->(n) edge from a non-operator
#     Point or an Event is incoming support; (n)-[:IMPL|NAND]->(t) to a
#     non-operator Point is outgoing load.
# Incoming NAND is NOT support (it is contradiction/contention) — surfaced
# separately as ``incoming_nand`` + ``contested``, never scored as support.
#
# Confidence (EP posterior mean, same read as StateRanker) is surfaced in the
# breakdown as a diagnostic — the rank itself stays structural. The
# min_load / max_support thresholds that define a "gap" are enforced by the
# caller (recall_gaps), mirroring recall_state's min_confidence floor.
#
# #2598 — DRAFT HONESTY applies to this ranker too, and for the same reason.
# ``recall_gaps`` does NOT exclude drafts (a draft is CURRENT, not terminal —
# see ``live.py``), so ``tortoise_recall mode=gaps`` can return one. It is
# EP-inert, so no belief was computed for it, and the diagnostic must say so:
# badged ``draft: True``, ``confidence: null`` +
# ``confidence_source: "uncomputed"`` instead of the neutral 0.5, and sorted
# after every live gap by an explicit status tiebreaker. ``mode=gaps`` has its
# OWN ranker (this one), which is why the StateRanker fix did not reach it.
#
# ``projection`` may be None for pure unit testing — graph signals are then
# read from the result dicts themselves (keys ``gaps_outgoing_impl``,
# ``gaps_outgoing_nand``, ``gaps_incoming_impl``, ``gaps_incoming_nand``,
# ``gaps_source_count``, ``gaps_confidence``, ``gaps_has_ep``,
# ``gaps_variance``, ``gaps_contested``) plus the row's own unprefixed
# ``status`` column (#2598 — the staged state). Absent keys degrade to
# zero/neutral — never a penalty.

# Default gap thresholds (enforced by recall_gaps, not the ranker).
DEFAULT_GAPS_MIN_LOAD = 1       # load-bearing: ≥1 outgoing IMPL/NAND edge
DEFAULT_GAPS_MAX_SUPPORT = 2    # "few incoming IMPL, no Source" boundary


class GapsRanker:
    """UC2 "gaps" recall ranking — epistemic load vs epistemic support (#898).

    score = load / (1 + support)

    High load AND low support → high score (a gap worth investigating).
    Zero load → score 0.0 (an isolated claim is NOT a gap — nothing leans
    on it). High support collapses the score (a claim the graph already
    supports is not under-supported).

    ``projection`` may be None for pure unit testing — graph signals are
    then read from the result dicts themselves (keys ``gaps_*``, see class
    docstring).
    """

    def __init__(self, projection=None):
        self.projection = projection

    # ── Public API ────────────────────────────────────────────────────

    def rerank(self, results: list[dict]) -> list[dict]:
        """Score claim results with the load/support formula.

        Each result needs at least ``id``. Returns the list sorted by score
        descending; input dicts are NOT mutated — annotated copies are
        returned with a ``gaps_ranking`` breakdown:

        ``{outgoing_impl, outgoing_nand, load, incoming_impl, incoming_nand,
          source_count, support, confidence, confidence_source, score}``
        plus, on a DRAFT row only, ``draft: True`` (#2598 — see the module note
        above the class). ``confidence`` is ``float | None``: null means "no
        belief was computed for this staged row".
        """
        if not results:
            return results

        signals = {}
        if self.projection is not None:
            ids = [r.get("id") for r in results if r.get("id")]
            try:
                # #7927: resolve the PRESENT labels ONCE for the whole rerank.
                # The three signal reads below are anchored on the SAME id set,
                # so resolving inside each one paid the resolution three times
                # — measured: 6 round-trips for a Point-only rerank where the
                # pre-widening path took 3, three of them the byte-identical
                # ``MATCH (n:Point) WHERE n.id IN $ids RETURN n.id``. That is
                # the exact cost ``epistemic_labels_of`` exists to avoid, so it
                # is paid once here and threaded down. Fail-open: a resolution
                # error leaves ``None`` and each read re-resolves as before.
                try:
                    _labels = epistemic_labels_of(self.projection.g, ids)
                except Exception as e:  # pragma: no cover — defensive
                    logger.warning("GapsRanker label resolution failed: %s", e)
                    _labels = None
                # Per-signal dicts carry per-id sub-dicts — merge the FIELDS
                # (dict.update would REPLACE the whole value for an id,
                # dropping earlier signals).
                for part in (
                    self._fetch_load_signals(ids, labels=_labels),
                    self._fetch_support_signals(ids, labels=_labels),
                    self._fetch_confidence_signals(ids, labels=_labels),
                ):
                    for pid, fields in part.items():
                        signals.setdefault(pid, {}).update(fields)
            except Exception as e:  # pragma: no cover — defensive
                logger.warning("GapsRanker signal fetch failed: %s", e)
        else:
            signals = self._embedded_signals(results)

        ann: list[dict] = []
        for r in results:
            sig = signals.get(r.get("id"), {})
            outgoing_impl = int(sig.get("outgoing_impl", 0))
            outgoing_nand = int(sig.get("outgoing_nand", 0))
            incoming_impl = int(sig.get("incoming_impl", 0))
            incoming_nand = int(sig.get("incoming_nand", 0))
            source_count = int(sig.get("source_count", 0))
            load = outgoing_impl + outgoing_nand
            support = incoming_impl + source_count
            score = load / (1.0 + support)
            confidence, conf_source = self._confidence_of(r, sig)
            # #2598: the row's STAGED state, read from the batch signal when the
            # graph was queried and from the row itself otherwise — the same two
            # reads (and the same ``n.status`` column) StateRanker uses, so the
            # two rankers cannot disagree on what a draft is.
            draft = sig.get("draft")
            if draft is None:
                draft = is_draft_status(r.get("status"))
            draft = bool(draft)
            # #2598: EP has not measured a draft (it is EP-inert at every
            # factor-extraction call site), so there is no belief to report —
            # never the fabricated neutral 0.5. Only a DRAFT is gated here: an
            # uncalibrated LIVE claim keeps the documented #898 neutral 0.5 +
            # ``confidence_source: "neutral"``. A draft that DOES carry a
            # persisted posterior keeps its measured number — there the number is
            # real and only the staging is the news.
            uncomputed = draft and not self._has_ep(r, sig)
            copy = dict(r)
            ranking: dict[str, object] = {
                "outgoing_impl": outgoing_impl,
                "outgoing_nand": outgoing_nand,
                "load": load,
                "incoming_impl": incoming_impl,
                "incoming_nand": incoming_nand,
                "source_count": source_count,
                "support": support,
                "confidence": None if uncomputed else round(confidence, 6),
                "confidence_source": (
                    UNCOMPUTED_CONFIDENCE_SOURCE if uncomputed else conf_source),
                "score": round(score, 6),
            }
            if draft:
                # Additive, emitted only when true (the #1353 D8 / #2294 rule):
                # every live gap stays byte-identical.
                ranking["draft"] = True
            copy["gaps_ranking"] = ranking
            if "variance" in sig:
                copy["gaps_ranking"]["variance"] = round(sig["variance"], 6)
                copy["gaps_ranking"]["contested"] = sig.get("contested", False)
            ann.append(copy)

        # #2598: drafts sort AFTER every live gap. Ordering is an EXPLICIT
        # status tiebreaker — ``(is_draft, -score)`` — not a score side effect:
        # the structural score says nothing about staging, so a draft's score
        # (however high) is not an acceptable basis for placing it first. The
        # same contract StateRanker states for recall_state.
        ann.sort(key=lambda r: (
            1 if r["gaps_ranking"].get("draft") else 0,
            -r["gaps_ranking"]["score"],
        ))
        return ann

    # ── Signal computation ────────────────────────────────────────────

    def _has_ep(self, result: dict, signals: dict) -> bool:
        """Whether EP has MEASURED this claim (#3276 semantics).

        The graph signal (``has_ep``) wins when the batch read returned the id;
        the embedded ``gaps_has_ep`` is the projection=None unit-path fallback.
        Extracted so :meth:`_confidence_of` and #2598's uncomputed-draft gate
        cannot drift on the same question — the same seam StateRanker carries.
        """
        has_ep = signals.get("has_ep")
        if has_ep is None:
            has_ep = bool(result.get("gaps_has_ep", False))
        return bool(has_ep)

    def _confidence_of(self, result: dict, signals: dict) -> tuple[float, str]:
        """Confidence (posterior mean) + source label for one result.

        Uncalibrated points fall back to the documented neutral 0.5 — same
        convention as StateRanker (absence of measurement is NOT low support;
        the STRUCTURE is what makes a gap). For a DRAFT the caller replaces
        both with null/``"uncomputed"`` (#2598) — this method reports the read,
        the staging gate lives in :meth:`rerank`."""
        has_ep = self._has_ep(result, signals)
        if "confidence" in signals:
            conf = float(signals["confidence"])
            return conf, ("posterior" if has_ep else "neutral")
        embedded = result.get("gaps_confidence")
        if isinstance(embedded, (int, float)):
            return float(embedded), result.get("gaps_confidence_source", "posterior")
        return NEUTRAL_CONFIDENCE, "neutral"

    def _fetch_load_signals(self, ids: list[str], *,
                            labels: list[str] | None = None) -> dict[str, dict]:
        """Outgoing epistemic load: IMPL/NAND edges where the claim is the
        SOURCE — operator-mediated (idx=0) or direct to a non-operator Point.
        Operator and direct edges are counted in SEPARATE columns (chained
        OPTIONAL MATCHes cross-product; a single per-row CASE over both
        patterns would drop one of two same-type edges — P0 fix).

        ``labels`` is the caller's already-resolved present-label set —
        ``GapsRanker.rerank`` resolves once and threads it through its three
        reads, so the resolution is not paid per read."""
        if not ids:
            return {}
        rows = epistemic_sweep(
            self.projection.g,
            "MATCH (n:{label}) WHERE n.id IN $ids "
            "OPTIONAL MATCH (op:Point {is_operator:true})-[r1:IMPL|NAND]->(n) "
            "  WHERE coalesce(r1.idx, 1) = 0 "
            "OPTIONAL MATCH (n)-[r2:IMPL|NAND]->(m) "
            # #7927: ``m`` is a TRAVERSAL position — a :Subject/:Object/:Event
            # carries no ``is_operator``, so admit UNSET as well as false.
            "  WHERE (m.is_operator IS NULL OR m.is_operator = false) "
            # The CANONICAL non-operator predicate: a legacy ``op_type``-only
            # operator (#2205/#943) has no ``is_operator``, so the UNSET
            # admission alone would count an operator's own edge as a direct
            # load — the pre-#7927 ``m.is_operator = false`` excluded it.
            "    AND m.op_type IS NULL "
            f"    AND {epistemic_disjunction('m')} "
            "RETURN n.id, "
            "  count(DISTINCT CASE WHEN type(r1) = 'IMPL' THEN r1 END) AS op_load_impl, "
            "  count(DISTINCT CASE WHEN type(r1) = 'NAND' THEN r1 END) AS op_load_nand, "
            "  count(DISTINCT CASE WHEN type(r2) = 'IMPL' THEN r2 END) AS direct_load_impl, "
            "  count(DISTINCT CASE WHEN type(r2) = 'NAND' THEN r2 END) AS direct_load_nand",
            {"ids": ids},
            labels=labels,
        )
        return {
            row[0]: {
                "outgoing_impl": int(row[1]) + int(row[3]),
                "outgoing_nand": int(row[2]) + int(row[4]),
            }
            for row in rows
        }

    def _fetch_support_signals(self, ids: list[str], *,
                               labels: list[str] | None = None) -> dict[str, dict]:
        """Incoming epistemic support: IMPL edges where the claim is a TARGET
        (operator-mediated idx>=1, or direct from a non-operator Point/Event),
        plus extractedFrom→Source count. Incoming NAND is counted separately
        (contention — never support).

        ``labels`` is the caller's already-resolved present-label set — see
        ``_fetch_load_signals``."""
        if not ids:
            return {}
        rows = epistemic_sweep(
            self.projection.g,
            "MATCH (n:{label}) WHERE n.id IN $ids "
            "OPTIONAL MATCH (op:Point {is_operator:true, op_type:'IMPL'})-[r3:IMPL]->(n) "
            "  WHERE coalesce(r3.idx, 0) >= 1 "
            "OPTIONAL MATCH (s)-[r4:IMPL]->(n) "
            # #7927: ``s`` is a TRAVERSAL position — admit the four labels and
            # treat an UNSET ``is_operator`` (:Subject/:Object/:Event) as a
            # non-operator claim, exactly as the old ``(s:Event)`` arm did.
            f"  WHERE (s.is_operator IS NULL OR s.is_operator = false) "
            "    AND s.op_type IS NULL "  # canonical: a legacy op_type-only operator is an operator
            f"    AND {epistemic_disjunction('s')} "
            "OPTIONAL MATCH (n)-[r5:extractedFrom]->(src:Source) "
            "OPTIONAL MATCH (opn:Point {is_operator:true, op_type:'NAND'})-[r6:NAND]->(n) "
            "  WHERE coalesce(r6.idx, 1) >= 1 "
            "OPTIONAL MATCH (s2)-[r7:NAND]->(n) "
            f"  WHERE (s2.is_operator IS NULL OR s2.is_operator = false) "
            "    AND s2.op_type IS NULL "  # canonical: a legacy op_type-only operator is an operator
            f"    AND {epistemic_disjunction('s2')} "
            "RETURN n.id, "
            "  count(DISTINCT r3) AS op_support, "
            "  count(DISTINCT r4) AS direct_support, "
            "  count(DISTINCT r5) AS source_count, "
            "  count(DISTINCT r6) AS op_nand, "
            "  count(DISTINCT r7) AS direct_nand",
            {"ids": ids},
            labels=labels,
        )
        return {
            row[0]: {
                "incoming_impl": int(row[1]) + int(row[2]),
                "source_count": int(row[3]),
                # Operator-mediated AND direct (reification) NAND into the
                # claim — both are contention, surfaced never scored.
                "incoming_nand": int(row[4]) + int(row[5]),
            }
            for row in rows
        }

    def _fetch_confidence_signals(self, ids: list[str], *,
                                  labels: list[str] | None = None) -> dict[str, dict]:
        """EP posterior mean + contested — same read as StateRanker.

        ``labels`` is the caller's already-resolved present-label set — see
        ``_fetch_load_signals``."""
        if not ids:
            return {}
        rows = epistemic_sweep(
            self.projection.g,
            "MATCH (n:{label}) WHERE n.id IN $ids "
            "RETURN n.id, "
            "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0) AS alpha, "
            "  coalesce(n.posterior_beta, n.ep_beta, 1.0) AS beta, "
            f"  {ep_measured_cypher('n')} AS has_ep, "
            "  n.status, coalesce(n.outdated, false)",
            {"ids": ids},
            labels=labels,
        )
        out = {}
        for row in rows:
            pid, alpha, beta, has_ep = row[0], float(row[1]), float(row[2]), bool(row[3])
            # #2490: terminal rows never surface as measured/contested EP (len
            # guard — see GraphRanker). #2598: the len guard also covers the test
            # fakes' 4-tuple row shape, so an absent status column reads as "not a
            # draft" rather than raising.
            status = row[4] if len(row) > 4 else ""
            if len(row) > 5 and is_terminal_status(row[4], bool(row[5])):
                has_ep = False
            variance = _beta_variance(alpha, beta)
            out[pid] = {
                "confidence": round(alpha / (alpha + beta), 6) if (alpha + beta) > 0 else NEUTRAL_CONFIDENCE,
                "has_ep": has_ep,
                "variance": variance,
                # #2598: the staged state rides the SAME batch read EP already
                # needs — no extra query on the recall path.
                "status": status or "",
                "draft": is_draft_status(status),
                "contested": has_ep and variance > CONTESTED_VARIANCE_THRESHOLD,
            }
        return out

    def _embedded_signals(self, results: list[dict]) -> dict[str, dict]:
        """Read gaps signals embedded in result dicts (projection=None).

        Keys: gaps_* load/support/confidence signals plus the row's own
        ``status`` (and the ``draft`` predicate derived from it, #2598 — this
        path exists precisely because there is no graph to query). Absent keys
        degrade to zero/neutral, never a penalty.
        """
        out = {}
        for r in results:
            rid = r.get("id")
            if not rid:
                continue
            sig = {}
            for key in ("outgoing_impl", "outgoing_nand", "incoming_impl",
                        "incoming_nand", "source_count"):
                if isinstance(r.get(f"gaps_{key}"), (int, float)):
                    sig[key] = int(r[f"gaps_{key}"])
            if isinstance(r.get("gaps_confidence"), (int, float)):
                sig["confidence"] = float(r["gaps_confidence"])
            sig["has_ep"] = bool(r.get("gaps_has_ep", False))
            if isinstance(r.get("gaps_variance"), (int, float)):
                sig["variance"] = float(r["gaps_variance"])
                sig["contested"] = bool(r.get("gaps_contested", False))
            # #2598: the staged state, read from the row's own status column
            # (this path exists precisely because there is no graph to query).
            sig["status"] = r.get("status") or ""
            sig["draft"] = is_draft_status(sig["status"])
            out[rid] = sig
        return out


# ── SubgraphExpander (recall UC3, #898 Wave B) ─────────────────────────
# UC3 "subgraph" recall returns the COMPLETE connected subgraph for a
# seed/topic — completeness-optimized (high recall, precision secondary).
# Used before connecting a new document to the graph: the agent needs the
# deep picture first, not a precision-ranked slice.
#
# Operation: resolve seed(s) → BFS-expand along ALL relationship types up to
# ``depth`` hops → return the collected nodes PLUS the edges between them.
#
#   * ``completeness="full"`` (default): every relationship type is an edge
#     of the subgraph (about*, extractedFrom, hasPart, mitigated_by, ...).
#   * ``completeness="core"``: epistemic core only — IMPL|NAND edges.
#   * Session/Tag nodes are bookkeeping containers, not knowledge — excluded
#     from expansion; their CONTAINS/TAGGED edges never appear.
#
# Bounded, not exhaustive-until-crash: ``max_nodes`` caps the node count
# (default 500) and ``depth`` is capped at 5. `truncated` in stats reports
# whether the cap was hit.

# Node labels that participate in the knowledge subgraph.
# D10 (ONTOLOGY v3.15 §4.4): :Document is retired — a document is a :Source,
# so the Source label already covers it.
SUBNODE_LABELS = (
    "Point", "Object", "Subject", "Event", "Source",
)
_SUBNODE_LABEL_WHERE = " OR ".join(f"m:{lab}" for lab in SUBNODE_LABELS)


class SubgraphExpander:
    """Complete connected-subgraph expansion for a seed (#898 UC3)."""

    def __init__(self, projection):
        self.projection = projection

    # ── Public API ────────────────────────────────────────────────────

    def expand(self, seeds: list[str], *,
               depth: int = 2,
               completeness: str = "full",
               max_nodes: int = 500) -> dict:
        """Expand from seed node ids to the connected subgraph.

        Returns ``{nodes, edges, stats}``:
        - nodes: [{id, type, content, kind, is_operator, status, confidence}]
        - edges: [{source, type, target}] — BOTH endpoints in the node set;
          deduplicated per (source, type, target) pair (parallel same-type
          edges between the same two nodes are collapsed in the edge list
          — the epistemic count is available via GapsRanker/StateRanker).
        - stats: {node_count, edge_count, depth, seed_count, truncated} —
          depth = hops actually expanded (<= requested depth; 0 when the
          seed resolved to nothing), seed_count = seeds that resolved to
          graph nodes.
        """
        if depth < 1 or depth > 5:
            raise ValueError(f"depth must be 1-5, got {depth}")
        if completeness not in ("core", "full"):
            raise ValueError(
                f"completeness must be 'core' or 'full', got {completeness!r}")
        if max_nodes < 10 or max_nodes > 5000:
            raise ValueError(f"max_nodes must be 10-5000, got {max_nodes}")
        if not seeds:
            return {"nodes": [], "edges": [],
                    "stats": {"node_count": 0, "edge_count": 0, "depth": 0,
                               "seed_count": 0, "truncated": False}}

        rel_pattern = ":IMPL|NAND" if completeness == "core" else ""
        # Seeds are part of the subgraph — never dropped from the node set.
        nodes: dict[str, dict] = {sid: {"id": sid} for sid in seeds if sid}
        edges: dict[tuple[str, str, str], dict] = {}
        frontier: list[str] = [s for s in seeds if s]
        truncated = False
        reached = 0  # hops actually expanded (a 1-node graph reaches 1)

        for _hop in range(depth):
            if not frontier:
                break
            reached += 1
            next_frontier: list[str] = []
            # Batch-fetch neighbors (both directions) + node payloads.
            neighbor_ids = self._neighbors(frontier, rel_pattern, edges)
            for nid in neighbor_ids:
                if nid in nodes:
                    continue
                if len(nodes) >= max_nodes:
                    truncated = True
                    break
                nodes[nid] = {"id": nid}
                next_frontier.append(nid)
            if truncated:
                break
            frontier = next_frontier

        # Node payloads (single batch query for display + metadata).
        self._enrich(nodes)
        # Drop seeds that did not resolve to a graph node (dangling id).
        resolved_seeds = [sid for sid in seeds if sid in nodes]
        for nid in [nid for nid, n in nodes.items() if "type" not in n]:
            del nodes[nid]
        resolved_seeds = [sid for sid in resolved_seeds if sid in nodes]
        # Keep only edges whose endpoints are both in the final node set
        # (a capped expansion may have dangling edges to excluded nodes).
        node_ids = set(nodes)
        kept = [
            e for e in edges.values()
            if e["source"] in node_ids and e["target"] in node_ids
        ]
        kept.sort(key=lambda e: (e["source"], e["type"], e["target"]))
        return {
            "nodes": list(nodes.values()),
            "edges": kept,
            "stats": {
                "node_count": len(nodes),
                "edge_count": len(kept),
                "depth": reached,
                "seed_count": len(resolved_seeds),
                "truncated": truncated,
            },
        }

    # ── Expansion internals ───────────────────────────────────────────

    def _neighbors(self, frontier: list[str], rel_pattern: str,
                   edges: dict[tuple[str, str, str], dict]) -> list[str]:
        """One BFS hop: all neighbors of the frontier via (optionally
        restricted) relationship types, recording edges as we go.

        ``rel_pattern`` is the edge-type pattern inside [ ] — empty string
        for all types, ":IMPL|NAND" for the epistemic core."""
        edge_decl = f"[r{rel_pattern}]"
        ids: list[str] = []
        # out: edges FROM frontier nodes (n)-[r]->(m) → record n→m, neighbor m.
        rows = self.projection.g.query(
            f"MATCH (n) WHERE n.id IN $frontier "
            "WITH n "
            f"MATCH (n)-{edge_decl}->(m) "
            f"WHERE {_SUBNODE_LABEL_WHERE} RETURN n.id, type(r), m.id",
            params={"frontier": frontier},
        ).result_set
        for src_id, rel, tgt_id in rows:
            edges.setdefault((src_id, rel, tgt_id),
                             {"source": src_id, "type": rel, "target": tgt_id})
            ids.append(tgt_id)
        # in: edges INTO frontier nodes (m)-[r]->(n) → record m→n, neighbor m.
        rows = self.projection.g.query(
            f"MATCH (n) WHERE n.id IN $frontier "
            "WITH n "
            f"MATCH (m)-{edge_decl}->(n) "
            f"WHERE {_SUBNODE_LABEL_WHERE} RETURN m.id, type(r), n.id",
            params={"frontier": frontier},
        ).result_set
        for src_id, rel, tgt_id in rows:
            edges.setdefault((src_id, rel, tgt_id),
                             {"source": src_id, "type": rel, "target": tgt_id})
            ids.append(src_id)
        return ids

    def _enrich(self, nodes: dict[str, dict]) -> None:
        """Fill node metadata (type label, display content, kind, status,
        confidence) in one batch query."""
        if not nodes:
            return
        rows = self.projection.g.query(
            "MATCH (n) WHERE n.id IN $ids "
            "RETURN n.id, labels(n)[0] AS label, "
            "  coalesce(n.content, n.name, n.title, n.url, '') AS display, "
            "  coalesce(n.pointKind, n.objectKind, n.subjectKind, "
            "           n.eventKind, n.documentKind, n.sourceKind, '') AS kind, "
            "  coalesce(n.is_operator, false) AS is_operator, "
            "  coalesce(n.status, '') AS status, "
            "  CASE WHEN labels(n)[0] = 'Point' THEN coalesce(n.confidence, 0.5) "
            "       ELSE null END AS confidence",
            params={"ids": list(nodes)},
        ).result_set
        for nid, label, display, kind, is_op, status, confidence in rows:
            node = nodes.get(nid)
            if node is None:
                continue
            node["type"] = (label or "").lower()
            node["content"] = display or ""
            if kind:
                node["kind"] = kind
            if is_op:
                node["is_operator"] = True
            if status:
                node["status"] = status
            if confidence is not None:
                node["confidence"] = round(float(confidence), 6)


def _similarity_of(result: dict) -> float:
    """Extract the similarity signal from a result dict.

    Preference: scores.rrf (hybrid engine) → similarity (legacy alias) →
    confidence (suggest_entry_points shape). Defaults to 0.0 when absent.
    """
    scores = result.get("scores")
    if isinstance(scores, dict):
        rrf = scores.get("rrf")
        if isinstance(rrf, (int, float)):
            return float(rrf)
    for key in ("similarity", "confidence"):
        val = result.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0
