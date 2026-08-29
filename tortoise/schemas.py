"""Ask-lane constant layer + canonical error vocabulary (#1987 Tasks 5/7).

The single source of truth for the ask surface's boundary RULES and
canonical error-code strings, referenced by BOTH the SDK local-lane
validator (``TortoiseSDK.ask``) and the ``AskRequest`` field validators
(Task 7) — no duplicated boundary literals (P2-14). Also re-exports the SDK
typed ask exceptions (defined in ``tortoise/exceptions.py``) so Tasks 9/11
have ONE import surface.
"""
from __future__ import annotations

# ── Boundary rules (single-sourced — P2-14) ────────────────────────────────

#: Max question length (chars). The 2000-char boundary passes; 2001 fails.
MAX_ASK_QUESTION_CHARS = 2000

#: The 4 fragment types + None (the closed question_type enum). Anything
#: else → 400 ``invalid_question_type`` with the valid list.
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
    """Punctuation-only question → 400 ``invalid_question`` (P2-20): after
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


# ── Canonical error-code vocabulary (10 codes — one vocabulary, two ────────
# ── surfaces: the wire body + the SDK exception ``code`` attributes) ───────

CODE_UNAUTHORIZED = "unauthorized"
CODE_QUOTA_EXCEEDED = "quota_exceeded"
CODE_IN_FLIGHT_LIMIT = "in_flight_limit"
CODE_READER_UNAVAILABLE = "reader_unavailable"
CODE_RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
CODE_TIMEOUT = "timeout"
CODE_INVALID_QUESTION = "invalid_question"
CODE_INVALID_QUESTION_TYPE = "invalid_question_type"
CODE_INVALID_QUESTION_DATE = "invalid_question_date"
CODE_QUESTION_TOO_LONG = "question_too_long"

ASK_ERROR_CODES: tuple[str, ...] = (
    CODE_UNAUTHORIZED, CODE_QUOTA_EXCEEDED, CODE_IN_FLIGHT_LIMIT,
    CODE_READER_UNAVAILABLE, CODE_RETRIEVAL_UNAVAILABLE, CODE_TIMEOUT,
    CODE_INVALID_QUESTION, CODE_INVALID_QUESTION_TYPE,
    CODE_INVALID_QUESTION_DATE, CODE_QUESTION_TOO_LONG,
)

#: Local-lane AskValidationError instance codes — pinned to the wire codes
#: (P2-14): empty/whitespace → invalid_question, oversize →
#: question_too_long, bad type → invalid_question_type, bad date →
#: invalid_question_date.
VALIDATION_CODE_EMPTY = CODE_INVALID_QUESTION
VALIDATION_CODE_OVERSIZE = CODE_QUESTION_TOO_LONG
VALIDATION_CODE_BAD_TYPE = CODE_INVALID_QUESTION_TYPE
VALIDATION_CODE_BAD_DATE = CODE_INVALID_QUESTION_DATE


def valid_question_types() -> str:
    """The human-readable valid list for the 400 ``invalid_question_type``
    body (the 4 fragment types; None is the default)."""
    return "|".join(t for t in ASK_QUESTION_TYPES if t)


# ── SDK typed exception re-export (one import surface for Tasks 9/11) ──────

from tortoise.exceptions import (  # noqa: E402
    AskInFlightLimit,
    AskQuotaExceeded,
    AskReaderUnavailable,
    AskRetrievalUnavailable,
    AskTimeout,
    AskValidationError,
)

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


# ── AskRequest (Task 7 — extends the Task-5 constant layer) ────────────────

from fastapi import HTTPException  # noqa: E402
from pydantic import BaseModel, field_validator, model_validator  # noqa: E402


class AskRequest(BaseModel):
    """POST /v1/ask request body (#1987 Task 7).

    The boundary RULES are single-sourced above (P2-14): max chars, the
    closed question_type enum, the date regex + calendar rule, the
    control-char rule. The validators raise ``HTTPException(400, detail=<the
    canonical code>)`` so FastAPI's default 422/``RequestValidationError``
    body never ships on the ask surface (P1-7).

    PERMISSIVE declaration + ``mode="before"`` validators (P1-7/P2-5): a
    plain ``field_validator('question')`` is mode="after" and never sees a
    wrong-typed value (``{"question": 123}`` raises a string_type
    ValidationError before any after-validator runs); the before-validator
    sees the RAW value, so the MISSING-question and wrong-type cases raise
    400 ``invalid_question`` here — never the default 422.
    """

    question: str | None = None
    question_type: str | None = None
    question_date: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _missing_question(cls, data):
        """MISSING question → 400 ``invalid_question`` (NOT the default 422):
        a field-level mode="before" validator does not run for an absent
        field with a default, so the model-level before validator sees the
        RAW body dict and rejects a body without the question key (P1-7)."""
        if isinstance(data, dict) and "question" not in data:
            raise HTTPException(status_code=400,
                                detail=CODE_INVALID_QUESTION)
        return data

    @field_validator("question", mode="before")
    @classmethod
    def _validate_question(cls, v):
        if v is None or not str(v).strip():
            raise HTTPException(status_code=400,
                                detail=CODE_INVALID_QUESTION)
        if not isinstance(v, str):
            raise HTTPException(status_code=400,
                                detail=CODE_INVALID_QUESTION)
        if ask_question_has_control_chars(v):
            raise HTTPException(status_code=400,
                                detail=CODE_INVALID_QUESTION)
        if ask_question_is_punctuation_only(v):
            raise HTTPException(status_code=400,
                                detail=CODE_INVALID_QUESTION)
        if len(v) > MAX_ASK_QUESTION_CHARS:
            raise HTTPException(status_code=400,
                                detail=CODE_QUESTION_TOO_LONG)
        return v

    @field_validator("question_type", mode="before")
    @classmethod
    def _validate_question_type(cls, v):
        if v is None:
            return v
        if v not in ASK_QUESTION_TYPES:
            raise HTTPException(
                status_code=400, detail=CODE_INVALID_QUESTION_TYPE)
        return v

    @field_validator("question_date", mode="before")
    @classmethod
    def _validate_question_date(cls, v):
        if v is None:
            return v
        if not validate_ask_question_date(str(v)):
            raise HTTPException(status_code=400,
                                detail=CODE_INVALID_QUESTION_DATE)
        return v
