"""#11237 — the relation WRITE leg's PLAN guards, on the surface that gates a merge.

WHY THIS FILE EXISTS (and why it is not inside the eval lane)
-------------------------------------------------------------
`tortoise/relation_write.py` matches no `SOURCE_PATTERNS` row, so a change to it
alone takes `select()`'s `tortoise/` fallback → the **`core`** surface. The
graph-level half of this leg lives in
``tests/eval/write_path/test_write_path_relation_lane.py``, and `eval/**` is
``on_demand`` by an OWNER DECISION (#10194, decision A, 2026-10-27): *"the eval
subtrees do not gate a merge … they run on demand, never on a merge"*.

So a follow-up that weakened the module ALONE — deleting the `#11235` ambiguity
refusal, the `#10000` typed-only rule, the declared-hierarchy branch, the
collapse accounting, or the inverted-predicate refusal — ran **none** of the
assertions written for it (measured before this file existed: `select(
["tortoise/relation_write.py"]).test_files` did not contain the eval lane — the
#1349/#2938/#3332/#3616 silent-drop class). ⛔ The remedy is NOT to promote the
eval lane onto a merge leg: that reverses #10194. It is to keep the **pure-plan**
guards — no graph, no SDK, no corpus, no model — on the surface the module
belongs to, and leave the product-number lane on demand.

Every case here is a pure function of ``(relations, entity_index, declared)``,
so it is hermetic and cheap enough for the fast gate.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.pack_hierarchy import DeclaredHierarchy
from tortoise.relation_write import (
    apply_relation_edges,
    plan_relation_edges,
)

INDEX = {
    "retry storm": [("obj-1", "occurrence")],
    "deadline slip": [("obj-2", "occurrence")],
}


def test_ambiguous_endpoint_is_refused_not_guessed():
    """⛔ #11235: identity is ``(name, kind)`` — a collision is NOT resolved.

    Plan-level because the GRAPH cannot express this collision: ``create_entity``
    keys an Object by ``(name, label)``, so two OBJECT kinds under one name
    collapse onto one node. The ambiguity that survives a capture is a name
    written to TWO DIFFERENT LABELS (an Object and a Subject), and the leg must
    decline it rather than pick the one it happens to see — ``extractor_v2``
    already warns that such an endpoint is ambiguous; this is the write-side
    half that refuses to settle it by accident.
    """
    index = {
        "lumen auth": [("obj-1", "organization"), ("subj-1", "organization")],
        "deadline slip": [("obj-2", "occurrence")],
    }
    edges, drops, collapsed = plan_relation_edges(
        [{"src": "lumen auth", "dst": "deadline slip",
          "predicate": "related"}], index)
    assert edges == [] and collapsed == [], (edges, collapsed)
    assert len(drops) == 1
    assert "#11235" in drops[0].reason, drops[0].reason


def test_a_declared_hierarchy_fixes_the_predicate():
    """The DECLARED pair wins over the verb the story used (consumed, not inferred).

    ``#11001`` built ``pack_hierarchy.plan_hierarchy_edges`` for exactly this
    pairing and recorded that nothing called it. The relation leg is that call
    site: when the pack declares ``theme → epic`` on ``hasPart``, a story that
    merely says the two are ``related`` must land the DECLARED predicate — the
    declaration is the pack's own claim about how its levels relate.

    Mutation: delete the hierarchy branch and the plan writes ``related``.
    """
    declared = (DeclaredHierarchy(
        pack="dev", path="Theme \u2192 Epic", predicate="hasPart",
        from_kind="dev:theme", to_kind="dev:epic",
        write_path="structural"),)
    index = {
        "quarry": [("obj-theme", "dev:theme")],
        "auth table": [("obj-epic", "dev:epic")],
    }
    edges, drops, collapsed = plan_relation_edges(
        [{"src": "quarry", "dst": "auth table", "predicate": "related"}],
        index, declared=declared)
    assert not drops, drops
    assert not collapsed, collapsed
    assert len(edges) == 1
    assert edges[0].predicate == "hasPart"
    assert edges[0].declared_by == "dev"
    assert edges[0].stated_predicate == "related"
    assert edges[0].write_path == "structural"


def test_two_meanings_over_one_pair_are_COUNTED_as_a_collapse():
    """The accounting reconciles: ``written + dropped + collapsed == stated``.

    The DIRECT writer's edge identity is ``(mechanism, src, dst)`` — the stated
    verb is deliberately NOT in it — so ``causes`` and ``supports`` over one pair
    are ONE ``IMPL`` edge and the second meaning cannot ride a label the first
    already carries. Reporting that as a DROP would call a meaning that landed
    lost; staying silent (what this leg used to do) leaves ``written + dropped``
    short of the stated population with nothing naming the gap (#11237 review).

    Mutation: turn the collapse back into a bare ``continue`` and the third
    bucket stops reconciling.
    """
    edges, drops, collapsed = plan_relation_edges([
        {"src": "retry storm", "dst": "deadline slip", "predicate": "causes"},
        {"src": "retry storm", "dst": "deadline slip", "predicate": "supports"},
    ], INDEX)
    assert len(edges) == 1, edges
    assert edges[0].predicate == "IMPL" and edges[0].label == "causes", edges
    assert not drops, drops
    assert len(collapsed) == 1, collapsed
    fold = collapsed[0]
    assert (fold.src, fold.dst) == ("retry storm", "deadline slip"), fold
    assert (fold.predicate, fold.into) == ("supports", "IMPL"), fold
    assert len(edges) + len(drops) + len(collapsed) == 2


def test_the_same_stated_relation_twice_folds_rather_than_dropping():
    """A literal duplicate is a COLLAPSE too — same edge, nothing lost."""
    edges, drops, collapsed = plan_relation_edges([
        {"src": "retry storm", "dst": "deadline slip", "predicate": "causes"},
        {"src": "retry storm", "dst": "deadline slip", "predicate": "causes"},
    ], INDEX)
    assert len(edges) == 1 and not drops, (edges, drops)
    assert len(collapsed) == 1, collapsed
    assert (collapsed[0].predicate, collapsed[0].into) == ("causes", "IMPL"), \
        collapsed[0]


def test_a_duplicated_STRUCTURAL_relation_says_it_is_the_same_edge():
    """The structural key IS the predicate, so a duplicate is literally one edge.

    Its collapse record says so rather than reusing the "second meaning"
    sentence, which would misdescribe a repeat as a lost meaning.
    """
    edges, drops, collapsed = plan_relation_edges([
        {"src": "retry storm", "dst": "deadline slip", "predicate": "related"},
        {"src": "retry storm", "dst": "deadline slip", "predicate": "related"},
    ], INDEX)
    assert len(edges) == 1 and not drops, (edges, drops)
    assert len(collapsed) == 1, collapsed
    assert collapsed[0].predicate == collapsed[0].into == "related", collapsed[0]
    assert "appears twice" in collapsed[0].reason, collapsed[0].reason


def test_the_plan_consumes_only_the_typed_record():
    """⛔ #10000 (P0): no prose on the statement reaches an edge field.

    ``_assemble_relations`` already strips the record, but this leg is the LAST
    thing that can put a string on an Object→Object edge — so an uncleaned
    record (a caller, a replay, a future producer) must still not leak. Every
    string the plan produces is a typed token: an id, a graph predicate, a
    closed-vocabulary meaning, or the story's own verb.

    Mutation: copy any unlisted key off the record onto ``RelationEdge`` and the
    prose assertion reds.
    """
    prose = "the retry storm ate the budget and then the deadline slipped again"
    edges, _drops, _collapsed = plan_relation_edges([{
        "src": "retry storm", "dst": "deadline slip", "predicate": "causes",
        "summary": prose, "_searchText": prose, "transcript": prose,
        "content": prose,
    }], INDEX)
    assert len(edges) == 1, edges
    edge = edges[0]
    for field in dataclasses.fields(edge):
        value = getattr(edge, field.name)
        if isinstance(value, str):
            assert prose not in value, (field.name, value)
    assert {f.name for f in dataclasses.fields(edge)} == {
        "src_id", "dst_id", "predicate", "write_path", "declared_by",
        "stated_predicate", "src_name", "dst_name", "mechanism", "label",
    }, "the edge grew a field — re-check that it cannot carry narrative text"


def test_a_predicate_link_entities_inverts_is_refused_not_written():
    """⛔ `wasDerivedFrom` is REFUSED structurally — `link_entities` inverts it.

    `link_entities("wasDerivedFrom", a, b)` routes to `create_derivation`, which
    stores `(b)-[:wasDerivedFrom]->(a)` — the opposite of what the story stated —
    and the structural arm's gate (`predicate in _VALID_EDGE_PREDICATES`) ADMITS
    it. Refusing it by name is the residual the #11237 review named: a
    stored-inverted edge is worse than a named drop.
    """
    edges, drops, collapsed = plan_relation_edges(
        [{"src": "retry storm", "dst": "deadline slip",
          "predicate": "wasDerivedFrom"}], INDEX)
    assert edges == [] and collapsed == [], (edges, collapsed)
    assert len(drops) == 1, drops
    assert (drops[0].src, drops[0].dst) == ("retry storm", "deadline slip")
    assert "OPPOSITE" in drops[0].reason, drops[0].reason


class _RefusingWriter:
    """An SDK whose one linking verb refuses every call."""

    def link_entities(self, *_a, **_kw):
        raise RuntimeError("projection refused")


def test_a_writer_refusal_names_the_pair_it_refused():
    """A writer failure's drop record carries the SAME shape as every other.

    `RelationDrop` is ``(src, dst, predicate, reason)`` and every plan-level drop
    fills ``src``/``dst`` with the two stated endpoint NAMES. The writer arm used
    to fill ``src`` with the VERB and ``dst`` with ``""``, so the one bucket a
    reader most needs to identify a pair with could not identify it (#11237
    review). Asserted on the RECORD, never on the reason string.
    """
    report = apply_relation_edges(
        _RefusingWriter(),
        [{"src": "retry storm", "dst": "deadline slip",
          "predicate": "related"}], INDEX)
    assert report["written"] == 0 and report["dropped"] == 1, report
    assert report["written"] + report["dropped"] + report["collapsed"] \
        == report["considered"] == 1, report
    drop, = report["drops"]
    assert (drop["src"], drop["dst"]) == ("retry storm", "deadline slip"), drop
    assert drop["predicate"] == "related", drop
    assert "RuntimeError" in drop["reason"], drop


def test_a_collapse_is_reported_through_the_warnings_channel():
    """A fold reaches the caller: the leg's own rule is "never silent"."""
    warnings: list[str] = []
    report = apply_relation_edges(
        _RefusingWriter(),
        [{"src": "retry storm", "dst": "deadline slip", "predicate": "causes"},
         {"src": "retry storm", "dst": "deadline slip", "predicate": "supports"}],
        INDEX, warn=warnings.append)
    # Both statements reached the (refusing) writer? No — the second FOLDS before
    # any write, so exactly one write was attempted and refused, and the fold is
    # reported on its own line.
    assert report["written"] == 0 and report["dropped"] == 1, report
    assert report["collapsed"] == 1, report
    assert any("folded" in line for line in warnings), warnings
