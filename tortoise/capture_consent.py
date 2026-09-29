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
    (``session capture``, ``sessions import``) fail closed here, and a command
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

The truthy vocabulary is NOT declared here: it delegates to the tree's single
declared contract, `tortoise/env_truthy.py` (#4097), so this module cannot drift
from it. Default OFF, presence-gated.
"""
from __future__ import annotations

import os
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


def record_capture_declined(home: Path | str | None = None) -> bool:
    """Write the durable migration notice. Returns True only on the first write.

    Best-effort and never raises: this runs on a fail-closed refusal path, and a
    failed bookkeeping write must not turn a declined capture into a crash.
    (Writing the file is not the same as *delivering* the notice — see
    `pending_capture_notice`.)
    """
    try:
        path = capture_notice_path(home)
        if path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"Tortoise: {CAPTURE_DECLINED_HINT}\n"
            "Capture used to follow the credential; it no longer does (#3615).\n",
            encoding="utf-8",
        )
        return True
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
        if capture_notice_shown_path(home).exists():
            return None
        text = capture_notice_path(home).read_text(encoding="utf-8").strip()
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
        stamp.write_text("shown\n", encoding="utf-8")
    except (OSError, RuntimeError):
        pass
