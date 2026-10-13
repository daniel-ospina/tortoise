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
kill: ``faulthandler_timeout`` writes a frame and lets the item run on. The only
pytest-timeout budget configured for a repo-wide run was the flag each CI leg
passes (a test-local subprocess argv, e.g. ``tests/test_tripwire.py``, is not a
config default), so a ``pytest`` a human or an agent ran by hand armed NO killer
at all — which is how this stayed alive for two days:

    pid 64502  ppid 1  elapsed 01-19:48:27  cputime 3124:47 (~1.19 cores avg)
    python -m pytest tests/test_cohort_cost_cap.py -q -p no:cacheprovider

Worse, the host has no ``timeout``/``gtimeout`` binary (checked: neither is on
PATH), so the ``timeout 300`` convention the issue cites is not even available
locally — the budget has to come from the config, which every invocation reads.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import time
import tomllib
from collections import Counter
from pathlib import Path

import pytest
import pytest_timeout

from tests._signal_hygiene import harness_alarm_remaining

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
    # Appended unconditionally: an ABSENT key must fail here as loudly as a
    # too-large one, or deleting `timeout` would leave this test green and the
    # dump timer unbounded-relative instead of dump-before-kill.
    assert ini.get("timeout") is not None, (
        "no `timeout` in [tool.pytest.ini_options] (#5035): the dump timer has "
        "no kill to precede, so a stall dumps a frame and runs on"
    )
    budgets.append(int(float(ini["timeout"])))
    assert budgets, "no workflow arms pytest-timeout (`--timeout=`)"
    assert 0 < float(configured) < min(budgets), (
        f"faulthandler_timeout={configured} must fire before pytest-timeout's "
        f"smallest --timeout={min(budgets)}, or the dump timer is cancelled first"
    )


def test_every_workflow_pytest_install_carries_pytest_timeout():
    """The P2 fix, guarded: an inert `timeout` key is not a bound (#5035).

    ``timeout`` is an ini key that ONLY pytest-timeout registers. A workflow that
    installs pytest by hand without the plugin (or without the ``.[test]`` extra
    that carries it) gets `PytestConfigWarning: Unknown config option: timeout`
    and arms NO per-test killer — the exact defect this PR exists to close, moved
    from a developer's box to a runner. The round-1 review found `timeout = 300`
    was inert on exactly that pair of legs; this pins the pairing so a future
    hand-rolled install cannot silently re-open it.

    Mutation that makes this RED: drop ``pytest-timeout`` from either install line
    in ``.github/workflows/blog-write-e2e.yml`` or ``deploy-pages.yml`` (verified:
    both are reported as offenders).
    """
    offenders: list[str] = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8").replace("\\\n", " ")
        for line in text.splitlines():
            if line.lstrip().startswith("#") or "pip install" not in line:
                continue
            # pytest itself, or any pytest plugin (pytest-playwright, ...): all
            # of them drag pytest in, and none of them brings pytest-timeout.
            if not re.search(r"\bpytest\b", line):
                continue
            has_plugin = "pytest-timeout" in line
            has_extra = re.search(r"\.\[[^\]]*\btest\b", line) is not None
            if not (has_plugin or has_extra):
                offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, (
        "these workflow `pip install` lines install pytest but neither "
        "pytest-timeout nor the `.[test]` extra that carries it, so the "
        "`timeout = 300` config default is INERT there (`Unknown config option: "
        "timeout`) and the leg has no per-test killer (#5035):\n  "
        + "\n  ".join(offenders)
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


def _main_gate_budget() -> int:
    """The main CI gate's per-test budget: the MODE of ``python-ci.yml``'s flags.

    The MODE, not the ``min`` of every workflow. Two one-sided pins were tried
    and both were wrong: ``>= min(all workflows)`` rested on the false premise
    that every CI leg passes ``--timeout`` (several do not), and
    ``<= min(all workflows)`` would accept a default TIGHTER than every leg, so
    ``timeout = 290`` would pass and kill tests the main gate runs at 300.
    Taking the main workflow's mode keeps the reference on the gate that matters
    and stops a single special-purpose leg (``--timeout=120``) from silently
    retightening every bare local run.

    ``most_common`` has no defined tie-break, so a tie is a LOUD failure rather
    than an order-dependent pin: ``Counter([600, 600, 300, 300]).most_common(1)``
    returns whichever value appeared first.
    """
    main_text = (
        (WORKFLOWS_DIR / "python-ci.yml")
        .read_text(encoding="utf-8")
        .replace("\\\n", " ")
    )
    budgets = [
        int(m)
        for line in main_text.splitlines()
        if not line.lstrip().startswith("#") and "pytest" in line
        for m in re.findall(r"--timeout[= ](\d+)", line)
    ]
    assert budgets, "the main CI gate arms no pytest-timeout (`--timeout=`)"
    counts = Counter(budgets).most_common()
    assert counts[0][1] >= 2, (
        f"python-ci.yml has no repeated budget to pin against ({dict(Counter(budgets))}): "
        "the main gate's budget must be a value it actually passes on more than one leg"
    )
    if len(counts) > 1:
        assert counts[0][1] > counts[1][1], (
            f"python-ci.yml's `--timeout` values tie at the top ({dict(Counter(budgets))}) "
            "— most_common would then pick by line order, so which value is pinned 'the "
            "main gate's budget' would depend on a reordering. Make the majority unambiguous."
        )
    return counts[0][0]


def test_the_config_arms_a_terminating_timeout_without_a_cli_flag(pytestconfig):
    """The config KEY must be present, above the dump, and equal to CI's budget.

    ``pytestconfig.getini`` is the resolver's own view of
    ``[tool.pytest.ini_options]`` — not a re-read of the file — so this REDs the
    moment the key goes away. pytest-timeout registers the ini option with an
    EMPTY default and resolves CLI flag (including one injected by ``addopts``)
    -> ``PYTEST_TIMEOUT`` env -> this value
    (``pytest_timeout.get_env_settings``), so an unset key means a bare
    ``pytest`` has no bound at all; that is the incident's exact command.

    This test pins the KEY. The RESOLVED bound — which a higher-priority source
    can defeat while this reads 300 — is pinned by
    ``test_the_resolved_bound_from_the_repo_config_is_the_ci_budget``.

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
    reference = _main_gate_budget()
    assert budget == reference, (
        f"the config default timeout={budget} must equal the main gate's budget "
        f"{reference}. Looser hands the legs that declare no `--timeout` a weaker "
        f"bound than their peers; tighter kills, on a bare local run, tests the "
        f"main gate runs at {reference}s"
    )
    # A fat-finger guard, not a policy: an hour is still ~44x under the 43h48m
    # this issue exists for, and no test here needs more than that unmarked.
    assert budget <= 3600, f"timeout={budget} is too loose to be a default guard"


def test_the_resolved_bound_from_the_repo_config_is_the_ci_budget(tmp_path):
    """Round-1 review finding: the KEY is not the GUARD, so pin the RESOLVED value.

    A config source with HIGHER priority than the ini value disarms the guard
    without touching ``[tool.pytest.ini_options]``: ``addopts = "--timeout=0"``
    injects a CLI flag, and ``get_env_settings`` consults the CLI value FIRST —
    so ``getini("timeout")`` still reads 300 while a bare run resolves to 0.0 and
    arms nothing. That is the incident's shape with every other test in this
    file green.

    The probe runs under the REPO config (``-c pyproject.toml``) with
    ``PYTEST_TIMEOUT`` and ``PYTEST_ADDOPTS`` cleared, so it measures what a bare
    ``pytest`` in this checkout actually gets.

    The probe also pins the ``method``/``func_only`` pair the comment in
    ``pyproject.toml`` calls "the default ``signal``": the default is the only
    method that reaches a GIL-holding stall (``thread`` starves on the GIL —
    #7649), and ``func_only`` exempts fixtures, so a default that silently
    changed would leave the key armed and still not bound the incident's shape.

    Scope limit, stated rather than assumed: the probe file lives in
    ``tmp_path``, OUTSIDE the rootdir, so pytest loads no repo ``conftest.py``
    for it. A conftest-side disarm is therefore invisible HERE and is pinned
    in-process by ``test_the_resolved_bound_matches_the_sanctioned_sources``.

    Mutation that makes this RED: add ``addopts = "--timeout=0"`` to
    ``[tool.pytest.ini_options]`` (verified: the probe resolves 0.0 and fails),
    or reduce the value below the CI reference.
    """
    reference = _main_gate_budget()
    (tmp_path / "test_probe.py").write_text(
        "import pytest_timeout\n"
        "\n"
        "\n"
        "def test_probe(pytestconfig):\n"
        "    settings = pytest_timeout.get_env_settings(pytestconfig)\n"
        "    resolved = settings.timeout\n"
        "    assert resolved is not None and float(resolved) > 0, (\n"
        "        'a bare run resolves NO per-test bound (resolved=%r)' % (resolved,)\n"
        "    )\n"
        "    assert settings.method == 'signal', (\n"
        "        'the default timeout METHOD is %r, not signal: the thread method '\n"
        "        'starves on a GIL-holding stall (#7649)' % (settings.method,)\n"
        "    )\n"
        "    assert settings.func_only is False, (\n"
        "        'func_only=%r exempts fixtures from the bound' % (settings.func_only,)\n"
        "    )\n"
        "    print('RESOLVED=%r METHOD=%r FUNC_ONLY=%r'\n"
        "          % (float(resolved), settings.method, settings.func_only))\n",
        encoding="utf-8",
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTEST_TIMEOUT", "PYTEST_ADDOPTS")
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-s",
            "-p",
            "no:cacheprovider",
            "-c",
            str(PYPROJECT),
            str(tmp_path / "test_probe.py"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, (
        "the REPO config resolves no terminating per-test bound for a bare run "
        f"(the incident's shape):\n{output}"
    )
    assert f"RESOLVED={float(reference)} METHOD='signal' FUNC_ONLY=False" in output, (
        f"a bare run under the repo config did not resolve the main gate's "
        f"{float(reference)}s with the default signal method and no func_only "
        f"exemption:\n{output}"
    )


def _bound_from_the_sanctioned_sources(pytestconfig) -> float:
    """What pytest-timeout SHOULD resolve: CLI -> env -> ini, its own order.

    Recomputed from the sources rather than read back from
    ``config.option.timeout``, because that attribute is the thing a conftest
    can overwrite: reading it back would make the check self-confirming.
    ``getini`` reflects ``-o`` overrides, so that form is covered too.
    """
    args = _sanctioned_cli_args(pytestconfig)
    for i, arg in enumerate(args):
        if arg == "--timeout" and i + 1 < len(args):
            return float(args[i + 1])
        if arg.startswith("--timeout="):
            return float(arg.split("=", 1)[1])
    env = os.environ.get("PYTEST_TIMEOUT")
    if env:
        return float(env)
    ini = pytestconfig.getini("timeout")
    assert ini not in ("", None), (
        "no `timeout` in [tool.pytest.ini_options], and neither --timeout nor "
        "PYTEST_TIMEOUT supplies one (#5035)"
    )
    return float(ini)


def _sanctioned_cli_args(pytestconfig) -> list[str]:
    """The argv a user actually passed: ``invocation_params`` plus ``PYTEST_ADDOPTS``.

    ``invocation_params.args`` does NOT carry ``PYTEST_ADDOPTS`` while
    ``config.getvalue`` does, so scanning it alone made an equivalent invocation
    (``PYTEST_ADDOPTS='--timeout=7'``) read as a disagreement and false-red
    (round-2 review, second pass).
    """
    extra = os.environ.get("PYTEST_ADDOPTS", "")
    return list(pytestconfig.invocation_params.args) + shlex.split(extra)


def _method_from_the_sanctioned_sources(pytestconfig) -> str:
    """The method a user asked for: ``--timeout-method`` -> ini -> the default.

    Same reasoning as :func:`_bound_from_the_sanctioned_sources`: a conftest can
    move ``config.option.timeout_method``, so the guard must not read the value it
    is checking from the thing being checked.
    """
    args = _sanctioned_cli_args(pytestconfig)
    for i, arg in enumerate(args):
        if arg == "--timeout-method" and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--timeout-method="):
            return arg.split("=", 1)[1]
    return str(pytestconfig.getini("timeout_method") or "signal")


def test_the_resolved_bound_matches_the_sanctioned_sources(pytestconfig):
    """Round-2 review: a CONFTEST can zero the resolved option, and the key stays 300.

    Round 1 closed the ``addopts`` vector by resolving the value in a child
    process. That child cannot see this one: its probe file is written to
    ``tmp_path``, which is outside the rootdir, so the repo ``conftest.py`` files are
    never loaded for it and a conftest that overwrites the option (in
    ``pytest_configure``: ``config.option.timeout = 0``), or that implements the
    ``pytest_timeout_set_timer`` hookspec without arming anything, leaves a real
    bare run in this checkout armed with nothing — while every other test in
    this file stays green. That is the incident's shape, reintroduced through a
    door none of them watched.

    This reads the value from the OUTER config, which HAS loaded the real
    conftest chain, and compares it to the same resolution recomputed from the
    sanctioned sources. Comparing against the sources rather than against the
    CI budget keeps a legitimate ``--timeout=0``/``PYTEST_TIMEOUT=0`` run green
    (the value then IS what the sources say) while still catching an
    in-process overwrite — so the guard is not vacuous on the CI legs that pass
    ``--timeout=300``.

    Mutation that makes this RED: append ``def pytest_configure(config):\n    config.option.timeout = 0`` to ``tests/conftest.py`` (verified: this test
    fails while the other three config pins stay green).
    """
    expected = _bound_from_the_sanctioned_sources(pytestconfig)
    resolved = pytest_timeout.get_env_settings(pytestconfig).timeout
    assert resolved is not None and float(resolved) == expected, (
        f"the resolved per-test bound is {resolved!r}, but the sanctioned sources "
        f"(CLI -> PYTEST_TIMEOUT -> ini) give {expected!r}: an in-process hook "
        "overwrote it, so a bare run in this checkout would arm no killer — the "
        "shape #5035 exists for (43h48m)"
    )


def test_the_per_test_timer_is_actually_armed(pytestconfig, request):
    """Round-2 review, third door: a SOURCE value is not an ARM.

    ``get_env_settings`` proves what pytest-timeout *would* use, not that
    anything was armed. The arming path is a firstresult hook: the plugin's
    ``pytest_runtest_protocol``/``pytest_runtest_call`` call
    ``pytest_timeout_set_timer(item, settings)``, and ``settings`` is rebuilt
    from the snapshot ``config._env_timeout*`` taken once in the plugin's
    ``pytest_configure``. A conftest that implements that hookspec and returns a
    non-None value — or that adds ``pytest.mark.timeout(0)`` in
    ``pytest_collection_modifyitems`` — therefore arms NOTHING while
    ``get_env_settings`` still reads 300, and every other test here stays green.
    Reproduced by a reviewer on an earlier head of this PR: a probe in ``tests/``
    printed ``armed_itimer_remaining=0.0`` under either attack and ``294.6``
    without one, with all 15 tests of this file plus
    ``test_timeout_not_disarmable`` passing.

    So this pins the MECHANISM rather than the number: the ITEM's own resolved
    settings (``_get_item_settings`` — what the runtest protocol will actually arm
    with, marker priority included) must be a positive bound, the ``signal``
    method and no ``func_only`` exemption; AND the harness's own ``ITIMER_REAL``
    must actually be running while this item runs — the same read the
    ``harness_sigalrm_integrity`` fixture uses. It is SKIPPED when the run itself
    asks for no timer (``--timeout=0``, ``PYTEST_TIMEOUT=0``) or a non-``signal``
    method, because an unarmed ITIMER is then the request, not a defect.

    Both halves are needed, and neither is a duplicate of the other:

    * the per-ITEM settings catch a marker that outranks the config. A global
      ``pytest.mark.timeout(func_only=True)`` added in
      ``pytest_collection_modifyitems`` overrides ``_env_timeout_func_only`` at
      arm time, so the protocol-level timer that covers fixtures is NEVER armed
      while ``config._env_timeout_func_only`` still reads ``False`` and the
      call-phase ITIMER below is armed and positive — every earlier check here
      stayed green (round-2 review, second pass). The same marker silently makes
      ``harness_sigalrm_integrity`` inert during setup, re-opening #7655.
    * the ITIMER read catches a short-circuited firstresult
      ``pytest_timeout_set_timer`` (the previous pass's door), which leaves the
      resolved settings untouched.

    The skip is on an EXACT ``0`` (and on an explicitly requested non-``signal``
    method), not on ``<= 0``: a negative or unset bound is a defect, not an
    opt-out, and ``os.environ['PYTEST_TIMEOUT'] = '-1'`` set from a conftest used
    to satisfy a ``<= 0`` skip while arming nothing at all.

    Mutations that make this RED (verified): append a
    ``pytest_timeout_set_timer`` implementation returning ``True``, or a
    ``pytest_collection_modifyitems`` adding ``pytest.mark.timeout(0)`` or
    ``pytest.mark.timeout(func_only=True)``, to ``tests/conftest.py``.
    """
    expected = _bound_from_the_sanctioned_sources(pytestconfig)
    method = _method_from_the_sanctioned_sources(pytestconfig)
    if expected == 0 or method != "signal" or pytestconfig.getini("timeout_func_only"):
        pytest.skip(
            f"this run asks for no signal timer (bound={expected}, method={method!r}, "
            f"func_only={pytestconfig.getini('timeout_func_only')!r}): an unarmed "
            "ITIMER_REAL is the request, not a defect"
        )
    item_settings = pytest_timeout._get_item_settings(request.node)
    assert item_settings.timeout is not None and float(item_settings.timeout) == expected, (
        f"the per-item bound for this test is {item_settings.timeout!r}, but the "
        f"sanctioned sources give {expected!r}: a MARKER (or an in-process hook) "
        "overrode it, so an item in this checkout can run unbounded — the "
        "incident's shape (#5035, 43h48m)"
    )
    assert item_settings.method == "signal" and item_settings.func_only is False, (
        f"the per-item settings are method={item_settings.method!r}, "
        f"func_only={item_settings.func_only!r} although the sanctioned sources say "
        "`signal` with no func_only exemption: `thread` starves on a GIL-holding "
        "stall (#7649), and func_only leaves SETUP/TEARDOWN unbounded while this "
        "check's ITIMER read (call phase) stays green"
    )
    remaining = harness_alarm_remaining()
    assert remaining > 0, (
        "ITIMER_REAL is not armed while this item runs, although the config "
        f"resolves a {expected}s bound with the signal method: something "
        "short-circuited the firstresult `pytest_timeout_set_timer`, so a bare "
        "run in this checkout arms NO killer — the incident's shape (#5035, 43h48m)"
    )
    assert remaining <= expected, (
        f"the armed timer ({remaining}s) exceeds the resolved bound {expected}s"
    )


def test_the_ini_source_kills_a_hung_item_with_no_timeout_flag(tmp_path):
    """#5035: pytest-timeout's INI source kills a hung item with no flag present.

    Scope, deliberately narrow (round-1 review): ``-o timeout=2`` writes the same
    INI value the pyproject key fills, and the child runs in ``tmp_path`` so it
    never loads the repo config. This therefore pins the RESOLUTION BRANCH — an
    ini-sourced ``timeout`` with no CLI flag and no ``PYTEST_TIMEOUT`` actually
    ends a hung item — not the repo's value. The repo's value is pinned by
    ``test_the_config_arms_a_terminating_timeout_without_a_cli_flag`` and its
    resolution by ``test_the_resolved_bound_from_the_repo_config_is_the_ci_budget``.

    A ``timeout`` key pytest-timeout never resolves would leave the item running,
    so the child is given a 2s budget inside an outer 60s guard and the assertion
    is on the KILL, not on a stack dump.

    Mutation that makes this RED: remove ``-o timeout=2`` — the hung item then
    runs on and the 60s outer guard fires.
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
