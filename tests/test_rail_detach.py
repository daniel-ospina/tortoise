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
import errno
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
    assert "not on PATH" in (out.cause or ""), out.cause


def test_a_missing_gh_is_a_REFUSAL_that_says_what_to_fix(tmp_path, monkeypatch):
    """`gh` absent is a precondition the launcher enforces, never a crash.

    The refusal has to carry all three facts a lane needs to act: the COMMAND
    that failed (`gh repo view`), the CAUSE (`not on PATH` — not "auth expired",
    not "no remote"), and the REMEDY (`install`, or `--repo` to name the target
    itself). Any one of them missing sends the operator guessing.
    """
    co = make_checkout(tmp_path)
    monkeypatch.setenv("PATH", _path_without_gh(tmp_path))
    with pytest.raises(rd.Refused) as exc:
        rd._resolve_repo(co)
    msg = str(exc.value)
    assert "gh repo view" in msg, msg
    assert "not on PATH" in msg, msg
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
    assert wait_state(sd, 21, rd.MERGED, timeout=20).state == rd.MERGED


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
    assert "chmod" in rd._oserror_remedy(PermissionError(errno.EACCES, "denied"))
    assert "chmod" in rd._oserror_remedy(PermissionError(errno.EPERM, "denied"))
    eagain = rd._oserror_remedy(OSError(errno.EAGAIN, "try again"))
    assert "loaded" in eagain, eagain
    assert "chmod" not in eagain, eagain
    assert "loaded" in rd._oserror_remedy(OSError(errno.ENOMEM, "out of memory"))
    assert "not a directory" in rd._oserror_remedy(OSError(errno.ENOTDIR, "nope"))
    generic = rd._oserror_remedy(OSError(errno.E2BIG, "too big"))
    assert "check the working directory" in generic, generic

    # ⛔ THE ERRNO IS NOT THE CAUSE ON ITS OWN (review, `#10777`): an `EACCES` on
    # the WORKING DIRECTORY must not be answered with `chmod +x` on a `gh` that is
    # fine. `filename` carries which object failed, and it must decide first.
    cwd_denied = rd._oserror_remedy(PermissionError(errno.EACCES, "denied", str(tmp_path)))
    assert "chmod" not in cwd_denied, cwd_denied
    assert "working directory" in cwd_denied, cwd_denied


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
    assert out.returncode == 127, (out.returncode, out.cause)
    assert out.cause == "", (
        "a real process's exit code must not be given a fabricated cause: "
        f"{out.cause!r}")

    with pytest.raises(rd.Refused) as exc:
        rd._resolve_repo(tmp_path)
    msg = str(exc.value)
    assert "not on PATH" not in msg, msg
    assert "install the GitHub CLI" not in msg, msg
    assert "ran and refused" in msg, msg
    assert "unresolvable gh" in msg, msg


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
    reaches the discrimination — so mutating `filename == "gh"` to `if True:`
    left the whole suite GREEN. This arm reddens on that mutant.
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

    ⚠️ With a WORKING `gh` later on `PATH`, glibc's `execvp` silently skips this
    candidate and runs the later one — so this arm must own `PATH` completely or it
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
    assert out.returncode == 126, (out.returncode, out.cause)
    assert "is not on PATH" not in out.cause, out.cause
    assert str(gh) in out.cause, out.cause

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

      bare `mkdir()`                         -> 1 failed  (FileExistsError, :1313)
      `mkdir(exist_ok=True)`, no `is_symlink` -> 1 failed  (FileExistsError, :1388)
      both in place                           -> 1 passed
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
