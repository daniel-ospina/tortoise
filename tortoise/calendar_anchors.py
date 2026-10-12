"""#2655 — deterministic calendar-anchor resolution for relative-date lookups.

The census ``relative-date-lookup`` class (6/133 rows,
``tests/_assembly_census.json``) is, MEASURED, only ~half a calendar-anchored
class: three of its six rows name a resolvable day ("last Saturday", "on
Valentine's day", "the past weekend"), one is an ``ago``-offset day (the
#2652 class) and two name no day at all (ordering-within-month and
frequency/count). This module is the deterministic resolution core for the
three that do — re-measured 2026-10-10, never read off the label.

It is deliberately PURE: no graph, no model, no IO, no clock. The caller
supplies ``as_of`` (the question's own ``question_date``) and gets concrete
``datetime.date`` values back. Two consequences that are the point of the
module:

* **As-of discipline** — a resolved day is NEVER after ``as_of``. A fixed-date
  holiday whose occurrence in ``as_of``'s year is still ahead rolls back one
  year; an explicit year that is ahead ABSTAINS rather than speculating. A
  relative-date lookup answers "what happened on <day>?", so a future day is a
  different question.
* **Abstain, never guess** — no ``as_of``, no anchor, more than one DISTINCT
  anchor, or an unrepresentable date all return ``None``. The caller falls
  through to the legacy lane byte-identically (the assembly lane's R1 spirit).

Two anchor families, matching the issue's scope exactly:

* **fixed-date holidays** — a ``(month, day)`` table (Valentine's day, New
  Year's day/eve, Christmas day/eve, Halloween, Independence day, St Patrick's
  day, April Fools' day, Veterans day), with an optional explicit year;
* **weekday-of-``as_of`` offsets** — ``last <weekday>`` / ``this past
  <weekday>`` / ``past <weekday>`` (the most recent such weekday STRICTLY
  before ``as_of``) and ``last/this past/the past/this weekend`` (the most
  recent Saturday on or before ``as_of``, plus its Sunday when that Sunday is
  not after ``as_of``).

Named residuals (recorded so they are not mistaken for verified):

* **movable feasts** (Thanksgiving, Easter, Labor day …) need nth-weekday-of-
  month arithmetic, not a ``(month, day)`` row — a v1 omission, not a silent
  gap;
* **non-anchor relative offsets** (``a week ago``, ``yesterday``, ``last
  week``) are NOT resolved here — they belong to the ``ago``-relative lane
  (#2652), and the assembler's classifier keeps rejecting them.

The holiday table is a fixed, reviewed literal (not derived): a computed
holiday calendar is a dependency this shape does not need, and an unreviewed
derivation is exactly how an as-of boundary drifts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

__all__ = [
    "FIXED_DATE_HOLIDAYS",
    "AnchorSpan",
    "CalendarAnchor",
    "CalendarAnchorKind",
    "find_anchor_spans",
    "resolve_calendar_anchor",
]


class CalendarAnchorKind(StrEnum):
    """The anchor family a matched surface belongs to."""

    FIXED_DATE = "fixed-date"
    WEEKDAY = "weekday"
    WEEKEND = "weekend"


#: Fixed-date holidays: surface alias → ``(month, day)``. Multiple aliases may
#: map to the same day; the ALIAS is the match surface and the canonical
#: ``label`` of a resolved anchor (so the rendered header names what the
#: question named).
FIXED_DATE_HOLIDAYS: dict[str, tuple[int, int]] = {
    "valentine's day": (2, 14),
    "valentines day": (2, 14),
    "valentine's": (2, 14),
    "valentines": (2, 14),
    "new year's day": (1, 1),
    "new years day": (1, 1),
    "new year's eve": (12, 31),
    "new years eve": (12, 31),
    "new year's": (1, 1),
    "christmas day": (12, 25),
    "christmas eve": (12, 24),
    "christmas": (12, 25),
    "halloween": (10, 31),
    "independence day": (7, 4),
    "the fourth of july": (7, 4),
    "fourth of july": (7, 4),
    "the 4th of july": (7, 4),
    "4th of july": (7, 4),
    "st. patrick's day": (3, 17),
    "st patrick's day": (3, 17),
    "saint patrick's day": (3, 17),
    "april fools' day": (4, 1),
    "april fools day": (4, 1),
    "veterans day": (11, 11),
    "veterans' day": (11, 11),
}

_WEEKDAYS: tuple[str, ...] = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday",
)

# Alternation is longest-alias-first: Python's `|` is LEFTMOST-first, not
# longest-match, so an unsorted table would let "valentine's" swallow
# "valentine's day" and lose the canonical label.
_HOLIDAY_ALT = "|".join(
    re.escape(a) for a in sorted(FIXED_DATE_HOLIDAYS, key=len, reverse=True))
_WEEKDAY_ALT = "|".join(_WEEKDAYS)
# `last`/`this past`/`past` — the relative weekday forms the issue names. A
# bare "on Tuesday" is deliberately NOT an anchor: which Tuesday is not
# recoverable from a single weekday name, and a wrong-week guess would fire the
# lane on a wrong day.
_ANCHOR_RE = re.compile(
    r"\b(?P<holiday>" + _HOLIDAY_ALT + r")(?:\s*,?\s+(?:of\s+)?"
    r"(?P<year>20\d{2}))?(?![\w'])"
    r"|\b(?P<weekday_prep>last|this\s+past|past)\s+(?P<weekday>" + _WEEKDAY_ALT
    + r")\b"
    r"|\b(?P<weekend_prep>last|this\s+past|the\s+past|past|this)\s+weekend\b",
    re.IGNORECASE)

_WEEKDAY_INDEX = {name: i for i, name in enumerate(_WEEKDAYS)}
# Saturday = index 5 in a Monday-first week (``datetime.date.weekday()``).
_SATURDAY = 5


@dataclass(frozen=True)
class AnchorSpan:
    """One matched anchor SURFACE in the question text (no date resolution).

    Date-free on purpose: the classifier must decide "is this a date-anchored
    lookup?" from text alone (it has no ``question_date``), and only the fired
    path resolves the span against ``as_of``.
    """

    label: str            # canonical label ("valentine's day", "last saturday")
    kind: CalendarAnchorKind
    text: str             # the exact matched surface, as written
    start: int
    end: int
    year: int | None = None   # explicit year, when the question names one


@dataclass(frozen=True)
class CalendarAnchor:
    """A resolved anchor: concrete day(s) + the surface that named them."""

    label: str
    kind: CalendarAnchorKind
    days: tuple[date, ...]
    surface: str

    @property
    def day(self) -> date:
        """The primary day (a weekend anchor's Saturday)."""
        return self.days[0]


def find_anchor_spans(question: str) -> tuple[AnchorSpan, ...]:
    """All non-overlapping calendar-anchor surfaces in ``question`` (in order).

    Deterministic and text-only. Returns ``()`` for empty/garbage input or a
    question that names no calendar anchor. More than one returned span is a
    caller-level ambiguity signal (a "between Christmas and New Year's day …"
    question must abstain) — this function never judges that, it reports.
    """
    if not question or not question.strip():
        return ()
    out: list[AnchorSpan] = []
    for m in _ANCHOR_RE.finditer(question):
        if m.group("holiday"):
            label = " ".join(m.group("holiday").lower().split())
            out.append(AnchorSpan(
                label=label,
                kind=CalendarAnchorKind.FIXED_DATE,
                text=m.group(0),
                start=m.start(),
                end=m.end(),
                year=int(m.group("year")) if m.group("year") else None))
        elif m.group("weekday"):
            wd = m.group("weekday").lower()
            out.append(AnchorSpan(
                label=f"last {wd}",
                kind=CalendarAnchorKind.WEEKDAY,
                text=m.group(0), start=m.start(), end=m.end()))
        else:
            out.append(AnchorSpan(
                label="the past weekend",
                kind=CalendarAnchorKind.WEEKEND,
                text=m.group(0), start=m.start(), end=m.end()))
    return tuple(out)


def _most_recent_weekday(as_of: date, weekday: int) -> date:
    """The most recent ``weekday`` STRICTLY before ``as_of``.

    "last Saturday" asked on a Saturday means the PREVIOUS Saturday — a
    same-day reading would silently answer a different question than the one
    the phrase names.
    """
    back = (as_of.weekday() - weekday) % 7
    if back == 0:
        back = 7
    return as_of - timedelta(days=back)


def _weekend_days(as_of: date) -> tuple[date, ...]:
    """The most recent Saturday on or before ``as_of``, plus its Sunday when
    that Sunday is not after ``as_of`` (so a Sunday question sees the weekend
    it is standing in, and no day is ever in the future)."""
    sat = as_of - timedelta(days=(as_of.weekday() - _SATURDAY) % 7)
    sun = sat + timedelta(days=1)
    return (sat, sun) if sun <= as_of else (sat,)


def _fixed_date_days(span: AnchorSpan, as_of: date) -> tuple[date, ...]:
    month, day = FIXED_DATE_HOLIDAYS[span.label]
    if span.year is not None:
        # An explicit year is the question's own claim: honour it or refuse.
        # A FUTURE explicit year abstains (never a fabricated rollback to a
        # year the question did not name).
        try:
            cand = date(span.year, month, day)
        except ValueError:
            return ()
        return (cand,) if cand <= as_of else ()
    year = as_of.year
    while year >= 1:
        try:
            cand = date(year, month, day)
        except ValueError:      # e.g. a Feb-29 alias on a non-leap year
            year -= 1
            continue
        if cand <= as_of:
            return (cand,)
        year -= 1
    return ()


def resolve_calendar_anchor(question: str,
                            as_of: date | None) -> CalendarAnchor | None:
    """Resolve ``question``'s calendar anchor to concrete day(s), as of ``as_of``.

    Returns ``None`` (abstain) when: ``as_of`` is absent (no clock is ever read
    here — the caller owns the question's date), no anchor surface is present,
    two or more DISTINCT anchors are present, or the named day is not
    representable as a past-or-present date.
    """
    if as_of is None or not isinstance(as_of, date):
        return None
    if not question or not question.strip():
        return None
    spans = find_anchor_spans(question)
    if not spans:
        return None
    distinct = {(s.kind, s.label) for s in spans}
    if len(distinct) > 1:
        return None
    span = spans[0]
    if span.kind is CalendarAnchorKind.FIXED_DATE:
        days = _fixed_date_days(span, as_of)
    elif span.kind is CalendarAnchorKind.WEEKDAY:
        weekday_name = span.label.split(" ", 1)[1]
        days = (_most_recent_weekday(as_of, _WEEKDAY_INDEX[weekday_name]),)
    else:
        days = _weekend_days(as_of)
    if not days:
        return None
    return CalendarAnchor(label=span.label, kind=span.kind, days=days,
                          surface=span.text)
