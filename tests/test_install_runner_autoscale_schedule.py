"""Hermetic tests for tools/install-runner-autoscale-schedule.sh (#9685 review round 9, P1-1).

The installer is the WIRING, and the PR's own thesis is that "a manual script nobody calls is this
fleet's most repeated defect" — so the wiring is exactly the file that must not be able to rot back
into an inert enforcer with a green suite. It is also the most host-mutating file in the PR: it
writes `sudo -n tee` unit files, edits the user's crontab and bootstraps a live LaunchAgent. The
sibling installers each carry a suite of this shape (`tests/test_install_reaper_schedule.py`,
`tests/test_install_queue_reconcile_schedule.py`).

⛔ These tests NEVER execute the real install path against the host. `launchctl` addresses its
domain BY UID (not by `$HOME`), so a throwaway HOME is not a sandbox — the sibling suite records
two review runs on this host that re-pointed the live agent at a deleted temp plist. Every external
tool is stubbed on `PATH` and every install tree points into `tmp_path`, so what gets asserted is
the script's own DECISIONS: the refusals (with nothing written), the rendered artifacts, the
measurement probe, and the fixed-string cron rewrite.

Each test whose docstring carries a ``Mutation:`` line names the edit that turns it RED.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "install-runner-autoscale-schedule.sh"

LABEL = "runner-autoscale"
PLIST_NAME = "com.tortoise.runner-autoscale.plist"

_ENV_LEAKS = (
    "AGENTS_DIR",
    "SYSTEMD_UNIT_DIR",
    "AUTOSCALE_INSTALL_DIR",
    "AUTOSCALE_LOG",
    "AUTOSCALE_PYTHON",
    "AUTOSCALE_PYTHON_CANDIDATES",
    "AUTOSCALE_REPO_ROOT",
    "AUTOSCALE_SHA256",
    "AUTOSCALE_PLATFORM",
    "AUTOSCALE_HAS_SYSTEMD",
    "AUTOSCALE_HAS_CRON",
    "AUTOSCALE_ALLOW_WORKTREE",
    "AUTOSCALE_SKIP_PROBE",
    "RUNNER_AUTOSCALE_INTERVAL",
    "RUNNER_AUTOSCALE_REPO",
    "RUNNER_AUTOSCALE_STATE_DIR",
)


def _write_stub(bindir: Path, name: str, body: str) -> Path:
    path = bindir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _sandbox(
    tmp_path: Path, uname: str = "Darwin", *, cron: bool = True, env_overrides: dict | None = None
) -> dict:
    """Throwaway repo/HOME/log + PATH stubs. Nothing here can touch the host.

    ⛔ `env_overrides` is for the installer's PROBE seams (`AUTOSCALE_HAS_SYSTEMD` /
    `AUTOSCALE_HAS_CRON`), and a case that exercises a NON-default scheduler branch must use
    those rather than `AUTOSCALE_PLATFORM`. Both are seams, but only the probes leave
    `platform()`'s real `case "$(uname -s)"` dispatch under test: `AUTOSCALE_PLATFORM`
    short-circuits `platform()` before the dispatch, so the branch it NAMES is covered and the
    `Linux)` arm is not. Measured in review of PR #9685: on the `AUTOSCALE_PLATFORM` seam, two
    mutations of the Linux arm (deleting `elif has_cron`, reordering the probes) survived a
    fully green suite.

    The branch also has to be FORCED rather than inherited from the box — `has_systemd` reads
    `/run/systemd/system` and `command -v systemctl`, so an unforced Linux+cron case renders a
    systemd unit on a systemd cell and calls it a pass. That was the original defect: 4 cases
    RED in `test (a)/(b)/(e)/(i)` on Linux, green only on the macOS box they were written on.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True)
    # `$SYSTEMD_UNIT_DIR` must EXIST, exactly as /etc/systemd/system does on every real systemd
    # box. Without it `sudo tee` has nowhere to write, and the `sudo` stub below used to `exit 0`
    # regardless — so the install reported "installed" having written nothing, and the fixture
    # silently agreed. A sandbox that is missing a directory the real box always has is a false
    # green, not a shortcut (round 11 review).
    (tmp_path / "units").mkdir(parents=True, exist_ok=True)
    home = tmp_path / "home"
    home.mkdir()
    logdir = tmp_path / "log"
    logdir.mkdir()
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    # A copy of the real tool so the installer's TOOL check resolves; the schedule never runs it
    # (the pinned interpreter is a stub below), and the install-time probe is a stub too.
    (repo / "tools" / "runner_autoscale.py").write_text("# placeholder\n", encoding="utf-8")
    # `<root>/.git` = a plain checkout, i.e. NOT a linked worktree.
    (repo / ".git").mkdir()

    _write_stub(bindir, "uname", f"#!/usr/bin/env bash\necho '{uname}'\n")
    _write_stub(
        bindir,
        "launchctl",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "launchctl $*" >> "$STUB_LOG/launchctl.log"\n'
        '[ "${1:-}" = "list" ] && echo "501 0 com.tortoise.runner-autoscale"\n'
        "exit 0\n",
    )
    _write_stub(bindir, "plutil", "#!/usr/bin/env bash\nexit 0\n")
    _write_stub(
        bindir,
        "id",
        '#!/usr/bin/env bash\ncase "$1" in -u) echo 501 ;; -un) echo "tester" ;; *) echo 501 ;; esac\n',
    )
    if cron:
        _write_stub(
            bindir,
            "crontab",
            "#!/usr/bin/env bash\n"
            'STORE="$STUB_LOG/crontab.txt"\n'
            'if [ "${1:-}" = "-l" ]; then [ -f "$STORE" ] && cat "$STORE"; exit 0; fi\n'
            # ⛔ write-then-rename: the installer pipelines `crontab -l` INTO `crontab -`, so a stub
            # that truncated STORE on open would empty the file before the left side read it (a
            # fixture race, not a product bug — the real crontab installs a temp copy atomically).
            'if [ "${1:-}" = "-" ]; then cat > "$STORE.new"; mv "$STORE.new" "$STORE"; exit 0; fi\n'
            "exit 0\n",
        )
        _write_stub(bindir, "cron", "#!/usr/bin/env bash\nexit 0\n")
    # `sha256sum` is deliberately NOT stubbed (stock macOS has no such binary) while `shasum` is —
    # the platform split the drift check has to survive. On Linux the real sha256sum wins the
    # resolver instead; either way the comparison must be a real one.
    _write_stub(
        bindir,
        "shasum",
        "#!/usr/bin/env bash\n"
        'f="${3:-${1:-}}"\n'
        'printf "%s  %s\\n" "$(wc -c < "$f" | tr -d " ")" "$f"\n',
    )
    # A pinned interpreter that claims to be new enough (the floor is tested on its own).
    _write_stub(
        bindir,
        "py312",
        '#!/usr/bin/env bash\nif [ "$1" = "-c" ]; then exit 0; fi\nexit 0\n',
    )

    env = dict(os.environ)
    for key in _ENV_LEAKS:
        env.pop(key, None)
    env.update(
        {
            "PATH": f"{bindir}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "HOME": str(home),
            "STUB_LOG": str(logdir),
            "AUTOSCALE_REPO_ROOT": str(repo),
            "AUTOSCALE_PYTHON": str(bindir / "py312"),
            "AUTOSCALE_INSTALL_DIR": str(tmp_path / "install"),
            "AUTOSCALE_LOG": str(logdir / "job.log"),
            "AGENTS_DIR": str(tmp_path / "agents"),
            "SYSTEMD_UNIT_DIR": str(tmp_path / "units"),
        }
    )
    if env_overrides:
        env.update(env_overrides)
    return {
        "env": env,
        "bin": bindir,
        "home": home,
        "log": logdir,
        "repo": repo,
        "install": tmp_path / "install",
        "agents": tmp_path / "agents",
        "units": tmp_path / "units",
        "launchctl_log": logdir / "launchctl.log",
        "crontab_store": logdir / "crontab.txt",
    }


def _run(sb: dict, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env if env is not None else sb["env"],
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )


def _systemd_stubs(sb: dict, *, active: str = "active") -> None:
    _write_stub(
        sb["bin"],
        "systemctl",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "systemctl $*" >> "$STUB_LOG/systemctl.log"\n'
        'case "$1" in\n'
        '  is-active) echo "' + active + '" ;; is-enabled) echo enabled ;;\n'
        "  *) exit 0 ;;\n"
        "esac\nexit 0\n",
    )
    _write_stub(
        sb["bin"],
        "sudo",
        "#!/usr/bin/env bash\n"
        '[ "${1:-}" = "-n" ] && shift\n'
        'printf "%s\\n" "sudo $*" >> "$STUB_LOG/sudo.log"\n'
        'case "$1" in cat) shift; [ -f "$1" ] && cat "$1"; exit 0 ;; esac\n'
        # `exit $?` — NOT `exit 0`: a tee whose write fails must fail the pipeline, because the
        # installer treats a non-zero tee as "could not install the unit" and exits 1. Swallowing
        # it made an unwritable unit directory look like a successful install.
        'case "$1" in tee) shift; cat > "$1"; exit $? ;; esac\n'
        'exec "$@"\n',
    )


# ── refusals: nothing is written, and the reason is named ────────────────────────────────────
def test_unsupported_platform_is_refused_with_nothing_written(tmp_path):
    """An unsupported platform is refused (exit 2) and NOTHING is written: no scheduler is
    improvised, and the runnable copy is not refreshed first (it used to be — VGATE round 1).

    Mutation: move the platform gate below `install -m 0755` -> the run-copy lands -> RED.
    """
    sb = _sandbox(tmp_path, uname="SunOS", cron=False)
    res = _run(sb)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "REFUSED" in res.stderr, res.stderr
    assert not (sb["install"] / "runner_autoscale.py").exists(), "refusal wrote the run-copy"
    assert not sb["launchctl_log"].exists(), "refusal called launchctl"


def test_linked_worktree_is_refused_before_anything_is_written(tmp_path):
    """A schedule pinned to a linked worktree installs cleanly and then fails forever, because the
    worktree is reaped with its PR.

    Mutation: drop the `_common` worktree check -> rc 0 and the run-copy installed -> RED.
    """
    sb = _sandbox(tmp_path)
    main = sb["repo"] / "main"
    linked = main / ".worktrees" / "br"
    linked.mkdir(parents=True)
    (main / ".git").mkdir()
    (linked / ".git").write_text("gitdir: /nowhere\n", encoding="utf-8")
    _write_stub(
        sb["bin"],
        "git",
        f'#!/usr/bin/env bash\ncase "$*" in *"git-common-dir"*) echo "{main}/.git" ;; esac\nexit 0\n',
    )
    res = _run(sb, env=dict(sb["env"], AUTOSCALE_REPO_ROOT=str(linked)))
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "linked worktree" in res.stderr, res.stderr
    assert not (sb["install"] / "runner_autoscale.py").exists(), "refusal wrote the run-copy"
    assert not sb["launchctl_log"].exists(), "refusal called launchctl"


def test_bad_interval_is_refused(tmp_path):
    """A non-integer or too-tight interval is a usage error, never a silently defaulted schedule."""
    sb = _sandbox(tmp_path)
    res = _run(sb, env=dict(sb["env"], RUNNER_AUTOSCALE_INTERVAL="10m"))
    assert res.returncode == 2 and "RUNNER_AUTOSCALE_INTERVAL" in res.stderr, (
        res.stdout,
        res.stderr,
    )
    res = _run(sb, env=dict(sb["env"], RUNNER_AUTOSCALE_INTERVAL="30"))
    assert res.returncode == 2 and ">= 60" in res.stderr, (res.stdout, res.stderr)


def test_too_old_interpreter_is_refused(tmp_path):
    """A <3.12 pin installs a schedule whose every fire is a named refusal — refuse at install.

    Mutation: drop the floor probe -> the install proceeds with an interpreter the tool refuses
    -> RED.
    """
    sb = _sandbox(tmp_path)
    old = _write_stub(
        sb["bin"], "py39", '#!/usr/bin/env bash\nif [ "$1" = "-c" ]; then exit 1; fi\nexit 0\n'
    )
    res = _run(sb, env=dict(sb["env"], AUTOSCALE_PYTHON=str(old)))
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "3.12" in res.stderr and "REFUSED" in res.stderr, res.stderr
    assert not (sb["install"] / "runner_autoscale.py").exists(), "refusal wrote the run-copy"


def test_the_interpreter_is_the_first_candidate_that_meets_the_floor(tmp_path):
    """The chain must pick by the FLOOR, not by `-x`, or the .venv branch is unreachable dead code.

    `[ -x /usr/bin/python3 ]` is true on essentially every box (on macOS it is the CLT's 3.9), so
    the old chain never reached `$REPO/.venv/bin/python` and then refused at the floor check — a
    machine with a perfectly good 3.12 one line below could not install at all (round 9, finding 3).

    Mutation: `if [ -x "$_cand" ]; then PYTHON="$_cand"; break; fi` (first EXECUTABLE wins) ->
    the 3.9 candidate is pinned -> `python=<old>` -> RED.
    """
    sb = _sandbox(tmp_path)
    old = _write_stub(
        sb["bin"], "py39", '#!/usr/bin/env bash\nif [ "$1" = "-c" ]; then exit 1; fi\nexit 0\n'
    )
    good = sb["bin"] / "py312"
    res = _run(
        sb,
        "--dry-run",
        env=dict(
            sb["env"],
            AUTOSCALE_PYTHON="",
            AUTOSCALE_PYTHON_CANDIDATES=f"{old}\n{good}\n",
        ),
    )
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert f"python={good}" in res.stdout, res.stdout
    assert f"python={old}" not in res.stdout, res.stdout


# ── the install-time measurement probe: "installed" must mean "it will do something" ─────────
def test_an_unmeasurable_box_is_refused_before_anything_is_written(tmp_path):
    """A schedule armed where the guard cannot MEASURE fires every INTERVAL into exit 2 forever.

    `--status` alone cannot settle this (it only proves the unit/plist is loaded), so the installer
    runs ONE read-only pass with the pinned interpreter and refuses on the tool's own UNMEASURABLE
    verdict (exit 2) — before `mkdir`/`install`, so the box is untouched.

    Mutation: drop the probe block -> rc 0, the run-copy is installed, the plist is written -> RED.
    """
    sb = _sandbox(tmp_path)
    refusing = _write_stub(
        sb["bin"],
        "py-unmeasurable",
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        '  *--status*) echo \'{"verdict": "unmeasurable", "error": "gh is not on PATH"}\'; exit 2 ;;\n'
        "  *) exit 0 ;;\n"
        "esac\n",
    )
    res = _run(sb, env=dict(sb["env"], AUTOSCALE_PYTHON=str(refusing)))
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "cannot MEASURE" in res.stderr, res.stderr
    assert "gh is not on PATH" in res.stderr, res.stderr
    assert not (sb["install"] / "runner_autoscale.py").exists(), "refusal wrote the run-copy"
    assert not (sb["agents"] / PLIST_NAME).exists(), "refusal wrote the plist"

    # …and the escape hatch really arms it.
    res = _run(sb, env=dict(sb["env"], AUTOSCALE_PYTHON=str(refusing), AUTOSCALE_SKIP_PROBE="1"))
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert (sb["agents"] / PLIST_NAME).exists(), res.stdout


# ── the rendered artifacts ───────────────────────────────────────────────────────────────────
def test_launchd_plist_pins_home_state_path_and_gh(tmp_path):
    """launchd's default PATH has no /opt/homebrew/bin, so `gh` is not found and EVERY fire exits 2
    "gh is not on PATH": installed, reported "installed", and inert forever (round 9, finding 2).

    Mutation: drop the PATH (or RUNNER_AUTOSCALE_GH) key from render_plist -> RED.
    """
    sb = _sandbox(tmp_path)
    res = _run(sb, "--dry-run")
    assert res.returncode == 0, (res.stdout, res.stderr)
    plist = res.stdout
    assert "<key>PATH</key>" in plist, plist
    assert "<key>RUNNER_AUTOSCALE_GH</key>" in plist, plist
    assert "<key>HOME</key>" in plist and "<key>RUNNER_AUTOSCALE_STATE_DIR</key>" in plist, plist
    assert f"<string>{sb['env']['AUTOSCALE_LOG']}</string>" in plist, plist
    assert "<key>StartInterval</key><integer>600</integer>" in plist, plist


def test_launchd_install_writes_a_lintable_plist_and_verifies_it_is_loaded(tmp_path):
    """Install writes, lints, and bootstraps the agent.

    ⛔ The LOAD VERIFICATION is pinned by its own case below
    (`test_launchd_install_fails_when_the_agent_is_not_loaded`), not here. This docstring used to
    claim "drop the `launchctl list | grep` verification -> RED", which was FALSE: the stub always
    lists the label, so dropping the check left every assertion here green (round 11 review, P2).
    A mutation claim nobody executes is a comment nobody re-reads.
    """
    sb = _sandbox(tmp_path)
    res = _run(sb)
    assert res.returncode == 0, (res.stdout, res.stderr)
    plist = sb["agents"] / PLIST_NAME
    assert plist.exists(), res.stdout
    body = plist.read_text(encoding="utf-8")
    assert "<key>PATH</key>" in body and "<key>RUNNER_AUTOSCALE_GH</key>" in body, body
    assert "bootstrap" in sb["launchctl_log"].read_text(encoding="utf-8")


def test_launchd_install_fails_when_the_agent_is_not_loaded(tmp_path):
    """⛔ The load check is the ONLY thing between "wrote a plist" and "a job that will run".

    A `launchctl bootstrap` that returns 0 is not proof the label is registered: a GUI session that
    is not the caller's, a disabled login item, or a `bootout` racing the bootstrap all leave the
    plist on disk and nothing loaded. Without the verification the installer reports success for an
    agent that never fires (the inert-enforcer shape this file exists to refuse).

    Mutation: drop the `launchctl list | grep` verification -> this install exits 0 -> RED.
    """
    sb = _sandbox(tmp_path)
    # a `launchctl` that records the call and NEVER lists the label: whatever we bootstrap, the
    # agent is not loaded.
    _write_stub(
        sb["bin"],
        "launchctl",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "launchctl $*" >> "$STUB_LOG/launchctl.log"\n'
        "exit 0\n",
    )
    res = _run(sb)
    assert res.returncode == 1, (res.stdout, res.stderr)
    assert "not loaded" in res.stderr, res.stderr


def test_launchd_rearm_check_is_not_fooled_by_a_large_job_table(tmp_path):
    """⛔ `launchctl list | grep -qF label` + `set -o pipefail` = a FALSE "not loaded" on a real Mac.

    `grep -q` exits at the FIRST match, so a still-writing `launchctl list` gets SIGPIPE and the
    pipeline reports 141; `!` then reads "the agent is not loaded" while it is. A real
    `launchctl list` with a few hundred registered jobs is 10-40 KB — past the pipe buffer — so
    every HEALTHY re-run would bootstrap an already-loaded agent, the bootstrap fails
    ("Bootstrap failed: 5: Input/output error"), and the installer exits 1 claiming the opposite
    of the truth. The single-line stub in the sibling case cannot reach this: one line never
    blocks the writer.

    Mutation: restore `grep -qF` -> the healthy re-run reports rc 1 (and bootstraps twice) -> RED.
    """
    sb = _sandbox(tmp_path)
    # A `launchctl` shaped like a real one on a busy Mac: the label appears FIRST (so `-q` would
    # leave early), followed by ~3000 unrelated jobs (~60 KB, well past a 64 KB pipe's capacity
    # once the reader is gone), and a bootstrap of an ALREADY-LOADED label FAILS as the real tool
    # does.
    _write_stub(
        sb["bin"],
        "launchctl",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "launchctl $*" >> "$STUB_LOG/launchctl.log"\n'
        'case "${1:-}" in\n'
        '  list) if [ -f "$STUB_LOG/loaded" ]; then\n'
        '          echo "501 0 com.tortoise.runner-autoscale"\n'
        '          i=1; while [ "$i" -le 3000 ]; do echo "501 0 com.example.bgjob.$i"; i=$((i+1)); done\n'
        '        fi; exit 0 ;;\n'
        '  bootstrap) if [ -f "$STUB_LOG/loaded" ]; then\n'
        '                echo "Bootstrap failed: 5: Input/output error" >&2; exit 5; fi\n'
        '              : > "$STUB_LOG/loaded"; exit 0 ;;\n'
        '  bootout) rm -f "$STUB_LOG/loaded"; exit 0 ;;\n'
        "esac\nexit 0\n",
    )
    assert _run(sb).returncode == 0
    # The healthy re-run — plist unchanged, agent loaded — must converge, not re-bootstrap.
    res = _run(sb)
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "Bootstrap failed" not in res.stderr, res.stderr
    log = sb["launchctl_log"].read_text(encoding="utf-8")
    assert "re-armed" not in res.stdout, res.stdout
    assert log.count("launchctl bootstrap") == 1, log


def test_launchd_reinstall_re_arms_an_agent_that_was_unloaded_out_of_band(tmp_path):
    """⛔ "UNCHANGED" IS NOT "LOADED": re-running the installer must CONVERGE to a loaded agent.

    An unload out of band (reboot that dropped the GUI session, a `bootout`, a login-item cleanup)
    leaves the plist byte-identical, so the `unchanged` branch skipped the bootstrap and the load
    check then exited 1 — on EVERY re-run — while printing "run without --status to load", which is
    exactly the invocation that just failed. The systemd leg (`systemctl start`) and the cron leg
    (rewrite) both re-arm; launchd has to as well.

    Mutation: drop the re-arm bootstrap from the `unchanged` branch -> the second run exits 1
    (`ERROR: ... is not loaded`) -> RED.
    """
    sb = _sandbox(tmp_path)
    # A `launchctl` with STATE: `bootstrap` marks the agent loaded, `bootout` clears it, `list`
    # reports the state. Without this the unchanged-re-run path cannot be reached at all.
    _write_stub(
        sb["bin"],
        "launchctl",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "launchctl $*" >> "$STUB_LOG/launchctl.log"\n'
        'case "${1:-}" in\n'
        '  list) [ -f "$STUB_LOG/loaded" ] && echo "501 0 com.tortoise.runner-autoscale"; exit 0 ;;\n'
        '  bootstrap) : > "$STUB_LOG/loaded"; exit 0 ;;\n'
        '  bootout) rm -f "$STUB_LOG/loaded"; exit 0 ;;\n'
        "esac\nexit 0\n",
    )
    assert _run(sb).returncode == 0
    log = sb["launchctl_log"].read_text(encoding="utf-8")
    assert log.count("launchctl bootstrap") == 1, log

    # out-of-band unload: the plist is untouched, the agent is gone.
    (sb["log"] / "loaded").unlink()
    res = _run(sb)
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "unchanged" in res.stdout, res.stdout
    assert "re-armed" in res.stdout, res.stdout
    log = sb["launchctl_log"].read_text(encoding="utf-8")
    assert log.count("launchctl bootstrap") == 2, log


def test_systemd_install_fails_when_the_unit_cannot_be_written(tmp_path):
    """⛔ A UNIT THAT WAS NOT WRITTEN MUST NOT BE REPORTED "installed".

    The install writes the unit through `printf | sudo -n tee`, and treats a non-zero tee as "could
    not install" (`|| exit 1`). That fail-closed edge is invisible to every other case here, because
    the sandbox provides a writable `$SYSTEMD_UNIT_DIR` — and the `sudo` stub ends its tee branch
    with `exit $?` precisely so this edge is reachable.

    Mutation: end the stub's tee branch with `exit 0` (swallowing the write failure) -> the install
    continues and reports success for units that do not exist -> RED. Verified.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    _systemd_stubs(sb)
    # A path whose PARENT is a regular file: `tee` fails with ENOTDIR without needing chmod, so
    # this holds even when the suite runs as root.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory\n", encoding="utf-8")
    env = dict(
        sb["env"],
        AUTOSCALE_PLATFORM="systemd",
        SYSTEMD_UNIT_DIR=str(blocker / "units"),
    )
    res = _run(sb, env=env)
    assert res.returncode == 1, (res.stdout, res.stderr)
    assert "installed: " not in res.stdout, res.stdout


def test_the_provisioner_is_pinned_for_the_timer_only_when_it_exists(tmp_path):
    """The FILL leg must be reachable from the TIMER, not only from an interactive shell.

    A schedule that can repair runners and never ADD one is the "installed but inert" half this
    installer exists to avoid. Emitted only when the file is really there: the tool treats
    RUNNER_AUTOSCALE_PROVISIONER as authoritative, so a stale path would REFUSE an install that a
    candidate list could have found.

    Mutation: emit the key unconditionally -> the absent case asserts it must not appear -> RED.
    """
    sb = _sandbox(tmp_path)
    res = _run(sb, "--dry-run")
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "RUNNER_AUTOSCALE_PROVISIONER" not in res.stdout, (
        "an absent provisioner was pinned anyway"
    )

    prov = sb["repo"] / "tools" / "install-ci-runner-x64.sh"
    prov.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    prov.chmod(0o755)
    res = _run(sb, "--dry-run")
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert f"<key>RUNNER_AUTOSCALE_PROVISIONER</key><string>{prov}</string>" in res.stdout, (
        res.stdout
    )


def test_systemd_units_render_with_path_and_the_active_check(tmp_path):
    """On Linux the unit pins HOME/PATH (measured: no HOME ⇒ `gh auth login`, exit 2) and the
    installer verifies `is-active`, not merely `is-enabled`.

    Mutation: replace the is-active check with is-enabled -> a timer that will not fire passes.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    _systemd_stubs(sb)
    env = dict(sb["env"], AUTOSCALE_PLATFORM="systemd")
    res = _run(sb, "--dry-run", env=env)
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "Environment=HOME=" in res.stdout, res.stdout
    assert "Environment=PATH=" in res.stdout, res.stdout
    assert "OnUnitActiveSec=600" in res.stdout, res.stdout

    res = _run(sb, env=env)
    assert res.returncode == 0, (res.stdout, res.stderr)
    calls = (sb["log"] / "systemctl.log").read_text(encoding="utf-8")
    assert "is-active runner-autoscale.timer" in calls, calls


def test_systemd_install_fails_when_the_timer_is_not_active(tmp_path):
    """`enabled` is not a schedule: an inactive timer must fail the install loudly."""
    sb = _sandbox(tmp_path, uname="Linux")
    _systemd_stubs(sb, active="inactive")
    res = _run(sb, env=dict(sb["env"], AUTOSCALE_PLATFORM="systemd"))
    assert res.returncode == 1, (res.stdout, res.stderr)
    assert "not active" in res.stderr, res.stderr


# ── the cron leg ────────────────────────────────────────────────────────────────────────────
def test_platform_selection_on_linux_picks_what_the_box_really_has(tmp_path):
    """`platform()`'s Linux arm is what makes or breaks the guard HERE, and it must be pinned
    through the real dispatch — `AUTOSCALE_PLATFORM` would cover only the branch it names.

    Both directions, because each fails differently (review of PR #9685: two mutations of this
    arm survived a suite that leaned on `AUTOSCALE_PLATFORM`):

      - systemd present                -> systemd  (the cell-4 shape. Choosing cron here installs
                                        cleanly and never fires — the inert enforcer)
      - systemd absent, cron installed -> cron     (delete `elif has_cron` and this resolves to
                                        `none`, so the box REFUSES to install and loses its
                                        schedule forever, with a green suite)
      - neither                        -> none, REFUSED with nothing written

    Mutations: drop `elif has_cron` -> case 2 exits 2 instead of rendering a cron line.
    Swap the probe order -> case 1 renders a cron line instead of the timer.
    """
    sb = _sandbox(
        tmp_path,
        uname="Linux",
        env_overrides={"AUTOSCALE_HAS_SYSTEMD": "1", "AUTOSCALE_HAS_CRON": "1"},
    )
    _systemd_stubs(sb)
    res = _run(sb, "--dry-run")
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "# platform=systemd" in res.stdout, res.stdout
    assert f"--- {sb['units']}/{LABEL}.service" in res.stdout, res.stdout

    sb = _sandbox(
        tmp_path / "cronbox",
        uname="Linux",
        env_overrides={"AUTOSCALE_HAS_SYSTEMD": "0", "AUTOSCALE_HAS_CRON": "1"},
    )
    res = _run(sb, "--dry-run")
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "# platform=cron" in res.stdout, res.stdout
    assert f"{LABEL}-cron" in res.stdout, res.stdout

    sb = _sandbox(
        tmp_path / "bare",
        uname="Linux",
        env_overrides={"AUTOSCALE_HAS_SYSTEMD": "0", "AUTOSCALE_HAS_CRON": "0"},
    )
    res = _run(sb, "--dry-run")
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "REFUSED" in res.stderr, res.stderr
    assert not (sb["install"] / "runner_autoscale.py").exists(), "a refused box was mutated"


def test_cron_line_carries_the_path_and_home_the_job_needs(tmp_path):
    """cron runs with `/usr/bin:/bin` and the PASSWD home, so both must be pinned.

    ⛔ HOME, not just PATH (round 10, F2's cron half): gh reads its credential from
    `$HOME/.config/gh/hosts.yml`, and the passwd home is not where the fleet's login lives — the
    systemd unit pins HOME for exactly this measured reason, and a cron-only host without it
    fired, found no credential and exited 2 every interval.

    Mutation: drop `$CRON_ENV` from cron_line -> no PATH=/HOME= in the entry -> RED.
    """
    sb = _sandbox(
        tmp_path,
        uname="Linux",
        cron=True,
        env_overrides={"AUTOSCALE_HAS_SYSTEMD": "0", "AUTOSCALE_HAS_CRON": "1"},
    )
    res = _run(sb, "--dry-run")
    assert res.returncode == 0, (res.stdout, res.stderr)
    line = [ln for ln in res.stdout.splitlines() if f"{LABEL}-cron" in ln]
    assert line and "PATH=" in line[0], res.stdout
    assert "HOME=" in line[0], f"cron needs HOME pinned too (gh credential): {line[0]}"


@pytest.mark.parametrize(
    ("interval", "want_sched", "want_note"),
    [
        ("600", "*/10 * * * *", False),  # the default: expressible exactly, no note
        ("1800", "*/30 * * * *", False),
        ("90", "*/2 * * * *", True),  # below one minute of cron granularity: round UP
        ("1500", "*/30 * * * *", True),  # 25 min: NOT a divisor — this is the up-vs-down case
        ("2700", "0 */1 * * *", True),  # 45 min: not a divisor of 60 -> hourly, never tighter
        ("5400", "0 */2 * * *", True),  # 90 min -> every 2h
        ("36000", "0 */12 * * *", True),  # 10h: `*/10` on the HOUR field is not a period either
    ],
)
def test_cron_cadence_is_a_real_period_and_never_tighter_than_requested(
    tmp_path, interval, want_sched, want_note
):
    """⛔ `*/N` IS NOT A PERIOD UNLESS N DIVIDES THE FIELD'S RANGE, and the old code assumed it was.

    Measured by --dry-run before the fix (round 11 review, P2):

      - 45 min -> `*/45 * * * *`, which fires at :00 and :45 only — 45 then 15 minutes apart, an
        average 30-minute loop, i.e. TIGHTER than requested;
      - 58 min -> `*/58` — 58 then 2 minutes apart;
      - 90 s   -> `*/1` — every 60s, 1.5x the request (floor division on a sub-minute interval);
      - the HOUR field has the same trap: `*/10 * * *` fires 00:00 and 10:00, then 00:00 again —
        gaps of 10h and 14h, not a 10-hour period.

    Over-firing is the unbounded harm the INTERVAL floor exists to prevent ("a tighter loop only
    hammers the API"), and it is invisible in the rendered line, so the rule is: pick the closest
    REAL period AT OR ABOVE the request and say so. Firing less often than configured is a
    visible, bounded loss of freshness.

    ⛔ THE 1500 (25 min) CASE IS THE ONE THAT PINS THE DIRECTION. Round-up and round-down only
    differ for a request that is NOT a divisor of 60, and only below the first branch's 30-minute
    ceiling — so a table of exact divisors (`*/10`, `*/30`, `*/2`) is green under BOTH rules and
    proves nothing about which one is implemented. 25 min rounds UP to `*/30` (never tighter); a
    round-down implementation renders `*/20`, which fires more often than asked.

    Mutations: emit `*/$minutes` for any minutes < 60 -> 2700 renders `*/45` -> RED. Round DOWN to
    the nearest divisor -> 1500 renders `*/20` -> RED. Keep `INTERVAL/60` floor division -> 90
    renders `*/1` -> RED. (All three verified by applying them.)
    """
    sb = _sandbox(
        tmp_path,
        uname="Linux",
        env_overrides={"AUTOSCALE_HAS_SYSTEMD": "0", "AUTOSCALE_HAS_CRON": "1"},
    )
    res = _run(sb, "--dry-run", env=dict(sb["env"], RUNNER_AUTOSCALE_INTERVAL=interval))
    assert res.returncode == 0, (res.stdout, res.stderr)
    line = [ln for ln in res.stdout.splitlines() if f"{LABEL}-cron" in ln]
    assert line, res.stdout
    sched = " ".join(line[0].split()[:5])
    assert sched == want_sched, (sched, want_sched, res.stdout)
    assert ("NOTE: cron cannot express" in res.stderr) is want_note, (res.stderr, want_note)


def test_cron_refuses_a_period_it_cannot_deliver(tmp_path):
    """⛔ REFUSE, DON'T PROMISE: cron has no faithful period longer than 12 hours.

    The hour field's `*/N` is a period only when N divides 24, so 12h is the longest real one. The
    first version of this fix fell back to `0 0 * * *` — DAILY — for anything above it, which for a
    25h request fires MORE often than asked (the harm this whole function refuses) while the NOTE
    printed the opposite. VGATE caught it on the change that introduced it; cron's day-of-month
    `*/N` is no escape either, because it restarts at every month boundary.

    So the same fail-closed rule as the >= 60s floor applies at the long end: name the refusal and
    write nothing. Firing less often than configured is a bounded, visible loss; firing more often
    is the unbounded API hammering this guards.

    Mutation: restore the `0 0 * * *` daily fallback -> 25h installs as a DAILY job -> RED.
    """
    sb = _sandbox(
        tmp_path,
        uname="Linux",
        env_overrides={"AUTOSCALE_HAS_SYSTEMD": "0", "AUTOSCALE_HAS_CRON": "1"},
    )
    # 46800 = 13h (no divisor of 24 is >= 13), 90000 = 25h (above a day at all), and
    # 2**63-1 — the ceiling of a signed 64-bit shell integer, where `(INTERVAL + 59) / 60`
    # WRAPS NEGATIVE and a `-lt 1` clamp pins the entry to `*/1` (every minute). Ceiling by
    # `(N - 1) / 60 + 1` cannot overflow.
    for interval in ("46800", "90000", "9223372036854775807", "9223372036854775750"):
        res = _run(sb, "--dry-run", env=dict(sb["env"], RUNNER_AUTOSCALE_INTERVAL=interval))
        assert res.returncode == 2, (interval, res.stdout, res.stderr)
        assert "REFUSED" in res.stderr and "MORE often" in res.stderr, (interval, res.stderr)
    # the INSTALL refuses the same way, and arms nothing.
    res = _run(sb, env=dict(sb["env"], RUNNER_AUTOSCALE_INTERVAL="90000"))
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "REFUSED" in res.stderr, res.stderr
    assert not sb["crontab_store"].exists(), "a refused interval must write no cron entry"
    # …and the EXIT line's promise ("refusal — nothing was written") must hold: no runnable copy
    # either. It did not, while the cadence was resolved AFTER `install -m 0755` (VGATE round 2).
    assert not (sb["install"] / "runner_autoscale.py").exists(), (
        "a refused interval must not refresh the runnable copy — the header promises a refusal "
        "writes nothing")
    # …while the longest expressible period still installs (the boundary is 12h, not "refuse"):
    res = _run(sb, "--dry-run", env=dict(sb["env"], RUNNER_AUTOSCALE_INTERVAL="43200"))
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "0 */12 * * *" in res.stdout, res.stdout


def test_cron_install_is_idempotent_and_survives_an_empty_crontab(tmp_path):
    """Re-installing with a changed interval must not exit 1 AFTER succeeding (pipefail)."""
    sb = _sandbox(
        tmp_path,
        uname="Linux",
        cron=True,
        env_overrides={"AUTOSCALE_HAS_SYSTEMD": "0", "AUTOSCALE_HAS_CRON": "1"},
    )
    first = _run(sb)
    assert first.returncode == 0, (first.stdout, first.stderr)
    assert f"{LABEL}-cron" in sb["crontab_store"].read_text(encoding="utf-8")

    again = _run(sb)
    assert again.returncode == 0 and "unchanged" in again.stdout, (again.stdout, again.stderr)

    res = _run(sb, env=dict(sb["env"], RUNNER_AUTOSCALE_INTERVAL="1800"))
    assert res.returncode == 0, (res.stdout, res.stderr)
    body = sb["crontab_store"].read_text(encoding="utf-8")
    assert body.count(f"{LABEL}-cron") == 1, body
    assert "*/30 * * * *" in body, body


def test_cron_uninstall_removes_only_its_own_line(tmp_path):
    """`--uninstall` on a crontab holding ONLY our line must report success, not
    "could not rewrite crontab" (the same pipefail shape)."""
    sb = _sandbox(
        tmp_path,
        uname="Linux",
        cron=True,
        env_overrides={"AUTOSCALE_HAS_SYSTEMD": "0", "AUTOSCALE_HAS_CRON": "1"},
    )
    assert _run(sb).returncode == 0
    store = sb["crontab_store"]
    store.write_text(
        store.read_text(encoding="utf-8") + "0 3 * * * /bin/true # somebody-else\n",
        encoding="utf-8",
    )
    res = _run(sb, "--uninstall")
    assert res.returncode == 0, (res.stdout, res.stderr)
    body = store.read_text(encoding="utf-8")
    assert LABEL not in body, body
    assert "somebody-else" in body, body

    assert _run(sb).returncode == 0
    res = _run(sb, "--uninstall")
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert LABEL not in store.read_text(encoding="utf-8")


# ── the drift check must work on the platform whose branch the script implements ────────────
def test_status_reports_drift_where_sha256sum_does_not_exist(tmp_path):
    """Stock macOS has no `sha256sum`; both substitutions used to yield "" so `"" == ""` and the
    DRIFT line could never print on macOS — the `--status` feature the header advertises was inert.

    Mutation: restore `sha256sum … | cut -d' ' -f1` -> no DRIFT line -> RED.
    """
    sb = _sandbox(tmp_path)
    assert _run(sb).returncode == 0
    installed = sb["install"] / "runner_autoscale.py"

    res = _run(sb, "--status")
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "sha256=" in res.stdout and "sha256=unavailable" not in res.stdout, res.stdout
    assert "DRIFT" not in res.stdout, res.stdout

    installed.write_text("# a NEWER copy, as a re-sync would leave it\n", encoding="utf-8")
    res = _run(sb, "--status")
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "DRIFT" in res.stdout, res.stdout


def test_status_warns_instead_of_claiming_no_drift_when_no_hasher_works(tmp_path):
    """Two unavailable readings comparing equal IS the bug that made the check inert — say so."""
    sb = _sandbox(tmp_path)
    assert _run(sb).returncode == 0
    res = _run(sb, "--status", env=dict(sb["env"], AUTOSCALE_SHA256=str(sb["bin"] / "no-such")))
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "sha256=unavailable" in res.stdout, res.stdout
    assert "drift cannot be verified" in res.stderr, res.stderr


def test_status_proves_the_schedule_would_actually_run(tmp_path):
    """`--status` must PROVE runnability, not just that the unit/plist is loaded.

    ⛔ Round 10, I2: `launchctl list` and `systemctl is-active` both report "installed/loaded" for
    a schedule whose guard cannot measure anything on this box — installed, and inert forever. The
    only honest proof is a read-only pass of the RUNNING copy with the interpreter the schedule
    uses, so `--status` now runs it and reports ITS exit code.

    Mutation: drop the probe block -> a guard that exits 2 reports "installed" with exit 0 -> RED.
    """
    sb = _sandbox(tmp_path)
    # An interpreter whose read-only pass REFUSES to measure (the tool's own exit-2 verdict).
    _write_stub(
        sb["bin"],
        "py312_refuses",
        "#!/usr/bin/env bash\n"
        'case " $* " in *" --json "*) echo \'{"verdict": "unmeasurable", "error": "gh is not on PATH"}\'; exit 2 ;; esac\n'
        "exit 0\n",
    )
    env = dict(sb["env"], AUTOSCALE_PYTHON=str(sb["bin"] / "py312_refuses"))
    # Install with a WORKING interpreter (the install's own probe refuses on a box that cannot
    # measure), then ask `--status` with the failing one: the installed copy has not changed, so
    # the difference is exactly what `--status` proves about it.
    assert _run(sb).returncode == 0

    res = _run(sb, "--status", env=env)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "probe: FAILED (exit 2)" in res.stderr, res.stderr
    assert "gh is not on PATH" in res.stderr, res.stderr


def test_status_fails_when_nothing_is_armed(tmp_path):
    """⛔ "NOT installed" was exit 0 — the strictly worse state read as healthy.

    The round's own contract is that a bring-up script can gate on `--status`. A non-zero answer
    for "installed but inert" and a ZERO answer for "nothing is armed" would be backwards.
    Mutation: drop `armed=0` from the NOT-installed branches -> rc 0 -> RED.
    """
    sb = _sandbox(tmp_path)
    res = _run(sb, "--status")
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "NOT installed" in res.stdout or "MISSING" in res.stdout, res.stdout

    # …and after an install, the same box is armed: the difference is the schedule, not the box.
    assert _run(sb).returncode == 0
    assert _run(sb, "--status").returncode == 0


def test_status_fails_when_the_schedule_would_fire_and_do_nothing(tmp_path):
    """⛔ THE THREE INERT STATES MUST ALL FAIL `--status`, not just "nothing is armed".

    `armed` started at 1 and was only ever cleared by the entry-exists branches, so a PRESENT
    entry whose TARGET was gone read as healthy — the exact state the file names in prose ("the
    schedule would run nothing", "the timer activates nothing") and the caller is told to gate on:

      1. the run-copy is GONE but the plist/timer/cron entry is present (a reaped worktree, a
         half-copy, an `--uninstall` that removed only the copy);
      2. the systemd `.timer` is armed but its `.service` is missing;
      3. the probe CRASHES (exit 1) instead of returning the tool's own exit-2 verdict — the line
         above already says "this schedule would fire and do NOTHING", and only `2` was propagated.

    Measured all three as `rc 0` before the fix (round 11 review, P1).
    Mutations: drop `armed=0` from the MISSING branch / from the service check; change
    `[ "$probe_rc" = "0" ] || return 2` back to `= "2"` -> each is RED.
    """
    # (1) entry present, run-copy gone.
    sb = _sandbox(tmp_path / "nocopy")
    assert _run(sb).returncode == 0
    (sb["install"] / "runner_autoscale.py").unlink()
    res = _run(sb, "--status")
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "run-copy: MISSING" in res.stdout, res.stdout

    # (2) the timer is armed but activates nothing.
    sb = _sandbox(tmp_path / "systemd", uname="Linux")
    _systemd_stubs(sb)
    env = dict(sb["env"], AUTOSCALE_PLATFORM="systemd")
    assert _run(sb, env=env).returncode == 0
    (sb["units"] / f"{LABEL}.service").unlink()
    res = _run(sb, "--status", env=env)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "activates nothing" in res.stdout, res.stdout

    # (3) the probe crashes (exit 1) rather than refusing with the tool's own exit 2.
    sb = _sandbox(tmp_path / "crash")
    _write_stub(
        sb["bin"],
        "py312_crash",
        "#!/usr/bin/env bash\n"
        'case " $* " in *" --json "*) echo "Traceback (most recent call last):" >&2; exit 1 ;; esac\n'
        "exit 0\n",
    )
    assert _run(sb).returncode == 0  # install with the working interpreter
    res = _run(sb, "--status", env=dict(sb["env"], AUTOSCALE_PYTHON=str(sb["bin"] / "py312_crash")))
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "probe: FAILED (exit 1)" in res.stderr, res.stderr


def test_status_fails_when_the_timer_was_stopped_out_of_band(tmp_path):
    """⛔ A STOPPED TIMER IS INERT, AND `--status` SAID OTHERWISE.

    The install path already fails hard when `is-active` is not `active`, so `--status` printing
    "active:  inactive" and returning 0 was the same false PASS one branch over: a `systemctl
    stop`, a mask, or a unit that failed after install all leave the timer installed, enabled, and
    never firing. Pre-existing (found in cycle 2, same family as the P1 above) and fixed here
    because the whole contract of this function is that a caller can gate on it.

    Mutation: drop `armed=0` from the active check -> rc 0 for a stopped timer -> RED.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    _systemd_stubs(sb)
    env = dict(sb["env"], AUTOSCALE_PLATFORM="systemd")
    assert _run(sb, env=env).returncode == 0
    # stop the timer OUT OF BAND: the unit files are untouched, the timer is no longer active.
    _systemd_stubs(sb, active="inactive")
    res = _run(sb, "--status", env=env)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "active:  inactive" in res.stdout, res.stdout
    assert "not active" in res.stdout or "will not fire" in res.stdout, res.stdout


def test_the_probe_runs_the_schedule_s_repo_and_pinned_environment(tmp_path):
    """The probe must reproduce what the TIMER runs, or it green-lights a different schedule.

    ⛔ Round 10 review, P3: the probes ignored `$ARGV_TAIL`, so with `RUNNER_AUTOSCALE_REPO` set
    the installer validated this shell's repo while the timer served another — and they reproduced
    only `RUNNER_AUTOSCALE_STATE_DIR`, not the HOME/PATH/RUNNER_AUTOSCALE_GH pins the unit and
    plist exist to carry. A box inert BECAUSE of those pins still probed OK.
    Mutation: drop `$ARGV_TAIL`/the pinned env from `probe()` -> no `--repo`, no pinned HOME -> RED.

    ⛔ The gh assertion is a PROPERTY, not `== the sandbox stub`. The installer resolves `gh` from
    an ABSOLUTE candidate list first (`/usr/bin/gh`, `/usr/local/bin/gh`, `/opt/homebrew/bin/gh`,
    `$HOME/bin/gh`) and only falls back to `command -v gh`, because a timer's PATH is
    `/usr/bin:/bin:/usr/sbin:/sbin` and a Homebrew `gh` is not on it. So the stub on the sandbox
    PATH wins only on a box with no system `gh` — asserting the stub path made this case pass on
    macOS and fail on the Linux CI runner, where `/usr/bin/gh` exists and won (4 cases RED in
    `test (a)/(b)/(e)/(i)`). What the probe must reproduce is the pin the TIMER carries, whichever
    `gh` that is, so the readable end is: non-empty, a real executable, and EQUAL to the value
    rendered into the installed plist.
    """
    sb = _sandbox(tmp_path)
    # a `gh` the installer can resolve at install time: the pin it then carries is what the probe
    # must reproduce (without one, the plist renders the bare `gh` default and nothing is pinned).
    _write_stub(sb["bin"], "gh", "#!/usr/bin/env bash\nexit 0\n")
    _write_stub(
        sb["bin"],
        "py312_echo",
        "#!/usr/bin/env bash\n"
        'printf "ARGV:%s\\n" "$*" >> "$STUB_LOG/probe.log"\n'
        'printf "HOME=%s\\n" "${HOME:-}" >> "$STUB_LOG/probe.log"\n'
        'printf "GH=%s\\n" "${RUNNER_AUTOSCALE_GH:-}" >> "$STUB_LOG/probe.log"\n'
        "exit 0\n",
    )
    env = dict(
        sb["env"],
        AUTOSCALE_PYTHON=str(sb["bin"] / "py312_echo"),
        RUNNER_AUTOSCALE_REPO="someone/other-repo",
    )
    assert _run(sb, env=env).returncode == 0
    log = (sb["log"] / "probe.log").read_text()
    assert "--repo someone/other-repo" in log, log
    assert f"HOME={sb['home']}" in log, log
    # (the first ARGV line is the interpreter FLOOR CHECK, `-c …`, not the probe)
    pinned = [ln[len("GH=") :] for ln in log.splitlines() if ln.startswith("GH=")]
    assert pinned, f"the probe must carry a pinned gh at all: {log}"
    gh = pinned[-1]
    assert gh, f"the resolved gh must be pinned, not left at the bare default: {log}"
    assert os.path.isabs(gh), (
        f"the pin must be an absolute path, not PATH-resolved at fire time: {gh!r}"
    )
    assert os.access(gh, os.X_OK), f"the pin must be a real executable: {gh!r}"
    # and it is the SAME pin the timer is given — a probe green on a different gh green-lights nothing
    installed = (sb["agents"] / PLIST_NAME).read_text(encoding="utf-8")
    assert f"<key>RUNNER_AUTOSCALE_GH</key><string>{gh}</string>" in installed, installed


def test_status_reports_a_healthy_probe(tmp_path):
    """The other direction: a measurable box is reported OK, and exit stays 0."""
    sb = _sandbox(tmp_path)
    assert _run(sb).returncode == 0
    res = _run(sb, "--status")
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "probe: OK (exit 0)" in res.stdout, res.stdout


def test_help_prints_the_usage_block():
    """`--help` is the documented entry point; it must not need a scheduler to answer."""
    res = subprocess.run(
        ["bash", str(SCRIPT), "--help"], capture_output=True, text=True, timeout=60
    )
    assert res.returncode == 0, res.stderr
    assert "USAGE" in res.stdout
