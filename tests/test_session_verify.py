"""#3809 — ``tortoise session verify`` (installed → captured → memory).

Every test here drives the REAL CLI chain:

* the installed seam is a REAL install written by ``capture_install`` into a
  TEMP HOME (never the user's ``~/.claude`` / ``~/.codex`` / ``~/.cursor`` /
  ``~/.pi``);
* the seam is FIRED for real (the registered command is executed by
  ``session_verify``);
* the capture lands against a REAL loopback HTTP server that answers the
  same ``/v1/sessions`` + ``/v1/onboarding/state`` contract the hosted API
  does.

The only test double is the ``tortoise`` CLI the fired hook invokes (a fake on
PATH that POSTs the capture to the loopback server) — the seam, the event
payload, the firing, the receipt read, the session retrieval and the deletion
are all the production paths.

Each guard docstring names the mutation that turns it RED; the mutations were
verified RED individually.
"""
from __future__ import annotations

import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tortoise.capture_install import install_capture
from tortoise.capture_receipts import capture_receipt_key
from tortoise.session_verify import (
    EXIT_BROKEN,
    EXIT_UNVERIFIABLE,
    resolve_install_root,
    verify_session_capture,
)

# ── a loopback hosted-API double ──────────────────────────────────────────


class _Graph:
    """In-memory stand-in for the hosted graph + onboarding state."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}
        self.receipts: dict[str, str] = {}
        self.posts: list[dict] = []
        self.deletes: list[str] = []
        self.fail_post = False
        self.fail_delete = False
        self.no_source = False
        self.omit_source_field = False
        self.no_receipt = False
        self.zero_extracted = False
        self.turn_override: dict[str, int] = {}
        self._clock = 0

    def tick(self) -> str:
        self._clock += 1
        return f"2026-09-19T00:00:{self._clock:02d}Z"


def _make_handlers(graph: _Graph):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep test output clean
            pass

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                return {}

        def do_POST(self):
            if self.path == "/v1/sessions":
                body = self._read_json()
                if graph.fail_post:
                    self._send(500, {"detail": "capture refused (test)"})
                    return
                graph.posts.append(body)
                sid = body.get("session_id") or "unnamed"
                harness = body.get("harness")
                conv = body.get("conversation") or []
                n = graph.turn_override.get(sid, len(conv))
                if not graph.no_receipt:
                    graph.receipts[capture_receipt_key(harness)] = graph.tick()
                session = {
                    "id": sid,
                    "harness": harness,
                    "turns": n,
                    "extracted": 0 if graph.zero_extracted else 1,
                    "turn_points": [
                        {"id": f"{sid}_t{i}", "role": t.get("role"),
                         "content": t.get("content")}
                        for i, t in enumerate(conv[:n])
                    ],
                    "extracted_points": ([] if graph.zero_extracted else
                                         [{"id": "pt_x", "kind": "statement",
                                           "content": "probe"}]),
                    "source": None if graph.no_source else {
                        "url": f"session:{sid}", "sourceKind": "agentSession",
                    },
                }
                if graph.omit_source_field:
                    session.pop("source", None)
                graph.sessions[sid] = session
                self._send(200, {"session_id": sid, "turns": n,
                                 "extracted": 0 if graph.zero_extracted else 1,
                                 "extraction_mode": "llm:mock"})
                return
            self._send(404, {"detail": "not found"})

        def do_GET(self):
            if self.path == "/v1/onboarding/state":
                self._send(200, {"onboarding": dict(graph.receipts)})
                return
            if self.path.startswith("/v1/sessions/"):
                sid = self.path.rsplit("/", 1)[-1]
                if sid in graph.sessions:
                    self._send(200, dict(graph.sessions[sid]))
                    return
                self._send(404, {"detail": "Session not found"})
                return
            self._send(404, {"detail": "not found"})

        def do_DELETE(self):
            if self.path.startswith("/v1/sessions/"):
                sid = self.path.rsplit("/", 1)[-1]
                if graph.fail_delete:
                    self._send(500, {"detail": "delete refused (test)"})
                    return
                graph.deletes.append(sid)
                graph.sessions.pop(sid, None)
                self._send(200, {"deleted": True, "cleaned_receipts": []})
                return
            self._send(404, {"detail": "not found"})

    return _Handler


@pytest.fixture
def hosted():
    graph = _Graph()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handlers(graph))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield graph, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


# ── the fake `tortoise` the fired seam invokes ────────────────────────────

_FAKE_TORTOISE = """\
#!/usr/bin/env python3
import json, os, sys, uuid
from urllib.request import Request, urlopen

argv = sys.argv[1:]
# The Claude hook backgrounds a corpus re-index; the fake must not block it.
if argv and (argv[0] == "index" or argv[0] == "context"
             or argv[0] == "session" and len(argv) > 1 and argv[1] == "probe"):
    sys.exit(0)
sid = None
harness = None
for i, a in enumerate(argv):
    if a == "--harness" and i + 1 < len(argv):
        harness = argv[i + 1]
    if a == "--session-id" and i + 1 < len(argv):
        sid = argv[i + 1]
if sid is None:
    sid = "fake-" + uuid.uuid4().hex[:8]
payload = {
    "session_id": sid,
    "harness": harness,
    "source": "verify",
    "conversation": [
        {"role": "user", "content": "probe turn one"},
        {"role": "assistant", "content": "probe turn two"},
    ],
}
req = Request(
    os.environ["TORTOISE_API_URL"].rstrip("/") + "/v1/sessions",
    data=json.dumps(payload).encode(),
    headers={"Authorization": "Bearer " + os.environ["TORTOISE_API_KEY"],
             "Content-Type": "application/json"},
    method="POST",
)
try:
    with urlopen(req, timeout=10) as resp:
        resp.read()
except Exception as exc:  # pragma: no cover - surfaces in the fired output
    print("fake tortoise capture failed: %r" % (exc,), file=sys.stderr)
    sys.exit(1)
sys.exit(0)
"""


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A hermetic install + fake CLI + loopback API, all under a temp HOME."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "tortoise"
    fake.write_text(_FAKE_TORTOISE, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    # Hermeticity: HOME is the temp home; the ambient CODEX_HOME (which would
    # move a Codex install to the real ~/.codex) is scrubbed.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}" + os.environ.get("PATH", ""))
    return home, bindir, fake


def _install(home: Path, harness: str, root: Path | None = None):
    if root is None:
        root = _root_for(home, harness)
    result = install_capture(harness, root=root, home=home)
    assert result.ok, result.error
    return root


def _root_for(home: Path, harness: str) -> Path:
    if harness == "claude":
        root = home / "proj"
        root.mkdir(exist_ok=True)
        return root
    return resolve_install_root(harness, home=home)


def _verify(hosted, home, harness, root, *, timeout=20.0, **kw):
    _graph, api_url = hosted
    return verify_session_capture(
        harness, api_key="tt_test", api_url=api_url, home=home,
        install_dir=root, timeout=timeout,
        env={**os.environ, "HOME": str(home),
             "TORTOISE_API_KEY": "tt_test", "TORTOISE_API_URL": api_url},
        **kw)


# ── the happy path ────────────────────────────────────────────────────────


def test_claude_chain_is_proven_and_the_probe_is_deleted(hosted, setup):
    """installed + captured + memory are all PROVEN, and the probe session is
    deleted (the task's no-orphan contract).

    Mutation: make the fake API server a no-op on DELETE — ``cleanup.deleted``
    is False and this REDs."""
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    report = _verify(hosted, home, "claude", root)
    graph, _url = hosted
    for link in ("installed", "captured", "memory"):
        assert report["links"][link]["status"] == "PROVEN", report["links"]
    assert report["cleanup"]["deleted"] is True
    assert report["session_id"] in graph.deletes
    assert report["session_id"].startswith("verify-claude-")


def test_codex_chain_is_proven_through_the_detaching_hook(hosted, setup):
    """The Codex seam detaches its worker; verify still observes the capture.

    Mutation: in the installed Codex hook, drop the worker hand-off so nothing
    fires — ``captured``/``memory`` never become retrievable and this REDs."""
    home, _bindir, _fake = setup
    root = _install(home, "codex")
    report = _verify(hosted, home, "codex", root)
    assert report["links"]["installed"]["status"] == "PROVEN"
    assert report["links"]["memory"]["status"] == "PROVEN"


# ── guards: each can RED on its named mutation ────────────────────────────


def test_guard_missing_registration_reds_installed(hosted, setup):
    """Mutation: delete the SessionEnd entry from ``.claude/settings.json`` —
    the loader finds no command for the event, so ``installed`` FAILs and the
    command exits 1."""
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    settings = root / ".claude" / "settings.json"
    data = json.loads(settings.read_text())
    data["hooks"].pop("SessionEnd", None)
    settings.write_text(json.dumps(data))
    report = _verify(hosted, home, "claude", root)
    assert report["exit_code"] == EXIT_BROKEN
    assert report["links"]["installed"]["status"] == "FAIL"
    assert "not current" in report["links"]["installed"]["detail"]


def test_guard_tampered_artifact_reds_installed(hosted, setup):
    """Mutation: locally edit the installed hook's bytes — the artifact is
    present and executable but no longer the shipped seam, so ``installed``
    FAILs on the drift detector."""
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    hook = root / ".claude" / "hooks" / "session-end.sh"
    hook.write_text(hook.read_text().replace(
        "set -euo pipefail", "set -euo pipefail\nexit 3", 1))
    report = _verify(hosted, home, "claude", root)
    assert report["exit_code"] == EXIT_BROKEN, report
    assert report["links"]["installed"]["status"] == "FAIL"
    assert "not current" in report["links"]["installed"]["detail"]


def test_guard_unexecutable_seam_reds_installed(hosted, setup, monkeypatch):
    """Mutation: the registered command cannot be executed at all
    (``subprocess.run`` raises ``OSError``) — the four on-disk checks pass,
    but the FIRING leg fails, so ``installed`` FAILs and nothing is captured."""
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    import tortoise.session_verify as sv

    def _boom(*_a, **_k):
        raise OSError("cannot exec (test)")

    monkeypatch.setattr(sv.subprocess, "run", _boom)
    graph, _url = hosted
    report = _verify(hosted, home, "claude", root)
    assert report["exit_code"] == EXIT_BROKEN, report
    assert report["links"]["installed"]["status"] == "FAIL"
    assert "cannot execute the seam" in report["links"]["installed"]["detail"]
    assert graph.posts == []


def test_guard_capture_refused_reds_captured(hosted, setup):
    """Mutation: the API refuses the capture POST (HTTP 500) — the seam fires
    but the receipt never advances and no session exists, so ``captured`` and
    ``memory`` FAIL."""
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    graph.fail_post = True
    report = _verify(hosted, home, "claude", root)
    assert report["exit_code"] == EXIT_BROKEN
    assert report["links"]["installed"]["status"] == "PROVEN"
    assert report["links"]["captured"]["status"] == "FAIL"
    assert report["links"]["memory"]["status"] == "FAIL"


def test_guard_wrong_turn_count_reds_captured(hosted, setup, monkeypatch):
    """Mutation: the API returns a session whose stored turn count does not
    match the fired payload — ``captured`` FAILs on the expectations, not on
    mere existence."""
    import tortoise.session_verify as sv

    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    monkeypatch.setattr(sv, "_probe_id", lambda _h: "verify-claude-fixed")
    graph.turn_override["verify-claude-fixed"] = 1
    report = _verify(hosted, home, "claude", root)
    assert report["links"]["captured"]["status"] == "FAIL", report
    assert "expected 2" in report["links"]["captured"]["detail"]
    assert report["exit_code"] == EXIT_BROKEN


def test_guard_receipt_not_advanced_reds_captured(hosted, setup):
    """Mutation: the capture stores the session but the server never advances
    the per-harness receipt — ``captured`` FAILs on the receipt leg even though
    the session is retrievable with the expected turns."""
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    graph.no_receipt = True
    report = _verify(hosted, home, "claude", root)
    assert report["exit_code"] == EXIT_BROKEN, report
    assert report["links"]["captured"]["status"] == "FAIL"
    assert "did not advance" in report["links"]["captured"]["detail"]


def test_guard_missing_source_node_reds_memory(hosted, setup):
    """Mutation: the capture materializes no Source node (``source: null``) —
    ``memory`` FAILs even though extraction produced a Point."""
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    graph.no_source = True
    report = _verify(hosted, home, "claude", root)
    assert report["exit_code"] == EXIT_BROKEN
    assert report["links"]["captured"]["status"] == "PROVEN"
    assert report["links"]["memory"]["status"] == "FAIL"
    assert "no Source node" in report["links"]["memory"]["detail"]


def test_guard_source_field_absent_is_unverifiable(hosted, setup):
    """Mutation: the API build predates the ``source`` field — the memory link
    reports UNVERIFIABLE-IN-CI (the source could not be observed), NEVER a
    fabricated PROVEN."""
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    graph.omit_source_field = True
    report = _verify(hosted, home, "claude", root)
    assert report["links"]["memory"]["status"] == "UNVERIFIABLE-IN-CI"
    assert "does not expose the session Source" in \
        report["links"]["memory"]["detail"]
    assert report["exit_code"] == EXIT_UNVERIFIABLE


def test_guard_zero_extraction_reds_memory(hosted, setup):
    """Mutation: the Source exists but extraction produced no memory Point —
    ``memory`` FAILs on the extraction leg."""
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    graph.zero_extracted = True
    report = _verify(hosted, home, "claude", root)
    assert report["exit_code"] == EXIT_BROKEN
    assert report["links"]["memory"]["status"] == "FAIL"
    assert "no memory Point" in report["links"]["memory"]["detail"]


def test_guard_cleanup_failure_is_surfaced_not_swallowed(hosted, setup):
    """Mutation: DELETE refuses — the chain is proven but the probe session is
    left behind; the command must exit non-zero and say so."""
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    graph.fail_delete = True
    report = _verify(hosted, home, "claude", root)
    assert report["links"]["captured"]["status"] == "PROVEN"
    assert report["cleanup"]["error"] is True
    assert report["exit_code"] == EXIT_BROKEN


def test_guard_missing_install_reds_before_any_write(hosted, setup):
    """Mutation: verify an empty root — no seam is present, so ``installed``
    FAILs and NOTHING is fired (no POST reaches the API)."""
    home, _bindir, _fake = setup
    root = home / "empty"
    root.mkdir()
    report = _verify(hosted, home, "claude", root)
    graph, _url = hosted
    assert report["exit_code"] == EXIT_BROKEN
    assert report["links"]["installed"]["status"] == "FAIL"
    assert report["links"]["captured"]["status"] == "FAIL"
    assert graph.posts == [], "nothing may be captured for a missing install"


# ── honest disclosure: cursor + pi ────────────────────────────────────────


def test_cursor_is_honestly_unverifiable(hosted, setup):
    """Cursor's ``sessionEnd`` is IDE-only: the seam is present+registered but
    cannot be fired headlessly, so every link reports UNVERIFIABLE-IN-CI with
    the reason and the exit code is 2 (not a pass, not a false FAIL)."""
    home, _bindir, _fake = setup
    root = _install(home, "cursor")
    report = _verify(hosted, home, "cursor", root)
    assert report["exit_code"] == EXIT_UNVERIFIABLE
    for link in ("installed", "captured", "memory"):
        assert report["links"][link]["status"] == "UNVERIFIABLE-IN-CI"
    assert "IDE-only" in report["links"]["installed"]["detail"]


def test_pi_is_honestly_unverifiable(hosted, setup):
    """Pi's seam is an in-process TypeScript extension — not script-firable.
    Installed is UNVERIFIABLE (present, not fired), never a fabricated pass."""
    home, _bindir, _fake = setup
    root = _install(home, "pi")
    report = _verify(hosted, home, "pi", root)
    assert report["exit_code"] == EXIT_UNVERIFIABLE
    assert report["links"]["installed"]["status"] == "UNVERIFIABLE-IN-CI"
    assert "extension" in report["links"]["installed"]["detail"]


# ── hermeticity: root resolution is home-scoped, env only where one exists ──


def test_install_root_is_home_scoped_and_cursor_has_no_env_override(
        tmp_path, monkeypatch):
    """The property, observed (not a sentinel): with an explicit temp home,
    Codex's root is ``$CODEX_HOME`` when set and ``<home>/.codex`` otherwise;
    Cursor's is ``<home>/.cursor`` EVEN WITH ``CURSOR_HOME`` set (Cursor has no
    such variable), so no ambient env can move a Cursor install to the live
    ``~/.cursor``.

    Mutation: make ``resolve_install_root`` read a generic ``*_HOME`` env var
    for Cursor — the ``CURSOR_HOME`` leg reads the evil path and this REDs.
    """
    home = tmp_path / "home"
    home.mkdir()
    live_codex = Path.home() / ".codex"
    live_cursor = Path.home() / ".cursor"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-elsewhere"))
    assert resolve_install_root("codex", home=home) == tmp_path / "codex-elsewhere"
    monkeypatch.delenv("CODEX_HOME")
    assert resolve_install_root("codex", home=home) == home / ".codex"
    # An ambient CURSOR_HOME pointing at the LIVE ~/.cursor must NOT move a
    # temp-home verification there — Cursor has no such variable, so the
    # resolved root stays under the injected home.
    monkeypatch.setenv("CURSOR_HOME", str(live_cursor))
    assert resolve_install_root("cursor", home=home) == home / ".cursor"
    assert resolve_install_root("cursor", home=home) != live_cursor
    assert resolve_install_root("codex", home=home) != live_codex


def test_probe_session_id_is_unmistakable(hosted, setup):
    """The probe id is ``verify-<harness>-<timestamp>`` — unmistakable in a
    real graph, so a leaked probe is auditable."""
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    report = _verify(hosted, home, "claude", root)
    sid = report["session_id"]
    assert sid.startswith("verify-claude-")
    assert len(sid) > len("verify-claude-")


def test_keep_leaves_the_probe_and_says_so(hosted, setup):
    """``--keep`` is an intentional hold, reported — never a silent leak."""
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    report = _verify(hosted, home, "claude", root, keep=True)
    graph, _url = hosted
    assert report["cleanup"]["kept"] is True
    assert graph.deletes == []
