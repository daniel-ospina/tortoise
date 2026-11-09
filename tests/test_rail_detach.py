#!/usr/bin/env python3
"""tests for tools/rail_detach.py — the rail must outlive the lane that starts it.

THE REGRESSION THESE TESTS LOCK (measured 2026-11-03)
`scripts/admin-merge.sh` waits for the PR's CI run, and that wait's duration is
set by the runner QUEUE (run 41557371710: created 01:05:15Z, still queued at
02:56Z — >= 6660 s) — while the lane's harness budget was 1700 s. The rail's own
ceiling is 18064 s, so it was healthy-and-waiting when the LANE died and took the
verdict with it. Make the rail the thing that outlives the lane.

The proof that matters is `test_detachment_is_what_survives_the_lane` in its
TWO arms (`[False]` and `[True]`): the same launch, the same kill, opposite
outcomes — the `[False]` arm is the mutant (detachment off) and the rail dies
with the lane, so a test that passes either way proves nothing.

No database, no network: the "rail" is a stub script that says what the real one
says, and the launcher never resolves a repo it was given.
"""
from __future__ import annotations

import ast
import contextlib
import errno
import inspect
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import rail_detach as rd  # noqa: E402

FAKE_RAIL = """#!/usr/bin/env bash
# stub of scripts/admin-merge.sh — same WORDS for the lines the artifact parses
set -u
PR="$1"
SLEEP="${FAKE_RAIL_SLEEP:-1}"
echo "admin-merge: … FIRST-CHECKS wait: run 41557371710 is pending (0s of 18064s)"
sleep "$SLEEP"
case "${FAKE_RAIL_VERDICT:-merged}" in
  merged)
    echo "admin-merge: ✅ merged PR #$PR at deadbeefcafe1234 (state=MERGED confirmed via the API)"
    exit 0 ;;
  refused)
    echo "admin-merge: ✗ BLOCK — the residual failure set is non-empty; refusing to merge"
    exit 9 ;;
esac
"""


def make_checkout(tmp_path: Path, body: str = FAKE_RAIL) -> Path:
    co = tmp_path / "co"
    (co / "scripts").mkdir(parents=True)
    rail = co / "scripts" / "admin-merge.sh"
    rail.write_text(body, encoding="utf-8")
    rail.chmod(0o755)
    return co


def fake_gh(tmp_path: Path, script: str, sub: str = "bin") -> Path:
    """A `gh` on PATH — the launcher's one external call, made hermetic."""
    bindir = tmp_path / sub
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text(script, encoding="utf-8")
    gh.chmod(0o755)
    return bindir


@pytest.fixture(autouse=True)
def _hermetic_gh(tmp_path, monkeypatch):
    """`launch()` reads the PR head with ONE best-effort `gh` call.

    ⛔ THIS SUITE USED TO MAKE A REAL `gh pr view` PER LAUNCH — ~1.9 s each on a
    rate-limited box, one per test that launches, and a network dependency in a
    file whose registration says it is hermetic. The call is INFORMATIONAL
    (nothing gates on `HEAD`), so a `gh` that answers nothing is the honest stub:
    `HEAD` records `unreadable` and every verdict assertion below is unaffected.
    (Stated per-launch, not as a launch COUNT: a count here is stale the moment a
    test is added — the same reason the `config/ci-surfaces.yml` row carries none.)
    """
    bindir = fake_gh(tmp_path, "#!/usr/bin/env bash\nexit 1\n")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")


def launch(state_dir, co, pr=7, **kw):
    return rd.launch(pr, cwd=co, state_dir=state_dir, repo="o/r", **kw)


# ⛔ A WAIT BOUND HERE IS A **STALL BUDGET**, NEVER THE RAIL'S EXPECTED DURATION.
# Every wait in this file returns on the first matching read, so a healthy run
# pays none of it; the number only decides how long the BOX may freeze before a
# healthy rail is reported as a verdict. Measured 2026-11-06 on shard `test (h)`
# of run 42185502189 (head `e190cc73a2b2`): this file's own
# `test_a_new_attempt_truncates_the_log` took **42.78 s** for a **1.03 s** local
# call, and two setups in the same shard took 44-60 s — so the 2 s stub rail
# outlived a 20 s wait, `test_start_returns_immediately_and_collect_sees_it_waiting`
# read `waiting` at its deadline, and the shard went red with 3 854 tests passing.
# 20 s was never the rail's duration; it was an unstated assumption that the box
# is not stalled, and on a shared runner that assumption is false. A bound below
# the largest measured stall CAN re-create that red, which is what the pin in
# `test_the_wait_budget_exceeds_the_largest_measured_stall` refuses.
STALL_BUDGET_S = 120


def wait_state(state_dir, pr, want, timeout=STALL_BUDGET_S, pred=None):
    end = time.time() + timeout
    st = None
    while time.time() < end:
        st = rd.collect(state_dir, "o/r", pr)
        if st is not None and st.state == want and (pred is None or pred(st)):
            return st
        time.sleep(0.05)
    return st


def not_merged(state_dir, pr, st, want=rd.MERGED) -> str:
    """Why a wait gave up: the state, its REASON and the rail's own words.

    ⛔ `STALL_BUDGET_S` FIXES THE BOUND; THIS FIXES THE OTHER HALF OF THE SAME RED.
    `assert wait_state(...).state == rd.MERGED` published `- merged` / `+ waiting`
    and nothing else, so the 2026-11-06 shard could not be read from its own log:
    nothing said whether the rail never ran, never printed, or printed and was
    scoped out, and settling that took fetching the job's `--durations` table and
    cross-referencing a sibling test. These are the three artifacts that answer it,
    rendered at the point of failure. Diagnostic only: no assertion moves.
    """
    art = rd.artifact_path(state_dir, "o/r", pr)
    try:
        text = art.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = "(the artifact is unlinked)"
    logp = rd.read_field(text, "LOG")
    if not logp:
        tail = "(the artifact names no LOG)"
    else:
        try:
            lines = Path(logp).read_text(encoding="utf-8", errors="replace").splitlines()
            tail = "\n".join(lines[-10:]) or "(the log is empty)"
        except OSError as exc:            # unreadable is a finding, not a crash
            tail = f"(the log is unreadable: {exc})"
    # ⛔ A LIVE `waiting` READ NOW DOES CARRY A REASON — `tools/rail_detach.py`'s
    # carrier arm names the wrapper pid, the rail pid and the rail's own log
    # (`_collect_read`, "a `waiting` verdict must say why it has no verdict yet").
    # The conditional below is kept for the states that still answer none, so an
    # empty reason is never rendered as `waiting ()`.
    detail = (f" ({st.reason})" if st is not None and st.reason else "")
    return (f"never reached `{want}` within {STALL_BUDGET_S:.0f}s — last read: "
            f"{st.state if st is not None else 'absent'}{detail}\n"
            f"--- artifact ---\n{text}"
            f"--- rail log tail ---\n{tail}")


# ⛔ `wait_field` IS THE SAME KIND OF WAIT AND WAS THE LAST BELLOW THE STALL FLOOR.
# It waits for a FIELD a peer process writes — `CHILD_PID`, which the RAIL records a
# few instructions after the fork, not the launcher (see the fork-window arms below)
# — so a stalled box delays it by exactly the seconds `STALL_BUDGET_S` exists to
# absorb. It carried `10.0`, well below the 42.78 s stall measured above: on such a
# box it returns `""`, the caller's `int("" or 0)` becomes `0`, and a LIVE rail reads
# as "no rail" — the durable-wrong verdict this file's fork-window arms exist to
# refuse. Widened to the shared budget (#10610's conflicted head re-applies this one
# bound on main's rewrite); every call site below is a POSITIVE wait that returns on
# the first match, so a healthy run pays none of it.
def wait_field(path: Path, key: str, timeout=STALL_BUDGET_S) -> str:
    end = time.time() + timeout
    while time.time() < end:
        value = rd.read_field(path.read_text(encoding="utf-8"), key)
        if value:
            return value
        time.sleep(0.02)
    return ""


def kill_group(pid: int, sig=signal.SIGKILL) -> None:
    """Kill a rail's WHOLE process group — the wrapper AND the rail.

    ⛔ `PID=` is the `bash -c` WRAPPER and `CHILD_PID=` is the rail, and they die
    separately: killing only the wrapper leaves the rail RUNNING (that is the P1
    this file now pins). "The rail is gone" therefore means the group, never the
    one pid.
    """
    with contextlib.suppress(OSError):   # ProcessLookupError: already gone
        os.killpg(os.getpgid(pid), sig)


# ── the collect contract ─────────────────────────────────────────────────────
def test_start_returns_immediately_and_collect_sees_it_waiting(tmp_path, monkeypatch):
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    monkeypatch.setenv("FAKE_RAIL_SLEEP", "2")
    ln = launch(sd, co)
    # STARTED is reported without waiting for the rail: this call already returned.
    assert ln.pid > 0
    assert Path(ln.artifact).exists()
    st = wait_state(sd, 7, rd.WAITING)
    assert st.state == rd.WAITING, st.as_dict()
    assert st.line().startswith("RAIL(#7, waiting,")
    # ⛔ A `waiting` VERDICT NAMES WHY IT HAS NO VERDICT — the invariant this arm
    # also pins, and the reason `not_merged()` below can now render a live read
    # instead of `waiting ()`. The 2026-11-06 shard printed
    # `assert 'waiting' == 'merged'` with no mechanism attached, so the pid, the
    # carrier and the rail's log all had to be re-derived by hand; `collect` now
    # carries them in `reason`, on every arm that can answer `waiting` (carrier,
    # sentinel, replaced record).
    assert st.reason, st.as_dict()
    assert st.log in st.reason, st.as_dict()
    st = wait_state(sd, 7, rd.MERGED)
    assert st.state == rd.MERGED, not_merged(sd, 7, st)


def test_the_wait_budget_exceeds_the_largest_measured_stall(tmp_path, monkeypatch):
    """The 2026-11-06 shard red, reproduced in ~3 s instead of on a stalled box.

    `test_start_returns_immediately_and_collect_sees_it_waiting` read `waiting` at
    its deadline on shard `test (h)` of run 42185502189 — while that same file's
    `test_a_new_attempt_truncates_the_log` paid 42.78 s for a 1.03 s local call and
    two setups took 44-60 s. The box stalled; the WAIT BOUND, not the state
    machine, turned that into a red shard for a PR that touches neither file.

    Both halves are asserted so the constant cannot be quietly tightened back:
    a bound SHORTER than the rail's own work reads `waiting` (the failure,
    reproduced deterministically in half a second), and the SAME rail, the same
    artifact, settles to `merged` under `STALL_BUDGET_S` — so the difference
    between the two readings is the clock and nothing else.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    monkeypatch.setenv("FAKE_RAIL_SLEEP", "2")
    launch(sd, co, pr=78)
    # A bound below the rail's own 2 s of work: `waiting`, which is exactly what
    # the shard reported. Nothing is wrong with the rail here.
    early = wait_state(sd, 78, rd.MERGED, timeout=0.5)
    assert early.state == rd.WAITING, early.as_dict()
    # …and the same rail reaches `merged` once the clock is allowed to run.
    assert wait_state(sd, 78, rd.MERGED).state == rd.MERGED
    # The pin: 42.78 s is the largest stall MEASURED on this file's own work, so a
    # budget at or below it can re-create the red that shipped `test (h)`.
    assert STALL_BUDGET_S > 42.78, (
        f"STALL_BUDGET_S={STALL_BUDGET_S} is at or below the 42.78 s stall measured "
        "on run 42185502189 — a healthy rail on a stalled box reads `waiting` "
        "again, which is the 2026-11-06 `test (h)` red")
    # ⛔ EVERY state/field-arrival wait in this file takes its default from THAT
    # budget — the pin above bounds a constant, and a call site that hardcodes a
    # smaller number beside it is the same red with the pin still green. `wait_field`
    # is the one that read `10.0` after main's rewrite: it waits for a field a PEER
    # process writes, so a 10 s cap on a 42 s-stalled box returns `""` and a LIVE
    # rail is read as "no rail". Both are asserted from their signatures, so a
    # genuine need for a TIGHTER bound has to say so here rather than by edit.
    for fn in (wait_state, wait_field):
        default = inspect.signature(fn).parameters["timeout"].default
        assert default == STALL_BUDGET_S, (
            f"{fn.__name__}'s default wait bound is {default!r}, not the shared "
            f"STALL_BUDGET_S ({STALL_BUDGET_S}). A bound below the 42.78 s stall "
            "re-creates the 2026-11-06 `test (h)` red for the caller that did not "
            "pass one explicitly; if a call site needs a tighter bound, pass it "
            "at that call site and record why here.")


def test_merged_state_names_the_sha_the_rail_printed(tmp_path):
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    launch(sd, co, pr=11)
    # ⛔ WAIT FOR THE SETTLED READ, not merely the first `merged`. A rail prints
    # its success line BEFORE it appends `EXIT=`, and `collect` answers `merged`
    # in that window (`exit_code=None`) — a terminal state, a real merge, but not
    # yet this test's evidence. Without the predicate this test is a race: it
    # failed twice in two runs at load 27 on 2026-11-03 while passing in
    # isolation, i.e. an intermittent red on a CI-gating file for a diff that
    # changed nothing.
    st = wait_state(sd, 11, rd.MERGED, pred=lambda s: s.exit_code is not None)
    assert st.state == rd.MERGED
    assert st.exit_code == 0
    assert st.merge_sha == "deadbeefcafe1234"      # from the rail's own log line
    assert st.run_ids == ["41557371710"]           # the run id, parsed, not invented
    assert st.line() == f"RAIL(#11, merged, 41557371710, {st.since})"


def test_refused_state_carries_the_rails_own_reason(tmp_path, monkeypatch):
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    monkeypatch.setenv("FAKE_RAIL_VERDICT", "refused")
    launch(sd, co, pr=12)
    st = wait_state(sd, 12, rd.REFUSED)
    assert st.state == rd.REFUSED and st.exit_code == 9
    assert "residual failure set is non-empty" in st.reason
    assert st.merge_sha == ""


def test_a_killed_rail_is_lost_never_merged(tmp_path):
    """No EXIT line is NOT a verdict. Reporting `merged` here would be #2235.

    The kill is to the GROUP: `PID=` names the wrapper, and killing it alone
    leaves the rail it started running (see the wrapper arm below).
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    os.environ["FAKE_RAIL_SLEEP"] = "30"
    try:
        ln = launch(sd, co, pr=13)
    finally:
        os.environ.pop("FAKE_RAIL_SLEEP", None)
    time.sleep(0.2)
    kill_group(ln.pid)
    time.sleep(0.3)
    st = rd.collect(sd, "o/r", 13)
    assert st.state == rd.LOST and st.exit_code is None
    assert "no EXIT line" in st.reason


def test_a_killed_wrapper_does_not_lose_a_live_rail(tmp_path, monkeypatch):
    """⛔ TWO PROCESSES CARRY THE VERDICT, AND THEY DIE SEPARATELY.

    `start` runs `bash -c <wrapper>`, and the wrapper runs the lander as a
    background job so it can `wait` for it and write `EXIT=`. `PID=` therefore
    names the WRAPPER; the rail has its own pid (`CHILD_PID=`, written by the rail
    itself). Probing only the wrapper reported `lost` for a rail that was still
    merging — measured before the fix: the rail went on to print its `✅ merged`
    line with nobody left to write `EXIT=`, while `collect` said `lost`, so the
    next beat's `start` was allowed, truncated the artifact and appended a new
    attempt mark, which scopes that real merge line out for good (#2235's durable
    wrong verdict, in the safe direction but permanent).
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    monkeypatch.setenv("FAKE_RAIL_SLEEP", "3")
    ln = launch(sd, co, pr=75)
    assert wait_state(sd, 75, rd.WAITING).state == rd.WAITING
    art = rd.artifact_path(sd, "o/r", 75)
    child = int(wait_field(art, "CHILD_PID") or 0)
    assert child > 0, art.read_text()          # named by the RAIL, not the wrapper
    assert child != ln.pid                     # …and it is a different process
    os.kill(ln.pid, signal.SIGKILL)            # the WRAPPER dies; the rail lives on
    time.sleep(0.2)
    st = rd.collect(sd, "o/r", 75)
    assert st.state != rd.LOST, st.as_dict()   # pre-fix: `lost` while it was merging
    st = wait_state(sd, 75, rd.MERGED)
    assert st.state == rd.MERGED and st.merge_sha == "deadbeefcafe1234", (
        not_merged(sd, 75, st))
    assert st.exit_code is None
    assert "EXIT line was never written" in st.reason


def test_the_fork_window_is_not_lost(tmp_path):
    """⛔ `child == 0` IS NOT "NO RAIL" — it is the window BEFORE `CHILD_PID=`.

    The wrapper forks the rail and the rail records ITS OWN pid a few
    instructions later. A wrapper killed in that window leaves no pid naming the
    rail, while the rail is alive and about to merge — the same durable-wrong-
    verdict shape as the arm above, reached by a different clock. The GROUP
    still answers and the rail is in it by construction, so `lost` here would
    truncate the artifact and scope out a real `✅ merged`.

    Discriminating: with the group probe removed, this reads `lost`.
    """
    # A SESSION LEADER that forks a child into its own group and then EXITS: the
    # leader is gone, `child == 0` (nothing named the rail yet), the group is not.
    leader = subprocess.Popen(
        ["bash", "-c", "sleep 30 & exit 0"],
        start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        leader.wait(timeout=10)
        assert _group_alive(leader.pid), "the forked child left the group"
        assert rd._pid_alive(leader.pid, "") is False      # the WRAPPER is gone
        assert rd._rail_alive(leader.pid, 0, str(tmp_path / "rail-o-r-77.md"))
    finally:
        with contextlib.suppress(OSError):
            os.killpg(leader.pid, signal.SIGKILL)


def _group_alive(pgid: int) -> bool:
    """Does the process GROUP still have a live member? `killpg(…, 0)` answers
    for the group, not for its leader — which is the whole point of the arm."""
    try:
        os.killpg(pgid, 0)
    except OSError:
        return False
    return True


def test_the_start_time_probe_reads_ps_where_there_is_no_procfs(tmp_path, monkeypatch):
    """⛔ THE IDENTITY TEST'S ONLY MACOS PATH, AND ITS ONE PARSE.

    The box class this module is written for (macOS lanes) has NO procfs, so the pid
    start time comes from `ps -o lstart=` — where `ps` pads the day to two characters
    (`Mon Nov  3 …`). If that parse fails, `_pid_started_epoch` returns None, the
    claim is kept (correct fail direction), and the recycled-pid wedge is silently
    back on exactly the boxes that run the fleet — so the parse is pinned here, with
    an unparseable answer asserted to degrade rather than guess.
    """
    orig = rd.Path

    def no_procfs(p="/", *a, **k):
        if str(p).startswith("/proc"):
            raise OSError("this box has no procfs")
        return orig(p, *a, **k)

    monkeypatch.setattr(rd, "Path", no_procfs)
    monkeypatch.setattr(rd, "_ps", lambda *args: "Mon Nov  3 01:05:15 2026\n")
    assert rd._pid_started_epoch(4242) == time.mktime(
        time.strptime("Mon Nov 3 01:05:15 2026", "%a %b %d %H:%M:%S %Y"))

    # An answer we cannot read must NOT be guessed at: None => keep the claim.
    monkeypatch.setattr(rd, "_ps", lambda *args: "not a timestamp\n")
    assert rd._pid_started_epoch(4242) is None
    assert rd._pid_is_a_stranger(4242, "2026-01-01T00:00:00Z") is False


def test_a_recycled_pid_without_procfs_does_not_read_waiting(tmp_path, monkeypatch):
    """⛔ ON A BOX WITHOUT PROCFS the argv marker was silently dropped.

    `_pid_alive` consulted only the `ps` STAT letter there, so ANY live pid
    inherited from a dead rail read `waiting` — reported alive forever AND
    refusing every later `start` on the PR (the unrailable wedge this module
    exists to remove; `SPAWNING_TTL_S` cannot age a pid-based `waiting` out).
    The wrapper NEVER execs, so its argv carries the artifact path for its whole
    life and IS a usable identity test for it (unlike for the rail, which execs
    — that is what `pgid` is for).
    """
    monkeypatch.setattr(rd, "_proc_stat_letter", lambda pid: None)
    monkeypatch.setattr(rd, "_proc_cmdline", lambda pid: None)
    monkeypatch.setattr(rd, "_PS_USABLE", True)
    me = os.getpid()
    marker = str(tmp_path / "rail-o-r-91.md")

    def unrelated(*args):
        return "some-unrelated-process --flag\n" if "command=" in args else f"{me} S\n"

    monkeypatch.setattr(rd, "_ps", unrelated)
    assert rd._pid_alive(me, marker) is False        # pre-fix: True, forever

    def ours(*args):
        return f"bash -c ... {marker}\n" if "command=" in args else f"{me} S\n"

    monkeypatch.setattr(rd, "_ps", ours)
    assert rd._pid_alive(me, marker) is True         # the real wrapper still reads alive


def test_a_LIVE_pid_in_ANOTHER_group_is_not_our_rail():
    """⛔ THE RECYCLED-PID CASE `_in_group` EXISTS FOR — the one no other test ASKS it about.

    `_in_group` is the module's stated *"THE ownership test for the rail
    process"*, and on the `pgid=` path it is the ONLY identity probe: the
    `os.kill(pid, 0)` existence check is skipped whenever `pgid` is supplied
    (see `_pid_alive`), so a LIVE pid recycled into some other process group
    reads `waiting` — reported alive forever AND refusing every later `start`
    on the PR, the unrailable wedge this module exists to remove.

    ⛔ WHY THIS TEST EXISTS, stated by BEHAVIOUR and not by a count, because the
    count this paragraph used to carry is not re-derivable. It said *"replacing
    `_in_group`'s body with `return True` left `tests/test_rail_detach.py` at 46
    passed, i.e. NOTHING pinned the rejection"*. Measured at this head, the
    mutant reds **2** tests — this one and `origin/main`'s
    `test_a_permission_denied_probe_keeps_the_claim_and_only_esrch_is_gone`
    (`assert rd._in_group(pid, 42) is False`, its own control arm), which the
    forward-merge brought in — so "NOTHING pinned it" is false at this head, and
    the figure is a frozen count of a file the merge has since grown (45
    `^def test_` at `e49e641f4^`, 46 at `e49e641f4`; ⛔ no live figure is restated
    here — a population count drifts with every test added, so it belongs keyed
    to the commit it was measured at or nowhere). ⛔ TWO COUNTING
    DEFINITIONS meet in this paragraph and neither figure is wrong: the `46
    passed` quoted above is pytest's COLLECTED population at `e49e641f4^` (45
    `^def test_` functions, one of them carrying a 2-case `parametrize`, so
    45 + 1 = 46), while the parenthetical counts `^def test_` definitions. Do
    not "correct" one figure to match the other. Reproduce with a plugin that
    sets `rail_detach._in_group = lambda pid, pgid: True`; measured at
    `71f6d4acb` that reads `2 failed, 96 passed`, and only the NAMES matter —
    this test and that control arm red, nothing else.

    The gap this test fills survives that correction, and it is narrower than the
    old sentence claimed: every other test reaches `_in_group`'s rejecting
    direction only through `getpgid` RAISING (`ProcessLookupError` for a reaped
    pid) or by monkeypatching `os.getpgid` — never by ASKING it about a LIVE pid
    in a foreign group. That non-raising `False` arm is the one that stops a
    recycled live pid reading `waiting`, which is the unrailable wedge stated
    above. The killed/fork-window/recycled tests all exercise the TRUE direction
    (a child that really is in the group) or the argv-marker path with
    `pgid=None`; the dead-pid case is caught by `_proc_stat_letter`/procfs.

    No procfs, no `ps`: a child started in its OWN session is a live pid this
    process does not own, which is the whole predicate.
    """
    # A sleeping stub on this interpreter's own argv, so no suppression is
    # needed: `S` is not in this repo's ruff select (pyproject `[tool.ruff.lint]`),
    # and naming a rule that is not enabled reds `RUF100` here in CI.
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True)
    try:
        # `setsid` runs in the child AFTER fork, so poll until the group splits.
        deadline = time.time() + 10
        while True:
            try:
                child_pgid = os.getpgid(child.pid)
            except OSError:                        # pragma: no cover - not reaped
                child_pgid = None
            if child_pgid is not None and child_pgid != os.getpgid(0):
                break
            assert time.time() < deadline, (
                "the stub child never left our process group, so this pin "
                "cannot test group membership")
            time.sleep(0.02)
        assert rd._in_group(child.pid, child_pgid) is True
        assert rd._in_group(child.pid, os.getpgid(0)) is False
        # LIVENESS-ONLY: the pid is alive, and only identity rejects it — so a
        # pass here cannot be `_pid_alive` deciding the process is dead.
        assert rd._pid_alive(child.pid) is True
        assert rd._pid_alive(child.pid, pgid=os.getpgid(0)) is False
        # the TRUE direction, so the predicate is not `return False` either.
        assert rd._pid_alive(os.getpid(), pgid=os.getpgid(0)) is True
    finally:
        child.kill()
        child.wait(timeout=30)



def test_the_no_procfs_argv_probe_asks_ps_for_the_full_width(tmp_path, monkeypatch):
    """⛔ BSD/macOS `ps` TRUNCATES `command=` TO THE DISPLAY WIDTH (80 when stdout
    is not a tty), which cuts the artifact path off BEFORE the marker.

    So on the box class this module says lanes run on — macOS, no procfs, this is
    the ONLY identity probe for the wrapper — a LIVE wrapper reads as "not ours"
    and `collect` draws `lost` from it. `-ww` is what makes the marker readable at
    all. Pinned on the argv (`ps` must be ASKED for the full width), never by
    monkeypatching `_ps` away — that is why the sibling arm could not see it.
    """
    seen: list[tuple[str, ...]] = []

    def recording_ps(*args):
        seen.append(args)
        return "some-unrelated-process --flag\n"   # a truncated line: no marker

    monkeypatch.setattr(rd, "_proc_stat_letter", lambda pid: None)
    monkeypatch.setattr(rd, "_proc_cmdline", lambda pid: None)
    monkeypatch.setattr(rd, "_ps_stat", lambda pid: "S")
    monkeypatch.setattr(rd, "_ps", recording_ps)
    marker = str(tmp_path / "rail-o-r-92.md")
    assert rd._pid_alive(os.getpid(), marker) is False
    assert any("command=" in argv for argv in seen), seen
    assert all("-ww" in argv for argv in seen if "command=" in argv), seen


def test_an_unspawnable_launch_is_an_environment_error_not_a_gate_verdict(
    tmp_path, monkeypatch, capsys
):
    """⛔ EXIT 1 IS THE MACHINE GATE ("not merged"). A `Popen` that raised is not.

    `main()` caught only `Refused`, so an `OSError` from the spawn (EAGAIN under
    cell load — named in this module's own `SPAWNING` comment — ENOMEM, a
    vanished CWD) escaped as a traceback and exited 1, which `collect
    --assert-merged` publishes as "not merged". Same mis-signal class as the
    missing-`gh` fix, which already has a dedicated test.
    """
    monkeypatch.setenv("CELL_HOME", str(tmp_path))

    def boom(*a, **kw):
        raise BlockingIOError(11, "Resource temporarily unavailable")

    monkeypatch.setattr(rd, "launch", boom)
    rc = rd.main(["start", "77", "--repo", "o/r", "--cwd", str(tmp_path)])
    assert rc == 2, f"pre-fix: exits 1 (the --assert-merged gate code), got {rc}"
    assert "Resource temporarily unavailable" in capsys.readouterr().err


def test_announced_merge_survives_a_missing_exit_line(tmp_path):
    """The rail prints `✅ merged …` only AFTER the API confirms state=MERGED. A
    crash between that line and the EXIT write is still a merge — but anything
    less stays `lost`."""
    body = textwrap.dedent(
        """#!/usr/bin/env bash
        echo "admin-merge: ✅ merged PR #55 at feedface123456 (state=MERGED confirmed via the API)"
        kill -9 $PPID
        """
    )
    co = make_checkout(tmp_path, body=body)
    sd = tmp_path / "state"
    rd.launch(55, cwd=co, state_dir=sd, repo="o/r")
    st = wait_state(sd, 55, rd.MERGED)
    assert st.state == rd.MERGED and st.merge_sha == "feedface123456"
    assert "EXIT line was never written" in st.reason


def test_a_rail_that_exits_during_the_liveness_probe_keeps_its_exit_code(tmp_path, monkeypatch):
    """The artifact is read BEFORE the liveness probe, so a rail that appends
    `EXIT=` and exits *while* that probe runs is seen as GONE holding a read that
    predates its EXIT — `merged` with `exit_code=None` and the false reason "the
    EXIT line was never written", for a rail that wrote one.

    The state stayed `merged` (the rail's own success line was in the log), so no
    gate moved; what was wrong was the exit code and the explanation. It is not
    theoretical: it made `test_merged_state_names_the_sha_the_rail_printed`
    flake 2/2 at load 27 on 2026-11-03 while passing in isolation, i.e. an
    intermittent red on a CI-gating file for a diff that changed nothing.

    The race is reproduced WITHOUT a race — the stub writes the EXIT line and
    answers "gone" in the same call, which is exactly the interleaving that the
    real code hit by luck.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    ln = launch(sd, co, pr=74)
    assert wait_state(sd, 74, rd.MERGED).state == rd.MERGED   # the rail merged…
    art = rd.artifact_path(sd, "o/r", 74)
    # …so strip its bookkeeping and let the probe race it, as it did in the wild.
    while ln.pid and rd._pid_alive(ln.pid, str(art)):
        time.sleep(0.05)
    text = art.read_text()
    attempt = rd.read_field(text, "ATTEMPT_ID")
    assert attempt, "launch must stamp the attempt id"
    assert "EXIT=" in text and text.count("EXIT=") == 1
    art.write_text(
        "\n".join(
            ln for ln in text.splitlines()
            if not ln.startswith(("EXIT=", "EXIT_ATTEMPT="))
        ) + "\n"
    )
    assert "EXIT=" not in art.read_text()                     # the stale read

    def exits_during_the_probe(pid, marker="", **kw):
        # `**kw` because the probe's ownership test grew a group-membership arm
        # (`pgid=`); this fake answers "gone" for BOTH processes, which is the
        # interleaving under test.
        with art.open("a", encoding="utf-8") as fh:
            # the wrapper writes the attempt tag IMMEDIATELY BEFORE its EXIT
            fh.write(f"EXIT_ATTEMPT={attempt}\nEXIT=0\nFINISHED_AT={rd._iso_now()}\n")
        return False                                          # …and it is GONE

    monkeypatch.setattr(rd, "_pid_alive", exits_during_the_probe)
    st = rd.collect(sd, "o/r", 74)
    assert st.state == rd.MERGED
    assert st.exit_code == 0, st.as_dict()        # pre-fix: None (the stale read)
    assert st.merge_sha == "deadbeefcafe1234"
    assert st.reason == "", st.reason            # not the "never written" lie


def _reaped_pid() -> int:
    """A pid that is provably GONE: a child that has already been reaped.

    `/bin/true` is not on every box (measured: absent on this one), so the child is
    the interpreter already running the suite.
    """
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    return proc.pid


def test_a_permission_denied_probe_keeps_the_claim_and_only_esrch_is_gone(monkeypatch):
    """⛔ THE ERRNO IS THE ANSWER, and this probe used to read `PermissionError`
    as `gone`. `kill(pid, 0)` / `getpgid(pid)` raise it for a process that
    **exists** and that we may not signal — a rail that is still merging — so a
    blanket `except OSError` drew `lost` for a live rail. `lost` is exactly the
    state `start` ACCEPTS, so the next beat truncates the artifact and scopes a
    real merged line out for good (#3061, #2235). The canonical implementation is
    `psutil.pid_exists`: True on `PermissionError`, False only on
    `ProcessLookupError`.

    The control arm is the SAME shape raising ESRCH: it must still answer False,
    so this cannot be satisfied by a probe that answers True for everything.
    """
    pid = 900001

    def raising(exc):
        def probe(p, *a):
            raise exc
        return probe

    denied = PermissionError(1, "Operation not permitted")
    gone = ProcessLookupError(3, "No such process")

    monkeypatch.setattr(os, "kill", raising(denied))
    assert rd._pid_alive(pid, "/state/rail-o-r-1.md") is True    # pre-fix: False
    monkeypatch.setattr(os, "kill", raising(gone))
    assert rd._pid_alive(pid, "/state/rail-o-r-1.md") is False   # the control

    monkeypatch.setattr(os, "getpgid", raising(denied))
    assert rd._in_group(pid, 42) is True                          # pre-fix: False
    monkeypatch.setattr(os, "getpgid", raising(gone))
    assert rd._in_group(pid, 42) is False                         # the control


def _deny_probes(monkeypatch, denied: set[int]) -> None:
    """Make BOTH liveness probes answer `EPERM` for `denied` — the one errno that
    means "the process EXISTS and you may not signal it". Pids outside the set are
    probed for real, so a teardown can still kill the rail."""
    real_kill, real_getpgid = os.kill, os.getpgid

    def kill(pid, sig):
        if pid in denied:
            raise PermissionError(1, "Operation not permitted")
        return real_kill(pid, sig)

    def getpgid(pid):
        if pid in denied:
            raise PermissionError(1, "Operation not permitted")
        return real_getpgid(pid)

    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(os, "getpgid", getpgid)


def test_a_live_rail_whose_wrapper_probe_is_denied_is_waiting_not_lost(tmp_path, monkeypatch):
    """The consequence of the errno arm above, at the verb the fleet reads: a rail
    that is LIVE and merging, on a box where `kill(pid, 0)` is DENIED for both its
    wrapper and the rail itself, must read `waiting`. Pre-fix it read `lost` — the
    durable-wrong direction, since `lost` releases `start` on a merged PR.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    monkeypatch.setenv("FAKE_RAIL_SLEEP", "20")
    ln = launch(sd, co, pr=76)
    art = rd.artifact_path(sd, "o/r", 76)
    child = int(wait_field(art, "CHILD_PID") or 0)
    assert child > 0, art.read_text()
    denied = {ln.pid, child}
    _deny_probes(monkeypatch, denied)
    try:
        st = rd.collect(sd, "o/r", 76)
    finally:
        denied.clear()      # teardown needs the real probes back
    assert st.state == rd.WAITING, st.as_dict()     # pre-fix: `lost`
    assert st.exit_code is None, st.as_dict()
    # …and the claim LIFTS when the rail really exits: the probe is one-sided, not
    # one-way. A killed rail writes no EXIT, so this is `lost`.
    kill_group(ln.pid)
    st2 = wait_state(sd, 76, rd.LOST)
    assert st2.state == rd.LOST, st2.as_dict()


def test_a_denied_group_probe_for_the_rail_itself_keeps_the_claim(tmp_path, monkeypatch):
    """THE OTHER ARM — the one `_in_group` carries. The wrapper here is genuinely
    GONE (a reaped pid), so `kill(pid, 0)` cannot answer for the rail at all and the
    only probe left is `getpgid(CHILD_PID)`, which raises `EPERM` for a LIVE rail
    the caller may not inspect. Reading that as "not ours" drew `lost` for a rail
    that is still merging; it must keep the claim, and it must still lift when the
    process genuinely exits.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 77)
    logp = rd.log_path(sd, "o/r", 77)
    rail = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"],
                            start_new_session=True, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        art.write_text(
            f"PR=77\nREPO=o/r\nHEAD=deadbeef\nSTARTED_AT={rd._iso_now()}\n"
            f"LOG={logp}\nATTEMPT_ID=t77\nPID={_reaped_pid()}\n"
            f"CHILD_PID={rail.pid}\n",
            encoding="utf-8",
        )
        logp.write_text(f"\n===== rail attempt started {rd._iso_now()} (detached) =====\n",
                        encoding="utf-8")
        denied = {rail.pid}
        _deny_probes(monkeypatch, denied)
        try:
            st = rd.collect(sd, "o/r", 77)
        finally:
            denied.clear()
        assert st.state == rd.WAITING, st.as_dict()   # pre-fix: `lost`
        rail.kill()
        rail.wait()
        st2 = rd.collect(sd, "o/r", 77)               # the claim lifts on a real exit
        assert st2.state == rd.LOST, st2.as_dict()
    finally:
        with contextlib.suppress(OSError):
            rail.kill()
            rail.wait()


def test_a_merge_written_during_the_second_probe_window_is_not_lost(tmp_path, monkeypatch):
    """The two-pass loop exists so a `lost` conclusion is drawn only from a read
    taken AFTER the liveness probe. It still failed on the LAST pass: the `continue`
    that detected the move threw that read away, so every conclusion below read the
    PRE-probe `text`/`log`/`exits` — a rail that wrote its merge line and then
    `EXIT=` in the second window was reported `lost`, i.e. a real merge scoped out
    and `start` released on a merged PR (#2235, #3061).

    The race is reproduced WITHOUT a race: the probe writes `CHILD_PID=` in the
    first window (so the loop must take its second pass) and the merge line plus
    `EXIT=` in the second, then answers "gone" both times. Pre-fix: `lost` with no
    exit code and no sha; post-fix: the rail's own evidence.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 75)
    logp = rd.log_path(sd, "o/r", 75)
    wrapper, child = _reaped_pid(), _reaped_pid()
    art.write_text(
        f"PR=75\nREPO=o/r\nHEAD=deadbeef\nSTARTED_AT={rd._iso_now()}\n"
        f"LOG={logp}\nATTEMPT_ID=t75\nPID={wrapper}\n",
        encoding="utf-8",
    )
    logp.write_text(
        f"\n===== rail attempt started {rd._iso_now()} (detached) =====\n"
        "admin-merge: … FIRST-CHECKS wait: run 41557371710 is pending\n",
        encoding="utf-8",
    )
    calls: list[int] = []

    def probe(leader, child_pid, marker, **_kw):
        calls.append(1)
        if len(calls) == 1:
            with art.open("a", encoding="utf-8") as fh:
                fh.write(f"CHILD_PID={child}\n")
        else:
            with logp.open("a", encoding="utf-8") as fh:
                fh.write("admin-merge: ✅ merged PR #75 at deadbeefcafe1234 "
                         "(state=MERGED confirmed via the API)\n")
            with art.open("a", encoding="utf-8") as fh:
                fh.write("EXIT_ATTEMPT=t75\nEXIT=0\n")
        return False                                     # GONE, both times

    monkeypatch.setattr(rd, "_rail_alive", probe)
    st = rd.collect(sd, "o/r", 75)
    assert st.state == rd.MERGED, st.as_dict()            # pre-fix: `lost`
    assert st.exit_code == 0, st.as_dict()                # pre-fix: None
    assert st.merge_sha == "deadbeefcafe1234", st.as_dict()   # the rail's own line


def test_previous_attempts_merge_line_is_not_inherited(tmp_path):
    """The log is append-only across attempts: a NEW attempt that dies with no EXIT
    must not inherit the OLD attempt's `✅ merged` — that is forged evidence."""
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    rd.launch(61, cwd=co, state_dir=sd, repo="o/r")
    # ⛔ WAIT FOR THE SETTLED READ: the rail prints its success line BEFORE its
    # wrapper appends `EXIT=`, and while the wrapper is still alive `collect`
    # reports `waiting` there — a live carrier outranks an announcement (see the
    # STATES table), so `merged` with `exit_code=None` is the POST-exit shape.
    # Without the predicate this arm may snapshot the handoff
    # and then race the previous wrapper's append against the new launch's
    # truncation — the flake the sibling arm above was already fixed for.
    assert wait_state(sd, 61, rd.MERGED,
                      pred=lambda s: s.exit_code is not None).state == rd.MERGED
    os.environ["FAKE_RAIL_SLEEP"] = "30"
    try:
        ln = rd.launch(61, cwd=co, state_dir=sd, repo="o/r")    # attempt 2: hangs…
    finally:
        os.environ.pop("FAKE_RAIL_SLEEP", None)
    time.sleep(0.2)
    kill_group(ln.pid)                                           # …and is killed
    time.sleep(0.3)
    st = rd.collect(sd, "o/r", 61)
    assert st.state == rd.LOST, st.as_dict()
    assert st.merge_sha == ""


def test_a_new_attempt_truncates_the_log(tmp_path):
    """The log is APPEND-ONLY ACROSS ATTEMPTS and read WHOLE on every `collect`,
    so a PR railed a dozen times pays every previous attempt's bytes on every
    verdict read, forever. `launch` truncates it in the same flock as the artifact.

    Discriminating both ways: the old attempt's mark must be gone (one mark, not
    two) AND its `✅ merged` sha must be unreadable from the new attempt's log —
    the second half is the forged-evidence direction the scoping guards.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    rd.launch(64, cwd=co, state_dir=sd, repo="o/r")
    first = wait_state(sd, 64, rd.MERGED, pred=lambda s: s.exit_code is not None)
    assert first.state == rd.MERGED, first.as_dict()
    logp = Path(rd.read_field(rd.artifact_path(sd, "o/r", 64).read_text(), "LOG"))
    assert "deadbeefcafe1234" in logp.read_text(encoding="utf-8")

    os.environ["FAKE_RAIL_SLEEP"] = "30"
    try:
        ln = rd.launch(64, cwd=co, state_dir=sd, repo="o/r")   # attempt 2
    finally:
        os.environ.pop("FAKE_RAIL_SLEEP", None)
    try:
        text = logp.read_text(encoding="utf-8")
        assert text.count(rd.ATTEMPT_MARK) == 1, text      # pre-fix: 2
        assert "deadbeefcafe1234" not in text, text        # pre-fix: inherited
    finally:
        kill_group(ln.pid)


def test_a_pid_that_lands_during_the_probe_is_probed_not_assumed_dead(tmp_path, monkeypatch):
    """⛔ `PID=` IS A SEPARATE WRITE FROM `SPAWNING=1`, AND IT IS WHAT A PROBE READS.

    `launch` writes `SPAWNING=1` + `ATTEMPT_ID`, spawns, and only THEN appends
    `PID=`/`SID=`. A `collect` landing between those writes reads "no pid", and if
    the pid arrives DURING the probe and is adopted without another pass, the
    post-loop arms (keyed on `pid == 0`) skip the `SPAWNING` protection and
    conclude `lost` for a LIVE wrapper — the state `start` ACCEPTS (#3061), plus
    the truncation that scopes a real `✅ merged` out (#2235).

    Reproduced without a race: the probe appends only `PID=<live wrapper>` and
    answers gone for the unnamed read, then answers alive for the named one.
    Pre-fix (no `PID` term in the move test) `lost`; post-fix `waiting` — and the
    published pid must be the artifact's, not 0.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 73)
    logp = rd.log_path(sd, "o/r", 73)
    art.write_text(f"PR=73\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nLOG={logp}\n"
                   f"SPAWNING=1\nATTEMPT_ID=a73\n", encoding="utf-8")
    logp.write_text(f"\n{rd.ATTEMPT_MARK} x attempt=a73 (detached) =====\n",
                    encoding="utf-8")
    wrapper = subprocess.Popen(["/bin/sleep", "20"], start_new_session=True,
                               stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    try:
        def probe(leader, child, marker, **_kw):
            if leader == 0:
                with art.open("a", encoding="utf-8") as fh:
                    fh.write(f"PID={wrapper.pid}\nSID={wrapper.pid}\n")
                return False                      # nothing named yet
            return leader == wrapper.pid          # the named wrapper answers

        monkeypatch.setattr(rd, "_rail_alive", probe)
        st = rd.collect(sd, "o/r", 73)
        assert st.state == rd.WAITING, st.as_dict()    # pre-fix: LOST
        assert st.pid == wrapper.pid, st.as_dict()     # the published pid is real
    finally:
        wrapper.kill()
        wrapper.wait()


def test_a_relaunch_during_the_probe_is_aged_from_the_adopted_artifact(tmp_path, monkeypatch):
    """The verdict's skeleton must describe the snapshot it concludes from.

    `st` is built at the top of a loop iteration; the loop then ADOPTS a fresh
    snapshot. Concluding from the old skeleton aged a re-launch that rewrote
    `STARTED_AT` during the probe against the PREVIOUS attempt's stamp, so a launch
    that had just begun with an hour-old predecessor read `lost` — "no rail ever
    started" — for a rail that is inside its spawn window.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 72)
    logp = rd.log_path(sd, "o/r", 72)
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    art.write_text(f"PR=72\nREPO=o/r\nSTARTED_AT={old}\nLOG={logp}\n"
                   f"SPAWNING=1\nATTEMPT_ID=a72\n", encoding="utf-8")
    logp.write_text(f"\n{rd.ATTEMPT_MARK} x attempt=a72 (detached) =====\n",
                    encoding="utf-8")

    def probe(leader, child, marker, **_kw):
        # a RE-LAUNCH rewrites the artifact mid-probe: fresh stamp, still spawning
        art.write_text(f"PR=72\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nLOG={logp}\n"
                       f"SPAWNING=1\nATTEMPT_ID=a72b\n", encoding="utf-8")
        return False

    monkeypatch.setattr(rd, "_rail_alive", probe)
    st = rd.collect(sd, "o/r", 72)
    assert st.state == rd.WAITING, st.as_dict()          # pre-fix: LOST
    assert "launching" in st.reason, st.reason


def test_a_replaced_artifact_is_never_concluded_upon(tmp_path, monkeypatch):
    """⛔ `launch` REPLACES THE ARTIFACT WHOLESALE, SO A NEW ATTEMPT IS A MOVE.

    The move test covered `EXIT=`, an arriving `PID=` and an arriving `CHILD_PID=`,
    but not a REPLACED record. A `start` landing inside this collect's probe window
    therefore handed the loop an artifact with a new `ATTEMPT_ID` and a new `PID`
    that no probe here had examined — and the loop broke anyway and published
    `lost` for the live replacement, printing that very pid in the same verdict (the
    state `start` accepts: #3061).

    Reproduced without a race: probe 1 rewrites the artifact (new attempt, live
    pid) and answers gone for the OLD pid; the next pass must examine the new one.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 71)
    logp = rd.log_path(sd, "o/r", 71)
    art.write_text(f"PR=71\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nLOG={logp}\n"
                   f"ATTEMPT_ID=old\nPID={_reaped_pid()}\n", encoding="utf-8")
    logp.write_text(f"\n{rd.ATTEMPT_MARK} x attempt=old (detached) =====\n",
                    encoding="utf-8")
    wrapper = subprocess.Popen(["/bin/sleep", "20"], start_new_session=True,
                               stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    try:
        def probe(leader, child, marker, **_kw):
            if leader != wrapper.pid:
                art.write_text(
                    f"PR=71\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nLOG={logp}\n"
                    f"ATTEMPT_ID=new\nPID={wrapper.pid}\nSID={wrapper.pid}\n",
                    encoding="utf-8")
                return False                       # the OLD pid is gone (it is)
            return True                            # the NEW wrapper is alive

        monkeypatch.setattr(rd, "_rail_alive", probe)
        st = rd.collect(sd, "o/r", 71)
        assert st.state == rd.WAITING, st.as_dict()     # pre-fix: LOST
        assert st.pid == wrapper.pid, st.as_dict()
        # …and it is a REAL verdict for the new attempt, not the generic
        # "re-collect" notice: a replaced identity forces another pass, which
        # probes the pid the artifact now names.
        assert "REPLACED" not in st.reason, st.reason
    finally:
        wrapper.kill()
        wrapper.wait()


def test_a_record_replaced_on_the_last_pass_is_a_recollect_not_a_verdict(tmp_path, monkeypatch):
    """The LAST pass cannot re-probe, so a record that changed identity there must
    not be concluded upon at all: the honest answer is `waiting` (so `start` stays
    refused and nothing is truncated) plus a reason naming the one action, a
    re-collect — which probes the new record. Pre-fix this published `lost` for the
    live replacement."""
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 70)
    logp = rd.log_path(sd, "o/r", 70)
    dead1, dead2 = _reaped_pid(), _reaped_pid()
    art.write_text(f"PR=70\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nLOG={logp}\n"
                   f"ATTEMPT_ID=old\nPID={dead1}\n", encoding="utf-8")
    logp.write_text(f"\n{rd.ATTEMPT_MARK} x attempt=old (detached) =====\n",
                    encoding="utf-8")
    calls: list[int] = []

    def probe(leader, child, marker, **_kw):
        calls.append(leader)
        if len(calls) == 1:
            # pass 1: the artifact moves (new pid) → the loop takes a second pass
            art.write_text(f"PR=70\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nLOG={logp}\n"
                           f"ATTEMPT_ID=old\nPID={dead2}\n", encoding="utf-8")
        elif len(calls) == 2:
            # pass 2 (the last one that probes): a LATER `start` owns it now
            art.write_text(f"PR=70\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nLOG={logp}\n"
                           f"ATTEMPT_ID=newer\nPID={dead2}\n", encoding="utf-8")
        return False

    monkeypatch.setattr(rd, "_rail_alive", probe)
    st = rd.collect(sd, "o/r", 70)
    assert st.state == rd.WAITING, st.as_dict()      # pre-fix: LOST
    assert "REPLACED" in st.reason and "collect" in st.reason


def test_a_pid_d_claim_refusal_names_the_check_and_the_remedy(tmp_path):
    """A refusal that names no way out is a wedge with a friendly voice.

    The pid'd claim is the ONE waiting arm where a RECYCLED pid never lifts: a
    genuine wrapper lifts it by exiting, a stranger holding that number answers
    forever. Saying nothing there left the operator with `pid 0`-less prose and no
    action. The hint names the read-only check first, so the `rm` is never advice to
    unlink a live rail's own record.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    sd.mkdir(parents=True)
    art = rd.artifact_path(sd, "o/r", 95)
    # a session leader stands in for the wrapper: its pgid IS its pid, so the group
    # arm answers it (the shape `launch` creates with `start_new_session=True`).
    wrapper = subprocess.Popen(["/bin/sleep", "20"], start_new_session=True,
                               stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    try:
        art.write_text(f"PR=95\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nLOG={sd}/x.log\n"
                       f"PID={wrapper.pid}\n", encoding="utf-8")
        assert rd.collect(sd, "o/r", 95).state == rd.WAITING
        with pytest.raises(rd.Refused) as exc:
            rd.launch(95, cwd=co, state_dir=sd, repo="o/r")
        msg = str(exc.value)
        assert "ALREADY LIVE" in msg
        assert f"ps -p {wrapper.pid}" in msg        # the read-only check, first
        assert "rm " in msg and str(art) in msg      # then the conditional remedy
    finally:
        wrapper.kill()
        wrapper.wait()


def test_the_duplicate_rail_guard_keys_on_the_repo_not_its_spelling(tmp_path, monkeypatch):
    """⛔ TWO SPELLINGS OF ONE REPO ARE ONE RAIL (#10691 review).

    A `flock` is held on the open FILE (`flock(2)`), so the guard is only as strong
    as the identity the path is keyed on — and the artifact used to be named from the
    RAW repo string. Measured at the pre-fix head: `launch(7, "o/r")` and
    `launch(7, "github.com/o/r")` wrote two records, took two locks and BOTH STARTED
    — two live rails on one PR, the #3061 collision this guard exists to refuse —
    while a third launch with the SAME spelling was correctly refused (the case
    above).

    Non-vacuity, and ⛔ THE CARRIER IS NOT THE ONE THIS DOCSTRING USED TO NAME (#10963
    review, measured). The identity is enforced at THREE sites THIS CASE DEPENDS ON, and the
    count is scoped deliberately: `_repo_key` decides repo identity at nine call sites in
    `tools/rail_detach.py` (`_record_names_repo`, `_names_this_repo`, the `collect` CLI
    resolution, and `launch`'s own `art_key` attestation among them — `:2115` is the site
    that actually fires in the `launch`-alone experiment below). THIS case's
    spelling-insensitivity is carried by the RECORD side, not the write side:
    `_waiting_record` → `_candidate_records` (`tools/rail_detach.py`) globs
    `rail-*-{pr}.md` and canonicalises each record's OWN `REPO=` line, so a record
    written under a different spelling is found by its CONTENT.
    * Reverting BOTH write-side canonicalisers — `launch`'s `repo = _repo_key(...)`
      and `_slug`'s `return _repo_key(repo).replace("/", "-")` — leaves this case
      GREEN (measured): the content scan still refuses the second spelling. The
      earlier text here claimed the opposite and its recipe, followed, would have
      removed the real carrier on the belief that the two named sites were it.
    * Reverting `launch`'s site ALONE reds this case (the record is written under the
      raw spelling, so the shared-path refusal fires first and `ALREADY LIVE` is
      absent) — the write-side sites are pinned by their own cases too.
    * The content scan is pinned by THREE sibling cases —
      `test_a_record_written_under_an_older_spelling_still_blocks`,
      `test_a_torn_record_cannot_certify_or_shadow_a_live_rail` and
      `test_the_duplicate_rail_guard_asks_a_liveness_question` — which all RED when
      it is removed, while this one stays green (measured).
    * `_slug`'s site ALONE is pinned by a case this one is not:
      `test_one_record_is_readable_under_any_spelling_of_its_repo` REDS when
      `_slug` loses its `_repo_key` (measured), which is why the negative
      results above are about THIS case and not about the keying.


    ⛔ EVERY SPELLING A CLONE URL CAN PRODUCE, because each one is a paste: `.git` is
    the suffix a clone URL always ends in and the SCP form glues the host to the owner
    (`git@github.com:o/r.git`). Both used to key a DIFFERENT artifact — measured
    `rail-o-r.git-7.md` and `rail-git@github.com:o-r.git-7.md` — so a `--repo`/cord
    value pasted from `git remote -v` started a second live rail on the same PR while
    this guard reported the PR free.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    monkeypatch.setenv("FAKE_RAIL_SLEEP", "20")
    ln = rd.launch(7, cwd=co, state_dir=sd, repo="o/r")
    try:
        assert wait_state(sd, 7, rd.WAITING).state == rd.WAITING
        for spelling in ("github.com/o/r", "GitHub.com/O/R", "o/r",
                         "o/r.git", "https://github.com/o/r.git",
                         "git@github.com:o/r.git"):
            with pytest.raises(rd.Refused) as exc:
                rd.launch(7, cwd=co, state_dir=sd, repo=spelling)
            assert "ALREADY LIVE" in str(exc.value), spelling
    finally:
        kill_group(ln.pid)


def test_one_record_is_readable_under_any_spelling_of_its_repo(tmp_path, monkeypatch):
    """The record a spelling resolves to is ONE file, in every direction.

    A canonical lock path is only half the property: if `collect` resolved a
    different file for a differently-spelled repo, the guard's holder check and the
    operator's `collect` would disagree about which record describes the rail — the
    same split, one verb over.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    monkeypatch.setenv("FAKE_RAIL_SLEEP", "4")
    ln = rd.launch(76, cwd=co, state_dir=sd, repo="o/r")
    try:
        assert (rd.artifact_path(sd, "github.com/o/r", 76)
                == rd.artifact_path(sd, "o/r", 76))
        # A clone URL's own spellings resolve to the SAME file, both directions.
        for spelling in ("o/r.git", "https://github.com/o/r.git",
                         "git@github.com:o/r.git"):
            assert (rd.artifact_path(sd, spelling, 76)
                    == rd.artifact_path(sd, "o/r", 76)), spelling
        st = rd.collect(sd, "github.com/o/r", 76)
        assert st is not None, "collect lost a record that launch just wrote"
        assert st.pr == 76
        assert st.state == rd.WAITING, st.as_dict()
    finally:
        kill_group(ln.pid)


def _guaranteed_dead_pid() -> int:
    """A pid that cannot be live here.

    ⛔ `PID=999999` IS NOT DEAD ON A BOX WITH `kernel.pid_max` 4194304 (this cell's
    value): once that number is recycled into any session/group leader, a record
    pointing at it reads LIVE through `_rail_alive`'s group fallback, and a test
    that seeds a "dead" record reds for a reason that has nothing to do with the
    code under test. `pid_max + 1` is outside the namespace on procfs boxes; where
    procfs is absent, a spawned-and-reaped process's pid is the best available
    answer (it may be recycled, and the assertion is then a false red, never a
    false green).
    """
    try:
        return int(Path("/proc/sys/kernel/pid_max").read_text().strip() or 0) + 1
    except (OSError, ValueError):
        dead = subprocess.Popen(["/bin/true"], start_new_session=True,
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        dead.wait()
        return dead.pid


def test_a_record_written_under_an_older_spelling_still_blocks(tmp_path):
    """⛔ A RECORD THE NEW NAME CANNOT SEE IS THE DUPLICATE-RAIL GUARD BYPASSED.

    `_slug` used to be the RAW repo string, so records written before this module
    keyed on `_repo_key` live under names like `rail-github.com-o-r-7.md`. Reading
    only `artifact_path(...)` misses them: `collect` answers `absent` (3) and
    `launch`'s duplicate guard therefore sees a free PR and starts a SECOND rail —
    the #3061 collision, moved to a version boundary rather than closed. `collect
    --assert-merged` is the fleet's MACHINE GATE and would answer 3/1 about a rail
    whose record sits on disk with a live pid in it.

    Non-vacuity: the canonical path is asserted ABSENT, so this cannot pass by the
    record happening to sit where the new code already looks. Revert
    `_artifact_for_any_spelling` to a bare `artifact_path` and every assert here
    fails (the first one on `None`).

    ⛔ THE SCAN IS ANCHORED ON `REPO=`, NOT ON THE FILENAME — the second half pins
    that a legacy-named record for a DIFFERENT repo is NOT adopted, so "same PR
    number" alone never resurrects someone else's rail.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    sd.mkdir(parents=True)
    legacy = sd / "rail-github.com-o-r-771.md"
    legacy_other = sd / "rail-someone-else-772.md"
    # a session leader stands in for the wrapper: `ps -p <pid>` answers it, which is
    # the read that keeps the record `waiting` rather than `lost`.
    live = subprocess.Popen(["/bin/sleep", "20"], start_new_session=True,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        legacy.write_text(f"PR=771\nREPO=github.com/o/r\nSTARTED_AT={rd._iso_now()}\n"
                          f"LOG={sd}/rail-github.com-o-r-771.log\nPID={live.pid}\n",
                          encoding="utf-8")
        legacy_other.write_text(f"PR=772\nREPO=someone/else\nSTARTED_AT={rd._iso_now()}\n"
                                f"PID={live.pid}\n", encoding="utf-8")

        assert not rd.artifact_path(sd, "o/r", 771).exists(), \
            "fixture is not the legacy-spelling shape: the canonical path exists"

        # collect reads it under EITHER spelling of the query.
        for spelling in ("o/r", "github.com/o/r", "git@github.com:o/r.git"):
            st = rd.collect(sd, spelling, 771)
            assert st is not None, f"collect lost the record for {spelling!r}"
            assert st.state == rd.WAITING, (spelling, st.as_dict())
            assert st.artifact == str(legacy), (spelling, st.artifact)

        # …and the duplicate-rail guard REFUSES a second launch on that PR.
        with pytest.raises(rd.Refused) as exc:
            rd.launch(771, cwd=co, state_dir=sd, repo="o/r")
        assert "ALREADY LIVE" in str(exc.value)
        assert str(legacy) in str(exc.value), "the refusal must name the record it read"
        assert not rd.artifact_path(sd, "o/r", 771).exists(), \
            "the refused launch wrote a second record for one PR"

        # The REPO= anchor: same PR number, different repo, is NOT this repo's rail.
        assert rd.collect(sd, "o/r", 772) is None
        other = rd.collect(sd, "someone/else", 772)
        assert other is not None and other.artifact == str(legacy_other)

        # ── COEXISTENCE: canonical + legacy for ONE PR, and the choice is LIVENESS
        # ⛔ MEASURED PRE-FIX: a DEAD canonical record beside a LIVE legacy one read
        # `lost` (the record alone read `waiting`), so `launch`'s guard accepted the
        # PR and started a SECOND rail — the module's own P1. Both orders are pinned,
        # because "prefer the canonical path" fails in exactly one of them.
        canonical = rd.artifact_path(sd, "o/r", 771)
        dead_pid = _guaranteed_dead_pid()
        legacy.write_text(f"PR=771\nREPO=github.com/o/r\nSTARTED_AT={rd._iso_now()}\n"
                          f"PID={dead_pid}\n", encoding="utf-8")        # DEAD
        canonical.write_text(f"PR=771\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\n"
                             f"PID={live.pid}\n", encoding="utf-8")     # LIVE
        st = rd.collect(sd, "o/r", 771)
        assert st is not None and st.state == rd.WAITING, (st and st.as_dict())
        assert st.artifact == str(canonical), "the live record must win"

        canonical.write_text(f"PR=771\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\n"
                             f"PID={dead_pid}\n", encoding="utf-8")     # DEAD
        legacy.write_text(f"PR=771\nREPO=github.com/o/r\nSTARTED_AT={rd._iso_now()}\n"
                          f"PID={live.pid}\n", encoding="utf-8")        # LIVE
        st = rd.collect(sd, "o/r", 771)
        assert st is not None and st.state == rd.WAITING, (st and st.as_dict())
        assert st.artifact == str(legacy), "a dead canonical record must not shadow a live one"
        with pytest.raises(rd.Refused) as exc:
            rd.launch(771, cwd=co, state_dir=sd, repo="o/r")
        assert "ALREADY LIVE" in str(exc.value), str(exc.value)

        # …and the CLI's disk-first resolution counts REPOS, not FILES: one repo, two
        # spellings, no `gh` — the answer is on disk, so `collect` must not ask (and
        # must not exit 2 for an ambiguity that is not one).
        assert rd.main(["collect", "771", "--state-dir", str(sd)]) == 0

        # Both dead: the record is still reported (never "absent") — the guard reads
        # it as finished, which is what lets the next `start` through.
        legacy.write_text(f"PR=771\nREPO=github.com/o/r\nSTARTED_AT={rd._iso_now()}\n"
                          f"PID={dead_pid}\n", encoding="utf-8")
        st = rd.collect(sd, "o/r", 771)
        assert st is not None and st.state == rd.LOST, (st and st.as_dict())

        # ── A TERMINAL MERGE OUTRANKS A LIVE-LOOKING STALE RECORD
        # ⛔ MEASURED ON d957903d4: ranking by LIVENESS ALONE hid a merged canonical
        # record behind a stale legacy record whose pid read live through
        # `_rail_alive`'s group fallback (a record with no `CHILD_PID`/`STARTED_AT`
        # gets the unbounded group claim). `collect --assert-merged` then answered 1 —
        # "did NOT merge" — about a merged PR, with the merge sha unreachable. A
        # merge is monotone: nothing downstream can un-merge it, while `waiting` is
        # not evidence of anything.
        sd2 = tmp_path / "state2"
        sd2.mkdir()
        canon2 = rd.artifact_path(sd2, "o/r", 55)
        logp2 = canon2.with_suffix(".log")
        canon2.write_text(
            f"PR=55\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nATTEMPT_ID=abc\n"
            f"EXIT_ATTEMPT=abc\nEXIT=0\nLOG={logp2}\n", encoding="utf-8")
        logp2.write_text(
            f"\n{rd.ATTEMPT_MARK} x attempt=abc (detached) =====\n"
            "admin-merge: ✅ merged PR #55 at deadbeefcafe1234 "
            "(state=MERGED confirmed via the API)\n", encoding="utf-8")
        # …and a stale legacy record that READS LIVE (no STARTED_AT ⇒ unbounded
        # group claim, and `live` is a session leader whose group answers).
        stale = sd2 / "rail-github.com-o-r-55.md"
        stale.write_text(f"PR=55\nREPO=github.com/o/r\nPID={live.pid}\n",
                         encoding="utf-8")
        assert rd._record_is_live(stale), (
            "fixture is not the shape this arm pins: the stale record must read live")
        st = rd.collect(sd2, "o/r", 55)
        assert st is not None and st.state == rd.MERGED, (st and st.as_dict())
        assert st.merge_sha == "deadbeefcafe1234"
        assert rd.main(["collect", "55", "--state-dir", str(sd2),
                        "--assert-merged"]) == 0

        # ── THE FORK-WINDOW CARRIER (`SPAWNING=1` + `CHILD_PID`, no `PID`)
        # ⛔ MEASURED: `_record_is_live` called `_rail_alive(0, child, …)`, which is
        # False because `_pid_alive` refuses `pid <= 0` — so the LIVE carrier was
        # skipped, the dead canonical fallback won, `collect` answered `lost`, and
        # `launch` started a SECOND rail. This is the artifact an old copy leaves
        # when its launcher dies between `Popen` returning and the `PID=` append.
        sd3 = tmp_path / "state3"
        sd3.mkdir()
        carrier = sd3 / "rail-github.com-o-r-88.md"
        carrier.write_text(f"PR=88\nREPO=github.com/o/r\nSTARTED_AT={rd._iso_now()}\n"
                          f"SPAWNING=1\nCHILD_PID={live.pid}\n", encoding="utf-8")
        dead_c = rd.artifact_path(sd3, "o/r", 88)
        dead_c.write_text(f"PR=88\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\n"
                          f"PID={dead_pid}\n", encoding="utf-8")
        assert rd._record_is_live(carrier), \
            "a CHILD_PID-carried rail must read live (the fork-window shape)"
        st = rd.collect(sd3, "o/r", 88)
        assert st is not None and st.state == rd.WAITING, (st and st.as_dict())
        assert st.artifact == str(carrier), "the live carrier must outrank a dead record"
        with pytest.raises(rd.Refused) as exc:
            rd.launch(88, cwd=co, state_dir=sd3, repo="o/r")
        assert "ALREADY LIVE" in str(exc.value), str(exc.value)

        # ── TWO REPOS, ONE SLUG: `_repo_key` identity on the READ **and the WRITE**
        # ⛔ `_slug` folds `/` to `-`, so `a/b-c` and `a-b/c` share
        # `rail-a-b-c-9.md` (and its `.log`). The occupying record is MERGED on
        # purpose: that is the shape `--assert-merged` can certify, so the pre-fix
        # reading is the FALSE PASS — exit 0 for a repo that never merged — that
        # this arm is named after. A non-merged record only pins 1→3 and would
        # leave the false 0 uncovered while looking covered.
        sd4 = tmp_path / "state4"
        sd4.mkdir()
        colliding = sd4 / "rail-a-b-c-9.md"
        logp9 = colliding.with_suffix(".log")
        colliding.write_text(
            f"PR=9\nREPO=a/b-c\nSTARTED_AT={rd._iso_now()}\nATTEMPT_ID=xyz\n"
            f"EXIT_ATTEMPT=xyz\nEXIT=0\nLOG={logp9}\n", encoding="utf-8")
        logp9.write_text(
            f"\n{rd.ATTEMPT_MARK} x attempt=xyz (detached) =====\n"
            "admin-merge: ✅ merged PR #9 at deadbeefcafe1234 (state=MERGED)\n",
            encoding="utf-8")
        assert rd.collect(sd4, "a-b/c", 9) is None, \
            "another repo's record was served to this repo"
        # ⛔ PRE-FIX THIS WAS 0 — a machine gate certifying a merge that never happened.
        assert rd.main(["collect", "9", "--repo", "a-b/c", "--state-dir", str(sd4),
                        "--assert-merged"]) == 3
        # …while the repo the record NAMES is still answered, merge and all.
        st = rd.collect(sd4, "a/b-c", 9)
        assert st is not None and st.state == rd.MERGED, (st and st.as_dict())
        assert st.merge_sha == "deadbeefcafe1234"
        assert rd.main(["collect", "9", "--repo", "a/b-c", "--state-dir", str(sd4),
                        "--assert-merged"]) == 0

        # ⛔ AND `launch` MUST REFUSE THAT SHARED PATH, NOT CLOBBER IT. Declining to
        # serve the record is only half the fix: the write below is unconditional
        # (artifact `write_text`, log TRUNCATION), so with `collect` correctly silent
        # the guard passed and the launch rewrote the other repo's `REPO=`, destroyed
        # its log — including a real `✅ merged` line — and ran a second rail
        # appending into the same two files. Measured before this arm existed.
        logp9.write_text(f"\n{rd.ATTEMPT_MARK} x attempt=z (detached) =====\n"
                         "admin-merge: ✅ merged PR #9 at deadbeefcafe1234 "
                         "(state=MERGED confirmed via the API)\n", encoding="utf-8")
        before = colliding.read_text(encoding="utf-8")
        with pytest.raises(rd.Refused) as exc:
            rd.launch(9, cwd=co, state_dir=sd4, repo="a-b/c")
        assert "ANOTHER repo's record" in str(exc.value), str(exc.value)
        assert colliding.read_text(encoding="utf-8") == before, \
            "the refused launch rewrote another repo's record"
        assert "✅ merged PR #9" in logp9.read_text(encoding="utf-8"), \
            "the refused launch truncated another repo's log"

        # ── A TORN RECORD NAMES NO REPO — SO IT MAY NOT CERTIFY A MERGE
        # ⛔ `_record_names_repo` ADMITS a record whose `REPO=` is absent, on purpose:
        # a header write cut before that line (SIGKILL/ENOSPC mid-`write_text`) must
        # still read. But the record's EMPTY repo was then handed to
        # `_merge_evidence`, whose own repo cross-check is one-sided — so the log's
        # `admin-merge: repo <other>` line, the reason that check exists, was skipped
        # and `--assert-merged` exited 0 for a repo that never merged. The requested
        # repo is now the fallback, so a torn record still reads for the repo it
        # actually belongs to and certifies nothing for anyone else.
        sd5 = tmp_path / "state5"
        sd5.mkdir()
        torn = sd5 / "rail-a-b-c-9.md"
        torn_log = torn.with_suffix(".log")
        torn.write_text("# rail artifact — a/b-c#9\nPR=9\n", encoding="utf-8")
        torn_log.write_text(
            f"\n{rd.ATTEMPT_MARK} x attempt=t (detached) =====\n"
            "admin-merge: repo a/b-c (resolved from --repo)\n"
            "admin-merge: ✅ merged PR #9 at deadbeefcafe1234 (state=MERGED)\n",
            encoding="utf-8")
        st = rd.collect(sd5, "a/b-c", 9)
        assert st is not None and st.state == rd.MERGED, (st and st.as_dict())
        assert rd.main(["collect", "9", "--repo", "a/b-c", "--state-dir", str(sd5),
                        "--assert-merged"]) == 0
        # ⛔ PRE-FIX THIS WAS 0 — the torn record certified `a/b-c`'s merge to `a-b/c`.
        assert rd.main(["collect", "9", "--repo", "a-b/c", "--state-dir", str(sd5),
                        "--assert-merged"]) == 1

        # ── THE LOG IS THE OTHER HALF OF THE SHARED PATH
        # ⛔ With the artifact gone — the state this tool's OWN remedy creates
        # (`rm <artifact>` clears a claim and stops nothing) — the artifact check is
        # skipped and `launch` truncated the orphan log, including its merge line.
        sd6 = tmp_path / "state6"
        sd6.mkdir()
        orphan = sd6 / "rail-a-b-c-9.log"
        orphan.write_text(
            f"\n{rd.ATTEMPT_MARK} x attempt=o (detached) =====\n"
            "admin-merge: repo a/b-c (resolved from --repo)\n"
            "admin-merge: ✅ merged PR #9 at deadbeefcafe1234 (state=MERGED)\n",
            encoding="utf-8")
        assert not (sd6 / "rail-a-b-c-9.md").exists(), \
            "fixture is not the orphan-log shape: the artifact exists"
        with pytest.raises(rd.Refused) as exc:
            rd.launch(9, cwd=co, state_dir=sd6, repo="a-b/c")
        assert "belongs to ANOTHER repo" in str(exc.value), str(exc.value)
        assert "✅ merged PR #9" in orphan.read_text(encoding="utf-8"), \
            "the refused launch truncated an orphaned log's merge evidence"

        # ── …AND A TORN ARTIFACT MUST NOT DEFEAT THAT LOG GUARD
        # ⛔ The guard was gated on `not art.exists()`, but the ARTIFACT arm admits a
        # record whose `REPO=` is absent (a torn header must still read), so a torn
        # artifact skipped BOTH guards: `launch` rewrote it and truncated the
        # colliding repo's log. The very shape fix #1 blesses is the shape that
        # bypassed fix #2, so it is pinned here.
        sd8 = tmp_path / "state8"
        sd8.mkdir()
        torn2 = sd8 / "rail-a-b-c-9.md"
        torn2_log = torn2.with_suffix(".log")
        torn2.write_text("# rail artifact — a/b-c#9\nPR=9\n", encoding="utf-8")
        torn2_log.write_text(
            f"\n{rd.ATTEMPT_MARK} x attempt=t2 (detached) =====\n"
            "admin-merge: repo a/b-c (resolved from --repo)\n"
            "admin-merge: ✅ merged PR #9 at deadbeefcafe1234 (state=MERGED)\n",
            encoding="utf-8")
        torn_before = torn2.read_text(encoding="utf-8")
        with pytest.raises(rd.Refused) as exc:
            rd.launch(9, cwd=co, state_dir=sd8, repo="a-b/c")
        assert "belongs to ANOTHER repo" in str(exc.value), str(exc.value)
        assert torn2.read_text(encoding="utf-8") == torn_before, \
            "the refused launch rewrote a torn record"
        assert "✅ merged PR #9" in torn2_log.read_text(encoding="utf-8"), \
            "the refused launch truncated a torn record's log"

        # ── THE CARRIER ARM REQUIRES `SPAWNING=` (the shape `_collect_read` uses)
        # ⛔ Without the conjunct, `CHILD_PID` alone read LIVE — while the verdict
        # path only reaches its carrier logic under `SPAWNING=`. The two probes had
        # drifted once already, and the resolver's answer decides which record the
        # verdict is drawn from. Pinned so the conjunct cannot be dropped silently.
        sd7 = tmp_path / "state7"
        sd7.mkdir()
        no_sentinel = sd7 / "rail-o-r-91.md"
        no_sentinel.write_text(f"PR=91\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\n"
                              f"CHILD_PID={live.pid}\n", encoding="utf-8")
        assert not rd._record_is_live(no_sentinel), \
            "a CHILD_PID with no SPAWNING= is not the fork-window carrier"
        st = rd.collect(sd7, "o/r", 91)
        assert st is not None and st.state == rd.LOST, (st and st.as_dict())
    finally:
        live.kill()
        live.wait()


def test_a_torn_record_cannot_certify_or_shadow_a_live_rail(tmp_path):
    """⛔ THE REQUESTED REPO IS HALF OF EVERY MERGE VERDICT — PIN BOTH SITES.

    `_record_names_repo` admits a record whose `REPO=` is absent (a header write cut
    before that line must still read). The record then names NO repo, so every
    `_merge_evidence` call needs the REQUESTED repo as its fallback — otherwise the
    log's `admin-merge: repo <x>` cross-check is one-sided on an empty value and
    silently skipped, which is how a torn record certified a merge it never made.

    Two of those four fallback sites had no pin, so each could be reverted with the
    suite still green: the RANKING site (`_record_is_merged`) let a torn record that
    merely sits beside another repo's merged log outrank a genuinely LIVE rail,
    reporting `lost`; the EXIT site (`_exit_verdict`) certified `merged` for the
    wrong repo. The ranking one is reachable; the EXIT one is defence-in-depth (a
    torn record is a PREFIX of one `write_text`, so it cannot carry `EXIT=` in
    practice) and is pinned anyway, because "unreachable today" is how it re-opens.
    """
    live = subprocess.Popen(["/bin/sleep", "20"], start_new_session=True,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        # ── THE RANKING SITE: a torn record beside another repo's merged log must
        # NOT outrank this repo's live rail.
        sd = tmp_path / "state"
        sd.mkdir()
        torn = sd / "rail-a-b-c-9.md"            # canonical for BOTH spellings
        torn.write_text("# rail artifact — a/b-c#9\nPR=9\n", encoding="utf-8")
        torn_log = torn.with_suffix(".log")
        torn_log.write_text(
            f"\n{rd.ATTEMPT_MARK} x attempt=xyz (detached) =====\n"
            "admin-merge: repo a/b-c (resolved from --repo)\n"
            "admin-merge: ✅ merged PR #9 at deadbeefcafe1234 (state=MERGED)\n",
            encoding="utf-8")
        mine = sd / "rail-github.com-a-b-c-9.md"   # this repo's LIVE rail, legacy name
        mine.write_text(f"PR=9\nREPO=github.com/a-b/c\nSTARTED_AT={rd._iso_now()}\n"
                        f"PID={live.pid}\n", encoding="utf-8")
        st = rd.collect(sd, "a-b/c", 9)
        assert st is not None and st.state == rd.WAITING, (
            "a torn record borrowed another repo's merge and shadowed the live "
            f"rail: {st and st.as_dict()}")
        assert st.artifact == str(mine), st.artifact

        # ── THE EXIT SITE: the same torn shape, now carrying EXIT=, must not
        # certify `merged` for a repo the log says was never graded.
        sd2 = tmp_path / "state2"
        sd2.mkdir()
        torn2 = sd2 / "rail-a-b-c-9.md"
        torn2.write_text("PR=9\nATTEMPT_ID=t\nEXIT_ATTEMPT=t\nEXIT=0\n",
                         encoding="utf-8")
        torn2.with_suffix(".log").write_text(
            f"\n{rd.ATTEMPT_MARK} x attempt=t (detached) =====\n"
            "admin-merge: repo a/b-c (resolved from --repo)\n"
            "admin-merge: ✅ merged PR #9 at deadbeefcafe1234 (state=MERGED)\n",
            encoding="utf-8")
        st = rd.collect(sd2, "a-b/c", 9)
        assert st is not None and st.state != rd.MERGED, (st and st.as_dict())
        assert rd.main(["collect", "9", "--repo", "a-b/c", "--state-dir", str(sd2),
                        "--assert-merged"]) == 1
        # …while the repo the log NAMES is still certified by the SAME record.
        st = rd.collect(sd2, "a/b-c", 9)
        assert st is not None and st.state == rd.MERGED, (st and st.as_dict())
    finally:
        live.kill()
        live.wait()


def test_an_announced_merge_outranks_a_recycled_child_pid(tmp_path):
    """⛔ ANNOUNCED BEATS `lost` — INCLUDING THE `lost` A RECYCLED PID DRAWS.

    Putting the live-carrier check first (alive outranks announced) must not run the
    stranger branch past the merge evidence: a stranger carries NOTHING, while the
    announcement was printed only after the API confirmed `state=MERGED`, so there
    the merge IS the verdict. Skipping it reported `lost` for a merged PR — the
    state `start` accepts, and the truncation that follows makes the loss permanent
    (#2235, #3061). The recycled-pid arm that already existed writes no log, so it
    cannot see this.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 92)
    logp = rd.log_path(sd, "o/r", 92)
    started_long_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                     time.gmtime(time.time() - 3600))
    stranger = subprocess.Popen(["/bin/sleep", "20"], start_new_session=True,
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    try:
        art.write_text(f"PR=92\nREPO=o/r\nSTARTED_AT={started_long_ago}\nLOG={logp}\n"
                       f"SPAWNING=1\nCHILD_PID={stranger.pid}\n"
                       f"CHILD_STARTED={started_long_ago}\n", encoding="utf-8")
        logp.write_text("\n===== rail attempt started x (detached) =====\n"
                        "admin-merge: ✅ merged PR #92 at deadbeefcafe1234 (state=MERGED)\n",
                        encoding="utf-8")
        st = rd.collect(sd, "o/r", 92)
        assert st.state == rd.MERGED, st.as_dict()   # pre-fix: LOST
        assert st.merge_sha == "deadbeefcafe1234"

        # CONTROL — with nothing announced, the same recycled pid IS `lost`.
        logp.write_text("\n===== rail attempt started x (detached) =====\n"
                        "admin-merge: … FIRST-CHECKS wait: run 41557371710 is pending\n",
                        encoding="utf-8")
        st = rd.collect(sd, "o/r", 92)
        assert st.state == rd.LOST, st.as_dict()
        assert "RECYCLED" in st.reason
    finally:
        stranger.kill()
        stranger.wait()


def test_the_live_child_is_what_the_refusal_points_at(tmp_path):
    """⛔ THE WRAPPER PID IS THE WRONG THING TO `ps` WHEN THE WRAPPER IS THE DEAD ONE.

    "wrapper killed, rail alive" is this module's documented P1, and there `collect`
    reports `waiting` because of `CHILD_PID` while `prior.pid` is gone. A hint that
    names the wrapper sends the operator to an empty `ps` table, whose own gate then
    reads "not this PR's rail" — and the `rm` it offers would unlink a LIVE rail's
    record. The child arm must win whenever a child is named.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    os.environ["FAKE_RAIL_SLEEP"] = "30"
    try:
        ln = rd.launch(91, cwd=co, state_dir=sd, repo="o/r")
    finally:
        os.environ.pop("FAKE_RAIL_SLEEP", None)
    art = rd.artifact_path(sd, "o/r", 91)
    assert wait_field(art, "CHILD_PID"), art.read_text(encoding="utf-8")
    child = int(rd.read_field(art.read_text(encoding="utf-8"), "CHILD_PID"))
    try:
        os.kill(ln.pid, signal.SIGKILL)     # ONLY the wrapper: the P1 shape
        st = wait_state(sd, 91, rd.WAITING)
        assert st.state == rd.WAITING, st.as_dict()
        assert st.pid == ln.pid and st.child == child, st.as_dict()
        with pytest.raises(rd.Refused) as exc:
            rd.launch(91, cwd=co, state_dir=sd, repo="o/r")
        msg = str(exc.value)
        assert "ALREADY LIVE" in msg
        assert f"ps -p {child}" in msg, msg          # the pid that CARRIES the rail
        assert f"ps -p {ln.pid}" not in msg, msg     # never the dead wrapper
    finally:
        kill_group(ln.pid)


def test_an_artifact_unlinked_mid_collect_is_absent_not_a_traceback(tmp_path, monkeypatch):
    """A crash must not wear the machine gate's exit code.

    `collect` reads the artifact more than once, and `rm <artifact>` is the recovery
    both verbs print. A `FileNotFoundError` escaping it exits 1 — which this tool
    publishes as `--assert-merged` "the state is NOT merged" — so a missing record
    would read as a completed rail that declined. `absent` (3) is the honest answer.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 90)
    logp = rd.log_path(sd, "o/r", 90)
    art.write_text(f"PR=90\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nLOG={logp}\n"
                   f"SPAWNING=1\nATTEMPT_ID=a90\nPID={_reaped_pid()}\n",
                   encoding="utf-8")
    logp.write_text(f"\n{rd.ATTEMPT_MARK} x attempt=a90 (detached) =====\n",
                    encoding="utf-8")

    def probe(leader, child, marker, **_kw):
        art.unlink()          # an operator's `rm`, mid-read
        return False

    monkeypatch.setattr(rd, "_rail_alive", probe)
    assert rd.collect(sd, "o/r", 90) is None            # pre-fix: FileNotFoundError
    assert rd.main(["collect", "90", "--repo", "o/r", "--state-dir", str(sd)]) == 3


def test_a_merge_line_naming_another_pr_is_not_this_prs_merge(tmp_path):
    """⛔ EVERY SUCCESS LINE CARRIES ITS PR NUMBER, AND THE NUMBER WAS IGNORED.

    A log whose one success line names a DIFFERENT PR read as this PR's completed
    merge. Reachable without a corrupted artifact: the launcher passes operator
    args through verbatim and the rail's own argument loop takes the LAST
    `--repo`, so `start 12 -- --repo other/repo` produced a rail for
    `other/repo#12` under `o/r#12`'s artifact. "Merged" for a PR that did not
    merge is the claim #2235 forbids; a mismatched number is NO evidence, which
    fails closed.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 43)
    logp = rd.log_path(sd, "o/r", 43)
    art.write_text(
        f"PR=43\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nLOG={logp}\n"
        f"ATTEMPT_ID=a43\nPID={_reaped_pid()}\n",
        encoding="utf-8",
    )
    mark = f"\n{rd.ATTEMPT_MARK} x attempt=a43 (detached) =====\n"
    logp.write_text(mark + "admin-merge: ✅ merged PR #44 at deadbeefcafe1234 "
                    "(state=MERGED confirmed via the API)\n", encoding="utf-8")
    st = rd.collect(sd, "o/r", 43)
    assert st.state != rd.MERGED, st.as_dict()     # pre-fix: MERGED, for #43
    assert st.merge_sha == ""

    # …and the no-sha / already-merged forms carry the number too.
    for line in ("admin-merge: ✅ merged PR #44 (state=MERGED) — merge commit unreadable\n",
                 "admin-merge: PR #44 is ALREADY MERGED (state=MERGED)\n"):
        logp.write_text(mark + line, encoding="utf-8")
        assert rd.collect(sd, "o/r", 43).state != rd.MERGED, line

    # CONTROL: the same words naming THIS PR are still the merge.
    logp.write_text(mark + "admin-merge: ✅ merged PR #43 at deadbeefcafe1234 "
                    "(state=MERGED confirmed via the API)\n", encoding="utf-8")
    st = rd.collect(sd, "o/r", 43)
    assert st.state == rd.MERGED, st.as_dict()
    assert st.merge_sha == "deadbeefcafe1234"


def test_already_merged_does_not_invent_a_merge_sha(tmp_path):
    """Under a squash the branch head is never on main, so reporting HEAD as
    `merge_sha` would be a fabricated sha."""
    body = textwrap.dedent(
        """#!/usr/bin/env bash
        echo "admin-merge: PR #$1 is ALREADY MERGED (state=MERGED) — duplicate execution is a NO-OP"
        exit 0
        """
    )
    co = make_checkout(tmp_path, body=body)
    sd = tmp_path / "state"
    rd.launch(62, cwd=co, state_dir=sd, repo="o/r")
    st = wait_state(sd, 62, rd.MERGED)
    assert st.state == rd.MERGED and st.merge_sha == ""


def test_merged_without_a_printed_sha_is_still_merged(tmp_path):
    """admin-merge.sh's `✅ merged PR #N (state=` line prints the same success with NO sha
    when the merge commit was unreadable. A crash after that line is still a merge."""
    body = textwrap.dedent(
        """#!/usr/bin/env bash
        echo "admin-merge: ✅ merged PR #$1 (state=MERGED confirmed via the API) — the merge commit was NOT readable"
        kill -9 $PPID
        """
    )
    co = make_checkout(tmp_path, body=body)
    sd = tmp_path / "state"
    rd.launch(63, cwd=co, state_dir=sd, repo="o/r")
    st = wait_state(sd, 63, rd.MERGED)
    assert st.state == rd.MERGED and st.merge_sha == ""
    assert "no merge sha printed" in st.reason


def test_cli_collect_resolves_the_repo_from_disk_not_the_network(tmp_path, capsys):
    """collect must answer with no `gh` at all: the artifact names its own repo."""
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    rd.main(["start", "64", "--repo", "o/r", "--cwd", str(co), "--state-dir", str(sd)])
    capsys.readouterr()
    rc = rd.main(["collect", "64", "--state-dir", str(sd)])   # no --repo, no network
    assert rc == 0
    assert capsys.readouterr().out.startswith("RAIL(#64, ")


def test_cli_collect_answers_absent_without_asking_gh(tmp_path, monkeypatch, capsys):
    """⛔ ZERO CANDIDATES IS AN ANSWER, NOT A REASON TO ASK GITHUB.

    The artifact filename is `<repo slug>-<pr>`, so an artifact the CLI's disk glob
    does not match cannot exist for ANY repo — the PR has never been railed here,
    which is `absent` (3) whatever `gh` would have said. Asking `gh` first made a
    STATUS read depend on the network: with `gh` unreachable, an empty state dir
    exited 2 (environment error) where the exit table promises 3, and the module's
    own comment claimed `collect` "never calls `gh`" (review finding, PR #10691).

    Both directions are pinned: the answer is 3, AND the network was never asked.
    The `--assert-merged` arm is the one that matters to a fleet beat — 3 ("no rail
    here") is a different instruction from 1 ("a rail ran and did NOT merge"), and
    collapsing them is how a missing artifact reads as a refusal.
    """
    called = tmp_path / "gh-calls"
    bindir = fake_gh(tmp_path, f"#!/usr/bin/env bash\necho \"$@\" >> {called}\nexit 1\n",
                     sub="bin2")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    sd = tmp_path / "state"
    sd.mkdir()
    capsys.readouterr()

    rc = rd.main(["collect", "95", "--state-dir", str(sd)])          # no --repo
    assert rc == 3, f"no artifact is `absent` (3), not an environment error: got {rc}"
    assert capsys.readouterr().out.startswith("RAIL(#95, absent, ")
    assert not called.exists(), "collect asked the network a question disk settles"

    # ⛔ THE ZERO-CANDIDATE RETURN IS A SEPARATE ARM FROM THE `st is None` ONE, and
    # `--json` must answer JSON on BOTH. Pinning only the `--repo` path left THIS
    # arm's prose answer alive (measured: mutating it back to the `RAIL(...)` print
    # left the whole file green at 100 passed, and the real CLI then emitted prose
    # with rc 3 — the exact defect the fix was written for, one arm over).
    rc = rd.main(["collect", "95", "--state-dir", str(sd), "--json"])
    assert rc == 3, rc
    doc = json.loads(capsys.readouterr().out)      # pre-fix: JSONDecodeError here
    assert doc["state"] == "absent" and doc["pr"] == 95

    # An absent rail is NOT "not merged": the machine gate must not read 1 here.
    rc = rd.main(["collect", "95", "--state-dir", str(sd), "--assert-merged"])
    assert rc == 3, f"absent must not wear the gate's code (1): got {rc}"

    # …and a state dir that does not exist yet answers identically.
    rc = rd.main(["collect", "95", "--state-dir", str(tmp_path / "never-created")])
    assert rc == 3
    assert not called.exists()


def test_dry_run_exit_zero_is_not_a_merge(tmp_path):
    """⛔ `admin-merge.sh --dry-run` merges nothing and exits 0. Reporting that as
    `merged` would release the next beat's gate on evidence that does not exist."""
    body = textwrap.dedent(
        """#!/usr/bin/env bash
        echo "admin-merge: --dry-run — evidence that WOULD be posted:"
        echo "admin-merge: --dry-run performs no base run, no CI re-run, posts no comment and merges nothing."
        exit 0
        """
    )
    co = make_checkout(tmp_path, body=body)
    sd = tmp_path / "state"
    rd.launch(65, cwd=co, state_dir=sd, repo="o/r", extra=["--dry-run"])
    st = wait_state(sd, 65, rd.REFUSED)
    assert st.state == rd.REFUSED and st.exit_code == 0
    assert "without a merge line" in st.reason


def test_torn_exit_line_does_not_crash_collect(tmp_path):
    """A group kill between `printf 'EXIT=%s'` and its argument leaves `EXIT=`.
    A read-only verb must report a state, never a traceback."""
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    os.environ["FAKE_RAIL_SLEEP"] = "20"
    try:
        ln = rd.launch(66, cwd=co, state_dir=sd, repo="o/r")
    finally:
        os.environ.pop("FAKE_RAIL_SLEEP", None)
    kill_group(ln.pid)          # the whole group: nothing is left to write an EXIT
    time.sleep(0.3)
    art = rd.artifact_path(sd, "o/r", 66)
    # The wrapper writes `EXIT_ATTEMPT=<id>` then `EXIT=%s`; a kill between the
    # format's expansions leaves the torn pair below, and the read-only verb must
    # report a state rather than a traceback.
    attempt = rd.read_field(art.read_text(encoding="utf-8"), "ATTEMPT_ID")
    assert attempt, "launch must stamp the attempt id"
    art.write_text(art.read_text() + f"EXIT_ATTEMPT={attempt}\nEXIT=\n")  # the torn append
    st = rd.collect(sd, "o/r", 66)
    assert st.state == rd.REFUSED and st.exit_code is None   # fail closed, no crash


def test_a_torn_LOG_header_does_not_crash_collect(tmp_path):
    """⛔ A HEADER CUT AT `LOG=` RESOLVES TO `Path(".")`, WHICH *EXISTS*.

    `_read_snapshot` reads `LOG=` out of the artifact and then `read_text()`s it. A
    header write cut at `LOG=` yields the EMPTY value, and `Path("")` is `Path(".")`
    — a DIRECTORY, for which `.exists()` answers True — so the read raises
    `IsADirectoryError` out of `collect`. That is a traceback on a read-only verb,
    and under `--assert-merged` it exits 1: the code this tool reserves for "the
    state is NOT merged". A crash must not wear the machine gate's verdict, which is
    the whole reason the guard is `.is_file()` and not `.exists()`.

    ⛔ THIS IS THE `.is_file()` GUARD'S ONLY REGRESSION DETECTOR. Reverting it to
    `.exists()` leaves every other case in this file green (measured: 98 passed
    either way), because no other test constructs the torn-`LOG=` shape.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 91)
    real_log = rd.log_path(sd, "o/r", 91)
    art.write_text(
        f"PR=91\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nLOG=\n"
        f"SPAWNING=1\nATTEMPT_ID=a91\nPID={_reaped_pid()}\n",
        encoding="utf-8",
    )
    # The real log DOES carry a success line, so a guard that fell back to
    # "read everything" would answer MERGED on evidence this attempt cannot name.
    real_log.write_text(
        f"\n{rd.ATTEMPT_MARK} x attempt=a91 (detached) =====\n"
        "admin-merge: ✅ merged PR #91 at deadbeefcafe1234 "
        "(state=MERGED confirmed via the API)\n",
        encoding="utf-8",
    )

    st = rd.collect(sd, "o/r", 91)                     # pre-fix: IsADirectoryError
    assert st is not None, "a torn header is a state, never an absent record"
    assert st.state != rd.MERGED, st.as_dict()
    assert rd.main(["collect", "91", "--repo", "o/r", "--state-dir", str(sd),
                    "--assert-merged"]) == 1

    # ARM 2 — `LOG=` naming a real DIRECTORY is the same read by another route.
    (tmp_path / "a-dir").mkdir()
    art.write_text(
        f"PR=91\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\n"
        f"LOG={tmp_path / 'a-dir'}\n"
        f"SPAWNING=1\nATTEMPT_ID=a91\nPID={_reaped_pid()}\n",
        encoding="utf-8",
    )
    st = rd.collect(sd, "o/r", 91)
    assert st is not None and st.state != rd.MERGED, st.as_dict()
    assert rd.main(["collect", "91", "--repo", "o/r", "--state-dir", str(sd),
                    "--assert-merged"]) == 1


def test_absent_collect_answers_json_when_json_was_asked_for(tmp_path, capsys):
    """⛔ `--json` MUST ANSWER JSON ON THE ABSENT PATH TOO — that is the path a lane hits most.

    Both absent returns printed a bare `RAIL(#n, absent, -, -)` line even under
    `--json`, so `json.loads(stdout)` raised `JSONDecodeError` on the single most
    common answer ("this PR was never railed here"). The exit code (3) was correct
    the whole time, which is exactly why a key-on-exit-code caller never noticed: the
    broken surface is the documented machine one, and it breaks only when there is
    nothing to report.

    Both output SHAPES are asserted here — a fix that made ABSENT json-only would
    break the human surface, which is the shape a person reads in a terminal. The
    OTHER absent return (zero candidates, no `--repo` given) is a SEPARATE code path;
    it is pinned by `test_cli_collect_answers_absent_without_asking_gh`.
    """
    sd = tmp_path / "state"
    sd.mkdir()

    rc = rd.main(["collect", "424242", "--repo", "o/r", "--state-dir", str(sd), "--json"])
    out = capsys.readouterr().out
    assert rc == 3
    doc = json.loads(out)                      # pre-fix: JSONDecodeError here
    assert doc["state"] == "absent" and doc["pr"] == 424242
    real = rd.RailState(pr=1, state=rd.LOST).as_dict()
    assert set(doc) == set(real), (
        "the absent document must carry the SAME key set as a real status, or a "
        "consumer needs two schemas")
    # ⛔ KEY SET IS NOT ENOUGH: emitting `None` where a real status emits `""` (and
    # `0` for `pid`/`child`) makes `int(doc["pid"])` raise for a consumer that
    # learned the schema on the NORMAL path — the same break, one level down.
    # VGATE caught exactly that in this fix's first cut, which is why the types and
    # not just the names are pinned.
    assert {k: type(v) for k, v in doc.items()} == {k: type(v) for k, v in real.items()}, (
        f"per-key TYPES must match a real status: "
        f"absent={ {k: type(v).__name__ for k, v in doc.items()} } "
        f"real={ {k: type(v).__name__ for k, v in real.items()} }")

    # ARM 2 — without `--json` the human line is unchanged.
    rc = rd.main(["collect", "424242", "--repo", "o/r", "--state-dir", str(sd)])
    out = capsys.readouterr().out
    assert rc == 3
    assert out.strip() == "RAIL(#424242, absent, -, -)", out


def test_a_named_rail_under_spawning_is_not_no_rail(tmp_path):
    """⛔ `SPAWNING=1` WITH NO `PID=` IS NOT "NO RAIL EVER STARTED".

    `launch` appends `PID=` only AFTER `Popen` returns, so a launcher killed in
    that instant leaves `SPAWNING=1` while the rail it already spawned has written
    `CHILD_PID=` and is merging. Concluding `lost` there is the same durable-wrong-
    verdict shape as the fork window: the next `start` truncates the artifact and
    scopes out a `✅ merged` line that already exists. So the named rail and the
    log are read BEFORE the sentinel is allowed to mean "nothing started".

    Both arms use a STARTED_AT well past `SPAWNING_TTL_S`, so neither can pass by
    the sentinel simply not having expired.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 98)
    logp = rd.log_path(sd, "o/r", 98)
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    rail = subprocess.Popen(["/bin/sleep", "10"], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        art.write_text(
            f"PR=98\nREPO=o/r\nSTARTED_AT={old}\nLOG={logp}\nSPAWNING=1\n"
            f"ATTEMPT_ID=attempt-98\nCHILD_PID={rail.pid}\n",
            encoding="utf-8",
        )
        # arm 1 — a LIVE named rail, nothing announced yet: waiting, not lost
        logp.write_text(
            "\n===== rail attempt started x (detached) =====\n"
            "admin-merge: … FIRST-CHECKS wait: run 41557371710 is pending\n",
            encoding="utf-8",
        )
        st = rd.collect(sd, "o/r", 98)
        assert st.state == rd.WAITING, st.as_dict()     # pre-fix: LOST
        assert "CHILD_PID" in st.reason

        # arm 2 — the named rail ANNOUNCED its merge and is STILL ALIVE.
        # ⛔ ALIVE OUTRANKS ANNOUNCED (`STATES`): the arm used to answer `merged`
        # here, which made `--assert-merged` exit 0 for an in-flight merge and
        # released `start`'s duplicate guard (it refuses only `waiting`). The merge
        # is NOT lost by waiting — the log keeps the line, and the verdict becomes
        # `merged` the moment nothing that carries the rail is left.
        logp.write_text(
            "\n===== rail attempt started x (detached) =====\n"
            "admin-merge: ✅ merged PR #98 at deadbeefcafe1234 (state=MERGED)\n",
            encoding="utf-8",
        )
        st = rd.collect(sd, "o/r", 98)
        assert st.state == rd.WAITING, st.as_dict()     # the documented precedence
        rail.kill()
        rail.wait()
        st = rd.collect(sd, "o/r", 98)
        # pre-fix (before the arm existed at all): LOST, "no rail ever started"
        assert st.state == rd.MERGED, st.as_dict()
        assert st.merge_sha == "deadbeefcafe1234"
        assert "EXIT line was never written" in st.reason
    finally:
        rail.kill()
        rail.wait()


def test_spawning_sentinel_expires_instead_of_wedging_the_pr(tmp_path):
    """A launch killed between `SPAWNING=1` and `PID=` leaves a state with no rail
    to ever write anything. It must age out to `lost` — and `start` must then be
    allowed again — or the PR is unrailable forever."""
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
    art = rd.artifact_path(sd, "o/r", 67)
    sd.mkdir(parents=True)
    for started_at in (now, old):
        art.write_text(f"PR=67\nREPO=o/r\nSTARTED_AT={started_at}\nSPAWNING=1\n")
        st = rd.collect(sd, "o/r", 67)
        if started_at == now:
            assert st.state == rd.WAITING and "launching" in st.reason
        else:
            assert st.state == rd.LOST and "no rail ever started" in st.reason
    # …and the stale one does not block a new attempt
    ln = rd.launch(67, cwd=co, state_dir=sd, repo="o/r")
    assert ln.pid > 0
    assert wait_state(sd, 67, rd.MERGED).state == rd.MERGED


def test_relative_state_dir_lands_in_one_place(tmp_path, monkeypatch):
    """The child runs with cwd=<checkout>, so a relative --state-dir must be
    resolved once: otherwise the launcher writes the artifact one place, the rail
    writes its log another, and a real merge reads back as `lost`."""
    co = make_checkout(tmp_path)
    monkeypatch.chdir(tmp_path)
    ln = rd.launch(68, cwd=co, state_dir=Path("relstate"), repo="o/r")
    assert Path(ln.artifact).is_absolute() and Path(ln.log).is_absolute()
    assert Path(ln.artifact).parent == Path(ln.log).parent == (tmp_path / "relstate").resolve()
    st = wait_state((tmp_path / "relstate").resolve(), 68, rd.MERGED)
    assert st.state == rd.MERGED and st.merge_sha == "deadbeefcafe1234"


def test_a_previous_attempts_late_exit_is_not_this_attempts(tmp_path, monkeypatch):
    """⛔ THE ARTIFACT HANDOFF HAS NO ORDERING.

    `launch` truncates the artifact while the PREVIOUS attempt's wrapper may still
    be about to append `EXIT=`: its rail printed the merge line first, so `collect`
    already answered `merged` and the new start was not refused. The late append
    then lands in the NEW artifact and used to read as this attempt's exit —
    `refused` ("exited 0 without a merge line") for an attempt that was KILLED.

    Each attempt stamps its own id, so only a matching `EXIT_ATTEMPT=`/`EXIT=` pair
    is evidence. Discriminating: without the scoping this reads `refused`.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    rd.launch(65, cwd=co, state_dir=sd, repo="o/r")
    first = wait_state(sd, 65, rd.MERGED, pred=lambda s: s.exit_code is not None)
    assert first.state == rd.MERGED, first.as_dict()
    art = rd.artifact_path(sd, "o/r", 65)
    first_id = rd.read_field(art.read_text(encoding="utf-8"), "ATTEMPT_ID")
    assert first_id, "launch must stamp the attempt id"

    monkeypatch.setenv("FAKE_RAIL_SLEEP", "30")
    ln = rd.launch(65, cwd=co, state_dir=sd, repo="o/r")   # truncates the artifact
    # let attempt 2's rail NAME itself, so this arm is about the stale EXIT line
    # and not about the (separately pinned) fork window
    assert wait_field(art, "CHILD_PID"), art.read_text(encoding="utf-8")
    kill_group(ln.pid)                                      # attempt 2 dies, no EXIT
    time.sleep(0.3)
    # …and attempt 1's wrapper appends ITS exit line after the truncation
    with art.open("a", encoding="utf-8") as fh:
        fh.write(f"EXIT_ATTEMPT={first_id}\nEXIT=0\nFINISHED_AT={rd._iso_now()}\n")
    st = rd.collect(sd, "o/r", 65)
    assert st.state == rd.LOST, st.as_dict()      # pre-fix: REFUSED, "exited 0 …"
    assert st.exit_code is None, st.as_dict()


def test_an_unreaped_wrapper_group_does_not_report_waiting_forever(tmp_path):
    """⛔ `killpg(..., 0)` ANSWERS FOR A ZOMBIE GROUP MEMBER FOREVER.

    A caller that stays alive after `launch()` (an importing fleet beat, not the
    one-shot `start` CLI) leaves its killed wrapper an UNREAPED child, so the
    group keeps answering with no rail in it and nothing left that could ever
    write `CHILD_PID=` or `EXIT=`. Unbounded, that is `waiting` forever AND
    `start` refused forever — the unrailable wedge this module exists to remove.
    Past `GROUP_CLAIM_GRACE_S` an unnamed group is a corpse: `lost` is both true
    and recoverable.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    os.environ["FAKE_RAIL_SLEEP"] = "30"
    try:
        ln = rd.launch(96, cwd=co, state_dir=sd, repo="o/r")
    finally:
        os.environ.pop("FAKE_RAIL_SLEEP", None)
    kill_group(ln.pid)              # the rail dies too, so no `CHILD_PID=` lands
    time.sleep(0.3)
    art = rd.artifact_path(sd, "o/r", 96)
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
    art.write_text("".join(
        f"STARTED_AT={old}\n" if ln_.startswith("STARTED_AT=") else
        "" if ln_.startswith("CHILD_PID=") else ln_ + "\n"
        for ln_ in art.read_text(encoding="utf-8").splitlines()
    ) + "")
    assert rd.read_field(art.read_text(encoding="utf-8"), "CHILD_PID") == ""
    st = rd.collect(sd, "o/r", 96)
    assert st.state == rd.LOST, st.as_dict()     # pre-fix: WAITING, forever
    assert "no EXIT line" in st.reason


def test_a_live_group_member_is_not_lost_past_the_grace(tmp_path):
    """⛔ AGE IS NOT EVIDENCE ABOUT A PROCESS — ITS GROUP'S MEMBERS ARE.

    The grace above exists because a ZOMBIE keeps `killpg(..., 0)` answering. It
    was applied as `age > GROUP_CLAIM_GRACE_S: return False`, which is a bound on
    the CLOCK, not on the group: a LIVE rail that has not yet executed its
    self-naming `printf` sits in exactly that group, and the rail's wait is set by
    the runner QUEUE (unbounded — the module's own measured case is >= 6660 s). So
    the claim expired on a rail that was still merging, `collect` answered `lost`,
    and `lost` is the state `start` ACCEPTS: a SECOND rail on a live PR (#3061),
    plus the truncation that scopes its real `✅ merged` line out (#2235).

    Reproduced without a race: a group whose LEADER is gone while a member of it
    is alive — the wrapper's death with the rail still running, one process over.
    Pre-fix `lost`, post-fix `waiting`. The control arm kills the member, so this
    cannot be satisfied by a probe that answers `waiting` for everything.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
    art = rd.artifact_path(sd, "o/r", 99)
    logp = rd.log_path(sd, "o/r", 99)
    # A non-interactive `sh` puts its background job in ITS OWN group, so the
    # leader (`sh`) exits and leaves a live member (`sleep`) holding the pgid —
    # the exact shape of a wrapper killed inside the fork window.
    leader = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 25 & sleep 0.2"],
        start_new_session=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        leader.wait()
        # THE PREMISE, asserted rather than assumed: the leader is GONE, and its
        # group still answers because a LIVE member is in it.
        assert rd._pid_alive(leader.pid, str(art)) is False
        os.killpg(leader.pid, 0)
        assert rd._group_has_a_live_member(leader.pid) is True
        art.write_text(
            f"PR=99\nREPO=o/r\nSTARTED_AT={old}\nLOG={logp}\nSPAWNING=1\n"
            f"ATTEMPT_ID=a99\nPID={leader.pid}\n",
            encoding="utf-8",
        )
        logp.write_text(f"\n{rd.ATTEMPT_MARK} x attempt=a99 (detached) =====\n"
                        "admin-merge: … FIRST-CHECKS wait: run 41557371710 is pending\n",
                        encoding="utf-8")
        st = rd.collect(sd, "o/r", 99)
        assert st.state == rd.WAITING, st.as_dict()   # pre-fix: LOST

        # CONTROL: the same artifact, once the group's member is gone too.
        os.killpg(leader.pid, signal.SIGKILL)
        deadline = time.time() + 10
        while time.time() < deadline and rd._group_has_a_live_member(leader.pid):
            time.sleep(0.05)
        st2 = rd.collect(sd, "o/r", 99)
        assert st2.state == rd.LOST, st2.as_dict()    # the claim still lifts
    finally:
        with contextlib.suppress(OSError):
            os.killpg(leader.pid, signal.SIGKILL)


def test_an_unparseable_group_probe_is_unmeasurable_not_empty(monkeypatch):
    """⛔ `_ps` RETURNS STDOUT WHETHER OR NOT IT ACCEPTED THE REQUEST.

    On the no-procfs box class a `ps` that RUNS but rejects `pid=,pgid=,stat=`
    hands back an empty string. Falling through to "no live member" there reports a
    LIVE rail's group as empty — `lost` for a rail that is still merging, which is
    the fail-danger direction the group bound exists to prevent, and the exact trap
    `_ps_stat` already documents ("macOS returns 1 for BOTH … trusting it would read
    a live rail as dead on a box with a different `ps`"). The probe now asks for a
    pid it MUST be able to see, in the SAME format, before believing an empty answer.

    Both arms are here so the fix cannot be "always answer None": the second arm is
    a `ps` that DOES speak the format, and it must still find the live member.
    """
    real_path = rd.Path

    def no_procfs(p="/", *a, **k):
        if str(p).startswith("/proc"):
            raise OSError("this box has no procfs")
        return real_path(p, *a, **k)

    monkeypatch.setattr(rd, "Path", no_procfs)
    monkeypatch.setattr(rd, "_ps", lambda *a, **k: "")          # format rejected
    assert rd._group_has_a_live_member(os.getpgrp()) is None     # pre-fix: False

    speaking = f"{os.getpid()} {os.getpgrp()} S\n"
    monkeypatch.setattr(rd, "_ps", lambda *a, **k: speaking)
    assert rd._group_has_a_live_member(os.getpgrp()) is True


def test_the_group_probe_reads_member_state_not_membership(tmp_path):
    """The two directions of `_group_has_a_live_member`, so neither can drift:
    a live member answers True, a ZOMBIE-ONLY group answers False. Answering
    True for a zombie reinstates the unrailable wedge; answering False for a live
    member reinstates the false `lost` above."""
    assert rd._group_has_a_live_member(os.getpgrp()) is True
    zombie = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True,
                              stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    try:
        os.kill(zombie.pid, signal.SIGKILL)
        deadline = time.time() + 10
        while time.time() < deadline and rd._proc_stat_letter(zombie.pid) != "Z":
            time.sleep(0.02)
        if rd._proc_stat_letter(zombie.pid) != "Z":
            pytest.skip("no procfs on this box — a zombie cannot be pinned here")
        assert rd._group_has_a_live_member(zombie.pid) is False
    finally:
        zombie.wait()


def test_a_log_without_an_attempt_mark_is_no_evidence(tmp_path):
    """No attempt mark ⇒ not this attempt's log ⇒ no scoped evidence. Reading it
    whole would let a dead attempt inherit an earlier `✅ merged` line."""
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    rd.launch(69, cwd=co, state_dir=sd, repo="o/r")
    assert wait_state(sd, 69, rd.MERGED).state == rd.MERGED
    art = rd.artifact_path(sd, "o/r", 69)
    logp = Path(rd.read_field(art.read_text(), "LOG"))
    # a foreign/recreated log: same words, no mark
    logp.write_text("admin-merge: ✅ merged PR #69 at deadbeefcafe1234 (state=MERGED)\n")
    art.write_text(art.read_text().replace("EXIT=", "EXIT_TORN="))  # attempt 2: no exit
    st = rd.collect(sd, "o/r", 69)
    assert st.state != rd.MERGED, st.as_dict()
    assert st.merge_sha == ""


def test_a_merge_line_for_another_repo_is_not_this_prs_merge(tmp_path):
    """⛔ THE NUMBER ALONE DOES NOT IDENTIFY A PR — THE REPO DOES.

    `start 12 -- --repo other/repo` reaches the rail's passthrough, whose argument
    loop takes the LAST `--repo`, so the rail grades `other/repo#12` … and prints
    `✅ merged PR #12`, whose NUMBER matches this artifact's. The PR-number check
    cannot see that. The rail prints the target it resolved on every run
    (`admin-merge.sh` `resolve_rail_repo`), so that line must name this artifact's
    repo; a differing one is NO evidence, which fails closed.

    Both halves present: one-sidedness (a log with no evidence line still reads its
    merge) is the control, so this cannot be satisfied by refusing every log.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 12)
    logp = rd.log_path(sd, "o/r", 12)
    art.write_text(
        f"PR=12\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nLOG={logp}\n"
        f"ATTEMPT_ID=a12\nPID={_reaped_pid()}\n",
        encoding="utf-8",
    )
    mark = f"\n{rd.ATTEMPT_MARK} x attempt=a12 (detached) =====\n"
    merged_line = ("admin-merge: ✅ merged PR #12 at deadbeefcafe1234 "
                   "(state=MERGED confirmed via the API)\n")
    logp.write_text(
        mark + "admin-merge: repo other/repo (resolved from --repo)\n" + merged_line,
        encoding="utf-8")
    st = rd.collect(sd, "o/r", 12)
    assert st.state != rd.MERGED, st.as_dict()      # pre-fix: MERGED for o/r#12
    assert st.merge_sha == ""

    # CONTROL — the same log naming THIS artifact's repo is the merge…
    logp.write_text(
        mark + "admin-merge: repo o/r (resolved from --repo)\n" + merged_line,
        encoding="utf-8")
    st = rd.collect(sd, "o/r", 12)
    assert st.state == rd.MERGED, st.as_dict()
    assert st.merge_sha == "deadbeefcafe1234"

    # …and ONE-SIDED: no evidence line at all is not a contradiction (legacy logs,
    # and every stub rail this suite uses).
    logp.write_text(mark + merged_line, encoding="utf-8")
    assert rd.collect(sd, "o/r", 12).state == rd.MERGED

    # …and NORMALISED ON BOTH SIDES: the rail drops an attestable host before it
    # prints the target, so a `github.com/o/r` artifact must still match the
    # `o/r` it announces. Comparing the raw values made a REAL merge read
    # `refused`, with a reason that said the operator had run a dry run.
    art.write_text(
        f"PR=12\nREPO=github.com/o/r\nSTARTED_AT={rd._iso_now()}\nLOG={logp}\n"
        f"ATTEMPT_ID=a12\nPID={_reaped_pid()}\n",
        encoding="utf-8",
    )
    logp.write_text(
        mark + "admin-merge: repo o/r (resolved from --repo)\n" + merged_line,
        encoding="utf-8")
    st = rd.collect(sd, "o/r", 12)   # the artifact FILE name is the o/r slug
    assert st.state == rd.MERGED, st.as_dict()


def test_a_merge_written_to_the_log_only_is_not_lost(tmp_path, monkeypatch):
    """⛔ THE ARTIFACT STOPS MOVING ONCE A RAIL IS WAITING; THE LOG DOES NOT.

    The two-pass loop exists so a `lost` conclusion comes from a read taken AFTER
    the liveness probe — but the "did it move?" test compared the ARTIFACT only,
    and the artifact changes only at `CHILD_PID=`/`CHILD_STARTED=` and at `EXIT=`.
    During the whole of a rail's wait it does not move at all, while the log is
    where every word a verdict reads is written. So the `break` concluded from a
    log read taken BEFORE the probe, and a rail that printed `✅ merged …` and then
    died in that window was reported `lost` — the state `start` ACCEPTS, so the
    next beat truncates the log and the announcement is permanently gone (#2235,
    #3061).

    Reproduced without a race: the probe appends only to the LOG and answers
    "gone"; the artifact is never touched. Pre-fix `lost`; post-fix the rail's own
    evidence.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 74)
    logp = rd.log_path(sd, "o/r", 74)
    art.write_text(
        f"PR=74\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nLOG={logp}\n"
        f"ATTEMPT_ID=a74\nPID={_reaped_pid()}\n",
        encoding="utf-8",
    )
    logp.write_text(f"\n{rd.ATTEMPT_MARK} x attempt=a74 (detached) =====\n"
                    "admin-merge: … FIRST-CHECKS wait: run 41557371710 is pending\n",
                    encoding="utf-8")

    def probe(leader, child, marker, **_kw):
        with logp.open("a", encoding="utf-8") as fh:
            fh.write("admin-merge: ✅ merged PR #74 at deadbeefcafe1234 "
                     "(state=MERGED confirmed via the API)\n")
        return False                                    # GONE

    monkeypatch.setattr(rd, "_rail_alive", probe)
    st = rd.collect(sd, "o/r", 74)
    assert st.state == rd.MERGED, st.as_dict()           # pre-fix: LOST
    assert st.merge_sha == "deadbeefcafe1234"


def test_the_attempt_mark_names_its_attempt_so_a_previous_tail_is_not_ours(tmp_path):
    """⛔ THE EVIDENCE BOUNDARY IS THE MARK, SO THE MARK HAS TO BE ATTRIBUTABLE.

    `launch` truncates the artifact and writes the new `ATTEMPT_ID=` BEFORE its
    mark lands in the log, so a `collect` running in that instant took the
    PREVIOUS attempt's tail as this attempt's — and a previous `✅ merged` line
    then read as a merge this attempt never announced. Reproduced without a race:
    an artifact for attempt `new`, a log holding only attempt `old`'s mark and a
    merge line. Pre-fix `merged`; post-fix no scoped evidence at all.

    The second half is the vacuity guard: a mark naming THIS attempt still bounds
    its own evidence, so the fix cannot be "ignore the log".
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 88)
    logp = rd.log_path(sd, "o/r", 88)
    art.write_text(
        f"PR=88\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nLOG={logp}\n"
        f"ATTEMPT_ID=new-attempt\nPID={_reaped_pid()}\n",
        encoding="utf-8",
    )
    logp.write_text(f"\n{rd.ATTEMPT_MARK} x attempt=old-attempt (detached) =====\n"
                    "admin-merge: ✅ merged PR #88 at deadbeefcafe1234 (state=MERGED)\n",
                    encoding="utf-8")
    st = rd.collect(sd, "o/r", 88)
    assert st.state != rd.MERGED, st.as_dict()     # pre-fix: MERGED (forged)
    assert st.merge_sha == ""

    logp.write_text(f"\n{rd.ATTEMPT_MARK} x attempt=new-attempt (detached) =====\n"
                    "admin-merge: ✅ merged PR #88 at deadbeefcafe1234 (state=MERGED)\n",
                    encoding="utf-8")
    st = rd.collect(sd, "o/r", 88)
    assert st.state == rd.MERGED, st.as_dict()
    assert st.merge_sha == "deadbeefcafe1234"


def test_collect_has_no_side_effects(tmp_path):
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    launch(sd, co, pr=14)
    # Same handoff as the arm above: a `merged` state can be read before the
    # wrapper's `EXIT=` append lands, and this arm asserts BYTE + mtime equality
    # over the artifact — so it must snapshot only the settled record.
    wait_state(sd, 14, rd.MERGED, pred=lambda s: s.exit_code is not None)
    art = rd.artifact_path(sd, "o/r", 14)
    before = (art.read_bytes(), art.stat().st_mtime_ns, sorted(p.name for p in sd.iterdir()))
    for _ in range(3):
        rd.collect(sd, "o/r", 14)
    after = (art.read_bytes(), art.stat().st_mtime_ns, sorted(p.name for p in sd.iterdir()))
    assert before == after


def test_collect_reports_absent_for_a_pr_never_railed(tmp_path):
    assert rd.collect(tmp_path / "nope", "o/r", 999) is None


# ── the launcher's own preconditions (never rail gates) ──────────────────────
def test_refuses_a_second_live_rail_on_one_pr(tmp_path):
    """Two rails on one PR is the #3061 collision — refuse, do not queue."""
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    os.environ["FAKE_RAIL_SLEEP"] = "20"
    try:
        launch(sd, co, pr=15)
        st = wait_state(sd, 15, rd.WAITING)
        assert st.state == rd.WAITING, st.as_dict()   # the first rail is live…
        with pytest.raises(rd.Refused, match="ALREADY LIVE"):
            launch(sd, co, pr=15)
    finally:
        os.environ.pop("FAKE_RAIL_SLEEP", None)


def test_the_un_pid_d_claim_refusal_names_its_own_recovery(tmp_path):
    """⛔ A REFUSAL THAT NAMES NO WAY OUT IS A WEDGE WITH A FRIENDLY VOICE.

    The `SPAWNING=1` + `CHILD_PID=` claim is the one `waiting` state NO clock lifts
    (`collect`'s branch has no age bound, deliberately: an age bound there would
    read a LIVE merging rail as `lost` and let a second rail through). So when that
    claim is a RECYCLED pid — the residual `_pid_alive` documents — `start` refuses
    forever with `pid 0`, which names nothing an operator can act on. The refusal
    therefore has to carry the artifact and the command that clears it.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    sd.mkdir(parents=True)
    art = rd.artifact_path(sd, "o/r", 96)
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    recycled = subprocess.Popen(["/bin/sleep", "20"], start_new_session=True,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    try:
        art.write_text(f"PR=96\nREPO=o/r\nSTARTED_AT={old}\nSPAWNING=1\n"
                       f"CHILD_PID={recycled.pid}\n", encoding="utf-8")
        with pytest.raises(rd.Refused) as exc:
            rd.launch(96, cwd=co, state_dir=sd, repo="o/r")
        msg = str(exc.value)
        assert "ALREADY LIVE" in msg          # the phrase lanes and tests match on
        assert f"rm {art}" in msg             # the recovery, spelled out
        # ⛔ And it must not OVERSTATE it: this claim DOES lift when the named child
        # exits — the durable part is only that a RECYCLED pid is not your rail. An
        # absolute "nothing will ever lift it" is the same prose-contradicting-code
        # class this round corrected, so the qualifier is pinned too.
        assert "no CLOCK lifts this claim" in msg and "RECYCLED" in msg
        # …and `collect` says the same thing where the operator will read it
        st = rd.collect(sd, "o/r", 96)
        assert st.state == rd.WAITING
        assert f"rm {st.artifact}" in st.reason
    finally:
        recycled.kill()
        recycled.wait()


def test_a_bare_sentinel_refusal_does_not_advise_deleting_a_mid_fork_launch(tmp_path):
    """⛔ `pid == 0` COVERS TWO WAITING ARMS, AND THE HINT IS ONLY TRUE OF ONE.

    A bare `SPAWNING=1` (no `CHILD_PID=`) is the launch-mid-fork window: a clock DOES
    lift it (`SPAWNING_TTL_S`, pinned by
    `test_spawning_sentinel_expires_instead_of_wedging_the_pr`), and it names no
    process at all. Advising `rm` there is not just wrong — it is destructive: the
    launcher writes `SPAWNING=1` BEFORE `Popen`, so `rm`-ing that artifact while the
    rail is still forking unlinks the file its `CHILD_PID=`/`EXIT=`/`✅ merged`
    appends go to (bash keeps the fd), and a real merge then reads back unreadable
    forever while a second `start` is allowed — the #3061 collision. The refusal
    must name the expiry and must NOT name a removal.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    sd.mkdir(parents=True)
    art = rd.artifact_path(sd, "o/r", 97)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    art.write_text(f"PR=97\nREPO=o/r\nSTARTED_AT={now}\nSPAWNING=1\n", encoding="utf-8")
    st = rd.collect(sd, "o/r", 97)
    assert st.state == rd.WAITING and "launching" in st.reason
    with pytest.raises(rd.Refused) as exc:
        rd.launch(97, cwd=co, state_dir=sd, repo="o/r")
    msg = str(exc.value)
    assert "ALREADY LIVE" in msg
    assert "rm " not in msg and str(art) not in msg      # ← the destructive advice
    assert f"{rd.SPAWNING_TTL_S}s" in msg                # ← what actually lifts it


def test_a_recycled_child_pid_reads_lost_not_waiting(tmp_path):
    """⛔ THE WEDGE THIS ARM NOW CLOSES, WITHOUT ANY AGE BOUND ON A LIVE RAIL.

    A rail's pid is recycled: `kill(pid, 0)` keeps answering forever, so a
    liveness-only probe reports a dead rail `waiting` for good and `start` refuses
    every later attempt. The artifact carries the material to settle it —
    `CHILD_STARTED=`, written immediately after the fork — and a process cannot have
    started before it was forked, so a pid that started long AFTER that stamp is
    provably not this rail. Pre-fix this read `waiting`.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 94)
    logp = rd.log_path(sd, "o/r", 94)
    started_long_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                     time.gmtime(time.time() - 3600))
    stranger = subprocess.Popen(["/bin/sleep", "20"], start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)   # started NOW
    try:
        art.write_text(f"PR=94\nREPO=o/r\nSTARTED_AT={started_long_ago}\nLOG={logp}\n"
                       f"SPAWNING=1\nCHILD_PID={stranger.pid}\n"
                       f"CHILD_STARTED={started_long_ago}\n", encoding="utf-8")
        st = rd.collect(sd, "o/r", 94)
        assert st.state == rd.LOST, st.as_dict()      # pre-fix: WAITING, forever
        assert "RECYCLED" in st.reason and str(stranger.pid) in st.reason
    finally:
        stranger.kill()
        stranger.wait()


def test_identity_uses_the_child_stamp_and_not_the_launch_age(tmp_path):
    """⛔ THE REFERENCE MUST BE `CHILD_STARTED`, NEVER `STARTED_AT`, AND AGE NEVER DECIDES.

    The launch record here is TWO HOURS old — 120x `SPAWNING_TTL_S`, the shape a real
    rail is in while it waits on the runner queue (measured >= 6660 s) — while the
    named rail is alive and its own `CHILD_STARTED=` stamp agrees with when it
    started. It must read `waiting`.

    Mutants this kills: any `age(STARTED_AT) <= …` gate on this arm reads `lost`, and
    `lost` is the state `start` accepts — so such a mutant lets a SECOND rail onto a
    PR that is still merging (#3061). It also pins the reference: comparing against
    `STARTED_AT` (2 h) instead of `CHILD_STARTED` (seconds) would classify this live
    rail as a stranger.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 95)
    logp = rd.log_path(sd, "o/r", 95)
    launch_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7200))
    child_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3))
    rail = subprocess.Popen(["/bin/sleep", "20"], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        art.write_text(f"PR=95\nREPO=o/r\nSTARTED_AT={launch_ts}\nLOG={logp}\n"
                       f"SPAWNING=1\nCHILD_PID={rail.pid}\n"
                       f"CHILD_STARTED={child_ts}\n", encoding="utf-8")
        st = rd.collect(sd, "o/r", 95)
        assert st.state == rd.WAITING, st.as_dict()
        assert "rm " not in st.reason      # identity settled it: no wedge to clear
    finally:
        rail.kill()
        rail.wait()


def test_the_ticks_anchor_is_exact_and_step_immune(tmp_path):
    """⛔ THE FAIL-DANGER DIRECTION, MEASURED — A CLOCK STEP MUST NOT RELEASE A LIVE RAIL.

    `btime` moves with `settimeofday`, so a FORWARD wall step makes a live process look
    NEWER than the artifact's timestamp (`lstart` / `btime + starttime`). Comparing wall
    epochs therefore reports a LIVE rail as `lost`, and `lost` is the state `start`
    accepts — a second rail on a PR that is still merging (#3061), plus an artifact
    truncation that scopes the first rail's merge line out. That is why the anchor is
    the kernel's `starttime` ticks, which never move: two probes of one process always
    agree, and a recycled pid always differs.

    Arm 1 is the step case exactly: ticks MATCH a live process while the wall stamp is an
    hour old. Arm 2 is the recycle case: ticks differ by one while the wall stamp is
    fresh — so the ticks answer decides in both directions, independently of the clock.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 92)
    logp = rd.log_path(sd, "o/r", 92)
    rail = subprocess.Popen(["/bin/sleep", "20"], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        ticks = rd._proc_start_ticks(rail.pid)
        if ticks is None:
            pytest.skip("no procfs on this box — the ticks anchor cannot exist here")
        old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # arm 1 — ticks MATCH a live process, wall stamp an hour old (a forward step)
        art.write_text(f"PR=92\nREPO=o/r\nSTARTED_AT={old}\nLOG={logp}\nSPAWNING=1\n"
                       f"CHILD_PID={rail.pid}\nCHILD_STARTED={old}\n"
                       f"CHILD_START_TICKS={ticks}\n", encoding="utf-8")
        st = rd.collect(sd, "o/r", 92)
        assert st.state == rd.WAITING, st.as_dict()   # wall-only pre-fix: LOST

        # arm 2 — ticks DIFFER by one, wall stamp FRESH: still a stranger
        art.write_text(f"PR=92\nREPO=o/r\nSTARTED_AT={now}\nLOG={logp}\nSPAWNING=1\n"
                       f"CHILD_PID={rail.pid}\nCHILD_STARTED={now}\n"
                       f"CHILD_START_TICKS={ticks - 1}\n", encoding="utf-8")
        st = rd.collect(sd, "o/r", 92)
        assert st.state == rd.LOST, st.as_dict()      # wall-only pre-fix: WAITING
        assert "RECYCLED" in st.reason
    finally:
        rail.kill()
        rail.wait()


def test_the_written_ticks_anchor_parses_past_a_paren_containing_comm(tmp_path):
    """The WRITER's `/proc/<pid>/stat` parse must agree with the READER's.

    `_proc_start_ticks` splits after the LAST `) ` and says why: the comm field is
    parenthesised and may itself contain spaces AND parens, so a positional split
    over the whole line reads the WRONG field. The wrapper wrote the anchor with
    `awk '{print $22}'` — a positional split — so the two halves of one identity
    anchor disagreed for any process whose comm carries a space.

    Why that is fail-danger rather than cosmetic: the writer's value is compared
    against the reader's in `_pid_is_a_stranger`, and a mismatch means "not our
    rail". That reads `lost`, which is the state `start` ACCEPTS — so a second rail
    is put on a PR that is still merging (#3061), the exact hazard this anchor
    exists to close.

    Non-vacuity: arm 2 runs the old positional expression on the SAME synthetic
    `/proc` line and asserts it does NOT produce the right field (measured on a
    live process whose comm is `a b)c`: positional -> 0, reader -> the real ticks).
    """
    body = rd.child_script(Path("/x/admin-merge.sh"), "o/r", 42,
                           tmp_path / "l.log", tmp_path / "a.txt", [])
    script = body[-1]
    parse_programs = [p for p in re.findall(r"awk '([^']*)'", script) if "sub(" in p]
    assert len(parse_programs) == 1, (
        f"expected exactly one parsing awk in the wrapper, found {parse_programs}")
    assert "print $22" not in script, (
        "the positional split over the whole line is back — it misses the field "
        "whenever the comm carries a space (the reader's rsplit exists for that)")

    # A synthetic `/proc/<pid>/stat`: comm `(a b)c)` — spaces AND a paren — and a
    # distinctive field 22, so the wrong field cannot coincide with the right one.
    fields = [str(n) for n in range(1, 41)]
    fields[21] = "555777"                     # field 22 is starttime
    stat = tmp_path / "stat"
    stat.write_text(f"1234 (a b)c) {' '.join(fields[2:])}\n", encoding="utf-8")

    expected = "555777"
    got = subprocess.run(["awk", parse_programs[0], str(stat)],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert got == expected, f"the wrapper's awk reads {got!r}, not field 22 ({expected})"

    positional = subprocess.run(["awk", "{print $22}", str(stat)],
                               capture_output=True, text=True, check=True).stdout.strip()
    assert positional != expected, (
        "this fixture does not discriminate: the positional split happens to agree"
        " here, so it proves nothing")


def test_a_live_named_rail_with_no_child_started_keeps_the_documented_wedge(tmp_path):
    """The identity test must DEGRADE when the stamp is absent, not guess.

    An artifact written before `CHILD_STARTED=` existed records nothing to compare,
    so the claim is kept (fail-safe) and `collect` names the residual and its way
    out. This is `test_a_named_rail_under_spawning_is_not_no_rail`'s arm stated as
    its own contract: unmeasurable ⇒ claim kept, with the recovery in the reason.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    art = rd.artifact_path(sd, "o/r", 93)
    logp = rd.log_path(sd, "o/r", 93)
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    rail = subprocess.Popen(["/bin/sleep", "20"], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        art.write_text(f"PR=93\nREPO=o/r\nSTARTED_AT={old}\nLOG={logp}\n"
                       f"SPAWNING=1\nCHILD_PID={rail.pid}\n", encoding="utf-8")
        st = rd.collect(sd, "o/r", 93)
        assert st.state == rd.WAITING, st.as_dict()
        assert "CHILD_STARTED" in st.reason and f"rm {st.artifact}" in st.reason
    finally:
        rail.kill()
        rail.wait()


def test_refuses_to_detach_a_rail_carrying_the_override_hatch(tmp_path, monkeypatch):
    """⛔ Detachment must never become the way a bypass gets lifted out of sight."""
    co = make_checkout(tmp_path)
    monkeypatch.setenv("AGENT_ADMIN_MERGE_OVERRIDE", "1")
    with pytest.raises(rd.Refused, match="AGENT_ADMIN_MERGE_OVERRIDE"):
        launch(tmp_path / "state", co, pr=16)


def test_refuses_when_the_rail_script_is_absent(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(rd.Refused, match="link_agent_infra_scripts"):
        rd.launch(3, cwd=bare, state_dir=tmp_path / "state", repo="o/r")


# ── a missing `gh` binary is an UNREADABLE answer, never a traceback ──────────
#
# `subprocess.run(["gh", …])` raises `FileNotFoundError` when the BINARY is
# absent. That killed every case in this module on the fleet's pool, measured
# TWICE before anyone believed it — on `pi-runner-vm-2` (2026-11-03, shard
# `test (d)` by this branch's record, `test (a)` by main's, 21 arms vs 20;
# neither job log is retrievable here, so the root is what is asserted and not
# the integer) and on `pi-runner-vm-6`/Linux-ARM64, run `41732583553`, where all
# 20 were killed. Both pool measurements are kept: each is the provenance a
# reader needs to know this is a real pool condition, not a local quirk.
#
# The tool's own contract already names the degraded answer (`_head_sha` →
# "unreadable", `_resolve_repo` → `Refused`); only the spawn was unguarded, so
# the missing binary escaped as an exception the callers never see in
# production.
#
# ⚠️ `origin/main` landed the SAME fix independently (`_gh` introduced by
# `f422799c4`, #10724; its lines edited later by `815a9495b`, #10622) and arrived
# here as a content conflict. The merge keeps
# ONE helper and ONE arm per layer rather than both sides' copies: the duplicate
# owner is the shape #10777 exists to remove (the merge left two copies of this
# very helper once, and the second binding silently shadowed the first —
# `test_this_module_defines_no_helper_twice` pins that class of defect). Where
# the two sides overlapped, the surviving arm asserts the UNION — "not on PATH"
# (this branch's precision) *and* "gh repo view" / "install" (main's remedy
# text); `HEAD=unreadable` in the artifact (this branch, the persistent record)
# *and* the rail reaching MERGED (main, the end-to-end outcome).
#
# ⛔ The CONTRACT layer is not redundant with either: `test_gh_never_returns_None_for_a_missing_binary`
# is the only arm that reds when `_gh` stops answering (returns `None`) — the
# state in which BOTH callers crash at `out.returncode`. It is what makes the
# absence of an `out is not None` guard safe rather than lucky.
def _path_without_gh(tmp_path: Path, *needed: str) -> str:
    """A `PATH` holding just the binaries the rail/child need, never `gh`.

    `*needed` stages extra tools: the detached child stamps its artifact with
    `date -u`, so a `PATH` without `date` makes the launch under test fail for a
    reason the arm is not about.

    #9018: `runs-on: ubuntu-latest` names a GitHub-hosted IMAGE, but the fallback
    pool carries that same label on a bare runner whose service PATH is a minimal
    non-login one — where `gh` is simply absent. Absence is SIMULATED here rather
    than inherited, so the arm means the same thing on every runner.

    ⛔ ONE DEFINITION, and `*needed` is load-bearing: a merge once left two
    top-level definitions of this helper, the later one-argument binding
    shadowing the first. Two top-level definitions of one name is SILENT
    SHADOWING — python binds the last, and ruff's F811 stays quiet because the
    earlier one is still referenced — so the `*needed` form went unreachable and
    any `_path_without_gh(tmp_path, "date")` call became a `TypeError`.
    `test_this_module_defines_no_helper_twice` and
    `test_the_ghless_path_helper_stages_the_tools_it_is_asked_for` pin it, and
    `test_the_ghless_path_helper_is_reusable_on_one_tmp_path` pins that a SECOND
    call is additive rather than an `FileExistsError`.
    """
    bin_dir = tmp_path / "nogh-bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("bash", "sleep", "date", "sh", "env", *needed):
        src = shutil.which(name)
        assert src, f"the fixture needs {name} on the host PATH"
        dst = bin_dir / name
        # BOTH halves of the reuse hazard: the directory (`exist_ok=True`) AND
        # the staged symlinks. `is_symlink` rather than `exists` so this asks
        # about OUR staging, not about the host binary the link resolves to.
        if not dst.is_symlink():
            dst.symlink_to(src)
    assert not (bin_dir / "gh").exists()
    return str(bin_dir)


def test_gh_never_returns_None_for_a_missing_binary(tmp_path, monkeypatch):
    """`_gh`'s annotation is a CONTRACT the callers may rely on, not a hint.

    `-> _GhRun` means the missing-binary case is ANSWERED (`rc=127`, with a
    `cause`), never signalled by `None` — so a caller needs no `out is not None`
    guard, and one that carries a guard is describing a return type this
    function does not have.

    This arm is the invariant that keeps the absence of such guards safe: a
    `None`-returning `_gh` reds the assertion below, before a guard can rot into
    a second, wrong description of the function. NON-VACUOUS — proved against a
    mutant of THIS tree (`return None` in the `except` arm): the arm fails at
    `out is not None`, and both callers would then crash at `out.returncode`.
    """
    co = make_checkout(tmp_path)
    monkeypatch.setenv("PATH", _path_without_gh(tmp_path))
    out = rd._gh(["repo", "view"], co)
    assert out is not None, "`_gh` returned None; its annotation says it cannot"
    assert out.returncode == 127, out.returncode
    assert "command not found" in (out.stderr or ""), out.stderr
    # `getattr` on the CAUSE too, not just on the message: the pre-fix module
    # returns a bare `CompletedProcess`, so a direct `out.cause` reds with
    # `AttributeError` while EVALUATING the assertion — a proof that reds for the
    # wrong reason is not one (review, #10777; same guard as the chdir arm).
    assert "not on PATH" in (getattr(out, "cause", "") or ""), getattr(out, "cause", "")


def test_a_missing_gh_is_a_REFUSAL_that_says_what_to_fix(tmp_path, monkeypatch):
    """`gh` absent is a precondition the launcher enforces, never a crash.

    The refusal has to carry all three facts a lane needs to act: the COMMAND
    that failed (`gh repo view`), the CAUSE (`not on PATH` — not "auth expired",
    not "no remote"), and the REMEDY (`install`, or `--repo` to name the target
    itself). Any one of them missing sends the operator guessing.

    ⛔ THIS ARM IS THE FOLD OF TWO (`#10622` merge, then `#10975`). Main's
    `test_a_missing_gh_is_a_REFUSAL_not_a_traceback` asserted the same refusal
    and the word `installed`; this branch's arm asserts the same message with
    two MORE facts (`not on PATH`, `pass --repo`). Keeping both would be two
    arms for one message, so the surviving arm asserts the UNION — and
    `installed` is pinned here rather than dropped, because it is the only word
    that says the CLI may ALREADY be there (the branch-side wording reads as
    "install it" at a binary that might be installed and misconfigured).
    """
    co = make_checkout(tmp_path)
    monkeypatch.setenv("PATH", _path_without_gh(tmp_path))
    with pytest.raises(rd.Refused) as exc:
        rd._resolve_repo(co)
    msg = str(exc.value)
    assert "gh repo view" in msg, msg
    assert "not on PATH" in msg, msg
    # ⛔ NOT REDUNDANT with `install`: `"install" in "installed"` is True, so
    # deleting the longer word leaves the shorter assertion green. This one is
    # what reds when main's half of the union is dropped in a future merge.
    assert "installed" in msg, msg
    assert "install" in msg, msg
    assert "pass --repo" in msg, msg


def test_with_no_gh_the_rail_still_starts_and_the_head_reads_unreadable(tmp_path, monkeypatch):
    """`launch()` degrades instead of crashing, and the record says so.

    A caller that passes `repo=` has already settled the one thing `gh` was
    needed for, and the head is recorded as `unreadable` — the same value
    `_head_sha` returns when `gh` runs and fails. Pre-fix this raised
    `FileNotFoundError` out of `launch()`, so the launcher died instead of
    stamping its artifact and the rail never outlived the lane.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    monkeypatch.setenv("PATH", _path_without_gh(tmp_path))
    ln = launch(sd, co, pr=21)
    assert ln.pid > 0, "the rail did not start without `gh`"
    assert ln.head == "unreadable", ln.head
    assert "HEAD=unreadable" in Path(ln.artifact).read_text(encoding="utf-8")
    st = wait_state(sd, 21, rd.MERGED)
    assert st.state == rd.MERGED, not_merged(sd, 21, st)


def test_a_gh_that_cannot_execute_is_not_reported_as_missing(tmp_path, monkeypatch):
    """Two spawn failures, two answers (review, #10777).

    A `gh` that is PRESENT but not executable raises `PermissionError` — not the
    `FileNotFoundError` of a PATH miss — and its remedy is `chmod +x`, never
    "install the GitHub CLI" (a binary that is present is not a binary that is
    missing). ⛔ Before the split that failure escaped `launch()` entirely, as a
    `PermissionError` traceback, and no version of `_gh` ever answered it with
    `127`: the mutant that folds `except OSError` into the 127 arm is what tells
    the operator to install a binary they already have, and this arm is what reds
    on that mutant.

    `EAGAIN`/`ENOMEM` from `fork` under cell load is a THIRD cause with its own
    remedy ("retry when it is not loaded"), asserted in
    `test_the_spawn_remedy_is_keyed_by_errno` — deliberately not a `chmod`.

    ⛔ AND THE REMEDY IS ASSERTED, not just the cause (review, `#10777`): this
    docstring once claimed a `chmod` remedy that no path in the code printed, so
    the claim was unfalsifiable from the artifact it describes. The `OSError` arm
    is keyed by errno now, and this arm pins the word it promises.
    """
    bin_dir = tmp_path / "unejecutable-bin"
    bin_dir.mkdir()
    # A real file named `gh` on PATH, deliberately not executable.
    (bin_dir / "gh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(rd.Refused) as exc:
        rd._resolve_repo(tmp_path)
    msg = str(exc.value)
    assert "is not on PATH" not in msg, msg
    assert "pass --repo" in msg, msg
    assert "chmod" in msg, (
        f"the remedy must match the cause (it is present, just not executable): {msg}")

    # And the same call answers `unreadable` rather than raising on this arm.
    assert rd._head_sha(tmp_path, "o/r", 5) == "unreadable"


def test_the_spawn_remedy_is_keyed_by_errno(tmp_path):
    """Every branch of `_oserror_remedy` names ITS OWN repair (review, `#10777`).

    Deleting a branch used to leave the suite green: only the EACCES and ENOTDIR
    arms were reachable from a test, so the `EAGAIN`/`ENOMEM` and generic
    fallbacks could be re-collapsed onto another cause's advice with nothing to
    notice. This arm calls the helper directly, which is the only way to reach a
    fork failure without inducing one.
    """
    denied = rd._oserror_remedy(PermissionError(errno.EACCES, "denied"))
    assert "chmod" in denied, denied
    # ⛔ BOTH OBJECTS, because this errno cannot say which one (review, `#10777`):
    # an untraversable `PATH` directory and a non-executable file both arrive as
    # `PermissionError(13, filename='gh')`, so a `chmod +x`-only remedy is wrong
    # advice for one of them. Pinned, or the directory half can be deleted unseen.
    assert "traversable" in denied, denied
    assert "chmod" in rd._oserror_remedy(PermissionError(errno.EPERM, "denied"))

    # ⛔ AND THE CWD IS BLAMED ONLY FOR AN ERRNO A `chdir` CAN RAISE (review,
    # `#10777`): otherwise the gate is unpinned, because every cwd-side arm uses
    # EACCES and gated/ungated code answer it identically. EIO cannot come from a
    # chdir, so it must not be reported as a working-directory fault.
    not_a_chdir_errno = rd._oserror_remedy(OSError(errno.EIO, "i/o error", "/some/cwd"))
    # The generic fallback legitimately mentions the cwd as ONE thing to check; what
    # is forbidden is CLAIMING it is the fault.
    assert "could not be used" not in not_a_chdir_errno, not_a_chdir_errno
    assert "is not a directory" not in not_a_chdir_errno, not_a_chdir_errno
    eagain = rd._oserror_remedy(OSError(errno.EAGAIN, "try again"))
    assert "loaded" in eagain, eagain
    assert "chmod" not in eagain, eagain
    assert "loaded" in rd._oserror_remedy(OSError(errno.ENOMEM, "out of memory"))
    assert "not a directory" in rd._oserror_remedy(OSError(errno.ENOTDIR, "nope", str(tmp_path)))
    generic = rd._oserror_remedy(OSError(errno.E2BIG, "too big"))
    assert "check the working directory" in generic, generic

    # ⛔ THE OBJECT GATES THE CHDIR-SIDE ERRNO TOO (review, `#10777`): a PATH entry
    # that is a regular FILE raises `ENOTDIR` with `filename='gh'`, and answering it
    # "the working directory is not a directory" blames a cwd that is fine.
    exec_side_enotdir = rd._oserror_remedy(
        NotADirectoryError(errno.ENOTDIR, "not a directory", "gh"))
    # The generic fallback mentions the cwd as ONE of two things to check; what is
    # forbidden is CLAIMING it is the fault.
    assert "is not a directory" not in exec_side_enotdir, exec_side_enotdir
    assert "could not be used" not in exec_side_enotdir, exec_side_enotdir

    # ⛔ THE ERRNO IS NOT THE CAUSE ON ITS OWN (review, `#10777`): an `EACCES` on
    # the WORKING DIRECTORY must not be answered with `chmod +x` on a `gh` that is
    # fine. `filename` carries which object failed, and it must decide first.
    cwd_denied = rd._oserror_remedy(PermissionError(errno.EACCES, "denied", str(tmp_path)))
    assert "chmod" not in cwd_denied, cwd_denied
    assert "working directory" in cwd_denied, cwd_denied


def test_an_exec_side_failure_does_not_blame_the_working_directory(tmp_path, monkeypatch):
    """A `PATH` entry that is a regular FILE is an EXEC failure, not a cwd failure.

    Review, `#10777`: `ENOTDIR` was checked before the failing object, so this
    shape was answered "the working directory is not a directory" about a cwd that
    is perfectly good — `filename` is `'gh'` here, because CPython sets it to
    `argv[0]` on the exec side. The object has to gate the errno, not the reverse.
    """
    a_file = tmp_path / "a-file"
    a_file.write_text("x", encoding="utf-8")
    monkeypatch.setenv("PATH", str(a_file))
    out = rd._gh(["repo", "view"], tmp_path)
    assert out.returncode == 126, (out.returncode, out.cause)
    assert "is not a directory" not in out.remedy, out.remedy
    assert "could not be used" not in out.remedy, out.remedy


def test_an_unreadable_cwd_is_not_a_broken_gh_install(tmp_path):
    """`EACCES` on the CWD is a directory problem, never a `gh` problem.

    Review, `#10777`: keying the remedy on errno alone re-created the defect this
    helper exists to prevent — `chmod 000` on a directory used as `cwd` raises
    `PermissionError(errno=13, filename=<cwd>)`, and the operator was told to
    `chmod +x` a binary that is fine. The exception already carries which object
    failed, so the object decides before the errno.
    """
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        out = rd._gh(["repo", "view"], locked)
    finally:
        locked.chmod(0o700)
    assert out.returncode == 126, (out.returncode, out.cause)
    assert "chmod" not in out.remedy, out.remedy
    assert "working directory" in out.remedy, out.remedy
    assert str(locked) in out.cause, out.cause


def test_a_real_gh_that_exits_127_is_not_reported_as_missing(tmp_path, monkeypatch):
    """`gh` exiting 127 is a REFUSAL BY the binary, never evidence it is absent.

    Review, `#10777`: `_gh` answers 127 for two unrelated conditions — a fabricated
    PATH miss (which carries a `cause`) and a REAL process that exited 127 (which
    never does) — and the refusal collapsed them, so an operator whose `gh` shim
    refused (`gh-shim: REFUSING to exec … — unset AGENT_GH_REAL`, the fleet's own
    shim does exactly this) was told the binary was not installed.
    """
    bin_dir = tmp_path / "gh-127-bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(
        "#!/bin/sh\n"
        'echo "gh-shim: refusing to exec an unresolvable gh" >&2\n'
        "exit 127\n",
        encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    out = rd._gh(["repo", "view"], tmp_path)
    # `getattr` in the message AND in the assertion, so the pre-fix module (whose
    # `_gh` returns a bare `CompletedProcess` with no `cause`) reds on the
    # BEHAVIOUR it is accused of, not on an `AttributeError` raised while
    # formatting the proof (review, #10777).
    assert out.returncode == 127, (out.returncode, getattr(out, "cause", ""))
    assert getattr(out, "cause", "") == "", (
        "a real process's exit code must not be given a fabricated cause: "
        f"{getattr(out, 'cause', '')!r}")

    with pytest.raises(rd.Refused) as exc:
        rd._resolve_repo(tmp_path)
    msg = str(exc.value)
    assert "not on PATH" not in msg, msg
    assert "install the GitHub CLI" not in msg, msg
    assert "ran and refused" in msg, msg
    assert "unresolvable gh" in msg, msg


def test_a_real_gh_that_exits_124_is_not_told_to_install_the_cli(tmp_path, monkeypatch):
    """`gh` exiting 124 is a process that RAN and timed out, not a missing install.

    Review cycle 5, finding 2. The 124 arm is gated on `cause` (fabricated by the
    `GH_PROBE_TIMEOUT_S` bound), so a REAL process exiting 124 skipped it and fell
    through to the generic arm, whose remedy is "is `gh` installed and
    authenticated? … or install the `gh` CLI" — advice that is provably wrong for a
    binary that just produced an exit status. Same defect the 126 and 127 real arms
    fix, one code over; `124` is what a wrapper around `gh` returns on its own
    timeout (`timeout(1)`'s convention), so it is reachable in practice.
    """
    bin_dir = tmp_path / "gh-124-bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(
        "#!/bin/sh\n"
        'echo "gh-wrapper: gave up waiting for the API" >&2\n'
        "exit 124\n",
        encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    out = rd._gh(["repo", "view"], tmp_path)
    assert out.returncode == 124, (out.returncode, out.cause)
    assert out.cause == "", f"a real process's exit code must not be given a cause: {out.cause!r}"

    with pytest.raises(rd.Refused) as exc:
        rd._resolve_repo(tmp_path)
    msg = str(exc.value)
    assert "install the `gh` CLI" not in msg, msg
    assert "installed and authenticated" not in msg, msg
    assert "exited 124" in msg, msg
    assert "hit its own timeout" in msg, msg
    assert "gave up waiting" in msg, msg


def test_a_real_gh_that_exits_126_is_not_reported_as_unexecutable(tmp_path, monkeypatch):
    """`gh` exiting 126 is a FAILURE of a process that ran, never "cannot execute".

    Review, `#10777`: the sibling 127 pair already separates a fabricated PATH
    miss (which carries a `cause`) from a real process's own exit code, but the
    126 arm was NOT gated on `cause` — so a real `gh` that exited 126 was told it
    "could not be executed", which its own exit proves false, and `out.stderr`
    (the only thing that says why) was dropped on the floor. Same defect class as
    the 127 collapse this PR exists to fix.
    """
    bin_dir = tmp_path / "gh-126-bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(
        "#!/bin/sh\n"
        'echo "gh: 126 from a binary that ran" >&2\n'
        "exit 126\n",
        encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    out = rd._gh(["repo", "view"], tmp_path)
    assert out.returncode == 126, (out.returncode, getattr(out, "cause", ""))
    assert getattr(out, "cause", "") == "", (
        "a real process's exit code must not be given a fabricated cause: "
        f"{getattr(out, 'cause', '')!r}")

    with pytest.raises(rd.Refused) as exc:
        rd._resolve_repo(tmp_path)
    msg = str(exc.value)
    assert "could not be executed" not in msg, msg
    assert "exited 126" in msg, msg
    # The stderr detail must survive: it is the only thing that says WHY.
    assert "126 from a binary that ran" in msg, msg


def test_a_gh_that_exits_zero_with_no_repo_is_not_an_install_problem(tmp_path, monkeypatch):
    """rc=0 with EMPTY stdout reaches the refusal too, and it is not an install fault.

    Review, `#10777`: the success guard requires `returncode == 0 AND stdout.strip()`,
    so a `gh` that exited 0 and printed nothing fell through the 127/126 pairs into
    the generic arm — which told the operator "`gh repo view` failed … is `gh`
    installed and authenticated? … install the `gh` CLI" about a binary that exited
    0, i.e. demonstrably ran. Same dishonesty as the 126 collapse, one branch down.
    """
    bin_dir = tmp_path / "gh-silent-bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    out = rd._gh(["repo", "view"], tmp_path)
    assert out.returncode == 0, (out.returncode, getattr(out, "cause", ""))
    assert out.stdout == "", repr(out.stdout)

    # ⛔ THE OTHER HALF OF THE SAME SEAM (review, `#10777`): `_resolve_repo` refuses
    # by name here, but `_head_sha`'s bare `returncode == 0` guard let the same
    # silent `gh` through as `""` — which `launch()` stamps as `HEAD=` (blank), a
    # value indistinguishable from a field never written. Both call sites read the
    # same surface, so both must answer a NAMED state. This assertion fails on
    # `return ... if out.returncode == 0 else "unreadable"`.
    assert rd._head_sha(tmp_path, "o/r", 5) == "unreadable", (
        "an empty head must read as `unreadable`, not as an empty string")

    with pytest.raises(rd.Refused) as exc:
        rd._resolve_repo(tmp_path)
    msg = str(exc.value)
    # The claim that must NOT survive: a 0-exit binary is not a broken install.
    assert "install the `gh` CLI" not in msg, msg
    assert "is `gh` installed and authenticated" not in msg, msg
    assert "exited 0" in msg, msg
    assert "pass `--repo" in msg, msg


def test_a_cwd_that_is_not_a_directory_is_not_a_broken_gh_install(tmp_path):
    """An `ENOTDIR` cwd is ALSO an `OSError`, and "repair the install" is wrong for it.

    Review, `#10777`: the chdir remedy was made per-cause, but only for the
    `FileNotFoundError` arm — a cwd that is present-but-a-FILE lands on
    `NotADirectoryError` and was still told to repair a `gh` install that is fine.
    Same defect one branch over, so the `OSError` arm is keyed by errno too.
    """
    not_a_dir = tmp_path / "not-a-dir"
    not_a_dir.write_text("x", encoding="utf-8")
    out = rd._gh(["repo", "view"], not_a_dir)
    assert out.returncode == 126, (out.returncode, out.cause)
    assert "repair the `gh` install" not in out.remedy, out.remedy
    assert "not a directory" in out.remedy, out.remedy
    assert str(not_a_dir) in out.cause, out.cause

    with pytest.raises(rd.Refused) as exc:
        rd._resolve_repo(not_a_dir)
    assert "repair the `gh` install" not in str(exc.value), str(exc.value)


def test_a_failed_chdir_is_not_reported_as_a_missing_binary(tmp_path):
    """A `chdir` failure is ALSO a `FileNotFoundError`, but the missing thing is the CWD.

    CPython 3.12 `subprocess.py:1940-1955`: a failed `chdir` sets
    `err_filename = cwd`, while a failed exec sets it to the executable. So the
    `filename == "gh"` discrimination in `_gh` is load-bearing — without it the
    two failures collapse and the operator is told that a binary they have is
    absent, sending them to install the wrong thing.

    ⛔ WHY THIS ARM EXISTS (rail review, P2 on `f25cda23d`): the refusal arm above
    is a `PermissionError`, which takes the `except OSError` branch and NEVER
    reaches the discrimination — so before this arm existed the whole suite stayed
    GREEN with the discrimination mutated.

    ⛔ AND THE MUTANT IS NAMED EXACTLY (review, #10777 — a false measurement in a
    docstring is the defect this file exists to refuse): this arm reddens on
    `if False:` — the discrimination COLLAPSED to 127, so a `chdir` miss answers
    "`gh` is not on PATH". Measured at head `32ad7fbe9`: `if False:` → 1 failed,
    and it is THIS arm. It does NOT redden on `if True:` (that mutant forces every
    `FileNotFoundError` down the cwd branch and this arm still gets its 126);
    `if True:` is owned by the absence arms —
    `test_gh_never_returns_None_for_a_missing_binary`,
    `test_a_missing_gh_is_a_REFUSAL_that_says_what_to_fix`,
    `test_a_gh_on_path_with_a_dead_shebang_is_not_reported_as_missing` and
    `test_collect_without_the_gh_cli_refuses_by_name_instead_of_tracebacking`
    (measured: 4 failed) — so the pair of mutants together pin both directions.
    """
    missing = tmp_path / "this-cwd-does-not-exist"
    out = rd._gh(["repo", "view"], missing)
    # `getattr` on the MESSAGE, so the pre-fix module (whose `_gh` returns a bare
    # `CompletedProcess` with no `cause`) fails on the BEHAVIOUR — rc 127 for a
    # chdir miss — instead of on an `AttributeError` raised while formatting this
    # assertion. Review, #10777: a proof that reds for the wrong reason is not one.
    assert out.returncode == 126, (out.returncode, getattr(out, "cause", ""))
    assert "is not on PATH" not in out.cause, out.cause
    assert str(missing) in out.cause, (
        f"the cause must name the CWD that could not be entered: {out.cause}")

    # ⛔ THE REMEDY IS PINNED TOO, not just the returncode (review, #10777): the
    # 126 arm used to append one hard-coded "fix the `gh` install" for every cause,
    # which is wrong advice for a reaped worktree — there is nothing wrong with the
    # `gh` install. Nothing asserted the message half, so it stayed wrong.
    with pytest.raises(rd.Refused) as exc:
        rd._resolve_repo(missing)
    msg = str(exc.value)
    assert str(missing) in msg, msg
    assert "fix the `gh` install" not in msg, (
        f"a missing CWD is not a broken `gh` install: {msg}")
    assert "live checkout" in msg, msg


def test_a_gh_on_path_with_a_dead_shebang_is_not_reported_as_missing(tmp_path, monkeypatch):
    """`filename == "gh"` is NOT evidence the binary is absent (review, #10777).

    CPython passes the literal argv[0] for ANY exec failure of a bare name, so a
    `gh` that IS on `PATH` but whose shebang interpreter is gone raises the same
    `FileNotFoundError(filename="gh")` as a PATH miss. Measured 2026-11-04 with the
    broken candidate as the SOLE `PATH` entry: `shutil.which("gh")` resolves it and
    `subprocess` still will not run it, so the predicate alone answered 127 "not on
    PATH — install the GitHub CLI" for a binary the operator already had, which is
    the misdiagnosis the 126 arm exists to prevent.

    ⚠️ With a WORKING `gh` later on `PATH`, CPython's own walk silently skips this
    candidate and runs the later one (`executable_list` is built from
    `os.get_exec_path` at `subprocess.py:1880-1882`; no `execvp` is called on this path) — so this arm must own `PATH` completely or it
    pins nothing.
    """
    bin_dir = tmp_path / "dead-shebang-bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/nonexistent/interp\necho hi\n", encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    assert shutil.which("gh") == str(gh), (
        "precondition: the binary IS on PATH, or this arm tests the PATH-miss case")

    out = rd._gh(["repo", "view"], tmp_path)
    # `getattr` on the cause, so the pre-fix module reds on the BEHAVIOUR ("a live
    # binary on PATH is not a missing one") rather than on an `AttributeError`
    # raised while formatting the assertion (review, #10777).
    assert out.returncode == 126, (out.returncode, getattr(out, "cause", ""))
    cause = getattr(out, "cause", "")
    assert "is not on PATH" not in cause, cause
    assert str(gh) in cause, cause

    with pytest.raises(rd.Refused) as exc:
        rd._resolve_repo(tmp_path)
    msg = str(exc.value)
    assert "not on PATH" not in msg, msg
    assert "install the GitHub CLI" not in msg, msg


def test_the_ghless_path_helper_stages_the_tools_it_is_asked_for(tmp_path):
    """`_path_without_gh(tmp, "cat")` must stage `cat` — a shadowed form raised TypeError.

    A merge left TWO top-level definitions of this helper: the `*needed` form
    first, then a one-argument form, which python bound last. The base set covers
    what the detached child's `date -u` stamp needs, and `*needed` is how an arm
    stages anything further; the shadowed form made that whole form unreachable,
    so this call raised `TypeError: _path_without_gh() takes 1 positional
    argument but 2 were given`.
    """
    path = _path_without_gh(tmp_path, "cat")
    assert (Path(path) / "cat").exists(), "the requested tool was not staged"
    assert not (Path(path) / "gh").exists(), "the helper must never stage `gh`"


def test_the_ghless_path_helper_is_reusable_on_one_tmp_path(tmp_path):
    """A SECOND call must ADD tools, not raise `FileExistsError`.

    `main`'s copy of this helper staged its base set with `mkdir(exist_ok=True)`;
    this branch's copy used a bare `mkdir()`, and the merge kept the stricter form
    with no reason stated (review, #10777). Nothing reddens today only because no
    arm calls the helper twice on one `tmp_path` — so the next arm that needs two
    different PATHs in one test would get a bare `FileExistsError` instead of a
    staged PATH, and the failure would name the fixture, not the arm.

    ⛔ THE HAZARD HAS TWO HALVES, and fixing only the first is why the arm below
    was written: `mkdir(exist_ok=True)` alone still reds, because the second call
    then dies on `dst.symlink_to(src)` for every base tool already staged. BOTH
    are idempotent here.

    Non-vacuity, measured on each half SEPARATELY (this arm, `-k
    is_reusable_on_one_tmp_path`, one mutation at a time):

      bare `mkdir()`                         -> 1 failed  (FileExistsError at `bin_dir.mkdir()`)
      `mkdir(exist_ok=True)`, no `is_symlink` -> 1 failed  (FileExistsError at `dst.symlink_to(src)`)
      both in place                           -> 1 passed

    ⛔ THE CALL SITES, NOT `pathlib.py` LINE NUMBERS (review, `#10777`). These cited
    `:1313` / `:1388` are `pathlib`'s own positions, not this branch's file: they are
    the `os.mkdir()` inside `Path.mkdir` and the `os.symlink()` inside
    `Path.symlink_to`, version-dependent, so they would rot at the next CPython.
    Naming the raising call makes the measurement re-runnable against any interpreter.
    """
    first = _path_without_gh(tmp_path)
    second = _path_without_gh(tmp_path, "cat")
    assert first == second, "the helper must reuse one staged directory"
    assert (Path(second) / "cat").exists(), "the second call staged nothing new"
    assert (Path(second) / "date").exists(), "the base set was lost on reuse"
    assert not (Path(second) / "gh").exists(), "the helper must never stage `gh`"


def test_this_module_defines_no_helper_twice():
    """A duplicate top-level definition is SILENT SHADOWING, not dead code.

    `ruff`'s F811 stays quiet when the first binding is still referenced (both
    were, from different arms), so nothing in CI could see #10777's duplicated
    `_path_without_gh`. Standalone: `python3 -m pytest tests/test_rail_detach.py`.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    names = [n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"shadowed top-level definition(s): {dupes}"


def test_child_invokes_only_the_one_lander(tmp_path):
    """#1178: no second lander. The child argv is
    `bash -c <self-naming wrapper> rail <artifact> <rail-argv…>` — one lander
    invocation, nothing else."""
    co = make_checkout(tmp_path)
    art, log = tmp_path / "a.md", tmp_path / "l.log"
    argv = rd.child_script(co / rd.RAIL_REL, "o/r", 21, log, art, ["--dry-run"])
    body = argv[-1]
    assert "gh pr merge" not in body
    assert "--admin" not in body
    assert body.count("admin-merge.sh") == 1
    assert "--dry-run" in body          # operator args pass through verbatim
    assert "AGENT_ADMIN_MERGE_OVERRIDE" not in body


# ── the runner's TOOL CONTRACT: `gh` ABSENT is a failed query, not a crash ───
def _cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in rd.FORBIDDEN_ENV}   # the launcher refuses a carried bypass
    env["PATH"] = _path_without_gh(tmp_path)
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "rail_detach.py"), *args],
        env=env, capture_output=True, text=True,
    )


def test_launch_without_the_gh_cli_starts_and_stamps_an_unreadable_head(tmp_path):
    """The launcher must not CRASH on a runner with no `gh`.

    Pre-fix `_head_sha`'s `subprocess.run(["gh", ...])` raised out of `launch()`,
    so the CLI printed a traceback, wrote no artifact and returned 1 — the rail
    never started at all on the bare pool. `HEAD=unreadable` is the honest stamp:
    the head is a best-effort read (the rail's own log remains the primary
    record), never a reason to refuse to detach.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    proc = _cli(tmp_path, "start", "77", "--repo", "o/r", "--cwd", str(co),
                "--state-dir", str(sd))
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout.startswith("STARTED(#77, pid "), proc.stdout
    art = rd.artifact_path(sd, "o/r", 77).read_text(encoding="utf-8")
    assert "HEAD=unreadable" in art, art


def test_collect_without_the_gh_cli_refuses_by_name_instead_of_tracebacking(tmp_path):
    """The one `collect` path that still needs `gh` refuses BY NAME, rc 2.

    Pre-fix this arm's trigger was "no artifact and no `--repo`", which exited 1
    with a `FileNotFoundError` — a read-only status verb tracebacking, worse than
    any verdict (the fleet beat then gets no state to act on). Two things changed
    since, and this arm pins what is LEFT rather than what is gone:

      * zero candidates and one candidate are answered from DISK (rc 3 / the
        artifact's own `REPO=`), so `gh` is not asked at all — that is
        `test_cli_collect_answers_absent_without_asking_gh` below;
      * TWO artifacts for one PR number under different repos is a target the disk
        genuinely cannot name, so this is the remaining `_resolve_repo` path — and
        a missing `gh` there must still be a NAMED refusal, never a traceback.

    Both halves matter: the first stops a status read depending on the network, the
    second keeps the failure legible where it still can.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    for slug in (("o", "r"), ("o", "s")):      # same PR, two repos ⇒ ambiguous
        rd.artifact_path(sd, "/".join(slug), 779).write_text(
            f"PR=779\nREPO={'/'.join(slug)}\nSTARTED_AT={rd._iso_now()}\n",
            encoding="utf-8")
    proc = _cli(tmp_path, "collect", "779", "--state-dir", str(sd))
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "could not resolve the repo" in proc.stderr
    assert "command not found" in proc.stderr      # names the cause, not just the effect
    assert "Traceback" not in proc.stderr


# ── PROOF + MUTANT: detached survives the lane's group kill; undetached dies ──
LANE = textwrap.dedent(
    """
    import sys, time
    sys.path.insert(0, {tools!r})
    import rail_detach as rd
    if {no_detach}:
        rd.DETACH_NEW_SESSION = False
    ln = rd.launch({pr}, cwd={co!r}, state_dir={sd!r}, repo="o/r")
    print(ln.pid, flush=True)
    time.sleep(120)      # the "lane": it is about to be killed by its harness
    """
)

LANE_ENV_DOC = (
    "The lane process group is killed with SIGKILL — the same thing a harness "
    "timeout does to a lane. Detachment is the only difference between the two arms."
)


@pytest.mark.parametrize("no_detach,expect", [(False, rd.MERGED), (True, rd.LOST)])
def test_detachment_is_what_survives_the_lane(tmp_path, no_detach, expect):
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    pr = 31
    env = dict(os.environ, FAKE_RAIL_SLEEP="3", PYTHONPATH=str(REPO_ROOT / "tools"))
    lane = subprocess.Popen(
        [sys.executable, "-c", LANE.format(tools=str(REPO_ROOT / "tools"), no_detach=no_detach,
                                           pr=pr, co=str(co), sd=str(sd))],
        cwd=str(REPO_ROOT), env=env, stdout=subprocess.PIPE, text=True,
        start_new_session=True,   # the LANE gets its own group, like a cmux pane
    )
    assert lane.stdout.readline().strip().isdigit()
    wait_state(sd, pr, rd.WAITING)
    os.killpg(os.getpgid(lane.pid), signal.SIGKILL)   # the harness kills the lane + its group
    lane.wait(timeout=10)

    st = wait_state(sd, pr, expect)
    assert st.state == expect, (
        f"expected {expect} (no_detach={no_detach}): {st.as_dict()}\n{LANE_ENV_DOC}"
    )
    if no_detach:
        # the defect, reproduced: the rail died with the lane and left no verdict
        assert st.merge_sha == "" and st.exit_code is None
    else:
        assert st.merge_sha == "deadbeefcafe1234"


# ── CLI surface ──────────────────────────────────────────────────────────────
def test_cli_start_then_collect(tmp_path, capsys):
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    rc = rd.main(["start", "41", "--repo", "o/r", "--cwd", str(co), "--state-dir", str(sd)])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("STARTED(#41, pid ")
    assert "collect 41" in out
    rc = rd.main(["collect", "41", "--repo", "o/r", "--state-dir", str(sd), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["state"] in (rd.WAITING, rd.MERGED)


def test_cli_passes_rail_args_after_a_double_dash(tmp_path, capsys):
    """The operator's own rail flags go through verbatim — and the launcher's own
    options are NOT swallowed by a greedy REMAINDER (a silent bug, locked here)."""
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    rc = rd.main(["start", "43", "--repo", "o/r", "--cwd", str(co),
                  "--state-dir", str(sd), "--", "--dry-run"])
    assert rc == 0
    capsys.readouterr()
    art = rd.artifact_path(sd, "o/r", 43).read_text()
    assert "--dry-run" in art and "RAIL_ARGS=43 --repo o/r --dry-run" in art
    assert Path(sd).exists()


def test_cli_collect_exit_codes(tmp_path, capsys):
    sd = tmp_path / "state"
    assert rd.main(["collect", "777", "--repo", "o/r", "--state-dir", str(sd)]) == 3
    assert capsys.readouterr().out.strip() == "RAIL(#777, absent, -, -)"
    co = make_checkout(tmp_path)
    rd.main(["start", "42", "--repo", "o/r", "--cwd", str(co), "--state-dir", str(sd)])
    wait_state(sd, 42, rd.MERGED)
    assert rd.main(["collect", "42", "--repo", "o/r", "--state-dir", str(sd)]) == 0
    # --assert-merged is the next fleet beat's gate: 0 only on a real merge
    assert rd.main(["collect", "42", "--repo", "o/r", "--state-dir", str(sd),
                    "--assert-merged"]) == 0
    capsys.readouterr()


def test_assert_merged_is_a_gate_on_every_non_merged_state(tmp_path, capsys):
    """⛔ `--assert-merged` IS A GATE WHOSE REFUSAL NO ARM COULD REACH.

    `EXIT CODES` publishes `1` as the fleet's MACHINE GATE for "the state is not
    `merged`", and the only arm that passed the flag read a `merged` artifact — so
    the refusal itself was unpinned: delete it, or widen it to `st.state == LOST`,
    and the whole suite (the arm above included, whose comment claims "0 only on a
    real merge") stayed green. That is an inert enforcer: it appears to protect
    the next beat and its termination condition can never fire.

    One arm per non-`merged` state, each an ARTIFACT so the state is exact rather
    than raced for. `refused` is the direction that matters most — the rail RAN
    and declined (a `--dry-run` exits 0), which is the shape a beat would read as
    permission if the gate were permissive.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    logp = rd.log_path(sd, "o/r", 91)
    art = rd.artifact_path(sd, "o/r", 91)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    logp.write_text("\n===== rail attempt started x (detached) =====\n"
                    "admin-merge: --dry-run performs no base run and merges nothing\n",
                    encoding="utf-8")

    def gate() -> int:
        return rd.main(["collect", "91", "--repo", "o/r", "--state-dir", str(sd),
                        "--assert-merged"])

    def plain() -> int:
        return rd.main(["collect", "91", "--repo", "o/r", "--state-dir", str(sd)])

    # 1. refused — the rail exited 0 without a merge line (a --dry-run)
    art.write_text(f"PR=91\nREPO=o/r\nSTARTED_AT={now}\nLOG={logp}\nEXIT=0\n",
                   encoding="utf-8")
    assert rd.collect(sd, "o/r", 91).state == rd.REFUSED
    assert plain() == 0
    assert gate() == 1

    # 2. lost — an expired `SPAWNING=1` sentinel: no rail ever ran, no verdict exists
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
    art.write_text(f"PR=91\nREPO=o/r\nSTARTED_AT={old}\nLOG={logp}\nSPAWNING=1\n",
                   encoding="utf-8")
    assert rd.collect(sd, "o/r", 91).state == rd.LOST
    assert plain() == 0
    assert gate() == 1

    # 3. waiting — a rail is still live and has decided nothing yet
    rail = subprocess.Popen(["/bin/sleep", "20"], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        art.write_text(f"PR=91\nREPO=o/r\nSTARTED_AT={old}\nLOG={logp}\n"
                       f"SPAWNING=1\nCHILD_PID={rail.pid}\n", encoding="utf-8")
        assert rd.collect(sd, "o/r", 91).state == rd.WAITING
        assert plain() == 0
        assert gate() == 1
    finally:
        rail.kill()
        rail.wait()
    capsys.readouterr()


# ── liveness must be TRUE on every box, and `gh` must never wedge the lock ───
def test_a_killed_rail_is_lost_on_a_box_without_procfs(tmp_path, monkeypatch):
    """`os.kill(pid, 0)` SUCCEEDS ON A ZOMBIE.

    Where procfs cannot answer, the old fallback was "kill(0) said yes, so it is
    alive" — which on macOS (where the lanes run) made a SIGKILLed rail read
    `waiting` FOREVER: `lost` was unreachable, and the duplicate-rail guard then
    refused every later `start` on that PR. The state letter now comes from a
    bounded local `ps` probe instead, so this holds without procfs.
    """
    monkeypatch.setattr(rd, "_proc_stat_letter", lambda pid: None)
    monkeypatch.setattr(rd, "_proc_cmdline", lambda pid: None)
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    marker = "/state/rail-o-r-9.md"
    try:
        os.kill(proc.pid, signal.SIGKILL)
        deadline = time.time() + 15
        while time.time() < deadline and rd._pid_alive(proc.pid, marker):
            time.sleep(0.05)
        # THE PREMISE, asserted rather than assumed: this pid still answers
        # kill(0) — so `False` below came from the state letter, not from ESRCH.
        os.kill(proc.pid, 0)
        assert rd._pid_alive(proc.pid, marker) is False
    finally:
        proc.wait()


def test_an_unbounded_gh_cannot_wedge_the_duplicate_rail_lock(tmp_path, monkeypatch):
    """`_head_sha` runs while the duplicate-rail flock is HELD.

    So an unbounded `gh` stalled on a saturating cell does not delay one launch —
    it holds the lock every later `start` for that PR must take, turning the
    #3061 guard into the thing that makes the PR unrailable. The call is
    informational (`HEAD` gates nothing), so a timeout costs nothing.
    """
    bindir = fake_gh(tmp_path, "#!/usr/bin/env bash\nsleep 8\n", sub="slowbin")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(rd, "GH_PROBE_TIMEOUT_S", 0.5)
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    t0 = time.time()
    ln = launch(sd, co, pr=71)
    elapsed = time.time() - t0
    # The LOWER bound is the vacuity guard: an instant `gh` would mean the stub
    # below never ran and this test proves nothing about the timeout.
    assert rd.GH_PROBE_TIMEOUT_S <= elapsed < rd.GH_PROBE_TIMEOUT_S + 3, \
        f"launch took {elapsed:.1f}s — not the {rd.GH_PROBE_TIMEOUT_S}s bound"
    assert ln.head == "unreadable"           # informational only; nothing gates on it
    assert wait_state(sd, 71, rd.MERGED).state == rd.MERGED   # the rail still ran


def test_an_unanswering_gh_repo_view_refuses_instead_of_hanging(tmp_path, monkeypatch):
    """The other `gh` call: naming the target. A timeout must REFUSE (an unnamed
    target is exactly what the GH_REPO check exists to prevent), not hang."""
    bindir = fake_gh(tmp_path, "#!/usr/bin/env bash\nsleep 8\n", sub="slowbin2")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(rd, "GH_PROBE_TIMEOUT_S", 0.5)
    co = make_checkout(tmp_path)
    t0 = time.time()
    with pytest.raises(rd.Refused) as ei:
        rd.launch(72, cwd=co, state_dir=tmp_path / "state")
    assert "did not answer within" in str(ei.value)
    assert rd.GH_PROBE_TIMEOUT_S <= time.time() - t0 < rd.GH_PROBE_TIMEOUT_S + 3


def test_an_unmeasurable_liveness_probe_keeps_the_rail_alive(tmp_path, monkeypatch):
    """No procfs AND no `ps`: unmeasurable must read ALIVE, never dead.

    Reading a live rail as dead would let a SECOND rail through (#3061) — the
    same reason `_ps_usable()` refuses to infer "no such process" from an exit
    code it cannot tell apart from "this `ps` cannot answer". The cost is the
    documented one: a dead rail on such a box stays `waiting`.
    """
    proc = subprocess.Popen(["/bin/sleep", "10"], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        monkeypatch.setattr(rd, "_proc_stat_letter", lambda pid: None)
        monkeypatch.setattr(rd, "_proc_cmdline", lambda pid: None)
        monkeypatch.setattr(rd, "_PS_USABLE", None)
        monkeypatch.setenv("PATH", "/nonexistent")   # `ps` cannot be run at all
        assert rd._pid_alive(proc.pid, "/state/rail-o-r-8.md") is True
    finally:
        # ⛔ NOT a bare `wait()`: the point is only that the pid is LIVE, and
        # waiting out the 10 s sleep cost a tenth of this gating file's runtime
        # (measured 10.03 s of ~38 s).
        proc.kill()
        proc.wait()


# ── `gh` that cannot RUN: a refusal, never a traceback whose exit code is the gate ──
def _gh_unrunnable(monkeypatch):
    """`gh` cannot be RUN: `subprocess.run(["gh", ...])` raises `PermissionError`.

    ⛔ NOT `FileNotFoundError`. `_gh` already converts a MISSING binary into
    rc 127 (that landed on main before this branch, and
    `test_launch_without_the_gh_cli_*` / `test_collect_without_the_gh_cli_*` pin
    the absent-binary class at PATH level) — so an injected `FileNotFoundError`
    never reaches the two `except (OSError, subprocess.SubprocessError)` clauses
    these arms are named after, which is why both arms passed on the pre-fix tree
    and could not fail for the reason their docstrings gave. `PermissionError` is
    an `OSError` `_gh` does NOT swallow, i.e. exactly the condition those clauses
    exist for. Filtered by argv so the local `ps` probe keeps working."""
    real_run = subprocess.run

    def run(argv, *a, **kw):
        if isinstance(argv, (list, tuple)) and argv and argv[0] == "gh":
            raise PermissionError(13, "Permission denied", "gh")
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(rd.subprocess, "run", run)


def test_an_unrunnable_gh_refuses_start_by_name_not_a_gate_code(tmp_path, monkeypatch, capsys):
    """⛔ EXIT 1 IS THIS TOOL'S MACHINE GATE (`collect --assert-merged`, "not
    merged"), so a launcher that could not even run `gh` must never report itself
    through it — and must not report a bare environment error either: the
    declared contract for `start` is `4 = refused`, naming what is wrong.
    Without the `_resolve_repo` clause the launch reports a generic environment
    error (2) with only `[Errno 13]` as the explanation."""
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    _gh_unrunnable(monkeypatch)
    rc = rd.main(["start", "73", "--cwd", str(co), "--state-dir", str(sd)])
    assert rc == 4, rc                       # pre-fix: 2, an unexplained environment error
    err = capsys.readouterr().err
    assert "could not run `gh`" in err, err  # pre-fix: just "[Errno 13] Permission denied"
    assert not rd.artifact_path(sd, "o/r", 73).exists()


def test_an_unrunnable_gh_makes_the_head_unreadable_not_a_crash(tmp_path, monkeypatch):
    """`_head_sha`'s declared contract is "the sha *or* `unreadable`", and `HEAD`
    gates nothing — so a `gh` that cannot RUN must not stop the rail starting.
    Without the `_head_sha` clause the `PermissionError` escapes `launch()` and
    the rail is never detached at all."""
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    _gh_unrunnable(monkeypatch)
    ln = launch(sd, co, pr=77)               # repo given ⇒ only `_head_sha` calls `gh`
    assert ln.head == "unreadable"
    assert wait_state(sd, 77, rd.MERGED).state == rd.MERGED


# ── the duplicate-rail guard is a LOCK, and only a race can tell ─────────────
RACER = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {tools!r})
    import rail_detach as rd
    try:
        ln = rd.launch({pr}, cwd={co!r}, state_dir={sd!r}, repo="o/r")
        print("STARTED", ln.pid, flush=True)
    except rd.Refused as e:
        print("REFUSED", str(e).replace("\\n", " "), flush=True)
    """
)


def test_two_processes_racing_start_produce_exactly_one_rail(tmp_path, monkeypatch):
    """⛔ THE SEQUENTIAL ARM ABOVE CANNOT SEE THE LOCK.

    `test_refuses_a_second_live_rail_on_one_pr` goes launch → collect → launch in
    ONE process, so a guard implemented as `collect()`-then-`Popen()` with **no
    lock at all** passes it unchanged. The lock's whole value is atomicity under
    concurrency, which that test never creates — this one does, with two REAL
    processes on one PR.

    The race is made deterministic in both directions instead of hoped for: the
    first racer is held INSIDE the lock by a slow `gh` (`_head_sha` is called
    there), and the second arrives while it is held. With the lock the second's
    `collect` runs after the first wrote `SPAWNING=`/`PID=` → `waiting` → refuse.
    Without it, both read "no rail", both spawn, and the artifact/log are written
    twice (#3061 — measured: the arm fails on a lockless guard, see the PR
    comment).
    """
    slow = fake_gh(tmp_path, "#!/usr/bin/env bash\nsleep 2\nexit 1\n", sub="slowbin3")
    monkeypatch.setenv("PATH", f"{slow}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_RAIL_SLEEP", "30")     # the winner's rail stays live
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "tools"))

    def racer():
        return subprocess.Popen(
            [sys.executable, "-c", RACER.format(
                tools=str(REPO_ROOT / "tools"), pr=76, co=str(co), sd=str(sd))],
            cwd=str(REPO_ROOT), env=env, stdout=subprocess.PIPE, text=True,
        )

    a = racer()
    time.sleep(0.4)      # A is inside the lock, held there by its slow `gh`
    b = racer()
    try:
        outs = [a.stdout.read(), b.stdout.read()]
        a.wait(timeout=40)
        b.wait(timeout=40)
        started = [o for o in outs if o.startswith("STARTED")]
        refused = [o for o in outs if o.startswith("REFUSED")]
        assert len(started) == 1 and len(refused) == 1, outs
        text = rd.artifact_path(sd, "o/r", 76).read_text(encoding="utf-8")
        assert len(rd.read_all(text, "PID")) == 1, text
        # ⛔ WAIT for `CHILD_PID=`: it is written by the RAIL, not by the
        # launcher, so it lands a few ms AFTER `launch()` returns. Asserting on
        # the immediate read raced that write — measured: `assert 0 == 1` on a
        # full-file run (artifact holding only `PID=`/`SID=`), green 4/4 in
        # isolation. An intermittent red on a CI-gating file for a diff that
        # changed nothing is exactly what the sibling arms above already avoid
        # with `wait_field`.
        assert wait_field(rd.artifact_path(sd, "o/r", 76), "CHILD_PID"), text
        assert Path(rd.read_field(text, "LOG")).read_text(encoding="utf-8").count(
            rd.ATTEMPT_MARK) == 1
        st = rd.collect(sd, "o/r", 76)
        assert st.state == rd.WAITING, st.as_dict()
    finally:
        for racer_proc in (a, b):
            if racer_proc.poll() is None:
                racer_proc.kill()
        st = rd.collect(sd, "o/r", 76)
        if st is not None and st.pid:
            kill_group(st.pid)


def test_the_duplicate_rail_guard_asks_a_liveness_question(tmp_path):
    """⛔ A MERGED RECORD COEXISTING WITH A LIVE ONE MUST NOT READ AS "FREE" (#10963).

    `collect` answers a VERDICT, and merged-first ranking (correct: a merge is
    monotone) means it returns the MERGED record when a merged one coexists with a
    live one. `launch` read that as `prior.state == WAITING` being false — so it
    truncated the LIVE record and started a second rail on one PR, the #3061
    collision the guard exists to refuse, and stripping the live attempt's
    `ATTEMPT_ID=` made its own announcements unreadable (#2235). Measured.

    Non-vacuity: revert `prior` to `collect(state_dir, repo, pr)` and this launch is
    ACCEPTED, so the `pytest.raises` fails.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    sd.mkdir(parents=True)
    live = subprocess.Popen(["/bin/sleep", "20"], start_new_session=True,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        # the MERGED record, under the legacy spelling so it is not the canonical path
        merged = sd / "rail-github.com-o-r-55.md"
        logp = merged.with_suffix(".log")
        merged.write_text(f"PR=55\nREPO=github.com/o/r\nSTARTED_AT={rd._iso_now()}\n"
                          f"ATTEMPT_ID=m1\nEXIT_ATTEMPT=m1\nEXIT=0\nLOG={logp}\n",
                          encoding="utf-8")
        logp.write_text(f"\n{rd.ATTEMPT_MARK} x attempt=m1 (detached) =====\n"
                        "admin-merge: ✅ merged PR #55 at deadbeefcafe1234 "
                        "(state=MERGED confirmed via the API)\n", encoding="utf-8")
        # …and a LIVE attempt for the same PR, under the canonical name
        canon = rd.artifact_path(sd, "o/r", 55)
        canon.write_text(f"PR=55\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\n"
                         f"PID={live.pid}\n", encoding="utf-8")

        assert rd.collect(sd, "o/r", 55).state == rd.MERGED, "the merged record wins the verdict"
        with pytest.raises(rd.Refused) as exc:
            rd.launch(55, cwd=co, state_dir=sd, repo="o/r")
        assert "ALREADY LIVE" in str(exc.value), str(exc.value)
        assert canon.read_text(encoding="utf-8").startswith("PR=55\nREPO=o/r"), \
            "the refused launch rewrote the live record"
    finally:
        live.kill()
        live.wait()


def test_the_shared_log_is_refused_even_without_the_artifact(tmp_path):
    """⛔ `rm <artifact>` IS THE DOCUMENTED RECOVERY, AND THE LOG SURVIVES IT.

    The shared-path refusal was gated on `art.exists()`, so after the recovery both
    refusal hints print the artifact is gone while the log is not, and the refusal
    did not fire: `a-b/c#9` truncated the surviving log and the still-live `a/b-c#9`
    rail appended its `✅ merged PR #9` into it — whose last evidence line then read
    as `a-b/c`'s own, so `--assert-merged` exited 0 for a repo that never merged
    (measured end-to-end). One-sided: a log with no evidence line still allows the
    launch, so this repo's own rm-and-relaunch flow is untouched.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    sd.mkdir(parents=True)
    shared = sd / "rail-a-b-c-9.log"
    shared.write_text("admin-merge: repo a/b-c (resolved from --repo)\n",
                      encoding="utf-8")
    with pytest.raises(rd.Refused) as exc:
        rd.launch(9, cwd=co, state_dir=sd, repo="a-b/c")
    assert "ANOTHER repo's rail" in str(exc.value), str(exc.value)
    assert "a/b-c" in shared.read_text(encoding="utf-8"), \
        "the refused launch truncated another repo's log"

    # …and the SAME repo's own log does NOT block its relaunch (the rule is
    # one-sided: a log its own rail wrote is not evidence against it).
    shared.write_text("admin-merge: repo a-b/c (resolved from --repo)\n",
                      encoding="utf-8")
    ln = rd.launch(9, cwd=co, state_dir=sd, repo="a-b/c")
    try:
        assert ln.pid > 0
    finally:
        kill_group(ln.pid)


def test_a_record_that_ALREADY_EXITED_is_not_live_whatever_its_pid_answers(
        tmp_path, monkeypatch):
    """`_record_is_live` must honour `EXIT=` before it consults `PID=`/`CHILD_PID=`.

    ⛔ Deleting `if _attempt_exits(text): return False` left this whole suite green
    (measured at `06ba8c8c5^`: arm present → 98 passed, arm DELETED → 98 passed), so
    the arm had no
    regression detector. The consequence is the ordering it exists for: during the
    window where a finished attempt's carrier is still exiting, its pid (possibly
    recycled) still answers alive, so a FINISHED attempt would outrank the
    genuinely-live record at `_artifact_for_any_spelling`'s liveness pass — and
    `collect` would publish that attempt's `refused`/`lost`. `lost` is exactly the
    state `launch`'s duplicate-rail guard ACCEPTS, i.e. the #3061 direction (a second
    rail on one PR).

    ⛔ AND THE PIN ITSELF WAS VACUOUS UNTIL THE PROBE WAS FORCED ALIVE. Its first
    form wrote `PID=<pytest's own pid>`, whose argv does not carry the artifact path,
    so `_rail_alive` answered "not ours" at `_pid_alive`'s marker test and the
    assertion held for a reason with nothing to do with `EXIT=`: arm present → this
    test passed, arm DELETED → this test passed (measured at `34e57cb58`). An
    assertion satisfied by an unrelated branch is not a detector, however green it
    is. `_rail_alive` is therefore monkeypatched to answer ALIVE, which is the one
    condition under which `EXIT=`'s precedence is observable at all: arm present →
    passed, arm deleted → FAILED (measured, same fixture).

    The same-record-differing-only-in-`EXIT=` assertion is the vacuity guard for the
    patch itself: if the monkeypatch were not in effect (wrong symbol, silently
    unapplied), the unterminated record would read NOT live and fail here instead of
    letting the pin pass for the old wrong reason.

    The control half matters as much as the pinned half: the same record WITHOUT the
    `EXIT=` line must still read LIVE under the REAL probe, so this cannot be
    satisfied by a blanket `False` (which would make every rail look dead and re-open
    the duplicate launch from the other side).
    """
    head = f"PR=77\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nPID={os.getpid()}\n"
    exited = tmp_path / "rail-o-r-77.md"
    exited.write_text(head + "EXIT=0\n", encoding="utf-8")
    # Byte-identical to `exited` minus the `EXIT=` line: under ONE probe, the two
    # answers can only be produced by `EXIT=`.
    unterminated = tmp_path / "rail-o-r-77-live.md"
    unterminated.write_text(head, encoding="utf-8")

    probes: list[str] = []

    def forced_alive(*_a, **_k):
        probes.append("probe")
        return True

    with monkeypatch.context() as m:
        m.setattr(rd, "_rail_alive", forced_alive)
        assert rd._record_is_live(unterminated), (
            "the forced-alive probe must be IN EFFECT — with no EXIT= this record "
            "reads live, or the pin below is measuring an unrelated branch again"
        )
        assert not rd._record_is_live(exited), (
            "a record whose writer already wrote EXIT= is FINISHED — a recycled pid "
            "must not resurrect it, even when the liveness probe answers alive"
        )
    assert probes, "the pinned half never consulted the liveness probe"

    alive = subprocess.Popen(["/bin/sleep", "20"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        live = tmp_path / "rail-o-r-78.md"
        live.write_text(
            f"PR=78\nREPO=o/r\nSTARTED_AT={rd._iso_now()}\nPID={alive.pid}\n",
            encoding="utf-8",
        )
        assert rd._record_is_live(live), (
            "control: the same record with no EXIT= must read live, or the pin above "
            "is satisfiable by a blanket False"
        )
    finally:
        alive.kill()
        alive.wait()
