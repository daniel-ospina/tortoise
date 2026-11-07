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
import sys

import pytest

from tortoise import extractor_v2 as v2
from tortoise import vet_gate as vg

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


def test_the_shape_gate_defers_the_relations_section_to_s5():
    """The D5 gate's ONE exception: `relations` shape (section- AND item-level)
    is S5's, which drops and COUNTS every mistype.

    A gate verdict here would make the recovery ladder accept a SHORTER prefix
    and lose every section emitted after `relations`; rung 1 never runs this
    gate at all, so it never protected the clean-parse path either. The
    structural gate stays live for every other section — asserted below so the
    exception cannot silently become a no-op gate.
    """
    for relations in ({"src": "A", "dst": "B", "predicate": "IMPL"},
                      [{"src": "A", "dst": "B"}],
                      [{"src": "A", "dst": None, "predicate": "IMPL"}],
                      [42]):
        ok, issues = v2._validate_output_shape({"relations": relations})
        assert ok and not issues, (relations, issues)

    # ...and the gate is ALIVE for a non-deferred section
    ok, issues = v2._validate_output_shape({"entities": [{"name": "A"}]})
    assert not ok
    assert any("'kind'" in i for i in issues), issues


def test_s5_owns_every_relations_mistype_the_gate_defers():
    """The deferral is only sound because S5 ACCOUNTS for every one of those
    shapes (the reporting discipline rule 1 rests on) — no mistype is silent.

    ⛔ #11333: "accounts for" is not "drops". The first shape is the realistic
    mistype (the triple where a list was asked for), and S5 now RECOVERS it,
    which is still not silent — the shape error is named as recovered. Each
    row's expectation is therefore `(kept, dropped, warning-fragment)` rather
    than the single `dropped >= 1` the dropped-everything rule produced.
    """
    cases = [
        ({"src": "Retry Storm", "dst": "Deadline Rule", "predicate": "IMPL"},
         1, 0, "recovered"),
        ([{"src": "Retry Storm", "dst": "Deadline Rule"}], 0, 1, "dropped"),
        ([{"src": "Retry Storm", "dst": None, "predicate": "IMPL"}],
         0, 1, "dropped"),
        ([42], 0, 1, "non-dict relation entry"),
    ]
    for relations, kept, dropped, named in cases:
        emission = copy.deepcopy(EMISSION)
        emission["relations"] = relations
        result = _run(emission)
        assert len(result["relations"]) == kept, (relations, result["relations"])
        assert result["stats"]["relations_dropped"] == dropped, relations
        assert any(named in w for w in result["warnings"]), \
            (relations, named, result["warnings"])


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
    # for a channel no writer consumes yet (the write path is #11237).
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
    # the reason names the ACTUAL tie: a relation endpoint is result-level, so
    # it is not "Layer-1 referential integrity" (that claim is false here and
    # the earlier wording made it — #11205 review cycle 3)
    assert any("named by a surviving entity↔entity relation" in w
               for w in warnings), warnings
    assert not any("Layer-1 referential integrity" in w for w in warnings)
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


def test_a_ghost_relation_endpoint_never_prescribes_a_mint():
    """#10999 review cycle 4 P2: a relation endpoint naming an entity no pass
    produced is DROPPED by `_assemble_relations` (never minted), so the VET
    referent scan must not report it as a missing referent — that warning says
    "the payload will fail Layer-1 unless the extractor emits it", which is
    both false (relations never enter the payload) and a prescription to mint.
    """
    from tortoise import vet_gate

    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Ghost Rule",
                              "predicate": "IMPL"}]
    item_id = vet_gate._item_id("entities", 1, emission["entities"][1])
    kept, warnings = vet_gate.apply_vet(
        emission, {item_id: {"outcome": "DISCARD"}})
    assert not any("Ghost Rule" in w or "ghost rule" in w for w in warnings), (
        warnings)
    assert not any("Layer-1" in w for w in warnings), warnings
    # the ghost shields nothing: the DISCARDed entity is still removed
    assert not any(e.get("name") == "Deadline Rule" for e in kept["entities"])
    # ...and S5 independently drops the ghost relation (it is not an endpoint
    # of anything the emission produced).
    assert _run(emission)["stats"]["relations_dropped"] == 1
def test_whitespace_variant_endpoint_resolves_to_the_emitted_spelling():
    """The endpoint's loose arm is the pipeline's OWN name identity, ``_norm``
    (whitespace-collapsed + lower) — not case-folding alone.

    ``_norm`` is what decides name identity everywhere else in this pipeline:
    ``_merge_key`` builds the relation's dedupe key with it, and
    ``vet_gate._norm`` matches entity references with it. So an endpoint that
    differs from the emitted name only by a whitespace RUN is the same entity
    to every other stage, and it is the one spelling variant a casefold-only
    arm drops as "does not resolve" — an edge lost to a space.
    """
    emission = copy.deepcopy(EMISSION)
    emission["entities"][0]["name"] = "Retry  Storm"      # inner double space
    emission["relations"] = [{"src": "Retry Storm", "dst": "Deadline Rule",
                              "predicate": "IMPL"}]
    result = _run(emission)
    # the SPACE of the emitted name wins — resolution canonicalises the endpoint
    assert [(r["src"], r["dst"]) for r in result["relations"]] == [
        ("Retry  Storm", "Deadline Rule")]
    assert result["stats"]["relations_dropped"] == 0


def test_self_relation_across_spellings_is_dropped_as_a_self_relation():
    """A self-edge is a self-edge whatever its two spellings: the check uses
    the same ``_norm`` identity, so it is DIAGNOSED as a self-relation rather
    than misreported as an unresolvable endpoint."""
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "retry  storm",
                              "predicate": "IMPL"}]
    result = _run(emission)
    assert result["relations"] == []
    assert any("self-relation" in w for w in result["warnings"]), \
        result["warnings"]


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
    """Re-finalising the same batch is ONE relation — and the COLLAPSE, not the
    fixture, is what keeps it at one.

    ⛔ The original form of this row ran `_run(EMISSION)` twice and compared the
    two results. `execute_embed` is deterministic over a deep-copied input, so
    that assertion held with the dedupe deleted; and `EMISSION` carries exactly
    ONE relation, so `len == 1` held too. It asserted a property it could not
    measure — a reviewer found it green under mutation. It now feeds the
    duplicate a retry actually produces and reds if the collapse is removed.
    """
    twice = copy.deepcopy(EMISSION)
    twice["relations"] = EMISSION["relations"] + [dict(EMISSION["relations"][0])]
    first = _run(twice)
    second = _run(twice)
    assert len(first["relations"]) == 1, first["relations"]
    assert first["stats"]["relations_dropped"] == 0, (
        "a repeated triple is a silent SKIP, not a gate drop")
    assert second["relations"] == first["relations"]


def test_the_channel_never_reaches_the_payload_nor_its_canonical_id():
    """#10000 (P0) — the payload boundary, pinned so the pin can FAIL.

    The assertion this replaces compared the `client_commit_id` of two runs of
    `_run(EMISSION)` — the same deterministic function over the same
    deep-copied input — so no mutation of this channel could ever change it,
    including the one it was cited for (agent-infra#3442 rule 3: an assertion
    whose value cannot vary under any mutation of the diff). The PR body cited
    it as a second pin on the P0 while `assert "relations" not in result[
    "payload"]` was doing all the work.

    This pin varies the INPUT instead of re-running the same code: the same
    batch WITH the channel and WITHOUT it. `compute_client_commit_id` hashes an
    explicit argument list, so the two ids agree only while the channel stays
    out of the canonical payload — add `relations` to the payload dict AND to
    that argument list and the ids diverge HERE, which is the mutation the dead
    version left green.
    """
    with_channel = _run(EMISSION)
    without_channel_emission = copy.deepcopy(EMISSION)
    without_channel_emission["relations"] = []
    without_channel = _run(without_channel_emission)

    # NON-VACUITY: the two runs differ in the channel and in nothing else, so
    # an id that agrees here agrees because the channel is excluded — not
    # because the two inputs were the same object.
    assert with_channel["relations"], "the channel must be non-empty here"
    assert without_channel["relations"] == []

    assert "relations" not in with_channel["payload"]
    assert "relations" not in without_channel["payload"]
    assert (with_channel["payload"]["client_commit_id"]
            == without_channel["payload"]["client_commit_id"]), (
        "the relation channel reached the commit payload's canonical form — "
        "#10000 (P0): the story's TEXT stays out of the graph, and a typed "
        "relation is RESULT-level (D8), never part of the client_commit_id")


def test_intra_emission_duplicate_triples_collapse_first_wins():
    """The SAME triple stated twice is ONE relation, and the collapse is a
    silent SKIP, not a gate drop — `relations_dropped` must stay 0.

    The duplicate's predicate carries the SAME case on purpose: the duplicate is
    same-case so that the COLLAPSE, not the vocabulary, is the only thing that
    can merge it. (The earlier wording claimed a case variant `impl` "would be
    refused by the case-SENSITIVE declaredness check" — that stopped being true
    when the gate was made fold-tolerant, and the case-variant accepted-and-
    canonicalised behaviour is pinned separately below.)
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
    # TWO entries, so the assertion can distinguish `len(raw)` from a
    # hardcoded 1 — a one-key fixture leaves `n = 1` mutation-green.
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = {"a": {"src": "Retry Storm"},
                             "b": {"src": "Deadline Rule"}}
    result = _run(emission)
    assert result["relations"] == []
    assert result["stats"]["relations_dropped"] == 2
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


def test_a_whitespace_variant_endpoint_survives_a_firing_resolution():
    """#10999 review cycle 5 P2: the merge keyed the rename on BOTH folds
    (`_endpoint_fold_keys`: `_norm` collapses a whitespace RUN, `.casefold()`
    folds ß/İ — neither is a superset of the other). That hunk was load-bearing
    and unpinned: a casefold-only key left a whitespace-variant endpoint behind
    while resolution fired, and S5 dropped the relation.

    Measured: emission entity "Retry  Storm" (two spaces), endpoint
    "Retry Storm", map renames it — HEAD carries the edge, the casefold-only
    key drops it.
    """
    emission = copy.deepcopy(EMISSION)
    emission["entities"] = [
        {"name": "Retry  Storm", "kind": "core:event"},
        {"name": "Deadline Rule", "kind": "core:rule"},
    ]
    emission["relations"] = [{"src": "Retry Storm", "dst": "Deadline Rule",
                              "predicate": "IMPL"}]
    resolved = v2._apply_entity_resolution(
        emission, {"Retry  Storm": {"name": "Retry Storm (2024)"}})
    assert resolved["relations"][0]["src"] == "Retry Storm (2024)"
    result = _run(resolved)
    assert result["stats"]["relations_dropped"] == 0
    assert result["relations"][0]["src"] == "Retry Storm (2024)"


def test_both_endpoint_stages_resolve_through_ONE_shared_rule(monkeypatch):
    """#10999 review cycle 5 P2 (the stated residual, now closed): endpoint
    identity had TWO homes — D3's nested `_fold_hits`/`_canonical_endpoint`
    and S5's `_resolve_relation_endpoint` — so an edit to either ambiguity arm
    could leave the other answering a different question, which is the
    wrong-node attribution cycles 3 and 5 both found.

    Discriminating by CONSTRUCTION rather than by comparing two copies:
    disabling the shared `_endpoint_fold_hits` must disable fold resolution in
    BOTH stages. A private copy re-introduced into either stage survives the
    patch, and that half then still carries the edge — so
    `relations_dropped == 1` reds the moment the rule grows a second home.
    """
    emission = copy.deepcopy(EMISSION)
    emission["entities"] = [
        {"name": "Retry  Storm", "kind": "core:event"},
        {"name": "Deadline Rule", "kind": "core:rule"},
    ]
    # The resolution renames the OTHER entity, so D3 runs (the rename stage is
    # a no-op on an empty map) while the src endpoint is answerable ONLY by the
    # fold over the emitted names — never by the rename map. Without that
    # split, D3's rename-map arm would carry the src on its own and the patch
    # below could not tell whether the STAGES read the shared rule.
    resolution = {"Deadline Rule": {"name": "Deadline Rule (2024)"}}
    emission["relations"] = [{"src": "retry storm", "dst": "Deadline Rule",
                              "predicate": "IMPL"}]

    resolved = v2._apply_entity_resolution(copy.deepcopy(emission), resolution)
    assert resolved["relations"][0]["src"] == "Retry  Storm"
    assert _run(resolved)["stats"]["relations_dropped"] == 0

    monkeypatch.setattr(v2, "_endpoint_fold_hits", lambda ref, folded: [])
    # D3 no longer folds, so it leaves the spelling alone...
    crippled = v2._apply_entity_resolution(copy.deepcopy(emission), resolution)
    assert crippled["relations"][0]["src"] == "retry storm"
    # ...and S5, which must be reading the SAME rule, no longer resolves it.
    result = _run(crippled)
    assert result["relations"] == []
    assert result["stats"]["relations_dropped"] == 1


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


def test_an_unreadable_vocabulary_fails_OPEN_and_names_the_relation(monkeypatch):
    """The ONE route by which an invented predicate reaches the channel.

    `_declared_relation_predicates()` returns `None` when the pack registry
    cannot be read, and `_assemble_relations` then KEEPS the relation with a
    warning — #10999 §4.4's declared fail-open ("Job 1 fails **open** … a
    quality problem in the reading, re-derivable from the same narrative"), not
    a fail-closed gate. Nothing in the suite reached this arm: the only other
    `_declared_relation_predicates` monkeypatch widens the set, so deleting the
    `else:` arm, flipping it to a drop, or keeping SILENTLY all left the suite
    green — i.e. the entire residual risk of #2766 was unpinned prose.

    Pins the direction AND the naming, so a later "tightening" to fail closed
    reds here instead of silently destroying relations it could not judge.
    """
    monkeypatch.setattr(v2, "_declared_relation_predicates", lambda: None)
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Deadline Rule",
                              "predicate": "vibesWith"}]
    result = _run(emission)

    assert len(result["relations"]) == 1, result["warnings"]
    assert result["relations"][0]["predicate"] == "vibesWith"
    assert result["stats"]["relations_dropped"] == 0, result["warnings"]
    assert any("vibesWith" in w and "unverified" in w
               for w in result["warnings"]), (
        "a kept-but-uncheckable predicate must be NAMED as unverified, or the "
        "fail-open is indistinguishable from a passing gate: "
        f"{result['warnings']}")


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


# ── 8. a MISTYPED relations section contributes nothing, never an exception ─
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
# ── 9. the S2.2 VET guard reads the same emission, in the same fold ───────
#
# The channel's endpoints are the entity's ONLY tie to the emission whenever
# the narrative is the sole place the entity is mentioned, so the VET guard
# must count them or it DISCARDs the entity and S5 then drops the relation —
# the same gap one stage earlier. Both arms below are load-bearing: the ASCII
# arm alone passes while a `.lower()`-vs-`.casefold()` spelling variant is
# still missed, because the two normalisers live on opposite sides of these
# two modules (see `vet_gate._norm` and `test_norm_matches_extractor_norm`).


def _discard_entity(emission, index):
    """A verdict map DISCARDing `entities[index]` — the shape `apply_vet`
    expects, keyed exactly as `vet_candidates` mints it."""
    return {vg._item_id("entities", index, emission["entities"][index]):
            {"outcome": "DISCARD"}}


def test_the_vet_guard_protects_an_entity_named_only_by_a_relation_endpoint():
    """The P2 this pins: an entity whose ONLY tie to the emission is a
    relation endpoint is REFERENCED, so a DISCARD is downgraded to KEEP.

    Nothing else names "Retry Storm" here — drop the guard arm and the entity
    goes, taking the relation with it.
    """
    emission = {
        "entities": [{"name": "Retry Storm", "kind": "core:event"},
                     {"name": "Deadline Rule", "kind": "core:rule"}],
        "relations": [{"src": "Retry Storm", "dst": "Deadline Rule",
                       "predicate": "IMPL"}],
    }
    out, warns = vg.apply_vet(emission, _discard_entity(emission, 0),
                              prior=None)
    assert [e["name"] for e in out["entities"]] == ["Retry Storm",
                                                    "Deadline Rule"]
    assert any("named by a surviving entity↔entity relation" in w
               for w in warns), warns
    # ...and the claim is a TRUE one: a relation endpoint is RESULT-level, so
    # it is not what keeps the entity inside the Layer-1 payload.
    assert not any("Layer-1 referential integrity" in w for w in warns), warns
    # ...and the relation still lands, so the guard's protection is not
    # academic — the edge survives the whole S2.2 → S5 path.
    assert _run(out)["relations"] == [
        {"src": "Retry Storm", "dst": "Deadline Rule", "predicate": "IMPL"}]


def test_the_vet_guard_protects_a_casefold_only_endpoint():
    """S5 resolves an endpoint by ``.casefold()``; this guard normalises by
    ``.lower()``. A spelling variant the two folds disagree on ("STRASSE" for
    "Straße") is resolvable to S5 and was still MISSED here, so the entity was
    discarded and the relation dropped — the arm half-closed.
    """
    emission = {
        "entities": [{"name": "Straße", "kind": "core:object"},
                     {"name": "Deadline Rule", "kind": "core:rule"}],
        "relations": [{"src": "STRASSE", "dst": "Deadline Rule",
                       "predicate": "IMPL"}],
    }
    # the premise: S5 DOES resolve this spelling, so the guard must not miss it
    assert v2._resolve_relation_endpoint(
        "STRASSE", {"Straße", "Deadline Rule"},
        {"strasse": ["Straße"], "deadline rule": ["Deadline Rule"]},
    ) == "Straße"
    out, _warns = vg.apply_vet(emission, _discard_entity(emission, 0),
                               prior=None)
    assert [e["name"] for e in out["entities"]] == ["Straße", "Deadline Rule"]
    assert [r["src"] for r in _run(out)["relations"]] == ["Straße"]


def test_a_dangling_endpoint_does_not_claim_the_payload_will_fail_layer1():
    """The channel is RESULT-level, so an endpoint naming an entity no pass
    emitted leaves the relation unresolved (dropped and named) and never
    reaches the Layer-1 payload — it CANNOT 422.

    The Rule-4 restore leg is about references that would 422, so counting
    endpoints there asserted the opposite on the routine case.
    """
    emission = {
        "entities": [{"name": "Retry Storm", "kind": "core:event"},
                     {"name": "Noise Entity", "kind": "core:object"}],
        "points": [{"content": "the retry storm tripped",
                    "pointKind": "core:fact",
                    "about_entities": ["Retry Storm"]}],
        "relations": [{"src": "Retry Storm", "dst": "Ghost Rule",
                       "predicate": "IMPL"}],
    }
    out, warns = vg.apply_vet(emission, _discard_entity(emission, 1),
                              prior=None)
    assert any("discarded entities 'Noise Entity'" in w for w in warns), warns
    assert not [w for w in warns if "Layer-1" in w], warns
    # the dangling endpoint is still REPORTED — by S5, where the drop happens
    assert any("Ghost Rule" in w for w in _run(out)["warnings"])

# ── 9. fresh-context review of the merged head (the third cycle) ──────────
#
# Three defects the earlier cycles left, each measured before it was fixed.

def test_a_case_variant_predicate_survives_the_union_and_is_canonicalised():
    """The S2∪S4 union's identity folds the predicate, so S4's case variant is
    ONE item that "wins" the collision; the declaredness gate was case-SENSITIVE,
    so the survivor was dropped as an invented verb the model never invented.

    Measured before the fix at `3edd17702c`: merged `predicate == "impl"` →
    `relations=[]`, `relations_dropped=1`, warning "predicate 'impl' is not a
    declared relation verb". An edge lost to a capital letter.
    """
    s2 = {"relations": [{"src": "A", "dst": "B", "predicate": "IMPL"}]}
    s4 = {"relations": [{"src": "A", "dst": "B", "predicate": "impl"}]}
    merged = v2.merge_embed_lists(s2, s4)
    result = _run({"entities": [{"name": "A", "kind": "core:object"},
                                {"name": "B", "kind": "core:object"}],
                   "relations": merged["relations"]})
    assert [r["predicate"] for r in result["relations"]] == ["IMPL"], result
    assert result["stats"]["relations_dropped"] == 0


def test_a_declared_verb_is_recorded_in_its_declared_spelling():
    """A fold variant is the same verb — and the record carries the DECLARED
    spelling, so two spellings of one verb cannot become two records later."""
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Deadline Rule",
                              "predicate": "impl"}]
    result = _run(emission)
    assert [r["predicate"] for r in result["relations"]] == ["IMPL"]


def test_a_case_variant_semantics_resolves_to_the_closed_spelling():
    """`semantics` is the same closed vocabulary and folds the same way: a case
    variant is the declared meaning, not an out-of-vocabulary token."""
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Deadline Rule",
                              "predicate": "IMPL", "semantics": "Supports"}]
    result = _run(emission)
    assert result["relations"][0]["semantics"] == "supports"
    assert not [w for w in result["warnings"] if "semantics" in w], result


def test_a_non_string_predicate_is_a_SHAPE_error_at_s5_not_a_vocabulary_miss():
    """The gate defers the section (see above), so S5 must carry the typing —
    and it names the defect as a SHAPE error, never as an invented verb."""
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Deadline Rule",
                              "predicate": 42}]
    result = _run(emission)
    assert result["relations"] == []
    assert result["stats"]["relations_dropped"] == 1
    rel_warns = [w for w in result["warnings"] if "relations[0]" in w]
    assert rel_warns and "not a string" in rel_warns[0], rel_warns


def test_the_declared_vocabulary_has_one_home():
    """#2766's defect is a vocabulary derived in several places and drifting.

    `pack_registry.declared_relation_predicates` is the ONE home; the write path
    (`sdk.create_operator`) and S5's gate both read it. THIS test is the OFFLINE
    half — it pins S5's gate to the same call, and pins the helper's answer to
    the sets the write path used to derive for itself. The write path's own
    half is BEHAVIOURAL and lives where `create_operator` can actually be
    driven: `test_operator_label_vocabulary.TestWritePathDeclaredness.
    test_the_write_path_reads_the_ONE_declaration_home`.
    """
    from tortoise.domain_loader import _get_registry
    from tortoise.pack_registry import (
        CORE_OPERATOR_LABELS,
        declared_relation_predicates,
    )

    declared = declared_relation_predicates()
    assert declared is not None
    # the write path's set, recomputed from its documented inputs
    reg = _get_registry()
    write_path = set(CORE_OPERATOR_LABELS)
    if reg is not None:
        write_path |= {r.get("predicate") for r in reg.list_relations()
                       if isinstance(r, dict) and r.get("predicate")}
    assert set(declared) == write_path
    # and S5's gate answers from the same call
    assert v2._declared_relation_predicates() == declared


# ── 10. the cross-pass leg (P1) and the mistype count (P2) ───────────────
#
# Both were raised by an earlier review at `3edd17702c` and were still
# present at `c0d016747`; the fixes here are red-first.

def test_a_relation_endpoint_restores_an_entity_an_earlier_pass_removed():
    """Rule 4 is the ONLY leg that can bring back an entity an EARLIER pass
    removed — and S4 arrives between the two passes, so the narrative's
    conclusion (`relations`) is exactly the cross-pass reference Rule 4 exists
    for.

    The `relation_endpoints` flag was applied to the whole `to_restore`
    computation instead of to the WARNING, so a relation-only endpoint restored
    nothing: the entity stayed gone and S5 dropped the edge as unresolvable,
    with no warning saying why.
    """
    emission = {
        "entities": [{"name": "Retry Storm", "kind": "core:event"}],
        "relations": [{"src": "Retry Storm", "dst": "Deadline Rule",
                       "predicate": "IMPL"}],
    }
    prior = {"removed_entities": {
        "deadline rule": {"name": "Deadline Rule", "kind": "core:rule"}}}
    out, warns = vg.apply_vet(emission, {}, prior=prior)
    assert [e["name"] for e in out["entities"]] == ["Retry Storm",
                                                    "Deadline Rule"]
    assert any("restored" in w for w in warns), warns
    # ...and the restore is not claimed as Layer-1 integrity: the channel is
    # result-level, so this reference cannot 422.
    assert not any("Layer-1 referential integrity" in w for w in warns), warns
    assert [(r["src"], r["dst"]) for r in _run(out)["relations"]] == [
        ("Retry Storm", "Deadline Rule")]


def test_a_single_relation_object_where_a_list_was_asked_for_counts_as_one():
    """The realistic mistype is ONE relation object as the whole section value.
    `len(dict)` counts its three KEYS, so the counter added to make the report
    honest over-reported 3 lost relations for 1.

    ⛔ #11333 CHANGED THE OUTCOME, not the count. Before the fix this value was
    dropped as a section (`relations == []`, `relations_dropped == 1`) while the
    SAME value beside a usable list was recovered — the census answering a
    question about the merge ARRIVAL. It is now recovered on both arrivals, so
    the loss this test guarded is a KEPT edge and the count is 0. The `== 1`
    the counter was written for is pinned where it still applies:
    `test_a_single_relation_object_counts_its_candidate_once` below.
    """
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = {"src": "Retry Storm", "dst": "Deadline Rule",
                             "predicate": "IMPL"}
    result = _run(emission)
    assert [(r["src"], r["dst"], r["predicate"])
            for r in result["relations"]] == [
        ("Retry Storm", "Deadline Rule", "IMPL")]
    assert result["stats"]["relations_dropped"] == 0


def test_a_single_relation_object_counts_its_candidate_once():
    """The `len(dict)` defect this census row exists for, still pinned: the
    mistype is ONE candidate, so an UNRESOLVABLE one is reported as exactly 1
    lost relation (its three keys would say 3)."""
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = {"src": "Ann", "dst": "Bob",
                             "predicate": "IMPL"}
    result = _run(emission)
    assert result["relations"] == []
    assert result["stats"]["relations_dropped"] == 1
    assert any("connections_incomplete" in w for w in result["warnings"])


# ── 11. round-6 review of the merged head (delta c5da11b4c) ───────────────
#
# Every defect below was REPRODUCED at the head before its fix. Each test is
# the reproduction, not a restatement of the fix.


def test_entities_differing_only_in_case_are_distinct_relations():
    """Round-6 P2 (silent loss + a false report): the collapse key folded the
    resolved ENDPOINT names.

    Endpoints are already canonical emitted spellings by the time the key is
    built, so the `.casefold()` added nothing for a true duplicate — it merged
    two DISTINCT Layer-1 entities whose names differ only in case (they survive
    `execute_embed`'s `(name, kind)` dedupe as two Objects) and reported
    `relations_dropped == 0`, so the caller was told nothing was lost.
    """
    emission = {
        "entities": [
            {"name": "Billing", "kind": "core:rule"},
            {"name": "billing", "kind": "core:plan"},
            {"name": "Ledger", "kind": "core:rule"},
        ],
        "points": [], "events": [], "operators": [],
        "relations": [
            {"src": "Billing", "dst": "Ledger", "predicate": "IMPL"},
            {"src": "billing", "dst": "Ledger", "predicate": "IMPL"},
        ],
    }
    result = _run(emission)
    assert [e["name"] for e in result["payload"]["entities"]] == [
        "Billing", "billing", "Ledger"]
    assert [(r["src"], r["dst"]) for r in result["relations"]] == [
        ("Billing", "Ledger"), ("billing", "Ledger")]
    assert result["stats"]["relations_dropped"] == 0


def test_a_self_relation_reached_only_after_resolution_is_dropped():
    """Round-6 P3: the self-relation guard ran on the RAW spellings, before
    resolution.

    Two spellings can each resolve to the SAME emitted entity through a
    DIFFERENT fold while sharing neither fold with each other (`_norm` collapses
    the whitespace run; `.casefold()` folds ß→ss), so the raw guard saw two
    names and the relation reached the record as `(X)-[IMPL]->(X)`.
    """
    emission = {
        "entities": [
            {"name": "Straße B", "kind": "core:rule"},
            {"name": "Other", "kind": "core:rule"},
        ],
        "points": [], "events": [], "operators": [],
        "relations": [{"src": "straße  b", "dst": "STRASSE B",
                       "predicate": "IMPL"}],
    }
    result = _run(emission)
    assert result["relations"] == []
    assert result["stats"]["relations_dropped"] == 1
    assert any("self-relation" in w for w in result["warnings"])


def test_an_ambiguous_same_name_pair_is_named_not_silently_carried():
    """Round-6 P2: the EXACT arm returned on the first name hit with no
    uniqueness test, while the fold arm refuses to guess.

    `execute_embed` keeps same-name/different-kind entities as distinct Objects
    (its dedupe key is `(name, kind)`), so an endpoint spelled exactly like a
    two-Object name denotes BOTH and the record carried no warning. The channel
    stays fail-open (the relation is kept — dropping is the destructive
    direction), but the ambiguity is NAMED so the write leg cannot attribute
    the edge silently.
    """
    emission = {
        "entities": [
            {"name": "Billing", "kind": "core:rule"},
            {"name": "Billing", "kind": "core:plan"},
            {"name": "Ledger", "kind": "core:rule"},
        ],
        "points": [], "events": [], "operators": [],
        "relations": [{"src": "Billing", "dst": "Ledger",
                       "predicate": "IMPL"}],
    }
    result = _run(emission)
    assert len([e for e in result["payload"]["entities"]
                if e["name"] == "Billing"]) == 2
    assert len(result["relations"]) == 1
    assert any("ambiguous" in w and "Billing" in w
               for w in result["warnings"]), result["warnings"]


def test_the_rule4_restore_matches_a_casefold_only_endpoint_spelling():
    """Round-6 P2: the Rule-4 RESTORE (the arm this PR added) matched only the
    `_norm` key, so a casefold-only endpoint spelling restored nothing.

    The DISCARD arm was widened to both folds; the restore arm — whose whole
    purpose is the cross-pass case the channel introduces — was not, so the
    entity stayed gone and S5 then dropped the edge as unresolvable.
    """
    emission = {
        "entities": [{"name": "Other", "kind": "core:rule"}],
        "points": [], "events": [], "operators": [],
        "relations": [{"src": "Other", "dst": "STRASSE",
                       "predicate": "IMPL"}],
    }
    prior = {"removed_entities": {
        "straße": {"name": "Straße", "kind": "core:rule"}}}
    out, warns = vg.apply_vet(emission, {}, prior=prior)
    assert [e["name"] for e in out["entities"]] == ["Other", "Straße"]
    assert any("restored" in w for w in warns), warns
    assert [(r["src"], r["dst"]) for r in _run(out)["relations"]] == [
        ("Other", "Straße")]


def test_the_recovery_ladder_keeps_sections_after_a_mistyped_relations():
    """Round-6 P2 (regression): adding `relations` to `_OUTPUT_SCHEMA` made a
    mistyped section truncate the recovered emission.

    The ladder accepts the LONGEST prefix that validates, so a `relations`
    value shaped as one OBJECT (the exact mistype S5 drops and COUNTS, by
    design) failed the gate and the accepted prefix ended before it — taking
    every section the model emitted after it with it, where the same response
    returned them before this PR. S5 owns this section's shape failure, so the
    gate must not fail the candidate over it.
    """
    text = ('{"entities": [{"name": "A", "kind": "core:rule"}], '
            '"relations": {"src": "A", "dst": "B", "predicate": "IMPL"}, '
            '"points": [{"content": "the point that matters"}]')
    parsed = v2._longest_valid_prefix(text)
    assert parsed is not None
    assert parsed.get("points") == [{"content": "the point that matters"}], parsed
    # the tolerance is EXACTLY the non-list mistype S5 counts; an item-level
    # shape error is still refused (pinned by the §1 shape-gate tests)
    ok, issues = v2._validate_output_shape(
        {"relations": {"src": "A"}, "points": [{"content": "p"}]})
    assert ok, issues


def test_a_non_string_predicate_is_named_as_a_SHAPE_error():
    """Round-6 P3: a non-string predicate was coerced with `str()` and then
    reported as an INVENTED VERB, sending the model to fix a vocabulary mistake
    it did not make (`_SCHEMA_PRIMITIVES`' own comment claimed the gate
    prevented this, and on rung 1 it does not)."""
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Deadline Rule",
                              "predicate": 42}]
    result = _run(emission)
    assert result["relations"] == []
    assert result["stats"]["relations_dropped"] == 1
    rel_warns = [w for w in result["warnings"] if "relations[0]" in w]
    assert rel_warns and "not a string" in rel_warns[0], rel_warns


def test_a_non_dict_relation_entry_is_clipped_in_the_warning():
    """Round-6 P3: the non-dict arm `repr`'d the whole entry into the warning —
    unbounded model text in a structure every consumer carries, where every
    sibling arm in the same loop clips."""
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = ["x" * 5000]
    result = _run(emission)
    assert result["stats"]["relations_dropped"] == 1
    warns = [w for w in result["warnings"]
             if "non-dict relation entry" in w]
    assert warns and len(warns[0]) < 200, warns


def test_a_non_string_semantics_is_named_not_silently_ignored():
    """Round-6 P3: the one optional field of the new section failed silently on
    a wrong type, while its out-of-vocabulary STRING case is named."""
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Deadline Rule",
                              "predicate": "IMPL", "semantics": 7}]
    result = _run(emission)
    assert len(result["relations"]) == 1
    assert result["relations"][0].get("semantics") is None
    assert any("semantics" in w for w in result["warnings"]), result["warnings"]


def test_the_prose_pair_is_carried_verbatim_and_no_relation_token_reaches_it():
    """#10000 (P0) — the pin, non-vacuously.

    The earlier pin was `assert "story_arc" not in payload or not
    payload["story_arc"]`, whose left disjunct is always False (the payload
    ALWAYS carries `story_arc`) and whose right one was True only because the
    fixture never passed one — so it would have stayed green if the channel
    ever started writing the prose pair. This drives a real `summary` and
    `story_arc` through S5 with a NON-EMPTY channel and asserts the prose
    arrives verbatim, the channel stays out of the payload, and no relation
    token reaches either prose field.
    """
    result = v2.execute_embed(copy.deepcopy(EMISSION), {}, session_id="s-10999",
                              summary="the batch narrative", story_arc="the arc")
    assert result["relations"], "the channel is non-empty — this pin is not vacuous"
    assert result["payload"]["summary"] == "the batch narrative"
    assert result["payload"]["story_arc"] == "the arc"
    assert "relations" not in result["payload"]
    carried = f"{result['payload']['summary']} {result['payload']['story_arc']}"
    assert "IMPL" not in carried
    assert "Retry Storm" not in carried


def test_the_vet_guard_and_the_resolver_agree_on_the_endpoint_fold():
    """Round-6 P2 (the "one home" claim's boundary): `vet_gate` cannot import
    `extractor_v2` (cycle), so its DISCARD/Rule-4 arm derives the endpoint fold
    itself. Nothing pinned the two derivations to agree, so a future fold change
    in `extractor_v2._endpoint_fold_keys` would silently re-open the discard gap
    this PR closed.

    This is a DRIFT PIN, not a reproduction: it passes at the head, and it is
    the red that fires if either side changes alone.
    """
    corpus = [
        ("Straße", "STRASSE"), ("Straße", "straße"),
        ("Retry  Storm", "retry storm"), ("Retry Storm", "RETRY  STORM"),
        ("İstanbul", "istanbul"), ("İstanbul", "i̇stanbul"),
        ("ﬁle", "FILE"), ("Σίσυφος", "σίσυφοσ"),
    ]
    for emitted, ref in corpus:
        embedding = {
            "entities": [{"name": emitted, "kind": "core:rule"}],
            "points": [], "events": [], "operators": [],
            "relations": [{"src": ref, "dst": "Peer", "predicate": "IMPL"}],
        }
        protected = vg._referenced_entity_names(embedding, set())
        exact = {emitted}
        resolved = v2._resolve_relation_endpoint(
            ref, exact, v2._endpoint_fold_index(exact))
        assert (vg._norm(emitted) in protected) == (resolved is not None), (
            emitted, ref, protected, resolved)


def test_the_s2_s4_union_keeps_relations_whose_endpoints_differ_only_in_case():
    """Round-7 P2: the union's identity key folded the endpoints.

    `_merge_key` used `_norm`, which LOWERCASES — so the S2∪S4 union merged two
    DIFFERENT relations between two pairs of DISTINCT Layer-1 entities before S5
    ever saw them, and the loss was reported as `relations_dropped == 0`. S5's
    own collapse key is exact on the resolved endpoints; the union must agree or
    the two derivations of relation identity contradict each other.
    """
    s2 = {"relations": [{"src": "Billing", "dst": "Ledger", "predicate": "IMPL"}]}
    s4 = {"relations": [{"src": "billing", "dst": "Ledger", "predicate": "IMPL"}]}
    merged = v2.merge_embed_lists(s2, s4)
    assert [r["src"] for r in merged["relations"]] == ["Billing", "billing"]
    # ...and the predicate is still folded: IMPL/impl is ONE verb
    merged2 = v2.merge_embed_lists(
        {"relations": [{"src": "A", "dst": "B", "predicate": "IMPL"}]},
        {"relations": [{"src": "A", "dst": "B", "predicate": "impl",
                        "semantics": "supports"}]})
    assert len(merged2["relations"]) == 1
    assert merged2["relations"][0]["semantics"] == "supports"


def test_the_union_carries_a_one_object_relations_section_to_s5():
    """Round-7 P2: the union coerced a mistyped section to `[]`.

    Iterating a dict yields its KEYS, all filtered out by the `isinstance(item,
    dict)` comprehension, and the section was then written as an empty list — so
    S5's counted-drop arm saw "no relations" and the record reported nothing
    lost. The mistyped value must reach S5 as it arrived.
    """
    merged = v2.merge_embed_lists(
        {"entities": [{"name": "Retry Storm", "kind": "core:rule"},
                      {"name": "Deadline Rule", "kind": "core:rule"}],
         "relations": {"src": "Retry Storm", "dst": "Deadline Rule",
                       "predicate": "IMPL"}},
        {})
    assert merged["relations"] == {"src": "Retry Storm", "dst": "Deadline Rule",
                                   "predicate": "IMPL"}
    result = _run(merged)
    # ⛔ #11333: the union's job asserted here — the mistype reaching S5 AS IT
    # ARRIVED — is unchanged. What changed is what S5 does with it: the value is
    # ONE relation, so it is recovered rather than dropped (`== []` / `== 1`
    # here was the arrival-dependent census the issue reports).
    assert [r["predicate"] for r in result["relations"]] == ["IMPL"]
    assert result["stats"]["relations_dropped"] == 0


def test_the_restore_does_not_duplicate_an_entity_already_held_in_another_case():
    """Round-7 P2: the Rule-4 restore's exact-`_norm` branch had no casefold
    guard, so it appended a SECOND copy of an entity the pass already held.

    Two Objects for one entity also make S5's fold arm ambiguous, so the edge
    the restore exists to save was dropped one step later — the arm failing at
    the exact shape it was widened for.
    """
    emission = {
        "entities": [{"name": "Straße", "kind": "core:rule"},
                     {"name": "Other", "kind": "core:rule"}],
        "points": [], "events": [], "operators": [],
        "relations": [{"src": "Strasse", "dst": "Other", "predicate": "IMPL"}],
    }
    prior = {"removed_entities": {
        "strasse": {"name": "STRASSE", "kind": "core:rule"}}}
    out, warns = vg.apply_vet(emission, {}, prior=prior)
    names = [e["name"] for e in out["entities"]]
    assert names == ["Straße", "Other"], names
    assert not [w for w in warns if "restored" in w], warns
    assert [(r["src"], r["dst"]) for r in _run(out)["relations"]] == [
        ("Straße", "Other")]


def test_the_restore_adds_one_copy_when_both_spellings_are_referenced():
    """Round-7 P3 (the same guard, from the other side): two divergent spellings
    of one entity can both be in `to_restore`; the second must not restore the
    same entity again."""
    emission = {
        "entities": [{"name": "Alpha", "kind": "core:rule"}],
        "points": [], "events": [], "operators": [],
        "relations": [
            {"src": "STRASSE", "dst": "Alpha", "predicate": "IMPL"},
            {"src": "Straße", "dst": "Alpha", "predicate": "NAND"},
        ],
    }
    prior = {"removed_entities": {
        "strasse": {"name": "STRASSE", "kind": "core:rule"}}}
    out, _warns = vg.apply_vet(emission, {}, prior=prior)
    names = [e["name"] for e in out["entities"]]
    assert names.count("STRASSE") == 1, names
    assert len(names) == 2, names


def test_a_mistyped_section_never_displaces_a_valid_list_from_the_other_side():
    """Round-7 P1 (a regression the VGATE verifier caught): the mistype
    passthrough applied to EVERY section and preferred a mistype over a usable
    list on the other side, reversing #1789 Task 3 ("kept S2 content for that
    section") and, for `entities`, producing a Layer-1 422-class payload."""
    s2 = {"relations": [{"src": "A", "dst": "B", "predicate": "IMPL"},
                        {"src": "B", "dst": "C", "predicate": "NAND"}]}
    s4 = {"relations": {"src": "X", "dst": "Y", "predicate": "IMPL"}}
    merged = v2.merge_embed_lists(s2, s4)
    # The usable LIST survives in place, AND the mistype is CARRIED behind it so
    # S5's own census can count and name it. Its previous form asserted
    # `len == 2` — i.e. that the mistyped side VANISHED — which is the silent
    # loss review round 10 raised: a dict filter over a mistyped mapping yields
    # its KEYS (zero items), so the side disappeared with
    # `relations_dropped == 0` and no warning anywhere.
    assert merged["relations"][:2] == s2["relations"], merged["relations"]
    assert merged["relations"][2] == s4["relations"], merged["relations"]

    # a mistype on the OTHER side of a valid section must not win either
    merged2 = v2.merge_embed_lists(
        {"entities": [{"name": "A", "kind": "core:rule"}]},
        {"entities": "garbage"})
    assert [e["name"] for e in merged2["entities"]] == ["A"]

    # and when NEITHER side carries a NON-EMPTY list, the mistype rides through
    # (S5 counts) — an EMPTY list is the normal "no gaps" delta, not a carrier
    merged3 = v2.merge_embed_lists({"relations": {"src": "A"}},
                                   {"relations": {"src": "B"}})
    assert merged3["relations"] == {"src": "B"}
    merged4 = v2.merge_embed_lists(
        {"relations": {"src": "A", "dst": "B", "predicate": "IMPL"}},
        {"relations": []})
    assert merged4["relations"] == {"src": "A", "dst": "B",
                                    "predicate": "IMPL"}
    # ⛔ #11333: this row used to assert `relations_dropped == 1` — S5 losing the
    # edge while the SAME value beside a usable list kept it. The section is now
    # judged by the same candidate rule, so the endpoint 'A'/'B' — which this
    # emission never produced — is what drops it (still 1, still counted), and
    # the ARRIVAL no longer decides. THIS body asserts the section arrival only;
    # both arrivals are asserted in §12 below.
    assert v2.execute_embed(copy.deepcopy(merged4), {},
                            session_id="s-e")["stats"]["relations_dropped"] == 1


def test_a_layer1_reference_is_restored_exactly_not_re_pointed_at_a_sibling():
    """Round-7 P2 (re-review): the duplicate-guard must NOT skip a LAYER-1
    reference.

    Payload identity is EXACT (`(str(name).strip(), kind)`), so when `prior` can
    supply the very spelling a point's `about_entities` names, restoring it is
    correct; re-pointing the reference at a casefold sibling would silently
    attribute the statement to a DIFFERENT entity — and a Layer-1-valid payload
    surfaces nothing. Measured pre-fix: the reference was rewritten to
    `Straße` with no warning.
    """
    emission = {
        "entities": [{"name": "Straße", "kind": "core:rule"}],
        "points": [{"content": "a point", "about_entities": ["STRASSE"]}],
        "events": [], "operators": [], "relations": [],
    }
    prior = {"removed_entities": {
        "strasse": {"name": "STRASSE", "kind": "core:rule"}}}
    out, warns = vg.apply_vet(emission, {}, prior=prior)
    assert [e["name"] for e in out["entities"]] == ["Straße", "STRASSE"]
    assert out["points"][0]["about_entities"] == ["STRASSE"]
    assert any("restored" in w for w in warns), warns


def test_a_relation_endpoint_already_held_in_another_case_is_reconciled():
    """...while a RELATION endpoint whose entity the pass already holds is the
    same entity: no second copy, and the endpoint spelling is reconciled to the
    held name (so the edge survives and nothing names an absent spelling)."""
    emission = {
        "entities": [{"name": "Straße", "kind": "core:rule"},
                     {"name": "Other", "kind": "core:rule"}],
        "points": [], "events": [], "operators": [],
        "relations": [{"src": "STRASSE", "dst": "Other",
                       "predicate": "IMPL"}],
    }
    prior = {"removed_entities": {
        "strasse": {"name": "STRASSE", "kind": "core:rule"}}}
    out, warns = vg.apply_vet(emission, {}, prior=prior)
    assert [e["name"] for e in out["entities"]] == ["Straße", "Other"]
    assert not [w for w in warns if "restored" in w], warns
    # the endpoint is resolved by S5's fold, so the edge survives with the
    # emission's own spelling
    assert [(r["src"], r["dst"]) for r in _run(out)["relations"]] == [
        ("Straße", "Other")]


def test_a_layer1_reference_reconciles_onto_a_held_casefold_sibling():
    """Round-8 P2: when `prior` cannot supply the reference's own spelling, the
    emission may still HOLD the entity under a casefold sibling — and payload
    identity is EXACT, so leaving the reference as written makes
    `validate_layer1` reject the whole session (422) where the parent commit
    reconciled it. Reconcile; warn only when nothing can be."""
    emission = {
        "entities": [{"name": "Straße", "kind": "core:rule"},
                     {"name": "Z", "kind": "core:rule"}],
        "points": [{"content": "a point", "about_entities": ["STRASSE"]}],
        "events": [], "operators": [], "relations": [],
    }
    # prior holds nothing casefold-equal, so the restore branch cannot answer;
    # the reference must be reconciled onto the HELD spelling. (A DISCARD keeps
    # `apply_vet` from early-returning on an empty `prior`.)
    out, _warns = vg.apply_vet(emission, _discard_entity(emission, 1))
    assert [e["name"] for e in out["entities"]] == ["Straße"]
    assert out["points"][0]["about_entities"] == ["Straße"]


def test_a_layer1_divergent_pair_restores_one_copy():
    """Round-8 P3: two divergent Layer-1 references can both resolve to ONE
    prior entry; the second must reconcile, not append a duplicate or repeat
    the warning."""
    emission = {
        "entities": [{"name": "Z", "kind": "core:rule"}],
        "points": [{"content": "p1", "about_entities": ["Straße"]},
                   {"content": "p2", "about_entities": ["STRASSE"]}],
        "events": [], "operators": [], "relations": [],
    }
    prior = {"removed_entities": {
        "straße": {"name": "Straße", "kind": "core:rule"}}}
    out, warns = vg.apply_vet(emission, {}, prior=prior)
    names = [e["name"] for e in out["entities"]]
    assert names == ["Z", "Straße"], names
    assert sum(1 for w in warns if "restored" in w) == 1, warns
    assert out["points"][0]["about_entities"] == ["Straße"]
    assert out["points"][1]["about_entities"] == ["Straße"]


# ── 9. review round 10 (head 4ff6f2310) — the panel's blockers ───────────

def test_an_endpoint_spelled_as_the_pre_rename_name_beats_a_casefold_sibling():
    """P2: an endpoint that EXACTLY names a renamed entity must resolve onto
    THAT entity, not onto a surviving casefold sibling.

    `emitted` holds POST-rename spellings only, so the pre-rename spelling had
    no exact match and fell through to the fold arm — where a sibling answered
    for it. The edge landed on the WRONG node, reported as
    `relations_dropped == 0` with no warning.
    """
    emission = {
        "entities": [{"name": "Billing", "kind": "core:rule"},
                     {"name": "billing", "kind": "core:plan"},
                     {"name": "Ledger", "kind": "core:rule"}],
        "relations": [{"src": "Billing", "dst": "Ledger", "predicate": "IMPL"}],
        "events": [], "points": [], "operators": [],
    }
    resolved = v2._apply_entity_resolution(
        emission, {"Billing": {"name": "Billing Inc"}})
    assert resolved["relations"][0]["src"] == "Billing Inc", \
        resolved["relations"]
    result = _run(resolved)
    assert result["relations"][0]["src"] == "Billing Inc"
    assert result["stats"]["relations_dropped"] == 0


def test_two_case_variant_entities_are_a_real_edge_not_a_self_relation():
    """P2: names differing only in case are DISTINCT Layer-1 Objects
    (`execute_embed` dedupes on `(name, kind)`), so a relation between them is
    a REAL edge. The raw fold test dropped it and called it 'a self-relation',
    which sends the model to fix a non-problem."""
    emission = {
        "entities": [{"name": "Apple", "kind": "core:organization"},
                     {"name": "APPLE", "kind": "core:product"}],
        "relations": [{"src": "Apple", "dst": "APPLE", "predicate": "IMPL"}],
        "events": [], "points": [], "operators": [],
    }
    result = _run(emission)
    assert result["stats"]["relations_dropped"] == 0, result["warnings"]
    assert len(result["relations"]) == 1
    assert {result["relations"][0]["src"], result["relations"][0]["dst"]} == {
        "Apple", "APPLE"}


def test_a_true_self_relation_is_still_dropped_and_named():
    """The fix above must not lose the guard it replaced: one entity named by
    two spellings that RESOLVE to it is still a self-relation."""
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "retry storm",
                              "predicate": "IMPL"}]
    result = _run(emission)
    assert result["relations"] == []
    assert result["stats"]["relations_dropped"] == 1
    assert any("self-relation" in w for w in result["warnings"]), result["warnings"]


def test_a_mistyped_section_beside_a_usable_list_is_still_counted():
    """P2: a mistyped S2 `relations` must not vanish because S4 carried a real
    list. The union's dict filter iterated the mapping's KEYS — zero items — so
    the whole side was lost with `relations_dropped == 0` and no warning."""
    # (a) the single-object mistype encodes ONE relation and is recovered.
    merged = v2.merge_embed_lists(
        {"entities": copy.deepcopy(EMISSION["entities"]),
         "relations": {"src": "Retry Storm", "dst": "Deadline Rule",
                       "predicate": "IMPL"}},
        {"relations": [{"src": "Retry Storm", "dst": "Deadline Rule",
                        "predicate": "IMPL"}]})
    result = _run(merged)
    assert len(result["relations"]) == 1, result["relations"]
    assert result["stats"]["relations_dropped"] == 0
    # (b) when its endpoints do not resolve it is COUNTED and named, not lost.
    merged = v2.merge_embed_lists(
        {"entities": copy.deepcopy(EMISSION["entities"]),
         "relations": {"src": "Ann", "dst": "Bob", "predicate": "IMPL"}},
        {"relations": [{"src": "Retry Storm", "dst": "Deadline Rule",
                        "predicate": "IMPL"}]})
    result = _run(merged)
    assert result["stats"]["relations"] == 1
    assert result["stats"]["relations_dropped"] == 1
    assert any("does not resolve" in w for w in result["warnings"]), \
        result["warnings"]


def test_a_non_dict_relation_entry_reaches_S5s_census():
    """P2: an item-level mistype ("garbage", an array-shaped triple) was
    filtered out by the union's dict comprehension whenever S4 emitted content,
    so S5 never counted it: `relations_dropped == 0`, no warning."""
    merged = v2.merge_embed_lists(
        {"entities": copy.deepcopy(EMISSION["entities"]),
         "relations": [{"src": "Retry Storm", "dst": "Deadline Rule",
                        "predicate": "IMPL"}, "garbage"]},
        {"points": [{"content": "p", "about_entities": ["Retry Storm"]}]})
    result = _run(merged)
    assert result["stats"]["relations"] == 1
    assert result["stats"]["relations_dropped"] == 1
    assert any("non-dict relation entry" in w for w in result["warnings"]), \
        result["warnings"]


def test_a_falsy_mistyped_section_is_named_not_silent():
    """P3: `if not raw` returned before the mistype arm, so `"relations": ""`
    (or 0) produced no diagnostic at all and `relations_dropped` stayed 0."""
    for bad in ("", 0):
        emission = copy.deepcopy(EMISSION)
        emission["relations"] = bad
        result = _run(emission)
        assert result["stats"]["relations_dropped"] == 1, bad
        assert any("not a list" in w for w in result["warnings"]), \
            (bad, result["warnings"])


def test_an_unverifiable_predicate_is_clamped(monkeypatch):
    """P3: the fail-open arm kept the model's verb verbatim — 5,000 chars and a
    newline reached a record the write leg (#11237) carries as an edge
    property, which is the prose surface this channel exists to keep out.

    ⛔ #11333: the fixture used to put the newline at index 5,000 — PAST the
    64-char cut — so the row was green whether or not control characters were
    handled at all (the non-discriminating-fixture class of #11236). The
    newline now sits at index 10, inside the kept window, so length alone
    cannot satisfy this row.
    """
    monkeypatch.setattr(v2, "_declared_relation_predicates", lambda: None)
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = [{"src": "Retry Storm", "dst": "Deadline Rule",
                              "predicate": "x" * 10 + "\nDROP" + "x" * 5000}]
    result = _run(emission)
    kept = result["relations"][0]["predicate"]
    assert len(kept) == v2._UNVERIFIED_TEXT_CAP, len(kept)
    assert "\n" not in kept
    assert any("clamped" in w for w in result["warnings"]), result["warnings"]
    assert any("control characters replaced" in w for w in result["warnings"]), \
        result["warnings"]


# ── 12. #11333 — the shape census must not depend on the ARRIVAL ──────────
#
# The shape census (kept / dropped) is a report about the VALUE the model
# emitted. One value reaches S5 two ways:
#
#   SECTION  `{"relations": <value>}`                      — the value IS the section
#   BESIDE   `merge_embed_lists(...)` with a usable list    — the value rides as an
#                                                             isolate ITEM behind it
#
# Judged by two rules, the same value was KEPT as an item and DROPPED as a
# section, so `relations_dropped` answered a question about the merge shape. The
# rows below pin one rule (`_relation_candidates`) for both arrivals, and the
# measured disagreement on the pre-fix tree (`de72cfd455`) was 3/3 values:

#     value                     as SECTION   as ITEM (delta)
#     one relation object        0 kept/1 drop   1 kept/0 drop
#     mapping of 2 relations     0 kept/2 drop   0 kept/1 drop
#     {}                         0 kept/0 drop   0 kept/1 drop

_ONE_RELATION = {"src": "Retry Storm", "dst": "Deadline Rule",
                 "predicate": "NAND"}
_TWO_RELATIONS = {
    "a": {"src": "Retry Storm", "dst": "Deadline Rule", "predicate": "NAND"},
    "b": {"src": "Deadline Rule", "dst": "Retry Storm", "predicate": "IMPL"},
}
#: The usable relation the "beside" arrival carries; its own edge is subtracted
#: from that arrival's counts so the two columns describe the SAME value.
_BASE_RELATION = {"src": "Retry Storm", "dst": "Deadline Rule",
                  "predicate": "IMPL"}


def _as_section(value):
    emission = copy.deepcopy(EMISSION)
    emission["relations"] = copy.deepcopy(value)
    return _run(emission)


def _beside(value):
    """The value beside a usable list — the shape `merge_embed_lists` emits when
    one side of the S2∪S4 union carries a mistype (#11205's isolate path)."""
    merged = v2.merge_embed_lists(
        {"entities": copy.deepcopy(EMISSION["entities"]),
         "relations": [copy.deepcopy(_BASE_RELATION)]},
        {"relations": copy.deepcopy(value)})
    return _run(merged)


def _value_counts(result, carried_elsewhere: int = 0) -> tuple[int, int]:
    return (result["stats"]["relations"] - carried_elsewhere,
            result["stats"]["relations_dropped"])


@pytest.mark.parametrize("label,value,expected", [
    ("one relation object", _ONE_RELATION, (1, 0)),
    ("mapping of 2 relations", _TWO_RELATIONS, (2, 0)),
    ("empty mapping", {}, (0, 0)),
])
def test_the_census_does_not_depend_on_the_arrival(label, value, expected):
    """#11333 defect 2: ONE value judged two ways.

    A section-arrival value and the same value riding beside a usable list must
    report the same `(kept, dropped)` — the census describes the value, not the
    merge that produced it.
    """
    as_section = _value_counts(_as_section(value))
    as_item = _value_counts(_beside(value), carried_elsewhere=1)
    assert as_section == expected, (label, "section", as_section)
    assert as_item == expected, (label, "beside", as_item)


def test_a_one_object_section_carries_its_edge():
    """#11333 defect 1: the realistic mistype — the model emitting the TRIPLE
    where a list of triples was asked for — lost its edge on a missing pair of
    brackets, and only when it arrived as the whole section.
    """
    result = _as_section(_ONE_RELATION)
    assert [(r["src"], r["dst"], r["predicate"])
            for r in result["relations"]] == [
        ("Retry Storm", "Deadline Rule", "NAND")]
    assert result["stats"]["relations_dropped"] == 0
    # the shape error is still NAMED, and named as RECOVERED — a warning that
    # says "ignored" over a kept edge is the mirror of the silent loss.
    shape = [w for w in result["warnings"] if "not a list" in w]
    assert shape, result["warnings"]
    assert "recovered" in shape[0], shape


def test_the_recovered_one_object_section_still_mints_nothing():
    """The recovery is SHAPE-only: the value is validated exactly as a list item
    is, so an endpoint this emission never produced is still dropped, named, and
    never materialised (the mint pin the channel's whole safety rests on)."""
    result = _as_section({"src": "Retry Storm", "dst": "Ghost Rule",
                          "predicate": "NAND"})
    assert result["relations"] == []
    assert result["stats"]["relations_dropped"] == 1
    assert {e["name"] for e in result["payload"]["entities"]} == {
        "Retry Storm", "Deadline Rule"}
    assert any("NOT minted" in w for w in result["warnings"]), \
        result["warnings"]


def test_a_mapping_section_is_judged_entry_by_entry():
    """A mapping OF relations is two relations, not one lost blob: the mapping
    arm is `_relation_candidates`'s dict-without-the-triple case."""
    result = _as_section(_TWO_RELATIONS)
    assert sorted((r["src"], r["dst"], r["predicate"])
                  for r in result["relations"]) == [
        ("Deadline Rule", "Retry Storm", "IMPL"),
        ("Retry Storm", "Deadline Rule", "NAND")]
    assert result["stats"]["relations_dropped"] == 0


def test_an_empty_mapping_section_loses_nothing():
    """`{}` carries no relation, so it loses none — and it is still NAMED.

    ⚠️ This row is NOT the discriminating half of the fix: the section arrival
    already reported `0 kept / 0 dropped` before #11333 (only the BESIDE arrival
    reported 1 dropped), so every assertion here passes on the pre-fix tree. The
    discriminating row is `test_the_census_does_not_depend_on_the_arrival`'s
    `empty mapping` case, which measures both arrivals.
    """
    result = _as_section({})
    assert result["relations"] == []
    assert result["stats"]["relations_dropped"] == 0
    assert any("not a list" in w for w in result["warnings"]), \
        result["warnings"]


def test_an_unverifiable_predicate_shorter_than_the_cap_loses_its_newline(
        monkeypatch):
    """#11333 defect 3 at the DISCRIMINATING size: `_UNVERIFIED_TEXT_CAP` bounds
    length, while its comment claims control characters are kept out too.

    A 3-char `"a\\nb"` is inside the cap, so length can never be what saves this
    row — the earlier 5,000-char fixture put the newline PAST the cut and stayed
    green with the defect live (#11236's non-discriminating-fixture class).
    """
    monkeypatch.setattr(v2, "_declared_relation_predicates", lambda: None)
    result = _as_section([{"src": "Retry Storm", "dst": "Deadline Rule",
                           "predicate": "a\nb"}])
    kept = result["relations"][0]["predicate"]
    assert not any(ord(c) < 0x20 or ord(c) == 0x7F for c in kept), repr(kept)
    assert "a b" in kept, kept
    warnings = [w for w in result["warnings"] if "predicate" in w]
    assert any("control characters replaced" in w for w in warnings), warnings


def test_an_unverified_semantics_shorter_than_the_cap_loses_its_control_char(
        monkeypatch):
    """The SECOND unverified call site (`semantics`), which the cap's comment
    also covers: with the Layer-2 vocabulary unreadable the value is carried
    verbatim, and `semantics` is an edge property on the write leg (#11237)."""
    # a `None` module entry makes `from tortoise.pack_registry import …` raise
    # ImportError — the same path a real unreadable registry takes (both the
    # declared-predicate and the Layer-2 arm degrade fail-open).
    monkeypatch.setitem(sys.modules, "tortoise.pack_registry", None)
    result = _as_section([{"src": "Retry Storm", "dst": "Deadline Rule",
                           "predicate": "IMPL", "semantics": "a\rb"}])
    kept = result["relations"][0]["semantics"]
    assert not any(ord(c) < 0x20 or ord(c) == 0x7F for c in kept), repr(kept)
    assert "a b" in kept, kept
    warnings = [w for w in result["warnings"] if "semantics" in w]
    assert any("control characters replaced" in w for w in warnings), warnings


def test_a_list_of_mappings_reaches_both_arrivals_intact():
    """#11333 review P3 — the residual the docstring's "one rule" claim hid.

    A LIST of mapping items is 2 relations judged entry by entry. As the whole
    section it was 2 kept; beside a usable list the union keyed every mapping
    item under `("relation", "", "", "")` and silently kept only the first
    (`relations_dropped == 0`) — the same arrival-dependent, UNCOUNTED loss this
    issue is about, one shape further out. `_merge_key` now keys a non-triple
    mapping by its content, so both arrivals carry both relations.
    """
    value = [{"a": copy.deepcopy(_TWO_RELATIONS["a"])},
             {"b": copy.deepcopy(_TWO_RELATIONS["b"])}]
    assert _value_counts(_as_section(value)) == (2, 0)
    assert _value_counts(_beside(value), carried_elsewhere=1) == (2, 0)


def test_an_unverified_field_loses_a_C1_or_unicode_line_boundary(monkeypatch):
    """The bound guards against a value that RE-PARSES into several properties, so
    the line-boundary points count too: C1 NEL (U+0085) and the Unicode LINE /
    PARAGRAPH separators (U+2028/U+2029) split a value under `splitlines()`
    exactly as LF does, while a C0-only regex leaves them in the record.
    """
    monkeypatch.setattr(v2, "_declared_relation_predicates", lambda: None)
    for bad in ("a\x85b", "a\u2028b", "a\u2029b", "a\x7fb"):
        result = _as_section([{"src": "Retry Storm", "dst": "Deadline Rule",
                               "predicate": bad}])
        kept = result["relations"][0]["predicate"]
        assert not any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F
                       or ord(c) in (0x2028, 0x2029) for c in kept), (bad, repr(kept))
        assert kept == "a b", (bad, repr(kept))
