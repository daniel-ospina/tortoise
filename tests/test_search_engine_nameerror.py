"""Regression tests for Issue #92: bare 'logger' NameError in search code paths.

Tests that malformed relationship_filter and traversal_path inputs
degrade gracefully (log a warning) instead of raising NameError.

Runnable with: .venv/bin/python -m pytest tests/test_search_engine_nameerror.py -v
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK with temp database. Closed after test."""
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_search_ne_test_"), "test.db"
    )
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _seed_matching_point(sdk):
    """Insert one point the FTS query will match.

    The malformed-filter branches are gated on a non-empty result set
    (`if relationship_filter and result_ids:`), so against an empty DB they
    short-circuit before the malformed input is ever parsed — leaving the guard
    vacuous. Seed a match so the branch under test actually executes.
    """
    sdk.create_point("statement", "test content")


class TestSearchNameError:
    """Issue #92: bare 'logger' references in search code paths must not crash."""

    def test_malformed_relationship_filter_no_nameerror(self, sdk, caplog):
        """relationship_filter without ':' should warn, not raise NameError."""
        # badformat has no colon — should trigger the "must be
        # 'predicate:target_id'" warning but NOT a NameError
        _seed_matching_point(sdk)
        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            try:
                sdk.tortoise_fts_query(
                    query="test",
                    relationship_filter="badformat",
                )
            except NameError as e:
                pytest.fail(
                    f"relationship_filter='badformat' raised NameError: {e}"
                )
        assert "relationship_filter must be 'predicate:target_id'" in caplog.text

    def test_relationship_filter_no_predicate_no_nameerror(self, sdk, caplog):
        """Empty predicate should log "Invalid relationship_filter format" and not crash."""
        _seed_matching_point(sdk)
        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            try:
                sdk.tortoise_fts_query(
                    query="test",
                    relationship_filter=":target",
                )
            except NameError as e:
                pytest.fail(
                    f"relationship_filter=':target' raised NameError: {e}"
                )
        assert "Invalid relationship_filter format: :target" in caplog.text

    def test_relationship_filter_no_target_no_nameerror(self, sdk, caplog):
        """Empty target should log "Invalid relationship_filter format" and not crash."""
        _seed_matching_point(sdk)
        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            try:
                sdk.tortoise_fts_query(
                    query="test",
                    relationship_filter="predicate:",
                )
            except NameError as e:
                pytest.fail(
                    f"relationship_filter='predicate:' raised NameError: {e}"
                )
        assert "Invalid relationship_filter format: predicate:" in caplog.text

    def test_ascii_arrow_traversal_path_no_nameerror(self, sdk, caplog):
        """ASCII '->' in traversal_path should log a warning, not crash."""
        _seed_matching_point(sdk)
        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            try:
                sdk.tortoise_fts_query(
                    query="test",
                    traversal_path="Product->Feature",
                )
            except NameError as e:
                pytest.fail(
                    f"traversal_path='Product->Feature' raised NameError: {e}"
                )
        assert "traversal_path uses ASCII '->'" in caplog.text

    def test_traversal_path_no_nameerror(self, sdk):
        """Unicode 'Product→Feature' traversal_path must not raise NameError."""
        _seed_matching_point(sdk)
        # The traversal branch is gated on a non-empty result set — assert the
        # seeded point is retrievable so the branch under test is reachable.
        assert sdk.tortoise_fts_query(query="test")
        try:
            sdk.tortoise_fts_query(
                query="test",
                traversal_path="Product→Feature",
            )
        except NameError as e:
            pytest.fail(
                f"traversal_path='Product→Feature' raised NameError: {e}"
            )

    def test_resolve_traversal_path_direct_ascii_arrow(self, sdk):
        """Direct call to _resolve_traversal_path with ASCII '->'."""
        try:
            result = sdk._resolve_traversal_path("Product->Feature")
        except NameError as e:
            pytest.fail(
                f"_resolve_traversal_path('Product->Feature') raised NameError: {e}"
            )
        # The assertion lives OUTSIDE the try: an AssertionError is an
        # Exception, so inside it would be swallowed and could never fire.
        assert result is None, "ASCII arrow should return None"

    def test_resolve_traversal_path_short_segment(self, sdk):
        """_resolve_traversal_path with only one segment (no arrow)."""
        try:
            result = sdk._resolve_traversal_path("Product")
        except NameError as e:
            pytest.fail(
                f"_resolve_traversal_path('Product') raised NameError: {e}"
            )
        # Outside the try — see the ascii-arrow test above.
        assert result is None, "Single segment should return None"
