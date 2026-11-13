"""#11928 — the explain path reaches the four epistemic labels.

THE DEFECT THIS PINS. ``tortoise/why.py`` answered "why does this claim have this
confidence" on the ``:Point`` lane only: every read was ``MATCH (n:Point)``, so
an operator endpoint written as an ``:Object`` (a goal) or an ``:Event`` (a
reading) got **no block at all** — ``assemble_why_blocks`` returned ``{}`` for it.
Measured before the fix, on the fixture in this file: the goal Object's id was
absent from the returned mapping while the judgement Point's id was present.

That is precisely the shape the reasoning loop produces (#11822: a ``derived``
judgement wired IMPL into a goal Object; a reading is an ``:Event`` wired
``aboutObject``), so the loop's verdicts were uninspectable — the same trust
failure as a silent propagation cap (#11823).

WHAT MUST NOW BE TRUE, and each is a test below:

1. a goal ``:Object`` gets a block, and it NAMES the reading, the target and the
   comparison that produced the verdict (the ``assessment`` dimension);
2. a reading ``:Event`` gets a block, and its snippet is its ``name`` — not the
   empty string a bare ``content`` read produces for that label (#8169's
   "inert on the message path");
3. an operator endpoint written as an Object/Event is traversed (both directions
   of the support pair, not just the anchor);
4. the ``:Point`` lane is UNCHANGED: same block dimensions, same pinned support
   entry key set, and no ``assessment`` key (nothing judged this claim);
5. the batch read count stays FIXED — the sweep is over PRESENT LABELS, never
   over result rows.

⚠️ RECORDED RESIDUAL, asserted as absent rather than implied: a non-Point block
carries no ``supersession`` key. That read is still ``:Point``-only because the
entity-supersession read gate is #11824's open work — absent-means-not-computed
(§3.1.3) is honest there, a fabricated ``live`` would not be.
"""
from __future__ import annotations

import contextlib

import pytest

from tortoise.goal_judgement import Target, judge_and_record
from tortoise.why import (
    W4_MAX_ASSESSMENTS,
    assemble_why_blocks,
    enrich_items,
    item_to_why_entry,
)

# The exact key set of a support entry (pinned by
# test_w4_why_enrichment::test_flag_on_emits_contract_on_search_surface). The
# #11928 widening must not have added anything here: the comparison belongs to
# the VERDICT, not to the supporter.
SUPPORT_ENTRY_KEYS = {
    "point_id", "content_snippet", "edge", "weight",
    "operator_id", "operator_type", "operator_label", "mitigation",
}


@pytest.fixture
def sdk(sdk_factory):
    s = sdk_factory()
    # A fresh graph has nothing to clear; a namespace-reused one might.
    with contextlib.suppress(Exception):
        s._get_proj().g.query("MATCH (n) DETACH DELETE n")
    yield s
    s.close()


def _world(sdk, *, comparator="<=", threshold=10.0, tag=None):
    """A measured thing, a target and a goal — all core kinds that exist today.

    Same shape as ``test_11822_goal_judgement``'s fixture: ``indicator`` is a
    ``workflow`` Object (#7860's core ``indicator`` kind is not promoted yet),
    the target is a ``target`` Object, the goal is a ``goal`` Object.
    """
    suffix = f" [{tag}]" if tag else ""
    indicator = sdk.create_object(f"CI pipeline duration{suffix}",
                                  objectKind="workflow")
    target_node = sdk.create_object(
        f"CI <= 10m{suffix}", objectKind="target",
        comparator=comparator, threshold=threshold, dwell=1,
    )
    goal = sdk.create_object(f"CI is fast{suffix}", objectKind="goal",
                             goalState="active")
    return indicator, target_node, goal


def _reading(sdk, indicator, value, *, at, unit="minutes"):
    return sdk.create_event(
        f"CI run at {at}", "measurement", value=value, unit=unit,
        startedAt=at, aboutObject=indicator["id"],
    )


def _judge(sdk, indicator, target_node, goal, value, *, at):
    _reading(sdk, indicator, value, at=at)
    return judge_and_record(
        sdk, goal_id=goal["id"], indicator_id=indicator["id"],
        target=Target.from_node(target_node),
    )


def _labels(sdk, node_id):
    return sdk._get_proj().g.query(
        "MATCH (n) WHERE n.id = $i RETURN labels(n)",
        params={"i": node_id}).result_set[0][0]


# ── 1. the acceptance case: a GOAL (an :Object) is explainable ────────────

def test_a_goal_object_is_explainable_and_names_reading_target_comparison(sdk):
    """THE ISSUE. Before #11928 this returned ``{}`` for the goal's id.

    The answer must name the READING (id, text, value, unit, as-of), the TARGET
    (id, text) and the COMPARISON (state, comparator, threshold) — not merely
    surface "some claim moved it", which is what a support chain alone says.
    """
    indicator, target_node, goal = _world(sdk)
    recorded = _judge(sdk, indicator, target_node, goal, 12.0,
                      at="2026-11-11T10:00:00Z")

    assert _labels(sdk, goal["id"]) == ["Object"], (
        "fixture drift: the goal must be an :Object for this pin to mean anything")

    blocks = assemble_why_blocks(sdk._get_proj(), [goal["id"], recorded["point_id"]])
    assert goal["id"] in blocks, (
        "the goal Object got no why-block — the anchor is still :Point-only")
    block = blocks[goal["id"]]
    assert block["point_id"] == goal["id"]
    # The dimension that makes the verdict inspectable.
    assessments = block.get("assessment") or []
    assert len(assessments) == 1, f"expected one live judgement, got {assessments}"
    assessment = assessments[0]
    assert set(assessment) == {
        "judgement_id", "state", "reason", "comparator", "threshold", "dwell",
        "indicator_id", "reading", "target", "evaluated_at",
    }
    assert assessment["judgement_id"] == recorded["point_id"]
    assert assessment["state"] == "unmet"
    assert assessment["comparator"] == "<="
    assert assessment["threshold"] == 10.0
    # THE COMPARISON'S OPERANDS, named:
    reading = assessment["reading"]
    assert reading["point_id"], "the reading must be NAMED, not just counted"
    assert reading["content_snippet"], (
        "an :Event's text lives on `name` — a bare `content` read emits '' "
        "here, which is the #8169 inert-surface defect")
    assert reading["value"] == 12.0
    assert reading["unit"] == "minutes"
    assert reading["as_of"] == "2026-11-11T10:00:00Z"
    target = assessment["target"]
    assert target["point_id"] == target_node["id"]
    assert target["content_snippet"] == "CI <= 10m"

    # And the support chain agrees with it, naming the judgement claim.
    chain = block["support_chain"]
    assert chain and chain[0]["point_id"] == recorded["point_id"]
    for entry in chain:
        assert set(entry) == SUPPORT_ENTRY_KEYS, (
            "the comparison must NOT ride the support entry: its key set is a "
            "pinned contract and the verdict is its own dimension")


def test_a_goal_belief_really_moved_so_the_explanation_is_about_a_move(sdk):
    """The issue's premise, measured: the goal's confidence follows the reading.

    Without this, "explains the goal's confidence" could be satisfied by a block
    over an inert node. The goal is an Object with NO stored prior of its own —
    the number it reports is EP's, propagated from the judgement claim.
    """
    indicator, target_node, goal = _world(sdk)
    met = _judge(sdk, indicator, target_node, goal, 9.0, at="2026-11-11T10:00:00Z")
    after_met = sdk.compute_confidence()
    g_met = after_met["confidences"][goal["id"]]["mean"]

    unmet = _judge(sdk, indicator, target_node, goal, 12.0, at="2026-11-11T11:00:00Z")
    after_unmet = sdk.compute_confidence()
    g_unmet = after_unmet["confidences"][goal["id"]]["mean"]
    assert g_unmet < g_met, f"the goal did not follow the reading: {g_met} -> {g_unmet}"

    # The live judgement is the one the block reports — the superseded one is
    # neither in the chain nor in the assessment (the assessment query filters
    # on liveness, not on "the newest row").
    blocks = assemble_why_blocks(sdk._get_proj(), [goal["id"]])
    assessments = blocks[goal["id"]]["assessment"]
    # The COUNT is the invariant the identity assertions cannot express: one
    # goal, one indicator, one target => exactly one live verdict. NOTE it is not
    # a liveness pin, and this fixture cannot be one: `supersede_point` transfers
    # the predecessor's operator edge (#6180), so the superseded judgement stops
    # matching the wiring read altogether and the liveness predicate is never
    # exercised here — a mutant that drops it leaves this row green (measured).
    # Liveness is pinned by
    # `test_a_retired_judgement_is_not_reported_as_a_live_verdict` below, which
    # retires a judgement WITHOUT a transfer.
    assert len(assessments) == 1, (
        "a SUPERSEDED judgement is being reported as a live verdict: "
        + repr([a["judgement_id"] for a in assessments]))
    assessment = assessments[0]
    assert assessment["judgement_id"] == unmet["point_id"]
    assert assessment["state"] == "unmet"
    assert assessment["judgement_id"] != met["point_id"]

    # The goal's OWN ep block is the propagated number, not an invented prior.
    ep = blocks[goal["id"]]["ep"]
    assert ep["has_ep"] is True, (
        "the goal's belief is EP's, but the ep read reported it unmeasured: "
        + repr(ep))


def test_a_retired_judgement_is_not_reported_as_a_live_verdict(sdk):
    """The assessment read's LIVENESS filter, pinged where it is load-bearing.

    The flip test above cannot reach it: `supersede_point` TRANSFERS the
    predecessor's operator edge (#6180), so a superseded judgement stops matching
    the wiring read at all and the filter is never exercised. This fixture
    retires the judgement WITHOUT a transfer (a hand `update_entity(status=…)`,
    and the shape an Object supersession leaves before #11826) — the arrow still
    points at the goal, so only `_live_only('j.status')` can drop it.
    """
    _indicator, _target, goal = _world(sdk, tag="retired")
    recorded = _judge(sdk, _indicator, _target, goal, 12.0,
                      at="2026-11-11T10:00:00Z")
    sdk.update_entity(recorded["point_id"], status="superseded")

    block = assemble_why_blocks(sdk._get_proj(), [goal["id"]])[goal["id"]]
    assert not block.get("assessment"), (
        "a RETIRED judgement is being reported as the goal's live verdict: "
        + repr(block.get("assessment")))


# ── 2. a READING (an :Event) is explainable ───────────────────────────────

def test_a_reading_event_is_explainable(sdk):
    """An ``:Event`` gets a block of its own.

    NOTE the reading's TEXT is not asserted here — a block carries no self-entry,
    so this row would pass with ``_text_expr`` reverted to a bare ``content``.
    The text is pinned where a reading is actually NAMED as an endpoint:
    ``test_an_operator_endpoint_written_as_an_event_is_traversed`` — which reds
    on that mutant. (The conflicts leg names it too, but through its own inline
    coalesce rather than ``_text_expr``, so it has its own kill:
    ``test_a_nand_counter_claim_written_as_an_event_is_surfaced`` reds when that
    chain is reverted, not when ``_text_expr`` is.)
    """
    indicator, _target, _goal = _world(sdk, tag="ev")
    reading = _reading(sdk, indicator, 12.0, at="2026-11-11T10:00:00Z")
    assert _labels(sdk, reading["id"]) == ["Event"]

    blocks = assemble_why_blocks(sdk._get_proj(), [reading["id"]])
    assert reading["id"] in blocks, "the reading Event got no why-block"
    block = blocks[reading["id"]]
    assert block["point_id"] == reading["id"]
    assert block["ep"]["confidence_mean"] is None and block["ep"]["has_ep"] is False, (
        "nothing measured this Event's belief; the block must say absent, not 0.5")
    # The RESIDUAL, asserted: the supersession read is still :Point-only.
    assert "supersession" not in block, (
        "a non-Point block now carries a supersession view — if #11824's read "
        "gate landed, widen this pin and the why.py note together")


# ── 3. an OPERATOR ENDPOINT written as an Object/Event ────────────────────

def test_an_operator_endpoint_written_as_an_event_is_traversed(sdk):
    """``#7813`` admits all four labels as operator endpoints — the SUPPORTER
    side must be traversed too, not just the anchor.

    The fixture is an operator whose SOURCE is an ``:Event`` (a reading lends its
    confidence to a claim) and whose TARGET is a ``:Point``. Pre-#11928 the
    ``(sup:Point)`` in the pattern excluded the reading, so the claim's block had
    an empty support chain — the block existed and answered nothing.
    """
    indicator, _target, _goal = _world(sdk, tag="op")
    reading = _reading(sdk, indicator, 12.0, at="2026-11-11T10:00:00Z")
    claim = sdk.create_point("statement", "the CI pipeline is slow")
    sdk.create_operator("IMPL", reading["id"], [claim["id"]],
                        direction="->", label="supports")

    blocks = assemble_why_blocks(sdk._get_proj(), [claim["id"]])
    chain = blocks[claim["id"]]["support_chain"]
    assert chain, "the Event-supporter was not traversed — `sup` is still :Point-only"
    assert chain[0]["point_id"] == reading["id"]
    assert chain[0]["content_snippet"], (
        "the supporter is an :Event — its text is `name`, and an empty snippet "
        "makes the support chain inert")


def test_an_operator_endpoint_written_as_an_object_is_traversed(sdk):
    """The mirror case: an ``:Object`` supporter (a goal lending support to a
    claim — the supra-goal shape the reasoning loop produces)."""
    _indicator, _target, goal = _world(sdk, tag="obj")
    claim = sdk.create_point("statement", "the team is shipping fast")
    sdk.create_operator("IMPL", goal["id"], [claim["id"]],
                        direction="->", label="supports")

    blocks = assemble_why_blocks(sdk._get_proj(), [claim["id"]])
    chain = blocks[claim["id"]]["support_chain"]
    assert chain and chain[0]["point_id"] == goal["id"]
    assert chain[0]["content_snippet"] == goal["name"]


def test_a_nand_counter_claim_written_as_an_event_is_surfaced(sdk):
    """The conflict leg's NANDer is widened too, and the TEXT falls through from
    the (absent) operator source to the NANDer itself.

    A direct operator-less NAND whose NANDer is an ``:Event`` is the case where
    an over-eager widening breaks the OTHER way: an unconditional
    ``epistemic_disjunction('src')`` on the OPTIONAL match would filter the
    direct arm out entirely, so the conflict would vanish rather than widen.
    """
    _indicator, _target, _goal = _world(sdk, tag="nand")
    claim = sdk.create_point("statement", "the weekly report is accurate")
    counter = sdk.create_event("report was disputed", "note",
                               startedAt="2026-11-11T09:00:00Z")
    sdk._get_proj().g.query(
        "MATCH (c), (t) WHERE c.id = $c AND t.id = $t CREATE (c)-[:NAND]->(t)",
        params={"c": counter["id"], "t": claim["id"]})

    blocks = assemble_why_blocks(sdk._get_proj(), [claim["id"]])
    conflicts = blocks[claim["id"]].get("conflicts") or {}
    nands = conflicts.get("nands") or []
    assert nands, f"the Event counter-claim was dropped: {blocks[claim['id']]}"
    assert nands[0]["point_id"] == counter["id"]
    assert nands[0]["content_snippet"] == "report was disputed"


def test_a_nand_operators_non_point_source_is_surfaced(sdk):
    """The CONFLICTS leg's ``src`` widening (review, mutation F3).

    The counter-claim of an operator-mediated NAND is its INPUT source at idx 0,
    and #7813 admits all four labels there. No fixture reached that arm: with
    ``epistemic_disjunction('src')`` narrowed to ``src:Point`` every test stayed
    green, while the real effect is an operator-mediated counterargument written
    as an ``:Event`` silently vanishing from ``conflicts``.
    """
    claim = sdk.create_point("statement", "the migration is safe to run")
    evidence = sdk.create_event("migration broke staging", "incident",
                                startedAt="2026-11-11T09:00:00Z")
    sdk.create_operator("NAND", evidence["id"], [claim["id"]], direction="-")
    assert _labels(sdk, evidence["id"]) == ["Event"]

    block = assemble_why_blocks(sdk._get_proj(), [claim["id"]])[claim["id"]]
    nands = (block.get("conflicts") or {}).get("nands") or []
    assert [n["content_snippet"] for n in nands] == ["migration broke staging"], (
        "an operator-mediated counterargument written as an :Event was dropped: "
        + repr(block.get("conflicts")))


def test_a_tradeoff_alternative_written_as_an_object_is_surfaced(sdk):
    """The TRADEOFFS leg's ``alt`` widening, same class (mutation F3).

    Every tradeoff fixture in the suite builds ``option`` POINTS, so narrowing
    ``epistemic_disjunction('alt')`` to ``alt:Point`` left every test green while
    an alternative written as an ``:Object`` would silently empty the dimension.
    """
    decision = sdk.create_point("decision", "how to land the rollout")
    alt_a = sdk.create_object("land it behind a flag", objectKind="option")
    alt_b = sdk.create_object("land it to everyone", objectKind="option")
    sdk.create_operator("IMPL", decision["id"], [alt_a["id"]])
    sdk.create_operator("IMPL", decision["id"], [alt_b["id"]])

    block = assemble_why_blocks(sdk._get_proj(), [decision["id"]])[decision["id"]]
    labels = sorted(t["label"] for t in block.get("tradeoffs") or [])
    assert labels == ["land it behind a flag", "land it to everyone"], (
        "Object alternatives were dropped from tradeoffs: "
        + repr(block.get("tradeoffs")))


def test_option_kind_alternatives_are_surfaced_under_a_NON_decision_anchor(sdk):
    """The gate's SECOND arm — and the pin the ``alt`` kind cell did not have
    (review round 2).

    ``_assemble_tradeoffs``'s docstring names two ways to be a decision point:
    the anchor's own kind is ``decision``, OR the anchor carries ≥ 2 alternatives
    whose kind is ``option``. Every other tradeoff case in this file satisfies the
    FIRST arm, so with only those, reverting the ALTERNATIVE's kind cell to
    ``alt.pointKind`` left the whole file green (measured: 20 passed) while the
    second arm was dead for ``:Object`` options — the same silent-empty shape one
    cell over from round 1's finding.

    The anchor here is deliberately a non-decision Object (``objectKind="plan"``),
    so only ``any(a["kind"] == "option")`` can admit it. Measured as a mutant:
    revert that one cell and this pin reports ``None``.
    """
    anchor = sdk.create_object("how to land the rollout", objectKind="plan")
    alt_a = sdk.create_object("land it behind a flag", objectKind="option")
    alt_b = sdk.create_object("land it to everyone", objectKind="option")
    sdk.create_operator("IMPL", anchor["id"], [alt_a["id"]])
    sdk.create_operator("IMPL", anchor["id"], [alt_b["id"]])

    block = assemble_why_blocks(sdk._get_proj(), [anchor["id"]])[anchor["id"]]
    labels = sorted(t["label"] for t in block.get("tradeoffs") or [])
    assert labels == ["land it behind a flag", "land it to everyone"], (
        "the option-kind arm of the decision gate is dead for Object options: "
        + repr(block.get("tradeoffs")))


# ── 3d. the decision gate, on BOTH of its arms ────────────────────────────
# (round 1's finding was the ANCHOR's kind cell; round 2's was the ALTERNATIVE's.
#  Both pins above sit here, under a header that describes them, instead of below
#  under ":Point lane" — a case filed under the wrong header is how a coverage
#  claim goes unread.)

def test_a_decision_written_as_an_object_emits_its_tradeoffs(sdk):
    """The gate's FIRST arm (the ANCHOR's kind) on a widened anchor.

    ``test_a_tradeoff_alternative_written_as_an_object_is_surfaced`` above passes
    with a POINT anchor, so it exercises the widening and never the gate: the
    gate reads the anchor's kind from ``n.pointKind`` while an ``:Object`` stores
    its kind in ``objectKind`` (``create_object`` writes that property and leaves
    ``pointKind`` NULL).

    ⛔ The alternatives are deliberately NOT ``option``-kind. ``_assemble_tradeoffs``
    admits a block by EITHER arm, so an ``option``-kind alternative lets the SECOND
    arm carry the block and the anchor cell goes untested — measured: with the
    anchor cell alone reverted, an option-kind pair still emitted ``tradeoffs``
    (21 passed), which is exactly the coverage hole review round 2 found one cell
    over. These two are ``plan``-kind, so only the anchor's own kind can admit the
    block. Measured as a mutant: revert the anchor cell alone and this pin reports
    ``None``, while the option-kind pin below stays green.
    """
    decision = sdk.create_object("how to land the rollout", objectKind="decision")
    alt_a = sdk.create_object("land it behind a flag", objectKind="plan")
    alt_b = sdk.create_object("land it to everyone", objectKind="plan")
    sdk.create_operator("IMPL", decision["id"], [alt_a["id"]])
    sdk.create_operator("IMPL", decision["id"], [alt_b["id"]])

    block = assemble_why_blocks(sdk._get_proj(), [decision["id"]])[decision["id"]]
    labels = sorted(t["label"] for t in block.get("tradeoffs") or [])
    assert labels == ["land it behind a flag", "land it to everyone"], (
        "an Object decision point emitted no tradeoffs: "
        + repr(block.get("tradeoffs")))


def test_two_distinct_event_counter_claims_are_both_surfaced(sdk):
    """The fusion must dedupe on the INTERNAL node id, not on the emitted
    ``point_id`` (review F1, measured).

    ``epistemic_identity`` coalesces ``id`` and ``eventId``, and
    ``_event_guarded_merge``'s suffix arm leaves two DISTINCT Events coalescing
    to one value. Keying the conflict fusion on that value dropped a genuine
    counter-claim, and which one survived was storage row order. Here the two
    NANDers carry colliding DISPLAY identities (``{id:'C1'}`` and
    ``{eventId:'C1'}``) and are distinct nodes.
    """
    claim = sdk.create_point("statement", "the weekly report is accurate")
    g = sdk._get_proj().g
    g.query("CREATE (e:Event {id:'C1', name:'first counter-claim', "
            "eventKind:'note', status:'live'})")
    g.query("CREATE (e:Event {eventId:'C1', name:'second counter-claim', "
            "eventKind:'note'})")
    g.query(
        "MATCH (a:Event {id:'C1'}), (t) WHERE t.id = $t "
        "CREATE (a)-[:NAND]->(t)", params={"t": claim["id"]})
    g.query(
        "MATCH (a:Event {eventId:'C1'}), (t) WHERE t.id = $t "
        "CREATE (a)-[:NAND]->(t)", params={"t": claim["id"]})

    block = assemble_why_blocks(sdk._get_proj(), [claim["id"]])[claim["id"]]
    snippets = [n["content_snippet"] for n in
                (block.get("conflicts") or {}).get("nands") or []]
    assert sorted(snippets) == ["first counter-claim", "second counter-claim"], (
        "two distinct counter-claims collapsed into one fusion entry: " + repr(snippets))


def test_two_distinct_event_supporters_are_both_surfaced(sdk):
    """The SUPPORT leg's fusion key, pinned — review round 4 (#12268).

    The counter-claim pin above covers `_assemble_conflicts`'s key. The support
    fold has its own (`_assemble_supports`'s ``pending[pid][sup_key]``, keyed on
    `id(sup)` from the last RETURN column), and it is the one review round 1
    landed FOR a measured defect — so leaving it unpinned would let a reversion
    to the display id keep every other case here green while a real supporter
    silently vanished from the chain again. The manifest `durations` row for this
    file names both collision pins, so the row must not describe coverage the
    suite does not have.

    Measured: passes at this head; a reverted ``sup_key`` yields ONE chain entry.
    """
    claim = sdk.create_point("statement", "the build is green")
    g = sdk._get_proj().g
    g.query("CREATE (e:Event {id:'SX1', name:'first supporter', "
            "eventKind:'note'})")
    g.query("CREATE (e:Event {eventId:'SX1', name:'second supporter', "
            "eventKind:'note'})")
    g.query(
        "MATCH (a:Event {id:'SX1'}), (t) WHERE t.id = $t "
        "CREATE (a)-[:IMPL]->(t)", params={"t": claim["id"]})
    g.query(
        "MATCH (a:Event {eventId:'SX1'}), (t) WHERE t.id = $t "
        "CREATE (a)-[:IMPL]->(t)", params={"t": claim["id"]})

    chain = assemble_why_blocks(sdk._get_proj(), [claim["id"]])[claim["id"]][
        "support_chain"]
    assert sorted(e["content_snippet"] for e in chain) == [
        "first supporter", "second supporter"], (
        "two distinct supporters collapsed into one fusion entry: " + repr(chain))


# ── 3c. the widened positions are still RESTRICTED to the four labels ─────

def test_a_non_epistemic_endpoint_is_not_traversed(sdk):
    """The disjunction on every traversal position is a FILTER, and a filter
    nobody asserts is a filter nobody will keep — found by the mutation pass:
    deleting `epistemic_disjunction('sup')` from the direct support read (and
    separately `epistemic_disjunction('c')` from the conflicts read) left every
    other case in this file GREEN.

    The widening's own cases pass either way, because the disjunction RESTRICTS:
    `MATCH (sup)-[r:IMPL]->(n)` is label-agnostic, so without it a `:Document`
    wired as an IMPL source is presented as an epistemic SUPPORTER, and a
    `:Source` NANDer as a counter-claim. Pre-#11928 those nodes were excluded by
    the label on `sup`/`c`; this pins the replacement.
    """
    claim = sdk.create_point("statement", "the build is green [non-epistemic]")
    g = sdk._get_proj().g
    g.query("CREATE (d:Document {id:'DOC-1', name:'a document', title:'Doc'})")
    g.query("CREATE (s:Source {id:'SRC-1', name:'a source'})")
    g.query("MATCH (d:Document {id:'DOC-1'}), (t) WHERE t.id = $t "
            "CREATE (d)-[:IMPL]->(t)", params={"t": claim["id"]})
    g.query("MATCH (s:Source {id:'SRC-1'}), (t) WHERE t.id = $t "
            "CREATE (s)-[:NAND]->(t)", params={"t": claim["id"]})
    # AND THE OPERATOR LEG, which is a different read with its own disjunction:
    # a `:Document` wired as an operator's INPUT source at idx 0. Deleting
    # `epistemic_disjunction('sup')` from `_SUPPORT_OP_CYPHER` left this file
    # fully green (measured), because every operator fixture used a Point source.
    g.query("CREATE (d2:Document {id:'DOC-2', name:'a document', title:'Doc2'})")
    g.query("CREATE (op2:Point {id:'OP-2', is_operator:true, status:'live', "
            "pointKind:'operator'})")
    g.query("MATCH (d:Document {id:'DOC-2'}), (o:Point {id:'OP-2'}) "
            "CREATE (d)-[:INPUT {idx:0}]->(o)")
    g.query("MATCH (o:Point {id:'OP-2'}), (t) WHERE t.id = $t "
            "CREATE (o)-[:IMPL {idx:1}]->(t)", params={"t": claim["id"]})

    block = assemble_why_blocks(sdk._get_proj(), [claim["id"]])[claim["id"]]
    chain_ids = {e["point_id"] for e in block["support_chain"]}
    assert "DOC-1" not in chain_ids, (
        "a non-epistemic IMPL source was presented as a supporter: "
        + repr(block["support_chain"]))
    assert "DOC-2" not in chain_ids, (
        "a non-epistemic OPERATOR INPUT source was presented as a supporter: "
        + repr(block["support_chain"]))
    nand_ids = {n["point_id"] for n in
                (block.get("conflicts") or {}).get("nands") or []}
    assert "SRC-1" not in nand_ids, (
        "a non-epistemic NANDer was presented as a counter-claim: "
        + repr(block.get("conflicts")))


def test_the_point_lane_gains_no_assessment_and_keeps_its_supersession(sdk):
    """A claim nothing judged gets NO ``assessment`` key, and still gets the
    Point-only supersession view it always had.

    Additive-only (#1353 D8) is the contract: the new dimension must not appear
    where the reasoning loop never wrote, or a consumer cannot tell "not judged"
    from "judged and unknown".
    """
    claim = sdk.create_point("statement", "an ordinary claim")
    supporter = sdk.create_point("statement", "an ordinary support")
    sdk.create_operator("IMPL", supporter["id"], [claim["id"]],
                        direction="->", label="supports")

    block = assemble_why_blocks(sdk._get_proj(), [claim["id"]])[claim["id"]]
    assert "assessment" not in block
    assert "supersession" in block, "the Point lane lost a dimension it owns"
    assert block["supersession"]["status"] == "live"
    assert block["support_chain"] and block["support_chain"][0]["operator_id"]


def test_a_derived_claim_that_is_not_a_judgement_is_not_an_assessment(sdk):
    """The discriminator is the DURABLE marker (``claimClass: 'derived'`` AND a
    ``judgementState``), not the shape of the arrow.

    Any derived statement wired IMPL into a goal is *some* derivation; only a
    judgement carries a state. Reading the arrow alone would present an
    unrelated derivation as the goal's verdict.
    """
    _indicator, _target, goal = _world(sdk, tag="derived")
    derived = sdk.create_point("statement", "a derived statement, no verdict",
                               claim_class="derived")
    sdk.create_operator("IMPL", derived["id"], [goal["id"]], direction="->")

    block = assemble_why_blocks(sdk._get_proj(), [goal["id"]])[goal["id"]]
    assert "assessment" not in block, (
        "a derived claim with no judgementState was presented as a verdict")
    # The support chain DOES name it — it is real support, just not a verdict.
    assert block["support_chain"][0]["point_id"] == derived["id"]

    # AND THE OTHER CONJUNCT: `claimClass = 'derived'`. A `judgementState` alone
    # is not a verdict either, and presenting such a claim as the goal's verdict
    # is the same defect from the other side. ⚠️ The shape is SDK-REACHABLE, not
    # writer-produced: `record_judgement` always passes
    # `claim_class="derived"` (`goal_judgement.py`), so no in-repo writer mints a
    # non-derived claim carrying `judgementState` — `sdk.create_point(...,
    # judgementState=...)` does, and the read must not be fooled by it. The
    # conjunct matters because the SIBLING reader discriminates on
    # `judgementState` ALONE (`goal_judgement.latest_judgement`), so dropping
    # `claimClass` here would make this read disagree with the writer's own
    # contract. Measured: the assertions above stay green with `claimClass`
    # dropped from the discriminator.
    other = _world(sdk, tag="nonderived")[2]
    stamped = sdk.create_point("statement", "a non-derived claim with a state",
                               judgementState="met")
    sdk.create_operator("IMPL", stamped["id"], [other["id"]], direction="->")
    other_block = assemble_why_blocks(sdk._get_proj(), [other["id"]])[other["id"]]
    assert "assessment" not in other_block, (
        "a NON-derived claim carrying a judgementState was presented as a "
        "verdict: " + repr(other_block.get("assessment")))


def test_an_eventId_only_event_is_not_explainable_and_that_is_recorded(sdk):
    """RESIDUAL 2, pinned as a behaviour rather than left to prose.

    Every anchor is ``id``-keyed (``WHERE n.id IN $ids``), so an ``:Event`` that
    carries ``eventId`` ONLY gets no block — while the same node surfaces fine as
    a NANDer, because the TRAVERSAL positions use ``epistemic_identity``. That is
    ``live.epistemic_identity``'s own split (a coalesced identity may be
    displayed, never used to address a node) and it is a real shape:
    ``projection/entities.py``'s ``MERGE (e:Event {eventId:$eid})`` writes no
    ``id``, and ``session_indexer`` writes ``eventId`` only.

    Pinned so a future widening is a DECISION: if the anchor starts resolving a
    coalesced key, this test reds and its docstring says what that costs
    (``eventId`` is not unique — #8879).

    ⚠️ The flip needs BOTH halves of the anchor — ``WHERE n.id IN $ids`` AND
    ``RETURN n.id`` — because the returned value is the block's key: widening
    only the WHERE leaves ``n.id`` null, so no block is emitted and this row
    stays green (measured, mutation pass F6). That is correct: a half-widening is
    not user-visible, so it must not be pinnable as if it were.
    """
    claim = sdk.create_point("statement", "a claim with a keyless counter-claim")
    sdk._get_proj().g.query(
        "CREATE (e:Event {eventId:'KEYLESS1', name:'a keyless reading', "
        "eventKind:'note', status:'live'})")
    sdk._get_proj().g.query(
        "MATCH (e:Event {eventId:'KEYLESS1'}), (t) WHERE t.id = $t "
        "CREATE (e)-[:NAND]->(t)", params={"t": claim["id"]})

    assert "KEYLESS1" not in assemble_why_blocks(sdk._get_proj(), ["KEYLESS1"]), (
        "the anchor now resolves a coalesced (non-unique) key — that is a new "
        "decision: widen this pin AND why.py's residual note together")
    nands = (assemble_why_blocks(sdk._get_proj(), [claim["id"]])[claim["id"]]
             .get("conflicts") or {}).get("nands") or []
    assert [n["content_snippet"] for n in nands] == ["a keyless reading"], (
        "the keyless Event must still surface as a TRAVERSAL position: " + repr(nands))


def test_the_fusion_order_is_total_so_row_order_cannot_decide(sdk):
    """ROUND-2 FIX, PINNED (#11928 mutation pass F2).

    Each assembler sorts on ``(weight is None, -weight, point_id, FUSION KEY)``.
    The last component is the INTERNAL node id — the same key the fusion dedupes
    on — because ``point_id`` alone is NOT unique (two distinct ``:Event``s can
    emit the same ``coalesce(id, eventId)``). Dropping it makes the order depend
    on Cypher row order, and NOTHING caught that: mutating all three sorts
    together left every test in the three changed files green. (No count is
    quoted here: it moves with every row added, and a figure copied from one run
    re-stales.)

    Two assertions, and they are different instruments on purpose:
      * BEHAVIOUR — two supporters whose display ids collide come out in
        ascending internal-node-id order (the fused order, not the arrival one);
      * SOURCE — every selection sort in this module actually ends on the fusion
        key. A behavioural row alone cannot be made to discriminate reliably,
        because whether the engine's raw row order happens to match the internal
        id order is not something a fixture can control; the source pin is what
        makes deleting the term red.
    """
    import inspect

    import tortoise.why as why_mod

    # SOURCE: all three selection sorts end on the fusion key.
    src = inspect.getsource(why_mod)
    for fn in ("_assemble_supports", "_assemble_conflicts", "_assemble_tradeoffs"):
        body = inspect.getsource(getattr(why_mod, fn))
        assert "kv[1][\"point_id\"], kv[0])" in body, (
            f"{fn}'s sort no longer ends on the fusion key (the internal node "
            "id) — the order becomes row-order dependent (see this test)")
    total_sorts = src.count("kv[1][\"point_id\"], kv[0])")
    assert total_sorts == 3, (
        f"expected exactly three total selection sorts in why.py, found {total_sorts}")

    # BEHAVIOUR: colliding display identities, fused order by internal id.
    claim = sdk.create_point("statement", "the fused-order claim")
    g = sdk._get_proj().g
    g.query("CREATE (e:Event {id:'DUP', name:'dup A', eventKind:'note', "
            "status:'live'})")
    g.query("CREATE (e:Event {eventId:'DUP', name:'dup B', eventKind:'note'})")
    for pattern in ("{id:'DUP'}", "{eventId:'DUP'}"):
        g.query(
            f"MATCH (a:Event {pattern}), (t) WHERE t.id = $t "
            "CREATE (a)-[:NAND]->(t)", params={"t": claim["id"]})
    internal = {
        row[0]: row[1] for row in g.query(
            "MATCH (a:Event), (t) WHERE t.id = $t "
            "MATCH (a)-[:NAND]->(t) "
            "RETURN a.name, id(a)", params={"t": claim["id"]}).result_set
    }
    block = assemble_why_blocks(sdk._get_proj(), [claim["id"]])[claim["id"]]
    order = [n["content_snippet"] for n in
             (block.get("conflicts") or {}).get("nands") or []]
    assert len(order) == 2, f"both counter-claims must fuse: {order}"
    assert order == sorted(order, key=lambda name: internal[name]), (
        "the chain order is not the fused (internal-id) order: "
        f"{order} vs {sorted(order, key=lambda n: internal[n])}")


# ── 5. bounded: budget + a fixed read count ───────────────────────────────

def test_an_unchanged_state_keeps_one_live_judgement_per_target(sdk):
    """D4 allows n targets per goal, so a goal can hold several live verdicts —
    the dimension is a bounded LIST, newest first, capped at
    ``W4_MAX_ASSESSMENTS``."""
    indicator, target_a, goal = _world(sdk, tag="multi")
    target_b = sdk.create_object("CI <= 5m [multi]", objectKind="target",
                                 comparator="<=", threshold=5.0, dwell=1)
    _reading(sdk, indicator, 7.0, at="2026-11-11T10:00:00Z")
    first = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                             target=Target.from_node(target_a))
    second = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                              target=Target.from_node(target_b))
    assert first["point_id"] != second["point_id"], (
        "two targets must not share one judgement (D4's scope guard)")

    block = assemble_why_blocks(sdk._get_proj(), [goal["id"]])[goal["id"]]
    judgements = [a["judgement_id"] for a in block["assessment"]]
    assert set(judgements) == {first["point_id"], second["point_id"]}
    assert len(judgements) <= W4_MAX_ASSESSMENTS

    # THE BUDGET ITSELF, with a fixture that EXCEEDS it: two targets cannot pin
    # a cap of three (measured — uncapping left every test green). D4 allows n
    # targets, so a goal with several must still emit at most W4_MAX_ASSESSMENTS.
    for i in range(4, 8):
        extra = sdk.create_object(f"CI <= {i}m [multi]", objectKind="target",
                                  comparator="<=", threshold=float(i), dwell=1)
        judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                         target=Target.from_node(extra))
    capped = assemble_why_blocks(sdk._get_proj(), [goal["id"]])[goal["id"]]
    assert len(capped["assessment"]) == W4_MAX_ASSESSMENTS, (
        "the assessment budget is not enforced: "
        f"{len(capped['assessment'])} verdicts emitted for a cap of "
        f"{W4_MAX_ASSESSMENTS}")
    # Newest first.
    assert judgements == sorted(
        judgements,
        key=lambda j: block["assessment"][judgements.index(j)]["evaluated_at"],
        reverse=True)


def test_the_batch_read_count_is_fixed_for_a_goal_batch(sdk, monkeypatch):
    """The sweep is bounded by DISTINCT LABELS IN THE BATCH, never by size — the
    S8 contract survives the widening.

    A one-goal batch and a three-goal batch issue the SAME number of queries.
    ⚠️ The exact number is not 7 for an ``:Object`` batch: the anchor is
    FIRST-WINS (``epistemic_labels_of``' rule), so every label ORDERED BEFORE the
    present one costs one empty indexed probe — ``Point`` and ``Subject`` for an
    ``:Object`` anchor (measured 9). The bound is what matters: ≤ one probe per
    label (4) + the six dependent reads, and independent of batch size.
    """
    from tortoise.live import EPISTEMIC_LABELS
    from tortoise.projection import _GuardedGraph

    goals = [_world(sdk, tag=f"count-{i}")[2] for i in range(3)]
    proj = sdk._get_proj()

    def _count_for(batch):
        calls = []
        real_query = proj.g.query

        def _counting_query(self, cypher, params=None, timeout=None):
            calls.append(cypher)
            return real_query(cypher, params=params, timeout=timeout)

        monkeypatch.setattr(_GuardedGraph, "query", _counting_query)
        try:
            blocks = assemble_why_blocks(proj, batch)
        finally:
            monkeypatch.setattr(_GuardedGraph, "query", real_query)
        assert set(blocks) == set(batch)
        return len(calls)

    one = _count_for([goals[0]["id"]])
    three = _count_for([g["id"] for g in goals])
    assert one == three, (
        f"a goal batch issued {one} then {three} queries — the count must be "
        "size-independent (a per-result loop is the S8 defect)")
    assert one <= len(EPISTEMIC_LABELS) + 6, (
        f"{one} queries for one present label: the sweep is bounded by the "
        "label set (≤1 probe per label + 6 dependent reads), not by the batch")


# ── the enriched item + the ask lane carry the dimension ──────────────────

def _two_label_batch(dimension, sdk):
    """A Point id + the ``:Object`` id whose DIMENSION must still be emitted.

    The Point is always the FIRST resolved label (``EPISTEMIC_LABELS`` order), so a
    Point-side read failure is the one that used to suppress everything after it.
    Returns ``(proj, point_id, object_id, block_key)``.
    """
    claim = sdk.create_point("statement", "the cache is warm")
    proj = sdk._get_proj()
    if dimension == "conflicts":
        _, _, anchor = _world(sdk, tag="legs-conflicts")
        counter = sdk.create_point("statement", "the cache is cold")
        proj.g.query(
            "MATCH (a:Point {id:$a}), (t) WHERE t.id = $t CREATE (a)-[:NAND]->(t)",
            params={"a": counter["id"], "t": anchor["id"]})
        return proj, claim["id"], anchor["id"], "conflicts"
    if dimension == "tradeoffs":
        anchor = sdk.create_object("how to land it [legs]", objectKind="decision")
        op_a = sdk.create_object("behind a flag [legs]", objectKind="plan")
        op_b = sdk.create_object("to everyone [legs]", objectKind="plan")
        sdk.create_operator("IMPL", anchor["id"], [op_a["id"]])
        sdk.create_operator("IMPL", anchor["id"], [op_b["id"]])
        return proj, claim["id"], anchor["id"], "tradeoffs"
    key = "assessment" if dimension == "assessments" else "support_chain"
    indicator, target_node, goal = _world(sdk, tag=f"legs-{dimension}")
    _judge(sdk, indicator, target_node, goal, 8.0, at="2026-05-01T00:00:00Z")
    return proj, claim["id"], goal["id"], key


@pytest.mark.parametrize("dimension,template_attr", [
    # the two support legs are independent reads and each has its own try
    ("support", "_SUPPORT_OP_CYPHER"),
    ("support", "_SUPPORT_DIRECT_CYPHER"),
    ("conflicts", "_CONFLICTS_CYPHER"),
    ("tradeoffs", "_TRADEOFFS_CYPHER"),
    ("assessments", "_ASSESSMENTS_CYPHER"),
])
def test_a_point_side_read_failure_does_not_suppress_the_object_dimension(
        sdk, monkeypatch, dimension, template_attr):
    """Fail-open is per DIMENSION, per LABEL and per LEG — one pin per site.

    Each dimension's ``try`` used to wrap the whole ``for _label, _ids in
    resolved.items()`` loop, and the support dimension's two legs shared one
    ``try`` inside it. Both were equivalent while every dimension was ONE query
    over the ONE resolved label; after the #11928 widening neither is, because
    ``Point`` is always FIRST: a raise there aborted every remaining label AND the
    support dimension's second leg, so the block survived and answered nothing —
    the trust failure this issue exists to end.

    ⛔ PARAMETRIZED OVER EVERY SITE ON PURPOSE (review round 4, finding 2). A pin
    that injects into one dimension leaves the other three revertible green: the
    first version of this test poisoned only the support direct leg, so moving the
    ``try`` back outside the loop at the conflicts/tradeoffs/assessment sites kept
    the suite green — the same "a pin that passes because the other arm fired"
    shape review round 2 filed. Each ``template_attr`` below names ONE mutant, and
    each mutant reds its own case and no other.
    """
    from tortoise import why as why_module
    from tortoise.projection import _GuardedGraph

    proj, point_id, object_id, key = _two_label_batch(dimension, sdk)
    batch = [point_id, object_id]

    healthy = assemble_why_blocks(proj, batch)
    assert healthy[object_id].get(key), (
        f"fixture: the Object anchor has no {key} before any injection")

    poisoned = why_module._for_label(getattr(why_module, template_attr), "Point")
    real_query = proj.g.query

    def _query(self, cypher, params=None, timeout=None):
        if cypher == poisoned:
            raise RuntimeError(f"injected Point-side {template_attr} failure")
        return real_query(cypher, params=params, timeout=timeout)

    monkeypatch.setattr(_GuardedGraph, "query", _query)
    try:
        blocks = assemble_why_blocks(proj, batch)
    finally:
        monkeypatch.setattr(_GuardedGraph, "query", real_query)

    assert not blocks[point_id].get(key), (
        "the injection did not take effect on the Point side: "
        + repr(blocks[point_id].get(key)))
    assert blocks[object_id].get(key), (
        f"a Point-side {template_attr} failure suppressed the Object anchor's "
        f"{key} — its own read never ran: " + repr(blocks[object_id].get(key)))


def test_the_support_dimensions_direct_leg_still_runs_when_the_operator_leg_raises(
        sdk, monkeypatch):
    """The support dimension's TWO LEGS each have their own try (review round 4).

    ``_SUPPORT_OP_CYPHER`` and ``_SUPPORT_DIRECT_CYPHER`` are independent reads that
    happened to share one ``try``. A raise in the first (a row-shape or fold error is
    data-dependent and says nothing about the second) then skipped the second leg's
    QUERY entirely, and the chain came back empty although a read that would have
    answered was never run. Measured as a mutant: fold the two legs back into one
    ``try`` and this pin reports ``[]`` while every other case in this file stays
    green (the parametrized cases above inject per dimension, not per leg).
    """
    from tortoise import why as why_module
    from tortoise.projection import _GuardedGraph

    proj = sdk._get_proj()
    claim = sdk.create_point("caching", "the cache is warm")
    supporter = sdk.create_point("cache-hit-rate", "the hit rate is 98%")
    proj.g.query(
        "MATCH (a:Point {id:$a}), (t:Point {id:$t}) CREATE (a)-[:IMPL]->(t)",
        params={"a": supporter["id"], "t": claim["id"]})
    healthy = assemble_why_blocks(proj, [claim["id"]])
    assert [e["content_snippet"] for e in healthy[claim["id"]]["support_chain"]] == [
        "the hit rate is 98%"], "fixture: the direct supporter is not in the chain"

    poisoned = why_module._for_label(why_module._SUPPORT_OP_CYPHER, "Point")
    real_query = proj.g.query

    def _query(self, cypher, params=None, timeout=None):
        if cypher == poisoned:
            raise RuntimeError("injected operator-leg failure")
        return real_query(cypher, params=params, timeout=timeout)

    monkeypatch.setattr(_GuardedGraph, "query", _query)
    try:
        blocks = assemble_why_blocks(proj, [claim["id"]])
    finally:
        monkeypatch.setattr(_GuardedGraph, "query", real_query)

    assert [e["content_snippet"] for e in blocks[claim["id"]]["support_chain"]] == [
        "the hit rate is 98%"], (
        "the operator leg's failure skipped the DIRECT leg's read — the two legs "
        "share one try: " + repr(blocks[claim["id"]].get("support_chain")))


def test_the_enriched_item_and_ask_entry_carry_the_assessment(sdk, monkeypatch):
    """The dimension reaches the surfaces that answer the question: the enriched
    item (search/analyze/recall) and ``item_to_why_entry`` (the ask lane)."""
    indicator, target_node, goal = _world(sdk, tag="surface")
    recorded = _judge(sdk, indicator, target_node, goal, 12.0,
                     at="2026-11-11T10:00:00Z")
    monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")

    items = enrich_items(sdk._get_proj(), [{"id": goal["id"], "content": "CI is fast"}])
    assert items[0]["assessment"][0]["judgement_id"] == recorded["point_id"]
    entry = item_to_why_entry(items[0])
    assert entry is not None, (
        "an item enriched ONLY with an assessment must still project to a why "
        "entry — the gate checks every dimension it can emit")
    assert entry["assessment"] == items[0]["assessment"]
    # ⛔ THE GATE CLAUSE, DIRECTLY. The fixture above also carries why/conflicts,
    # so deleting `"assessment" not in item` from `item_to_why_entry`'s gate left
    # that row GREEN (measured, mutation pass F4). This row is the one that
    # isolates the clause: an item whose ONLY enrichment is an assessment.
    only_assessment = {"id": goal["id"],
                       "assessment": [{"judgement_id": recorded["point_id"]}]}
    isolated = item_to_why_entry(only_assessment)
    assert isolated is not None and isolated["assessment"] == [
        {"judgement_id": recorded["point_id"]}], (
        "an assessment-only item was dropped by the projection gate: " + repr(isolated))
    # AND THE WIRE PROJECTION. `volunteer.why_entry` is the ONE shape `/v1/context`
    # and the pushed envelope serve; deleting its assessment line left every
    # volunteer test and this whole file green (measured, review round 7).
    from tortoise.volunteer import why_entry

    block = assemble_why_blocks(sdk._get_proj(), [goal["id"]])[goal["id"]]
    wire = why_entry(block)
    assert wire["assessment"] == block["assessment"], (
        "the wire why-entry dropped the assessment dimension: " + repr(wire))
