"""#5358 — ``invalidate_point`` must not persist an INVERTED predecessor window.

``invalidate_point`` stamps the predecessor's window END from an independent
fact (``now``) without reading the point's window START::

    MATCH (n:Point {id:$id}) SET n.outdated = true, n.updatedAt = $now,
        n.validTo = $now, n.expiredAt = $now, ...

For a **future-dated** predecessor (``validFrom > now`` — reachable, because
``create_point`` / ``update_point`` accept a caller ``validFrom``) that persists
``validTo < validFrom``; ``restore_point_at``'s ``_covers`` then covers NO
instant, so the point silently disappears from every temporal query while the
system reports honest absence.  Same defect class as #4021 (superseded
predecessor window), different root — hence its own guard.

Owner ruling (#5358): **fail-closed refusal** — ``ValueError`` BEFORE any
mutation (no partial write, no journal event), naming ``retract_point`` as the
window-agnostic route so the caller has a real way forward.  Equality is legal
(a zero-length ``[now, now]`` window is well-formed).  The comparison reuses the
read path's measure (``_created_sort_key`` — the SAME key ``_covers`` orders
with) and its PRESENCE predicate (``is not None``, never truthiness; the
falsey-but-present truthiness bug is #3985).  A refusal fires only on a
*decidable* inversion: an unparseable stored start is not compared.

Every test asserts the read-path OUTCOME via ``restore_point_at``; props are
asserted ADDITIONALLY as corroboration — byte-identical pre/post props for the
refusal, and the stamped window/flag for the success cases — never as the sole
evidence of an outcome.

Runnable with:
  TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
    uv run pytest tests/test_invalidate_inverted_window_5358.py -v
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK on a fresh temp DB.

    The explicit temp path wins at the SDK constructor (#139), but under a
    supported ``TORTOISE_DB_URI`` the projection redirect (#1647) still targets
    that server — so the suite runs on the configured lane either way.
    """
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_inv5358_test_"), "test.db"
    )
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _make_point(sdk: TortoiseSDK, content: str = "content", **kw):
    return sdk.create_point(kw.pop("kind", "statement"), content, **kw)


def _props(sdk: TortoiseSDK, pid: str) -> dict:
    row = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)",
        params={"id": pid}).result_set
    assert row, f"point {pid} missing"
    return dict(row[0][0])


def _corrects_count(sdk: TortoiseSDK, new_id: str, old_id: str) -> int:
    row = sdk._get_proj().g.query(
        "MATCH (a:Point {id:$n})-[r:CORRECTS]->(b:Point {id:$o}) "
        "RETURN count(r)",
        params={"n": new_id, "o": old_id}).result_set
    return row[0][0]


def _invalidated_events(sdk: TortoiseSDK, pid: str) -> list:
    from tortoise.event_store import read_after
    out = []
    for e in read_after(sdk._get_proj(), 0, types=["PointInvalidated"]):
        if (e.get("payload") or e).get("id") == pid:
            out.append(e)
    return out


# ── The refusal (positive) ─────────────────────────────────────────────

def test_future_dated_predecessor_refused_without_mutation(sdk):
    """A predecessor whose ``validFrom`` is AFTER ``now`` is refused BEFORE
    any mutation.

    (1) *What value makes it fail?*  ``validFrom`` strictly greater than the
        ``now`` the stamp block computes.  Pre-fix, ``invalidate_point``
        succeeds and writes ``validTo = now < validFrom``: the persisted window
        is inverted and the point becomes unreachable from every parseable
        query instant.
    (2) *Does the fixture reach it?*  Yes — ``create_point`` persists a caller
        ``validFrom`` verbatim (pinned by
        test_validity_windows.py::test_create_point_writes_valid_from_prop), so
        the stamp block reads no start and writes the inverted end.

    Fail-closed has TWO halves, both asserted: the ``ValueError`` names
    ``retract_point`` (the window-agnostic route), AND nothing mutated — the
    window/flag props, the CORRECTS edge, and the ``PointInvalidated`` journal
    are all untouched ("before any mutation" is silently violable).
    """
    now = _dt.datetime.now(_dt.UTC)
    future = (now + _dt.timedelta(days=30)).replace(microsecond=0)
    old = _make_point(sdk, content="future claim",
                      validFrom=future.isoformat())
    repl = _make_point(sdk, content="replacement")
    before = _props(sdk, old["id"])

    with pytest.raises(ValueError, match="retract_point"):
        sdk.invalidate_point(old["id"], repl["id"])

    # (a) nothing was mutated — byte-identical props (validFrom, no validTo,
    #     no expiredAt, no outdated, unchanged updatedAt).
    after = _props(sdk, old["id"])
    assert after == before
    assert after["validFrom"] == future.isoformat()
    assert not after.get("outdated")
    assert "validTo" not in after
    assert "expiredAt" not in after
    # (b) no CORRECTS edge and no PointInvalidated journal event.
    assert _corrects_count(sdk, repl["id"], old["id"]) == 0
    assert _invalidated_events(sdk, old["id"]) == []
    # (c) …and the point is still READABLE through the read path: an instant
    #     inside its open-ended window resolves to it — the exact property the
    #     inversion would have destroyed.
    out = sdk.restore_point_at(
        old["id"], (future + _dt.timedelta(days=15)).isoformat())
    assert out["found"] is True
    assert out["valid_point"]["id"] == old["id"]


# ── The negative (over-fix guard) ──────────────────────────────────────

def test_normal_dated_predecessor_still_invalidates_and_stays_readable(sdk):
    """A predecessor dated in the PAST still invalidates exactly as before.

    This is the half that catches an OVER-fix: a guard that refuses all
    invalidation (or stamps the wrong window) would pass the refusal test
    alone.  The resulting ``[validFrom, now]`` window must still resolve
    through ``restore_point_at`` for an instant strictly before ``now``.
    """
    from tortoise.search_engine import _created_sort_key

    now = _dt.datetime.now(_dt.UTC)
    past = (now - _dt.timedelta(days=30)).replace(microsecond=0)
    old = _make_point(sdk, content="live claim", validFrom=past.isoformat())
    repl = _make_point(sdk, content="replacement")

    res = sdk.invalidate_point(old["id"], repl["id"])
    assert res["invalidated"] is True

    op = _props(sdk, old["id"])
    assert op["outdated"] is True
    assert op["validTo"] == op["expiredAt"]
    assert op["validTo"]
    # the window is NOT inverted
    assert _created_sort_key(op["validTo"]) >= _created_sort_key(op["validFrom"])
    # …and it is readable strictly BEFORE now: the point did not vanish.
    out = sdk.restore_point_at(
        old["id"], (now - _dt.timedelta(days=1)).replace(
            microsecond=0).isoformat())
    assert out["found"] is True
    assert out["valid_point"]["id"] == old["id"]
    # the normal path still lands its edge + journal event
    assert _corrects_count(sdk, repl["id"], old["id"]) == 1
    assert len(_invalidated_events(sdk, old["id"])) == 1


# ── Boundary: equality is well-formed ──────────────────────────────────

def test_valid_from_equal_to_now_is_not_an_inversion(sdk, monkeypatch):
    """``validFrom == now`` must NOT raise — a zero-length window is legal.

    The clock is pinned so the point's stored ``validFrom`` and the ``now`` the
    stamp block computes are the SAME instant; with a live wall clock the test
    could not distinguish ``>`` from ``>=`` (``now`` always advances past a
    value read a moment earlier, so a ``>=`` guard would pass by accident).
    """
    fixed = _dt.datetime(2031, 3, 4, 5, 6, 7, tzinfo=_dt.UTC)
    stamp = fixed.isoformat()
    old = _make_point(sdk, content="zero-length", validFrom=stamp)
    repl = _make_point(sdk, content="replacement")

    class _FrozenDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    with monkeypatch.context() as m:
        m.setattr(_dt, "datetime", _FrozenDatetime)
        res = sdk.invalidate_point(old["id"], repl["id"])

    assert res["invalidated"] is True
    op = _props(sdk, old["id"])
    assert op["validFrom"] == stamp
    assert op["validTo"] == stamp
    # a zero-length window covers its own instant
    out = sdk.restore_point_at(old["id"], stamp)
    assert out["found"] is True
    assert out["valid_point"]["id"] == old["id"]


# ── Undecidable / falsey-but-present starts ────────────────────────────

def test_unparseable_valid_from_does_not_refuse(sdk):
    """An UNPARSEABLE stored ``validFrom`` is not a DECIDABLE inversion.

    ``_created_sort_key`` buckets unparseable values LAST (``(1, text)`` vs a
    parseable ``(0, epoch)``), so a raw ``>`` against ``now`` would report
    "future" purely as an ordering-fallback artifact — a guess, not a
    comparison.  The guard refuses only when BOTH sides are parseable, so this
    stamps as before.  (That point's window already covers no PARSEABLE instant,
    so the write cannot newly hide it.)
    """
    now_iso = _dt.datetime.now(_dt.UTC).isoformat()
    old = _make_point(sdk, content="undecidable", validFrom="not-a-date")
    repl = _make_point(sdk, content="replacement")

    # the documented outcome IS honest absence, before and after the write —
    # the write cannot newly hide the point from any PARSEABLE query instant
    assert sdk.restore_point_at(old["id"], now_iso)["found"] is False

    res = sdk.invalidate_point(old["id"], repl["id"])
    assert res["invalidated"] is True
    op = _props(sdk, old["id"])
    assert op["outdated"] is True
    assert op["validTo"] == op["expiredAt"]
    assert sdk.restore_point_at(old["id"], now_iso)["found"] is False


def test_falsey_but_present_valid_from_is_a_real_past_start(sdk):
    """``validFrom = 0`` is a real PAST window bound, not treated as future.

    ``_created_sort_key(0)`` parses to the epoch-0 instant, so the guard's
    decidable-inversion branch is not taken and invalidation proceeds
    normally.  NOTE: this does NOT pin ``is not None`` over truthiness — every
    falsey ``validFrom`` maps to a key that cannot satisfy the refusal
    predicate (``None`` skips under both predicates; ``0`` → epoch 0, past;
    ``""`` → unparseable), so the presence predicate is not observable from
    this guard's behaviour; the READ-PATH outcome is what is pinned here.
    """
    now_iso = _dt.datetime.now(_dt.UTC).isoformat()
    old = _make_point(sdk, content="epoch-zero", validFrom=0)
    repl = _make_point(sdk, content="replacement")

    assert sdk.restore_point_at(old["id"], now_iso)["found"] is True

    res = sdk.invalidate_point(old["id"], repl["id"])
    assert res["invalidated"] is True
    op = _props(sdk, old["id"])
    assert op["validFrom"] == 0
    assert op["outdated"] is True
    # the closed window still resolves at an instant before the write's now
    after = sdk.restore_point_at(old["id"], now_iso)
    assert after["found"] is True
    assert after["valid_point"]["id"] == old["id"]


# ── The route the refusal names must actually work ─────────────────────

def test_retract_point_is_the_window_agnostic_route_after_refusal(sdk):
    """After the refusal, the route the error names works and leaves the
    window intact.

    The refusal's whole justification is the escape hatch it names
    (``retract_point``), so the way forward is CHECKED, not just promised: it
    must not touch ``validTo``/``expiredAt``, and the future-dated window must
    still resolve through the read path afterwards.
    """
    now = _dt.datetime.now(_dt.UTC)
    future = (now + _dt.timedelta(days=30)).replace(microsecond=0)
    old = _make_point(sdk, content="future claim", validFrom=future.isoformat())
    repl = _make_point(sdk, content="replacement")

    with pytest.raises(ValueError, match="retract_point"):
        sdk.invalidate_point(old["id"], repl["id"])

    sdk.retract_point(old["id"])
    op = _props(sdk, old["id"])
    assert op["status"] == "retracted"
    assert "validTo" not in op
    assert "expiredAt" not in op
    out = sdk.restore_point_at(
        old["id"], (future + _dt.timedelta(days=15)).isoformat())
    assert out["found"] is True
    assert out["valid_point"]["id"] == old["id"]


# ── Duplicate ids: the writer stamps EVERY matching node ───────────────

def test_duplicate_id_group_refuses_when_ANY_node_is_future_dated(sdk):
    """The writer's stamp block MATCHes **EVERY** node carrying the id, so the
    guard must refuse on ANY inverted stored start — not just the first row.

    Point ids are not unique (the duplicate fan-out is a tested shape:
    ``test_dry_run_preview`` counts duplicate-id pairs). A first-row-only guard
    passes when the server returns the past-dated node first, and the stamp
    block then inverts the future-dated sibling's window — the exact #5358
    corruption, still reachable. The past-dated node is inserted FIRST here so
    the first-row read is the one that would pass.
    """
    now = _dt.datetime.now(_dt.UTC)
    past = (now - _dt.timedelta(days=30)).replace(microsecond=0)
    future = (now + _dt.timedelta(days=30)).replace(microsecond=0)
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (a:Point {id:'dup5358', content:'past', validFrom:$past})",
        params={"past": past.isoformat()},
    )
    proj.g.query(
        "CREATE (b:Point {id:'dup5358', content:'future', validFrom:$future})",
        params={"future": future.isoformat()},
    )
    repl = _make_point(sdk, content="replacement")

    with pytest.raises(ValueError, match="retract_point"):
        sdk.invalidate_point("dup5358", repl["id"])

    rows = proj.g.query(
        "MATCH (n:Point {id:'dup5358'}) RETURN n.validFrom, n.validTo"
    ).result_set
    assert len(rows) == 2
    for _vf, vt in rows:
        assert vt is None, f"a duplicate-id node was stamped validTo={vt!r}"
    # both windows stay open-ended, so the future instant resolves — the
    # inversion would have made it unreachable
    out = sdk.restore_point_at(
        "dup5358", (future + _dt.timedelta(days=15)).isoformat())
    assert out["found"] is True
    # NOTE: which duplicate is row[0] is server-unspecified, so this black-box
    # test only DISCRIMINATES a first-row-only guard when the non-triggering
    # node is returned first. The order-independent contract is pinned
    # deterministically by `test_all_rows_semantics_are_order_independent`.


def test_all_rows_semantics_are_order_independent(sdk, monkeypatch):
    """The guard examines EVERY matching row, whatever the DB row order.

    The black-box duplicate-id test above depends on the server's unspecified
    row order; this feeds the guard the exact row sequences instead, so the
    every-row contract and the `None`-start handling are pinned without a
    row-order assumption.

    The `None` case is load-bearing: a leading UNDATED node must NOT
    short-circuit the scan (`continue`, never `return`) or a future-dated
    sibling is silently missed.
    """
    now = _dt.datetime.now(_dt.UTC)
    past = (now - _dt.timedelta(days=30)).replace(microsecond=0).isoformat()
    future = (now + _dt.timedelta(days=30)).replace(microsecond=0).isoformat()

    class _Rows:
        def __init__(self, rows):
            self.result_set = rows

    class _G:
        def __init__(self, rows):
            self._rows = rows

        def query(self, _cypher, params=None):
            return _Rows(self._rows)

    class _Proj:
        def __init__(self, rows):
            self.g = _G(rows)

    def _guard(rows):
        monkeypatch.setattr(sdk, "_get_proj", lambda: _Proj(rows))
        sdk._assert_window_start_not_inverted("any-id", now.isoformat())

    # no rows / no stored start / only past starts -> proceed
    _guard([])
    _guard([(None,)])
    _guard([(past,)])
    # ANY future start refuses, whatever the order
    for rows in ([(future,)], [(past,), (future,)], [(future,), (past,)]):
        with pytest.raises(ValueError, match="retract_point"):
            _guard(rows)
    # a leading UNDATED node must not short-circuit the scan
    with pytest.raises(ValueError, match="retract_point"):
        _guard([(None,), (future,)])
