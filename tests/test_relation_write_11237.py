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
``written + dropped + folded`` reconciles with the stated population on every
arm, including the two where it did not (#11548 round 3, P2): a second
DIFFERENT stated verb binding one edge (``create_direct_edge`` is one edge per
``(src, tgt, type)``; a declared HIERARCHY pair is one declared predicate) is a
recorded FOLD, and a refused edge that had absorbed a meaning reports one drop
per LOST MEANING. A literal repeat of the SAME statement is skipped by the
plan's own identity key and is NOT counted as a folded meaning — it is the same
edge and no meaning was lost. That case cannot arrive from the real producer
either: ``extractor_v2._assemble_relations`` dedups on
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


class _FalseWriter:
    """A writer that answers the bare ``bool`` rather than an envelope.

    ⛔ NOT the shape either production writer returns, and this docstring used to
    claim it was: ``link_entities`` reaches ``TortoiseSDK.create_edge`` on the
    structural arm, and that door WRAPS the projection's bool in
    ``{"edge": …, "created": created, "nudges": […]}``
    (``tortoise/sdk.py:49406-49412``) — so the bare ``False`` that
    ``Projection.create_edge`` returns (``tortoise/projection/edges.py:2919-2920``)
    never reaches this call site. It is kept as the SECOND channel (a writer that
    answers a plain bool is still a refusal, not a write); the REAL envelope is
    :class:`_StructuralRefusalWriter` (#11548 review, P1).
    """

    def link_entities(self, *_a, **_kw):
        return False


class _StructuralRefusalWriter:
    """The STRUCTURAL door's REAL refusal envelope — a write it did NOT make.

    ``TortoiseSDK.create_edge`` returns
    ``{"edge": …, "created": created, "nudges": […]}``
    (``tortoise/sdk.py:49406-49412``), so a projection that resolved no endpoint
    comes back as a TRUTHY dict with ``created: False``. An ``is False`` test on
    that result is dead, and the row was appended to ``written`` while the graph
    held no edge (#11548 review, P1).
    """

    def link_entities(self, *_a, **_kw):
        return {"edge": {"relation": "related", "from": "obj-1",
                         "to": "obj-2"},
                "created": False, "nudges": []}


class _DirectDedupWriter:
    """``create_direct_edge``'s MERGE dedup hit — ``created: False`` on an edge
    the graph DOES hold (``{"created": created, "deduped": not created}``,
    ``tortoise/sdk.py:29283-29284``).

    ⛔ The control that stops the structural fix from over-reaching: reading
    ``created is False`` on the DIRECT arm would report every idempotent
    re-capture as a refusal, which is the same fail-silent in the opposite
    direction (a written edge reported as uncommitted).
    """

    def link_entities(self, *_a, **_kw):
        return {"direct_edge": "IMPL", "from": "obj-1", "to": "obj-2",
                "created": False, "deduped": True}


def test_ambiguous_endpoint_is_refused_not_guessed():
    """⛔ ``#11235``: identity is ``(name, kind)`` — a collision is NOT resolved.

    Plan-level because the GRAPH cannot express this collision: ``create_entity``
    keys an Object by ``(name, label)``, so two OBJECT kinds under one name
    collapse onto one node. The ambiguity that survives a capture is a name
    written to TWO DIFFERENT LABELS (an Object and a Subject), and the leg must
    decline it rather than pick the one it happens to see — ``extractor_v2``
    already warns that such an endpoint is ambiguous; this is the write-side
    half that refuses to settle it by accident.

    Mutation: delete the `len(ids) > 1` arm of `_endpoint_reason` and the name
    collision is guessed through (executed: the plan then raises `TypeError`
    unpacking two ids, so the row REDs). ⛔ It is NOT the `len(ids) != 1` guard
    in `_entity_kind` — that guard could never fire, because `_endpoint_reason`
    refuses every failing input first, and naming it certified cover the row did
    not have (agent-infra#3442 rule 3; #11548 review F2). That dead guard is
    gone: `_entity_kind` now states the precondition instead of re-checking it.
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
        # #11548 review round 4: the direction the edge is WRITTEN with, on the
        # arm whose writer takes one. It is a closed-vocabulary token
        # (`->`/`<-`/`<->`/`-`), never prose — the loop above checks it like
        # every other string field, and this pin is what makes adding a field a
        # deliberate decision.
        "direction",
    }


def test_the_stated_direction_is_consumed_from_the_registry_map():
    """⛔ #11548 round 3 P1: a one-way statement is not stored bidirectional.

    ``apply_relation_edges`` used to call ``link_entities`` with NO direction, so
    the direct arm fell through to ``create_direct_edge``'s CYCLE-25 default —
    ``bidirectional`` for IMPL — and *"the retry storm causes the deadline
    slip"*, which ``SEMANTICS_DIRECTIONS["causes"]`` records as ``->``, landed
    as belief flowing back along a causal edge the narrative stated one way.
    ``direction_flows`` then propagated it both ways.

    The leg consumes the ONE declared map (never a table of its own), and the
    RULED value wins in every case including ``<->`` meanings — which is why a
    blanket one-way constant is not the fix (#11548 round 4, P1). The one-way
    token is reached only where NO candidate declares a direction: a bare
    ``IMPL``/``NAND`` verb, whose writer default would otherwise be
    ``bidirectional``.

    Mutation: drop ``direction=_direction_for(...)`` from the plan and the first
    three assertions read the writer default instead; force the fallback for
    every meaning and the ``<->`` case goes one-way.
    """
    plan, drops = plan_relation_edges([
        {"src": "retry storm", "dst": "deadline slip", "predicate": "causes"},
        {"src": "retry storm", "dst": "retry budget",
         "predicate": "mutuallySupportive"},
        {"src": "retry budget", "dst": "deadline slip", "predicate": "IMPL"},
        # The verb is not a declared meaning: its GLOSS is, and the gloss's
        # ruling must reach the edge — not the one-way fallback.
        {"src": "deadline slip", "dst": "retry storm", "predicate": "nuances",
         "semantics": "supports"},
        {"src": "deadline slip", "dst": "retry budget", "predicate": "resonates",
         "semantics": "mutuallyExclusive"},
    ], {**INDEX, "retry budget": [("obj-3", "occurrence")]})
    by_stated = {edge.stated_predicate: edge for edge in plan}
    assert by_stated["causes"].direction == "->", by_stated["causes"]
    assert by_stated["mutuallySupportive"].direction == "<->", by_stated
    assert by_stated["IMPL"].direction == "->", (
        "a bare mechanism declares no direction, so the one-way fallback (a "
        f"stated relation runs src → dst) applies, never the writer's "
        f"bidirectional default: {by_stated['IMPL']!r}")
    assert by_stated["nuances"].direction == "->", by_stated["nuances"]
    assert by_stated["resonates"].direction == "<->", (
        "the record's `semantics` gloss carries the RULED direction when the "
        f"verb is outside the vocabulary: {by_stated['resonates']!r}")
    assert not drops, drops

    writer = _RecordingWriter()
    apply_relation_edges(writer, [
        {"src": "retry storm", "dst": "deadline slip", "predicate": "causes"}],
        INDEX)
    _predicate, _src, _dst, kwargs = writer.calls[-1]
    assert kwargs.get("direction") == "->", writer.calls


def test_the_rows_own_declared_direction_outranks_the_meaning():
    """⛔ #11548 review P1: the census and the writer must agree about a ROW.

    :func:`pack_registry.effective_direction` exists because *"the census and
    the writer cannot disagree about a row"* (#11083 indicator (c)) — and this
    leg disagreed with it on every row that declares its OWN ``direction:``.
    ``_direction_for`` consulted the row's MEANING and skipped the row's token, so
    two shipped rows were written backwards against their own declaration:

      * ``blocks`` — ``packs/project-management: semantics: dependsOn,
        direction: "<-"`` → written ``->``
      * ``holds`` — ``packs/venture: mechanism: IMPL, direction: "<-"`` →
        written ``->``

    while ``tools/pack_relation_census.py`` reports ``<-`` for the same two rows.
    ONTOLOGY §3.6 carries the sense per declaration and §8's ``ownedBy`` row says
    a declared token outranks the default; for ``holds`` the inverted ``->``
    asserts *the company owns the investment* on a claim that states the reverse,
    which is the reading the venture row's own comment says it was added to
    prevent.

    Non-vacuity: with ``declared_direction`` skipped (the pre-fix lookup) both
    assertions read ``->`` — the meaning's ``dependsOn``/``ownedBy`` default —
    while ``effective_direction`` still says ``<-`` for each row.
    """
    from tortoise.pack_registry import declared_directions

    for predicate in ("blocks", "holds"):
        declared = declared_directions(label=predicate)
        assert declared, (
            f"the pack catalog must declare a direction for {predicate!r} — "
            "this case is vacuous without it")
        (ruled, _source), = declared
        edges, drops = plan_relation_edges([
            {"src": "retry storm", "dst": "deadline slip",
             "predicate": predicate}], INDEX)
        assert not drops, drops
        assert len(edges) == 1, edges
        assert edges[0].direction == ruled == "<-", (
            f"{predicate!r} declares {ruled!r} and the leg planned "
            f"{edges[0].direction!r} — the writer and the census disagree "
            "about the row, which is the one thing effective_direction "
            "exists to prevent")


def test_a_second_verb_on_a_declared_hierarchy_pair_is_a_COUNTED_fold():
    """A different verb over a declared pair is a FOLD, not a silent ``continue``.

    The structural arm keys ``structural_at`` on the DECLARED predicate, so
    ``related`` then ``supports`` over a pair the pack declares
    ``theme → epic hasPart`` both plan the same ``hasPart`` edge. The second one
    used to vanish through a bare ``continue``: ``written 1, dropped 0,
    folded 0`` for a population of **2**, with the second meaning nowhere. A
    literal REPEAT of the same verb is still the same statement and stays
    deduped (no fold) — the code can now tell the two apart.

    Mutation: back to ``if key in seen: continue`` for a different verb and
    ``folded`` reads 0 (the two-statement population stops reconciling).
    """
    declared = (DeclaredHierarchy(
        pack="dev", path="Theme \u2192 Epic", predicate="hasPart",
        from_kind="dev:theme", to_kind="dev:epic", write_path="structural"),)
    index = {"quarry": [("obj-theme", "dev:theme")],
             "auth table": [("obj-epic", "dev:epic")]}
    relations = [
        {"src": "quarry", "dst": "auth table", "predicate": "related"},
        {"src": "quarry", "dst": "auth table", "predicate": "supports"},
    ]
    plan, drops = plan_relation_edges(relations, index, declared=declared)
    assert not drops, drops
    assert len(plan) == 1, plan
    assert plan[0].predicate == "hasPart", plan[0]
    assert plan[0].folded == ("supports",), plan[0]

    report = apply_relation_edges(_RecordingWriter(), relations, index,
                                 declared=declared)
    assert report["written"] == 1, report
    assert report["dropped"] == 0, report
    assert report["folded"] == 1, report
    assert (report["written"] + report["dropped"]
            + report["folded"]) == 2, report
    assert [f["folded"] for f in report["collapsed"]] == [["supports"]], report

    # A LITERAL repeat is the same statement: deduped, not a lost meaning.
    literal, literal_drops = plan_relation_edges([
        {"src": "quarry", "dst": "auth table", "predicate": "related"},
        {"src": "quarry", "dst": "auth table", "predicate": "related"},
    ], index, declared=declared)
    assert len(literal) == 1, literal
    assert literal[0].folded == (), literal[0]
    assert literal_drops == [], literal_drops


def test_a_refused_edge_counts_the_meaning_it_was_carrying():
    """A refused fold is a LOSS, not a ``written=0, dropped=1`` two-statement hole.

    ``collapsed`` is recorded only for an edge that LANDED (correct — a fold must
    not be described as absorbed when the writer refused its carrier), so the
    refused arm used to report ``written 0, dropped 1, folded 0`` for a stated
    population of **2** with the absorbed verb appearing nowhere. One drop row
    per lost MEANING restores the invariant on that arm.

    Mutation: drop the ``for folded_verb in edge.folded`` loop and the counts no
    longer sum to the stated population.
    """
    report = apply_relation_edges(
        _RefusingWriter(),
        [{"src": "retry storm", "dst": "deadline slip",
          "predicate": "causes"},
         {"src": "retry storm", "dst": "deadline slip",
          "predicate": "supports"}],
        INDEX, warn=lambda _w: None)
    assert report["written"] == 0, report
    assert report["collapsed"] == [], report
    assert report["folded"] == 0, report
    # TWO statements, TWO named losses (the carrier AND the meaning it carried).
    assert report["dropped"] == 2, report
    assert (report["written"] + report["dropped"]
            + report["folded"]) == 2, report
    predicates = [row["predicate"] for row in report["drops"]]
    assert predicates == ["causes", "supports"], report
    assert all(row["dst"] == "deadline slip" for row in report["drops"]), report


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


def test_a_manifest_only_structural_predicate_names_the_writer_it_would_reach():
    """⛔ The refusal must name the REAL reason — a union set is not a writer.

    ``pack_registry.relation_write_paths`` answers for the **union** of three
    structural writers (``STRUCTURAL_WRITE_PREDICATES`` = ``create_edge`` +
    ``create_about_edge`` + ``ingest``), while this leg reaches exactly ONE of
    them — ``create_edge``, whose own set is ``_VALID_EDGE_PREDICATES``. The two
    sets differ by exactly ``aboutPoint`` and ``extractedFrom`` (measured), so
    those two reached the final drop arm with ``paths = ('structural',)`` and
    were told they bind *"only the operator path (('structural',))"* — a
    sentence that contradicts its own parenthetical. A reader of that row goes
    looking for an operator to mint when the actual state is "a different
    structural writer exists, and this leg does not call it"
    (``docs/evidence/7949-capstone/RECONCILE-A1-A2-A5.md:149`` records what a
    wrong cause here costs: a lane sent off to build a writer the graph already
    has).

    Mutation: reuse the operator-path sentence for this state (the pre-fix
    shape) and ``"create_edge"`` disappears from the reason while
    ``"binds only the operator path"`` appears.
    """
    # ``declared=()`` keeps this a test of the STORY's verb: a live catalog that
    # happens to declare ``occurrence → occurrence`` must not divert the case
    # into the hierarchy branch (the peer implementation's isolation, kept).
    for predicate in ("aboutPoint", "extractedFrom"):
        edges, drops = plan_relation_edges(
            [{"src": "retry storm", "dst": "deadline slip",
              "predicate": predicate}], INDEX, declared=())
        assert edges == [], (predicate, edges)
        assert len(drops) == 1, (predicate, drops)
        reason = drops[0].reason
        assert "create_edge" in reason, (predicate, reason)
        assert "binds only the operator path" not in reason, (predicate, reason)
        # The drop still carries the pair and the stated verb.
        assert (drops[0].src, drops[0].dst) == (
            "retry storm", "deadline slip"), (predicate, drops[0])
        assert drops[0].predicate == predicate, (predicate, drops[0])


def test_one_statement_is_never_reported_written_AND_lost():
    """⛔ ONE statement → at most ONE accounted outcome (#11548 review F3(a)).

    ``plan_hierarchy_edges`` returns one entry per DECLARATION, not one per
    pair, so a pack that declares the same kind pair on BOTH legs planned a
    structural edge AND an operator-path edge. The old loop landed the first
    and appended a drop row for the second, so ONE statement was reported as
    ``written=1, dropped=1`` — a population of 1 accounted as 2, with a drop
    row claiming the relation was not written after its edge landed.

    The operator declaration is a fact about the PACK
    (``pack_hierarchy.hierarchy_diagnostics`` is its named home), not a loss of
    the story's statement, so it is reported only when nothing for that
    statement landed — the second arm below, where it IS the loss.

    Mutation: append an operator-path ``RelationDrop`` per planned entry again
    (the pre-fix shape) and the first arm reads ``written=1, dropped=1``.
    """
    dual = (
        DeclaredHierarchy(
            pack="dev", path="Theme \u2192 Epic", predicate="hasPart",
            from_kind="dev:theme", to_kind="dev:epic",
            write_path="structural"),
        DeclaredHierarchy(
            pack="dev", path="Theme \u2192 Epic", predicate="IMPL",
            from_kind="dev:theme", to_kind="dev:epic",
            write_path="operator"),
    )
    index = {"quarry": [("obj-theme", "dev:theme")],
             "auth table": [("obj-epic", "dev:epic")]}
    relations = [{"src": "quarry", "dst": "auth table",
                  "predicate": "related"}]

    # (a) A structural edge landed: the operator declaration is NOT a loss.
    edges, drops = plan_relation_edges(relations, index, declared=dual)
    assert len(edges) == 1, edges
    assert edges[0].predicate == "hasPart", edges[0]
    assert drops == [], drops
    assert len(edges) + len(drops) == 1, (edges, drops)

    report = apply_relation_edges(_RecordingWriter(), relations, index,
                                 declared=dual)
    assert (report["written"], report["dropped"],
            report["folded"]) == (1, 0, 0), report

    # (b) NOTHING landed — the operator path IS the reason, named once.
    operator_only = (dual[1],)
    edges, drops = plan_relation_edges(relations, index,
                                       declared=operator_only)
    assert edges == [], edges
    assert len(drops) == 1, drops
    assert "OPERATOR" in drops[0].reason, drops[0].reason
    assert "IMPL" in drops[0].reason, drops[0].reason
    assert len(edges) + len(drops) == 1, (edges, drops)


def test_a_repeated_unwritable_statement_counts_once():
    """⛔ The DROP arms dedup a repeat exactly as the LANDING arms do (F3(b)).

    A literal repeat of one statement is one statement — the landing arms have
    always counted it once (one edge, no meaning lost), and
    ``extractor_v2._assemble_relations`` dedups it upstream. The drop arms
    appended per repetition instead, so two identical un-writable statements
    reported ``written=0, dropped=2`` for a population the landing arms call
    ``1`` — the same accounting break in the opposite direction.

    The identity includes the ``semantics`` gloss, because that gloss can decide
    which mechanism BINDS: two statements that read the same but carry different
    glosses are genuinely two statements and must still count twice (the third
    arm below).

    Mutation: append to ``drops`` unconditionally again (the pre-fix shape) and
    the first arm reads ``dropped=2``.
    """
    # (a) A repeat of one un-writable statement: ONE loss.
    for predicate in ("transacts", "wasDerivedFrom", "aboutPoint"):
        edges, drops = plan_relation_edges(
            [{"src": "retry storm", "dst": "deadline slip",
              "predicate": predicate}] * 2, INDEX)
        assert edges == [], (predicate, edges)
        assert len(drops) == 1, (predicate, drops)
        assert len(edges) + len(drops) == 1, (predicate, edges, drops)

    report = apply_relation_edges(
        _RecordingWriter(),
        [{"src": "retry storm", "dst": "deadline slip",
          "predicate": "transacts"}] * 2,
        INDEX, warn=lambda _w: None)
    assert report["written"] == 0, report
    assert report["dropped"] == 1, report
    assert (report["written"] + report["dropped"]
            + report["folded"]) == 1, report

    # (b) A repeat of a statement that LANDS still counts once (the control:
    # this arm already behaved, and the drop dedup must not change it).
    edges, drops = plan_relation_edges(
        [{"src": "retry storm", "dst": "deadline slip",
          "predicate": "causes"}] * 2, INDEX)
    assert len(edges) == 1, edges
    assert edges[0].folded == (), edges[0]
    assert drops == [], drops

    # (c) Two DIFFERENT glosses are two statements, not a repeat — the key is
    # the statement's own identity, so the dedup is not over-broad.
    edges, drops = plan_relation_edges([
        {"src": "ghost entity", "dst": "deadline slip",
         "predicate": "related", "semantics": "causes"},
        {"src": "ghost entity", "dst": "deadline slip",
         "predicate": "related", "semantics": "supports"},
    ], INDEX)
    assert edges == [], edges
    assert len(drops) == 2, drops


def test_a_writer_that_returns_False_is_a_REFUSAL_not_a_write():
    """⛔ Reading only the exception made a structural refusal count as a WRITE.

    The leg has two refusal channels and used to read only the raising one, so a
    refused edge was appended to ``written`` with no ``RelationDrop`` and no
    warning — the statement counted as a SUCCESS against a graph that holds no
    edge. That is the `fail-silent` class `link_entities`'s own dispatch says a
    merge must never add, and `written` is the number the capture receipt carries
    and the PR body quotes (#11548 review, ISSUE 1).

    This case pins the BARE-BOOL channel (a writer answering `False`); the
    production SHAPE is the next test, which is the one that was missing:
    an ``is False`` test on it is DEAD because the door wraps the bool.

    Mutation: drop the ``result is False`` arm and `written` reads 1 with
    `dropped == 0`.
    """
    report = apply_relation_edges(
        _FalseWriter(),
        [{"src": "retry storm", "dst": "deadline slip",
          "predicate": "related"}],
        INDEX)
    assert report["written"] == 0, report
    assert report["dropped"] == 1, report
    drop = report["drops"][0]
    assert (drop["src"], drop["dst"]) == ("retry storm", "deadline slip"), drop
    assert drop["predicate"] == "related", drop
    assert "created NOTHING" in drop["reason"], drop

    # …and a refused edge that had ABSORBED a meaning loses that meaning too,
    # exactly as the raising channel does — one drop per lost meaning, so the
    # accounting reconciles on BOTH refusal channels.
    folded = apply_relation_edges(
        _FalseWriter(),
        [{"src": "retry storm", "dst": "deadline slip", "predicate": "causes"},
         {"src": "retry storm", "dst": "deadline slip", "predicate": "supports"}],
        INDEX)
    assert folded["written"] == 0, folded
    assert folded["dropped"] == 2, folded
    assert folded["folded"] == 0, folded
    assert sorted(row["predicate"] for row in folded["drops"]) == \
        ["causes", "supports"], folded


def test_the_structural_doors_refusal_envelope_is_NOT_a_write():
    """⛔ #11548 review P1: the refusal channel on the arm that HAS the bug.

    ``TortoiseSDK.create_edge`` does not return the projection's bool — it wraps
    it (``sdk.py:49406-49412``), so the real refusal arrives as a TRUTHY dict
    with ``created: False``. ``if result is False`` never fired on it, the row
    was appended to ``written``, and ``written + dropped + folded`` still
    reconciled: a receipt claiming an edge the graph does not hold, on exactly
    the arm whose docstring claims to have closed that class.

    Non-vacuity: with the bare-bool-only arm restored, this case reports
    ``written == 1, dropped == 0`` (executed). It is a SEPARATE case from the
    bare-bool one on purpose — the two channels are different shapes, and one
    fabricated shape certified coverage the other does not provide.
    """
    report = apply_relation_edges(
        _StructuralRefusalWriter(),
        [{"src": "retry storm", "dst": "deadline slip",
          "predicate": "related"}],
        INDEX)
    assert report["written"] == 0, report
    assert report["dropped"] == 1, report
    assert report["drops"][0]["predicate"] == "related", report


def test_a_direct_dedup_hit_is_a_WRITE_not_a_refusal():
    """The control the structural fix must not break.

    ``create_direct_edge`` answers ``{"created": created, "deduped": not
    created}``, and a MERGE dedup hit is ``created: False`` on an edge the graph
    DOES hold. Both writers are MERGE-based — idempotence is the module's stated
    property — so reading ``created is False`` on this arm would report every
    re-capture as a refusal and count a held edge as uncommitted. The test must
    therefore be arm-scoped, and this is the arm it must not apply to.
    """
    report = apply_relation_edges(
        _DirectDedupWriter(),
        [{"src": "retry storm", "dst": "deadline slip",
          "predicate": "causes"}],
        INDEX)
    assert report["written"] == 1, report
    assert report["dropped"] == 0, report


class _DeclaringRegistry:
    """A catalog that declares a MEANING for verbs no shipped pack does.

    ``plan_relation_edges(registry=…)`` is the documented seam for "a test
    passes its own catalog". Using it here keeps the case hermetic: a pack
    rename cannot turn a write-path guard into a pack test, and the verbs below
    are deliberately NOT ONTOLOGY §8 meanings — the pack row is the ONLY thing
    that declares what they mean, which is exactly the population round 8
    measured as wrong.
    """

    def __init__(self, rows):
        self._rows = [dict(row) for row in rows]

    def list_relations(self):
        return [dict(row) for row in self._rows]


DIRECTION_ROWS = (
    # `splitsInto` is not a meaning: its ROW declares `hasPart`, ruled `<->`.
    {"predicate": "splitsInto", "mechanism": "IMPL", "semantics": "hasPart",
     "pack": "testpack"},
    # `settlesInto` → `related`, ruled `-` (the connection exists, NO confidence
    # transfers). Stored one-way it would propagate belief the ruling denies.
    {"predicate": "settlesInto", "mechanism": "IMPL", "semantics": "related",
     "pack": "testpack"},
    # A RETIRED spelling as the declared meaning: `funds` collapses onto
    # `transacts` (SEMANTICS_TRANSITION) — the canonicalisation must happen on
    # the row's word too, or the retired spelling silently rules nothing.
    {"predicate": "tradesFor", "mechanism": "IMPL", "semantics": "funds",
     "pack": "testpack"},
)

DIRECTION_INDEX = {
    "retry storm": [("obj-1", "occurrence")],
    "deadline slip": [("obj-2", "occurrence")],
    "retry budget": [("obj-3", "occurrence")],
    "quarry": [("obj-4", "occurrence")],
    "auth table": [("obj-5", "occurrence")],
    "lumen auth": [("obj-6", "occurrence")],
}

DIRECTION_RELATIONS = [
    {"src": "retry storm", "dst": "deadline slip", "predicate": "splitsInto"},
    {"src": "retry budget", "dst": "quarry", "predicate": "settlesInto"},
    {"src": "auth table", "dst": "lumen auth", "predicate": "tradesFor"},
]


def test_a_pack_declared_meaning_fixes_the_direction_the_leg_writes():
    """⛔ The row that LICENSED the write also rules its DIRECTION (#11548).

    ``_direction_for`` chose from ``(label, predicate, semantics)`` — the STORY's
    verb and the record's optional gloss — and the pack relation row's own
    ``semantics:`` was never a candidate. That is the declaration
    ``_mechanism_for`` reads and ``relation_write_paths`` vetoes on, i.e. the row
    that made the write licensed in the first place, and its MEANING was dropped
    before the direction was chosen. Measured on the shipped catalog before the
    fix: ``partOf`` (``semantics: hasPart``, ruled ``<->``) and ``competesWith``
    (``opposes`` → ``mutuallyExclusive``, ``<->``) wrote ``->``; ``protects`` /
    ``marks`` (``related``) and ``funds`` (``transacts``) wrote ``->`` where the
    ruling is ``-``. ``live.direction_flows`` reads those tokens back unchanged,
    so EP propagated belief along edges the declaration says carry none.

    It was also NON-DETERMINISTIC by gloss: ``marks`` wrote ``-`` when the model
    happened to restate ``semantics: related`` and ``->`` when it did not — one
    pack declaration, two graphs. The declaration is the authority on what its
    verb means (the rule ``_mechanism_for`` already follows for the mechanism
    half of the same triple, #7852), so it is consulted FIRST and the gloss no
    longer decides what a declared verb means.

    Mutation: drop the declared candidate from ``_direction_for`` (the pre-fix
    shape) and all three verbs read the one-way fallback.
    """
    registry = _DeclaringRegistry(DIRECTION_ROWS)
    plan, drops = plan_relation_edges(DIRECTION_RELATIONS, DIRECTION_INDEX,
                                      registry, declared=())
    assert not drops, drops
    by_stated = {edge.stated_predicate: edge for edge in plan}
    assert by_stated["splitsInto"].direction == "<->", by_stated["splitsInto"]
    assert by_stated["settlesInto"].direction == "-", by_stated["settlesInto"]
    assert by_stated["tradesFor"].direction == "-", by_stated["tradesFor"]

    # …and the token is what the EP reader makes of it: `-` is NO belief
    # transfer in either direction, which is the property the one-way fallback
    # destroyed for every `related`/`transacts` verb the catalog declares.
    from tortoise.live import direction_flows

    assert direction_flows(by_stated["splitsInto"].direction) == (True, True)
    assert direction_flows(by_stated["settlesInto"].direction) == (False, False)
    assert direction_flows(by_stated["tradesFor"].direction) == (False, False)

    # The gloss is NOT the authority for a declared verb: restating `supports`
    # on a verb its row declares `hasPart` must not change the stored direction
    # (the non-determinism the pre-fix lookup produced).
    for gloss in ("supports", "refutes", None):
        rel = {"src": "retry storm", "dst": "deadline slip",
               "predicate": "splitsInto"}
        if gloss:
            rel["semantics"] = gloss
        glossed, glossed_drops = plan_relation_edges(
            [rel], DIRECTION_INDEX, registry, declared=())
        assert not glossed_drops, glossed_drops
        assert glossed[0].direction == "<->", (gloss, glossed[0])

    # …and the ruled token is what reaches the WRITER, not just the plan.
    writer = _RecordingWriter()
    apply_relation_edges(writer, DIRECTION_RELATIONS, DIRECTION_INDEX,
                         registry, declared=())
    assert [kw["direction"] for _p, _s, _d, kw in writer.calls] == [
        "<->", "-", "-"], writer.calls


def test_declared_meanings_reads_the_row_that_licenses_the_write():
    """⛔ The Layer-2 HALF of the declaration, from the registry (never here).

    ``declared_mechanisms`` answers "which mechanism does this verb declare";
    nothing answered "which MEANING", so the direction lookup had no way to
    reach the row that licensed the write and fell back to the story's verb —
    the defect the case above pins on the write path. This is the accessor's own
    contract: the row's ``semantics`` CANONICALIZED through the ONE transition
    table, filtered to the closed vocabulary, never a word invented here.

    Mutation: return the row's ``semantics`` verbatim and the retired-spelling
    arm reads ``funds`` instead of ``transacts``; drop the ``SEMANTICS_VALUES``
    filter and an out-of-vocabulary word is reported as a meaning.
    """
    from tortoise.pack_registry import declared_meanings

    registry = _DeclaringRegistry(DIRECTION_ROWS)
    assert declared_meanings(label="splitsInto", registry=registry) == [
        ("hasPart", "the pack relation 'splitsInto' (testpack)")], \
        declared_meanings(label="splitsInto", registry=registry)
    # A RETIRED spelling is canonicalized on BOTH halves of the declaration.
    assert declared_meanings(label="tradesFor", registry=registry) == [
        ("transacts", "the pack relation 'tradesFor' (testpack)")], \
        declared_meanings(label="tradesFor", registry=registry)
    # A verb no row declares declares NO meaning — never "unknown".
    assert declared_meanings(label="nuances", registry=registry) == [], \
        declared_meanings(label="nuances", registry=registry)
    # A word outside the closed vocabulary is not a meaning this reader can rule.
    assert declared_meanings(
        label="splitsInto",
        registry=_DeclaringRegistry([{"predicate": "splitsInto",
                                      "semantics": "sideways"}])) == [], \
        "an out-of-vocabulary meaning must not be reported as declared"
    # The three-state sentinel, spelled the same way as `declared_mechanisms`:
    # `None` is "known absent", NOT "load the default".
    assert declared_meanings(label="splitsInto", registry=None) == []

    # NEVER raises, and a row it cannot read does not truncate the ones after it.
    assert declared_meanings(label="splitsInto", registry=object()) == []

    class _BadRow(dict):
        def get(self, key, default=None):
            if key == "predicate":
                raise RuntimeError("row will not read")
            return super().get(key, default)

    mixed = _DeclaringRegistry([_BadRow({"predicate": "x"}),
                               dict(DIRECTION_ROWS[0])])
    assert declared_meanings(label="splitsInto", registry=mixed) == [
        ("hasPart", "the pack relation 'splitsInto' (testpack)")], \
        declared_meanings(label="splitsInto", registry=mixed)
