"""
Epistemic topic summarization — surface settled vs contested knowledge around a topic.

#592: When searching around a topic, return not just ranked claims but an
EPISTEMIC STRUCTURE: what is significant (high confidence, strong connections —
"settled") and what is contested (elevated variance, NAND conflicts), plus the
argument structure connecting them.

This is NOT a ranking/boost feature — ranking is a byproduct. The core value is
understanding the topic landscape: the settled core, the contested zones, and
the arguments linking them.

Classification thresholds (documented, testable):
- significant/settled: confidence_mean >= 0.7 AND variance < 0.01
      (high confidence + stable posterior -> primary concerns)
- contested: variance > CONTESTED_VARIANCE_THRESHOLD (0.04)
      (destabilized posterior -> actively disputed)
- disputed pair: NAND-connected pair where BOTH have variance > 0.02
      (competing evidence destabilizing both sides)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict

from tortoise.search_engine import (
    annotate_ep_batch,
    get_relationships,
    CONTESTED_VARIANCE_THRESHOLD,
    EpBreakdown,
)

logger = logging.getLogger(__name__)

# ── Classification constants (documented, kept inline for testability) ───────

# Settled zone: confidence_mean must be at or above this.
SETTLED_CONFIDENCE_THRESHOLD = 0.7
# Settled zone: EP posterior variance must be strictly below this.
# 0.01 captures tightly converged posteriors (alpha=5,beta=2 -> v~0.01).
SETTLED_VARIANCE_THRESHOLD = 0.01
# Disputed pair: both members of a NAND-connected pair must have variance above
# this to qualify as a "disputed zone" (lower bar than global contested — a
# pair can be destabilizing each other without individually being above 0.04).
NAND_PAIR_VARIANCE_THRESHOLD = 0.02


# ── Output dataclasses ──────────────────────────────────────────────────────

@dataclass
class SettledPoint:
    """A settled/significant claim in the topic neighborhood."""
    id: str
    content: str
    point_kind: str
    confidence_mean: float
    variance: float
    impl_count: int
    nand_count: int
    contention: float


@dataclass
class ContestedPoint:
    """A contested claim in the topic neighborhood."""
    id: str
    content: str
    point_kind: str
    confidence_mean: float
    variance: float
    impl_count: int
    nand_count: int
    contention: float
    reason: str  # "variance" or "nand_pair"


@dataclass
class DisputedPair:
    """Two Points connected by a NAND operator where both have elevated variance."""
    point_a: str
    point_b: str
    variance_a: float
    variance_b: float
    operator_id: str
    mechanism: str  # "NAND"


@dataclass
class ArgumentStructure:
    """Supporting argument topology: IMPL chains and NAND conflicts."""
    impl_chains: list[dict] = field(default_factory=list)
    nand_conflicts: list[dict] = field(default_factory=list)
    relationships: dict[str, list[dict]] = field(default_factory=dict)


@dataclass
class TopicSummary:
    """Complete epistemic topic summary."""
    topic: str
    total_points: int
    significant: list[SettledPoint] = field(default_factory=list)
    contested: list[ContestedPoint] = field(default_factory=list)
    disputed_pairs: list[DisputedPair] = field(default_factory=list)
    argument_structure: ArgumentStructure = field(default_factory=ArgumentStructure)
    # Metadata for the topic neighborhood retrieval
    seed_count: int = 0
    neighborhood_hops: int = 1

    def to_dict(self) -> dict:
        """Convert to JSON-safe dict for API responses."""
        return {
            "topic": self.topic,
            "total_points": self.total_points,
            "significant": [asdict(s) for s in self.significant],
            "contested": [asdict(c) for c in self.contested],
            "disputed_pairs": [asdict(dp) for dp in self.disputed_pairs],
            "argument_structure": {
                "impl_chains": self.argument_structure.impl_chains,
                "nand_conflicts": self.argument_structure.nand_conflicts,
            },
            "meta": {
                "seed_count": self.seed_count,
                "neighborhood_hops": self.neighborhood_hops,
                "thresholds": {
                    "settled_confidence": SETTLED_CONFIDENCE_THRESHOLD,
                    "settled_variance": SETTLED_VARIANCE_THRESHOLD,
                    "contested_variance": CONTESTED_VARIANCE_THRESHOLD,
                    "nand_pair_variance": NAND_PAIR_VARIANCE_THRESHOLD,
                },
            },
        }


# ── Topic neighborhood retrieval ────────────────────────────────────────────

def _retrieve_topic_neighborhood(
    graph,
    topic: str,
    *,
    max_seeds: int = 50,
    max_hops: int = 1,
) -> tuple[list[str], list[dict], int]:
    """Retrieve Points in the topic neighborhood via about* edges + operator chains.

    Strategy (staged retrieval):
    1. Find seed Points via:
       a. Subject/Object entities whose name matches the topic -> incoming
          aboutSubject/aboutObject edges -> Points
       b. Points whose content/pointKind matches the topic via CONTAINS
    2. Expand seeds via IMPL/NAND operator chains (max_hops)

    Returns:
        (point_ids, point_meta, seed_count) — all point IDs in neighborhood,
        metadata dicts, and count of seed Points (before expansion).
    """
    seed_ids: set[str] = set()
    point_meta: list[dict] = []

    # Stage 1a: Topic -> Subjects/Objects -> about* -> Points
    for entity_label in ("Subject", "Object"):
        try:
            # Match entities by name (case-insensitive substring)
            entity_rows = graph.query(
                f"MATCH (e:{entity_label}) "
                "WHERE toLower(coalesce(e.name, '')) CONTAINS toLower($topic) "
                "RETURN e.name LIMIT $max_seeds",
                params={"topic": topic, "max_seeds": max_seeds},
            ).result_set

            for row in entity_rows:
                entity_name = row[0]
                # Follow incoming about* edges from Points
                edge_type = "aboutSubject" if entity_label == "Subject" else "aboutObject"
                point_rows = graph.query(
                    f"MATCH (p:Point)-[:{edge_type}]->(e:{entity_label} {{name: $name}}) "
                    "WHERE (p.is_operator IS NULL OR p.is_operator = false) "
                    "  AND (p.status IS NULL OR p.status <> 'retracted') "
                    "RETURN p.id, p.content, p.pointKind "
                    "LIMIT $max_seeds",
                    params={"name": entity_name, "max_seeds": max_seeds},
                ).result_set
                for pr in point_rows:
                    if pr[0] not in seed_ids:
                        seed_ids.add(pr[0])
                        point_meta.append({
                            "id": pr[0],
                            "content": (pr[1] or "")[:200],
                            "point_kind": pr[2] or "",
                        })
        except Exception:
            logger.debug("Topic neighborhood: %s entity match failed", entity_label,
                         exc_info=True)

    # Stage 1b: Direct Point content/kind match via CONTAINS (FTS fallback)
    try:
        point_rows = graph.query(
            "MATCH (p:Point) "
            "WHERE (p.is_operator IS NULL OR p.is_operator = false) "
            "  AND (p.status IS NULL OR p.status <> 'retracted') "
            "  AND (toLower(coalesce(p.content, '')) CONTAINS toLower($topic) "
            "       OR toLower(coalesce(p.pointKind, '')) CONTAINS toLower($topic)) "
            "RETURN p.id, p.content, p.pointKind "
            "LIMIT $max_seeds",
            params={"topic": topic, "max_seeds": max_seeds},
        ).result_set
        for pr in point_rows:
            if pr[0] not in seed_ids:
                seed_ids.add(pr[0])
                point_meta.append({
                    "id": pr[0],
                    "content": (pr[1] or "")[:200],
                    "point_kind": pr[2] or "",
                })
    except Exception:
        logger.debug("Topic neighborhood: Point content match failed", exc_info=True)

    all_ids = set(seed_ids)
    seed_count = len(seed_ids)

    # Stage 2: Expand via operator chains (IMPL/NAND) up to max_hops
    if max_hops > 0 and all_ids:
        try:
            frontier: set[str] = set(seed_ids)  # only expand from newly discovered IDs
            for _hop in range(max_hops):
                new_ids: set[str] = set()
                current_ids = list(frontier)
                frontier = set()
                # Process in batches of 100 to avoid massive Cypher strings
                for batch_start in range(0, len(current_ids), 100):
                    batch = current_ids[batch_start:batch_start + 100]
                    rows = graph.query(
                        "MATCH (n:Point)<-[r1:IMPL|NAND]-(op:Point {is_operator:true})"
                        "-[r2:IMPL|NAND]->(m:Point) "
                        "WHERE n.id IN $ids AND m.id <> n.id "
                        "  AND (n.status IS NULL OR n.status <> 'retracted') "
                        "  AND (m.status IS NULL OR m.status <> 'retracted') "
                        "  AND (m.is_operator IS NULL OR m.is_operator = false) "
                        "RETURN DISTINCT m.id, m.content, m.pointKind",
                        params={"ids": batch},
                    ).result_set
                    for row in rows:
                        if row[0] not in all_ids:
                            new_ids.add(row[0])
                            frontier.add(row[0])
                            point_meta.append({
                                "id": row[0],
                                "content": (row[1] or "")[:200],
                                "point_kind": row[2] or "",
                            })
                if not new_ids:
                    break
                all_ids |= new_ids
        except Exception:
            logger.debug("Topic neighborhood: operator chain expansion failed",
                         exc_info=True)

    return list(all_ids), point_meta, seed_count


# ── Classification ──────────────────────────────────────────────────────────

def _classify_points(
    breakdowns: dict[str, EpBreakdown],
    relationships: dict[str, list[dict]],
    point_meta: list[dict],
) -> tuple[list[SettledPoint], list[ContestedPoint], list[DisputedPair]]:
    """Classify points into settled, contested, and disputed pairs.

    Classification rules (documented, testable):
    - Settled: confidence_mean >= 0.7 AND variance < 0.01
    - Contested: variance > CONTESTED_VARIANCE_THRESHOLD (0.04)
    - Disputed pair: NAND-connected pair where both have variance > 0.02
    """
    # Build lookup: point ID -> content/kind
    meta_lookup: dict[str, dict] = {
        sp["id"]: sp for sp in point_meta if sp.get("id")
    }

    significant: list[SettledPoint] = []
    contested: list[ContestedPoint] = []

    for pid, ep in breakdowns.items():
        meta_info = meta_lookup.get(pid, {})
        content = meta_info.get("content", "")
        point_kind = meta_info.get("point_kind", "")
        evidence = ep.evidence

        impl_count = evidence.impl_count if evidence else 0
        nand_count = evidence.nand_count if evidence else 0

        # Settled: high confidence + tight posterior variance
        if (ep.confidence_mean >= SETTLED_CONFIDENCE_THRESHOLD
                and ep.variance < SETTLED_VARIANCE_THRESHOLD):
            significant.append(SettledPoint(
                id=pid,
                content=content,
                point_kind=point_kind,
                confidence_mean=round(ep.confidence_mean, 4),
                variance=round(ep.variance, 6),
                impl_count=impl_count,
                nand_count=nand_count,
                contention=round(ep.contention, 4),
            ))

        # Contested: elevated posterior variance
        if ep.contested:
            contested.append(ContestedPoint(
                id=pid,
                content=content,
                point_kind=point_kind,
                confidence_mean=round(ep.confidence_mean, 4),
                variance=round(ep.variance, 6),
                impl_count=impl_count,
                nand_count=nand_count,
                contention=round(ep.contention, 4),
                reason="variance",
            ))

    # Detect disputed NAND pairs: NAND-connected where both have variance > 0.02
    disputed_pairs: list[DisputedPair] = []
    seen_pairs: set[tuple[str, str]] = set()

    for pid, rel_list in relationships.items():
        for rel in rel_list:
            if rel.get("mechanism") != "NAND":
                continue
            related_id = rel.get("related_id", "")
            if not related_id:
                continue

            # Canonical pair key (sorted to deduplicate)
            pair_key = tuple(sorted([pid, related_id]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            ep_a = breakdowns.get(pid)
            ep_b = breakdowns.get(related_id)
            if ep_a is None or ep_b is None:
                continue

            # Gate on has_ep: uncalibrated points (no persisted ep_alpha/ep_beta)
            # fall back to Beta(1,1) → variance 0.0833, which would falsely
            # classify every NAND-connected pair as disputed.
            if not ep_a.has_ep or not ep_b.has_ep:
                continue

            var_a = ep_a.variance
            var_b = ep_b.variance

            if (var_a > NAND_PAIR_VARIANCE_THRESHOLD
                    and var_b > NAND_PAIR_VARIANCE_THRESHOLD):
                disputed_pairs.append(DisputedPair(
                    point_a=pid,
                    point_b=related_id,
                    variance_a=round(var_a, 6),
                    variance_b=round(var_b, 6),
                    operator_id=rel.get("operator_id", ""),
                    mechanism="NAND",
                ))

                # Mark both sides as contested (NAND-pair reason) if not already
                contested_ids = {c.id for c in contested}
                for side_pid in (pid, related_id):
                    if side_pid not in contested_ids:
                        side_ep = breakdowns.get(side_pid)
                        if side_ep:
                            side_info = meta_lookup.get(side_pid, {})
                            side_ev = side_ep.evidence
                            contested.append(ContestedPoint(
                                id=side_pid,
                                content=side_info.get("content", ""),
                                point_kind=side_info.get("point_kind", ""),
                                confidence_mean=round(side_ep.confidence_mean, 4),
                                variance=round(side_ep.variance, 6),
                                impl_count=side_ev.impl_count if side_ev else 0,
                                nand_count=side_ev.nand_count if side_ev else 0,
                                contention=round(side_ep.contention, 4),
                                reason="nand_pair",
                            ))

    return significant, contested, disputed_pairs


def _build_argument_structure(
    relationships: dict[str, list[dict]],
    breakdowns: dict[str, EpBreakdown],
) -> ArgumentStructure:
    """Build supporting argument topology from operator-edge relationships.

    Extracts IMPL chains (support chains) and NAND conflicts, annotated with
    EP confidence at each link.
    """
    impl_chains: list[dict] = []
    nand_conflicts: list[dict] = []

    for pid, rel_list in relationships.items():
        for rel in rel_list:
            mechanism = rel.get("mechanism", "")
            related_id = rel.get("related_id", "")
            ep_self = breakdowns.get(pid)
            ep_other = breakdowns.get(related_id)

            # Determine direction: relationship already has 'direction' field
            edge_info = {
                "source_id": pid if rel.get("direction") == "outgoing" else related_id,
                "target_id": related_id if rel.get("direction") == "outgoing" else pid,
                "mechanism": mechanism,
                "predicate": rel.get("predicate", ""),
                "operator_id": rel.get("operator_id", ""),
                "source_confidence": round(ep_self.confidence_mean, 4) if ep_self else None,
                "target_confidence": round(ep_other.confidence_mean, 4) if ep_other else None,
                "source_variance": round(ep_self.variance, 6) if ep_self else None,
                "target_variance": round(ep_other.variance, 6) if ep_other else None,
            }

            if mechanism == "IMPL":
                impl_chains.append(edge_info)
            elif mechanism == "NAND":
                nand_conflicts.append(edge_info)

    return ArgumentStructure(
        impl_chains=impl_chains,
        nand_conflicts=nand_conflicts,
        relationships=relationships,
    )


# ── Main entry point ────────────────────────────────────────────────────────

def topic_summarize(
    graph,
    topic: str,
    *,
    max_seeds: int = 50,
    max_hops: int = 1,
    include_relationships: bool = True,
) -> TopicSummary:
    """Summarize epistemic structure around a topic.

    Retrieves the topic neighborhood -> computes EP confidence per Point ->
    classifies each as significant/settled or contested -> detects disputed
    NAND pairs -> builds argument structure.

    Args:
        graph: FalkorDB graph connection.
        topic: Topic string (e.g. "pricing", "architecture", "security").
        max_seeds: Max seed Points to retrieve (Stage 1).
        max_hops: Operator-chain expansion depth (0 = seeds only).
        include_relationships: Whether to fetch relationship topology.

    Returns:
        TopicSummary with significant, contested, disputed_pairs, and
        argument_structure.
    """
    # 1. Retrieve topic neighborhood
    point_ids, point_meta, seed_count = _retrieve_topic_neighborhood(
        graph, topic, max_seeds=max_seeds, max_hops=max_hops,
    )

    if not point_ids:
        return TopicSummary(topic=topic, total_points=0)

    # 2. Compute EP confidence per Point
    breakdowns = annotate_ep_batch(graph, point_ids)

    # 3. Fetch operator-edge relationships (argument topology)
    relationships: dict[str, list[dict]] = {}
    if include_relationships:
        relationships = get_relationships(graph, point_ids)

    # 4. Classify settled / contested / disputed pairs
    significant, contested, disputed_pairs = _classify_points(
        breakdowns, relationships, point_meta,
    )

    # 5. Build argument structure
    argument_structure = _build_argument_structure(relationships, breakdowns)

    return TopicSummary(
        topic=topic,
        total_points=len(point_ids),
        significant=significant,
        contested=contested,
        disputed_pairs=disputed_pairs,
        argument_structure=argument_structure,
        seed_count=seed_count,
        neighborhood_hops=max_hops,
    )
