"""
Test isolation guard — prevents integration tests from wiping production data.

Background (#102, incident 2026-08-05):
test_calibration.py and similar integration tests defaulted TORTOISE_DB_URI to
the PRODUCTION graph (16379/tortoise) with teardowns that run
`MATCH (n) DETACH DELETE n`. A parallel test run wiped the real graph
(5,748 points lost).

This conftest enforces isolation:
1. If TORTOISE_DB_URI points at a production-looking graph (16379 or 6379
   with graph "tortoise"), tests FAIL FAST with a clear message unless
   ALLOW_DESTRUCTIVE_TESTS=1 is explicitly set.
2. Tests that mutate the graph should use an isolated graph name
   (test_<name>) instead of the production default.

Usage:
  # Safe: isolated graph, or no destructive teardown
  pytest tests/test_calibration.py

  # Explicit opt-in for genuinely destructive tests against a real DB
  ALLOW_DESTRUCTIVE_TESTS=1 TORTOISE_DB_URI=... pytest tests/...
"""
import os
import pytest

# ── Production-looking URIs that tests must never touch without opt-in ──
_DANGEROUS_PORTS = {"6379", "6380", "16379"}


def _looks_like_production(uri: str) -> bool:
    """Heuristic: docker://host:PORT/graph where PORT is a FalkorDB port and
    graph is the default 'tortoise'."""
    if not uri or "docker://" not in uri:
        return False
    try:
        hostport = uri.split("://", 1)[1].split("/", 1)[0]
        port = hostport.rsplit(":", 1)[-1].split("@")[-1]
        graph = ""
        if "/" in uri.split("://", 1)[1]:
            graph = uri.split("://", 1)[1].split("/", 1)[1]
    except Exception:
        return False
    if port not in _DANGEROUS_PORTS:
        return False
    # graph "tortoise" (default) is dangerous; "test_foo" or other named are safer
    return graph in ("tortoise", "")


def pytest_configure(config):
    """Fail fast if tests would target a production graph without opt-in."""
    if os.environ.get("ALLOW_DESTRUCTIVE_TESTS") == "1":
        return  # explicit opt-in
    uri = os.environ.get("TORTOISE_DB_URI", "")
    if _looks_like_production(uri):
        pytest.exit(
            "\n\n⛔ TEST ISOLATION GUARD (#102):\n"
            f"  TORTOISE_DB_URI={uri!r} looks like the PRODUCTION graph.\n"
            "  Integration tests here use destructive teardowns "
            "(MATCH (n) DETACH DELETE n) that would WIPE PRODUCTION DATA.\n"
            "  To run destructive tests against a real DB, set:\n"
            "    ALLOW_DESTRUCTIVE_TESTS=1 TORTOISE_DB_URI=<isolated-or-dev-db> pytest ...\n"
            "  Prefer an isolated graph name: docker://host:port/test_<name>.\n",
            returncode=1,
        )
