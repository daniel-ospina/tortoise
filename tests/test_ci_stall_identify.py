"""#7649/#7655/#3719 — a stalled test must dump its own frame.

The cheap per-test guard (`pytest-timeout --timeout=300`, its default `signal`
method) fires on SIGALRM, dumps every thread EXCEPT the stuck one, and is
silently disarmed by any in-process `signal.alarm()` (shared ``ITIMER_REAL``,
#7655). pytest's built-in faulthandler watchdog is a thread: it needs no signal
and no ITIMER, which is why neither the GIL-holding stall (#7649) nor the alarm
collision (#7655) can quiet it, and its dump names the stuck thread.

``faulthandler_timeout`` is the one line that turns that on. These tests pin the
config invariant (the line must fire *before* ``--timeout``, or the dump timer
is cancelled when pytest-timeout ends the item) and reproduce the stall: a test
that holds the GIL continuously still gets a named frame.

#5035 adds the other half, and it is the half that was missing. A dump is not a
kill: ``faulthandler_timeout`` writes a frame and lets the item run on. Every
``--timeout`` in this repo used to live on a CI command line, so a ``pytest`` a
human or an agent ran by hand armed NO killer at all — which is how this stayed
alive for two days:

    pid 64502  ppid 1  elapsed 01-19:48:27  cputime 3124:47 (~1.19 cores avg)
    python -m pytest tests/test_cohort_cost_cap.py -q -p no:cacheprovider

Worse, the host has no ``timeout``/``gtimeout`` binary (checked: neither is on
PATH), so the ``timeout 300`` convention the issue cites is not even available
locally — the budget has to come from the config, which every invocation reads.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
import tomllib
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# Catastrophic regex backtracking: a pure-C loop that holds the GIL. The
# ``signal`` method of pytest-timeout does reach it (the re engine checks
# signals), but the ``thread`` method starves on the GIL — which is why the
# faulthandler watchdog thread (no GIL needed) is the mechanism that works.
_GIL_STALL = 're.match(r"(a+)+$", "a" * 32 + "b")'


def test_faulthandler_timeout_is_armed_below_the_per_test_budget():
    """Without the pyproject line a stall produces no frame — so this REDs.

    The bound must be strictly below pytest-timeout's. ``_pytest/faulthandler``
    arms ``dump_traceback_later`` per item and CANCELS it in a ``finally``; if
    pytest-timeout fires first the item ends and the dump timer is cancelled
    before it can write, making ``faulthandler_timeout >= --timeout`` a silent
    no-op rather than a slower dump.
    """
    with PYPROJECT.open("rb") as fh:
        ini = tomllib.load(fh)["tool"]["pytest"]["ini_options"]
    configured = ini.get("faulthandler_timeout")
    assert configured is not None, (
        "faulthandler_timeout is not set in pyproject.toml (#7649/#7655/#3719): "
        "a stalled test would produce a red with no frame"
    )
    # `faulthandler_timeout` is global, so every workflow's pytest leg must be
    # checked — a shorter `--timeout` added to any of them would silently make
    # the dump timer a no-op on that leg. Backslash continuations are folded
    # first, or a flag on a continuation line has no `pytest` beside it.
    text = "\n".join(
        path.read_text(encoding="utf-8").replace("\\\n", " ")
        for path in sorted(WORKFLOWS_DIR.glob("*.yml"))
    )
    budgets = [
        int(m)
        for line in text.splitlines()
        if not line.lstrip().startswith("#") and "pytest" in line
        for m in re.findall(r"--timeout[= ](\d+)", line)
    ]
    # #5035: the config's own budget is a REAL budget now — a bare run (no
    # --timeout flag) resolves to it — so the dump timer must precede it too.
    if ini.get("timeout") is not None:
        budgets.append(int(float(ini["timeout"])))
    assert budgets, "no workflow arms pytest-timeout (`--timeout=`)"
    assert 0 < float(configured) < min(budgets), (
        f"faulthandler_timeout={configured} must fire before pytest-timeout's "
        f"smallest --timeout={min(budgets)}, or the dump timer is cancelled first"
    )


def test_a_stall_dumps_its_own_frame(tmp_path):
    """A GIL-holding stall must produce a named frame, not a 15-minute silence.

    The dump is produced with a 1s bound so the test is fast; the stall itself
    never returns, so the still-stuck child is ended as soon as the dump has
    been observed. Asserting the *frame* (not just the header) is the point:
    pytest-timeout's own dump omits the stuck thread, so a bare "Timeout" banner
    would not prove this guard fired.
    """
    (tmp_path / "test_stall.py").write_text(
        "import re\n\n\ndef test_stall():\n    " + _GIL_STALL + "\n",
        encoding="utf-8",
    )
    log = tmp_path / "stall.log"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-o",
        "faulthandler_timeout=1",
        "--timeout=300",
        "test_stall.py",
    ]
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, cwd=tmp_path, stdout=fh, stderr=subprocess.STDOUT)
        try:
            _wait_for_text(log, "Timeout (0:00:01)!", proc, deadline_s=30)
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    output = log.read_text(encoding="utf-8", errors="replace")
    assert "Timeout (0:00:01)!" in output, (
        f"faulthandler did not fire for the GIL-holding stall:\n{output}"
    )
    assert "in test_stall" in output and "test_stall.py" in output, (
        f"faulthandler fired but did not name the stuck frame:\n{output}"
    )


def test_the_config_arms_a_terminating_timeout_without_a_cli_flag(pytestconfig):
    """The config alone must arm a per-test KILLER (#5035).

    ``pytestconfig.getini`` is the resolver's own view of
    ``[tool.pytest.ini_options]`` — not a re-read of the file — so this REDs the
    moment the key goes away. pytest-timeout registers the ini option with an
    EMPTY default and resolves CLI flag -> ``PYTEST_TIMEOUT`` env -> this value
    (``pytest_timeout._get_timeout``), so an unset key means a bare ``pytest``
    has no bound at all; that is the incident's exact command.

    Mutation that makes this RED: delete ``timeout = 300`` from
    ``[tool.pytest.ini_options]``. ``getini("timeout")`` then returns ``""`` and
    the first assertion fires.
    """
    ini = pytestconfig.getini("timeout")
    assert ini not in ("", None), (
        "no `timeout` in [tool.pytest.ini_options] (#5035): a pytest run that "
        "passes no --timeout flag — the incident's command — arms NO per-test "
        "killer; faulthandler_timeout only dumps a frame and lets it run on"
    )
    budget = float(ini)
    with PYPROJECT.open("rb") as fh:
        ini_block = tomllib.load(fh)["tool"]["pytest"]["ini_options"]
    dump_at = float(ini_block["faulthandler_timeout"])
    assert budget > dump_at, (
        f"timeout={budget} must exceed faulthandler_timeout={dump_at}, or the "
        "item ends (and the dump timer is cancelled) before the frame is written"
    )
    text = "\n".join(
        path.read_text(encoding="utf-8").replace("\\\n", " ")
        for path in sorted(WORKFLOWS_DIR.glob("*.yml"))
    )
    ci_budgets = [
        int(m)
        for line in text.splitlines()
        if not line.lstrip().startswith("#") and "pytest" in line
        for m in re.findall(r"--timeout[= ](\d+)", line)
    ]
    assert ci_budgets, "no workflow arms pytest-timeout (`--timeout=`)"
    # The pin is an EQUALITY against the MAIN GATE's budget, which is two-sided
    # on purpose. A one-sided `>= min(ci_budgets)` passed on the false premise
    # that "every CI leg passes --timeout" (it does not — `.github/workflows/ci.yml`
    # e2e legs, `blog-write-e2e.yml`, `deploy-pages.yml` and `evals-on-demand.yml`
    # pass none), and a one-sided `<= min(ci_budgets)` would accept a default
    # TIGHTER than every leg — `timeout = 290` would pass and kill tests CI runs
    # at 300, which is the failure this file's own rejected-alternatives row
    # names. Taking the MODE (not the min) of `python-ci.yml` keeps the reference
    # on the main gate: a single special-purpose leg adding `--timeout=120`
    # cannot silently retighten every bare run, and a real move of the main
    # gate's budget still REDs here.
    main_text = (WORKFLOWS_DIR / "python-ci.yml").read_text(encoding="utf-8")
    main_budgets = [
        int(m)
        for line in main_text.replace("\\\n", " ").splitlines()
        if not line.lstrip().startswith("#") and "pytest" in line
        for m in re.findall(r"--timeout[= ](\d+)", line)
    ]
    assert main_budgets, "the main CI gate arms no pytest-timeout (`--timeout=`)"
    reference, seen = Counter(main_budgets).most_common(1)[0]
    assert seen >= 2, (
        f"python-ci.yml has no repeated budget to pin against ({dict(Counter(main_budgets))}): "
        "the main gate's budget must be a value it actually passes on more than one leg"
    )
    assert budget == reference, (
        f"the config default timeout={budget} must equal the main gate's budget "
        f"{reference} (passed {seen}x in python-ci.yml). Looser hands the legs that "
        f"declare no `--timeout` a weaker bound than their peers; tighter kills, on "
        f"a bare local run, tests the main gate runs at {reference}s"
    )
    # A fat-finger guard, not a policy: an hour is still ~44x under the 43h48m
    # this issue exists for, and no test here needs more than that unmarked.
    assert budget <= 3600, f"timeout={budget} is too loose to be a default guard"


def test_a_bare_run_kills_a_hung_item_with_no_timeout_flag(tmp_path):
    """#5035: the ini source kills a hung item with no ``--timeout`` flag present.

    ``-o timeout=2`` writes the SAME ini value the pyproject key fills, so this
    exercises the branch a bare invocation takes: no CLI flag, no
    ``PYTEST_TIMEOUT``. A ``timeout`` key pytest-timeout never resolves would
    leave the item running, so the child is given a 2s budget inside an outer
    60s guard and the assertion is on the KILL, not on a stack dump.
    """
    (tmp_path / "test_hang.py").write_text(
        "import time\n\n\ndef test_hang():\n    time.sleep(600)\n", encoding="utf-8"
    )
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-o",
        "timeout=2",
        "test_hang.py",
    ]
    # The whole point is the branch that has no flag: assert the flag is absent
    # so a future edit cannot make this pass by supplying one.
    assert not any(a == "--timeout" or a.startswith("--timeout=") for a in argv)
    # Every ambient override of the ini source is cleared: `PYTEST_TIMEOUT` is a
    # HIGHER-priority source than the ini value this test is pinning.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTEST_TIMEOUT", "PYTEST_ADDOPTS")
    }
    started = time.monotonic()
    proc = subprocess.run(
        argv, cwd=tmp_path, capture_output=True, text=True, env=env, timeout=60
    )
    elapsed = time.monotonic() - started
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"the hung item was reported as passing:\n{output}"
    assert "Timeout (>2.0s) from pytest-timeout." in output, (
        f"the ini budget did not kill the hung item — it ran on past its bound "
        f"(the incident's shape):\n{output}"
    )
    assert elapsed < 30, f"the item was killed, but only after {elapsed:.1f}s"


def _wait_for_text(path: Path, needle: str, proc: subprocess.Popen, deadline_s: float) -> None:
    """Return once ``needle`` is in the (separately reopened) child log."""
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline and proc.poll() is None:
        if path.exists() and needle in path.read_text(encoding="utf-8", errors="replace"):
            return
        time.sleep(0.2)
