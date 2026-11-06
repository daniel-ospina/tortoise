"""#10999 — S5 Job 1: the batch's own story reaches the graph.

The batch is a unit of work with its own narrative: what it was for, what it
concluded. Before this channel the pipeline could carry what the story
STORED (points, entities, events) and never what it CONCLUDED — an
entity↔entity relation only the narrative states ("A changed because of B").
Those conclusions are EDGES; the story's TEXT stays out of the graph
(#10000's P0, §9.1/D1), so a typed relation is the one shape of "the story"
that may legitimately be carried.

Every test here is offline and deterministic — no model, no graph, no network.
`execute_embed` is S5, the batch-finalise boundary, and it is driven directly.

Red-first evidence (measured on `origin/main` before this change, 277a559b1a):
    $ git show origin/main:tortoise/extractor_v2.py | grep -c '_assemble_relations'
    0
    $ ... | sed -n '/^OUTPUT_CONTRACT/,/^}/p' | grep -c relations
    0
    $ ... | sed -n '/^_OUTPUT_SCHEMA/,/^}/p' | grep -c relations
    0
  (`grep -c relations` over the WHOLE file is NOT 0 — the word appears in
  unrelated prose comments — so the citations above are scoped to the
  contract, the schema and the helper, which is what this change adds.)
  The contract has no entity↔entity channel there, so
  `test_contract_advertises_the_relations_channel` cannot pass, and
  `_assemble_relations` does not exist (every S5 test would AttributeError).
"""

from __future__ import annotations

import copy

import pytest

from tortoise import extractor_v2 as v2

#: A two-entity emission plus ONE relation the narrative states. Deliberately
#: minimal: the relation's endpoints are the two entities the same emission
#: produced, which is the channel's whole contract.
EMISSION = {
    "entities": [
        {"name": "Retry Storm", "kind": "core:event"},
        {"name": "Deadline Rule", "kind": "core:rule"},
    ],
    "events": [],
    "points": [],
    "operators": [],
    "relations": [
        {"src": "Retry Storm", "dst": "Deadline Rule", "predicate": "IMPL",
         "semantics": "supports"},
    ],
}


def _run(emission):
    return v2.execute_embed(copy.deepcopy(emission), {}, session_id="s-10999")


# ── 1. the contract carries the channel ──────────────────────────────────

def test_contract_advertises_the_relations_channel():
    """The channel exists in the prompt contract AND in the flag-on variant.

    A channel absent from the contract is a channel the model is never told
    about — which is the structural reason the graph holds tens of thousands
    of `aboutObject` edges and zero edges between two Objects (#10999).
    """
    assert '"relations"' in v2.OUTPUT_CONTRACT
    assert '"relations"' in v2.OUTPUT_CONTRACT_CORE_ONLY
    # the clauses the channel's validation actually enforces
    for clause in ('"src": str, "dst": str, "predicate"', "entities[]",
                   "DROPPED", "ALLOWLIST", "semantics"):
        assert clause in v2.OUTPUT_CONTRACT, clause


def test_every_verb_the_contract_advertises_actually_survives_the_gate():
    """#10999 review P1: the contract is the ONLY place the model learns the
    accepted vocabulary, so the set it names must be exactly the set the gate
    accepts.

    This is the test that was missing: cycle 1 pinned clause STRINGS, so a
    contract advertising one vocabulary while the gate enforced another — the
    cycle-2 finding, where the contract's own example verb (`weakens`) was
    dropped by the gate — passed unnoticed.
    """
    clause = v2.OUTPUT_CONTRACT.split('"predicate": "', 1)[1].split('"', 1)[0]
    advertised = [p.strip() for p in clause.split("|") if p.strip()]
    assert len(advertised) >= 13, advertised

    for predicate in advertised:
        emission = copy.deepcopy(EMISSION)
        emission["relations"] = [{"src": "Retry Storm",
                                  "dst": "Deadline Rule",
                                  "predicate": predicate}]
        result = _run(emission)
        assert [r["predicate"] for r in result["relations"]] == [predicate], (
            f"the contract advertises {predicate!r} but the gate drops it")

    # ...and the same for the advertised Layer-2 meanings, which are refused
    # INTO the record (the relation survives) rather than dropped whole.
    sem_clause = v2.OUTPUT_CONTRACT.split('"semantics": "', 1)[1].split('"', 1)[0]
    meanings = [m.strip() for m in sem_clause.split("|") if m.strip() and m != "null"]
    assert meanings, sem_clause
    for meaning in meanings:
        emission = copy.deepcopy(EMISSION)
        emission["relations"] = [{"src": "Retry Storm",
                                  "dst": "Deadline Rule",
                                  "predicate": "IMPL",
                                  "semantics": meaning}]
        result = _run(emission)
        assert result["relations"][0].get("semantics") == meaning, meaning


def test_shape_gate_covers_the_new_channel():
    """The D5 structural schema parses a relations item and REFUSES one with
    no predicate — the channel is validated, not merely advertised."""
    ok, issues = v2._validate_output_shape(
        {"relations": [{"src": "A", "dst": "B", "predicate": "IMPL"}]})
    assert ok and not issues

    ok, issues = v2._validate_output_shape(
        {"relations": [{"src": "A", "dst": "B"}]})
    assert not ok
    assert any("missing required key 'predicate'" in i for i in issues), issues


# ── 2. the story lands, between the two resolved entities ────────────────

def test_a_stated_relation_lands_between_the_two_emitted_entities():
    """The issue's own indicator: a narrative stating "A changed because of B"
    produces a relation between A and B.

    Both endpoints resolve to the entities THIS emission produced — the
    relation is not a per-item aggregate of the emission's entities, it names
    the pair the story asserted.
    """
    result = _run(EMISSION)

    assert result["relations"] == [
        {"src": "Retry Storm", "dst": "Deadline Rule",
         "predicate": "IMPL", "semantics": "supports"}
    ]
    assert result["stats"]["relations"] == 1
    assert result["stats"]["relations_dropped"] == 0
    # the channel is RESULT-level: the Layer-1 commit payload does not grow
    # for a channel no writer materialises yet (that is #11001's transport).
    assert "relations" not in result["payload"]
    # and #10000's P0 is untouched — the story's PROSE pair is not written.
    assert "story_arc" not in result["payload"] or not result["payload"][
        "story_arc"]


def test_an_absent_channel_adds_nothing():
    """Non-vacuity's control arm: with no relations the result is empty and
    no `connections_incomplete` warning is invented."""
    emission = copy.deepcopy(EMISSION)
    del emission["relations"]
    result = _run(emission)
    assert result["relations"] == []
    assert result["stats"]["relations"] == 0
    assert not [w for w in result["warnings"] if "connections_incomplete" in w]


# ── 3. zero minting: an unresolved endpoint is dropped and NAMED ─────────

def test_unresolved_endpoint_is_dropped_and_named_never_minted():
    """An endpoint naming something this emission did not produce is dropped
    with a warning that NAMES it — never materialised.

    A minted endpoint would be an unsupported assertion in the memory layer:
    the worst failure this channel could introduce, and the exact hazard the
    S3 identity issue (#10997) exists to close on the other end.
    """
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Ghost Rule",
                              "predicate": "IMPL"}]
    result = _run(emission)

    assert result["relations"] == []
    assert result["stats"]["relations_dropped"] == 1
    named = [w for w in result["warnings"] if "Ghost Rule" in w]
    assert named, result["warnings"]
    assert "NOT minted" in named[0]
    # ⛔ the mint pin: no entity beyond the emission's own two was created
    assert {e["name"] for e in result["payload"]["entities"]} == {
        "Retry Storm", "Deadline Rule"}
    # ...and the failure is REPORTED, not silent (S5 Job 1 fails open).
    assert any("connections_incomplete" in w for w in result["warnings"])


def test_undeclared_predicate_is_dropped_and_named():
    """An invented verb re-opens #2766, so it is dropped and named."""
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Deadline Rule",
                              "predicate": "vibesWith"}]
    result = _run(emission)

    assert result["relations"] == []
    assert any("vibesWith" in w and "not a declared relation verb" in w
               for w in result["warnings"]), result["warnings"]


def test_self_relation_and_malformed_item_are_dropped():
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [
        {"src": "Retry Storm", "dst": "Retry Storm", "predicate": "IMPL"},
        {"src": "Retry Storm"},          # no dst / no predicate
        "not-a-dict",
    ]
    result = _run(emission)
    assert result["relations"] == []
    assert result["stats"]["relations_dropped"] == 3


# ── 4. endpoint resolution is unique-or-nothing ──────────────────────────

def test_case_insensitive_endpoint_resolves_to_the_emitted_spelling():
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "retry storm", "dst": "DEADLINE RULE",
                              "predicate": "IMPL"}]
    result = _run(emission)
    assert [(r["src"], r["dst"]) for r in result["relations"]] == [
        ("Retry Storm", "Deadline Rule")]


def test_the_composed_stage_agrees_with_the_resolver_about_ambiguity():
    """#10999 review cycle 3 P2: `_apply_entity_resolution` and
    `_resolve_relation_endpoint` must answer the SAME ambiguity question —
    uniquely-one EMITTED entity, not uniquely-one resolved name.

    Measured before this fix: with two entities differing only in case and a
    resolution naming one of them, the D3 stage renamed the endpoint onto the
    resolved node and S5 kept it with `relations_dropped == 0` and NO warning —
    the edge attributed to the wrong node, silently. The resolver alone refuses
    this case (`test_ambiguous_case_fold_resolves_to_nothing`); composing the
    two stages must not launder it.
    """
    emission = copy.deepcopy(EMISSION)
    emission["entities"] = [
        {"name": "Deadline Rule", "kind": "core:rule"},
        {"name": "DEADLINE RULE", "kind": "core:rule"},
        {"name": "Retry Storm", "kind": "core:event"},
    ]
    emission["relations"] = [{"src": "Retry Storm", "dst": "DEADLINE RULE",
                              "predicate": "IMPL"}]
    resolved = v2._apply_entity_resolution(
        emission, {"Deadline Rule": {"name": "Deadline Rule (2024)"}})
    # ⛔ The endpoint must NOT be attributed to the node the resolution map
    # chose — that is the wrong node, and it is what measuring the fold over
    # `name_map` produced. After the rename exactly ONE emitted entity is still
    # spelled "deadline rule", so the surviving spelling is the right answer.
    assert resolved["relations"][0]["dst"] != "Deadline Rule (2024)"
    assert resolved["relations"][0]["dst"] == "DEADLINE RULE"
    # ...and the two stages agree: the relation is kept, against that entity,
    # with no false "does not resolve" warning.
    result = _run(resolved)
    assert result["stats"]["relations_dropped"] == 0
    assert result["relations"][0]["dst"] == "DEADLINE RULE"


def test_a_discarded_entity_named_only_by_a_relation_is_kept():
    """#10999 review cycle 3 P2: the vet_gate referent fix needs a
    DISCRIMINATING case — deleting the new loop must red something.

    `apply_vet` downgrades a DISCARD to KEEP only for a REFERENCED entity, so
    an entity whose sole tie to the emission is a relation endpoint was
    discarded and S5 then dropped the relation as unresolvable.
    """
    from tortoise import vet_gate

    emission = copy.deepcopy(EMISSION)
    target = emission["entities"][1]           # "Deadline Rule"
    item_id = vet_gate._item_id("entities", 1, target)
    kept, warnings = vet_gate.apply_vet(
        emission, {item_id: {"outcome": "DISCARD"}})
    assert any(e.get("name") == "Deadline Rule" for e in kept["entities"])
    assert any("referenced by a surviving candidate" in w for w in warnings)
    # the relation survives the VET pass intact
    assert kept["relations"] == emission["relations"]

    # CONTROL: without the relation nothing references it, so it IS removed —
    # i.e. the case above measures the new loop, not a general refusal to drop.
    control = copy.deepcopy(EMISSION)
    del control["relations"]
    kept_c, _ = vet_gate.apply_vet(
        control, {vet_gate._item_id("entities", 1, control["entities"][1]):
                  {"outcome": "DISCARD"}})
    assert not any(e.get("name") == "Deadline Rule"
                   for e in kept_c["entities"])


def test_ambiguous_case_fold_resolves_to_nothing():
    """Two entities differing only in case: guessing which one the story
    meant would attribute the edge to the WRONG node."""
    emission = copy.deepcopy(EMISSION)
    emission["entities"] = [
        {"name": "Deadline Rule", "kind": "core:rule"},
        {"name": "deadline rule", "kind": "core:rule"},
        {"name": "Retry Storm", "kind": "core:event"},
    ]
    emission["relations"] = [{"src": "Retry Storm", "dst": "DEADLINE RULE",
                              "predicate": "IMPL"}]
    result = _run(emission)
    assert result["relations"] == []
    assert result["stats"]["relations_dropped"] == 1


# ── 5. idempotence: finalising the same batch twice is ONE story ─────────

def test_finalising_the_same_batch_twice_does_not_duplicate_the_story():
    """The duplicate the issue is really about: the same narrative emitted
    twice (an S2/S4 union, a retry, a re-finalise) is ONE relation."""
    first = _run(EMISSION)
    second = _run(EMISSION)
    assert first["relations"] == second["relations"]
    assert len(first["relations"]) == 1
    # the commit id is stable across the two finalisations (nothing in the
    # channel leaks into the payload's canonical form)
    assert (first["payload"]["client_commit_id"]
            == second["payload"]["client_commit_id"])


def test_intra_emission_duplicate_triples_collapse_first_wins():
    """The SAME triple stated twice is ONE relation, and the collapse is a
    silent SKIP, not a gate drop — `relations_dropped` must stay 0.

    The duplicate's predicate carries the SAME case on purpose: a case
    variant (`impl`) would be refused by the case-SENSITIVE declaredness
    check first, so the test would pass with the dedupe deleted.
    """
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [
        {"src": "Retry Storm", "dst": "Deadline Rule", "predicate": "IMPL",
         "semantics": "supports"},
        {"src": "retry storm", "dst": "deadline rule", "predicate": "IMPL"},
    ]
    result = _run(emission)
    assert len(result["relations"]) == 1
    assert result["relations"][0]["semantics"] == "supports"
    assert result["stats"]["relations_dropped"] == 0


def test_a_mistyped_section_is_counted_as_dropped():
    """A mapping instead of a list loses EVERY relation, so it must reach the
    `connections_incomplete` report — not only a shape warning."""
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = {"src": "Retry Storm"}
    result = _run(emission)
    assert result["relations"] == []
    assert result["stats"]["relations_dropped"] == 1
    assert any("connections_incomplete" in w for w in result["warnings"])


def test_a_relation_survives_entity_resolution():
    """#10999 review P1: the D3 stage renames entities, so the channel's
    endpoints must be renamed with them.

    Resolution fires exactly when S3 found candidates — i.e. for entities the
    graph ALREADY knows, the common real case. Left unthreaded, the relation
    would name the pre-resolution spelling and S5 would drop it as
    unresolvable.
    """
    resolved = v2._apply_entity_resolution(
        EMISSION, {"Retry Storm": {"name": "Retry Storm (2024-05)"}})
    assert resolved["relations"][0]["src"] == "Retry Storm (2024-05)"

    result = _run(resolved)
    assert [(r["src"], r["dst"]) for r in result["relations"]] == [
        ("Retry Storm (2024-05)", "Deadline Rule")]
    assert result["stats"]["relations_dropped"] == 0


def test_a_case_variant_endpoint_survives_entity_resolution():
    """#10999 review cycle 2 P2: the rename mirrors the resolver — exact first,
    then a UNIQUE case-insensitive hit.

    An exact-match-only rename left a case-variant endpoint behind, and the
    moment resolution fired S5 reported a FALSE "does not resolve to an entity
    this emission produced" for an entity it had produced.
    """
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "retry storm", "dst": "DEADLINE RULE",
                              "predicate": "IMPL"}]
    resolved = v2._apply_entity_resolution(
        emission, {"Retry Storm": {"name": "Retry Storm (2024-05)"}})
    assert resolved["relations"][0]["src"] == "Retry Storm (2024-05)"

    result = _run(resolved)
    assert result["stats"]["relations_dropped"] == 0
    assert len(result["relations"]) == 1


def test_out_of_vocabulary_semantics_is_dropped_from_the_record():
    """#10999 review cycle 2 P2: `semantics` is a CLOSED vocabulary, and it is
    the one field that would otherwise carry free text into an edge property.
    The RELATION survives — only the meaning is refused."""
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Deadline Rule",
                              "predicate": "IMPL",
                              "semantics": "x" * 300}]
    result = _run(emission)
    assert len(result["relations"]) == 1
    assert "semantics" not in result["relations"][0]
    assert any("closed Layer-2 vocabulary" in w for w in result["warnings"])


def test_a_retired_semantics_spelling_is_canonicalised():
    """The retired word is recorded as its canonical replacement (#7852), not
    silently kept and not thrown away."""
    from tortoise.pack_registry import SEMANTICS_TRANSITION

    retired, canonical = next(iter(SEMANTICS_TRANSITION.items()))
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Deadline Rule",
                              "predicate": "IMPL", "semantics": retired}]
    result = _run(emission)
    assert result["relations"][0]["semantics"] == canonical
    assert any("retired" in w for w in result["warnings"])


def test_extract_session_v2_always_returns_the_channel():
    """#10999 review cycle 2 P2: the empty-conversation return is a second
    hand-enumerated result dict — a consumer must never have to distinguish
    "no relations" from "the key is missing"."""
    result = v2.extract_session_v2(None, [])
    assert result["relations"] == []


# ── 6. the S2 ∪ S4 union dedupes on the triple ───────────────────────────

def test_merge_embed_lists_unions_relations_by_triple():
    s2 = {"relations": [{"src": "A", "dst": "B", "predicate": "IMPL"}]}
    s4 = {"relations": [
        {"src": "A", "dst": "B", "predicate": "IMPL", "semantics": "supports"},
        {"src": "B", "dst": "A", "predicate": "NAND"},
    ]}
    merged = v2.merge_embed_lists(s2, s4)
    assert len(merged["relations"]) == 2
    # S4's graph-informed version wins on the collision (E4 semantics)
    assert merged["relations"][0]["semantics"] == "supports"


# ── 7. non-vacuity by MUTATION ───────────────────────────────────────────

def test_mutation_the_mint_pin_is_live(monkeypatch):
    """Mutation: make the endpoint resolver accept ANY name (the resolver
    removed) and the zero-mint test above must go red.

    This is what stops the mint pin being a tautology — if the resolution step
    were deleted, the endpoint would be carried verbatim and no test would
    notice.
    """
    monkeypatch.setattr(v2, "_resolve_relation_endpoint",
                        lambda name, exact, folded: name)
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Ghost Rule",
                              "predicate": "IMPL"}]
    result = _run(emission)
    # the mutated resolver CARRIES the unresolved endpoint — i.e. the assertion
    # in `test_unresolved_endpoint_is_dropped_and_named_never_minted` is
    # measuring the resolution, not an empty list.
    assert result["relations"] == [
        {"src": "Retry Storm", "dst": "Ghost Rule", "predicate": "IMPL"}]
    assert result["stats"]["relations_dropped"] == 0


def test_mutation_the_predicate_gate_is_live(monkeypatch):
    """Mutation: widen the declared vocabulary and the undeclared-predicate
    drop disappears — the gate is measuring the vocabulary."""
    monkeypatch.setattr(v2, "_declared_relation_predicates",
                        lambda: frozenset({"vibesWith"}))
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Deadline Rule",
                              "predicate": "vibesWith"}]
    result = _run(emission)
    assert [r["predicate"] for r in result["relations"]] == ["vibesWith"]


def test_mutation_the_union_arm_is_live():
    """Mutation: drop `relations` from the union's section list and the union
    test loses its second relation."""
    s2 = {"relations": [{"src": "A", "dst": "B", "predicate": "IMPL"}]}
    merged = v2.merge_embed_lists(s2, {})
    assert "relations" in merged, (
        "the S2∪S4 union must carry the channel, or S4's gap additions and "
        "S2's own relations are silently discarded")


@pytest.mark.parametrize("predicate", ["IMPL", "NAND", "supports", "causes",
                                       "hasPart", "dependsOn"])
def test_core_predicates_are_accepted(predicate):
    """The core vocabulary is accepted end-to-end (a negative-only gate would
    pass every test above while rejecting every real relation)."""
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Deadline Rule",
                              "predicate": predicate}]
    result = _run(emission)
    assert [r["predicate"] for r in result["relations"]] == [predicate]


# ── 9. a MISTYPED relations section contributes nothing, never an exception ─
#
# `relations` is MODEL-emitted, so a malformed value is reachable — the same
# reason `_assemble_relations` treats it as a drop rather than a no-op. Every
# stage that touches it before S5 must therefore be total on a bad shape: an
# exception does not stay local, it escapes into a per-stage `except` and
# abandons that ENTIRE stage (the whole S2.2 vet pass, or the whole D3 rename)
# while reporting only one warning.

def test_a_mistyped_relations_section_never_aborts_the_vet_pass():
    """`apply_vet` is documented as total on a malformed section shape, and
    `_referenced_entity_names` on "a shape we do not recognise contributes
    nothing" — both false while the referent scan iterated the raw value.

    Measured pre-fix: `apply_vet(…, {"relations": 5}, <one DISCARD>)` raised
    `TypeError: 'int' object is not iterable`, so a direct caller saw an
    exception and `_run_vet_pass` silently abandoned EVERY verdict in the
    batch (all candidates kept, no discards, one `vet failed` warning).
    """
    from tortoise import vet_gate

    emission = {"entities": [{"name": "Retry Storm", "kind": "core:event"},
                            {"name": "Deadline Rule", "kind": "core:rule"}],
                "events": [], "points": [], "operators": [],
                "relations": 5}
    item_id = vet_gate._item_id("entities", 1, emission["entities"][1])
    kept, _warnings = vet_gate.apply_vet(
        emission, {item_id: {"outcome": "DISCARD"}})

    # The verdict is APPLIED — not merely "no exception raised": a silently
    # abandoned pass would keep both entities and still return normally.
    assert [e.get("name") for e in kept["entities"]] == ["Retry Storm"]
    # …and the malformed section contributes no name to the referent set.
    assert vet_gate._referenced_entity_names(emission, set()) == set()
    # …and a non-Mapping embed list stays total (it returns an empty set, as
    # the pre-#10999 code did, instead of raising AttributeError).
    for bad in (None, 5, [], "x"):
        assert vet_gate._referenced_entity_names(bad, set()) == set(), bad


def test_a_mistyped_relations_section_never_aborts_the_rename_stage():
    """The D3 rename reads `relations` too, and an exception there costs the
    WHOLE rename for the session — i.e. precisely the canonicalisation that
    makes a relation resolvable at S5.

    Measured pre-fix with a non-empty resolution map: `TypeError: 'int' object
    is not iterable`, raised out of `_apply_entity_resolution`.
    """
    resolved = v2._apply_entity_resolution(
        {"entities": [{"name": "Ghost", "kind": "core:rule"},
                      {"name": "Keep", "kind": "core:rule"}],
         "relations": 5},
        {"Ghost": {"name": "Renamed"}})

    assert [e["name"] for e in resolved["entities"]] == ["Renamed", "Keep"]
    assert resolved["relations"] == 5       # untouched, not "fixed up"
