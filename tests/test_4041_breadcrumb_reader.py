"""#4041 — the capture breadcrumb must be read BACK to the agent.

``~/.tortoise/capture-errors/<harness>.json`` was written by two authors and
read by nobody that could tell the user: ``session_verify`` deliberately
ignores the ``capture-failure`` kind and ``capture_spool`` only unlinks it, so
an agent whose memory had stopped being filed was never told.  The owner ruled
the agent SESSION is the primary surface (the dashboard was explicitly
rejected), so ``session-start.sh`` now renders the record to stdout, which
Claude Code injects into the session context.

The evidence standard is the one ``tests/test_4314_inert_hooks.py`` and
``tests/test_hook_run_observation.py`` set: the REAL shipped hook is executed
(bash, subprocess) and observed.  A grep of the script cannot see this
regression — it lives in control flow, and it needs the interpreter the hook
actually spawns.

Every test names the mutation that REDs it.

⛔ Two renderers exist on purpose: ``install-inert`` (pure shell — the branch is
reached BECAUSE the interpreter or module dir did not resolve) and
``capture-failure`` (Python — always written by Python, and the one detail that
needs ``redact_secrets``).  ``test_install_inert_is_rendered_with_no_python_on_
path`` is the proof that a Python-only renderer would have missed the first.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
#: Overridable so the pre-wiring RED can be demonstrated against the ORIGINAL
#: hook from git without touching the working tree:
#:   TORTOISE_SESSION_START_OVERRIDE=/tmp/orig-session-start.sh pytest …
SESSION_START = Path(os.environ.get("TORTOISE_SESSION_START_OVERRIDE")
                     or (REPO / "tortoise" / "claude-hooks"
                         / "session-start.sh"))

pytestmark = pytest.mark.skipif(
    not SESSION_START.exists(), reason="claude-hooks script not present")

#: The literal words the payload TEMPLATE (``what:``/``next:``) may NEVER use —
#: the capture is spooled and retried by design, so "failed" would be untrue and
#: "run <cmd>" is the imperative phrasing Claude Code's injection defences
#: reject.  Deliberately NOT asserted over the echoed ``why:`` detail: the
#: shipped writer's detail is ``f"import failed (HTTP {code}): {body}"``, so a
#: whole-stdout scan would only pass while the fixture happened to avoid the
#: word.
_FORBIDDEN = ("failed", "run `", "execute `", "you must", "you should")


# ── helpers ──────────────────────────────────────────────────────────────

def _crumb_path(home: Path, harness: str = "claude") -> Path:
    return home / ".tortoise" / "capture-errors" / f"{harness}.json"


def _install_crumb_path(home: Path, harness: str = "claude") -> Path:
    """The ``install-inert`` slot — its OWN file since #5838.

    The two kinds used to share ``<harness>.json``; an inert install then
    overwrote a live ``capture-failure``.  Every install-inert write/read now
    targets ``<harness>-install.json``, so a test that reads the wrong slot
    would not just be wrong — it would assert the very collision #5838 removes.
    """
    return home / ".tortoise" / "capture-errors" / f"{harness}-install.json"


def _seed_breadcrumb(home: Path, **fields) -> Path:
    path = _crumb_path(home, fields.get("harness", "claude"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields, indent=2), encoding="utf-8")
    return path


def _capture_failure(**overrides) -> dict:
    record = {
        "harness": "claude",
        "detail": "Cannot reach API at https://api.example: connection refused",
        "kind": "capture-failure",
        "session_id": "imp_abc",
        "recorded_at": "2026-09-26T00:00:00Z",
    }
    record.update(overrides)
    return record


def _mock_tortoise(bindir: Path, log: Path,
                  digest: str | None = None) -> None:
    """A fake ``tortoise`` on PATH: logs argv, exits 0 for everything.

    The digest, the install probe and the replay drain must not reach the
    network in a hermetic test; the renderer under test does not go through
    this binary at all.

    ``digest``, when given, is printed on every invocation (the probe and drain
    calls redirect their own stdout away, so only ``tortoise context``'s copy
    reaches the hook's stdout).  A test that needs to observe the BOUNDARY
    between the breadcrumb payload and the memory digest needs a digest present —
    without one, stdout has no following line for the payload to merge into.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    mock = bindir / "tortoise"
    mock.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        + (f'printf \'%s\\n\' "{digest}"\n' if digest is not None else "")
        + "exit 0\n",
        encoding="utf-8")
    mock.chmod(0o755)


def _python3_shim(bindir: Path) -> Path:
    """A 3.12 ``python3`` first on PATH.

    The renderer imports ``tortoise.capture_breadcrumb``; the system
    ``/usr/bin/python3`` here is 3.9 and the repo requires 3.12, so the test
    supplies the interpreter the hook would find on a correctly-set-up host.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "python3"
    if shim.exists() or shim.is_symlink():
        shim.unlink()
    shim.symlink_to(sys.executable)
    return shim


def _shell_tools_only(bindir: Path) -> Path:
    """A PATH with the hook's shell plumbing but NO ``python3``/``tortoise``."""
    bindir.mkdir(parents=True, exist_ok=True)
    for tool in ("cat", "tr", "head", "mkdir", "date", "dirname"):
        real = shutil.which(tool)
        assert real, tool
        (bindir / tool).symlink_to(real)
    return bindir


def _link(bindir: Path, *tools: str) -> Path:
    """Symlink named REAL tools into ``bin`` (every one must exist)."""
    bindir.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        real = shutil.which(tool)
        assert real, tool
        (bindir / tool).symlink_to(real)
    return bindir


def _custom_python3(bindir: Path, *, stdout: str = "", stderr: str = "",
                    rc: int = 0, marker: Path | None = None) -> Path:
    """A ``python3`` whose behaviour the test controls exactly.

    The hook always runs the breadcrumb renderer through ``python3 -c``; this
    shim lets a test inject a renderer that prints nothing, fails, or records
    that it was spawned at all.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "python3"
    body = "#!/usr/bin/env bash\n"
    if marker is not None:
        body += f'touch "{marker}"\n'
    if stdout:
        body += f'printf \'%s\\n\' "{stdout}"\n'
    if stderr:
        body += f'printf \'%s\\n\' "{stderr}" >&2\n'
    body += f"exit {rc}\n"
    shim.write_text(body, encoding="utf-8")
    shim.chmod(0o755)
    return shim


def _run_hook(home: Path, *, path: str, src: Path | None = None,
              hook: Path | None = None,
              cwd: Path | None = None,
              extra_env: dict[str, str] | None = None
              ) -> subprocess.CompletedProcess:
    """Drive the REAL shipped hook with a hermetic env.

    ``HOME`` is always the caller's tmp dir: the hook writes a breadcrumb and a
    hook-run record, and a test must never let them land in the developer's
    real ``$HOME``.  ``cwd`` is settable for the CWE-427 test: ``python3 -c``
    puts the process cwd on ``sys.path``, so the hook's cwd is an attacker-
    influenced surface.  ``extra_env`` lets a test supply ``PYTHONPATH`` so the
    installed-package fallback can import ``tortoise`` even when no module dir
    resolves (#5838's both-causes case).
    """
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    env = {"HOME": str(home), "PATH": path, "TMPDIR": str(home / "tmp")}
    if src is not None:
        env["TORTOISE_SRC_DIR"] = str(src)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["/bin/bash", str(hook or SESSION_START)], input="",
        capture_output=True, text=True, env=env, timeout=120,
        cwd=str(cwd) if cwd is not None else None)


def _fields(stdout: str) -> dict[str, str]:
    """Split a payload into ``{label: value}`` (label without its colon)."""
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        if not line:
            continue
        label, _, value = line.partition(":")
        out[label.strip()] = value.strip()
    return out


# ── the capture-failure path (the normal branch) ─────────────────────────

def test_a_capture_failure_breadcrumb_reaches_the_agent(tmp_path):
    """The premise of #4041: a breadcrumb left by a previous capture is RENDERED
    to the hook's stdout (which Claude Code injects), with the existing
    machine-readable ``kind`` and all three human/agent parts.

    The passing render is also PROOF the branch's code ran: the renderer module
    does not exist on ``main``, so an import resolving to the main checkout
    would raise and produce no output.

    Mutation: drop the ``_render_capture_failure_breadcrumb`` call from the
    hook, or remove the renderer — stdout is empty and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    _seed_breadcrumb(home, **_capture_failure())

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    fields = _fields(proc.stdout)
    assert fields.get("code") == "capture-failure", proc.stdout
    assert "NOT been filed" in fields.get("what", ""), proc.stdout
    assert "claude capture is affected" in fields.get("what", ""), proc.stdout
    assert "2026-09-26T00:00:00Z" in fields.get("what", ""), proc.stdout
    assert fields.get("why") == (
        "Cannot reach API at https://api.example: connection refused"), proc.stdout
    assert fields.get("next", "").startswith("Recovery:"), proc.stdout
    assert "tortoise session drain" in fields.get("next", ""), proc.stdout


def test_the_payload_and_the_memory_digest_do_not_merge(tmp_path):
    """The breadcrumb payload and the memory digest are written to the SAME
    stdout, payload first.  ``payload="$(...)"`` strips EVERY trailing newline,
    so without the explicit one ``printf '%s'`` glues the payload's last line
    (``next: …``) onto the digest's first line (``# Tortoise memory …``): the
    recovery line is corrupted AND the digest header stops being a Markdown
    heading.  Every other test mocks ``tortoise`` without a digest, so stdout
    never had a following line and no test could see the merge.

    Mutation: print ``printf '%s' "$payload"`` (or otherwise drop the trailing
    newline) — the two surfaces share a line and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log",
                   digest="# Tortoise memory (from previous sessions) — local")
    _python3_shim(bindir)
    _seed_breadcrumb(home, **_capture_failure())

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    # Both surfaces reached this stdout...
    assert "next:     Recovery:" in proc.stdout, proc.stdout
    assert "# Tortoise memory" in proc.stdout, proc.stdout
    # ...and the boundary between them is a real newline: the digest header
    # STARTS a line, and the exact glued artefact is absent.
    assert "\n# Tortoise memory (from previous sessions) — local" in proc.stdout, \
        proc.stdout
    assert "meanwhile.# Tortoise memory" not in proc.stdout, proc.stdout
    lines = proc.stdout.splitlines()
    assert any(line.startswith("next:") for line in lines), proc.stdout
    assert any(line.startswith("# Tortoise memory") for line in lines), proc.stdout


def test_no_breadcrumb_means_no_output_and_exit_zero(tmp_path):
    """The exit-0 contract is inviolable: with no breadcrumb file the hook must
    emit NOTHING.  A single stray byte here is injected into every session.

    Mutation: print a header/footer unconditionally (or let the renderer emit a
    placeholder) — the byte-count assertion REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", (
        f"a no-breadcrumb session start emitted {len(proc.stdout)} bytes "
        f"({proc.stdout!r}) — it must emit none")


def test_an_absent_breadcrumb_does_not_even_spawn_the_renderer(tmp_path):
    """``[ -f "$crumb" ]`` is not merely an optimisation: with no breadcrumb
    file the renderer must not be spawned at all.  Spawning it means every
    session start pays an interpreter start-up for a file that is absent in the
    overwhelming majority of sessions, and it makes the branch's behaviour
    depend on what an unreadable path happens to raise.

    Mutation: drop the ``[ -f "$crumb" ] || return 0`` guard — the renderer is
    spawned, the marker appears, and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    marker = tmp_path / "renderer-spawned"
    _custom_python3(bindir, marker=marker)
    # Deliberately NO breadcrumb seeded.

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", proc.stdout
    assert not marker.exists(), (
        "the renderer was spawned although no breadcrumb file exists")


def test_a_directory_at_the_crumb_path_does_not_spawn_the_renderer(tmp_path):
    """``[ -f "$crumb" ]`` — not ``-e`` — is what keeps a DIRECTORY named
    ``claude.json`` from costing an interpreter start.  ``-e`` is equally true
    for a directory, so it would spawn the renderer for a path that can never
    be a record.  The OUTPUT is the same either way (``render_file`` refuses a
    directory and prints nothing), which is exactly why only a spawn marker can
    see the difference.

    Mutation: widen the test to ``[ -e "$crumb" ]`` — the marker appears and
    this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    marker = tmp_path / "renderer-spawned"
    _custom_python3(bindir, marker=marker)
    _crumb_path(home).mkdir(parents=True)  # a DIRECTORY, not a record

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", proc.stdout
    assert not marker.exists(), (
        "the renderer was spawned for a crumb path that is a directory — the "
        "`-f` test must not be widened to `-e`")


def test_a_stale_install_inert_breadcrumb_is_not_rendered_as_live(tmp_path):
    """An ``install-inert`` record surviving from an EARLIER inert run is stale
    once the hook resolves a module dir: rendering it would tell the agent that
    memory is not being filed while the working seam is filing it.  The record
    for the CURRENT run is rendered by the inert branch that writes it.

    #5838: the stale record lives in the install-inert slot
    (``<harness>-install.json``); the normal path reads only the
    capture-failure slot, so a resolved install never replays the old claim.

    Mutation: read the install-inert slot on the resolved path (or drop the
    ``kind == capture-failure`` gate in ``render``) — the stale claim appears
    and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    stale = _install_crumb_path(home)
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(json.dumps(_capture_failure(
        kind="install-inert",
        detail="the installed Claude session-start hook resolved nothing")),
        encoding="utf-8")
    assert not _crumb_path(home).exists()

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", (
        f"a stale install-inert record was rendered as a live claim: "
        f"{proc.stdout!r}")


def test_an_unknown_kind_is_never_rendered(tmp_path):
    """No new taxonomy (#4041): only the two EXISTING ``kind`` values render. An
    unrecognised record must produce nothing rather than invent a code or
    prose-match the record.

    Mutation: render any dict, or add a fallback label — the foreign record
    produces output and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    _seed_breadcrumb(home, **_capture_failure(kind="something-else"))

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", proc.stdout


def test_a_compact_single_line_record_is_still_rendered(tmp_path):
    """The ``kind`` must be decided by PARSING the record, not by a shell text
    match. ``_record_capture_error`` happens to write ``indent=2``, but that is
    not a contract; a compact single-line record is valid JSON and MUST render.
    A start-of-line ``sed`` gate silently discarded it, reintroducing the exact
    "nobody is told" defect #4041 exists to fix.

    Mutation: decide the kind with the old start-of-line ``sed`` (or any matcher
    that assumes the writer's indentation) — a compact record renders nothing
    and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    crumb = _crumb_path(home)
    crumb.parent.mkdir(parents=True, exist_ok=True)
    crumb.write_text(json.dumps(_capture_failure(), separators=(",", ":")),
                     encoding="utf-8")
    # The record really is one line — the premise of the test.
    assert crumb.read_text(encoding="utf-8").count("\n") == 0

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    fields = _fields(proc.stdout)
    assert fields.get("code") == "capture-failure", proc.stdout
    assert "NOT been filed" in fields.get("what", ""), proc.stdout


def test_the_renderer_does_not_depend_on_sed_or_head(tmp_path):
    """The capture half exists BECAUSE Python is available; it must not also
    need shell text tools. The old gate ran ``sed | head`` to read the ``kind``
    and, with either absent from PATH, silently disabled the whole feature —
    even though the interpreter that renders the payload was right there.

    Mutation: restore the ``sed``/``head`` gate — with neither on PATH the
    record renders nothing and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    # Everything the CAPTURE path needs, and deliberately NO sed / NO head.
    _link(bindir, "cat", "mkdir", "date", "nohup")
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    assert shutil.which("sed", path=str(bindir)) is None, "test PATH leaked sed"
    assert shutil.which("head", path=str(bindir)) is None, "test PATH leaked head"
    _seed_breadcrumb(home, **_capture_failure())

    proc = _run_hook(home, path=str(bindir), src=REPO)

    assert proc.returncode == 0, proc.stderr
    fields = _fields(proc.stdout)
    assert fields.get("code") == "capture-failure", proc.stdout
    assert "tortoise session drain" in fields.get("next", ""), proc.stdout


def test_a_renderer_that_writes_then_fails_injects_nothing(tmp_path):
    """The exit-0 contract only guarantees the renderer's EXIT STATUS is
    swallowed — not that its bytes are. A ``python3`` that writes partial
    output and THEN exits non-zero would otherwise inject that garbage into the
    session context at rc 0. The payload must therefore be buffered and printed
    only on success.

    Mutation: print straight from the renderer (drop the buffer/guard) — the
    partial bytes reach stdout and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "python3"
    shim.write_text("#!/usr/bin/env bash\n"
                    "printf 'GARBAGE PARTIAL\\n'\n"
                    "exit 1\n", encoding="utf-8")
    shim.chmod(0o755)
    _seed_breadcrumb(home, **_capture_failure())

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert "GARBAGE" not in proc.stdout, (
        f"a failing renderer injected bytes into the session: {proc.stdout!r}")
    assert proc.stdout == "", proc.stdout


def test_a_renderer_that_succeeds_with_no_output_prints_nothing(tmp_path):
    """A renderer can exit 0 and still produce an EMPTY payload (the record is
    malformed, its kind is unknown, or the file vanished between the ``-f``
    check and the read).  Printing that empty payload would still emit a bare
    newline into the session context — a stray byte in EVERY such session.

    Mutation: drop ``[ -n "$payload" ] || return 0`` — ``printf '%s\n'``
    emits the newline and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _custom_python3(bindir, stdout="", rc=0)
    _seed_breadcrumb(home, **_capture_failure())

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", (
        f"an empty payload emitted {len(proc.stdout)} bytes: {proc.stdout!r}")


def test_a_renderer_failure_cannot_leak_onto_hook_stderr(tmp_path):
    """The renderer is best-effort: a ``python3`` that fails (an unimportable
    module, a traceback) must not write its diagnostics to the hook's stderr.
    Claude Code surfaces hook stderr, and the crash shape that matters is the
    one that did NOT get caught — a raised exception rendered by the
    interpreter.

    Mutation: drop ``2>/dev/null`` from the renderer invocation — the traceback
    reaches stderr and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _custom_python3(bindir, stderr="TRACEBACK-MARKER", rc=1)
    _seed_breadcrumb(home, **_capture_failure())

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", proc.stdout
    assert "TRACEBACK-MARKER" not in proc.stderr, (
        f"a failing renderer wrote to hook stderr: {proc.stderr!r}")


def test_the_renderer_never_imports_a_planted_cwd_module(tmp_path):
    """CWE-427: ``python3 -c`` puts the process cwd (``''``/``'.'``) at
    ``sys.path[0]``, and the hook's cwd is the agent's workspace — an
    attacker-influenced surface.  ``capture_breadcrumb`` imports ``json`` at
    module level, so a planted ``./json.py`` would execute as the user at every
    session start.  The ``-c`` block drops ``''``/``'.'`` from ``sys.path``
    BEFORE importing anything beyond the builtin ``sys``.

    Mutation: drop the ``sys.path`` scrub — the planted ``./json.py`` executes,
    the marker appears, and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    _seed_breadcrumb(home, **_capture_failure())

    workdir = tmp_path / "work"
    workdir.mkdir()
    marker = tmp_path / "pwned"
    (workdir / "json.py").write_text(
        f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO,
                     cwd=workdir)

    assert proc.returncode == 0, proc.stderr
    assert not marker.exists(), (
        "a planted ./json.py in the hook cwd executed — the cwd is "
        "attacker-influenced (CWE-427)")
    # The empty ``not marker.exists()`` check above would PASS VACUOUSLY if the
    # capture half were disabled entirely (no renderer -> no planted module
    # imported).  Pin that the renderer actually RAN and produced its normal
    # payload, so this test cannot be satisfied by producing nothing.
    # Mutation: disable the breadcrumb rendering — code is absent and this REDs.
    assert _fields(proc.stdout).get("code") == "capture-failure", proc.stdout


# ── the install-inert path (no interpreter reachable) ────────────────────

def test_install_inert_is_rendered_with_no_python_on_path(tmp_path):
    """The record that matters MOST for reachability: the install-inert
    breadcrumb is reached BECAUSE the interpreter or the module dir could not
    be resolved, so a Python-only renderer could never report it.  This drives
    that branch with NO ``python3`` and NO ``tortoise`` on PATH.

    Mutation: move the install-inert render behind the Python renderer (or
    otherwise require an interpreter) — nothing is printed here and this
    REDs."""
    home = tmp_path / "home"
    home.mkdir()
    hook = home / ".claude" / "hooks" / "session-start.sh"
    hook.parent.mkdir(parents=True)
    shutil.copy(SESSION_START, hook)
    hook.chmod(0o755)
    # `../..` from the installed position is $HOME — deliberately not a
    # checkout, so the module dir cannot resolve either.
    assert not (home / "tortoise").exists()

    bindir = tmp_path / "bin"
    _shell_tools_only(bindir)

    proc = _run_hook(home, path=str(bindir), hook=hook)

    assert proc.returncode == 0, proc.stderr
    assert shutil.which("python3", path=str(bindir)) is None, (
        "the hermetic PATH must hold no python3: this branch is the proof that "
        "install-inert renders without an interpreter")
    fields = _fields(proc.stdout)
    assert fields.get("code") == "install-inert", proc.stdout
    assert "NOT been filed" in fields.get("what", ""), proc.stdout
    assert "claude capture is affected" in fields.get("what", ""), proc.stdout
    assert "could not resolve a tortoise module dir" in fields.get("why", ""), \
        proc.stdout
    assert fields.get("next", "").startswith("Recovery:"), proc.stdout

    # The record the agent was told about is on disk too, so `session verify`
    # can read the same evidence — in the install-inert slot, and NOT in the
    # capture-failure slot (#5838).
    body = json.loads(_install_crumb_path(home).read_text(encoding="utf-8"))
    assert body["kind"] == "install-inert", body
    assert not _crumb_path(home).exists(), (
        "the inert writer touched the capture-failure slot")


def test_the_second_inert_branch_also_renders_the_breadcrumb(tmp_path):
    """There are TWO inert branches and each renders its OWN record.  This is
    the SECOND: a tortoise module dir RESOLVED (so the first branch does not
    apply) but no python3 exists to render the capture half with.  Branch 1's
    call is exercised by ``test_install_inert_is_rendered_with_no_python_on_
    path``; branch 2's call is a SEPARATE statement and needs its own proof.

    Mutation: drop the ``_render_breadcrumb_inert`` call from the resolved-
    module-dir branch — nothing is printed and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _shell_tools_only(bindir)  # no python3, no tortoise

    # A resolved module dir: `TORTOISE_SRC_DIR` holds a real `tortoise/`
    # package, so the hook does NOT take the unresolvable branch.
    src = tmp_path / "src"
    (src / "tortoise").mkdir(parents=True)
    (src / "tortoise" / "__init__.py").write_text("", encoding="utf-8")

    proc = _run_hook(home, path=str(bindir), src=src)

    assert proc.returncode == 0, proc.stderr
    assert shutil.which("python3", path=str(bindir)) is None
    fields = _fields(proc.stdout)
    assert fields.get("code") == "install-inert", proc.stdout
    assert "resolved a tortoise module dir but found no python3" in \
        fields.get("why", ""), proc.stdout
    # ⛔ THE RECOVERY MUST MATCH THIS BRANCH'S CAUSE (#4041 review round 3, P2). The
    # renderer used to hard-code branch 1's sentence, so this branch's payload
    # contradicted ITSELF (`why:` said the module dir WAS resolved; `next:` said it was
    # not) and prescribed `hooks upgrade` for a missing interpreter. Asserting only
    # ``startswith("Recovery:")`` could not see that.
    recovery = fields.get("next", "")
    assert recovery.startswith("Recovery:"), proc.stdout
    assert "no python3" not in recovery or "python3" in recovery, recovery
    assert "python3" in recovery, recovery
    assert "resolved no tortoise module dir" not in recovery, (
        "this branch's next: must not repeat branch 1's cause — its why: says the "
        f"module dir DID resolve: {recovery!r}")

    # The INERT RECORD this branch writes is what `session verify` reads, so it
    # must be pinned too — the stdout alone does not prove the write happened.
    # Mutation: drop this branch's `_record_breadcrumb` call — stdout stays
    # correct and this REDs.
    body = json.loads(_install_crumb_path(home).read_text(encoding="utf-8"))
    assert body["kind"] == "install-inert", body
    assert body["harness"] == "claude", body
    assert "resolved a tortoise module dir but found no python3" in \
        body["detail"], body
    assert body["recorded_at"], body


# ── #5838: the two causes no longer share a slot ────────────────────────

def test_an_inert_write_preserves_a_capture_failure_record(tmp_path):
    """#5838, the defect itself: an ``install-inert`` write must not touch the
    ``capture-failure`` record.  The two causes are independent — a machine can
    be over quota AND have a moved checkout — and before the slot split the
    inert write replaced the refusal, so the reader reported the WRONG cause.

    Mutation: point ``_record_breadcrumb`` back at ``<harness>.json`` (the
    single-slot behaviour) — the capture bytes change and this REDs.
    """
    home = tmp_path / "home"
    home.mkdir()
    hook = home / ".claude" / "hooks" / "session-start.sh"
    hook.parent.mkdir(parents=True)
    shutil.copy(SESSION_START, hook)
    hook.chmod(0o755)
    bindir = tmp_path / "bin"
    _shell_tools_only(bindir)  # branch 1: no interpreter, no module dir

    failure = _seed_breadcrumb(home, **_capture_failure(
        detail="import failed (HTTP 402): quota exceeded"))
    before = failure.read_bytes()
    assert json.loads(before)["kind"] == "capture-failure"

    proc = _run_hook(home, path=str(bindir), hook=hook)

    assert proc.returncode == 0, proc.stderr
    # The higher-priority cause SURVIVES, byte-for-byte.  Other tests use the
    # OLD single slot for install-inert, which is exactly the collision.
    assert failure.read_bytes() == before, (
        "an install-inert write destroyed the capture-failure record: "
        f"{failure.read_text(encoding='utf-8')!r}")
    # ...and the inert evidence exists, in its OWN slot.
    inert = json.loads(_install_crumb_path(home).read_text(encoding="utf-8"))
    assert inert["kind"] == "install-inert", inert
    assert inert["harness"] == "claude", inert


def _codes(stdout: str) -> list[str]:
    """The ``code:`` value of EVERY 4-line block in a payload, in order."""
    return [line.split(":", 1)[1].strip()
            for line in stdout.splitlines()
            if line.startswith("code:")]


def test_the_two_readers_resolve_the_two_slots(tmp_path, monkeypatch):
    """#5838: the two readers read BOTH files — ``capture_breadcrumb`` renders
    the capture-failure slot and ``session_verify`` (the install leg) resolves
    the install-inert slot — and the slot is derived from the KIND in exactly
    one place, so a writer and a reader cannot disagree about which file a
    cause lives in.

    Mutation: derive either slot from a second literal (e.g. point the install
    reader back at ``<harness>.json``) — the resolved path changes and this
    REDs.
    """
    from tortoise import capture_breadcrumb, session_verify
    from tortoise.hook_install import KIND_CAPTURE_FAILURE, KIND_INSTALL_INERT, breadcrumb_name

    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home)}

    assert breadcrumb_name("claude", KIND_CAPTURE_FAILURE) == "claude.json"
    assert breadcrumb_name("claude", KIND_INSTALL_INERT) == \
        "claude-install.json"

    capture = _seed_breadcrumb(home, **_capture_failure(
        detail="import failed (HTTP 402): quota exceeded"))
    install = capture.parent / breadcrumb_name("claude", KIND_INSTALL_INERT)
    install.write_text(json.dumps({
        "harness": "claude", "kind": KIND_INSTALL_INERT,
        "detail": "resolved no tortoise module dir",
        "recorded_at": "2026-09-26T00:00:00Z"}), encoding="utf-8")

    # The capture reader renders the refusal from its OWN slot...
    rendered = capture_breadcrumb.render_file(capture)
    assert "code:     capture-failure" in rendered, rendered
    assert "quota exceeded" in rendered, rendered
    # ...and the install reader resolves the install-inert slot for its own
    # kind, NOT the capture slot (and can be asked for either).
    assert session_verify._local_capture_error_file("claude", env) == install
    assert session_verify._local_capture_error_file(
        "claude", env, KIND_CAPTURE_FAILURE) == capture
    found = session_verify._local_capture_error("claude", env)
    assert found is not None and found["kind"] == KIND_INSTALL_INERT, found


def test_the_payload_carries_both_causes_when_both_exist(tmp_path):
    """#5838: with separate slots the hook reports BOTH facts.  The
    ``install-inert`` block is pure shell; the ``capture-failure`` block is
    Python, reachable here because the shim interpreter can import the installed
    ``tortoise`` via ``PYTHONPATH`` even though no module dir resolved.

    Mutation: read one slot only (drop either render call) — one block is
    missing and this REDs.
    """
    home = tmp_path / "home"
    home.mkdir()
    hook = home / ".claude" / "hooks" / "session-start.sh"
    hook.parent.mkdir(parents=True)
    shutil.copy(SESSION_START, hook)
    hook.chmod(0o755)
    bindir = tmp_path / "bin"
    _python3_shim(bindir)  # interpreter present, no module dir -> branch 1
    _seed_breadcrumb(home, **_capture_failure(
        detail="import failed (HTTP 402): quota exceeded"))

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", hook=hook,
                     extra_env={"PYTHONPATH": str(REPO)})

    assert proc.returncode == 0, proc.stderr
    assert _codes(proc.stdout) == ["capture-failure", "install-inert"], \
        proc.stdout
    # Neither cause may be dropped: the quota detail is the capture block's
    # reason, the unresolved seam is the install block's.
    assert "quota exceeded" in proc.stdout, proc.stdout
    assert "could not resolve a tortoise module dir" in proc.stdout, proc.stdout


def test_only_the_capture_cause_is_reported_when_only_it_exists(tmp_path):
    """#5838, order 1: a live capture-failure with NO install-inert record
    renders exactly one block, and no inert evidence is invented.

    Mutation: render an install-inert block unconditionally — a second block
    appears and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    _seed_breadcrumb(home, **_capture_failure())

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert _codes(proc.stdout) == ["capture-failure"], proc.stdout
    assert not _install_crumb_path(home).exists(), (
        "the resolved path wrote install-inert evidence it never had")


def test_only_the_install_cause_is_reported_when_only_it_exists(tmp_path):
    """#5838, order 2: an inert seam with NO capture-failure record renders
    exactly one block, and the capture slot stays untouched.

    Mutation: print a capture block without a record (or write one) — either
    assertion REDs."""
    home = tmp_path / "home"
    home.mkdir()
    hook = home / ".claude" / "hooks" / "session-start.sh"
    hook.parent.mkdir(parents=True)
    shutil.copy(SESSION_START, hook)
    hook.chmod(0o755)
    bindir = tmp_path / "bin"
    _shell_tools_only(bindir)

    proc = _run_hook(home, path=str(bindir), hook=hook)

    assert proc.returncode == 0, proc.stderr
    assert _codes(proc.stdout) == ["install-inert"], proc.stdout
    assert not _crumb_path(home).exists(), (
        "the inert path wrote to the capture-failure slot")


# ── the payload's wording contract ───────────────────────────────────────

@pytest.mark.parametrize("kind", ["capture-failure", "install-inert"])
def test_the_payload_says_not_filed_and_is_never_imperative(tmp_path, kind):
    """Two halves of the same contract:

    * the capture is SPOOLED and retried by design, so the payload says it is
      NOT FILED — never that it "failed" (which would be untrue and would
      contradict the spool's purpose);
    * the recovery half is available ACTIONS, never commands: Claude Code's
      hook documentation warns that output framed as out-of-band system
      commands trips its prompt-injection defences, which makes Claude surface
      the text to the user instead of treating it as injected context.  This is
      vendor-mandated; the code carries the same note so it is not "fixed" into
      imperatives.

    Mutation: reword to "capture failed" / "Run `tortoise session drain` now"
    — both assertions RED."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    if kind == "capture-failure":
        _mock_tortoise(bindir, tmp_path / "calls.log")
        _python3_shim(bindir)
        # The REAL writer emits f"import failed (HTTP {e.code}): {body}"
        # (__main__.py:4957), so the ECHOED detail legitimately contains
        # "failed".  Seeding that exact shape makes the template check below
        # prove itself: a whole-stdout scan would have passed only because the
        # previous fixture happened to avoid the word.
        _seed_breadcrumb(home, **_capture_failure(
            detail="import failed (HTTP 503): upstream unavailable"))
        proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)
    else:
        hook = home / ".claude" / "hooks" / "session-start.sh"
        hook.parent.mkdir(parents=True)
        shutil.copy(SESSION_START, hook)
        hook.chmod(0o755)
        _shell_tools_only(bindir)
        proc = _run_hook(home, path=str(bindir), hook=hook)

    assert proc.returncode == 0, proc.stderr
    fields = _fields(proc.stdout)
    # The ban is on the TEMPLATE's words — `what:` and `next:` — never on the
    # echoed `why:` detail.  The detail is the writer's own error string and may
    # legitimately say "failed"; asserting over the whole stdout would silently
    # stop pinning the template the moment a fixture carried the word.
    template = f"{fields.get('what', '')}\n{fields.get('next', '')}".lower()
    assert "not filed" in template, proc.stdout
    for phrase in _FORBIDDEN:
        assert phrase not in template, (phrase, proc.stdout)
    if kind == "capture-failure":
        # The fixture really does carry the forbidden word, so the assertion
        # above is exercised rather than vacuous.
        assert "failed" in fields.get("why", "").lower(), proc.stdout
    # The recovery half names actions, and only after the factual three parts.
    lines = proc.stdout.splitlines()
    assert lines[-1].startswith("next:     Recovery:"), proc.stdout


def test_the_detail_is_bounded_to_one_redacted_line(tmp_path):
    """An error string can carry a credential and can be a whole traceback. On
    the capture-failure path the detail is redacted through
    ``tortoise.security.redact_secrets`` and bounded to one line / ~400 chars,
    so a session start can neither leak a token nor dump a stack trace into the
    context window.

    Mutation: render the raw detail (drop ``bound_detail``) — the token
    survives, the payload gains lines, and this REDs."""
    from tortoise.capture_breadcrumb import MAX_DETAIL_CHARS

    token = "ghp_" + "a" * 36
    detail = ("first line\nsecond line\n" + token + "\n"
              + "x" * 2000)
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    _seed_breadcrumb(home, **_capture_failure(detail=detail))

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert token not in proc.stdout, "the credential survived into the context"
    assert "[REDACTED:github_token]" in proc.stdout, proc.stdout
    lines = proc.stdout.splitlines()
    assert len(lines) == 4, proc.stdout
    why = lines[2]
    assert why.startswith("why:      "), proc.stdout
    assert len(why) <= 10 + MAX_DETAIL_CHARS + 1, (len(why), why)
    assert why.rstrip().endswith("…"), (len(why), why)


def test_a_bound_that_lands_on_whitespace_is_rstripped_before_the_ellipsis():
    """The bound cuts at :data:`MAX_DETAIL_CHARS` and then ``rstrip()``s the cut
    BEFORE appending ``"…"``, so the ellipsis sits against the last real
    character instead of floating after the whitespace the cut happened to
    leave behind.  Cosmetic, but visible in the injected context.

    Mutation: ``text[:MAX_DETAIL_CHARS] + "…"`` (drop the ``rstrip``) — the
    rendered line keeps the two cut-trailing spaces before the marker and this
    REDs."""
    from tortoise.capture_breadcrumb import MAX_DETAIL_CHARS, bound_detail

    # The normalized line is exactly MAX_DETAIL_CHARS with a single trailing
    # space at the cut, so the raw 400-char prefix ends on whitespace the
    # rstrip must remove.
    out = bound_detail("A" * (MAX_DETAIL_CHARS - 1) + " " + "B" * 50)
    assert out == "A" * (MAX_DETAIL_CHARS - 1) + "…", repr(out[-8:])


def test_a_secret_straddling_the_bound_cannot_leak_a_fragment(tmp_path):
    """The redact-BEFORE-bound ordering is a safety property, not a
    preference: bounding first can cut a credential-shaped span in half, and the
    surviving prefix no longer matches any rule — cleartext key material in the
    session context. The existing bounded-detail test seeds the token near the
    START, so it passes under EITHER order; this one straddles the 400-char
    boundary so only redact-BEFORE-bound can survive it.

    Mutation: bound first, then redact — the truncation leaves ``ghp_aaaaa…``
    in cleartext and this REDs."""
    token = "ghp_" + "a" * 36
    detail = "A" * 390 + " " + token + " " + "B" * 50
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    _seed_breadcrumb(home, **_capture_failure(detail=detail))

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert len(lines) == 4, proc.stdout
    why = lines[2]
    assert "ghp_" not in why, (
        f"a secret FRAGMENT survived the bound in cleartext: {why!r}")
    assert "aaaaa" not in why, why
    # Redaction ran, and the bound cut the marker (not the secret).
    assert "[REDACTED" in why, why
    assert why.rstrip().endswith("…"), why


def test_a_nul_inside_a_credential_is_not_re_formed_by_the_transport(tmp_path):
    """A NUL INSIDE a credential is the input the REAL transport composes on:
    ``_render_capture_failure_breadcrumb`` captures the renderer's output with
    ``payload="$(...)"``, and command substitution strips EVERY NUL byte — so
    fragments redaction left untouched are RE-JOINED into a contiguous
    credential before ``printf '%s\\n'`` writes it to the injected stdout.

    The record is reachable, not synthetic: the ``capture-failure`` detail is
    ``f"import failed (HTTP {code}): {e.read().decode('utf-8','replace')}"``
    (``tortoise/__main__.py``), and a UTF-16LE / mis-decoded body is
    ``g\\x00h\\x00p\\x00_\\x00…`` — every credential character NUL-separated.
    ``bound_detail`` must normalize control characters BEFORE
    ``redact_secrets`` scans, so the scan sees the bytes the transport will
    actually emit (the NUL removed and the fragment re-joined), not the
    interrupted prefix no rule matches.

    Mutation: normalize AFTER redacting (the pre-fix order) — the renderer
    emits the NUL-interrupted detail, bash strips the NUL, and the re-formed
    ``ghp_`` + 36 chars appears in the injected stdout; this REDs."""
    token = "ghp_" + "a" * 36
    # The reviewer's exact reproduction: a NUL inside the credential body.
    detail = "import failed (HTTP 503): token ghp_\u0000" + "a" * 36
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    _seed_breadcrumb(home, **_capture_failure(detail=detail))

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert token not in proc.stdout, (
        f"a NUL-interrupted credential was RE-FORMED by the shell transport "
        f"and injected in cleartext: {proc.stdout!r}")
    lines = proc.stdout.splitlines()
    assert len(lines) == 4, proc.stdout
    assert "[REDACTED:github_token]" in lines[2], proc.stdout


def test_a_lone_surrogate_does_not_silently_drop_the_breadcrumb(tmp_path):
    """A record can carry a LONE SURROGATE: ``"\\ud800"`` is a valid JSON
    escape and ``json.loads`` produces the surrogate code point, which is not an
    encodable character.  ``sys.stdout.write`` then raises
    ``UnicodeEncodeError``, and the shell's ``|| return 0`` swallows it — so NO
    breadcrumb renders at all and the feature silently disappears for that
    record.  ``_normalize`` round-trips through UTF-8 with ``errors="replace"``
    so every rendered character is encodable.

    Mutation: drop the UTF-8 round trip in ``_normalize`` — the renderer exits
    non-zero, stdout is empty, and this REDs."""
    from tortoise.capture_breadcrumb import render

    # The direct renderer must produce an ENCODABLE payload: the hook writes it
    # to a UTF-8 stdout at the very end, which is where the surrogate raises.
    direct = render({"kind": "capture-failure", "detail": "\ud800 x",
                     "harness": "claude"})
    direct.encode("utf-8")  # must not raise
    assert direct.startswith("code:     capture-failure"), repr(direct[:40])

    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    crumb = _crumb_path(home)
    crumb.parent.mkdir(parents=True, exist_ok=True)
    crumb.write_text(json.dumps(_capture_failure(detail="\ud800 x")),
                     encoding="utf-8")

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    fields = _fields(proc.stdout)
    assert fields.get("code") == "capture-failure", proc.stdout
    assert "NOT been filed" in fields.get("what", ""), proc.stdout


# ── the Python renderer, directly ────────────────────────────────────────

#: The ONLY kind the Python renderer answers for. ``install-inert`` is owned by
#: the shell renderer (see the module docstring), so it is refused here.
_EXPECT_KIND = "capture-failure"


def test_render_refuses_any_kind_but_capture_failure():
    """The Python ``kind`` guard is the SOLE gate on the resolved path now (the
    shell no longer pre-filters), so it must be pinned directly — including for
    the ``install-inert`` kind the shell renders, and for a record with no kind
    at all.

    Mutation: render any dict, or fall back to a guessed kind — the foreign
    record produces a payload and this REDs."""
    from tortoise.capture_breadcrumb import render

    assert render({"kind": "something-else"}) == ""
    assert render({"kind": "install-inert", "harness": "claude",
                   "detail": "the seam resolved nothing",
                   "recorded_at": "2026-09-26T00:00:00Z"}) == ""
    assert render({}) == ""
    assert render(_capture_failure()).startswith(
        f"code:     {_EXPECT_KIND}")


def test_a_missing_harness_falls_back_to_unknown():
    """``harness`` is interpolated into the ``what:`` line.  A record written
    without one — or with an empty string — must render the documented
    ``"unknown"`` fallback, not an empty `` capture is affected.`` claim that
    names no harness at all.

    Mutation: ``record.get("harness")`` (drop ``or "unknown"``) — the field
    renders empty and this REDs."""
    from tortoise.capture_breadcrumb import render

    out = render({"kind": "capture-failure", "detail": "d"})
    assert "unknown capture is affected" in out, out

    # An empty string is the same missing-value case and takes the fallback too.
    out = render({"kind": "capture-failure", "detail": "d", "harness": ""})
    assert "unknown capture is affected" in out, out


def test_a_whitespace_only_harness_or_stamp_takes_the_fallback():
    """``render`` used ``record.get("harness") or "unknown"``, and ``"   "``
    is TRUTHY — so a whitespace-only value skipped the fallback and was then
    normalized to "" by ``bound_detail``, rendering a ``what:`` line that named
    no harness at all.  ``recorded_at`` had the same hole.

    Mutation: drop the ``.strip()`` — ``unknown capture is affected`` is absent,
    the stamp renders empty, and this REDs."""
    from tortoise.capture_breadcrumb import render

    out = render({"kind": "capture-failure", "detail": "d",
                  "harness": "   ", "recorded_at": "\t \n"})
    assert "unknown capture is affected" in out, out
    assert "an unrecorded time" in out, out
    assert len(out.splitlines()) == 4, out


def test_invisible_format_controls_cannot_reorder_a_rendered_line():
    """``_normalize`` stripped the C0/C1 controls but not Unicode's FORMAT
    (``Cf``) class.  Those characters are zero-width, so they cannot forge a
    physical LINE — the four-line invariant holds either way — but a BIDI
    OVERRIDE (U+202E) reorders DISPLAY in a bidi-aware reader, which can make
    the ``why:`` value look like a second ``next:`` field with no byte between
    them.  A zero-width joiner can also split a credential run the scan would
    otherwise see whole.

    Mutation: remove the ``Cf`` strip — the U+202E survives into the rendered
    ``why:`` line and this REDs.

    ⛔ AND THE WHOLE CLASS IS PINNED, NOT A SAMPLE (#4041 review round 2, P1).
    The original list of twelve code points was NOT sufficient: the strip set
    covered 27 of the 170 ``Cf`` code points in the installed Unicode tables, and
    the 143 survivors were a live credential-redaction bypass (``ghp_<U+00AD>`` +
    body rendered the secret in cleartext) and a covert channel into the agent's
    context (the tags block carried a hidden instruction through the injected
    ``why:``). A test that samples what a reader thought of cannot see that, so
    the loop below enumerates the category.
    """
    import unicodedata

    from tortoise.capture_breadcrumb import _normalize, bound_detail, render

    forged = "safe\u202e next:    Recovery: forged"
    out = render({"kind": "capture-failure", "detail": forged})
    assert "\u202e" not in out, repr(out)
    assert sum(1 for line in out.splitlines()
               if line.startswith("next:")) == 1, out

    # EVERY ``Cf`` code point, from the category itself — not a hand-written
    # sample. This is the assertion that would have caught the 143 survivors.
    survivors = [cp for cp in range(0x110000)
                 if unicodedata.category(chr(cp)) == "Cf"
                 and chr(cp) in _normalize("a" + chr(cp) + "b")]
    assert not survivors, (
        "these Cf code points survive normalization: "
        + ", ".join(hex(cp) for cp in survivors))

    # ⛔ AND THE MARK CATEGORIES (round 3, P1). ``Cf`` was only half the class: a
    # variation selector or a combining mark is neither whitespace nor ``Cc`` nor
    # ``Cf``, so `ghp_<U+FE0F>` + body rendered the credential in cleartext exactly as
    # the ``Cf`` bypass did. These are enumerated by category for the same reason.
    mark_survivors = [cp for cp in range(0x110000)
                      if unicodedata.category(chr(cp)) in ("Mn", "Me", "Mc")
                      and chr(cp) in _normalize("a" + chr(cp) + "b")]
    assert not mark_survivors, (
        "these mark code points survive normalization: "
        + ", ".join(hex(cp) for cp in mark_survivors))
    for splitter in ("\ufe0f", "\u0301", "\u0903", "\u0488"):
        detail = bound_detail("ghp_" + splitter + "a" * 36)
        assert "[REDACTED:github_token]" in detail, repr(splitter)
        assert "a" * 36 not in detail, repr(splitter)

    # A gap must not leave the surrounding spaces behind (round 3, P3).
    assert _normalize("a \u200b \u200b b") == "a b"
    assert _normalize("a \x00 b") == "a b"

    # A zero-width space inside a credential must not hide it from the scan.
    assert "[REDACTED:github_token]" in bound_detail("ghp_\u200b" + "a" * 36)
    # The same for a soft hyphen, the code point the sample above never covered and
    # on which the bypass was demonstrated.
    assert "[REDACTED:github_token]" in bound_detail("ghp_\u00ad" + "a" * 36)

    # No value that normalizes to empty may render as an empty field, for ANY
    # composition — the Cf-SEPARATED-BY-WHITESPACE rows are the ones that survived
    # the first attempt at this (they collapse to a single space AFTER the Cf
    # removal, so they were non-empty when the fallback was decided). ``why:`` is
    # included: it took no fallback at all until round 3.
    for empty_ish in ("\u200b", "\u2060", "\u00ad", "\ufeff", "\u202e",
                      "   ", "\t\n", "", None,
                      "\u200b \u200b", " \u200b ", "\u2060\t\u2060",
                      "\u200b\n\u200b", "\u200b \u200b \u200b"):
        line = render({"kind": "capture-failure", "harness": empty_ish,
                       "detail": "d"})
        assert "unknown capture is affected" in line, (empty_ish, line)
        assert "no detail was recorded" in render(
            {"kind": "capture-failure", "harness": "c", "detail": empty_ish}), (
                empty_ish, render({"kind": "capture-failure", "harness": "c",
                                   "detail": empty_ish}))


def test_bound_detail_redacts_before_it_bounds():
    """The ordering property at the unit level, on the SAME straddling input
    the hook test uses. ``bound_detail`` is where the ordering lives, so a
    direct assertion is the tightest proof that the property — not an
    incidental surrounding behaviour — is what makes the token disappear.

    Mutation: swap the two statements — ``ghp_`` survives in cleartext and this
    REDs."""
    from tortoise.capture_breadcrumb import MAX_DETAIL_CHARS, bound_detail

    token = "ghp_" + "a" * 36
    out = bound_detail("A" * 390 + " " + token + " " + "B" * 50)
    assert "ghp_" not in out, out
    assert "[REDACTED" in out, out
    assert len(out) <= MAX_DETAIL_CHARS + 1, len(out)


def test_the_redaction_scan_sees_normalized_text(monkeypatch):
    """Normalization runs BEFORE the ``redact_secrets`` scan, not after it.

    That order is the fix for the NUL re-formation: the shell transport strips
    NUL from its command substitution, so a credential whose body carries a NUL
    must be seen by the scan in its post-normalization form (NUL removed and
    the fragments re-joined) or the scan matches nothing and the transport
    re-forms it in cleartext downstream.  Whitespace is normalized in the same
    pass for the same reason — the scan must see the single-space line the
    renderer emits, not the raw run.

    The scan's INPUT is the observable here because the two orders are
    otherwise indistinguishable on this input (the transport is what completes
    the NUL story), so the spy is the tightest proof that the ORDER — not an
    incidental later normalization — is what removes the control characters.

    Mutation: redact first, then normalize — the captured input still carries
    the NUL and the raw newline run and this REDs."""
    import tortoise.capture_breadcrumb as cb

    seen: list[str] = []
    real = cb.redact_secrets

    def spy(text: str):
        seen.append(text)
        return real(text)

    monkeypatch.setattr(cb, "redact_secrets", spy)
    cb.bound_detail("ghp_\u0000" + "a" * 36 + "\n\n\n  x")

    assert seen, "the scan was never invoked"
    scanned = seen[0]
    assert "\u0000" not in scanned, repr(scanned)
    assert "\n" not in scanned and "\t" not in scanned, repr(scanned)
    assert "  " not in scanned, repr(scanned)


@pytest.mark.parametrize("control", ["\u0000", "\u0001", "\u0007", "\u001b", "\u007f"])
def test_a_control_split_credential_is_redacted_after_normalization(control):
    """A control character INSIDE a credential body splits the vendor anchor.
    The NUL is the reachable one (the shell transport strips it and re-joins
    the fragments — see the end-to-end test), but the whole non-whitespace
    C0/DEL class is normalized away for the same reason: the scan must see the
    credential the renderer/transport will emit, not a prefix no rule matches.

    Whitespace controls are NOT stripped — they collapse to a space with the
    rest of the whitespace and the credential stays split — so this
    parametrization deliberately uses only non-whitespace controls.

    Mutation: normalize controls AFTER the redaction scan — the token survives
    and this REDs."""
    from tortoise.capture_breadcrumb import bound_detail

    token = "ghp_" + "a" * 36
    out = bound_detail("token " + "ghp_" + control + "a" * 36)
    assert token not in out, out
    assert "[REDACTED:github_token]" in out, out


def test_a_very_large_detail_is_redacted_within_a_bounded_window(monkeypatch):
    """The detail is an error string that can be a whole HTTP response body
    stored verbatim (``import failed (HTTP {code}): {body}``).  The redaction
    table is a set of regexes whose cost is linear in the text, so scanning all
    of a large body stalls the session start — measured through the real hook:
    1 MB = 1.9 s, 50 MB = 109 s, past the hook's 60 s timeout.  Redaction must
    run over a bounded WINDOW while still redacting a secret that sits inside
    it.

    The WINDOW is pinned by the scan's INPUT LENGTH (a spy), which discriminates
    on structure and so is load-independent.  The elapsed assertion is only a
    coarse stall guard for the rest of the render and is deliberately loose: on
    this input ``bound_detail`` is dominated by the O(n) in-memory read +
    ``_normalize`` (``" ".join(text.split())``), measured ~0.65–0.94 s idle and
    ~1.3 s loaded — the SCAN is not the cost.  The UNWINDOWED scan alone
    measured ~72 s on the same input, so 10 s still discriminates a removed
    window on a loaded host without blaming the wrong component.

    Mutation: redact the whole detail (drop the window slice) — the spy sees the
    full 20 MB and the length assertion REDs (the elapsed guard would too)."""
    import tortoise.capture_breadcrumb as cb
    from tortoise.capture_breadcrumb import REDACT_WINDOW_CHARS, bound_detail

    seen: list[int] = []
    real = cb.redact_secrets

    def spy(text: str):
        seen.append(len(text))
        return real(text)

    monkeypatch.setattr(cb, "redact_secrets", spy)

    token = "ghp_" + "a" * 36
    detail = token + " " + "A" * 20_000_000

    start = time.perf_counter()
    out = bound_detail(detail)
    elapsed = time.perf_counter() - start

    # The secret inside the window is STILL redacted...
    assert "ghp_" not in out, out
    assert "[REDACTED:github_token]" in out, out[:200]
    # ...and the SCAN saw no more than the window (load-independent).
    assert seen, "the scan was never invoked"
    assert max(seen) <= REDACT_WINDOW_CHARS, (
        f"the redaction scan saw {max(seen)} chars on a 20 MB detail — the "
        f"window is not applied and the scan is unbounded")
    # A coarse stall guard for the WHOLE render (dominated by the O(n) normalize,
    # NOT the scan — see the docstring).  The unwindowed path measured ~72 s
    # here, so 10 s still discriminates.
    assert elapsed < 10.0, (
        f"bound_detail took {elapsed:.2f}s on a 20 MB detail — far past the "
        f"~0.9 s the in-memory read + `_normalize` costs, so the render is "
        f"unbounded somewhere the window does not cover")


def test_a_secret_straddling_the_window_boundary_cannot_leak_a_fragment():
    """The window must not cut MID-SECRET without redacting: a credential that
    BEGINS inside the rendered bound has to be captured WHOLE by the redaction
    window, or the cut leaves a prefix that no rule matches and that prefix is
    rendered in cleartext.  The window therefore extends a margin past the
    bound.  The tail beyond the window is never rendered, so skipping its
    redaction cannot expose it.

    Mutation: set the window to the bound (no margin) — the token's ``ghp_``
    prefix is left in cleartext inside the rendered 400 chars and this REDs."""
    from tortoise.capture_breadcrumb import MAX_DETAIL_CHARS, REDACT_WINDOW_CHARS, bound_detail

    # The window must extend past everything that is rendered.
    assert REDACT_WINDOW_CHARS > MAX_DETAIL_CHARS

    token = "ghp_" + "a" * 36

    # (1) The token STARTS inside the rendered bound and ENDS beyond it, so a
    # zero-margin window cuts it — the prefix would render.  The leading space
    # is the rule's own word boundary (a credential glued to a letter is not
    # the anchored shape the table matches).
    out = bound_detail("A" * 394 + " " + token + " " + "B" * 50)
    assert "ghp_" not in out, out
    assert "aaaaa" not in out, out

    # (2) The same token placed so its redaction MARKER is complete within the
    # rendered bound: redaction really ran (not merely truncated away).
    marked = bound_detail("A" * 390 + " " + token + " " + "B" * 50)
    assert "[REDACTED" in marked, marked

    # (3) The token straddling the REDACTION WINDOW's own end must still not
    # put a cleartext fragment in the output, and the output stays bounded.
    tail = "A" * (REDACT_WINDOW_CHARS - 6) + " " + token + " " + "C" * 50
    out2 = bound_detail(tail)
    assert "ghp_" not in out2, out2
    assert len(out2) <= MAX_DETAIL_CHARS + 1, len(out2)


def test_the_structured_multi_delimiter_residual_at_the_window_edge():
    """The documented WINDOW residual, pinned rather than asserted.

    A VENDOR-PREFIX rule is anchored by its prefix, so the window's cut at
    end-of-string leaves the prefix intact and the rule still matches — it fails
    CLOSED (pinned by
    ``test_vendor_prefix_rules_fail_closed_at_the_window_edge``).  A STRUCTURED
    MULTI-DELIMITER rule instead needs delimiters that can fall PAST the cut:
    the Slack ``xapp-…`` form puts only its first field inside the window, and
    a ``jwt``'s second and third dot-separated segments sit the same way.  A
    token of that family whose interior is longer than the margin therefore
    renders its prefix with NO marker.

    This test exists so the residual is BOUND to the code: it is what REDs if
    the window/margin relationship changes, and it is the evidence the
    ``_REDACT_MARGIN`` comment points to instead of claiming safety.
    """
    from tortoise.capture_breadcrumb import MAX_DETAIL_CHARS, REDACT_WINDOW_CHARS, bound_detail
    from tortoise.security import redact_secrets

    # (1) Inside the window the whole xapp token redacts — the rule works.
    small = "xapp-1-" + "A" * 40 + "-1234-" + "B" * 20
    assert "[REDACTED:slack_token]" in bound_detail(small), bound_detail(small)

    # (2) A token whose interior runs past the window: the later delimiters
    # fall beyond the cut, no rule matches the truncated prefix, and the prefix
    # is what renders — bounded, but cleartext.  THIS is the residual.
    huge = "xapp-1-" + "A" * 200_000 + "-1234-" + "B" * 100
    out = bound_detail(huge)
    assert len(huge) > REDACT_WINDOW_CHARS, "premise: the token exceeds the window"
    assert "[REDACTED" not in out, out[:120]
    assert out.startswith("xapp-1-"), out[:120]
    assert len(out) <= MAX_DETAIL_CHARS + 1, len(out)

    # (3) The miss is the WINDOW, not a broken rule: scanning the same text
    # whole redacts it — which is why REDACT_WINDOW_CHARS (not the rule) is the
    # thing the residual is attributed to.
    assert redact_secrets(huge)[1].get("slack_token") == 1


def test_the_redaction_margin_carries_headroom_beyond_a_toy_token():
    """``_REDACT_MARGIN``'s comment claims the window has "orders of
    magnitude" of headroom past the rendered bound, but every other window test
    only constrains the margin to be larger than the ~36-char token — a margin
    of a few hundred would pass all of them.  The margin is what makes the
    STRUCTURED MULTI-DELIMITER family fail CLOSED for a realistic token: a
    Slack ``xapp-`` token whose interior is a few KiB long sits entirely inside
    a 64 KiB margin (so the later delimiters are present and the rule matches),
    but falls outside a 2 KiB one (so the cut leaves only the unmatched prefix —
    the residual the comment names).

    Mutation: ``_REDACT_MARGIN = 2048`` — the token's interior runs past the
    window, no rule matches the prefix, ``xapp-1-`` renders in cleartext, and
    this REDs."""
    from tortoise.capture_breadcrumb import MAX_DETAIL_CHARS, bound_detail

    token = "xapp-1-" + "A" * 8192 + "-1234-" + "B" * 100
    # Both premises are LITERAL, not read from the module: the mutation under
    # test changes the module's margin, and a premise that moved with it would
    # fail on the premise instead of on the leak the margin causes.
    assert len(token) > MAX_DETAIL_CHARS + 2048, (
        "premise: the token's interior runs past a 2 KiB margin")
    assert len(token) < MAX_DETAIL_CHARS + 64 * 1024, (
        "premise: the token fits whole inside the documented 64 KiB window")
    out = bound_detail(token)
    assert "xapp-" not in out, out[:120]
    assert "[REDACTED:slack_token]" in out, out[:120]


@pytest.mark.parametrize("name, text", [
    ("ghp_", "ghp_" + "A" * 200_000),
    ("github_pat_", "github_pat_" + "A" * 200_000),
    ("glpat-", "glpat-" + "A" * 200_000),
    ("sk-ant-", "sk-ant-" + "A" * 200_000),
    ("AIza", "AIza" + "A" * 200_000),
    ("xoxb-", "xoxb-" + "A" * 200_000),
    ("aws_secret_access_key", "aws_secret_access_key=" + "A" * 200_000),
    ("Authorization: Bearer", "Authorization: Bearer " + "A" * 200_000),
    ("PEM", "-----BEGIN RSA PRIVATE KEY-----\n" + "A" * 200_000),
])
def test_vendor_prefix_rules_fail_closed_at_the_window_edge(name, text):
    """The OTHER half of ``_REDACT_MARGIN``'s claim, pinned: a VENDOR-PREFIX
    rule is anchored by its PREFIX, so a window cut at end-of-string leaves the
    prefix intact and the rule still matches — the token's body may run past the
    whole window and the rendered bound still shows the MARKER, never the
    prefix.  This is the safe direction the comment describes, and this test is
    what keeps it evidence rather than assertion.

    Mutation: de-anchor a vendor rule so it instead needs its trailing delimiter
    present (the JWT/``xapp-`` shape) — the prefix renders in cleartext and this
    REDs."""
    from tortoise.capture_breadcrumb import REDACT_WINDOW_CHARS, bound_detail

    assert len(text) > REDACT_WINDOW_CHARS, "premise: the token exceeds the window"
    out = bound_detail(text)
    assert "[REDACTED" in out, (name, out[:120])


def test_render_returns_a_newline_terminated_payload():
    """``render`` returns its four lines TERMINATED by a newline.  The shell
    capture half strips trailing newlines in ``$(...)`` and re-adds one with
    ``printf '%s\\n'``, so the terminator is benign END TO END — but it is part
    of ``render``'s own contract (the payload is a complete final line, not a
    fragment a caller might concatenate) and nothing pinned it.

    Mutation: return ``"\\n".join(lines)`` without the terminator — this REDs."""
    from tortoise.capture_breadcrumb import render

    out = render(_capture_failure())
    assert out.endswith("\n"), repr(out[-40:])
    assert out.count("\n") == 4, repr(out)
    assert not out.endswith("\n\n"), repr(out[-4:])


@pytest.mark.parametrize("label, raw", [
    ("malformed JSON", b"{not json at all"),
    ("non-UTF-8 bytes", b'{"kind": "capture-failure", "detail": "\xff\xfe"}'),
])
def test_render_file_refuses_undecodable_and_malformed_records(tmp_path, label, raw):
    """``render_file`` runs inside a hook whose exit-0 contract is inviolable:
    an unreadable, undecodable or malformed record must yield NO output rather
    than propagate.  The catch is broad on purpose.

    Mutation: narrow ``except Exception`` to ``except OSError`` — the
    ``JSONDecodeError``/``UnicodeDecodeError`` escapes and this REDs."""
    from tortoise.capture_breadcrumb import render_file

    path = tmp_path / "crumb.json"
    path.write_bytes(raw)
    assert render_file(path) == "", label


def test_render_file_refuses_a_deeply_nested_record(tmp_path):
    """A deeply nested document raises ``RecursionError`` inside
    ``json.loads``, which is NOT an ``OSError`` — the docstring names this
    case, so the catch must stay broad enough to absorb it.

    Mutation: narrow ``except Exception`` to ``except OSError`` — the
    ``RecursionError`` escapes and this REDs."""
    from tortoise.capture_breadcrumb import render_file

    path = tmp_path / "crumb.json"
    path.write_text("[" * 200_000, encoding="utf-8")
    assert render_file(path) == ""


@pytest.mark.parametrize("raw", [
    "[]", '["capture-failure"]', '"a string"', "42", "true", "null",
])
def test_render_file_refuses_a_non_dict_document(tmp_path, raw):
    """``render_file``'s docstring says a non-dict document is refused the same
    way as malformed JSON.  A top-level JSON list/string/number/bool/null is
    VALID JSON, so it passes ``json.loads`` and reaches ``render`` — where
    ``.get`` raises.  Without the ``isinstance(data, dict)`` guard that
    ``AttributeError`` escapes ``render_file`` and breaks the exit-0 contract
    the hook depends on.

    Mutation: drop the ``isinstance(data, dict)`` guard — this REDs with
    ``AttributeError`` instead of ``""``."""
    from tortoise.capture_breadcrumb import render_file

    path = tmp_path / "crumb.json"
    path.write_text(raw, encoding="utf-8")
    assert render_file(path) == "", raw


def test_a_bom_prefixed_record_is_still_rendered(tmp_path):
    """A BOM-prefixed record is what a Windows-authored copy looks like. The
    renderer decodes ``utf-8-sig`` so the BOM is stripped rather than making
    ``json.loads`` raise — which would silently read as "no breadcrumb".

    Mutation: decode plain ``utf-8`` — the parse raises, nothing renders, and
    this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    crumb = _crumb_path(home)
    crumb.parent.mkdir(parents=True, exist_ok=True)
    crumb.write_text("\ufeff" + json.dumps(_capture_failure(), indent=2),
                     encoding="utf-8")

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert _fields(proc.stdout).get("code") == "capture-failure", proc.stdout


def test_an_oversized_record_is_bounded_and_still_tells_the_agent(tmp_path):
    """A breadcrumb record can be huge: the ``capture-failure`` writer stores an
    UNCAPPED ``e.read()`` HTTP error body
    (``__main__._record_capture_error(harness, f"import failed ...: {body}")``),
    and ``REDACT_WINDOW_CHARS`` bounds only the redaction SCAN — the read,
    ``json.loads`` and ``_normalize`` stayed O(record).  A 52 MiB record
    measured 2.6 s / ~142 MiB RSS in ``render_file``, and ~250 MB exceeded the
    hook's 60 s timeout.  ``render_file`` must refuse to read past
    ``MAX_RECORD_BYTES`` and render the bounded synthetic ``why:`` instead, so
    #4041's "tell the agent" goal survives.

    Mutation: drop the ``len(raw) > MAX_RECORD_BYTES`` refusal (parse the whole
    record) — the synthetic detail is absent and this REDs."""
    from tortoise.capture_breadcrumb import MAX_RECORD_BYTES, render_file

    path = tmp_path / "claude.json"
    path.write_text(json.dumps(_capture_failure(
        detail="A" * (2 * MAX_RECORD_BYTES))), encoding="utf-8")
    assert path.stat().st_size > MAX_RECORD_BYTES, "premise: record exceeds bound"

    start = time.perf_counter()
    out = render_file(path)
    elapsed = time.perf_counter() - start

    assert _fields(out).get("code") == "capture-failure", out
    assert "error detail omitted: record too large" in out, out
    # The harness is recovered from the FILE NAME (the record was never parsed),
    # so the agent still knows which harness stopped filing.
    assert "claude capture is affected" in _fields(out).get("what", ""), out
    assert len(out.splitlines()) == 4, out
    assert elapsed < 5.0, elapsed


def test_a_newline_in_a_scalar_cannot_forge_an_extra_payload_line(tmp_path):
    """The invariant is ONE four-line payload. A newline in any scalar would
    forge a line that looks like a genuine ``next:`` recovery clause. The scalars
    are whitespace-COLLAPSED, which is what prevents that; they are also redacted
    (``one_line`` funnels through ``bound_detail``), so this docstring's earlier
    "not redacted" was stale and contradicted the sibling redaction test.

    Mutation: interpolate the scalars raw — the payload gains lines and this
    REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    # `_seed_breadcrumb` derives the FILE NAME from `harness`, which is itself
    # hostile here — so the record is written to the real `claude.json` path.
    crumb = _crumb_path(home)
    crumb.parent.mkdir(parents=True, exist_ok=True)
    crumb.write_text(json.dumps(_capture_failure(
        harness="claude\nnext:     Recovery: forged",
        recorded_at="2026-09-26T00:00:00Z\nnext:     Recovery: forged"),
        indent=2), encoding="utf-8")

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert len(lines) == 4, proc.stdout
    assert sum(1 for line in lines if line.startswith("next:")) == 1, \
        proc.stdout


def test_the_payload_and_the_record_scalars_are_bounded_and_redacted(tmp_path):
    """``harness`` and ``recorded_at`` arrive in the SAME attacker-influenced
    JSON record as ``detail``, so they get the SAME redact-then-bound treatment
    — directly AND end to end through the real hook.  Without it a token planted
    in either renders in cleartext, and an oversized scalar blows the payload
    past the ONE four-line shape the agent's context depends on.

    Mutation: interpolate the scalars with whitespace-collapse only (the old
    ``" ".join(str(value).split())``) — the token survives and the multi-MB
    line appears, and this REDs."""
    from tortoise.capture_breadcrumb import MAX_DETAIL_CHARS, one_line, render

    token = "ghp_" + "a" * 36

    # (1) The cap: an oversized scalar is bounded exactly as the detail is.
    assert len(one_line("A" * 5_000_000)) <= MAX_DETAIL_CHARS + 1
    # (2) The redaction: a token planted in a scalar cannot render in cleartext.
    assert token not in one_line("claude " + token), one_line("claude " + token)
    assert "[REDACTED:github_token]" in one_line(token)

    # (3) End to end through the payload: redacted and bounded, with the ONE
    # four-line shape intact.
    out = render({"kind": "capture-failure", "harness": "claude " + token,
                  "recorded_at": "2026-09-26T00:00:00Z " + token, "detail": "d"})
    assert token not in out, out
    assert "[REDACTED:github_token]" in out, out
    assert len(out.splitlines()) == 4, out

    huge = render({"kind": "capture-failure", "harness": "A" * 5_000_000,
                   "recorded_at": "t", "detail": "d"})
    assert len(huge.splitlines()) == 4, huge
    widest = max(len(line) for line in huge.splitlines())
    assert widest <= 900, (widest, huge[:120])

    # (4) The same through the REAL shipped hook: the crumb on disk carries the
    # secret in its scalars and the hook's stdout must not.
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    crumb = _crumb_path(home)
    crumb.parent.mkdir(parents=True, exist_ok=True)
    crumb.write_text(json.dumps(_capture_failure(
        harness="claude " + token,
        recorded_at="2026-09-26T00:00:00Z " + token), indent=2),
        encoding="utf-8")

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert token not in proc.stdout, proc.stdout
    assert "[REDACTED:github_token]" in proc.stdout, proc.stdout
    assert len(proc.stdout.splitlines()) == 4, proc.stdout


def test_the_vendor_phrasing_constraint_is_recorded_in_the_code(tmp_path):
    """The imperative ban is vendor-mandated, not stylistic.  It is pinned HERE
    because the next reader's most natural "fix" is to turn the recovery half
    into commands — and that silently costs the payload its context-injection
    path.  The note must survive in both renderers.

    Mutation: delete the constraint note from either file — this REDs."""
    hook = (REPO / "tortoise" / "claude-hooks" / "session-start.sh").read_text(
        encoding="utf-8")
    module = (REPO / "tortoise" / "capture_breadcrumb.py").read_text(
        encoding="utf-8")
    for name, text in (("session-start.sh", hook),
                       ("capture_breadcrumb.py", module)):
        assert re.search(r"prompt-injection", text), name
        assert re.search(r"imperative", text, re.IGNORECASE), name
