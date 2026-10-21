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

**The ``objectKind`` axis: which half this file owns.** The **§5 parse** of that axis is owned
by ``tests/test_pack_kinds.py::TestCanonicalObjectKindAlignment``, which asserts ``§5 ==
CANONICAL_OBJECT_KINDS == extractor_v2.CORE_OBJECT_KEYS`` (a three-way diff over ``§5``,
``CANONICAL_OBJECT_KINDS`` and a surface this file never imports) — parsing the ``§5`` block
here as well would be a second parser of the same block with no added coverage. What this file
DOES assert, at the bottom of it, is the other half: the **derived tooling copies** of that
canonical set (``tools/probe_extractor``, ``tools/calibration_harness``) against
``CANONICAL_OBJECT_KINDS``. So the split is §5-parse **there** vs derived-copies **here**; the
legs at the bottom of this file are deliberately owned by this file and are not covered by
``test_pack_kinds.py``.
**Named residual, tracked on #2786** (recorded so this file is never read as "the point axis
is consistent"): the point axis carries a deliberate TWO-name exception — ``event`` and
``humanApproval`` are accepted by the Layer-1 gate and are not pack-facing (asserted below,
sized to catch a third). ``event`` is additionally a *live* three-way divergence: the gate
accepts it, ``PackRegistry`` cannot resolve it, and the hosted point-create door's DECLARED
layers do not carry it (#1013 removed ``event`` as a point kind) — see the caveat on
``known_kinds('pointKind')`` in that test, which is why the claim is made on the declared
layers rather than on the door's whole allowlist. Pinned as a size-1 exception below rather
than fixed here: widening the hosted door's point vocabulary would walk back #1013's option-B
ruling, and widening the pack-facing set would leak the legacy kinds into the extractor's
prompt bucket. AF-9 (the CRM bridge mints a ``commitment`` pointKind that §5 declares nowhere)
and the flat legacy registry's cross-axis overlap in ``domain_loader._BASE_KINDS``
(``incident``/``session``/``meeting`` fold into the point bucket) are the remaining legs on
the issue.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tortoise.commit_schema import CORE_POINT_KINDS, EVENT_KINDS, compile_vocab
from tortoise.pack_registry import (
    CANONICAL_DOCUMENT_KINDS,
    CANONICAL_EVENT_KINDS,
    CANONICAL_OBJECT_KINDS,
    CANONICAL_POINT_KINDS,
    CORE_KINDS,
    PackRegistry,
    registered_source_types,
)
from tortoise.subject_binding import subject_kind_names
from tortoise.value_extractor import EVENT_KINDS as VALUE_EXTRACTOR_EVENT_KINDS

_ROOT = Path(__file__).resolve().parent.parent
_DOC = _ROOT / "docs" / "ONTOLOGY.md"


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
    assert {"humanApproval", "event"} == CORE_POINT_KINDS - CANONICAL_POINT_KINDS, (
        "the gate-only point kinds changed; §5 + the #1013 ruling admit exactly two "
        f"(got {sorted(CORE_POINT_KINDS - CANONICAL_POINT_KINDS)})")
    # `event` is the size-1 live divergence: gate-accepted and pack-unresolvable. Asserted so
    # it cannot grow silently; see the module docstring.
    from tortoise import domain_loader
    from tortoise.domain_loader import known_kinds

    # `humanApproval` must stay ADMITTED — it is §5's timeline kind and the reason the
    # exception is a pair rather than a single name.
    assert "humanApproval" in known_kinds("pointKind")

    # ⛔ The two legs below are read from the DECLARED layers on purpose, never from
    # `known_kinds("pointKind")`. That function is the hosted point-create door's allowlist,
    # and it is `CORE_KINDS_BY_BUCKET["pointKind"] ∪ _legacy_point_kinds() ∪ pack point kinds`
    # — where the middle term is the PROCESS-GLOBAL, IMPORT-MUTABLE
    # `domain_loader._BASE_KINDS` that `register_kind()` appends to at import time.
    # `tortoise/sdk.py` registers `diary`/`checkpoint-item`/`option`/`evidence`, and
    # `tools/longmem_eval/ingest.py` registers `event`/`session-transcript`; two test modules
    # import that file at COLLECTION time (`tests/test_session_reinjection.py`,
    # `tests/test_coverage_loop.py`), so on a full shard `event` is already in the set before
    # this test runs. A `"event" not in known_kinds("pointKind")` assertion therefore measured
    # WHICH MODULES THE PROCESS IMPORTED, not the vocabulary: it was green file-scoped and red
    # in shard (f) (#9175). The claim itself is unchanged and still guarded — by the two
    # layers a `register_kind()` call cannot reach.

    # (a) The door's CORE layer is the pack-facing nine, NOT the gate's wider
    # `CORE_POINT_KINDS`. Swapping it for the gate set is the one edit that would let `event`
    # (and `humanApproval`) into every request without touching a pack — the regression this
    # pins.
    assert set(domain_loader._CORE_KINDS_BY_BUCKET["pointKind"]) == CANONICAL_POINT_KINDS, (
        "the door's core point layer is no longer the pack-facing set — using the gate's "
        f"wider CORE_POINT_KINDS admits {sorted(CORE_POINT_KINDS - CANONICAL_POINT_KINDS)} "
        "across the whole point axis (#1013 option B)")

    # (b) No PACK may declare `event` as a point kind either: that would reach the door
    # through the pack term and leak an episodic kind into the point axis.
    assert "event" not in domain_loader._pack_kinds_by_bucket()["pointKind"], (
        "a pack now declares `event` as a point kind — #1013's option-B ruling retired it "
        "from this axis; drop it from the pack's `pointKinds`")
    assert "event" not in CANONICAL_POINT_KINDS


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


# ── the TOOLING copies: the extraction probe and the calibration harness ────
#
# Indicator 1 is "for each kind category there is exactly one canonical set, or a test asserts
# `runtime_set == canonical_set`". #9146 landed that leg for the GATE and the PACK-FACING
# layers; two MORE copies live under `tools/`, and neither was asserted. They are not inert
# prose: `probe_extractor` is the classification prompt the extraction probe PUTS IN FRONT OF
# THE MODEL, and `calibration_harness.VOCAB` is the set its `minted` metric treats as
# in-vocabulary — so a retired kind in either one is a kind the model is instructed to emit and
# the measurement then scores as CORRECT. The leg that was supposed to catch this
# (`test_calibration_tools::test_probe_vocab_is_subset_of_packs_and_core`) compared the harness
# copy against a second hand copy of the same list, so both sides carried the same stale
# entries and it could not fire ON THAT DRIFT — a guard that agrees with itself on the drift
# they share. Its other arm (`or kind in pack_kinds`) was already live, built from
# `packs/*/manifest.yaml`, so it still caught a kind absent from BOTH sets and a pack-side
# rename; only the shared copy stayed invisible.
#
# The fixture is the canonical set itself, never a transcription of it.


def _tool_module(name: str):
    """Import ``tools/<name>.py`` the way the harness does (``tools/`` on the path)."""
    import sys

    tools = str(_ROOT / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    return __import__(name)


def _prompt_core_kinds(prompt: str) -> set[str]:
    """Every ``core:<kind>`` spelling the prompt instructs the model with."""
    return {tok.split(":", 1)[1]
            for tok in re.findall(r"\bcore:[A-Za-z_][A-Za-z0-9_]*", prompt)}


def test_extraction_probe_object_kinds_are_the_canonical_set():
    """``probe_extractor.CORE_OBJECT_KINDS`` is the canonical set, not a fourth copy.

    Measured before this leg existed: the probe advertised ``document`` (retired as an
    ``objectKind`` by D10 — a document is a ``:Source``, ONTOLOGY §4.4) and ``concept``
    (never a §5 ``objectKind``; removed from the extractor brief by T12/#1272), while OMITTING
    five kinds §5 declares — ``Problem``, ``strategy``,
    ``plan``, ``goal``, ``target``. Both directions are wrong in the same way: the probe could
    not name a canonical core kind and could name two that resolve nowhere.
    """
    pe = _tool_module("probe_extractor")
    probe = {k.strip() for k in pe.CORE_OBJECT_KINDS.split(",") if k.strip()}
    assert probe == set(CANONICAL_OBJECT_KINDS), (
        "probe_extractor.CORE_OBJECT_KINDS drifted from CANONICAL_OBJECT_KINDS: "
        f"probe-only={sorted(probe - set(CANONICAL_OBJECT_KINDS))}, "
        f"canonical-only={sorted(set(CANONICAL_OBJECT_KINDS) - probe)}")


def test_extraction_probe_semantics_are_canonical_kinds():
    """The probe's kind→semantics table may describe only kinds the vocabulary declares.

    A description is an INSTRUCTION ("emit this when…"), so a stale entry does not sit there
    harmlessly — it asks the model for a kind no read path can resolve. The table is also
    allowed to describe a SUBSET (it carries the confusable-kind hints, not the whole axis), so
    the assertion is ``⊆``, one-directional on purpose.
    """
    pe = _tool_module("probe_extractor")
    stale = set(pe.CORE_SEMANTICS) - set(CANONICAL_OBJECT_KINDS)
    assert not stale, (
        "probe_extractor.CORE_SEMANTICS describes kinds that are not canonical — the probe "
        f"instructs the model to emit them: {sorted(stale)}")


def test_extraction_prompt_advertises_only_canonical_core_kinds():
    """The BUILT prompt — where the copies actually reach the model — names no stale kind.

    The two constants above are the source; this is the surface. Reading the assembled system
    prompt (rather than either constant) is what makes the leg catch a stale mention in the
    prompt's own prose: the ``core:concept`` instruction lived in TWO places — the
    ``CORE_SEMANTICS`` entry AND the confusable-kind guard paragraph — so a fix to the table
    alone would have left the paragraph's mentions in the built prompt (measured: restoring the
    paragraph alone re-fails this leg).
    """
    pe = _tool_module("probe_extractor")
    prompt = pe._build_extraction_system(_ROOT / "packs")
    stale = _prompt_core_kinds(prompt) - set(CANONICAL_OBJECT_KINDS)
    assert not stale, (
        "the extraction probe's system prompt instructs the model with non-canonical core "
        f"kinds: {sorted(stale)}")
    # Vacuity control: the prompt really does carry core kinds, so an empty parse cannot pass.
    assert _prompt_core_kinds(prompt), "no `core:<kind>` token found in the prompt at all"


def test_calibration_harness_core_vocabulary_is_the_canonical_set():
    """``calibration_harness.VOCAB``'s ``core:`` half is exactly the canonical set.

    ``metrics()`` counts an entity whose kind is NOT in ``VOCAB`` as ``minted`` — so a stale
    member does not merely fail to catch drift, it ABSORBS it: an extractor emitting the
    retired ``core:document`` was scored as in-vocabulary. Measured before this leg existed:
    the ``core:`` half carried ``concept``/``document`` and omitted ``Problem``.
    """
    ch = _tool_module("calibration_harness")
    core = {k.split(":", 1)[1] for k in ch.VOCAB if k.startswith("core:")}
    assert core == set(CANONICAL_OBJECT_KINDS), (
        "calibration_harness.VOCAB's core: half drifted from CANONICAL_OBJECT_KINDS: "
        f"harness-only={sorted(core - set(CANONICAL_OBJECT_KINDS))}, "
        f"canonical-only={sorted(set(CANONICAL_OBJECT_KINDS) - core)}")


def test_extraction_probe_described_subset_is_sized():
    """The described-subset leg is TWO-directional: the exception is named and sized.

    ``CORE_SEMANTICS`` is the only core vocabulary the prompt's menu renders, and it is
    deliberately a SUBSET (a canonical kind with no confusable-kind hint is left undescribed).
    That makes a one-directional "no stale key" assertion a hole in the direction that matters
    most here: DELETING a description narrows what the model is offered, and no test would
    fire. So the undescribed remainder is pinned by NAME (the same sized-exception idiom the
    point axis uses above), which makes both edits — a description dropped, a canonical kind
    added — a failing test rather than a quiet drift.

    Completing the descriptions is rubric authoring for the calibration lane; this leg only
    guarantees the shape of the gap cannot move without review.
    """
    pe = _tool_module("probe_extractor")
    undescribed = set(CANONICAL_OBJECT_KINDS) - set(pe.CORE_SEMANTICS)
    assert undescribed == set(pe.UNDESCRIBED_CORE_KINDS), (
        "the probe's described core-kind subset moved — `UNDESCRIBED_CORE_KINDS` is the "
        "recorded exception and must be updated in the same edit:\n"
        f"  undescribed but not recorded: {sorted(undescribed - set(pe.UNDESCRIBED_CORE_KINDS))}\n"
        f"  recorded but now described: {sorted(set(pe.UNDESCRIBED_CORE_KINDS) - undescribed)}")


def test_extraction_probe_descriptions_carry_text():
    """A described kind's description is TEXT, not just a surviving key.

    The legs above compare KEY SETS, so they pin which kinds the prompt's menu offers but not
    that the offer says anything: setting ``CORE_SEMANTICS["tool"] = ""`` keeps every one of
    them green, because the key is still canonical, the subset is still sized, and
    ``_build_extraction_system`` still renders the token ``core:tool`` (the parse finds it and
    the vacuity control is satisfied) — while the prompt loses the instruction for that kind
    exactly as if the key had been deleted.

    This is a SEPARATE mutation from the one the sized leg owns, not a restatement of it: the
    sized leg catches a description *dropped* (the key removed — ``tools/probe_extractor.py``'s
    ``UNDESCRIBED_CORE_KINDS`` comment promises that much), and it cannot see an emptied value,
    measured at ``4 passed`` under the mutant above. So the value is asserted here rather than
    left to the key sets.

    Prose that is only whitespace is the same hole (``"  core:tool —    "`` renders an empty
    instruction), so the assertion is on the STRIPPED value.
    """
    pe = _tool_module("probe_extractor")
    empty = sorted(k for k, v in pe.CORE_SEMANTICS.items()
                   if not isinstance(v, str) or not v.strip())
    assert not empty, (
        "probe_extractor.CORE_SEMANTICS carries a description with no text — the prompt renders "
        f"an empty instruction for these kinds: {empty}")
