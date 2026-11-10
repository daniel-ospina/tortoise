"""#11646 — the ONE resolver for a stored date value's calendar day.

Three shipped seams read a stored date by assuming ISO, so the ingest
producer's real shape — ``2023/05/20 (Sat) 02:21`` — silently parsed to
``None``: the assembly spine dated every eval row UNDATED, the shipped
valid-time selection evaluated a *present* question date at the wall clock,
and the C6 dense anchor injected nothing. Each had grown its own parser and
they had drifted; this module is the single home for the shape set.

The shape set is measured, not assumed: the LongMemEval corpus emits
``YYYY/MM/DD (Day) HH:MM`` for **500/500** questions and for every
``haystack_dates`` entry — **0** ISO (re-measured for #11646; #3011 measured
the same shape 23,867/23,867 on the frozen dataset). Around it, this repo's
own writers add ISO dates (``YYYY-MM-DD``), ISO timestamps (``Z`` or an
offset), and the legacy compact (``YYYYMMDD``) / ISO-week (``YYYY-Www-D``)
spellings ``date.fromisoformat`` accepts; ``supersede_point`` can copy a
numeric epoch into a window. All of them are accepted here, and the result is
always validated through ``date``, so ``2026-13-45`` is undated rather than a
fabricated date.

⛔ MONTH GRANULARITY IS NOT COERCED. A value naming only ``YYYY/MM`` (or
``2026-09``) has NO day, and this resolver returns ``None`` rather than
inventing the 1st. Inventing one would silently place a month-granularity
value at a specific position inside that month — ahead of every row dated
within it — and no in-repo producer emits that shape, so the "fix" could only
ever invent days for hand-built input. Undated is the honest answer; the
caller decides what undated means (#8710's rule: unresolvable ⇒ no bound,
never matches-nothing).

TWO DAY SEMANTICS, both required, so the caller states which it wants:

* :func:`calendar_date` — the calendar day **as written**. The default
  (``utc_truncate=False``) is the prefix reading every already-fixed surface
  uses (``coverage_loop._calendar_date``, ``sdk._iso_date10``): a session
  stamped ``2026-09-01T00:30+02:00`` is the 1st, the day the dataset says.
* ``utc_truncate=True`` — an instant truncates in **UTC**, which is what the
  assembly ladder needs for its as-of day boundary (a host-local boundary
  would scramble the equality day and the Task-5 byte-goldens). Date-only
  values and naive timestamps keep their written day either way; only an
  OFFSET-bearing instant can differ, and only across a day boundary.

:func:`calendar_datetime` is the instant view, for a consumer that ORDERS
rather than buckets (``search_engine._created_sort_key``). It mirrors the
#3982 anchoring rule: a date-only value is UTC midnight (a stated offset
wins), while a zone-less date-TIME keeps the LOCAL reading that rule
deliberately left alone.

⛔ THE EPOCH ASYMMETRY IS A RECORDED RULING, not an oversight. A NUMERIC epoch
is a date to both functions. An epoch spelled as a STRING (``"1900000000"``)
is a date to :func:`calendar_date` — ``time_aware._as_date`` has always read
it that way, and naming a day makes no instant claim — but NOT to
:func:`calendar_datetime`, because #9202 (owner ruling, ``ONTOLOGY.md`` §4.7)
makes a present-but-un-orderable window end ``END_UNKNOWN`` and names the
number as the fix; dating the string here would be a fourth notion of "names
an instant", the exact defect #9202 exists to remove.

The undated sentinel (``tools/longmem_eval/ingest.UNDATED_SENTINEL``) is a
POSITIVE date that means "no date". It is EXPORTED, never applied: callers
differ deliberately — the assembly ladder floors ``<= 1970-01-01`` (any
pre-1970 artifact is unusable there), ``coverage_loop`` rejects exactly that
date, and the ``time_aware`` seams strip nothing.

Deliberately NOT routed here: ``subgraph_render._parse_date`` (a FROZEN render
spec, padded-only by contract) and ``sdk._iso_date10`` (already correct, and
its callers render a session label, not a bound).
"""
from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, timedelta, timezone

__all__ = [
    "UNDATED_SENTINEL_DATE",
    "calendar_date",
    "calendar_datetime",
]

#: The ingest lane's "no date" marker — a POSITIVE value that means UNDATED,
#: in every ISO spelling of its calendar date. Mirrored (not imported) from
#: ``tools/longmem_eval/ingest`` so the product module never imports the eval
#: harness: a fabricated 1970 date is worse than no date, and as a BOUND it
#: turns an unresolvable input into a window that matches (or excludes)
#: everything.
UNDATED_SENTINEL_DATE = "1970-01-01"

#: The calendar-date PREFIX — the shapes both date sources actually arrive in,
#: ISO (``2026-09-01`` / ``2026-09-01T00:00:00Z``) and the producer's
#: ``2023/05/20 (Sat) 03:29``. Month/day are ``\d{1,2}``: an UNPADDED
#: ``2023/4/9`` is the same calendar date, and reading it as undated would drop
#: that session from a bounded span (the session-level #8710 defect). The
#: trailing ``(?!\d)`` keeps a 3-digit day (``2023/04/105``) from being
#: truncated to two. Moved here from ``coverage_loop`` (#11646) so the shape
#: set has ONE home instead of one copy per seam.
_CALENDAR_SHAPE_RE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)")

#: A WHOLE-value instant: the calendar prefix, an optional producer weekday
#: label, an optional time of day, and an optional offset. The weekday label
#: (``(Sat)``) is REDUNDANT and is deliberately not checked against the date —
#: a mislabelled day must never make a real date unparseable. Hours/minutes
#: are ``\d{2}`` because that is what ``datetime.fromisoformat`` accepts (it
#: refuses ``T2:5``), so the two agree on what a timestamp is.
_INSTANT_RE = re.compile(
    r"^(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})(?!\d)"
    r"(?:\s*\([A-Za-z]{2,9}\))?"
    r"(?:[T\s]+(?P<hour>\d{2}):(?P<minute>\d{2})"
    r"(?::(?P<second>\d{2})(?:\.(?P<fraction>\d{1,6}))?)?)?"
    r"\s*(?P<offset>[Zz]|[+-]\d{2}:?\d{2})?\s*\Z"
)

#: The date-only spellings ``date.fromisoformat`` accepts beyond the dashed
#: calendar form: compact (``20261020``) and ISO week (``2026-W43-2``,
#: ``2026W432``, week-only). Kept because the assembly ladder has always
#: accepted them — this union must not drop a value that already parsed.
_COMPACT_OR_WEEK_RE = re.compile(r"^(?:\d{8}|\d{4}-?W\d{2}(?:-?\d)?)")

_OFFSET_RE = re.compile(r"^(?P<sign>[+-])(?P<hours>\d{2}):?(?P<minutes>\d{2})$")


def calendar_date(value: object, *, utc_truncate: bool = False) -> date | None:
    """``value``'s calendar day, or ``None`` when it names no day.

    Accepts a ``date``/``datetime``, a numeric epoch, and the string shapes in
    the module docstring. ``None`` means UNDATED (absent, empty, free text, a
    month-granularity value, an out-of-range date, the sentinel when the caller
    strips it) — never a fabricated day, and never an exception.

    ``utc_truncate=True`` truncates an OFFSET-bearing instant in UTC instead of
    reading its written day. See the module docstring for which consumers need
    which; do not flip it without re-reading them.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):  # before date — datetime IS a date
        if utc_truncate and value.tzinfo is not None:
            return value.astimezone(UTC).date()
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return _date_from_epoch(value)
    return _string_date(str(value).strip(), utc_truncate=utc_truncate)


def calendar_datetime(value: object) -> datetime | None:
    """``value``'s instant view, or ``None`` when it orders on no instant.

    A date-only value anchors to UTC midnight (a stated offset wins); a
    zone-less date-TIME stays NAIVE, the reading #3982 deliberately left to
    ``datetime.timestamp()``. A numeric epoch is an instant; an epoch spelled
    as a STRING is not (#9202 — see the module docstring).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, (int, float)):
        return _instant_from_epoch(value)
    return _string_instant(str(value).strip())


# ── internals ───────────────────────────────────────────────────────────────


def _instant_from_epoch(value: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value), UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _date_from_epoch(value: float) -> date | None:
    instant = _instant_from_epoch(value)
    return None if instant is None else instant.date()


def _string_date(s: str, *, utc_truncate: bool) -> date | None:
    """The day a STRING names — the instant grammar first, then the date-only
    spellings, then an epoch spelling (see the #9202 asymmetry)."""
    if not s:
        return None
    match = _INSTANT_RE.match(s)
    if match is not None:
        d = _match_date(match)
        if d is None:
            return None
        if utc_truncate and match.group("hour") is not None:
            naive = _match_naive(match)
            offset = _match_offset(match)
            if naive is not None and offset is not None:
                return naive.replace(tzinfo=offset).astimezone(UTC).date()
        return d
    d = _date_only_shape(s)
    if d is not None:
        return d
    epoch = _as_epoch(s)
    return None if epoch is None else _date_from_epoch(epoch)


def _string_instant(s: str) -> datetime | None:
    if not s:
        return None
    match = _INSTANT_RE.match(s)
    if match is not None:
        naive = _match_naive(match)
        if naive is None:
            return None
        if match.group("hour") is None:
            # date-only: UTC midnight, a stated offset wins (#3982)
            return naive.replace(tzinfo=_match_offset(match) or UTC)
        offset = _match_offset(match)
        return naive if offset is None else naive.replace(tzinfo=offset)
    d = _date_only_shape(s)
    if d is not None:
        return datetime(d.year, d.month, d.day, tzinfo=UTC)
    return None


def _date_only_shape(s: str) -> date | None:
    """Compact / ISO-week date-only spellings, validated by the stdlib."""
    match = _COMPACT_OR_WEEK_RE.match(s)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(0))
    except ValueError:
        return None


def _as_epoch(s: str) -> float | None:
    try:
        epoch = float(s)
    except (ValueError, TypeError):
        return None
    return epoch if math.isfinite(epoch) else None


def _match_date(match: re.Match[str]) -> date | None:
    try:
        return date(int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")))
    except ValueError:
        return None


def _match_naive(match: re.Match[str]) -> datetime | None:
    d = _match_date(match)
    if d is None:
        return None
    hour = match.group("hour")
    try:
        return datetime(
            d.year, d.month, d.day,
            0 if hour is None else int(hour),
            int(match.group("minute") or 0),
            int(match.group("second") or 0),
            int((match.group("fraction") or "0").ljust(6, "0")),
        )
    except ValueError:
        return None


def _match_offset(match: re.Match[str]) -> timezone | None:
    raw = match.group("offset")
    if not raw:
        return None
    if raw in ("Z", "z"):
        return UTC
    parts = _OFFSET_RE.match(raw)
    if parts is None:
        return None
    hours, minutes = int(parts.group("hours")), int(parts.group("minutes"))
    if hours > 23 or minutes > 59:
        return None
    delta = timedelta(hours=hours, minutes=minutes)
    return timezone(-delta if parts.group("sign") == "-" else delta)
