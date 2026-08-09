"""Tests for `tortoise context` CLI (#7840 auto-inject) — memory digest for
agent session-start hooks.

Runnable with: .venv/bin/python -m pytest tests/test_cli_context.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

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


class TestCliOnboardDbTarget:
    """#705: the index step of `tortoise onboard` must receive the SAME DB
    target init resolved — embedded path (env or --path) or docker:// URI —
    and index failures must surface instead of a false 'Onboarding complete.'
    """

    @staticmethod
    def _stub_onboard(repo_root, index_rc=0):
        """Run _cmd_onboard with sub-steps stubbed; return (rc, idx_args)."""
        from tortoise import __main__ as m

        seen: dict = {}

        def fake_index(idx_args):
            seen["idx"] = idx_args
            return index_rc

        with mock.patch.object(m, "_cmd_init", return_value=0), \
             mock.patch.object(m, "_cmd_demo", return_value=0), \
             mock.patch.object(m, "_cmd_doctor", return_value=0), \
             mock.patch("subprocess.run") as fake_run, \
             mock.patch.object(m, "_cmd_index_github", side_effect=fake_index):
            fake_run.return_value.returncode = 0  # git repo detected
            fake_run.return_value.stdout = str(repo_root)
            rc = m._cmd_onboard(mock.Mock(path=None, cmd="onboard"))
        return rc, seen["idx"]

    def test_index_receives_resolved_embedded_path(self, tmp_path, monkeypatch):
        """Embedded-only mode: index gets the TORTOISE_DB_PATH target — not the
        canonical ~/.tortoise default (#705 regression guard)."""
        db_path = str(tmp_path / "onboard.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "README.md").write_text("# readme", encoding="utf-8")

        rc, idx = self._stub_onboard(repo_root)

        assert rc == 0
        assert idx.url == str(repo_root)
        assert idx.db == db_path
        assert idx.db != "tortoise.db"  # never silently fall to the default
        # Issue 4 guard: every attr _cmd_index_github reads must be present.
        for attr in ("url", "branch", "background", "db"):
            assert hasattr(idx, attr)

    def test_index_receives_docker_uri(self, tmp_path, monkeypatch):
        """TORTOISE_DB_URI=docker:// selects URI mode for the index step."""
        uri = "docker://:pw@localhost:16379/tortoise"
        monkeypatch.setenv("TORTOISE_DB_URI", uri)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "README.md").write_text("# readme", encoding="utf-8")

        rc, idx = self._stub_onboard(repo_root)

        assert rc == 0
        assert idx.db == uri

    def test_explicit_path_beats_env(self, tmp_path, monkeypatch):
        """onboard --path /custom.db must win over TORTOISE_DB_PATH (#715 bug:
        index silently targeted the default instead of --path)."""
        explicit = str(tmp_path / "explicit.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "env.db"))
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "README.md").write_text("# readme", encoding="utf-8")

        from tortoise import __main__ as m

        seen: dict = {}
        with mock.patch.object(m, "_cmd_init", return_value=0), \
             mock.patch.object(m, "_cmd_demo", return_value=0), \
             mock.patch.object(m, "_cmd_doctor", return_value=0), \
             mock.patch("subprocess.run") as fake_run, \
             mock.patch.object(m, "_cmd_index_github",
                               side_effect=lambda a: seen.update(idx=a) or 0):
            fake_run.return_value.returncode = 0
            fake_run.return_value.stdout = str(repo_root)
            rc = m._cmd_onboard(mock.Mock(path=explicit, cmd="onboard"))

        assert rc == 0
        assert seen["idx"].db == explicit

    def test_index_failure_surfaces_rc(self, tmp_path, monkeypatch, capsys):
        """Graceful index failure must exit non-zero, not claim success."""
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "onboard.db"))
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "README.md").write_text("# readme", encoding="utf-8")

        rc, _ = self._stub_onboard(repo_root, index_rc=1)
        out = capsys.readouterr().out

        assert rc == 1
        assert "Index failed" in out
        assert "Onboarding complete." not in out

    def test_onboard_relative_path_clean_error(self, capsys):
        """onboard --path rel.db must fail cleanly at init (rc 1, no
        traceback, index never called) — #715: resolve_db_path's _abs
        hard-reject was unguarded at the index call site and tracebacked."""
        from tortoise import __main__ as m

        with mock.patch.object(m, "_cmd_index_github") as fake_index:
            rc = m._cmd_onboard(mock.Mock(path="rel.db", cmd="onboard"))

        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert rc == 1
        assert "Relative DB path" in out
        assert "Traceback" not in out
        fake_index.assert_not_called()

    def test_init_no_silent_fallback_when_docker_target_down(self, monkeypatch, capsys):
        """conf 60: with TORTOISE_DB_URI=docker:// configured but unreachable,
        init must fail loudly (rc 1) — NOT silently fall back to the embedded
        default, which would split the graph from the index step (init writes
        embedded, index writes the remote URI)."""
        monkeypatch.setenv("TORTOISE_DB_URI", "docker://@127.0.0.1:1/tortoise")
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
        from tortoise import __main__ as m

        rc = m._cmd_init(mock.Mock(path=None, cmd="init", yes=True, api_key=None))

        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert rc == 1
        assert "Embedded mode initialized" not in out
        assert "Traceback" not in out

    def test_init_background_spawn_carries_resolved_db(self, tmp_path, monkeypatch):
        """conf 75: init --yes's background index spawn must carry the resolved
        --db target. index's --db is argparse-required, so an env-only spawn
        dies before resolving anything and "Indexing in background" would be
        a lie — the child would never index."""
        db_path = str(tmp_path / "init.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "README.md").write_text("# readme", encoding="utf-8")

        from tortoise import __main__ as m

        with mock.patch("subprocess.run") as fake_run, \
             mock.patch("subprocess.Popen") as fake_popen, \
             mock.patch("tortoise.projection.FalkorProjection"), \
             mock.patch("tortoise.sdk.TortoiseSDK") as fake_sdk:
            fake_run.return_value.returncode = 0  # git repo detected
            fake_run.return_value.stdout = str(repo_root)
            fake_sdk.return_value.status.return_value = {"counts": {"Point": 1}}
            rc = m._cmd_init(mock.Mock(path=None, cmd="init", yes=True, api_key=None))

        assert rc == 0
        assert fake_popen.called
        args = fake_popen.call_args.args[0]
        # spawn is: python -m tortoise index github <repo> --db <target>
        assert args[:3] == [sys.executable, "-m", "tortoise"]
        assert args[-4] == "github"
        assert "--db" in args
        assert args[args.index("--db") + 1] == db_path
        assert args[-1] != "tortoise.db"  # never the canonical default

    def test_init_prompted_spawn_carries_resolved_db(self, tmp_path, monkeypatch):
        """conf 75: the interactive (non --yes) init path's background spawn
        must carry --db too — same argparse-required failure otherwise."""
        db_path = str(tmp_path / "init.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "README.md").write_text("# readme", encoding="utf-8")

        from tortoise import __main__ as m

        with mock.patch("subprocess.run") as fake_run, \
             mock.patch("subprocess.Popen") as fake_popen, \
             mock.patch("tortoise.projection.FalkorProjection"), \
             mock.patch("tortoise.sdk.TortoiseSDK") as fake_sdk, \
             mock.patch("builtins.input", return_value="y"):
            fake_run.return_value.returncode = 0
            fake_run.return_value.stdout = str(repo_root)
            fake_sdk.return_value.status.return_value = {"counts": {"Point": 1}}
            rc = m._cmd_init(mock.Mock(path=None, cmd="init", yes=False, api_key=None))

        assert rc == 0
        assert fake_popen.called
        args = fake_popen.call_args.args[0]
        assert args[:3] == [sys.executable, "-m", "tortoise"]
        assert "--db" in args
        assert args[args.index("--db") + 1] == db_path
