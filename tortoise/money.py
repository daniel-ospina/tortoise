"""Money as a flat, currency-safe structural value (#2782).

Design of record: ``docs/plans/2026-09-10-ws-c-numeric-normalization.md`` (the
WS-C design, owner-approved; its Option (a) ruling of 2026-10-07 is recorded on
``#2817``). This module is the **consume** half of the design's §7.2 step 5 —
the sum-and-ratio traversal the grounded requirement needs. It is deliberately
NOT the prose→value derivation: that is the S5 normalizer owned by ``#2817``
(``tortoise/value_normalizer.py`` on the held ``feat/2817-value-normalizer``
branch). Nothing here parses text.

Shape (design §2.1, §2.4 — a value is a FLAT field on the entity that owns it):

    amountMinor          int   minor units (cents), NEVER a float
    currency             str   ISO 4217 alpha-3
    minorUnitExponent    int   0..4 (JPY=0, USD/BRL/EUR=2, KWD/BHD=3)
    verbatim             str   the exact source text the value came from
    asOf                 str   ISO ``YYYY-MM-DD`` assertion/validity date

FalkorDB has no map property and no decimal type, so the value cannot be a
nested ``{amount, currency}`` object and a ``Decimal`` would be an unindexable
string (design §2.4). That is why these are siblings.

Rounding / format contract (design §2.4, OD2, OD4) — this is the documentation
the issue's indicator (4) asks for, and it is the invariant this module holds:

  * an amount is stored as an INTEGER in minor units; ``10_000`` is R$ 100.00
    at exponent 2. No amount here is ever a float, and no float is accepted;
  * the minor-unit exponent travels WITH the amount (``minorUnitExponent``),
    so a reader never depends on a runtime default table — and two records for
    one currency that disagree on the exponent are not summed, because the
    totals would not be comparable;
  * currency is explicit PER AMOUNT, never a graph-level default; amounts in
    different currencies are never added;
  * sub-minor precision is not represented — the raw text stays in
    ``verbatim`` (design OD4: drop, never round silently).

This module never touches the graph; the read surface that feeds it (and the
write-path acceptance of the field on ``create_entity``) rides the ``#4282``
surface migration, per design §7.1.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

__all__ = [
    "AMOUNT_MINOR",
    "AS_OF",
    "CURRENCY",
    "MINOR_UNIT_EXPONENT",
    "MONEY_VALUE_FIELDS",
    "VERBATIM",
    "MoneyValueError",
    "money_props",
    "ratio_bp",
    "ratio_meets",
    "read_money_value",
    "total_minor",
]

AMOUNT_MINOR = "amountMinor"
CURRENCY = "currency"
MINOR_UNIT_EXPONENT = "minorUnitExponent"
VERBATIM = "verbatim"
AS_OF = "asOf"

#: The five declared sibling fields of one money value, in canonical order.
MONEY_VALUE_FIELDS: tuple[str, ...] = (
    AMOUNT_MINOR, CURRENCY, MINOR_UNIT_EXPONENT, VERBATIM, AS_OF,
)

#: ISO 4217 permits 0, 1, 2, 3 and (CLF/UYW) 4 minor-unit digits.
_MIN_EXPONENT = 0
_MAX_EXPONENT = 4

#: ISO 4217 currency codes are three uppercase letters.
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
#: ``Point._iso_date_prefix``'s contract: a bare ``YYYY-MM-DD`` prefix.
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

_BASIS_POINTS = 10_000


class MoneyValueError(ValueError):
    """A present money field is malformed — never guess, refuse."""


def _validate_amount_minor(value: Any) -> int:
    # ``bool`` is an ``int`` subclass; ``True`` is not an amount.
    if isinstance(value, bool) or not isinstance(value, int):
        raise MoneyValueError(
            f"{AMOUNT_MINOR} must be an int in minor units, "
            f"got {type(value).__name__}: {value!r}")
    return value


def _validate_currency(value: Any) -> str:
    if not isinstance(value, str) or not _CURRENCY_RE.match(value):
        raise MoneyValueError(
            f"{CURRENCY} must be an ISO 4217 alpha-3 code, got {value!r}")
    return value


def _validate_exponent(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) \
            or not (_MIN_EXPONENT <= value <= _MAX_EXPONENT):
        raise MoneyValueError(
            f"{MINOR_UNIT_EXPONENT} must be an int in "
            f"[{_MIN_EXPONENT}, {_MAX_EXPONENT}], got {value!r}")
    return value


def _validate_verbatim(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise MoneyValueError(
            f"{VERBATIM} must be the exact non-empty source text, "
            f"got {value!r}")
    return value


def _validate_as_of(value: Any) -> str:
    if not isinstance(value, str) or not _DATE_PREFIX_RE.match(value):
        raise MoneyValueError(
            f"{AS_OF} must be an ISO YYYY-MM-DD date (design OD3 leaves it "
            f"absent when the text states none), got {value!r}")
    return value


def money_props(amount_minor: int, currency: str, *,
                minor_unit_exponent: int,
                verbatim: str | None = None,
                as_of: str | None = None) -> dict[str, Any]:
    """Build the flat money value fields for one amount.

    Validates at the boundary (never guess), so a malformed amount can neither
    be constructed nor reach a write. Optional fields are OMITTED when absent
    rather than written as ``None`` — the projection's ``SET n += $props``
    drops ``None`` anyway, and an explicit absence is what OD3 asks for.
    """
    props: dict[str, Any] = {
        AMOUNT_MINOR: _validate_amount_minor(amount_minor),
        CURRENCY: _validate_currency(currency),
        MINOR_UNIT_EXPONENT: _validate_exponent(minor_unit_exponent),
    }
    if verbatim is not None:
        props[VERBATIM] = _validate_verbatim(verbatim)
    if as_of is not None:
        props[AS_OF] = _validate_as_of(as_of)
    return props


def read_money_value(record: Mapping[str, Any]) -> tuple[str, int, int] | None:
    """Read one record's ``(currency, amountMinor, exponent)``.

    Returns ``None`` when the record carries no money at all (no
    ``amountMinor`` and no ``currency``) — a non-money node in a mixed set.
    Raises :class:`MoneyValueError` when a field IS present but malformed: a
    present-but-wrong amount is a data error, never something to average over.
    """
    has_amount = record.get(AMOUNT_MINOR) is not None
    has_currency = record.get(CURRENCY) is not None
    if not has_amount and not has_currency:
        return None
    currency = _validate_currency(record.get(CURRENCY))
    amount = _validate_amount_minor(record.get(AMOUNT_MINOR))
    exponent = _validate_exponent(record.get(MINOR_UNIT_EXPONENT))
    return currency, amount, exponent


def total_minor(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Sum ``amountMinor`` per currency across ``records``.

    The sum is exact integer arithmetic at a known scale, so it is safe to
    compare, divide and threshold. Currency is never mixed; a currency whose
    included records disagree on ``minorUnitExponent`` is REFUSED (raise),
    because summing ``1.00`` and ``1.000`` would be a silent unit error. A
    currency is never converted (FX is out of scope, design §4).
    """
    totals: dict[str, int] = {}
    exponents: dict[str, int] = {}
    for record in records:
        read = read_money_value(record)
        if read is None:
            continue
        currency, amount, exponent = read
        if currency in exponents and exponents[currency] != exponent:
            raise MoneyValueError(
                f"{currency} has mixed {MINOR_UNIT_EXPONENT} values "
                f"({exponents[currency]} and {exponent}); the totals are not "
                f"comparable — refusing to sum rather than guess")
        exponents[currency] = exponent
        totals[currency] = totals.get(currency, 0) + amount
    return totals


def ratio_bp(part_minor: int, whole_minor: int) -> int | None:
    """``part/whole`` in basis points (integer, floored).

    ``None`` when ``whole_minor`` is 0 — the ratio is undefined, not 0%. Both
    operands must be non-negative ints in the SAME currency (this function
    cannot see the currency, so the caller must not mix).
    """
    part = _validate_amount_minor(part_minor)
    whole = _validate_amount_minor(whole_minor)
    if part < 0 or whole < 0:
        raise MoneyValueError("ratio operands must be non-negative")
    if whole == 0:
        return None
    return (part * _BASIS_POINTS) // whole


def ratio_meets(part_minor: int, whole_minor: int,
                threshold_bp: int) -> bool | None:
    """Whether ``part/whole`` reaches ``threshold_bp`` — the unlock test.

    The comparison is exact (no truncation): ``75% of committed`` at exactly
    ``7500`` bp MEETS ``7500`` bp. Returns ``None`` when the ratio is undefined
    (``whole_minor`` is 0), so a caller cannot read "no evidence" as "met".
    """
    part = _validate_amount_minor(part_minor)
    whole = _validate_amount_minor(whole_minor)
    threshold = _validate_amount_minor(threshold_bp)
    if part < 0 or whole < 0:
        raise MoneyValueError("ratio operands must be non-negative")
    if threshold < 0:
        raise MoneyValueError("threshold_bp must be non-negative")
    if whole == 0:
        return None
    return part * _BASIS_POINTS >= threshold * whole
