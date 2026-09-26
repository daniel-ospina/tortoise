"""Install the per-harness CAPTURE seam (#3808).

Why this exists
---------------
The capture hook is deliberately fail-open (``2>/dev/null || exit 0``): a
missing or mistyped install files no sessions and reports no error.  This
module installs the seam itself — ``tortoise install claude`` writes the
session-start/session-end scripts plus their merged registration, and
``tortoise install pi`` writes the capture extension — so the product never
depends on a hand-copied script or a hand-merged settings fragment.

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

``codex``
    ``tortoise/codex-hooks/session-end.sh`` into ``<codex_home>/hooks/``
    (mode 0755), and **merges** — never overwrites — the ``SessionEnd``
    registration into ``<codex_home>/hooks.json``. ``<codex_home>`` is
    ``$CODEX_HOME`` when set, else ``~/.codex``. This is deliberately
    HOME-scoped, not project-scoped: **verified live against Codex CLI
    0.154.0 (#3818), ``$CODEX_HOME/hooks.json`` is the only hook source the
    CLI reads** — a project-local ``<repo>/.codex/hooks.json`` (the file the
    read-hook installer writes) and ``<repo>/.codex/config.toml [hooks]``
    both fire nothing, with or without project trust. The registered command
    is the script's absolute path, because Codex runs it from the session's
    cwd.

``cursor``
    ``tortoise/cursor-hooks/session-end.sh`` into ``~/.cursor/hooks/``
    (mode 0755), and **merges** the ``sessionEnd`` registration into
    ``~/.cursor/hooks.json``.  The root is the HOME-scoped ``.cursor`` dir —
    ``CursorHooksService`` resolves ``pathService.userHome() / ".cursor" /
    "hooks.json"`` and Cursor has NO config-dir env var (verified: ``CURSOR_HOME``
    appears nowhere in Cursor 3.20.21's bundle), while a project-local
    ``<repo>/.cursor/hooks.json`` is gated on workspace trust.  Cursor's entry
    is a FLAT ``{"command": …, "timeout": …}`` script object — its own
    validator rejects a nested matcher group and invalidates the WHOLE config.
    ``sessionEnd`` is IDE-only: Cursor's docs state cloud agents have no
    editor-lifetime session boundary.

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
regular file, a foreign artifact at a hook/extension path (refused, never
clobbered).  A differing copy that DOES look like ours is preserved as
``<name>.bak`` before the shipped bytes replace it — the same rule
``tortoise hooks upgrade`` applies.  Writes are atomic (temp file +
``os.replace``) so a failed write cannot leave a half-written
``settings.json`` behind.  **ANY** failure inside the install — a write-time
``OSError`` (an immutable target, ``EROFS``/``ENOSPC``/``EDQUOT``, a directory
that lost its mode), a ``RecursionError`` from ``json.loads`` on a deeply
nested document, a ``RuntimeError`` from a symlink loop, or an unenumerated
member a future path adds — is caught at the :func:`install_capture` boundary
and returned as a populated ``error`` — never raised out of the module into a
CLI traceback — because a failure that escapes the seam is exactly the
half-install (scripts on disk, no registration) this module exists to prevent.
The boundary is ONE catch-all with an explicit ``MemoryError`` re-raise (the
refusal message itself allocates), deliberately not an ``except (A, B, ...)``
tuple: a finite enumeration is refutable by the next member it omits, which is
how ``TypeError`` (#3987) and ``UnicodeDecodeError`` (#3988) escaped the read
half's previous ``(OSError, RuntimeError)`` tuple.

Relationship to ``tortoise/hook_install.py`` (PR #3866)
-------------------------------------------------------
#3866 adds the **drift/repair** path — ``tortoise hooks status|upgrade`` — over
a ``HarnessLayout`` registry, and versions the contract with a
``tortoise-hook-version: N`` marker inside each shipped artifact (``#`` for a
shell hook, ``//`` for the Pi TypeScript extension, #4680).  This module
deliberately does **not** re-declare any of that: it installs the shipped
artifacts *verbatim* (no marker is injected, no version constant is declared)
and emits exactly the settings shape #3866's ``_entry_command_dicts`` /
``_invokes_script`` classify as ours — a matcher entry whose ``hooks`` array
holds ``{"type": "command", "command": ".claude/hooks/<name>", "timeout": 60}``.
The artifact a **layout** install produces is therefore one
``tortoise hooks status`` reads as current, and a stale layout install is
repaired by ``tortoise hooks upgrade`` — #3808 is the mechanism
#3795/#3801 operate through.  There is one version contract, and it lives in
the shipped artifact: the shell half in ``HARNESS_LAYOUTS`` (that status/
upgrade pair), and the Pi artifact half in
``hook_install.ARTIFACT_CONTRACTS["pi"]`` — which those two
commands CANNOT reach, because both resolve through ``get_layout`` and reject
``pi`` outright (#5351).  Pi's installed seam is graded by ``tortoise doctor``
and by ``session verify`` through the same contract, and it is repaired by
``tortoise install pi`` (plus ``hook_install.detect_artifact_install`` for the
read side).

Because the two modules answer the same question — "is this entry ours?" —
:func:`_is_our_script_command` here **delegates** to #3866's
:func:`tortoise.hook_install._invokes_script` rather than re-implementing it:
one classifier, two callers, so the surfaces cannot drift apart.  The earlier
hand-mirrored copy diverged from #3866 on 10 of 21 command forms — it appended
a duplicate registration for ``/bin/sh .claude/hooks/session-end.sh`` (the hook
then ran twice) and reported ``sudo -u <hook>`` as ours, the fail-open shape
#3808 exists to close.  A foreign path that merely shares our basename, a hook
named only as an argument (``cat .claude/hooks/session-end.sh``), a flat
event-level ``{type, command}`` (which Claude Code ignores), and a handler
missing its ``type`` are all **not** ours in both modules; treating any of them
as ours would leave a project capturing nothing while reporting a successful
install.

The same one-definition rule governs whether a hook is *runnable*: this
module's exec-bit repair asks :func:`tortoise.hook_install._has_owner_exec_bit`
— the predicate ``detect_install``/``upgrade_install`` use — instead of
spelling the mask a second time.  A separate ``st_mode & 0o111`` test on each
side made ``0o601``/``0o410`` (a non-owner exec bit is the only exec bit) read
as installed to ``status``/``upgrade`` while this installer repaired it
(#4000 R33).

One intentional difference: #3866 refuses *any* symlink below the install root,
while :func:`_symlink_escape` refuses only those that leave it — a symlink whose
target is inside the project is replaced by a real file rather than refused.
That is never a silent loss (the hook still runs), and #3866's ``status`` reads
the resulting regular file as current.  The carve-out is for the **leaf** (the
script / settings file itself), which ``_atomic_bytes`` swaps for a regular
file.  A symlinked intermediate **directory** (``.claude`` / ``.claude/hooks``
→ an in-root dir) is refused instead: it cannot be replaced by a regular file,
and #3866's ``status`` reports any symlink below the install root as
``symlinked-install`` while ``upgrade`` refuses it — so accepting one would
print "installed" for a state the repair path will not touch.
"""
from __future__ import annotations

import contextlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tortoise import hook_install

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
    "codex": "tortoise/codex-hooks/session-end.sh",
    "cursor": "tortoise/cursor-hooks/session-end.sh",
    "pi": "tortoise/pi-hooks/tortoise-capture.ts",
}

#: The per-hook budget #3754 established and #3801 identified as the
#: load-bearing half of the contract: Claude Code cancels a SessionEnd hook at
#: its 1.5 s default, the budget rises to the highest per-hook timeout, and 60
#: is the documented ceiling.  Must stay equal to the value the dashboard
#: block emits (pinned by ``tests/test_capture_install.py``).
CLAUDE_TIMEOUT = 60

#: The cheaper per-turn budget.  The turn hook spools the transcript locally
#: (no network round-trip), so it does not need SessionEnd's 60 s; the
#: dashboard emits this value too.
CLAUDE_PER_TURN_TIMEOUT = 30

#: Claude Code capture hooks as ``(script, event, timeout)`` — THIS is the
#: single source of truth.  ``CLAUDE_SCRIPTS`` is DERIVED from it, so the files
#: the installer COPIES and the registrations it MERGES can never name
#: different sets of scripts.
#:
#: They once did (#3971 merge): ``hook_install._claude_layout()`` carried
#: ``session-turn.sh`` (added with the per-turn spool) while this module kept a
#: separately-maintained ``CLAUDE_SCRIPTS`` pair, so the installer placed TWO
#: scripts and ``detect_install`` demanded THREE — an install that reports
#: ``missing-script: session-turn.sh`` immediately after installing, with the
#: drift guard unable to say why.
CLAUDE_CAPTURE_HOOKS: tuple[tuple[str, str, int], ...] = (
    ("session-start.sh", "SessionStart", CLAUDE_TIMEOUT),
    ("session-end.sh", "SessionEnd", CLAUDE_TIMEOUT),
    # #3963: the CHEAP per-turn capture.  Capture used to happen only at
    # SessionEnd, which is cancelled at its ~1.5 s default (#3754) and does not
    # fire at all on a kill — so an interrupted session filed nothing.  This
    # hook spools the transcript locally (no network) at every user prompt; the
    # filing is deferred to the SessionStart drain / the SessionEnd flush.
    ("session-turn.sh", "UserPromptSubmit", CLAUDE_PER_TURN_TIMEOUT),
)

#: Claude Code hook scripts → ``.claude/hooks/`` (derived; see above).
CLAUDE_SCRIPTS: tuple[str, ...] = tuple(
    name for name, _, _ in CLAUDE_CAPTURE_HOOKS)

#: Codex's shipped capture hook (one script) and the event it registers.
CODEX_SCRIPT_NAME = "tortoise-session-end.sh"
CODEX_EVENT = "SessionEnd"

#: Cursor's shipped capture hook (one script) and the event it registers.
#: Cursor's `sessionEnd` is an IDE-only event (see the shipped hook's header):
#: cloud agents have no editor-lifetime session boundary.
CURSOR_SCRIPT_NAME = "tortoise-session-end.sh"
CURSOR_EVENT = "sessionEnd"

#: Codex hook registrations live in ``$CODEX_HOME/hooks.json`` — the ONE hook
#: source Codex CLI 0.154.0 actually reads (verified live, #3818): neither the
#: project-local ``<repo>/.codex/hooks.json`` nor ``<repo>/.codex/config.toml
#: [hooks]`` fires, with or without project trust. ``$CODEX_HOME`` is the
#: environment override (default ``~/.codex``).
CODEX_REGISTRATION_FILE = "hooks.json"
CODEX_HOOKS_SUBDIR = "hooks"

#: Cursor hook registrations live in ``~/.cursor/hooks.json`` — the ONE
#: user-scoped hook source Cursor 3.20.21 reads (``CursorHooksService`` joins
#: ``pathService.userHome() / ".cursor" / "hooks.json"``; verified against the
#: installed bundle, #3819).  Unlike Codex there is NO config-dir env var —
#: ``CURSOR_HOME`` does not exist in the app bundle.
CURSOR_REGISTRATION_FILE = "hooks.json"
CURSOR_HOOKS_SUBDIR = "hooks"

#: ``CLAUDE_TIMEOUT`` / ``CLAUDE_PER_TURN_TIMEOUT`` are declared beside
#: ``CLAUDE_CAPTURE_HOOKS`` above — one block, so each budget and the script it
#: belongs to cannot drift apart.

#: The extension name Pi auto-discovers under ``~/.pi/agent/extensions/``.
#: DERIVED from the install-contract registry: the seam's NAME and its version
#: contract are one fact, and the drift detector must inspect exactly the file
#: the installer writes.  Two independent literals could disagree, which would
#: leave the detector checking a path the installer never produced (#4680).
#: Evaluated at import: safe today because ``hook_install`` never imports this
#: module at module level — if that ever changes, this line becomes an
#: ImportError rather than a test failure, so keep the dependency one-way.
PI_EXTENSION_NAME = hook_install.ARTIFACT_CONTRACTS["pi"].install_name

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


def _backup_path(dst: Path) -> Path:
    """The ``<name>.bak`` path a differing install target is preserved to.

    Mirrors :func:`tortoise.hook_install.upgrade_install`: the first free
    ``<name>.bak``, then ``<name>.bak.N``.  A dangling symlink at a candidate
    name is skipped (``exists()`` is False for it, but it would be followed),
    so the backup never writes through a planted link.
    """
    candidate = dst.with_suffix(dst.suffix + ".bak")
    n = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = dst.with_suffix(dst.suffix + f".bak.{n}")
        n += 1
    return candidate


def _foreign_install_refusal(dst: Path, data: bytes) -> str:
    """Refusal when ``dst`` is a differing file that is not a Tortoise artifact.

    The sibling ``hook_install.upgrade_install`` refuses the same file rather
    than silently deactivating another product's hook.  A file whose bytes
    already match the shipped artifact is never foreign (and is handled as an
    unchanged install); a symlink is not inspected here — it is replaced, not
    written through.  Returns ``""`` when ``dst`` is missing, matches, is a
    symlink, or looks like one of ours.
    """
    if dst.is_symlink() or not dst.is_file():
        return ""
    if _is_regular_unchanged(dst, data):
        return ""
    if hook_install._looks_like_our_script(dst):
        return ""
    return (f"Refusing: {dst} exists but does not look like a Tortoise "
            "artifact — move it aside and re-run, so a foreign file is not "
            "silently replaced")


def _symlink_escape(root: Path, target: Path, *, label: str) -> str:
    """Refusal message when any component of ``target`` under ``root`` escapes.

    A repo/config symlink must not write through to a real file elsewhere
    (``.claude/settings.json`` → ``~/.claude/settings.json``).  Resolved paths
    are compared on both sides, so a root reached via a symlinked alias
    (macOS ``/tmp`` → ``/private/tmp``, a git worktree) is not a false
    positive.  Returns ``""`` when the path is safe.

    The in-root carve-out applies to the **leaf** only.  A symlinked
    intermediate directory (``.claude/hooks`` → an in-root directory) is
    refused even though it stays inside the root: the install would write
    through it and report success, while #3866's ``detect_install`` reports
    ``symlinked-install`` and ``upgrade_install`` refuses the same tree — an
    install the repair path cannot maintain is not an install.  A leaf
    symlink (``.claude/hooks/session-end.sh`` → an in-root file) IS accepted:
    ``_atomic_bytes`` replaces the link with a regular file, so the resulting
    state is one ``status`` reads as current.

    A symlink LOOP (``Path.resolve()`` raises ``RuntimeError`` on Py3.12, not
    ``OSError``) is returned as this same refusal message rather than
    escaping ``install_capture`` as a traceback (#3808 R17).
    """
    try:
        root_r = root.resolve()
    except (OSError, RuntimeError) as e:
        # Py3.12 ``Path.resolve()`` raises ``RuntimeError`` (deliberately, not
        # ``OSError``) on a symlink cycle; the module promises a populated
        # result, so a cyclic root is a refusal, never a traceback.
        return (f"{root} cannot be resolved ({e.__class__.__name__}: {e}) — "
                f"the {label} contains a symlink loop. Fix the symlink chain "
                "and re-run.")
    try:
        rel_parts = target.relative_to(root).parts
    except ValueError:
        rel_parts = target.parts  # target outside root — fall back to leaf
    cur = root
    for index, part in enumerate(rel_parts):
        cur = cur / part
        if not cur.is_symlink():
            continue
        try:
            resolved = cur.resolve()
        except (OSError, RuntimeError) as e:
            return (
                f"{cur} cannot be resolved ({e.__class__.__name__}: {e}) — "
                f"a symlink loop in the {label}. Fix the symlink chain "
                "(unlink the cyclic link first) and re-run."
            )
        if resolved != root_r and root_r not in resolved.parents:
            return (
                f"{cur} resolves to {resolved} — outside the {label} "
                f"{root_r}. Symlinked configs are not touched (unlink the "
                "symlink first)."
            )
        if index != len(rel_parts) - 1:
            return (
                f"{cur} is a symlink to {resolved}, inside the {label} "
                f"{root_r} — a symlinked hooks directory is not an install "
                "`tortoise hooks status` reads as current (and `tortoise "
                "hooks upgrade` refuses it), so nothing is written through "
                "it. Replace the symlink with a real directory and re-run."
            )
    return ""


def _preflight_probe(dirs: list[Path]) -> str:
    """The WRITE-FREE half of the pre-flight: could ``dirs`` be installed to?

    Every target must be creatable-or-usable as found: any existing component
    at the target must already be a directory, an existing target must itself
    be writable, and the nearest existing ancestor of a not-yet-existing
    target must be writable (else the real run's ``mkdir`` fails).  Nothing is
    created or modified, so ``--dry-run`` runs it too (#3808 R25): a dry run
    over an impossible install (``.claude/hooks`` is a file) must refuse with
    a non-zero exit exactly as the real run does, instead of printing
    ``[dry-run] would install …`` and exiting 0.

    Returns ``""`` on success, a refusal message otherwise.
    """
    for d in dirs:
        anc = d
        while not anc.exists() and not anc.is_symlink() and anc != anc.parent:
            anc = anc.parent
        if not anc.is_dir():
            return (f"cannot install into {d}: {anc} is not a directory "
                    f"(move it aside or pass an explicit install root)")
        # A not-yet-existing target is created under ``anc``; a dangling
        # symlink is left as the target itself (``mkdir`` would raise).
        probe = d if (d.exists() or d.is_symlink()) else anc
        if not os.access(probe, os.W_OK):
            return f"cannot install into {d}: directory is not writable"
    return ""


def _preflight_writable(dirs: list[Path]) -> str:
    """Create ``dirs`` and prove each is writable *before* the first write.

    A half-install that reports success is the failure mode this exists to
    prevent: an unwritable hooks dir must abort the whole install before a
    script or a settings entry is written, not after.  Returns ``""`` on
    success, a refusal message otherwise.

    ``_preflight_probe`` runs first so a refusal that needs no write (an
    impossible path) is identical under ``--dry-run`` and a real run; the
    ``mkdir`` below is the only write this function performs.
    """
    refusal = _preflight_probe(dirs)
    if refusal:
        return refusal
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return f"cannot create {d}: {e.__class__.__name__}: {e}"
        if not os.access(d, os.W_OK):
            return f"cannot install into {d}: directory is not writable"
    return ""


def _is_our_script_command(command: str, script_name: str,
                           hooks_dir: str, root: Path) -> bool:
    """True when ``command`` EXECUTES this project's hook ``script_name``.

    Delegates to :func:`tortoise.hook_install._invokes_script` — the ONE
    classifier the drift/repair path (``tortoise hooks status|upgrade``) also
    uses.  A second copy here, however carefully mirrored, drifted from it on
    10 of 21 command forms (``/bin/sh <hook>`` was appended a SECOND
    registration, so the hook ran twice; ``sudo -u <hook>`` was read as ours,
    which is the fail-open shape that stamps a timeout with no real
    registration).  Delegation enforces the contract structurally: one
    function, two callers, no copy to diverge.

    Confinement to the install is provided by ``hook_install`` itself — a
    token resolving to a DIFFERENT file (a vendored
    ``vendor/.claude/hooks/session-end.sh``, an absolute path outside
    ``root``) is somebody else's hook, while a ``$VAR`` token
    (``$CLAUDE_PROJECT_DIR/.claude/hooks/…``, the form Claude Code documents)
    falls back to the shared directory-suffix rule.
    """
    return hook_install._invokes_script(command, script_name, hooks_dir, root)


def _our_command_dicts(entry: object, script_name: str, hooks_dir: str,
                       root: Path) -> list[dict]:
    """Every child command dict of ``entry`` that runs our ``script_name``.

    DELEGATES to :func:`tortoise.hook_install._entry_command_dicts` — the ONE
    entry-shape reader the drift/repair path also uses.  This was a
    hand-mirrored copy, and a copy is exactly the drift this module already
    paid for on the tokenizer side: tightening the shape gate here (or there)
    left the other surface reading a flat event-level ``{type, command}`` —
    which Claude Code silently IGNORES — as a live registration, so the
    install reported success while the project captured nothing.  One
    function, two callers: the entry-shape contract (an entry needs a
    ``hooks`` ARRAY of properly typed child commands) cannot diverge between
    the installer and ``tortoise hooks status|upgrade``.  ALL children are
    inspected, never just the first: ours may sit behind a foreign hook in the
    same array, and one left untimed is cancelled at Claude Code's 1.5 s
    default.
    """
    return hook_install._entry_command_dicts(entry, script_name, hooks_dir, root)


def merge_capture_hooks(data: dict, *, timeout: int | None = None,
                        hooks_dir: str = ".claude/hooks",
                        root: str | os.PathLike[str] = ".") -> dict:
    """Merge the capture registrations into a Claude ``settings.json``
    document, in place, and return it.

    Merge, never overwrite: every unrelated key, every other event, and any
    foreign hook already registered under ``SessionStart`` / ``SessionEnd`` /
    ``UserPromptSubmit`` survive untouched (a foreign hook is *appended after*,
    never replaced).  An existing registration of ours is repaired in place —
    the #3754 ``timeout`` is set when absent or lower than the script's budget
    (#3963: per-script, so the per-turn hook keeps its cheaper 30 s), and never
    lowered.

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
    for script_name, event, script_timeout in CLAUDE_CAPTURE_HOOKS:
        # An explicit ``timeout`` overrides every script's declared budget;
        # otherwise each entry keeps the budget declared beside it above.
        effective = script_timeout if timeout is None else timeout
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
                # Share ONE predicate with ``hook_install`` (#3866): a ``float``
                # counts as a real budget too, so testing ``int`` alone here
                # silently LOWERED a deliberate 120.0 to 60 (the docstring's
                # "never lowered" promise) — and the same divergence made
                # ``tortoise hooks status`` report blocking drift on the state
                # this installer preserves.
                if (not hook_install._is_timeout_budget(current)
                        or current < effective):
                    existing["timeout"] = effective
                existing.setdefault("type", "command")
            continue
        entries.append({
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": f"{hooks_dir}/{script_name}",
                "timeout": effective,
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


def codex_home(home: Path) -> Path:
    """Resolve Codex's config root: ``$CODEX_HOME`` when set, else ``~/.codex``.

    Codex honors ``CODEX_HOME`` for its whole config/auth tree, so an install
    that ignored it would register the hook in a file Codex never reads on
    every non-default setup. ``home`` is the user home (injectable for tests).

    DELEGATES to :func:`tortoise.hook_install.default_root` — the ONE resolver
    ``tortoise hooks status|upgrade`` also uses, so the installer and the CLI
    can never disagree about where Codex's hooks live (#3818).
    """
    return hook_install.default_root(
        hook_install.get_layout("codex"), home)


def cursor_home(home: Path) -> Path:
    """Resolve Cursor's config root: ``~/.cursor`` (no env override).

    Cursor resolves its hook source as
    ``pathService.userHome() / ".cursor" / "hooks.json"`` (verified against
    Cursor 3.20.21's bundle, #3819) and has NO config-dir env var — unlike
    Codex's ``CODEX_HOME``, ``CURSOR_HOME`` does not exist.  ``home`` is the
    user home (injectable for tests).

    DELEGATES to :func:`tortoise.hook_install.default_root` — the ONE resolver
    ``tortoise hooks status|upgrade`` also uses, so the installer and the CLI
    can never disagree about where Cursor's hooks live.
    """
    return hook_install.default_root(
        hook_install.get_layout("cursor"), home)


def pi_home(home: Path) -> Path:
    """Resolve Pi's extension root: ``~/.pi/agent/extensions``.

    Pi has NO ``HarnessLayout`` (its seam is not a scripted hook, so
    ``hook_install.default_root`` cannot answer for it), so the directory is
    part of the artifact contract in ``hook_install`` — the
    ``ARTIFACT_CONTRACTS["pi"].root_relpath`` field — which this delegates to,
    so the contract, the installer, ``session verify`` and ``doctor`` all read
    ONE declaration of where the seam lives (#4680).
    """
    root = hook_install.artifact_root("pi", Path(home))
    if root is None:  # pragma: no cover - "pi" is a registered contract
        raise RuntimeError(
            "'pi' is missing from hook_install.ARTIFACT_CONTRACTS")
    return root


def _merge_capture_hooks(data: dict, *, script_name: str, event: str,
                         command: str, root: str | os.PathLike[str],
                         hooks_dir: str, flat: bool) -> dict:
    """Shared merge for a HOME-scoped harness's capture registration.

    ONE implementation for Codex and Cursor: only ``flat`` and the event/
    script names differ, and a per-harness copy is exactly the sibling
    divergence #4024 paid four review cycles for.

    Merge, never overwrite: every unrelated key, every other event, and any
    foreign hook already registered under ``event`` survive untouched (a
    foreign hook is *appended after*, never replaced). An existing
    registration of ours is repaired in place to the current ``command`` —
    these harnesses resolve the command string relative to their own cwd, so a
    relative or stale-path registration is a silent no-capture; the absolute
    path the installer emits is the fix.

    ``flat`` emits Cursor's flat script object (``{"command": …}``); the
    default emits the nested matcher-group shape Codex 0.154.0 accepts
    (``[{hooks: [{type: "command", command: …}]}]`` — the same shape
    ``hook_install._entry_command_dicts`` reads; a flat event-level
    ``{type, command}`` is silently ignored by Codex).

    Raises :class:`ValueError` for a shape that cannot be merged safely.
    """
    root_path = Path(root)
    if flat:
        # Cursor's `hooks.json` REQUIRES a positive-integer `version`; without
        # it Cursor rejects the WHOLE file and loads NO hooks (verified live:
        # Cursor 3.20.21 logs `Invalid user config: Config version must be a
        # number`).  Set it when absent/invalid; never overwrite a valid value.
        version = data.get("version")
        if not hook_install._is_positive_int_value(version):
            data["version"] = 1
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
        data["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise ValueError('"hooks" is not a JSON object')
    entries = hooks.get(event)
    if entries is None:
        entries = []
        hooks[event] = entries
    if not isinstance(entries, list):
        raise ValueError(f'"{event}" entries are not a list')
    if flat:
        # Cursor's validator rejects the WHOLE document — after which NO hook
        # fires — on an unknown event key, a non-list event, or any entry it
        # cannot parse.  Refuse loudly rather than append beside a broken
        # entry and report success (#3819).  (The version key is set above,
        # and structure is checked independently of it so a bad version can
        # never mask a structural defect.)
        refusal = hook_install._flat_structure_refusal(data)
        if refusal:
            raise ValueError(f"hooks.json {refusal}")
    registered: list[dict] = []
    for entry in entries:
        registered.extend(hook_install._entry_command_dicts(
            entry, script_name, hooks_dir, root_path, flat=flat))
    if registered:
        for existing in registered:
            existing["command"] = command
            if not flat:
                existing.setdefault("type", "command")
        return data
    if flat:
        entries.append({"command": command})
    else:
        entries.append({"hooks": [{"type": "command", "command": command}]})
    return data


def merge_codex_capture_hooks(data: dict, *, command: str,
                              root: str | os.PathLike[str] = ".",
                              hooks_dir: str = CODEX_HOOKS_SUBDIR) -> dict:
    """Merge the Codex ``SessionEnd`` capture registration into a
    ``hooks.json`` document, in place, and return it.

    Thin wrapper over :func:`_merge_capture_hooks` (``flat=False``); see there
    for the merge/repair contract and the entry shape.
    """
    return _merge_capture_hooks(
        data, script_name=CODEX_SCRIPT_NAME, event=CODEX_EVENT,
        command=command, root=root, hooks_dir=hooks_dir, flat=False)


def merge_cursor_capture_hooks(data: dict, *, command: str,
                               root: str | os.PathLike[str] = ".",
                               hooks_dir: str = CURSOR_HOOKS_SUBDIR) -> dict:
    """Merge the Cursor ``sessionEnd`` capture registration into a
    ``hooks.json`` document, in place, and return it.

    Thin wrapper over :func:`_merge_capture_hooks` (``flat=True``).  Cursor's
    entry is a FLAT script object: its own validator rejects a nested
    ``{hooks: [...]}`` entry, which invalidates the WHOLE ``hooks.json`` and
    silently disables every Cursor hook (#3819).
    """
    return _merge_capture_hooks(
        data, script_name=CURSOR_SCRIPT_NAME, event=CURSOR_EVENT,
        command=command, root=root, hooks_dir=hooks_dir, flat=True)


def _install_home_scoped(harness: str, home: Path, *,
                         dry_run: bool) -> InstallResult:
    """Install a HOME-scoped capture seam described by its HarnessLayout.

    ONE implementation for every HOME-scoped harness (Codex #3818, Cursor
    #3819): the layout supplies the root env var, hooks dir, registration
    file, entry shape, and shipped script, so the two seams cannot drift.

    The registration file is ``<root>/hooks.json`` — the only hook source the
    harness reads — and the shipped script is copied to ``<root>/hooks/``.
    The registered command is the script's ABSOLUTE path: the hook runs from
    the harness's own cwd, so a relative path would not resolve.
    """
    layout = hook_install.get_layout(harness)
    spec = layout.scripts[0]
    root = hook_install.default_root(layout, home)
    hooks_dir = layout.hooks_root(root)
    dst = hooks_dir / spec.name
    settings_path = layout.settings_path(root)
    assert settings_path is not None  # every HOME-scoped layout declares one

    src = spec.source
    if not src.is_file():
        return InstallResult(harness, error=(
            f"shipped capture hook not found at {src} — this install is "
            "incomplete (broken package?). Reinstall tortoise."))
    payload = src.read_bytes()

    for target in (dst, settings_path):
        escape = _symlink_escape(
            root, target, label=f"{harness} config dir")
        if escape:
            return InstallResult(harness, error=f"Refusing: {escape}")
    non_regular = _refuse_non_regular(dst)
    if non_regular:
        return InstallResult(harness, error=non_regular)
    foreign = _foreign_install_refusal(dst, payload)
    if foreign:
        return InstallResult(harness, error=foreign)

    data, refusal = _load_settings(settings_path)
    if refusal:
        return InstallResult(harness, error=f"Refusing: {refusal}")
    if data is None:  # unreachable (a refusal is set whenever data is None)
        return InstallResult(harness, error=(
            f"Refusing: {settings_path} could not be read — refusing to touch "
            "it. Merge manually."))
    before = json.dumps(data, sort_keys=True)
    command = hook_install._spec_command(layout, spec, root)
    try:
        _merge_capture_hooks(
            data, script_name=spec.name, event=spec.event, command=command,
            root=root, hooks_dir=layout.hooks_dir, flat=layout.flat_entry)
    except ValueError as e:
        return InstallResult(harness, error=(
            f"Refusing: {settings_path} has {e} — refusing to touch it. "
            "Merge manually."))

    unwritable = _preflight_probe([hooks_dir, settings_path.parent])
    if not unwritable and not dry_run:
        unwritable = _preflight_writable([hooks_dir, settings_path.parent])
    if unwritable:
        return InstallResult(harness, error=f"Refusing: {unwritable}")

    actions: list[str] = []
    changed = _install_script(dst, payload, dry_run=dry_run, actions=actions)

    if before != json.dumps(data, sort_keys=True):
        if dry_run:
            actions.append(f"[dry-run] would merge the {spec.event} capture "
                           f"hook into {settings_path}")
        else:
            _atomic_bytes(settings_path,
                          (json.dumps(data, indent=2) + "\n").encode("utf-8"),
                          0o644 if not settings_path.exists()
                          else settings_path.stat().st_mode & 0o777)
            actions.append(f"merged the {spec.event} capture hook into "
                           f"{settings_path}")
        changed = True

    return InstallResult(harness, changed=changed, actions=tuple(actions))


def _install_script(dst: Path, payload: bytes, *, dry_run: bool,
                    actions: list[str]) -> bool:
    """Write/repair one hook script at ``dst``; return whether it changed.

    Shared by the Claude and Codex halves so the two seams cannot drift:

    * bytes already current ⇒ no write, but an unchanged script that lost its
      OWNER exec bit is repaired — Claude Code / Codex execute the hook
      directly, and the fail-open script swallows the permission error, so a
      `cp`-without-`chmod` hook files nothing while the install reports
      success. The predicate is the ONE ``hook_install._has_owner_exec_bit``
      ``status``/``upgrade`` also use (a non-owner exec bit is not enough,
      #4000).
    * a differing copy that passed the ownership guard is a stale/edited
      Tortoise hook — preserved as ``<name>.bak`` before the shipped bytes
      replace it (exactly as ``tortoise hooks upgrade`` does).
    """
    if _is_regular_unchanged(dst, payload):
        if hook_install._has_owner_exec_bit(dst.stat().st_mode):
            return False
        repaired = (dst.stat().st_mode & 0o777) | stat.S_IXUSR
        if dry_run:
            actions.append(f"[dry-run] would restore the exec bit on {dst} "
                           f"(mode {repaired:o})")
        else:
            os.chmod(dst, repaired)
            actions.append(f"restored the exec bit on {dst} "
                           f"(mode {repaired:o})")
        return True
    mode = 0o755
    if dst.exists() and dst.is_file() and not dst.is_symlink():
        mode = (dst.stat().st_mode & 0o777) | 0o111
        backup = _backup_path(dst)
        if dry_run:
            actions.append(f"[dry-run] would back up {dst} → {backup}")
        else:
            _atomic_bytes(backup, dst.read_bytes(), 0o600)
            actions.append(f"backed up {dst} → {backup}")
    if dry_run:
        actions.append(f"[dry-run] would install {dst} (mode {mode:o})")
    else:
        _atomic_bytes(dst, payload, mode)
        actions.append(f"installed {dst}")
    return True


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
    # Ownership guard, mirroring ``hook_install.upgrade_install``: a differing
    # file at OUR path that does not look like a Tortoise hook is another
    # product's — refuse it whole (before anything is written) instead of
    # clobbering it.  A differing copy that DOES look like ours is a
    # stale/edited install: it is preserved as ``<name>.bak`` below.
    for name in CLAUDE_SCRIPTS:
        foreign = _foreign_install_refusal(hooks_dir / name, payloads[name])
        if foreign:
            return InstallResult(harness, error=foreign)

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
    # The write-free probe runs on BOTH paths (#3808 R25), so `--dry-run`
    # refuses an impossible install with the same non-zero exit as the real
    # run; only the `mkdir` half is skipped when dry.
    unwritable = _preflight_probe([hooks_dir, settings_path.parent])
    if not unwritable and not dry_run:
        unwritable = _preflight_writable([hooks_dir, settings_path.parent])
    if unwritable:
        return InstallResult(harness, error=f"Refusing: {unwritable}")

    for name in CLAUDE_SCRIPTS:
        # The exec-bit repair / stale-backup rules live in the ONE
        # ``_install_script`` the Codex half shares, so the two seams cannot
        # disagree about what "installed" means (#4000).
        if _install_script(hooks_dir / name, payloads[name],
                           dry_run=dry_run, actions=actions):
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
    ext_dir = pi_home(home)
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
    # Ownership guard, mirroring the claude half and
    # ``hook_install.upgrade_install``: a differing, foreign extension at this
    # path is never silently replaced.  It is refused BEFORE the legacy
    # disable below, so a refusal leaves the whole home untouched.
    foreign = _foreign_install_refusal(dst, payload)
    if foreign:
        return InstallResult(harness, error=foreign)

    actions: list[str] = []
    changed = False
    # Same split as ``_install_claude`` (#3808 R25): the write-free probe runs
    # under ``--dry-run`` too, so the two halves agree on an impossible home.
    unwritable = _preflight_probe([ext_dir])
    if not unwritable and not dry_run:
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
        if dst.exists() and dst.is_file() and not dst.is_symlink():
            # A differing copy that passed the ownership guard is a stale or
            # locally edited Tortoise extension — preserve it before the
            # shipped bytes replace it (mirrors ``tortoise hooks upgrade``).
            backup = _backup_path(dst)
            if dry_run:
                actions.append(f"[dry-run] would back up {dst} → {backup}")
            else:
                _atomic_bytes(backup, dst.read_bytes(), 0o600)
                actions.append(f"backed up {dst} → {backup}")
        if dry_run:
            actions.append(f"[dry-run] would install {dst}")
        else:
            _atomic_bytes(dst, payload, 0o644)
            actions.append(f"installed {dst}")
        changed = True

    return InstallResult(harness, changed=changed, actions=tuple(actions))


def _install_failed(harness: str, e: Exception) -> InstallResult:
    """The populated failure result for ANY failure inside the install.

    The install runs behind ONE catch-all (see :func:`install_capture`), so
    this is not keyed to a write-time ``OSError``: a ``RecursionError`` from
    ``json.loads`` on a deeply nested ``settings.json`` (#3999), a
    ``RuntimeError`` from a symlink loop, a ``TypeError`` from a wrong-shape
    document, a ``UnicodeDecodeError`` from a non-UTF-8 one, or anything
    unenumerated a future read/parse/pre-flight/write path adds are all routed
    here.  Keying the boundary to one class is exactly the defect that let
    those members escape (a finite ``except (A, B, ...)`` tuple is refutable by
    the next unenumerated member).

    ``where`` is the failing filename when the exception carries one (every
    ``OSError`` does) and the install target otherwise.  ``NOT fully
    installed`` is load-bearing: the capture hook is fail-open, so a
    half-install files no sessions and says nothing — this string is the ONLY
    signal that the seam is incomplete.
    """
    where = getattr(e, "filename", None) or "the install target"
    return InstallResult(harness, error=(
        f"install failed at {where}: {e.__class__.__name__}: {e} — the capture "
        "seam is NOT fully installed. Re-run once the cause is fixed; "
        "`tortoise hooks status` reports what is on disk."))


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

    The whole install runs inside ONE catch-all so EVERY failure path
    populates :attr:`InstallResult.error` (non-empty stderr + non-zero exit at
    the CLI) instead of escaping as a traceback: ``except MemoryError: raise``
    first (resource exhaustion is not a refusal; the handler allocates), then
    ``except Exception``.  Deliberately NOT an ``except (A, B, ...)`` tuple —
    a finite enumeration is refutable by the next unenumerated member, which
    is how ``RecursionError`` (a ``RuntimeError`` from ``json.loads`` on a
    deeply nested document, #3999) escaped the previous ``except OSError``.
    ``KeyboardInterrupt``/``SystemExit`` are ``BaseException``, outside
    ``except Exception``, and still propagate.
    """
    if harness not in CAPTURE_SEAM:
        known = ", ".join(sorted(CAPTURE_SEAM))
        return InstallResult(harness, error=(
            f"no capture seam for harness {harness!r} (known: {known})"))
    if harness == "claude":
        try:
            result = _install_claude(Path(root), dry_run=dry_run)
        except MemoryError:
            raise  # resource exhaustion is not a refusal; the handler allocates
        except Exception as e:
            return _install_failed(harness, e)
    elif harness in ("codex", "cursor"):
        # HOME-scoped harnesses share ONE installer; the layout supplies every
        # harness-specific fact (#3818, #3819).
        try:
            result = _install_home_scoped(
                harness,
                Path(home) if home is not None else Path.home(),
                dry_run=dry_run)
        except MemoryError:
            raise  # resource exhaustion is not a refusal; the handler allocates
        except Exception as e:
            return _install_failed(harness, e)
    else:
        try:
            result = _install_pi(
                Path(home) if home is not None else Path.home(),
                dry_run=dry_run)
        except MemoryError:
            raise  # resource exhaustion is not a refusal; the handler allocates
        except Exception as e:
            return _install_failed(harness, e)

    # Record where this install came FROM, so the INSTALLED hook can resolve
    # its module dir instead of falling through to its own silent no-op — from
    # `~/.codex/hooks/`, `$(dirname "$0")/../..` is `$HOME`, not a checkout
    # (#4314). Best-effort and only after a successful real write: a dry run or
    # a refusal leaves no record to be misread.  The ONE need-based rule
    # (``record_hook_src_dir_for_install``) writes it only when the installed
    # hook cannot resolve `../..` on its own — the SAME condition the hook
    # reads it under — so it fixes the Codex/Cursor HOME installs and a Claude
    # project install, while a repo-scoped `--dir` whose `../..` IS a checkout
    # writes nothing (#4110, #4314).
    if result.ok and not dry_run:
        resolved_home = Path(home) if home is not None else Path.home()
        # A harness with no shell-hook layout (`pi` — a TypeScript extension,
        # not a `session-end.sh`) has no hooks_dir for the `../..` fallback to
        # be derived from, so the pre-ruling `get_layout` call raised and the
        # whole install crashed (the no-fake-layout ruling; recorded on
        # #4680).  Only a layout-bearing harness has a non-trivial
        # `default_root`; for the others the record is skipped by
        # `record_hook_src_dir_for_install` itself.
        _layout = hook_install.get_layout_optional(harness)
        if _layout is None or harness == "claude":
            effective_root = Path(root)
        else:
            effective_root = hook_install.default_root(_layout, resolved_home)
        # Best-effort and unable to fail the install: a record-write raise
        # (``OSError``, ``UnicodeDecodeError``, …) must never turn a landed
        # install into a traceback (#3999, #4314).
        hook_install.record_hook_src_dir_for_install(
            harness, root=effective_root,
            home=Path(home) if home is not None else None)
    return result


__all__ = [
    "CAPTURE_SEAM",
    "CLAUDE_CAPTURE_HOOKS",
    "CLAUDE_PER_TURN_TIMEOUT",
    "CLAUDE_SCRIPTS",
    "CLAUDE_TIMEOUT",
    "CODEX_EVENT",
    "CODEX_REGISTRATION_FILE",
    "CODEX_SCRIPT_NAME",
    "CURSOR_EVENT",
    "CURSOR_REGISTRATION_FILE",
    "CURSOR_SCRIPT_NAME",
    "PI_EXTENSION_NAME",
    "InstallResult",
    "codex_home",
    "cursor_home",
    "install_capture",
    "merge_capture_hooks",
    "merge_codex_capture_hooks",
    "merge_cursor_capture_hooks",
    "pi_home",
]
