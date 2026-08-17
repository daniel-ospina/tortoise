"""Frontmatter validator tests (issue #1362).

The validator is OPTIONAL (TORTOISE_VALIDATE_FRONTMATTER=1, default OFF),
warn-only, and ADDS on top of the tolerant ``file_indexer.parse_frontmatter``
(which still degrades to {} on malformed input — unchanged). These tests pin:

  - the required-field sets per template (session vs document),
  - the env-gate seam (mirrors TORTOISE_SESSION_LLM_MOCK),
  - warn-only behavior via caplog (never raises, silent when disabled),
  - the hosted-capture SessionRequest shape check.
"""
import logging

import pytest

from tortoise.frontmatter_validator import (
    CAPTURE_REQUIRED_FIELDS,
    DOCUMENT_REQUIRED_FIELDS,
    SESSION_REQUIRED_FIELDS,
    TORTOISE_VALIDATE_FRONTMATTER,
    validate_and_warn,
    validate_frontmatter,
    validation_enabled,
)

# ── fixtures ────────────────────────────────────────────────────────────

VALID_SESSION_FM = {
    "sessionId": "session_abc123",
    "topics": ["engineering", "infrastructure"],
    "summary": "Debugged the index sweep non-convergence.",
    "eventId": "session_abc123_event",
    "doc_status": "captured",
    "agent": "pi",
    "message_count": 12,
}

VALID_DOCUMENT_FM = {
    "title": "Indexer architecture notes",
    "doc_status": "draft",
    "topics": ["indexing", "epic-900"],
    "summary": "Notes on the #900 indexer boundary.",
    "sessionId": "session_abc123",
}


@pytest.fixture()
def enable_validation(monkeypatch):
    """Turn the shared flag ON for a test."""
    monkeypatch.setenv(TORTOISE_VALIDATE_FRONTMATTER, "1")


# ── required-field sets ────────────────────────────────────────────────

def test_session_required_fields():
    assert SESSION_REQUIRED_FIELDS == (
        "sessionId", "topics", "summary", "eventId", "doc_status",
        "agent", "message_count",
    )


def test_document_required_fields():
    assert DOCUMENT_REQUIRED_FIELDS == (
        "title", "doc_status", "topics", "summary", "sessionId",
    )


def test_document_required_set_differs_from_session():
    assert set(DOCUMENT_REQUIRED_FIELDS) != set(SESSION_REQUIRED_FIELDS)
    # document requires title but NOT eventId/agent/message_count
    assert "title" in DOCUMENT_REQUIRED_FIELDS
    for f in ("eventId", "agent", "message_count"):
        assert f not in DOCUMENT_REQUIRED_FIELDS


def test_capture_required_fields():
    # Hosted SessionRequest synthetic shape — identity + conversation.
    assert CAPTURE_REQUIRED_FIELDS == ("session_id", "conversation")


# ── validate_frontmatter ───────────────────────────────────────────────

def test_valid_session_frontmatter_no_messages():
    assert validate_frontmatter(VALID_SESSION_FM, kind="session") == []


def test_valid_document_frontmatter_no_messages():
    assert validate_frontmatter(VALID_DOCUMENT_FM, kind="document") == []


def test_missing_required_fields_return_messages():
    fm = {"sessionId": "s1", "topics": ["a"], "summary": "s"}
    msgs = validate_frontmatter(fm, kind="session")
    assert msgs == [
        "missing required field: eventId",
        "missing required field: doc_status",
        "missing required field: agent",
        "missing required field: message_count",
    ]


def test_malformed_empty_dict_reports_all_required_missing():
    # parse_frontmatter degrades to {} on malformed input (unchanged) — the
    # validator reports every required field as missing on the degraded dict.
    msgs = validate_frontmatter({}, kind="session")
    assert len(msgs) == len(SESSION_REQUIRED_FIELDS)
    assert all(m.startswith("missing required field: ") for m in msgs)
    assert {m.rsplit(": ", 1)[1] for m in msgs} == set(SESSION_REQUIRED_FIELDS)


def test_kind_selects_document_required_set():
    # A session-complete file is NOT document-complete (missing title).
    msgs = validate_frontmatter(VALID_SESSION_FM, kind="document")
    assert msgs == ["missing required field: title"]
    # A document file lacks agent/message_count/eventId — only the doc set
    # matters under kind="document".
    assert validate_frontmatter(
        {k: v for k, v in VALID_DOCUMENT_FM.items()}, kind="document") == []


def test_unknown_kind_falls_back_to_session():
    assert validate_frontmatter(VALID_SESSION_FM, kind="meeting") == []
    msgs = validate_frontmatter({}, kind="meeting")
    assert len(msgs) == len(SESSION_REQUIRED_FIELDS)


# ── field-type checks ──────────────────────────────────────────────────

def test_topics_must_be_list_of_nonempty_strings():
    base = dict(VALID_SESSION_FM)
    base["topics"] = "engineering"  # scalar string
    msgs = validate_frontmatter(base)
    assert any("invalid topics" in m for m in msgs)
    base["topics"] = []  # empty list
    assert any("invalid topics" in m for m in validate_frontmatter(base))
    base["topics"] = ["ok", ""]  # blank member
    assert any("invalid topics" in m for m in validate_frontmatter(base))
    base["topics"] = [1, 2]  # non-strings
    assert any("invalid topics" in m for m in validate_frontmatter(base))


def test_summary_must_be_nonempty_string():
    base = dict(VALID_SESSION_FM)
    base["summary"] = 42
    msgs = validate_frontmatter(base)
    assert any("invalid summary" in m for m in msgs)
    base["summary"] = "   "
    msgs = validate_frontmatter(base)
    assert any("missing required field: summary" in m for m in msgs)


def test_session_id_and_event_id_must_be_nonempty_strings():
    base = dict(VALID_SESSION_FM)
    base["sessionId"] = 123
    msgs = validate_frontmatter(base)
    assert any("invalid sessionId" in m for m in msgs)
    base = dict(VALID_SESSION_FM)
    base["eventId"] = None
    assert any("missing required field: eventId" in m for m in
               validate_frontmatter(base))


def test_doc_status_lenient_nonempty_string():
    # No canonical doc_status vocabulary exists (draft/captured/extracted
    # observed in ingest.py; POINT_STATUS_VALUES is Point status) — the check
    # is a lenient non-empty string, so unknown-but-sane values pass.
    base = dict(VALID_SESSION_FM)
    base["doc_status"] = "extracted"
    assert validate_frontmatter(base) == []
    base["doc_status"] = 7
    assert any("invalid doc_status" in m for m in validate_frontmatter(base))


def test_message_count_must_be_nonnegative_integer():
    base = dict(VALID_SESSION_FM)
    base["message_count"] = -1
    assert any("invalid message_count" in m for m in validate_frontmatter(base))
    base["message_count"] = "abc"
    assert any("invalid message_count" in m for m in validate_frontmatter(base))
    base["message_count"] = True  # bool is not a count
    assert any("invalid message_count" in m for m in validate_frontmatter(base))
    base["message_count"] = 0  # 0 is a valid (if suspicious) count
    assert validate_frontmatter(base) == []
    base["message_count"] = "12"  # numeric string tolerated
    assert validate_frontmatter(base) == []


def test_non_dict_frontmatter_is_reported_not_raised():
    msgs = validate_frontmatter(["not", "a", "dict"], kind="session")
    assert len(msgs) == 1
    assert "not a dict" in msgs[0]


# ── env gate ───────────────────────────────────────────────────────────

def test_validation_enabled_default_off(monkeypatch):
    monkeypatch.delenv(TORTOISE_VALIDATE_FRONTMATTER, raising=False)
    assert validation_enabled() is False


def test_validation_enabled_flag_on(monkeypatch):
    monkeypatch.setenv(TORTOISE_VALIDATE_FRONTMATTER, "1")
    assert validation_enabled() is True


def test_validation_enabled_flag_off_values(monkeypatch):
    # Seam mirrors TORTOISE_SESSION_LLM_MOCK: ONLY "1" enables — any other
    # value (including case variants and "true") is off.
    for val in ("0", "", "false", "no", "off", "TRUE", "true"):
        monkeypatch.setenv(TORTOISE_VALIDATE_FRONTMATTER, val)
        assert validation_enabled() is False, val


# ── validate_and_warn (warn-only, silent when disabled) ────────────────

def test_validate_and_warn_logs_when_enabled(enable_validation, caplog):
    with caplog.at_level(logging.WARNING,
                         logger="tortoise.frontmatter_validator"):
        validate_and_warn({}, kind="session", context="unit:file.md")
    assert caplog.records
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "unit:file.md" in joined
    assert "missing required field: sessionId" in joined
    assert all(r.levelno == logging.WARNING for r in caplog.records)


def test_validate_and_warn_silent_when_disabled(caplog):
    with caplog.at_level(logging.WARNING,
                         logger="tortoise.frontmatter_validator"):
        validate_and_warn({}, kind="session", context="unit:file.md")
    assert not caplog.records


def test_validate_and_warn_clean_no_warnings(enable_validation, caplog):
    with caplog.at_level(logging.WARNING,
                         logger="tortoise.frontmatter_validator"):
        validate_and_warn(VALID_SESSION_FM, kind="session",
                          context="unit:file.md")
    assert not caplog.records


def test_validate_and_warn_never_raises(enable_validation, caplog):
    # Garbage inputs — even non-dicts — must never raise (warn-only contract).
    with caplog.at_level(logging.WARNING,
                         logger="tortoise.frontmatter_validator"):
        validate_and_warn(None, kind="session", context="unit:file.md")  # type: ignore[arg-type]
        validate_and_warn("garbage", kind="document", context="unit:doc.md")  # type: ignore[arg-type]
        validate_and_warn({}, kind="document", context="unit:doc.md")
    assert len(caplog.records) >= 1


# ── hosted capture shape check (synthetic dict via validate_and_warn) ───

def test_capture_clean():
    assert validate_frontmatter(
        {"session_id": "session_x",
         "conversation": [{"role": "user", "content": "hi"}]},
        kind="capture",
    ) == []


def test_capture_missing_session_id():
    msgs = validate_frontmatter(
        {"session_id": None,
         "conversation": [{"role": "user", "content": "hi"}]},
        kind="capture",
    )
    assert len(msgs) == 1
    assert "missing required field: session_id" in msgs[0]


def test_capture_empty_conversation():
    msgs = validate_frontmatter(
        {"session_id": "session_x", "conversation": []},
        kind="capture",
    )
    assert len(msgs) == 1
    assert "invalid conversation" in msgs[0]


def test_capture_both_gaps():
    msgs = validate_frontmatter(
        {"session_id": "", "conversation": []}, kind="capture")
    assert len(msgs) == 2
    assert all("session_id" in m or "conversation" in m for m in msgs)


def test_capture_non_string_session_id():
    msgs = validate_frontmatter(
        {"session_id": 123,
         "conversation": [{"role": "user", "content": "hi"}]},
        kind="capture",
    )
    assert len(msgs) == 1
    assert "invalid session_id" in msgs[0]


def test_validate_and_warn_capture_logs_when_enabled(enable_validation, caplog):
    with caplog.at_level(logging.WARNING,
                         logger="tortoise.frontmatter_validator"):
        validate_and_warn(
            {"session_id": None, "conversation": []},
            kind="capture", context="capture_session",
        )
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "capture_session" in joined
    assert "session_id" in joined
    assert "conversation" in joined


def test_validate_and_warn_capture_silent_when_disabled(caplog):
    with caplog.at_level(logging.WARNING,
                         logger="tortoise.frontmatter_validator"):
        validate_and_warn(
            {"session_id": None, "conversation": []},
            kind="capture", context="capture_session",
        )
    assert not caplog.records


def test_validate_and_warn_context_defaults_to_empty():
    # Signature contract from the issue: context defaults to "" — the call
    # must never require it (silent when the flag is off).
    validate_and_warn({}, kind="session")
    validate_and_warn({}, kind="document", context="x")
    assert True
