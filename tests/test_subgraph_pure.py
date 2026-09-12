"""#3011 Track A — hermetic tests for the subgraph traversal + ranking engine.

NO database, NO model, NO network. Every test drives ``tortoise/subgraph.py``
through a plain in-memory fake graph exposing the same
``query(cypher, params).result_set`` shape as FalkorDB (the pattern
``tests/test_falkordb_compat.py`` establishes for engine-shaped mocks).

Coverage maps 1:1 onto the frozen §3 "Arm B/C construction" rules:

* seed ordering, dedupe-keeping-highest, and the byte-wise boundary tie-break;
* min–max normalization (including the all-equal 1.0 case);
* operator nodes traversed through, never returned as candidates/anchors;
* 1-hop vs the hub-mediated 2-hop (seed → aboutObject entity → sibling);
* hop decay / each edge weight / hub damping on 2-hop only;
* cap → dedupe order; dedupe keeps the highest score and never frees a cap
  slot in another anchor's block; reserved endpoints never consume a cap slot;
* zero seeds → ``zero_seed=True`` with empty candidates;
* ``point_props`` — raw point metadata (``session_id``, ``createdAt``,
  ``validFrom``, ``source_turn_id``, the EP posteriors, ``status``,
  ``is_operator``) populated for every admitted anchor and candidate, with
  absent properties omitted rather than set to ``None``;
* the hard leakage rule — the module reads no gold annotation field, asserted
  statically (source scan) and at run time (issued Cypher + params).
"""

from __future__ import annotations

import ast
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import subgraph
from tortoise.subgraph import (
    EDGE_PRIORITY_WEIGHT,
    HOP_DECAY,
    PER_ANCHOR_CAP,
    SEED_LIMIT,
    build_subgraph_from_seeds,
    normalize_seeds,
    rank_score,
    select_seeds,
)

_REPO = Path(__file__).resolve().parent.parent
_MODULE_PATH = _REPO / "tortoise" / "subgraph.py"

#: The four gold artifacts the §10 static-reference assertion names. They must
#: never appear in the engine source (which is why the module docstring names
#: none of them literally).
_FORBIDDEN_TOKENS = (
    "has_answer",
    "answer_session_ids",
    "lme_session_index",
    "gold-evidence-claims",
)


# ── fake graph ────────────────────────────────────────────────────────────


class _Result:
    """Minimal FalkorDB ``QueryResult`` stand-in (only ``result_set`` used)."""

    __slots__ = ("result_set",)

    def __init__(self, rows: list):
        self.result_set = rows


class FakeGraph:
    """In-memory model of the claim/operator/entity graph the engine walks."""

    def __init__(self) -> None:
        self.points: dict[str, dict] = {}
        self.operators: dict[str, dict] = {}
        self.objects: dict[str, str] = {}
        self.about: dict[str, list[str]] = {}
        self.corrects: list[tuple[str, str]] = []
        self.object_degree: dict[str, int] = {}
        self.issued: list[tuple[str, dict]] = []

    # ── builders ──
    def add_point(
        self, pid: str, content: str | None = None, *, is_operator: bool = False, **props
    ) -> FakeGraph:
        self.points[pid] = {
            "id": pid,
            "content": content,
            "is_operator": is_operator,
            **props,
        }
        return self

    def add_operator(
        self, op_id: str, edge_type: str, endpoints: list[tuple[str, int]]
    ) -> FakeGraph:
        self.operators[op_id] = {"type": edge_type, "endpoints": tuple(endpoints)}
        return self

    def add_object(self, oid: str, name: str) -> FakeGraph:
        self.objects[oid] = name
        return self

    def set_degree(self, oid: str, degree: int) -> FakeGraph:
        self.object_degree[oid] = degree
        return self

    def link_about(self, pid: str, oid: str) -> FakeGraph:
        self.about.setdefault(pid, [])
        if oid not in self.about[pid]:
            self.about[pid].append(oid)
        return self

    def add_corrects(self, new_id: str, old_id: str) -> FakeGraph:
        self.corrects.append((new_id, old_id))
        return self

    # ── query dispatch (one branch per engine Cypher statement) ──
    def _prop_values(self, pid: str) -> list:
        """Raw point properties in the engine's projection order (id first)."""
        point = self.points.get(pid)
        if point is None:
            return [None] * len(subgraph._POINT_PROP_KEYS)
        return [pid if key == "id" else point.get(key) for key in subgraph._POINT_PROP_KEYS]

    def query(self, cypher: str, params: dict | None = None) -> _Result:
        params = dict(params or {})
        self.issued.append((cypher, params))

        if "WHERE p.id IN $ids" in cypher:
            rows = []
            for pid in params.get("ids", []):
                if pid not in self.points:
                    continue
                rows.append(self._prop_values(pid))
            return _Result(rows)

        if "count(r) AS hub_degree" in cypher:
            rows = []
            for oid in params.get("ids", []):
                if oid in self.object_degree:
                    degree = self.object_degree[oid]
                else:
                    degree = sum(1 for links in self.about.values() if oid in links)
                rows.append([oid, degree])
            return _Result(rows)

        if "sib.id AS sib_id" in cypher:
            nid = params["id"]
            rows = []
            for oid in self.about.get(nid, []):
                for other, links in self.about.items():
                    if other == nid or oid not in links:
                        continue
                    if self.points.get(other, {}).get("is_operator"):
                        continue
                    rows.append(
                        [
                            oid,
                            self.objects.get(oid, ""),
                            *self._prop_values(other),
                        ]
                    )
            return _Result(rows)

        if "o.id AS hub_id, o.name AS hub_name" in cypher:
            nid = params["id"]
            rows = [[oid, self.objects.get(oid, "")] for oid in self.about.get(nid, [])]
            return _Result(rows)

        if "type(r2) AS edge_type" in cypher:
            nid = params["id"]
            rows = []
            for op_id, op in self.operators.items():
                for pid, idx in op["endpoints"]:
                    if pid != nid:
                        continue
                    for other, other_idx in op["endpoints"]:
                        if other == nid:
                            continue
                        if self.points.get(other, {}).get("is_operator"):
                            continue
                        rows.append(
                            [
                                op["type"],
                                idx,
                                other_idx,
                                op_id,
                                *self._prop_values(other),
                            ]
                        )
            return _Result(rows)

        if "CORRECTS" in cypher:
            nid = params["id"]
            if "(other:Point)-[:CORRECTS]->(n)" in cypher:
                return _Result([self._prop_values(new) for new, old in self.corrects if old == nid])
            return _Result([self._prop_values(old) for new, old in self.corrects if new == nid])

        raise AssertionError(f"unrecognized Cypher in fake graph: {cypher!r}")


def _ids(seq) -> list[str]:
    return [c.point_id for c in seq]


def _by_id(sg) -> dict:
    return {c.point_id: c for c in sg.candidates}


# ── seed ordering + boundary tie-break ────────────────────────────────────


def test_select_seeds_ranks_by_score_and_dedupes_keeping_highest():
    hits = [("a", 0.1), ("b", 0.9), ("a", 0.5), ("c", 0.7)]
    assert select_seeds(hits, n=8) == [("b", 0.9), ("c", 0.7), ("a", 0.5)]


def test_select_seeds_takes_exactly_eight():
    hits = [(f"p{i:02d}", float(100 - i)) for i in range(20)]
    out = select_seeds(hits, n=8)
    assert [pid for pid, _ in out] == [f"p{i:02d}" for i in range(8)]


def test_select_seeds_boundary_tie_break_is_bytewise_ascending_id():
    # 5 strictly-above candidates, then 4 candidates tied at the boundary
    # score for the final 3 slots.
    hits = [
        ("hi0", 9.0),
        ("hi1", 8.0),
        ("hi2", 7.0),
        ("hi3", 6.0),
        ("hi4", 5.5),
        ("s:2", 5.0),
        ("s:10", 5.0),
        ("s:1", 5.0),
        ("s:3", 5.0),
    ]
    out = select_seeds(hits, n=8)
    selected = [pid for pid, _ in out]
    assert len(selected) == 8
    # byte-wise ascending: "s:1" < "s:10" < "s:2" < "s:3"
    assert selected[5:] == ["s:1", "s:10", "s:2"]
    assert "s:3" not in selected


def test_select_seeds_boundary_tie_break_is_input_order_independent():
    hits = [
        ("hi0", 9.0),
        ("hi1", 8.0),
        ("hi2", 7.0),
        ("hi3", 6.0),
        ("hi4", 5.5),
        ("s:2", 5.0),
        ("s:10", 5.0),
        ("s:1", 5.0),
        ("s:3", 5.0),
    ]
    expected = select_seeds(hits, n=8)
    for seed in range(5):
        shuffled = list(hits)
        random.Random(seed).shuffle(shuffled)
        assert select_seeds(shuffled, n=8) == expected


# ── min–max normalization ─────────────────────────────────────────────────


def test_normalize_seeds_min_max():
    out = normalize_seeds([("a", 1.0), ("b", 3.0), ("c", 5.0)])
    assert [pid for pid, _ in out] == ["a", "b", "c"]
    assert [round(s, 6) for _, s in out] == [0.0, 0.5, 1.0]


def test_normalize_seeds_all_equal_is_one():
    out = normalize_seeds([("a", 4.0), ("b", 4.0), ("c", 4.0)])
    assert [s for _, s in out] == [1.0, 1.0, 1.0]


def test_normalize_seeds_empty():
    assert normalize_seeds([]) == []


# ── ranking formula: each edge weight, hop decay, damping ────────────────


@pytest.mark.parametrize(
    "edge_type,weight",
    [
        ("aboutObject", 1.0),
        ("supersession", 0.9),
        ("NAND", 0.8),
        ("IMPL", 0.7),
    ],
)
def test_each_edge_priority_weight(edge_type, weight):
    assert rank_score(1.0, edge_type, 1, hub_degree=None) == pytest.approx(weight)


def test_hop_decay_halves_two_hop_raw():
    assert HOP_DECAY == {1: 1.0, 2: 0.5}
    one_hop = rank_score(1.0, "IMPL", 1, hub_degree=None)
    two_hop = rank_score(1.0, "IMPL", 2, hub_degree=0)
    assert two_hop == pytest.approx(one_hop * 0.5)


def test_rank_score_hub_damping_is_natural_log():
    import math

    raw = 1.0 * EDGE_PRIORITY_WEIGHT["aboutObject"] * HOP_DECAY[2]
    assert rank_score(1.0, "aboutObject", 2, hub_degree=3) == pytest.approx(
        raw / (1.0 + math.log(1.0 + 3))
    )


def test_rank_score_one_hop_ignores_hub_degree():
    # Direct 1-hop candidates are never damped, even if a degree is passed.
    assert rank_score(1.0, "IMPL", 1, hub_degree=10_000) == pytest.approx(0.7)


def test_rank_score_two_hop_requires_hub_degree():
    with pytest.raises(ValueError):
        rank_score(1.0, "aboutObject", 2, hub_degree=None)


# ── operator nodes ────────────────────────────────────────────────────────


def _operator_graph() -> FakeGraph:
    g = FakeGraph()
    g.add_point("S1", "seed claim")
    g.add_point("C1", "implied claim")
    g.add_point("opX", None, is_operator=True)
    g.add_operator("op1", "IMPL", [("S1", 0), ("C1", 1)])
    return g


def test_operator_nodes_never_returned_as_candidates():
    g = _operator_graph()
    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    assert _ids(sg.candidates) == ["C1"]
    assert "opX" not in _ids(sg.candidates)
    assert all(c.content for c in sg.candidates)


def test_operator_seed_is_never_an_anchor():
    g = FakeGraph()
    g.add_point("opX", None, is_operator=True)
    sg = build_subgraph_from_seeds(g, [("opX", 1.0)])
    assert sg.zero_seed is True
    assert sg.anchors == ()
    assert sg.candidates == ()


def test_operator_endpoint_in_a_mixed_operator_is_skipped():
    # An operator whose other endpoint is ALSO an operator must not surface it.
    g = FakeGraph()
    g.add_point("S1", "seed")
    g.add_point("C1", "claim")
    g.add_point("opY", None, is_operator=True)
    g.add_operator("op1", "IMPL", [("S1", 0), ("C1", 1), ("opY", 2)])
    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    assert _ids(sg.candidates) == ["C1"]


def test_nary_operator_does_not_fabricate_relation_between_targets():
    # P2 regression: a single operator with 3+ non-operator endpoints is
    # S idx=0 (source), T1 idx=1, T2 idx=2 (both targets). Traversing from T1
    # must emit the real edge S IMPLIES T1 and must NOT fabricate
    # T2 IMPLIES T1 — both are targets, so no edge exists between them. The
    # sibling target is not even a candidate reached from T1.
    g = FakeGraph()
    g.add_point("S", "source")
    g.add_point("T1", "target one")
    g.add_point("T2", "target two")
    g.add_operator("op", "IMPL", [("S", 0), ("T1", 1), ("T2", 2)])
    sg = build_subgraph_from_seeds(g, [("T1", 1.0)])
    rels = {(r.source_id, r.relation, r.target_id) for r in sg.relations}
    assert rels == {("S", "IMPL", "T1")}
    assert ("T2", "IMPL", "T1") not in rels
    assert ("T1", "IMPL", "T2") not in rels
    assert not any("T2" in (r.source_id, r.target_id) for r in sg.relations)
    assert _ids(sg.candidates) == ["S"]


@pytest.mark.parametrize(
    ("anchor", "expected"),
    [
        # From the source: both targets are real outgoing edges.
        ("S", {("S", "IMPL", "T1"), ("S", "IMPL", "T2")}),
        # From a target: only the source link is real; the sibling target must
        # never be emitted in either direction.
        ("T1", {("S", "IMPL", "T1")}),
        ("T2", {("S", "IMPL", "T2")}),
    ],
)
def test_nary_operator_edges_always_run_source_to_target(anchor, expected):
    # Frozen semantics: r.idx == 0 is the SOURCE, every idx > 0 a TARGET, and
    # the only real edges are source → target, regardless of which endpoint
    # the anchor is.
    g = FakeGraph()
    g.add_point("S", "source")
    g.add_point("T1", "target one")
    g.add_point("T2", "target two")
    g.add_operator("op", "IMPL", [("S", 0), ("T1", 1), ("T2", 2)])
    sg = build_subgraph_from_seeds(g, [(anchor, 1.0)])
    rels = {(r.source_id, r.relation, r.target_id) for r in sg.relations}
    assert rels == expected


# ── 1-hop vs hub-mediated 2-hop ───────────────────────────────────────────


def test_one_hop_and_hub_mediated_two_hop():
    g = FakeGraph()
    g.add_point("S1", "seed claim")
    g.add_point("C1", "direct implication")
    g.add_point("C2", "co-mentioned sibling")
    g.add_object("O1", "Rovo")
    g.add_operator("op1", "IMPL", [("S1", 0), ("C1", 1)])
    g.link_about("S1", "O1")
    g.link_about("C2", "O1")

    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    assert sg.anchors == ("S1",)
    assert set(_ids(sg.candidates)) == {"C1", "C2"}

    by_id = _by_id(sg)
    assert by_id["C1"].hop == 1 and by_id["C1"].edge_type == "IMPL"
    assert by_id["C2"].hop == 2 and by_id["C2"].edge_type == "aboutObject"
    # The hub entity is never a candidate claim.
    assert "O1" not in by_id
    # aboutObject relation carries the entity display name.
    assert any(
        r.source_id == "S1"
        and r.relation == "aboutObject"
        and r.target_id == "O1"
        and r.target_label == "Rovo"
        for r in sg.relations
    )


def test_no_general_second_hop():
    # S1 → C1 (IMPL); C1 → C3 (IMPL). C3 is a 2nd hop through a CLAIM, not a
    # hub, so it must NOT be reached.
    g = FakeGraph()
    g.add_point("S1", "seed")
    g.add_point("C1", "one hop")
    g.add_point("C3", "two hops through a claim")
    g.add_operator("op1", "IMPL", [("S1", 0), ("C1", 1)])
    g.add_operator("op2", "IMPL", [("C1", 0), ("C3", 1)])
    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    assert _ids(sg.candidates) == ["C1"]


# ── hop decay + damping integration ───────────────────────────────────────


def test_hop_decay_and_hub_damping_in_candidates():
    g = FakeGraph()
    g.add_point("S1", "seed")
    g.add_point("C1", "impl")
    g.add_point("C2", "sibling")
    g.add_object("O1", "Entity")
    g.set_degree("O1", 3)
    g.add_operator("op1", "IMPL", [("S1", 0), ("C1", 1)])
    g.link_about("S1", "O1")
    g.link_about("C2", "O1")

    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    by_id = _by_id(sg)

    # 1-hop: raw == score, undamped.
    assert by_id["C1"].raw_score == pytest.approx(0.7)
    assert by_id["C1"].score == pytest.approx(0.7)
    assert by_id["C1"].damped is False

    # 2-hop: raw == s_norm * aboutObject * decay; score is damped below raw.
    assert by_id["C2"].raw_score == pytest.approx(0.5)
    assert by_id["C2"].damped is True
    assert by_id["C2"].score < by_id["C2"].raw_score


# ── cap → dedupe, dedupe keeps highest, cap slot is not freed ─────────────


def _cap_graph() -> FakeGraph:
    g = FakeGraph()
    g.add_point("S1", "low seed")
    g.add_point("S2", "mid seed")
    g.add_point("S3", "high seed")
    for i in range(1, 14):
        g.add_point(f"n{i:02d}", f"claim {i}")
    # S1 (s_norm 0.0) fans out to 13 claims; S2 (s_norm 0.5) reaches n01 too.
    for i in range(1, 14):
        g.add_operator(f"op{i:02d}", "IMPL", [("S1", 0), (f"n{i:02d}", 1)])
    g.add_operator("opS2", "IMPL", [("S2", 0), ("n01", 1)])
    return g


def test_cap_then_dedupe_order_and_no_freed_cap_slot():
    g = _cap_graph()
    sg = build_subgraph_from_seeds(g, [("S1", 1.0), ("S2", 2.0), ("S3", 3.0)])
    ids = _ids(sg.candidates)

    # The per-anchor cap admitted 12 of S1's 13 normal candidates (byte-wise
    # ascending id: n01..n12); n13 is dropped and dedupe must NOT promote it.
    assert "n13" not in ids
    assert sorted(ids) == [f"n{i:02d}" for i in range(1, 13)]
    assert len(ids) == PER_ANCHOR_CAP

    # Dedupe kept the HIGHEST-scoring occurrence of n01 (from S2, s_norm 0.5).
    n01 = _by_id(sg)["n01"]
    assert n01.anchor_id == "S2"
    assert n01.s_norm == pytest.approx(0.5)
    assert n01.score == pytest.approx(0.5 * 0.7)


def test_dedupe_keeps_highest_score_not_highest_priority():
    # X reached from S2 (higher s_norm) via IMPL and from S1 (lower s_norm)
    # via the higher-priority aboutObject 2-hop — score decides.
    g = FakeGraph()
    g.add_point("S1", "low")
    g.add_point("S2", "high")
    g.add_point("S3", "high seed")
    g.add_point("X", "target")
    g.add_object("O1", "Entity")
    g.link_about("S1", "O1")
    g.link_about("X", "O1")
    g.add_operator("opX", "IMPL", [("S2", 0), ("X", 1)])
    sg = build_subgraph_from_seeds(g, [("S1", 1.0), ("S2", 2.0), ("S3", 3.0)])
    x = _by_id(sg)["X"]
    # S2 s_norm 0.5 → 0.35; S1 s_norm 0.0 → 0.0. Score wins.
    assert x.anchor_id == "S2"
    assert x.score == pytest.approx(0.35)


def test_dedupe_tie_breaks_on_edge_priority():
    # s_norm 0.8 * supersession(0.9) == s_norm 0.9 * NAND(0.8) == 0.72.
    g = FakeGraph()
    g.add_point("S0", "lowest")
    g.add_point("S_super", "a")
    g.add_point("S_nand", "b")
    g.add_point("S1", "highest")
    g.add_point("X", "target")
    g.add_corrects("S_super", "X")  # supersession (priority 3)
    g.add_operator("opN", "NAND", [("S_nand", 0), ("X", 1)])  # NAND (priority 2)
    sg = build_subgraph_from_seeds(
        g,
        [("S0", 0.0), ("S_super", 0.8), ("S_nand", 0.9), ("S1", 1.0)],
    )
    x = _by_id(sg)["X"]
    assert x.score == pytest.approx(0.72)
    assert x.edge_type == "supersession"
    assert x.anchor_id == "S_super"
    assert _ids(sg.candidates).count("X") == 1


# ── reserved endpoints do not consume a cap slot ──────────────────────────


def test_reserved_endpoints_do_not_consume_cap_slots():
    g = FakeGraph()
    g.add_point("S1", "seed")
    for i in range(1, 14):
        g.add_point(f"n{i:02d}", f"claim {i}")
        g.add_operator(f"op{i:02d}", "IMPL", [("S1", 0), (f"n{i:02d}", 1)])
    g.add_point("r1", "contradicted a")
    g.add_point("r2", "contradicted b")
    g.add_point("s_old", "superseded")
    g.add_point("s_new", "superseding")
    g.add_operator("opN1", "NAND", [("S1", 0), ("r1", 1)])
    g.add_operator("opN2", "NAND", [("S1", 0), ("r2", 1)])
    g.add_corrects("S1", "s_old")  # S1 supersedes s_old (CORRECTS out)
    g.add_corrects("s_new", "S1")  # s_new supersedes S1 (CORRECTS in)

    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    by_id = _by_id(sg)

    # 12 normal (cap) + 4 reserved (exempt) = 16.
    assert len(sg.candidates) == 16
    assert {"r1", "r2", "s_old", "s_new"} <= set(by_id)
    assert all(by_id[pid].reserved for pid in ("r1", "r2", "s_old", "s_new"))
    assert "n13" not in by_id  # cap still applies to the normal set only


def test_supersession_direction_follows_corrects():
    g = FakeGraph()
    g.add_point("S1", "new claim")
    g.add_point("s_old", "old claim")
    g.add_point("s_new", "newer claim")
    g.add_corrects("S1", "s_old")
    g.add_corrects("s_new", "S1")
    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    rels = {(r.source_id, r.relation, r.target_id) for r in sg.relations}
    assert ("S1", "supersession", "s_old") in rels
    assert ("s_new", "supersession", "S1") in rels


def test_point_reached_via_reserved_and_normal_edge_is_reserved():
    g = FakeGraph()
    g.add_point("S1", "seed")
    g.add_point("X", "both")
    g.add_operator("opI", "IMPL", [("S1", 0), ("X", 1)])
    g.add_operator("opN", "NAND", [("S1", 0), ("X", 1)])
    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    assert len(sg.candidates) == 1
    x = sg.candidates[0]
    assert x.point_id == "X"
    assert x.reserved is True
    assert x.edge_type == "NAND"  # higher-scoring occurrence wins the identity


def test_nand_endpoint_reserved_and_beats_impl_on_score():
    g = FakeGraph()
    g.add_point("S1", "seed")
    g.add_point("I", "implied")
    g.add_point("N", "contradicted")
    g.add_operator("opI", "IMPL", [("S1", 0), ("I", 1)])
    g.add_operator("opN", "NAND", [("S1", 0), ("N", 1)])
    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    by_id = _by_id(sg)
    assert by_id["N"].reserved is True and by_id["N"].score == pytest.approx(0.8)
    assert by_id["I"].reserved is False and by_id["I"].score == pytest.approx(0.7)


# ── in-network before out-of-network ──────────────────────────────────────


def test_in_network_admitted_before_out_of_network():
    g = FakeGraph()
    g.add_point("S_low", "seed 0.0")
    g.add_point("S_mid", "seed 0.2")
    g.add_point("S_high", "seed 1.0")
    g.add_point("C_impl", "direct but low-scored")
    g.add_point("C_hub", "hub-mediated but higher-scored")
    g.add_object("O1", "Entity")
    g.set_degree("O1", 1)
    g.add_operator("op1", "IMPL", [("S_mid", 0), ("C_impl", 1)])
    g.link_about("S_high", "O1")
    g.link_about("C_hub", "O1")

    sg = build_subgraph_from_seeds(g, [("S_low", 0.0), ("S_mid", 0.2), ("S_high", 1.0)])
    by_id = _by_id(sg)
    assert by_id["C_hub"].score > by_id["C_impl"].score
    # ...yet the in-network (typed-edge) candidate is admitted first.
    assert _ids(sg.candidates)[0] == "C_impl"
    assert _ids(sg.candidates)[1] == "C_hub"


def test_admission_tie_break_lower_point_id_first():
    g = FakeGraph()
    g.add_point("S1", "seed")
    for pid in ("b", "a", "c"):
        g.add_point(pid, f"claim {pid}")
        g.add_operator(f"op_{pid}", "IMPL", [("S1", 0), (pid, 1)])
    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    # Identical scores → byte-wise ascending point id.
    assert _ids(sg.candidates) == ["a", "b", "c"]


# ── relations keep only admitted endpoints ────────────────────────────────


def test_relations_to_dropped_candidates_are_pruned():
    g = FakeGraph()
    g.add_point("S1", "seed")
    for i in range(1, 15):
        g.add_point(f"c{i:02d}", f"claim {i}")
        g.add_operator(f"op{i:02d}", "IMPL", [("S1", 0), (f"c{i:02d}", 1)])
    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    assert "c13" not in _ids(sg.candidates)
    assert all(
        r.target_id in set(_ids(sg.candidates)) for r in sg.relations if r.relation != "aboutObject"
    )


# ── zero seeds / abstention ───────────────────────────────────────────────


def test_zero_seed_returns_empty_candidates():
    g = FakeGraph()
    sg = build_subgraph_from_seeds(g, [], seed_fn="bm25")
    assert sg.zero_seed is True
    assert sg.seeds == ()
    assert sg.anchors == ()
    assert sg.candidates == ()
    assert sg.relations == ()
    assert sg.reserved_overflow == 0
    assert sg.seed_fn == "bm25"


def test_zero_degree_graph_is_abstention_not_error():
    g = FakeGraph()
    g.add_point("S1", "seed with no edges")
    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    assert sg.zero_seed is False
    assert sg.anchors == ("S1",)
    assert sg.candidates == ()


# ── point_props: raw point metadata for the serializer (Track B) ──────────


def test_point_props_populated_for_anchors():
    g = FakeGraph()
    g.add_point(
        "S1",
        "seed claim",
        session_id="s11",
        createdAt="2023-05-06",
        validFrom="2023-05-07T00:00:00Z",
        source_turn_id="lme:q1:s11:t3",
        posterior_alpha=0.82,
        posterior_beta=0.18,
        ep_alpha=0.8,
        ep_beta=0.2,
        status="live",
    )
    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])

    assert set(sg.point_props) == {"S1"}
    props = sg.point_props["S1"]
    assert set(props) == set(subgraph._POINT_PROP_KEYS)
    assert props["id"] == "S1"
    assert props["content"] == "seed claim"
    assert props["is_operator"] is False
    assert props["session_id"] == "s11"
    assert props["createdAt"] == "2023-05-06"
    assert props["validFrom"] == "2023-05-07T00:00:00Z"
    assert props["source_turn_id"] == "lme:q1:s11:t3"
    assert props["posterior_alpha"] == 0.82
    assert props["posterior_beta"] == 0.18
    assert props["ep_alpha"] == 0.8
    assert props["ep_beta"] == 0.2
    assert props["status"] == "live"


def test_point_props_populated_for_candidates():
    g = FakeGraph()
    g.add_point("S1", "seed")
    g.add_point(
        "C1",
        "implied claim",
        session_id="s12",
        createdAt="2023-05-06T00:00:00Z",
        source_turn_id="lme:q1:s12:t7",
        posterior_alpha=0.7,
        posterior_beta=0.3,
    )
    g.add_operator("op1", "IMPL", [("S1", 0), ("C1", 1)])

    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    assert set(sg.point_props) == {"S1", "C1"}
    c1 = sg.point_props["C1"]
    assert c1["id"] == "C1"
    assert c1["content"] == "implied claim"
    assert c1["session_id"] == "s12"
    assert c1["createdAt"] == "2023-05-06T00:00:00Z"
    assert c1["source_turn_id"] == "lme:q1:s12:t7"
    assert c1["posterior_alpha"] == 0.7
    assert c1["posterior_beta"] == 0.3


def test_point_props_carried_for_hub_mediated_siblings():
    g = FakeGraph()
    g.add_point("S1", "seed")
    g.add_point("C2", "sibling", session_id="s9", ep_alpha=0.6, ep_beta=0.4)
    g.add_object("O1", "Entity")
    g.link_about("S1", "O1")
    g.link_about("C2", "O1")

    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    c2 = sg.point_props["C2"]
    assert c2["session_id"] == "s9"
    assert c2["ep_alpha"] == 0.6
    assert c2["ep_beta"] == 0.4


def test_point_props_carried_for_supersession_endpoints():
    g = FakeGraph()
    g.add_point("S1", "new claim")
    g.add_point("s_old", "old claim", session_id="s1")
    g.add_point("s_new", "newer claim", session_id="s2")
    g.add_corrects("S1", "s_old")
    g.add_corrects("s_new", "S1")

    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    assert sg.point_props["s_old"]["session_id"] == "s1"
    assert sg.point_props["s_new"]["session_id"] == "s2"


def test_point_props_omits_absent_properties_never_none():
    g = FakeGraph()
    g.add_point("S1", "seed claim", session_id="s11")
    g.add_point("C1", "implied claim")  # carries no provenance/EP state
    g.add_operator("op1", "IMPL", [("S1", 0), ("C1", 1)])

    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    c1 = sg.point_props["C1"]
    for key in (
        "session_id",
        "createdAt",
        "validFrom",
        "source_turn_id",
        "posterior_alpha",
        "posterior_beta",
        "ep_alpha",
        "ep_beta",
        "status",
    ):
        assert key not in c1, f"{key} must be omitted, not set to None"
    # Omission, not presence-with-None.
    assert all(value is not None for value in c1.values())
    # The properties the graph does carry are still there.
    assert c1["id"] == "C1"
    assert c1["content"] == "implied claim"
    assert c1["is_operator"] is False
    assert sg.point_props["S1"]["session_id"] == "s11"


def test_point_props_empty_for_zero_seed():
    sg = build_subgraph_from_seeds(FakeGraph(), [])
    assert sg.point_props == {}


def test_subgraph_is_constructible_without_point_props():
    sg = subgraph.Subgraph(
        seeds=(),
        anchors=(),
        candidates=(),
        relations=(),
        zero_seed=True,
        reserved_overflow=0,
        seed_fn="vector",
    )
    assert sg.point_props == {}


def test_point_props_covers_only_admitted_points():
    g = _cap_graph()
    sg = build_subgraph_from_seeds(g, [("S1", 1.0), ("S2", 2.0), ("S3", 3.0)])
    admitted = set(sg.anchors) | {c.point_id for c in sg.candidates}
    assert set(sg.point_props) == admitted
    # n13 lost the cap in S1's block and dedupe must not resurrect its props.
    assert "n13" not in sg.point_props


def test_point_props_agrees_with_content_by_id_for_admitted_claims():
    g = _operator_graph()
    sg = build_subgraph_from_seeds(g, [("S1", 1.0)])
    for pid, text in sg.content_by_id.items():
        assert sg.point_props[pid]["content"] == text


# ── seed fetch seam (vector + BM25 fallback) ──────────────────────────────


def test_fetch_seeds_uses_vector_search_at_limit_64(monkeypatch):
    import tools.longmem_eval.retrieve as retrieve

    seen: dict = {}

    def fake_vector_search(sdk, question, limit):
        seen["question"] = question
        seen["limit"] = limit
        return [("v1", 1.0)]

    monkeypatch.setattr(retrieve, "vector_search", fake_vector_search)
    seeds, seed_fn = subgraph._fetch_seeds(object(), FakeGraph(), "hello")
    assert seed_fn == "vector"
    assert seeds == [("v1", 1.0)]
    assert SEED_LIMIT == 64
    assert seen == {"question": "hello", "limit": 64}


def test_fetch_seeds_falls_back_to_bm25_on_model_encode_failure(monkeypatch):
    import tools.longmem_eval.retrieve as retrieve
    import tortoise.search_engine as search_engine

    def boom(*args, **kwargs):
        raise retrieve.ModelEncodeFailedError("no embedding-bearing points")

    monkeypatch.setattr(retrieve, "vector_search", boom)
    monkeypatch.setattr(
        search_engine,
        "run_fts_query",
        lambda graph, q, **kwargs: [("b1", 2.0), ("b2", 1.0)],
    )
    seeds, seed_fn = subgraph._fetch_seeds(object(), FakeGraph(), "hello")
    assert seed_fn == "bm25"
    assert seeds == [("b1", 2.0), ("b2", 1.0)]


# ── public entry point: build_subgraph(sdk, question, namespace=...) ────


class _FakeProj:
    def __init__(self, graph: FakeGraph) -> None:
        self.g = graph


class _FakeSDK:
    """Minimal SDK stand-in: only ``_namespace``/``_proj``/``_get_proj``."""

    def __init__(self, graph: FakeGraph, *, namespace: str | None = None) -> None:
        self._namespace = namespace
        self._proj = None
        self._graph = graph

    def _get_proj(self) -> _FakeProj:
        return _FakeProj(self._graph)


def test_build_subgraph_end_to_end_with_fake_sdk(monkeypatch):
    g = _operator_graph()
    sdk = _FakeSDK(g)
    monkeypatch.setattr(
        subgraph,
        "_fetch_seeds",
        lambda s, graph, question: ([("S1", 1.0)], "vector"),
    )
    sg = subgraph.build_subgraph(sdk, "When did I fly to Lisbon?")
    assert sg.anchors == ("S1",)
    assert sg.zero_seed is False
    assert _ids(sg.candidates) == ["C1"]
    assert sg.content_by_id["C1"] == "implied claim"


def test_build_subgraph_binds_namespace_for_seeding_and_traversal(monkeypatch):
    g = _operator_graph()
    sdk = _FakeSDK(g, namespace="eval_ns")
    seen: list = []

    def fake_fetch(s, graph, question):
        seen.append(s._namespace)  # seeding sees the scoped namespace
        return [("S1", 1.0)], "vector"

    monkeypatch.setattr(subgraph, "_fetch_seeds", fake_fetch)
    sg = subgraph.build_subgraph(sdk, "q", namespace="scratch_ns")
    assert seen == ["scratch_ns"]
    assert sdk._namespace == "eval_ns"  # restored afterwards
    assert _ids(sg.candidates) == ["C1"]


# ── hard leakage rule: no gold field is ever read ─────────────────────────


def test_no_gold_token_appears_in_engine_source():
    source = _MODULE_PATH.read_text()
    for token in _FORBIDDEN_TOKENS:
        assert token not in source, f"forbidden gold token {token!r} in subgraph.py"


def test_no_gold_field_in_issued_cypher_or_params():
    g = _cap_graph()
    g.add_object("O1", "Entity")
    g.link_about("S1", "O1")
    sg = build_subgraph_from_seeds(g, [("S1", 1.0), ("S2", 2.0), ("S3", 3.0)])
    assert sg.candidates  # the traversal actually ran
    for cypher, params in g.issued:
        for token in _FORBIDDEN_TOKENS:
            assert token not in cypher
            for key in params:
                assert token not in key


def test_engine_ast_reads_no_gold_attribute_or_string(monkeypatch):
    """AST scan: no attribute name or non-docstring string is a gold field.

    Stronger than the substring scan — it would catch a gold read even if it
    were assembled at runtime from fragments.
    """
    tree = ast.parse(_MODULE_PATH.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    forbidden_fragments = ("has_answer", "answer_session", "session_index", "gold")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            for fragment in forbidden_fragments:
                assert fragment not in node.attr, node.attr
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            lowered = node.value.lower()
            for fragment in forbidden_fragments:
                assert fragment not in lowered, node.value
