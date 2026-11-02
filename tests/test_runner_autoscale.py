"""Hermetic tests for tools/runner_autoscale.py — the runner-capacity guard.

No network, no real `gh`, no systemd, no sudo: every external surface is a fixture
(`gh` fake, `systemctl` fake, provisioner fake, a meminfo-shaped file, a temp runner root).
Each test is written so it can FAIL on a regression, and every gate that could be decoration
is also exercised in the NEGATIVE direction via a source MUTATION (`test_each_gate_is_load_bearing`):
the mutant must produce the wrong verdict the test is designed to catch. A gate whose mutation
still passes the suite is a gate nobody is enforcing.

The gates, and the failure each one exists to prevent:

  * ceiling = min(cores, live + memory headroom)
      - cores: 8 → 3, 4 → 2 (the owner's table, agent-infra#2344) — a flat "3–5" would put 5
        shards on a 4-core cell and fail shards for reasons unrelated to the code.
      - memory: additive over the listeners ALREADY alive, because MemAvailable has already
        paid for them. A non-additive arm shrinks the ceiling as runners come up, so a healthy
        box at its target reads "over ceiling".
      - the reserve floor is per ADDITION and re-measured every step; `--want` above the
        ceiling is REFUSED with the reading printed.
  * an unreadable measurement at pass START is exit 2 with NOTHING changed — never 0 runners,
    never "plenty". ⛔ A MID-pass re-measure failure is ALSO exit 2, and the box was already
    touched: the record then lists what it did (see the tool's own exit-2 entry). Reading this line
    as "exit 2 means untouched" is the mis-triage the tool's EXIT block warns against.
  * a full pool adds nothing (idempotent), a dead runner is restarted (or replaced), an
    orphaned listener is re-registered, and a BUSY/SERVING runner is never touched.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "runner_autoscale.py"
INSTALLER = REPO_ROOT / "tools" / "install-ci-runner-x64.sh"
BASH = shutil.which("bash") or "/bin/bash"
REPO = "daniel-ospina/tortoise"

#: A pool label set carrying BOTH hosted version spellings — the bare shape the pool is opted
#: into (`ubuntu-24.04`, PRESERVED through a repair) and GitHub's arm64 variant
#: (`ubuntu-24.04-arm`, DROPPED — `data/reusables/actions/supported-github-runners.md`) — plus the
#: labels that must PRESERVE — the fallback, a custom tag, and `ubuntu-2204-tools`, which merely
#: starts `ubuntu-<digits>` and is not a hosted label. Shared by the test and its mutation fixture so
#: the two cannot drift into exercising different label sets.
POOL_LABELS_WITH_VERSION_PINS = "self-hosted,Linux,X64,self-hosted-x64,ubuntu-latest,ubuntu-24.04,ubuntu-24.04-arm,gpu,ubuntu-2204-tools"

# ── fakes ────────────────────────────────────────────────────────────────────────────────────
GH_FAKE = textwrap.dedent("""\
    #!/usr/bin/env bash
    # Fake `gh`. Serves the pool from $POOL_TSV; mints a token. When a restart marker exists the
    # named runner(s) read `online`, so the real `wait_online` poll loop is exercised rather than
    # assumed.
    set -uo pipefail
    if [ "$1" = "api" ] && [ "${2:-}" = "-X" ]; then printf 'TOKEN-FROM-FAKE-GH\\n'; exit 0; fi
    # POOL_FAIL_AFTER=<n>: the first n pool reads succeed, every later read FAILS. That is the
    # "restart succeeded, then the API went away" window, which must not become a --replace.
    if [ -n "${POOL_FAIL_AFTER:-}" ]; then
        n=$(cat "${POOL_READ_COUNT:-/dev/null}" 2>/dev/null || echo 0)
        n=$(( n + 1 ))
        printf '%s' "$n" > "${POOL_READ_COUNT:-/dev/null}"
        if [ "$n" -gt "$POOL_FAIL_AFTER" ]; then echo 'gh: 502 Bad Gateway' >&2; exit 1; fi
    fi
    if [ -f "${RESTART_MARKER:-/nonexistent}" ]; then
        m="$(cat "${RESTART_MARKER}")"
        awk -v m="$m" 'BEGIN{FS="\\t"} { if (m == "*" || $1 == m) $2 = "online"; print $1"\\t"$2"\\t"$3"\\t"$4 }' "$POOL_TSV"
        exit 0
    fi
    cat "$POOL_TSV"
""")

SYSTEMCTL_FAKE = textwrap.dedent("""\
    #!/usr/bin/env bash
    # Fake `systemctl`. `restart <unit>` succeeds only when SYSTEMCTL_RC=0, and records the
    # runner name (from the unit) in the restart marker so the pool read flips it online.
    printf '%s\\n' "$*" >> "${SYSTEMCTL_LOG:-/dev/null}"
    case "$1" in
      restart)
        [ "${SYSTEMCTL_RC:-0}" = "0" ] || exit 1
        printf '%s' "${2##*.}" > /dev/null           # unit: actions.runner.<slug>.<name>.service
        unit="${2:-}"; unit="${unit#actions.runner.}"; unit="${unit%.service}"
        printf '%s' "${unit##*.}" > "${RESTART_MARKER:-/dev/null}"
        exit 0 ;;
      enable)
        # SYSTEMCTL_ENABLE_RC!=0: the unit never comes up — a registration that must NOT be
        # counted as live, and must not be reported `converged`.
        [ "${SYSTEMCTL_ENABLE_RC:-0}" = "0" ] || exit 1
        exit 0 ;;
      *) exit 0 ;;
    esac
""")

PROVISIONER_FAKE = textwrap.dedent("""\
    #!/usr/bin/env bash
    # Fake installer, modelling the REAL contract: `--count N` means "ensure installs 1..N exist",
    # so it creates every missing index and adds each new install to the alive-dirs seam (a real
    # install starts a listener). PROVISIONER_ADDS=0 makes it add nothing, which is the
    # "provisioner returned 0 but the box did not change" case the fill loop must not mistake for
    # progress. PROVISIONER_SHRINK_TO lowers MemAvailable after an install, because a real
    # addition consumes memory — the per-step re-measure is only meaningful if the reading moves.
    printf '%s\\n' "$*" >> "${PROVISIONER_LOG:-/dev/null}"
    if [ "${PROVISIONER_RC:-0}" != "0" ]; then echo "${PROVISIONER_MSG:-boom}" >&2; exit "$PROVISIONER_RC"; fi
    count="$2"
    base="$RUNNER_AUTOSCALE_RUNNER_ROOT/gh-runner-x64"
    if [ "${PROVISIONER_ADDS:-1}" = "1" ]; then
        i=1
        while [ "$i" -le "$count" ]; do
            dir="$base"
            [ "$i" -gt 1 ] && dir="$base-$i"
            if [ ! -f "$dir/.runner" ]; then
                mkdir -p "$dir"
                printf '{"agentName":"auto-%s","gitHubUrl":"https://github.com/daniel-ospina/tortoise"}' "$i" \\
                    > "$dir/.runner"
                if [ "${PROVISIONER_ALIVE:-1}" = "1" ]; then
                    printf '%s\\n' "$dir" >> "$RUNNER_AUTOSCALE_ALIVE_DIRS"
                fi
                if [ -n "${PROVISIONER_SHRINK_TO:-}" ]; then
                    # ⛔ PORTABLE, NOT `sed -i`. That form is GNU-only: BSD sed (macOS, the box the
                    # schedule runs on) rejects it with "unescaped newline inside substitute
                    # pattern", and because this fake has no `set -e` the shrink then silently did
                    # not happen — the post-step memory guard looked dead and TWO test items failed
                    # on macOS while Linux CI stayed green (round 9, finding P2-1). awk + mv is
                    # portable to both.
                    awk -v mb="${PROVISIONER_SHRINK_TO}" \
                        '/^MemAvailable:/ { print "MemAvailable:   " mb " kB"; next } { print }' \
                        "$RUNNER_AUTOSCALE_MEMINFO" > "$RUNNER_AUTOSCALE_MEMINFO.tmp" \
                        && mv "$RUNNER_AUTOSCALE_MEMINFO.tmp" "$RUNNER_AUTOSCALE_MEMINFO"
                fi
            fi
            i=$(( i + 1 ))
        done
    fi
    if [ -n "${PROVISIONER_BREAK_MEMINFO:-}" ]; then
        rm -f "$RUNNER_AUTOSCALE_MEMINFO"
    fi
    echo "installed up to $count"
""")

CONFIG_SH_FAKE = (
    '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "${CONFIG_LOG:-/dev/null}"\nexit 0\n'
)

# A fake `svc.sh`: the runner's OWN service primitive (launchd on macOS, systemd-wrap elsewhere).
# `stop`/`start`/`install` are the only verbs the tool may use on a launchd host, and `start`
# writes the restart marker so the real `wait_online` poll sees the runner come back.
SVC_SH_FAKE = textwrap.dedent("""\
    #!/usr/bin/env bash
    printf '%s\\n' "$*" >> "${SVC_LOG:-/dev/null}"
    case "${1:-}" in
      install) exit "${SVC_INSTALL_RC:-0}" ;;
      stop)    exit "${SVC_STOP_RC:-0}" ;;
      start)
        [ "${SVC_START_RC:-0}" = "0" ] || exit 1
        printf '%s' "${SVC_MARK:-}" > "${RESTART_MARKER:-/dev/null}"
        exit 0 ;;
      *) exit 0 ;;
    esac
""")

_counter = itertools.count()


def _exe(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _meminfo(path: Path, available_mb: int, *, omit: bool = False) -> Path:
    body = "MemTotal:       16384000 kB\nMemFree:          512000 kB\n"
    if not omit:
        body += f"MemAvailable:   {available_mb * 1024} kB\n"
    path.write_text(body, encoding="utf-8")
    return path


def _install(root: Path, dirname: str, name: str, repo: str = REPO) -> Path:
    """A runner install the tool can see: `.runner` (with a BOM, as some versions write) and a
    `config.sh` to re-register with."""
    directory = root / dirname
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.sh").write_text(CONFIG_SH_FAKE, encoding="utf-8")
    (directory / "config.sh").chmod(0o755)
    (directory / ".runner").write_text(
        json.dumps({"agentName": name, "gitHubUrl": f"https://github.com/{repo}"}),
        encoding="utf-8-sig",
    )
    return directory


def _pool(path: Path, rows: list[tuple[str, str, bool, str]]) -> Path:
    path.write_text(
        "".join(
            f"{name}\t{status}\t{'true' if busy else 'false'}\t{labels}\n"
            for name, status, busy, labels in rows
        ),
        encoding="utf-8",
    )
    return path


class Env:
    """The whole external surface of one invocation, hermetic and inspectable."""

    def __init__(
        self,
        tmp: Path,
        *,
        mem_mb: int = 8000,
        ncpu: int = 8,
        pool: list[tuple[str, str, bool, str]] | None = None,
        installs: list[tuple[str, str]] | None = None,
        systemctl_rc: int = 0,
        shrink_to: int | None = None,
        omit_memavailable: bool = False,
        machine: str = "x86_64",
        install_repo: str = REPO,
        foreign: list[str] | None = None,
        not_alive: list[str] | None = None,
        pool_fail_after: int | None = None,
        provisioner_rc: int = 0,
        provisioner_msg: str = "boom",
        provisioner_alive: int = 1,
        provisioner_break_meminfo: int = 0,
        gap_slots: list[int] | None = None,
        ps_rc: int = 0,
        alive_seam: bool = True,
        svc_sh: bool = False,
        service: str | None = None,
        svc_start_rc: int = 0,
        svc_mark: str = "",
        provisioner_absent: bool = False,
        sudo_fails: bool = False,
        sudo_scoped: bool = False,
        os_label: str | None = None,
    ) -> None:
        self.os_label = os_label
        self.tmp = tmp
        self.root = tmp / "opt"
        self.root.mkdir(parents=True)
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.gh = _exe(self.bin / "gh", GH_FAKE)
        self.systemctl = _exe(self.bin / "systemctl", SYSTEMCTL_FAKE)
        self.provisioner = _exe(self.bin / "provision.sh", PROVISIONER_FAKE)
        self.ps = _exe(self.bin / "ps", f"#!/usr/bin/env bash\necho 'ps: boom' >&2\nexit {ps_rc}\n")
        # A `sudo` that refuses (no passwordless sudo — the shipped default on a dev box).
        # `sudo_scoped` instead models the COMMON least-privilege sudoers, a rule scoped to the
        # command (`NOPASSWD: /usr/bin/systemctl`): it permits the restart and denies anything else,
        # which is exactly what a `sudo -n true` pre-probe would misread as "no escalation".
        self.sudo_log = tmp / "sudo.log"
        self.sudo = _exe(
            self.bin / "sudo",
            "#!/usr/bin/env bash\n"
            f'printf "%s\\n" "sudo $*" >> "{tmp / "sudo.log"}"\n'
            + (
                '[ "${1:-}" = "-n" ] && shift\n'
                'case "$*" in *systemctl*restart*) exec "$@" ;; esac\n'
                "echo 'sudo: a password is required' >&2\nexit 1\n"
                if sudo_scoped
                else "echo 'sudo: a password is required' >&2\nexit 1\n"
            ),
        )
        self.meminfo = _meminfo(tmp / "meminfo", mem_mb, omit=omit_memavailable)
        self.pool_tsv = _pool(tmp / "pool.tsv", pool or [])
        self.restart_marker = tmp / "restarted"
        self.provisioner_log = tmp / "provisioner.log"
        self.config_log = tmp / "config.log"
        self.systemctl_log = tmp / "systemctl.log"
        self.svc_log = tmp / "svc.log"
        self.service = service
        self.svc_sh = svc_sh
        self.svc_start_rc = svc_start_rc
        self.svc_mark = svc_mark
        self.provisioner_absent = provisioner_absent
        self.sudo_fails = sudo_fails
        self.sudo_scoped = sudo_scoped
        self.state_dir = tmp / "state"
        self.alive_file = tmp / "alive.txt"
        self.busy_file = tmp / "busy.txt"
        self.busy_file.write_text("", encoding="utf-8")
        self.pool_read_count = tmp / "pool-reads.txt"
        self.ps_rc = ps_rc
        self.alive_seam = alive_seam
        self.ncpu = ncpu
        self.machine = machine
        self.systemctl_rc = systemctl_rc
        self.shrink_to = shrink_to
        self.enable_rc = 0
        self.provisioner_rc = provisioner_rc
        # The callee's OWN words on a refusal. `die` in install-ci-runner-x64.sh exits 2 from every
        # guard arm, and the tool reads these words to decide which arm it was (a sizing refusal
        # cites #9672; a disk/arch/fork refusal must not).
        self.provisioner_msg = provisioner_msg
        self.provisioner_adds = 1
        self.provisioner_alive = provisioner_alive
        self.provisioner_break_meminfo = provisioner_break_meminfo
        self.pool_fail_after: int | None = pool_fail_after
        dirs = []
        for dirname, name in installs or []:
            directory = _install(self.root, dirname, name, repo=install_repo)
            if name in (foreign or []):
                # another repo's runner on this box: it occupies capacity, but is never repaired
                (directory / ".runner").write_text(
                    json.dumps(
                        {"agentName": name, "gitHubUrl": "https://github.com/daniel-ospina/eldato"}
                    ),
                    encoding="utf-8-sig",
                )
            if svc_sh:
                _exe(directory / "svc.sh", SVC_SH_FAKE)
            dirs.append(str(directory))
        # Every fixture install is ALIVE by default: `running` is a measured process count in
        # production, and this seam is that measurement for the suite.
        for slot in gap_slots or []:
            # a bare directory with NO `.runner`: the provisioner treats it as free
            bare = self.root / ("gh-runner-x64" if slot == 1 else f"gh-runner-x64-{slot}")
            bare.mkdir(parents=True, exist_ok=True)
        live_dirs = [d for d in dirs if Path(d).name not in (not_alive or [])]
        self.not_alive = not_alive or []
        self.alive_file.write_text(
            "\n".join(live_dirs) + ("\n" if live_dirs else ""), encoding="utf-8"
        )

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "RUNNER_AUTOSCALE_GH": str(self.gh),
                "RUNNER_AUTOSCALE_SYSTEMCTL": str(self.systemctl),
                "RUNNER_AUTOSCALE_PROVISIONER": str(
                    self.bin / "absent.sh" if self.provisioner_absent else self.provisioner
                ),
                "RUNNER_AUTOSCALE_MEMINFO": str(self.meminfo),
                "RUNNER_AUTOSCALE_NPROC": str(self.ncpu),
                "RUNNER_AUTOSCALE_MACHINE": self.machine,
                "RUNNER_AUTOSCALE_STATE_DIR": str(self.state_dir),
                "RUNNER_AUTOSCALE_RUNNER_ROOT": str(self.root),
                "RUNNER_AUTOSCALE_PS": str(self.ps),
                # The suite normally runs rootless; `sudo_fails` keeps the escalation word in the
                # argv so the case where `sudo -n` cannot run is exercised (it must never be read
                # as "the restart failed").
                **({} if self.sudo_fails else {"RUNNER_AUTOSCALE_NO_SUDO": "1"}),
                "RUNNER_AUTOSCALE_ONLINE_WAIT_S": "0",  # no real sleeping in the suite
                "RUNNER_AUTOSCALE_POLL_S": "1",
                "POOL_TSV": str(self.pool_tsv),
                "RESTART_MARKER": str(self.restart_marker),
                "SYSTEMCTL_RC": str(self.systemctl_rc),
                "SYSTEMCTL_ENABLE_RC": str(self.enable_rc),
                "RUNNER_AUTOSCALE_BUSY_DIRS": str(self.busy_file),
                "POOL_FAIL_AFTER": ""
                if self.pool_fail_after is None
                else str(self.pool_fail_after),
                "POOL_READ_COUNT": str(self.pool_read_count),
                "SYSTEMCTL_LOG": str(self.systemctl_log),
                "SVC_LOG": str(self.svc_log),
                "SVC_START_RC": str(self.svc_start_rc),
                "SVC_MARK": self.svc_mark,
                "PROVISIONER_LOG": str(self.provisioner_log),
                "CONFIG_LOG": str(self.config_log),
                "PROVISIONER_SHRINK_TO": "" if self.shrink_to is None else str(self.shrink_to),
                "PROVISIONER_ADDS": str(self.provisioner_adds),
                "PROVISIONER_ALIVE": str(self.provisioner_alive),
                "PROVISIONER_BREAK_MEMINFO": "1" if self.provisioner_break_meminfo else "",
                "PROVISIONER_RC": str(self.provisioner_rc),
                "PROVISIONER_MSG": self.provisioner_msg,
            }
        )
        if self.service:
            env["RUNNER_AUTOSCALE_SERVICE"] = self.service
        else:
            env.pop("RUNNER_AUTOSCALE_SERVICE", None)
        if self.sudo_fails:
            env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '/usr/bin:/bin')}"
        if self.alive_seam:
            env["RUNNER_AUTOSCALE_ALIVE_DIRS"] = str(self.alive_file)
        else:
            env.pop("RUNNER_AUTOSCALE_ALIVE_DIRS", None)
        env.pop("RUNNER_AUTOSCALE_REPO", None)
        # ⛔ `resolve_labels` reads the operator's OS-label opt-in out of the environment — the same
        # channel `install-runner-autoscale-schedule.sh` renders into the timer — so a box (or CI
        # job) that exports it would red these tests for a reason unrelated to the code. The default
        # arm belongs to the fixture; each test that means an arm sets it, via `monkeypatch.setenv`
        # (read at call time, so it reaches the child).
        for leak in ("CI_RUNNER_OS_LABEL", "CI_RUNNER_OS_NAME", "CI_RUNNER_OS_RELEASE"):
            env.pop(leak, None)
        if self.os_label is not None:
            env["CI_RUNNER_OS_LABEL"] = self.os_label
        return env

    def run(self, *args: str, module: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(module or TOOL), *args],
            capture_output=True,
            text=True,
            env=self.env(),
            timeout=120,
        )

    def record(self, *args: str, module: Path | None = None) -> dict:
        proc = self.run("--json", *args, module=module)
        text = proc.stdout.strip() or proc.stderr.strip()
        try:
            rec = json.loads(text.splitlines()[-1]) if text else {}
        except (ValueError, IndexError):
            rec = {}
        rec["_rc"] = proc.returncode
        rec["_stderr"] = proc.stderr
        rec["_stdout"] = proc.stdout
        return rec

    def listeners(self) -> int:
        """The BOX's measured live-listener count, read from the measurement itself.

        ⛔ Never from the record: a mutant's own counter is exactly what a load-bearing gate must
        not trust (round 5's `foreign-listener-not-counted` predicate read the mutant's `live` and
        therefore could not see the over-fill it caused).
        """
        if not self.alive_file.exists():
            return 0
        return len([ln for ln in self.alive_file.read_text().splitlines() if ln.strip()])

    def provisioned(self) -> list[str]:
        if not self.provisioner_log.exists():
            return []
        return [ln for ln in self.provisioner_log.read_text().splitlines() if ln.strip()]

    def restarted(self) -> list[str]:
        if not self.systemctl_log.exists():
            return []
        return [
            ln for ln in self.systemctl_log.read_text().splitlines() if ln.startswith("restart")
        ]

    def service_calls(self) -> list[str]:
        """Every verb the tool asked `svc.sh` for (the launchd restart primitive)."""
        if not self.svc_log.exists():
            return []
        return [ln for ln in self.svc_log.read_text().splitlines() if ln.strip()]

    def config(self) -> str:
        return self.config_log.read_text() if self.config_log.exists() else ""


@pytest.fixture()
def env(tmp_path: Path):
    def make(**kwargs) -> Env:
        return Env(tmp_path / f"case{next(_counter)}", **kwargs)

    return make


# ── the ceiling predicate, directly ───────────────────────────────────────────────────────────
sys.path.insert(0, str(REPO_ROOT / "tools"))
import runner_autoscale as RA  # noqa: E402


@pytest.mark.parametrize("ncpu,expected", [(8, 3), (12, 3), (4, 2), (6, 2), (2, 1), (1, 1)])
def test_core_table_is_the_owner_ruling(ncpu, expected):
    """8 cores → 3, 4 cores → 2 — the owner's table, never a flat 3–5 (agent-infra#2344)."""
    assert RA.core_ceiling(ncpu) == expected


def test_memory_arm_is_additive_over_live_runners():
    """A box at 3 live with 5.5 GB free keeps its ceiling of 3 — the arm ADDS headroom.

    Non-additive arithmetic reads (5535−2048)//1500 = 2 and declares a healthy box over
    ceiling, a false red on exactly the box that is working.
    """
    ceil, bound, reading = RA.ceiling(8, 5535, running=3)
    assert (ceil, bound) == (3, "cores"), reading
    assert "by_cores=3 by_memory=5" in reading


def test_memory_arm_binds_and_reports_the_reading():
    """Memory binds on a small box: 4 cores, 3.0 GB free, nothing live → 0 runners fit."""
    ceil, bound, reading = RA.ceiling(4, 3000, running=0)
    assert (ceil, bound) == (0, "memory"), reading


# ── 1. measurement gates: unreadable is fail-closed ─────────────────────────────────────────
def _vm_stat(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_darwin_memory_is_a_measurement_not_a_refusal(tmp_path, monkeypatch):
    """macOS has no /proc/meminfo, so the launchd leg measured NOTHING and exited 2 forever.

    The installer arms a LaunchAgent on Darwin; a tool with only a Linux memory arm makes that
    schedule fire every 600 s into `UNMEASURABLE`, i.e. an inert enforcer on the platform the
    installer advertises. `vm_stat` is the macOS counterpart (free + inactive + speculative), and
    it carries its own page size — so one command is the whole reading, and a half-read one
    RAISES rather than reporting "plenty".
    """
    fake = _vm_stat(
        tmp_path / "vm_stat",
        "#!/usr/bin/env bash\n"
        "cat <<'EOF'\n"
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pages free:                               100000.\n"
        "Pages active:                             200000.\n"
        "Pages inactive:                            50000.\n"
        "Pages speculative:                         25000.\n"
        "EOF\n",
    )
    monkeypatch.setenv("RUNNER_AUTOSCALE_VMSTAT", str(fake))
    monkeypatch.delenv("RUNNER_AUTOSCALE_MEMINFO", raising=False)
    # 100000 + 50000 + 25000 pages × 16 KiB = 2734 MiB
    assert RA._darwin_available_mb() == 2734

    # An unparseable reading is NOT a number and never "plenty".
    broken = _vm_stat(tmp_path / "vm_stat-broken", "#!/usr/bin/env bash\necho 'nonsense'\n")
    monkeypatch.setenv("RUNNER_AUTOSCALE_VMSTAT", str(broken))
    with pytest.raises(RA.Unmeasurable):
        RA._darwin_available_mb()


def test_unreadable_memory_is_fail_closed_and_changes_nothing(env):
    """No MemAvailable line ⇒ exit 2, nothing added. An absent reading is never "plenty"."""
    e = env(omit_memavailable=True)
    rec = e.record()
    assert rec["_rc"] == 2, rec
    assert rec["verdict"] == "unmeasurable"
    assert e.provisioned() == [], "an unreadable measurement must not act"


def test_unreadable_cores_is_fail_closed(env):
    e = env()
    e.ncpu = "not-a-number"
    rec = e.record()
    assert rec["_rc"] == 2 and rec["verdict"] == "unmeasurable", rec
    assert e.provisioned() == []


def test_unreadable_pool_is_not_an_empty_pool(env):
    """A failed `gh` read must not be read as "no runners exist" — that re-registers everything."""
    e = env(installs=[("gh-runner-x64-1", "r1")])
    e.gh.write_text("#!/usr/bin/env bash\necho 'gh: boom' >&2\nexit 1\n", encoding="utf-8")
    e.gh.chmod(0o755)
    rec = e.record()
    assert rec["_rc"] == 2 and rec["verdict"] == "unmeasurable", rec
    assert e.config() == "", "a failed pool read must not re-register anything"


# ── 2. full pool adds nothing (idempotence) ─────────────────────────────────────────────────
def test_full_pool_adds_nothing_and_says_at_ceiling(env):
    """8 cores, 8 GB free, 3 live and online = its ceiling ⇒ no provisioner call, exit 0."""
    e = env(
        pool=[
            ("r1", "online", False, "self-hosted-x64"),
            ("r2", "online", False, "self-hosted-x64"),
            ("r3", "online", False, "self-hosted-x64"),
        ],
        installs=[("gh-runner-x64-1", "r1"), ("gh-runner-x64-2", "r2"), ("gh-runner-x64-3", "r3")],
    )
    rec = e.record()
    assert rec["_rc"] == 0, rec
    assert rec["verdict"] == "at-ceiling", rec
    assert rec["ceiling"] == 3
    assert e.provisioned() == [] and e.restarted() == []


def test_running_twice_is_idempotent(env):
    e = env(
        pool=[
            ("r1", "online", False, ""),
            ("r2", "online", False, ""),
            ("r3", "online", False, ""),
        ],
        installs=[("gh-runner-x64-1", "r1"), ("gh-runner-x64-2", "r2"), ("gh-runner-x64-3", "r3")],
    )
    first, second = e.record(), e.record()
    assert first["actions"] == [] and second["actions"] == [], (first["actions"], second["actions"])
    assert first["verdict"] == second["verdict"] == "at-ceiling"


# ── 3. fill: one at a time, the reading printed per step ────────────────────────────────────
def test_below_ceiling_fills_one_at_a_time_with_memory_readings(env):
    """8 cores, 8 GB free, ONE live runner ⇒ ceiling 3, reached WITHOUT exceeding it.

    The provisioner is asked for its next install index and the box is RE-MEASURED after every
    step. Here one call creates TWO listeners (the installer numbers 1..N and this box was behind
    by one), which is exactly why the decision must read the measurement: the loop stops at 3 and
    reports 3 — it does not assume +1 and ask again (round 3, finding 1).
    """
    e = env(pool=[("r1", "online", False, "self-hosted-x64")], installs=[("gh-runner-x64-1", "r1")])
    rec = e.record()
    assert rec["_rc"] == 0, rec
    assert rec["verdict"] == "converged"
    assert rec["ceiling"] == 3
    assert rec["live"] == 3, rec
    steps = [a for a in rec["actions"] if "live listeners " in a]
    assert len(steps) >= 1, rec["actions"]
    assert all(a.startswith("--count ") for a in e.provisioned()), e.provisioned()


def test_a_provisioner_that_adds_nothing_does_not_report_convergence(env):
    """One call may add several listeners or NONE — the decision must come from a measurement.

    `--count N` means "ensure installs 1..N", so assuming +1 per call both over-fills (a call that
    adds three) and under-fills (a call that adds none but is counted). With nothing added the pass
    must STOP, say so, and report `under-target` — never `converged` (round 3, finding 1).
    """
    e = env(pool=[("r1", "online", False, "self-hosted-x64")], installs=[("gh-runner-x64-1", "r1")])
    e.provisioner_adds = 0
    rec = e.record()
    assert rec["verdict"] == "under-target", rec
    # ⛔ AND THE EXIT CODE SAYS SO: the pass TRIED (a provisioner ran) and the pool did not grow,
    # so exit 0 would report "converged" to everything that gates on the status — the systemd
    # timer's own success line, a controller reading the last run, `--status`'s probe. Named
    # `problem_kind=no-listener` so the code keys on the CAUSE: a `--status`/`--dry-run` plan and
    # a memory-reserve stop also end `under-target` by design and must stay exit 0 (pinned below).
    assert rec["problem_kind"] == "no-listener", rec
    assert rec["_rc"] == 4, rec


def test_a_status_read_that_ends_under_target_is_not_a_failed_schedule(env):
    """`--status`/`--dry-run` change NOTHING by design, so they cannot be a failed pass.

    The installer's install-time probe runs exactly `--json --status` and reads a non-zero exit
    as "this schedule would fire and do NOTHING" (install-runner-autoscale-schedule.sh, the
    `probe:` block). Mapping the `under-target` VERDICT to a non-zero code — the obvious fix for
    the exit-0 hole above — would therefore red the install probe on every healthy box that is
    simply below its target, which is most of them most of the time. This pin is that edge.
    """
    e = env(pool=[("r1", "online", False, "self-hosted-x64")], installs=[("gh-runner-x64-1", "r1")])
    e.provisioner_adds = 0
    rec = e.record("--status")
    assert rec["dry_run"] is True, rec
    assert rec["verdict"] == "under-target", rec
    assert "problem_kind" not in rec or rec["problem_kind"] != "no-listener", rec
    assert rec["_rc"] == 0, rec
    assert e.provisioned() == [], e.provisioned()


def test_a_capacity_stop_under_target_is_a_refusal_not_a_failure(env):
    """A box that stops at its own memory reserve is REFUSED by physics, not failing the guard.

    This is the capacity counterpart of `fill-blocked`, which the EXIT contract keeps at 0 on
    purpose ("a refusal is an answer"). Pinned so the exit-4 cause cannot quietly widen into
    "every under-target", which would red the timer on a box that needs a bigger machine rather
    than a repair.
    """
    e = env(mem_mb=6700, ncpu=8, shrink_to=2000)
    rec = e.record()
    assert rec["verdict"] == "under-target", rec
    assert rec["live"] < rec["ceiling"], rec
    assert any("reserve_mb" in n for n in rec["notes"]), rec["notes"]
    assert rec.get("problem_kind") != "no-listener", rec
    assert rec["_rc"] == 0, rec


def test_fill_re_measures_and_stops_before_starving_a_shard(env):
    """The per-step guard, with the reading actually moving: 5.2 GB → 2 runners fit; after the
    first addition leaves 2.0 GB, the second is refused (2.0−1.5 < 2.05)."""
    e = env(mem_mb=5200, ncpu=4, shrink_to=2000)
    rec = e.record()
    assert rec["_rc"] == 0, rec
    assert rec["ceiling"] == 2, rec
    assert len(e.provisioned()) == 1, e.provisioned()
    assert any("reserve_mb" in n for n in rec["notes"]), rec["notes"]


def test_low_memory_cell_refuses_and_prints_its_reading(env):
    """3.2 GB free on a 4-core box ⇒ ceiling 0; the refusal must carry the arithmetic."""
    e = env(ncpu=4, mem_mb=3200)
    rec = e.record()
    assert rec["_rc"] == 0, rec
    assert rec["ceiling"] == 0, rec
    assert e.provisioned() == []
    assert "bound=memory" in rec["notes"][-1], rec["notes"]


def test_want_above_ceiling_is_refused_with_the_reading(env):
    """The explicit-request form of the guard: `--want 5` on an 8-core box is exit 1, no action."""
    e = env(mem_mb=30000, pool=[("r1", "online", False, "")], installs=[("gh-runner-x64-1", "r1")])
    rec = e.record("--want", "5")
    assert rec["_rc"] == 1, rec
    assert rec["verdict"] == "refused"
    assert "ceiling 3" in rec["refusal"] and "ncpu=8" in rec["refusal"], rec["refusal"]
    assert e.provisioned() == []


def test_over_ceiling_is_reported_and_nothing_is_removed(env):
    """Three live installs where only one may be: reported, never deleted, never "repaired"."""
    e = env(
        ncpu=2,
        mem_mb=4000,
        pool=[
            ("r1", "online", False, ""),
            ("r2", "online", False, ""),
            ("r3", "online", False, ""),
        ],
        installs=[("gh-runner-x64-1", "r1"), ("gh-runner-x64-2", "r2"), ("gh-runner-x64-3", "r3")],
    )
    rec = e.record()
    assert rec["verdict"] == "over-ceiling", rec
    assert rec["ceiling"] == 1
    # Exit 0 is the RECORDED choice, not an accident: a live count above the measured ceiling
    # removes nothing (a runner that may be serving is never removed), so there is no action this
    # pass could have failed at. Pinned here because the EXIT block now says so out loud.
    assert rec["_rc"] == 0
    assert e.provisioned() == [] and e.restarted() == []


# ── 4. repair: dead runner, orphaned listener, serving protection ───────────────────────────
def test_dead_runner_is_restarted_and_not_replaced(env):
    """Offline in the pool ⇒ restart its unit; when it comes back online, NO re-registration."""
    e = env(
        pool=[("r1", "offline", False, "self-hosted-x64")], installs=[("gh-runner-x64-1", "r1")]
    )
    rec = e.record()
    assert rec["_rc"] == 0, rec
    assert any(a.startswith("restart r1") for a in rec["actions"]), rec["actions"]
    assert any("came back online" in a for a in rec["actions"]), rec["actions"]
    assert e.config() == "", "a successful restart must not re-register (that duplicates)"


def test_dead_runner_that_does_not_recover_is_re_registered(env):
    """A restart that fails falls through to `--replace` under the SAME name (adoption)."""
    e = env(
        pool=[("r1", "offline", False, "self-hosted-x64")],
        installs=[("gh-runner-x64-1", "r1")],
        systemctl_rc=1,
    )
    rec = e.record()
    assert any("re-registering with --replace" in a for a in rec["actions"]), rec["actions"]
    assert "--replace" in e.config() and "--name r1" in e.config(), e.config()


def test_orphaned_listener_is_re_registered(env):
    """A local install missing from the pool is ORPHANED: it reconnects forever, serves nothing."""
    e = env(pool=[], installs=[("gh-runner-x64-1", "pi-runner-ghost")])
    rec = e.record()
    assert rec["runners"][0]["state"] == "ORPHANED", rec
    assert any("registered pi-runner-ghost" in a for a in rec["actions"]), rec["actions"]
    assert "--replace" in e.config()


def test_busy_runner_is_never_touched(env):
    """A runner serving a job is BUSY: no restart, no relabel, no removal."""
    e = env(pool=[("r1", "online", True, "self-hosted-x64")], installs=[("gh-runner-x64-1", "r1")])
    rec = e.record()
    assert rec["runners"][0]["state"] == "BUSY", rec
    assert e.restarted() == [] and e.config() == ""


def test_serving_runner_is_never_restarted(env):
    e = env(pool=[("r1", "online", False, "")], installs=[("gh-runner-x64-1", "r1")])
    rec = e.record()
    assert rec["runners"][0]["state"] == "SERVING", rec
    assert e.restarted() == []


# ── 5. labels ───────────────────────────────────────────────────────────────────────────────
def test_another_repos_runner_is_reported_and_never_touched(env):
    """A local install whose `.runner` names ANOTHER repo is absent from THIS repo's pool.

    Treating that absence as ORPHANED re-registers it against `--repo` with `--replace`, which
    deletes the other repo's registration and cancels whatever it is running (review round 1,
    finding 1). It must be reported and left alone.
    """
    e = env(pool=[], installs=[("gh-runner-x64-1", "eldato-runner-1")])
    (e.root / "gh-runner-x64-1" / ".runner").write_text(
        json.dumps(
            {
                "agentName": "eldato-runner-1",
                "gitHubUrl": "https://github.com/daniel-ospina/eldato/",
            }
        ),
        encoding="utf-8-sig",
    )
    rec = e.record()
    assert rec["runners"][0]["state"] == "OTHER-REPO", rec
    assert RA._repo_slug(rec["runners"][0]["repo"]) == "daniel-ospina/eldato", rec
    assert e.config() == "", "another repo's registration must never be replaced"
    assert not any("eldato-runner-1" in a for a in rec["actions"]), rec["actions"]


def test_worker_alive_install_absent_from_the_pool_is_not_re_registered(env):
    """A listener executing a job is BUSY even when the pool does not list it.

    `--replace` on that registration would cancel the job in flight — the one action the file
    promises never to take on a runner that may be serving (review round 1, finding 2).
    """
    e = env(pool=[], installs=[("gh-runner-x64-1", "ghost")])
    (e.tmp / "busy.txt").write_text(str(e.root / "gh-runner-x64-1") + "\n", encoding="utf-8")
    rec = e.record()
    assert rec["runners"][0]["state"] == "BUSY", rec
    assert e.config() == "", "a working listener must not be re-registered"


def test_repair_preserves_the_labels_the_runner_already_has(env):
    """Re-registering keeps the runner's own labels (minus the host-derived ones).

    A derived set drops a custom/tier label (and the fallback label when it is engaged), so every
    workflow targeting it queues forever — a "repair" that removes capacity, and it is the
    DERIVATION that the work order forbids (review round 1, finding 3). The arm64 hosted spelling is
    the one label dropped on purpose, not kept — see
    `test_repair_keeps_the_os_label_and_drops_the_arm64_spelling`.
    """
    e = env(
        pool=[("r1", "offline", False, "self-hosted,Linux,X64,self-hosted-x64,ubuntu-latest,gpu")],
        installs=[("gh-runner-x64-1", "r1")],
        systemctl_rc=1,
    )
    e.record()
    cfg = e.config()
    assert "--labels self-hosted-x64,ubuntu-latest,gpu" in cfg, cfg
    assert "self-hosted," not in cfg and "X64" not in cfg, (
        f"host-derived labels cannot be set: {cfg}"
    )


def test_repair_drops_the_version_pinned_class_and_keeps_every_custom_label(env):
    """A re-registered runner drops the WHOLE `ubuntu-<version>` class and keeps everything else.

    ⛔ THIS IS #10298'S LANDED RULE, AND #10275 DID NOT GET TO REVERSE IT. A re-registration derives
    a label set for a runner the pool still knows, so it carries the class FORWARD only if
    `_preserve_labels` keeps it — and keeping it is how a label attached once out of band becomes
    permanent. The rule is therefore unchanged: the whole family (`ubuntu-24.04` AND
    `ubuntu-24.04-arm`) is dropped, because a job naming either wants the GitHub-hosted image.

    ⛔ THE RESIDUAL IS NAMED, NOT HIDDEN (agent-infra #10275). The installer can now attach
    `ubuntu-24.04` (`CI_RUNNER_OS_LABEL=on|auto`), so a runner re-registered by THIS path loses a pin
    it legitimately carried, and nothing here re-attaches it. A deliberate `--repair-labels` pass
    does (with a resolution that CLAIMS it — `on`, or `auto` on a 24.04 host — it KEEPS `$OS_LABEL`
    and DROPS the rest of the family with a `NOT KEPT:` line, while a declining `auto` or `off` drops
    the whole class — the narrowing is in `tools/install-ci-runner-x64.sh`), a fresh install does,
    and `--status`' `label-coverage` exits
    5 when the pool is asked for the label and answers with none — so the loss is reported, not
    silent. Pinning the DROP here is what keeps that report the only way the label comes back.

    `ubuntu-2204-tools` is the opposite boundary: a CUSTOM label that merely starts
    `ubuntu-<digits>`, and it must NOT be eaten by a widened match.
    """
    e = env(
        pool=[("r1", "offline", False, POOL_LABELS_WITH_VERSION_PINS)],
        installs=[("gh-runner-x64-1", "r1")],
        systemctl_rc=1,
    )
    e.record()
    cfg = e.config()
    # The argv's OWN label list, read back exactly: a substring test cannot tell `ubuntu-24.04` from
    # `ubuntu-24.04-arm`, and the pair is half the point of this arm.
    kept = set(cfg.split("--labels ")[1].split(" ")[0].split(","))
    assert kept == {
        "self-hosted-x64",
        "ubuntu-latest",
        "gpu",
        "ubuntu-2204-tools",
    }, (
        "a re-registered runner drops the whole version-pinned class (keeping it makes an "
        "out-of-band pin permanent) and must not lose a custom label (which starves its own jobs); "
        f"`ubuntu-latest` is the fallback label and is not in the class: {cfg}"
    )
    assert "ubuntu-24.04" not in kept and "ubuntu-24.04-arm" not in kept, (
        f"no version-pinned hosted label may ride a re-registration: {cfg}"
    )


def test_preserve_labels_drops_the_version_pinned_class_and_the_arch_marker():
    """The branches of `_preserve_labels`, asked directly rather than through a repair pass.

    The schedule is what makes this worth its own arm: `tools/install-runner-autoscale-schedule.sh`
    re-registers repaired runners with nobody reading the output, so a label dropped here disappears
    with no reader outside `--status`' `label-coverage` exit 5.

    ⛔ ONE CLASS, EVERY ARCH: a version-pinned hosted label (`ubuntu-24.04`, `ubuntu-24.04-arm`) is
    dropped whatever the box's architecture, because the label names an IMAGE and a job that wants
    it wants GitHub-hosted. The arch MARKER is re-derived from this box instead — a mis-registered
    runner can carry the other arch's marker, and passing it straight back is the false PASS (an
    arm64 job built on x86) this function exists to prevent — GitHub derives the marker from the
    host at registration and it cannot be set by hand, so the ARM64 VMs register their own markers (`:54-55`, `docs/ops/ci-runner-x64.md`) and this box registers its own.
    """
    for label in ("ubuntu-24.04", "ubuntu-24.04-arm"):
        assert RA._preserve_labels(["self-hosted-x64", label], "x86_64") == (
            "self-hosted-x64"
        ), f"a version-pinned hosted label ({label}) must not survive a re-registration"
        assert RA._preserve_labels(["self-hosted-arm64", label], "aarch64") == (
            "self-hosted-arm64"
        ), f"the class is dropped on every arch ({label}) — it names an image, not an architecture"

    # a custom label that merely STARTS `ubuntu-<digits>` is not in the class
    assert RA._preserve_labels(
        ["self-hosted-x64", "gpu", "ubuntu-2204-tools"], "x86_64"
    ) == "self-hosted-x64,gpu,ubuntu-2204-tools", (
        "the class boundary is `<version>` (and `-arm`), not the `ubuntu-` prefix"
    )
    # the marker is RE-DERIVED, never carried across architectures
    assert RA._preserve_labels(["self-hosted-arm64", "gpu"], "x86_64") == (
        "self-hosted-x64,gpu"
    ), "a foreign arch marker must be replaced with this box's own"


def test_a_custom_host_shaped_label_survives_re_registration_exactly_as_the_repair_keeps_it(env):
    """⛔ ONE INVARIANT, TWO WRITERS — SO THEY MUST COMPARE A LABEL THE SAME WAY.

    `--repair-labels` matches the host-derived names EXACTLY (`is_host_label` in
    `tools/install-ci-runner-x64.sh`: `self-hosted|Linux|macOS|Windows|X64|ARM64|ARM`), so an
    operator's own `linux` or `x64` custom label is KEPT — filtering a label out of that PUT body
    IS a removal, the one thing that block must never do. This writer compared case-INSENSITIVELY,
    so the same label was silently DELETED from `--labels` on the next re-registration: a label one
    writer keeps and the other removes, which is exactly what the shared `POOL_LABELS` fixture's
    "one invariant, two writers" comment says cannot happen.

    GitHub returns the reserved spellings (`Linux`, `X64`, …) and refuses to have them re-set, so
    an exact-case compare loses nothing from the class — the host trio below is still dropped.

    Non-vacuity: with the lower-cased compare restored (`x.lower() not in host_derived`) both
    custom labels disappear from `--labels` and this arm reds.
    """
    e = env(
        pool=[
            (
                "r1",
                "offline",
                False,
                "self-hosted,Linux,X64,self-hosted-x64,ubuntu-latest,linux,x64",
            )
        ],
        installs=[("gh-runner-x64-1", "r1")],
        systemctl_rc=1,
    )
    e.record()
    cfg = e.config()
    kept = set(cfg.split("--labels ")[1].split(" ")[0].split(","))
    assert kept == {"self-hosted-x64", "ubuntu-latest", "linux", "x64"}, (
        "a re-registration keeps a CUSTOM label the repair keeps too — a case-folded host match "
        "drops the operator's own `linux`/`x64` here while `--repair-labels` preserves it, and a "
        f"label one writer deletes is not a shared invariant: {cfg}"
    )
    assert "Linux" not in kept and "X64" not in kept, (
        "the host-derived spellings cannot be set by hand and must stay out of `--labels`: " + cfg
    )


@pytest.mark.parametrize("marker", ["Self-Hosted-Arm64", "SELF-HOSTED-ARM64"])
def test_a_mixed_case_foreign_arch_marker_is_not_preserved(env, marker):
    """⛔ THE ARCH-MARKER TEST IS CASE-INSENSITIVE, DELIBERATELY — unlike the host-derived one.

    The two classes are not the same, and the neighbour above is what makes the difference easy to
    get wrong: `Linux`/`X64` are vendor-FIXED spellings GitHub derives from the host (an exact-case
    compare loses nothing), while `self-hosted-<arch>` is a CUSTOM label whose spelling is whatever
    created it. docs.github.com "apply labels" says **"Labels are case-insensitive"**, so
    `Self-Hosted-Arm64` IS the arm64 marker here and still answers a lower-case `runs-on:` —
    preserving it on re-registration hands an arm64 job to an x86 box and builds a wrong artifact,
    the false PASS this function's own docstring forbids.

    Non-vacuity: with the exact-case prefix test restored
    (`not x.startswith("self-hosted-")`) both spellings are preserved and this arm reds.
    """
    e = env(
        pool=[
            (
                "r1",
                "offline",
                False,
                f"self-hosted,Linux,X64,self-hosted-x64,ubuntu-latest,gpu,{marker}",
            )
        ],
        installs=[("gh-runner-x64-1", "r1")],
        systemctl_rc=1,
    )
    e.record()
    cfg = e.config()
    kept = set(cfg.split("--labels ")[1].split(" ")[0].split(","))
    assert marker not in kept, (
        "GitHub matches labels case-insensitively, so a mixed-case spelling of the OTHER arch's "
        f"marker is that marker — it must be re-derived away, not preserved: {cfg}"
    )
    assert kept == {"self-hosted-x64", "ubuntu-latest", "gpu"}, (
        "the custom labels still survive; only the foreign arch marker is dropped: " + cfg
    )


def test_another_repos_listener_still_counts_against_the_boxes_ceiling(env):
    """A foreign listener occupies the box, so it counts in `live` — no over-fill.

    `ceiling` is a BOX total (its memory arm is additive over every listener alive), so the fill
    decision must compare like with like. Counting only our own runners made a box with one
    foreign listener add ceil−0 more: 8 cores allows 3, and the box ended with 4 (round 2,
    finding 1).
    """
    e = env(
        pool=[],
        installs=[("gh-runner-x64-1", "eldato-runner-1")],
        install_repo="https://github.com/daniel-ospina/eldato",
    )
    rec = e.record()
    assert rec["ceiling"] == 3, rec
    # one foreign listener already occupies the box ⇒ two additions, not three
    assert all(a.startswith("--count ") for a in e.provisioned()), e.provisioned()
    assert rec["live"] <= rec["ceiling"], f"over-filled the box: {rec['live']} > {rec['ceiling']}"


def test_a_listener_outside_the_install_glob_still_counts(env):
    """`running`/`live` are the MEASURED listener count over the BOX, not over `discover()`.

    `discover()` only names `<root>/gh-runner*`, so deriving the fill operand from it read
    `running=0 live=0` on a box whose listeners live elsewhere (`/opt/actions-runner`, the upstream
    default, or a `--root` that is not the install root) and filled the FULL core ceiling on top
    of them — then reported `converged`, because the same narrow count was used afterwards
    (round 9, finding 4).
    """
    e = env(pool=[], installs=[])
    strays = []
    for i in range(3):  # three listeners the install inventory cannot name
        d = e.tmp / "opt2" / ("actions-runner" if i == 0 else f"actions-runner-{i + 1}")
        d.mkdir(parents=True)
        strays.append(str(d))
    e.alive_file.write_text("\n".join(strays) + "\n", encoding="utf-8")
    rec = e.record()
    assert rec["ceiling"] == 3, rec
    assert rec["live"] == 3, f"the measured listener count was thrown away: {rec}"
    assert e.provisioned() == [], (
        f"filled a box that already held three listeners: {e.provisioned()}"
    )


def test_a_repair_never_inherits_the_previous_runners_labels(env):
    """Each repair derives its OWN label set — and never an other-arch marker.

    A cached `labels` variable leaked the first runner's set (including `self-hosted-arm64`) onto
    every later repair in the pass (round 2, finding 3), so an orphaned runner registered with a
    foreign arch marker on an x86 box: the false PASS the module header forbids.
    """
    e = env(
        pool=[("r1", "offline", False, "self-hosted,Linux,ARM64,self-hosted-arm64,gpu")],
        installs=[("gh-runner-x64-1", "r1"), ("gh-runner-x64-2", "ghost")],
        systemctl_rc=1,
    )
    e.record()
    lines = [ln for ln in e.config().splitlines() if ln.strip()]
    by_name = {ln.split("--name ")[1].split()[0]: ln for ln in lines if "--name " in ln}
    assert "gpu" in by_name["r1"], by_name
    assert "self-hosted-arm64" not in by_name["r1"], (
        f"an x86 box must not claim arm64: {by_name['r1']}"
    )
    assert "self-hosted-x64" in by_name["r1"], by_name
    # the ORPHANED runner gets its own derived set, never r1's
    assert "gpu" not in by_name["ghost"], f"ghost inherited r1's labels: {by_name['ghost']}"
    assert "--labels self-hosted-x64 " in by_name["ghost"], by_name["ghost"]


def test_an_unreadable_process_list_is_fail_closed(env):
    """`ps` is a measurement too: its failure must not read as "zero listeners".

    It was the one arm that degraded to a value, so a broken `ps` made the guard add a runner to a
    box already at its ceiling (and printed `running=0` as if it were a reading) — round 7,
    finding 1.
    """
    e = env(
        pool=[
            ("r1", "online", False, ""),
            ("r2", "online", False, ""),
            ("r3", "online", False, ""),
        ],
        installs=[("gh-runner-x64", "r1"), ("gh-runner-x64-2", "r2"), ("gh-runner-x64-3", "r3")],
        alive_seam=False,
        ps_rc=1,
    )
    rec = e.record()
    assert rec["_rc"] == 2, rec
    assert rec["verdict"] == "unmeasurable", rec
    # ⛔ `error`, not `_error`: `Env.record()` adds only `_rc`/`_stdout`/`_stderr`, so the old
    # `assert ... in rec["_error"] if "_error" in rec else True` was `assert True` — the message
    # this test is named for was never checked (round 9, finding 5).
    assert "process list unreadable" in rec["error"], rec
    assert e.provisioned() == [], "an unreadable process list must not add a runner"


def test_a_slot_holding_no_runner_config_is_free(env):
    """The provisioner's own test is `.runner`, not "the path exists" — so must ours be.

    A directory left by an interrupted download (or a deregistered install) is invisible to
    `discover()` and FREE to the installer. Calling it occupied skipped past it, and the one call
    then filled the gap AND the requested slot: two runners where the ceiling allowed one (round 6,
    finding 1).
    """
    e = env(
        pool=[("r1", "online", False, ""), ("r2", "online", False, "")],
        installs=[("gh-runner-x64", "r1"), ("gh-runner-x64-2", "r2")],
    )
    (e.root / "gh-runner-x64-3").mkdir()  # no .runner: the provisioner will fill it
    rec = e.record()
    asked = [ln.split("--count ")[1].split()[0] for ln in e.provisioned()]
    assert asked and asked[0] == "3", f"skipped the free slot: {asked}"
    assert rec["live"] <= rec["ceiling"], f"over-filled: {rec}"


def test_a_mid_pass_failure_does_not_blame_a_restart(env):
    """The `unmeasurable` error must name the measurement that actually failed.

    One fixed sentence said "the pool could not be read after a restart" even for a pass with no
    restart and a failed MEMORY read (round 6, finding 3).
    """
    e = env(pool=[], installs=[])
    e.provisioner_break_meminfo = 1
    rec = e.record()
    assert rec["verdict"] == "unmeasurable", rec
    assert "restart" not in rec["error"], rec["error"]
    assert "re-measured after an install" in rec["error"], rec["error"]


def test_installs_without_a_listener_do_not_loop_forever(env):
    """A pass must terminate even when installs appear and no listener stays up.

    Measuring only the INSTALL count made that state loop forever — hundreds of installer calls in
    one pass, bounded only by the systemd unit's timeout (round 5, finding 2).
    """
    e = env(pool=[], installs=[])
    e.provisioner_alive = 0
    rec = e.record()
    assert len(e.provisioned()) == 1, f"unbounded fill loop: {len(e.provisioned())} calls"
    assert any("no listener came up" in n for n in rec["notes"]), rec["notes"]
    assert rec["verdict"] == "under-target", rec
    assert rec["_rc"] == 4, rec


def test_a_mid_pass_measurement_failure_keeps_what_already_happened(env):
    """After a real install, "nothing changed" would be false — the record must carry the action.

    The memory read happens AFTER provisioning, so its failure must not erase the fact that a
    runner was created and registered (round 5, finding 3).
    """
    e = env(pool=[], installs=[])
    e.provisioner_break_meminfo = 1
    rec = e.record()
    assert rec["_rc"] == 2, rec
    assert rec["verdict"] == "unmeasurable", rec
    assert any("already happened" in n or "already performed" in n for n in rec["notes"]), rec
    assert "nothing changed" not in rec.get("error", ""), rec
    assert any("post-step measurement FAILED" in a for a in rec["actions"]), rec["actions"]


def test_the_fill_note_never_contradicts_the_action_above_it(env):
    """`grown` must compare INSTALLS with INSTALLS, not live listeners with installs.

    A box with a DEAD install (no live listener) used to compute a negative delta, then print
    "the provisioner added no listener" directly under an action line that had just added one, and
    stop a step short (round 4, finding 1).
    """
    e = env(
        pool=[("r1", "online", False, "")],
        installs=[("gh-runner-x64-1", "r1"), ("gh-runner-x64-2", "dead")],
        not_alive=["gh-runner-x64-2"],
    )
    rec = e.record()
    assert e.provisioned(), "a box below its ceiling must attempt an addition"
    assert not any("no listener came up" in n for n in rec["notes"]), (
        f"contradicted its own action: {rec['actions']} / {rec['notes']}"
    )
    assert rec["live"] <= rec["ceiling"], rec


def test_the_requested_install_index_is_repo_agnostic(env):
    """The installer numbers installs regardless of repo, so the index must too.

    Asking for a slot another repo's install already occupies makes the installer "reuse" it and
    start THAT repo's runner — the box then stays below its ceiling forever (round 4, finding 2).
    """
    e = env(
        pool=[],
        installs=[("gh-runner-x64", "ours-1"), ("gh-runner-x64-2", "fx")],
        foreign=["fx"],
    )
    rec = e.record()
    asked = [ln.split("--count ")[1].split()[0] for ln in e.provisioned()]
    assert "2" not in asked, f"asked for the slot a foreign install occupies: {asked}"
    assert rec["live"] <= rec["ceiling"], rec


def test_a_provisioner_refusal_is_VISIBLE_not_green(env):
    """`install-ci-runner-x64.sh` exit 2 is its GUARD REFUSING — and that must NOT read as success.

    The refusal means the box is UNDER target and the one sanctioned registration path will not
    fill it. Folding it into `action-failed` made a scheduled pass cry failure over a deliberate
    no; folding it into exit 0 — the pre-#9685 contract — made the timer GREEN on an
    under-filled box, which is the false PASS this file exists to prevent. It is therefore its own
    verdict and its own non-zero code, keyed on the CAUSE so a `--status` plan and a memory-reserve
    stop keep exit 0.
    """
    e = env(pool=[("r1", "online", False, "")], installs=[("gh-runner-x64-1", "r1")])
    e.provisioner_rc = 3
    rec = e.record()
    # rc=3 is a genuine failure; rc=2 is the refusal
    assert rec["verdict"] == "action-failed", rec
    e2 = env(pool=[("r1", "online", False, "")], installs=[("gh-runner-x64-1", "r1")])
    e2.provisioner_rc = 2
    rec2 = e2.record()
    assert rec2["verdict"] == "fill-refused-by-provisioner", rec2
    assert rec2["_rc"] == 5, rec2
    notes = " ".join(rec2["notes"])
    assert "fill-refused-by-provisioner: slot #" in notes and "rc=2" in notes, rec2["notes"]


def test_a_NON_sizing_provisioner_refusal_is_not_blamed_on_the_model_disagreement(env):
    """⛔ `die` EXITS 2 FROM EVERY GUARD ARM, so exit 2 alone does NOT mean the sizing dispute.

    `tools/install-ci-runner-x64.sh:234` is `die() { say "REFUSED: $1"; exit "${2:-2}"; }` — the
    wrong architecture, an unreadable fork cap, a FULL DISK, an unreadable core count and an
    unreadable gate state all refuse with 2. Naming #9672 for those would describe a disk-full box
    as "expected until the decision lands" and nobody would ever fix it. The classification is
    therefore keyed on the CALLEE'S OWN WORDS, and this fixture's callee refuses on DISK — the arm
    this box's real installer hits first (measured: 5 G free against 36 G needed for 2 runners).
    """
    e = env(
        pool=[("r1", "online", False, "")],
        installs=[("gh-runner-x64-1", "r1")],
        provisioner_rc=2,
        provisioner_msg="REFUSED: disk headroom: 4G free against 36G needed for 2 runners",
    )
    rec = e.record()
    notes = " ".join(rec["notes"])
    assert rec["verdict"] == "fill-refused-by-provisioner", rec
    assert rec["_rc"] == 5, rec
    # The callee's own arm is quoted for the operator, and the open decision is NOT cited.
    assert "disk headroom" in notes, rec["notes"]
    assert "NOT the sizing disagreement at #9672" in notes, rec["notes"]
    assert "2 cores + 2 reserved" not in notes, rec["notes"]


def test_a_SIZING_provisioner_refusal_names_both_models_and_the_open_decision(env):
    """The one arm that IS the two-models disagreement: quote it, and cite the decision (#9672).

    The CPU arm is the one no memory reading can move — `COUNT*2 + 2 <= ncpu` caps a 4-core box at
    ONE while the owner's table targets 2 — and it was the arm the earlier prose never mentioned.
    """
    e = env(
        pool=[("r1", "online", False, "")],
        installs=[("gh-runner-x64-1", "r1")],
        provisioner_rc=2,
        provisioner_msg=(
            "REFUSED: cpu headroom: 2 runners need 2x2 cores + 2 reserved for agent lanes = 6, "
            "this box has 4"
        ),
    )
    rec = e.record()
    notes = " ".join(rec["notes"])
    assert rec["verdict"] == "fill-refused-by-provisioner", rec
    assert "2 cores + 2 reserved" in notes, rec["notes"]
    assert "1.5 GB + 2 GB" in notes, rec["notes"]
    assert "#9672" in notes, rec["notes"]
    assert "not the binding predicate" in notes, rec["notes"]


def test_dry_run_promises_only_what_it_can_measure(env):
    """A plan can say which call it WOULD make; it cannot predict the listeners that yields."""
    e = env(
        pool=[("r1", "online", False, "")],
        installs=[("gh-runner-x64-1", "r1"), ("gh-runner-x64-2", "dead")],
        not_alive=["gh-runner-x64-2"],
    )
    rec = e.record("--dry-run")
    assert e.provisioned() == [], "a dry run must not provision"
    steps = [a for a in rec["actions"] if a.startswith("would ask the provisioner")]
    assert len(steps) == 1, rec["actions"]
    assert "MEASURED by a real pass" in steps[0], steps
    assert rec["verdict"] != "converged", rec


def test_unverified_record_does_not_claim_nothing_happened(env):
    """The record must be TRUE about what already happened before the unread pool.

    An ORPHANED repair earlier in the classed order (alphabetical) runs before the DEAD restart
    whose pool read then fails. Saying "no runner was registered" would be a false "nothing
    changed" about the one action that can delete a registration (round 3, finding 2).
    """
    e = env(
        pool=[("r1", "offline", False, "self-hosted-x64")],
        installs=[("gh-runner-x64-1", "ghost"), ("gh-runner-x64-2", "r1")],
    )
    e.pool_fail_after = 1
    rec = e.record()
    assert rec["verdict"] == "unmeasurable", rec
    assert "--name ghost" in e.config(), "the orphan repair happened before the unread pool"
    assert "no runner was registered" not in rec["error"], rec["error"]
    assert "already performed BEFORE" in rec["error"], rec["error"]


def test_unreadable_pool_after_a_restart_never_re_registers(env):
    """ "We could not look" is not "it is still offline" (review round 1, finding 4).

    The first pool read classifies r1 DEAD; the restart succeeds; every later read fails. The
    guard must report unmeasurable (exit 2) and touch nothing — not assert a fact it never read
    and replace the registration of a runner that may be reconnecting.
    """
    e = env(
        pool=[("r1", "offline", False, "self-hosted-x64")], installs=[("gh-runner-x64-1", "r1")]
    )
    e.pool_fail_after = 1
    rec = e.record()
    assert rec["_rc"] == 2, rec
    assert rec["verdict"] == "unmeasurable", rec
    assert e.config() == "", "an unread pool must not become a --replace"
    assert e.provisioned() == [], "and it must not provision either (the count is unknown)"
    assert rec["actions"] and rec["actions"][0].startswith("restart r1"), rec["actions"]


def test_registration_without_an_enabled_unit_fails_loudly(env):
    """A registered runner whose unit never came up is NOT live and NOT `converged`.

    Counting it live both hides a runner that will never start and skips an addition the box
    still needs (review round 1, finding 5).
    """
    e = env(pool=[], installs=[("gh-runner-x64-1", "ghost")])
    e.enable_rc = 1
    rec = e.record()
    assert rec["_rc"] == 3, rec
    assert rec["verdict"] == "action-failed", rec
    assert any("will not stay up" in p for p in rec["problems"]), rec


def test_repo_slug_normalises_every_spelling():
    for value in (
        "https://github.com/o/n",
        "https://github.com/o/n/",
        "https://github.com/o/n.git",
        "github.com/o/n",
        "o/n",
        "O/N",
    ):
        assert RA._repo_slug(value) == "o/n", value
    assert RA._repo_slug("https://github.com/daniel-ospina/tortoise") != RA._repo_slug(
        "https://github.com/daniel-ospina/eldato"
    )


def test_labels_follow_the_gate_and_never_claim_the_other_arch(monkeypatch):
    """Fallback not engaged ⇒ arch marker ONLY (baking `ubuntu-latest` makes the gate read
    `local` forever); an x86 box never claims `self-hosted-arm64`.

    ⛔ HERMETIC FOR THE OS-LABEL SEAM, because THIS PR is what made it matter: `resolve_labels`
    calls `_os_labels`, which reads `CI_RUNNER_OS_LABEL` from the environment, and this test calls
    it IN-PROCESS with no fixture — so on the box that opted into the mitigation (the spelling the
    runbook's own registration recipe now tells operators to export) the first assertion reded with
    `self-hosted-x64,ubuntu-24.04` != `self-hosted-x64`, and `=true` raised `Unmeasurable`. A red
    for a reason unrelated to the code under test. The two neighbours that DO read the seam already
    pop it; this one is pinned to the `off` DEFAULT — the state that protects #6897 — by removing
    the variable entirely rather than setting it.

    Mutation: delete the `delenv` -> `CI_RUNNER_OS_LABEL=on pytest …` reds at the first assertion.
    """
    for seam in ("CI_RUNNER_OS_LABEL", "CI_RUNNER_OS_NAME", "CI_RUNNER_OS_RELEASE"):
        monkeypatch.delenv(seam, raising=False)
    assert RA.resolve_labels({}, "x86_64") == "self-hosted-x64"
    engaged = {
        "vm-2": RA.PoolEntry("vm-2", "online", False, ["self-hosted-arm64", "ubuntu-latest"])
    }
    assert RA.resolve_labels(engaged, "x86_64") == "self-hosted-x64,ubuntu-latest"
    assert RA.resolve_labels({}, "aarch64") == "self-hosted-arm64"


# ⛔ `""` IS A MODE, NOT A SKIP: a set-but-empty `CI_RUNNER_OS_LABEL` is `off` on both surfaces
# (`${CI_RUNNER_OS_LABEL:-off}` in the installer and the schedule renderer, `or "off"` here), and
# this parameterisation could not see it while the derivation used `os.environ.get(k, "off")` —
# where the default applies only when the key is ABSENT, so the empty export aborted the pass on
# the unattended path while the installer proceeded as `off`. Mutation: restore
# `os.environ.get("CI_RUNNER_OS_LABEL", "off")` -> the `""` arm raises `Unmeasurable` -> RED.
@pytest.mark.parametrize("mode", ["off", "on", "auto", ""])
@pytest.mark.parametrize(
    "host,release_marker",
    [("Linux", "24.04"), ("Linux", "22.04"), ("Darwin", None)],
)
def test_resolve_labels_matches_the_installer_on_every_os_label_arm(
    tmp_path, monkeypatch, mode, host, release_marker
):
    """The OS fact this file derives IS the installer's rule — proven by running both.

    The docstring of `resolve_labels` claimed to mirror `install-ci-runner-x64.sh::resolve_labels`
    while emitting no OS label at all, so an ORPHANED re-registration silently dropped the
    `ubuntu-24.04` mitigation on the one path with no labels to preserve. A comment cannot hold
    that mirror together — the installer grew the OS arm and nothing here failed — so the claim is
    pinned by EXECUTION: the real installer's `--print-labels` beside `resolve_labels`, over every
    mode × host-release combination, on both the bare and the engaged arm.

    Mutation: drop `_os_labels` from `resolve_labels`' return -> every claiming arm reds -> RED.
    Mutation: ignore `CI_RUNNER_OS_LABEL` and always emit the label -> the `off` arm reds -> RED.
    """
    os_release = tmp_path / "os-release"
    os_release.write_text(
        f'NAME="{host}"\n' + (f'VERSION_ID="{release_marker}"\n' if release_marker else "")
    )
    installer_env = {
        **os.environ,
        "CI_RUNNER_OS_LABEL": mode,
        "CI_RUNNER_OS_NAME": host,
        "CI_RUNNER_OS_RELEASE": str(os_release),
    }
    monkeypatch.setenv("CI_RUNNER_OS_LABEL", mode)
    monkeypatch.setenv("CI_RUNNER_OS_NAME", host)
    monkeypatch.setenv("CI_RUNNER_OS_RELEASE", str(os_release))

    # `on` is the operator saying "this box is that release"; `auto` has to READ it; `off` never
    # claims it. `on` does not consult the host (the installer returns before the read).
    claims = mode == "on" or (mode == "auto" and host == "Linux" and release_marker == "24.04")
    suffix = ",ubuntu-24.04" if claims else ""

    def printed(*args: str) -> str:
        r = subprocess.run(
            [BASH, str(INSTALLER), "--print-labels", *args],
            capture_output=True,
            text=True,
            env=installer_env,
        )
        assert r.returncode == 0, (args, r.returncode, r.stderr)
        return r.stdout.strip()

    # the bare arm (no fallback) and the engaged arm — the whole set, in the installer's order
    assert printed("--fallback", "off") == f"self-hosted-x64{suffix}"
    assert RA.resolve_labels({}, "x86_64") == printed("--fallback", "off")

    engaged = {
        "vm-2": RA.PoolEntry("vm-2", "online", False, ["self-hosted-arm64", "ubuntu-latest"])
    }
    assert printed("--fallback", "on") == f"self-hosted-x64,ubuntu-latest{suffix}"
    assert RA.resolve_labels(engaged, "x86_64") == printed("--fallback", "on")

    # ⛔ AND ONLY THE ARCH THAT HOSTS THAT IMAGE MAY CLAIM IT: `ubuntu-24.04` names the x64 hosted
    # image, so an arm64 box deriving it would be the false architecture claim `_preserve_labels`
    # drops. The installer never meets this case (it refuses a non-x86_64 INSTALL); this path can.
    assert RA.resolve_labels({}, "aarch64") == "self-hosted-arm64", (
        f"mode={mode}: an arm64 box claimed a version-pinned x64 label"
    )


def test_a_set_but_empty_os_release_seam_reads_the_default_file_like_the_installer(monkeypatch):
    """`CI_RUNNER_OS_RELEASE=` must mean the REAL `/etc/os-release`, on both sides of the mirror.

    The installer spells it `${CI_RUNNER_OS_RELEASE:-/etc/os-release}`; this module spelled it
    `os.environ.get("CI_RUNNER_OS_RELEASE", "/etc/os-release")`, where the default applies only
    when the key is ABSENT. So an EMPTY export — which is what `export X=$SOME_UNSET_VAR` produces,
    and how an env file drops a seam — made the installer read the real release while this
    derivation tried to `open("")`, got `OSError`, and declined. On an `ubuntu-24.04` box that is
    the installer claiming the label and the autoscaler's mirror refusing to re-apply it, on the
    unattended path, with nothing else saying why. Same class as the `get(k, "off")` bug in
    `_os_labels` three functions away.

    Host-independent by construction: the DEFAULT PATH is intercepted, so the assertion is about
    which file the empty value resolves to, not about this box's release. Mutation: restore
    `get("CI_RUNNER_OS_RELEASE", "/etc/os-release")` -> the read is `""` -> RED.
    """
    import builtins

    monkeypatch.setenv("CI_RUNNER_OS_NAME", "Linux")
    monkeypatch.setenv("CI_RUNNER_OS_RELEASE", "")
    seen: list[str] = []
    real_open = builtins.open

    def spy(path, *args, **kwargs):
        seen.append(str(path))
        if str(path) == "/etc/os-release":
            raise FileNotFoundError(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    assert RA._host_is_the_os_release() is False  # the intercepted default file does not exist
    assert seen == ["/etc/os-release"], (
        "an EMPTY `CI_RUNNER_OS_RELEASE` must resolve to the default file (`:-`, the installer's "
        f"spelling), not to the empty path: read {seen}"
    )


def test_an_unknown_os_label_value_refuses_rather_than_defaulting_to_off(monkeypatch):
    """A typo is not `off`: refuse, the way the installer does.

    Defaulting the unreadable value to `off` would drop the label while LOOKING like restraint —
    and this is the path an unattended autoscale pass runs, so nothing else would say so.
    """
    monkeypatch.setenv("CI_RUNNER_OS_LABEL", "true")
    with pytest.raises(RA.Unmeasurable) as exc:
        RA.resolve_labels({}, "x86_64")
    assert "CI_RUNNER_OS_LABEL must be off|on|auto" in str(exc.value)


def test_an_orphaned_re_registration_keeps_the_os_label(env, monkeypatch):
    """ORPHANED is the ONE repair with no labels to preserve — and it used to drop the mitigation.

    `resolve_labels` emitted `arch[,ubuntu-latest]` and never the OS fact, so a runner absent from
    the pool was re-registered WITHOUT `ubuntu-24.04`: the same erasure as the repair pass this PR
    already fixed, one door over, and equally silent (no error, no log line). The opt-in is driven
    through the environment the schedule's timer renders, because that is the sanctioned channel.

    Mutation: return `resolve_labels` without `_os_labels` -> the `on` case loses the label -> RED.
    """
    monkeypatch.setenv("CI_RUNNER_OS_LABEL", "on")
    e = env(pool=[], installs=[("gh-runner-x64-1", "pi-runner-ghost")], os_label="on")
    rec = e.record()
    assert rec["_rc"] == 0, rec
    assert rec["runners"][0]["state"] == "ORPHANED", rec
    assert "--labels self-hosted-x64,ubuntu-24.04 " in e.config(), (
        f"an ORPHANED re-registration dropped the mitigation: {e.config()}"
    )

    # ⛔ NOT ON AN ARM64 BOX: the bare label names the x64 hosted image, so deriving it here is the
    # false architecture claim `_preserve_labels` exists to drop.
    arm = env(pool=[], installs=[("gh-runner-x64-1", "pi-runner-ghost")], machine="aarch64", os_label="on")
    arm.record()
    assert "ubuntu-24.04" not in arm.config(), (
        f"an arm64 box claimed the x64 version-pinned label: {arm.config()}"
    )

    # The control: unset (the default) claims nothing — the #6897 pin is not reversed by a repair.
    monkeypatch.delenv("CI_RUNNER_OS_LABEL")
    default = env(pool=[], installs=[("gh-runner-x64-1", "pi-runner-ghost")])
    default.record()
    assert "ubuntu-24.04" not in default.config(), default.config()

    # And a typo REFUSES the repair instead of guessing which set to register.
    typo = env(pool=[], installs=[("gh-runner-x64-1", "pi-runner-ghost")], os_label="true")
    rec = typo.record()
    assert "CI_RUNNER_OS_LABEL must be off|on|auto" in " ".join(rec["problems"]), rec
    assert typo.config() == "", "a refused derivation must not register anything"


def test_unknown_architecture_refuses_rather_than_guessing(env):
    e = env(installs=[("gh-runner-x64-1", "ghost")], machine="mips")
    rec = e.record()
    assert rec["_rc"] == 3, rec
    assert rec["verdict"] == "action-failed"
    assert "unknown architecture" in " ".join(rec["problems"]), rec


def test_missing_provisioner_is_a_named_refusal_not_a_failed_pass(env):
    """A box that cannot create a NEW install refuses to add — and SAYS WHY, without crying failure.

    ⛔ Round 10, I1: the FILL step delegates to `install-ci-runner-x64.sh`, which ships in this
    repo at `tools/install-ci-runner-x64.sh` since #9639. Reporting an unresolvable path as
    `action-failed` made a scheduled pass on a below-ceiling box exit 3 every interval. It is a
    REFUSAL with a named cause: `fill-blocked`, exit 0, and the note names the file, its home in
    this repo, and the env var that fixes it — never a merged PR as the owner of a missing thing.
    """
    e = env()
    e.provisioner = e.bin / "absent.sh"
    rec = e.record()
    assert rec["_rc"] == 0, rec
    assert rec["verdict"] == "fill-blocked", rec
    assert rec["problems"] == [], rec["problems"]
    notes = " ".join(rec["notes"])
    assert "no provisioner" in notes, rec["notes"]
    # The message must point at something an operator can ACT on: the file's home in this repo
    # and the env var. It must not name a merged PR as the owner of a file that exists here.
    assert "tools/install-ci-runner-x64.sh" in notes, rec["notes"]
    assert "RUNNER_AUTOSCALE_PROVISIONER" in notes, rec["notes"]
    assert "#9639" not in notes, (
        "the absence is about this box's candidate paths, not about a PR that has landed: " + notes
    )


def test_missing_provisioner_still_repairs_dead_runners(env):
    """The refusal is scoped to FILL: measurement and repair are unaffected by the absent file."""
    e = env(
        pool=[("r1", "offline", False, "self-hosted-x64")], installs=[("gh-runner-x64-1", "r1")]
    )
    e.provisioner = e.bin / "absent.sh"
    rec = e.record()
    assert any(a.startswith("restart r1") for a in rec["actions"]), rec["actions"]
    assert rec["verdict"] == "fill-blocked", rec


# ── 4b. the service manager is the HOST's, never assumed to be systemd ────────────────────
def test_a_launchd_host_restarts_through_svc_sh_and_never_systemctl(env):
    """⛔ Round 10 (F1/I2): macOS has no systemctl. The restart primitive follows the host.

    On a launchd host the runner's own `svc.sh` is the ONLY way to bounce the agent. The old code
    ran `systemctl restart` unconditionally: rc 127, read as "restart failed", which fell through
    to `--replace` — re-registering a runner on the strength of an attempt that never ran.
    """
    e = env(
        pool=[("r1", "offline", False, "self-hosted-x64")],
        installs=[("gh-runner-x64-1", "r1")],
        svc_sh=True,
        service="launchd",
        svc_mark="r1",
    )
    rec = e.record()
    assert rec["_rc"] == 0, rec
    assert any(a.startswith("restart r1") and "svc.sh" in a for a in rec["actions"]), rec["actions"]
    assert e.service_calls()[:2] == ["stop", "start"], e.service_calls()
    assert e.restarted() == [], "a launchd host must not shell out to systemctl at all"
    assert any("came back online" in a for a in rec["actions"]), rec["actions"]
    assert e.config() == "", "a successful restart must never re-register"


def test_a_launchd_host_without_svc_sh_is_unknown_never_offline(env):
    """No service manager ⇒ the runner's state is UNKNOWN: stop the pass, register nothing.

    ⛔ This is the destructive half of F1: `unsupported` folded into `failed` reaches
    `register(..., replace=True)`, which invalidates the credentials of a listener that may be
    reconnecting and picking up a job — the one action this file promises never to take blindly.
    """
    e = env(
        pool=[("r1", "offline", False, "self-hosted-x64")],
        installs=[("gh-runner-x64-1", "r1")],
        service="launchd",
    )
    rec = e.record()
    assert rec["_rc"] == 2, rec
    assert rec["verdict"] == "unmeasurable", rec
    assert rec.get("unverified_cause") == "no-restart-primitive", rec
    assert "service manager" in rec["error"], rec["error"]
    assert e.config() == "", f"nothing may be re-registered on an unknown state: {e.config()}"
    assert e.restarted() == []


def test_a_scoped_sudo_DENIAL_is_not_mistaken_for_a_failed_restart(env):
    """⛔ THE REGEX MUST KNOW SUDO'S ACTUAL WORDS, or the destructive half reopens.

    `_SUDO_REFUSED` first listed `password is required|not in the sudoers|a terminal is required|
    no tty present|no askpass|command not allowed`. The CANONICAL denial is none of those: sudo says

        Sorry, user tester is not allowed to execute '…' as root on host.

    and `command not allowed` — the wording that WAS listed — is `doas`, not sudo. So a command-
    scoped sudoers that resolves a different path (`/usr/bin/systemctl` vs a rule for
    `/bin/systemctl`), a rule for `start` but not `restart`, or a `nosuid` mount all landed on
    `failed` -> skip `wait_online` -> `register(..., replace=True)`: re-registering a runner that
    was never restarted, the exact destructive action the classification exists to prevent.

    Mutation: drop the `Sorry, user … is not allowed to execute` alternative -> `failed` and a
    `--replace` re-registration -> RED.
    """
    e = env(
        pool=[("r1", "offline", False, "self-hosted-x64")],
        installs=[("gh-runner-x64-1", "r1")],
        service="systemd",
        sudo_fails=True,
    )
    e.sudo = _exe(
        e.bin / "sudo",
        "#!/usr/bin/env bash\n"
        "shift\n"
        "echo \"Sorry, user tester is not allowed to execute '$*' as root on host.\" >&2\n"
        "exit 1\n",
    )
    rec = e.record()
    assert rec["unverified_cause"] == "no-restart-primitive", rec
    assert rec["_rc"] == 2, rec
    assert e.config() == "", f"a sudo DENIAL must never re-register: {e.config()}"


def test_a_command_scoped_sudoers_still_repairs(env):
    """⛔ THE FIX FOR THE REFUSED-ESCALATION CASE MUST NOT DELETE A WORKING REPAIR.

    The first version of the fix asked `sudo -n true` whether escalation worked at all and refused
    the restart on any non-zero rc. But sudoers is commonly COMMAND-SCOPED — `NOPASSWD:
    /usr/bin/systemctl`, or just the restart line — which DENIES `true` while allowing exactly the
    restart this code needs. That box was then classified `unsupported` on every pass: a repair the
    code had always performed correctly was silently deleted, and (because `unsupported` sets
    `stop_pass`) every scheduled fire exited 2 forever. Classify on sudo's OWN refusal signature
    instead, so an allowed restart succeeds and only an actual refusal reads `unsupported`.

    Mutation: reinstate a `_sudo_prefix()` + `sudo -n true` pre-probe -> state is `unsupported`, no
    restart is attempted, nothing comes back online -> RED.
    """
    e = env(
        pool=[("r1", "offline", False, "self-hosted-x64")],
        installs=[("gh-runner-x64-1", "r1")],
        service="systemd",
        sudo_fails=True,
        sudo_scoped=True,
    )
    rec = e.record()
    assert rec["_rc"] == 0, rec
    restarts = e.restarted()
    assert len(restarts) == 1 and "restart" in restarts[0] and "r1" in restarts[0], restarts
    assert any("came back online" in a for a in rec["actions"]), rec["actions"]
    assert e.config() == "", f"a working restart must never re-register: {e.config()}"


def test_systemd_escalation_that_cannot_run_is_unknown_never_a_replace(env):
    """⛔ REFUSED ESCALATION IS rc 1, NOT 127 — so the systemd leg fell through to `--replace`.

    `_run` returns 127 only when the command could not be SPAWNED (OSError), and the systemd branch
    guarded on exactly that. But the shipped unit runs as `User=$(id -un)` and does NOT set
    RUNNER_AUTOSCALE_NO_SUDO, so `sudo -n systemctl restart` is the default, and on a box without
    passwordless sudo it exits 1 having run NOTHING ("sudo: a password is required" — the same
    measurement the launchd branch records). Read as `failed`, the caller skips `wait_online` and
    re-registers with `--replace`, invalidating the credentials of a runner that may be
    reconnecting. The launchd leg was fixed for this; the systemd leg was not, and no fixture
    exercised it (every systemd case sets RUNNER_AUTOSCALE_NO_SUDO=1).

    Mutation: drop the `_sudo_prefix()` probe -> "restart r1: … rc=1", then a `--replace`
    re-registration -> RED.
    """
    e = env(
        pool=[("r1", "offline", False, "self-hosted-x64")],
        installs=[("gh-runner-x64-1", "r1")],
        service="systemd",
        sudo_fails=True,
    )
    rec = e.record()
    assert rec["_rc"] == 2, rec
    assert rec["verdict"] == "unmeasurable", rec
    assert rec["unverified_cause"] == "no-restart-primitive", rec
    assert e.config() == "", f"nothing may be re-registered on an unknown state: {e.config()}"
    # The escalation IS attempted (that is how sudo gets to say no), so asserting "no restart was
    # attempted" would be decorative — the refusing `sudo` stub never execs `systemctl`, leaving
    # `restarted()` empty in BOTH directions (measured; the earlier version of this assertion could
    # not fail). Assert the layer that is actually reached instead: `sudo` was invoked, `systemctl`
    # was NOT, and the refusal was attributed to escalation rather than to the runner.
    assert "restart" in e.sudo_log.read_text(encoding="utf-8"), e.sudo_log.read_text()
    assert e.restarted() == [], e.restarted()
    assert "password is required" in " ".join(rec["actions"]), rec["actions"]


def test_launchd_never_escalates_so_a_passwordless_sudo_gap_cannot_force_a_replace(env):
    """⛔ The shipped default is `sudo -n`, and a launchd agent is PER-USER: root cannot address
    `gui/<uid>`, and on a box without passwordless sudo `sudo -n` exits 1 without running
    anything. Prefixing `svc.sh` with it turned "could not be attempted" into "restart failed" →
    `config.sh --replace` — the destructive action the F1 fix exists to prevent, on the default
    configuration the suite never exercised (every fixture sets RUNNER_AUTOSCALE_NO_SUDO=1).
    """
    e = env(
        pool=[("r1", "offline", False, "self-hosted-x64")],
        installs=[("gh-runner-x64-1", "r1")],
        svc_sh=True,
        service="launchd",
        svc_mark="r1",
        sudo_fails=True,
    )
    rec = e.record()
    assert rec["_rc"] == 0, rec
    assert any(a.startswith("restart r1") and "svc.sh" in a for a in rec["actions"]), rec["actions"]
    assert any("came back online" in a for a in rec["actions"]), rec["actions"]
    assert e.config() == "", f"a per-user svc.sh must not be escalated: {e.config()}"


def test_a_launchd_host_enables_the_unit_through_svc_sh(env):
    """ORPHANED registration on launchd enables the service with `svc.sh install` + `start`.

    The old code called `systemctl enable --now` after a SUCCESSFUL `config.sh`, so the pass
    reported exit 3 for a runner that was in fact registered and up (F1, second half).
    """
    e = env(
        pool=[], installs=[("gh-runner-x64-1", "pi-runner-ghost")], svc_sh=True, service="launchd"
    )
    rec = e.record()
    assert rec["_rc"] == 0, rec
    assert "--replace" in e.config() and "--name pi-runner-ghost" in e.config(), e.config()
    calls = [c.split()[0] for c in e.service_calls()]
    assert calls[:2] == ["install", "start"], e.service_calls()
    assert e.restarted() == [], "launchd hosts must never call systemctl"
    assert rec["problems"] == [], rec["problems"]


# ── 6. mutation testing — every gate must be load-bearing ───────────────────────────────────
MUTATIONS = {
    "memory-arm-not-additive": dict(
        old="by_mem = max(0, running) + headroom",
        new="by_mem = headroom",
        kwargs=dict(
            mem_mb=5535,
            pool=[
                ("r1", "online", False, ""),
                ("r2", "online", False, ""),
                ("r3", "online", False, ""),
            ],
            installs=[
                ("gh-runner-x64-1", "r1"),
                ("gh-runner-x64-2", "r2"),
                ("gh-runner-x64-3", "r3"),
            ],
        ),
        args=(),
        wrong=lambda r: r["verdict"] != "at-ceiling",
    ),
    "flat-core-table": dict(
        old="CORE_TABLE = ((8, 3), (4, 2), (1, 1))",
        new="CORE_TABLE = ((8, 5), (4, 5), (1, 5))",
        kwargs=dict(mem_mb=30000, ncpu=8),
        args=(),
        wrong=lambda r: r["ceiling"] != 3,
    ),
    "no-memory-reserve": dict(
        old="headroom = max(0, (mem_mb - MEM_RESERVE_MB) // MEM_PER_RUNNER_MB)",
        new="headroom = max(0, mem_mb // MEM_PER_RUNNER_MB)",
        kwargs=dict(ncpu=4, mem_mb=3200),
        args=(),
        wrong=lambda r: r["ceiling"] != 0,
    ),
    "post-step-memory-guard-removed": dict(
        old="        if after < MEM_RESERVE_MB:",
        new="        if False:",
        kwargs=dict(ncpu=4, mem_mb=5200, shrink_to=2000),
        args=(),
        wrong=lambda r: not any("below reserve_mb" in n for n in r.get("notes", [])),
    ),
    "orphan-read-as-serving": dict(
        old='if entry is None:\n            state = "ORPHANED"',
        new='if entry is None:\n            state = "SERVING"',
        kwargs=dict(pool=[], installs=[("gh-runner-x64-1", "ghost")]),
        args=(),
        wrong=lambda r: r["runners"][0]["state"] != "ORPHANED",
    ),
    "serving-read-as-dead": dict(
        old='elif entry.online:\n            state = "SERVING"',
        new='elif entry.online:\n            state = "DEAD"',
        kwargs=dict(pool=[("r1", "online", False, "")], installs=[("gh-runner-x64-1", "r1")]),
        args=(),
        wrong=lambda r: any(a.startswith("restart r1") for a in r["actions"]),
    ),
    "unreadable-read-as-zero": dict(
        old='        raise Unmeasurable(f"no MemAvailable line in {path}")',
        new="        return 0",
        kwargs=dict(omit_memavailable=True),
        args=(),
        wrong=lambda r: r["_rc"] != 2,
    ),
    "want-guard-removed": dict(
        old="if want is not None and want > ceil:",
        new="if False:",
        kwargs=dict(mem_mb=30000),
        args=("--want", "9"),
        wrong=lambda r: r["_rc"] != 1,
    ),
    "cross-repo-guard-removed": dict(
        old="if _repo_slug(r.repo) != _repo_slug(repo):",
        new="if False:",
        kwargs=dict(
            pool=[],
            installs=[("gh-runner-x64-1", "eldato-runner-1")],
            install_repo="https://github.com/daniel-ospina/eldato",
        ),
        args=(),
        # the mutant re-registers ANOTHER repo's runner into this repo (config.sh was called)
        wrong=lambda r: bool(r.get("_config", "")),
    ),
    "repair-relabels-from-scratch": dict(
        old='preserved = _preserve_labels(c["labels"], machine) if c["labels"] else ""',
        new='preserved = ""',
        kwargs=dict(
            pool=[("r1", "offline", False, "self-hosted-x64,ubuntu-latest,gpu")],
            installs=[("gh-runner-x64-1", "r1")],
            systemctl_rc=1,
        ),
        args=(),
        wrong=lambda r: "gpu" not in r.get("_config", ""),
    ),
    "other-arch-marker-preserved": dict(
        # The mutation reverts the case-INSENSITIVE marker test to the exact-case one this lane
        # found (review at `2d9a49ad`: a mixed-case marker survived BOTH heal paths). Its fixture
        # therefore carries a mixed-case marker: a lower-case-only fixture cannot see the casing
        # change at all, and would report this gate as enforced while it was not.
        old='and not x.lower().startswith("self-hosted-")',
        new="and True",
        kwargs=dict(
            pool=[("r1", "offline", False, "self-hosted,ARM64,Self-Hosted-Arm64,gpu")],
            installs=[("gh-runner-x64-1", "r1")],
            systemctl_rc=1,
        ),
        args=(),
        wrong=lambda r: "Self-Hosted-Arm64" in r.get("_config", ""),
    ),
    "version-pinned-hosted-label-preserved": dict(
        # The whole class is DROPPED on re-registration (#10298's landed rule), so a mutant that
        # stops filtering it keeps both spellings on the box — which makes an out-of-band pin
        # permanent on an x64 pool, and lets an arm64 hosted label serve a job on hardware it did
        # not ask for. Both spellings are on the fixture, so either surviving is caught.
        old="and not VERSION_PINNED_HOSTED_LABEL.fullmatch(x)",
        new="and True",
        kwargs=dict(
            pool=[("r1", "offline", False, POOL_LABELS_WITH_VERSION_PINS)],
            installs=[("gh-runner-x64-1", "r1")],
            systemctl_rc=1,
        ),
        args=(),
        wrong=lambda r: "ubuntu-24.04" in r.get("_config", ""),
    ),
    "foreign-listener-not-counted": dict(
        old="        live = _live()",
        new=(
            "        live = sum(1 for r in _found() "
            "if r.alive and _repo_slug(r.repo) == _repo_slug(repo))"
        ),
        # a box AT its ceiling where one of the three listeners is another repo's: the mutant
        # cannot see it, asks for an extra install, and ends with FOUR listeners on 8 cores.
        kwargs=dict(
            pool=[("r1", "online", False, ""), ("r2", "online", False, "")],
            installs=[
                ("gh-runner-x64-1", "r1"),
                ("gh-runner-x64-2", "r2"),
                ("gh-runner-x64-3", "fx"),
            ],
            foreign=["fx"],
        ),
        args=(),
        # ⛔ MEASURED FROM THE BOX, never from the mutant's own counter: the mutant reports
        # `live=3` (r1, r2, its own new install) and `ceiling=3`, so a `live > ceiling` predicate
        # read the report and could not see the over-fill it had just caused — the mutation
        # survived while the box held four listeners (round 5, found again in round 9).
        wrong=lambda r: r.get("_listeners", 0) > r.get("ceiling", 0),
    ),
    "slot-index-from-install-count": dict(
        # the old model: assume the provisioner added exactly one, instead of re-measuring. On a
        # box whose call adds TWO listeners it asks again and over-fills past the ceiling.
        # the round-5 over-fill: index off the INSTALL COUNT instead of the next free SLOT
        old="        index = _next_free_slot(root)",
        new="        index = len(_found()) + 1",
        # this box's real shape: an ad-hoc `gh-runner-cell4-x64` beside numbered slots 1-2, one
        # listener dead ⇒ a count says 3 while slot 3 is free, so `--count 4` creates 3 AND 4.
        kwargs=dict(
            pool=[("r1", "online", False, ""), ("r2", "online", False, "")],
            installs=[
                ("gh-runner-x64", "r1"),
                ("gh-runner-x64-2", "r2"),
                ("gh-runner-cell4-x64", "dead"),
            ],
            not_alive=["gh-runner-cell4-x64"],
        ),
        args=(),
        wrong=lambda r: r.get("live", 0) > r.get("ceiling", 0),
    ),
    "unverified-error-claims-nothing-happened": dict(
        old='"that point; nothing was added or registered after it."',
        new='"the pass stopped; no runner was registered and none was added"',
        kwargs=dict(
            pool=[("r1", "offline", False, "self-hosted-x64")],
            installs=[("gh-runner-x64-1", "ghost"), ("gh-runner-x64-2", "r1")],
            pool_fail_after=1,
        ),
        args=(),
        wrong=lambda r: "no runner was registered" in r.get("error", ""),
    ),
    "provisioner-refusal-as-failure": dict(
        old="    if rc == 2:",
        new="    if False:",
        kwargs=dict(
            pool=[("r1", "online", False, "")],
            installs=[("gh-runner-x64-1", "r1")],
            provisioner_rc=2,
        ),
        args=(),
        wrong=lambda r: r.get("verdict") == "action-failed",
    ),
    "dry-run-assumes-plus-one": dict(
        old="            break\n        try:\n            note = provision(index)",
        new=(
            "            live += 1\n            continue\n"
            "        try:\n            note = provision(index)"
        ),
        kwargs=dict(pool=[("r1", "online", False, "")], installs=[("gh-runner-x64-1", "r1")]),
        args=("--dry-run",),
        wrong=lambda r: (
            sum(1 for a in r.get("actions", []) if a.startswith("would ask the provisioner")) != 1
        ),
    ),
    "ps-failure-read-as-zero-listeners": dict(
        # anchored on the LISTENER path's own raise: the bare `if proc.returncode != 0:` also
        # appears in the Darwin memory arm and the worker check, and the mutator replaces the
        # first match — which silently retargeted this gate (the mutant then behaved correctly
        # and "survived").
        old=(
            "    if proc.returncode != 0:\n"
            "        raise Unmeasurable(\n"
            '            f"process list unreadable (ps exited'
        ),
        new=(
            "    if False:\n"
            "        raise Unmeasurable(\n"
            '            f"process list unreadable (ps exited'
        ),
        kwargs=dict(
            pool=[
                ("r1", "online", False, ""),
                ("r2", "online", False, ""),
                ("r3", "online", False, ""),
            ],
            installs=[
                ("gh-runner-x64", "r1"),
                ("gh-runner-x64-2", "r2"),
                ("gh-runner-x64-3", "r3"),
            ],
            alive_seam=False,
            ps_rc=1,
        ),
        args=(),
        wrong=lambda r: bool(r.get("_provisioned", [])),
    ),
    "slot-occupied-by-path-existence": dict(
        old='if not (slot / ".runner").exists():',
        new="if not slot.exists():",
        kwargs=dict(
            pool=[("r1", "online", False, ""), ("r2", "online", False, "")],
            installs=[("gh-runner-x64", "r1"), ("gh-runner-x64-2", "r2")],
            gap_slots=[3],
        ),
        args=(),
        wrong=lambda r: r.get("live", 0) > r.get("ceiling", 0),
    ),
    "loop-not-bounded-by-listener-progress": dict(
        old="        if live <= previous_live:",
        new="        if False:",
        kwargs=dict(pool=[], installs=[], provisioner_alive=0),
        args=(),
        wrong=lambda r: len(r.get("_provisioned", [])) != 1,
    ),
    "mid-pass-failure-reports-nothing-changed": dict(
        old='                "stopped: the box could not be re-measured after an install — the action above "',
        new='                "nothing changed — "',
        kwargs=dict(pool=[], installs=[], provisioner_break_meminfo=1),
        args=(),
        wrong=lambda r: any("nothing changed" in n for n in r.get("notes", [])),
    ),
    "fill-after-unverified": dict(
        old="        if stop_pass or live >= target:",
        new="        if live >= target:",
        kwargs=dict(
            pool=[("r1", "offline", False, "self-hosted-x64")],
            installs=[("gh-runner-x64-1", "r1")],
            pool_fail_after=1,
        ),
        args=(),
        wrong=lambda r: bool(r.get("_provisioned", [])),
    ),
    # ⛔ Round 10's gates. Each of these three is a fail-open this PR was reviewed for twice:
    # a registration whose unit never came up counted as live; a restart that could not be
    # ATTEMPTED read as "still offline" (and re-registered); and a missing provisioner reported
    # as the installer's own headroom refusal — a different fact with a different remedy.
    "enable-unit-failure-read-as-registered": dict(
        old="    if failures:\n        raise ActionFailed(",
        new="    if False:\n        raise ActionFailed(",
        kwargs=dict(pool=[], installs=[("gh-runner-x64-1", "ghost")], service="launchd"),
        args=(),
        wrong=lambda r: not r.get("problems"),
    ),
    "no-restart-primitive-read-as-offline": dict(
        old='            if restart_state == "unsupported":',
        new="            if False:",
        kwargs=dict(
            pool=[("r1", "offline", False, "self-hosted-x64")],
            installs=[("gh-runner-x64-1", "r1")],
            service="launchd",
        ),
        args=(),
        wrong=lambda r: "--replace" in (r.get("_config") or ""),
    ),
    "missing-provisioner-reported-as-the-installers-own-refusal": dict(
        old='    elif record.get("problem_kind") == "no-provisioner":',
        new="    elif False:",
        kwargs=dict(provisioner_absent=True),
        args=(),
        wrong=lambda r: r.get("verdict") != "fill-blocked",
    ),
    # ⛔ The exit-5 arm (#9685 review, Issue 2): a callee-guard refusal must NOT exit 0. Mutating
    # the mapping back to the pre-fix contract leaves the verdict intact and only the CODE wrong,
    # so the predicate reads the exit code — the datum a systemd timer actually sees.
    "provisioner-refusal-exits-zero": dict(
        old='    if record["verdict"] == "fill-refused-by-provisioner":\n        return 5',
        new="    if False:\n        return 5",
        kwargs=dict(
            pool=[("r1", "online", False, "")],
            installs=[("gh-runner-x64-1", "r1")],
            provisioner_rc=2,
        ),
        args=(),
        wrong=lambda r: r.get("verdict") == "fill-refused-by-provisioner" and r.get("_rc") == 0,
    ),
}


def _mutant(tmp_path: Path, old: str, new: str) -> Path:
    src = TOOL.read_text(encoding="utf-8")
    assert old in src, f"mutation anchor not found (the gate moved): {old!r}"
    out = tmp_path / "mutant" / "runner_autoscale.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(src.replace(old, new, 1), encoding="utf-8")
    return out


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_each_gate_is_load_bearing(tmp_path, name):
    """Mutate ONE gate; the mutant must produce the wrong verdict the gate prevents.

    This is what separates a guard from decoration: if a mutation survives, the suite is
    asserting the implementation instead of the property.
    """
    spec = MUTATIONS[name]
    mutant = _mutant(tmp_path, spec["old"], spec["new"])

    e_mut = Env(tmp_path / "mutant-run", **spec["kwargs"])
    rec_mut = e_mut.record(*spec["args"], module=mutant)
    rec_mut["_provisioned"] = e_mut.provisioned()
    rec_mut["_config"] = e_mut.config()
    rec_mut["_listeners"] = e_mut.listeners()
    # ⛔ A MUTANT THAT CRASHED PROVES NOTHING. An anchor edit once left a mutated module raising
    # UnboundLocalError, whose empty record satisfied the predicate by accident (round 6, finding
    # 2). Every mutant must produce a RECORD (a verdict), not just "not the right one".
    assert rec_mut.get("verdict"), f"mutation {name} did not run to a verdict: {rec_mut}"
    assert spec["wrong"](rec_mut), f"mutation {name} SURVIVED — the gate is decoration: {rec_mut}"

    # …and the REAL tool on the same fixture is correct: the mutation is the only difference.
    e_real = Env(tmp_path / "real-run", **spec["kwargs"])
    rec_real = e_real.record(*spec["args"])
    rec_real["_provisioned"] = e_real.provisioned()
    rec_real["_config"] = e_real.config()
    rec_real["_listeners"] = e_real.listeners()
    assert not spec["wrong"](rec_real), f"the unmutated tool also failed the property: {rec_real}"


def test_no_ceiling_override_flag_exists():
    """⛔ A box that can be told to exceed its own measurement has no guard.

    The ABSENCE of the flag is the invariant, so it is asserted rather than assumed.
    """
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--help"], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    for forbidden in ("--ceiling", "--max-runners", "--force", "--ignore-memory"):
        assert forbidden not in proc.stdout, f"{forbidden} would bypass the measured ceiling"


def test_a_FAILED_restart_is_MEASURED_before_anything_is_re_registered(env):
    """⛔ `failed` IS NOT "STILL OFFLINE", AND RE-REGISTERING WITH `--replace` IS DESTRUCTIVE.

    `failed` is assigned to ANY non-zero restart rc that is neither 127 nor a recognised sudo
    refusal — an AppArmor/SELinux policy denial, a sudo plugin error, a timeout. The pass used to
    fold that into `state = "offline"` WITHOUT reading the pool, then log "still offline after
    restart" (a measurement it never took) and re-register with `--replace`, which deletes the
    registration of a runner that was already back and keeps running with now-invalid credentials
    until its next job. `wait_online` is the measurement; a runner that came back on its own must
    be seen coming back, not replaced.

    Mutation: restore `... if restart_state == "ok" else "offline"` -> the pool is never read, the
    runner is re-registered with --replace -> RED.
    """
    e = env(
        pool=[("r1", "offline", False, "self-hosted-x64")],
        installs=[("gh-runner-x64-1", "r1")],
        service="systemd",
    )
    # A `failed` restart (rc 1, no sudo wording, not 127) whose effect LANDED anyway: the runner
    # is back in the pool. This is the case the unmeasured fold destroyed.
    e.systemctl = _exe(
        e.bin / "systemctl",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "${SYSTEMCTL_LOG:-/dev/null}"\n'
        'case "$1" in\n'
        '  restart) unit="${2:-}"; unit="${unit#actions.runner.}"; unit="${unit%.service}"\n'
        '           printf "%s" "${unit##*.}" > "${RESTART_MARKER:-/dev/null}"; exit 1 ;;\n'
        "  *) exit 0 ;;\n"
        "esac\n",
    )
    rec = e.record()
    assert "--replace" not in e.config(), (
        "re-registered with --replace a runner it never measured as offline: " + e.config()
    )
    assert any("came back online after restart" in a for a in rec.get("actions", [])), rec
    assert not any("still offline after restart" in a for a in rec.get("actions", [])), rec
