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
      * EARLIER kwarg → GAP: neither window covers [kwarg, stored), so
        ``restore_point_at`` reports honest absence for a covered period;
      * LATER kwarg → OVERLAP: both windows cover [stored, kwarg], so every
        instant inside it reads ``ambiguous``.

    Fail-closed: the refusal is raised in the resolution block (after the
    lifecycle guards, before the PointSuperseded emit and every write), so
    the graph is left untouched and the agreeing path still works."""
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v2", validFrom="2026-06-10")

    # (a) kwarg EARLIER than the stored value → would GAP [06-05, 06-10)
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
    epoch-0 instant and ``""`` keys as an unparseable start that covers
    nothing. The two forms fail DIFFERENTLY, and both are refused:

      * ``0`` — trusting the kwarg wrote a predecessor ``validTo`` INSIDE the
        successor's ``[epoch-0, ∞)`` window ⇒ ``ambiguous`` (the overlap this
        guard exists to prevent).
      * ``""`` — the read path treats the start as present but unorderable, so
        the successor covers NOTHING at any instant; trusting the kwarg leaves
        the successor permanently unreachable rather than visibly overlapping.
        Refused fail-closed because the write path and ``_covers`` would
        silently diverge on it — not because of an overlap.
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
    ``_covers`` cannot order it — refused rather than written."""
    old = _make_point(sdk, content="claim v1", validFrom="2026-06-01")
    new = _make_point(sdk, content="claim v2",
                      validFrom="2026-06-10T00:00:00+00:00")
    with pytest.raises(ValueError, match="disagrees"):
        sdk.supersede_point(old["id"], new["id"], valid_from="not-a-date")
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
        is covered by BOTH ⇒ ``ambiguous``. This is the overlap the guard
        prevents, and it is why a falsey-but-present stored value cannot be
        skipped as "undated".
      * successor ``validFrom = ""`` — unparseable to ``_created_sort_key``
        (``(1, "")``) and never ordered below a parseable key ⇒ the successor
        covers NOTHING. That distinguishes presence from truthiness: under a
        truthiness predicate ``""`` would be skipped and the successor WOULD
        cover, flipping the verdict to ``ambiguous``.
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

    # (b) validFrom = "" → present but unorderable ⇒ covers NOTHING
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
