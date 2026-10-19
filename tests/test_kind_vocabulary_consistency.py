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

* **LAYER-1 GATE** — what the commit write door accepts: ``commit_schema.EVENT_KINDS`` and
  ``commit_schema.CORE_POINT_KINDS``, compiled into ``commit_schema.compile_vocab()``.
* **PACK-FACING** — what a pack author may name: ``pack_registry.CANONICAL_*``. These are the
  ``nearMisses``/``equivalentTo``/chain targets and the ``tortoise pack new`` namespace
  reservation, resolved through ``PackRegistry._resolve_kind_ref()`` over ``CORE_KINDS`` ∪
  every loaded pack's kinds.

A kind the gate accepts but the pack layer cannot name is #2786's **AF-1**, and it is this
module's regression: measured at ``4d9a0ddef`` as
``EVENT_KINDS - CANONICAL_EVENT_KINDS == {AgentSession, humanApproval, occurrence, turn}``
— all four declared core by ONTOLOGY §5, all four unresolvable as a pack kind reference.

Reading ONTOLOGY §5 rather than restating it in Python is deliberate: a hardcoded copy here
would be the same hand-maintained duplication this test exists to catch, one layer over.

**Axes this file does NOT own, on purpose.** ``objectKind`` already has a stronger owner —
``tests/test_pack_kinds.py::TestCanonicalObjectKindAlignment`` asserts
``§5 == CANONICAL_OBJECT_KINDS == extractor_v2.CORE_OBJECT_KEYS`` (a three-way diff, including
a surface this file cannot see), so adding it here would be a second parser of the same block
with no added coverage.
**Named residual, tracked on #2786** (recorded so this file is never read as "the point axis
is consistent"): the point axis carries a deliberate TWO-name exception — ``event`` and
``humanApproval`` are accepted by the Layer-1 gate and are not pack-facing (asserted below,
sized to catch a third). ``event`` is additionally a *live* three-way divergence: the gate
accepts it, ``PackRegistry`` cannot resolve it, and the hosted point-create door silently
rejects it (``known_kinds('pointKind')`` never carried it, because #1013 removed ``event`` as
a point kind). Pinned as a size-1 exception below rather than fixed here: widening the hosted
door's point vocabulary would walk back #1013's option-B ruling, and widening the pack-facing
set would leak the legacy kinds into the extractor's prompt bucket. AF-9 (the CRM bridge mints
a ``commitment`` pointKind that §5 declares nowhere) and the flat legacy registry's cross-axis
overlap in ``domain_loader._BASE_KINDS`` (``incident``/``session``/``meeting`` fold into the
point bucket) are the remaining legs on the issue.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tortoise.commit_schema import CORE_POINT_KINDS, EVENT_KINDS, compile_vocab
from tortoise.pack_registry import (
    CANONICAL_DOCUMENT_KINDS,
    CANONICAL_EVENT_KINDS,
    CANONICAL_POINT_KINDS,
    CORE_KINDS,
    PackRegistry,
    registered_source_types,
)
from tortoise.subject_binding import subject_kind_names
from tortoise.value_extractor import EVENT_KINDS as VALUE_EXTRACTOR_EVENT_KINDS

_DOC = Path(__file__).resolve().parent.parent / "docs" / "ONTOLOGY.md"


def _ontology_axis(heading: str) -> frozenset[str]:
    """The kind names ONTOLOGY §5 declares under ``heading``'s fenced block.

    The §5 blocks are fences whose entries are comma/whitespace separated and may carry a
    trailing ``#`` comment on the same line (``milestone,  # #2767: …``). Both are stripped.
    The fence's info string is dropped too — an UNLABELLED fence contributes an empty line
    and a labelled one its language, and reading either as a kind is the #6927 failure. A
    block that yields no names is an extraction failure, not an empty vocabulary.
    """
    text = _DOC.read_text(encoding="utf-8")
    try:
        tail = text[text.index(heading):]
    except ValueError as e:  # the heading was renamed — the test must not silently skip
        raise AssertionError(
            f"ONTOLOGY.md no longer contains the §5 heading {heading!r}; "
            "update this guard's anchor rather than dropping the axis") from e
    fence = re.search(r"```[A-Za-z]*\n(.*?)```", tail, re.S)
    assert fence is not None, f"no fenced block follows {heading!r}"
    names: set[str] = set()
    for line in fence.group(1).splitlines():
        body = line.split("#", 1)[0]
        names.update(tok for tok in re.split(r"[,\s]+", body.strip()) if tok)
    assert names, f"{heading!r} block parsed to zero kind names"
    return frozenset(names)


#: axis → (ONTOLOGY §5 heading, the code set that must equal it, which layer that set is).
#: ``layer-1`` is the commit write door; ``pack-facing`` is what a pack may name (a vocabulary
#: only a pack reads, so a drift there is invisible to the gate and to every write path).
_DOC_MIRRORS = {
    "eventKind": ("### Event Kind Vocabulary (core)", EVENT_KINDS, "layer-1"),
    "pointKind": ("### Point Kind Vocabulary (core)", CORE_POINT_KINDS, "layer-1"),
    "documentKind": ("### Document Kind Vocabulary", CANONICAL_DOCUMENT_KINDS, "pack-facing"),
}


@pytest.mark.parametrize("axis", sorted(_DOC_MIRRORS))
def test_vocabulary_matches_ontology(axis: str):
    """Each axis accepts exactly what §5 declares — no more, no less.

    ``no more`` matters as much as ``no less``: a kind the code accepts but the doc does not
    declare is one no pack can name and no reader can look up (AF-9's class).
    """
    heading, code, layer = _DOC_MIRRORS[axis]
    declared = _ontology_axis(heading)
    assert code == declared, (
        f"{axis}: the {layer} vocabulary has drifted from ONTOLOGY §5 ({heading}).\n"
        f"  accepted but undeclared: {sorted(code - declared)}\n"
        f"  declared but not accepted: {sorted(declared - code)}")


def test_pack_facing_event_kinds_cover_the_gate():
    """AF-1: every core event kind the gate accepts must be nameable by a pack.

    Two hand-maintained literals mirror §5 on this axis — ``commit_schema.EVENT_KINDS`` (the
    gate) and ``pack_registry.CANONICAL_EVENT_KINDS`` (the pack layer) — and the two are kept
    distinct ON PURPOSE: widening the commit write door must be a deliberate edit of the gate
    file, never a side effect of a vocabulary edit. That separation is only safe with this
    cross-check, which is what makes the duplication reviewable instead of a hazard.
    """
    missing = EVENT_KINDS - CANONICAL_EVENT_KINDS
    assert not missing, (
        "these core event kinds are accepted by the Layer-1 gate but are not in "
        f"the pack-facing CANONICAL_EVENT_KINDS: {sorted(missing)}")
    invented = CANONICAL_EVENT_KINDS - EVENT_KINDS
    assert not invented, (
        "CANONICAL_EVENT_KINDS declares event kinds the runtime gate does not "
        f"accept: {sorted(invented)}")


def test_compiled_gate_vocabulary_carries_every_core_event_kind():
    """The gate the write door ACTUALLY uses, and the ``#2714`` rule that it never gates core.

    ``compile_vocab()`` unions the core legs with each pack's declarations, and the core legs
    must survive a namespace gate that matches NO installed pack — ``compile_vocab``'s own
    contract (#2714/#5163): a graph always accepts core vocabulary, whatever it has installed.
    A change that moved ``EVENT_KINDS``/``CORE_POINT_KINDS`` inside the ``_gate`` branch would
    keep every constant pin above green while the gate stopped accepting §5's kinds.
    """
    vocab = compile_vocab(installed_namespaces=frozenset({"no-such-namespace"}))
    missing_events = sorted(set(EVENT_KINDS) - set(vocab.event_kinds))
    missing_points = sorted(set(CORE_POINT_KINDS) - set(vocab.point_kinds))
    assert not missing_events, (
        f"compile_vocab() gated out these CORE event kinds: {missing_events}")
    assert not missing_points, (
        f"compile_vocab() gated out these CORE point kinds: {missing_points}")


def test_every_doc_core_kind_resolves_as_a_pack_reference(tmp_path):
    """A §5 core kind must be referenceable by a REAL, loaded pack manifest.

    ``_resolve_kind_ref`` resolves ``nearMisses`` / chain / relation references against the
    loaded catalog, and an unresolvable reference produces a validation ERROR that drops the
    pack (``load_all``). That is the exact author gesture #2786's AF-1 broke: the pack was
told a §5 core event kind did not exist. A ``CORE_KINDS`` membership assertion would not
exercise that path (measured: an ``equivalentTo`` target is not resolved at all, so a
manifest-only version of this leg passes for any string), so this writes real manifests and
loads them — and re-loads one with an unknown target to prove the leg is not vacuous.
    """
    manifest = (
        "namespace: acme\nname: Acme\nversion: 0.1.0\ntier: free\n"
        "ontology:\n  extends: core\n  pointKinds: [vocabKind]\n"
        "  kindDefs:\n    vocabKind:\n      description: probe\n"
        "      nearMisses: [{target}]\n")

    def load(target: str) -> tuple[list[str], dict[str, list[str]]]:
        packs_dir = tmp_path / target.replace(":", "_")
        (packs_dir / "acme").mkdir(parents=True)
        (packs_dir / "acme" / "manifest.yaml").write_text(
            manifest.format(target=target), encoding="utf-8")
        registry = PackRegistry(packs_dir)
        registry.load_all()
        return sorted(registry.packs), registry.errors

    for heading in ("### Event Kind Vocabulary (core)",
                    "### Document Kind Vocabulary"):
        for kind in sorted(_ontology_axis(heading)):
            assert kind in CORE_KINDS, kind
            packs, errors = load(kind)
            assert not errors, (kind, errors)
            assert packs == ["acme"], (kind, packs)

    # The leg is not vacuous: the SAME path refuses an unknown reference and drops the pack.
    packs, errors = load("notAKind")
    assert errors and packs == [], (packs, errors)


def test_value_extractor_event_mirror_is_the_gate_set():
    """The THIRD event-kind copy — ``value_extractor.EVENT_KINDS`` — is derived, not copied.

    It had drifted to 15 of §5's 16 names (missing ``milestone``, the #2767 core event) while
    every other leg stayed green: a public constant that disagreed with the gate is a trap even
    though nothing imported it. It now derives from ``commit_schema.EVENT_KINDS``; this leg is
    what makes re-copying it red.
    """
    assert set(VALUE_EXTRACTOR_EVENT_KINDS) == set(EVENT_KINDS), (
        "value_extractor.EVENT_KINDS has re-diverged from the gate's set: "
        f"gate-only={sorted(set(EVENT_KINDS) - set(VALUE_EXTRACTOR_EVENT_KINDS))}, "
        f"mirror-only={sorted(set(VALUE_EXTRACTOR_EVENT_KINDS) - set(EVENT_KINDS))}")


def test_point_axis_runtime_exception_is_exactly_two_names():
    """The point axis's gate-only half is a TWO-name exception, not a blank cheque.

    §5 declares eleven point kinds; ``CANONICAL_POINT_KINDS`` carries the nine that are also
    pack-facing, and ``commit_schema.CORE_POINT_KINDS`` adds ``humanApproval`` (a timeline kind,
    §5) and ``event`` (removed as a point kind by #1013's option-B ruling, kept runtime-accepted
    for write compatibility). Sizing the exception is the point: a third name entering that
    private literal would otherwise stay invisible — the equality leg sees doc == the union and
    the pack leg only inspects events.
    """
    assert CORE_POINT_KINDS - CANONICAL_POINT_KINDS == {"humanApproval", "event"}, (
        "the gate-only point kinds changed; §5 + the #1013 ruling admit exactly two "
        f"(got {sorted(CORE_POINT_KINDS - CANONICAL_POINT_KINDS)})")
    # `event` is the size-1 live divergence: gate-accepted, pack-unresolvable, and rejected by
    # the hosted point-create door. Asserted so it cannot grow silently; see the module docstring.
    from tortoise.domain_loader import known_kinds
    assert "humanApproval" in known_kinds("pointKind")
    assert "event" not in known_kinds("pointKind")


def test_ontology_source_types_are_pack_validatable(tmp_path):
    """#2726, locked in: the pack validator derives from the live source registry.

    The validator used to compare against a hand-copied 5-item literal, so
    ``meeting_summary``/``github_pr``/``agentSession`` were runtime-registered and
    pack-illegal. Reading ``registered_source_types()`` fixed it; a doc-declared source type
    that stopped validating would be that defect returning.
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

    ``subject_kind_names()`` deliberately excludes ``other`` — the NIL bucket is not a bindable
    identity (its own contract in ``subject_binding``) — so the comparison adds it back rather
    than relaxing the doc side.
    """
    declared = _ontology_axis("### Subject Kind Vocabulary (core)")
    # The binder declares keys in the `core:` namespace (its declaration form); the doc spells
    # the bare identity. Compare identities, not spellings.
    binder = {n.split(":", 1)[1] if n.startswith("core:") else n
              for n in subject_kind_names()}
    assert binder | {"other"} == declared, (
        "subject kinds drifted from ONTOLOGY §5: "
        f"binder-only={sorted(binder - declared)}, "
        f"doc-only={sorted(declared - binder - {'other'})}")
