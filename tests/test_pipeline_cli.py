"""Pipeline CLI tests — #715 P2 conf 75: TORTOISE_DB_URI routing.

`tortoise pipeline run` must route a supported TORTOISE_DB_URI (docker:// /
redis:// / rediss://) through FalkorProjection.from_uri() — never silently
resolve the embedded default while the URI points elsewhere (split graph).
"""
from __future__ import annotations

import os
import sys
import types
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise import pipeline_cli


def _write_config(tmp_path, name):
    cfg = tmp_path / "pipelines.yaml"
    cfg.write_text(
        "version: 1\npipelines:\n"
        f"  {name}:\n"
        "    enabled: true\n"
        "    connector:\n"
        f"      module: {name}\n"
        "      class: FakeConn\n"
        "      config: {}\n",
        encoding="utf-8",
    )
    return cfg


def _register_connector(monkeypatch, module_name, ingested=1):
    fake_mod = types.ModuleType(module_name)

    class FakeConn:
        def __init__(self, config=None):
            pass

        def poll(self):
            return ["e1"]

        def ingest(self, proj):
            return ingested

    fake_mod.FakeConn = FakeConn
    monkeypatch.setitem(sys.modules, module_name, fake_mod)


def _patch_projection(monkeypatch, seen):
    """Record whether cmd_run uses from_uri (URI) or __init__ (path)."""
    from tortoise.projection import FalkorProjection

    class _FakeProj:
        def close(self):
            pass

    def _from_uri(cls, u, graph_name=None):
        seen["uri"] = u
        return _FakeProj()

    def _init_path(self, path, *a, **k):
        seen["path"] = path
        return None

    monkeypatch.setattr(FalkorProjection, "from_uri", classmethod(_from_uri))
    monkeypatch.setattr(FalkorProjection, "__init__", _init_path)


def test_run_routes_supported_uri_through_from_uri(monkeypatch, tmp_path, capsys):
    """TORTOISE_DB_URI=redis:// → FalkorProjection.from_uri(uri), never the
    embedded default."""
    uri = "redis://:pw@db.example.com:6379/tortoise"
    monkeypatch.setenv("TORTOISE_DB_URI", uri)
    monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)

    monkeypatch.setattr(pipeline_cli, "CONFIG_PATH", _write_config(tmp_path, "t1"))
    _register_connector(monkeypatch, "t1")
    seen: dict = {}
    _patch_projection(monkeypatch, seen)

    pipeline_cli.cmd_run("t1")

    assert seen.get("uri") == uri
    assert "path" not in seen  # embedded path never used
    assert "Ingested 1 entities" in capsys.readouterr().out


def test_run_uses_embedded_path_when_no_uri(monkeypatch, tmp_path):
    """No TORTOISE_DB_URI → embedded path via resolve_db_path() (unchanged)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)

    monkeypatch.setattr(pipeline_cli, "CONFIG_PATH", _write_config(tmp_path, "t2"))
    _register_connector(monkeypatch, "t2", ingested=2)
    from tortoise.config import DEFAULT_DB_PATH
    seen: dict = {}
    _patch_projection(monkeypatch, seen)

    pipeline_cli.cmd_run("t2")

    assert "uri" not in seen
    assert seen.get("path") == DEFAULT_DB_PATH
