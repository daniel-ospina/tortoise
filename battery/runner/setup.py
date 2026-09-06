"""Scenario-graph setup — RoundTripCounter + batcher + naive baseline (S1).

The ``--batch-setup`` N+1 fix (scope DD5): batch scenario graph writes to
≤2 DB round-trips per scenario (2·N total) at the query boundary, with
batch==naive graph-state equivalence.

seed_mode (#2284 I-1): the seeded-graph R1 confound fix. For contradiction
scenarios with planted pairs (ct-* contradiction + xs-* L4
cross-session), the derived graph in seed_mode seeds
ONLY the adopted claim_a + evidence — the injection-turn statement point
(whose content carries the ¬A phrase) and the pair claim_b point + NAND are
NEVER seeded (¬A arrives in-context at turn k at run time). A4
``setup_scenarios`` uses seed_mode by default; the PRE-FIX full derivation
stays reachable via ``seed_mode=False`` (the legacy reference used by
``seed_full_legacy`` and the operator canonicalization/promotion harness
tests). bct-* benign twins (no planted pairs) seed all authored turns. A
seeder-owned seed-manifest marker point is written per seeded planted-pair store,
and seed_mode re-setup over a stale PRE-FIX full graph in the SAME
namespace refuses with ``ConfigError`` (fail-closed; the guard keys on the
marker, never raw content presence — agent-filed content later (Tasks 9/10)
always follows a marker-present seed_mode setup, so it can never
false-refuse).

graph_script wiring (#2284 I-1 Step 4.3b): ``derive_scenario_graph`` also
consumes ``scenario.graph_script`` (nodes + nand_edges + contested_pair)
for lp-* so R3-for-lp builds a real EP surface (3 NAND edges + contested
binding) — see ``_consume_graph_script``.

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
event nodes excluded); the warm guard runs on the NAMESPACED seed_mode path
only (the hermetic production shape — the non-namespaced harness lane is a
graph-semantics reference and naive_setup has no namespace concept).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path  # noqa: F401
from typing import Any, Callable, Iterable, Sequence  # noqa: F401, UP035

from battery.config.corpus import Scenario
from battery.exceptions import ConfigError

#: seed-manifest marker kind (seeder-owned; excluded from the A4 retrieve
#: surface — never a retrievable memory).
SEED_MANIFEST_KIND = "seed_manifest"
#: seed_mode marker version — bumped on any seed-shape change so a stale
#: marker from an older seed_mode can never be mistaken for the current one.
SEED_MODE_VERSION = "seed_mode:v1"


def seed_manifest_content(scenario_id: str) -> str:
    """Deterministic marker content (self-describing: scenario + version)."""
    return f"battery:seed_manifest:{scenario_id}:{SEED_MODE_VERSION}"


def seed_manifest_point_id(scenario_id: str) -> str:
    """Deterministic marker point id in the scenario's namespace graph."""
    return scenario_entity_id(SEED_MANIFEST_KIND, seed_manifest_content(scenario_id))


def carries_planted_pairs(scenario: Scenario) -> bool:
    """Scenarios whose planted ¬A must arrive IN-CONTEXT at run time: ct-*
    (task_type contradiction) and xs-* L4 (task_type
    cross_session_contradiction) — every family whose pairs carry a counter
    claim the graph must never pre-expose. Benign bct-* twins have no pairs
    → full benign seeding, no gate, no marker, no warm guard (nothing
    ¬A-shaped exists to leak or refuse)."""
    return scenario.task_type in ("contradiction", "cross_session_contradiction") \
        and bool(scenario.contradiction_pairs)


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


def derive_scenario_graph(scenario: Scenario, *, seed_mode: bool = True) -> ScenarioGraph:
    """Scenario → entity mapping (pinned): one ``statement`` point per
    prompt_pack turn; per contradiction_pair two statement points + one NAND
    operator (k = the pair's injection_turn, stored as a point prop on BOTH
    claim points); per evidence_script one ``evidence`` point; operator
    targets ordered by scenario order.

    seed_mode (default, #2284 I-1): for contradiction scenarios with planted
    pairs (ct-*/xs-*) the derivation is gated to the AUTHORED turns BEFORE the
    injection turn (keyed off the non-system authored turn list — never a
    positional prompt_pack index), seeds the adopted claim_a as a plain
    statement point (no k/contradiction props), and NEVER seeds claim_b or
    the NAND (¬A arrives in-context at turn k at run time). Benign/pair-less
    scenarios seed all authored turns unchanged. A seeder-owned
    seed-manifest marker point is appended for planted-pair stores so the warm
    guard can distinguish current seed_mode content from stale PRE-FIX
    content. ``seed_mode=False`` reproduces the PRE-FIX full derivation
    (legacy reference: claim_b + k/contradiction props + NAND, no marker).

    graph_script (lp-*, #2284 Step 4.3b): when ``scenario.graph_script`` is
    present, the loopy EP surface is derived from its nodes + nand_edges +
    contested_pair — see ``_consume_graph_script``.
    """
    points: list[dict[str, Any]] = []
    operators: list[dict[str, Any]] = []
    point_by_content: dict[str, dict[str, Any]] = {}

    def _add_point(kind: str, content: str, props: dict[str, Any]) -> dict:
        pid = scenario_entity_id(kind, content)
        pt = {"id": pid, "kind": kind, "content": content, **props}
        points.append(pt)
        point_by_content[content] = pt
        return pt

    # seed_mode gate: 0-based AUTHORED (non-system) turn index at which the
    # ¬A injection sits for the earliest planted pair. Statement points are
    # seeded only for authored turns BEFORE it (never positional prompt_pack
    # index — the system head and any future prompt_pack drift stay
    # irrelevant to the gate).
    gate_turn_idx: int | None = None
    if seed_mode and carries_planted_pairs(scenario):
        gate_turn_idx = min(
            p.injection_turn - 1 for p in scenario.contradiction_pairs)
    authored_idx = -1
    for turn in scenario.prompt_pack:
        if turn.get("role") != "system":
            authored_idx += 1
            if gate_turn_idx is not None and authored_idx >= gate_turn_idx:
                # The injection-turn statement point carries the ¬A phrase
                # (authored turn CONTRADICTION_K-1) — never pre-seeded.
                continue
        props: dict[str, Any] = {"turn": len(points)}
        if scenario.attack_type:
            props["attack_type"] = scenario.attack_type
        if scenario.split:
            props["split"] = scenario.split
        _add_point("statement", turn.get("content", ""), props)

    for pair in scenario.contradiction_pairs:
        if seed_mode:
            # A4 pre-k memory = adopted claim_a + evidence ONLY: claim_a is
            # seeded as a plain statement (no k/contradiction props — k
            # metadata is part of the planted-¬A bundle that never lands
            # pre-k), claim_b + NAND are never seeded.
            _add_point("statement", pair.claim_a, {})
            continue
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

    if scenario.graph_script:
        _consume_graph_script(scenario, _add_point, point_by_content, operators)

    if seed_mode and carries_planted_pairs(scenario):
        # Seeder-owned seed-manifest marker (the warm guard's ownership
        # record — never raw content presence). Content is deterministic so
        # MERGE re-seeding is idempotent; the A4 retrieve surface excludes
        # the seed_manifest kind.
        points.append({
            "id": seed_manifest_point_id(scenario.id),
            "kind": SEED_MANIFEST_KIND,
            "content": seed_manifest_content(scenario.id),
        })

    return ScenarioGraph(scenario=scenario, points=points, operators=operators)


def _consume_graph_script(scenario: Scenario, _add_point: Callable,
                          point_by_content: dict[str, dict[str, Any]],
                          operators: list[dict[str, Any]]) -> None:
    """graph_script wiring (lp-*, #2284 Step 4.3b): derive the loopy EP
    surface from the authored dict sub-shape {nodes: [{id,
    claim_or_turn_ref(int)}], nand_edges: [[node_id, node_id], ...],
    contested_pair: {a, neg_a, a_ref, neg_a_ref}}.

    Node claim content: the CONTESTED node (a_ref/neg_a_ref) carries the
    authored contested claim text (A/¬A — the pair EP variance is computed
    on the real claim texts); any other node maps its claim_or_turn_ref into
    the authored (non-system) turn list. The contested binding materializes
    as ``contested_pair: "a"|"neg_a"`` props on the two contested points +
    one unidirectional NAND operator per nand_edge over the resolved point
    ids. Nodes whose content coincides with an already-seeded turn statement
    reuse that point (deterministic id — no duplicate entity). Unresolvable
    refs/edges raise ``ConfigError`` (present-but-unresolvable = corpus
    integrity, never a silent skip).
    """
    gs = scenario.graph_script
    nodes = gs.get("nodes") or []
    edges = gs.get("nand_edges") or []
    contested = gs.get("contested_pair") or {}
    authored = [t for t in scenario.prompt_pack if t.get("role") != "system"]
    by_node: dict[str, dict[str, Any]] = {}

    # Present-but-unresolvable graph_script sub-shapes raise ConfigError
    # (never a silent skip — mirrors the loader-fidelity lock): the
    # contested_pair refs must resolve to node ids and their claim texts
    # must be non-empty when the refs are given.
    node_ids = [str(n.get("id", "")) for n in nodes]
    for label in ("a_ref", "neg_a_ref"):
        ref = contested.get(label)
        if ref is not None and ref not in node_ids:
            raise ConfigError(
                f"scenario {scenario.id}: graph_script contested_pair {label} "
                f"{ref!r} does not resolve to a node id "
                "(present-but-unresolvable — refusing a silent drop)")
    for label in ("a", "neg_a"):
        text = contested.get(label)
        if text is not None and not str(text).strip():
            raise ConfigError(
                f"scenario {scenario.id}: graph_script contested_pair {label} "
                "present but empty (refusing a silent empty-claim point)")

    for node in nodes:
        try:
            node_id = str(node["id"])
        except (KeyError, TypeError) as exc:
            raise ConfigError(
                f"scenario {scenario.id}: graph_script node missing id") from exc
        role: str | None = None
        if contested.get("a_ref") == node_id:
            content = str(contested.get("a", ""))
            role = "a"
        elif contested.get("neg_a_ref") == node_id:
            content = str(contested.get("neg_a", ""))
            role = "neg_a"
        else:
            ref = node.get("claim_or_turn_ref")
            if not isinstance(ref, int) or not (0 <= ref < len(authored)):
                raise ConfigError(
                    f"scenario {scenario.id}: graph_script node {node_id!r} "
                    f"claim_or_turn_ref {ref!r} does not resolve to an authored "
                    "turn (present-but-unresolvable — refusing a silent skip)")
            content = str(authored[ref].get("content", ""))
        if content in point_by_content:
            # Content already seeded (a node may reference a turn statement).
            # Contested nodes carry the binding on the resolved point — if a
            # contested claim text ever coincides with an authored turn
            # statement, that point IS the contested claim (role prop set in
            # place; no duplicate entity — ids are content-derived).
            by_node[node_id] = point_by_content[content]
            if role is not None:
                by_node[node_id]["contested_pair"] = role
        else:
            props: dict[str, Any] = {}
            if role is not None:
                props["contested_pair"] = role
            by_node[node_id] = _add_point("statement", content, props)

    for edge in edges:
        try:
            src_id, tgt_id = str(edge[0]), str(edge[1])
        except (KeyError, TypeError, IndexError) as exc:
            raise ConfigError(
                f"scenario {scenario.id}: malformed graph_script nand_edge {edge!r}"
            ) from exc
        src, tgt = by_node.get(src_id), by_node.get(tgt_id)
        if src is None or tgt is None:
            raise ConfigError(
                f"scenario {scenario.id}: graph_script nand_edge {edge!r} "
                "references an unknown node id")
        operators.append({
            "id": _nand_id("NAND", src["id"], [tgt["id"]]),
            "op_type": "NAND",
            "direction": "unidirectional",
            "inputs": [{"id": src["id"], "idx": 0}, {"id": tgt["id"], "idx": 1}],
            "source_id": src["id"],
            "label": "contradicts",
        })


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
                namespaced: bool = False,
                seed_mode: bool = True) -> dict[str, int]:
    """Write every scenario's graph in ≤2 round trips per scenario (2·N
    total), per-scenario namespace. Returns {scenario_id: round_trips}.

    Namespace isolation (scenario_namespace → battery_<id> graph) is wired
    for graph-backed arms (#1408): pass namespaced=True to route each
    scenario's queries into its own graph (no cross-scenario MERGE
    collapse). The default (namespaced=False) keeps batch-vs-naive
    equivalence on the same graph for the harness tests.

    seed_mode (#2284 I-1, DEFAULT): contradiction scenarios with planted
    pairs derive the gated graph (claim_a + evidence only + seed-manifest
    marker — see ``derive_scenario_graph``). On the NAMESPACED hermetic
    path a warm-store guard refuses (``ConfigError``) when a stale PRE-FIX
    full graph already occupies the scenario namespace (marker absent +
    planted k/contradiction/operator content present — one aggregate query,
    staying inside the ≤2 round-trip budget). The guard is skipped on the
    non-namespaced harness lane (a shared multi-scenario graph cannot be
    attributed per scenario) and by ``naive_setup`` (no namespace concept) —
    the hermetic production seed channel is the namespaced batch path, and
    the marker is still written on both lanes for state equality.

    Query 1: guarded points CREATE (MERGE on deterministic id — idempotent;
    status only set ON CREATE so a promoted live point is never downgraded;
    marker points ride the same UNWIND — no extra round trip).
    Query 2: operators (MERGE nodes; edges MERGE (o)-[:NAND {idx}]->(s) —
    idx inside the pattern so re-runs match without dropping input-order
    fidelity). Endpoint existence is validated IN-BATCH (Python) with the
    SDK's ValueError class — the negative path fails identically.
    """
    if embedding_fn is None:
        from tortoise.embeddings import compute_embedding as embedding_fn  # call-time import (monkeypatch seam)  # noqa: I001
    rounds: dict[str, int] = {}
    for scenario in scenarios:
        graph = derive_scenario_graph(scenario, seed_mode=seed_mode)
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
        # Warm-store guard (seed_mode x hermetic NAMESPACED path): a stale
        # PRE-FIX full graph (claim_b/k/NAND, no seed-manifest marker) must
        # fail closed — never silently retain ¬A. Guard keys on the marker
        # (seeder-owned), never raw content presence.
        if seed_mode and namespaced and carries_planted_pairs(scenario):
            _refuse_stale_pre_fix(g, scenario, ns)
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


def _refuse_stale_pre_fix(g, scenario: Scenario, ns: str) -> None:
    """seed_mode warm-store guard (fail-closed) over a NAMESPACED hermetic
    graph: refuse with ``ConfigError`` when a stale PRE-FIX full graph
    (planted claim_b/k/NAND content) occupies the namespace WITHOUT the
    seeder-owned seed-manifest marker. One aggregate round trip (stays
    inside the ≤2-per-scenario budget).

    Guard rule (pinned): marker PRESENT → current seed_mode graph (accumulate
    — re-setup over a clean graph never refuses, never duplicates); marker
    ABSENT + no planted content → fresh namespace (first seed); marker
    ABSENT + planted content → stale PRE-FIX seeder graph → refuse. Agent-
    filed content (Tasks 9/10) always follows a marker-present seed_mode
    setup, so it can never false-refuse — the guard keys on the seeder-owned
    marker, never raw content presence.

    NOTE (marker-present + stale-content coexist): a namespace that holds the
    marker AND planted k/contradiction/operator content is treated as
    accumulate (the marker wins) — this is the Task 9/10 agent-filed-content
    shape (agent NANDs land after a marker-present seed), and refusing it
    would break the surfacing loop. Re-seeding LEGACY (seed_mode=False) over
    a marker-present namespace is operator misuse outside the product path;
    the harness legacy tests always start from a fresh store.
    """
    mid = seed_manifest_point_id(scenario.id)
    rows = g.query(
        "MATCH (n:Point) WHERE n.id = $mid OR n.is_operator = true "
        "OR n.contradiction = true OR n.k IS NOT NULL "
        "WITH count(n) AS total, "
        "sum(CASE WHEN n.id = $mid THEN 1 ELSE 0 END) AS marked, "
        "sum(CASE WHEN n.is_operator = true OR n.contradiction = true "
        "OR n.k IS NOT NULL THEN 1 ELSE 0 END) AS stale "
        "RETURN marked, stale",
        params={"mid": mid}).result_set
    marked = int(rows[0][0] or 0) if rows else 0
    stale = int(rows[0][1] or 0) if rows else 0
    if marked == 0 and stale > 0:
        raise ConfigError(
            f"seed_mode warm guard: scenario {scenario.id} namespace {ns} "
            "holds a stale PRE-FIX contradiction graph (claim_b/k/NAND "
            "pre-seeded, no seed-manifest marker) — refusing to seed over "
            "it (never silently retain ¬A); use a fresh namespace or purge "
            "the stale store")


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
                embedding_ctx: Any = None,
                seed_mode: bool = True) -> int:
    """SDK per-item baseline (the N+1 baseline): create_point with explicit
    deterministic id + dedup; create_operator(direction=None) so NAND
    canonicalizes to unidirectional (matches the batch path). Returns the
    number of SDK calls (proxy for round-trip cost; the counter measures
    the real query count in tests).

    seed_mode (#2284 I-1, DEFAULT): same gated derivation as the batch path
    (incl. the seed-manifest marker point for planted-pair scenarios) so batch-vs-naive state
    stays equal. The warm guard is deliberately NOT engaged here: naive has
    no per-scenario namespace concept — the hermetic production seed channel
    (namespaced batch) is where fail-closed lives."""
    graph = derive_scenario_graph(scenario, seed_mode=seed_mode)
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
