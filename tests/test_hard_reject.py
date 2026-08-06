"""Hard-reject relative paths + escape hatch tests (plan Task 7).

Covers: relative path raises ValueError with 3 remedies; absolute passes;
escape hatch allows absolute non-canonical ONLY; relative ALWAYS rejected
even with escape hatch; empty-string and tilde-path edge cases; shared
RELATIVE_PATH_ERROR constant; mcp_server error surface.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest

from tortoise.config import RELATIVE_PATH_ERROR

pytest.importorskip("redislite")


def _tmp_db(name: str) -> str:
    d = tempfile.mkdtemp(prefix="tortoise-hardreject-")
    return os.path.join(d, name)


def test_relative_path_raises():
    """FalkorProjection('tortoise.db') raises ValueError."""
    from tortoise.projection import FalkorProjection
    with pytest.raises(ValueError):
        FalkorProjection("tortoise.db")


def test_absolute_path_passes():
    """Absolute path (incl. tempdirs) unaffected."""
    from tortoise.projection import FalkorProjection
    path = _tmp_db("abs.db")
    proj = FalkorProjection(path)
    proj.close()
    for suffix in (".db", ".db.settings"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def test_relative_path_raises_even_with_escape_hatch():
    """Escape hatch NEVER permits relative paths — the whole point of the fix."""
    from tortoise.projection import FalkorProjection
    with pytest.raises(ValueError):
        FalkorProjection("tortoise.db", allow_nonstandard_path=True)


def test_escape_hatch_allows_absolute_only():
    """Escape hatch allows non-standard ABSOLUTE paths only."""
    from tortoise.projection import FalkorProjection
    path = _tmp_db("nonstandard.db")
    proj = FalkorProjection(path, allow_nonstandard_path=True)
    proj.close()
    for suffix in (".db", ".db.settings"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def test_escape_hatch_env_var(monkeypatch):
    """TORTOISE_ALLOW_NONSTANDARD_PATH=1 env enables the escape hatch for
    absolute paths; relative still rejected."""
    from tortoise.projection import FalkorProjection
    monkeypatch.setenv("TORTOISE_ALLOW_NONSTANDARD_PATH", "1")
    path = _tmp_db("env-nonstandard.db")
    proj = FalkorProjection(path)  # absolute, allowed via env
    proj.close()
    for suffix in (".db", ".db.settings"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass
    with pytest.raises(ValueError):
        FalkorProjection("relative.db")  # env never permits relative


def test_empty_string_path_raises():
    """Empty-string path raises clear error."""
    from tortoise.projection import FalkorProjection
    with pytest.raises(ValueError):
        FalkorProjection("")


def test_tilde_path_treated_as_relative_raises(monkeypatch):
    """~/path (tilde, not expanded) is relative -> raises with hint."""
    from tortoise.projection import FalkorProjection
    # If ~ expands to absolute it passes; otherwise (relative-like) it must
    # raise. Force by setting HOME to something that makes the check fail.
    monkeypatch.setenv("HOME", tempfile.mkdtemp())
    with pytest.raises(ValueError):
        FalkorProjection("~/tortoise.db")


def test_error_message_lists_three_remedies():
    """ValueError message contains the 3 remedies."""
    from tortoise.projection import FalkorProjection
    try:
        FalkorProjection("tortoise.db")
    except ValueError as e:
        msg = str(e)
        assert "canonical path" in msg
        assert "absolute path" in msg
        assert "allow_nonstandard_path" in msg
        assert "Relative" in msg


def test_error_message_uses_shared_constant():
    """The hard-reject message is the shared RELATIVE_PATH_ERROR constant
    (call sites cannot drift)."""
    from tortoise.projection import FalkorProjection
    try:
        FalkorProjection("drift.db")
    except ValueError as e:
        # The message must contain the constant's substance
        assert str(e) == RELATIVE_PATH_ERROR.format(path="drift.db")


def test_sdk_normalizes_env_relative_to_absolute(monkeypatch):
    """The SDK's resolve_db_path() normalizes a relative TORTOISE_DB_PATH to
    absolute (so the hard-reject is not triggered for env-configured paths —
    only for DIRECT relative construction, which the other tests cover)."""
    from tortoise.sdk import TortoiseSDK
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", "./relative.db")
    sdk = TortoiseSDK()
    assert os.path.isabs(sdk._db_path), "SDK must normalize env path to absolute"
