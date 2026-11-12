"""Refuse a suppression or deselection MARKER on the §4.3 citation wiring gate.

`tests/test_session_schema_3554.py::test_the_citation_pin_calls_every_arm_it_declares`
certifies that the citation pin still *calls* every arm it declares, and the pin
it certifies sweeps the whole module for stale restatements. Both are gates: what
they protect is the ABSENCE of an unwiring, so a gate that does not RUN certifies
nothing while pytest reports green — the false-PASS class AGENTS.md files as
category A.

Three measured ways to silence it with ONE added line and no other trace:

1. `@pytest.mark.skip` (or `skipif`/`xfail`) on the gate — the item is collected
   and never executed. Measured on main at ``9828250732``, with every value check
   of that gate intact: `@pytest.mark.skip` plus the sweep narrowed from
   `module_source` to `doc` — exactly the unwiring the gate exists to catch, which
   reds as `1 failed, 58 passed` on its own — gave `58 passed, 1 skipped, exit 0`.
   `_SKIP = pytest.mark.skip; @_SKIP(...)` is the same mark, so a word match over
   the item's SOURCE cannot reach it; the mark is read off the collected ITEM.

2. `@pytest.mark.parametrize("monkeypatch", [])` — an empty parameter set, which
   pytest reports as a skip. Adding a case-level mark
   (`pytest.param(None, marks=pytest.mark.skip(...))`) does the same. Measured
   (pytest 9.1.1): pytest INJECTS a `skip` mark on both shapes, so the item's own
   marks read `['parametrize', 'skip']` and a mark-name read does catch them today.
   The explicit `parametrize` arms below are therefore a BELT, not the only catch:
   the injection is `empty_parameter_set_mark`'s default and could be reconfigured
   (`xfail`, `fail`) or change across pytest versions, and a gate should not depend
   on that. ⛔ What made these shapes ship green on main was not the mark read at
   all — it was matching the item's `name`, and a parametrized item is RENAMED
   `test_…[case]`, so the guarded name never matched (`refuse_suppressed_gates`
   reads `originalname`).

3. A module-level `pytestmark = pytest.mark.skip(...)`. An import-time assertion
   inside the test module cannot see it: the module namespace is still being built
   when that line runs, so a directive written BELOW the check — measured, one
   appended line — skipped all 59 items with the check passing. Reading the
   collected ITEM is immune: module-level marks are resolved by pytest after import.

Plus the selectors this repo's own CI legs filter on
(`.github/workflows/python-ci.yml` runs `-m 'not track_b and not live and not
integration'`): a marker naming one of those on the gate is written in the test
file, but it deselects the gate in the only invocation that matters, so it is
refused here too.

⛔ ITEM-LEVEL, NOT IMPORT-TIME AND NOT INSIDE THE GATE. A guard written inside the
gate is skipped with it, and a sibling witness test only moves the regress one
item over. `pytest_collection_modifyitems` runs on the collected items of the
whole session, before any marker is applied — and `tryfirst` so it sees the gate
even in the CI invocation whose `-m` expression is about to deselect it.

⛔ NOT A SECOND OWNER OF `tools/skip-guard.py`'s PREDICATE. That tool owns "which
nodeids must run in CI": it reds a nodeid MISSING from the run, and (only under
`--manifest-only`) a nodeid SKIPPED beyond a per-file budget. Under the CI
invocation it is actually called with, a collected-then-skipped nodeid is present
in the junitxml and does not red, and a `-m`-deselected one is absent from the
manifest too, because the manifest's collect-only mirrors the same expression.
This module asserts a narrower, different predicate — these two items carry no
mark that can stop or deselect them — and owns nothing about manifest coverage.

Totals covered, both halves: a MARKER (this module's collection hook) and an
OUTCOME (`refuse_unpassed` — a `pytest.skip()` in the gate body, or a fixture that
skips, carries no marker and was measured green at `62 passed, 1 skipped, exit 0`
and `63 skipped, exit 0` before it existed).

Stated residuals, each measured or mechanically argued rather than assumed:

* **Selection is the caller's.** `-k`, `--deselect`, `--lf` and `--ff` narrow the
  run before any item exists (measured on a clean tree: `-k "not
  citation_pin_calls"` → `62 passed, 1 deselected, exit 0`; `--lf` filters at
  `pytest_make_collect_report`, so the guarded file is never imported — review
  round 3's measurement, not this module's). A gate the
  caller did not select is not `required`, so the outcome half does not false-block
  a legitimate partial run — and equally, nothing here can tell a deliberate `-k`
  from a hostile one. That is the boundary of what a test can see about its own
  invocation.
* **Under `-n`** the OUTCOME half is reconciled across processes — each worker
  publishes its record through `workeroutput` and the controller merges them, since
  a worker runs a slice and the controller collects nothing (before that, measured:
  a body-skipped gate gave `-n 2` → `63 passed, 2 skipped, exit 0`). The MARKER half
  is still RED there but surfaces as an xdist INTERNALERROR (exit 3) that does not
  carry the refusal text — a traded diagnostic, not an open gate. Both pinned by
  `test_the_wiring_gate_reader_names_a_gate_suppressed_at_COLLECTION` and
  `test_the_wiring_gate_refuses_a_gate_that_never_PASSED`.
* **Deleting this module AND the hook in `conftest.py` together** is a two-place
  edit no in-tree check can see. Deleting the HOOK alone is ONE place and is
  witnessed by
  `tests/test_session_schema_3554.py::test_the_wiring_gate_hook_is_wired_into_the_suite`
  (measured: without that witness, removing just the hook left a skipped gate at
  `61 passed, 1 skipped, exit 0` with no test redding). Truncating `GUARDED_ITEMS`
  is also ONE place and is witnessed by
  `test_the_wiring_gate_guards_every_citation_gate` (measured: a truncated set plus
  a skip marker was `62 passed, 1 skipped, exit 0`).
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

# The module whose citations the pin certifies, and the two items that certify it.
GUARDED_FILE = "test_session_schema_3554.py"
GUARDED_ITEMS = frozenset({
    "test_the_citation_pin_calls_every_arm_it_declares",
    "test_the_citation_scope_docstring_states_the_measured_count",
})

# Marks that stop an item from executing: `skip` always, `skipif`/`xfail` depending
# on a runtime condition that no source read can decide.
STOPPING_MARKS = frozenset({"skip", "skipif", "xfail"})
# Marks this repo's CI legs filter on. They cannot stop an item in isolation, but
# they deselect it from the invocation that decides whether main is green.
DESELECTING_MARKS = frozenset({"integration", "live", "track_b"})
FORBIDDEN_MARKS = STOPPING_MARKS | DESELECTING_MARKS


def _name(mark: Any) -> str | None:
    return getattr(mark, "name", None)


def _parametrize_values(mark: Any) -> Iterable[Any] | None:
    """The argvalues a `parametrize` mark will expand to, or None if unknowable."""
    values = (mark.args[1] if len(mark.args) > 1
              else mark.kwargs.get("argvalues"))
    if values is None:
        return None
    try:
        len(values)
    except TypeError:
        return None
    return values


def _as_marks(values: Any) -> list[Any]:
    if values is None:
        return []
    if isinstance(values, (list, tuple, set, frozenset)):
        return list(values)
    return [values]


def _forbidden(names: Iterable[str | None]) -> list[str]:
    return sorted({name for name in names if name in FORBIDDEN_MARKS})


def refusal_reasons(marks: Iterable[Any]) -> list[str]:
    """Why these marks keep the item from running — empty when they cannot.

    ``marks`` is anything iterable of pytest marks: ``item.iter_markers()`` at
    collection time, or an explicit list. Both directions matter — a marker that
    CAN stop the item must be named (missing it leaves the gate green over a body
    nobody executed), and one that CANNOT must not be, because refusing this
    repo's `@pytest.mark.timeout` budget would be a false block denying a marker
    that is doing its job.
    """
    marks = list(marks)
    reasons: list[str] = []
    named = _forbidden(_name(mark) for mark in marks)
    if named:
        reasons.append("carries " + ", ".join(named))
    for mark in marks:
        if _name(mark) != "parametrize":
            continue
        values = _parametrize_values(mark)
        if values is not None and len(values) == 0:
            reasons.append(
                "carries an EMPTY parameter set, which pytest reports as a skip "
                "rather than a failure")
            continue
        # `parametrize(..., marks=pytest.mark.skip)` applies to EVERY case.
        declared = _forbidden(_name(extra) for extra in _as_marks(mark.kwargs.get("marks")))
        # ...and `pytest.param(..., marks=...)` applies to the case it wraps.
        per_case = sorted({
            name
            for value in (values or ())
            for name in _forbidden(_name(extra) for extra in _as_marks(
                getattr(value, "marks", None)))
        })
        suppressed = sorted(set(declared) | set(per_case))
        if suppressed:
            reasons.append(
                "suppresses a parametrized case with " + ", ".join(suppressed))
    return reasons


# ── The OUTCOME half: a marker is not the only way a gate fails to certify ────
# Refusing markers still leaves `pytest.skip()` as the FIRST STATEMENT of the gate
# body, or a fixture-level skip, green — measured (review round 3): `62 passed,
# 1 skipped, exit 0` for the body skip and `63 skipped, exit 0` for a module-level
# `monkeypatch` fixture that skips. Both are a gate that did not run, and neither
# carries a marker, so the item-level read above cannot see either.
#
# So the guarded nodeids are RECORDED at collection (after `-k`/`-m`/`--deselect`
# filtering, which is why this does not false-block a legitimate partial run) and
# each must report `call: passed`. The verdict is only raised when the session
# reports SUCCESS — a run that already red is not made noisier.
_REQUIRED: set[str] = set()
_PASSED: set[str] = set()
_OUTCOMES: dict[str, list[str]] = {}


def reset() -> None:
    """Clear the session's recorded state (a process may run several sessions)."""
    _REQUIRED.clear()
    _PASSED.clear()
    _OUTCOMES.clear()


def _is_guarded_nodeid(nodeid: str) -> bool:
    path, _, name = nodeid.partition("::")
    return Path(path).name == GUARDED_FILE and name.split("[")[0] in GUARDED_ITEMS


def note_required(items: Iterable[Any]) -> None:
    """Record which guarded gates THIS session collected (post-deselection)."""
    for item in items:
        name = getattr(item, "originalname", None) or getattr(item, "name", "")
        path = getattr(item, "path", None)
        if path is None:
            path = getattr(item, "fspath", None)
        if name not in GUARDED_ITEMS or Path(str(path)).name != GUARDED_FILE:
            continue
        _REQUIRED.add(item.nodeid)


def note_report(report: Any) -> None:
    """Record how a guarded gate actually reported."""
    nodeid = getattr(report, "nodeid", "")
    if nodeid not in _REQUIRED:
        return
    if getattr(report, "when", None) == "call" and getattr(report, "passed", False):
        _PASSED.add(nodeid)
        return
    if not getattr(report, "passed", False):
        _OUTCOMES.setdefault(nodeid, []).append(
            f"{report.when}:{report.outcome}")


def _payload() -> dict[str, Any]:
    """This process's record, in a form xdist can ship to the controller."""
    return {
        "required": sorted(_REQUIRED),
        "passed": sorted(_PASSED),
        "outcomes": {nodeid: list(phases) for nodeid, phases in _OUTCOMES.items()},
    }


def merge_worker(payload: Any) -> None:
    """Fold a worker's record into the controller's (xdist collects on the workers)."""
    if not isinstance(payload, dict):
        return
    _REQUIRED.update(payload.get("required") or ())
    _PASSED.update(payload.get("passed") or ())
    for nodeid, phases in (payload.get("outcomes") or {}).items():
        _OUTCOMES.setdefault(nodeid, []).extend(phases)


def publish_worker_record(config: Any) -> None:
    """Hand this worker's record to the controller through `workeroutput`.

    ⛔ THE OUTCOME HALF MUST SURVIVE `-n`, and this is why: each worker collects the
    whole item set and runs a SLICE of it, and the controller collects nothing, so
    neither side alone can answer "did the gate PASS". Measured (review round 4)
    with the gate's body skipped: `-n 2` gave `63 passed, 2 skipped, exit 0` — a
    green CI run for the exact shape this half exists to catch, because the worker
    refused to decide and the controller had no items. Each worker now publishes its
    record, the controller merges them (`merge_worker`), and only the controller
    decides.
    """
    workeroutput = getattr(config, "workeroutput", None)
    if isinstance(workeroutput, dict):
        workeroutput["wiring_gate_marks"] = _payload()


# Options that collect or INSPECT without running: no call phase can exist, so the
# outcome question is not merely unanswered — it is not being asked. ⛔ THIS IS A
# BAIL, not a courtesy: without it, `--collect-only` turned green into RED and broke
# `tools/skip-guard.py --emit-manifest`, which runs `pytest --collect-only` and fails
# closed on any rc other than 0/5 — i.e. it would have failed the coverage-manifest
# step of `.github/workflows/python-ci.yml`, `post-merge-validation.yml`,
# `ci.yml`'s collection check and `ci-timing.yml` (measured, review round 4).
_NON_RUNNING_OPTIONS = ("collectonly", "setuponly", "setupplan", "showfixtures")


def refuse_unpassed(config: Any, exitstatus: int) -> int:
    """Return the session's exit status, red if a guarded gate did not PASS.

    ⛔ REPORTED ONLY WHEN THE SESSION IS OTHERWISE GREEN, and that is the point: a
    run that already failed is not made noisier, and the failure this catches is
    precisely "the suite reports green while the gate certified nothing".

    ⛔ NOT UNDER xdist (`workerinput`): each worker collects the whole item set but
    runs a SLICE of it, so a gate that another worker ran would read as unrun here.
    The controller receives the reports but does not collect, so it has no required
    set. Under `-n` this half is therefore INERT rather than wrong — a stated
    residual, not a silent one; the marker half still reds there.
    """
    if hasattr(config, "workerinput"):
        publish_worker_record(config)
        return exitstatus
    option = getattr(config, "option", None)
    if any(getattr(option, name, False) for name in _NON_RUNNING_OPTIONS):
        return exitstatus
    if exitstatus != 0:
        return exitstatus
    missing = {
        nodeid: _OUTCOMES.get(nodeid, ["never reported"])
        for nodeid in sorted(_REQUIRED - _PASSED)}
    if not missing:
        return exitstatus
    detail = "\n".join(
        f"  {nodeid}\n    " + ", ".join(outcomes)
        for nodeid, outcomes in missing.items())
    print(
        "\n❌ a §4.3 citation WIRING GATE did not PASS, so the run is green only "
        "because the gate certified nothing:\n"
        f"{detail}\n"
        "A `pytest.skip()` in the gate body or a skipping fixture is not a marker, "
        "so the collection-time guard cannot see it; this outcome check is what "
        "does. Drop the skip (and fix whatever made it necessary).",
        file=sys.stderr)
    return 1


def refuse_suppressed_gates(items: Iterable[Any]) -> None:
    """Fail collection if a guarded gate carries a mark that can stop it running.

    Raises ``pytest.UsageError`` rather than skipping or xfailing the item: a
    deselection is the failure mode being refused, so the run must be REMEMBERED
    as red, not quietly narrowed.
    """
    offenders: dict[str, list[str]] = {}
    for item in items:
        path = getattr(item, "path", None)
        if path is None:
            path = getattr(item, "fspath", None)
        if path is None or Path(str(path)).name != GUARDED_FILE:
            continue
        # ⛔ `originalname`, NOT `name`: a parametrized item is renamed
        # `test_…[case]`, so a `name` match is BLIND to exactly the suppression shape
        # this guard exists to refuse (`parametrize(…, [])` and a skip-marked
        # `pytest.param`) — measured: 61 passed, 1 skipped, green, with a `name` match.
        if getattr(item, "originalname", None) not in GUARDED_ITEMS and \
                getattr(item, "name", None) not in GUARDED_ITEMS:
            continue
        reasons = refusal_reasons(item.iter_markers())
        if reasons:
            offenders[item.nodeid] = reasons
    if not offenders:
        return
    detail = "\n".join(
        f"  {nodeid}\n    " + "\n    ".join(reasons)
        for nodeid, reasons in sorted(offenders.items()))
    raise pytest.UsageError(
        "a §4.3 citation WIRING GATE carries a marker that can stop it running, so "
        "it would certify nothing while the suite reports green:\n"
        f"{detail}\n"
        "Drop the marker (and fix whatever made it necessary). Command-line "
        "deselection (-k, --deselect) stays out of a test's reach; a marker in the "
        "test file does not have to be")
