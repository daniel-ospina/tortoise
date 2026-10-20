"""The STORED FORM of a vector property — one spelling, shared by writer and check.

**#8615, §17.2 (``docs/architecture/STORAGE-ARCHITECTURE.md``).** FalkorDB holds
a vector property at the engine's ``float32`` width (``vecf32``) unless the
caller owns the value (``embedding_verbatim``, #5004). So the value a record
(truth) holds and the value the cache holds for the SAME faithful vector are
**different numbers** — ``0.1`` against ``0.10000000149011612`` — and any check
that compares them by exact value reports a permanent divergence on a
correctness-preserving projection.

**OVERRIDES:** exact by-value comparison of a vector property between the record
and the cache — the store narrows a non-verbatim vector to ``float32``, so an
exact ``float64``-vs-``float32`` comparison cannot distinguish the narrowing
from a real divergence, and reports both as ``field_mismatches`` (#8615).

WHY NARROW INSTEAD OF A TOLERANCE
=================================
``tortoise/reconciliation.py::_canonical`` compares numbers by exact value **by
design** and warns that a tolerant comparator would hide the small drifts the
check exists to surface. A tolerance is the wrong instrument here, in BOTH
directions:

* it is a *bound* (a magnitude below which a difference is forgiven), so it
  would also forgive a real divergence of that magnitude — the store's own
  ``float32`` step at 0.1 is already ~1.5e-9, far below any bound someone would
  choose for a 384-dimensional ranking vector;
* it is *one number for every property*, while the narrowing is a property of
  the STORAGE FORM of one class of property.

Narrowing the truth side to the width the projection writes is not forgiveness:
it is the **same comparison, on the same numbers, in the store's own
representation**. A vector that differs after narrowing is genuinely divergent,
and the ``str``-free integer count keeps saying so.

WHY THE TRUTH SIDE ONLY (and why that is the strict direction)
--------------------------------------------------------------
The cache is compared **as it is read**. The acceptance window is therefore a
single point — the exact `float32` rounding of the record's own value — and not
a band. Narrowing BOTH sides widens it to a full `float32` step on each side, so
a cache holding a *different* (but nearby) `float64` — e.g. `0.100000001`
against a record's `0.1`, inside one ulp — would narrow onto the same number and
compare EQUAL, a real difference forgiven by a comparator nobody bounded. One
sided narrowing keeps every comparison exact: the cache value must equal the
rounding of the record value, or it is a divergence.

WHY ``embedding_verbatim`` IS READ FROM EITHER SIDE
---------------------------------------------------
The marker is PRIMARY and travels with the vector (#5004). A verbatim vector is
stored un-narrowed, so it must be compared EXACTLY. The rule is ``OR`` of both
sides, for the reason ``consistency`` records: reading the form only from the
authority lets a narrowed vector hide behind a flag that was dropped on its own
node — and a dropped flag is precisely the ``live != rebuild`` class #5004
exists to prevent. ``OR`` is also the fail-closed direction: claiming verbatim
anywhere produces the STRICTER comparison.

THE SECOND FORM RULE: A BOOLEAN-VALUED PROPERTY (#9184)
======================================================
The same defect class reaches one more property, on the MARKER rather than the
vector. ``embedding_verbatim`` is ``text`` in the record (a journal ``True``
lands there as ``'true'``) and a graph BOOLEAN in the cache, so exact equality
reports a permanent ``field_mismatches`` entry for every verbatim point — a
faithful projection that can never go green (#9184). :data:`BOOLEAN_FORM_PROPS`
+ :func:`declared_boolean` hold that rule, in this module, for the same reason
the vector rule lives here: ONE spelling, shared with the check that reads it.
It normalises BOTH directions over the DECLARED vocabulary, so ``'false'`` is
``False`` and garbage stays exact — a genuinely different marker is still a
divergence.

THE MARKER IS READ THROUGH THE SAME VOCABULARY BY THE CHECK
-----------------------------------------------------------
The boolean rule fixes the MARKER's own comparison, and that alone was not
enough: the VECTOR rule reads the marker too, to decide whether the truth side
may be narrowed. Read through ``bool(...)`` — the ``bool('false') is True`` trap
— a record holding the falsy spelling claims verbatim, so a cache that
legitimately stored the narrowed ``f32`` vector is reported a PERMANENT
``field_mismatches``: #8615's symptom, reached through #9184's spelling.
:func:`declares_verbatim` is the CHECK-side read — the marker through
:func:`declared_boolean`, with the old truthiness read kept only as the fallback
for a value OUTSIDE the declared vocabulary (where truthiness is the
fail-closed direction). ⚠️ It deliberately does NOT change
:func:`is_verbatim`, whose truthiness read decides what the WRITER stores; that
half is #9184's. A record holding ``'false'`` therefore still has its vector
written un-narrowed, and the exact-agreement branch decides it before the
marker is consulted at all.

THE SINGLE SPELLING
===================
:data:`STORED_VECTOR_PROPS` / :func:`is_verbatim` / :func:`declares_verbatim` /
:func:`narrow_to_stored_width` are the one place the storage form is spelled.
Three consumers read them:

* ``tortoise/cache_projection.py::node_statement`` — the WRITER, which emits
  ``n.embedding = vecf32($embedding)`` vs ``= $embedding`` from this rule;
* ``tortoise/reconciliation.py`` — the record↔cache check, which narrows the
  truth side by it (#8615);
* ``tortoise/consistency.py::_as_f32`` — the journal↔graph check, which already
  narrowed by it and now delegates here rather than keeping a second copy.
"""
from __future__ import annotations

import struct
from collections.abc import Iterable, Mapping

from .env_truthy import FALSY, TRUTHY

__all__ = [
    "BOOLEAN_FORM_PROPS",
    "STORED_VECTOR_PROPS",
    "VERBATIM_MARKER",
    "declared_boolean",
    "declares_verbatim",
    "is_verbatim",
    "narrow_to_stored_width",
]

#: The property names the projection writes through the engine's ``vecf32``
#: narrowing. ONE entry today (``node_statement`` handles exactly these), and
#: deliberately a SET rather than a literal at each call site: a second vector
#: property must be declared here, which is what makes the writer and both
#: checks move together.
STORED_VECTOR_PROPS: frozenset[str] = frozenset({"embedding"})

#: The PRIMARY marker that says a caller owns the value's storage form, so the
#: vector is written and compared un-narrowed (#5004).
VERBATIM_MARKER = "embedding_verbatim"

#: The properties whose two sides hold DIFFERENT FORMS of one declared value by
#: SCHEMA, not by value: the record column is ``text`` (so a journal payload's
#: ``True`` lands as the string ``'true'``) while the cache holds a graph
#: boolean. An exact comparison of a FAITHFUL projection therefore reports a
#: permanent, unrepairable ``field_mismatches`` entry — the same class as #8615
#: (a property whose STORED FORM differs between the stores), on the marker
#: rather than the vector (#9184).
#:
#: **OVERRIDES:** exact by-value comparison of a boolean-valued property between
#: the record and the cache — the record's column is ``text`` and the cache's
#: property is a boolean, so an exact comparison reports the SCHEMA's choice of
#: type as divergence and can never go green for a verbatim point (#9184).
#:
#: ⛔ This is NOT ``is_verbatim``'s truthiness read and does NOT widen it. The
#: normalisation is the DECLARED boolean vocabulary (:func:`declared_boolean`:
#: real booleans, ``0``/``1``, and the ``env_truthy`` truthy/falsy spellings) in
#: BOTH directions, so ``'false'`` is ``False`` — never the ``bool('false')``
#: trap — and a value outside that vocabulary stays EXACT (it is not coerced to
#: either side). A genuinely different marker (``'true'`` vs ``False``) is still
#: a divergence. One entry today, deliberately a SET so a second boolean-form
#: property must be declared here rather than special-cased at a call site.
#:
#: ⚠️ **Reachable for the truthy spellings; the FALSY spelling waits on
#: #9184's WRITER half.** This rule is the CHECK side only. The writer still
#: decides the storage form with :func:`is_verbatim` (truthy for ``'false'``),
#: so a projection of a record holding ``'false'`` stores the marker as ``True``
#: and the vector un-narrowed — and the reconciler reports the MARKER as one
#: divergence, correctly: that cache is not faithful to what the record
#: declares. Fixing it means the writer reads the DECLARED boolean
#: (:func:`declares_verbatim`), which is #9184's half and changes what is
#: STORED, not what is compared. Pinned by
#: ``tests/test_reconciliation.py::test_the_shipped_writer_stores_the_falsy_marker_truthily``
#: and ``tests/test_record_reconcile_trigger.py::test_a_falsy_marker_point_reports_the_WRITER_half_as_divergence``.
BOOLEAN_FORM_PROPS: frozenset[str] = frozenset({VERBATIM_MARKER})


def declared_boolean(value: object) -> bool | None:
    """The DECLARED boolean a value spells, or ``None`` when it spells none.

    ``None`` means "not a boolean spelling", never ``False``: an unparseable
    value must degrade to the exact comparison the caller would have made
    anyway (the totality argument :func:`narrow_to_stored_width` records for a
    hand-editable record). Reading it as ``False`` would make every garbage
    string compare EQUAL to a boolean ``false`` — an impossible comparison
    reported as agreement (the #1382 class).

    The vocabulary is the tree's already-declared one (:mod:`tortoise.env_truthy`,
    #4097), not a new list: ``bool``, ``int`` ``0``/``1``, and the truthy/falsy
    spellings, case-insensitive and stripped.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUTHY:
            return True
        if normalized in FALSY:
            return False
    return None


def is_verbatim(*prop_maps: Mapping[str, object]) -> bool:
    """True when ANY of ``prop_maps`` declares the verbatim storage form.

    ``OR`` of every side, and the fail-closed direction (see the module
    docstring): a marker anywhere means compare exactly. A missing key, ``None``
    and ``False`` all mean "the store narrows".

    ⚠️ This is the WRITER's read (``node_statement`` chooses ``vecf32`` vs the
    raw value from it), and its truthiness is #9184's remaining half: a record
    holding the string ``'false'`` reads as verbatim HERE. The CHECK must not
    inherit that — see :func:`declares_verbatim`.
    """
    return any(bool(m.get(VERBATIM_MARKER)) for m in prop_maps)


def declares_verbatim(*prop_maps: Mapping[str, object]) -> bool:
    """True when ANY of ``prop_maps`` DECLARES the verbatim storage form.

    The CHECK-side read of the marker — the one that must not carry
    ``bool('false')`` into the comparison (:data:`BOOLEAN_FORM_PROPS`'s reason,
    one property over). Per side:

    * a DECLARED spelling decides on its own: ``'false'``/``0``/``FALSE`` mean
      "the store narrows", so the vector rule applies and a faithful ``f32``
      cache compares equal — #8615's forgiveness reaches the falsy spelling;
    * a value OUTSIDE the declared vocabulary falls back to the truthiness read
      :func:`is_verbatim` uses, the fail-closed direction, because claiming
      verbatim anywhere produces the STRICTER exact comparison;
    * a missing key or ``None`` means "the store narrows", exactly as in
      :func:`is_verbatim`: a non-verbatim point carries no marker at all, and
      reading that absence as verbatim would report every narrowed vector as a
      divergence.

    ``OR`` of every side, for the reason the module docstring records: a marker
    dropped on its own node must not let a narrowed vector hide.
    """
    for props in prop_maps:
        value = props.get(VERBATIM_MARKER)
        declared = declared_boolean(value)
        if declared is True:
            return True
        if declared is None and bool(value):
            return True
    return False


def narrow_to_stored_width(vector: Iterable[object]) -> list[float] | None:
    """``vector`` rounded to the store's ``vecf32`` width, or ``None``.

    ``None`` means "this is not a numeric vector", never "equal": the caller
    must fall back to the exact comparison it would have made anyway. The
    function is TOTAL — a record is a FILE (hand-editable, or written by another
    build), so a non-numeric / over-range element must degrade rather than
    raise inside a check (#5004's ``_writable_journalled_vector`` documents the
    same class for the writer).

    The round trip is ``struct``'s ``float32`` rather than a decimal rounding:
    it is exactly the conversion the engine performs, so the narrowed value is
    the number the cache will hold, not an approximation of it.
    """
    try:
        return [struct.unpack("<f", struct.pack("<f", float(x)))[0] for x in vector]
    except (TypeError, ValueError, struct.error, OverflowError):
        return None
