"""Scenario-graph setup — RoundTripCounter + batcher + naive baseline (S1).

The ``--batch-setup`` N+1 fix (scope DD5): batch scenario graph writes to
≤2 DB round-trips per scenario (2·N total) at the query boundary, with
batch==naive graph-state equivalence.

- RoundTripCounter wraps ``FalkorProjection.g.query`` (every call = 1 DB
  round trip; never SDK-method counting).
- batch_setup writes each scenario's points (guarded CREATE keyed on
  deterministic id — idempotent) in ONE UNWIND query and its operators
  (MERGE nodes + MERGE edges, endpoint validation in-batch) in ONE more.
- naive_setup mirrors the SDK's per-item path (create_point with explicit
  deterministic id + dedup; create_operator with direction=None so NAND
  canonicalizes to unidirectional on BOTH paths).

Documented divergences: the batch path skips :GraphEvent emission (scratch
eval namespace); equivalence compares keyed point/operator lookups, never
graph-wide node counts; compared props are a prop-SUBSET (timestamps and
event nodes excluded).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path  # noqa: F401
from typing import Any, Callable, Iterable, Sequence  # noqa: F401, UP035

from battery.config.corpus import Scenario


def scenario_entity_id(kind: str, content: str) -> str:
    """Deterministic id: sha256(content) hex [:26] with a kind prefix
    (mirrors the SDK's _entity_name_id precedent; deterministic by design —
    the SDK's non-ULID explicit-id warning is intentional here)."""
    return f"{kind[:4]}-{hashlib.sha256(content.encode('utf-8')).hexdigest()[:26]}"


def _nand_id(op_type: str, source: str, targets: Sequence[str]) -> str:
    return scenario_entity_id("nand", f"{op_type}|{source}|{','.join(targets)}")


@dataclass(frozen=True)
class ScenarioGraph:
    """The derived graph entities for one scenario (the shape #1409's
    probes read — scope DD5 pinned derivation rule)."""

    scenario: Scenario
    points: list[dict[str, Any]]
    operators: list[dict[str, Any]]

    @property
    def point_ids(self) -> set[str]:
        return {p["id"] for p in self.points}


def derive_scenario_graph(scenario: Scenario) -> ScenarioGraph:
    """Scenario → entity mapping (pinned): one ``statement`` point per
    prompt_pack turn; per contradiction_pair two statement points + one NAND
    operator (k = the pair's injection_turn, stored as a point prop on BOTH
    claim points); per evidence_script one ``evidence`` point; operator
    targets ordered by scenario order."""
    points: list[dict[str, Any]] = []
    operators: list[dict[str, Any]] = []
    point_by_content: dict[str, dict[str, Any]] = {}

    def _add_point(kind: str, content: str, props: dict[str, Any]) -> dict:
        pid = scenario_entity_id(kind, content)
        pt = {"id": pid, "kind": kind, "content": content, **props}
        points.append(pt)
        point_by_content[content] = pt
        return pt

    for turn in scenario.prompt_pack:
        props: dict[str, Any] = {"turn": len(points)}
        if scenario.attack_type:
            props["attack_type"] = scenario.attack_type
        if scenario.split:
            props["split"] = scenario.split
        _add_point("statement", turn.get("content", ""), props)

    for pair in scenario.contradiction_pairs:
        a = _add_point("statement", pair.claim_a,
                       {"k": pair.injection_turn, "contradiction": True})
        b = _add_point("statement", pair.claim_b,
                       {"k": pair.injection_turn, "contradiction": True})
        inputs = [a["id"], b["id"]]
        operators.append({
            "id": _nand_id("NAND", a["id"], [b["id"]]),
            "op_type": "NAND",
            "direction": "unidirectional",
            "inputs": [{"id": i, "idx": n} for n, i in enumerate(inputs)],
            "source_id": a["id"],
            "label": "contradicts",
        })

    for script in scenario.evidence_scripts:
        _add_point("evidence", script, {"evidence_tier": True})

    return ScenarioGraph(scenario=scenario, points=points, operators=operators)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


class RoundTripCounter:
    """Proxy over a projection graph — every ``query`` call = 1 round trip.

    Attach via ``proj.g = RoundTripCounter(proj.g)`` (g is a plain
    attribute on FalkorProjection). Pass-through: same query signature +
    result passthrough (incl. ``result_set``).
    """

    def __init__(self, graph):
        self._graph = graph
        self.count = 0

    def query(self, cypher: str, params: dict | None = None, timeout=None):
        self.count += 1
        return self._graph.query(cypher, params=params, timeout=timeout)

    def __getattr__(self, name):
        return getattr(self._graph, name)


def batch_setup(proj, scenarios: Sequence[Scenario], *,
                embedding_fn: Callable[[str], list[float] | None] | None = None,
                counter: RoundTripCounter | None = None,
                namespaced: bool = False) -> dict[str, int]:
    """Write every scenario's graph in ≤2 round trips per scenario (2·N
    total), per-scenario namespace. Returns {scenario_id: round_trips}.

    Namespace isolation (scenario_namespace → battery_<id> graph) is wired
    for graph-backed arms (#1408): pass namespaced=True to route each
    scenario's queries into its own graph (no cross-scenario MERGE
    collapse). The default (namespaced=False) keeps batch-vs-naive
    equivalence on the same graph for the harness tests.

    Query 1: guarded points CREATE (MERGE on deterministic id — idempotent;
    status only set ON CREATE so a promoted live point is never downgraded).
    Query 2: operators (MERGE nodes; edges MERGE (o)-[:NAND {idx}]->(s) —
    idx inside the pattern so re-runs match without dropping input-order
    fidelity). Endpoint existence is validated IN-BATCH (Python) with the
    SDK's ValueError class — the negative path fails identically.
    """
    if embedding_fn is None:
        from tortoise.embeddings import compute_embedding as embedding_fn  # call-time import (monkeypatch seam)  # noqa: I001
    rounds: dict[str, int] = {}
    for scenario in scenarios:
        graph = derive_scenario_graph(scenario)
        now = _now_iso()
        # In-batch endpoint validation (mirrors create_operator's "fail
        # loudly" — ValueError, same class).
        _validate_operator_endpoints(graph)
        ns = scenario_namespace(scenario.id)
        # #1408 wiring (was the #1406 P2-2 deferral): graph-backed arms (A4)
        # isolate per-scenario graphs via db.select_graph(ns). The counter
        # path (equivalence tests) keeps the default graph so batch-vs-naive
        # comparison stays on the same semantics.
        if counter is not None:
            g = counter
        elif namespaced:
            g = proj.db.select_graph(ns)
        else:
            g = proj.g
        # Query 1 — points (guarded CREATE on deterministic id).
        point_rows = []
        for p in graph.points:
            try:
                embedding = embedding_fn(p["content"])
            except Exception:  # noqa: BLE001, RUF100
                embedding = None
            props = {k: v for k, v in p.items() if k not in ("id", "kind", "content")}
            point_rows.append({
                "id": p["id"], "content": p["content"], "kind": p["kind"],
                "props": props, "now": now, "embedding": embedding,
                "content_hash": _content_hash(p["content"]),
            })
        g.query(
            f"UNWIND $rows AS r "  # noqa: F541
            f"MERGE (n:Point {{id: r.id}}) "  # noqa: F541
            f"ON CREATE SET n += {{content: r.content, pointKind: r.kind, "  # noqa: F541
            f"is_operator: false, status: 'draft', createdAt: r.now, "  # noqa: F541
            f"updatedAt: r.now, content_hash: r.content_hash}}, "  # noqa: F541
            f"n += r.props "  # noqa: F541
            f"SET n.embedding = vecf32(r.embedding)",  # noqa: F541
            params={"rows": point_rows},
        )
        # Query 2 — operators (MERGE nodes; guarded edge MERGE; promote
        # folded in — keeps 2 round trips per scenario total).
        op_rows = []
        for op in graph.operators:
            op_rows.append({
                "id": op["id"], "op_type": op["op_type"],
                "direction": op["direction"], "label": op.get("label"),
                "inputs": op["inputs"], "source_id": op["source_id"],
            })
        if op_rows:
            g.query(
                f"UNWIND $rows AS r "  # noqa: F541
                f"MERGE (o:Point {{id: r.id}}) "  # noqa: F541
                f"ON CREATE SET o.is_operator = true, o.op_type = r.op_type, "  # noqa: F541
                f"o.direction = r.direction, o.label = r.label "  # noqa: F541
                f"WITH o, r UNWIND r.inputs AS inp "  # noqa: F541
                f"MATCH (s) WHERE (s:Point OR s:Event) AND s.id = inp.id "  # noqa: F541
                f"MERGE (o)-[:NAND {{idx: inp.idx}}]->(s) "  # noqa: F541
                f"WITH DISTINCT o, r "  # noqa: F541
                f"MATCH (src:Point {{id: r.source_id}}) "  # noqa: F541
                f"WHERE src.status IS NULL OR src.status = 'draft' "  # noqa: F541
                f"SET src.status = 'live'",  # noqa: F541
                params={"rows": op_rows},
            )
        rounds[scenario.id] = 2
    return rounds


def _promote_sources(g, graph: ScenarioGraph) -> None:
    """Mirror create_operator's promote_source: flip only Point sources with
    status IS NULL or 'draft' to 'live' (never Events, never terminal).

    NOTE (2026-08-17): superseded by the batched promotion folded into
    batch_setup's query 2 — kept only as the single-scenario reference
    implementation; remove when #1408 wires the runner to the graph batcher.
    """
    source_ids = [op["source_id"] for op in graph.operators]
    if not source_ids:
        return
    g.query(
        "UNWIND $ids AS sid "
        "MATCH (s:Point {id: sid}) "
        "WHERE s.status IS NULL OR s.status = 'draft' "
        "SET s.status = 'live'",
        params={"ids": source_ids},
    )


def _validate_operator_endpoints(graph: ScenarioGraph) -> None:
    """In-batch endpoint validation — ValueError (SDK class, same message
    shape). Raises before any DB write so a bad edge ref never lands."""
    known = graph.point_ids
    missing: list[str] = []
    for op in graph.operators:
        for inp in op["inputs"]:
            if inp["id"] not in known:
                missing.append(inp["id"])
    if missing:
        raise ValueError(f"Cannot create operator: Points {missing} do not exist")


def naive_setup(sdk, scenario: Scenario, *,
                embedding_ctx: Any = None) -> int:
    """SDK per-item baseline (the N+1 baseline): create_point with explicit
    deterministic id + dedup; create_operator(direction=None) so NAND
    canonicalizes to unidirectional (matches the batch path). Returns the
    number of SDK calls (proxy for round-trip cost; the counter measures
    the real query count in tests)."""
    graph = derive_scenario_graph(scenario)
    for p in graph.points:
        props = {k: v for k, v in p.items() if k not in ("id", "kind", "content")}
        props["id"] = p["id"]
        props["dedup"] = True
        sdk.create_point(p["kind"], p["content"], **props)
    for op in graph.operators:
        sdk.create_operator(
            op["op_type"], op["source_id"],
            [i["id"] for i in op["inputs"][1:]],
            label=op.get("label"), direction=None)
    return len(graph.points) + len(graph.operators)


def scenario_namespace(scenario_id: str) -> str:
    """Per-scenario namespace (materializes as team_<ns> graphs per the
    SDK's non-test namespace prefix — documented so scenario ids can never
    collide with provisioned team ids)."""
    import re
    return "battery_" + re.sub(r"[^a-zA-Z0-9_-]", "_", scenario_id)[:40]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def graph_state_equivalence(proj, scenarios: Sequence[Scenario]) -> dict[str, Any]:
    """Keyed point/operator inventory for the equivalence test: points by
    id → {content_hash, pointKind, status, is_operator}; operators by id →
    {op_type, direction, inputs[(id, idx)]}. Keyed lookups only — never
    graph-wide node counts (event-emission divergence documented)."""
    state: dict[str, Any] = {"points": {}, "operators": {}}
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.is_operator = false "
        "RETURN n.id, n.content_hash, n.pointKind, n.status, n.embedding IS NOT NULL"
    ).result_set
    for rid, ch, kind, status, has_emb in rows:
        state["points"][rid] = {
            "content_hash": ch, "pointKind": kind, "status": status,
            "has_embedding": bool(has_emb),
        }
    rows = proj.g.query(
        "MATCH (o:Point {is_operator: true})-[e]->(s) "
        "RETURN o.id, o.op_type, o.direction, s.id, e.idx "
        "ORDER BY o.id, e.idx"
    ).result_set
    for oid, op_type, direction, sid, idx in rows:
        op = state["operators"].setdefault(
            oid, {"op_type": op_type, "direction": direction, "inputs": []})
        op["inputs"].append({"id": sid, "idx": idx})
    return state
