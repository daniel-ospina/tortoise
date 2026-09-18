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
The version is **the marker inside the shipped script itself** — there is no
constant, no separate manifest, and nothing to keep in sync::

    # tortoise-hook-version: 3        <- column-0 header line, one per file

``read_hook_version()`` reads that line out of a file; the "expected" version
for an install is read from the *repo* copy of the script, and the "installed"
version from the *user's* copy.  The comparison is therefore directly
source-vs-installed, and the number a user greps is the same number this code
acts on.  All scripts in a layout must declare the **same** generation
(:func:`contract_version` returns ``None`` if they disagree) so the contract
covering both halves — script bytes *and* the settings ``timeout`` — has one
visible number.  The marker is bumped on every behavioural edit to a shipped
hook, and on every change to the install contract those hooks participate in.

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
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

#: The marker token.  A canonical marker is a column-0 ``#`` comment line of
#: the form ``# tortoise-hook-version: <int>``.
HOOK_VERSION_TOKEN = "tortoise-hook-version"

# Column-0 anchored on purpose: an indented marker (a comment inside a script
# body) is a historical site marker, never the contract declaration.
_HOOK_VERSION_RE = re.compile(
    rf"^#\s*{re.escape(HOOK_VERSION_TOKEN)}:\s*(\d+)\s*$", re.MULTILINE
)

#: Where the shipped hook scripts live inside this package.
_HOOKS_SOURCE_DIR = Path(__file__).resolve().parent / "claude-hooks"


#: Substrings that identify a hook body as Tortoise's. Deliberately specific
#: (a bare word ``tortoise`` would match a foreign hook that merely mentions
#: it) — the pre-#3795 un-markered hooks contain several of these.
_TORTOISE_SIGNATURES = (
    "tortoise context",
    "tortoise session",
    "tortoise index",
    "session probe --harness",
    "from tortoise",
    "import tortoise",
    "tortoise.__main__",
    "tortoise/claude-hooks",
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
    directory, or carries no column-0 ``# tortoise-hook-version: N`` line.  A
    pre-#3795 install (no marker on ``session-start.sh``) is exactly this
    case, so ``None`` is a first-class *stale* signal, never an error.  The
    ``is_file`` gate also keeps a FIFO/socket at the path from blocking on
    ``read_text``.
    """
    p = Path(path)
    if not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    matches = _HOOK_VERSION_RE.findall(text)
    return int(matches[0]) if matches else None


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
    """One installed script artifact and the settings entry it requires."""

    name: str
    event: str
    timeout: int
    rel_command: str

    @property
    def source(self) -> Path:
        """The shipped (repo) copy of this script — the install source."""
        return _HOOKS_SOURCE_DIR / self.name


@dataclass(frozen=True)
class HarnessLayout:
    """Everything harness-specific about a capture-hook install.

    ``settings_file=None`` describes a scripts-only harness (no JSON settings
    merge) — the reason the settings logic is an adapter, not a Claude
    assumption.  ``hooks_dir``/``settings_file`` are install-root-relative.
    """

    harness: str
    hooks_dir: str
    scripts: tuple[HookScriptSpec, ...]
    settings_file: str | None = None

    def hooks_root(self, root: Path) -> Path:
        return root / self.hooks_dir

    def settings_path(self, root: Path) -> Path | None:
        return (root / self.settings_file) if self.settings_file else None


_CLAUDE_HOOKS_DIR = ".claude/hooks"


def _claude_layout() -> HarnessLayout:
    return HarnessLayout(
        harness="claude",
        hooks_dir=_CLAUDE_HOOKS_DIR,
        settings_file=".claude/settings.json",
        scripts=(
            HookScriptSpec(
                "session-start.sh", "SessionStart", 60,
                f"{_CLAUDE_HOOKS_DIR}/session-start.sh",
            ),
            HookScriptSpec(
                "session-end.sh", "SessionEnd", 60,
                f"{_CLAUDE_HOOKS_DIR}/session-end.sh",
            ),
        ),
    )


#: Shipped layouts.  Cursor (#3819) and Codex (#3818) add entries here.
HARNESS_LAYOUTS: dict[str, HarnessLayout] = {
    "claude": _claude_layout(),
}


def get_layout(harness: str) -> HarnessLayout:
    try:
        return HARNESS_LAYOUTS[harness]
    except KeyError:
        known = ", ".join(sorted(HARNESS_LAYOUTS))
        raise ValueError(
            f"unknown harness {harness!r} — known layouts: {known}"
        ) from None


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
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-S",
                      "--split-string", "-P", "--path", "--argv0"}),
    # sudo: every option that names a user/group/dir/role/host/prompt takes a
    # word; ``-n``/``-s``/``-k``/``-i``/``-E``/``-S``/``-b``/``-A``/``-H`` are
    # booleans.
    "sudo": frozenset({
        "-a", "--auth-type", "-c", "--class", "-C", "--close-from",
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
        "--max-args", "-e", "-P", "--max-procs", "-R", "-s", "-S",
        "--max-chars",
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

#: Launchers that are shell syntax, not programs, and so cannot have options.
_OPTION_LESS_LAUNCHERS = frozenset({
    "eval", "!", ".", "source", "if", "while", "until", "then", "do",
    "else", "elif", "`",
})

#: An environment assignment prefix (``A=/x/y``, ``PATH=$PATH:/bin``).
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: A launcher operand that is a number/duration (``timeout 5``, ``nice -n 7``).
_LAUNCHER_OPERAND_RE = re.compile(r"^[0-9]+(\.[0-9]+)?[smhd]?$")


def _as_launcher(tok: str, quoted: bool = False) -> str | None:
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
    """
    name = tok if tok in _LAUNCHERS else Path(tok).name
    if name in _OPTIONS_WITH_ARG:
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
                    root: str | os.PathLike[str] | None = None) -> bool:
    """True when a command line EXECUTES our script under ``hooks_dir``.

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
            if _invokes_script(tok, script_name, hooks_dir, root):
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
        launcher = _as_launcher(tok, quoted)
        if launcher is not None:
            launcher_word = launcher
            continue
        if not quoted and tok.startswith("-"):
            if launcher_word in _SHELLS and (
                    tok == "--noexec"
                    or (not tok.startswith("--") and "n" in tok[1:])):
                return False  # ``bash -n`` / ``sh -n``: syntax check only
            options = _OPTIONS_WITH_ARG.get(launcher_word or "", frozenset())
            if launcher_word in _SHELLS and tok in _SHELL_COMMAND_FLAGS:
                # A shell's ``-c`` argument is a command STRING to re-parse.
                recurse_next = True
            elif tok in options:
                skip_next = True
            elif (len(tok) > 2 and not tok.startswith("--")
                    and any(("-" + ch) in options for ch in tok[1:])):
                # combined short flags hide a separate value (``-euxo pipefail``)
                skip_next = True
            continue
        if _ASSIGNMENT_RE.match(tok):
            continue
        if (launcher_word == "timeout" and not quoted
                and _LAUNCHER_OPERAND_RE.match(tok)):
            continue
        expect_cmd = False
        if _token_is_our_script(tok, script_name, hooks_dir, root):
            return True
    return False


def _entry_command_dicts(entry: object, script_name: str | None = None,
                         hooks_dir: str | None = None,
                         root: str | os.PathLike[str] | None = None,
                         ) -> list[dict]:
    """EVERY child command dict in ``entry`` that invokes our script.

    A wrapper entry may hold the same command more than once; checking only
    the first would leave the second untimed (Claude Code cancels it at its
    1.5 s default) while ``detect_install`` reported the install current.
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

    inner = entry.get("hooks")
    if isinstance(inner, list):
        return [item for item in inner if _ok(item)]
    return []


def _entry_command_dict(entry: object, script_name: str | None = None,
                        hooks_dir: str | None = None,
                        root: str | os.PathLike[str] | None = None) -> dict | None:
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
    """
    if not isinstance(entry, dict):
        return None

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
                   root: str | os.PathLike[str] | None = None) -> bool:
    """True when an entry invokes our ``script_name`` under ``hooks_dir``."""
    return _entry_command_dict(entry, script_name, hooks_dir, root) is not None


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


def _target_mode(installed: Path) -> int:
    """Mode for a rewritten hook: 0755 for a fresh copy, else the existing
    mode plus exec bits (a script installed 0700 stays 0700, not 0755)."""
    if installed.exists():
        try:
            return (installed.stat().st_mode & 0o777) | 0o111
        except OSError:
            return 0o755
    return 0o755


def _settings_findings(layout: HarnessLayout, data: dict,
                       root: str | os.PathLike[str] | None = None,
                       ) -> list[Finding]:
    hooks = data.get("hooks") or {}
    findings: list[Finding] = []
    for spec in layout.scripts:
        entries = hooks.get(spec.event)
        if entries is None:
            findings.append(Finding(
                "missing-hook-entry",
                f"no {spec.event} hook entry in settings (expected "
                f"{spec.rel_command} with timeout {spec.timeout})",
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
        ours = [e for e in entries
                if _entry_is_ours(e, spec.name, layout.hooks_dir, root)]
        if not ours:
            findings.append(Finding(
                "missing-hook-entry",
                f"{spec.event} has no entry invoking {spec.name} (expected "
                f"timeout {spec.timeout})",
                script=spec.name, event=spec.event,
            ))
            continue
        for entry in ours:
            for inner in _entry_command_dicts(entry, spec.name,
                                              layout.hooks_dir, root):
                timeout = inner.get("timeout")
                if not isinstance(timeout, int) or isinstance(timeout, bool):
                    findings.append(Finding(
                        "settings-no-timeout",
                        f"{spec.event} entry for {spec.name} has no integer "
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
        if installed.stat().st_mode & 0o111 == 0:
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
                    _entry_is_ours(e, spec.name, layout.hooks_dir, root)
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

    Merges in place: only the specific entry's ``timeout`` is written; every
    other key, event, and entry is preserved byte-for-byte after the JSON
    round-trip.  Returns True when the document changed.
    """
    changed = False
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
                if _entry_is_ours(e, spec.name, layout.hooks_dir, root)]
        if not ours:
            entries.append({
                "matcher": "",
                "hooks": [{
                    "type": "command",
                    "command": spec.rel_command,
                    "timeout": spec.timeout,
                }],
            })
            actions.append(
                f"settings: added {spec.event} entry for {spec.name} "
                f"(timeout {spec.timeout})"
            )
            changed = True
            continue
        for entry in ours:
            for inner in _entry_command_dicts(entry, spec.name,
                                              layout.hooks_dir, root):
                timeout = inner.get("timeout")
                if (not isinstance(timeout, int) or isinstance(timeout, bool)
                        or timeout < spec.timeout):
                    inner["timeout"] = spec.timeout
                    actions.append(
                        f"settings: set {spec.event} timeout={spec.timeout} "
                        f"on {spec.name} (was {timeout!r})"
                    )
                    changed = True
    return changed


def upgrade_install(root: str | os.PathLike[str], harness: str = "claude",
                    dry_run: bool = False) -> UpgradeResult:
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
    # list) must be refused, never silently replaced by an empty list — that
    # would destroy a user's hooks.
    if any(f.kind == "unreadable-settings" for f in result.findings_before):
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
            and not (installed.stat().st_mode & 0o111)
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

    result.findings_after = detect_install(root, harness)
    return result
