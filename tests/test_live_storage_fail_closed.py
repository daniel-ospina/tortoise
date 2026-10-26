"""The in-process live-storage gate: a live test FAILS, it does not skip (#1436).

Posture, and the reasoning behind it, live in `tests/_live_gate.py`. These tests
pin the two things that could quietly undo it:

  * the DECISION TABLE — which skip reasons are violations and which are
    legitimate (the carve-out lane, the permanent design classes, the deliberate
    URI gate, and every non-live optional-extra skip). A gate that reds those
    would be worse than the hole it closes: it would make the embedded lane
    un-runnable and turn a missing optional extra into a false red;
  * BOTH END-TO-END DIRECTIONS on a real live file, because only one of them is
    the fix. The failing direction (no database) is the reported defect; the
    carve-out direction proves the legitimate lane still works. A gate that
    proved only the failing direction would let the carve-out die silently.
  * the COLLECTION class (`pytest.skip(..., allow_module_level=True)`), which
    pytest decides one phase EARLIER than a mark: no item exists, so the mark
    hook and the `_p4_uri_required` fixture are both unreachable and a mixed
    selection exits 0 with the module counted as a skip. That is the class CI's
    junitxml matcher already reds, so it is pinned here in both directions too.

The "run WITH a private container executes the live tests" direction needs
Docker, so it is not automated here: it is proved by
`eval "$(uv run python tools/test_lane.py uri)"` and a run of the live file
(measured at the PR head: 14 passed in 203.38 s, against 14 skipped before the
seam and the gate existed).

Deliberately no Docker in this file either: the failing direction is made
deterministic by aiming the lane probe at a CLOSED port
(`TORTOISE_TEST_DOCKER_PORT=1`) rather than by hoping no server is up — on this
box the shared instance is up and refusing writes, which is exactly the case
that used to skip.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import _live_gate

ROOT = Path(__file__).resolve().parents[1]
#: 14 tests, every one of them mark-skipped by the module-level availability
#: probe — the file that reported "14 skipped in 2.50s", rc=0, in the report.
LIVE_FILE = "tests/test_ep_directional.py"
#: A module-level `pytest.skip(..., allow_module_level=True)` gate: pytest aborts
#: it during COLLECTION, so no item exists for the mark hook to see.
COLLECTION_FILE = "tests/test_capabilities_endpoint.py"
#: One hermetic sibling, so the selection still COLLECTS an item. The MIXED
#: shape is the false PASS: before the collection half of the gate the same
#: selection exited 0 reporting the aborted module as a skip.
COLLECTED_WITNESS = ("tests/test_live_storage_fail_closed.py::"
                     "test_the_pytest_api_the_gate_depends_on_still_exists")


class _FakeItem:
    """Just enough Item for `pytest`'s own skip resolution (bool conditions)."""

    nodeid = "tests/test_x.py::test_y"

    def __init__(self, marks):
        self._marks = list(marks)

    def iter_markers(self, name=None):
        for mark in self._marks:
            if name is None or mark.name == name:
                yield mark


def _skipif(reason: str, condition: bool = True) -> object:
    return pytest.mark.skipif(condition, reason=reason).mark


@pytest.fixture(autouse=True)
def _clear_lane_env(monkeypatch):
    """Every arm starts UNDECLARED — the local-run shape the gate exists for."""
    for name in ("TORTOISE_TEST_EXPECT_URI", "EXPECT_URI",
                 "TORTOISE_TEST_CARVE_OUT"):
        monkeypatch.delenv(name, raising=False)


def test_the_pytest_api_the_gate_depends_on_still_exists():
    """The gate resolves skips with pytest's own `evaluate_skip_marks`.

    Pinned so a pytest upgrade that removes or reshapes it fails HERE, loudly,
    instead of disarming every live test in the suite (a `try/except ImportError`
    fallback would be the silent version of the same failure).
    """
    from _pytest.skipping import Skip, evaluate_skip_marks

    assert callable(evaluate_skip_marks)
    assert Skip("x").reason == "x"
    # The collection class's signal is pytest's CONSTANT message attribute — the
    # value `_pytest/junitxml.py` stamps on a whole-module abort
    # (`_add_simple("skipped", "collection skipped", str(report.longrepr))`),
    # and the only half available in process. Pinned so a rename cannot quietly
    # turn `collection_violation` into a no-op for every module.
    assert _live_gate._COLLECTION_SKIP_MESSAGE == "collection skipped"


def test_the_gate_reads_the_reason_pytest_would_skip_with():
    item = _FakeItem([_skipif("Live FalkorDB (Docker) not available")])
    assert _live_gate.pending_skip_reason(item) == \
        "Live FalkorDB (Docker) not available"


def test_a_false_condition_is_not_a_pending_skip():
    """A mark that will NOT skip must never be read as a live-storage failure."""
    item = _FakeItem([_skipif("Live FalkorDB (Docker) not available",
                             condition=False)])
    assert _live_gate.pending_skip_reason(item) is None
    assert _live_gate.violation(item) is None


@pytest.mark.parametrize(
    "reason, env, violation",
    [
        # The reported defect: an undeclared local run has no database and did
        # not say it may run without one.
        ("Live FalkorDB (Docker) not available", {}, True),
        ("FalkorDB not available", {}, True),
        # The declared embedded/carve-out lane: its live skips are its shape.
        ("Live FalkorDB (Docker) not available",
         {"TORTOISE_TEST_CARVE_OUT": "1"}, False),
        # A lane that declared itself URI-less (#3592's exemption).
        ("Live FalkorDB (Docker) not available",
         {"TORTOISE_TEST_EXPECT_URI": "0"}, False),
        # A lane that PROMISED the URI and skipped anyway: the #3592 false PASS.
        ("Live FalkorDB (Docker) not available",
         {"TORTOISE_TEST_EXPECT_URI": "1"}, True),
        # Design classes no lane can satisfy — exempt everywhere.
        ("Live FalkorDB server on localhost:6399 not available", {}, False),
        ("embedded FalkorDBLite unavailable", {}, False),
        # The deliberate URI gate: a claim ABOUT the URI, exempt unless the
        # lane promised one.
        ("requires TORTOISE_DB_URI (live FalkorDB lane)", {}, False),
        ("requires TORTOISE_DB_URI (live FalkorDB lane)",
         {"TORTOISE_TEST_EXPECT_URI": "1"}, True),
        # NOT a live-storage skip: an optional extra must stay a skip.
        ("no LLM provider key", {}, False),
        ("all-MiniLM-L6-v2 not in HF cache (HF_HUB_OFFLINE in CI)", {}, False),
        ("no os.register_at_fork on this platform", {}, False),
    ],
)
def test_verdict_table(reason, env, violation, monkeypatch):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    item = _FakeItem([_skipif(reason)])
    assert (_live_gate.violation(item) is not None) is violation


def test_carve_out_is_a_uri_less_declaration_not_a_second_switch(monkeypatch):
    """One declaration per run: the carve-out IS the "no live URI" statement."""
    assert _live_gate.lane_declaration() is None, "undeclared by default"
    monkeypatch.setenv("TORTOISE_TEST_CARVE_OUT", "1")
    assert _live_gate.lane_declaration() is False
    monkeypatch.delenv("TORTOISE_TEST_CARVE_OUT")
    monkeypatch.setenv("TORTOISE_TEST_EXPECT_URI", "1")
    assert _live_gate.lane_declaration() is True


def test_an_explicit_uri_promise_outranks_the_carve_out_flag(monkeypatch):
    """A lane that promised the URI cannot excuse itself with the carve-out."""
    monkeypatch.setenv("TORTOISE_TEST_CARVE_OUT", "1")
    monkeypatch.setenv("TORTOISE_TEST_EXPECT_URI", "1")
    assert _live_gate.lane_declaration() is True
    item = _FakeItem([_skipif("Live FalkorDB (Docker) not available")])
    assert _live_gate.violation(item) is not None


class _FakeCollectReport:
    """Just enough CollectReport for `collection_violation`.

    `outcome`/`longrepr`/`nodeid` are the three attributes it reads, and
    `longrepr` is the `(path, lineno, message)` tuple pytest's
    `pytest_make_collect_report` builds for a module-level skip.
    """

    nodeid = "tests/test_x.py"

    def __init__(self, outcome: str, longrepr: object):
        self.outcome = outcome
        self.longrepr = longrepr


def _abort(reason: str) -> _FakeCollectReport:
    return _FakeCollectReport(
        "skipped", ("tests/test_x.py", 29, f"Skipped: {reason}"))


@pytest.mark.parametrize(
    "report, env, violation",
    [
        # A lane that PROMISED the URI and aborted the whole module anyway: the
        # CI junit matcher reds this, so the in-process gate must too.
        (_abort("docker-lane caps tests require TORTOISE_DB_URI "
                "(tier-2 embedded legs skip)"),
         {"TORTOISE_TEST_EXPECT_URI": "1"}, True),
        # The same abort on the declared URI-less (carve-out) lane is the
        # deliberate epic-#1647-P4 gate, not a vacancy.
        (_abort("docker-lane caps tests require TORTOISE_DB_URI"),
         {"TORTOISE_TEST_CARVE_OUT": "1"}, False),
        (_abort("docker-lane caps tests require TORTOISE_DB_URI"),
         {"TORTOISE_TEST_EXPECT_URI": "0"}, False),
        # …and on an undeclared local run the historical exemption stands,
        # exactly as it does for the mark class and in CI.
        (_abort("docker-lane caps tests require TORTOISE_DB_URI"), {}, False),
        # The STRUCTURAL arm: an abort naming no URI precondition is a vacancy
        # in every lane (the #4221 class — 26 tests green for months).
        (_abort("no fastapi on this interpreter"), {}, True),
        # Not a skip: a real collection ERROR already fails the run, and a
        # collected module must never be touched.
        (_FakeCollectReport("failed", "ImportError: boom"), {}, False),
        (_FakeCollectReport("passed", None), {}, False),
    ],
)
def test_collection_verdict_table(report, env, violation, monkeypatch):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    message = _live_gate.collection_violation(report)
    assert (message is not None) is violation
    if violation:
        assert "REQUIRED by this MODULE" in message
        assert report.nodeid in message


def _run_pytest(*, carve_out: bool, expect_uri: bool = False,
                paths: tuple[str, ...] = (LIVE_FILE,)
                ) -> subprocess.CompletedProcess:
    """Run the live file in a CHILD pytest, so the hook runs as it really does.

    In-process would prove nothing here: the defect is about WHERE in pytest's
    runtest phases the enforcement happens (a session fixture is too late), and
    only a real collection+run exercises that ordering.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("TORTOISE_DB_URI", "TORTOISE_TEST_EXPECT_URI",
                        "EXPECT_URI", "TORTOISE_TEST_CARVE_OUT",
                        "TORTOISE_TEST_ALLOW_CANONICAL",
                        "TORTOISE_TEST_DOCKER_HOST")}
    # A CLOSED port makes "no database" deterministic on every host.
    env["TORTOISE_TEST_DOCKER_PORT"] = "1"
    if carve_out:
        env["TORTOISE_TEST_CARVE_OUT"] = "1"
    if expect_uri:
        # The lane PROMISED the URI (#3592) — the CI docker lane's declaration,
        # which is the shape a whole-module abort is a contradiction in.
        env["TORTOISE_TEST_EXPECT_URI"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", *paths, "-q", "-rs",
         "-p", "no:cacheprovider"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=900,
    )


def test_without_a_database_the_live_file_fails_instead_of_skipping():
    """THE regression: this run used to be `14 skipped`, rc=0."""
    proc = _run_pytest(carve_out=False)
    assert proc.returncode != 0, (
        "a live-requiring file with no database and no carve-out opt-in must "
        f"FAIL — it exited 0:\n{proc.stdout[-2000:]}")
    assert "live storage is REQUIRED by this test" in proc.stdout, \
        proc.stdout[-3000:]
    # The old signature was a skip summary with rc=0: no case of the availability
    # family may be reported SKIPPED any more. Only the LIVE-STORAGE reasons are
    # refused, not every skip in that file — a future optional-extra or platform
    # skip there is legitimate and must not break this pin.
    live_skips = [line for line in proc.stdout.splitlines()
                  if line.startswith("SKIPPED [")
                  and ("FalkorDB" in line or "TORTOISE_DB_URI" in line)]
    assert not live_skips, live_skips


def test_a_whole_module_live_skip_fails_instead_of_reporting_a_skip():
    """A module-level abort is decided at COLLECTION: rc used to be 0.

    The observed defect one phase before the mark hook: with one hermetic test
    in the selection the aborted module was counted as a skip and the run
    exited 0, so a lane could report green while a live module never ran. On
    pre-fix code this run prints `SKIPPED [1] tests/test_capabilities_endpoint.py`
    and exits 0 — both assertions below fail there.
    """
    proc = _run_pytest(carve_out=False, expect_uri=True,
                       paths=(COLLECTION_FILE, COLLECTED_WITNESS))
    assert "live storage is REQUIRED by this MODULE" in proc.stdout, \
        proc.stdout[-3000:]
    assert "SKIPPED [" not in proc.stdout, proc.stdout[-3000:]
    assert proc.returncode != 0, (
        f"a whole-module live-storage skip exited 0:\n{proc.stdout[-2000:]}")


def test_the_carve_out_lane_still_skips_a_whole_module_by_design():
    """The declared URI-less lane keeps its module-level gates (epic #1647 P4)."""
    proc = _run_pytest(carve_out=True,
                       paths=(COLLECTION_FILE, COLLECTED_WITNESS))
    assert proc.returncode == 0, proc.stdout[-3000:]
    assert "live storage is REQUIRED by this MODULE" not in proc.stdout
    # The gate is exempted, not deleted: the legitimate skip is still reported.
    assert "docker-lane capabilities tests require TORTOISE_DB_URI" \
        in proc.stdout, proc.stdout[-3000:]


def test_the_carve_out_lane_still_skips_by_design():
    """The legitimate embedded lane must not be made impossible to run."""
    proc = _run_pytest(carve_out=True)
    assert proc.returncode == 0, proc.stdout[-3000:]
    assert "skipped" in proc.stdout
    assert "live storage is REQUIRED by this test" not in proc.stdout
