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
