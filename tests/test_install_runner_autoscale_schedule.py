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


def _sandbox(tmp_path: Path, uname: str = "Darwin", *, cron: bool = True) -> dict:
    """Throwaway repo/HOME/log + PATH stubs. Nothing here can touch the host."""
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True)
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
        'case "$1" in tee) shift; cat > "$1"; exit 0 ;; esac\n'
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
    """Install writes, lints, bootstraps, and then CHECKS the agent is loaded.

    Mutation: drop the `launchctl list | grep` verification -> an unloaded agent reports success
    -> RED.
    """
    sb = _sandbox(tmp_path)
    res = _run(sb)
    assert res.returncode == 0, (res.stdout, res.stderr)
    plist = sb["agents"] / PLIST_NAME
    assert plist.exists(), res.stdout
    body = plist.read_text(encoding="utf-8")
    assert "<key>PATH</key>" in body and "<key>RUNNER_AUTOSCALE_GH</key>" in body, body
    assert "bootstrap" in sb["launchctl_log"].read_text(encoding="utf-8")


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
def test_cron_line_carries_the_path_and_home_the_job_needs(tmp_path):
    """cron runs with `/usr/bin:/bin` and the PASSWD home, so both must be pinned.

    ⛔ HOME, not just PATH (round 10, F2's cron half): gh reads its credential from
    `$HOME/.config/gh/hosts.yml`, and the passwd home is not where the fleet's login lives — the
    systemd unit pins HOME for exactly this measured reason, and a cron-only host without it
    fired, found no credential and exited 2 every interval.

    Mutation: drop `$CRON_ENV` from cron_line -> no PATH=/HOME= in the entry -> RED.
    """
    sb = _sandbox(tmp_path, uname="Linux", cron=True)
    res = _run(sb, "--dry-run")
    assert res.returncode == 0, (res.stdout, res.stderr)
    line = [ln for ln in res.stdout.splitlines() if f"{LABEL}-cron" in ln]
    assert line and "PATH=" in line[0], res.stdout
    assert "HOME=" in line[0], f"cron needs HOME pinned too (gh credential): {line[0]}"


def test_cron_install_is_idempotent_and_survives_an_empty_crontab(tmp_path):
    """Re-installing with a changed interval must not exit 1 AFTER succeeding (pipefail)."""
    sb = _sandbox(tmp_path, uname="Linux", cron=True)
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
    sb = _sandbox(tmp_path, uname="Linux", cron=True)
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


def test_the_probe_runs_the_schedule_s_repo_and_pinned_environment(tmp_path):
    """The probe must reproduce what the TIMER runs, or it green-lights a different schedule.

    ⛔ Round 10 review, P3: the probes ignored `$ARGV_TAIL`, so with `RUNNER_AUTOSCALE_REPO` set
    the installer validated this shell's repo while the timer served another — and they reproduced
    only `RUNNER_AUTOSCALE_STATE_DIR`, not the HOME/PATH/RUNNER_AUTOSCALE_GH pins the unit and
    plist exist to carry. A box inert BECAUSE of those pins still probed OK.
    Mutation: drop `$ARGV_TAIL`/the pinned env from `probe()` -> no `--repo`, no pinned HOME -> RED.
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
    assert f"GH={sb['bin']}/gh" in log, f"the resolved gh must be pinned for the probe: {log}"


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
