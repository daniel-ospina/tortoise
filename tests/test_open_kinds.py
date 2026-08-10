"""Tests for open-ended kind vocabularies (#6881).

Runnable without pytest:  .venv/bin/python tests/test_open_kinds.py
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK                    # noqa: E402
from tortoise.domain_loader import known_kinds, register_kind, kind_is_known  # noqa: E402


def test_known_kind_accepted():
    """Known kind values work without warnings."""
    sdk = TortoiseSDK(":memory:")
    try:
        point = sdk.create_point("statement", "A test statement")
        assert point["pointKind"] == "statement"
        assert point["content"] == "A test statement"
    finally:
        sdk.close()


def test_unrecognized_kind_warns_not_errors():
    """Unrecognized kind values are accepted but produce a warning."""
    sdk = TortoiseSDK(":memory:")
    try:
        # Capture warnings from the sdk logger
        logger = logging.getLogger("tortoise.sdk")
        logger.setLevel(logging.WARNING)
        
        with _log_capture(logger) as records:
            point = sdk.create_point("customResearchFinding", "A custom kind point")
        
        assert point["pointKind"] == "customResearchFinding"
        assert point["content"] == "A custom kind point"
        
        warnings = [r for r in records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "customResearchFinding" in warnings[0].getMessage()
    finally:
        sdk.close()


def test_register_kind_suppresses_warning():
    """After register_kind(), the kind is known and produces no warning."""
    register_kind("auditFinding")
    assert kind_is_known("auditFinding")
    
    sdk = TortoiseSDK(":memory:")
    try:
        logger = logging.getLogger("tortoise.sdk")
        logger.setLevel(logging.WARNING)
        
        with _log_capture(logger) as records:
            point = sdk.create_point("auditFinding", "An audit finding")
        
        assert point["pointKind"] == "auditFinding"
        warnings = [r for r in records if r.levelno == logging.WARNING]
        assert len(warnings) == 0, f"Expected no warnings, got: {[r.getMessage() for r in warnings]}"
    finally:
        sdk.close()


# ── helper ───────────────────────────────────────────────────

from contextlib import contextmanager

@contextmanager
def _log_capture(logger: logging.Logger):
    """Capture log records from a logger. Context managed."""
    handler = logging.handlers.MemoryHandler(100)
    handler.setLevel(logging.WARNING)
    old_handlers = logger.handlers[:]
    logger.handlers = [handler]
    try:
        yield handler.buffer
    finally:
        logger.handlers = old_handlers


if __name__ == "__main__":
    test_known_kind_accepted()
    test_unrecognized_kind_warns_not_errors()
    test_register_kind_suppresses_warning()
    print("All tests passed.")
