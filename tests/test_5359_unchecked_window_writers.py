"""#5359 — an INVERTED validity window is refused at the Point write boundary.

``create_point`` / ``update_point`` accept ``validFrom``/``validTo`` as caller
props, and ``mining._temporal_wire`` stamps ``validFrom`` on a draft decision
point. None of those writers read the OPPOSITE bound, so a caller (or the
miner's session date against a stored end) could persist ``validTo <
validFrom`` directly — bypassing the refusal #4021 added inside
``supersede_point``. ``restore_point_at``'s ``_covers`` then covers NO instant
and the point disappears from every temporal query while every read reports
honest absence.

The fix routes all three writers through ONE shared guard,
``sdk._refuse_inverted_point_window``, which delegates the PREDICATE to the
declared contract ``commit_schema.validate_validity_window`` (#5374, the ONE
HOME shared with ``invalidate_point``'s #5358 guard and the read path) and owns
only the merge a PARTIAL write needs: a caller's bound against the STORED
opposite bound. Tests for that declaration itself live in
``test_validity_window_contract_5374.py``; this file drives the writers.

Class-B test doctrine — every test answers BOTH:
  (1) *What value makes this test fail?*  Named per test. The refusal half reds
      if the writer persists a strictly inverted orderable pair; the over-fix
      half reds if a legal open-ended, well-formed or equal pair is refused.
  (2) *Does the fixture reach it?*  Yes — every pair is driven through the real
      writer (or the real mining post-pass), and the persisted node is read
      back with a raw query.

Runnable with:
  TORTOISE_TEST_CARVE_OUT=1 TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
    uv run pytest tests/test_5359_unchecked_window_writers.py -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.sdk import TortoiseSDK

EARLY = "2026-06-01T00:00:00+00:00"
LATE = "2026-06-10T00:00:00+00:00"
MID = "2026-06-05T00:00:00+00:00"
AFTER = "2026-07-01T00:00:00+00:00"


@pytest.fixture
def sdk():
    """SDK on a fresh temp DB (the same fixture shape as the #5374 suite)."""
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_vw5359_test_"), "test.db"
    )
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _props(sdk: TortoiseSDK, pid: str) -> dict:
    row = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)",
        params={"id": pid}).result_set
    assert row, f"point {pid} missing"
    return dict(row[0][0])


def _node_count(sdk: TortoiseSDK, pid: str) -> int:
    return sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN count(n)",
        params={"id": pid}).result_set[0][0]


def _raw_point(sdk: TortoiseSDK, pid: str, *, vf=None, vt=None,
               kind: str = "statement", status: str | None = None) -> None:
    """Write a Point with a raw query, bypassing the write guard.

    This is how a window that predates the #5359 guard is simulated: the guard
    is a WRITE-side refusal, so existing stored data must still be reachable to
    the read path. It is also the fan-out setup for the duplicate-node test.
    """
    query = ("CREATE (n:Point {id:$id, content:$c, pointKind:$k")
    params = {"id": pid, "c": f"raw {pid}", "k": kind}
    if vf is not None:
        query += ", validFrom:$vf"
        params["vf"] = vf
    if vt is not None:
        query += ", validTo:$vt"
        params["vt"] = vt
    if status is not None:
        query += ", status:$st"
        params["st"] = status
    query += "})"
    sdk._get_proj().g.query(query, params=params)


def _raised(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


# ── create_point: the caller supplies BOTH bounds ───────────────────────


def test_create_point_refuses_an_inverted_caller_window(sdk):
    """A strictly inverted pair is refused and NOTHING is created.

    (1) *What value makes this test fail?*  ``(validFrom=LATE, validTo=EARLY)``:
        without the write guard the pair persists verbatim and the
        ``pytest.raises`` never fires.
    (2) *Does the fixture reach it?*  Yes — the caller props go straight to
        ``create_point``, and the graph is queried to prove no partial write.
    """
    before = sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)").result_set[0][0]
    with pytest.raises(ValueError, match="inverted validity window"):
        sdk.create_point("statement", "inverted", validFrom=LATE, validTo=EARLY)
    after = sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)").result_set[0][0]
    assert after == before, "a refused create left a partial write behind"


def test_create_point_accepts_legal_windows(sdk):
    """The guard refuses only an INVERSION — never a legal window.

    (1) *What value makes this test fail?*  Every value here is legal
        (ordered, equal, lone start, lone end); an over-fix that demanded both
        props or refused an open-ended window reds this test.
    (2) *Does the fixture reach it?*  Yes — each window is created through
        ``create_point`` and read back off the node.
    """
    ordered = sdk.create_point("statement", "ordered",
                               validFrom="2026-01-01", validTo="2026-12-31")
    assert (ordered["validFrom"], ordered["validTo"]) == (
        "2026-01-01", "2026-12-31")

    lone_start = sdk.create_point("statement", "open-ended",
                                  validFrom="2026-06-01")
    assert lone_start["validFrom"] == "2026-06-01"
    assert lone_start.get("validTo") is None

    lone_end = sdk.create_point("statement", "open-start", validTo="2026-06-01")
    assert lone_end.get("validFrom") is None
    assert lone_end["validTo"] == "2026-06-01"

    equal = sdk.create_point("statement", "zero-length",
                             validFrom=MID, validTo=MID)
    assert (equal["validFrom"], equal["validTo"]) == (MID, MID)


# ── update_point: the caller moves ONE edge against a STORED opposite ───


def test_update_point_refuses_inversion_against_stored_start(sdk):
    """``update_point(id, validTo=EARLY)`` under a future stored start.

    The declaration's scope note is explicit that it checks the pair it is
    GIVEN; the writer must merge the stored start in first. This is the exact
    gap the issue names.

    (1) *What value makes this test fail?*  ``stored validFrom=LATE`` +
        ``validTo=EARLY``: a writer that checks ``(None, EARLY)`` (or skips the
        guard) persists the inversion and reds both the raise and the
        untouched-window assertion.
    (2) *Does the fixture reach it?*  Yes — the stored start is written by a
        legal ``create_point``, then the opposite edge is moved by the real
        ``update_point``.
    """
    p = sdk.create_point("statement", "future start", validFrom=LATE)
    pid = p["id"]
    with pytest.raises(ValueError, match="inverted validity window"):
        sdk.update_point(pid, validTo=EARLY)
    stored = _props(sdk, pid)
    assert stored["validFrom"] == LATE
    assert "validTo" not in stored, "a refused update still wrote validTo"


def test_update_point_refuses_inversion_against_stored_end(sdk):
    """``update_point(id, validFrom=LATE)`` over an earlier stored end.

    The mirror of the case above: a guard that only ever read the stored START
    would miss this direction.

    (1) *What value makes this test fail?*  ``stored validTo=EARLY`` +
        ``validFrom=LATE``: skipping the guard (or merging only the start)
        persists the inversion.
    (2) *Does the fixture reach it?*  Yes — a legal lone-``validTo`` create
        followed by the real ``update_point`` moving the start.
    """
    p = sdk.create_point("statement", "early end", validTo=EARLY)
    pid = p["id"]
    with pytest.raises(ValueError, match="inverted validity window"):
        sdk.update_point(pid, validFrom=LATE)
    stored = _props(sdk, pid)
    assert stored["validTo"] == EARLY
    assert "validFrom" not in stored, "a refused update still wrote validFrom"


def test_update_point_accepts_legal_edges_and_clears(sdk):
    """A legal single-edge move, a legal both-edge move and a clear all pass.

    (1) *What value makes this test fail?*  All values are legal: a lone
        ``validTo`` after a lone ``validFrom`` (open start → closed end), the
        reverse, and ``validTo=None`` clearing the end (``SET n += $props``
        removes the property, so the effective window is open-ended). An
        over-fix that treated the CLEAR as "absent, keep stored" would refuse
        the ``validFrom=LATE`` after the clear and red the last assertion.
    (2) *Does the fixture reach it?*  Yes — every step is driven through
        ``update_point`` and read back.
    """
    p = sdk.create_point("statement", "legal moves")
    pid = p["id"]

    sdk.update_point(pid, validFrom=EARLY)
    sdk.update_point(pid, validTo=LATE)
    assert _props(sdk, pid)["validTo"] == LATE

    # Clearing the end (None removes the property) re-opens the window, so a
    # start AFTER the old end is now legal — a guard that merged the cleared
    # bound as "absent, use stored" would wrongly refuse this.
    sdk.update_point(pid, validTo=None)
    assert "validTo" not in _props(sdk, pid)
    sdk.update_point(pid, validFrom=AFTER)
    assert _props(sdk, pid)["validFrom"] == AFTER


def test_update_point_refuses_inversion_on_any_sibling_node(sdk):
    """A duplicate point id is checked on EVERY node carrying it.

    Point ids are not unique (the duplicate fan-out is a tested shape) and the
    write MATCHes every node with the id, so a first-row-only read could pass
    the guard and still stamp an inverted window on a sibling — the verdict
    would then depend on server row order.

    (1) *What value makes this test fail?*  Two nodes share the id: one has no
        stored end, the other stores ``validTo=EARLY``. Moving ``validFrom`` to
        ``LATE`` inverts on the second node; a guard that reads only one row
        (or is absent) leaves the inversion and reds.
    (2) *Does the fixture reach it?*  Yes — the second node is created with a
        raw query for the same id, so both are matched by the real
        ``update_point``.
    """
    p = sdk.create_point("statement", "fan-out", validFrom=EARLY)
    pid = p["id"]
    _raw_point(sdk, pid, vt=EARLY)  # sibling: open start, early end
    assert _node_count(sdk, pid) == 2

    with pytest.raises(ValueError, match="inverted validity window"):
        sdk.update_point(pid, validFrom=LATE)
    stored = [row[0] for row in sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.validFrom",
        params={"id": pid}).result_set]
    assert LATE not in stored, "a refused fan-out update still wrote a sibling"
    assert EARLY in stored, "the refusal must precede every write"


# ── legacy posture: stored data is untouched, only NEW writes are refused ──


def test_legacy_inverted_window_stays_readable_and_repairable(sdk):
    """The guard does NOT reject already-stored data; the read path flags it.

    The write refusal and the legacy posture are different questions. A window
    that was persisted before the guard existed (simulated with a raw write)
    must stay reachable to the read path — the projection folds deliberately
    replay such windows verbatim so a rebuild is not a FAILED RESTORE (#5374),
    and detecting/repairing them is #5361's. This test pins that the change is
    WRITE-side only: the legacy node reads back as ``found=False`` **and**
    ``malformed=True`` (the #5361 read-path flag), and a later write that
    REPAIRS the window is allowed while one that AGGRAVATES it is refused.

    (1) *What value makes this test fail?*  The final ``pytest.raises`` — a
        change that also rejected the repair (or failed to refuse the
        aggravation) reds. Without the #5359 fix the aggravation write is
        allowed and the raise never fires, so the test is non-vacuous.
    (2) *Does the fixture reach it?*  Yes — the legacy node is written raw,
        read through the real ``restore_point_at``, then moved through the real
        ``update_point``.
    """
    pid = "pt_vw5359_legacy"
    _raw_point(sdk, pid, vf=LATE, vt=EARLY)

    read = sdk.restore_point_at(pid, MID)
    assert read["found"] is False
    assert read.get("malformed") is True
    assert read.get("malformed_ids") == [pid]

    # Aggravating the inversion is refused...
    with pytest.raises(ValueError, match="inverted validity window"):
        sdk.update_point(pid, validTo="2026-05-01")
    assert _props(sdk, pid)["validTo"] == EARLY
    # ... repairing it is allowed.
    sdk.update_point(pid, validTo=AFTER)
    assert _props(sdk, pid)["validTo"] == AFTER


# ── mining._temporal_wire: the miner writes only the START ─────────────


def _temporal_wire(sdk, point_ids, session_date):
    from tortoise.mining import ConversationMiner
    api = SimpleNamespace(projection=sdk._get_proj())
    return ConversationMiner(None)._temporal_wire(
        "", "session_5359", api, point_ids,
        session_date=session_date, sdk=sdk)


def test_mining_temporal_wire_refuses_a_start_that_inverts_stored_end(sdk):
    """The session date must not invert a draft decision's stored end.

    ``_temporal_wire`` SETs only ``validFrom``; a draft decision carrying a
    stored ``validTo`` earlier than the session date would invert. The miner
    routes through the same shared guard.

    (1) *What value makes this test fail?*  ``stored validTo=EARLY`` + session
        date ``LATE``: without the guard the stamp is written and the
        ``pytest.raises`` never fires.
    (2) *Does the fixture reach it?*  Yes — a draft decision point is created
        with a lone ``validTo`` (legal at create time), then the real
        ``_temporal_wire`` post-pass runs over its id.
    """
    p = sdk.create_point("decision", "we decided X", status="draft",
                         validTo=EARLY)
    pid = p["id"]
    with pytest.raises(ValueError, match="inverted validity window"):
        _temporal_wire(sdk, [pid], LATE)
    assert "validFrom" not in _props(sdk, pid), (
        "a refused temporal wire still stamped validFrom")


def test_mining_temporal_wire_still_stamps_a_legal_start(sdk):
    """An open draft decision is stamped exactly as before (no over-fix).

    (1) *What value makes this test fail?*  A draft without a stored end has an
        open window, so the stamp is legal; an over-fix that refused any
        start-only write would never stamp and reds.
    (2) *Does the fixture reach it?*  Yes — the real post-pass runs over a
        draft decision point with no ``validTo``.
    """
    p = sdk.create_point("decision", "we decided Y", status="draft")
    pid = p["id"]
    report = _temporal_wire(sdk, [pid], LATE)
    assert report is not None
    assert _props(sdk, pid)["validFrom"] == LATE
