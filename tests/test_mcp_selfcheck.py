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
