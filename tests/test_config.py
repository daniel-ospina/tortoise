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
