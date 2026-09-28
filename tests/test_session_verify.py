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

The only test double is the `tortoise` console script the fired hook invokes:
a shim on PATH that delegates every capture call to the REAL CLI
(`tortoise.__main__.main`), so the transcript handed off by the installed hook
is parsed by the production parser. The seam, the event payload, the firing,
the parse, the receipt read, the session retrieval and the deletion are all the
production paths.

Each guard docstring names the mutation that turns it RED; the mutations were
verified RED individually.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tortoise import hook_install
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
        self.fail_get_session = False
        self.hide_session_on_get = False
        self.no_source = False
        self.omit_source_field = False
        self.no_receipt = False
        self.zero_extracted = False
        #: Write the per-harness receipt only from the Nth ``/v1/onboarding/state``
        #: read onward — models the server writing it AFTER the session row is
        #: visible (the abandoned handler completing), which is the real
        #: ordering (#4675).
        self.receipt_on_state_read: int | None = None
        self.state_reads = 0
        self.turn_override: dict[str, int] = {}
        #: Delay (s) between STORING a captured session and answering the POST
        #: — lets a test drive ``_fire`` past its timeout while the seam has
        #: really captured (the orphan-on-timeout reproduction).
        self.post_delay = 0.0
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
                if graph.post_delay:
                    time.sleep(graph.post_delay)
                self._send(200, {"session_id": sid, "turns": n,
                                 "extracted": 0 if graph.zero_extracted else 1,
                                 "extraction_mode": "llm:mock"})
                return
            self._send(404, {"detail": "not found"})

        def do_GET(self):
            if self.path == "/v1/onboarding/state":
                graph.state_reads += 1
                if (graph.receipt_on_state_read is not None
                        and graph.state_reads >= graph.receipt_on_state_read
                        and "session_capture_receipt_claude"
                        not in graph.receipts):
                    graph.receipts[capture_receipt_key("claude")] = graph.tick()
                self._send(200, {"onboarding": dict(graph.receipts)})
                return
            if self.path.startswith("/v1/sessions/"):
                sid = self.path.rsplit("/", 1)[-1]
                if graph.fail_get_session:
                    self._send(500, {"detail": "read refused (test)"})
                    return
                if sid in graph.sessions and not graph.hide_session_on_get:
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
                if sid not in graph.sessions:
                    # The real API 404s a DELETE for a session that does not
                    # exist; the double must too, or the ``DELETE 404`` cleanup
                    # path is unreachable from any test.
                    self._send(404, {"detail": "Session not found"})
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
    # A handler that is mid-``post_delay`` when the test ends writes to a
    # socket its client has abandoned; that is expected here, so do not let
    # socketserver print a traceback for it.
    server.handle_error = lambda *_a, **_k: None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield graph, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


# ── the `tortoise` the fired seam invokes ─────────────────────────────

#: The repo root the shim delegates into (tests/ → repo root).
_REPO_ROOT = Path(__file__).resolve().parents[1]

_FAKE_TORTOISE = '''\
#!/usr/bin/env python3
"""Test stand-in for the installed `tortoise` console script.

It is NOT a capture implementation.  Every capture invocation (`session
capture` / `sessions import`) is handed verbatim to the REAL CLI
(`tortoise.__main__.main`), so the transcript the installed hook passes is
parsed by the production parser and the turn count the test asserts comes from
that parse — never from a payload hardcoded here.  Only the hook's background
corpus re-index (`index`/`context`) and the unrelated probe beacon (`session
probe`) are skipped: they are not the path under verification and would touch
a real graph.

`TORTOISE_TEST_FORK_MARKER` / `TORTOISE_TEST_FORK_PROBE` / `TORTOISE_TEST_RELEASE_FILE`
make a guard's outcome depend on the hook FORKING its capture step, never on
a wall clock: the marker records the instant the fork happened (so a
calibration run can measure the hook prologue at the CURRENT load), probe mode
then exits without capturing, and a release file makes the forked child BLOCK
until the test lets it capture — so a late POST is guaranteed by the test, not
raced against the fire timeout.
"""
import os
import sys
import time

argv = sys.argv[1:]
if argv and (argv[0] == "index" or argv[0] == "context"
             or argv[0] == "session" and len(argv) > 1 and argv[1] == "probe"):
    sys.exit(0)

# The hook has FORKED this capture step: record it, and let a calibration run
# (probe mode) exit without capturing, or a real run block until released.
_FORK_MARKER = os.environ.get("TORTOISE_TEST_FORK_MARKER")
_RELEASE = os.environ.get("TORTOISE_TEST_RELEASE_FILE")
if argv and argv[0] in ("session", "sessions") and (_FORK_MARKER or _RELEASE):
    if _FORK_MARKER:
        try:
            with open(_FORK_MARKER, "w") as _fh:
                _fh.write(str(time.monotonic()))
        except OSError:
            pass
    if os.environ.get("TORTOISE_TEST_FORK_PROBE") == "1":
        sys.exit(0)
    if _RELEASE:
        _until = time.monotonic() + 300
        while not os.path.exists(_RELEASE) and time.monotonic() < _until:
            time.sleep(0.05)

sys.path.insert(0, "__SRC__")
from tortoise.__main__ import main  # noqa: E402

raise SystemExit(main(argv))
'''


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A hermetic install + fake CLI + loopback API, all under a temp HOME."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "tortoise"
    fake.write_text(_FAKE_TORTOISE.replace("__SRC__", str(_REPO_ROOT)),
                    encoding="utf-8")
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


def _verify(hosted, home, harness, root, *, timeout=None, extra_env=None,
            **kw):
    if timeout is None:
        timeout = _derived_fire_timeout(home, root, harness)
    _graph, api_url = hosted
    env = {**os.environ, "HOME": str(home),
           "TORTOISE_API_KEY": "tt_test", "TORTOISE_API_URL": api_url,
           # #3682: capture is opt-in — the credential alone no longer consents.
           # This file exists to exercise the capture chain, so it opts in.
           "TORTOISE_CAPTURE": "1"}
    env.update(extra_env or {})
    return verify_session_capture(
        harness, api_key="tt_test", api_url=api_url, home=home,
        install_dir=root, timeout=timeout,
        env=env,
        **kw)


def _hook_fork_seconds(home, root, harness, tmp_path):
    """How long the installed hook takes to reach its capture fork — NOW.

    Fires the registered command once with the fake CLI told to record its
    arrival (``TORTOISE_TEST_FORK_MARKER``) and exit WITHOUT capturing
    (``TORTOISE_TEST_FORK_PROBE``), so no probe session is created.  A guard
    that must kill the hook AFTER its fork derives its fire timeout from this
    measurement, so the kill lands on the right side of the fork no matter
    how slow the box is — the guard tests the mechanism, never a wall-clock
    constant (a hardcoded 1s false-REDed at load avg 38, #3809 rr4).
    """
    import tortoise.session_verify as sv

    command = sv._registered_capture_command(harness, root)
    assert command, "the installed seam must register a capture command"
    transcript = sv._write_probe_transcript(harness, tmp_path / "calibration")
    payload = sv._probe_payload(
        harness, "verify-calibration", transcript, root)
    env = {
        **os.environ,
        "HOME": str(home),
        "TORTOISE_TEST_FORK_MARKER": str(tmp_path / "calibration-fork-marker"),
        "TORTOISE_TEST_FORK_PROBE": "1",
        # #3682: without this the hook declines at the consent gate, so the
        # calibration measures an early exit instead of the capture fork.
        "TORTOISE_CAPTURE": "1",
    }
    started = time.monotonic()
    subprocess.run(
        ["/bin/bash", "-c", command], input=json.dumps(payload),
        cwd=str(root), env=env, capture_output=True, text=True, timeout=600)
    return time.monotonic() - started


#: The hook's time-to-fork per SEAM, measured once at the CURRENT load and
#: reused.  The key is the registered command plus the hook bytes, not the
#: harness alone: keying on the harness alone let a guard that short-circuits
#: the hook (``exit 3``) poison every later landing test's budget (#3809 rr6).
#: Fresh temp roots install the shipped hooks byte-identically, so the honest
#: case still pays for one calibration run per harness.
_FORK_SECONDS: dict[tuple[str, str], float] = {}


def _hook_byte_sources(harness: str, root: Path, command: str) -> list[Path]:
    """The on-disk hook files whose bytes feed this seam's fingerprint.

    The raw command path is taken literally for a command with no ``$`` (a
    guard may synthesise a bare path).  A command containing ``$`` never
    contributes that raw path: the literal string is not the file a shell
    would run, so keying on it would key on a path that did not execute.

    The file a registered command names is resolved through the installer's
    OWN classifier — ``hook_install._invokes_script``, the same predicate
    ``detect_install``/``registered_commands`` use.  A token the classifier
    matched by its ``$VAR`` suffix form is NOT resolvable here, so it
    contributes no source either.
    """
    import tortoise.hook_install as hook_install

    sources: list[Path] = []
    if command and "$" not in command:
        raw = Path(command)
        sources.append(raw if raw.is_absolute() else Path(root) / raw)
    layout = hook_install.get_layout(harness)
    for spec in layout.scripts:
        matched: list[str] = []
        if not hook_install._invokes_script(
                command, spec.name, layout.hooks_dir, root, matched=matched):
            continue
        token = matched[0]
        if token.startswith("$"):
            continue  # a ``$VAR`` value is unknowable — never guess a path
        sources.append(
            Path(token) if os.path.isabs(token) else Path(root) / token)
    seen: set[str] = set()
    unique: list[Path] = []
    for source in sources:
        if str(source) not in seen:
            seen.add(str(source))
            unique.append(source)
    return unique


def _seam_fingerprint(harness: str, root: Path) -> tuple[str, str]:
    """Identity of the seam this root fires: the command AND the hook bytes.

    The key is this harness, the root-canonicalized command, and the bytes of
    every located hook source — so a locally edited hook, or a substituted
    command (``exit 7``), gets its own key, never a landing test's throttled
    value.

    The bytes leg hashes what ``_hook_byte_sources`` finds, so a command that
    quotes the path (Codex/Cursor) or prefixes a launcher (``/bin/sh <hook>``)
    does not drop it.  When no candidate can be read, a marker is mixed in
    under its own tag, distinct from a bytes leg.
    """
    import tortoise.session_verify as sv

    command = sv._registered_capture_command(harness, root) or ""
    # Canonicalize the per-install root prefix away (Codex registers an
    # absolute path), so two temp installs of the SAME shipped hook share one
    # calibration while a substituted command still differs.
    canonical = command.replace(str(root), "<root>")
    digest = hashlib.sha256(canonical.encode("utf-8"))
    hashed = False
    for artifact in _hook_byte_sources(harness, root, command):
        try:
            blob = artifact.read_bytes()
        except OSError:
            # A candidate that is not the invoked file (e.g. the raw quoted
            # token); ``hashed`` decides whether EVERY candidate missed.
            continue
        digest.update(b"\0bytes\0")
        digest.update(len(blob).to_bytes(8, "big"))
        digest.update(blob)
        hashed = True
    if command and not hashed:
        digest.update(b"\0marker\0")
        digest.update(len(canonical).to_bytes(8, "big"))
        digest.update(canonical.encode("utf-8"))
    return (harness, digest.hexdigest())


def _prologue_seconds(home, root, harness):
    """The hook's time-to-fork at the current load, cached per seam."""
    key = _seam_fingerprint(harness, root)
    if key not in _FORK_SECONDS:
        _FORK_SECONDS[key] = _hook_fork_seconds(home, root, harness, home)
    return _FORK_SECONDS[key]


def _derived_fire_timeout(home, root, harness):
    """A fire/observation timeout derived from the hook's own prologue.

    Never a wall-clock constant: the prologue is measured at the CURRENT load
    (``_prologue_seconds``), and the timeout is a multiple of it plus a margin
    covering the CLI import/parse/POST that follows the fork.  A root with no
    fireable seam (the missing-install guard) and a harness this command may
    not fire (Cursor/Pi) never reach the fire path, so they get a nominal
    timeout the fire path never consumes.
    """
    import tortoise.session_verify as sv

    if not sv.HEADLESS_FIRABLE.get(harness):
        return 20.0
    if not sv._registered_capture_command(harness, root):
        return 20.0
    return 2.0 * _prologue_seconds(home, root, harness) + 15.0


def test_guard_prologue_cache_is_keyed_on_the_seam_not_the_harness(
        monkeypatch, tmp_path):
    """A short-circuited hook cannot poison a later landing test's budget.

    ``_prologue_seconds`` caches per SEAM (registered command + hook bytes),
    not per harness.  A guard that rewrites the hook to ``exit 3`` — or swaps
    the command for ``exit 7`` — measures a near-zero prologue; a harness-only
    key then handed that value to every later claude landing test as its fire
    budget, so it raced a wall clock again (#3809 rr6).  The seeder runs
    first, then the intact seam is asked for ITS prologue.

    Mutation: key ``_FORK_SECONDS`` on ``harness`` alone — the intact seam
    reuses the tampered seam's 0.05 s and this REDs (5.0 expected).
    """
    this = sys.modules[__name__]
    import tortoise.session_verify as sv

    monkeypatch.setattr(this, "_FORK_SECONDS", {})
    monkeypatch.setattr(
        sv, "_registered_capture_command",
        lambda _h, root: str(Path(root) / "session-end.sh"))

    def _measured(_home, root, _harness, _tmp):
        text = (Path(root) / "session-end.sh").read_text()
        return 0.05 if "exit 3" in text else 5.0

    monkeypatch.setattr(this, "_hook_fork_seconds", _measured)

    home = tmp_path / "home"
    home.mkdir()
    seeder = tmp_path / "seeder"
    seeder.mkdir()
    (seeder / "session-end.sh").write_text("set -euo pipefail\nexit 3\n")
    assert this._prologue_seconds(home, seeder, "claude") == pytest.approx(0.05)

    landing = tmp_path / "landing"
    landing.mkdir()
    (landing / "session-end.sh").write_text("set -euo pipefail\n")
    assert this._prologue_seconds(home, landing, "claude") == pytest.approx(5.0)


def test_guard_hook_bytes_are_hashed_for_quoted_and_multitoken_commands(
        monkeypatch, tmp_path):
    """A quoted or launcher-prefixed command still keys on the hook bytes.

    ``_seam_fingerprint``'s bytes leg must hash the file the command ACTUALLY
    executes.  Codex/Cursor register the path ``shlex.quote``d (so the quoted
    token is not ``Path(...).is_absolute()``), and the project's own
    ``/bin/sh <hook>`` shape is multi-token (so no prefix-join resolves); under
    the raw prefix join both missed, the bare ``except OSError`` dropped the
    bytes, and a SHIPPED and a TAMPERED hook hashed the same key — the
    cross-seam poisoning rr7 claimed closed.  Reads no process and fires
    nothing: the fingerprint is a pure function of the install.

    Mutation: resolve the hook with ``Path(command)`` alone (prefix-join when
    not absolute) — the quoted Codex/Cursor and the multi-token commands miss,
    the tampered and shipped keys become equal and this REDs.
    """
    this = sys.modules[__name__]
    import tortoise.hook_install as hook_install
    import tortoise.session_verify as sv

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)

    def _tamper(path: Path) -> None:
        path.write_bytes(path.read_bytes() + b"\n# tampered\n")

    def _codex_root(home: Path) -> Path:
        assert install_capture("codex", home=home).ok
        return hook_install.default_root(
            hook_install.get_layout("codex"), home)

    # ── Codex: ``shlex.quote``d ABSOLUTE path, root carrying a space so the
    # quoting is load-bearing.  Two shipped installs in different spacey roots
    # MUST share one key (the bytes hash, not the root); tampering one MUST NOT.
    root_a = _codex_root(tmp_path / "home with space")
    root_b = _codex_root(tmp_path / "home with a second space")
    quoted = sv._registered_capture_command("codex", root_a)
    assert quoted and quoted.startswith("'") and " " in quoted, quoted
    shipped = this._seam_fingerprint("codex", root_a)
    assert this._seam_fingerprint("codex", root_b) == shipped
    _tamper(root_a / "hooks" / "tortoise-session-end.sh")
    assert this._seam_fingerprint("codex", root_a) != shipped

    # ── Claude: the project's own multi-token ``/bin/sh <hook>`` shape.  Two
    # shipped roots share a key; tampering the hook the launcher runs does not.
    project_a = tmp_path / "proj a"
    project_b = tmp_path / "proj b"
    project_a.mkdir()
    project_b.mkdir()
    for project in (project_a, project_b):
        assert install_capture("claude", root=project, home=tmp_path).ok
    monkeypatch.setattr(
        sv, "_registered_capture_command",
        lambda _h, _root: "/bin/sh .claude/hooks/session-end.sh")
    claude_shipped = this._seam_fingerprint("claude", project_a)
    assert this._seam_fingerprint("claude", project_b) == claude_shipped
    _tamper(project_a / ".claude" / "hooks" / "session-end.sh")
    assert this._seam_fingerprint("claude", project_a) != claude_shipped


def test_guard_unresolvable_var_token_never_keys_on_a_guessed_path(
        monkeypatch, tmp_path):
    """A ``$VAR``-form command keys on its marker, never on a guessed path.

    ``_invokes_script`` accepts a ``$VAR`` token via its suffix branch, but the
    variable's value is unknowable to ``_hook_byte_sources``, so the token must
    contribute NO source.  Mapping the hit to ``root/<hooks_dir>/<script>``
    would hash a file bash may never run and stay blind to the file ``$VAR``
    really names — the rr9-1 silent skip.  Tampering the guessed file must
    therefore leave the ``$VAR`` key UNCHANGED, while a command that resolves
    to that same file DOES key on its bytes.

    Mutation: map every ``_invokes_script`` hit to
    ``Path(root) / layout.hooks_dir / spec.name`` (the old guess) — tampering
    the guessed file moves the ``$VAR`` key and this REDs.
    """
    this = sys.modules[__name__]
    import tortoise.hook_install as hook_install
    import tortoise.session_verify as sv

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    home = tmp_path / "home"
    assert install_capture("codex", home=home).ok
    root = hook_install.default_root(hook_install.get_layout("codex"), home)
    hook = root / "hooks" / "tortoise-session-end.sh"
    assert hook.is_file()

    var_command = "$OTHER_DIR/hooks/tortoise-session-end.sh"
    assert hook_install._invokes_script(
        var_command, "tortoise-session-end.sh", "hooks", root) is True

    monkeypatch.setattr(sv, "_registered_capture_command",
                        lambda _h, _root: var_command)
    var_key = this._seam_fingerprint("codex", root)
    hook.write_bytes(hook.read_bytes() + b"\n# tampered\n")
    assert this._seam_fingerprint("codex", root) == var_key

    # A command that DOES resolve to that same file keys on its bytes.
    monkeypatch.setattr(
        sv, "_registered_capture_command",
        lambda _h, r: str(Path(r) / "hooks" / "tortoise-session-end.sh"))
    resolved = this._seam_fingerprint("codex", root)
    hook.write_bytes(hook.read_bytes() + b"\n# again\n")
    assert this._seam_fingerprint("codex", root) != resolved


def test_guard_unresolved_marker_is_tagged_apart_from_hook_bytes(
        monkeypatch, tmp_path):
    """A missing hook and a hook imitating the marker never collide.

    ``_seam_fingerprint`` mixes a marker when no candidate can be read and a
    bytes leg when one can.  If the marker were a bare constant suffix, a hook
    whose bytes are exactly that suffix plus the canonical command would hash
    identically to the hook being ABSENT — two distinct install states sharing
    one calibration key (rr9-2).  The two legs are tagged apart, so the states
    are distinguishable.

    Mutation: encode the bytes leg as ``b"\\0" + blob`` and the marker as
    ``b"\\0unresolved\\0" + canonical`` (the old scheme) — the two fingerprints
    below compare EQUAL and this REDs.
    """
    this = sys.modules[__name__]
    import tortoise.session_verify as sv

    monkeypatch.setenv("HOME", str(tmp_path))
    assert install_capture("claude", root=tmp_path, home=tmp_path).ok
    command = sv._registered_capture_command("claude", tmp_path)
    assert command
    canonical = command.replace(str(tmp_path), "<root>")
    hook = tmp_path / ".claude" / "hooks" / "session-end.sh"
    assert hook.is_file()

    hook.write_bytes(b"unresolved\x00" + canonical.encode("utf-8"))
    present = this._seam_fingerprint("claude", tmp_path)
    hook.unlink()
    absent = this._seam_fingerprint("claude", tmp_path)
    assert present != absent


def test_default_fire_timeout_is_derived_not_a_wall_clock(monkeypatch, tmp_path):
    """``_verify``'s default timeout comes from the hook's own prologue.

    Mutation: restore ``timeout=20.0`` as ``_verify``'s default (a wall-clock
    constant) — 20.0, not the derived 2 × 5.0 + 15.0 = 25.0, reaches the fire
    and this REDs.
    """
    this = sys.modules[__name__]
    import tortoise.session_verify as sv

    seen: list[float] = []

    def _spy(*_a, timeout, **_k):
        seen.append(timeout)
        return {"exit_code": 0}

    monkeypatch.setattr(this, "verify_session_capture", _spy)
    monkeypatch.setattr(this, "_FORK_SECONDS", {})
    monkeypatch.setattr(this, "_hook_fork_seconds", lambda *_a, **_k: 5.0)
    monkeypatch.setattr(sv, "_registered_capture_command",
                        lambda *_a, **_k: "true")
    this._verify(({}, "http://127.0.0.1:1"), tmp_path / "home",
                 "claude", tmp_path / "root")
    assert seen == [25.0]


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
    # The report is what `--json` serializes: the launch outcome is a plain
    # string and carries no un-serializable object.
    assert json.loads(json.dumps(report))["fire"]["outcome"] == "exited"


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
    (the ``Popen`` spawn raises ``OSError``) — the four on-disk checks pass,
    but the FIRING leg fails, so ``installed`` FAILs and nothing is captured.

    The launch outcome is ``NOT_LAUNCHED``, so the SAME report must not claim
    the seam ran: ``captured`` says the capture definitively did not happen,
    and cleanup is a definite "nothing was left behind" (no ``in_flight``).
    Mutation: key the fire-failure message on the success flag (the old "the
    installed seam was launched but the fire did not complete") — the
    "never launched" assertions RED.  Reverting the cleanup certainty to a
    launched-fire shape (``in_flight`` on any 404) REDs the cleanup asserts.
    """
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    import tortoise.session_verify as sv

    def _boom(*_a, **_k):
        raise OSError("cannot exec (test)")

    monkeypatch.setattr(sv.subprocess, "Popen", _boom)
    graph, _url = hosted
    # Popen is replaced, so the prologue cannot be measured and the outcome
    # is deterministic (no process ever exists): a fixed budget, never a race.
    report = _verify(hosted, home, "claude", root, timeout=20.0)
    assert report["exit_code"] == EXIT_BROKEN, report
    assert report["links"]["installed"]["status"] == "FAIL"
    assert "cannot execute the seam" in report["links"]["installed"]["detail"]
    assert report["fire"]["outcome"] == "not-launched", report["fire"]
    captured = report["links"]["captured"]["detail"]
    assert "could not be launched" in captured, captured
    assert "definitely does not exist" in captured, captured
    assert "was launched" not in captured, captured
    cleanup = report["cleanup"]
    assert cleanup["launch"] == "not-launched", cleanup
    assert cleanup.get("in_flight") is None, cleanup
    assert "nothing was left behind" in cleanup["detail"], cleanup
    assert graph.posts == []


def test_guard_never_launched_is_not_in_flight_for_the_detaching_shape(
        hosted, setup, monkeypatch):
    """Nothing was launched, so nothing can be in flight — for Codex too.

    The detaching seam's shape must not turn a pre-exec failure into "still in
    flight": no worker was ever detached, so the report is the same definite
    "never launched" as any other harness.

    Mutation: set ``in_flight`` on the 404 branch whenever the harness is in a
    static detach table (or whenever ``launch is not None`` is not consulted)
    — Codex reports ``in_flight=True`` with no worker and this REDs.
    """
    home, _bindir, _fake = setup
    root = _install(home, "codex")
    import tortoise.session_verify as sv

    monkeypatch.setattr(
        sv.subprocess, "Popen",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("cannot exec (test)")))
    graph, _url = hosted
    # Popen is replaced — a deterministic "no process" for every harness.
    report = _verify(hosted, home, "codex", root, timeout=20.0)
    assert report["exit_code"] == EXIT_BROKEN, report
    assert report["fire"]["outcome"] == "not-launched", report["fire"]
    assert report["links"]["captured"]["detail"].startswith(
        "the seam could not be launched"), report["links"]["captured"]
    cleanup = report["cleanup"]
    assert cleanup.get("in_flight") is None, cleanup
    assert "nothing was left behind" in cleanup["detail"], cleanup
    assert graph.posts == []


def test_guard_oserror_after_spawn_is_not_reported_as_never_launched(
        hosted, setup, monkeypatch, tmp_path):
    """An ``OSError`` from the WAIT is not proof the seam never ran.

    Only a SPAWN failure proves no process ever existed.  ``subprocess.run``
    wrapped the spawn AND the wait in one ``except OSError``, so an error
    raised after the seam had already run was reported as "the seam could not
    be launched ... the session definitely does not exist" while its capture
    sat on the server — the contradiction this guard pins down.  The catch is
    now scoped to ``Popen``, so the post-spawn error propagates and cleanup
    still deletes what the fire wrote.

    Mutation: wrap the whole spawn+wait in ``except OSError`` again (the
    ``subprocess.run`` shape) — this monkeypatched post-spawn error is mapped
    to ``NOT_LAUNCHED`` and the guard REDs (``_verify`` returns instead of
    raising).

    The fire budget is DERIVED from the hook's own prologue, measured BEFORE
    ``Popen`` is replaced (the calibration path is itself a ``Popen`` caller).
    A fixed 20 s raced the hook's real foreground capture — 6.68 s warm vs
    15.47 s isolated at load ~22 — and a lost race made the real
    ``communicate`` raise ``TimeoutExpired`` first, so ``_fire`` returned
    ``TIMED_OUT`` instead of letting the post-spawn error propagate (#3809
    rr6).
    """
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    import tortoise.session_verify as sv

    # Measure the prologue BEFORE replacing Popen: subprocess.run (the
    # calibration path) is itself a Popen caller.
    prologue = _hook_fork_seconds(home, root, "claude", tmp_path)
    timeout = 2.0 * prologue + 15.0

    real_popen = subprocess.Popen

    class _SpawnedThenWaitFails:
        """A real child that runs, then the WAIT reports an OS error."""

        def __init__(self, *a, **k):
            self._proc = real_popen(*a, **k)

        def communicate(self, *a, **k):
            self._proc.communicate(*a, **k)  # the seam really ran + captured
            raise BrokenPipeError("the wait failed after the seam ran")

        def kill(self):
            self._proc.kill()

        def wait(self, *a, **k):
            return self._proc.wait(*a, **k)

        def __getattr__(self, name):
            return getattr(self._proc, name)

    monkeypatch.setattr(sv.subprocess, "Popen", _SpawnedThenWaitFails)
    # The real hook's foreground capture must land inside the derived budget;
    # the wait then raises and the error propagates.
    with pytest.raises(OSError):
        _verify(hosted, home, "claude", root, timeout=timeout)
    assert len(graph.posts) == 1, "the seam really ran and captured"
    assert graph.sessions == {}, "cleanup still deleted what the fire wrote"


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
    the session is retrievable with the expected turns.

    This is #3809's Scope §2 predicate ("a ``session_capture_receipt_<harness>``
    advanced AND the session is retrievable by id with the expected turns") and
    PR #4182's documented guard. #4675 does NOT change it; it changes when the
    receipt is READ. The DERIVED timeout is used, not a wall-clock constant:
    the same `timeout` bounds the FIRE, and this file's own doctrine forbids
    hardcoding one (a 3.0 constant killed the seam's prologue on a loaded box
    and false-REDed this guard — #3809 rr4).
    """
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    graph.no_receipt = True
    report = _verify(hosted, home, "claude", root)
    assert report["exit_code"] == EXIT_BROKEN, report
    captured = report["links"]["captured"]
    assert captured["status"] == "FAIL"
    assert "did not advance" in captured["detail"]
    assert captured["receipt_advanced"] is False


def test_captured_is_proven_when_the_receipt_advances_after_the_session_row(
        hosted, setup):
    """#4675, the real defect: the receipt is written by a handler the
    transport bound ABANDONED, so it lands AFTER the session row is visible —
    measured live, a seam fired 08:21:02 and `session_capture_receipt_cursor`
    was written 08:21:06.

    Reading it exactly once, on the first sighting of the session row, reported
    "did not advance" for a capture that had landed. The predicate is unchanged
    (#3809): the observation now polls both legs to the same deadline.

    MUTATION THAT REDS THIS: read the receipt once before the loop (the old
    behaviour) — the link FAILs for a capture that landed.

    The session row is visible from the first state read and the receipt is not
    written until the fifth, so a single pre-loop read cannot see it — and the
    turn count has already settled at its expected value, which is the case an
    early break must NOT take.
    """
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    # The POST must not write the receipt (that is the ordering under test) and
    # the delayed write must arrive after the session row is already visible.
    graph.no_receipt = True
    # Later than the second read, so the turn count has SETTLED at the expected
    # value while the receipt is still outstanding — the early-break bug that
    # would end the window on a settled-but-correct count.
    graph.receipt_on_state_read = 5
    report = _verify(hosted, home, "claude", root)
    captured = report["links"]["captured"]
    assert graph.state_reads >= 5, graph.state_reads
    assert captured["status"] == "PROVEN", report
    assert captured["receipt_advanced"] is True, captured
    assert captured["turns"] == 2, captured
    assert report["links"]["memory"]["status"] == "PROVEN", report["links"]


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


def _names_pi(text: str) -> bool:
    """True when ``text`` names Pi — bare (``pi`` / ``PI``) or possessive
    (``Pi's``).  A comment block that refers to the Pi ruling only possessively
    must still be scanned, or a wrapped line can carry a banned absolute
    unseen (#4620)."""
    for word in text.split():
        token = word.strip("`*_.,;:()<>\"'").lower().replace("\u2019", "'")
        if token.endswith("'s"):
            token = token[:-2]
        if token == "pi":
            return True
    return False


def test_pi_is_honestly_unverifiable(hosted, setup):
    """Pi's seam is an in-process TypeScript extension — not a command this
    verifier can execute.  Installed is UNVERIFIABLE (present, not fired),
    never a fabricated pass.  The reason must name the suite that DOES exercise
    it and the manual-only residual, and must never restate the over-broad
    absolute (the seam's handlers ARE fired headlessly by its own suite, and
    `pi -p` is non-interactive)."""
    home, _bindir, _fake = setup
    root = _install(home, "pi")
    report = _verify(hosted, home, "pi", root)
    assert report["exit_code"] == EXIT_UNVERIFIABLE
    assert report["links"]["installed"]["status"] == "UNVERIFIABLE-IN-CI"
    detail = report["links"]["installed"]["detail"]
    assert "extension" in detail
    assert "tortoise-capture.test.ts" in detail
    assert "tests/test_pi_capture_hooks.py" in detail
    # ⛔ Not `"installed"`: `_unverifiable_link` builds the detail as
    # "<link> not exercised: <reason>", and the link IS "installed" — so that
    # substring is supplied by the PREFIX and the assertion could never fail
    # (#4620 review).  Anchor on a phrase only the reason can supply.
    assert "installed artifact" in detail
    assert "manual-only" in detail
    assert "not firable by this command" in detail
    # The over-broad absolutes must never return: the seam IS fired headlessly
    # by its own suite (the source seam) and by the installed-artifact probe.
    # Scan the PI RULING's own text, never the whole module.  The phrases are
    # over-broad ABOUT PI, and one of them — "no headless trigger" — is TRUE of
    # Cursor (`UNVERIFIABLE_REASON["cursor"]` says exactly that).  The ruling
    # lives in the module docstring, the `UNVERIFIABLE_REASON["pi"]` value, and
    # the comments that state it; another harness's text is out of scope.
    # Comment blocks are JOINED before matching so a phrase wrapped across two
    # `#` lines is still seen.
    from tortoise import session_verify as _sv

    absolutes = (
        "cannot be executed headlessly",
        "cannot be fired headlessly",
        "no headless trigger",
    )
    for phrase in absolutes:
        assert phrase not in detail, phrase
    source = inspect.getsource(_sv)

    def _comment_blocks(text: str) -> list[str]:
        blocks: list[str] = []
        current: list[str] = []
        for raw in text.splitlines():
            stripped = raw.lstrip()
            if stripped.startswith("#"):
                # Strip the marker INCLUDING Sphinx's ``#:`` colon: leaving it
                # in injects " : " at every join, so a phrase wrapped across
                # two ``#:`` lines would match nothing.
                current.append(stripped.lstrip("#").lstrip(": ").rstrip())
            else:
                if current:
                    blocks.append(" ".join(current))
                    current = []
        if current:
            blocks.append(" ".join(current))
        return blocks

    doc = _sv.__doc__ or ""
    # Non-vacuity: an empty or truncated read must not pass this pin trivially.
    # Anchor on STABLE identifiers, never on copy this pin does not own — a
    # legitimate rewording of the ruling must not be reported as a failed read
    # (#4620 review).
    assert "HONEST DISCLOSURE" in doc, "module docstring not read — pin is vacuous"
    assert "def resolve_install_root" in source, "module source not read — pin is vacuous"
    pi_comments = [block for block in _comment_blocks(source) if _names_pi(block)]
    assert pi_comments, "no Pi ruling comment matched — pin is vacuous"
    for text in [doc, _sv.UNVERIFIABLE_REASON["pi"], *pi_comments]:
        for phrase in absolutes:
            assert phrase not in text, (phrase, text[:90])


def test_pi_stale_install_is_reported_not_unverifiable(hosted, setup):
    """The #4680 defect, at the surface that hid it: a present-but-STALE Pi
    seam used to report ``UNVERIFIABLE-IN-CI`` — indistinguishable from "fine"
    — while capturing with older logic.  It must instead be a BLOCKING
    install finding (``stale-artifact``/``unversioned-artifact``), the same
    way the three shell seams already report ``stale-script``, and nothing may
    be fired for it.

    Mutation: restore the pre-#4680 ``_static_findings`` Pi branch (existence
    only, no version comparison) — this REDs: a tampered seam reads
    UNVERIFIABLE and no finding names the staleness.
    """
    home, _bindir, _fake = setup
    root = _install(home, "pi")
    # The pre-contract shape: a REAL installed seam with its marker stripped —
    # present, ours, unmarkered.  The basename is DERIVED from the contract
    # registry, never re-typed, so a rename of the artifact cannot leave this
    # test writing a file the installer/detector do not use (#4680 review).
    seam = root / hook_install.ARTIFACT_CONTRACTS["pi"].install_name
    seam.write_text(
        "\n".join(line for line in seam.read_text(encoding="utf-8").splitlines()
                  if not line.startswith("// tortoise-hook-version:")) + "\n",
        encoding="utf-8")
    graph, _url = hosted
    report = _verify(hosted, home, "pi", root)
    assert report["links"]["installed"]["status"] == "FAIL", report["links"]
    detail = report["links"]["installed"]["detail"]
    assert "not current" in detail
    assert "unversioned-artifact" in detail
    assert "tortoise install pi" in detail, (
        "verify must name the sanctioned repair for the state it reports")
    assert report["exit_code"] == EXIT_BROKEN
    assert graph.posts == [], "nothing may be captured for a stale install"


def test_pi_ruling_matcher_covers_the_possessive():
    """A comment block whose only Pi reference is the possessive ``Pi's`` must
    still count as naming the Pi ruling — otherwise a wrapped line carrying a
    banned absolute is never scanned (#4620)."""
    assert _names_pi("Pi's seam cannot be executed headlessly")
    assert _names_pi("PI's seam cannot be fired headlessly")
    assert _names_pi("must never restate the over-broad absolute about Pi")
    assert not _names_pi("Cursor's seam cannot be executed headlessly")


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


# ── P1: the probe is deleted on the capture, not on the read ──────────────


def test_guard_read_failure_after_capture_still_deletes_the_probe(
        hosted, setup):
    """A GET that 500s AFTER a successful capture must not orphan the probe.

    Mutation: key cleanup on ``created=detail is not None`` and drop the
    ``try/finally`` — the ``_ApiError`` escapes before ``_cleanup``,
    ``POSTS=1``, ``graph.deletes == []`` and this REDs.
    """
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    graph.fail_get_session = True
    report = _verify(hosted, home, "claude", root)
    assert len(graph.posts) == 1, "the seam must really have captured"
    assert report["links"]["captured"]["status"] == "FAIL"
    assert report["cleanup"]["attempted"] is True, report["cleanup"]
    assert report["cleanup"]["deleted"] is True
    assert report["session_id"] in graph.deletes
    assert graph.sessions == {}, "the probe session must not remain"
    assert report["exit_code"] == EXIT_BROKEN


def test_guard_never_claims_nothing_to_delete_after_a_capture(
        hosted, setup, tmp_path):
    """A persistent GET 404 must not stop the probe from being deleted.

    Deletion is keyed on the fired seam, not the observation.  The fire
    timeout is derived from the hook's measured prologue so the capture has
    landed before ``_fire`` returns at any load (a fixed 6s false-REDed on a
    loaded box).  Mutation: key cleanup on ``detail is not None`` — the probe
    is never deleted (POSTS=1) and this REDs.
    """
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    graph.hide_session_on_get = True
    prologue = _hook_fork_seconds(home, root, "claude", tmp_path)
    report = _verify(hosted, home, "claude", root,
                     timeout=2.0 * prologue + 15.0)
    assert len(graph.posts) == 1
    assert report["links"]["captured"]["status"] == "FAIL"
    assert report["cleanup"]["attempted"] is True, report["cleanup"]
    assert report["cleanup"]["deleted"] is True
    assert "nothing to delete" not in report["cleanup"]["detail"]
    assert report["session_id"] in graph.deletes
    assert graph.sessions == {}
    assert report["exit_code"] == EXIT_BROKEN


# ── P2-1: a leaked probe is BROKEN, never "unverifiable" ─────────────────


def test_guard_cleanup_failure_beats_an_unverifiable_link(hosted, setup):
    """The old-build memory link (UNVERIFIABLE) plus a failed DELETE exits 1.

    Mutation: move the cleanup-error branch back BELOW the UNVERIFIABLE branch
    in ``_exit_code`` — exit becomes 2 while ``cleanup.error`` is True, and
    this REDs.
    """
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    graph.omit_source_field = True
    graph.fail_delete = True
    report = _verify(hosted, home, "claude", root)
    assert report["links"]["memory"]["status"] == "UNVERIFIABLE-IN-CI"
    assert report["cleanup"]["error"] is True
    assert report["exit_code"] == EXIT_BROKEN


def test_guard_fire_timeout_after_a_capture_still_deletes_the_probe(
        hosted, setup, tmp_path):
    """A fire that is LAUNCHED but reports failure must still delete the probe.

    The seam stores the session and advances the receipt, THEN blocks past the
    fire timeout — so ``_fire`` reports failure after a capture that really
    happened.  The timeout is derived from the hook's measured prologue (so
    the capture has landed long before the kill at any load) while the
    server holds the response far past it, so the fire still TIMES OUT.

    Mutation: restore the old ``fired["ok"]`` keying by returning early
    ("no capture was attempted — nothing to delete") from ``_cleanup``
    whenever ``launch is LaunchOutcome.TIMED_OUT`` — the probe is orphaned
    (``cleanup.attempted`` False, ``graph.deletes == []``, the session
    survives) and this REDs.  Reverting the captured message to the old "the
    installed seam did not run" wording REDs the "was launched" assertion.
    """
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    graph.post_delay = 90.0
    prologue = _hook_fork_seconds(home, root, "claude", tmp_path)
    report = _verify(hosted, home, "claude", root, timeout=prologue + 20.0)
    assert len(graph.posts) == 1, "the seam really captured before the timeout"
    assert report["links"]["installed"]["status"] == "FAIL", report
    assert "did not return within" in report["links"]["installed"]["detail"]
    assert report["fire"]["outcome"] == "timed-out", report.get("fire")
    assert report["cleanup"]["attempted"] is True, report["cleanup"]
    assert report["cleanup"]["deleted"] is True, report["cleanup"]
    assert report["session_id"] in graph.deletes
    assert report["session_id"] not in graph.sessions
    assert "did not run" not in report["links"]["captured"]["detail"]
    assert "was launched" in report["links"]["captured"]["detail"]
    assert report["exit_code"] == EXIT_BROKEN


# ── P1 (rr3): only NOT_LAUNCHED can prove "nothing was left behind" ──


def test_guard_timed_out_fire_never_claims_nothing_was_left_behind(
        hosted, setup, tmp_path):
    """A FORKED capture child outlives the SIGKILL — a DELETE 404 is not proof.

    The SHIPPED Claude hook forks its capture step (the line ends ``|| exit
    0``, so bash cannot ``exec`` it).  The fire timeout is DERIVED from the
    hook's own measured prologue (``_hook_fork_seconds``) so the kill lands
    AFTER the fork at any load — then the kill reaches only ``/bin/bash``,
    the forked child survives, and it captures only when the test releases it
    (``TORTOISE_TEST_RELEASE_FILE``), never in a race with a wall clock.  A
    server-side gate could not show this — it would prove only that this
    server was still working.

    Mutation: key the cleanup disclosure on a harness property (e.g. "is this
    a detaching harness?") or on the success flag instead of the launch
    outcome — Claude is not detaching and the fire reported failure, so the
    report claims "nothing was left behind" while the orphan is still alive
    and this REDs.  (Verified RED: ``_OUTCOME_PROVES_NOTHING_CAN_LAND``
    ``TIMED_OUT`` flipped to ``True``.)
    """
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    graph, _url = hosted
    # The hook's time-to-fork at the CURRENT load, so the timeout below
    # cannot false-RED on a loaded box (the old hardcoded 1.0s did: 5/5).
    prologue = _hook_fork_seconds(home, root, "claude", tmp_path)
    timeout = 2.0 * prologue + 10.0
    fork_marker = tmp_path / "forked-at"
    release = tmp_path / "release-the-capture"
    try:
        report = _verify(
            hosted, home, "claude", root, timeout=timeout,
            extra_env={"TORTOISE_TEST_FORK_MARKER": str(fork_marker),
                       "TORTOISE_TEST_RELEASE_FILE": str(release)})
        assert report["fire"]["outcome"] == "timed-out", report.get("fire")
        assert report["links"]["installed"]["status"] == "FAIL"
        assert "did not return within" in report["links"]["installed"]["detail"]
        captured = report["links"]["captured"]["detail"]
        assert "was launched" in captured and "did not run" not in captured
        cleanup = report["cleanup"]
        assert cleanup["launch"] == "timed-out", cleanup
        assert cleanup.get("in_flight") is True, cleanup
        assert "nothing was left behind" not in cleanup["detail"], cleanup
        assert "in flight" in cleanup["detail"], cleanup
        assert report["exit_code"] == EXIT_BROKEN, report
        # The hook really FORKED the capture step (the kill was late enough),
        # and that child is blocked on our release: nothing has landed.
        assert fork_marker.exists(), (
            "the hook never reached its capture fork before the fire timeout")
        assert graph.posts == [], "the capture had not landed at cleanup time"
    finally:
        # Let the forked child — which survived the SIGKILL of /bin/bash —
        # capture; it can only land after verify returned.
        release.write_text("go")
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline and not graph.posts:
        time.sleep(0.1)
    assert len(graph.posts) == 1, "the forked child POSTed after verify"
    assert report["session_id"] in graph.sessions


def test_guard_detaching_seam_reports_in_flight_not_nothing_left_behind(
        hosted, setup, tmp_path):
    """Codex's disowned worker outlives a CLEAN exit — say so, do not claim.

    The worker BLOCKS inside the shim on the test's release file, so the Codex
    hook exits 0 immediately after ``nohup … & disown`` while its worker is
    still alive: the late POST proves the worker really survived ``_fire`` (a
    server-side gate would not).  The timeout is derived from the hook's
    measured prologue so the hook still exits cleanly at any load (a fixed 2s
    false-REDed on a loaded box).

    Mutation: treat ``EXITED`` as proof (drop the launch-outcome branch in
    ``_cleanup``) — the report claims "nothing was left behind", the
    ``in_flight``/message assertions RED, and the late POST still lands.
    """
    home, _bindir, _fake = setup
    root = _install(home, "codex")
    graph, _url = hosted
    prologue = _hook_fork_seconds(home, root, "codex", tmp_path)
    release = tmp_path / "release-the-disowned-worker"
    try:
        report = _verify(
            hosted, home, "codex", root, timeout=2.0 * prologue + 10.0,
            extra_env={"TORTOISE_TEST_RELEASE_FILE": str(release)})
        assert report["fire"]["outcome"] == "exited", report.get("fire")
        assert report["fire"]["returncode"] == 0, report.get("fire")
        assert report["links"]["captured"]["status"] == "FAIL", report
        cleanup = report["cleanup"]
        assert cleanup["launch"] == "exited", cleanup
        assert cleanup.get("in_flight") is True, cleanup
        assert "nothing was left behind" not in cleanup["detail"]
        assert "in flight" in cleanup["detail"]
        assert report["exit_code"] == EXIT_BROKEN, report
        # The disowned worker is blocked on our release: nothing has landed,
        # so this assertion cannot race a fixed client-side delay.
        assert graph.posts == [], "the worker had not landed at cleanup time"
    finally:
        release.write_text("go")
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline and not graph.posts:
        time.sleep(0.1)
    assert len(graph.posts) == 1, "the disowned worker POSTed after verify"
    assert report["session_id"] in graph.sessions


def test_guard_nonzero_exit_is_not_reported_as_did_not_run(
        hosted, setup, monkeypatch):
    """``EXITED`` with rc≠0 is NOT the same launch fact as ``NOT_LAUNCHED``.

    The command really ran and returned 7, so the capture may have been filed
    before the failure: the message must say it ran and exited, never that it
    did not run (or did not complete).  The command is monkeypatched so the
    failure is the COMMAND's, not the OS's, with the install still current.

    Mutation: fold an rc≠0 exit into ``NOT_LAUNCHED`` (or reuse the generic
    "the installed seam did not run") — the "ran and exited"/"was launched"
    assertions RED.
    """
    home, _bindir, _fake = setup
    root = _install(home, "claude")
    import tortoise.session_verify as sv

    monkeypatch.setattr(sv, "_registered_capture_command",
                        lambda _h, _r: "exit 7")
    report = _verify(hosted, home, "claude", root)
    assert report["fire"]["outcome"] == "exited", report.get("fire")
    assert report["fire"]["returncode"] == 7, report.get("fire")
    assert "rc=7" in report["links"]["installed"]["detail"]
    captured = report["links"]["captured"]["detail"]
    assert "ran and exited" in captured, captured
    assert "rc=7" in captured, captured
    assert "did not run" not in captured, captured
    assert "could not be launched" not in captured, captured
    assert report["cleanup"].get("in_flight") is True, report["cleanup"]
    assert report["exit_code"] == EXIT_BROKEN, report


def test_launch_outcomes_are_exhaustively_classified():
    """A fourth launch outcome cannot silently inherit a sibling's semantics.

    The cleanup-certainty table is keyed on EVERY member, and a member with no
    entry raises ``KeyError`` instead of defaulting.  Mutation: add a fourth
    ``LaunchOutcome`` (e.g. ``SIGNALLED``) without deciding its cleanup
    certainty — the set equality REDs and the lookup raises.
    """
    from tortoise import session_verify as sv

    assert set(sv._OUTCOME_PROVES_NOTHING_CAN_LAND) == set(sv.LaunchOutcome)
    assert sv._proves_nothing_can_land(sv.LaunchOutcome.NOT_LAUNCHED) is True
    assert sv._proves_nothing_can_land(sv.LaunchOutcome.TIMED_OUT) is False
    assert sv._proves_nothing_can_land(sv.LaunchOutcome.EXITED) is False
    assert sv._proves_nothing_can_land(None) is False
    assert sv.FireResult(sv.LaunchOutcome.NOT_LAUNCHED, "x").proves_nothing_can_land
    assert not sv.FireResult(sv.LaunchOutcome.TIMED_OUT, "x").proves_nothing_can_land
    assert not sv.FireResult(
        sv.LaunchOutcome.EXITED, "x", returncode=0).proves_nothing_can_land

    class _Fourth(str, __import__("enum").Enum):
        SIGNALLED = "signalled"

    with pytest.raises(KeyError):
        sv._proves_nothing_can_land(_Fourth.SIGNALLED)


def test_guard_in_flight_cleanup_is_broken_not_a_clean_exit():
    """An in-flight cleanup must exit non-zero, even with every link PROVEN.

    Mutation: drop ``or cleanup.get("in_flight")`` from the ``_exit_code``
    cleanup branch — a report whose only defect is an in-flight capture returns
    ``EXIT_OK`` and this REDs.
    """
    from tortoise.session_verify import EXIT_OK, _exit_code

    report = {
        "links": {
            "installed": {"status": "PROVEN", "detail": "fired"},
            "captured": {"status": "PROVEN", "detail": "observed"},
            "memory": {"status": "PROVEN", "detail": "extracted"},
        },
        "cleanup": {"attempted": True, "deleted": False, "in_flight": True},
    }
    assert _exit_code(report) == EXIT_BROKEN
    assert _exit_code(report) != EXIT_OK


# ── P2-2: the CLI boundary (catch-all form + exit propagation) ────────────


def _cli_args(**over):
    import argparse
    base = {"harness": "claude", "dir": None, "timeout": 1.0,
            "keep": False, "json": False}
    base.update(over)
    return argparse.Namespace(**base)


def _bind_cli_verify(monkeypatch, func):
    """Bind ``_cmd_session_verify`` to a stubbed ``verify_session_capture``."""
    import tortoise.session_verify as sv
    from tortoise.__main__ import _cmd_session_verify
    monkeypatch.setattr(sv, "verify_session_capture", func)
    return _cmd_session_verify


class _BoundaryBoom(Exception):
    """An exception no enumerated ``except (A, B)`` tuple can name."""


def test_cli_boundary_catches_a_non_enumerated_exception(monkeypatch, capsys):
    """The CLI boundary is the ruled CATCH-ALL, not an enumerated tuple.

    Mutation: replace ``except Exception as e`` with ``except (ValueError,
    _ApiError) as e`` in ``_cmd_session_verify`` — ``_BoundaryBoom`` escapes
    the function and this REDs.
    """
    def _boom(*_a, **_k):
        raise _BoundaryBoom("unexpected")

    cmd = _bind_cli_verify(monkeypatch, _boom)
    rc = cmd(_cli_args(), "tt_test", "http://127.0.0.1:1")
    assert rc == EXIT_BROKEN
    err = capsys.readouterr().err
    assert "verify failed: _BoundaryBoom: unexpected" in err


def test_cli_boundary_reraises_memory_error(monkeypatch):
    """``MemoryError`` is re-raised, never converted to a refusal.

    Mutation: delete the ``except MemoryError: raise`` arm — ``MemoryError``
    is caught by the catch-all, returns 1, and this REDs.
    """
    def _boom(*_a, **_k):
        raise MemoryError("out of memory")

    cmd = _bind_cli_verify(monkeypatch, _boom)
    with pytest.raises(MemoryError):
        cmd(_cli_args(), "tt_test", "http://127.0.0.1:1")


def test_cli_boundary_propagates_the_report_exit_code(monkeypatch, capsys):
    """The report's exit code reaches the process exit code.

    Mutation: ``return int(report.get("exit_code", EXIT_BROKEN))`` ->
    ``return EXIT_OK`` — an UNVERIFIABLE (2) report exits 0 and this REDs.
    """
    report = {"harness": "claude", "root": "/tmp/x", "session_id": None,
              "links": {"installed": {"status": "UNVERIFIABLE-IN-CI",
                                      "detail": "not firable"}},
              "cleanup": {}, "exit_code": EXIT_UNVERIFIABLE}
    cmd = _bind_cli_verify(monkeypatch, lambda *_a, **_k: report)
    assert cmd(_cli_args(), "tt_test", "http://127.0.0.1:1") == \
        EXIT_UNVERIFIABLE


def test_cli_boundary_defaults_a_missing_exit_code_to_broken(monkeypatch):
    """A report with no ``exit_code`` must NOT exit 0.

    Mutation: ``report.get("exit_code", EXIT_BROKEN)`` -> ``... , EXIT_OK`` —
    the pre-contract shape silently exits 0 and this REDs.
    """
    report = {"harness": "claude", "root": "/tmp/x", "session_id": None,
              "links": {}, "cleanup": {}}
    cmd = _bind_cli_verify(monkeypatch, lambda *_a, **_k: report)
    assert cmd(_cli_args(), "tt_test", "http://127.0.0.1:1") == EXIT_BROKEN


# ── P2-4: one harness definition, not two ────────────────────────────────


def test_harnesses_is_the_capture_seam_definition():
    """``HARNESSES`` is derived from ``capture_install.CAPTURE_SEAM``.

    Mutation: replace the derivation with a drifted hard-coded tuple (drop
    ``pi``) — the two definitions no longer agree and this REDs.
    """
    from tortoise import capture_install as ci
    from tortoise import session_verify as sv
    assert tuple(sv.HARNESSES) == tuple(ci.CAPTURE_SEAM)
    assert set(sv.HARNESSES) == set(ci.CAPTURE_SEAM)
