"""#4647/#7174 — the store's numeric domain lives in ONE place.

FalkorDB stores an integer as INT64 and a number as a double. A Python ``int``
is UNBOUNDED and ``decimal.Decimal`` is arbitrary-precision, so a value outside
those domains is SILENTLY ALTERED by the store — ``SET n.v = $v`` reports
success and stores a different number (measured samples below). Refusing at the
boundary is the honest failure mode: the caller has a faithful representation
available (a string) and should choose it deliberately rather than have the
value inferred.

**Why this module exists (#7174).** The predicate had one home — in
``tortoise/sdk.py`` — and was enforced from a HAND-MAINTAINED list of SDK call
sites. That list leaked in five consecutive review cycles on #7007; each round
found another unguarded writer, because the set of writers is open. Moving the
predicate here lets a second consumer enforce it at a boundary nobody has to
remember:

* ``tortoise.sdk`` — the EARLY refusal, before the in-memory mutation and before
  the journal emit (#4647).
* ``tortoise.cypher_guard`` — every param map on every guarded handle, which is
  the SDK, the registry, the projection, and the replay path (#7174). The
  channels that boundary excludes are listed once, in
  ``cypher_guard._guard_numeric_params``.

This module imports nothing from ``tortoise`` (the standard library, plus numpy
as an optional dependency): ``cypher_guard`` sits UNDERNEATH the SDK, so an
import back into ``tortoise.sdk`` would cycle.

The predicate is EXACTNESS, never a digit count — see the docstring below for
why a digit-count proxy was wrong in both directions.
"""
from __future__ import annotations

import decimal
import math
import numbers

#: #4647: FalkorDB stores an integer as INT64 and a number as a double. A
#: Python `int` is UNBOUNDED and `decimal.Decimal` is arbitrary-precision, so a
#: value outside those domains is SILENTLY ALTERED by the store — `SET n.v = $v`
#: reports success and stores a different number:
#:
#:     wrote 2**70        -> stored 9223372036854775807   (clamped)
#:     wrote -(2**70)     -> stored -9223372036854775808  (clamped)
#:     wrote 2**63        -> stored 9223372036854775807   (clamped: max + 1)
#:     wrote Decimal('0.12345678901234567890')
#:                        -> stored 0.123456789012346    (rounded)
#:
#: Reproduced against a live FalkorDB (#4647). The store cannot hold the value,
#: so some information is lost either way; refusing the write is the honest
#: failure mode, matching this function's existing fail-closed rejects below and
#: the repo's own `SPAN_OFFSET_MAX = 2**63 - 1` precedent (commit_schema.py).
#: A caller needing a wider identifier has a correct representation available —
#: a string — and should choose it deliberately rather than have it inferred.
_INT64_MIN = -(2 ** 63)
_INT64_MAX = 2 ** 63 - 1

#: Depth cap for the container recursion: a self-referential container would
#: otherwise raise ``RecursionError`` instead of a bounded fail-closed error
#: (code-review cycle 2, P3). 32 is the repo's own persistable depth
#: (``projection/entities.py::_PERSISTABLE_MAX_DEPTH``); the shallower cap (12)
#: refused structures the store holds EXACTLY — a false refusal, because the
#: guard must sit AT the store's limit, never below it. At the limit it still
#: fails closed (code-review cycle 5, P2).
_MAX_RECURSION_DEPTH = 32

try:  # numpy is a declared dependency, but not on every import path.
    import numpy as _np

    _HAS_NUMPY = True
except Exception:  # pragma: no cover - environment dependent
    _np = None
    _HAS_NUMPY = False


def numeric_alteration_reason(
    key: str, value: object, _depth: int = 0
) -> str | None:
    """#4647: why the store would ALTER this value, or None if it would not.

    The test is EXACTNESS, not a digit count. A digit-count proxy is wrong in
    both directions (code-review P1): it refused ``Decimal('1000000000000001')``
    and ``Decimal(0.1)``, which a double holds exactly, while admitting
    ``Decimal('0.1')``, which it silently rounds. The predicate is therefore
    \"does this value round-trip through the store's own number type\" — plus
    finiteness, which also catches the exponent axis (``Decimal('1E+400')`` ->
    ``inf``, ``Decimal('1e-400')`` -> ``0.0``) and the non-finite Decimals.

    ``numbers.Integral`` (not ``int``) so a ``numpy`` scalar is caught too —
    numpy integers are NOT Python ``int`` subclasses. ``bool`` is excluded
    explicitly: it is an ``int`` subclass and is always representable.
    """
    if _depth > _MAX_RECURSION_DEPTH:
        # REFUSE at the cap, never ``return None``: a fail-closed guard that
        # fails OPEN is worse than no cap, because a value nested past the cap
        # is admitted and the store then clamps its leaf — the exact #4647
        # defect, and a regression introduced by adding the cap at all
        # (code-review cycle 3, P2).
        return (
            f"{key!r}: value is nested deeper than the guard inspects "
            f"({_MAX_RECURSION_DEPTH} levels), so its contents cannot be "
            "checked for a number the store would silently alter. Flatten it "
            "or store the deep part as a string."
        )
    if _HAS_NUMPY and isinstance(value, (_np.timedelta64, _np.datetime64)):
        # numpy temporal scalars register as ``numbers.Integral`` (timedelta64),
        # or fall through every branch (datetime64), but neither has a Cypher
        # number literal: the driver inlines ``str(value)``, which is
        # ``'1 years'`` / ``'1970-01-02'``. Catching only the units whose
        # ``int()`` raises left the calendar/sub-microsecond units admitted —
        # ``str()`` is not a number for ANY unit (code-review cycle 7, P2).
        return (
            f"{key!r}: {type(value).__name__} has no Cypher number literal "
            "(``str()`` renders a date/duration, not a number), so the store "
            "cannot hold it. Store it as a string or an epoch integer."
        )
    if isinstance(value, bool):
        # ``bool`` is an ``int`` subclass, but 0/1 are in range either way — this
        # is documentation, not a load-bearing branch (cycle-2 P3).
        return None
    if isinstance(value, numbers.Integral):
        # ``int()`` can raise on a numpy scalar that registers as Integral but
        # has no integer conversion — ``np.timedelta64`` does exactly this
        # (``TypeError: ... not 'datetime.timedelta'``), which escaped as a
        # bare key-less TypeError from a helper documented to raise ValueError
        # (code-review cycle 6, P2). Fail closed with the key instead.
        try:
            ivalue = int(value)
        except (TypeError, ValueError, OverflowError):
            return (
                f"{key!r}: {type(value).__name__} has no faithful INT64 "
                "representation. Store it as a string if the full value is "
                "needed."
            )
        if not (_INT64_MIN <= ivalue <= _INT64_MAX):
            # ``bit_length`` rather than ``str(value)``: ``str`` on a very large
            # int raises the 4300-digit limit error BEFORE the message is built,
            # losing the key, the range and the remedy (code-review P3).
            return (
                f"{key!r}: integer with {ivalue.bit_length()} bits is outside "
                f"the range FalkorDB can store ({_INT64_MIN}..{_INT64_MAX}) and "
                "would be SILENTLY clamped to a different number. Store it as "
                "a string if the full value is needed."
            )
        return None
    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            return (
                f"{key!r}: Decimal {value} is not a finite number, so the "
                "store cannot represent it faithfully. Store it as a string "
                "if the full value is needed."
            )
        # The driver sends ``str(value)``, and Cypher parses a bare integer
        # literal as INT64 — a DIFFERENT domain from the double. An integral
        # Decimal must therefore be range-checked, not round-tripped through a
        # double (code-review cycle 2, P1): ``Decimal(2**70)`` is exactly
        # representable as a double yet the store CLAMPS it, while
        # ``Decimal('9223372036854775807')`` is NOT exactly a double yet the
        # store holds it exactly. Checking one domain for both was wrong in
        # both directions.
        _text = str(value)
        if "e" not in _text.lower() and "." not in _text:
            _ivalue = int(value)
            if not (_INT64_MIN <= _ivalue <= _INT64_MAX):
                return (
                    f"{key!r}: integer with {_ivalue.bit_length()} bits is "
                    "outside the range FalkorDB can store "
                    f"({_INT64_MIN}..{_INT64_MAX}) and would be SILENTLY "
                    "clamped to a different number. Store it as a string if "
                    "the full value is needed."
                )
            return None
        _as_float = float(value)
        if not math.isfinite(_as_float) or decimal.Decimal(_as_float) != value:
            return (
                f"{key!r}: Decimal {value} is not exactly representable as a "
                "double, so FalkorDB would store a DIFFERENT number. Store it "
                "as a string if the full precision is needed."
            )
        return None
    if isinstance(value, numbers.Rational):
        # ``fractions.Fraction`` and friends. An INTEGRAL Fraction is a bare
        # INT64 literal to the store (cycle-3 P2: ``Fraction(2**70)`` was
        # admitted and then clamped). A NON-integral Fraction has no Cypher
        # number literal at all — the driver inlines ``str(value)``, which for
        # ``Fraction(1, 2)`` is ``'1/2'``, and the store rejects that with an
        # opaque ``Invalid input '/'`` rather than holding it. The cycle-5
        # branch compared ``Fraction(float(value))`` and so ADMITTED it, i.e.
        # the predicate contradicted the transport (code-review cycle 6, P2).
        # Model the serialization honestly and refuse every non-integral ratio.
        # ``bit_length()`` (never ``{value}``) because rendering a huge
        # numerator hits CPython's 4300-digit int->str limit and would make the
        # MESSAGE itself raise (cycle-4 P2).
        if value.denominator == 1:
            return numeric_alteration_reason(key, int(value), _depth)
        return (
            f"{key!r}: a ratio with a {value.numerator.bit_length()}-bit "
            f"numerator and a {value.denominator.bit_length()}-bit denominator "
            "has no Cypher number literal (``str()`` renders '1/2'), so the "
            "store cannot hold it. Store it as a string (or a float)."
        )
    if isinstance(value, numbers.Real):
        # Plain ``float`` and the numpy floating scalars (float16/32/64,
        # longdouble). The driver sends ``str(value)`` and Cypher parses that
        # as a DOUBLE, so the predicate must compare the number the store will
        # PARSE against the caller's value — not the caller's value against
        # itself. ``np.float32(0.1)`` renders as ``'0.1'`` (numpy's
        # shortest-repr) and parses to the double ``0.1``, a DIFFERENT number
        # from the float32 value; a bare ``float``/finiteness test admitted it
        # and the store altered it (code-review cycle 5, P1). ``np.float64``
        # and plain ``float`` round-trip through their repr exactly.
        # ``numbers.Real`` is after ``numbers.Rational`` on purpose: a
        # ``Fraction`` is both, and the Rational branch is the exact one.
        try:
            _as_float = float(value)
        except (OverflowError, ValueError):
            _as_float = math.inf
        if not math.isfinite(_as_float):
            return (
                f"{key!r}: {value!r} is not a finite number, so the store "
                "cannot represent it faithfully. Store it as a string if the "
                "full value is needed."
            )
        try:
            _parsed = float(str(value))
        except (OverflowError, ValueError):
            _parsed = math.inf
        # Compare at FULL precision: a numpy scalar's own ``!=`` narrows to its
        # dtype, and ``float(value)`` is exact for float16/32/64 but NOT for
        # longdouble — either misses the alteration. Widening the stored double
        # back to longdouble and comparing there is exact for every numpy float.
        if _HAS_NUMPY and isinstance(value, _np.floating):
            _exact_ok = bool(_np.longdouble(_parsed) == value)
        else:
            _exact_ok = _parsed == float(value)
        if not math.isfinite(_parsed) or not _exact_ok:
            return (
                f"{key!r}: {value!r} is not exactly representable as a double, "
                "so FalkorDB would store a DIFFERENT number. Store it as a "
                "string if the full precision is needed."
            )
        return None
    if isinstance(value, (list, tuple, set, frozenset)):
        # Fast path (code-review cycle 5, P2 perf): a flat sequence of plain
        # Python floats is the embedding case — every element is exactly a
        # double, so the only possible refusal is non-finite, which can be
        # checked without a Python-level recursive call per element (~60-75x
        # an ordinary props write; 1.4 ms for a 1536-dim embedding).
        if value and all(type(item) is float for item in value):
            for item in value:
                if not math.isfinite(item):
                    return (
                        f"{key!r}: {item!r} is not a finite number, so the "
                        "store cannot represent it faithfully. Store it as a "
                        "string if the full value is needed."
                    )
            return None
        for item in value:
            reason = numeric_alteration_reason(key, item, _depth + 1)
            if reason:
                return reason
        return None
    if isinstance(value, dict):
        for item in value.values():
            reason = numeric_alteration_reason(key, item, _depth + 1)
            if reason:
                return reason
        return None
    if _HAS_NUMPY and isinstance(value, _np.ndarray):
        # list/tuple/dict alone missed array-likes (cycle-2 P2):
        # `np.array([2**70])` was admitted and stored as [9223372036854775807].
        # Iterate the ELEMENTS, not ``.tolist()`` (code-review cycle 5, P1):
        # ``tolist()`` widens a float32/float16 array to Python floats, so the
        # serialized form the store actually parses (``str()`` of the ORIGINAL
        # numpy scalar, e.g. ``'0.1'``) was never checked and a float32 array
        # was admitted then altered.
        for item in value.ravel():
            reason = numeric_alteration_reason(key, item, _depth + 1)
            if reason:
                return reason
        return None
    return None


def reject_unrepresentable_number(key: str, value: object) -> None:
    """#4647: fail closed on a value the store cannot hold without altering it."""
    reason = numeric_alteration_reason(key, value)
    if reason:
        raise ValueError(reason)
