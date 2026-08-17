"""Optional frontmatter-metadata validator (issue #1362) — warn-only, gated.

The index/ingest pipeline's identity and metadata logic (``file_indexer``
``parse_frontmatter``, ``session_indexer.extract_keywords_from_frontmatter``,
``sdk._session_event_write``, ``ingest._parse_frontmatter``) CONSUMES a set of
frontmatter fields per template:

  - **session template** — ``sessionId``, ``topics``, ``summary``, ``eventId``,
    ``doc_status``, ``agent``, ``message_count``
  - **document template** (corpus files) — ``title``, ``doc_status``,
    ``topics``, ``summary``, ``sessionId``

This module ADDS an *optional* quality gate on top of the tolerant parser:
when the shared flag ``TORTOISE_VALIDATE_FRONTMATTER=1`` is set, files that
miss or mangle required metadata are **warned on** (WARNING log lines) —
never hard-failed, consistent with the tolerant-degrade philosophy. The flag
defaults OFF and is honored by BOTH onboarding/capture channels:

  1. **Hosted capture** — ``POST /v1/sessions`` (``hosted_api.capture_session``):
     the SessionRequest is a payload, not a frontmatter file, so the shape
     validator runs ``validate_and_warn`` over a SYNTHETIC dict built from
     the expected-metadata surface the request CAN provide (``session_id``
     identity + non-empty ``conversation``) under ``kind="capture"``.
  2. **CLI ingest/index** — ``sdk.ingest_corpus`` (legacy path) and the
     #900 index path write funnels (``sdk._session_event_write`` /
     ``sdk._doc_write``): the parsed frontmatter dict is validated right at
     the parse/write boundary.

Design invariants:

  - ``parse_frontmatter``'s tolerant behavior is UNCHANGED — malformed input
    still degrades to ``{}`` (never raises); validation only *reports* on the
    degraded dict (all required fields missing).
  - Nothing here ever raises or blocks a write — ``validate_and_warn`` is the
    only call-site surface used by the channels and it logs WARNINGs only.
  - The env-gate seam mirrors ``TORTOISE_SESSION_LLM_MOCK``
    (``os.environ.get(...).strip().lower() == "1"``) — set ``1`` to enable.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("tortoise.frontmatter_validator")

# Shared config flag — default OFF; set TORTOISE_VALIDATE_FRONTMATTER=1 to
# enable warn-only frontmatter validation on both onboarding channels.
TORTOISE_VALIDATE_FRONTMATTER = "TORTOISE_VALIDATE_FRONTMATTER"

# ── Required-field sets per template (#1362 decision record) ───────────────
# Session template — the fields sdk._session_event_write /
# session_indexer.extract_keywords_from_frontmatter /
# ingest._parse_frontmatter consume for session files.
SESSION_REQUIRED_FIELDS: tuple[str, ...] = (
    "sessionId",
    "topics",
    "summary",
    "eventId",
    "doc_status",
    "agent",
    "message_count",
)

# Document template — the fields the corpus (document) path consumes.
DOCUMENT_REQUIRED_FIELDS: tuple[str, ...] = (
    "title",
    "doc_status",
    "topics",
    "summary",
    "sessionId",
)

# Hosted-capture synthetic shape (``hosted_api.capture_session``) — the
# SessionRequest is a payload, not a frontmatter file; the synthetic dict
# carries the expected-metadata surface it CAN provide.
CAPTURE_REQUIRED_FIELDS: tuple[str, ...] = (
    "session_id",
    "conversation",
)

# Fields whose only check is "a non-empty string" (beyond presence).
# ``session_id`` is the hosted-capture synthetic identity field (#1362).
_STRING_FIELDS = frozenset(
    {"sessionId", "eventId", "summary", "doc_status", "agent", "title",
     "session_id"}
)


def validation_enabled() -> bool:
    """True when ``TORTOISE_VALIDATE_FRONTMATTER`` is set to ``1``.

    Default OFF. The seam mirrors the ``TORTOISE_SESSION_LLM_MOCK`` test-seam
    pattern (``os.environ.get(...).strip().lower() == "1"``) so the two gates
    behave identically under CI/test environments.
    """
    return os.environ.get(TORTOISE_VALIDATE_FRONTMATTER, "").strip().lower() == "1"


def _missing(value: Any) -> bool:
    """A required field is missing when absent, None, or blank (whitespace)."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _valid_topics(value: Any) -> bool:
    """topics must be a non-empty list of non-empty strings."""
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(isinstance(t, str) and t.strip() for t in value)
    )


def _valid_message_count(value: Any) -> bool:
    """message_count must be a non-negative integer (int or numeric string)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    if isinstance(value, str):
        s = value.strip()
        return s.lstrip("-").isdigit() and int(s) >= 0
    return False


def _valid_conversation(value: Any) -> bool:
    """conversation (hosted-capture synthetic) must be a non-empty list."""
    return isinstance(value, list) and len(value) > 0


def _describe(value: Any) -> str:
    """Short human description of a value for warning messages."""
    return repr(value)[:60]


def validate_frontmatter(frontmatter: dict, *, kind: str = "session") -> list[str]:
    """Return a list of missing/invalid required-field messages.

    ``kind`` selects the required-field set: ``"session"``
    (``SESSION_REQUIRED_FIELDS``) vs ``"document"``
    (``DOCUMENT_REQUIRED_FIELDS``) vs ``"capture"``
    (``CAPTURE_REQUIRED_FIELDS`` — the hosted SessionRequest synthetic
    shape); anything else is treated as ``"session"``. The tolerant parser
    already guarantees a dict (``{}`` on malformed), so a non-dict input is
    reported defensively — never raised. Empty list = clean.
    """
    if not isinstance(frontmatter, dict):
        return [
            "frontmatter is not a dict — required metadata cannot be validated"
        ]
    if kind == "document":
        required = DOCUMENT_REQUIRED_FIELDS
    elif kind == "capture":
        required = CAPTURE_REQUIRED_FIELDS
    else:
        required = SESSION_REQUIRED_FIELDS
    messages: list[str] = []
    for field in required:
        value = frontmatter.get(field)
        if _missing(value):
            messages.append(f"missing required field: {field}")
            continue
        if field == "topics":
            if not _valid_topics(value):
                messages.append(
                    f"invalid topics: expected a list of non-empty strings, "
                    f"got {_describe(value)}"
                )
        elif field == "message_count":
            if not _valid_message_count(value):
                messages.append(
                    f"invalid message_count: expected a non-negative integer, "
                    f"got {_describe(value)}"
                )
        elif field == "conversation":
            if not _valid_conversation(value):
                messages.append(
                    f"invalid conversation: expected a non-empty list of turns, "
                    f"got {_describe(value)}"
                )
        elif field in _STRING_FIELDS and not isinstance(value, str):
            messages.append(
                f"invalid {field}: expected a non-empty string, "
                f"got {_describe(value)}"
            )
    return messages


def validate_and_warn(
    frontmatter: dict, kind: str = "session", *, context: str = ""
) -> None:
    """Validate and log a WARNING per message — never raises.

    No-op unless ``validation_enabled()``. ``context`` names the call site
    (e.g. ``"ingest_corpus:docs/x.md"``) so warnings are actionable.
    """
    if not validation_enabled():
        return
    for msg in validate_frontmatter(frontmatter, kind=kind):
        logger.warning("frontmatter validation [%s] (%s): %s", kind, context, msg)
