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
    optional extra imported inside a test or a fixture (`pytest.importorskip`
    there, a missing HF model, a platform guard) is untouched by design.

A WHOLE-MODULE ABORT IS A THIRD ENFORCEMENT POINT, one phase EARLIER. The
module-level `pytest.skip(..., allow_module_level=True)` gate in the
`tests/*.py` live files — and pytest's own `pytest.importorskip(...)`, which
raises the same whole-module skip — is decided during COLLECTION, so no item
ever exists and the mark hook above cannot see it — pytest's `wrap_session`
returns NO_TESTS_COLLECTED/OK for an all-skipped selection (#1436's class). CI reds it through the junitxml matcher's
`is_collection_skip_violation`, so an in-process gate that ignored it would leave
exactly the CI/lane asymmetry this module exists to remove. `collection_violation`
applies that SAME guard function to pytest's `CollectReport`, from the same two
halves the junit matcher reads (`_pytest/junitxml.py` writes the constant message
"collection skipped" plus `str(report.longrepr)` as the detail), so the two
enforcement points apply ONE verdict to the same two halves.

ONE LANE ARM IS DELIBERATELY WIDER HERE, AND IT IS NAMED RATHER THAN CLAIMED
CLOSED. `lane_declaration()` folds `TORTOISE_TEST_CARVE_OUT` into `False`
(a URI-less declaration — the same opt-in `_p4_uri_required` demands), while
the guard's own `lane_expects_live_uri()` reads only `TORTOISE_TEST_EXPECT_URI`.
So on CI's `test-carve-out` leg (`python-ci.yml:2611` sets the carve-out flag
and NOT the expectation, and its matcher at `:2818` runs WITHOUT
`--manifest-only`) the log matcher sees lane `None` and would hold an
availability skip against the run, where this gate exempts it. That asymmetry
is deliberate on this side, and it is CREATED here (the guard's derivation and
that leg's matcher are what pre-exist): epic #1647 P4 records the carve-out
lane's live skips as its shape, so the in-process verdict is the correct one
and re-pointing the guard's lane derivation is a change to the #1436/#3592
calibration this module must not make on its own initiative. It is
latent — that shard's selection produces no FalkorDB-availability skip today —
but it is a real difference, so it is recorded here instead of being covered by
a blanket "the two points cannot disagree".

THE ARM'S SCOPE IS NOT ITS VERDICT, and three exemptions keep it inside the
class it exists for. Each mirrors a decision recorded OUTSIDE this module — a
verdict function cannot supply them, because they are statements about which
skips this gate is about, not about whether a skip is a violation:

  * a lane that DECLARED itself URI-less gets no verdict at all, because CI runs
    no skip matcher on that lane: the URI-unset `test-d14-hosted-api` leg passes
    `--manifest-only` (`python-ci.yml:3047-3052`) with its own reason — "this
    lane's skips are the expected state (URI unset), so the docker-calibrated
    skip matchers would false-red every e2e module (24, measured)". The lane
    question is the same `lane_declaration()` the reason arm already uses, so
    the two arms are exempted by one rule.
  * `tests/e2e/**` is not selectable: "e2e is exempt from `integrity()` (#1349)
    and is not selectable" (`tests/test_uri_requiring_selection.py`), and no
    python-matrix leg collects it. Its twelve opt-in gates (`RUN_DASHBOARD_E2E`,
    `RUN_LEGAL_E2E`, `ALLOW_PROD`, `RUN_HOSTED_E2E`) abort collection BY DESIGN
    and name no URI because they are not live-storage gates at all. CI's matcher
    never sees them; an in-process arm that did would red the documented local
    run (`uv run pytest tests/ -v`) on evidence CI deliberately excludes.
  * pytest's own `pytest.importorskip(...)` abort is the optional-extra class,
    which "must remain a skip in a lane" (the same recorded reason the guard's
    third class, `is_embedder_reason_violation`, is deliberately CI-only).
    pytest builds BOTH of its whole-module reasons in `_pytest/outcomes.py` at
    the pinned 9.1.1 — `f"could not import {modname!r}: {exc}"` for an absent
    module, and `f"module {modname!r} has __version__ {verattr!r}, required is:
    {minversion!r}"` for an `importorskip(..., minversion=...)` shortfall, the
    second also with `allow_module_level=True`. This arm — which sees the detail
    the skip was raised with — can tell either shape from a live-storage abort,
    where the junit matcher cannot. Applying the matcher's verdict here would
    manufacture a false red the repository promises not to produce (and a
    wrong one: the remedy printed would send a reader looking for a database).

DECLARED RESIDUALS of the collection arm, neither silently covered:
  * the exemption above matches pytest's SHAPE for an `importorskip` abort —
    both of its reason spellings — not its cause. `importorskip(...,
    reason="…")` replaces that shape, so a custom-reason abort still reaches the
    verdict; a hand-written reason quoting `module 'x' has __version__ …` is
    exempted by the `minversion` spelling; and an `importorskip` on a module that
    IS live storage is exempted with the rest of the shape. Narrowing further
    would need the ORIGIN of the skip, which pytest does not report on a
    `CollectReport`. The `minversion` half is DEFENSIVE: it is pytest's real
    shape at the pinned 9.1.1, but no module in this tree calls
    `importorskip(..., minversion=...)` today, so unlike the other two
    exemptions it is not backed by a measured population.
  * a live-storage skip raised from a test BODY or a fixture at RUNTIME is still
    a plain skip here. The measured population is not "optional extras": e.g.
    `tests/test_cross_lens.py:392` skips `live FalkorDB (localhost:1) not
    reachable` and the run exits 0, while `is_falkor_reason_violation` calls the
    same string a violation. The invariant this module delivers is therefore
    scoped exactly as its enforcement points are — MARK-decided and
    COLLECTION-decided skips — and the runtime class is the reason CI still
    carries a post-hoc junit matcher.

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
import re
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

#: Subtrees whose whole-module aborts are NOT this gate's class: they are not
#: selectable, so no CI skip matcher is ever run against them (#1349 in
#: `tests/test_uri_requiring_selection.py`; `python-ci.yml:3047-3052` names the
#: 24 opt-in e2e gates the matchers would false-red). The PREFIX is only the
#: fallback spelling: `CollectReport.nodeid` is ROOTDIR-relative, so a run that
#: moves the rootdir (`--rootdir`, `-c` from another tree, an installed layout)
#: spells the same module without the `tests/` prefix — `_out_of_scope` decides
#: from the report's own `(path, lineno, message)` tuple, so the exemption is a
#: property of the tree rather than of one invocation.
_NON_SELECTABLE_PREFIXES = ("tests/e2e/",)
_E2E_ROOT = (_ROOT / "tests" / "e2e").resolve()

#: pytest's own `pytest.importorskip(...)` whole-module skips — BOTH shapes, and
#: both are the optional-extra class that "must remain a skip in a lane":
#:   * an absent module — `could not import '<mod>': <exc>`;
#:   * a version shortfall — `module '<mod>' has __version__ <v>, required is:
#:     <min>` (`_pytest/outcomes.py:281-285` in the pinned 9.1.1 raises it with
#:     `allow_module_level=True`, so it is decided at COLLECTION like the first).
#: The quoted module name plus the `: ` / ` has __version__ ` that carries the
#: reason keep this anchored to pytest's shape: a hand-written reason that merely
#: MENTIONS an import is not exempt.
_IMPORTORSKIP_DETAIL_RES = (
    re.compile(r"could not import (?P<q>['\"])[^'\"]+(?P=q): "),
    re.compile(r"module (?P<q>['\"])[^'\"]+(?P=q) has __version__ "),
)


def _is_importorskip_abort(detail: str) -> bool:
    """True when `detail` is pytest's own `importorskip` abort — either shape."""
    return any(rx.search(detail) for rx in _IMPORTORSKIP_DETAIL_RES)


def _report_file(report: pytest.CollectReport) -> Path | None:
    """The FILE a collection report is about, or None when it carries none.

    `CollectReport.fspath` — and its alias `location[0]` — cannot be used for
    this: both are READ-ONLY PROPERTIES returning `self.nodeid.split("::")[0]`
    (`_pytest/reports.py:161-165`, `:498-502` in the pinned 9.1.1), i.e. the
    ROOTDIR-RELATIVE spelling again, never a filesystem path (and not
    assignable: the property has no setter). The real path is the first field
    of the tuple `pytest_make_collect_report` builds for a module-level skip —
    `(str(r.path), r.lineno, r.message)` (`_pytest/runner.py:419-425`).
    """
    longrepr = getattr(report, "longrepr", None)
    if not isinstance(longrepr, (tuple, list)) or not longrepr:
        return None
    first = longrepr[0]
    if not isinstance(first, str) or not first.strip():
        return None
    path = Path(first)
    # A relative spelling is what hand-built reports in this suite pass; pytest
    # itself always writes an absolute `str(r.path)`. Anchor a relative one at
    # the repo root, which is the directory a nodeid is relative TO.
    return path if path.is_absolute() else _ROOT / path


def _out_of_scope(report: pytest.CollectReport) -> bool:
    """True when this module is not part of the gate's class — see the module
    docstring's scope paragraph.

    The NODEID is checked first because that is the spelling every CI leg and
    the documented `pytest tests/` run produce. It is rootdir-relative, though,
    so the report's FILE is the authority when it carries one — otherwise a run
    that relocated the rootdir (`--rootdir`, `-c` from another tree) would lose
    the `tests/e2e/**` exemption and turn a documented opt-in gate into a
    collection error naming a FalkorDB remedy.
    """
    nodeid = str(getattr(report, "nodeid", "") or "")
    if nodeid.replace("\\", "/").startswith(_NON_SELECTABLE_PREFIXES):
        return True
    path = _report_file(report)
    if path is None:  # pragma: no cover - a whole-module skip carries the tuple
        return False
    try:
        return path.resolve().is_relative_to(_E2E_ROOT)
    except (OSError, ValueError):  # pragma: no cover - unreadable/odd path
        return False


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

    Three SCOPE exemptions run before the verdict — a declared URI-less lane
    (mirroring CI's `--manifest-only`, `python-ci.yml:3047-3052`), a
    non-selectable subtree (`tests/e2e/**`), and pytest's `importorskip` shape
    (the optional-extra class that stays a skip in a lane). They are statements
    about which skips this gate is about, not a second verdict; the module
    docstring's scope paragraph is where each one is grounded.

    A report that is not a skip (a genuine collection ERROR, or a collected
    module) is never this class: an error already fails the run, and the guard
    keys on the structural skip signal.
    """
    if getattr(report, "outcome", None) != "skipped":
        return None
    nodeid = str(getattr(report, "nodeid", "") or "")
    if _out_of_scope(report):
        return None
    lane = lane_declaration()
    if lane is False:
        # Declared URI-less: CI runs its skip matchers with `--manifest-only`
        # here, so a verdict from this arm would be STRICTER than the policy it
        # mirrors — a false red on every module-level opt-in gate.
        return None
    detail = str(getattr(report, "longrepr", "") or "")
    if _is_importorskip_abort(detail):
        # An absent (or too old) optional extra: a legitimate skip in every
        # lane, in BOTH of pytest's `importorskip` abort shapes.
        return None
    if not _GUARD.is_collection_skip_violation(
            _COLLECTION_SKIP_MESSAGE, detail, lane=lane):
        return None
    return _collection_message(nodeid or "<unknown>", detail)


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
        "decides a module-level skip during COLLECTION, so no test item exists, "
        "no item-level hook and no session fixture ever runs, and the run "
        "reports `N skipped` with rc=0.\n"
        f"  module: {nodeid}\n"
        f"  report: {detail}\n"
        "Give the run a database (a private throwaway container is the "
        "preferred shape, #5084):\n"
        "  eval \"$(uv run python tools/test_lane.py uri)\"\n"
        "Or opt this run into the embedded/carve-out lane explicitly (the only "
        "lane whose whole-module URI gates are legitimate):\n"
        "  TORTOISE_TEST_CARVE_OUT=1 uv run python -m pytest <selection>"
    )
