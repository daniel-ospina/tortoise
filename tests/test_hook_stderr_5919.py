"""#5919 — a hook's best-effort breadcrumb write must not leak onto stderr.

Bash applies redirections left to right and reports a failed open of the
**stdout** target *before* a later ``2>/dev/null`` takes effect, so the
trailing-``2>/dev/null`` form used by every breadcrumb writer:

    printf '...' > "$dir/$harness.json" 2>/dev/null || true

made **the shell itself** write a raw error to the hook's stderr whenever the
HOME-scoped target directory was unwritable (``HOME=""`` →
``${HOME:-/nonexistent}``).  The hooks' contract says their stderr stays clean
(a SessionStart/SessionEnd hook must not print noise into the harness), and
``|| true`` only rescues the *exit status*, never the already-emitted error.

These tests drive the REAL shipped hook scripts with a hostile environment and
assert **byte-empty stderr** plus unchanged (empty) stdout.  Each hook is
installed where ``../..`` is NOT a checkout — the same trick
``tests/test_4314_inert_hooks.py`` uses.  That pins a SPECIFIC inert branch and
keeps the test independent of the repo layout: running the repo copy in place
resolves the repo's own ``tortoise/`` package, which for ``session-end.sh``
would proceed past the breadcrumb branch, and for ``session-start.sh`` would
take the sibling "resolved a module dir but found no python3" branch.

``session-turn.sh`` is FROZEN by a standing hard rule (its "stdout must stay
empty" contract must not change, and its behaviour must not be altered), so it
appears here only as a stdout guard — never a stderr assertion and never a fix.
Its residual leak is tracked in #5919.

Each test names the mutation that turns it RED.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

CLAUDE_START = REPO / "tortoise" / "claude-hooks" / "session-start.sh"
CLAUDE_END = REPO / "tortoise" / "claude-hooks" / "session-end.sh"
CLAUDE_VOLUNTEER = REPO / "tortoise" / "claude-hooks" / "volunteer-turn.sh"
CLAUDE_TURN = REPO / "tortoise" / "claude-hooks" / "session-turn.sh"
CODEX_END = REPO / "tortoise" / "codex-hooks" / "session-end.sh"
CURSOR_END = REPO / "tortoise" / "cursor-hooks" / "session-end.sh"

#: The shell plumbing the hooks need.  ``python3`` is NOT here and neither is
#: ``tortoise`` — one failure branch is reached *because* the interpreter is
#: missing, so a sandbox that leaks the developer's interpreter would not
#: exercise it.
_TOOLS = ("cat", "tr", "head", "mkdir", "date", "dirname", "sed", "basename",
          "rm", "env", "mktemp", "sleep", "awk", "grep", "cut", "nohup",
          "sort", "uniq", "wc", "ls", "pwd", "chmod", "cp", "mv")


def _install_hook(hook: Path, tmp_path: Path, name: str) -> Path:
    """Copy ``hook`` to ``<tmp>/.claude/hooks/<name>`` so its ``../..``
    (``<tmp>``) is not a checkout and the inert branches are reachable."""
    dest = tmp_path / ".claude" / "hooks" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(hook, dest)
    dest.chmod(0o755)
    return dest


def _sandbox_bin(tmp_path: Path, *, python3: bool) -> Path:
    bindir = tmp_path / ("bin-py" if python3 else "bin-nopy")
    bindir.mkdir(parents=True, exist_ok=True)
    for tool in _TOOLS:
        real = shutil.which(tool)
        assert real, tool
        (bindir / tool).symlink_to(real)
    if python3:
        real = shutil.which("python3")
        assert real, "python3"
        (bindir / "python3").symlink_to(real)
    return bindir


def _run(hook: Path, tmp_path: Path, *, python3: bool,
         stdin: str = "", env_extra: dict[str, str] | None = None,
         argv: list[str] | None = None) -> subprocess.CompletedProcess:
    """Drive the REAL hook with ``HOME=""`` (→ ``/nonexistent``), a ``PATH``
    with no ``tortoise``, and stdin as given.  ``env`` is built from scratch so
    no ambient ``TORTOISE_*`` override or the developer's interpreter leaks in.
    """
    (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": "",
        "PATH": str(_sandbox_bin(tmp_path, python3=python3)),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    env.update(env_extra or {})
    return subprocess.run(
        ["/bin/bash", str(hook), *(argv or [])],
        input=stdin, capture_output=True, text=True, env=env,
        cwd=str(tmp_path), timeout=60)


def _payload(tmp_path: Path, **extra) -> Path:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"type": "user",
                    "message": {"role": "user", "content": "hi"}}) + "\n",
        encoding="utf-8")
    body = {"session_id": "sid-5919", "transcript_path": str(transcript),
            "cwd": str(tmp_path)}
    body.update(extra)
    path = tmp_path / "payload.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# ── the fixed writers: stderr must be BYTE-EMPTY, stdout unchanged ───────

def test_claude_session_start_inert_branches_keep_stderr_empty(tmp_path):
    """The repro from #5919: ``HOME=""`` and no python3/tortoise.

    ``session-start.sh`` reaches BOTH writers (``capture-errors`` and
    ``hook-runs``), so this is the two-line repro the issue reports.

    Mutation: restore the trailing-``2>/dev/null`` form on either writer → the
    shell's own ``...: No such file or directory`` line lands on stderr → RED.
    """
    hook = _install_hook(CLAUDE_START, tmp_path, "session-start.sh")
    proc = _run(hook, tmp_path, python3=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", f"stdout must stay empty: {proc.stdout!r}"
    assert proc.stderr == "", (
        "an inert hook leaked to stderr — the writer's redirect order is back")
    assert "No such file or directory" not in proc.stderr


def test_claude_session_end_breadcrumb_keeps_stderr_empty(tmp_path):
    """``session-end.sh``'s breadcrumb, reached via a real transcript payload.

    python3 is REQUIRED here (the hook parses stdin with it), so the sandbox
    carries an interpreter — the hostile variable under test is ``HOME``, not
    the interpreter.

    Mutation: restore the trailing-``2>/dev/null`` form → RED.
    """
    hook = _install_hook(CLAUDE_END, tmp_path, "session-end.sh")
    payload = _payload(tmp_path)
    proc = _run(hook, tmp_path, python3=True, stdin=payload.read_text())
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", f"stdout must stay empty: {proc.stdout!r}"
    assert proc.stderr == "", (
        "session-end leaked to stderr — the breadcrumb redirect order is back")


def test_claude_volunteer_turn_breadcrumb_keeps_stderr_empty(tmp_path):
    """``volunteer-turn.sh``'s breadcrumb, reached with a prompt on stdin.

    Mutation: restore the trailing-``2>/dev/null`` form → RED.
    """
    hook = _install_hook(CLAUDE_VOLUNTEER, tmp_path, "volunteer-turn.sh")
    proc = _run(hook, tmp_path, python3=False, stdin="hello there")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", f"stdout must stay empty: {proc.stdout!r}"
    assert proc.stderr == "", (
        "volunteer-turn leaked to stderr — the breadcrumb redirect order is back")


def test_codex_worker_breadcrumb_keeps_stderr_empty(tmp_path):
    """``codex-hooks/session-end.sh --worker``'s breadcrumb.

    The REAL parent fires the worker detached with ``>/dev/null 2>&1``, which
    MASKS this leak — so the worker is driven directly here.  That is not a
    contrived path: the worker is the code that owns the writer, and any
    future caller that keeps its stderr (or a test, or a diagnostic run) sees
    the raw error.

    Mutation: restore the trailing-``2>/dev/null`` form → RED.
    """
    hook = _install_hook(CODEX_END, tmp_path, "session-end.sh")
    payload = _payload(tmp_path)
    proc = _run(
        hook, tmp_path, python3=True, argv=["--worker"],
        env_extra={"TORTOISE_CODEX_PAYLOAD": str(payload)})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", f"stdout must stay empty: {proc.stdout!r}"
    assert proc.stderr == "", (
        "codex worker leaked to stderr — the breadcrumb redirect order is back")


def test_cursor_worker_breadcrumb_keeps_stderr_empty(tmp_path):
    """``cursor-hooks/session-end.sh --worker``'s breadcrumb (see the codex
    note about the detached-parent mask).

    Mutation: restore the trailing-``2>/dev/null`` form → RED.
    """
    hook = _install_hook(CURSOR_END, tmp_path, "session-end.sh")
    payload = _payload(tmp_path, workspace_roots=[str(tmp_path)])
    proc = _run(
        hook, tmp_path, python3=True, argv=["--worker"],
        env_extra={"TORTOISE_CURSOR_PAYLOAD": str(payload)})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", f"stdout must stay empty: {proc.stdout!r}"
    assert proc.stderr == "", (
        "cursor worker leaked to stderr — the breadcrumb redirect order is back")


# ── a fix that simply DISABLES the writer must not pass ──────────────────

def test_the_writer_still_writes_when_the_directory_is_writable(tmp_path):
    """Silence must not come from dropping the breadcrumb.

    With a writable HOME the hook MUST still leave both observations — the
    whole point of the inert-install evidence (#4314).  Without this, a fix
    that replaced the write with ``true`` would pass the tests above.

    Mutation: replace the write block with ``:`` (or drop the call site) → no
    crumb file is written → RED.
    """
    hook = _install_hook(CLAUDE_START, tmp_path, "session-start.sh")
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "HOME": str(home),
        "PATH": str(_sandbox_bin(tmp_path, python3=False)),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["/bin/bash", str(hook)], input="", capture_output=True, text=True,
        env=env, cwd=str(tmp_path), timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == "", proc.stderr
    crumb = home / ".tortoise" / "capture-errors" / "claude.json"
    run = home / ".tortoise" / "hook-runs" / "claude.json"
    assert crumb.is_file(), f"breadcrumb missing at {crumb}"
    assert run.is_file(), f"hook-run record missing at {run}"
    assert json.loads(crumb.read_text())["kind"] == "install-inert"
    assert json.loads(run.read_text())["kind"] == "hook-run"


# ── the FROZEN hook: stdout guard only ───────────────────────────────────

def test_frozen_session_turn_stdout_stays_empty(tmp_path):
    """``session-turn.sh`` is under a standing hard rule — its stdout contract
    must not be altered — and #5919 must not touch it.  This guard pins the
    contract WITHOUT asserting its (still-leaking) stderr, so a future fix
    cannot silently give it a non-empty stdout.

    It is intentionally NOT a stderr assertion: that residual is reported on
    #5919, not fixed here.
    """
    hook = _install_hook(CLAUDE_TURN, tmp_path, "session-turn.sh")
    payload = _payload(tmp_path)
    proc = _run(hook, tmp_path, python3=True, stdin=payload.read_text())
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", (
        "session-turn.sh stdout must stay empty — hard rule, do not touch")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
