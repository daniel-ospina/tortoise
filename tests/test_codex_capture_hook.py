"""#3818 — the shipped Codex capture hook (`tortoise/codex-hooks/session-end.sh`).

These tests drive the REAL script with a controlled PATH, HOME and a fake
`tortoise` on PATH, and assert the resolved outcome: the argv the capture step
receives, the fail-open exits, and — load-bearing — that the hook DETACHES.

Codex CLI 0.154.0 kills a `SessionEnd` command hook at a hard ~1 s budget
(measured live 2026-09-18: a hook whose only work was `sleep 1` never reached
its next line). The shipped hook must therefore return immediately and let a
detached worker do the slow POST. A hook that runs the capture synchronously
would be killed mid-flight and file nothing — the exact silent-no-capture
failure this seam exists to prevent.

Every docstring names the mutation that turns it RED.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from pathlib import Path

from tortoise.capture_install import install_capture
from tortoise.hook_install import count_canonical_markers, read_hook_version

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "tortoise" / "codex-hooks" / "session-end.sh"
VERSION_MARKER = "# tortoise-hook-version: 1"


def _fake_tortoise(bindir: Path, log: Path, *, sleep_s: float = 0.0) -> None:
    """A `tortoise` that records its argv (and a DONE marker after ``sleep_s``)."""
    script = bindir / "tortoise"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'sleep {sleep_s}\n'
        f'printf "%s\\n" "$@" >> {log}\n'
        f'echo DONE >> {log}\n',
        encoding="utf-8",
    )
    script.chmod(0o755)


def _run_hook(stdin_json: str, *, home: Path, bindir: Path, timeout: float = 15):
    env = {
        "HOME": str(home),
        "PATH": f"{bindir}:/usr/bin:/bin",
        # The module fallback must not accidentally find a real checkout.
        "TORTOISE_SRC_DIR": str(home / "no-checkout"),
        "TMPDIR": str(home / "tmp"),
    }
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    proc = subprocess.run(
        ["/bin/bash", str(HOOK)],
        input=stdin_json,
        text=True,
        capture_output=True,
        env=env,
        timeout=timeout,
    )
    return proc, time.monotonic() - start


def test_hook_artifact_carries_the_version_marker():
    """The install contract is one marker, column-0, one per file.

    Mutation: delete ``# tortoise-hook-version: 1`` from the shipped hook — the
    install then has no generation to compare and this REDs."""
    text = HOOK.read_text(encoding="utf-8")
    assert text.startswith(f"#!/usr/bin/env bash\n{VERSION_MARKER}\n"), text[:120]
    assert read_hook_version(HOOK) == 1
    assert count_canonical_markers(HOOK) == 1, (
        "exactly one column-0 marker (an in-body mention is not a declaration)")


def test_hook_detaches_so_codex_cannot_kill_the_capture(tmp_path):
    """The measured ~1 s SessionEnd budget: the hook must return immediately
    and the capture must complete AFTER the parent has exited.

    Mutation: drop the trailing ``&``/``disown`` (run the capture
    synchronously) — the hook then blocks for the capture's duration and
    ``elapsed`` fails its bound."""
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "argv.log"
    home.mkdir()
    bindir.mkdir()
    _fake_tortoise(bindir, log, sleep_s=4.0)
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text('{"type":"response_item","payload":{"type":"message",'
                       '"role":"user","content":[{"type":"input_text",'
                       '"text":"hi"}]}}\n', encoding="utf-8")

    proc, elapsed = _run_hook(
        json.dumps({"session_id": "sid-1", "transcript_path": str(rollout),
                    "cwd": str(tmp_path), "hook_event_name": "SessionEnd",
                    "reason": "other"}),
        home=home, bindir=bindir)
    assert proc.returncode == 0, proc.stderr
    assert elapsed < 2.5, (
        f"the hook blocked for {elapsed:.1f}s — Codex kills SessionEnd at ~1s, "
        "so a synchronous capture is filed never")
    assert not log.exists(), "the capture finished before the hook returned"

    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if log.exists() and "DONE" in log.read_text(encoding="utf-8"):
            break
        time.sleep(0.2)
    assert log.exists() and "DONE" in log.read_text(encoding="utf-8"), (
        "the detached worker did not survive the hook's exit")


def test_hook_files_the_real_rollout_with_harness_codex_and_the_session_id(tmp_path):
    """The capture step is `sessions import --harness codex` with the REAL
    SessionEnd session_id as the idempotency key.

    Mutation: hardcode ``--harness claude`` (or drop ``--session-id``) — the
    server files the session under the wrong bucket / loses convergence and
    this REDs."""
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "argv.log"
    home.mkdir()
    bindir.mkdir()
    _fake_tortoise(bindir, log)
    rollout = tmp_path / "rollout-2026.jsonl"
    rollout.write_text("{}\n", encoding="utf-8")

    proc, _ = _run_hook(
        json.dumps({"session_id": "01a0b5c6-e7f1", "transcript_path": str(rollout),
                    "cwd": str(tmp_path), "hook_event_name": "SessionEnd",
                    "reason": "other"}),
        home=home, bindir=bindir)
    assert proc.returncode == 0, proc.stderr

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not log.exists():
        time.sleep(0.1)
    argv = [t for t in log.read_text(encoding="utf-8").split() if t != "DONE"]
    assert argv == ["sessions", "import", "--file", str(rollout),
                    "--harness", "codex", "--session-id", "01a0b5c6-e7f1"], argv


def test_hook_is_fail_open_when_transcript_path_is_null(tmp_path):
    """`transcript_path` is NULLABLE in Codex's SessionEnd schema — a null path
    is a clean no-op, never a crash.

    Mutation: drop the empty-path guard — the worker runs with an empty
    ``--file`` and this REDs."""
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "argv.log"
    home.mkdir()
    bindir.mkdir()
    _fake_tortoise(bindir, log)

    proc, _ = _run_hook(
        json.dumps({"session_id": "sid", "transcript_path": None,
                    "cwd": str(tmp_path), "hook_event_name": "SessionEnd",
                    "reason": "other"}),
        home=home, bindir=bindir)
    assert proc.returncode == 0, proc.stderr
    time.sleep(1.0)
    assert not log.exists(), "a null transcript_path still invoked the capture"


def test_hook_is_fail_open_when_transcript_is_missing_on_disk(tmp_path):
    """A transcript_path that no longer exists must not invoke the capture.

    Mutation: drop the ``-f`` test — a stale path reaches the CLI and this
    REDs."""
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "argv.log"
    home.mkdir()
    bindir.mkdir()
    _fake_tortoise(bindir, log)

    proc, _ = _run_hook(
        json.dumps({"session_id": "sid",
                    "transcript_path": str(tmp_path / "gone.jsonl"),
                    "cwd": str(tmp_path), "hook_event_name": "SessionEnd",
                    "reason": "other"}),
        home=home, bindir=bindir)
    assert proc.returncode == 0, proc.stderr
    time.sleep(1.0)
    assert not log.exists(), "a missing transcript still invoked the capture"


def test_hook_exits_zero_on_empty_stdin(tmp_path):
    """Codex never blocks on memory capture — garbage/empty stdin is a no-op.

    Mutation: remove the empty-stdin guard — the worker is spawned with an
    unreadable payload and this REDs on the log assertion."""
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "argv.log"
    home.mkdir()
    bindir.mkdir()
    _fake_tortoise(bindir, log)

    proc, _ = _run_hook("", home=home, bindir=bindir)
    assert proc.returncode == 0, proc.stderr
    time.sleep(1.0)
    assert not log.exists()


def test_installed_hook_is_executable_by_its_owner(tmp_path):
    """Codex executes the registered command directly, so the install must
    produce an owner-executable script.

    Mutation: install with mode 0o644 — Codex cannot run the hook and this
    REDs."""
    home = tmp_path / "home"
    home.mkdir()
    result = install_capture("codex", home=home)
    assert result.ok, result.error
    installed = home / ".codex" / "hooks" / "tortoise-session-end.sh"
    assert installed.is_file()
    mode = installed.stat().st_mode
    assert mode & stat.S_IXUSR, f"installed hook is not owner-executable: {mode:o}"
    assert installed.read_bytes() == HOOK.read_bytes(), (
        "the installed hook is not the shipped artifact byte-for-byte")


def test_reinstall_repairs_a_hook_that_lost_its_exec_bit(tmp_path):
    """A `cp`-without-`chmod` install files nothing while reporting success —
    the install must repair the owner exec bit.

    Mutation: skip the exec-bit repair in ``_install_script`` — this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    assert install_capture("codex", home=home).ok
    installed = home / ".codex" / "hooks" / "tortoise-session-end.sh"
    os.chmod(installed, 0o644)

    again = install_capture("codex", home=home)
    assert again.ok, again.error
    assert again.changed is True, again.actions
    assert installed.stat().st_mode & stat.S_IXUSR
