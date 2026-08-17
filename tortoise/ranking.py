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
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from .search_engine import _beta_variance, CONTESTED_VARIANCE_THRESHOLD  # noqa: E402

logger = logging.getLogger(__name__)

# Default signal weights — must sum to 1.0.
DEFAULT_SIMILARITY_WEIGHT = 0.5
DEFAULT_GRAPH_BOOST_WEIGHT = 0.35
DEFAULT_RECENCY_WEIGHT = 0.15
DEFAULT_HALF_LIFE_DAYS = 30.0


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
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
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

    # ── Public API ────────────────────────────────────────────────────────

    def rerank(
        self,
        results: list[dict],
        *,
        entity_type: str = "point",
    ) -> list[dict]:
        """Re-rank results, each annotated with a ``graph_ranking`` breakdown.

        Each result dict needs at least ``id`` and a similarity-like field
        (``scores.rrf``, else ``similarity``, else ``confidence``). Returns the
        list sorted by final_score descending; input dicts are NOT mutated —
        annotated copies are returned.
        """
        if not results:
            return results

        ids = [r.get("id") for r in results if r.get("id")]
        signals = self._fetch_signals(ids, entity_type) if self.projection is not None else {}

        similarities = []
        for r in results:
            sim = _similarity_of(r)
            similarities.append(sim)

        norm_sims = _min_max_normalize(similarities)
        annotated: list[dict] = []
        for r, norm_sim in zip(results, norm_sims):
            sig = signals.get(r.get("id"), {})
            graph_boost = self.graph_boost(r, sig)
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

        Contestation is deliberately NOT used as a ranking penalty: a
        contested claim is a claim the agent should KNOW is contested (the
        ep.contested flag + variance on the result), not one that is silently
        ranked lower. Ranking stays about relevance and graph structure;
        epistemic honesty is surfaced, not scored.
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
                connectivity = 1.0 - 1.0 / (1.0 + (degree or 0))
                return round(0.5 * confidence + 0.5 * connectivity, 4)
            # #1348 ablation: degree neutralized — boost is confidence-only.
            return round(confidence, 4)
        # No graph data — degrade to neutral 0.0 (never a penalty).
        return 0.0

    def recency_boost(self, result: dict, signals: dict) -> float:
        """Exponential recency decay from createdAt/startedAt; missing → 1.0."""
        ts = signals.get("created") or result.get("createdAt") or result.get("startedAt")
        dt = _parse_iso(ts)
        if dt is None:
            return 1.0  # unknown age — neutral, no demotion
        age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
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
        cypher = (
            "MATCH (n:Point) WHERE n.id IN $ids "
            "OPTIONAL MATCH (n)-[r:IMPL|NAND]-(:Point {is_operator: true}) "
            "WITH n, count(r) AS degree "
            "RETURN n.id, coalesce(n.confidence, 0.5) AS conf, degree, n.createdAt AS created, "
            "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0) AS alpha, coalesce(n.posterior_beta, n.ep_beta, 1.0) AS beta, "
            "  n.ep_alpha IS NOT NULL AS has_ep"
        )
        rows = self.projection.g.query(cypher, params={"ids": ids}).result_set
        out = {}
        for row in rows:
            pid = row[0]
            variance = _beta_variance(float(row[4]), float(row[5]))
            has_ep = bool(row[6])
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
#   signal the existing order_by="confidence" path reads (n.confidence).
#   Uncalibrated points (no persisted α/β → Beta(1,1)) fall back to a
#   documented neutral 0.5: absence of measurement is NOT evidence against.
#   NOTE: EpBreakdown.confidence_mean is deliberately NOT used — it is the
#   structural impl/(impl+nand) edge-ratio ("edge-ratio, not belief", per
#   sdk.py order_by=confidence) and ties equally-relevant claims with
#   different support levels (3 IMPL vs 1 IMPL both read 1.0).
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
    ) -> list[dict]:
        """Re-rank results with the multiplicative gate.

        Each result needs at least ``id`` and a relevance-like field
        (``scores.rrf``, else ``similarity``, else ``confidence`` — same
        extraction as GraphRanker). Returns the list sorted by final score
        descending; input dicts are NOT mutated — annotated copies are
        returned with a ``recall_ranking`` breakdown:

        ``{relevance_norm, confidence, confidence_source, centrality_norm,
          base_score, final_score, degree, variance, contested}``
        """
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

        # 1. relevance_norm: min-max within the result set.
        relevances = [_similarity_of(r) for r in results]
        norm_rel = _min_max_normalize(relevances)

        # 2. confidence + centrality per result (graph signal or embedded).
        ann: list[dict] = []
        for r, norm_r in zip(results, norm_rel):
            sig = signals.get(r.get("id"), {})
            confidence, source = self._confidence_of(r, sig)
            degree = int(sig.get("degree", 0))
            base = (norm_r ** self.relevance_exp) * (confidence ** self.confidence_exp)
            copy = dict(r)
            copy["recall_ranking"] = {
                "relevance_norm": round(norm_r, 6),
                "confidence": round(confidence, 6),
                "confidence_source": source,
                "degree": degree,
                "centrality_norm": 0.0,  # filled after the min-max pass
                "base_score": round(base, 6),
                "final_score": round(base, 6),  # updated with centrality below
            }
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
        for a, nd in zip(ann, norm_deg):
            a["recall_ranking"]["centrality_norm"] = round(nd, 6)
            base = a["recall_ranking"]["base_score"]
            a["recall_ranking"]["final_score"] = round(
                base * (1.0 + self.centrality_weight * nd), 6)

        ann.sort(key=lambda r: r["recall_ranking"]["final_score"], reverse=True)
        return ann

    # ── Signal computation ────────────────────────────────────────────

    def _confidence_of(self, result: dict, signals: dict) -> tuple[float, str]:
        """Confidence (posterior mean) + source label for one result.

        Uncalibrated points (no persisted α/β) fall back to the documented
        neutral 0.5 (Beta(1,1) prior mean) — absence of measurement is NOT
        low support. Source is "posterior" when EP actually measured the
        claim, "neutral" otherwise.
        """
        has_ep = signals.get("has_ep")
        if has_ep is None:
            has_ep = bool(result.get("state_has_ep", False))
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
            "MATCH (n:Point) WHERE n.id IN $ids "
            "OPTIONAL MATCH (n)-[r:IMPL|NAND]-() "
            "WITH n, count(r) AS ep_degree "
            "OPTIONAL MATCH (n)-[a:aboutSubject|aboutObject|aboutAction|"
            "aboutEvent|aboutPoint|aboutDocument]->() "
            "WITH n, ep_degree, count(a) AS about_degree "
            "RETURN n.id, "
            "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0) AS alpha, "
            "  coalesce(n.posterior_beta, n.ep_beta, 1.0) AS beta, "
            "  (n.posterior_alpha IS NOT NULL OR n.ep_alpha IS NOT NULL) AS has_ep, "
            "  ep_degree + about_degree AS degree"
        )
        rows = self.projection.g.query(cypher, params={"ids": ids}).result_set
        out = {}
        for row in rows:
            pid, alpha, beta, has_ep, degree = (
                row[0], float(row[1]), float(row[2]), bool(row[3]), int(row[4]))
            variance = _beta_variance(alpha, beta)
            out[pid] = {
                "confidence": round(alpha / (alpha + beta), 6) if (alpha + beta) > 0 else NEUTRAL_CONFIDENCE,
                "has_ep": has_ep,
                "degree": degree,
                "variance": variance,
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
            "OPTIONAL MATCH (p:Point)-[a:" + about_types + "]->(o) "
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
# ``projection`` may be None for pure unit testing — graph signals are then
# read from the result dicts themselves (keys ``gaps_outgoing_impl``,
# ``gaps_outgoing_nand``, ``gaps_incoming_impl``, ``gaps_incoming_nand``,
# ``gaps_source_count``, ``gaps_confidence``, ``gaps_has_ep``,
# ``gaps_variance``, ``gaps_contested``). Absent keys degrade to zero/neutral
# — never a penalty.

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
        """
        if not results:
            return results

        signals = {}
        if self.projection is not None:
            ids = [r.get("id") for r in results if r.get("id")]
            try:
                # Per-signal dicts carry per-id sub-dicts — merge the FIELDS
                # (dict.update would REPLACE the whole value for an id,
                # dropping earlier signals).
                for part in (
                    self._fetch_load_signals(ids),
                    self._fetch_support_signals(ids),
                    self._fetch_confidence_signals(ids),
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
            copy = dict(r)
            copy["gaps_ranking"] = {
                "outgoing_impl": outgoing_impl,
                "outgoing_nand": outgoing_nand,
                "load": load,
                "incoming_impl": incoming_impl,
                "incoming_nand": incoming_nand,
                "source_count": source_count,
                "support": support,
                "confidence": round(confidence, 6),
                "confidence_source": conf_source,
                "score": round(score, 6),
            }
            if "variance" in sig:
                copy["gaps_ranking"]["variance"] = round(sig["variance"], 6)
                copy["gaps_ranking"]["contested"] = sig.get("contested", False)
            ann.append(copy)

        ann.sort(key=lambda r: r["gaps_ranking"]["score"], reverse=True)
        return ann

    # ── Signal computation ────────────────────────────────────────────

    def _confidence_of(self, result: dict, signals: dict) -> tuple[float, str]:
        """Confidence (posterior mean) + source label for one result.

        Uncalibrated points fall back to the documented neutral 0.5 — same
        convention as StateRanker (absence of measurement is NOT low support;
        the STRUCTURE is what makes a gap)."""
        has_ep = signals.get("has_ep")
        if has_ep is None:
            has_ep = bool(result.get("gaps_has_ep", False))
        if "confidence" in signals:
            conf = float(signals["confidence"])
            return conf, ("posterior" if has_ep else "neutral")
        embedded = result.get("gaps_confidence")
        if isinstance(embedded, (int, float)):
            return float(embedded), result.get("gaps_confidence_source", "posterior")
        return NEUTRAL_CONFIDENCE, "neutral"

    def _fetch_load_signals(self, ids: list[str]) -> dict[str, dict]:
        """Outgoing epistemic load: IMPL/NAND edges where the claim is the
        SOURCE — operator-mediated (idx=0) or direct to a non-operator Point.
        Operator and direct edges are counted in SEPARATE columns (chained
        OPTIONAL MATCHes cross-product; a single per-row CASE over both
        patterns would drop one of two same-type edges — P0 fix)."""
        if not ids:
            return {}
        rows = self.projection.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "OPTIONAL MATCH (op:Point {is_operator:true})-[r1:IMPL|NAND]->(n) "
            "  WHERE coalesce(r1.idx, 1) = 0 "
            "OPTIONAL MATCH (n)-[r2:IMPL|NAND]->(m:Point) "
            "  WHERE m.is_operator = false "
            "RETURN n.id, "
            "  count(DISTINCT CASE WHEN type(r1) = 'IMPL' THEN r1 END) AS op_load_impl, "
            "  count(DISTINCT CASE WHEN type(r1) = 'NAND' THEN r1 END) AS op_load_nand, "
            "  count(DISTINCT CASE WHEN type(r2) = 'IMPL' THEN r2 END) AS direct_load_impl, "
            "  count(DISTINCT CASE WHEN type(r2) = 'NAND' THEN r2 END) AS direct_load_nand",
            params={"ids": ids},
        ).result_set
        return {
            row[0]: {
                "outgoing_impl": int(row[1]) + int(row[3]),
                "outgoing_nand": int(row[2]) + int(row[4]),
            }
            for row in rows
        }

    def _fetch_support_signals(self, ids: list[str]) -> dict[str, dict]:
        """Incoming epistemic support: IMPL edges where the claim is a TARGET
        (operator-mediated idx>=1, or direct from a non-operator Point/Event),
        plus extractedFrom→Source count. Incoming NAND is counted separately
        (contention — never support)."""
        if not ids:
            return {}
        rows = self.projection.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "OPTIONAL MATCH (op:Point {is_operator:true, op_type:'IMPL'})-[r3:IMPL]->(n) "
            "  WHERE coalesce(r3.idx, 0) >= 1 "
            "OPTIONAL MATCH (s)-[r4:IMPL]->(n) "
            "  WHERE (s:Point AND s.is_operator = false) "
            "     OR (s:Event) "
            "OPTIONAL MATCH (n)-[r5:extractedFrom]->(src:Source) "
            "OPTIONAL MATCH (opn:Point {is_operator:true, op_type:'NAND'})-[r6:NAND]->(n) "
            "  WHERE coalesce(r6.idx, 1) >= 1 "
            "OPTIONAL MATCH (s2)-[r7:NAND]->(n) "
            "  WHERE (s2:Point AND s2.is_operator = false) "
            "     OR (s2:Event) "
            "RETURN n.id, "
            "  count(DISTINCT r3) AS op_support, "
            "  count(DISTINCT r4) AS direct_support, "
            "  count(DISTINCT r5) AS source_count, "
            "  count(DISTINCT r6) AS op_nand, "
            "  count(DISTINCT r7) AS direct_nand",
            params={"ids": ids},
        ).result_set
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

    def _fetch_confidence_signals(self, ids: list[str]) -> dict[str, dict]:
        """EP posterior mean + contested — same read as StateRanker."""
        if not ids:
            return {}
        rows = self.projection.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "RETURN n.id, "
            "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0) AS alpha, "
            "  coalesce(n.posterior_beta, n.ep_beta, 1.0) AS beta, "
            "  (n.posterior_alpha IS NOT NULL OR n.ep_alpha IS NOT NULL) AS has_ep",
            params={"ids": ids},
        ).result_set
        out = {}
        for row in rows:
            pid, alpha, beta, has_ep = row[0], float(row[1]), float(row[2]), bool(row[3])
            variance = _beta_variance(alpha, beta)
            out[pid] = {
                "confidence": round(alpha / (alpha + beta), 6) if (alpha + beta) > 0 else NEUTRAL_CONFIDENCE,
                "has_ep": has_ep,
                "variance": variance,
                "contested": has_ep and variance > CONTESTED_VARIANCE_THRESHOLD,
            }
        return out

    def _embedded_signals(self, results: list[dict]) -> dict[str, dict]:
        """Read gaps signals embedded in result dicts (projection=None)."""
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
SUBNODE_LABELS = (
    "Point", "Object", "Subject", "Event", "Source", "Document",
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
