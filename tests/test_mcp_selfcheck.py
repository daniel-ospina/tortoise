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
    # ⛔ A git top-level BOUNDS the read walk. Without one the walk runs to the
    # filesystem root (it mirrors the `mcp-client` extension), so on a box where an
    # unrelated `/tmp/.mcp.json` exists — pytest's own `tmp_path` lives under `/tmp`
    # — this arm classified THAT file (`env-unset`) instead of this project. The
    # bound is what makes the `entry-absent` claim about this project rather than
    # about the host.
    (project / ".git").mkdir()
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


def test_declared_endpoint_follows_the_DISPATCHED_transport(project):
    """An http entry is contacted over `url`; `env` is read only by the stdio branch.

    ⛔ The previous version of this arm pinned `env` winning for an http entry,
    which is the opposite of what the harness does (`connectServer` reads
    `url`/`headers`/`oauth`; `env` belongs to `buildMcpServerEnv`). That order made
    `--live` grade a host the entry never contacts.
    """
    _write_config(project, {
        "type": "http",
        "url": "https://url-wins-14607.invalid/mcp",
        "env": {"TORTOISE_MCP_URL": "https://env-ignored-14607.invalid/mcp"},
    })
    assert (mcp_selfcheck.declared_endpoint("pi", project_dir=project)
            == "https://url-wins-14607.invalid/mcp")


def test_declared_endpoint_reads_env_for_a_stdio_entry(project):
    """The bridge is a stdio shim: its target IS `env.TORTOISE_MCP_URL`."""
    _write_config(project, {
        "type": "stdio",
        "command": "python3",
        "args": ["-m", "tortoise.mcp_bridge"],
        "env": {"TORTOISE_MCP_URL": "http://127.0.0.1:9999/mcp"},
    })
    assert (mcp_selfcheck.declared_endpoint("pi", project_dir=project)
            == "http://127.0.0.1:9999/mcp")


def test_declared_endpoint_is_none_for_a_bare_stdio_entry(project):
    """No declared endpoint ⇒ the client's own resolution is the right answer."""
    _write_config(project, {
        "type": "stdio", "command": "python3",
        "args": ["-m", "tortoise.mcp_bridge"],
    })
    assert mcp_selfcheck.declared_endpoint("pi", project_dir=project) is None


def test_a_local_stdio_server_is_not_proved_by_an_http_call(
        project, auth_endpoint, monkeypatch):
    """⛔ A LOCAL stdio SERVER IS NOT THE BRIDGE — a green here is about another host.

    `declared_endpoint` is None for EVERY bare stdio entry, so the probe fell back
    to the CLIENT-resolved endpoint in the operator's shell: a self-hosted entry
    (`python -m tortoise.mcp_server`, whose own server may point at a dead port)
    came back `OK (…, stdio): live call succeeded`, graded about an endpoint the
    entry never contacts. `onboard` reads `verdict.exit_code`, so that green also
    licensed writing the "reads go through the MCP" sentences on an install
    nothing proved. Only the bridge is a stdio→HTTP shim an HTTP call can grade.
    """
    monkeypatch.setenv("TORTOISE_MCP_URL", auth_endpoint)
    monkeypatch.setenv("TORTOISE_API_KEY", RIGHT_KEY_14607)
    # The static pass must reach `resolves-unprobed` for this test to exercise the
    # LIVE-upgrade decision, so the interpreter check is stubbed to "imports fine"
    # (this box's own `tortoise.mcp_server` cannot import — `fastmcp` is absent — and
    # that is a DIFFERENT, correct verdict: `server-error`, pinned separately below).
    monkeypatch.setattr(mcp_selfcheck, "_missing_module", lambda *a, **k: "")
    _write_config(project, {
        "command": "python3",
        "args": ["-m", "tortoise.mcp_server"],
        "env": {"TORTOISE_DB_URI": "docker://:pw@127.0.0.1:1/none"},
    })
    verdict = mcp_selfcheck.check("pi", project_dir=project, live=True)
    assert verdict.status == mcp_selfcheck.RESOLVES_UNPROBED, verdict.line()
    assert verdict.exit_code == 3, (
        "a local stdio server cannot be proved by an HTTP call, so this must be "
        "an abstention — never a green: " + verdict.line())
    assert not verdict.ok


def test_an_import_failure_that_is_not_a_missing_module_is_not_told_to_install(
        project, monkeypatch):
    """⛔ NOT EVERY IMPORT FAILURE IS A MISSING PACKAGE.

    `python -c "import tortoise.mcp_server"` exits non-zero on an interpreter that
    HAS tortoise whenever the module refuses to load at import time (measured: the
    local stdio server raises on a hosted `TORTOISE_API_KEY`; on this box it raises
    because `fastmcp` is absent). Reporting that as `python-lacks-tortoise` and
    prescribing "install tortoise" sent the reader to install a package they
    already had — the wrong-layer remedy this module exists to remove.
    """
    monkeypatch.setattr(mcp_selfcheck, "_missing_module",
                        lambda *a, **k: "RuntimeError: TORTOISE_API_KEY is the "
                                        "hosted/cloud key — it is not used by local stdio MCP")
    _write_config(project, {
        "command": "python3",
        "args": ["-m", "tortoise.mcp_server"],
    })
    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status == mcp_selfcheck.SERVER_ERROR, verdict.line()
    assert "install tortoise" not in verdict.line(), (
        "the interpreter has tortoise; this remedy is for a different failure: "
        + verdict.line())
    assert "hosted/cloud key" in verdict.line(), (
        "the child's own cause must be carried through: " + verdict.line())


def test_the_bridge_is_still_proved_by_an_http_call(
        project, auth_endpoint, monkeypatch):
    """The other half: the bridge IS a stdio→HTTP shim, so the probe still grades it."""
    monkeypatch.setenv("TORTOISE_API_KEY", RIGHT_KEY_14607)
    monkeypatch.delenv("TORTOISE_MCP_URL", raising=False)
    _write_config(project, {
        "command": "python3",
        "args": ["-m", "tortoise.mcp_bridge"],
        "env": {"TORTOISE_MCP_URL": auth_endpoint},
    })
    assert mcp_selfcheck._forwards_over_http("pi", project_dir=project) is True
    verdict = mcp_selfcheck.check("pi", project_dir=project, live=True)
    assert verdict.ok, verdict.line()


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


def test_an_entry_supplied_credential_is_graded_as_the_entry_names_it(
        project, auth_endpoint, monkeypatch):
    """The probe sends the ENTRY's bearer — and a refusal of it is `auth-rejected`.

    Two defects in one place, both measured before this test existed: the probe
    resolved the credential from the SHELL (so an entry naming a different key was
    graded with an unrelated one), and `_FixedBearer` never recorded what it sent,
    so the classifier re-resolved the shell and reported a REJECTED entry key as
    `no-credential` — "supply one" — while the key was already in the config. The
    class for one entry must not depend on whether some other variable happens to
    be exported.
    """
    for name in mcp_selfcheck.CREDENTIAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(mcp_selfcheck, "CREDENTIAL_FILES", ())
    _write_config(project, {
        "type": "http",
        "url": auth_endpoint,
        "headers": {"Authorization": "Bearer tt_revoked_14607"},
    })
    endpoint = mcp_selfcheck.declared_endpoint("pi", project_dir=project)
    verdict = mcp_selfcheck.probe(url=endpoint, token="tt_revoked_14607")
    assert verdict.status == mcp_selfcheck.AUTH_REJECTED, (
        "a credential WAS sent (the entry's own) and the server refused it — this "
        "is an auth verdict, never `no-credential`: " + verdict.line())
    assert "supply one" not in verdict.line()

    accepted = mcp_selfcheck.probe(url=endpoint, token=RIGHT_KEY_14607)
    assert accepted.ok, accepted.line()


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


def test_a_fenced_example_of_the_block_is_neither_counted_nor_removed():
    """⛔ A FENCED EXAMPLE IS DOCUMENTATION, NOT THE BLOCK.

    A repo that DOCUMENTS what the installer appends puts the markers in a fence.
    The scan was `text.count(...)` and `_strip_block` removed from the first BEGIN
    to the next END, so `--uninstall` emptied the example's fence and exited 0 with
    no refusal — the file's own contract is "removes exactly the marked block
    (never a user's own text)". The sibling installer
    (`install-tortoise-skills.sh`) already skips CommonMark fences for the count AND
    the rewrite, so the two installers must agree here.
    """
    fenced = ("# House rules\n\n```md\n" + harness_onboard.BEGIN_MARK +
              "\nexample body\n" + harness_onboard.END_MARK + "\n```\n\n"
              "My own notes: never delete this.\n")
    assert harness_onboard._markers_well_formed(fenced) == (True, "")
    assert harness_onboard._strip_block(fenced) == fenced, (
        "the fenced example was edited — it is documentation, not our block")


def test_a_real_block_is_removed_while_the_fenced_example_survives():
    """Both at once: the real block goes, the example stays, the notes stay."""
    text = ("# House rules\n\n```md\n" + harness_onboard.BEGIN_MARK +
            "\nexample body\n" + harness_onboard.END_MARK + "\n```\n\n"
            + harness_onboard.INSTRUCTIONS + "\n\nMy own notes: keep this.\n")
    assert harness_onboard._markers_well_formed(text) == (True, "")
    stripped = harness_onboard._strip_block(text)
    assert "example body" in stripped, "the fenced example was destroyed"
    assert "My own notes: keep this." in stripped
    assert "Reads go through the Tortoise MCP" not in stripped, (
        "the real block must still be removed")


def test_an_unclosed_fence_is_REFUSED_not_read_as_fenced_to_eof():
    """⛔ "FENCED TO EOF" HID EVERY REAL MARKER BELOW IT — and appended forever.

    My first cut of the fence rule read an unterminated fence as covering the rest
    of the file, so `_marker_pair` saw (0, 0) = "nothing to do" and
    `write_instructions` APPENDED one more copy of the block per run — inside the
    open fence, where `--uninstall` could then never remove it: measured, three runs
    produced three BEGINS and the removal still wrote the file while deleting
    nothing. The sibling installer learned the same lesson (`fenceopen`: *"An
    UNCLOSED fence would hide every real marker below it, and 'no markers' means
    APPEND — one more copy per run"*). Refusing by name costs a clear error instead
    of an unreversible file.
    """
    text = ("# House rules\n\n```md\n" + harness_onboard.BEGIN_MARK +
            "\nhalf-written example\n")
    ok, why = harness_onboard._markers_well_formed(text)
    assert ok is False, "an unclosed fence must be refused, never read as (0, 0)"
    assert "line 3" in why, why


def test_a_four_space_indented_example_is_not_ours_either():
    """A fence inside a LIST ITEM is indented — CommonMark agrees, so must we.

    `indent <= 3` excluded it, so a 4-space-indented documentation example read as
    the real block: measured, `--uninstall` deleted the example's body and exited 0
    with no refusal. `MarkdownIt("commonmark")` parses the same input as a fence
    whose content is the example, and the sibling installer refuses this shape for
    exactly that reason.
    """
    text = ("1. Document the block:\n\n    ```md\n    " + harness_onboard.BEGIN_MARK +
            "\n    example body\n    " + harness_onboard.END_MARK +
            "\n    ```\n\nMy own notes: keep.\n")
    assert harness_onboard._markers_well_formed(text) == (True, "")
    assert "example body" in harness_onboard._strip_block(text), (
        "a 4-space-indented example was deleted")


def test_a_prose_mention_of_a_marker_is_not_the_block():
    """The marker must be ALONE on its line — a substring match ate the prose.

    Measured before the fix: `"Our installer writes <BEGIN>" … "and <END> at the
    end."` lost BOTH lines, and `"<BEGIN><END>"` on one line swallowed everything
    after it. The sibling installer matches the anchored form; so does this now.
    """
    prose = ("# House\n\nOur installer writes " + harness_onboard.BEGIN_MARK +
             "\nat the top, and " + harness_onboard.END_MARK + " at the end.\n\nkeep.\n")
    assert harness_onboard._markers_well_formed(prose) == (True, "")
    stripped = harness_onboard._strip_block(prose)
    assert harness_onboard.BEGIN_MARK in stripped, "a prose mention was removed"
    assert "keep." in stripped

    shared = ("# House\n\n" + harness_onboard.BEGIN_MARK +
              harness_onboard.END_MARK + "\n\nkeep.\n")
    assert "keep." in harness_onboard._strip_block(shared), (
        "a line carrying both markers swallowed everything after it")


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

def test_the_cli_does_not_hardcode_the_hosted_default(project, monkeypatch, tmp_path):
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
    # ⛔ `dir=` is a knob the CODE under test resolves, so this arm's sandbox was
    # only as good as that resolution. Measured while fixing #14607: with
    # `project_dir=_Path.cwd()` in `_cmd_onboard_harness`, this test wrote its
    # `.invalid` endpoint into the REPO'S OWN `.mcp.json` and then reported the
    # FileNotFoundError below — a repo-mutating test whose symptom named the
    # fixture, not the damage, and whose only real signal was a downstream
    # `TestCommittedRepoMcpJson` arm going red over a committed config.
    # ⛔ chdir to an EMPTY scratch dir, NOT to `project`: the write must still land
    # in `project` only because `dir` was honoured, so a `dir` regression reds here
    # (the fixture file is absent) instead of passing on a cwd coincidence — and
    # the damage can only land in scratch, never in the checkout.
    scratch = tmp_path / "not-the-checkout"
    scratch.mkdir()
    monkeypatch.chdir(scratch)
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


def test_a_literal_bearer_with_an_unset_other_header_is_not_an_empty_bearer(
        project, monkeypatch):
    """The guard is the INTERSECTION: an `Authorization` leaf AND this unset name.

    Two earlier revisions of it were wrong in opposite directions: it fired for a
    stdio entry whose unset variable was a `cwd`, then for an entry whose
    `Authorization` value is a LITERAL bearer while the unset name belonged to a
    different header. Here the bearer in the file is non-empty and the unset name
    is somebody else's — so a sentence about `Authorization: Bearer ` (empty) is a
    claim about a header this entry does not have.
    """
    monkeypatch.delenv("RV14607_UNSET", raising=False)
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_present-14607")
    _write_config(project, {
        "type": "http",
        "url": "https://api.premiselabs.co/mcp/",
        "headers": {"Authorization": "Bearer tt_literal_in_file",
                    "X-Org-Id": "${RV14607_UNSET}"},
    })
    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status == mcp_selfcheck.ENV_UNSET, verdict.line()
    assert "RV14607_UNSET" in verdict.line()
    assert "Bearer ` (empty)" not in verdict.line(), (
        "the bearer in this file is a literal; the unset name is a different "
        "header, so the empty-bearer sentence is a wrong-layer claim: " + verdict.line())


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
    # ⛔ `type` IS NOT BLAMED (#14617 F2). The old sentence ended "its `type` does
    # not select a transport", which is false — `type` is not a selector in the
    # harness at all, it dispatches on `url`/`command` — and it sent the reader to
    # inspect a field that was never the problem.
    assert "`url`" in verdict.detail and "`command`" in verdict.detail
    assert "its `type` does not" not in verdict.detail, verdict.detail
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
        # ⛔ the credential layer is NOT what this arm grades: since #14617 F6 an
        # http entry with no `Authorization` header is `no-credential` (a config
        # sends only what it declares), so a literal header keeps the TRANSPORT
        # question — the one this arm exists for — the thing under test.
        "headers": {"Authorization": "Bearer tt_literal_14607"},
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


def test_a_non_string_transport_is_unloadable_not_a_live_transport(project, monkeypatch):
    """The harness DROPS an entry whose `url`/`command` is a truthy non-string.

    Treating it as live re-opened the false PASS this PR closes: `declared_endpoint`
    returns None for a non-string, so the probe graded the CLIENT-resolved endpoint
    and printed `OK` for a config the harness had dropped. A non-string `command`
    also raised out of `classify`, so `mcp-status` tracebacked.
    """
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_x-14607")
    for entry in ({"url": ["https://api.premiselabs.co/mcp/"]},
                  {"url": {"host": "x"}},
                  {"url": 123},
                  {"command": ["python3"]},
                  {"command": 7}):
        _write_config(project, entry)
        verdict = mcp_selfcheck.classify("pi", project_dir=project)   # must not raise
        assert verdict.status == mcp_selfcheck.COMMAND_MISSING, (entry, verdict.line())
        assert not verdict.ok and verdict.exit_code != 0


# ── JS truthiness: `[]` and `{}` are TRUTHY, so the harness drops the entry ──

def test_empty_container_transports_are_unloadable_like_the_harness_says(
        project, monkeypatch):
    """`!![]` and `!!{}` are TRUE in JS — Python reads both as falsy.

    The harness predicate is `typeof … !== "string" && !!…`, so those entries are
    DROPPED; a Python-truthy guard read them as absent and kept an entry the harness
    had thrown away, which is the false PASS this whole guard exists to close.
    """
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_x-14607")
    # ⛔ A container transport ALONE cannot discriminate: the old guard popped it as
    # falsy and landed on the same class. The false PASS was reachable through a
    # container transport BESIDE a working one, which the old code read as "absent"
    # and kept — so those are the entries that red on a revert.
    for entry in ({"url": []}, {"url": {}}, {"command": []}, {"command": {}},
                  {"url": [], "command": "python3"},
                  {"command": [], "url": "https://api.premiselabs.co/mcp/"},
                  {"url": {}, "command": "python3"}):
        _write_config(project, entry)
        verdict = mcp_selfcheck.classify("pi", project_dir=project)
        assert verdict.status == mcp_selfcheck.COMMAND_MISSING, (entry, verdict.line())
        assert not verdict.ok


def test_a_blank_url_is_declared_by_the_harness_and_is_unloadable(
        project, monkeypatch):
    """`typeof v === "string" && v !== ""` — so `"  "` IS a declared transport.

    It overrides the base and `new URL("  ")` throws, i.e. the entry is dead. The
    class must say so, and nothing may be probed for it.
    """
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_x-14607")
    _write_config(project, {"url": "   "})

    probed: list[object] = []
    monkeypatch.setattr(mcp_selfcheck, "probe",
                        lambda **kw: probed.append(kw) or mcp_selfcheck.Verdict(
                            status=mcp_selfcheck.OK, detail="should not be reached"))

    verdict = mcp_selfcheck.check("pi", project_dir=project, live=True)
    assert verdict.status == mcp_selfcheck.COMMAND_MISSING, verdict.line()
    assert "BLANK" in verdict.detail, verdict.detail
    assert not probed, "a blank transport must never be probed"
    assert not verdict.ok and verdict.exit_code != 0


def test_check_refuses_to_probe_an_http_entry_with_no_readable_endpoint(
        project, monkeypatch):
    """The generic guard behind the blank-url case: never invent a probe target.

    `{"url": "${VAR}"}` with VAR blank classifies as http, and `declared_endpoint`
    cannot read an endpoint from it — so `check` must abstain rather than grade the
    CLIENT-resolved endpoint (which is how a dead config printed `OK`).
    """
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_x-14607")
    monkeypatch.setenv("TORTOISE_TEST_BLANK_14607", "   ")
    # A DECLARED credential keeps the entry past the http credential layer (#14617
    # F6: a headerless http entry is `no-credential`, a different question) so this
    # arm still grades the endpoint guard it exists for.
    _write_config(project, {"url": "${TORTOISE_TEST_BLANK_14607}",
                            "headers": {"Authorization": "Bearer tt_literal_14607"}})

    probed: list[object] = []
    monkeypatch.setattr(mcp_selfcheck, "probe",
                        lambda **kw: probed.append(kw) or mcp_selfcheck.Verdict(
                            status=mcp_selfcheck.OK, detail="should not be reached"))

    verdict = mcp_selfcheck.check("pi", project_dir=project, live=True)
    assert not probed, "the probe must not run without a readable endpoint"
    # ⛔ The CLASS, not "not OK": with the guard deleted the next branch returns
    # RESOLVES_UNPROBED, which is also "not OK" and also an abstention — so the old
    # assertion could not red on the guard it named.
    assert verdict.status == mcp_selfcheck.PROBE_UNKNOWN, verdict.line()
    assert "no readable endpoint" in verdict.detail, verdict.detail


def test_a_kept_repo_only_entry_survives_a_DROPPED_base(tmp_path, monkeypatch):
    """"There is no inherited entry" must survive the base being dropped.

    With the base dropped, a repo entry declaring both transports is no longer a
    delta — the harness KEEPS it and runs over `url`, while the verifier reported
    `command-missing` on a config that loads.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)
    (home / ".pi/agent/.mcp.json").write_text(json.dumps({"mcpServers": {
        "tortoise": {"type": "http", "url": ["bad-transport"]}}}),
        encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"tortoise": {
        "url": "https://api.premiselabs.co/mcp/", "command": "python3",
        # a DECLARED credential: the transport question is the one under test here
        "headers": {"Authorization": "Bearer tt_literal_14607"}}}}),
        encoding="utf-8")
    verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert verdict.status == mcp_selfcheck.RESOLVES_UNPROBED, verdict.line()
    assert verdict.transport == "http", verdict.line()


def test_a_changed_destination_drops_the_inherited_credential(tmp_path, monkeypatch):
    """The harness drops `headers`/`oauth` when the projected `url` moves.

    Measured before this arm: the self-host override to `http://localhost:8000/mcp`
    inherited the base's `Authorization: Bearer ${TORTOISE_API_MCP_KEY}` and the
    verdict read `env-unset` — "the request would carry an empty bearer" — for an
    effective entry that has no headers at all, naming the repo file as the source.
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
        "url": "http://localhost:8000/mcp"}}}), encoding="utf-8")

    _source, entry, _read, _status = mcp_selfcheck.read_entry("pi", project_dir=repo)
    assert "headers" not in entry, entry
    verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert verdict.status != mcp_selfcheck.ENV_UNSET, verdict.line()


def test_a_loopback_endpoint_without_a_credential_is_not_called_down(project, monkeypatch):
    """The shipped compose default is `auth_mode=none` with an empty key."""
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_MCP_API_KEY", raising=False)
    _write_config(project, {"type": "http", "url": "http://localhost:8000/mcp"})
    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status != mcp_selfcheck.NO_CREDENTIAL, verdict.line()
    assert verdict.status in mcp_selfcheck.ABSTENTION_CLASSES, verdict.line()

    _write_config(project, {"type": "http",
                            "url": "https://api.premiselabs.co/mcp/"})
    hosted = mcp_selfcheck.classify("pi", project_dir=project)
    assert hosted.status == mcp_selfcheck.NO_CREDENTIAL, (
        "the hosted endpoint DOES require a credential — that must stay a DOWN")


def test_an_identical_launch_restatement_keeps_the_base_env(tmp_path, monkeypatch):
    """`sameLaunch` compares command/cwd/args — a restatement is NOT a change.

    Firing the `env` guard on mere mention dropped the base's `TORTOISE_MCP_URL`,
    so `declared_endpoint` returned None and `check(live=True)` graded the CLI-ent
    shell's endpoint — a hybrid no harness runs.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)
    base_env = {"TORTOISE_MCP_URL": "http://127.0.0.1:9/mcp"}
    (home / ".pi/agent/.mcp.json").write_text(json.dumps({"mcpServers": {"tortoise": {
        "command": "python3", "args": ["-m", "tortoise.mcp_bridge"],
        "env": base_env}}}), encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"tortoise": {
        "command": "python3", "args": ["-m", "tortoise.mcp_bridge"]}}}),
        encoding="utf-8")
    assert (mcp_selfcheck.declared_endpoint("pi", project_dir=repo)
            == "http://127.0.0.1:9/mcp")


def test_destination_identity_keeps_a_trailing_slash_significant():
    """`/mcp` and `/mcp/` are routed separately and the harness keeps them apart."""
    assert not mcp_selfcheck.same_destination("https://x/mcp", "https://x/mcp/"), (
        "a trailing slash must count as a DIFFERENT destination — otherwise the "
        "verifier keeps an inherited Authorization header the harness drops")
    # …while a default port is not a difference (`new URL` maps :443 away).
    assert mcp_selfcheck.same_destination("https://x:443/mcp", "https://x/mcp")


def test_an_ipv6_loopback_endpoint_is_exempt_like_ipv4(tmp_path, monkeypatch):
    """`partition(":")` splits inside `[::1]`, so the exemption never fired."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_MCP_API_KEY", raising=False)
    (home / ".pi/agent").mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    for url in ("http://[::1]:8000/mcp", "http://[::1]/mcp"):
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "tortoise": {"type": "http", "url": url}}}), encoding="utf-8")
        verdict = mcp_selfcheck.classify("pi", project_dir=repo)
        assert verdict.status != mcp_selfcheck.NO_CREDENTIAL, (url, verdict.line())


def test_a_sanitised_away_base_is_not_inherited_but_an_empty_one_is(tmp_path, monkeypatch):
    """`{"url": ""}` is dropped by the harness; `{}` is inherited (`hasOwn` + truthy)."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    # a DECLARED credential: the merge three-state is what this arm grades, and
    # since #14617 F6 a headerless http entry is `no-credential` before it gets here.
    repo_entry = {"url": "https://api.premiselabs.co/mcp/", "command": "python3",
                  "headers": {"Authorization": "Bearer tt_literal_14607"}}
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"tortoise": repo_entry}}), encoding="utf-8")

    (home / ".pi/agent/.mcp.json").write_text(json.dumps({"mcpServers": {
        "tortoise": {"url": ""}}}), encoding="utf-8")
    dropped = mcp_selfcheck.classify("pi", project_dir=repo)
    assert dropped.status == mcp_selfcheck.RESOLVES_UNPROBED, dropped.line()

    (home / ".pi/agent/.mcp.json").write_text(json.dumps({"mcpServers": {
        "tortoise": {}}}), encoding="utf-8")
    inherited = mcp_selfcheck.classify("pi", project_dir=repo)
    assert inherited.status == mcp_selfcheck.COMMAND_MISSING, inherited.line()


# ═══════════════════════════════════════════════════════════════════════════
# #14617 review round 7 — the eight `no row reddens` rows, each with its arm.
#
# F1..F7 were a single shape: a behaviour the diff asserted in prose (a refusal
# text, a docstring, a verdict line) that NO assertion varied, so reverting the
# behaviour left the suite green. Each arm below states the revert it reds.
# ═══════════════════════════════════════════════════════════════════════════

def test_codex_at_project_scope_names_the_file_codex_actually_reads(
        project, monkeypatch):
    """F1 — `--harness codex` at the DEFAULT scope named `<repo>/.mcp.json` as TOML.

    `HARNESS_CONFIG_PATHS["codex"]` has no project rels, so the project loop was
    empty and the old fallback returned `<repo>/.mcp.json`. The refusal then told a
    Codex user to paste a TOML table into a file Codex never reads — three false
    statements in one message, at the path a plain `tortoise onboard --harness
    codex` takes (`--scope` defaults to `project`).

    REDS ON REVERT: move the TOML refusal back after `_config_path` (or drop the
    `if not project_rels: return None` arm) — the message names `.mcp.json` again.
    """
    project = Path(project)
    (project / ".git").mkdir()
    home = Path.home()
    (home / ".codex").mkdir(parents=True, exist_ok=True)
    real = home / ".codex/config.toml"

    assert harness_onboard._config_path("codex", project, scope="project") is None, (
        "codex has no project layer — inventing `<repo>/.mcp.json` is the F1 defect")

    out = harness_onboard.write_config("codex", project, scope="project")
    assert out.wrote == []
    text = "\n".join(out.refusals)
    assert str(real) in text, f"the refusal must name the file Codex reads: {text!r}"
    assert str(project / ".mcp.json") not in text, (
        f"the refusal named a file Codex never reads: {text!r}")
    assert "is TOML" not in text.replace(f"{real} is TOML", ""), (
        f"only the harness's OWN TOML file may be called TOML: {text!r}")
    assert "--scope project" in text, (
        "codex has one global config; saying so is the fix's second half")
    assert not (project / ".mcp.json").exists()


def test_a_dropped_DELTA_leaves_the_base_standing(tmp_path, monkeypatch):
    """F2(a) — the arm that reds `_unloadable` on its own.

    The existing non-string arm asserted `command-missing` for a repo-only bad
    entry — a class the mutant produces too, so `_unloadable -> return False` left
    it GREEN. The discriminating shape is a DROPPED DELTA over a good base: head
    keeps the base (`resolves-unprobed`/http), the mutant reports the delta's
    `command-missing`/stdio instead.

    REDS ON REVERT: `_unloadable` -> `return False`.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)
    (home / ".pi/agent/.mcp.json").write_text(json.dumps({"mcpServers": {
        "tortoise": {"type": "http", "url": "https://api.premiselabs.co/mcp/",
                     "headers": {"Authorization": "Bearer tt_base_14607"}}}}),
        encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"tortoise": {
        "url": ["https://evil-14607.invalid/mcp"]}}}), encoding="utf-8")

    verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert verdict.status == mcp_selfcheck.RESOLVES_UNPROBED, verdict.line()
    assert verdict.transport == "http", (
        "the repo layer's non-string `url` must be DROPPED, leaving the base's "
        f"http entry standing — not read as a stdio switch: {verdict.line()}")
    assert "premiselabs.co" in mcp_selfcheck.declared_endpoint("pi", project_dir=repo)


def test_a_non_string_transport_is_named_as_the_fault_not_as_type(project, monkeypatch):
    """F2(b) — the `isinstance` selector in `classify`, reachable on its own.

    With `_unloadable` present a non-string transport never reaches `classify`, so
    the selector there had no arm: dropping both `isinstance(...)` checks made
    `url.strip()` raise on a list, and nothing reddened. `read_entry` is stubbed
    here so `classify` itself is the unit under test.

    REDS ON REVERT: remove `isinstance(url_value, str)` / `isinstance(command_value,
    str)` — `AttributeError`/`TypeError` out of `classify` instead of a verdict.
    """
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_x-14607")
    for entry, field in (({"url": ["https://x/mcp/"]}, "url"),
                         ({"command": ["python3"]}, "command"),
                         ({"url": 123}, "url"),
                         ({"command": {"a": 1}}, "command")):
        monkeypatch.setattr(mcp_selfcheck, "read_entry",
                            lambda *a, _e=entry, **k: ("stub/.mcp.json", _e, [], "ok"))
        verdict = mcp_selfcheck.classify("pi", project_dir=project)
        assert verdict.status == mcp_selfcheck.COMMAND_MISSING, verdict.line()
        assert f"`{field}`" in verdict.detail, verdict.detail
        assert "not a string" in verdict.detail, (
            "the detail must name the actual fault — a declared field that is not "
            f"a string — not `type`: {verdict.detail!r}")
        assert "`type`" not in verdict.detail, verdict.detail


def test_project_scope_never_writes_outside_a_project_without_git(
        tmp_path, monkeypatch):
    """F3 — outside a git work tree, `--scope project` edited an ANCESTOR's files.

    `_project_layer` mirrors the harness's READ walk, which escapes to the
    filesystem root when there is no `.git` (`resolveMcpConfigPaths`). Following it
    for a WRITE made `cd ~/scratch && tortoise onboard --harness pi` edit
    `~/AGENTS.md` and `~/.mcp.json` while the flag reported project scope — a write
    outside the directory the user scoped, with a false provenance label.

    REDS ON REVERT: `write_layer` -> `_project_layer` in `_config_path` /
    `_instruction_path`.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    ancestor = tmp_path / "ancestor"
    ancestor.mkdir()
    (ancestor / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    (ancestor / "AGENTS.md").write_text("# ancestor house rules\n", encoding="utf-8")
    project = ancestor / "proj"
    project.mkdir()

    # The READ side must NOT change: it is the file the harness loads.
    assert mcp_selfcheck._repo_root(project) is None
    assert mcp_selfcheck._project_layer(project, ".mcp.json") == ancestor / ".mcp.json"
    assert mcp_selfcheck.write_layer(project, ".mcp.json") is None

    config = harness_onboard.write_config("pi", project, scope="project")
    assert config.wrote == [str(project / ".mcp.json")], (
        f"a write must stay inside the project it was scoped to: {config.wrote}")
    assert harness_onboard._instruction_path(project, "AGENTS.md") == \
        project / "AGENTS.md"
    notes = harness_onboard.write_instructions("pi", project)
    assert notes.wrote == [str(project / "AGENTS.md")], notes.wrote

    assert (project / ".mcp.json").exists()
    assert (project / "AGENTS.md").exists()
    assert (ancestor / "AGENTS.md").read_text(encoding="utf-8") == \
        "# ancestor house rules\n", "the ancestor's instruction file was edited"
    assert json.loads((ancestor / ".mcp.json").read_text(encoding="utf-8")) == \
        {"mcpServers": {}}, "the ancestor's config was edited"


def test_the_backup_keeps_the_source_mode_and_is_never_0644(tmp_path, monkeypatch):
    """F4 — the 0600-backup fix landed with NO assertion, so a revert was silent.

    `write_text` creates at `0666 & ~umask`: a measured backup of a `0600
    ~/.claude.json` landed at **0644** with the key material inside — the installer
    widening the permissions of a file it was only reading.

    REDS ON REVERT: `os.open(..., mode)` + `chmod` -> `backup.write_text(before)`.
    """
    import stat as stat_mod

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    home.mkdir(parents=True, exist_ok=True)
    target = home / ".claude.json"
    target.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    target.chmod(0o600)

    out = harness_onboard.write_config("claude", home, scope="user")
    assert out.wrote == [str(target)], out.actions + out.refusals
    backup = target.with_suffix(target.suffix + ".bak-14607")
    assert backup.exists(), out.actions
    assert stat_mod.S_IMODE(backup.stat().st_mode) == 0o600, (
        "the backup of a 0600 config must stay 0600 — a 0644 copy leaks the key "
        f"material the installer was only reading (mode {oct(stat_mod.S_IMODE(backup.stat().st_mode))})")


def test_an_unprotectable_backup_refuses_instead_of_rewriting(tmp_path, monkeypatch):
    """F4 — the refuse-on-unprotectable arm, which had no assertion either.

    REDS ON REVERT: delete the `except OSError -> refusals + return out` arm; the
    config is then rewritten with no protected copy of what it held.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    target = home / ".claude.json"            # claude's USER-scope file
    before = json.dumps({"mcpServers": {}}, indent=2)
    target.write_text(before, encoding="utf-8")
    # The backup is `<file>.bak-14607` in the SAME directory, so a home directory
    # that refuses new entries is exactly "a backup that cannot be created".
    home.chmod(0o500)
    try:
        out = harness_onboard.write_config("claude", home, scope="user")
    finally:
        home.chmod(0o700)
    assert out.refusals, "an unprotectable backup must be a REFUSAL, not a write"
    assert out.wrote == []
    assert target.read_text(encoding="utf-8") == before, (
        "the config was rewritten after its backup could not be protected")
    assert not (home / ".claude.json.bak-14607").exists()


def test_a_green_line_for_pi_names_the_extension_that_loads_the_file(
        project, monkeypatch):
    """F5 — an `OK` for `pi` claimed a load the verifier cannot see.

    `HARNESS_CONFIG_PATHS["pi"]` is the `mcp-client` EXTENSION's convention; pi's
    built-in loader reads `.pi/mcp.json`. The probe grades the ENDPOINT, so an
    unqualified green said "your agent has tools" for a file pi may never load —
    and `onboard` then wrote the "reads go through the MCP" sentences on it.

    REDS ON REVERT: drop the `HARNESS_LOADER_NOTES` append in `check()` (or empty
    the `pi` note).
    """
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_x-14607")
    _write_config(project, {
        "type": "stdio", "command": "python3",
        "args": ["-m", "tortoise.mcp_bridge"],
        "env": {"TORTOISE_MCP_URL": "http://127.0.0.1:1/mcp"},
    })
    monkeypatch.setattr(mcp_selfcheck, "probe", lambda **kw: mcp_selfcheck.Verdict(
        status=mcp_selfcheck.OK, detail="live call succeeded"))

    verdict = mcp_selfcheck.check("pi", project_dir=project, live=True)
    assert verdict.status == mcp_selfcheck.OK
    assert "mcp-client" in verdict.detail and ".pi/mcp.json" in verdict.detail, (
        "a green for pi must say what LOADED the file and what it did not prove: "
        f"{verdict.detail!r}")


def test_a_headerless_http_entry_never_borrows_the_shells_credential(
        project, monkeypatch):
    """F6 — the false PASS in the credential layer.

    An http entry with NO `headers` sends nothing, but `credentials_present()`
    answered "a key exists in this shell" and the static pass read that as "the
    entry carries a credential"; `check(live=True)` then supplied the shell's key
    itself and printed `OK — live call succeeded` for a config whose own request
    goes out unauthenticated. Every earlier layer passed truthfully.

    REDS ON REVERT: restore `and not credentials_present(project_dir)` as the
    guard's second disjunct — the status is `resolves-unprobed` again.
    """
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_shell_only_14607")
    _write_config(project, {"type": "http", "url": "https://api.premiselabs.co/mcp/"})

    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status == mcp_selfcheck.NO_CREDENTIAL, verdict.line()
    assert "carries a credential" not in verdict.detail, verdict.detail
    assert "tt_shell_only_14607" not in verdict.line(), (
        "no credential VALUE may appear in a verdict")
    assert "env:TORTOISE_API_KEY" in verdict.detail, (
        "the detail must name WHERE the credential it is NOT sending came from, "
        f"so the reader knows the shell has one: {verdict.detail!r}")
    assert "headers" in verdict.fix and "bridge" in verdict.fix, (
        f"the fix must name both working shapes: {verdict.fix!r}")

    # …and the LIVE pass must not inject it either: headerless + loopback is the one
    # http shape that needs no credential, and it is graded as the harness would
    # send it (nothing).
    probed: list[bool] = []
    monkeypatch.setattr(mcp_selfcheck, "probe_probe", None, raising=False)
    monkeypatch.setattr(mcp_selfcheck, "probe", lambda **kw: probed.append(
        kw.get("send_credential", True)) or mcp_selfcheck.Verdict(
            status=mcp_selfcheck.OK, detail="live call succeeded"))
    _write_config(project, {"type": "http", "url": "http://127.0.0.1:1/mcp"})
    verdict = mcp_selfcheck.check("pi", project_dir=project, live=True)
    assert probed == [False], (
        "an http entry that declares no Authorization header must be probed as it "
        f"sends: with NO credential borrowed from the shell, got {probed}")
    assert verdict.status == mcp_selfcheck.OK


def test_a_declared_oauth_provider_is_not_no_credential(project, monkeypatch):
    """`oauth` is a credential carrier in this module — grade it as one.

    `HTTP_CREDENTIAL_FIELDS = ("headers", "oauth")` and the layer merge DROPS
    `oauth` when the destination moves precisely because it holds the credential.
    So a static pass that graded only the `Authorization` header reported
    `no-credential` for an entry the harness authenticates through OAuth: a false
    DOWN, and `onboard` refuses the instruction write while its `verdict.ok` is
    false. `_declares_credential` now accepts both shapes.

    REDS ON REVERT: `_declares_credential -> _header_carries_credential` in
    `classify` (the status becomes `no-credential`).
    """
    for name in mcp_selfcheck.CREDENTIAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(mcp_selfcheck, "CREDENTIAL_FILES", ())
    _write_config(project, {"type": "http",
                            "url": "https://api.premiselabs.co/mcp/",
                            "oauth": {"clientId": "abc", "scopes": ["mcp"]}})
    verdict = mcp_selfcheck.classify("pi", project_dir=project)
    assert verdict.status == mcp_selfcheck.RESOLVES_UNPROBED, verdict.line()
def test_a_base_with_NO_transport_key_is_INHERITED_not_sanitised_away(
        tmp_path, monkeypatch):
    """The harness drops a base only when a present-but-falsy transport is STRIPPED.

    `{lazy: true}` / `{timeoutMs: …}` / `{type: "http"}` carry no transport key at
    all, so they are KEPT and inherited — and then a repo entry declaring both
    transports is an ambiguous DELTA that is ignored. Treating those as "sanitised
    away" kept the repo entry and resolved it over `url` for a server the harness
    never loads (`has neither 'command' nor 'url'`).
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"tortoise": {
        "url": "https://api.premiselabs.co/mcp", "command": "python3"}}}),
        encoding="utf-8")
    for base in ({"lazy": True}, {"timeoutMs": 60000}, {"type": "http"}):
        (home / ".pi/agent/.mcp.json").write_text(
            json.dumps({"mcpServers": {"tortoise": base}}), encoding="utf-8")
        verdict = mcp_selfcheck.classify("pi", project_dir=repo)
        assert verdict.status == mcp_selfcheck.COMMAND_MISSING, (base, verdict.line())
        assert not verdict.ok


def test_destination_identity_normalises_a_default_port_for_ipv6_too():
    """`partition(":")` splits inside `[2001:db8::1]`, so `:443` never normalised."""
    assert mcp_selfcheck.same_destination(
        "https://[2001:db8::1]:443/mcp", "https://[2001:db8::1]/mcp"), (
        "a default port is not a destination change — dropping the inherited "
        "credential turned a working entry into `no-credential`")
    assert mcp_selfcheck.same_destination("https://[::1]:443/mcp", "https://[::1]/mcp")


def test_the_verdict_names_the_file_that_actually_holds_the_fault(tmp_path, monkeypatch):
    """A dropped BASE entry must not be attributed to the repo file.

    Measured before this arm: a list-valued `url` in `~/.pi/agent/.mcp.json` plus a
    `{timeoutMs}` delta in the repo produced "`<repo>/.mcp.json` … `url` is list
    (["…"])" — about a file that contains no `url` at all, i.e. the one sentence this
    module exists to get right pointing at the file that is fine.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)
    base_path = home / ".pi/agent/.mcp.json"
    base_path.write_text(json.dumps({"mcpServers": {"tortoise": {
        "url": ["https://api.premiselabs.co/mcp/"]}}}), encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"tortoise": {
        "timeoutMs": 90000}}}), encoding="utf-8")

    verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert verdict.status == mcp_selfcheck.COMMAND_MISSING, verdict.line()
    assert verdict.source == str(base_path), (
        f"the handed-back entry came from the BASE; the verdict named {verdict.source}")
    assert str(repo / ".mcp.json") in verdict.detail, (
        "the other layer consulted must be named, or the reader thinks the repo "
        "file declares the bad `url`")


def test_a_base_without_a_transport_names_the_layer_it_came_from(tmp_path, monkeypatch):
    """The effective entry can be the base's while the repo delta was ignored."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)
    base_path = home / ".pi/agent/.mcp.json"
    base_path.write_text(json.dumps({"mcpServers": {"tortoise": {"lazy": True}}}),
                         encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"tortoise": {
        "url": "https://api.premiselabs.co/mcp/", "command": "python3"}}}),
        encoding="utf-8")
    verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert verdict.status == mcp_selfcheck.COMMAND_MISSING, verdict.line()
    assert verdict.source == str(base_path), verdict.source
    assert str(repo / ".mcp.json") in verdict.detail, verdict.detail


def test_a_MERGED_entry_attributes_the_transport_field_to_its_own_layer(
        tmp_path, monkeypatch):
    """`source` is per-field after a merge, not "the most specific file present".

    Measured before per-field provenance: a base carrying `url` + an
    `Authorization: Bearer ${VAR}` header, with a `{timeoutMs}` delta in the repo
    (the harness's own documented delta recipe), produced `env-unset` naming the
    REPO file — which contains no header and no `${…}` at all.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_MCP_API_KEY", raising=False)
    (home / ".pi/agent").mkdir(parents=True)
    base_path = home / ".pi/agent/.mcp.json"
    base_path.write_text(json.dumps({"mcpServers": {"tortoise": {
        "url": "https://api.premiselabs.co/mcp/",
        "headers": {"Authorization": "Bearer ${TORTOISE_API_KEY}"}}}}),
        encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"tortoise": {
        "timeoutMs": 90000}}}), encoding="utf-8")

    verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert verdict.status == mcp_selfcheck.ENV_UNSET, verdict.line()
    assert verdict.source == str(base_path), (
        f"the header lives in the BASE; the verdict named {verdict.source}")
    assert str(repo / ".mcp.json") in verdict.detail, verdict.detail


def test_a_blank_transport_in_the_base_is_attributed_to_the_base(tmp_path, monkeypatch):
    """Same class, blank-transport branch: the `url` is the base's, not the repo's."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)
    base_path = home / ".pi/agent/.mcp.json"
    base_path.write_text(json.dumps({"mcpServers": {"tortoise": {
        "url": "   "}}}), encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"tortoise": {
        "timeoutMs": 90000}}}), encoding="utf-8")
    verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert verdict.status == mcp_selfcheck.COMMAND_MISSING, verdict.line()
    assert verdict.source == str(base_path), verdict.source
    assert "BLANK" in verdict.detail and str(repo / ".mcp.json") in verdict.detail


def test_a_transport_supplied_by_the_REPO_is_attributed_to_the_repo(tmp_path, monkeypatch):
    """The converse, on an input where provenance and the fallback DISAGREE.

    ⛔ `{timeoutMs}` over a base `{url}` cannot discriminate: `layers[0][0]` (the
    fallback) returns the same repo path, so this arm stayed green even with the
    delta→origin line deleted. A TRANSPORT SWITCH does discriminate — base supplies
    `url`, the repo supplies `command`, so the surviving transport is the repo's
    while the fallback would name… the repo too; what it actually pins is the
    `origin.pop` in the stdio branch, without which the verdict would name the
    base's dropped `url`.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)
    base_path = home / ".pi/agent/.mcp.json"
    base_path.write_text(json.dumps({"mcpServers": {"tortoise": {
        "url": "https://api.premiselabs.co/mcp/"}}}), encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    repo_cfg = repo / ".mcp.json"
    # ⛔ A command that EXISTS. With a non-existent one `classify` returns from the
    # command-missing arm, whose own `_origin_of("command")` masks the `source` the
    # merge produced — so the arm stayed green with the load-bearing `origin.pop` in
    # the stdio branch deleted (verified by mutation). A command that exists reaches
    # the `resolves-unprobed` return and exposes that `source` directly.
    repo_cfg.write_text(json.dumps({"mcpServers": {"tortoise": {
        "command": sys.executable,
        "args": ["-m", "tortoise.mcp_bridge"]}}}), encoding="utf-8")

    verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert verdict.transport == "stdio", verdict.line()
    assert verdict.status == mcp_selfcheck.RESOLVES_UNPROBED, verdict.line()
    assert verdict.source == str(repo_cfg), (
        "the surviving transport is the REPO's `command` (the base's `url` was "
        f"dropped by the switch); naming the base is the wrong-file defect — got "
        f"{verdict.source}")
    assert verdict.source != str(base_path)


def test_a_field_detail_names_the_layer_that_supplied_that_FIELD(tmp_path, monkeypatch):
    """`cwd` and an inherited `env` must name their own layer, not the transport's."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_MCP_API_KEY", raising=False)
    (home / ".pi/agent").mkdir(parents=True)
    base_path = home / ".pi/agent/.mcp.json"
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    repo_cfg = repo / ".mcp.json"

    # repo supplies the cwd; the base supplies everything else
    base_path.write_text(json.dumps({"mcpServers": {"tortoise": {
        "command": sys.executable, "args": ["-m", "tortoise.mcp_bridge"]}}}),
        encoding="utf-8")
    repo_cfg.write_text(json.dumps({"mcpServers": {"tortoise": {
        "cwd": "/nope-14617"}}}), encoding="utf-8")
    cwd_verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert cwd_verdict.status == mcp_selfcheck.CWD_MISSING, cwd_verdict.line()
    assert cwd_verdict.source == str(repo_cfg), cwd_verdict.source

    # the base supplies the `env` that carries the unset name; the repo restates
    # the launch identically, so the inherited `env` SURVIVES and is the base's
    base_path.write_text(json.dumps({"mcpServers": {"tortoise": {
        "command": sys.executable, "args": ["-m", "tortoise.mcp_bridge"],
        "env": {"TORTOISE_MCP_URL": "${PROBE_UNSET_14607}"}}}}), encoding="utf-8")
    repo_cfg.write_text(json.dumps({"mcpServers": {"tortoise": {
        "command": sys.executable, "args": ["-m", "tortoise.mcp_bridge"]}}}),
        encoding="utf-8")
    env_verdict = mcp_selfcheck.classify("pi", project_dir=repo)
    assert env_verdict.status == mcp_selfcheck.ENV_UNSET, env_verdict.line()
    assert env_verdict.source == str(base_path), (
        "the `${VAR}` is in the BASE's inherited `env`; the repo file has no env "
        f"at all — got {env_verdict.source}")


def test_the_command_arms_attribute_a_REPO_supplied_command(tmp_path, monkeypatch):
    """A repo-supplied COMMAND is attributed to the repo in both command arms.

    ⛔ Not a mutation-discriminating arm, and it must not claim to be: for a stdio
    entry `read_entry` returns the TRANSPORT's origin, and the stdio branch has
    already popped `url` from `origin` — so `source` IS `origin["command"]`, and a
    per-field `_origin_of("command")` in these arms could only ever return the same
    file. Measured over 81 base×repo configs: zero divergence. The two redundant
    calls were therefore REMOVED rather than left looking load-bearing; this arm
    keeps the behaviour pinned, and the per-field resolutions that ARE observable
    (`cwd`, and the `env` that carried an unset `${VAR}`) are covered by
    `test_a_field_detail_names_the_layer_that_supplied_that_FIELD`, which does red.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".pi/agent").mkdir(parents=True)
    (home / ".pi/agent/.mcp.json").write_text(json.dumps({"mcpServers": {
        "tortoise": {"url": "https://api.premiselabs.co/mcp/"}}}), encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    repo_cfg = repo / ".mcp.json"

    # command supplied by the REPO, missing on disk -> command-missing arm
    repo_cfg.write_text(json.dumps({"mcpServers": {"tortoise": {
        "command": "/nope-14617/python3",
        "args": ["-m", "tortoise.mcp_bridge"]}}}), encoding="utf-8")
    missing = mcp_selfcheck.classify("pi", project_dir=repo)
    assert missing.status == mcp_selfcheck.COMMAND_MISSING, missing.line()
    assert missing.source == str(repo_cfg), missing.source

    # command supplied by the REPO, exists but cannot import -> python-lacks arm
    fake = tmp_path / "python3"
    fake.write_text(
        "#!/bin/sh\n"
        'echo "ModuleNotFoundError: No module named \'tortoise\'" >&2\n'
        "exit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    repo_cfg.write_text(json.dumps({"mcpServers": {"tortoise": {
        "command": str(fake),
        "args": ["-m", "tortoise.mcp_bridge"]}}}), encoding="utf-8")
    lacks = mcp_selfcheck.classify("pi", project_dir=repo)
    assert lacks.status == mcp_selfcheck.PYTHON_LACKS_TORTOISE, lacks.line()
    assert lacks.source == str(repo_cfg), lacks.source
