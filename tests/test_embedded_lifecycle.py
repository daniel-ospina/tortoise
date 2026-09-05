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
import signal as _signal
import subprocess as _subprocess
import sys
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
# directly. Epic #1647 P4 (Task 10): the list IS the embedded surface —
# the carve-out files (embedded-specific machinery: reaper, lifecycle,
# hygiene, concurrency, guard, migrations, backup-restore — they RUN
# embedded by design in the URI-unset carve-out job) plus the
# seam/helper/repro/fixture files whose construction IS the
# embedded-construction-under-test input. The 15 docker-lane files that
# left at P4 (the plan's 13: test_export_cli, test_import_endpoint,
# test_projection, test_indexes, test_ingest, test_supplementary,
# test_semantic_extractor, test_de2e1_entity_extraction, test_extractor_doc,
# test_extractor_priors, test_index_github_cli, test_m1,
# test_remove_context_migration — plus test_search_engine and
# test_backfill_embeddings_force, whose constructions are SERVER-mode by
# design) construct via the URI-aware redirect on the docker lane and never
# run embedded: their path= sites flip to the server under
# TORTOISE_DB_URI + TORTOISE_TEST_MODE, and the P4 URI-required enforcement
# (conftest session-start) fails any URI-less non-carve-out run before a
# test can construct embedded. New files must be added here deliberately —
# the source-scan test below fails otherwise (issue #1005 leak-rate
# regression guard). Generated from the 2026-08-12 audit (31 files); #1012
# conversion shrank it to 29; epic #1647 P4 shrank it to the 28 embedded-
# surface entries below: 11 of the plan's 13 "migrate out" files left for
# docker (test_export_cli and test_import_endpoint came back as embedded-
# file-contract files), plus test_search_engine and
# test_backfill_embeddings_force left (server-mode constructions / fixed
# lane-agnostically). The plan target "~21" predates the 6 seam test files
# Tasks 1-9 added and the 2 embedded-file-contract files that reality kept —
# divergence documented in the epic README.
RAW_EMBEDDED_ALLOWLIST = {
    "_embedded.py",  # seam/helper — raw constructions ARE the embedded-under-test input
    "fixtures/redis-guard/bad_relative_path.py",  # redis-guard fixture — embedded path resolution input
    "fixtures/redis-guard/good_absolute_path.py",  # redis-guard fixture — embedded path resolution input
    "repro/reproduce_redislite_leak.py",  # repro — deliberate embedded leak reproduction
    "test_backup_e2e.py",
    "test_config.py",
    "test_derived_names.py",  # epic #1647 Task 7 — the path= construction IS the derived-name-under-test input
    "test_divergence_conformance.py",  # epic #1647 E2E-8 — both legs construct the projection as the input
    "test_embedded_concurrency.py",
    "test_embedded_lifecycle.py",
    "test_export_cli.py",  # embedded-file-contract (P4 divergence from the plan's 13-migrate-out list): the `tortoise export` CLI reads a LOCAL DB file — the redirect would void the seed→CLI parity; a module-scoped autouse fixture pops TORTOISE_DB_URI so the file runs embedded on both lanes
    "test_index_cli.py",  # embedded-file-contract (CI-fix PR #1684): E2E-15 is a two-process embedded choreography (hook child owns the local db, parent reopens fresh after) — the redirect would split child-write vs parent-read; module-scoped autouse fixture pops TORTOISE_DB_URI
    "test_import_endpoint.py",  # embedded-file-contract (P4 divergence): the harness patches TortoiseSDK onto one local file (the hosted app's store) — the redirect would split seed vs app across stores; module-scoped autouse fixture pops TORTOISE_DB_URI
    "e2e/hosted/test_12_selfhost_migration.py",  # embedded by design (Task 10 Step 2 carve-out decision): the parity journey's source graph is a LOCAL file the `tortoise export` CLI subprocess reads — a redirect would silently flip it to the server and void the parity assertions; runs only in the URI-less hosted-e2e lane
    "test_embedded_lifecycle_fast_close.py",  # #1371 lifecycle-seam tests
    "test_flip_gate.py",
    "test_guard.py",
    "test_hard_reject.py",
    "test_hosted_backup.py",
    "test_migrate_db.py",
    "test_ops_safety.py",
    "test_pre_migration_safety.py",
    "test_projection_lifecycle.py",
    "test_reaper.py",
    "test_redis_guard.py",
    "test_redirect_seam.py",  # epic #1647 seam unit tests — construction IS the test input
    "test_round_trip_parity.py",  # epic #1647 E2E-1 — both legs construct the projection directly
    "test_tripwire.py",  # epic #1647 Task 4 tripwire probe — the path= probe construction IS the redirect-under-test input
    "test_wipe_server.py",  # epic #1647 E2E-2 — the projection construction IS the wipe-under-test input
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
    """Source-scan (epic #1647 P4, Task 10): the embedded construction
    surface is EXACTLY the carve-out + the deliberate seam/helper set.

    At P4 the allowlist = the files that can actually run embedded:
      - the carve-out files (embedded-specific machinery) — they RUN
        embedded by design in the URI-unset carve-out job, and
      - the seam/helper/repro/fixture files whose raw construction IS the
        embedded-construction-under-test input.
    Every OTHER test file runs on the docker lane: under
    TORTOISE_DB_URI + TORTOISE_TEST_MODE its path= constructions flip to
    the server via the URI-aware redirect (never spawning a redislite
    server), and the P4 enforcement (conftest session-start) fails any
    URI-less non-carve-out run before a test can construct embedded.

    What the scan still guards (the P4 leak vectors):
      - raw Redislite( — the guarded-subclass bypass, embedded in ANY lane
        (never allowed outside the lifecycle test that documents the rule);
      - a CARVE-OUT file that constructs raw without an allowlist entry —
        it runs embedded by design, so its constructions MUST be deliberate
        (a carve-out file missing from the allowlist reds).

    A file outside the allowlist that constructs FalkorDB(/FalkorProjection(
    is a migrated docker-lane file by construction (its constructions
    redirect) — not an embedded surface, and it cannot leak a redislite
    server in CI. Redislite constructions are never allowed (raw bypass of
    tortoise's guard). TortoiseSDK( is the public lifecycle-guarded API —
    allowed everywhere.

    NOTE (P4 review): the scan keys on source CONSTRUCTIONS, so a
    HOST-mode raw client (`FalkorDB(host=..., port=...)`) in a non-carve-out
    file is not flagged — it is server-mode by construction (the redirect
    only fires for explicit path=), it cannot spawn a redislite server, and
    on a URI-less tier-2 leg it fails LOUD (connection refused) rather than
    green-passing. The embedded-spawn vector the scan guards is the
    path=/Redislite( family, which the carve-out completeness + Redislite
    ban above cover.
    """
    tests_dir = Path(__file__).resolve().parent
    redislite_re = re.compile(r"\bRedislite\(")
    falkordb_re = re.compile(r"\bFalkorDB\(|\bFalkorProjection\(")
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    carve_out = set(TEST_NO_REDIRECT_STEMS)
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
        stem = Path(rel).stem
        if (falkordb_re.search(text) and stem in carve_out
                and rel not in RAW_EMBEDDED_ALLOWLIST):
            offenders_falkor.append(rel)
    # The allowlist itself must not carry stale entries (a file that no
    # longer exists — the shrink is a deliberate list edit; a stale path is
    # a bookkeeping error), and every NON-carve-out entry must carry a `#`
    # justification on its source line (a carved-out file needs no comment
    # — its membership IS the justification; a seam/helper/file-contract
    # entry must say why it constructs raw outside the carve-out).
    missing = [rel for rel in RAW_EMBEDDED_ALLOWLIST
               if not (tests_dir / rel).exists()]
    unjustified = [rel for rel in RAW_EMBEDDED_ALLOWLIST
                   if Path(rel).stem not in carve_out
                   and "#" not in _allowlist_line_comment(rel)]
    assert not missing, f"stale RAW_EMBEDDED_ALLOWLIST entries: {missing}"
    assert not unjustified, (
        f"non-carve-out allowlist entries need a `#` justification on "
        f"their line: {unjustified}")
    assert not offenders_redislite, (
        f"raw Redislite( constructions found (never allowed): "
        f"{offenders_redislite}")
    assert not offenders_falkor, (
        f"carve-out file constructs raw embedded clients without an "
        f"allowlist entry — add the file to RAW_EMBEDDED_ALLOWLIST with "
        f"justification: {offenders_falkor}")


_EMBEDDED_ALLOWLIST_SRC: str | None = None


def _allowlist_line_comment(rel: str) -> str:
    """The tail of the allowlist entry's source line ('' when the entry
    carries no `#` justification). Reads RAW_EMBEDDED_ALLOWLIST from source
    so the justification check stays in lockstep with the list."""
    global _EMBEDDED_ALLOWLIST_SRC
    if _EMBEDDED_ALLOWLIST_SRC is None:
        _EMBEDDED_ALLOWLIST_SRC = Path(__file__).read_text()
    m = re.search(rf'^\s*"{re.escape(rel)}"(.*)$', _EMBEDDED_ALLOWLIST_SRC, re.M)
    if not m:
        return ""
    return m.group(1)





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


def test_team_create_journals_minted_graph(tmp_path, monkeypatch):
    """#1686: team_create's minted team_{name} graph is journaled via the
    product-side seam (_journal_append_product) so the session-end sweep
    drops it — team_* graphs no longer accumulate on the docker.

    Carve-out file → explicit-path constructions stay embedded in BOTH
    lanes (exemption holds under a URI-set process); a temp journal env
    makes the membership assertion exact. team_create writes the registry
    Team node + mints team_{name} + the graph node, all on the embedded
    server."""
    from tests._embedded import _read_journal_file
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "team-create.graphs.jsonl"
    monkeypatch.setenv("TORTOISE_TEST_JOURNAL_FILE", str(journal))
    sdk = TortoiseSDK(str(tmp_path / "team-create.db"))
    try:
        res = sdk.team_create("journalled")
        assert res["graph_name"] == "team_journalled"
        assert "team_journalled" in _read_journal_file(str(journal)), \
            "team_create mint must be journaled (#1686)"
    finally:
        sdk.close()


# ── Issue #2203: terminating signals must close embedded servers ─────────
#
# Regression tests for the #2203 fix (signal guard in embedded_lifecycle +
# entry-point wiring): redislite's redis-server child daemonizes away from
# the python parent (ppid=1, own session), so SIGTERM/SIGHUP — whose default
# disposition kills the parent WITHOUT running atexit — orphaned it, and a
# piped-stdin child starts with SIGINT=SIG_IGN (CPython) so kill -INT was
# ignored entirely. Every test is process-level (kill-parent → child-gone)
# and pid-scoped (no global server-count asserts — safe under concurrent
# suite churn): spawn a child that owns an embedded server, signal it, and
# assert the child died AND its specific redis-server pid is gone.


_REPO_ROOT = str(Path(__file__).resolve().parent.parent)


def _child_env() -> dict:
    """Environment for embedded-child subprocesses: no docker URI (embedded
    path mode), no fast-atexit flag (real SAVE close semantics — the #1371
    fast path is a test-tree optimization we deliberately do not exercise
    here), repo root importable."""
    env = dict(os.environ)
    env.pop("TORTOISE_DB_URI", None)
    env.pop("TORTOISE_FAST_ATEXIT", None)
    env["PYTHONPATH"] = _REPO_ROOT
    return env


def _read_child_line(proc, prefix: str, timeout: float = 90) -> str:
    """Read the child's stdout until a line starting with ``prefix`` (or
    fail). Bounded readline via select so a hung child fails fast."""
    import select as _select
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        r, _, _ = _select.select([proc.stdout], [], [], min(5, deadline - time.time()))
        if not r:
            if proc.poll() is not None:
                raise AssertionError(
                    f"child died (rc={proc.returncode}) before printing {prefix!r}; "
                    f"saw: {''.join(seen)[-800:]}")
            continue
        line = proc.stdout.readline()
        if not line:
            raise AssertionError(
                f"child stdout closed before {prefix!r}; saw: {''.join(seen)[-800:]}")
        seen.append(line)
        if line.startswith(prefix):
            return line.strip()
    raise AssertionError(f"timed out waiting for {prefix!r}; saw: {''.join(seen)[-800:]}")


def _registry_redis_pid(db_path) -> int | None:
    """The redis-server pid recorded in redislite's ``<db>.settings``
    registry next to an embedded db file (None when not yet started)."""
    import json as _json
    settings = str(db_path) + ".settings"
    if not os.path.exists(settings):
        return None
    try:
        reg = _json.loads(Path(settings).read_text())
        pidfile = reg.get("pidfile")
        if pidfile and os.path.exists(pidfile):
            return int(Path(pidfile).read_text().strip())
    except Exception:
        return None  # registry mid-write -> retry
    return None


def _wait_for_registry_redis(db_path, proc, timeout: float = 90) -> int:
    """Poll the child's embedded-server registry until its redis pid is
    alive (the CLI/serve paths don't print the pid — the .settings registry
    is the deterministic signal the server started)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        pid = _registry_redis_pid(db_path)
        if pid is not None and _pid_alive(pid):
            return pid
        if proc.poll() is not None:
            out = ""
            if proc.stdout is not None:
                out = proc.stdout.read()
            raise AssertionError(
                f"child exited (rc={proc.returncode}) before its server was up:\n{out[-1200:]}")
        time.sleep(0.25)
    raise AssertionError(f"timed out waiting for embedded server at {db_path}.settings")


def _kill_quiet(pid) -> None:
    try:  # noqa: SIM105
        os.kill(pid, _signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


_SIGNAL_GUARD_CHILD = (
    "import sys, time, os, signal\n"
    "sys.path.insert(0, %(root)r)\n"
    "from tortoise.embedded_lifecycle import install_embedded_signal_cleanup\n"
    "from tortoise import FalkorDB\n"
    "install_embedded_signal_cleanup()\n"
    "db = FalkorDB(os.path.join(sys.argv[1], 'sig.db'))\n"
    "cli = getattr(db, 'client', db)\n"
    "print('REDIS_PID=%%s' %% cli.pid, flush=True)\n"
    "print('GUARD_READY', flush=True)\n"
    "time.sleep(600)\n"
)


@pytest.mark.parametrize("signum", [_signal.SIGTERM, _signal.SIGHUP])
def test_terminating_signal_closes_embedded_server(tmp_path, signum):
    """#2203 (indicator 4): kill-parent → child-gone for the terminating
    signals whose default disposition killed the parent WITHOUT atexit —
    SIGTERM (daemon/stdio/indexer kill) and SIGHUP (terminal/session death).
    Pre-fix the daemonized redis-server survived both (orphan)."""
    dbdir = tmp_path / f"sig-{signum}"
    dbdir.mkdir()
    proc = _subprocess.Popen(
        [sys.executable, "-c", _SIGNAL_GUARD_CHILD % {"root": _REPO_ROOT}, str(dbdir)],
        stdin=_subprocess.PIPE, stdout=_subprocess.PIPE,
        stderr=_subprocess.PIPE, text=True, env=_child_env(),
    )
    redis_pid = None
    try:
        line = _read_child_line(proc, "REDIS_PID=")
        redis_pid = int(line.split("=", 1)[1])
        assert _pid_alive(redis_pid), "child's embedded server should be up"
        os.kill(proc.pid, signum)
        try:
            proc.wait(timeout=45)
        except _subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail(f"child survived {_signal.Signals(signum).name} — "
                        "the #2203 guard did not terminate it")
        assert proc.returncode == -signum, (
            f"expected signal death ({-signum}), rc={proc.returncode}")
        assert _wait_server_dead(redis_pid, timeout=20), (
            f"child's redis-server survived its {_signal.Signals(signum).name}ed parent")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if redis_pid is not None and _pid_alive(redis_pid):
            _kill_quiet(redis_pid)


def test_sigint_ignored_at_startup_still_terminates_and_closes(tmp_path):
    """#2203 (indicator 3): a piped-stdin child starts with SIGINT=SIG_IGN
    (CPython, stdin not a tty) — pre-fix `kill -INT` was swallowed forever.
    The guard replaces the ignored disposition, so SIGINT terminates the
    process AND closes its embedded server."""
    dbdir = tmp_path / "sigint-ignored"
    dbdir.mkdir()
    script = (  # noqa: UP031 - child-script %-template (matches sibling tests)
        "import sys, time, os, signal\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)  # CPython non-tty startup\n"
        "sys.path.insert(0, %(root)r)\n"
        "from tortoise.embedded_lifecycle import install_embedded_signal_cleanup\n"
        "from tortoise import FalkorDB\n"
        "print('PRE_IGNORED=%%s' %% (signal.getsignal(signal.SIGINT) is signal.SIG_IGN), flush=True)\n"
        "install_embedded_signal_cleanup()\n"
        "print('POST_HANDLED=%%s' %% (signal.getsignal(signal.SIGINT) is not signal.SIG_IGN), flush=True)\n"
        "db = FalkorDB(os.path.join(sys.argv[1], 'sig.db'))\n"
        "cli = getattr(db, 'client', db)\n"
        "print('REDIS_PID=%%s' %% cli.pid, flush=True)\n"
        "print('GUARD_READY', flush=True)\n"
        "time.sleep(600)\n"
    ) % {"root": _REPO_ROOT}
    proc = _subprocess.Popen(
        [sys.executable, "-c", script, str(dbdir)],
        stdin=_subprocess.PIPE, stdout=_subprocess.PIPE,
        stderr=_subprocess.PIPE, text=True, env=_child_env(),
    )
    redis_pid = None
    try:
        pre = _read_child_line(proc, "PRE_IGNORED=")
        assert pre == "PRE_IGNORED=True", f"SIGINT not ignored at startup: {pre}"
        post = _read_child_line(proc, "POST_HANDLED=")
        assert post == "POST_HANDLED=True", (
            "the #2203 guard did not replace the ignored SIGINT disposition")
        line = _read_child_line(proc, "REDIS_PID=")
        redis_pid = int(line.split("=", 1)[1])
        assert _pid_alive(redis_pid)
        os.kill(proc.pid, _signal.SIGINT)
        try:
            proc.wait(timeout=45)
        except _subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("SIGINT was ignored (pre-#2203 behavior) — guard did not fire")
        assert proc.returncode == -_signal.SIGINT, f"rc={proc.returncode}"
        assert _wait_server_dead(redis_pid, timeout=20), (
            "redis-server survived its SIGINTed parent")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if redis_pid is not None and _pid_alive(redis_pid):
            _kill_quiet(redis_pid)


def _tortoise_cli_popen(argv, env, cwd, sigint_ignored: bool):
    """Spawn the real `tortoise` CLI (`python -m tortoise <argv...>`).

    sigint_ignored=True runs it through ``runpy`` with SIGINT preset to
    SIG_IGN — the harness condition (piped stdin) that made `kill -INT` a
    no-op pre-#2203 — without touching this process's disposition."""
    if sigint_ignored:
        code = (
            "import signal; signal.signal(signal.SIGINT, signal.SIG_IGN); "
            "import runpy; runpy.run_module('tortoise', run_name='__main__')"
        )
        return _subprocess.Popen(
            [sys.executable, "-c", code, *argv],
            stdin=_subprocess.PIPE, stdout=_subprocess.PIPE,
            stderr=_subprocess.STDOUT, text=True, env=env, cwd=cwd,
        )
    return _subprocess.Popen(
        [sys.executable, "-m", "tortoise", *argv],
        stdin=_subprocess.PIPE, stdout=_subprocess.PIPE,
        stderr=_subprocess.STDOUT, text=True, env=env, cwd=cwd,
    )


@pytest.mark.parametrize("signum", [_signal.SIGINT, _signal.SIGTERM])
def test_index_github_cli_signal_closes_embedded_server(tmp_path, signum):
    """#2203 (indicator 3): `tortoise index github` honors SIGINT (even when
    the process started with it ignored — piped stdin) and SIGTERM, and its
    embedded redis-server dies with it. The corpus contains a FIFO that
    sorts first, so the CLI blocks mid-index with its projection open —
    deterministic kill window (no race with a fast-completing index)."""
    sigint_ignored = signum == _signal.SIGINT
    work = tmp_path / f"idx-{signum}"
    corpus = work / "repo"
    corpus.mkdir(parents=True)
    db = work / "db" / "tortoise.db"
    db.parent.mkdir(parents=True)
    hold = corpus / "00000-hold.md"
    os.mkfifo(hold)  # blocks the index loop's read_text with proj open
    for i in range(3):
        (corpus / f"1000{i}.md").write_text(
            f"# File {i}\n\nSpeaker noted decision {i} is sound and final.\n")
    env = _child_env()
    proc = _tortoise_cli_popen(
        ["index", "github", str(corpus), "--db", str(db)],
        env=env, cwd=_REPO_ROOT, sigint_ignored=sigint_ignored,
    )
    redis_pid = None
    try:
        redis_pid = _wait_for_registry_redis(db, proc)
        assert _pid_alive(redis_pid), "indexer's embedded server should be up"
        os.kill(proc.pid, signum)
        try:
            proc.wait(timeout=45)
        except _subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail(
                f"`tortoise index github` ignored {_signal.Signals(signum).name} "
                "(pre-#2203 behavior)")
        assert proc.returncode == -signum, f"rc={proc.returncode}"
        assert _wait_server_dead(redis_pid, timeout=20), (
            "indexer's redis-server survived its killed parent")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if redis_pid is not None and _pid_alive(redis_pid):
            _kill_quiet(redis_pid)


def _stdio_serve_proc(tmp_path):
    """Spawn the real stdio MCP server (`python -m tortoise serve`) on an
    embedded TORTOISE_DB_PATH, drive the minimal MCP handshake + one
    DB-touching tool call (forces lazy projection creation → redis-server
    up), and return (proc, redis_pid). A failure mid-setup kills the child
    and its server before re-raising — the helper must never leak an orphan
    (the caller's try/finally only starts once it returns)."""
    import json as _json
    db = tmp_path / "stdio" / "t.db"
    db.parent.mkdir(parents=True)
    env = _child_env()
    env["TORTOISE_DB_PATH"] = str(db)
    proc = None
    redis_pid = None
    try:
        proc = _subprocess.Popen(
            [sys.executable, "-m", "tortoise", "serve"],
            stdin=_subprocess.PIPE, stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE, text=True, env=env, cwd=_REPO_ROOT,
        )

        def send(obj):
            proc.stdin.write(_json.dumps(obj) + "\n")
            proc.stdin.flush()

        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "lifecycle-test", "version": "1"}}})
        _read_child_line(proc, '{"jsonrpc"', timeout=60)  # initialize result
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "tortoise_get_point", "arguments": {"id": "missing-2203"}}})
        _read_child_line(proc, '{"jsonrpc"', timeout=60)  # tool result
        redis_pid = _wait_for_registry_redis(db, proc)
        assert _pid_alive(redis_pid), "stdio server's embedded server should be up"
        return proc, redis_pid
    except Exception:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if redis_pid is not None and _pid_alive(redis_pid):
            _kill_quiet(redis_pid)
        raise


def test_serve_stdio_sigterm_closes_embedded_server(tmp_path):
    """#2203 (indicator 2): SIGTERM on the stdio MCP server stops its
    embedded redis-server child (the harness kill after a client
    disconnect used to orphan it)."""
    proc, redis_pid = _stdio_serve_proc(tmp_path)
    try:
        os.kill(proc.pid, _signal.SIGTERM)
        try:
            proc.wait(timeout=45)
        except _subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("stdio server survived SIGTERM — guard did not fire")
        assert proc.returncode == -_signal.SIGTERM, f"rc={proc.returncode}"
        assert _wait_server_dead(redis_pid, timeout=20), (
            "stdio server's redis-server survived its SIGTERMed parent")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if _pid_alive(redis_pid):
            _kill_quiet(redis_pid)


def test_serve_stdio_client_disconnect_closes_embedded_server(tmp_path):
    """#2203 (indicator 2): client disconnect (stdin EOF) ends the stdio
    session and closes the embedded redis-server child deterministically."""
    proc, redis_pid = _stdio_serve_proc(tmp_path)
    try:
        proc.stdin.close()  # EOF = MCP client disconnect
        try:
            proc.wait(timeout=45)
        except _subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("stdio server did not exit on client disconnect")
        assert proc.returncode == 0, f"rc={proc.returncode}"
        assert _wait_server_dead(redis_pid, timeout=20), (
            "stdio server's redis-server survived client disconnect")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if _pid_alive(redis_pid):
            _kill_quiet(redis_pid)


def test_selfhost_daemon_sigterm_closes_embedded_server(tmp_path):
    """#2203 (indicator 1): SIGTERM on the selfhost daemon (docker CMD /
    `python -m tortoise.selfhost`) stops its embedded redis-server child.
    uvicorn's graceful shutdown restores the #2203 guard as the original
    SIGTERM handler and re-raises into it, which closes the server before
    the final death (verified composition)."""
    import socket as _socket
    import urllib.request as _urlreq
    env = _child_env()
    db = tmp_path / "daemon" / "tortoise.db"
    db.parent.mkdir(parents=True)
    env["TORTOISE_DB_PATH"] = str(db)
    # Pre-probe a free loopback port (uvicorn does not print its bound port
    # when stdout is not a tty).
    _s = _socket.socket()
    _s.bind(("127.0.0.1", 0))
    port = _s.getsockname()[1]
    _s.close()
    env["TORTOISE_PORT"] = str(port)
    proc = _subprocess.Popen(
        [sys.executable, "-m", "tortoise.selfhost"],
        stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT,
        text=True, env=env, cwd=_REPO_ROOT,
    )
    redis_pid = None
    try:
        # Probe /health until it answers (deep DB probe creates the SDK →
        # embedded server).
        deadline = time.time() + 90
        healthy = False
        while time.time() < deadline:
            if proc.poll() is not None:
                raise AssertionError(
                    f"daemon exited early rc={proc.returncode}: "
                    f"{proc.stdout.read()[-1500:]}")
            try:
                with _urlreq.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except Exception:
                pass
            time.sleep(0.4)
        assert healthy, f"daemon never became healthy on :{port}"
        redis_pid = _wait_for_registry_redis(db, proc)
        assert _pid_alive(redis_pid), "daemon's embedded server should be up"
        os.kill(proc.pid, _signal.SIGTERM)
        try:
            proc.wait(timeout=60)
        except _subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("daemon survived SIGTERM")
        assert _wait_server_dead(redis_pid, timeout=25), (
            "daemon's redis-server survived its SIGTERMed parent")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if redis_pid is not None and _pid_alive(redis_pid):
            _kill_quiet(redis_pid)
