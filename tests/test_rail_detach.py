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
says, and the launcher never resolves a repo it was given — except in the
`PATH`-without-`gh` pins, which exercise the repo-less default path deliberately.
"""
from __future__ import annotations

import contextlib
import json
import os
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


def launch(state_dir, co, pr=7, **kw):
    return rd.launch(pr, cwd=co, state_dir=state_dir, repo="o/r", **kw)


def wait_state(state_dir, pr, want, timeout=15.0):
    end = time.time() + timeout
    st = None
    while time.time() < end:
        st = rd.collect(state_dir, "o/r", pr)
        if st is not None and st.state == want:
            return st
        time.sleep(0.05)
    return st


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
    assert wait_state(sd, 7, rd.MERGED, timeout=20).state == rd.MERGED


def test_merged_state_names_the_sha_the_rail_printed(tmp_path):
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    launch(sd, co, pr=11)
    st = wait_state(sd, 11, rd.MERGED)
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
    """No EXIT line is NOT a verdict. Reporting `merged` here would be #2235."""
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    os.environ["FAKE_RAIL_SLEEP"] = "30"
    try:
        ln = launch(sd, co, pr=13)
    finally:
        os.environ.pop("FAKE_RAIL_SLEEP", None)
    time.sleep(0.2)
    os.kill(ln.pid, signal.SIGKILL)
    time.sleep(0.3)
    st = rd.collect(sd, "o/r", 13)
    assert st.state == rd.LOST and st.exit_code is None
    assert "no EXIT line" in st.reason


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


def test_previous_attempts_merge_line_is_not_inherited(tmp_path):
    """The log is append-only across attempts: a NEW attempt that dies with no EXIT
    must not inherit the OLD attempt's `✅ merged` — that is forged evidence."""
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    rd.launch(61, cwd=co, state_dir=sd, repo="o/r")
    assert wait_state(sd, 61, rd.MERGED).state == rd.MERGED      # attempt 1: merges
    os.environ["FAKE_RAIL_SLEEP"] = "30"
    try:
        ln = rd.launch(61, cwd=co, state_dir=sd, repo="o/r")    # attempt 2: hangs…
    finally:
        os.environ.pop("FAKE_RAIL_SLEEP", None)
    time.sleep(0.2)
    os.kill(ln.pid, signal.SIGKILL)                              # …and is killed
    time.sleep(0.3)
    st = rd.collect(sd, "o/r", 61)
    assert st.state == rd.LOST, st.as_dict()
    assert st.merge_sha == ""


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
    """admin-merge.sh:9033 prints the same success with NO sha when the merge
    commit was unreadable. A crash after that line is still a merge."""
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
    os.kill(ln.pid, signal.SIGKILL)
    time.sleep(0.3)
    art = rd.artifact_path(sd, "o/r", 66)
    art.write_text(art.read_text() + "EXIT=\n")      # the torn append
    st = rd.collect(sd, "o/r", 66)
    assert st.state == rd.REFUSED and st.exit_code is None   # fail closed, no crash


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


def test_collect_has_no_side_effects(tmp_path):
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    launch(sd, co, pr=14)
    wait_state(sd, 14, rd.MERGED)
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


def test_child_invokes_only_the_one_lander(tmp_path):
    """#1178: no second lander. The child argv is `bash <rail> <pr> --repo <r>`."""
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
def _path_without_gh(tmp_path: Path) -> str:
    """A PATH that resolves what the rail needs but has NO `gh` on it.

    #9018: `runs-on: ubuntu-latest` names a GitHub-hosted IMAGE, but the
    fallback pool carries that same label on a bare runner whose service PATH is
    a minimal non-login one — where `gh` is simply absent (measured 2026-11-03:
    `test (a)` on `pi-runner-vm-2`, `FileNotFoundError: 'gh'`, 20 arms red on a
    PR that touches neither this tool nor this file). Absence is SIMULATED here
    rather than inherited, so the arm means the same thing on every runner.
    """
    b = tmp_path / "bin-no-gh"
    b.mkdir(exist_ok=True)
    for tool in ("bash", "sleep"):
        src = shutil.which(tool)
        assert src, f"{tool} must exist for this arm to mean anything"
        (b / tool).symlink_to(src)
    return str(b)


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
    """No `--repo` and no artifact ⇒ refuse (rc 2), naming what is missing.

    Pre-fix this arm exited 1 with `FileNotFoundError` — a read-only status verb
    tracebacking, which is worse than any verdict (the fleet beat then gets no
    state to act on). The artifact path never needs `gh` (the arm above).
    """
    proc = _cli(tmp_path, "collect", "778", "--state-dir", str(tmp_path / "state"))
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

    st = wait_state(sd, pr, expect, timeout=25)
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
    wait_state(sd, 42, rd.MERGED, timeout=20)
    assert rd.main(["collect", "42", "--repo", "o/r", "--state-dir", str(sd)]) == 0
    # --assert-merged is the next fleet beat's gate: 0 only on a real merge
    assert rd.main(["collect", "42", "--repo", "o/r", "--state-dir", str(sd),
                    "--assert-merged"]) == 0
    capsys.readouterr()


def _path_without_gh_shim(tmp_path: Path, monkeypatch) -> Path:
    """Rebuild `PATH` from this box's own executables with every `gh` dropped.

    ⛔ The pin cannot pass by finding a real `gh`: it fails on the pre-fix code on
    ANY box, including one that has `gh` installed.

    ⛔ DELIBERATELY A SECOND HELPER, NOT A REPLACEMENT FOR `_path_without_gh` ABOVE
    (merge of `origin/main`, #10620). The two shims answer different questions and
    both callers are load-bearing: `_path_without_gh` builds a MINIMAL PATH holding
    only `bash` and `sleep` for `_cli`'s subprocess environment, while this one
    mirrors the whole `PATH` minus `gh` for the in-process `monkeypatch` pins, and
    it also clears `GH_REPO`. Merging them into one name meant the later definition
    shadowed the earlier, so `_cli` called a two-argument helper with one argument
    (`TypeError: missing 1 required positional argument: 'monkeypatch'`) — a silent
    auto-merge break of work from BOTH sides, caught by running the suite.
    """
    shim = tmp_path / "nogh"
    shim.mkdir(exist_ok=True)
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d or not Path(d).is_dir():
            continue
        for exe in Path(d).iterdir():
            if exe.name.startswith("gh"):
                continue
            link = shim / exe.name
            if link.exists() or not os.access(exe, os.X_OK) or exe.is_dir():
                continue
            with contextlib.suppress(OSError):
                link.symlink_to(exe)
    monkeypatch.setenv("PATH", str(shim))
    # An inherited GH_REPO would otherwise be the only repo source left on the
    # default path, and this pin is about the `gh` call, not that variable.
    monkeypatch.delenv("GH_REPO", raising=False)
    assert not any(p.name.startswith("gh") for p in shim.iterdir())
    return shim


def test_a_missing_gh_degrades_the_head_annotation_instead_of_killing_the_launch(
        tmp_path, monkeypatch):
    """⛔ AN ABSENT `gh` IS A SOFT MISS, NOT A CRASH (#10620 required check `test (a)`).

    `_head_sha` is a best-effort annotation on the artifact — the rail's verdict is
    parsed from the rail's own log, never from `HEAD=`. Before this pin, a `gh` that
    was not on PATH raised `FileNotFoundError` out of `launch()` BEFORE the spawn, so
    every `collect` reported `lost`. Measured on a self-hosted runner with no `gh`:
    21 of this module's tests failed with `FileNotFoundError: ... 'gh'`.
    """
    _path_without_gh_shim(tmp_path, monkeypatch)

    assert rd._head_sha(tmp_path, "o/r", 99) == "unreadable"

    # ...and the launch still happens: the annotation miss is recorded, not raised.
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    ln = launch(sd, co, pr=99)
    assert ln.pid > 0
    assert "HEAD=unreadable" in Path(ln.artifact).read_text()
    assert wait_state(sd, 99, rd.MERGED, timeout=20).state == rd.MERGED


def test_a_missing_gh_refuses_the_default_repo_path_instead_of_crashing(
        tmp_path, monkeypatch, capsys):
    """⛔ THE SAME BOUNDARY, THE OTHER CALL SITE (#10620 finding 2).

    `_resolve_repo` shells out to the SAME binary as `_head_sha`, and `launch()`
    calls it whenever `--repo` is not supplied — the CLI default. A handler on
    `_head_sha` alone therefore does not remove the crash, it moves it: measured
    against the pre-fix code with a `gh`-free `PATH`,

        _head_sha('/tmp', 'o/r', 1)   -> 'unreadable'                        (fixed)
        _resolve_repo(Path('/tmp'))   -> RAISED FileNotFoundError … 'gh'     (not fixed)

    The repo is not optional the way the annotation is — it names the artifact,
    the log and the rail's own `--repo` — so the only safe answer is the
    launcher's own `Refused`, which `start` already turns into exit 4 with
    `✗ REFUSED — …` and `collect` into exit 2. Tested through BOTH surfaces,
    because an exception class nobody catches is still a crash to the operator.

    Non-vacuity: on the pre-fix code the `_resolve_repo` line below raises
    `FileNotFoundError` and the `main()` line raises out of `start` instead of
    returning 4 — the test fails on ANY box, `gh` installed or not.

    ⛔ PROVENANCE, because it changes what this pin PROVES (#10620 merge of
    `origin/main`): `main` landed the same absent-`gh` fix independently in
    `#10724` (`_gh(argv, cwd)` -> rc 127, plus two `PATH`-without-`gh` arms),
    under the same measured runner evidence. This arm and its sibling above
    therefore PASS on `origin/main`'s `tools/rail_detach.py` too (measured: 4
    passed, both sides' arms, against main's blob). They are not the fix's
    falsification any more — they are the extra assertions main's arms do not
    make: `rd.Refused` at the unit contract, `collect` staying silent, and a
    refused launch spawning nothing.
    """
    _path_without_gh_shim(tmp_path, monkeypatch)
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"

    with pytest.raises(rd.Refused) as exc:
        rd._resolve_repo(co)
    assert "gh" in str(exc.value) and "--repo" in str(exc.value)

    # The default path end to end: no `--repo`, so `launch()` must resolve one.
    # A traceback here is the bug; exit 4 + a named refusal is the contract.
    rc = rd.main(["start", "98", "--cwd", str(co), "--state-dir", str(sd)])
    assert rc == 4
    assert "REFUSED" in capsys.readouterr().err
    assert not rd.collect(sd, "o/r", 98), "a refused launch must not spawn a rail"

    # `collect` goes through the same helper (its disk-first lookup misses here).
    rc = rd.main(["collect", "98", "--state-dir", str(sd)])
    assert rc == 2
    assert "REFUSED" not in capsys.readouterr().err


# ── the SAME boundary when `gh` exists and cannot be EXECUTED ────────────────
def _exec_fails_with_permission_denied(monkeypatch) -> list[list[str]]:
    """Make every `gh` exec fail the way a non-executable `gh` fails.

    ⛔ `PermissionError`, NOT `FileNotFoundError`. A `gh` that IS on `PATH` but
    carries no `+x` is found and still cannot be started, so `subprocess.run`
    raises `PermissionError` at exec — the same operator-visible crash from a
    cause the absent-binary arms cannot reach.

    Simulated by patching the exec rather than `chmod`-ing a fake `gh` on a temp
    `PATH`, deliberately: a mode-bit pin is VACUOUS for root (root ignores `+x`),
    so it would prove the arm on a laptop and prove nothing on a root container —
    the same suite, two different meanings (#9018's own hazard). The returned
    list records every exec attempted, so a test cannot pass by never shelling out.
    """
    calls: list[list[str]] = []

    def fake(argv, *a, **kw):
        calls.append(list(argv))
        assert argv[0] == "gh", f"this shim only stands in for `gh`, saw {argv[0]!r}"
        raise PermissionError(13, "Permission denied", argv[0])

    monkeypatch.setattr(rd.subprocess, "run", fake)
    return calls


def test_an_unexecutable_gh_is_a_soft_miss_on_the_head_annotation(monkeypatch, tmp_path):
    """`_head_sha` answers `unreadable`, it does not raise out of `launch()`.

    Pre-fix (`_gh` catching `FileNotFoundError` only, the shape `#10724` landed)
    a `PermissionError` propagated out of `launch()` before the spawn — the
    artifact is never stamped, so every `collect` reports `lost`. Measured on the
    pre-fix blob: this arm raises `PermissionError: [Errno 13] Permission denied:
    'gh'`.
    """
    calls = _exec_fails_with_permission_denied(monkeypatch)

    assert rd._head_sha(tmp_path, "o/r", 99) == "unreadable"
    assert calls and calls[0][0] == "gh", "the arm must actually attempt the exec"


def test_an_unexecutable_gh_refuses_the_repo_path_instead_of_tracebacking(
        monkeypatch, tmp_path, capsys):
    """The repo path REUSES the same boundary: `Refused`, never a traceback.

    A handler on `FileNotFoundError` alone does not remove the crash, it moves it
    to the rarer trigger — and the repo half is the half that NAMES the artifact,
    so degrading it is not an option either. Both `start` (exit 4) and `collect`
    (exit 2) must still reach their existing `Refused` sinks.
    """
    calls = _exec_fails_with_permission_denied(monkeypatch)
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"

    with pytest.raises(rd.Refused) as exc:
        rd._resolve_repo(co)
    assert "could not resolve the repo" in str(exc.value)
    assert "Permission denied" in str(exc.value), str(exc.value)
    assert calls

    rc = rd.main(["start", "97", "--cwd", str(co), "--state-dir", str(sd)])
    assert rc == 4
    assert "REFUSED" in capsys.readouterr().err
    assert not rd.collect(sd, "o/r", 97), "a refused launch must not spawn a rail"
