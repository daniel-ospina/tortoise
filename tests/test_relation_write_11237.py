"""#11237 — the relation WRITE leg's PLAN guards, on the surface that gates a merge.

WHY THIS FILE EXISTS (and why it is not inside the eval lane)
-------------------------------------------------------------
`tortoise/relation_write.py` matches no `SOURCE_PATTERNS` row, so a change to it
alone takes `select()`'s `tortoise/` fallback → the **`core`** surface. The
graph-level half of this leg lives in
``tests/eval/write_path/test_write_path_relation_lane.py``, and `eval/**` is
``on_demand`` by an OWNER DECISION (#10194 decision A, 2026-10-27): *"the eval
subtrees do not gate a merge … they run on demand, never on a merge"*.

So a follow-up that weakened the module ALONE — deleting the `#11235` ambiguity
refusal, the `#10000` typed-only rule, the declared-hierarchy branch or the fold
accounting — ran **none** of the assertions written for it (measured before this
file existed: `select(["tortoise/relation_write.py"])["test_files"]` did not
contain the eval lane, then nor after it was registered on `core` — the
`on_demand` subtraction runs AFTER surface selection, so an `eval/**` row can
never select that file: the #1349/#2938/#3332/#3616 silent-drop class).

⛔ The remedy is NOT to promote the eval lane onto a merge leg: that reverses
#10194. It is to keep the **pure-plan** guards — no graph, no SDK, no corpus,
no model — on the surface the module belongs to, and leave the product-number
lane on demand. Every case here is a pure function of ``(relations,
entity_index, declared)`` or of a fake writer, so the fast gate can afford it.

SCOPE OF THE RECONCILIATION CLAIM
---------------------------------
``written + dropped + folded`` reconciles with the stated population for the
case the review named: two DIFFERENT stated verbs binding one mechanism between
one pair (``create_direct_edge`` is one edge per ``(src, tgt, type)``). A
literal repeat of the SAME structural statement is skipped by the plan's own
``seen`` key and is NOT counted as a folded meaning — it is the same edge and
no meaning was lost. That case cannot arrive from the real producer either:
``extractor_v2._assemble_relations`` dedups on
``(src, dst, predicate.casefold())`` upstream (``sdk.py``'s leg is the second
line of defence, not the first).
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.pack_hierarchy import DeclaredHierarchy
from tortoise.relation_write import apply_relation_edges, plan_relation_edges

INDEX = {
    "retry storm": [("obj-1", "occurrence")],
    "deadline slip": [("obj-2", "occurrence")],
}


class _RefusingWriter:
    """An SDK whose one linking verb refuses every call."""

    def link_entities(self, *_a, **_kw):
        raise RuntimeError("graph refused")


class _RecordingWriter:
    """An SDK that accepts every call and records it."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def link_entities(self, predicate, src, dst, **kw):
        self.calls.append((predicate, src, dst, kw))
        return {"ok": True}


def test_ambiguous_endpoint_is_refused_not_guessed():
    """⛔ ``#11235``: identity is ``(name, kind)`` — a collision is NOT resolved.

    Plan-level because the GRAPH cannot express this collision: ``create_entity``
    keys an Object by ``(name, label)``, so two OBJECT kinds under one name
    collapse onto one node. The ambiguity that survives a capture is a name
    written to TWO DIFFERENT LABELS (an Object and a Subject), and the leg must
    decline it rather than pick the one it happens to see — ``extractor_v2``
    already warns that such an endpoint is ambiguous; this is the write-side
    half that refuses to settle it by accident.

    Mutation: `if len(ids) != 1` → `if not ids` guesses through the collision.
    """
    index = {
        "lumen auth": [("obj-1", "organization"), ("subj-1", "organization")],
        "deadline slip": [("obj-2", "occurrence")],
    }
    edges, drops = plan_relation_edges(
        [{"src": "lumen auth", "dst": "deadline slip",
          "predicate": "related"}], index)
    assert edges == [], edges
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
    edges, drops = plan_relation_edges(
        [{"src": "quarry", "dst": "auth table", "predicate": "related"}],
        index, declared=declared)
    assert not drops, drops
    assert len(edges) == 1
    assert edges[0].predicate == "hasPart"
    assert edges[0].declared_by == "dev"
    assert edges[0].stated_predicate == "related"
    assert edges[0].write_path == "structural"


def test_two_meanings_over_one_pair_are_COUNTED_as_a_fold():
    """A landed edge's absorbed meaning is RECORDED, so the totals reconcile.

    ``create_direct_edge`` is one edge per ``(src, tgt, type)``, so ``causes``
    and ``supports`` over one pair are ONE ``IMPL`` edge and the second meaning
    cannot ride a label the first already carries. Reporting it as a DROP would
    call a meaning that landed lost; staying silent leaves ``written + dropped``
    short of the stated population with nothing naming the gap (#11548 review).

    Mutation: turn the fold into a bare ``continue`` and `folded` reads 0.
    """
    edges, drops = plan_relation_edges([
        {"src": "retry storm", "dst": "deadline slip", "predicate": "causes"},
        {"src": "retry storm", "dst": "deadline slip", "predicate": "supports"},
    ], INDEX)
    assert len(edges) == 1, edges
    assert drops == [], drops
    assert edges[0].predicate == "IMPL" and edges[0].label == "causes", edges
    assert edges[0].folded == ("supports",), edges[0]

    writer = _RecordingWriter()
    report = apply_relation_edges(
        writer,
        [{"src": "retry storm", "dst": "deadline slip", "predicate": "causes"},
         {"src": "retry storm", "dst": "deadline slip", "predicate": "supports"}],
        INDEX)
    assert report["written"] == 1, report
    assert report["dropped"] == 0, report
    assert report["folded"] == 1, report
    assert report["written"] + report["dropped"] + report["folded"] == 2, report
    fold, = report["collapsed"]
    assert (fold["src"], fold["dst"]) == ("retry storm", "deadline slip"), fold
    assert fold["folded"] == ["supports"], fold
    assert fold["kept"] == "causes", fold


def test_a_fold_is_recorded_only_when_its_edge_was_written():
    """A fold describes a WRITTEN edge — a refused edge must not report one.

    The fold record used to be built from the PLAN, so an edge the writer then
    refused produced ``written=0, dropped=1, folded=1``: the lost meaning
    described as safely absorbed, and one short on the loss count.

    Mutation: build ``collapsed`` from ``plan`` instead of from what landed.
    """
    report = apply_relation_edges(
        _RefusingWriter(),
        [{"src": "retry storm", "dst": "deadline slip", "predicate": "causes"},
         {"src": "retry storm", "dst": "deadline slip", "predicate": "supports"}],
        INDEX, warn=lambda _w: None)
    assert report["written"] == 0, report
    assert report["collapsed"] == [], report
    assert report["folded"] == 0, report


def test_a_writer_refusal_names_the_pair_and_the_verb_the_story_used():
    """A refusal's drop row carries the two NAMES and the STATED verb (#11548 P2).

    Plan-level drops have always carried ``(src, dst, predicate, reason)``. The
    WRITER's refusal arm passed the stated verb as ``src`` and an empty ``dst``
    and the mechanism as ``predicate``, so a receipt had two drop shapes and a
    reader could not tell which pair (or which stated verb) was lost.

    Mutation: back to ``(edge.stated_predicate, "", edge.predicate, …)``.
    """
    warnings: list[str] = []
    report = apply_relation_edges(
        _RefusingWriter(),
        [{"src": "retry storm", "dst": "deadline slip",
          "predicate": "causes"}],
        INDEX, warn=warnings.append)
    assert report["written"] == 0, report
    assert report["dropped"] == 1, report
    drop = report["drops"][0]
    assert (drop["src"], drop["dst"]) == ("retry storm", "deadline slip"), drop
    assert drop["predicate"] == "causes", drop
    assert "graph refused" in drop["reason"], drop
    assert any("not written" in w for w in warnings), warnings


def test_the_plan_consumes_only_the_typed_record():
    """⛔ #10000 (P0): no prose on the statement reaches an EDGE field.

    ``_assemble_relations`` already strips the record, but this leg is the LAST
    thing that can put a string on an Object→Object edge — so an uncleaned
    record (a caller, a replay, a future producer) must still not leak. Every
    string the plan produces is a typed token: an id, a graph predicate, a
    closed-vocabulary meaning, the story's own verb, or an endpoint NAME.

    Mutation: copy any unlisted key off the record onto ``RelationEdge``.
    """
    prose = "the retry storm ate the budget and then the deadline slipped again"
    edges, _drops = plan_relation_edges([{
        "src": "retry storm", "dst": "deadline slip", "predicate": "causes",
        "summary": prose, "_searchText": prose, "transcript": prose,
        "content": prose,
    }], INDEX)
    assert len(edges) == 1, edges
    edge = edges[0]
    for field in dataclasses.fields(edge):
        value = getattr(edge, field.name)
        for token in (value if isinstance(value, tuple) else (value,)):
            if isinstance(token, str):
                assert prose not in token, (field.name, token)
    # The field set is pinned so a NEW field is a deliberate decision rather
    # than a quiet channel for narrative text.
    assert {f.name for f in dataclasses.fields(edge)} == {
        "src_id", "dst_id", "predicate", "write_path", "declared_by",
        "stated_predicate", "mechanism", "label", "src_name", "dst_name",
        "folded",
    }


def test_a_predicate_link_entities_inverts_is_refused_not_written():
    """⛔ `wasDerivedFrom` is REFUSED structurally — `link_entities` INVERTS it.

    `link_entities("wasDerivedFrom", a, b)` routes to `create_derivation`, which
    stores `(b)-[:wasDerivedFrom]->(a)` — the opposite of what the story stated —
    while the structural arm's gate (`predicate in _VALID_EDGE_PREDICATES`)
    ADMITS it. Refusing it by name is the residual the #11548 review named: a
    stored-inverted edge is worse than a named drop.

    Mutation: drop the `_routed_away_reason` arm and the plan writes an
    inverted edge instead of a drop.
    """
    edges, drops = plan_relation_edges(
        [{"src": "retry storm", "dst": "deadline slip",
          "predicate": "wasDerivedFrom"}], INDEX)
    assert edges == [], edges
    assert len(drops) == 1, drops
    assert (drops[0].src, drops[0].dst) == ("retry storm", "deadline slip")
    assert "OPPOSITE" in drops[0].reason, drops[0].reason
