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
import sys
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


def test_interpreter_without_tortoise_importable_is_its_own_class(project, tmp_path):
    """A command that EXISTS and whose module does not import: a distinct layer.

    This is why the written entry names a portable `python3` instead of the
    installer's own virtualenv path — and why the resulting failure had to be
    given a name rather than reported as "command missing".

    ⛔ The command must actually BE a Python interpreter, by name: `/bin/false -c
    "import x"` also exits non-zero, but calling that `python-lacks-tortoise`
    describes the wrong program. `_looks_like_python` now gates the probe, so the
    stub below is named `python3` and refuses the import the way an interpreter
    without the package does.
    """
    fake = tmp_path / "python3"
    fake.write_text(
        "#!/bin/sh\n"
        'echo "ModuleNotFoundError: No module named \'tortoise\'" >&2\n'
        "exit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    _write_config(project, {
        "type": "stdio",
        "command": str(fake),
        "args": ["-m", "tortoise.mcp_bridge"],
    })
    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status == mcp_selfcheck.PYTHON_LACKS_TORTOISE, verdict.line()
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


# ── 5 · a nested `${A:-${B}}` default is expanded with BRACE BALANCE ─────
#
# Both arms below red on the `[^}]*` group this used to use, and the second is
# the one that matters: the classifier named the WRONG LAYER on this repo's own
# recorded config shape (`docs/evidence/14607-mcp-not-loaded.md`), sending the
# reader to replace an interpreter when the fault was an unset variable.

def test_a_nested_default_expands_instead_of_leaking_its_braces(monkeypatch):
    """`${A:-${B}/x}` must resolve to a real path, and must report what it lost."""
    monkeypatch.delenv("TORTOISE_HOME_14607", raising=False)
    value, unset = mcp_selfcheck._expand(
        "${TORTOISE_HOME_14607:-${HOME}/Documents/GitHub/tortoise}")
    assert value == f"{Path.home()}/Documents/GitHub/tortoise"
    assert "${" not in value, f"the expansion leaked an unexpanded expression: {value!r}"
    # A usable default is not a lost variable: the entry CAN be built.
    assert unset == []


def test_a_nested_default_with_nothing_set_names_every_lost_variable(monkeypatch):
    """The unset list is the payload — a nested default must not swallow the name."""
    monkeypatch.delenv("OUTER_14607", raising=False)
    monkeypatch.delenv("INNER_14607", raising=False)
    value, unset = mcp_selfcheck._expand("${OUTER_14607:-${INNER_14607}}")
    assert value == ""
    assert unset == ["INNER_14607", "OUTER_14607"]


def test_the_repos_own_nested_config_shape_is_not_diagnosed_as_a_bad_interpreter(
        project, monkeypatch):
    """The defect, end to end: a MANGLED expression must never reach the verdict."""
    monkeypatch.delenv("TORTOISE_HOME_14607", raising=False)
    _write_config(project, {
        "type": "stdio",
        "command": "${TORTOISE_HOME_14607:-${HOME}/not-here-14607/.venv/bin/python3}",
        "args": ["-m", "tortoise.mcp_bridge"],
    })
    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status == mcp_selfcheck.COMMAND_MISSING
    assert "${" not in verdict.detail, (
        "the verdict quotes an unexpanded/mangled expression — it is reasoning "
        f"about a string that is not the path: {verdict.detail!r}")
    assert f"{Path.home()}/not-here-14607" in verdict.detail, (
        "the verdict must name the path it actually checked")


# ── 6 · the probe grades the endpoint the ENTRY names ────────────────────
#
# `tortoise onboard --url http://localhost:8000/mcp` writes the target into the
# entry's `env`, while `mcp_client.mcp_url()` reads the CALLING SHELL. Grading the
# shell's endpoint is wrong in both directions: OK while the entry points at a
# dead local endpoint (a FALSE PASS in the verifier), and a transport/auth verdict
# about a host the harness never contacts.

def test_the_probe_grades_the_endpoint_the_entry_names(
        project, refusing_endpoint, monkeypatch):
    """A dead endpoint in the ENTRY wins over a LIVE one in the shell.

    The shell's endpoint here is a real server that REFUSES the credential, so
    grading it would produce `auth-rejected`. The entry's endpoint is closed, so
    `endpoint-unreachable` is the verdict only when the ENTRY was graded.
    """
    dead = f"http://127.0.0.1:{_dead_port()}/mcp"
    _write_config(project, {
        "type": "stdio",
        "command": "python3",
        "args": ["-m", "tortoise.mcp_bridge"],
        "env": {"TORTOISE_MCP_URL": dead},
    })
    monkeypatch.setenv("TORTOISE_MCP_URL", refusing_endpoint)
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_present-but-wrong-endpoint-14607")
    verdict = mcp_selfcheck.check("pi", project_dir=project, live=True)
    assert verdict.status == mcp_selfcheck.UNREACHABLE, verdict.line()
    assert dead in verdict.line(), (
        "the verdict must say WHICH endpoint it graded — otherwise a green is "
        f"about an unnamed host: {verdict.line()!r}")


def test_declared_endpoint_prefers_the_entry_env_over_the_top_level_url(project):
    """`onboard` writes `env.TORTOISE_MCP_URL`; that is the bridge's real target."""
    _write_config(project, {
        "type": "http",
        "url": "https://top-level-14607.invalid/mcp",
        "env": {"TORTOISE_MCP_URL": "https://env-wins-14607.invalid/mcp"},
    })
    assert (mcp_selfcheck.declared_endpoint("pi", project_dir=project)
            == "https://env-wins-14607.invalid/mcp")


def test_declared_endpoint_is_none_for_a_bare_stdio_entry(project):
    """No declared endpoint ⇒ the client's own resolution is the right answer."""
    _write_config(project, {
        "type": "stdio", "command": "python3",
        "args": ["-m", "tortoise.mcp_bridge"],
    })
    assert mcp_selfcheck.declared_endpoint("pi", project_dir=project) is None


# ── 7 · the probe runs under the CHILD'S environment, not the shell's ────
#
# The installed shape is: the harness spawns `python3 -m tortoise.mcp_bridge`
# with the entry's `env` MERGED over its own environment (MCP stdio transport),
# so the child resolves endpoint AND credential from the entry's
# `TORTOISE_MCP_URL` and they agree. A probe that grades the entry's URL while
# the CLIENT resolves the credential from the operator's shell manufactures a
# co-source conflict the child never has, withholds the bearer, and reports
# `DOWN` about `localhost:8000` — a host the entry never names — for a config
# that works. That is the DEFAULT install on any box with `TORTOISE_API_KEY`
# exported, i.e. the one path this change is for.

RIGHT_KEY_14607 = "tt_right_key_14607"


class _AuthOk(BaseHTTPRequestHandler):
    """An endpoint that answers MCP only for `Bearer <RIGHT_KEY_14607>`."""

    def _refuse(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "id": None, "error": {
            "code": -32001,
            "message": "Unauthorized: invalid or missing Bearer token."}},
        ).encode()
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.headers.get("Authorization") != f"Bearer {RIGHT_KEY_14607}":
            return self._refuse()
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


@pytest.fixture()
def auth_endpoint():
    server = HTTPServer(("127.0.0.1", 0), _AuthOk)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp"
    finally:
        server.shutdown()
        server.server_close()


class _NoTools(BaseHTTPRequestHandler):
    """An endpoint that completes the MCP handshake and advertises ZERO tools."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        method = payload.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05",
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "tortoise", "version": "test"}}
        elif method == "tools/list":
            result = {"tools": []}
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


@pytest.fixture()
def tool_less_endpoint():
    server = HTTPServer(("127.0.0.1", 0), _NoTools)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp"
    finally:
        server.shutdown()
        server.server_close()


class _ForeignTools(BaseHTTPRequestHandler):
    """An endpoint that completes the handshake and advertises SOMEBODY ELSE's tool."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        method = payload.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05",
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "not-tortoise", "version": "test"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "foo_bar",
                                 "description": "somebody else's tool",
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


@pytest.fixture()
def foreign_tool_endpoint():
    server = HTTPServer(("127.0.0.1", 0), _ForeignTools)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp"
    finally:
        server.shutdown()
        server.server_close()


def test_a_handshake_with_zero_tools_is_a_failure_not_a_pass(
        tool_less_endpoint, monkeypatch):
    """⛔ THE SYMPTOM ARRIVING THROUGH A HEALTHY TRANSPORT.

    `initialize` and `tools/list` both succeed; the list is EMPTY. The harness
    then loads a server exposing no `tortoise_*` tool — issue #14607's own
    symptom — while every earlier layer (transport, credential, handshake) tells
    the truth. Grading that `OK` is the fail-open this module exists to remove, so
    the verdict is a DOWN with a non-zero exit, and its class is `server-error`:
    the endpoint ANSWERED, with something unusable.
    """
    monkeypatch.setenv("TORTOISE_MCP_URL", tool_less_endpoint)
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_present-but-useless-14607")
    verdict = mcp_selfcheck.probe()
    assert verdict.status == mcp_selfcheck.SERVER_ERROR, verdict.line()
    assert not verdict.ok
    assert verdict.exit_code == 1
    assert "0 tool(s)" in verdict.line(), (
        "the verdict must name what was found, not just which class it is")
    assert "DOWN [server-error]" in verdict.line()


def test_a_server_advertising_someone_elses_tools_is_not_a_pass(
        foreign_tool_endpoint, monkeypatch):
    """THE SAME FAIL-OPEN, ONE CASE OVER: the count is not the surface.

    A server that completes the handshake and advertises tools that are NOT
    `tortoise_*` leaves the agent with exactly the symptom #14607 is about — no
    `tortoise_*` tool to call — while a count-based verdict says `OK — 1 tools
    registered` and `onboard` writes the two "reads go through the MCP" sentences
    on it. The product registers every MCP tool behind the `tortoise_` prefix
    (`TOOL_REGISTRY`), so that prefix is what the probe grades.
    """
    monkeypatch.setenv("TORTOISE_MCP_URL", foreign_tool_endpoint)
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_present-but-foreign-14607")
    verdict = mcp_selfcheck.probe()
    assert verdict.status == mcp_selfcheck.SERVER_ERROR, verdict.line()
    assert not verdict.ok
    assert verdict.exit_code == 1
    assert "tortoise_*" in verdict.line(), (
        "the verdict must say what is missing, by name: " + verdict.line())


def test_the_default_install_verifies_although_the_shell_declares_no_endpoint(
        project, auth_endpoint, monkeypatch):
    """⛔ THE SHAPE THE PR IS FOR: entry names a live endpoint, shell names none.

    Pre-fix this reported `DOWN [server-error]` / `MCP-CREDENTIAL-HOST-MISMATCH`
    about `http://localhost:8000/mcp`: `_BearerProvider` resolved the credential
    from the SHELL (which had no `TORTOISE_MCP_URL`, so the built-in default) while
    the transport dialled the ENTRY's endpoint, so the two disagreed and the
    bearer was withheld. The entry's own `env` is the child's environment, so both
    halves must resolve from it — as this asserts by requiring a live `OK` from an
    endpoint that refuses anything but the exact bearer.
    """
    monkeypatch.delenv("TORTOISE_MCP_URL", raising=False)
    monkeypatch.setenv("TORTOISE_API_KEY", RIGHT_KEY_14607)
    _write_config(project, {
        "type": "stdio",
        "command": "python3",
        "args": ["-m", "tortoise.mcp_bridge"],
        "env": {"TORTOISE_MCP_URL": auth_endpoint},
    })
    verdict = mcp_selfcheck.check("pi", project_dir=project, live=True)
    assert verdict.ok, (
        "the entry's endpoint is live and the key is the one it accepts, so this "
        "must verify — a DOWN here is about a host the entry never names:\n"
        + verdict.line())
    assert "localhost:8000" not in verdict.line(), (
        "the verdict must never name an endpoint the entry does not declare")


def test_the_probe_restores_the_shell_endpoint_it_borrowed(
        project, auth_endpoint, monkeypatch):
    """The child-environment window must not leak into the caller's process."""
    monkeypatch.setenv("TORTOISE_API_KEY", RIGHT_KEY_14607)
    monkeypatch.setenv("TORTOISE_MCP_URL", "http://shell-14607.invalid/mcp")
    _write_config(project, {
        "type": "stdio", "command": "python3",
        "args": ["-m", "tortoise.mcp_bridge"],
        "env": {"TORTOISE_MCP_URL": auth_endpoint},
    })
    mcp_selfcheck.check("pi", project_dir=project, live=True)
    import os
    assert os.environ.get("TORTOISE_MCP_URL") == "http://shell-14607.invalid/mcp", (
        "the probe left the CHILD's endpoint in this process's environment")


# ── 8 · a nested object that happens to be called `tortoise` ─────────────
#
# `_splice_entry` located the entry with an unanchored regex, so the FIRST
# `"tortoise":` ANYWHERE won — an unrelated object (a profile, a per-project
# entry) was overwritten with the server entry, the harness still had no
# `tortoise` server, and `write_config` reported the file as updated.

def test_an_unrelated_object_named_tortoise_is_never_rewritten(project):
    path = project / ".mcp.json"
    path.write_text(json.dumps({
        "profiles": {"tortoise": {"enabled": False, "note": "my personal profile"}},
        "mcpServers": {"other": {"command": "x"}},
    }, indent=2) + "\n", encoding="utf-8")

    result = harness_onboard.write_config("pi", project)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["profiles"]["tortoise"] == {
        "enabled": False, "note": "my personal profile"}, (
        "an unrelated `tortoise` object was overwritten with the server entry")
    assert written["mcpServers"]["tortoise"]["args"] == [
        "-m", "tortoise.mcp_bridge"], (
        "the entry must land in `mcpServers` — reporting a write while the harness "
        "still has no tortoise server is the silent-failure class")
    assert written["mcpServers"]["other"] == {"command": "x"}, (
        "a sibling server must survive the merge")
    assert result.wrote, "the entry WAS added, so the write must be reported"


def test_the_splice_targets_mcpservers_when_the_name_appears_twice(project):
    """Both objects hold `tortoise`; only the `mcpServers` one may move."""
    path = project / ".mcp.json"
    path.write_text(json.dumps({
        "mcpServers": {"tortoise": {"type": "http", "url": "https://old/mcp",
                                    "headers": {"Authorization": "Bearer tt_x"}}},
        "profiles": {"tortoise": {"enabled": False}},
    }, indent=2) + "\n", encoding="utf-8")

    harness_onboard.write_config("pi", project)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["mcpServers"]["tortoise"]["args"] == [
        "-m", "tortoise.mcp_bridge"]
    assert "headers" not in written["mcpServers"]["tortoise"], (
        "the credential-carrying entry must be replaced, not merged into")
    assert written["profiles"]["tortoise"] == {"enabled": False}


# ── 9 · a TOML harness is REFUSED by name, never written as JSON ─────────
#
# `--harness codex` wrote a JSON document into `~/.codex/config.toml`, which
# Codex cannot parse — so the install did nothing while `wrote:` said the file
# was updated — and, in the other direction, told an existing Codex user their
# valid TOML was "not valid JSON".

def test_codex_is_refused_by_name_and_no_file_is_written(project):
    result = harness_onboard.write_config("codex", project, scope="user")
    target = Path(harness_onboard.Path.home()) / ".codex" / "config.toml"
    assert not result.wrote, "a TOML harness must never be written by this installer"
    assert not target.exists(), "a file appeared at a .toml path that we cannot write"
    joined = "\n".join(result.refusals)
    assert "TOML" in joined, joined
    assert "[mcp_servers.tortoise]" in joined, (
        "the refusal must hand the reader the table to paste — refusing without "
        f"a remedy leaves them stuck: {joined}")


def test_an_existing_codex_config_is_not_called_broken_json(project):
    target = Path(harness_onboard.Path.home()) / ".codex"
    target.mkdir(parents=True)
    config = target / "config.toml"
    config.write_text('[mcp_servers.tortoise]\ncommand = "python3"\n',
                      encoding="utf-8")
    result = harness_onboard.write_config("codex", project, scope="user")
    joined = "\n".join(result.refusals)
    assert "not valid JSON" not in joined, (
        "valid TOML reported as broken JSON sends the user to fix a file that is "
        f"fine: {joined}")
    assert config.read_text(encoding="utf-8").startswith("[mcp_servers.tortoise]"), (
        "the user's own config must be left byte-identical")


# ── 10 · `--uninstall` is VISIBLE ────────────────────────────────────────
#
# The reversal EDITS the user's `AGENTS.md`. Forwarding only `.refusals` printed
# nothing while the file changed, and the CLI returned 0 before any verdict — the
# file's own first AX rule ("its diff is PRINTED") inverted on the arm that has
# no test.

def test_uninstall_prints_the_diff_it_applied(project):
    import io
    harness_onboard.write_instructions("pi", project)
    assert harness_onboard.BEGIN_MARK in (project / "AGENTS.md").read_text()

    result = harness_onboard.onboard("pi", project_dir=project, uninstall=True)
    after = (project / "AGENTS.md").read_text()
    assert harness_onboard.BEGIN_MARK not in after, "the block was not removed"
    assert result.diffs, "the file changed, so the change must be shown"
    buffer = io.StringIO()
    harness_onboard.render(result, out=buffer)
    assert buffer.getvalue().strip(), (
        "`--uninstall` rendered NOTHING while editing the user's AGENTS.md")
    assert harness_onboard.BEGIN_MARK in buffer.getvalue(), (
        "the rendered diff must be the one that was applied")


def test_uninstall_says_so_when_there_is_nothing_to_remove(project):
    (project / "AGENTS.md").write_text("# House rules\n", encoding="utf-8")
    result = harness_onboard.onboard("pi", project_dir=project, uninstall=True)
    assert not result.diffs
    assert result.actions, (
        "a silent exit 0 leaves the user unable to tell 'removed' from "
        "'nothing was there'")
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == "# House rules\n"


# ── 11 · `credentials_present` reads `./.tortoise` ───────────────────────
#
# `rel.lstrip("./")` strips a CHARACTER SET, not a prefix, so the first store
# became `tortoise` — a self-hoster keeping the key in the project-scope store
# (the first source this module's own docstring names) was told `no-credential`
# with the key sitting in the file it was supposed to read.

def test_the_project_scope_store_is_read_at_the_project_path(project, monkeypatch):
    monkeypatch.chdir(project)
    (project / ".tortoise").write_text(
        json.dumps({"api_key": "tt_project_store_14607"}), encoding="utf-8")
    found = mcp_selfcheck.credentials_present()
    assert any(entry.startswith("file:") and entry.endswith(".tortoise")
               for entry in found), (
        "the project-scope store was not read — it looks for <cwd>/tortoise "
        f"instead of <cwd>/.tortoise; found {found!r}")


# ── 12 · `_missing_module` checks the module the entry RUNS ──────────────
#
# `import {module.split('.')[0]}` passed on an interpreter whose `tortoise`
# predates `mcp_bridge` — the plausible trigger, since the entry was made
# portable precisely because an installed copy can be old.

def test_missing_module_checks_the_dotted_module_not_its_package(
        tmp_path, monkeypatch):
    import sys

    package = tmp_path / "pkgs14607" / "pkg14607"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "pkgs14607"))

    assert mcp_selfcheck._missing_module(sys.executable, "pkg14607") == "", (
        "the package itself imports, so this row must be clean")
    reason = mcp_selfcheck._missing_module(sys.executable, "pkg14607.sub14607")
    assert reason, (
        "a package that imports while its SUBMODULE does not was reported as "
        "spawnable — the check must import the dotted name the entry runs")


# ── 13 · the CLI lets `default_url()` supply the endpoint ────────────────

def test_the_cli_does_not_hardcode_the_hosted_default(project, monkeypatch):
    """`TORTOISE_MCP_URL` must win at WRITE time on the path users run.

    `url=getattr(args,"url",None) or DEFAULT_MCP_URL` meant `onboard` never saw
    `None`, so `default_url()` was dead on the CLI and a self-hosting box still got
    the hosted default written into its entry — the write-time disagreement the
    helper was added to close, passing at the library level only.
    """
    from types import SimpleNamespace

    from tortoise import __main__ as cli

    self_hosted = "http://self-hosted-14607.invalid/mcp"
    monkeypatch.setenv("TORTOISE_MCP_URL", self_hosted)
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_x")
    args = SimpleNamespace(
        harness="pi", dir=str(project), url=None, dry_run=False, verify=False,
        no_verify=True, no_hooks=True, no_instructions=True, uninstall=False,
        scope="project")
    cli._cmd_onboard_harness(args)
    entry = json.loads((project / ".mcp.json").read_text(
        encoding="utf-8"))["mcpServers"]["tortoise"]
    assert entry["env"]["TORTOISE_MCP_URL"] == self_hosted, (
        "the operator's own endpoint must win at WRITE time — otherwise the entry "
        "and the verification disagree by construction")


# ── 14 · a WITHHELD credential is a credential class, not a server error ──

def test_a_withheld_credential_is_not_classified_as_a_server_error():
    """Co-source conflict: the key belongs to another host, so it was WITHHELD.

    The request carried NO credential, which is why `server-error` ("the endpoint
    answered with an error that is neither transport nor auth") is the one class
    that cannot be right. The detail names both hosts, and the FIX must tell the
    reader to point the ENTRY somewhere else — not to re-mint a working key.
    """
    from tortoise import mcp_client

    exc = mcp_client.MCPCredentialHostMismatchError(
        "MCP-CREDENTIAL-HOST-MISMATCH: the credential supplied by TORTOISE_API_KEY "
        "belongs to http://store-host-14607/mcp, while this client talks to "
        "http://entry-host-14607/mcp. Nothing was sent.")
    status = mcp_selfcheck._classify_probe_failure(exc)
    assert status == mcp_selfcheck.NO_CREDENTIAL, (
        f"a withheld credential was classified {status!r} — it is a credential "
        "class: the request was sent with no credential at all")
    fix = mcp_selfcheck._fix_for(exc, status)
    assert "WITHHELD" in fix and "entry" in fix.lower(), (
        "the remedy must point at the entry, not at the key — the key is fine, "
        f"only its host disagrees: {fix!r}")
    assert "re-mint" not in fix.lower(), (
        f"re-minting a valid key does not fix a host mismatch: {fix!r}")


def test_an_unset_cwd_variable_is_not_described_as_an_empty_bearer(project, monkeypatch):
    """The empty-bearer story belongs to a HEADER, so it is emitted only for one.

    A stdio entry whose unset variable is its WORKING DIRECTORY has no
    `Authorization` header at all: telling the reader its request "would carry
    `Authorization: Bearer ` (empty)" and earn `-32001` names a layer that entry
    never uses — the wrong-layer claim this module exists to remove. The class
    (`env-unset`) and the fix are unchanged; only the mechanism sentence must not
    be invented.
    """
    for name in mcp_selfcheck.CREDENTIAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(mcp_selfcheck, "CREDENTIAL_FILES", ())
    monkeypatch.delenv("WORKDIR_14607", raising=False)
    _write_config(project, {
        "type": "stdio",
        "command": "python3",
        "args": ["-m", "tortoise.mcp_bridge"],
        "cwd": "${WORKDIR_14607}",
    })
    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status == mcp_selfcheck.ENV_UNSET, verdict.line()
    assert "WORKDIR_14607" in verdict.line(), verdict.line()
    assert "Bearer" not in verdict.line(), (
        "this entry has no Authorization header, so a sentence about an empty "
        "bearer names a layer it never uses: " + verdict.line())


def test_a_missing_credential_still_carries_a_remedy():
    """Every DOWN line owes the reader a fix — including the credential classes.

    `no-credential` arrived on the PROBE path (`MCPKeyEmptyError`, or the
    `MCP-KEY-EMPTY` marker) and had no `_FIX_BY_CLASS` entry, so a probe that
    resolved nothing rendered `DOWN [no-credential]: …` with an EMPTY remedy —
    the dead end the module's own docstring says it exists to remove. The class
    names the layer; the fix has to say what to do about it.
    """
    class _KCEmpty(Exception):
        pass

    status = mcp_selfcheck._classify_probe_failure(_KCEmpty(
        "MCP-KEY-EMPTY: no credential resolved from TORTOISE_API_KEY, ./.tortoise, "
        "~/.tortoise/credentials.json"))
    assert status == mcp_selfcheck.NO_CREDENTIAL, status
    fix = mcp_selfcheck._fix_for(_KCEmpty("MCP-KEY-EMPTY"), status)
    assert fix.strip(), (
        "a no-credential verdict rendered no remedy at all — the DOWN line ends "
        "at the class name and the reader has nowhere to go")
    assert "key" in fix.lower(), f"the remedy must be about the key: {fix!r}"


def test_a_5xx_answer_is_a_server_error_not_a_transport_failure():
    """A host that ANSWERS has not become unreachable — the mirror of the
    auth-vs-transport collapse this module exists to prevent.

    The transport token list carried a bare `"unavailable"`, which matched
    httpx's `Client error '503 Service Unavailable'` and classified an ANSWERING
    host as `endpoint-unreachable` — whose fix sends the operator to check
    egress/DNS for a machine that just replied. `server-error` is the class the
    module's own table assigns to a 5xx; a genuine transport phrase must still
    reach `endpoint-unreachable`.
    """
    class _Http(Exception):
        pass

    for text in ("Client error '503 Service Unavailable' for url 'http://h/mcp'",
                 "Client error '502 Bad Gateway' for url 'http://h/mcp'",
                 "500 Internal Server Error"):
        status = mcp_selfcheck._classify_probe_failure(_Http(text))
        assert status == mcp_selfcheck.SERVER_ERROR, (
            f"{text!r} was classified {status!r} — the host answered, so this is "
            "not a transport failure")
    for text in ("All connection attempts failed",
                 "[Errno -3] Temporary failure in name resolution"):
        status = mcp_selfcheck._classify_probe_failure(_Http(text))
        assert status == mcp_selfcheck.UNREACHABLE, (
            f"{text!r} was classified {status!r} — a real transport failure must "
            "still say so")


# ── the entry is resolved the way the harness resolves it (walk UP) ───────

def test_a_cwd_below_the_repo_root_still_finds_the_repo_entry(tmp_path):
    """`pi`'s mcp-client walks UP to the git top-level; reading only the cwd lies.

    Measured before this fix: from `<worktree>/tortoise`, `mcp-status` named
    `~/.pi/agent/.mcp.json` (http) while the harness loads the worktree root's
    `.mcp.json` (stdio bridge, which WINS) — the wrong file, the wrong transport
    and the wrong class.
    """
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    nested = root / "tortoise" / "deep"
    nested.mkdir(parents=True)
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"tortoise": {
        "type": "stdio", "command": "python3",
        "args": ["-m", "tortoise.mcp_bridge"],
        "env": {"TORTOISE_MCP_URL": "http://127.0.0.1:9999/mcp"}}}}),
        encoding="utf-8")
    source, entry, _read, status = mcp_selfcheck.read_entry("pi", project_dir=nested)
    assert status == mcp_selfcheck.OK, source
    assert source == str(root / ".mcp.json"), source
    assert entry["type"] == "stdio"


def test_the_walk_stops_at_the_git_top_level(tmp_path):
    """A `.mcp.json` ABOVE the repo must not be adopted."""
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"tortoise": {
        "type": "http", "url": "https://wrong-14607.invalid/mcp"}}}),
        encoding="utf-8")
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    source, entry, _read, status = mcp_selfcheck.read_entry("pi", project_dir=root)
    assert entry is None, f"adopted an entry above the git top-level: {source}"
    assert status == mcp_selfcheck.ENTRY_ABSENT


# ── the project credential store is scoped to project_dir ────────────────

def test_the_project_credential_store_is_read_from_project_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_MCP_API_KEY", raising=False)
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".tortoise").write_text(
        json.dumps({"api_key": "tt_project_store_key"}), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert any(".tortoise" in s
               for s in mcp_selfcheck.credentials_present(project)), (
        "the project's own credential store was not read when the cwd differed — a "
        "false `no-credential` whose remedy is a key that is already on disk")


# ── a MALFORMED marker pair must be refused, never "repaired" by deleting ──

def test_a_dangling_begin_marker_is_refused_instead_of_eating_user_text(
        project, monkeypatch):
    """Measured before the fix: install appended a second block, uninstall then
    deleted from the FIRST BEGIN to the first END — i.e. the user's own text."""
    monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:9999/mcp")
    path = project / "AGENTS.md"
    user_text = "My own later notes: never delete this."
    path.write_text(
        f"# House rules\n\n{harness_onboard.BEGIN_MARK}\n\n{user_text}\n",
        encoding="utf-8")

    install = harness_onboard.write_instructions("pi", project)
    assert install.refusals, "a dangling BEGIN must REFUSE, not append a second block"
    assert path.read_text().count(harness_onboard.BEGIN_MARK) == 1
    assert user_text in path.read_text()

    removal = harness_onboard.remove_instructions("pi", project)
    assert removal.refusals, "uninstall must refuse a malformed pair"
    assert user_text in path.read_text(), "the reversal deleted the user's own text"


def test_a_well_formed_block_still_round_trips(project, monkeypatch):
    """The guard must not block the safe path it protects."""
    monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:9999/mcp")
    path = project / "AGENTS.md"
    path.write_text("# House\n\nuser notes\n", encoding="utf-8")
    assert not harness_onboard.write_instructions("pi", project).refusals
    assert harness_onboard.BEGIN_MARK in path.read_text()
    assert not harness_onboard.remove_instructions("pi", project).refusals
    assert harness_onboard.BEGIN_MARK not in path.read_text()
    assert "user notes" in path.read_text()


# ── layers are MERGED per field, the way the harness merges them ──────────

def test_a_repo_delta_that_sets_only_one_field_is_not_a_missing_command(
        tmp_path, monkeypatch):
    """`pi`'s own recovery hint is "set only `timeoutMs` in `.mcp.json`"."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)
    (home / ".pi/agent/.mcp.json").write_text(json.dumps({"mcpServers": {
        "tortoise": {"type": "stdio", "command": sys.executable,
                     "args": ["-m", "tortoise.mcp_bridge"],
                     "env": {"TORTOISE_MCP_URL": "http://127.0.0.1:9999/mcp"}}}}),
        encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "tortoise": {"timeoutMs": 40000}}}), encoding="utf-8")

    verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert verdict.status != mcp_selfcheck.COMMAND_MISSING, verdict.line()
    assert verdict.transport == "stdio"
    assert len(verdict.configs_read) == 2, (
        "every layer consulted must be named, not only the one the walk stopped at")


# ── a URL's digits are not a status code ─────────────────────────────────

def test_a_url_containing_401_does_not_become_an_auth_verdict():
    class Fake(Exception):
        pass

    same_answer = "Client error '500 Internal Server Error' for url '{}'"
    for url in ("http://127.0.0.1:9/401/mcp", "http://127.0.0.1:9/plain/mcp"):
        status = mcp_selfcheck._classify_probe_failure(Fake(same_answer.format(url)))
        assert status == mcp_selfcheck.SERVER_ERROR, (url, status)
    # …while a REAL refusal and a REAL transport failure still classify.
    assert mcp_selfcheck._classify_probe_failure(
        Fake("401 Unauthorized from http://h/mcp")) == mcp_selfcheck.AUTH_REJECTED
    assert mcp_selfcheck._classify_probe_failure(
        Fake("All connection attempts failed to http://h/mcp")) == mcp_selfcheck.UNREACHABLE


# ── the installer writes where the verifier reads ────────────────────────

def test_the_project_config_is_written_at_the_repo_root_not_a_nested_shadow(tmp_path):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    nested = root / "tortoise"
    nested.mkdir()
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"x": 1}}}),
                                    encoding="utf-8")
    result = harness_onboard.write_config("pi", nested)
    assert result.wrote == [str(root / ".mcp.json")], result.wrote
    assert not (nested / ".mcp.json").exists(), (
        "a nested .mcp.json SHADOWS the repo-root config for every session started "
        "below it, hiding that file's other servers")
    servers = json.loads((root / ".mcp.json").read_text())["mcpServers"]
    assert "tortoise" in servers and "other" in servers


# ── `claude-code` is an alias, not a harness without a seam ──────────────

def test_the_claude_code_alias_finds_the_claude_seam(project, monkeypatch):
    monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:9999/mcp")
    result = harness_onboard.onboard("claude-code", project_dir=project,
                                     hooks=True, instructions=False, live=False,
                                     dry_run=True)
    assert not [r for r in result.refusals if "seam" in r], (
        "a seam that IS the right artifact must not be refused by alias")


# ── the merge must DELETE the other transport's fields, not inherit them ──

def test_a_repo_entry_declaring_stdio_does_not_inherit_the_base_http_headers(
        tmp_path, monkeypatch):
    """The harness deletes `url`/`headers`/`oauth` when a layer declares `command`.

    Measured before this arm: the shipped bridge in the repo layer on top of an
    http hub base reported `env-unset` about a `${VAR}` in a header the CHILD NEVER
    RECEIVES — a false DOWN naming the good file as `source` and prescribing the
    install the user already has.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)
    (home / ".pi/agent/.mcp.json").write_text(json.dumps({"mcpServers": {
        "tortoise": {"type": "http", "url": "https://api.premiselabs.co/mcp/",
                     "headers": {"Authorization": "Bearer ${TORTOISE_MCP_API_KEY}"}}}}),
        encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"tortoise": {
        "type": "stdio", "command": "python3",
        "args": ["-m", "tortoise.mcp_bridge"],
        "env": {"TORTOISE_MCP_URL": "https://api.premiselabs.co/mcp/"}}}}),
        encoding="utf-8")

    _source, entry, _read, _status = mcp_selfcheck.read_entry("pi", project_dir=repo)
    assert "headers" not in entry and "url" not in entry, entry
    verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert verdict.transport == "stdio", verdict.line()
    assert verdict.status != mcp_selfcheck.ENV_UNSET, verdict.line()


def test_a_stdio_entry_over_an_http_base_is_still_checked_for_its_command(
        tmp_path, monkeypatch):
    """No inherited `url` may beat an explicit `type: stdio`."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)
    (home / ".pi/agent/.mcp.json").write_text(json.dumps({"mcpServers": {
        "tortoise": {"type": "http", "url": "https://api.premiselabs.co/mcp/"}}}),
        encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"tortoise": {
        "type": "stdio", "command": "/nonexistent/14607/python3",
        "args": ["-m", "tortoise.mcp_bridge"]}}}), encoding="utf-8")
    verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert verdict.status == mcp_selfcheck.COMMAND_MISSING, verdict.line()


def test_a_wrapper_command_is_not_import_probed():
    """`uv run python -m …` is a wrapper; probing it graded the wrong program."""
    assert not mcp_selfcheck._looks_like_python("uv")
    assert not mcp_selfcheck._looks_like_python("/usr/local/bin/npx")
    assert mcp_selfcheck._looks_like_python("python3")
    assert mcp_selfcheck._looks_like_python("/opt/venv/bin/python3.12")


def test_the_instruction_block_lands_at_the_repo_root(tmp_path):
    """Split from the config, the install wrote a nested AGENTS.md nobody reads."""
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    nested = root / "tortoise"
    nested.mkdir()
    (root / "AGENTS.md").write_text("# root\n\nuser text\n", encoding="utf-8")
    result = harness_onboard.write_instructions("pi", nested)
    assert result.wrote == [str(root / "AGENTS.md")], result.wrote
    assert not (nested / "AGENTS.md").exists()
    assert harness_onboard.BEGIN_MARK in (root / "AGENTS.md").read_text()


# ── `type` is not a transport selector: no url and no command cannot load ─

def test_an_entry_with_neither_url_nor_command_is_never_ok(project, monkeypatch):
    """Measured before this arm: `{"type": "http", "headers": {...}}` probed the
    CLIENT-resolved endpoint and returned `OK — live call succeeded`, exit 0, for a
    config the harness cannot load (`has neither 'command' nor 'url'`)."""
    _write_config(project, {"type": "http",
                            "headers": {"Authorization": "Bearer tt_whatever"}})
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_whatever")
    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status == mcp_selfcheck.COMMAND_MISSING, verdict.line()
    assert "its `type` does not" in verdict.detail
    assert not verdict.ok and verdict.exit_code != 0


# ── the two merge shapes the harness documents ──────────────────────────

def _layers(tmp_path, monkeypatch, *, base, repo_entry):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)
    (home / ".pi/agent/.mcp.json").write_text(
        json.dumps({"mcpServers": {"tortoise": base}}), encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"tortoise": repo_entry}}), encoding="utf-8")
    return repo


def test_an_ambiguous_BASE_still_loads_behind_a_field_delta(tmp_path, monkeypatch):
    """A base declaring BOTH transports is copied verbatim; only a DELTA is ignored."""
    repo = _layers(tmp_path, monkeypatch,
                   base={"type": "http", "url": "https://api.premiselabs.co/mcp/",
                         "command": "python3"},
                   repo_entry={"timeoutMs": 40000})
    verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert verdict.status != mcp_selfcheck.COMMAND_MISSING, verdict.line()
    assert verdict.status != mcp_selfcheck.ENTRY_ABSENT, verdict.line()


def test_a_falsy_transport_is_ABSENCE_not_a_delta_value(tmp_path, monkeypatch):
    """`{"command": ""}` must not erase the base's working transport."""
    repo = _layers(tmp_path, monkeypatch,
                   base={"type": "stdio", "command": "python3",
                         "args": ["-m", "tortoise.mcp_bridge"],
                         "env": {"TORTOISE_MCP_URL": "http://127.0.0.1:9999/mcp"}},
                   repo_entry={"command": ""})
    verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert verdict.transport == "stdio", verdict.line()
    assert verdict.status != mcp_selfcheck.COMMAND_MISSING, verdict.line()


def test_a_repo_only_entry_declaring_both_transports_is_kept_over_url(
        tmp_path, monkeypatch):
    """Dropping it would zero a working server; the harness keeps and runs over `url`."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)          # no base entry at all
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"tortoise": {
        "type": "http", "url": "https://api.premiselabs.co/mcp/",
        "command": "python3"}}}), encoding="utf-8")
    verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert verdict.status == mcp_selfcheck.RESOLVES_UNPROBED, verdict.line()
    assert verdict.transport == "http"


def test_the_default_relative_dir_still_writes_the_repo_root_config(tmp_path, monkeypatch):
    """The CLI's `--dir` default is `Path(".")`; `Path(".").parents == []`."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    sub = root / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    result = harness_onboard.write_config("pi", Path("."))
    assert result.wrote == [str(root / ".mcp.json")], result.wrote
    assert not (sub / ".mcp.json").exists()
