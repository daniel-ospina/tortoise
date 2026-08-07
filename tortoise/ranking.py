"""Graph-informed ranking for Tortoise search results (#25).

Search results are ranked by text similarity alone (RRF fusion of FTS /
vector / structural). This module adds Tortoise's structural differentiator —
graph topology — as a composable ranking signal:

    final_score = α · normalize(similarity) + β · graph_boost + γ · recency_decay

* graph_boost for Points uses the persisted EP confidence (``n.confidence``,
  written by ``compute_confidence``) and operator connectivity (incident
  IMPL/NAND edge count).
* graph_boost for Events/Sessions uses INSTANTIATES edge count (Objects
  produced = reusable knowledge) plus the mean EP confidence of Points the
  session produced, per docs/scoping-7769-graph-informed-ranking.md.
* recency_decay is exponential with a configurable half-life (default 30 days).

Design notes (from the scoping doc):
- GraphRanker is a standalone module, unit-testable without a live graph
  (``projection=None`` → only signals embedded in the result dicts are used).
- Weights are configurable at construction; defaults α=0.5, β=0.35, γ=0.15.
- Missing signals degrade to neutral, never to a penalty — uncalibrated
  Points get a graph boost of 0.0 and missing timestamps get neutral recency.
- INSTANTIATES / PRODUCES edges may be absent on older graphs; the queries
  are OPTIONAL MATCH so the boost degrades to 0.0 gracefully.
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
    ``graph_degree``, ``graph_instantiates``, ``createdAt``/``startedAt``).
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
        Events/Sessions: 0.6·INSTANTIATES count (Objects produced) +
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
            instantiates = signals.get("instantiates", 0)
            if instantiates > 0 or signals.get("is_event"):
                inst_norm = 1.0 - 1.0 / (1.0 + instantiates)
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
            "  coalesce(n.ep_alpha, 1.0) AS alpha, coalesce(n.ep_beta, 1.0) AS beta, "
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
            "OPTIONAL MATCH (e)-[:INSTANTIATES]->(o:Object) "
            "WITH e, count(o) AS instantiates "
            "OPTIONAL MATCH (e)-[:PRODUCES]->(p:Point) "
            "WITH e, instantiates, avg(p.confidence) AS avg_conf "
            "RETURN e.eventId, coalesce(avg_conf, 0.5) AS conf, "
            "       instantiates, e.startedAt AS created"
        )
        rows = self.projection.g.query(cypher, params={"ids": ids}).result_set
        out = {}
        for row in rows:
            eid = row[0]
            out[eid] = {
                "confidence": float(row[1]),
                "instantiates": int(row[2]),
                "created": row[3],
                "is_event": True,
            }
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
