"""Tests for `tortoise context` CLI (#7840 auto-inject) — memory digest for
agent session-start hooks.

Runnable with: .venv/bin/python -m pytest tests/test_cli_context.py -v
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture(autouse=True)
def _home_isolated(monkeypatch, tmp_path):
    """#1708 D9: never read the developer's real ~/.tortoise credentials (a
    global config would flip `tortoise context` to hosted mode), and never
    resolve a stray ./.tortoise file in the pytest CWD."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


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


def _delenv_falkordb(monkeypatch):
    """Isolate the legacy FALKORDB_* trio (#715 P2 conf 55): the honoring
    branch in _resolve_db_target only fires when these are explicitly set,
    so tests asserting embedded behavior must clear them explicitly."""
    for k in ("FALKORDB_HOST", "FALKORDB_PORT", "FALKORDB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)


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
        _delenv_falkordb(monkeypatch)
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

    def test_index_receives_rediss_uri(self, tmp_path, monkeypatch):
        """#715 P2 bug conf 65: rediss:// (documented TORTOISE_DB_URI scheme)
        must pass through _resolve_db_target as a URI — previously only
        docker:// was recognized, so rediss:// fell into the relative-path
        hard-reject with a misleading "Relative DB path 'rediss://...'
        rejected" error."""
        uri = "rediss://:pw@db.example.com:6379/tortoise"
        monkeypatch.setenv("TORTOISE_DB_URI", uri)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
        _delenv_falkordb(monkeypatch)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "README.md").write_text("# readme", encoding="utf-8")

        rc, idx = self._stub_onboard(repo_root)

        assert rc == 0
        assert idx.db == uri  # passed through unchanged, never path-mangled

    def test_redis_uri_passes_resolve_db_target(self, monkeypatch):
        """redis:// (plain) is routed as a URI too — not treated as a path."""
        from tortoise.__main__ import _resolve_db_target
        uri = "redis://:pw@db.example.com:6379/tortoise"
        monkeypatch.setenv("TORTOISE_DB_URI", uri)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
        _delenv_falkordb(monkeypatch)
        assert _resolve_db_target(None) == uri

    def test_init_no_silent_fallback_when_rediss_target_down(self, monkeypatch, capsys):
        """conf 60 for rediss://: configured URI unreachable -> init fails
        loudly (rc 1), never silently falls back to embedded (split graph)."""
        monkeypatch.setenv("TORTOISE_DB_URI", "rediss://@127.0.0.1:1/tortoise")
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
        _delenv_falkordb(monkeypatch)
        from tortoise import __main__ as m

        rc = m._cmd_init(mock.Mock(path=None, cmd="init", yes=True, api_key=None))

        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert rc == 1
        assert "Embedded mode initialized" not in out
        assert "Traceback" not in out

    def test_init_embedded_success_carries_eval_note(self, monkeypatch, capsys, tmp_path):
        """#942: `tortoise init` on embedded labels the success line
        single-writer eval-only — the entry point the flipped docs push
        toward must never look first-class."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "init.db"))
        _delenv_falkordb(monkeypatch)
        from tortoise import __main__ as m

        rc = m._cmd_init(mock.Mock(
            path=None, cmd="init", yes=True, api_key=None, no_index=True))

        out = capsys.readouterr().out
        assert rc == 0
        assert "Embedded mode initialized" in out
        assert "single-writer, eval only" in out

    def test_init_status_failure_never_prints_points_placeholder(self, monkeypatch, capsys, tmp_path):
        """#2210: when the post-init status read fails, `tortoise init` must
        not print 'Points: ?' (a placeholder the user can't act on) — it
        prints a real count on success and an actionable note on failure."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "init-fail.db"))
        _delenv_falkordb(monkeypatch)
        from tortoise import __main__ as m
        from tortoise.sdk import TortoiseSDK as _SDK

        def _boom(self):
            raise RuntimeError("status read failed")

        monkeypatch.setattr(_SDK, "status", _boom)
        rc = m._cmd_init(mock.Mock(
            path=None, cmd="init", yes=True, api_key=None, no_index=True))

        out = capsys.readouterr().out
        assert rc == 0
        assert "Graph: tortoise" in out
        assert "Points: ?" not in out
        assert "unavailable" in out  # actionable note, never a fake count

    def test_init_success_prints_real_point_count(self, monkeypatch, capsys, tmp_path):
        """#2210: on success `tortoise init` prints the REAL graph point
        count from sdk.status() (the welcome Point exists -> >= 1)."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "init-ok.db"))
        _delenv_falkordb(monkeypatch)
        from tortoise import __main__ as m

        rc = m._cmd_init(mock.Mock(
            path=None, cmd="init", yes=True, api_key=None, no_index=True))

        out = capsys.readouterr().out
        assert rc == 0
        assert "Points: ?" not in out
        assert re.search(r"Points: \d+", out), out

    def test_falkordb_env_honored_as_docker_uri(self, monkeypatch, capsys):
        """#715 P2 conf 55: legacy FALKORDB_* trio (still in .env.example,
        still probed by doctor) set without TORTOISE_DB_URI must be HONORED
        as a docker:// URI matching doctor semantics — not silently dropped,
        which would switch Docker->embedded on upgrade with no warning."""
        from tortoise.__main__ import _resolve_db_target
        monkeypatch.setenv("FALKORDB_HOST", "db.internal")
        monkeypatch.setenv("FALKORDB_PORT", "6380")
        monkeypatch.setenv("FALKORDB_PASSWORD", "secret")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)

        target = _resolve_db_target(None)

        assert target == "docker://:secret@db.internal:6380/tortoise"
        out = capsys.readouterr().out
        assert "legacy" in out and "FALKORDB" in out  # loud, not silent

    def test_falkordb_env_alone_password_defaults(self, monkeypatch):
        """Partial trio (password only) still honored — host/port default like
        _cmd_doctor (localhost:16379)."""
        from tortoise.__main__ import _resolve_db_target
        monkeypatch.setenv("FALKORDB_PASSWORD", "s3cret")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
        monkeypatch.delenv("FALKORDB_HOST", raising=False)
        monkeypatch.delenv("FALKORDB_PORT", raising=False)

        assert _resolve_db_target(None) == "docker://:s3cret@localhost:16379/tortoise"

    def test_falkordb_env_invalid_port_loud_error(self, monkeypatch):
        """Invalid FALKORDB_PORT raises ValueError with a clear message
        (matches pre-#705 init's loud failure, never a silent fallback)."""
        from tortoise.__main__ import _resolve_db_target
        monkeypatch.setenv("FALKORDB_HOST", "localhost")
        monkeypatch.setenv("FALKORDB_PORT", "not-a-port")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)

        with pytest.raises(ValueError, match="FALKORDB_PORT"):
            _resolve_db_target(None)

    def test_falkordb_env_unset_keeps_embedded_default(self, monkeypatch):
        """Clean env (no FALKORDB_*) keeps the embedded default — legacy
        honoring must NOT force docker://localhost on users with no config."""
        from tortoise.__main__ import _resolve_db_target
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
        _delenv_falkordb(monkeypatch)

        # Oracle = the import-time-cached constant production actually uses
        # (tortoise.config.DEFAULT_DB_PATH); a runtime os.path.expanduser
        # would read the D9-patched HOME and mismatch (#1708).
        from tortoise.config import DEFAULT_DB_PATH

        assert _resolve_db_target(None) == DEFAULT_DB_PATH

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
        _delenv_falkordb(monkeypatch)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "README.md").write_text("# readme", encoding="utf-8")

        rc, _ = self._stub_onboard(repo_root, index_rc=1)
        out = capsys.readouterr().out

        assert rc == 1
        assert "Index failed" in out
        assert "Onboarding complete." not in out

    def test_onboard_forwards_path_to_doctor(self, tmp_path, monkeypatch):
        """#720 conf 80: Step 5 must pass onboard's resolved target into
        doctor — `onboard --path X` health-checks X (same graph onboard
        wrote to), never the default graph."""
        db_path = str(tmp_path / "explicit.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "env.db"))
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "README.md").write_text("# readme", encoding="utf-8")

        from tortoise import __main__ as m

        seen: dict = {}
        with mock.patch.object(m, "_cmd_init", return_value=0), \
             mock.patch.object(m, "_cmd_demo", return_value=0), \
             mock.patch.object(m, "_cmd_doctor",
                               side_effect=lambda a: seen.update(doctor=a) or 0), \
             mock.patch("subprocess.run") as fake_run, \
             mock.patch.object(m, "_cmd_index_github",
                               side_effect=lambda a: seen.update(idx=a) or 0):
            fake_run.return_value.returncode = 0
            fake_run.return_value.stdout = str(repo_root)
            rc = m._cmd_onboard(mock.Mock(path=db_path, cmd="onboard"))

        assert rc == 0
        doctor = seen["doctor"]
        assert doctor.path == db_path  # --path forwarded, not the default
        assert doctor.db is None

    def test_onboard_forwards_uri_to_doctor(self, tmp_path, monkeypatch):
        """#720 conf 80: URI targets are forwarded to doctor via args.db."""
        uri = "docker://:pw@localhost:16379/tortoise"
        monkeypatch.setenv("TORTOISE_DB_URI", uri)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
        _delenv_falkordb(monkeypatch)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "README.md").write_text("# readme", encoding="utf-8")

        from tortoise import __main__ as m

        seen: dict = {}
        with mock.patch.object(m, "_cmd_init", return_value=0), \
             mock.patch.object(m, "_cmd_demo", return_value=0), \
             mock.patch.object(m, "_cmd_doctor",
                               side_effect=lambda a: seen.update(doctor=a) or 0), \
             mock.patch("subprocess.run") as fake_run, \
             mock.patch.object(m, "_cmd_index_github",
                               side_effect=lambda a: seen.update(idx=a) or 0):
            fake_run.return_value.returncode = 0
            fake_run.return_value.stdout = str(repo_root)
            rc = m._cmd_onboard(mock.Mock(path=None, cmd="onboard"))

        assert rc == 0
        doctor = seen["doctor"]
        assert doctor.db == uri
        assert doctor.path is None

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
        _delenv_falkordb(monkeypatch)
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
        _delenv_falkordb(monkeypatch)
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
        _delenv_falkordb(monkeypatch)
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


class TestCliSecurityAndIndex:
    """#715 P1/P2 fixes: password hygiene in warnings/argv, blank
    FALKORDB_* guard, single-index onboard, and URI routing parity for
    decide/list-kinds/list-sources."""

    @staticmethod
    def _fake_pipeline_module(monkeypatch):
        """The real tortoise.extraction_pipeline does not exist (#713) — for
        code paths AFTER the guarded import we need to fake it in sys.modules."""
        import types
        fake = types.ModuleType("tortoise.extraction_pipeline")

        class ExtractionPipeline:
            def __init__(self, *a, **k):
                pass

            def process_file(self, fp, api):
                return {"points": 1, "operators": 0, "documentKind": "md"}

        fake.ExtractionPipeline = ExtractionPipeline
        monkeypatch.setitem(sys.modules, "tortoise.extraction_pipeline", fake)

    # ── Fix 2 (P2): FALKORDB_* warning masks the password ──

    def test_falkordb_warning_masks_password(self, monkeypatch, capsys):
        """#715 P2 conf 95: the legacy FALKORDB_* warning must print a masked
        URI (docker://:***@host) — never the raw password."""
        from tortoise.__main__ import _resolve_db_target
        monkeypatch.setenv("FALKORDB_HOST", "db.internal")
        monkeypatch.setenv("FALKORDB_PORT", "6380")
        monkeypatch.setenv("FALKORDB_PASSWORD", "s3cret-hunter2")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)

        target = _resolve_db_target(None)

        # the RETURNED URI keeps the real password (used to connect)…
        assert target == "docker://:s3cret-hunter2@db.internal:6380/tortoise"
        # …but the printed warning never leaks it.
        out = capsys.readouterr().out
        assert ":***@" in out
        assert "s3cret-hunter2" not in out

    # ── Fix 5 (P2): blank FALKORDB_* counts as unset ──

    def test_falkordb_blank_values_treated_as_unset(self, monkeypatch):
        """#715 P2 conf 68: FALKORDB_PORT='' (and friends) count as UNSET —
        no int('') crash; a clean trio keeps the embedded default."""
        from tortoise.__main__ import _resolve_db_target
        monkeypatch.setenv("FALKORDB_HOST", "")
        monkeypatch.setenv("FALKORDB_PORT", "")
        monkeypatch.setenv("FALKORDB_PASSWORD", "")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)

        # Oracle = the import-time-cached constant production actually uses
        # (tortoise.config.DEFAULT_DB_PATH); a runtime os.path.expanduser
        # would read the D9-patched HOME and mismatch (#1708).
        from tortoise.config import DEFAULT_DB_PATH

        assert _resolve_db_target(None) == DEFAULT_DB_PATH

        # Mixed: host set, PORT blank → legacy honored with default port,
        # no int('') ValueError.
        monkeypatch.setenv("FALKORDB_HOST", "db.internal")
        assert _resolve_db_target(None) == "docker://:@db.internal:16379/tortoise"

    # ── Fix 3 (P2): password-bearing targets go via env, never argv ──

    def test_init_spawn_uri_password_uses_env_not_argv(self, tmp_path, monkeypatch):
        """#715 P2 conf 85: with a password-bearing TORTOISE_DB_URI, init's
        background index spawn must NOT carry --db in argv (ps leak) — the
        child gets TORTOISE_DB_URI via env instead."""
        uri = "docker://:hunter2@localhost:16379/tortoise"
        monkeypatch.setenv("TORTOISE_DB_URI", uri)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
        _delenv_falkordb(monkeypatch)
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
            rc = m._cmd_init(mock.Mock(path=None, cmd="init", yes=True,
                                       api_key=None))

        assert rc == 0
        assert fake_popen.called
        argv = fake_popen.call_args.args[0]
        assert "--db" not in argv  # secret never in argv
        env = fake_popen.call_args.kwargs.get("env") or {}
        assert env.get("TORTOISE_DB_URI") == uri

    # ── Fix 4 (P2): index github --background re-spawn carries the target ──

    def test_index_github_background_respawn_carries_db(self, tmp_path, monkeypatch):
        """#715 P2 conf 90: `index github --background` re-spawn must pass the
        resolved DB target to the child (was omitted entirely → child could
        index a different store)."""
        from tortoise import __main__ as m
        self._fake_pipeline_module(monkeypatch)
        db_path = str(tmp_path / "idx.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        _delenv_falkordb(monkeypatch)

        with mock.patch("subprocess.Popen") as fake_popen:
            rc = m._cmd_index_github(mock.Mock(
                url="https://github.com/daniel-ospina/tortoise",
                branch="main", background=True, db=None))

        assert rc == 0
        assert fake_popen.called
        argv = fake_popen.call_args.args[0]
        assert "--db" in argv
        assert argv[argv.index("--db") + 1] == db_path
        assert fake_popen.call_args.kwargs.get("env") is None

    def test_index_github_background_respawn_password_env(self, monkeypatch):
        """#715 P2 conf 85+90: password-bearing URI → the background re-spawn
        hands TORTOISE_DB_URI via env, never --db argv."""
        from tortoise import __main__ as m
        self._fake_pipeline_module(monkeypatch)
        uri = "redis://:hunter2@db.example.com:6379/tortoise"
        monkeypatch.setenv("TORTOISE_DB_URI", uri)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
        _delenv_falkordb(monkeypatch)

        with mock.patch("subprocess.Popen") as fake_popen:
            rc = m._cmd_index_github(mock.Mock(
                url="https://github.com/daniel-ospina/tortoise",
                branch="main", background=True, db=None))

        assert rc == 0
        assert fake_popen.called
        argv = fake_popen.call_args.args[0]
        assert "--db" not in argv
        env = fake_popen.call_args.kwargs.get("env") or {}
        assert env.get("TORTOISE_DB_URI") == uri

    def test_uri_has_password_requires_userinfo(self):
        """#715 P2 conf 75: a URI without `@` in its authority has no
        userinfo and therefore no password — docker://host:6379/db's ":6379"
        is a host:port, not a credential. Only userinfo-bearing URIs
        (:pw@ or user:pw@) classify as password-bearing."""
        from tortoise.__main__ import _uri_has_password

        # No `@` → no userinfo → NOT password-bearing (previously True: the
        # ":6379" host:port was misread as a credential, routing the target
        # to the env handoff instead of the documented --db argv branch).
        assert _uri_has_password("docker://host:6379/db") is False
        assert _uri_has_password("redis://db.example.com:6379/tortoise") is False
        assert _uri_has_password("rediss://db.example.com/tortoise") is False

        # Userinfo with a password → password-bearing.
        assert _uri_has_password("docker://:pass@host:6379/db") is True
        assert _uri_has_password("docker://user:pass@host") is True
        assert _uri_has_password("redis://:hunter2@db.example.com:6379/tortoise") is True

        # Userinfo WITHOUT a password → not password-bearing.
        assert _uri_has_password("docker://user@host:6379/db") is False

        # Non-URI targets are never password-bearing.
        assert _uri_has_password("/abs/path/tortoise.db") is False
        assert _uri_has_password("~/.tortoise/tortoise.db") is False

    # ── Fix 7 (P2): onboard indexes exactly once ──

    def test_onboard_indexes_once(self, tmp_path, monkeypatch):
        """#715 P2 conf 70: onboard must index exactly ONCE (inline step 3) —
        init is invoked with no_index=True so its background auto-index spawn
        never fires (previously: double index of the same repo)."""
        db_path = str(tmp_path / "onboard.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        _delenv_falkordb(monkeypatch)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "README.md").write_text("# readme", encoding="utf-8")

        from tortoise import __main__ as m
        seen = {"init_args": None, "index_calls": 0}

        def fake_init(a):
            seen["init_args"] = a
            return 0

        with mock.patch.object(m, "_cmd_init", side_effect=fake_init), \
             mock.patch.object(m, "_cmd_demo", return_value=0), \
             mock.patch.object(m, "_cmd_doctor", return_value=0), \
             mock.patch("subprocess.run") as fake_run, \
             mock.patch.object(
                 m, "_cmd_index_github",
                 side_effect=lambda a: seen.__setitem__(
                     "index_calls", seen["index_calls"] + 1) or 0):
            fake_run.return_value.returncode = 0  # git repo detected
            fake_run.return_value.stdout = str(repo_root)
            rc = m._cmd_onboard(mock.Mock(path=None, cmd="onboard"))

        assert rc == 0
        assert getattr(seen["init_args"], "no_index", False) is True
        assert seen["index_calls"] == 1

    # ── Fix 8 (P2): decide/list-kinds/list-sources route the shared target ──

    def test_list_kinds_routes_embedded_path(self, tmp_path, monkeypatch):
        """#715 P2 conf 75: list-kinds must NOT hardcode docker://localhost —
        a TORTOISE_DB_PATH target resolves to the embedded path."""
        from tortoise import __main__ as m
        from tortoise.sdk import TortoiseSDK
        db_path = str(tmp_path / "kinds.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        _delenv_falkordb(monkeypatch)

        with mock.patch.object(TortoiseSDK, "__init__", return_value=None), \
             mock.patch.object(TortoiseSDK, "list_pointkinds", return_value=[]), \
             mock.patch("tortoise.projection.FalkorProjection") as FP:
            rc = m._cmd_list_kinds(mock.Mock(cmd="list-kinds"))

        assert rc == 0
        assert FP.from_uri.called is False  # never the localhost hardcode
        assert FP.call_args.kwargs.get("path") == db_path

    def test_list_sources_routes_embedded_path(self, tmp_path, monkeypatch):
        """#715 P2 conf 75: list-sources routes the shared target too."""
        from tortoise import __main__ as m
        from tortoise.sdk import TortoiseSDK
        db_path = str(tmp_path / "sources.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        _delenv_falkordb(monkeypatch)

        with mock.patch.object(TortoiseSDK, "__init__", return_value=None), \
             mock.patch.object(TortoiseSDK, "list_sources", return_value=[]), \
             mock.patch("tortoise.projection.FalkorProjection") as FP:
            rc = m._cmd_list_sources(mock.Mock(cmd="list-sources"))

        assert rc == 0
        assert FP.from_uri.called is False
        assert FP.call_args.kwargs.get("path") == db_path

    def test_decide_routes_uri_target(self, monkeypatch):
        """#715 P2 conf 75: decide resolves --db or env through the shared
        target resolution — a TORTOISE_DB_URI is routed via from_uri, never
        the hardcoded docker://localhost default."""
        from tortoise import __main__ as m
        uri = "redis://:pw@db.example.com:6379/tortoise"
        monkeypatch.setenv("TORTOISE_DB_URI", uri)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
        _delenv_falkordb(monkeypatch)

        seen: dict = {}
        with mock.patch.object(m, "_projection_for",
                               side_effect=lambda t: seen.update(
                                   target=t) or mock.Mock()):
            rc = m._cmd_decide(mock.Mock(
                input=None, options='{"opt:a": "Option A"}',
                criteria=None, findings=None, edges=None, db=None))

        assert rc == 0
        assert seen.get("target") == uri

    # ── Fix 9 (P2): guarded inline index target + decide relative --db ──

    def test_index_github_inline_bad_falkordb_port_clean_error(self, tmp_path, monkeypatch, capsys):
        """#715 P2 conf 60: the inline `index github` path resolves the DB
        target through the shared guard — a bad FALKORDB_PORT with no --db
        surfaces as a clean CLI error, never a ValueError traceback."""
        import sys as _sys  # noqa: I001
        from tortoise import __main__ as m

        # The extraction pipeline does not exist in the real env — stub the
        # module so the command can reach the DB-target guard.
        fake_pipeline = mock.Mock()
        fake_pipeline.ExtractionPipeline = mock.Mock()
        monkeypatch.setitem(
            _sys.modules, "tortoise.extraction_pipeline", fake_pipeline)

        (tmp_path / "README.md").write_text("# Hi\n")
        monkeypatch.setenv("FALKORDB_HOST", "localhost")
        monkeypatch.setenv("FALKORDB_PORT", "not-an-int")
        monkeypatch.delenv("FALKORDB_PASSWORD", raising=False)
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)

        rc = m._cmd_index_github(mock.Mock(
            url=str(tmp_path), branch="main", background=False, db=None))

        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert rc == 1
        assert "Invalid DB target" in out
        assert "FALKORDB_PORT" in out
        assert "Traceback" not in out

    def test_decide_relative_db_clean_error(self, monkeypatch, capsys):
        """#715 P2 conf 55: `decide --db <relative>` is rejected by
        _projection_for's RELATIVE_PATH_ERROR — now INSIDE the guarded try,
        so it surfaces as a clean CLI error, never a traceback."""
        from tortoise import __main__ as m
        from tortoise.sdk import TortoiseSDK

        with mock.patch.object(TortoiseSDK, "__init__", return_value=None):
            rc = m._cmd_decide(mock.Mock(
                input=None, options='{"opt:a": "Option A"}',
                criteria=None, findings=None, edges=None,
                db="relative/path.db"))

        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert rc == 1
        assert "Invalid DB target" in out
        assert "relative" in out
        assert "Traceback" not in out

    # ── Fix 10 (P2): no credential at ANY non-doctor catch site ──

    @pytest.mark.parametrize("command", ["init", "list-kinds", "index", "decide"])
    def test_mask_applied_at_non_doctor_catch_sites(self, command, monkeypatch, capsys, tmp_path):
        """#720 P2 conf 55: the userinfo mask must be applied at every
        non-doctor catch site, not just doctor. A bolt:// (unsupported-
        scheme) URI with a credential falls into RELATIVE_PATH_ERROR with
        the RAW URI embedded in the message — whichever command surfaces
        it, the secret must never reach stdout/stderr and the masked URI
        (host/path intact) must appear instead."""
        from tortoise import __main__ as m

        uri = "bolt://user:sup3rsekrit@host:7687/g"
        (tmp_path / "README.md").write_text("# repo\n", encoding="utf-8")

        if command == "init":
            # catch site __main__.py:331 — explicit --path that is not a
            # supported-scheme URI → relative-path reject embeds the raw URI
            rc = m._cmd_init(mock.Mock(path=uri, cmd="init", yes=True,
                                       api_key=None))
        elif command == "list-kinds":
            # catch site __main__.py:1867 — env TORTOISE_DB_URI with an
            # unsupported scheme falls through to the path branch → reject
            monkeypatch.setenv("TORTOISE_DB_URI", uri)
            monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
            _delenv_falkordb(monkeypatch)
            rc = m._cmd_list_kinds(mock.Mock(cmd="list-kinds"))
        elif command == "index":
            # catch site __main__.py:1596 — standalone `index github` with
            # no --db resolves the shared env target → reject
            monkeypatch.setenv("TORTOISE_DB_URI", uri)
            monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
            _delenv_falkordb(monkeypatch)
            rc = m._cmd_index_github(mock.Mock(
                url=str(tmp_path), branch="main", background=False, db=None))
        elif command == "decide":
            # catch site __main__.py:1995 — decide --db with an unsupported-
            # scheme URI → _projection_for's hard-reject embeds the raw URI
            from tortoise.sdk import TortoiseSDK
            with mock.patch.object(TortoiseSDK, "__init__", return_value=None):
                rc = m._cmd_decide(mock.Mock(
                    input=None, options='{"opt:a": "Option A"}',
                    criteria=None, findings=None, edges=None, db=uri))

        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert rc == 1
        assert "Invalid DB" in out  # clean CLI error, actionable prefix
        assert "Traceback" not in out
        assert "sup3rsekrit" not in out  # credential never reaches output
        assert "bolt://:***@host:7687/g" in out  # masked, host/path intact

def test_index_sessions_constructor_failure_clean_error(monkeypatch, capsys, tmp_path):
    """Round-11: TortoiseSDK() constructor raise (FLY_APP_NAME production
    guard with empty URI) must produce a clean CLI error — no raw traceback,
    exit 1 — same contract as unreachable-graph."""
    import os  # noqa: F401, I001
    import sys as _sys
    import tortoise.__main__ as m
    monkeypatch.setenv("FLY_APP_NAME", "dummy")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setattr(_sys, "argv", ["tortoise", "index", "sessions"])
    rc = m.main()
    err = capsys.readouterr().err
    assert rc == 1
    assert "graph unreachable" in err
    assert "Traceback" not in err


class TestOnboardCountExcludesNonContentDirs:
    """#2201: the 'Found N markdown files' ANNOUNCE sites (init auto-index and
    the indexer itself — the onboard step-3 index runs the REAL indexer, its
    duplicate count line was removed) share the indexer's discovery — .venv
    and other non-content dirs must not inflate the announced count (the
    652-announced vs 527-walked divergence from the issue repro)."""

    @staticmethod
    def _repo_with_junk(tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# readme\n\ncontent\n")
        junk = repo / ".venv"
        junk.mkdir()
        (junk / "junk.md").write_text("# junk\n\nbody\n")
        return repo

    @staticmethod
    def _embedded_env(monkeypatch, tmp_path):
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "onboard.db"))
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        _delenv_falkordb(monkeypatch)

    def test_onboard_announce_count_excludes_venv(self, tmp_path, monkeypatch, capsys):
        """`tortoise onboard` step 3 runs the REAL indexer, which announces
        only the README (1 md file — the .venv/junk.md must not inflate the
        count). The README has no ## headers, so nothing is indexed; the
        all-empty run still exits 0 and onboard completes."""
        from tortoise import __main__ as m
        repo = self._repo_with_junk(tmp_path)
        self._embedded_env(monkeypatch, tmp_path)

        with mock.patch.object(m, "_cmd_init", return_value=0), \
             mock.patch.object(m, "_cmd_demo", return_value=0), \
             mock.patch.object(m, "_cmd_doctor", return_value=0), \
             mock.patch("subprocess.run") as fake_run:
            fake_run.return_value.returncode = 0  # git repo detected
            fake_run.return_value.stdout = str(repo)
            # #715 P2 (prescription): dispatch through the REAL argparse
            # parser, not a hand-built Namespace/bare Mock — those drift.
            rc = m.main(["onboard"])

        out = capsys.readouterr().out
        assert rc == 0, out
        # Exactly ONE announce: the real indexer's bare line. The two-space
        # pre-announce was removed from _cmd_onboard — a revert would print
        # "  Found N markdown files. Indexing…" again (count N, whether the
        # shared count of 1 or a raw-rglob count of 2) and fail the regex.
        assert out.count("Found 1 markdown files. Indexing…") == 1, out
        assert not re.search(
            r"^  Found \d+ markdown files\. Indexing…", out, re.M), out
        assert "junk.md" not in out, "non-content files must not be announced"
        assert "Onboarding complete." in out, out

    def test_init_autoindex_announce_count_excludes_venv(self, tmp_path,
                                                        monkeypatch, capsys):
        """`tortoise init --yes` auto-index announce counts the README only —
        same shared discovery as the indexer (#2201)."""
        from tortoise import __main__ as m
        repo = self._repo_with_junk(tmp_path)
        self._embedded_env(monkeypatch, tmp_path)

        with mock.patch("subprocess.run") as fake_run, \
             mock.patch("subprocess.Popen") as fake_popen, \
             mock.patch("tortoise.projection.FalkorProjection"), \
             mock.patch("tortoise.sdk.TortoiseSDK") as fake_sdk:
            fake_run.return_value.returncode = 0  # git repo detected
            fake_run.return_value.stdout = str(repo)
            fake_sdk.return_value.status.return_value = {"counts": {"Point": 1}}
            # #715 P2 (prescription): dispatch through the REAL argparse
            # parser, not a hand-built Namespace/bare Mock — those drift.
            rc = m.main(["init", "--yes"])

        out = capsys.readouterr().out
        assert rc == 0, out
        assert "Found 1 markdown files in this repo. Auto-indexing…" in out, out
        assert "junk.md" not in out, "non-content files must not be announced"
        assert fake_popen.called

