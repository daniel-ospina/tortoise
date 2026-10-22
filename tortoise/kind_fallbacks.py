"""The classifier's FAMILY FALLBACK kinds — a repair, never a discriminator.

#9572: the kind pipelines write a family fallback when they could not classify
an entity (``extractor_v2._ENTITY_FALLBACK`` / ``core:other``;
``kind_classifier.KindClassifier._fallback``). That fallback is **our repair**,
not a value a caller authored, so a write door must not apply it to a node that
already exists: the fold writes the discriminator ``ON MATCH`` as
``coalesce($kind, stored)``, so a repaired ``core:other`` silently reset a
stored ``capability``.

Why this is its own leaf module rather than a private in `sdk.py`: the defect
had already been found at four layers, and the *fifth* was the hosted
``/v1/sessions/commit`` door — a twin that imports nothing from the SDK's
private surface. ONE predicate that both doors import is what keeps a sixth
from appearing under a second spelling. Stdlib-only, no imports.
"""
from __future__ import annotations

#: The family fallback, in its **namespaced** spelling only. ``core:other`` is
#: what the pipelines emit when they could not classify (the ``_ENTITY_FALLBACK``
#: sentinel); the bare ``other`` is a REAL core kind a caller may name explicitly
#: — including to re-classify a node back to ``other`` — so it stays writable.
#: Do not add ``"other"`` to this set: an explicit ``other`` would then become
#: unwritable on the payload doors, and it would contradict
#: ``extractor._normalize_entity``, which deliberately treats an explicit
#: ``other`` as a NAMED kind (``objectKind_named``). Pinned by
#: ``test_kind_fallbacks_do_not_swallow_an_explicit_other``.
FAMILY_FALLBACK_KINDS: frozenset[str] = frozenset({"core:other"})


def is_family_fallback(kind: object) -> bool:
    """Is ``kind`` the classifier's repair sentinel rather than a real kind?"""
    if kind is None:
        return False
    return str(kind).strip().lower() in FAMILY_FALLBACK_KINDS


def named_kind(kind: object) -> str | None:
    """The caller-authored kind, or ``None`` when the caller named NONE.

    The IDENTITY doors' predicate (``sdk._entity_door_props``,
    ``create_subject``/``create_object``, ``EventAPI.add_subject``/
    ``add_object``, the two hosted REST handlers, the two seed surfaces).
    #9572's line was drawn at ``kind is not None``, so **blank was still a
    kind**: ``subjectKind=""`` is not ``None``, so it passed the approved-kind
    gate (a bare value names no pack, by design) and reached the fold's
    ``coalesce($k, stored)`` — which returns the first NON-NULL argument, and
    ``""`` is not null. A blank re-create therefore reset a stored ``team`` to
    ``""``, and a blank FIRST create stored ``""`` instead of the ``'other'``
    default. Blank is not a kind: the same rule ``extractor._normalize_entity``
    already applied (``not str(value).strip()`` ⇒ not named) and that
    ``writable_kind`` applied to the payload doors.

    It lives HERE, beside ``writable_kind``, so the two spellings of
    "nothing was named" cannot diverge again — one predicate per rule, shared
    by every door. It is NOT ``writable_kind``: that one also drops the
    classifier's ``core:other`` repair, which is a PAYLOAD rule. An identity
    door's kind is authored by the caller, not classified, so a caller who
    names ``core:other`` is naming a kind and stays writable.
    """
    if kind is None:
        return None
    _kind = str(kind).strip()
    if not _kind:
        return None
    return _kind


def writable_kind(kind: object) -> str | None:
    """The kind to WRITE, or ``None`` when it must not be written at all.

    ``None`` for an absent/blank kind (nothing was named — ``named_kind``) and
    for the family fallback (a repair); the value itself for a named kind.
    """
    _kind = named_kind(kind)
    if _kind is None or is_family_fallback(_kind):
        return None
    return _kind


def payload_entity_kind(entity: dict) -> str | None:
    """``writable_kind`` over a v2 payload entity's ``kind`` field."""
    if not isinstance(entity, dict):
        return None
    return writable_kind(entity.get("kind"))
