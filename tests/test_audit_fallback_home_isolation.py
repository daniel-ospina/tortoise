"""#7816 — importing ``tortoise.hosted_api`` must not write the audit JSONL
fallback into the real ``$HOME``.

Two layers guard the invariant, and both are pinned here:

* the LIBRARY resolves the fallback path lazily (at write/replay time), so a
  module-level ``AuditLogger`` — ``hosted_api._audit_logger``, built at import
  — binds nothing and creates nothing on import; and
* the SUITE pins ``$TORTOISE_AUDIT_FALLBACK_DIR`` to the per-test tmp tree
  (``tests/conftest.py::_isolated_audit_fallback_dir``), so no test can reach
  the real ``$HOME`` even when it drives an audit write.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tortoise import audit_events
from tortoise.audit_events import AuditLogger

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _no_override(monkeypatch) -> None:
    """Drop the suite's fallback-dir pin so ``$HOME`` is the resolved default."""
    monkeypatch.delenv("TORTOISE_AUDIT_FALLBACK_DIR", raising=False)


def test_audit_logger_construction_creates_no_fallback_dir(tmp_path, monkeypatch):
    """#7816 indicator 2: construction must be side-effect-free."""
    _no_override(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    AuditLogger(dsn=None)
    assert not (tmp_path / ".tortoise").exists(), (
        "constructing an AuditLogger must not create $HOME/.tortoise")


def test_documented_default_resolves_to_home_tortoise(tmp_path, monkeypatch):
    """The default stays ``$HOME/.tortoise/audit_fallback.jsonl`` when used.

    Mirror of the analytics-writer pin
    (``tests/test_analytics_write_path_resolution.py::
    test_fallback_path_resolves_to_the_documented_default``): a lazy resolver
    must still resolve to the SAME documented file, from ``$HOME``.
    """
    _no_override(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    logger = AuditLogger(dsn=None)
    logger.append("org-1", None, "op")
    assert (tmp_path / ".tortoise" / "audit_fallback.jsonl").exists()


def test_fallback_resolves_home_at_write_time_not_construction(
        tmp_path, monkeypatch):
    """The module-level-logger shape: construct under HOME_A, write under HOME_B.

    Mutation that reds this test: reintroduce ``self._fallback_path =
    Path.home() / ...`` in ``AuditLogger.__init__``.
    """
    _no_override(monkeypatch)
    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    home_a.mkdir()
    home_b.mkdir()
    monkeypatch.setenv("HOME", str(home_a))
    logger = AuditLogger(dsn=None)  # constructed while HOME=home_a
    assert not (home_a / ".tortoise").exists()
    monkeypatch.setenv("HOME", str(home_b))
    logger.append("org-1", None, "op")
    assert (home_b / ".tortoise" / "audit_fallback.jsonl").exists()
    assert not (home_a / ".tortoise").exists(), (
        "the fallback path was frozen at construction time")


def test_env_override_beats_home(tmp_path, monkeypatch):
    """$TORTOISE_AUDIT_FALLBACK_DIR relocates the fallback off ``$HOME``."""
    home = tmp_path / "home"
    home.mkdir()
    override = tmp_path / "fallback"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TORTOISE_AUDIT_FALLBACK_DIR", str(override))
    AuditLogger(dsn=None).append("org-1", None, "op")
    assert (override / "audit_fallback.jsonl").exists()
    assert not (home / ".tortoise").exists()


def test_explicit_path_override_still_wins(tmp_path, monkeypatch):
    """Tests that pin ``_fallback_path`` keep working (back-compat)."""
    _no_override(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    pinned = tmp_path / "pinned"
    logger = AuditLogger(dsn=None)
    logger._fallback_path = pinned / "audit_fallback.jsonl"
    logger.append("org-1", None, "op")
    assert (pinned / "audit_fallback.jsonl").exists()


def test_suite_pins_the_fallback_dir_to_the_per_test_tmp_tree(tmp_path):
    """Guard (#7816 indicator 1): deleting the conftest fixture reddens here."""
    assert os.environ.get("TORTOISE_AUDIT_FALLBACK_DIR") == str(
        tmp_path / ".tortoise"), (
        "tests/conftest.py::_isolated_audit_fallback_dir must pin the audit "
        "fallback into the per-test tmp tree")


def test_importing_hosted_api_creates_no_home_tortoise(tmp_path):
    """End-to-end (#7816 indicator 2): the bare import writes nothing."""
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items()
           if k != "TORTOISE_AUDIT_FALLBACK_DIR"}
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", "import tortoise.hosted_api"],
        cwd=str(_REPO_ROOT), env=env,
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert not (home / ".tortoise").exists(), (
        "importing tortoise.hosted_api created $HOME/.tortoise")


# ── #7924 review P2: the READ half of the resolver, and a DROP is counted ──

def test_whitespace_only_env_override_is_treated_as_unset(tmp_path, monkeypatch):
    """#7924 review P2: `Path(" ")` must never become a CWD-relative base.

    Before the fix a whitespace-only override was truthy, so the fallback
    resolved to the RELATIVE path ``""/audit_fallback.jsonl`` and the event
    was written into the process CWD (and orphaned from ``$HOME``).
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TORTOISE_AUDIT_FALLBACK_DIR", "   ")
    monkeypatch.chdir(tmp_path)  # a relative fallback would land visibly HERE
    AuditLogger(dsn=None).append("org-1", None, "op")
    assert (home / ".tortoise" / "audit_fallback.jsonl").exists()
    assert not (tmp_path / " ").exists(), (
        "a whitespace-only override created a relative fallback dir")


def test_replay_resolves_env_and_does_not_create_the_dir(tmp_path, monkeypatch):
    """#7924 review P2: replay resolves lazily and creates nothing.

    The read half of ``_fallback_file()``: a successful-Postgres append
    replays the fallback WITHOUT a filesystem side effect, even when the
    fallback directory does not exist.
    """
    home = tmp_path / "home"
    home.mkdir()
    absent = tmp_path / "absent"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TORTOISE_AUDIT_FALLBACK_DIR", str(absent))
    AuditLogger(dsn=None)._replay_fallback()
    assert not absent.exists(), "replay must not create the fallback dir"
    assert not (home / ".tortoise").exists()


class _FakeCursor:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self._sink.append(params)


class _FakeConn:
    """Minimum surface `_replay_fallback` uses: `.cursor()` + execute."""

    def __init__(self):
        self.rows = []

    def cursor(self):
        return _FakeCursor(self.rows)


def test_replay_reads_and_truncates_the_env_resolved_file(tmp_path, monkeypatch):
    """#7924 review P2: replay must READ the same env-resolved path.

    Every pre-existing replay test pinned `_fallback_dir`/`_fallback_path`,
    taking the override branch; this pins the env branch of the READ half.
    """
    override = tmp_path / "fb"
    override.mkdir()
    fb = override / "audit_fallback.jsonl"
    fb.write_text(json.dumps({"id": "e1"}) + "\n")
    monkeypatch.setenv("TORTOISE_AUDIT_FALLBACK_DIR", str(override))
    logger = AuditLogger(dsn=None)
    logger._conn = _FakeConn()  # no real Postgres needed for the read leg
    logger._replay_fallback()
    assert logger._conn.rows, "replay did not INSERT the env-resolved file"
    assert fb.read_text() == "", "replayed events must be truncated"


def test_unresolvable_home_is_counted_as_a_drop(tmp_path, monkeypatch):
    """#7924 review P2: a DROP must be surfaced, not reported as success.

    ``Path.home()`` raises for a malformed ``$HOME``; the event reached no
    durable sink. The flow must still not break (non-fatal audit doctrine),
    but the loss must be visible in ``audit_fallback_drop_count()``.
    """
    _no_override(monkeypatch)
    monkeypatch.setenv("HOME", "~")  # Path.home() -> RuntimeError
    before = audit_events.audit_fallback_drop_count("unresolvable_path")
    logger = AuditLogger(dsn=None)
    assert logger._write_fallback({"id": "e1"}) is False
    assert audit_events.audit_fallback_drop_count("unresolvable_path") == before + 1
    assert audit_events.audit_fallback_drop_count() >= before + 1
    logger.append("org-1", None, "op")  # must NOT raise (non-fatal by design)
