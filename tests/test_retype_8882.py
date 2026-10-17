"""#8882 Phase 2 — the offline re-typing MECHANICS (``tortoise/retype.py``).

What this module pins, and what it deliberately does not:

* pins the mechanics: a curated ``:Point`` becomes the layer its *decision*
  says, the kind property relocates, the target layer's identity contract is
  satisfied, props that are provably wrong for the new layer are dropped, and
  ``dump_id`` — the identity edges reference — is never touched.
* pins the refusals: a partition hole, an off-vocabulary kind, a multi-label
  node, an operator re-typed off ``:Point``, a name-less ``:Object``.
* does NOT pin any classification quality. The labels are inputs here; whether
  the labeler is good is a different question with a different instrument
  (``tools/kind_eval.py``), and mixing them would make a labelling regression
  look like a mechanics regression.

The fixture is a hand-built ``tortoise-logical-dump-v1`` dict — the same shape
``tortoise.hosted_backup.dump_graph`` emits — so every test here is DB-free and
runs on any lane. ``test_output_is_restore_shaped`` closes the loop to the
loader by calling the loader's OWN primitives (``DUMP_FORMAT``,
``_sanitize_label``) rather than trusting this module's opinion.
"""

from __future__ import annotations

import copy
import json

import pytest

from tortoise.retype import (
    KEEP,
    NEEDS_DECISION,
    RetypeError,
    apply_plan,
    build_plan,
    index_dump,
    propose,
    retype,
)

# ── fixture: a curated graph, flattened into :Point, as #8882 describes ──────


def _node(dump_id: int, labels: list[str], **props) -> dict:
    return {"dump_id": dump_id, "labels": labels, "props": props}


def _dump() -> dict:
    nodes = [
        _node(
            1,
            ["Point"],
            id="pt-goal-1",
            pointKind="goal",
            content="Ship the alpha by November",
            embedding=[0.1, 0.2, 0.3],
        ),
        _node(
            2,
            ["Point"],
            id="pt-dec-1",
            pointKind="decision",
            content="Decided to keep the fleet on Pro (#4824)",
            embedding=[0.4, 0.5, 0.6],
        ),
        _node(
            3,
            ["Point"],
            id="pt-stmt-1",
            pointKind="statement",
            content="Retrieval is hybrid (keyword + dense)",
        ),
        _node(
            4,
            ["Point"],
            id="pt-obs-1",
            pointKind="observation",
            content="The extractor emits noise from conversations",
        ),
        _node(
            5,
            ["Point"],
            id="pt-vis-1",
            pointKind="vision",
            content="A graph that reasons about its own beliefs",
        ),
        _node(
            6,
            ["Point"],
            id="pt-op-1",
            pointKind="statement",
            content="NAND operator",
            is_operator=True,
        ),
        _node(
            7,
            ["Point"],
            id="pt-pack-1",
            pointKind="customPack:widget",
            content="A pack-declared kind",
        ),
        _node(
            8, ["Point", "Object"], id="pt-multi-1", pointKind="goal", content="Multi-label node"
        ),
        _node(9, ["Object"], id="obj-1", objectKind="goal", name="Alpha"),
        _node(10, ["Subject"], id="sub-1", subjectKind="organization", name="Premise Labs"),
        _node(11, ["Event"], eventId="evt-1", eventKind="decision", name="Pro tier decision"),
        _node(12, ["Source"], id="src-1", url="https://example.test/doc", sourceKind="web"),
        _node(13, ["Tag"], name="alpha"),
    ]
    edges = [
        {"src": 3, "dst": 1, "type": "IMPL", "props": {"weight": 0.8}},
        {"src": 6, "dst": 3, "type": "NAND", "props": {}},
        {"src": 1, "dst": 9, "type": "aboutObject", "props": {}},
        {"src": 11, "dst": 9, "type": "produces", "props": {}},
        {"src": 3, "dst": 13, "type": "TAGGED", "props": {}},
    ]
    return {
        "format": "tortoise-logical-dump-v1",
        "dump_revision": 2,
        "dumped_at": "2026-10-17T00:00:00+00:00",
        "graph_name": "local-curated",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "source_node_count": len(nodes),
        "source_edge_count": len(edges),
        "read_edge_count": len(edges),
        "excluded_node_count": 0,
        "skipped_edge_count": 0,
        "unresolved_edge_count": 0,
        "nodes": nodes,
        "edges": edges,
        "event_meta": {"first_seq": 1, "last_seq": 42},
    }


def _keys(dump: dict) -> set[str]:
    return set(index_dump(dump))


def _all_keep(dump: dict, *, overrides: dict[str, dict] | None = None) -> dict:
    """A total decisions payload: everything keeps unless overridden."""
    decisions = []
    for key in sorted(_keys(dump)):
        row = {"key": key, "label": KEEP}
        row.update((overrides or {}).get(key) or {})
        decisions.append(row)
    return {"decisions": decisions}


GOAL_MOVE = {"label": "Object", "kind": "goal", "name": "Ship the alpha by November"}
DECISION_MOVE = {
    "label": "Event",
    "kind": "decision",
    "name": "Decided to keep the fleet on Pro (#4824)",
}


# ── propose (the labeler's starting point) ──────────────────────────────────


def test_propose_covers_every_node_and_leaves_no_silent_default():
    dump = _dump()
    rows = propose(dump)
    assert {row["key"] for row in rows} == _keys(dump)
    assert len(rows) == len(dump["nodes"])
    for row in rows:
        # every node is decided, or explicitly flagged — never omitted
        assert row["label"] in {"Point", "Object", "Subject", "Event", KEEP, NEEDS_DECISION}


def test_propose_applies_the_ontology_layer_rules():
    by_key = {row["key"]: row for row in propose(_dump())}
    assert by_key["pt-goal-1"]["label"] == "Object"
    assert by_key["pt-goal-1"]["kind"] == "goal"
    assert "STATE kind" in by_key["pt-goal-1"]["rule"]
    # a Point's first line is the PROPOSED name for a name-keyed layer
    assert by_key["pt-goal-1"]["name"] == "Ship the alpha by November"
    assert by_key["pt-dec-1"]["label"] == "Event"
    assert by_key["pt-dec-1"]["kind"] == "decision"
    # an Event needs a name: its FULLTEXT index is (subject, name)
    assert by_key["pt-dec-1"]["name"].startswith("Decided to keep")
    # observation folds into the logic layer's one kind
    assert by_key["pt-obs-1"]["label"] == "Point"
    assert by_key["pt-obs-1"]["kind"] == "statement"


def test_propose_flags_vision_because_no_object_kind_exists():
    """ONTOLOGY prose says vision is a STATE kind → Object, but the §5 object
    vocabulary has no `vision` member. Minting one is the off-vocabulary-kind
    defect class, so the proposal refuses to guess and says why."""
    row = {r["key"]: r for r in propose(_dump())}["pt-vis-1"]
    assert row["label"] == NEEDS_DECISION
    assert "CANONICAL_OBJECT_KINDS" in row["why"]


def test_propose_keeps_operators_already_typed_nodes_and_non_epistemic_nodes():
    by_key = {row["key"]: row for row in propose(_dump())}
    assert by_key["pt-op-1"]["label"] == KEEP
    assert "operator" in by_key["pt-op-1"]["why"]
    assert by_key["pt-stmt-1"]["label"] == KEEP
    assert by_key["obj-1"]["label"] == KEEP
    assert by_key["src-1"]["label"] == KEEP
    # a :Tag carries no id/url — its key is its own name
    assert by_key["alpha"]["label"] == KEEP
    # a multi-label node has no single layer to re-type FROM
    assert by_key["pt-multi-1"]["label"] == NEEDS_DECISION


def test_propose_is_deterministic_and_reproduces_the_same_partition_twice():
    dump = _dump()
    first = json.dumps(propose(dump), sort_keys=True)
    second = json.dumps(propose(copy.deepcopy(dump)), sort_keys=True)
    assert first == second


def test_propose_flags_a_point_kind_with_no_layer_rule():
    row = {r["key"]: r for r in propose(_dump())}["pt-pack-1"]
    assert row["label"] == NEEDS_DECISION
    assert "customPack:widget" in row["why"]


# ── invariant 1: the partition must be total ────────────────────────────────


def test_missing_decision_is_refused_naming_the_nodes():
    dump = _dump()
    decisions = _all_keep(dump)
    dropped = decisions["decisions"].pop(0)
    with pytest.raises(RetypeError) as exc:
        build_plan(dump, decisions)
    assert any("no decision" in p for p in exc.value.problems)
    assert dropped["key"] in " ".join(exc.value.problems)


def test_decision_for_an_unknown_key_is_refused():
    dump = _dump()
    decisions = _all_keep(dump)
    decisions["decisions"].append({"key": "pt-ghost", "label": KEEP})
    with pytest.raises(RetypeError) as exc:
        build_plan(dump, decisions)
    assert any("pt-ghost" in p for p in exc.value.problems)


def test_duplicate_decision_is_refused():
    dump = _dump()
    decisions = _all_keep(dump)
    decisions["decisions"].append({"key": "pt-goal-1", "label": KEEP})
    with pytest.raises(RetypeError) as exc:
        build_plan(dump, decisions)
    assert any("duplicate decision" in p for p in exc.value.problems)


def test_needs_decision_row_blocks_and_carries_its_reason():
    dump = _dump()
    rows = propose(dump)
    with pytest.raises(RetypeError) as exc:
        build_plan(dump, {"decisions": rows})
    joined = " ".join(exc.value.problems)
    assert "still needs a decision" in joined
    # the reason travels: the vision/object-kind divergence is legible
    assert "CANONICAL_OBJECT_KINDS" in joined


# ── invariants 2/3/5: only a single-labelled non-operator :Point moves ──────


def test_a_non_point_node_cannot_be_moved():
    dump = _dump()
    with pytest.raises(RetypeError) as exc:
        build_plan(
            dump, _all_keep(dump, overrides={"obj-1": {"label": "Event", "kind": "decision"}})
        )
    assert any("only a flattened :Point may be re-typed" in p for p in exc.value.problems)


def test_a_multi_label_node_cannot_be_moved():
    dump = _dump()
    with pytest.raises(RetypeError) as exc:
        build_plan(
            dump,
            _all_keep(
                dump, overrides={"pt-multi-1": {"label": "Object", "kind": "goal", "name": "x"}}
            ),
        )
    assert any("multiple epistemic labels" in p for p in exc.value.problems)


def test_an_operator_node_stays_a_point():
    dump = _dump()
    with pytest.raises(RetypeError) as exc:
        build_plan(
            dump, _all_keep(dump, overrides={"pt-op-1": {"label": "Event", "kind": "decision"}})
        )
    assert any("operator node" in p for p in exc.value.problems)


# ── invariant 4: kind legality (no second door for off-vocabulary kinds) ────


def test_off_vocabulary_kind_is_refused():
    dump = _dump()
    with pytest.raises(RetypeError) as exc:
        build_plan(
            dump, _all_keep(dump, overrides={"pt-goal-1": {"label": "Object", "kind": "statement"}})
        )
    assert any("not in the Object vocabulary" in p for p in exc.value.problems)


def test_a_pack_kind_must_be_named_explicitly():
    dump = _dump()
    overrides = {"pt-pack-1": {"label": "Object", "kind": "customPack:widget", "name": "Widget"}}
    with pytest.raises(RetypeError):
        build_plan(dump, _all_keep(dump, overrides=overrides))
    plan = build_plan(
        dump, _all_keep(dump, overrides=overrides), extra_kinds={"Object": ["customPack:widget"]}
    )
    assert [m.key for m in plan.moves] == ["pt-pack-1"]


def test_a_target_kind_is_required():
    dump = _dump()
    with pytest.raises(RetypeError) as exc:
        build_plan(dump, _all_keep(dump, overrides={"pt-goal-1": {"label": "Object", "name": "x"}}))
    assert any("needs an explicit `kind`" in p for p in exc.value.problems)


# ── invariant 6: the target layer's identity contract ───────────────────────


def test_object_or_subject_without_a_name_is_refused():
    dump = _dump()
    with pytest.raises(RetypeError) as exc:
        build_plan(
            dump, _all_keep(dump, overrides={"pt-goal-1": {"label": "Object", "kind": "goal"}})
        )
    assert any("needs a `name`" in p for p in exc.value.problems)


def test_point_to_event_derives_event_id_from_id():
    dump = _dump()
    out, report = retype(dump, _all_keep(dump, overrides={"pt-dec-1": DECISION_MOVE}))
    node = next(n for n in out["nodes"] if n["props"].get("eventId") == "pt-dec-1")
    assert node["labels"] == ["Event"]
    assert node["props"]["eventKind"] == "decision"
    assert node["props"]["id"] == "pt-dec-1"  # the id is preserved too
    assert node["props"]["name"] == DECISION_MOVE["name"]
    assert "pointKind" not in node["props"]
    # an Event keeps its vector: only name-keyed layers re-derive from `name`
    assert node["props"]["embedding"] == [0.4, 0.5, 0.6]
    assert report["derived"]["eventId_from_id"] == 1


def test_event_target_requires_a_name_for_the_fulltext_leg():
    dump = _dump()
    with pytest.raises(RetypeError) as exc:
        build_plan(
            dump, _all_keep(dump, overrides={"pt-dec-1": {"label": "Event", "kind": "decision"}})
        )
    assert any("FULLTEXT index is (subject, name)" in p for p in exc.value.problems)


# ── the mechanics: label swap, kind relocation, stale-prop removal ──────────


def test_point_to_object_relocates_the_kind_prop_and_drops_the_stale_vector():
    dump = _dump()
    out, report = retype(dump, _all_keep(dump, overrides={"pt-goal-1": GOAL_MOVE}))
    node = next(n for n in out["nodes"] if n["props"]["id"] == "pt-goal-1")
    assert node["labels"] == ["Object"]
    assert node["props"]["objectKind"] == "goal"
    assert "pointKind" not in node["props"]
    assert node["props"]["name"] == "Ship the alpha by November"
    # #7845: an :Object's vector is encoded FROM name → the Point's
    # content-derived vector is text the node no longer holds (#8419)
    assert "embedding" not in node["props"]
    assert report["dropped"]["embedding"] == 1
    assert any("lost their `embedding`" in w for w in report["warnings"])
    # everything else is preserved verbatim
    assert node["props"]["content"] == "Ship the alpha by November"


def test_keep_embedding_carries_the_vector_when_asked():
    dump = _dump()
    out, report = retype(
        dump, _all_keep(dump, overrides={"pt-goal-1": GOAL_MOVE}), keep_embedding=True
    )
    node = next(n for n in out["nodes"] if n["props"]["id"] == "pt-goal-1")
    assert node["props"]["embedding"] == [0.1, 0.2, 0.3]
    assert report["dropped"]["embedding"] == 0


def test_is_episodic_is_dropped_on_a_state_object():
    dump = _dump()
    for node in dump["nodes"]:
        if node["props"].get("id") == "pt-goal-1":
            node["props"]["is_episodic"] = True
    out, report = retype(dump, _all_keep(dump, overrides={"pt-goal-1": GOAL_MOVE}))
    node = next(n for n in out["nodes"] if n["props"]["id"] == "pt-goal-1")
    assert "is_episodic" not in node["props"]
    assert report["dropped"]["is_episodic"] == 1


def test_point_to_point_is_a_kind_fold_not_a_layer_move():
    dump = _dump()
    out, report = retype(
        dump, _all_keep(dump, overrides={"pt-obs-1": {"label": "Point", "kind": "statement"}})
    )
    node = next(n for n in out["nodes"] if n["props"]["id"] == "pt-obs-1")
    assert node["labels"] == ["Point"]
    assert node["props"]["pointKind"] == "statement"
    assert report["moves_by_layer"] == {"Point->Point": 1}


def test_a_no_op_decision_counts_as_a_keep():
    dump = _dump()
    out, report = retype(
        dump, _all_keep(dump, overrides={"pt-stmt-1": {"label": "Point", "kind": "statement"}})
    )
    assert report["moves_total"] == 0
    assert report["keeps"] == len(dump["nodes"])
    assert out["nodes"] == dump["nodes"]


# ── invariants 7: counts reconcile, ids and edges survive ───────────────────


def test_dump_ids_edges_and_bookkeeping_are_preserved():
    dump = _dump()
    out, report = retype(
        dump, _all_keep(dump, overrides={"pt-goal-1": GOAL_MOVE, "pt-dec-1": DECISION_MOVE})
    )
    assert [n["dump_id"] for n in out["nodes"]] == [n["dump_id"] for n in dump["nodes"]]
    assert out["edges"] == dump["edges"]
    for field in ("format", "dump_revision", "dumped_at", "graph_name", "event_meta"):
        assert out[field] == dump[field]
    assert out["node_count"] == dump["node_count"]
    assert out["edge_count"] == dump["edge_count"]
    assert report["nodes_in"] == report["nodes_out"] == len(dump["nodes"])
    assert report["edges_preserved"] == len(dump["edges"])


def test_the_report_counts_reconcile():
    dump = _dump()
    _out, report = retype(
        dump, _all_keep(dump, overrides={"pt-goal-1": GOAL_MOVE, "pt-dec-1": DECISION_MOVE})
    )
    assert report["moves_total"] + report["keeps"] == report["nodes_in"]
    assert sum(report["moves_by_layer"].values()) == report["moves_total"]
    assert sum(report["moves_by_kind"].values()) == report["moves_total"]
    assert report["moves_by_layer"] == {"Point->Event": 1, "Point->Object": 1}
    assert report["decisions_sha256"]


def test_retyping_is_pure_and_deterministic():
    dump = _dump()
    pristine = copy.deepcopy(dump)
    decisions = _all_keep(dump, overrides={"pt-goal-1": GOAL_MOVE})
    first, report_a = retype(dump, decisions)
    assert dump == pristine, "the input dump must not be mutated"
    second, report_b = retype(copy.deepcopy(dump), decisions)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert report_a == report_b


def test_epistemic_endpoints_are_surfaced_not_assumed_away():
    """Moving a Point that carries IMPL/NAND is legal (#7813) but the residual
    :Point-only EP reads (#7927) stop seeing it — the report says so."""
    dump = _dump()
    _out, report = retype(dump, _all_keep(dump, overrides={"pt-goal-1": GOAL_MOVE}))
    assert report["epistemic_endpoint_moves"]["count"] == 1
    assert report["epistemic_endpoint_moves"]["nodes"] == ["pt-goal-1"]
    assert any("#7927" in w for w in report["warnings"])


# ── the loop back to the loader ─────────────────────────────────────────────


def test_output_is_restore_shaped():
    """``restore_graph`` is the Phase-4 loader; a re-typed dump must satisfy
    the preconditions it enforces. Asserted with the loader's OWN primitives so
    this cannot drift into an opinion of its own."""
    from tortoise.hosted_backup import DUMP_FORMAT, _sanitize_label

    dump = _dump()
    out, _report = retype(
        dump, _all_keep(dump, overrides={"pt-goal-1": GOAL_MOVE, "pt-dec-1": DECISION_MOVE})
    )
    assert out["format"] == DUMP_FORMAT
    for node in out["nodes"]:
        assert isinstance(node["dump_id"], int)
        assert isinstance(node["props"], dict)
        assert "__dump_id" not in node["props"]
        for label in node["labels"]:
            assert _sanitize_label(label) == label
    present = {node["dump_id"] for node in out["nodes"]}
    for edge in out["edges"]:
        assert _sanitize_label(edge["type"]) == edge["type"]
        assert edge["src"] in present and edge["dst"] in present


def test_end_to_end_propose_then_apply_on_a_reviewed_file():
    """The documented flow: propose → a labeler edits → apply."""
    dump = _dump()
    rows = propose(dump)
    for row in rows:
        if row["key"] == "pt-multi-1":
            # a multi-label node has no single layer to re-type from; the only
            # honest decision is to keep it as it is
            row.update({"label": KEEP, "why": "reviewed: leave the labels"})
        if row["key"] == "pt-vis-1":
            row.update(
                {"label": "Point", "kind": "statement", "why": "reviewed: kept in the logic layer"}
            )
        if row["key"] == "pt-pack-1":
            row.update({"label": "Object", "kind": "customPack:widget", "name": "Widget"})
    plan = build_plan(dump, {"decisions": rows}, extra_kinds={"Object": ["customPack:widget"]})
    out, report = apply_plan(dump, plan)
    moved = {m.key for m in plan.moves}
    assert moved == {"pt-goal-1", "pt-dec-1", "pt-obs-1", "pt-vis-1", "pt-pack-1"}
    assert report["keeps"] == len(dump["nodes"]) - len(moved)
    assert len(out["nodes"]) == len(dump["nodes"])


# ── the CLI contract (refusal must not write a partial artifact) ────────────


def test_cli_propose_then_apply_roundtrip(tmp_path):
    from tools.retype_snapshot import main

    dump_path = tmp_path / "snapshot.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    proposals = tmp_path / "decisions.json"
    assert main(["--dump", str(dump_path), "--propose", str(proposals)]) == 0

    # unmapped decisions refuse, and nothing is written
    out_path = tmp_path / "retyped.json"
    assert (
        main(["--dump", str(dump_path), "--decisions", str(proposals), "--out", str(out_path)]) == 2
    )
    assert not out_path.exists()

    rows = json.loads(proposals.read_text(encoding="utf-8"))["decisions"]
    for row in rows:
        if row["label"] == NEEDS_DECISION:
            row.update({"label": KEEP, "why": "reviewed: left as-is for this pass"})
    proposals.write_text(json.dumps({"decisions": rows}), encoding="utf-8")

    report_path = tmp_path / "report.json"
    assert (
        main(
            [
                "--dump",
                str(dump_path),
                "--decisions",
                str(proposals),
                "--out",
                str(out_path),
                "--report",
                str(report_path),
            ]
        )
        == 0
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["nodes_in"] == report["nodes_out"] == len(_dump()["nodes"])
    assert json.loads(out_path.read_text(encoding="utf-8"))["nodes"]


def test_a_plan_built_for_another_snapshot_cannot_apply():
    """Two snapshots of the same size must not be interchangeable: dump_id is
    positional, so a plan from the wrong one would otherwise apply nothing and
    look like a clean run."""
    dump = _dump()
    other = copy.deepcopy(dump)
    for node in other["nodes"]:  # shift every internal id
        node["dump_id"] += 100
    plan = build_plan(dump, _all_keep(dump, overrides={"pt-goal-1": GOAL_MOVE}))
    with pytest.raises(RetypeError) as exc:
        apply_plan(other, plan)
    assert any("does not apply to this dump" in p for p in exc.value.problems)
