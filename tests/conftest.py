"""Pytest configuration — test graph isolation guard (#99) + per-test isolation (#221).

Forces ALL integration tests onto an isolated graph name starting with
'tortoise_test_' so that the SDK-level DETACH DELETE guard passes, and
no test can affect the real graph (falkordb-personal:16379/tortoise).

The default URI uses the test FalkorDB container (port 6379) with an
isolated graph name. Individual test files or CI can override via
TORTOISE_DB_URI env var as long as the graph name starts with
'test_' or 'tortoise_test'.

Per-test isolation (#221): an autouse fixture recomposes TORTOISE_DB_URI
per test so every test gets its OWN graph name
(``tortoise_test_<session>_<testname>``). Whole-graph ``DETACH DELETE``
calls then only ever wipe the test's own graph — order-dependence from
shared-graph pollution is eliminated structurally.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import uuid

import pytest

# ── Embedded-mode dependency guard (#450) ──────────────────────────────────
# falkordblite (not plain redislite) provides redislite.falkordb_client.
# Catch the gap early so a fresh checkout doesn't silently fail 69+ tests.
try:
    from redislite.falkordb_client import FalkorDB  # noqa: F401
except ImportError:
    print(
        "
⚠️  falkordblite NOT installed — embedded-mode tests will fail.
"
        "    Fix: pip install falkordblite>=0.10
"
        "    (plain redislite does NOT include the FalkorDB embedded client)
",
        file=sys.stderr,
    )


# ── Test graph isolation ───────────────────────────────────────────────────
# Generate a unique graph name per test session so parallel pytest runs
# do not collide. The graph name starts with 'tortoise_test_' which is
# required by the FalkorProjection._assert_test_graph() guard for
# DETACH DELETE operations.
TEST_GRAPH = f"tortoise_test_{uuid.uuid4().hex[:8]}"

# Default to the test FalkorDB container (not the real graph at 16379).
# Individual test files / CI can override via the env var as long as
# the graph name starts with 'test_' or 'tortoise_test'.
_TEST_DEFAULT_URI = f"docker://:falkordb@localhost:6379/{TEST_GRAPH}"

os.environ.setdefault("TORTOISE_DB_URI", _TEST_DEFAULT_URI)

# Per-worker uniqueness (xdist-safe): the session UUID is combined with
# the worker PID so parallel workers never collide on graph names.
_WORKER_UUID = f"{uuid.uuid4().hex[:8]}_{os.getpid()}"

# Tests that MUST keep whole-graph DETACH DELETE on their own graph
# (they verify the guard itself). Marked with @pytest.mark.allow_graph_delete
# — the per-test fixture still isolates them to their own graph, so the
# wipe stays safe; the marker documents intent and permits a future
# collection-time lint to require it.
_ALLOW_GRAPH_DELETE_MARK = "allow_graph_delete"


def _recompose_graph_name(base_uri: str, new_graph: str) -> str:
    """Replace the graph-name segment of a connection URI.

    URI-scheme-aware (#221): docker:// / redis:// / rediss:// URIs get their
    path (graph name) replaced, preserving query strings and fragments.
    Embedded file-path URIs pass through UNCHANGED — they are inherently
    isolated per-test via the per-test DB path, and a naive rewrite would
    corrupt them.

    Returns the original URI if the scheme is not one of the supported
    graph-addressed schemes.
    """
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(base_uri)
    if parsed.scheme not in ("docker", "redis", "rediss"):
        return base_uri
    # Pathless URI: append the per-test graph so isolation still applies
    # (P2, #221): docker://host:6379 → docker://host:6379/<graph>
    return urlunparse(parsed._replace(path=f"/{new_graph}"))


def _sanitize_node_name(name: str) -> str:
    """Sanitize a pytest node id into a safe graph-name fragment."""
    # node ids look like test_foo.py::TestClass::test_bar[param]
    fragment = name.split("::")[-1]
    fragment = re.sub(r"[^A-Za-z0-9_]", "_", fragment)
    fragment = fragment[:40]
    # P3 (#221): append a short hash so long parameterized names whose first
    # 40 sanitized chars collide (e.g. [ctx-a] vs [ctx-b]) still get unique
    # graphs.
    import hashlib
    suffix = hashlib.blake2b(name.encode(), digest_size=3).hexdigest()
    return f"{fragment or 'test'}_{suffix}"


@pytest.fixture(scope="session")
def shared_embedded_db():
    """One shared embedded FalkorDBLite DB for the whole session (#221 R5).

    R5 mitigation for the redislite process leak (#176): tests that need an
    embedded (redislite) DB create ONE server per session instead of one per
    test. Each test wipes the graph on its own (or the per-test graph name
    isolates it), so state never leaks across tests while the subprocess
    count stays at 1.

    # TODO(#176): stopgap — remove when the redislite root-cause fix lands.
    """
    import tempfile as _tf
    db_path = os.path.join(_tf.mkdtemp(prefix="tortoise_shared_embedded_"), "shared.db")
    yield db_path


@pytest.fixture(autouse=True)
def _per_test_graph(monkeypatch, request):
    """Give every test its own graph name on the session DB connection.

    Intercepts ALL SDK constructions during the test (they read
    TORTOISE_DB_URI at construction), including direct from_uri callers
    that read the env var at use-time. Module-level URI captures are
    handled individually in their test files (R3/R4, #221).
    """
    base_uri = os.environ.get("TORTOISE_DB_URI", "")
    if not base_uri:
        yield
        return
    test_name = _sanitize_node_name(request.node.name)
    graph = f"tortoise_test_{_WORKER_UUID}_{test_name}"
    monkeypatch.setenv("TORTOISE_DB_URI", _recompose_graph_name(base_uri, graph))
    yield
    # No teardown needed — the next test gets a fresh graph name.
    # Graphs accumulate on the shared server between tests (harmless —
    # each is private to its test).


def pytest_configure(config):
    """Register the allow_graph_delete marker (P4, #221)."""
    config.addinivalue_line(
        "markers",
        "allow_graph_delete: test legitimately wipes its own graph (guard-testing)",
    )


def pytest_collection_modifyitems(config, items):
    """Attach the allow_graph_delete marker to marked items."""
    for item in items:
        if _ALLOW_GRAPH_DELETE_MARK in item.keywords:
            item.add_marker(getattr(pytest.mark, _ALLOW_GRAPH_DELETE_MARK))
