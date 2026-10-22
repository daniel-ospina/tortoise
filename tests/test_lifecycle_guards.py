"""#2498 — the shared point-lifecycle guard.

``supersede_point`` / ``retract_point`` / ``invalidate_point`` all terminalize
a claim, but before #2498 each carried its OWN guard and they drifted:

  - ``supersede_point`` / ``retract_point`` hardcoded the 3-status subset
    ``("retracted", "superseded", "archived")`` — a point whose STATUS is
    ``outdated`` / ``deprecated``, or whose legacy ``outdated=true`` FLAG is
    set, passed the guard and could be terminalized a second time;
  - ``invalidate_point`` had NO guard — it flagged OPERATOR nodes
    ``outdated=true`` and re-stamped / re-edged already-terminal points.

The fix routes all three through one predicate, ``live.is_terminal_status``
(``status in live.TERMINAL_EXCLUDED_STATUSES`` OR the ``outdated=true`` flag),
via ``TortoiseSDK._assert_lifecycle_guard``. These tests pin the full
status × transition matrix plus the previously-divergent inputs.

Runnable embedded (the carve-out opt-in):

    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_lifecycle_guards.py -q
"""
from __future__ import annotations

import inspect
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.live import TERMINAL_EXCLUDED_STATUSES, is_terminal_status
from tortoise.sdk import POINT_STATUS_VALUES, TortoiseSDK


@pytest.fixture
def sdk(shared_embedded_db):
    """Per-test isolation via a unique test_* namespace graph on the shared
    embedded server (mirrors test_sdk_legacy_coverage's #176 pattern)."""
    ns = f"test_lifeguard_{uuid.uuid4().hex[:8]}"
    sdk = TortoiseSDK(db_path=shared_embedded_db, namespace=ns)
    yield sdk
    sdk.close()


def _point(sdk, status="live", *, outdated=False, is_operator=False) -> str:
    """Create a Point in an ARBITRARY persisted state.

    ``create_point`` validates ``status`` against ``POINT_STATUS_VALUES``, so
    terminal / legacy-flag / operator states are written directly — the guard
    must handle any state the graph can hold, not just creatable ones.
    """
    pid = sdk.create_point("statement", f"p-{uuid.uuid4().hex[:8]}")["id"]
    sets, params = ["n.status=$st"], {"id": pid, "st": status}
    if outdated:
        sets.append("n.outdated=true")
    if is_operator:
        sets.append("n.is_operator=true")
    sdk._get_proj().g.query(
        f"MATCH (n:Point {{id:$id}}) SET {', '.join(sets)}", params=params)
    return pid


def _op(sdk) -> str:
    src, dst = _point(sdk), _point(sdk)
    return sdk.create_operator("IMPL", src, [dst])["id"]


_TERMINAL_STATUSES = sorted(TERMINAL_EXCLUDED_STATUSES)


# ── the vocabulary itself ───────────────────────────────────────────────

def test_terminal_vocabulary_is_the_shared_set():
    # The guard is only as good as its vocabulary — pin it so a local subset
    # cannot silently reappear (#2498).
    assert frozenset(
        {"retracted", "superseded", "outdated", "archived", "deprecated"}
    ) == TERMINAL_EXCLUDED_STATUSES
    for s in TERMINAL_EXCLUDED_STATUSES:
        assert is_terminal_status(s), s
    # `deprecated` is a legacy write-only status — outside POINT_STATUS_VALUES
    # but still terminal in the shared vocabulary (read surfaces exclude it).
    assert "deprecated" not in POINT_STATUS_VALUES
    assert is_terminal_status("live") is False
    assert is_terminal_status(None) is False          # legacy/absent = live
    assert is_terminal_status("live", True) is True   # legacy outdated flag


# ── the full status × transition matrix ─────────────────────────────────

@pytest.mark.parametrize("status", _TERMINAL_STATUSES)
def test_supersede_rejects_every_terminal_status(sdk, status):
    with pytest.raises(ValueError, match="already terminal"):
        sdk.supersede_point(_point(sdk, status), _point(sdk))


@pytest.mark.parametrize("status", _TERMINAL_STATUSES)
def test_retract_rejects_every_terminal_status(sdk, status):
    with pytest.raises(ValueError, match="already terminal"):
        sdk.retract_point(_point(sdk, status))


@pytest.mark.parametrize("status", _TERMINAL_STATUSES)
def test_invalidate_rejects_every_terminal_status(sdk, status):
    with pytest.raises(ValueError, match="already terminal"):
        sdk.invalidate_point(_point(sdk, status), _point(sdk))


@pytest.mark.parametrize("status", ["live", "draft", None])
def test_non_terminal_sources_are_still_allowed(sdk, status):
    # No new rejections of legitimately-legal transitions: live / draft /
    # legacy-NULL sources pass all three guards.
    new = _point(sdk)
    assert sdk.supersede_point(_point(sdk, status), new)["invalidated"] is True
    assert sdk.retract_point(_point(sdk, status))["status"] == "retracted"
    corr = _point(sdk)
    assert sdk.invalidate_point(
        _point(sdk, status), corr)["invalidated"] is True


# ── the legacy outdated=true FLAG is terminal (divergence #1) ───────────

@pytest.mark.parametrize("method", ["supersede", "retract", "invalidate"])
def test_legacy_outdated_flag_is_terminal_for_every_method(sdk, method):
    old = _point(sdk, "live", outdated=True)   # invalidate_point's flag write
    new = _point(sdk)
    with pytest.raises(ValueError, match="already terminal"):
        if method == "supersede":
            sdk.supersede_point(old, new)
        elif method == "retract":
            sdk.retract_point(old)
        else:
            sdk.invalidate_point(old, new)


def test_flag_dead_source_cannot_transfer_edges(sdk):
    """A flag-dead claim must not be re-superseded — the pre-#2498 guard let
    it through and transferred its operator edges off a dead point."""
    src, dst = _point(sdk), _point(sdk)
    sdk.create_operator("IMPL", src, [dst])
    sdk.invalidate_point(src, _point(sdk))     # flag-dead, status stays live
    before = _operator_edges(sdk)
    with pytest.raises(ValueError, match="already terminal"):
        sdk.supersede_point(src, _point(sdk))
    assert _operator_edges(sdk) == before, "guard must reject before any write"


def _operator_edges(sdk) -> int:
    return sdk._get_proj().g.query(
        "MATCH (:Point {is_operator:true})-[r]->(:Point) RETURN count(r)",
    ).result_set[0][0]


# ── the previously-divergent statuses (divergence #1, exact repros) ─────

def test_status_outdated_could_not_be_re_superseded(sdk):
    # `outdated` is in POINT_STATUS_VALUES but was NOT in the pre-#2498 tuple.
    with pytest.raises(ValueError, match="already terminal"):
        sdk.supersede_point(_point(sdk, "outdated"), _point(sdk))


def test_status_deprecated_could_not_be_re_retracted(sdk):
    # `deprecated` is legacy write-only but terminal on every read surface.
    with pytest.raises(ValueError, match="already terminal"):
        sdk.retract_point(_point(sdk, "deprecated"))


# ── operator input (divergence #2: invalidate_point) ────────────────────

@pytest.mark.parametrize("method", ["supersede", "retract", "invalidate"])
def test_operator_input_rejected_by_every_method(sdk, method):
    op, dst = _op(sdk), _point(sdk)
    with pytest.raises(ValueError, match="operator"):
        if method == "supersede":
            sdk.supersede_point(op, dst)
        elif method == "retract":
            sdk.retract_point(op)
        else:
            # Pre-#2498: invalidate_point flagged an operator outdated=true and
            # dropped its EP messages — the headline #2498 repro.
            sdk.invalidate_point(op, dst)


@pytest.mark.parametrize("bad", ["terminal", "operator"])
def test_supersede_rejects_bad_target(sdk, bad):
    old = _point(sdk)
    if bad == "terminal":
        with pytest.raises(ValueError, match="already terminal"):
            sdk.supersede_point(old, _point(sdk, "retracted"))
    else:
        with pytest.raises(ValueError, match="operator"):
            sdk.supersede_point(old, _op(sdk))


@pytest.mark.parametrize("bad", ["terminal", "operator"])
def test_invalidate_rejects_bad_corrector(sdk, bad):
    """P1: the CORRECTOR leg must reject an operator / dead point too —
    otherwise `(operator|terminal)-[:CORRECTS]->(point)` is writable and
    `supersede(..., transfer_edges=False)` disagrees with
    `supersede(..., transfer_edges=True)`, which guards both endpoints."""
    old = _point(sdk)
    corrector = _op(sdk) if bad == "operator" else _point(sdk, "superseded")
    with pytest.raises(ValueError, match=r"operator|already terminal"):
        sdk.invalidate_point(old, corrector)
    assert not sdk.get_point(old).get("outdated"), "no partial write"


def test_invalidate_missing_corrector_raises_without_write(sdk):
    old = _point(sdk)
    with pytest.raises(ValueError, match="No point"):
        sdk.invalidate_point(old, "missing-corrector")
    assert not sdk.get_point(old).get("outdated"), "no partial write"


def test_supersede_both_legs_agree_on_operator_target(sdk):
    # #2498: transfer_edges=True and False are the SAME transition; they must
    # not diverge on an operator successor.
    old1, old2, op = _point(sdk), _point(sdk), _op(sdk)
    with pytest.raises(ValueError, match="operator"):
        sdk.supersede(old1, op)
    with pytest.raises(ValueError, match="operator"):
        sdk.supersede(old2, op, transfer_edges=False)


# ── missing-input contracts are unchanged ───────────────────────────────

def test_missing_input_contracts(sdk):
    with pytest.raises(ValueError, match="No point"):
        sdk.supersede_point("missing-old", _point(sdk))
    with pytest.raises(ValueError, match="No point"):
        sdk.supersede_point(_point(sdk), "missing-new")
    with pytest.raises(ValueError, match="No point"):
        sdk.retract_point("missing")
    # #330: invalidate keeps the retry-friendly missing-old contract.
    corr = _point(sdk)
    assert sdk.invalidate_point("missing", corr) == {
        "invalidated": False, "id": "missing", "corrected_by": corr}


# ── structural: one guard, no hardcoded subset ──────────────────────────

def test_all_three_methods_route_through_the_shared_guard():
    for meth in (TortoiseSDK.supersede_point, TortoiseSDK.retract_point,
                 TortoiseSDK.invalidate_point):
        assert "_assert_lifecycle_guard" in inspect.getsource(meth), meth.__name__


def test_lifecycle_writers_have_no_hardcoded_status_subset():
    # Regression pin: the pre-#2498 tuple must not reappear in the writers.
    # Whitespace-normalised so a differently-spaced reintroduction is caught.
    src = "".join(inspect.getsource(m) for m in (
        TortoiseSDK.supersede_point, TortoiseSDK.retract_point,
        TortoiseSDK.invalidate_point))
    norm = "".join(src.split())
    assert '("retracted","superseded","archived")' not in norm
    assert "IN$terminal" not in norm


# ── #7822: the verdict is over EVERY node carrying the id ───────────────

# Point ids are not unique (``create_point(..., id=X)`` twice is a tested,
# graph-reachable shape) and every lifecycle writer's stamp is a bare
# ``MATCH (n:Point {id:$id}) SET …`` that binds EVERY matching node. The
# pre-#7822 guard read ``row[0]``, so on an id with one dead and one live node
# its terminal/operator verdict — and therefore whether a dead claim got
# re-terminalized and its edges transferred — depended on server row order.
#
# These tests build the fan-out so that the FIRST row the server returns is the
# LIVE sibling (the setup query below is the guard's own statement, and the
# terminal status is written to its LAST row). A first-row-only read therefore
# sees a live point and PASSES — the pre-#7822 failure is deterministic here,
# not a row-order coin flip.


def _duplicate_with_dead_sibling(sdk, dup: str, *, dead_status="retracted",
                                 dead_is_operator=False) -> list[tuple]:
    """Two nodes share ``dup``: the server's FIRST row stays LIVE, the LAST
    goes terminal (or operator). Returns the pre-state rows so a caller can
    assert the premise it relies on."""
    from tortoise.live import LIFECYCLE_FANOUT_QUERY

    sdk.create_point("statement", "dup live sibling", id=dup)
    sdk.create_point("statement", "dup dead sibling", id=dup)
    # Same MATCH as the guard's statement, with ID(n) appended — the row order
    # is the server's, so the node in the LAST row is the one a first-row-only
    # read would leave unexamined.
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.is_operator, n.status, "
        "coalesce(n.outdated, false), ID(n)", params={"id": dup}
    ).result_set
    assert len(rows) == 2, rows
    old_nid = rows[-1][3]
    sets, params = [], {"nid": old_nid}
    if dead_is_operator:
        sets.append("n.is_operator = true")
    else:
        sets.append("n.status = $st")
        params["st"] = dead_status
    sdk._get_proj().g.query(
        f"MATCH (n:Point) WHERE ID(n) = $nid SET {', '.join(sets)}",
        params=params)
    # Read the POST-SET fan-out with the guard's own statement: the premise a
    # caller asserts (first row live, last row dead) must hold for the state the
    # guard actually classifies.
    return sdk._get_proj().g.query(
        LIFECYCLE_FANOUT_QUERY, params={"id": dup}).result_set


def test_fanout_classifier_is_row_order_independent():
    """The shared classifier's verdict does not depend on row order (#7822)."""
    from tortoise.live import classify_lifecycle_fanout

    live, dead = (False, "live", False), (False, "retracted", False)
    # Both orders of the SAME two rows must give the SAME verdict: terminal.
    assert classify_lifecycle_fanout([live, dead])[0] == "terminal"
    assert classify_lifecycle_fanout([dead, live])[0] == "terminal"
    # Operator outranks terminal (the pre-#7822 single-row precedence).
    op = (True, "live", False)
    assert classify_lifecycle_fanout([live, dead, op])[0] == "operator"
    assert classify_lifecycle_fanout([op, live, dead])[0] == "operator"
    # All-live (and absent) are unchanged, and the representative is stable.
    assert classify_lifecycle_fanout([live, (False, "draft", False)]) == (
        "ok", "draft", False)
    assert classify_lifecycle_fanout([(False, "draft", False), live]) == (
        "ok", "draft", False)
    assert classify_lifecycle_fanout([]) == ("absent", None, False)
    # The legacy flag is terminal too, on ANY sibling.
    assert classify_lifecycle_fanout(
        [live, (False, "live", True)])[0] == "terminal"


@pytest.mark.parametrize("method", ["supersede", "retract", "invalidate"])
def test_duplicate_id_with_a_dead_sibling_is_refused(sdk, method):
    """Every lifecycle writer refuses when ANY node carrying the id is dead.

    The refusal must be fail-closed: NEITHER sibling is stamped and no edge is
    transferred, because the writers' stamps bind every matching node.
    """
    dup = f"pt_dup_{uuid.uuid4().hex[:8]}"
    pre = _duplicate_with_dead_sibling(sdk, dup)
    # The premise, asserted rather than assumed: a first-row-only read of this
    # fan-out sees a LIVE point, so the pre-#7822 guard would have passed.
    assert not is_terminal_status(pre[0][1], bool(pre[0][2])), pre
    assert is_terminal_status(pre[-1][1], bool(pre[-1][2])), pre

    new = _point(sdk)
    with pytest.raises(ValueError, match="already terminal"):
        if method == "supersede":
            sdk.supersede_point(dup, new)
        elif method == "retract":
            sdk.retract_point(dup)
        else:
            sdk.invalidate_point(dup, new)

    # Fail-closed: the LIVE sibling was not terminalized and the dead one kept
    # its own state (no partial write on either node).
    after = {tuple(r) for r in sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.is_operator, n.status, "
        "coalesce(n.outdated, false)", params={"id": dup}).result_set}
    assert after == {tuple(r) for r in pre}, after
    assert sdk._get_proj().g.query(
        "MATCH (a:Point {id:$new})-[:CORRECTS]->(b:Point {id:$dup}) "
        "RETURN count(a)",
        params={"new": new, "dup": dup}).result_set[0][0] == 0


def _operator_edges_at(sdk, pid: str) -> int:
    """Operator-mediated edges INTO ``pid`` — the set supersede's 2a transfer
    re-points to the successor (the count alone would not move, so the check
    must be per-endpoint)."""
    return sdk._get_proj().g.query(
        "MATCH (op:Point {is_operator:true})-[r]->(p:Point {id:$id}) "
        "RETURN count(r)", params={"id": pid}).result_set[0][0]


def test_duplicate_id_with_a_dead_sibling_cannot_transfer_edges(sdk):
    """The #2498 invariant on a duplicated id: a dead claim's edges stay put."""
    dup = f"pt_dup_{uuid.uuid4().hex[:8]}"
    _duplicate_with_dead_sibling(sdk, dup)
    tgt = _point(sdk)
    # An operator edge into the id. The writer's 2a transfer MATCHes by logical
    # id, so a passed guard would re-point this edge to the successor.
    sdk.create_operator("IMPL", dup, [tgt])
    new = _point(sdk)
    # The operator writer itself fans out over the duplicate id (one edge per
    # matching node), so only the BEFORE/AFTER equality is asserted — the
    # invariant is that a refused transition moves nothing.
    before_dup = _operator_edges_at(sdk, dup)
    assert before_dup >= 1, before_dup
    assert _operator_edges_at(sdk, new) == 0
    with pytest.raises(ValueError, match="already terminal"):
        sdk.supersede_point(dup, new)
    assert _operator_edges_at(sdk, dup) == before_dup, (
        "guard must reject before any edge transfer")
    assert _operator_edges_at(sdk, new) == 0


def test_duplicate_id_with_an_operator_sibling_is_refused(sdk):
    """An operator sibling refuses the id for every writer (any-node scan)."""
    dup = f"pt_dup_{uuid.uuid4().hex[:8]}"
    pre = _duplicate_with_dead_sibling(sdk, dup, dead_is_operator=True)
    # Premise: a first-row-only read sees the live statement sibling.
    assert not bool(pre[0][0]), pre  # (is_operator, status, outdated)
    assert bool(pre[-1][0]), pre
    with pytest.raises(ValueError, match="operator"):
        sdk.retract_point(dup)


# ── #7819: `update_point` re-stamped a DEAD claim's temporal record ───────
#
# `update_point` never consulted the shared `_assert_lifecycle_guard`, so a
# TERMINAL claim's validity window could be rewritten through it:
#
#     sdk.retract_point(p)                       # p is now TERMINAL
#     sdk.update_point(p, validTo="2026-06-10")  # ...accepted, window written
#
# The guard is scoped to `_TEMPORAL_RESTAMP_PROPS` (window bounds + their
# precision markers + `expiredAt`) because a content/metadata revision of a
# terminal claim is a recorded capability (#3305). Both halves are pinned here:
# what is refused, and what keeps working. `update_entity`'s Point arms open a
# second door onto the same window and are tracked separately as #9649.

def _stored(pid, key, sdk):
    rows = sdk._get_proj().g.query(
        f"MATCH (n:Point {{id:$id}}) RETURN n.{key}",
        params={"id": pid}).result_set
    return rows[0][0] if rows else None


@pytest.mark.parametrize("status", _TERMINAL_STATUSES)
def test_update_point_refuses_a_window_on_every_terminal_status(sdk, status):
    """The #7819 hole: a dead claim's window is not writable.

    Fails on the pre-fix tree — `update_point` returned normally and stored the
    caller's `validTo` on a `retracted`/`superseded`/`outdated`/`archived`/
    `deprecated` point.
    """
    pid = _point(sdk, status=status)
    with pytest.raises(ValueError, match="already terminal"):
        sdk.update_point(pid, validTo="2026-06-10")
    assert _stored(pid, "validTo", sdk) is None, (
        f"the refused write still landed a window on a {status} point")


@pytest.mark.parametrize("prop,value", [
    ("validFrom", "2026-01-01"),
    ("validTo", "2026-06-10"),
    ("expiredAt", "2026-06-10"),
])
def test_update_point_refuses_the_whole_temporal_record(sdk, prop, value):
    """The refused set is the TEMPORAL RECORD, not just `validTo`: `expiredAt` is
    the transaction-time termination stamp. Pre-fix each of these landed."""
    pid = _point(sdk, status="retracted")
    with pytest.raises(ValueError, match="already terminal"):
        sdk.update_point(pid, **{prop: value})
    assert _stored(pid, prop, sdk) is None


@pytest.mark.parametrize("bound,marker", [
    ("validFrom", "validFromPrecision"),
    ("validTo", "validToPrecision"),
])
def test_update_point_refuses_a_precision_marker_on_a_dead_claim(
        sdk, bound, marker):
    """A precision marker describes a BOUND, so it is part of the temporal
    record and is refused with it.

    Exercised RIDING its bound (the realistic caller shape). Supplied alone, the
    #2350 precision validator refuses it whenever the point stores no bound of
    its own — a different and equally correct refusal, which is why the guard
    sits after the inverted-window and precision checks.
    """
    pid = _point(sdk, status="retracted")
    with pytest.raises(ValueError, match="already terminal"):
        sdk.update_point(pid, **{bound: "2026-06-10", marker: "day"})
    assert _stored(pid, bound, sdk) is None
    assert _stored(pid, marker, sdk) is None


def test_update_point_refuses_the_legacy_outdated_flag(sdk):
    """The legacy ``outdated=true`` flag is terminal for this door too — the
    shared predicate, never a locally hardcoded status list."""
    pid = _point(sdk, outdated=True)
    with pytest.raises(ValueError, match=r"already terminal \(outdated\)"):
        sdk.update_point(pid, validTo="2026-06-10")
    assert _stored(pid, "validTo", sdk) is None


def test_a_dead_claim_still_accepts_a_content_and_metadata_revision(sdk):
    """The OTHER half, and the reason the guard is scoped (#3305).

    A revision that arrives after a terminalization is the NEWER same-id writer
    and the replay fold models it (`skip_updated_at` via `max_inline_seq`) —
    pinned by
    `test_pointinvalidated_rebuild.py::test_revise_after_invalidate_skip_
    updated_at` and
    `test_pointsuperseded_rebuild.py::test_supersede_then_revision_does_not_
    end_at_the_supersede_ts`, both of which revise a terminal point THROUGH
    this door. A blanket refusal would redden both. Content, tags and ordinary
    metadata therefore stay writable; only the temporal record is refused.
    """
    pid = _point(sdk, status="superseded")
    sdk.update_point(pid, content="revised after supersede")
    assert _stored(pid, "content", sdk) == "revised after supersede"
    sdk.update_point(pid, note="revised metadata", tags=["after-death"])
    assert _stored(pid, "note", sdk) == "revised metadata"
    assert _stored(pid, "status", sdk) == "superseded", "no resurrection"
    # ...and the temporal record is STILL refused after those revisions.
    with pytest.raises(ValueError, match="already terminal"):
        sdk.update_point(pid, validTo="2026-06-10")


def test_a_refused_temporal_write_leaves_the_point_byte_identical(sdk):
    """Fail-closed: status, window and props are untouched, so a caller that
    catches the ValueError is not left with a half-applied edit."""
    pid = _point(sdk, status="superseded")
    keys = ("status", "validFrom", "validTo", "validToPrecision", "expiredAt",
            "content", "confidence", "updatedAt")
    before = tuple(_stored(pid, k, sdk) for k in keys)
    for props in ({"validTo": "2026-06-10"},
                  {"validFrom": "2026-01-01"},
                  {"validTo": "2026-06-10", "confidence": 0.9},
                  {"validFrom": "2026-01-01", "content": "rewritten"}):
        with pytest.raises(ValueError, match="already terminal"):
            sdk.update_point(pid, **props)
    assert tuple(_stored(pid, k, sdk) for k in keys) == before


def test_a_refused_temporal_write_journals_no_revision(tmp_path):
    """No record may precede a write the guard refuses.

    The non-promote arm appends its ``PointRevised`` BEFORE the write (#7951
    outbox ordering), so a guard placed after the append would leave the
    journal claiming a revision the graph never took — and a rebuild would
    apply it.
    """
    from tortoise.log import EventLog
    from tortoise.sdk import TortoiseSDK

    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "g7819.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        pid = sdk.create_point("statement", "dead before revision")["id"]
        sdk.retract_point(pid)
        path = events / "events.jsonl"
        before = [e for e in EventLog(str(path)).read_all()
                  if e.get("type") == "PointRevised"
                  and e.get("id") == pid]
        with pytest.raises(ValueError, match="already terminal"):
            sdk.update_point(pid, validTo="2026-06-10")
        after = [e for e in EventLog(str(path)).read_all()
                 if e.get("type") == "PointRevised"
                 and e.get("id") == pid]
        assert after == before, "a refused update must journal nothing"
    finally:
        sdk.close()


def test_update_point_keeps_the_absent_id_contract(sdk):
    """``missing_ok=True``: a missing id still returns ``{}`` (measured at HEAD
    before this change) — the guard must not turn it into a raise."""
    assert sdk.update_point("missing-7819", validTo="2026-06-10") == {}


def test_update_point_still_writes_a_live_operator(sdk):
    """``allow_operator=True``: ``annotate_operator``'s body IS this verb, so
    operator points must stay writable — closing the hole must not break the
    annotate path."""
    src, dst = _point(sdk), _point(sdk)
    op = sdk.create_operator("IMPL", src, [dst])["id"]
    sdk.annotate_operator(op, 0.1, 0.9, 0.9, 0.9)
    assert _stored(op, "annotator_precision", sdk) == 0.9
    # ...and a generic prop on a live operator is still an ordinary write.
    sdk.update_point(op, note="carried")
    assert _stored(op, "note", sdk) == "carried"


def test_update_point_refuses_a_terminal_operator_record(sdk):
    """The other half of ``allow_operator``: operator-ness is writable here,
    the TEMPORAL record is not — ``live.classify_lifecycle_fanout`` ranks the
    operator verdict ABOVE terminal, so this case is only refused if the guard
    scans the same fan-out for terminality instead of trusting the operator
    branch."""
    src, dst = _point(sdk), _point(sdk)
    op = sdk.create_operator("IMPL", src, [dst])["id"]
    # ``retract_point`` refuses operators by design (retraction is for
    # statement points), so the terminal operator is written directly — the
    # guard must handle any state the graph can hold, not just creatable ones
    # (same reason ``_point`` writes status directly).
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status = 'retracted'",
        params={"id": op})
    with pytest.raises(ValueError, match="already terminal"):
        sdk.update_point(op, validTo="2026-06-10")
    assert _stored(op, "validTo", sdk) is None


def test_update_point_refuses_a_duplicate_id_with_a_dead_sibling(sdk):
    """#7822's fan-out rule reaches this door too: the write MATCHes EVERY node
    carrying the id, so ONE dead sibling refuses the whole id."""
    dup = f"pt_dup_{uuid.uuid4().hex[:8]}"
    pre = _duplicate_with_dead_sibling(sdk, dup)
    assert not is_terminal_status(pre[0][1], bool(pre[0][2])), pre
    assert is_terminal_status(pre[-1][1], bool(pre[-1][2])), pre
    with pytest.raises(ValueError, match="already terminal"):
        sdk.update_point(dup, validTo="2026-06-10")
    after = {tuple(r) for r in sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.is_operator, n.status, "
        "coalesce(n.outdated, false)", params={"id": dup}).result_set}
    assert after == {tuple(r) for r in pre}, after


def test_update_point_refuses_a_duplicate_id_with_a_DEAD_OPERATOR_sibling(sdk):
    """A terminal OPERATOR sibling on a duplicated id.

    The classifier answers ``operator`` for this fan-out, so a guard that
    treated "operator" as "allowed" on this door would still let the write
    land on the dead node — the exact shape ``allow_operator`` must NOT turn
    into a hole.
    """
    dup = f"pt_dup_{uuid.uuid4().hex[:8]}"
    _duplicate_with_dead_sibling(sdk, dup, dead_is_operator=True)
    # make the operator sibling TERMINAL as well (operator outranks terminal in
    # the classifier, so the two verdicts are now carried by ONE node).
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) WHERE n.is_operator = true "
        "SET n.status = 'retracted'", params={"id": dup})
    with pytest.raises(ValueError, match="already terminal"):
        sdk.update_point(dup, validTo="2026-06-10")


def test_update_point_routes_through_the_shared_guard():
    """Census pin: `update_point`'s terminal refusal goes through the shared
    guard, and its scope is a named set rather than an inline literal."""
    src = inspect.getsource(TortoiseSDK.update_point)
    assert "_assert_lifecycle_guard" in src
    assert "allow_operator=True" in src
    assert "missing_ok=True" in src
    assert "_TEMPORAL_RESTAMP_PROPS" in src, (
        "the scoping must be a named set, not an inline literal")


def test_create_point_dedup_does_not_fold_a_window_onto_a_dead_claim(
        sdk, caplog):
    """#7819, the dedup side: a fold that carries a window is all-or-nothing.

    The fold writes through more than one route (``update_point``, and the
    authored-word leg's ``set_point_baseline`` / ``_stamp_authored_word``), so
    the check sits at the top of the branch rather than beside any single write,
    and the WHOLE fold is dropped rather than applied-minus-the-window. The
    drop is a WARNING with the dedup HIT still reported — never a silent
    skip (#5365) and never a raise, which is the fail-closed-500 class #3366
    exists to prevent on the hosted commit path.
    """
    content = f"dedup onto dead {uuid.uuid4().hex[:8]}"
    pid = sdk.create_point("statement", content)["id"]
    sdk.retract_point(pid)
    before = sdk.get_point(pid)
    with caplog.at_level("WARNING"):
        again = sdk.create_point("statement", content, validFrom="2026-01-01",
                                 confidence=0.99, dedup=True)
    assert again["id"] == pid, "a dedup HIT is still reported as the same point"
    after = sdk.get_point(pid)
    assert after["status"] == "retracted"
    assert _stored(pid, "validFrom", sdk) is None
    assert after["confidence"] == before["confidence"], (
        "the fold is all-or-nothing — no prop may land beside the refused "
        "window")
    assert any("re-stamp its temporal record" in r.getMessage()
               for r in caplog.records), (
        "the dropped fold must WARN — a silent drop is the #5365 defect")


def test_a_live_claim_still_takes_a_temporal_write(sdk):
    """The scope boundary from the ALLOW side: the guard is over the TARGET's
    lifecycle, not over the props, so a live claim must still accept a window.
    Without this mirror a blanket refusal (or a marker returned for every
    point) would leave the rest of this block green."""
    pid = _point(sdk)
    sdk.update_point(pid, validTo="2026-06-10", confidence=0.93)
    assert _stored(pid, "validTo", sdk) == "2026-06-10"
    assert _stored(pid, "confidence", sdk) == pytest.approx(0.93)
    assert sdk.get_point(pid)["status"] == "live"


def test_create_point_dedup_folds_a_window_onto_a_live_claim(sdk):
    """Same boundary on the dedup door: a fold carrying a window applies in
    full when the target is live — the drop is the TERMINAL case only, so a
    blanket drop would be caught here."""
    content = f"dedup window {uuid.uuid4().hex[:8]}"
    pid = sdk.create_point("statement", content)["id"]
    again = sdk.create_point("statement", content, dedup=True,
                             validFrom="2026-01-01", confidence=0.97)
    assert again["id"] == pid
    assert _stored(pid, "validFrom", sdk) == "2026-01-01"
    assert _stored(pid, "confidence", sdk) == pytest.approx(0.97)


def test_create_point_dedup_without_a_window_still_folds(sdk):
    """The boundary: a fold with NO temporal prop keeps applying, dead target or
    not — the same rule as any other revision of a dead claim (#3305)."""
    content = f"dedup metadata {uuid.uuid4().hex[:8]}"
    pid = sdk.create_point("statement", content)["id"]
    sdk.retract_point(pid)
    again = sdk.create_point("statement", content, confidence=0.99,
                             dedup=True)
    assert again["id"] == pid
    assert sdk.get_point(pid)["confidence"] == pytest.approx(0.99)
    assert sdk.get_point(pid)["status"] == "retracted"
