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
image does not guarantee, so `ready` asserts only (a) the pid we recorded is
still alive and (b) the port answers — strictly more than the pre-fix bare
`curl`, which related the two not at all. The remaining window needs our
process to survive a failed BIND while a peer listens on the same port; the
allocator's pre-boot free-check plus wrangler's exit on `EADDRINUSE` are what
close it in practice.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
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
    """RUNNING — not merely present. A zombie is a reaped-to-be child, i.e. dead."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return False
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


def _spawn(cmd: list[str], cwd: Path) -> subprocess.Popen:
    """A detached process, like `wrangler pages dev` under a runner step."""
    return subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def _wait_alive(pid: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _pid_alive(pid):
            return
        time.sleep(0.05)
    pytest.fail(f"decoy pid {pid} never came alive — the harness cannot measure")


def _reap(*pids: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


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


# ── 2. the mechanism: stop() is pid-scoped, and it refuses a recycled pid ───

def test_stop_all_leaves_a_peer_jobs_decoy_workerd_alone(tmp_path: Path, state) -> None:
    """#10283's own indicator (a): job A's cleanup leaves job B's decoy alive.

    The decoy is a process whose `comm` is literally `workerd` — the name the
    pre-fix teardown matched with `pkill workerd` — belonging to a DIFFERENT
    job's state directory.
    """
    assert SCRIPT.is_file(), f"missing {SCRIPT}"

    # Job B: a `workerd`-named decoy, registered in ITS OWN state directory.
    peer_bin = tmp_path / "bin"
    peer_bin.mkdir()
    peer_workerd = peer_bin / "workerd"
    peer_workerd.symlink_to("/bin/sleep")
    peer_state = dict(state, PREVIEW_STATE=str(tmp_path / "peer-preview"), GITHUB_JOB="dashboard-e2e")
    peer = _spawn([str(peer_workerd), "120"], tmp_path)
    _wait_alive(peer.pid)
    assert (peer_bin / "workerd").exists()
    assert _script("record", "--name", "app", "--pid", str(peer.pid), env=peer_state).returncode == 0

    # Job A: a real tree (a supervisor + two children, like wrangler + workerd).
    own = _spawn(["bash", "-c", "sleep 120 & sleep 120"], tmp_path)
    _wait_alive(own.pid)
    deadline = time.time() + 5
    while time.time() < deadline and len(_child_pids(own.pid)) < 2:
        time.sleep(0.05)
    children = _child_pids(own.pid)
    assert len(children) == 2, f"the tree did not form (children={children})"

    assert _script("record", "--name", "app", "--pid", str(own.pid), env=state).returncode == 0
    result = _script("stop-all", env=state)
    try:
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
        _reap(own.pid, *children, peer.pid)


def test_stop_refuses_a_pid_whose_start_time_changed(tmp_path: Path, state) -> None:
    """A recycled pid is not our server: refusing is fail-safe, signalling is not."""
    victim = _spawn(["/bin/sleep", "120"], tmp_path)
    _wait_alive(victim.pid)
    try:
        assert _script("record", "--name", "app", "--pid", str(victim.pid), env=state).returncode == 0
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
