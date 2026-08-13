"""Live-FalkorDB test utilities (#942).

Plain module on purpose: a function defined in conftest.py is auto-collected
as a pytest FIXTURE and cannot be called directly. This module is not
auto-loaded, so _skip_unless_live_uri stays a plain function importable by
test_event_store.py and test_embedded_concurrency.py.
"""
from __future__ import annotations


def _skip_unless_live_uri():
    """Skip a docker:// live-FalkorDB test when no URI is configured (#942).

    Divergence from _skip_if_no_falkor (probe-based, test_projection.py):
    these tests REQUIRE the real server that CI's test-concurrency-falkor job
    provides (TORTOISE_DB_URI set); in every other surface they must skip
    VISIBLY (pytest.skip), never early-return-green — the vacuity pattern
    #942 exists to kill.
    """
    import os

    import pytest

    if not os.environ.get("TORTOISE_DB_URI"):
        pytest.skip(
            "requires TORTOISE_DB_URI (live FalkorDB sidecar; see CI job "
            "test-concurrency-falkor)"
        )
