"""Hermetic tests for ``tools/install-reaper-schedule.sh`` (#4438 review).

The installer writes a schedule a *real* run would point at the live user
agent — `launchctl` addresses the domain **by uid**, not by ``$HOME`` — so a
throwaway ``HOME``/``AGENTS_DIR`` is NOT a sandbox (two review runs on this
host actually left the live agent pointing at a deleted temp plist). These
tests therefore NEVER execute the real install path: they render into a
throwaway sandbox with ``uname``, ``launchctl``, ``plutil`` and ``crontab``
stubbed on ``PATH``, and assert the script's own decisions (refusal, rendered
schedule, fixed-string marker replacement) rather than the host's schedule.

Each test whose docstring carries a ``Mutation:`` line names the mutation
that turns it RED.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "install-reaper-schedule.sh"

_MARKER = "# tortoise-embedded-reaper (#1642)"
_PLIST_NAME = "com.tortoise.embedded-reaper.plist"

_ENV_LEAKS = (
    "AGENTS_DIR",
    "REAPER_ALLOW_NONSTANDARD_AGENTS_DIR",
    "REAPER_TIMEOUT",
    "REAPER_JOBS",
    "REAPER_INTERVAL",
)


def _write_stub(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _sandbox(tmp_path: Path, uname: str = "Darwin") -> dict:
    """Throwaway HOME/repo/log + PATH stubs for every external tool.

    ``launchctl``/``crontab`` only RECORD invocations — they never touch the
    host. Returns the env dict plus the paths the tests inspect.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    logdir = tmp_path / "log"
    logdir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()

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

    env = dict(os.environ)
    for key in _ENV_LEAKS:
        env.pop(key, None)
    env.update({
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(home),
        "STUB_LOG": str(logdir),
        "TORTOISE_REPO": str(repo),
        "PYTHON_BIN": "/usr/bin/true",
        "CRONTAB_CMD": "crontab",
    })
    return {
        "env": env,
        "home": home,
        "log": logdir,
        "repo": repo,
        "launchctl_log": logdir / "launchctl.log",
        "crontab_store": logdir / "crontab.txt",
    }


def _run(sb: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=sb["env"], capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _schedule_line(sb: dict) -> str:
    lines = [
        ln for ln in sb["crontab_store"].read_text(encoding="utf-8").splitlines()
        if "tortoise.embedded_reaper" in ln
    ]
    assert len(lines) == 1, f"expected exactly one schedule line, got {lines!r}"
    return lines[0]


def _plist_jobs(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    anchor = "<string>--jobs</string>\n    <string>"
    assert anchor in text, f"--jobs not in rendered plist:\n{text}"
    return text.split(anchor, 1)[1].split("</string>", 1)[0]


# ── P1: a throwaway HOME/AGENTS_DIR must not re-point the live agent ────────

def test_darwin_refuses_nonstandard_agents_dir_without_optin(tmp_path):
    """A non-standard AGENTS_DIR is refused (exit 2) before any launchctl
    call or plist write.

    Mutation: drop ``require_standard_agents_dir`` from install_darwin ->
    rc 0, launchctl.log exists, plist written -> RED.
    """
    sb = _sandbox(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    sb["env"]["AGENTS_DIR"] = str(elsewhere)
    res = _run(sb)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "refusing" in res.stderr.lower()
    assert not sb["launchctl_log"].exists(), "refusal still called launchctl"
    assert not (elsewhere / _PLIST_NAME).exists(), "refusal still wrote a plist"


def test_darwin_refuses_throwaway_home_with_standard_agents_dir(tmp_path):
    """The *sandbox illusion* itself: a throwaway HOME whose AGENTS_DIR is
    ``$HOME/Library/LaunchAgents`` is still refused, because the live agent
    lives under the LOGIN ACCOUNT's home, not the exported one.

    Mutation: compare ``AGENTS_DIR`` against ``$HOME/Library/LaunchAgents``
    (the literal finding wording) instead of the login home -> the guard
    passes and bootstrap runs -> RED.
    """
    sb = _sandbox(tmp_path)
    sb["env"]["AGENTS_DIR"] = str(sb["home"] / "Library" / "LaunchAgents")
    res = _run(sb)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "refusing" in res.stderr.lower()
    assert not sb["launchctl_log"].exists(), "throwaway HOME still bootstrapped"


def test_darwin_optin_allows_nonstandard_agents_dir(tmp_path):
    """``REAPER_ALLOW_NONSTANDARD_AGENTS_DIR=1`` permits the install and the
    rendered plist carries the new schedule values."""
    sb = _sandbox(tmp_path)
    target = tmp_path / "elsewhere"
    sb["env"]["AGENTS_DIR"] = str(target)
    sb["env"]["REAPER_ALLOW_NONSTANDARD_AGENTS_DIR"] = "1"
    res = _run(sb)
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "bootstrap" in sb["launchctl_log"].read_text(encoding="utf-8")
    plist = target / _PLIST_NAME
    assert plist.exists()
    text = plist.read_text(encoding="utf-8")
    assert "<key>StartInterval</key><integer>1200</integer>" in text
    assert _plist_jobs(plist) == "16"


# ── P2: the numeric guard must fail CLOSED on an out-of-range value ─────────

def test_huge_numeric_value_is_refused_closed(tmp_path):
    """A value too large for shell integer arithmetic is refused (exit 2),
    not silently wrapped past the ``>= 1`` guard.

    Mutation: remove the ``${#X}`` digit bound AND restore ``[ "$X" -lt 1 ]``
    -> the huge value passes the errored comparison and the digit guard, and
    the install proceeds rc 0 -> RED. (The digit bound alone refuses it, so
    reverting the comparison alone does NOT redden this test.)
    """
    sb = _sandbox(tmp_path, uname="Linux")
    sb["env"]["AGENTS_DIR"] = str(tmp_path / "elsewhere")
    sb["env"]["REAPER_ALLOW_NONSTANDARD_AGENTS_DIR"] = "1"
    sb["env"]["REAPER_TIMEOUT"] = "99999999999999999999"
    res = _run(sb)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "REAPER_TIMEOUT" in res.stderr and "out of range" in res.stderr
    assert not sb["crontab_store"].exists(), "corrupt schedule was installed"


def test_huge_reaper_jobs_value_is_refused_closed(tmp_path):
    """Same fail-closed guard for ``REAPER_JOBS``."""
    sb = _sandbox(tmp_path, uname="Linux")
    sb["env"]["REAPER_JOBS"] = "99999999999999999999"
    res = _run(sb)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "REAPER_JOBS" in res.stderr and "out of range" in res.stderr


def test_huge_reaper_interval_is_refused_closed(tmp_path):
    """``REAPER_INTERVAL`` has NO digit bound, so the fail-closed comparison
    is its only defense — a 20-digit value must be refused, not wrapped into
    the schedule arithmetic.

    Mutation: delete the ``REAPER_INTERVAL >= 1`` guard -> the huge value
    reaches the arithmetic, wraps, and the install proceeds rc 0 -> RED.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    sb["env"]["REAPER_INTERVAL"] = "99999999999999999999"
    res = _run(sb)
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "REAPER_INTERVAL" in res.stderr and ">= 1" in res.stderr
    assert not sb["crontab_store"].exists(), "wrapped schedule was installed"


def test_zero_and_negative_knobs_are_refused(tmp_path):
    """``REAPER_TIMEOUT=0``/``REAPER_JOBS=0`` are still refused (the >= 1
    guard is not weakened by the fail-closed rewrite)."""
    for var in ("REAPER_TIMEOUT", "REAPER_JOBS"):
        sb = _sandbox(tmp_path / var, uname="Linux")
        sb["env"][var] = "0"
        res = _run(sb)
        assert res.returncode == 2, (var, res.stdout, res.stderr)
        assert var in res.stderr and ">= 1" in res.stderr


def test_empty_reaper_jobs_falls_back_to_default(tmp_path):
    """An empty ``REAPER_JOBS`` uses the default (16), never errors.

    Mutation: replace ``${REAPER_JOBS:-16}`` with ``${REAPER_JOBS}`` ->
    the empty value hits the digit/length guard -> RED.
    """
    sb = _sandbox(tmp_path)
    target = tmp_path / "elsewhere"
    sb["env"]["AGENTS_DIR"] = str(target)
    sb["env"]["REAPER_ALLOW_NONSTANDARD_AGENTS_DIR"] = "1"
    sb["env"]["REAPER_JOBS"] = ""
    res = _run(sb)
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert _plist_jobs(target / _PLIST_NAME) == "16"


# ── P2: the cron marker must be REPLACED, not accumulated ───────────────────

def test_linux_cron_marker_replaced_not_accumulated(tmp_path):
    """Re-running the installer (this PR documents the re-run upgrade path)
    leaves exactly ONE marker + ONE schedule line, preserving other entries.

    Mutation: restore the broken ``grep -vE`` pattern -> two markers after
    the second run and a stale ``/old`` repo line survives -> RED.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    sb["crontab_store"].write_text(
        "# personal crontab line\n"
        f"{_MARKER}\n"
        "*/10 * * * * cd /old/repo && /old/py -m tortoise.embedded_reaper "
        "--no-dry-run --only-safe --timeout 300\n",
        encoding="utf-8",
    )
    for _ in range(2):
        res = _run(sb)
        assert res.returncode == 0, (res.stdout, res.stderr)
    text = sb["crontab_store"].read_text(encoding="utf-8")
    assert text.count(_MARKER) == 1, text
    assert text.count("tortoise.embedded_reaper") == 1, text
    assert text.count("# personal crontab line") == 1, text
    assert "/old/repo" not in text, "stale schedule line survived"


def test_linux_default_schedule_uses_20_minutes(tmp_path):
    """The default interval renders ``*/20`` with ``--timeout 900 --jobs 16``."""
    sb = _sandbox(tmp_path, uname="Linux")
    res = _run(sb)
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert _schedule_line(sb).startswith("*/20 * * * *")
    assert _schedule_line(sb).endswith(
        "--timeout 900 --jobs 16 >> $HOME/.tortoise/reaper.log 2>&1")


def test_linux_uninstall_removes_marker_and_schedule(tmp_path):
    """``--uninstall`` removes the marker + schedule line, and preserves the
    user's own entries.

    Mutation: revert the fixed-string ``grep`` in uninstall -> the marker is
    left behind -> RED.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    sb["crontab_store"].write_text(
        "# keep me\n"
        f"{_MARKER}\n"
        "*/20 * * * * cd /repo && py -m tortoise.embedded_reaper --x\n",
        encoding="utf-8",
    )
    res = _run(sb, "--uninstall")
    assert res.returncode == 0, (res.stdout, res.stderr)
    text = sb["crontab_store"].read_text(encoding="utf-8")
    assert _MARKER not in text, text
    assert "tortoise.embedded_reaper" not in text, text
    assert "# keep me" in text, text


def test_linux_uninstall_of_only_reaper_entries_exits_zero(tmp_path):
    """A crontab holding ONLY the marker + schedule line (exactly what this
    installer creates on a machine with no other cron jobs) uninstalls with
    rc 0. ``grep -v`` selects nothing -> exit 1, and under ``pipefail`` the
    write was reported as failed, skipping the success message.

    Mutation: put ``|| return 1`` back on the grep->crontab pipeline with no
    ``|| true`` guard -> rc 1 and no output -> RED.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    sb["crontab_store"].write_text(
        f"{_MARKER}\n"
        "*/20 * * * * cd /repo && py -m tortoise.embedded_reaper --x\n",
        encoding="utf-8",
    )
    res = _run(sb, "--uninstall")
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "removed cron entry" in res.stdout
    assert sb["crontab_store"].read_text(encoding="utf-8").strip() == "", \
        sb["crontab_store"].read_text(encoding="utf-8")


# ── P2: an interval >= 60 min must not silently mean "hourly" on cron ───────

def test_linux_interval_two_hours_renders_hour_field(tmp_path):
    """``REAPER_INTERVAL=7200`` renders ``0 */2 * * *`` (every 2 hours), not
    the silent ``*/120`` (= hourly, since the minute range is 0-59).

    Mutation: restore the blind ``*/minutes`` render -> ``*/120`` -> RED.
    """
    sb = _sandbox(tmp_path, uname="Linux")
    sb["env"]["REAPER_INTERVAL"] = "7200"
    res = _run(sb)
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert _schedule_line(sb).startswith("0 */2 * * *")
    assert "WARNING" not in res.stderr, res.stderr


def test_linux_non_exact_interval_warns_and_runs_hourly(tmp_path):
    """An interval cron cannot express exactly warns and schedules hourly at
    minute 0 instead of silently pretending to honour the step."""
    sb = _sandbox(tmp_path, uname="Linux")
    sb["env"]["REAPER_INTERVAL"] = "9000"  # 150 min
    res = _run(sb)
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "cannot express" in res.stderr
    assert _schedule_line(sb).startswith("0 * * * *")
