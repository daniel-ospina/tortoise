"""#3819 — the shipped Cursor capture hook (`tortoise/cursor-hooks/session-end.sh`).

These tests drive the REAL script with a controlled PATH, HOME and a fake
`tortoise` on PATH, and assert the resolved outcome: the argv the capture step
receives, the transcript fallback, the fail-open exits, and — load-bearing —
that the hook DETACHES.

Cursor's `onWillShutdown` JOINS the sessionEnd hook promise, so a hook that
performs the capture POST synchronously DELAYS the app quitting by the POST
duration (~1–10 s). The shipped hook must therefore return immediately and let
a detached worker do the slow POST — exactly the shape the Codex seam needed
for its measured ~1 s budget.

Every docstring names the mutation that turns it RED.
"""
from __future__ import annotations

import json
import os
import pwd
import stat
import subprocess
import time
from pathlib import Path

from tortoise import hook_install
from tortoise.capture_install import install_capture
from tortoise.hook_install import count_canonical_markers, read_hook_version

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "tortoise" / "cursor-hooks" / "session-end.sh"
VERSION_MARKER = "# tortoise-hook-version: 2"

#: The machine's REAL home, resolved from the password database — NOT from
#: ``$HOME``, which tests monkeypatch.  ``~/.cursor`` under this path is the
#: live Cursor store; no test may read or write it.
_REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
_REAL_CURSOR = _REAL_HOME / ".cursor"

#: A REAL Cursor 3.20.21 agent transcript captured on this machine
#: (2026-09-18, `~/.cursor/projects/empty-window/agent-transcripts/
#: 86bd7492-46a1-4bee-a91f-0a4ea4a10ee1/…jsonl`). The ONE redaction is the
#: live API key the composer draft happened to contain (`tt_…` →
#: ``<REDACTED-API-KEY>``); every other byte is as Cursor wrote it. It is a
#: captured artifact, not a hand-planted fixture written to match the parser.
REAL_TRANSCRIPT = (
    REPO_ROOT / "tests" / "fixtures" / "cursor"
    / "agent-transcript-86bd7492.jsonl")


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
    capture log can be silent because the WORKER bailed on a later guard (a
    different defect than the hook spawning one it should not have).
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


def _run_hook(stdin_json: str, *, home: Path, bindir: Path, timeout: float = 15,
              extra_env: dict[str, str] | None = None):
    _install_fake_nohup(bindir)
    env = {
        "HOME": str(home),
        "PATH": f"{bindir}:/usr/bin:/bin",
        # The module fallback must not accidentally find a real checkout.
        "TORTOISE_SRC_DIR": str(home / "no-checkout"),
        "TMPDIR": str(home / "tmp"),
    }
    env.update(extra_env or {})
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


def _wait_for_done(log: Path, timeout: float = 12) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log.exists() and "DONE" in log.read_text(encoding="utf-8"):
            return
        time.sleep(0.1)
    raise AssertionError(f"the detached capture did not complete: {log}")


def test_hook_artifact_carries_the_version_marker():
    """The install contract is one marker, column-0, one per file.

    Mutation: delete ``# tortoise-hook-version: 2`` from the shipped hook — the
    install then has no generation to compare and this REDs."""
    text = HOOK.read_text(encoding="utf-8")
    assert text.startswith(f"#!/usr/bin/env bash\n{VERSION_MARKER}\n"), text[:120]
    assert read_hook_version(HOOK) == 2
    assert count_canonical_markers(HOOK) == 1, (
        "exactly one column-0 marker (an in-body mention is not a declaration)")


def test_no_cursor_test_can_reach_the_real_cursor_store(tmp_path, monkeypatch):
    """The hermeticity guard must observe the PROPERTY, not a fixture-set
    sentinel.  An earlier version asserted ``TORTOISE_TEST_CURSOR_HOME_SCRUBBED``
    — a value the autouse fixture itself sets — which proves the fixture ran
    and says nothing about scope; nothing about it could go red for a real
    hermeticity defect.

    This asserts the property instead: with ``HOME`` under the tmp tree and an
    ambient ``CURSOR_HOME`` deliberately pointed at the LIVE ``~/.cursor``
    (resolved from the password database, not from the monkeypatched ``$HOME``),
    the resolved root and the real install stay under the tmp tree, and the
    live store is never the resolution target.

    Mutation: give the cursor layout a ``root_env`` (or make
    ``cursor_home``/``default_root`` consult ``CURSOR_HOME``) — the ambient
    value below becomes the resolved root, it escapes the tmp tree, and this
    REDs.  (Un-scrubbing the fixture alone cannot RED it, which is exactly why
    the sentinel form was vacuous.)"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CURSOR_HOME", str(_REAL_CURSOR))

    layout = hook_install.get_layout("cursor")
    resolved = hook_install.default_root(layout, Path.home())
    assert resolved == home / ".cursor", (
        f"an ambient CURSOR_HOME moved the cursor root to {resolved}")
    assert resolved != _REAL_CURSOR and _REAL_HOME not in resolved.parents, (
        f"the cursor root escaped the tmp tree: {resolved}")
    assert layout.root_env is None, "Cursor has no config-dir env var"

    assert install_capture("cursor", home=Path.home()).ok
    assert (home / ".cursor" / "hooks.json").is_file()
    assert not (tmp_path / "elsewhere").exists()


def test_hook_detaches_so_cursor_shutdown_cannot_kill_the_capture(tmp_path):
    """Cursor's `onWillShutdown` JOINS the sessionEnd hook, so the hook must
    return immediately and the capture must complete AFTER the parent exits.

    Mutation: drop the trailing ``&``/``disown`` (run the capture
    synchronously) — the hook blocks for the capture's duration, Cursor waits
    on it, and ``elapsed`` fails its bound."""
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "argv.log"
    home.mkdir()
    bindir.mkdir()
    _fake_tortoise(bindir, log, sleep_s=4.0)
    transcript = tmp_path / "agent.jsonl"
    transcript.write_text(
        '{"role":"user","message":{"content":[{"type":"text","text":"hi"}]}}\n',
        encoding="utf-8")

    proc, elapsed = _run_hook(
        json.dumps({"conversation_id": "c-1", "session_id": "c-1",
                    "transcript_path": str(transcript),
                    "reason": "window_close", "hook_event_name": "sessionEnd"}),
        home=home, bindir=bindir)
    assert proc.returncode == 0, proc.stderr
    assert elapsed < 2.5, (
        f"the hook blocked for {elapsed:.1f}s — Cursor JOINS this promise on "
        "shutdown, so a synchronous capture delays quitting by the POST")
    assert not log.exists(), "the capture finished before the hook returned"

    _wait_for_done(log)
    assert "DONE" in log.read_text(encoding="utf-8"), (
        "the detached worker did not survive the hook's exit")


def test_hook_files_the_real_transcript_with_harness_cursor_and_the_session_id(tmp_path):
    """The capture step is `sessions import --harness cursor` with the REAL
    session_id as the idempotency key.

    Mutation: hardcode ``--harness claude`` (or drop ``--session-id``) — the
    server files the session under the wrong bucket / loses convergence and
    this REDs."""
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "argv.log"
    home.mkdir()
    bindir.mkdir()
    _fake_tortoise(bindir, log)
    transcript = tmp_path / "86bd7492-46a1-4bee-a91f-0a4ea4a10ee1.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")

    proc, _ = _run_hook(
        json.dumps({"conversation_id": "86bd7492-46a1-4bee-a91f-0a4ea4a10ee1",
                    "session_id": "86bd7492-46a1-4bee-a91f-0a4ea4a10ee1",
                    "transcript_path": str(transcript),
                    "reason": "user_close", "hook_event_name": "sessionEnd"}),
        home=home, bindir=bindir)
    assert proc.returncode == 0, proc.stderr

    _wait_for_done(log)
    argv = [t for t in log.read_text(encoding="utf-8").split() if t != "DONE"]
    assert argv == ["sessions", "import", "--file", str(transcript),
                    "--harness", "cursor", "--session-id",
                    "86bd7492-46a1-4bee-a91f-0a4ea4a10ee1"], argv


def test_real_transcript_parses_and_imports_through_the_real_cli(tmp_path, monkeypatch):
    """The seam's load-bearing link: the REAL `parse_cursor` over a REAL Cursor
    agent transcript, driven through the REAL `tortoise sessions import
    --harness cursor` path. Every other test here stubs `tortoise` on PATH, so
    a `parse_cursor` that returned 0 turns (or a rejected `--harness cursor` /
    `--session-id`, or a wrong endpoint) would leave them all green.

    Uses the captured transcript in tests/fixtures/cursor/ (see
    ``REAL_TRANSCRIPT``), copied into a tmpdir so the test never mutates the
    live Cursor store.

    Mutation: make ``parse_cursor`` return ``[]`` — the real-turn assertion
    REDs."""
    from types import SimpleNamespace
    from unittest import mock

    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.session_import import parse_transcript

    assert REAL_TRANSCRIPT.is_file(), (
        f"the captured real transcript is missing: {REAL_TRANSCRIPT}")
    transcript = tmp_path / REAL_TRANSCRIPT.name
    transcript.write_bytes(REAL_TRANSCRIPT.read_bytes())

    # 1. The REAL parser over the REAL transcript.
    turns = parse_transcript(str(transcript), "cursor")
    assert turns, "parse_cursor returned no turns for a real Cursor transcript"
    assert turns[0]["role"] == "user"

    # 2. The REAL CLI path, with only the network transport stubbed (a receipt
    # is a 2xx server fact; `_cmd_sessions_import` builds the request for real).
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test")
    # #3615: capture is gated on EXPLICIT consent — a credential is not consent.
    # This test exercises the real import path, so opt in.
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
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
            return b'{"session_id": "s-cursor-real"}'

    def _fake_urlopen(req, timeout=None):
        captured["payload"] = json.loads(req.data.decode())
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return _Resp()

    args = SimpleNamespace(
        file=str(transcript), harness="cursor",
        session_id="86bd7492-46a1-4bee-a91f-0a4ea4a10ee1")
    with mock.patch("urllib.request.urlopen", _fake_urlopen):
        rc = _cmd_sessions_import(args)

    assert rc == 0
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/sessions"), captured["url"]
    assert captured["payload"]["harness"] == "cursor"
    assert captured["payload"]["session_id"] == (
        "86bd7492-46a1-4bee-a91f-0a4ea4a10ee1")
    assert captured["payload"]["conversation"] == turns
    receipts = list((tmp_path / "receipts").glob("*.json"))
    assert len(receipts) == 1, receipts


def test_hook_resolves_the_agent_transcripts_fallback_when_path_is_null(tmp_path):
    """`transcript_path` is NULL when the user disabled transcripts. The hook
    must fall back to Cursor's machine-local store —
    ``~/.cursor/projects/<mangled-workspace>/agent-transcripts/<mangled-id>/
    <mangled-id>.jsonl`` (bundle: ``$5i`` / ``hCf``) — and file THAT.

    Mutation: drop the fallback resolver — the null-path worker exits 0 and
    the capture log stays empty, REDing the argv assertion."""
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "argv.log"
    home.mkdir()
    bindir.mkdir()
    _fake_tortoise(bindir, log)

    game_id = "abc-123-def"
    project = Path("/tmp/My Project/scratch")
    mangled_project = "tmp-My-Project-scratch"
    transcripts = (home / ".cursor" / "projects" / mangled_project
                   / "agent-transcripts" / game_id)
    transcripts.mkdir(parents=True)
    transcript = transcripts / f"{game_id}.jsonl"
    transcript.write_text(
        '{"role":"user","message":{"content":[{"type":"text","text":"hi"}]}}\n',
        encoding="utf-8")

    proc, _ = _run_hook(
        json.dumps({"conversation_id": game_id, "session_id": game_id,
                    "transcript_path": None, "reason": "window_close",
                    "hook_event_name": "sessionEnd",
                    "workspace_roots": [str(project)]}),
        home=home, bindir=bindir,
        extra_env={"CURSOR_PROJECT_DIR": str(project)})
    assert proc.returncode == 0, proc.stderr

    _wait_for_done(log)
    argv = [t for t in log.read_text(encoding="utf-8").split() if t != "DONE"]
    assert argv == ["sessions", "import", "--file", str(transcript),
                    "--harness", "cursor", "--session-id", game_id], argv


def _write_cursor_pair(tmp_path, sid: str):
    """A `.txt` and a `.jsonl` for the SAME conversation, both on disk.

    Cursor writes both for a conversation (bundle: ``joinPath(n, v, `${v}.txt`)``
    next to ``joinPath(n, v, `${v}.jsonl`)``), so "which one does the hook
    read?" is a real, reachable question — not a hypothetical.
    """
    txt = tmp_path / f"{sid}.txt"
    jsonl = tmp_path / f"{sid}.jsonl"
    txt.write_text(
        "USER: hi\nASSISTANT: hello\n", encoding="utf-8")  # not JSONL
    jsonl.write_text(
        '{"role":"user","message":{"content":[{"type":"text",'
        '"text":"hi"}]}}\n', encoding="utf-8")
    return txt, jsonl


def _capture_argv(log: Path) -> list[str]:
    """The capture argv the fake `tortoise` recorded ("DONE" stripped)."""
    return [t for t in log.read_text(encoding="utf-8").split() if t != "DONE"]


def test_hook_uses_cursor_transcript_path_when_the_payload_transcript_is_null(tmp_path):
    """Cursor hands `sessionEnd` the transcript in TWO places and they can
    disagree.  `executeHookForStep` computes the payload's ``transcript_path``
    with ``preferJsonl = (stop || subagentStop)`` — FALSE for sessionEnd — so
    `getTranscriptPath` walks ``['txt','jsonl']`` and can hand the hook a
    `.txt`; `_buildHookEnvironment` computes ``CURSOR_TRANSCRIPT_PATH`` with
    ``preferJsonl=true`` → ``['jsonl','txt']``.  When the payload's own path is
    ``null`` (transcripts off for the composer), the env var is the ONLY way a
    JSONL transcript can be found at all.

    A `.txt` fed to the JSONL parser (`parse_cursor`) yields 0 turns, so a
    capture that resolved one would file nothing while the hook still exits 0
    — the silent no-capture this seam exists to prevent.

    Mutation: drop the ``$CURSOR_TRANSCRIPT_PATH`` candidate from the hook's
    candidate list.  This payload carries ``transcript_path: null`` and no
    store entry exists under the tmp ``HOME``, so NO candidate remains, no
    capture runs, the fake `tortoise` never writes its log, and
    ``_wait_for_done`` REDs.  (A non-null payload ``.txt`` normalises to the
    SAME ``.jsonl`` sibling, so that shape left the argv unchanged and pinned
    nothing — which is why this test uses the env-only shape.)"""
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "argv.log"
    home.mkdir()
    bindir.mkdir()
    _fake_tortoise(bindir, log)

    sid = "86bd7492-46a1-4bee-a91f-0a4ea4a10ee1"
    _txt, jsonl = _write_cursor_pair(tmp_path, sid)

    proc, _ = _run_hook(
        json.dumps({"conversation_id": sid, "session_id": sid,
                    "transcript_path": None, "reason": "window_close",
                    "hook_event_name": "sessionEnd"}),
        home=home, bindir=bindir,
        extra_env={"CURSOR_TRANSCRIPT_PATH": str(jsonl)})
    assert proc.returncode == 0, proc.stderr

    _wait_for_done(log)
    argv = _capture_argv(log)
    assert argv == ["sessions", "import", "--file", str(jsonl),
                    "--harness", "cursor", "--session-id", sid], argv


def test_hook_resolves_a_txt_payload_to_its_jsonl_sibling(tmp_path):
    """A Cursor build that exports NO ``CURSOR_TRANSCRIPT_PATH`` still hands
    the sessionEnd payload a `.txt`-preferred path.  With no env var to
    correct it, the hook must resolve the `.jsonl` SIBLING itself — Cursor
    writes both next to each other — rather than feed the `.txt` to the JSONL
    parser.

    Mutation: drop the extension-normalising sibling resolution — the argv
    names the `.txt` and this REDs."""
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "argv.log"
    home.mkdir()
    bindir.mkdir()
    _fake_tortoise(bindir, log)

    sid = "86bd7492-46a1-4bee-a91f-0a4ea4a10ee1"
    txt, jsonl = _write_cursor_pair(tmp_path, sid)
    assert txt.is_file() and jsonl.is_file()

    proc, _ = _run_hook(
        json.dumps({"conversation_id": sid, "session_id": sid,
                    "transcript_path": str(txt), "reason": "window_close",
                    "hook_event_name": "sessionEnd"}),
        home=home, bindir=bindir)
    assert proc.returncode == 0, proc.stderr

    _wait_for_done(log)
    argv = _capture_argv(log)
    assert argv == ["sessions", "import", "--file", str(jsonl),
                    "--harness", "cursor", "--session-id", sid], argv


def test_hook_never_feeds_a_txt_transcript_to_the_jsonl_parser(tmp_path):
    """A `.txt` with NO `.jsonl` sibling anywhere is a clean no-op, never a
    capture call.  ``parse_cursor`` over a `.txt` returns 0 turns, which the
    hook's mandatory `exit 0` would present as a successful capture while
    nothing was filed — the exact silent no-capture this seam exists to
    prevent.  Refusing to hand a non-JSONL file to the JSONL parser is what
    makes the failure impossible rather than merely unlikely.

    Mutation: let the payload's `.txt` through to `sessions import` (drop the
    extension guard) — the fake `tortoise` is invoked with ``--file <…>.txt``
    and this REDs."""
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "argv.log"
    home.mkdir()
    bindir.mkdir()
    _fake_tortoise(bindir, log)

    sid = "86bd7492-46a1-4bee-a91f-0a4ea4a10ee1"
    txt = tmp_path / f"{sid}.txt"
    txt.write_text("USER: hi\nASSISTANT: hello\n", encoding="utf-8")
    assert not (tmp_path / f"{sid}.jsonl").exists()

    proc, _ = _run_hook(
        json.dumps({"conversation_id": sid, "session_id": sid,
                    "transcript_path": str(txt), "reason": "window_close",
                    "hook_event_name": "sessionEnd"}),
        home=home, bindir=bindir)
    assert proc.returncode == 0, proc.stderr
    time.sleep(1.0)
    assert not log.exists(), (
        "a `.txt` transcript was handed to the JSONL parser — parse_cursor "
        "returns 0 turns and the capture files nothing while reporting "
        "success")


def test_hook_is_fail_open_when_transcript_path_is_null(tmp_path):
    """A null ``transcript_path`` WITH no fallback transcript on disk is a
    clean no-op, never a crash. The parent hands off; the WORKER rejects the
    empty path.

    Mutation: drop the empty-path guard chain (``[ -n "$TRANSCRIPT_PATH" ]``
    together with ``[ -f "$TRANSCRIPT_PATH" ]``) — the worker then invokes the
    capture with an empty ``--file`` and this REDs. Either guard alone already
    rejects a null path (``[ -f "" ]`` is false), so the pair is the unit an
    empty path exercises."""
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "argv.log"
    home.mkdir()
    bindir.mkdir()
    _fake_tortoise(bindir, log)

    proc, _ = _run_hook(
        json.dumps({"conversation_id": "sid", "session_id": "sid",
                    "transcript_path": None, "reason": "window_close",
                    "hook_event_name": "sessionEnd"}),
        home=home, bindir=bindir)
    assert proc.returncode == 0, proc.stderr
    time.sleep(1.0)
    assert not log.exists(), "a null transcript_path still invoked the capture"
    assert (bindir / "nohup.log").exists(), (
        "the parent must hand off — the null-path rejection lives in the worker")


def test_hook_is_fail_open_when_transcript_is_missing_on_disk(tmp_path):
    """A ``transcript_path`` that no longer exists must not invoke the capture.

    Mutation: drop the ``-f`` test — a stale path reaches the CLI and this
    REDs."""
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "argv.log"
    home.mkdir()
    bindir.mkdir()
    _fake_tortoise(bindir, log)

    proc, _ = _run_hook(
        json.dumps({"conversation_id": "sid", "session_id": "sid",
                    "transcript_path": str(tmp_path / "gone.jsonl"),
                    "reason": "window_close", "hook_event_name": "sessionEnd"}),
        home=home, bindir=bindir)
    assert proc.returncode == 0, proc.stderr
    time.sleep(1.0)
    assert not log.exists(), "a missing transcript still invoked the capture"


def test_hook_exits_zero_on_empty_stdin(tmp_path):
    """Cursor never blocks on memory capture — an empty payload is a no-op,
    and the hook must not even SPAWN the detached worker (a null transcript is
    a different case, rejected by the worker itself).

    Mutation: remove the empty-payload guard (``[ -s "$PAYLOAD" ]``) — the hook
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
    """Cursor executes the registered command directly, so the install must
    produce an owner-executable script.

    Mutation: install with mode 0o644 — Cursor cannot run the hook and this
    REDs."""
    home = tmp_path / "home"
    home.mkdir()
    result = install_capture("cursor", home=home)
    assert result.ok, result.error
    installed = home / ".cursor" / "hooks" / "tortoise-session-end.sh"
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
    assert install_capture("cursor", home=home).ok
    installed = home / ".cursor" / "hooks" / "tortoise-session-end.sh"
    os.chmod(installed, 0o644)

    again = install_capture("cursor", home=home)
    assert again.ok, again.error
    assert again.changed is True, again.actions
    assert installed.stat().st_mode & stat.S_IXUSR


def _failing_tortoise(bindir: Path, log: Path, message: str) -> None:
    """A `tortoise` that records its argv and FAILS with ``message`` on stderr.

    Stands in for a real non-2xx capture — the 504 the deployed server returns
    when its wait bound is exceeded (#4580, #4714)."""
    script = bindir / "tortoise"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" >> {log}\n'
        f"echo '{message}' >&2\n"
        f'echo DONE >> {log}\n'
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def test_a_failed_capture_is_recorded_as_evidence_not_swallowed(tmp_path):
    """#4714: a capture that fails must leave EVIDENCE, not silence.

    The seam used to run `sessions import … || true`, so a non-2xx (the
    server's 504 wait bound, #4580) vanished: no spool, no breadcrumb, no
    receipt — the user was told nothing while nothing was captured. That is
    the defect this test pins shut.

    The hook must STILL exit 0 — fail-open is the contract: a capture failure
    must never block the session. But the failure has to be RECORDED, and with
    kind ``capture-failure`` rather than the recorder's ``install-inert``
    default. `session verify` reads the kind, so recording a capture failure
    as install-inert would report a HEALTHY install as INERT — the inversion
    #4314 exists to prevent.

    Mutation: restore ``|| true`` — the breadcrumb assertion REDs (silence).
    Mutation: drop the third argument (the kind) — the kind assertion REDs.
    """
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "argv.log"
    home.mkdir()
    bindir.mkdir()
    # The REAL failure shape: the 504 body contains DOUBLE QUOTES. A
    # quote-free message let an escaping bug through (the raw detail emitted
    # invalid JSON that `session verify` could not parse, measured #4714), so
    # the error text here must keep its quotes. No apostrophe: the helper
    # wraps this in single quotes, so a `'` would truncate the fake's own
    # script.
    _failing_tortoise(
        bindir, log,
        'import failed (HTTP 504): {"detail":"The wait\tbudget was exceeded"}')
    transcript = tmp_path / "agent.jsonl"
    transcript.write_text(
        '{"role":"user","message":{"content":[{"type":"text","text":"hi"}]}}\n',
        encoding="utf-8")

    proc, _ = _run_hook(
        json.dumps({"conversation_id": "c-fail", "session_id": "c-fail",
                    "transcript_path": str(transcript),
                    "reason": "window_close", "hook_event_name": "sessionEnd"}),
        home=home, bindir=bindir)
    assert proc.returncode == 0, (
        "a failed capture must never break Cursor's shutdown (fail-open)")

    _wait_for_done(log)
    # Wait on the ARTIFACT, not the marker. The fake writes DONE *before* it
    # exits, while the hook records the breadcrumb only AFTER reaping it — so
    # _wait_for_done returning does not imply the crumb exists and asserting on
    # it races.
    # Poll until the content PARSES, not merely until the NAME exists: the hook
    # writes with `>` (truncate) then printf, so a reader can catch an empty or
    # partial file and raise JSONDecodeError — the same "assert on the artifact
    # before it is complete" class this poll exists to close.
    crumb = home / ".tortoise" / "capture-errors" / "cursor.json"
    deadline = time.monotonic() + 10
    record = None
    while time.monotonic() < deadline:
        try:
            record = json.loads(crumb.read_text(encoding="utf-8"))
            break
        except (OSError, ValueError):
            time.sleep(0.05)
    assert record is not None, (
        "a failed capture left NO parseable evidence — silence is the defect (#4714)")
    assert record["kind"] == "capture-failure", record
    assert "504" in record["detail"], record
    assert record["harness"] == "cursor", record
    assert "wait" in record["detail"] and "budget" in record["detail"], (
        "the error text must survive JSON-escaping — a raw interpolation of a "
        "quote-bearing message emits INVALID JSON")
    raw = crumb.read_text(encoding="utf-8")
    assert "\\t" in raw, (
        "POSITIVE CONTROL: the tab must actually reach the file as a \\t escape. "
        "Without this the guard below passes vacuously if tab delivery ever breaks")
    assert "\t" not in raw, (
        "a raw tab in the JSON text is a control character and makes the file "
        "unparseable — it must be escaped or stripped. Assert on the RAW file: "
        "json.loads would decode a correct \\t back to a tab and hide this")
