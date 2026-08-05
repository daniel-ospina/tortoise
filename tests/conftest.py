"""Pytest configuration — test graph isolation guard (#99).

Forces ALL integration tests onto an isolated graph name starting with
'tortoise_test_' so that the SDK-level DETACH DELETE guard passes, and
no test can affect the real graph (falkordb-personal:16379/tortoise).

The default URI uses the test FalkorDB container (port 6379) with an
isolated graph name. Individual test files or CI can override via
TORTOISE_DB_URI env var as long as the graph name starts with
'test_' or 'tortoise_test'.
"""
from __future__ import annotations

import os
import uuid

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
