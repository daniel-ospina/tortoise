"""#3615 — explicit consent for hosted session capture.

`TORTOISE_API_KEY` is the **MCP Bearer credential and nothing else**. Session
capture (shipping a session transcript to ``{TORTOISE_API_URL}``) is a
data-sharing act, not an authentication act, so it must never be inferred from
credential *presence* — exporting the key for the MCP ``Authorization`` header
must not opt a machine into uploading transcripts.

Capture therefore requires an explicit, credential-independent opt-in:
``TORTOISE_CAPTURE`` set to a truthy value (1 / true / yes / on). Unset, blank,
or any other value ⇒ capture is OFF. The predicate is deliberately
**host-agnostic**: a self-hosted daemon is still data leaving the machine, so
the consent requirement does not depend on which endpoint the config names.

Consumers:
  * `tortoise/__main__.py` — the transcript-upload primitives
    (``session capture``, ``sessions import``, ``session drain``) fail closed
    here, and a command
    whose stderr is a terminal pushes the pending migration notice once (the
    surface gate: a redirected/piped stderr consumes nothing; a pty-allocating
    non-human caller is a declared, notice-only residual).
  * `tortoise/claude-hooks/session-end.sh` — the ambient automatic path
    pre-checks the same variable in bash. The predicate is implemented twice on
    purpose (defense-in-depth: the hook gate stops the ambient path from even
    attempting the upload, the primitive gate neutralizes a STALE copied hook
    that never updates itself). The bash↔Python **parity is pinned by
    tests/test_session_capture_e2e.py** (the truthy/falsy matrix); the CLI-side
    gate is covered by tests/test_capture_consent.py.
  * `tortoise/sdk.py` — the SDK's client TRANSMISSION primitive
    (``_post_commit`` → ``POST {TORTOISE_API_URL}/v1/sessions/commit``) is the
    third client-side upload path (#3662), and it refuses here too. Its public
    caller ``TortoiseSDK.commit_session`` refuses FIRST, before any extraction
    runs, so no provider call is spent on an unauthorised transmission; the
    primitive re-checks so a future caller cannot bypass consent by calling it
    directly. Two ENFORCEMENT points, one predicate — the verdict string comes
    from ``capture_declined_reason`` below.

NOT gated here, by design (recorded so the two consent contracts cannot drift,
#3662):
  * ``tortoise/mcp_server.py::tortoise_session_capture`` executes SERVER-side
    (it answers "session capture requires hosted mode" for stdio/self-host),
    so the client host's ``TORTOISE_CAPTURE`` is unreadable there. Its gate is
    the server policy ``session_recording`` — default-ON, ToS-covered, an
    off-switch and explicitly NOT a consent gate (#1927). Gating this surface
    needs a CLIENT-CARRIED signal (an MCP request header) and is an open
    product question, not a client-side predicate (see #3662).
  * ``TortoiseSDK.capture_session`` writes to the GRAPH (embedded, or
    ``TORTOISE_DB_URI``) AND sends each turn to the configured BYOK extractor
    provider. It is out of scope here because gating it would also refuse pure
    local writes — but it is NOT egress-free: the provider leg transmits
    regardless of which backend ``TORTOISE_DB_URI`` names.

The truthy vocabulary is NOT declared here: it delegates to the tree's single
declared contract, `tortoise/env_truthy.py` (#4097), so this module cannot drift
from it. Default OFF, presence-gated.
"""
from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path

from tortoise.env_truthy import TRUTHY

#: The opt-in variable. Deliberately a *capture* name — never a credential
#: (`*_KEY`/`*_TOKEN`) name, so the two concepts cannot be confused.
#: (Ambient `TORTOISE_CAPTURE_*` names exist for server-side capture tuning —
#: `TORTOISE_CAPTURE_WORKERS` / `TORTOISE_CAPTURE_IN_FLIGHT` — the bare name is
#: the consent switch.)
CAPTURE_OPT_IN_ENV = "TORTOISE_CAPTURE"

#: The whitespace trimmed before the truthy comparison. Deliberately the ASCII
#: POSIX set (space/tab/CR/LF/VT/FF) and NOT `str.strip()`'s full `isspace()`
#: set: Python's also trims C1 controls (U+001C-U+001F, U+0085, U+2028, ...)
#: that bash's `[[:space:]]` leaves in place, which made the two predicates
#: disagree on those inputs (security review P2). Narrowing Python to the ASCII
#: set is the fail-closed direction and makes the parity claim exact.
_ASCII_WS = " \t\r\n\v\f"

#: Shown (stderr, non-blocking) when a capture is declined for lack of consent.
CAPTURE_DECLINED_HINT = (
    "capture is off — session capture requires explicit consent; "
    f"set {CAPTURE_OPT_IN_ENV}=1 to enable it (see docs/quickstart-cloud.md)"
)

#: The one-time migration notice is *written to disk*, not only printed: a
#: stale copied hook runs ``tortoise session capture … 2>/dev/null`` and would
#: discard the message, so a durable file is the only channel that survives it.
NOTICE_RELPATH = (".tortoise", "capture-consent-notice")

#: Written next to the notice once a human-facing surface has actually SHOWN it.
#: The notice file records that a refusal happened (which a stale hook does
#: silently); this stamp records that the *user* was told. Keeping them apart is
#: what makes delivery idempotent per human rather than per hook run.
NOTICE_SHOWN_SUFFIX = ".shown"


def capture_consent_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return True only for an explicit, truthy capture opt-in.

    Default OFF: unset, empty, whitespace, or any unrecognised value is False.
    """
    source = os.environ if env is None else env
    return str(source.get(CAPTURE_OPT_IN_ENV, "")).strip(_ASCII_WS).lower() in TRUTHY


def capture_declined_reason() -> str | None:
    """The refusal message when the host has NOT opted in, else ``None`` (#3662).

    The single *decline* side of the predicate. A transmitting surface refuses
    on a non-``None`` answer, so:

    * the durable migration notice is recorded on the SAME call that decides the
      refusal (a surface cannot refuse and forget the notice), and
    * a new surface cannot re-implement the refusal text — the returned string
      IS ``CAPTURE_DECLINED_HINT``, so returning it to a caller and printing it
      to stderr cannot diverge.

    Deliberately NOT a boolean: ``capture_consent_enabled()`` is the predicate,
    this is the refusal. Callers that only need the predicate keep calling it.
    """
    if capture_consent_enabled():
        return None
    record_capture_declined()
    return CAPTURE_DECLINED_HINT


def capture_notice_path(home: Path | str | None = None) -> Path:
    """Path of the durable one-time migration notice (``$HOME/.tortoise/…``).

    Resolved lazily (never frozen at import) so tests and callers that change
    ``$HOME`` see the right location.
    """
    base = Path(home) if home is not None else Path.home()
    return base.joinpath(*NOTICE_RELPATH)


def capture_notice_shown_path(home: Path | str | None = None) -> Path:
    """Path of the stamp consumed once the notice has been shown to a human."""
    notice = capture_notice_path(home)
    return notice.with_name(notice.name + NOTICE_SHOWN_SUFFIX)


def _open_no_follow(path: Path, flags: int) -> int | None:
    """`os.open` with `O_NOFOLLOW` folded in; `None` on any refusal.

    Never raises `OSError`: every failure (EEXIST, ELOOP, EISDIR, EACCES,
    ENOTDIR, ENXIO …) is the "this call did not touch it" answer these helpers
    return, because they run on a fail-closed refusal path where a bookkeeping
    failure must not become a crash.

    `O_NOFOLLOW` is folded in through `getattr` so the module still imports on a
    platform that lacks it (CPython documents it as Unix-only), and the two other
    Unix-only members this module uses — `O_NONBLOCK` and `os.fchmod` — are
    guarded the same way, so on such a platform these helpers degrade to the
    pre-#3684 behaviour (following the link, umask mode) rather than raising
    `AttributeError` out of a helper whose callers only expect `OSError`. That
    degradation is real and deliberate; the hardening is Unix-scoped in fact, and
    this is where that is decided.
    """
    try:
        return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return None


def _write_text_no_follow(path: Path, text: str, *, exclusive: bool) -> bool:
    """Write `text` to `path` WITHOUT following a symlink, at mode 0o600.

    `Path.write_text` follows symlinks and pins no mode, so a DANGLING symlink at
    `path` defeats a `path.exists()` first-write guard: `exists()` follows the
    link, sees nothing, and the write then creates or truncates an
    attacker-chosen target (#3684). `O_NOFOLLOW` refuses the final-component
    symlink instead, and `O_CREAT|O_EXCL` makes the first-write check atomic with
    the write rather than a separate, racy `exists()` probe.

    Three smaller hazards the same open closes:

    * the file is opened WITHOUT `O_TRUNC` and truncated only once `fstat` shows
      a regular file, so a FIFO or device at the path is refused rather than
      truncated, and `O_NONBLOCK` keeps the OPEN itself from blocking on a FIFO;
    * `fchmod` pins 0o600 outright instead of leaving it at `0o600 & ~umask`, so
      a restrictive umask cannot leave the notice unreadable to its own owner;
    * the fd is closed on every path, including a failure between `open` and the
      write.

    Returns True only when this call wrote the file, False when it deliberately
    did not (the path already exists, a symlink sits at it, it is not a regular
    file, or the open failed).
    """
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NONBLOCK", 0)
    if exclusive:
        flags |= os.O_EXCL
    handle_fd = _open_no_follow(path, flags)
    if handle_fd is None:
        return False
    try:
        handle_stat = os.fstat(handle_fd)
        if not stat.S_ISREG(handle_stat.st_mode):
            return False
        if not exclusive and handle_stat.st_nlink != 1:
            # A HARDLINK planted at the stamp is a REGULAR file that would pass
            # an O_TRUNC open and truncate data outside this directory.
            # O_NOFOLLOW does not cover hardlinks; this gate does, and it is the
            # same refusal `embedded_reaper.py` makes for its marker file. The
            # notice path needs no gate — O_EXCL already refuses any
            # pre-existing name.
            return False
        # `os.fchmod` is Unix-only (Windows gains it in 3.13), so it is guarded:
        # on a platform without it the mode falls back to `0o600 & ~umask`, which
        # is what the pre-#3684 code did — degraded, not a crash.
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            fchmod(handle_fd, 0o600)
        if not exclusive:
            os.ftruncate(handle_fd, 0)
        data = text.encode("utf-8")
        while data:
            data = data[os.write(handle_fd, data):]
    finally:
        os.close(handle_fd)
    return True


def _read_text_no_follow(path: Path) -> str | None:
    """Read `path` only when it is a regular file, never through a symlink.

    The READ half of the same guard (#3684): `read_text` follows links, so a
    symlink at the notice made the caller return an ARBITRARY readable file's
    content — which a terminal stderr then printed — and a symlink at the
    `….shown` stamp made it look already-shown, silently SUPPRESSING the
    migration notice. `O_NONBLOCK` keeps the open from blocking on a FIFO.

    Returns None when the path is absent, is not a regular file, or is
    unreadable — the same "nothing to say" answer the caller handles.
    """
    handle_fd = _open_no_follow(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    if handle_fd is None:
        return None
    try:
        if not stat.S_ISREG(os.fstat(handle_fd).st_mode):
            return None
        chunks: list[bytes] = []
        while True:
            chunk = os.read(handle_fd, 1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError:
        return None
    finally:
        os.close(handle_fd)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        return None


def record_capture_declined(home: Path | str | None = None) -> bool:
    """Write the durable migration notice. Returns True only on the first write.

    Best-effort and never raises: this runs on a fail-closed refusal path, and a
    failed bookkeeping write must not turn a declined capture into a crash.
    (Writing the file is not the same as *delivering* the notice — see
    `pending_capture_notice`.)
    """
    try:
        path = capture_notice_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        return _write_text_no_follow(
            path,
            f"Tortoise: {CAPTURE_DECLINED_HINT}\n"
            "Capture used to follow the credential; it no longer does (#3615).\n",
            exclusive=True,
        )
    except (OSError, RuntimeError):
        # RuntimeError: `Path.home()` with no $HOME and no passwd entry.
        return False


def pending_capture_notice(home: Path | str | None = None) -> str | None:
    """The migration notice text if it has never been shown to a human, else None.

    Delivery half of the migration. Writing the file is necessary but not
    sufficient: the population this change targets runs a STALE copied hook that
    swallows the CLI's stderr, and has no reason to ever run `tortoise doctor`,
    so a file-only notice is evidence without notification. Commands on a
    terminal stderr call this and print the result once (see
    `mark_capture_notice_shown`); a redirected or piped unattended run must NOT,
    so it cannot consume the human's one sighting of it (a pty-allocating
    non-human caller remains a declared residual).
    """
    try:
        # A symlink at the stamp is not a stamp (#3684): `exists()` follows the
        # link, so a link to ANY readable file read as "already shown" and the
        # migration notice was silently never delivered. `lstat` + `S_ISREG`
        # counts only a real regular file.
        try:
            stamp_mode = os.lstat(capture_notice_shown_path(home)).st_mode
        except OSError:
            stamp_mode = 0
        if stat.S_ISREG(stamp_mode):
            return None
        text = (_read_text_no_follow(capture_notice_path(home)) or "").strip()
    except (OSError, RuntimeError, UnicodeDecodeError):
        # UnicodeDecodeError is a ValueError, not an OSError: a partially
        # written notice (the body contains a multi-byte em dash, and
        # `write_text` truncates before writing) would otherwise escape this
        # best-effort helper and crash EVERY command — this runs unconditionally
        # from `main`. Same class `_resolve_config_path` guards for.
        return None
    return text or None


def mark_capture_notice_shown(home: Path | str | None = None) -> None:
    """Stamp the notice as shown. Best-effort; never raises."""
    try:
        stamp = capture_notice_shown_path(home)
        stamp.parent.mkdir(parents=True, exist_ok=True)
        # Rewritten by design, so TRUNC without EXCL — but still O_NOFOLLOW: a
        # symlink at the stamp is not a stamp.
        _write_text_no_follow(stamp, "shown\n", exclusive=False)
    except (OSError, RuntimeError):
        pass
