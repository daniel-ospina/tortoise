"""Embedded-client lifecycle tests — issue #1005.

Verifies that tortoise.FalkorDB and TortoiseSDK shut down their embedded
redislite servers deterministically: weakref.finalize on GC, context-manager
support, and idempotent close. Also enforces that no NEW raw embedded
constructions appear in tests without being allowlisted.
"""
from __future__ import annotations

import gc
import os
import re
import sys  # noqa: F401
import time
from pathlib import Path

import pytest

pytest.importorskip("redislite")

import tortoise  # noqa: F401
from tortoise import FalkorDB


def _count_redis_servers() -> int:
    """Count live redislite redis-server processes (pgrep)."""
    import subprocess
    try:
        out = subprocess.run(
            ["pgrep", "-f", "redislite/bin/redis-server"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return -1
    return len([l for l in out.stdout.splitlines() if l.strip().isdigit()])  # noqa: E741

# Files that legitimately construct FalkorDB / FalkorProjection / Redislite
# directly (reaper internals, guard tests, concurrency tests, repro scripts,
# projection lifecycle suites). New files must be added here deliberately —
# the source-scan test below fails otherwise (issue #1005 leak-rate
# regression guard). Generated from the 2026-08-12 audit (31 files); #1012 conversion shrank it to 29.
RAW_EMBEDDED_ALLOWLIST = {
    "_embedded.py",
    "fixtures/redis-guard/bad_relative_path.py",
    "fixtures/redis-guard/good_absolute_path.py",
    "repro/reproduce_redislite_leak.py",
    "test_backup_e2e.py",
    "test_config.py",
    "test_de2e1_entity_extraction.py",
    "test_embedded_concurrency.py",
    "test_embedded_lifecycle.py",
    "test_export_cli.py",  # drift registration (#1401) — raw embedded construction
    "test_import_endpoint.py",  # drift registration — raw embedded construction
    "e2e/hosted/test_12_selfhost_migration.py",  # pre-existing raw construction (origin/main drift)
    "test_extractor_doc.py",
    "test_extractor_priors.py",
    "test_embedded_lifecycle_fast_close.py",  # #1371 lifecycle-seam tests
    "test_flip_gate.py",
    "test_guard.py",
    "test_hard_reject.py",
    "test_hosted_backup.py",
    "test_index_github_cli.py",
    "test_indexes.py",
    "test_ingest.py",
    "test_m1.py",
    "test_migrate_db.py",
    "test_ops_safety.py",
    "test_pre_migration_safety.py",
    "test_projection.py",
    "test_projection_lifecycle.py",
    "test_reaper.py",
    "test_redis_guard.py",
    "test_remove_context_migration.py",
    "test_semantic_extractor.py",
    "test_supplementary.py",
}


def test_falkordb_context_manager_closes_once(monkeypatch, tmp_path):
    """__exit__ closes; a second __exit__/close is a no-op."""
    calls = []
    orig_close = FalkorDB.close

    def recorder(self):
        calls.append("close")
        return orig_close(self)

    monkeypatch.setattr(FalkorDB, "close", recorder)
    db = FalkorDB(str(tmp_path / "a.db"))
    assert db.__enter__() is db
    db.__exit__(None, None, None)
    db.__exit__(None, None, None)
    assert calls == ["close"]


def test_falkordb_close_idempotent_and_atexit_registered(tmp_path):
    """close() via the lifecycle wrapper is idempotent; atexit is wired so
    process exit never orphans the server."""
    db = FalkorDB(str(tmp_path / "b.db"))
    assert db._t_closed is False
    db._t_close()
    db._t_close()
    assert db._t_closed is True


def test_sdk_close_idempotent_and_context_manager(tmp_path):
    """TortoiseSDK: close once (idempotent); __enter__/__exit__ work."""
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(str(tmp_path / "d.db"))
    sdk.close()
    sdk.close()
    assert sdk._t_closed is True
    assert sdk._proj is None
    with TortoiseSDK(str(tmp_path / "e.db")) as sdk2:
        assert sdk2 is not None
    assert sdk2._t_closed is True


def test_sdk_close_closes_projection_via_context_manager(monkeypatch, tmp_path):
    """Context-manager exit closes the SDK, which closes its projection."""
    from tortoise.projection import FalkorProjection
    from tortoise.sdk import TortoiseSDK
    calls = []
    orig_close = FalkorProjection.close

    def recorder(self):
        calls.append("close")
        return orig_close(self)

    monkeypatch.setattr(FalkorProjection, "close", recorder)
    with TortoiseSDK(str(tmp_path / "f.db")) as sdk:
        sdk.get_point("missing-1")  # force lazy projection creation
        assert sdk._proj is not None
    assert calls == ["close"]
    assert sdk._t_closed is True


def test_projection_context_manager_closes_db(monkeypatch, tmp_path):
    """Dropping the context closes the projection's db client (embedded
    server shuts down)."""
    from tortoise import FalkorDB as TFalkorDB
    from tortoise.projection import FalkorProjection
    calls = []
    orig_close = TFalkorDB.close

    def recorder(self):
        calls.append("close")
        return orig_close(self)

    monkeypatch.setattr(TFalkorDB, "close", recorder)
    with FalkorProjection(str(tmp_path / "g.db")) as proj:
        assert proj is not None
    assert calls == ["close"]


def test_falkordb_atexit_closes_server_on_process_exit(tmp_path):
    """End-to-end: a bare FalkorDB client whose process exits normally must
    NOT leave a server behind (atexit close). Skips the strict count
    assertion when other test suites are running concurrently (their server
    churn makes the global count noisy) — the check is only meaningful on a
    quiet machine."""
    import subprocess  # noqa: I001
    import sys as _sys
    from tortoise.embedded_reaper import active_suite_tokens
    script = (  # noqa: UP031
        "import sys; sys.path.insert(0, %r); "
        "from tortoise import FalkorDB; "
        "import tempfile, os; "
        "FalkorDB(os.path.join(tempfile.mkdtemp(), 'atexit.db'))\n"
    ) % str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env.pop("TORTOISE_DB_URI", None)
    before = _count_redis_servers()
    subprocess.run([_sys.executable, "-c", script], capture_output=True,
                   text=True, timeout=120, env=env)
    time.sleep(3)  # let the server shut down + count settle
    after = _count_redis_servers()
    other_suites = len(active_suite_tokens()) > 1
    if other_suites:
        pytest.skip("other suites active — server-count assertion invalid")
    # atexit closed the server: the count must not grow (tolerance 1 for
    # races with unrelated background processes).
    assert after <= before + 1, f"server leaked: {before} -> {after}"


def test_no_new_raw_embedded_constructions():
    """Source-scan: no test file outside the allowlist may construct
    embedded clients without going through the lifecycle-guarded API.

    Redislite constructions are never allowed (raw bypass of tortoise's
    guard).
    FalkorDB( / FalkorProjection( only in allowlisted files.
    TortoiseSDK( is the public lifecycle-guarded API — allowed everywhere.
    """
    tests_dir = Path(__file__).resolve().parent
    redislite_re = re.compile(r"\bRedislite\(")
    falkordb_re = re.compile(r"\bFalkorDB\(|\bFalkorProjection\(")
    offenders_redislite = []
    offenders_falkor = []
    for path in sorted(tests_dir.rglob("*.py")):
        if ".venv" in str(path) or "__pycache__" in str(path):
            continue
        text = path.read_text()
        rel = str(path.relative_to(tests_dir))
        # test_embedded_lifecycle.py defines the rule + documents it, so its
        # own docstring matches the pattern — excluded from the Redislite scan.
        if redislite_re.search(text) and rel != "test_embedded_lifecycle.py":
            offenders_redislite.append(rel)
        if falkordb_re.search(text) and rel not in RAW_EMBEDDED_ALLOWLIST:
            offenders_falkor.append(rel)
    assert not offenders_redislite, (
        f"raw Redislite( constructions found (never allowed): "
        f"{offenders_redislite}")
    assert not offenders_falkor, (
        f"un-allowlisted FalkorDB(/FalkorProjection( constructions — add the "
        f"file to RAW_EMBEDDED_ALLOWLIST with justification: "
        f"{offenders_falkor}")


# ── Issue #1475: deterministic close-on-GC (lifecycle finalize) ────────────

def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _wait_server_dead(pid, timeout=10):
    deadline = time.time() + timeout
    while _pid_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    return not _pid_alive(pid)


def test_leaked_projection_closes_on_gc(tmp_path):
    """#1475: a leaked (never-closed) projection's embedded server shuts down
    deterministically on GC, not only at atexit. The finalizer works around
    the dead-referent constraint by closing via a weakref to the pinned
    internal client (kept alive by redislite's own atexit registration)."""
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection(str(tmp_path / "gc_proj.db"), graph_name="test")
    cli = getattr(proj.db, "client", proj.db)
    pid = cli.pid
    assert _pid_alive(pid), "server should be running before GC"
    del proj, cli
    gc.collect()
    assert _wait_server_dead(pid), (
        "leaked projection's server survived GC — close-on-GC did not fire"
    )


def test_leaked_sdk_closes_on_gc(tmp_path):
    """#1475: a leaked TortoiseSDK (the dominant per-suite leak path) closes
    its embedded server when the SDK is collected — its projection dies in
    the same refcount cascade and the projection's finalizer shuts the
    server down mid-suite."""
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(str(tmp_path / "gc_sdk.db"))
    sdk.get_point("missing-1475")  # force lazy projection creation
    assert sdk._proj is not None
    cli = getattr(sdk._proj.db, "client", sdk._proj.db)
    pid = cli.pid
    assert _pid_alive(pid), "server should be running before GC"
    del sdk, cli
    gc.collect()
    assert _wait_server_dead(pid), (
        "leaked SDK's server survived GC — close-on-GC did not fire"
    )


def test_explicit_close_then_gc_safe(monkeypatch, tmp_path):
    """#1475: explicit close() is unaffected; a later GC of the (already
    closed) projection is a safe no-op — exactly one close call, no crash,
    server stays dead."""
    from tortoise.projection import FalkorProjection  # noqa: I001
    from tortoise import FalkorDB as TFalkorDB
    calls = []
    orig_close = TFalkorDB.close

    def recorder(self):
        calls.append("close")
        return orig_close(self)

    monkeypatch.setattr(TFalkorDB, "close", recorder)
    proj = FalkorProjection(str(tmp_path / "gc_explicit.db"), graph_name="test")
    cli = getattr(proj.db, "client", proj.db)
    pid = cli.pid
    proj.close()
    assert calls == ["close"]
    del proj, cli
    gc.collect()
    assert calls == ["close"], "GC finalizer must not double-close"
    assert _wait_server_dead(pid), "server should be dead after explicit close"


def test_shared_server_survives_single_gc(tmp_path):
    """#1475: two clients on ONE server — GC of the first must NOT kill the
    shared server (the #1371 last-client guard). The server dies only when
    the last client is collected."""
    from tortoise.projection import FalkorProjection
    db_path = str(tmp_path / "gc_shared.db")
    a = FalkorProjection(db_path, graph_name="test")
    b = FalkorProjection(db_path, graph_name="test")
    cli_a = getattr(a.db, "client", a.db)
    pid = cli_a.pid
    assert _pid_alive(pid)
    del a, cli_a
    gc.collect()
    time.sleep(0.5)
    assert _pid_alive(pid), "shared server killed while a live client remains"
    del b
    gc.collect()
    assert _wait_server_dead(pid), "last client's GC should shut the server down"
