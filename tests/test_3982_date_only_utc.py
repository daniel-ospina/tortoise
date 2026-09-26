"""#3982 — a date-only value means UTC midnight, not the reader's midnight.

⛔ CLASS B — every test below answers BOTH questions:

(1) **What value makes this test fail?** The reader's timezone. Each test that
    exists to pin the fix runs under a FORCED non-UTC ``TZ`` (``EST5EDT``,
    UTC-5). This is not decoration — it is the falsifier. Under the old
    behaviour a date-only value was parsed to a naive datetime whose
    ``.timestamp()`` read it as LOCAL, so:
        TZ=UTC      -> date-only == the same date at UTC midnight  (PASSES)
        TZ=EST5EDT  -> they differ by 5h                           (FAILS)
    A test that ran only under the ambient zone would pass on any CI runner
    set to UTC and could never fail, which is the cardinal sin. So the fixture
    CONTAINS the row where the value is reachable: a deliberately offset zone.

(2) **Does the fixture contain a row where that value is reachable?** Yes —
    ``_EST``/``_UTC`` below are real POSIX zones applied with ``time.tzset()``,
    and the difference is asserted to be non-zero BEFORE the equality is
    asserted, so a host where the zone switch silently failed cannot report a
    false green.

The governing standard, quoted, because anchoring is not a preference here —
ECMA-262 §21.4.3.2 (published also as ISO/IEC 16262):

    "When the UTC offset representation is absent, date-only forms are
     interpreted as a UTC time and date-time forms are interpreted as a local
     time."

Corroborated from the other direction by W3C XML Schema 1.1, which rules that
a zone-less value is not one moment (a calendar day spans up to 52 hours) and
is therefore incomparable with a zoned value. #153 set "no zone given means
UTC" for a sibling date field; this makes the primitive agree with it.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager

import pytest

from tortoise.search_engine import _created_sort_key

#: A REAL UTC-5 zone (with US DST rules). POSIX TZ strings are honoured by
#: ``time.tzset()`` on the platforms this suite runs on; the tests assert the
#: zone actually took effect, so a platform that ignored it fails loudly
#: rather than passing vacuously.
_EST = "EST5EDT"
_UTC = "UTC"

#: The same instant stated three ways. The first is the value under test.
_DATE_ONLY = "2026-06-10"
_AS_UTC = "2026-06-10T00:00:00+00:00"
_AS_NAIVE_DATETIME = "2026-06-10T00:00:00"


@contextmanager
def _forces_tz(name: str):
    """Run a block under a forced timezone, restoring the ambient one.

    ``time.tzset()`` re-reads ``TZ`` for the C library; Python's ``datetime``
    uses it, which is precisely the dependency #3982 is about.
    """
    prev = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prev
        time.tzset()


def _zone_shift_seconds() -> float:
    """Guard: prove the fixture can reach the failing value.

    Returns the measured shift a forced ``EST5EDT`` applies to a naive
    datetime's local reading. If forcing the zone did not actually move the
    local zone, every equality below would hold for the wrong reason and the
    suite would be green on a broken implementation — so the shift is
    asserted non-zero, and the CALLER uses this same measured value rather
    than a hardcoded offset.

    Measured, not assumed: on the 10 June fixture ``EST5EDT`` is in DST, so
    the shift is 4h (UTC-4), not the 5h the zone's name suggests. Hardcoding
    5h is how this guard first failed — the check must describe the zone it
    actually got, not the one it expected.
    """
    with _forces_tz(_UTC):
        utc_naive = _created_sort_key(_AS_NAIVE_DATETIME)[1]
    with _forces_tz(_EST):
        est_naive = _created_sort_key(_AS_NAIVE_DATETIME)[1]
    shift = abs(utc_naive - est_naive)
    assert shift > 3600, (
        "fixture cannot fail: forcing TZ=EST5EDT did not shift the local "
        "reading of a naive datetime, so the date-only assertions would pass "
        f"vacuously (utc={utc_naive}, est={est_naive})"
    )
    return shift


def test_date_only_is_utc_midnight_on_a_non_utc_host():
    """THE falsifier: on a UTC-5 host a date-only value is still UTC midnight.

    Pre-fix this is the failing row — the value was read as local midnight,
    5 hours off. This is the test that must go red without the change.
    """
    _sanity = _zone_shift_seconds()
    with _forces_tz(_EST):
        assert _created_sort_key(_DATE_ONLY)[1] == _created_sort_key(_AS_UTC)[1]


def test_date_only_does_not_move_when_the_reader_moves():
    """The instant a date-only value names is a property of the data, not the reader."""
    _zone_shift_seconds()
    with _forces_tz(_UTC):
        on_utc = _created_sort_key(_DATE_ONLY)[1]
    with _forces_tz(_EST):
        on_est = _created_sort_key(_DATE_ONLY)[1]
    assert on_utc == on_est


def test_date_only_is_exactly_utc_midnight():
    """Pins the anchor itself, not merely agreement with another parse path.

    ``_AS_UTC`` and ``_DATE_ONLY`` could in principle agree on a wrong value;
    this asserts the absolute epoch so the meaning is nailed down.
    """
    import datetime as _dt

    expected = _dt.datetime(
        2026, 6, 10, tzinfo=_dt.UTC
    ).timestamp()
    with _forces_tz(_EST):
        assert _created_sort_key(_DATE_ONLY)[1] == expected


def test_date_time_without_offset_keeps_the_local_reading():
    """The OTHER half of the same clause — do not over-apply the fix.

    ECMA-262 §21.4.3.2 says date-only forms are UTC and date-TIME forms are
    local. A date-time carrying no offset must therefore still read locally;
    a fix that anchored every zone-less value would be wrong by the same
    standard it cites. This test fails if the change is over-broad.
    """
    shift = _zone_shift_seconds()
    with _forces_tz(_UTC):
        utc = _created_sort_key(_AS_NAIVE_DATETIME)[1]
    with _forces_tz(_EST):
        est = _created_sort_key(_AS_NAIVE_DATETIME)[1]
    assert abs(utc - est) == pytest.approx(shift, abs=1), (
        "a zone-less date-TIME must keep its local reading per the same clause"
    )


def test_explicit_offset_is_still_honoured():
    """Unchanged behaviour: a stated offset wins over any anchoring."""
    with _forces_tz(_EST):
        assert _created_sort_key("2026-06-10T09:30:00+02:00")[1] == _created_sort_key(
            "2026-06-10T07:30:00+00:00"
        )[1]


def test_z_suffix_is_still_honoured():
    """``Z`` is an explicit offset, so it is untouched by the date-only rule."""
    with _forces_tz(_EST):
        assert _created_sort_key("2026-06-10T00:00:00Z")[1] == _created_sort_key(
            _AS_UTC
        )[1]


def test_ordering_is_stable_across_hosts():
    """The reason it matters: window coverage decided by the reader's machine.

    A date-only fact and a zoned fact one second later must order the same way
    wherever the question is asked. Pre-fix the 5-hour skew on a non-UTC host
    could invert or collapse this ordering.
    """
    _zone_shift_seconds()
    earlier, later = _DATE_ONLY, "2026-06-10T00:00:01+00:00"
    with _forces_tz(_UTC):
        assert _created_sort_key(earlier)[1] < _created_sort_key(later)[1]
    with _forces_tz(_EST):
        assert _created_sort_key(earlier)[1] < _created_sort_key(later)[1]


def test_epoch_and_iso_still_compare_by_real_instant():
    """Existing contract (#1353): mixed numeric/ISO graphs compare by instant."""
    with _forces_tz(_EST):
        numeric = 1_780_000_000
        assert _created_sort_key(numeric)[0] == 0
        assert _created_sort_key(_DATE_ONLY)[0] == 0
        assert _created_sort_key(numeric)[1] != _created_sort_key(_DATE_ONLY)[1]


def test_unparseable_values_still_bucket_last():
    """Existing contract: junk sorts last, deterministically."""
    assert _created_sort_key("not-a-date") == (1, "not-a-date")
    assert _created_sort_key("not-a-date") > _created_sort_key(_DATE_ONLY)
