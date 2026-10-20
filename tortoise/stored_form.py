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

THE SINGLE SPELLING
===================
:data:`STORED_VECTOR_PROPS` / :func:`is_verbatim` / :func:`narrow_to_stored_width`
are the one place the storage form is spelled. Three consumers read them:

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

__all__ = [
    "STORED_VECTOR_PROPS",
    "VERBATIM_MARKER",
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


def is_verbatim(*prop_maps: Mapping[str, object]) -> bool:
    """True when ANY of ``prop_maps`` declares the verbatim storage form.

    ``OR`` of every side, and the fail-closed direction (see the module
    docstring): a marker anywhere means compare exactly. A missing key, ``None``
    and ``False`` all mean "the store narrows".
    """
    return any(bool(m.get(VERBATIM_MARKER)) for m in prop_maps)


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
