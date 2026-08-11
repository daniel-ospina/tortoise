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
            connectivity = 1.0 - 1.0 / (1.0 + (degree or 0))
            return round(0.5 * confidence + 0.5 * connectivity, 4)
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
