"""Tests for `tortoise context` CLI (#7840 auto-inject) — memory digest for
agent session-start hooks.

Runnable with: .venv/bin/python -m pytest tests/test_cli_context.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def db_env():
    """Temp database wired via TORTOISE_DB_PATH."""
    d = tempfile.mkdtemp(prefix="tortoise_ctx_test_")
    db_path = os.path.join(d, "test.db")
    old_path = os.environ.get("TORTOISE_DB_PATH")
    old_uri = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_PATH"] = db_path
    os.environ.pop("TORTOISE_DB_URI", None)
    sdk = TortoiseSDK(db_path=db_path)
    yield sdk
    sdk.close()
    if old_path is None:
        os.environ.pop("TORTOISE_DB_PATH", None)
    else:
        os.environ["TORTOISE_DB_PATH"] = old_path
    if old_uri is None:
        os.environ.pop("TORTOISE_DB_URI", None)
    else:
        os.environ["TORTOISE_DB_URI"] = old_uri


def _run_context():
    """Invoke `tortoise context` (main returns int; only __main__ raises)."""
    from tortoise.__main__ import main
    return main(["context"])


class TestCliContext:
    def test_empty_graph_prints_empty_notice(self, db_env, capsys):
        """Empty graph → digest says memory is empty, exits 0."""
        rc = _run_context()
        assert rc == 0
        out = capsys.readouterr().out
        assert "no prior sessions" in out

    def test_with_points_prints_digest(self, db_env, capsys):
        """Points → digest includes 'Tortoise memory' header + decision line."""
        db_env.create_point(kind="decision", content="Use FalkorDB as primary graph store")
        db_env.create_point(kind="observation", content="Alpha in production")

        rc = _run_context()
        assert rc == 0
        out = capsys.readouterr().out
        assert "# Tortoise memory" in out
        assert "Use FalkorDB as primary graph store" in out
        assert "file new decisions with tortoise_create_point" in out

    def test_empty_prints_to_stdout_not_stderr(self, db_env, capsys):
        """Empty notice goes to stdout (so it's injected, not swallowed)."""
        _run_context()
        captured = capsys.readouterr()
        assert "no prior sessions" in captured.out
        assert "no prior sessions" not in captured.err
