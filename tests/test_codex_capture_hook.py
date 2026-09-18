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

#: A VERBATIM copy of the live Codex rollout captured on 2026-09-18:
#: ~/.codex/sessions/2026/09/18/
#:   rollout-2026-09-18T13-39-54-01a0b5d1-677c-7ff2-89eb-689ff75241ba.jsonl
#: (15 records; 3 conversation turns). Checked in so the real-parser/CLI test
#: runs everywhere; the test copies it into a tmpdir for hermeticity. It is a
#: captured artifact, NOT a hand-planted fixture written to match the parser.
REAL_ROLLOUT = (
    REPO_ROOT / "tests" / "fixtures" / "codex"
    / "rollout-2026-09-18T13-39-54-01a0b5d1-677c-7ff2-89eb-689ff75241ba.jsonl")


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


def _install_fake_nohup(bindir: Path) -> Path:
    """A `nohup` shim that records every worker spawn, then runs the real one.

    The synchronous hook hands off by `nohup "$SELF" --worker &`; recording
    that call is the only way to observe "was a worker spawned at all?" — the
    capture log can be silent because the WORKER bailed on a later guard
    (a different defect than the hook spawning one it should not have).
    """
    log = bindir / "nohup.log"
    script = bindir / "nohup"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "NOHUP %s\\n" "$*" >> {log}\n'
        'exec /usr/bin/nohup "$@"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return log


def _run_hook(stdin_json: str, *, home: Path, bindir: Path, timeout: float = 15):
    _install_fake_nohup(bindir)
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


def test_codex_home_is_scrubbed_so_no_codex_test_can_touch_the_real_home():
    """Every test here runs under the autouse CODEX_HOME scrub: a direct
    `install_capture("codex", home=...)` resolves its root through
    `$CODEX_HOME`, so an ambient value sends the install into the REAL
    `~/.codex` — the suite would mutate the machine it runs on.

    Mutation: delete the autouse `_codex_home_isolation` fixture from
    tests/conftest.py — the sentinel is absent and this REDs."""
    assert os.environ.get("TORTOISE_TEST_CODEX_HOME_SCRUBBED") == "1", (
        "the autouse CODEX_HOME scrub did not run — an ambient CODEX_HOME "
        "would send install_capture('codex', home=...) into the real home")
    assert not os.environ.get("CODEX_HOME"), (
        f"an ambient CODEX_HOME leaked into a codex test: "
        f"{os.environ.get('CODEX_HOME')!r} — installs would land in the real "
        "Codex config")


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


def test_real_rollout_parses_and_imports_through_the_real_cli(tmp_path, monkeypatch):
    """The seam's load-bearing link: the REAL `parse_codex` over a REAL Codex
    rollout, driven through the REAL `tortoise sessions import --harness codex`
    path. Every other test here stubs `tortoise` on PATH, so a `parse_codex`
    that returned 0 turns (or a rejected `--harness codex`/`--session-id`, or
    a wrong endpoint) would leave them all green.

    Uses the verbatim captured rollout in tests/fixtures/codex/ (see
    ``REAL_ROLLOUT``), copied into a tmpdir so the test never mutates the live
    Codex store.

    Mutation: make ``parse_codex`` return ``[]`` — the real-turn assertion
    REDs."""
    from types import SimpleNamespace
    from unittest import mock

    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.session_import import parse_transcript

    assert REAL_ROLLOUT.is_file(), (
        f"the captured real rollout is missing: {REAL_ROLLOUT}")
    rollout = tmp_path / REAL_ROLLOUT.name
    rollout.write_bytes(REAL_ROLLOUT.read_bytes())

    # 1. The REAL parser over the REAL rollout.
    turns = parse_transcript(str(rollout), "codex")
    assert turns, "parse_codex returned no turns for a real Codex rollout"
    assert len(turns) == 3, turns

    # 2. The REAL CLI path, with only the network transport stubbed (a receipt
    # is a 2xx server fact; `_cmd_sessions_import` builds the request for real).
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test")
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "receipts"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    captured: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"session_id": "s-codex-real"}'

    def _fake_urlopen(req, timeout=None):
        captured["payload"] = json.loads(req.data.decode())
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return _Resp()

    args = SimpleNamespace(
        file=str(rollout), harness="codex",
        session_id="01a0b5d1-677c-7ff2-89eb-689ff75241ba")
    with mock.patch("urllib.request.urlopen", _fake_urlopen):
        rc = _cmd_sessions_import(args)

    assert rc == 0
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/sessions"), captured["url"]
    assert captured["payload"]["harness"] == "codex"
    assert captured["payload"]["session_id"] == (
        "01a0b5d1-677c-7ff2-89eb-689ff75241ba")
    assert captured["payload"]["conversation"] == turns
    receipts = list((tmp_path / "receipts").glob("*.json"))
    assert len(receipts) == 1, receipts


def test_pre_post_capture_failure_leaves_a_local_breadcrumb(tmp_path, monkeypatch):
    """A capture that never reaches the server (unreachable host) must not
    read as health: the dashboard reads SERVER state, so the worker's failure
    is otherwise invisible. `sessions import` writes a local breadcrumb on
    failure and clears it on the next 2xx.

    Mutation: drop `_record_capture_error` from the URLError branch — the
    breadcrumb assertion REDs."""
    from types import SimpleNamespace
    from unittest import mock

    from tortoise.__main__ import _capture_error_file, _cmd_sessions_import

    assert REAL_ROLLOUT.is_file()
    rollout = tmp_path / REAL_ROLLOUT.name
    rollout.write_bytes(REAL_ROLLOUT.read_bytes())

    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test")
    monkeypatch.setenv("TORTOISE_API_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "receipts"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    args = SimpleNamespace(file=str(rollout), harness="codex",
                           session_id="sid-breadcrumb")
    assert _cmd_sessions_import(args) == 1
    assert not list((tmp_path / "receipts").glob("*.json")), (
        "a failed POST must not write a receipt")

    breadcrumb = _capture_error_file("codex")
    assert breadcrumb.is_file(), (
        "a pre-POST failure left no observable breadcrumb — the dashboard "
        "reads server state and would show healthy")
    body = json.loads(breadcrumb.read_text())
    assert body["harness"] == "codex"
    assert "127.0.0.1:1" in body["detail"], body

    # A later successful import clears the breadcrumb (the session IS captured).
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"session_id": "s-ok"}'

    with mock.patch("urllib.request.urlopen",
                    lambda req, timeout=None: _Resp()):
        assert _cmd_sessions_import(args) == 0

    assert not breadcrumb.exists(), (
        "a 2xx did not clear the failure breadcrumb")
    assert len(list((tmp_path / "receipts").glob("*.json"))) == 1


def test_hook_is_fail_open_when_transcript_path_is_null(tmp_path):
    """`transcript_path` is NULLABLE in Codex's SessionEnd schema — a null path
    is a clean no-op, never a crash. The parent hands off; the WORKER is what
    rejects the empty path.

    Mutation: drop the empty-path guard CHAIN (`[ -n "$TRANSCRIPT_PATH" ]`
    together with `[ -f "$TRANSCRIPT_PATH" ]`) — the worker then invokes the
    capture with an empty `--file` and this REDs. Either guard alone already
    rejects a null path (`[ -f "" ]` is false), so the pair — not one member —
    is the unit an empty path exercises."""
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
    assert (bindir / "nohup.log").exists(), (
        "the parent must hand off — the null-path rejection lives in the "
        "worker")


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
    """Codex never blocks on memory capture — garbage/empty stdin is a no-op,
    and the hook must not even SPAWN the detached worker (a null transcript is
    a different case, rejected by the worker itself).

    Mutation: remove the empty-stdin guard (`[ -s "$PAYLOAD" ]`) — the hook
    then nohup-spawns the worker on empty stdin and this REDs on the
    ``nohup.log`` assertion."""
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
    assert not (bindir / "nohup.log").exists(), (
        "empty stdin still spawned the detached capture worker")


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
