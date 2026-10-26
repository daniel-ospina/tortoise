"""#4282 phase 2.2 — the four merges into the approved 40.

The owner's approved SDK target (`docs/product/beta-sdk-surface.md`, rows 12, 19, 22, 24)
has **one** verb for each of four jobs. Each merged verb absorbs the narrower doors that
used to do the job separately, and dispatches to them from the caller's SEMANTIC INTENT —
the `type`, the `relation`, the temporal field — never by copying their graph logic.

WHAT THIS FILE IS THE GATE ON

  1. **Nothing was added to the surface.** All four names are rows of the approved list and
     already live in `tortoise/__all__.py`; the declaration stays at exactly 40. A merge
     that invented a fifth spelling would be an unapproved surface change.
  2. **Each merge routes to the door it absorbed** — asserted per arm, by substitution, so
     a regression names the arm that broke rather than "dispatch is wrong".
  3. **Behaviour is preserved, not re-implemented.** The narrow doors are still the writers:
     the merged verb calls them, so a caller who used the narrow name lands in the same
     place. `test_delete_knowledge_keeps_the_two_arms_the_narrow_doors_had` pins that for
     the one merge whose narrow door has its own branch; the others assert the ARGUMENTS
     the door receives, which is what a delegation can actually get wrong.
  4. **The refusal arms stay refusals.** A merge must not soften a guard (an unknown type
     still raises; `corrected_by_id` with no temporal field is still an error; a parameter
     an arm cannot record is refused, never silently dropped).
  5. **The arguments are ACCEPTED BY THE DOOR, not just routed to it.** A routing assertion
     proves which door fired; it cannot prove the door accepts what it was handed (#4282:
     `link_entities("supports", …)` routed to `create_operator` with `'SUPPORTS'` as its
     `op_type` and raised on every call). The `_accepts` tests below exercise the real
     writer's own contract.

The narrow doors are substituted on the INSTANCE, so no database is needed: these are
routing tests, and routing is what a merge changes. The writers' own behaviour is covered
by their own suites, which this change does not touch.
"""
from __future__ import annotations

import pytest

from tools.sdk_surface_declaration import load_declaration
from tortoise.sdk import TortoiseSDK

#: The approved surface, read the SUPPORTED way — by path, through the gate's own loader.
#: ⛔ Never `from tortoise import __all__`: that imports the MODULE `tortoise.__all__`, and the
#: import system then binds it onto the package as `tortoise.__all__`, which breaks
#: `from tortoise import *` for the rest of the process and reds
#: `test_sdk_surface_declaration.py::test_loading_the_declaration_by_path_does_not_shadow_star_import`
#: — the test that exists precisely to catch this. Found by running this file together with
#: that one; alone, each passed.
DECLARATION: tuple[str, ...] = tuple(load_declaration()["__all__"])

#: The four merge targets — `docs/product/beta-sdk-surface.md` rows 12, 19, 22, 24.
MERGES = ("create_entity", "link_entities", "update_knowledge", "delete_knowledge")

#: A complete annotation — `annotate_operator` requires all four dimensions, so a partial
#: dict is refused for a DIFFERENT reason and would mask the accept-and-drop cases below.
_FULL_ANNOTATION = {"bias": 0.1, "precision": 0.2, "consistency": 0.3, "directness": 0.4}


def _sdk() -> TortoiseSDK:
    """A bare SDK with no graph — every door this file exercises is substituted."""
    return TortoiseSDK.__new__(TortoiseSDK)


@pytest.fixture
def sdk(monkeypatch):
    """An SDK whose write doors are recorded instead of executed."""
    inst = _sdk()
    inst.calls = []  # type: ignore[attr-defined]

    def record(name):
        def _spy(*args, **kwargs):
            inst.calls.append((name, args, kwargs))
            # Shape-tolerant: `create_entity` returns {node, nudges} and the typed
            # aliases flatten it, so the spy answers in that shape.
            return {"node": {"id": "spy"}, "nudges": []}

        return _spy

    for door in (
        "create_entity",
        "create_derivation",
        "create_direct_edge",
        "create_operator",
        "create_edge",
        "link_source_to_entity",
        "update",
        "retract_point",
        "invalidate_point",
        "annotate_operator",
        "delete",
        "delete_point",
        "delete_entity",
    ):
        monkeypatch.setattr(inst, door, record(door))
    return inst


# ── 1. nothing added to the surface ──────────────────────────────────────────


def test_the_four_merges_are_rows_of_the_approved_40_and_the_declaration_is_unchanged():
    assert len(DECLARATION) == 40, (
        "the approved declaration is 40 names; a merge must not change it")
    for name in MERGES:
        assert name in DECLARATION, f"{name} must already be approved"
    for name in MERGES:
        assert callable(getattr(TortoiseSDK, name, None)), (
            f"approved and declared but absent from TortoiseSDK: {name}")


# ── 2a. create_entity absorbs the four typed constructors (epic #888 W2 shape) ──


@pytest.mark.parametrize(
    ("alias", "type_", "extra"),
    [
        ("create_subject", "subject", {"name": "Acme"}),
        ("create_object", "object", {"name": "Widget"}),
        ("create_event", "event", {"name": "Kickoff", "eventKind": "core:meeting"}),

    ],
)
def test_create_entity_is_the_door_the_typed_constructors_land_in(sdk, alias, type_, extra):
    """The absorbed constructor reaches `create_entity(type=...)` — same writer, same place."""
    getattr(sdk, alias)(**extra)
    door, args, _kwargs = sdk.calls[-1]
    assert door == "create_entity", f"{alias} must land in create_entity, got {door}"
    assert args[0] == type_, f"{alias} must route type={type_!r}, got {args[0]!r}"


def test_create_document_is_a_parallel_writer_not_an_alias_FINDING(monkeypatch):
    """⛔ `create_document`'s docstring says "Thin alias for create_entity(type='document')".
    It is not: it calls `_create_entity` DIRECTLY and then `_link_source` itself, so it is
    a second writer on the same door — and the two have already diverged once (#8171: the
    `_link_source` half was LOST when `type='document'` was added to `create_entity`).

    Pinned here as a finding, not fixed: re-pointing `create_document` at `create_entity`
    is a behaviour change (the arms differ in how they mint the `:Source` and the
    `extractedFrom` edge) and belongs in its own change with its own evidence. What this
    test guarantees is that the divergence stays VISIBLE instead of being assumed away by
    the absorption ledger.
    """
    import inspect

    source = inspect.getsource(TortoiseSDK.create_document)
    assert "_create_entity(" in source, (
        "if this no longer holds, create_document became a real alias — delete this "
        "finding test and add it to the parametrised absorption test above")
    assert "self.create_entity(" not in source, (
        "create_document now delegates to create_entity — the divergence is closed")


def test_create_entity_still_refuses_a_type_it_does_not_absorb(monkeypatch):
    """`create_point` is NOT `type=`-routable — a Point is another layer, not another kind.

    Recorded rather than silently accepted: the absorption ledger's `create_point` row is
    not reachable through this door, and a merge that swallowed it would either have to
    grow the door's accepted types (a behaviour change) or lie about the mapping.
    """
    real = TortoiseSDK.create_entity
    with pytest.raises(ValueError, match="unknown type"):
        real(_sdk(), "point", "a claim")


# ── 2b. link_entities dispatches on the relation ─────────────────────────────


@pytest.mark.parametrize(
    ("relation", "door", "check"),
    [
        # The operator-less half of the epistemic layer: a bare IMPL/NAND edge.
        ("IMPL", "create_direct_edge", lambda a, k: a[0] == "IMPL"),
        ("nand", "create_direct_edge", lambda a, k: a[0] == "NAND"),
        # The derivation writer owns the direction — the caller's is not used,
        # and the arm passes the two ids through POSITIONALLY: `link_entities`
        # documents the ontology's `(dst)-[:wasDerivedFrom]->(src)` orientation
        # rather than silently re-ordering the caller's arguments (#10053 review).
        ("wasDerivedFrom", "create_derivation", lambda a, k: a == ("src", "dst")),
        # A structural relation stays PLAIN (ontology v3.5 §8) even though
        # `ownedBy` is in CORE_OPERATOR_LABELS — that set mixes the two vocabularies.
        ("ownedBy", "create_edge", lambda a, k: a[0] == "ownedBy"),
        ("hasPart", "create_edge", lambda a, k: a[0] == "hasPart"),
        # Point↔Point support/contradict is the ontology's operator case — and
        # the relation is a MEANING, so `op_type` carries the MECHANISM the
        # registry fixes for it, never the meaning itself (#10053).
        ("supports", "create_operator",
         lambda a, k: a[0] == "IMPL" and k["semantics"] == "supports"),
        ("refutes", "create_operator",
         lambda a, k: a[0] == "NAND" and k["semantics"] == "refutes"),
        ("mutuallySupportive", "create_operator",
         lambda a, k: a[0] == "IMPL" and k["semantics"] == "mutuallySupportive"),
        # The caller's casing is not the vocabulary's: `mutuallyExclusive` is
        # camelCase and `create_operator` matches it exactly.
        ("MUTUALLYEXCLUSIVE", "create_operator",
         lambda a, k: a[0] == "NAND" and k["semantics"] == "mutuallyExclusive"),
    ],
)
def test_link_entities_routes_each_relation_to_the_door_it_absorbed(
        sdk, relation, door, check):
    sdk.link_entities(relation, "src", "dst")
    got, args, kwargs = sdk.calls[-1]
    assert got == door, f"{relation} must route to {door}, got {got}"
    assert check(args, kwargs), f"{relation} → {door} got unexpected arguments {args}"


def test_link_entities_builds_an_operator_when_the_caller_asks_for_one(sdk):
    """IMPL is the one relation with two legitimate storages — `operator=True` is the override."""
    sdk.link_entities("IMPL", "src", "dst", operator=True)
    assert sdk.calls[-1][0] == "create_operator"


def test_link_entities_does_not_mint_an_unanchored_references_edge_when_the_label_is_given(sdk):
    """Naming the entity label selects the PROVENANCE writer, which stamps `sourceVersion`.

    The bare `create_edge` path mints a `references` edge with no anchor and nothing adds
    one later (see `create_edge`'s docstring); the merge must not be the thing that routes
    a caller into that hole when they asked for the anchored write.
    """
    sdk.link_entities("references", "https://ex/s", "e1", entity_label="Event")
    door, _args, kwargs = sdk.calls[-1]
    assert door == "link_source_to_entity"
    assert kwargs["source_url"] == "https://ex/s" and kwargs["entity_id"] == "e1"


def test_link_entities_refuses_an_empty_relation(sdk):
    with pytest.raises(ValueError, match="relation is required"):
        sdk.link_entities("", "src", "dst")


# ── 2b-bis. the support/contradict FAMILY routes by MEANING (#10053 P0) ──────
#
# The arm used to dispatch these spellings STRAIGHT into `create_operator`'s
# `op_type`, whose accepted set is IMPL/NAND/the part-whole kinds — so every one
# of them raised `ValueError` and the merge's central claim never executed
# (measured: the P0 finding on #10053). The meaning belongs in `semantics=` and
# the MECHANISM comes from `pack_registry.SEMANTICS_MECHANISMS`, which is READ
# here — never re-transcribed, because a local copy is exactly how the two slots
# drifted apart.

#: (relation the caller spells, the MEANING it names, the MECHANISM that meaning fixes).
EPISTEMIC_RELATIONS = (
    ("supports", "supports", "IMPL"),
    ("SUPPORTS", "supports", "IMPL"),
    ("refutes", "refutes", "NAND"),
    ("REFUTES", "refutes", "NAND"),
    ("mutuallySupportive", "mutuallySupportive", "IMPL"),
    ("MUTUALLYSUPPORTIVE", "mutuallySupportive", "IMPL"),
    ("mutuallyExclusive", "mutuallyExclusive", "NAND"),
    ("MUTUALLYEXCLUSIVE", "mutuallyExclusive", "NAND"),
)

#: The spellings the arm used to CLAIM. ONTOLOGY §8 gives them no meaning, so no
#: mechanism exists for them: they must be refused BY NAME, and the error must
#: name the documented spelling (`CONTRADICTS` is §8's PROSE for NAND, ambiguous
#: between the directed and the mutual reading — so it cannot pick one).
UNMAPPED_EPISTEMIC_SPELLINGS = (
    ("SUPPORT", "supports"),
    ("support", "supports"),
    ("CONTRADICTS", "refutes"),
    ("contradicts", "refutes"),
)


@pytest.mark.parametrize(("relation", "meaning", "mechanism"), EPISTEMIC_RELATIONS)
def test_link_entities_passes_the_meaning_and_the_mechanism_in_their_own_slots(
        sdk, relation, meaning, mechanism):
    """`semantics=` carries the MEANING, `op_type=` carries the MECHANISM, and they agree."""
    from tortoise.pack_registry import SEMANTICS_MECHANISMS

    sdk.link_entities(relation, "src", "dst")
    door, args, kwargs = sdk.calls[-1]
    assert door == "create_operator", (
        f"{relation} is Point↔Point support/contradict — it must carry an operator, got {door}")
    assert args[0] == mechanism, (
        f"{relation} must resolve to its documented mechanism {mechanism!r}, got {args[0]!r}")
    assert args[0] == SEMANTICS_MECHANISMS[meaning], (
        "the mechanism must come from SEMANTICS_MECHANISMS, not from a local table")
    assert kwargs["semantics"] == meaning, (
        f"{relation} must be recorded as the MEANING {meaning!r}, got {kwargs.get('semantics')!r}")
    assert args[1] == "src" and args[2] == ["dst"]


@pytest.mark.parametrize(("relation", "_meaning", "_mechanism"), EPISTEMIC_RELATIONS)
def test_the_routed_op_type_clears_the_real_create_operator_guard(relation, _meaning, _mechanism):
    """The REAL `create_operator`, not the spy, must not refuse what this arm passes it.

    `create_operator` validates `op_type` BEFORE any graph access, which is what made
    the defect observable with no database — so the same property proves the fix: a
    bare SDK must now get PAST the guard and die on the missing projection instead.
    """
    inst = _sdk()
    with pytest.raises(Exception) as exc:
        TortoiseSDK.link_entities(inst, relation, "src", "dst")
    assert "op_type must be" not in str(exc.value), (
        f"{relation} was still handed to `op_type`: {exc.value}")
    assert "_proj" in str(exc.value), (
        f"{relation} should have reached the write path (graph access), got {exc.value!r}")


@pytest.mark.parametrize(("relation", "documented"), UNMAPPED_EPISTEMIC_SPELLINGS)
def test_a_support_spelling_with_no_documented_meaning_is_refused_by_name(
        sdk, relation, documented):
    """No mechanism is invented, and the relaton is never downgraded to a plain edge."""
    with pytest.raises(ValueError) as exc:
        sdk.link_entities(relation, "src", "dst")
    message = str(exc.value)
    assert relation in message, f"the refusal must name the relation, got {message!r}"
    assert documented in message, (
        f"the refusal must name the documented spelling {documented!r}, got {message!r}")
    assert sdk.calls == [], "a refused relation must not reach a writer"


def test_every_routed_spelling_names_a_meaning_the_ontology_declares():
    """Derived, not transcribed: the arm may only route meanings that declare a mechanism."""
    from tortoise.pack_registry import SEMANTICS_MECHANISMS, SEMANTICS_VALUES

    for meaning in TortoiseSDK._EPISTEMIC_RELATION_MEANINGS.values():
        assert meaning in SEMANTICS_VALUES, f"{meaning!r} is not a Layer-2 meaning"
        assert meaning in SEMANTICS_MECHANISMS, (
            f"{meaning!r} is routed by link_entities but declares no mechanism — the arm "
            "would have nothing to put in op_type")


def test_the_refused_spellings_are_not_silently_mapped_to_a_meaning():
    """The two refusals stay refusals: no alias table crept in for them."""
    for relation, _documented in UNMAPPED_EPISTEMIC_SPELLINGS:
        assert relation.upper() in TortoiseSDK._EPISTEMIC_SPELLINGS_WITHOUT_A_MEANING
        assert relation.upper() not in TortoiseSDK._EPISTEMIC_RELATION_MEANINGS


# ── 2c. update_knowledge — the revise verb, with retraction as a FIELD ───────


def test_update_knowledge_is_the_door_update_lives_in(sdk):
    sdk.update_knowledge("p1", content="new text")
    door, args, kwargs = sdk.calls[-1]
    assert door == "update"
    assert args[0] == "p1" and kwargs["content"] == "new text"


def test_retraction_is_a_temporal_field_not_a_separate_verb(sdk):
    """`retract_point` is reached through `update_knowledge(valid_until=…)`."""
    sdk.update_knowledge("p1", valid_until="2026-01-01")
    assert sdk.calls[-1][0] == "retract_point"
    sdk.update_knowledge("p1", invalid_at="2026-01-01")
    assert sdk.calls[-1][0] == "retract_point"


def test_a_temporal_field_with_a_successor_is_the_invalidate_arm(sdk):
    """A specific successor replaces the claim → `invalidate_point`, not a bare retract."""
    sdk.update_knowledge("p1", invalid_at="2026-01-01", corrected_by_id="p2")
    door, args, _ = sdk.calls[-1]
    assert door == "invalidate_point" and args == ("p1", "p2")


def test_two_different_window_ends_are_refused_not_resolved_by_precedence(sdk):
    """`valid_until` and `invalid_at` are ONE field: two values are a conflict.

    Before this refusal `update_knowledge(p, valid_until=A, invalid_at=B)`
    returned success having written B and dropped A — the "a call that stored
    only part of what it said" class this verb refuses everywhere else (the
    annotation arm, the temporal-beside-props arm). Two ends are both
    recordable, so the refusal is about the CONFLICT, not about an arm that
    cannot carry it.
    """
    with pytest.raises(ValueError, match="two different window ends"):
        sdk.update_knowledge("p1", valid_until="2026-01-01", invalid_at="2026-06-01")
    assert sdk.calls == [], "a refused conflict must not fall through to a write"


def test_two_equal_window_ends_are_one_field_and_pass_through(sdk):
    """Agreement is not a conflict: the same instant spelled twice is one end."""
    sdk.update_knowledge("p1", valid_until="2026-01-01", invalid_at="2026-01-01")
    door, args, kwargs = sdk.calls[-1]
    assert door == "retract_point" and args == ("p1",)
    assert kwargs["valid_to"] == "2026-01-01", (
        "an agreeing pair must reach the writer as the caller's instant, "
        f"not be dropped: {kwargs!r}")


def test_annotating_a_link_is_an_update_on_it(sdk):
    sdk.update_knowledge("op1", annotation={
        "bias": 0.1, "precision": 0.2, "consistency": 0.3, "directness": 0.4})
    door, args, kwargs = sdk.calls[-1]
    assert door == "annotate_operator" and args[0] == "op1"
    assert kwargs["precision"] == 0.2


def test_a_successor_without_a_temporal_field_is_refused_not_silently_dropped(sdk):
    with pytest.raises(ValueError, match="needs a temporal field"):
        sdk.update_knowledge("p1", corrected_by_id="p2")
    assert sdk.calls == [], "a refusal must not fall through to a write"


# ── 2d. delete_knowledge — one removal verb ─────────────────────────────────


def test_delete_knowledge_is_the_door_delete_lives_in(sdk):
    """`delete(name)` already resolves the label (Point → delete_point, entity →
    delete_entity), so the merge must DELEGATE rather than re-implement the branch."""
    sdk.delete_knowledge("p1")
    door, args, _ = sdk.calls[-1]
    assert door == "delete" and args == ("p1",)


def test_delete_knowledge_keeps_the_two_arms_the_narrow_doors_had(monkeypatch):
    """Behaviour preserved: the label split still happens, inside `delete`."""
    inst = _sdk()
    monkeypatch.setattr(
        inst, "_get_proj", lambda: type("P", (), {
            "_resolve_entity": lambda self, i, **k: [{"label": "Point"}],
        })())
    monkeypatch.setattr(inst, "delete_point", lambda i: "point-arm")
    monkeypatch.setattr(inst, "delete_entity", lambda i: "entity-arm")
    assert TortoiseSDK.delete_knowledge(inst, "x") == "point-arm"

    monkeypatch.setattr(
        inst, "_get_proj", lambda: type("P", (), {
            "_resolve_entity": lambda self, i, **k: [{"label": "Object"}],
        })())
    assert TortoiseSDK.delete_knowledge(inst, "x") == "entity-arm"


# ── 4. the door ACCEPTS what the merge hands it (not just "it was routed") ────
#
# A routing spy proves WHICH door fired; it cannot prove the door accepts the
# arguments, because the spy never runs the writer's own input contract. That gap
# is how `link_entities("supports", …)` shipped with `'SUPPORTS'` as the
# `op_type` of a door whose mechanism set is closed: routed correctly, raised on
# every call, and every assertion here was green (#4282).


class _ReachedTheGraph(RuntimeError):
    """The writer got past its own input contract and tried to touch the graph."""


def _stops_at_the_graph(inst: TortoiseSDK) -> TortoiseSDK:
    """Run the REAL writer and stop it at its first graph access.

    `create_operator` validates `op_type` and resolves `semantics` BEFORE it reads
    the projection, so reaching the graph is proof the arguments were accepted.
    """
    def _boom():
        raise _ReachedTheGraph

    inst._get_proj = _boom  # type: ignore[method-assign]
    return inst


@pytest.mark.parametrize("relation", [
    "supports", "refutes", "mutuallySupportive", "mutuallyExclusive",
])
def test_link_entities_hands_the_operator_door_arguments_it_accepts(relation):
    inst = _stops_at_the_graph(_sdk())
    with pytest.raises(_ReachedTheGraph):
        TortoiseSDK.link_entities(inst, relation, "p1", "p2")


def test_the_operator_door_really_does_refuse_the_uppercased_word():
    """The contradiction that proves the test above is not vacuous.

    This is the pre-fix argument, verbatim. It raises inside the door, so a merge
    passing it could never have written anything.
    """
    with pytest.raises(ValueError, match="op_type must be"):
        TortoiseSDK.create_operator(_sdk(), "SUPPORTS", "p1", ["p2"])


@pytest.mark.parametrize("mechanism", ["IMPL", "NAND", "composedOf",
                                       "decomposesInto", "contains", "wraps"])
def test_every_mechanism_the_merge_can_pick_is_accepted_by_the_door(mechanism):
    """The operator door's mechanism set is closed — every member is accepted."""
    inst = _stops_at_the_graph(_sdk())
    with pytest.raises(_ReachedTheGraph):
        TortoiseSDK.create_operator(inst, mechanism, "p1", ["p2"])


def test_operator_true_on_a_relation_with_no_mechanism_is_refused():
    """`related` declares no mechanism, so the door refuses the word as one."""
    inst = _sdk()
    with pytest.raises(ValueError, match="op_type must be"):
        TortoiseSDK.link_entities(inst, "related", "p1", "p2", operator=True)


# ── 5. no arm may accept a parameter it cannot RECORD ────────────────────────


@pytest.mark.parametrize(("relation", "kwargs", "door"), [
    ("wasDerivedFrom", {"confidence": 0.8}, "create_derivation"),
    ("wasDerivedFrom", {"promote_source": False}, "create_derivation"),
    ("hasPart", {"confidence": 0.8}, "create_edge"),
    ("hasPart", {"label": "part-of"}, "create_edge"),
    ("hasPart", {"direction": "->"}, "create_edge"),
    ("supports", {"weight": 0.5}, "create_operator"),
    ("supports", {"batch_id": "b1"}, "create_operator"),
    ("references", {"entity_label": "Event", "label": "cited"}, "link_source_to_entity"),
    ("references", {"entity_label": "Event", "confidence": 0.4}, "link_source_to_entity"),
    # #10053 review (P2): `entity_label` / `source_kind` belong to the PROVENANCE arm
    # alone. They were absent from `requested`, so every other arm accepted and
    # ignored them — `link_entities("hasPart", a, b, entity_label="Event")`
    # returned success for a plain edge with the label dropped.
    ("hasPart", {"entity_label": "Event"}, "create_edge"),
    ("hasPart", {"source_kind": "document"}, "create_edge"),
    ("wasDerivedFrom", {"entity_label": "Event"}, "create_derivation"),
    ("supports", {"source_kind": "document"}, "create_operator"),
    ("IMPL", {"entity_label": "Event"}, "create_direct_edge"),
])
def test_link_entities_refuses_a_parameter_the_chosen_arm_cannot_record(
        sdk, relation, kwargs, door):
    """Accept-and-drop is the fail-silent class: success returned, nothing stored."""
    with pytest.raises(ValueError, match=f"`{door}`, which records no"):
        sdk.link_entities(relation, "src", "dst", **kwargs)
    assert sdk.calls == [], "a refusal must not fall through to a write"


def test_the_direct_edge_arm_still_records_every_attribute_it_can(sdk):
    """The refusal above is scoped to the arms that CANNOT store the value."""
    sdk.link_entities("IMPL", "src", "dst", confidence=0.8, weight=0.5,
                      label="bearson", batch_id="b1", direction="->")
    door, _args, kwargs = sdk.calls[-1]
    assert door == "create_direct_edge"
    assert kwargs["confidence"] == 0.8 and kwargs["weight"] == 0.5
    assert kwargs["label"] == "bearson" and kwargs["batch_id"] == "b1"
    assert kwargs["direction"] == "->"


def test_the_operator_arm_still_records_the_attributes_it_can(sdk):
    sdk.link_entities("supports", "src", "dst", label="backed", direction="->")
    door, _args, kwargs = sdk.calls[-1]
    assert door == "create_operator"
    assert kwargs["label"] == "backed" and kwargs["direction"] == "->"


def test_the_provenance_arm_still_records_the_label_and_the_kind(sdk):
    """The refusal above is scoped: the one arm with a slot for them still gets them.

    `source_kind` defaults to `None` in the signature (the "not asked" sentinel that
    makes the refusal possible at all), so the arm must resolve it — the narrow door's
    own default is `"document"`, and dropping it to `None` here would change what the
    provenance writer records.
    """
    sdk.link_entities("references", "https://x", "e1", entity_label="Event")
    door, _args, kwargs = sdk.calls[-1]
    assert door == "link_source_to_entity"
    assert kwargs["entity_label"] == "Event"
    assert kwargs["source_kind"] == "document", (
        "the arm must resolve the not-asked sentinel to the writer's own default")

    sdk.calls.clear()
    sdk.link_entities("references", "https://x", "e1", entity_label="Person",
                      source_kind="profile")
    assert sdk.calls[-1][2] == {"source_url": "https://x", "entity_id": "e1",
                                "entity_label": "Person", "source_kind": "profile"}


# ── 6. the temporal FIELD's value is the datum, not just the branch selector ──


def test_the_retraction_instant_reaches_the_writer(sdk):
    """#4282: `valid_until=…` used to be tested and thrown away."""
    sdk.update_knowledge("p1", valid_until="2026-01-01")
    door, _args, kwargs = sdk.calls[-1]
    assert door == "retract_point" and kwargs["valid_to"] == "2026-01-01"

    sdk.update_knowledge("p1", invalid_at="2026-02-02")
    assert sdk.calls[-1][2]["valid_to"] == "2026-02-02"


def test_the_supersede_arms_instant_reaches_the_writer(sdk):
    """#4282: the successor arm stamped `now` and dropped the caller's instant."""
    sdk.update_knowledge("p1", invalid_at="2026-01-01", corrected_by_id="p2")
    door, args, kwargs = sdk.calls[-1]
    assert door == "invalidate_point" and args == ("p1", "p2")
    assert kwargs["valid_to"] == "2026-01-01"


@pytest.mark.parametrize("door", ["retract_point", "invalidate_point"])
def test_the_writers_have_somewhere_to_put_the_callers_instant(door):
    """The merge may only forward `valid_to` if the writers accept it."""
    import inspect

    assert "valid_to" in inspect.signature(getattr(TortoiseSDK, door)).parameters


def test_the_merge_exposes_the_temporal_field_and_forwards_its_value():
    import inspect

    params = inspect.signature(TortoiseSDK.update_knowledge).parameters
    assert {"valid_until", "invalid_at"} <= set(params), (
        "beta row 22: retraction is `valid_until`/`invalid_at` on the update verb")


def test_the_retraction_record_carries_the_window_end():
    """A window end that is not journalled is lost on the next rebuild.

    This pins the DECLARATION only. Whether the producer actually puts the key on the
    record is a different question, and it is answered by execution in
    `tests/test_4282_dated_retraction_durability.py` — where it was FALSE until
    `retract_point` stopped passing `valid_to` as a change-feed-only `payload=`
    (a declared `from_payload` on a record that never carries the key is exactly the
    vacuous shape this file must not rely on).
    """
    from tortoise.record_projection import RECORD_MUTATIONS

    mutation = RECORD_MUTATIONS["PointRetracted"]
    written = getattr(mutation.effect, "from_payload", frozenset())
    assert "valid_to" in written, (
        "PointRetracted must carry the caller's `valid_to`, or a rebuild replays "
        "the tombstone with no window end while the live node has one")


# ── 6b. one arm per call: a keyword the chosen arm cannot carry is REFUSED ──────
#
# #10053 review (P2). `update_knowledge` has three arms (property bag / annotation /
# temporal window + tombstone) and each RETURNS from its branch, so everything named
# beside it used to be accepted and dropped — the same fail-silent class
# `link_entities`' `_dispatch` already refuses. `link_entities` refuses; this verb now
# does too, in the same `ValueError` class and with the same rule.


def test_the_annotation_arm_refuses_properties_it_cannot_carry(sdk):
    """`annotate_operator` takes the four dimensions and nothing else.

    Before: `update_knowledge(op, content="new text", annotation={…})` annotated the
    link, DISCARDED the content and returned success.
    """
    with pytest.raises(ValueError, match="cannot carry content"):
        sdk.update_knowledge("op1", annotation=_FULL_ANNOTATION, content="new text")
    assert sdk.calls == [], "a refusal must not fall through to a write"


def test_the_annotation_arm_refuses_a_temporal_field_beside_it(sdk):
    """Before: this ANNOTATED and never retracted — discarding `corrected_by_id` too,
    which the same function refuses on its own when no temporal field accompanies it."""
    with pytest.raises(ValueError, match="cannot carry"):
        sdk.update_knowledge("op1", annotation=_FULL_ANNOTATION,
                             invalid_at="2026-01-01", corrected_by_id="p2")
    assert sdk.calls == []


def test_a_temporal_field_refuses_properties_it_cannot_carry(sdk):
    """Before: `update_knowledge(p, content="corrected text", valid_until=…)`
    retracted the point and silently lost the text the caller asked to store."""
    with pytest.raises(ValueError, match="cannot carry content"):
        sdk.update_knowledge("p1", valid_until="2026-01-01", content="corrected text")
    assert sdk.calls == [], "a refusal must not reach a writer"


# ── 7. malformed annotation input is ValueError, like every other refusal ────


@pytest.mark.parametrize("annotation", [
    {"precision": 0.2},                                   # partial → was TypeError
    {"bias": 0.1, "precision": 0.2, "consistency": 0.3, "directness": 0.4,
     "bogus": 1.0},                                       # typo'd key → was TypeError
])
def test_a_malformed_annotation_is_a_ValueError_not_a_TypeError(sdk, annotation):
    with pytest.raises(ValueError, match="annotation takes exactly"):
        sdk.update_knowledge("op1", annotation=annotation)
    assert sdk.calls == [], "a refusal must not fall through to a write"
