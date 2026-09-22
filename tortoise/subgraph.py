"""#3011 Track A — epistemic-subgraph traversal + ranking engine.

Implements the frozen Arm B/C construction parameters of
``docs/experiments/2026-09-11-abc-context-assembly-experiment.md`` §3
("Arm B/C construction"), as frozen by
``docs/plans/2026-09-11-3011-context-assembly-impl.md`` §1.1. Where the spec
and the plan disagree, the spec wins.

⛔ Hard leakage rule (§3 labeling rule / §10 static-reference assertion).
Seeds are selected from the **question text only**. This module never reads
any gold annotation: neither the per-turn answer mark, the gold answer-session
list, the ingest session index, nor the pre-registered gold-evidence claim
artifact. ``tests/test_subgraph_pure.py`` asserts this both statically (a
source scan for the four forbidden tokens, which therefore never appear
anywhere in this file) and at run time (every issued Cypher statement and its
params are inspected).

Graph reality this engine is written against:

* claims are ``:Point`` nodes carrying a ``content`` property;
* operator nodes are **also** ``:Point`` (``is_operator: true``) and carry
  **no** ``content`` — they are traversed *through*, never returned as
  claims (every traversal filters ``other.is_operator = false``);
* ``IMPL``/``NAND`` edges run operator→BOTH endpoints, ``r.idx == 0`` is the
  source and ``idx > 0`` the targets — a bare edge walk returns operator
  nodes, so the *other* endpoint is always the claim;
* supersession is the ``CORRECTS`` edge, ``(new:Point)-[:CORRECTS]->(old:Point)``.
  There is **no** ``superseded_by`` property — it is derived by following
  ``CORRECTS``;
* the entity link is ``(p:Point)-[:aboutObject]->(o:Object)`` and the display
  text is ``o.name``.

Serializer seam (additive — not part of the frozen §1.1 shapes).
``Subgraph.point_props`` maps every admitted point id (anchors and admitted
candidates) to that point's **raw** graph properties: ``id``, ``content``,
``is_operator``, ``session_id``, ``createdAt``, ``validFrom``,
``source_turn_id``, ``posterior_alpha``, ``posterior_beta``, ``ep_alpha``,
``ep_beta`` and ``status``. Track B reads it to derive the rendered session
number (``session_id`` → position in the frozen haystack list), the date
(``validFrom`` → ``createdAt``), the turn ordinal (``source_turn_id``) and the
EP posterior (``read_confidence``) with no second graph handle. Properties the
graph does not carry are **omitted**, never invented and never materialized as
``None``. The map is filled by the same Cypher statements that already fetch
anchors and candidates — the properties ride along in their ``RETURN`` clauses,
so no per-point query is issued — and it only ever holds points the traversal
actually admitted. ``content_by_id`` remains the text channel; ``point_props``
is the metadata channel; neither carries a gold annotation.

Frozen operation order: **cap → dedupe**. Per-anchor cap of 12 is applied
*after* the reserved supersession/NAND endpoints are admitted (reserved
claims never consume a cap slot); dedupe then runs once, globally, by point
``id``, keeping the highest score (score decides; edge priority only breaks
an exact tie). Because dedupe runs after the cap, a point deduped out of one
anchor's block does not free a cap slot in any other anchor's block. There
is no separate global candidate cap; the §5 word budget (Track B) is the
only global bound.
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

__all__ = [
    "EDGE_PRIORITY_WEIGHT",
    "HOP_DECAY",
    "PER_ANCHOR_CAP",
    "SEED_COUNT",
    "SEED_LIMIT",
    "Candidate",
    "Relation",
    "Subgraph",
    "build_subgraph",
    "build_subgraph_from_seeds",
    "normalize_seeds",
    "rank_score",
    "select_seeds",
]

# ── Frozen constants (spec §3 "Arm B/C construction") ─────────────────────

#: Admission-order weight per edge class (spec §3 "Edge priority").
EDGE_PRIORITY_WEIGHT: dict[str, float] = {
    "aboutObject": 1.0,
    "supersession": 0.9,
    "NAND": 0.8,
    "IMPL": 0.7,
}

#: Hop decay (spec §3 "Ranking").
HOP_DECAY: dict[int, float] = {1: 1.0, 2: 0.5}

#: ``vector_search``/BM25 fetch width — the frozen tie-break must see the
#: full tied set (a width of exactly 8 would hide the tied candidates the
#: boundary tie-break exists to order).
SEED_LIMIT = 64

#: Selected seeds per question.
SEED_COUNT = 8

#: Per-anchor candidate cap, counted AFTER the reserved endpoints are admitted.
PER_ANCHOR_CAP = 12

#: Reserved edge classes — supersession/validity and NAND endpoints are
#: admitted ahead of the 12-per-anchor cap and never consume a cap slot.
_RESERVED_EDGES = frozenset({"supersession", "NAND"})

#: Edge-priority tie-break rank (higher = higher priority). Only used when two
#: occurrences of the SAME point carry an *identical* score.
_EDGE_PRIORITY = {"aboutObject": 4, "supersession": 3, "NAND": 2, "IMPL": 1}


# ── Frozen data shapes (plan §1.1) ────────────────────────────────────────


@dataclass(frozen=True)
class Candidate:
    """One point reached from an anchor during traversal (pre/post-dedupe)."""

    point_id: str
    content: str
    anchor_id: str
    edge_type: str  # aboutObject | supersession | NAND | IMPL
    hop: int  # 1 | 2
    s_norm: float  # min-max over the 8 SELECTED seeds
    raw_score: float  # s_norm * edge weight * hop decay (pre-damp)
    score: float  # post-damp (authoritative for admission)
    damped: bool
    reserved: bool  # supersession/validity or NAND endpoint


@dataclass(frozen=True)
class Relation:
    """A typed, directed relation discovered during traversal."""

    source_id: str
    relation: str  # aboutObject | supersession | NAND | IMPL
    target_id: str
    target_label: str  # entity name for aboutObject, else ""


@dataclass
class Subgraph:
    """The traversal + ranking result handed to the serializer (Track B)."""

    seeds: tuple[tuple[str, float], ...]  # SELECTED seeds, rank order (raw fetch scores)
    anchors: tuple[str, ...]  # ordered anchors (seed order, operators dropped)
    candidates: tuple[Candidate, ...]  # post cap->dedupe, admission order
    relations: tuple[Relation, ...]
    zero_seed: bool
    reserved_overflow: int  # count of dropped reserved lines (budget, Track B)
    seed_fn: str  # "vector" | "bm25"
    # Additive extension (not part of the frozen §1.1 dataclass): claim text
    # keyed by point id for every anchor and admitted candidate, so the
    # renderer can emit ``C1: <claim text>`` without a graph handle. Track B
    # consumes it; Track A never requires it to be populated for the frozen
    # ranking rules to hold.
    content_by_id: dict[str, str] = field(default_factory=dict)
    # Additive extension (not part of the frozen §1.1 dataclass): raw graph
    # properties keyed by point id, for every admitted anchor and candidate.
    # Track B reads ``session_id``/``createdAt``/``validFrom``/
    # ``source_turn_id``/the EP posteriors from here to render provenance and
    # confidence. Populated by the traversal's existing Cypher statements (the
    # properties ride along in their RETURN clauses); absent properties are
    # omitted rather than set to None. Defaults to empty so callers that had
    # no use for it (and the zero-seed paths) construct a Subgraph unchanged.
    point_props: dict[str, dict] = field(default_factory=dict)


# ── Pure seed selection + normalization (spec §3 "Seed policy") ───────────


def normalize_seeds(
    seeds: list[tuple[str, float]],
) -> list[tuple[str, float]]:
    """Min–max normalize seed scores to [0, 1] (spec §3 "Score normalization").

    Normalization is over the *selected* seed set only. When every selected
    seed score is equal, every ``s_norm`` is ``1.0`` (the frozen all-equal
    rule — never a division by zero).
    """
    if not seeds:
        return []
    values = [float(score) for _, score in seeds]
    lo = min(values)
    hi = max(values)
    if hi == lo:
        return [(pid, 1.0) for pid, _ in seeds]
    span = hi - lo
    return [(pid, (float(score) - lo) / span) for pid, score in seeds]


def select_seeds(
    candidates: list[tuple[str, float]], n: int = SEED_COUNT
) -> list[tuple[str, float]]:
    """Rank → dedupe by point id → take ``n``, with the frozen tie-break.

    Rank by raw score (descending); dedupe by point ``id`` keeping the
    highest score; take the top ``n``. When raw scores tie at the ``n``
    boundary, **every** returned candidate carrying the tied boundary score
    is ordered by ascending point ``id`` (byte-wise) and the first is taken —
    never a gold field, never random.
    """
    if n <= 0 or not candidates:
        return []

    best: dict[str, float] = {}
    for pid, score in candidates:
        pid = str(pid)
        score = float(score)
        if pid not in best or score > best[pid]:
            best[pid] = score

    ranked = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(ranked) <= n:
        return ranked

    boundary = ranked[n - 1][1]
    above = [entry for entry in ranked if entry[1] > boundary]
    tied = sorted(
        (entry for entry in ranked if entry[1] == boundary),
        key=lambda kv: kv[0],  # byte-wise ascending point id
    )
    return above + tied[: n - len(above)]


# ── Pure ranking (spec §3 "Ranking" / "Hub damping") ──────────────────────


def rank_score(
    s_norm: float,
    edge_type: str,
    hop: int,
    *,
    hub_degree: int | None,
) -> float:
    """``seed_similarity × edge_priority_weight × hop_decay``, hub-damped.

    Hub damping applies to **2-hop (hub-mediated) candidates only**:
    ``score /= 1 + ln(1 + degree)`` with the natural logarithm, where
    ``degree`` is the hub entity's *whole-graph* incident-edge count at
    render time. Direct 1-hop candidates are never damped, so ``hub_degree``
    is ignored for them.
    """
    try:
        weight = EDGE_PRIORITY_WEIGHT[edge_type]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(f"unknown edge_type {edge_type!r}") from exc
    try:
        decay = HOP_DECAY[hop]
    except KeyError as exc:
        raise ValueError(f"unknown hop {hop!r} (expected 1 or 2)") from exc

    raw = float(s_norm) * weight * decay
    if hop == 2:
        if hub_degree is None:
            raise ValueError("hub_degree is required for hub-mediated (2-hop) candidates")
        return raw / (1.0 + math.log(1.0 + max(int(hub_degree), 0)))
    return raw


# ── Traversal Cypher (the query shape mirrors search_engine.py) ───────────

#: Raw point properties carried into ``Subgraph.point_props``, in projection
#: order. Every statement below returns this exact block (aliased per query)
#: so one parser handles them all. ``id``/``content``/``is_operator`` lead
#: because callers already relied on those columns; a property the graph does
#: not carry comes back ``None`` and is omitted by :func:`_props`.
_POINT_PROP_KEYS: tuple[str, ...] = (
    "id",
    "content",
    "is_operator",
    "session_id",
    "createdAt",
    "validFrom",
    "source_turn_id",
    "posterior_alpha",
    "posterior_beta",
    "ep_alpha",
    "ep_beta",
    "status",
)


def _prop_projection(alias: str, prefix: str) -> str:
    """``alias.key AS prefixkey`` for every carried property, in fixed order."""
    return ", ".join(f"{alias}.{key} AS {prefix}{key}" for key in _POINT_PROP_KEYS)


_CYPHER_POINT_META = f"MATCH (p:Point) WHERE p.id IN $ids RETURN {_prop_projection('p', 'p_')}"

# 1-hop IMPL/NAND through the operator node: op→BOTH endpoints, idx=0 source.
# 1-hop IMPL/NAND through the operator node: op→BOTH endpoints, idx=0 source.
# Shape mirrors search_engine.get_relationships (two MATCH clauses, never a
# bare operator walk — the other endpoint is always the claim).
_CYPHER_OPS = (
    "MATCH (n:Point) WHERE n.id = $id "
    "MATCH (n)-[r:IMPL|NAND]-(op:Point {is_operator:true}) "
    "MATCH (op)-[r2:IMPL|NAND]-(other:Point) "
    "WHERE other.id <> n.id AND other.is_operator = false "
    "RETURN type(r2) AS edge_type, r.idx AS n_idx, r2.idx AS other_idx, "
    "  op.id AS op_id, "
    f"{_prop_projection('other', 'other_')}"
)

# 1-hop aboutObject entity link (the hub itself is never a candidate).
_CYPHER_ABOUT = (
    "MATCH (n:Point) WHERE n.id = $id "
    "MATCH (n)-[:aboutObject]->(o:Object) "
    "RETURN o.id AS hub_id, o.name AS hub_name"
)

# The ONLY permitted 2nd hop: seed → aboutObject hub → sibling claim.
_CYPHER_SIBLINGS = (
    "MATCH (n:Point) WHERE n.id = $id "
    "MATCH (n)-[:aboutObject]->(o:Object) "
    "MATCH (o)<-[:aboutObject]-(sib:Point) "
    "WHERE sib.id <> n.id AND sib.is_operator = false "
    "RETURN o.id AS hub_id, o.name AS hub_name, "
    f"{_prop_projection('sib', 'sib_')}"
)

# Whole-graph incident-edge count of a hub entity (not subgraph degree).
_CYPHER_HUB_DEGREE = (
    "MATCH (o:Object) WHERE o.id IN $ids "
    "MATCH (o)-[r]-() "
    "RETURN o.id AS hub_id, count(r) AS hub_degree"
)

# Supersession is CORRECTS: (new)-[:CORRECTS]->(old); there is no
# superseded_by property, so BOTH directions are walked.
_CYPHER_CORRECTS_OUT = (
    "MATCH (n:Point) WHERE n.id = $id "
    "MATCH (n)-[:CORRECTS]->(other:Point) "
    "WHERE other.is_operator = false "
    f"RETURN {_prop_projection('other', 'other_')}"
)
_CYPHER_CORRECTS_IN = (
    "MATCH (n:Point) WHERE n.id = $id "
    "MATCH (other:Point)-[:CORRECTS]->(n) "
    "WHERE other.is_operator = false "
    f"RETURN {_prop_projection('other', 'other_')}"
)


def _q(graph: Any, cypher: str, params: dict) -> list:
    """Issue one graph query and return ``result_set`` rows (never ``None``)."""
    result = graph.query(cypher, params=params)
    return list(getattr(result, "result_set", None) or [])


def _row(row: Any, width: int) -> list:
    """Coerce a result row to a fixed-width list (missing columns → None)."""
    values = list(row)
    if len(values) < width:
        values.extend([None] * (width - len(values)))
    return values[:width]


def _props(values: Sequence[Any]) -> dict[str, Any]:
    """Raw point properties from a projection-order value slice.

    Absent properties (``None``) are **omitted**, never materialized as a
    ``None`` value: a claim with no stored ``session_id`` carries no
    ``session_id`` key at all.
    """
    out: dict[str, Any] = {}
    for key, value in zip(_POINT_PROP_KEYS, values, strict=False):
        if value is None:
            continue
        out[key] = value
    return out


def _norm_edge_type(value: Any) -> str | None:
    """Map a Cypher relationship type to the frozen edge class (or None)."""
    token = str(value or "").upper()
    if token in ("IMPL", "IMPLIES"):
        return "IMPL"
    if token in ("NAND", "CONTRADICTS"):
        return "NAND"
    return None


def _occurrence(
    point_id: Any,
    content: Any,
    anchor_id: str,
    edge_type: str,
    hop: int,
    s_norm: float,
    hub_degree: int | None,
) -> Candidate:
    """Build one traversal occurrence with raw + post-damp scores."""
    raw_score = float(s_norm) * EDGE_PRIORITY_WEIGHT[edge_type] * HOP_DECAY[hop]
    score = rank_score(s_norm, edge_type, hop, hub_degree=hub_degree)
    return Candidate(
        point_id=str(point_id),
        content=content or "",
        anchor_id=anchor_id,
        edge_type=edge_type,
        hop=hop,
        s_norm=float(s_norm),
        raw_score=raw_score,
        score=score,
        damped=(hop == 2),
        reserved=(edge_type in _RESERVED_EDGES),
    )


def _collect_raw(graph: Any, anchor_id: str) -> dict[str, list]:
    """All 1-hop rows for one anchor (plus the aboutObject hub links)."""
    return {
        "ops": _q(graph, _CYPHER_OPS, {"id": anchor_id}),
        "about": _q(graph, _CYPHER_ABOUT, {"id": anchor_id}),
        "siblings": _q(graph, _CYPHER_SIBLINGS, {"id": anchor_id}),
        "corrects_out": _q(graph, _CYPHER_CORRECTS_OUT, {"id": anchor_id}),
        "corrects_in": _q(graph, _CYPHER_CORRECTS_IN, {"id": anchor_id}),
    }


def _build_anchor(
    anchor_id: str,
    s_norm: float,
    raw: dict[str, list],
    hub_degrees: dict[str, int],
) -> tuple[list[Candidate], list[Relation], dict[str, dict]]:
    """Turn one anchor's raw rows into candidate occurrences, relations, props."""
    occs: list[Candidate] = []
    relations: list[Relation] = []
    props_by_id: dict[str, dict] = {}

    # 1-hop IMPL / NAND (traversed through the operator node).
    prop_width = len(_POINT_PROP_KEYS)
    for row in raw["ops"]:
        values = _row(row, 4 + prop_width)
        et_raw, n_idx, other_idx, _op_id = values[:4]
        other_id = values[4]
        other_content = values[5]
        edge_type = _norm_edge_type(et_raw)
        if edge_type is None or other_id is None:
            continue
        props_by_id.setdefault(str(other_id), _props(values[4:]))
        # Frozen edge semantics: ``r.idx == 0`` is the SOURCE and every
        # ``idx > 0`` a TARGET, with real edges ``source → target``.
        # Direction therefore needs BOTH indices, not ``n_idx`` alone. When
        # the anchor is a target (``n_idx > 0``) and the other endpoint is
        # the source (``other_idx == 0``) the edge runs other → anchor — the
        # mirror of the anchor-as-source case. When BOTH are targets the two
        # endpoints are siblings under the operator and **no edge exists
        # between them**, so neither a relation nor a candidate occurrence
        # may be emitted (deriving direction from ``n_idx`` alone fabricated
        # ``T2 IMPLIES T1`` for a 3-endpoint operator).
        if n_idx == 0:
            relations.append(Relation(anchor_id, edge_type, str(other_id), ""))
        elif other_idx == 0:
            relations.append(Relation(str(other_id), edge_type, anchor_id, ""))
        else:
            continue
        occs.append(_occurrence(other_id, other_content, anchor_id, edge_type, 1, s_norm, None))

    # 1-hop aboutObject — the entity is a hub, never a candidate claim.
    for row in raw["about"]:
        hub_id, hub_name = _row(row, 2)
        if hub_id is None:
            continue
        relations.append(Relation(anchor_id, "aboutObject", str(hub_id), hub_name or ""))

    # 2-hop ONLY through an aboutObject hub → sibling claim.
    for row in raw["siblings"]:
        values = _row(row, 2 + prop_width)
        hub_id, hub_name = values[:2]
        sib_id = values[2]
        sib_content = values[3]
        if hub_id is None or sib_id is None:
            continue
        props_by_id.setdefault(str(sib_id), _props(values[2:]))
        relations.append(Relation(anchor_id, "aboutObject", str(hub_id), hub_name or ""))
        relations.append(Relation(str(sib_id), "aboutObject", str(hub_id), hub_name or ""))
        occs.append(
            _occurrence(
                sib_id,
                sib_content,
                anchor_id,
                "aboutObject",
                2,
                s_norm,
                hub_degrees.get(str(hub_id), 0),
            )
        )

    # 1-hop supersession (CORRECTS, both directions).
    for row in raw["corrects_out"]:
        values = _row(row, prop_width)
        other_id = values[0]
        other_content = values[1]
        if other_id is None:
            continue
        props_by_id.setdefault(str(other_id), _props(values))
        relations.append(Relation(anchor_id, "supersession", str(other_id), ""))
        occs.append(
            _occurrence(
                other_id,
                other_content,
                anchor_id,
                "supersession",
                1,
                s_norm,
                None,
            )
        )
    for row in raw["corrects_in"]:
        values = _row(row, prop_width)
        other_id = values[0]
        other_content = values[1]
        if other_id is None:
            continue
        props_by_id.setdefault(str(other_id), _props(values))
        relations.append(Relation(str(other_id), "supersession", anchor_id, ""))
        occs.append(
            _occurrence(
                other_id,
                other_content,
                anchor_id,
                "supersession",
                1,
                s_norm,
                None,
            )
        )

    return occs, relations, props_by_id


def _better_occurrence(a: Candidate, b: Candidate) -> bool:
    """True when occurrence ``a`` beats ``b`` for the SAME point id.

    Score decides; edge priority only breaks an exact score tie; a shorter
    hop wins a perfect tie. (The admission tie-break, lower point id first,
    is unchanged and applies across *distinct* points.)
    """
    if a.score != b.score:
        return a.score > b.score
    pa = _EDGE_PRIORITY.get(a.edge_type, 0)
    pb = _EDGE_PRIORITY.get(b.edge_type, 0)
    if pa != pb:
        return pa > pb
    return a.hop < b.hop


def _network_rank(c: Candidate) -> int:
    """In-network (0) before out-of-network (1).

    In-network = reached via a typed epistemic edge (IMPL/NAND/supersession)
    — PPR-like reachability. Out-of-network = reached only through an
    ``aboutObject`` co-mention hub (the 2-hop sibling). This is the only
    distinction available in this graph: the sole permitted 2nd hop is the
    entity hub, and entities are never candidates, so hop-1 candidates are
    exactly the typed-edge candidates.
    """
    return 1 if c.edge_type == "aboutObject" else 0


def _admission_key(c: Candidate) -> tuple[int, float, str]:
    """Greedy admission key: in-network first, then score desc, then id asc."""
    return (_network_rank(c), -c.score, c.point_id)


def _collapse_anchor(occs: list[Candidate], *, cap: int = PER_ANCHOR_CAP) -> list[Candidate]:
    """Per-anchor resolve → reserved-first → cap (frozen cap → dedupe order).

    Reserved endpoints are admitted ahead of the cap and never consume a cap
    slot. A point reached via both a reserved and a normal edge is reserved.
    """
    by_id: dict[str, Candidate] = {}
    reserved_ids: set[str] = set()
    for cand in occs:
        current = by_id.get(cand.point_id)
        if current is None or _better_occurrence(cand, current):
            by_id[cand.point_id] = cand
        if cand.reserved:
            reserved_ids.add(cand.point_id)

    reps: list[Candidate] = []
    for pid, cand in by_id.items():
        if pid in reserved_ids and not cand.reserved:
            cand = replace(cand, reserved=True)
        reps.append(cand)

    reserved = sorted((c for c in reps if c.reserved), key=_admission_key)
    normal = sorted((c for c in reps if not c.reserved), key=_admission_key)
    return reserved + normal[:cap]


def _dedupe_global(per_anchor: list[list[Candidate]]) -> list[Candidate]:
    """One global dedupe by point id, keeping the highest-scoring occurrence.

    Iteration is anchor order, so an exact score+priority+hop tie keeps the
    occurrence from the earlier anchor (deterministic).
    """
    best: dict[str, Candidate] = {}
    for cands in per_anchor:
        for cand in cands:
            current = best.get(cand.point_id)
            if current is None or _better_occurrence(cand, current):
                best[cand.point_id] = cand
    return list(best.values())


def _relation_kept(rel: Relation, admitted: set[str]) -> bool:
    """Drop relations whose endpoints are not in the admitted claim set.

    ``aboutObject`` targets are entities, not claims — only the claim-side
    endpoint must be admitted.
    """
    if rel.source_id not in admitted:
        return False
    if rel.relation == "aboutObject":
        return True
    return rel.target_id in admitted


def _dedupe_relations(relations: list[Relation]) -> tuple[Relation, ...]:
    """A ``(source, relation, target)`` line appears at most once globally."""
    seen: set[tuple[str, str, str]] = set()
    out: list[Relation] = []
    for rel in relations:
        key = (rel.source_id, rel.relation, rel.target_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(rel)
    return tuple(out)


def _anchor_meta(graph: Any, ids: list[str]) -> dict[str, dict]:
    """``id → raw point properties`` for the seed set (operators filtered)."""
    if not ids:
        return {}
    out: dict[str, dict] = {}
    width = len(_POINT_PROP_KEYS)
    for row in _q(graph, _CYPHER_POINT_META, {"ids": list(ids)}):
        props = _props(_row(row, width))
        pid = props.get("id")
        if pid is None or str(pid) in out:
            continue
        out[str(pid)] = props
    return out


def _hub_degrees(graph: Any, hub_ids: set[str]) -> dict[str, int]:
    """Whole-graph incident-edge count per hub entity (never subgraph degree)."""
    if not hub_ids:
        return {}
    out: dict[str, int] = {}
    for row in _q(graph, _CYPHER_HUB_DEGREE, {"ids": list(hub_ids)}):
        hub_id, degree = _row(row, 2)
        if hub_id is None:
            continue
        with contextlib.suppress(TypeError, ValueError):
            out[str(hub_id)] = int(degree)
    return out


# ── Public entry points ───────────────────────────────────────────────────


def build_subgraph_from_seeds(
    graph: Any,
    seeds: list[tuple[str, float]],
    *,
    seed_fn: str = "vector",
) -> Subgraph:
    """Traverse + rank from an already-fetched seed set.

    This is the hermetic seam: it takes a plain graph handle exposing
    ``query(cypher, params).result_set`` (see ``tests/test_subgraph_pure.py``)
    and the raw seed ``(id, score)`` tuples, and performs the frozen
    selection → normalization → 1-hop (+ hub-mediated 2nd hop) expansion →
    per-anchor cap → global dedupe → admission ordering.
    """
    selected = select_seeds(
        [(str(pid), float(score)) for pid, score in (seeds or [])],
        n=SEED_COUNT,
    )
    if not selected:
        return Subgraph(
            seeds=(),
            anchors=(),
            candidates=(),
            relations=(),
            zero_seed=True,
            reserved_overflow=0,
            seed_fn=seed_fn,
        )

    # Min–max over the 8 SELECTED seeds (the spec's scoped normalization).
    normalized = normalize_seeds(selected)
    meta = _anchor_meta(graph, [pid for pid, _ in normalized])
    # Operator nodes are never claims — never anchors either, and a seed id
    # the graph does not hold is dropped (never render a phantom claim).
    anchors = [
        (pid, s_norm)
        for pid, s_norm in normalized
        if pid in meta and not meta[pid].get("is_operator")
    ]
    if not anchors:
        return Subgraph(
            seeds=tuple(selected),
            anchors=(),
            candidates=(),
            relations=(),
            zero_seed=True,
            reserved_overflow=0,
            seed_fn=seed_fn,
        )

    raw_by_anchor = {pid: _collect_raw(graph, pid) for pid, _ in anchors}
    hub_ids: set[str] = set()
    for raw in raw_by_anchor.values():
        for row in raw["siblings"]:
            values = _row(row, 4)
            if values[0] is not None:
                hub_ids.add(str(values[0]))
    degrees = _hub_degrees(graph, hub_ids)

    per_anchor: list[list[Candidate]] = []
    all_relations: list[Relation] = []
    candidate_props: dict[str, dict] = {}
    for pid, s_norm in anchors:
        occs, relations, props = _build_anchor(pid, s_norm, raw_by_anchor[pid], degrees)
        per_anchor.append(_collapse_anchor(occs))
        all_relations.extend(relations)
        for cand_id, cand_props in props.items():
            candidate_props.setdefault(cand_id, cand_props)

    deduped = _dedupe_global(per_anchor)
    ordered = tuple(sorted(deduped, key=_admission_key))

    admitted = {pid for pid, _ in anchors} | {c.point_id for c in ordered}
    relations = _dedupe_relations([rel for rel in all_relations if _relation_kept(rel, admitted)])

    content_by_id: dict[str, str] = {
        pid: meta.get(pid, {}).get("content", "") for pid, _ in anchors
    }
    for cand in ordered:
        content_by_id[cand.point_id] = cand.content

    # Raw properties for every ADMITTED point only: anchors (fetched with the
    # seed set) plus the candidates that survived cap → dedupe. Dropped
    # candidates may have been observed during traversal but are not carried.
    point_props: dict[str, dict] = {pid: dict(meta[pid]) for pid, _ in anchors if pid in meta}
    for cand in ordered:
        props = candidate_props.get(cand.point_id)
        if props is not None:
            point_props.setdefault(cand.point_id, props)

    return Subgraph(
        seeds=tuple(selected),
        anchors=tuple(pid for pid, _ in anchors),
        candidates=ordered,
        relations=relations,
        zero_seed=False,
        # The §5 word budget (and therefore any reserved overflow) is a
        # renderer concern; Track A admits no budget, so nothing is dropped.
        reserved_overflow=0,
        seed_fn=seed_fn,
        content_by_id=content_by_id,
        point_props=point_props,
    )


def _fetch_seeds(sdk: Any, graph: Any, question: str) -> tuple[list[tuple[str, float]], str]:
    """Frozen seed function: ``vector_search(question, limit=64)``, else BM25.

    Seeds come from the QUESTION TEXT only. The BM25 fallback is the FTS leg
    of ``tortoise_fts_query`` at the same ``k=64`` and is only used when the
    vector path raises ``ModelEncodeFailedError`` (the graph has zero
    embedding-bearing points) — the run must never mix the two across arms.
    """
    from tools.longmem_eval.retrieve import (
        ModelEncodeFailedError,
        vector_search,
    )

    try:
        return list(vector_search(sdk, question, limit=SEED_LIMIT)), "vector"
    except ModelEncodeFailedError:
        from tortoise import search_engine

        return (
            list(
                search_engine.run_fts_query(graph, question, entity_type="point", limit=SEED_LIMIT)
            ),
            "bm25",
        )


@contextlib.contextmanager
def _graph_scope(sdk: Any, namespace: str | None) -> Iterator[Any]:
    """Yield the graph handle for ``namespace`` (default: the SDK's own).

    When an alternate namespace is requested (the §10 scratch-namespace leak
    test), the SDK is bound to it for the duration — so seeding and traversal
    both hit the same graph — and its prior projection is restored afterwards.
    """
    current = getattr(sdk, "_namespace", None)
    if namespace is None or namespace == current:
        yield sdk._get_proj().g
        return

    saved_ns = current
    saved_proj = getattr(sdk, "_proj", None)
    sdk._namespace = namespace
    sdk._proj = None
    proj = None
    try:
        proj = sdk._get_proj()
        yield proj.g
    finally:
        close = getattr(proj, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()
        sdk._namespace = saved_ns
        sdk._proj = saved_proj


def build_subgraph(sdk: Any, question: str, *, namespace: str | None = None) -> Subgraph:
    """Build the Arm B/C epistemic subgraph for ``question``.

    Seeds are fetched from the question text only (``vector_search`` with the
    frozen BM25 fallback); traversal is exactly 1 hop with a 2nd hop permitted
    only through an ``aboutObject`` hub; ranking is the frozen formula. A
    zero-seed question returns ``zero_seed=True`` with empty candidates (the
    caller renders the sentinel).
    """
    with _graph_scope(sdk, namespace) as graph:
        seeds, seed_fn = _fetch_seeds(sdk, graph, question)
        return build_subgraph_from_seeds(graph, seeds, seed_fn=seed_fn)
