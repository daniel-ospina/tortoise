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
              "FALKORDB_PASSWORD", "FALKORDB_GRAPH", "FALKORDB_SSL"):
        monkeypatch.delenv(k, raising=False)


def test_defaults_match_local_dev():
    """No env → the docker-compose local defaults (unchanged behavior)."""
    mod = _load_helper()
    kwargs = mod.build_connection_kwargs()
    assert kwargs == {"host": "localhost", "port": 16379}


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
