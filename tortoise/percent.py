"""Percent as a flat, bounded structural value (#11915).

Sibling of :mod:`tortoise.money` (#2782), and deliberately a SIBLING rather
than a mode of it: ``money``'s invariant is the pinned ISO 4217 minor-unit
exponent, ``percent``'s is a bound — ``0 ≤ basisPoints ≤ 10000``. Those are two
different rules about what a number may be, so they are two shapes, each
stating its own rule where that unit lives.

Why per-unit rather than one generic ``(value, unit)`` pair — recorded so it is
not re-litigated (#11915, and the reading it rests on): ``schema.org`` models
this as one ``QuantitativeValue`` (``value`` + ``unitCode``) **plus** a
``MonetaryAmount`` subtype, and FHIR keeps ``Quantity``, ``Money`` and ``Ratio``
as **separate datatypes**. The convergence is *"a number plus a reference"* as
the rule, with each unit's invariant expressed where that unit lives — not one
undifferentiated triple. ``money``'s exponent table and ``percent``'s 0–10000
bound are exactly those invariants.

Shape (`docs/ONTOLOGY.md` §4.1 — a value is a number plus a reference, kept as
flat sibling properties on the entity that carries it):

    basisPoints          int   0 … 10000 (12% = 1200). REQUIRED
    verbatim             str   the exact source text — ONLY when the value was
                               derived from prose

⛔ **No temporal field.** A value carries no temporal slot of its own: the
instant is the CARRIER's valid-time start — ``when``/``validFrom`` on a Point,
``startedAt`` on an Event (#11827, §4.7). ``asOf`` was retired for exactly this
and is not reintroduced under a percent-shaped name.

⛔ **`verbatim` is derived-only (owner decision D4b).** A machine-supplied value
carries its ORIGIN instead (``source_ref`` plus the ingest reference on the
carrier), so there is no fabricated-evidence path: this module validates a
``verbatim`` that a caller SUPPLIES (non-empty, a string), and it never
synthesises one. A caller with no source text simply passes none, and the
property is absent — which is what "machine-supplied" looks like on the node.

Where the rule lives (the #11836 pattern, one home):

  * **derivation** is ``value_normalizer``-shaped work and is NOT done here —
    this module parses no text (a ``%`` in prose is the S5 half's problem, and
    today the normalizer abstains on it: ``not_money_percent``);
  * **refusal** is :func:`refuse_malformed_percent`, called from the SDK's one
    props intake (``_sanitize_props``: every create/update props door) and from
    the pipeline seam that bypasses that intake (``api.EventAPI._emit``,
    through :func:`refuse_malformed_percent_in_payload`), so a value the reader
    would refuse cannot enter through either door's FLAT props.
    ⚠️ STATED BOUND: the props door grades the caller's flat bag, so a
    ``basisPoints`` parked one level down (``create_point(..., uses=[{...}])``)
    is graded only where the payload walk runs — ``EventAPI._emit``'s record
    shape. That is money's boundary too, not a regression: the props doors have
    never walked nesting, and inventing a walk there would grade the caller's
    unrelated sub-payloads;
  * **reading** is :func:`read_percent_value`, and the refusal DELEGATES to it
    rather than restating the bound, so the write boundary and the read
    boundary cannot drift into disagreeing about what a percent value is.

⚠️ STATED BOUND, not a claim of closure: ``basisPoints`` is stored in the
open-set ``props`` tail — there is no typed record column for it (#11915's
scope; the record-side reader that would justify one does not exist). The graph
property is still validated at every live door listed above, and replay is
deliberately ungated, exactly as for money's optional members.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from tortoise.money import (
    AMOUNT_MINOR,
    CURRENCY,
    MINOR_UNIT_EXPONENT,
    PAYLOAD_MAX_DEPTH,
    VERBATIM,
)

__all__ = [
    "BASIS_POINTS",
    "PERCENT_MAX_BP",
    "PERCENT_MIN_BP",
    "PERCENT_VALUE_FIELDS",
    "PercentValueError",
    "percent_correction",
    "percent_props",
    "read_percent_value",
    "refuse_malformed_percent",
    "refuse_malformed_percent_in_payload",
    "refuse_shared_evidence",
]

BASIS_POINTS = "basisPoints"

#: 0% and 100%. A percent outside this band is not a percentage of anything —
#: the bound IS the invariant a generic ``(value, unit)`` pair cannot express,
#: which is why the shape is per-unit (#11915).
PERCENT_MIN_BP = 0
PERCENT_MAX_BP = 10_000

#: The declared sibling fields of one percent value, in canonical order.
#: ``verbatim`` is shared with ``money`` (one evidence field, one spelling) and
#: is present only where the value was derived from prose.
PERCENT_VALUE_FIELDS: tuple[str, ...] = (BASIS_POINTS, VERBATIM)

#: Money's three GROUP members (not its optional evidence): a bag naming any of
#: these and `basisPoints` names TWO values. Read from money's own constants, so
#: a renamed member cannot leave this list silently stale.
_MONEY_MEMBERS: tuple[str, ...] = (
    AMOUNT_MINOR, CURRENCY, MINOR_UNIT_EXPONENT)


class PercentValueError(ValueError):
    """A present percent field is malformed — never guess, refuse."""


def percent_correction(value: Any) -> str:
    """The corrected-value half of a refusal, or ``""`` when not computable.

    ``docs/ax/ax-principles.md`` rule 2: a refusal says it refused, names the
    field and the bound, and **gives the corrected value where computable**.
    So an out-of-range int is reported as the percentage it would mean
    (``12000`` is ``120%``), and the reference conversion is always spelled
    (``12%`` is ``1200``) so a caller holding a percentage can land it without
    a second guess. A value that names no number (a string, a bool, ``None``)
    has no computable correction and gets none — inventing one would be the
    guess this module refuses.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    try:
        pct = value / 100
    except (OverflowError, ValueError):  # unreachable for int/float; explicit
        return ""
    suffix = (
        f", and {value} would be {pct:g}%"
        if not (PERCENT_MIN_BP <= value <= PERCENT_MAX_BP) else ""
    )
    return f"12% is 1200{suffix}"


def _validate_basis_points(value: Any) -> int:
    # ``bool`` is an ``int`` subclass; ``True`` is not a percentage.
    if isinstance(value, bool) or not isinstance(value, int):
        raise PercentValueError(
            f"{BASIS_POINTS} must be an int in basis points "
            f"({PERCENT_MIN_BP}–{PERCENT_MAX_BP}), got "
            f"{type(value).__name__}: {value!r}{_type_correction(value)}")
    if not (PERCENT_MIN_BP <= value <= PERCENT_MAX_BP):
        raise PercentValueError(
            f"{BASIS_POINTS} must be {PERCENT_MIN_BP}–{PERCENT_MAX_BP}: "
            f"{percent_correction(value)}, got {value!r}")
    return value


def _type_correction(value: Any) -> str:
    """The corrected value for a WRONG-TYPE percentage, where computable.

    ``ax-principles.md`` rule 2 asks a refusal to give the corrected value where
    it can be computed, and a percentage the caller holds as a PERCENTAGE is the
    common case for this branch (``12.0`` / ``"12"``). It is stated only as a
    conversion, never applied: a value this gate converted itself would be the
    guess that ``12.5 → 1250`` may be the wrong reading (a rounded percentage is
    not a basis-point count), so the caller states the number.
    """
    if isinstance(value, bool):
        return ""
    if isinstance(value, float) and math.isfinite(value * 100):
        # ⚠️ `value * 100` is guarded, not `value`: a finite float above
        # ~1.8e306 overflows to `inf` in the MULTIPLICATION, and `round(inf)`
        # raises `OverflowError` — from a refusal formatter, which would then
        # escape as a non-`ValueError` and be mapped to a 5xx by the routers
        # that catch `ValueError` (measured at 1e308). The write is still
        # blocked, but the refusal must be the class the doors catch.
        return (f" — {value:g}% is {round(value * 100)} basis points; "
                "supply the int")
    if isinstance(value, str):
        return " — a percentage written as text is not a value; supply the int"
    return ""


def _validate_verbatim(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise PercentValueError(
            f"{VERBATIM} must be the exact non-empty source text, got "
            f"{value!r}")
    return value


def percent_props(basis_points: int, *,
                  verbatim: str | None = None) -> dict[str, Any]:
    """Build the flat percent value fields for one percentage.

    Validates at the boundary (never guess), so a malformed percentage can
    neither be constructed nor reach a write. ``verbatim`` is OMITTED when
    absent rather than written as ``None`` — the projection's ``SET n +=
    $props`` drops ``None`` anyway, and absence is the machine-supplied case
    (owner decision D4b): a value with no prose origin carries none.
    """
    props: dict[str, Any] = {
        BASIS_POINTS: _validate_basis_points(basis_points),
    }
    if verbatim is not None:
        props[VERBATIM] = _validate_verbatim(verbatim)
    return props


def read_percent_value(record: Mapping[str, Any]) -> int | None:
    """Read one record's ``basisPoints``.

    Returns ``None`` when the record carries no percent at all — a non-percent
    node in a mixed set. Raises :class:`PercentValueError` when the field IS
    present but malformed: a present-but-out-of-range percentage is a data
    error, never something to clamp into the band.
    """
    present = record.get(BASIS_POINTS)
    if present is None:
        return None
    return _validate_basis_points(present)


def refuse_malformed_percent(props: Mapping[str, Any], *,
                             door: str = "") -> None:
    """Refuse, at the WRITE boundary, a percent value the READER would refuse.

    Returns ``None`` when the bag carries no percent — it inherits
    :func:`read_percent_value`'s engagement rule (no ``basisPoints`` key
    present), so a bare ``verbatim`` that belongs to a money value or to
    ordinary quoting metadata is untouched.

    WHY THERE IS NO UPDATE-SIDE "VIEW", unlike ``money``: that helper exists
    because money's value is a GROUP of three required fields, so a bag can
    name one member and leave the state HALF a value (``currency=None``
    deletes the currency and makes the remainder unreadable). A percent value
    is ONE required scalar, so a bag that names it IS the state the write
    leaves, and ``basisPoints=None`` deletes it into "no percent here" — the
    no-op this gate must not refuse. The one optional member,
    ``verbatim``, is graded on the CALLER's bag only, the same leniency
    ``money._validate_present_optional_fields`` states and for the same
    reason: a row written before this rule must keep reading, and must keep
    being updatable.

    ONE HOME for the rule: this delegates to :func:`read_percent_value`
    rather than re-deriving the bound. Failure is a hard refusal naming the
    field, the bound and — where computable — the corrected value
    (:func:`percent_correction`), never a clamp into range: a percentage
    silently clamped to 100% is a wrong number that reads as a real one.
    """
    try:
        if read_percent_value(props) is not None:
            supplied = props.get(VERBATIM)
            if supplied is not None:
                _validate_verbatim(supplied)
    except PercentValueError as exc:
        where = f" ({door})" if door else ""
        raise PercentValueError(
            f"percent value refused at the write boundary{where}: {exc}. "
            "A value the reader refuses must not become durable state "
            "(#11915, the #11836 pattern)."
        ) from exc


def refuse_shared_evidence(named: Mapping[str, Any], *,
                           stored: frozenset[str] | str | None = None,
                           door: str = "") -> None:
    """Refuse a write that would give ONE `verbatim` to TWO values (#11827 review).

    ⚠️ THE DEFECT THIS CLOSES, measured on an embedded SDK before it existed: a
    Point carrying a money value with its source text
    (``money_props(80_000, "EUR", minor_unit_exponent=2, verbatim="800")``) then
    ``update_point(id, **percent_props(1_200, verbatim="12%"))`` left
    ``amountMinor 80000 / currency EUR / verbatim "12%"`` — the money value's
    evidence now quotes a percentage, and ``read_money_value`` still answers
    ``("EUR", 80000, 2)``, so nothing complained. A `verbatim` that no longer
    quotes the amount's source is the normalize-then-lose-the-source path
    `tortoise/money.py` exists to prevent, arriving through a new shape.

    ``verbatim`` is ONE property on the carrier, so it is ONE value's evidence.
    Three refusals, each naming the field and the way out:

      1. the bag itself names two shapes AND a ``verbatim`` — one evidence field
         cannot describe two values, so the bag is not writable whatever the
         node holds (the CREATE half: no ``stored`` is needed);
      2. the bag carries ``basisPoints`` + ``verbatim`` and the CARRIER already
         holds a money value — the write would overwrite that value's evidence;
      3. symmetrical to 2, with the shapes exchanged.

    ⚠️ WHAT IT DELIBERATELY DOES NOT REFUSE: a carrier holding two values whose
    evidence is not contested — ``{basisPoints}`` alone on a money-bearing node
    overwrites nothing (a machine-supplied percent carries no ``verbatim``, D4b),
    so a node can hold both shapes and the rule stays about the EVIDENCE, not
    about how many values a carrier may have. A blanket "one shape per carrier"
    rule would refuse a perfectly readable state (a budget and a target
    percentage on one node) — over-blocking for a defect that is not there.

    ``stored`` is ``{"money", "percent"}`` names for the shapes the node already
    carries (probed by the caller, which owns the identity keys), or ``None``
    when the caller is a CREATE (nothing is stored, so the bag is the state).
    """
    named_money = bool(set(named) & set(_MONEY_MEMBERS))
    named_percent = BASIS_POINTS in named
    supplied = named.get(VERBATIM)
    if supplied is None:
        return
    held = frozenset({stored} if isinstance(stored, str) else (stored or ()))
    where = f" ({door})" if door else ""

    def refuse(reason: str) -> None:
        raise PercentValueError(
            f"shared source evidence refused at the write boundary{where}: "
            f"{reason}. `{VERBATIM}` is ONE property on the carrier, so it can "
            "be the source text of ONE value — write each value's evidence on "
            "its own carrier, or drop the evidence from this bag (#11915, "
            "#11827)."
        )

    if named_money and named_percent:
        refuse("this bag names a money value and a percent value AND supplies a "
               "single 'verbatim' for both")
    if named_percent and "money" in held:
        refuse("this write would overwrite the source text of the money value "
               "already on this carrier, whose 'verbatim' quotes the amount's "
               "own prose")
    if named_money and "percent" in held:
        refuse("this write would overwrite the source text of the percent value "
               "already on this carrier")


def refuse_malformed_percent_in_payload(payload, *, door: str = "") -> None:
    """Refuse a malformed percent value ANYWHERE in a journal RECORD (#11915).

    The journal-first seam (``api.EventAPI._emit``) does not hand its props in
    one flat bag the way the SDK doors do: it passes the RECORD it is about to
    journal, and ``PointAdded`` nests the whole record under ``point=`` while
    ``EventRecorded`` also carries ``uses[]`` items. A top-level-only gate is
    measurably INERT on the Point door — that is #11836 review Finding 1 for
    money, and the shape is identical here, so the walk is the same.

    ⚠️ ENGAGEMENT: each mapping the walk reaches is judged by
    :func:`refuse_malformed_percent`, so a nested mapping is graded only if it
    carries ``basisPoints`` at all; every record this seam journals is a full
    snapshot, so the bag IS the state.

    ⚠️ FAILURE DIRECTION — REFUSE, not admit: a nested mapping that carries a
    ``basisPoints`` for a non-percent purpose would be refused too, because the
    bag alone cannot say which it is. That is the correct direction for a write
    gate — an allowlist of which nesting carries a real value is a second copy
    of the fold's knowledge that goes stale silently, and a stale ALLOWLIST
    fails OPEN.

    ⚠️ THE DEPTH REFUSAL IS NOT ABOUT A PERCENT VALUE, and its message must not
    pretend otherwise: the walk refuses a payload it could not finish reading
    EVEN WHEN that payload carries no percent at all — it says what it measured
    (the depth) and what it could not do, never a member it never found.
    ``PAYLOAD_MAX_DEPTH`` is money's bound, shared rather than copied so the two
    value shapes cannot drift on payload SHAPE.
    """
    seen: set[int] = set()
    stack: list[tuple[object, int]] = [(payload, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > PAYLOAD_MAX_DEPTH:
            where = f" ({door})" if door else ""
            raise PercentValueError(
                f"the payload nests deeper than {PAYLOAD_MAX_DEPTH} levels, so "
                f"this gate cannot see what it holds — refused at the write "
                f"boundary{where}: flatten the payload or write the value "
                "through the props door (#11915)")
        if isinstance(node, (Mapping, list, tuple)):
            if id(node) in seen:
                continue
            seen.add(id(node))
        if isinstance(node, Mapping):
            if BASIS_POINTS in node:
                refuse_malformed_percent(node, door=door)
                # …and the bag-level half of the evidence clash (#11827 review):
                # a record naming BOTH shapes with ONE `verbatim` cannot be
                # written whatever the store holds, so the payload walk refuses
                # it here. The carrier-state half (a `verbatim` overwriting a
                # value already stored) needs the target node, which this walk
                # does not read — that gate lives at the props doors.
                refuse_shared_evidence(node, door=door)
            stack.extend(
                (value, depth + 1) for value in node.values()
                if isinstance(value, (Mapping, list, tuple)))
        elif isinstance(node, (list, tuple)):
            stack.extend(
                (item, depth + 1) for item in node
                if isinstance(item, (Mapping, list, tuple)))
