"""E-prec (#2350): the validity-window PRECISION vocabulary + 3-state end.

THE ONE HOME for the per-end precision layer that extends #1538's canonical
``validFrom``/``validTo`` fields — ADDITIVELY, never re-stamping an existing
window and never minting a parallel validity field. The window predicate
itself (ordering + inversion) lives in
``commit_schema.validate_validity_window``; this module owns only what that
predicate deliberately does not: HOW PRECISELY each end is known, and whether
an absent end means "still true" or "ended, but I cannot date it".

Why the distinction exists (the anti-pattern this prevents). An absent
``validTo`` has always read as an OPEN end (``sdk._covers``): the window
covers every instant after its start. That is correct for a still-true claim
and WRONG for a claim whose end is simply unknown — reading the second as the
first fabricates coverage, the "half-honest dates are worse than none" bug
class this epic adopts from deeplethe/utopia (#2349). The 3-state end makes
the difference REPRESENTABLE and READABLE:

  * ``END_OPEN``    — no end is asserted: ``validTo`` absent, no precision.
  * ``END_KNOWN``   — the end is dated: ``validTo`` present (precision
                      year/month/day; a legacy bare-ISO bound reads as ``day``
                      with no migration — ``effective_precision``).
  * ``END_UNKNOWN`` — the window ENDED, but at a time we cannot pin:
                      ``validToPrecision == "unknown"`` and NO ``validTo``.
                      A date is never fabricated to fill the hole.

The three-state is END-ONLY. ``validFromPrecision`` may not be ``"unknown"``:
a window with no datable start is not a window, and the 3-state vocabulary
this issue adds is about the end (open / closed-at-known-date /
closed-unknown).

Legacy compatibility is total: a window written by #1538 carries no precision
property, ``effective_precision`` reports ``"day"`` for its present bounds,
``end_state`` reports ``END_OPEN`` for its open end — byte-identical reads,
no migration.
"""
from __future__ import annotations

#: Concrete granularity a caller may DECLARE for a bound it supplies. The
#: stored bound stays whatever the caller wrote (the vocabulary is metadata
#: about the claim's precision, not a second date format — ``_created_sort_key``
#: keeps ordering the same value it always did).
BOUND_PRECISIONS: tuple[str, ...] = ("year", "month", "day")

#: The end-only "closed, un-datable" marker (utopia's ``valid_to_precision``
#: pattern). Never legal for a start.
PRECISION_UNKNOWN = "unknown"

PRECISION_VALUES: tuple[str, ...] = (*BOUND_PRECISIONS, PRECISION_UNKNOWN)

#: The 3-state window end.
END_OPEN = "open"
END_KNOWN = "known"
END_UNKNOWN = "unknown"
END_STATES: tuple[str, ...] = (END_OPEN, END_KNOWN, END_UNKNOWN)

#: Node property names (camelCase, matching ``validFrom``/``validTo``).
FROM_PRECISION_PROP = "validFromPrecision"
TO_PRECISION_PROP = "validToPrecision"


def normalize_precision(value: object, *, field: str = "precision") -> str | None:
    """Validate ONE precision token, or ``None`` when the bound declares none.

    ``None`` and the empty string BOTH mean "no precision declared" — the
    empty string is the codebase's undated convention (``when=""``), so it is
    normalized away rather than refused. Everything else must be a lowercase
    token in ``PRECISION_VALUES``: an unknown word is a caller error, not a
    bound to guess at, so it raises ``ValueError`` naming the field.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"{field} must be a string in {PRECISION_VALUES} (got {value!r})"
        )
    token = value.strip().lower()
    if not token:
        return None
    if token not in PRECISION_VALUES:
        raise ValueError(
            f"{field} {value!r} is not a validity precision — "
            f"expected one of {PRECISION_VALUES}"
        )
    return token


def effective_precision(bound: object, declared: object) -> str | None:
    """The precision a READ path should report for ``bound``.

    A declared token wins. A PRESENT bound with no declared token is the
    #1538 legacy shape and reads as ``"day"`` (``is not None`` presence, the
    same gate ``_covers`` uses — a falsey-but-present bound is a real bound).
    An absent bound with no declared token has no precision (``None``); an
    absent bound with ``"unknown"`` is the closed-unknown end.
    """
    declared_token = normalize_precision(declared, field="precision")
    if declared_token is not None:
        return declared_token
    if bound is not None:
        return "day"
    return None


def end_state(valid_to: object, valid_to_precision: object) -> str:
    """The 3-state end of a window — ``END_OPEN`` | ``END_KNOWN`` | ``END_UNKNOWN``.

    ``END_UNKNOWN`` when the end is explicitly marked un-datable; otherwise a
    PRESENT ``validTo`` is ``END_KNOWN`` and an absent one is ``END_OPEN``.
    An explicitly declared concrete precision on an absent end is a
    contradiction and is refused by ``validate_validity_precision``, so it is
    not silently resolved here.
    """
    token = normalize_precision(valid_to_precision, field=TO_PRECISION_PROP)
    if token == PRECISION_UNKNOWN:
        return END_UNKNOWN
    if valid_to is not None:
        return END_KNOWN
    return END_OPEN


def validate_validity_precision(
    valid_from: object,
    valid_to: object,
    valid_from_precision: object,
    valid_to_precision: object,
) -> None:
    """Refuse a precision declaration inconsistent with the bounds it annotates.

    THE ONE HOME for the rule, called by every Point write that can persist a
    precision property (``sdk._refuse_inconsistent_validity_precision``). It
    checks the pair it is GIVEN; the writer resolves the pair a PARTIAL update
    will actually persist (caller bound merged with the stored opposite) before
    delegating — the same split ``validate_validity_window`` declares.

    Refused states, each of which would otherwise make a read path produce an
    answer the data does not support:

    * an unknown word as either precision (a typo must not be stored and then
      silently ignored by ``end_state``);
    * ``validFromPrecision == "unknown"`` — the 3-state vocabulary is END-only;
    * ``validToPrecision == "unknown"`` with a PRESENT ``validTo`` — the marker
      means "no date", so a coexisting date is a fabrication;
    * a concrete precision (year/month/day) with an ABSENT opposite bound — a
      granularity cannot attach to a bound that does not exist.

    Raises ``ValueError``; callers that want a 422/ValueError boundary get one.
    """
    from_token = normalize_precision(valid_from_precision, field=FROM_PRECISION_PROP)
    to_token = normalize_precision(valid_to_precision, field=TO_PRECISION_PROP)

    if from_token == PRECISION_UNKNOWN:
        raise ValueError(
            f"{FROM_PRECISION_PROP}='unknown' is not legal — the 3-state "
            "vocabulary (open / closed-at-known-date / closed-unknown) "
            "describes the window END only; a window needs a datable start"
        )
    if to_token == PRECISION_UNKNOWN:
        if valid_to is not None:
            raise ValueError(
                f"{TO_PRECISION_PROP}='unknown' with a validTo "
                f"({valid_to!r}) is a contradiction — the marker means the "
                "end is un-datable, so no date may be fabricated alongside it"
            )
    elif to_token is not None and valid_to is None:
        raise ValueError(
            f"{TO_PRECISION_PROP}={to_token!r} requires a validTo — a "
            "granularity cannot describe a bound that does not exist"
        )
    if from_token is not None and valid_from is None:
        raise ValueError(
            f"{FROM_PRECISION_PROP}={from_token!r} requires a validFrom — a "
            "granularity cannot describe a bound that does not exist"
        )


def window_read_fields(
    valid_from: object,
    valid_to: object,
    valid_from_precision: object,
    valid_to_precision: object,
) -> dict:
    """The additive precision block every window-consuming READ path emits.

    ONE HOME for the read projection so ``search``/``recall`` annotation,
    ``assembly``, and ``get_historical_knowledge`` cannot drift in how they
    name or resolve the precision of a window. The returned dict is meant to
    be merged into an existing entry; the legacy default (no precision props,
    a dated bound) yields ``day`` for a present bound and ``open`` for an
    absent end — the pre-#2350 reading.

    Callers pass the bounds RAW. The presence gate is ``is not None`` (inside
    ``effective_precision``/``end_state``), so a falsey-but-present bound —
    ``""``, ``0`` — is a REAL bound and reads as ``day``, the same predicate
    ``_covers`` orders with. Pre-normalizing at a call site (``bound or
    None``) makes the same stored window resolve differently per surface,
    which is the drift this home exists to prevent (#2350 review P2).
    """
    return {
        "valid_from_precision": effective_precision(valid_from, valid_from_precision),
        "valid_to_precision": effective_precision(valid_to, valid_to_precision),
        "end_state": end_state(valid_to, valid_to_precision),
    }
