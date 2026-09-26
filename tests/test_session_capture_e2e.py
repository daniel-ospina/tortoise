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
import time
from pathlib import Path

import pytest

from tortoise.capture_consent import capture_consent_enabled

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


def _run_hook(hook: Path, stdin_data: str, path: Path,
              extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run a hook with a hermetic environment.

    #3615: the capture-consent gate and the legacy-credential migration notice
    read ``TORTOISE_CAPTURE`` / ``TORTOISE_API_KEY`` / ``TORTOISE_API_URL`` and
    probe ``$HOME/.tortoise/credentials.json`` (dropping a one-time notice
    marker under ``$HOME``). None of that may be inherited from the developer's
    shell or touch the real ``$HOME`` — tests set exactly what they need via
    ``extra_env``.
    """
    env = dict(os.environ)
    env["PATH"] = f"{path}:{env.get('PATH', '')}"
    env.pop("TORTOISE_SRC_DIR", None)
    for var in ("TORTOISE_CAPTURE", "TORTOISE_API_KEY", "TORTOISE_API_URL",
                # #3797: the shipped hooks now write a local ``hook-run``
                # observation, so an inherited receipt dir would put it
                # OUTSIDE this test's tmp HOME — the "never touch the real
                # $HOME" contract this helper exists to keep.
                "TORTOISE_IMPORT_RECEIPT_DIR"):
        env.pop(var, None)
    home = path.parent / "home"
    home.mkdir(exist_ok=True)
    env["HOME"] = str(home)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(hook)], input=stdin_data, capture_output=True, text=True,
        env=env, timeout=60)


def _wait_for(log: Path, needle: str, timeout: float = 5.0) -> str:
    """The local reindex sweep is backgrounded (``nohup … &``), so the calls
    log fills asynchronously — poll instead of racing it."""
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        text = log.read_text() if log.exists() else ""
        if needle in text:
            return text
        time.sleep(0.05)
    return text


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
    call contract + the exit-0 guarantee).

    #3615: capture now requires the explicit opt-in, so the test asks for it.
    """
    log = tmp_path / "calls.log"
    bindir = _write_mock_tortoise(tmp_path, log)
    meta = json.dumps({"session_id": "s-hook-abc", "cwd": "/tmp",
                       "transcript_path": str(transcript)})
    r = _run_hook(SESSION_END, meta, bindir,
                  extra_env={"TORTOISE_CAPTURE": "1"})
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
    r = _run_hook(SESSION_END, meta, bindir,
                  extra_env={"TORTOISE_CAPTURE": "1"})
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


# ── #3615: capture requires EXPLICIT consent, never a credential ──────────


def test_session_end_no_capture_without_explicit_opt_in(tmp_path, transcript):
    """#3615 (NON-VACUITY): a resolvable CREDENTIAL must not file a session.

    Exports the hosted key + URL exactly as the MCP `Authorization: Bearer`
    recipe tells the user to, and asserts the hook still does NOT capture.
    This is the regression guard: against the pre-fix hook (capture inferred
    from credential presence) this assertion fails — the old script always
    invoked `session capture` here.
    """
    log = tmp_path / "calls.log"
    bindir = _write_mock_tortoise(tmp_path, log)
    meta = json.dumps({"session_id": "s-no-consent", "cwd": "/tmp",
                       "transcript_path": str(transcript)})
    r = _run_hook(SESSION_END, meta, bindir, extra_env={
        "TORTOISE_API_KEY": "tt_legacy_credential",
        "TORTOISE_API_URL": "https://api.premiselabs.co",
    })
    assert r.returncode == 0, f"hook must exit 0 (stderr: {r.stderr})"
    calls = log.read_text() if log.exists() else ""
    assert "session capture" not in calls, calls
    # The LOCAL reindex sweep is not capture — it never leaves the machine and
    # must keep running (the consent gate may not disable local memory).
    assert "index directory" in _wait_for(log, "index directory"), calls


def test_session_end_local_sweep_runs_with_consent_too(tmp_path, transcript):
    """With the opt-in present the sweep still runs (ordering unchanged)."""
    log = tmp_path / "calls.log"
    bindir = _write_mock_tortoise(tmp_path, log)
    meta = json.dumps({"session_id": "s-consent", "transcript_path": str(transcript)})
    r = _run_hook(SESSION_END, meta, bindir, extra_env={"TORTOISE_CAPTURE": "1"})
    assert r.returncode == 0
    assert "index directory" in _wait_for(log, "index directory")
    assert "session capture" in log.read_text()


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("Yes", True), ("on", True),
    (" 1 ", True), ("\ttrue\n", True),
    ("0", False), ("false", False), ("off", False), ("no", False),
    ("", False), ("2", False), ("y", False), ("enable", False),
    # Security review P2: C1 controls. Python's str.strip() treats these as
    # whitespace and AUTHORIZED; bash's [[:space:]] does not. Both refuse now.
    ("1\x1c", False), ("\x1c1", False), ("\x1d1", False), ("1\x1f", False),
    # Security review cycle-2 P2: `[[:space:]]` is locale/platform-dependent and
    # matches Unicode spaces in a UTF-8 locale (U+00A0/U+2028/U+2029/U+3000)
    # while Python trims only the ASCII set — a naive bash twin AUTHORIZES on
    # these and the CLI refuses, reopening the parity-drift class. Both must
    # refuse; the hook now trims the explicit ASCII set.
    ("\u00a01", False), ("1\u00a0", False), ("\u20281", False),
    ("\u20291", False), ("\u30001", False), ("\u00851", False),
])
def test_capture_opt_in_parity_bash_and_python(tmp_path, transcript, value, expected):
    """The bash gate in session-end.sh and capture_consent_enabled() implement
    the SAME security predicate across the language boundary — pin them
    together here so they cannot drift (e.g. the whitespace-trim divergence a
    naive bash `case` would have)."""
    assert capture_consent_enabled({"TORTOISE_CAPTURE": value}) is expected
    log = tmp_path / "calls.log"
    bindir = _write_mock_tortoise(tmp_path, log)
    meta = json.dumps({"session_id": "s-parity", "transcript_path": str(transcript)})
    r = _run_hook(SESSION_END, meta, bindir, extra_env={"TORTOISE_CAPTURE": value})
    assert r.returncode == 0, f"hook must exit 0 (stderr: {r.stderr})"
    fired = log.exists() and "session capture" in log.read_text()
    assert fired is expected, (
        f"hook disagreed with the Python predicate for TORTOISE_CAPTURE={value!r}"
        f" (hook fired={fired}, expected={expected})")


def test_session_end_migration_notice_is_one_time_on_disk_but_always_visible(
        tmp_path, transcript):
    """#3615 migration: a host that WOULD have captured under the old contract
    gets a quiet non-blocking notice telling it how to re-enable — never a
    silent vanish.

    Two channels, two lifetimes. The FILE is written once: its content is the
    instruction and it is the durable channel (a stale copied hook swallows
    stderr). The VISIBLE line is emitted on every session close, deliberately
    NOT gated on the marker's absence — a stale copied hook's CLI refusal writes
    that same marker, so a marker-gated notice would never speak on precisely
    the hosts it exists for (solution-verify cycle 2 P1).
    """
    log = tmp_path / "calls.log"
    bindir = _write_mock_tortoise(tmp_path, log)
    meta = json.dumps({"session_id": "s-notice", "transcript_path": str(transcript)})
    legacy = {"TORTOISE_API_KEY": "tt_legacy_credential"}

    first = _run_hook(SESSION_END, meta, bindir, extra_env=legacy)
    assert first.returncode == 0
    assert "session capture is OFF" in first.stderr, first.stderr
    assert "TORTOISE_CAPTURE=1" in first.stderr, first.stderr
    marker = bindir.parent / "home" / ".tortoise" / "capture-consent-notice"
    assert marker.exists(), "the migration notice must be discoverable on disk"
    body = marker.read_text(encoding="utf-8")
    assert "TORTOISE_CAPTURE=1" in body

    second = _run_hook(SESSION_END, meta, bindir, extra_env=legacy)
    assert second.returncode == 0
    assert "session capture is OFF" in second.stderr, (
        "the visible notice must not be suppressible by the marker")
    assert marker.read_text(encoding="utf-8") == body, "the file stays one-time"


def test_session_end_notice_survives_a_marker_written_by_a_stale_hook(
        tmp_path, transcript):
    """Regression pin for the solution-verify cycle-2 P1 (reproduced live).

    A STALE copied hook runs `tortoise session capture … 2>/dev/null`, so its
    CLI refusal writes ``~/.tortoise/capture-consent-notice`` while the message
    itself is discarded. The updated hook must STILL deliver the visible line
    (file-only delivery would mean the targeted population sees capture simply
    stop, with no in-band signal at all).
    """
    log = tmp_path / "calls.log"
    bindir = _write_mock_tortoise(tmp_path, log)
    marker = bindir.parent / "home" / ".tortoise" / "capture-consent-notice"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("written by a stale hook's CLI refusal\n", encoding="utf-8")
    meta = json.dumps({"session_id": "s-stale", "transcript_path": str(transcript)})

    r = _run_hook(SESSION_END, meta, bindir,
                  extra_env={"TORTOISE_API_KEY": "tt_legacy_credential"})
    assert r.returncode == 0
    assert "session capture is OFF" in r.stderr, r.stderr
    assert marker.read_text(encoding="utf-8") == \
        "written by a stale hook's CLI refusal\n"


def test_session_end_notice_ignores_a_blank_legacy_credential(tmp_path, transcript):
    """The notice predicate is a *second* bash predicate; a whitespace-only key
    is not a resolvable credential (the resolver strips it and treats it as
    unset), so it must not manufacture a false-positive notice."""
    log = tmp_path / "calls.log"
    bindir = _write_mock_tortoise(tmp_path, log)
    meta = json.dumps({"session_id": "s-blank", "transcript_path": str(transcript)})
    r = _run_hook(SESSION_END, meta, bindir, extra_env={"TORTOISE_API_KEY": "   "})
    assert r.returncode == 0
    assert "session capture is OFF" not in r.stderr, r.stderr


def test_session_end_no_notice_without_a_legacy_credential(tmp_path, transcript):
    """A capture-off host (no credential at all) is never nagged — the notice
    targets exactly the population whose behavior changed."""
    log = tmp_path / "calls.log"
    bindir = _write_mock_tortoise(tmp_path, log)
    meta = json.dumps({"session_id": "s-quiet", "transcript_path": str(transcript)})
    r = _run_hook(SESSION_END, meta, bindir)
    assert r.returncode == 0
    assert "session capture is OFF" not in r.stderr, r.stderr
