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
     place. `test_..._is_the_old_door` pins that by proving the same call reaches the same
     method with the same arguments.
  4. **The refusal arms stay refusals.** A merge must not soften a guard (an unknown type
     still raises; `corrected_by_id` with no temporal field is still an error).

The narrow doors are substituted on the INSTANCE, so no database is needed: these are
routing tests, and routing is what a merge changes. The writers' own behaviour is covered
by their own suites, which this change does not touch.
"""
from __future__ import annotations

import pytest

from tortoise import __all__ as declaration
from tortoise.sdk import TortoiseSDK

#: The four merge targets — `docs/product/beta-sdk-surface.md` rows 12, 19, 22, 24.
MERGES = ("create_entity", "link_entities", "update_knowledge", "delete_knowledge")


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
    assert len(declaration.__all__) == 40, (
        "the approved declaration is 40 names; a merge must not change it")
    for name in MERGES:
        assert name in declaration.__all__, f"{name} must already be approved"
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
        # The derivation writer owns the direction — the caller's is not used.
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
