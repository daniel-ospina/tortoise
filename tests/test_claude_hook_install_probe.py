"""#3755 — session-start.sh must fire the install-probe beacon even when the
memory digest fails.

``tortoise/claude-hooks/session-start.sh`` does two independent things:

  1. the memory digest  — ``tortoise context`` (best-effort context injection)
  2. the install-probe beacon — ``tortoise session probe --harness claude``
     (the server-visible ``install_probe_claude`` signal the dashboard reads)

Before #3755 the digest was written ``... 2>/dev/null || exit 0`` — so a
busy/unreachable embedded store (the common case that ``2>/dev/null`` hides)
ended the script BEFORE line 38's probe ever ran, and install telemetry
silently stayed ``None``. The digest and the probe are independent; only the
digest is best-effort.

These tests EXECUTE the hook (bash, subprocess) with a failing ``tortoise
context`` and assert the probe still fired. The regression lives in the
control flow, so a grep of the file cannot see it — the hook must actually
run.

Both discovery branches are covered, because both carried ``|| exit 0``:
  - installed-bin branch (``tortoise`` on PATH)
  - source-tree fallback (no ``tortoise`` on PATH → ``TORTOISE_SRC_DIR``)

And the documented contract is pinned: the hook ALWAYS exits 0 — session
start is never blocked by either step.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "tortoise" / "claude-hooks"
SESSION_START = HOOKS_DIR / "session-start.sh"

PROBE_CALL = "session probe --harness claude"
# Printed by a SUCCESSFUL digest: stdout is what Claude Code injects, so the
# hook must keep forwarding it (the fix must not silence the digest while
# un-coupling it from the probe).
DIGEST_MARKER = "TORTOISE-DIGEST"

pytestmark = pytest.mark.skipif(
    not SESSION_START.exists(), reason="claude-hooks script not present")


def _write_mock_tortoise(bindir: Path, log: Path, context_rc: int) -> None:
    """A fake ``tortoise`` on PATH: logs every invocation, and exits
    ``context_rc`` for ``context`` (the digest) while succeeding for
    ``session probe`` (the beacon). A successful digest prints
    ``DIGEST_MARKER`` to stdout — the injection contract the hook must keep."""
    bindir.mkdir(parents=True, exist_ok=True)
    mock = bindir / "tortoise"
    mock.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        'if [ "$1" = "context" ]; then\n'
        f"  [ {context_rc} -eq 0 ] && echo '{DIGEST_MARKER}'\n"
        '  echo "embedded store busy" >&2\n'
        f"  exit {context_rc}\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8")
    mock.chmod(mock.stat().st_mode | stat.S_IEXEC)


def _write_fake_source_tree(src_dir: Path, log: Path, context_rc: int) -> None:
    """A fake checkout for the source-tree fallback: ``<src>/tortoise/__main__.py``
    with a ``main(argv)`` that logs argv, fails for ``context`` (the digest),
    and succeeds otherwise (the probe)."""
    pkg = src_dir / "tortoise"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text(
        "import pathlib\n"
        f"_LOG = pathlib.Path({str(log)!r})\n"
        "def main(argv):\n"
        "    with _LOG.open('a') as f:\n"
        "        f.write(' '.join(argv) + chr(10))\n"
        f"    return {context_rc} if argv and argv[0] == 'context' else 0\n",
        encoding="utf-8")


def _run_hook(path: str, extra_env: dict | None = None,
              home: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = path
    env.pop("TORTOISE_SRC_DIR", None)
    env.pop("TORTOISE_BIN", None)
    if home is not None:
        # #3797: the shipped hook now writes a local ``hook-run`` observation,
        # so a test that drives the REAL hook must never let it land in the
        # developer's own ``$HOME`` (#3721's trap).
        env["HOME"] = str(home)
        env["TORTOISE_IMPORT_RECEIPT_DIR"] = str(home / "receipts")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SESSION_START)], input="", capture_output=True, text=True,
        env=env, timeout=60)


def _path_without_tortoise() -> str:
    """PATH with no ``tortoise`` executable — forces the source fallback.

    Keeps every existing entry that does not contain a ``tortoise`` binary
    (so ``python3``/``dirname``/``bash`` resolve), and strips any that do.
    Used with ``TORTOISE_SRC_DIR`` so the fallback branch is exercised
    deterministically even on a machine with an installed ``tortoise``."""
    kept: list[str] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry and (Path(entry) / "tortoise").exists():
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


def _path_with_tortoise(bindir: Path) -> str:
    """PATH where the mock ``tortoise`` wins, with the rest of PATH intact so
    ``bash`` (subprocess program lookup) and shell helpers still resolve."""
    return os.pathsep.join([str(bindir), _path_without_tortoise()])


def test_probe_fires_when_context_fails(tmp_path):
    """#3755 — the beacon fires when the digest fails (installed-bin branch).

    A failing ``tortoise context`` (busy embedded store) must not exit the
    script: ``session probe --harness claude`` must still be invoked and the
    hook must exit 0."""
    log = tmp_path / "calls.log"
    bindir = tmp_path / "bin"
    _write_mock_tortoise(bindir, log, context_rc=1)

    r = _run_hook(_path_with_tortoise(bindir), home=tmp_path)

    assert r.returncode == 0, f"hook must exit 0 (stderr: {r.stderr})"
    calls = log.read_text(encoding="utf-8")
    assert "context" in calls, f"digest should have been attempted: {calls!r}"
    assert PROBE_CALL in calls, (
        "install-probe beacon did not fire after a failing `tortoise context` "
        f"(#3755): {calls!r}")


def test_probe_fires_when_context_fails_source_fallback(tmp_path):
    """#3755 — same regression, source-tree fallback branch (no ``tortoise``
    on PATH): a failing ``main(['context'])`` must still reach
    ``main(['session', 'probe', '--harness', 'claude'])``."""
    log = tmp_path / "calls.log"
    src = tmp_path / "src"
    _write_fake_source_tree(src, log, context_rc=1)

    r = _run_hook(_path_without_tortoise(),
                  extra_env={"TORTOISE_SRC_DIR": str(src)}, home=tmp_path)

    assert r.returncode == 0, f"hook must exit 0 (stderr: {r.stderr})"
    calls = log.read_text(encoding="utf-8")
    assert "context" in calls, f"fallback digest should have been run: {calls!r}"
    assert "session probe --harness claude" in calls, (
        "install-probe beacon did not fire after a failing fallback digest "
        f"(#3755): {calls!r}")


def test_digest_and_probe_both_fire_on_success(tmp_path):
    """The happy path is unchanged: digest stdout is forwarded (Claude injects
    it) AND the beacon fires, exit 0."""
    log = tmp_path / "calls.log"
    bindir = tmp_path / "bin"
    _write_mock_tortoise(bindir, log, context_rc=0)

    r = _run_hook(_path_with_tortoise(bindir), home=tmp_path)

    assert r.returncode == 0, f"hook must exit 0 (stderr: {r.stderr})"
    calls = log.read_text(encoding="utf-8")
    assert "context" in calls and PROBE_CALL in calls, calls
    # The digest is still forwarded to stdout (Claude Code injects it).
    assert DIGEST_MARKER in r.stdout, (
        f"digest stdout must still be forwarded for injection: {r.stdout!r}")
