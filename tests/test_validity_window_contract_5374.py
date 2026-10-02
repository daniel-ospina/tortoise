"""#5374 — ``validate_validity_window`` is the declared validity-window contract.

``tortoise/commit_schema.py::validate_validity_window`` is THE ONE HOME for the
interval rule

    _created_sort_key(validTo) >= _created_sort_key(validFrom)

with equality allowed and presence via ``is not None`` — the exact predicate
``restore_point_at``'s ``_covers`` assumes (``tortoise/sdk.py``). The temporal
axis is the analogue of ``commit_schema.validate_span``'s integer character-offset
axis.

Who the declaration binds IN THIS CHANGE: ``invalidate_point``, through
``TortoiseSDK._assert_window_start_not_inverted``. That guard already existed
(#5358), so the delegation re-homes the comparison without adding a refusal.
The other live ``:Point`` window writers are owned by their own issues —
``supersede_point`` by #4021 (its ``_preview_supersede`` parity by #5506), and
``create_point`` / ``update_point`` caller props plus ``mining._temporal_wire``
by #5359. Tests for those writers' future guards belong to those issues, so this
file deliberately drives only the declaration and its one current caller.

The projection fold/replay writers are deliberately NOT wired to the
declaration: a rebuild must REPLAY windows that already exist — including ones
inverted before a guard existed — so a fold that refused would turn a legacy
corruption into a FAILED RESTORE, before the repair path exists. Detection and
repair of such persisted windows is #5361's. That exclusion is pinned
behaviourally by
``test_projection_folds_replay_an_inverted_window_verbatim``.

Class-B test doctrine — every test answers BOTH:
  (1) *What value makes this test fail?*  Named per test below. For the refusal
      half, a strictly inverted orderable pair must be refused; a predicate that
      never raises reds. For the over-fix half, an equal or open-ended pair must
      NOT be refused; a predicate that refuses everything reds.
  (2) *Does the fixture reach it?*  Yes — every pair is passed literally to the
      declared function, and the read-path agreement test persists the pair and
      then asks ``restore_point_at``.

Runnable with:
  TORTOISE_DB_URI='docker://:falkordb@localhost:16720/tortoise_test_matrix' \\
    uv run pytest tests/test_validity_window_contract_5374.py -v
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

EARLY = "2026-06-01T00:00:00+00:00"
LATE = "2026-06-10T00:00:00+00:00"
MID = "2026-06-05T00:00:00+00:00"
BEFORE = "2026-05-01T00:00:00+00:00"
AFTER = "2026-07-01T00:00:00+00:00"
UNPARSEABLE = "not-a-date"


@pytest.fixture
def sdk():
    """SDK on a fresh temp DB (the same fixture shape as the #5358 suite).

    The explicit temp path wins at the SDK constructor (#139), but under a
    supported ``TORTOISE_DB_URI`` the projection redirect (#1647) still targets
    that server — so the suite runs on the configured lane either way.
    """
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_vw5374_test_"), "test.db"
    )
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _declared_raises(valid_from, valid_to) -> bool:
    """The declared contract's verdict for a pair (True == refused)."""
    from tortoise.commit_schema import validate_validity_window
    try:
        validate_validity_window(valid_from, valid_to)
        return False
    except ValueError:
        return True


def _raised(fn) -> bool:
    try:
        fn()
        return False
    except ValueError:
        return True


def _props(sdk: TortoiseSDK, pid: str) -> dict:
    row = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)",
        params={"id": pid}).result_set
    assert row, f"point {pid} missing"
    return dict(row[0][0])


def _covers_at(sdk: TortoiseSDK, pid: str, instant: str) -> bool:
    """Whether the read path resolves ``pid`` at ``instant``.

    ``restore_point_at`` is the observable of `_covers`: a window that covers
    the instant resolves to the point; one that does not reports honest
    absence. Driving it (instead of re-deriving `_covers` here) is what makes
    the agreement assertion a comparison against the read path rather than a
    restatement of the guard.
    """
    return sdk.restore_point_at(pid, instant)["found"] is True


# ── the declared predicate: boundary cases ─────────────────────────────

def test_declaration_refuses_strictly_inverted_orderable_pair():
    """A strictly inverted, fully orderable pair is refused.

    (1) *What value makes this test fail?*  ``(valid_from=LATE, valid_to=EARLY)``
        with ``LATE > EARLY``: a predicate that never raises leaves
        ``_declared_raises`` at ``False``. A predicate that compared the raw
        ordering buckets without the parseability gate would also refuse a pair
        whose sides are unparseable, which the over-fix tests below catch.
    (2) *Does the fixture reach it?*  Yes — both bounds are literal ISO instants
        passed straight to the declaration.
    """
    assert _declared_raises(LATE, EARLY) is True


def test_declaration_allows_equal_bounds():
    """Equality is well-formed — a zero-length ``[t, t]`` window is legal.

    (1) *What value makes this test fail?*  ``(EARLY, EARLY)`` (and
        ``(LATE, LATE)``): a predicate using ``>=`` instead of ``>`` refuses
        the equal pair and reds. This is the boundary that separates an
        inversion from a legal zero-length window.
    (2) *Does the fixture reach it?*  Yes — the equal bound is written on both
        sides.
    """
    assert _declared_raises(EARLY, EARLY) is False
    assert _declared_raises(LATE, LATE) is False


def test_declaration_allows_open_ended_window_either_side():
    """An absent bound is legal — a window may be open at either end.

    (1) *What value makes this test fail?*  ``(EARLY, None)`` / ``(None, EARLY)``
        / ``(None, None)``: a predicate that required BOTH bounds to be present
        (or that compared ``None`` as a bucket) refuses an open-ended window
        and reds.
    (2) *Does the fixture reach it?*  Yes — ``None`` is passed literally for the
        absent side.
    """
    assert _declared_raises(EARLY, None) is False
    assert _declared_raises(None, EARLY) is False
    assert _declared_raises(None, None) is False


def test_declaration_allows_both_bounds_forward():
    """An upward-ordered pair (``EARLY <= LATE``) is not an inversion.

    (1) *What value makes this test fail?*  ``(EARLY, LATE)``: a predicate that
        refuses on ANY present pair (an over-fix) reds here. This is the
        positive control the inversion test needs — together they pin
        ``k_from > k_to`` rather than ``k_from != k_to``.
    (2) *Does the fixture reach it?*  Yes — both bounds are literal ISO
        instants passed straight to the declaration.
    """
    assert _declared_raises(EARLY, LATE) is False


def test_declaration_allows_unparseable_bound_because_inversion_is_undecidable():
    """An unparseable bound is not a *decidable* inversion, so it is allowed.

    ``_created_sort_key`` buckets an unparseable value as ``(1, text)`` while a
    parseable instant is ``(0, epoch)``. Refusing on that bucket would be an
    ordering-fallback artifact, not a comparison — a separate concern with its
    own guard (#5360). The honest reason this is ALLOWED is that the two sides
    are not both parseable instants, so the inversion cannot be decided.

    (1) *What value makes this test fail?*  ``("not-a-date", EARLY)``,
        ``(EARLY, "not-a-date")``, ``(LATE, "not-a-date")`` and ``("", EARLY)``:
        a predicate that compared buckets without the ``k[0] != 0`` gate raises
        on the third pair (``LATE`` is parseable, the end is not), and reds.
    (2) *Does the fixture reach it?*  Yes — the unparseable strings are passed
        literally to the declaration.
    """
    assert _declared_raises(UNPARSEABLE, EARLY) is False
    assert _declared_raises(EARLY, UNPARSEABLE) is False
    assert _declared_raises(LATE, UNPARSEABLE) is False
    assert _declared_raises("", EARLY) is False


def test_declaration_treats_zero_as_a_present_epoch_start():
    """``0`` is a PRESENT, parseable instant (epoch 0), not an absent bound.

    Presence is ``is not None``, never truthiness. ``_created_sort_key(0)``
    parses to ``(0, 0.0)``, so a window whose end is ``0`` is inverted by any
    start after the epoch and must be refused; a truthiness gate would read
    ``0`` as absent and allow it.

    (1) *What value makes this test fail?*  ``(LATE, 0)``: a predicate that
        tested ``if not valid_to: return`` treats the end as absent, does not
        raise, and reds. ``(0, EARLY)`` is the over-fix control — epoch-0 start
        precedes ``EARLY`` and must be allowed.
    (2) *Does the fixture reach it?*  Yes — the integer ``0`` is passed
        literally for the bound.
    """
    assert _declared_raises(LATE, 0) is True
    assert _declared_raises(0, EARLY) is False
    assert _declared_raises(0, 0) is False


# ── the declaration agrees with the read path's `_covers` ──────────────

_AGREEMENT_PAIRS = [
    pytest.param(LATE, EARLY, id="inverted"),
    pytest.param(EARLY, LATE, id="forward"),
    pytest.param(EARLY, EARLY, id="equal"),
    pytest.param(EARLY, None, id="open-end"),
    pytest.param(None, EARLY, id="open-start"),
    pytest.param(None, None, id="open-both"),
]
_AGREEMENT_INSTANTS = [BEFORE, EARLY, MID, LATE, AFTER]
_AGREEMENT_EXPECTED_COVERS = {
    "inverted": [],
    "forward": ["2026-06-01T00:00:00+00:00", "2026-06-05T00:00:00+00:00",
                "2026-06-10T00:00:00+00:00"],
    "equal": ["2026-06-01T00:00:00+00:00"],
    "open-end": ["2026-06-01T00:00:00+00:00", "2026-06-05T00:00:00+00:00",
                 "2026-06-10T00:00:00+00:00", "2026-07-01T00:00:00+00:00"],
    "open-start": ["2026-05-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00"],
    "open-both": ["2026-05-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00",
                  "2026-06-05T00:00:00+00:00", "2026-06-10T00:00:00+00:00",
                  "2026-07-01T00:00:00+00:00"],
}


@pytest.mark.parametrize("vf,vt", _AGREEMENT_PAIRS)
def test_declaration_agrees_with_the_read_path_covers(sdk, vf, vt, request):
    """The declaration's verdict IS the read path's coverage, measurably.

    The contract's whole justification is that a persisted inverted window
    ``[validFrom, validTo]`` (``validTo < validFrom``) covers no instant and
    so disappears from every temporal query. This test PERSISTS the pair on a
    real ``:Point`` and asks the read path (``restore_point_at`` →
    ``_covers``) which instants it resolves, then compares that against the
    declaration's verdict:

      * the declaration refuses the pair ⟹ NO instant in the grid resolves;
      * the declaration allows the pair ⟹ the grid resolves EXACTLY the
        instants inside the window (the expected set is spelled out per pair,
        so an over-fix that lets the write through but corrupts the measured
        coverage also reds).

    (1) *What value makes this test fail?*  For the inverted pair, a window
        that resolves ANY instant reds — i.e. a declaration that refuses a pair
        the read path still covers, or a read path that covers an inverted
        window. For every legal pair, a coverage set that differs from the
        expected list reds, catching a predicate whose boundary is not the read
        path's (e.g. a truthiness presence gate would drop the open-ended
        cases).
    (2) *Does the fixture reach it?*  Yes — the pair is written straight onto
        the ``:Point`` node (bypassing every writer, so this test measures the
        READ path's coverage rather than any writer's acceptance policy), and
        each grid instant is passed to ``restore_point_at``.
    """
    pair_id = request.node.callspec.id
    pid = f"pt_vw5374_agree_{pair_id}"
    row = sdk._get_proj().g.query(
        "CREATE (n:Point {id:$id, content:$content, validFrom:$vf, "
        "validTo:$vt}) RETURN n.validFrom, n.validTo",
        params={"id": pid, "content": f"vw5374 agreement {pair_id}",
                "vf": vf, "vt": vt},
    ).result_set
    assert row, f"point {pid} was not created"
    # corroborate the persisted window is the pair the guard judged
    props = _props(sdk, pid)
    assert props.get("validFrom") == vf
    assert props.get("validTo") == vt

    covered = [t for t in _AGREEMENT_INSTANTS if _covers_at(sdk, pid, t)]
    assert _declared_raises(vf, vt) is (len(covered) == 0)
    assert covered == _AGREEMENT_EXPECTED_COVERS[pair_id]


# ── the one current caller: invalidate_point delegates ─────────────────

_INVALIDATE_CASES = [
    pytest.param(LATE, EARLY, True, id="start-after-now"),
    pytest.param(EARLY, EARLY, False, id="start-equal-now"),
    pytest.param(EARLY, LATE, False, id="start-before-now"),
    pytest.param(None, EARLY, False, id="open-start"),
]


@pytest.mark.parametrize("vf,now,expected", _INVALIDATE_CASES)
def test_invalidate_point_delegates_the_verdict_to_the_declaration(
        sdk, monkeypatch, vf, now, expected):
    """``invalidate_point`` keeps its #5358 verdict through the delegation.

    ``_assert_window_start_not_inverted`` stamped ``validTo = now`` from an
    independent fact, so a predecessor whose stored ``validFrom`` is AFTER
    ``now`` would persist an inverted window and vanish from temporal reads.
    Its comparison now delegates to ``validate_validity_window``; the verdict
    must be unchanged, including the boundary cases.

    (1) *What value makes this test fail?*  ``(validFrom=LATE, now=EARLY)``: a
        delegation that swallowed the declaration's refusal (or a guard that
        stopped delegating) lets ``invalidate_point`` succeed and reds. The
        equal and before-``now`` cases red an over-fix that refuses a legal
        zero-length or ordinary window.
    (2) *Does the fixture reach it?*  Yes — ``create_point`` persists the
        predecessor ``validFrom`` verbatim, and the clock is frozen so the
        ``now`` the stamp block computes is exactly ``now``.
    """
    old = sdk.create_point(
        "statement", "vw5374 invalidate old",
        **({"validFrom": vf} if vf is not None else {}))
    repl = sdk.create_point("statement", "vw5374 invalidate repl")
    fixed = _dt.datetime.fromisoformat(now)

    class _FrozenDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    with monkeypatch.context() as m:
        m.setattr(_dt, "datetime", _FrozenDatetime)
        raised = _raised(lambda: sdk.invalidate_point(old["id"], repl["id"]))

    assert raised is expected
    if expected:
        # the refusal happens BEFORE any mutation: the window is untouched
        props = _props(sdk, old["id"])
        assert "validTo" not in props
        assert not props.get("outdated")


# ── decision pin: the projection folds replay an inverted window ───────

def _drive_upsert_point_props(sdk, vf, vt):
    payload = {"id": "pt_vw5374_upsert", "content": "x",
               "pointKind": "statement"}
    if vf is not None:
        payload["validFrom"] = vf
    if vt is not None:
        payload["validTo"] = vt
    sdk._get_proj()._upsert_point_props(payload)


def _drive_fold_point_superseded(sdk, vf, vt):
    proj = sdk._get_proj()
    pid = "pt_vw5374_foldsup"
    if vf is not None:
        proj.g.query("CREATE (n:Point {id:$id, validFrom:$vf})",
                     params={"id": pid, "vf": vf})
    else:
        proj.g.query("CREATE (n:Point {id:$id})", params={"id": pid})
    proj._fold_point_superseded(
        {"id": pid, "new_id": "pt_vw5374_foldnew", "valid_to": vt})


def _drive_fold_point_invalidated(sdk, vf, vt):
    proj = sdk._get_proj()
    pid = "pt_vw5374_foldinv"
    if vf is not None:
        proj.g.query("CREATE (n:Point {id:$id, validFrom:$vf})",
                     params={"id": pid, "vf": vf})
    else:
        proj.g.query("CREATE (n:Point {id:$id})", params={"id": pid})
    proj._fold_point_invalidated({"id": pid, "valid_to": vt})


def test_projection_folds_replay_an_inverted_window_verbatim(sdk):
    """The projection fold writers do NOT re-validate an inverted window.

    A rebuild must REPLAY windows that already exist — including ones inverted
    before a guard existed — so a fold that refused would turn a legacy
    corruption into a FAILED RESTORE at exactly the moment (restore) an
    operator can least afford it, before the repair path exists. The live
    guards reduce how many inversions reach a journal but do not eliminate all
    of them, and the folds deliberately tolerate what is already there.
    Wiring ``validate_validity_window`` into ``_upsert_point_props`` /
    ``_fold_point_superseded`` / ``_fold_point_invalidated`` is therefore NOT
    the fix; detecting and repairing already-persisted inversions is #5361's.

    This test is BEHAVIOURAL, not a code-shape assertion: it pins the
    observable contract (a fold replay of an inverted window does not raise)
    rather than the module's syntax, so a rename or split of the fold helpers
    does not red it while a re-wired guard does.

    (1) *What value makes this test fail?*  An INVERTED pair (``LATE`` start,
        ``EARLY`` end) that a fold REJECTS — wiring the declaration into any of
        the three folds makes that fold raise and reds this test.
    (2) *Does the fixture reach it?*  Yes — each fold is driven with the
        inverted value in its own replay input: the point payload for
        ``_upsert_point_props``, and the journaled ``valid_to`` against the
        node's stored ``validFrom`` for the two folds.
    """
    for name, driver in (
        ("_upsert_point_props", _drive_upsert_point_props),
        ("_fold_point_superseded", _drive_fold_point_superseded),
        ("_fold_point_invalidated", _drive_fold_point_invalidated),
    ):
        raised = _raised(lambda d=driver: d(sdk, LATE, EARLY))
        assert raised is False, (
            f"projection fold {name} re-validated the window — a legacy "
            f"journal replay must be verbatim (#5361 owns the repair path)"
        )
