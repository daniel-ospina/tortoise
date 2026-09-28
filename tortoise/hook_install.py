"""Harness-agnostic install / drift / upgrade for capture-hook artifacts.

Why this exists (#3795, #3801)
-------------------------------
The Claude Code capture seam is a *manual copy*: the user copies the two
shipped hook scripts into ``.claude/hooks/`` and separately merges a
``settings.json`` fragment.  Because the copy is byte-frozen at install time,
every already-installed host keeps whatever revision it copied — and the
#3755/#3754 fixes (probe decoupling, the load-bearing ``timeout``) reached
**fresh installs only**.  A stale install silently files no sessions: the
hook is fail-open (``2>/dev/null || exit 0``) and there is no marker, no
drift check, and nothing that tells the user to re-copy.

This module is the mechanism that closes that gap.  It is deliberately
**harness-agnostic**: a harness is described by a :class:`HarnessLayout`
(where its hook dir lives, which scripts belong to it, and — optionally —
the JSON settings file whose entries carry the per-hook ``timeout``).  Today
only ``claude`` is registered; the Cursor and Codex seams (#3819, #3818) plug
in by adding a layout, not by forking this logic.  A layout may omit the
settings file entirely (a scripts-only harness), which is why the settings
half is an adapter rather than an assumption.

Where the "current version" lives (single source of truth)
----------------------------------------------------------
The version is **the marker inside the shipped artifact itself** — there is no
constant, no separate manifest, and nothing to keep in sync::

    # tortoise-hook-version: 3        <- column-0 header line, one per file
    // tortoise-hook-version: 1       <- the same marker in a non-shell artifact

``read_hook_version()`` reads that line out of a file; the "expected" version
for an install is read from the *repo* copy of the artifact, and the "installed"
version from the *user's* copy.  The comparison is therefore directly
source-vs-installed, and the number a user greps is the same number this code
acts on.  All scripts in a layout must declare the **same** generation
(:func:`contract_version` returns ``None`` if they disagree) so the contract
covering both halves — script bytes *and* the settings ``timeout`` — has one
visible number.  The marker is bumped on every behavioural edit to a shipped
artifact, and on every change to the install contract it participates in.

The marker's comment prefix is the artifact's language, not a property of the
contract: a shell hook writes ``#`` and the Pi seam (TypeScript, #4680) writes
``//``.  Both are column-0 anchored, which is what makes the marker canonical.

Deliberately *ignored*: indented markers inside a script body (the historical
``  # tortoise-hook-version: 2`` site markers).  Only a column-0 header line is
the canonical declaration, so a stray in-code comment can never be mistaken
for the contract version.

Detect vs. upgrade
------------------
:func:`detect_install` reports drift without writing anything (the
``tortoise hooks status`` / ``tortoise doctor`` surface);
:func:`upgrade_install` repairs it in place.  The settings half is **merged**,
never overwritten: unrelated events, foreign hooks in the same event list, and
every other settings key survive.  The script half is re-copied from the repo;
any copy whose bytes differ from the shipped hook is first preserved as
``<name>.bak`` (the pre-#3795 population has no marker, so a rule keyed on the
marker would destroy exactly the customizations this migration must protect).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

#: The marker token.  A canonical marker is a column-0 comment line of the
#: form ``# tortoise-hook-version: <int>`` (shell) or
#: ``// tortoise-hook-version: <int>`` (TypeScript — the Pi seam, #4680).
HOOK_VERSION_TOKEN = "tortoise-hook-version"

# Column-0 anchored on purpose: an indented marker (a comment inside a script
# body) is a historical site marker, never the contract declaration.  The two
# comment prefixes are the artifact's LANGUAGE, not a per-harness setting: a
# shell hook cannot open a line with ``//`` and a TypeScript module cannot open
# one with ``#``, so accepting both here costs nothing and lets ONE reader
# serve every shipped artifact (#4680).
_HOOK_VERSION_RE = re.compile(
    rf"^(?:#|//)\s*{re.escape(HOOK_VERSION_TOKEN)}:\s*(\d+)\s*$", re.MULTILINE
)

#: Where the shipped hook scripts live inside this package.
_HOOKS_SOURCE_DIR = Path(__file__).resolve().parent / "claude-hooks"

#: Where the installer records the module dir it installed FROM, relative to
#: ``$HOME``.  An installed hook sits under ``~/.codex`` / ``~/.cursor`` /
#: ``~/.claude``, so ``$(dirname "$0")/../..`` is ``$HOME`` — not a checkout —
#: and a hook that trusted it captured nothing while still exiting 0 (#4314).
HOOK_SRC_DIR_RELPATH = Path(".tortoise") / "hook-src-dir"

#: The ``kind`` marker on the local ``capture-errors/<harness>.json``
#: breadcrumb.  Two writers share that ONE path, so the reader must be able to
#: tell them apart in BOTH directions:
#:
#: * the shipped shell hooks write :data:`KIND_INSTALL_INERT` when the installed
#:   hook resolved no module dir (its install leg is inert);
#: * ``tortoise.__main__._record_capture_error`` writes
#:   :data:`KIND_CAPTURE_FAILURE` when a ``sessions import`` capture attempt
#:   failed (an API outage, a parse failure, zero turns).
#:
#: The write condition and the read condition are the SAME condition: session
#: verify accepts a breadcrumb as install-inert evidence ONLY when this marker
#: is present and equal to ``KIND_INSTALL_INERT``, so a capture outage can
#: never read as an inert install.  Conversely a reader looking for a capture
#: failure must exclude the install-inert kind, so an inert install can never
#: read as a failed capture.
KIND_INSTALL_INERT = "install-inert"
KIND_CAPTURE_FAILURE = "capture-failure"

#: The ``kind`` marker on the local ``hook-runs/<harness>.json`` observation.
#:
#: This is a THIRD local writer, and it deliberately lives in its own file
#: rather than as a third ``kind`` on the two-writer
#: ``capture-errors/<harness>.json`` path above: that path's readers key on an
#: exact marker (``session verify`` accepts it as install-inert evidence ONLY
#: when ``kind == KIND_INSTALL_INERT``), so adding a writer there invites
#: exactly the read/write divergence #4314 fixed.
#:
#: #3797: what it records is that the INSTALLED HOOK RAN.  Before it existed,
#: a host that copied the hook but never ran ``tortoise init`` produced no
#: observation at all — the probe is refused at the credential gate before any
#: request is dispatched, the server route is auth-gated, and the hook
#: discarded the exit code — so "installed and ran" was indistinguishable from
#: "not installed".  The record is written by the HOOK (the only faithful
#: witness: ``tortoise session probe`` is also invoked by hand and by other
#: harnesses), it carries no credential and no content (harness, timestamp,
#: and the probe's outcome), and it is best-effort — a failed write can never
#: change the hook's exit-0 contract.  The read condition is the write
#: condition: a reader accepts the record only when ``kind`` equals this
#: marker AND the recorded ``harness`` matches the one asked about, so a
#: foreign or corrupt file can never read as a run.
KIND_HOOK_RUN = "hook-run"

#: The script generation at which the hook-run observation was INTRODUCED
#: (#3797).  An INSTALLED ``session-start.sh`` below it cannot record a run, so
#: its silence is not evidence that the hook never ran: the reader must say it
#: could not tell, never render the absence as an observation.  While the write
#: contract stays unchanged this coincides with the shipped ``session-start.sh``
#: generation; a later UNRELATED behaviour bump moves the shipped marker past
#: it, and that is correct — an install at or above this generation can still
#: write the record, so this floor must NOT be raised to follow such a bump.
HOOK_RUN_GENERATION = 7


def local_state_dir(leaf: str) -> Path:
    """The HOME-scoped local-state directory ``leaf`` — ONE derivation.

    #3797: the record a hook writes (``hook-runs/``) and the capture breadcrumb
    (``capture-errors/``) sit under the SAME base, and it is derived in several
    places.  This function is the spelling used by
    ``__main__._capture_error_file``, ``__main__._hook_run_file`` (and so
    ``_read_hook_run``) and ``capture_spool._clear_breadcrumb_for``, and
    ``_tortoise_state_dir`` in ``tortoise/claude-hooks/session-start.sh`` is
    the shell one, kept in step by tests rather than by hope.

    The copies that are NOT routed through here must move in lockstep with any
    change to the rule, and are tracked in #5503:

    * ``session_verify._local_capture_error_file`` — a deliberately
      env-parameterised TWIN (it resolves HOME from the environment the hook
      was FIRED with, #4314), so it re-derives this base by hand;
    * the ``sessions import`` RECEIPT path in ``__main__._cmd_sessions_import``
      and its reader ``session_verify._local_import_receipt`` — a copy whose
      EMPTY-override behaviour differs (the writer takes ``""`` as a value,
      the reader as unset);
    * the five sibling shell hooks, which do not drop a trailing ``/.``.

    ``TORTOISE_IMPORT_RECEIPT_DIR`` names the RECEIPT dir, so the base is its
    ``.parent``.  An override that is UNSET **or EMPTY** falls back to
    ``$HOME``: the shell spells that ``${VAR:-default}``, and a plain
    ``os.environ.get(name, default)`` would read ``""`` as the CURRENT
    DIRECTORY instead — a writer and its clearer would then look in two
    different trees for the SAME run, so a breadcrumb written under ``$HOME``
    is never removed and a run reads as never-ran (the #4373 class).
    ``Path`` drops a trailing ``/`` and a trailing ``/.`` before ``.parent``,
    matching the shell helper's explicit normalisation.

    ``Path.home()`` is touched only when there is no override at all, so it can
    still RAISE when ``$HOME`` is ``~``/``~/x`` — callers must treat the
    derivation as fallible.
    """
    override = os.environ.get("TORTOISE_IMPORT_RECEIPT_DIR")
    receipt_dir = (Path(override) if override
                   else Path.home() / ".tortoise" / "import-receipts")
    return receipt_dir.parent / leaf


#: Substrings that identify a hook body as Tortoise's. Deliberately specific
#: (a bare word ``tortoise`` would match a foreign hook that merely mentions
#: it) — the pre-#3795 un-markered hooks contain several of these.
#:
#: ``TORTOISE_API_KEY`` is the Pi seam's CODE-stable anchor.  The Pi seam's
#: prose matches (``tortoise session``, ``tortoise/claude-hooks``) are comment
#: text, and the artifact's own name ``tortoise-capture`` is a display string
#: (a log prefix) — a comment or log rewording would drop them, so a
#: pre-contract copy would fall to ``foreign-artifact``, whose repair
#: instruction is wrong.  ``TORTOISE_API_KEY`` is an IDENTIFIER the seam must
#: keep to talk to the API, so it cannot be reworded away (#4680).
#:
#: ⚠️ This sniff is FAIL-OPEN toward "ours": a foreign file that mentions any
#: of these is classified as a Tortoise artifact and the installer REPLACES it
#: (after keeping a ``.bak`` copy — never a silent overwrite, never a delete).
#: The opposite error is the loud one, so the asymmetry is the safer
#: direction; do not read the tuple as a security boundary.
_TORTOISE_SIGNATURES = (
    "tortoise context",
    "tortoise session",
    "tortoise index",
    "session probe --harness",
    "from tortoise",
    "import tortoise",
    "tortoise.__main__",
    "tortoise/claude-hooks",
    "TORTOISE_API_KEY",
)


def _atomic_copy(src: Path, dst: Path, mode: int) -> None:
    """Copy ``src`` over ``dst`` via a fresh same-dir temp + ``os.replace``.

    ``os.replace`` swaps the DIRECTORY ENTRY instead of writing through the
    existing inode, so a HARD-LINKED target does not truncate the other link.
    The temp comes from ``tempfile.mkstemp`` (``O_EXCL``, random name) so a
    pre-planted symlink at a deterministic temp path cannot hijack the write,
    and it is removed if anything after creation fails.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(dst.parent), prefix=dst.name + ".", suffix=".tortoise-tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out)
        os.chmod(tmp, mode)
        os.replace(tmp, dst)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _atomic_write_text(dst: Path, text: str) -> None:
    """Write ``text`` to ``dst`` via a fresh same-dir temp + ``os.replace``.

    Same hard-link / pre-planted-temp-symlink rationale as :func:`_atomic_copy`.
    The destination's existing mode is preserved (``mkstemp`` creates 0600), so
    a shared/packaged ``settings.json`` does not silently lose group/other
    read on upgrade.
    """
    try:
        mode = os.stat(dst).st_mode & 0o777
    except OSError:
        mode = 0o644
    fd, tmp_name = tempfile.mkstemp(
        dir=str(dst.parent), prefix=dst.name + ".", suffix=".tortoise-tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, dst)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _hook_src_dir_base(home: Path | None) -> Path:
    """The base the ``hook-src-dir`` record is written under.

    ``~/.tortoise`` by default.  The base is FAIL-SAFE: a call with no
    resolved ``home`` that runs under pytest never writes to the developer
    machine's real ``~/.tortoise`` — under pytest the base is derived
    DETERMINISTICALLY from the test id, so every process of one test shares
    one directory and none of them touches the real home.  Whether to RECORD
    at all is a SEPARATE decision that keys on whether the installed hook can
    resolve on its own (see ``_record_hook_src_dir`` and
    ``_hook_needs_src_dir_record``), not on this fail-safe: ``upgrade_install``
    at a repo-scoped ``--dir`` that is a checkout records nothing.

    This is not hypothetical: the first cut of #4314 used ``Path.home()``
    unconditionally and a single test run created
    ``~/.tortoise/hook-src-dir`` on the developer's machine.
    """
    if home is not None:
        return Path(home)
    test_id = os.environ.get("PYTEST_CURRENT_TEST")
    if test_id:
        # ``PYTEST_CURRENT_TEST`` is ``"<nodeid> (call|setup|teardown)"`` —
        # the phase suffix differs per phase, so hashing it verbatim gave one
        # test three directories and a record written in ``setup`` was
        # invisible in ``call``.  Strip the phase so ONE test == ONE dir.
        node_id = test_id.rsplit(" (", 1)[0]
        digest = hashlib.sha256(
            node_id.encode("utf-8", "surrogatepass")).hexdigest()[:16]
        return Path(tempfile.gettempdir()) / "tortoise-hook-src-tests" / digest
    return Path.home()


def _record_hook_src_dir(home: Path | None = None) -> None:
    """Record this package's module dir for the installed hooks to resolve.

    The module dir is the directory that CONTAINS the ``tortoise`` package —
    the repo root for a checkout, ``site-packages`` for a wheel install.  A
    hook accepts a candidate only when ``<candidate>/tortoise`` exists, so this
    record is what keeps an installed hook from falling through to its silent
    no-op (#4314).

    Best-effort and idempotent: a read-only ``~/.tortoise`` must never fail an
    install, and a re-run whose record is already correct does not rewrite
    (and does not churn the file's mtime).
    """
    base = _hook_src_dir_base(home)
    target = base / HOOK_SRC_DIR_RELPATH
    text = str(Path(__file__).resolve().parent.parent) + "\n"
    try:
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, text)
    except (OSError, ValueError):
        # Best-effort: this must NEVER fail an install. A non-UTF-8 record
        # raises ``UnicodeDecodeError`` (a ``ValueError``) from the read — a
        # class the old ``except OSError`` guard let escape, so
        # `tortoise install` / `hooks upgrade` tracebacked AFTER the hooks
        # were written (#3999, #4314).
        pass


def _record_hook_src_dir_best_effort(home: Path | None = None) -> None:
    """``_record_hook_src_dir`` that can NEVER fail the caller's install.

    ``_record_hook_src_dir`` already swallows the filesystem/decode failures
    it can name; this wrapper is the belt-and-suspenders boundary the install
    call sites need so no future raise-set member escapes a completed install
    (#3999, #4314). ``MemoryError`` is deliberately propagated: resource
    exhaustion is not a swallowed failure anywhere else in the install.
    """
    try:
        _record_hook_src_dir(home)
    except MemoryError:
        raise
    except Exception:
        pass


def _hook_needs_src_dir_record(layout: HarnessLayout, root: Path) -> bool:
    """True when the installed hook's OWN ``../..`` cannot resolve ``tortoise``.

    The ``hook-src-dir`` record exists for exactly one reason: the installed
    hook falls back to ``$(dirname "$0")/../..`` when ``$TORTOISE_SRC_DIR`` and
    the record are both absent, and from ``~/.codex/hooks`` / ``~/.cursor/
    hooks`` that fallback is ``$HOME`` — not a checkout.  So the record is
    NEEDED iff that fallback directory does not itself contain a ``tortoise/``
    package.  The directory is derived from the ACTUAL install layout
    (``layout.hooks_dir``), never a hardcoded assumption, so it is correct for
    a two-level ``.claude/hooks`` and a one-level ``hooks`` alike.

    This is a NEED-based rule, not a layout-based one, and that is deliberate:
    the write condition (this function) and the read condition (the hook's
    candidate loop) are the SAME condition.  It fixes Claude's project install
    and every ``--dir`` HOME install (``~/.codex``, ``~/.cursor``,
    ``~/.claude``) while still writing NOTHING for a repo-scoped ``--dir`` whose
    ``../..`` IS a checkout — the #4110 case, where a HOME side effect is both
    unnecessary and unwanted.
    """
    implied = (Path(root) / layout.hooks_dir).parent.parent
    return not (implied / "tortoise").is_dir()


def record_hook_src_dir_for_install(harness: str, *, root: Path,
                                    home: Path | None = None) -> bool:
    """Write the ``hook-src-dir`` record iff the installed hook needs it.

    The ONE shared helper both entry points call (``install_capture`` and
    ``upgrade_install``), so the two call sites can never drift: they write the
    record under the same need condition the installed hook reads it under.
    Best-effort and idempotent.  Returns whether a write was attempted.
    """
    layout = get_layout_optional(harness)
    # A harness with no shell-hook layout has no `../..` fallback to rescue, so
    # no record is needed — and this is the SAME condition the record's reader
    # applies (WRITE == READ).  Before the no-fake-layout ruling a
    # `get_layout`-based lookup raised `ValueError` for `pi`, which made
    # `tortoise install pi` crash outright (the ruling is recorded on #4680).
    if layout is None:
        return False
    if not _hook_needs_src_dir_record(layout, Path(root)):
        return False
    _record_hook_src_dir_best_effort(home)
    return True


def _looks_like_our_script(path: Path) -> bool:
    """Heuristic ownership sniff for the *doctor* install signal.

    A file named ``session-start.sh``/``session-end.sh`` could be another
    product's hook (the basenames are generic), so existence alone is not
    enough to report a Tortoise install and print a repair instruction. A
    canonical marker OR the string ``tortoise`` in the body identifies ours —
    the latter is what keeps the pre-#3795 **un-markered** population (the
    exact installs that need the warning) detectable.
    """
    if not path.is_file():
        return False
    if read_hook_version(path) is not None:
        return True
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(sig in text for sig in _TORTOISE_SIGNATURES)


def read_hook_version(path: str | os.PathLike[str]) -> int | None:
    """Return the canonical marker value in ``path``, or ``None``.

    ``None`` means "no readable canonical marker" — the file is missing, a
    directory, carries no column-0 ``# tortoise-hook-version: N`` (shell) or
    ``// tortoise-hook-version: N`` (TypeScript) line, or names a generation
    too large to convert to ``int`` (#3928).  A pre-#3795 install (no marker
    on ``session-start.sh``) is exactly this case, so ``None`` is a
    first-class *stale* signal, never an error.  The ``is_file`` gate also
    keeps a FIFO/socket at the path from blocking on ``read_text``.

    ``is_file`` and ``read_text`` share one guard because ``is_file`` raises as
    readily as ``read_text`` does: ``pathlib`` swallows only ENOENT, ENOTDIR,
    EBADF and ELOOP, so a non-searchable parent directory reaches the caller as
    ``PermissionError`` instead of ``None``.  ``None`` is the only failure
    signal this reader emits for a path the user can edit.
    """
    p = Path(path)
    try:
        if not p.is_file():
            return None
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    matches = _HOOK_VERSION_RE.findall(text)
    if not matches:
        return None
    try:
        # #3928: `None` is a first-class STALE signal, so this reader must
        # never raise.  CPython bounds int<->str conversion
        # (`sys.get_int_max_str_digits()`, 4300 by default), and a marker with
        # more digits than that makes `int()` raise `ValueError` — a
        # user-editable artifact at ``~/.pi/agent/extensions/`` therefore
        # reaches one of the new callers (#4680) unguarded.  An
        # unrepresentable generation is not a generation: report it as
        # unmarkered, which is the classification every caller already
        # handles.
        return int(matches[0])
    except ValueError:
        return None


def count_canonical_markers(path: str | os.PathLike[str]) -> int:
    """How many column-0 markers ``path`` declares (the contract wants one)."""
    p = Path(path)
    if not p.is_file():
        return 0
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return len(_HOOK_VERSION_RE.findall(text))


@dataclass(frozen=True)
class HookScriptSpec:
    """One installed script artifact and the settings entry it requires.

    ``timeout`` is ``None`` for a harness that resolves no per-hook timeout
    key (Codex reads no ``timeout``): an entry WITHOUT one is then current,
    not drift.  ``source_subdir``/``source_name`` name the shipped artifact
    when it differs from the installed ``name`` (Codex installs the shipped
    ``session-end.sh`` as ``tortoise-session-end.sh``); ``None`` keeps the
    historical ``claude-hooks/<name>`` default (resolved through the module
    global so a test can point it elsewhere).
    """

    name: str
    event: str
    timeout: int | None
    rel_command: str
    source_subdir: str | None = None
    source_name: str | None = None

    @property
    def source(self) -> Path:
        """The shipped (repo) copy of this script — the install source."""
        shipped = self.source_name or self.name
        if self.source_subdir is None:
            return _HOOKS_SOURCE_DIR / shipped
        return Path(__file__).resolve().parent / self.source_subdir / shipped


@dataclass(frozen=True)
class HarnessLayout:
    """Everything harness-specific about a capture-hook install.

    ``settings_file=None`` describes a scripts-only harness (no JSON settings
    merge) — the reason the settings logic is an adapter, not a Claude
    assumption.  ``hooks_dir``/``settings_file`` are install-root-relative.

    ``absolute_command`` registers the script's ABSOLUTE path (Codex runs the
    command from the session's cwd, so a relative path never resolves);
    ``matcher`` is False for a harness whose event entry is a bare group with
    no ``matcher`` key (Codex's nested shape).  Both default to the Claude
    shape so the existing layout is untouched.

    ``root_env`` declares the env var a harness resolves its install root
    through when the caller passes NO explicit directory (``root_home_default``
    is the ``$HOME``-relative fallback).  Codex reads its hooks ONLY from
    ``$CODEX_HOME``; Cursor has NO config-dir env var (verified — the string
    ``CURSOR_HOME`` appears nowhere in Cursor 3.20.21's JS bundle, asar or
    binary; it resolves ``pathService.userHome() / ".cursor"``), so the Cursor
    layout declares ``root_env=None`` with ``root_home_default=".cursor"``: a
    HOME-scoped root with no env override.  A layout with NEITHER (Claude)
    keeps the cwd default — Claude's install is project-scoped.  A cwd default
    for a HOME-scoped harness would inspect and "upgrade" a path it never
    reads while the real install stays broken (#3818, #3819).

    ``flat_entry`` selects the settings ENTRY shape.  Claude and Codex nest
    the handler under a matcher group (``{"hooks": [{"type": "command", …}]}``);
    Cursor's ``.cursor/hooks.json`` uses a FLAT script object
    (``{"command": …, "timeout": …}``) and its own validator REJECTS a nested
    entry (``Hook script command must be a string``), which invalidates the
    WHOLE config so Cursor loads no hooks at all — a silent no-capture (#3819).
    """

    harness: str
    hooks_dir: str
    scripts: tuple[HookScriptSpec, ...]
    settings_file: str | None = None
    absolute_command: bool = False
    matcher: bool = True
    root_env: str | None = None
    root_home_default: str | None = None
    flat_entry: bool = False
    #: Whether this harness's SHIPPED hooks write a local ``hook-run``
    #: observation (``~/.tortoise/hook-runs/<harness>.json``) when they run.
    #: Only the Claude ``session-start.sh`` seam does (#3797): the reader
    #: (``tortoise hooks status``) must not render an absence-of-observation
    #: for a harness whose hooks structurally never write one — that would be
    #: the same "assert what was not observed" defect this record exists to
    #: remove, wearing a new surface.  A harness adopts the record by adding
    #: the write to its own hook and flipping this flag, not by the reader
    #: guessing.
    writes_hook_run: bool = False

    def hooks_root(self, root: Path) -> Path:
        return root / self.hooks_dir

    def settings_path(self, root: Path) -> Path | None:
        return (root / self.settings_file) if self.settings_file else None


def _spec_command(layout: HarnessLayout, spec: HookScriptSpec,
                  root: str | os.PathLike[str] | None) -> str:
    """The command string an entry for ``spec`` must carry under ``layout``.

    Claude's is the root-relative ``rel_command``; a harness with
    ``absolute_command`` (Codex) needs the script's absolute path, quoted the
    ONE way the installer quotes it (``shlex.quote``) so the drift detector,
    ``upgrade`` and ``capture_install._install_home_scoped`` agree
    byte-for-byte.
    """
    if not layout.absolute_command or root is None:
        return spec.rel_command
    return shlex.quote(str(layout.hooks_root(Path(root)) / spec.name))


_CLAUDE_HOOKS_DIR = ".claude/hooks"


def _claude_layout() -> HarnessLayout:
    return HarnessLayout(
        harness="claude",
        hooks_dir=_CLAUDE_HOOKS_DIR,
        settings_file=".claude/settings.json",
        # #3797: the Claude session-start hook writes the local hook-run
        # observation, so `hooks status` MAY render it for claude — and only
        # for claude (see HarnessLayout.writes_hook_run).
        writes_hook_run=True,
        scripts=(
            HookScriptSpec(
                "session-start.sh", "SessionStart", 60,
                f"{_CLAUDE_HOOKS_DIR}/session-start.sh",
            ),
            HookScriptSpec(
                "session-end.sh", "SessionEnd", 60,
                f"{_CLAUDE_HOOKS_DIR}/session-end.sh",
            ),
            # #3963: the CHEAP per-turn capture. Capture used to happen only at
            # SessionEnd, which is cancelled at its ~1.5s default (#3754) and
            # does not fire at all on a kill — so an interrupted session filed
            # nothing. This hook spools the transcript locally (no network) at
            # every user prompt; the filing is deferred to the SessionStart
            # drain / the SessionEnd final flush.
            HookScriptSpec(
                "session-turn.sh", "UserPromptSubmit", 30,
                f"{_CLAUDE_HOOKS_DIR}/session-turn.sh",
            ),
        ),
    )


def _cursor_layout() -> HarnessLayout:
    """The Cursor capture seam as a layout (#3819) — NOT a fork of the logic.

    Cursor reads hook registrations from ``~/.cursor/hooks.json`` (verified
    against the installed bundle: ``CursorHooksService`` resolves
    ``pathService.userHome() / ".cursor" / "hooks.json"``, and there is NO
    config-dir env var — ``CURSOR_HOME`` appears nowhere in the app bundle);
    a project-local ``<repo>/.cursor/hooks.json`` is gated on workspace trust
    and fires nothing when untrusted, so the HOME-scoped registration is the
    reliable one.  Its entry is a FLAT ``{"command": …, "timeout": …}``
    object (``flat_entry``), the script is registered by ABSOLUTE path (the
    command runs from the hook cwd, not the install dir), and there is no
    matcher key.  ``sessionEnd`` is an IDE-only event: Cursor's docs state
    cloud agents have no editor-lifetime session boundary.
    """
    return HarnessLayout(
        harness="cursor",
        hooks_dir="hooks",
        settings_file="hooks.json",
        absolute_command=True,
        matcher=False,
        root_env=None,
        root_home_default=".cursor",
        flat_entry=True,
        scripts=(
            HookScriptSpec(
                "tortoise-session-end.sh", "sessionEnd", None,
                "hooks/tortoise-session-end.sh",
                source_subdir="cursor-hooks", source_name="session-end.sh",
            ),
        ),
    )


def _codex_layout() -> HarnessLayout:
    """The Codex capture seam as a layout (#3818) — NOT a fork of the logic.

    Unlike Claude, Codex resolves NO ``timeout`` key (its SessionEnd budget is
    a hard ~1 s the shipped hook detaches past), registers the script's
    ABSOLUTE path (Codex runs the command from the session cwd), and nests the
    handler under a bare ``{"hooks": [...]}`` group with no ``matcher``.
    ``root`` for every ``detect_install``/``upgrade_install`` call is the
    resolved ``$CODEX_HOME`` (``capture_install.codex_home``), where
    ``hooks/`` and ``hooks.json`` live.
    """
    return HarnessLayout(
        harness="codex",
        hooks_dir="hooks",
        settings_file="hooks.json",
        absolute_command=True,
        matcher=False,
        root_env="CODEX_HOME",
        root_home_default=".codex",
        scripts=(
            HookScriptSpec(
                "tortoise-session-end.sh", "SessionEnd", None,
                "hooks/tortoise-session-end.sh",
                source_subdir="codex-hooks", source_name="session-end.sh",
            ),
        ),
    )


#: Shipped layouts.  Every seam with an installer is registered here, so it
#: is drift-checked and upgradeable like Claude's (#3818, #3819).
HARNESS_LAYOUTS: dict[str, HarnessLayout] = {
    "claude": _claude_layout(),
    "codex": _codex_layout(),
    "cursor": _cursor_layout(),
}


def get_layout(harness: str) -> HarnessLayout:
    try:
        return HARNESS_LAYOUTS[harness]
    except KeyError:
        known = ", ".join(sorted(HARNESS_LAYOUTS))
        raise ValueError(
            f"unknown harness {harness!r} — known layouts: {known}"
        ) from None


def get_layout_optional(harness: str) -> HarnessLayout | None:
    """``get_layout`` for the harnesses that HAVE a shell-hook layout.

    A ``HarnessLayout`` describes where SHELL hooks live: a ``hooks_dir`` to
    derive the ``$(dirname "$0")/../..`` fallback from, a registration file,
    shipped scripts.  A harness that installs a non-shell integration has no
    such layout — ``pi`` ships a TypeScript extension (``tortoise/pi-hooks/
    tortoise-capture.ts``), not a ``session-end.sh``.

    Asking what such a harness's layout is must not be an ERROR, because the
    only question the layout answers here is "does the installed hook need a
    ``hook-src-dir`` record?" — and for a hook that is not a shell script
    there is no ``../..`` fallback, so the answer is simply NO.

    **The ruling is deliberate and it cuts both ways: a harness with no
    shell-hook layout must NOT be handed a fake one.** A layout-shaped
    stand-in would claim a ``hooks_dir`` and a registration file the seam does
    not have, and every layout consumer (``default_root``, ``detect_install``,
    ``upgrade_install``) would then act on paths that do not exist. Such a
    seam is described by :class:`ArtifactContract` instead. Recorded (with the
    ``OVERRIDES:`` marker) on #4680; the number that used to sit here, #4544,
    is a backup issue that does not carry it.
    """
    return HARNESS_LAYOUTS.get(harness)


#: The extension name Pi auto-discovers under ``~/.pi/agent/extensions/``.  It
#: lives HERE because the artifact and its version contract are one fact: the
#: detector must inspect exactly the file the installer writes, and
#: ``capture_install.PI_EXTENSION_NAME`` DERIVES from this declaration so the
#: two can never name different files (#4680).
PI_EXTENSION_NAME = "tortoise-capture.ts"


@dataclass(frozen=True)
class ArtifactContract:
    """A shipped capture seam that is NOT a shell hook.

    ``HarnessLayout`` describes a hooks *directory* plus a registration *file*
    — neither of which a non-shell seam has, and the no-fake-layout ruling
    documented on :func:`get_layout_optional` says inventing one for it is
    wrong.  What the VERSION CONTRACT needs from such a seam is the shipped
    artifact, the name it installs under, and the root it installs into, so
    that is all this carries; the marker's comment prefix is the READER's
    business (``read_hook_version`` accepts a shell ``#`` or a TS ``//``
    column-0 marker), never restated per artifact.

    ``root_relpath`` is here so the module that OWNS the contract also owns
    where the seam lives: without it every consumer re-hardcodes the harness
    (``session_verify`` and ``doctor`` each hardcoded ``pi_home``), so a second
    artifact seam would be probed at Pi's path — the same silent-omission
    class this registry exists to remove (#4680 review).

    This is deliberately NOT a ``HarnessLayout`` and must never grow into one:
    a layout-shaped stand-in would claim a ``hooks_dir`` and a registration
    file the seam does not have, which is the exact fake that ruling rejects.
    """

    harness: str
    source: Path
    install_name: str
    #: Directory the artifact installs into, RELATIVE to ``$HOME``.
    root_relpath: Path


def _pi_artifact_contract() -> ArtifactContract:
    """Pi's seam — the TypeScript extension installed at
    ``~/.pi/agent/extensions/tortoise-capture.ts`` (#3575)."""
    return ArtifactContract(
        harness="pi",
        source=Path(__file__).resolve().parent / "pi-hooks" / PI_EXTENSION_NAME,
        install_name=PI_EXTENSION_NAME,
        root_relpath=Path(".pi") / "agent" / "extensions",
    )


def artifact_root(harness: str, home: Path) -> Path | None:
    """The ``$HOME``-scoped install root for a REGISTERED artifact seam.

    ``None`` for a harness that declares no artifact contract, so a caller can
    tell "no such seam" from "a seam at this path" — the same distinction
    :func:`get_layout_optional` draws for the shell half.
    """
    contract = ARTIFACT_CONTRACTS.get(harness)
    if contract is None:
        return None
    return Path(home) / contract.root_relpath


#: Shipped non-shell seams, by harness.  The version contract is the
#: harness-agnostic half of this module: a shell-hook seam answers through
#: ``HARNESS_LAYOUTS``, a non-shell seam through this registry, and the
#: version ACCESSOR (``contract_version_for``) is the same call either way —
#: so ``pi`` is pinned by the same test table as its siblings without a fake
#: layout.  Detection has two siblings instead (``detect_install`` for shell
#: hooks, ``detect_artifact_install`` for artifacts): they cannot share one
#: body because the shell half needs a ``hooks_dir``, a registration file and
#: an exec bit, none of which apply here — so they are kept in lockstep
#: deliberately (same ``read_hook_version``, same ``Finding`` vocabulary),
#: not by delegation (#4680).
ARTIFACT_CONTRACTS: dict[str, ArtifactContract] = {
    "pi": _pi_artifact_contract(),
}


def default_root(layout: HarnessLayout, home: Path) -> Path:
    """The install root to use when the caller passes no explicit directory.

    Claude's install is project-scoped, so its default is the cwd (``.``).  A
    layout with a HOME-scoped root (``root_env`` and/or ``root_home_default``)
    resolves through the env var when set — Codex's documented
    ``${CODEX_HOME:-$HOME/.codex}`` — else ``$HOME/<root_home_default>``.
    Cursor declares only ``root_home_default=".cursor"`` (no env var; Cursor
    has none), so its root is ``~/.cursor``.  A cwd default for such a harness
    would inspect and "upgrade" the dead project-local path this seam
    replaces, and report success while nothing is captured (#3818, #3819).

    The returned root is ALWAYS absolute and ``~``-expanded.  Returning the
    env value verbatim registered a command the harness could never resolve: a
    literal ``CODEX_HOME=~/.codex`` (a tilde written into a config file is
    never shell-expanded) stayed a literal ``~`` directory, and a relative
    ``CODEX_HOME=relcodex`` registered ``relcodex/hooks/...`` — which the
    harness resolves against the SESSION cwd, so it silently captured nothing
    while ``install`` printed success.  A relative env value is anchored at the
    same HOME-scoped base the documented fallback uses, so the root is
    deterministic and ``install``/``status`` can never disagree (#3818).
    """
    if layout.root_env is None and not layout.root_home_default:
        return Path(".")
    home = Path(home).expanduser()
    env = (os.environ.get(layout.root_env, "").strip()
           if layout.root_env else "")
    root = (Path(env).expanduser() if env
            else home / (layout.root_home_default or ""))
    if not root.is_absolute():
        root = home / root
    if not root.is_absolute():
        # Truly unresolvable (not even the supplied home is absolute) —
        # refuse loudly rather than register a cwd-relative command that
        # the harness will resolve somewhere unknowable.
        raise ValueError(
            f"cannot resolve an absolute install root for the {layout.harness} "
            f"capture hook from home {home!r} — set an absolute HOME"
            + (f" or ${layout.root_env}" if layout.root_env else ""))
    return root


def contract_version(layout: HarnessLayout) -> int | None:
    """The single generation every script in ``layout`` declares.

    Returns ``None`` when any shipped script is unmarkered or the scripts
    disagree — the "one contract, one number" invariant.  A ``None`` here is a
    *repo* defect (pinned by tests), distinct from an installed copy being
    stale.
    """
    versions = {read_hook_version(spec.source) for spec in layout.scripts}
    if len(versions) != 1:
        return None
    version = versions.pop()
    return version


def contract_version_for(harness: str) -> int | None:
    """The generation the SHIPPED capture seam for ``harness`` declares.

    The harness-agnostic accessor: a caller never has to know whether the seam
    is a shell hook (answered by ``contract_version`` over a ``HarnessLayout``)
    or a non-shell artifact (answered from :data:`ARTIFACT_CONTRACTS`), so
    ``pi`` is pinned by the SAME test table as its three shell siblings instead
    of falling outside the machinery (#4680).  Its production consumer is
    ``tortoise doctor`` step 7, which grades both seam classes;
    ``tortoise hooks status`` also reads its version through it, but only for
    layout harnesses — the CLI still rejects ``pi`` before reaching this call,
    so Pi is unreachable there (#5351).

    ``None`` means "no contract is registered for this harness" or "the
    shipped seam declares no readable generation".  The former is the normal
    answer for a harness with no seam.  The latter is a REPO defect pinned by
    tests, never an install's problem — and for a layout it also covers the
    scripts DISAGREEING with each other (``contract_version`` returns ``None``
    then), a check the single-file artifact half has no analogue for.
    """
    layout = HARNESS_LAYOUTS.get(harness)
    if layout is not None:
        return contract_version(layout)
    artifact = ARTIFACT_CONTRACTS.get(harness)
    if artifact is None:
        return None
    return read_hook_version(artifact.source)


@dataclass(frozen=True)
class Finding:
    """One piece of drift (or a note) about an installed layout."""

    kind: str
    detail: str
    script: str | None = None
    event: str | None = None
    #: Blocking findings mean "this install is stale and can be repaired";
    #: non-blocking findings are informational (e.g. installed ahead of source).
    blocking: bool = True

    def line(self) -> str:
        icon = "❌" if self.blocking else "⚠️"
        return f"{icon} {self.kind}: {self.detail}"


#: Blocking finding kinds the automated repair CANNOT fix: the installer
#: refuses rather than clobber an unreadable, unsafe or foreign path, so the
#: only correct instruction is the manual one the finding already carries.
#: One declaration, consulted by both surfaces that recommend a repair
#: (``tortoise hooks status`` for shell seams, ``tortoise doctor`` for both
#: classes): a list copied into each caller drifts, and a drifted copy tells
#: the user to run a command that refuses (#4680 review).  Both seam classes
#: share the two structural names; the rest are per-class.
MANUAL_FIX_KINDS = frozenset({
    "unreadable-settings",
    "settings-unreadable-entry",
    "not-a-regular-file",
    "not-executable-symlink",
    "not-readable",
    "foreign-script",
    "foreign-artifact",
})


def is_manual_fix(kind: str) -> bool:
    """Whether a finding of ``kind`` needs a human fix before any repair run.

    True for :data:`MANUAL_FIX_KINDS` and for every ``symlinked*`` kind.  For
    the SHELL half that is exact: ``upgrade_install`` refuses on any symlink in
    a target path.

    For an artifact seam the same rule is deliberately CONSERVATIVE, and the
    direction matters more than the precision: ``install_capture`` refuses a
    symlinked install ROOT outright, and for a leaf link whether it refuses
    depends on where the link RESOLVES TO (a target outside ``$HOME`` is
    refused, an in-home one is replaced) — which no finding kind expresses.  A
    blocking ``symlinked-artifact`` covers a broken leaf link, which the
    installer does replace, so this predicate can ask for a needless manual
    step.  That is the cheap error: the other one recommends a command that
    fails.  Callers must therefore read ``kind`` only, never "the installer
    will work" (the doctor/status hint wording is scoped accordingly).
    """
    return kind in MANUAL_FIX_KINDS or kind.startswith("symlinked")


# ── settings helpers ────────────────────────────────────────────────────


#: Tokens that start a command WITHOUT being the executed program — the NEXT
#: non-option token is in executable position (``bash script.sh``,
#: ``timeout 5 script.sh``).  ``command`` is handled specially (``-v`` is a
#: query).  A backtick starts a command substitution, so the token after it is
#: in executable position.  Membership is tested on the token's BASENAME, so a
#: path-qualified launcher (``/bin/bash``, ``/usr/bin/env``) is recognised the
#: same as the bare name — see :func:`_as_launcher`.
_LAUNCHERS = frozenset({
    "bash", "sh", "zsh", "dash", "ksh", "env", "exec", "nohup", "sudo",
    "time", "timeout", "setsid", "nice", "ionice", "xargs", "firejail",
    "eval", "!", ".", "source", "if", "while", "until", "then", "do",
    "else", "elif", "`",
})

#: Shells whose ``-n``/``--noexec`` flag means "parse only, execute nothing".
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})

#: Shell flags whose NEXT token is a command string to be re-parsed (and thus
#: executed), not an option value.  Only valid for :data:`_SHELLS`: ``sudo -c``
#: is ``--close-from`` (an option VALUE) and must not be recursed into.
_SHELL_COMMAND_FLAGS = frozenset({"-c", "-lc", "-cl", "-ic"})

#: Shell separators that start a new command within a compound command line.
_SEPARATORS = frozenset({"&&", "||", ";", "|", "&"})

#: Redirection operators.  A bare operator redirects to the NEXT token (not a
#: command); one carrying a filename (``2>/dev/null``) is skipped by itself.
_REDIRECT_OPS = frozenset({
    ">", ">>", "<", "<<", "<>", "&>", "&>>", ">&>", ">|",
    "1>", "1>>", "2>", "2>>", "2>&1", "0<",
})
_REDIRECT_RE = re.compile(r"^[0-9]*(?:&?>>?|&?>&|<>)")

#: Grouping punctuation — a fresh command follows ``(``/``{`` and a command
#: ends at ``)``/``}``.
_GROUP = frozenset({"(", ")", "{", "}"})

#: Options that take a SEPARATE argument, keyed by the launcher's basename —
#: the following token is the option's VALUE, never the executed command.
#:
#: A single flat table cannot be right: ``-n`` is sudo's boolean
#: ``--non-interactive`` flag but nice/ionice/xargs' argument-taking adjustment,
#: and ``-s`` is timeout's ``--signal`` value but sudo's boolean ``--shell``
#: flag.  A flat set therefore makes ``sudo -u <hook>`` and ``sudo -n <hook>``
#: agree when bash disagrees about which token is the command, and the wrong
#: side is a FAIL-OPEN (the hook is never run, yet the matcher says current).
_OPTIONS_WITH_ARG: dict[str, frozenset[str]] = {
    # Shells: ``-c`` (command string) and ``-o``/``-O`` (option name) take a
    # separate word; ``-n`` is the no-execute flag handled above.
    "bash": frozenset({"-c", "-o", "+o", "-O", "+O",
                       "--rcfile", "--init-file"}),
    "sh": frozenset({"-c", "-o", "+o"}),
    "dash": frozenset({"-c", "-o", "+o"}),
    "zsh": frozenset({"-c", "-o", "+o"}),
    "ksh": frozenset({"-c", "-o", "+o"}),
    # env (GNU + BSD): ``-u``/``-C``/``-S``/``-P``/``--argv0`` take a word;
    # ``-i``/``-0``/``-v`` do not.
    "env": frozenset({"-a", "--argv0", "-u", "--unset", "-C", "--chdir",
                      "-S", "--split-string", "-P", "--path"}),
    # sudo: every option that names a user/group/dir/role/host/prompt takes a
    # word; ``-n``/``-s``/``-k``/``-i``/``-E``/``-S``/``-b``/``-A``/``-H`` are
    # booleans.
    "sudo": frozenset({
        "-a", "--auth-type", "-c", "--login-class", "-C", "--close-from",
        "-D", "--chdir", "-g", "--group", "-h", "--host", "-p",
        "--prompt", "-R", "--chroot", "-r", "--role", "-t", "--type",
        "-T", "--command-timeout", "-u", "--user", "-U", "--other-user",
    }),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    # GNU/BSD ``time``: ``-o file``/``--output`` and GNU ``-f``/``--format``
    # take the next word.  ``time`` is a LAUNCHER, so an unlisted argument-
    # taking option (``time -o <hook>``) parsed ``<hook>`` as the timed command
    # and reported a healthy install while BSD time consumed it as the output
    # file and ran nothing.
    "time": frozenset({"-o", "--output", "-f", "--format"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "ionice": frozenset({"-c", "--class", "-n", "--classdata", "-p", "--pid",
                         "-P", "--pgid", "-u", "--uid"}),
    "exec": frozenset({"-a"}),
    # GNU + BSD xargs: BSD adds ``-J``/``-R``/``-S`` (all take a word) and the
    # optional-argument ``-i``/``-l``/``-e``.  Omitting ``-J``/``-R``/``-S``
    # made ``xargs -J <hook>`` parse ``<hook>`` as the utility and report a
    # healthy install while BSD xargs consumed it as the replacement string.
    "xargs": frozenset({
        "-a", "--arg-file", "-d", "--delimiter", "-E", "--eof", "-i",
        "-I", "--replace", "-J", "-l", "-L", "--max-lines", "-n",
        "--max-args", "-e", "-P", "--max-procs", "--process-slot-var",
        "-R", "-s", "-S", "--max-chars",
    }),
    # Launchers whose option arity is DECLARED even when empty: an explicit
    # table keeps the coverage assertion below honest (every program launcher
    # declares its arity) instead of relying on a missing key defaulting to
    # "nothing takes an argument" — the exact shape that hid ``time -o`` and
    # ``xargs -J``.  ``setsid`` has no documented argument-taking option, but
    # ``-t``/``--wait-timeout`` are listed defensively: real util-linux setsid
    # rejects them (so bash runs nothing) and a future version that accepts
    # them takes a word — either way the hook is not the program.
    "setsid": frozenset({"-t", "--wait-timeout"}),
    "nohup": frozenset(),
    # firejail documents every value-taking option in the ``--opt=value`` form
    # (there is no separate-argument form to skip); its bare long options are
    # boolean.
    "firejail": frozenset(),
}

#: LONG options that are BOOLEAN — they consume no separate argument.  THE
#: ARITY DEFAULT IS "CONSUMES": an option not proved boolean here is assumed
#: to take the following token as its value.  Enumerating argument-taking
#: long options cannot be complete — GNU ``getopt_long`` accepts any
#: unambiguous PREFIX (``--sig`` == ``--signal``), a short option has many
#: long aliases (``env -a`` == ``--argv0``), and builds differ (BSD sudo's
#: ``--login-class``) — and every gap was a FAIL-OPEN (the hook is read as the
#: option's value, bash runs nothing, and the install still reports current).
#: Defaulting the unknown case to "consumes" makes the class safe by
#: construction: a long option can leave the hook in command position only
#: when it is one of these booleans.
_BOOLEAN_LONG: dict[str, frozenset[str]] = {
    "bash": frozenset({
        "--debugger", "--dump-po-strings", "--dump-strings", "--help",
        "--login", "--noediting", "--noprofile", "--norc", "--posix",
        "--protected", "--restricted", "--verbose", "--version",
        "--wordexp",
    }),
    "sh": frozenset({"--help", "--version"}),
    "dash": frozenset({"--help", "--version"}),
    "zsh": frozenset({"--help", "--version", "--no-rcs", "--login",
                      "--interactive"}),
    "ksh": frozenset({"--help", "--version"}),
    "env": frozenset({"--ignore-environment", "--null", "--debug",
                      "--list-signal-handling", "--help", "--version"}),
    "sudo": frozenset({
        "--edit", "--help", "--login", "--list", "--non-interactive",
        "--preserve-env", "--remove-timestamp", "--reset-timestamp",
        "--set-home", "--shell", "--stdin", "--validate", "--version",
    }),
    "timeout": frozenset({"--verbose", "--foreground", "--preserve-status",
                          "--help", "--version"}),
    "nice": frozenset({"--help", "--version"}),
    "ionice": frozenset({"--ignore", "--help", "--version"}),
    "time": frozenset({"--append", "--portability", "--quiet", "--verbose",
                       "--help", "--version"}),
    "xargs": frozenset({"--interactive", "--no-run-if-empty", "--verbose",
                        "--exit", "--open-tty", "--null", "--help",
                        "--version"}),
    "exec": frozenset({"-c", "-l"}),
    "setsid": frozenset({"--ctty", "--fork", "--wait", "--help",
                         "--version"}),
    "nohup": frozenset({"--help", "--version"}),
    "firejail": frozenset({
        "--allow-debuggers", "--allusers", "--apparmor", "--appimage",
        "--build", "--caps", "--debug", "--force", "--help", "--list",
        "--noprofile", "--private", "--quiet", "--top", "--tree",
        "--version", "--x11",
    }),
}

#: SHORT options that are PROVED BOOLEAN — they consume no separate argument
#: AND leave the token that follows them in executable position.  THE ARITY
#: DEFAULT FOR SHORT OPTIONS IS "CONSUMES": a short option (or one letter of a
#: combined cluster) that is not in this table is assumed to take the next
#: token as its VALUE.
#:
#: This is the same design as :data:`_BOOLEAN_LONG`, and for the same reason.
#: Enumerating the ARGUMENT-TAKING short options cannot be complete — ``ksh``'s
#: ``-R file`` / ``-T mask`` and bash-as-``sh``'s ``-O`` were missing from the
#: old per-launcher table — and every gap was a FAIL-OPEN: the hook path was
#: read as the option's value, bash ran nothing, and the install still reported
#: current (the entry kept a stamped ``timeout`` and no real registration was
#: appended).  A false NEGATIVE reports "missing", which only appends a
#: DUPLICATE registration, so the unknown case MUST consume.
#:
#: Only options PROVEN boolean belong here: verified against the tool's own
#: documentation AND, where the tool is installed on the development box,
#: against real bash ground truth (``<launcher> -X <hook>`` actually executes
#: the hook).  An option that is boolean but still does not run the following
#: token — ``bash -s`` (stdin), ``bash -t``/``-D`` (no execution), ``sudo -V``
#: (prints version and exits) — is deliberately ABSENT: treating it as boolean
#: would put the hook back into command position and re-open the class.
#: Under-listing is safe (a duplicate at worst); over-listing is not.
_BOOLEAN_SHORT: dict[str, frozenset[str]] = {
    "bash": frozenset({"-a", "-b", "-e", "-f", "-h", "-i", "-k", "-l",
                       "-m", "-p", "-r", "-u", "-v", "-x", "-B", "-C",
                       "-E", "-H", "-P", "-T"}),
    "sh": frozenset({"-a", "-b", "-C", "-e", "-f", "-i", "-k", "-l",
                     "-m", "-u", "-v", "-x"}),
    "dash": frozenset({"-a", "-b", "-C", "-e", "-f", "-i", "-l", "-m",
                       "-u", "-v", "-x"}),
    "zsh": frozenset({"-a", "-b", "-C", "-D", "-e", "-E", "-f", "-g",
                      "-G", "-h", "-H", "-i", "-I", "-k", "-l", "-L",
                      "-m", "-N", "-p", "-P", "-Q", "-r", "-R", "-T",
                      "-U", "-v", "-w", "-W", "-x", "-X", "-y", "-Y",
                      "-Z"}),
    "ksh": frozenset({"-a", "-b", "-e", "-f", "-h", "-i", "-k", "-l",
                      "-m", "-p", "-r", "-u", "-v", "-x", "-B", "-C",
                      "-E", "-G", "-H"}),
    "env": frozenset({"-i"}),
    "sudo": frozenset({"-A", "-b", "-B", "-E", "-H", "-n", "-P", "-S",
                       "-s"}),
    "timeout": frozenset({"-v"}),
    "time": frozenset({"-a", "-p"}),
    "nice": frozenset(),
    "ionice": frozenset({"-t"}),
    "exec": frozenset({"-c", "-l"}),
    "xargs": frozenset(),
    "setsid": frozenset({"-c", "-f", "-w"}),
    "nohup": frozenset(),
    "firejail": frozenset(),
}

#: Launchers that are shell syntax, not programs, and so cannot have options.
_OPTION_LESS_LAUNCHERS = frozenset({
    "eval", "!", ".", "source", "if", "while", "until", "then", "do",
    "else", "elif", "`",
})

#: An environment assignment prefix (``A=/x/y``, ``PATH=$PATH:/bin``).
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: A launcher operand that is a number/duration (``timeout 5``, ``nice -n 7``).
_LAUNCHER_OPERAND_RE = re.compile(r"^[0-9]+(\.[0-9]+)?[smhd]?$")


def _as_launcher(tok: str, quoted: bool = False,
                 root: str | os.PathLike[str] | None = None) -> str | None:
    """The launcher basename for ``tok``, or ``None`` when it is not one.

    ``/bin/sh``, ``/usr/bin/env`` and ``./bash`` exec exactly the program the
    bare name does, so they are launchers too: recognising only the bare name
    made ``/bin/bash <hook>`` look like a foreign entry, which reported a
    working install as ``missing-hook-entry`` and appended a DUPLICATE
    registration (the hook then ran twice per event).

    A QUOTED single word that names a real launcher PROGRAM (``'/bin/bash'``,
    ``'env'``) is also accepted — bash removes the quotes and runs the program
    — but a quoted shell KEYWORD (``'if'``) is a plain command name, not the
    reserved word, so it must not open an operand position.  The final check
    keeps the exact unquoted non-path launchers (``.``, ``!``, ```` ` ````)
    which ``Path`` mangles.

    A PATH-QUALIFIED launcher is accepted only when bash can actually find it.
    A nonexistent path (``./bash``, ``/opt/nope/bash``) runs nothing, so
    recognising it would leave the hook as a mere operand and FAIL OPEN —
    reporting the install current while bash ran nothing.
    """
    name = tok if tok in _LAUNCHERS else Path(tok).name
    if name in _OPTIONS_WITH_ARG:
        if "/" in tok:
            path = Path(tok)
            if not path.is_absolute() and root is not None:
                path = Path(root) / tok
            if not (path.is_file() and os.access(path, os.X_OK)):
                return None
        return name  # a real launcher program: path-qualified and quoted both run it
    if not quoted and tok in _LAUNCHERS:
        return tok   # exact unquoted shell keyword/builtin (never path-qualified)
    return None


def _balanced_parens_end(command: str, start: int, open_len: int,
                        depth: int) -> int | None:
    """End index (exclusive) of a balanced paren group, or ``None``.

    ``open_len`` is the width of the opener (3 for ``$((``, 2 for ``((``) and
    ``depth`` is how many ``(`` that opener contributes.  Used for the two
    ARITHMETIC forms, whose contents are operands, never commands.  Nesting is
    counted so a ``$(…)`` inside closes the right paren; an unterminated group
    is a bash syntax error that executes nothing, reported as ``None``.
    """
    i, n = start + open_len, len(command)
    while i < n:
        ch = command[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _arithmetic_expansion_end(command: str, start: int) -> int | None:
    """End index (exclusive) of the ``$((…))`` opening at ``start``, or None.

    ``$((`` opens an ARITHMETIC expansion — its contents are operands, not
    commands — so ``echo $((<hook>))`` (and ``x=$((<hook>))``) executes
    nothing, while ``echo $(<hook>)`` (single paren: command substitution)
    really does run the hook.
    """
    return _balanced_parens_end(command, start, 3, 2)


def _split_command(command: str) -> list[tuple[str, bool]] | None:
    """Split a shell command into ``(token, quoted)`` pairs.

    Unquoted ``(``/``)``/``;``/``&&``/``|``/backtick become their own tokens so
    executable position can be tracked; an unquoted newline is a command
    separator.  A token that had ANY quoted span is marked ``quoted`` — a
    quoted ``'('`` or ``';'`` is an ARGUMENT, never a boundary (treating it as
    one is how ``grep -e '(' .claude/hooks/session-end.sh`` was mistaken for an
    executed hook).  ``${VAR}`` is consumed atomically (its braces are not
    grouping punctuation) and a ``$((…))`` arithmetic expansion is dropped —
    its contents are operands, never executed commands (a ``$(…)`` command
    substitution, by contrast, keeps its ``(`` so its contents are).  Returns
    ``None`` for an unterminated quote or expansion — a command bash would
    reject executes nothing.
    """
    tokens: list[tuple[str, bool]] = []
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c == "\n":
            tokens.append((";", False))
            i += 1
            continue
        if c.isspace():
            i += 1
            continue
        if c in ";|&":
            if c == "&" and command.startswith("&>", i):
                j = i + 2
                if j < n and command[j] == ">":
                    j += 1  # ``&>>``
                tokens.append((command[i:j], False))
                i = j
                continue
            j = i
            while j < n and command[j] in ";|&":
                j += 1
            run = command[i:j]
            if run in _SEPARATORS:
                tokens.append((run, False))
            else:
                for ch in run:
                    tokens.append((ch, False))
            i = j
            continue
        if c == "(" and command.startswith("((", i):
            # ``((expr))`` is an arithmetic COMMAND: its contents are operands,
            # never commands (the same family as ``$((…))``).  A lone ``(`` —
            # or ``( (`` with a space — is a subshell and stays a boundary.
            j = _balanced_parens_end(command, i, 2, 2)
            if j is None:
                return None  # unterminated ``((`` — bash executes nothing
            i = j
            continue
        if c in "(){}\\`":
            tokens.append((c, False))
            i += 1
            continue
        buf: list[str] = []
        quoted = False
        while i < n and not command[i].isspace() and command[i] not in ";|&(){}\\`":
            ch = command[i]
            if ch == "$" and command.startswith("$((", i):
                # Arithmetic expansion: an OPERAND, not a command.  Flush the
                # word so far and DROP the expansion — its contents are never
                # executed (unlike ``$(…)``, whose contents are).
                if buf:
                    tokens.append(("".join(buf), quoted))
                    buf = []
                    quoted = False
                j = _arithmetic_expansion_end(command, i)
                if j is None:
                    return None  # unterminated ``$((`` — bash executes nothing
                i = j
                continue
            if ch == "'":
                quoted = True
                i += 1
                while i < n and command[i] != "'":
                    buf.append(command[i])
                    i += 1
                if i >= n:
                    return None  # unterminated quote
                i += 1
            elif ch == '"':
                quoted = True
                i += 1
                while i < n and command[i] != '"':
                    if command[i] == "\\" and i + 1 < n:
                        buf.append(command[i + 1])
                        i += 2
                    else:
                        buf.append(command[i])
                        i += 1
                if i >= n:
                    return None  # unterminated quote
                i += 1
            elif ch == "$" and i + 1 < n and command[i + 1] == "{":
                j = i + 2
                while j < n and command[j] != "}":
                    j += 1
                buf.append(command[i:j + 1] if j < n else command[i:])
                i = j + 1 if j < n else n
            elif ch == "\\" and i + 1 < n:
                buf.append(command[i + 1])
                i += 2
            else:
                buf.append(ch)
                i += 1
        if buf or quoted:
            tokens.append(("".join(buf), quoted))
    return tokens


def _token_is_our_script(tok: str, script_name: str, hooks_dir: str | None,
                         root: str | os.PathLike[str] | None = None) -> bool:
    """True when a single EXECUTED token names our script under ``hooks_dir``.

    Basename alone is not enough; the rejected forms are documented in
    :func:`_invokes_script`.
    """
    if Path(tok).name != script_name:
        return False
    if hooks_dir is None:
        return True
    if root is not None and not tok.startswith("$"):
        # The token must resolve to THIS project's installed hook.  Absolute
        # AND relative paths are compared against ``root/hooks_dir/script`` —
        # suffix-matching a relative path let ``vendor/.claude/hooks/
        # session-end.sh`` (a DIFFERENT file) count as ours, which stamped a
        # timeout on the foreign entry and never registered the real one (a
        # silent no-capture).
        expected = Path(root) / hooks_dir / script_name
        candidate = Path(tok) if os.path.isabs(tok) else Path(root) / tok
        try:
            return candidate.resolve() == expected.resolve()
        except (OSError, ValueError, RuntimeError):
            # NUL byte (ValueError) / symlink loop (RuntimeError) / IO error
            return False
    # No root (or a ``$VAR`` token whose value is unknowable): fall back to the
    # directory-suffix check.  An absolute path is never accepted this way.
    if os.path.isabs(tok):
        return False
    dir_parts = Path(tok).parent.parts
    want = Path(hooks_dir).parts
    return len(dir_parts) >= len(want) and dir_parts[-len(want):] == want


def _invokes_script(command: str, script_name: str,
                    hooks_dir: str | None,
                    root: str | os.PathLike[str] | None = None,
                    *, matched: list[str] | None = None) -> bool:
    """True when a command line EXECUTES our script under ``hooks_dir``.

    ``matched``, when given, receives the raw token the classifier resolved as
    our script (the first one, matching this predicate's first-hit semantics).
    It is informational only and does not change the verdict.

    THE RULE: walk the token stream and ask, at each position, whether the token
    that BASH would resolve as an executed command names our hook.  A token is in
    executable position at the start of a command (the first token, after a
    separator/group-open, or inside a command substitution ``$(…)``/backtick);
    after a recognised launcher it remains executable, because a launcher
    (``env``/``sudo``/``timeout``/``bash`` …) runs its first non-option operand.
    Skipped before executable position: a launcher's own options and the separate
    VALUE of any option the launcher's per-launcher table declares argument-taking
    (``sudo -u root cmd``, ``timeout -s KILL 60 cmd``), environment-assignment
    prefixes, redirection operators and their targets, ``timeout``'s numeric
    duration operand, and ``$((…))`` arithmetic contents (operands, never
    commands).  A shell's ``-c`` argument is RECURSED into as a nested command
    line.  A launcher is matched by BASENAME, so ``/bin/sh -c '…'`` behaves as
    ``sh -c '…'``.

    A BARE filename is rejected because it resolves to the project root, and an
    absolute path is accepted only when it resolves to THIS project's hook.  Each
    false positive would suppress our own registration while reporting a healthy
    install; each false negative appends a DUPLICATE registration.
    """
    tokens = _split_command(command)
    if tokens is None or not tokens:
        return False  # unterminated quote — bash executes nothing
    if not tokens[0][1] and tokens[0][0] in _SEPARATORS:
        return False  # leading separator: a syntax error, nothing executes
    heredoc: str | None = None
    expect_cmd = True
    skip_next = False  # separate argument of an argument-taking launcher option
    skip_operand = False  # redirection target (never a command)
    recurse_next = False  # previous token was ``-c`` (a shell command string)
    launcher_word: str | None = None  # the launcher that governs operands
    for tok, quoted in tokens:
        if heredoc is not None:
            if tok == heredoc:
                heredoc = None
            continue
        if not quoted and (tok in _SEPARATORS or tok in _GROUP
                           or tok == "`"):
            expect_cmd = True
            skip_next = False
            skip_operand = False
            recurse_next = False
            launcher_word = None
            continue
        if tok.startswith("<<") and not tok.startswith("<<<"):
            heredoc = tok[2:].lstrip("-")
            continue
        if not quoted and (_REDIRECT_OPS.issuperset({tok})
                           or _REDIRECT_RE.match(tok)):
            # A redirection is not a command; a BARE operator redirects to the
            # next token — which is a filename, never an executed command.
            skip_operand = tok in _REDIRECT_OPS
            skip_next = False
            continue
        if skip_operand:
            skip_operand = False
            continue
        if not expect_cmd:
            continue
        if recurse_next:
            recurse_next = False
            # Trailing tokens after a ``-c`` command string are the child
            # shell's positional args ($0, $1, …), not commands.
            expect_cmd = False
            if _invokes_script(tok, script_name, hooks_dir, root,
                               matched=matched):
                return True
            continue
        if skip_next:
            skip_next = False
            # A launcher option's separate argument is a VALUE, never the
            # executed command (``sudo -u <user>``, ``timeout -s <signal>``,
            # ``bash -o <option-name>``).  Which options take one is decided by
            # the launcher's own table, so nothing here may be executed — the
            # old blanket "if it is our hook, it ran" safety net was the
            # fail-open: it fired for ``sudo -u <hook>`` where bash consumes the
            # path as a username.
            continue
        if not quoted and tok == "command":
            launcher_word = "command"
            continue
        if not quoted and tok in ("-v", "-V") and launcher_word == "command":
            return False  # ``command -v <path>`` is a query, not an execution
        launcher = _as_launcher(tok, quoted, root)
        if launcher is not None:
            launcher_word = launcher
            continue
        if (not quoted and launcher_word is not None
                and tok.startswith("-")):
            # A launcher governs this operand position, so an option-looking
            # token here is an OPTION of that launcher.  With NO launcher
            # (``launcher_word is None``) an option-looking token is the
            # command NAME itself — bash reports ``-x: command not found`` —
            # so nothing after it executes: this block is skipped and the
            # token is judged as a command below (which ends the run).
            if launcher_word in _SHELLS and (
                    tok == "--noexec"
                    or (not tok.startswith("--") and "n" in tok[1:])):
                return False  # ``bash -n`` / ``sh -n``: syntax check only
            options = _OPTIONS_WITH_ARG.get(launcher_word, frozenset())
            long_booleans = _BOOLEAN_LONG.get(launcher_word, frozenset())
            short_booleans = _BOOLEAN_SHORT.get(launcher_word, frozenset())
            if launcher_word in _SHELLS and tok in _SHELL_COMMAND_FLAGS:
                # A shell's ``-c`` argument is a command STRING to re-parse.
                recurse_next = True
            elif tok == "--":
                pass  # end of options: the NEXT token is the command
            elif "=" in tok:
                pass  # attached value (``--signal=KILL``) consumes no token
            elif tok.startswith("--"):
                if any(o.startswith("--") and o.startswith(tok)
                       for o in options):
                    # GNU getopt_long accepts an unambiguous PREFIX of a long
                    # option (``timeout --sig`` == ``--signal``).
                    skip_next = True
                elif any(b.startswith("--") and b.startswith(tok)
                         for b in long_booleans):
                    pass  # a boolean long option (or its abbreviation)
                else:
                    # UNKNOWN long option: assume it consumes the next token.
                    # Enumerating argument-taking options cannot be complete —
                    # GNU prefixes, short/long aliases, and platform builds
                    # (``sudo --login-class``) — and every gap is a FAIL-OPEN
                    # (the hook is read as the option's value and bash runs
                    # nothing), so the default is the safe direction.
                    skip_next = True
            elif (len(tok) > 1
                    and all(("-" + ch) in short_booleans
                            for ch in tok[1:])):
                pass  # every clustered short option is PROVED boolean
            else:
                # THE SHORT-OPTION ARITY DEFAULT IS "CONSUMES".  A short
                # option (or one letter of a combined cluster) that the
                # launcher's ``_BOOLEAN_SHORT`` table does not PROVE boolean is
                # assumed to take the next token as its VALUE.  Enumerating
                # the argument-taking short options cannot be complete
                # (``ksh -R``/``-T``, bash-as-``sh``'s ``-O``) and every gap
                # was a FAIL-OPEN: the hook was read as the option's value,
                # bash ran nothing, and the install still reported current.
                # A false "missing" only appends a DUPLICATE registration, so
                # the unknown case MUST consume.
                skip_next = True
            continue
        if _ASSIGNMENT_RE.match(tok):
            continue
        if (launcher_word == "timeout" and not quoted
                and _LAUNCHER_OPERAND_RE.match(tok)):
            continue
        expect_cmd = False
        if _token_is_our_script(tok, script_name, hooks_dir, root):
            if matched is not None:
                matched.append(tok)
            return True
    return False


def _entry_command_dicts(entry: object, script_name: str | None = None,
                         hooks_dir: str | None = None,
                         root: str | os.PathLike[str] | None = None,
                         *, flat: bool = False,
                         ) -> list[dict]:
    """EVERY child command dict in ``entry`` that invokes our script.

    A wrapper entry may hold the same command more than once; checking only
    the first would leave the second untimed (Claude Code cancels it at its
    1.5 s default) while ``detect_install`` reported the install current.

    ``flat`` selects the Cursor shape: the ENTRY ITSELF is the command dict
    (``{"command": …}``), with no ``hooks`` array.  That is not a cosmetic
    difference — Cursor's validator rejects a nested entry and invalidates the
    whole ``hooks.json``, so reading a flat entry as "not ours" would append a
    duplicate that breaks the file, and writing a nested one would silently
    disable every Cursor hook (#3819).
    """
    if not isinstance(entry, dict):
        return []

    def _ok(item: object) -> bool:
        # A handler must be a properly-typed child command: Claude Code's
        # schema requires ``{"type": "command", "command": …}`` inside an
        # entry's ``hooks`` array.  Anything else (a missing ``type``, a
        # non-string command) is silently ignored by the harness, so treating
        # it as ours would report a broken install as current.
        if not isinstance(item, dict):
            return False
        if item.get("type") != "command":
            return False
        command = item.get("command")
        if not isinstance(command, str):
            return False
        return script_name is None or _invokes_script(
            command, script_name, hooks_dir, root)

    if flat:
        # Cursor's schema accepts ``type`` omitted (defaults to "command") or
        # "command"; anything else (a prompt hook) is not a command hook.
        if entry.get("type") not in (None, "command"):
            return []
        command = entry.get("command")
        if not isinstance(command, str):
            return []
        if script_name is not None and not _invokes_script(
                command, script_name, hooks_dir, root):
            return []
        return [entry]

    inner = entry.get("hooks")
    if isinstance(inner, list):
        return [item for item in inner if _ok(item)]
    return []


def _entry_command_dict(entry: object, script_name: str | None = None,
                        hooks_dir: str | None = None,
                        root: str | os.PathLike[str] | None = None,
                        *, flat: bool = False) -> dict | None:
    """The dict carrying ``command`` for a nested (matcher) entry.

    Claude Code requires ``{"type": "command", "command": …}`` inside an
    entry's ``hooks`` array (a ``hookMatcher``); a flat ``{type, command}`` at
    the EVENT level — and a handler missing its ``type`` — is silently ignored
    by the harness, so neither counts as our registration (a ``None`` return
    makes ``_merge_settings`` append a valid matcher).  A wrapper entry may
    hold MULTIPLE commands — when ``script_name`` is given, the FIRST child
    command invoking our script is returned, never blindly ``hooks[0]`` (which
    would miss a hook that is not first and leave the load-bearing ``timeout``
    unset).  Returns ``None`` for anything malformed rather than raising —
    malformed entries are treated as foreign and left untouched.

    ``flat`` is Cursor's entry shape — the entry IS the command dict (#3819).
    """
    if not isinstance(entry, dict):
        return None
    if flat:
        found = _entry_command_dicts(entry, script_name, hooks_dir, root,
                                     flat=True)
        return found[0] if found else None

    inner = entry.get("hooks")
    if isinstance(inner, list):
        for item in inner:
            if isinstance(item, dict) and item.get("type") == "command":
                command = item.get("command")
                if isinstance(command, str) and (
                        script_name is None or _invokes_script(
                            command, script_name, hooks_dir, root)):
                    return item
    # A FLAT ``{"type", "command"}`` at the EVENT level (no ``hooks`` array) is
    # silently ignored by Claude Code — it is NOT a registration, so it must
    # not be treated as ours (that would leave the install non-functional
    # while reporting it current).  It is left untouched as a foreign entry.
    return None


def _entry_is_ours(entry: object, script_name: str,
                   hooks_dir: str | None = None,
                   root: str | os.PathLike[str] | None = None,
                   *, flat: bool = False) -> bool:
    """True when an entry invokes our ``script_name`` under ``hooks_dir``."""
    return _entry_command_dict(entry, script_name, hooks_dir, root,
                               flat=flat) is not None


def registered_commands(root: str | os.PathLike[str],
                        harness: str = "claude",
                        ) -> list[tuple[str, str]]:
    """Every ``(event, command)`` the harness will RUN for this installed seam.

    Reads the harness's OWN registration file through the SAME loader helpers
    ``detect_install`` classifies with (``_entry_command_dicts`` /
    ``_entry_is_ours``), so the command ``tortoise session verify`` fires is
    byte-identical to the one ``detect_install`` judged current — no second
    parser that could disagree about which entry is ours.

    Returns ``[]`` for a scripts-only layout (no settings file), an absent
    file, an unreadable/malformed file, or when no entry invokes our script.
    Read-only; never raises for a user file (a malformed file is simply no
    registration to report).
    """
    layout = get_layout(harness)
    settings_path = layout.settings_path(Path(root))
    if settings_path is None:
        return []
    data, _refusal = _load_settings(settings_path)
    if not isinstance(data, dict):
        return []
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return []
    commands: list[tuple[str, str]] = []
    for spec in layout.scripts:
        entries = hooks.get(spec.event)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            for inner in _entry_command_dicts(
                    entry, spec.name, layout.hooks_dir, Path(root),
                    flat=layout.flat_entry):
                command = inner.get("command")
                if isinstance(command, str) and command.strip():
                    commands.append((spec.event, command))
    return commands


def _load_settings(path: Path | None) -> tuple[dict | None, str | None]:
    """Load a settings file.  Returns ``(data, error)`` — never raises."""
    if path is None:
        return {}, None
    if not path.exists():
        return {}, None
    if not path.is_file():
        # A directory/FIFO/socket at the settings path must never be READ
        # (a FIFO read blocks forever) — report it as drift to refuse on.
        return None, f"{path} is not a regular file — refusing to touch it"
    try:
        raw = path.read_bytes()
    except OSError as e:
        # An unreadable file — a diagnostic must never traceback; report it as
        # drift to refuse on.
        return None, (f"{path} could not be read "
                      f"({e.__class__.__name__}) — refusing to touch it")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Decoding with ``errors="replace"`` would silently rewrite unrelated
        # values (and foreign hook commands) on the merge write-back — refuse
        # instead of losing the user's bytes.
        return None, f"{path} is not valid UTF-8 — refusing to touch it"
    try:
        data = json.loads(text)
    except ValueError:
        return None, f"{path} is not valid JSON — refusing to touch it"
    if not isinstance(data, dict):
        return None, f"{path} top level is not a JSON object — refusing to touch it"
    hooks = data.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        return None, f'{path} "hooks" is not a JSON object — refusing to touch it'
    return data, None


def _read_bytes(path: Path) -> bytes | None:
    """``path.read_bytes()`` or ``None`` on ``OSError`` (never raises)."""
    try:
        return path.read_bytes()
    except OSError:
        return None


def _has_owner_exec_bit(st_mode: int) -> bool:
    """True when the OWNER's exec bit is set — the bit that decides whether
    the harness, running as the install's owner, can execute the hook.

    The ONE exec-bit predicate both surfaces share (#4000 R33).  The
    drift/upgrade half here (``detect_install`` + ``upgrade_install``) and the
    install half (``capture_install``) each grew their own ``st_mode & 0o111``
    test, and "any exec bit" is a silent false success for ``0o601``/``0o410``:
    a non-owner exec bit is the ONLY exec bit there, so the owner still cannot
    run the hook while ``detect_install`` returned ``[]`` (status reported it
    current), ``upgrade_install`` planned no write (its mode-only repair was
    skipped as unnecessary), and only ``capture_install`` — fixed first —
    repaired it.  One definition, both callers, so the two halves cannot
    diverge again.

    ``stat.S_IXUSR`` and ``0o100`` are the same bit; the named constant is
    used so the *ownership decision* has exactly one spelling in this
    codebase.  The repair/rewrite target modes below still OR in ``0o111``
    (``_target_mode`` and the differing-file replacement paths): those add
    exec bits rather than test them, so they always set the owner bit and
    cannot reintroduce the any-exec-bit fail-open this predicate exists to
    close.
    """
    return bool(st_mode & stat.S_IXUSR)


def _target_mode(installed: Path) -> int:
    """Mode for a rewritten hook: 0755 for a fresh copy, else the existing
    mode plus exec bits (a script installed 0700 stays 0700, not 0755)."""
    if installed.exists():
        try:
            return (installed.stat().st_mode & 0o777) | 0o111
        except OSError:
            return 0o755
    return 0o755


def _is_timeout_budget(value: object) -> bool:
    """True when ``value`` is a usable per-hook timeout budget.

    A ``float`` counts: ``120.0`` is a real budget.  The ONE predicate both
    surfaces share — ``_settings_findings``/``_merge_settings`` here and
    ``capture_install.merge_capture_hooks`` — because testing ``int`` alone
    made ``tortoise hooks status`` report BLOCKING drift on a float timeout
    the installer deliberately preserved, and ``tortoise hooks upgrade`` then
    LOWERED it to 60: the opposite of the module's "never lowered" promise.
    ``bool`` is excluded explicitly (``True`` is an ``int``).

    The value must also be FINITE: ``json.loads`` happily accepts a bare
    ``NaN``/``Infinity`` literal, and a NaN budget is not a budget — nothing
    can be compared against it (``nan < 60`` is False), so the old
    ``isinstance``-only test let a ``"timeout": NaN`` through as "already
    budgeted" and left the hook to Claude Code's 1.5 s default (#3808 R15).
    Passing the widened float gate without this check is what made the
    non-finite form survive every surface that shares this predicate.

    An ``int`` larger than a double must ALSO come back ``False``, not raise:
    ``json.loads`` parses an integer literal of any magnitude as an
    arbitrary-precision ``int``, and ``math.isfinite`` coerces its argument to
    a C double, so a >308-digit ``"timeout"`` raises ``OverflowError`` — a
    CLI traceback out of install/status/upgrade on a perfectly valid
    ``settings.json``.  A budget no double can hold is not a budget (Claude
    Code's own ``JSON.parse`` reads it as ``Infinity``), and the shared
    predicate is the single gate all three surfaces read (#3808 R16).
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _expected_entry(layout: HarnessLayout, spec: HookScriptSpec,
                    root: str | os.PathLike[str] | None) -> str:
    """Human hint for the entry ``_merge_settings`` would write."""
    command = _spec_command(layout, spec, root)
    if spec.timeout is None:
        return command
    return f"{command} with timeout {spec.timeout}"


#: Cursor's known hook steps (`r6o` in Cursor 3.20.21's bundle).  Cursor's
#: validator iterates EVERY key under `hooks` and rejects the WHOLE file on an
#: unknown one, so a flat document is invalid if it names a step Cursor does
#: not know.
_FLAT_KNOWN_EVENTS = frozenset({
    "beforeShellExecution", "beforeMCPExecution", "afterShellExecution",
    "afterMCPExecution", "beforeReadFile", "afterFileEdit",
    "beforeTabFileRead", "afterTabFileEdit", "stop", "beforeSubmitPrompt",
    "afterAgentResponse", "afterAgentThought", "sessionStart", "sessionEnd",
    "preCompact", "subagentStart", "subagentStop", "preToolUse",
    "postToolUse", "postToolUseFailure", "workspaceOpen",
})


def _is_positive_int_value(value: object) -> bool:
    """True for a JSON positive integer the way JS ``Number.isInteger`` sees
    it — an ``int >= 1``, or an integral ``float`` (``1.0`` is a valid Cursor
    ``version``; ``json.loads`` yields a Python float for ``1.0``).
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 1
    if isinstance(value, float):
        return value.is_integer() and value >= 1
    return False


#: Regex syntax Python accepts but JS ``new RegExp`` REJECTS — a definite
#: JS-invalid matcher (refuse).  Inline flags and scoped flag removal (``(?i``,
#: ``(?-i:``), atomic groups (``(?>``), possessive quantifiers (``*+ ++ ?+
#: {m,n}+``), and Python's own group syntax compile in Python and throw in JS,
#: so accepting them would let a Cursor-invalid file through.  The possessive
#: alternative requires an UNESCAPED quantifier char (``\++`` is valid in both
#: engines).
_PYTHON_ONLY_REGEX = re.compile(
    r"\(\?(?:P<|P=|#|\(|-|[imsxaLu])"    # Python group syntax / inline flags
    r"|\(\?>"                            # atomic group
    r"|(?<!\\)(?:[*+?]|\{\d+(?:,\d*)?\})\+"  # possessive quantifier
)

#: Regex syntax JS ``new RegExp`` accepts but Python ``re`` REJECTS.  Used only
#: when Python cannot compile a matcher: if one of these is present the matcher
#: is ASSUMED JS-valid (do not block a valid Cursor config); otherwise a compile
#: failure is a genuine refusal (both engines reject it).
_JS_ONLY_REGEX = re.compile(r"\(\?<[A-Za-z_]|\\[pP]\{|\\k<|\\u\{")


def _flat_entry_is_harness_valid(entry: object) -> bool:
    """True when ``entry`` is a script object Cursor's ``hooks.json``
    validator (``Uvd``/``Fvd``/``Bvd``/``Ovd`` in 3.20.21) ACCEPTS.

    Cursor rejects the WHOLE document on a single bad entry, so a flat merge
    that appends beside a nested/malformed entry is a silent no-capture —
    which is why this predicate gates the merge.  It mirrors Cursor's own
    field checks (command/prompt shape, ``matcher`` regex, numeric positive
    ``timeout``, integer/null ``loop_limit``, boolean ``failClosed``, prompt
    ``model``) rather than a subset: a partial validator CERTIFIES a foreign
    entry Cursor will reject (#3819).

    Presence, not ``None``, is the test.  Cursor checks ``e.field !== void 0``
    then ``typeof``, so an explicit JSON ``null`` is present-and-wrong and is
    REJECTED (``typeof null`` is ``"object"``) — except ``loop_limit``, which
    Cursor explicitly allows to be ``null``.
    """
    if not isinstance(entry, dict):
        return False
    has_type = "type" in entry
    htype = entry.get("type")
    if htype == "prompt":
        prompt = entry.get("prompt")
        if not (isinstance(prompt, str) and prompt.strip()):
            return False
        if "model" in entry and not (
                isinstance(entry["model"], str) and entry["model"].strip()):
            return False
    elif htype == "command" or not has_type:
        if not isinstance(entry.get("command"), str):
            return False
    else:
        return False
    if "matcher" in entry:
        matcher = entry["matcher"]
        if not isinstance(matcher, str):
            return False
        if matcher not in ("", "*"):
            # The two regex engines cannot be mirrored exactly, so judge only
            # the SAFE direction: a Python-only construct is a definite
            # JS-invalid matcher (REFUSE).  If Python cannot compile it, accept
            # ONLY when the matcher carries a known JS-only construct
            # (``(?<name>…)``, ``\p{L}``); a compile failure with no such
            # marker means both engines reject it (REFUSE).  Never raise — a
            # deeply nested pattern raises RecursionError, not re.error.
            if _PYTHON_ONLY_REGEX.search(matcher):
                return False
            compiled = False
            with contextlib.suppress(Exception):
                re.compile(matcher)
                compiled = True
            if not compiled and not _JS_ONLY_REGEX.search(matcher):
                return False
    if "timeout" in entry:
        timeout = entry["timeout"]
        if (isinstance(timeout, bool)
                or not isinstance(timeout, (int, float)) or timeout <= 0):
            return False
    if "loop_limit" in entry:
        loop_limit = entry["loop_limit"]
        if loop_limit is not None and (
                isinstance(loop_limit, bool)
                or not _is_positive_int_value(loop_limit)):
            return False
    fail_closed_present = "failClosed" in entry
    return not (fail_closed_present
                and not isinstance(entry["failClosed"], bool))


def _flat_version_refusal(data: dict) -> str | None:
    """A populated refusal when ``data`` lacks a valid positive-integer
    ``version`` (JS ``Number.isInteger``), else ``None``."""
    version = data.get("version")
    if not _is_positive_int_value(version):
        return (f'needs a positive integer "version" (found {version!r}) — '
                "Cursor rejects the WHOLE file without it")
    return None


def _flat_structure_refusal(data: dict) -> str | None:
    """A populated refusal when ``data`` has a structural problem Cursor's
    validator rejects (an unknown event, a non-list event, an unparseable
    entry) — ANYWHERE under ``hooks`` — else ``None``.

    Deliberately INDEPENDENT of ``version``: a document can have both a bad
    version and a structural defect, and the structural one is a manual fix
    (``upgrade`` must refuse, not "repair" the version and leave the file
    rejected) — so the two are reported as distinct findings.
    """
    hooks = data.get("hooks")
    if hooks is None:
        return None
    if not isinstance(hooks, dict):
        return '"hooks" is not a JSON object'
    for event, entries in hooks.items():
        if event not in _FLAT_KNOWN_EVENTS:
            return (f'unknown hook type {event!r} — Cursor rejects the WHOLE '
                    "file on an unknown step")
        if not isinstance(entries, list):
            return f'"{event}" entries are not a list'
        for entry in entries:
            if not _flat_entry_is_harness_valid(entry):
                return (f'a "{event}" entry is not a script object Cursor '
                        f"can parse ({entry!r})")
    return None


def _settings_findings(layout: HarnessLayout, data: dict,
                       root: str | os.PathLike[str] | None = None,
                       ) -> list[Finding]:
    hooks = data.get("hooks") or {}
    findings: list[Finding] = []
    if layout.flat_entry:
        # Cursor's validator rejects the WHOLE document — after which NO hook
        # fires — on a missing/non-positive `version`, an unknown event key, a
        # non-list event value, or any entry it cannot parse (verified live
        # against Cursor 3.20.21, #3819).  The STRUCTURAL defect is reported
        # separately and is a manual fix (`upgrade` refuses on it); only a bad
        # version alone is repairable by `upgrade`.
        structure = _flat_structure_refusal(data)
        if structure:
            findings.append(Finding(
                "settings-unreadable-entry",
                f"{layout.harness} hooks.json {structure}"))
        version = _flat_version_refusal(data)
        if version:
            findings.append(Finding(
                "settings-invalid-version",
                f"{layout.harness} hooks.json {version}"))
    for spec in layout.scripts:
        entries = hooks.get(spec.event)
        expected_entry = _expected_entry(layout, spec, root)
        if entries is None:
            findings.append(Finding(
                "missing-hook-entry",
                f"no {spec.event} hook entry in settings (expected "
                f"{expected_entry})",
                script=spec.name, event=spec.event,
            ))
            continue
        if not isinstance(entries, list):
            findings.append(Finding(
                "unreadable-settings",
                f'{spec.event} is not a list — refusing to touch it',
                script=spec.name, event=spec.event,
            ))
            continue
        if layout.flat_entry:
            # (The whole-document refusal above already covers every event, so
            # this is only reached when the document is valid.)
            pass
        ours = [e for e in entries
                if _entry_is_ours(e, spec.name, layout.hooks_dir, root,
                                  flat=layout.flat_entry)]
        if not ours:
            findings.append(Finding(
                "missing-hook-entry",
                f"{spec.event} has no entry invoking {spec.name} (expected "
                f"{expected_entry})",
                script=spec.name, event=spec.event,
            ))
            continue
        expected_command = _spec_command(layout, spec, root)
        for entry in ours:
            for inner in _entry_command_dicts(entry, spec.name,
                                              layout.hooks_dir, root,
                                              flat=layout.flat_entry):
                # The harness runs the command from its own cwd, so a relative
                # or stale-path registration is a silent no-capture — flag it
                # as drift `upgrade` repairs (Claude's
                # ``$CLAUDE_PROJECT_DIR`` form is deliberately left alone, so
                # this is gated on the absolute-command layout).
                if (layout.absolute_command
                        and inner.get("command") != expected_command):
                    findings.append(Finding(
                        "settings-stale-command",
                        f"{spec.event} entry for {spec.name} runs "
                        f"{inner.get('command')!r}; expected the absolute "
                        f"path {expected_command!r} ({layout.harness} "
                        "resolves the command from its own cwd)",
                        script=spec.name, event=spec.event,
                    ))
                if spec.timeout is None:
                    continue  # this harness resolves no timeout key
                timeout = inner.get("timeout")
                if not _is_timeout_budget(timeout):
                    findings.append(Finding(
                        "settings-no-timeout",
                        f"{spec.event} entry for {spec.name} has no numeric "
                        f'"timeout" (#3754: Claude Code cancels the hook at '
                        f"its 1.5s default) — expected {spec.timeout}",
                        script=spec.name, event=spec.event,
                    ))
                elif timeout < spec.timeout:
                    findings.append(Finding(
                        "settings-low-timeout",
                        f"{spec.event} timeout={timeout}s is below the "
                        f"required {spec.timeout}s for {spec.name}",
                        script=spec.name, event=spec.event,
                    ))
    return findings


def _symlink_in_path(root: Path, target: Path) -> Path | None:
    """First symlink component strictly BELOW ``root`` on the way to ``target``.

    ``root`` itself may resolve through a symlinked alias (macOS ``/tmp`` →
    ``/private/tmp``), so it is not examined — only the install's own
    components. ANY symlink below root is refused, including one whose target
    is inside the project: ``os.replace`` would silently convert a
    symlink-based install into a stale regular-file copy.
    """
    try:
        rel_parts = target.relative_to(root).parts
    except ValueError:
        rel_parts = target.parts
    cur = root
    for part in rel_parts:
        cur = cur / part
        if cur.is_symlink():
            return cur
    return None


def _probe_writable(path: Path) -> None:
    """Raise ``OSError`` unless ``path``'s parent dir can be written to.

    Run for EVERY target before the first real write so an unwritable
    directory aborts the upgrade atomically instead of leaving the script half
    repaired and the settings (load-bearing #3801) half untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=str(path.parent), prefix=".tortoise-probe")
    os.close(fd)
    os.unlink(name)


# ── detection ───────────────────────────────────────────────────────────


def detect_install(root: str | os.PathLike[str], harness: str = "claude",
                   ) -> list[Finding]:
    """Report drift between the shipped hooks and the install at ``root``.

    Read-only.  Returns ``[]`` for a current install; findings are ordered
    scripts-then-settings.  Never raises for a malformed user file — a
    malformed file is itself a finding.
    """
    layout = get_layout(harness)
    root = Path(root)
    findings: list[Finding] = []
    hooks_root = layout.hooks_root(root)

    # A symlinked hooks DIRECTORY (`.claude/hooks` → the repo's claude-hooks/)
    # is the natural dev install and tracks the source; upgrade refuses it, so
    # warn here or status/doctor would say "current" while upgrade refuses.
    dir_link = _symlink_in_path(root, hooks_root)
    if dir_link is not None:
        findings.append(Finding(
            "symlinked-install",
            f"{dir_link} is a symlink — upgrade leaves symlinked installs "
            "untouched (unlink it, or point --dir at the real install)",
            blocking=False,
        ))

    for spec in layout.scripts:
        expected = read_hook_version(spec.source)
        installed = hooks_root / spec.name
        if installed.is_symlink() and not installed.exists():
            # A BROKEN symlink: `.exists()` follows the link, so it would fall
            # through to `missing-script` and the status hint would recommend
            # an `upgrade` that refuses (symlinks are never written through).
            findings.append(Finding(
                "symlinked-script",
                f"{installed} is a broken symlink — remove or re-point it; "
                "upgrade leaves symlinks untouched",
                script=spec.name,
            ))
            continue
        if not installed.exists():
            findings.append(Finding(
                "missing-script",
                f"{installed} is not installed",
                script=spec.name,
            ))
            continue
        if not installed.is_file():
            # A directory / FIFO / socket at the script path is drift, and
            # reading it (FIFO) could block — report without reading.
            findings.append(Finding(
                "not-a-regular-file",
                f"{installed} exists and is not a regular file",
                script=spec.name,
            ))
            continue
        if expected is None:
            # Repo defect (pinned by tests) — not this install's problem.
            continue
        if installed.is_symlink():
            # A symlink install tracks the source automatically and upgrade
            # deliberately leaves it alone — note it so `status`/`doctor` do
            # not imply an upgrade is possible.
            findings.append(Finding(
                "symlinked-script",
                f"{installed} is a symlink — upgrade leaves symlinked installs "
                "untouched (it already tracks the source)",
                script=spec.name, blocking=False,
            ))
        if not os.access(installed, os.R_OK):
            findings.append(Finding(
                "not-readable",
                f"{installed} is not readable — chmod it so the hook can run",
                script=spec.name,
            ))
        if not _has_owner_exec_bit(installed.stat().st_mode):
            # The exec-bit check applies to symlinks too: `stat` follows the
            # link, and an unexecutable target cannot be run by the harness.
            # Upgrade cannot repair a symlink (it refuses them), so this kind
            # is manual — name the resolved target.
            if installed.is_symlink():
                findings.append(Finding(
                    "not-executable-symlink",
                    f"{installed} resolves to {installed.resolve()}, which is "
                    "not executable — chmod +x that file",
                    script=spec.name,
                ))
            else:
                findings.append(Finding(
                    "not-executable",
                    f"{installed} is not executable — the harness cannot run "
                    "it",
                    script=spec.name,
                ))
        found = read_hook_version(installed)
        if found is None:
            if not os.access(installed, os.R_OK):
                pass  # already reported as `not-readable` above
            elif _looks_like_our_script(installed):
                findings.append(Finding(
                    "unversioned-script",
                    f"{installed} carries no {HOOK_VERSION_TOKEN} marker "
                    f"(expected {expected}) — installed before #3795",
                    script=spec.name,
                ))
            else:
                # A foreign hook at OUR path — upgrade refuses it, so flag it
                # as manual rather than as a pre-#3795 Tortoise copy that
                # `upgrade` can repair.
                findings.append(Finding(
                    "foreign-script",
                    f"{installed} is not a Tortoise hook — move it aside to "
                    "let Tortoise install its own",
                    script=spec.name,
                ))
        elif found < expected:
            findings.append(Finding(
                "stale-script",
                f"{installed} is {HOOK_VERSION_TOKEN} {found}, "
                f"current is {expected}",
                script=spec.name,
            ))
        elif found > expected:
            findings.append(Finding(
                "ahead-script",
                f"{installed} is {HOOK_VERSION_TOKEN} {found}, newer than "
                f"this CLI's {expected} — not downgraded",
                script=spec.name, blocking=False,
            ))
        elif installed.read_bytes() != spec.source.read_bytes():
            findings.append(Finding(
                "modified-script",
                f"{installed} marker matches ({found}) but its bytes differ "
                "from the shipped hook (local edit) — upgrade backs it up "
                "to .bak before restoring",
                script=spec.name,
            ))

    settings_path = layout.settings_path(root)
    if settings_path is None:
        return findings  # scripts-only harness — no settings half
    # A symlinked settings.json is invisible to the per-file reads (they follow
    # the link) but upgrade refuses it — note it so status/doctor do not
    # report clean while upgrade refuses.
    settings_link = _symlink_in_path(root, settings_path)
    if settings_link is not None:
        findings.append(Finding(
            "symlinked-settings",
            f"{settings_link} is a symlink — upgrade leaves symlinked "
            "installs untouched",
            blocking=False,
        ))
    data, error = _load_settings(settings_path)
    if error is not None:
        findings.append(Finding("unreadable-settings", error))
        return findings
    findings.extend(_settings_findings(layout, data or {}, root))
    return findings


def detect_artifact_install(root: str | os.PathLike[str],
                            harness: str) -> list[Finding]:
    """Report drift for a NON-shell capture seam — the artifact half.

    The sibling of :func:`detect_install` for a harness in
    :data:`ARTIFACT_CONTRACTS`: ONE shipped artifact, no ``hooks_dir`` and no
    registration file, so the checks that need those (symlink-in-path,
    settings, exec bit) do not apply.  The checks it DOES share are the same
    ones: missing / not-a-regular-file / not-readable / foreign / unversioned /
    stale / ahead / modified — the same classification ``detect_install``
    applies, so ``session verify`` reports a stale Pi seam exactly as it
    reports a stale Claude one (#4680) instead of the old presence-only check
    that could not tell them apart.

    Kind names are suffixed ``-artifact`` rather than ``-script`` ONLY for the
    seam-specific kinds (``missing``/``unversioned``/``stale``/``ahead``/
    ``modified``/``symlinked``/``foreign``); the structural kinds
    ``not-a-regular-file`` and ``not-readable`` are shared verbatim with
    ``detect_install`` because the two detectors genuinely report the same
    structural defect there, and a kind-keyed caller (the manual-fix predicate,
    :func:`is_manual_fix`) then covers both classes with one entry.  The
    ``symlinked-install`` note (a symlinked install ROOT) is shared verbatim
    too, for the same reason.

    KNOWN LIMITATION (tracked by #3713, not this detector's fix): only the one
    artifact file is inspected, so an ACTIVE legacy ``tortoise-capture/``
    directory beside it — the double-producer collision ``_install_pi``
    disables on install — is not reported here.  A shell seam has no such
    directory-shaped duplicate, so ``detect_install`` has no peer check.

    Read-only, like its shell sibling.  Returns ``[]`` for a harness with no
    registered artifact (the normal answer for a harness this module does not
    cover), and ``[]`` for a present artifact when the SHIPPED copy is
    defective — a repo defect pinned by tests, never the install's problem.
    """
    contract = ARTIFACT_CONTRACTS.get(harness)
    if contract is None:
        return []
    root_path = Path(root)
    installed = root_path / contract.install_name
    findings: list[Finding] = []
    # The install HOME is the ancestor `root_relpath` names, so the symlink
    # check covers EVERY component the installer walks — `.pi`, `agent`,
    # `extensions` and the artifact leaf itself.  `install_capture` refuses a
    # symlinked install root and writes nothing through it (verified for both
    # an in-home and an out-of-home target), so without this note `doctor`
    # would recommend `tortoise install <harness>` for a command that refuses.
    # This is the artifact peer of `detect_install`'s `symlinked-install`.
    # A caller handing us a root unrelated to the contract (a test's tmp_path)
    # falls back to that root, where the check still covers the artifact.
    home = root_path
    _parts = contract.root_relpath.parts
    if tuple(root_path.parts[-len(_parts):]) == _parts:
        home = root_path.parents[len(_parts) - 1]
    root_link = _symlink_in_path(home, root_path)
    if root_link is not None:
        findings.append(Finding(
            "symlinked-install",
            f"{root_link} is a symlink — the installer refuses a symlinked "
            "install root and writes nothing through it; replace it with a "
            f"real directory, then re-run `tortoise install {harness}`",
            blocking=False,
        ))
    if installed.is_symlink() and not installed.exists():
        # A BROKEN symlink: `.exists()` follows the link, so it would fall
        # through to `missing-artifact` and imply the seam is absent when it
        # is really a broken pointer.  (`_symlink_in_path` above did not fire:
        # it excludes the caller's root and this leaf IS `root`'s child, so the
        # leaf is still checked here.)
        findings.append(Finding(
            "symlinked-artifact",
            f"{installed} is a broken symlink — remove or re-point it",
            script=contract.install_name,
        ))
        return findings
    if not installed.exists():
        findings.append(Finding(
            "missing-artifact",
            f"{installed} is not installed",
            script=contract.install_name,
        ))
        return findings
    if not installed.is_file():
        # A directory / FIFO / socket at the artifact path is drift, and
        # reading it (FIFO) could block — report without reading.
        findings.append(Finding(
            "not-a-regular-file",
            f"{installed} exists and is not a regular file",
            script=contract.install_name,
        ))
        return findings
    expected = read_hook_version(contract.source)
    if expected is None:
        # Repo defect (pinned by tests) — not this install's problem.
        return findings
    if installed.is_symlink():
        # A symlink install tracks whatever it points at, so it may STILL be
        # stale (a checkout from an older generation is stale) — unlike
        # `upgrade_install`, which refuses symlinks, the detector reports on
        # the RESOLVED bytes, so this is a note, never a reason to skip the
        # version checks below.
        findings.append(Finding(
            "symlinked-artifact",
            f"{installed} is a symlink — it tracks its target, so a stale "
            "target is reported below; re-point or copy it to upgrade",
            script=contract.install_name, blocking=False,
        ))
    if not os.access(installed, os.R_OK):
        findings.append(Finding(
            "not-readable",
            f"{installed} is not readable — chmod it so the seam can load",
            script=contract.install_name,
        ))
    found = read_hook_version(installed)
    if found is None:
        if not os.access(installed, os.R_OK):
            pass  # already reported as `not-readable` above
        elif _looks_like_our_script(installed):
            # The pre-contract population (#4680): a present, functioning
            # Tortoise seam that carries no marker, so nothing could tell it
            # was weeks old.  Ownership is sniffed exactly as the shell half
            # does it (`_looks_like_our_script`).  Getting this wrong is
            # ASYMMETRIC, and the sniff is fail-open toward "ours": a foreign
            # file that merely mentions Tortoise would be classified as a
            # pre-contract copy and REPLACED by the installer — preserving a
            # `.bak` of the foreign bytes, never deleting them.  The opposite
            # error (reporting our own unmarkered seam as foreign) is the
            # loud one: its instruction tells the user to move a working
            # Tortoise seam aside.
            findings.append(Finding(
                "unversioned-artifact",
                f"{installed} carries no {HOOK_VERSION_TOKEN} marker "
                f"(expected {expected}) — it was installed before the version "
                f"contract, so it captures with older logic; reinstall with "
                f"`tortoise install {harness}`",
                script=contract.install_name,
            ))
        else:
            findings.append(Finding(
                "foreign-artifact",
                f"{installed} is not a Tortoise seam — move it aside, then "
                f"re-run `tortoise install {harness}` to install ours",
                script=contract.install_name,
            ))
    elif found < expected:
        findings.append(Finding(
            "stale-artifact",
            f"{installed} is {HOOK_VERSION_TOKEN} {found}, "
            f"current is {expected} — it captures with older logic; "
            f"reinstall with `tortoise install {harness}`",
            script=contract.install_name,
        ))
    elif found > expected:
        findings.append(Finding(
            "ahead-artifact",
            f"{installed} is {HOOK_VERSION_TOKEN} {found}, newer than "
            f"this CLI's {expected} — it captures with NEWER logic; "
            f"`tortoise install {harness}` would replace it with {expected}",
            script=contract.install_name, blocking=False,
        ))
    elif installed.read_bytes() != contract.source.read_bytes():
        findings.append(Finding(
            "modified-artifact",
            f"{installed} marker matches ({found}) but its bytes differ "
            "from the shipped seam (local edit)",
            script=contract.install_name,
        ))
    return findings


def is_installed(root: str | os.PathLike[str], harness: str = "claude") -> bool:
    """True when ``root`` looks like a capture-hook install of ``harness``."""
    layout = get_layout(harness)
    hooks_root = layout.hooks_root(Path(root))
    # Our own script file is the install signal. A bare `.claude/hooks/` dir is
    # NOT (another product's hooks live there too), and neither is a file that
    # merely SHARES our generic basename — ownership is sniffed from the marker
    # or the ``tortoise`` body so `tortoise doctor` neither false-fails on a
    # foreign install nor nudges the user to install hooks they never asked
    # for. The un-markered pre-#3795 population still matches on body content.
    if any(_looks_like_our_script(hooks_root / spec.name)
           for spec in layout.scripts):
        return True
    settings_path = layout.settings_path(Path(root))
    if settings_path is not None and settings_path.exists():
        data, error = _load_settings(settings_path)
        if error is None and data:
            hooks = data.get("hooks") or {}
            for spec in layout.scripts:
                entries = hooks.get(spec.event)
                if isinstance(entries, list) and any(
                    _entry_is_ours(e, spec.name, layout.hooks_dir, root,
                                   flat=layout.flat_entry)
                    for e in entries
                ):
                    return True
    return False


# ── upgrade ─────────────────────────────────────────────────────────────


@dataclass
class UpgradeResult:
    """Outcome of :func:`upgrade_install`."""

    harness: str
    refused: str | None = None
    actions: list[str] = field(default_factory=list)
    findings_before: list[Finding] = field(default_factory=list)
    findings_after: list[Finding] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.actions) or bool(self.refused)

    @property
    def ok(self) -> bool:
        return self.refused is None


def _merge_settings(layout: HarnessLayout, data: dict, actions: list[str],
                    root: str | os.PathLike[str] | None = None,
                    ) -> bool:
    """Ensure each script's settings entry exists with the required timeout.

    Merges in place: only the specific entry's ``timeout`` is written (and,
    for an ``absolute_command`` harness, a relative/stale command is repaired
    to the absolute path); every other key, event, and entry is preserved
    byte-for-byte after the JSON round-trip.  Returns True when the document
    changed.
    """
    changed = False
    if layout.flat_entry:
        # Cursor's `hooks.json` REQUIRES a positive-integer `version`; without
        # it Cursor rejects the WHOLE file and loads no hooks.  Set it when
        # absent/invalid, never overwrite a user's valid value.  (A valid
        # `1.0` counts — JS `Number.isInteger(1.0)` is true.)
        version = data.get("version")
        if not _is_positive_int_value(version):
            data["version"] = 1
            actions.append(
                f"settings: set \"version\" to 1 in {layout.harness} "
                f"hooks.json (was {version!r}; Cursor rejects the whole file "
                "without a positive integer version)")
            changed = True
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    for spec in layout.scripts:
        entries = hooks.get(spec.event)
        if not isinstance(entries, list):
            entries = []
            hooks[spec.event] = entries
        ours = [e for e in entries
                if _entry_is_ours(e, spec.name, layout.hooks_dir, root,
                                  flat=layout.flat_entry)]
        if not ours:
            command = _spec_command(layout, spec, root)
            if layout.flat_entry:
                # Cursor's shape: the entry IS the command dict.  Nesting it
                # would fail Cursor's validator and disable EVERY hook in the
                # file — the silent no-capture this seam exists to prevent.
                fresh = {"command": command}
                if spec.timeout is not None:
                    fresh["timeout"] = spec.timeout
                entries.append(fresh)
            else:
                inner = {"type": "command", "command": command}
                if spec.timeout is not None:
                    inner["timeout"] = spec.timeout
                fresh: dict = {"hooks": [inner]}
                if layout.matcher:
                    fresh["matcher"] = ""
                entries.append(fresh)
            if spec.timeout is None:
                actions.append(
                    f"settings: added {spec.event} entry for {spec.name}")
            else:
                actions.append(
                    f"settings: added {spec.event} entry for {spec.name} "
                    f"(timeout {spec.timeout})")
            changed = True
            continue
        expected_command = _spec_command(layout, spec, root)
        for entry in ours:
            for inner in _entry_command_dicts(entry, spec.name,
                                              layout.hooks_dir, root,
                                              flat=layout.flat_entry):
                if (layout.absolute_command
                        and inner.get("command") != expected_command):
                    actions.append(
                        f"settings: repaired {spec.event} command for "
                        f"{spec.name} ({inner.get('command')!r} -> "
                        f"{expected_command!r})")
                    inner["command"] = expected_command
                    changed = True
                if spec.timeout is None:
                    continue
                timeout = inner.get("timeout")
                if (not _is_timeout_budget(timeout)
                        or timeout < spec.timeout):
                    inner["timeout"] = spec.timeout
                    actions.append(
                        f"settings: set {spec.event} timeout={spec.timeout} "
                        f"on {spec.name} (was {timeout!r})"
                    )
                    changed = True
    return changed


def upgrade_install(root: str | os.PathLike[str], harness: str = "claude",
                    dry_run: bool = False,
                    home: Path | None = None) -> UpgradeResult:
    """Install or upgrade the capture hooks at ``root``, in place.

    * scripts — re-copied from the shipped repo copy when missing or stale;
      a copy whose bytes differ from the shipped hook is preserved as
      ``<name>.bak`` (or ``<name>.bak.N`` when one already exists) before the
      shipped copy is restored;
    * settings — the ``timeout`` half is **merged** into the existing entry
      (or a missing entry is added), preserving all other content.

    Refuses (writes nothing) when a target path contains a symlink below
    ``root`` (a live symlink install must not be turned into a stale copy), at
    a non-regular file, at a file that does not look like a Tortoise hook
    (never silently deactivate another product), or when the settings file is
    unparseable.  Each write target's directory is probed for writability
    before the first write, so the common unwritable-directory case aborts
    with nothing changed; a target that becomes unreplaceable only at write
    time (e.g. an immutable file) is reported as a partial upgrade rather than
    a traceback.  A byte-identical copy with a lost exec bit is repaired with
    ``chmod``.  A hard-linked target is not refused — the atomic replace
    breaks the link instead of writing through it.
    """
    layout = get_layout(harness)
    root = Path(root)
    result = UpgradeResult(harness=harness)
    result.findings_before = detect_install(root, harness)

    # ── symlink guard: check every target before any write ──────────────
    targets = [layout.hooks_root(root) / s.name for s in layout.scripts]
    settings_path = layout.settings_path(root)
    if settings_path is not None:
        targets.append(settings_path)
    for target in targets:
        link = _symlink_in_path(root, target)
        if link is not None:
            result.refused = (
                f"Refusing: {link} is a symlink — a symlinked harness install "
                "is left untouched (unlink it first, or point --dir at the "
                "real install)"
            )
            return result

    data, error = _load_settings(settings_path)
    if error is not None:
        result.refused = f"Refusing: {error}"
        return result
    # A malformed event value (e.g. SessionEnd holding an object instead of a
    # list), or — for a flat (Cursor) layout — an entry the harness's own
    # validator would reject, must be refused, never silently replaced by an
    # empty list or merged alongside.  Appending a valid flat entry next to a
    # nested one still leaves a file Cursor rejects, so the repair path must
    # refuse it (the manual fix is to remove the bad entry) (#3819).
    if any(f.kind in ("unreadable-settings", "settings-unreadable-entry")
           for f in result.findings_before):
        result.refused = (
            "Refusing: the settings file has a malformed hooks entry — fix "
            "it manually, then re-run"
        )
        return result
    # A directory (or other non-regular file) at a script path must be refused
    # BEFORE the loop writes anything, so a crash cannot leave the install
    # half-repaired (the settings-path sibling is refused above).  An
    # UNREADABLE regular file is refused here too: `read_bytes()` in the guards
    # below would otherwise raise `PermissionError` out of a public entry point
    # whose contract is "refuse, never raise".
    for spec in layout.scripts:
        installed = layout.hooks_root(root) / spec.name
        if installed.exists() and not installed.is_file():
            result.refused = (
                f"Refusing: {installed} exists and is not a regular file — "
                "remove it manually, then re-run"
            )
            return result
        if installed.exists() and not os.access(installed, os.R_OK):
            result.refused = (
                f"Refusing: {installed} is not readable — chmod it and re-run"
            )
            return result

    # Ownership guard: an existing file at OUR path that does not look like a
    # Tortoise hook is another product's — never silently deactivate it. (The
    # un-markered pre-#3795 hooks DO look like ours via their body signatures.)
    for spec in layout.scripts:
        installed = layout.hooks_root(root) / spec.name
        if installed.exists() and not _looks_like_our_script(installed):
            theirs = _read_bytes(installed)
            if theirs is None:
                result.refused = (
                    f"Refusing: {installed} is not readable — chmod it and "
                    "re-run"
                )
                return result
            if theirs != spec.source.read_bytes():
                result.refused = (
                    f"Refusing: {installed} exists but does not look like a "
                    "Tortoise hook — move it aside and re-run, so a foreign "
                    "hook is not silently replaced"
                )
                return result

    before = {s.name: read_hook_version(layout.hooks_root(root) / s.name)
              for s in layout.scripts}

    # ── plan the script writes (no writes yet) ──────────────────────────
    plan: list[tuple[HookScriptSpec, Path, int, bool]] = []
    for spec in layout.scripts:
        expected = read_hook_version(spec.source)
        if expected is None:
            result.refused = (
                f"Refusing: shipped {spec.source} carries no "
                f"{HOOK_VERSION_TOKEN} marker — the repo copy is broken"
            )
            return result
        installed = layout.hooks_root(root) / spec.name
        found = before[spec.name]
        content_differs = (
            installed.exists()
            and _read_bytes(installed) != spec.source.read_bytes()
        )
        missing_exec = (
            installed.exists()
            and not _has_owner_exec_bit(installed.stat().st_mode)
        )
        if found is not None and found > expected:
            result.actions.append(
                f"skipped {spec.name} ({HOOK_VERSION_TOKEN} {found} is ahead "
                f"of this CLI's {expected})"
            )
            if missing_exec:
                # Never downgrade the version — but a lost exec bit still
                # means the hook files nothing, and `status` advertises an
                # `upgrade` that must converge.  Mode-only repair.
                plan.append((spec, installed, expected, False))
            continue
        if (installed.exists() and found == expected and not content_differs
                and not missing_exec):
            continue  # already current — byte-identical, no write, no action
        plan.append((spec, installed, expected, content_differs))

    # ── settings plan (the #3801 half; mutates `data` + actions) ────────
    assert data is not None
    if settings_path is not None:
        settings_changed = _merge_settings(layout, data, result.actions, root)
    else:
        settings_changed = False  # scripts-only harness

    if dry_run:
        for _spec, installed, expected, content_differs in plan:
            if content_differs or not installed.exists():
                result.actions.append(
                    f"would write {installed} ({HOOK_VERSION_TOKEN} {expected})"
                )
            else:
                result.actions.append(f"would make {installed} executable")
        if settings_changed and settings_path is not None:
            result.actions.append(f"would merge settings into {settings_path}")
        result.actions = [f"[dry-run] {a}" for a in result.actions]
        return result

    # ── writability pre-flight: abort ATOMICALLY before any real write ──
    probe_targets = [item[1] for item in plan]
    if settings_changed and settings_path is not None:
        probe_targets.append(settings_path)
    for target in probe_targets:
        try:
            _probe_writable(target)
        except OSError as e:
            result.refused = (
                f"Refusing: cannot write {target.parent} "
                f"({e.__class__.__name__}: {e}) — nothing was changed"
            )
            return result

    # ── perform the writes ──────────────────────────────────────────────
    try:
        for _spec, installed, expected, content_differs in plan:
            if not content_differs and installed.exists():
                # Mode-only repair: the bytes are current but the exec bit was
                # lost (e.g. a mode-stripping copy) — add the exec bits to the
                # existing mode, do not rewrite and do not broaden the mode.
                os.chmod(installed,
                         (installed.stat().st_mode & 0o777) | 0o111)
                result.actions.append(f"made {installed} executable")
                continue
            installed.parent.mkdir(parents=True, exist_ok=True)
            # Back up the copy we are about to replace whenever its bytes
            # differ — a pristine older copy acquiring a harmless `.bak` is
            # fine; a locally edited copy (any generation, un-markered
            # included) is never destroyed silently. Written atomically, so a
            # symlink planted at the deterministic `.bak` path is not followed.
            if content_differs:
                backup = installed.with_suffix(installed.suffix + ".bak")
                n = 1
                while backup.exists():
                    backup = installed.with_suffix(
                        installed.suffix + f".bak.{n}")
                    n += 1
                _atomic_copy(installed, backup, 0o600)
                result.actions.append(f"backed up {installed} → {backup}")
            _atomic_copy(_spec.source, installed, _target_mode(installed))
            result.actions.append(
                f"wrote {installed} ({HOOK_VERSION_TOKEN} {expected})"
            )
        if settings_changed and settings_path is not None:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(settings_path, json.dumps(data, indent=2) + "\n")
            result.actions.append(f"wrote {settings_path}")
    except OSError as e:
        result.refused = (
            f"Refusing: upgrade failed mid-write ({e.__class__.__name__}: "
            f"{e}) — the install may be partially upgraded; re-run `tortoise "
            "hooks upgrade` after fixing the filesystem"
        )
        return result

    # ── record where this install came FROM (best-effort) ───────────────
    # The installed hooks resolve their module dir from $TORTOISE_SRC_DIR, then
    # this record, then `../..` — and `../..` from an installed hook is $HOME.
    # Written last (only after every real write landed) so a refused or
    # dry-run upgrade leaves no misleading breadcrumb.  The ONE need-based rule
    # (``record_hook_src_dir_for_install``) writes it for every harness whose
    # installed hook cannot resolve `../..`, and writes NOTHING when `../..` is
    # a checkout (#4110, #4314).
    if result.ok and not dry_run:
        record_hook_src_dir_for_install(harness, root=root, home=home)

    result.findings_after = detect_install(root, harness)
    return result
