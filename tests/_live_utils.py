"""Live-FalkorDB test utilities (#942, #2815).

Plain module on purpose: a function defined in conftest.py is auto-collected
as a pytest FIXTURE and cannot be called directly. This module is not
auto-loaded, so _skip_unless_live_uri stays a plain function importable by
test_event_store.py and test_embedded_concurrency.py.
"""
from __future__ import annotations

import os

# The docker-lane default URI. python-ci.yml's `test` job provisions the
# passworded falkordb service on 6379 for BOTH the full (push/schedule) and
# the tier-2 (PR) fast-matrix legs — the job's `services:` block is
# unconditional. The tier-2 leg withholds TORTOISE_DB_URI (the URI-less
# embedded shape), NOT the service: these docker-lane probes still find a
# live server there, which is exactly why an "unavailable" skip in that lane
# is an availability regression the #1436 guard must red on.
DOCKER_TEST_URI = "docker://:falkordb@localhost:6379/tortoise_test_matrix"


def live_uri(default: str = DOCKER_TEST_URI) -> str:
    """TORTOISE_DB_URI for a live-FalkorDB-lane test — empty means UNSET (#2815).

    CI exports ``TORTOISE_DB_URI=""`` on the tier-2 URI-less (embedded-shape)
    leg, and the epic #1647 lane contract is "empty == unset": ``is_db_uri("")``
    is False and the redirect seam is truthy-gated (python-ci.yml: "Every reader
    treats \"\" as unset"). ``os.environ.get("TORTOISE_DB_URI", <default>)``
    does NOT honor that contract — a set-but-empty variable returns ``""`` and
    the default is never reached — so every docker probe built a scheme-less
    URI (``"_<hex>"``), ``FalkorProjection.from_uri`` raised
    "Unsupported scheme", the modules skipped with a "Live FalkorDB ...
    not available" reason, and the #1436 skip-guard redded every tier-2 PR
    while the job's falkordb service was up. ``or`` is the empty-as-unset read.

    Returns the value with any trailing ``"/"`` stripped: callers append
    ``"_<suffix>"`` to build a per-test graph URI. A value that strips to the
    empty string (e.g. ``"/"``) is not a usable URI — it falls through to
    ``default`` rather than handing callers the scheme-less ``"_<suffix>"``
    shape this helper exists to prevent.

    Not to be confused with ``tests/test_ingest.py::_live_uri``, a per-test
    ``test_*`` graph-path builder: always append a per-test suffix before
    using this value as a graph name.
    """
    raw = (os.environ.get("TORTOISE_DB_URI") or default).rstrip("/")
    return raw or default.rstrip("/")


def _skip_unless_live_uri():
    """Skip a docker:// live-FalkorDB test when no URI is configured (#942).

    Divergence from _skip_if_no_falkor (probe-based, test_projection.py):
    these tests REQUIRE the real server that CI's test-concurrency-falkor job
    provides (TORTOISE_DB_URI set); in every other surface they must skip
    VISIBLY (pytest.skip), never early-return-green — the vacuity pattern
    #942 exists to kill.
    """
    import pytest

    if not os.environ.get("TORTOISE_DB_URI"):
        pytest.skip(
            "requires TORTOISE_DB_URI (live FalkorDB sidecar; see CI job "
            "test-concurrency-falkor)"
        )
