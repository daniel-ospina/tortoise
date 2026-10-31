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
alive, (b) the port is bound, and (c) that listener answers HTTP successfully.
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
        # Already-exited decoys are the normal case here: the point is that the
        # helper killed them, not that we did. SIM105's `suppress` is the idiom.
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 9)


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
        assert _script(
            "record", "--name", "app", "--pid", str(broken.pid), env=state
        ).returncode == 0
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
        assert _script(
            "record", "--name", "app", "--pid", str(healthy.pid), env=state
        ).returncode == 0
        result = _script("ready", "--name", "app", env=state)
        assert result.returncode == 0, (
            f"a serving 200 preview on :{port} was reported NOT ready — the boot loop "
            f"would spin 240 s and red the job: {result.stderr}"
        )
    finally:
        _reap(broken.pid, *([healthy.pid] if healthy is not None else []))


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

    def __enter__(self) -> "_FakeResponse":
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
