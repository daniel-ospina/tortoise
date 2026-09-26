"""#3808 — the installer must install CAPTURE, per harness, idempotently.

The capture hook is fail-open, so a missing or mistyped install files no
sessions and reports no error.

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
import math
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
    CLAUDE_CAPTURE_HOOKS,
    CLAUDE_PER_TURN_TIMEOUT,
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
        # Hermetic: a CODEX_HOME in the ambient env would send the codex
        # capture install into the REAL ~/.codex. Empty ⇒ ~/.codex under the
        # temp HOME.
        "CODEX_HOME": "",
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


#: Modes that are all "not installed" for a hook Claude Code, the OWNER,
#: must execute directly.  `0o644` has no exec bit at all; `0o601` is owner-rw
#: + OTHERS-exec (the exact reproduction in #4000); `0o410` is owner-read +
#: GROUP-exec, i.e. a non-owner exec bit is the ONLY exec bit.  The last two
#: are what made "any exec bit" (`st_mode & 0o111`) a silent false success: the
#: owner still cannot run the hook while the installer reports it already
#: correct.  `0o410` rather than a bare `0o010` because an owner-unreadable
#: file never reaches the repair path — the foreign-install guard cannot verify
#: its bytes and refuses it (#4000 scoping note).
_UNRUNNABLE_HOOK_MODES = (0o644, 0o601, 0o410)


@pytest.mark.parametrize("mode", _UNRUNNABLE_HOOK_MODES,
                         ids=[f"{m:o}" for m in _UNRUNNABLE_HOOK_MODES])
def test_claude_install_repairs_a_missing_exec_bit(tmp_path, mode):
    """Bytes identical but no OWNER exec bit is NOT "already installed".

    Claude Code executes `.claude/hooks/<name>` directly, and the fail-open
    script swallows the permission error — so a hook that lost its exec bit
    (a `cp` without `chmod`, a zip/tarball checkout) files nothing while the
    install reports success.

    The repair must test the OWNER's bit (`stat.S_IXUSR`), the same bit the
    assertion below makes: an any-exec-bit test (`st_mode & 0o111`) passes for
    `0o601`/`0o410`, where a non-owner exec bit is the only exec bit and
    `os.access(hook, os.X_OK)` is False, so the second install returns
    ``changed=False, actions=()`` on an unexecutable hook (#4000).

    Mutation: change the guard back to `st_mode & 0o111` — the `0o601`/`0o410`
    cases RED (`changed` stays False); or treat identical bytes as unchanged
    without checking the mode at all — every case REDs."""
    install_capture("claude", root=tmp_path)
    hooks = [tmp_path / ".claude" / "hooks" / name
             for name in ("session-start.sh", "session-end.sh")]
    for hook in hooks:
        os.chmod(hook, mode)

    res = install_capture("claude", root=tmp_path)

    assert res.ok, res.error
    assert res.changed is True, (
        f"the lost owner exec bit was not repaired from mode {mode:o}")
    for hook in hooks:
        assert hook.stat().st_mode & stat.S_IXUSR, f"{hook.name} is still unrunnable"
        assert os.access(hook, os.X_OK), (
            f"{hook.name} is not executable by its owner (mode "
            f"{hook.stat().st_mode & 0o777:o})")
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


def test_claude_install_refuses_a_foreign_hook_and_keeps_it(tmp_path):
    """A differing, non-Tortoise ``session-end.sh`` is another product's
    hook: refuse the WHOLE install (nothing written) instead of silently
    replacing it — exactly what ``hook_install.upgrade_install`` does on the
    same tree.

    Mutation: drop the ``_foreign_install_refusal`` preflight — the foreign
    bytes are overwritten by the shipped script and the call reports success."""
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    foreign = hooks / "session-end.sh"
    foreign.write_text("#!/bin/sh\necho not-tortoise\n")

    res = install_capture("claude", root=tmp_path)

    assert not res.ok, "a foreign hook was silently replaced"
    assert "does not look like a Tortoise artifact" in res.error, res.error
    assert foreign.read_text() == "#!/bin/sh\necho not-tortoise\n", (
        "the foreign hook's bytes were destroyed")
    # The refusal is atomic — the other script was not written either, and no
    # ``.bak`` was created for a file we never touched.
    assert not (hooks / "session-start.sh").exists()
    assert not (hooks / "session-end.sh.bak").exists()


def test_claude_install_backs_up_a_stale_tortoise_hook(tmp_path):
    """A differing copy that IS a Tortoise hook (an older or locally edited
    install) is preserved as ``<name>.bak`` before the shipped bytes replace
    it — the ``hook_install.upgrade_install`` contract.

    Mutation: remove the backup write from the install loop — the old bytes
    are destroyed silently."""
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    stale = _END + b"\n# locally edited - still a Tortoise hook\n"
    (hooks / "session-end.sh").write_bytes(stale)

    res = install_capture("claude", root=tmp_path)

    assert res.ok, res.error
    backup = hooks / "session-end.sh.bak"
    assert backup.exists(), res.actions
    assert backup.read_bytes() == stale, "the differing copy was not preserved"
    assert (hooks / "session-end.sh").read_bytes() == _END
    assert any("backed up" in a for a in res.actions), res.actions


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


def test_claude_install_refuses_a_symlink_loop_instead_of_raising(tmp_path):
    """A symlink cycle under the install root is a refusal, not a traceback.

    Py3.12 ``Path.resolve()`` raises ``RuntimeError`` (deliberately, not
    ``OSError``) on a symlink loop, and ``install_capture`` only caught
    ``OSError`` — so a project whose ``.claude`` links to itself escaped the
    module's documented populated-error contract as a CLI traceback.

    Mutation: remove the ``except (OSError, RuntimeError)`` guard around
    ``_symlink_escape``'s ``resolve()`` — this call raises ``RuntimeError``
    instead of returning ``ok=False`` with a populated error."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".claude").symlink_to(".claude")  # a self-loop

    res = install_capture("claude", root=root)

    assert not res.ok, "a symlink loop reported success"
    assert "Refusing" in res.error, res.error
    assert "symlink loop" in res.error, res.error
    assert (root / ".claude").is_symlink(), "the cyclic link was replaced"


def test_claude_install_fails_loudly_when_the_hooks_path_is_not_a_directory(tmp_path):
    """An install that cannot write the hook must NOT report success.

    The guard that carries this test's signal is ``_preflight_probe``'s
    ``is_dir`` refusal (the write-free probe), NOT ``_preflight_writable``.

    Mutation: neutralize ``_preflight_probe``'s ``is_dir`` refusal — the
    refusal then falls through to ``_preflight_writable``'s ``mkdir``
    ``FileExistsError`` ("cannot create …"), and ``"not a directory"``
    REDs.  Swallowing the ``OSError`` in ``_preflight_writable`` (or skipping
    it entirely) does NOT RED this test, because the probe refuses first —
    naming that mutation was #4001 R29."""
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
    assert "install failed" in res.error, res.error
    assert "PermissionError" in res.error, res.error
    assert "NOT fully installed" in res.error, res.error
    # The dangerous half is REAL: both scripts were written and chmodded
    # before the settings write failed — the error is the ONLY thing telling
    # the user the seam is incomplete.
    for name in ("session-start.sh", "session-end.sh"):
        script = tmp_path / ".claude" / "hooks" / name
        assert script.is_file(), f"{name} was not written before the failure"
        assert script.stat().st_mode & stat.S_IXUSR, f"{name} is not owner-executable"
    # The registration write failed, so no settings.json was left behind.
    assert not settings_path.exists()


def test_capture_install_turns_a_deep_json_recursionerror_into_a_populated_error(
        tmp_path):
    """``install_capture`` called DIRECTLY on a deeply nested
    ``settings.json`` must not raise — the failure is a populated
    ``InstallResult.error``.

    ``json.loads`` raises ``RecursionError`` (a ``RuntimeError``, not an
    ``OSError``) on a deeply nested document, and ``_load_settings`` catches
    ``ValueError`` only, so the failure reaches ``install_capture``'s boundary
    — which caught ``OSError`` alone and let it escape the public API,
    contradicting the module's "every failure path populates
    ``InstallResult.error``" contract (#3999).

    GAP THIS CLOSES: the ``deep-json-recursionerror`` case in
    ``tests/test_platform_seams.py`` runs through ``_read_hook_refusal`` (the
    READ half's boundary), so it cannot RED for THIS module.  This test calls
    ``install_capture`` directly.

    Mutation: narrow the boundary back to ``except OSError`` — ``RecursionError``
    escapes and this call raises instead of returning a populated error."""
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_bytes(b"[" * 100_000 + b"]" * 100_000)

    res = install_capture("claude", root=tmp_path)

    assert not res.ok, "a RecursionError was reported as a successful install"
    assert res.error, "the RecursionError path returned no populated error"
    assert "RecursionError" in res.error, res.error


def test_capture_install_reraises_memory_error(tmp_path, monkeypatch):
    """``MemoryError`` is the one failure a refusal is the wrong answer for
    (the refusal message itself allocates), so the boundary re-raises it
    rather than converting it — the SAME form the read half's boundary uses.

    Mutation: drop the ``except MemoryError: raise`` clause — the following
    ``except Exception`` swallows it into a populated error and this
    ``pytest.raises`` REDs."""
    def _oom(*_args, **_kwargs):
        raise MemoryError("pretend the process cannot allocate")

    monkeypatch.setattr(capture_install, "_install_claude", _oom)

    with pytest.raises(MemoryError):
        install_capture("claude", root=tmp_path)


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
    assert "install failed" in captured.err, captured.err
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
    """Mutation: return an empty successful result for an unknown harness.

    ``cursor`` used to be the unknown-harness stand-in; it is a real seam now
    (#3819), so this uses a harness that genuinely has no capture seam.
    """
    res = install_capture("vim", root=tmp_path)

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
    """An install into a home whose extensions path is a FILE must not report
    success (Pi then captures nothing, silently).

    As with the claude case above, the guard carrying the signal is
    ``_preflight_probe``'s ``is_dir`` refusal, not the ``mkdir`` OSError.

    Mutation: neutralize ``_preflight_probe``'s ``is_dir`` refusal — the
    refusal falls through to ``_preflight_writable``'s ``mkdir``
    ``FileExistsError`` ("cannot create …") and ``"not a directory"`` REDs.
    Swallowing the ``mkdir`` ``OSError`` does NOT RED this test (#4001 R29)."""
    (home / ".pi" / "agent").mkdir(parents=True)
    (home / ".pi" / "agent" / "extensions").write_text("")

    res = install_capture("pi", home=home)

    assert not res.ok, "an impossible install reported success"
    assert "not a directory" in res.error


def test_pi_install_refuses_a_foreign_extension_and_keeps_it(home):
    """A differing, non-Tortoise ``tortoise-capture.ts`` at the destination
    is refused (nothing written) instead of silently replaced.

    Mutation: drop the ``_foreign_install_refusal`` preflight — a user's own
    same-named extension is overwritten and the install reports success."""
    ext_dir = home / ".pi" / "agent" / "extensions"
    ext_dir.mkdir(parents=True)
    foreign = ext_dir / "tortoise-capture.ts"
    foreign.write_text("// someone else's extension\nexport default 1\n")

    res = install_capture("pi", home=home)

    assert not res.ok, "a foreign extension was silently replaced"
    assert "does not look like a Tortoise artifact" in res.error, res.error
    assert foreign.read_text() == (
        "// someone else's extension\nexport default 1\n")
    assert not (ext_dir / "tortoise-capture.ts.bak").exists()


def test_pi_install_backs_up_a_stale_extension(home):
    """A differing copy that IS the Tortoise extension is preserved as
    ``<name>.bak`` before the shipped bytes replace it.

    Mutation: remove the backup write — the stale extension is destroyed."""
    ext_dir = home / ".pi" / "agent" / "extensions"
    ext_dir.mkdir(parents=True)
    stale = _PI + b"\n// locally edited - still ours (tortoise session)\n"
    (ext_dir / "tortoise-capture.ts").write_bytes(stale)

    res = install_capture("pi", home=home)

    assert res.ok, res.error
    backup = ext_dir / "tortoise-capture.ts.bak"
    assert backup.exists(), res.actions
    assert backup.read_bytes() == stale, "the differing copy was not preserved"
    assert (ext_dir / "tortoise-capture.ts").read_bytes() == _PI


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


# ── codex: the artifact + the $CODEX_HOME registration (#3818) ──────────


def _codex_json(home: Path) -> dict:
    return json.loads((home / ".codex" / "hooks.json").read_text())


def _codex_commands(home: Path) -> list[str]:
    entries = _codex_json(home)["hooks"][capture_install.CODEX_EVENT]
    return [h["command"] for e in entries for h in e.get("hooks", [])]


def _codex_session_end_entries(*roots: Path) -> list[dict]:
    """Every ``SessionEnd`` capture entry under ``roots``, wherever the
    installer actually wrote it — so a mutation that resolves the root wrongly
    REDs on the assertion (a relative command / a duplicate), not on a
    hard-coded path the test happened to guess."""
    entries: list[dict] = []
    for root in roots:
        if not root.exists():
            continue
        for hooks_json in sorted(root.rglob("hooks.json")):
            data = json.loads(hooks_json.read_text())
            entries.extend((data.get("hooks") or {}).get(
                capture_install.CODEX_EVENT) or [])
    return entries


def _codex_session_end_commands(*roots: Path) -> list[str]:
    return [h["command"] for e in _codex_session_end_entries(*roots)
            for h in e.get("hooks", [])]


def _cli_env(home: Path, codex_home: str) -> dict:
    return {
        **os.environ,
        "HOME": str(home),
        "CODEX_HOME": codex_home,
        "TORTOISE_DB_URI": "",
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
    }


def _resolved_codex_root(home: Path, codex_home: str) -> Path:
    return (home / ".codex") if codex_home.startswith("~") else (home / codex_home)


def test_codex_install_writes_the_hook_and_merges_the_absolute_command(home):
    """The Codex seam: the shipped hook into ``$CODEX_HOME/hooks/`` (0755) and
    a ``SessionEnd`` registration in ``$CODEX_HOME/hooks.json`` whose command
    is the script's ABSOLUTE path.

    Mutation: register a relative command (Codex runs the hook from the
    session's cwd, so it never resolves) — this REDs."""
    res = install_capture("codex", home=home)

    assert res.ok, res.error
    installed = home / ".codex" / "hooks" / capture_install.CODEX_SCRIPT_NAME
    assert installed.read_bytes() == (
        _REPO_ROOT / "tortoise" / "codex-hooks" / "session-end.sh").read_bytes()
    assert installed.stat().st_mode & stat.S_IXUSR
    assert _codex_commands(home) == [str(installed)], _codex_json(home)
    assert os.path.isabs(_codex_commands(home)[0])


def test_codex_install_honors_codex_home(tmp_path, monkeypatch):
    """Codex's whole config tree moves with ``$CODEX_HOME`` — an install that
    ignored it would register the hook in a file Codex never reads on every
    non-default setup.

    Mutation: resolve ``~/.codex`` unconditionally — this REDs."""
    home = tmp_path / "home"
    codex_home = tmp_path / "elsewhere"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    res = install_capture("codex", home=home)

    assert res.ok, res.error
    assert (codex_home / "hooks.json").is_file()
    assert (codex_home / "hooks" / capture_install.CODEX_SCRIPT_NAME).is_file()
    assert not (home / ".codex").exists()


@pytest.mark.parametrize("codex_home", [None, "", "   ", "relcodex", "~/.codex"])
def test_codex_default_root_is_absolute_and_expanded(tmp_path, monkeypatch,
                                                     codex_home):
    """The ONE ``$CODEX_HOME`` resolver always returns an ABSOLUTE, expanded
    root — the layout's ``absolute_command`` invariant cannot hold otherwise.

    Verbatim, ``CODEX_HOME=relcodex`` registered ``relcodex/hooks/...``
    (Codex resolves it against the SESSION cwd — a silent no-capture) and a
    literal ``CODEX_HOME=~/.codex`` (a tilde written into a config file is
    never shell-expanded) made a literal ``~`` directory.  ``status`` then
    double-prepended the relative root, never recognized our own registration,
    and every reinstall appended a duplicate.

    Mutation: return ``Path(env)`` / ``home / default`` verbatim — the
    relative and tilde cases are non-absolute and this REDs."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))  # hermetic `~` expansion
    if codex_home is None:
        monkeypatch.delenv("CODEX_HOME", raising=False)
    else:
        monkeypatch.setenv("CODEX_HOME", codex_home)
    layout = hook_install.get_layout("codex")

    root = hook_install.default_root(layout, home)

    assert root == {
        None: home / ".codex",
        "": home / ".codex",
        "   ": home / ".codex",
        "relcodex": home / "relcodex",
        "~/.codex": home / ".codex",
    }[codex_home], codex_home
    assert root.is_absolute(), root


@pytest.mark.parametrize("codex_home", ["relcodex", "~/.codex"])
def test_codex_relative_or_tilde_codex_home_registers_absolute_and_status_clean(
        tmp_path, codex_home):
    """A relative or literal-tilde ``$CODEX_HOME`` still registers the script's
    ABSOLUTE path, and ``status`` recognizes that same registration in the same
    run (exit 0, not ``missing-hook-entry``).

    Mutation: resolve ``$CODEX_HOME`` verbatim — the registered command is not
    absolute and ``status`` double-prepends the root, exits 1 with
    ``missing-hook-entry``, and this REDs."""
    root = tmp_path / "proj"
    home = tmp_path / "home"
    root.mkdir()
    home.mkdir()
    env = _cli_env(home, codex_home)
    resolved = _resolved_codex_root(home, codex_home)

    r = _run(("install", "codex", "--dir", str(root)), env, root)

    assert r.returncode == 0, r.stdout + r.stderr
    registered = _codex_session_end_commands(home, root)
    assert registered == [
        str(resolved / "hooks" / capture_install.CODEX_SCRIPT_NAME)], registered
    assert os.path.isabs(registered[0]), registered
    s = _run(("hooks", "status", "--harness", "codex"), env, root)
    assert s.returncode == 0, s.stdout + s.stderr
    assert "are current" in s.stdout, s.stdout


@pytest.mark.parametrize("codex_home", ["relcodex", "~/.codex"])
def test_codex_reinstall_with_a_relative_or_tilde_codex_home_does_not_duplicate(
        tmp_path, codex_home):
    """Installing twice from a relative or literal-tilde ``$CODEX_HOME``
    leaves ONE ``SessionEnd`` registration.

    Mutation: resolve ``$CODEX_HOME`` verbatim — ``status`` does not recognize
    our own (relative) registration, so the second install appends a second
    entry and the count grows → this REDs."""
    root = tmp_path / "proj"
    home = tmp_path / "home"
    root.mkdir()
    home.mkdir()
    env = _cli_env(home, codex_home)
    resolved = _resolved_codex_root(home, codex_home)

    for _ in range(2):
        r = _run(("install", "codex", "--dir", str(root)), env, root)
        assert r.returncode == 0, r.stdout + r.stderr

    entries = _codex_session_end_entries(home, root)
    assert len(entries) == 1, entries
    commands = _codex_session_end_commands(home, root)
    assert commands == [
        str(resolved / "hooks" / capture_install.CODEX_SCRIPT_NAME)], commands


def test_codex_install_is_home_scoped_not_project_scoped(tmp_path):
    """VERIFIED LIVE (Codex CLI 0.154.0, #3818): only ``$CODEX_HOME/hooks.json``
    is a hook source — a project-local ``<repo>/.codex/hooks.json`` fires
    nothing (with or without project trust). The installer must therefore
    write HOME-scoped even when a project ``root`` is supplied.

    Mutation: write the registration into ``root/.codex/hooks.json`` — the
    install reports success while Codex never reads it, and this REDs."""
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    home.mkdir()
    proj.mkdir()

    res = install_capture("codex", root=proj, home=home)

    assert res.ok, res.error
    assert (home / ".codex" / "hooks.json").is_file()
    assert not (proj / ".codex").exists(), (
        "the capture seam must not be written to the dead project-local path")


def test_codex_install_is_a_clean_no_op_on_rerun(home):
    """Re-running is the upgrade path and must be a byte-level no-op.

    Mutation: append the registration unconditionally (a second run then
    emits a duplicate ``SessionEnd`` entry)."""
    assert install_capture("codex", home=home).ok
    json_path = home / ".codex" / "hooks.json"
    script = home / ".codex" / "hooks" / capture_install.CODEX_SCRIPT_NAME
    before = (json_path.stat().st_mtime_ns, script.stat().st_mtime_ns)

    again = install_capture("codex", home=home)

    assert again.ok and again.changed is False, again.actions
    assert (json_path.stat().st_mtime_ns, script.stat().st_mtime_ns) == before
    assert len(_codex_commands(home)) == 1, _codex_json(home)


def test_codex_install_preserves_foreign_keys_events_and_hooks(home):
    """Merge, never overwrite: an unrelated top-level key, another event, and
    a foreign hook in the SAME ``SessionEnd`` list all survive.

    Mutation: write the document wholesale (the foreign hook is lost)."""
    doc = {
        "model": "gpt-5.6-terra",
        "hooks": {
            "PermissionRequest": [{"hooks": [
                {"type": "command", "command": "/bin/other"}]}],
            "SessionEnd": [{"hooks": [
                {"type": "command", "command": "/bin/other-end"}]}],
        },
    }
    path = home / ".codex" / "hooks.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(doc))

    assert install_capture("codex", home=home).ok

    merged = _codex_json(home)
    assert merged["model"] == "gpt-5.6-terra"
    assert merged["hooks"]["PermissionRequest"][0]["hooks"][0]["command"] \
        == "/bin/other"
    assert "/bin/other-end" in _codex_commands(home)
    assert len(_codex_commands(home)) == 2, merged


def test_codex_install_repairs_a_stale_relative_command_in_place(home):
    """A registration of ours whose command is relative/stale must be repaired
    to the absolute path, not left as a silent no-capture.

    Mutation: skip the repair loop in ``merge_codex_capture_hooks`` — the
    stale command survives and this REDs."""
    script = home / ".codex" / "hooks" / capture_install.CODEX_SCRIPT_NAME
    script.parent.mkdir(parents=True)
    script.write_bytes((_REPO_ROOT / "tortoise" / "codex-hooks"
                        / "session-end.sh").read_bytes())
    (home / ".codex" / "hooks.json").write_text(json.dumps({"hooks": {
        capture_install.CODEX_EVENT: [{"hooks": [{
            "type": "command",
            "command": f"hooks/{capture_install.CODEX_SCRIPT_NAME}"}]}],
    }}))

    assert install_capture("codex", home=home).ok

    assert _codex_commands(home) == [str(script)], _codex_json(home)


def test_codex_install_refuses_a_foreign_script_and_keeps_it(home):
    """A foreign file at OUR script path is another product's — refuse whole,
    never clobber.

    Mutation: write through the foreign file (it is destroyed while the
    install reports success)."""
    script = home / ".codex" / "hooks" / capture_install.CODEX_SCRIPT_NAME
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n# someone else's hook\nexit 0\n")

    res = install_capture("codex", home=home)

    assert not res.ok and "does not look like a Tortoise artifact" in res.error
    assert "someone else's hook" in script.read_text()


def test_codex_install_refuses_invalid_hooks_json(home):
    """An unparsable ``hooks.json`` must never be clobbered.

    Mutation: fall back to ``{}`` on a parse error — the user's file is
    destroyed and this REDs."""
    path = home / ".codex" / "hooks.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")

    res = install_capture("codex", home=home)

    assert not res.ok and "not valid JSON" in res.error
    assert path.read_text() == "{ not json"


def test_codex_dry_run_writes_nothing(tmp_path):
    """``--dry-run`` is write-free.

    Mutation: drop the ``dry_run`` gate on the script/settings writes — files
    appear on disk and this REDs."""
    home = tmp_path / "home"
    home.mkdir()

    res = install_capture("codex", home=home, dry_run=True)

    assert res.ok, res.error
    assert res.changed is True
    assert not (home / ".codex").exists()


def test_codex_install_then_status_is_clean_and_upgrade_is_a_no_op(home):
    """The Codex seam ships the SAME version-marker/settings contract as
    Claude, so it is covered by the layout registry — not an install-only
    fork: `detect_install` reads the produced state as current and
    `upgrade_install` changes nothing.

    Mutation: drop the codex entry from `hook_install.HARNESS_LAYOUTS` —
    `get_layout("codex")` raises `unknown harness 'codex'`, so a stale
    installed hook is never flagged or repaired, and this REDs."""
    layout = hook_install.get_layout("codex")
    # The contract must be READABLE, not a particular generation: a literal
    # here (this asserted ``== 1`` until #4544) goes stale silently on every
    # deliberate install-contract bump — which is how this branch left two red
    # assertions behind. Readability still REDs on the mutation the docstring
    # names, and also if the marker is dropped or the layout's scripts disagree
    # (`contract_version` -> ``None``). The shipped GENERATIONS are pinned
    # deliberately, once, by `test_shipped_install_contract_generations`.
    assert hook_install.contract_version(layout) is not None, (
        "the shipped codex hook carries no readable install contract")

    res = install_capture("codex", home=home)
    assert res.ok, res.error
    codex_root = capture_install.codex_home(home)

    assert hook_install.detect_install(codex_root, "codex") == [], (
        "the installer produced state the drift detector calls drifted")
    upgrade = hook_install.upgrade_install(codex_root, "codex")
    assert upgrade.refused is None, upgrade.refused
    assert upgrade.actions == [], (
        f"upgrade was not a no-op on a fresh install: {upgrade.actions}")

    again = install_capture("codex", home=home)
    assert again.ok and again.changed is False, (
        "re-installing over a status-current install was not a clean no-op")


def test_codex_hooks_status_reports_the_install_as_current(cli):
    """`tortoise hooks status --harness codex` names the harness instead of
    rejecting it, and reads a fresh install as current.

    Mutation: remove the codex layout — the CLI exits 1 with `unknown harness
    'codex'` and this REDs."""
    run, _root, home = cli
    assert install_capture("codex", home=home).ok

    r = run("hooks", "status", "--harness", "codex",
            "--dir", str(capture_install.codex_home(home)))

    assert r.returncode == 0, r.stderr
    assert "unknown harness" not in (r.stdout + r.stderr), r.stderr
    assert "are current" in r.stdout, r.stdout


def test_codex_hooks_status_defaults_to_codex_home_not_the_cwd(cli):
    """With NO ``--dir``, `tortoise hooks status --harness codex` resolves its
    root from ``$CODEX_HOME`` (here ``$HOME/.codex``) — the only path Codex
    reads — not the cwd.

    Mutation: resolve the default root from the cwd (``--dir .``) → the check
    lands on a path with no install, reports ``missing-script`` +
    ``missing-hook-entry``, and exits 1 → this REDs."""
    run, root, home = cli
    assert install_capture("codex", home=home).ok
    codex_root = capture_install.codex_home(home)

    r = run("hooks", "status", "--harness", "codex")  # no --dir

    assert r.returncode == 0, r.stdout + r.stderr
    assert "are current" in r.stdout, r.stdout
    assert str(codex_root) in r.stdout, r.stdout
    # the dead project-local path Codex never reads was not inspected
    assert not (root / "hooks.json").exists()


def test_codex_hooks_upgrade_defaults_to_codex_home_not_the_cwd(cli):
    """With NO ``--dir``, `tortoise hooks upgrade --harness codex` writes into
    ``$CODEX_HOME`` — the script plus an ABSOLUTE registration — and leaves the
    dead project-local path untouched.

    Mutation: resolve the default root from the cwd (``--dir .``) → the
    upgrade writes ``<cwd>/hooks.json`` and ``<cwd>/hooks/`` with a RELATIVE
    command, prints ``upgraded.``, and the real ``$CODEX_HOME/hooks.json`` is
    never created → this REDs (the #3818 silent no-capture)."""
    run, root, home = cli
    codex_root = capture_install.codex_home(home)
    assert not codex_root.exists()

    r = run("hooks", "upgrade", "--harness", "codex")  # no --dir

    assert r.returncode == 0, r.stdout + r.stderr
    installed = codex_root / "hooks" / capture_install.CODEX_SCRIPT_NAME
    assert installed.is_file(), r.stdout + r.stderr
    assert _codex_commands(home) == [str(installed)], _codex_json(home)
    assert os.path.isabs(_codex_commands(home)[0])
    # nothing landed in the dead cwd path the old default wrote into
    assert not (root / "hooks.json").exists(), r.stdout
    assert not (root / "hooks").exists(), r.stdout


@pytest.mark.parametrize("hooks_cmd", ["status", "upgrade"])
@pytest.mark.parametrize(
    ("home", "expected"),
    [
        # A RELATIVE `$HOME`: `Path.home()` returns it VERBATIM, and
        # `default_root` refuses it as a `ValueError`.
        ("relhome", "cannot resolve an absolute install root"),
        # NOTE: an EMPTY `$HOME` is deliberately NOT in this matrix. It does
        # not reach the root resolver at all: `Path.home()` yields `/`, which
        # is absolute, so `default_root` returns `/.codex` and the refusal
        # comes LATER from the writability preflight (a DIFFERENT refusal with
        # a different message). Pinning it here would assert the wrong
        # mechanism — and on a machine that can write `/.codex` it would not
        # refuse at all. The same reasoning applies to an UNSET `$HOME`, which
        # falls back to the passwd entry and resolves normally.
        # A literal `~` / `~/x`: the expansion is a no-op, so `Path.home()`
        # ITSELF raises `RuntimeError("Could not determine home directory.")`.
        ("~", "Could not determine home directory."),
        ("~/x", "Could not determine home directory."),
    ],
)
def test_codex_hooks_refuses_an_unresolvable_home_as_a_populated_error(
        tmp_path, hooks_cmd, home, expected):
    """An unresolvable ``HOME`` leaves the capture root with no absolute base;
    the refusal must reach the CLI as a populated message plus a non-zero
    exit, exactly like every other refusal in this command — never an
    uncaught traceback.  ``_cmd_hooks`` is shared by BOTH ``hooks status``
    and ``hooks upgrade``, and it evaluated the root outside any try/except
    (#4024 P2-1).

    The matrix spans BOTH members of the raise-set, which is why the boundary
    is a catch-all rather than an enumeration:

    * ``relhome`` — ``Path.home()`` returns a NON-absolute value
      VERBATIM and ``default_root`` refuses it with a ``ValueError``.
    * ``~`` and ``~/x`` — ``Path.home()`` itself RAISES ``RuntimeError``:
      ``pathlib`` refuses when the ``~`` expansion is a no-op.  An earlier
      revision asserted "``Path.home()`` returns it verbatim — it does NOT
      raise", which is true of ``relhome`` and FALSE of ``~`` — and that
      false generalisation is exactly what let the ``RuntimeError`` escape an
      ``except ValueError`` boundary.

    Mutation: restore the enumerated ``except ValueError`` around the root
    resolution — the ``relhome`` cases stay green while the
    ``~``/``~/x`` cases RED with the raw ``RuntimeError`` traceback."""
    cwd = tmp_path / "proj"
    cwd.mkdir()
    env = {
        **os.environ,
        "HOME": home,
        "CODEX_HOME": "",
        "TORTOISE_DB_URI": "",
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
    }

    r = _run(("hooks", hooks_cmd, "--harness", "codex"), env, cwd)

    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr
    # a populated refusal, not a bare exit — with the repair path.
    assert expected in r.stderr, r.stderr
    assert "to repair" in r.stderr, r.stderr
    # the refusal is read-only: nothing was written into a relative root.
    assert not (cwd / "relhome").exists(), r.stdout + r.stderr


@pytest.mark.parametrize("hooks_cmd", ["status", "upgrade"])
@pytest.mark.parametrize("home", ["~", "~/x"])
def test_hooks_claude_also_survives_an_unresolvable_home(
        tmp_path, hooks_cmd, home):
    """``_P.home()`` is evaluated BEFORE ``default_root``'s early
    ``return Path('.')`` for the project-scoped Claude layout, so the raising
    ``HOME`` reaches ``--harness claude`` too and the SAME boundary must catch
    it.  A fix scoped to the codex arm, or placed around only the
    ``default_root`` call, leaves this RED.

    Mutation: evaluate ``_P.home()`` outside the guarded region, or guard the
    codex arm only — this prints the raw ``RuntimeError`` traceback."""
    cwd = tmp_path / "proj"
    cwd.mkdir()
    env = {
        **os.environ,
        "HOME": home,
        "CODEX_HOME": "",
        "TORTOISE_DB_URI": "",
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
    }

    r = _run(("hooks", hooks_cmd, "--harness", "claude"), env, cwd)

    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr
    assert "Could not determine home directory." in r.stderr, r.stderr
    assert "to repair" in r.stderr, r.stderr


@pytest.mark.parametrize(
    ("hooks_cmd", "currency"),
    [("status", "are current"), ("upgrade", "already current")],
)
@pytest.mark.parametrize("harness", ["codex", "claude", "cursor"])
@pytest.mark.parametrize("home", ["~", "~/x"])
def test_explicit_dir_keeps_an_unresolvable_home_irrelevant(
        tmp_path, harness, hooks_cmd, home, currency):
    """An explicit ``--dir`` makes ``HOME`` irrelevant, so an unresolvable
    ``HOME`` must NOT abort a ``hooks status`` / ``hooks upgrade`` that names a
    valid absolute install root.  The refusal in the sibling tests above comes
    from ``_P.home()`` RAISING; evaluating it ABOVE the ``explicit_dir``
    ternary (the 145260bb1 form) aborts a ``--dir`` inspect/repair that had no
    need to consult ``HOME`` at all, returning a false refusal (rc=1).  This
    test observes BOTH sides of that boundary: WITH ``--dir`` the command
    succeeds, WITHOUT ``--dir`` the SAME ``HOME`` refuses cleanly — so the
    happy path is meaningful rather than merely optimistic.

    The no-``--dir`` half duplicates the sibling coverage
    (``test_codex_hooks_refuses_an_unresolvable_home_as_a_populated_error`` and
    ``test_hooks_claude_also_survives_an_unresolvable_home``); it is asserted
    here too so the pair reads as one boundary rather than two disconnected
    tests.

    Claude's asymmetry is encoded, not fought: the Claude layout is
    project-scoped, so ``default_root`` ignores ``home`` and only the RAISING
    homes (``~``/``~/x``) — the matrix here — reach it; a RELATIVE ``HOME`` is
    not a Claude refusal by design and is deliberately NOT asserted.

    Mutation (VERIFIED RED): re-hoist ``home = _P.home()`` above the
    ``explicit_dir`` ternary — ``default_root(layout, home)`` then never
    receives an unraised ``HOME``, and every ``--dir`` case here REDs on
    ``returncode == 0`` with a ``RuntimeError`` refusal (the #4024 P2-1 gap:
    no test covered ``--dir`` plus an unresolvable ``HOME``)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    install_home = tmp_path / "installhome"
    install_home.mkdir()

    # A CURRENT seam at an ABSOLUTE dir, so the ``--dir`` invocation has
    # something valid to inspect (status: current; upgrade: nothing to do).
    if harness == "codex":
        assert install_capture("codex", home=install_home).ok
        abs_dir = capture_install.codex_home(install_home)
    elif harness == "cursor":
        # #3819: cursor is HOME-scoped too — the same boundary must hold.
        assert install_capture("cursor", home=install_home).ok
        abs_dir = capture_install.cursor_home(install_home)
    else:
        assert install_capture("claude", root=proj).ok
        abs_dir = proj

    env = {
        **os.environ,
        "HOME": home,
        "CODEX_HOME": "",
        "TORTOISE_DB_URI": "",
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
    }

    # WITH ``--dir``: HOME is irrelevant — the explicit absolute root is
    # valid, so the command succeeds and reports the normal currency sentence.
    with_dir = _run(
        ("hooks", hooks_cmd, "--harness", harness, "--dir", str(abs_dir)),
        env, proj)
    assert with_dir.returncode == 0, (
        with_dir.returncode, with_dir.stdout, with_dir.stderr)
    assert currency in with_dir.stdout, with_dir.stdout + with_dir.stderr
    assert "Traceback" not in with_dir.stderr, with_dir.stderr

    # WITHOUT ``--dir``: the same HOME refuses cleanly (populated message, no
    # traceback) — the other side of the boundary.
    without_dir = _run(("hooks", hooks_cmd, "--harness", harness), env, proj)
    assert without_dir.returncode != 0, (
        without_dir.returncode, without_dir.stdout, without_dir.stderr)
    assert "Traceback" not in without_dir.stderr, without_dir.stderr
    assert "Could not determine home directory." in without_dir.stderr, (
        without_dir.stderr)
    assert "to repair" in without_dir.stderr, without_dir.stderr


# ── cursor: the artifact + the ~/.cursor registration (#3819) ───────────


def _cursor_json(home: Path) -> dict:
    return json.loads((home / ".cursor" / "hooks.json").read_text())


def _cursor_entries(home: Path) -> list[dict]:
    return _cursor_json(home)["hooks"][capture_install.CURSOR_EVENT]


def _cursor_commands(home: Path) -> list[str]:
    return [e["command"] for e in _cursor_entries(home)
            if isinstance(e.get("command"), str)]


def _cursor_session_end_entries(*roots: Path) -> list[dict]:
    """Every ``sessionEnd`` capture entry under ``roots``, wherever the
    installer actually wrote it — so a mutation that resolves the root wrongly
    REDs on the assertion, not on a hard-coded path the test guessed."""
    entries: list[dict] = []
    for root in roots:
        if not root.exists():
            continue
        for hooks_json in sorted(root.rglob("hooks.json")):
            data = json.loads(hooks_json.read_text())
            entries.extend((data.get("hooks") or {}).get(
                capture_install.CURSOR_EVENT) or [])
    return entries


def _cursor_session_end_commands(*roots: Path) -> list[str]:
    return [e["command"] for e in _cursor_session_end_entries(*roots)
            if isinstance(e.get("command"), str)]


def test_cursor_install_writes_the_hook_and_merges_the_absolute_command(home):
    """The Cursor seam: the shipped hook into ``~/.cursor/hooks/`` (0755)
    and a ``sessionEnd`` registration in ``~/.cursor/hooks.json`` whose
    command is the script's ABSOLUTE path.

    Mutation: register a relative command (Cursor runs the hook from its own
    cwd, so it never resolves) — this REDs."""
    res = install_capture("cursor", home=home)

    assert res.ok, res.error
    installed = home / ".cursor" / "hooks" / capture_install.CURSOR_SCRIPT_NAME
    assert installed.read_bytes() == (
        _REPO_ROOT / "tortoise" / "cursor-hooks" / "session-end.sh").read_bytes()
    assert installed.stat().st_mode & stat.S_IXUSR
    assert _cursor_commands(home) == [str(installed)], _cursor_json(home)
    assert os.path.isabs(_cursor_commands(home)[0])


def test_cursor_entry_is_flat_not_nested(home):
    """Cursor's ``.cursor/hooks.json`` entry is a FLAT script object. Its own
    validator requires a string ``command`` on the entry itself; a nested
    ``{"hooks": [...]}`` group FAILS validation and invalidates the WHOLE
    file, so Cursor loads NO hooks — a silent no-capture.

    Mutation: emit the Claude/Codex nested matcher-group shape (drop
    ``flat_entry`` from the cursor layout) — this REDs."""
    assert install_capture("cursor", home=home).ok

    doc = _cursor_json(home)
    # Cursor's validator REQUIRES a positive-integer `version` on the
    # document.  Without it Cursor logs `Invalid user config: Config version
    # must be a number`, rejects the WHOLE file and loads NO hooks — the
    # install prints success and captures nothing (verified live, #3819).
    assert doc.get("version") == 1, doc
    entry = _cursor_entries(home)[0]
    assert isinstance(entry.get("command"), str), entry
    assert "hooks" not in entry, (
        f"a nested matcher group would be rejected by Cursor: {entry}")
    assert "matcher" not in entry, entry
    assert entry.get("type") in (None, "command"), entry


def test_cursor_install_writes_the_version_key_cursor_requires(home):
    """Cursor's `hooks.json` needs a positive-integer `version`; without it the
    WHOLE file is rejected and no hook fires.  `install` must emit it and
    `hooks status` must report a MISSING one as blocking drift.

    Mutation: drop the `version` set in `_merge_capture_hooks` — the fresh
    install has no version and this REDs; drop the version finding in
    `_settings_findings` — the stale-install half REDs."""
    assert install_capture("cursor", home=home).ok
    root = capture_install.cursor_home(home)
    assert _cursor_json(home)["version"] == 1
    assert hook_install.detect_install(root, "cursor") == []

    # A file missing `version` (e.g. one an earlier buggy install wrote) is
    # flagged blocking and repaired by `upgrade`.
    doc = _cursor_json(home)
    del doc["version"]
    (home / ".cursor" / "hooks.json").write_text(json.dumps(doc))
    findings = hook_install.detect_install(root, "cursor")
    assert any(f.kind == "settings-invalid-version" and f.blocking
               for f in findings), findings

    result = hook_install.upgrade_install(root, "cursor")
    assert result.ok, result.refused
    assert _cursor_json(home)["version"] == 1
    assert hook_install.detect_install(root, "cursor") == []


def test_cursor_install_refuses_a_nested_session_end_entry(home):
    """A nested matcher group under a flat event makes Cursor reject the WHOLE
    file, so the merge must REFUSE rather than append a flat duplicate beside
    it (the mixed list is the invalid shape).

    Mutation: drop the `_flat_entry_is_harness_valid` guard in
    `_merge_capture_hooks` — the installer appends beside the nested entry,
    prints success, and this REDs."""
    script = home / ".cursor" / "hooks" / capture_install.CURSOR_SCRIPT_NAME
    (home / ".cursor").mkdir(parents=True)
    (home / ".cursor" / "hooks.json").write_text(json.dumps({
        "version": 1,
        "hooks": {capture_install.CURSOR_EVENT: [{"hooks": [{
            "type": "command", "command": str(script)}]}]},
    }))

    res = install_capture("cursor", home=home)

    assert not res.ok, res.actions
    assert "Cursor" in res.error and "manual" in res.error, res.error


def test_cursor_install_refuses_a_document_cursor_would_reject(home):
    """Cursor's validator iterates EVERY event and rejects the WHOLE file on an
    unknown step, a non-list event, or any unparseable entry — including under
    an event we do not merge.  The refusal must be whole-document, not scoped
    to ``sessionEnd``.

    Mutation: check only ``spec.event`` (the nested-entry-only guard) — an
    invalid entry under another event installs "successfully" and this REDs."""
    (home / ".cursor").mkdir(parents=True)
    path = home / ".cursor" / "hooks.json"
    # (a) an unparseable entry under a DIFFERENT event
    path.write_text(json.dumps({"version": 1, "hooks": {
        "afterFileEdit": [{"hooks": [{"type": "command", "command": "/x"}]}],
    }}))
    res = install_capture("cursor", home=home)
    assert not res.ok, res.actions
    assert "afterFileEdit" in res.error, res.error

    # (b) an unknown event key
    path.write_text(json.dumps({"version": 1, "hooks": {
        "notARealStep": [{"command": "/bin/other"}],
    }}))
    res = install_capture("cursor", home=home)
    assert not res.ok, res.actions
    assert "unknown hook type" in res.error, res.error

    # (c) a field Cursor's validator rejects (a non-numeric timeout)
    path.write_text(json.dumps({"version": 1, "hooks": {
        "sessionEnd": [{"command": "/bin/other", "timeout": "30s"}],
    }}))
    res = install_capture("cursor", home=home)
    assert not res.ok, res.actions
    assert "sessionEnd" in res.error, res.error


def test_cursor_hooks_status_calls_a_malformed_entry_a_manual_fix(home):
    """`hooks status` must NOT recommend `upgrade` for a malformed flat entry —
    `upgrade` refuses on it, so the hint would point at a command that refuses.
    `settings-unreadable-entry` belongs in the manual-fix set.

    Mutation: drop `settings-unreadable-entry` from the `_manual` frozenset —
    status recommends `hooks upgrade` and this REDs."""
    (home / ".cursor").mkdir(parents=True)
    (home / ".cursor" / "hooks.json").write_text(json.dumps(
        {"version": 1, "hooks": {capture_install.CURSOR_EVENT: [
            {"hooks": [{"type": "command", "command": "/x"}]}]}}))

    r = _run(("hooks", "status", "--harness", "cursor",
              "--dir", str(capture_install.cursor_home(home))),
             {**os.environ, "HOME": str(home), "TORTOISE_DB_URI": "",
              "TORTOISE_SECRET_PEPPER": "test-static-pepper"}, home)

    assert r.returncode != 0, r.stdout
    assert "manual fix" in r.stdout, r.stdout
    assert "hooks upgrade" not in r.stdout, r.stdout


def test_cursor_flat_validator_matches_cursor_null_and_float_semantics():
    """Cursor's validator uses presence (``e.field !== void 0``) then
    ``typeof``, so an explicit JSON ``null`` on matcher/timeout/failClosed/
    model is REJECTED, while ``loop_limit: null`` and an integral ``version``
    (``1.0`` — ``Number.isInteger(1.0)`` is true) are valid.

    Mutation: test with ``is not None`` (conflating JSON null with absent) —
    the null cases are accepted and this REDs; test version with
    ``isinstance(int)`` — ``1.0`` is rejected and this REDs."""
    from tortoise.hook_install import (  # noqa: I001
        _flat_entry_is_harness_valid, _is_positive_int_value)
    assert _flat_entry_is_harness_valid({"command": "/bin/other"})
    assert _flat_entry_is_harness_valid({"command": "/bin/other",
                                         "timeout": 30})
    assert _flat_entry_is_harness_valid({"command": "/bin/other",
                                         "loop_limit": None})
    assert _flat_entry_is_harness_valid({"type": "prompt", "prompt": "hi"})
    for bad in (
        {"command": "/bin/other", "timeout": None},
        {"command": "/bin/other", "matcher": None},
        {"command": "/bin/other", "failClosed": None},
        {"type": "prompt", "prompt": "hi", "model": None},
        {"command": "/bin/other", "timeout": "30s"},
        {"command": "/bin/other", "type": None},
    ):
        assert not _flat_entry_is_harness_valid(bad), bad
    assert _is_positive_int_value(1)
    assert _is_positive_int_value(1.0)
    assert not _is_positive_int_value(0)
    assert not _is_positive_int_value(True)
    assert not _is_positive_int_value("1")


def test_cursor_flat_validator_regex_and_loop_limit_semantics():
    """The matcher is judged only in the SAFE direction: a JS-only-valid
    matcher Python rejects must be ACCEPTED (a valid Cursor config must still
    install), a Python-only construct JS rejects must be REFUSED, a matcher
    BOTH engines reject must be REFUSED, an integral float ``loop_limit`` is
    valid, and a matcher Python cannot compile must not escape as a traceback.

    Mutation: drop the Python-only denylist — ``(?i)a`` is accepted and this
    REDs; refuse on any Python ``re.error`` — the JS-only matcher is refused
    and this REDs; accept without the JS-only marker check — ``(`` is accepted
    and this REDs; use ``isinstance(int)`` for loop_limit — ``2.0`` is refused
    and this REDs."""
    from tortoise.hook_install import _flat_entry_is_harness_valid as ok
    # JS-only but valid: named groups and a Unicode property escape
    assert ok({"command": "/x", "matcher": "(?<name>a)"})
    assert ok({"command": "/x", "matcher": "\\p{L}+"})
    # an escaped quantifier is valid in BOTH engines
    assert ok({"command": "/x", "matcher": "\\++"})
    # Python-only: JS `new RegExp` throws on each of these
    for bad in ("(?i)a", "(?>a)", "a*+", "(?P<n>a)", "(?-i:a)"):
        assert not ok({"command": "/x", "matcher": bad}), bad
    # a matcher BOTH engines reject, with no JS-only marker, is refused
    for bad in ("(", "[", "a**"):
        assert not ok({"command": "/x", "matcher": bad}), bad
    # Cursor's own wildcard sentinel is always valid
    assert ok({"command": "/x", "matcher": "*"})
    # an integral float loop_limit is valid (JS Number.isInteger(2.0))
    assert ok({"command": "/x", "loop_limit": 2.0})
    assert not ok({"command": "/x", "loop_limit": 0})
    # deeply nested groups must not escape as a traceback (they refuse)
    assert ok({"command": "/x", "matcher": "(" * 600 + ")" * 600}) is False


def test_cursor_upgrade_refuses_structure_even_when_version_is_bad(home):
    """A document with BOTH a bad version and a structural defect is a MANUAL
    fix: `upgrade` must REFUSE, not repair the version and leave a file Cursor
    still rejects.

    Mutation: report the version finding and skip the structural one (the
    pre-fix ordering) — `upgrade` sets version=1, appends its entry, returns
    ok, and this REDs."""
    (home / ".cursor").mkdir(parents=True)
    (home / ".cursor" / "hooks.json").write_text(json.dumps(
        {"hooks": {capture_install.CURSOR_EVENT: [
            {"hooks": [{"type": "command", "command": "/x"}]}]}}))
    root = capture_install.cursor_home(home)

    kinds = {f.kind for f in hook_install.detect_install(root, "cursor")}
    assert "settings-unreadable-entry" in kinds, kinds
    assert "settings-invalid-version" in kinds, kinds
    result = hook_install.upgrade_install(root, "cursor")
    assert result.refused is not None, result.actions


def test_cursor_version_1_0_is_valid_and_not_rewritten(home):
    """JS ``Number.isInteger(1.0)`` is true, so a user's ``"version": 1.0``
    is valid; the installer must not report drift or rewrite it.

    Mutation: use ``isinstance(version, int)`` — 1.0 reads as invalid, is
    flagged/rewritten, and this REDs."""
    (home / ".cursor").mkdir(parents=True)
    (home / ".cursor" / "hooks.json").write_text(json.dumps(
        {"version": 1.0, "hooks": {capture_install.CURSOR_EVENT: []}}))
    root = capture_install.cursor_home(home)

    findings = [f for f in hook_install.detect_install(root, "cursor")
                if f.kind == "settings-invalid-version"]
    assert findings == [], findings
    assert install_capture("cursor", home=home).ok
    assert _cursor_json(home)["version"] == 1.0


def test_cursor_root_is_home_scoped_and_ignores_an_unrelated_env(
        tmp_path, monkeypatch):
    """Cursor resolves ``~/.cursor/hooks.json`` and has NO config-dir env var —
    verified: the string ``CURSOR_HOME`` appears nowhere in Cursor 3.20.21's JS
    bundle, asar or binary (`CursorHooksService` joins
    ``pathService.userHome() / ".cursor"``).  The install must land in
    ``$HOME/.cursor`` regardless of any ambient ``CURSOR_HOME``, or it writes a
    file Cursor never reads — the silent no-capture class this seam exists to
    prevent.

    Mutation: declare ``root_env="CURSOR_HOME"`` on the cursor layout —
    setting it redirects the install and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CURSOR_HOME", str(tmp_path / "elsewhere"))
    layout = hook_install.get_layout("cursor")

    assert layout.root_env is None, "Cursor has no config-dir env var"
    assert hook_install.default_root(layout, home) == home / ".cursor"
    assert install_capture("cursor", home=home).ok
    assert (home / ".cursor" / "hooks.json").is_file()
    assert not (tmp_path / "elsewhere").exists(), (
        "an ambient CURSOR_HOME redirected the install away from ~/.cursor")


def test_cursor_default_root_is_absolute_or_refuses(tmp_path, monkeypatch):
    """The resolver always returns an ABSOLUTE root — the layout's
    ``absolute_command`` invariant cannot hold otherwise.  A RELATIVE ``$HOME``
    (``Path.home()`` returns it verbatim) makes the root relative, so it must
    REFUSE loudly rather than register a command Cursor resolves somewhere
    unknowable.

    Mutation: return ``home / root_home_default`` without the absoluteness
    guard — a relative home yields a relative root and this REDs."""
    layout = hook_install.get_layout("cursor")
    assert hook_install.default_root(layout, tmp_path / "home").is_absolute()
    with pytest.raises(ValueError, match="absolute"):
        hook_install.default_root(layout, Path("relhome"))


def test_cursor_install_registers_an_absolute_command_and_status_clean(
        tmp_path, monkeypatch):
    """An install registers the script's ABSOLUTE path, and ``status``
    recognizes that registration in the same run.

    Mutation: register a relative command — the registered path is not
    absolute and ``status`` reports ``settings-stale-command`` → this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    resolved = home / ".cursor"

    assert install_capture("cursor", home=home).ok

    registered = _cursor_commands(home)
    assert registered == [
        str(resolved / "hooks" / capture_install.CURSOR_SCRIPT_NAME)], registered
    assert os.path.isabs(registered[0]), registered
    assert hook_install.detect_install(resolved, "cursor") == [], (
        "the installer produced state the drift detector calls drifted")


def test_cursor_reinstall_does_not_duplicate(tmp_path, monkeypatch):
    """Installing twice leaves ONE ``sessionEnd`` registration.

    Mutation: the detector does not recognize our own registration, so the
    second install appends a duplicate → this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    for _ in range(2):
        assert install_capture("cursor", home=home).ok

    assert len(_cursor_entries(home)) == 1, _cursor_json(home)


# The `--dir`-vs-unresolvable-HOME boundary is covered for cursor by
# `test_explicit_dir_keeps_an_unresolvable_home_irrelevant` (harness matrix
# includes "cursor"), not duplicated here.


def test_cursor_install_is_home_scoped_not_project_scoped(tmp_path):
    """Cursor reads hook registrations from the HOME-scoped ``.cursor/
    hooks.json`` (verified against Cursor 3.20.21's bundle); a project-local
    ``<repo>/.cursor/hooks.json`` is gated on workspace trust and fires
    nothing when untrusted. The installer must write HOME-scoped even when a
    project ``root`` is supplied.

    Mutation: write the registration into ``root/.cursor/hooks.json`` — the
    install reports success while Cursor never reads it, and this REDs."""
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    home.mkdir()
    proj.mkdir()

    res = install_capture("cursor", root=proj, home=home)

    assert res.ok, res.error
    assert (home / ".cursor" / "hooks.json").is_file()
    assert not (proj / ".cursor").exists(), (
        "the capture seam must not be written to the untrusted project path")


def test_cursor_install_is_a_clean_no_op_on_rerun(home):
    """Re-running is the upgrade path and must be a byte-level no-op.

    Mutation: append the registration unconditionally (a second run emits a
    duplicate ``sessionEnd`` entry)."""
    assert install_capture("cursor", home=home).ok
    json_path = home / ".cursor" / "hooks.json"
    script = home / ".cursor" / "hooks" / capture_install.CURSOR_SCRIPT_NAME
    before = (json_path.stat().st_mtime_ns, script.stat().st_mtime_ns)

    again = install_capture("cursor", home=home)

    assert again.ok and again.changed is False, again.actions
    assert (json_path.stat().st_mtime_ns, script.stat().st_mtime_ns) == before
    assert len(_cursor_commands(home)) == 1, _cursor_json(home)


def test_cursor_install_preserves_foreign_keys_events_and_hooks(home):
    """Merge, never overwrite: an unrelated top-level key, another event, a
    foreign hook in the SAME ``sessionEnd`` list, and the required ``version``
    key all survive.

    Mutation: write the document wholesale (the foreign hook is lost)."""
    doc = {
        "version": 1,
        "editor": "cursor",
        "hooks": {
            "beforeSubmitPrompt": [{"command": "/bin/other"}],
            "sessionEnd": [{"command": "/bin/other-end"}],
        },
    }
    path = home / ".cursor" / "hooks.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(doc))

    assert install_capture("cursor", home=home).ok

    merged = _cursor_json(home)
    assert merged["version"] == 1
    assert merged["editor"] == "cursor"
    assert merged["hooks"]["beforeSubmitPrompt"][0]["command"] == "/bin/other"
    assert "/bin/other-end" in _cursor_commands(home)
    assert len(_cursor_commands(home)) == 2, merged


def test_cursor_install_repairs_a_stale_relative_command_in_place(home):
    """A registration of ours whose command is relative/stale must be repaired
    to the absolute path, not left as a silent no-capture.

    Mutation: skip the repair loop in ``merge_cursor_capture_hooks`` — the
    stale command survives and this REDs."""
    script = home / ".cursor" / "hooks" / capture_install.CURSOR_SCRIPT_NAME
    script.parent.mkdir(parents=True)
    script.write_bytes((_REPO_ROOT / "tortoise" / "cursor-hooks"
                        / "session-end.sh").read_bytes())
    (home / ".cursor" / "hooks.json").write_text(json.dumps({"version": 1,
        "hooks": {capture_install.CURSOR_EVENT: [{
            "command": f"hooks/{capture_install.CURSOR_SCRIPT_NAME}"}]}}))

    assert install_capture("cursor", home=home).ok

    assert _cursor_commands(home) == [str(script)], _cursor_json(home)


def test_cursor_install_refuses_a_foreign_script_and_keeps_it(home):
    """A foreign file at OUR script path is another product's — refuse whole,
    never clobber.

    Mutation: write through the foreign file (it is destroyed while the
    install reports success)."""
    script = home / ".cursor" / "hooks" / capture_install.CURSOR_SCRIPT_NAME
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n# someone else's hook\nexit 0\n")

    res = install_capture("cursor", home=home)

    assert not res.ok and "does not look like a Tortoise artifact" in res.error
    assert "someone else's hook" in script.read_text()


def test_cursor_install_preserves_a_differing_ours_script_as_bak(home):
    """A DIFFERING copy that DOES look like ours (a stale/edited Tortoise
    hook) is preserved as ``.bak`` before the shipped bytes replace it —
    never destroyed silently.

    Mutation: skip the backup in ``_install_script`` — this REDs."""
    script = home / ".cursor" / "hooks" / capture_install.CURSOR_SCRIPT_NAME
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\n# tortoise-hook-version: 0\n"
                      "# a local Tortoise edit\nexit 0\n")

    assert install_capture("cursor", home=home).ok

    backup = script.with_suffix(script.suffix + ".bak")
    assert backup.is_file(), "the differing ours-like copy was not preserved"
    assert "a local Tortoise edit" in backup.read_text()
    assert script.read_bytes() == (
        _REPO_ROOT / "tortoise" / "cursor-hooks" / "session-end.sh").read_bytes()


def test_cursor_install_refuses_invalid_hooks_json(home):
    """An unparsable ``hooks.json`` must never be clobbered.

    Mutation: fall back to ``{}`` on a parse error — the user's file is
    destroyed and this REDs."""
    path = home / ".cursor" / "hooks.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")

    res = install_capture("cursor", home=home)

    assert not res.ok and "not valid JSON" in res.error
    assert path.read_text() == "{ not json"


def test_cursor_install_refuses_non_utf8_hooks_json(home):
    """A non-UTF-8 ``hooks.json`` must be refused, never silently rewritten
    (decoding with ``errors="replace"`` would corrupt the user's bytes on the
    merge write-back).

    Mutation: read with ``errors="replace"`` — the file is rewritten and this
    REDs."""
    path = home / ".cursor" / "hooks.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"hooks": {"sessionEnd": [\xff\xfe]}}')

    res = install_capture("cursor", home=home)

    assert not res.ok and "not valid UTF-8" in res.error
    assert path.read_bytes() == b'{"hooks": {"sessionEnd": [\xff\xfe]}}'


def test_cursor_install_refuses_a_symlinked_intermediate_dir(home):
    """A symlinked intermediate directory (``.cursor/hooks`` -> an in-root
    dir) cannot be replaced by a regular file and is not an install
    ``hooks status`` reads as current (`upgrade` refuses it) — so the install
    must refuse it too, not write through and print success.

    Mutation: accept the symlinked ``hooks/`` dir (the install writes through
    it and reports success while ``status`` reports ``symlinked-install``) —
    this REDs."""
    real = home / ".cursor" / "real-hooks"
    real.mkdir(parents=True)
    (home / ".cursor" / "hooks").symlink_to(real, target_is_directory=True)

    res = install_capture("cursor", home=home)

    assert not res.ok, res.actions
    assert "symbolic link" in res.error or "symlink" in res.error.lower()
    assert not (real / capture_install.CURSOR_SCRIPT_NAME).exists()


def test_cursor_dry_run_writes_nothing(tmp_path):
    """``--dry-run`` is write-free.

    Mutation: drop the ``dry_run`` gate on the script/settings writes — files
    appear on disk and this REDs."""
    home = tmp_path / "home"
    home.mkdir()

    res = install_capture("cursor", home=home, dry_run=True)

    assert res.ok, res.error
    assert res.changed is True
    assert not (home / ".cursor").exists()


def test_cursor_install_then_status_is_clean_and_upgrade_is_a_no_op(home):
    """The Cursor seam ships the SAME version-marker/settings contract as
    Claude/Codex, so it is covered by the layout registry — not an
    install-only fork: `detect_install` reads the produced state as current
    and `upgrade_install` changes nothing.

    Mutation: drop the cursor entry from `hook_install.HARNESS_LAYOUTS` —
    `get_layout("cursor")` raises `unknown harness 'cursor'`, so a stale
    installed hook is never flagged or repaired, and this REDs."""
    layout = hook_install.get_layout("cursor")
    # Readable, not a literal generation — same reasoning as the codex seam
    # above (#4544: cursor was bumped 1 -> 2 by #4314). The generations are
    # pinned deliberately by `test_shipped_install_contract_generations`.
    assert hook_install.contract_version(layout) is not None, (
        "the shipped cursor hook carries no readable install contract")

    res = install_capture("cursor", home=home)
    assert res.ok, res.error
    cursor_root = capture_install.cursor_home(home)

    assert hook_install.detect_install(cursor_root, "cursor") == [], (
        "the installer produced state the drift detector calls drifted")
    upgrade = hook_install.upgrade_install(cursor_root, "cursor")
    assert upgrade.refused is None, upgrade.refused
    assert upgrade.actions == [], (
        f"upgrade was not a no-op on a fresh install: {upgrade.actions}")

    again = install_capture("cursor", home=home)
    assert again.ok and again.changed is False, (
        "re-installing over a status-current install was not a clean no-op")


def test_cursor_hooks_status_reports_the_install_as_current(cli):
    """`tortoise hooks status --harness cursor` names the harness instead of
    rejecting it, and reads a fresh install as current.

    Mutation: remove the cursor layout — the CLI exits 1 with `unknown harness
    'cursor'` and this REDs."""
    run, _root, home = cli
    assert install_capture("cursor", home=home).ok

    r = run("hooks", "status", "--harness", "cursor",
            "--dir", str(capture_install.cursor_home(home)))

    assert r.returncode == 0, r.stderr
    assert "unknown harness" not in (r.stdout + r.stderr), r.stderr
    assert "are current" in r.stdout, r.stdout


def test_cursor_hooks_status_defaults_to_cursor_home_not_the_cwd(cli):
    """With NO ``--dir``, `tortoise hooks status --harness cursor` resolves its
    root from ``~/.cursor`` (``$HOME/.cursor``) — the only path Cursor reads
    — not the cwd.

    Mutation: resolve the default root from the cwd (``--dir .``) → the check
    lands on a path with no install, reports ``missing-script`` +
    ``missing-hook-entry``, and exits 1 → this REDs."""
    run, root, home = cli
    assert install_capture("cursor", home=home).ok
    cursor_root = capture_install.cursor_home(home)

    r = run("hooks", "status", "--harness", "cursor")  # no --dir

    assert r.returncode == 0, r.stdout + r.stderr
    assert "are current" in r.stdout, r.stdout
    assert str(cursor_root) in r.stdout, r.stdout
    # the untrusted project-local path Cursor never reads was not inspected
    assert not (root / "hooks.json").exists()


def test_cursor_hooks_upgrade_defaults_to_cursor_home_not_the_cwd(cli):
    """With NO ``--dir``, `tortoise hooks upgrade --harness cursor` writes into
    ``~/.cursor`` — the script plus an ABSOLUTE registration — and leaves the
    project path untouched.

    Mutation: resolve the default root from the cwd (``--dir .``) → the
    upgrade writes ``<cwd>/hooks.json`` and ``<cwd>/hooks/`` with a RELATIVE
    command, prints ``upgraded.``, and the real ``~/.cursor/hooks.json`` is
    never created → this REDs (the #3819 silent no-capture)."""
    run, root, home = cli
    cursor_root = capture_install.cursor_home(home)
    assert not cursor_root.exists()

    r = run("hooks", "upgrade", "--harness", "cursor")  # no --dir

    assert r.returncode == 0, r.stdout + r.stderr
    installed = cursor_root / "hooks" / capture_install.CURSOR_SCRIPT_NAME
    assert installed.is_file(), r.stdout + r.stderr
    assert _cursor_commands(home) == [str(installed)], _cursor_json(home)
    assert os.path.isabs(_cursor_commands(home)[0])
    # nothing landed in the project path the old default would have written
    assert not (root / "hooks.json").exists(), r.stdout
    assert not (root / "hooks").exists(), r.stdout


# The `--dir`-vs-unresolvable-HOME boundary is covered for cursor by
# `test_explicit_dir_keeps_an_unresolvable_home_irrelevant` (harness matrix
# includes "cursor"), not duplicated here.


# ── the CLI surface (`tortoise install <harness>`) ──────────────────────


def test_cli_install_codex_installs_capture_into_the_codex_home(tmp_path):
    """The CLI installs BOTH codex halves: the per-turn read hook in the
    project and the capture seam in ``$CODEX_HOME``.

    Mutation: leave codex out of the capture dispatch in
    ``_cmd_install_hooks`` — no ``SessionEnd`` registration, this REDs."""
    root = tmp_path / "proj"
    home = tmp_path / "home"
    codex_home = tmp_path / "codex"
    root.mkdir()
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "TORTOISE_DB_URI": "",
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
    }

    r = _run(("install", "codex", "--dir", str(root)), env, root)

    assert r.returncode == 0, r.stderr
    assert (codex_home / "hooks" / capture_install.CODEX_SCRIPT_NAME).is_file()
    reg = json.loads((codex_home / "hooks.json").read_text())
    assert reg["hooks"][capture_install.CODEX_EVENT]
    # the read half still lands in the project
    read = json.loads((root / ".codex" / "hooks.json").read_text())
    assert "UserPromptSubmit" in read["hooks"]

    # P1-3: the trust guidance must name the ACTUAL effective capture file
    # ($CODEX_HOME/hooks.json), not the dead project-local one — following the
    # wrong hint leaves the HOME-scoped hook untrusted and captures nothing.
    assert str(codex_home / "hooks.json") in r.stdout, r.stdout
    assert "$CODEX_HOME/hooks.json" in r.stdout, r.stdout
    assert "TRUST" in r.stdout, r.stdout


def test_cli_install_cursor_installs_capture_and_discloses_the_ide_only_limit(
        tmp_path):
    """`tortoise install cursor` installs the HOME-scoped capture seam and
    DISCLOSES the IDE-only limitation on the install surface (owner ruling,
    #3819) — never a buried footnote.

    Mutation: drop cursor from the capture dispatch in `_cmd_install_hooks` —
    no `sessionEnd` registration lands and this REDs."""
    root = tmp_path / "proj"
    home = tmp_path / "home"
    cursor_home = home / ".cursor"
    root.mkdir()
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "TORTOISE_DB_URI": "",
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
    }

    r = _run(("install", "cursor", "--dir", str(root)), env, root)

    assert r.returncode == 0, r.stdout + r.stderr
    assert (cursor_home / "hooks"
            / capture_install.CURSOR_SCRIPT_NAME).is_file()
    reg = json.loads((cursor_home / "hooks.json").read_text())
    entry = reg["hooks"][capture_install.CURSOR_EVENT][0]
    assert entry["command"].endswith(capture_install.CURSOR_SCRIPT_NAME)
    assert "hooks" not in entry, "the entry must be flat (#3819)"
    # cursor has no read seam — nothing may be written to a project cline path
    assert not (root / ".cline").exists(), r.stdout
    # the IDE-only disclosure is on the install surface
    assert "IDE-ONLY" in r.stdout, r.stdout
    assert "CLOUD" in r.stdout.upper(), r.stdout
    assert "no editor-lifetime session boundary" in r.stdout, r.stdout


def test_cli_install_cursor_uninstall_says_the_seam_remains(tmp_path):
    """`tortoise install cursor --uninstall` must NOT route to the read-hook
    surface (which exits 1 with "has no shell-hook read seam") while the
    capture hook stays live — Cursor has no read seam, so the honest answer is
    a note naming what to delete (#3819).

    Mutation: drop the cursor uninstall branch — the command exits 1 with the
    read-hook refusal and this REDs."""
    root = tmp_path / "proj"
    home = tmp_path / "home"
    root.mkdir()
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "TORTOISE_DB_URI": "",
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
    }
    assert _run(("install", "cursor", "--dir", str(root)), env, root).returncode == 0

    r = _run(("install", "cursor", "--uninstall", "--dir", str(root)),
             env, root)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "capture seam is left in place" in r.stdout, r.stdout
    assert "tortoise-session-end.sh" in r.stdout, r.stdout
    # it never inspected/rewrote a cline registration as if it were cursor's
    assert not (root / ".cline").exists(), r.stdout


def test_cli_install_codex_second_run_is_a_no_op(cli):
    """A second run reports the no-op instead of re-installing.

    Mutation: reopen the settings write unconditionally — the CLI prints the
    install line again and this REDs."""
    run, root, _home = cli
    run("install", "codex", "--dir", str(root))

    r = run("install", "codex", "--dir", str(root))

    assert r.returncode == 0, r.stderr
    assert "already installed" in r.stdout, r.stdout


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


def test_cli_install_pi_dry_run_refuses_an_impossible_extensions_dir(cli):
    """`--dry-run` over an impossible install must refuse with a non-zero
    exit exactly as the real run does (#3808 R25).

    The write-free pre-flight probe (`_preflight_probe`) is what makes the two
    agree; only the `mkdir` is skipped when dry.  A file at
    ``~/.pi/agent/extensions`` is un-installable, so the dry run must not
    print ``[dry-run] would install …`` and exit 0 while the real run exits 1.

    Mutation: gate the pi probe behind ``if not dry_run`` — the dry run exits
    0 with the would-install line and this REDs on returncode."""
    run, _root, home = cli
    (home / ".pi" / "agent").mkdir(parents=True)
    blocker = home / ".pi" / "agent" / "extensions"
    blocker.write_text("")

    r = run("install", "pi", "--dry-run")

    assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
    assert "Capture install FAILED" in r.stderr, r.stderr
    assert "would install" not in r.stdout, r.stdout
    assert blocker.read_text() == "", "the dry run touched the blocker"


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


def test_cli_install_claude_dry_run_refuses_an_impossible_hooks_dir(cli):
    """`--dry-run` over an impossible install must refuse with a non-zero exit
    exactly as the real run does — never print ``[dry-run] would install …``
    and exit 0 while the real run refuses (#3808 R25).

    ``_preflight_probe`` is the write-free half of the pre-flight (the `mkdir`
    is the other half and stays behind ``not dry_run``); running it on both
    paths is what makes the halves agree.  The read half's own probe
    (``_read_hook_refusal``) has always run its real checks, so a dry run that
    skipped the capture check contradicted it.

    Mutation: gate the claude probe behind ``if not dry_run`` — the dry run
    exits 0 with the would-install line and this REDs on returncode."""
    run, root, _home = cli
    hooks = root / ".claude" / "hooks"
    hooks.parent.mkdir(parents=True)
    hooks.write_text("not a directory\n")

    dry = run("install", "claude", "--dir", str(root), "--dry-run")

    assert dry.returncode != 0, (dry.returncode, dry.stdout, dry.stderr)
    assert "Capture install FAILED" in dry.stderr, dry.stderr
    assert "would install" not in dry.stdout, dry.stdout

    # ...and the REAL run refuses for the same reason: the halves agree.
    real = run("install", "claude", "--dir", str(root))
    assert real.returncode != 0, (real.returncode, real.stdout, real.stderr)

    assert hooks.read_text() == "not a directory\n"
    assert not (root / ".claude" / "settings.json").exists()


def test_cli_install_claude_read_half_refusal_writes_nothing(cli):
    """A malformed read-half registration (``UserPromptSubmit: {}``) must fail
    the WHOLE install with NOTHING written — not leave the capture scripts and
    their SessionStart/SessionEnd entries on disk while exiting 1.

    Mutation: drop the ``_read_hook_refusal`` preflight in
    ``_cmd_install_hooks`` — capture runs first, so the command exits 1 with
    the capture seam already installed and this REDs on the not-exists
    assertions."""
    run, root, _home = cli
    settings = root / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": {}}}))

    r = run("install", "claude")

    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "UserPromptSubmit" in r.stderr, r.stderr
    # Nothing of EITHER half landed...
    assert not (root / ".claude" / "hooks").exists(), (
        "the capture scripts were written before the read half refused")
    # ...and the malformed file is exactly as the user left it.
    assert json.loads(settings.read_text()) == {
        "hooks": {"UserPromptSubmit": {}}}


def test_cli_install_codex_directory_hooks_json_is_a_populated_error(cli):
    """A directory at ``<root>/.codex/hooks.json`` must be a populated error,
    never an uncaught ``IsADirectoryError`` traceback.

    Mutation: drop the ``(OSError, RuntimeError)`` boundary in
    ``_install_read_hook`` — the CLI prints a traceback with
    ``IsADirectoryError`` and no ``Install failed`` line."""
    run, root, _home = cli
    (root / ".codex" / "hooks.json").mkdir(parents=True)

    r = run("install", "codex")

    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr
    assert "Install failed" in r.stderr, r.stderr
    assert "IsADirectoryError" in r.stderr, r.stderr
    # #3818: the read half is validated before the capture half writes, so a
    # refused install leaves NOTHING on disk — no capture seam in $CODEX_HOME.
    assert not (_home / ".codex").exists(), (
        "a read-half refusal must not leave the capture seam behind")


def test_cli_install_codex_symlink_loop_is_a_populated_error(cli):
    """A ``.codex`` symlink loop must be a populated error, never an uncaught
    ``RuntimeError`` traceback (``Path.resolve()`` raises on a cycle).

    Mutation: drop the ``(OSError, RuntimeError)`` boundary — a traceback
    escapes instead of ``Install failed``."""
    run, root, _home = cli
    (root / ".codex").symlink_to(".codex")  # self-loop

    r = run("install", "codex")

    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr
    assert "Install failed" in r.stderr, r.stderr


def test_cli_install_codex_non_ascii_registration_under_ascii_locale(tmp_path):
    """A UTF-8 registration file with one non-ASCII byte must not crash the
    install on a host whose ``locale.getencoding()`` is ASCII (#3808 R24).

    The byte-identical no-op guard re-read the target with NO explicit
    encoding, so the locale default applied and the UTF-8 bytes raised
    ``UnicodeDecodeError`` — a ``ValueError``, which the read half's
    ``(OSError, RuntimeError)`` boundary does not catch — so the CLI died
    with a traceback and exit 1.

    Mutation: drop ``encoding="utf-8"`` from that read (the default encoding
    decodes the file as ASCII and the run ends in ``Traceback``)."""
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "proj"
    (root / ".codex").mkdir(parents=True)
    target = root / ".codex" / "hooks.json"
    target.write_bytes('{"hooks": {},"note": "caf\u00e9"}'.encode("utf-8"))
    env = {
        **os.environ,
        "HOME": str(home),
        "TORTOISE_DB_URI": "",
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
        # The canonical ASCII-locale recipe: coercion OFF, UTF-8 mode OFF.
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONUTF8": "0",
        "PYTHONCOERCECLOCALE": "0",
    }
    # The bug EXISTS only where the default text encoding is ASCII, so prove
    # the child really is in that state — a superset codec (latin-1) would
    # decode the bytes and false-PASS this test without exercising R24.
    enc = subprocess.run(
        [sys.executable, "-c", "import locale; print(locale.getencoding())"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert enc.stdout.strip().lower() in ("ascii", "us-ascii",
                                          "ansi_x3.4-1968"), (
        f"child locale encoding is {enc.stdout.strip()!r}, not ASCII — this "
        "test cannot exercise the R24 decode")

    r = _run(("install", "codex", "--dir", str(root)), env, root)

    assert "Traceback" not in r.stderr, r.stderr
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    # The merge really happened (the non-ASCII sibling key survives escaped).
    merged = json.loads(target.read_text(encoding="utf-8"))
    assert merged["note"] == "caf\u00e9"
    assert any(
        "volunteer-turn.sh" in e.get("hooks", [{}])[0].get("command", "")
        for e in merged["hooks"]["UserPromptSubmit"]
        if isinstance(e, dict) and e.get("hooks")
    )


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


def test_cli_install_pi_rerun_does_not_claim_installed(cli):
    """A second `tortoise install pi` is a no-op: it must report the no-op and
    NOT claim it installed the extension.

    Mutation: print the pi success sentence unconditionally on the non-dry
    path (the re-run prints "already installed — nothing to do." AND "Pi
    capture extension installed" — the false-success class `--dry-run` already
    fixed) — the second stdout assertion turns RED on that one line."""
    run, _root, home = cli
    ext = (home / ".pi" / "agent" / "extensions"
           / capture_install.PI_EXTENSION_NAME)

    first = run("install", "pi")
    assert first.returncode == 0, first.stderr
    assert "Pi capture extension installed" in first.stdout, first.stdout
    before = ext.stat().st_mtime_ns

    second = run("install", "pi")

    assert second.returncode == 0, second.stderr
    assert "pi capture seam already installed" in second.stdout, second.stdout
    assert "Pi capture extension installed" not in second.stdout, second.stdout
    assert ext.stat().st_mtime_ns == before, "a re-run rewrote the extension"


def test_cli_install_claude_uninstall_discloses_the_live_capture_seam(cli):
    """`--uninstall` is scoped to the read-hook registration, so claude's
    capture seam stays live and the run must SAY so rather than printing only
    "Uninstalled volunteer-turn.sh".

    Mutation: drop the disclosure print after `_install_read_hook` (the output
    reads as a full uninstall while the capture scripts and their
    SessionStart/SessionEnd/UserPromptSubmit registrations remain live), or
    delete those artifacts (the disclosure becomes false) — either way this
    turns RED."""
    run, root, _home = cli
    first = run("install", "claude", "--dir", str(root))
    assert first.returncode == 0, first.stderr

    r = run("install", "claude", "--dir", str(root), "--uninstall")

    assert r.returncode == 0, r.stderr
    assert "Uninstalled volunteer-turn.sh" in r.stdout, r.stdout
    # The disclosure names the surviving seam and where it lives.
    assert "left in place" in r.stdout, r.stdout
    assert "session-start.sh" in r.stdout and "session-end.sh" in r.stdout, (
        r.stdout)
    # The disclosure must name EVERY surviving script: announcing two of three
    # reads as a complete accounting while the third's registration stays live
    # and unmentioned (#3963).
    assert "session-turn.sh" in r.stdout, r.stdout
    assert "settings.json" in r.stdout, r.stdout
    # ...and it is TRUE: only the read-hook half was removed.  EVERY capture
    # script survives, including the per-turn one (#3963).
    hooks = root / ".claude" / "hooks"
    assert (hooks / "session-start.sh").is_file()
    assert (hooks / "session-end.sh").is_file()
    assert (hooks / "session-turn.sh").is_file()
    cfg = _settings(root)["hooks"]
    assert "SessionStart" in cfg and "SessionEnd" in cfg, cfg
    # The read hook's REGISTRATION is gone.  Absence of the event KEY was only
    # ever a proxy for that: since #3963 the capture seam registers its own
    # per-turn hook on the SAME ``UserPromptSubmit`` event, and that one must
    # SURVIVE ``--uninstall`` (the disclosure promises the seam is left in
    # place) — so requiring the key to be absent now fails a CORRECT uninstall.
    read_cmds = [h.get("command")
                 for e in cfg.get("UserPromptSubmit", [])
                 for h in e.get("hooks", [])]
    assert not any("volunteer-turn.sh" in (c or "") for c in read_cmds), (
        f"the read hook was not removed: {read_cmds}")


def test_install_uninstall_help_does_not_overpromise(cli):
    """`tortoise install --help` must not describe `--uninstall` as removing
    "the hook registration" — the capture seam survives it.

    Mutation: revert the help string to "Remove the hook registration for the
    harness" (the promise the live capture seam contradicts)."""
    run, _root, _home = cli

    r = run("install", "--help")

    assert r.returncode == 0, r.stderr
    # argparse wraps help to the terminal width — compare unwrapped text.
    help_text = " ".join(r.stdout.split())
    assert "capture seam is left in place" in help_text, help_text
    assert "Remove the hook registration for the harness" not in help_text, (
        help_text)


# ── #3915: the capture-install contract is pinned against #3866 (parity) ─
# `capture_install` (installs the seam) and `hook_install` (status/upgrade)
# must answer "is this entry ours?" identically.  Since the fix they share ONE
# classifier (`hook_install._invokes_script`); these tests pin the observable
# contract across both modules so a future re-split cannot reintroduce a silent
# divergence — the pre-fix copy diverged on 10 of 21 command forms (a duplicate
# SessionEnd registration for `/bin/sh <hook>`, fail-open for `sudo -u <hook>`).


# (command, expected "ours?" verdict in BOTH modules).  The first 16 forms
# REALLY execute the hook ("ours"); the last 5 must stay foreign in both.  The
# expected verdict is asserted DIRECTLY — comparing the two call sites alone
# cannot give this test signal, because `capture_install._is_our_script_command`
# is a one-line delegation to `hook_install._invokes_script`, so both sides of
# the comparison move together under any mutation.
_CLASSIFIER_PARITY_FORMS = [
    # forms that REALLY execute the hook — "ours" in both modules
    (".claude/hooks/session-end.sh", True),
    ("bash .claude/hooks/session-end.sh", True),
    ("/bin/bash .claude/hooks/session-end.sh", True),
    ("/bin/sh .claude/hooks/session-end.sh", True),
    ("timeout 5 .claude/hooks/session-end.sh", True),
    ("timeout -s KILL 60 .claude/hooks/session-end.sh", True),
    ("nice -n 5 .claude/hooks/session-end.sh", True),
    ("xargs .claude/hooks/session-end.sh", True),
    ("sudo -u root .claude/hooks/session-end.sh", True),
    ("if true; then .claude/hooks/session-end.sh; fi", True),
    ("A=/x/y .claude/hooks/session-end.sh", True),
    ("env -u FOO .claude/hooks/session-end.sh", True),
    ("true; .claude/hooks/session-end.sh", True),
    ("bash -c 'true; .claude/hooks/session-end.sh'", True),
    ("2>/dev/null .claude/hooks/session-end.sh", True),
    ("$CLAUDE_PROJECT_DIR/.claude/hooks/session-end.sh", True),
    # forms that must stay FOREIGN in both modules
    ("cat .claude/hooks/session-end.sh", False),
    ("cat $CLAUDE_PROJECT_DIR/.claude/hooks/session-end.sh", False),
    ("command -v .claude/hooks/session-end.sh", False),
    ("sudo -u .claude/hooks/session-end.sh", False),  # path is sudo's -u VALUE
    ("vendor/.claude/hooks/session-end.sh", False),
]


def test_classifier_parity_with_hook_install(tmp_path):
    """`capture_install._is_our_script_command` and
    `hook_install._invokes_script` return the SAME verdict for every corpus
    form — and that verdict is the expected one.

    Mutation: make `hook_install._invokes_script` `return False` — the 16
    positive corpus forms RED here.  Asserting the expected verdict directly
    is what gives this test signal; the parity comparison alone is a
    self-identity assertion because `capture_install._is_our_script_command`
    delegates to the same function."""
    for command, expected in _CLASSIFIER_PARITY_FORMS:
        ours = capture_install._is_our_script_command(
            command, "session-end.sh", ".claude/hooks", tmp_path)
        theirs = hook_install._invokes_script(
            command, "session-end.sh", ".claude/hooks", tmp_path)
        assert ours is expected, (
            f"capture_install misclassified {command!r}: "
            f"got {ours}, expected {expected}")
        assert theirs is expected, (
            f"hook_install misclassified {command!r}: "
            f"got {theirs}, expected {expected}")
        assert ours == theirs, (
            f"classifier divergence on {command!r}: "
            f"capture_install={ours}, hook_install={theirs}")


#: (st_mode, expected "owner can execute it?" verdict) for the ONE shared
#: predicate.  ``0o111``/``0o100`` set the owner bit under different
#: spellings; ``0o755``/``0o700`` are ordinary install modes; ``0o601`` /
#: ``0o410`` are the modes where a NON-owner exec bit is the only exec bit and
#: ``st_mode & 0o111`` gives the WRONG answer; ``0o644``/``0o000`` agree under
#: either mask and therefore discriminate nothing.
_OWNER_EXEC_CORPUS = [
    (0o755, True), (0o700, True), (0o111, True), (0o100, True),
    (0o644, False), (0o601, False), (0o410, False), (0o000, False),
]


def test_owner_exec_predicate_reads_only_the_owners_bit():
    """``hook_install._has_owner_exec_bit`` is the one definition of "can the
    harness run this hook?" — the OWNER's bit, not any exec bit.

    Mutation: revert it to ``st_mode & 0o111`` — the ``0o601`` and ``0o410``
    corpus entries RED (it reports True where the owner cannot execute the
    hook); treat it as ``st_mode & stat.S_IXGRP | st_mode & stat.S_IXOTH`` —
    the same two RED.  The expected verdict is asserted DIRECTLY, so the test
    has signal independent of the delegation (comparing the two call sites
    alone would move together under any mutation)."""
    for mode, expected in _OWNER_EXEC_CORPUS:
        got = hook_install._has_owner_exec_bit(mode)
        assert got is expected, (
            f"_has_owner_exec_bit({mode:o}) = {got}, expected {expected}")


def test_capture_install_delegates_the_owner_exec_predicate(tmp_path, monkeypatch):
    """`capture_install`'s exec-bit repair asks
    `hook_install._has_owner_exec_bit` instead of spelling the mask again —
    the same one-definition rule the classifier and command-dict helpers
    follow.

    Signal: force the shared predicate to say "has the owner exec bit" for a
    `0o601` hook, where the owner CANNOT execute it.  If the two surfaces
    share one definition, the capture installer now reads the hook as
    installed and does nothing; if `capture_install` kept its own
    `st_mode & stat.S_IXUSR` test, the hook is still repaired and
    ``res.changed`` stays True → RED.  A second pass without the patch pins
    the un-patched behaviour (the hook IS repaired), so the first pass is not
    vacuously green.

    Mutation: replace the delegated call with a local ``dst.stat().st_mode &
    stat.S_IXUSR`` — the first pass REDs."""
    install_capture("claude", root=tmp_path)
    hook = tmp_path / ".claude" / "hooks" / "session-end.sh"
    os.chmod(hook, 0o601)

    monkeypatch.setattr(hook_install, "_has_owner_exec_bit",
                        lambda st_mode: True)
    res = install_capture("claude", root=tmp_path)
    assert res.ok, res.error
    assert res.changed is False, (
        "capture_install ignored the shared exec-bit predicate and repaired "
        "the hook on its own mask")
    assert (hook.stat().st_mode & 0o777) == 0o601

    monkeypatch.undo()
    res = install_capture("claude", root=tmp_path)
    assert res.ok, res.error
    assert res.changed is True, "the real predicate no longer repairs 0o601"
    assert os.access(hook, os.X_OK)


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


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_timeout_is_never_a_budget(tmp_path, literal):
    """A JSON ``NaN``/``Infinity`` timeout is not a budget on ANY surface.

    ``json.loads`` accepts a bare ``NaN``/``Infinity`` literal, and the float
    arm of ``_is_timeout_budget`` accepts it too, so a non-finite value read as
    "already budgeted": ``nan < 60`` is False, meaning the low-timeout check
    missed it as well, ``hooks status`` reported nothing, ``hooks upgrade``
    refused to lower it, and the installer preserved it — leaving the hook on
    Claude Code's 1.5 s default, the exact fail-open the ``timeout`` exists to
    prevent (the int-only predicate this float arm replaced flagged it).

    Mutation: drop the ``math.isfinite(value)`` clause — every assertion below
    turns RED and the ``nan`` ends up on disk."""
    settings = ('{"hooks": {"SessionEnd": [{"matcher": "", "hooks": '
                '[{"type": "command", "command": '
                '".claude/hooks/session-end.sh", "timeout": ' + literal
                + '}]}]}}')

    # 1. hooks status — the non-finite value is drift, not a budget.
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(settings)
    findings = hook_install.detect_install(tmp_path, "claude")
    assert [f for f in findings if f.kind == "settings-no-timeout"], (
        f"status accepted a {literal} timeout: {findings}")

    # 2. hooks upgrade — it is rewritten to the required budget.
    upgrade = hook_install.upgrade_install(tmp_path, "claude")
    assert upgrade.refused is None, upgrade.refused
    after = _settings(tmp_path)["hooks"]["SessionEnd"][0]["hooks"][0]
    assert after["timeout"] == CLAUDE_TIMEOUT, (
        f"upgrade left a {literal} timeout in place: {upgrade.actions}")

    # 3. the installer — the same, into a fresh project.
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text(settings)
    res = install_capture("claude", root=proj)
    assert res.ok, res.error
    merged = _settings(proj)["hooks"]["SessionEnd"][0]["hooks"][0]
    assert merged["timeout"] == CLAUDE_TIMEOUT, (
        f"the installer preserved a {literal} timeout: {res.actions}")
    assert math.isfinite(merged["timeout"]), merged

    # ...and the shared predicate itself, which is the single gate all three
    # surfaces read (last, so a regression reports the SURFACE that failed).
    assert not hook_install._is_timeout_budget(float(literal)), (
        f"_is_timeout_budget accepted {literal}")


def test_int_timeout_larger_than_a_double_is_never_a_budget(tmp_path):
    """A JSON integer too large for a double is not a budget on ANY surface,
    and the predicate must return a verdict instead of raising.

    ``json.loads`` parses an integer literal of any magnitude as an
    arbitrary-precision ``int`` (no float coercion), and ``math.isfinite``
    coerces to a C double, so a >308-digit ``timeout`` raises
    ``OverflowError`` out of ``hooks status``, ``hooks upgrade`` and
    ``install_capture`` — a CLI traceback on valid JSON, and a regression the
    float non-finite gate introduced.

    Mutation: restore the bare ``and math.isfinite(value)`` return — the
    predicate call raises ``OverflowError`` and every surface below dies
    rather than rewriting the value to 60."""
    huge = 10 ** 400
    settings = json.dumps({"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
        {"type": "command", "command": ".claude/hooks/session-end.sh",
         "timeout": huge}]}]}})

    # 1. the shared predicate itself — a verdict, not an exception.
    assert hook_install._is_timeout_budget(huge) is False

    # 2. hooks status — drift, not a budget.
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(settings)
    findings = hook_install.detect_install(tmp_path, "claude")
    assert [f for f in findings if f.kind == "settings-no-timeout"], (
        f"status accepted a >double-int timeout: {findings}")

    # 3. hooks upgrade — rewritten to the required budget.
    upgrade = hook_install.upgrade_install(tmp_path, "claude")
    assert upgrade.refused is None, upgrade.refused
    after = _settings(tmp_path)["hooks"]["SessionEnd"][0]["hooks"][0]
    assert after["timeout"] == CLAUDE_TIMEOUT, (
        f"upgrade left a >double-int timeout in place: {upgrade.actions}")

    # 4. the installer — the same, into a fresh project.
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text(settings)
    res = install_capture("claude", root=proj)
    assert res.ok, res.error
    merged = _settings(proj)["hooks"]["SessionEnd"][0]["hooks"][0]
    assert merged["timeout"] == CLAUDE_TIMEOUT, (
        f"the installer preserved a >double-int timeout: {res.actions}")


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
    # Derive the expectation from the installer's OWN declaration (#3971): a
    # hardcoded count cannot see a script the installer ships but the dashboard
    # never registers — it stays GREEN while the copy-paste block is missing a
    # hook entirely, which is exactly the drift this guard exists to catch.
    expected_timeouts = [str(budget) for _, _, budget in CLAUDE_CAPTURE_HOOKS]
    assert len(timeouts) == len(CLAUDE_CAPTURE_HOOKS), (
        f"the dashboard capture block declares {len(timeouts)} timeout(s), "
        f"expected one per script ({len(CLAUDE_CAPTURE_HOOKS)})")
    assert timeouts == expected_timeouts, (
        f"the dashboard capture block emits timeout(s) {timeouts}, expected "
        f"{expected_timeouts} — the installer and the copy-paste block "
        "disagree")
    for script_name, event, _ in CLAUDE_CAPTURE_HOOKS:
        assert f".claude/hooks/{script_name}" in fragment, (
            f"the dashboard capture block never registers {script_name} "
            f"({event})")


def test_installer_declares_registers_and_detects_one_script_set():
    """The three surfaces that define a Claude install must name the SAME set.

    ``CLAUDE_CAPTURE_HOOKS`` (what is copied + registered), ``CLAUDE_SCRIPTS``
    (the copy loop's list), and ``hook_install``'s layout — what
    ``detect_install`` demands and ``upgrade_install`` repairs — are three
    separate surfaces, and nothing pinned them together (#3971 merge): the
    installer copied TWO scripts while the layout demanded THREE, so a fresh
    install was reported by detection as ``missing-script: session-turn.sh``
    — broken by construction, and nothing could name the cause.

    Mutation: hardcode ``CLAUDE_SCRIPTS`` back to the pair (the copy leg REDs)
    or drop the ``session-turn.sh`` spec from the layout (the detect leg REDs).
    LEGITIMATE GREEN: adding a script to ``CLAUDE_CAPTURE_HOOKS`` AND the
    layout — which is how the per-turn hook was declared.
    """
    layout = hook_install.get_layout("claude")
    declared = [(name, event) for name, event, _ in CLAUDE_CAPTURE_HOOKS]
    assert list(capture_install.CLAUDE_SCRIPTS) == [n for n, _ in declared], (
        "CLAUDE_SCRIPTS drifted from CLAUDE_CAPTURE_HOOKS — the installer "
        f"copies {list(capture_install.CLAUDE_SCRIPTS)} while the seam "
        f"declares {[n for n, _ in declared]}")
    assert [(s.name, s.event) for s in layout.scripts] == declared, (
        "the layout detect_install uses names different (script, event) "
        f"pairs than the installer registers: "
        f"{[(s.name, s.event) for s in layout.scripts]} vs {declared}")
    assert [s.timeout for s in layout.scripts] == [
        t for _, _, t in CLAUDE_CAPTURE_HOOKS], (
        "the layout's per-script budgets differ from CLAUDE_CAPTURE_HOOKS — "
        "detect/upgrade then disagree about a script whose budget is not the "
        "default")


def test_a_fresh_claude_install_is_reported_complete(tmp_path):
    """The observable the merge broke: install, then ASK detection.

    Mutation: make the installer skip one ``CLAUDE_CAPTURE_HOOKS`` script (the
    #3971 defect) → ``detect_install`` returns a blocking ``missing-script``
    finding → RED.
    """
    install_capture("claude", root=tmp_path)

    assert hook_install.detect_install(tmp_path, "claude") == []
    # The per-turn hook (#3963) landed under its OWN event and its OWN budget —
    # not silently rendered as a second SessionEnd at the 60 s default.
    inner = [h for e in _settings(tmp_path)["hooks"]["UserPromptSubmit"]
             for h in e.get("hooks", [])]
    assert {"type": "command", "command": ".claude/hooks/session-turn.sh",
            "timeout": CLAUDE_PER_TURN_TIMEOUT} in inner, inner


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


# The shipped install-contract generations, one per wizard-offered harness.
# The marker is what makes a stale installed seam detectable, so it MUST be
# bumped when what the seam writes changes, and bumping must be DELIBERATE.
# Pinning the values here, ONCE, is what makes a revert RED (a silently reverted
# marker mis-classifies current installs as stale, or stale ones as current) and
# makes the next bump a deliberate edit of this table.  A literal at each
# install assertion does neither: it goes stale silently, which is exactly how
# #4314 left two red assertions behind.
# claude 5→6 is the #3615 consent gate merged over main's 5 (the hooks changed
# behaviour again, so an already-installed copy must read as stale).
# pi 1 is the FIRST generation of the Pi seam's contract (#4680): the seam is a
# TypeScript extension rather than a shell hook, so it has no `HarnessLayout` —
# its contract is carried by `hook_install.ARTIFACT_CONTRACTS['pi']`.  Before
# #4680 the Pi seam carried no marker at all, which is why a two-week-old
# installed copy read as merely UNVERIFIABLE while capturing the old logic.
_EXPECTED_INSTALL_CONTRACT = {"claude": 6, "codex": 2, "cursor": 2, "pi": 1}


@pytest.mark.parametrize("harness", sorted(_EXPECTED_INSTALL_CONTRACT))
def test_shipped_install_contract_generations(harness):
    """#4314 changes what an installed hook writes (a capture-error breadcrumb)
    and what the installer records, so every shipped generation moved — claude
    3→4, codex 1→2, cursor 1→2.  #3971 then changed the claude hooks'
    BEHAVIOUR again (the CWE-427 sys.path scrub), so claude moved 4→5: an
    already-installed copy must be detected as stale, otherwise the security
    fix never reaches it.  Those numbers are a reviewed decision, not a
    detail, so they are pinned once and explicitly.

    `pi` (#4680) reaches the same table through the ARTIFACT half of the
    contract: it ships a TypeScript extension and has no `HarnessLayout` —
    the no-fake-layout ruling documented on
    ``hook_install.get_layout_optional`` — so it is pinned by
    ``contract_version_for``, the ONE accessor that answers for a shell-hook
    layout and a non-shell artifact alike.
    """
    shipped = hook_install.contract_version_for(harness)
    assert shipped == _EXPECTED_INSTALL_CONTRACT[harness], (
        f"{harness} ships contract generation {shipped}, expected "
        f"{_EXPECTED_INSTALL_CONTRACT[harness]} — if that bump was deliberate, "
        f"update _EXPECTED_INSTALL_CONTRACT; if not, this is the revert")


def _pi_seam_without_marker() -> str:
    """The shipped Pi seam with its contract marker stripped.

    Models the real pre-contract population #4680 was filed about: a
    functioning Tortoise seam that declares no generation.  The BODY is kept
    intact on purpose, because ownership is sniffed from it — a synthetic body
    without a Tortoise signature would model a FOREIGN file, not a
    pre-contract copy of ours.
    """
    return "\n".join(
        line for line in _PI_SRC.read_text(encoding="utf-8").splitlines()
        if not line.startswith("// tortoise-hook-version:")) + "\n"


def test_pi_seam_carries_exactly_one_canonical_marker():
    """The Pi seam declares the SAME contract vocabulary as its three shell
    siblings, so a stale installed copy is comparable rather than
    unmeasurable (#4680).

    Mutation: delete ``// tortoise-hook-version: 1`` from
    ``tortoise/pi-hooks/tortoise-capture.ts`` — ``read_hook_version`` returns
    ``None``, ``contract_version_for('pi')`` returns ``None``, and both this
    test and the generation pin RED.
    """
    assert hook_install.read_hook_version(_PI_SRC) == 1
    assert hook_install.count_canonical_markers(_PI_SRC) == 1, (
        "the Pi seam must carry exactly ONE column-0 marker (a second one is a "
        "site marker that would be mistaken for the contract)")


def test_pi_contract_is_pinned_to_the_installer_artifact():
    """The contract registry names the SAME file the installer writes, so the
    drift detector can never inspect a path the installer does not produce.

    Mutation: hardcode a literal in ``capture_install.PI_EXTENSION_NAME``
    (drop the derivation from the registry) and change the registry basename —
    parity REDs.  The derivation is what makes them one fact; the equality
    below is the assertion that it is still there.
    """
    contract = hook_install.ARTIFACT_CONTRACTS["pi"]
    assert contract.install_name == contract.source.name, (
        "the detector must probe the shipped artifact's own basename, or it "
        "inspects a file the installer would never write")
    assert contract.install_name == capture_install.PI_EXTENSION_NAME
    assert contract.source == _PI_SRC
    assert contract.source.relative_to(_REPO_ROOT).as_posix() == (
        CAPTURE_SEAM["pi"])


def test_pi_installed_seam_is_read_as_current(home):
    """The installer's own output is "current" to the artifact detector — the
    same property ``detect_install`` has for the three shell seams, so
    ``session verify --harness pi`` cannot report a fresh install as stale.

    Mutation: make ``detect_artifact_install`` compare against a different
    generation than the shipped marker — a fresh install reports drift.
    """
    res = install_capture("pi", home=home)
    assert res.ok, res.error
    root = capture_install.pi_home(home)
    assert hook_install.detect_artifact_install(root, "pi") == [], (
        "the installer produced a Pi seam the drift detector calls drifted")


def test_pi_stale_installed_seam_is_reported_not_silent(home):
    """The failure this contract exists to catch (#4680): an installed Pi seam
    from an older generation captures with the old logic and NO surface says
    so.  Both shapes of stale must be BLOCKING findings — a marked-but-older
    copy (``stale-artifact``) and the pre-contract copy that has no marker at
    all (``unversioned-artifact``).

    Mutation: make ``detect_artifact_install`` return ``[]`` for a Pi root
    that is present (the pre-#4680 ``_static_findings`` behaviour) — this
    REDs on both the stale and the unversioned leg.
    """
    res = install_capture("pi", home=home)
    assert res.ok, res.error
    root = capture_install.pi_home(home)
    installed = root / capture_install.PI_EXTENSION_NAME

    # (a) a marked copy from an OLDER generation
    installed.write_text(
        "// tortoise-hook-version: 0\n" + "// body\n", encoding="utf-8")
    findings = hook_install.detect_artifact_install(root, "pi")
    blocking = [f for f in findings if f.blocking]
    assert [f.kind for f in blocking] == ["stale-artifact"], findings
    assert "current is" in blocking[0].detail

    # (b) the pre-contract copy: a REAL seam with its marker stripped, so it
    # is ours by body signature (`_looks_like_our_script`) but declares no
    # generation — the population #4680 was filed about.
    installed.write_text(_pi_seam_without_marker(), encoding="utf-8")
    findings = hook_install.detect_artifact_install(root, "pi")
    blocking = [f for f in findings if f.blocking]
    assert [f.kind for f in blocking] == ["unversioned-artifact"], findings
    assert "tortoise install pi" in blocking[0].detail, (
        "a stale finding must name the sanctioned repair, or verify reports a "
        "problem with no path out")


def test_pi_foreign_artifact_is_reported_foreign_not_unversioned(home):
    """A file at the Pi extension path that is NOT a Tortoise seam must be
    classified foreign, not as a pre-contract copy of ours — the shell half
    makes the same distinction (``foreign-script``).  Mislabeling it as ours
    would make the installer REPLACE the foreign file (keeping a ``.bak`` of
    the bytes) under an instruction that says "reinstall"; labeling it
    foreign routes the user to the loud, correct instruction instead.

    Mutation: drop the ``_looks_like_our_script`` ownership sniff (treat every
    unmarkered file as ours) — this REDs with ``unversioned-artifact``.
    """
    res = install_capture("pi", home=home)
    assert res.ok, res.error
    root = capture_install.pi_home(home)
    (root / capture_install.PI_EXTENSION_NAME).write_text(
        "// some other product extension\nexport default () => {};\n",
        encoding="utf-8")
    findings = hook_install.detect_artifact_install(root, "pi")
    blocking = [f for f in findings if f.blocking]
    assert [f.kind for f in blocking] == ["foreign-artifact"], findings


def test_pi_symlinked_seam_is_judged_on_its_target(home):
    """A symlink install (the shape the issue's notes describe for a dev
    checkout) is reported on the RESOLVED bytes: a symlink to the shipped
    seam is current, a symlink to an OLD copy is BLOCKING stale, and a broken
    symlink is neither missing nor current.

    Mutation: skip the version comparison for a symlink (return only the
    non-blocking ``symlinked-artifact`` note) — a symlink to a stale checkout
    reads as current and this REDs.
    """
    res = install_capture("pi", home=home)
    assert res.ok, res.error
    root = capture_install.pi_home(home)
    installed = root / capture_install.PI_EXTENSION_NAME
    installed.unlink()

    # (i) symlink to the SHIPPED seam — current, with only the non-blocking note
    installed.symlink_to(_PI_SRC)
    findings = hook_install.detect_artifact_install(root, "pi")
    assert [f.kind for f in findings if f.blocking] == [], findings
    assert [f.kind for f in findings] == ["symlinked-artifact"], findings

    # (ii) symlink to an OLD copy — the target's generation is what counts
    old = home / "old-seam.ts"
    old.write_text(_pi_seam_without_marker(), encoding="utf-8")
    installed.unlink()
    installed.symlink_to(old)
    blocking = [f for f in hook_install.detect_artifact_install(root, "pi")
                if f.blocking]
    assert [f.kind for f in blocking] == ["unversioned-artifact"], blocking

    # (iii) a BROKEN symlink is its own finding, never `missing-artifact`
    installed.unlink()
    installed.symlink_to(home / "gone.ts")
    findings = hook_install.detect_artifact_install(root, "pi")
    assert [f.kind for f in findings] == ["symlinked-artifact"], findings
    assert "broken symlink" in findings[0].detail


def test_pi_absent_seam_is_a_blocking_missing_finding(tmp_path):
    """A Pi root with no seam must still be reported — the detector may not
    trade the old ``missing-extension`` signal away for the version check.

    Mutation: return only version findings (drop the existence check) — a
    missing seam reads as current and verify fires nothing.
    """
    findings = hook_install.detect_artifact_install(tmp_path, "pi")
    assert [f.kind for f in findings] == ["missing-artifact"]
    assert findings[0].blocking


def test_artifact_registry_and_the_installer_agree_on_root_and_name():
    """The registry is a SINGLE source of truth for the whole contract: which
    harnesses exist, where each installs, and what file it installs under.

    Mutation: add a contract whose key names a different harness than its own
    `harness` field, register an artifact for a harness that also has a shell
    layout, or let `root_relpath` disagree with `pi_home` — each assertion
    below REDs, and each failure is a silent-omission bug (doctor and
    `session verify` would probe a path no installer writes).
    """
    from tortoise import hook_install
    assert "pi" in hook_install.ARTIFACT_CONTRACTS
    for key, contract in hook_install.ARTIFACT_CONTRACTS.items():
        assert contract.harness == key, (
            f"ARTIFACT_CONTRACTS[{key!r}].harness is {contract.harness!r} — "
            "a mismatch makes the registry unusable as a lookup table")
        assert key not in hook_install.HARNESS_LAYOUTS, (
            f"{key!r} has BOTH a shell layout and an artifact contract — a "
            "caller must be able to tell the two seam classes apart")
        assert contract.source.name == contract.install_name
    assert hook_install.artifact_root("pi", Path("/tmp/home")) == (
        Path("/tmp/home") / ".pi" / "agent" / "extensions")
    assert hook_install.artifact_root("pi", Path("/tmp/home")) == (
        capture_install.pi_home(Path("/tmp/home")))
    assert hook_install.artifact_root("claude", Path("/tmp/home")) is None, (
        "a layout-only harness must not answer with an artifact root")


def test_read_hook_version_reports_an_unrepresentable_marker_as_unmarkered(
        home):
    """#3928: `read_hook_version`'s documented contract is that it returns
    None, never raises.  A marker with more digits than CPython will convert
    (`sys.get_int_max_str_digits()`, 4300 by default) makes `int()` raise
    ValueError, and #4680 wires the reader to a USER-EDITABLE artifact at
    ``~/.pi/agent/extensions/`` — so a corrupt seam must still classify as
    unmarkered rather than unwind the detector.

    Mutation: `return int(matches[0])` with no guard — install a marker of
    5000 digits and this REDs with `ValueError`.
    """
    res = install_capture("pi", home=home)
    assert res.ok, res.error
    root = capture_install.pi_home(home)
    installed = root / capture_install.PI_EXTENSION_NAME
    # The shipped BODY with only the marker line replaced, so the file is still
    # recognisably ours (`_looks_like_our_script`) — otherwise the assertion
    # below would be about a foreign file, not about the marker.
    installed.write_text(
        "\n".join(
            "// tortoise-hook-version: " + "9" * 5000
            if line.startswith("// tortoise-hook-version:") else line
            for line in installed.read_text(encoding="utf-8").splitlines()
        ) + "\n",
        encoding="utf-8")

    assert hook_install.read_hook_version(installed) is None
    blocking = [f for f in hook_install.detect_artifact_install(root, "pi")
                if f.blocking]
    assert [f.kind for f in blocking] == ["unversioned-artifact"], blocking


def test_read_hook_version_returns_none_for_a_non_searchable_parent(tmp_path):
    """The reader's only failure signal is `None` (#4680 review).

    `Path.is_file` re-raises EACCES — pathlib ignores ENOENT, ENOTDIR, EBADF
    and ELOOP only — so a non-searchable parent reached every caller as
    `PermissionError`, contradicting the docstring this reader is trusted on.
    The reader is wired to a USER-EDITABLE artifact, so `None` must be the
    whole of its failure surface.

    Mutation: move `p.is_file()` back OUTSIDE the `try` — this REDs with
    `PermissionError` instead of returning None.
    """
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses the permission bits")
    locked = tmp_path / "locked"
    locked.mkdir()
    target = locked / "session-start.sh"
    target.write_text("# tortoise-hook-version: 3\n", encoding="utf-8")
    locked.chmod(0o000)
    try:
        assert hook_install.read_hook_version(target) is None
    finally:
        locked.chmod(0o700)


def test_pi_artifact_kinds_are_reported_on_the_finding_they_describe(home):
    """Every artifact kind the seam can report is pinned behaviourally.

    Only `stale-artifact` had one, so a `<`↔`>` swap (ahead), a `!=`↔`==` swap
    on the byte compare (modified), or a dropped structural branch all
    survived the suite — the shape of a finding is part of its contract.

    Mutation: compare the marker with `>` instead of `<` → the ahead arm REDs;
    compare bytes with `==` instead of `!=` → the modified arm REDs.
    """
    res = install_capture("pi", home=home)
    assert res.ok, res.error
    root = capture_install.pi_home(home)
    installed = root / capture_install.PI_EXTENSION_NAME
    current = hook_install.contract_version_for("pi")
    shipped = installed.read_text(encoding="utf-8")

    def kinds() -> dict[str, bool]:
        return {f.kind: f.blocking
                for f in hook_install.detect_artifact_install(root, "pi")}

    # A NEWER marker than the contract is ahead, and non-blocking: the seam is
    # not from this checkout, so the repair must not call it stale.
    installed.write_text(
        shipped.replace(f"tortoise-hook-version: {current}",
                        f"tortoise-hook-version: {current + 1}"),
        encoding="utf-8")
    assert kinds() == {"ahead-artifact": False}, kinds()

    # The SAME marker with different bytes is ours but drifted, and it blocks.
    installed.write_text(shipped + "// drifted\n", encoding="utf-8")
    assert kinds() == {"modified-artifact": True}, kinds()

    # A directory at the artifact path is structural, not a version state.
    installed.unlink()
    installed.mkdir()
    assert kinds() == {"not-a-regular-file": True}, kinds()


def test_manual_fix_predicate_covers_both_seam_classes():
    """`is_manual_fix` is the ONE declaration of "the automated repair refuses
    this kind", consulted by `hooks status` and by `doctor`.  It must know the
    artifact kinds too: `tortoise install pi` refuses a foreign or unreadable
    artifact / non-regular file / out-of-HOME symlink exactly as `hooks
    upgrade` refuses their shell siblings.

    Mutation: drop `foreign-artifact` (or a shared structural name) from
    `MANUAL_FIX_KINDS` — the doctor/status test that checks the hint does not
    name a refusing command REDs.
    """
    for kind in ("foreign-script", "foreign-artifact", "not-a-regular-file",
                 "not-readable", "unreadable-settings", "symlinked-artifact",
                 "symlinked-script", "symlinked-settings",
                 "symlinked-install"):
        assert hook_install.is_manual_fix(kind), kind
    for kind in ("stale-script", "stale-artifact", "unversioned-script",
                 "unversioned-artifact", "modified-script",
                 "modified-artifact", "ahead-script", "ahead-artifact"):
        assert not hook_install.is_manual_fix(kind), kind


def test_pi_symlinked_install_root_is_noted_as_uninstallable(home):
    """A symlinked install ROOT is refused by `install_capture` (it will not
    write through a symlink, in-HOME or out), so the detector must SAY so —
    otherwise `doctor` recommends `tortoise install pi` for a command that
    refuses (the artifact peer of `detect_install`'s `symlinked-install`).

    Mutation: drop the root-symlink check — the root link is invisible
    (`missing-artifact` only), and the doctor hint test REDs.
    """
    real = home / "checkout-extensions"
    real.mkdir(parents=True)
    (home / ".pi" / "agent").mkdir(parents=True)
    root = home / ".pi" / "agent" / "extensions"
    root.symlink_to(real)
    (real / "tortoise-capture.ts").write_text(
        "// tortoise-hook-version: 0\n// tortoise session\n", encoding="utf-8")

    findings = hook_install.detect_artifact_install(root, "pi")
    kinds = [f.kind for f in findings]
    assert "symlinked-install" in kinds, findings
    note = next(f for f in findings if f.kind == "symlinked-install")
    assert not note.blocking, "a symlink note is informational, not blocking"
    assert hook_install.is_manual_fix("symlinked-install")
    # The stale bytes inside are still reported: the note never masks drift.
    assert "stale-artifact" in kinds, findings
    # And the installer really refuses it — the note must not lie.
    res = install_capture("pi", home=home)
    assert not res.ok, res.actions
    assert "symlink" in (res.error or "").lower(), res.error
