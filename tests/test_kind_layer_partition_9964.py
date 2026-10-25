"""#9964 — the kind↔layer partition is enforced ONCE, and the kind decides.

MEASURED on ``origin/main@4c797596d`` (2026-10-25), before this file:

    create_object("T3 Acme Holdings", objectKind="core:organization")
        -> {'objectKind': 'core:organization', 'status': 'live', 'warnings': None}
        stored as: [['Object']]
        MATCH (s:Subject {subjectKind:'core:organization'}) -> 0 rows
    create_subject("Widget", subjectKind="core:product")     # an OBJECT kind
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
  vocabulary — both stay silent.

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
    NIL_KINDS,
    SECTION_LAYERS,
    core_sections,
    declared_layers,
    entity_route,
    undeclared_on,
)
from tortoise.sdk import TortoiseSDK
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
    """The mirror: `create_subject(subjectKind=<an Object kind>)` is an Object."""
    r = sdk.create_subject("Widget", subjectKind="core:product")
    labels, object_kind, _sk, _pk, _ek = _node(sdk, r["id"])
    assert labels == ["Object"], r
    assert object_kind == "core:product"
    assert [n["code"] for n in _layer_notes(r)] == ["kind_layer_routed"]


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


# ── one partition: the classifier's sections are the door's authority ─────

def test_the_classifiers_partition_is_the_one_the_doors_enforce():
    """Every kind the classifier can assign is declared on its OWN section's layer.

    This is the invariant that makes the two layers unable to disagree: the
    classifier restricts candidates per section (``TYPE_SECTIONS``) and returns
    the winning kind; the door asks ``kind_layers`` which layer declares it. If
    the section's layer were not among them, a classifier assignment could be
    re-routed (or warned about) by the write — the exact silent disagreement
    #9964 measured.
    """
    spec = compile_kind_index_spec()
    assert spec["core:team"]["section"] == "subjects"  # the measured case
    # ── #2818: the CORE key order is the classifier's partition AND the npz
    # cache key. `compile_kind_index_spec` now builds these entries from
    # `core_sections()` — whose INSERTION ORDER is deliberately objects →
    # subjects → points → events — so a reorder (or a member moving group)
    # rotates `kind_index.cache_key_for`'s hash and silently re-embeds the whole
    # kind index. `test_pack_value_declarations_2818` compiles the spec twice
    # and compares the two, which cannot see this: a reorder moves BOTH sides
    # together. Pin the order itself, against the ONE authority.
    core = list(core_sections())
    assert list(spec)[:len(core)] == core, (
        "the kind-index spec's core key order changed — that is the npz cache "
        "key (#2818): every graph silently re-embeds the index")
    assert [spec[k]["section"] for k in core] == [core_sections()[k] for k in core]
    assert [core_sections()[k] for k in core] == (
        ["objects"] * len(CORE_OBJECT_KEYS)
        + ["subjects"] * len(SUBJECTS)
        + ["points"] * len(POINTS)
        + ["events"] * len(EVENTS)
    ), "the four sections must stay contiguous and in objects→subjects→points→events order"
    disagreements = []
    for kind, md in spec.items():
        layer = SECTION_LAYERS[md["section"]]
        if kind in NIL_KINDS:
            # `other` asserts nothing about the layer: it routes nowhere and
            # warns about nothing (covered by the silence test below), so it
            # cannot disagree with a section.
            assert declared_layers(kind) == frozenset(), kind
            continue
        if layer not in declared_layers(kind):
            disagreements.append((kind, md["section"], sorted(declared_layers(kind))))
    assert not disagreements, (
        "the classifier can assign a kind its own section's layer does not "
        f"declare: {disagreements[:5]}")


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

def test_a_malformed_pack_manifest_cannot_refuse_a_write(sdk, tmp_path, monkeypatch):
    """The spec COMPILES the installed manifests, so a broken one raises.

    MEASURED on the pre-fix head with a structurally valid pack directory whose
    manifest carries malformed YAML: `create_object`/`create_point` raised
    `yaml.parser.ParserError` out of the ADVISORY path (`section_of` →
    `kind_index_sections` → `compile_kind_index_spec` → `compile_value_brief`) —
    a hard refusal of a write that lands on `origin/main` (#1934 forbids exactly
    that; its neighbours on the path are deliberately fail-open:
    `domain_loader._get_registry` catches `Exception`, and `create_point`'s
    sibling advisory (#5516) degrades to "no notice").
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
