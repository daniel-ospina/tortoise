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
    assessment = blocks[goal["id"]]["assessment"][0]
    assert assessment["judgement_id"] == unmet["point_id"]
    assert assessment["state"] == "unmet"
    assert assessment["judgement_id"] != met["point_id"]

    # The goal's OWN ep block is the propagated number, not an invented prior.
    ep = blocks[goal["id"]]["ep"]
    assert ep["has_ep"] is True, (
        "the goal's belief is EP's, but the ep read reported it unmeasured: "
        + repr(ep))


# ── 2. a READING (an :Event) is explainable ───────────────────────────────

def test_a_reading_event_is_explainable_and_carries_its_name(sdk):
    """An ``:Event`` gets a block, and its text is its ``name``.

    This is the #8169 defect class: a read that matches but returns an empty
    snippet is inert — the block exists and says nothing.
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


# ── 4. the :Point lane is unchanged ───────────────────────────────────────

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
