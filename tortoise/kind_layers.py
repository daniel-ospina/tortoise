"""ONE authority for the kind ↔ layer question (#9964, #9675's step 1).

A kind is *declared* on a **layer** — Subject / Object / Point / Event
(ONTOLOGY §1). Two surfaces used to answer "which layer does this kind belong
to?" separately:

* the **classifier**, which partitions its candidate set by SECTION before it
  chooses a kind (``kind_classifier.TYPE_SECTIONS`` over the kind-index spec,
  whose ``section`` field is assigned from the declarations here — see
  ``core_sections()`` and ``value_extractor.compile_kind_index_spec``), and
* the **write path**, which labels the node (``:Subject`` / ``:Object`` / …) —
  from the caller's ``type`` argument at the public doors, and from
  ``subject_binding.is_subject_kind`` in the two lanes that happened to route
  it (the extractor's capture spine and the hosted commit door).

Two answers to one question is the defect, and the disagreement is SILENT.
``KindClassifier.TYPE_SECTIONS["entity"]`` merges the object and subject
sections, so the kind the classifier returns carries no layer at all:
MEASURED at ``4c797596d`` on ``2026-10-25``,
``classify_items([{"type": "entity", "text": "the Acme Corp leadership
team"}])`` returns ``core:team`` (a ``subjects``-section kind) for an item the
SDK door then stored as ``:Object`` — and
``create_object("T3 Acme Holdings", objectKind="core:organization")`` minted a
``:Object`` that ``MATCH (s:Subject {subjectKind:'core:organization'})``
cannot see (0 rows), permanently (#2489: ``rebuild_all`` does not repair an
existing graph).

This module answers it ONCE, and every consumer derives from it:

* ``section_of(kind)`` — the kind's section in the kind-index spec, i.e. the
  classifier's OWN partition. (The spec's core sections come from
  ``core_sections()`` here, so the classifier's partition and this answer
  cannot drift.)
* ``declared_layers(kind)`` — every layer that DECLARES the kind: its index
  section, the §5 Subject vocabulary, and each layer's write-gate vocabulary
  (``domain_loader.known_kinds(bucket)``). It is a SET, not one value, because
  the point↔object and point↔event overlaps are CORRECT (#7813 owner ruling,
  implemented by #7853): ``goal``/``plan``/``strategy``/``target`` are declared
  on both Point and Object, ``decision``/``humanApproval`` on both Point and
  Event. A write on any declaring layer is correct and must never be
  "corrected" — that would reverse a recorded decision.
* ``undeclared_on(kind, layer)`` — the OTHER layers that declare it when
  ``layer`` does not. THAT is a kind↔layer disagreement, and the write doors
  report it as a warning (warn-not-block is #1934's recorded decision, so the
  write LANDS; a refusal would be a reopen for the owner).
* ``entity_route(kind)`` — the single entity layer a kind is declared on, for
  the one disagreement a door can re-route LOSSLESSLY: an Object kind at the
  subject door, or a Subject kind at the object door. Both are ``:entity``
  nodes in the classifier's partition, so the door's ``type`` argument is a
  hint and the KIND is the decision. The two entity layers are disjoint
  (measured: no Object kind is a §5 Subject kind, and no pack declares a
  subject kind), so the route has one answer.

``other`` / ``core:other`` declares NOTHING about the layer — §5's catch-all,
and the fold's ON CREATE default for every entity label — so it is never
routed and never warned about.

Deliberately stdlib-only at module scope (like ``kind_fallbacks``): this module
is imported by the SDK doors, the binder and the classifier's spec builder, and
each of those would otherwise inherit a cycle. Every vocabulary read is lazy.
"""
from __future__ import annotations

from typing import Any

#: The layers a kind can be declared on, in the order the write doors know them.
LAYERS = ("subject", "object", "event", "point")

#: The layers that are `:entity` nodes in the classifier's partition
#: (``TYPE_SECTIONS["entity"] = ("objects", "subjects")``) — the two a door can
#: re-route between losslessly.
ENTITY_LAYERS = ("object", "subject")

#: kind-index spec section → layer. The spec's section names are the classifier's
#: partition (`objects`/`subjects`/`events`/`points`), not the label names.
SECTION_LAYERS: dict[str, str] = {
    "objects": "object",
    "subjects": "subject",
    "events": "event",
    "points": "point",
}

#: The canonical kind property each layer writes (ONTOLOGY §4.1-§4.3, §4.5).
#: The layer→prop direction is the authority; the prop→layer direction is derived.
KIND_PROP: dict[str, str] = {
    "subject": "subjectKind",
    "object": "objectKind",
    "event": "eventKind",
    "point": "pointKind",
}

PROP_LAYER: dict[str, str] = {prop: layer for layer, prop in KIND_PROP.items()}

#: The door that writes each layer — named in the warning so the caller has the
#: remedy, not just the diagnosis.
DOOR: dict[str, str] = {
    "subject": "create_subject",
    "object": "create_object",
    "event": "create_event",
    "point": "create_point",
}

#: §5's catch-all bucket. It is a REAL writable kind on every entity layer (the
#: fold's ON CREATE default) and it asserts nothing about the layer, so it never
#: routes and never warns.
NIL_KINDS = frozenset({"other", "core:other"})


# ── the core declarations ─────────────────────────────────────────────────

def core_sections() -> dict[str, str]:
    """``{core kind: section}`` — the CORE half of the classifier's partition.

    Insertion order IS the kind-index spec's key order (objects → subjects →
    points → events), because ``compile_kind_index_spec`` builds the spec from
    this map and ``kind_index.cache_key_for`` hashes the spec JSON: reordering
    would rotate the persisted index's cache key and silently re-embed the whole
    kind index (the #2818 note). The four source declarations are imported, never
    re-spelled — ONTOLOGY §5 is canonical and ``extractor_v2`` mirrors it.
    """
    from tortoise.extractor_v2 import CORE_OBJECT_KEYS, EVENTS, POINTS, SUBJECTS

    sections: dict[str, str] = {}
    for kind in CORE_OBJECT_KEYS:
        sections[kind] = "objects"
    for kind in SUBJECTS:
        sections[kind] = "subjects"
    for kind in POINTS:
        sections[kind] = "points"
    for kind in EVENTS:
        sections[kind] = "events"
    return sections


def subject_kind_names() -> frozenset[str]:
    """The declared §5 Subject kind vocabulary — ``extractor_v2.SUBJECTS`` keys
    EXACT, with the ``other`` NIL bucket excluded (it declares nothing about the
    layer; see ``NIL_KINDS``).

    The ONE declaration of the subject vocabulary: ``subject_binding`` re-exports
    this (its ``is_subject_kind`` gate and the drift test that pins it against
    ``extractor_v2.SUBJECTS``), so the binder, the retype path, the write doors
    and the classifier's spec cannot disagree about what a Subject kind is.
    """
    from tortoise.extractor_v2 import SUBJECTS

    return frozenset(
        str(k).strip() for k in SUBJECTS
        if str(k).strip().split(":", 1)[-1].lower() != "other"
    )


def declares_subject(kind: Any) -> bool:
    """True iff ``kind`` names a DECLARED §5 Subject kind (the D2/T2 contract).

    Accepted EXACTLY: either a declared key (``core:team``) or — only when the
    declared key lives in the ``core:`` namespace — its bare form (``team``).
    A foreign namespace (``acme:team``), a leading-colon form (``:team``), a
    case/whitespace variant, and ``None``/``""`` are False. Never invent a
    Subject from an arbitrary namespace.

    ⚠️ NO whitespace normalization, on purpose: ``' core:team '`` is not a
    declared key, and the fail-closed contract of the binder depends on the
    comparison being the caller's RAW string.
    """
    if not isinstance(kind, str):
        return False
    declared = subject_kind_names()
    if kind in declared:
        return True
    if ":" in kind:
        # A namespaced form is accepted only when it is a declared key itself
        # (handled above). Never strip an arbitrary namespace to a bare name.
        return False
    # Bare form: accepted only for a declared ``core:`` key.
    return f"core:{kind}" in declared


# ── the classifiers partition, read from the spec ─────────────────────────

def section_of(kind: Any) -> str | None:
    """The kind's section in the kind-index spec (the classifier's partition).

    ``None`` for a kind the spec does not carry (a legacy/registered/pack kind
    with no classification entry) — the caller then answers from the write-gate
    vocabularies alone.
    """
    if not isinstance(kind, str) or not kind:
        return None
    from tortoise.value_extractor import kind_index_sections

    return kind_index_sections().get(kind)


# ── the answer ────────────────────────────────────────────────────────────

def declared_layers(kind: Any) -> frozenset[str]:
    """Every layer that DECLARES ``kind`` (empty set = nothing declares it).

    The union of three declarations, each read from its own home:

    * the classifier's section (``section_of``) — the section decides its layer;
    * the §5 Subject vocabulary (``declares_subject``);
    * each layer's write-gate vocabulary (``domain_loader.known_kinds(bucket)``)
      — the same predicate the hosted validator uses (``kind_is_known``), so
      this answer matches what the write gate already accepts, both spellings of
      a namespaced kind included.

    A ``NIL_KINDS`` value returns EMPTY: ``other`` is writable on every entity
    layer (the fold's ON CREATE default) and asserts nothing, so it must not be
    read as evidence for one layer.
    """
    if not isinstance(kind, str) or not kind.strip():
        return frozenset()
    if kind in NIL_KINDS:
        return frozenset()
    from tortoise.domain_loader import kind_is_known

    layers: set[str] = set()
    section = section_of(kind)
    if section in SECTION_LAYERS:
        layers.add(SECTION_LAYERS[section])
    if declares_subject(kind):
        layers.add("subject")
    for layer in LAYERS:
        if layer == "subject":
            continue  # no subject bucket: §5's subject vocabulary is SUBJECTS
        if kind_is_known(kind, KIND_PROP[layer]):
            layers.add(layer)
    return frozenset(layers)


def undeclared_on(kind: Any, layer: str) -> tuple[str, ...]:
    """The OTHER layers declaring ``kind``, when ``layer`` does not.

    Empty when the write is consistent (``layer`` is among the declaring layers,
    or nothing declares the kind at all — the vocabulary is deliberately open).
    Sorted for a stable message.
    """
    layers = declared_layers(kind)
    if not layers or layer in layers:
        return ()
    return tuple(sorted(layers))


def entity_route(kind: Any) -> str | None:
    """The ONE entity layer ``kind`` is declared on, or ``None``.

    ``None`` when the kind is undeclared, declared on more than one entity layer
    (impossible today — the two are disjoint — but not assumed), or declared
    anywhere outside the entity partition (``goal`` is Point AND Object; an
    event kind is an Event). Only a kind that belongs *exclusively* to one
    entity layer can decide a door's label without losing something.
    """
    layers = declared_layers(kind)
    if not layers:
        return None
    if layers - set(ENTITY_LAYERS):
        return None
    found = sorted(layers)
    return found[0] if len(found) == 1 else None
