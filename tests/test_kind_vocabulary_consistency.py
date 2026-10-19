"""#2786 — one core kind vocabulary per axis, asserted ACROSS the layers that mirror it.

**ONTOLOGY §5 is canonical** ("the doc is canonical" — ``tortoise/pack_registry.py``,
#2727). Two code layers mirror it, and both have drifted from it and from each other —
every instance found by hand, after the fact:

* **#2726** the pack validator read a hand-copied 5-item ``sourceTypes`` literal while the
  runtime registry carried 16, so a registered kind was accepted at write time and rejected
  in a pack manifest;
* **#2767** ``milestone`` sat on the point axis (an owner ruling moved it to events);
* **#7848** ``requirement`` → objectKind and ``deployment`` → event kind (owner rulings),
  which is why ``domain_loader._POINT_KINDS_REMOVED`` exists at all;
* **#6146** a migration table rewrote a stored kind to a name no pack declares, so the read
  path could not reach it.

The layers, and what each one is for:

* **RUNTIME** — what the Layer-1 gate accepts: ``commit_schema.EVENT_KINDS`` /
  ``commit_schema.CORE_POINT_KINDS`` / ``registered_source_types()`` /
  ``subject_binding.subject_kind_names()``.
* **PACK-FACING** — what a pack author may name: ``pack_registry.CANONICAL_*``. These are
  the ``nearMisses``/``equivalentTo``/chain targets and the ``tortoise pack new`` namespace
  reservation, resolved through ``PackRegistry._all_known_kinds()`` (= ``CORE_KINDS`` ∪
  every loaded pack's kinds).

A kind the runtime accepts but the pack layer cannot name is #2786's **AF-1**, and it is
this module's regression: it was measured live at ``4d9a0ddef`` as
``EVENT_KINDS - CANONICAL_EVENT_KINDS == {AgentSession, humanApproval, occurrence, turn}``
— all four declared core by ONTOLOGY §5, all four unresolvable as a pack kind reference.

Reading ONTOLOGY §5 rather than restating it in Python is deliberate: a hardcoded copy here
would be the same hand-maintained duplication this test exists to catch, one layer over.

**Out of scope, tracked on #2786** (named so this file is not read as "the vocabulary is
reconciled"): AF-9 ``commitment`` — the CRM minutes bridge mints a pointKind ONTOLOGY §5
declares nowhere (``integrations/crm/twenty/bridge.py``), so which axis owns it is an open
question, not a drift a test can settle; and the flat legacy registry's cross-axis overlap in
``domain_loader._BASE_KINDS`` (``incident``/``session``/``meeting`` fold into the point
bucket through ``known_kinds('pointKind')``), which is a write-gate behaviour change.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tortoise.commit_schema import CORE_POINT_KINDS, EVENT_KINDS
from tortoise.pack_registry import (
    CANONICAL_DOCUMENT_KINDS,
    CANONICAL_EVENT_KINDS,
    CANONICAL_OBJECT_KINDS,
    PackRegistry,
    registered_source_types,
)
from tortoise.subject_binding import subject_kind_names

_DOC = Path(__file__).resolve().parent.parent / "docs" / "ONTOLOGY.md"


def _ontology_axis(heading: str) -> frozenset[str]:
    """The kind names ONTOLOGY §5 declares under ``heading``'s fenced block.

    The §5 blocks are ```text fences whose entries are comma/whitespace
    separated and may carry a trailing ``#`` comment on the same line
    (``milestone,  # #2767: …``). Both are stripped; a block that yields no
    names is an extraction failure, not an empty vocabulary, so it raises.
    """
    text = _DOC.read_text()
    try:
        tail = text[text.index(heading):]
    except ValueError as e:  # the heading was renamed — the test must not silently skip
        raise AssertionError(
            f"ONTOLOGY.md no longer contains the §5 heading {heading!r}; "
            "update this guard's anchor rather than dropping the axis") from e
    fence = re.search(r"```text\n(.*?)```", tail, re.S)
    assert fence is not None, f"no ```text block follows {heading!r}"
    names: set[str] = set()
    for line in fence.group(1).splitlines():
        body = line.split("#", 1)[0]
        names.update(tok for tok in re.split(r"[,\s]+", body.strip()) if tok)
    assert names, f"{heading!r} block parsed to zero kind names"
    return frozenset(names)


#: axis → (ONTOLOGY §5 heading, the RUNTIME set the Layer-1 gate validates against)
_RUNTIME_AXES = {
    "eventKind": ("### Event Kind Vocabulary (core)", EVENT_KINDS),
    "pointKind": ("### Point Kind Vocabulary (core)", CORE_POINT_KINDS),
    "objectKind": ("### Object Kind Vocabulary (core)", CANONICAL_OBJECT_KINDS),
    "documentKind": ("### Document Kind Vocabulary", CANONICAL_DOCUMENT_KINDS),
}


@pytest.mark.parametrize("axis", sorted(_RUNTIME_AXES))
def test_runtime_vocabulary_matches_ontology(axis: str):
    """The gate accepts exactly what §5 declares — no more, no less.

    ``no more`` matters as much as ``no less``: a kind the runtime accepts but
    the doc does not declare is one no pack can name and no reader can look up
    (AF-9's class).
    """
    heading, runtime = _RUNTIME_AXES[axis]
    declared = _ontology_axis(heading)
    assert runtime == declared, (
        f"{axis}: runtime vocabulary has drifted from ONTOLOGY §5 "
        f"({heading}).\n"
        f"  accepted but undeclared: {sorted(runtime - declared)}\n"
        f"  declared but not accepted: {sorted(declared - runtime)}")


def test_pack_facing_event_kinds_cover_the_runtime_gate():
    """AF-1: every core event kind the gate accepts must be nameable by a pack.

    The pack-facing set is what resolves ``nearMisses``, ``equivalentTo``
    targets and chain steps. A §5 core event kind missing from it is accepted on
    the write path and invisible to pack authoring — measured live (#2786) as
    ``{AgentSession, humanApproval, occurrence, turn}``.
    """
    missing = EVENT_KINDS - CANONICAL_EVENT_KINDS
    assert not missing, (
        "these core event kinds are accepted by the Layer-1 gate but are not in "
        f"the pack-facing CANONICAL_EVENT_KINDS: {sorted(missing)}")
    invented = CANONICAL_EVENT_KINDS - EVENT_KINDS
    assert not invented, (
        "CANONICAL_EVENT_KINDS declares event kinds the runtime gate does not "
        f"accept: {sorted(invented)}")


def test_every_core_kind_resolves_as_a_pack_reference(tmp_path):
    """A §5 core kind must resolve through the pack layer's own resolution set.

    ``PackRegistry._all_known_kinds()`` is what ``_resolve_kind_ref`` consults for
    a ``equivalentTo``/chain target, and what ``expand_kind`` builds its core
    expansion roots from. Asserting on it (rather than on ``expand_kind``) is
    deliberate: ``expand_kind`` falls back to ``[kind]`` for an UNKNOWN kind, which
    is indistinguishable from a known kind that has no subclasses.

    The POINT axis is deliberately absent: ONTOLOGY §5 splits it into the LOGIC
    kind (``statement``, the only extraction write kind) and the ten LEGACY
    write-compat kinds, and only the former half is pack-facing — pushing
    ``humanApproval``/``event`` into ``CANONICAL_POINT_KINDS`` would offer them in
    the extractor prompt and unwind the state-centric (option B) alignment that
    §5 declares. Their gate acceptance is asserted by
    ``test_runtime_vocabulary_matches_ontology[pointKind]`` instead.
    """
    registry = PackRegistry(tmp_path)
    known = registry._all_known_kinds()
    unresolved: dict[str, list[str]] = {}
    for heading in ("### Event Kind Vocabulary (core)",
                    "### Object Kind Vocabulary (core)",
                    "### Document Kind Vocabulary"):
        absent = sorted(_ontology_axis(heading) - known)
        if absent:
            unresolved[heading] = absent
    assert not unresolved, (
        "ONTOLOGY §5 core kinds that the pack layer cannot resolve: "
        f"{unresolved}")


def test_ontology_source_types_are_pack_validatable(tmp_path):
    """#2726, locked in: the pack validator derives from the live source registry.

    The validator used to compare against a hand-copied 5-item literal, so
    ``meeting_summary``/``github_pr``/``agentSession`` were runtime-registered and
    pack-illegal. Reading ``registered_source_types()`` fixed it; a doc-declared
    source type that stopped validating would be that defect returning.
    """
    registry = PackRegistry(tmp_path)
    for source_type in sorted(_ontology_axis("### Source Type Vocabulary")):
        assert source_type in registered_source_types(), (
            f"ONTOLOGY §5 declares source type {source_type!r} but the runtime "
            "registry does not register it")
        errors = registry._validate({
            "namespace": "vocabguard", "name": "Vocab Guard",
            # A kind of this pack's own: the guard is about `extraction.sourceTypes`,
            # and re-declaring a canonical kind would add an unrelated error.
            "ontology": {"extends": "core", "objectKinds": ["vocabGuardNote"]},
            "extraction": {"sourceTypes": [source_type]},
        })
        assert not errors, (source_type, errors)


def test_declared_subject_kinds_match_ontology():
    """The binder's declared §5 Subject kinds match the doc (``other`` aside).

    ``subject_kind_names()`` deliberately excludes ``other`` — the NIL bucket is
    not a bindable identity (its own contract in ``subject_binding``) — so the
    comparison adds it back rather than relaxing the doc side.
    """
    declared = _ontology_axis("### Subject Kind Vocabulary (core)")
    # The binder declares keys in the `core:` namespace (its declaration form);
    # the doc spells the bare identity. Compare identities, not spellings — a
    # blind `split(":")` would be wrong here, but every one of these IS `core:`.
    binder = {n.split(":", 1)[1] if n.startswith("core:") else n
              for n in subject_kind_names()}
    assert binder | {"other"} == declared, (
        "subject kinds drifted from ONTOLOGY §5: "
        f"binder-only={sorted(binder - declared)}, "
        f"doc-only={sorted(declared - binder - {'other'})}")
