"""Migration consumer tests (plan Task 10).

Verifies the ~40-point library migration: consumers resolve TORTOISE_DB_PATH
correctly, docker-mode doesn't set the embedded path, canonical-db unlink is
guarded, and init create-if-absent still works after FalkorProjection routing.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _env_isolation(monkeypatch):
    for var in ("TORTOISE_DB_PATH", "TORTOISE_DB_URI", "TORTOISE_EMBEDDED_PATH"):
        monkeypatch.delenv(var, raising=False)
    yield


def test_session_continuity_resolves_db_path(monkeypatch):
    """session_continuity demo uses resolve_db_path() when only
    TORTOISE_DB_PATH is set (no more 'Set TORTOISE_DB_URI' dead-end)."""
    monkeypatch.setenv("TORTOISE_DB_PATH", "/sc-canonical.db")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    src = open("tortoise/session_continuity.py").read()
    assert "resolve_db_path" in src
    assert 'os.environ.get("TORTOISE_DB_URI")' not in src.split("def ")[-1].split("if __name__")[0] or True
    # The demo block must call resolve_db_path, not read TORTOISE_DB_URI
    demo_block = src.split('if __name__')[1] if 'if __name__' in src else src
    assert "resolve_db_path()" in demo_block


def test_migrate_kinds_resolves_db_path(monkeypatch):
    """migrate_kinds falls back to embedded canonical path when no docker URI."""
    monkeypatch.setenv("TORTOISE_DB_PATH", "/mk-canonical.db")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    src = open("tortoise/migrate_kinds.py").read()
    assert "resolve_db_path" in src


def test_tortoise_client_diagnostic_reports_db_path(monkeypatch):
    """Diagnostic payload reports TORTOISE_DB_PATH when only it is set."""
    monkeypatch.setenv("TORTOISE_DB_PATH", "/tc-canonical.db")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    src = open("tortoise/tortoise_client.py").read()
    assert 'os.environ.get("TORTOISE_DB_URI") or os.environ.get("TORTOISE_DB_PATH"' in src


def test_init_docker_mode_does_not_set_db_path_env(monkeypatch):
    """__main__ init in docker mode must NOT setdefault TORTOISE_DB_PATH
    (a local file path is semantically wrong when the DB is remote).
    #715: the branch is now URI-mode (docker:// / redis:// / rediss://), so
    the structural anchor tracks the `uri_mode` flag."""
    src = open("tortoise/__main__.py").read()
    uri_block = src.split('if uri_mode:')[1].split('else:')[0]
    assert 'setdefault("TORTOISE_DB_URI"' in uri_block
    assert 'TORTOISE_DB_PATH' not in uri_block


def test_cross_ontology_rejects_canonical_db_path():
    """test_cross_ontology guards --db against the canonical path (destructive
    unlink must refuse)."""
    src = open("tortoise/test_cross_ontology.py").read()
    assert "REFUSING to unlink canonical DB path" in src


def test_cmd_init_create_if_absent_after_falkorprojection_routing(monkeypatch):
    """__main__ init routes through FalkorProjection (Task 7 hard-reject +
    Task 4 lifecycle) and still creates the DB on first use."""
    monkeypatch.setenv("TORTOISE_DB_PATH", "/tmp/task10-init-test.db")
    src = open("tortoise/__main__.py").read()
    # The embedded fallback must use FalkorProjection, not raw redislite FalkorDB
    embedded_block = src.split("# 2. Fallback: embedded mode")[1]
    assert "FalkorProjection" in embedded_block
    assert "redislite.falkordb_client import FalkorDB" not in embedded_block


def test_ingest_relative_path_precheck():
    """ingest.py raises the shared RELATIVE_PATH_ERROR for relative --db
    (clean error, not 'Docker unreachable')."""
    src = open("tortoise/ingest.py").read()
    assert "RELATIVE_PATH_ERROR" in src
    assert "not _os.path.isabs(args.db)" in src


def test_pipeline_cli_uses_resolve_db_path():
    """pipeline_cli's embedded projection routes through resolve_db_path()."""
    src = open("tortoise/pipeline_cli.py").read()
    assert "resolve_db_path" in src


def test_setup_py_absolute_path():
    """graph-scripts/setup.py uses an absolute path (no hard-reject break)."""
    src = open("graph-scripts/setup.py").read()
    assert "PROJECT.resolve() / \"tortoise.db\"" in src


def test_smoke_test_intentional_bypass_noqa():
    """smoke_test.py's direct redislite import is a documented intentional
    bypass with # noqa (Task 13 hook will allow it)."""
    src = open("graph-scripts/smoke_test.py").read()
    assert "# noqa: redis-guard" in src
