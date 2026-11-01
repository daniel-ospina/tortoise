"""E6 (#1538) — bi-temporal validity windows.

T1: supersede_point/invalidate_point stamp validTo/expiredAt (contiguity,
    kwarg/read/fallback matrix); T2: when→validFrom on the create path;
    T3: get_historical_knowledge chain-walk (in-window, open interval, ambiguity,
    honest absence).

Runnable with:
  uv run pytest tests/test_validity_windows.py -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK with temp database. Closed after test."""
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_validity_test_"), "test.db"
    )
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _make_point(sdk: TortoiseSDK, content: str = "test content", **kw):
    return sdk.create_point(
        kw.pop("kind", "statement"), content, **kw
    )


def _props(sdk: TortoiseSDK, pid: str) -> dict:
    row = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)",
        params={"id": pid}).result_set
    assert row, f"point {pid} missing"
    return dict(row[0][0])


# ── #4096: fixture hygiene — the temp tree is reclaimed on teardown ───────

def test_sdk_fixture_reclaims_its_temp_tree():
    """The ``sdk`` fixture must remove the tree it mkdtemp's (#4096).

    Drives the fixture's own generator the way pytest does — one ``next()`` for
    setup, a second for teardown — so the assertion is deterministic and needs no
    cross-test ordering. Fails on the pre-#4096 fixture, whose finalizer only
    closed the SDK and left ``tortoise_validity_test_*`` behind (5,725 of them
    were live on the dev box).
    """
    gen = sdk.__wrapped__()
    live = next(gen)
    tree = os.path.dirname(live._db_path)
    assert os.path.isdir(tree), "fixture did not create its temp tree"
    with pytest.raises(StopIteration):
        next(gen)  # run the fixture's teardown
    assert not os.path.exists(tree), f"fixture left its temp tree behind: {tree}"


# ── T1: supersede_point stamps the window (contiguity + fallback matrix) ──

def test_supersede_valid_from_is_the_sole_source_when_successor_is_undated(sdk):
    """The ``valid_from`` kwarg is the window-END source — and the SOLE source,
    because this successor carries no stored ``validFrom``.

    ⚠️ This scenario produces an OVERLAP, not contiguity: an undated successor
    has an open window start, so ``_covers`` treats it as covering every
    instant and the predecessor's kwarg-written ``validTo`` cannot meet it
    (ONTOLOGY.md §4.7; the undated-successor overlap is tracked as #3945 —
    NOT #3985, which is the separate falsey-but-present no-kwarg residual).
    Contiguity from the kwarg is
    demonstrated by ``test_supersede_successor_valid_from_contiguity``, where
    the successor IS dated.

    Name pinned to that condition: the kwarg is refused when it DISAGREES with
    a stored ``validFrom`` (see the disagreement tests below), so "explicit
    valid_from wins" is no longer an unconditional claim (ONTOLOGY.md §4.7)."""
    old = _make_point(sdk, content="gym at 6pm")
    new = _make_point(sdk, content="gym at 5pm")
    result = sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-14")
    # return shape unchanged (additive regression)
    assert result == {"invalidated": True, "id": old["id"],
                      "corrected_by": new["id"], "edges_transferred": 0}
    op = _props(sdk, old["id"])
    assert op["status"] == "superseded"
    assert op["outdated"] is True
    assert op["validTo"] == "2026-06-14"
    assert op["expiredAt"]  # present
    assert op["expiredAt"] >= op["createdAt"]
    # successor untouched (its validFrom belongs to the create path, D3)
    np = _props(sdk, new["id"])
    assert "validTo" not in np


def test_supersede_reads_successor_valid_from(sdk):
    """Kwarg absent → read the successor's validFrom property."""
    old = _make_point(sdk, content="gym at 6pm")
    new = _make_point(sdk, content="gym at 5pm", validFrom="2026-06-14")
    sdk.supersede_point(old["id"], new["id"])
    assert _props(sdk, old["id"])["validTo"] == "2026-06-14"


def test_supersede_fallback_to_successor_created_at(sdk):
    """Kwarg absent + no successor validFrom → successor's createdAt."""
    old = _make_point(sdk, content="gym at 6pm")
    new = _make_point(sdk, content="gym at 5pm")
    created = new["createdAt"]
    sdk.supersede_point(old["id"], new["id"])
    assert _props(sdk, old["id"])["validTo"] == created


def test_supersede_fallback_to_now(sdk):
    """Kwarg absent + no validFrom + no createdAt → now (monotone, no gap)."""
    old = _make_point(sdk, content="gym at 6pm")
    new = _make_point(sdk, content="gym at 5pm")
    # remove createdAt to force the final fallback
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) REMOVE n.createdAt",
        params={"id": new["id"]})
    sdk.supersede_point(old["id"], new["id"])
    vt = _props(sdk, old["id"])["validTo"]
    assert vt  # non-empty now-iso
    assert vt.startswith("20")  # sane ISO year prefix


def test_supersede_undated_legacy_pair_still_supersedes(sdk):
    """Undated legacy points (no props) supersede cleanly via fallback."""
    old = _make_point(sdk, content="legacy claim A")
    new = _make_point(sdk, content="legacy claim B")
    for pid in (old["id"], new["id"]):
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) REMOVE n.validFrom, n.validTo",
            params={"id": pid})
    result = sdk.supersede_point(old["id"], new["id"])
    assert result["invalidated"] is True
    assert _props(sdk, old["id"])["status"] == "superseded"
    assert _props(sdk, old["id"]).get("validTo")  # from createdAt/now fallback


def test_supersede_successor_valid_from_contiguity(sdk):
    """Contiguity: old.validTo == successor.validFrom — no gap between
    windows (Graphiti semantics).

    (Name corrected: this exercises a successor that carries an explicit
    ``validFrom``; the ``createdAt`` fallback is the branch that produces an
    OVERLAP, and is covered by
    ``test_supersede_fallback_to_successor_created_at``.)"""
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v2", validFrom="2026-06-10")
    sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-10")
    assert _props(sdk, old["id"])["validTo"] == "2026-06-10"
    assert _props(sdk, new["id"])["validFrom"] == "2026-06-10"


def test_supersede_disagreeing_valid_from_refused(sdk):
    """A ``valid_from`` kwarg that disagrees with the successor's STORED
    ``validFrom`` is refused BEFORE any mutation.

    Trusting the kwarg verbatim let it pick the predecessor's window end,
    which broke chain contiguity silently in BOTH directions:
      * EARLIER kwarg → GAP: neither window covers the instants strictly
        between them — the predecessor's end is the kwarg instant (inclusive)
        and the successor's start is the stored one — so the uncovered region
        is ``(kwarg, stored)`` and ``get_historical_knowledge`` reports honest absence
        for instants that fall in it;
      * LATER kwarg → OVERLAP: both windows cover ``[stored, kwarg]`` (both
        ends inclusive), so every instant inside it reads ``ambiguous``.

    Fail-closed: the refusal is raised in the resolution block (after the
    lifecycle guards, before the PointSuperseded emit and every write), so
    the graph is left untouched and the agreeing path still works."""
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v2", validFrom="2026-06-10")

    # (a) kwarg EARLIER than the stored value → would GAP (06-05, 06-10)
    with pytest.raises(ValueError, match="disagrees"):
        sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-05")
    # (b) kwarg LATER than the stored value → would OVERLAP [06-10, 06-20]
    with pytest.raises(ValueError, match="disagrees"):
        sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-20")

    # Fail-closed: no mutation on either refusal.
    op = _props(sdk, old["id"])
    assert op.get("status") != "superseded"
    assert not op.get("outdated")
    assert "validTo" not in op
    assert "expiredAt" not in op
    # ...and fail-closed across the EVENT JOURNAL too: `_emit_event` runs
    # append-before-mutation, so if the guard ever moved after the
    # PointSuperseded emit a refusal would journal a phantom supersession that
    # mutated nothing. The graph-state assertions above cannot see that.
    from tortoise.event_store import read_after
    assert read_after(sdk._get_proj(), 0, types=["PointSuperseded"]) == []
    assert sdk._get_proj().g.query(
        "MATCH (a:Point {id:$n})-[:CORRECTS]->(b:Point {id:$o}) RETURN a.id",
        params={"n": new["id"], "o": old["id"]}).result_set == []

    # The agreeing kwarg still works (documented resolution order preserved)
    # and the chain it writes is CONTIGUOUS — the gap instant resolves to the
    # predecessor, the post-transition instant resolves to the successor with
    # no ambiguity.
    sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-10")
    assert _props(sdk, old["id"])["validTo"] == "2026-06-10"
    at_gap = sdk.get_historical_knowledge(new["id"], "2026-06-07")
    assert at_gap["found"] is True
    assert at_gap["valid_point"]["id"] == old["id"]
    at_overlap = sdk.get_historical_knowledge(new["id"], "2026-06-15")
    assert at_overlap["found"] is True
    assert at_overlap.get("ambiguous") is not True
    assert at_overlap["valid_point"]["id"] == new["id"]


def test_supersede_valid_from_format_difference_is_agreement(sdk):
    """Instant-level, not string-level, comparison via ``_created_sort_key``
    — the same mixed-format primitive ``get_historical_knowledge``'s ``_covers``
    uses to decide coverage. A format-only difference ("…Z" vs "…+00:00")
    names the same instant and must not be refused."""
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v2",
                      validFrom="2026-06-10T00:00:00Z")
    sdk.supersede_point(old["id"], new["id"],
                        valid_from="2026-06-10T00:00:00+00:00")
    assert _props(sdk, old["id"])["validTo"] == "2026-06-10T00:00:00+00:00"


def test_supersede_valid_from_cross_format_disagreement_refused(sdk):
    """A disagreement ACROSS formats (date-only kwarg vs offset-aware stored
    value) is still caught — the guard is not a raw string compare.

    The two dates are a full day apart, deliberately, so the assertion measures
    the cross-format DISAGREEMENT and not the day boundary. (A date-only value
    now anchors to UTC midnight — #3982 — so the spacing no longer varies by
    host; it is kept because the disagreement is the property under test.)
    """
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v2",
                      validFrom="2026-06-10T00:00:00+00:00")
    with pytest.raises(ValueError, match="disagrees"):
        sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-09")


def test_supersede_valid_from_same_day_instant_disagreement_refused(sdk):
    """The contract is the same INSTANT, not the same calendar day.

    The two literals the GUARD COMPARES — the kwarg and the successor's stored
    ``validFrom`` — carry an explicit time and offset, so nothing depends on the
    host timezone. (The predecessors' date-only ``validFrom`` above is never
    passed to the guard, so it is deliberately kept out of the comparison; it
    anchors to UTC midnight under #3982.) Four
    properties:

      * same day, different instant → REFUSED. Without this, a guard weakened
        to CALENDAR-DAY equality (``epoch // 86400``) would accept it. The
        direction of the disagreement decides the damage: an EARLIER kwarg
        leaves a GAP between the predecessor's end and the successor's start,
        a LATER one an OVERLAP — see case (a) and the parent
        ``test_supersede_disagreeing_valid_from_refused`` for both.
      * sub-second disagreement → REFUSED (case (c), 0.8 s apart). This case
        refuses a difference below one second, which the coarsest weakened
        guards would accept: a tolerance-based equality
        (``abs(kwarg - stored) < 1.0``) or whole-second truncation
        (``int(x)``) treats these two instants as equal, so it fails here.
      * disagreement of ONE MICROSECOND → also REFUSED (case (e)), and a zero
        difference with a DIFFERENT fractional encoding → accepted (case (f)).
        Together they pin exactness rather than a tolerance down to the 1 µs
        ISO floor: case (c) alone leaves tolerances below 0.8 s alive, and
        case (e) kills those of 1 µs or more. The floor BELOW 1 µs — which
        ISO literals cannot express, since ``datetime.fromisoformat`` truncates
        beyond 6 fractional digits — is pinned by
        ``test_supersede_valid_from_below_microsecond_disagreement_refused``.
      * same instant, DIFFERENT offset encodings (an explicit non-zero offset
        on the kwarg, ``+00:00`` on the stored successor) → ACCEPTED, and the
        value the caller passed is what gets persisted (``str(valid_from)``,
        not the stored form). A raw string comparison would refuse both, so
        cases (b) and (d) pin instant-level — not string-level — agreement.
    """
    # (a) same day, 12 hours EARLIER → refused (would leave a GAP)
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v2",
                      validFrom="2026-06-10T12:00:00+00:00")
    with pytest.raises(ValueError, match="disagrees"):
        sdk.supersede_point(old["id"], new["id"],
                            valid_from="2026-06-10T00:00:00+00:00")
    op = _props(sdk, old["id"])
    assert op.get("status") != "superseded"
    assert "validTo" not in op

    # (b) same instant, -04:00 encoding → accepted, caller's text persisted
    sdk.supersede_point(old["id"], new["id"],
                        valid_from="2026-06-10T08:00:00-04:00")
    assert _props(sdk, old["id"])["validTo"] == "2026-06-10T08:00:00-04:00"

    # (c) 0.8 s LATER, same offset → refused (would OVERLAP by 0.8 s)
    old2 = _make_point(sdk, content="claim v3", validFrom="2026-06-01")
    new2 = _make_point(sdk, content="claim v4",
                       validFrom="2026-06-10T12:00:00.100000+00:00")
    with pytest.raises(ValueError, match="disagrees"):
        sdk.supersede_point(old2["id"], new2["id"],
                            valid_from="2026-06-10T12:00:00.900000+00:00")
    assert "validTo" not in _props(sdk, old2["id"])

    # (d) same instant, fractional seconds AND a non-zero offset → accepted
    sdk.supersede_point(old2["id"], new2["id"],
                        valid_from="2026-06-10T08:00:00.100000-04:00")
    assert (_props(sdk, old2["id"])["validTo"]
            == "2026-06-10T08:00:00.100000-04:00")

    # (e) ONE MICROSECOND later → refused (exactness, not a tolerance)
    old3 = _make_point(sdk, content="claim v5", validFrom="2026-06-01")
    new3 = _make_point(sdk, content="claim v6",
                       validFrom="2026-06-10T12:00:00.000001+00:00")
    with pytest.raises(ValueError, match="disagrees"):
        sdk.supersede_point(old3["id"], new3["id"],
                            valid_from="2026-06-10T12:00:00+00:00")
    assert "validTo" not in _props(sdk, old3["id"])

    # (f) zero difference, DIFFERENT fractional encoding (`.000000` vs none) →
    # accepted; both key to the same float instant
    old4 = _make_point(sdk, content="claim v7", validFrom="2026-06-01")
    new4 = _make_point(sdk, content="claim v8",
                       validFrom="2026-06-10T12:00:00.000000+00:00")
    sdk.supersede_point(old4["id"], new4["id"],
                        valid_from="2026-06-10T12:00:00+00:00")
    assert _props(sdk, old4["id"])["validTo"] == "2026-06-10T12:00:00+00:00"


def test_supersede_valid_from_below_microsecond_disagreement_refused(sdk):
    """Pins the comparison BELOW the microsecond floor that ISO pairs cannot
    reach.

    An ISO-8601 literal pair is limited to microsecond resolution —
    ``datetime.fromisoformat`` truncates beyond 6 fractional digits — so the
    smallest separation an ISO case can construct is one microsecond (float
    delta 9.5367431640625e-07 s). A tolerance-based equality therefore survives
    every ISO case in this file: replacing the guard's
    ``k_kwarg[1] == k_stored[1]`` with ``abs(k_kwarg[1] - k_stored[1]) < 1e-9``
    leaves the whole file green without this case.

    The stored start is consequently a NUMERIC epoch, ``validFrom=5e-10``
    (keyed ``(0, 5e-10)``), compared against an ISO kwarg at the epoch
    (``"1970-01-01T00:00:00+00:00"`` → ``(0, 0.0)``). The real guard refuses it
    (``5e-10 != 0.0``); a sub-nanosecond tolerance accepts it.
    """
    old = _make_point(sdk, content="claim v9", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v10", validFrom=5e-10)
    assert _props(sdk, new["id"])["validFrom"] == 5e-10
    with pytest.raises(ValueError, match="disagrees"):
        sdk.supersede_point(old["id"], new["id"],
                            valid_from="1970-01-01T00:00:00+00:00")
    # fail-closed: nothing written
    assert "validTo" not in _props(sdk, old["id"])


def test_supersede_numeric_epoch_kwarg_refused(sdk):
    """The guard keys the value the write PERSISTS (``str(valid_from)``), not
    the caller's object.

    A numeric-epoch kwarg names the same instant as the stored value, but the
    ``str()`` that lands in ``validTo`` is UNPARSEABLE to ``_created_sort_key``
    (its ISO branch needs a ``-`` or ``T``) — so ``_covers`` cannot order the
    predecessor's window end and it silently becomes unbounded, i.e. the exact
    OVERLAP this guard exists to prevent. Keying the caller's object instead
    would accept it and corrupt the chain.
    """
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v2",
                      validFrom="2026-06-10T00:00:00+00:00")
    # 2026-06-10T00:00:00Z as an epoch — the same instant, unserializable form
    with pytest.raises(ValueError, match="disagrees"):
        sdk.supersede_point(old["id"], new["id"], valid_from=1781049600.0)
    # fail-closed: nothing written
    assert "validTo" not in _props(sdk, old["id"])


def test_supersede_falsey_but_present_stored_valid_from_refused(sdk):
    """The guard's PRESENCE predicate is the read path's, not the resolution
    branch's truthiness.

    ``_covers`` gates its start on presence AND orderability, so a
    falsey-but-present stored ``validFrom`` is a real window start there only
    when it NAMES an instant: ``0`` keys as the parseable epoch-0 instant,
    while ``""`` names no instant at all and is, since the #6140 ruling, an OPEN
    start. The two forms fail DIFFERENTLY, and both are refused:

      * ``0`` — trusting the kwarg wrote a predecessor ``validTo`` INSIDE the
        successor's ``[epoch-0, ∞)`` window ⇒ ``ambiguous`` (the overlap this
        guard exists to prevent).
      * ``""`` — the successor's window is open below, so it covers EVERY
        instant and the dated kwarg contradicts it outright (there is no
        instant the kwarg could name that the stored value agrees with). The
        refusal is the #3980 agreement guard doing its job: two spellings of the
        successor's start that disagree about whether it has one are a
        contradiction, not a window that needs ordering.
    """
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    # epoch-0: present AND parseable to the read path
    new_zero = _make_point(sdk, content="claim v2", validFrom=0)
    assert _props(sdk, new_zero["id"])["validFrom"] == 0
    with pytest.raises(ValueError, match="disagrees"):
        sdk.supersede_point(old["id"], new_zero["id"],
                            valid_from="2026-06-10")
    assert "validTo" not in _props(sdk, old["id"])
    # empty string: present but unparseable ⇒ not orderable by _covers
    new_empty = _make_point(sdk, content="claim v3", validFrom="")
    assert _props(sdk, new_empty["id"])["validFrom"] == ""
    with pytest.raises(ValueError, match="disagrees"):
        sdk.supersede_point(old["id"], new_empty["id"],
                            valid_from="2026-06-14")
    assert "validTo" not in _props(sdk, old["id"])


def test_supersede_unparseable_valid_from_refused(sdk):
    """An unparseable kwarg cannot be shown to name the stored instant, and
    ``_covers`` cannot order it — refused rather than written.

    This covers the MIXED pair (unparseable kwarg vs a parseable stored start).
    The BOTH-unparseable pair is covered by
    ``test_supersede_both_sides_unparseable_refused``."""
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v2",
                      validFrom="2026-06-10T00:00:00+00:00")
    with pytest.raises(ValueError, match="disagrees"):
        sdk.supersede_point(old["id"], new["id"], valid_from="not-a-date")
    assert "validTo" not in _props(sdk, old["id"])


def test_supersede_numeric_stored_valid_from_agrees_with_iso_kwarg(sdk):
    """The guard keys the STORED value AS STORED, matching ``_covers``.

    A numeric epoch is a supported stored form (``_created_sort_key`` documents
    it and seeded corpora carry it), and it keys RAW as ``(0, float)``. Passing
    it through ``str()`` first — a natural-looking edit — would key it as
    unparseable ``(1, text)`` (no ``-``/``T``), so the guard would REFUSE an
    instant ``_covers`` orders fine, and the write path would stop matching the
    read path's contiguity boundary. Every other test pairs a numeric stored
    value only with a STRING kwarg, which refuses for an unrelated reason — so
    this hole is invisible without the agreeing case below.
    """
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v2", validFrom=1781049600.0)
    assert _props(sdk, new["id"])["validFrom"] == 1781049600.0
    # numeric stored + ISO kwarg naming the SAME instant → accepted
    sdk.supersede_point(old["id"], new["id"],
                        valid_from="2026-06-10T00:00:00+00:00")
    assert _props(sdk, old["id"])["validTo"] == "2026-06-10T00:00:00+00:00"

    # numeric stored + NUMERIC kwarg of the same epoch → still refused: the
    # value the write persists is `str(1781049600.0)`, which `_covers` cannot
    # order, so the agreeing key is not enough (see the conjunct test).
    old2 = _make_point(sdk, content="claim v3", validFrom="2026-06-01")
    new2 = _make_point(sdk, content="claim v4", validFrom=1781049600.0)
    with pytest.raises(ValueError, match="disagrees"):
        sdk.supersede_point(old2["id"], new2["id"], valid_from=1781049600.0)
    assert "validTo" not in _props(sdk, old2["id"])


def test_supersede_both_sides_unparseable_refused(sdk):
    """The guard requires BOTH sides to be parseable — not merely equal.

    Byte-identical unparseable values are still refused: a key of ``(1, text)``
    is an *unorderable* start wherever it sits, so writing one would leave the
    predecessor's window without an orderable end. This pins the guard's
    parseability conjunct (``k_kwarg[0] == 0 and k_stored[0] == 0``), which is
    otherwise load-bearing but invisible: the mixed-pair tests still fail under a
    guard that drops it, because a parseable key's payload is a ``float`` and an
    unparseable one's is a ``str``, so ``float == str`` is False anyway.
    """
    for bad in ("", "not-a-date"):
        old = _make_point(sdk, content=f"claim v1 {bad!r}",
                          validFrom="2026-06-01")
        new = _make_point(sdk, content=f"claim v2 {bad!r}", validFrom=bad)
        with pytest.raises(ValueError, match="disagrees"):
            sdk.supersede_point(old["id"], new["id"], valid_from=bad)
        assert "validTo" not in _props(sdk, old["id"])


def _future_instant():
    """A probe instant relative to NOW.

    The #5360 fix bounds a predecessor's window at the successor's
    ``createdAt``, so a probe pinned to a literal calendar date would begin
    falling INSIDE that window as the clock advanced and redden CI on a date
    that has nothing to do with the fix.
    """
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) + timedelta(days=365)).isoformat()


def test_supersede_no_kwarg_unparseable_stored_start_is_normalised(sdk):
    """#5360: the NO-KWARG path must not persist an UNORDERABLE window end.

    The sibling tests above all pass ``valid_from=``, so they exercise the
    KWARG path and its agreement guard. The no-kwarg path resolves the end from
    the successor's STORED ``validFrom``, and it used to accept any TRUTHY value
    — so a present-but-unparseable stored start (``"not-a-date"``) was written
    RAW as ``old.validTo``.

    That end is unorderable (``_created_sort_key`` keys it ``(1, text)``), which
    breaks BOTH guards at once:

      * the #4021 inversion guard skips an unorderable successor, so its
        ``end >= start`` invariant is trivially satisfied while the end names no
        instant; and
      * ``_covers`` cannot exclude it either, because ``(1, x) < (0, epoch)`` is
        False — so the predecessor was treated as covering EVERY later instant,
        the exact overlap class #3980 exists to prevent, on the one path #3980
        does not guard.

    The fix makes the stored-start branch require ORDERABILITY, so a value with
    no instant falls through to ``successor_created_at`` — the same treatment a
    FALSEY stored start already gets (#3985). The first assertion is the
    discriminating one: it FAILS on the unfixed code, where ``end`` is the raw
    ``"not-a-date"``.
    """
    from tortoise.search_engine import _created_sort_key

    old = _make_point(sdk, content="unord v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="unord v2", validFrom="not-a-date")
    assert _props(sdk, new["id"])["validFrom"] == "not-a-date"

    sdk.supersede_point(old["id"], new["id"])  # NO kwarg — the unguarded path

    end = _props(sdk, old["id"])["validTo"]
    assert _created_sort_key(end)[0] == 0, (
        f"an UNORDERABLE end {end!r} was persisted — the window has no "
        "orderable boundary, so no query instant can exclude it (#5360)"
    )
    assert end != "not-a-date"

    # The harm, measured on the read path: the predecessor must NOT be treated
    # as covering an instant after its own end. The probe is RELATIVE — see
    # ``_future_instant``. The SUCCESSOR does cover it, deliberately: its start
    # names no instant, so under the #6140 ruling it is an OPEN start and the
    # window is unbounded below. What must never happen is the PREDECESSOR
    # answering here — that is the unbounded end #5360 closed.
    out = sdk.get_historical_knowledge(new["id"], _future_instant())
    assert out["found"] is True
    assert out["valid_point"]["id"] == new["id"], (
        "the predecessor covered a later instant — its end is still unbounded"
    )


def test_supersede_string_kwarg_with_no_stored_start_is_normalised(sdk):
    """#5360, the STRING-kwarg route.

    The call-site agreement guard is reachable only when the successor HAS a
    stored ``validFrom`` to disagree with, so a successor with NO stored start
    bypassed it entirely and ``str(valid_from)`` was persisted RAW — the same
    unbounded window as the reported no-kwarg route, by a different road.

    The NUMERIC kwarg is deliberately NOT covered here: it stays exempt, because
    the guard tests ``isinstance(valid_from, str)`` so that
    ``test_window_end_numeric_kwarg_resolved_before_measure`` (B3) keeps its
    pinned meaning.
    """
    from tortoise.search_engine import _created_sort_key

    old = _make_point(sdk, content="unord kw v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="unord kw v2")  # NO stored validFrom
    assert _props(sdk, new["id"]).get("validFrom") is None

    sdk.supersede_point(old["id"], new["id"], valid_from="not-a-date")

    end = _props(sdk, old["id"])["validTo"]
    assert _created_sort_key(end)[0] == 0, (
        f"an UNORDERABLE end {end!r} was persisted via the string-kwarg route"
    )
    # The predecessor must not be the point in force at a far-future instant.
    # It appears in the CHAIN legitimately (it is history), so assert on
    # ``valid_point`` rather than on the whole response.
    out = sdk.get_historical_knowledge(new["id"], _future_instant())
    assert out["valid_point"]["id"] == new["id"], (
        f"the predecessor was still in force — its end is unbounded: {out}"
    )


def test_supersede_unparseable_successor_created_at_is_normalised(sdk):
    """#5360, the THIRD route (found in the third review cycle).

    ``createdAt`` is a normal CALLER prop — ``create_point`` documents that
    api.py, ingest.py and the source-inheritance path all pass one — so an
    unparseable one is reachable through the public API. The no-kwarg fallback
    used to persist it RAW as the predecessor's END: the same unbounded window
    as the other two routes.
    """
    from tortoise.search_engine import _created_sort_key

    old = _make_point(sdk, content="unord ca v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="unord ca v2", createdAt="not-a-date")
    assert _props(sdk, new["id"])["createdAt"] == "not-a-date"

    sdk.supersede_point(old["id"], new["id"])  # NO kwarg

    end = _props(sdk, old["id"])["validTo"]
    assert _created_sort_key(end)[0] == 0, (
        f"an UNORDERABLE end {end!r} was persisted via the createdAt fallback"
    )


def test_supersede_no_kwarg_orderable_stored_start_is_still_used(sdk):
    """The #5360 guard must not break the path it guards: an ORDERABLE stored
    start is still taken verbatim (the anti-vacuity companion — a fix that fell
    through unconditionally would pass the test above and fail here)."""
    old = _make_point(sdk, content="ord v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="ord v2", validFrom="2026-06-10")
    sdk.supersede_point(old["id"], new["id"])  # NO kwarg
    assert _props(sdk, old["id"])["validTo"] == "2026-06-10"


def test_invalidate_point_stamps_withdrawal(sdk):
    """invalidate_point stamps validTo == expiredAt == now (withdrawal
    terminates the window at withdrawal time — E7 DELETE-soft posture)."""
    old = _make_point(sdk, content="claim to withdraw")
    repl = _make_point(sdk, content="replacement")
    res = sdk.invalidate_point(old["id"], repl["id"])
    assert res["invalidated"] is True
    op = _props(sdk, old["id"])
    assert op["outdated"] is True
    assert op["validTo"] == op["expiredAt"]
    assert op["validTo"]  # non-empty now


def test_supersede_event_payload_gains_window_fields(sdk):
    """PointSuperseded event payload gains valid_from/valid_to/expired_at
    (additive; event schema is free-form JSON)."""
    from tortoise.event_store import read_after
    old = _make_point(sdk, content="gym at 6pm")
    new = _make_point(sdk, content="gym at 5pm", validFrom="2026-06-14")
    sdk.supersede_point(old["id"], new["id"])
    events = read_after(sdk._get_proj(), 0, types=["PointSuperseded"])
    assert events, "PointSuperseded event missing"
    payload = events[-1].get("payload") or events[-1]
    assert payload.get("valid_from") == "2026-06-14"
    assert payload.get("valid_to") == "2026-06-14"
    assert payload.get("expired_at")


# ── T2: when → validFrom on the create path ────────────────────────

def test_create_point_writes_valid_from_prop(sdk):
    """create_point accepts validFrom as an additive prop (D3)."""
    p = _make_point(sdk, content="gym at 6pm", validFrom="2026-06-10",
                    when="2026-06-10")
    props = _props(sdk, p["id"])
    assert props["validFrom"] == "2026-06-10"
    assert props["when"] == "2026-06-10"


def test_undated_create_writes_no_valid_from(sdk):
    """Undated points: no validFrom (open window), no error."""
    p = _make_point(sdk, content="timeless durable belief")
    props = _props(sdk, p["id"])
    assert "validFrom" not in props


# ── T3: get_historical_knowledge chain-walk ─────────────────────────────────

def test_restore_in_window_hit(sdk):
    """at_date inside the CURRENT point's window → current is the answer."""
    p = _make_point(sdk, content="gym at 5pm", validFrom="2026-06-14")
    out = sdk.get_historical_knowledge(p["id"], "2026-07-01")
    assert out["found"] is True
    assert out["valid_point"]["id"] == p["id"]
    assert out["current"]["id"] == p["id"]


def test_restore_two_session_gym_chain(sdk):
    """Gym 6pm (D1) → superseded by gym 5pm (D2): restore at D1.5 returns
    the 6pm point; current = the 5pm point (E2E-9 core)."""
    old = _make_point(sdk, content="gym at 6pm", validFrom="2026-06-10")
    new = _make_point(sdk, content="gym at 5pm", validFrom="2026-06-14")
    sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-14")

    out = sdk.get_historical_knowledge(new["id"], "2026-06-12")
    assert out["found"] is True
    assert out["valid_point"]["id"] == old["id"]
    assert out["valid_point"]["valid_from"] == "2026-06-10"
    assert out["valid_point"]["valid_to"] == "2026-06-14"
    assert out["current"]["id"] == new["id"]
    assert out["current"]["content"] == "gym at 5pm"
    # chain: newest → oldest with window endpoints
    assert [e["id"] for e in out["chain"]] == [new["id"], old["id"]]
    assert out["chain"][0]["valid_from"] == "2026-06-14"
    assert out["chain"][1]["valid_to"] == "2026-06-14"  # contiguity


def test_restore_legacy_undated_old_point_covers(sdk):
    """Legacy undated superseded point (no validTo) → open interval covers
    everything before the successor's validFrom (no false miss)."""
    old = _make_point(sdk, content="legacy claim A")
    new = _make_point(sdk, content="gym at 5pm", validFrom="2026-06-14")
    # legacy old: no validFrom/validTo at all
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) REMOVE n.validFrom, n.validTo",
        params={"id": old["id"]})
    sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-14")

    out = sdk.get_historical_knowledge(new["id"], "2020-01-01")
    assert out["found"] is True
    assert out["valid_point"]["id"] == old["id"]


def test_restore_before_earliest_window_honest_absence(sdk):
    """Date before the earliest window → found:false + nearest (E2E-9 owned
    negative — never a fabricated answer)."""
    old = _make_point(sdk, content="gym at 6pm", validFrom="2026-06-10")
    new = _make_point(sdk, content="gym at 5pm", validFrom="2026-06-14")
    sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-14")

    out = sdk.get_historical_knowledge(new["id"], "2020-01-01")
    assert out["found"] is False
    assert "valid_point" not in out
    assert out["nearest"]["id"] == old["id"]  # closest window


def test_restore_ambiguous_overlapping_windows(sdk):
    """Two candidates whose windows both cover → explicit ambiguity signal
    (never a silent wrong answer — E2E-9 owned negative)."""
    a = _make_point(sdk, content="claim A", validFrom="2026-06-01",
                    validTo="2026-06-30")
    b = _make_point(sdk, content="claim B", validFrom="2026-06-15")
    # hand-plant overlapping windows: b supersedes a but a's window also
    # still covers the date
    sdk._get_proj().g.query(
        "MATCH (a:Point {id:$aid}), (b:Point {id:$bid}) "
        "CREATE (b)-[:CORRECTS]->(a)",
        params={"aid": a["id"], "bid": b["id"]})
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status='superseded', n.outdated=true, "
        "n.validTo='2026-06-30'",
        params={"id": a["id"]})

    out = sdk.get_historical_knowledge(b["id"], "2026-06-20")
    assert out.get("ambiguous") is True
    assert len(out["candidates"]) == 2
    assert {c["id"] for c in out["candidates"]} == {a["id"], b["id"]}
    assert "valid_point" not in out


def test_restore_read_path_treats_falsey_but_present_valid_from_as_present(sdk):
    """Pins the READ-path premise the write-path guard is built on: ``_covers``
    gates its START bound on PRESENCE (``vf is not None``) **and ORDERABILITY**
    — an unorderable start is not a bound at all (the #6140 ruling).

    Both halves below bypass the guard by hand-planting the stamps, so they
    measure `get_historical_knowledge` rather than `supersede_point`:

      * successor ``validFrom = 0`` — the parseable epoch-0 instant ⇒ its
        window is ``[epoch-0, ∞)``. An instant inside the predecessor's window
        is covered by BOTH ⇒ ``ambiguous``. This half pins that a
        falsey-but-present start is a real WINDOW BOUND (so the guard is
        necessary for this form), and that trusting the kwarg here produces a
        visible overlap. It does NOT discriminate presence from truthiness (a
        truthiness predicate would drop the start, leave the window
        ``(-∞, ∞)``, and still return ``ambiguous``). Post-#6140 NEITHER part
        discriminates truthiness from presence: part (b) pins the RULING
        instead (it fails on the pre-ruling code).
      * successor ``validFrom = ""`` — unparseable to ``_created_sort_key``
        (``(1, "")``). **#6140, owner ruling 2026-10-11 ("A and C"), reading
        A: a start that names no instant means NO START WAS RECORDED, so the
        window is OPEN BELOW and the successor covers ``2026-06-15`` too ⇒
        ``ambiguous``, not a silent single answer.** Before the ruling the
        read path gated on presence ALONE and then ordered the value, so
        ``(1, "")`` sorted after every parseable instant and the successor
        covered NOTHING — the predecessor came back alone, and a claim
        genuinely in force read as absent from a dated query. Part (b) now
        pins the ruling instead: the overlap is reported, never hidden.

    An unparseable QUERY instant is a separate matter and unchanged: it keys
    as ``(1, <text>)``, which the predecessor's parseable end
    (``2026-06-20``) is strictly below, so only the successor covers it.
    """
    # (a) validFrom = 0 → a real window start ⇒ overlap ⇒ ambiguous
    old = _make_point(sdk, content="zero v1", validFrom="2026-06-01")
    new_zero = _make_point(sdk, content="zero v2", validFrom=0)
    sdk._get_proj().g.query(
        "MATCH (a:Point {id:$n}), (b:Point {id:$o}) CREATE (a)-[:CORRECTS]->(b)",
        params={"n": new_zero["id"], "o": old["id"]})
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status='superseded', n.outdated=true, "
        "n.validTo='2026-06-20'",
        params={"id": old["id"]})
    out = sdk.get_historical_knowledge(new_zero["id"], "2026-06-15")
    assert out.get("ambiguous") is True
    assert len(out["candidates"]) == 2

    # (b) validFrom = "" → no start recorded ⇒ OPEN below (#6140 reading A), so
    # the successor covers the parseable instant TOO ⇒ the overlap is reported.
    old2 = _make_point(sdk, content="empty v1", validFrom="2026-06-01")
    new_empty = _make_point(sdk, content="empty v2", validFrom="")
    sdk._get_proj().g.query(
        "MATCH (a:Point {id:$n}), (b:Point {id:$o}) CREATE (a)-[:CORRECTS]->(b)",
        params={"n": new_empty["id"], "o": old2["id"]})
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status='superseded', n.outdated=true, "
        "n.validTo='2026-06-20'",
        params={"id": old2["id"]})
    out2 = sdk.get_historical_knowledge(new_empty["id"], "2026-06-15")
    assert out2.get("ambiguous") is True, (
        "the unorderable start was still treated as a LOWER BOUND — the #6140 "
        f"ruling makes the window OPEN below: {out2}"
    )
    assert out2["found"] is True
    assert {c["id"] for c in out2["candidates"]} == {
        new_empty["id"], old2["id"]}
    # CONTROL, unchanged by the ruling: an UNPARSEABLE query instant keys as
    # `(1, <text>)`, above the predecessor's parseable end (2026-06-20), so only
    # the successor covers it. It passes on the PRE-ruling code too — the
    # discriminating assertion above is `ambiguous is True`.
    out2b = sdk.get_historical_knowledge(new_empty["id"], "zzz")
    assert out2b.get("ambiguous") is not True
    assert out2b["found"] is True
    assert out2b["valid_point"]["id"] == new_empty["id"]


def test_a_start_naming_no_instant_leaves_the_window_open(sdk):
    """#6140 — the OWNER RULING (2026-10-11): *"A and C (c should be a warning)"*.

    A stored ``validFrom`` that names NO INSTANT — ``""`` (this issue) and
    ``"not-a-date"`` (the #5360 sibling shape) alike — means **no start was
    recorded**, so (reading A) the window is OPEN BELOW: the point covers every
    query instant, including instants before it was written. ONTOLOGY §4.7
    already forbids inventing a start from the write clock, and the read path
    must not invent a *lower bound* from an unorderable value either.

    Before the ruling ``_covers`` gated the start on PRESENCE alone
    (``vf is not None``) and ordered it, so an unparseable start keyed
    ``(1, <text>)`` — after every parseable instant — and the point covered
    **nothing**: a dated query answered ``absent`` for a claim that is genuinely
    in force. That is the defect: the same record behaved differently depending
    on which half you asked.

    Discriminating: on the unfixed code this point reports ``status="absent"``.
    The open-END side is deliberately NOT touched here, and it is NOT flagged
    either: a present-but-unparseable ``validTo`` is not a bound at all
    (``_covers`` skips its arm, #9202 — it used to EXCLUDE an unparseable probe
    by comparing the text) and, since #9202's 2026-10-25 owner ruling, it reads
    ``end_state: "unknown"`` and fires the ``end_state_unknown`` /
    ``indeterminate_end_ids`` flags (`END_UNKNOWN` now has two spellings: the
    declared ``validToPrecision="unknown"`` with no ``validTo``, and a stored
    end that names no instant). See
    ::``test_an_end_naming_no_instant_bounds_no_instant_even_for_an_unparseable_probe``
    and ::``test_an_un_datable_end_reads_unknown_and_fires_the_flags``.
    """
    empty = _make_point(sdk, content="start recorded as empty", validFrom="")
    assert _props(sdk, empty["id"])["validFrom"] == ""

    out = sdk.get_historical_knowledge(empty["id"], "2026-06-15")
    assert out["found"] is True, (
        "a start that names no instant hid the point from a dated query — "
        f"the #6140 (A) reading is OPEN, not absent: {out}"
    )
    assert out["status"] == "found"
    assert out["valid_point"]["id"] == empty["id"]

    # The same reading for the truthy-but-unorderable shape, which reaches the
    # graph through the same `create_point` props passthrough.
    other = _make_point(sdk, content="start recorded as TBD", validFrom="TBD")
    out2 = sdk.get_historical_knowledge(other["id"], "2026-06-15")
    assert out2["found"] is True
    assert out2["valid_point"]["id"] == other["id"]

    # The REVIEW-side annotation mirrors the same gate: an absent reply whose
    # window ends BEFORE the probe still gets a `nearest` answer, because the
    # unparseable start is not a candidate arm any more. Before the ruling the
    # start arm was taken, yielded no distance, and the entry was dropped from
    # `nearest` even though its end distance was computable.
    old = _make_point(sdk, content="empty start, dated end")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.validFrom='', n.validTo='2026-01-01'",
        params={"id": old["id"]})
    absent = sdk.get_historical_knowledge(old["id"], "2027-01-01")
    assert absent["found"] is False
    assert absent.get("nearest", {}).get("id") == old["id"], (
        "the end distance is computable, so the nearest-window annotation must "
        f"survive an unparseable start: {absent}"
    )


def _legacy_un_datable_end(sdk: TortoiseSDK, stored_end: str,
                           content: str = "legacy row") -> dict:
    """A row carrying a LITERAL un-datable end, written the PRE-2026-10-26 way.

    Since the shaping ruling (#9202, 2026-10-26) the guarded doors cannot produce
    this dialect: a caller end that declares no date is SHAPED into the canonical
    form (no value + the declared marker) and echoed back. A legacy row therefore
    exists only as a row written BEFORE the ruling, and the only way to make one
    in a test is to write the property directly.

    The READING assertions that use this fixture are unchanged on purpose: they
    pin how such a row reads, and the ruling deliberately left the read path — and
    every row written before it — alone. Both dialects must keep reading
    ``end_state: "unknown"`` identically.
    """
    p = _make_point(sdk, content=content, validFrom="2024-01-01")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) SET n.validTo = $v",
        params={"i": p["id"], "v": stored_end})
    assert _props(sdk, p["id"])["validTo"] == stored_end, "fixture precondition"
    return p


@pytest.mark.parametrize("stored_end", ["TBD", ""],
                         ids=["text-that-is-not-an-instant", "empty-value"])
def test_an_end_naming_no_instant_bounds_no_instant_even_for_an_unparseable_probe(
        sdk, stored_end):
    """#9202 (coverage half) — a stored end that names no instant is not a bound.

    ``_covers`` gates the START arm on ORDERABILITY since #6140 (an unorderable
    start is skipped, so the window is open below), but the END arm ordered the
    key unconditionally. An unorderable END and an UNPARSEABLE PROBE both bucket
    as ``(1, <text>)``, so that comparison fell through to the TEXT:
    ``_created_sort_key("TBD") < _created_sort_key("zzz")`` is True and the
    window was EXCLUDED — while the very same window covered ``"AAA"`` and
    every dated probe. The answer depended on the alphabet of the probe, not on
    the data: the stored end bounds nothing, yet it excluded one instant and
    not another.

    Both shapes of the class are exercised, because they were broken
    DIFFERENTLY: ``"TBD"`` excluded only the probes its text sorts before
    (``"zzz"``, not ``"AAA"``), while the empty value keys as ``(1, "")``
    and therefore sorted before EVERY UNPARSEABLE probe — both junk probes, and
    neither a dated one: bucket ``(1, "")`` sorts AFTER every ``(0, epoch)``
    instant, which is why the ``dated`` assertion below is green on the unfixed
    code too.

    ONTOLOGY §4.7 already states the rule for this value — an *"unorderable
    bound ... names no instant, so a refusal would rest on a lexicographic
    accident rather than a comparison"* — and the same section records the
    reading the END keeps: an unorderable ``validTo`` *"covers every later
    instant"*. This test pins the COVERAGE half only.

    NOT touched here, on purpose: this test pins the COVERAGE half only, so it
    never asserts ``end_state``. The reported state is the flag half and it moved
    to ``unknown`` under #9202's 2026-10-25 owner ruling (option B) — pinned
    separately by ::``test_an_un_datable_end_reads_unknown_and_fires_the_flags``,
    which is deliberately a DIFFERENT test so that a coverage regression and a
    semantics regression cannot mask one another.

    Discriminating: on the unfixed code the ``"zzz"`` probe reports
    ``status="absent"`` for both shapes.
    """
    # The row is created the way a pre-ruling row exists (see the helper): the
    # guarded door now SHAPES this value instead of storing it.
    p = _legacy_un_datable_end(sdk, stored_end, content="end recorded as un-datable")
    assert _props(sdk, p["id"])["validTo"] == stored_end

    # A DATED probe — covered on both the fixed and the unfixed code.
    dated = sdk.get_historical_knowledge(p["id"], "2050-01-01")
    assert dated["status"] == "found"

    # Two probes that name no instant, one sorting AFTER the stored text and one
    # BEFORE it: the same window, the same un-datable end — ONE answer.
    after = sdk.get_historical_knowledge(p["id"], "zzz")
    assert after["status"] == "found", (
        "a stored end that names no instant cannot exclude the probe — the "
        f"unfixed END arm compared the text ({stored_end!r} < 'zzz'): {after}"
    )
    assert after["valid_point"]["id"] == p["id"]

    before = sdk.get_historical_knowledge(p["id"], "AAA")
    assert before["status"] == "found", (
        "coverage must not depend on the probe's spelling — the "
        f"{stored_end!r} end names no instant, so it cannot exclude this "
        f"probe either: {before}"
    )
    assert before["valid_point"]["id"] == p["id"]

    # CONTROL — a DATED end still bounds the window. The fix SKIPS an
    # unorderable bound; it does not stop windows from ending.
    dated_end = _make_point(sdk, content="dated end",
                            validFrom="2024-01-01", validTo="2024-03-01")
    out = sdk.get_historical_knowledge(dated_end["id"], "2050-01-01")
    assert out["status"] == "absent"
    assert out["found"] is False


#: Every spelling #9202's 2026-10-25 owner ruling names as an UN-DATABLE end.
#: The list is the ruling's own enumeration, not a sample of it. Covering only
#: ``"TBD"`` is the vacuous pin this issue already suffered from: the class
#: reaches the graph through six spellings that disagree about truthiness
#: (``""`` vs ``"TBD"``), about digits (``"   "``, ``"1719792000"``) and about
#: shape (``"2026-02-30"`` looks exactly like the ISO date beside it), and each
#: of those differences is a way for a future gate to pass this test by
#: accident.
_UN_DATABLE_END_SPELLINGS = ["TBD", "", "   ", "not-a-date", "2026-02-30",
                             "1719792000"]


@pytest.mark.parametrize(
    "stored_end", _UN_DATABLE_END_SPELLINGS,
    ids=["text-placeholder", "empty-value", "whitespace", "plain-nonsense",
         "impossible-calendar-date", "epoch-as-string"],
)
def test_an_un_datable_end_reads_unknown_and_fires_the_flags(sdk, stored_end):
    """#9202 (flag half) — an end that names no instant is an UN-DATABLE end.

    Owner ruling 2026-10-25, option B: *"a stored end that names no instant
    reports ``end_state: \"unknown\"``"* — the same state a deliberately
    declared ``validToPrecision="unknown"`` already produces. Before the
    ruling every spelling below reported ``end_state: "known"`` and **no reply
    field** distinguished it from a dated window, so ``get_historical_knowledge``
    answered *"this was true in 2050"* on the strength of a value that names
    nothing (the defect in the issue's title).

    What must hold for EVERY spelling:

    * ``end_state == "unknown"`` — on the ONE read-projection home
      (``window_read_fields``) and on the reply, so all three read surfaces
      (``get_historical_knowledge``, ``assembly.window_rows``, search/recall
      wire) move together through the same home (#2350 review P2);
    * the flags callers key on — ``end_state_unknown`` and
      ``indeterminate_end_ids`` — FIRE for this class, which they could not
      while the end read ``known``;
    * a PRE-RULING row's stored value is returned **verbatim**, so
      ``valid_to: "TBD"`` stays distinguishable from ``valid_to: None`` (why
      option C's extra marker was not needed) — the fixture writes the property
      directly because the guarded door now SHAPES this value instead of storing
      it, and the read path is deliberately unchanged for rows written before
      that ruling;
    * coverage is UNCHANGED — the window still covers 2050, because a bound that
      names no instant bounds no instant (#9408). The fix must not buy its
      reading by hiding the point.

    Discriminating: on the unfixed head every row of this test reports
    ``end_state: "known"``, ``end_state_unknown`` absent and
    ``indeterminate_end_ids`` absent — measured, not inferred.
    """
    from tortoise.validity import end_state, window_read_fields

    # (0) the ONE home: the precision and the state sit behind the SAME
    # orderability predicate, so the emitted triple cannot contradict itself.
    assert window_read_fields("2024-01-01", stored_end, None, None) == {
        "valid_from_precision": "day",
        "valid_to_precision": None,
        "end_state": "unknown",
    }
    assert end_state(stored_end, None) == "unknown"

    # A pre-ruling row (see the helper): the door now SHAPES a caller end that
    # declares no date, so this dialect can only be written the way it was
    # written BEFORE the ruling.
    p = _legacy_un_datable_end(sdk, stored_end,
                               content=f"un-datable end {stored_end!r}")
    assert _props(sdk, p["id"])["validTo"] == stored_end, (
        "a row written before the shaping ruling keeps its bound VERBATIM — the "
        "read path must never rewrite or drop the value"
    )

    out = sdk.get_historical_knowledge(p["id"], "2050-01-01")
    assert out["status"] == "found", (
        "coverage is settled and unchanged: an end that names no instant "
        f"bounds no instant (#9408), it does not hide the window: {out}"
    )
    vp = out["valid_point"]
    assert vp["end_state"] == "unknown", (
        "a stored end that names no instant is an un-datable end (#9202 owner "
        f"ruling 2026-10-25, option B): {vp}"
    )
    assert vp["valid_to"] == stored_end, (
        f"the stored end must come back VERBATIM, not normalised: {vp}"
    )
    assert vp["valid_to_precision"] is None

    # (2) the two reply flags — the warning the ``known`` reading never gave.
    assert out["end_state_unknown"] is True, (
        f"the flag must fire for a covering un-datable end: {out}"
    )
    assert out["indeterminate_end_ids"] == [p["id"]], (
        f"the covering window must be named as indeterminate: {out}"
    )

    # (3) the chain entry resolves through the same home, so a caller walking
    # the chain instead of ``valid_point`` sees the same state.
    assert out["chain"][0]["end_state"] == "unknown"


def test_the_two_un_datable_spellings_stay_distinguishable(sdk):
    """#9202 — one state, two spellings, and the reply keeps them apart.

    The ruling's reason for NOT taking option C (a separate named marker): the
    distinction is already in the reply, because the stored value is echoed
    verbatim. A stored ``validTo="TBD"`` and a declared
    ``validToPrecision="unknown"`` with no bound now report the SAME
    ``end_state`` (so both are treated as un-datable by a consumer), and remain
    distinguishable by ``valid_to`` / ``valid_to_precision`` (so a consumer can
    still tell "garbage arrived" from "the author said unknown").

    Discriminating: on the unfixed head ``end_state`` differs between these two
    points (``known`` vs ``unknown``), which is the whole defect.
    """
    # The literal-end spelling is a PRE-RULING row (see the helper); the declared
    # spelling is what the door itself now produces.
    stored = _legacy_un_datable_end(sdk, "TBD", content="stored junk end")
    declared = sdk.create_point("statement", "declared un-datable end",
                                validFrom="2024-01-01",
                                validToPrecision="unknown")

    a = sdk.get_historical_knowledge(stored["id"], "2050-01-01")
    b = sdk.get_historical_knowledge(declared["id"], "2050-01-01")

    assert a["valid_point"]["end_state"] == "unknown"
    assert b["valid_point"]["end_state"] == "unknown"
    assert a["end_state_unknown"] is True
    assert b["end_state_unknown"] is True

    # the distinction the ruling preserves: the value somebody WROTE is echoed;
    # the declared spelling has no value at all
    assert a["valid_point"]["valid_to"] == "TBD"
    assert b["valid_point"]["valid_to"] is None
    assert a["valid_point"]["valid_to_precision"] is None
    assert b["valid_point"]["valid_to_precision"] == "unknown"


def test_the_un_datability_gate_does_not_widen_end_state():
    """#9202 — the gate is ORDERABILITY, so no other end changes its reading.

    A control set, so the fix cannot buy its pin by relabelling ends nobody
    asked about. ``END_KNOWN`` still means "dated": a calendar date, a
    date-time with an offset, and a NUMERIC epoch (``0`` is
    1970-01-01T00:00:00Z — an instant, #3985) all keep it. ``END_OPEN`` stays
    reserved for an ABSENT bound — the distinction the ruling insists on,
    because reading a present-but-un-datable end as ``open`` is the
    fabricated-coverage hazard the vocabulary exists to prevent.
    """
    from tortoise.validity import end_state, window_read_fields

    # NUMERIC epochs name an instant, so they keep END_KNOWN.
    assert end_state("2024-03-01", None) == "known"
    assert end_state("2024-03-01T12:00:00+02:00", None) == "known"
    assert end_state(0, None) == "known"
    assert end_state(1719792000, None) == "known"

    # ABSENT stays OPEN — the value that names no instant is NOT read as
    # "no end was recorded".
    assert end_state(None, None) == "open"
    assert end_state(None, "") == "open"
    assert end_state(None, "unknown") == "unknown"

    # The issue's opening measurement, re-measured: the triple for a window
    # whose two bounds both name no instant. Before the ruling this read
    # ``end_state: 'known'``; the start half was already ``None`` (#9201).
    assert window_read_fields("TBD", "TBD", None, None) == {
        "valid_from_precision": None,
        "valid_to_precision": None,
        "end_state": "unknown",
    }


def test_an_unorderable_valid_from_warns_at_the_write_boundary(sdk, caplog):
    """#6140 — option C, softened by the owner's ruling: **a warning**.

    *"A and C (c should be a warning)"* — an empty/unparseable start must not
    be refused (that would make a legacy point impossible to supersede until
    its window was repaired), and it must not be silent either. The write
    boundary says which reading is in force, so an operator who stored the value
    by accident can find it.

    The warning is emitted from ``sdk._refuse_inverted_point_window``, the
    write boundary ``create_point`` / ``update_point`` route through, so it
    names the value the CALLER just supplied rather than a bound merged in from
    the stored opposite side (an unrelated update of a legacy point must not
    re-warn about a value nobody wrote).

    A legal undated point (no ``validFrom`` at all) must stay SILENT: undated is
    the documented common case, not a mistake.
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
        _make_point(sdk, content="empty start warns", validFrom="")
    warned = [r for r in caplog.records if "no instant" in r.getMessage()]
    assert warned, (
        "an empty validFrom was stored with no warning — option C asks for one "
        f"(records: {[r.name for r in caplog.records]})"
    )

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
        undated = _make_point(sdk, content="no start recorded at all")
    assert _props(sdk, undated["id"]).get("validFrom") is None
    assert not [r for r in caplog.records if "no instant" in r.getMessage()], (
        "a legal UNDATED point must not warn — only a value that names no "
        "instant does"
    )

    # A numeric epoch 0 is a REAL start (the epoch instant), so it is silent too.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
        _make_point(sdk, content="epoch zero start", validFrom=0)
    assert not [r for r in caplog.records if "no instant" in r.getMessage()], (
        "validFrom=0 names the epoch instant — it is a real start, not a warning"
    )

    # ONE warning per write ATTEMPT, even when the attempt re-enters the
    # boundary: a dedup hit creates, then routes through `update_point` with
    # the same props, so the same value reaches the write boundary twice while
    # only one write happens. The update leg is what must stay silent — the
    # create leg still speaks, because at that point the point may yet be
    # minted.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
        first = _make_point(sdk, content="dedup warn once", validFrom="TBD",
                            dedup=True)
    assert len([r for r in caplog.records
                if "no instant" in r.getMessage()]) == 1
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
        second = _make_point(sdk, content="dedup warn once", validFrom="TBD",
                             dedup=True)
    assert second["id"] == first["id"], "the fixture must reach the dedup hit"
    hits = [r for r in caplog.records if "no instant" in r.getMessage()]
    assert len(hits) == 1, (
        "a dedup hit re-entered the write boundary with a value it already "
        f"stored — it must warn once, not twice: got {len(hits)}"
    )

    # Control: a dedup hit that stores an ORDERABLE start says nothing at all.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
        _make_point(sdk, content="dedup warn once", validFrom="2026-06-01",
                    dedup=True)
    assert not [r for r in caplog.records if "no instant" in r.getMessage()], (
        "an ORDERABLE start must never warn"
    )
    # The shape that is NOT silent and must still speak exactly once: a dedup
    # hit whose caller value DIFFERS from the stored start — the update leg is
    # the write that stores it, while the create leg has already spoken for the
    # same write (both would fire on the unguarded path).
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
        fourth = _make_point(sdk, content="dedup warn once", validFrom="",
                             dedup=True)
    assert fourth["id"] == first["id"]
    assert _props(sdk, fourth["id"])["validFrom"] == ""
    hits = [r for r in caplog.records if "no instant" in r.getMessage()]
    assert len(hits) == 1, (
        "a dedup hit that CHANGES the stored start to an unparseable one must "
        f"warn once, not twice: got {len(hits)}"
    )

    # The UPDATE leg on its own, so the suppression above cannot make this
    # silent by accident: a plain update that INTRODUCES an unparseable start
    # is the write, and it must speak. (Without this, deleting the update leg's
    # warning would leave every assertion above green.)
    sdk.update_point(first["id"], validFrom="2026-06-01")  # back to a real date
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
        sdk.update_point(first["id"], validFrom="")
    hits = [r for r in caplog.records if "no instant" in r.getMessage()]
    assert len(hits) == 1, (
        "an update that introduces an unparseable start must warn once — the "
        f"suppression is scoped to the dedup write: got {len(hits)}"
    )
    # … and re-writing the value it ALREADY carries introduces nothing.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
        sdk.update_point(first["id"], validFrom="")
    assert not [r for r in caplog.records if "no instant" in r.getMessage()], (
        "an unrelated re-write of the value the point already stores must not "
        "re-warn"
    )


# ── #9202: the END side of the write door — SHAPE or REFUSE ─────────────────
#
# The START half was ruled by the owner (#6140: "A and C (c should be a
# warning)"): coverage reads an unorderable start as NO START RECORDED, and the
# write door WARNS. The END's *coverage* rule is the same and is already landed
# (#9408, ONTOLOGY §4.7: a bound that names no instant is not a bound), and the
# END's *reported state* was RULED on 2026-10-25: option B, an end that names no
# instant reads `end_state: "unknown"` and the reply's `end_state_unknown` /
# `indeterminate_end_ids` flags fire for it.
#
# The write door moved on 2026-10-26 (owner ruling, #9202): what used to be
# "stored verbatim, merely warned about" is now three outcomes, on the ONE
# caller-bound gate (`sdk._refuse_inverted_point_window` → `validity.read_caller_end`):
#
#   accept — a real orderable instant, and representational variance
#            (`2024-3-1`, `2024/03/01`), normalised SILENTLY;
#   shape  — blank or a human uncertainty marker → the canonical un-datable form
#            (no end value + `validToPrecision='unknown'`), with the caller's
#            token ECHOED in the reply as `valid_to_shaped`;
#   refuse — an attempted instant that fails, or text with no recoverable
#            intent → `ValueError` naming the fault, the repair and the forms the
#            field accepts.
#
# These tests are the RE-STATEMENT of the pre-2026-10-26 write-door pin, never a
# deletion of it: that pin was the only thing that would catch a silent reversion
# to verbatim storage, and the contract it guarded is exactly the contract this
# ruling changed. Its rows are re-cut into the matrices below — shaping rows,
# refusal rows, and the control rows that must stay untouched and SILENT.
#
# The change is READING-NEUTRAL. A shaped end stores no value, which `_covers`
# skipped for the verbatim spelling too; every reading pin in this file stays
# green and unmoved.

#: The marker vocabulary the ruling names, plus the two blank spellings. Case-
#: and whitespace-insensitive (`" tbd "` is a row), and ONLY these: `none` /
#: `null` / `nil` are serialisation mistakes, so they live in the REFUSAL matrix
#: below — absorb uncertainty, refuse mistakes (§7 trap 5).
#:
#: `(sent, why)` — every row must be stored as `sent` → (no end) + the declared
#: `unknown` marker, never as itself and never as `open`.
_END_UNCERTAINTY_MARKERS = [
    ("TBD", "the most common placeholder"),
    ("TBA", "announced-but-not-yet-dated"),
    ("TBC", "to-be-confirmed"),
    ("unknown", "the word the DECLARED marker already uses"),
    (" tbd ", "case- and whitespace-insensitive"),
    ("N/A", "the slash form, upper case"),
    ("n/a", "the slash form, lower case"),
    ("NA", "the bare form"),
    ("-", "a single dash"),
    ("--", "a double dash"),
    ("—", "an em dash"),
    ("?", "a question mark"),
    ("", "the empty value — the codebase's undated convention"),
    ("   ", "whitespace alone declares nothing"),
]


@pytest.mark.parametrize(
    ("sent", "why"),
    _END_UNCERTAINTY_MARKERS,
    ids=[repr(s) for s, _w in _END_UNCERTAINTY_MARKERS],
)
def test_a_caller_end_that_declares_no_date_is_shaped_and_echoed(
        sdk, sent, why):
    """#9202 (2026-10-26 ruling) — a no-date END is SHAPED, and the reply says so.

    Three things at once, because they are one contract:

    1. **The stored form is canonical** — no end value plus
       `validToPrecision='unknown'`, not the caller's text. A stored `"TBD"` is
       a value no reader can order; the canonical pair means the same thing and
       every read path already understands it.
    2. **The reply echoes the token** (`valid_to_shaped`) — the in-band receipt
       that makes shaping defensible at all (§7 trap 1). Without it a real typo
       merges into "the author did not know" with no trace.
    3. **It reads `unknown`, never `open`** — the invariant the 2026-10-25
       ruling pinned. A marker mapped to `open` would claim no end was asserted,
       which is the fabricated-coverage hazard the vocabulary exists to prevent.
    """
    from tortoise.validity import END_WRITE_OUTCOMES

    before = dict(END_WRITE_OUTCOMES)
    p = _make_point(sdk, content=f"end declared as {sent!r}", why=why,
                    validFrom="2024-01-01", validTo=sent)

    stored = _props(sdk, p["id"])
    assert stored.get("validTo") is None, (
        f"validTo={sent!r} ({why}) must be stored as NO end value — the "
        f"canonical un-datable form, not the caller's text: {stored}"
    )
    assert stored.get("validToPrecision") == "unknown", (
        "the canonical un-datable end is no value PLUS the declared marker: "
        f"{stored}"
    )

    receipt = p.get("valid_to_shaped")
    assert receipt == {"sent": sent, "validTo": None,
                       "validToPrecision": "unknown"}, (
        "the write reply must ECHO the token it shaped — shaping without the "
        f"receipt is the trap that makes the decision indefensible: {p.keys()}"
    )

    read = sdk.get_historical_knowledge(p["id"], "2050-01-01")
    assert read["status"] == "found"
    assert read["valid_point"]["end_state"] == "unknown", (
        "a shaped end reads 'unknown' — NEVER 'open', which would claim no end "
        f"was asserted: {read['valid_point']}"
    )
    assert read["end_state_unknown"] is True
    assert read["indeterminate_end_ids"] == [p["id"]]
    assert END_WRITE_OUTCOMES["shaped"] == before["shaped"] + 1, (
        "the ruling requires shaping to be COUNTED — the count is the "
        f"measurement: {before} -> {END_WRITE_OUTCOMES}"
    )


#: The refusal matrix: one row per NAMED fault. `match` is the phrase that makes
#: the fault identifiable in the message (a refusal that does not say what is
#: wrong cannot be repaired); the shared assertions below add the two things every
#: refusal owes — the REPAIR and the accepted forms (§7 trap 2).
_END_REFUSALS = [
    ("2026-02-30", "has no day 30",
     "an impossible calendar day: the intent (a day in Feb 2026) is clear, the "
     "datum is not a day"),
    ("2026-13-01", "month 13 is not a calendar month",
     "month 13 — an attempted instant whose component is out of range"),
    ("0000-01-01", "is not a calendar year",
     "year 0 — the YEAR is the component out of range, so the refusal must name "
     "the year and not blame the day (review P3)"),
    ("1719792000", "Unix epoch supplied as a STRING",
     "an epoch as a string: _created_sort_key reads an epoch from a NUMBER, so "
     "this string names no instant while the intent is unmistakable"),
    ("not-a-date", "no date could be recovered",
     "arbitrary prose with no recoverable intent (owner-confirmed as a refusal)"),
    ("03/04/2024", "no date could be recovered",
     "legible but AMBIGUOUS — day/month order — so choosing silently would be "
     "betrayal dressed as competence (§0)"),
    ("none", "no date could be recovered",
     "a serialisation mistake, not human uncertainty: surfaced with the marker "
     "vocabulary named, never absorbed (trap 5)"),
    ("null", "no date could be recovered",
     "the same serialisation mistake, spelled the other way"),
]


@pytest.mark.parametrize(
    ("sent", "match", "why"),
    _END_REFUSALS,
    ids=[repr(s) for s, _m, _w in _END_REFUSALS],
)
def test_a_caller_end_that_attempts_an_instant_and_fails_is_refused(
        sdk, sent, match, why):
    """#9202 (2026-10-26 ruling) — an illegible attempted instant is REFUSED.

    Refusing is the only route that fulfils the caller's actual intent. Shaping
    it would store "the author did not know", when the author knew exactly — a
    record fulfilling a DIFFERENT intent, which is the failure mode that looks
    like success. And the caller can almost certainly supply the right value
    next turn, which is why the message owes them the repair.
    """
    from tortoise.validity import END_WRITE_OUTCOMES

    before = dict(END_WRITE_OUTCOMES)
    with pytest.raises(ValueError, match=match):
        _make_point(sdk, content=f"end attempted as {sent!r}", why=why,
                    validFrom="2024-01-01", validTo=sent)

    # EVERY refusal names the fault (above), the repair, and the accepted forms.
    with pytest.raises(ValueError) as refused:
        _make_point(sdk, content=f"again {sent!r}", validTo=sent)
    message = str(refused.value)
    assert "Pass an ISO-8601 instant" in message, (
        f"a refusal without the REPAIR manufactures a retry loop: {message}"
    )
    assert "uncertainty marker" in message, (
        "a refusal must list what the field DOES accept, including the marker "
        f"vocabulary: {message}"
    )
    assert END_WRITE_OUTCOMES["refused"] == before["refused"] + 2, (
        f"refusals must be COUNTED: {before} -> {END_WRITE_OUTCOMES}"
    )


def test_the_refusal_repair_is_true_on_the_door_that_raises_it(sdk):
    """#9202 (review P2) — the repair must be followable ON THE DOOR THAT SAID IT.

    The message used to end "... or None/omitted for an OPEN end". That is true
    on `create_point` and FALSE on `update_point`, where a partial write that
    omits `validTo` leaves the STORED end exactly as it was — so a caller who
    followed the repair came back with the same value and the same refusal. A
    refusal that misdescribes its own repair is worse than a vague one.

    Two halves, because they are one claim: the message states the door-specific
    truth, and the STORE behaves the way the message now says it does. The second
    half is what made the old sentence untrue; the first is what reds if the
    sentence is restored.
    """
    target = _make_point(sdk, content="a dated end to be repaired",
                         validFrom="2024-01-01", validTo="2024-06-01")

    with pytest.raises(ValueError) as refused:
        sdk.update_point(target["id"], validTo="2026-02-30")
    message = str(refused.value)

    assert "update_point" in message and "OMITTING validTo" in message, (
        "the repair must say that omitting the field is NOT an open end on "
        f"update_point — that is the door that just refused: {message}"
    )
    assert "omitted for an OPEN end" not in message, (
        "the create-door sentence must not be quoted at the update door — it "
        f"is the instruction that fails again: {message}"
    )
    assert "validTo=None" in message, (
        f"the repair must name the form that DOES work here: {message}"
    )

    # …and the store keeps the end when the field is omitted, clears it on None.
    sdk.update_point(target["id"], content="an unrelated edit")
    assert _props(sdk, target["id"])["validTo"] == "2024-06-01", (
        "omitting validTo on update_point leaves the STORED end — the sentence "
        "the refusal used to carry promised the opposite"
    )
    sdk.update_point(target["id"], validTo=None)
    assert _props(sdk, target["id"]).get("validTo") is None


def test_a_declared_granularity_beside_a_no_date_value_is_refused(sdk):
    """#9202 — a concrete precision beside a marker is a CONTRADICTION, refused.

    `validTo='TBD'` with `validToPrecision='day'` states two different things
    about one bound. Shaping could overwrite the token and say nothing, which is
    the silent substitution the whole door exists to prevent — so the pair is
    refused and the caller picks one.
    """
    with pytest.raises(ValueError, match="declares NO date"):
        _make_point(sdk, content="contradictory pair", validFrom="2024-01-01",
                    validTo="TBD", validToPrecision="day")


def test_a_real_instant_and_a_readable_variant_are_accepted_silently(sdk):
    """#9202 — the CONTROL: what we DO understand is finished and not reported.

    §0's first commitment: never make the caller do work the system could have
    done. An orderable instant is stored exactly as sent (never re-spelled), a
    format variant the read measure could not order (`2024-3-1`, `2024/03/01`)
    is normalised to the canonical form because it names the SAME instant, and
    neither says a word — no receipt, no shape count.

    This is the row that keeps the shaping matrix from passing on a door that
    shapes EVERYTHING: a `"friendly"` implementation that rewrote every value
    would satisfy the matrix above and red only here.
    """
    from tortoise.validity import END_WRITE_OUTCOMES

    before = dict(END_WRITE_OUTCOMES)

    # A real instant is untouched — byte-identical, INCLUDING its spelling. The
    # triples carry the window START and an in-window probe because two rows are
    # the pre-2026-10-26 pin's own boundaries: a ZONE-LESS date-time (read
    # locally, #3982) and numeric epoch 0 — the 1970-01-01T00:00:00Z instant
    # (#3985) — which needs a 1970 start to stay a legal window. Both were rows of
    # that 11-row pin and are restored here rather than dropped by the re-cut.
    for valid_from, sent, probe in (
        ("2024-01-01", "2024-06-01", "2024-05-01"),
        ("2024-01-01", "2024-06-01T12:00:00+02:00", "2024-05-01"),
        ("2024-01-01", "2024-06-01T12:00:00", "2024-05-01"),
        ("2024-01-01", "2024-06-01T12:00:00Z", "2024-05-01"),
        ("2024-01-01", 1719792000, "2024-05-01"),
        ("1970-01-01", 0, "1970-01-01"),
    ):
        p = _make_point(sdk, content=f"real end {sent!r}", validFrom=valid_from,
                        validTo=sent)
        assert "valid_to_shaped" not in p, (
            f"an orderable end {sent!r} must be SILENT: {p.get('valid_to_shaped')}"
        )
        assert _props(sdk, p["id"]).get("validTo") == sent, (
            f"{sent!r} names an instant, so it is stored exactly as sent"
        )
        assert sdk.get_historical_knowledge(
            p["id"], probe)["valid_point"]["end_state"] == "known"

    # …and representational variance is normalised WITHOUT comment: an un-padded
    # date, and the SAME un-padded date beside a time-of-day. The pair is one
    # rule (#9202 review P3) — a padded date was finished silently while the
    # padded date-TIME beside it was refused, two verdicts for one sloppiness.
    for sent, canonical in (("2024-3-1", "2024-03-01"),
                            ("2024/03/01", "2024-03-01"),
                            ("2024/3/1", "2024-03-01"),
                            ("2024-3-1T12:00:00", "2024-03-01T12:00:00"),
                            ("2024/3/1T12:00:00", "2024-03-01T12:00:00")):
        p = _make_point(sdk, content=f"variant end {sent!r}",
                        validFrom="2024-01-01", validTo=sent)
        assert "valid_to_shaped" not in p, (
            f"{sent!r} is the SAME instant spelled differently — normalise it "
            "silently, do not report a change that did not happen"
        )
        assert _props(sdk, p["id"]).get("validTo") == canonical, (
            f"{sent!r} must be stored as the read measure can order it: "
            f"{_props(sdk, p['id']).get('validTo')!r}"
        )

    # An ABSENT end is the OPEN window — not a shaped one.
    p = _make_point(sdk, content="no end at all", validFrom="2024-01-01")
    assert "valid_to_shaped" not in p
    assert sdk.get_historical_knowledge(
        p["id"], "2050-01-01")["valid_point"]["end_state"] == "open"

    assert before == END_WRITE_OUTCOMES, (
        f"nothing was shaped or refused on this walk: {before} -> "
        f"{END_WRITE_OUTCOMES}"
    )


def test_the_end_shaping_is_scoped_like_the_start_warning(sdk, caplog):
    """#9202 — the UPDATE leg, the dedup re-entry, and the legacy row.

    Three scoping rules, all properties of the boundary rather than of the field:

    * an update that INTRODUCES a no-date end also shapes it, and echoes it;
    * a dedup-hit create reaches the boundary TWICE for one write, and must
      produce exactly ONE shaping (the create leg rewrites the props to the
      canonical form, so the re-entrant update leg has nothing left to shape);
    * a row written BEFORE this ruling is never re-validated: re-writing an
      unrelated property on it leaves its literal end alone, and it keeps
      reading `unknown` — the read path does not care which form a row carries.
    """
    import logging

    # The UPDATE leg on its own. (Without this, deleting the update path's
    # shaping would leave the create-path assertions above green.)
    p = _make_point(sdk, content="end becomes undatable",
                    validFrom="2024-01-01", validTo="2024-02-01")
    out = sdk.update_point(p["id"], validTo="TBD")
    assert out["valid_to_shaped"] == {"sent": "TBD", "validTo": None,
                                     "validToPrecision": "unknown"}
    assert _props(sdk, p["id"]).get("validTo") is None
    assert _props(sdk, p["id"]).get("validToPrecision") == "unknown"

    # Clearing the end of an already-canonical row changes nothing to report.
    out = sdk.update_point(p["id"], validTo=None)
    assert "valid_to_shaped" not in out, (
        "there is no token to echo when the caller already declared nothing"
    )

    # ONE shaping per write, on the dedup-hit path.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tortoise.validity"):
        first = _make_point(sdk, content="dedup shaped end", validFrom="2024-01-01",
                            validTo="TBD", dedup=True)
    assert first["valid_to_shaped"]["sent"] == "TBD"
    shaped_logs = [r for r in caplog.records if "declares no date" in r.getMessage()]
    assert len(shaped_logs) == 1, (
        "a dedup hit reaches the write boundary twice for ONE write — the "
        f"create leg rewrites the value, so it must log once: {len(shaped_logs)}"
    )
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tortoise.validity"):
        second = _make_point(sdk, content="dedup shaped end", validFrom="2024-01-01",
                             validTo="TBD", dedup=True)
    assert second["id"] == first["id"], "the fixture must reach the dedup hit"
    assert second["valid_to_shaped"]["sent"] == "TBD", (
        "the second call shapes the value IT was given, and says so"
    )

    # A LEGACY row: a literal un-datable end, written the pre-ruling way. It is
    # never re-validated or migrated — and both dialects read identically.
    legacy = _make_point(sdk, content="legacy verbatim end", validFrom="2024-01-01",
                         validTo="2024-02-01")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) SET n.validTo = 'TBD'", params={"i": legacy["id"]})
    assert _props(sdk, legacy["id"])["validTo"] == "TBD", "fixture precondition"
    assert sdk.get_historical_knowledge(
        legacy["id"], "2050-01-01")["valid_point"]["end_state"] == "unknown", (
        "a row written before the shaping door keeps its literal end and keeps "
        "reading 'unknown' — no backfill, no re-validation"
    )
    sdk.update_point(legacy["id"], content="unrelated edit")
    assert _props(sdk, legacy["id"])["validTo"] == "TBD", (
        "an unrelated update must not migrate a legacy row's end"
    )
    assert sdk.get_historical_knowledge(
        legacy["id"], "2050-01-01")["valid_point"]["end_state"] == "unknown", (
        "both dialects must read identically: the shaped form and the legacy "
        "literal form are the same END_UNKNOWN evidence"
    )


def test_superseding_a_shaped_end_dates_it_without_leaving_the_marker(sdk):
    """#9202 pre-flight — supersession does not CLOBBER a shaped end.

    Supersession's contract is to stamp the predecessor's END with the
    successor's resolved start (contiguous windows: the old fact stopped being
    true when the new one became true), so a shaped end is legitimately DATEd by
    it — that is the supersession doing its job, not a caller `validTo` write.

    What must NOT happen is the inconsistent triple: a real `validTo` left
    beside the stale `validToPrecision='unknown'` marker, which would make a
    DATED end read `unknown` forever. And a caller who wants the end to stay
    un-datable says so with `valid_to_precision='unknown'`.
    """
    predecessor = _make_point(sdk, content="shaped predecessor end",
                              validFrom="2024-01-01", validTo="TBD")
    successor = _make_point(sdk, content="successor", validFrom="2026-01-01")
    sdk.supersede_point(predecessor["id"], successor["id"])

    stored = _props(sdk, predecessor["id"])
    assert stored.get("validTo") is not None, "supersession dates the predecessor"
    assert stored.get("validToPrecision") is None, (
        "the un-datable marker must be CLEARED when a real end is stamped — a "
        f"dated end reading 'unknown' is the contradiction: {stored}"
    )
    assert sdk.get_historical_knowledge(
        predecessor["id"], "2024-06-01")["valid_point"]["end_state"] == "known"

    # …and the caller can keep it un-datable instead.
    un_datable = _make_point(sdk, content="stays un-datable", validFrom="2024-01-01",
                             validTo="unknown")
    successor2 = _make_point(sdk, content="successor 2", validFrom="2026-01-01")
    sdk.supersede_point(un_datable["id"], successor2["id"],
                        valid_to_precision="unknown")
    stored2 = _props(sdk, un_datable["id"])
    assert stored2.get("validTo") is None and stored2.get(
        "validToPrecision") == "unknown", (
        f"valid_to_precision='unknown' keeps the end un-datable: {stored2}"
    )


def test_restore_missing_point(sdk):
    """Missing point → {found: false}, no crash."""
    out = sdk.get_historical_knowledge("pt_does_not_exist", "2026-06-12")
    assert out["found"] is False
    assert out["chain"] == []


def test_restore_requires_at_date(sdk):
    p = _make_point(sdk, content="gym at 5pm")
    with pytest.raises(ValueError):
        sdk.get_historical_knowledge(p["id"], "")


def test_restore_chain_guard_bounded(sdk):
    """Long chain: bounded walk never loops forever (3-link chain)."""
    pts = [_make_point(sdk, content=f"claim v{i}", validFrom=f"2026-06-0{i}")
           for i in (1, 2, 3)]
    # v2 supersedes v1, then v3 supersedes v2 — chain v3→v2→v1
    sdk.supersede_point(pts[0]["id"], pts[1]["id"], valid_from="2026-06-02")
    sdk.supersede_point(pts[1]["id"], pts[2]["id"], valid_from="2026-06-03")
    out = sdk.get_historical_knowledge(pts[2]["id"], "2026-06-01")
    assert out["found"] is True
    assert out["valid_point"]["id"] == pts[0]["id"]
    assert len(out["chain"]) == 3


# ── #4021: inverted predecessor window (backdated successor) ──────────────
#
# The defect: ``supersede_point`` stamped the predecessor's window END from
# the successor's window START without ever reading the predecessor's own
# ``validFrom`` — so a successor dated EARLIER than its predecessor persisted
# ``validTo < validFrom``.  The window then satisfies ``_covers`` for no query
# instant, so the predecessor becomes unreachable from every read surface
# (#4021).  The fix is ``_supersede_window_end``: one home for the successor-
# window RESOLUTION plus a fail-closed refusal of the inverted direction.

from tortoise.sdk import _supersede_window_end  # noqa: E402
from tortoise.search_engine import _created_sort_key  # noqa: E402


def _window_end(old_vf, *, valid_from=None, stored_vf=None,
                successor_created_at=None, now="2030-01-01T00:00:00+00:00"):
    """Call the helper the way ``supersede_point`` does (keyword-only)."""
    return _supersede_window_end(
        old_id="pt_old", new_id="pt_new", old_vfs=[old_vf],
        valid_from=valid_from, stored_vf=stored_vf,
        successor_created_at=successor_created_at, now=now)


def _corrects_out(sdk: TortoiseSDK, from_id: str) -> int:
    """Outgoing CORRECTS edges — ``(successor)-[:CORRECTS]->(predecessor)``."""
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id})-[:CORRECTS]->() RETURN count(*)",
        params={"id": from_id}).result_set
    return rows[0][0]


# -- the helper: resolution + the inversion refusal -----------------------

def test_window_end_refuses_inverted_start():
    """B1/B6 — every resolution branch is measured against the predecessor's
    start, not just the kwarg branch (the pre-fix write read no start at all).

    The four rows are the four sources of the successor window start, each one
    strictly BEFORE the predecessor's ``validFrom``."""
    cases = [
        ("stored validFrom", dict(stored_vf="2026-06-01")),
        ("kwarg", dict(valid_from="2026-06-01")),
        ("successor createdAt", dict(successor_created_at="2026-06-01")),
        ("now fallback", dict(now="2026-06-01")),
    ]
    for _label, kw in cases:
        with pytest.raises(ValueError, match="inverted window"):
            _window_end("2026-06-10", **kw)
    # forward control — the same four sources, one day LATER, are accepted
    for label, kw in [
        ("stored validFrom", dict(stored_vf="2026-06-20")),
        ("kwarg", dict(valid_from="2026-06-20")),
        ("successor createdAt", dict(successor_created_at="2026-06-20")),
        ("now fallback", dict(now="2026-06-20")),
    ]:
        assert _window_end("2026-06-10", **kw) == "2026-06-20", label
    # an OPEN predecessor window (no start) can never be inverted
    assert _window_end(None, stored_vf="2026-06-01") == "2026-06-01"


def test_window_end_equal_start_allowed():
    """Equality is well-formed (a zero-length predecessor window) — the guard
    is strictly-before only, so it must not reject contiguity."""
    assert _window_end("2026-06-10", stored_vf="2026-06-10") == "2026-06-10"
    # format-only difference naming the SAME instant is still equality
    assert _window_end("2026-06-10T00:00:00+00:00",
                       stored_vf="2026-06-10T00:00:00Z") == "2026-06-10T00:00:00Z"


def test_window_end_falsey_but_present_orderable_start_is_used():
    """#3985 — a FALSEY-but-PRESENT, ORDERABLE start is the successor's real
    window start, not an absent one.

    ``stored_vf`` is the successor's own stored ``validFrom``, and the read
    path reads it with ``_covers``'s ``is not None`` presence predicate, so a
    numeric ``0`` is the epoch-0 instant there.  This resolver gated on
    truthiness, so it skipped ``0`` and fell back to ``successor_created_at`` —
    a value INSIDE the read path's ``[epoch 0, ∞)`` successor window.  The
    predecessor's end then landed inside the successor's window and
    ``get_historical_knowledge`` reported a 2-candidate ``ambiguous`` instead of the
    successor.

    Value that makes it fail: ``stored_vf = 0`` (or ``0.0``).  Pre-fix both
    assertions below see the ``successor_created_at`` fallback
    (``'2026-09-28T00:00:00+00:00'``); post-fix they see ``0`` / ``0.0``.
    The fixture reaches the value — ``create_point`` accepts any caller
    ``validFrom`` and stores it verbatim, so ``0`` is a real graph state (the
    probe that pinned this issue created one and read back ``validFrom == 0``;
    the #5358 sibling's ``test_falsey_but_present_valid_from_is_a_real_past_
    start`` writes the same value through the public path).

    An OPEN predecessor window is the shape where the fixed branch is
    observable as a plain value: there is no start to compare against, so no
    inversion is possible and the resolution's answer is the return value.
    """
    created = "2026-09-28T00:00:00+00:00"
    assert _window_end(None, stored_vf=0, successor_created_at=created) == 0
    assert _window_end(None, stored_vf=0.0, successor_created_at=created) == 0.0
    # `False` is an int subclass -> keys `(0, 0.0)`, i.e. orderable, and is
    # PRESENT, so it is a real window start too (the read path agrees).
    assert _window_end(None, stored_vf=False,
                       successor_created_at=created) is False
    # Contiguity: an epoch-0 predecessor is the SAME instant, so the guard's
    # strictly-before test does not fire and write == read exactly.
    assert _window_end(0, stored_vf=0, successor_created_at=created) == 0
    # Control — the fallback still applies when there is no stored start.
    assert _window_end(None, stored_vf=None,
                       successor_created_at=created) == created


def test_prevalidate_mirrors_the_resolver_for_a_falsey_but_orderable_start():
    """#3985 — the hosted pre-write check must share the resolver's PRESENCE
    predicate for ``stored_vf``, not truthiness.

    ``hosted_api._prevalidate_supersede_window`` was written when a falsey
    ``stored_vf`` fell through to the writer's ``now``.  #3985 makes the
    resolver land on the KNOWN instant ``0`` instead, so a truthiness test in
    the mirror defers a resolution that is NOT ``now``: the successor is
    minted, and the refusal arrives only at the last-resort boundary — losing
    the #5363 no-orphan guarantee the pre-check exists to give.

    Fails before the fix: the pre-check returns early, so nothing raises.
    """
    from types import SimpleNamespace

    from tortoise.hosted_api import _prevalidate_supersede_window
    from tortoise.sdk import InvertedSupersedeWindow

    class _G:
        def query(self, cypher, params=None):
            if "n.validFrom, n.createdAt" in cypher:
                # the successor: stored start is 0 (epoch-0), no createdAt
                return SimpleNamespace(result_set=[[0, None]])
            # the predecessor's own stored start
            return SimpleNamespace(result_set=[["2026-06-01"]])

    proj = SimpleNamespace(g=_G())

    class _SDK:
        def _get_proj(self):
            return proj

        def _find_point_by_content(self, content, *, pointKind=None):
            return "succ-id"  # a dedup target exists -> existing_id truthy

    pr = SimpleNamespace(
        existing_id="old-id",
        supersede_id="succ-id",
        point=SimpleNamespace(content="c", pointKind="statement", when=None),
    )
    with pytest.raises(InvertedSupersedeWindow):
        _prevalidate_supersede_window(
            _SDK(), pr, now="2026-09-28T00:00:00+00:00")


def test_supersede_refuses_epoch0_successor_over_a_dated_predecessor(sdk):
    """#3985 end-to-end — the overlap is gone, replaced by the honest refusal.

    The issue's own reproduction: a successor created with ``validFrom=0`` and
    a predecessor dated ``'2026-06-01'``, superseded with NO ``valid_from``
    kwarg (the documented fallback chain).

    Measured on the pre-fix code, exactly as the issue reports: the supersede
    SUCCEEDS with ``old.validTo == <successor.createdAt>``, and
    ``get_historical_knowledge`` then returns ``ambiguous=True`` with two candidates —
    the successor's ``[0 .. None]`` window CONTAINS the predecessor's
    ``['2026-06-01' .. createdAt]``.

    Post-fix the same call raises, because epoch-0 precedes ``'2026-06-01'``: the
    resolution now hands the guard the successor's real start, and the guard's
    existing strictly-before test refuses the inverted window (#4021's
    contract).  The refusal is the fix — a silent two-candidate ambiguity is
    replaced by an error that names the route out.

    Value that makes it fail: the successor's ``validFrom = 0``.  The fixture
    reaches it — ``create_point`` stores it verbatim, asserted below so a
    future change that normalises it away cannot let this test pass vacuously.

    The refusal must leave the predecessor UNTOUCHED: it is raised before any
    mutation, so no event is journaled and no half-write survives.
    """
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v2", validFrom=0)

    assert _props(sdk, new["id"])["validFrom"] == 0, (
        "the fixture must reach the epoch-0 start for this to mean anything"
    )

    with pytest.raises(ValueError, match="inverted window"):
        sdk.supersede_point(old["id"], new["id"])  # NO valid_from kwarg

    # No half-write: the predecessor still carries its original window and is
    # not superseded, so a caller can retry with a repair rather than having
    # to reverse a mutation.
    op = _props(sdk, old["id"])
    assert op["validFrom"] == "2026-06-01"
    assert op.get("validTo") is None, f"predecessor was stamped: {op!r}"
    assert op.get("status") != "superseded"


def test_window_end_unorderable_starts_fall_through_to_createdat():
    """#3985 x #5360 — the #3985 fix must change EXACTLY ONE case.

    The natural "tidier" spelling of the fix is ``stored_vf is not None``,
    and it is wrong in both directions.  This test pins the inputs that
    distinguish the predicates, so a later simplification is caught rather
    than silently re-introducing a worse bug:

    - ``''`` is falsey AND unorderable.  ``is not None`` would take it and
      stamp the predecessor's end ``''``, which ``_covers`` reads as an OPEN
      end (``(1, text) < (0, instant)`` is False) — so the predecessor would
      cover every later instant and the successor would NEVER become the
      answer.  That is worse than today's honest absence.  It must stay on the
      ``successor_created_at`` fallback.
    - ``'TBD'`` is TRUTHY but unorderable.  ``#5360`` (which landed on main
      after ``#3985`` was opened) sends this to the ``successor_created_at``
      fallback too: a present-but-unorderable start names no instant, so
      persisting it would leave the window unbounded.  So ``#3985`` changes
      exactly ONE case — a FALSEY but ORDERABLE start (``0``), which the
      sibling test ``test_window_end_falsey_but_present_orderable_start_is_used``
      covers.

    Value that makes it fail: ``''`` and ``'TBD'`` as the stored successor
    start.  ``create_point`` stores any caller ``validFrom`` verbatim, so the
    public path stores either.  ``test_unparseable_predecessor_start_matches_
    the_sibling_5358`` calls that same ``create_point`` with a ``'TBD'``
    ``validFrom`` — in the PREDECESSOR slot; the storage path is
    position-independent, so the successor slot is reachable the same way.
    """
    created = "2026-09-28T00:00:00+00:00"
    # falsey + unorderable: fallback, NOT the stored ''
    assert _window_end("2026-06-01", stored_vf="",
                       successor_created_at=created) == created
    # truthy + unorderable: #5360 sends it to the fallback too, NOT 'TBD'
    assert _window_end("2026-06-01", stored_vf="TBD",
                       successor_created_at=created) == created


def test_window_end_falsey_or_unparseable_start_refused():
    """B4/B5 — the PRESENCE predicate is ``is not None`` and the measure is
    ``_created_sort_key`` (the read path's), not truthiness.

    The two predicates disagree in BOTH directions, and the fixture reaches
    both:

    - ``0`` is PRESENT but falsey.  It keys as the parseable epoch-0 instant,
      so it is a real window start and IS compared — a truthiness predicate
      would have skipped a genuine inversion.
    - ``'not-a-date'`` is TRUTHY but unorderable, and ``''`` is falsey and
      unorderable.  Both key as ``(1, text)``, which sorts after every
      parseable instant, so comparing them would refuse ANY successor on an
      ordering-fallback artefact.  Both are SKIPPED — the same choice
      ``_assert_window_start_not_inverted``, the #5358 sibling, documents for
      the identical shape.  (``''`` is skipped under EITHER predicate, so it is
      not the case that discriminates them; ``'not-a-date'`` is.)

    Both directions are pinned because they are different behaviours, not two
    spellings of one.
    """
    # ``0`` is present-and-parseable: a real inversion, refused.
    with pytest.raises(ValueError, match="inverted window"):
        _window_end(0, stored_vf="1969-12-31T00:00:00+00:00")
    # control: a POST-epoch end is not an inversion against epoch-0
    assert _window_end(0, stored_vf="2026-06-01") == "2026-06-01"
    # present-but-UNORDERABLE starts are skipped, not refused.
    assert _window_end("", stored_vf="2026-06-01") == "2026-06-01"
    assert _window_end("not-a-date", stored_vf="2026-06-01") == "2026-06-01"


def test_window_end_checks_EVERY_predecessor_start_not_just_the_first():
    """A point id is not UNIQUE, and the stamp block MATCHes and stamps
    EVERY node carrying it — so reading only the first row's start would pass
    the guard and still persist an inverted window on a sibling node.

    Value that makes it fail: starts on BOTH sides of the successor's start.
    The fixture reaches it — the sequence carries ``'2026-06-01'`` (before)
    and ``'2026-06-20'`` (after) against a successor start of
    ``'2026-06-10'``. A first-row-only read sees the first entry and accepts;
    the verdict would also flip with the row order the server happens to
    return, which is what makes it a correctness bug and not a nicety.
    """
    with pytest.raises(ValueError, match="inverted window"):
        _supersede_window_end(
            old_id="pt_old", new_id="pt_new",
            old_vfs=["2026-06-01", "2026-06-20"],
            valid_from=None, stored_vf="2026-06-10",
            successor_created_at=None, now="2030-01-01T00:00:00+00:00")
    # Order must not change the verdict: the same two starts, reversed.
    with pytest.raises(ValueError, match="inverted window"):
        _supersede_window_end(
            old_id="pt_old", new_id="pt_new",
            old_vfs=["2026-06-20", "2026-06-01"],
            valid_from=None, stored_vf="2026-06-10",
            successor_created_at=None, now="2030-01-01T00:00:00+00:00")
    # Control: no sibling start is after the successor, so it is accepted.
    assert _supersede_window_end(
        old_id="pt_old", new_id="pt_new",
        old_vfs=["2026-06-01", None],
        valid_from=None, stored_vf="2026-06-10",
        successor_created_at=None, now="2030-01-01T00:00:00+00:00"
    ) == "2026-06-10"


def test_unparseable_predecessor_start_matches_the_sibling_5358(sdk):
    """An unorderable predecessor start is SKIPPED — exactly as the merged
    #5358 sibling skips it — so the supersede still completes.

    Value that makes it fail: the predecessor start ``'TBD'``, written through
    the public create path.  The fixture reaches it — ``create_point`` accepts
    any caller ``validFrom``, so a legacy or imported non-ISO start is a real
    graph state.  Refusing here would make such a point impossible to
    supersede until its window was repaired: the guard would refuse the very
    write a caller uses to move past the bad point.  The sibling's docstring
    states the same reasoning — an unorderable start names no instant, so the
    inversion comparison cannot decide against it.  (Under the #6140 ruling
    that start is an OPEN one, which is why trusting it as the predecessor's
    END would be wrong: the resolution falls through instead.)
    """
    old = _make_point(sdk, content="legacy claim", validFrom="TBD")
    new = _make_point(sdk, content="corrected claim", validFrom="2026-06-01")

    sdk.supersede_point(old["id"], new["id"])

    op = _props(sdk, old["id"])
    assert op["validFrom"] == "TBD", (
        "the fixture must reach the unorderable start for this to mean anything"
    )
    assert op["validTo"] == "2026-06-01"
    assert op["status"] == "superseded"
    assert _corrects_out(sdk, new["id"]) == 1


def test_window_end_numeric_stored_value_stays_raw():
    """The STORED branch returns the value AS STORED — raw, never ``str()``.

    ``_created_sort_key`` keys a numeric epoch as ``(0, float)`` but
    ``str(1781049600.0)`` as unparseable ``(1, text)``; normalising it would
    make the guard read a different instant than the read path (and would
    re-introduce the unbounded predecessor window #3980 exists to prevent)."""
    out = _window_end("2026-06-01", stored_vf=1781049600.0)
    assert out == 1781049600.0
    assert isinstance(out, float), f"stored value was normalised: {out!r}"


def test_window_end_numeric_kwarg_resolved_before_measure():
    """B3 — a numeric kwarg is measured AFTER the resolution the writer
    persists, i.e. ``str(valid_from)`` (``sdk.py``'s ``succ_vf = str(valid_from)``).

    This is the undated-successor sibling of the pre-existing
    ``test_supersede_numeric_epoch_kwarg_refused``: a numeric kwarg can only
    reach this guard when the successor carries NO stored ``validFrom``,
    because the #3980 agreement guard already refuses it whenever a stored
    start exists (``(1, text)`` never equals ``(0, float)``).

    The self-check below is the discriminator and it is HOST-INDEPENDENT:
    ``_created_sort_key`` anchors a date-only string to UTC midnight (#3982),
    so ``'2026-06-10'`` keys as that instant on every host. The raw epoch is
    therefore asserted to precede it, rather than assumed."""
    raw = 1780000000.0  # a fixed epoch comfortably before old_vf's instant
    assert _created_sort_key(raw) < _created_sort_key("2026-06-10"), (
        "the raw-vs-resolved discriminator needs a raw epoch strictly before "
        "the predecessor's (UTC-anchored) start"
    )
    out = _window_end("2026-06-10", valid_from=raw)
    # (a) the resolved (persisted) value keys unparseable, so it is NOT an
    #     inversion — measuring the caller's raw object would have refused;
    # (b) and the value returned is the string the write persists.
    assert out == str(raw) == "1780000000.0"
    assert isinstance(out, str), f"returned the unresolved object: {out!r}"


# -- the writer: fail-closed refusal, no half-write ------------------------

def test_supersede_retroactive_successor_refused(sdk):
    """B1 — a backdated successor is refused BEFORE any mutation.

    Fail-closed means *nothing* of the supersede landed: no window end, no
    transaction-time expiry, no terminal status, no CORRECTS edge, and no
    ``PointSuperseded`` journal line (a journaled event is what rebuild
    replays, so an early emit would re-materialise the corruption)."""
    from tortoise.event_store import read_after

    old = _make_point(sdk, content="claim v1", validFrom="2026-06-10")
    new = _make_point(sdk, content="claim v2", validFrom="2026-06-01")

    with pytest.raises(ValueError, match="inverted window"):
        sdk.supersede_point(old["id"], new["id"])

    op = _props(sdk, old["id"])
    assert "validTo" not in op
    assert "expiredAt" not in op
    assert op.get("status") != "superseded"
    assert _corrects_out(sdk, new["id"]) == 0
    assert read_after(sdk._get_proj(), 0, types=["PointSuperseded"]) == []


def test_supersede_retroactive_successor_agreeing_kwarg_refused(sdk):
    """B2 — the agreement guard (#3980) is NOT enough.

    When the kwarg AGREES with the successor's stored start, #3980 accepts it
    (both sides parse to the same instant) — so this input reaches the window
    stamp untouched and is the case the new guard exists for.  Distinct from
    #3980: that guard compares the kwarg to the successor, this one compares
    the successor to the PREDECESSOR."""
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-10")
    new = _make_point(sdk, content="claim v2", validFrom="2026-06-01")

    with pytest.raises(ValueError, match="inverted window"):
        sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-01")

    assert "validTo" not in _props(sdk, old["id"])
    assert _corrects_out(sdk, new["id"]) == 0


def test_supersede_refuses_when_a_DUPLICATE_predecessor_node_is_late(sdk):
    """The end-to-end form of the fan-out gap: a duplicated predecessor id
    with starts on BOTH sides of the successor's start.

    Value that makes it fail: the LATE sibling node's window. The fixture
    reaches it — two nodes share one id (``create_point(id=...)`` allows it),
    carrying starts ``'2026-06-01'`` and ``'2026-06-20'`` against a successor
    start of ``'2026-06-10'``. The writer's stamp is a bare
    ``MATCH (n:Point {id:$id}) SET n.validTo = ...``, so it stamps EVERY node
    with that id: reading only the first start accepts and leaves the
    ``'2026-06-20'`` node with ``validFrom > validTo`` — the exact #4021
    corruption, silently, and with the verdict depending on server row order.
    """
    dup = "pt_dup_4021"
    sdk.create_point("statement", "claim v1 early", id=dup,
                     validFrom="2026-06-01")
    sdk.create_point("statement", "claim v1 late", id=dup,
                     validFrom="2026-06-20")
    new = _make_point(sdk, content="claim v2", validFrom="2026-06-10")

    with pytest.raises(ValueError, match="inverted window"):
        sdk.supersede_point(dup, new["id"])

    # Fail-closed: NEITHER node was stamped, and no CORRECTS edge landed.
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.validFrom, n.validTo",
        params={"id": dup}).result_set
    assert len(rows) == 2, rows
    # The PREMISE, asserted rather than assumed: the fixture must actually carry
    # one start BEFORE the successor's and one AFTER it, or "it raised" would be
    # the only evidence and a first-row-only read could pass by row luck (the
    # verdict would flip if the server returned the early sibling first).
    starts = {vf for vf, _ in rows}
    assert starts == {"2026-06-01", "2026-06-20"}, (
        f"the fan-out premise is not reached: {starts}"
    )
    assert any(vf > "2026-06-10" for vf in starts if vf), (
        "no sibling start is AFTER the successor's — nothing to discriminate"
    )
    for _start, vt in rows:
        assert vt is None, f"a node was stamped despite the refusal: {rows}"
    assert _corrects_out(sdk, new["id"]) == 0



def test_supersede_equal_start_allowed(sdk):
    """A zero-length predecessor window (start == end) is well-formed and must
    still supersede — the guard refuses strictly-before only."""
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-10")
    new = _make_point(sdk, content="claim v2", validFrom="2026-06-10")

    sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-10")

    op = _props(sdk, old["id"])
    assert op["validTo"] == op["validFrom"] == "2026-06-10"
    assert op["status"] == "superseded"
    assert _corrects_out(sdk, new["id"]) == 1


def test_supersede_future_dated_predecessor_refused(sdk):
    """B6 — a FUTURE-dated predecessor against an undated successor (whose
    resolved end falls back to its ``createdAt``/now) is refused.

    The undated successor has an open window start, so this is the write that
    would otherwise stamp a predecessor whose window has not even begun."""
    old = _make_point(sdk, content="future claim", validFrom="2099-01-01")
    new = _make_point(sdk, content="undated claim")

    with pytest.raises(ValueError, match="inverted window"):
        sdk.supersede_point(old["id"], new["id"])

    assert "validTo" not in _props(sdk, old["id"])
    assert _corrects_out(sdk, new["id"]) == 0


def test_supersede_numeric_stored_start_no_kwarg_raw_end(sdk):
    """The no-kwarg path stamps the successor's numeric start RAW.

    Numeric epochs are a supported stored form (``_created_sort_key`` documents
    them, seeded corpora carry them), and the predecessor's end must equal it
    exactly — normalising to ``str(...)`` would write an UNPARSEABLE end that
    ``_covers`` cannot order, i.e. an unbounded predecessor window."""
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v2", validFrom=1781049600.0)

    sdk.supersede_point(old["id"], new["id"])

    vt = _props(sdk, old["id"])["validTo"]
    assert vt == 1781049600.0
    assert isinstance(vt, float), f"numeric end was normalised: {vt!r}"


def test_get_historical_knowledge_forward_supersede_window_not_inverted(sdk):
    """Read-path control: a legitimate forward supersede leaves a well-formed
    window that an interior instant resolves to the PREDECESSOR."""
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v2", validFrom="2026-06-10")

    sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-10")

    out = sdk.get_historical_knowledge(old["id"], "2026-06-05")
    assert out["found"] is True
    assert out["valid_point"]["id"] == old["id"]
    assert out["chain"][0]["valid_to"] == "2026-06-10"
    assert _created_sort_key(out["chain"][0]["valid_to"]) >= \
        _created_sort_key(out["chain"][0]["valid_from"])


def test_get_historical_knowledge_after_refusal_predecessor_window_intact(sdk):
    """The read-path assertion that REDs without the guard.

    Pre-fix the retroactive ``supersede_point`` is ACCEPTED, inverts the
    predecessor's window, and every read instant then reports honest absence
    (``_covers`` rejects an inverted interval) — the predecessor becomes
    unreachable.  Post-fix the write is refused and the still-open predecessor
    window keeps resolving.
    """
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-10")
    new = _make_point(sdk, content="claim v2", validFrom="2026-06-01")

    with pytest.raises(ValueError, match="inverted window"):
        sdk.supersede_point(old["id"], new["id"])

    out = sdk.get_historical_knowledge(old["id"], "2026-06-15")
    assert out["found"] is True, "predecessor became unreachable"
    assert out["valid_point"]["id"] == old["id"]
    entry = out["chain"][0]
    assert entry["valid_to"] is None or (
        _created_sort_key(entry["valid_to"]) >=
        _created_sort_key(entry["valid_from"])
    ), f"inverted predecessor window: {entry['valid_from']!r} -> {entry['valid_to']!r}"


def test_supersede_refusal_message_survives_scrub(sdk):
    r"""The refusal must reach the MCP surface intact.

    ``_safe`` scrubs every error through ``_scrub_error``, whose
    ``(host=|at |to )[\w.-]+`` rule rewrites any word ending in ``at``/``to``
    followed by a space.  A message that trips it loses the actionable hint
    behind ``***``, so the template must avoid those forms entirely."""
    from tortoise.mcp_server import _scrub_error

    old = _make_point(sdk, content="claim v1", validFrom="2026-06-10")
    new = _make_point(sdk, content="claim v2", validFrom="2026-06-01")

    with pytest.raises(ValueError, match="inverted window") as excinfo:
        sdk.supersede_point(old["id"], new["id"])

    msg = str(excinfo.value)
    assert "inverted window" in msg
    assert _scrub_error(msg) == msg, f"scrub rewrote the message: {_scrub_error(msg)}"
    # the remedy is named, and it exists
    assert "retract_point" in msg
    assert hasattr(sdk, "retract_point")


# ---------------------------------------------------------------------------
# #9202 review P2 regressions — two repairs that were wrong in KIND, not degree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("digits,shape_word", [
    ("1719792000000", "MILLISECOND"),
    ("20240101120000", "YYYYMMDDHHMMSS"),
])
def test_epoch_as_string_refuses_without_prescribing_the_wrong_number(digits, shape_word):
    """#10079 review P2 — the repair must not manufacture a different instant.

    The epoch-as-string arm used to prescribe ``pass the NUMBER <n>`` for ANY
    9+-digit string. For a millisecond epoch that repair is not a fix: the field
    reads a NUMBER as SECONDS, so following it stored year 56468 and read
    ``end_state="known"`` with no error. A repair that lands somewhere other
    than the intent is the retry-loop class the door exists to prevent.
    """
    from tortoise.validity import read_caller_end

    with pytest.raises(ValueError) as excinfo:
        read_caller_end(digits)

    msg = str(excinfo.value)
    # the number is NOT offered back as the repair ...
    assert f"pass the NUMBER {digits}" not in msg, msg
    # ... and the repair names THIS input's own magnitude. ⛔ Asserting only
    # `shape_word in msg` was vacuous (#10079 review round 2, P2): the message
    # named both magnitudes, so the positive half passed for either input and a
    # swapped 13<->14 mapping stayed green.
    assert f"{len(digits)}-digit numeric string" in msg, msg
    assert shape_word in msg, msg
    # the other magnitude's advice must NOT be the one given here
    other = "YYYYMMDDHHMMSS" if len(digits) == 13 else "MILLISECOND"
    assert other not in msg, (
        f"the refusal for a {len(digits)}-digit input also names the {other} "
        f"shape, so it cannot pin which applies: {msg}")


def test_epoch_as_string_still_prescribes_the_number_when_it_is_the_true_repair():
    """The narrowed repair must not cost the caller the working one.

    A SECONDS epoch inside the authored-range span is still handed back as
    ``pass the NUMBER <n>``, because there the number IS the instant they meant.
    """
    from tortoise.validity import read_caller_end

    with pytest.raises(ValueError) as excinfo:
        read_caller_end("1719792000")

    assert "pass the NUMBER 1719792000" in str(excinfo.value)


def test_no_end_receipt_when_the_shaped_end_was_never_STORED(sdk):
    """#10079 review P2 — a receipt may not report an action that did not happen.

    A dedup fold that resolves to an already-terminal claim is REFUSED (#7819):
    nothing is applied. The door still shapes the caller's ``TBD`` in memory, and
    used to return ``valid_to_shaped`` on that reply — telling the caller their
    end was now the canonical un-datable one while the record still ended on its
    original date and read ``known``. The honest signal on that path is
    ``temporal_not_applied``, which rides the same reply; the shaping receipt
    must be ABSENT. Reachability is pinned below, so this cannot pass silently by
    never entering the branch.
    """
    content = "end-receipt on a refused fold"
    first = sdk.create_point("statement", content, status="superseded")
    assert first.get("id"), first

    reply = sdk.create_point("statement", content, status="superseded",
                             dedup=True, validTo="TBD",
                             validFrom="2024-02-02")

    codes = [a.get("code") for a in (reply.get("advisories") or [])]
    # ⛔ reachability first: if this branch was never entered, the assertion
    # below would pass for the wrong reason.
    assert "temporal_not_applied" in codes, (
        "did not reach the refused terminal fold — the receipt assertion would "
        f"be vacuous: {reply.get('advisories')}")
    assert "valid_to_shaped" not in reply, (
        "a shaping receipt was emitted for an end that was never stored: "
        f"{reply.get('valid_to_shaped')!r}")


def test_a_missing_id_takes_no_receipt_and_stays_empty(sdk):
    """#10079 review round 2 P2 — the absent-id contract must survive shaping.

    ``update_point`` returns ``{}`` for an id that does not exist, and that path
    writes NOTHING. Shaping still ran from the caller's props, so attaching
    ``valid_to_shaped`` turned the empty result into a truthy dict:
    a caller's existence check (``if not sdk.update_point(id, ...)``) silently
    passed, and the receipt reported a shaping that never reached the record.

    #10119 — THE REPLY IS NOT THE WHOLE CONTRACT. This test used to pin the
    reply SHAPE only, which is why the door could return ``{}`` while it had
    already journalled a revision, advanced the graph-wide EP epoch and
    stranded a dirty root that no sweep could converge (a typo'd id pinning
    the instance into local dream mode). Each of the three is now measured
    DIRECTLY here, so the reply cannot be honest and the effects dishonest
    again.
    """
    # A real point first, so the epoch baseline is a real number rather than
    # the cold-start ``None`` — a delta assertion against ``None`` would pass
    # for a door that never touched the epoch at all.
    sdk.create_point("statement", "anchor for the absent-id contract")

    # ⛔ WIRE THE JOURNAL, or the row assertions below are VACUOUS. This
    # fixture's SDK has no ``event_log_path``, so ``_emit_event`` returns
    # ``None`` ("no journal configured") and the pre-fix tree would also read
    # ``[] == []`` — a test that passes with the guard deleted proves nothing.
    # The handle is lazy (``_event_log is None``), so setting the path is
    # enough; the shape is the one `tests/test_7951_update_point_outbox.py`
    # uses.
    import json
    _journal_dir = tempfile.mkdtemp(prefix="tortoise_validity_journal_")
    _journal_path = os.path.join(_journal_dir, "events.jsonl")
    sdk._event_log_path = _journal_path
    sdk._event_log = None

    def _ep_version():
        rows = sdk._get_proj().g.query(
            "MATCH (m:EpMeta) RETURN m.ep_version").result_set
        return rows[0][0] if rows else None

    def _journal_rows(pid):
        if not os.path.exists(_journal_path):
            return []
        out = []
        with open(_journal_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("type") == "PointRevised" and row.get("id") == pid:
                    out.append(row)
        return out

    missing = "no-such-point-9202"

    def _assert_no_side_effects(pid):
        """The three effects #10119 measured, each on its own surface."""
        assert _journal_rows(pid) == [], (
            f"an absent id journalled a revision it cannot have: "
            f"{_journal_rows(pid)!r}")
        assert _ep_version() == _ep_before, (
            "an absent id advanced the graph-wide EP epoch: "
            f"{_ep_before!r} -> {_ep_version()!r}")
        assert pid not in sdk._dirty_roots, (
            f"an absent id entered the dirty set: {sorted(sdk._dirty_roots)!r}")

    _ep_before = _ep_version()

    # ── the HEADLINE path: NO temporal prop (the issue's own repro) ──────
    # This call did NOT reach the guard before #10119 (the gate was scoped to
    # `_TEMPORAL_RESTAMP_PROPS`), so it is the only shape whose side effects
    # this fix newly closes. Without it, a regression that re-scopes the gate
    # leaves this test green.
    assert sdk.update_point(missing, confidence=0.5) == {}
    _assert_no_side_effects(missing)

    # the non-shaping case pins the pre-existing contract, so a failure below
    # cannot be blamed on the verb having changed shape generally
    assert sdk.update_point(missing, validTo="2026-06-10") == {}

    reply = sdk.update_point(missing, validTo="TBD")

    assert reply == {}, f"an absent id returned a truthy reply: {reply!r}"
    assert "valid_to_shaped" not in reply

    # ── the SIDE EFFECTS, each measured directly (#10119) ─────────────
    _assert_no_side_effects(missing)
    # and the stranded-root harm itself: a sweep over every REAL id converges.
    real_ids = [r[0] for r in sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN n.id").result_set]
    sdk._sweep_dirty_roots(set(real_ids))
    assert missing not in sdk._dirty_roots, (
        "the absent id survived a converged sweep — it pins the instance out "
        f"of full dream mode: {sorted(sdk._dirty_roots)!r}")
    assert sdk._auto_dream_mode() == "full"

    # ── the positive control: a REAL revision still journals + advances ──
    # Without this, every assertion above would also pass for a door that had
    # simply stopped journalling and stopped advancing the epoch at all.
    real = sdk.create_point("statement", "a real revision target")["id"]
    _ep_real_before = _ep_version()
    sdk.update_point(real, confidence=0.42)
    assert len(_journal_rows(real)) == 1, (
        "a real revision did not journal — the absent-id assertions above "
        "are then not measuring the guard")
    assert _ep_version() != _ep_real_before, (
        "a real revision did not advance the EP epoch")
    assert real in sdk._dirty_roots
    shutil.rmtree(_journal_dir, ignore_errors=True)

