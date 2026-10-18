"""Unit tests for graph-viz server connection kwargs (#1079).

The server builds its FalkorDB connection kwargs from env vars. This test
locks the contract: username/ssl passthrough (hosted FalkorDB Cloud /
ACL-auth self-hosted) without regressing the password-less local dev path.
"""
from __future__ import annotations

import importlib.util
import os  # noqa: F401
import sys  # noqa: F401
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"


def _load_helper():
    """Import just the connection-kwargs builder without importing main.py
    (which connects to FalkorDB at import time)."""
    spec = importlib.util.spec_from_file_location(
        "gviz_conn", SERVER_DIR / "connection.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("FALKORDB_HOST", "FALKORDB_PORT", "FALKORDB_USERNAME",
              "FALKORDB_PASSWORD", "FALKORDB_GRAPH", "FALKORDB_SSL",
              # #8118: the socket bounds come from the product's resolver, so the
              # operator knobs are part of this builder's env contract too.
              "TORTOISE_DB_SOCKET_TIMEOUT", "TORTOISE_DB_SOCKET_CONNECT_TIMEOUT"):
        monkeypatch.delenv(k, raising=False)


def test_defaults_match_local_dev():
    """No env → the docker-compose local defaults (unchanged behavior).

    The socket pair is asserted separately (#8118) rather than pinned inside this
    dict equality, so an added bound never looks like a change of ENDPOINT.
    """
    mod = _load_helper()
    kwargs = mod.build_connection_kwargs()
    assert {k: v for k, v in kwargs.items() if not k.startswith("socket_")} == {
        "host": "localhost", "port": 16379}


def test_socket_bounds_are_present_by_default():
    """#8118: ``FalkorDB`` defaults ``socket_timeout=None`` — the read is UNBOUNDED.

    A missing bound is not "redis-py's default"; ``falkordb`` passes its own
    ``None`` to redis-py explicitly. One wedged read then parks the boot loop in
    ``recv()`` forever, so the pair must be PRESENT without any env set.
    """
    mod = _load_helper()
    kwargs = mod.build_connection_kwargs()
    assert kwargs["socket_timeout"] is not None, (
        "an absent read bound is the unbounded recv() #8118 exists to kill")
    assert kwargs["socket_timeout"] > 0
    assert kwargs["socket_connect_timeout"] is not None
    assert kwargs["socket_connect_timeout"] > 0


def test_socket_bounds_follow_the_product_knobs(monkeypatch):
    """The bound is the PRODUCT's policy, not a literal re-derived here.

    With the package importable the operator's knobs must reach this server, or
    the viz boot would be the one client that ignores them.
    """
    pytest.importorskip("tortoise")
    monkeypatch.setenv("TORTOISE_DB_SOCKET_TIMEOUT", "3")
    monkeypatch.setenv("TORTOISE_DB_SOCKET_CONNECT_TIMEOUT", "0.5")
    mod = _load_helper()
    kwargs = mod.build_connection_kwargs()
    assert kwargs["socket_timeout"] == 3.0
    assert kwargs["socket_connect_timeout"] == 0.5


def test_username_passthrough(monkeypatch):
    """FALKORDB_USERNAME lands in the client kwargs (hosted Cloud / ACL)."""
    monkeypatch.setenv("FALKORDB_USERNAME", "tortoise")
    monkeypatch.setenv("FALKORDB_PASSWORD", "secret")
    mod = _load_helper()
    kwargs = mod.build_connection_kwargs()
    assert kwargs["username"] == "tortoise"
    assert kwargs["password"] == "secret"


def test_ssl_flag(monkeypatch):
    """FALKORDB_SSL=1 enables TLS (rediss-style endpoints)."""
    monkeypatch.setenv("FALKORDB_SSL", "1")
    mod = _load_helper()
    kwargs = mod.build_connection_kwargs()
    assert kwargs["ssl"] is True


def test_ssl_disabled_by_default(monkeypatch):
    """No FALKORDB_SSL → ssl not forced (None, client default)."""
    mod = _load_helper()
    kwargs = mod.build_connection_kwargs()
    assert kwargs.get("ssl") is None


def test_graph_env(monkeypatch):
    """FALKORDB_GRAPH selects the graph; connection kwargs stay clean."""
    monkeypatch.setenv("FALKORDB_GRAPH", "mygraph")
    mod = _load_helper()
    kwargs = mod.build_connection_kwargs()
    assert "graph" not in kwargs and "graph_name" not in kwargs
    assert mod.graph_name() == "mygraph"


def test_graph_default(monkeypatch):
    """No FALKORDB_GRAPH → default 'tortoise'."""
    mod = _load_helper()
    assert mod.graph_name() == "tortoise"
