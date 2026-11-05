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


#: How long a rail STUB may take to reach the state under test.
#:
#: This bounds SPAWN LATENCY (fork + exec + `bash` startup on a shared pool
#: box), never a rail invariant: every assertion below is about the STATE the
#: artifact records, so a wider deadline cannot make a defect green — only a
#: loaded box is prevented from making correct code red. 15 s was measured on
#: an idle host against a stub that sleeps 1 s; on a nine-shard sweep the same
#: wait has missed. Evidence: `test (e)` on run 42006117288 (2026-11-05, head
#: `192de3b5f`, PR #10610) reported
#: `test_merged_state_names_the_sha_the_rail_printed` FAILED on
#: `pi-runner-x64-4` while the run's other seven shards were green, and that
#: shard carries NO changed test file from the diff — the leg was killed by its
#: 21-minute WATCHDOG at 73% progress, i.e. the box, not the rail.
#:
#: The detached worker's bounds elsewhere in this file (`lane.wait(timeout=10)`,
#: `_assert_no_worker_spawned`'s settle window) are untouched: those are
#: process-exit and negative windows, not state-arrival bounds.
_STATE_DEADLINE_S = 60.0


def wait_state(state_dir, pr, want, timeout=_STATE_DEADLINE_S):
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
    assert wait_state(sd, 7, rd.MERGED, timeout=_STATE_DEADLINE_S).state == rd.MERGED


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


# ── a box with NO `gh` on PATH ──────────────────────────────────────────────
#
# `subprocess.run(["gh", …])` raises `FileNotFoundError` when the BINARY is
# absent, and that killed every case in this module on the fleet's self-hosted
# pool (measured 2026-11-03: `test (h)` on pi-runner-vm-6/Linux-ARM64, run
# 41732583553 — 20 of 20 here, `FileNotFoundError: [Errno 2] … 'gh'`). The two
# callers already have a documented degradation for "this checkout cannot tell
# me" (`Refused` for the repo, `unreadable` for the head) — a missing binary is
# that case, not a different one — so these pin both arms on a PATH that has the
# shell tools the child needs and no `gh` at all.
#
# ⚠️ `main` landed the SAME fix independently at `f422799c` (`_gh` returns
# `CompletedProcess(argv, 127, "", "gh: command not found")`, never `None`) and
# its own CLI-level pair for the same defect — see `_path_without_gh` below and
# `test_launch_without_the_gh_cli_starts_and_stamps_an_unreadable_head` /
# `test_collect_without_the_gh_cli_refuses_by_name_instead_of_tracebacking`. The
# two arms here are therefore kept as the UNIT-level pair the merge kept from
# the other side (`_resolve_repo`'s refusal text, and `Launch.head`); the CLI
# pair below is the end-to-end one. Five tests, one root, three layers — recorded
# rather than deduplicated, because each layer asserts something the other does
# not (the `_gh` return contract is `None`-never with rc=127; the "installed"
# remedy in the refusal text; `HEAD=unreadable` stamped in the artifact).
#
# ⛔ The CONTRACT layer is not redundant with either: `test_gh_never_returns_None_for_a_missing_binary`
# is the only arm that reds when `_gh` is changed to return `None` — the state in
# which BOTH callers crash at `out.returncode`. It is what makes dropping the two
# `out is not None` guards the merge kept safe (#10622 round 9), instead of tidy.

def no_gh_on_path(tmp_path, monkeypatch):
    """A PATH with the child's shell tools but NOT `gh`."""
    d = tmp_path / "bin-nogh"
    d.mkdir()
    for name in ("bash", "date", "env", "sh", "sleep"):
        real = shutil.which(name)
        assert real, f"{name} is missing from this test box"
        (d / name).symlink_to(real)
    monkeypatch.setenv("PATH", str(d))
    return d


def test_gh_never_returns_None_for_a_missing_binary(tmp_path, monkeypatch):
    """`_gh`'s annotation is a CONTRACT the callers may rely on, not a hint.

    `-> subprocess.CompletedProcess` means the missing-binary case is ANSWERED
    (`rc=127`), never signalled by `None` — so a caller needs no
    `out is not None` guard, and one that carries a guard is describing a
    return type this function does not have. Two such guards arrived on the
    branch side of the #10622 merge (where `_gh` *could* return `None`) and sat
    unreachable under main's `_gh` until a fresh-context review read the
    annotation against them.

    This arm is the invariant that keeps that deletion safe: a `None`-returning
    `_gh` reds the assertion below, before a guard can rot into a second
    description of the function. NON-VACUOUS — proved twice, because the two
    senses differ and only one of them is a test against history:

    * **Against a mutant of THIS tree** (`return None` in the `except` arm):
      `1 failed` at the `out is not None` assertion, and both callers would
      then crash at `out.returncode` — the guards were not protecting them.
    * **Against the true pre-fix head** (`cbefdcbe^`, before #2148): `_gh` does
      not exist there at all — the absent binary was an UNGUARDED
      `subprocess.run(["gh", ...])` inside `_resolve_repo`/`_head_sha` — so
      this arm reds with `AttributeError`, not with a behaviour claim. Said
      plainly: the "before" side of this test is the function's absence, and
      the invariant it pins is the one #2148 introduced.
    """
    co = make_checkout(tmp_path)
    no_gh_on_path(tmp_path, monkeypatch)
    out = rd._gh(["gh", "repo", "view"], co)
    assert out is not None, "`_gh` returned None; its annotation says it cannot"
    assert out.returncode == 127, out
    assert "command not found" in (out.stderr or ""), out.stderr


def test_a_missing_gh_is_a_REFUSAL_not_a_traceback(tmp_path, monkeypatch):
    """`gh` absent is a precondition the launcher enforces, never a crash."""
    co = make_checkout(tmp_path)
    no_gh_on_path(tmp_path, monkeypatch)
    with pytest.raises(rd.Refused) as exc:
        rd._resolve_repo(co)
    assert "gh repo view" in str(exc.value)
    assert "installed" in str(exc.value), (
        "the refusal does not say WHAT to fix — a lane reading it has to guess "
        f"whether the checkout or the tool is wrong: {exc.value}")


def test_with_no_gh_the_rail_still_starts_and_the_head_reads_unreadable(tmp_path, monkeypatch):
    """The rail must outlive the lane on a box that has no `gh` too.

    A caller that passes `repo=` has already settled the one thing `gh` was
    needed for, and the head is recorded as `unreadable` — the same value
    `_head_sha` returns when `gh` runs and fails. Neither may become a crash.
    """
    co = make_checkout(tmp_path)
    sd = tmp_path / "state"
    no_gh_on_path(tmp_path, monkeypatch)
    ln = launch(sd, co, pr=21)
    assert ln.pid > 0, "the rail did not start without `gh`"
    assert ln.head == "unreadable", ln.head
    assert wait_state(sd, 21, rd.MERGED, timeout=_STATE_DEADLINE_S).state == rd.MERGED


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

    st = wait_state(sd, pr, expect, timeout=_STATE_DEADLINE_S)
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
    wait_state(sd, 42, rd.MERGED, timeout=_STATE_DEADLINE_S)
    assert rd.main(["collect", "42", "--repo", "o/r", "--state-dir", str(sd)]) == 0
    # --assert-merged is the next fleet beat's gate: 0 only on a real merge
    assert rd.main(["collect", "42", "--repo", "o/r", "--state-dir", str(sd),
                    "--assert-merged"]) == 0
    capsys.readouterr()
