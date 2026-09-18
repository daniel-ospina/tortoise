"""#3808 — the installer must install CAPTURE, per harness, idempotently.

The capture seam used to be a copy-paste block in the dashboard:
``grep -rn "HARNESS_INSTALL" tortoise/`` returned nothing, so ``tortoise
install claude`` gave a user the READ hook and no capture at all — and the
capture hook is fail-open, so a missing or mistyped install filed no sessions
and reported no error.

Every test here drives the REAL installer (``tortoise.capture_install``) or the
REAL CLI and asserts the **resolved outcome on disk** — file bytes, mode, the
merged ``settings.json``, exit code, mtime — never source text.  Each
docstring names the mutation that turns it RED; all mutations were verified RED
during the review cycle.  A test that cannot RED is a false PASS and does not
belong here.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tortoise import capture_install, hook_install
from tortoise.capture_install import (
    CAPTURE_SEAM,
    CLAUDE_TIMEOUT,
    install_capture,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HOOKS_DIR = _REPO_ROOT / "tortoise" / "claude-hooks"
_PI_SRC = _REPO_ROOT / "tortoise" / "pi-hooks" / capture_install.PI_EXTENSION_NAME
_DASHBOARD = _REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "harnesses.js"
_PY = sys.executable

# The shipped hook scripts, byte-for-byte — the install copies these.
_START = (_HOOKS_DIR / "session-start.sh").read_bytes()
_END = (_HOOKS_DIR / "session-end.sh").read_bytes()
_PI = _PI_SRC.read_bytes()


def _run(argv, env, cwd, timeout=180):
    return subprocess.run(
        [_PY, "-m", "tortoise", *argv],
        env=env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture
def home(tmp_path) -> Path:
    """An isolated HOME — nothing in the installer may touch the real one."""
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def cli(tmp_path, home):
    """A CLI runner whose HOME is a temp dir and whose project root is temp."""
    root = tmp_path / "proj"
    root.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "TORTOISE_DB_URI": "",
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
    }
    return lambda *argv: _run(argv, env, root), root, home


def _settings(root: Path) -> dict:
    return json.loads((root / ".claude" / "settings.json").read_text())


def _mtimes(root: Path) -> dict[str, int]:
    hooks = root / ".claude" / "hooks"
    out = {p.name: p.stat().st_mtime_ns for p in hooks.iterdir()}
    out["settings.json"] = (root / ".claude" / "settings.json").stat().st_mtime_ns
    return out


# ── claude: the artifact ────────────────────────────────────────────────


def test_claude_install_writes_both_hooks_executable_and_merges_the_timeout(tmp_path):
    """Mutation: skip the chmod (mode lands 0644) — or drop a script from
    CLAUDE_SCRIPTS (the file never appears)."""
    res = install_capture("claude", root=tmp_path)

    assert res.ok, res.error
    assert res.changed
    for name, payload in (("session-start.sh", _START), ("session-end.sh", _END)):
        dst = tmp_path / ".claude" / "hooks" / name
        assert dst.is_file(), f"{name} was not installed"
        assert dst.read_bytes() == payload, f"{name} is not the shipped artifact"
        assert dst.stat().st_mode & stat.S_IXUSR, f"{name} is not executable"

    cfg = _settings(tmp_path)
    for event, name in (("SessionStart", "session-start.sh"),
                        ("SessionEnd", "session-end.sh")):
        entries = cfg["hooks"][event]
        assert len(entries) == 1, f"{event} has {len(entries)} entries, expected 1"
        assert entries[0]["matcher"] == ""
        inner = entries[0]["hooks"][0]
        assert inner["type"] == "command"
        assert inner["command"] == f".claude/hooks/{name}"
        # The #3754/#3801 load-bearing timeout — without it Claude Code
        # cancels the SessionEnd hook at its 1.5s default and files nothing.
        assert inner["timeout"] == CLAUDE_TIMEOUT


def test_claude_install_preserves_unrelated_settings_and_foreign_hooks(tmp_path):
    """Merge, never overwrite. Mutation: `data = {}` in `_load_settings`
    (every unrelated key and the user's own PreToolUse hook vanish)."""
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    existing = {
        "enableAllProjectHooks": True,
        "model": "opus",
        "hooks": {
            "PreToolUse": [{"matcher": "Edit", "hooks": [
                {"type": "command", "command": "lint.sh"}]}],
            "SessionEnd": [{"matcher": "", "hooks": [
                {"type": "command", "command": "my-own-teardown.sh"}]}],
        },
    }
    target.write_text(json.dumps(existing))

    res = install_capture("claude", root=tmp_path)

    assert res.ok, res.error
    cfg = _settings(tmp_path)
    assert cfg["enableAllProjectHooks"] is True
    assert cfg["model"] == "opus"
    assert cfg["hooks"]["PreToolUse"] == existing["hooks"]["PreToolUse"]
    # the foreign SessionEnd hook survives, and ours is ADDED alongside it
    commands = [h["command"] for e in cfg["hooks"]["SessionEnd"]
                for h in e["hooks"]]
    assert "my-own-teardown.sh" in commands
    assert ".claude/hooks/session-end.sh" in commands


def test_claude_install_is_a_clean_no_op_on_rerun(tmp_path):
    """This path is also the upgrade path: a second run must write nothing.

    Mutation: drop the `_is_regular_unchanged` guard (both hooks are rewritten
    and both mtimes move) — or append unconditionally in
    `merge_capture_hooks` (the entry count doubles)."""
    install_capture("claude", root=tmp_path)
    before = _mtimes(tmp_path)
    count_before = {e: len(v) for e, v in _settings(tmp_path)["hooks"].items()
                    if e in ("SessionStart", "SessionEnd")}

    # mtime granularity: prove the guard does not merely rewrite identical bytes
    second = install_capture("claude", root=tmp_path)

    assert second.ok, second.error
    assert second.changed is False, f"second run reported changes: {second.actions}"
    assert second.actions == ()
    assert _mtimes(tmp_path) == before, "a re-run rewrote files it should have skipped"
    after = {e: len(v) for e, v in _settings(tmp_path)["hooks"].items()
             if e in ("SessionStart", "SessionEnd")}
    assert after == count_before == {"SessionStart": 1, "SessionEnd": 1}


def test_claude_install_repairs_a_timeoutless_registration_in_place(tmp_path):
    """An already-registered hook with NO timeout is repaired, not duplicated.

    Mutation: drop the `existing["timeout"] = timeout` branch (the install
    leaves the un-timed entry — the #3801 silent-loss shape)."""
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"hooks": {"SessionStart": [{
        "matcher": "", "hooks": [
            {"type": "command", "command": ".claude/hooks/session-start.sh"}],
    }]}}))

    res = install_capture("claude", root=tmp_path)

    assert res.ok, res.error
    entries = _settings(tmp_path)["hooks"]["SessionStart"]
    assert len(entries) == 1, "the existing registration was duplicated"
    assert entries[0]["hooks"][0]["timeout"] == CLAUDE_TIMEOUT


def test_claude_install_never_lowers_a_higher_user_timeout(tmp_path):
    """Mutation: `if current != timeout: set` (a deliberate 120s budget is
    silently downgraded to 60)."""
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"hooks": {"SessionEnd": [{
        "matcher": "", "hooks": [
            {"type": "command", "command": ".claude/hooks/session-end.sh",
             "timeout": 120}],
    }]}}))

    install_capture("claude", root=tmp_path)

    inner = _settings(tmp_path)["hooks"]["SessionEnd"][0]["hooks"][0]
    assert inner["timeout"] == 120


# ── claude: "is this entry ours?" — the #3866 classification contract ──
#
# `tortoise hooks status` (#3866) and this installer must answer the question
# identically: a divergence leaves an install that one calls current and the
# other calls drifted — or, worse, one that neither can see is not capturing.
# Each test below pins one shape #3866 classifies as NOT ours.


def test_claude_install_repairs_ours_wherever_it_sits_in_the_entry_array(tmp_path):
    """Ours may sit BEHIND a foreign hook in the same event array.

    Mutation: inspect only `entry["hooks"][0]` (ours is missed, a second
    registration is appended, and Claude Code runs the hook twice)."""
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"hooks": {"SessionEnd": [{
        "matcher": "", "hooks": [
            {"type": "command", "command": "my-own-teardown.sh"},
            {"type": "command", "command": ".claude/hooks/session-end.sh"},
        ],
    }]}}))

    install_capture("claude", root=tmp_path)

    entries = _settings(tmp_path)["hooks"]["SessionEnd"]
    assert len(entries) == 1, "ours was not recognised behind the foreign hook"
    commands = [h["command"] for h in entries[0]["hooks"]]
    assert commands == ["my-own-teardown.sh", ".claude/hooks/session-end.sh"]
    assert entries[0]["hooks"][1]["timeout"] == CLAUDE_TIMEOUT


def test_claude_install_rejects_a_flat_event_level_entry(tmp_path):
    """A flat ``{type, command}`` at the EVENT level is NOT a registration —
    Claude Code silently ignores it.

    Mutation: accept the flat shape as ours (the install reports success, the
    harness still captures nothing, and `tortoise hooks status` calls the same
    file `missing-hook-entry`)."""
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"hooks": {"SessionEnd": [{
        "type": "command", "command": ".claude/hooks/session-end.sh",
    }]}}))

    install_capture("claude", root=tmp_path)

    entries = _settings(tmp_path)["hooks"]["SessionEnd"]
    flat = [e for e in entries if "hooks" not in e]
    assert len(flat) == 1, "the user's flat entry was removed"
    proper = [e for e in entries if "hooks" in e]
    assert len(proper) == 1, "no valid registration was appended"
    assert proper[0]["hooks"][0]["timeout"] == CLAUDE_TIMEOUT


def test_claude_install_ignores_a_handler_without_type(tmp_path):
    """A handler with a command but no ``type`` is ignored by the harness.

    Mutation: drop the `type != "command"` gate (the entry is "repaired" with
    a timeout but never runs — the silent-loss shape)."""
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"hooks": {"SessionStart": [{
        "matcher": "", "hooks": [
            {"command": ".claude/hooks/session-start.sh"}],
    }]}}))

    install_capture("claude", root=tmp_path)

    entries = _settings(tmp_path)["hooks"]["SessionStart"]
    typed = [h for e in entries for h in e.get("hooks", [])
             if h.get("type") == "command"
             and h.get("command") == ".claude/hooks/session-start.sh"]
    assert len(typed) == 1, "no valid typed registration was added"
    assert typed[0]["timeout"] == CLAUDE_TIMEOUT
    # the user's untyped handler is left exactly as it was found — repairing it
    # in place would setdefault a type on an entry we never registered
    untyped = [h for e in entries for h in e.get("hooks", [])
               if "type" not in h]
    assert untyped == [{"command": ".claude/hooks/session-start.sh"}], (
        "the untyped foreign handler was mutated instead of left alone")


def test_claude_install_ignores_a_foreign_hook_at_the_same_basename(tmp_path):
    """A DIFFERENT project's `session-end.sh` merely shares our basename.

    Mutation: basename-only matching (our timeout is stamped on the foreign
    hook and ours is never registered — capture is lost while the install
    reports success)."""
    vendor = tmp_path / "vendor" / ".claude" / "hooks"
    vendor.mkdir(parents=True)
    (vendor / "session-end.sh").write_text("#!/bin/sh\nexit 0\n")
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"hooks": {"SessionEnd": [{
        "matcher": "", "hooks": [
            {"type": "command",
             "command": "vendor/.claude/hooks/session-end.sh"}],
    }]}}))

    install_capture("claude", root=tmp_path)

    entries = _settings(tmp_path)["hooks"]["SessionEnd"]
    foreign = [h for e in entries for h in e.get("hooks", [])
               if h["command"] == "vendor/.claude/hooks/session-end.sh"]
    assert len(foreign) == 1
    assert "timeout" not in foreign[0], "our timeout was stamped on a foreign hook"
    ours = [h for e in entries for h in e.get("hooks", [])
            if h["command"] == ".claude/hooks/session-end.sh"]
    assert len(ours) == 1, "our own registration was never added"
    assert ours[0]["timeout"] == CLAUDE_TIMEOUT


def test_claude_install_ignores_a_mention_of_our_hook_in_an_argument(tmp_path):
    """``cat .claude/hooks/session-end.sh`` names our hook; it does not RUN it.

    Mutation: match any token instead of only executable-position ones (ours
    is never registered and capture is lost)."""
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"hooks": {"SessionEnd": [{
        "matcher": "", "hooks": [
            {"type": "command", "command": "cat .claude/hooks/session-end.sh"}],
    }]}}))

    install_capture("claude", root=tmp_path)

    entries = _settings(tmp_path)["hooks"]["SessionEnd"]
    ours = [h for e in entries for h in e.get("hooks", [])
            if h["command"] == ".claude/hooks/session-end.sh"]
    assert len(ours) == 1, "our own registration was never added"
    assert ours[0]["timeout"] == CLAUDE_TIMEOUT


def test_claude_install_accepts_the_project_dir_variable_form(tmp_path):
    """``$CLAUDE_PROJECT_DIR/.claude/hooks/session-end.sh`` is a registration
    of OURS (the form Claude Code documents) — it must be repaired in place,
    never duplicated.

    Mutation: always append when the token cannot be resolved (an already
    registered hook is added a second time and runs twice)."""
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"hooks": {"SessionEnd": [{
        "matcher": "", "hooks": [{
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/session-end.sh",
        }],
    }]}}))

    install_capture("claude", root=tmp_path)

    entries = _settings(tmp_path)["hooks"]["SessionEnd"]
    assert len(entries) == 1, "the existing registration was duplicated"
    inner = entries[0]["hooks"][0]
    assert inner["command"] == "$CLAUDE_PROJECT_DIR/.claude/hooks/session-end.sh"
    assert inner["timeout"] == CLAUDE_TIMEOUT


def test_claude_install_repairs_a_missing_exec_bit(tmp_path):
    """Bytes identical but no exec bit is NOT "already installed".

    Claude Code executes `.claude/hooks/<name>` directly, and the fail-open
    script swallows the permission error — so a hook that lost its exec bit
    (a `cp` without `chmod`, a zip/tarball checkout) files nothing while the
    install reports success.

    Mutation: treat identical bytes as unchanged without checking the mode
    (the `continue` skips the `os.chmod` repair; `changed` stays False and the
    hook is left unrunnable)."""
    install_capture("claude", root=tmp_path)
    hooks = [tmp_path / ".claude" / "hooks" / name
             for name in ("session-start.sh", "session-end.sh")]
    for hook in hooks:
        os.chmod(hook, 0o644)

    res = install_capture("claude", root=tmp_path)

    assert res.ok, res.error
    assert res.changed is True, "the lost exec bit was not repaired"
    for hook in hooks:
        assert hook.stat().st_mode & stat.S_IXUSR, f"{hook.name} is still unrunnable"
        assert hook.read_bytes() in (_START, _END)


def test_claude_install_never_lowers_a_higher_float_timeout(tmp_path):
    """A float budget above 60 is a real budget and must not be lowered.

    Mutation: `not isinstance(current, int)` (a `120.0` timeout is rewritten
    to `60` — the same silent downgrade the int case guards against)."""
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"hooks": {"SessionEnd": [{
        "matcher": "", "hooks": [
            {"type": "command", "command": ".claude/hooks/session-end.sh",
             "timeout": 120.0}],
    }]}}))

    install_capture("claude", root=tmp_path)

    inner = _settings(tmp_path)["hooks"]["SessionEnd"][0]["hooks"][0]
    assert inner["timeout"] == 120.0


def test_claude_install_refuses_a_non_regular_file_at_a_hook_path(tmp_path):
    """A directory at a hook path is refused, not written through (and not a
    bare traceback).

    Mutation: drop the `_refuse_non_regular` preflight (`os.replace` raises
    `IsADirectoryError` out of `install_capture` instead of returning the
    documented populated `error`)."""
    blocker = tmp_path / ".claude" / "hooks" / "session-start.sh"
    blocker.mkdir(parents=True)

    res = install_capture("claude", root=tmp_path)

    assert not res.ok
    assert "not a regular file" in res.error
    assert blocker.is_dir()


# ── claude: failure modes (fail LOUDLY) ─────────────────────────────────


def test_claude_install_refuses_to_clobber_invalid_settings(tmp_path):
    """Mutation: `except ValueError: data = {}` — the user's file is silently
    replaced with `{"hooks": {...}}` and their settings are gone."""
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text("{ this is not json")

    res = install_capture("claude", root=tmp_path)

    assert not res.ok
    assert "not valid JSON" in res.error
    assert target.read_text() == "{ this is not json", "the file was modified"


def test_claude_install_refuses_a_settings_symlink_that_escapes_the_root(tmp_path):
    """A symlinked settings.json pointing outside the project is refused.

    Mutation: remove the `_symlink_escape` call — the symlink is silently
    REPLACED by a project-local regular file, detaching the user's shared
    (e.g. global) settings file.  The guard is NOT what protects the outside
    file from being written THROUGH: `_atomic_bytes` writes a same-dir temp and
    `os.replace`s it onto the path, which swaps the directory entry and never
    follows the link (contrast the read-hook install, whose
    `target.write_text` DOES follow a symlink).  What the guard prevents is the
    silent severance — the link becomes a stale local copy while the real
    shared config keeps diverging."""
    outside = tmp_path / "outside" / "settings.json"
    outside.parent.mkdir()
    outside.write_text('{"hooks": {}}')
    root = tmp_path / "proj"
    settings = root / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.symlink_to(outside)

    res = install_capture("claude", root=root)

    assert not res.ok
    assert "Refusing" in res.error
    assert "session-end.sh" in res.error or "settings.json" in res.error
    # The symlink survives the refusal...
    assert settings.is_symlink(), "the symlink was replaced by a regular file"
    assert settings.resolve() == outside
    # ...and the outside file is untouched (it was never at risk from the write
    # itself — the guard prevents the severance, not a write-through).
    assert outside.read_text() == '{"hooks": {}}'


def test_claude_install_fails_loudly_when_the_hooks_path_is_not_a_directory(tmp_path):
    """An install that cannot write the hook must NOT report success.

    Mutation: swallow the OSError / skip `_preflight_writable` — the call
    returns ok=True with no hooks installed (the silent-loss shape)."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "hooks").write_text("")  # a FILE where the dir goes

    res = install_capture("claude", root=tmp_path)

    assert not res.ok, "an impossible install reported success"
    assert "not a directory" in res.error
    assert (tmp_path / ".claude" / "hooks").is_file()


def test_claude_install_fails_loudly_when_a_shipped_hook_is_missing(tmp_path, monkeypatch):
    """A package that lost its hook must fail, never install half a seam.

    Mutation: drop the `if not src.is_file()` guard (the copy raises
    FileNotFoundError, or — worse — the settings half is written anyway)."""
    empty = tmp_path / "empty-package"
    empty.mkdir()
    monkeypatch.setattr(capture_install, "PACKAGE_DIR", empty)

    res = install_capture("claude", root=tmp_path / "proj")

    assert not res.ok
    assert "not found" in res.error
    assert not (tmp_path / "proj" / ".claude").exists(), "half an install was left behind"


def test_claude_install_turns_a_write_time_oserror_into_a_populated_error(
        tmp_path, monkeypatch):
    """The DANGEROUS ordering: the hook scripts land, then the
    ``settings.json`` write fails at ``os.replace`` (an immutable target —
    ``chflags uchg``, ``EROFS``/``ENOSPC``/``EDQUOT``).  The script half is on
    disk by then, so a swallowed error would leave the project HALF-INSTALLED
    with no registration and no signal; the call must instead come back as a
    populated error naming the half-install.

    Mutation: wrap the settings write in a bare ``except OSError: pass`` (or
    return the success ``InstallResult``) — the call returns ``ok=True`` and
    the half-install is silent, so ``not res.ok`` / ``NOT fully installed``
    RED while the script half is already on disk."""
    settings_path = tmp_path / ".claude" / "settings.json"
    real_replace = capture_install.os.replace

    def boom(src, dst, *args, **kwargs):
        # Fail ONLY the settings write — the scripts must land first, so the
        # test exercises the half-install ordering rather than a first-write
        # failure that never reaches the dangerous state.
        if Path(dst) == settings_path:
            raise PermissionError(1, "Operation not permitted", str(dst))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(capture_install.os, "replace", boom)

    res = install_capture("claude", root=tmp_path)

    assert not res.ok, "a failed write reported success"
    assert "write failed" in res.error, res.error
    assert "PermissionError" in res.error, res.error
    assert "NOT fully installed" in res.error, res.error
    # The dangerous half is REAL: both scripts were written and chmodded
    # before the settings write failed — the error is the ONLY thing telling
    # the user the seam is incomplete.
    for name in ("session-start.sh", "session-end.sh"):
        script = tmp_path / ".claude" / "hooks" / name
        assert script.is_file(), f"{name} was not written before the failure"
        assert script.stat().st_mode & 0o111, f"{name} is not executable"
    # The registration write failed, so no settings.json was left behind.
    assert not settings_path.exists()


def test_claude_write_failure_is_loud_at_the_cli_seam(tmp_path, monkeypatch, capsys):
    """The CLI layer reports the write failure as ``Capture install FAILED``
    with a non-zero exit — never a traceback.

    Mutation: let the ``OSError`` escape ``install_capture`` (the seam's
    ``result.ok`` branch is bypassed and ``main`` has no handler, so this
    returns an exception instead of exit 1)."""
    from tortoise import __main__ as tortoise_main

    def boom(src, dst, *args, **kwargs):
        raise PermissionError(1, "Operation not permitted", str(dst))

    monkeypatch.setattr(capture_install.os, "replace", boom)
    args = type("Args", (), {"harness": "claude", "dir": str(tmp_path),
                             "dry_run": False})()

    rc = tortoise_main._install_capture_seam(args, install_capture)

    captured = capsys.readouterr()
    assert rc == 1, f"a failed install exited {rc}"
    assert "Capture install FAILED" in captured.err, captured.err
    assert "write failed" in captured.err, captured.err
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_claude_install_refuses_an_in_root_symlinked_hooks_directory(tmp_path):
    """A hooks DIRECTORY symlinked to an in-root directory is refused — the
    stated carve-out is for the leaf only.

    Mutation: allow any in-root symlink (``_symlink_escape`` returns "" for an
    intermediate component) — the install writes through the symlink and exits
    0, while ``hook_install.detect_install`` reports ``symlinked-install`` and
    ``upgrade_install`` refuses the very tree the installer just claimed to
    have installed.  This pins the two surfaces together."""
    root = tmp_path / "proj"
    real = root / "real-hooks"
    real.mkdir(parents=True)
    (root / ".claude").mkdir()
    (root / ".claude" / "hooks").symlink_to(real, target_is_directory=True)

    res = install_capture("claude", root=root)

    assert not res.ok, "a symlinked hooks dir was reported as installed"
    assert "Refusing" in res.error
    assert "symlink" in res.error
    # Nothing was written THROUGH the symlink — no half-install in the target.
    assert list(real.iterdir()) == [], "hooks were written through the symlink"
    assert not (root / ".claude" / "settings.json").exists()
    # ...and the drift detector agrees this is not an install (upgrade refuses
    # symlinked installs), so refusing cannot mask a state status calls clean.
    findings = hook_install.detect_install(root, "claude")
    assert any(f.kind == "symlinked-install" for f in findings), findings
    assert hook_install.upgrade_install(root, "claude").refused is not None


def test_unknown_harness_is_refused(tmp_path):
    """Mutation: return an empty successful result for an unknown harness."""
    res = install_capture("cursor", root=tmp_path)

    assert not res.ok
    assert "no capture seam" in res.error


# ── pi: the artifact ────────────────────────────────────────────────────


def test_pi_install_copies_the_extension_into_the_temp_home(home):
    """Mutation: install to the wrong filename / skip the copy."""
    res = install_capture("pi", home=home)

    assert res.ok, res.error
    assert res.changed
    dst = home / ".pi" / "agent" / "extensions" / capture_install.PI_EXTENSION_NAME
    assert dst.is_file()
    assert dst.read_bytes() == _PI, "the installed extension is not the shipped artifact"


def test_pi_install_disables_a_legacy_symlink_without_touching_its_target(home):
    """#3713 collision guard, non-destructive leg.

    Mutation: `os.remove(legacy.resolve())` / `shutil.rmtree` instead of
    `unlink()` — the agent-infra checkout the symlink points at is destroyed."""
    checkout = home / "agent-infra" / "extensions" / "tortoise-capture"
    checkout.mkdir(parents=True)
    (checkout / "index.ts").write_text("// legacy producer\n")
    ext_dir = home / ".pi" / "agent" / "extensions"
    ext_dir.mkdir(parents=True)
    (ext_dir / "tortoise-capture").symlink_to(checkout, target_is_directory=True)

    res = install_capture("pi", home=home)

    assert res.ok, res.error
    assert not (ext_dir / "tortoise-capture").is_symlink(), "legacy symlink survived"
    assert (checkout / "index.ts").read_text() == "// legacy producer\n", (
        "the symlink TARGET was deleted — the guard must only unlink the link")
    assert (ext_dir / capture_install.PI_EXTENSION_NAME).read_bytes() == _PI


def test_pi_install_disables_a_legacy_directory_non_destructively(home):
    """A real ``tortoise-capture/`` directory is RENAMED, never deleted — its
    files must survive under the dot-prefixed name Pi's loader skips.

    Mutation: `shutil.rmtree(legacy)` (the user's files are gone)."""
    ext_dir = home / ".pi" / "agent" / "extensions"
    legacy = ext_dir / "tortoise-capture"
    legacy.mkdir(parents=True)
    (legacy / "index.ts").write_text("// legacy producer\n")

    res = install_capture("pi", home=home)

    assert res.ok, res.error
    assert not legacy.exists()
    disabled = ext_dir / capture_install.PI_DISABLED_DIRNAME
    assert (disabled / "index.ts").read_text() == "// legacy producer\n", (
        "the legacy extension's files were not preserved")
    assert (ext_dir / capture_install.PI_EXTENSION_NAME).read_bytes() == _PI


def test_pi_install_is_a_clean_no_op_on_rerun(home):
    """Mutation: drop the `_is_regular_unchanged` guard (the mtime moves) — or
    re-run the legacy guard unconditionally (a second rename would raise)."""
    install_capture("pi", home=home)
    dst = home / ".pi" / "agent" / "extensions" / capture_install.PI_EXTENSION_NAME
    before = dst.stat().st_mtime_ns

    second = install_capture("pi", home=home)

    assert second.ok, second.error
    assert second.changed is False, f"second run reported changes: {second.actions}"
    assert second.actions == ()
    assert dst.stat().st_mtime_ns == before, "a re-run rewrote the extension"


def test_pi_install_fails_loudly_when_the_extensions_path_is_a_file(home):
    """Mutation: swallow the mkdir OSError — the install reports success with
    no extension on disk (Pi then captures nothing, silently)."""
    (home / ".pi" / "agent").mkdir(parents=True)
    (home / ".pi" / "agent" / "extensions").write_text("")

    res = install_capture("pi", home=home)

    assert not res.ok, "an impossible install reported success"
    assert "not a directory" in res.error


def test_pi_install_fails_loudly_when_a_legacy_disable_would_destroy_state(home):
    """Both the legacy entry AND its disabled name exist → refuse loudly
    rather than clobber or silently leave two producers registered.

    Mutation: `legacy.rename(disabled)` unconditionally (the existing disabled
    tree is destroyed / the rename raises a bare traceback)."""
    ext_dir = home / ".pi" / "agent" / "extensions"
    (ext_dir / "tortoise-capture").mkdir(parents=True)
    (ext_dir / capture_install.PI_DISABLED_DIRNAME).mkdir(parents=True)
    (ext_dir / capture_install.PI_DISABLED_DIRNAME / "keep.ts").write_text("keep\n")

    res = install_capture("pi", home=home)

    assert not res.ok
    assert "already exists" in res.error
    assert (ext_dir / capture_install.PI_DISABLED_DIRNAME / "keep.ts").exists()


# ── the CLI surface (`tortoise install <harness>`) ──────────────────────


def test_cli_install_claude_installs_capture_in_a_temp_home(cli):
    """Mutation: drop the `_install_capture_seam` call from `_cmd_install_hooks`
    — exit 0, the read hook installed, and NO capture scripts on disk."""
    run, root, _home = cli

    r = run("install", "claude", "--dir", str(root))

    assert r.returncode == 0, r.stderr
    assert (root / ".claude" / "hooks" / "session-start.sh").is_file()
    assert (root / ".claude" / "hooks" / "session-end.sh").is_file()
    cfg = _settings(root)
    assert cfg["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] == CLAUDE_TIMEOUT
    assert cfg["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"] == CLAUDE_TIMEOUT
    assert "UserPromptSubmit" in cfg["hooks"]  # the read hook still lands


def test_cli_install_claude_second_run_is_reported_as_a_no_op(cli):
    """The same command is the upgrade path. Mutation: append unconditionally
    in `merge_capture_hooks` (the second run duplicates the entries)."""
    run, root, _home = cli
    run("install", "claude", "--dir", str(root))
    before = _mtimes(root)

    r = run("install", "claude", "--dir", str(root))

    assert r.returncode == 0, r.stderr
    assert "capture seam already installed" in r.stdout
    cfg = _settings(root)
    assert len(cfg["hooks"]["SessionStart"]) == 1
    assert len(cfg["hooks"]["SessionEnd"]) == 1
    # the whole command is the upgrade path: a second run must not churn the
    # hooks OR settings.json (the read-hook half used to rewrite it every run)
    assert _mtimes(root) == before, "a re-run rewrote a file it should have skipped"


def test_cli_install_pi_writes_the_extension_into_the_temp_home(cli):
    """Mutation: add "pi" to the parser choices without handling it — the
    request falls into the cline branch and writes a bogus
    `.cline/hooks/UserPromptSubmit` instead of the capture extension."""
    run, root, home = cli

    r = run("install", "pi")

    assert r.returncode == 0, r.stderr
    assert (home / ".pi" / "agent" / "extensions"
            / capture_install.PI_EXTENSION_NAME).read_bytes() == _PI
    assert not (root / ".cline").exists(), "pi was mis-routed to the cline seam"


def test_cli_install_pi_fails_loudly_and_preserves_the_blocking_file(cli):
    """Mutation: print success on a failed install (non-zero exit + a message
    on stderr are the whole contract)."""
    run, _root, home = cli
    (home / ".pi" / "agent").mkdir(parents=True)
    blocker = home / ".pi" / "agent" / "extensions"
    blocker.write_text("")

    r = run("install", "pi")

    assert r.returncode != 0, "a failed install exited 0"
    assert "Capture install FAILED" in r.stderr
    assert blocker.read_text() == ""


def test_cli_install_pi_dry_run_does_not_claim_installed(cli):
    """`--dry-run` writes nothing, so it must not print "installed".

    Mutation: print the pi success sentence unconditionally (a false success
    message over an install that deliberately did not happen)."""
    run, _root, home = cli

    r = run("install", "pi", "--dry-run")

    assert r.returncode == 0, r.stderr
    assert "Pi capture extension installed" not in r.stdout
    assert "[dry-run]" in r.stdout
    assert not (home / ".pi" / "agent" / "extensions"
                / capture_install.PI_EXTENSION_NAME).exists(), (
        "a dry run wrote the extension")


def test_cli_install_claude_dry_run_writes_nothing(cli):
    """`install claude --dry-run` must report what WOULD happen and write
    nothing — neither the hook scripts nor the merged ``settings.json``.

    Mutation: make ``_install_claude`` ignore ``dry_run`` (it writes the two
    scripts and the settings merge before printing) — the on-disk assertions
    below turn RED."""
    run, root, _home = cli

    r = run("install", "claude", "--dir", str(root), "--dry-run")

    assert r.returncode == 0, r.stderr
    # Nothing was written — not even the hooks directory. This is the primary
    # signal: a dry run that touched disk is the false-success shape.
    assert not (root / ".claude").exists(), "a dry run created .claude/"
    assert not (root / ".claude" / "hooks" / "session-start.sh").exists()
    assert not (root / ".claude" / "hooks" / "session-end.sh").exists()
    assert not (root / ".claude" / "settings.json").exists()
    # ...and it must still SAY what it would do, in dry-run voice.
    assert "[dry-run]" in r.stdout
    assert "would install" in r.stdout, r.stdout
    assert "would merge SessionStart + SessionEnd capture hooks" in r.stdout, (
        r.stdout)


def test_cli_install_pi_uninstall_never_touches_a_cline_file(cli):
    """`pi` has no read hook — `--uninstall` must not fall through to the
    cline target and rewrite `.cline/hooks/UserPromptSubmit`.

    Mutation: route every `--uninstall` to `_install_read_hook` before the pi
    check (pi lands in that function's cline `else` branch, reads the cline
    file as if it were a pi registration, and prints a misleading path)."""
    run, root, _home = cli
    cline = root / ".cline" / "hooks" / "UserPromptSubmit"
    cline.parent.mkdir(parents=True)
    original = json.dumps({"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": "someone-elses.sh"}]}]}})
    cline.write_text(original)

    r = run("install", "pi", "--uninstall")

    assert r.returncode == 0, r.stderr
    assert "pi has no shell-hook read seam" in r.stdout
    assert cline.read_text() == original, "the cline hook file was modified"


# ── #3915: the capture-install contract is pinned against #3866 (parity) ─
#
# `capture_install` (installs the seam) and `hook_install` (status/upgrade)
# must answer "is this entry ours?" identically.  Since the fix they share ONE
# classifier (`hook_install._invokes_script`); these tests pin the observable
# contract across both modules so a future re-split cannot reintroduce a silent
# divergence — the pre-fix copy diverged on 10 of 21 command forms (a duplicate
# SessionEnd registration for `/bin/sh <hook>`, fail-open for `sudo -u <hook>`).


_CLASSIFIER_PARITY_FORMS = [
    # forms that REALLY execute the hook — "ours" in both modules
    ".claude/hooks/session-end.sh",
    "bash .claude/hooks/session-end.sh",
    "/bin/bash .claude/hooks/session-end.sh",
    "/bin/sh .claude/hooks/session-end.sh",
    "timeout 5 .claude/hooks/session-end.sh",
    "timeout -s KILL 60 .claude/hooks/session-end.sh",
    "nice -n 5 .claude/hooks/session-end.sh",
    "xargs .claude/hooks/session-end.sh",
    "sudo -u root .claude/hooks/session-end.sh",
    "if true; then .claude/hooks/session-end.sh; fi",
    "A=/x/y .claude/hooks/session-end.sh",
    "env -u FOO .claude/hooks/session-end.sh",
    "true; .claude/hooks/session-end.sh",
    "bash -c 'true; .claude/hooks/session-end.sh'",
    "2>/dev/null .claude/hooks/session-end.sh",
    "$CLAUDE_PROJECT_DIR/.claude/hooks/session-end.sh",
    # forms that must stay FOREIGN in both modules
    "cat .claude/hooks/session-end.sh",
    "cat $CLAUDE_PROJECT_DIR/.claude/hooks/session-end.sh",
    "command -v .claude/hooks/session-end.sh",
    "sudo -u .claude/hooks/session-end.sh",  # path is sudo's -u VALUE
    "vendor/.claude/hooks/session-end.sh",
]


def test_classifier_parity_with_hook_install(tmp_path):
    """`capture_install._is_our_script_command` and
    `hook_install._invokes_script` return the SAME verdict for every form.

    Mutation: reintroduce a local classifier (the pre-fix copy) — any launcher
    form above (`/bin/sh`, `nice -n`, `sudo -u root`, …) diverges and REDs."""
    for command in _CLASSIFIER_PARITY_FORMS:
        ours = capture_install._is_our_script_command(
            command, "session-end.sh", ".claude/hooks", tmp_path)
        theirs = hook_install._invokes_script(
            command, "session-end.sh", ".claude/hooks", tmp_path)
        assert ours == theirs, (
            f"classifier divergence on {command!r}: "
            f"capture_install={ours}, hook_install={theirs}")


def test_entry_shape_gate_reads_only_nested_typed_command_handlers(tmp_path):
    """The installer's entry reader accepts ONLY the shape Claude Code
    actually executes — a matcher entry whose ``hooks`` ARRAY holds typed
    ``{"type": "command", "command": …}`` dicts.

    Everything the harness silently IGNORES (a flat ``{type, command}`` at the
    event level, a handler missing its ``type``, a non-string command, a
    non-array ``hooks``) must NOT be read as an existing registration — the
    installer would otherwise "repair" an entry Claude Code never runs and
    leave the project capturing nothing while reporting success (#3866).

    Mutation: make ``hook_install._entry_command_dicts`` (which
    ``capture_install._our_command_dicts`` delegates to) return a flat
    event-level entry as ours — ``return [entry]`` instead of ``[]`` — and the
    flat case REDs; make ``_ok`` stop checking ``item.get("type")`` and the
    untyped case REDs."""
    hooks_dir = ".claude/hooks"
    ours = {"type": "command", "command": ".claude/hooks/session-end.sh",
            "timeout": 60}
    foreign = {"type": "command",
               "command": "vendor/.claude/hooks/session-end.sh"}

    def read(entry):
        return capture_install._our_command_dicts(
            entry, "session-end.sh", hooks_dir, tmp_path)

    # Not entries at all.
    assert read(None) == []
    assert read("session-end.sh") == []
    # A flat ``{type, command}`` at the EVENT level: ignored by Claude Code.
    assert read({"type": "command",
                 "command": ".claude/hooks/session-end.sh"}) == []
    # A handler missing its ``type``.
    assert read({"matcher": "", "hooks": [
        {"command": ".claude/hooks/session-end.sh"}]}) == []
    # A non-string command.
    assert read({"matcher": "", "hooks": [
        {"type": "command", "command": 5}]}) == []
    # ``hooks`` is not an array.
    assert read({"hooks": {"type": "command",
                           "command": ".claude/hooks/session-end.sh"}}) == []
    # A foreign path at the same basename is somebody else's hook.
    assert read({"matcher": "", "hooks": [dict(foreign)]}) == []
    # Positive control: a properly nested typed entry IS read — and EVERY
    # child is inspected, not just the first (ours may sit behind a foreign
    # hook, and one left untimed is cancelled at the 1.5 s default).
    assert read({"matcher": "", "hooks": [dict(foreign), dict(ours)]}) == [ours]
    assert read({"matcher": "", "hooks": [dict(ours), dict(ours)]}) == [
        ours, ours]


def test_install_then_status_is_clean_and_upgrade_is_a_no_op(tmp_path):
    """An install this module produces is one `tortoise hooks status` reads as
    current, and `tortoise hooks upgrade` changes nothing.

    Mutation: emit a settings shape #3866 classifies as foreign (flat
    event-level `{type, command}`, a missing `type`, a foreign path) —
    `detect_install` then reports `missing-hook-entry` and this REDs."""
    res = install_capture("claude", root=tmp_path)
    assert res.ok, res.error

    assert hook_install.detect_install(tmp_path, "claude") == [], (
        "the installer produced state the drift detector calls drifted")
    upgrade = hook_install.upgrade_install(tmp_path, "claude")
    assert upgrade.refused is None, upgrade.refused
    assert upgrade.actions == [], (
        f"upgrade was not a no-op on a fresh install: {upgrade.actions}")

    again = install_capture("claude", root=tmp_path)
    assert again.ok and again.changed is False, (
        "re-installing over a status-current install was not a clean no-op")


def test_upgrade_then_install_agrees_in_the_other_direction(tmp_path):
    """The reverse direction: after #3866's `upgrade_install` repairs a stale
    install, `capture_install` is itself a clean no-op.

    Mutation: emit or expect a settings/script shape the two modules disagree
    on — the installer rewrites what upgrade just repaired (changed=True)."""
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    # A stale, unversioned copy (the pre-#3795 population — it must still LOOK
    # like a Tortoise hook, or upgrade refuses it as foreign) + a timeoutless
    # registration: upgrade repairs both halves.
    for name in ("session-start.sh", "session-end.sh"):
        (hooks / name).write_text("#!/bin/sh\n# tortoise session capture\nexit 0\n")
        os.chmod(hooks / name, 0o644)
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({
        "hooks": {
            "SessionStart": [{"matcher": "", "hooks": [
                {"type": "command",
                 "command": ".claude/hooks/session-start.sh"}]}],
            "SessionEnd": [{"matcher": "", "hooks": [
                {"type": "command",
                 "command": ".claude/hooks/session-end.sh"}]}],
        }}))

    upgrade = hook_install.upgrade_install(tmp_path, "claude")
    assert upgrade.refused is None, upgrade.refused
    assert upgrade.actions, "upgrade repaired nothing on a stale install"

    res = install_capture("claude", root=tmp_path)
    assert res.ok, res.error
    assert res.changed is False, (
        f"installer rewrote a state upgrade had just repaired: {res.actions}")


@pytest.mark.parametrize("command", [
    "cat .claude/hooks/session-end.sh",
    "cat $CLAUDE_PROJECT_DIR/.claude/hooks/session-end.sh",
    "command -v .claude/hooks/session-end.sh",
    "vendor/.claude/hooks/session-end.sh",
    "sudo -u .claude/hooks/session-end.sh",
])
def test_foreign_shapes_are_not_ours_in_both_modules(tmp_path, command):
    """The shapes #3866 classifies as NOT ours are not "repaired" here either
    — repairing one would leave the project capturing nothing while reporting
    success.

    Mutation: accept any token naming our basename (a timeout is stamped on a
    foreign command and no real registration is appended)."""
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"hooks": {"SessionEnd": [{"matcher": "",
        "hooks": [{"type": "command", "command": command,
                   "timeout": 60}]}]}}))

    install_capture("claude", root=tmp_path)

    entries = _settings(tmp_path)["hooks"]["SessionEnd"]
    foreign = [h for e in entries for h in e.get("hooks", [])
               if h.get("command") == command]
    assert len(foreign) == 1, "the foreign entry was dropped"
    assert len(entries) == 2, (
        f"no proper registration appended for a foreign command: {entries}")
    ours = [h for e in entries for h in e.get("hooks", [])
            if h.get("command") == ".claude/hooks/session-end.sh"]
    assert len(ours) == 1 and ours[0]["timeout"] == CLAUDE_TIMEOUT
    assert hook_install.detect_install(tmp_path, "claude") == []


def test_float_timeout_parity_between_install_and_status(tmp_path):
    """A float timeout is preserved by BOTH sides: the installer leaves 120.0
    alone and #3866 neither reports it as drift nor lowers it on upgrade.

    Mutation: restore the int-only predicate in `hook_install`'s
    `_settings_findings`/`_merge_settings` — status reports
    `settings-no-timeout` and upgrade rewrites 120.0 → 60."""
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"hooks": {"SessionEnd": [{"matcher": "",
        "hooks": [{"type": "command",
                   "command": ".claude/hooks/session-end.sh",
                   "timeout": 120.0}]}]}}))

    res = install_capture("claude", root=tmp_path)
    assert res.ok, res.error
    inner = _settings(tmp_path)["hooks"]["SessionEnd"][0]["hooks"][0]
    assert inner["timeout"] == 120.0, "the installer lowered a float timeout"

    findings = hook_install.detect_install(tmp_path, "claude")
    assert not [f for f in findings
                if f.kind in ("settings-no-timeout",
                              "settings-low-timeout")], (
        f"status treats the preserved float as drift: {findings}")
    upgrade = hook_install.upgrade_install(tmp_path, "claude")
    assert upgrade.refused is None, upgrade.refused
    after = _settings(tmp_path)["hooks"]["SessionEnd"][0]["hooks"][0]
    assert after["timeout"] == 120.0, (
        f"upgrade lowered the float timeout: {upgrade.actions}")


# ── the seam map is one map (drift guards) ──────────────────────────────


def test_capture_seam_matches_the_dashboard_seam_map_and_timeout():
    """The dashboard's copy-paste block and the installer must not drift.

    Mutation: change ``HARNESS_CAPTURE_SEAM.claude`` (or the emitted timeout)
    in ``harnesses.js`` without updating the installer — the two surfaces then
    disagree about what "installed" means."""
    src = _DASHBOARD.read_text(encoding="utf-8")
    block = re.search(r"export const HARNESS_CAPTURE_SEAM = \{(.*?)\n\}",
                      src, re.DOTALL)
    assert block, "HARNESS_CAPTURE_SEAM not found in harnesses.js"
    declared = dict(re.findall(r"(\w[\w-]*):\s*'([^']+)'", block.group(1)))
    assert declared == CAPTURE_SEAM
    # The timeout must be pinned in the CAPTURE block specifically: asserting
    # the substring against the whole file is satisfied by HARNESS_INSTALL's
    # own copy, so mutating the capture instruction alone would leave this
    # GREEN (a drift guard that cannot RED the drift it exists to catch).
    capture = re.search(r"export const HARNESS_CAPTURE_INSTALL = \{(.*?)\n\}",
                        src, re.DOTALL)
    assert capture, "HARNESS_CAPTURE_INSTALL not found in harnesses.js"
    claude_block = re.search(r"claude:\s*`(.*?)`\s*,", capture.group(1),
                             re.DOTALL)
    assert claude_block, "HARNESS_CAPTURE_INSTALL.claude not found"
    fragment = claude_block.group(1)
    # EVERY timeout in the capture fragment must equal the installer's, and the
    # fragment must register BOTH scripts — a single `"timeout": 60` substring
    # check passes while the sibling script's timeout has drifted to anything.
    timeouts = re.findall(r'"timeout":\s*(\d+)', fragment)
    assert len(timeouts) == 2, (
        f"the dashboard capture block declares {len(timeouts)} timeout(s), "
        "expected one per script")
    assert all(int(v) == CLAUDE_TIMEOUT for v in timeouts), (
        f"the dashboard capture block emits timeout(s) {timeouts}, expected "
        f"{CLAUDE_TIMEOUT} — the installer and the copy-paste block disagree")
    assert ".claude/hooks/session-start.sh" in fragment
    assert ".claude/hooks/session-end.sh" in fragment


def test_every_capture_artifact_ships_in_the_wheel():
    """``tortoise install`` resolves its artifacts from the PACKAGE, so a wheel
    missing one fails the install at runtime.  Evaluates the declared
    ``package-data`` globs against the real tree.

    Mutation: remove ``pi-hooks/tortoise-capture.ts`` from
    ``[tool.setuptools.package-data]`` (the Pi install fails on a wheel)."""
    with open(_REPO_ROOT / "pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    patterns = data["tool"]["setuptools"]["package-data"]["tortoise"]

    def covered(relative: str) -> bool:
        return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)

    for harness, artifact in CAPTURE_SEAM.items():
        rel = str(Path(artifact).relative_to("tortoise"))
        assert covered(rel), (
            f"{harness}: {artifact} is not matched by any package-data pattern "
            f"{patterns} — a wheel install would not ship it")

    # ``CAPTURE_SEAM["claude"]`` names only ``session-end.sh``, but the
    # installer resolves EVERY ``CLAUDE_SCRIPTS`` entry — a glob narrowed to
    # the one artifact in the seam map leaves a wheel whose
    # ``session-start.sh`` is missing (the install then fails on the user's
    # machine, not in CI).
    claude_artifacts = [f"tortoise/claude-hooks/{name}"
                        for name in capture_install.CLAUDE_SCRIPTS]
    assert len(claude_artifacts) >= 2, (
        "CLAUDE_SCRIPTS declares fewer scripts than the installer needs:"
        f" {capture_install.CLAUDE_SCRIPTS}")
    for artifact in claude_artifacts:
        rel = str(Path(artifact).relative_to("tortoise"))
        assert covered(rel), (
            f"{artifact} is not matched by any package-data pattern "
            f"{patterns} — a wheel install would fail resolving it")
