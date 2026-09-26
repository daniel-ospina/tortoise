"""E6 (#1538) — bi-temporal validity windows.

T1: supersede_point/invalidate_point stamp validTo/expiredAt (contiguity,
    kwarg/read/fallback matrix); T2: when→validFrom on the create path;
    T3: restore_point_at chain-walk (in-window, open interval, ambiguity,
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
        is ``(kwarg, stored)`` and ``restore_point_at`` reports honest absence
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
    at_gap = sdk.restore_point_at(new["id"], "2026-06-07")
    assert at_gap["found"] is True
    assert at_gap["valid_point"]["id"] == old["id"]
    at_overlap = sdk.restore_point_at(new["id"], "2026-06-15")
    assert at_overlap["found"] is True
    assert at_overlap.get("ambiguous") is not True
    assert at_overlap["valid_point"]["id"] == new["id"]


def test_supersede_valid_from_format_difference_is_agreement(sdk):
    """Instant-level, not string-level, comparison via ``_created_sort_key``
    — the same mixed-format primitive ``restore_point_at``'s ``_covers``
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

    The two dates are a full day apart, deliberately: a date-only value parses
    as LOCAL midnight, so a same-day pair would compare equal on a UTC host and
    unequal elsewhere — a host-timezone-dependent assertion is not a test.
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
    passed to the guard; date-only parses as LOCAL midnight, the #3982
    behaviour, so it is deliberately kept out of the comparison.) Four
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

    ``_covers`` gates on ``vf is not None``, so a falsey-but-present stored
    ``validFrom`` is a real window start there: ``0`` keys as the parseable
    epoch-0 instant and ``""`` keys as an unparseable start that covers no
    PARSEABLE instant (an unparseable query instant, by contrast, is covered —
    see the bullet below). The two forms fail DIFFERENTLY, and both are refused:

      * ``0`` — trusting the kwarg wrote a predecessor ``validTo`` INSIDE the
        successor's ``[epoch-0, ∞)`` window ⇒ ``ambiguous`` (the overlap this
        guard exists to prevent).
      * ``""`` — the read path treats the start as present but unorderable, so
        the successor covers no PARSEABLE query instant (every such instant
        lands in the predecessor's window end instead), and trusting the kwarg
        leaves the successor unreachable for those queries rather than
        visibly overlapping. An unparseable query instant, by contrast, keys
        as ``(1, <text>)`` and IS covered by it. Refused fail-closed because
        the write path and ``_covers`` would silently diverge on it — not
        because of an overlap.
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


# ── T3: restore_point_at chain-walk ────────────────────────────────

def test_restore_in_window_hit(sdk):
    """at_date inside the CURRENT point's window → current is the answer."""
    p = _make_point(sdk, content="gym at 5pm", validFrom="2026-06-14")
    out = sdk.restore_point_at(p["id"], "2026-07-01")
    assert out["found"] is True
    assert out["valid_point"]["id"] == p["id"]
    assert out["current"]["id"] == p["id"]


def test_restore_two_session_gym_chain(sdk):
    """Gym 6pm (D1) → superseded by gym 5pm (D2): restore at D1.5 returns
    the 6pm point; current = the 5pm point (E2E-9 core)."""
    old = _make_point(sdk, content="gym at 6pm", validFrom="2026-06-10")
    new = _make_point(sdk, content="gym at 5pm", validFrom="2026-06-14")
    sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-14")

    out = sdk.restore_point_at(new["id"], "2026-06-12")
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

    out = sdk.restore_point_at(new["id"], "2020-01-01")
    assert out["found"] is True
    assert out["valid_point"]["id"] == old["id"]


def test_restore_before_earliest_window_honest_absence(sdk):
    """Date before the earliest window → found:false + nearest (E2E-9 owned
    negative — never a fabricated answer)."""
    old = _make_point(sdk, content="gym at 6pm", validFrom="2026-06-10")
    new = _make_point(sdk, content="gym at 5pm", validFrom="2026-06-14")
    sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-14")

    out = sdk.restore_point_at(new["id"], "2020-01-01")
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

    out = sdk.restore_point_at(b["id"], "2026-06-20")
    assert out.get("ambiguous") is True
    assert len(out["candidates"]) == 2
    assert {c["id"] for c in out["candidates"]} == {a["id"], b["id"]}
    assert "valid_point" not in out


def test_restore_read_path_treats_falsey_but_present_valid_from_as_present(sdk):
    """Pins the READ-path premise the write-path guard is built on: ``_covers``
    gates on presence (``vf is not None``), NOT on truthiness.

    Both halves below bypass the guard by hand-planting the stamps, so they
    measure `restore_point_at` rather than `supersede_point`:

      * successor ``validFrom = 0`` — the parseable epoch-0 instant ⇒ its
        window is ``[epoch-0, ∞)``. An instant inside the predecessor's window
        is covered by BOTH ⇒ ``ambiguous``. This half pins that a
        falsey-but-present start is a real WINDOW BOUND (so the guard is
        necessary for this form), and that trusting the kwarg here produces a
        visible overlap — it does NOT discriminate presence from truthiness
        (a truthiness predicate would drop the start, leave the window
        ``(-∞, ∞)``, and still return ``ambiguous``); part (b) does.
      * successor ``validFrom = ""`` — unparseable to ``_created_sort_key``
        (``(1, "")``) and never ordered below a parseable key ⇒ the successor
        covers no PARSEABLE query instant (``2026-06-15`` lands in the
        predecessor instead). Presence still bites: under a truthiness
        predicate ``""`` would be skipped and the successor WOULD cover that
        parseable instant too, flipping the verdict to ``ambiguous``. An
        unparseable query instant is covered either way — it keys as
        ``(1, <text>)``, which ``(1, "")`` is never greater than.
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
    out = sdk.restore_point_at(new_zero["id"], "2026-06-15")
    assert out.get("ambiguous") is True
    assert len(out["candidates"]) == 2

    # (b) validFrom = "" → present but unorderable ⇒ covers no PARSEABLE instant
    old2 = _make_point(sdk, content="empty v1", validFrom="2026-06-01")
    new_empty = _make_point(sdk, content="empty v2", validFrom="")
    sdk._get_proj().g.query(
        "MATCH (a:Point {id:$n}), (b:Point {id:$o}) CREATE (a)-[:CORRECTS]->(b)",
        params={"n": new_empty["id"], "o": old2["id"]})
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status='superseded', n.outdated=true, "
        "n.validTo='2026-06-20'",
        params={"id": old2["id"]})
    out2 = sdk.restore_point_at(new_empty["id"], "2026-06-15")
    assert out2.get("ambiguous") is not True
    assert out2["found"] is True
    assert out2["valid_point"]["id"] == old2["id"]
    # … but only for PARSEABLE instants: an unparseable query keys as
    # `(1, <text>)`, which the successor's `(1, "")` is NOT greater than, so
    # the `""` successor itself DOES cover it.
    out2b = sdk.restore_point_at(new_empty["id"], "zzz")
    assert out2b.get("ambiguous") is not True
    assert out2b["found"] is True
    assert out2b["valid_point"]["id"] == new_empty["id"]


def test_restore_missing_point(sdk):
    """Missing point → {found: false}, no crash."""
    out = sdk.restore_point_at("pt_does_not_exist", "2026-06-12")
    assert out["found"] is False
    assert out["chain"] == []


def test_restore_requires_at_date(sdk):
    p = _make_point(sdk, content="gym at 5pm")
    with pytest.raises(ValueError):
        sdk.restore_point_at(p["id"], "")


def test_restore_chain_guard_bounded(sdk):
    """Long chain: bounded walk never loops forever (3-link chain)."""
    pts = [_make_point(sdk, content=f"claim v{i}", validFrom=f"2026-06-0{i}")
           for i in (1, 2, 3)]
    # v2 supersedes v1, then v3 supersedes v2 — chain v3→v2→v1
    sdk.supersede_point(pts[0]["id"], pts[1]["id"], valid_from="2026-06-02")
    sdk.supersede_point(pts[1]["id"], pts[2]["id"], valid_from="2026-06-03")
    out = sdk.restore_point_at(pts[2]["id"], "2026-06-01")
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
        old_id="pt_old", new_id="pt_new", old_vf=old_vf,
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


def test_window_end_falsey_or_unparseable_start_refused():
    """B4/B5 — the PRESENCE predicate is ``is not None`` and the measure is
    ``_created_sort_key`` (the read path's), not truthiness.

    ``''`` and ``'not-a-date'`` key as unparseable ``(1, text)``, which sorts
    AFTER every parseable instant — so an ordinary end precedes them and the
    (degenerate) window is refused rather than persisted.  ``0`` keys as the
    parseable epoch-0 instant, so it is only an inversion against an end that
    precedes it.  The truthiness-based predicate this replaces would silently
    skip all three."""
    with pytest.raises(ValueError, match="inverted window"):
        _window_end("", stored_vf="2026-06-01")
    with pytest.raises(ValueError, match="inverted window"):
        _window_end("not-a-date", stored_vf="2026-06-01")
    with pytest.raises(ValueError, match="inverted window"):
        _window_end(0, stored_vf="1969-12-31T00:00:00+00:00")
    # control: epoch-0 is a real instant, so a POST-epoch end is not inverted
    assert _window_end(0, stored_vf="2026-06-01") == "2026-06-01"


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
    ``_created_sort_key`` parses a date-only string on a NAIVE datetime, so
    ``'2026-06-10'`` keys as LOCAL midnight (+/- 14 h across real timezones).
    The raw epoch is therefore asserted to precede it, rather than assumed."""
    raw = 1780000000.0  # 2026-05-29T01:46:40Z — comfortably before old_vf
    assert _created_sort_key(raw) < _created_sort_key("2026-06-10"), (
        "the raw-vs-resolved discriminator needs a raw epoch strictly before "
        "the predecessor's (host-local) start"
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


def test_restore_point_at_forward_supersede_window_not_inverted(sdk):
    """Read-path control: a legitimate forward supersede leaves a well-formed
    window that an interior instant resolves to the PREDECESSOR."""
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v2", validFrom="2026-06-10")

    sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-10")

    out = sdk.restore_point_at(old["id"], "2026-06-05")
    assert out["found"] is True
    assert out["valid_point"]["id"] == old["id"]
    assert out["chain"][0]["valid_to"] == "2026-06-10"
    assert _created_sort_key(out["chain"][0]["valid_to"]) >= \
        _created_sort_key(out["chain"][0]["valid_from"])


def test_restore_point_at_after_refusal_predecessor_window_intact(sdk):
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

    out = sdk.restore_point_at(old["id"], "2026-06-15")
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
