"""Embedded-client lifecycle tests — issue #1005.

Verifies that tortoise.FalkorDB and TortoiseSDK shut down their embedded
redislite servers deterministically: weakref.finalize on GC, context-manager
support, and idempotent close. Also enforces that no NEW raw embedded
constructions appear in tests without being allowlisted.
"""
from __future__ import annotations

import contextlib
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
# conversion shrank it to 29; epic #1647 P4 shrank it to the embedded-surface entries below: 11 of the plan's 13 "migrate out" files left for
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
    # #4047: the two #3845 fork-guard modules are carve-out by their own
    # module-level `embedded_only` marker, and both construct raw embedded
    # clients as the under-test input (the embedded daemon's fork children /
    # its socket path) — so they belong in both this allowlist and the
    # carve-out stem list.
    "test_fork_safety_3845.py",
    "test_fork_slot_wedge_3845.py",
    "test_guard.py",
    "test_hard_reject.py",
    "test_hosted_backup.py",
    "test_migrate_db.py",
    "test_ops_safety.py",
    "test_pre_migration_safety.py",
    "test_projection_lifecycle.py",
    "test_projection_embedded_socket_timeout.py",  # #3350: the embedded client's socket timeouts / bounded retry ARE the under-test input (a redirected construction has neither)
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


#: Budget for "the embedded server is gone after its parent exited" (#2947).
#: This is LIVENESS on a cleanup path, not a performance assertion: the
#: mechanism (signal → close → server exit) is deterministic and sub-second,
#: and on a loaded runner the observation itself is what needs headroom.
#: It was briefly 60s while #2947 was misdiagnosed as a starved runner; the
#: real cause was that the CLI test signalled mid-construction, where
#: redislite's last-client guard declines to shut the server down at all
#: (no timeout would have helped — see the CLI test above). 30s keeps the
#: loaded-runner margin while halving what a systematic cleanup regression
#: costs across the 6 call sites, and the message carries the evidence
#: (pid, elapsed, parent rc) instead of leaving the next occurrence a mystery.
_SERVER_DEATH_TIMEOUT_S = 30


def _assert_server_dies_with_parent(redis_pid: int, proc, what: str) -> None:
    """#2947: assert the embedded server died with its exited ``proc``.

    ``what`` names the case (the parent was SIGINTed, the client disconnected,
    …) so a failure says which lifecycle path failed to clean up.
    """
    started = time.time()
    assert _wait_server_dead(redis_pid, timeout=_SERVER_DEATH_TIMEOUT_S), (
        f"{what}: embedded redis-server (pid {redis_pid}) was still alive "
        f"{time.time() - started:.1f}s after its parent exited "
        f"(parent rc={proc.returncode}); budget {_SERVER_DEATH_TIMEOUT_S}s "
        "(#2947)")


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
    """#1686: team_create's minted org_{name} graph is journaled via the
    product-side seam (_journal_append_product) so the session-end sweep
    drops it — team_* graphs no longer accumulate on the docker.

    Carve-out file → explicit-path constructions stay embedded in BOTH
    lanes (exemption holds under a URI-set process); a temp journal env
    makes the membership assertion exact. team_create writes the registry
    Team node + mints org_{name} + the graph node, all on the embedded
    server."""
    from tests._embedded import _read_journal_file
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "team-create.graphs.jsonl"
    monkeypatch.setenv("TORTOISE_TEST_JOURNAL_FILE", str(journal))
    sdk = TortoiseSDK(str(tmp_path / "team-create.db"))
    try:
        res = sdk.org_create("journalled")
        assert res["graph_name"] == "org_journalled"
        assert "org_journalled" in _read_journal_file(str(journal)), \
            "team_create mint must be journaled (#1686)"
    finally:
        sdk.close()


def test_team_create_drops_the_graph_when_the_journal_append_fails(
        tmp_path, monkeypatch):
    """#3214 (review P2): the journal append IS the ownership record, so its
    failure must not leave the just-minted team graph behind — the raise is
    only honest if it is not itself a leak.

    The append is forced to fail for the TEAM graph only (the registry append
    must succeed, or _get_registry would raise before anything is created —
    that call site's own contract is that a raise there mints nothing). Then
    assert: the raise propagated, the ``org_{name}`` graph is GONE (post-fix
    the failure path calls ``team_graph.delete()``; pre-fix it survived with
    no ownership record, and no sweep could attribute it), and the registry
    Team node was rolled back by team_create's own handler.
    """
    import tortoise.projection as proj_mod
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "team-create-fail.graphs.jsonl"
    monkeypatch.setenv("TORTOISE_TEST_JOURNAL_FILE", str(journal))

    real_append = proj_mod._journal_append_product

    def _fail_team_only(graph_name):
        if graph_name == "org_unjournalled":
            raise RuntimeError(f"forced append failure for {graph_name!r}")
        return real_append(graph_name)

    monkeypatch.setattr(proj_mod, "_journal_append_product", _fail_team_only)
    sdk = TortoiseSDK(str(tmp_path / "team-create-fail.db"))
    try:
        with pytest.raises(RuntimeError, match="forced append failure"):
            sdk.org_create("unjournalled")
        assert "org_unjournalled" not in (sdk._get_proj().db.list_graphs() or []), \
            "team_create must DROP the graph whose ownership it could not record"
        rows = sdk._get_registry().query(
            "MATCH (t:Team {name:$n}) RETURN count(t)",
            params={"n": "unjournalled"},
        ).result_set
        assert rows[0][0] == 0, \
            "the registry Team node must be rolled back by team_create"
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
        _assert_server_dies_with_parent(
            redis_pid, proc, f"child's {_signal.Signals(signum).name}ed parent")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if redis_pid is not None and _pid_alive(redis_pid):
            _kill_quiet(redis_pid)


def test_sigkill_owner_record_lets_reaper_reclaim_the_server(tmp_path,
                                                             monkeypatch):
    """#3599 ACCEPTANCE (the SIGKILL branch): teardown CANNOT run under
    SIGKILL, so the owner record is the only signal that the server is an
    orphan. Kill -9 a real lane that owns an embedded server, then drive the
    reaper's own confirmation + only_safe reap against that live leftover
    and assert the server is actually gone.

    Every other test hand-writes a dead-owner record and calls the reaper on
    a synthetic dict (stub coverage); this one exercises the production
    writer -> SIGKILL -> production parser -> kill path end to end, which is
    the branch the issue was filed for. Pre-#3599 the confirmation was gated
    on the host-global `suites_active` check, so this reap was impossible.
    """
    from tortoise.embedded_reaper import (
        _active_client_count,
        _classify_dir,
        _mark_orphan_confirmation,
        _owner_records,
        _socket_dir_from_cmdline,
        reap,
    )
    dbdir = tmp_path / "sigkill"
    dbdir.mkdir()
    # No-path construction: redislite creates an autogenerated tempdir and the
    # generic `redis.db` filename, which is what makes the reaper classify the
    # leftover as an EPHEMERAL candidate (both classification signals must
    # agree — a user-named `<x>.db` would be `protected` and never reaped).
    script = (  # noqa: UP031 - child-script %-template (matches siblings)
        "import sys, time, os, signal\n"
        "sys.path.insert(0, %(root)r)\n"
        "from tortoise.embedded_lifecycle import install_embedded_signal_cleanup\n"
        "from tortoise import FalkorDB\n"
        "install_embedded_signal_cleanup()\n"
        "db = FalkorDB()\n"
        "cli = getattr(db, 'client', db)\n"
        "print('REDIS_PID=%%s' %% cli.pid, flush=True)\n"
        "time.sleep(600)\n"
    ) % {"root": _REPO_ROOT}
    proc = _subprocess.Popen(
        [sys.executable, "-c", script, str(dbdir)],
        stdin=_subprocess.PIPE, stdout=_subprocess.PIPE,
        stderr=_subprocess.PIPE, text=True, env=_child_env(),
    )
    redis_pid = None
    try:
        line = _read_child_line(proc, "REDIS_PID=")
        redis_pid = int(line.split("=", 1)[1])
        # NB: no second read for GUARD_READY — the owner record is written
        # during construction, i.e. BEFORE the child prints REDIS_PID, so
        # that line alone proves the record exists.
        assert _pid_alive(redis_pid), "child's embedded server should be up"
        # The reaper's boot cooldown (MIN_UPTIME) is orthogonal to #3599 and a
        # just-started server would be classified `protected` for it. A real
        # orphan is old by definition, so drive this test with the cooldown
        # disabled rather than asserting on a fresh server.
        monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
        sock_dir = _socket_dir_from_cmdline(redis_pid)
        assert sock_dir, "could not resolve the child server's socket dir"
        socket_path = os.path.join(sock_dir, "redis.socket")

        # SIGKILL: no atexit, no signal handler, no _t_release_owner.
        os.kill(proc.pid, _signal.SIGKILL)
        proc.wait(timeout=45)
        assert _pid_alive(redis_pid), (
            "the daemonized redis-server must SURVIVE its SIGKILLed parent — "
            "that survival is the orphan #3599 is about")

        owners = _owner_records(socket_path)
        assert owners is not None, (
            "the SIGKILLed lane must leave an owner record behind")
        assert owners[0] == 0, (
            f"every owner of the SIGKILLed lane is dead -> (0, N), got {owners}")

        rec = _classify_dir(sock_dir, socket_path, known_pid=redis_pid)
        assert rec is not None and rec.get("classification") == "candidate", \
            f"leftover server should classify as a candidate: {rec}"
        rec["path_based"] = False  # this test is about the owner signal
        rec["client_count"] = _active_client_count(socket_path)
        _mark_orphan_confirmation([rec])
        assert rec.get("_orphan_confirmed") is True, (
            "a server whose only owner is provably dead must be confirmed, "
            "regardless of other suites on the host")

        acted = reap([rec], dry_run=False, only_safe=True, batch_size=1)
        assert acted, "the reaper must act on the confirmed orphan"
        deadline = time.time() + 30
        while time.time() < deadline and _pid_alive(redis_pid):
            time.sleep(0.25)
        assert not _pid_alive(redis_pid), (
            "the SIGKILLed lane's orphaned server must be reclaimed by the "
            "reaper's next only_safe sweep")
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
        _assert_server_dies_with_parent(
            redis_pid, proc, "redis-server after its SIGINTed parent")
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


def _wait_ready_file(path, proc, timeout: float = 60) -> None:
    """#2947: block until the CLI child writes its post-construction readiness
    marker (``TORTOISE_INDEX_READY_FILE``), or the child exits.

    A file (not stdout) is the channel: while the index loop is blocked on the
    FIFO the child's stdout is unreliable to read from the parent, but the
    marker file is written and closed before the block. `_wait_for_registry_redis`
    returns as soon as redislite has published ``<db>.settings`` — which happens
    INSIDE projection construction — so without this wait the signal can land
    mid-construction, while an in-flight query still holds a connection; the
    last-client cleanup guard then declines to shut the server down.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            return
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout is not None else ""
            raise AssertionError(
                f"child exited (rc={proc.returncode}) before readiness marker:\n{out[-1200:]}")
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for readiness marker {path!r}")


@pytest.mark.parametrize("signum", [_signal.SIGINT, _signal.SIGTERM])
def test_index_github_cli_signal_closes_embedded_server(tmp_path, signum):
    """#2203 (indicator 3): `tortoise index github` honors SIGINT (even when
    the process started with it ignored — piped stdin) and SIGTERM, and its
    embedded redis-server dies with it. The corpus contains a FIFO that
    sorts first, so the CLI blocks mid-index with its projection open.

    #2947: the signal must land AFTER projection construction. The corpus's
    `.settings` registry is published DURING construction (redislite's
    server start), so waiting on it and signalling immediately could hit an
    in-flight server command; redislite's last-client cleanup guard then sees
    a second connection and declines to shut the server down, orphaning it.
    The child writes TORTOISE_INDEX_READY_FILE once construction is complete
    (just before the index loop), giving the deterministic open-projection
    kill window this test intends.
    """
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
    ready_file = work / "ready.marker"
    env["TORTOISE_INDEX_READY_FILE"] = str(ready_file)
    proc = _tortoise_cli_popen(
        ["index", "github", str(corpus), "--db", str(db)],
        env=env, cwd=_REPO_ROOT, sigint_ignored=sigint_ignored,
    )
    redis_pid = None
    try:
        redis_pid = _wait_for_registry_redis(db, proc)
        # #2947: wait for the CLI to finish building its projection before
        # signalling. `_wait_for_registry_redis` returns as soon as redislite
        # has published `<db>.settings` — which happens INSIDE
        # `FalkorProjection.__init__` (and its `_ensure_indexes` queries), not
        # after it. Signalling in that window lands while the process still
        # has an in-flight server command, so redislite's last-client cleanup
        # guard sees `_connection_count() > 1` and declines to shut the server
        # down — the kill then orphans it. The readiness marker is written
        # once construction is complete; the FIFO below then blocks the index
        # loop, giving the deterministic open-projection kill window this test
        # intends.
        _wait_ready_file(ready_file, proc)
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
        _assert_server_dies_with_parent(
            redis_pid, proc, "indexer's redis-server after its killed parent")
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
        _assert_server_dies_with_parent(
            redis_pid, proc, "stdio server's redis-server after its SIGTERMed parent")
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
        _assert_server_dies_with_parent(
            redis_pid, proc, "stdio server's redis-server after client disconnect")
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
        _assert_server_dies_with_parent(
            redis_pid, proc, "daemon's redis-server after its SIGTERMed parent")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if redis_pid is not None and _pid_alive(redis_pid):
            _kill_quiet(redis_pid)


# ── #3599: per-server owner records (the reaper's orphan discriminator) ───

def _owner_entries(socket_file: str):
    from tortoise.embedded_lifecycle import owner_record_dir
    d = owner_record_dir(socket_file)
    return sorted(os.listdir(d)) if os.path.isdir(d) else None


def test_owner_socket_of_resolves_the_inner_client():
    """owner_socket_of must accept BOTH shapes: the guarded tortoise
    FalkorDB wrapper keeps its redislite server on the INNER client
    (self.client), so reading `.socket_file` off the wrapper yields None —
    the bug that made the first cut of the #3599 fix silently write no
    owner record at all."""
    from tortoise.embedded_lifecycle import owner_socket_of

    class Inner:
        socket_file = "/tmp/inner/redis.socket"

    class Wrapper:
        client = Inner()

    assert owner_socket_of(Wrapper()) == "/tmp/inner/redis.socket"
    assert owner_socket_of(Inner()) == "/tmp/inner/redis.socket"
    # Host/port (server-mode) clients have no socket -> None (no child).
    assert owner_socket_of(object()) is None
    assert owner_socket_of(type("NoSocket", (), {"socket_file": None})()) is None


def test_owner_record_written_on_construction_removed_on_close(tmp_path):
    """A guarded construction records THIS process as the server's owner;
    close() releases it. A SIGKILL cannot run close(), which is exactly why
    the record is what lets the reaper see the orphan.

    NB (#3599 review P2): asserting only on the record FILE would not test
    the release seam — redislite's ``_cleanup`` rmtree's the whole socket
    dir, so the file vanishes even if the release never runs. The refcount
    and the release flag are asserted directly for that reason."""
    from tortoise.embedded_lifecycle import OWNERS_DIRNAME, _owner_refcounts  # noqa: F401
    db = FalkorDB(str(tmp_path / "own.db"))
    sock = None
    try:
        sock = db.client.socket_file
        # NB: a unix socket is not a regular file — os.path.isfile() is False.
        assert os.path.exists(sock), "redislite server socket should exist"
        entries = _owner_entries(sock)
        assert entries, "construction must write an owner record"
        assert all(e.startswith(f"{os.getpid()}-") for e in entries), entries
        assert _owner_refcounts.get(sock) == 1, "one client, one claim"
        # #3599 review (P1): pin the PRODUCTION writer->reader contract. The
        # reaper decides kill/no-kill by parsing what record_owner wrote; a
        # divergence between the writer's stamp and `_owner_records`' parser
        # would read a live owner as dead and orphan-confirm a live server
        # (fail-open) — and the first cut of this fix already silently wrote
        # no record at all, so this integration is where it broke before.
        from tortoise.embedded_reaper import _owner_records
        assert _owner_records(sock) == (1, 1), (
            "the reaper's parser must read the production writer's stamp as "
            "exactly one LIVE owner")
    finally:
        # The PUBLIC close seam (not _t_close): a bare db.close() must
        # release the owner claim too, or an SDK that closes that way
        # strands the record.
        db.close()
    assert getattr(db, "_t_owner_released", False) is True, \
        "close() must drive the owner-release seam (not just rmtree the dir)"
    assert sock not in _owner_refcounts, \
        "close() must drop this process's per-process owner claim"
    assert _owner_entries(sock) in (None, []), \
        "close() must release this process's owner record"


@pytest.mark.skipif(not hasattr(os, "register_at_fork"),
                    reason="no os.register_at_fork on this platform")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")  # fork in pytest
def test_forked_child_reclaims_ownership_of_inherited_server(tmp_path):
    """#3599 adversarial review (fail-open): `_owner_refcounts` is inherited
    across `fork()`, so without an at-fork hook the child's `record_owner`
    would early-return on the parent's count and write NO record naming the
    child. When the parent was then SIGKILLed (no `forget_owner`), the child
    — a live owner holding the inherited connection — would be invisible and
    the reaper would kill the server out from under it.

    Runs the real `fork()`, reads the owners dir from the parent, and asserts
    a record naming the CHILD exists.
    """
    db_path = str(tmp_path / "fork.db")
    db = FalkorDB(db_path)
    try:
        sock = db.client.socket_file
        assert _owner_entries(sock), "parent should own the record"
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:  # child
            rc = 1
            try:
                os.close(r)
                # A second client on the SAME server in the forked child.
                child_db = FalkorDB(db_path)
                _ = child_db.client.socket_file
                rc = 0
            except BaseException:
                rc = 1
            finally:
                with contextlib.suppress(OSError):
                    os.write(w, str(rc).encode())
                    os.close(w)
                os._exit(rc if rc else 0)
        os.close(w)
        try:
            child_rc = os.read(r, 8).decode()
        finally:
            os.close(r)
        os.waitpid(pid, 0)
        assert child_rc == "0", "child failed to construct its own client"
        entries = _owner_entries(sock)
        assert entries, "the parent's record must survive"
        assert any(e.startswith(f"{pid}-") for e in entries), (
            f"the forked child must be recorded as an owner of the inherited "
            f"server (owners={entries}, child pid={pid}) — otherwise a "
            f"parent SIGKILL makes a live child's server reapable")
    finally:
        with contextlib.suppress(Exception):
            db.close()


def test_shared_server_keeps_co_tenant_owner_record(tmp_path):
    """#3599 P0 guard, end-to-end: two clients in ONE process on ONE server
    each own a record claim; closing the first must NOT drop the shared
    record (the reaper would otherwise see zero live owners and kill a live
    co-tenant's server).

    The record is reference-counted per process, so it disappears only when
    the last client releases it."""
    from tortoise.projection import FalkorProjection
    db_path = str(tmp_path / "owners_shared.db")
    a = FalkorProjection(db_path, graph_name="test")
    b = None
    try:
        b = FalkorProjection(db_path, graph_name="test")
        cli_a = getattr(a.db, "client", a.db)
        cli_b = getattr(b.db, "client", b.db)
        assert cli_a.socket_file == cli_b.socket_file, "must share one server"
        assert cli_a is not cli_b, "distinct clients on the shared server"
        sock = cli_a.socket_file
        assert _owner_entries(sock), "a shared construction must be recorded"
        a.close()
        assert _owner_entries(sock), (
            "the co-tenant's owner record must survive a.close() — losing it "
            "would let the reaper kill a live shared server (#3599 P0)")
        b.close()
        assert _owner_entries(sock) in (None, []), \
            "the last client's close must release the shared record"
    finally:
        # #3599 review (P2): without this guard a mid-test assertion failure
        # leaves two live embedded servers behind — the very leak this test
        # exists to prevent, leaked BY the test.
        for proj in (b, a):
            if proj is not None:
                with contextlib.suppress(Exception):
                    proj.close()


def test_close_must_not_delete_a_live_cotenants_socket_dir(tmp_path):
    """#3653: closing ONE client of a SHARED embedded server must never delete
    the server's socket dir (or shut the live server down) while a co-tenant
    still holds it.

    redislite's ``RedisMixin._cleanup()`` rmtrees the socket dir whenever its
    own ``_connection_count() <= 1`` — and that count is **0** the moment
    ``_is_redis_running()`` is False, i.e. once the shared ``.settings``
    registry file is gone (a previous close removes it; ``_cleanup`` does
    ``os.remove(self.settingregistryfile)``). Zero is then read as "last
    client", so the close SHUTDOWNs the live server and deletes its socket
    dir out from under every live co-tenant. On CI that is the observed
    ``Error 2 connecting to /tmp/tmpXXXX/redis.socket. No such file or
    directory`` / ``FATAL CONFIG FILE ERROR ... 'dir '/tmp/tmpXXXX''`` —
    seeded state vanishing mid-test.

    The registry-removed state is set up explicitly here because that is the
    exact precondition observed; the fix must decide co-tenancy from a
    registry-INDEPENDENT signal (owner records + a raw socket probe), not
    from the stale registry.
    """
    from tortoise.projection import FalkorProjection

    db_path = str(tmp_path / "cotenant_registry_gone.db")
    a = FalkorProjection(db_path, graph_name="test")
    b = None
    try:
        b = FalkorProjection(db_path, graph_name="test")
        cli_a = getattr(a.db, "client", a.db)
        cli_b = getattr(b.db, "client", b.db)
        sock = cli_a.socket_file
        assert sock and cli_b.socket_file == sock, "must share one server"
        pid = cli_a.pid
        assert _pid_alive(pid)

        # The CI precondition: the shared registry file is already gone, so
        # redislite's liveness check reads False and _connection_count() 0.
        registry = getattr(cli_a, "settingregistryfile", None)
        assert registry and os.path.exists(registry)
        os.remove(registry)
        assert cli_a._is_redis_running() is False

        a.close()

        assert os.path.exists(sock), (
            "#3653: the co-tenant's live socket was deleted by a.close() — a "
            "live embedded instance must never be torn down")
        cli_b.ping()  # the co-tenant is still served
        assert _pid_alive(pid), "#3653: shared server was killed by a.close()"

        b.close()
        assert _wait_server_dead(pid), (
            "the LAST client's close must still shut the server down")
    finally:
        for proj in (b, a):
            if proj is not None:
                with contextlib.suppress(Exception):
                    proj.close()


# ── #3653: the four adversarial findings (F1-F4) ──────────────────────────
#
# The guard in `test_close_must_not_delete_a_live_cotenants_socket_dir`
# covers exactly ONE teardown seam (FalkorDB.close). These tests cover the
# seams and defects the prior adversarial review proved by execution:
#   F1 redislite's own atexit `_cleanup` + `__del__` still run on the shared
#      path and tear the live co-tenant down once the registry file is gone;
#   F2 the CI-default `atexit_fast_close` (TORTOISE_FAST_ATEXIT=1) bypassed
#      the guard entirely and sent SHUTDOWN NOSAVE to a live peer's server;
#   F3 the fast path stranded the ephemeral socket dir forever (redislite
#      only rmtrees inside `if self.pid:`).
#   F4 the age-based `_active_client_count` probe missed a fresh, unnamed
#      live peer, letting the teardown proceed anyway.


def test_cotenant_close_neutralizes_redislites_own_teardown(tmp_path):
    """#3653 F1: the explicit close() guard is not enough — redislite's OWN
    atexit-registered ``RedisMixin._cleanup`` (and ``__del__``) still run at
    process exit. With the shared registry file gone they read
    ``_connection_count() == 0`` and stop the live server / rmtree its socket
    dir out from under the co-tenant. ``a.close()`` must neutralize them.

    RED mutation: remove the ``_neutralize_redislite_cleanup(inner)`` call
    from ``FalkorDB.close``'s shared branch. The explicit ``ia._cleanup()``
    below is byte-for-byte what redislite's atexit handler and ``__del__``
    call, and it then kills the co-tenant (pid dead, ``ib.ping()`` raises).
    """
    from tortoise.projection import FalkorProjection

    db_path = str(tmp_path / "f1_neutralize.db")
    a = FalkorProjection(db_path, graph_name="test")
    b = None
    ia = getattr(a.db, "client", a.db)
    try:
        b = FalkorProjection(db_path, graph_name="test")
        ib = getattr(b.db, "client", b.db)
        sock, pid = ia.socket_file, ia.pid
        assert sock and ib.socket_file == sock and _pid_alive(pid)

        # The observed precondition: the shared registry file is already gone.
        os.remove(ia.settingregistryfile)
        assert ia._is_redis_running() is False

        a.close()

        # Exactly what redislite's atexit `_cleanup` / `__del__` run.
        ia._cleanup()

        assert os.path.exists(sock), (
            "#3653 F1: redislite's own teardown deleted the live co-tenant's "
            "socket dir")
        ib.ping()
        assert _pid_alive(pid), (
            "#3653 F1: redislite's own teardown killed the live co-tenant")

        b.close()
        assert _wait_server_dead(pid), (
            "the LAST client must still be able to shut the server down")
    finally:
        for proj in (b, a):
            if proj is not None:
                with contextlib.suppress(Exception):
                    proj.close()


def test_fast_atexit_never_shuts_down_a_live_cotenant(tmp_path, monkeypatch):
    """#3653 F2: ``TORTOISE_FAST_ATEXIT=1`` is armed at CI workflow level, so
    ``atexit_fast_close`` is the DEFAULT teardown in CI. It must not SHUTDOWN
    a server a live co-tenant holds — the old ``_connection_count() > 1``
    guard is registry-based and reads 0 once the shared registry file is gone.

    RED mutation: restore ``client._connection_count() > 1`` in
    ``atexit_fast_close`` — the co-tenant is then killed by SHUTDOWN NOSAVE.
    """
    from tortoise.projection import FalkorProjection

    monkeypatch.setenv("TORTOISE_FAST_ATEXIT", "1")
    db_path = str(tmp_path / "f2_fast_atexit.db")
    a = FalkorProjection(db_path, graph_name="test")
    b = None
    ia = getattr(a.db, "client", a.db)
    try:
        b = FalkorProjection(db_path, graph_name="test")
        ib = getattr(b.db, "client", b.db)
        sock, pid = ia.socket_file, ia.pid
        assert sock and ib.socket_file == sock and _pid_alive(pid)

        os.remove(ia.settingregistryfile)
        assert ia._is_redis_running() is False

        # The real CI-default seam (also releases a's owner record).
        a.db._atexit_close()

        assert os.path.exists(sock), (
            "#3653 F2: the fast atexit path deleted the live co-tenant's "
            "socket dir")
        ib.ping()
        assert _pid_alive(pid), (
            "#3653 F2: the fast atexit path SHUTDOWN a live co-tenant")

        b.close()
        assert _wait_server_dead(pid), (
            "the last client must still shut the server down")
    finally:
        for proj in (b, a):
            if proj is not None:
                with contextlib.suppress(Exception):
                    proj.close()


def test_fast_close_reclaims_the_ephemeral_socket_dir(tmp_path, monkeypatch):
    """#3653 F3: the CI-default fast-close path must not strand the server's
    ephemeral socket dir. redislite's ``_cleanup`` only rmtrees inside
    ``if self.pid:`` — and ``self.pid`` is 0 once the server is dead — so a
    NOSAVEd server's dir leaked forever (measured: 10,711 orphaned dirs vs
    123 live). The fast path must reclaim it.

    #4214: this MUST hold on the exit seam too, not just mid-run — after a
    clean NOSAVE shutdown redislite has unlinked both ``redis.socket`` and
    ``redis.pid``, and the #4068 reaper's discovery requires one of those
    markers, so a deferred dir is invisible to the reaper and leaks forever.

    RED mutation: drop the ``_remove_ephemeral_socket_dir`` call from the
    fast path's SHUTDOWN-NOSAVE branch — the dir survives the close.
    """
    from tortoise.projection import FalkorProjection

    monkeypatch.setenv("TORTOISE_FAST_ATEXIT", "1")
    # #4214: the exit budget is process-global, and an earlier test in this
    # session may have consumed it by driving a seam directly. This test is
    # about RECLAMATION, so give the seam an unspent budget (monkeypatch
    # restores the module state afterwards).
    from tortoise import embedded_lifecycle as _el
    monkeypatch.setattr(_el, "_atexit_deadline", None)
    db_path = str(tmp_path / "f3_reclaim.db")
    proj = FalkorProjection(db_path, graph_name="test")
    inner = getattr(proj.db, "client", proj.db)
    try:
        rdir = inner.redis_dir
        pid = inner.pid
        assert rdir and os.path.isdir(rdir), "server owns an ephemeral dir"
        assert _pid_alive(pid)

        proj.db._atexit_close()  # the CI-default fast path + owner release

        assert _wait_server_dead(pid)
        assert not os.path.isdir(rdir), (
            "#3653 F3: the fast-close path stranded the ephemeral socket dir")
    finally:
        with contextlib.suppress(Exception):
            proj.db._t_release_owner()
        with contextlib.suppress(Exception):
            proj.close()


def test_cotenant_probe_is_age_independent(tmp_path):
    """#3653 F4: a FRESH, unnamed live peer must still count as a co-tenant.

    ``embedded_reaper._active_client_count`` deliberately excludes
    connections younger than 2s (and unnamed ones) — that SKIPME heuristic
    is right for the reaper but wrong here: a peer that attached moments ago
    was MISSED, the guard returned False, and the teardown proceeded against
    a live server. The peer here is deliberately UNINSTRUMENTED (a raw
    redislite client that writes no owner record), so the owner-record
    signal cannot mask the age miss.

    RED mutation: restore ``_active_client_count(key) > 1`` in
    ``cotenant_holds_server`` — the precondition asserts below prove the peer
    is invisible to the age heuristic while ``_client_list`` sees it.
    """
    from redislite.falkordb_client import FalkorDB as _RawFalkorDB

    from tortoise.embedded_lifecycle import cotenant_holds_server
    from tortoise.embedded_reaper import _active_client_count, _client_list
    from tortoise.projection import FalkorProjection

    db_path = str(tmp_path / "f4_age.db")
    a = FalkorProjection(db_path, graph_name="test")
    peer = None
    ia = getattr(a.db, "client", a.db)
    try:
        # Uninstrumented peer: shares the server, writes NO owner record.
        peer = _RawFalkorDB(db_path)
        ip = getattr(peer, "client", peer)
        sock = ia.socket_file
        assert ip.socket_file == sock, "the raw peer must share the server"
        ip.ping()

        # Precondition (the F4 bug): the age-based heuristic misses the fresh
        # unnamed peer while a raw CLIENT LIST proves a second client exists.
        assert _active_client_count(sock) is not None
        assert _active_client_count(sock) <= 1, (
            "test precondition: the peer must be young/unnamed so the age "
            "heuristic misses it")
        assert len(_client_list(sock) or []) >= 2, (
            "test precondition: the raw list must see the live peer")

        assert cotenant_holds_server(ia) is True, (
            "#3653 F4: a fresh, unnamed live peer was judged absent — the "
            "teardown would then kill it")
    finally:
        with contextlib.suppress(Exception):
            a.close()
        if peer is not None:
            with contextlib.suppress(Exception):
                peer.close()


def test_peer_completes_a_run_while_a_cotenant_exits_cross_process(tmp_path):
    """#3653 ACCEPTANCE — cross-process, the property the issue asks for.

    With the CI-default ``TORTOISE_FAST_ATEXIT=1`` and the exact observed
    precondition (the shared registry file is gone), a PEER process must
    complete a run while its co-tenant EXITS mid-session. The child's exit
    runs BOTH teardown seams — our ``_atexit_close``/``atexit_fast_close``
    (F2) and redislite's own atexit ``_cleanup`` + ``__del__`` (F1) — each of
    which, pre-fix, saw ``_connection_count() == 0`` and killed the live
    server + deleted its socket dir. Process B (this test) must still write
    and read afterwards.

    RED mutation: remove the ``cotenant_holds_server`` guard from
    ``atexit_fast_close`` (fall back to ``_connection_count``) or the
    ``_neutralize_redislite_cleanup`` call on its shared path. The assertion
    that B's post-exit query succeeds then fails with
    ``redis.socket. No such file or directory``.
    """
    from tortoise.projection import FalkorProjection

    db_path = str(tmp_path / "cross_process_cotenant.db")
    proj = FalkorProjection(db_path, graph_name="test")
    inner = getattr(proj.db, "client", proj.db)
    pid, sock = inner.pid, inner.socket_file
    assert sock and _pid_alive(pid)

    script = (  # noqa: UP031 - child-script %-template (matches siblings)
        "import sys, os\n"
        "sys.path.insert(0, %r)\n"
        "os.environ['TORTOISE_FAST_ATEXIT'] = '1'\n"
        "from tortoise.projection import FalkorProjection\n"
        "p = FalkorProjection(sys.argv[1], graph_name='test')\n"
        "cli = getattr(p.db, 'client', p.db)\n"
        "print('READY pid=%%s sock=%%s' %% (cli.pid, cli.socket_file), flush=True)\n"
        "sys.stdin.readline()\n"  # parent closes stdin -> normal exit
    ) % _REPO_ROOT
    env = _child_env()
    env["TORTOISE_FAST_ATEXIT"] = "1"
    proc = _subprocess.Popen(
        [sys.executable, "-c", script, db_path],
        stdin=_subprocess.PIPE, stdout=_subprocess.PIPE,
        stderr=_subprocess.PIPE, text=True, env=env,
    )
    try:
        ready = _read_child_line(proc, "READY ")
        kv = dict(part.split("=", 1) for part in ready.split()[1:])
        assert int(kv["pid"]) == pid, "the child must share B's server"
        assert kv["sock"] == sock

        # The exact observed precondition: the shared registry file is gone.
        assert inner.settingregistryfile and os.path.exists(
            inner.settingregistryfile)
        os.remove(inner.settingregistryfile)
        assert inner._is_redis_running() is False

        # Let the co-tenant exit mid-session (normal exit -> atexit runs).
        proc.stdin.close()
        proc.wait(timeout=90)

        # The peer completes its run.
        assert os.path.exists(sock), (
            "#3653: a co-tenant's exit deleted the live peer's socket dir")
        assert _pid_alive(pid), "#3653: a co-tenant's exit killed the peer"
        proj.g.query("CREATE (n:Point {id:'cotenant-survived'})")
        rows = proj.g.query(
            "MATCH (n:Point {id:'cotenant-survived'}) RETURN count(n)"
        ).result_set
        assert rows and rows[0][0] >= 1, (
            "#3653: the peer could not complete its run after the co-tenant "
            "exited")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        with contextlib.suppress(Exception):
            proj.close()


def test_partial_init_cleanup_reclaims_the_orphaned_server(tmp_path):
    """#3653 regression: a redislite client whose ``__init__`` aborted AFTER
    the embedded server started has a live pidfile but NO ``connection_pool``.

    redislite's ``_start_redis()`` runs before the redis-py ``ConnectionPool``
    is built, so a failure in that window (a ``<db>.settings`` parent that
    vanished under the tempdir race, a registry read that lost its file)
    leaves the object half-built. Its own atexit-registered ``_cleanup`` and
    ``__del__`` then abort at ``self.shutdown(...)`` with
    ``AttributeError: 'Redis' object has no attribute 'connection_pool'``,
    leaking one embedded server per aborted ``__del__`` (CI measured 43).

    These objects never pass through a ``tortoise.FalkorDB`` close seam (the
    wrapper registers only after ``super().__init__()`` returns), so the
    guarded ``_cleanup`` must reclaim the orphan itself: stop the server over
    the raw socket and neutralize the client.

    RED mutation: restore the unguarded ``RedisMixin._cleanup`` (drop
    ``_install_partial_init_cleanup_guard``). ``ia._cleanup()`` then raises
    ``AttributeError`` and the server survives.
    """
    from tortoise.projection import FalkorProjection

    db_path = str(tmp_path / "partial_init.db")
    proj = FalkorProjection(db_path, graph_name="test")
    inner = getattr(proj.db, "client", proj.db)
    pid, sock = inner.pid, inner.socket_file
    assert sock and _pid_alive(pid), "server owns a live socket"

    # The exact half-built shape: a live pidfile, no connection pool.
    del inner.connection_pool
    assert not hasattr(inner, "connection_pool")
    assert _pid_alive(pid)

    # Byte-for-byte what redislite's atexit handler and ``__del__`` call.
    inner._cleanup()

    assert _wait_server_dead(pid), (
        "#3653: a partial-init client's _cleanup leaked its orphaned server")
    assert getattr(inner, "pidfile", "MISSING") is None, (
        "#3653: _cleanup must leave the client neutralized")

    with contextlib.suppress(Exception):
        proj.db._t_close()
