"""Hermetic tests for ``tools/install-queue-reconcile-schedule.sh`` (#7810).

The installer writes a schedule a *real* run would point at the live user agent
-- ``launchctl`` addresses the domain **by uid**, not by ``$HOME`` -- so a
throwaway ``HOME``/``AGENTS_DIR`` is NOT a sandbox (the reaper installer's
``test_install_reaper_schedule.py`` records two review runs that actually left a
live agent pointing at a deleted temp plist). These tests therefore NEVER
execute the real install path: they render into a throwaway sandbox with
``uname``, ``launchctl``, ``plutil``, ``crontab`` and ``gh`` stubbed on ``PATH``,
and assert the script's own decisions -- the refusals, the rendered schedule,
and the fixed-string cron replacement -- rather than the host's schedule.

The installer is the missing trigger for ``tools/queue_reconcile.py`` (#7801):
the reconciliation was correct but nothing invoked it, so ``CLAIMS.tsv``
re-staled exactly as it did before the tool existed. What these tests pin is
that a SCHEDULED run is the SAFE run: armed with ``--apply`` (the tool's
append-only, backed-up, idempotent correction path), pointed at an interpreter
the tool will actually accept, and never installed against a queue or a ``gh``
that is not there.

Each test whose docstring carries a ``Mutation:`` line names the mutation that
turns it RED.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "install-queue-reconcile-schedule.sh"

LABEL = "com.tortoise.queue-reconcile"
PLIST_NAME = f"{LABEL}.plist"
MARKER = "# tortoise-queue-reconcile (#7810)"

_ENV_LEAKS = (
    "AGENTS_DIR",
    "QUEUE_RECONCILE_ALLOW_NONSTANDARD_AGENTS_DIR",
    "QUEUE_RECONCILE_INTERVAL",
    "CLAIMS_QUEUE",
    "TORTOISE_REPO",
    "PYTHON_BIN",
    "CRONTAB_CMD",
)

#: The `gh` stub EMITS TEXT, on purpose. A silent stub made the suite blind to
#: a real defect (2026-10-08): the plist template was an UNQUOTED heredoc whose
#: prose named `gh`, so rendering the plist ran `` `gh` `` as COMMAND
#: SUBSTITUTION and spliced gh's help — which contains `--help`, illegal inside
#: an XML comment — into the plist. Every test passed because the stub printed
#: nothing. A stub that prints `--`-bearing help text makes that class
#: observable; `--help`/`--version` are verbatim from the real `gh --help`.
_GH_HELP_LINES = [
    "Work seamlessly with GitHub from the command line.",
    "",
    "USAGE",
    "  gh <command> <subcommand> [flags]",
    "",
    "FLAGS",
    "  --help      Show help for command",
    "  --version   Show gh version",
    "",
    "LEARN MORE",
    "  Use `gh <command> <subcommand> --help` for more information about a command.",
]
_GH_STUB = (
    "#!/usr/bin/env bash\n"
    + "cat <<'GHEOF'\n"
    + "\n".join(_GH_HELP_LINES)
    + "\nGHEOF\n"
)


def _write_stub(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _sandbox(tmp_path: Path, uname: str = "Darwin", *, gh: bool = True,
             queue: bool = True) -> dict:
    """Throwaway HOME/repo/log + PATH stubs for every external tool.

    ``launchctl``/``crontab`` only RECORD invocations -- they never touch the
    host. ``gh`` and the queue file exist by default because the installer
    REFUSES to install without them; the tests that pin those refusals pass
    ``gh=False`` / ``queue=False``.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "log"
    log.mkdir()
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    # A tool file must exist at the resolved path or the install refuses; it is
    # never executed by the installer (only the interpreter floor probe runs).
    (repo / "tools" / "queue_reconcile.py").write_text("# stub\n", encoding="utf-8")

    _write_stub(bindir, "uname", f"#!/usr/bin/env bash\necho '{uname}'\n")
    _write_stub(
        bindir, "launchctl",
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "launchctl $*" >> "$STUB_LOG/launchctl.log"\n'
        'exit 0\n',
    )
    _write_stub(bindir, "plutil", "#!/usr/bin/env bash\nexit 0\n")
    _write_stub(
        bindir, "crontab",
        '#!/usr/bin/env bash\n'
        'STORE="$STUB_LOG/crontab.txt"\n'
        'if [ "${1:-}" = "-l" ]; then [ -f "$STORE" ] && cat "$STORE"; exit 0; fi\n'
        'if [ "${1:-}" = "-" ]; then cat > "$STORE"; exit 0; fi\n'
        'exit 0\n',
    )
    if gh:
        _write_stub(bindir, "gh", _GH_STUB)

    if queue:
        qdir = home / ".pi" / "agent" / "state" / "queues"
        qdir.mkdir(parents=True)
        (qdir / "CLAIMS.tsv").write_text("# pr\tlane\tverdict\treason\n", encoding="utf-8")

    env = dict(os.environ)
    for key in _ENV_LEAKS:
        env.pop(key, None)
    env.update({
        # A CONTROLLED PATH: the stub dir plus the system dirs ONLY. Appending
        # the ambient PATH would leak the host's own `gh` (observed at
        # ~/.pi/agent/shims/gh), which silently defeats the gh-absent refusal
        # test — the sandbox would not be a sandbox for the very command the
        # guard is about. `bash`, `sed`, `dirname`, `python3` and `dscl` all
        # resolve under these four dirs, so the installer still runs normally.
        "PATH": f"{bindir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(home),
        "STUB_LOG": str(log),
        "TORTOISE_REPO": str(repo),
        # `/usr/bin/true` accepts (and ignores) `-c ...` and exits 0, so the
        # floor probe passes without depending on this box's interpreter.
        "PYTHON_BIN": "/usr/bin/true",
        "CRONTAB_CMD": "crontab",
    })
    return {
        "env": env,
        "bindir": bindir,
        "home": home,
        "log": log,
        "repo": repo,
        "launchctl_log": log / "launchctl.log",
        "crontab_store": log / "crontab.txt",
        "plist_default": home / "Library" / "LaunchAgents" / PLIST_NAME,
    }


def _run(sb: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=sb["env"], capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _install(sb: dict, target: Path, *args: str) -> subprocess.CompletedProcess:
    """Install into a non-standard AGENTS_DIR with the documented opt-in."""
    sb["env"]["AGENTS_DIR"] = str(target)
    sb["env"]["QUEUE_RECONCILE_ALLOW_NONSTANDARD_AGENTS_DIR"] = "1"
    return _run(sb, *args)


def _program_args(plist: Path) -> list[str]:
    with open(plist, "rb") as fh:
        return plistlib.load(fh)["ProgramArguments"]


def _cron_schedule_line(sb: dict) -> str:
    lines = [
        ln for ln in sb["crontab_store"].read_text(encoding="utf-8").splitlines()
        if "queue_reconcile.py" in ln
    ]
    assert len(lines) == 1, f"expected exactly one schedule line, got {lines!r}"
    return lines[0]


# ── P1: a throwaway HOME/AGENTS_DIR must not re-point the live agent ────────

def test_darwin_refuses_nonstandard_agents_dir_without_optin(tmp_path):
    """A non-standard AGENTS_DIR is refused (exit 2) before any launchctl call
    or plist write.

    Mutation: drop ``require_standard_agents_dir`` from install_darwin -> rc 0,
    launchctl.log exists, plist written -> RED.
    """
    sb = _sandbox(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    sb["env"]["AGENTS_DIR"] = str(elsewhere)
    res = _run(sb)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "refusing" in res.stderr.lower()
    assert not sb["launchctl_log"].exists(), "refusal still called launchctl"
    assert not (elsewhere / PLIST_NAME).exists(), "refusal still wrote a plist"


def test_darwin_throwaway_home_is_not_a_sandbox(tmp_path):
    """The *sandbox illusion* itself: a throwaway HOME whose AGENTS_DIR is
    ``$HOME/Library/LaunchAgents`` is still refused, because the live agent
    lives under the LOGIN ACCOUNT's home, not the exported one.

    Mutation: compare AGENTS_DIR against ``$HOME/Library/LaunchAgents`` (the
    literal finding wording) instead of the login home -> the guard passes and
    bootstrap runs -> RED.
    """
    sb = _sandbox(tmp_path)
    sb["env"]["AGENTS_DIR"] = str(sb["plist_default"].parent)
    res = _run(sb)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "refusing" in res.stderr.lower()
    assert not sb["launchctl_log"].exists(), "throwaway HOME still bootstrapped"
    # …and --uninstall is refused on the same gate, so a throwaway HOME cannot
    # tear the live agent's plist path away either.
    res_un = _run(sb, "--uninstall")
    assert res_un.returncode == 2, (res_un.stdout, res_un.stderr)


# ── P1: the armed schedule is the safe schedule ────────────────────────────

def test_darwin_default_install_is_armed_with_apply(tmp_path):
    """The DEFAULT install runs the tool's OWN ``--apply`` path: append-only,
    timestamped-backup, idempotent (the tool's `apply_corrections` re-reads the
    queue and skips an already-terminal number). Installing the reconciliation
    report-only would leave the queue re-staling, which is the defect.

    Mutation: default ``MODE_FLAG`` to empty -> the schedule reports forever and
    appends nothing -> RED.
    """
    sb = _sandbox(tmp_path)
    target = tmp_path / "agents"
    res = _install(sb, target)
    assert res.returncode == 0, (res.stdout, res.stderr)
    args = _program_args(target / PLIST_NAME)
    assert "--apply" in args, args
    assert "--dry-run" not in args, (
        "queue_reconcile.py has NO `--dry-run` flag — dry run is the ABSENCE of "
        f"--apply, so passing one dies on argparse at every fire: {args}"
    )
    assert args[0] == "/usr/bin/true", args
    assert args[1].endswith("/tools/queue_reconcile.py"), args
    assert "bootstrap" in sb["launchctl_log"].read_text(encoding="utf-8")


def test_dry_run_installs_report_only_without_a_bogus_flag(tmp_path):
    """``--dry-run`` selects a report-only job, and emits NO mode argument —
    not a ``--dry-run`` the tool does not accept.

    Mutation: pass ``$MODE_FLAG`` (set to ``--dry-run``) through -> the
    scheduled command carries a flag the tool rejects -> RED.
    """
    sb = _sandbox(tmp_path)
    target = tmp_path / "agents"
    res = _install(sb, target, "--dry-run")
    assert res.returncode == 0, (res.stdout, res.stderr)
    args = _program_args(target / PLIST_NAME)
    assert "--apply" not in args, args
    assert "--dry-run" not in args, args
    assert args[-1].endswith("queue_reconcile.py"), args


def test_darwin_rerender_is_idempotent(tmp_path):
    """A byte-identical re-render skips the reload, so the installer is safe on
    every sync and does not churn the live agent.

    Mutation: drop the ``cmp -s`` unchanged branch -> the second run calls
    bootstrap again -> RED.
    """
    sb = _sandbox(tmp_path)
    target = tmp_path / "agents"
    assert _install(sb, target).returncode == 0
    first = sb["launchctl_log"].read_text(encoding="utf-8").count("bootstrap")
    res = _install(sb, target)
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "unchanged" in res.stdout, res.stdout
    assert sb["launchctl_log"].read_text(encoding="utf-8").count("bootstrap") == first


def test_rendered_plist_carries_home_and_gh_on_path(tmp_path):
    """The job's environment is explicit: ``HOME`` resolves the tool's default
    queue path and ``PATH`` contains the directory holding ``gh`` (launchd's
    own PATH is minimal, so an uncaptured gh would make every identifier
    UNKNOWN — a report with no information).

    Mutation: drop the EnvironmentVariables dict -> the plist has no PATH ->
    RED.
    """
    sb = _sandbox(tmp_path)
    target = tmp_path / "agents"
    assert _install(sb, target).returncode == 0
    with open(target / PLIST_NAME, "rb") as fh:
        doc = plistlib.load(fh)
    env = doc["EnvironmentVariables"]
    assert env["HOME"] == str(sb["home"])
    assert str(sb["bindir"]) in env["PATH"].split(":")
    assert doc["StartInterval"] == 21600, doc["StartInterval"]


def test_rendered_plist_is_strictly_well_formed_xml(tmp_path):
    """The rendered plist must parse under a STRICT XML parser, and rendering
    must not EXECUTE anything.

    The template is a quoted heredoc with literal placeholder substitution. An
    UNQUOTED heredoc runs command substitution on any backtick in its body, and
    the body necessarily names the `gh` binary — so rendering would run `gh`
    and splice its help (full of `--help`, which is illegal inside an XML
    comment) into the plist. `plutil -lint` is LENIENT about comment bodies and
    still says OK; Python's expat/plistlib refuse the file, and the job would
    be installed from a malformed document.

    Mutation: restore the unquoted heredoc (``<<PLIST``) with a backticked
    `gh` in the prose -> gh's help lands in the plist -> plistlib raises
    `not well-formed (invalid token)` and the leak assertion fires -> RED.
    """
    sb = _sandbox(tmp_path)
    target = tmp_path / "agents"
    res = _install(sb, target)
    assert res.returncode == 0, (res.stdout, res.stderr)
    plist = target / PLIST_NAME
    text = plist.read_text(encoding="utf-8")
    # 1. The command-substitution leak is the observable symptom.
    assert "Show help for command" not in text, (
        "rendering EXECUTED a command and spliced its output into the plist:\n" + text
    )
    assert "@@" not in text, f"an unresolved placeholder survived:\n{text}"
    # 2. Strict parse — the check plutil -lint does NOT give us.
    with open(plist, "rb") as fh:
        doc = plistlib.load(fh)
    assert doc["Label"] == LABEL
    assert doc["ProgramArguments"][0] == "/usr/bin/true"


def test_rendering_does_not_run_a_command_from_the_prose(tmp_path):
    """A second, independent angle on the same class: rendering must leave no
    trace of execution. The stub `gh` writes a sentinel file when RUN, so a
    heredoc regression is caught even if an editor reworks the help text.

    Mutation: unquote the heredoc and put a backticked command in the prose ->
    the sentinel appears -> RED.
    """
    sb = _sandbox(tmp_path)
    sentinel = tmp_path / "gh-was-run"
    _write_stub(
        sb["bindir"], "gh",
        f"#!/usr/bin/env bash\ntouch {sentinel}\nexit 0\n",
    )
    target = tmp_path / "agents"
    res = _install(sb, target)
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert not sentinel.exists(), (
        "rendering the plist EXECUTED `gh` — the heredoc is performing command "
        "substitution on the prose"
    )


# ── P1: never install a job that can only fail ─────────────────────────────

def test_refuses_an_interpreter_the_tool_rejects(tmp_path):
    """``tools/queue_reconcile.py`` carries a >= 3.12 runtime guard (#5128), so
    an install pointed at an older interpreter would fail on EVERY fire with no
    diagnostic gain. Refuse instead.

    Mutation: drop the ``sys.version_info >= (3, 12)`` probe -> the install
    proceeds rc 0 against an interpreter the tool refuses -> RED.
    """
    sb = _sandbox(tmp_path)
    sb["env"]["PYTHON_BIN"] = "/usr/bin/false"  # any probe exits nonzero
    target = tmp_path / "agents"
    res = _install(sb, target)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "3.12" in res.stderr, res.stderr
    assert not sb["launchctl_log"].exists(), "refusal still called launchctl"
    assert not (target / PLIST_NAME).exists(), "refusal still wrote a plist"


def test_refuses_when_the_queue_is_absent(tmp_path):
    """A job pointed at a missing queue fails on every fire (the tool exits 3).
    Refuse at install time instead of scheduling a dead job.

    Mutation: drop the ``[ -f "$QUEUE_PATH" ]`` check -> rc 0 and a plist that
    can never succeed -> RED.
    """
    sb = _sandbox(tmp_path, queue=False)
    target = tmp_path / "agents"
    res = _install(sb, target)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "queue not found" in res.stderr, res.stderr
    assert not sb["launchctl_log"].exists(), "refusal still called launchctl"


def test_refuses_when_gh_is_not_on_path(tmp_path):
    """With no ``gh``, every identifier resolves to UNKNOWN — the report carries
    no information and the job is dead weight.

    Mutation: drop the ``command -v gh`` check -> rc 0 and a useless schedule
    -> RED.
    """
    sb = _sandbox(tmp_path, gh=False)
    target = tmp_path / "agents"
    res = _install(sb, target)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "gh" in res.stderr, res.stderr
    assert not sb["launchctl_log"].exists(), "refusal still called launchctl"


def test_refuses_when_the_tool_is_absent(tmp_path):
    """A checkout without the tool must not get a schedule pointing at it.

    Mutation: drop the ``[ -f "$TOOL" ]`` check -> the plist is written with a
    broken target -> RED.
    """
    sb = _sandbox(tmp_path)
    (sb["repo"] / "tools" / "queue_reconcile.py").unlink()
    target = tmp_path / "agents"
    res = _install(sb, target)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "tool not found" in res.stderr, res.stderr


def test_broken_program_argument_target_refuses_the_install(tmp_path):
    """Even when the checks above pass, every absolute path in the rendered
    ProgramArguments must resolve before the job is installed — a dead job must
    never be installed.

    Mutation: drop ``verify_targets`` -> a plist naming an interpreter that
    does not exist is installed -> RED.
    """
    sb = _sandbox(tmp_path)
    sb["env"]["PYTHON_BIN"] = str(tmp_path / "gone" / "python")
    target = tmp_path / "agents"
    res = _install(sb, target)
    # The `-x` probe refuses this one first (it is also a nonexistent
    # interpreter); the invariant is the same either way: no job, no launchctl.
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert not sb["launchctl_log"].exists()
    assert not (target / PLIST_NAME).exists()


# ── P2: the numeric guard must fail CLOSED ─────────────────────────────────

def test_huge_interval_is_refused_closed(tmp_path):
    """A value too large for shell integer arithmetic is refused (exit 2), not
    silently wrapped past the ``>= 1`` guard.

    Mutation: remove the ``${#X}`` digit bound AND restore ``[ "$X" -lt 1 ]``
    -> the huge value passes the errored comparison and the install proceeds
    rc 0 -> RED.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    sb["env"]["QUEUE_RECONCILE_INTERVAL"] = "99999999999999999999"
    res = _run(sb)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "QUEUE_RECONCILE_INTERVAL" in res.stderr and "out of range" in res.stderr
    assert not sb["crontab_store"].exists(), "corrupt schedule was installed"


def test_zero_interval_is_refused(tmp_path):
    """``QUEUE_RECONCILE_INTERVAL=0`` is refused rather than arming a
    zero-length interval.

    Mutation: drop the ``>= 1`` guard -> rc 0 and a ``*/0`` cron schedule -> RED.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    sb["env"]["QUEUE_RECONCILE_INTERVAL"] = "0"
    res = _run(sb)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "QUEUE_RECONCILE_INTERVAL" in res.stderr and ">= 1" in res.stderr
    assert not sb["crontab_store"].exists()


def test_empty_interval_falls_back_to_default(tmp_path):
    """An empty ``QUEUE_RECONCILE_INTERVAL`` uses the default (21600), not the
    empty string.

    Mutation: replace ``${QUEUE_RECONCILE_INTERVAL:-21600}`` with the bare
    expansion -> the empty value hits the digit guard -> RED.
    """
    sb = _sandbox(tmp_path)
    target = tmp_path / "agents"
    sb["env"]["QUEUE_RECONCILE_INTERVAL"] = ""
    res = _install(sb, target)
    assert res.returncode == 0, (res.stdout, res.stderr)
    with open(target / PLIST_NAME, "rb") as fh:
        assert plistlib.load(fh)["StartInterval"] == 21600


# ── P2: cron rendering and marker replacement ─────────────────────────────

def test_linux_default_schedule_is_six_hourly_and_armed(tmp_path):
    """The default 21600s renders ``0 */6 * * *`` — NOT ``*/360``, which cron
    evaluates over the 0-59 minute range and matches minute 0 only (SILENTLY
    hourly) — and carries ``--apply``.

    Mutation: restore the blind ``*/minutes`` render -> ``*/360`` -> RED.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    res = _run(sb)
    assert res.returncode == 0, (res.stdout, res.stderr)
    line = _cron_schedule_line(sb)
    assert line.startswith("0 */6 * * *"), line
    assert "--apply" in line, line
    assert "WARNING" not in res.stderr, res.stderr


def test_linux_two_hour_interval_renders_hour_field(tmp_path):
    """``QUEUE_RECONCILE_INTERVAL=7200`` renders ``0 */2 * * *``."""
    sb = _sandbox(tmp_path, uname="Linux")
    sb["env"]["QUEUE_RECONCILE_INTERVAL"] = "7200"
    res = _run(sb)
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert _cron_schedule_line(sb).startswith("0 */2 * * *")


def test_linux_non_exact_interval_warns_and_runs_hourly(tmp_path):
    """An interval cron cannot express exactly warns and schedules hourly at
    minute 0 instead of silently pretending to honour the step."""
    sb = _sandbox(tmp_path, uname="Linux")
    sb["env"]["QUEUE_RECONCILE_INTERVAL"] = "9000"  # 150 min
    res = _run(sb)
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "cannot express" in res.stderr
    assert _cron_schedule_line(sb).startswith("0 * * * *")


def test_linux_rerun_replaces_the_marker_not_accumulates(tmp_path):
    """Re-running the installer leaves exactly ONE marker + ONE schedule line
    and preserves the user's own entries — no stale repo path survives.

    Mutation: drop the fixed-string replacement (always append) -> two markers
    after the second run and the old repo line survives -> RED.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    sb["crontab_store"].write_text(
        "# personal crontab line\n",
        encoding="utf-8",
    )
    for _ in range(2):
        res = _run(sb)
        assert res.returncode == 0, (res.stdout, res.stderr)
    text = sb["crontab_store"].read_text(encoding="utf-8")
    assert text.count(MARKER) == 1, text
    assert text.count("queue_reconcile.py") == 1, text
    assert text.count("# personal crontab line") == 1, text


def test_linux_replaces_a_stale_schedule_line(tmp_path):
    """A pre-existing (stale-repo) schedule line is removed, not left running
    alongside the new one.

    Mutation: match only the marker (not ``queue_reconcile.py``) when filtering
    -> the old line survives -> RED.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    sb["crontab_store"].write_text(
        f"{MARKER}\n"
        "*/10 * * * * /old/py /old/repo/tools/queue_reconcile.py --apply "
        ">> /old/log 2>&1\n",
        encoding="utf-8",
    )
    res = _run(sb)
    assert res.returncode == 0, (res.stdout, res.stderr)
    text = sb["crontab_store"].read_text(encoding="utf-8")
    assert "/old/repo" not in text, text
    assert text.count("queue_reconcile.py") == 1, text


def test_linux_uninstall_removes_our_entries_and_preserves_others(tmp_path):
    """``--uninstall`` removes the marker + schedule line and keeps the user's
    own entries.

    Mutation: drop the filter in uninstall -> the marker survives -> RED.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    sb["crontab_store"].write_text(
        "# keep me\n"
        f"{MARKER}\n"
        "0 */6 * * * /py /repo/tools/queue_reconcile.py --apply\n",
        encoding="utf-8",
    )
    res = _run(sb, "--uninstall")
    assert res.returncode == 0, (res.stdout, res.stderr)
    text = sb["crontab_store"].read_text(encoding="utf-8")
    assert MARKER not in text, text
    assert "queue_reconcile.py" not in text, text
    assert "# keep me" in text, text


def test_linux_uninstall_of_only_our_entries_exits_zero(tmp_path):
    """A crontab holding ONLY our entries — exactly what this installer
    creates on a machine with no other cron jobs — uninstalls with rc 0.
    ``grep -v`` selects nothing (exit 1), and under ``pipefail`` that made the
    write look failed and skipped the success message.

    Mutation: put ``|| return 1`` back on the grep->crontab pipeline with no
    ``|| true`` guard -> rc 1 and no output -> RED.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    sb["crontab_store"].write_text(
        f"{MARKER}\n"
        "0 */6 * * * /py /repo/tools/queue_reconcile.py --apply\n",
        encoding="utf-8",
    )
    res = _run(sb, "--uninstall")
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "removed cron entry" in res.stdout
    assert sb["crontab_store"].read_text(encoding="utf-8").strip() == ""


# ── the installer's own contract ───────────────────────────────────────────

def test_help_documents_the_claim_root_and_exits_zero(tmp_path):
    """``--help`` prints the documented usage (the file header) and exits 0."""
    sb = _sandbox(tmp_path)
    res = _run(sb, "--help")
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "--apply" in res.stdout
    assert "#7810" in res.stdout
    assert not sb["launchctl_log"].exists(), "--help must not touch launchd"


def test_unknown_argument_is_a_usage_error(tmp_path):
    """An unrecognised argument is refused loudly rather than silently ignored."""
    sb = _sandbox(tmp_path)
    res = _run(sb, "--nope")
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "unknown argument" in res.stderr
    assert not sb["launchctl_log"].exists()
