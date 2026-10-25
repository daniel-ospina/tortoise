"""#9964 — the kind↔layer partition is enforced ONCE, and the kind decides.

MEASURED on ``origin/main@4c797596d`` (2026-10-25), before this file:

    create_object("T3 Acme Holdings", objectKind="core:organization")
        -> {'objectKind': 'core:organization', 'status': 'live', 'warnings': None}
        stored as: [['Object']]
        MATCH (s:Subject {subjectKind:'core:organization'}) -> 0 rows
    create_subject("Widget", subjectKind="core:skill")       # an OBJECT kind
        stored as: [['Subject']]
    create_point("core:deployment", "the deploy happened")   # an EVENT kind
        stored as: [['Point'], 'core:deployment']

The layer is permanent: there is no repair path (#2489 — ``rebuild_all`` does
not repair a pre-existing graph). And the AUTOMATED path produces the first
shape without a human spelling it, because the classifier's entity partition
merges the two entity layers:

    KindClassifier(...).classify_items([{"type": "entity", "text": "the Acme
    Corp leadership team"}]) -> {'kind': 'core:team'}   # a `subjects` section

— a kind the write path then stored as ``:Object``. The classifier partitions
its candidate set by SECTION (``kind_classifier.TYPE_SECTIONS``) and returns a
kind with no layer on it; the door labelled the node from its own ``type``
argument. Two answers to one question, and they disagreed silently.

The contract these tests pin:

* ONE authority answers "which layer declares this kind" (``kind_layers``), and
  the classifier's own partition is derived from it — so a kind the classifier
  can return from a section is ALWAYS a kind its section's layer declares
  (``test_the_classifiers_partition_is_the_one_the_doors_enforce``).
* The two ENTITY layers are re-routed, not refused: an Object kind at the
  subject door (or a Subject kind at the object door) writes the layer its kind
  names, with a ``kind_layer_routed`` advisory. ``#1934``'s warn-not-block is a
  recorded decision, so a REFUSAL here would be a reopen for the owner.
  ⛔ The re-route's authority is the CLASSIFIER's partition plus the §5 Subject
  vocabulary — NOT the write-gate vocabularies. ``objectKind``'s bucket accepts
  the bare form of 56 legacy names, so routing from it re-labelled
  ``create_subject('pi-agent', 'agent')`` (no classifier section; ``agent`` is a
  §5 Object key) into an ``:Object`` and redded two shipped tests. A kind only
  that vocabulary knows is REPORTED and left on the layer the caller asked for.
* A kind that belongs to another AXIS (an Event kind at the Point door, an
  entity kind at the Event door) is NOT re-routed — a Point and an Event are
  different records, and §5 declares ``decision``/``humanApproval`` on BOTH
  axes (#7813). The write lands as asked and the caller is TOLD
  (``kind_other_layer``), which is the half ``_validate_kind`` never did: it
  warned ``_logger``, not the caller.
* The §5 overlap (``goal``/``plan``/``strategy``/``target`` on Point AND
  Object, ``decision``/``humanApproval`` on Point AND Event) is the MODEL, not
  residue (#7813 owner ruling, #7853) — a write on either layer is correct and
  must never be "corrected".
* ``other``/``core:other`` asserts nothing about the layer (it is the fold's
  ON CREATE default on every entity label), and an UNDECLARED kind is the open
  vocabulary — both stay silent. A NIL kind is also **not a vote**: it cannot
  veto a sibling prop that does name a layer (``create_object`` with
  ``subjectKind="other"`` beside an Object kind still re-files).

Runnable embedded:
  TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_kind_layer_partition_9964.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextlib
from pathlib import Path

import pytest

from tortoise.extractor_v2 import CORE_OBJECT_KEYS, EVENTS, POINTS, SUBJECTS
from tortoise.kind_layers import (
    ENTITY_LAYERS,
    KIND_PROP,
    NIL_KINDS,
    SECTION_LAYERS,
    core_sections,
    declared_layers,
    entity_route,
    section_of,
    undeclared_on,
)
from tortoise.sdk import TortoiseSDK, _kind_layer_note
from tortoise.value_extractor import _clear_kind_spec_cache, compile_kind_index_spec

TEST_GRAPH = "tortoise_test_9964_kind_layer"

#: ONTOLOGY §5's Subject vocabulary — the kinds that must never be stored as an
#: Object, because no Subject read can see them there.
SUBJECT_KINDS = [
    "core:organization", "core:team", "core:role",
    "core:legalPerson", "core:naturalPerson",
]

LAYER_CODES = ("kind_layer_routed", "kind_other_layer")


@pytest.fixture
def sdk(tmp_path):
    events = tmp_path / "events"
    events.mkdir()
    s = TortoiseSDK(str(tmp_path / "kinds.db"), namespace=TEST_GRAPH,
                    event_log_path=str(events / "events.jsonl"))
    yield s
    with contextlib.suppress(Exception):
        s._get_proj().g.query("MATCH (n) DETACH DELETE n")
    s.close()


def _graph(sdk):
    return sdk._get_proj().g


def _node(sdk, node_id):
    """(labels, objectKind, subjectKind, pointKind, eventKind) — read off the graph."""
    rows = _graph(sdk).query(
        "MATCH (n) WHERE n.id=$i RETURN labels(n), n.objectKind, "
        "n.subjectKind, n.pointKind, n.eventKind",
        params={"i": node_id},
    ).result_set
    assert rows, f"no node {node_id!r} in the graph"
    return list(rows[0])


def _layer_notes(result) -> list[dict]:
    return [a for a in (result.get("advisories") or [])
            if a.get("code") in LAYER_CODES]


# ── the issue's named test ────────────────────────────────────────────────

def test_object_door_refuses_a_subject_kind(sdk):
    """`create_object(objectKind=<a §5 Subject kind>)` must not mint a :Object.

    The door does not REFUSE (warn-not-block, #1934) — it writes the layer the
    kind declares, which is the assertion the issue's own snippet makes
    (`assert labels == [["Subject"]]`), and reports the re-route.
    """
    r = sdk.create_object("T3 Acme Holdings", objectKind="core:organization")
    labels, _object_kind, subject_kind, _pk, _ek = _node(sdk, r["id"])
    assert labels == ["Subject"], (
        f"a §5 Subject kind was minted as {labels} — the Subject read can never "
        f"see it (#9964)")
    assert subject_kind == "core:organization"
    notes = _layer_notes(r)
    assert [n["code"] for n in notes] == ["kind_layer_routed"], r.get("advisories")
    assert "Subject" in notes[0]["message"]


def test_subject_door_writes_an_object_kind_as_an_object(sdk):
    """The mirror: `create_subject(subjectKind=<an Object kind>)` is an Object.

    ``core:skill`` is in the classifier's ``objects`` section, so it is an
    Object kind by the ROUTING authority (``kind_layers.routing_layers``).
    """
    r = sdk.create_subject("Widget", subjectKind="core:skill")
    labels, object_kind, _sk, _pk, _ek = _node(sdk, r["id"])
    assert labels == ["Object"], r
    assert object_kind == "core:skill"
    assert [n["code"] for n in _layer_notes(r)] == ["kind_layer_routed"]


def test_a_vocabulary_only_kind_stays_on_the_door_the_caller_chose(sdk):
    """A kind only the WRITE-GATE vocabulary knows is reported, never routed.

    `agent` is an ONTOLOGY §5 Object key with **no classifier section** — it is
    "declared object" only because ``objectKind``'s legacy bucket accepts the
    bare name. Routing that declaration into a label re-labelled an explicitly
    Subject-typed write (``create_subject('pi-agent', 'agent')`` — the door name
    AND the property both say Subject, no classifier section says anything) into
    an ``:Object``, which redded two shipped tests
    (``test_entity_identity.py::…::test_provenance_resolves_id_typed_authored_by``
    and ``test_p1_differentiators.py::…::test_provenance_with_matching_subject``)
    and left `provenance` with no Subject to answer from — #9964 review P1.

    The write LANDS where the caller asked (#1934 warn-not-block) and the
    disagreement is REPORTED, which is the most a vocabulary-only declaration
    can justify.

    ⚠️ About the BARE spelling only (#9964 review P2-2): the namespaced
    ``core:agent`` IS in the classifier's ``objects`` section, so it DOES route
    this same door to ``:Object``. Both answers are correct — the catalog is
    keyed by the namespaced kind, and the bare legacy name reaches only the
    write-gate bucket — and the assertion below is the bare one.
    """
    assert section_of("agent") is None
    assert section_of("core:agent") == "objects", (
        "the caveat in this docstring is what this pin protects: the bare form "
        "has no section, the namespaced one does")
    assert entity_route("agent") is None
    r = sdk.create_subject("pi-agent", subjectKind="agent")
    labels, object_kind, subject_kind, _pk, _ek = _node(sdk, r["id"])
    assert labels == ["Subject"], r
    assert subject_kind == "agent"
    assert object_kind is None
    assert [n["code"] for n in _layer_notes(r)] == ["kind_other_layer"], \
        r.get("advisories")
    # ...and the Subject-side read the door exists for still answers.
    p = sdk.create_point("statement", "A claim", authoredBy=r["id"])
    assert sdk.provenance(p["id"])["subject"] is not None


def test_routing_never_turns_a_legal_write_into_a_refusal(sdk):
    """The re-route must not invent a refusal (#1934 warn-not-block).

    The Object door validates no `status`; the Subject door validates it against
    ONTOLOGY §4.2. So an Object write that names a Subject kind AND an
    Object-only status (`active`) would RAISE if it were re-routed — a refusal
    this fix would have invented. It keeps the layer the caller asked for and is
    reported instead, verbatim: the write lands, nothing is rewritten to fit.
    """
    r = sdk.create_object("Zeta Holdings", objectKind="core:team", status="active")
    labels, object_kind, _sk, _pk, _ek = _node(sdk, r["id"])
    assert labels == ["Object"], r
    assert object_kind == "core:team"
    notes = _layer_notes(r)
    assert [n["code"] for n in notes] == ["kind_other_layer"], r.get("advisories")
    assert "not re-routed" in notes[0]["message"]
    # The same write with a Subject-lifecycle status IS routed — the guard is
    # about the status, not about refusing to route.
    r2 = sdk.create_object("Zeta Live", objectKind="core:team", status="live")
    labels2, _ok2, subject_kind2, _pk2, _ek2 = _node(sdk, r2["id"])
    assert labels2 == ["Subject"], r2
    assert subject_kind2 == "core:team"


def test_no_object_carries_a_subject_kind_through_the_doors(sdk):
    """The issue's indicator 2, object half — written through the SDK doors."""
    for i, kind in enumerate(SUBJECT_KINDS):
        sdk.create_object(f"Acme {i}", objectKind=kind)
    rows = _graph(sdk).query(
        "MATCH (o:Object) WHERE o.objectKind IN $kinds RETURN count(o)",
        params={"kinds": SUBJECT_KINDS},
    ).result_set
    assert rows[0][0] == 0, rows
    # ...and each one IS reachable through the Subject read it belongs to.
    seen = _graph(sdk).query(
        "MATCH (s:Subject) WHERE s.subjectKind IN $kinds RETURN count(s)",
        params={"kinds": SUBJECT_KINDS},
    ).result_set
    assert seen[0][0] == len(SUBJECT_KINDS), seen


def test_a_nil_kind_does_not_veto_the_kind_that_names_a_layer(sdk):
    """`other` declares NOTHING about the layer, so it cannot veto the kind.

    #9964 review P2. ``_named_layer_kinds`` used to return EVERY layer prop the
    write named, ``other``/``core:other`` included — and ``other`` is the fold's
    ON CREATE default on every entity label, i.e. a value the caller (or a
    schema-filling agent) supplies without asserting anything. The agreement
    test then read ``[None, 'subject']``, refused to route ("never on a coin
    flip"), and landed a §5 Subject kind on an ``:Object`` with only a
    ``kind_other_layer`` note — the permanently-unrepairable shape #2489
    records no repair path for, and the shape ruling **A** says must be
    re-filed (`kind_layers.NIL_KINDS`: ``other`` "asserts nothing about the
    layer, so it never routes and never warns about").

    ⛔ Non-vacuous: pre-fix this write stays ``:Object`` carrying
    ``objectKind='core:organization'`` (measured by reverting the filter).
    """
    r = sdk.create_object("Acme Nil Peer", objectKind="core:organization",
                          subjectKind="other")
    labels, object_kind, subject_kind, _pk, _ek = _node(sdk, r["id"])
    assert labels == ["Subject"], r
    assert subject_kind == "core:organization"
    assert object_kind is None
    assert [n["code"] for n in _layer_notes(r)] == ["kind_layer_routed"], \
        r.get("advisories")
    # The graph-level indicator the issue measures: the write landed on the
    # layer its kind declares, so the Subject read sees it and no Object carries
    # the Subject kind any more.
    assert _graph(sdk).query(
        "MATCH (s:Subject {subjectKind:'core:organization'}) RETURN count(s)"
    ).result_set[0][0] == 1
    assert _graph(sdk).query(
        "MATCH (o:Object) WHERE o.objectKind = 'core:organization' "
        "RETURN count(o)").result_set[0][0] == 0

    # The mirror door, the other NIL spelling: `core:other` is equally silent.
    m = sdk.create_subject("Skill Nil Peer", subjectKind="core:skill",
                           objectKind="core:other")
    labels2, object_kind2, subject_kind2, _pk2, _ek2 = _node(sdk, m["id"])
    assert labels2 == ["Object"], m
    assert object_kind2 == "core:skill"
    assert subject_kind2 is None
    assert [n["code"] for n in _layer_notes(m)] == ["kind_layer_routed"], \
        m.get("advisories")


# ── one partition: the classifier's sections are the door's authority ─────

def test_the_classifiers_partition_is_the_one_the_doors_enforce():
    """The classifier's section and the door's re-route can never disagree.

    ⛔ The earlier version of this test asserted
    ``layer in declared_layers(kind)`` — TRUE BY CONSTRUCTION, and therefore
    inert: ``declared_layers`` derives that layer from the SAME ``section_of``
    over the SAME spec the loop read, so the loop reduced to ``assert x in {x}``
    and could not fail if the doors disagreed with the classifier (measured:
    replace ``_kind_layer_note``'s body with ``return t, None`` — routing AND
    reporting gone — and the old loop still passed; #9964 review).

    What is asserted instead is the ROUTING CONSEQUENCE, over every kind the
    classifier can assign, THROUGH THE DOOR FUNCTION:

    * a kind from a POINT/EVENT section is never entity-routed;
    * a kind from an ENTITY section is never re-routed to the OTHER entity
      layer — the silent disagreement #9964 measured (``core:team`` is a
      ``subjects`` kind an ``type='object'`` write stored as an ``:Object``);
    * a kind whose entity layer is EXCLUSIVE (``entity_route`` is not ``None``)
      IS routed there, and is reported as ``kind_layer_routed``;
    * every other kind keeps the layer the caller asked for and is never
      reported as routed.
    """
    spec = compile_kind_index_spec()
    assert spec["core:team"]["section"] == "subjects"  # the measured case
    routed = 0
    for kind, md in spec.items():
        if kind in NIL_KINDS:
            # ``other`` asserts nothing about the layer: it routes nowhere and
            # warns about nothing (its own silence test pins the door).
            assert declared_layers(kind) == frozenset(), kind
            assert entity_route(kind) is None, kind
            continue
        layer = SECTION_LAYERS[md["section"]]
        route = entity_route(kind)
        if layer not in ENTITY_LAYERS:
            assert route is None, (
                f"{kind!r} is classified from the {md['section']} section, so it "
                f"must never be entity-routed; entity_route said {route!r}")
            continue
        # A §5 overlap kind (``core:goal`` is objects-section AND pointKind) is
        # declared outside the entity partition too, so it is never routed
        # (#7813) — the invariant is "never the OPPOSITE entity layer".
        other = "object" if layer == "subject" else "subject"
        assert route != other, (
            f"the classifier assigns {kind!r} from the {md['section']} section, "
            f"but the door would re-route it to {other} — the silent "
            f"disagreement #9964 measured")
        assert route in (None, layer), (kind, layer, route)
        # ── the DOOR, not the map: a caller who typed the WRONG entity door is
        # either re-labelled to this kind's own layer, or left where they asked.
        asked = other
        door_type, note = _kind_layer_note(asked, {KIND_PROP[asked]: kind})
        assert door_type in (asked, layer), (
            f"the {asked} door moved {kind!r} to {door_type!r}; the classifier "
            f"assigns it from the {md['section']} section")
        if route == layer:
            assert door_type == layer, (
                f"{kind!r} is declared only on the {layer} layer but the "
                f"{asked} door did not route it (#9964)")
            assert note and note["code"] == "kind_layer_routed", (kind, note)
            assert note["actual"] == (layer,), note
            routed += 1
        else:
            assert door_type == asked, (kind, door_type)
            assert note is None or note["code"] == "kind_other_layer", (kind, note)
    assert routed, ("no spec kind re-routes through the door — the assertions "
                    "above cannot fire (non-vacuity guard)")


def test_the_core_key_order_is_a_pinned_stability_convention():
    """The spec's CORE key order is a STABILITY convention — pinned as one.

    ``compile_kind_index_spec`` builds its core entries from ``core_sections()``,
    so that map's insertion order (objects → subjects → points → events) fixes
    the compiled spec's key order. It is worth pinning: a reorder is a gratuitous
    diff in a generated artifact, and ``test_pack_value_declarations_2818``'s
    byte-identity check compiles the spec TWICE and compares the two, so a
    reorder moves BOTH sides together and cannot fire there.

    ⚠️ It is NOT a cache-key dependency, which is what this file (and
    ``core_sections()``'s docstring, and ``value_extractor``'s comment) used to
    claim: ``kind_index.cache_key_for`` serializes with
    ``json.dumps(spec, sort_keys=True, default=str)`` — key order cannot reach
    the digest at all — and ``KindIndex.build`` sorts its kind names, so
    candidate order is sorted too. Measured at this head:
    ``cache_key_for(spec) == cache_key_for({k: spec[k] for k in reversed(spec)})``
    is ``True``. The false mechanism was propagated from the PR body into three
    code comments by a fix cycle instead of being removed; it is corrected here
    (#9964 review).
    """
    from tortoise.kind_index import cache_key_for

    spec = compile_kind_index_spec()
    core = list(core_sections())
    assert list(spec)[:len(core)] == core, (
        "the kind-index spec's core key order changed — the historic order is a "
        "stability convention (see the docstring: this is NOT a cache key)")
    assert [spec[k]["section"] for k in core] == [core_sections()[k] for k in core]
    assert [core_sections()[k] for k in core] == (
        ["objects"] * len(CORE_OBJECT_KEYS)
        + ["subjects"] * len(SUBJECTS)
        + ["points"] * len(POINTS)
        + ["events"] * len(EVENTS)
    ), "the four sections must stay contiguous and in objects→subjects→points→events order"
    # ...and the ORDER is provably not the cache key (the claim this replaces).
    reversed_spec = {k: spec[k] for k in reversed(list(spec))}
    assert cache_key_for(spec) == cache_key_for(reversed_spec), (
        "key order reached the cache key after all — the corrected rationale "
        "in core_sections()'s docstring is wrong")


def test_the_automatic_entity_case_lands_on_the_subject_layer(sdk):
    """`type=entity` + `core:team` (the classifier's measured output) is a Subject.

    The classifier's entity partition spans objects AND subjects, so its answer
    carries the layer: a door whose caller typed "object" must still write the
    Subject the kind names.
    """
    assert entity_route("core:team") == "subject"
    r = sdk.create_entity("object", "the Acme Corp leadership team",
                          objectKind="core:team")
    labels, _ok, subject_kind, _pk, _ek = _node(sdk, r["node"]["id"])
    assert labels == ["Subject"], r["node"]
    assert subject_kind == "core:team"


# ── the §5 overlap is the model, not residue (#7813/#7853) ────────────────

@pytest.mark.parametrize("kind", ["goal", "plan", "strategy", "target"])
def test_the_point_object_overlap_is_not_corrected(sdk, kind):
    """`goal`/`plan`/`strategy`/`target` are Point AND Object kinds by ruling."""
    assert declared_layers(kind) >= {"point", "object"}, declared_layers(kind)
    point = sdk.create_point(kind, f"the {kind} holds")
    assert _node(sdk, point["id"])[0] == ["Point"]
    assert _node(sdk, point["id"])[3] == kind
    assert _layer_notes(point) == [], point.get("advisories")
    obj = sdk.create_object(f"{kind} node", objectKind=kind)
    assert _node(sdk, obj["id"])[0] == ["Object"]
    assert _layer_notes(obj) == [], obj.get("advisories")


@pytest.mark.parametrize("kind", ["decision", "humanApproval"])
def test_the_point_event_overlap_is_not_corrected(sdk, kind):
    """`decision`/`humanApproval` are Point AND Event kinds by ruling."""
    assert declared_layers(kind) >= {"point", "event"}, declared_layers(kind)
    point = sdk.create_point(kind, f"the {kind} was made")
    assert _node(sdk, point["id"])[0] == ["Point"]
    assert _layer_notes(point) == [], point.get("advisories")


# ── cross-axis: reported, never re-routed (warn-not-block, #1934) ────────

def test_an_event_kind_on_the_point_door_lands_with_a_specific_warning(sdk):
    """The write is NOT refused, and the caller now hears what used to be a log line."""
    assert undeclared_on("core:deployment", "point") == ("event",)
    r = sdk.create_point("core:deployment", "the deploy happened")
    assert r.get("id"), "warn-not-block (#1934): the write must land"
    labels, _ok, _sk, point_kind, _ek = _node(sdk, r["id"])
    assert labels == ["Point"] and point_kind == "core:deployment", r
    notes = _layer_notes(r)
    assert [n["code"] for n in notes] == ["kind_other_layer"], r.get("advisories")
    assert "Event" in notes[0]["message"]
    assert "create_event" in notes[0]["message"]


def test_an_entity_kind_on_the_event_door_names_the_entity_door(sdk):
    r = sdk.create_event("mislabeled", "core:organization")
    assert r.get("id"), "warn-not-block (#1934): the write must land"
    assert _node(sdk, r["id"])[0] == ["Event"]
    notes = _layer_notes(r)
    assert [n["code"] for n in notes] == ["kind_other_layer"], r.get("advisories")
    assert "Subject" in notes[0]["message"]
    assert "create_subject" in notes[0]["message"]


# ── the silences: they are what keeps the warning discriminating ─────────

def test_a_declared_kind_on_its_own_layer_is_silent(sdk):
    point = sdk.create_point("statement", "a plain claim")
    assert _layer_notes(point) == [], point.get("advisories")
    # a pack DOCUMENT kind is in the classifier's `objects` section — the same
    # declaration says the object door is its layer, so no warning either.
    obj = sdk.create_object("Runbook", objectKind="dev:runbook")
    assert _layer_notes(obj) == [], obj.get("advisories")


def test_undeclared_nil_and_absent_kinds_are_silent(sdk):
    """The vocabulary is open, and `other` asserts nothing about the layer."""
    for kind in ("analyst", "core:other", "other"):
        node = sdk.create_subject(f"subject {kind}", subjectKind=kind)
        assert _layer_notes(node) == [], (kind, node.get("advisories"))
    bare = sdk.create_object("no kind named")
    assert _layer_notes(bare) == [], bare.get("advisories")


# ── #1934: an ADVISORY must never refuse a write ─────────────────────────

def test_a_malformed_pack_manifest_cannot_refuse_a_write(sdk, tmp_path,
                                                         monkeypatch, caplog):
    """The spec COMPILES the installed manifests, so a broken one raises.

    MEASURED on the pre-fix head with a structurally valid pack directory whose
    manifest carries malformed YAML: `create_object`/`create_point` raised
    `yaml.parser.ParserError` out of the ADVISORY path (`section_of` →
    `kind_index_sections` → `compile_kind_index_spec` → `compile_value_brief`) —
    a hard refusal of a write that lands on `origin/main` (#1934 forbids exactly
    that; its neighbours on the path are deliberately fail-open:
    `domain_loader._get_registry` catches `Exception`, and `create_point`'s
    sibling advisory (#5516) degrades to "no notice").

    ⛔ AND THE MESSAGE MUST NOT OVERSTATE WHAT WAS LOST (#9964 review P2).
    `section_of` is only ONE of the two routing authorities: `routing_layers`
    also reads `declares_subject`, so a §5 Subject kind still routes when the
    catalog cannot compile. The logged text used to say "THE KIND<->LAYER
    NOTICE AND ROUTING WERE SKIPPED for this write: it kept the door's label" —
    false for exactly the case it prints for, and it sent the operator hunting a
    mis-labelled node that was labelled correctly. The `:Subject` assertion
    below and these log assertions must agree, so the message cannot drift back.
    """
    packs = tmp_path / "packs"
    (packs / "dev").mkdir(parents=True)
    src = Path(__file__).resolve().parent.parent / "packs" / "dev" / "manifest.yaml"
    (packs / "dev" / "manifest.yaml").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8")
    (packs / "broken").mkdir()
    (packs / "broken" / "manifest.yaml").write_text(
        "kindDefs: {thing: {kind: object, text: [unclosed, flow\n",
        encoding="utf-8")
    monkeypatch.setenv("TORTOISE_PACKS_DIR", str(packs))
    _clear_kind_spec_cache()
    try:
        obj = sdk.create_object("T3 Acme Holdings", objectKind="core:organization")
        assert obj.get("id"), "the advisory refused an entity write (#1934)"
        point = sdk.create_point("statement", "the deploy happened")
        assert point.get("id"), "the advisory refused a point write (#1934)"
    finally:
        _clear_kind_spec_cache()
    # The advisory degrades to "no section" — the write still lands on the layer
    # the kind declares, exactly as it does with a readable catalog.
    assert _node(sdk, obj["id"])[0] == ["Subject"], obj

    # ── …and the message must say only what is actually lost (#9964 review P2).
    # `core:organization` is a §5 Subject kind, and it ROUTED anyway, above. A
    # message claiming routing was skipped contradicts that assertion, and the
    # operator reading it would chase a node that is in fact correct.
    degraded = [r.getMessage() for r in caplog.records
                if "kind_layers.section_of" in r.getMessage()]
    assert degraded, (
        "the compile failure was silent — the operator never hears about it: "
        f"{[r.getMessage() for r in caplog.records]}")
    for msg in degraded:
        assert "ROUTING WERE" not in msg, msg
        assert "SKIPPED for this write" not in msg, msg
        # It must still name BOTH halves honestly: the half that degraded…
        assert "CATALOG-SECTION half" in msg, msg
        assert "keeps the door's label" in msg, msg
        # …and the authority that survives it, so the reader is not left
        # believing nothing routes.
        assert "declares_subject" in msg, msg
        assert "still routes" in msg, msg


def test_the_mcp_nested_props_shape_is_reported(sdk):
    """The MCP-style `props={…}` shape must carry the advisory too.

    The note used to be computed BEFORE `_coerce_props`, so the nested shape —
    the shape the MCP server itself sends — stored an Event kind on a `:Point`
    with NO advisory (measured pre-fix: `advisories` carried only
    `no_source`). Reading the props AFTER coercion is what makes the reported
    kind the one the fold actually stores.
    """
    r = sdk.create_point("statement", "nested props",
                         props={"pointKind": "core:deployment"})
    assert r.get("id")
    labels, _ok, _sk, point_kind, _ek = _node(sdk, r["id"])
    assert labels == ["Point"] and point_kind == "core:deployment", r
    assert [n["code"] for n in _layer_notes(r)] == ["kind_other_layer"], \
        r.get("advisories")


def test_an_unhashable_status_cannot_crash_the_routing_guard(sdk):
    """The guard exists to avoid a refusal; it must not invent a CRASH.

    `_SUBJECT_STATUS_VALUES` is a frozenset, so membership HASHES its operand —
    an unhashable `status` (a list/dict) raised `TypeError: unhashable type`
    where `origin/main` wrote the node. The Subject door's own validation tests
    `isinstance(…, str)` FIRST; the guard must mirror it (a non-str status is
    not routable, and it is not a crash either).
    """
    r = sdk.create_object("Q3", objectKind="core:team", status=["active"])
    assert r.get("id"), "no write may be refused or crash (#1934)"
    labels, object_kind, _sk, _pk, _ek = _node(sdk, r["id"])
    assert labels == ["Object"], r
    assert object_kind == "core:team"
    # Reported (not re-routed, not crashed): the status is not a Subject status.
    assert [n["code"] for n in _layer_notes(r)] == ["kind_other_layer"], \
        r.get("advisories")


def test_two_disagreeing_spellings_never_drop_the_caller_s_kind(sdk):
    """Two spellings of ONE property disagreeing must be REPORTED, not resolved.

    The fold persists `objectKind` and `object_kind` onto the same property, and
    both route to `subject`. Re-routing had to pick ONE of the two values the
    caller gave, and the survivor was `named[-1]` — i.e. `_KIND_PROP_SPELLINGS`'s
    dict-iteration order, not the caller's canonical spelling (measured:
    `create_object("Q4", objectKind="core:organization", object_kind="core:team")`
    stored `subjectKind='core:team'` and dropped `core:organization` silently).
    This module's contract is "no prop is ever rewritten to fit", so an
    ambiguous write keeps its layer, keeps BOTH spellings, and is reported.
    """
    r = sdk.create_object("Q4", objectKind="core:organization",
                          object_kind="core:team")
    assert r.get("id")
    labels, object_kind, subject_kind, _pk, _ek = _node(sdk, r["id"])
    assert labels == ["Object"], r
    assert object_kind == "core:organization", "the caller's kind was dropped"
    assert subject_kind is None, r
    assert [n["code"] for n in _layer_notes(r)] == ["kind_other_layer"], \
        r.get("advisories")
    # Agreement is still routed — the ambiguity is the disagreement, not the
    # second spelling (the #5475 lesson: a snake-cased kind cannot escape).
    ok = sdk.create_object("Q5", objectKind="core:organization",
                           object_kind="core:organization")
    labels2, _ok2, subject_kind2, _pk2, _ek2 = _node(sdk, ok["id"])
    assert labels2 == ["Subject"], ok
    assert subject_kind2 == "core:organization"
    assert [n["code"] for n in _layer_notes(ok)] == ["kind_layer_routed"]


# ── the re-route's blast radius: label-scoped identity ───────────────────

def test_a_re_route_that_splits_an_existing_entity_names_both_ids(sdk):
    """Routing is lossless only when the other label holds nothing (#9964 review).

    Entity ids are LABEL-scoped (`ids.entity_name_id` hashes
    ``f"{label}:{name}"``), so on a graph that already holds the entity under the
    label the write LEFT — the pre-fix shape this issue exists to describe, which
    #2489 records as never repaired — the re-route mints a SECOND node beside the
    stale one. The advisory asserted "no data was lost" in exactly that case; it
    must name both ids instead, and keep the lossless wording only when the write
    really did not split anything.
    """
    legacy = sdk.create_object("Acme Split")          # no kind → a plain :Object
    assert _node(sdk, legacy["id"])[0] == ["Object"]

    routed = sdk.create_object("Acme Split", objectKind="core:organization")
    assert _node(sdk, routed["id"])[0] == ["Subject"]
    assert routed["id"] != legacy["id"], "ids are label-scoped — the split IS real"
    notes = _layer_notes(routed)
    assert [n["code"] for n in notes] == ["kind_layer_routed"], routed.get("advisories")
    message = notes[0]["message"]
    assert legacy["id"] in message, f"the stale twin is not named: {message}"
    assert routed["id"] in message, f"the new node is not named: {message}"
    assert "no data was lost" not in message, (
        f"the advisory claims losslessness over a graph it just split: {message}")
    # ...and the split is in the GRAPH, not just in the prose.
    assert _graph(sdk).query(
        "MATCH (n {name:'Acme Split'}) RETURN count(n)").result_set[0][0] == 2

    # The lossless case keeps the lossless wording (the branch is the split, not
    # the route) — and names the door of the layer actually written, not the one
    # the caller just used (#9964 review: it rendered `DOOR[requested]`, so
    # `create_object(objectKind='core:organization')` was told "the door that
    # writes a Object explicitly is ``create_object``").
    clean = sdk.create_object("Fresh Holdings", objectKind="core:organization")
    clean_notes = _layer_notes(clean)
    assert [n["code"] for n in clean_notes] == ["kind_layer_routed"]
    assert "no data was lost" in clean_notes[0]["message"], clean_notes[0]
    assert "``create_subject``" in clean_notes[0]["message"], (
        f"the remedy names the wrong door: {clean_notes[0]['message']}")
    assert "``create_object``" not in clean_notes[0]["message"], clean_notes[0]


def test_the_split_advisory_states_a_graph_fact_not_a_causal_one(sdk):
    """"this write created a SECOND node" is FALSE on a retry that created nothing.

    The routed id is DETERMINISTIC (``_entity_name_id("Subject", name)``), so
    re-issuing the very call that first split the entity MERGES onto the node the
    first call minted. The peer probe only asks whether the OTHER label holds the
    name — never whether the routed node is new — so the retry re-reports the
    split, and the pre-fix wording turned that into a causal claim: *"this write
    created a SECOND node"*, false on every retry while the graph fact ("`'Acme'`
    has a node under EACH entity label") stayed true. Retries are the common case
    here (ingest and capture both re-issue writes) and the advisory is the
    statement a caller reconciles from, so the claim is about the GRAPH.
    """
    legacy = sdk.create_object("Acme Retry")            # no kind → a plain :Object
    first = sdk.create_object("Acme Retry", objectKind="core:organization")
    assert _node(sdk, first["id"])[0] == ["Subject"], first

    again = sdk.create_object("Acme Retry", objectKind="core:organization")
    assert again["id"] == first["id"], (
        "the routed id is deterministic — the retry must MERGE onto the node the "
        f"first call minted, not mint a third: {first['id']} vs {again['id']}")
    assert _graph(sdk).query(
        "MATCH (n {name:'Acme Retry'}) RETURN count(n)").result_set[0][0] == 2, (
        "the retry added a node: the graph must hold exactly the stale twin and "
        "the routed one")

    notes = _layer_notes(again)
    assert [n["code"] for n in notes] == ["kind_layer_routed"], again.get("advisories")
    message = notes[0]["message"]
    # The graph fact still holds, and both ids are still named…
    assert "SECOND" in message and "the graph now holds" in message, message
    assert legacy["id"] in message and first["id"] in message, message
    # …but the call did NOT create anything this time.
    assert "this write created" not in message, (
        "the advisory claims THIS write created a node on a retry that created "
        f"nothing — it merged onto {first['id']}: {message}")


def test_ingest_dedup_follows_the_label_the_write_lands_on(sdk):
    """`ingest`'s `existed` probe must ask for the label the WRITE uses (#9964).

    The probe ran on the ITEM's `type` label while the door re-labelled the write
    by the kind, so re-ingesting ``{"type": "object", "objectKind": "core:team"}``
    probed `:Object`, found nothing (the first run minted a `:Subject`) and
    reported **created** while the write MERGED — on every run, forever.

    ⛔ The probe must ask at the DOOR's fold depth, not at depth 0 (#9964 review
    P2). The entity doors fold the item TWICE (`create_subject`/`create_object`
    apply `_coerce_props`, then relay `**props` to `create_entity`, which folds
    again — `_ENTITY_ITEM_FLATTEN_DEPTH`), so a probe reading the RAW item was
    blind to both supported nested spellings while the write still re-labelled
    by the kind. That is why the flat shape and each nested spelling below are
    exercised separately: a depth-1-only fix leaves `props: {props: {kind}}`
    wrong, and the flat case passing is not evidence for either.
    """
    bundle = {"entities": [{"type": "object", "name": "Ingest Team",
                            "objectKind": "core:team"}]}
    first = sdk.ingest(bundle)
    assert first["created"]["entities"] == 1, first
    assert _node(sdk, first["ids"]["entities"][0])[0] == ["Subject"]

    second = sdk.ingest(bundle)
    assert second["created"]["entities"] == 0, (
        f"the probe missed the node the first run created: {second}")
    assert second["deduped"]["entities"] == 1, second
    assert second["ids"]["entities"] == first["ids"]["entities"]

    # The mirror: an Object kind declared on a `type="subject"` item.
    mirror = {"entities": [{"type": "subject", "name": "Ingest Widget",
                            "subjectKind": "core:skill"}]}
    m1 = sdk.ingest(mirror)
    assert m1["created"]["entities"] == 1, m1
    assert _node(sdk, m1["ids"]["entities"][0])[0] == ["Object"]
    m2 = sdk.ingest(mirror)
    assert m2["created"]["entities"] == 0, m2
    assert m2["deduped"]["entities"] == 1, m2
    assert _graph(sdk).query(
        "MATCH (n {name:'Ingest Widget'}) RETURN count(n)").result_set[0][0] == 1

    # ── The NESTED-`props` spellings, at every depth the write door folds
    # (#9964 review P2). The entity doors fold TWICE (`create_subject`/
    # `create_object` apply `_coerce_props`, then relay `**props` to
    # `create_entity`, which folds again — `_ENTITY_ITEM_FLATTEN_DEPTH`), so a
    # probe that reads the RAW item is blind to the kind one level down AND two
    # levels down, while the write still re-labels: `created` is then reported
    # on every run, forever, exactly as the flat shape did before the fix.
    for spelling, nested_props in (
            ("props: {kind}", {"objectKind": "core:team"}),
            ("props: {props: {kind}}", {"props": {"objectKind": "core:team"}})):
        name = f"Ingest Nested {len(spelling)}"

        def _bundle(props=nested_props, name=name):
            # A FRESH bundle per run: `ingest` is allowed to consume the item it
            # is handed, so re-feeding one object would test the probe against
            # residue rather than against the same input twice.
            return {"entities": [{"type": "object", "name": name,
                                  "props": dict(props)}]}

        n1 = sdk.ingest(_bundle())
        assert n1["created"]["entities"] == 1, (spelling, n1)
        assert _node(sdk, n1["ids"]["entities"][0])[0] == ["Subject"], (
            f"{spelling}: the nested kind did not re-label the write, so this "
            f"case has stopped exercising the probe", n1)
        n2 = sdk.ingest(_bundle())
        assert n2["created"]["entities"] == 0, (
            f"{spelling}: the probe missed the node the first run created — the "
            f"write MERGED onto the routed label while the probe read the "
            f"item's own: {n2}")
        assert n2["deduped"]["entities"] == 1, (spelling, n2)
        assert n2["ids"]["entities"] == n1["ids"]["entities"], spelling
        assert _graph(sdk).query(
            "MATCH (n {name:$n}) RETURN count(n)",
            params={"n": name}).result_set[0][0] == 1, (
            f"{spelling}: a second identical ingest minted a twin: {n2}")


def test_ingest_dedup_predicts_the_door_not_an_idealised_door(sdk):
    """The probe must replay the door's PIPELINE, not normalise its fold output.

    The entity aliases BIND their own camel kind param out of the item, fold the
    rest, and then put the param back **through ``named_kind``** — so
    ``objectKind=" core:team "`` reaches the routing authority as ``core:team``
    and the write re-files to ``:Subject``, while the dedup probe (folding only)
    still answered ``object`` and queried ``:Object``: ``created`` on every
    re-ingest, forever (#9964 review P2). The mirror is the same defect on the
    other door — ``subjectKind=" core:skill "`` writes an ``:Object``.

    ⛔ The two CONTROL shapes carry the other half of the finding, and they are
    why the repair is positional rather than a blanket "strip every kind prop":
    the snake spelling and a NESTED ``props`` kind are normalised by NO door on
    this head (the strip happens before ``create_entity`` folds again), so their
    writes stay ``:Object``. A probe that stripped the FOLD's output would
    predict ``:Subject`` for them and re-open the same void in the opposite
    direction — the controls red that fix.
    """
    cases = (
        # (shape, raw item WITHOUT the name, the label the WRITE lands on)
        ("top-level camel subject kind, object door",
         {"type": "object", "objectKind": " core:team "}, "Subject"),
        ("top-level camel object kind, subject door",
         {"type": "subject", "subjectKind": " core:skill "}, "Object"),
        # CONTROLS — normalised by no door, so the write stays on the door's
        # own entity layer and the probe must NOT strip them.
        ("snake spelling (no alias normalises it)",
         {"type": "object", "object_kind": " core:team "}, "Object"),
        ("nested props (strip happens before create_entity folds)",
         {"type": "object", "props": {"objectKind": " core:team "}}, "Object"),
    )
    for i, (shape, raw, landed) in enumerate(cases):
        name = f"Ingest Door Normalised {i}"

        def _bundle(raw=raw, name=name):
            # A FRESH item per run: `ingest` consumes the item it is handed.
            return {"entities": [dict(raw, name=name)]}

        first = sdk.ingest(_bundle())
        assert first["created"]["entities"] == 1, (shape, first)
        assert _node(sdk, first["ids"]["entities"][0])[0] == [landed], (
            f"{shape}: the write landed on {landed}, not where this case "
            f"expects it — the shape no longer exercises the probe", first)

        second = sdk.ingest(_bundle())
        assert second["created"]["entities"] == 0, (
            f"{shape}: the probe queried the wrong entity label — the write "
            f"MERGED onto the {landed} the first run created while the probe "
            f"looked elsewhere, so every re-ingest reports a phantom `created`: "
            f"{second}")
        assert second["deduped"]["entities"] == 1, (shape, second)
        assert second["ids"]["entities"] == first["ids"]["entities"], shape
        assert _graph(sdk).query(
            "MATCH (n {name:$n}) RETURN count(n)",
            params={"n": name}).result_set[0][0] == 1, (
            f"{shape}: a second identical ingest minted a twin: {second}")


def test_the_dedup_hit_keeps_the_layer_advisory(sdk):
    """The DEDUP-hit branch must not drop the layer notice (#9964 review).

    `create_point`'s dedup-hit return attached only `temporal_not_applied`, so
    the SAME call reported `kind_other_layer` on its first run and lost it on the
    re-run — the branch a retry actually takes. Both advisories ride ONE
    `advisories` key, so the merge (not a second `**` splat) is what keeps them.
    """
    first = sdk.create_point("core:deployment", "deploy twice", status="superseded")
    assert [n["code"] for n in _layer_notes(first)] == ["kind_other_layer"], \
        first.get("advisories")
    again = sdk.create_point("core:deployment", "deploy twice",
                             status="superseded", dedup=True,
                             validFrom="2020-01-01T00:00:00Z")
    codes = [n["code"] for n in (again.get("advisories") or [])]
    assert "temporal_not_applied" in codes, again.get("advisories")
    assert "kind_other_layer" in codes, (
        f"the layer notice was dropped by the dedup branch: {again.get('advisories')}")
