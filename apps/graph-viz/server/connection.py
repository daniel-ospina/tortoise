"""FalkorDB connection configuration for the graph-viz server (#1079).

Extracted from main.py so the env→kwargs contract is unit-testable without
triggering the import-time connection. Mirrors the tortoise SDK's
``FalkorProjection.from_uri`` semantics: ``username``/``password`` for
FalkorDB Cloud and ACL-auth self-hosted instances, ``ssl`` for rediss-style
TLS endpoints. All defaults preserve the original password-less local dev
behavior (docker compose up -d → localhost:16379).
"""
from __future__ import annotations

import os

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 16379
DEFAULT_GRAPH = "tortoise"


def build_connection_kwargs() -> dict:
    """Build the FalkorDB client kwargs from env vars.

    Returns a dict suitable for ``FalkorDB(**kwargs)``. Only connection
    params are included; the graph name is returned separately via
    :func:`graph_name` (it selects the graph, not the connection).
    """
    host = os.environ.get("FALKORDB_HOST", DEFAULT_HOST)
    port = int(os.environ.get("FALKORDB_PORT", str(DEFAULT_PORT)))
    username = os.environ.get("FALKORDB_USERNAME") or None
    password = os.environ.get("FALKORDB_PASSWORD") or None
    ssl = os.environ.get("FALKORDB_SSL", "").strip().lower() in (
        "1", "true", "yes", "on")

    kwargs: dict = {"host": host, "port": port}
    if username is not None:
        kwargs["username"] = username
    if password is not None:
        kwargs["password"] = password
    if ssl:
        kwargs["ssl"] = True
    return kwargs


def graph_name() -> str:
    """The graph to select (FALKORDB_GRAPH, default 'tortoise')."""
    return os.environ.get("FALKORDB_GRAPH", DEFAULT_GRAPH)
