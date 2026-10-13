"""#2121 — the Hermes two-way seam (epic #2080 end-state platform).

Hermes (NousResearch/hermes-agent) is the ONE harness in the seam family whose
per-turn surface is a NATIVE PLUGIN rather than a shell command: the injection
contract is ``register(ctx)`` → ``ctx.register_hook("pre_llm_call", cb)`` and
the callback's RETURN value (``{"context": ...}``) is appended to the current
turn's user message (docs ``plugins/index.md`` §"``pre_llm_call`` context
injection"; ``agent/turn_context.py::_collect_pre_llm_call_context``). There is
therefore no registration file whose "command" can point at
``volunteer-turn.sh``, and this seam ships a directory plugin instead.

This file pins both directions of the seam, at the surfaces a regression would
actually move:

1. ``tortoise install hermes`` — the agent-first onboarding writes the shipped
   plugin (``plugin.yaml`` + ``__init__.py``) under
   ``<dir>/.hermes/plugins/tortoise-volunteer/``; it is idempotent, refuses a
   foreign directory of the same name, and ``--uninstall`` deletes only what it
   can prove it wrote.
2. The INSTALLED plugin, executed for real (imported from the installed path
   with a fake ``tortoise`` reflex on PATH): ``register()`` wires
   ``pre_llm_call``, the handler runs the shared reflex, returns
   ``{"context": block}``, passes the harness ``session_id`` through (the
   reflex's cross-turn re-mention suppression), bounds the block below
   Hermes' 10,000-char spill cap, and fails OPEN (no injection) on a missing
   reflex / the kill switch.
3. ``parse_hermes`` — the ingest half, over a REAL SQLite ``state.db`` fixture
   shaped from the store's own DDL (``hermes_state_common.py``): the live
   transcript is ``messages.active = 1`` ordered by ``id`` (``timestamp`` is
   NOT monotonic), generated compaction summaries and non-user/assistant roles
   are skipped, the default selection is the most recently started session,
   and the store is opened read-only (a backfill must never mutate a live
   Hermes store).
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest

from tortoise.session_import import (
    PARSERS,
    parse_hermes,
    parse_transcript,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PY = sys.executable
_PLUGIN_MARKER = "tortoise-hermes-plugin"

# Hermes' reserved content-encoding sentinel, written literally and independently
# of the implementation: `hermes_state_messages.py::_CONTENT_JSON_PREFIX`. A list
# or dict ``content`` is stored as this prefix + ``json.dumps(...)`` (sqlite3
# binds only scalars) and decoded back by ``_decode_content``.
_HERMES_JSON_PREFIX = "\x00json:"

# The subset of the Hermes store DDL this seam reads, kept faithful to
# ``hermes_state_common.py`` (column names/types + the ``active`` /
# ``_compressed_summary`` defaults the parser filters on).
_STATE_DB_DDL = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    message_count INTEGER DEFAULT 0
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    compacted INTEGER NOT NULL DEFAULT 0,
    _compressed_summary INTEGER NOT NULL DEFAULT 0
);
"""


def _env(**extra) -> dict:
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    env.update(extra)
    return env


def _run(argv, env, cwd: str | None = None):
    return subprocess.run(
        [_PY, "-m", "tortoise", *argv],
        env=env, cwd=cwd, capture_output=True, text=True, timeout=180,
    )


def _make_state_db(path: Path, sessions: dict[str, list[dict]],
                   started: dict[str, float] | None = None) -> Path:
    """Write a real Hermes-shaped ``state.db``.

    ``sessions``: {session_id: [message dicts]}; each message carries ``id``
    (insertion order), ``role``, ``content`` and optional ``active`` /
    ``summary`` flags.

    The store is written in WAL mode — Hermes' own mode — and closed, which
    checkpoints and reaps ``-wal``/``-shm``. That leaves a WAL store whose
    ``-shm`` is ABSENT: exactly the shape where an IN-PLACE read-only open
    makes SQLite CREATE the sidecars beside it (the source directory is
    written), which the snapshot reader must avoid.
    """
    started = started or {}
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_STATE_DB_DDL)
        for sid, msgs in sessions.items():
            conn.execute("INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                         (sid, started.get(sid, 1_000.0)))
            for m in msgs:
                conn.execute(
                    "INSERT INTO messages (id, session_id, role, content, "
                    "timestamp, active, _compressed_summary) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (m["id"], sid, m["role"], m.get("content"),
                     m.get("ts", float(m["id"])),
                     1 if m.get("active", True) else 0,
                     1 if m.get("summary", False) else 0))
        conn.commit()
    finally:
        conn.close()
    return path


def _make_live_wal_state_db(path: Path, rows: list[tuple[int, str, str]]) -> Path:
    """A Hermes store in WAL mode with `-wal`/`-shm` ON DISK — the LIVE shape.

    SQLite deletes the sidecars when the last connection closes (it checkpoints
    on close), so they are saved while the connection is open and restored
    afterwards. The rows are committed with ``wal_autocheckpoint=0``, so their
    pages live ONLY in the `-wal` file: a reader that ignores the WAL returns
    nothing for them. ``rows`` are ``(id, role, content)``.
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_STATE_DB_DDL)
        conn.execute("INSERT INTO sessions (id, started_at) VALUES ('s1', 1000.0)")
        # Autocheckpoint OFF *before* the inserts, so every row's pages stay in
        # the WAL and the main db file is never rewritten by the close.
        conn.execute("PRAGMA wal_autocheckpoint=0")
        for row_id, role, content in rows:
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, "
                "timestamp) VALUES (?, 's1', ?, ?, ?)",
                (row_id, role, content, float(row_id)))
        conn.commit()
        sidecars = {p: p.read_bytes()
                    for p in (path.with_name(path.name + "-wal"),
                              path.with_name(path.name + "-shm")) if p.exists()}
    finally:
        conn.close()
    assert sidecars, "the WAL fixture produced no sidecars to pin"
    for p, data in sidecars.items():
        p.write_bytes(data)
    return path


# ── 1. `tortoise install hermes` — the shipped plugin registration ───────


def _plugin_dir(root: Path) -> Path:
    return root / ".hermes" / "plugins" / "tortoise-volunteer"


def test_install_hermes_writes_shipped_plugin(tmp_path):
    r = _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    assert r.returncode == 0, r.stderr
    target = _plugin_dir(tmp_path)
    manifest = (target / "plugin.yaml").read_text()
    init = (target / "__init__.py").read_text()
    # The injection contract Hermes' own doctor validates: the declared hook
    # must be the hook `register(ctx)` actually registers.
    assert "pre_llm_call" in manifest
    assert 'ctx.register_hook("pre_llm_call", on_pre_llm_call)' in init
    # Ownership marker — the thing --uninstall and the overwrite refusal key on.
    assert _PLUGIN_MARKER in init
    assert _PLUGIN_MARKER in manifest
    assert "Installed" in r.stdout


def test_install_hermes_is_idempotent_and_uninstalls(tmp_path):
    _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    target = _plugin_dir(tmp_path)
    marker_text = (target / "__init__.py").read_text()
    # A reinstall UPDATES in place (never a second copy, never a refusal).
    r = _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    assert r.returncode == 0, r.stderr
    assert "Updated" in r.stdout
    assert (target / "__init__.py").read_text() == marker_text
    # --uninstall removes the plugin, leaving the .hermes tree clean.
    r = _run(["install", "hermes", "--dir", str(tmp_path), "--uninstall"],
             _env())
    assert r.returncode == 0, r.stderr
    assert not target.exists()
    # Uninstalling again is a clean no-op, never an inverted reinstall.
    r = _run(["install", "hermes", "--dir", str(tmp_path), "--uninstall"],
             _env())
    assert r.returncode == 0, r.stderr
    assert not target.exists()


def test_install_hermes_writes_atomically(tmp_path):
    """The plugin files go through `_atomic_write_text` (#3918): a reinstall
    REPLACES the directory entry (fresh temp + ``os.replace``) instead of
    truncating in place, and a planted leaf symlink pointing outside the
    install dir is replaced rather than written THROUGH (#2121 review)."""
    _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    target = _plugin_dir(tmp_path)
    before = os.stat(target / "plugin.yaml").st_ino
    r = _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    assert r.returncode == 0, r.stderr
    # write_text keeps the inode (truncate-in-place); os.replace swaps it.
    assert os.stat(target / "plugin.yaml").st_ino != before

    # The root→target symlink guard never inspects the LEAF files, so only
    # the atomic replace stops a planted `plugin.yaml` symlink from writing
    # through to a file outside the install dir.
    outside = tmp_path / "outside.yaml"
    outside.write_text("ORIGINAL\n")
    (target / "plugin.yaml").unlink()
    (target / "plugin.yaml").symlink_to(outside)
    r = _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    assert r.returncode == 0, r.stderr
    assert outside.read_text() == "ORIGINAL\n"
    assert not (target / "plugin.yaml").is_symlink()
    assert (target / "plugin.yaml").is_file()


def test_install_hermes_prints_the_required_activation_steps(tmp_path):
    """Hermes gates a project plugin through TWO opt-ins this installer cannot
    perform: ``HERMES_ENABLE_PROJECT_PLUGINS`` (off by default) and
    ``plugins.enabled`` (``hermes plugins enable``). Printing plain success
    over a plugin Hermes never loads is an inert install read as a working
    seam (#3808 R13), so BOTH required steps plus the real ingestion command
    must be in the output (#2121 review P1).
    """
    r = _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "HERMES_ENABLE_PROJECT_PLUGINS=1" in out
    assert "hermes plugins enable tortoise-volunteer" in out
    # The documented ingestion command must actually run — `--file` is
    # required, so the old `sessions import --harness hermes <state.db>` form
    # failed with an argparse error.
    assert ("tortoise sessions import --harness hermes --file <state.db>"
            in out)


def test_install_hermes_dry_run_writes_nothing(tmp_path):
    r = _run(["install", "hermes", "--dir", str(tmp_path), "--dry-run"],
             _env())
    assert r.returncode == 0, r.stderr
    assert "[dry-run]" in r.stdout
    assert not _plugin_dir(tmp_path).exists()


def test_install_hermes_refuses_foreign_plugin_dir(tmp_path):
    """A `tortoise-volunteer` directory this installer did not write is never
    overwritten — and never deleted by --uninstall."""
    target = _plugin_dir(tmp_path)
    target.mkdir(parents=True)
    (target / "__init__.py").write_text("def register(ctx):\n    pass\n")
    user_bytes = (target / "__init__.py").read_text()
    r = _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    assert r.returncode == 1
    assert "refusing to overwrite" in r.stderr.lower()
    assert (target / "__init__.py").read_text() == user_bytes
    r = _run(["install", "hermes", "--dir", str(tmp_path), "--uninstall"],
             _env())
    assert r.returncode == 1
    assert "refusing to delete" in r.stderr.lower()
    assert (target / "__init__.py").read_text() == user_bytes


def test_install_hermes_refuses_file_where_plugin_dir_belongs(tmp_path):
    target = _plugin_dir(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("not a directory")
    r = _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    assert r.returncode == 1
    assert "not a directory" in r.stderr
    assert target.read_text() == "not a directory"


def test_install_hermes_replaces_a_dangling_plugin_symlink(tmp_path):
    """A DANGLING in-root symlink at the plugin path is neither a directory
    nor a file: ``exists()`` follows the link and is False, so the ownership
    guards skip it and ``mkdir(exist_ok=True)`` re-raises FileExistsError —
    rc 1 with an opaque traceback — while the write-free dry run reports
    success over the SAME tree (#2121 review). The file seams REPLACE a
    dangling leaf symlink, so a directory-shaped seam does too: the link is
    dropped, the real directory created, and the install succeeds.

    MUTATION: drop the ``unlink()`` → the real install exits 1 (RED).
    """
    target = _plugin_dir(tmp_path)
    target.parent.mkdir(parents=True)
    target.symlink_to("./missing-dir")  # dangling, resolves INSIDE root
    assert target.is_symlink() and not target.exists()

    # The dry run must not report a success the real run cannot deliver.
    r = _run(["install", "hermes", "--dir", str(tmp_path), "--dry-run"],
             _env())
    assert r.returncode == 0, r.stderr

    r = _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    assert r.returncode == 0, r.stderr
    assert not target.is_symlink()
    assert (target / "plugin.yaml").is_file()
    assert (target / "__init__.py").is_file()


def test_install_hermes_uninstall_keeps_unrelated_files(tmp_path):
    """Only the two files the installer wrote are removed; a file the USER
    added keeps the directory alive (an uninstall never deletes what it
    cannot prove it created)."""
    _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    target = _plugin_dir(tmp_path)
    (target / "notes.txt").write_text("user data\n")
    r = _run(["install", "hermes", "--dir", str(tmp_path), "--uninstall"],
             _env())
    assert r.returncode == 0, r.stderr
    assert target.is_dir()
    assert (target / "notes.txt").read_text() == "user data\n"
    assert not (target / "__init__.py").exists()


# ── 2. The INSTALLED plugin — the real pre_llm_call seam ─────────────────


def _install_and_load(tmp_path, monkeypatch, *, block: str = "",
                      argv_file: Path | None = None, **env_extra):
    """Install the shipped plugin into a temp root and import THAT copy.

    A fake ``tortoise`` console script stands in for the reflex: it records
    its argv (so the test can prove ``--session-id`` is passed) and prints
    ``block``. The fallback candidates are cleared so nothing on the host can
    masquerade as the reflex.
    """
    r = _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    assert r.returncode == 0, r.stderr
    plugin = _plugin_dir(tmp_path) / "__init__.py"

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "tortoise"
    script = "#!/bin/bash\n"
    if argv_file is not None:
        script += f'printf "%s\\n" "$@" > {shlex.quote(str(argv_file))}\n'
    script += "cat > /dev/null\n"
    if block:
        script += f"printf '%s\\n' {shlex.quote(block)}\n"
    fake.write_text(script)
    fake.chmod(0o755)

    monkeypatch.setenv("PATH", f"{bin_dir}:/bin:/usr/bin")
    monkeypatch.setenv("TORTOISE_SRC_DIR", "")
    monkeypatch.setenv("VIRTUAL_ENV", "")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # The breadcrumb path must come from HOME, not a leaked override.
    monkeypatch.delenv("TORTOISE_IMPORT_RECEIPT_DIR", raising=False)
    for key, value in env_extra.items():
        monkeypatch.setenv(key, value)

    spec = importlib.util.spec_from_file_location(
        "tortoise_hermes_plugin_under_test", plugin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BLOCK = "- **CI deploys on merge** → point/pt_ci123 — GitHub Actions on main"


def test_installed_plugin_registers_pre_llm_call(tmp_path, monkeypatch):
    module = _install_and_load(tmp_path, monkeypatch, block=_BLOCK)
    seen = []

    class _Ctx:
        def register_hook(self, name, cb):
            seen.append((name, cb))

    module.register(_Ctx())
    assert seen == [("pre_llm_call", module.on_pre_llm_call)]


def test_pre_llm_call_returns_context_and_passes_session_id(
        tmp_path, monkeypatch):
    argv_file = tmp_path / "argv.txt"
    module = _install_and_load(tmp_path, monkeypatch, block=_BLOCK,
                               argv_file=argv_file)
    out = module.on_pre_llm_call(
        user_message="How is CI deploy triggered?", session_id="sess-42")
    assert out == {"context": _BLOCK}
    argv = argv_file.read_text().split("\n")
    # The reflex is the SHARED `tortoise volunteer` CLI and carries the
    # harness session id — the reflex's cross-turn re-mention suppression.
    assert argv[0] == "volunteer"
    assert "--session-id" in argv and "sess-42" in argv


def test_pre_llm_call_no_session_id_still_injects(tmp_path, monkeypatch):
    module = _install_and_load(tmp_path, monkeypatch, block=_BLOCK)
    out = module.on_pre_llm_call(user_message="what do we know?")
    assert out == {"context": _BLOCK}


def test_pre_llm_call_reads_text_parts_not_a_multimodal_repr(
        tmp_path, monkeypatch):
    """#2121 review: ``user_message`` is ``str | list``.

    A multimodal turn passes the content parts as a LIST, and an image part
    carries a base64 data URL. ``str()`` on that list hands the reflex Python's
    repr of the parts, so the reflex searches a repr; the 5 s timeout then
    fires and the seam records a FALSE ``install-inert`` breadcrumb for an
    install that is perfectly healthy -- on every image turn. Only the text
    parts are the turn, and the prompt handed to the reflex is bounded.
    """
    module = _install_and_load(tmp_path, monkeypatch, block=_BLOCK)
    seen: dict[str, str] = {}

    def _capture(prompt, session_id):
        seen["prompt"] = prompt
        return _BLOCK

    monkeypatch.setattr(module, "_reflex_block", _capture)
    parts = [
        {"type": "text", "text": "what does this screenshot show?"},
        {"type": "image_url",
         "image_url": {"url": "data:image/png;base64," + "A" * 20_000}},
    ]
    out = module.on_pre_llm_call(user_message=parts, session_id="sess-9")
    assert out == {"context": _BLOCK}
    assert seen["prompt"] == "what does this screenshot show?"
    assert "base64" not in seen["prompt"]
    assert "image_url" not in seen["prompt"]

    # A text-only list and a plain string are the same turn.
    assert module._prompt_text([{"type": "text", "text": "hi"}]) == "hi"
    assert module._prompt_text("hi") == "hi"

    # A huge text turn reaches the reflex BOUNDED, never whole.
    module.on_pre_llm_call(user_message=[{"type": "text",
                                          "text": "x" * 50_000}])
    assert len(seen["prompt"]) == module.MAX_PROMPT_CHARS
    assert seen["prompt"] == "x" * module.MAX_PROMPT_CHARS


def test_pre_llm_call_bounds_the_block_below_the_hermes_spill_cap(
        tmp_path, monkeypatch):
    """Hermes spills >10,000-char hook context to disk and shows a preview;
    the seam bounds itself at 8,000 so the block is never spilled."""
    module = _install_and_load(tmp_path, monkeypatch, block="x" * 20_000)
    out = module.on_pre_llm_call(user_message="how is CI deployed?")
    assert out is not None
    assert len(out["context"]) == module.MAX_INJECT_CHARS == 8000


def test_pre_llm_call_fails_open_without_a_reflex(tmp_path, monkeypatch):
    """No `tortoise` anywhere → no injection, no raise, the turn proceeds."""
    module = _install_and_load(tmp_path, monkeypatch)  # no block, but a fake CLI
    # Remove the fake CLI: nothing resolvable.
    (tmp_path / "bin" / "tortoise").unlink()
    assert module.on_pre_llm_call(user_message="hi there") is None


def test_pre_llm_call_fails_open_when_the_reflex_fails(tmp_path, monkeypatch):
    module = _install_and_load(tmp_path, monkeypatch)
    fake = tmp_path / "bin" / "tortoise"
    fake.write_text("#!/bin/bash\nexit 1\n")
    fake.chmod(0o755)
    assert module.on_pre_llm_call(user_message="hi there") is None


def test_pre_llm_call_fails_open_on_empty_graph(tmp_path, monkeypatch):
    """A clean reflex run with NOTHING above the gate prints nothing — the
    documented content fail-open (no injection, exit 0)."""
    module = _install_and_load(tmp_path, monkeypatch)  # block="" → prints nothing
    assert module.on_pre_llm_call(user_message="hi there") is None


def test_pre_llm_call_kill_switch_skips_the_reflex(tmp_path, monkeypatch):
    argv_file = tmp_path / "argv.txt"
    module = _install_and_load(tmp_path, monkeypatch, block=_BLOCK,
                               argv_file=argv_file,
                               TORTOISE_VOLUNTEER_DISABLE="1")
    assert module.on_pre_llm_call(user_message="how is CI deployed?") is None
    assert not argv_file.exists()  # the reflex was never invoked


def test_plugin_resolves_the_installer_recorded_module_dir(tmp_path,
                                                           monkeypatch):
    """No `tortoise` on PATH → the plugin falls back to the module dir the
    `~/.tortoise/hook-src-dir` record names — the same rung the shipped shell
    hook walks. The record is written by the shell-hook seams (`install
    hermes` writes NO record); `TORTOISE_SRC_DIR` populates the same rung."""
    module = _install_and_load(tmp_path, monkeypatch, block=_BLOCK)
    (tmp_path / "bin" / "tortoise").unlink()
    home = Path(os.environ["HOME"])
    src = tmp_path / "checkout"
    (src / ".venv" / "bin").mkdir(parents=True)
    console = src / ".venv" / "bin" / "tortoise"
    console.write_text("#!/bin/bash\ncat > /dev/null\nprintf '%s\\n' "
                       f"{shlex.quote(_BLOCK)}\n")
    console.chmod(0o755)
    (home / ".tortoise").mkdir(parents=True)
    (home / ".tortoise" / "hook-src-dir").write_text(str(src))
    assert module.on_pre_llm_call(user_message="how is CI deployed?") == {
        "context": _BLOCK}


def test_plugin_home_unset_never_resolves_a_cwd_relative_record(
        tmp_path, monkeypatch):
    """An UNSET ``HOME`` must not fall back to a CWD-relative
    ``./.tortoise/hook-src-dir`` — the agent's own (writable, often untrusted)
    workspace. The plugin uses the shell hook's ``/nonexistent`` sentinel
    (``${HOME:-/nonexistent}``), so a planted record in the CWD is never read
    (#2121 review).

    MUTATION: ``os.environ.get("HOME") or ""`` → ``Path("")`` is ``.``, so
    the planted ``./.tortoise/hook-src-dir`` (pointing at a planted
    ``.venv/bin/tortoise``) becomes a candidate and the empty-list assertion
    goes RED.
    """
    module = _install_and_load(tmp_path, monkeypatch, block=_BLOCK)
    (tmp_path / "bin" / "tortoise").unlink()  # nothing on PATH
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("TORTOISE_SRC_DIR", "")
    monkeypatch.setenv("VIRTUAL_ENV", "")
    workspace = tmp_path / "workspace"
    evil_src = workspace / "evil-src"
    (evil_src / ".venv" / "bin").mkdir(parents=True)
    console = evil_src / ".venv" / "bin" / "tortoise"
    console.write_text("#!/bin/bash\ncat > /dev/null\nprintf '%s\\n' "
                       f"{shlex.quote('PLANTED-EXECUTED')}\n")
    console.chmod(0o755)
    (workspace / ".tortoise").mkdir(parents=True)
    (workspace / ".tortoise" / "hook-src-dir").write_text(str(evil_src))
    monkeypatch.chdir(workspace)
    assert module._tortoise_candidates() == []
    assert module.on_pre_llm_call(user_message="how is CI deployed?") is None


def test_plugin_falls_back_to_the_venv_that_owns_a_wheel_module_dir(
        tmp_path, monkeypatch):
    """A WHEEL install records a ``site-packages`` module dir with no
    ``.venv`` beneath it; the plugin walks up to the ``pyvenv.cfg`` owner and
    uses THAT venv's console script (#2385 item 3) — the step the shipped
    shell hook takes (#2121 review).

    MUTATION: drop the owner walk → no candidate resolves and
    ``on_pre_llm_call`` returns ``None`` → RED.
    """
    module = _install_and_load(tmp_path, monkeypatch, block=_BLOCK)
    (tmp_path / "bin" / "tortoise").unlink()  # nothing on PATH
    home = Path(os.environ["HOME"])
    venv = tmp_path / "venv"
    site_packages = venv / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    console = venv / "bin" / "tortoise"
    console.parent.mkdir(parents=True)
    console.write_text("#!/bin/bash\ncat > /dev/null\nprintf '%s\\n' "
                       f"{shlex.quote(_BLOCK)}\n")
    console.chmod(0o755)
    (home / ".tortoise").mkdir(parents=True)
    (home / ".tortoise" / "hook-src-dir").write_text(str(site_packages))
    assert [str(console), "volunteer"] in module._tortoise_candidates()
    assert module.on_pre_llm_call(user_message="how is CI deployed?") == {
        "context": _BLOCK}


def _install_inert_crumb(tmp_path) -> Path:
    """The canonical ``install-inert`` slot for ``hermes``.

    Derived from the SHARED filename rule (``hook_install.breadcrumb_name``)
    rather than a second literal, so the plugin's stdlib-only mirror of the
    slot (it cannot import ``tortoise.*``) cannot drift from the slot a
    reader will read. The plugin's automated reader is still missing (#8360);
    this pins that the record at least lands where that reader must look.
    """
    from tortoise.hook_install import KIND_INSTALL_INERT, breadcrumb_name

    return (tmp_path / "home" / ".tortoise" / "capture-errors"
            / breadcrumb_name("hermes", KIND_INSTALL_INERT))


def test_plugin_leaves_install_inert_evidence_when_no_reflex_resolves(
        tmp_path, monkeypatch):
    """A dead seam must leave EVIDENCE, not silence (#4314). Fail-open injects
    nothing on a healthy empty graph AND on an unresolved reflex, so without a
    breadcrumb the two are indistinguishable; the shipped shell hooks write
    exactly this record for exactly this case. The path mirrors
    `hook_install.local_state_dir`/`breadcrumb_name`
    (`<home>/.tortoise/capture-errors/<harness>-install.json`)."""
    module = _install_and_load(tmp_path, monkeypatch)
    (tmp_path / "bin" / "tortoise").unlink()  # nothing resolvable
    crumb = _install_inert_crumb(tmp_path)
    assert not crumb.exists()
    assert module.on_pre_llm_call(user_message="hi there") is None
    record = json.loads(crumb.read_text(encoding="utf-8"))
    assert record["kind"] == "install-inert"
    assert record["harness"] == "hermes"
    assert "resolved no tortoise reflex" in record["detail"]


def test_plugin_leaves_install_inert_evidence_when_every_reflex_fails(
        tmp_path, monkeypatch):
    """Every resolved reflex exited non-zero (or raised) → the install is
    broken, not the graph empty — evidence, not silence (#4314)."""
    module = _install_and_load(tmp_path, monkeypatch)
    fake = tmp_path / "bin" / "tortoise"
    fake.write_text("#!/bin/bash\nexit 3\n")
    fake.chmod(0o755)
    assert module.on_pre_llm_call(user_message="hi there") is None
    record = json.loads(_install_inert_crumb(tmp_path).read_text(
        encoding="utf-8"))
    assert record["kind"] == "install-inert"
    assert "failed" in record["detail"]


def test_plugin_truthy_mirror_is_the_canonical_vocabulary(tmp_path,
                                                          monkeypatch):
    """The plugin is loaded by the HOST interpreter from a copied-out
    directory and cannot import `tortoise.env_truthy`, so its kill-switch
    vocabulary is a standalone mirror. Pin it to the canonical set (the reaper
    has the same assertion) so a future widening of `TRUTHY` cannot diverge
    silently in the one seam whose kill switch is this literal (#2121
    review)."""
    from tortoise.env_truthy import TRUTHY

    module = _install_and_load(tmp_path, monkeypatch)
    assert set(module._TRUTHY) == set(TRUTHY)


# ── 3. `parse_hermes` — the ingest half over a real state.db ─────────────


def test_parse_hermes_live_transcript_in_id_order(tmp_path):
    """`active = 1` rows in insertion (id) order — NOT timestamp order, and
    rewound rows / generated summaries / non-conversation roles are skipped."""
    db = _make_state_db(tmp_path / "state.db", {"s1": [
        {"id": 1, "role": "user", "content": "Let's wire Hermes.",
         "ts": 900.0},
        {"id": 2, "role": "assistant", "content": "Agreed — the plugin first.",
         "ts": 100.0},
        {"id": 3, "role": "tool", "content": "tool noise", "ts": 200.0},
        {"id": 4, "role": "session_meta", "content": "meta", "ts": 300.0},
        {"id": 5, "role": "user", "content": "rewound turn",
         "active": False},
        {"id": 6, "role": "user", "content": "compaction summary",
         "summary": True},
        {"id": 7, "role": "assistant", "content": "   "},
        {"id": 8, "role": "user", "content": "Ship the parser too."},
    ]})
    assert parse_hermes(db) == [
        {"role": "user", "content": "Let's wire Hermes."},
        {"role": "assistant", "content": "Agreed — the plugin first."},
        {"role": "user", "content": "Ship the parser too."},
    ]


def test_parse_hermes_captures_only_active_non_summary_utterances(tmp_path):
    """In-place compaction archives the summarized turns (``active = 0``) AND
    writes a generated summary row (``_compressed_summary = 1``); both are
    outside the captured set, so a compacted session yields only its tail.
    Pins the docstring's precise claim — not a "live model-visible
    transcript" that would include the summary (#2121 review P2)."""
    db = _make_state_db(tmp_path / "state.db", {"s1": [
        {"id": 1, "role": "user", "content": "archived turn",
         "active": False},
        {"id": 2, "role": "assistant", "content": "archived reply",
         "active": False},
        {"id": 3, "role": "user", "content": "generated summary",
         "summary": True},
        {"id": 4, "role": "user", "content": "post-compaction turn"},
    ]})
    assert parse_hermes(db) == [
        {"role": "user", "content": "post-compaction turn"}]


def test_parse_hermes_defaults_to_the_most_recent_session(tmp_path):
    db = _make_state_db(
        tmp_path / "state.db",
        {
            "old": [{"id": 1, "role": "user", "content": "old session"}],
            "new": [{"id": 2, "role": "user", "content": "new session"}],
        },
        started={"old": 1_000.0, "new": 2_000.0},
    )
    assert parse_hermes(db) == [
        {"role": "user", "content": "new session"}]


def test_parse_hermes_explicit_session_selector(tmp_path):
    db = _make_state_db(
        tmp_path / "state.db",
        {
            "old": [{"id": 1, "role": "user", "content": "old session"}],
            "new": [{"id": 2, "role": "user", "content": "new session"}],
        },
        started={"old": 1_000.0, "new": 2_000.0},
    )
    # `<state.db>::<session-id>` — the escape hatch for a specific session,
    # reached through the SHARED dispatch contract too.
    assert parse_transcript(f"{db}::old", "hermes") == [
        {"role": "user", "content": "old session"}]
    assert PARSERS["hermes"] is parse_hermes


def test_parse_hermes_does_not_split_a_colon_colon_directory_name(tmp_path):
    """`::` is a session selector only when the whole argument does not exist
    on disk — a store under a directory legitimately named ``a::b`` must be
    read literally, not truncated to ``a`` (#2121 review)."""
    store = tmp_path / "a::b"
    store.mkdir()
    db = _make_state_db(store / "state.db", {"s1": [
        {"id": 1, "role": "user", "content": "colon dir"}]})
    assert parse_hermes(db) == [{"role": "user", "content": "colon dir"}]


def test_parse_hermes_reads_the_store_read_only(tmp_path):
    """A backfill must never mutate a Hermes store (never create -wal/-shm
    beside a live state.db).

    The fixture is a LIVE WAL store — `-wal`/`-shm` on disk, with every turn
    readable only THROUGH the WAL — because that is the shape where the
    mechanism is load-bearing. On a rollback-journal file that is only ever
    read, the old assertions held for a plain writable `sqlite3.connect(path)`
    too, so "drop `mode=ro`" survived (the test could not fail). Here a
    writable open is the last connection and CHECKPOINTS ON CLOSE: it deletes
    both sidecars and rewrites the main db, so the mutation reds (measured at
    this head).
    """
    db = _make_live_wal_state_db(
        tmp_path / "state.db",
        [(1, "user", "hello Hermes"), (2, "assistant", "wal-only turn")])
    wal = tmp_path / "state.db-wal"
    shm = tmp_path / "state.db-shm"
    assert wal.exists() and shm.exists(), "fixture is not the live WAL shape"
    main_before, wal_before = db.read_bytes(), wal.read_bytes()

    # The second turn is reachable ONLY through the WAL — so a green parse
    # proves the read actually used it, not just the checkpointed main file.
    assert parse_hermes(db) == [
        {"role": "user", "content": "hello Hermes"},
        {"role": "assistant", "content": "wal-only turn"},
    ]

    # The sidecars SURVIVE (a writable close deletes both) and the WAL is
    # byte-identical. `-shm` is asserted for PRESENCE only: SQLite stores
    # read-marks and lock slots in it, so even a read-only open rewrites bytes
    # there (measured) — byte equality would be a FALSE invariant, while
    # presence is exactly what the writable close destroys.
    assert wal.exists() and wal.read_bytes() == wal_before
    assert shm.exists()
    assert db.read_bytes() == main_before


def test_parse_hermes_snapshots_so_the_source_dir_is_never_written(tmp_path):
    """A WAL store whose ``-shm`` was reaped — what a clean close leaves — is
    exactly the shape where a DIRECT read-only open CREATES ``-wal``/``-shm``
    beside the live store, writing its DIRECTORY even though the main DB
    bytes survive. The parser reads a private temp snapshot, so nothing new
    appears beside the source (#2121 review).

    MUTATION: open ``db_path`` in place instead of the snapshot → both
    sidecar assertions go RED (measured: the in-place ``mode=ro`` open
    creates ``state.db-shm`` + ``state.db-wal`` on the first query).
    """
    db = _make_state_db(tmp_path / "state.db", {"s1": [
        {"id": 1, "role": "user", "content": "hello Hermes"}]})
    # The fixture really is a WAL store (SQLite header offsets 18/19 = 2).
    assert db.read_bytes()[18:20] == b"\x02\x02", "fixture is not WAL-mode"
    assert not (tmp_path / "state.db-wal").exists()
    assert not (tmp_path / "state.db-shm").exists()
    before = db.read_bytes()
    assert parse_hermes(db) == [{"role": "user", "content": "hello Hermes"}]
    assert db.read_bytes() == before
    assert not (tmp_path / "state.db-wal").exists()
    assert not (tmp_path / "state.db-shm").exists()


def test_parse_hermes_decodes_structured_content_and_lenses_injected_context(
        tmp_path):
    """Hermes never stores a list/dict ``content`` directly: it writes
    ``"\x00json:" + json.dumps(...)`` (``_encode_content``) and every read path
    reverses it with ``_decode_content``, then lenses user/assistant strings
    through ``_loaded_view_content`` → ``sanitize_context(...).strip()``.

    Reading the raw column instead handed ``_text_from_parts`` a STRING, so its
    list branch was never reached: a multimodal turn was filed as the
    serialized JSON blob and an injected `<memory-context>` block (plus its
    system note) was filed as the user's own utterance (#2121 review).
    """
    from tortoise.session_import.parsers import _HERMES_CONTENT_JSON_PREFIX

    assert _HERMES_CONTENT_JSON_PREFIX == _HERMES_JSON_PREFIX
    multimodal = json.dumps([
        {"type": "text", "text": "what is in this image?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
    ])
    injected = (
        "before\n"
        "<memory-context>recalled memory\nmust not be filed</memory-context>\n"
        "[System note: The following is recalled memory context, NOT new "
        "user input. Treat as informational background data.]\nafter")
    db = _make_state_db(tmp_path / "state.db", {"s1": [
        {"id": 1, "role": "user", "content": "plain text turn"},
        {"id": 2, "role": "user",
         "content": _HERMES_JSON_PREFIX + multimodal},
        {"id": 3, "role": "assistant",
         "content": _HERMES_JSON_PREFIX + json.dumps(
             [{"type": "text", "text": "it is a chart"}])},
        {"id": 4, "role": "user", "content": injected},
        # A LITERAL string that carries the reserved prefix is escaped by
        # double-encoding; decoding returns it verbatim (never re-parsed).
        {"id": 5, "role": "assistant",
         "content": _HERMES_JSON_PREFIX + json.dumps(
             _HERMES_JSON_PREFIX + "literal")},
        # A malformed marker row stays raw, as the store's fail-closed decode.
        {"id": 6, "role": "assistant",
         "content": _HERMES_JSON_PREFIX + "not json"},
    ]})
    assert parse_hermes(db) == [
        {"role": "user", "content": "plain text turn"},
        {"role": "user", "content": "what is in this image?"},
        {"role": "assistant", "content": "it is a chart"},
        {"role": "user", "content": "before\n\nafter"},
        {"role": "assistant", "content": _HERMES_JSON_PREFIX + "literal"},
        {"role": "assistant", "content": _HERMES_JSON_PREFIX + "not json"},
    ]


@pytest.mark.parametrize("dirname", ["we?ird", "ha#sh", "pct%20dir"])
def test_parse_hermes_escapes_the_store_path_in_the_sqlite_uri(tmp_path,
                                                               dirname):
    """A `?` / `#` / `%` in the store path must not become SQLite URI
    structure: the raw interpolation swallowed ``mode=ro``, read a different
    (truncated) path, and CREATED a stray empty file beside it (#2121
    review)."""
    store = tmp_path / dirname
    store.mkdir()
    db = _make_state_db(store / "state.db", {"s1": [
        {"id": 1, "role": "user", "content": "escaped path"}]})
    assert parse_hermes(db) == [{"role": "user", "content": "escaped path"}]
    # No stray sibling produced by URI parsing (e.g. `<tmp>/we` from `we?ird`).
    assert sorted(p.name for p in tmp_path.iterdir()) == [dirname]


@pytest.mark.parametrize("build,needle", [
    (lambda p: p / "missing.db", "session store not found"),
    (lambda p: _empty_store(p / "state.db"), "no sessions in Hermes"),
    (lambda p: _plain_file(p / "state.db"), "cannot read Hermes session"),
    (lambda p: _sqlite_without_tables(p / "state.db"), "cannot read Hermes session"),
])
def test_parse_hermes_errors_are_honest_value_errors(tmp_path, build, needle):
    with pytest.raises(ValueError) as exc:
        parse_hermes(build(tmp_path))
    assert needle in str(exc.value)


def test_parse_hermes_unknown_session_is_a_clean_error(tmp_path):
    db = _make_state_db(tmp_path / "state.db", {"s1": [
        {"id": 1, "role": "user", "content": "hi"}]})
    with pytest.raises(ValueError) as exc:
        parse_hermes(f"{db}::nope")
    assert "no Hermes session 'nope'" in str(exc.value)


def test_parse_hermes_accepts_the_hermes_home_directory(tmp_path):
    home = tmp_path / "hermes-home"
    home.mkdir()
    _make_state_db(home / "state.db", {"s1": [
        {"id": 1, "role": "user", "content": "from HERMES_HOME"}]})
    assert parse_hermes(home) == [
        {"role": "user", "content": "from HERMES_HOME"}]


def _empty_store(path: Path) -> Path:
    _make_state_db(path, {})
    return path


def _plain_file(path: Path) -> Path:
    path.write_text("this is not a sqlite database\n")
    return path


def _sqlite_without_tables(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    return path


# ── 4. CLI wiring — `sessions import --harness hermes` ──────────────────


def test_cli_accepts_the_hermes_harness(tmp_path):
    """The `--harness` choice is wired: a missing file is reported as a
    missing SESSION FILE (not an argparse rejection), which means `hermes`
    parsed and reached the import path."""
    r = _run(["sessions", "import", "--harness", "hermes",
              "--file", str(tmp_path / "missing.db")],
             _env(TORTOISE_CAPTURE="1", HOME=str(tmp_path / "home")))
    assert r.returncode == 1
    assert "Session file not found" in r.stderr
    assert "invalid choice" not in r.stderr


def test_cli_resolves_the_hermes_session_selector(tmp_path):
    """The documented `<state.db>::<session-id>` selector must clear the CLI
    existence guard and reach the PARSER — the composite string is not a
    filesystem path, so judging it as one made the selector unreachable
    (#2121 review)."""
    db = _make_state_db(
        tmp_path / "state.db",
        {
            "old": [{"id": 1, "role": "user", "content": "old session"}],
            "new": [{"id": 2, "role": "user", "content": "new session"}],
        },
        started={"old": 1_000.0, "new": 2_000.0},
    )
    env = _env(TORTOISE_CAPTURE="1", HOME=str(tmp_path / "home"))
    # The unknown-session error is emitted ONLY by parse_hermes, so seeing it
    # proves the selector cleared the existence check and reached the parser.
    r = _run(["sessions", "import", "--harness", "hermes",
              "--file", f"{db}::nope"], env)
    assert r.returncode == 1
    assert "Session file not found" not in r.stderr
    assert "no Hermes session 'nope'" in r.stderr
    # A REAL selector clears the guard too and proceeds down the shared
    # import path (here stopping at the user-global config, as every other
    # harness does in this isolated HOME).
    r = _run(["sessions", "import", "--harness", "hermes",
              "--file", f"{db}::old"], env)
    assert "Session file not found" not in r.stderr
    assert "No .tortoise config found" in r.stderr


def test_install_list_mentions_the_hermes_seam(tmp_path):
    r = _run(["install", "--list"], _env())
    assert r.returncode == 0, r.stderr
    assert "install hermes" in r.stdout
    assert "pre_llm_call" in r.stdout
    # The listing must not advertise a seam as ready when Hermes will not load
    # it without two opt-ins (#2121 review P1).
    assert "HERMES_ENABLE_PROJECT_PLUGINS" in r.stdout
    assert "hermes plugins enable tortoise-volunteer" in r.stdout


def test_hermes_import_reaches_the_hosted_boundary_and_writes_a_receipt(
        tmp_path, monkeypatch):
    """The ingest half's whole point: `sessions import --harness hermes` must
    be able to LAND a session. The command POSTs ``harness`` verbatim to
    `/v1/sessions`, and that route validates the body through
    ``SessionRequest``, whose harness vocabulary is CLOSED — a value outside it
    is refused 422 BEFORE the handler. Because 422 is not in
    ``capture_spool.classify_failure``'s transient family, such a refusal is
    neither spooled nor receipted: the session is LOST, not deferred, while the
    CLI looks wired.

    The stand-in transport applies that SAME model to the posted body before
    accepting it, so a harness the real server would reject is rejected here.

    MUTATION THAT REDS THIS: drop "hermes" from ``_SESSION_HARNESS_VALUES`` —
    the boundary refuses 422, rc is 1, and no receipt is written (#2121 review
    P1).
    """
    from types import SimpleNamespace

    from pydantic import ValidationError

    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.hosted_api import (
        _SESSION_HARNESS_VALUES,
        SessionRequest,
        _capture_server_owned_keys,
    )

    # The DERIVED server-owned receipt family must exist for the harness, or
    # even a successful POST has no key to record its receipt under.
    assert "hermes" in _SESSION_HARNESS_VALUES
    assert {"session_capture_receipt_hermes",
            "session_capture_last_error_hermes"} <= _capture_server_owned_keys()

    db = _make_state_db(tmp_path / "state.db", {"s1": [
        {"id": 1, "role": "user", "content": "hello Hermes"},
        {"id": 2, "role": "assistant", "content": "hi there"}]})

    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test")
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR",
                       str(tmp_path / "receipts"))
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    posted: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"session_id": "s1"}).encode("utf-8")

    def _open(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        try:
            # Exactly what the route does with a JSON body before the handler.
            SessionRequest(**body)
        except ValidationError as exc:
            raise HTTPError("https://api.tortoise.test/v1/sessions", 422,
                            "unprocessable", hdrs=None,
                            fp=io.BytesIO(b'{"detail":"invalid harness"}')
                            ) from exc
        posted.update(body)
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _open)
    rc = _cmd_sessions_import(SimpleNamespace(
        file=str(db), harness="hermes", session_id="imp-hermes"))

    assert rc == 0, posted
    assert posted["harness"] == "hermes"
    assert posted["conversation"] == [
        {"role": "user", "content": "hello Hermes"},
        {"role": "assistant", "content": "hi there"},
    ]
    receipts = list((tmp_path / "receipts").glob("*.json"))
    assert [p.name for p in receipts] == ["imp-hermes.json"], receipts
    assert json.loads(receipts[0].read_text())["harness"] == "hermes"


def test_hermes_import_key_is_per_session_not_per_store_file(
        tmp_path, monkeypatch):
    """The derived ``session_id`` must be a function of the SELECTED SESSION,
    not of the mutating store FILE: ``<db>`` (newest) and ``<db>::<that id>``
    are the SAME session and must share one key, while a different session
    must not.

    Pre-fix the key was the whole-store hash salted with the raw selector, so
    the two spellings derived TWO ids and the same session was POSTed twice
    (and an unrelated session's appended turn re-keyed it) — #2121 review P2.
    """
    from types import SimpleNamespace

    from tortoise.__main__ import _cmd_sessions_import

    db = _make_state_db(
        tmp_path / "state.db",
        {"old": [{"id": 1, "role": "user", "content": "old session"}],
         "new": [{"id": 2, "role": "user", "content": "new session"}]},
        started={"old": 1_000.0, "new": 2_000.0})

    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test")
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR",
                       str(tmp_path / "receipts"))
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    posted: list[str] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"session_id": "s1"}'

    def _open(req, timeout=None):
        posted.append(json.loads(req.data.decode("utf-8"))["session_id"])
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _open)

    def _import(arg: str) -> int:
        return _cmd_sessions_import(SimpleNamespace(
            file=arg, harness="hermes", session_id=None))

    assert _import(str(db)) == 0                 # newest session
    assert _import(f"{db}::new") == 0            # SAME session, second spelling
    assert len(posted) == 1, posted              # receipt short-circuited it
    assert len(list((tmp_path / "receipts").glob("*.json"))) == 1
    assert _import(f"{db}::old") == 0            # a DIFFERENT session
    assert len(posted) == 2, posted
    assert posted[0] != posted[1]
