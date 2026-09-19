"""#3795/#3801 — capture-hook install drift detection + in-place upgrade.

The install seam is a **manual copy**: a user copies the two shipped hook
scripts into ``.claude/hooks/`` and separately merges a ``settings.json``
fragment.  A byte-frozen copy can therefore keep the pre-#3755 defect (the
probe never fires) or the pre-#3754 defect (no ``timeout`` → Claude Code
cancels SessionEnd at 1.5 s → the session is silently never filed).

These tests EXECUTE the mechanism rather than grepping the source (lane
directive §8: a guard pinned to source text is a false PASS — reverting the
guarded thing leaves it green).  Each test builds a real old-version install
fixture, runs the CLI, and asserts the **resolved** outcome: the installed
marker reads the current generation and the merged ``settings.json`` carries
the ``timeout``.  The mutation that turns each one RED is named in its
docstring.

The "current generation" is never hard-coded: it is read from the shipped
scripts through the same API the CLI uses, so a future marker bump updates the
tests automatically and only a genuine regression fails them.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import string
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import hook_install
from tortoise.hook_install import (
    HarnessLayout,
    HookScriptSpec,
    contract_version,
    count_canonical_markers,
    detect_install,
    get_layout,
    read_hook_version,
    upgrade_install,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PY = sys.executable
_LAYOUT = get_layout("claude")
_CURRENT = contract_version(_LAYOUT)
assert _CURRENT is not None, "shipped hooks must declare one contract version"

#: Modes where a NON-owner exec bit is the ONLY exec bit.  The harness runs as
#: the install's OWNER, so it cannot execute a hook at these modes and the
#: fail-open script files nothing — yet ``st_mode & 0o111`` is non-zero and an
#: any-exec-bit test reads them as already installed.  ``0o644`` (no exec bit
#: at all) CANNOT distinguish the two masks: ``& 0o111`` and ``& stat.S_IXUSR``
#: agree it is unexecutable, which is why the pre-existing 0o644 tests stayed
#: green under the buggy mask and are not evidence for this fix (#4000 R33).
_OWNER_UNEXECUTABLE_MODES = (0o601, 0o410)

_DB_ENV_VARS = (
    "TORTOISE_DB_URI", "TORTOISE_DB_PATH", "FALKORDB_HOST", "FALKORDB_PORT",
    "FALKORDB_PASSWORD",
)


def _env(tmp_path: Path) -> dict:
    """Child env: static pepper, isolated HOME, no DB lane."""
    env = {
        **os.environ,
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
        "HOME": str(tmp_path / "home"),
        "TORTOISE_DB_URI": "",
    }
    env.pop("TORTOISE_DB_PATH", None)
    return env


def _run(argv: list[str], root: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_PY, "-m", "tortoise", *argv, "--dir", str(root)],
        capture_output=True, text=True, timeout=180,
        env=_env(tmp_path), cwd=str(root),
    )


# A body that identifies the artifact as a Tortoise hook even without a
# marker — this is what the pre-#3795 installed copies look like.
_HOOK_BODY = "from tortoise.__main__ import main"


def _write_script(path: Path, version: int | None, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = f"# tortoise-hook-version: {version}\n" if version is not None else ""
    path.write_text(f"#!/usr/bin/env bash\n{marker}{body}\n")
    path.chmod(0o755)  # a real install is executable


def _old_install(tmp_path: Path, settings: dict | None = None,
                 root: Path | None = None) -> Path:
    """A realistic pre-fix install: un-markered session-start, old session-end,
    and a settings file whose SessionEnd entry has no ``timeout`` (#3801)."""
    if root is None:
        root = tmp_path / "project"
    hooks = root / ".claude" / "hooks"
    _write_script(hooks / "session-start.sh", None, f"{_HOOK_BODY} context")
    _write_script(hooks / "session-end.sh", _CURRENT - 1,
                  f"{_HOOK_BODY} capture")
    doc = settings if settings is not None else {
        "enableAllProjectHooks": True,
        "hooks": {
            "PreToolUse": [
                {"matcher": "Edit|Write",
                 "hooks": [{"type": "command", "command": "lint.sh"}]}
            ],
            "SessionEnd": [
                {"matcher": "",
                 "hooks": [{"type": "command",
                            "command": ".claude/hooks/session-end.sh"}]}
            ],
        },
    }
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "settings.json").write_text(
        json.dumps(doc, indent=2) + "\n")
    return root


def _settings(root: Path) -> dict:
    return json.loads((root / ".claude" / "settings.json").read_text())


def _our_entry(doc: dict, event: str, script: str) -> dict:
    """The inner command dict of our entry for (event, script)."""
    for entry in doc["hooks"][event]:
        if hook_install._entry_is_ours(entry, script):
            inner = hook_install._entry_command_dict(entry)
            assert inner is not None
            return inner
    raise AssertionError(f"no {event} entry for {script}: {doc['hooks'].get(event)}")


# ── 1. the shipped artifacts declare one canonical marker ───────────────


class TestMarkerContract:
    def test_every_shipped_hook_carries_exactly_one_canonical_marker(self):
        """The install contract's source of truth is the marker in the script.

        MUTATION: delete ``# tortoise-hook-version: N`` from a shipped hook →
        ``read_hook_version`` returns None and ``contract_version`` returns
        None → RED (this is #3795's acceptance criterion, executed rather than
        grepped).
        """
        version = contract_version(_LAYOUT)
        assert version is not None, (
            "shipped hooks must all declare the same canonical "
            "'# tortoise-hook-version: N' header marker"
        )
        for spec in _LAYOUT.scripts:
            assert read_hook_version(spec.source) == version, spec.name
            assert count_canonical_markers(spec.source) == 1, (
                f"{spec.name} must declare exactly one column-0 marker"
            )

    def test_indented_site_markers_are_not_the_contract_version(self, tmp_path):
        """A historical in-body ``  # tortoise-hook-version: 2`` comment must
        not be mistaken for the column-0 contract declaration.

        MUTATION: anchor the marker regex at the line start without the column
        requirement (or use ``search`` on the whole text) → this fixture's
        version parses as 9 instead of None → RED.
        """
        f = tmp_path / "x.sh"
        f.write_text("#!/usr/bin/env bash\n  # tortoise-hook-version: 9\n")
        assert read_hook_version(f) is None


# ── 2. detection (the "tell the user their install is stale" half) ───────


class TestDetection:
    def test_status_reports_pre_3795_install_and_exits_1(self, tmp_path):
        """`hooks status` exits 1 and names both halves of the drift.

        MUTATION: make ``detect_install`` return ``[]`` (or drop the
        unversioned/missing-entry findings) → exit 0 → RED.
        """
        root = _old_install(tmp_path)
        r = _run(["hooks", "status"], root, tmp_path)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "session-start.sh" in r.stdout   # #3795 un-markered script
        assert "SessionStart" in r.stdout       # missing settings entry
        assert "timeout" in r.stdout            # #3801
        assert "hooks upgrade" in r.stdout      # actionable

    def test_detect_api_reports_both_halves(self, tmp_path):
        """The library API (used by doctor) sees script + settings drift.

        MUTATION: return early after the script loop (skipping the settings
        check) → the settings findings disappear → RED.
        """
        root = _old_install(tmp_path)
        kinds = {f.kind for f in detect_install(root)}
        assert "unversioned-script" in kinds
        assert "stale-script" in kinds
        assert "missing-hook-entry" in kinds        # SessionStart
        assert "settings-no-timeout" in kinds       # SessionEnd

    def test_low_timeout_is_drift(self, tmp_path):
        """A present-but-too-small timeout is drift, not a pass.

        MUTATION: drop the ``timeout < spec.timeout`` branch → RED.
        """
        doc = {
            "hooks": {
                "SessionStart": [{"matcher": "", "hooks": [
                    {"type": "command",
                     "command": ".claude/hooks/session-start.sh",
                     "timeout": 5}]}],
                "SessionEnd": [{"matcher": "", "hooks": [
                    {"type": "command",
                     "command": ".claude/hooks/session-end.sh",
                     "timeout": 5}]}],
            }
        }
        root = _old_install(tmp_path, settings=doc)
        kinds = {f.kind for f in detect_install(root)}
        assert "settings-low-timeout" in kinds


# ── 3. upgrade (the "repair it in place" half) ──────────────────────────


class TestUpgrade:
    def test_upgrade_repairs_scripts_and_merges_timeout(self, tmp_path):
        """The end-to-end requirement: after upgrade the marker reads the
        current generation AND the settings entry carries the timeout.

        MUTATION (script half): skip the copy when the marker differs → the
        installed marker stays ``_CURRENT - 1`` → RED.
        MUTATION (settings half): skip ``_merge_settings`` → no timeout → RED.
        """
        root = _old_install(tmp_path)
        r = _run(["hooks", "upgrade"], root, tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr

        # script half — resolved marker, read back through the same parser.
        assert read_hook_version(
            root / ".claude/hooks/session-start.sh") == _CURRENT
        assert read_hook_version(
            root / ".claude/hooks/session-end.sh") == _CURRENT
        assert os.access(root / ".claude/hooks/session-start.sh", os.X_OK)

        # settings half — the #3801 timeout merged into the EXISTING entry,
        # and a missing entry added.
        doc = _settings(root)
        assert _our_entry(doc, "SessionEnd", "session-end.sh")["timeout"] == 60
        assert _our_entry(doc, "SessionStart", "session-start.sh")["timeout"] == 60
        # merge is additive — foreign hook + unrelated key survive.
        assert doc["enableAllProjectHooks"] is True
        assert doc["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "lint.sh"

    def test_status_clean_after_upgrade(self, tmp_path):
        """Upgrade resolves the drift: status flips 1 → 0.

        MUTATION: any incomplete repair leaves a blocking finding → RED.
        """
        root = _old_install(tmp_path)
        assert _run(["hooks", "status"], root, tmp_path).returncode == 1
        _run(["hooks", "upgrade"], root, tmp_path)
        r = _run(["hooks", "status"], root, tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "current" in r.stdout

    def test_upgrade_is_idempotent(self, tmp_path):
        """A second upgrade adds no duplicate entry and reports a no-op.

        MUTATION: append our entry unconditionally (drop the
        ``_entry_is_ours`` guard) → SessionEnd/SessionStart gain a duplicate →
        RED.
        """
        root = _old_install(tmp_path)
        _run(["hooks", "upgrade"], root, tmp_path)
        r2 = _run(["hooks", "upgrade"], root, tmp_path)
        assert r2.returncode == 0, r2.stdout + r2.stderr
        assert "nothing to do" in r2.stdout
        doc = _settings(root)
        assert len(doc["hooks"]["SessionStart"]) == 1
        assert len(doc["hooks"]["SessionEnd"]) == 1

    def test_upgrade_preserves_foreign_entry_in_same_event(self, tmp_path):
        """A foreign SessionEnd hook (another product) is untouched; only our
        entry gains the timeout.  The settings half is a merge, never an
        overwrite.

        MUTATION: replace ``hooks[event]`` with our registration (as a fresh
        install does) → the foreign entry is lost → RED.
        """
        doc = {
            "hooks": {
                "SessionEnd": [
                    {"matcher": "", "hooks": [
                        {"type": "command", "command": "/usr/bin/foreign.sh"}]},
                    {"matcher": "", "hooks": [
                        {"type": "command",
                         "command": ".claude/hooks/session-end.sh"}]},
                ],
            }
        }
        root = _old_install(tmp_path, settings=doc)
        upgrade_install(root)
        doc = _settings(root)
        entries = doc["hooks"]["SessionEnd"]
        assert len(entries) == 2
        foreign = [e for e in entries
                   if not hook_install._entry_is_ours(e, "session-end.sh")]
        assert len(foreign) == 1
        assert foreign[0]["hooks"][0]["command"] == "/usr/bin/foreign.sh"
        assert "timeout" not in foreign[0]["hooks"][0]

    def test_upgrade_backs_up_a_locally_modified_script(self, tmp_path):
        """A current-marker but hand-edited script is backed up before the
        shipped copy is restored — a user customization is never destroyed
        silently.

        MUTATION: drop the ``.bak`` step → no backup file → RED.
        """
        root = _old_install(tmp_path)
        mod = root / ".claude/hooks/session-end.sh"
        _write_script(mod, _CURRENT, 'echo "locally customized"')
        upgrade_install(root)
        backup = mod.with_suffix(mod.suffix + ".bak")
        assert backup.exists()
        assert "locally customized" in backup.read_text()
        assert read_hook_version(mod) == _CURRENT
        assert "locally customized" not in mod.read_text()

    def test_upgrade_backs_up_an_unversioned_customized_script(self, tmp_path):
        """An UN-MARKERED customized script is backed up too — the pre-#3795
        population this migration targets has no marker, so a backup rule
        keyed only on ``marker == current`` silently destroys it.

        MUTATION: restore the old ``content_differs and found == expected``
        backup guard → the un-markered custom script is clobbered with no
        ``.bak`` → RED.
        """
        root = _old_install(tmp_path)
        custom = root / ".claude/hooks/session-end.sh"
        _write_script(custom, None,
                      f"{_HOOK_BODY}  # MY CUSTOM LOGIC")
        upgrade_install(root)
        backup = custom.with_suffix(custom.suffix + ".bak")
        assert backup.exists(), "un-markered custom copy must be preserved"
        assert "MY CUSTOM LOGIC" in backup.read_text()
        assert read_hook_version(custom) == _CURRENT

    def test_upgrade_ignores_a_foreign_script_with_the_same_basename(self, tmp_path):
        """Another product's ``/opt/other/session-end.sh`` is NOT ours: matching
        by basename alone would set a timeout on the foreign hook and skip
        registering the real one (a silent no-capture).

        MUTATION: drop the hooks-dir suffix requirement in ``_invokes_script``
        → the foreign entry gains a timeout and no Tortoise entry is added →
        RED.
        """
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command", "command": "/opt/otherproduct/session-end.sh"}
        ]}]}}
        root = _old_install(tmp_path, settings=doc)
        upgrade_install(root)
        out = _settings(root)
        foreign = [e for e in out["hooks"]["SessionEnd"]
                   if not hook_install._entry_is_ours(
                       e, "session-end.sh", ".claude/hooks")]
        assert len(foreign) == 1
        assert "timeout" not in foreign[0]["hooks"][0]  # untouched
        # …and a REAL Tortoise entry was added.
        ours = [e for e in out["hooks"]["SessionEnd"]
                if hook_install._entry_is_ours(
                    e, "session-end.sh", ".claude/hooks")]
        assert len(ours) == 1
        assert ours[0]["hooks"][0]["timeout"] == 60

    def test_upgrade_sets_timeout_when_our_command_is_not_first(self, tmp_path):
        """A wrapper entry with several commands: the timeout lands on the
        command that invokes our script, and no duplicate entry is appended.

        MUTATION: inspect only ``hooks[0]`` (the old ``_entry_command_dict``)
        → our command keeps no timeout and a duplicate entry is added → RED.
        """
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command", "command": "/usr/bin/telemetry.sh"},
            {"type": "command", "command": ".claude/hooks/session-end.sh"},
        ]}]}}
        root = _old_install(tmp_path, settings=doc)
        upgrade_install(root)
        out = _settings(root)
        assert len(out["hooks"]["SessionEnd"]) == 1  # no duplicate
        inner = out["hooks"]["SessionEnd"][0]["hooks"]
        by_cmd = {c["command"]: c for c in inner}
        assert by_cmd[".claude/hooks/session-end.sh"]["timeout"] == 60
        assert "timeout" not in by_cmd["/usr/bin/telemetry.sh"]

    def test_upgrade_refuses_symlink_escape(self, tmp_path):
        """A `.claude/settings.json` symlinked outside the project is refused
        and the outside file is untouched (a repo symlink must not write
        through to a real user config).

        MUTATION: drop the ``_symlink_escape`` guard → the outside file is
        rewritten → RED.
        """
        outside = tmp_path / "outside" / "settings.json"
        outside.parent.mkdir(parents=True)
        outside.write_text(json.dumps({"hooks": {}, "sentinel": True}))
        root = tmp_path / "proj"
        (root / ".claude").mkdir(parents=True)
        (root / ".claude" / "settings.json").symlink_to(outside)

        r = _run(["hooks", "upgrade"], root, tmp_path)
        assert r.returncode == 1
        assert "Refusing" in r.stderr
        assert json.loads(outside.read_text()) == {"hooks": {}, "sentinel": True}

    def test_upgrade_refuses_a_directory_at_a_script_path_before_writing(self, tmp_path):
        """A directory at a script path is refused BEFORE the loop writes — a
        crash mid-loop must not leave the install half-repaired.

        MUTATION: drop the pre-loop regular-file check (or use the raw
        ``installed.read_bytes()``) → IsADirectoryError mid-loop after
        session-start.sh was already rewritten → RED.
        """
        root = _old_install(tmp_path)
        victim = root / ".claude/hooks/session-end.sh"
        victim.unlink()
        victim.mkdir()
        r = _run(["hooks", "upgrade"], root, tmp_path)
        assert r.returncode == 1
        assert "Refusing" in r.stderr and "Traceback" not in r.stderr
        # no half-repair: session-start.sh is untouched.
        assert read_hook_version(
            root / ".claude/hooks/session-start.sh") is None

    def test_upgrade_backs_up_a_stale_markered_local_edit(self, tmp_path):
        """A copy with an OLDER marker but local edits is backed up too — with
        the next marker bump every current install becomes exactly this shape.

        MUTATION: back up only when ``found == expected`` → the stale-markered
        custom copy is clobbered with no ``.bak`` → RED.
        """
        root = _old_install(tmp_path)
        mod = root / ".claude/hooks/session-end.sh"
        _write_script(mod, _CURRENT - 1, 'echo "edited at an old generation"')
        upgrade_install(root)
        backup = mod.with_suffix(mod.suffix + ".bak")
        assert backup.exists()
        assert "edited at an old generation" in backup.read_text()
        assert read_hook_version(mod) == _CURRENT

    def test_upgrade_does_not_write_through_a_planted_temp_symlink(self, tmp_path):
        """The atomic temp is ``mkstemp``-created, so a pre-planted symlink at
        the OLD deterministic ``.tortoise-tmp`` path is never followed.

        MUTATION: revert ``_atomic_copy`` to the fixed-name temp → the outside
        victim is overwritten and the installed script becomes a symlink → RED.
        """
        root = _old_install(tmp_path)
        outside = tmp_path / "outside" / "victim.sh"
        outside.parent.mkdir(parents=True)
        outside.write_text("ORIGINAL-VICTIM\n")
        planted = root / ".claude/hooks/session-end.sh.tortoise-tmp"
        planted.symlink_to(outside)
        upgrade_install(root)
        assert outside.read_text() == "ORIGINAL-VICTIM\n"
        assert not (root / ".claude/hooks/session-end.sh").is_symlink()
        assert read_hook_version(
            root / ".claude/hooks/session-end.sh") == _CURRENT

    def test_upgrade_does_not_write_through_a_planted_bak_symlink(self, tmp_path):
        """A DANGLING symlink planted at ``<name>.bak`` is not followed: the
        atomic backup replaces the entry instead of creating its target.

        MUTATION: ``shutil.copyfile(installed, backup)`` → the dangling link's
        outside target is created/overwritten with the hook bytes → RED.
        """
        root = _old_install(tmp_path)
        outside = tmp_path / "outside" / "precious.txt"  # does NOT exist yet
        outside.parent.mkdir(parents=True)
        (root / ".claude/hooks/session-end.sh.bak").symlink_to(outside)
        upgrade_install(root)
        assert not outside.exists(), "backup wrote through a dangling symlink"

    def test_bare_name_command_is_treated_as_foreign(self, tmp_path):
        """A bare ``session-end.sh`` resolves to the PROJECT ROOT, not
        ``.claude/hooks/`` — treating it as ours would suppress our real
        registration and report a healthy install that files nothing.

        MUTATION: accept a token with no directory component → the bare entry
        is "ours", gets the timeout, and no canonical entry is added → RED.
        """
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command", "command": "session-end.sh"}]}]}}
        root = _old_install(tmp_path, settings=doc)
        findings = detect_install(root)
        assert [f for f in findings
                if f.kind == "missing-hook-entry"
                and f.script == "session-end.sh"]
        upgrade_install(root)
        out = _settings(root)
        # our canonical entry was added; the bare foreign entry is untouched.
        assert len(out["hooks"]["SessionEnd"]) == 2
        bare = [e for e in out["hooks"]["SessionEnd"]
                if not hook_install._entry_is_ours(
                    e, "session-end.sh", ".claude/hooks")]
        assert len(bare) == 1
        assert "timeout" not in bare[0]["hooks"][0]

    def test_upgrade_refuses_a_directory_at_the_settings_path(self, tmp_path):
        """A directory sitting at ``settings.json`` is refused cleanly — never a
        traceback (``_load_settings`` catches OSError, not just ValueError).

        MUTATION: revert ``_load_settings`` to catch only ``ValueError`` →
        ``IsADirectoryError`` escapes and the CLI prints a traceback → RED.
        """
        root = _old_install(tmp_path)
        settings = root / ".claude/settings.json"
        settings.unlink()
        settings.mkdir()
        r = _run(["hooks", "upgrade"], root, tmp_path)
        assert r.returncode == 1
        assert "Refusing" in r.stderr
        assert "Traceback" not in r.stderr
        assert "Traceback" not in r.stdout
        r2 = _run(["hooks", "status"], root, tmp_path)
        assert "Traceback" not in r2.stderr and "Traceback" not in r2.stdout

    def test_upgrade_breaks_a_hard_link_without_touching_the_other_link(self, tmp_path):
        """A hard-linked hook target must not write through to the other link:
        the atomic replace swaps the directory entry, leaving the outside file
        intact.

        MUTATION: use ``shutil.copyfile(src, installed)`` (writes through the
        inode) → the outside file is truncated to the shipped hook bytes → RED.
        """
        root = _old_install(tmp_path)
        outside = tmp_path / "outside" / "precious.sh"
        outside.parent.mkdir(parents=True)
        outside.write_text(
            "#!/usr/bin/env bash\n" + _HOOK_BODY + "  # PRECIOUS\n")
        link = root / ".claude/hooks/session-end.sh"
        link.unlink()
        os.link(outside, link)
        upgrade_install(root)
        assert "PRECIOUS" in outside.read_text()  # other link untouched
        assert read_hook_version(link) == _CURRENT

    def test_upgrade_refuses_to_replace_a_foreign_script(self, tmp_path):
        """An existing file at OUR path that is not a Tortoise hook belongs to
        another product — upgrade refuses instead of silently deactivating it.

        MUTATION: drop the ownership guard → the foreign hook is overwritten
        and rc is 0 → RED.
        """
        root = _old_install(tmp_path)
        foreign = root / ".claude/hooks/session-end.sh"
        foreign.write_text("#!/usr/bin/env bash\necho another-product-hook\n")
        r = _run(["hooks", "upgrade"], root, tmp_path)
        assert r.returncode == 1
        assert "Refusing" in r.stderr and "Traceback" not in r.stderr
        assert "another-product-hook" in foreign.read_text()

    def test_upgrade_does_not_half_repair_when_a_target_dir_is_unwritable(self, tmp_path):
        """An unwritable settings directory aborts BEFORE any script write, so
        the install is never left half-repaired (scripts new, settings old).

        MUTATION: drop the ``_probe_writable`` pre-flight → the scripts are
        rewritten first and the settings write raises afterwards → the
        un-markered session-start.sh is upgraded despite rc 1 → RED.
        """
        root = _old_install(tmp_path)
        claude_dir = root / ".claude"
        before_mode = claude_dir.stat().st_mode
        os.chmod(claude_dir, 0o500)
        try:
            r = _run(["hooks", "upgrade"], root, tmp_path)
        finally:
            os.chmod(claude_dir, before_mode)
        assert r.returncode == 1
        assert "Refusing" in r.stderr and "Traceback" not in r.stderr
        # Nothing was written: the un-markered session-start.sh is untouched.
        assert read_hook_version(
            root / ".claude/hooks/session-start.sh") is None

    def test_upgrade_preserves_the_settings_file_mode(self, tmp_path):
        """Merging must not silently narrow a shared settings.json to 0600
        (``mkstemp``'s default).

        MUTATION: drop the mode capture in ``_atomic_write_text`` → the file
        becomes 0600 → RED.
        """
        root = _old_install(tmp_path)
        settings = root / ".claude" / "settings.json"
        os.chmod(settings, 0o640)
        upgrade_install(root)
        assert (settings.stat().st_mode & 0o777) == 0o640

    def test_upgrade_refuses_an_inside_root_symlink(self, tmp_path):
        """A symlink BELOW root — even one pointing inside the project — is
        refused: replacing it would silently turn a live symlink install into
        a stale regular-file copy (contradicting the refusal message).

        MUTATION: refuse only symlinks that ESCAPE root → the inside symlink
        is replaced by a regular file → RED.
        """
        root = _old_install(tmp_path)
        real = root / ".claude/hooks/real-source.sh"
        _write_script(real, None, _HOOK_BODY)
        link = root / ".claude/hooks/session-end.sh"
        link.unlink()
        link.symlink_to(real)
        r = _run(["hooks", "upgrade"], root, tmp_path)
        assert r.returncode == 1
        assert "Refusing" in r.stderr
        assert link.is_symlink() and link.resolve() == real.resolve()

    def test_detect_reports_a_non_regular_file_at_a_script_path(self, tmp_path):
        """A FIFO at a script path is reported as drift without being read (a
        blocking read of a FIFO would hang `tortoise hooks status`).

        MUTATION: drop the ``is_file`` guard in ``read_hook_version`` / the
        detect loop → ``read_text`` on the FIFO blocks → RED (hang).
        """
        root = _old_install(tmp_path)
        victim = root / ".claude/hooks/session-start.sh"
        victim.unlink()
        os.mkfifo(victim)
        findings = detect_install(root)
        kinds = [f.kind for f in findings if f.script == "session-start.sh"]
        assert kinds and kinds[0] == "not-a-regular-file"
        assert "unversioned-script" not in kinds  # not read as empty text

    def test_flat_event_entry_is_not_a_registration(self, tmp_path):
        """A flat ``{type, command}`` directly under the event key is silently
        ignored by Claude Code (the schema needs a matcher with a ``hooks``
        array), so it must NOT be treated as our registration — upgrade adds a
        valid nested entry instead of declaring the install current.

        MUTATION: accept a flat entry in ``_entry_command_dict`` → no entry is
        added and status reports current while nothing is ever filed → RED.
        """
        doc = {"hooks": {"SessionEnd": [
            {"type": "command", "command": ".claude/hooks/session-end.sh"}
        ]}}
        root = _old_install(tmp_path, settings=doc)
        before = detect_install(root)
        assert any(
            f.kind == "missing-hook-entry" and f.script == "session-end.sh"
            for f in before)
        upgrade_install(root)
        entries = _settings(root)["hooks"]["SessionEnd"]
        assert len(entries) == 2  # flat foreign entry + our nested matcher
        ours = _our_entry(_settings(root), "SessionEnd", "session-end.sh")
        assert ours["timeout"] == 60 and ours["type"] == "command"

    def test_handler_without_command_type_is_not_a_registration(self, tmp_path):
        """Claude's schema requires ``type: "command"``; a handler missing it
        is ignored, so it must not count as our registration.

        MUTATION: make ``_ok`` check only ``command`` → the malformed handler
        is accepted, no valid entry is added → RED.
        """
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"command": ".claude/hooks/session-end.sh", "timeout": 60}]}]}}
        root = _old_install(tmp_path, settings=doc)
        assert any(
            f.kind == "missing-hook-entry" and f.script == "session-end.sh"
            for f in detect_install(root))
        upgrade_install(root)
        ours = _our_entry(_settings(root), "SessionEnd", "session-end.sh")
        assert ours["type"] == "command" and ours["timeout"] == 60

    def test_argument_position_reference_is_not_a_registration(self, tmp_path):
        """A token that merely PASSES our path as an argument
        (``echo .claude/hooks/session-end.sh``) is not an executed hook and
        must not suppress our registration.

        MUTATION: match the script name in ANY token position → no entry is
        added, rc 0, silent no-capture → RED.
        """
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command",
             "command": "echo .claude/hooks/session-end.sh"}]}]}}
        root = _old_install(tmp_path, settings=doc)
        assert any(
            f.kind == "missing-hook-entry" and f.script == "session-end.sh"
            for f in detect_install(root))
        upgrade_install(root)
        ours = _our_entry(_settings(root), "SessionEnd", "session-end.sh")
        assert ours["timeout"] == 60

    def test_absolute_path_without_a_variable_prefix_is_foreign(self, tmp_path):
        """``/opt/other/.claude/hooks/session-end.sh`` is another project's
        copy, not ours.

        MUTATION: accept any absolute path whose tail is ``.claude/hooks`` →
        the foreign hook gets the timeout and ours is never registered → RED.
        """
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command",
             "command": "/opt/other/.claude/hooks/session-end.sh"}]}]}}
        root = _old_install(tmp_path, settings=doc)
        upgrade_install(root)
        assert len(_settings(root)["hooks"]["SessionEnd"]) == 2

    def test_non_executable_current_script_is_repaired(self, tmp_path):
        """A byte-identical but non-executable hook cannot run — status must
        flag it and upgrade must chmod it.

        MUTATION: drop the exec-bit check from detect and/or the mode-only
        repair → status says current and the file stays 0644 → RED.
        """
        spec = next(s for s in _LAYOUT.scripts if s.name == "session-end.sh")
        root = _old_install(tmp_path)
        installed = root / ".claude/hooks/session-end.sh"
        installed.write_bytes(spec.source.read_bytes())
        installed.chmod(0o644)
        assert any(f.kind == "not-executable"
                   for f in detect_install(root))
        upgrade_install(root)
        assert installed.stat().st_mode & 0o111
        assert not any(f.kind == "not-executable" for f in detect_install(root))

    @pytest.mark.parametrize("mode", _OWNER_UNEXECUTABLE_MODES,
                             ids=[f"{m:o}" for m in _OWNER_UNEXECUTABLE_MODES])
    def test_non_owner_exec_bit_is_stale_and_repaired(self, tmp_path, mode):
        """A byte-identical hook whose ONLY exec bit is a non-owner bit is NOT
        installed: the harness runs as the owner, cannot execute it, and the
        fail-open script files nothing while status says "current".

        MUTATION: revert the shared predicate to ``st_mode & 0o111`` — for
        ``0o601``/``0o410`` ``detect_install`` returns no ``not-executable``
        finding (first assertion RED) AND ``upgrade_install``'s mode-only
        repair is skipped, so the hook stays unexecutable by its owner
        (second assertion RED).  ``0o644`` cannot show this; see
        ``_OWNER_UNEXECUTABLE_MODES``.
        """
        spec = next(s for s in _LAYOUT.scripts if s.name == "session-end.sh")
        root = _old_install(tmp_path)
        installed = root / ".claude/hooks/session-end.sh"
        installed.write_bytes(spec.source.read_bytes())  # bytes are current
        installed.chmod(mode)
        assert not os.access(installed, os.X_OK), (
            f"fixture bug: a {mode:o} file is already owner-executable")

        findings = detect_install(root)
        assert any(f.kind == "not-executable" and f.blocking
                   for f in findings), (
            f"a {mode:o} hook the owner cannot execute was not reported "
            f"stale: {[(f.kind, f.script) for f in findings]}")

        result = upgrade_install(root)
        assert result.ok, result.refused
        assert installed.stat().st_mode & stat.S_IXUSR, (
            f"upgrade left the {mode:o} hook unexecutable by its owner "
            f"(mode {installed.stat().st_mode & 0o777:o})")
        assert os.access(installed, os.X_OK)
        assert installed.read_bytes() == spec.source.read_bytes()
        assert not any(f.kind == "not-executable"
                       for f in detect_install(root))

    @pytest.mark.parametrize("mode", _OWNER_UNEXECUTABLE_MODES,
                             ids=[f"{m:o}" for m in _OWNER_UNEXECUTABLE_MODES])
    def test_ahead_hook_with_non_owner_exec_bit_is_repaired(self, tmp_path, mode):
        """The second plan branch: an AHEAD hook (never downgraded) whose only
        exec bit is a non-owner bit must still get the mode-only repair —
        otherwise it files nothing and status's advertised ``upgrade`` never
        converges.

        MUTATION: same predicate revert as the test above, but the assertion
        that REDs is in the ``ahead-script`` branch — with ``& 0o111`` the
        ``missing_exec`` plan entry is never added, so the mode stays
        unexecutable and the ``ahead`` version is the only surviving property.
        """
        root = _old_install(tmp_path)
        ahead = root / ".claude/hooks/session-end.sh"
        _write_script(ahead, _CURRENT + 5, _HOOK_BODY)
        ahead.chmod(mode)
        assert not os.access(ahead, os.X_OK), (
            f"fixture bug: a {mode:o} file is already owner-executable")

        findings = detect_install(root)
        assert any(f.kind == "not-executable" for f in findings)
        assert any(f.kind == "ahead-script" for f in findings)
        result = upgrade_install(root)
        assert result.ok, result.refused
        assert ahead.stat().st_mode & stat.S_IXUSR, (
            f"upgrade left the ahead {mode:o} hook unexecutable by its owner")
        assert read_hook_version(ahead) == _CURRENT + 5  # not downgraded
        assert not any(f.kind == "not-executable"
                       for f in detect_install(root))

    def test_status_notes_a_symlinked_install(self, tmp_path):
        """A symlinked hook is functionally current (it tracks the source) but
        upgrade refuses it — status must say so instead of "current".

        MUTATION: drop the ``symlinked-script`` finding → status reports
        current while upgrade refuses → RED (the disagreement cycle 4 found).
        """
        root = _old_install(tmp_path)
        real = root / ".claude/hooks/real-source.sh"
        _write_script(real, None, _HOOK_BODY)
        link = root / ".claude/hooks/session-end.sh"
        link.unlink()
        link.symlink_to(real)
        findings = detect_install(root)
        notes = [f for f in findings if f.kind == "symlinked-script"]
        assert notes and not notes[0].blocking

    def test_fifo_at_the_settings_path_does_not_hang(self, tmp_path):
        """A FIFO at ``settings.json`` must be refused without a blocking read.

        MUTATION: drop the ``is_file`` guard in ``_load_settings`` (use
        ``path.read_text()`` directly) → the read blocks forever → the
        subprocess times out → RED.
        """
        root = _old_install(tmp_path)
        settings = root / ".claude" / "settings.json"
        settings.unlink()
        os.mkfifo(settings)
        try:
            proc = subprocess.run(
                [_PY, "-m", "tortoise", "hooks", "status",
                 "--dir", str(root)],
                capture_output=True, text=True, timeout=20,
                env=_env(tmp_path), cwd=str(root),
            )
        except subprocess.TimeoutExpired:  # pragma: no cover - regression
            pytest.fail("hooks status hung on a FIFO settings path")
        assert proc.returncode == 1
        assert "not a regular file" in (proc.stdout + proc.stderr)
        assert "Traceback" not in proc.stderr

    def test_invalid_utf8_settings_is_refused_not_rewritten(self, tmp_path):
        """A settings file with invalid UTF-8 must be REFUSED (no traceback)
        and left byte-identical — decoding with ``errors="replace"`` would
        silently rewrite unrelated values on the merge write-back.

        MUTATION: decode with ``errors="replace"`` → the file is rewritten and
        its bytes change → RED; decode strictly without catching → traceback →
        RED.
        """
        root = _old_install(tmp_path)
        settings = root / ".claude" / "settings.json"
        raw = b'{"hooks": {}, "note": "\xff\xfe"}'
        settings.write_bytes(raw)
        r = _run(["hooks", "status"], root, tmp_path)
        assert "Traceback" not in r.stderr and "Traceback" not in r.stdout
        assert r.returncode == 1
        r2 = _run(["hooks", "upgrade"], root, tmp_path)
        assert "Traceback" not in r2.stderr and "Traceback" not in r2.stdout
        assert r2.returncode == 1
        assert settings.read_bytes() == raw

    def test_symlinked_hook_without_exec_bit_is_not_current(self, tmp_path):
        """A symlink whose TARGET lacks the exec bit cannot run — status must
        fail, not report current (the exec check followed the link but was an
        ``elif`` skipped for symlinks).

        MUTATION: keep the exec check as an ``elif`` on the symlink branch →
        no ``not-executable-symlink`` finding, rc 0 → RED.
        """
        root = _old_install(tmp_path)
        real = root / ".claude/hooks/real-source.sh"
        _write_script(real, None, _HOOK_BODY)
        real.chmod(0o644)
        link = root / ".claude/hooks/session-end.sh"
        link.unlink()
        link.symlink_to(real)
        findings = detect_install(root)
        assert any(f.kind == "not-executable-symlink" and f.blocking
                   for f in findings)
        r = _run(["hooks", "status"], root, tmp_path)
        assert r.returncode == 1
        assert "manual fix" in r.stdout  # upgrade refuses symlinks

    def test_hooks_dir_symlink_is_reported_by_detect(self, tmp_path):
        """A directory-level symlink install (``.claude/hooks`` → repo) is
        invisible to the per-file loop — detect must still note it, or status
        says current while upgrade refuses.

        MUTATION: only emit ``symlinked-script`` for the final file component →
        detect returns [] for a directory symlink → RED.
        """
        real = tmp_path / "repo-hooks"
        real.mkdir()
        _write_script(real / "session-start.sh", None, _HOOK_BODY)
        _write_script(real / "session-end.sh", _CURRENT, _HOOK_BODY)
        root = tmp_path / "project"
        (root / ".claude").mkdir(parents=True)
        (root / ".claude" / "hooks").symlink_to(real, target_is_directory=True)
        (root / ".claude" / "settings.json").write_text(json.dumps({
            "hooks": {"SessionEnd": [{"matcher": "", "hooks": [
                {"type": "command",
                 "command": ".claude/hooks/session-end.sh",
                 "timeout": 60}]}]}}))
        findings = detect_install(root)
        notes = [f for f in findings if f.kind == "symlinked-install"]
        assert notes and not notes[0].blocking

    @pytest.mark.parametrize("command", [
        "timeout 5 .claude/hooks/session-end.sh",
        "( .claude/hooks/session-end.sh )",
        "bash .claude/hooks/session-end.sh",
        "$CLAUDE_PROJECT_DIR/.claude/hooks/session-end.sh",
        "${CLAUDE_PROJECT_DIR}/.claude/hooks/session-end.sh",
        "A=/x/y .claude/hooks/session-end.sh",
        "PATH=/usr/local/bin:$PATH .claude/hooks/session-end.sh",
        "env -u FOO .claude/hooks/session-end.sh",
        "sudo -u root .claude/hooks/session-end.sh",
        "timeout -s KILL 60 .claude/hooks/session-end.sh",
        "true; .claude/hooks/session-end.sh",
        "bash -eux .claude/hooks/session-end.sh",
        "sh -e .claude/hooks/session-end.sh",
        "` .claude/hooks/session-end.sh `",
        "bash -c 'true; .claude/hooks/session-end.sh'",
        "bash -e -c 'true; .claude/hooks/session-end.sh'",
        "2>/dev/null .claude/hooks/session-end.sh",
        "&> /dev/null .claude/hooks/session-end.sh",
        "command .claude/hooks/session-end.sh",
        "eval .claude/hooks/session-end.sh",
        ".claude/hooks/session-end.sh <<< data",
    ])
    def test_executed_noncanonical_reference_is_still_ours(self, tmp_path, command):
        """Commands that really EXECUTE the hook — wrapped in a launcher or a
        subshell — must not be treated as foreign (that adds a DUPLICATE
        registration and invokes SessionEnd twice).

        MUTATION: drop ``timeout``/``(`` from ``_LAUNCHERS`` (or the numeric
        launcher operand skip) → the wrapped form is foreign → a second entry
        is appended → RED.
        """
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command", "command": command, "timeout": 60}]}]}}
        root = _old_install(tmp_path, settings=doc)
        assert not [f for f in detect_install(root)
                    if f.kind == "missing-hook-entry"
                    and f.script == "session-end.sh"]
        upgrade_install(root)
        assert len(_settings(root)["hooks"]["SessionEnd"]) == 1

    def test_absolute_path_to_this_project_is_ours(self, tmp_path):
        """An absolute path that resolves to THIS project's hook is ours (only
        a FOREIGN absolute path is rejected) — otherwise a duplicate is added.

        MUTATION: reject every absolute token unconditionally → the entry is
        foreign → a second SessionEnd entry is appended → RED.
        """
        root = _old_install(tmp_path)
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command",
             "command": str(root / ".claude/hooks/session-end.sh"),
             "timeout": 60}]}]}}
        (root / ".claude" / "settings.json").write_text(json.dumps(doc))
        assert not [f for f in detect_install(root)
                    if f.kind == "missing-hook-entry"
                    and f.script == "session-end.sh"]
        upgrade_install(root)
        assert len(_settings(root)["hooks"]["SessionEnd"]) == 1

    def test_mode_repair_adds_exec_bits_without_broadening(self, tmp_path):
        """The exec-bit repair must not widen 0600 to 0755 — it adds exec bits
        to the existing mode.

        MUTATION: ``os.chmod(installed, 0o755)`` → the mode becomes 0755 → RED.
        """
        spec = next(s for s in _LAYOUT.scripts if s.name == "session-end.sh")
        root = _old_install(tmp_path)
        installed = root / ".claude/hooks/session-end.sh"
        installed.write_bytes(spec.source.read_bytes())
        installed.chmod(0o600)
        upgrade_install(root)
        assert (installed.stat().st_mode & 0o777) == 0o711

    def test_status_does_not_recommend_upgrade_for_manual_findings(self, tmp_path):
        """For a directory at ``settings.json`` upgrade refuses, so status must
        not print "Run `tortoise hooks upgrade` to repair".

        MUTATION: print the upgrade hint whenever any blocking finding exists
        → the advice points at a command that refuses → RED.
        """
        root = _old_install(tmp_path)
        settings = root / ".claude" / "settings.json"
        settings.unlink()
        settings.mkdir()
        r = _run(["hooks", "status"], root, tmp_path)
        assert r.returncode == 1
        assert "to repair." not in r.stdout
        assert "manual fix" in r.stdout

    def test_unreadable_script_is_refused_not_raised(self, tmp_path):
        """An unreadable installed script must be REFUSED, not raise
        ``PermissionError`` out of the public ``upgrade_install`` (its contract
        is refuse-never-raise), and `detect` already tolerates it.

        MUTATION: drop the readability guard / use raw ``installed.read_bytes()``
        → ``PermissionError`` escapes → RED.
        """
        root = _old_install(tmp_path)
        victim = root / ".claude/hooks/session-end.sh"
        victim.chmod(0o200)
        findings = detect_install(root)  # must not raise
        assert any(f.kind == "not-readable" for f in findings)
        result = upgrade_install(root)  # must not raise
        assert not result.ok and "not readable" in result.refused

    def test_symlink_plus_blocking_finding_does_not_recommend_upgrade(self, tmp_path):
        """A symlinked install with an independent blocking finding must not
        print the `upgrade` hint (upgrade refuses on the symlink).

        MUTATION: gate the manual hint only on finding KIND → the hint says
        "run upgrade to repair" while upgrade refuses → RED.
        """
        real = tmp_path / "repo-hooks"
        real.mkdir()
        _write_script(real / "session-start.sh", 5, _HOOK_BODY)  # stale
        _write_script(real / "session-end.sh", _CURRENT, _HOOK_BODY)
        root = tmp_path / "project"
        (root / ".claude").mkdir(parents=True)
        (root / ".claude" / "hooks").symlink_to(real, target_is_directory=True)
        (root / ".claude" / "settings.json").write_text(json.dumps({
            "hooks": {"SessionEnd": [{"matcher": "", "hooks": [
                {"type": "command",
                 "command": ".claude/hooks/session-end.sh",
                 "timeout": 60}]}]}}))
        r = _run(["hooks", "status"], root, tmp_path)
        assert r.returncode == 1
        assert "to repair." not in r.stdout
        assert "manual fix" in r.stdout

    def test_symlinked_settings_json_is_reported_by_detect(self, tmp_path):
        """A symlinked ``settings.json`` is invisible to the per-file reads but
        upgrade refuses it — detect must note it.

        MUTATION: drop the ``symlinked-settings`` finding → detect returns []
        (status ✅) while upgrade refuses → RED.
        """
        outside = tmp_path / "outside" / "settings.json"
        outside.parent.mkdir(parents=True)
        outside.write_text(json.dumps({"hooks": {}}))
        root = tmp_path / "project"
        (root / ".claude").mkdir(parents=True)
        (root / ".claude" / "settings.json").symlink_to(outside)
        findings = detect_install(root)
        notes = [f for f in findings if f.kind == "symlinked-settings"]
        assert notes and not notes[0].blocking

    def test_foreign_unmarkered_script_is_manual_not_unversioned(self, tmp_path):
        """A foreign hook at our path must be `foreign-script` (manual) —
        reporting it as a pre-#3795 copy makes status recommend an `upgrade`
        that refuses.

        MUTATION: classify every markerless file as ``unversioned-script`` →
        status prints "to repair." for a file upgrade refuses → RED.
        """
        root = _old_install(tmp_path)
        foreign = root / ".claude/hooks/session-start.sh"
        foreign.write_text("#!/usr/bin/env bash\necho their-own-hook\n")
        findings = detect_install(root)
        assert any(f.kind == "foreign-script" and f.blocking for f in findings)
        r = _run(["hooks", "status"], root, tmp_path)
        assert r.returncode == 1
        assert "to repair." not in r.stdout
        assert "foreign-script" in r.stdout

    def test_broken_symlink_at_a_script_path_is_manual(self, tmp_path):
        """A broken symlink is not `missing-script` — upgrade refuses symlinks,
        so status must not recommend it.

        MUTATION: check ``exists()`` before ``is_symlink()`` → the broken link
        is `missing-script` and status says "to repair." → RED.
        """
        root = _old_install(tmp_path)
        victim = root / ".claude/hooks/session-end.sh"
        victim.unlink()
        victim.symlink_to(root / ".claude/hooks/gone.sh")
        findings = detect_install(root)
        assert any(f.kind == "symlinked-script" and f.blocking
                   for f in findings)
        r = _run(["hooks", "status"], root, tmp_path)
        assert r.returncode == 1
        assert "to repair." not in r.stdout
        assert "manual fix" in r.stdout

    def test_quoted_punctuation_is_not_a_command_boundary(self, tmp_path):
        """A quoted ``'('`` is an ARGUMENT, not a subshell — so
        ``grep -e '(' .claude/hooks/session-end.sh`` must NOT count as our
        registration (it would stamp a timeout on a foreign grep and register
        nothing that runs the hook).

        MUTATION: use quote-blind tokenization (shlex/punctuation_chars) → the
        quoted paren resets executable position and the entry is judged ours →
        no real entry is added → RED.
        """
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command",
             "command": "grep -e '(' .claude/hooks/session-end.sh"}]}]}}
        root = _old_install(tmp_path, settings=doc)
        assert any(
            f.kind == "missing-hook-entry" and f.script == "session-end.sh"
            for f in detect_install(root))
        upgrade_install(root)
        entries = _settings(root)["hooks"]["SessionEnd"]
        assert len(entries) == 2  # foreign grep entry + our real entry
        grep_entry = [e for e in entries if "grep" in e.get("hooks", [{}])[0]
                      .get("command", "")]
        assert grep_entry and "timeout" not in grep_entry[0]["hooks"][0]

    @pytest.mark.parametrize("command", [
        "bash -euo pipefail .claude/hooks/session-end.sh",
        "sh -euxo pipefail .claude/hooks/session-end.sh",
        "true\n.claude/hooks/session-end.sh",
    ])
    def test_combined_flags_and_newline_separators(self, tmp_path, command):
        """Combined short flags (``-euo pipefail``) hide a separate option
        value and an unquoted newline separates commands — neither must turn a
        real invocation into a foreign entry (duplicate registration).

        MUTATION: drop the combined-short-flag or newline handling → the entry
        is foreign → a second SessionEnd entry is appended → RED.
        """
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command", "command": command, "timeout": 60}]}]}}
        root = _old_install(tmp_path, settings=doc)
        assert not [f for f in detect_install(root)
                    if f.kind == "missing-hook-entry"
                    and f.script == "session-end.sh"]
        upgrade_install(root)
        assert len(_settings(root)["hooks"]["SessionEnd"]) == 1

    def test_manual_note_names_the_actual_symlink_kind(self, tmp_path):
        """The manual note must name the real kind (``symlinked-script``),
        not a hardcoded ``symlinked-install``.

        MUTATION: hardcode ``{\"symlinked-install\"}`` → the note names the
        wrong finding → RED.
        """
        root = _old_install(tmp_path)
        real = root / ".claude/hooks/real-source.sh"
        _write_script(real, None, _HOOK_BODY)
        real.chmod(0o644)
        link = root / ".claude/hooks/session-end.sh"
        link.unlink()
        link.symlink_to(real)
        r = _run(["hooks", "status"], root, tmp_path)
        assert r.returncode == 1
        assert "symlinked-script" in r.stdout
        assert "symlinked-install" not in r.stdout

    @pytest.mark.parametrize("command", [
        "cat <<'EOF'\n.claude/hooks/session-end.sh\nEOF",
        "cat <<EOF\n.claude/hooks/session-end.sh\nEOF",
        ".claude/hooks/session-end.sh 'unterminated",
        ";.claude/hooks/session-end.sh",
        "5 .claude/hooks/session-end.sh",
        "command -v .claude/hooks/session-end.sh",
        "2> .claude/hooks/session-end.sh",
        "> .claude/hooks/session-end.sh",
        "bash -c 'echo' .claude/hooks/session-end.sh",
        "bash -n .claude/hooks/session-end.sh",
        "sh -n .claude/hooks/session-end.sh",
        "nice 5 .claude/hooks/session-end.sh",
    ])
    def test_non_executing_command_is_not_a_registration(self, tmp_path, command):
        """Heredoc bodies, unterminated quotes and a leading separator execute
        nothing — none may be judged our registration (that would report a
        broken install as current while filing nothing).

        MUTATION: drop the ``<<`` guard / unterminated-quote ``None`` /
        leading-separator guard → the entry is judged ours → no real entry is
        added → RED.
        """
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command", "command": command, "timeout": 60}]}]}}
        root = _old_install(tmp_path, settings=doc)
        assert any(
            f.kind == "missing-hook-entry" and f.script == "session-end.sh"
            for f in detect_install(root))
        upgrade_install(root)
        assert len(_settings(root)["hooks"]["SessionEnd"]) == 2

    def test_nul_and_symlink_loop_commands_do_not_raise(self, tmp_path):
        """A JSON-valid command containing a NUL byte or a symlink loop must
        not raise ``ValueError``/``RuntimeError`` out of detect/upgrade (the
        contract is refuse-never-raise).

        MUTATION: catch only ``OSError`` around ``resolve()`` → the CLI
        tracebacks → RED.
        """
        root = _old_install(tmp_path)
        a = root / ".claude/hooks/a"
        a.symlink_to(root / ".claude/hooks/b")
        (root / ".claude/hooks/b").symlink_to(a)
        for command in (str(a / "session-end.sh"),
                        "/tmp/\u0000/session-end.sh"):
            doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
                {"type": "command", "command": command,
                 "timeout": 60}]}]}}
            (root / ".claude" / "settings.json").write_text(json.dumps(doc))
            r = _run(["hooks", "status"], root, tmp_path)
            assert "Traceback" not in r.stderr and "Traceback" not in r.stdout
            r2 = _run(["hooks", "upgrade"], root, tmp_path)
            assert "Traceback" not in r2.stderr and "Traceback" not in r2.stdout

    def test_unreadable_tortoise_script_is_not_called_foreign(self, tmp_path):
        """An unreadable Tortoise hook must report ``not-readable`` only — not
        ``foreign-script`` (which would tell the user to discard their own
        hook).

        MUTATION: emit ``foreign-script`` for any markerless file → the chmod
        0200 Tortoise hook is mislabelled foreign → RED.
        """
        root = _old_install(tmp_path)
        victim = root / ".claude/hooks/session-end.sh"
        victim.chmod(0o200)
        kinds = [f.kind for f in detect_install(root) if f.script == "session-end.sh"]
        assert "not-readable" in kinds
        assert "foreign-script" not in kinds

    def test_relative_path_to_another_project_is_foreign(self, tmp_path):
        """``vendor/.claude/hooks/session-end.sh`` is a DIFFERENT file — a
        relative path must be resolved against the project root, not matched
        by directory suffix (suffix-matching stamped a timeout on the foreign
        entry and never registered ours: silent no-capture).

        MUTATION: fall back to suffix-matching relative paths → the vendor
        entry is judged ours → no real entry is added → RED.
        """
        root = _old_install(tmp_path)
        vendor = root / "vendor" / ".claude" / "hooks"
        _write_script(vendor / "session-end.sh", None, _HOOK_BODY)
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command",
             "command": "vendor/.claude/hooks/session-end.sh",
             "timeout": 60}]}]}}
        (root / ".claude" / "settings.json").write_text(json.dumps(doc))
        assert any(
            f.kind == "missing-hook-entry" and f.script == "session-end.sh"
            for f in detect_install(root))
        upgrade_install(root)
        entries = _settings(root)["hooks"]["SessionEnd"]
        assert len(entries) == 2
        ours = _our_entry(_settings(root), "SessionEnd", "session-end.sh")
        assert ours["timeout"] == 60

    def test_all_our_commands_in_one_entry_are_timed(self, tmp_path):
        """A single entry invoking the hook twice: the SECOND child must be
        timed too — checking only the first leaves it cancelled at 1.5s while
        detect reports current.

        MUTATION: inspect only the first matching child → detect finds
        nothing and the second child stays untimed → RED.
        """
        root = _old_install(tmp_path, settings={"hooks": {"SessionEnd": [
            {"matcher": "", "hooks": [
                {"type": "command",
                 "command": ".claude/hooks/session-end.sh", "timeout": 60},
                {"type": "command",
                 "command": ".claude/hooks/session-end.sh"},
            ]}]}})
        assert any(f.kind == "settings-no-timeout"
                   for f in detect_install(root))
        upgrade_install(root)
        inner = _settings(root)["hooks"]["SessionEnd"][0]["hooks"]
        assert [c["timeout"] for c in inner] == [60, 60]

    def test_ahead_script_with_lost_exec_bit_is_repaired(self, tmp_path):
        """An AHEAD script (never downgraded) with a lost exec bit must still
        get the mode-only repair — otherwise it files nothing and status's
        advertised `upgrade` never converges.

        MUTATION: `continue` on ahead before planning the mode repair → the
        exec bit stays 0 and `upgrade_install` reports ok with a blocking
        `not-executable` left → RED.
        """
        root = _old_install(tmp_path)
        ahead = root / ".claude/hooks/session-end.sh"
        _write_script(ahead, _CURRENT + 5, _HOOK_BODY)
        ahead.chmod(0o644)
        findings = detect_install(root)
        assert any(f.kind == "not-executable" for f in findings)
        assert any(f.kind == "ahead-script" for f in findings)
        result = upgrade_install(root)
        assert result.ok
        assert ahead.stat().st_mode & 0o111
        assert read_hook_version(ahead) == _CURRENT + 5  # not downgraded
        assert not any(f.kind == "not-executable"
                       for f in detect_install(root))

    def test_rewrite_preserves_the_existing_script_mode(self, tmp_path):
        """A stale hook installed 0700 must not be broadened to 0755 on
        rewrite (symmetry with ``_atomic_write_text``'s mode preservation).

        MUTATION: hardcode 0o755 in ``_atomic_copy`` → the mode becomes 0755
        → RED.
        """
        root = _old_install(tmp_path)
        stale = root / ".claude/hooks/session-end.sh"
        stale.chmod(0o700)
        upgrade_install(root)
        assert (stale.stat().st_mode & 0o777) == 0o711

    def test_dry_run_output_has_a_single_prefix(self, tmp_path):
        """``--dry-run`` prints ``[dry-run] would write …`` exactly once.

        MUTATION: prefix the already-prefixed actions in the CLI →
        ``[dry-run] [dry-run]`` → RED.
        """
        root = _old_install(tmp_path)
        r = _run(["hooks", "upgrade", "--dry-run"], root, tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "[dry-run] would write" in r.stdout
        assert "[dry-run] [dry-run]" not in r.stdout
        # dry-run wrote nothing
        assert read_hook_version(
            root / ".claude/hooks/session-start.sh") is None

    def test_upgrade_refuses_malformed_settings_json(self, tmp_path):
        """An unparseable settings file is refused, never clobbered.

        MUTATION: write the merged document without checking ``_load_settings``
        error → the file is replaced → RED.
        """
        root = _old_install(tmp_path)
        bad = root / ".claude" / "settings.json"
        bad.write_text("[1, 2, 3]")
        r = _run(["hooks", "upgrade"], root, tmp_path)
        assert r.returncode == 1
        assert "Refusing" in r.stderr
        assert bad.read_text() == "[1, 2, 3]"

    def test_upgrade_refuses_malformed_event_value(self, tmp_path):
        """An event key holding a non-list is refused, never replaced by [].

        MUTATION: let ``_merge_settings`` coerce a non-list event to ``[]`` →
        the user's malformed-but-present value is destroyed and rc is 0 → RED.
        """
        root = _old_install(tmp_path, settings={
            "hooks": {"SessionEnd": {"not": "a list"}}}
        )
        before = (root / ".claude" / "settings.json").read_text()
        r = _run(["hooks", "upgrade"], root, tmp_path)
        assert r.returncode == 1
        assert "Refusing" in r.stderr
        assert (root / ".claude" / "settings.json").read_text() == before

    def test_hooks_status_and_upgrade_never_traceback_on_a_deep_settings_file(
            self, tmp_path):
        """``tortoise hooks status|upgrade`` reads and parses the settings
        file, and the parser's raise-set is open-ended: ``json.loads`` on a
        deeply nested document raises ``RecursionError`` — a ``RuntimeError``,
        NOT an ``OSError`` — which the old enumerated ``except OSError``
        boundary let escape as a raw traceback (#4024 P2-2; the same shape that
        let ``TypeError`` #3987 and ``UnicodeDecodeError`` #3988 escape
        ``capture_install``).  The CLI must surface a populated message and a
        non-zero exit, never a traceback, because the fail-closed install path
        tells the user to run exactly this command to repair.

        MUTATION: enumerate the boundary (``except OSError as e:``) — the
        traceback escapes, no populated ``Cannot inspect`` / ``Upgrade failed``
        line is printed, and this REDs."""
        root = tmp_path / "cursor-root"
        root.mkdir()
        deep = "[" * 200000 + "]" * 200000
        (root / "hooks.json").write_text('{"hooks": ' + deep + "}")

        for argv, prefix in (
            (("hooks", "status", "--harness", "cursor"), "Cannot inspect"),
            (("hooks", "upgrade", "--harness", "cursor"), "Upgrade failed"),
        ):
            r = _run(list(argv), root, tmp_path)
            assert r.returncode == 1, r
            assert prefix in r.stderr, r.stderr
            assert "RecursionError" in r.stderr, r.stderr
            assert "Traceback (most recent call last)" not in r.stderr, r.stderr
            assert "RecursionError" not in r.stdout, r.stdout

    def test_installed_ahead_of_source_is_not_downgraded(self, tmp_path):
        """An install whose marker is NEWER than this CLI's is reported but
        not overwritten.

        MUTATION: drop the ``found > expected`` skip → the newer script is
        downgraded → RED.
        """
        root = _old_install(tmp_path)
        future = _CURRENT + 5
        _write_script(root / ".claude/hooks/session-end.sh", future, "echo future")
        findings = detect_install(root)
        ahead = [f for f in findings if f.kind == "ahead-script"]
        assert ahead and not ahead[0].blocking
        upgrade_install(root)
        assert read_hook_version(
            root / ".claude/hooks/session-end.sh") == future


# ── 4. doctor integration ───────────────────────────────────────────────


class TestDoctorIntegration:
    @pytest.fixture
    def doctor_env(self, monkeypatch, tmp_path):
        for k in _DB_ENV_VARS:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "test-static-pepper")
        # Hermetic HOME: doctor resolves the Codex root through `Path.home()`
        # (`$CODEX_HOME` is scrubbed by the autouse conftest fixture), so the
        # real HOME would let doctor read the developer's own ~/.codex.
        monkeypatch.setenv("HOME", str(tmp_path))
        from tortoise import config as _config
        monkeypatch.setattr(
            _config, "DEFAULT_DB_PATH",
            str(tmp_path / ".tortoise" / "tortoise.db"))
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def _doctor_lines(self, capsys) -> list[str]:
        import argparse

        from tortoise.__main__ import _cmd_doctor
        _cmd_doctor(argparse.Namespace(cmd="doctor", db=None, path=None))
        out = capsys.readouterr().out
        return [ln for ln in out.splitlines() if "Capture hooks" in ln]

    def test_doctor_fails_on_a_stale_capture_install(self, doctor_env, capsys):
        """`tortoise doctor` reports the stale install as a FAILURE.

        MUTATION: never add the Capture hooks row (or mark it ⚠️) → the line
        is absent / not ❌ → RED.
        """
        _old_install(doctor_env, root=doctor_env)
        lines = self._doctor_lines(capsys)
        assert lines, "doctor must report the capture-hook install"
        assert "❌" in lines[0]
        assert "hooks status" in lines[0]

    def test_doctor_passes_on_a_current_install(self, doctor_env, capsys):
        """After upgrade, doctor's capture-hook row is ✅.

        MUTATION: report ❌ unconditionally → RED.
        """
        root = _old_install(doctor_env, root=doctor_env)
        upgrade_install(root)
        lines = self._doctor_lines(capsys)
        assert lines and "✅" in lines[0]

    def test_doctor_skips_when_no_capture_install(self, doctor_env, capsys):
        """A project that never installed the hooks is not nagged.

        MUTATION: run the check unconditionally → a "Capture hooks: ❌ missing"
        row appears for a non-install → RED.
        """
        assert self._doctor_lines(capsys) == []

    def test_doctor_skips_when_hooks_dir_holds_only_foreign_hooks(self, doctor_env, capsys):
        """A ``.claude/hooks/`` dir holding ONLY another product's hooks is not
        a Tortoise install — doctor must not false-fail and nudge the user to
        install hooks they never asked for.

        MUTATION: revert ``is_installed`` to ``hooks_root(root).is_dir()`` →
        the foreign dir is reported as a stale Tortoise install (❌) → RED.
        """
        hooks = doctor_env / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "other-tool.sh").write_text("#!/usr/bin/env bash\necho other\n")
        (doctor_env / ".claude" / "settings.json").write_text(json.dumps({
            "hooks": {"SessionStart": [{"matcher": "", "hooks": [
                {"type": "command",
                 "command": ".claude/hooks/other-tool.sh"}]}]}
        }))
        assert self._doctor_lines(capsys) == []

    def test_doctor_skips_a_foreign_script_sharing_our_basename(self, doctor_env, capsys):
        """A foreign hook that merely SHARES the generic basename is not a
        Tortoise install (the marker/body sniff decides).

        MUTATION: revert ``is_installed`` to a basename-existence check →
        doctor false-fails and prints a destructive repair instruction → RED.
        """
        hooks = doctor_env / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        for name in ("session-start.sh", "session-end.sh"):
            (hooks / name).write_text(
                "#!/usr/bin/env bash\necho another-product\n")
        assert self._doctor_lines(capsys) == []

    def test_doctor_still_sees_an_unmarkered_tortoise_install(self, doctor_env, capsys):
        """The pre-#3795 population has NO marker but a ``tortoise`` body —
        doctor must still report it (that is the copy that needs upgrading).

        MUTATION: sniff ownership by marker only → the un-markered install is
        skipped and no warning reaches the user → RED.
        """
        hooks = doctor_env / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "session-start.sh").write_text(
            "#!/usr/bin/env bash\ntortoise context\n")
        lines = self._doctor_lines(capsys)
        assert lines and "❌" in lines[0]

    def test_doctor_reports_a_codex_install_at_codex_home(self, doctor_env, capsys):
        """Doctor checks the Codex seam at `$CODEX_HOME`, the only path Codex
        reads — not the cwd.

        MUTATION: check only the `claude` layout (anchor the root at cwd) → no
        codex row → RED.
        """
        from tortoise.capture_install import install_capture
        assert install_capture("codex", home=doctor_env).ok

        lines = self._doctor_lines(capsys)
        assert any("codex" in ln and "✅" in ln for ln in lines), lines
        # ...and nothing was read from the dead project-local path.
        assert not (doctor_env / "hooks.json").exists()

    def test_doctor_fails_on_a_stale_codex_install(self, doctor_env, capsys):
        """A stale Codex install is a FAIL, same as Claude's — doctor's
        freshness row must not be Claude-only (#3818).

        MUTATION: add the codex row but never mark its drift → RED.
        """
        from tortoise.capture_install import install_capture
        assert install_capture("codex", home=doctor_env).ok
        stale = (doctor_env / ".codex" / "hooks" / "tortoise-session-end.sh")
        stale.write_text("#!/usr/bin/env bash\n# tortoise session capture\nexit 0\n")

        lines = self._doctor_lines(capsys)
        assert any("codex" in ln and "❌" in ln for ln in lines), lines
        assert any("hooks status --harness codex" in ln for ln in lines), lines

    def test_doctor_reports_a_cursor_install_at_cursor_home(self, doctor_env, capsys):
        """Doctor checks the Cursor seam at ``~/.cursor``, the only path Cursor
        reads — not the cwd (#3819).

        MUTATION: check only claude+codex layouts → no cursor row → RED.
        """
        from tortoise.capture_install import install_capture
        assert install_capture("cursor", home=doctor_env).ok

        lines = self._doctor_lines(capsys)
        assert any("cursor" in ln and "✅" in ln for ln in lines), lines
        # ...and nothing was read from the untrusted project-local path.
        assert not (doctor_env / "hooks.json").exists()

    def test_doctor_fails_on_a_stale_cursor_install(self, doctor_env, capsys):
        """A stale Cursor install is a FAIL, same as Claude's/Codex's —
        doctor's freshness row must not be Claude-only (#3819).

        MUTATION: add the cursor row but never mark its drift → RED.
        """
        from tortoise.capture_install import install_capture
        assert install_capture("cursor", home=doctor_env).ok
        stale = (doctor_env / ".cursor" / "hooks" / "tortoise-session-end.sh")
        stale.write_text("#!/usr/bin/env bash\n# tortoise session capture\nexit 0\n")

        lines = self._doctor_lines(capsys)
        assert any("cursor" in ln and "❌" in ln for ln in lines), lines
        assert any("hooks status --harness cursor" in ln for ln in lines), lines


# ── 5. harness-agnosticism (the mechanism must not be Claude-shaped) ────


class TestHarnessAgnostic:
    def _synthetic(self, monkeypatch, tmp_path, *, settings_file):
        """Register a non-Claude layout with its own dirs/events/timeout."""
        src = tmp_path / "shipped"
        _write_script(src / "capture-a.sh", 7, "echo a")
        _write_script(src / "capture-b.sh", 7, "echo b")
        monkeypatch.setattr(hook_install, "_HOOKS_SOURCE_DIR", src)
        layout = HarnessLayout(
            harness="widget",
            hooks_dir=".widget/hooks",
            settings_file=settings_file,
            scripts=(
                HookScriptSpec("capture-a.sh", "WidgetStart", 45,
                               ".widget/hooks/capture-a.sh"),
                HookScriptSpec("capture-b.sh", "WidgetStop", 45,
                               ".widget/hooks/capture-b.sh"),
            ),
        )
        monkeypatch.setitem(hook_install.HARNESS_LAYOUTS, "widget", layout)
        return layout

    def test_synthetic_json_harness_upgrades(self, monkeypatch, tmp_path):
        """A layout with different dirs/events/timeout detects and upgrades —
        proof the mechanism carries no Claude-specific assumption.

        MUTATION: hardcode ``.claude``/``SessionEnd``/``60`` in
        ``detect_install``/``upgrade_install`` → the synthetic harness is not
        found or not repaired → RED.
        """
        root = tmp_path / "proj"
        _write_script(root / ".widget/hooks/capture-a.sh", 1, "echo old")
        _write_script(root / ".widget/hooks/capture-b.sh", 1, "echo old")
        (root / ".widget").mkdir(parents=True, exist_ok=True)
        (root / ".widget/settings.json").write_text(json.dumps({
            "hooks": {"WidgetStop": [{"hooks": [
                {"type": "command", "command": ".widget/hooks/capture-b.sh"}]}]}
        }))
        self._synthetic(monkeypatch, tmp_path, settings_file=".widget/settings.json")

        findings = detect_install(root, "widget")
        assert {f.kind for f in findings} >= {
            "stale-script", "missing-hook-entry", "settings-no-timeout"}

        upgrade_install(root, "widget")
        assert read_hook_version(root / ".widget/hooks/capture-a.sh") == 7
        doc = json.loads((root / ".widget/settings.json").read_text())
        assert _our_entry(doc, "WidgetStop", "capture-b.sh")["timeout"] == 45
        assert _our_entry(doc, "WidgetStart", "capture-a.sh")["timeout"] == 45
        assert detect_install(root, "widget") == []

    def test_scripts_only_harness_has_no_settings_half(self, monkeypatch, tmp_path):
        """A harness with no settings file (an extension/file-based seam like
        Pi/Cline) is still detected and upgraded from its scripts alone.

        MUTATION: assume ``layout.settings_file`` is always present → this
        path raises / reports phantom settings drift → RED.
        """
        root = tmp_path / "proj"
        _write_script(root / ".widget/hooks/capture-a.sh", 1, "echo old")
        _write_script(root / ".widget/hooks/capture-b.sh", 1, "echo old")
        self._synthetic(monkeypatch, tmp_path, settings_file=None)

        assert {f.kind for f in detect_install(root, "widget")} == {"stale-script"}
        upgrade_install(root, "widget")
        assert read_hook_version(root / ".widget/hooks/capture-a.sh") == 7
        assert not (root / ".widget/settings.json").exists()
        assert detect_install(root, "widget") == []


# ── 6. the shipped layout's timeout agrees with the shipped snippets ─────


class TestShippedContractAgreement:
    def _snippet_timeouts(self) -> dict[str, set[int]]:
        text = (_REPO_ROOT / "website/apps/dashboard/src/harnesses.js"
                ).read_text(encoding="utf-8")
        found: dict[str, set[int]] = {}
        for m in re.finditer(r'\{\s*"hooks"\s*:', text):
            depth = 0
            for j in range(m.start(), len(text)):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
            doc = json.loads(text[m.start():j + 1].replace("#", ""))
            for event, groups in doc["hooks"].items():
                for group in groups:
                    for entry in group["hooks"]:
                        if isinstance(entry.get("timeout"), int):
                            found.setdefault(event, set()).add(entry["timeout"])
        return found

    def test_layout_timeout_matches_shipped_settings_snippets(self):
        """One numeric truth: the installer's required timeout is the timeout
        the shipped settings snippets use.

        MUTATION: change either the layout's ``timeout`` or the snippet's
        → the sets disagree → RED.
        """
        shipped = self._snippet_timeouts()
        for spec in _LAYOUT.scripts:
            assert spec.timeout in shipped.get(spec.event, set()), (
                f"layout requires {spec.event} timeout={spec.timeout} but the "
                f"shipped snippets use {sorted(shipped.get(spec.event, set()))}"
            )


# ── 7. the docs that describe upgrading ─────────────────────────────────


class TestUpgradeDocs:
    def _section(self) -> str:
        text = (_REPO_ROOT / "docs/quickstart-selfhosted.md").read_text(
            encoding="utf-8")
        start = text.index("### Upgrading an existing hook install")
        rest = text[start + 1:]
        end = rest.index("\n### ")
        return rest[:end]

    def test_upgrade_section_names_both_halves_and_the_command(self):
        """#3801's other half: the documented upgrade path must name the
        settings-file ``timeout`` step, not just the script re-copy — and give
        a machine-checkable command (#3801's verification checklist: "a
        copy-pasteable verification command"), not prose to trust.

        MUTATION: delete the ``"timeout": 60`` line (or the ``tortoise hooks
        upgrade`` command) from the section → RED.
        """
        section = self._section()
        assert "session-start.sh" in section
        assert "session-end.sh" in section
        assert ".claude/settings.json" in section
        assert '"timeout": 60' in section   # the settings half, as a command
        assert "tortoise hooks upgrade" in section


# ── 8. declared threat surface: argv / path resolution (#3866) ──────────
#
# ``_invokes_script`` is gate/enforcement code whose correctness is "an
# attacker cannot make it fail open" (AGENTS.md, adversarial domain).  Its
# bound is the DECLARED threat surface — the two classes below — and acceptance
# is every declared class covered by a test that REDs when its guard is
# removed, not "the reviewer ran out of ideas".


class TestDeclaredThreatSurface:
    """One test per declared class; each names the mutation that REDs it."""

    @pytest.mark.parametrize("command", [
        # A launcher option whose separate VALUE is our path.  None of these
        # commands executes the hook:
        #   sudo -u <hook>     -> the path is read as a USERNAME
        #   sudo -g <hook>     -> the path is read as a GROUP
        #   sudo -c <hook>     -> the path is read as a CLASS
        #   timeout -s <hook>  -> the path is read as a SIGNAL NAME
        #   bash -o <hook>     -> the path is read as an OPTION NAME
        #   env -u <hook>      -> the path is read as a VARIABLE NAME
        #   nice -n <hook>     -> the path is read as an ADJUSTMENT
        #   time -o <hook>     -> the path is read as the OUTPUT FILE
        #   xargs -J <hook>    -> the path is read as the REPLACEMENT STRING
        #   env -a <hook>      -> the path is read as ARGV0
        #   xargs --process-slot-var <hook> -> the path is read as the slot var
        #   timeout --sig <hook> -> GNU getopt_long ABBREVIATION of --signal
        #   sudo --login-class <hook> -> BSD sudo's canonical --login-class
        #   <unknown --long> <hook> -> arity unknown: the SAFE default consumes
        #   ksh -R <hook>      -> the path is read as a CROSS-REFERENCE FILE
        #   ksh -T <hook>      -> the path is read as a TEST MASK
        #   sh -O <hook>       -> the path is read as a SHOPT OPTION NAME
        #   bash -O <hook>     -> the path is read as a SHOPT OPTION NAME
        "sudo -u {abs}",
        "sudo -g {abs}",
        "sudo -c {abs}",
        "sudo --login-class {abs}",
        "sudo --login-c {abs}",
        "timeout -s {abs}",
        "timeout --sig {abs}",
        "timeout --zzz {abs}",
        "bash -o {abs}",
        "bash -O {abs}",
        "ksh -R {abs}",
        "ksh -T {abs}",
        "sh -O {abs}",
        "env -u {abs}",
        "env -a {abs}",
        "env --uns {abs}",
        "env --frobnicate {abs}",
        "nice -n {abs}",
        "nice --adj {abs}",
        "/usr/bin/time -o {abs}",
        "/usr/bin/time --output {abs}",
        "xargs -J {abs}",
        "xargs -R {abs}",
        "xargs -S {abs}",
        "xargs --process-slot-var {abs}",
        "xargs --max-a {abs}",
    ])
    def test_consumed_option_argument_is_not_a_registration(
            self, tmp_path, command):
        """CLASS 1 (fail-open): a launcher option's consumed argument must
        never count as the executed command.

        A single flat option table cannot be right for every launcher: ``-n``
        is sudo's boolean non-interactive flag but nice's argument-taking
        adjustment, and ``-s`` is timeout's signal value but sudo's boolean
        shell flag.  The old table's blanket "if the argument is our hook, it
        ran" safety net then reported a healthy install while bash never ran
        the hook — a silent no-capture.

        MUTATION: restore the ``skip_next`` safety net
        (``if _token_is_our_script(tok, …): return True``) → the consumed value
        is judged executed → no ``missing-hook-entry`` → no real entry is
        added → RED.  The alias/abbreviation cases additionally RED if the
        ``-a``/``--process-slot-var`` entries or the long-option prefix branch
        are removed; the ``--login-class``/unknown-long cases RED if the
        unknown-long "assume it consumes" default is changed back to boolean;
        and the ``ksh -R``/``-T``/``sh -O``/``bash -O`` cases RED if the
        unknown-SHORT "assume it consumes" default is changed back to boolean
        (the enumerated ``_OPTIONS_WITH_ARG`` table never listed them).
        """
        root = tmp_path / "project"
        abs_hook = root / ".claude" / "hooks" / "session-end.sh"
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command",
             "command": command.format(abs=str(abs_hook))}]}]}}
        root = _old_install(tmp_path, settings=doc, root=root)
        assert any(
            f.kind == "missing-hook-entry" and f.script == "session-end.sh"
            for f in detect_install(root))
        upgrade_install(root)
        entries = _settings(root)["hooks"]["SessionEnd"]
        assert len(entries) == 2  # the consumed-argument entry + our real one
        ours = _our_entry(_settings(root), "SessionEnd", "session-end.sh")
        assert ours["timeout"] == 60

    @pytest.mark.parametrize("command", [
        "echo $(({abs}))",    # arithmetic: operands, never executed
        "x=$(({abs}))",       # arithmetic inside an assignment value
        "(({abs}))",          # the arithmetic COMMAND form (no leading $)
    ])
    def test_arithmetic_operand_is_not_a_registration(self, tmp_path, command):
        """CLASS 1 (fail-open), second reproduction: ``$((…))`` is an
        ARITHMETIC expansion — its contents are operands — while ``$(…)``
        (single paren) is command substitution, whose contents ARE executed.
        Treating the two alike lets ``echo $((<hook>))`` count as our
        registration while bash runs nothing.

        MUTATION: drop the ``$((`` branch from ``_split_command`` AND the
        ``((`` arithmetic-command block → the parens reset executable position
        → the operand is judged executed → no ``missing-hook-entry`` → RED.
        """
        root = tmp_path / "project"
        abs_hook = root / ".claude" / "hooks" / "session-end.sh"
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command",
             "command": command.format(abs=str(abs_hook))}]}]}}
        root = _old_install(tmp_path, settings=doc, root=root)
        assert any(
            f.kind == "missing-hook-entry" and f.script == "session-end.sh"
            for f in detect_install(root))
        upgrade_install(root)
        assert len(_settings(root)["hooks"]["SessionEnd"]) == 2

    def test_boolean_option_leaves_hook_in_command_position(self, tmp_path):
        """The other half of CLASS 1: a launcher option that is BOOLEAN does
        not consume the next word.  ``sudo -n`` is sudo's non-interactive flag
        (not nice's adjustment), so ``sudo -n <hook>`` really runs the hook and
        must not be judged foreign.

        MUTATION: list ``-n`` in sudo's ``_OPTIONS_WITH_ARG`` entry (or use one
        flat table for every launcher) → the token after ``-n`` is consumed →
        no registration is found → a DUPLICATE is appended → RED.
        """
        root = tmp_path / "project"
        abs_hook = root / ".claude" / "hooks" / "session-end.sh"
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command",
             "command": "sudo -n " + str(abs_hook)}]}]}}
        root = _old_install(tmp_path, settings=doc, root=root)
        assert not [f for f in detect_install(root)
                    if f.kind == "missing-hook-entry"
                    and f.script == "session-end.sh"]
        upgrade_install(root)
        assert len(_settings(root)["hooks"]["SessionEnd"]) == 1
        ours = _our_entry(_settings(root), "SessionEnd", "session-end.sh")
        assert ours["timeout"] == 60

    def test_every_launcher_declares_its_option_arity(self):
        """STRUCTURAL guard for the class-1 mechanism: the fix keys option
        arity by launcher, so a launcher with NO key silently defaults to
        "no option takes an argument" — exactly how ``time -o <hook>`` and
        ``xargs -J <hook>`` stayed fail-open.  Every program launcher must
        declare a table (possibly empty) in EACH arity table — the
        argument-taking table AND both proved-boolean tables; only shell
        syntax may be declared option-less.

        MUTATION: drop the ``"time"`` key (or ``"setsid"``/``"nohup"``, or
        add a new launcher to ``_LAUNCHERS`` without a table) → the launcher
        is in neither declared set → RED.  Dropping a ``_BOOLEAN_SHORT`` /
        ``_BOOLEAN_LONG`` key REDs the same way (an absent boolean table is
        ``frozenset()``, which is safe but silently stops declaring arity).
        """
        declared = set(hook_install._OPTIONS_WITH_ARG) | set(
            hook_install._OPTION_LESS_LAUNCHERS)
        assert set(hook_install._LAUNCHERS) == declared
        assert set(hook_install._BOOLEAN_SHORT) == set(
            hook_install._OPTIONS_WITH_ARG)
        assert set(hook_install._BOOLEAN_LONG) == set(
            hook_install._OPTIONS_WITH_ARG)

    def test_unproved_option_arity_defaults_to_consuming(self, tmp_path):
        """STRUCTURAL guard for the class-1 mechanism — asserts the PROPERTY,
        not an instance: for EVERY registered launcher, an option its
        proved-boolean tables do NOT list must leave the matcher with NO match
        when our hook follows it.

        This is what ``test_every_launcher_declares_its_option_arity`` could
        not see: that test only asserted each launcher had a KEY, so an
        incomplete argument-taking table (``ksh -R``/``-T``, ``sh -O``) stayed
        green while fail-open.  Iterating the SHORT-option space proves the
        inversion holds for options no table mentions — the case an
        enumeration can never cover.

        MUTATION: replace the short ``else: skip_next = True`` with the old
        ``any(("-" + ch) in options for ch in tok[1:])`` enumeration, or make
        the unknown short option boolean (``pass``) → every unlisted short
        option leaves the hook in command position → RED.
        """
        root = tmp_path / "project"
        abs_hook = root / ".claude" / "hooks" / "session-end.sh"
        root = _old_install(tmp_path, root=root)
        offenders = []
        for launcher in sorted(hook_install._OPTIONS_WITH_ARG):
            for ch in string.ascii_letters + string.digits:
                opt = "-" + ch
                if opt in hook_install._BOOLEAN_SHORT[launcher]:
                    continue
                if (launcher in hook_install._SHELLS
                        and opt in hook_install._SHELL_COMMAND_FLAGS):
                    continue  # a command STRING: really executed, by design
                cmd = f"{launcher} {opt} {abs_hook}"
                if hook_install._invokes_script(
                        cmd, "session-end.sh", ".claude/hooks", root):
                    offenders.append(cmd)
            for opt in ("--frobnicate-xyz", "--sig", "--login-class",
                        "--output", "--zzz"):
                if any(b.startswith(opt)
                       for b in hook_install._BOOLEAN_LONG[launcher]):
                    continue  # an abbreviation of a proved boolean: matches
                cmd = f"{launcher} {opt} {abs_hook}"
                if hook_install._invokes_script(
                        cmd, "session-end.sh", ".claude/hooks", root):
                    offenders.append(cmd)
        assert offenders == [], (
            "option not proved boolean left the hook in command position "
            f"(FAIL-OPEN): {offenders}")

    @pytest.mark.parametrize("launcher,option", [
        ("ksh", "-R"),   # ksh: ``-R file`` — cross-reference database FILE
        ("ksh", "-T"),   # ksh: ``-T mask`` — implementation test MASK
        ("sh", "-O"),    # bash-as-sh: ``-O`` takes a shopt OPTION NAME
    ])
    def test_short_option_arity_matches_bash_ground_truth(
            self, tmp_path, launcher, option):
        """The reproduced instance, checked against REAL bash execution.

        ``ksh -R <hook>`` (also ``-T``, and bash-as-``sh``'s ``-O``) consumed
        the path as the option's VALUE: bash ran NOTHING, yet the matcher
        returned True — ``detect_install`` judged the dead entry ours and
        ``upgrade_install`` stamped ``timeout`` onto it instead of appending a
        real registration (silent no-capture).  This runs the command through
        bash, asserts the hook did NOT execute, then asserts the matcher
        agrees — grounding the static table in observable behaviour rather
        than another enumeration we could get wrong.

        MUTATION: make the unknown short option default boolean (``pass``
        instead of ``skip_next = True``) → the matcher claims the hook ran →
        RED.
        """
        if shutil.which(launcher) is None:
            pytest.skip(
                f"{launcher!r} is not installed on this box — the reproduced "
                f"instance cannot be ground-truthed here")
        root = tmp_path / "project"
        hook = root / ".claude" / "hooks" / "session-end.sh"
        hook.parent.mkdir(parents=True)
        marker = root / "MARKER"
        hook.write_text(f"#!/bin/bash\ntouch {marker}\n")
        hook.chmod(0o755)
        command = f"{launcher} {option} {shlex.quote(str(hook))}"
        completed = subprocess.run(
            ["bash", "-c", command], capture_output=True, text=True,
            timeout=30, cwd=str(root))
        assert not marker.exists(), (
            f"ground truth changed: {command!r} executed the hook "
            f"(stdout={completed.stdout!r} stderr={completed.stderr!r})")
        assert hook_install._invokes_script(
            command, "session-end.sh", ".claude/hooks", root) is False

    @pytest.mark.parametrize("command", [
        "/bin/bash {abs}",
        "/usr/bin/env bash {abs}",
        "/bin/sh -c '{abs}'",
        "'/bin/bash' {abs}",       # a QUOTED path-qualified launcher
    ])
    def test_path_qualified_launcher_is_recognised(self, tmp_path, command):
        """CLASS 2 (false negative): a path-qualified launcher execs the hook
        exactly as the bare name does.  Recognising only the bare name judged
        a working install ``missing-hook-entry`` and appended a DUPLICATE
        registration — the hook then ran twice per event.

        MUTATION: match launchers with ``tok in _LAUNCHERS`` instead of
        basename-aware ``_as_launcher`` → the path-qualified form is foreign →
        ``missing-hook-entry`` + a duplicate entry → RED.
        """
        root = tmp_path / "project"
        abs_hook = root / ".claude" / "hooks" / "session-end.sh"
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command",
             "command": command.format(abs=str(abs_hook))}]}]}}
        root = _old_install(tmp_path, settings=doc, root=root)
        assert not [f for f in detect_install(root)
                    if f.kind == "missing-hook-entry"
                    and f.script == "session-end.sh"]
        upgrade_install(root)
        assert len(_settings(root)["hooks"]["SessionEnd"]) == 1
        ours = _our_entry(_settings(root), "SessionEnd", "session-end.sh")
        assert ours["timeout"] == 60

    @pytest.mark.parametrize("command", [
        "./bash {abs}",
        "./bin/bash {abs}",
        "/nonexistent/bin/bash {abs}",
    ])
    def test_nonexistent_path_qualified_launcher_is_not_ours(
            self, tmp_path, command):
        """A PATH-QUALIFIED launcher must actually EXIST to be one: bash runs
        nothing for a path it cannot find, so treating the hook as its operand
        would FAIL OPEN (report current while the hook never runs).  The entry
        must be reported missing and REPAIRED.

        MUTATION: drop the ``path.is_file() and os.access(path, X_OK)`` gate
        from :func:`_as_launcher` → ``./bash <hook>`` is read as the launcher
        with the hook in operand position → no ``missing-hook-entry`` → RED.
        """
        root = tmp_path / "project"
        abs_hook = root / ".claude" / "hooks" / "session-end.sh"
        doc = {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [
            {"type": "command",
             "command": command.format(abs=str(abs_hook))}]}]}}
        root = _old_install(tmp_path, settings=doc, root=root)
        missing = [f for f in detect_install(root)
                   if f.kind == "missing-hook-entry"
                   and f.script == "session-end.sh"]
        assert missing
        upgrade_install(root)
        assert len(_settings(root)["hooks"]["SessionEnd"]) == 2
