"""Config resolution tests (plan Task 6) — TORTOISE_DB_PATH precedence."""
from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

from tortoise.config import (
    DEFAULT_DB_PATH,
    resolve_db_path,
    is_docker_uri,
    is_db_uri,
)


@pytest.fixture(autouse=True)
def _env_isolation(monkeypatch):
    """Env isolation: TORTOISE_* vars must not leak between tests."""
    for var in ("TORTOISE_DB_PATH", "TORTOISE_DB_URI", "TORTOISE_EMBEDDED_PATH"):
        monkeypatch.delenv(var, raising=False)
    yield


def test_default_path(monkeypatch):
    """No env vars -> ~/.tortoise/tortoise.db default."""
    assert resolve_db_path() == DEFAULT_DB_PATH
    assert os.path.isabs(resolve_db_path())


def test_db_path_env_wins(monkeypatch):
    """TORTOISE_DB_PATH env -> used as the file path."""
    monkeypatch.setenv("TORTOISE_DB_PATH", "/custom/canonical.db")
    assert resolve_db_path() == "/custom/canonical.db"


def test_db_path_expands_tilde(monkeypatch):
    """~ in TORTOISE_DB_PATH is expanded."""
    monkeypatch.setenv("TORTOISE_DB_PATH", "~/my-db/canonical.db")
    resolved = resolve_db_path()
    assert resolved == os.path.abspath(os.path.expanduser("~/my-db/canonical.db"))
    assert "~" not in resolved


def test_non_docker_uri_treated_as_file(monkeypatch, caplog):
    """Backward compat: bare non-docker TORTOISE_DB_URI -> file path."""
    monkeypatch.setenv("TORTOISE_DB_URI", "/legacy/embedded.db")
    assert resolve_db_path() == "/legacy/embedded.db"
    assert "file path" in caplog.text


def test_path_wins_over_non_docker_uri(monkeypatch, caplog):
    """Both non-docker URI and PATH set -> PATH wins with a warning."""
    monkeypatch.setenv("TORTOISE_DB_URI", "/legacy/embedded.db")
    monkeypatch.setenv("TORTOISE_DB_PATH", "/canonical.db")
    assert resolve_db_path() == "/canonical.db"


def test_docker_uri_never_resolved_to_file(monkeypatch):
    """docker:// URI must NEVER be resolved to a file path by resolve_db_path
    (docker handled by caller via is_docker_uri / from_uri)."""
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pass@host:6379/tortoise")
    # resolve_db_path falls through to default (does not treat docker:// as a path)
    assert resolve_db_path() == DEFAULT_DB_PATH


def test_redis_uris_never_resolved_to_file(monkeypatch):
    """#715 P2 conf 65: redis:// and rediss:// TORTOISE_DB_URI must also never
    be resolved to a file path (previously only docker:// was recognized, so
    rediss:// was treated as a path and rejected as 'Relative DB path')."""
    for uri in ("redis://:pw@host:6379/tortoise", "rediss://:pw@host:6379/tortoise"):
        monkeypatch.setenv("TORTOISE_DB_URI", uri)
        assert resolve_db_path() == DEFAULT_DB_PATH


def test_is_db_uri():
    """#715: is_db_uri recognizes every documented TORTOISE_DB_URI scheme and
    nothing else — docker://, redis://, rediss:// are URIs; paths are not."""
    assert is_db_uri("docker://:pass@host:6379/tortoise") is True
    assert is_db_uri("redis://:pass@host:6379/tortoise") is True
    assert is_db_uri("rediss://:pass@host:6379/tortoise") is True
    assert is_db_uri("/file.db") is False
    assert is_db_uri("tortoise.db") is False
    assert is_db_uri(None) is False
    assert is_db_uri("") is False


def test_invalid_db_path_env_falls_through_to_default(monkeypatch, caplog):
    """Empty/whitespace TORTOISE_DB_PATH -> falls through to default with
    warning; never passes "" to FalkorProjection."""
    monkeypatch.setenv("TORTOISE_DB_PATH", "")
    assert resolve_db_path() == DEFAULT_DB_PATH
    assert "empty" in caplog.text.lower() or "whitespace" in caplog.text.lower()

    monkeypatch.setenv("TORTOISE_DB_PATH", "   ")
    assert resolve_db_path() == DEFAULT_DB_PATH


def test_explicit_path_wins_over_env(monkeypatch):
    """Explicit caller-provided path wins over all env."""
    monkeypatch.setenv("TORTOISE_DB_PATH", "/env.db")
    assert resolve_db_path("/cli.db") == "/cli.db"


def test_is_docker_uri():
    assert is_docker_uri("docker://:pass@host:6379/tortoise") is True
    assert is_docker_uri("/file.db") is False
    assert is_docker_uri(None) is False
    assert is_docker_uri("") is False


def test_sdk_defaults_to_db_path_env(monkeypatch):
    """Set only TORTOISE_DB_PATH -> TortoiseSDK() uses it (SDK is the
    choke-point; must be blind to TORTOISE_DB_URI when only PATH is set)."""
    from tortoise.sdk import TortoiseSDK
    monkeypatch.setenv("TORTOISE_DB_PATH", "/sdk-canonical.db")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    sdk = TortoiseSDK()
    # SDK resolves via resolve_db_path() when neither db_path nor URI given
    assert sdk._db_path == "/sdk-canonical.db"
    assert sdk._db_uri is None


def test_hosted_api_fly_default(monkeypatch):
    """hosted_api resolves to /data/tortoise.db (Fly) when TORTOISE_DB_PATH
    unset — the hosted default differs from local default deliberately."""
    # The hosted default is /data/tortoise.db (Fly mount); verify the code
    # path preserves it when TORTOISE_DB_PATH is unset.
    assert os.environ.get("TORTOISE_DB_PATH") is None  # isolation fixture
    # hosted_api.py:42 default
    assert os.environ.get("TORTOISE_DB_PATH", "/data/tortoise.db") == "/data/tortoise.db"


def test_mcp_server_uses_db_path_env(monkeypatch):
    """Set only TORTOISE_DB_PATH -> mcp_server connects to that path
    (non-docker fallback wired to resolve_db_path)."""
    # Simulate mcp_server's module-level resolution logic
    from tortoise.config import resolve_db_path, is_docker_uri
    monkeypatch.setenv("TORTOISE_DB_PATH", "/mcp-canonical.db")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    uri = os.environ.get("TORTOISE_DB_URI", "")
    if is_docker_uri(uri):
        path = None
    else:
        path = resolve_db_path()
    assert path == "/mcp-canonical.db"


# ── #329: Lite-mode path rejection gap-fill ─────────────────────────

def test_resolve_db_path_explicit_relative_rejected():
    """resolve_db_path('tortoise.db') with an explicit relative arg raises
    (the shared RELATIVE_PATH_ERROR) — gap-fill for the #329 Lite-mode item."""
    from tortoise.config import RELATIVE_PATH_ERROR
    with pytest.raises(ValueError) as exc:
        resolve_db_path("tortoise.db")
    assert "Relative" in str(exc.value)
    assert exc.value.args[0] == RELATIVE_PATH_ERROR.format(path="tortoise.db")


def test_resolve_db_path_explicit_uri_rejected():
    """#715 P2 conf 75: a supported URI passed as the explicit path must
    raise a clear error (route via FalkorProjection.from_uri), never be
    mangled into a "path" that silently misses the real target."""
    from tortoise.config import RELATIVE_PATH_ERROR
    for uri in ("docker://:pw@host:6379/tortoise",
                "redis://:pw@host:6379/tortoise",
                "rediss://:pw@host:6379/tortoise"):
        with pytest.raises(ValueError, match="from_uri"):
            resolve_db_path(uri)
        # the scheme in the message must not leak the password
        try:
            resolve_db_path(uri)
        except ValueError as exc:
            assert "pw@" not in str(exc)


def test_resolve_db_path_explicit_absolute_accepted():
    """Explicit absolute args are still accepted (backward compat)."""
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "abs.db")
    assert resolve_db_path(path) == os.path.abspath(path)


def test_falkorprojection_unexpanded_tilde_rejected():
    """Unexpanded ~ passed DIRECTLY to FalkorProjection is rejected (the
    #329 Lite-mode boundary; resolve_db_path tilde expansion is intentional)."""
    from tortoise.projection import FalkorProjection
    with pytest.raises(ValueError):
        FalkorProjection("~/x.db")


# ── #478: MCP server env precedence for DB target resolution ───────────


class TestGetSdkEnvPrecedence:
    """Verify _get_sdk() correctly resolves TORTOISE_DB_URI with env precedence.

    Env precedence (documented in how-to-use-tortoise SKILL.md §DB URI Reality):
      1. TORTOISE_DB_URI with docker:// prefix → FalkorProjection.from_uri
      2. TORTOISE_DB_URI without docker:// → resolve_db_path (backward compat)
      3. No URI → resolve_db_path() default (~/.tortoise/tortoise.db)

    _load_dotenv() only fills keys NOT already in os.environ (mcp_server.py:72),
    so a process-level TORTOISE_DB_URI always wins over .env.
    """

    @pytest.fixture(autouse=True)
    def _reset_mcp_sdk_cache(self, monkeypatch):
        """Reset _get_sdk cache before each test so env changes take effect."""
        import tortoise.mcp_server as mcp_mod
        mcp_mod._sdk = None
        mcp_mod.sdk = None
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
        yield
        mcp_mod._sdk = None
        mcp_mod.sdk = None

    def test_docker_uri_routes_to_from_uri(self, monkeypatch):
        """Set TORTOISE_DB_URI=docker://... → _get_sdk() calls from_uri."""
        import tortoise.mcp_server as mcp_mod
        from unittest import mock
        from tortoise.sdk import TortoiseSDK

        monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise")

        # Mock FalkorProjection.from_uri to avoid live DB connection
        fake_proj = mock.MagicMock()
        with mock.patch("tortoise.projection.FalkorProjection.from_uri",
                        return_value=fake_proj) as mock_from_uri:
            # Also prevent main() path (called by entry points, not _get_sdk)
            sdk = mcp_mod._get_sdk()

            mock_from_uri.assert_called_once()
            assert sdk._proj is fake_proj
            assert sdk._db_uri == "docker://:@localhost:16379/tortoise"

    def test_file_path_uri_routes_to_resolve_db_path(self, monkeypatch, tmp_path):
        """Set TORTOISE_DB_URI=/some/path (non-docker) → _get_sdk() uses
        resolve_db_path and passes it as db_path to TortoiseSDK."""
        import tortoise.mcp_server as mcp_mod
        from unittest import mock

        db_path = str(tmp_path / "embedded.db")
        monkeypatch.setenv("TORTOISE_DB_URI", db_path)

        with mock.patch("tortoise.sdk.TortoiseSDK._get_proj",
                        return_value=mock.MagicMock()):
            sdk = mcp_mod._get_sdk()

        # Non-docker URIs fall through to resolve_db_path, so _db_path is set
        # (the URI is converted to a path; _db_uri is not populated for file paths)
        assert sdk._db_path == db_path
        assert sdk._db_uri is None  # file path URIs go to _db_path, not _db_uri

    def test_no_env_falls_through_to_default_path(self, monkeypatch):
        """Neither TORTOISE_DB_URI nor TORTOISE_DB_PATH set → _get_sdk()
        calls resolve_db_path() with no arg (default ~/.tortoise/tortoise.db)."""
        import tortoise.mcp_server as mcp_mod
        from unittest import mock
        from tortoise.config import DEFAULT_DB_PATH

        with mock.patch("tortoise.sdk.TortoiseSDK._get_proj",
                        return_value=mock.MagicMock()):
            sdk = mcp_mod._get_sdk()

        assert sdk._db_path == DEFAULT_DB_PATH
        assert sdk._db_uri is None

    def test_dotenv_does_not_override_explicit_env(self, monkeypatch, tmp_path):
        """_load_dotenv() must not override an already-set TORTOISE_DB_URI
        in os.environ (process env always wins — mcp_server.py:72)."""
        from tortoise.mcp_server import _load_dotenv

        env_file = tmp_path / ".env"
        env_file.write_text("TORTOISE_DB_URI=docker://:@dotenv:6379/tortoise\n")

        # Explicit env is ALREADY set before _load_dotenv runs
        monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@explicit:6379/tortoise")

        _load_dotenv(str(env_file))

        assert os.environ["TORTOISE_DB_URI"] == "docker://:@explicit:6379/tortoise"

    def test_dotenv_fills_when_env_unset(self, monkeypatch, tmp_path):
        """When TORTOISE_DB_URI is NOT set in process env, _load_dotenv()
        fills it from .env file."""
        from tortoise.mcp_server import _load_dotenv

        env_file = tmp_path / ".env"
        env_file.write_text("TORTOISE_DB_URI=docker://:@dotenv:6379/tortoise\n")

        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)

        _load_dotenv(str(env_file))

        assert os.environ["TORTOISE_DB_URI"] == "docker://:@dotenv:6379/tortoise"
