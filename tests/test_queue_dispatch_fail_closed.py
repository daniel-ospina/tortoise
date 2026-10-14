"""Regression test for the QUEUE-DISPATCH FAIL-OPEN defect (P0, 2026-10-13).

THE DEFECT
    `~/.pi/agent/scripts/queue-dispatch.py`'s `lanes()` ended with

        if rc != 0 or not out.strip().startswith("["):
            print(f"  \u26a0 lane-status read FAILED (rc={rc}): ...")
            return []                      # <-- FAIL-OPEN

    `sh()` returns `(None, "(timeout)")` on `subprocess.TimeoutExpired`, so a scan that was
    KILLED became an EMPTY lane list. The pass then printed `lanes READ : 0 / dispatchable: 0 /
    dispatched 0` and EXITED 0 -- byte-identical to a healthy, fed, empty fleet. 17 lanes sat
    idle while every run read `: 0`.

WHAT EACH CASE PINS (each can FAIL on the unfixed code, except the two marked)
    * `test_failed_read_raises_*`      -- rc != 0 must RAISE, not return [].
    * `test_timed_out_read_raises_*`   -- the live mechanism (rc is None) must RAISE.
    * `test_empty_roster_is_refused`   -- [] is a refusal, matching lane-status.py:1670.
    * `test_degraded_roster_is_refused`-- a partial (TSV-only) roster must not read as the fleet.
    * `test_unparseable_output_raises` -- garbage must not read as "no lanes".
    * `test_queue_read_*`              -- the SAME polarity one function down (`claim_next`).
    * `test_process_exit_*`            -- the exit code crosses the REAL process boundary
                                          (proves `sys.exit(main())`, not a discarded return).
    * `test_healthy_read_still_works`  -- (over-fix guard; passes on both versions, by design).
    * `test_abort_summary_*`           -- (banner-text pin; calls the helper directly, so it is
                                          NOT coupled to the fail-open mutation -- it only bites
                                          if the banner regresses).

MUTATION (how this test is proven to bite)
    Point `QUEUE_DISPATCH_SUT` at a copy with the original fail-open restored and the suite
    MUST fail. See the command in the module's `__main__`-free docstring below; the test file
    reads the env var so the mutation runs the SAME assertions:

        QUEUE_DISPATCH_SUT=/tmp/mutated-queue-dispatch.py \\
            TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_queue_dispatch_fail_closed.py

Hermetic: no network, no cmux, no git, no dispatch. The subprocess cases run under a throwaway
`HOME`, so the real fleet's state (locks, queues, lane-status) is never touched.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SUT = REPO / "tools" / "agent-infra-queue-dispatch.py"
# The mutation runner overrides this to point at a scratch copy (never the working tree).
SUT = Path(os.environ.get("QUEUE_DISPATCH_SUT", DEFAULT_SUT))


def _load_sut(path: Path):
    """Load the hyphenated script by path (it is not an importable module name)."""
    spec = importlib.util.spec_from_file_location("queue_dispatch_sut", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def sut():
    return _load_sut(SUT)


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """An isolated HOME with the script's expected skeleton, so a live fleet is never touched."""
    home = tmp_path / "home"
    (home / ".pi" / "agent" / "scripts").mkdir(parents=True)
    (home / ".pi" / "agent" / "state" / "queues").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def _stub_lane_status(home: Path, body: str) -> None:
    """Drop a fake lane-status.py where the SUT resolves LANE_STATUS under this HOME."""
    p = home / ".pi" / "agent" / "scripts" / "lane-status.py"
    p.write_text(body)
    p.chmod(0o755)


# ---------------------------------------------------------------------------
# unit: the read itself must RAISE, never return []
# ---------------------------------------------------------------------------

def test_failed_read_raises_and_never_returns_empty_list(sut, fake_home):
    """rc != 0 is a FAILED READ. The old code returned [] here and printed 'dispatchable: 0'."""
    _stub_lane_status(fake_home, "import sys; sys.stderr.write('boom\\n'); sys.exit(1)\n")
    sut.LANE_STATUS = str(fake_home / ".pi" / "agent" / "scripts" / "lane-status.py")
    with pytest.raises(sut.LaneReadError) as ei:
        sut.lanes(scan_timeout=10)
    assert "rc=1" in str(ei.value)


def test_timed_out_read_raises_and_names_the_timeout(sut, fake_home):
    """THE LIVE MECHANISM: `sh()` returns (None, '(timeout)'). It must RAISE, not return []."""
    _stub_lane_status(fake_home, "import time; time.sleep(30)\n")
    sut.LANE_STATUS = str(fake_home / ".pi" / "agent" / "scripts" / "lane-status.py")
    with pytest.raises(sut.LaneReadError) as ei:
        sut.lanes(scan_timeout=0.4)
    assert "TIMED OUT" in str(ei.value)
    assert "UNKNOWN, not empty" in str(ei.value)


def test_empty_roster_is_refused_not_returned(sut, fake_home):
    """[] is a REFUSAL (lane-status.py:1670 raises SystemExit(3) for it). Never a read."""
    _stub_lane_status(fake_home, "print('[]')\n")
    sut.LANE_STATUS = str(fake_home / ".pi" / "agent" / "scripts" / "lane-status.py")
    with pytest.raises(sut.LaneReadError) as ei:
        sut.lanes(scan_timeout=10)
    assert "EMPTY" in str(ei.value).upper()


def test_unparseable_output_raises(sut, fake_home):
    """Non-JSON must not be silently spent as 'no lanes'."""
    _stub_lane_status(fake_home, "print('not json at all')\n")
    sut.LANE_STATUS = str(fake_home / ".pi" / "agent" / "scripts" / "lane-status.py")
    with pytest.raises(sut.LaneReadError):
        sut.lanes(scan_timeout=10)


def test_degraded_roster_is_refused(sut, fake_home):
    """A PARTIAL (TSV-only) roster is rc=0 and non-empty -- it must still be refused.

    lane-status marks every row `degraded` when the cmux registry read failed, and such a
    roster is missing every live-only lane; `lane-actions.py` refuses the same payload.
    """
    rows = [{"lane": "real-lane", "ws": "W1", "verdict": "DONE-IDLE", "degraded": False},
            {"lane": "live-only-lane", "ws": "W2", "verdict": "DONE-IDLE", "degraded": True}]
    _stub_lane_status(fake_home, f"import json; print(json.dumps({rows!r}))\n")
    sut.LANE_STATUS = str(fake_home / ".pi" / "agent" / "scripts" / "lane-status.py")
    with pytest.raises(sut.LaneReadError) as ei:
        sut.lanes(scan_timeout=10)
    assert "DEGRADED" in str(ei.value).upper()
    assert "live-only-lane" in str(ei.value)


def test_scan_timeout_env_is_parsed_defensively(sut, monkeypatch):
    """A bad tuning knob is a fallback, never a crash and never an unbounded read."""
    monkeypatch.setenv("QUEUE_DISPATCH_SCAN_TIMEOUT", "not-a-number")
    assert sut._env_scan_timeout() == sut.SCAN_TIMEOUT_DEFAULT
    monkeypatch.setenv("QUEUE_DISPATCH_SCAN_TIMEOUT", "0")
    assert sut._env_scan_timeout() == sut.SCAN_TIMEOUT_DEFAULT
    monkeypatch.setenv("QUEUE_DISPATCH_SCAN_TIMEOUT", "inf")
    assert sut._env_scan_timeout() == sut.SCAN_TIMEOUT_DEFAULT
    monkeypatch.setenv("QUEUE_DISPATCH_SCAN_TIMEOUT", "90")
    assert sut._env_scan_timeout() == 90.0


# ---------------------------------------------------------------------------
# the same polarity one function down: the work-queue read
# ---------------------------------------------------------------------------

def _stub_claim(home: Path, body: str) -> str:
    p = home / "claim-stub.py"
    p.write_text(body)
    return str(p)


def test_queue_read_failure_raises_never_empty(sut, fake_home):
    """`claim.py` exits 2 for an unreadable queue. That must not read as 'nothing left'."""
    sut.CLAIM = _stub_claim(fake_home, "import sys; sys.stderr.write('missing queue'); sys.exit(2)\n")
    with pytest.raises(sut.QueueReadError) as ei:
        sut.claim_next("lane", "REMEDY.tsv")
    assert "rc=2" in str(ei.value)


def test_queue_read_silent_output_raises_never_empty(sut, fake_home):
    """rc=0 with NO output is UNKNOWN, not an empty queue (the explicit empty answer is NONE)."""
    sut.CLAIM = _stub_claim(fake_home, "import sys; sys.exit(0)\n")
    with pytest.raises(sut.QueueReadError):
        sut.claim_next("lane", "REMEDY.tsv")


def test_queue_read_none_is_genuinely_empty(sut, fake_home):
    """OVER-FIX GUARD: an explicit NONE (read, and empty) still returns (None, path)."""
    sut.CLAIM = _stub_claim(fake_home, "print('NONE')\n")
    item, path = sut.claim_next("lane", "REMEDY.tsv")
    assert item is None
    assert path.endswith("REMEDY.tsv")


def test_queue_read_item_is_returned(sut, fake_home):
    sut.CLAIM = _stub_claim(fake_home, "print('PR 123 fix the thing')\n")
    item, _path = sut.claim_next("lane", "REMEDY.tsv")
    assert item == "PR 123 fix the thing"


def test_abort_summary_cannot_be_mistaken_for_an_empty_fleet(sut, capsys):
    """The banner must never contain the incident's misleading string."""
    rc = sut._abort_unreadable(sut.LaneReadError("lane-status.py TIMED OUT after 1.0s (rc=None)"))
    out = capsys.readouterr().out
    assert rc == 3, "the abort must exit 3 (same polarity as lane-status.py:1670)"
    assert "LANE ROSTER UNREADABLE" in out
    assert "UNKNOWN" in out
    # ⛔ THE WHOLE POINT: the exact string that hid the defect for every run must be absent.
    assert "lanes READ  : 0" not in out
    assert "dispatched 0" not in out


# ---------------------------------------------------------------------------
# the real process boundary: exit code + summary
# ---------------------------------------------------------------------------

def _run_sut_as_process(fake_home: Path, *extra: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env["QUEUE_DISPATCH_SCAN_TIMEOUT"] = "15"
    return subprocess.run(
        [sys.executable, str(SUT), *extra],
        capture_output=True, text=True, timeout=120, env=env,
    )


def test_process_exit_is_nonzero_and_summary_is_distinguishable(sut, fake_home):
    """A failed scan must make the PROCESS non-zero, never 'lanes READ : 0' with exit 0."""
    _stub_lane_status(fake_home, "import sys; sys.stderr.write('simulated failure\\n'); sys.exit(1)\n")
    r = _run_sut_as_process(fake_home)
    assert r.returncode == 3, (
        f"expected exit 3 (unreadable roster), got {r.returncode}\n"
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}")
    assert "LANE ROSTER UNREADABLE" in r.stdout
    assert "lanes READ  : 0" not in r.stdout
    assert "dispatched 0" not in r.stdout
    assert "Traceback" not in r.stderr


def test_process_exit_is_nonzero_on_timeout(sut, fake_home):
    """The exact live path: the scan is killed, so the pass must abort non-zero."""
    _stub_lane_status(fake_home, "import time; time.sleep(30)\n")
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env["QUEUE_DISPATCH_SCAN_TIMEOUT"] = "1"
    r = subprocess.run([sys.executable, str(SUT)], capture_output=True, text=True,
                       timeout=120, env=env)
    assert r.returncode == 3, f"got {r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "TIMED OUT" in r.stdout
    assert "lanes READ  : 0" not in r.stdout


def test_healthy_read_still_works(sut, fake_home, capsys):
    """OVER-FIX GUARD: a good read must still return rows and exit 0."""
    _stub_lane_status(
        fake_home,
        "import json; print(json.dumps([{'lane': 'x', 'ws': 'WS', 'verdict': 'WORKING'}]))\n",
    )
    sut.LANE_STATUS = str(fake_home / ".pi" / "agent" / "scripts" / "lane-status.py")
    rows = sut.lanes(scan_timeout=10)
    assert rows == [{"lane": "x", "ws": "WS", "verdict": "WORKING"}]

    _stub_lane_status(
        fake_home,
        "import json; print(json.dumps([{'lane': 'x', 'ws': 'WS', 'verdict': 'WORKING'}]))\n",
    )
    r = _run_sut_as_process(fake_home, "--status")
    assert r.returncode == 0, f"a healthy read must not abort\n{r.stdout}\n{r.stderr}"
    assert "lanes READ  : 1" in r.stdout


# ---------------------------------------------------------------------------
# the last-turn classifier is a DEPENDENCY: a failed load is not a lane verdict
# (PR #8594 review P2)
# ---------------------------------------------------------------------------

def test_missing_classifier_sibling_is_a_named_load_failure(sut, tmp_path, monkeypatch):
    """A failed DEPENDENCY LOAD must be a named refusal, not a per-lane WITHHOLD.

    The loader resolves `turn-classify.py` beside the script. Here it is absent by
    construction (the copy lives in an empty temp dir), which is exactly the in-repo
    copy's real shape -- the live `turn-classify.py` is in `~/.pi/agent/scripts/`.
    Pre-fix this raised an anonymous FileNotFoundError; the suite names the type.
    """
    sut._TC_MODULE = None
    monkeypatch.setattr(sut, "__file__", str(tmp_path / "queue-dispatch-copy.py"))
    with pytest.raises(sut.ClassifierLoadError) as ei:
        sut._turn_classify()
    assert "turn-classify.py" in str(ei.value)
    assert "NOT beside this script" in str(ei.value)


def test_classify_last_turn_propagates_a_load_failure_not_a_verdict(
    sut, tmp_path, monkeypatch
):
    """`classify_last_turn` must RAISE on a failed load, never return `(None, ...)`.

    `choice is None` is main()'s per-lane WITHHOLD branch: folding a failed import into it
    printed `WITHHOLD <lane>: last-turn unreadable` for the whole fleet, then
    `dispatched 0` and exit 0.
    """
    sut._TC_MODULE = None
    monkeypatch.setattr(sut, "__file__", str(tmp_path / "queue-dispatch-copy.py"))
    with pytest.raises(sut.ClassifierLoadError):
        sut.classify_last_turn({"lane": "x", "sid": "a" * 36})


def _roster_with_one_idle_lane(fake_home: Path) -> None:
    """A roster the pass WOULD dispatch from: one assigned DONE-IDLE lane, no WIP signals."""
    _stub_lane_status(
        fake_home,
        "import json; print(json.dumps(["
        "{'lane': 'idle-lane', 'ws': 'WS-IDLE', 'verdict': 'DONE-IDLE', 'grew': False,"
        " 'spinner': False, 'child': None, 'declared': None, 'stale_s': 9999}]))\n",
    )
    a = fake_home / ".pi" / "agent" / "state" / "queues" / "LANE-ASSIGNMENTS.tsv"
    a.write_text("lane\tclass\nidle-lane\tland\n")


def _sut_with_classifier_sibling(tmp_path: Path) -> Path:
    """A copy of the SUT with a stub `turn-classify.py` BESIDE it (the live dir's shape)."""
    d = tmp_path / "copy"
    d.mkdir()
    copy = d / "queue-dispatch.py"
    copy.write_text(DEFAULT_SUT.read_text())
    (d / "turn-classify.py").write_text(
        "def session_file_for(sid):\n    return None\n"
        "def last_turn_text(path):\n    return ''\n"
        "def jev_choice(turn):\n    return None, {}\n"
    )
    return copy


def test_process_aborts_when_the_classifier_cannot_load(fake_home):
    """THE REVIEW'S REPRO: a failed dependency load must make the PROCESS non-zero.

    Pre-fix: every lane printed `WITHHOLD <lane>: last-turn unreadable`, the pass printed
    `dispatched 0` and exited 0 -- identical to a healthy, fed fleet. Post-fix: the pass
    aborts with exit 3 and a summary that names the dependency.
    """
    _roster_with_one_idle_lane(fake_home)
    r = _run_sut_as_process(fake_home)
    assert r.returncode == 3, (
        f"a failed dependency load must abort (exit 3), got {r.returncode}\n"
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}")
    assert "DISPATCH DEPENDENCY UNREADABLE" in r.stdout
    assert "turn-classify.py" in r.stdout
    assert "last-turn unreadable" not in r.stdout, "a failed load is not a lane reading"
    assert "dispatched 0" not in r.stdout
    assert "Traceback" not in r.stderr


def test_process_does_not_abort_when_the_classifier_sibling_exists(fake_home, tmp_path):
    """OVER-FIX GUARD: with `turn-classify.py` present the pass classifies, never aborts."""
    _roster_with_one_idle_lane(fake_home)
    copy = _sut_with_classifier_sibling(tmp_path)
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env["QUEUE_DISPATCH_SCAN_TIMEOUT"] = "15"
    r = subprocess.run([sys.executable, str(copy)], capture_output=True, text=True,
                       timeout=120, env=env)
    assert "DISPATCH DEPENDENCY UNREADABLE" not in r.stdout, r.stdout
    assert "last-turn unreadable" not in r.stdout, r.stdout
    assert "LAST-TURN idle-lane: finished_the_work" in r.stdout, r.stdout
