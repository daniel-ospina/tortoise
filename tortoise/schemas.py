"""Ask-lane constant layer + canonical error vocabulary (#1987 Tasks 5/7).

The single source of truth for the ask lane's boundary RULES and canonical
error-code strings, referenced by the eval-only lane's local validator
(``tortoise.ask_lane._ask_validate``) — no duplicated boundary literals
(P2-14). Also re-exports the typed ask exceptions AND the canonical
error-code vocabulary (defined in ``tortoise/exceptions.py``) so callers
have ONE import surface.

⛔ The ask lane is EVAL-ONLY (#3849): this is the constant layer it
validates against, NOT a product surface (no MCP tool, no SDK method, no
REST route).
"""
from __future__ import annotations

# ── Boundary rules (single-sourced — P2-14) ────────────────────────────────

#: Max question length (chars). The 2000-char boundary passes; 2001 fails.
MAX_ASK_QUESTION_CHARS = 2000

#: The 4 fragment types + None (the closed question_type enum). Anything
#: else → ``invalid_question_type`` with the valid list.
ASK_QUESTION_TYPES: tuple[str | None, ...] = (
    "temporal-reasoning", "knowledge-update", "multi-session",
    "single-session-preference", None,
)

#: question_date regex — ``YYYY-MM-DD``; the calendar rule (real month/day,
#: leap-year aware) is enforced by ``validate_ask_question_date``.
_ASK_DATE_RE = r"^\d{4}-\d{2}-\d{2}$"

import datetime as _dt  # noqa: E402
import re as _re  # noqa: E402

_ASK_DATE_PATTERN = _re.compile(_ASK_DATE_RE)


def validate_ask_question_date(value: str) -> bool:
    """Calendar-checked question_date validation (P2-20): the regex AND the
    real calendar (month 00/13, day 00/30/31-vs-month, non-leap Feb 29 →
    False). Future dates accepted — no time-travel v1."""
    if not value or not _ASK_DATE_PATTERN.match(value):
        return False
    try:
        y, m, d = (int(x) for x in value.split("-"))
        _dt.date(y, m, d)
        return True
    except ValueError:
        return False


def ask_question_has_control_chars(question: str) -> bool:
    """Reject any question containing U+0000-U+001F control chars, or
    U+200B/U+00A0-only-after-strip, at ANY position (P2-9/P2-22).
    Validate-then-reject — no sanitize-then-send in v1."""
    for ch in question:
        if "\x00" <= ch <= "\x1f":
            return True
    stripped = question.strip()
    if not stripped:
        return False  # empty handled by the caller (invalid_question)
    return all(ch in ("\u200b", "\u00a0") for ch in stripped)


_PUNCT_CATEGORIES = frozenset(("Pc", "Pd", "Pe", "Pf", "Pi", "Po", "Ps"))


def ask_question_is_punctuation_only(question: str) -> bool:
    """Punctuation-only question → ``invalid_question`` (P2-20): after
    strip, every remaining char is a Unicode punctuation category or
    whitespace (so ".", "?!", "…" reject; "a.", digits-only, and
    emoji/Symbol questions pass). Empty-after-strip is the caller's empty
    case — not flagged here."""
    import unicodedata as _ud
    stripped = question.strip()
    if not stripped:
        return False
    return all(
        ch.isspace() or _ud.category(ch) in _PUNCT_CATEGORIES
        for ch in stripped
    )


# ── Canonical error-code vocabulary + typed exception re-export ────────────
# (one import surface for Tasks 9/11)
# The CODE_* constants live in ``tortoise/exceptions.py`` — the typed ask
# exception ``code`` class attributes reference them there — and are
# re-exported here so the lane's validators and the exception ``code``
# attributes share ONE vocabulary (a drift between the two is impossible by
# construction; the previous literal duplication here was the manual drift
# invariant). The retired half of the vocabulary — the /v1/ask wire body —
# went with the product surface (#3849); see ASK_ERROR_CODES below. The typed
# ask exceptions are re-exported too.

from tortoise.exceptions import (  # noqa: E402
    CODE_IN_FLIGHT_LIMIT,
    CODE_INVALID_QUESTION,
    CODE_INVALID_QUESTION_DATE,
    CODE_INVALID_QUESTION_TYPE,
    CODE_QUESTION_TOO_LONG,
    CODE_QUOTA_EXCEEDED,
    CODE_READER_UNAVAILABLE,
    CODE_RETRIEVAL_UNAVAILABLE,
    CODE_TIMEOUT,
    CODE_UNAUTHORIZED,
    AskInFlightLimit,
    AskQuotaExceeded,
    AskReaderUnavailable,
    AskRetrievalUnavailable,
    AskTimeout,
    AskValidationError,
)

#: RETIRED (#3849): the full code tuple the removed path-scoped /v1/ask
#: translators matched on. No consumer remains (the route and its handlers
#: went with the product surface); retained as vocabulary pending the
#: #3849 §7 D5 purge — see the note in tortoise/exceptions.py.
ASK_ERROR_CODES: tuple[str, ...] = (
    CODE_UNAUTHORIZED, CODE_QUOTA_EXCEEDED, CODE_IN_FLIGHT_LIMIT,
    CODE_READER_UNAVAILABLE, CODE_RETRIEVAL_UNAVAILABLE, CODE_TIMEOUT,
    CODE_INVALID_QUESTION, CODE_INVALID_QUESTION_TYPE,
    CODE_INVALID_QUESTION_DATE, CODE_QUESTION_TOO_LONG,
)

#: Local-lane AskValidationError instance codes — pinned to the canonical
#: vocabulary constants (P2-14): empty/whitespace → invalid_question, oversize →
#: question_too_long, bad type → invalid_question_type, bad date →
#: invalid_question_date.
VALIDATION_CODE_EMPTY = CODE_INVALID_QUESTION
VALIDATION_CODE_OVERSIZE = CODE_QUESTION_TOO_LONG
VALIDATION_CODE_BAD_TYPE = CODE_INVALID_QUESTION_TYPE
VALIDATION_CODE_BAD_DATE = CODE_INVALID_QUESTION_DATE


def valid_question_types() -> str:
    """RETIRED (#3849): the human-readable valid list for the removed 400
    ``invalid_question_type`` wire body (the 4 fragment types; None is the
    default). No caller left — the eval-only lane inlines the list in its
    ``AskValidationError`` message."""
    return "|".join(t for t in ASK_QUESTION_TYPES if t)


__all__ = [
    "ASK_ERROR_CODES",
    "ASK_QUESTION_TYPES",
    "CODE_INVALID_QUESTION",
    "CODE_INVALID_QUESTION_DATE",
    "CODE_INVALID_QUESTION_TYPE",
    "CODE_IN_FLIGHT_LIMIT",
    "CODE_QUESTION_TOO_LONG",
    "CODE_QUOTA_EXCEEDED",
    "CODE_READER_UNAVAILABLE",
    "CODE_RETRIEVAL_UNAVAILABLE",
    "CODE_TIMEOUT",
    "CODE_UNAUTHORIZED",
    "MAX_ASK_QUESTION_CHARS",
    "VALIDATION_CODE_BAD_DATE",
    "VALIDATION_CODE_BAD_TYPE",
    "VALIDATION_CODE_EMPTY",
    "VALIDATION_CODE_OVERSIZE",
    "AskInFlightLimit",
    "AskQuotaExceeded",
    "AskReaderUnavailable",
    "AskRetrievalUnavailable",
    "AskTimeout",
    "AskValidationError",
    "ask_question_has_control_chars",
    "ask_question_is_punctuation_only",
    "valid_question_types",
    "validate_ask_question_date",
]

