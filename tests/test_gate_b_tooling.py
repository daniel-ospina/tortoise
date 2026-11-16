"""Gate B tooling tests (epic-264 #779): mean_grounding + drift snapshot +
calibration milestone marker.

Covers the DE2E-4 grounding snapshot contract (mean over ``confidence`` of
live non-operator Points, #780 live-only semantics).

#4282 retired five tests that stood under the DE2E-7 calibration marker section,
and they split in two — which is why the ledger records three and REBUILDS two:

- **Rebuilt (2)** — `test_calibration_passed_false_then_true` and
  `test_calibration_marker_persists_across_restart` were tests OF the
  ``calibration_passed()`` accessor. They drove ``record_calibration`` only
  as ARRANGEMENT, so they are restored below with the arrangement written as the
  marker node the record writer itself creates (the raw-Cypher form already used
  by `tests/test_rebuild_config_preservation.py`), with the marker KEY's value
  pinned by literal — deriving it from the class attribute the accessor also
  reads would make a rename invisible.
  ⛔ **#12901 wave 1 retired the accessor itself.** The two tests are re-pointed
  at the marker through the PRIVATE `_calibration_marker_recorded()` seam
  (`_calibration_marker_present`) — the SAME seam `tortoise/gates.py` reads —
  instead of at a public method that no longer exists, and
  `test_gate_b_cli_reads_the_private_marker_seam` pins that wiring. The durable
  contract was always "the writer stores the marker and a fresh SDK still sees
  it", never "there is a public reader for it".
  `_CALIBRATION_MARKER_KEY` STAYS on the class: the projection's config-reset
  preservation reads it.
- **Recorded as removed (3)** — `test_record_calibration_validation`,
  `test_record_calibration_enforces_gate_targets` and
  `test_record_calibration_is_idempotent` assert behaviour OF
  ``record_calibration``, one of the 26 dead public methods #4282 deleted (see
  `tortoise/sdk_retired.py`), so no subject survives to re-point them at.
"""
from __future__ import annotations  # noqa: I001

import pytest

from tortoise.analyze import (
    MAX_GROUNDING_DRIFT,
    MAX_POINT_DRIFT,
    mean_grounding,
    grounding_snapshot,
    grounding_drift,
)
from tortoise.sdk import TortoiseSDK


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def make_point(sdk: TortoiseSDK, content: str, *, confidence=None,
               status="live", **props):
    """Create a Point with explicit confidence/status (create_point defaults
    to status='draft' — Gate B measures LIVE Points only)."""
    if confidence is not None:
        props["confidence"] = confidence
    props["status"] = status
    return sdk.create_point("statement", content, **props)


# ── mean_grounding: DE2E-4 formula ────────────────────────────────

def test_mean_grounding_empty_graph_zero(sdk):
    assert mean_grounding(sdk._get_proj()) == 0.0


def test_mean_grounding_live_non_operator_only(sdk):
    proj = sdk._get_proj()
    # Live Points with explicit confidence → mean is over these three.
    make_point(sdk, "a", confidence=0.4)
    make_point(sdk, "b", confidence=0.6)
    make_point(sdk, "c", confidence=0.8)
    # Draft Point excluded even with extreme confidence.
    make_point(sdk, "draft poison", confidence=0.0, status="draft")
    # Operator Point excluded (is_operator: true) regardless of confidence.
    s = make_point(sdk, "source", confidence=0.5)
    t = make_point(sdk, "target", confidence=0.5)
    sdk.create_operator("IMPL", s["id"], [t["id"]])
    # Legacy NULL-status Point is LIVE (#780 semantics) and counts.
    legacy = make_point(sdk, "legacy", confidence=0.7)
    proj.g.query("MATCH (n:Point {id:$id}) REMOVE n.status",
                 params={"id": legacy["id"]})
    # NULL-confidence live Point defaults to 0.5 (repo convention).
    make_point(sdk, "no conf")

    # Counted: a, b, c, legacy, no-conf, plus the IMPL source/target (live
    # non-operator Points — operators are excluded, endpoints are not).
    expected = (0.4 + 0.6 + 0.8 + 0.7 + 0.5 + 0.5 + 0.5) / 7
    assert mean_grounding(proj) == pytest.approx(expected)
    # Operator exclusion is structural: assert the op node exists in the
    # graph as a Point yet is not counted (count stays 7, not 8).
    snap = grounding_snapshot(proj)
    assert snap["count"] == 7
    assert snap["mean"] == pytest.approx(expected)


def test_mean_grounding_snapshot_matches_mean(sdk):
    proj = sdk._get_proj()
    a = make_point(sdk, "a", confidence=0.3)
    b = make_point(sdk, "b", confidence=0.9)
    snap = grounding_snapshot(proj)
    assert snap["count"] == 2
    assert snap["mean"] == pytest.approx(0.6)
    assert snap["mean"] == pytest.approx(mean_grounding(proj))
    assert set(snap["points"]) == {a["id"], b["id"]}
    assert "sampled_at" in snap and snap["sampled_at"]  # noqa: RUF019


def test_mean_grounding_null_confidence_imputed_zero_five(sdk):
    """A live Point with no confidence contributes 0.5 to the mean — the
    coalesce(0.5) imputation is a DELIBERATE extension of the DE2E-4 formula
    (repo-wide NULL-confidence convention), not part of the pinned plan
    formula. Asserted here per review round 1."""
    proj = sdk._get_proj()
    make_point(sdk, "no conf a")
    make_point(sdk, "no conf b")
    assert mean_grounding(proj) == pytest.approx(0.5)
    snap = grounding_snapshot(proj)
    assert snap["count"] == 2
    assert set(snap["points"].values()) == {0.5}
    # Mixed: one confident + two null → mean is (0.9 + 0.5 + 0.5) / 3.
    make_point(sdk, "confident", confidence=0.9)
    assert mean_grounding(proj) == pytest.approx((0.9 + 0.5 + 0.5) / 3)


# ── grounding_drift: ≤2% mean / ≤5% max single-point ceilings ─────

def test_grounding_drift_identical_snapshots_pass(sdk):
    make_point(sdk, "a", confidence=0.5)
    proj = sdk._get_proj()
    pre = grounding_snapshot(proj)
    post = grounding_snapshot(proj)
    drift = grounding_drift(pre, post)
    assert drift["passed"] is True
    assert drift["mean_abs_delta"] == 0.0
    assert drift["max_point_abs_delta"] == 0.0


def test_grounding_drift_mean_ceiling_fails(sdk):
    # Hand-built snapshots: mean shifts 0.50 → 0.53 (3% > 2% ceiling) while
    # every point moves < 5% — only the mean ceiling trips.
    pre = {"count": 2, "mean": 0.50,
           "points": {"p1": 0.50, "p2": 0.50}}
    post = {"count": 2, "mean": 0.53,
            "points": {"p1": 0.51, "p2": 0.55}}
    drift = grounding_drift(pre, post)
    assert drift["passed"] is False
    assert drift["mean_abs_delta"] == pytest.approx(0.03)
    assert drift["max_point_abs_delta"] == pytest.approx(0.05)
    assert drift["mean_ceiling"] == MAX_GROUNDING_DRIFT


def test_grounding_drift_point_ceiling_fails(sdk):
    # Mean moves 0.017 (≤2%) but one point flips 0.5 → 0.6 (10% > 5%): the
    # per-point ceiling (R12) must catch what the mean masks. Means are
    # recomputed over the ID intersection (review round 1) — with 6 points
    # the single 0.1 flip dilutes to a 0.0167 mean delta, under the 2% mean
    # ceiling, so only the per-point ceiling trips.
    pre = {"count": 6, "mean": 0.50,
           "points": {"p0": 0.50, "p1": 0.50, "p2": 0.50,
                       "p3": 0.50, "p4": 0.50, "p5": 0.50}}
    post = {"count": 6, "mean": 0.5167,
            "points": {"p0": 0.60, "p1": 0.50, "p2": 0.50,
                        "p3": 0.50, "p4": 0.50, "p5": 0.50}}
    drift = grounding_drift(pre, post)
    assert drift["passed"] is False
    assert drift["mean_abs_delta"] == pytest.approx(1 / 60)  # 0.0167 ≤ 0.02
    assert drift["max_point_abs_delta"] == pytest.approx(0.10)


def test_grounding_drift_removed_points_not_regression(sdk):
    # A Point deleted between snapshots is not a regression: per-point deltas
    # only run over the ID intersection.
    pre = {"count": 2, "mean": 0.50,
           "points": {"p1": 0.50, "gone": 0.50}}
    post = {"count": 1, "mean": 0.50, "points": {"p1": 0.50}}
    drift = grounding_drift(pre, post)
    assert drift["passed"] is True
    assert drift["max_point_abs_delta"] == 0.0


def test_grounding_drift_removed_point_mean_term_intersection_scoped(sdk):
    """The MEAN term is also computed over the ID intersection (review
    round 1): removing a high-confidence point whose value ≠ set mean shifts
    the full-set mean (0.90 → 0.50 here) but is not a regression — the
    intersection-scoped mean must stay flat and the check must pass."""
    pre = {"count": 2, "mean": 0.70,
           "points": {"p1": 0.50, "gone": 0.90}}
    post = {"count": 1, "mean": 0.50, "points": {"p1": 0.50}}
    drift = grounding_drift(pre, post)
    # Full-set means would read |0.50 - 0.70| = 0.20 > 0.02 ceiling;
    # intersection-scoped means are both 0.50 → delta 0.0.
    assert drift["passed"] is True
    assert drift["mean_abs_delta"] == pytest.approx(0.0)
    assert drift["max_point_abs_delta"] == pytest.approx(0.0)
    assert drift["pre_count"] == 2
    assert drift["post_count"] == 1


def test_grounding_drift_zero_overlap_fails_closed(sdk):
    """Total replacement (zero shared Point ids) must FAIL CLOSED (review
    round 2): with an empty intersection both recomputed means are 0.0, so
    the ceilings previously passed vacuously — pre 0.90 → post 0.30
    reported passed True. An empty comparison is vacuous, not evidence:
    fail with an explicit ``no_common_points`` reason and ``overlap: 0``."""
    pre = {"count": 2, "mean": 0.90,
           "points": {"old_a": 0.90, "old_b": 0.90}}
    post = {"count": 2, "mean": 0.30,
            "points": {"new_a": 0.30, "new_b": 0.30}}
    drift = grounding_drift(pre, post)
    assert drift["passed"] is False
    assert drift["reason"] == "no_common_points"
    assert drift["overlap"] == 0
    assert drift["mean_abs_delta"] == 0.0
    assert drift["max_point_abs_delta"] == 0.0
    assert drift["pre_count"] == 2
    assert drift["post_count"] == 2


def test_grounding_drift_nonempty_overlap_reported(sdk):
    """The ID-intersection size is surfaced on the result for all paths —
    an empty intersection is the fail-closed signal (overlap == 0), so a
    non-empty intersection reports its overlap alongside a normal pass."""
    pre = {"count": 3, "mean": 0.50,
           "points": {"p1": 0.50, "p2": 0.50, "gone": 0.50}}
    post = {"count": 2, "mean": 0.50, "points": {"p1": 0.50, "p2": 0.50}}
    drift = grounding_drift(pre, post)
    assert drift["passed"] is True
    assert drift["overlap"] == 2
    assert "reason" not in drift


def test_grounding_drift_ceiling_constants_pinned(sdk):
    # The 0.02 constant is the EpSafeCommit max_grounding_drift seam (#785) —
    # pin it so the contract cannot silently drift.
    assert MAX_GROUNDING_DRIFT == 0.02
    assert MAX_POINT_DRIFT == 0.05


# ── Calibration milestone marker: DE2E-7 local contract ───────────

def _store_calibration_marker(sdk, **props) -> None:
    """Write the marker node the RETIRED record writer used to create.

    The writer (`record_calibration`) is gone with #4282, so the tests below
    arrange its RESULT directly — the same `MERGE (m:Meta {key:$key}) SET m += $props`
    form `tests/test_rebuild_config_preservation.py` already uses, and the same
    `recordedAt` stamp the writer applied.

    ⛔ The key is derived from the class attribute here, which on its own would
    be VACUOUS as a pin (the accessor reads that same attribute, so a rename
    moves both sides together and both stay green). The key's VALUE is therefore
    pinned separately, by literal, in
    `test_calibration_marker_key_is_the_persisted_literal` below — a literal on
    the TEST side is what makes a rename visible.
    """
    props.setdefault("recordedAt", "2026-01-01T00:00:00Z")
    sdk._get_proj().g.query(
        "MERGE (m:Meta {key:$key}) SET m += $props",
        params={"key": TortoiseSDK._CALIBRATION_MARKER_KEY, "props": props})


def _calibration_marker_present(sdk) -> bool:
    """Is the Gate B calibration-milestone marker stored? (through the PRODUCTION seam)

    #12901 wave 1 retired the public `TortoiseSDK.calibration_passed()` accessor. Its one
    production caller (`tortoise/gates.py`, the Gate B CLI) reads the same marker through
    the PRIVATE `_calibration_marker_recorded()` seam, so this helper drives THAT — not a
    copy of its query — and the reader gates.py depends on is covered rather than assumed.

    ⚠️ It used to re-spell the query here, which is exactly why a broken reader could stay
    invisible: the test proved the MARKER round-tripped while the accessor it was supposed
    to protect was deleted underneath a bare `except Exception`. Drive the seam.
    """
    return sdk._calibration_marker_recorded()


def test_gate_b_cli_reads_the_private_marker_seam():
    """`tortoise/gates.py` resolves Gate B through the private seam, not a dead name.

    ⛔ THIS IS THE REGRESSION THAT ACTUALLY HAPPENED. gates.py read
    ``TortoiseSDK().calibration_passed()`` inside a bare ``except Exception``; #12901 wave 1
    deleted that public method, the call started raising ``AttributeError``, the bare
    except swallowed it, and ``calibration_passed`` resolved to ``False`` FOREVER — making
    the Gate B CLI report "not recorded" for every issue while every existing test stayed
    green (they pre-supply the flag to ``check_gates()`` and never exercised the read).

    A receiver-keyed scan misses the caller too: its receiver is a CONSTRUCTOR CALL
    (``TortoiseSDK()``), not a name. So the wiring is pinned at the source.

    FAILS IF gates.py goes back to a public accessor that no longer exists, if it stops
    carrying the seam's value into the Gate B verdict, or if it invents a third read path
    that fills `calibration_passed` from somewhere else.
    """
    import ast
    import inspect

    from tortoise import gates

    src = inspect.getsource(gates)
    # ⛔ The arm must be about a CALL, not a mention. `gates.py`'s own module docstring and its
    # comments name this seam three times, so a substring test (`"_calibration_marker_recorded()"
    # in src`) passes with NO call present — a fresh-context reviewer measured exactly that at
    # `ac6a4066`. A `Call` node cannot be satisfied by prose, a docstring, or a comment, so the
    # wiring assertion is taken from the AST and the retired-accessor arm is too.
    tree = ast.parse(src)
    called = [n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert "_calibration_marker_recorded" in called, (
        "gates.py MENTIONS the private seam but never CALLS it — the bare `except Exception` "
        "would swallow the AttributeError and Gate B would resolve to False forever"
    )
    assert "calibration_passed" not in called, (
        "gates.py calls the retired public accessor `calibration_passed()`, which no longer "
        "exists on TortoiseSDK (#12901 wave 1)"
    )
    # ⛔ PRESENCE IS NOT FLOW. The two arms above only ask whether the seam is CALLED — a
    # `gates.py` that calls it for effect and then fills the flag from somewhere else passes
    # both, which is the "third read path" the docstring claims to reject (measured
    # 2026-11-16 by a fresh-context reviewer). So the variable that carries Gate B's state
    # into `check_gates` is pinned at its SOURCES: it may be the seam call or the `False`
    # fallback, and nothing else.
    assignments = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "calibration_passed" for t in n.targets)
    ]
    assert any(
        isinstance(n.value, ast.Call)
        and getattr(n.value.func, "attr", None) == "_calibration_marker_recorded"
        for n in assignments
    ), (
        "gates.py never assigns `calibration_passed` FROM the private seam — naming the seam "
        "in a call does not carry its value into the Gate B verdict, so the marker read "
        "result is thrown away"
    )
    other_sources = sorted(
        ast.unparse(n.value)
        for n in assignments
        if not (
            (
                isinstance(n.value, ast.Call)
                and getattr(n.value.func, "attr", None) == "_calibration_marker_recorded"
            )
            or (isinstance(n.value, ast.Constant) and n.value.value is False)
        )
    )
    assert not other_sources, (
        "gates.py fills `calibration_passed` from a THIRD read path — only the private seam "
        f"and the `False` fallback may source it, found: {other_sources}"
    )
    # The seam must be reachable on the class the CLI constructs, or the except swallows it.
    # ⛔ Attribute access, not `getattr(..., "<name>")`: B009 (ruff, active on this repo's
    # select surface) is right that a constant-name `getattr` is no safer than the plain
    # access — and the plain access is strictly more informative, because a missing seam
    # raises `AttributeError: type object 'TortoiseSDK' has no attribute
    # '_calibration_marker_recorded'` instead of a bare `assert False`.
    assert callable(TortoiseSDK._calibration_marker_recorded)


def test_calibration_marker_key_is_the_persisted_literal():
    """The marker key is DATA already written into shipped graphs — pin its value.

    `_CALIBRATION_MARKER_KEY` was read by ``calibration_passed()`` (retired by #12901
    wave 1) and was written by the retired ``record_calibration``. Pinning it only
    vacuous: a rename moves writer and reader together, every test stays green,
    and every EXISTING graph — whose marker sits under the literal
    ``calibration_milestone``, the key `tortoise/hosted_backup.py` and
    `tortoise/record_restore.py` classify as DATA — silently reads False. So the
    LITERAL is asserted here.

    FAILS IF the constant's VALUE is re-spelled — that is the case where the
    graphs already in the field would stop answering. An attribute RENAME also
    reds it (harmlessly): the reader and any writer move with the attribute, so
    the stored key is untouched and no field graph is affected — do not read
    that red as evidence of data breakage.
    """
    assert TortoiseSDK._CALIBRATION_MARKER_KEY == "calibration_milestone"


def test_calibration_marker_recorded_then_read(sdk):
    """The marker node round-trips: absent ⇒ False, present ⇒ True.

    #12901 wave 1: rebuilt on the marker NODE, because the ``calibration_passed()``
    accessor this originally drove left the public surface. FAILS IF the marker
    node stops being written where a reader looks for it, or is reported present
    before anything recorded it.
    """
    assert _calibration_marker_present(sdk) is False
    _store_calibration_marker(
        sdk, precision=0.73, sample_size=50, mean_grounding_delta=0.012,
        notes="50-session human review, 3 reviewers")
    assert _calibration_marker_present(sdk) is True


def test_calibration_marker_persists_across_restart(tmp_path):
    """Marker is stored in the graph DB (:Meta node), not process memory —
    a fresh SDK on the same DB still reads it (survives restarts).

    #4282: rebuilt on the calibration marker; #12901 wave 1 re-pointed it at the
    # marker NODE after ``calibration_passed()`` left the public surface. The
    # arrangement is the marker node itself. FAILS IF the marker ever answers
    # from process state instead of the graph.
    """
    db_path = str(tmp_path / "persist.db")
    sdk1 = TortoiseSDK(db_path=db_path)
    try:
        assert _calibration_marker_present(sdk1) is False
        _store_calibration_marker(
            sdk1, precision=0.70, sample_size=50, mean_grounding_delta=0.02)
    finally:
        sdk1.close()
    sdk2 = TortoiseSDK(db_path=db_path)  # fresh instance, same DB file
    try:
        assert _calibration_marker_present(sdk2) is True
    finally:
        sdk2.close()


