"""#14304 — the ORIENTATION leg: an edge's ends follow the DECLARATION, not word order.

WHY THIS FILE EXISTS
--------------------
`docs/evidence/14592-operating-model-validation/REPORT.md` §G2 measured the
defect on a fresh graph with a fixed corpus: a document stating *"Marta Reyes
owns the cold-chain temperature log"* produced **8** `ownedBy` rows written
`Subject -> Object`, i.e. `Marta Reyes --ownedBy--> the log`. Under `ownedBy`'s
documented sense (`docs/ONTOLOGY.md` §3.6, `pack_registry.SEMANTICS_DIRECTIONS`)
that asserts **the log owns Marta** — the fact is present, its direction
reversed.

`#11656`'s endpoint leg cannot see it: both endpoints are legal. Its own
`reversed_pair` outcome cannot see it either, because no pack declares
`ownedBy` for `core:naturalPerson`↔`Object` in EITHER orientation — the measured
write is `undeclared_pair` both ways, and that verdict is true while saying
nothing about the inversion.

WHAT IS ASSERTED HERE (and what the mutation is)
-----------------------------------------------
Every case below is a pure function of `(relations, entity_index, declared)` or
of a recording fake writer — no graph, no model, no Docker — so it belongs on
the surface that gates a merge rather than in the on-demand eval lane (the same
reasoning `tests/test_relation_write_11237.py` records).

⛔ NON-VACUITY: the mutant is the pre-`#14304` write path, which planned
`RelationEdge(src_id=Dana, dst_id=account)`. `test_the_corpus_pair_is_written_
owned_first` and `test_the_writer_receives_the_declared_orientation` RED on it
(both assert the exact id order handed to the writer), and
`test_the_receipt_declares_the_correction` reds too (the pre-fix receipt carries
no `orientation` key at all). Measured, not asserted — and re-runnable:
`docs/evidence/14304-ownedby-direction/probe_prefix_14304.py` mirrors those three
assertions against the parent tree, with the transcript and the exact command in
that bundle's `README.md` §3a. (⛔ Not the lane's `WORKTREE-REPORT.md`: it is
gitignored by design, so a citation to it is unverifiable for every other reader.)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.pack_hierarchy import DeclaredHierarchy
from tortoise.relation_guards import (
    ABSTAIN,
    ABSTAIN_NO_LABEL_SIDE,
    ABSTAIN_ORIENTATION_AMBIGUOUS,
    ORIENTATION_AS_ASKED,
    ORIENTATION_REVERSED,
    RULE_ORIENTATION,
    declared_label_sides,
    orient_declared_pair,
)
from tortoise.relation_write import apply_relation_edges, plan_relation_edges

#: The `#14592` corpus pair, with the kinds the census resolved
#: (`out/census.json`: `Marta Reyes` = `core:naturalPerson`, a `:Subject`; the
#: log = an `Object`). Ids are the receipt's own, shortened for readability.
DANA = "subj-dana"
ACCOUNT = "obj-nordicfreight"
INDEX = {
    "Dana": [(DANA, "core:naturalPerson")],
    "NordicFreight account": [(ACCOUNT, "object")],
}


class _RecordingWriter:
    """An SDK whose one linking verb records what it was asked to write."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def link_entities(self, predicate, src, dst, **kw):
        self.calls.append((predicate, src, dst, kw))
        return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# The verdict, on its own — the declarations, and nothing else
# ─────────────────────────────────────────────────────────────────────────────


def test_the_measured_corpus_pair_is_reversed_but_UNDECLARED_in_both_orders():
    """⛔ The premise of this whole leg: `#11656`'s own verdicts cannot see it.

    `ownedBy` IS declared (`packs/dev`: `core:goal -> Subject`), so the pair leg
    has a premise — and it answers `reversed_pair` for the reverse of the
    DECLARED pair, which the measured pair is not. `core:naturalPerson -> object`
    and `object -> core:naturalPerson` are both NOT `reversed_pair`, because no
    pack declares this pair in either order. (The ORIENTATION leg is a different
    question — it reads the label-spelled side, which is what
    `test_the_owner_on_the_source_side_is_reversed` and
    `test_the_owner_on_the_target_side_is_left_alone` assert, not this test.)
    """
    from tortoise.relation_guards import (
        UNDECLARED_PAIR,
        declared_relation_pairs,
        relation_pair_verdict,
    )

    declared = declared_relation_pairs()
    assert declared is not None
    # The endpoint leg's answer for the measured write — legal, and silent.
    # ⛔ `==` and not `not in (REVERSED_PAIR,)`: the loose spelling also passes on
    # `accept`/`normalised`/`abstained`, so the day a pack declares this pair the
    # premise silently becomes false while the test stays green and keeps
    # claiming it (measured — the pair leg answers `undeclared_pair` both ways at
    # this head).
    assert relation_pair_verdict(
        "ownedBy", "core:naturalPerson", "object",
        from_label="Subject", to_label="Object", declared=declared,
    ).outcome == UNDECLARED_PAIR
    # …and the same pair, reversed, is also NOT `reversed_pair`: no pack declares
    # this pair in either order, which is why the pair leg could never fix it.
    assert relation_pair_verdict(
        "ownedBy", "object", "core:naturalPerson",
        from_label="Object", to_label="Subject", declared=declared,
    ).outcome == UNDECLARED_PAIR


def test_the_owner_on_the_source_side_is_reversed():
    verdict = orient_declared_pair(
        "ownedBy", "core:naturalPerson", "object",
        from_label="Subject", to_label="Object")
    assert verdict.outcome == ORIENTATION_REVERSED
    assert verdict.reversed
    payload = verdict.payload or {}
    assert payload["rule"] == RULE_ORIENTATION
    assert payload["stated"] == {"from": "core:naturalPerson", "to": "object"}
    assert payload["written"] == {"from": "object", "to": "core:naturalPerson"}
    assert payload["declaredFor"] == [{"fromKind": "core:goal",
                                       "toKind": "Subject"}]
    assert payload["evidence"] == "declared-endpoint-label"


def test_the_owner_on_the_target_side_is_left_alone():
    """The other direction of the same rule — a correct write is NOT flipped.

    This is the control that stops the leg from being "flip every ownedBy": the
    declared orientation already holds here, so nothing is reported and nothing
    moves.
    """
    verdict = orient_declared_pair(
        "ownedBy", "object", "core:naturalPerson",
        from_label="Object", to_label="Subject")
    assert verdict.outcome == ORIENTATION_AS_ASKED
    assert verdict.payload is None


def test_a_declared_pair_whose_end_is_a_LABEL_reversed_uses_the_LABEL_premise():
    """A declared pair with a LABEL end reversed → premise (b), not premise (a).

    ⚠️ The name used to be `…is_also_reversed`, which described the guard wrongly:
    this ask is NOT which premise answers it — `ownedBy` declares
    `core:goal -> Subject`, and `Subject` is a LABEL, so premise (a)'s exact-pair
    match cannot reach it from KINDS. The correction comes from the label leg and
    therefore carries `evidence="declared-endpoint-label"`. Premise (a)'s own tag
    is pinned separately, on `causes`
    (`test_a_KIND_only_declared_pair_reversed_carries_the_DECLARED_PAIR_evidence`).

    Mutation: make `declared_label_sides` return `((), ())` for this predicate and
    the outcome becomes `abstain` (`no-label-endpoint`).
    """
    verdict = orient_declared_pair("ownedBy", "core:naturalPerson", "core:goal")
    assert verdict.outcome == ORIENTATION_REVERSED
    assert (verdict.payload or {})["evidence"] == "declared-endpoint-label"


def test_a_KIND_only_declared_pair_reversed_carries_the_DECLARED_PAIR_evidence():
    """⛔ PREMISE (a)'s evidence tag, which nothing else pinned.

    `config/ci-surfaces.yml`'s `durations:` row for this file claims the weight
    covers *the exact declared pair asked in REVERSE — `reversed_orientation` with
    `evidence="declared-pair"`*, and that string was pinned NOWHERE: the two
    `payload["evidence"]` assertions above expect `declared-endpoint-label`, and
    the `hasPart` case asserts the OUTCOME only. So a regression that relabelled
    premise (a)'s evidence would have merged green while the manifest asserted
    coverage of it (round-3 review, P2).

    `causes` is the right witness and the one that matters — it is the
    PRICED direct-arm predicate (`dev:bug -> dev:incident`, `->` → `IMPL`), so
    this is also the pin that the priced path really is reached. ⚠️ Its reverse
    ask is the ask a write CAN produce (both ends are KINDS; no `Subject` label
    is passed as a kind).
    """
    verdict = orient_declared_pair("causes", "dev:incident", "dev:bug")
    assert verdict.outcome == ORIENTATION_REVERSED
    assert (verdict.payload or {})["evidence"] == "declared-pair"
    assert verdict.payload["declaredFor"] == [
        {"fromKind": "dev:bug", "toKind": "dev:incident"}
    ]


def test_a_pair_the_declaration_names_exactly_is_never_touched():
    """A pair the packs declare, in the declared order, is left exactly as asked.

    ⚠️ The evidence is the LABEL leg here, not the pair leg, and that is not a
    detail: the declared `toKind` is the LABEL `Subject`, so `core:goal ->
    core:naturalPerson` matches no pair unless the endpoint's label is supplied —
    `orient_declared_pair` is called from the planner with KINDS only, so it must
    reach `as_asked` on the label leg. Asserting `reason == "declared-pair"` here
    was wrong (it needs `to_label="Subject"`), and a test that only passes with
    labels the real caller does not have would certify a path nothing runs.
    """
    verdict = orient_declared_pair("ownedBy", "core:goal", "core:naturalPerson")
    assert verdict.outcome == ORIENTATION_AS_ASKED
    assert verdict.reason == "declared-endpoint-label"
    # …and WITH the labels the pair leg's own evidence answers:
    assert orient_declared_pair(
        "ownedBy", "core:goal", "core:naturalPerson",
        from_label="Object", to_label="Subject").reason == "declared-pair"


def test_a_predicate_with_KIND_endpoints_abstains():
    """`hasPart: dev:theme -> dev:epic` says nothing about orientation.

    ⛔ The widening guard: any predicate whose declared ends are both kinds must
    not acquire an orientation opinion, or the leg starts rewriting pairs the
    packs never put in an order.

    The one case this leg DOES answer for `hasPart` is the pack's own pair in
    reverse (`dev:epic -> dev:theme`) — a genuine reversal, and the overlap with
    `#11656`'s `reversed_pair`, asserted below so the two legs cannot drift.
    """
    assert orient_declared_pair(
        "hasPart", "dev:theme", "dev:epic").outcome == ORIENTATION_AS_ASKED
    assert orient_declared_pair(
        "hasPart", "dev:epic", "dev:theme").outcome == ORIENTATION_REVERSED
    for from_kind, to_kind in (("dev:indicator", "dev:epic"),
                               ("dev:theme", "dev:indicator")):
        verdict = orient_declared_pair("hasPart", from_kind, to_kind)
        assert verdict.outcome == ABSTAIN, (from_kind, to_kind)
        assert verdict.reason == ABSTAIN_NO_LABEL_SIDE
    assert declared_label_sides("hasPart") == (frozenset(), frozenset())


def test_two_subjects_are_not_orientable():
    """"Dev manages Marta" — the declaration cannot say which Subject owns which."""
    verdict = orient_declared_pair(
        "ownedBy", "core:naturalPerson", "core:team",
        from_label="Subject", to_label="Subject")
    assert verdict.outcome == ABSTAIN
    assert verdict.reason == ABSTAIN_ORIENTATION_AMBIGUOUS


def test_an_undeclared_predicate_abstains():
    verdict = orient_declared_pair("related", "object", "object")
    assert verdict.outcome == ABSTAIN


def test_an_unresolved_endpoint_abstains():
    verdict = orient_declared_pair("ownedBy", None, "object")
    assert verdict.outcome == ABSTAIN


def test_an_unreadable_declaration_set_abstains(monkeypatch):
    """A declaration read that FAILED must abstain, never silently accept.

    ⛔ The `declared is None` arm is the fail-closed half of this leg: a write
    must not depend on a read this leg could not make, so the caller keeps its
    OWN order — but it keeps it because the leg said it could not answer, not
    because the order was checked. Pinned here because
    `config/ci-surfaces.yml`'s `durations:` row for this file claims the
    abstention set covers "an unreadable declaration set" (#14790 review): the
    only other unreadable test in the tree pins the PAIR leg
    (`test_relation_guards_14263.py`), so without this case the manifest's claim
    about THIS file was false.
    """
    import tortoise.relation_guards as relation_guards

    monkeypatch.setattr(relation_guards, "declared_relation_pairs", lambda: None)
    verdict = relation_guards.orient_declared_pair(
        "ownedBy", "core:naturalPerson", "object")
    assert verdict.outcome == ABSTAIN
    assert verdict.reason == relation_guards.ABSTAIN_UNREADABLE
    # …and the planner takes the same path: no correction, and no raise.
    edges, _drops = plan_relation_edges(
        [{"src": "Dana", "dst": "NordicFreight account", "predicate": "ownedBy"}],
        INDEX,
    )
    assert [(e.src_id, e.dst_id) for e in edges] == [(DANA, ACCOUNT)]
    assert edges[0].orientation is None


# ─────────────────────────────────────────────────────────────────────────────
# The write path — the plan and the receipt
# ─────────────────────────────────────────────────────────────────────────────


def test_the_corpus_pair_is_written_owned_first():
    """⛔ THE DEFECT, at the seam that produced it (#14592 §G2).

    Stated: `Dana --ownedBy--> NordicFreight account` (word order — the sentence
    is "Dana owns the NordicFreight account"). Written: the declared orientation,
    `account --ownedBy--> Dana`, because every declared `ownedBy` row puts its
    `:Subject` on the target side and `SDK._get_owned_entities` reads
    `(s:Subject)<-[:ownedBy]-(e)`.

    ON THE PRE-FIX CODE THIS IS `src_id == DANA` — the inversion.
    """
    edges, drops = plan_relation_edges(
        [{"src": "Dana", "dst": "NordicFreight account",
          "predicate": "ownedBy"}],
        INDEX)
    assert drops == []
    assert len(edges) == 1
    edge = edges[0]
    assert (edge.src_id, edge.dst_id) == (ACCOUNT, DANA)
    assert edge.orientation is not None
    assert edge.orientation["stated"] == {"from": "core:naturalPerson",
                                          "to": "object"}
    # The NAMES stay as STATED: they identify the statement, not the edge.
    assert (edge.src_name, edge.dst_name) == ("Dana", "NordicFreight account")


def test_an_already_correct_pair_is_planned_unchanged():
    edges, _drops = plan_relation_edges(
        [{"src": "NordicFreight account", "dst": "Dana",
          "predicate": "ownedBy"}],
        INDEX)
    assert (edges[0].src_id, edges[0].dst_id) == (ACCOUNT, DANA)
    assert edges[0].orientation is None


def test_the_writer_receives_the_declared_orientation():
    """The ENDS reach `link_entities` in the declared order — the whole fix."""
    writer = _RecordingWriter()
    result = apply_relation_edges(
        writer,
        [{"src": "Dana", "dst": "NordicFreight account",
          "predicate": "ownedBy"}],
        INDEX)
    assert [call[:3] for call in writer.calls] == [("ownedBy", ACCOUNT, DANA)]
    assert result["written"] == 1
    assert result["reoriented"] == 1


def test_the_receipt_declares_the_correction():
    """AX Rule 1: a redirect is DECLARED — on the receipt AND the warnings."""
    writer = _RecordingWriter()
    warnings: list[str] = []
    result = apply_relation_edges(
        writer,
        [{"src": "Dana", "dst": "NordicFreight account",
          "predicate": "ownedBy"}],
        INDEX, warn=warnings.append)
    row = result["edges"][0]
    assert row["orientation"] is not None, \
        "the pre-fix receipt has no `orientation` key at all"
    assert row["orientation"]["rule"] == RULE_ORIENTATION
    assert row["direction"] is None  # structural arm carries no direction
    assert any("RE-ORIENTED (#14304)" in w for w in warnings), warnings


def test_the_HIERARCHY_arm_declares_the_correction_it_applied():
    """⛔ THE THIRD LANDING ARM — and the one where the declaration was unpinned.

    A pack-declared hierarchy (`dev`: `hasPart`, `theme -> epic`) lands on its own
    `RelationEdge` construction, not on the story's-verb arm. A statement in the
    REVERSE order (`epic --hasPart--> theme`) is oriented FIRST, so the declared
    pair is restored **and** the hierarchy edge carries the correction.

    Mutation, and it is the reason this test exists: delete
    `orientation=orientation` from the hierarchy arm's `RelationEdge(...)`
    (`relation_write.py`, the `RELATION_STRUCTURAL` + `declared_by=edge.pack`
    construction) and the whole suite stays GREEN while `orientation` reads
    `None` on a row the seam DID correct — a live fix with no assertion
    (found by this PR's mutation-attribution pass, agent-infra#3442). The WRITTEN
    edge is unaffected either way, which is exactly why nothing caught it: the
    graph is right and only the declaration is lost, and
    `docs/ax/ax-principles.md` Rule 1 makes the declaration mandatory.

    Second mutation: drop the swap and `src_id`/`dst_id` come out stated-order.
    """
    declared = (DeclaredHierarchy(
        pack="dev", path="Theme \u2192 Epic", predicate="hasPart",
        from_kind="dev:theme", to_kind="dev:epic", write_path="structural"),)
    index = {
        "quarry": [("obj-theme", "dev:theme")],
        "auth table": [("obj-epic", "dev:epic")],
    }
    # Stated in the REVERSE of the declared pair (`epic -> theme`).
    edges, drops = plan_relation_edges(
        [{"src": "auth table", "dst": "quarry", "predicate": "hasPart"}],
        index, declared=declared)
    assert not drops, drops
    assert len(edges) == 1
    edge = edges[0]
    assert (edge.src_id, edge.dst_id) == ("obj-theme", "obj-epic"), \
        "the declaration, not the sentence's word order, fixes the ends"
    assert edge.predicate == "hasPart"
    assert edge.write_path == "structural"
    assert edge.declared_by == "dev"
    assert edge.orientation is not None, \
        "the hierarchy arm must DECLARE the correction it applied"
    assert edge.orientation["outcome"] == ORIENTATION_REVERSED
    assert edge.orientation["evidence"] == "declared-pair"
    assert edge.orientation["rule"] == RULE_ORIENTATION


def test_a_correct_pair_raises_no_warning_and_counts_zero():
    writer = _RecordingWriter()
    warnings: list[str] = []
    result = apply_relation_edges(
        writer,
        [{"src": "NordicFreight account", "dst": "Dana",
          "predicate": "ownedBy"}],
        INDEX, warn=warnings.append)
    assert result["reoriented"] == 0
    assert result["edges"][0]["orientation"] is None
    assert not any("#14304" in w for w in warnings), warnings


def test_the_correction_carries_no_prose_from_the_record():
    """The AX payload is built from the PREDICATE and the two ENDS — never prose.

    ⛔ `RelationEdge.orientation` is a DICT, so the `#10000` typed-record pin in
    `tests/test_relation_write_11237.py` (which walks STRINGS on the dataclass)
    does not reach inside it. The channel is closed by construction — every
    string in the payload is a predicate, a kind or a rendered template — and
    this is the assertion that keeps it that way, on a record stuffed with
    narrative the way a caller or a replay could stuff it.
    """
    import json

    prose = "Dana owns the NordicFreight account, and remember the Tuesday call"
    edges, _drops = plan_relation_edges([{
        "src": "Dana", "dst": "NordicFreight account",
        "predicate": "ownedBy",
        "summary": prose, "_searchText": prose, "transcript": prose,
        "content": prose,
    }], INDEX)
    edge = edges[0]
    assert edge.orientation is not None
    rendered = json.dumps(edge.orientation)
    assert prose not in rendered
    # The prose sentence is not reachable through any of its distinctive tokens
    # either — the payload names KINDS and the PREDICATE, never the story.
    assert "Tuesday" not in rendered
    assert "core:naturalPerson" in rendered and "ownedBy" in rendered


def test_two_statements_of_one_fact_are_one_edge_after_orientation():
    """`A owns B` and `B --ownedBy--> A` are the SAME edge once oriented.

    ⛔ A silent drop would be the one thing this leg must not do, and ⛔ nor is
    a second, opposite edge written. The second statement reaches the plan's
    identity key AFTER its ends were oriented, so it is the same edge and is
    deduped — the same accounting a literal repeat gets, ``folded`` EMPTY
    because the predicate did not change (`_fold_onto` returns on an identical
    ``stated_predicate``; a fold records a DIFFERENT meaning over one edge).
    """
    edges, drops = plan_relation_edges(
        [{"src": "Dana", "dst": "NordicFreight account",
          "predicate": "ownedBy"},
         {"src": "NordicFreight account", "dst": "Dana",
          "predicate": "ownedBy"}],
        INDEX)
    assert drops == []
    assert len(edges) == 1
    assert (edges[0].src_id, edges[0].dst_id) == (ACCOUNT, DANA)
    # No meaning was folded away: the two statements said the same thing, so the
    # dedupe is not a fold and must not be counted as one.
    assert edges[0].folded == ()


def test_a_reverse_row_places_the_labelled_end_by_the_SPELLING_and_keeps_its_direction():
    """A `direction: "<-"` row is a PLACEMENT rule, not an ownership claim.

    `packs/venture`'s `holds` is the shipped reverse row: `venture:investment ->
    Subject`, `direction: "<-"` (#11083), where the labelled `:Subject` is the
    company the investment is a stake IN — the OWNED end, not the owner. The leg
    must still place that label on the side the row spells (the TO side), and
    must leave the `<-` sense token alone: the redirect moves the ENDS, never
    the meaning.

    The mutation this pins: a leg that inferred "the label is the owner, so it
    goes on the target" would pass on `ownedBy` and still be wrong here; one
    that swapped the ends of the ALREADY declared order would invert a correct
    row. Both are measured below in the same test.
    """
    investment = "inv-nordic"
    person = "subj-dana"
    index = {
        "Nordic Capital": [(investment, "venture:investment")],
        "Dana": [(person, "core:naturalPerson")],
    }
    edges, drops = plan_relation_edges(
        [{"src": "Nordic Capital", "dst": "Dana", "predicate": "holds"}],
        index)
    assert drops == []
    assert (edges[0].src_id, edges[0].dst_id) == (investment, person)
    # The declaration's own sense survives: a `<-` row keeps `<-`.
    assert edges[0].direction == "<-"
    assert edges[0].orientation is None, (
        "the statement already has the labelled end on the declared side — "
        "a redirect here would report a correction that did not happen")

    # …and the reversed ask is redirected to the SAME written edge, with the
    # sense token still `<-` (the redirect moves ends, not meaning).
    reversed_edges, reversed_drops = plan_relation_edges(
        [{"src": "Dana", "dst": "Nordic Capital", "predicate": "holds"}],
        index)
    assert reversed_drops == []
    assert (reversed_edges[0].src_id, reversed_edges[0].dst_id) == (
        investment, person)
    assert reversed_edges[0].direction == "<-"
    assert reversed_edges[0].orientation is not None, (
        "a swapped pair on a `<-` row is still a redirect and must be declared")


def test_an_unresolved_endpoint_is_still_a_drop_not_a_swap():
    """The orientation leg never rescues a pair the endpoint checks reject."""
    edges, drops = plan_relation_edges(
        [{"src": "Dana", "dst": "nobody knows", "predicate": "ownedBy"}],
        INDEX)
    assert edges == []
    assert len(drops) == 1
    assert drops[0].predicate == "ownedBy"


@pytest.mark.parametrize("predicate", ["managedBy", "authoredBy"])
def test_the_other_responsibility_verbs_abstain_until_a_pack_declares_them(
        predicate):
    """⛔ NO INVENTED VOCABULARY: `managedBy`/`authoredBy` are not declared as
    RELATIONS by any shipped pack (`declared_relation_pairs()` has no row), so
    this leg has no premise for them and stays out of the way. The fix is a
    declaration, not a hard-coded verb list — see the lane report's residual.
    """
    verdict = orient_declared_pair(predicate, "core:naturalPerson", "object")
    assert verdict.outcome == ABSTAIN
