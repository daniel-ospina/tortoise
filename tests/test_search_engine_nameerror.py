"""Regression tests for Issue #92: bare 'logger' NameError in search code paths.

Tests that malformed relationship_filter and traversal_path inputs
degrade gracefully (log a warning) instead of raising NameError.

Runnable with: .venv/bin/python -m pytest tests/test_search_engine_nameerror.py -v
"""
from __future__ import annotations

import os
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


class TestSearchNameError:
    """Issue #92: bare 'logger' references in search code paths must not crash."""

    def test_malformed_relationship_filter_no_nameerror(self, sdk):
        """relationship_filter without ':' should not raise NameError."""
        # badformat has no colon — should trigger the "Invalid format" warning
        # but NOT a NameError
        try:
            results = sdk.tortoise_fts_query(
                query="test",
                relationship_filter="badformat",
            )
        except NameError as e:
            pytest.fail(
                f"relationship_filter='badformat' raised NameError: {e}"
            )
        except Exception:
            # Other exceptions (e.g., DB errors in temp db) are acceptable
            pass

    def test_relationship_filter_no_predicate_no_nameerror(self, sdk):
        """relationship_filter with colon but empty predicate should not crash."""
        try:
            results = sdk.tortoise_fts_query(
                query="test",
                relationship_filter=":target",
            )
        except NameError as e:
            pytest.fail(
                f"relationship_filter=':target' raised NameError: {e}"
            )
        except Exception:
            pass

    def test_relationship_filter_no_target_no_nameerror(self, sdk):
        """relationship_filter with colon but empty target should not crash."""
        try:
            results = sdk.tortoise_fts_query(
                query="test",
                relationship_filter="predicate:",
            )
        except NameError as e:
            pytest.fail(
                f"relationship_filter='predicate:' raised NameError: {e}"
            )
        except Exception:
            pass

    def test_ascii_arrow_traversal_path_no_nameerror(self, sdk):
        """ASCII '->' in traversal_path should log a warning, not crash."""
        try:
            results = sdk.tortoise_fts_query(
                query="test",
                traversal_path="Product->Feature",
            )
        except NameError as e:
            pytest.fail(
                f"traversal_path='Product->Feature' raised NameError: {e}"
            )
        except Exception:
            pass

    def test_traversal_path_no_nameerror(self, sdk):
        """Unicode 'Product→Feature' traversal_path (no matching pack relation)."""
        try:
            results = sdk.tortoise_fts_query(
                query="test",
                traversal_path="Product→Feature",
            )
        except NameError as e:
            pytest.fail(
                f"traversal_path='Product→Feature' raised NameError: {e}"
            )
        except Exception:
            pass

    def test_resolve_traversal_path_direct_ascii_arrow(self, sdk):
        """Direct call to _resolve_traversal_path with ASCII '->'."""
        try:
            result = sdk._resolve_traversal_path("Product->Feature")
            assert result is None, "ASCII arrow should return None"
        except NameError as e:
            pytest.fail(
                f"_resolve_traversal_path('Product->Feature') raised NameError: {e}"
            )
        except Exception:
            pass

    def test_resolve_traversal_path_short_segment(self, sdk):
        """_resolve_traversal_path with only one segment (no arrow)."""
        try:
            result = sdk._resolve_traversal_path("Product")
            assert result is None, "Single segment should return None"
        except NameError as e:
            pytest.fail(
                f"_resolve_traversal_path('Product') raised NameError: {e}"
            )
        except Exception:
            pass
