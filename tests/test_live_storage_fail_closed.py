"""The in-process live-storage gate: a live test FAILS, it does not skip (#1869).

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


def _run_pytest(*, carve_out: bool) -> subprocess.CompletedProcess:
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
    return subprocess.run(
        [sys.executable, "-m", "pytest", LIVE_FILE, "-q", "-rs",
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
    # family may be reported SKIPPED any more (the count is not asserted — a
    # test added to that file must not break this pin).
    assert "SKIPPED [" not in proc.stdout, proc.stdout[-3000:]


def test_the_carve_out_lane_still_skips_by_design():
    """The legitimate embedded lane must not be made impossible to run."""
    proc = _run_pytest(carve_out=True)
    assert proc.returncode == 0, proc.stdout[-3000:]
    assert "skipped" in proc.stdout
    assert "live storage is REQUIRED by this test" not in proc.stdout
