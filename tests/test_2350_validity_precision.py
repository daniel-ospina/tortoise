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
    # a caller-DECLARED token is an assertion, not a derivation: it wins
    assert effective_precision("TBD", "year") == "year"
    # a numeric bound is an instant even when ``_created_sort_key`` cannot
    # float() it — the read path must not acquire a raise it never had
    assert effective_precision(10 ** 400, None) == "day"


def test_window_read_fields_denies_precision_to_an_unorderable_bound():
    """#9201: the ONE read-projection home reports no precision for a bound
    that names no instant, on either end, and leaves the orderable end alone."""
    from tortoise.validity import window_read_fields

    assert window_read_fields("", "2024-01-01", None, None) == {
        "valid_from_precision": None,
        "valid_to_precision": "day",
        "end_state": "known",
    }
    assert window_read_fields("2024-01-01", "TBD", None, None) == {
        "valid_from_precision": "day",
        "valid_to_precision": None,
        "end_state": "known",
    }


def test_end_state_is_three_valued():
    from tortoise.validity import END_KNOWN, END_OPEN, END_UNKNOWN, end_state

    assert end_state(None, None) == END_OPEN
    assert end_state(None, "") == END_OPEN
    assert end_state("2024-01-01", None) == END_KNOWN
    assert end_state("2024-01-01", "month") == END_KNOWN
    assert end_state(None, "unknown") == END_UNKNOWN


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

    ``end_state`` stays ``known``: whether a stored-but-un-datable END should
    report ``END_UNKNOWN`` is #9202's owner decision, and this read fix does
    not pre-empt it. That assertion is the pin — #9202 changes it on purpose.
    """
    sdk, _events = lane
    from tortoise.assembly import docker_walker_port

    p = sdk.create_point("statement", "unorderable end",
                         validFrom="2024-01-01", validTo="TBD")
    assert _props(sdk, p["id"])["validTo"] == "TBD"

    # 1. get_historical_knowledge (raw bound)
    gk = sdk.get_historical_knowledge(p["id"], A_DATE)
    assert gk["chain"][0]["valid_to_precision"] is None
    assert gk["chain"][0]["end_state"] == "known"

    # 2. assembly window rows
    port = docker_walker_port(sdk)
    today = _utc_today()
    row = next(r for r in port.window_rows(today, today) if r["id"] == p["id"])
    assert row["valid_to_precision"] is None

    # 3. search/recall wire
    hit = next(r for r in _scan(sdk) if r["id"] == p["id"])
    assert "valid_to_precision" in hit, hit
    assert hit["valid_to_precision"] is None
    assert hit["end_state"] == "known"

    # 4. the embedded fallback decoration
    from tortoise.sdk import _decorate_fallback_hits
    fb = _decorate_fallback_hits([{"id": p["id"]}], sdk._get_proj().g)
    assert "valid_to_precision" in fb[0], fb[0]
    assert fb[0]["valid_to_precision"] is None

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
