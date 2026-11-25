"""#14607 — the MCP must load, and a failure must be a NAMED, LOUD verdict.

The defect these tests exist for is not "a config was wrong". It is that a `core`
MCP server sat down **silently** while the only message that surfaced named the
wrong layer (`-32001 invalid or missing Bearer token` for a client that had no
credential, because `${TORTOISE_API_KEY}` expanded to `""`), so an agent working
inside this repo gave up on the product and hand-rolled HTTP instead.

So there are three properties, and each has its own arm here:

1. **The class names the layer** — config, interpolation, spawn, credential,
   transport, auth — and never collapses them (an unreachable endpoint reported as
   an auth problem sends the reader to the wrong file).
2. **An unprobed config is never green** — absence of evidence is an abstention
   (exit 3), not a pass.
3. **The installer's writes are visible, idempotent and reversible**, and it
   refuses to write "reads go through the MCP" while the MCP is down.
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tortoise import harness_onboard, mcp_selfcheck


@pytest.fixture(autouse=True)
def sandbox_home(tmp_path, monkeypatch):
    """Never read (or write) the operator's REAL home during these tests.

    `Path.home()` follows `$HOME` on this platform, and the verifier legitimately
    reads the USER-scope config as well as the project one — so an unsandboxed
    test would classify this very machine's fleet config, and the installer's
    `user` scope could write into it.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


def _write_config(project: Path, entry: dict) -> Path:
    path = project / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": {"tortoise": entry}}, indent=2),
                    encoding="utf-8")
    return path


# ── 1 · the class names the layer ────────────────────────────────────────

def test_env_unset_entry_is_the_capture_class(project, monkeypatch):
    """The -32001 root cause, as a class: an `${VAR}` that expands to nothing."""
    monkeypatch.delenv("TORTOISE_TEST_ABSENT_KEY", raising=False)
    _write_config(project, {
        "type": "http",
        "url": "https://api.example.invalid/mcp/",
        "headers": {"Authorization": "Bearer ${TORTOISE_TEST_ABSENT_KEY}"},
    })
    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status == mcp_selfcheck.ENV_UNSET
    assert verdict.exit_code == 1
    # The message must name the mechanism AND the server's only possible answer,
    # because that pairing is what tells the reader the fault is the capture.
    assert "$TORTOISE_TEST_ABSENT_KEY" in verdict.line()
    assert "-32001" in verdict.line()
    assert verdict.source.endswith(".mcp.json")


def test_entry_absent_names_every_file_it_read(project):
    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status == mcp_selfcheck.ENTRY_ABSENT
    assert "read:" in verdict.detail


def test_stdio_command_that_does_not_exist_is_its_own_class(project):
    _write_config(project, {
        "type": "stdio",
        "command": "/nonexistent/14607/python3",
        "args": ["-m", "tortoise.mcp_bridge"],
    })
    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status == mcp_selfcheck.COMMAND_MISSING
    assert "/nonexistent/14607/python3" in verdict.detail


def test_missing_cwd_is_its_own_class(project):
    _write_config(project, {
        "type": "stdio",
        "command": "python3",
        "args": ["-m", "tortoise.mcp_bridge"],
        "cwd": "/nonexistent/14607/workdir",
    })
    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status == mcp_selfcheck.CWD_MISSING


def test_interpreter_without_tortoise_importable_is_its_own_class(project):
    """A command that EXISTS and whose module does not import: a distinct layer.

    This is why the written entry names a portable `python3` instead of the
    installer's own virtualenv path — and why the resulting failure had to be
    given a name rather than reported as "command missing".
    """
    _write_config(project, {
        "type": "stdio",
        "command": "/bin/false",
        "args": ["-m", "tortoise.mcp_bridge"],
    })
    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status == mcp_selfcheck.PYTHON_LACKS_TORTOISE
    assert "spawn SUCCEEDS" in verdict.detail


def test_http_entry_with_no_credential_anywhere_is_no_credential(
        project, monkeypatch):
    for name in mcp_selfcheck.CREDENTIAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(mcp_selfcheck, "CREDENTIAL_FILES", ())
    _write_config(project, {"type": "http", "url": "https://api.example.invalid/mcp/"})
    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status == mcp_selfcheck.NO_CREDENTIAL


# ── 2 · an unprobed config is never green ────────────────────────────────

def test_an_unprobed_entry_is_an_abstention_never_ok(project):
    _write_config(project, {
        "type": "stdio",
        "command": "python3",
        "args": ["-m", "tortoise.mcp_bridge"],
        "env": {"TORTOISE_MCP_URL": "http://127.0.0.1:1/mcp"},
    })
    verdict = mcp_selfcheck.check("pi", project_dir=project, live=False)
    assert verdict.status == mcp_selfcheck.RESOLVES_UNPROBED
    assert not verdict.ok
    assert verdict.exit_code == 3, "an abstention must not exit 0"
    assert verdict.line().startswith("Tortoise MCP: UNVERIFIED")


# ── 3 · the live probe separates transport from auth ─────────────────────

class _Refusing(BaseHTTPRequestHandler):
    """An endpoint that answers 401 for everything — the auth-rejected layer."""

    def do_POST(self):
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {
            "code": -32001,
            "message": "Unauthorized: invalid or missing Bearer token."}},
        ).encode())

    def log_message(self, *_args):      # silence the server's own stderr chatter
        pass


@pytest.fixture()
def refusing_endpoint():
    server = HTTPServer(("127.0.0.1", 0), _Refusing)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp"
    finally:
        server.shutdown()
        server.server_close()


def _dead_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()                        # closed: nothing listens there
    return port


def test_broken_token_is_a_named_loud_failure(refusing_endpoint, monkeypatch):
    """A deliberately-broken credential must be LOUD and SPECIFIC (#14607 §A).

    Not "an endpoint said something": the class is `auth-rejected`, the line says
    the credential was REFUSED, and the exit code is non-zero.
    """
    monkeypatch.setenv("TORTOISE_MCP_URL", refusing_endpoint)
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_deliberately-broken-14607")
    verdict = mcp_selfcheck.probe()
    assert verdict.status == mcp_selfcheck.AUTH_REJECTED, verdict.line()
    assert verdict.exit_code == 1
    assert not verdict.ok
    line = verdict.line()
    assert "DOWN [auth-rejected]" in line
    assert "REFUSED" in line
    assert "tt_deliberately-broken-14607" not in line, (
        "the verdict must never echo the credential")


def test_unreachable_endpoint_is_a_transport_class_not_an_auth_verdict(
        monkeypatch):
    """The inversion this module exists to prevent: an outage read as a key fault."""
    monkeypatch.setenv("TORTOISE_MCP_URL", f"http://127.0.0.1:{_dead_port()}/mcp")
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_present-but-unreachable-14607")
    verdict = mcp_selfcheck.probe()
    assert verdict.status == mcp_selfcheck.UNREACHABLE, verdict.line()
    assert "TRANSPORT failure, not a credential one" in verdict.line()


def test_probe_keeps_the_entry_transport_label(project, monkeypatch):
    """A stdio entry is a bridge to HTTP; the verdict describes what the HARNESS loads."""
    _write_config(project, {
        "type": "stdio",
        "command": "python3",
        "args": ["-m", "tortoise.mcp_bridge"],
    })
    monkeypatch.setenv("TORTOISE_MCP_URL", f"http://127.0.0.1:{_dead_port()}/mcp")
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_x-14607")
    verdict = mcp_selfcheck.check("pi", project_dir=project, live=True)
    assert verdict.transport == "stdio"
    assert verdict.status == mcp_selfcheck.UNREACHABLE


def test_every_class_in_the_set_is_rendered_distinctly():
    """No two classes may render the same way — a collapsed set is a silent failure."""
    lines = {
        cls: mcp_selfcheck.Verdict(status=cls, detail="d").line()
        for cls in mcp_selfcheck.CLASSES
    }
    assert len(set(lines.values())) == len(mcp_selfcheck.CLASSES)


# ── 4 · the installer: credential-free entry, visible, idempotent, reversible ──

def test_installer_writes_a_credential_free_bridge_entry(project):
    result = harness_onboard.write_config("pi", project)
    assert result.wrote, "the first install must write"
    assert result.diffs, "the diff is shown"
    assert "tortoise.mcp_bridge" in result.diffs[0]
    assert "${" not in result.diffs[0], (
        "the diff must show a credential-FREE entry — an interpolated ${VAR} is "
        "the #14607 capture")
    entry = json.loads((project / ".mcp.json").read_text())["mcpServers"]["tortoise"]
    assert entry["args"] == ["-m", "tortoise.mcp_bridge"]
    assert "headers" not in entry
    assert "${" not in json.dumps(entry), (
        "the written entry must interpolate NOTHING — that is the #14607 fix")
    assert entry["env"]["TORTOISE_MCP_URL"] == harness_onboard.DEFAULT_MCP_URL


def test_installer_is_idempotent(project):
    harness_onboard.write_config("pi", project)
    before = (project / ".mcp.json").read_text()
    second = harness_onboard.write_config("pi", project)
    assert not second.wrote and not second.diffs
    assert (project / ".mcp.json").read_text() == before


def test_installer_preserves_the_rest_of_the_file_byte_for_byte(project):
    """A merge, not a rewrite: unrelated entries and their formatting survive."""
    original = (
        '{\n'
        '  "mcpServers": {\n'
        '    "other": {"command": "npx", "purpose": "keep my \\u2014 escaping"},\n'
        '    "tortoise": {"type": "http", "url": "https://api.example.invalid/mcp/"}\n'
        '  }\n'
        '}\n'
    )
    path = project / ".mcp.json"
    path.write_text(original, encoding="utf-8")
    harness_onboard.write_config("pi", project)
    after = path.read_text(encoding="utf-8")
    assert '"keep my \\u2014 escaping"' in after, (
        "an unrelated entry was reformatted — the installer must splice, not dump")
    assert "tortoise.mcp_bridge" in after


def test_installer_refuses_an_unparseable_config(project):
    path = project / ".mcp.json"
    path.write_text("{ not json", encoding="utf-8")
    result = harness_onboard.write_config("pi", project)
    assert not result.wrote
    assert any("refusing" in r for r in result.refusals)
    assert path.read_text(encoding="utf-8") == "{ not json", "it must not clobber"


def test_instruction_block_is_written_once_and_is_reversible(project):
    target = project / "AGENTS.md"
    target.write_text("# House rules\n\nKeep this line.\n", encoding="utf-8")

    first = harness_onboard.write_instructions("pi", project)
    assert first.wrote and first.diffs
    text = target.read_text(encoding="utf-8")
    assert text.count(harness_onboard.BEGIN_MARK) == 1
    assert "Keep this line." in text, "the user's own text must survive"
    assert "Reads go through the Tortoise MCP" in text
    assert "product defect" in text

    second = harness_onboard.write_instructions("pi", project)
    assert not second.wrote, "a second run must write nothing"
    assert target.read_text(encoding="utf-8") == text

    harness_onboard.remove_instructions("pi", project)
    restored = target.read_text(encoding="utf-8")
    assert harness_onboard.BEGIN_MARK not in restored
    assert "Keep this line." in restored
    assert "Reads go through the Tortoise MCP" not in restored


def test_instructions_are_refused_while_the_mcp_is_down(project, monkeypatch):
    """The AX point: never instruct an agent to use a door that does not open."""
    monkeypatch.setenv("TORTOISE_MCP_URL", f"http://127.0.0.1:{_dead_port()}/mcp")
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_x-14607")
    result = harness_onboard.onboard("pi", project_dir=project, hooks=False)
    assert not result.ok
    assert not (project / "AGENTS.md").exists()
    assert any("NOT written" in r for r in result.refusals)


def test_a_dry_run_shows_the_instruction_diff_without_writing(project, monkeypatch):
    """Acceptance #C: the diff is SHOWN — a dry run is how a user asks for it."""
    monkeypatch.setenv("TORTOISE_MCP_URL", f"http://127.0.0.1:{_dead_port()}/mcp")
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_x-14607")
    result = harness_onboard.onboard("pi", project_dir=project, hooks=False,
                                     dry_run=True)
    assert result.diffs, "a dry run must still produce the diff"
    assert not (project / "AGENTS.md").exists()
    assert not (project / ".mcp.json").exists()
    assert any("SHOWN ONLY" in r for r in result.refusals)


def test_onboard_writes_instructions_only_after_verification(project, monkeypatch):
    """The green path: a verified endpoint installs the sentences."""
    server = HTTPServer(("127.0.0.1", 0), _Ok)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("TORTOISE_MCP_URL",
                           f"http://127.0.0.1:{server.server_port}/mcp")
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_x-14607")
        result = harness_onboard.onboard("pi", project_dir=project, hooks=False)
    finally:
        server.shutdown()
        server.server_close()
    assert result.ok, result.verdict.line() if result.verdict else "no verdict"
    assert (project / "AGENTS.md").exists()
    assert harness_onboard.BEGIN_MARK in (project / "AGENTS.md").read_text()


class _Ok(BaseHTTPRequestHandler):
    """A minimal endpoint that answers an MCP `initialize` + `tools/list`."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        method = payload.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05",
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "tortoise", "version": "test"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "tortoise_health",
                                 "description": "health",
                                 "inputSchema": {"type": "object"}}]}
        else:
            result = {}
        body = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"),
                           "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass
