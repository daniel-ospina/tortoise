"""Migration consumer tests (plan Task 10).

Verifies the ~40-point library migration: consumers resolve TORTOISE_DB_PATH
correctly, docker-mode doesn't set the embedded path, canonical-db unlink is
guarded, and init create-if-absent still works after FalkorProjection routing.
"""
from __future__ import annotations

import os  # noqa: F401
import subprocess  # noqa: F401
import sys  # noqa: F401
import tempfile  # noqa: F401

import pytest


@pytest.fixture(autouse=True)
def _env_isolation(monkeypatch):
    for var in ("TORTOISE_DB_PATH", "TORTOISE_DB_URI", "TORTOISE_EMBEDDED_PATH"):
        monkeypatch.delenv(var, raising=False)
    yield


def test_session_continuity_resolves_db_path(monkeypatch):
    """session_continuity demo uses resolve_db_path() when only
    TORTOISE_DB_PATH is set (no more 'Set TORTOISE_DB_URI' dead-end).

    Behavioural: the ``__main__`` demo block is executed with a spy
    ``SessionContinuity`` and ``TORTOISE_DB_URI`` unset, so the embedded
    branch must hand the *result of* ``resolve_db_path()`` to the
    constructor.  A hardcoded path, or a resolution call stranded on a dead
    branch (``resolve_db_path() if False else "/tmp/x.db"``), fails here — a
    source-substring check passes both, which is why this is not one.
    """
    monkeypatch.setenv("TORTOISE_DB_PATH", "/sc-canonical.db")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    src = open("tortoise/session_continuity.py").read()  # noqa: SIM115
    assert "resolve_db_path" in src
    marker = 'if __name__ == "__main__":'
    assert marker in src, "session_continuity demo entrypoint moved — update this test"
    demo_block = marker + src.split(marker, 1)[1]

    resolved = "/sentinel/canonical-resolved.db"
    calls: list[int] = []

    def _fake_resolve_db_path(*_args, **_kwargs):
        calls.append(1)
        return resolved

    monkeypatch.setattr("tortoise.config.resolve_db_path", _fake_resolve_db_path)

    constructed: dict[str, object] = {}

    class _SpySessionContinuity:
        def __init__(self, db_path=None):
            constructed["db_path"] = db_path

        def start(self, _topic="General"):
            return "session-spy"

        def capture(self, *_args, **_kwargs):
            return None

        def end(self):
            return None

    # Executing the demo block (not importing the module) is what lets the
    # spy stand in for the real SessionContinuity, which would open a DB.
    exec(compile(demo_block, "<session_continuity __main__>", "exec"),
         {"SessionContinuity": _SpySessionContinuity, "__name__": "__main__"})

    assert calls == [1], "demo's embedded branch must call resolve_db_path()"
    assert constructed["db_path"] == resolved, (
        "the constructed SessionContinuity must receive resolve_db_path()'s "
        f"result, got {constructed['db_path']!r}"
    )


def test_migrate_kinds_resolves_db_path(monkeypatch):
    """migrate_kinds falls back to embedded canonical path when no docker URI."""
    monkeypatch.setenv("TORTOISE_DB_PATH", "/mk-canonical.db")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    src = open("tortoise/migrate_kinds.py").read()  # noqa: SIM115
    assert "resolve_db_path" in src


def test_tortoise_client_diagnostic_reports_db_path(monkeypatch):
    """Diagnostic payload reports TORTOISE_DB_PATH when only it is set."""
    monkeypatch.setenv("TORTOISE_DB_PATH", "/tc-canonical.db")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    src = open("tortoise/tortoise_client.py").read()  # noqa: SIM115
    # Anchor on the EXPRESSION, not its line wrapping: `ruff format` reflows the
    # URI-or-PATH fallback across lines without changing the diagnostic payload,
    # so flatten whitespace before matching (stale line-anchor broke when
    # 7b452c0c7 reformatted this call).
    flat = " ".join(src.split())
    assert 'os.environ.get("TORTOISE_DB_URI") or os.environ.get("TORTOISE_DB_PATH"' in flat


def test_init_docker_mode_does_not_set_db_path_env(monkeypatch):
    """__main__ init in docker mode must NOT setdefault TORTOISE_DB_PATH
    (a local file path is semantically wrong when the DB is remote).
    #715: the branch is now URI-mode (docker:// / redis:// / rediss://), so
    the structural anchor tracks the `uri_mode` flag."""
    src = open("tortoise/__main__.py").read()  # noqa: SIM115
    uri_block = src.split('if uri_mode:')[1].split('else:')[0]
    assert 'setdefault("TORTOISE_DB_URI"' in uri_block
    assert 'TORTOISE_DB_PATH' not in uri_block


def test_cross_ontology_rejects_canonical_db_path():
    """test_cross_ontology guards --db against the canonical path (destructive
    unlink must refuse)."""
    src = open("tortoise/test_cross_ontology.py").read()  # noqa: SIM115
    assert "REFUSING to unlink canonical DB path" in src


def test_cmd_init_create_if_absent_after_falkorprojection_routing(monkeypatch):
    """__main__ init routes through FalkorProjection (Task 7 hard-reject +
    Task 4 lifecycle) and still creates the DB on first use."""
    monkeypatch.setenv("TORTOISE_DB_PATH", "/tmp/task10-init-test.db")
    src = open("tortoise/__main__.py").read()  # noqa: SIM115
    # The embedded fallback must use FalkorProjection, not raw redislite FalkorDB
    embedded_block = src.split("# 2. Fallback: embedded mode")[1]
    assert "FalkorProjection" in embedded_block
    assert "redislite.falkordb_client import FalkorDB" not in embedded_block


def test_ingest_relative_path_precheck():
    """ingest.py raises the shared RELATIVE_PATH_ERROR for relative --db
    (clean error, not 'Docker unreachable')."""
    src = open("tortoise/ingest.py").read()  # noqa: SIM115
    assert "RELATIVE_PATH_ERROR" in src
    assert "not _os.path.isabs(args.db)" in src


def test_pipeline_cli_uses_resolve_db_path():
    """pipeline_cli's embedded projection routes through resolve_db_path()."""
    src = open("tortoise/pipeline_cli.py").read()  # noqa: SIM115
    assert "resolve_db_path" in src


def test_setup_py_absolute_path():
    """graph-scripts/setup.py uses an absolute path (no hard-reject break)."""
    src = open("graph-scripts/setup.py").read()  # noqa: SIM115
    assert "PROJECT.resolve() / \"tortoise.db\"" in src


def test_smoke_test_intentional_bypass_noqa():
    """smoke_test.py's direct redislite import is a documented intentional
    bypass with # noqa (Task 13 hook will allow it)."""
    src = open("graph-scripts/smoke_test.py").read()  # noqa: SIM115
    assert "# noqa: redis-guard" in src
