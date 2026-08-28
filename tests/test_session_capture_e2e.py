"""#1727 Slice 2 (Task 14) — hook smoke tests (T1 wiring, exit-0 guarantee).

The Claude Code hooks (tortoise/claude-hooks/session-{start,end}.sh) are
bash — these tests drive them with a MOCKED ``tortoise`` binary on PATH so
no config / network / DB is needed:

  - session-end.sh: parses the SessionEnd metadata (session_id +
    transcript_path) from stdin, converts the transcript, and calls
    ``tortoise session capture --file <tmp> --harness claude --session-id
    <id>`` — the harness + real session_id pass-through (T1-P11) — and the
    hook ALWAYS exits 0 (a failing capture must never block session close).
  - session-start.sh: fires ``tortoise context`` (memory digest) AND the
    install-probe beacon ``tortoise session probe --harness claude``
    (T2-P1 — the server-visible install signal), best-effort exit 0.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "tortoise" / "claude-hooks"

SESSION_END = HOOKS_DIR / "session-end.sh"
SESSION_START = HOOKS_DIR / "session-start.sh"

pytestmark = pytest.mark.skipif(
    not SESSION_END.exists() or not SESSION_START.exists(),
    reason="claude-hooks scripts not present")


def _write_mock_tortoise(tmp_path: Path, log: Path, *, fail_capture: bool = False) -> Path:
    """A fake `tortoise` CLI: records every invocation to ``log`` and
    simulates session capture (success or failure). Placed on PATH so the
    hooks' installed-bin branch runs (no source fallback)."""
    mock = tmp_path / "bin" / "tortoise"
    mock.parent.mkdir(parents=True, exist_ok=True)
    fail_line = 'echo "boom" >&2; exit 1' if fail_capture else \
        'echo \'{"session_id": "mock-session-1", "extraction_mode": "llm:mock", "turns": 2}\''
    mock.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        'if [ "$1" = "session" ] && [ "$2" = "capture" ]; then\n'
        f'  {fail_line}\n'
        "  exit 0\n"
        "fi\n"
        # sweep / context / probe / anything else: succeed silently
        "exit 0\n",
        encoding="utf-8")
    mock.chmod(mock.stat().st_mode | stat.S_IEXEC)
    return mock.parent


def _run_hook(hook: Path, stdin_data: str, path: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{path}:{env.get('PATH', '')}"
    env.pop("TORTOISE_SRC_DIR", None)
    return subprocess.run(
        ["bash", str(hook)], input=stdin_data, capture_output=True, text=True,
        env=env, timeout=60)


@pytest.fixture()
def transcript(tmp_path):
    p = tmp_path / "transcript.jsonl"
    p.write_text(
        json.dumps({"type": "user", "message": {"role": "user",
                                                "content": "hello"}}) + "\n" +
        json.dumps({"type": "assistant",
                    "message": {"role": "assistant", "content": "hi"}}) + "\n",
        encoding="utf-8")
    return p


def test_session_end_forwards_harness_and_session_id(tmp_path, transcript):
    """T1-P11: session-end.sh forwards --harness claude + the REAL
    session_id from hook metadata; exit 0; the mocked POST-shaped capture
    runs (Session + receipt is the server side — the smoke covers the hook
    call contract + the exit-0 guarantee)."""
    log = tmp_path / "calls.log"
    bindir = _write_mock_tortoise(tmp_path, log)
    meta = json.dumps({"session_id": "s-hook-abc", "cwd": "/tmp",
                       "transcript_path": str(transcript)})
    r = _run_hook(SESSION_END, meta, bindir)
    assert r.returncode == 0, f"hook must exit 0 (stderr: {r.stderr})"
    calls = log.read_text()
    assert "session capture" in calls, calls
    # the capture invocation carries --harness claude AND --session-id
    assert "--harness claude" in calls, calls
    assert "--session-id s-hook-abc" in calls, calls


def test_session_end_exit0_on_capture_failure(tmp_path, transcript):
    """The hook ALWAYS exits 0 — a failing capture (mock exits 1) must never
    block Claude's session close."""
    log = tmp_path / "calls.log"
    bindir = _write_mock_tortoise(tmp_path, log, fail_capture=True)
    meta = json.dumps({"session_id": "s-hook-fail", "cwd": "/tmp",
                       "transcript_path": str(transcript)})
    r = _run_hook(SESSION_END, meta, bindir)
    assert r.returncode == 0, \
        f"hook must exit 0 even when capture fails (stderr: {r.stderr})"


def test_session_end_no_metadata_exits_0(tmp_path):
    """No stdin metadata → skip silently (exit 0) — the mock is never
    invoked (no transcript to convert, no capture to fire)."""
    log = tmp_path / "calls.log"
    bindir = _write_mock_tortoise(tmp_path, log)
    r = _run_hook(SESSION_END, "", bindir)
    assert r.returncode == 0
    # nothing was captured — no capture call in the log (if the mock ran at
    # all, which it must not for a metadata-less session)
    assert not log.exists() or "capture" not in log.read_text()


def test_session_start_fires_context_and_probe(tmp_path):
    """T2-P1: session-start.sh fires the memory digest AND the install-probe
    beacon (tortoise session probe --harness claude) — the server-visible
    install signal — best-effort exit 0."""
    log = tmp_path / "calls.log"
    bindir = _write_mock_tortoise(tmp_path, log)
    r = _run_hook(SESSION_START, "", bindir)
    assert r.returncode == 0, f"session-start must exit 0 (stderr: {r.stderr})"
    calls = log.read_text()
    assert "context" in calls, calls
    assert "session probe --harness claude" in calls, calls


def test_session_start_probe_failure_exits_0(tmp_path):
    """The probe is best-effort — a failing probe (no config, unreachable
    API) must not block the session start digest."""
    log = tmp_path / "calls.log"
    bindir = _write_mock_tortoise(tmp_path, log, fail_capture=True)
    # probe hits the same mocked `session` subcommand path — force failure
    # via a probe-specific mock that exits 1 for `session probe`.
    probe_fail = bindir / "tortoise"
    probe_fail.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        'if [ "$1" = "session" ] && [ "$2" = "probe" ]; then\n'
        '  exit 1\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8")
    probe_fail.chmod(probe_fail.stat().st_mode | stat.S_IEXEC)
    r = _run_hook(SESSION_START, "", bindir)
    assert r.returncode == 0, f"session-start must exit 0 (stderr: {r.stderr})"
    assert "probe" in log.read_text()
