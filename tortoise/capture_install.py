"""Install the per-harness CAPTURE seam (#3808).

Why this exists
---------------
The capture seam was delivered as **copy-paste instructions**.  The dashboard
shipped ``HARNESS_CAPTURE_INSTALL.claude`` / ``.pi`` (and the matching blocks
inside ``HARNESS_INSTALL``) and the user performed the install by hand: copy
the hook scripts into ``.claude/hooks/``, ``chmod +x``, then hand-merge a
``settings.json`` fragment — or copy ``tortoise-capture.ts`` into
``~/.pi/agent/extensions/``.  ``grep -rn "HARNESS_INSTALL" tortoise/`` returned
nothing: **no CLI path installed capture at all**, so ``tortoise install
claude`` gave a user the read hook and no capture.

A manual step that fails silently is indistinguishable from a working one —
and the capture hook is deliberately fail-open (``2>/dev/null || exit 0``), so
a missing or mistyped install files no sessions and reports no error.  That is
the gap this module closes: the product now installs its own seam.

What it installs, per harness
-----------------------------
``claude``
    ``tortoise/claude-hooks/session-start.sh`` + ``session-end.sh`` into
    ``<root>/.claude/hooks/`` (mode 0755), and **merges** — never overwrites —
    the ``SessionStart`` / ``SessionEnd`` registration into
    ``<root>/.claude/settings.json``, carrying the load-bearing
    ``"timeout": 60`` from #3754/#3801.  Unrelated settings keys, other
    events, and the user's own hooks in those events are preserved.

``pi``
    ``tortoise/pi-hooks/tortoise-capture.ts`` into
    ``<home>/.pi/agent/extensions/tortoise-capture.ts``, after the
    non-destructive legacy guard from #3713 (see below).

Idempotent by construction — this is also the upgrade path
----------------------------------------------------------
Every target is compared before it is written: a script/extension whose bytes
already match the shipped artifact is not rewritten at all (so a second run
leaves the mtime untouched), and the settings file is only rewritten when the
merged document actually differs from what is on disk.  Re-running the install
is therefore a clean no-op, and re-running it after the shipped artifact
changes is the upgrade.

Fail loudly
-----------
An install that cannot write its hook must never report success.  Every
failure path returns a populated :attr:`InstallResult.error` (non-empty
stderr + non-zero exit at the CLI): a missing shipped artifact, a symlinked
target that escapes the install root, an unparsable/invalid ``settings.json``
(never clobbered), an unwritable directory, a destination that is not a
regular file.  Writes are atomic (temp file + ``os.replace``) so a failed
write cannot leave a half-written ``settings.json`` behind.

Relationship to ``tortoise/hook_install.py`` (PR #3866)
-------------------------------------------------------
#3866 adds the **drift/repair** path — ``tortoise hooks status|upgrade`` — over
a ``HarnessLayout`` registry, and versions the contract with a
``# tortoise-hook-version: N`` marker inside each shipped script.  This module
deliberately does **not** re-declare any of that: it installs the shipped
artifacts *verbatim* (no marker is injected, no version constant is declared)
and emits exactly the settings shape #3866's ``_entry_command_dicts`` /
``_invokes_script`` classify as ours — a matcher entry whose ``hooks`` array
holds ``{"type": "command", "command": ".claude/hooks/<name>", "timeout": 60}``.
The artifact an install produces is therefore one ``tortoise hooks status``
reads as current, and a stale install is repaired by ``tortoise hooks
upgrade`` — #3808 is the mechanism #3795/#3801 operate through.  There is one
version contract, and it lives in the shipped script.

Because the two modules answer the same question — "is this entry ours?" —
:func:`_executable_tokens` / :func:`_is_our_script_command` here mirror #3866's
executable-position and install-confinement rules rather than re-deciding them.
A foreign path that merely shares our basename, a hook named only as an
argument (``cat .claude/hooks/session-end.sh``), a flat event-level
``{type, command}`` (which Claude Code ignores), and a handler missing its
``type`` are all **not** ours in both modules; treating any of them as ours
would leave a project capturing nothing while reporting a successful install.
One intentional difference: #3866 refuses *any* symlink below the install root,
while :func:`_symlink_escape` refuses only those that leave it — a symlink whose
target is inside the project is replaced by a real file rather than refused.
That is never a silent loss (the hook still runs), and #3866's ``status`` reads
the resulting regular file as current.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path

#: Where the shipped artifacts live, resolved from this package (so a
#: site-packages/wheel install resolves them — they are declared in
#: ``[tool.setuptools.package-data]``).  Same resolution rule as
#: ``__main__._cmd_install_hooks`` for ``volunteer-turn.sh``.
PACKAGE_DIR = Path(__file__).resolve().parent

#: The in-repo artifact each harness's capture seam installs.  Mirrors
#: ``HARNESS_CAPTURE_SEAM`` in ``website/apps/dashboard/src/harnesses.js`` and
#: the dashboard's ``harnesses.test.js`` pins the two together.
CAPTURE_SEAM: dict[str, str] = {
    "claude": "tortoise/claude-hooks/session-end.sh",
    "pi": "tortoise/pi-hooks/tortoise-capture.ts",
}

#: Claude Code hook scripts → ``.claude/hooks/``.
CLAUDE_SCRIPTS: tuple[str, ...] = ("session-start.sh", "session-end.sh")

#: The per-hook budget #3754 established and #3801 identified as the
#: load-bearing half of the contract: Claude Code cancels a SessionEnd hook at
#: its 1.5 s default, the budget rises to the highest per-hook timeout, and 60
#: is the documented ceiling.  Must stay equal to the value the dashboard
#: block emits (pinned by ``tests/test_capture_install.py``).
CLAUDE_TIMEOUT = 60

#: The extension name Pi auto-discovers under ``~/.pi/agent/extensions/``.
PI_EXTENSION_NAME = "tortoise-capture.ts"

#: The legacy agent-infra extension directory name (#3713).  Pi's loader does
#: no basename dedupe, so ``tortoise-capture.ts`` and
#: ``tortoise-capture/index.ts`` are TWO extensions that both POST the same
#: ``session_id``.
LEGACY_PI_DIRNAME = "tortoise-capture"

#: Where the legacy entry is moved when disabled — dot-prefixed, so Pi's
#: loader skips it (``entry.name.startsWith(".")``) while its files live on.
PI_DISABLED_DIRNAME = ".tortoise-capture.disabled"


@dataclass(frozen=True)
class InstallResult:
    """The outcome of one :func:`install_capture` call.

    ``error`` non-empty means the install did not complete and the caller must
    fail loudly (non-zero exit, message on stderr).  ``changed`` False with no
    ``error`` means re-running was a clean no-op — the state on disk is already
    the installed state.
    """

    harness: str
    changed: bool = False
    actions: tuple[str, ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _atomic_bytes(dst: Path, data: bytes, mode: int) -> None:
    """Replace ``dst`` with ``data`` atomically, setting ``mode``.

    A fresh same-dir temp + ``os.replace`` swaps the directory entry rather
    than writing through the existing inode, so a hard-linked or symlinked
    target is replaced instead of being written through, and a crash mid-write
    cannot leave a truncated artifact behind.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(dst.parent), prefix=dst.name + ".", suffix=".tortoise-tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, dst)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _is_regular_unchanged(dst: Path, data: bytes) -> bool:
    """True when ``dst`` is a regular file whose bytes already equal ``data``.

    A symlink or a non-regular file is never "unchanged" — it must be replaced
    (the symlink itself, not its target) or refused, never silently accepted.
    """
    if dst.is_symlink() or not dst.is_file():
        return False
    try:
        return dst.read_bytes() == data
    except OSError:
        return False


def _refuse_non_regular(dst: Path) -> str:
    """Refusal when ``dst`` exists and is not a regular file, else ``""``.

    A directory / FIFO / socket at a script path must be refused with the
    documented populated :attr:`InstallResult.error`, never written through and
    never surfaced as a bare ``OSError`` traceback out of
    :func:`_atomic_bytes`.  A symlink is not refused here (it is replaced — the
    link, not its target); a path that does not exist is fine.
    """
    if dst.is_symlink() or not dst.exists():
        return ""
    if dst.is_file():
        return ""
    return (f"Refusing: {dst} exists but is not a regular file — move it "
            "aside and re-run")


def _symlink_escape(root: Path, target: Path, *, label: str) -> str:
    """Refusal message when any component of ``target`` under ``root`` escapes.

    A repo/config symlink must not write through to a real file elsewhere
    (``.claude/settings.json`` → ``~/.claude/settings.json``).  Resolved paths
    are compared on both sides, so a root reached via a symlinked alias
    (macOS ``/tmp`` → ``/private/tmp``, a git worktree) is not a false
    positive.  Returns ``""`` when the path is safe.
    """
    root_r = root.resolve()
    try:
        rel_parts = target.relative_to(root).parts
    except ValueError:
        rel_parts = target.parts  # target outside root — fall back to leaf
    cur = root
    for part in rel_parts:
        cur = cur / part
        if cur.is_symlink():
            resolved = cur.resolve()
            if resolved != root_r and root_r not in resolved.parents:
                return (
                    f"{cur} resolves to {resolved} — outside the {label} "
                    f"{root_r}. Symlinked configs are not touched (unlink the "
                    "symlink first)."
                )
    return ""


def _preflight_writable(dirs: list[Path]) -> str:
    """Create ``dirs`` and prove each is writable *before* the first write.

    A half-install that reports success is the failure mode this exists to
    prevent: an unwritable hooks dir must abort the whole install before a
    script or a settings entry is written, not after.  Returns ``""`` on
    success, a refusal message otherwise.
    """
    for d in dirs:
        anc = d
        while not anc.exists() and anc != anc.parent:
            anc = anc.parent
        if not anc.is_dir():
            return (f"cannot install into {d}: {anc} is not a directory "
                    f"(move it aside or pass an explicit install root)")
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return f"cannot create {d}: {e.__class__.__name__}: {e}"
        if not os.access(d, os.W_OK):
            return f"cannot install into {d}: directory is not writable"
    return ""


#: Tokens that RUN the token after them rather than being the command
#: themselves: ``bash .claude/hooks/session-end.sh`` registers the hook, and so
#: does a bare ``.claude/hooks/session-end.sh``.  Mirrors the launcher set
#: ``tortoise/hook_install.py`` (#3866) applies to the same question, so the two
#: surfaces agree on which command lines count as a registration — a
#: disagreement would make ``tortoise hooks status`` report drift on an install
#: this module considers current.
_LAUNCHERS = frozenset({
    "bash", "sh", "zsh", "dash", "ksh", "env", "exec", "nohup", "time",
    "timeout", "setsid", "nice", "sudo", "command", "eval", ".", "source",
})

#: Shell separators that end one command and begin the next — the token after
#: one of these is again in executable position.
_SEPARATORS = frozenset({";", "&&", "||", "|", "&"})

#: A bare redirection operator redirects to the NEXT token, which is a filename
#: and never an executed command.  An operator carrying its target
#: (``2>/dev/null``) is matched by :data:`_REDIRECT_RE` and skips nothing.
_REDIRECT_OPERATORS = frozenset({">", ">>", "<", "<<", "&>", "<>"})
_REDIRECT_RE = re.compile(r"^[0-9]*(?:&?>>?|&?>&|<>)")

#: An environment-assignment prefix (``A=/x/y``) — not the command.
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: A launcher operand that is a number/duration (``timeout 5 …``).
_LAUNCHER_OPERAND_RE = re.compile(r"^[0-9]+(\.[0-9]+)?[smhd]?$")


def _executable_tokens(command: str) -> list[str] | None:
    """The tokens of ``command`` that are in EXECUTABLE position.

    ``None`` for an unterminated quote — a command bash would reject executes
    nothing.  Only the command itself (or the operand of a launcher such as
    ``bash`` / ``timeout`` / ``env``) is returned, so a token that merely
    NAMES our hook (``cat .claude/hooks/session-end.sh``) or queries it
    (``command -v …``) is never mistaken for a registration.  Deliberately a
    compact mirror of ``tortoise/hook_install.py::_invokes_script`` (#3866):
    the two must agree, or ``tortoise hooks status`` reports drift on an
    install this module produced.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    executable: list[str] = []
    expect_cmd = True
    skip_operand = False
    launcher: str | None = None
    for token in tokens:
        if token in _SEPARATORS:
            expect_cmd, launcher, skip_operand = True, None, False
            continue
        if skip_operand:
            skip_operand = False
            continue
        if _REDIRECT_RE.match(token):
            skip_operand = token in _REDIRECT_OPERATORS
            continue
        if not expect_cmd:
            continue
        if launcher == "command" and token in ("-v", "-V"):
            return []  # ``command -v <path>`` asks a question; it runs nothing
        if token in _LAUNCHERS:
            launcher = token
            continue
        if token.startswith("-"):
            continue  # an option (of a launcher, or of the command itself)
        if _ASSIGNMENT_RE.match(token):
            continue
        if launcher == "timeout" and _LAUNCHER_OPERAND_RE.match(token):
            continue  # ``timeout 5`` — the duration, not the command
        executable.append(token)
        expect_cmd, launcher = False, None
    return executable


def _is_our_script_command(command: str, script_name: str,
                           hooks_dir: str, root: Path) -> bool:
    """True when ``command`` EXECUTES this project's hook ``script_name``.

    Confined to the install: a token resolving to a DIFFERENT file — a vendored
    ``vendor/.claude/hooks/session-end.sh``, an absolute path outside ``root`` —
    is somebody else's hook, and stamping our ``timeout`` on it would leave this
    project with no working capture while the install reported success (the
    silent-loss shape #3808 exists to close).  A ``$VAR`` token
    (``$CLAUDE_PROJECT_DIR/.claude/hooks/…``, the form Claude Code documents)
    cannot be resolved, so it falls back to the directory-suffix rule — the same
    carve-out ``hook_install._token_is_our_script`` makes (#3866).
    """
    tokens = _executable_tokens(command)
    if not tokens:
        return False
    want_dir = Path(hooks_dir).parts
    expected = root / hooks_dir / script_name
    for token in tokens:
        if Path(token).name != script_name:
            continue
        if "$" not in token:
            candidate = Path(token)
            if not candidate.is_absolute():
                candidate = root / candidate
            try:
                if candidate.resolve() == expected.resolve():
                    return True
            except (OSError, ValueError, RuntimeError):
                pass
            continue  # resolved somewhere else — not ours
        parts = Path(token).parent.parts
        if len(parts) >= len(want_dir) and parts[-len(want_dir):] == want_dir:
            return True
    return False


def _our_command_dicts(entry: object, script_name: str, hooks_dir: str,
                       root: Path) -> list[dict]:
    """Every child command dict of ``entry`` that runs our ``script_name``.

    Claude Code only executes handlers inside an entry's ``hooks`` ARRAY with a
    ``{"type": "command", "command": …}`` shape; a flat ``{type, command}`` at
    the EVENT level, a handler missing its ``type``, and a non-string command
    are all silently IGNORED by the harness.  Counting any of them as our
    registration would leave the project capturing nothing while reporting
    success, so they are foreign and a fresh valid entry is appended instead
    (#3866 makes the same distinction in ``_entry_command_dicts``).  ALL
    children are inspected, never just the first: ours may sit behind a foreign
    hook in the same array, and one left untimed is cancelled at Claude Code's
    1.5 s default.
    """
    if not isinstance(entry, dict):
        return []
    inner = entry.get("hooks")
    if not isinstance(inner, list):
        return []
    found: list[dict] = []
    for item in inner:
        if not isinstance(item, dict) or item.get("type") != "command":
            continue
        command = item.get("command")
        if not isinstance(command, str):
            continue
        if _is_our_script_command(command, script_name, hooks_dir, root):
            found.append(item)
    return found


def merge_capture_hooks(data: dict, *, timeout: int = CLAUDE_TIMEOUT,
                        hooks_dir: str = ".claude/hooks",
                        root: str | os.PathLike[str] = ".") -> dict:
    """Merge the two capture registrations into a Claude ``settings.json``
    document, in place, and return it.

    Merge, never overwrite: every unrelated key, every other event, and any
    foreign hook already registered under ``SessionStart`` / ``SessionEnd``
    survive untouched (a foreign hook is *appended after*, never replaced).  An
    existing registration of ours is repaired in place — the #3754 ``timeout``
    is set when absent or lower than ``timeout``, and never lowered.

    The emitted entry is the shape ``tortoise hooks status`` (#3866) classifies
    as ours: a matcher entry whose ``hooks`` array holds
    ``{"type": "command", "command": ".claude/hooks/<script>", "timeout": 60}``.
    Installing produces an artifact that is therefore already current, and a
    stale install is repaired by ``tortoise hooks upgrade``.

    Raises :class:`ValueError` for a shape that cannot be merged safely (a
    non-dict ``hooks``, a non-list event), so the caller refuses loudly instead
    of clobbering the user's file.
    """
    root_path = Path(root)
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
        data["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise ValueError('"hooks" is not a JSON object')
    for script_name, event in (("session-start.sh", "SessionStart"),
                               ("session-end.sh", "SessionEnd")):
        entries = hooks.get(event)
        if entries is None:
            entries = []
            hooks[event] = entries
        if not isinstance(entries, list):
            raise ValueError(f'"{event}" entries are not a list')
        registered: list[dict] = []
        for entry in entries:
            registered.extend(
                _our_command_dicts(entry, script_name, hooks_dir, root_path))
        if registered:
            # Ours already: repair the timeout on EVERY matching handler, never
            # downgrade a higher one.
            for existing in registered:
                current = existing.get("timeout")
                # ``float`` counts as a real budget too: ``not isinstance(120.0,
                # int)`` is True, so testing ``int`` alone silently LOWERED a
                # deliberate 120.0 to 60 (the docstring's "never lowered"
                # promise).
                if (not isinstance(current, (int, float))
                        or isinstance(current, bool) or current < timeout):
                    existing["timeout"] = timeout
                existing.setdefault("type", "command")
            continue
        entries.append({
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": f"{hooks_dir}/{script_name}",
                "timeout": timeout,
            }],
        })
    return data


def _load_settings(path: Path) -> tuple[dict | None, str]:
    """Read+parse ``settings.json``; ``({}, "")`` when absent.

    Returns ``(None, refusal)`` for anything that must not be clobbered:
    invalid JSON, invalid UTF-8, an unreadable file, or a non-object top level.
    """
    if not path.exists():
        return {}, ""
    if not path.is_file():
        return None, f"{path} exists but is not a regular file — refusing to touch it"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"cannot read {path}: {e.__class__.__name__}: {e}"
    except UnicodeDecodeError as e:
        return None, (f"{path} is not valid UTF-8 ({e}) — refusing to touch it. "
                      "Merge manually.")
    try:
        parsed = json.loads(raw)
    except ValueError as e:
        return None, (f"{path} exists but is not valid JSON ({e}) — refusing to "
                      "touch it. Merge manually.")
    if not isinstance(parsed, dict):
        return None, (f"{path} exists but its top level is not a JSON object — "
                      "refusing to touch it. Merge manually.")
    return parsed, ""


def _install_claude(root: Path, *, dry_run: bool) -> InstallResult:
    harness = "claude"
    hooks_dir = root / ".claude" / "hooks"
    settings_path = root / ".claude" / "settings.json"
    targets = [hooks_dir / name for name in CLAUDE_SCRIPTS]

    # Every shipped artifact must resolve before anything is touched — a
    # wheel that lost package-data must fail here, not install half a seam.
    payloads: dict[str, bytes] = {}
    for name in CLAUDE_SCRIPTS:
        src = PACKAGE_DIR / "claude-hooks" / name
        if not src.is_file():
            return InstallResult(harness, error=(
                f"shipped capture hook not found at {src} — this install is "
                "incomplete (broken package?). Reinstall tortoise."))
        payloads[name] = src.read_bytes()

    for target in [*targets, settings_path]:
        escape = _symlink_escape(root, target, label="install dir")
        if escape:
            return InstallResult(harness, error=f"Refusing: {escape}")
    for name in CLAUDE_SCRIPTS:
        non_regular = _refuse_non_regular(hooks_dir / name)
        if non_regular:
            return InstallResult(harness, error=non_regular)

    # Refuse an unusable settings file BEFORE writing the scripts, so a
    # refusal leaves the project exactly as it was found.
    data, refusal = _load_settings(settings_path)
    if refusal:
        return InstallResult(harness, error=f"Refusing: {refusal}")
    if data is None:  # unreachable (a refusal is set whenever data is None)
        return InstallResult(harness, error=(
            f"Refusing: {settings_path} could not be read — refusing to touch "
            "it. Merge manually."))
    before = json.dumps(data, sort_keys=True)
    try:
        merge_capture_hooks(data, root=root)
    except ValueError as e:
        return InstallResult(harness, error=(
            f"Refusing: {settings_path} has {e} — refusing to touch it. "
            "Merge manually."))

    actions: list[str] = []
    changed = False
    if not dry_run:
        unwritable = _preflight_writable([hooks_dir, settings_path.parent])
        if unwritable:
            return InstallResult(harness, error=f"Refusing: {unwritable}")

    for name in CLAUDE_SCRIPTS:
        dst = hooks_dir / name
        data_bytes = payloads[name]
        if _is_regular_unchanged(dst, data_bytes):
            # Bytes are current — but a hook that LOST its exec bit is not
            # installed: Claude Code executes `.claude/hooks/<name>` directly,
            # and the fail-open script swallows the permission error, so the
            # session files nothing while the install reports success.  This
            # is the `cp`-without-`chmod` legacy state the installer exists to
            # repair, so an unchanged-bytes hook is not automatically a no-op.
            if dst.stat().st_mode & 0o111:
                continue
            repaired = (dst.stat().st_mode & 0o777) | 0o111
            if dry_run:
                actions.append(f"[dry-run] would restore the exec bit on "
                               f"{dst} (mode {repaired:o})")
            else:
                os.chmod(dst, repaired)
                actions.append(f"restored the exec bit on {dst} "
                               f"(mode {repaired:o})")
            changed = True
            continue
        mode = 0o755
        if dst.exists() and dst.is_file() and not dst.is_symlink():
            mode = (dst.stat().st_mode & 0o777) | 0o111
        if dry_run:
            actions.append(f"[dry-run] would install {dst} (mode {mode:o})")
        else:
            _atomic_bytes(dst, data_bytes, mode)
            actions.append(f"installed {dst}")
        changed = True

    if before != json.dumps(data, sort_keys=True):
        if dry_run:
            actions.append(f"[dry-run] would merge SessionStart + SessionEnd "
                           f"capture hooks into {settings_path}")
        else:
            _atomic_bytes(settings_path, (json.dumps(data, indent=2) + "\n").encode("utf-8"),
                          0o644 if not settings_path.exists()
                          else settings_path.stat().st_mode & 0o777)
            actions.append(f"merged SessionStart + SessionEnd capture hooks "
                           f"(timeout {CLAUDE_TIMEOUT}s) into {settings_path}")
        changed = True

    return InstallResult(harness, changed=changed, actions=tuple(actions))


def _install_pi(home: Path, *, dry_run: bool) -> InstallResult:
    harness = "pi"
    ext_dir = home / ".pi" / "agent" / "extensions"
    dst = ext_dir / PI_EXTENSION_NAME
    legacy = ext_dir / LEGACY_PI_DIRNAME
    legacy_disabled = ext_dir / PI_DISABLED_DIRNAME

    src = PACKAGE_DIR / "pi-hooks" / PI_EXTENSION_NAME
    if not src.is_file():
        return InstallResult(harness, error=(
            f"shipped capture extension not found at {src} — this install is "
            "incomplete (broken package?). Reinstall tortoise."))
    payload = src.read_bytes()

    escape = _symlink_escape(home, dst, label="install home")
    if escape:
        return InstallResult(harness, error=f"Refusing: {escape}")
    non_regular = _refuse_non_regular(dst)
    if non_regular:
        return InstallResult(harness, error=non_regular)

    actions: list[str] = []
    changed = False
    if not dry_run:
        unwritable = _preflight_writable([ext_dir])
        if unwritable:
            return InstallResult(harness, error=f"Refusing: {unwritable}")

    # #3713: disable a pre-existing agent-infra extension first — Pi loads a
    # top-level tortoise-capture.ts AND tortoise-capture/index.ts as two
    # extensions, and both POST the same session_id. NON-DESTRUCTIVE: unlink a
    # symlink (the checkout it points at is untouched) or rename a real
    # directory to a dot-prefixed name the loader skips. Never a recursive
    # delete.
    if legacy.is_symlink():
        if dry_run:
            actions.append(f"[dry-run] would unlink legacy extension {legacy}")
        else:
            legacy.unlink()
            actions.append(f"disabled legacy capture extension: unlinked "
                           f"{legacy} (symlink target untouched)")
        changed = True
    elif legacy.is_dir():
        if legacy_disabled.exists() or legacy_disabled.is_symlink():
            return InstallResult(harness, error=(
                f"cannot disable the legacy extension {legacy}: "
                f"{legacy_disabled} already exists. Move one of them aside and "
                "re-run."))
        if dry_run:
            actions.append(f"[dry-run] would rename {legacy} → {legacy_disabled}")
        else:
            legacy.rename(legacy_disabled)
            actions.append(f"disabled legacy capture extension: renamed {legacy} "
                           f"→ {legacy_disabled} (files preserved)")
        changed = True

    if not _is_regular_unchanged(dst, payload):
        if dry_run:
            actions.append(f"[dry-run] would install {dst}")
        else:
            _atomic_bytes(dst, payload, 0o644)
            actions.append(f"installed {dst}")
        changed = True

    return InstallResult(harness, changed=changed, actions=tuple(actions))


def install_capture(
    harness: str,
    *,
    root: str | os.PathLike[str] = ".",
    home: str | os.PathLike[str] | None = None,
    dry_run: bool = False,
) -> InstallResult:
    """Install (or upgrade) the capture seam for ``harness``.

    ``root`` is the project directory the Claude Code seam installs into;
    ``home`` is the home directory the Pi extension installs under (default:
    the running user's home, i.e. what ``~`` resolves to).  Idempotent: a
    second call against an already-current install writes nothing and reports
    ``changed=False``.
    """
    if harness not in CAPTURE_SEAM:
        known = ", ".join(sorted(CAPTURE_SEAM))
        return InstallResult(harness, error=(
            f"no capture seam for harness {harness!r} (known: {known})"))
    if harness == "claude":
        return _install_claude(Path(root), dry_run=dry_run)
    return _install_pi(Path(home) if home is not None else Path.home(),
                       dry_run=dry_run)


__all__ = [
    "CAPTURE_SEAM",
    "CLAUDE_SCRIPTS",
    "CLAUDE_TIMEOUT",
    "InstallResult",
    "install_capture",
    "merge_capture_hooks",
]
