"""#3515 — the store-sync BACKSTOP, proven at PROCESS level.

The cursor's claims are about processes: a process that exits, and a later
process that resumes. An in-process call cannot produce them, so every row here
drives the real artifact through ``tortoise store-sync`` (a ``__main__.py``
subcommand, §C(a) of the issue) with ``subprocess.run`` and asserts side
effects — cursor bytes on disk and POST bodies against a REAL local HTTP server
(not a stubbed ``fetch``: the transport half of the two-part verification has to
be transport).

The node-level unit rows (cursor math, branch window, backoff schedule) live in
``tortoise/pi-hooks/store-sync.test.mjs`` and are run by
``test_node_suite_passes`` here, so the suite tests the ARTIFACT and never a
Python copy of it (§C(b),(e)).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PI_HOOKS = REPO_ROOT / "tortoise" / "pi-hooks"
WORKER = PI_HOOKS / "store-sync.ts"
CURSOR_STORE = PI_HOOKS / "cursor-store.ts"
NODE_SUITE = PI_HOOKS / "store-sync.test.mjs"
CAPTURE_TS = PI_HOOKS / "tortoise-capture.ts"

RECEIPT_SENTINEL = "RECEIPT_JSON:"


# ── the local capture server (a REAL transport for the verification) ───────


class CaptureServer:
    """A minimal ``/v1/sessions`` + ``/v1/sessions/install-probe`` collector.

    It records every body it RECEIVES, which is what makes the verification
    two-part rather than self-reported: the client's own "I posted" is not
    evidence; the server's stored copy is.
    """

    def __init__(self) -> None:
        self.sessions: list[dict] = []
        self.probes: list[dict] = []
        self.status = 200
        self.calls = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # the BaseHTTPRequestHandler protocol name
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = {"__unparsable__": True}
                outer.calls += 1
                if self.path.endswith("/install-probe"):
                    outer.probes.append(body)
                else:
                    outer.sessions.append(body)
                payload = json.dumps({"ok": outer.status < 300}).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args) -> None:  # silence
                return

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture()
def server():
    srv = CaptureServer()
    try:
        yield srv
    finally:
        srv.close()


def _env(server: CaptureServer) -> dict[str, str]:
    env = dict(os.environ)
    env["TORTOISE_API_KEY"] = "test-key-3515"
    env["TORTOISE_API_URL"] = server.url
    # Never let a real hosted config leak into a test run.
    env.pop("TORTOISE_CAPTURE", None)
    return env


def _run_sync(tmp: Path, *args: str, server: CaptureServer, extra_env=None):
    cmd = [
        sys.executable, "-m", "tortoise", "store-sync",
        "--no-spool",
        "--session-dir", str(tmp / "sessions"),
        "--cursor-dir", str(tmp / "cursor"),
        "--json",
        *args,
    ]
    proc = subprocess.run(
        cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180,
        env={**_env(server), **(extra_env or {})},
    )
    receipt = None
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith(RECEIPT_SENTINEL):
            receipt = json.loads(line[len(RECEIPT_SENTINEL):])
            break
    if receipt is None:
        # `tortoise store-sync` composes the spool half with the worker half and
        # prints ONE document (the worker's `RECEIPT_JSON:` line is consumed by
        # `tortoise/store_sync.py`, and unwrapped into this shape).
        try:
            receipt = json.loads(proc.stdout or "")
        except json.JSONDecodeError:
            receipt = None
    assert receipt is not None, f"no receipt\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}"
    return proc, receipt


# ── fixtures ──────────────────────────────────────────────────────────────


def _header(session_id: str) -> str:
    return json.dumps({
        "type": "session", "version": 3, "id": session_id,
        "timestamp": "2026-10-23T10:00:00.000Z", "cwd": "/tmp/x",
    })


def _message(entry_id: str, parent: str | None, role: str, text: str, ts: str = "") -> str:
    return json.dumps({
        "type": "message", "id": entry_id, "parentId": parent,
        "timestamp": ts or "2026-10-23T10:00:01.000Z",
        "message": {"role": role, "content": [{"type": "text", "text": text}]},
    })


def _session_file(tmp: Path, name: str, lines: list[str]) -> Path:
    d = tmp / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _cursor(tmp: Path) -> dict:
    path = tmp / "cursor" / "cursor.json"
    if not path.exists():
        return {"files": {}}
    return json.loads(path.read_text(encoding="utf-8"))


# ── §C(e): the artifact guard ─────────────────────────────────────────────


def test_the_worker_artifact_exists_and_is_what_the_suite_drives():
    """Fails if `store-sync.ts` is deleted, so the suite cannot drift into
    testing a Python copy of it (§C(e))."""
    assert WORKER.exists(), f"the store-sync worker is missing: {WORKER}"
    assert CURSOR_STORE.exists(), f"the cursor store is missing: {CURSOR_STORE}"
    source = WORKER.read_text(encoding="utf-8")
    # The pinned rules must live in the ARTIFACT, not only in this docstring.
    for pin in (
        "advance-on-2xx",  # case-insensitive marker of the cursor rule
        "cursor_past_eof",
        "file_replaced",
        "activeBranch",
        "LANE = \"store_sync\"",
    ):
        assert pin.lower() in source.lower(), f"pinned rule missing from the worker: {pin}"


def test_the_worker_does_not_carry_a_second_payload_builder():
    """ONE capture path: the worker must import the hook's payload builder
    rather than re-deriving a payload shape (the 'fourth copy' failure)."""
    source = WORKER.read_text(encoding="utf-8")
    assert "./tortoise-capture.ts" in source
    for imported in ("buildCapturePayload", "extractTurns", "postCapture"):
        assert imported in source, f"the worker must reuse {imported} from the hook"


def test_the_capture_lane_vocabulary_is_mirrored_in_the_ts_leg():
    """The TS mirror must equal the server's closed set, or a lane the client
    emits draws a 422 the client cannot see it caused."""
    from tortoise.hosted_api import (
        _SESSION_CAPTURE_LANE_VALUES,
        _SESSION_CAPTURED_AT_SOURCE_VALUES,
    )

    source = CAPTURE_TS.read_text(encoding="utf-8")
    for lane in _SESSION_CAPTURE_LANE_VALUES:
        assert f'"{lane}"' in source, f"lane {lane} missing from the TS mirror"
    for source_name in _SESSION_CAPTURED_AT_SOURCE_VALUES:
        assert f'"{source_name}"' in source, (
            f"client_captured_at_source {source_name} missing from the TS mirror"
        )
    # #3515 piece 12: the store-sync lane's own clock is a named source.
    assert "entry_timestamp" in _SESSION_CAPTURED_AT_SOURCE_VALUES


# ── the node-level unit rows (§C(b)) ──────────────────────────────────────


def test_node_suite_passes():
    """The node rows, run the way CI runs them: no ambient credential, no
    `~/.pi/agent/tortoise-config.json`, and a Node that can run TypeScript
    without a flag.

    #3721 is the reason for every word of that. `runStoreSync` resolves its
    credential from `TORTOISE_API_KEY` / the Pi config file, so with the
    INHERITED environment the suite is a coin flip on the machine: on a
    developer box it passed while asserting the developer's own config, and on
    a clean runner the empty key short-circuited the POST before the injected
    `fetch` was ever called (`0 !== 1`). The remedy is the sibling's, applied
    here in both places — a scrubbed env for the runner (so a local run
    reproduces CI) and `HERMETIC` at every call site inside the suite.
    """
    from test_pi_capture_hooks import _node_supports_ts, _scrubbed_env

    node = shutil.which("node")
    if not node:
        pytest.skip("node is absent")
    if not _node_supports_ts(node):
        pytest.skip("node < 22.18 does not enable TypeScript type stripping by "
                    "default — the source pins above still ran")
    with tempfile.TemporaryDirectory() as fake_home:
        proc = subprocess.run(
            [node, "--test", str(NODE_SUITE)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
            env=_scrubbed_env(fake_home),
        )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    assert "fail 0" in proc.stdout, proc.stdout[-2000:]


# ── the two-part verification, part (i): TRANSPORT ────────────────────────


def test_part_i_install_probe_reaches_the_server_and_is_read_back(server, tmp_path):
    """Part (i) of the two-part verification: the install probe → 2xx + a
    server-side key read-back.

    Driven through the SHIPPED extension (``postInstallProbe``) so the artifact
    that runs on a user's machine is the thing proven, and against a REAL HTTP
    server so the leg includes network, headers, path and payload shape. A
    probe is synthetic: it proves transport and CANNOT prove the hook fires on
    real sessions — part (ii) is the other half, and neither substitutes for
    the other.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is absent")
    probe_runner = tmp_path / "probe.mjs"
    probe_runner.write_text(
        "import { postInstallProbe } from "
        f"{json.dumps(CAPTURE_TS.as_uri())};\n"
        "const cfg = { apiKey: 'test-key-3515', apiUrl: process.env.PROBE_URL };\n"
        "const res = await postInstallProbe(cfg);\n"
        "console.log('PROBE_RESULT:' + JSON.stringify(res));\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [node, str(probe_runner)],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "PROBE_URL": server.url},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = None
    for line in proc.stdout.splitlines():
        if line.startswith("PROBE_RESULT:"):
            result = json.loads(line[len("PROBE_RESULT:"):])
    assert result is not None, proc.stdout
    assert result["ok"] is True and result["status"] == 200, result

    # The read-back: the SERVER holds a probe for this harness (the client's
    # own success is not the evidence).
    assert server.probes, "the server received no install probe"
    assert server.probes[-1]["harness"] == "pi"


# ── the two-part verification, part (ii): REAL-SESSION CAPTURE ────────────


def test_part_ii_a_real_pi_session_file_is_captured_and_attributed(server, tmp_path):
    """Part (ii): real Pi OUTPUT — the product's own file, not a synthetic
    writer — lands in the store, with its ``session_id`` equal to the header
    ``id`` and its lane the store-sync backstop's.

    The file is COPIED out of ``~/.pi/agent/sessions`` into ``tmp_path`` and
    driven from there. Two reasons, both from review:

    * the row used to read the developer's REAL store and POST real
      conversation content to a local collector — the product's transcript
      leaving a machine to prove a mechanism the pinned fixtures above already
      prove;
    * it picked ``max(mtime)``, i.e. on a working box THE LIVE SESSION, and then
      hard-asserted that its header id equals the filename UUID — a statement
      about Pi's FILENAMING convention, measured on one machine, in a test for a
      module with no opinion about filenames. A resumed/forked session whose
      header id differs from the filename reds it for a reason unrelated to
      store-sync.

    Where no real session exists on the machine the row SKIPS with the reason
    (it is an ops check there, never a fake pass). The copy keeps the property
    that matters: the bytes are what Pi wrote.
    """
    real_root = Path.home() / ".pi" / "agent" / "sessions"
    real_files = sorted(real_root.rglob("*.jsonl")) if real_root.is_dir() else []
    candidates: list[tuple[dict, Path]] = []
    for candidate in real_files:
        try:
            with candidate.open(encoding="utf-8") as fh:
                first = fh.readline()
            parsed = json.loads(first)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict) and parsed.get("type") == "session" and parsed.get("id"):
            candidates.append((parsed, candidate))
            if len(candidates) >= 40:
                break
    if not candidates:
        pytest.skip(f"no usable real Pi session file under {real_root}")

    def _compacted(path: Path) -> bool:
        try:
            return '"type":"compaction"' in path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False

    # NON-COMPACTED FIRST. A compacted file is legitimately REFUSED on its first
    # ship (the shrink guard: its window starts at `firstKeptEntryId`, which is a
    # DIFFERENT turn-index space from the hook's, so shipping it would overwrite
    # the hook's turn 0 and delete the rest). Measured on one box: 55 of 292 real
    # session files carry a compaction entry, while `sorted()` happens to put a
    # compacted cluster first — so an unfiltered pick spends the whole row on
    # refusals and reports a skip. Preferring a non-compacted candidate makes the
    # real-bytes half actually run; the refusal branch below still covers the
    # case where none is available.
    candidates.sort(key=lambda item: _compacted(item[1]))

    (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
    header: dict | None = None
    body: dict | None = None
    refused = 0
    log: dict = {}
    for index, (candidate_header, source) in enumerate(candidates[:8]):
        target = tmp_path / "sessions" / f"copied-{index}.jsonl"
        target.write_bytes(source.read_bytes())
        _proc, receipt = _run_sync(tmp_path, "--file", str(target), server=server)
        log = receipt["log"]
        assert log["errors"] == [], log["errors"]
        if server.sessions:
            header, body = candidate_header, server.sessions[-1]
            break
        if log.get("shrunk"):
            # A COMPACTED real session is REFUSED by the shrink guard on purpose:
            # shipping its short post-/compact window would delete the turns the
            # hook already filed. That is the guard WORKING, not a defect — so the
            # row advances to the next real file instead of turning a refusal
            # into a silent "pass". Before this branch existed, one compacted
            # candidate disabled the whole real-bytes half of the verification.
            refused += 1
            continue
        # Nothing shippable and no refusal (the live session was mid-answer): the
        # row's claim is about the HEADER and the lane, which a file carrying no
        # turn cannot make. Keep looking, then skip honestly.
    if body is None:
        pytest.skip(
            f"no shippable real Pi session among {len(candidates)} candidate(s) "
            f"({refused} refused by the shrink guard as compacted; last log={log})"
        )
    assert header is not None
    assert body["session_id"] == header["id"], (
        "piece 6: session_id must come from the on-disk header id, never a "
        "generated one"
    )
    assert body["capture_lane"] == "store_sync", "piece 8: the lane discriminator"
    assert body["harness"] == "pi"
    assert body["conversation"], "a real session must carry turns"
    assert isinstance(body.get("client_captured_at"), (int, float)), "piece 12"


# ── cursor behaviours, at PROCESS level ──────────────────────────────────


def test_cursor_survives_a_restart_and_does_not_re_emit(server, tmp_path):
    """A restart resumes from the cursor: the second process ships NOTHING."""
    path = _session_file(tmp_path, "a.jsonl", [
        _header("sid-restart"),
        _message("u1", None, "user", "hello"),
        _message("a1", "u1", "assistant", "hi"),
    ])
    _, first = _run_sync(tmp_path, server=server)
    assert first["log"]["shipped"] == 1
    assert len(server.sessions) == 1
    cursor_after_first = _cursor(tmp_path)["files"][str(path)]["offset"]
    assert cursor_after_first == path.stat().st_size

    server.calls = 0
    _, second = _run_sync(tmp_path, server=server)
    assert second["log"]["shipped"] == 0, "a restart must not re-ship"
    assert second["log"]["skipped"] == 1
    assert server.calls == 0, "no POST at all on the resumed run"
    assert _cursor(tmp_path)["files"][str(path)]["offset"] == cursor_after_first


def test_a_half_written_tail_is_neither_consumed_nor_lost(server, tmp_path):
    """The trailing line is incomplete: the cursor must stay BEHIND it, and the
    completed line must ship on the next process."""
    path = _session_file(tmp_path, "a.jsonl", [
        _header("sid-tail"),
        _message("u1", None, "user", "first"),
    ])
    partial = '{"type":"message","id":"a1","parentId":"u1","message":{"role":"assistant","cont'
    with path.open("a", encoding="utf-8") as fh:
        fh.write(partial)

    _, first = _run_sync(tmp_path, server=server)
    assert first["log"]["shipped"] == 1
    assert [t["content"] for t in server.sessions[-1]["conversation"]] == ["first"]
    offset = _cursor(tmp_path)["files"][str(path)]["offset"]
    assert offset < path.stat().st_size, (
        "the cursor advanced past a half-written line — that is the loss the "
        "partial-tail rule exists to forbid"
    )

    with path.open("a", encoding="utf-8") as fh:
        fh.write('ent":[{"type":"text","text":"second"}]}}\n')
    _, second = _run_sync(tmp_path, server=server)
    assert second["log"]["shipped"] == 1
    assert [t["content"] for t in server.sessions[-1]["conversation"]] == ["first", "second"]


def test_a_truncated_file_is_re_anchored_not_silently_skipped(server, tmp_path):
    """``cursor > file_size`` (Pi's ``_rewriteFile`` truncates then rewrites) —
    the normal degenerate case. It must re-read from the header."""
    path = _session_file(tmp_path, "a.jsonl", [
        _header("sid-trunc"),
        _message("u1", None, "user", "one"),
        _message("a1", "u1", "assistant", "two"),
    ])
    _run_sync(tmp_path, server=server)
    before = _cursor(tmp_path)["files"][str(path)]["offset"]
    assert before > 0

    path.write_text(
        "\n".join([_header("sid-trunc"), _message("u1", None, "user", "one")]) + "\n",
        encoding="utf-8",
    )
    assert path.stat().st_size < before

    server.calls = 0
    _, second = _run_sync(tmp_path, server=server)
    assert server.calls >= 1, "a silent skip would have made zero POSTs"
    assert [t["content"] for t in server.sessions[-1]["conversation"]] == ["one"]
    assert second["log"]["files"][0]["action"] == "ship"
    assert _cursor(tmp_path)["files"][str(path)]["offset"] == path.stat().st_size


def test_a_failing_transport_does_not_advance_the_cursor(server, tmp_path):
    """At-least-once: no 2xx, no advance — and a later run delivers it."""
    path = _session_file(tmp_path, "a.jsonl", [
        _header("sid-fail"),
        _message("u1", None, "user", "must survive"),
    ])
    server.status = 503
    _, first = _run_sync(tmp_path, server=server)
    assert first["log"]["shipped"] == 0
    assert first["log"]["files"][0]["action"] == "failed"
    assert _cursor(tmp_path)["files"][str(path)]["offset"] == 0

    server.status = 200
    _, second = _run_sync(tmp_path, server=server)
    assert second["log"]["shipped"] == 1
    assert [t["content"] for t in server.sessions[-1]["conversation"]] == ["must survive"]


def test_a_permanent_4xx_is_dead_lettered_and_counts_the_drop(server, tmp_path):
    """A permanently refused file must not head-of-line-block the next one, and
    the loss must be COUNTED (never a silent drop)."""
    _session_file(tmp_path, "a-bad.jsonl", [_header("sid-bad"), _message("u1", None, "user", "x")])
    _session_file(tmp_path, "b-good.jsonl", [_header("sid-good"), _message("v1", None, "user", "y")])
    server.status = 422
    _, receipt = _run_sync(tmp_path, "--max-files", "1", server=server)
    assert receipt["log"]["deadLettered"] == 1
    assert receipt["log"]["dropped"] >= 1
    letters = receipt["log"]["deadLetters"]
    assert letters and letters[0]["sessionId"] == "sid-bad"

    server.status = 200
    _, second = _run_sync(tmp_path, server=server)
    assert second["log"]["shipped"] == 1, "the second file still ships"
    assert server.sessions[-1]["session_id"] == "sid-good"


def test_shipped_turns_match_the_logs_own_content_and_branch(server, tmp_path):
    """The window is the ACTIVE BRANCH: an abandoned branch is not shipped."""
    _session_file(tmp_path, "a.jsonl", [
        _header("sid-branch"),
        _message("u1", None, "user", "question one"),
        _message("a1", "u1", "assistant", "answer one"),
        _message("u2", "a1", "user", "abandoned question"),
        _message("a2", "u2", "assistant", "abandoned answer"),
        _message("u3", "a1", "user", "live question"),
        _message("a3", "u3", "assistant", "live answer"),
    ])
    _run_sync(tmp_path, server=server)
    contents = [t["content"] for t in server.sessions[-1]["conversation"]]
    assert contents == ["question one", "answer one", "live question", "live answer"]
    assert "abandoned question" not in contents


def test_the_sweep_is_bounded_across_runs(server, tmp_path):
    for i in range(4):
        _session_file(tmp_path, f"s{i}.jsonl", [
            _header(f"sid-{i}"), _message(f"u{i}", None, "user", f"t{i}"),
        ])
    _, first = _run_sync(tmp_path, "--max-files", "2", server=server)
    assert first["log"]["shipped"] == 2
    _, second = _run_sync(tmp_path, "--max-files", "2", server=server)
    assert second["log"]["shipped"] == 2, "the remainder drains on the NEXT run"
    _, third = _run_sync(tmp_path, server=server)
    assert third["log"]["shipped"] == 0


def test_the_cli_never_invents_a_session_id(server, tmp_path):
    _session_file(tmp_path, "a.jsonl", [_message("u1", None, "user", "no header")])
    proc, receipt = _run_sync(tmp_path, server=server)
    assert receipt["log"]["files"][0]["reason"] == "no_header_id"
    assert server.calls == 0, "an invented id would have produced a POST"
    assert proc.returncode == 1, "a structurally unusable file is reported, not swallowed"


# ── the CREDENTIAL is one chain (review: split-brain key/URL) ─────────────


def test_the_cli_resolved_credential_reaches_the_log_half(server, tmp_path):
    """A key written by `tortoise init --api-key` lands in
    ``~/.tortoise/credentials.json`` — a store the WORKER never reads.

    `run_sweep` resolved that key for the spool half and forwarded only the
    URL, so the log half — the half this PR exists for — resolved its OWN chain
    (env → ``~/.pi/agent/tortoise-config.json``), found nothing, and reported
    "no TORTOISE_API_KEY configured" forever. That is the #2369 D1.1 split-brain
    in both directions: nothing shipped, and where a key did exist it was paired
    with a URL from a different chain.

    MUTATION THAT REDS THIS: drop `api_key=api_key` from the `run_worker` call
    in `run_sweep` (or the `env` hand-over in `run_worker`) → the receipt's log
    half carries `no TORTOISE_API_KEY configured` and ships 0.
    """
    home = tmp_path / "home"
    (home / ".tortoise").mkdir(parents=True)
    (home / ".tortoise" / "credentials.json").write_text(
        json.dumps({"api_key": "tt_from_file_store", "api_url": server.url}),
        encoding="utf-8",
    )
    _session_file(tmp_path, "a.jsonl", [
        _header("sid-cred"), _message("u1", None, "user", "from the file store"),
    ])
    # NO env key: the credential exists ONLY in the user-global store.
    _, receipt = _run_sync(tmp_path, server=server, extra_env={
        "HOME": str(home),
        "TORTOISE_API_KEY": "",
        "TORTOISE_API_URL": "",
    })
    log = receipt["log"]
    assert log["errors"] == [], log["errors"]
    assert log["shipped"] == 1, f"the log half never got the key: {log}"
    assert server.sessions[-1]["conversation"][0]["content"] == "from the file store"


def test_the_worker_gets_the_key_in_its_ENVIRONMENT_not_its_argv(monkeypatch):
    """A credential on argv is readable by every process on the box (`ps`)."""
    from tortoise import store_sync

    seen: dict = {}

    class _Proc:
        returncode = 0
        stdout = 'RECEIPT_JSON:{"shipped":0,"skipped":0,"errors":[],"files":[]}'
        stderr = ""

    def _run(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs.get("env")
        return _Proc()

    monkeypatch.setattr(store_sync.subprocess, "run", _run)
    receipt = store_sync.run_worker(
        session_dir="/tmp/s", cursor_dir="/tmp/c", api_key="tt_secret",
        api_url="https://api.example", node="/usr/bin/node",
    )
    assert receipt["ok"] is True
    assert "tt_secret" not in " ".join(seen["argv"]), "the key must not be in argv"
    assert seen["env"]["TORTOISE_API_KEY"] == "tt_secret"
    assert seen["env"]["TORTOISE_API_URL"] == "https://api.example", (
        "the URL must come from the SAME chain as the key (co-source, #2369 D1.1)"
    )


def test_a_failed_sweep_leaves_its_reason_where_the_caller_can_read_it(tmp_path):
    """The production caller DISCARDED our output, so the outcome must be a file.

    The Pi extension spawns this detached with ``stdio="ignore"``: stdout and
    stderr — where ``main`` prints every diagnosis — go nowhere. Without the
    status file a worker that RAN AND FAILED (``node`` too old, no receipt) is
    indistinguishable from one that never ran, and the box reports
    ``capture: OK`` while the crash/kill recovery is inert.
    """
    from tortoise import store_sync

    cursor = tmp_path / "store-sync"
    # A worker that cannot even start is a FAILED sweep, not an absent one.
    receipt = store_sync.run_worker(
        session_dir=str(tmp_path / "s"), cursor_dir=str(cursor),
        node=str(tmp_path / "no-such-node"),
    )
    assert receipt["ok"] is False, receipt
    store_sync.write_sweep_status(receipt, str(cursor))

    status = json.loads((cursor / "sweep-status.json").read_text())
    assert status["ok"] is False
    assert status["reason"], "a FAILED sweep must carry a reason, not just ok=false"
    assert status["version"] == 1
    # Best-effort by contract: unwritable bookkeeping must not raise into a sweep.
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    store_sync.write_sweep_status(receipt, str(blocker))


def _main_args(cursor_dir, **overrides):
    """The exact attribute set `main` reads — so a new CLI flag fails loudly here."""
    base = dict(
        api_key=None, api_url=None, no_spool=True, session_dir=None,
        cursor_dir=str(cursor_dir), file=None, max_files=None, max_requests=None,
        dry_run=False, exclude_session_id=None, json=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_MAIN_persists_the_failure_it_reports(tmp_path, monkeypatch):
    """The CALL must write the status, not just the function when called by hand.

    The extension discards `main`'s output, so the write in `main` is the ONLY
    thing connecting a real failing sweep to the health line. The row above calls
    `write_sweep_status` itself, which proves the function and not the wiring:
    deleting the call from `main` left this file green.
    """
    from tortoise import store_sync

    # No worker artifact => a FAILED sweep with a named reason, deterministically
    # (no `node`, no network, no clock).
    monkeypatch.setattr(store_sync, "worker_path", lambda: tmp_path / "missing-worker.ts")
    cursor = tmp_path / "store-sync"
    assert store_sync.main(_main_args(cursor)) == 1, "a failed sweep exits non-zero"

    status = json.loads((cursor / "sweep-status.json").read_text())
    assert status["ok"] is False
    assert status["reason"] == "worker_missing", status


def test_the_persisted_reason_is_a_TOKEN_and_the_file_is_0600(tmp_path):
    """The health line embeds `reason` in a comma-delimited field.

    The precedence fix is what made the CLI's `reason` reach that line, so a
    free-form stderr sentence would arrive there: a real no-credential box
    printed the same fact twice and put an unquoted em-dash clause inside a
    comma-separated list. The full text stays on stderr and in `--json`.
    """
    from tortoise import store_sync

    store = tmp_path / "store"
    store_sync.write_sweep_status(
        {"ok": False, "log": {"errors": [
            "no TORTOISE_API_KEY configured — the log half shipped nothing"]}},
        str(store),
    )
    status = json.loads((store / "sweep-status.json").read_text())
    assert status["reason"] == "no_credential", "the reason must be a token"
    # ... and the SAME filename must not carry two modes depending on which
    # writer made it: the extension writes 0600, and the CLI used to write 0644.
    assert (store / "sweep-status.json").stat().st_mode & 0o777 == 0o600
    assert store.stat().st_mode & 0o777 == 0o700


def test_an_EXISTING_0755_store_directory_is_narrowed_by_the_CLI_too(tmp_path):
    """`mkdir`'s `mode` applies only when it CREATES the directory.

    An upgraded box already has the store, created 0755 by an earlier
    generation — and on a box whose sweeps SUCCEED this writer is the only one
    that ever runs, so without an explicit chmod the 0700 intent stays unmet
    there forever, silently. The extension's writer narrows it; the CLI's must
    too, or the two legs disagree about a directory they share.
    """
    from tortoise import store_sync

    store = tmp_path / "store"
    store.mkdir()
    store.chmod(0o755)
    assert store.stat().st_mode & 0o777 == 0o755, "the premise: an older generation's dir"
    store_sync.write_sweep_status({"ok": True, "log": {"shipped": 1}}, str(store))
    assert store.stat().st_mode & 0o777 == 0o700, "the CLI must narrow it too"
    assert (store / "sweep-status.json").stat().st_mode & 0o777 == 0o600


def test_every_reason_source_is_sanitised_at_the_SINGLE_write_site(tmp_path):
    """The health line renders the reason inside a COMMA-DELIMITED field.

    A per-producer fix was the generation-13 shape, and `spool.error` — a raw
    exception message, which is precisely where commas and em-dashes live —
    walked straight past it. This pins the sanitiser AND the three producers that
    feed it, so a new source cannot be added without passing through it.
    """
    from tortoise import store_sync

    forbidden = set(",;—–\"'()")

    def reason_for(receipt):
        store = tmp_path / f"store-{reason_for.n}"
        reason_for.n += 1
        store_sync.write_sweep_status(receipt, str(store))
        return json.loads((store / "sweep-status.json").read_text())["reason"]

    reason_for.n = 0

    # (a) known classes become TOKENS, per leg of the failure
    assert reason_for({"ok": False, "log": {"errors": [
        "SyntaxError: Unexpected token , in JSON at position 41"]}}) == "bad_json"
    assert reason_for({"ok": False, "log": {"errors": [
        "EACCES: permission denied, open '/a/b'"]}}) == "permission_denied"
    assert reason_for({"ok": False, "log": {"errors": [
        "ENOSPC: no space left on device"]}}) == "no_space"
    assert reason_for({"ok": False, "log": {"errors": [
        "/s/f.jsonl: no session header id — refused (never invented)"]}}) == "no_header_id"

    # (b) an UNRECOGNISED error keeps its information but loses the punctuation
    # that would forge a sibling term in the line.
    odd = reason_for({"ok": False, "log": {"errors": [
        "weird: a, b — c (d) \"e\"'f"]}})
    assert not (set(odd) & forbidden), f"punctuation survived: {odd!r}"
    assert "weird" in odd, "the information must survive the sanitisation"

    # (c) `spool.error` is a RAW EXCEPTION MESSAGE and used to bypass the
    # sanitiser entirely — measured through the line as:
    #   store-sync FAILED (FileNotFoundError: [Errno 2] ... '/tmp/a, b' — gave up)
    spool = reason_for({"ok": False, "spool": {
        "error": "FileNotFoundError: [Errno 2] No such file or directory: '/tmp/a, b' — gave up"}})
    assert not (set(spool) & forbidden), f"spool.error bypassed the sanitiser: {spool!r}"
    assert "FileNotFoundError" in spool

    # (d) `worker_status` tokens stay exact
    assert reason_for({"ok": False, "log": {"worker_status": "worker_missing"}}) == "worker_missing"


def test_a_dry_run_never_overwrites_a_real_failure(tmp_path, monkeypatch):
    """A PLAN is not an outcome — `--dry-run` must not turn a failure healthy.

    `main` wrote the status unconditionally, so a human running
    `tortoise store-sync --dry-run` on a box whose real sweep had recorded
    `ok: false` erased that failure and the health line went back to OK until the
    next real sweep.
    """
    from tortoise import store_sync

    cursor = tmp_path / "store-sync"
    cursor.mkdir()
    (cursor / "sweep-status.json").write_text(
        json.dumps({"version": 1, "ok": False, "reason": "no_credential"})
    )
    monkeypatch.setattr(store_sync, "worker_path", lambda: tmp_path / "missing-worker.ts")
    store_sync.main(_main_args(cursor, dry_run=True))

    status = json.loads((cursor / "sweep-status.json").read_text())
    assert status["reason"] == "no_credential", (
        "a dry run reported an outcome it did not produce"
    )
    assert status["ok"] is False


# ── the SPOOL half: consent, credential, and the two filters ──────────────


def _spool_fixture(monkeypatch, tmp_path, session_ids=("sess-live", "sess-dead")):
    """A real spool under tmp_path with one entry per session id."""
    from tortoise.capture_spool import Snapshot, write_spool_entry

    root = tmp_path / "spool"
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(root))
    for sid in session_ids:
        write_spool_entry(root, Snapshot(
            session_id=sid,
            turns=[{"role": "user", "content": f"hello from {sid}"},
                   {"role": "assistant", "content": "and back"}],
            source=f"{sid}.pi.jsonl",
            machine_id="m" * 64,
            harness="pi",
        ))
    return root


def test_the_spool_half_files_when_consented(monkeypatch, tmp_path):
    """The spool leg really runs — the headline claim ("one command, both
    substrates"), which had NO row at all: every process row passes
    `--no-spool`, so a regression there was green."""
    from tortoise.store_sync import run_sweep

    posted: list[dict] = []

    def post(payload):
        from tortoise.capture_spool import PostOutcome
        posted.append(payload)
        return PostOutcome(ok=True, status=200, body={"session_id": payload["session_id"]})

    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    _spool_fixture(monkeypatch, tmp_path, session_ids=("sess-dead",))
    receipt = run_sweep(spool=True, post=post, node="/nonexistent/node")
    assert receipt["spool"]["filed"] == 1, receipt["spool"]
    assert [p["session_id"] for p in posted] == ["sess-dead"]
    # And the summary keys are read off a REAL FlushSummary — not a stub.
    for key in ("filed", "deferred", "skipped", "held_back", "held_by_backoff",
                "terminal", "discarded"):
        assert key in receipt["spool"], f"{key} missing from the spool summary"


def test_the_spool_half_is_CONSENT_GATED(monkeypatch, tmp_path):
    """`tortoise store-sync` is the FOURTH upload path.

    Every other transmitting primitive (`session capture`, `sessions import`,
    `session drain`) refuses without consent (#3615); this one POSTed the spool
    anyway. The spool write itself is deliberately ungated (consent authorizes
    transmission, not durability), so a host that never consented has a POPULATED
    spool — and this command shipped it.

    MUTATION THAT REDS THIS: delete the `capture_consent_enabled()` branch in
    `run_sweep` → the entry below is posted.
    """
    from tortoise.store_sync import run_sweep

    posted: list[dict] = []

    def post(payload):
        from tortoise.capture_spool import PostOutcome
        posted.append(payload)
        return PostOutcome(ok=True, status=200, body={})

    monkeypatch.delenv("TORTOISE_CAPTURE", raising=False)
    _spool_fixture(monkeypatch, tmp_path, session_ids=("sess-unconsented",))
    receipt = run_sweep(spool=True, post=post, node="/nonexistent/node")
    assert posted == [], "a host that never consented had its spool uploaded"
    assert receipt["spool"]["status"] == "declined", receipt["spool"]
    assert "consent" in receipt["spool"]["detail"]


def test_the_spool_half_reports_no_credential_without_posting(monkeypatch, tmp_path):
    from tortoise.store_sync import run_sweep

    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    _spool_fixture(monkeypatch, tmp_path, session_ids=("sess-nocred",))
    receipt = run_sweep(spool=True, api_key=None, node="/nonexistent/node")
    assert receipt["spool"]["status"] == "no_credential", receipt["spool"]
    assert receipt["ok"] is False, "a substrate that was NOT drained is not a success"


def test_a_dry_run_drains_NO_spool_entry_and_stamps_NOTHING(monkeypatch, tmp_path):
    """`--dry-run` is "Plan only — POST nothing, move no cursor" (`--help`).

    `dry_run` reached `run_worker` and nothing else, so the DEFAULT (spool on)
    mode uploaded every spooled transcript — a real `flush_spool` transport, not
    a stub — and then stamped the entry's outcome (`filed_key`, backoff, or a
    terminal marker). The plan could not even be reproduced afterwards: the
    entries it reported on had changed.

    The spool is snapshotted byte-for-byte, so this reds on ANY mutation (a POST
    is only the loudest one) — and the follow-up real sweep proves the entry is
    still filable, i.e. nothing was consumed.

    MUTATION THAT REDS THIS: drop the `spool and dry_run` branch in `run_sweep`.
    """
    from tortoise.store_sync import run_sweep

    posted: list[dict] = []

    def post(payload):
        from tortoise.capture_spool import PostOutcome
        posted.append(payload)
        return PostOutcome(ok=True, status=200, body={})

    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    root = _spool_fixture(monkeypatch, tmp_path, session_ids=("sess-dry",))

    def snapshot() -> dict[str, bytes]:
        return {
            str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }

    before = snapshot()
    receipt = run_sweep(spool=True, dry_run=True, post=post, node="/nonexistent/node")
    assert posted == [], "a plan-only run POSTed spooled transcript turns"
    assert receipt["spool"]["status"] == "dry_run", receipt["spool"]
    assert snapshot() == before, "a plan-only run mutated the spool it was reporting on"

    # And the entry is untouched, not merely quiet: a REAL sweep still files it.
    real = run_sweep(spool=True, post=post, node="/nonexistent/node")
    assert [p["session_id"] for p in posted] == ["sess-dry"], (
        "the dry run consumed the entry it was supposed to report on"
    )
    assert real["spool"]["filed"] == 1


def test_both_halves_honour_exclude_session_id_and_file(monkeypatch, tmp_path):
    """`--exclude-session-id` reaches the SPOOL leg too.

    It was forwarded only to the log half, so `tortoise store-sync
    --exclude-session-id <live>` filed the LIVE session from the spool anyway —
    after which the server skips extraction for every later turn of it. The
    command's own help documents exactly that consequence. `--file` had the
    same shape (forwarded only to the worker).

    MUTATION THAT REDS THIS: drop `exclude_session_id=` from either `flush_spool`
    call in `run_sweep` → the live session is posted.
    """
    from tortoise.store_sync import run_sweep

    posted: list[dict] = []

    def post(payload):
        from tortoise.capture_spool import PostOutcome
        posted.append(payload)
        return PostOutcome(ok=True, status=200, body={})

    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    _spool_fixture(monkeypatch, tmp_path, session_ids=("sess-live", "sess-dead"))
    run_sweep(spool=True, post=post, exclude_session_id="sess-live",
              node="/nonexistent/node")
    assert [p["session_id"] for p in posted] == ["sess-dead"], (
        "the LIVE session was filed from the spool"
    )

    # `--file` names one session for the LOG half; the spool half must narrow to
    # that same session rather than draining everything.
    posted.clear()
    run_sweep(spool=True, post=post,
              files=[str(tmp_path / "2026-10-23T10-00-00Z_sess-live.jsonl")],
              node="/nonexistent/node")
    assert [p["session_id"] for p in posted] == ["sess-live"], posted


# ── the worker's skip path, and the cursor it must not touch ──────────────


def test_dry_run_moves_no_cursor_at_process_level(server, tmp_path):
    """The CLI advertises "Plan only — POST nothing, move no cursor".

    The no-turns branch advanced the cursor unconditionally, so a `--dry-run`
    over a session with no shippable turn yet wrote `cursor.json` and moved an
    offset — a durable side effect from a command documented as read-only.

    MUTATION THAT REDS THIS: remove the `if (dryRun)` guard above the
    `!turns.length` branch in `store-sync.ts`, or the `if (!dryRun)` around the
    final `saveCursor`.
    """
    _session_file(tmp_path, "a.jsonl", [
        _header("sid-dryrun"),
        json.dumps({"type": "label", "id": "l1", "parentId": None,
                    "timestamp": "2026-10-23T10:00:01.000Z", "label": "cp"}),
    ])
    _proc, receipt = _run_sync(tmp_path, "--dry-run", server=server)
    assert server.calls == 0, "a dry run POSTed"
    assert receipt["log"]["noTurns"] == 1, receipt["log"]
    assert not (tmp_path / "cursor" / "cursor.json").exists(), (
        "a report-only run created the cursor store"
    )


def test_an_up_to_date_file_is_skipped_without_a_second_full_read():
    """Source pin: the sweep reads the HEADER LINE before the whole file.

    A `session_start` sweep runs over the user's entire session history, so the
    full `readFileSync` + per-line `JSON.parse` in `scanSessionFile` must sit
    BEHIND the skip decision — otherwise every start re-reads the whole tree (a
    fleet box: gigabytes, per start, times concurrent sessions). This is a
    STRUCTURAL pin: a behavioural one would need a byte counter inside the
    worker, and the worker deliberately takes no test seam for its reads. It
    fails if the ordering is reversed or the cheap path is deleted.
    """
    source = WORKER.read_text(encoding="utf-8")
    loop = source[source.index("for (const path of candidates)"):]
    head_at = loop.index("scanHeaderLine(")
    plan_at = loop.index("planFile(")
    full_at = loop.index("scanSessionFile(")
    assert head_at < plan_at < full_at, (
        "the header-only read and the skip decision must come BEFORE the full "
        "scan: a sweep must not read a file it is going to skip"
    )
    cheap = loop[:full_at]
    assert 'action === "skip"' in cheap and "continue;" in cheap, (
        "the cheap skip branch (no full read) is missing"
    )


# ── the sweep's VERDICT: "delivered nothing" must never read OK ────────

def test_a_RETAINED_batch_is_not_reported_OK(server, tmp_path):
    """401/403/407 are RETAINED on purpose (crossing the cursor would destroy the
    sessions), and a retained batch exits 0 with no ``errors[]`` — so the worker's
    own verdict is TRUE for a sweep that delivered NOTHING.

    That made ``capture: OK`` the only user-visible report over a backstop that a
    rotated/expired key had silently dead-ended: the turns are safe, but nothing
    ships, indefinitely. The verdict must carry it, the exit code must follow it,
    and the status file the extension spawns against must name the code.
    """
    _session_file(tmp_path, "a.jsonl", [
        _header("sid-retained"),
        _message("u1", None, "user", "must survive a 401"),
    ])
    server.status = 401
    proc, receipt = _run_sync(tmp_path, server=server)
    assert receipt["log"]["shipped"] == 0
    assert receipt["log"]["files"][0]["action"] == "failed"
    assert receipt["log"]["withheld"] == "http_401", receipt["log"]
    assert receipt["ok"] is False, "a sweep that shipped nothing, holding turns, is not OK"
    assert proc.returncode == 1, "and the exit code must follow the verdict"

    status = json.loads((tmp_path / "cursor" / "sweep-status.json").read_text())
    assert status["ok"] is False
    assert status["reason"] == "http_401", status

    # The turns are SAFE (the cursor was retained): a later accepted run ships them.
    server.status = 200
    _, later = _run_sync(tmp_path, server=server)
    assert later["log"]["shipped"] == 1
    assert later["ok"] is True, "a sweep that delivered is OK again"
    assert [t["content"] for t in server.sessions[-1]["conversation"]] == [
        "must survive a 401"
    ]


def test_a_shrunk_REFUSAL_does_not_fail_the_sweep_but_an_ACTIONABLE_one_still_wins(
    server, tmp_path
):
    """A ``/compact`` refusal must not paint a permanent red — and must never HIDE
    an actionable refusal made in the same sweep.

    Refusing the shorter window is the design (shipping it would let the turn store
    hard-delete past it), and the unproven refusal writes no cursor row, so the same
    dead file is refused on EVERY sweep for as long as it exists — 55 of 292 real
    session files carry a `compaction`. Failing the sweep for that would make the
    health line permanently red on a backstop that is working exactly as designed
    (and `store-sync.ts` documents 409 as benign for the same reason: "already in
    flight"). But the skip must be PER FILE: a rotated key on the same box is the
    thing a user can act on, and a sweep that reported the unfixable `window_shrunk`
    instead would hide it on every sweep, forever.
    """
    _session_file(tmp_path, "a.jsonl", [
        _header("sid-shrink"),
        _message("u1", None, "user", "one"),
        _message("a1", "u1", "assistant", "two"),
        _message("u2", "a1", "user", "three"),
        _message("a2", "u2", "assistant", "four"),
    ])
    _, first = _run_sync(tmp_path, server=server)
    assert first["log"]["shipped"] == 1
    assert first["ok"] is True

    # Pi rewrote the tree: a /compact entry cuts the kept window to u3..a3,
    # which is SHORTER than what the store already holds for this session.
    path = tmp_path / "sessions" / "a.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "compaction", "id": "c1", "parentId": "a2",
            "timestamp": "2026-10-23T10:00:02.000Z",
            "firstKeptEntryId": "u3",
        }) + "\n")
        fh.write(_message("u3", "c1", "user", "five") + "\n")
        fh.write(_message("a3", "u3", "assistant", "six") + "\n")

    # (1) The shrunk refusal ALONE: the count is reported, the verdict is not failed.
    shrunk_proc, second = _run_sync(tmp_path, server=server)
    assert second["log"]["shrunk"] == 1, second["log"]
    assert second["log"]["shipped"] == 0
    assert second["log"].get("withheld") is None, (
        "a designed refusal of one dead file is not the backstop being inert"
    )
    assert second["ok"] is True
    assert shrunk_proc.returncode == 0

    # (2) The SAME sweep, with a rotated key: the actionable refusal must win.
    _session_file(tmp_path, "b.jsonl", [
        _header("sid-rotated"),
        _message("v1", None, "user", "this one a user can fix"),
    ])
    server.status = 401
    mixed_proc, mixed = _run_sync(tmp_path, server=server)
    assert mixed["log"]["shrunk"] >= 1, mixed["log"]
    assert mixed["log"]["withheld"] == "http_401", mixed["log"]
    assert mixed["ok"] is False
    assert mixed_proc.returncode == 1

    status = json.loads((tmp_path / "cursor" / "sweep-status.json").read_text())
    assert status["ok"] is False
    assert status["reason"] == "http_401", status


def test_a_409_is_SURFACED_because_one_of_its_two_meanings_is_a_policy_refusal(
    server, tmp_path
):
    """`POST /v1/sessions` answers 409 for TWO different things and the worker cannot
    tell them apart (`postCapture` keeps no headers, and the worker forbids matching
    prose): the benign in-flight duplicate, AND "Session recording is disabled for
    this team" — an org policy the user can reverse and which never clears by
    retrying.

    Calling the code benign would report `capture: OK` for ever on a box whose
    recording is switched off. Surfacing it is fail-CLOSED and self-clearing: an
    in-flight collision clears on the next sweep, and a disabled toggle clears when
    the user enables it.
    """
    _session_file(tmp_path, "a.jsonl", [
        _header("sid-409"),
        _message("u1", None, "user", "a policy the user can reverse"),
    ])
    server.status = 409
    proc, receipt = _run_sync(tmp_path, server=server)
    assert receipt["log"]["files"][0]["action"] == "failed"
    assert receipt["log"]["withheld"] == "http_409", receipt["log"]
    assert receipt["ok"] is False
    assert proc.returncode == 1
    status = json.loads((tmp_path / "cursor" / "sweep-status.json").read_text())
    assert status["reason"] == "http_409", status

    # Self-clearing: the same file ships once the refusal lifts.
    server.status = 200
    later_proc, later = _run_sync(tmp_path, server=server)
    assert later["log"]["shipped"] == 1
    assert later["ok"] is True
    assert later_proc.returncode == 0


def test_an_ACTIONABLE_refusal_outranks_a_no_header_id_error(server, tmp_path):
    """``errors[]`` must not mask an actionable refusal.

    A header-less ``.jsonl`` pushes ``no_header_id`` into ``errors[]``, so the sweep
    is already failed and the reason precedence would take ``errors[0]``. One stray
    file is therefore enough to turn every sweep's reason into the one thing nobody
    can act on, while a rotated key goes unreported on every surface the production
    caller reads (the detached sweep discards `--json`).
    """
    _session_file(tmp_path, "a-noheader.jsonl", [
        _message("v1", None, "user", "no session header"),
    ])
    _session_file(tmp_path, "b.jsonl", [
        _header("sid-rotated"),
        _message("u1", None, "user", "the key got rotated"),
    ])
    server.status = 401
    proc, receipt = _run_sync(tmp_path, server=server)
    assert receipt["log"]["errors"], "the premise: errors[] is non-empty here"
    # The header-less file SORTS FIRST, so the worker enumerates it first and
    # `errors[0]` is the no-header reason: this is the ordering that makes the
    # assertion discriminate. If `no_header_id` were ever treated as actionable
    # (or if the withheld rule were gated on the worker's own verdict), one stray
    # file would mask the rotated key again and these assertions red.
    assert receipt["log"]["files"][0]["reason"] == "no_header_id", receipt["log"]["files"]
    assert receipt["log"]["withheld"] == "http_401", receipt["log"]
    assert receipt["ok"] is False
    assert proc.returncode == 1
    status = json.loads((tmp_path / "cursor" / "sweep-status.json").read_text())
    assert status["reason"] == "http_401", status


def test_a_no_header_id_ONLY_sweep_still_reports_no_header_id(server, tmp_path):
    """The other side of the classification: not actionable for MASKING purposes is
    not "silent". A header-less ``.jsonl`` is refused, the sweep is failed, and the
    reason is named — it just cannot outrank an actionable refusal."""
    _session_file(tmp_path, "a-noheader.jsonl", [
        _message("u1", None, "user", "no session header"),
    ])
    proc, receipt = _run_sync(tmp_path, server=server)
    assert receipt["log"]["files"][0]["reason"] == "no_header_id"
    assert receipt["log"].get("withheld") is None, "non-actionable, so not withheld"
    assert receipt["ok"] is False, "the sweep is still failed"
    assert proc.returncode == 1
    status = json.loads((tmp_path / "cursor" / "sweep-status.json").read_text())
    assert status["ok"] is False
    assert status["reason"] == "no_header_id", status
    assert server.calls == 0, "an invented id would have produced a POST"


def test_a_cursor_that_could_not_be_PERSISTED_is_a_withheld_verdict(server, tmp_path):
    """The turns post, the offset does not move — so every sweep re-POSTs the same
    window. That signal is named by the worker (``cursorWriteError``) and used to be
    printed only on stderr, which the production caller discards."""
    _session_file(tmp_path, "a.jsonl", [
        _header("sid-cursor-ro"),
        _message("u1", None, "user", "the cursor cannot be saved"),
    ])
    cursor = tmp_path / "cursor"
    cursor.mkdir(parents=True, exist_ok=True)
    os.chmod(cursor, 0o500)  # read+traverse, no create: the save must fail
    try:
        proc, receipt = _run_sync(tmp_path, server=server)
    finally:
        os.chmod(cursor, 0o700)
    assert receipt["log"].get("cursorWriteError"), receipt["log"]
    assert receipt["log"]["withheld"] == "cursor_write_failed", receipt["log"]
    assert receipt["ok"] is False
    assert proc.returncode == 1


def test_the_default_cursor_dir_is_BESIDE_the_spool_like_both_readers(monkeypatch, tmp_path):
    """A manual ``tortoise store-sync`` with no ``--cursor-dir`` must not write to
    ``$HOME``.

    Both health-line readers derive the store as ``<spool-parent>/store-sync``
    (``capture_spool._store_sync_dir``, and the Pi leg's ``dirname(spoolDir)``),
    and the extension spawns its sweep with exactly that ``--cursor-dir``. A
    ``$HOME`` default therefore made a manual sweep's FAILED verdict invisible
    (the line kept the previous one) AND gave the manual run a SECOND cursor over
    the same files, re-reading them from 0 and re-shipping turns the extension's
    cursor already covered.
    """
    from tortoise import capture_spool, store_sync

    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path / "root" / "spool"))
    assert store_sync.default_cursor_dir() == tmp_path / "root" / "store-sync"
    assert store_sync.default_cursor_dir() == capture_spool._store_sync_dir(
        capture_spool.spool_dir()
    )
    # The helper being right is not the claim: the WIRING is. Drive `main` with no
    # `--cursor-dir` and prove the status lands where both readers look — the old
    # `$HOME` default wrote it somewhere no reader opens, and this assertion is
    # the one that reds.
    monkeypatch.setattr(store_sync, "worker_path", lambda: tmp_path / "missing-worker.ts")
    args = _main_args(tmp_path / "unused")
    args.cursor_dir = None
    assert store_sync.main(args) == 1, "a failed manual sweep exits non-zero"
    landed = tmp_path / "root" / "store-sync" / "sweep-status.json"
    assert landed.exists(), "the verdict must land BESIDE the spool, not under $HOME"
    assert json.loads(landed.read_text())["reason"] == "worker_missing"


def test_the_status_temp_path_is_PER_PROCESS(tmp_path, monkeypatch):
    """The CLI and the extension's ``noteFailure`` write the same directory.

    With one shared ``sweep-status.json.tmp``, a permanent failure recorded by
    one could be published away by the other's healthy payload — a lost update
    that reads ``capture: OK``. The WRITER is what must be pinned: a successful
    write renames the temp away, so the rename is refused on purpose to leave the
    name behind and show which one was used.
    """
    from tortoise import store_sync

    def refuse_replace(self, target):
        raise OSError("rename refused (so the temp name is observable)")

    monkeypatch.setattr(type(tmp_path), "replace", refuse_replace, raising=False)
    assert store_sync._status_tmp_path(tmp_path).name == f"sweep-status.json.tmp.{os.getpid()}"
    receipt = {"ok": True, "log": {"errors": [], "shipped": 0}}
    for pid in (111, 222):
        monkeypatch.setattr(store_sync, "os", types.SimpleNamespace(getpid=lambda p=pid: p))
        store_sync.write_sweep_status(receipt, tmp_path)
    survivors = sorted(p.name for p in tmp_path.iterdir())
    assert survivors == [
        "sweep-status.json.tmp.111",
        "sweep-status.json.tmp.222",
    ], f"two writers must never share a temp name: {survivors}"


# ── the SESSION-EVENTS leg (#6388): the third substrate and its two call sites
#
# Every row below exists because the leg arrived with NO test on either of the
# two sites that make it a transmission path: `run_sweep`'s third leg
# (`store_sync.py`) and `_cmd_session_drain`'s appended block
# (`__main__.py`). Deleting either left every case in the repo green — including
# the CONSENT GATE, which is the one line that decides whether a new primitive
# uploads a user's transcripts.


def _events_fixture(monkeypatch, tmp_path, session_ids=("sess-ev-1",)):
    """A real ``~/.tortoise/session-events/`` day file under ``tmp_path``.

    ``TORTOISE_SESSION_EVENTS_DIR`` is pinned (the module documents that this is
    how a test gets its own substrate) and the SPOOL root is pinned beside it, so
    the derived cursor directory also lands under ``tmp_path``.
    """
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path / "spool"))
    root = tmp_path / "session-events"
    root.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps({
        "session_id": sid,
        "conversation": [{"role": "user", "content": f"hello from {sid}"}],
        "metadata": {"source": "pi-session-quit"},
        "harness": "pi",
        "machine_id": "m" * 64,
        "model": "deepseek/deepseek-flash",
    }) + "\n" for sid in session_ids)
    (root / "2026-10-01.jsonl").write_text(body, encoding="utf-8")
    monkeypatch.setenv("TORTOISE_SESSION_EVENTS_DIR", str(root))
    return root


def _stub_post(sink: list[dict]):
    """A transport that records payloads and answers 2xx-with-extraction."""
    from tortoise.capture_spool import PostOutcome

    def post(payload):
        sink.append(payload)
        return PostOutcome(ok=True, status=201, body={"extraction_mode": "extracted"})

    return post


def test_the_session_events_leg_files_when_consented(monkeypatch, tmp_path):
    """The THIRD substrate really drains — the leg this PR exists to add.

    MUTATION THAT REDS THIS: delete the `else:` branch that calls
    `drain_session_events` in `run_sweep` → the day file is never read and
    nothing is posted.
    """
    from tortoise.store_sync import run_sweep

    posted: list[dict] = []
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    _events_fixture(monkeypatch, tmp_path, ("sess-ev-1", "sess-ev-2"))
    cursor = tmp_path / "store-sync"

    receipt = run_sweep(spool=False, post=_stub_post(posted),
                        cursor_dir=cursor, node="/nonexistent/node")

    assert receipt["session_events"]["filed"] == 2, receipt["session_events"]
    assert sorted(p["session_id"] for p in posted) == ["sess-ev-1", "sess-ev-2"]
    # The pass persisted its position: the NEXT sweep must not re-POST these.
    assert (cursor / "session-events-cursor.json").exists()


def test_the_session_events_leg_is_CONSENT_GATED(monkeypatch, tmp_path):
    """The leg is a TRANSMISSION primitive: no consent ⇒ nothing leaves the box.

    MUTATION THAT REDS THIS: drop the `capture_consent_enabled()` branch for this
    leg in `run_sweep` → an unconsented host's transcripts are uploaded.
    """
    from tortoise.store_sync import run_sweep

    posted: list[dict] = []
    monkeypatch.delenv("TORTOISE_CAPTURE", raising=False)
    _events_fixture(monkeypatch, tmp_path, ("sess-ev-unconsented",))
    cursor = tmp_path / "store-sync"

    receipt = run_sweep(spool=False, post=_stub_post(posted),
                        cursor_dir=cursor, node="/nonexistent/node")

    assert posted == [], "a host that never consented had its day files uploaded"
    assert receipt["session_events"]["status"] == "declined", receipt["session_events"]
    assert "consent" in receipt["session_events"]["detail"]
    assert not (cursor / "session-events-cursor.json").exists()


def test_the_session_events_leg_reports_no_credential_without_posting(
        monkeypatch, tmp_path):
    """No key ⇒ the leg says so AND the sweep is not reported as OK.

    Under `--no-spool` (the Pi cadence's own invocation) there is no spool half to
    carry that verdict, so if this leg is silent the whole sweep reads `ok: true`
    over an undrained substrate.
    """
    from tortoise.store_sync import run_sweep

    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    _events_fixture(monkeypatch, tmp_path, ("sess-ev-nocred",))

    receipt = run_sweep(spool=False, api_key=None, post=None,
                        cursor_dir=tmp_path / "store-sync",
                        node="/nonexistent/node")

    assert receipt["session_events"]["status"] == "no_credential", receipt["session_events"]
    assert receipt["ok"] is False, "a substrate that was NOT drained is not a success"


def test_a_dry_run_plans_the_session_events_leg_without_moving_it(
        monkeypatch, tmp_path):
    """`--dry-run` answers "how big is the backlog" for THIS substrate too.

    MUTATION THAT REDS THIS: drop the `dry_run` branch → the plan posts the day's
    records and moves the cursor, i.e. the plan performs the work it reports on.
    """
    from tortoise.store_sync import run_sweep

    posted: list[dict] = []
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    _events_fixture(monkeypatch, tmp_path, ("sess-ev-plan-1", "sess-ev-plan-2"))
    cursor = tmp_path / "store-sync"

    receipt = run_sweep(spool=False, dry_run=True, post=_stub_post(posted),
                        cursor_dir=cursor, node="/nonexistent/node")

    assert receipt["session_events"]["status"] == "dry_run", receipt["session_events"]
    assert receipt["session_events"]["queued"] == 2
    assert posted == []
    assert not (cursor / "session-events-cursor.json").exists()


def test_a_cursor_store_that_cannot_be_written_is_not_reported_OK(
        monkeypatch, tmp_path):
    """A backlog that moved but could not be REMEMBERED is a failure, not a pass.

    The next sweep re-POSTs the whole pass, so the operator has to see it — the
    verdict belongs on the sweep, not only in the leg's own summary.

    MUTATION THAT REDS THIS: drop the `cursor_error → ok = False` arm in
    `run_sweep` → the health line reports a clean sweep over a re-POST storm.
    """
    from tortoise.store_sync import run_sweep

    posted: list[dict] = []
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    _events_fixture(monkeypatch, tmp_path, ("sess-ev-cursor",))
    # A FILE where the cursor directory should be: every write into it is ENOENT
    # / ENOTDIR, deterministically, with no permissions to arrange.
    blocker = tmp_path / "store-is-a-file"
    blocker.write_text("not a directory", encoding="utf-8")

    receipt = run_sweep(spool=False, post=_stub_post(posted),
                        cursor_dir=blocker, node="/nonexistent/node")

    assert receipt["session_events"]["cursor_error"], receipt["session_events"]
    assert receipt["ok"] is False


def test_a_broken_events_leg_does_not_kill_the_other_legs(monkeypatch, tmp_path):
    """A substrate that raises is REPORTED on the leg, never fatal to the sweep.

    MUTATION THAT REDS THIS: remove the `except Exception` arm around the leg →
    the exception escapes `run_sweep` and the log half never runs at all.
    """
    import tortoise.session_events_drain as events_mod
    from tortoise.store_sync import run_sweep

    posted: list[dict] = []
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    _events_fixture(monkeypatch, tmp_path, ("sess-ev-boom",))
    monkeypatch.setattr(events_mod, "drain_session_events",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("substrate on fire")))

    receipt = run_sweep(spool=False, post=_stub_post(posted),
                        cursor_dir=tmp_path / "store-sync",
                        node="/nonexistent/node")

    assert "substrate on fire" in receipt["session_events"]["error"], receipt["session_events"]
    assert receipt["ok"] is False
    # The log half still ran: it is the leg that must not be lost to this one.
    assert receipt["log"] is not None


def test_the_sweep_forwards_the_FILE_scope_to_the_events_leg(monkeypatch, tmp_path):
    """`--file <session>` reaches this leg, and out-of-scope records stay QUEUED.

    MUTATION THAT REDS THIS: drop `only_session_id=` from the leg's call →
    `store-sync --file X` drains every session's records from this substrate.
    """
    from tortoise.store_sync import run_sweep

    posted: list[dict] = []
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    # The filename tail IS the scope (`_session_id_from_filename` reads it), so the
    # two ids have to be plausible ones — a 6-character tail is deliberately
    # rejected by the CLI rather than guessed at.
    _events_fixture(monkeypatch, tmp_path, ("sess-target", "sess-other"))
    cursor = tmp_path / "store-sync"

    receipt = run_sweep(spool=False, post=_stub_post(posted), cursor_dir=cursor,
                        files=[str(tmp_path / "2026-10-01T10-00-00Z_sess-target.jsonl")],
                        node="/nonexistent/node")

    assert [p["session_id"] for p in posted] == ["sess-target"]
    assert receipt["session_events"]["unselected"] == 1, receipt["session_events"]


def test_the_persisted_reason_NAMES_the_session_events_leg(tmp_path):
    """An events-only failure must not be persisted as a node-worker crash.

    The reason chain could only see the LOG half and the SPOOL half; with
    `--no-spool` there is no spool half, so the fallback `"worker exited
    non-zero"` named a component that had exited 0 and delivered its turns.

    MUTATION THAT REDS THIS: remove `_events_failure_reason` from the chain in
    `write_sweep_status` → both reasons become `"worker exited non-zero"`.
    """
    from tortoise import store_sync

    store = tmp_path / "store"

    store_sync.write_sweep_status(
        {"ok": False, "log": {}, "spool": None,
         "session_events": {"status": "no_credential", "detail": "no API key"}},
        store)
    assert json.loads((store / "sweep-status.json").read_text())["reason"] == "no_credential"

    store_sync.write_sweep_status(
        {"ok": False, "log": {}, "spool": None,
         "session_events": {"cursor_error": "PermissionError: denied"}}, store)
    assert json.loads((store / "sweep-status.json").read_text())["reason"] == "PermissionError: denied"


def test_write_sweep_status_PERSISTS_the_events_counts(tmp_path):
    """The body calls this leg "observable": the counts must reach the file.

    `sweep-status.json` is the only surface a caller that discarded our output can
    read, so a count that exists only in the receipt is a count nobody sees —
    including `held` and `unselected`, the two states that mean "nothing can move
    yet" rather than "nothing is left".

    MUTATION THAT REDS THIS: drop the `session_events` block from
    `write_sweep_status` (or any single key in it) → the health line can no longer
    distinguish an empty backlog from a withheld one.
    """
    from tortoise import store_sync

    store = tmp_path / "store"
    store_sync.write_sweep_status({
        "ok": True, "log": {},
        "session_events": {
            "filed": 3, "dead_lettered": 1, "deferred": 2, "held": 4,
            "unselected": 5, "degraded": 1, "queued": 6, "stuck": 7,
            "stopped": "batch", "cursor_error": "EACCES denied",
            "unreadable": ["a.jsonl.gz", "b.jsonl"],
        },
    }, store)

    assert json.loads((store / "sweep-status.json").read_text())["session_events"] == {
        "filed": 3, "dead_lettered": 1, "deferred": 2, "held": 4, "unselected": 5,
        "degraded": 1, "queued": 6, "stuck": 7, "stopped": "batch",
        "cursor_error": "EACCES denied", "unreadable": 2,
    }


def test_MAIN_prints_the_session_events_leg(monkeypatch, tmp_path, capsys):
    """The body claims every pass is "printed as one line".

    The persistence was implemented; the PRINT was not, on the leg the production
    cadence actually runs (`store-sync --no-spool`, stdio ignored) — so `stuck`,
    `held`, `unreadable` and `cursor_error` reached only `--json`.

    MUTATION THAT REDS THIS: delete the `receipt["session_events"]` print block
    from `main` → the progress line is absent from stderr.
    """
    from tortoise import store_sync

    posted: list[dict] = []
    monkeypatch.setattr(store_sync, "worker_path", lambda: tmp_path / "missing-worker.ts")
    monkeypatch.setattr("tortoise.__main__._session_post",
                        lambda key, url: _stub_post(posted))
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    _events_fixture(monkeypatch, tmp_path, ("sess-ev-main",))
    cursor = tmp_path / "store-sync"

    rc = store_sync.main(_main_args(cursor, api_key="tt_test",
                                    api_url="https://example.invalid"))

    err = capsys.readouterr().err
    assert rc == 1, "the log half has no worker, so the sweep is not OK"
    assert "session-events drain: filed 1" in err, err


def test_the_session_DRAIN_command_drains_the_session_events_substrate(
        monkeypatch, tmp_path, capsys):
    """`session drain` (the Claude `SessionStart` cadence) drains it too.

    MUTATION THAT REDS THIS: delete the `#6388` block from `_cmd_session_drain` →
    nothing is posted from the day file and no line is printed.
    """
    from tortoise import __main__ as cli

    posted: list[dict] = []
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    _events_fixture(monkeypatch, tmp_path, ("sess-ev-drain",))
    monkeypatch.setattr(cli, "_session_post", lambda key, url: _stub_post(posted))

    assert cli._cmd_session_drain("tt_test", "https://example.invalid") == 0

    assert [p["session_id"] for p in posted] == ["sess-ev-drain"]
    assert "session-events drain: filed 1" in capsys.readouterr().err


def test_the_session_drain_command_prints_a_pass_that_only_HELD(
        monkeypatch, tmp_path, capsys):
    """The print gate must fire on the states that make NO progress at all.

    Gating on `consumed` alone printed nothing when every record was held for the
    live session — the exact state an operator needs to see, because nothing will
    ever move until that session ends.

    MUTATION THAT REDS THIS: gate the print on `events["consumed"]` → held-only
    passes print nothing.
    """
    from tortoise import __main__ as cli

    posted: list[dict] = []
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    _events_fixture(monkeypatch, tmp_path, ("sess-ev-live",))
    monkeypatch.setattr(cli, "_session_post", lambda key, url: _stub_post(posted))

    assert cli._cmd_session_drain("tt_test", "https://example.invalid",
                                  exclude_session_id="sess-ev-live") == 0

    err = capsys.readouterr().err
    assert posted == [], "the LIVE session was filed from the day file"
    assert "held 1 (live session)" in err, err
