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

import pytest

from tortoise import monitoring
from tortoise.audit_events import AuditLogger

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_ambient_audit_dsn(monkeypatch):
    """Neutralize an ambient ``TORTOISE_AUDIT_DSN`` for every test in the file.

    ``AuditLogger(dsn=None)`` reads ``TORTOISE_AUDIT_DSN`` from the
    environment and ``append()`` tries Postgres FIRST, so with that variable
    exported the JSONL fallback is never written and the tests assert on a
    file the code never touches — and a malformed DSN makes ``_connect``
    raise ``ValueError``. The sibling ``tests/test_audit_events.py`` clears
    the variable for the same reason. Function-scoped via ``monkeypatch``
    (never session-scoped), so it cannot perturb any other test.
    """
    monkeypatch.delenv("TORTOISE_AUDIT_DSN", raising=False)


def _no_override(monkeypatch) -> None:
    """Drop the suite's fallback-dir pin so ``$HOME`` is the resolved default."""
    monkeypatch.delenv("TORTOISE_AUDIT_FALLBACK_DIR", raising=False)
    monkeypatch.delenv("TORTOISE_AUDIT_DSN", raising=False)


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


def test_tilde_env_override_is_expanded_to_an_absolute_path(tmp_path, monkeypatch):
    """#7924 review P2: the relocation knob expands ``~`` like its peer knobs
    (``TORTOISE_PACKS_DIR`` / ``TORTOISE_DB_PATH``).

    Mutation that reds this test: drop the ``.expanduser()`` — a literal
    ``~/.audit`` is then RELATIVE, the absolute check raises, and EVERY
    fallback event is dropped for the whole duration of a Postgres outage.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TORTOISE_AUDIT_FALLBACK_DIR", "~/.audit")
    logger = AuditLogger(dsn=None)
    assert logger._fallback_file() == (
        tmp_path / ".audit" / "audit_fallback.jsonl")


def test_relative_override_seam_is_refused_not_materialized(tmp_path, monkeypatch):
    """#7924 review P2: the override legs are held to the same ABSOLUTE
    invariant as the env/``$HOME`` legs, so a relative ``_fallback_dir``
    cannot quietly mkdir a directory in the CWD and drop events there."""
    _no_override(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    logger = AuditLogger(dsn=None)
    logger._fallback_dir = Path("relative-fb")
    with pytest.raises(RuntimeError, match="is not an absolute"):
        logger._fallback_file()


def test_whitespace_only_knob_warns_once_not_per_call(tmp_path, monkeypatch, caplog):
    """#7924 review P2: the whitespace-only-knob warning is warn-ONCE.

    ``_fallback_file()`` runs on the audit hot path, so a warning emitted per
    call is one log line per audit event for the entire duration of an outage.
    Mutation that reds this test: drop the ``_WHITESPACE_OVERRIDE_WARNED``
    gate (back to an unconditional warning) — 5 calls then emit 5 records
    instead of 1.
    """
    _no_override(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TORTOISE_AUDIT_FALLBACK_DIR", "   ")
    from tortoise import audit_events as _ae
    monkeypatch.setattr(_ae, "_WHITESPACE_OVERRIDE_WARNED", False)
    logger = AuditLogger(dsn=None)
    with caplog.at_level("WARNING"):
        for _ in range(5):
            logger._fallback_file()
    hits = [r.getMessage() for r in caplog.records
            if "whitespace-only" in r.getMessage()]
    assert len(hits) == 1, f"expected exactly one warning, got {len(hits)}"
    # Whitespace-only is still treated as UNSET (never a CWD-relative base).
    assert logger._fallback_file() == (
        tmp_path / ".tortoise" / "audit_fallback.jsonl")


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
           if k not in ("TORTOISE_AUDIT_FALLBACK_DIR", "TORTOISE_AUDIT_DSN")}
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
    """#7924 review P2: `Path("   ")` must never become a CWD-relative base.

    A whitespace-only override is TRUTHY, so before the fix the fallback
    resolved to the RELATIVE path ``"   "/audit_fallback.jsonl`` and the event
    was written into the process CWD (orphaned from ``$HOME``). The CWD is
    snapshotted so the assertion cannot go vacuous on a mismatched name.
    """
    home = tmp_path / "home"
    home.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TORTOISE_AUDIT_FALLBACK_DIR", "   ")
    monkeypatch.chdir(cwd)
    before = {p.name for p in cwd.iterdir()}
    AuditLogger(dsn=None).append("org-1", None, "op")
    assert (home / ".tortoise" / "audit_fallback.jsonl").exists()
    assert {p.name for p in cwd.iterdir()} == before, (
        "a whitespace-only override created a relative fallback dir in the CWD")


def test_whitespace_only_home_is_refused_as_a_drop(tmp_path, monkeypatch):
    """#7924 review P2: a set-but-whitespace ``$HOME`` must be refused.

    ``$HOME`` is not normalized, so ``HOME="   "`` makes ``Path.home()``
    relative (``PosixPath('   ')``) — a CWD hazard one level down from the
    override leg. Since #7924 review round 3 it is refused UP FRONT, before
    ``Path.home()`` is consulted (``_refuse_unusable_home``); it must still be
    refused (and counted as a drop), never materialized under the CWD.

    The refusal MESSAGE is asserted on purpose (#7924 review round 3): a
    whitespace ``$HOME`` is ALREADY refused by the absolute-path check (its
    ``Path.home()`` is relative), so a drop is counted either way and an
    assertion on the drop alone does not exercise ``_refuse_unusable_home``.
    Mutation that reds this test: drop the ``home.strip()`` gate — the raise
    becomes "is not an absolute path" and the message match fails.
    """
    _no_override(monkeypatch)
    monkeypatch.setenv("HOME", "   ")
    monkeypatch.chdir(tmp_path)
    logger = AuditLogger(dsn=None)
    with pytest.raises(RuntimeError, match=r"\$HOME is set but empty"):
        logger._fallback_file()
    before = monitoring.audit_fallback_drop_counts().get("unresolvable_path", 0)
    logger.append("org-1", None, "op")  # must NOT raise
    after = monitoring.audit_fallback_drop_counts().get("unresolvable_path", 0)
    assert after == before + 1, "a non-absolute resolved base must be counted"
    assert not (tmp_path / "   ").exists(), (
        "a relative base must never be materialized under the CWD")


def test_empty_home_is_refused_as_a_drop(tmp_path, monkeypatch):
    """#7924 review round 2 P2-1: a SET-but-EMPTY ``$HOME`` must be refused.

    ``Path.home()`` returns ``/`` for ``HOME=""`` — which IS absolute — so
    the absolute-path guard passes and the fallback resolves to
    ``/.tortoise/audit_fallback.jsonl``, OUTSIDE ``$HOME``, in the filesystem
    root. In a root-writable container (the hosted shape) the mkdir+append
    then SUCCEEDS, so the drop counter is never incremented and the loss is
    silent. It is the whitespace-only override's misconfiguration one value
    over: it must be refused and counted in the PATH class, never misreported
    as ``write_failed``.

    Mutations that red this test: (a) drop the ``home.strip()`` guard — the
    resolved ``/…`` path is no longer refused, so no drop is counted; (b) count
    the refusal as ``write_failed`` — the second assertion reds.
    """
    _no_override(monkeypatch)
    monkeypatch.setenv("HOME", "")
    monkeypatch.chdir(tmp_path)
    logger = AuditLogger(dsn=None)
    with pytest.raises(RuntimeError, match=r"\$HOME is set but empty"):
        logger._fallback_file()
    counts = monitoring.audit_fallback_drop_counts()
    before_path = counts.get("unresolvable_path", 0)
    before_write = counts.get("write_failed", 0)
    logger.append("org-1", None, "op")  # must NOT raise
    counts = monitoring.audit_fallback_drop_counts()
    assert counts.get("unresolvable_path", 0) == before_path + 1, (
        "a set-but-empty $HOME must be counted as an unresolvable PATH drop")
    assert counts.get("write_failed", 0) == before_write, (
        "refusal is a path-resolution failure, not a write failure")


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
    but the loss must be visible in the monitoring drop counter.
    """
    _no_override(monkeypatch)
    monkeypatch.setenv("HOME", "~")  # Path.home() -> RuntimeError
    before = monitoring.audit_fallback_drop_counts().get("unresolvable_path", 0)
    logger = AuditLogger(dsn=None)
    logger.append("org-1", None, "op")  # must NOT raise (non-fatal by design)
    after = monitoring.audit_fallback_drop_counts().get("unresolvable_path", 0)
    assert after == before + 1, (
        "an audit event that reached no durable sink must be counted as a drop")
    # #7924 review P2: the drop must ride the surface the fleet serves
    # (the hosted app exposes no /metrics route), like journal_write_failures.
    snap = monitoring.metrics()
    assert snap["audit_fallback_drops"].get("unresolvable_path", 0) == after


# ── #7924 review round 3: the empty-$HOME escape is reachable TWICE ──────

def test_tilde_override_with_empty_home_is_refused(tmp_path, monkeypatch):
    """#7924 review round 3: the ADMISSION the round-2 guard left open.

    ``Path("~/.audit").expanduser()`` reads ``$HOME`` for a BARE leading
    ``~``; with ``HOME=""`` it expands to ``/.audit`` — an ABSOLUTE path — so
    the empty-``$HOME`` guard on the *default* leg and the absolute-path check
    BOTH pass, and in a root-writable container the mkdir+append then
    SUCCEEDS with no drop counted. The override leg must refuse it too, and
    count it as a PATH drop (never ``write_failed``).

    Mutations that red this test: (a) drop the ``_refuse_unusable_home()``
    call on the ``~``-override leg — no refusal, no drop; (b) count the
    refusal as ``write_failed`` — the third assertion reds.
    """
    _no_override(monkeypatch)
    monkeypatch.setenv("HOME", "")
    monkeypatch.setenv("TORTOISE_AUDIT_FALLBACK_DIR", "~/.audit")
    logger = AuditLogger(dsn=None)
    with pytest.raises(RuntimeError, match=r"\$HOME is set but empty"):
        logger._fallback_file()
    counts = monitoring.audit_fallback_drop_counts()
    before_path = counts.get("unresolvable_path", 0)
    before_write = counts.get("write_failed", 0)
    logger.append("org-1", None, "op")  # must NOT raise
    counts = monitoring.audit_fallback_drop_counts()
    assert counts.get("unresolvable_path", 0) == before_path + 1, (
        "a `~` override under an empty $HOME must be counted as a PATH drop")
    assert counts.get("write_failed", 0) == before_write, (
        "refusal is a path-resolution failure, not a write failure")


def test_filesystem_root_override_is_refused(tmp_path, monkeypatch):
    """#7924 review round 3: the resolved base must not BE the filesystem root.

    Defense in depth over every leg: an explicit
    ``TORTOISE_AUDIT_FALLBACK_DIR=/`` would otherwise write
    ``/audit_fallback.jsonl``. Refused (and counted), not materialized.
    """
    _no_override(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TORTOISE_AUDIT_FALLBACK_DIR", "/")
    logger = AuditLogger(dsn=None)
    with pytest.raises(RuntimeError, match="filesystem root"):
        logger._fallback_file()
    before = monitoring.audit_fallback_drop_counts().get("unresolvable_path", 0)
    logger.append("org-1", None, "op")  # must NOT raise
    after = monitoring.audit_fallback_drop_counts().get("unresolvable_path", 0)
    assert after == before + 1, "a root base must be counted as a path drop"


def test_dotdot_cannot_escape_to_the_root_on_any_leg(tmp_path, monkeypatch):
    """#7924 review round 3: the root refusal must survive ``..``.

    ``Path('/..') != Path('/')`` LEXICALLY, but the kernel resolves both to
    the filesystem root, so a lexical comparison accepted the path and
    ``mkdir``+``open`` wrote ``/audit_fallback.jsonl`` — the round-2
    silent-loss shape, one spelling over. It is reachable through the env
    knob AND the ``_fallback_path`` seam, so both legs are pinned here. The
    check resolves the directory (collapsing ``..`` and symlinks) first.

    Mutation that reds this test: compare ``base`` to ``Path(base.anchor)``
    lexically — ``/..`` is then accepted and no drop is counted.
    """
    _no_override(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TORTOISE_AUDIT_FALLBACK_DIR", "/..")
    logger = AuditLogger(dsn=None)
    with pytest.raises(RuntimeError, match="filesystem root"):
        logger._fallback_file()
    before = monitoring.audit_fallback_drop_counts().get("unresolvable_path", 0)
    logger.append("org-1", None, "op")  # must NOT raise
    after = monitoring.audit_fallback_drop_counts().get("unresolvable_path", 0)
    assert after == before + 1, "a `..` escape we refuse must be counted"
    # The same escape must be closed on the explicit-path seam.
    pinned = AuditLogger(dsn=None)
    pinned._fallback_path = Path("/../audit_fallback.jsonl")
    with pytest.raises(RuntimeError, match="filesystem root"):
        pinned._fallback_file()


def test_root_resolving_home_spellings_are_refused_and_counted(
        tmp_path, monkeypatch):
    """#7924 review round 4: ``$HOME`` is judged by its RESOLVED location.

    ``HOME=""`` was refused because ``Path.home()`` returns ``/``, but the
    refusal keyed on the literal string. ``HOME=/``, ``//``, ``/..`` and
    ``/tmp/../..`` are all NON-empty, and the artefact ``$HOME/.tortoise`` is
    not itself the root, so NEITHER guard saw them: each resolved to the SAME
    ``/.tortoise/audit_fallback.jsonl`` the empty spelling is refused for,
    with the mkdir+append succeeding and no drop counted. They are now refused
    and counted as ``unresolvable_path`` drops.

    Mutation that reds this test: drop the resolved-root branch of
    ``_refuse_unusable_home`` — ``HOME=/..`` is accepted and no drop counted.
    """
    _no_override(monkeypatch)
    for bad in ("/", "//", "/..", "/tmp/../.."):
        monkeypatch.setenv("HOME", bad)
        logger = AuditLogger(dsn=None)
        with pytest.raises(RuntimeError, match="filesystem root"):
            logger._fallback_file()
        before = monitoring.audit_fallback_drop_counts().get(
            "unresolvable_path", 0)
        logger.append("org-1", None, "op")  # must NOT raise
        after = monitoring.audit_fallback_drop_counts().get(
            "unresolvable_path", 0)
        assert after == before + 1, f"HOME={bad!r} must be a counted path drop"


def test_replay_resolution_failure_warns_once_not_per_successful_append(
        tmp_path, monkeypatch, caplog):
    """#7924 review round 3: the READ leg must not flood the log on the HEALTHY path.

    ``_replay_fallback()`` runs on every SUCCESSFUL Postgres write, so an
    unconditional ERROR there emits one line per audit event whenever the
    fallback path cannot be resolved — even though every one of those events
    WAS durably persisted, which makes the message read as a loss that did not
    happen. Warn once per process, like the whitespace-only-knob warning.

    Mutation that reds this test: drop the ``_REPLAY_RESOLUTION_WARNED`` gate
    — five successful appends then emit five ERROR records instead of one.
    """
    _no_override(monkeypatch)
    monkeypatch.setenv("HOME", "")
    from tortoise import audit_events as _ae
    monkeypatch.setattr(_ae, "_REPLAY_RESOLUTION_WARNED", False)
    logger = AuditLogger(dsn=None)
    logger._conn = _FakeConn()  # a healthy "Postgres" — every append succeeds
    with caplog.at_level("ERROR"):
        for _ in range(5):
            logger.append("org-1", None, "op")
    hits = [r.getMessage() for r in caplog.records
            if "could not inspect the audit fallback" in r.getMessage()]
    assert len(hits) == 1, f"expected exactly one warning, got {len(hits)}"
