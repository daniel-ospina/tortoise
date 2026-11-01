"""#2350 (E-prec) — per-end validity precision + the 3-state window end.

The additive precision layer on #1538's ``validFrom``/``validTo``:

  * ``tortoise/validity.py`` — the vocabulary + validator + the ONE read
    projection home;
  * the write boundary (``create_point`` / ``update_point`` /
    ``supersede_point`` / ``invalidate_point``);
  * the 3-state end on every window-consuming read path — most importantly
    that an ended-unknown window is NOT read as still-true;
  * rebuild parity (precision survives a JSONL wipe+replay verbatim) and the
    no-re-stamp guarantee for pre-existing windows.

Runnable with:
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
      tests/test_2350_validity_precision.py -q
"""
from __future__ import annotations

import datetime
import inspect
import os

import pytest

from tortoise.sdk import TortoiseSDK

A_DATE = "2024-06-01"


def _utc_today() -> datetime.date:
    """#9406: the window walk's day boundary is a UTC calendar day.

    ``assembly._norm_date`` truncates an aware instant in **UTC** — its note
    is explicit: *"aware instants truncate in UTC — NEVER the machine-local
    zone (a host-dependent day-boundary would scramble the as-of equality
    boundary)"* — and every product caller names the day from the UTC clock
    (``ask_lane``: ``datetime.now(UTC).strftime("%Y-%m-%d")``,
    ``time_aware.resolve_question_date``: ``datetime.now(UTC).date()``).

    A local ``date.today()`` asserts a day boundary the product denies, so on
    a box whose local date LAGS UTC (any US zone) these three tests went red
    for the hours between UTC midnight and local midnight — a clock reading,
    not a state reading: 3 failed / 35 passed with ``TZ=America/New_York``,
    38 passed with ``TZ=UTC``, same tree.
    """
    return datetime.datetime.now(datetime.UTC).date()


@pytest.fixture
def lane(tmp_path):
    """(sdk, events_dir) with the journal wired so rebuild parity is testable."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _props(sdk: TortoiseSDK, pid: str) -> dict:
    row = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)",
        params={"id": pid}).result_set
    assert row, f"point {pid} missing"
    return dict(row[0][0])


def _window_props(sdk: TortoiseSDK, pid: str) -> dict:
    p = _props(sdk, pid)
    return {k: p.get(k) for k in
            ("validFrom", "validTo", "validFromPrecision", "validToPrecision")}


# ── the vocabulary + validator (pure — no graph) ─────────────────────────

def test_normalize_precision_accepts_vocabulary_and_normalizes_empty():
    from tortoise.validity import normalize_precision

    for token in ("year", "month", "day", "unknown", "YEAR", " day "):
        assert normalize_precision(token) == token.strip().lower()
    assert normalize_precision(None) is None
    assert normalize_precision("") is None
    assert normalize_precision("   ") is None


def test_normalize_precision_refuses_an_unknown_word_and_non_string():
    from tortoise.validity import normalize_precision

    with pytest.raises(ValueError, match="not a validity precision"):
        normalize_precision("week")
    with pytest.raises(ValueError, match="must be a string"):
        normalize_precision(2024)


def test_effective_precision_legacy_default_is_day():
    from tortoise.validity import effective_precision

    # A bare-ISO bound (no declared precision) reads as DAY — no migration.
    assert effective_precision("2024-01-01", None) == "day"
    # A declared token wins over the legacy default.
    assert effective_precision("2024-01-01", "year") == "year"
    # Absent bound + no declaration ⇒ no precision.
    assert effective_precision(None, None) is None
    # The closed-unknown marker on an absent bound.
    assert effective_precision(None, "unknown") == "unknown"


def test_effective_precision_denies_a_bound_that_names_no_instant():
    """#9201: presence is NOT precision.

    ``effective_precision`` gated the legacy default on ``is not None`` alone,
    so a PRESENT but UNORDERABLE bound (``validFrom=""`` / ``"TBD"``) reported
    ``"day"`` — a day-granularity claim about a start that names no instant.
    The PRESENCE gate is right and stays: a falsey-but-present bound is a real
    bound for ``_covers`` / ``_assert_window_start_not_inverted``. What it
    cannot support is a granularity, so the precision gate additionally
    requires the bound to ORDER — the same ``_created_sort_key`` the read path
    orders with (``key[0] == 0`` ⇒ parseable instant).

    Discriminating values: ``""`` and ``"TBD"`` (present, unorderable ⇒ None)
    against ``0`` and ``False`` (present, ORDERABLE ⇒ still ``day``: epoch 0 is
    1970-01-01T00:00:00Z, a real instant — the #6140 ruling makes an unparseable
    start unrecorded, not a numeric epoch).
    """
    from tortoise.validity import effective_precision

    assert effective_precision("", None) is None
    assert effective_precision("TBD", None) is None
    assert effective_precision("not-a-date", None) is None
    # orderable — the pre-#2350 legacy reading is untouched
    assert effective_precision("2024-01-01", None) == "day"
    assert effective_precision("2024-01-01T00:00:00+00:00", None) == "day"
    # epoch 0 is an INSTANT, not an absent start
    assert effective_precision(0, None) == "day"
    assert effective_precision(False, None) == "day"
    # a numeric bound is an instant even when ``_created_sort_key`` cannot
    # float() it — the read path must not acquire a raise it never had
    assert effective_precision(10 ** 400, None) == "day"
    # #9244 REVERSES the pre-#9244 assertion here (``("TBD", "year") ==
    # "year"``, "a caller-DECLARED token is an assertion, not a derivation:
    # it wins"): a declaration ANNOTATES a bound, it cannot summon one.
    assert effective_precision("TBD", "year") is None


def test_declared_precision_does_not_resurrect_a_bound_that_names_no_instant():
    """#9244 (residual of #9201): the DECLARED token is gated on the bound too.

    ``effective_precision`` returned the declared token BEFORE the
    ``_created_sort_key`` orderability gate, so ``create_point(validFrom="TBD",
    validFromPrecision="year")`` — accepted by the write boundary, which only
    refuses a declaration on an ABSENT bound — made every read surface report
    ``valid_from_precision == "year"`` for a start the #6140 owner ruling reads
    as *never recorded*. That is the #9201 lie with one extra step: a
    declaration annotates a bound, it cannot make one name an instant.

    The gate is on the BOUND, so an ABSENT bound keeps its declaration
    (``validToPrecision="unknown"`` IS #2350's ``END_UNKNOWN`` marker — there is
    no bound to order), and an ORDERABLE bound keeps it too (the declaration
    still wins over the legacy ``day`` default).
    """
    from tortoise.validity import effective_precision, window_read_fields

    # PRESENT but UNORDERABLE ⇒ no precision, declared or not.
    assert effective_precision("TBD", "year") is None
    assert effective_precision("", "year") is None
    assert effective_precision("not-a-date", "month") is None
    # ORDERABLE ⇒ the declaration still wins over the legacy default.
    assert effective_precision("2024-06-01", "year") == "year"
    assert effective_precision("2024-06-01", "month") == "month"
    assert effective_precision("2024-06-01", "day") == "day"
    assert effective_precision("2024-06-01", None) == "day"
    # ABSENT ⇒ the declaration is the only datum there is; the 3-state end
    # marker survives (``END_UNKNOWN``), it is not a claim about an instant.
    assert effective_precision(None, "unknown") == "unknown"
    assert effective_precision(None, None) is None
    assert effective_precision(None, "year") == "year"
    # a NUMERIC bound always names an instant: the declaration wins, and the
    # no-declaration default stays ``day`` without an ``OverflowError``.
    assert effective_precision(0, "year") == "year"
    assert effective_precision(0, None) == "day"
    assert effective_precision(10 ** 400, "year") == "year"
    # the ONE read projection reports the same ``None``, key still emitted.
    assert window_read_fields("TBD", None, "year", None) == {
        "valid_from_precision": None,
        "valid_to_precision": None,
        "end_state": "open",
    }


def test_window_read_fields_denies_precision_to_an_unorderable_bound():
    """#9201: the ONE read-projection home reports no precision for a bound
    that names no instant, on either end, and leaves the orderable end alone."""
    from tortoise.validity import window_read_fields

    assert window_read_fields("", "2024-01-01", None, None) == {
        "valid_from_precision": None,
        "valid_to_precision": "day",
        "end_state": "known",
    }
    # #9202 (owner ruling 2026-10-25, option B): the END bound here names no
    # instant, so beside ``valid_to_precision: None`` the state must NOT claim
    # the end is dated. Before the ruling this read ``known``.
    assert window_read_fields("2024-01-01", "TBD", None, None) == {
        "valid_from_precision": "day",
        "valid_to_precision": None,
        "end_state": "unknown",
    }


def test_end_state_is_three_valued():
    from tortoise.validity import END_KNOWN, END_OPEN, END_UNKNOWN, end_state

    assert end_state(None, None) == END_OPEN
    assert end_state(None, "") == END_OPEN
    assert end_state("2024-01-01", None) == END_KNOWN
    assert end_state("2024-01-01", "month") == END_KNOWN
    assert end_state(None, "unknown") == END_UNKNOWN
    # #9202 (owner ruling 2026-10-25): the SECOND spelling of END_UNKNOWN — a
    # stored end that names no instant. Absent still reads OPEN and an
    # orderable end still reads KNOWN, so the gate is datability, not presence.
    assert end_state("TBD", None) == END_UNKNOWN
    assert end_state("", None) == END_UNKNOWN
    assert end_state("2026-02-30", None) == END_UNKNOWN
    assert end_state(0, None) == END_KNOWN  # a numeric epoch IS an instant


def test_validate_refuses_unknown_start():
    from tortoise.validity import validate_validity_precision

    with pytest.raises(ValueError, match="END only"):
        validate_validity_precision("2024-01-01", None, "unknown", None)


def test_validate_refuses_unknown_end_alongside_a_fabricated_date():
    from tortoise.validity import validate_validity_precision

    with pytest.raises(ValueError, match="contradiction"):
        validate_validity_precision(None, "2024-01-01", None, "unknown")


def test_validate_refuses_a_precision_without_its_bound():
    from tortoise.validity import validate_validity_precision

    with pytest.raises(ValueError, match="requires a validTo"):
        validate_validity_precision(None, None, None, "year")
    with pytest.raises(ValueError, match="requires a validFrom"):
        validate_validity_precision(None, None, "month", None)


def test_window_read_fields_legacy_shape_is_the_pre_2350_reading():
    from tortoise.validity import window_read_fields

    legacy = window_read_fields("2024-01-01", "2024-06-01", None, None)
    assert legacy == {"valid_from_precision": "day",
                      "valid_to_precision": "day", "end_state": "known"}
    open_ended = window_read_fields("2024-01-01", None, None, None)
    assert open_ended["end_state"] == "open"
    assert open_ended["valid_to_precision"] is None


# ── the E2E the issue names: year precision → open read → unknown end ─────

def test_precision_roundtrip_three_state_e2e(lane):
    """write year-precision open → read → supersede with an unknown end →
    read 3-state (the #2350 E2E)."""
    sdk, _events = lane
    old = sdk.create_point("statement", "old claim",
                           validFrom="2024-01-01",
                           validFromPrecision="year")

    first = sdk.get_historical_knowledge(old["id"], A_DATE)
    vp = first["valid_point"]
    assert vp["valid_from_precision"] == "year"
    assert vp["end_state"] == "open"
    assert "end_state_unknown" not in first

    new = sdk.create_point("statement", "new claim", validFrom="2025-03-01")
    sdk.supersede_point(old["id"], new["id"], valid_to_precision="unknown")

    # The predecessor's end is CLOSED but un-datable: no validTo is fabricated.
    assert _window_props(sdk, old["id"]) == {
        "validFrom": "2024-01-01", "validTo": None,
        "validFromPrecision": "year", "validToPrecision": "unknown"}

    after = sdk.get_historical_knowledge(old["id"], A_DATE)
    assert after["valid_point"]["end_state"] == "unknown"
    assert after["valid_point"]["valid_from_precision"] == "year"
    # The reply says plainly that coverage past the start is not attested.
    assert after["end_state_unknown"] is True
    assert after["indeterminate_end_ids"] == [old["id"]]


def test_legacy_bare_iso_window_reads_as_day_precision_no_migration(lane):
    sdk, _events = lane
    p = sdk.create_point("statement", "legacy window",
                         validFrom="2024-01-01", validTo="2024-12-31")
    assert _window_props(sdk, p["id"]) == {
        "validFrom": "2024-01-01", "validTo": "2024-12-31",
        "validFromPrecision": None, "validToPrecision": None}
    out = sdk.get_historical_knowledge(p["id"], "2024-06-01")
    assert out["valid_point"]["valid_from_precision"] == "day"
    assert out["valid_point"]["valid_to_precision"] == "day"
    assert out["valid_point"]["end_state"] == "known"


def test_open_and_closed_unknown_are_distinguishable_on_the_same_read(lane):
    sdk, _events = lane
    still = sdk.create_point("statement", "still true", validFrom="2024-01-01")
    ended = sdk.create_point("statement", "ended unknown",
                             validFrom="2024-01-01",
                             validToPrecision="unknown")
    open_out = sdk.get_historical_knowledge(still["id"], A_DATE)
    unknown_out = sdk.get_historical_knowledge(ended["id"], A_DATE)
    assert open_out["valid_point"]["end_state"] == "open"
    assert unknown_out["valid_point"]["end_state"] == "unknown"
    assert "end_state_unknown" not in open_out
    assert unknown_out["end_state_unknown"] is True


# ── the write boundary ───────────────────────────────────────────────────

def test_create_point_refuses_inconsistent_precision(lane):
    sdk, _events = lane
    with pytest.raises(ValueError, match="not a validity precision"):
        sdk.create_point("statement", "x", validToPrecision="week")
    with pytest.raises(ValueError, match="END only"):
        sdk.create_point("statement", "x", validFromPrecision="unknown")
    with pytest.raises(ValueError, match="contradiction"):
        sdk.create_point("statement", "x", validTo="2024-01-01",
                         validToPrecision="unknown")
    with pytest.raises(ValueError, match="requires a validTo"):
        sdk.create_point("statement", "x", validToPrecision="year")


def test_update_point_refuses_a_one_sided_move_off_unknown(lane):
    sdk, _events = lane
    p = sdk.create_point("statement", "x", validFrom="2024-01-01",
                         validToPrecision="unknown")
    # A stored end of 'unknown' plus a newly dated validTo is refused until the
    # caller states the new precision — no stale marker beside a real date.
    with pytest.raises(ValueError, match="contradiction"):
        sdk.update_point(p["id"], validTo="2025-01-01")
    sdk.update_point(p["id"], validTo="2025-01-01", validToPrecision="day")
    assert _window_props(sdk, p["id"])["validToPrecision"] == "day"


def test_supersede_inherits_the_successors_start_precision(lane):
    sdk, _events = lane
    old = sdk.create_point("statement", "old", validFrom="2025-01-01")
    new = sdk.create_point("statement", "new", validFrom="2026-01-01",
                           validFromPrecision="month")
    sdk.supersede_point(old["id"], new["id"])
    assert _window_props(sdk, old["id"])["validToPrecision"] == "month"
    assert _window_props(sdk, old["id"])["validTo"] == "2026-01-01"


def test_supersede_inherits_through_an_agreeing_valid_from_kwarg(lane):
    """#9729 control: the kwarg rung of the end ladder still inherits.

    ``_supersede_window_end`` takes the caller's ``valid_from`` kwarg AHEAD of
    the stored start, and the agreement guard admits it only when it names the
    same instant — so that end IS the successor's start and must keep the
    declared token. Pinned separately because the no-kwarg test above passes
    with the kwarg arm of the #9729 gate deleted (measured: a mutant adding
    ``valid_from is None`` to the gate left the whole suite green).
    """
    sdk, _events = lane
    old = sdk.create_point("statement", "old", validFrom="2024-01-01")
    new = sdk.create_point("statement", "new", validFrom="2026-01-01",
                           validFromPrecision="month")
    sdk.supersede_point(old["id"], new["id"], valid_from="2026-01-01")
    props = _window_props(sdk, old["id"])
    assert props["validTo"] == "2026-01-01"
    assert props["validToPrecision"] == "month"


def test_supersede_does_not_inherit_a_declaration_onto_a_fabricated_end(lane):
    """#9729: the inherited token annotates the successor's START — the
    predecessor's END may only carry it when that END **is** that start.

    ``effective_precision`` gates a declared token on the bound naming an
    instant (#9244), so the successor's own ``validFrom="TBD"`` reads ``None``.
    ``supersede_point`` did not go through that resolution: it inherited
    ``vf_rows[0][2]`` onto the predecessor UNCONDITIONALLY as a WRITE, while the
    resolution ladder fell through the unorderable start to ``createdAt`` — so a
    declaration made for a start that names no instant was re-stamped onto an
    instant nobody declared a granularity for (the successor's ``createdAt``)
    and read back as that end's precision. The same declaration read ``None``
    one property away.
    """
    sdk, _events = lane
    old = sdk.create_point("statement", "old", validFrom="2024-01-01")
    new = sdk.create_point("statement", "new", validFrom="TBD",
                           validFromPrecision="year")
    sdk.supersede_point(old["id"], new["id"])
    props = _window_props(sdk, old["id"])
    # the end came from the fallback ladder (createdAt / now), never from the
    # successor's unorderable start.
    assert props["validTo"] not in (None, "TBD")
    # …so no declaration may be attached to it: the property stays ABSENT (the
    # legacy read default derives `day` from the bare timestamp — the
    # #9201/#9244 axis, which is about bounds that name NO instant).
    assert props["validToPrecision"] is None
    assert "validToPrecision" not in _props(sdk, old["id"])
    # the read surface must not resurrect it either. The stored property is
    # ABSENT, so the read derives the legacy `day` default from the bare
    # timestamp — the pre-#2350 reading every undeclared, ORDERABLE end gets.
    # What it must NOT read is the inherited `year`.
    assert sdk.get_historical_knowledge(
        old["id"], "2025-01-01")["chain"][0]["valid_to_precision"] == "day"
    # …and it agrees with what the SAME declaration reads on the successor's own
    # start: `None`, because that bound names no instant.
    assert sdk.get_historical_knowledge(
        new["id"], "2027-01-01")["valid_point"]["valid_from_precision"] is None


def test_supersede_stamps_no_precision_when_the_end_is_the_writer_clock(lane):
    """#9729 control: the gate is on the END'S SOURCE, not on ``createdAt``.

    With the successor's ``validFrom`` unorderable AND its ``createdAt``
    unparseable, the ladder lands on the writer's ``now`` — the third source,
    which no declared token ever annotated. The same no-precision verdict must
    hold there too, otherwise the fix would only cover the ``createdAt`` step.
    """
    sdk, _events = lane
    old = sdk.create_point("statement", "old", validFrom="2024-01-01")
    new = sdk.create_point("statement", "new", validFrom="TBD",
                           validFromPrecision="month",
                           createdAt="not-a-date")
    sdk.supersede_point(old["id"], new["id"])
    props = _window_props(sdk, old["id"])
    assert props["validTo"] not in (None, "TBD", "not-a-date")
    assert props["validToPrecision"] is None


def test_supersede_does_not_fabricate_precision_on_a_legacy_chain(lane):
    sdk, _events = lane
    old = sdk.create_point("statement", "old", validFrom="2025-01-01")
    new = sdk.create_point("statement", "new", validFrom="2026-01-01")
    sdk.supersede_point(old["id"], new["id"])
    # No declared precision anywhere ⇒ the property stays ABSENT (legacy day
    # default on read), never a stored guess.
    assert _window_props(sdk, old["id"])["validToPrecision"] is None


def test_invalidate_point_accepts_an_unknown_end(lane):
    sdk, _events = lane
    old = sdk.create_point("statement", "old", validFrom="2024-01-01")
    other = sdk.create_point("statement", "other", validFrom="2024-06-01")
    sdk.invalidate_point(old["id"], other["id"], valid_to_precision="unknown")
    props = _window_props(sdk, old["id"])
    assert props["validTo"] is None
    assert props["validToPrecision"] == "unknown"


def test_supersede_unknown_end_is_not_refused_by_the_inversion_guard(lane):
    """#2350 review P2: ``valid_to_precision='unknown'`` stamps NO end, so the
    #5358/#4021 inversion guard's premise ("persisting it would leave an
    inverted window") is false of the write — the guard must be SKIPPED, as
    ``invalidate_point`` already does for the same marker. Pre-fix
    ``supersede_point`` resolved the precision AFTER the guard and raised
    ``InvertedSupersedeWindow`` on a request that persists ``validTo=None``,
    while the identical call at the sibling door succeeded. The no-kwarg
    control below pins that the guard is still enforced for a REAL end."""
    from tortoise.sdk import InvertedSupersedeWindow

    sdk, _events = lane
    old = sdk.create_point("statement", "old", validFrom="2025-01-01")
    # the successor's start precedes the predecessor's → an inverted window IF
    # an end were stamped (it is not, under the 'unknown' marker).
    new = sdk.create_point("statement", "new", validFrom="2024-01-01")
    sdk.supersede_point(old["id"], new["id"], valid_to_precision="unknown")
    props = _window_props(sdk, old["id"])
    assert props["validTo"] is None
    assert props["validToPrecision"] == "unknown"
    assert sdk.get_historical_knowledge(
        old["id"], "2025-06-01")["chain"][0]["end_state"] == "unknown"

    # control — the guard is still enforced for a REAL end: the same inverted
    # pair WITHOUT the 'unknown' marker is refused (so the skip is scoped to
    # the no-end-stamped write, not a blanket bypass).
    old2 = sdk.create_point("statement", "old2", validFrom="2025-01-01")
    new2 = sdk.create_point("statement", "new2", validFrom="2024-01-01")
    with pytest.raises(InvertedSupersedeWindow):
        sdk.supersede_point(old2["id"], new2["id"])


# ── no re-stamp of pre-existing windows ──────────────────────────────────

def test_supersession_leaves_the_predecessors_own_start_untouched(lane):
    sdk, _events = lane
    old = sdk.create_point("statement", "old", validFrom="2025-01-01",
                           validFromPrecision="year")
    new = sdk.create_point("statement", "new", validFrom="2026-03-01")
    sdk.supersede_point(old["id"], new["id"])
    assert _window_props(sdk, old["id"])["validFrom"] == "2025-01-01"
    assert _window_props(sdk, old["id"])["validFromPrecision"] == "year"


# ── rebuild parity (full-prop preserved) ─────────────────────────────────

def test_precision_survives_a_jsonl_wipe_and_rebuild(lane):
    sdk, events = lane
    old = sdk.create_point("statement", "old", validFrom="2024-01-01",
                           validFromPrecision="year")
    new = sdk.create_point("statement", "new", validFrom="2025-03-01")
    sdk.supersede_point(old["id"], new["id"], valid_to_precision="unknown")
    live = _window_props(sdk, old["id"])

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _window_props(sdk, old["id"]) == live
    rebuilt = sdk.get_historical_knowledge(old["id"], A_DATE)
    assert rebuilt["valid_point"]["end_state"] == "unknown"
    assert rebuilt["end_state_unknown"] is True


def test_invalidated_unknown_precision_survives_rebuild(lane):
    sdk, events = lane
    old = sdk.create_point("statement", "old", validFrom="2024-01-01")
    other = sdk.create_point("statement", "other", validFrom="2024-06-01")
    sdk.invalidate_point(old["id"], other["id"], valid_to_precision="unknown")
    live = _window_props(sdk, old["id"])

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _window_props(sdk, old["id"]) == live


def test_update_door_precision_survives_rebuild_and_stays_ended_unknown(lane):
    """#2350 review P1: a precision written through the ``update_point`` DOOR
    must replay. The ``PointRevised`` fold built its props only from
    content/annotator/belief/``mitigation_strength``/``tags``, so replay
    dropped a precision the live path wrote and ``end_state`` flipped
    ``unknown`` -> ``open`` — an un-datable end read as still-true (#2349
    fabricated coverage). ``check_consistency`` (whose reference view is the
    same pure fold) also red the healthy live graph. Fails on pre-fix code:
    rebuilt ``validToPrecision`` is ``None``, ``end_state`` is ``open``, and
    the checker reports a ``validToPrecision`` divergence."""
    from tortoise.consistency import check_consistency

    sdk, events = lane
    p = sdk.create_point("statement", "a", validFrom="2024-01-01")
    sdk.update_point(p["id"], validToPrecision="unknown")
    # non-vacuous: the LIVE write landed (the update door persisted it).
    assert _window_props(sdk, p["id"])["validToPrecision"] == "unknown"
    assert sdk.get_historical_knowledge(
        p["id"], "2025-01-01")["chain"][0]["end_state"] == "unknown"
    # (a) the durability check must not red a graph the live path wrote.
    assert check_consistency(str(events / "events.jsonl"),
                             sdk._get_proj())["ok"]

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    # (b) live == rebuilt, and the end is STILL ended-unknown, not open.
    assert _window_props(sdk, p["id"])["validToPrecision"] == "unknown"
    after = sdk.get_historical_knowledge(p["id"], "2025-01-01")
    assert after["chain"][0]["end_state"] == "unknown"
    assert after["end_state_unknown"] is True


def test_update_door_start_precision_survives_rebuild(lane):
    """#2350 review P1, start-end leg: the same update door must carry
    ``validFromPrecision`` through replay (pre-fix it rebuilt to ``None``)."""
    sdk, events = lane
    p = sdk.create_point("statement", "a", validFrom="2024-01-01")
    sdk.update_point(p["id"], validFromPrecision="year")
    assert _window_props(sdk, p["id"])["validFromPrecision"] == "year"

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _window_props(sdk, p["id"])["validFromPrecision"] == "year"
    assert sdk.get_historical_knowledge(
        p["id"], A_DATE)["chain"][0]["valid_from_precision"] == "year"


def test_update_door_precision_clear_replays_as_absent(lane):
    """#2350 review P1: presence-conditional, like the live ``SET n += $props``
    — an explicit ``None`` is the documented CLEAR and folds as absent on
    BOTH replay engines, not as a stale value."""
    sdk, events = lane
    p = sdk.create_point("statement", "a", validFrom="2024-01-01",
                         validFromPrecision="year",
                         validToPrecision="unknown")
    sdk.update_point(p["id"], validToPrecision=None)
    assert _window_props(sdk, p["id"])["validToPrecision"] is None

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _window_props(sdk, p["id"])["validToPrecision"] is None
    # the untouched sibling is not clobbered by the one-ended update.
    assert _window_props(sdk, p["id"])["validFromPrecision"] == "year"


# ── search / recall annotation ───────────────────────────────────────────

def _scan(sdk, **kw):
    return sdk.tortoise_fts_query(query=None, kind="statement",
                                  entity_type="point", limit=50,
                                  include_terminal=True, **kw)


def test_reader_marker_does_not_relabel_invalidated_unknown_as_still_true(lane):
    """#2350: the reader-facing ``[valid …]`` marker must not render an
    invalidated-un-datable window as still-true, and must keep the
    transaction-time expiry date (``invalidate_point`` stamps ``expiredAt``)."""
    from tortoise.retrieval import _validity_marker

    sdk, _events = lane
    old = sdk.create_point("statement", "old", validFrom="2024-01-01")
    other = sdk.create_point("statement", "other", validFrom="2024-06-01")
    sdk.invalidate_point(old["id"], other["id"], valid_to_precision="unknown")
    hit = next(r for r in _scan(sdk) if r["id"] == old["id"])
    assert hit["end_state"] == "unknown"
    assert hit["valid_to_precision"] == "unknown"
    marker = _validity_marker(hit)
    # the SUPERSEDED BY marker renders first, so match the window half.
    assert "[valid since 2024-01-01; expired " in marker
    assert marker.endswith("; end unknown]")


def test_reader_marker_omits_a_start_that_names_no_instant():
    """#9730: ``_validity_marker`` interpolated the raw ``valid_from``, so a
    start naming no instant rendered as a since-DATE (``[valid since TBD]``)
    — a bound the data denies.

    ``_validity_marker`` reads the BOUND, not the resolved precision, so the
    #9201/#9244 read gate (``validity.effective_precision``) never reached it:
    ``""`` escaped by being falsey, but every TRUTHY unorderable value leaked
    its raw text into the reader-facing evidence. Under the #6140 owner ruling
    such a start means *no start was recorded*, which is exactly why the
    precision beside it resolves to ``None`` — so the marker must drop the
    start clause rather than assert one.

    Fails pre-fix on every window assertion here: ``'[valid since TBD]'``,
    ``'[valid since TBD; end unknown]'``, ``'[valid since not-a-date]'``.
    """
    from tortoise.retrieval import _validity_marker
    from tortoise.validity import effective_precision

    for bogus in ("TBD", "not-a-date"):
        # non-vacuous: the bound is PRESENT, and the read gate already reads it
        # as "no instant" — the marker is the surface that disagreed.
        assert effective_precision(bogus, None) is None
        open_marker = _validity_marker(
            {"valid_from": bogus, "valid_from_precision": None,
             "end_state": "open"})
        assert bogus not in open_marker, open_marker
        assert "since" not in open_marker, open_marker
        assert open_marker == "[valid]"
        # the END half is #9202's question and is read from the resolved
        # state — see
        # ``test_reader_marker_states_an_undatable_end_as_unknown``; what this
        # test pins is that the START clause is omitted either way.
        assert _validity_marker({"valid_from": bogus,
                                 "end_state": "unknown"}) == \
            "[valid; end unknown]"
        # a present, orderable END still renders as a bounded window.
        assert _validity_marker({"valid_from": bogus,
                                 "valid_to": "2026-06-12"}) == \
            "[valid → 2026-06-12]"


#: Every spelling #9202's 2026-10-25 owner ruling names as an UN-DATABLE end —
#: the ruling's own enumeration, not a sample of it (the same six
#: ``tests/test_validity_windows.py`` sweeps for the READ half). They disagree
#: about truthiness (``""`` vs ``"TBD"``), about digits (``"   "``,
#: ``"1719792000"``) and about shape (``"2026-02-30"`` looks exactly like the
#: ISO date beside it) — each difference is a way for a gate keyed on the raw
#: text to pass by accident.
_UN_DATABLE_END_SPELLINGS = [
    ("TBD", "text-placeholder"),
    ("", "empty-value"),
    ("   ", "whitespace"),
    ("not-a-date", "plain-nonsense"),
    ("2026-02-30", "impossible-calendar-date"),
    ("1719792000", "epoch-as-string"),
]


@pytest.mark.parametrize(
    ("stored_end", "_why"), _UN_DATABLE_END_SPELLINGS,
    ids=[why for _end, why in _UN_DATABLE_END_SPELLINGS],
)
def test_reader_marker_states_an_undatable_end_as_unknown(stored_end, _why):
    """#9741 (render half): the marker CONSUMES the end state #9202 computes.

    ``_validity_marker`` kept its own notion of a dated end ("``valid_to`` is
    truthy"), so a STORED end that names no instant — the state #9202's owner
    ruling calls ``END_UNKNOWN`` — took the bounded-window arm and printed
    itself as a date: ``[valid 2024-01-01 → TBD]``. The reader was told the
    window closed at ``TBD`` while the reply beside it said ``end_state:
    "unknown"``. Two places deciding the same question is what produced it, so
    the fix reads the state (``validity.end_state``, spread onto every hit by
    ``window_read_fields``) instead of deciding again from the bound.

    Both spellings of that one state now render the ONE form #2350 defined for
    it, and the stored text does not reach the reader at all. Discriminating:
    the ``==`` assertion below fails on the unfixed head for the four spellings
    that survive the renderer's ``.strip()`` — ``TBD``, ``not-a-date``,
    ``2026-02-30``, ``1719792000`` (``[valid 2024-01-01 → <raw>]``, the pre-fix
    shape); the two that are falsey once trimmed (``""`` and ``"   "``)
    already routed to the ``end unknown`` arm and are kept so the class stays
    covered.
    """
    from tortoise.retrieval import _validity_marker
    from tortoise.validity import window_read_fields

    # (0) non-vacuous: the state the renderer must consume IS ``unknown``, and
    # the raw value really is on the hit (a renderer printing the bound would
    # print it — the pre-fix shape).
    state = window_read_fields("2024-01-01", stored_end, None, None)
    assert state["end_state"] == "unknown", stored_end
    hit = {"valid_from": "2024-01-01", "valid_to": stored_end, **state}
    marker = _validity_marker(hit)
    assert marker == "[valid since 2024-01-01; end unknown]", (stored_end, marker)
    if stored_end.strip():
        assert stored_end.strip() not in marker, (stored_end, marker)

    # the transaction-time expiry still survives the state arm (#2350: it is a
    # different datum from the validity end).
    assert _validity_marker({**hit, "expired_at": "2026-07-01T10:00:00+00:00"}) \
        == "[valid since 2024-01-01; expired 2026-07-01; end unknown]"

    # CONTROL — a DATED end keeps the byte-identical bounded window. The fix
    # withholds an un-datable bound; it does not stop windows from ending.
    dated_state = window_read_fields("2024-01-01", "2024-06-01", None, None)
    assert dated_state["end_state"] == "known"
    assert _validity_marker({**hit, "valid_to": "2024-06-01", **dated_state}) \
        == "[valid 2024-01-01 → 2024-06-01]"


def test_reader_marker_on_the_search_wire_states_an_undatable_end(lane):
    """#9741 end-to-end: the state reaches the renderer from the SURFACE.

    The pure-function pin above builds the hit itself, so it would still pass
    if the search wire stopped carrying ``end_state``/``valid_to_precision``
    (the renderer would fall back or print the raw bound again). This reads
    the marker off a hit produced by the PUBLIC write and the scan decoration —
    the path the ask lane renders.

    ⚠️ **The un-datable fixture is a PRE-2026-10-26 row, written directly** (#9202
    merged after this pin was written): the shaping ruling makes
    ``create_point(validTo="TBD")`` store the canonical un-datable form — no end
    plus the declared marker — instead of the literal, so a LITERAL end on the
    wire can now only come from a graph written before the ruling. Every
    assertion below is unchanged: the renderer's contract is the same for both
    spellings, which is the property this pin exists to protect.
    """
    from tortoise.retrieval import _validity_marker

    sdk, _events = lane
    un_datable = sdk.create_point("statement", "un-datable end",
                                 validFrom="2024-01-01")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) SET n.validTo = 'TBD'",
        params={"i": un_datable["id"]})
    dated = sdk.create_point("statement", "dated end",
                             validFrom="2024-01-01", validTo="2024-06-01")
    hits = {r["id"]: r for r in _scan(sdk)}
    hit = hits[un_datable["id"]]
    # non-vacuous: the raw bound IS on the wire the renderer consumes, and the
    # state beside it already says the end cannot be pinned.
    assert hit["valid_to"] == "TBD"
    assert hit["end_state"] == "unknown"
    assert _validity_marker(hit) == "[valid since 2024-01-01; end unknown]"
    # control: the same surface still renders a dated end as a bounded window.
    assert _validity_marker(hits[dated["id"]]) == \
        "[valid 2024-01-01 → 2024-06-01]"


def test_reader_marker_control_keeps_an_orderable_start_byte_identical():
    """#9730 control: the gate is the read path's OWN predicate
    (``_created_sort_key(value)[0] == 0``), so every bound that NAMES an
    instant keeps the exact marker it rendered before the fix — including the
    END_UNKNOWN form #2350 added."""
    from tortoise.retrieval import _validity_marker

    assert _validity_marker({"valid_from": "2026-06-10",
                             "end_state": "open"}) == \
        "[valid since 2026-06-10]"
    # a full timestamp is still truncated to the ISO date in the reader text.
    assert _validity_marker({"valid_from": "2026-06-10T09:30:00Z",
                             "end_state": "open"}) == \
        "[valid since 2026-06-10]"
    assert _validity_marker({"valid_from": "2024-01-01",
                             "valid_to": "2024-06-01"}) == \
        "[valid 2024-01-01 → 2024-06-01]"
    # #2350's ended-but-un-datable end is UNCHANGED (this issue is the START
    # half only).
    assert _validity_marker({"valid_from": "2024-01-01",
                             "end_state": "unknown"}) == \
        "[valid since 2024-01-01; end unknown]"
    # an ABSENT start still emits no window marker at all.
    assert _validity_marker({"valid_from": "", "end_state": "open"}) == ""


def test_reader_marker_on_the_search_wire_never_leaks_an_unorderable_start(lane):
    """#9730 end-to-end: the bug is on the SURFACE THE MODEL READS.

    ``create_point(validFrom="TBD")`` is accepted (narrowing the write
    contract is #9244's owner decision, not this read-path lane), so the marker
    is the last honest place to drop the start — measured through the public
    write and the scan decoration, not just the pure function.
    """
    from tortoise.retrieval import _validity_marker

    sdk, _events = lane
    bogus = sdk.create_point("statement", "undated start", validFrom="TBD")
    dated = sdk.create_point("statement", "dated start",
                             validFrom="2024-01-01")
    hits = {r["id"]: r for r in _scan(sdk)}
    # non-vacuous: the raw text IS on the wire the renderer consumes.
    assert hits[bogus["id"]]["valid_from"] == "TBD"
    assert hits[bogus["id"]]["valid_from_precision"] is None
    bad_marker = _validity_marker(hits[bogus["id"]])
    assert "TBD" not in bad_marker, bad_marker
    assert bad_marker == "[valid]"
    # control: the same surface still renders an orderable start.
    assert _validity_marker(hits[dated["id"]]) == "[valid since 2024-01-01]"


def test_a_padded_start_is_gated_on_the_raw_bound_not_the_display_copy(lane):
    """#9730 P2: the orderability gate must run on the RAW stored bound.

    ``_validity_marker`` trims the bound for DISPLAY, but every other surface
    resolves orderability from the raw value: ``search_engine._created_sort_key``
    tests ``s[0].isdigit()``, so one leading/trailing space buckets the bound as
    UNORDERABLE — therefore ``validity.effective_precision`` (and the #6140 start
    gate in ``sdk._covers``, and the write-path warning) reads a padded date as
    "no start was recorded", while gating on the trimmed copy made the marker
    assert one. That is the cross-surface drift ``window_read_fields`` passes the
    bounds RAW to prevent (#2350 review P2).

    The trimmed text is still what a rendered start PRINTS; a bound that cannot
    be ordered prints no start clause at all — the reading the precision beside
    it gives.

    Fails pre-fix on every assertion here: the marker was
    ``[valid since 2024-06-01]`` for a bound whose ``valid_from_precision`` is
    ``None``.
    """
    from tortoise.retrieval import _validity_marker
    from tortoise.validity import effective_precision

    for padded in (" 2024-06-01", "2024-06-01 ", "  2024-06-01  "):
        # non-vacuous: the bound IS present, is a date once trimmed, and the
        # read gate already reads it as "no instant" — the marker disagreed.
        assert padded.strip() == "2024-06-01"
        assert effective_precision(padded, None) is None, padded
        marker = _validity_marker({"valid_from": padded,
                                   "valid_from_precision": None,
                                   "end_state": "open"})
        assert marker == "[valid]", (padded, marker)
        # ... and the same for the two other shapes the start clause feeds.
        assert _validity_marker({"valid_from": padded,
                                 "end_state": "unknown"}) == \
            "[valid; end unknown]"
        assert _validity_marker({"valid_from": padded,
                                 "valid_to": "2026-06-12"}) == \
            "[valid → 2026-06-12]"

    # the wire: the padded bound is stored VERBATIM (no write-side trim), so the
    # renderer really does receive the raw value it must gate on.
    sdk, _events = lane
    padded_pt = sdk.create_point("statement", "padded start",
                                 validFrom=" 2024-06-01 ")
    dated = sdk.create_point("statement", "clean start",
                             validFrom="2024-06-01")
    hits = {r["id"]: r for r in _scan(sdk)}
    assert hits[padded_pt["id"]]["valid_from"] == " 2024-06-01 "
    assert hits[padded_pt["id"]]["valid_from_precision"] is None
    assert _validity_marker(hits[padded_pt["id"]]) == "[valid]"
    # control: the untrimmed-orderable twin keeps its start clause.
    assert hits[dated["id"]]["valid_from_precision"] == "day"
    assert _validity_marker(hits[dated["id"]]) == "[valid since 2024-06-01]"


def test_bounds_an_instant_agrees_with_the_read_gate_on_every_bound_it_sees():
    """#9730 P3: ``_bounds_an_instant`` resolves orderability exactly as
    ``validity.effective_precision`` does — including that helper's NUMERIC
    short-circuit, not just its ``_created_sort_key`` gate.

    Scope, precisely: this pins the PREDICATE parity (the two helpers agree on
    every value either is handed). It does not claim the RENDERER consults the
    predicate for every bound — a falsey numeric is dropped upstream by
    ``(raw_vf or "")``; that residual is pinned separately in
    ``test_an_epoch_zero_start_renders_no_window_is_recorded``.

    ``effective_precision`` short-circuits ``int``/``float`` BEFORE ordering,
    deliberately: an integer too large for ``float()`` raises ``OverflowError``
    out of ``_created_sort_key``, and a read path must not acquire a failure
    mode it never had (#9201 review P3). Without the same branch here, a
    numeric bound makes the two surfaces disagree — ``effective_precision(0,
    None) == "day"`` while ``_bounds_an_instant(0)`` was ``False`` (the falsey
    shortcut) and a huge int RAISED.

    (1) FAILS pre-fix: ``_bounds_an_instant(0)`` was False and
        ``_bounds_an_instant(10**400)`` raised OverflowError.
    (2) REACHABLE: both args are bounds ``_created_sort_key`` documents as
        supported (a mixed ISO/epoch graph), and the assertion is on the real
        helpers.
    """
    from tortoise.retrieval import _bounds_an_instant
    from tortoise.validity import effective_precision

    for numeric in (0, 0.0, False, 1735689600, 10 ** 400):
        assert _bounds_an_instant(numeric) is True, numeric
        assert effective_precision(numeric, None) == "day", numeric
    # the string half is unchanged — parity for absent and unorderable text.
    for text in ("", None, "TBD", "not-a-date", "2026"):
        assert _bounds_an_instant(text) is False, text
        assert effective_precision(text, None) is None, text
    # a PADDED date is unorderable on BOTH surfaces: ``_created_sort_key``
    # tests ``s[0].isdigit()`` / ``fromisoformat`` on the raw string, so one
    # space is enough. Parity here is what makes the marker drop the start
    # clause instead of asserting one (``_validity_marker`` gates on the raw
    # bound for exactly this reason).
    for padded in (" 2024-06-01", "2024-06-01 ", "  2024-06-01  "):
        assert _bounds_an_instant(padded) is False, padded
        assert effective_precision(padded, None) is None, padded
    assert _bounds_an_instant("2024-01-01") is True
    assert effective_precision("2024-01-01", None) == "day"


def test_an_epoch_zero_start_renders_its_day():
    """#9730 P3 → #9741: the residual is CLOSED — an epoch-0 start is a start.

    This test used to pin the residual: ``(raw_vf or "")`` dropped a FALSEY
    numeric (``0``/``0.0``/``False``) before the orderability gate ever saw it,
    while ``validity.effective_precision(0, None)`` read ``"day"`` (epoch 0 IS
    an instant, 1970-01-01T00:00:00Z) — the marker rendered no window and the
    precision beside it named one. It also pinned the crash for a TRUTHY
    numeric (``pytest.raises(AttributeError)`` on ``1735689600``), i.e. the
    suite was green BECAUSE the defect was expected.

    #9741 removed the truthiness trap (bounds are read through
    ``retrieval._display_bound`` and gated on ``_bounds_an_instant`` over the
    RAW value), so both halves now assert the fixed reading: an epoch-0 start
    renders its own calendar day, and the precision beside it agrees. The
    falsy ``False`` case is the same epoch (``bool`` is an ``int``) and is kept
    for the trap's other spelling.
    """
    from tortoise.retrieval import _validity_marker
    from tortoise.validity import effective_precision

    for epoch_zero in (0, 0.0, False):
        assert effective_precision(epoch_zero, None) == "day", epoch_zero
        assert _validity_marker({"valid_from": epoch_zero,
                                 "end_state": "open"}) == \
            "[valid since 1970-01-01]", epoch_zero
    # a TRUTHY numeric renders its calendar day instead of raising (#9741)
    assert _validity_marker({"valid_from": 1735689600,
                             "end_state": "open"}) == "[valid since 2025-01-01]"


# ── #9741: a NUMBER is a legal bound; one bad point never denies the read ──

def test_a_numeric_start_renders_its_utc_day_in_both_epoch_units():
    """#9741: the reported defect — a NUMERIC ``validFrom``, which the write
    boundary accepts, raised ``AttributeError: 'int' object has no attribute
    'strip'`` at ``retrieval._validity_marker``'s bound extraction.

    Both epoch units a stored graph can hold must render the SAME calendar day:
    seconds (``1735689600``) and millis (``1735689600000``) both name
    2025-01-01T00:00:00Z, and reading one as the other would state a date off
    by a factor of 1000. The ISO sibling is the control: the marker text is
    byte-identical, so the reader cannot tell which storage form it read.
    """
    from tortoise.retrieval import _validity_marker

    iso = _validity_marker({"id": "p", "valid_from": "2025-01-01",
                            "end_state": "open"})
    from tortoise.retrieval import BOUND_RENDER_OUTCOMES
    millis_before = BOUND_RENDER_OUTCOMES["epoch_read_as_millis"]
    for epoch in (1735689600, 1735689600000, 1735689600.0, 1735689600000.0):
        assert _validity_marker({"id": "p", "valid_from": epoch,
                                 "end_state": "open"}) == iso, epoch
    assert iso == "[valid since 2025-01-01]"
    # the millis reinterpretation is OBSERVABLE (it is this marker's alone:
    # ``_created_sort_key`` and ``time_aware._as_date`` read a NUMBER as seconds)
    assert BOUND_RENDER_OUTCOMES["epoch_read_as_millis"] == millis_before + 2
    # the END half goes through the same normaliser (one boundary, not one
    # call site): a numeric validTo renders a date instead of raising.
    assert _validity_marker({"id": "p", "valid_from": "2024-06-01",
                             "valid_to": 1735689600000,
                             "end_state": "known"}) == \
        "[valid 2024-06-01 → 2025-01-01]"


def test_a_numeric_start_through_the_public_write_renders_the_start(lane):
    """#9741 through the PUBLIC write, on the real read path.

    ``create_point(validFrom=<epoch seconds>)`` is ACCEPTED by the write
    boundary (``validate_validity_window`` orders it with the same
    ``_created_sort_key`` the read path orders with), so the reader must render
    it. Asserts the OBSERVABLE — the returned text states the stored instant —
    and the ISO sibling's block is byte-identical apart from the id/content,
    which is the no-regression control for the existing ISO rendering.
    """
    from tortoise.retrieval import render_context

    sdk, _events = lane
    epoch = sdk.create_point("statement", "epoch start", validFrom=1735689600)
    iso = sdk.create_point("statement", "iso start", validFrom="2025-01-01")
    assert _window_props(sdk, epoch["id"])["validFrom"] == 1735689600

    hits = {r["id"]: r for r in _scan(sdk)}
    assert hits[epoch["id"]]["valid_from"] == 1735689600
    # the read completes and states the instant (it used to raise here)
    text = render_context([hits[epoch["id"]]])
    assert "[valid since 2025-01-01]" in text
    # the ISO sibling renders the same marker — byte-identical evidence
    assert render_context([hits[iso["id"]]]).replace("iso start", "epoch start") \
        == text


def test_an_unreadable_bound_is_skipped_with_an_observable_signal():
    """#9741 second direction: a genuinely unreadable bound.

    Chosen mechanism: SKIP the clause and COUNT it (``BOUND_RENDER_OUTCOMES``),
    never raise and never coerce to ``None``/``0``. Coercing would make a
    malformed timestamp indistinguishable from an absent one, and raising is
    the poison pill itself. The ISO control below is the no-regression half:
    a readable bound on the same hit still renders exactly as before.
    """
    from tortoise.retrieval import BOUND_RENDER_OUTCOMES, _validity_marker

    control = _validity_marker({"id": "p", "valid_from": "2024-06-01",
                                "end_state": "open"})
    for junk in ({"nested": 1}, ["x"], float("nan"), float("inf"), 10 ** 400):
        before = BOUND_RENDER_OUTCOMES["unreadable_bound"]
        assert _validity_marker({"id": "p", "valid_from": junk,
                                 "end_state": "open"}) == "", junk
        # the skip is OBSERVABLE — a silent skip is the silent-loss defect
        assert BOUND_RENDER_OUTCOMES["unreadable_bound"] == before + 1, junk
        # and the ISO control is untouched by the counter / the skip
        assert _validity_marker({"id": "p", "valid_from": "2024-06-01",
                                 "end_state": "open"}) == control


def test_one_unreadable_hit_does_not_deny_the_rendered_read(monkeypatch):
    """#9741 — the poison pill, asserted as the observable it is.

    Before the containment, ``render_context([good, bad])`` raised for the ONE
    bad hit and returned NOTHING: every hit in the result set lost its evidence
    to a row it is not part of. After it, the read returns for all hits, and
    the hit whose annotation zone cannot be built at all (here a
    ``superseded_by`` of the wrong TYPE, which raises inside the marker) still
    renders its content — it loses the ANNOTATION, never the evidence — with
    ``marker_failed`` counted so the caller can tell.
    """
    from tortoise.retrieval import (BOUND_RENDER_OUTCOMES, _render_block,
                                    render_context)

    good = {"id": "good", "content": "the answer", "valid_from": "2024-06-01"}
    junk_bound = {"id": "junk-bound", "content": "poison",
                  "valid_from": {"nested": 1}}
    text = render_context([good, junk_bound])
    # BOTH hits are rendered — the entity's evidence the read is whole
    assert "the answer" in text and "poison" in text
    assert "[valid since 2024-06-01]" in text

    # a wrong-TYPE supersession field is read defensively by the marker itself
    # (it is called unguarded from ``_has_claim_text`` on the ask lane), so the
    # hit keeps its OWN marker and the read completes
    from tortoise.retrieval import _validity_marker
    assert _validity_marker({"id": "x", "valid_from": "2024-06-01",
                             "superseded_by": "not-a-dict"}) == \
        "[valid since 2024-06-01]"
    assert _validity_marker({"id": "x", "valid_from": "2024-06-01",
                             "supersedes": ["not-a-dict", 7]}) == \
        "[valid since 2024-06-01]"
    assert _validity_marker({"id": "x", "valid_from": "2024-06-01",
                             "end_state": 1}) == "[valid since 2024-06-01]"

    # the BACKSTOP: a marker shape nobody has seen yet still cannot deny the
    # read — it costs that hit its marker only, and it is counted
    before = BOUND_RENDER_OUTCOMES["marker_failed"]
    broken_annotation = {"id": "broken", "content": "still readable",
                         "session_id": "s1", "session_date": "2026-01-01"}
    block = _render_block(broken_annotation)
    # the marker's OWN contract is now "never raises" for stored shapes, so the
    # backstop is exercised by a marker that does raise: the hit must keep its
    # session identity and content, and the skip must be counted
    import tortoise.retrieval as _r

    def _boom(_h):
        raise RuntimeError("marker shape nobody has seen yet")

    monkeypatch.setattr(_r, "_validity_marker", _boom)
    block = _render_block(broken_annotation)
    assert "still readable" in block
    assert "[session s1]" in block and "(session date 2026-01-01)" in block
    assert BOUND_RENDER_OUTCOMES["marker_failed"] == before + 1
    # the read as a whole still completes over a mixed set
    assert "still readable" in render_context([broken_annotation, good])


def test_search_annotation_distinguishes_open_from_unknown(lane):
    sdk, _events = lane
    still = sdk.create_point("statement", "still", validFrom="2024-01-01")
    ended = sdk.create_point("statement", "ended", validFrom="2024-01-01",
                             validToPrecision="unknown")
    hits = {r["id"]: r for r in _scan(sdk)}
    assert hits[still["id"]]["end_state"] == "open"
    assert "valid_to_precision" not in hits[still["id"]]
    assert hits[ended["id"]]["end_state"] == "unknown"
    assert hits[ended["id"]]["valid_to_precision"] == "unknown"


def test_search_annotation_carries_declared_precision(lane):
    sdk, _events = lane
    p = sdk.create_point("statement", "dated", validFrom="2024-01-01",
                         validFromPrecision="year")
    hit = next(r for r in _scan(sdk) if r["id"] == p["id"])
    assert hit["valid_from_precision"] == "year"
    assert hit["end_state"] == "open"


def test_declared_precision_on_an_unorderable_start_reads_null_everywhere(lane):
    """#9244 residual of #9201, measured through the PUBLIC write.

    ``create_point(validFrom="TBD", validFromPrecision="year")`` is ACCEPTED —
    ``validate_validity_precision`` only refuses a declaration on an ABSENT
    bound, and narrowing that is a write-contract change this read-path lane
    deliberately does not make (#9244: that shape goes to the owner). What is
    fixed here is the READ: every window surface must report the start's
    precision as ``None``, because the stored bound names no instant.

    The CONTROL is the same declaration on an ORDERABLE start, which must still
    report ``year`` — otherwise this test would pass by erasing declarations.
    """
    sdk, _events = lane
    from tortoise.assembly import docker_walker_port
    from tortoise.sdk import _decorate_fallback_hits

    p = sdk.create_point("statement", "declared on no instant",
                         validFrom="TBD", validFromPrecision="year")
    # non-vacuous: the write boundary stored BOTH the bound and the declaration
    props = _window_props(sdk, p["id"])
    assert props["validFrom"] == "TBD", props
    assert props["validFromPrecision"] == "year", props

    # 1. get_historical_knowledge (raw bound)
    gk = sdk.get_historical_knowledge(p["id"], A_DATE)
    assert gk["chain"][0]["valid_from_precision"] is None

    # 2. assembly window rows
    port = docker_walker_port(sdk)
    today = _utc_today()
    row = next(r for r in port.window_rows(today, today) if r["id"] == p["id"])
    assert row["valid_from_precision"] is None

    # 3. search/recall wire — PRESENT key, ``None`` value (dropping the key
    #    would be the cross-surface drift of #2350 review P2).
    hit = next(r for r in _scan(sdk) if r["id"] == p["id"])
    assert "valid_from_precision" in hit, hit
    assert hit["valid_from_precision"] is None, hit

    # 4. the embedded fallback decoration
    fb = _decorate_fallback_hits([{"id": p["id"]}], sdk._get_proj().g)
    assert fb[0].get("valid_from_precision") is None, fb[0]

    # CONTROL — the same declaration on an ORDERABLE start still reads ``year``
    # (the day is re-read for the lookup: ``window_rows`` selects on
    # ``when OR createdAt``, and ``q`` is only reachable through its
    # ``createdAt`` stamp, so a run crossing UTC midnight between the two
    # ``_utc_today()`` calls would drop it — the #9406 clock-reading class
    # this file's ``_utc_today`` docstring records).
    q = sdk.create_point("statement", "declared on an instant",
                         validFrom="2024-06-01", validFromPrecision="year")
    assert sdk.get_historical_knowledge(
        q["id"], A_DATE)["chain"][0]["valid_from_precision"] == "year"
    q_day = _utc_today()
    qrow = next(r for r in port.window_rows(q_day, q_day) if r["id"] == q["id"])
    assert qrow["valid_from_precision"] == "year"
    qhit = next(r for r in _scan(sdk) if r["id"] == q["id"])
    assert qhit["valid_from_precision"] == "year"
    qfb = _decorate_fallback_hits([{"id": q["id"]}], sdk._get_proj().g)
    assert qfb[0].get("valid_from_precision") == "year"


def test_declared_precision_on_an_unorderable_end_reads_null_both_sides(lane):
    """#9244 applies to BOTH ends — ``effective_precision`` is end-agnostic.

    The issue asked that the both-ends consequence be stated EXPLICITLY rather
    than discovered: a declaration on a PRESENT-but-unorderable ``validTo``
    (accepted by the same write boundary, which only refuses a declaration on an
    ABSENT end) also resolves to no precision.

    ⚠️ **The fixture is a PRE-2026-10-26 row, written directly.** Under the
    #9202 shaping ruling a caller ``validTo="TBD"`` is SHAPED (no value + the
    declared ``unknown`` marker), so this pair can no longer be produced by the
    public write: shaping would leave a concrete ``validToPrecision`` beside an
    absent bound, and the #2350 precision gate refuses that pair. The row here is
    the one that exists on graphs written BEFORE the ruling, which is exactly the
    population this read property is about — and every assertion below is
    unchanged, because the ruling deliberately left the read path (and those
    rows) alone.

    ``end_state`` stays ``known`` for a DATED bound (the control below).
    Whether a stored-but-un-datable END should report ``END_UNKNOWN`` was
    #9202's owner decision; it was ruled on 2026-10-25 (option B) — it does,
    pinned by ``test_unorderable_end_is_emitted_as_null_on_every_read_surface``.
    """
    sdk, _events = lane
    p = sdk.create_point("statement", "declared end on no instant",
                         validFrom="2024-06-01")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) "
        "SET n.validTo = 'TBD', n.validToPrecision = 'year'",
        params={"i": p["id"]})
    props = _window_props(sdk, p["id"])
    assert props["validTo"] == "TBD", props
    assert props["validToPrecision"] == "year", props

    gk = sdk.get_historical_knowledge(p["id"], A_DATE)["chain"][0]
    assert gk["valid_to_precision"] is None
    assert gk["end_state"] == "unknown"

    hit = next(r for r in _scan(sdk) if r["id"] == p["id"])
    assert "valid_to_precision" in hit, hit
    assert hit["valid_to_precision"] is None, hit
    assert hit["end_state"] == "unknown", hit

    # CONTROL — the same declaration on an ORDERABLE end still reads ``year``
    q = sdk.create_point("statement", "declared end on an instant",
                         validFrom="2024-06-01", validTo="2026-01-01",
                         validToPrecision="year")
    assert sdk.get_historical_knowledge(
        q["id"], A_DATE)["chain"][0]["valid_to_precision"] == "year"
    qhit = next(r for r in _scan(sdk) if r["id"] == q["id"])
    assert qhit["valid_to_precision"] == "year"


def test_undated_hits_emit_no_precision_keys(lane):
    sdk, _events = lane
    p = sdk.create_point("statement", "timeless")
    hit = next(r for r in _scan(sdk) if r["id"] == p["id"])
    for key in ("valid_from_precision", "valid_to_precision", "end_state"):
        assert key not in hit, (key, hit)


def test_get_historical_knowledge_nearest_does_not_gain_an_unrecorded_content_key(lane):
    """#2350 review P2: ``nearest``'s pre-#2350 shape was
    ``{id, valid_from, valid_to, malformed}`` — it never carried ``content``
    (``valid_point``/``candidates`` did). Routing it through ``window_entry``
    with the default ``include_content=True`` silently grew a public reply
    field nobody recorded (CONTRIBUTING.md makes that a reviewing obligation,
    and no check can see an unrecorded field). Pre-fix this asserted key set
    contained ``content``."""
    sdk, _events = lane
    p = sdk.create_point("statement", "claim", validFrom="2024-06-01")
    out = sdk.get_historical_knowledge(p["id"], "2020-01-01")
    assert out["status"] == "absent"
    assert "nearest" in out
    assert "content" not in out["nearest"], sorted(out["nearest"])
    # the additive precision triple IS the intended delta on nearest.
    assert out["nearest"]["valid_from_precision"] == "day"
    assert out["nearest"]["end_state"] == "open"


def test_embedded_fallback_decorates_a_falsey_but_present_start(lane):
    """#2350 review P2 + #9201: the degraded TF-IDF fallback decoration owed the
    same precision block as the primary ``SearchResult.to_dict`` gate, but its
    gate omitted ``valid_from_precision``. For a falsey-but-PRESENT start
    (``validFrom=''``) the fetched state is
    ``valid_from=None, valid_from_precision=None, end_state='open'`` — the
    bound is present but names no instant, so it carries no granularity
    (#9201) — and the block is still EMITTED here rather than dropped: the
    key is present with ``None``. Pre-#2350 P2 both keys were absent on this
    surface while the primary path emitted them."""
    from tortoise.sdk import _decorate_fallback_hits

    sdk, _events = lane
    p = sdk.create_point("statement", "empty start", validFrom="")
    assert _props(sdk, p["id"])["validFrom"] == ""

    hits = _decorate_fallback_hits([{"id": p["id"]}], sdk._get_proj().g)
    # #9201: an unorderable start has no precision — but the block is still
    # EMITTED (the bound is present), with ``None``, so no surface drops it.
    assert "valid_from_precision" in hits[0]
    assert hits[0]["valid_from_precision"] is None
    assert hits[0]["end_state"] == "open"

    # control — an undated hit still gains NO window keys (the gate is not a
    # blanket emit).
    q = sdk.create_point("statement", "timeless")
    plain = _decorate_fallback_hits([{"id": q["id"]}], sdk._get_proj().g)
    for key in ("valid_from_precision", "valid_to_precision", "end_state"):
        assert key not in plain[0], (key, plain[0])


def test_assembly_window_rows_carry_the_precision_block(lane):
    sdk, _events = lane
    from tortoise.assembly import docker_walker_port
    ended = sdk.create_point("statement", "ended", validFrom="2024-01-01",
                             validToPrecision="unknown")
    port = docker_walker_port(sdk)
    today = _utc_today()
    rows = port.window_rows(today, today)
    row = next(r for r in rows if r["id"] == ended["id"])
    assert row["end_state"] == "unknown"
    assert row["valid_to_precision"] == "unknown"


def test_falsey_but_present_bound_resolves_identically_on_all_three_read_paths(lane):
    """#2350 review P2: the ONE read-projection home gates on ``is not None``
    (a falsey-but-PRESENT bound — ``validFrom=""`` — is a REAL bound, the
    same predicate ``_covers`` orders with), so search/recall, assembly and
    ``get_historical_knowledge`` must resolve the SAME stored window the same
    way. Pre-fix, ``fetch_point_epistemic_state`` pre-normalized with
    ``or None`` (precision ``None``) and ``SearchResult.to_dict`` dropped the
    block entirely, while the other two surfaces said ``day``/``open`` — the
    exact cross-surface drift the module exists to prevent. #9201 then changed
    WHAT the agreed value is (an unorderable start has no precision, so the
    triple is ``None``/``None``/``open`` on all three); agreement is still the
    invariant under test."""
    sdk, _events = lane
    from tortoise.assembly import docker_walker_port

    p = sdk.create_point("statement", "empty start", validFrom="")
    # non-vacuous: the empty string is STORED (a falsey-but-present bound),
    # not refused and not normalized away at the write boundary.
    assert _props(sdk, p["id"])["validFrom"] == ""

    # 1. get_historical_knowledge (raw bound)
    gk = sdk.get_historical_knowledge(p["id"], A_DATE)
    assert gk["chain"][0]["valid_from_precision"] is None
    assert gk["chain"][0]["end_state"] == "open"

    # 2. assembly window rows (raw bound)
    port = docker_walker_port(sdk)
    today = _utc_today()
    row = next(r for r in port.window_rows(today, today) if r["id"] == p["id"])
    assert row["valid_from_precision"] is None
    assert row["end_state"] == "open"

    # 3. search/recall wire — PRESENT, with the same ``None`` (a dropped key
    #    would be the cross-surface drift of #2350 review P2 all over again)
    hit = next(r for r in _scan(sdk) if r["id"] == p["id"])
    assert "valid_from_precision" in hit
    assert hit["valid_from_precision"] is None
    assert hit["end_state"] == "open"


def test_unorderable_end_is_emitted_as_null_on_every_read_surface(lane):
    """#9201 (review P2): the presence datum is per-END, not start-only.

    ``effective_precision`` is end-agnostic, so a PRESENT but UNORDERABLE
    ``validTo`` (``"TBD"``) resolves to no precision. Without an END presence
    datum the two truthiness-gated wire surfaces would DROP
    ``valid_to_precision`` while ``assembly``/``get_historical_knowledge``
    emit ``null`` — a brand-new cross-surface divergence introduced by the
    start-side fix. All four surfaces must agree on ``None``.

    ``end_state`` stays ``known`` for a DATED bound: whether a stored-but-
    un-datable END should report ``END_UNKNOWN`` was #9202's owner decision and
    it was ruled on 2026-10-25 (option B) — ``unknown``, pinned in
    ``test_unorderable_end_is_emitted_as_null_on_every_read_surface`` below.
    """
    sdk, _events = lane
    from tortoise.assembly import docker_walker_port

    # A PRE-2026-10-26 row, written directly: the shaping ruling (#9202) makes
    # the public write store the canonical un-datable form instead, and the read
    # property pinned here is about rows that carry the literal value.
    p = sdk.create_point("statement", "unorderable end",
                         validFrom="2024-01-01")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) SET n.validTo = 'TBD'", params={"i": p["id"]})
    assert _props(sdk, p["id"])["validTo"] == "TBD"

    # 1. get_historical_knowledge (raw bound)
    gk = sdk.get_historical_knowledge(p["id"], A_DATE)
    assert gk["chain"][0]["valid_to_precision"] is None
    assert gk["chain"][0]["end_state"] == "unknown", (
        "#9202 owner ruling 2026-10-25: an end that names no instant is "
        f"un-datable, not dated: {gk['chain'][0]}"
    )

    # 2. assembly window rows
    port = docker_walker_port(sdk)
    today = _utc_today()
    row = next(r for r in port.window_rows(today, today) if r["id"] == p["id"])
    assert row["valid_to_precision"] is None
    assert row["end_state"] == "unknown", row

    # 3. search/recall wire
    hit = next(r for r in _scan(sdk) if r["id"] == p["id"])
    assert "valid_to_precision" in hit, hit
    assert hit["valid_to_precision"] is None
    assert hit["end_state"] == "unknown", hit

    # 4. the embedded fallback decoration
    from tortoise.sdk import _decorate_fallback_hits
    fb = _decorate_fallback_hits([{"id": p["id"]}], sdk._get_proj().g)
    assert "valid_to_precision" in fb[0], fb[0]
    assert fb[0]["valid_to_precision"] is None
    assert fb[0]["end_state"] == "unknown", fb[0]

    # CONTROL — an OPEN end (no validTo at all) keeps its absent key on the
    # wire; emitting ``null`` there would erase the open/un-datable distinction.
    open_ended = sdk.create_point("statement", "open end",
                                  validFrom="2024-01-01")
    open_hit = next(r for r in _scan(sdk) if r["id"] == open_ended["id"])
    assert "valid_to_precision" not in open_hit
    assert open_hit["end_state"] == "open"


def test_window_pre_filter_spends_no_cap_slots_on_out_of_window_createdAt_rows(
        lane):
    """#9406 (review): the pre-filter's ends are per FIELD, and the ``createdAt``
    end must NOT take the free-text one.

    ``window_rows`` is a capped, newest-first fetch
    (``ORDER BY p.createdAt DESC … LIMIT cap``), so a day of rows admitted
    ABOVE the window occupies the first cap slots and is then dropped by the
    ladder — the window answers empty for facts the graph holds. The rows
    below are dosed so the failure is unambiguous: 3 rows inside the window
    and 50 rows one raw day PAST it, cap 10.

    FAILS IF the ``createdAt`` end is widened to ``hi + 2`` (the free-text
    end): the 50 out-of-window rows sort first and all 3 in-window rows are
    starved (measured 0/3). Passing requires ``hi + 1`` on the clock-stamped
    field, which admits every timestamp whose raw date is its UTC date.

    SCOPE, stated because the name is narrow on purpose: this pins the
    ``createdAt`` leg only. The free-text legs pay a DOCUMENTED cost for
    admitting offset-bearing values (see ``_window_bounds``) — that cost is
    not asserted here, because asserting it would pin a limitation as if it
    were the contract.

    Every row is written at the projection handle with an EXPLICIT
    ``createdAt``: ``create_point`` stamps the wall clock, and this test's
    ordering has to be fixed, not read from the machine's date.
    """
    sdk, _events = lane
    from tortoise.assembly import docker_walker_port

    g = sdk._get_proj().g
    inside = [f"p-in-9406-{i}" for i in range(3)]
    for i, pid in enumerate(inside):
        g.query("CREATE (:Point {id:$id, content:$c, pointKind:'statement', "
                "status:'live', when:'2026-10-20T12:00:00+00:00', "
                "createdAt:'2026-10-20T12:00:00+00:00'})",
                params={"id": pid, "c": f"inside {i}"})
    for i in range(50):
        # OUT of the window on every field; raw createdAt date is hi+1
        g.query("CREATE (:Point {id:$id, content:$c, pointKind:'statement', "
                "status:'live', createdAt:'2026-10-21T00:05:00+00:00'})",
                params={"id": f"p-out-9406-{i}", "c": f"outside {i}"})

    rows = docker_walker_port(sdk).window_rows(
        datetime.date(2026, 10, 20), datetime.date(2026, 10, 20), 10)
    got = {r["id"] for r in rows}
    assert len(rows) <= 10
    assert set(inside) <= got, (
        "the widened band starved the window: fetched "
        f"{sorted(got)} with cap 10")


def test_window_pre_filter_does_not_fetch_date_only_rows_past_the_window(lane):
    """#9406 (review): the free-text end is ``"<hi+2>"`` and the literal value
    itself is EXCLUDED, so a date-only ``when`` on ``hi+2`` is not fetched.

    That value's ladder day is ``hi+2`` — a day outside the window — so
    admitting it buys nothing and spends cap slots at the head of the
    newest-first fetch. Dosed the same way as the ``createdAt`` leg: 3 rows
    inside the window, 50 date-only rows one day past it, cap 10.

    FAILS IF the ``<> $hi`` exclusion is dropped: the 50 past-window rows are
    admitted and the window starves (measured 0/3).

    The residual this leaves standing is a ``when`` TIMESTAMP whose raw date is
    ``hi+1`` but whose ladder day is ``hi+1`` — a string bound cannot tell it
    from the offset-bearing value the wider end exists to admit. Recorded in
    ``_window_bounds`` and on #9406, not asserted here.
    """
    sdk, _events = lane
    from tortoise.assembly import docker_walker_port

    g = sdk._get_proj().g
    inside = [f"w-in-9406-{i}" for i in range(3)]
    for i, pid in enumerate(inside):
        g.query("CREATE (:Point {id:$id, content:$c, pointKind:'statement', "
                "status:'live', when:'2026-10-20T12:00:00+00:00', "
                "createdAt:'2026-10-20T12:00:00+00:00'})",
                params={"id": pid, "c": f"w inside {i}"})
    for i in range(50):
        # date-only `when` on hi+2 -> ladder day hi+2, i.e. OUT of the window;
        # backfilled createdAt so they lead the newest-first fetch
        g.query("CREATE (:Point {id:$id, content:$c, pointKind:'statement', "
                "status:'live', when:'2026-10-22', "
                "createdAt:'2027-03-01T00:00:00+00:00'})",
                params={"id": f"w-out-9406-{i}", "c": f"w outside {i}"})

    rows = docker_walker_port(sdk).window_rows(
        datetime.date(2026, 10, 20), datetime.date(2026, 10, 20), 10)
    got = {r["id"] for r in rows}
    assert len(rows) <= 10
    assert set(inside) <= got, (
        "a date-only `when` past the window starved it: fetched "
        f"{sorted(got)} with cap 10")


def test_window_pre_filter_when_leg_takes_the_free_text_bounds(lane):
    """#9406 (review): the ``when`` leg's WIDER end is the point of this diff, so
    it needs a LIVE pin — a unit assertion on ``_window_bounds`` cannot see which
    end the call site handed the field.

    Dosed like the siblings: 50 date-only ``when`` rows one day past the window
    (ladder day ``hi+1``, i.e. outside) plus the one row the widening exists for
    — ``when="2026-10-21T02:00:00+09:00"``, whose UTC day IS the window's day —
    and an in-window control. At cap 10 the past-window population would take
    every slot.

    FAILS IF the ``when`` leg is narrowed to the ``createdAt`` end (the
    offset-bearing row disappears — the exact #9406 defect) or loses its
    exclusions (the 50 rows are fetched and starve the rest).
    """
    sdk, _events = lane
    from tortoise.assembly import docker_walker_port

    g = sdk._get_proj().g
    inside = [f"w-in-live-9406-{i}" for i in range(3)]
    for i, pid in enumerate(inside):
        g.query("CREATE (:Point {id:$id, content:$c, pointKind:'statement', "
                "status:'live', when:'2026-10-20T12:00:00+00:00', "
                "createdAt:'2026-10-20T12:00:00+00:00'})",
                params={"id": pid, "c": f"w inside {i}"})
    offset = "w-offset-live-9406"
    # a STALE caller createdAt, so ONLY the `when` leg can admit this row — the
    # point of the test. With a `createdAt` inside the stamped range the row
    # would arrive through THAT leg and the `when` end would go unpinned.
    g.query("CREATE (:Point {id:$id, content:'offset', pointKind:'statement', "
            "status:'live', when:'2026-10-21T02:00:00+09:00', "
            "createdAt:'2026-09-01T00:00:00+00:00'})",
            params={"id": offset})
    for i in range(50):
        # date-only `when` on hi+1 -> ladder day hi+1, OUT of the window;
        # backfilled createdAt so they would lead the newest-first fetch
        g.query("CREATE (:Point {id:$id, content:$c, pointKind:'statement', "
                "status:'live', when:'2026-10-21', "
                "createdAt:'2027-03-01T00:00:00+00:00'})",
                params={"id": f"w-out-live-9406-{i}", "c": f"w outside {i}"})

    rows = docker_walker_port(sdk).window_rows(
        datetime.date(2026, 10, 20), datetime.date(2026, 10, 20), 10)
    got = {r["id"] for r in rows}
    assert offset in got, (
        "the `when` leg dropped an offset-bearing value whose ladder day is IN "
        f"the window: fetched {sorted(got)}")
    assert set(inside) <= got, sorted(got)
    assert not [i for i in got if i.startswith("w-out-live-9406")], (
        f"date-only rows past the window were fetched: {sorted(got)}")


def test_window_pre_filter_lower_end_is_strict_about_its_date_only_value(lane):
    """#9406 (review): the LOWER end's mirror of the same leak.

    ``lo - 1`` is needed (a negative offset moves a raw date one day BACK), but
    the DATE-ONLY value ``lo-1`` is a day BELOW the window on its own ladder date
    — and a backfilled population of it starves the walk: measured, 250 date-only
    ``when='2026-10-19'`` rows + 3 in-window rows at the production cap 200 kept
    **0/3**, and starved the negative-offset row the widening exists for. The
    lower end is therefore STRICTLY greater.

    FAILS IF the ``>`` becomes ``>=``: the 250 rows are admitted at the head of
    the newest-first fetch and the window (and the offset row) starve.
    """
    sdk, _events = lane
    from tortoise.assembly import docker_walker_port

    g = sdk._get_proj().g
    inside = [f"lo-in-9406-{i}" for i in range(3)]
    for i, pid in enumerate(inside):
        g.query("CREATE (:Point {id:$id, content:$c, pointKind:'statement', "
                "status:'live', when:'2026-10-20T12:00:00+00:00', "
                "createdAt:'2026-10-20T12:00:00+00:00'})",
                params={"id": pid, "c": f"lo inside {i}"})
    neg = "lo-neg-9406"
    g.query("CREATE (:Point {id:$id, content:'neg', pointKind:'statement', "
            "status:'live', when:'2026-10-19T22:00:00-05:00', "
            "createdAt:'2026-10-20T03:00:00+00:00'})",
            params={"id": neg})
    for i in range(250):
        g.query("CREATE (:Point {id:$id, content:$c, pointKind:'statement', "
                "status:'live', when:'2026-10-19', "
                "createdAt:'2027-03-01T00:00:00+00:00'})",
                params={"id": f"lo-out-9406-{i}", "c": f"lo outside {i}"})

    rows = docker_walker_port(sdk).window_rows(
        datetime.date(2026, 10, 20), datetime.date(2026, 10, 20), 200)
    got = {r["id"] for r in rows}
    assert set(inside) <= got, (
        "a date-only `lo-1` population starved the window: "
        f"fetched {len(got)} rows with cap 200")
    assert neg in got, (
        "the negative-offset row the `lo-1` widening exists for was lost: "
        f"{sorted(got)[:5]}")
    assert not [i for i in got if i.startswith("lo-out-9406")], (
        f"date-only rows below the window were fetched: {len(got)} rows")


def test_window_pre_filter_events_leg_mirrors_the_points_leg(lane):
    """#9406 (review): the EVENTS leg is the points leg's twin and must not
    drift from it — it takes the free-text ends (``startedAt`` is caller /
    connector supplied) and the same DATE-ONLY exclusions.

    Dosed so a dropped exclusion is visible: 50 Events whose only date is a
    date-only ``startedAt`` one day past the window (ladder day ``hi+1``, i.e.
    outside) and one offset-bearing ``startedAt`` on the boundary day (ladder
    day ``hi``, i.e. INSIDE — the row the wider end exists for). At cap 10 the
    past-window population would take every slot.

    FAILS IF the events leg loses its exclusion (the 50 rows are fetched and
    the offset row is pushed past the cap) or takes the narrow ``createdAt``
    end (the offset row is not fetched at all).
    """
    sdk, _events = lane
    from tortoise.assembly import docker_walker_port

    for i in range(50):
        sdk.create_event(f"past-window event {i}", "occurrence",
                         startedAt="2026-10-21")
    offset = sdk.create_event("offset event", "occurrence",
                              startedAt="2026-10-21T02:00:00+09:00")
    inside = sdk.create_event("inside event", "occurrence",
                              startedAt="2026-10-20T12:00:00+00:00")

    rows = docker_walker_port(sdk).window_rows(
        datetime.date(2026, 10, 20), datetime.date(2026, 10, 20), 10)
    event_ids = {r["id"] for r in rows if r["kind"] == "event"}
    assert offset["eventId"] in event_ids, (
        "the events leg dropped an offset-bearing startedAt whose ladder day "
        f"is IN the window: fetched {sorted(event_ids)}")
    assert inside["eventId"] in event_ids, sorted(event_ids)
    # the date-only `hi+1` population is not fetched at all (ladder day hi+1)
    assert len(event_ids) == 2, (
        f"date-only rows past the window were fetched: {sorted(event_ids)}")


def test_createdAt_writer_claim_and_the_caller_offset_residual(lane):
    """The ``createdAt`` end is narrow because of a WRITER claim, so measure
    the writer — and pin the one shape it does not cover as a RECORDED RESIDUAL.

    1. WRITER: a plain ``create_point`` stores a UTC stamp (``+00:00``), which
       is what makes the narrow end sound for every in-repo writer.
    2. RESIDUAL (#9406, recorded, NOT desired): ``createdAt`` is also a caller
       prop (``create_point(**props)`` — ``api.py``/``ingest.py``/the
       source-inheritance path all pass one), and a caller-supplied OFFSET is
       stored verbatim. The ladder reads ``2026-10-21T02:00+09:00`` as the
       window day ``2026-10-20``, while its raw string is past the window's
       narrow end — so ``window_rows`` does not fetch it. Widening the end
       would close this and reopen the cap starvation measured above; the
       honest cure is a fetch that cannot starve (recorded on #9406).

    Both halves can fail: (1) if a writer starts stamping local time, (2) if a
    later change closes the residual — in which case this test should be
    flipped to assert the row IS returned.
    """
    sdk, _events = lane
    from tortoise.assembly import _norm_date, docker_walker_port

    plain = sdk.create_point("statement", "plain stamp")
    stamp = _props(sdk, plain["id"])["createdAt"]
    assert stamp.endswith(("+00:00", "Z")), stamp

    offset = sdk.create_point("statement", "caller offset stamp",
                              createdAt="2026-10-21T02:00:00+09:00")
    assert _props(sdk, offset["id"])["createdAt"] == \
        "2026-10-21T02:00:00+09:00", "a caller prop must be stored verbatim"
    assert _norm_date("2026-10-21T02:00:00+09:00") == datetime.date(2026, 10, 20)
    rows = docker_walker_port(sdk).window_rows(
        datetime.date(2026, 10, 20), datetime.date(2026, 10, 20))
    assert offset["id"] not in {r["id"] for r in rows}, (
        "the caller-offset residual is CLOSED — flip this test to assert the "
        "row IS returned, and drop the residual note in _window_bounds")


def test_check_consistency_reference_fold_states_the_terminalizer_precision(lane):
    """#2350 review P1: ``check_consistency``'s reference fold must state
    ``validToPrecision`` on every precision-carrying terminalizer.

    The live writers (``invalidate_point`` / ``supersede_point``) SET
    ``n.validToPrecision`` and the graph/replay folds restore it, so a
    reference fold that omits it reports a graph the live path itself wrote
    as diverged — with advice ("replay the journal") that cannot clear it,
    the #5048/#3817 class. Fails on the pre-fix fold, where ``validToPrecision``
    is graph-only and lands in ``fields``."""
    from tortoise.consistency import check_consistency

    sdk, events = lane
    # (a) invalidate with the closed-unknown end
    a = sdk.create_point("statement", "a", validFrom="2024-01-01")
    other = sdk.create_point("statement", "other", validFrom="2024-06-01")
    sdk.invalidate_point(a["id"], other["id"], valid_to_precision="unknown")
    # (b) supersede inheriting a declared precision from the successor's start
    b = sdk.create_point("statement", "b", validFrom="2024-02-01")
    c = sdk.create_point("statement", "c", validFrom="2025-03-01",
                         validFromPrecision="month")
    sdk.supersede_point(b["id"], c["id"])
    # (c) a legacy supersede with NO precision stays symmetric-absent
    d = sdk.create_point("statement", "d", validFrom="2024-03-01")
    e = sdk.create_point("statement", "e", validFrom="2026-01-01")
    sdk.supersede_point(d["id"], e["id"])

    result = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert result["ok"], result.get("divergent_points")


# ── surface discipline (no new MCP tool; params on existing verbs only) ───

def test_no_new_mcp_tool_was_added_for_precision():
    """#2350 indicator 4: the feature grows SDK *arguments* on existing validity
    write verbs, not the MCP tool surface.

    The guard the review rejected asserted only that no tool *name contains the
    substring* ``"precision"`` — true of any registry and false evidence for
    "no new tool" (a tool named ``tortoise_set_validity_window`` passed it).
    This pins the actual delta: the registered tool-name set must equal the
    frozen baseline ``config/surface-manifest.yml`` records (cut under the #4282
    approval mandate). A committed registry-only addition — the un-approved
    shape — reds here whatever the tool is named.

    Non-vacuity: an injected entry with no ``"precision"`` substring is shown
    to be caught by the same comparison the test asserts, then the registry is
    restored.
    """
    import dataclasses
    from pathlib import Path

    import yaml

    from tortoise.tool_registry import TOOL_REGISTRY

    baseline = yaml.safe_load(
        (Path(__file__).resolve().parent.parent
         / "config" / "surface-manifest.yml").read_text(encoding="utf-8"))
    recorded = {str(r["name"]) for r in baseline["rows"]
                if not str(r["name"]).startswith("sdk:")}

    def surface_grew(registry) -> bool:
        return {t.name for t in registry} != recorded

    assert not surface_grew(TOOL_REGISTRY), (
        "the MCP tool surface changed — #2350 is additive at the SDK-argument "
        "level only (supersede_point/invalidate_point gained a kwarg). "
        f"registered={sorted(t.name for t in TOOL_REGISTRY)} "
        f"recorded={sorted(recorded)}"
    )

    original = list(TOOL_REGISTRY)
    try:
        TOOL_REGISTRY.append(dataclasses.replace(
            TOOL_REGISTRY[0], name="tortoise_set_validity_window"))
        assert surface_grew(TOOL_REGISTRY), (
            "a new tool whose name omits 'precision' was not detected — the "
            "rejected substring guard's failure mode"
        )
    finally:
        TOOL_REGISTRY[:] = original
    assert not surface_grew(TOOL_REGISTRY), "the registry was not restored"


def test_precision_is_an_optional_param_on_existing_write_verbs():
    sup = inspect.signature(TortoiseSDK.supersede_point).parameters
    inv = inspect.signature(TortoiseSDK.invalidate_point).parameters
    assert "valid_to_precision" in sup and sup["valid_to_precision"].default is None
    assert "valid_to_precision" in inv and inv["valid_to_precision"].default is None
    # create/update keep their generic props passthrough — no signature change.
    assert any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in inspect.signature(TortoiseSDK.create_point).parameters.values())
    assert any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in inspect.signature(TortoiseSDK.update_point).parameters.values())


# ── the ONTOLOGY record: the doc records THIS feature where its convention says ──

def test_ontology_records_the_2350_precision_layer():
    """#2350 review P2: the canonical ONTOLOGY doc is where this feature's
    contract is stated, and the doc's own convention is that the change it
    records is named in the changelog AND spelled out in the body. The
    re-targeted invariant: the v3.28 changelog entry names #2350, and §4.7
    carries the per-end-precision / 3-state-end paragraph referencing its home
    module.

    The changelog block lives in ``docs/ONTOLOGY-CHANGELOG.md`` since the
    model/changelog split (this branch, #8745): ``ONTOLOGY.md`` is the model and
    states the §4.7 contract; the per-model-change record, including the v3.28
    entry, is read from the changelog file.

    Fails on the pre-#2350 doc (merge base ``ce9d97e0``): there the v3.28 entry
    names only #3998, and neither the §4.7 heading, ``validToPrecision`` nor
    ``tortoise/validity.py`` appears. The previous version of this test asserted
    a v3.27→v3.28 bump that #3998 had already shipped on main, so it could not
    fail for #2350."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    doc = (root / "docs" / "ONTOLOGY.md").read_text(encoding="utf-8")
    changelog = (root / "docs" / "ONTOLOGY-CHANGELOG.md").read_text(
        encoding="utf-8")
    v328 = re.search(r"Changelog v3\.28 \(([^)]*)\)", changelog)
    assert v328, "the ONTOLOGY changelog carries no Changelog v3.28 entry"
    assert "#2350" in v328.group(1), (
        "the v3.28 changelog entry does not record #2350: " + v328.group(1))
    assert "Per-end precision and the 3-state end" in doc, (
        "§4.7 does not carry the #2350 precision / 3-state-end paragraph")
    assert "validToPrecision" in doc
    assert "tortoise/validity.py" in doc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
