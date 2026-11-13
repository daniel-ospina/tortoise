"""#10283 — a preview job's teardown must not reach a PEER job's preview.

O/I/T of the issue this file pins: *"two `legal-e2e`-style jobs sharing one
self-hosted machine fight over fixed loopback ports (127.0.0.1:8788/:8790) and
fixed `/tmp/wrangler*.pid|log` paths, and each job's teardown runs machine-global
`pkill workerd` — so one job's cleanup kills a PEER job's previews mid-run and
reds a required check on a PR whose diff has nothing to do with it."*

The measured incident (PR #10179, 2026-10-30): `legal-e2e` job 123953321176 on
`pi-runner-cmux-x64-8` reported both previews ready at 08:12:03Z, ran 50 tests
green, and its LAST spec failed at 08:12:29Z with
`net::ERR_CONNECTION_REFUSED` on `127.0.0.1:8790`. A PEER `legal-e2e`
(job 123951276145, `pi-runner-cmux-x64-7` — the same `Machine name: 'cmux'`)
logged its teardown `pkill workerd` at 08:12:25.8Z. The servers did not answer
wrongly; they were gone. Three unrelated PRs carried the same signature inside
four minutes.

Two halves are pinned here, and they are DIFFERENT kinds of assertion:

1. **The workflow shape** (text + YAML arms). A preview job may not carry a
   host-global reap (`pkill workerd`, `pkill -f 'wrangler…'`) or a shared
   literal port / `/tmp/wrangler*` path, and every boot must go through
   `.github/scripts/preview-server.sh`. These arms are RED on the pre-fix
   `ci.yml` — they are the non-vacuity proof, not a restatement of the fix.
2. **The mechanism** (process arms, executed). The helper's `stop` really does
   signal exactly the recorded process and its descendants, and really does
   leave a peer's decoy `workerd` alone. Measured, not asserted from prose.

⚠️ What `ready` does NOT prove: that the port's LISTENER belongs to our pid
tree. Proving that needs a socket→pid mapping (`ss`/`lsof`) that the runner
image does not guarantee, so `ready` asserts (a) the pid we recorded is still
alive **and still OURS** — the start-time witness `record` wrote, re-read, and
refused when it cannot be re-read — (b) the port is bound, and (c) that listener
answers HTTP successfully.
(c) is the pre-fix `curl -fsS` requirement, kept: a bound listener is not a
serving server, and the specs behind this gate require 200 —
`test_ready_requires_the_preview_to_SERVE_not_just_bind` is the arm that pins it.
The remaining window needs our process to survive a failed BIND while a peer
listens on the same port; the allocator's pre-boot free-check plus wrangler's
exit on `EADDRINUSE` are what close it in practice.

Those four sentences are pinned below
(`test_readiness_comments_do_not_claim_a_listener_pid_mapping`): the OVERCLAIM
lived in the helper's contract block and its `cmd_ready`, and in the two `ci.yml`
boot steps — the two files a CI author actually opens — while the paragraph above
was the only place that said it correctly.
"""
from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
SCRIPT = REPO_ROOT / ".github" / "scripts" / "preview-server.sh"

# The three jobs that boot a local `wrangler pages dev` preview.
PREVIEW_JOBS = ("welcome-e2e", "legal-e2e", "dashboard-e2e")
BOOT_STEP_PREFIX = "Start wrangler pages dev preview"


# ── helpers ─────────────────────────────────────────────────────────────────

def _workflow_paths() -> list[Path]:
    paths = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert paths, f"no workflow files found under {WORKFLOWS}"
    return paths


def _cut_comment(line: str) -> str:
    """Drop a YAML comment that starts a token (` # …` or a line-leading `#`).

    Comment-aware on purpose, the same convention `test_e2e_wrangler_prewarm.py`
    and `test_reap_redislite_orphans.py` use: this file's own explanatory prose
    NAMES the old `pkill workerd` teardown in `ci.yml` comments, and naming a
    removed hazard is not committing it.
    """
    return re.sub(r"(?:^|\s)#.*$", "", line)


def _load_workflow(name: str) -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _script(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _pid_alive(pid: int) -> bool:
    """RUNNING — not merely present. A zombie is a reaped-to-be child, i.e. dead.

    `/proc` first (Linux, every CI leg); `ps` when the host has no `/proc` (a
    developer's macOS run). The verdict is the same question either way, and a
    host that cannot answer it must not be reported as "the process is gone".
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        state = subprocess.run(
            ["ps", "-o", "state=", "-p", str(pid)],
            capture_output=True, text=True,
        ).stdout.strip()
        # "" = no such pid; "Z"/"Z+" = a zombie.
        return bool(state) and not state.startswith("Z")
    except OSError:  # pragma: no cover — /proc unreadable, not ours
        return True
    # state is field 3, right after the `(comm)` field, which may contain spaces.
    rest = stat[stat.rfind(") ") + 2:]
    return rest.split(" ", 1)[0] != "Z"


def _child_pids(pid: int) -> list[int]:
    out = subprocess.run(["ps", "-eo", "pid=,ppid="], capture_output=True, text=True, check=True).stdout
    kids = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == str(pid):
            kids.append(int(parts[0]))
    return kids


def _spawn(cmd: list[str], cwd: Path, executable: str | None = None) -> subprocess.Popen:
    """A detached process, like `wrangler pages dev` under a runner step.

    `executable=` decouples WHAT RUNS from `argv[0]`: the census/`pkill` name and the
    program are then two different things, which is the only portable way to put an
    arbitrary name in front of a live process (#12039).
    """
    return subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True,
                            executable=executable)


def _wait_alive(pid: int, timeout: float = 5.0) -> None:
    """Wait for the pid to be RUNNING — ⚠️ in the sense `_pid_alive` means, which is NOT
    "the decoy is up".

    ⛔ `Popen` DOES NOT RETURN BEFORE `execve` on CPython/Linux (the parent blocks on the
    CLOEXEC errpipe), so this never samples a pre-exec fork of THIS process. What it does
    sample is the window right after exec: reading `/proc/<pid>/cmdline` the instant
    `Popen` returned NEVER carried the parent's argv, but it CAN succeed and be EMPTY
    while the child is state `R`, the same file reading our `argv[0]` microseconds later
    (measured over 40-spawn samples: OUR argv 13-30, EMPTY 10-27 depending on spawn mode
    and load — load-dependent, so not a constant to quote). Against `executable=/bin/false`
    (execs and exits at once) every wait passed, 6/6, because the process is still live
    when the wait looks. ⚠️ `posix_spawn` paths on other platforms may return earlier, so
    this is a statement about this box's CPython, not a guarantee everywhere.

    Use `_await_exec` when the question is "does a row NAME what we spawned", which is
    what a census matcher and a human reading a failure both need.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _pid_alive(pid):
            return
        time.sleep(0.05)
    pytest.fail(f"decoy pid {pid} never came alive — the harness cannot measure")


def _await_exec(pid: int, argv0: str, timeout: float = 5.0) -> None:
    """Wait until a process-table row NAMES the command we spawned (#12039).

    `_wait_alive` cannot answer that: it reads `/proc/<pid>/stat` directly and returns in
    the milliseconds a doomed decoy is still alive (measured 6/6 against a decoy that
    execs and exits immediately), so it reports a decoy as up that `record` will refuse
    moments later. A `ps` row costs ~40 ms, which is longer than that window — so this
    fails HERE, naming the row it last saw, instead of surfacing later as `record`
    refusing a pid the harness had "just confirmed alive".
    """
    deadline = time.time() + timeout
    seen = ""
    while time.time() < deadline:
        seen = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True,
        ).stdout.strip()
        if seen.startswith(argv0):
            return
        if not _pid_alive(pid):
            break
        time.sleep(0.05)
    why = (f"the last row read for it named {seen!r}, not {argv0!r}" if seen
           else "the table had no row for it")
    pytest.fail(
        f"decoy pid {pid} never showed a row starting {argv0!r} — the harness cannot "
        f"measure ({why})"
    )


def _kill0(pid: int) -> str:
    """`kill -0 pid` from THIS process — the leg `record` grades with (#12039).

    Read only into a FAILURE message, never into an assertion: it is the other
    predicate's answer, printed so that a disagreement between the two is legible
    instead of being collapsed into "not running".
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "ESRCH (no such process)"
    except PermissionError:
        return "EPERM (exists, not signalable by this user)"
    except OSError as exc:  # pragma: no cover — exhaustive on Linux/macOS
        return f"{exc}"
    return "ok"


def _record_when_running(name: str, pid: int, env: dict[str, str],
                        timeout: float = 10.0) -> subprocess.CompletedProcess:
    """`record` a just-spawned pid, asking the SUT until it answers — and naming why not.

    ⛔ THEY AGREE FOR ANY PID THIS USER CAN SIGNAL. `kill -0` succeeds on a zombie — it
    is the state letter that rules one out, on either side — so both ask "exists and is
    not a zombie". The only one-sample divergences are EPERM (exists, not signalable)
    and an unreadable `/proc`, and neither is reachable for a child of THIS process. So a
    single sample cannot disagree, and what the reds this issue reports actually are is
    not a predicate mismatch:

      * at the preview decoy the decoy reached `execve` and died there — a renamed
        multi-call binary cannot run under its new name (#12039 measured on
        `ubuntu-26.04`) — the fixture's; fixed here, and now probed with `_await_exec`,
        because a decoy that dies in the millisecond after exec is still "alive" to the
        `/proc` read `_wait_alive` uses and is refused moments later;
      * otherwise the process was gone by the time `record` spawned its shell — a GAP IN
        TIME, which no retry reverses (issue #12039; `test (a)` red on main at
        `4b1d6cd0d4`).

    ⚠️ WHAT THE POLL CAN AND CANNOT REPAIR — stated because the honest answer
    determines whether a red here blames the code or the box. The poll re-asks `record`
    until it answers or the deadline expires. ⛔ IT DOES MASK A FIRST REFUSAL — that is
    its whole point, and `test_the_record_poll_retries_a_refusal_until_the_sut_answers`
    pins exactly that (a refusal on call 1, `returncode == 0` on call 2). What it never
    does is MANUFACTURE a success: every success returned from here is an answer the SUT
    gave, and a refusal the SUT repeats survives the deadline as a FAILURE.
    Of the two arms: the LIVENESS arm (`kill -0` says ESRCH/EPERM, or the state letter
    is `Z`) is a POSITIVE observation that the process is gone or a zombie, and no number
    of retries brings it back. The START-TIME arm (`pid_starttime` returned nothing) is
    only the observation that a READ FAILED — on Linux, `/proc/<pid>/stat` could not be
    read, which for a child this test spawned means the pid went away before that read
    (a row it COULD parse cannot produce this refusal at all: a short row yields an empty
    start and exit 0, so `record` would not refuse). So this is not a fix for "the decoy
    was OOM-killed on a thrashing runner", and it does not turn a dead pid green: what it
    buys is a SECOND SAMPLE, plus a refusal that says WHICH leg refused so the next
    reader does not have to guess. ⚠️ The loop re-asks on ANY refusal (gating it on the
    arm would make a message an assertion condition, which this file forbids), so a
    liveness refusal costs the full deadline and still fails — correctly.

    A refusal that survives the deadline stays a FAILURE: this is a bounded second
    look at a transient window, not a licence for a dead pid to go green.
    """
    deadline = time.time() + timeout
    result = _script("record", "--name", name, "--pid", str(pid), env=env)
    while result.returncode != 0 and time.time() < deadline:
        time.sleep(0.05)
        result = _script("record", "--name", name, "--pid", str(pid), env=env)
    return result


def _refusal_arm(env: dict[str, str], name: str, pid: int) -> str:
    """WHICH of `record`'s two refusal arms fired — read from the SUT's OWN witness.

    ⛔ THE ARM IS A FACT THE SUT WROTE AT REFUSAL TIME, not an inference from the pid
    afterwards. `cmd_record` writes `refused <pid> not-running|no-start-time` before
    either refusal can fire (`preview-server.sh::cmd_record`), precisely because the
    two arms send the reader to different places. Probing the pid here instead would
    be a measurement of a LATER instant: a decoy that was alive at refusal (the
    start-time arm, the one this change exists to surface) can be a zombie
    by the time the message is formatted, and a derived "the decoy died" would then
    invert the attribution in the very message #12039 asks to be legible.

    ⛔ AND THE WITNESS MUST NAME *THIS* PID. The file is never removed by `stop` or
    `stop-all`, and `cmd_record` writes nothing at all on its `die`/`mkdir` paths — so
    a refusal that does not reach either arm (an unwritable `PREVIEW_STATE`, a bad
    argument) leaves an EARLIER refusal's witness in place. Reading it unqualified would
    report a confident arm for a refusal that never happened, which is the same
    attribution inversion this helper exists to end, one layer up.

    An unrecognised, mismatched or missing witness is reported as such, never guessed.

    ⛔ AND "MISSING" MUST BE ASKED OF THE SUT'S OWN STATE DIRECTORY. `Path(env.get(
    "PREVIEW_STATE", ""))` is `Path("")` → `.`, the TEST's cwd, when the variable is
    unset — while the SUT's `state_dir` falls back to
    `${RUNNER_TEMP:-${TMPDIR:-/tmp}}/tortoise-preview/<run>-<job>`. Reading the wrong
    directory would report "no witness was written" about a witness `cmd_record` DID
    write, i.e. the same attribution inversion this helper exists to end, one layer
    down. The SUT answers this question itself (`dir`), so this mirrors nothing and
    there is no second derivation to drift.
    """
    root = env.get("PREVIEW_STATE", "")
    if root:
        state_dir = Path(root)
    else:
        resolved = _script("dir", env=env)
        reported = resolved.stdout.strip()
        if resolved.returncode != 0 or not reported:
            return (
                "`PREVIEW_STATE` is unset and the SUT's own `dir` verb reported no state "
                "directory — the arm is unrecorded (the witness cannot be located)"
            )
        state_dir = Path(reported)
    path = state_dir / f"{name}.refused"
    try:
        text = path.read_text().strip()
    except OSError:
        return f"no `{name}.refused` witness was written — the arm is unrecorded"
    parts = text.split()
    if len(parts) < 3 or parts[0] != "refused":
        return f"unrecognised refusal witness {text!r} — the arm is not covered here"
    if parts[1] != str(pid):
        return (
            f"the witness on disk names pid {parts[1]}, not this refusal's pid {pid} — "
            "the arm is unrecorded for this pid (a refusal that writes no witness, e.g. "
            "an unwritable state dir, leaves an EARLIER one in place)"
        )
    arm = parts[2]
    if arm == "not-running":
        return (
            "the LIVENESS leg refused (`pid_running`). ⚠️ THE WITNESS DOES NOT SAY WHICH "
            "OF THAT ARM'S THREE CAUSES FIRED — `kill -0` failing gives ESRCH (gone) or "
            "EPERM (unsignalable), and a `Z` state letter is the third — so this names "
            "the candidates and not a culprit: the decoy we spawned died — at the "
            "sites that run an interpreter under a fake `argv[0]` its own `-c` code can "
            "be the cause, and elsewhere it is the box that killed it (a thrashing "
            "runner OOM-killing a decoy is #12039's own wording for this case) — or a "
            "peer job's machine-global reap killed it (#10283's actual defect, i.e. a "
            "REAL finding). ⚠️ A pid the read could not SEE is NOT one of them and "
            "lands on the start-time arm instead: `pid_state` returns empty, "
            "`[[ \"\" != \"Z\" ]]` is true, so `pid_running` passes and "
            "`pid_starttime` is what refuses. Read the decoy's own exit status and the "
            "job log before closing a red here as flake (`stderr` says 'is not "
            "running' for every cause named above)"
        )
    if arm == "no-start-time":
        return ("the START-TIME leg refused (`pid_starttime`): the SUT read no start "
                "time for the pid — `/proc/<pid>/stat` on Linux, `ps -o lstart=` on a "
                "host without it. ⚠️ THIS IS NOT THE SUT MISREADING A ROW: "
                "`proc_starttime` cannot refuse on a row it could parse — a short row "
                "yields an EMPTY start and exit 0, so `cmd_record` would write "
                "`<name>.start` and not refuse at all — so the witness means the READ "
                "ITSELF failed. That read is `/proc/<pid>/stat`, so the pid went away "
                "between `pid_running` and this read (a `pid_state` that returned "
                "EMPTY lands here too: `[[ \"\" != \"Z\" ]]` passes and the "
                "start-time read is then what refuses), or `/proc` could not be read "
                "at all. Either way the cause is the box or the decoy — never a wrong "
                "start time — and the poll does not repair it: a reaped process does "
                "not come back")
    return f"unrecognised refusal witness {text!r} — the arm is not covered here"


def _pid_now(pid: int) -> str:
    """The pid's state NOW — an OBSERVATION for the failure message, never an attribution.

    Kept strictly separate from `_refusal_arm`: this is sampled seconds after the
    refusal, so it may not be used to explain it.
    """
    kill0 = _kill0(pid)
    if not kill0.startswith("ok"):
        return f"kill -0 says {kill0}"
    return f"kill -0 says ok, state is {_pid_state_letter(pid) or 'unreadable'!r} " \
        "(from `/proc/<pid>/stat`, or `ps -o state=` on a host without /proc)"


def _recorded(result: subprocess.CompletedProcess, pid: int, env: dict[str, str],
              name: str = "app", timeout: float = 10.0) -> str:
    """The assertion message for a `record` refusal: the arm, then the current state.

    `timeout` is read from the caller rather than assumed — a message that claims a
    deadline the poll never spent sends the next reader to the wrong conclusion — so a
    caller that did NOT poll passes `timeout=0.0`, which omits the duration instead of
    rendering `for 0s`. The default matches `_record_when_running`'s.
    The pid's state NOW is labelled as an observation, because attributing a refusal
    to what the pid looks like afterwards is how a fixture death gets blamed on the
    SUT (or the reverse).
    """
    spent = f" for {timeout:g}s" if timeout else ""
    return (
        f"`record` refused pid {pid}{spent}: {result.stderr.strip()!r} — "
        f"the SUT's own arm: {_refusal_arm(env, name, pid)}; observed NOW: {_pid_now(pid)} "
        "(#12039: the refusing arm is the SUT's witness, not a later guess)"
    )


def _reap(*pids: int) -> None:
    for pid in pids:
        # Already-exited decoys are the normal case here: the point is that the
        # helper killed them, not that we did. SIM105's `suppress` is the idiom.
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 9)


def _reap_group(pid: int) -> None:
    """Kill a `_spawn`ed tree by its SESSION, not by an enumerated child list (#12227).

    `_spawn` sets `start_new_session=True`, so `pid` is the group leader and every
    descendant is in its group — including a child forked AFTER the last
    `_child_pids` sample. Reaping the ENUMERATION cannot cover that window: the one
    place this matters is `assert len(children) == 2`, whose whole content is that the
    enumeration is incomplete, and SIGKILL to the supervisor does not reach a child.
    Measured before this change: a guard reaping only one of two enumerated children
    left `sleep 120` (ppid 1) on the box, one per red shard.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, 9)


@pytest.fixture()
def state(tmp_path: Path) -> dict[str, str]:
    return {
        "PREVIEW_STATE": str(tmp_path / "preview"),
        "GITHUB_RUN_ID": "424242",
        "GITHUB_JOB": "legal-e2e",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }


# ── 1. the workflow shape: no host-global reap, no shared port, no /tmp pid ──

def test_no_workflow_reaps_a_preview_by_process_name() -> None:
    """`pkill workerd` is machine-global: several runner AGENTS share one host.

    This is the arm that is RED on the pre-fix workflow (6 occurrences across
    `welcome-e2e`, `legal-e2e` and `dashboard-e2e`) and it is the one no other
    guard in this repo covers: the `redis-server` arm next door pins a different
    process name, and the wrangler arms pin the version, not the teardown.
    """
    offenders: list[str] = []
    for path in _workflow_paths():
        for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
            line = _cut_comment(raw)
            if re.search(r"\bpkill\b.*(?:workerd|wrangler\b)", line) or re.search(
                r"\bkillall\b.*(?:workerd|wrangler\b)", line
            ):
                offenders.append(f"{path.name}:{lineno}: {raw.strip()}")
    assert not offenders, (
        "a workflow reaps a preview by PROCESS NAME — `pkill`/`killall` match every "
        "process on the box, so one job's teardown kills a CONCURRENT job's preview "
        "and reds a required check on a PR that never touched it (#10283). Signal "
        "the pid the job recorded instead (`.github/scripts/preview-server.sh stop-all`):\n"
        + "\n".join(offenders)
    )


def test_no_workflow_shares_a_fixed_preview_port_or_tmp_path() -> None:
    """A shared port is the OTHER half: it cannot bind, and the probe passes anyway.

    A second job booting `--port 8788` cannot bind, while its readiness `curl`
    SUCCEEDS against the FIRST job's server — so it silently grades a peer's
    checkout (the #2757 shape). The `/tmp/wrangler*.pid` files are the same
    defect one level down: `kill $(cat /tmp/wrangler.pid)` can only ever address
    whatever pid the LAST writer put there.
    """
    offenders: list[str] = []
    for path in _workflow_paths():
        for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
            line = _cut_comment(raw)
            if re.search(r"--port\s+(?:8788|8790)\b", line):
                offenders.append(f"{path.name}:{lineno}: {raw.strip()}")
            if re.search(r"/tmp/wrangler[\w.-]*", line):
                offenders.append(f"{path.name}:{lineno}: {raw.strip()}")
    assert not offenders, (
        "a workflow pins a preview to the shared loopback port / shared /tmp path; "
        "these are per-run+job values from `.github/scripts/preview-server.sh` (#10283):\n"
        + "\n".join(offenders)
    )


# A readiness comment that claims the probe maps the port's LISTENER to our pid.
# `ready` cannot: it asserts that our pid is ALIVE and that the port ANSWERS, two
# INDEPENDENT facts, and nothing in it relates them (a socket→pid map needs
# `ss`/`lsof`, which the runner image does not guarantee). Both spellings this
# branch has shipped are listed so the arm catches the claim, not one wording.
_READY_OVERCLAIM = re.compile(
    r"hold(?:s|ing)?\s+(?:that|the)\s+port"
    r"|(?:must\s+never|cannot|can\s+never|never)\s+satisfy",
    re.IGNORECASE,
)
# The disclaimer, in the form `cmd_ready` and this file's docstring already use.
_READY_RESIDUAL = re.compile(
    r"does\s+not\s+prove\s+the\s+(?:port'?s\s+)?listener", re.IGNORECASE
)
_READY_SUBJECT = re.compile(r"\bready\b|\breadiness\b", re.IGNORECASE)


def _comment_blocks(path: Path) -> list[tuple[int, str]]:
    """Consecutive `#` lines joined — a claim that WRAPS must be read as one claim.

    Line-scoped matching is what let the overclaim survive: `a peer's server on
    the same port must never satisfy this` puts the subject on the first line and
    the false verb on the second, so no single line carried the defect.
    """
    blocks: list[tuple[int, str]] = []
    start = 0
    acc: list[str] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("#"):
            if not acc:
                start = lineno
            acc.append(stripped.lstrip("#").strip())
            continue
        if acc:
            blocks.append((start, " ".join(acc)))
            acc = []
    if acc:
        blocks.append((start, " ".join(acc)))
    return blocks


def test_readiness_comments_do_not_claim_a_listener_pid_mapping() -> None:
    """`ready` relates our pid's liveness to the port not at all — don't say it does.

    `cmd_ready` asserts (a) the pid we recorded is still alive and (b) the port
    answers. A bare `connect()` cannot name the pid behind a listener, so the two
    facts can be satisfied by a live-but-unbound pid beside a PEER's server —
    exactly the #2757 shape this helper exists to narrow. The window is closed by
    `alloc`'s pre-boot free-check and by wrangler's own exit on `EADDRINUSE`.

    The claim was still in four places at `5c8d598a3` — the helper's contract
    block (`preview-server.sh:45-47`), its `cmd_ready` (`:226-228`), and both
    `ci.yml` boot steps (`:1931`, `:2133`) — while this file's docstring (above)
    was the only place that stated the limitation. That direction is the defect:
    a lane reads `ci.yml`, believes readiness closes the collision window, and
    drops or widens the free-check on stated grounds. So the arm is RED on the
    pre-fix text at all four sites (non-vacuity proof) and the disclaimer has to
    survive in the helper a later edit would otherwise erode.
    """
    offenders: list[str] = []
    examined = 0
    for path in (SCRIPT, *_workflow_paths()):
        for start, block in _comment_blocks(path):
            if not _READY_SUBJECT.search(block):
                continue
            examined += 1
            if _READY_OVERCLAIM.search(block):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{start}: {block}")
    assert not offenders, (
        "a readiness comment claims `ready` proves the port's LISTENER is ours. It "
        "does not: it asserts our pid is alive and the port answers, and relates "
        "them not at all (a socket→pid map needs `ss`/`lsof`, which the runner "
        "image does not guarantee). State the residual instead — `alloc`'s "
        "pre-boot free-check and wrangler's exit on EADDRINUSE are what narrow it "
        "(#10283/#2757):\n" + "\n".join(offenders)
    )
    assert examined, "no readiness comment found in the helper or any workflow — vacuous arm"
    assert _READY_RESIDUAL.search(SCRIPT.read_text()), (
        "`.github/scripts/preview-server.sh` no longer states what `ready` does NOT "
        "prove. The limitation has to live where the mechanism lives — dropping it "
        "sent four other comments back to the false stronger claim (#10283)."
    )


def test_every_preview_job_boots_and_aborts_through_the_scoped_helper() -> None:
    """The workflow must actually USE the scoped helper — the arms above are negative.

    Without this, deleting the boots would satisfy every arm in this file.
    """
    ci = _load_workflow("ci.yml")
    jobs = ci.get("jobs") or {}
    problems: list[str] = []
    for job_name in PREVIEW_JOBS:
        job = jobs.get(job_name)
        if job is None:  # pragma: no cover - a rename is a finding, not a skip
            problems.append(f"{job_name}: job is gone (rename it here too)")
            continue
        env = job.get("env") or {}
        if "PREVIEW_STATE" in env:
            # actionlint reds a `runner.` context here (it is unavailable at
            # job-level `env:`), and a job-level state dir is the shared-literal
            # shape this issue is about anyway.
            problems.append(f"{job_name}: job-level PREVIEW_STATE (shared state dir / invalid context)")
        boots = [
            step for step in (job.get("steps") or [])
            if (step.get("name") or "").startswith(BOOT_STEP_PREFIX)
        ]
        if not boots:
            problems.append(f"{job_name}: no boot step")
        for step in boots:
            run = step.get("run") or ""
            name = step.get("name")
            for needle, why in (
                ("preview-server.sh", "does not invoke the scoped helper at all"),
                ('PREVIEW_STATE="$(bash "$PREVIEW" dir)"',
                 "does not resolve its per-job state directory from the helper"),
                (" alloc", "does not allocate its port"),
                (" record", "does not record the pid it started"),
                (" ready", "readiness does not verify its own pid"),
                ("$GITHUB_OUTPUT", "does not publish the allocated port to later steps"),
            ):
                if needle not in run:
                    problems.append(f"{job_name}/{name!r}: {why}")
        stops = [
            step for step in (job.get("steps") or [])
            if "stop-all" in (step.get("run") or "")
        ]
        if not stops:
            problems.append(f"{job_name}: no scoped teardown (`stop-all`) step")
    assert not problems, "preview jobs are not wired to the scoped helper:\n" + "\n".join(problems)


# The only accepted guard spelling on a `record` line: a NON-aborting `||`
# handler. `|| exit 1` / `|| return 4` do not count — they keep the black box.
_RECORD_TOLERATED = re.compile(r"\|\|\s*(?:true|:)\s*$")


def test_every_preview_boot_tolerates_a_dead_pid_from_record() -> None:
    """`record` is not infallible, and a bare call turns a retry into a black box.

    `preview-server.sh record` exits **4** when the pid handed to it is already
    gone (`.github/scripts/preview-server.sh::cmd_record`:
    `pid_running "$pid" || { …; return 4; }`). A `run:` step's shell is
    `bash -e {0}`, so a bare `record` line aborts the whole STEP on exactly the
    failure the boot step's own comment names: a `wrangler` that exits within
    milliseconds because the bundled workerd lags the default compatibility
    date (also `EADDRINUSE`, also a missing binary). The abort drops three
    things the pre-fix step always did — attempt 2, the `::error::` annotation,
    and the `cat`/`tail` of wrangler's own log — and the job then reports only
    `Process completed with exit code 4`, with no wrangler stderr anywhere.

    Tolerating the exit keeps the readiness probe as the thing that reds (it
    fails on its own when the pid file was never written), so the loop still
    retries and still dumps the log. This arm is RED on the pre-fix workflow at
    all five boot sites, which is its non-vacuity proof.
    """
    ci = _load_workflow("ci.yml")
    jobs = ci.get("jobs") or {}
    problems: list[str] = []
    seen = 0
    for job_name in PREVIEW_JOBS:
        job = jobs.get(job_name) or {}
        for step in job.get("steps") or []:
            if not (step.get("name") or "").startswith(BOOT_STEP_PREFIX):
                continue
            for line in (step.get("run") or "").splitlines():
                code = _cut_comment(line).strip()
                if " record " not in f" {code} ":
                    continue
                seen += 1
                if not _RECORD_TOLERATED.search(code):
                    problems.append(
                        f"{job_name}/{step.get('name')!r}: `{code}` — a dead pid "
                        "exits 4 and `bash -e` aborts the step before the retry "
                        "and the log dump run"
                    )
    assert seen, "no `record` invocation found in any preview boot step — vacuous arm"
    assert not problems, (
        "a preview boot step aborts opaquely when `record` cannot record pid $!:\n"
        + "\n".join(problems)
    )


# ── 2. the mechanism: stop() is pid-scoped, and it refuses a recycled pid ───

def test_stop_all_leaves_a_peer_jobs_decoy_workerd_alone(tmp_path: Path, state) -> None:
    """#10283's own indicator (a): job A's cleanup leaves job B's decoy alive.

    The decoy is a process whose `comm` is literally `workerd` — the process NAME
    the pre-fix teardown matched with `pkill workerd` (no `-f`, so `pkill` reads the
    name, not the command line) — belonging to a DIFFERENT job's state directory.

    ⚠️ Scoped to a Linux host, and stated rather than implied: `comm` is set with
    `prctl(PR_SET_NAME)`, which is Linux-only, and the assertion that the name really
    took is read from `ps -o comm=` behind a `Path("/proc").is_dir()` guard. On a host
    without `/proc` the decoy is still spawned and still recorded, but its `comm` stays
    the interpreter's — so this test does not reproduce the `pkill workerd` shape there.
    """
    assert SCRIPT.is_file(), f"missing {SCRIPT}"

    # Job B: a `workerd`-named decoy, registered in ITS OWN state directory.
    #
    # ⛔ NOT A SYMLINK TO `sleep` (#12039, measured). On the `ubuntu-26.04` hosted image
    # `/usr/bin/sleep` is a uutils MULTI-CALL binary that dispatches on
    # `basename(argv[0])`, so a process renamed to `workerd` refuses to run at all
    # (`coreutils: unknown program 'workerd'`, exit 1). The decoy then never exists and
    # this test reds as `record: pid N is not running` — #12039's exact signature — with
    # nothing wrong in the SUT. The sibling census fixture in `tests/test_fleet_state.py`
    # carries the same fix (`executable=`), for the same reason.
    #
    # ⛔ `argv[0]` IS NOT WHAT `pkill workerd` MATCHED — `pkill` with no `-f` matches the
    # process NAME, which on Linux is `comm`, so `prctl(PR_SET_NAME)` is the half that
    # makes this decoy faithful to #10283's shape. `argv[0]` is here only as the handle
    # the interpreter is spawned under, and `executable=` is what removes the dependency
    # on a copied binary accepting its new name.
    peer_bin = tmp_path / "bin"
    peer_bin.mkdir()
    peer_workerd = peer_bin / "workerd"
    peer_state = dict(state, PREVIEW_STATE=str(tmp_path / "peer-preview"), GITHUB_JOB="dashboard-e2e")
    peer = _spawn(
        [str(peer_workerd), "-c",
         "import ctypes, time\n"
         "try:\n"
         "    ctypes.CDLL(None).prctl(15, b'workerd', 0, 0, 0)\n"
         "except (OSError, AttributeError):\n"
         "    pass\n"              # no prctl (macOS): `comm` stays the interpreter's,
         "                        #  and the name pin below cannot run without /proc\n"
         "time.sleep(120)\n"],
        tmp_path, executable=sys.executable,
    )
    # ⛔ THE DECOY IS SPAWNED BEFORE THE `try:` THAT OWNS ITS `finally: _reap(...)`
    # further down, so every assertion between the two is a leak path for a 120 s
    # process in its own session. This guard reaps on those paths only.
    try:
        _await_exec(peer.pid, str(peer_workerd))
    except BaseException:
        _reap(peer.pid)
        raise
    # The decoy must really BE a `workerd` process, or this test proves nothing about the
    # name `pkill workerd` matched (#12039 replaced the symlinked `sleep` with an
    # interpreter under that argv[0] + `prctl`, so the pin moves from "the symlink exists"
    # to "the process is named workerd"). `ps -o comm=` is what `pkill` reads.
    #
    # ⛔ `ctypes.CDLL(None)` RESOLVES `prctl` FROM THE LIBC PYTHON ALREADY LOADED, so no
    # soname is assumed — hardcoding `libc.so.6` would fail on musl, where `/proc` DOES
    # exist, and the pin below would then red a fixture on a box where the SUT is fine
    # (the class this PR exists to remove). The `/proc` guard is a Linux test, because
    # `prctl` is Linux-only and comm stays the interpreter's elsewhere.
    #
    # ⛔ A BOUNDED POLL, because `prctl` is NOT part of the `execve` `_await_exec` waits
    # for: it runs later, inside the child's `-c` code, ~13-22 ms after exec on this box
    # (measured), while `ps -o comm=` costs ~42 ms — so a one-shot sample passes here by
    # luck of relative latency. It fails deterministically on a faster process table
    # (measured: 4/4 reds in a PID namespace where `ps` takes ~4 ms). The poll still reds
    # if `prctl` never runs at all.
    #
    # ⛔ THE POLL IS INSIDE THE GUARD, NOT ONLY THE `assert` (#12330 REVIEW). The `ps`
    # call has NO `timeout=`, so its realistic failure is an `OSError` under fork pressure
    # (EAGAIN/ENOMEM) — the exact trigger the JOB-A guard below names — and it runs for a
    # bounded 5 s on every Linux leg. ⛔ That bound is ~80 iterations, not the 250 a bare
    # `5 s / 0.02 s` suggests: this block's own measurement puts a `ps -o comm=` sample at
    # ~42 ms, so each iteration costs ~62 ms, and two claims in one sentence that cannot
    # both hold is the count class this PR already corrects elsewhere. With the `try:`
    # around the assertion alone, that raise ran no teardown and leaked this 120 s decoy in
    # its own session.
    # Measured: injecting `OSError` from the `ps` call left the decoy alive, reparented to
    # pid 1. Nothing between the spawn and its reaper may be unguarded.
    if Path("/proc").is_dir():
        try:
            deadline, comm = time.time() + 5.0, ""
            while time.time() < deadline:
                comm = subprocess.run(["ps", "-o", "comm=", "-p", str(peer.pid)],
                                      capture_output=True, text=True).stdout.strip()
                if comm == "workerd":
                    break
                time.sleep(0.02)
            assert comm == "workerd", (
                f"the peer decoy is named {comm!r}, not `workerd` — the shape #10283's "
                "pre-fix machine-global `pkill workerd` matched is not reproduced"
            )
        except BaseException:
            _reap(peer.pid)
            raise
    try:
        result = _record_when_running("app", peer.pid, env=peer_state)
        assert result.returncode == 0, _recorded(result, peer.pid, peer_state)
    except BaseException:
        _reap(peer.pid)
        raise

    # Job A: a real tree (a supervisor + two children, like wrangler + workerd).
    # ⛔ JOB A'S OWN SPAWN IS A LEAK PATH TOO (#12227 review cycle 6). It runs BEFORE the
    # `try:` below, so a failure in ANY of the three asserts that shape the tree leaks the
    # supervisor, its two children AND the peer decoy — three 120 s processes in their own
    # sessions — which the comment above used to claim "the later `finally` still reaps on
    # the rest". It did not. This guard covers exactly that window; the `finally` below
    # covers everything from ITS `try:` onward — which is why `stop-all` is the first
    # statement inside it and nothing sits between the two arms. A double reap is a
    # suppressed no-op.
    #
    # ⛔ AND IT REAPS THE SESSION, NOT THE ENUMERATION (#12227 review cycle 8). Reaping
    # `own.pid` plus the `children` LIST could not cover this guard's own failure mode:
    # the list is exactly what `assert len(children) == 2` is about to reject when the
    # poll timed out, and a child forked after the last sample is in the group but not in
    # the list — SIGKILL to the supervisor does not reach it. Measured: omitting one
    # enumerated child from the reap left that child behind as `ppid 1`, because Linux
    # reparents at the parent's EXIT (`do_exit`), not at its reap — the supervisor was
    # killed but never `wait()`ed, and its children were orphaned anyway (the same
    # measurement `_reap_group` records).
    #
    # ⛔ AND THE SPAWN ITSELF IS INSIDE THE `try:` (#12227 review cycle 10). `_spawn` can
    # raise (`OSError` under fork pressure); with `own` created before the guard, that
    # raise ran no teardown and leaked the PEER decoy, which is already alive at this
    # point. `own is None` is the "no tree was created" case.
    own = None
    children: list[int] = []
    try:
        own = _spawn(["bash", "-c", "sleep 120 & sleep 120"], tmp_path)
        _wait_alive(own.pid)
        deadline = time.time() + 5
        while time.time() < deadline and len(_child_pids(own.pid)) < 2:
            time.sleep(0.05)
        children = _child_pids(own.pid)
        assert len(children) == 2, f"the tree did not form (children={children})"

        recorded = _record_when_running("app", own.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, own.pid, state)
    except BaseException:
        if own is not None:
            _reap_group(own.pid)
        _reap(peer.pid)
        raise
    try:
        # ⛔ `stop-all` IS INSIDE THE `try:`, NOT BETWEEN THE GUARD AND IT (#12227 review
        # cycle 9). It was the one statement between the leak guard's `except` and the
        # `try:` that owns the `finally`, so its own failure — a `timeout=60` expiring on a
        # stalled runner, an `OSError` under fork pressure — ran NEITHER teardown and
        # leaked the supervisor, its two `sleep 120` children and the peer's 120 s
        # `workerd` decoy. Measured before the move: forcing that call to raise left
        # `bash -c "sleep 120 & sleep 120"`, its two `sleep 120` children — still parented
        # to the SURVIVING supervisor, because nothing on this path is killed — and the
        # peer decoy, alive. Nothing may sit between the two arms.
        result = _script("stop-all", env=state)
        assert result.returncode == 0, result.stderr
        deadline = time.time() + 10
        while time.time() < deadline and _pid_alive(own.pid):
            time.sleep(0.05)
        assert not _pid_alive(own.pid), "the job's OWN recorded pid was not stopped"
        for child in children:
            deadline = time.time() + 10
            while time.time() < deadline and _pid_alive(child):
                time.sleep(0.05)
            assert not _pid_alive(child), (
                f"a descendant of the recorded pid survived stop-all ({child}) — the "
                "tree kill is what replaces `pkill workerd`"
            )
        assert _pid_alive(peer.pid), (
            "stop-all killed a process from ANOTHER job's state directory — this is "
            "the #10283 defect: a peer's preview dies mid-run and its spec fails with "
            "ERR_CONNECTION_REFUSED"
        )
    finally:
        # ⛔ NO `own is not None` GUARD HERE (#12330 review, P3). It is dead: this `try:`
        # is only reached when the FIRST `try:` completed, which requires `_spawn` to have
        # returned — the path where `own` stayed `None` re-raises out of the `except` above
        # and never gets here. The guard is load-bearing in that `except`, which is why the
        # `None` sentinel still exists.
        _reap_group(own.pid)
        _reap(peer.pid)


def test_a_raising_stop_all_still_reaps_the_tree_and_the_peer_decoy(
    tmp_path: Path, state, monkeypatch
) -> None:
    """⛔ THE MOVE IS PINNED BY REVERTING IT (#12330 review, P3).

    `test_stop_all_leaves_a_peer_jobs_decoy_workerd_alone` passes byte-identically whether
    `result = _script("stop-all", …)` sits INSIDE the `try:` that owns the `finally` or
    between the two arms: the arms' `assert` text is unchanged, so nothing in the tree
    reds when the window is reopened — which is how it was reopened once already (#12227
    closed the guard, and left this one statement outside it).

    So this test exercises the FAILURE MODE the guard exists for, using the real body
    above rather than a re-implementation of it: `_script` keeps working for every verb,
    and only `stop-all` raises the `OSError` its own comment names (fork pressure under
    EAGAIN/ENOMEM; a `timeout=60` expiring is the same shape). `_spawn` is wrapped only to
    REMEMBER the pids — the body does not return them.

    Measured both ways: with `stop-all` outside the `try:` the supervisor, its two
    `sleep 120` children (still parented to it, because nothing on that path is killed)
    and the peer decoy are all alive when the `OSError` surfaces, and this test reds;
    with it inside, every spawned pid is gone when the raise propagates.

    ⛔ THE SURVIVAL CHECK IS NOT THE WHOLE PIN (#12330 review, P3). `spawned` holds only
    the pids `_spawn` RETURNED, so the supervisor's two `sleep 120` children are not in
    it. A teardown downgraded from `_reap_group(own.pid)` to `_reap(own.pid)` kills the
    supervisor, orphans both children (ppid 1) and left this test GREEN (measured).
    A liveness probe cannot close that on its own either: `killpg(pid, 0)` still succeeds
    for a group whose members are zombies, so it reports "alive" on the CORRECT tree
    (measured, both spawned pids). The group reap is therefore pinned DIRECTLY, by
    recording `_reap_group`'s calls while the production teardown runs — no dependence on
    how fast a killed process leaves `/proc`.
    """
    spawned: list[int] = []
    injected: list[str] = []
    real_spawn = _spawn
    real_script = _script

    def recording_spawn(*args, **kwargs):
        proc = real_spawn(*args, **kwargs)
        spawned.append(proc.pid)
        return proc

    def raising_stop_all(*args, **kwargs):
        verb = args[0] if args else kwargs.get("name")
        if verb == "stop-all":
            injected.append(verb)
            raise OSError(11, "Resource temporarily unavailable")
        return real_script(*args, **kwargs)

    monkeypatch.setattr(sys.modules[__name__], "_spawn", recording_spawn)
    monkeypatch.setattr(sys.modules[__name__], "_script", raising_stop_all)

    # ⛔ RECORD THE TEARDOWN'S GROUP REAP (#12330 review, P3). See the docstring: a
    # per-pid `_reap(own.pid)` orphans the supervisor's children, and they are invisible
    # to a check over `spawned`. The recorder DELEGATES, so the real teardown still runs.
    group_reaps: list[int] = []
    real_reap_group = _reap_group

    def recording_reap_group(pid: int) -> None:
        group_reaps.append(pid)
        real_reap_group(pid)

    monkeypatch.setattr(sys.modules[__name__], "_reap_group", recording_reap_group)
    # The teardown is asserted AFTER the raise, so a regression here is the one case where
    # this test must not leak: reap whatever survived before it reports.
    try:
        with pytest.raises(OSError, match="Resource temporarily unavailable"):
            test_stop_all_leaves_a_peer_jobs_decoy_workerd_alone(tmp_path, state)
        assert injected, (
            "the body never reached `stop-all`, so this pin did NOT exercise the window "
            f"it exists for (spawned {spawned})"
        )
        assert len(spawned) >= 2, (
            f"only {spawned} were spawned — the tree or the decoy is missing, so a leak "
            "check over them would pass vacuously"
        )
        # ⛔ A BOUNDED WAIT, NOT ONE SAMPLE (#12330): `_reap_group`/`_reap` send SIGKILL
        # and return at once, so the very next `/proc` read still sees the process for a
        # few microseconds (measured: both spawned pids alive on the FIRST sample of the
        # fixed tree, gone well inside the first 50 ms). A one-sample assertion reds on a
        # correct tree; the same poll on a REVERTED tree never settles, because nothing on
        # that path kills these processes at all.
        # ⛔ SNAPSHOT THE PRODUCTION TEARDOWN, NOT THIS TEST'S OWN CLEANUP (#12330).
        # Taken here — after the `with pytest.raises(...)` exited and BEFORE the `finally`
        # below — `group_reaps` holds only what the body's teardown did. If it held the
        # `finally`'s calls too, the gap this pins would be hidden again.
        assert spawned[-1] in group_reaps, (
            "the `stop-all` teardown did not reap the job-A tree by its PROCESS GROUP "
            f"(groups reaped: {group_reaps}, spawned: {spawned}). A per-pid `_reap` kills "
            "the supervisor and orphans its two `sleep 120` children into the same "
            "session — #12227's leak class — so `_reap_group(own.pid)` is the behaviour "
            "this pins."
        )
        deadline = time.time() + 10
        survived = [pid for pid in spawned if _pid_alive(pid)]
        while survived and time.time() < deadline:
            time.sleep(0.05)
            survived = [pid for pid in spawned if _pid_alive(pid)]
        assert not survived, (
            "a raise from `stop-all` left processes alive "
            f"({survived} of {spawned}) — that is #12227's leak class, reopened by "
            "moving the call out of the `try:` that owns the `finally`. Nothing may sit "
            "between the two teardown arms."
        )
    finally:
        for pid in spawned:
            _reap_group(pid)


def test_a_raising_ps_poll_still_reaps_the_peer_decoy(
    tmp_path: Path, state, monkeypatch
) -> None:
    """⛔ THE `ps` POLL IS INSIDE THE GUARD, PINNED BY REVERTING IT (#12330).

    The comm poll that proves the peer decoy is NAMED `workerd` has no `timeout=`, so its
    realistic failure is an `OSError` under fork pressure (EAGAIN/ENOMEM) — the trigger
    the job-A guard already names — and it runs up to 250 times on every Linux leg. Before
    this PR the poll sat OUTSIDE the guard that reaps the decoy: only the `assert` after it
    was inside a `try:`, so a raise from `ps` left a 120 s `workerd` alive, reparented to
    pid 1.

    Moving the poll back outside the guard leaves the REST of this file GREEN (measured:
    43 passed) — nothing else injects a failure from the `ps` call, because the raising
    `_script` arm in `test_a_raising_stop_all_...` never reaches it. So the guard was
    unpinned while its own comment claimed nothing between the spawn and its reaper is
    unguarded; this pins it by raising from the poll ONLY and asserting the decoy is gone.
    """
    if not Path("/proc").is_dir():
        pytest.skip("the comm poll is a Linux test (`prctl` names the decoy)")

    spawned: list[int] = []
    real_spawn = _spawn
    real_run = subprocess.run

    def recording_spawn(*args, **kwargs):
        proc = real_spawn(*args, **kwargs)
        spawned.append(proc.pid)
        return proc

    def raising_comm_poll(cmd, *args, **kwargs):
        # ONLY the `ps -o comm= -p <pid>` read raises. `-o command=` (what `_await_exec`
        # reads) does not contain the literal `comm=`, so the wait for the decoy's argv0
        # still runs and the body reaches the poll.
        if isinstance(cmd, (list, tuple)) and "comm=" in cmd:
            raise OSError(11, "Resource temporarily unavailable")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(sys.modules[__name__], "_spawn", recording_spawn)
    monkeypatch.setattr(subprocess, "run", raising_comm_poll)
    try:
        with pytest.raises(OSError, match="Resource temporarily unavailable"):
            test_stop_all_leaves_a_peer_jobs_decoy_workerd_alone(tmp_path, state)
        assert spawned, "the body spawned nothing, so a leak check over it would be vacuous"
        peer_pid = spawned[0]
        # A BOUNDED WAIT, for the same reason as the arm above: SIGKILL is asynchronous.
        # On a tree with the poll outside its guard nothing kills the decoy at all, so
        # this never settles and the test reds.
        deadline = time.time() + 10
        while _pid_alive(peer_pid) and time.time() < deadline:
            time.sleep(0.05)
        assert not _pid_alive(peer_pid), (
            f"a raise from the `ps` comm poll left the peer decoy ({peer_pid}) alive — "
            "the poll must sit INSIDE the guard that reaps it (#12330). A 120 s `workerd` "
            "reparented to pid 1 is exactly the leak that guard exists to prevent."
        )
    finally:
        for pid in spawned:
            _reap_group(pid)


def test_a_raising_job_a_spawn_still_reaps_the_peer_decoy(
    tmp_path: Path, state, monkeypatch
) -> None:
    """⛔ `_reap(peer.pid)` SITS OUTSIDE THE `own is not None` GUARD — PINNED (#12330).

    The job-A arm is `except BaseException: if own is not None: _reap_group(own.pid);
    _reap(peer.pid); raise`. That guard is load-bearing, not decoration: when `_spawn`
    raises, `own` is still `None`, and an UNGUARDED `_reap_group(own.pid)` raises
    `AttributeError: 'NoneType' object has no attribute 'pid'`, which (a) leaks the 120 s
    `workerd` decoy because the `_reap(peer.pid)` on the next line never runs, and (b)
    MASKS the real `OSError` that CI needs to see. Removing the guard leaves the whole
    file green (measured: 44 passed), so it is pinned here: make `_spawn`'s SECOND call
    (job A's tree root) raise the EAGAIN/ENOMEM `OSError` the comment names, then assert
    that `OSError` — not `AttributeError` — surfaces, and that the decoy is gone.
    """
    spawned: list[int] = []
    calls = {"n": 0}
    real_spawn = _spawn

    def raising_second_spawn(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # #1 is the peer decoy; #2 is job A's tree root
            raise OSError(11, "Resource temporarily unavailable")
        proc = real_spawn(*args, **kwargs)
        spawned.append(proc.pid)
        return proc

    monkeypatch.setattr(sys.modules[__name__], "_spawn", raising_second_spawn)
    try:
        with pytest.raises(OSError, match="Resource temporarily unavailable"):
            test_stop_all_leaves_a_peer_jobs_decoy_workerd_alone(tmp_path, state)
        assert calls["n"] == 2, (
            f"`_spawn` was called {calls['n']} time(s) — the job-A raise was not reached, "
            "so this pin did NOT exercise the `own is None` path"
        )
        assert spawned, "the peer decoy was never spawned, so a leak check over it is vacuous"
        peer_pid = spawned[0]
        # A BOUNDED WAIT, for the same reason as the other arms: SIGKILL is asynchronous.
        deadline = time.time() + 10
        while _pid_alive(peer_pid) and time.time() < deadline:
            time.sleep(0.05)
        assert not _pid_alive(peer_pid), (
            f"a raise from job A's `_spawn` left the peer decoy ({peer_pid}) alive — "
            "`_reap(peer.pid)` must sit OUTSIDE the `own is not None` guard, or an "
            "unguarded `_reap_group(own.pid)` raises `AttributeError` first and the "
            "decoy leaks while the real `OSError` is masked (#12330)."
        )
    finally:
        for pid in spawned:
            _reap_group(pid)


def test_await_exec_REFUSES_a_decoy_that_never_shows_its_name(tmp_path: Path) -> None:
    """`_await_exec` is the predicate that can refuse — pin the refusal, not just the pass.

    #12039 replaced a liveness read that could only ever say "alive" in the window that
    matters. Mutating `_await_exec` to a bare `return` leaves every other test in this
    file green, because a healthy decoy is visible to both predicates; only the refusal
    tells the two apart.

    ⛔ AND THE DECOY MUST BE ALIVE FOR THE ARM THAT DISCRIMINATES (#12227 review cycle 8).
    A pid that is gone is the cheapest input to reject — but a LIVENESS-ONLY predicate
    rejects it too, so a dead-pid-only pin stays green when `_await_exec` regresses to
    exactly the check #12039 removed (measured: splicing `_wait_alive(pid, timeout);
    return` in left every runnable test in this file green). The live-but-unnamed case is
    the one that separates them: the row reads `/bin/sleep 60`, `_pid_alive` answers True,
    and the row still does not start with the argv0 asked for — so only the name read can
    be what refuses.
    """
    victim = _spawn(["/bin/sleep", "60"], tmp_path)
    try:
        _wait_alive(victim.pid)
        # THE DISCRIMINATING ARM: alive, and the table names something else.
        with pytest.raises(pytest.fail.Exception):
            _await_exec(victim.pid, "/nonexistent/never-shown", timeout=0.3)
    finally:
        _reap(victim.pid)
    deadline = time.time() + 10
    while time.time() < deadline and _pid_alive(victim.pid):
        time.sleep(0.02)
    assert not _pid_alive(victim.pid), "the harness cannot measure: the decoy survived its own kill"

    # The cheap arm, kept: a pid that is gone is still rejected — legibly, without waiting
    # out the deadline.
    with pytest.raises(pytest.fail.Exception):
        _await_exec(victim.pid, "/nonexistent/never-shown", timeout=0.3)


def test_stop_refuses_a_pid_whose_start_time_changed(tmp_path: Path, state) -> None:
    """A recycled pid is not our server: refusing is fail-safe, signalling is not."""
    victim = _spawn(["/bin/sleep", "120"], tmp_path)
    _wait_alive(victim.pid)
    try:
        recorded = _record_when_running("app", victim.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, victim.pid, state)
        start_file = Path(state["PREVIEW_STATE"]) / "app.start"
        assert start_file.is_file()
        start_file.write_text("1\n")  # a DIFFERENT process now supposedly owns this pid
        result = _script("stop", "--name", "app", env=state)
        assert result.returncode == 0, result.stderr
        assert "recycled" in result.stdout, result.stdout
        assert _pid_alive(victim.pid), "a recycled pid was signalled — the guard did not fire"
    finally:
        _reap(victim.pid)


def test_alloc_returns_a_port_that_is_actually_free(tmp_path: Path, state) -> None:
    """The allocator is what prevents silently grading a peer's checkout (#2757)."""
    result = _script("alloc", "--name", "marketing", "--base", "18788", env=state)
    assert result.returncode == 0, result.stderr
    port = result.stdout.strip()
    assert port.isdigit(), result.stdout
    # The same connect test the script uses: nothing is listening there yet.
    probe = subprocess.run(
        ["bash", "-c", f'(exec 3<>"/dev/tcp/127.0.0.1/{port}") 2>/dev/null && exit 0 || exit 1'],
        capture_output=True,
    )
    assert probe.returncode != 0, f"alloc handed back a port that is already in use ({port})"
    assert (Path(state["PREVIEW_STATE"]) / "marketing.port").read_text().strip() == port


def _spawn_http(port: int, status: int, cwd: Path) -> subprocess.Popen:
    """A preview-shaped listener: binds, accepts, answers `status` on `/`."""
    code = (
        "import http.server, socketserver\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        f"        self.send_response({status})\n"
        "        self.send_header('Content-Length', '0')\n"
        "        self.end_headers()\n"
        "    def log_message(self, *a):\n"
        "        pass\n"
        "socketserver.TCPServer.allow_reuse_address = True\n"
        f"socketserver.TCPServer(('127.0.0.1', {port}), H).serve_forever()\n"
    )
    return _spawn([sys.executable, "-c", code], cwd)


def _port_answers(port: int) -> bool:
    """The PRE-fix readiness test: does a TCP connect to `port` succeed?"""
    return subprocess.run(
        ["bash", "-c", f'(exec 3<>"/dev/tcp/127.0.0.1/{port}") 2>/dev/null'],
        capture_output=True,
    ).returncode == 0


def _wait_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_answers(port):
            return True
        time.sleep(0.05)
    return False


def test_ready_requires_the_preview_to_SERVE_not_just_bind(tmp_path: Path, state) -> None:
    """`ready` must not hand a bound-but-not-yet-serving listener to the specs.

    The gate this replaces (`curl -fsS http://127.0.0.1:<port>/`) required an HTTP
    success; "the port answers a connect" does not — and the consumers require
    200: `tests/e2e/test_session_login_flow.py` exits the run on a non-200, which
    reds a REQUIRED check. This is the `tcpSocket` vs `httpGet` distinction in
    Kubernetes' own wording (`content/en/docs/concepts/workloads/pods/probes.md`):
    a TCP probe is *"considered successful if the port is open"*, while an HTTP
    probe requires a status in [200, 400).

    Both listeners below are alive, bound and recorded; only the response STATUS
    separates them, so the 500 arm is RED on a `ready` that stops at liveness +
    a connect (its non-vacuity proof). The 200 arm is the positive control —
    without it a `ready` that always returned 1 would satisfy the first arm.
    """
    result = _script("alloc", "--name", "app", "--base", "18788", env=state)
    assert result.returncode == 0, result.stderr
    port = int(result.stdout.strip())

    broken = _spawn_http(port, 500, tmp_path)
    healthy = None
    try:
        assert _wait_port(port), f"the decoy listener never bound :{port}"
        recorded = _record_when_running("app", broken.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, broken.pid, state)
        assert _script("ready", "--name", "app", env=state).returncode == 1, (
            f"a listener on :{port} that is bound, alive and answering 500 was reported "
            "READY — the specs behind this gate require 200, so the boot step would "
            "hand them a server they then declare unreachable (#10283's own class)"
        )

        _reap(broken.pid)
        deadline = time.time() + 10
        while time.time() < deadline and _port_answers(port):
            time.sleep(0.05)

        healthy = _spawn_http(port, 200, tmp_path)
        assert _wait_port(port), f"the healthy listener never bound :{port}"
        recorded = _record_when_running("app", healthy.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, healthy.pid, state)
        result = _script("ready", "--name", "app", env=state)
        assert result.returncode == 0, (
            f"a serving 200 preview on :{port} was reported NOT ready — the boot loop "
            f"would spin 240 s and red the job: {result.stderr}"
        )
    finally:
        _reap(broken.pid, *([healthy.pid] if healthy is not None else []))


def _pid_state_letter(pid: int) -> str:
    """The kernel's state letter for `pid` (`Z` = zombie), `""` when it is gone."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return subprocess.run(
            ["ps", "-o", "state=", "-p", str(pid)], capture_output=True, text=True
        ).stdout.strip()[:1]
    return stat[stat.rfind(") ") + 2:].split(" ", 1)[0]


def _function_body(text: str, name: str) -> str:
    """The lines of `name() { … }` in a bash script, without the signature/brace."""
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if not inside:
            inside = bool(re.match(rf"^{re.escape(name)}\(\)\s*\{{", line))
            continue
        if line == "}":
            break
        out.append(line)
    return "\n".join(out)


def test_alive_calls_a_zombie_GONE_not_alive(tmp_path: Path, state) -> None:
    """A zombie answers kill(0) forever, and the dump asserted a CAUSE from it.

    This is the first of the three false-verdict paths the fresh-context review of
    PR #11675 measured in the `ci.yml` steps. The pre-fix step branched on a bare
    `kill -0 "$pid"`, so an unreaped preview read as ALIVE and the step then told
    the reader the red was "the specs' or the content's, not a dead server" — from
    a process that had already exited. RED on the pre-fix code: `kill -0`
    SUCCEEDS here (asserted below, so the arm can never pass for the wrong
    reason), while `alive` must report GONE.
    """
    if not hasattr(os, "fork"):  # pragma: no cover — Windows only
        pytest.skip("no zombie can be produced without a real child process")
    # A real child that exits WITHOUT being reaped: `subprocess.Popen` holds the
    # pid until `wait()`/`poll()`, so the child stays a zombie. Deliberately not
    # `os.fork()` — forking a multi-threaded pytest process warns (and can
    # deadlock) for no benefit here.
    child = _spawn(["/bin/sleep", "2"], tmp_path)
    pid = child.pid
    try:
        recorded = _record_when_running("app", pid, env=state)
        assert recorded.returncode == 0, (
            "the child must be recorded while it is still running, like a boot step does"
            f" — {_recorded(recorded, pid, state)}"
        )
        deadline = time.time() + 15
        while time.time() < deadline and _pid_state_letter(pid) != "Z":
            time.sleep(0.05)
        assert _pid_state_letter(pid) == "Z", (
            "the child never became an unreaped zombie — this arm cannot measure"
        )
        assert subprocess.run(["bash", "-c", f"kill -0 {pid}"]).returncode == 0, (
            "the harness cannot measure: the pre-fix primitive must still SUCCEED on "
            "a zombie, which is exactly why it produced a false ALIVE"
        )
        result = _script("alive", "--name", "app", env=state)
        assert result.returncode == 1, (
            "an unreaped zombie was not reported as GONE — a preview that exited is "
            f"being called alive: {result.stdout}{result.stderr}"
        )
        assert "GONE" in result.stdout and "DIED MID-RUN" in result.stdout, result.stdout
        assert " is ALIVE" not in result.stdout, result.stdout
    finally:
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.wait(timeout=20)


def test_alive_refuses_a_recycled_pid(tmp_path: Path, state) -> None:
    """A recycled pid is a stranger: the process we started is still GONE.

    The second false-verdict path. `stop` already refuses to signal a pid whose
    start time changed (`test_stop_refuses_a_pid_whose_start_time_changed`), and
    the READ needs the same guard: the pre-fix step reported the stranger as our
    ALIVE preview.
    """
    victim = _spawn(["/bin/sleep", "120"], tmp_path)
    _wait_alive(victim.pid)
    try:
        recorded = _record_when_running("app", victim.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, victim.pid, state)
        (Path(state["PREVIEW_STATE"]) / "app.start").write_text("1\n")  # a different process
        result = _script("alive", "--name", "app", env=state)
        assert result.returncode == 8, (
            "a pid whose start time no longer matches the boot witness was not "
            f"reported as RECYCLED: {result.stdout}{result.stderr}"
        )
        assert "RECYCLED" in result.stdout, result.stdout
        assert "is ALIVE" not in result.stdout, result.stdout
    finally:
        _reap(victim.pid)


def test_the_recycled_sentence_is_WELL_FORMED_when_no_port_was_recorded(
    tmp_path: Path, state
) -> None:
    """The RECYCLED message spliced an empty port suffix into a clause.

    `anything answering${at_port} is a stranger's answer` with `at_port=""` renders
    "anything answering is a stranger's answer" — a malformed sentence in the one
    verdict a failure dump exists to make readable. Every SIBLING branch already
    guards the empty port (`the no-port branch says "no port was recorded … was
    NOT checked"`); only this one spliced, and the `no-port` leg is unreachable
    through the workflow today (`alloc` precedes `record`), which is why it
    survived (#11675 review, finding 2).

    RED on the pre-fix code: the no-port arm produced the double-space-free but
    ungrammatical "anything answering is a stranger's answer", with no statement
    that WHERE it answers was not checked.
    """
    victim = _spawn(["/bin/sleep", "120"], tmp_path)
    _wait_alive(victim.pid)
    try:
        recorded = _record_when_running("app", victim.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, victim.pid, state)
        (Path(state["PREVIEW_STATE"]) / "app.start").write_text("1\n")
        assert not (Path(state["PREVIEW_STATE"]) / "app.port").exists()

        no_port = _script("alive", "--name", "app", env=state)
        assert no_port.returncode == 8, no_port.stdout
        assert "anything answering is a stranger's answer" not in no_port.stdout, (
            "the RECYCLED sentence spliced an EMPTY port suffix into the clause: "
            f"{no_port.stdout}"
        )
        assert "answering at all is a stranger's answer" in no_port.stdout, no_port.stdout
        assert "WHERE it would answer was NOT checked" in no_port.stdout, (
            "the no-port RECYCLED verdict must say the leg it did NOT take, like its "
            f"sibling branches: {no_port.stdout}"
        )

        # …and the port-carrying form still names the port: the guard must not
        # have replaced the useful sentence with the vague one everywhere.
        (Path(state["PREVIEW_STATE"]) / "app.port").write_text("18997\n")
        with_port = _script("alive", "--name", "app", env=state)
        assert with_port.returncode == 8, with_port.stdout
        assert "anything answering is a stranger's answer" not in with_port.stdout
        assert "anything answering on :18997 is a stranger's answer" in with_port.stdout, (
            with_port.stdout
        )
    finally:
        _reap(victim.pid)


def test_alive_names_a_record_that_RAN_and_REFUSED(tmp_path: Path, state) -> None:
    """`record` exit 4 means the preview SPAWNED AND DIED, and it said "never started".

    `cmd_record` refuses with exit 4 on its two failure paths (the pid is not
    running, or its start time cannot be read) BEFORE it can write the `.booted`
    witness, so `alive` took the no-witness branch and printed "NEVER RECORDED —
    … the boot step never reached record". That is the OPPOSITE mechanism, and it
    is the single most common "the preview DIED" case: every boot step calls
    `record … || true` and `ci.yml` says in terms what exit 4 means ("wrangler
    already died, so there is nothing to record"). It points the reader at the
    boot wiring instead of at a process that spawned and exited — the same defect
    class as the bare `kill -0` this verb replaced (#11675 review, finding 1).

    RED on the pre-fix code: the refusal left the state dir EMPTY, so `alive`
    returned 6 and claimed "the boot step never reached record".
    """
    refused = _script("record", "--name", "app", "--pid", "999999", env=state)
    assert refused.returncode == 4, (
        f"the harness cannot measure: recording a dead pid must REFUSE (rc 4): {refused.stderr}"
    )

    result = _script("alive", "--name", "app", env=state)
    assert result.returncode == 10, (
        "a `record` that RAN and REFUSED was not given its own verdict — it was "
        f"folded into {result.returncode}: {result.stdout}"
    )
    assert "RECORD REFUSED" in result.stdout, result.stdout
    assert "STARTED AND DIED" in result.stdout, result.stdout
    # ⚠️ The trailer NAMES the alternative verdict ("A boot that never reached
    # record reports itself as NEVER RECORDED, not as this") on purpose — it is a
    # cross-reference, not a claim about THIS state — so the arm below matches the
    # CLAIM and the exit code, never a bare token that the cross-reference also
    # carries.
    assert "so the boot step never reached record" not in result.stdout, (
        "the verdict CLAIMS the opposite mechanism — the boot step DID reach record: "
        f"{result.stdout}"
    )
    assert "no pid was ever recorded for this name" not in result.stdout, result.stdout


def test_the_refusal_message_names_the_arm_the_SUT_recorded(tmp_path: Path, state) -> None:
    """#12039, the other half of the fix: a refusal must be ATTRIBUTABLE.

    `record` prints one line for every path, so a red on a required shard could not be
    read as "the fixture died" or "the SUT could not read the pid's start time". The
    SUT already answers this at refusal time — `cmd_record` writes
    `refused <pid> not-running|no-start-time` BEFORE either refusal fires — so the
    message reads THAT witness instead of inferring an arm from the pid's state
    afterwards (a later sample, which can invert the attribution: a decoy alive at
    refusal can be a zombie by the time the message is formatted).

    ⚠️ THE LIVENESS ARM IS NAMED BY CANDIDATES, NOT BY A CULPRIT — the witness records
    the VERDICT and none of the three legs that can produce it (ESRCH, EPERM, `Z`), and
    a dead decoy is *possibly a true positive* (#10283's machine-global reap, the defect
    that is then named). Calling it "the fixture's" would tell the next reader to close
    a real defect as flake.

    ⛔ THE LIVENESS WITNESS IS PRODUCED BY THE SUT, NOT WRITTEN BY HAND. The parser
    couples to `cmd_record`'s exact wording, and a fabricated string cannot notice the
    day that wording moves — every arm would silently degrade to "arm is unrecorded"
    and every run would stay green. Recording a dead pid is the real, cheap way to make
    the SUT emit it (rc 4, `refused <pid> not-running`).

    The START-TIME arm cannot be forced on demand (it needs `/proc` to lose a live pid
    between two reads), so that one witness IS fabricated and is therefore the weaker
    half of this test — said here rather than passed off as equal.

    The pid's current state is labelled an observation — never the explanation.
    """
    state_dir = Path(state["PREVIEW_STATE"])
    state_dir.mkdir(parents=True, exist_ok=True)
    dead = subprocess.CompletedProcess([], 4, "", "pid 4242 is not running — nothing recorded")

    sut_refusal = _script("record", "--name", "app", "--pid", "99999999", env=state)
    assert sut_refusal.returncode == 4, (
        f"the harness cannot measure: the SUT must refuse a dead pid (rc 4): {sut_refusal.stderr}"
    )
    # ⛔ NO POLL HAPPENED FOR THESE FOUR, so the message must not claim a deadline
    # (`timeout=0.0`): one result came from a single `record` invocation and three are
    # fabricated outright.
    liveness = _recorded(sut_refusal, 99999999, state, timeout=0.0)
    assert "LIVENESS leg refused" in liveness, (
        "the SUT's own witness no longer parses — its wording moved and every arm is now "
        f"attributed to nothing: {liveness}"
    )
    assert "the candidates and not a culprit" in liveness, liveness
    assert "#10283" in liveness, (
        "the liveness arm's message must name the REAL defect a dead decoy can be "
        f"(#10283's machine-global reap), not only the fixture: {liveness}"
    )
    # ⛔ NO POLL HAPPENED HERE, so the message must not claim a deadline. Pinned
    # positively — without this the `if timeout` guard could be dropped and every test
    # would still pass while the message claimed `for 0s` (measured before pinning).
    assert "refused pid 99999999:" in liveness, liveness
    assert "observed NOW" in liveness, liveness

    # ⛔ A witness for a DIFFERENT pid is not this refusal's arm. `cmd_record` writes
    # nothing on its `die`/`mkdir` paths, so an earlier refusal's witness can be the one
    # on disk — reading it unqualified reports a confident arm for a refusal that never
    # happened.
    (state_dir / "app.refused").write_text("refused 4242 not-running\n")
    stale = _recorded(dead, 4243, state, timeout=0.0)
    assert "names pid 4242" in stale and "arm is unrecorded for this pid" in stale, stale
    assert "LIVENESS leg refused" not in stale, stale

    (state_dir / "app.refused").write_text("refused 4242 no-start-time\n")
    starttime = _recorded(dead, 4242, state, timeout=0.0)
    assert "START-TIME leg refused" in starttime, starttime
    # The OTHER arm of the caller-deadline guard: a caller that really polled DOES name
    # its deadline. Pinned so "never claim one" cannot be implemented as "never state one".
    assert "refused pid 4242 for 10s:" in _recorded(dead, 4242, state, timeout=10.0)
    # ⛔ The reachable cause, and the one that is NOT — `proc_starttime` exits 0 with an
    # EMPTY start on a row it cannot parse, so `cmd_record` writes `<name>.start` and
    # never refuses; a message blaming a misread row points a reader at a SUT defect this
    # leg cannot produce. Pinned positively, because a message that merely omits the claim
    # is indistinguishable from one that never had it.
    assert "NOT THE SUT MISREADING A ROW" in starttime, (
        f"the start-time arm must rule out the row-misread cause: {starttime}"
    )
    assert "READ ITSELF failed" in starttime, (
        f"the start-time arm must name the failed read: {starttime}"
    )
    # …and it still does not BORROW the liveness arm's three-candidate wording: the two
    # arms stay tellable apart, which is the whole point of reading the witness.
    assert "the candidates and not a culprit" not in starttime, (
        f"the two arms collapsed into one wording: {starttime}"
    )

    (state_dir / "app.refused").unlink()
    unrecorded = _recorded(dead, 4242, state, timeout=0.0)
    assert "arm is unrecorded" in unrecorded, unrecorded

    # The current-state observation is a separate sentence and must not raise on a pid
    # that never existed, nor claim an arm from it. ⛔ Pinned on BOTH halves: every branch
    # of `_pid_now` starts with "kill -0 says", so a bare substring check on that prefix
    # is satisfied by ANY output — including one that wrongly reported it alive.
    now = _pid_now(99999999)
    assert "ESRCH" in now, now
    assert "LIVENESS" not in now and "START-TIME" not in now, (
        f"the observation asserted an arm — that is what `_refusal_arm` is for: {now}"
    )


def test_a_witness_is_located_by_the_SUTs_own_state_dir_when_PREVIEW_STATE_is_unset(
    tmp_path: Path, state
) -> None:
    """#12227 review cycle 5: "no witness was written" must not be a guess.

    `_refusal_arm` read `Path(env.get("PREVIEW_STATE", "")) / "<name>.refused"`, and
    `Path("")` is `.` — the TEST's cwd — while the SUT's `state_dir` falls back to
    `${RUNNER_TEMP:-${TMPDIR:-/tmp}}/tortoise-preview/<run>-<job>`. With the variable
    unset the helper therefore read a directory the SUT never writes to, and reported
    "no `app.refused` witness was written" about a witness `cmd_record` DID write —
    asserting a cause it had not measured, which is the inversion this helper exists to
    end, one layer down. The SUT answers the question itself (`dir`), so the helper now
    asks it instead of deriving a second copy of the rule.

    Latent rather than live: every call site in this file passes a `state` fixture with a
    non-empty `PREVIEW_STATE`, which is exactly why it is pinned here directly — no
    fixture-shaped test would ever reach the arm.
    """
    # ⛔ THE STATE ROOT COMES FROM THE SUT, not from a literal here. A test that spelled
    # `${RUNNER_TEMP}/tortoise-preview/<run>-<job>` itself would still pass against a
    # helper carrying the same second copy, and the drift between the two IS the defect.
    no_state = {k: v for k, v in state.items() if k != "PREVIEW_STATE"} | {
        "RUNNER_TEMP": str(tmp_path / "runner-temp"),
        "GITHUB_JOB": "dashboard-e2e",
    }
    where = _script("dir", env=no_state)
    assert where.returncode == 0 and where.stdout.strip(), (
        f"the harness cannot measure: the SUT must report a state dir: {where.stderr}"
    )
    state_dir = Path(where.stdout.strip())
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "app.refused").write_text("refused 4242 no-start-time\n")

    arm = _refusal_arm(no_state, "app", 4242)
    assert "START-TIME leg refused" in arm, (
        "the helper did not read the SUT's own state directory when `PREVIEW_STATE` was "
        f"unset — it reported about a directory the SUT never writes: {arm}"
    )
    assert "witness was written" not in arm, (
        f"the helper claimed no witness exists while one was on disk: {arm}"
    )


def test_the_record_poll_does_not_call_a_dead_pid_running(tmp_path: Path, state) -> None:
    """#12039: the poll buys the WINDOW, never a green for a pid that is really gone.

    `_record_when_running` retries `record`'s own predicate, so the hazard it must not
    introduce is the opposite direction: a bounded poll that eventually answers yes
    about a process that no longer exists (a false PASS, worse than the false red it
    replaces). The decoy is SIGNAL-killed and not `wait()`ed, so it is a ZOMBIE — which
    `pid_running` refuses by its `state != "Z"` leg (`kill -0` still succeeds on a
    zombie, so this pins that leg and not the ESRCH one, whose arm is the
    `--pid 999999` refusals above). Either way a refusal must leave the state directory
    without a pid witness.
    """
    victim = _spawn(["/bin/sleep", "60"], tmp_path)
    _wait_alive(victim.pid)
    _reap(victim.pid)
    deadline = time.time() + 10
    while time.time() < deadline and _pid_alive(victim.pid):
        time.sleep(0.02)
    assert not _pid_alive(victim.pid), "the harness cannot measure: the decoy survived its own kill"

    result = _record_when_running("app", victim.pid, env=state, timeout=1.0)
    assert result.returncode == 4, (
        "the poll turned a dead pid into a recorded one — "
        f"{_recorded(result, victim.pid, state, timeout=1.0)}"
    )
    assert not (Path(state["PREVIEW_STATE"]) / "app.pid").exists(), (
        "a refused record wrote a pid witness anyway"
    )


def test_the_record_poll_retries_a_refusal_until_the_sut_answers(tmp_path: Path, state, monkeypatch) -> None:
    """#12039's mechanism itself: the FIRST refusal is not the verdict.

    The mechanism this pins is the LOOP, not an environmental race: on this file's own
    account (`_record_when_running`) a live, signalable pid cannot be refused at all, so
    the window it absorbs is the START-TIME read — which cannot be forced on demand. It is
    therefore pinned by STANDING IN for it: the pid is alive and the SUT refuses exactly
    once. The poll must ask again and take the second answer. Without the retry this sees
    one call and a refusal — so deleting the loop cannot pass unnoticed, which is the
    failure mode a purely environmental reproducer would leave untested. ⚠️ It does NOT
    follow that the poll rescues a decoy that died: a refusal on the LIVENESS leg survives
    every retry, by design.
    """
    victim = _spawn(["/bin/sleep", "60"], tmp_path)
    _wait_alive(victim.pid)
    real = _script
    calls: list[tuple] = []

    def flaky(*args: str, env: dict[str, str], **kw) -> subprocess.CompletedProcess:
        calls.append(args)
        if len(calls) == 1:
            return subprocess.CompletedProcess(args, 4, "", "pid refused once (simulated window)")
        return real(*args, env=env, **kw)

    monkeypatch.setattr(sys.modules[__name__], "_script", flaky)
    try:
        result = _record_when_running("app", victim.pid, env=state, timeout=5.0)
        assert result.returncode == 0, _recorded(result, victim.pid, state, timeout=5.0)
        assert len(calls) >= 2, (
            f"the poll took the first answer as the verdict ({len(calls)} call(s)) — "
            "a single-shot probe is exactly the #12039 shape"
        )
    finally:
        _reap(victim.pid)


@pytest.mark.parametrize("which", ["pid", "start", "booted"])
def test_a_refusal_is_superseded_by_a_later_successful_record(
    tmp_path: Path, state, which: str
) -> None:
    """A refusal witness must not OUTLIVE the boot that later succeeded.

    The boot step's retry loop can refuse on attempt 1 and succeed on attempt 2,
    so a stale `.refused` would report a preview that DID boot as one that never
    could. Each state-dir artefact is checked separately because `alive` reads
    each of them on its own branch, and the refusal must lose to all three.
    """
    dead = _script("record", "--name", "app", "--pid", "999999", env=state)
    assert dead.returncode == 4, dead.stderr
    assert (Path(state["PREVIEW_STATE"]) / "app.refused").exists(), (
        "the harness cannot measure: a refused record must leave its witness"
    )

    victim = _spawn(["/bin/sleep", "120"], tmp_path)
    _wait_alive(victim.pid)
    try:
        recorded = _record_when_running("app", victim.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, victim.pid, state)
        assert not (Path(state["PREVIEW_STATE"]) / "app.refused").exists(), (
            "a successful record left the earlier refusal witness in place"
        )
        assert (Path(state["PREVIEW_STATE"]) / f"app.{which}").exists(), (
            f"the harness cannot measure: a successful record must write app.{which}"
        )
        assert _script("stop", "--name", "app", env=state).returncode == 0
        stopped = _script("alive", "--name", "app", env=state)
        assert stopped.returncode == 5, (
            "a preview that recorded successfully and was then stopped reports "
            f"itself as {stopped.returncode}, not as RECORDED-then-STOPPED: {stopped.stdout}"
        )
        assert "RECORDED, then STOPPED" in stopped.stdout, stopped.stdout
        assert "RECORD REFUSED" not in stopped.stdout, stopped.stdout
    finally:
        _reap(victim.pid)


def test_alive_tells_a_stopped_preview_from_one_that_never_started(
    tmp_path: Path, state
) -> None:
    """The third false-verdict path: `stop` deletes the pid file on EVERY branch.

    So "no pid file" is a double boot failure as often as it is a boot that never
    ran — and the pre-fix step asserted the opposite ("no pid was recorded at
    boot — it never started") for both. `record`'s boot witness is what separates
    them, and both states must be distinguishable.
    """
    never = _script("alive", "--name", "app", env=state)
    assert never.returncode == 6, never.stdout
    assert "NEVER RECORDED" in never.stdout, never.stdout

    victim = _spawn(["/bin/sleep", "120"], tmp_path)
    _wait_alive(victim.pid)
    try:
        recorded = _record_when_running("app", victim.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, victim.pid, state)
        assert _script("stop", "--name", "app", env=state).returncode == 0
        stopped = _script("alive", "--name", "app", env=state)
        assert stopped.returncode == 5, (
            "a preview that WAS recorded and then stopped is reported the same as one "
            f"that never started: {stopped.stdout}{stopped.stderr}"
        )
        assert "RECORDED, then STOPPED" in stopped.stdout, stopped.stdout
        assert "NEVER RECORDED" not in stopped.stdout, stopped.stdout
        assert "does NOT mean the preview never started" in stopped.stdout, stopped.stdout
    finally:
        _reap(victim.pid)


def test_alive_separates_a_serving_preview_from_a_surviving_one(
    tmp_path: Path, state
) -> None:
    """The PROCESS surviving and the SERVER serving are two facts, not one.

    The middle verdict the `ci.yml` step used to carry must not be lost when the
    branch collapses into one helper call: a live wrangler whose bundle/workerd is
    not answering is a preview that did NOT die and did NOT serve, and the log is
    read differently for each. Exit 0 is reserved for alive-AND-serving so the
    step's `::error::` prefix lands on both failure shapes.
    """
    result = _script("alloc", "--name", "app", "--base", "18988", env=state)
    assert result.returncode == 0, result.stderr
    port = int(result.stdout.strip())

    broken = _spawn_http(port, 500, tmp_path)
    healthy = None
    try:
        assert _wait_port(port), f"the decoy listener never bound :{port}"
        recorded = _record_when_running("app", broken.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, broken.pid, state)
        result = _script("alive", "--name", "app", env=state)
        assert result.returncode == 7, (
            "an alive process whose port answers 500 was reported as a preview that "
            f"served: {result.stdout}{result.stderr}"
        )
        assert "is ALIVE" in result.stdout and "did not serve" in result.stdout, result.stdout
        assert "SURVIVED" not in result.stdout, result.stdout

        _reap(broken.pid)
        deadline = time.time() + 10
        while time.time() < deadline and _port_answers(port):
            time.sleep(0.05)

        healthy = _spawn_http(port, 200, tmp_path)
        assert _wait_port(port), f"the healthy listener never bound :{port}"
        recorded = _record_when_running("app", healthy.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, healthy.pid, state)
        result = _script("alive", "--name", "app", env=state)
        assert result.returncode == 0, (
            f"a serving, surviving preview was not reported whole: {result.stdout}{result.stderr}"
        )
        assert "SURVIVED" in result.stdout, result.stdout
    finally:
        _reap(broken.pid, *([healthy.pid] if healthy is not None else []))


def test_alive_does_not_claim_an_identity_it_could_not_check(tmp_path: Path, state) -> None:
    """A verdict may not assert a fact the probe never took.

    Identity is checked only against the start-time witness `record` writes. If
    that file is gone (a hand-written pid file, a truncated state dir) the verb
    still has a liveness answer — and must say WHICH answer it has, because the
    same class of overclaim is what this whole finding is about: the step asserted
    "not a dead server" from a probe that had not asked.
    """
    victim = _spawn(["/bin/sleep", "120"], tmp_path)
    _wait_alive(victim.pid)
    try:
        recorded = _record_when_running("app", victim.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, victim.pid, state)
        (Path(state["PREVIEW_STATE"]) / "app.start").unlink()
        result = _script("alive", "--name", "app", env=state)
        # 9, not 0: no port was recorded for this name, so the SERVING leg was not
        # taken either and `0` ("alive AND serving") is not this verb's to return —
        # its own arm,
        # `test_alive_refuses_to_call_an_unchecked_port_leg_a_serving_verdict`.
        assert result.returncode == 9, result.stdout + result.stderr
        assert "IDENTITY was NOT checked" in result.stdout, (
            "`alive` claims the start time matches the boot witness even though no "
            f"witness exists: {result.stdout}"
        )
        assert "still matches the boot witness" not in result.stdout, result.stdout
    finally:
        _reap(victim.pid)


def test_alive_does_not_claim_an_identity_it_could_not_READ(tmp_path: Path, state) -> None:
    """A witness that EXISTS but cannot be re-read is still not a match.

    The sibling arm below deletes `.start`, so it only covers *no witness*. The
    harder state is a witness that IS there and a start-time read that FAILS —
    the process exited in the window between `alive`'s liveness probe and its
    start-time probe, or the host could not answer (psutil documents the
    pid + creation-time rule as *inherently racy*, so that window is designed
    for). The pre-fix code branched the sentence on the WITNESS
    (`[[ -n "$recorded" ]]`) instead of on the comparison, so it printed *"its
    start time still matches the boot witness"* for a comparison it never ran —
    measured on this arm's exact state.

    The capability gap is simulated the way #5049 describes it — an `awk` that
    cannot answer, first on `PATH` — so the script under test is the REAL file,
    unmodified. RED on the pre-fix code (verified by running it): the sentence
    claimed a match.
    """
    blind = tmp_path / "blind-bin"
    blind.mkdir()
    # BOTH read paths, because the gap must be simulated where the helper reads:
    # Linux goes `/proc` + `awk`, a host without `/proc` (a developer's macOS — the
    # branch `pid_state`/`pid_starttime` ship for) goes through `ps`. A blind `awk`
    # alone is a NO-OP there, so this arm false-redded on macOS — "`alive` claims
    # the start time matches" — for a capability the platform never uses. Blinding
    # both keeps the arm about the STATE (a witness that cannot be re-read)
    # instead of about one implementation.
    for tool in ("awk", "ps"):
        (blind / tool).write_text("#!/bin/sh\nexit 1\n")
        (blind / tool).chmod(0o755)

    port = _script("alloc", "--name", "app", "--base", "18789", env=state)
    assert port.returncode == 0, port.stderr
    victim = _spawn(["/bin/sleep", "120"], tmp_path)
    _wait_alive(victim.pid)
    try:
        recorded = _record_when_running("app", victim.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, victim.pid, state)
        # Positive control: with a working `awk` the same state DOES report the
        # match, so the arm below cannot pass for the wrong reason (no match was
        # ever expected here).
        control = _script("alive", "--name", "app", env=state)
        assert control.returncode == 7, control.stdout + control.stderr
        assert "still matches the boot witness" in control.stdout, control.stdout

        result = _script(
            "alive", "--name", "app",
            env={**state, "PATH": f"{blind}:{state['PATH']}"},
        )
        assert "still matches the boot witness" not in result.stdout, (
            "`alive` claims the start time matches the boot witness although the "
            f"read that would have compared them never ran: {result.stdout}"
        )
        assert "its start time could NOT be read" in result.stdout, result.stdout
        assert "IDENTITY was NOT checked" in result.stdout, result.stdout
        # The liveness answer DOES stand — a host gap degrades the read, never a
        # verdict (#5049), so the process is still reported alive.
        assert " is ALIVE" in result.stdout, result.stdout
        assert result.returncode == 7, result.stdout + result.stderr
    finally:
        _reap(victim.pid)


def test_alive_does_not_borrow_an_unchecked_identity_for_a_serving_verdict(
    tmp_path: Path, state
) -> None:
    """Serving + an UNREADABLE witness: exit 0, but the verdict may not say SURVIVED.

    The header's old `iff` said exit 0 required the start-time witness to be
    re-read AND to match, while `cmd_alive` fell through to 0 when the re-read
    FAILED and the port answered — the block a CI author reads first asserted an
    invariant the code did not have, and the message spent it: *"this preview
    SURVIVED the run, so the red is the specs' or the content's, not a dead
    server"* is a CAUSAL verdict, drawn from an identity leg the probe had not
    taken (`#11675` review, finding 3). The state is designed for, not exotic: the
    pid + creation-time rule is documented as inherently racy, so a process can
    exit between the liveness probe and the start-time probe.

    Exit 0 STAYS on this state — a non-zero would mark a live, serving preview
    dead on a box that cannot read `/proc`, which is a false red — so what this
    pins is the claim: alive-and-serving, with the identity leg named as
    unchecked, and no SURVIVED verdict riding on it.

    RED on the pre-fix code (measured): it printed SURVIVED and claimed the red
    was not a dead server.
    """
    blind = tmp_path / "blind-bin"
    blind.mkdir()
    (blind / "awk").write_text("#!/bin/sh\nexit 1\n")
    (blind / "awk").chmod(0o755)

    allocated = _script("alloc", "--name", "app", "--base", "18679", env=state)
    assert allocated.returncode == 0, allocated.stderr
    port = int(allocated.stdout.strip())

    victim = _spawn(["/bin/sleep", "120"], tmp_path)
    _wait_alive(victim.pid)
    server = _spawn_http(port, 200, tmp_path)
    try:
        assert _wait_port(port), f"the listener never bound :{port}"
        recorded = _record_when_running("app", victim.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, victim.pid, state)
        result = _script(
            "alive", "--name", "app",
            env={**state, "PATH": f"{blind}:{state['PATH']}"},
        )
        assert result.returncode == 0, (
            "an alive, serving preview must stay exit 0 even when the identity leg "
            f"could not be taken: {result.stdout}{result.stderr}"
        )
        assert "IDENTITY was NOT checked" in result.stdout, result.stdout
        assert "SURVIVED" not in result.stdout, (
            "`alive` graded the preview SURVIVED from a start-time leg it never "
            f"took: {result.stdout}"
        )
        assert "ALIVE AND SERVING" in result.stdout, result.stdout
    finally:
        _reap(victim.pid, server.pid)


def test_alive_refuses_to_call_an_unchecked_port_leg_a_serving_verdict(
    tmp_path: Path, state
) -> None:
    """`0` means "alive AND serving"; a leg that was NOT taken may not return it.

    The contract block said `exit 0 iff … AND the port still answers HTTP`, while
    the no-port branch returned 0 — a claim broader than the code, in the block a
    CI author reads first (the #11675 review's P3). `alloc` always precedes
    `record` in the workflow, so this is not a live CI defect; it is the same
    class of overclaim the rest of this verb exists to remove, and the fix gives
    the state its own code so the contract's `iff` is true.

    RED on the pre-fix code (measured): it returned 0 and called itself ALIVE.
    """
    victim = _spawn(["/bin/sleep", "120"], tmp_path)
    _wait_alive(victim.pid)
    try:
        recorded = _record_when_running("app", victim.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, victim.pid, state)
        assert not (Path(state["PREVIEW_STATE"]) / "app.port").exists(), (
            "the harness cannot measure: `record` must not have written a port"
        )
        result = _script("alive", "--name", "app", env=state)
        assert result.returncode == 9, (
            "no port was recorded, so SERVING was never checked, yet `alive` "
            f"returned its alive-and-serving code: {result.stdout}{result.stderr}"
        )
        assert "SERVING was NOT checked" in result.stdout, result.stdout
        assert "SURVIVED the run" not in result.stdout, result.stdout
    finally:
        _reap(victim.pid)


def test_both_exit_code_lists_name_every_cause_their_sentences_produce():
    """The two exit-code lists are PROSE, and nothing pinned them, so they drifted
    from the code twice in one review (`#11675` rounds 13 and 15).

    Two clauses are checked, because the same drift happened in both: `9` (four causes)
    and `12` (three). `cmd_alive` returns `9` for four on-disk facts, and its sentence
    names each: no
    port file; a file that is EMPTY; a file holding content that is not a port number
    (which is where a whitespace-only file lands — `$(cat …)` strips only trailing
    newlines, so `"   "` survives as a non-empty non-number and used to reach exit 7);
    and a file whose content could not be read. The header table and the
    `cmd_alive` list each enumerate those causes independently; both have now been
    wrong in both directions, and the in-code list even says it "must not drift from"
    the header table — which is exactly what happened. The `12` clause had the same
    defect: both lists said the pid file "could not be read" while the code also
    returns `12` for a READABLE file that is empty or holds a non-number, and its own
    message already says "unreadable, empty, or not numeric".

    ⛔ Each check is scoped to the `9 …` CLAUSE, not the whole comment block, because
    the same block also carries `12 the pid FILE itself could not be read`: a
    block-wide `"could not be read" in block` is satisfied by the pid-file clause, so
    deleting the CAUSE from the port clause stayed green (measured). The clauses are
    separated by the lists' own `·`, so the segment is the unit.
    """
    blocks = []
    current: list[str] = []
    for line in SCRIPT.read_text().splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            # Drop the comment MARKER as well as the newline: a phrase this pin
            # requires ("not numeric") can straddle a line wrap, and leaving `#` in
            # would make it read "not # numeric" (measured).
            current.append(re.sub(r"^#+\s?", "", stripped))
            continue
        if current:
            blocks.append(" ".join(current))
            current = []
    if current:
        blocks.append(" ".join(current))
    port_lists = [b for b in blocks if "no port is known" in b.lower()]
    assert len(port_lists) == 2, (
        "expected exactly two exit-code lists naming the no-port state (the header "
        f"table and `cmd_alive`'s), found {len(port_lists)} — a list that moved, "
        "merged or vanished makes this pin vacuous instead of red")
    for block in port_lists:
        clauses = [c for c in block.split("\u00b7") if "no port is known" in c.lower()]
        assert len(clauses) == 1, (
            "the no-port state must be described by exactly one `·`-separated clause "
            f"of an exit-code list, found {len(clauses)}: {block[:300]}")
        clause = clauses[0].lower()
        for cause in ("empty", "not a port number", "could not be read"):
            assert cause in clause, (
                f"an exit-code list enumerates the no-port causes without naming "
                f"{cause!r}, which `cmd_alive` does produce — a reader auditing the "
                "code from the list alone is told about fewer causes than exist: "
                + clause[:400])
        assert "none" in clause or "no port was recorded" in clause, (
            "an exit-code list omits the ABSENT cause (no port file at all), which is "
            "the one `alloc`-never-ran state the whole code exists to report: "
            + clause[:400])

        # …and the SAME class one clause away: `12` is returned for an unreadable pid
        # file, an EMPTY one, and one whose content is not a number — as the code's own
        # `PID FILE UNREADABLE` message says. Both lists said only "could not be read",
        # which is one of three and the wrong one for the other two.
        pid_clauses = [c for c in block.split("\u00b7") if "no pid could be read" in c.lower()]
        assert len(pid_clauses) == 1, (
            "the pid-unreadable state must be described by exactly one `\u00b7`-separated "
            f"clause of an exit-code list, found {len(pid_clauses)}: {block[:300]}")
        pid_clause = pid_clauses[0].lower()
        for cause in ("unreadable", "empty", "not numeric"):
            assert cause in pid_clause, (
                f"an exit-code list enumerates the `12` causes without naming "
                f"{cause!r}, which the code produces and its own message states — a "
                "reader is sent to a permissions hunt for a file that was read fine: "
                + pid_clause[:400])


def test_a_later_refusal_outranks_an_earlier_boot(tmp_path: Path, state) -> None:
    """The retry loop can SUCCEED, be STOPPED, and then be REFUSED.

    `record` writes `.booted` on success and `.refused` when the process it was
    handed was already gone; `stop` deletes the pid file and keeps both. Checking
    `.booted` first reported that state as "RECORDED, then STOPPED" and discarded
    the one datum that says the LAST spawn STARTED AND DIED — the mechanism the
    `.refused` witness was added to surface. The later witness wins, and a tie
    goes to the refusal (it is the more specific statement about the attempt that
    explains the red).

    RED on the pre-fix code (**verified by running it**): rc 5 for this sequence.
    """
    victim = _spawn(["/bin/sleep", "120"], tmp_path)
    _wait_alive(victim.pid)
    try:
        recorded = _record_when_running("app", victim.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, victim.pid, state)
        assert _script("stop", "--name", "app", env=state).returncode == 0
        refused = _script("record", "--name", "app", "--pid", "999999", env=state)
        assert refused.returncode == 4, refused.stderr
        state_dir = Path(state["PREVIEW_STATE"])
        both_witnesses = (
            (state_dir / "app.booted").exists()
            and (state_dir / "app.refused").exists()
        )
        assert both_witnesses, (
            "the harness cannot measure: this state needs BOTH witnesses on disk")
        result = _script("alive", "--name", "app", env=state)
        assert result.returncode == 10, (
            "the LAST attempt was a refusal, but `alive` reported "
            f"{result.returncode}: {result.stdout}{result.stderr}"
        )
        assert "RECORD REFUSED" in result.stdout, result.stdout
        assert "EARLIER attempt DID record" in result.stdout, (
            "the message must keep BOTH facts — a later refusal AND an earlier "
            f"successful boot: {result.stdout}"
        )
    finally:
        _reap(victim.pid)


def test_an_unreadable_pid_file_is_not_the_gone_verdict(tmp_path: Path, state) -> None:
    """A pid file this probe cannot READ is not evidence that the preview DIED.

    Under `set -euo pipefail` the unguarded `cat` exited 1 with EMPTY stdout, so
    the step printed a blank `::error::` and the code said GONE. The dump took no
    verdict at all, and the recorded process may well be alive — every sibling
    read in `cmd_alive` already carried the `|| true` this one lacked.

    Two unreadable shapes, because they fail on DIFFERENT hosts: a file with no
    pid in it (deterministic everywhere), and a mode-000 file, which only denies
    the read when the process is NOT root — root holds `CAP_DAC_OVERRIDE` and
    reads it anyway, so that half skips rather than redding a correct helper
    (the convention `test_e2e_wrangler_prewarm.py:839` already uses).

    RED on the pre-fix code (**verified by running it**): the mode-000 shape gave
    rc 1 with no output, and the empty shape gave rc 1 wearing the GONE code.
    """
    victim = _spawn(["/bin/sleep", "120"], tmp_path)
    _wait_alive(victim.pid)
    pfile = Path(state["PREVIEW_STATE"]) / "app.pid"
    try:
        recorded = _record_when_running("app", victim.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, victim.pid, state)

        pfile.write_text("")
        empty = _script("alive", "--name", "app", env=state)
        assert empty.returncode == 12, (
            "a pid file with no pid in it measured nothing, so `alive` must not "
            f"answer with a state verdict: {empty.returncode} "
            f"{empty.stdout}{empty.stderr}"
        )
        assert "PID FILE UNREADABLE" in empty.stdout, empty.stdout
        assert " is GONE" not in empty.stdout, (
            f"the empty pid file was reported as the gone verdict: {empty.stdout}")

        if os.geteuid() == 0:
            pytest.skip(
                "root ignores the file mode this arm needs to make the read fail")
        pfile.write_text(str(victim.pid))
        pfile.chmod(0o000)
        result = _script("alive", "--name", "app", env=state)
        assert result.returncode == 12, (
            "the pid file could not be read, so no verdict was taken — `alive` "
            f"must not answer with the GONE code: {result.returncode} "
            f"{result.stdout}{result.stderr}"
        )
        assert "PID FILE UNREADABLE" in result.stdout, (
            "a blank `::error::` is the failure this arm exists to catch: "
            f"{result.stdout!r} / {result.stderr!r}"
        )
        assert " is GONE" not in result.stdout, (
            "the unreadable state must not be REPORTED as the gone verdict: "
            f"{result.stdout}")
    finally:
        with contextlib.suppress(OSError):
            pfile.chmod(0o644)
        _reap(victim.pid)


def test_the_no_port_verdict_does_not_borrow_an_untaken_identity_leg(
    tmp_path: Path, state
) -> None:
    """Exit 9 reports liveness only, so its SENTENCE may not claim identity.

    `0` and `7` were re-worded when the identity leg was not taken; this branch
    was missed, and it printed "our recorded process" in the same clause that
    admitted the identity was never checked. Nothing pinned either sentence, so a
    revert to the single-printf form left the whole file green (measured).

    Also pins the ABSENT-vs-unreadable distinction for the two files this branch
    reads: a `.start`/`.port` that EXISTS but cannot be read may not be reported
    as one that was never written — the same conflation `12` exists to prevent
    for the pid file.
    """
    victim = _spawn(["/bin/sleep", "120"], tmp_path)
    _wait_alive(victim.pid)
    state_dir = Path(state["PREVIEW_STATE"])
    try:
        recorded = _record_when_running("app", victim.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, victim.pid, state)

        # The matched-witness shape keeps the claim: positive control, and it also
        # pins the FIRST exit-9 sentence's port leg (an empty port file must not be
        # reported as one that was never recorded) — the second sentence is pinned
        # further down, so neither printf can regress unobserved.
        (state_dir / "app.port").write_text("")
        verified = _script("alive", "--name", "app", env=state)
        assert verified.returncode == 9, verified.stdout + verified.stderr
        assert "our recorded process" in verified.stdout, (
            "the positive control lost the verified claim: " + verified.stdout)
        assert "port file EXISTS" in verified.stdout, (
            "a readable-but-empty port file was reported as one that was never "
            "recorded, in the MATCHED-witness sentence: " + verified.stdout)
        assert "is EMPTY" in verified.stdout, (
            "the EMPTY case must be NAMED as such, not merged into 'could not be "
            "read' — the other cause, with a different remedy (a permissions or "
            "torn-state hunt), so one sentence for both sends the reader to the "
            "wrong one: " + verified.stdout)
        (state_dir / "app.port").unlink()

        # No witness at all: the verdict may say the leg was not taken…
        (state_dir / "app.start").unlink()
        absent = _script("alive", "--name", "app", env=state)
        assert absent.returncode == 9, absent.stdout + absent.stderr
        assert "no start-time witness was recorded" in absent.stdout, absent.stdout
        assert "our recorded process" not in absent.stdout, (
            "an untaken identity leg was still reported as a verified one: "
            + absent.stdout)

        # …and a witness that EXISTS but cannot be read must not be called absent.
        (state_dir / "app.start").write_text("")
        empty = _script("alive", "--name", "app", env=state)
        assert "EXISTS" in empty.stdout and "could not be read (unreadable, or empty)" in empty.stdout, (
            "an empty witness was reported without its own cause (unreadable vs "
            "empty): " + empty.stdout)
        assert "no start-time witness was recorded" not in empty.stdout, empty.stdout
        assert "our recorded process" not in empty.stdout, empty.stdout

        # Same rule for the port file, and it is THREE states: absent, EXISTS with
        # no port VALUE (`-f`, and `! -s` or `-r` — the read succeeded and returned
        # nothing, which a whitespace-only file also does once command substitution
        # strips the newlines), and EXISTS with content that could not be read
        # (`-s` AND `! -r`: `-s` alone is a size test that passes on a file this uid
        # cannot read, so it does not by itself establish "could not be read").
        # Collapsing the last two is the same conflation in the other direction, and
        # the RECYCLED branch (exit 8) computes its port clause from the same value,
        # so it used to name an absence it never observed.
        (state_dir / "app.port").write_text("")
        port_empty = _script("alive", "--name", "app", env=state)
        assert port_empty.returncode == 9, port_empty.stdout + port_empty.stderr
        assert "port file EXISTS" in port_empty.stdout and "is EMPTY" in port_empty.stdout, (
            "an empty port file was reported as one that was never recorded, or as "
            "one that could not be read: "
            + port_empty.stdout)
        assert "no port was recorded" not in port_empty.stdout, port_empty.stdout

        # ⛔ …and the THIRD state has its own leg, because a sentence nobody drives
        # is the shape this whole arm exists to catch (measured: a mutant emitting
        # the no-value sentence for an unreadable file left this file green).
        # Root ignores the mode this state needs, so skip there — the sibling
        # root-skip above is the same reason.
        if os.geteuid() != 0:
            (state_dir / "app.port").write_text("4999\n")
            (state_dir / "app.port").chmod(0o000)
            try:
                unreadable = _script("alive", "--name", "app", env=state)
                assert unreadable.returncode == 9, unreadable.stdout + unreadable.stderr
                assert "with content but could not be read" in unreadable.stdout, (
                    "an unreadable port file was NOT named as unreadable: "
                    + unreadable.stdout)
                assert "is EMPTY" not in unreadable.stdout and "not a port number" not in unreadable.stdout, (
                    "an unreadable port file was reported as one with no value: "
                    + unreadable.stdout)
            finally:
                (state_dir / "app.port").chmod(0o644)
            (state_dir / "app.port").write_text("")

        # …and a file that is NOT EMPTY but yields no port must not be called
        # unreadable when it was read fine (whitespace only): `-s` is true there.
        (state_dir / "app.port").write_text("\n\n")
        whitespace = _script("alive", "--name", "app", env=state)
        assert whitespace.returncode == 9, whitespace.stdout + whitespace.stderr
        assert "with content but could not be read" not in whitespace.stdout, (
            "a readable whitespace-only port file was reported as unreadable: "
            + whitespace.stdout)

        # ⛔ …and NON-NEWLINE whitespace is not a port at all: `$(cat …)` strips only
        # TRAILING NEWLINES (bash §3.5.4), so `"   "` survives the substitution as a
        # non-empty value and used to be spliced into " on :   " and handed to
        # `serves_now` — measured: exit 7, "does not answer an HTTP success", i.e. a
        # SERVING verdict taken against a value that is not a port. Same for a tab and
        # for "abc". All three now take the no-port path and say why.
        for garbage in ("   ", "\tabc", "abc\n"):
            (state_dir / "app.port").write_text(garbage)
            bogus = _script("alive", "--name", "app", env=state)
            assert bogus.returncode == 9, (
                f"a port file holding {garbage!r} was treated as a port and put the "
                "SERVING leg to a probe against it: " + bogus.stdout)
            assert "not a port number" in bogus.stdout, (
                f"a port file holding {garbage!r} was not named as a non-number: "
                + bogus.stdout)
            assert " on :" not in bogus.stdout, (
                "a non-port value was spliced into the sentence as a port: "
                + bogus.stdout)
        (state_dir / "app.port").write_text("")

        # …and the RECYCLED (exit 8) sentence derives its port clause from the
        # SAME value, so it may not say "no port was recorded" over a port file
        # that EXISTS. Forced by stamping a witness that no longer matches the live
        # start time — the same technique `test_the_recycled_verdict_...` uses.
        (state_dir / "app.start").write_text("1\n")
        recycled = _script("alive", "--name", "app", env=state)
        assert recycled.returncode == 8, recycled.stdout + recycled.stderr
        assert "no port was recorded for this name" not in recycled.stdout, (
            "the RECYCLED verdict named a port absence it did not observe: "
            + recycled.stdout)
        assert "is EMPTY" in recycled.stdout, (
            "the RECYCLED verdict did not carry the three-way port clause: "
            + recycled.stdout)
    finally:
        _reap(victim.pid)


def test_ready_refuses_a_witness_that_no_longer_describes_the_recorded_pid(
    tmp_path: Path, state
) -> None:
    """The boot gate's identity leg is a BEHAVIOUR change, and nothing pinned it.

    `cmd_ready`'s (a) leg was replaced by `pid_is_ours` — which can now refuse a
    preview that a bare `kill -0` accepted (a zombie, or a recycled pid beside a
    peer's listener: the #2757 shape one step out). Reverting that call to
    `kill -0 "$pid"` reddened NOTHING in the diff, so the guard's value could not
    vary under any mutation for the reader who assumed it was covered.

    RED on that mutant (**verified by applying it**): the refusal arm below then
    returns 0. Two arms are needed for it to be discriminating — the untouched
    witness is the positive control, and the port still SERVES 200, so the refusal
    can only come from the identity leg (a dead port refuses under either
    primitive).
    """
    port = _script("alloc", "--name", "app", "--base", "18801", env=state)
    assert port.returncode == 0, port.stderr
    listener = _spawn_http(int(port.stdout.strip()), 200, tmp_path)
    try:
        assert _wait_port(int(port.stdout.strip())), "the decoy listener never bound"
        recorded = _record_when_running("app", listener.pid, env=state)
        assert recorded.returncode == 0, _recorded(recorded, listener.pid, state)
        healthy = _script("ready", "--name", "app", env=state)
        assert healthy.returncode == 0, (
            "the positive control failed: our own recorded listener serves 200 and "
            f"was still reported NOT ready — {healthy.stdout}{healthy.stderr}"
        )

        # The witness now describes a DIFFERENT process; the listener still serves.
        (Path(state["PREVIEW_STATE"]) / "app.start").write_text("1\n")
        recycled = _script("ready", "--name", "app", env=state)
        assert recycled.returncode == 1, (
            "a recorded pid whose boot witness no longer matches was reported READY "
            "while the port answered 200 — a recycled pid beside a serving listener "
            f"passes the boot gate: {recycled.stdout}{recycled.stderr}"
        )
    finally:
        _reap(listener.pid)


def test_alive_takes_its_liveness_and_identity_from_the_guarded_predicates() -> None:
    """The `ci.yml` arm only pins the CALL; the semantics must be pinned here.

    A later edit that swapped `pid_running` for a bare `kill -0`, or dropped the
    start-time witness, would re-open the zombie and pid-reuse false-ALIVE paths
    while the workflow arm stayed green — so the mechanism is pinned where it
    lives, next to the boot witness that makes "stopped" distinguishable.
    """
    text = SCRIPT.read_text()
    body = _function_body(text, "cmd_alive")
    assert body, "`.github/scripts/preview-server.sh` has no `cmd_alive` function"
    # ⛔ EXECUTABLE lines only for the POSITIVE pins: the function's own prose names
    # `pid_running` and the boot witness, so a comment satisfied a body-substring
    # assertion and a mutant that DELETED the liveness block passed the whole file
    # (measured: `# Zombies answer kill(0) forever: liveness is `pid_running`…`
    # alone kept this arm green, while the helper returned ALIVE-and-SERVING for a
    # process that had already exited). `code_only` is built here, before the
    # assertions, not after them for the negative arms.
    code_only = "\n".join(_cut_comment(line) for line in body.splitlines())
    assert "pid_running" in code_only, (
        "`alive` no longer CALLS the zombie-aware predicate `record`/`stop` use, so an "
        "unreaped preview reads as a surviving one (naming it in a comment is not "
        "calling it):\n" + body
    )
    assert "pid_identity" in code_only, (
        "`alive` no longer takes its identity from the shared predicate `record` and "
        "`ready` use, so one question has two answers again (the drift that made it "
        "print a comparison it never ran):\n" + body
    )
    assert not re.search(r"\bpid_starttime\b", code_only), (
        "`alive` re-derives the start-time comparison instead of reading the shared "
        "predicate's answer, so a witness it could not READ can be reported as a "
        "match (finding 1 of the #11675 review):\n" + body
    )
    assert not re.search(r"\bkill\s+-0\b", code_only), (
        "`alive` decides liveness with a bare `kill -0`, which a zombie answers "
        "forever:\n" + body
    )
    assert "booted" in code_only, (
        "`alive` cannot tell a preview that was recorded and then STOPPED from one "
        "that never started (that is what `record`'s boot witness is for):\n" + body
    )
    assert re.search(r"^\s*alive\)\s*cmd_alive", text, re.MULTILINE), (
        "`alive` is not dispatched from `main`, so every arm above is unreachable "
        "from the workflow that calls it"
    )


def test_the_helper_carries_no_process_name_kill() -> None:
    """The mechanism itself must stay scoped — a `pkill` inside the helper is the bug."""
    text = SCRIPT.read_text()
    offenders = [
        f"{lineno}: {line.strip()}"
        for lineno, line in enumerate(text.splitlines(), start=1)
        if re.search(r"\b(?:pkill|killall)\b", line) and not line.lstrip().startswith("#")
    ]
    assert not offenders, (
        "the preview helper signals processes by name again; it must only ever signal "
        "the pid it recorded and that pid's descendants (#10283):\n" + "\n".join(offenders)
    )


# ── #10305: the CONSUMER's readiness budget ───────────────────────────────────
# The arms above pin `preview-server.sh`'s `ready` verb. #10305 is the other end
# of the same contract: the specs' own preflight (`tests/e2e/preview_preflight.py`)
# re-probes both origins before any spec drives them, and it used to sample ONCE.
# Measured (run 41141786500 job 124361929728): auth `ready` at 23:21:53.99, the
# dashboard preview booted beside it at 23:21:56.09, and the single preflight
# probe at 23:21:59.36 caught a 500 — the job reddened with "local preview
# server(s) unreachable" over a server that had served 200 four seconds earlier,
# while run 41140572622 (head `b3bbf2f1c`, byte-identical `website/`, `tests/e2e/`
# and `.github/`) ran the same suite GREEN. A budget is the fix; these arms pin
# that it is a BUDGET (retries, then still refuses) and not a blanket pass.


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc) -> None:
        return None


class _StatusScript:
    """An `opener` that answers a scripted sequence of statuses, per URL.

    A status >= 400 is raised the way `urllib` raises it (`HTTPError`), because
    that is what the real probe sees for the measured failure — a 500 from
    wrangler is an exception, not a response object.
    """

    def __init__(self, script: dict[str, list[int]], default: int = 200) -> None:
        self.script = {url: list(codes) for url, codes in script.items()}
        self.default = default
        self.calls: list[str] = []

    def open(self, url: str, timeout: float | None = None):
        self.calls.append(url)
        codes = self.script.get(url) or []
        status = codes.pop(0) if codes else self.default
        if status >= 400:
            raise urllib.error.HTTPError(url, status, "Internal Server Error", {}, None)
        return _FakeResponse(status)


class _Clock:
    """A monotonic stand-in that advances only when the poll sleeps."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step
        self.sleeps = 0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += max(self.step, seconds)


def _preflight(**kwargs):
    from tests.e2e.preview_preflight import preflight_local_servers
    return preflight_local_servers(**kwargs)


def test_preflight_polls_through_a_transient_5xx() -> None:
    """A single 500 must not declare the previews unreachable (#10305).

    The measured failure is exactly this: one 500 from a preview that had
    answered 200 seconds earlier. On the pre-fix gate (one sample, then
    `pytest.exit`) this test cannot even reach its assertion — the call raises,
    which is the non-vacuity proof for this arm.
    """
    auth = "http://127.0.0.1:8836/"
    app = "http://127.0.0.1:8791/"
    opener = _StatusScript({auth: [500, 500]})
    clock = _Clock(step=0.5)

    _preflight(targets=(("auth", auth), ("dashboard", app)), budget=20.0,
               interval=0.5, opener=opener, sleep=clock.sleep,
               monotonic=clock.monotonic)

    assert opener.calls == [auth, app, auth, auth], (
        "round 1 probes BOTH origins, then only the one still failing is "
        f"re-probed: {opener.calls}"
    )
    assert clock.sleeps == 2, (
        "two transient 500s must cost two polls, not a refusal: "
        f"sleeps={clock.sleeps}"
    )


def test_preflight_still_refuses_once_the_budget_is_spent() -> None:
    """A server that never serves still reds — the budget is not a blanket pass.

    The positive-control mirror of the arm above: without it, a gate that
    retried forever would satisfy `test_preflight_polls_through_a_transient_5xx`.
    """
    auth = "http://127.0.0.1:8836/"
    opener = _StatusScript({auth: [500] * 500})
    clock = _Clock(step=0.5)

    with pytest.raises(pytest.exit.Exception) as excinfo:
        _preflight(targets=(("auth", auth),), budget=3.0, interval=0.5,
                   opener=opener, sleep=clock.sleep, monotonic=clock.monotonic)

    message = str(excinfo.value)
    assert "500" in message, message
    assert "auth http://127.0.0.1:8836/" in message, message
    assert "Polled for 3s" in message, (
        "the refusal must say it was a BUDGET that ran out, so the next reader "
        f"does not re-diagnose it as a one-shot miss: {message}"
    )
    assert len(opener.calls) > 1, (
        "a refusal after ONE sample is the pre-fix behaviour this fixes: "
        f"calls={len(opener.calls)}"
    )


def test_the_refusal_keeps_the_first_observation_and_counts_every_distinct_one() -> None:
    """A refusal must name BOTH ends of the failure, and count them all (#10305).

    `_probe` renders a status failure as `HTTPError: HTTP Error <code>: ...`, so
    six distinct codes are six distinct observations. The retained list is
    capped at `MAX_TRAIL`, and the pre-fix trim (`del trail[:-MAX_TRAIL]`) cut
    from the FRONT — deleting exactly the opening 500 that
    `MAX_TRAIL`'s own comment says the trail exists to preserve, so the message
    led with a later problem as if it were the first and reported "4 distinct
    observations" for six.

    Non-vacuity: on the pre-fix source this message leads with `HTTP Error 503`
    (the first sample, 500, is gone) and says "4", so both count assertions red.
    """
    from tests.e2e.preview_preflight import MAX_TRAIL

    auth = "http://127.0.0.1:8836/"
    opener = _StatusScript({auth: [500, 502, 503, 504, 505, 506]}, default=500)
    clock = _Clock(step=0.5)

    with pytest.raises(pytest.exit.Exception) as excinfo:
        _preflight(targets=(("auth", auth),), budget=2.5, interval=0.5,
                   opener=opener, sleep=clock.sleep, monotonic=clock.monotonic)

    message = str(excinfo.value)
    assert "HTTP Error 500:" in message, (
        "the refusal must still name the FIRST sample: a preview that 500s from "
        "the start and later fails differently reads as a different fault "
        f"otherwise, which is the #10305 misreading: {message}"
    )
    assert "HTTP Error 506:" in message, (
        f"the refusal must name the MOST RECENT sample too: {message}"
    )
    assert "6 distinct observations" in message, (
        "the count is every distinct problem seen, never the retained-list "
        f"cap (MAX_TRAIL={MAX_TRAIL}): {message}"
    )
    assert message.index("HTTP Error 500:") < message.index("HTTP Error 506:"), (
        f"the first sample must still read as first: {message}"
    )


def test_a_serving_preview_costs_exactly_one_probe_and_no_sleep() -> None:
    """The happy path stays fail-FAST — the budget must not slow a green job.

    Positive control for the two arms above: a gate that always waited the full
    budget would pass them both and add 20 s to every CI run.
    """
    auth = "http://127.0.0.1:8836/"
    app = "http://127.0.0.1:8791/"
    opener = _StatusScript({})
    clock = _Clock(step=0.5)

    _preflight(targets=(("auth", auth), ("dashboard", app)), budget=20.0,
               interval=0.5, opener=opener, sleep=clock.sleep,
               monotonic=clock.monotonic)

    assert opener.calls == [auth, app], opener.calls
    assert clock.sleeps == 0, f"a serving preview must not be waited on: {clock.sleeps}"


def test_preflight_refuses_a_non_loopback_origin_without_burning_the_budget() -> None:
    """A refusal polling cannot fix must not be retried (#2731 review P2).

    The loopback rule is request-INDEPENDENT: re-probing `10.0.0.5` twenty times
    cannot make it local, so it refuses on the first evaluation and the opener is
    never called. Without this arm a poll could paper over a misconfigured origin
    (the guard would still fire, but only after the budget — and the message
    would imply the far end answered something).
    """
    opener = _StatusScript({})
    clock = _Clock(step=0.5)

    with pytest.raises(pytest.exit.Exception) as excinfo:
        _preflight(targets=(("auth", "http://10.0.0.5:8788/"),), budget=20.0,
                   interval=0.5, opener=opener, sleep=clock.sleep,
                   monotonic=clock.monotonic)

    message = str(excinfo.value)
    assert "non-loopback host" in message, message
    assert opener.calls == [], (
        f"a non-local origin must not be probed at all: {opener.calls}"
    )
    assert clock.sleeps == 0, clock.sleeps


# ── #10305: the fix's own CI WIRING, pinned so it cannot silently unwind ──────
# The two halves of the #10305 fix are only useful if CI keeps running them: a
# polled preflight that the job SKIPS is not a fix (#4221's inversion — a
# skipped job reports green), and a diagnostic step that no longer exists is
# how the next red is undiagnosable again. Both are `ci.yml` wiring, so both are
# pinned here — the file that already pins this job's preview machinery.


def _changes_gate_source() -> str:
    """The `changes` job's inline python gate — the per-surface path filters."""
    steps = _load_workflow("ci.yml")["jobs"]["changes"]["steps"]
    gate = [step for step in steps if step.get("id") == "gate"]
    assert len(gate) == 1, f"expected exactly one `id: gate` step, got {len(gate)}"
    return gate[0]["run"]


def _any_file_entry(source: str, key: str) -> str:
    """The argument list of `"<key>": any_file( … )` in the gate source.

    Keyed on the ENTRY, never on a line range: a sibling entry that grows or
    shrinks must not be able to change this answer, and `dashboard_e2e` is a
    multi-line call whose arguments are exactly what is under test.
    """
    match = re.search(
        rf'^(\s*)"{re.escape(key)}": any_file\(\n(.*?)^\1\),',
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"no `{key}: any_file(...)` entry in the ci.yml `changes` gate"
    return match.group(2)


def test_the_dashboard_e2e_gate_names_the_shared_preflight_module() -> None:
    """A change to the shared readiness gate must RE-RUN the job, not skip it.

    `tests/e2e/preview_preflight.py` (#10305) is the readiness gate every spec
    the job runs calls — `test_keys_table_mixed`, `test_graphs_management`,
    `test_ship_test_onboarding`, `test_dashboard_onboarding`, and the flow
    module that exports `_preflight_local_servers`. The `dashboard_e2e` path
    gate names those SPECS, which does not cover a file it had never seen: a PR
    touching only the new module would leave the job `skipped` — reported as
    Success — so the suite that would catch a broken gate never runs on the
    change that breaks it (#4221 is this exact inversion).
    """
    entry = _any_file_entry(_changes_gate_source(), "dashboard_e2e")
    named = set(re.findall(r'"([^"]+)"', entry))

    assert "tests/e2e/preview_preflight.py" in named, (
        "the `dashboard_e2e` change gate does not name "
        "tests/e2e/preview_preflight.py, so a PR touching ONLY the shared "
        "preflight skips the job that exercises it (#4221's inversion).\n"
        f"named: {sorted(named)}"
    )
    # The module is named as a PATH, not as prose: an `isfile(...)` argument is
    # what the gate actually evaluates, and a mention inside a comment (which
    # this file's own `_cut_comment` rationale would not even see) changes
    # nothing about which jobs run.
    assert re.search(r'isfile\([^)]*"tests/e2e/preview_preflight\.py"', entry), (
        "tests/e2e/preview_preflight.py is mentioned in the `dashboard_e2e` "
        "gate but not as an `isfile(...)` argument, so it gates nothing"
    )


@pytest.mark.parametrize(
    "job_name,servers",
    [
        # The `ci.yml` jobs that boot `wrangler pages dev` previews, run specs
        # against them, AND carry the diagnostic step. They are pinned TOGETHER
        # because the step was written for one and not the others, and that
        # asymmetry is exactly the defect this arm closed: #10305 added it to
        # `dashboard-e2e`, and `legal-e2e` — same boot steps, same helper, same
        # failure mode — kept red-ing undiagnosably until #11675. `welcome-e2e`
        # is the THIRD such job (`PREVIEW_JOBS`, above, and
        # `preview-server.sh`'s own header) and it is NOT in this list because it
        # still has no dump step — the same asymmetry, still open there and
        # recorded on #10283. Registering it here without the step would red this
        # pin, which is the correct outcome, not a reason to omit it from the
        # comment.
        ("dashboard-e2e", ("auth", "app")),
        ("legal-e2e", ("marketing", "app")),
    ],
)
def test_a_preflight_red_dumps_the_preview_servers_own_logs(
    job_name: str, servers: tuple[str, ...]
) -> None:
    """A preflight red is undiagnosable unless the servers' logs are read.

    The measured failure (#10305, job 124361929728) reddened on
    `auth http://127.0.0.1:8836/ -> HTTPError: HTTP Error 500`, and nothing on
    the runner ever read `auth.log` — which records every request wrangler
    answered, `[wrangler:info] GET / 500` included. The two boot steps `cat`
    their log only when the BOOT fails, which is the opposite case. This pins
    the diagnostic step: it exists, it runs on FAILURE, and it reads the logs
    through the same per-job state directory the boot wrote to (`dir`), never a
    shared `/tmp/wrangler*.log` path.

    #11675 added the second parametrisation after the SAME absence reddened the
    required `legal-e2e` check on PR #11675 itself (job 129650228746): `:8808`
    was ready at 05:42:03Z, 11 app-origin specs then failed
    `ERR_CONNECTION_REFUSED`, and the teardown printed `app (pid 983187) already
    exited` while the marketing preview was still RUNNING — a preview that DIED
    MID-RUN, whose own log went unread, so the red could not be diagnosed after
    the fact. `legal-e2e` boots the same two previews through the same helper and
    had no such step, from #10305 until now.
    """
    job = _load_workflow("ci.yml")["jobs"][job_name]
    # Selected by SHAPE, not by name: the two boot steps also call
    # `preview-server.sh … dir` and `tail` a log (their OWN, and only when the
    # boot fails). A step that dumps BOTH servers' logs is the only one that
    # iterates them.
    dumps = [
        step
        for step in job["steps"]
        if "preview-server.sh" in str(step.get("run", ""))
        and " dir" in str(step.get("run", ""))
        and "tail" in str(step.get("run", ""))
        and "for name in" in str(step.get("run", ""))
    ]
    assert len(dumps) == 1, (
        "expected exactly one step that dumps the preview logs on failure, "
        f"found {[step.get('name') for step in dumps]}"
    )
    step = dumps[0]
    assert step.get("if") == "failure()", (
        "the log dump must be `if: failure()` — on success it is noise, and on "
        f"any other condition it does not run for the red it exists for: {step.get('if')!r}"
    )
    run = str(step["run"])
    for name in servers:
        assert name in run, f"the dump does not name the {name} log: {run}"
    assert "/tmp/wrangler" not in run, (
        "the dump reads a shared /tmp path; the boot writes to the per-job "
        f"state dir `preview-server.sh dir` (#10283): {run}"
    )
    # The LIVENESS verdict (#11675). A log alone leaves the reader guessing
    # whether the server died or the specs were wrong, and both misreadings are
    # measured: #10305 read a 500 as unreachability, #8059 read a dead preview as
    # a `--d1`/`dist` problem, and PR #11675's own red (job 129650228746) read a
    # mid-run death as 11 app-origin spec failures.
    #
    # ⛔ A `#` COMMENT MUST NOT SATISFY A GATE (#11675). `run` is the whole
    # shell blob, prose included, so a plain `in run` is satisfied by a comment
    # that merely NAMES the call — the regression this assertion exists for
    # (deleting the probe, leaving `# was: bash "$PREVIEW" alive …` behind) has
    # been measured green. `_cut_comment` is this file's own answer to that.
    code = "\n".join(_cut_comment(line) for line in run.splitlines())
    gate = 'bash "$PREVIEW" alive --name "$name"'
    assert gate in code, (
        "the dump does not ask `preview-server.sh alive` whether OUR recorded "
        "process survived, so it cannot say a preview DIED mid-run: "
        f"{code}"
    )
    # ⛔ `kill -0` IS THE PRIMITIVE THIS REPLACED — it must not come back here.
    # A zombie answers kill(0) forever and a recycled pid belongs to a stranger,
    # so a step that branches on it can print a categorical ALIVE over a preview
    # that is gone and then assert the CAUSE of the red. `alive` applies
    # `pid_running` (zombie test) AND the boot-time start witness (the same
    # PID-REUSE GUARD `stop` applies); `test_alive_*` below pin that it really
    # does. One call per server, so the two jobs cannot drift.
    assert "kill -0" not in code, (
        "the dump decides liveness with a bare `kill -0`, which a ZOMBIE answers "
        "forever and a RECYCLED pid answers for a different process — call "
        f"`preview-server.sh alive` instead: {code}"
    )
    # The verdict PROSE lives in `cmd_alive` alone. It was once copy-pasted into
    # both jobs (verbatim, 25 lines each), which is two definitions of "the
    # preview survived" — the second one drifts. The step prints the helper's own
    # line and decides only whether it is an annotation.
    for verdict in ("ALIVE", "DIED MID-RUN"):
        assert verdict not in run, (
            f"the step restates the {verdict!r} verdict instead of printing '"
            "`preview-server.sh alive`'s own message — that is a second "
            f"definition of it: {run}"
        )
    # ⛔ THE ORDERING IS THE WHOLE VERDICT. `stop-all` deletes each `<name>.pid`
    # (`preview-server.sh` `cmd_stop`), so a dump that runs AFTER the teardown
    # finds no pid file at all and missays every preview as unstarted — while
    # every assertion above still passes, because they only read the dump's own
    # text. Pin the position instead of trusting it.
    names = [str(s.get("name", "")) for s in job["steps"]]
    dump_at = names.index(str(step.get("name", "")))
    teardown_at = next(
        (i for i, s in enumerate(job["steps"])
         if "preview-server.sh" in str(s.get("run", ""))
         and "stop-all" in str(s.get("run", ""))),
        None,
    )
    assert teardown_at is not None, (
        f"no `preview-server.sh stop-all` teardown in {job_name}: {names}"
    )
    assert dump_at < teardown_at, (
        "the log dump runs AFTER the preview teardown, which deletes the pid "
        "files the dump reads — every preview would then be reported as never "
        f"having started: dump at {dump_at}, teardown at {teardown_at} in {names}"
    )
