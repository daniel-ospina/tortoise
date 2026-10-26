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
        # Point↔Point support/contradict is the ontology's operator case.
        ("supports", "create_operator", lambda a, k: a[0] == "SUPPORTS"),
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
