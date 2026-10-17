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
