"""In-process fail-closed gate for live-storage skips (#1436, #5084 remainder).

THE DEFECT (measured on this box, 2026-10-25). Every live-FalkorDB file in the
suite guards itself with a module-level probe plus

    pytestmark = pytest.mark.skipif(
        not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")

On a host with no usable server that is a SILENT GREEN: `tests/test_ep_directional.py`
reported "14 skipped in 2.50s" with **rc=0**, so a suite that says
`N passed, k skipped` claimed nothing about the live behaviour it exists to
verify. That is #1436 broken inside the suite — a skipped check standing in for
an unread one (#1436 measured this exact file: "72 skipped", CI 'green' masking
EP regressions) — and it is what made "verified by tests" possible tonight for
a P0 whose acceptance criteria require a database.

The reason string is also not the truth it appears to be. The same run's probe
failed because the shared instance was PRESENT and REFUSING WRITES (its
`maxmemory` ceiling was reached: `DB refused writes on open: the graph is INTACT
but the server has reached its memory ceiling (used_memory 2.0 GiB of maxmemory
2.0 GiB)`), and the probe's `except Exception` collapses that into "not
available" — the same string a genuinely absent server produces.

WHY A PRETEST HOOK AND NOT THE EXISTING GATE. `tests/conftest.py::_p4_uri_required`
already fails a URI-less run without the carve-out, but it is a SESSION FIXTURE,
and pytest resolves `skipif` marks in `pytest_runtest_setup` — the skipping
plugin's tryfirst hook — BEFORE any fixture is set up. A module whose every test
is mark-skipped therefore never reaches that gate. Measured, same interpreter,
same missing URI: `tests/test_canary_classify.py` (hermetic, nothing skipped)
gives 18 failures, while `tests/test_ep_directional.py` (every test mark-skipped)
gives "14 skipped", rc=0. The enforcement has to sit where the skip is DECIDED.

WHY THE VERDICT IS `tools/skip-guard.py`'s AND NOT A SECOND COPY.
`is_falkor_reason_violation` is the RECORDED policy for exactly this question —
#1436 (availability regressions are violations), #3592 (the lane declaration
decides, because a reason string cannot) and epic #1647 — and CI applies it
post-hoc to the pytest log. Two copies of one policy drift, and the drift is
invisible in the worst direction: a file whose reason matched one matcher and
not the other would be RED in CI and GREEN in a lane. So this module LOADS that
function by path (the hyphen in the filename makes it unimportable, the same
idiom as `tests/test_ci_expected_manifests.py::_load_guard`) and applies it in
process: one verdict function, two enforcement points.

THE LANE DECLARATION, and what stays a legitimate skip:
  * the lane's own `TORTOISE_TEST_EXPECT_URI` decides first. UNDECLARED (a local
    run, the reported case) keeps the historical calibration, which is why the
    availability family is a violation here: nobody declared that this run may
    skip the live half. A PROMISE is never excused — see `lane_declaration`.
  * `TORTOISE_TEST_CARVE_OUT` (truthy) fills the UNDECLARED arm as
    lane=``False`` ("this lane declared itself URI-less") — it is already what
    `_p4_uri_required` demands before a URI-less session may run at all, so it
    is the same declaration, not a second switch. The embedded/carve-out lane
    therefore keeps working. It does NOT override an explicit promise.
  * the "requires TORTOISE_DB_URI…" family is a claim ABOUT the URI, so it is
    exempt on an undeclared or URI-less lane and a violation only on a lane that
    PROMISED the URI (the #3592 false PASS).
  * design classes no lane can satisfy (the permanent legacy `localhost:6399`
    skip, the embedded-lane "FalkorDBLite unavailable" precondition) stay
    exempt in every lane — that is the guard's own list, not a decision made here.
  * skips raised at RUNTIME never reach this gate (it reads marks), so an
    optional extra (`pytest.importorskip`, a missing HF model, a platform guard)
    is untouched by design.

A WHOLE-MODULE ABORT IS A THIRD ENFORCEMENT POINT, one phase EARLIER. The
`pytest.skip(..., allow_module_level=True)` gate in twelve `tests/*.py` files is
decided during COLLECTION, so no item ever exists and the mark hook above cannot
see it — pytest's `wrap_session` returns NO_TESTS_COLLECTED/OK for an all-skipped
selection (#1436's class). CI reds it through the junitxml matcher's
`is_collection_skip_violation`, so an in-process gate that ignored it would leave
exactly the CI/lane asymmetry this module exists to remove. `collection_violation`
applies that SAME guard function to pytest's `CollectReport`, from the same two
halves the junit matcher reads (`_pytest/junitxml.py` writes the constant message
"collection skipped" plus `str(report.longrepr)` as the detail), so the two
enforcement points still cannot disagree.

WHAT THIS GATE CANNOT SEE (declared, because a silence about it is
indistinguishable from coverage). It applies the guard's REASON families, so it
inherits their boundary: a live-storage skip phrased outside them — e.g.
"needs a live graph (TORTOISE_DB_URI) — the fake-store tests cover the CAS
logic", which names no backend — remains a SKIP here and in CI. Widening the
families is a policy change to `tools/skip-guard.py` (the recorded #1436/#3592
calibration), not something this module may do on its own initiative. Likewise
it says nothing about a live test that PASSES against the wrong backend; that
is `tests/conftest.py::_assert_backend_identity`'s job.

The guard's THIRD class — `is_embedder_reason_violation` — is deliberately NOT
applied in process, in either hook. A missing optional extra (no HF model, no
provider key) is a legitimate skip in a lane and the decision table below pins
those reasons as non-violations; CI keeps the verdict for the surface that
no longer carries the extra.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_GUARD_PATH = _ROOT / "tools" / "skip-guard.py"


def _load_guard() -> Any:
    """Load `tools/skip-guard.py` by path — see the module docstring."""
    spec = importlib.util.spec_from_file_location("_skip_guard_for_live_gate",
                                                  _GUARD_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - broken checkout
        raise RuntimeError(f"cannot load {_GUARD_PATH} — the live-storage gate "
                           f"has no verdict function and must not silently "
                           f"become a no-op")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GUARD = _load_guard()


def lane_declaration() -> bool | None:
    """The run's declared live-URI expectation (True / False / None).

    Reads the environment at CALL time so a test can pin the arm. The carve-out
    is folded in as an explicit URI-less declaration — it is the same opt-in
    `tests/conftest.py::_p4_uri_required` already requires before a URI-less
    session may run at all, so treating it as "declared URI-less" keeps ONE
    declaration per run instead of inventing a second.
    """
    from tests._embedded import _carve_out_opted_in

    lane = _GUARD.lane_expects_live_uri()
    # An explicit PROMISE is never excused: a lane that declared
    # TORTOISE_TEST_EXPECT_URI=1 has said its live half must run, and the
    # carve-out flag is the weaker statement, so it may not override it. The
    # carve-out only fills in the arm nobody declared (the local-run shape).
    if lane is None and _carve_out_opted_in():
        return False
    return lane


def pending_skip_reason(item: pytest.Item) -> str | None:
    """The reason pytest is ABOUT to skip `item` for, or None.

    `evaluate_skip_marks` is pytest's own resolution of `skip`/`skipif` marks —
    including `condition` callables and string conditions — so this gate sees
    exactly the skips pytest would perform and cannot disagree about which mark
    fires. Private API on purpose: re-deriving the condition from the mark's
    kwargs is how a `skipif("some_expr")` would be mis-read as unconditionally
    true. `tests/test_live_storage_fail_closed.py` pins its presence, so a
    pytest upgrade that removes it fails that test instead of silently
    disarming every live test.
    """
    from _pytest.skipping import evaluate_skip_marks

    skip = evaluate_skip_marks(item)
    if skip is None:
        return None
    reason = getattr(skip, "reason", "")
    return reason if isinstance(reason, str) else str(reason)


#: pytest's CONSTANT `message` attribute on a whole-module collection abort —
#: the reason rides in the element TEXT / the report's longrepr instead. Verified
#: against the pinned pytest (9.1.1): `_pytest/junitxml.py` writes
#: `self._add_simple("skipped", "collection skipped", str(report.longrepr))`, and
#: `_pytest/runner.py::pytest_make_collect_report` builds that `str(report.longrepr)`
#: from `(str(path), lineno, message)` — the two halves `is_collection_skip_violation`
#: keys on. Kept here (not read from the guard) because it is pytest's contract,
#: not the guard's, and `tests/test_live_storage_fail_closed.py` pins it.
_COLLECTION_SKIP_MESSAGE = "collection skipped"


def violation(item: pytest.Item) -> str | None:
    """The fail-closed message for `item`, or None when its skip is legitimate."""
    reason = pending_skip_reason(item)
    if not reason:
        return None
    if not _GUARD.is_falkor_reason_violation(reason, lane=lane_declaration()):
        return None
    return _message(item.nodeid, reason)


def refuse_live_skip(item: pytest.Item) -> None:
    """Fail `item` instead of letting it skip without live storage."""
    message = violation(item)
    if message is not None:
        pytest.fail(message, pytrace=False)


def collection_violation(report: pytest.CollectReport) -> str | None:
    """The fail-closed message for a whole-module abort, or None.

    Reads the SAME two halves the junitxml matcher reads, from the report itself:
    the constant `collection skipped` message pytest stamps on such a report, and
    `str(report.longrepr)` — the `(path, lineno, message)` tuple that carries the
    real reason. Passing them through `is_collection_skip_violation` keeps ONE
    policy for the question, so the in-process and CI verdicts cannot drift.

    A report that is not a skip (a genuine collection ERROR, or a collected
    module) is never this class: an error already fails the run, and the guard
    keys on the structural skip signal.
    """
    if getattr(report, "outcome", None) != "skipped":
        return None
    detail = str(getattr(report, "longrepr", "") or "")
    if not _GUARD.is_collection_skip_violation(
            _COLLECTION_SKIP_MESSAGE, detail, lane=lane_declaration()):
        return None
    return _collection_message(str(getattr(report, "nodeid", "<unknown>")),
                               detail)


def _message(nodeid: str, reason: str) -> str:
    return (
        "live storage is REQUIRED by this test and this run could not provide "
        "one, so it would SKIP — refusing that (#1436): a skipped check is an "
        "unread one, and a suite reporting `N passed, k skipped` reads as green "
        "whether or not the live behaviour ever ran.\n"
        f"  test:   {nodeid}\n"
        f"  reason: {reason!r}\n"
        "The probe that produced that reason catches EVERY failure, so it does "
        "not establish that no server exists: a server that is PRESENT but "
        "refusing writes (measured on this box — the shared instance reached "
        "its 2 GiB maxmemory ceiling, so opening it raised `DB refused writes "
        "on open`) reports the same 'not available' string as an absent one.\n"
        "Give the run a database (a private throwaway container is the "
        "preferred shape, #5084):\n"
        "  eval \"$(uv run python tools/test_lane.py uri)\"\n"
        "Or opt this run into the embedded/carve-out lane explicitly (the only "
        "lane whose live-storage skips are legitimate):\n"
        "  TORTOISE_TEST_CARVE_OUT=1 uv run python -m pytest <selection>"
    )


def _collection_message(nodeid: str, detail: str) -> str:
    return (
        "live storage is REQUIRED by this MODULE and this run could not provide "
        "one, so its whole module would SKIP — refusing that (#1436): pytest "
        "decides `pytest.skip(..., allow_module_level=True)` during COLLECTION, "
        "so no test item exists, no item-level hook and no session fixture ever "
        "runs, and the run reports `N skipped` with rc=0.\n"
        f"  module: {nodeid}\n"
        f"  report: {detail}\n"
        "Give the run a database (a private throwaway container is the "
        "preferred shape, #5084):\n"
        "  eval \"$(uv run python tools/test_lane.py uri)\"\n"
        "Or opt this run into the embedded/carve-out lane explicitly (the only "
        "lane whose whole-module URI gates are legitimate):\n"
        "  TORTOISE_TEST_CARVE_OUT=1 uv run python -m pytest <selection>"
    )
