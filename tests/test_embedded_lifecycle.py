"""Embedded-client lifecycle tests — issue #1005.

Verifies that tortoise.FalkorDB and TortoiseSDK shut down their embedded
redislite servers deterministically: weakref.finalize on GC, context-manager
support, and idempotent close. Also enforces that no NEW raw embedded
constructions appear in tests without being allowlisted.
"""
from __future__ import annotations

import contextlib
import gc
import logging
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


# ── #4879: no test here may inherit or leave an ARMED exit budget ─────────
#
# `tortoise.embedded_lifecycle._atexit_deadline` is a PROCESS-WIDE clock: the
# first `atexit_fast_close(..., at_exit=True)` call in the process anchors it
# (default 30 s) and it is NEVER re-armed. Several tests in this module call
# the exit seam MID-RUN (`_atexit_close()`) to exercise it without exiting,
# which arms that clock for the rest of the process. A later module's own
# seam test then reads the clock as SPENT and takes the budget
# short-circuit — which returns "handled" while deliberately leaving the
# server RUNNING — instead of the close it is asserting. That is exactly how
# `test_embedded_lifecycle_fast_close.py::test_atexit_seams_registered` went
# red once this module grew: the clock armed at
# `test_fast_atexit_never_shuts_down_a_live_cotenant`, and 51 s later (budget
# 30 s) the sibling module's assertion read it as spent. The mechanism is the
# clock, not a leaked server/socket/pid — with `TORTOISE_ATEXIT_BUDGET=0`
# (never expires) the same pair is green.
#
# `test_exit_cascade_still_reclaims_the_socket_dir` in the sibling module
# already works around this hazard per-test ("The process budget is global
# and may already be spent by an earlier test's direct seam call"). This is
# that same reset applied to EVERY test in this module, in both directions,
# so neither an inherited nor a left-behind clock can reach a test that
# asserts a close happened. Resetting to None (never to a captured value)
# is the documented mid-run contract — "Mid-run calls are unbounded" — and
# is safe in the only direction that matters: an unspent budget can only
# make the seam do more work, never skip a close it should have done.
@pytest.fixture(autouse=True)
def _isolate_atexit_budget():
    from tortoise import embedded_lifecycle

    embedded_lifecycle._atexit_deadline = None  # no cascade is running
    yield
    embedded_lifecycle._atexit_deadline = None


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
    # #2814: opens its own FalkorProjection on a scratch path so the
    # rebuild CLI surface (`python -m tortoise rebuild`) can be driven
    # against a real DB — raw construction is the input under test, and
    # the carve-out membership above is the justification.
    "test_rebuild_config_preservation.py",
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


def test_team_create_leaves_no_graph_when_the_journal_append_fails(
        tmp_path, monkeypatch):
    """#3214/#3390: the journal append IS the ownership record, so its
    failure must not leave an unowned team graph behind — no graph a sweep
    cannot attribute.

    #3390 made the order write-ahead at this site: the journal line is
    written BEFORE the TeamMeta CREATE that materializes ``org_{name}``, so a
    failed append means the CREATE never ran and there is nothing to drop.
    This test still guards the invariant (no unowned graph after a failed
    append); it now passes because the graph is NEVER MINTED, not because a
    compensating ``delete()`` removes it. Do NOT re-add a delete on the
    failure path — it would be dead compensation for a graph that cannot
    exist, re-introducing the very removal #3390 made.

    The append is forced to fail for the TEAM graph only (the registry append
    must succeed, or _get_registry would raise before anything is created —
    that call site's own contract is that a raise there mints nothing). Then
    assert: the raise propagated, the ``org_{name}`` graph is ABSENT, and the
    registry Team node was rolled back by team_create's own handler.
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
            "team_create must leave no unowned graph when the journal append fails"
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
    """Owner-RECORD filenames for the server (control dotfiles excluded).

    #4577: the owner dir also holds a `.lock` control file while an owner is
    live; these assertions are about the record files, so hidden entries are
    filtered exactly as `_owner_records` filters them.
    """
    from tortoise.embedded_lifecycle import owner_record_dir
    d = owner_record_dir(socket_file)
    if not os.path.isdir(d):
        return None
    return sorted(n for n in os.listdir(d) if not n.startswith("."))


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


def test_owner_socket_of_reads_own_socket_and_never_a_callable_client():
    """#4487 review: a raw embedded redislite `Redis` exposes `.client` as a
    BOUND METHOD (its self-constructing clone helper), so the old
    `getattr(client, "client", ...) or client` resolved 'inner' to that
    method, found no `socket_file`, and returned None — silently breaking
    the RELEASE fallback for raw clients whose record the #4487 constructor
    patch now writes. The client's OWN `.socket_file` must win, and a
    callable `.client` is never an inner client."""
    from tortoise.embedded_lifecycle import owner_socket_of

    class RawRedis:
        socket_file = "/tmp/raw/redis.socket"

        def client(self):  # redislite's clone helper — a callable attribute
            return object()

    assert owner_socket_of(RawRedis()) == "/tmp/raw/redis.socket"

    class Wrapper:
        socket_file = None  # the guarded wrapper has no socket of its own

        class Inner:
            socket_file = "/tmp/wrapped/redis.socket"

        client = Inner()

    assert owner_socket_of(Wrapper()) == "/tmp/wrapped/redis.socket"


def test_raw_client_owner_record_released_by_the_fallback(tmp_path):
    """#4487 review: the constructor patch writes a record for a RAW client;
    the release fallback (`forget_owner(owner_socket_of(db))`) must be able
    to release it. Pre-fix `owner_socket_of` returned None for a raw Redis,
    so the claim was stranded with a LIVE pid and pinned the server."""
    from redislite.client import Redis as RawRedis

    from tortoise.embedded_lifecycle import (
        _owner_refcounts,
        _release_owner_quietly,
    )
    from tortoise.embedded_reaper import _owner_records

    raw = RawRedis(str(tmp_path / "raw_release.db"))
    try:
        sock = raw.socket_file
        assert _owner_records(sock) == (1, 1), "patch must have recorded it"
        assert _owner_refcounts.get(sock) == 1
        # No captured sock argument: this is exactly the fallback path.
        _release_owner_quietly(raw)
        assert sock not in _owner_refcounts, (
            "the release fallback must resolve a raw client's socket — "
            "pre-fix it resolved None and stranded the claim")
        assert _owner_records(sock) in (None, (0, 1)), (
            "after release the record must be gone (or count as dead)")
    finally:
        with contextlib.suppress(Exception):
            raw.close()


def test_release_owner_uses_the_captured_socket_after_teardown(tmp_path):
    """#4487 review (cycle 2): `close_embedded_clients` captures the socket
    BEFORE its teardown (redislite `_cleanup()` nulls `socket_file`) and hands
    it to `_release_owner`. Pre-fix the fallback re-derived
    `owner_socket_of(inner)` AFTER teardown, got None, and stranded the
    refcount — which then makes `record_owner` short-circuit forever, so a
    later LIVE server on the same path got NO record (the #4487
    uninstrumented-live-server class).

    The seam itself is deliberately NOT called: `close_embedded_clients()` is
    process-wide and consumes the process-global atexit budget (#4214), which
    broke the fast-atexit tests that follow it in the carve-out lane. The
    captured-socket contract is what the seam calls into, and that is what is
    pinned here."""
    from redislite.client import Redis as RawRedis

    from tortoise.embedded_lifecycle import (
        _owner_refcounts,
        _release_owner,
        owner_socket_of,
    )
    from tortoise.embedded_reaper import _owner_records

    raw = RawRedis(str(tmp_path / "captured_sock.db"))
    try:
        sock = raw.socket_file
        assert _owner_records(sock) == (1, 1), "the patch must have recorded it"
        # Construct the TRUE post-teardown state: redislite's `_cleanup()`
        # nulls `socket_file`, so the caller-captured `sock` becomes the ONLY
        # way to resolve the claim. Without this the test passes against the
        # pre-fix code too (it would just re-derive the same path).
        raw.socket_file = None
        assert owner_socket_of(raw) is None, (
            "the test must exercise the nulled-socket_file state")
        _release_owner(raw, raw, sock)
        assert sock not in _owner_refcounts, (
            "the captured-socket path must release a raw client's claim; "
            "pre-fix the post-teardown owner_socket_of resolved None and "
            "stranded it")
    finally:
        with contextlib.suppress(Exception):
            raw.close()


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


# ── #4487: EVERY redislite construction writes an owner record ────────────
# The guarded `tortoise.FalkorDB` used to be the only writer, so a RAW
# `redislite.falkordb_client.FalkorDB(...)` / `redislite.client.Redis(...)`
# spawn was uninstrumented — the reaper's per-server "all owners dead"
# signal then had no input and `--only-safe` failed closed forever on a host
# whose suites are continuously active. `embedded_lifecycle` now patches
# redislite's own `RedisMixin.__init__` so the raw constructions below record
# too. Every test here fails on the pre-fix code.

def test_raw_redislite_construction_writes_an_owner_record(tmp_path):
    """A RAW redislite construction (guarded class NOT involved) must record
    its owner — the #4487 gap. Both shapes redislite offers are covered:
    `falkordb_client.FalkorDB` (server on `.client`) and `client.Redis`.

    Positive control for the writer side: delete the `RedisMixin.__init__`
    patch and this reads (None) instead of (1, 1)."""
    from redislite.client import Redis as RawRedis
    from redislite.falkordb_client import FalkorDB as RawFalkorDB

    from tortoise.embedded_lifecycle import _owner_refcounts, owner_socket_of
    from tortoise.embedded_reaper import _owner_records

    raw_fdb = RawFalkorDB(str(tmp_path / "raw_fdb.db"))
    raw_redis = RawRedis(str(tmp_path / "raw_redis.db"))
    try:
        for label, client in (("FalkorDB", raw_fdb), ("Redis", raw_redis)):
            # The patch reads `.socket_file` off the constructed object first
            # (an embedded `Redis` carries its own `.client` attribute, so
            # `owner_socket_of` is only the fallback); resolve it the same way
            # here, as later assertions do.
            sock = getattr(client, "socket_file", None) or owner_socket_of(client)
            assert sock, f"raw {label} should expose a socket"
            entries = _owner_entries(sock)
            assert entries, (
                f"raw {label} construction wrote NO owner record — the "
                f"uninstrumented-spawn gap #4487 exists to close")
            assert all(e.startswith(f"{os.getpid()}-") for e in entries), \
                entries
            assert _owner_records(sock) == (1, 1), (
                f"raw {label}: the reaper must read exactly one LIVE owner "
                f"from what the patch wrote")
            assert _owner_refcounts.get(sock) == 1, \
                f"raw {label}: one client, one claim"
    finally:
        with contextlib.suppress(Exception):
            raw_fdb.close()
        with contextlib.suppress(Exception):
            raw_redis.close()


def test_guarded_construction_writes_exactly_one_owner_record(tmp_path):
    """#4487 single-writer invariant: the guarded `tortoise.FalkorDB` and the
    redislite patch must not BOTH record. `record_owner` is refcounted per
    (process, socket path); two writers for one client would leave the record
    (with a LIVE pid) pinning the server after close() released only one
    claim — a fail-closed leak the reaper could never clear.

    Positive control: restore the guard's own `record_owner` call and the
    refcount reads 2."""
    from tortoise.embedded_lifecycle import _owner_refcounts, owner_socket_of
    from tortoise.embedded_reaper import _owner_records

    db = FalkorDB(str(tmp_path / "one_claim.db"))
    try:
        sock = owner_socket_of(db)
        assert sock
        assert _owner_refcounts.get(sock) == 1, (
            "exactly one writer must claim the server — 2 means the guard and "
            "the patch both recorded (close() then strands the record)")
        assert _owner_records(sock) == (1, 1)
    finally:
        with contextlib.suppress(Exception):
            db.close()
    assert _owner_refcounts.get(sock) is None, \
        "close() must release the single claim"
    assert _owner_entries(sock) in (None, []), \
        "close() must remove the record it claimed"


def test_raw_construction_abnormal_exit_leaves_a_confirmable_record(tmp_path):
    """#4487 end-to-end, the generator the issue proved: a RAW construction
    whose process dies ABNORMALLY (no close seam runs) must leave an owner
    record with a DEAD pid, so the reaper's per-server signal reads
    ``(0, 1)`` — all owners dead — and can confirm the orphan WITHOUT the
    global suite-marker gate ever opening.

    `os._exit` skips every atexit/close seam (the SIGKILL analogue that
    stays deterministic under pytest). Pre-fix the child writes no record at
    all, so the parent would read ``None`` and never confirm.

    The child's DATA dir is the parent's ``tmp_path`` (passed as argv) so the
    DB dir is reclaimed by pytest; the child's own redislite SOCKET dir is
    removed below. A child-side ``mkdtemp`` would leak one dir per run."""
    import shutil

    from tortoise.embedded_reaper import _owner_records

    dbdir = tmp_path / "killdb"
    dbdir.mkdir()
    child = (
        "import os, sys\n"
        "import tortoise\n"  # installs the RedisMixin owner-record patch
        "from redislite.falkordb_client import FalkorDB\n"
        "db = FalkorDB(os.path.join(sys.argv[1], 'kill.db'))\n"
        "print(db.client.socket_file, flush=True)\n"
        "os._exit(0)\n"  # no close seam: exactly the abnormal-exit generator
    )
    proc = _subprocess.Popen([sys.executable, "-c", child, str(dbdir)],
                             stdout=_subprocess.PIPE, stderr=_subprocess.PIPE,
                             text=True)
    try:
        out, err = proc.communicate(timeout=120)
    except _subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        pytest.fail(f"child hung: out={out!r} err={err!r}")
    assert proc.returncode == 0, err
    sock = out.strip().splitlines()[-1]
    d = os.path.dirname(sock)
    try:
        assert os.path.exists(sock), f"child never started its server: {out!r}"
        owners = _owner_records(sock)
        assert owners == (0, 1), (
            f"an abnormally-exited RAW spawn must leave exactly one DEAD "
            f"owner record so the orphan is confirmable; got {owners!r}")
        # The record's pid is the dead child — the #1642 FIX 5 identity read
        # must therefore count it dead (that is what makes (0, 1) an orphan
        # verdict rather than a live-owner protection).
        entries = _owner_entries(sock)
        assert entries and len(entries) == 1, entries
        assert entries[0].split("-", 1)[0] == str(proc.pid), entries
    finally:
        pidfile = os.path.join(d, "redis.pid")
        try:
            server_pid = int(Path(pidfile).read_text().strip())
        except (OSError, ValueError):
            server_pid = None
        if server_pid:
            _kill_quiet(server_pid)
        shutil.rmtree(d, ignore_errors=True)


def test_fork_hook_drops_the_inherited_start_cache():
    """#4487 review (fail-open): `_own_start_cache` is inherited across
    `fork()`. A stale entry for a pid the kernel later reassigns to the child
    would make `record_owner` stamp the child's record with a DEAD ancestor's
    start — `_owner_records` compares it against the real start, reads the
    record dead, and the reaper kills a live owner's server (the #1642 FIX 5
    class). The at-fork hook must drop the cache so the child resolves its
    own start fresh.

    Mutation: delete `_own_start_cache.clear()` from
    `_adopt_owner_records_after_fork` and this fails."""
    from tortoise.embedded_lifecycle import (
        _adopt_owner_records_after_fork,
        _own_start_cache,
    )
    _own_start_cache.clear()
    me = os.getpid()
    try:
        _own_start_cache[me] = 1.0          # a dead ancestor's start
        _own_start_cache[99999999] = 2.0    # any other inherited key
        _adopt_owner_records_after_fork()
        # The hook may legitimately RE-populate `me` (re-recording an inherited
        # socket resolves the real start), so assert the STALE VALUE is gone —
        # not that the key is absent.
        assert _own_start_cache.get(me) != 1.0, (
            "the child must not keep a stale start for its own pid — a "
            "dead-ancestor start stamps a live owner's record as DEAD")
        assert 99999999 not in _own_start_cache
    finally:
        _own_start_cache.clear()


# ── #4577: the shared owner liveness flock (writer side) ────────────────

def test_record_owner_holds_and_releases_the_shared_lock(tmp_path):
    """#4577: `record_owner` holds a SHARED flock on
    `.tortoise-owners/.lock` for the process's lifetime (the reaper's
    `_owner_lock_held` reads it as a live owner); the lock survives until the
    LAST client on the socket forgets, and its fd is closed then (no leak).

    Mutation: delete the `_acquire_owner_lock` call from `record_owner`; the
    first `is True` assertion fails. Make `_release_owner_lock` return without
    closing the fd and the post-release `os.fstat(fd)` check fails."""
    from tortoise.embedded_lifecycle import (
        _owner_lock_fds,
        _owner_refcounts,
        forget_owner,
        record_owner,
    )
    from tortoise.embedded_reaper import _owner_lock_held

    sock = str(tmp_path / "sock" / "redis.socket")
    key = os.path.abspath(sock)
    try:
        assert record_owner(sock) is True
        assert _owner_refcounts[key] == 1, "one client, one claim"
        assert _owner_lock_held(sock) is True, (
            "record_owner must hold the shared liveness lock")
        fd = _owner_lock_fds[key]
        # Idempotent per (process, socket): the second claim reuses the SAME
        # descriptor — a new one would leak the first and could be unlocked
        # independently.
        assert record_owner(sock) is False
        assert _owner_refcounts[key] == 2
        assert _owner_lock_fds[key] == fd, "no second fd for the same socket"
        # First forget keeps the lock: another client still owns the server.
        assert forget_owner(sock) is False
        assert _owner_lock_held(sock) is True, (
            "the lock must survive the first (non-last) forget")
        # Last forget drops it and closes the fd.
        assert forget_owner(sock) is True
        assert _owner_lock_held(sock) in (None, False), (
            "the last forget must release the lock (the .lock file is "
            "reclaimed, so the probe reads False or UNKNOWN — never True)")
        assert key not in _owner_lock_fds, "the fd map must not leak"
        with pytest.raises(OSError):
            os.fstat(fd)  # closed descriptor -> EBADF
    finally:
        _owner_refcounts.pop(key, None)
        _fd = _owner_lock_fds.pop(key, None)
        if _fd is not None:
            with contextlib.suppress(OSError):
                os.close(_fd)


def test_record_owner_never_follows_a_symlinked_lock(tmp_path):
    """#4577 / #4098: `record_owner` opens `.lock` with `O_NOFOLLOW`, so a
    symlink planted at `.lock` is never followed — the process takes no lock
    rather than locking an attacker-chosen file. The owner record is still
    written, so the fallback liveness signal survives.

    Mutation: drop `O_NOFOLLOW` from `_acquire_owner_lock`; the symlink is
    followed, `_owner_lock_fds` gains an fd, and the `key not in` assertion
    fails (the process would believe it owned a lock on the target)."""
    from tortoise.embedded_lifecycle import (
        _owner_lock_fds,
        forget_owner,
        record_owner,
    )
    from tortoise.embedded_reaper import _owner_lock_held

    sock = str(tmp_path / "sock" / "redis.socket")
    owners = Path(sock).parent / ".tortoise-owners"
    owners.mkdir(parents=True)
    target = tmp_path / "victim.lock"
    target.write_text("")
    (owners / ".lock").symlink_to(target)
    key = os.path.abspath(sock)
    try:
        record_owner(sock)  # must not raise and must not follow the link
        assert key not in _owner_lock_fds, (
            "a symlinked .lock must never be followed into a held lock")
        assert _owner_lock_held(sock) is None, (
            "the symlinked lock reads UNKNOWN -> the records are the "
            "fallback")
        assert _owner_entries(sock), (
            "the owner record itself must still be written")
    finally:
        forget_owner(sock)
        _owner_lock_fds.pop(key, None)


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


# ── #4879: a DEAD recorded socket must not be replayed ────────────────────
#
# redislite's `RedisMixin._is_redis_running()` (client.py:305-332) validates
# only three things: the `<db>.settings` registry file exists, the recorded
# `pidfile` exists, and that pid is a live process. It NEVER validates the
# recorded `unixsocket`, and `_load_setting_registry()` (client.py:351-378)
# then assigns `self.socket_file = settings['unixsocket']` unconditionally
# (client.py:376). So when the registry + pidfile survive but the socket file
# is gone (with a live pid), the predicate reads True, the DEAD path is
# replayed, and the construction ping dies with
#     redis.exceptions.ConnectionError: Error 2 connecting to
#     /tmp/tmpXXXX/redis.socket. No such file or directory.
# That is the deterministic main-branch failure of
# `tests/test_pack_state.py::TestBackfillScript::test_apply_writes_to_introspection_read_target`.
#
# The #4879-REVIEW hole: answering False is NOT a repair. `__init__` takes
# its `else:` branch (client.py:454-462) and runs
# `_create_redis_directory_tree()` (client.py:203-216, a new mkdtemp) +
# `_start_redis()` (client.py:218-236), which passes
# `'dbdir': self.dbdir, 'dbfilename': self.dbfilename` (client.py:234-235)
# straight through — THE SAME RDB FILE. So the state built below (registry
# present, recorded socket GONE, recorded pid LIVE) is A state in which
# answering False starts a SECOND writer on one RDB — but not the only such
# state: a plain cold start with no registry does it too (tortoise#4921). The
# repair in
# `tortoise/embedded_lifecycle.py` therefore PROVES the live pid is this
# registry's own server, stops it gracefully, and only then drops the stale
# registry. These tests pin the END STATE, not "construction succeeded".


def _live_rdb_writers(db_dir, dbfilename):
    """Live redis-server pids whose OWN redis.config declares this RDB.

    The RDB's identity is (dir, dbfilename) in the server's config — NOT in
    its argv: redislite starts `redis-server unixsocket:<socket_dir>/redis.socket`,
    so the argv names the SOCKET tempdir while the RDB lives at
    `<db_dir>/<dbfilename>`. Counting by argv dir would MISS a second server
    started against the SAME RDB — the hazard under test. Reuses the reaper's
    own pass-1 helpers (pgrep enumeration + argv/config parsing).
    """
    from tortoise.embedded_reaper import (
        _pgrep_redis_servers,
        _read_redis_config,
        _socket_dir_from_cmdline,
    )
    want = os.path.realpath(str(db_dir))
    writers = []
    for pid in _pgrep_redis_servers():
        socket_dir = _socket_dir_from_cmdline(pid)
        if not socket_dir:
            continue
        config = _read_redis_config(socket_dir) or {}
        if config.get("dbfilename") != dbfilename:
            continue
        if os.path.realpath(config.get("dir", "")) == want:
            writers.append(pid)
    return sorted(writers)


def test_live_recorded_server_with_dead_socket_is_stopped_not_doubled(tmp_path):
    """#4879: registry + LIVE server + recorded socket GONE (the hole's state).

    The recorded server is genuinely alive and holding this RDB — a state in
    which answering False arms a SECOND writer on the same dbfilename, though
    NOT the only one: a cold start with no registry arms it too (tortoise#4921).
    RED on the first cut (`77ce56763`): construction succeeds but
    this RDB ends up with TWO live writers. RED on unmodified redislite: the
    construction raises the ``ConnectionError`` above. GREEN: the proven
    holder is stopped, its in-memory write is persisted by that graceful
    stop, the stale registry is dropped, and EXACTLY ONE live writer remains.
    """
    import json as _json

    db_path = tmp_path / "replayed_registry.db"
    # #4439: the harness disables redislite's periodic save schedule
    # (`tests/_embedded.py`), and redis only SAVEs on SIGTERM while a save
    # schedule exists (`saveparamslen > 0`) — and the #4879 repair stops this
    # stale holder with SIGTERM. Opt THIS holder back into a schedule so the
    # stop stays graceful and the assertion below keeps proving it: the
    # in-memory write must survive. The 900 s window never elapses inside the
    # test, so this adds no fork to the #4439 storm.
    first = FalkorDB(str(db_path), serverconfig={"save": ["900 1"]})
    registry = Path(str(db_path) + ".settings")
    recorded = _json.loads(registry.read_text())
    holder_pid = int(Path(recorded["pidfile"]).read_text().strip())
    dead_socket = recorded["unixsocket"]
    second = None
    try:
        assert _pid_alive(holder_pid), "the recorded server must be live"
        assert _live_rdb_writers(tmp_path, db_path.name) == [holder_pid], (
            "#4879 baseline: exactly the recorded server writes this RDB")
        # A write that exists ONLY in the holder's memory -> the repair must
        # stop it GRACEFULLY (redis saves on SIGTERM); a SIGKILL would lose it.
        assert first.client.set("4879-graceful-stop", "persisted")

        # The hole's trigger: the socket FILE vanishes while dir + pidfile +
        # registry survive and the server keeps running, still holding the RDB.
        os.remove(dead_socket)
        assert not os.path.exists(dead_socket)
        assert _pid_alive(holder_pid)

        second = FalkorDB(str(db_path))
        assert second.client.ping(), "a fresh embedded server must answer"

        # (1) the recorded holder was STOPPED, not left running.
        assert not _pid_alive(holder_pid), (
            "#4879: the recorded server must have been stopped")
        # (2) ...gracefully: its in-memory write survived into the RDB.
        assert second.client.get("4879-graceful-stop") == "persisted", (
            "#4879: the stop must be graceful (redis saves on SIGTERM)")
        # (3) THE BAR — exactly ONE live writer on this RDB: not zero, not two.
        writers = _live_rdb_writers(tmp_path, db_path.name)
        replacement = _json.loads(registry.read_text())
        new_pid = int(Path(replacement["pidfile"]).read_text().strip())
        assert writers == [new_pid], (
            f"#4879: expected exactly one live writer for {db_path} "
            f"(pid {new_pid}); found {writers} "
            f"(recorded holder {holder_pid})")
        # (4) ...and the registry was rebuilt onto a LIVE socket, not replayed.
        assert replacement["unixsocket"] != dead_socket, (
            "#4879: the DEAD recorded socket path was replayed")
        assert os.path.exists(replacement["unixsocket"])
    finally:
        for client in (second, first):
            if client is not None:
                with contextlib.suppress(Exception):
                    client._t_close()


def test_bound_client_predicate_does_not_signal_a_live_server(tmp_path):
    """#4879 review, scope: the repair fires only where a start is imminent.

    `_is_redis_running` is ALSO reached from redislite's close path
    (`_cleanup` -> `_connection_count`, client.py:188). A client that already
    holds a socket can never take `__init__`'s registry-load branch
    (client.py:449 requires `not self.socket_file`), so this state is not a
    replay waiting to be repaired: the predicate must keep the ORIGINAL
    answer and signal nothing — otherwise a close would kill a live server
    and drop its registry, the #3653 tear-a-live-co-tenant-down fail-open.
    """
    import json as _json
    import shutil

    db_path = tmp_path / "bound_predicate.db"
    client = None
    holder_pid = None
    socket_dir = None
    try:
        client = FalkorDB(str(db_path))
        registry = Path(str(db_path) + ".settings")
        recorded = _json.loads(registry.read_text())
        holder_pid = int(Path(recorded["pidfile"]).read_text().strip())
        socket_dir = os.path.dirname(recorded["unixsocket"])
        assert _pid_alive(holder_pid)

        os.remove(recorded["unixsocket"])
        assert not os.path.exists(recorded["unixsocket"])

        assert client.client._is_redis_running() is True, (
            "#4879: a bound client keeps the original predicate answer")
        assert _pid_alive(holder_pid), (
            "#4879: the predicate must not signal a live server")
        assert registry.exists(), (
            "#4879: ...nor drop the registry of a live server")
    finally:
        # The predicate deliberately did NOT stop the holder (that IS the
        # assertion above), so the TEST stops it and reclaims its dir.
        if holder_pid:
            with contextlib.suppress(OSError):
                os.kill(holder_pid, _signal.SIGTERM)
            _wait_server_dead(holder_pid)
        if client is not None:
            with contextlib.suppress(Exception):
                client._t_close()
        if socket_dir:
            shutil.rmtree(socket_dir, ignore_errors=True)


def test_unproven_recorded_pid_is_not_signalled_and_nothing_is_started(tmp_path):
    """#4879 review: provenance unverified -> LOUD failure, never a second writer.

    The recorded pid is LIVE but is not this registry's redis-server (here:
    this very test process), and the recorded socket is gone. The documented,
    DELIBERATE behaviour is today's loud ``ConnectionError``: nothing is
    signalled, nothing is started, and the registry is left alone. Answering
    False here would arm a second writer while an unproven live process may
    still hold the RDB.
    """
    import atexit
    import json as _json
    import shutil
    import tempfile

    import redis
    from redislite.client import RedisMixin

    # The recorded socket path must be SHORT enough for AF_UNIX: macOS rejects
    # paths over ~104 bytes with ENAMETOOLONG, which would mask the ENOENT this
    # bug actually produces. pytest's tmp_path is often that long, so the dead
    # socket lives in its own short temp dir (the same shape as redislite's
    # own /tmp/tmpXXXX/redis.socket).
    sock_dir = tempfile.mkdtemp(prefix="t4879_unproven_")
    db_path = tmp_path / "unproven_registry.db"
    registry = Path(str(db_path) + ".settings")
    dead_socket = os.path.join(sock_dir, "redis.socket")  # never created
    pidfile = tmp_path / "redis.pid"
    pidfile.write_text(str(os.getpid()))  # genuinely LIVE, but not ours
    registry.write_text(_json.dumps({
        "pidfile": str(pidfile),
        "unixsocket": dead_socket,
        "dbdir": str(tmp_path),
        "dbfilename": db_path.name,
    }))
    assert not os.path.exists(dead_socket), "the recorded socket must be DEAD"

    leaked = None
    try:
        with pytest.raises(redis.exceptions.ConnectionError) as excinfo:
            FalkorDB(str(db_path))
        # The loud branch is the REGISTRY REPLAY, not some other failure.
        assert dead_socket in str(excinfo.value)
        assert _pid_alive(os.getpid()), (
            "#4879: an unproven live pid must never be signalled")
        assert _live_rdb_writers(tmp_path, db_path.name) == [], (
            "#4879: the unproven branch must not start a second server")
        assert registry.exists(), "#4879: the registry is left untouched"
        # #4926: the construction ABORTED inside `original(...)`, so the claim
        # must have been released on the abort path. A claim left behind would
        # name a LIVE pid whose construction is gone: `_inflight_claim_holds`
        # would return True forever and the last-client decision would read
        # "co-tenant" for the life of the socket (the #3599 immortal-pin
        # class — the reaper's parsers ignore claim files, so nothing else
        # prunes it).
        import tortoise.embedded_lifecycle as _lifecycle
        aborted_key = os.path.abspath(dead_socket)
        assert _lifecycle._in_flight_replays.get(aborted_key, 0) == 0, (
            "#4926: an aborted construction must release its in-flight claim")
        assert _lifecycle._inflight_claim_holds(aborted_key) is False, (
            "#4926: an aborted construction must not hold the server")
        assert not os.path.exists(
            _lifecycle._inflight_claim_path(aborted_key)), (
            "#4926: the aborted construction's on-disk claim must be "
            "retracted — nothing else ever removes it")

        # The failed construction leaves a partially-built client whose own
        # atexit `_cleanup` (registered at client.py:448, before the raise)
        # would re-enter the dead socket at interpreter shutdown. Find it in
        # the raising traceback and neutralise it exactly as redislite's own
        # `_cleanup` does: a None pidfile makes that teardown a no-op
        # (client.py:94-97).
        for entry in excinfo.traceback:
            candidate = entry.frame.f_locals.get("self")
            if isinstance(candidate, RedisMixin):
                leaked = candidate
                break
        assert leaked is not None, "no partially-built client to neutralise"
    finally:
        if leaked is not None:
            atexit.unregister(leaked._cleanup)
            leaked.pidfile = None
        shutil.rmtree(sock_dir, ignore_errors=True)


def test_registry_without_a_holder_record_starts_clean(tmp_path):
    """#4879 review: a registry with NO ``pidfile`` is not a replay at all.

    ``_load_setting_registry`` returns early when the registry carries no
    ``pidfile`` (client.py:369-374), so answering True for this shape would
    leave ``socket_file`` at None and hand the construction to redis-py's TCP
    defaults — a SILENT cross-connection to whatever listens on
    localhost:6379 (the lane's docker FalkorDB here, a plain redis in most
    dev setups). It must start an EMBEDDED server instead.
    """
    import json as _json
    import shutil
    import tempfile

    sock_dir = tempfile.mkdtemp(prefix="t4879_noholder_")
    db_path = tmp_path / "no_holder_record.db"
    registry = Path(str(db_path) + ".settings")
    registry.write_text(_json.dumps({
        "unixsocket": os.path.join(sock_dir, "redis.socket"),
        "dbdir": str(tmp_path),
        "dbfilename": db_path.name,
    }))

    db = None
    try:
        db = FalkorDB(str(db_path))
        assert db.client.ping()
        sock = db.client.socket_file
        assert sock and os.path.exists(sock), (
            "#4879: must be an EMBEDDED unix socket, never a TCP default")
    finally:
        if db is not None:
            with contextlib.suppress(Exception):
                db._t_close()
        shutil.rmtree(sock_dir, ignore_errors=True)


def test_stop_proven_holder_escalates_only_after_its_budget(monkeypatch):
    """#4879 review: the stop is SIGTERM-first, and SIGKILL is the last resort.

    `_stop_proven_holder` may report a proven holder gone ONLY once it is
    actually gone. A holder that IGNORES SIGTERM is the case the escalation
    exists for: the helper must still return True (SIGKILL after the bounded
    budget), because a False there sends the caller to the loud branch — safe
    — while a True-while-alive would drop the registry and start a SECOND
    writer. The budget is shrunk so the escalation is exercised quickly.

    The holder is double-forked (orphaned to launchd/init) on purpose: a
    direct child would stay a ZOMBIE after SIGKILL and `_pid_alive` reads a
    zombie as alive on macOS (its /proc zombie check is Linux-only), which
    would make this assert the wrong thing.
    """
    import tortoise.embedded_lifecycle as _lifecycle

    monkeypatch.setattr(_lifecycle, "_STALE_HOLDER_SIGTERM_TIMEOUT", 0.5)
    spawner = _subprocess.Popen(
        [sys.executable, "-c", (
            "import os, signal, time\n"
            "pid = os.fork()\n"
            "if pid:\n"
            "    print(pid, flush=True)\n"
            "    os._exit(0)\n"
            "os.setsid()\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(60)\n"
        )],
        stdout=_subprocess.PIPE, text=True,
    )
    holder_pid = None
    try:
        holder_pid = int(spawner.stdout.readline().strip())
        spawner.wait(timeout=10)
        assert _pid_alive(holder_pid), "the stubborn holder must be live"
        assert _lifecycle._stop_proven_holder(holder_pid) is True, (
            "#4879: a SIGTERM-ignoring holder must be escalated, not left")
        assert not _pid_alive(holder_pid)
    finally:
        if holder_pid:
            with contextlib.suppress(OSError):
                os.kill(holder_pid, _signal.SIGKILL)
        with contextlib.suppress(Exception):
            spawner.kill()


def _spawn_redis_server_stub(stub_dir, socket_arg):
    """Spawn a live, ORPHANED process `ps`/pgrep read as a redis-server.

    Its argv is ``<stub_dir>/redis-server unixsocket:<socket_arg>`` — the
    same argv shape a real embedded server carries for
    `_pid_cmdline_names_dir`/`_socket_dir_from_cmdline`, and enough for
    `_pid_is_redis` (which only requires ``redis-server`` in the cmdline).
    The path deliberately does NOT contain ``redislite/bin/redis-server``:
    that is the pattern the process-wide reaper pgrep matches
    (`embedded_reaper._pgrep_redis_servers`), and a concurrent lane's reaper
    sweep would reap this stub mid-test.

    Double-forked like `test_stop_proven_holder_escalates_only_after_its_budget`:
    a directly-spawned child would stay a ZOMBIE after SIGTERM and
    `_pid_alive` reads a zombie as ALIVE on macOS (its /proc check is
    Linux-only), which would silently mask the provenance verdict this stub
    exists to exercise. Orphaned to launchd/init it is reaped on exit.
    Returns the stub's pid; the caller must SIGKILL its process GROUP (the
    stub keeps a ``sleep`` child).
    """
    stub = os.path.join(stub_dir, "redis-server")
    os.makedirs(os.path.dirname(stub), exist_ok=True)
    Path(stub).write_text(
        "#!/bin/sh\n"
        "trap 'exit 0' TERM\n"
        "while true; do sleep 1; done\n"
    )
    os.chmod(stub, 0o755)
    spawner = _subprocess.Popen(
        [sys.executable, "-c", (
            "import os, sys\n"
            "pid = os.fork()\n"
            "if pid:\n"
            "    print(pid, flush=True)\n"
            "    os._exit(0)\n"
            "os.setsid()\n"
            "os.execv(sys.argv[1], "
            "[sys.argv[1], 'unixsocket:' + sys.argv[2]])\n"
        ), stub, socket_arg],
        stdout=_subprocess.PIPE, text=True,
    )
    stub_pid = int(spawner.stdout.readline().strip())
    spawner.wait(timeout=10)
    return stub_pid


def test_foreign_live_redis_server_is_not_proven_and_not_signalled(
        tmp_path, monkeypatch):
    """#4879 review: the two PROVENANCE legs must reject a live foreign server.

    `test_unproven_recorded_pid_is_not_signalled_and_nothing_is_started`
    records THIS test process, so `_proven_stale_holder_pid` returns at the
    earlier `_pid_is_redis` gate and never reaches the start-time or
    argv-binding legs — mutating either leg to a constant left the suite
    green. Here the recorded pid IS a live redis-server (a stub whose argv
    names a ``redis-server`` under a directory of this test's own choosing —
    deliberately NOT ``redislite/bin/redis-server``, see
    `_spawn_redis_server_stub`), the pidfile is written AFTER it starts, so the
    start-time leg PASSES, and only the argv-binding leg can refuse the match.

    What a wrong match would cost is NOT a doubled writer: this RDB has no live
    writer at all (`_live_rdb_writers(...) == []`, asserted below), so
    accepting the stub would kill an innocent process and start the FIRST
    server over this RDB. The two-writer divergence `#4879` exists to prevent is
    asserted by the tests that DO hold a live holder, not by this one.
    """
    import atexit
    import json as _json
    import shutil
    import tempfile

    import redis
    from redislite.client import RedisMixin

    from tortoise import embedded_reaper as _reaper

    # A fresh cache: a stale discover() sweep entry for this pid would answer
    # `_pid_is_redis`/`_process_start_time` for a different process and make
    # this test vacuous. Via monkeypatch so the module-global rebind is undone
    # at teardown (restoring the ORIGINAL dict, and dropping this test's
    # entries with it) — a bare assignment would hand a rebound cache to every
    # later test in the process.
    monkeypatch.setattr(_reaper, "_PROC_INFO_CACHE", {})

    # Both paths must be short enough for AF_UNIX: macOS rejects >~104 bytes
    # with ENAMETOOLONG, masking the ENOENT this state actually produces.
    sock_dir = tempfile.mkdtemp(prefix="t4879_foreign_")
    stub_dir = tempfile.mkdtemp(prefix="t4879_stub_")
    db_path = tmp_path / "foreign_holder.db"
    registry = Path(str(db_path) + ".settings")
    dead_socket = os.path.join(sock_dir, "redis.socket")  # never created
    # The stub's argv names a DIFFERENT directory than the recorded socket's.
    other_socket = os.path.join(stub_dir, "elsewhere", "redis.socket")
    stub_pid = _spawn_redis_server_stub(stub_dir, other_socket)
    pidfile = tmp_path / "redis.pid"
    leaked = None
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not _reaper._pid_is_redis(stub_pid):
            time.sleep(0.1)
        assert _reaper._pid_is_redis(stub_pid), (
            "test setup: the stub must be live and read as a redis-server")

        # Written AFTER the stub starts, so the start-time leg PASSES: this
        # test must fail on the ARGV-binding leg alone.
        pidfile.write_text(str(stub_pid))
        registry.write_text(_json.dumps({
            "pidfile": str(pidfile),
            "unixsocket": dead_socket,
            "dbdir": str(tmp_path),
            "dbfilename": db_path.name,
        }))
        assert not os.path.exists(dead_socket), "the recorded socket must be DEAD"

        with pytest.raises(redis.exceptions.ConnectionError) as excinfo:
            FalkorDB(str(db_path))
        assert dead_socket in str(excinfo.value)
        assert _pid_alive(stub_pid), (
            "#4879: a live redis-server whose argv names a DIFFERENT directory "
            "is not this registry's holder and must never be signalled")
        assert _live_rdb_writers(tmp_path, db_path.name) == [], (
            "#4879: the unproven branch must not start a server for this RDB")
        assert registry.exists(), "#4879: the foreign registry is left untouched"

        # Neutralise the partially-built client's own atexit `_cleanup` that
        # the raising construction registered (client.py:448) — same repair as
        # the sibling unproven-pid test: `pidfile = None` makes it a no-op.
        for entry in excinfo.traceback:
            candidate = entry.frame.f_locals.get("self")
            if isinstance(candidate, RedisMixin):
                leaked = candidate
                break
        assert leaked is not None, "no partially-built client to neutralise"
    finally:
        if leaked is not None:
            atexit.unregister(leaked._cleanup)
            leaked.pidfile = None
        with contextlib.suppress(OSError):
            os.killpg(os.getpgid(stub_pid), _signal.SIGKILL)
        shutil.rmtree(sock_dir, ignore_errors=True)
        shutil.rmtree(stub_dir, ignore_errors=True)


def test_recycled_pid_started_after_the_pidfile_is_not_signalled(
        tmp_path, monkeypatch):
    """#4879 review: the START-TIME leg must refuse a recycled pid.

    The sibling foreign-argv test pins the argv-binding leg; this pins the
    start-time leg. The stub's argv DOES name the recorded socket's
    directory (so the argv leg would accept it), but its pidfile mtime is
    back-dated BEFORE the process started — exactly the recycled-pid shape
    (#1642 FIX 5): the live redis-server is not the process that wrote this
    pidfile. Provenance must refuse, the stub must not be signalled, and
    nothing may start.
    """
    import atexit
    import json as _json
    import shutil
    import tempfile

    import redis
    from redislite.client import RedisMixin

    from tortoise import embedded_reaper as _reaper

    # monkeypatch (not a bare rebind) so the module-global cache is restored at
    # teardown — see the sibling foreign-argv test.
    monkeypatch.setattr(_reaper, "_PROC_INFO_CACHE", {})

    sock_dir = tempfile.mkdtemp(prefix="t4879_recycled_")
    stub_dir = tempfile.mkdtemp(prefix="t4879_stub_")
    db_path = tmp_path / "recycled_holder.db"
    registry = Path(str(db_path) + ".settings")
    # The recorded socket is NEVER created, and the stub's argv names THIS
    # exact path — so the argv-binding leg PASSES; only start time can refuse.
    dead_socket = os.path.join(sock_dir, "redis.socket")
    stub_pid = _spawn_redis_server_stub(stub_dir, dead_socket)
    pidfile = tmp_path / "redis.pid"
    leaked = None
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not _reaper._pid_is_redis(stub_pid):
            time.sleep(0.1)
        assert _reaper._pid_is_redis(stub_pid), (
            "test setup: the stub must be live and read as a redis-server")
        # The pidfile is back-dated an hour (the stub started seconds ago), so
        # the live pid cannot be the process that wrote it (recycled-pid
        # shape). Computed WITHOUT the helper under test so the mutation run
        # still reaches the provenance path instead of erroring in setup.
        pidfile.write_text(str(stub_pid))
        backdated = time.time() - 3600
        os.utime(pidfile, (backdated, backdated))
        assert os.path.getmtime(pidfile) < time.time() - 1800, (
            "test setup: the pidfile must predate the stub")
        registry.write_text(_json.dumps({
            "pidfile": str(pidfile),
            "unixsocket": dead_socket,
            "dbdir": str(tmp_path),
            "dbfilename": db_path.name,
        }))
        assert not os.path.exists(dead_socket), "the recorded socket must be DEAD"

        with pytest.raises(redis.exceptions.ConnectionError) as excinfo:
            FalkorDB(str(db_path))
        assert dead_socket in str(excinfo.value)
        assert _pid_alive(stub_pid), (
            "#4879: a pid that started AFTER its pidfile was written is a "
            "recycled number and must never be signalled")
        assert _live_rdb_writers(tmp_path, db_path.name) == [], (
            "#4879: the unproven branch must not start a server for this RDB")
        assert registry.exists(), "#4879: the registry is left untouched"

        for entry in excinfo.traceback:
            candidate = entry.frame.f_locals.get("self")
            if isinstance(candidate, RedisMixin):
                leaked = candidate
                break
        assert leaked is not None, "no partially-built client to neutralise"
    finally:
        if leaked is not None:
            atexit.unregister(leaked._cleanup)
            leaked.pidfile = None
        with contextlib.suppress(OSError):
            os.killpg(os.getpgid(stub_pid), _signal.SIGKILL)
        shutil.rmtree(sock_dir, ignore_errors=True)
        shutil.rmtree(stub_dir, ignore_errors=True)


def test_registry_rewritten_during_the_stop_window_is_left_alone(
        tmp_path, monkeypatch):
    """#4879 review: the registry is re-validated at the last moment.

    Between the registry read and ``os.remove`` the repair can spend ~10 s
    (``_STALE_HOLDER_SIGTERM_TIMEOUT + _STALE_HOLDER_DEATH_TIMEOUT``) stopping
    the proven holder. A concurrent construction on this exact
    ``<dbdir>/<dbfilename>`` can, in that window, stop the same holder and
    install a NEW, live registry. Removing THAT would make this construction
    start a second writer over the same RDB — the divergence the patch exists
    to prevent. The repair must re-read the registry, leave the fresh record
    alone, and take the LOUD branch instead.
    """
    import json as _json

    import tortoise.embedded_lifecycle as _lifecycle

    db_path = tmp_path / "revalidation.db"
    registry = Path(str(db_path) + ".settings")
    first = FalkorDB(str(db_path))
    recorded = _json.loads(registry.read_text())
    dead_socket = recorded["unixsocket"]
    holder_pid = int(Path(recorded["pidfile"]).read_text().strip())
    assert _pid_alive(holder_pid), "the recorded holder must be live"

    installed = {}
    fresh_clients = []
    real_stop = _lifecycle._stop_proven_holder

    def _stop_then_install_fresh_registry(pid):
        # The concurrent construction (same <dbdir>/<dbfilename>) stops the
        # same holder and installs its own live server + registry inside our
        # stop window. The holder is gone by the time the nested predicate
        # runs, so it starts fresh rather than re-entering this repair.
        result = real_stop(pid)
        fresh_clients.append(FalkorDB(str(db_path)))
        installed.update(_json.loads(registry.read_text()))
        return result

    monkeypatch.setattr(_lifecycle, "_stop_proven_holder",
                        _stop_then_install_fresh_registry)

    os.remove(dead_socket)
    assert not os.path.exists(dead_socket)

    second = None
    try:
        second = FalkorDB(str(db_path))
        assert installed, "test setup: the concurrent holder must be installed"
        installed_pid = int(Path(installed["pidfile"]).read_text().strip())
        assert installed["pidfile"] != recorded["pidfile"], (
            "test setup: the concurrent construction must install a NEW registry")
        current = _json.loads(registry.read_text())
        assert current == installed, (
            "#4879: the registry installed by the concurrent construction was "
            "removed/replaced — it must be left ALONE, not unlinked")
        writers = _live_rdb_writers(tmp_path, db_path.name)
        assert writers == [installed_pid], (
            f"#4879: exactly the concurrent holder (pid {installed_pid}) must "
            f"write this RDB; found {writers} — a second writer was started "
            "over a live holder's RDB")
    finally:
        for client in [second, *fresh_clients, first]:
            if client is not None:
                with contextlib.suppress(Exception):
                    client._t_close()


# ── #4879: the last-client decision must see a MID-CONSTRUCTION co-tenant ──
#
# `cotenant_holds_server`'s in-process branch reads `_owner_refcounts`, which
# the #4487 `RedisMixin.__init__` patch only increments AFTER `original(...)`
# returns. A construction still INSIDE `original(...)` — `socket_file`
# assigned from the registry (client.py:376), ping not yet attempted
# (client.py:471) — is therefore invisible, and the CI shard's ordering turns
# that into a teardown:
#
#   1. `test_pack_state.py:791` builds `TortoiseSDK(db_path=...).org_create(...)`
#      as a TEMPORARY; its projection survives only through the reference cycle
#      `proj.g -> _GuardedGraph -> proj` (tortoise/projection/__init__.py), so
#      it is refcount-unreachable but cycle-held.
#   2. `:794/:795` constructs again on the SAME `db_path`. The registry exists
#      and the pid is live, so redislite takes the replay branch and
#      `_load_setting_registry()` assigns construction #2's `socket_file`.
#   3. INSIDE that window a cyclic-GC pass collects the leaked projection ->
#      `weakref.finalize` -> `_gc_close` -> SHUTDOWN + `shutil.rmtree`. The
#      dying client read `owner_refcounts={<socket>: 1}` as "last client"
#      because construction #2 had not recorded yet.
#   4. The socket construction #2 is about to ping is unlinked ->
#      `ConnectionError: Error 2 connecting to /tmp/tmpXXXX/redis.socket. No
#      such file or directory.`
#
# The test below drives step 3 DETERMINISTICALLY — a `gc.collect()` inside the
# replay window instead of waiting for CPython's allocator to cross a GC
# threshold — and pins the pre-fix read (`refcount == 1`, i.e. the refcount
# branch ALONE cannot see the co-tenant) as well as the outcome.


def test_midconstruction_replay_is_a_cotenant_the_last_client_must_see(
        tmp_path, monkeypatch):
    """#4879: count a co-tenant that is still MID-REPLAY before it attaches.

    The ORDERING assertion — the in-flight claim exists at attach — runs
    INSIDE the replay window (the registry has adopted the socket; the ping
    has not run), so a missing claim is the RED signal. Without the fix the
    construction instead dies downstream with the ``Error 2 connecting to
    .../redis.socket. No such file or directory`` above — the SYMPTOM of the
    missing claim, not the ordering itself. GREEN: the claim registered
    BEFORE ``original(...)`` makes the guard read "shared", so the socket
    survives the collection and construction #2 is served.
    """
    import json as _json

    from redislite.client import RedisMixin

    import tortoise.embedded_lifecycle as _lifecycle
    from tortoise.sdk import TortoiseSDK

    db_path = str(tmp_path / "midconstruction_replay.db")
    # Construction #1, leaked EXACTLY as test_pack_state.py:791 leaks it: the
    # SDK is built as an unbound temporary and only its projection's reference
    # cycle keeps the server side alive, so a gc.collect() can take it.
    created = TortoiseSDK(db_path=db_path).org_create("LegacyCo")
    sock = _json.loads(Path(db_path + ".settings").read_text())["unixsocket"]
    key = os.path.abspath(sock)
    assert os.path.exists(sock), "test setup: construction #1 must be live"

    real_load = RedisMixin._load_setting_registry
    seen: dict = {}

    def _load_then_collect(self):
        real_load(self)
        # client.py:376 has just assigned `self.socket_file` from the registry;
        # the ping (client.py:471) has NOT run. This is the defect's window.
        seen["refcount"] = _lifecycle._owner_refcounts.get(key, 0)
        # `getattr` so the same test runs against a tree WITHOUT the fix and
        # fails on the OUTCOME (the ConnectionError), not on an absent symbol.
        seen["inflight"] = getattr(
            _lifecycle, "_in_flight_replays", {}).get(key, 0)
        seen["replayed"] = os.path.abspath(self.socket_file or "")
        # ── THE ORDERING ASSERTION ─────────────────────────────────────
        # Evaluated HERE — after the registry adopted the socket, before the
        # ping and before `gc.collect()` can let anything unlink it — so the
        # RED signal is the missing ordering claim, NOT the downstream
        # ConnectionError. (`getattr` keeps the test importable on a tree
        # WITHOUT the fix; there the assertion is what fails, on the ordering.)
        assert seen["inflight"] >= 1, (
            "#4879: inflight claim missing at attach — the construction must "
            "register its claim BEFORE `original(...)` can block; the "
            "refcount branch alone cannot see it")
        gc.collect()  # collect the leaked construction #1 HERE, not on luck
        seen["socket_after_gc"] = os.path.exists(sock)

    monkeypatch.setattr(RedisMixin, "_load_setting_registry", _load_then_collect)

    second = TortoiseSDK(db_path=db_path, namespace=created["id"])
    try:
        # ── second half: the OUTCOMES (construction succeeds, server live) ──
        proj = second._get_proj()
        client = getattr(proj.db, "client", proj.db)
        assert seen.get("replayed") == key, (
            "#4879: test setup — construction #2 must take the replay branch")
        assert seen["refcount"] <= 1, (
            "#4879: test setup — the owner refcount alone sees only the leaked "
            "client's claim, which is exactly why the guard read 'last client'")
        assert seen["socket_after_gc"], (
            "#4879: collecting the leaked client removed the socket the "
            "in-flight construction had already adopted (the CI failure)")
        assert client.ping(), "construction #2 must be served by a live server"
        assert _pid_alive(client.pid), "the replayed server must still be live"
        assert os.path.exists(sock), "the replayed socket must still exist"
        assert not getattr(_lifecycle, "_in_flight_replays", {}).get(key), (
            "#4879: the in-flight claim must be RELEASED when the construction "
            "finishes — a stale claim would make every later teardown read "
            "'shared' and pin this server (and its socket dir) forever")
    finally:
        with contextlib.suppress(Exception):
            second.close()


# ── #4879 F1: a fork inherits no THREAD, so it must inherit no CLAIM ──────


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork only")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")  # fork in pytest
def test_fork_midconstruction_drops_the_inherited_in_flight_claim(
        tmp_path, monkeypatch):
    """#4879 F1: a forked child has NO thread to release an inherited claim.

    The claim is registered by a thread inside `RedisMixin.__init__`. Fork
    copies the map into the child, but that thread does not exist there — so
    the child could never release the claim, `cotenant_holds_server` would
    read "co-tenant" forever, and the socket would never be torn down (a
    leaked server + socket dir). The fork hook must clear it exactly like the
    inherited refcounts.

    The fork happens INSIDE the replay window (after `_load_setting_registry()`
    adopted the socket), so the parent's claim is genuinely in flight; the
    assertion runs in the CHILD, which then `os._exit`s.
    """
    import json as _json

    from redislite.client import RedisMixin

    import tortoise.embedded_lifecycle as _lifecycle
    from tortoise.sdk import TortoiseSDK

    db_path = str(tmp_path / "fork_midconstruction.db")
    created = TortoiseSDK(db_path=db_path).org_create("ForkCo")
    sock = _json.loads(Path(db_path + ".settings").read_text())["unixsocket"]
    key = os.path.abspath(sock)

    real_load = RedisMixin._load_setting_registry
    outcome: dict = {}
    state = {"forked": False}
    # #4926: the parent's construction published an ON-DISK claim before the
    # replay could block. Its filename names the PARENT's pid, so the child
    # must NOT retract it (the parent's construction is still in flight) —
    # the deliberate asymmetry with `_in_flight_replays` above.
    claim_path = _lifecycle._inflight_claim_path(key)

    def _load_then_fork(self):
        real_load(self)
        if state["forked"]:
            return
        state["forked"] = True
        outcome["parent_claim"] = dict(_lifecycle._in_flight_replays)
        outcome["parent_claim_file"] = os.path.exists(claim_path)
        # #4926: hold the claim lock ACROSS the fork. The at-fork hook must
        # REPLACE it in the child — an inherited held lock would deadlock the
        # child's next construction. `held_lock` keeps the object even after
        # the child's hook rebinds the module global.
        held_lock = _lifecycle._inflight_claim_lock
        held_lock.acquire()
        read_fd, write_fd = os.pipe()
        try:
            pid = os.fork()
        except BaseException:
            held_lock.release()
            raise
        if pid == 0:  # child — no thread here can ever release a claim
            os.close(read_fd)
            try:
                child_claims = dict(_lifecycle._in_flight_replays)
                # THE ASSERTIONS ARE IN THE CHILD.
                assert child_claims == {}, (
                    "#4879 F1: forked child inherited in-flight replay claim "
                    f"{child_claims!r} — no thread exists here to release it, "
                    "so the socket would never be torn down")
                assert os.path.exists(claim_path), (
                    "#4926: the forked child must NOT retract the ON-DISK "
                    "mid-construction claim — its filename names the PARENT's "
                    "pid and the parent's construction is still in flight; "
                    "unlinking it would drop a live construction's "
                    "cross-process signal")
                assert _lifecycle._inflight_claim_lock is not held_lock, (
                    "#4926: the at-fork hook must REPLACE the inherited claim "
                    "lock — we forked holding it, so an inherited lock would "
                    "deadlock the child's next construction")
                assert _lifecycle._inflight_claim_lock.locked() is False, (
                    "#4926: the child's claim lock must be unlocked")
                os.write(write_fd, b"OK")
            except BaseException as exc:
                with contextlib.suppress(Exception):
                    os.write(write_fd, f"FAIL: {exc!r}".encode())
            finally:
                with contextlib.suppress(OSError):
                    os.close(write_fd)
                os._exit(0)
        held_lock.release()
        os.close(write_fd)
        child_result = os.read(read_fd, 65536).decode()
        os.close(read_fd)
        _, status = os.waitpid(pid, 0)
        outcome["child_result"] = child_result
        outcome["child_exit"] = os.waitstatus_to_exitcode(status)

    monkeypatch.setattr(RedisMixin, "_load_setting_registry", _load_then_fork)

    second = TortoiseSDK(db_path=db_path, namespace=created["id"])
    try:
        second._get_proj()
        assert outcome.get("parent_claim") == {key: 1}, (
            "#4879 F1: test setup — the parent must hold exactly one in-flight "
            f"claim at fork time, got {outcome.get('parent_claim')!r}")
        assert outcome.get("parent_claim_file") is True, (
            "#4926: test setup — the parent's ON-DISK claim must exist at fork "
            "time, so the child has something to (deliberately) leave alone")
        assert outcome.get("child_result") == "OK", (
            "#4879 F1: the forked child must observe NO inherited in-flight "
            f"claim, got {outcome.get('child_result')!r}")
        assert outcome.get("child_exit") == 0, (
            "#4879 F1: the fork child must exit cleanly, got "
            f"{outcome.get('child_exit')!r}")
    finally:
        with contextlib.suppress(Exception):
            second.close()


# ── #4879 F2: the replay gate line is a CONSTRUCTION claim, not socket_file ──


def test_close_path_with_empty_socket_file_emits_no_replay_warning(
        tmp_path, caplog):
    """#4879 F2: a close-path call with an empty `socket_file` must not log.

    redislite nulls `socket_file` in `_cleanup` (client.py:146) before
    `pidfile` (client.py:181), so a mid-teardown `_cleanup` ->
    `_connection_count` -> `_is_redis_running` also has an empty
    `socket_file`. The old `socket_file`-empty proxy logged a replay that
    client was never part of; gating on the live in-flight claim does not.

    Captured at DEBUG, not WARNING: the gate line is DEBUG-only, so a
    WARNING-level capture could no longer see the record this test exists to
    prove absent.
    """
    import json as _json

    import tortoise.embedded_lifecycle as _lifecycle
    from tortoise.sdk import TortoiseSDK

    db_path = str(tmp_path / "close_path_no_replay_warning.db")
    sdk = TortoiseSDK(db_path=db_path)
    try:
        sdk.org_create("ClosePathCo")
        proj = sdk._get_proj()
        client = getattr(proj.db, "client", proj.db)
        assert client.ping(), "test setup: the server must be live"
        sock = _json.loads(
            Path(db_path + ".settings").read_text())["unixsocket"]
        assert os.path.exists(sock)
        assert not _lifecycle._in_flight_replays, (
            "#4879 F2: test setup — no claim may be live before the close path")
        # The mid-teardown shape: `socket_file` already nulled, `pidfile` not.
        client.socket_file = None
        caplog.clear()
        caplog.set_level("DEBUG", logger="tortoise.embedded_lifecycle")
        client._cleanup()
        offenders = [
            record.getMessage() for record in caplog.records
            if "#4879: replay allowed" in record.getMessage()
        ]
        assert offenders == [], (
            "#4879 F2: a close-path `_cleanup` with an empty `socket_file` "
            f"must emit ZERO replay warnings, got {offenders!r}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_staged_but_released_claim_emits_no_replay_warning(tmp_path, caplog):
    """#4879 F2: the warning gate needs a LIVE claim, not just a claim KEY.

    `_tortoise_inflight_replay_key` is stashed on a construction that WILL
    replay and is never cleared, so a finished construction still carries the
    key after its claim was released in the patch's `finally`. A later
    close-path `_cleanup` on that client (with `socket_file` nulled) then has
    `claim_key` truthy and `_tortoise_replay_logged` still False, yet its
    claim is NOT live; only the `_in_flight_replays.get(...) > 0` conjunct
    stops the warning.

    That state is built NATURALLY here: a registry that parses and names a
    socket but carries NO `pidfile` makes `_replay_socket_for_init` resolve
    the socket (the claim registers and the key is stashed) while
    `_is_redis_running`'s shape guard answers False before `_allow_replay`,
    so the construction starts a fresh server and never logs. RED (liveness
    conjunct deleted): this close path emits one `#4879: replay allowed`.
    The capture is DEBUG because the gate line is DEBUG-only.
    """
    import json as _json

    import tortoise.embedded_lifecycle as _lifecycle
    from tortoise import FalkorDB

    db_path = tmp_path / "staged_claim.db"
    staged_socket = str(tmp_path / "dead-4879.socket")
    # A registry with `unixsocket` but NO `pidfile`: `_replay_socket_for_init`
    # resolves it (claim registered, key stashed) while the guard's shape
    # check returns False before any `_allow_replay` — the construction
    # starts clean.
    (tmp_path / "staged_claim.db.settings").write_text(
        _json.dumps({"unixsocket": staged_socket}))
    client = FalkorDB(str(db_path))
    try:
        live_socket = client.client.socket_file
        assert live_socket and live_socket != staged_socket, (
            "test setup: the pidfile-less registry must have started a FRESH "
            "server, not replayed the hand-made one")
        staged = client.client._tortoise_inflight_replay_key
        assert staged == os.path.abspath(staged_socket), (
            "#4879 F2: test setup — the construction must carry a STAGED "
            f"claim key, got {staged!r}")
        assert not _lifecycle._in_flight_replays.get(staged, 0), (
            "#4879 F2: test setup — the staged claim must be NO LONGER LIVE")
        assert not getattr(client.client, "_tortoise_replay_logged", False), (
            "#4879 F2: test setup — the construction must not have logged")
        # The mid-teardown shape: `socket_file` already nulled, `pidfile` not.
        client.client.socket_file = None
        caplog.clear()
        caplog.set_level("DEBUG", logger="tortoise.embedded_lifecycle")
        client.client._cleanup()
        offenders = [
            record.getMessage() for record in caplog.records
            if "#4879: replay allowed" in record.getMessage()
        ]
        assert offenders == [], (
            "#4879 F2: a client whose staged claim is NO LONGER LIVE must "
            "emit ZERO replay warnings on the close path (only the "
            f"live-claim conjunct stops it), got {offenders!r}")
    finally:
        with contextlib.suppress(Exception):
            client._t_close()


# ── #4879 regression: the gate line is DEBUG-only, never a WARNING ────────


def test_replay_gate_line_is_debug_and_never_warning(tmp_path, caplog):
    """#4879 regression: the replay this test drives must emit NOTHING at WARNING.

    As a WARNING the gate line collided with the `caplog` filter of an
    UNRELATED test (#4954): at the time,
    `tests/test_metering.py::TestThresholdEvents::test_no_threshold_for_free_tier`
    filtered every captured record by the bare substring "threshold", and
    pytest names that test's tmpdir `test_no_threshold_for_free_tie0`, so the
    registry PATH embedded in the line matched it. (#4957/#4964 has since
    scoped that capture to the `tortoise.metering` logger.)

    The test asserts both:
    (a) the replay path this test drives emits ZERO
        `tortoise.embedded_lifecycle` records at WARNING, and
    (b) the same flow DOES emit the gate line at DEBUG.

    RED without the fix (gate line at WARNING): (a) captures the line and
    the first assertion fails, naming it.
    """
    import json as _json

    from tortoise.sdk import TortoiseSDK

    db_path = str(tmp_path / "gate_level.db")
    first = TortoiseSDK(db_path=db_path)
    second = None
    third = None
    try:
        first.org_create("GateLevelCo")
        sock = _json.loads(
            Path(db_path + ".settings").read_text())["unixsocket"]
        assert os.path.exists(sock), "test setup: server #1 must be live"

        # (a) The replay path is SILENT at WARNING.
        caplog.clear()
        with caplog.at_level(logging.WARNING,
                             logger="tortoise.embedded_lifecycle"):
            second = TortoiseSDK(db_path=db_path, namespace="gate-level")
            second._get_proj()
        warnings = [
            record for record in caplog.records
            if record.name == "tortoise.embedded_lifecycle"
        ]
        assert warnings == [], (
            "#4879: the replay this test drives must emit NO WARNING from "
            "embedded_lifecycle; "
            f"got {[(r.levelname, r.getMessage()) for r in warnings]!r}")

        # (b) ...and the gate line IS emitted, at DEBUG.
        caplog.clear()
        with caplog.at_level(logging.DEBUG,
                             logger="tortoise.embedded_lifecycle"):
            third = TortoiseSDK(db_path=db_path, namespace="gate-level-2")
            third._get_proj()
        gate = [
            record for record in caplog.records
            if record.levelno == logging.DEBUG
            and "#4879: replay allowed" in record.getMessage()
        ]
        assert gate, (
            "#4879: the gate line must still be emitted at DEBUG — "
            "the replay this test drives must say which gate allowed it")
        assert any("recorded-socket-present" in r.getMessage() for r in gate), (
            "#4879: the gate that allowed this replay must be named, got "
            f"{[r.getMessage() for r in gate]!r}")
    finally:
        with contextlib.suppress(Exception):
            if third is not None:
                third.close()
        with contextlib.suppress(Exception):
            if second is not None:
                second.close()
        with contextlib.suppress(Exception):
            first.close()


# ── #4879 F4: claim registration mirrors redislite's replay shape ─────────


def test_replay_socket_for_init_shape_table(tmp_path, monkeypatch):
    """#4879 F4: only a construction that WILL replay may register a claim.

    Derived by mirroring redislite's own registry-path derivation
    (client.py:415-449). In particular `Redis(path, dbfilename=None)` must
    register NOTHING: redislite overrides the positional filename with the
    `dbfilename` KEYWORD unconditionally (client.py:427-428), so that shape
    never sets `settingregistryfile` and never replays.
    """
    import json as _json

    import tortoise.embedded_lifecycle as _lifecycle

    monkeypatch.chdir(tmp_path)
    db_name = "shape.db"
    db_path = tmp_path / db_name
    sock = "/tmp/shape-4879.socket"
    (tmp_path / (db_name + ".settings")).write_text(
        _json.dumps({"unixsocket": sock, "pidfile": "/tmp/shape-4879.pid"}))
    existing = os.path.abspath(sock)
    missing = tmp_path / "missing.db"

    cases = [
        ("positional path, registry present",
         (str(db_path),), {}, existing),
        ("dbfilename keyword, registry present",
         (), {"dbfilename": str(db_path)}, existing),
        ("dbfilename=None overrides a positional path (client.py:427)",
         (str(db_path),), {"dbfilename": None}, None),
        ("host= is server mode (no embedded child)",
         (), {"host": "localhost"}, None),
        ("port= is server mode (no embedded child)",
         (), {"port": 6379}, None),
        ("explicit unix_socket_path disables the registry-load branch",
         (str(db_path),), {"unix_socket_path": sock}, None),
        ("bare basename resolves relative to cwd",
         (db_name,), {}, existing),
        ("registry missing -> no socket to replay",
         (str(missing),), {}, None),
        ("bytes dbfilename never raises",
         (), {"dbfilename": os.fsencode(str(db_path))}, None),
        ("no filename at all",
         (), {}, None),
    ]
    for label, args, kwargs, expected in cases:
        got = _lifecycle._replay_socket_for_init(args, kwargs)
        assert got == expected, (
            f"#4879 F4: shape {label!r} -> {got!r}, expected {expected!r}")


# ── #4879 F3: a failed owner hand-off fails CLOSED (claim kept) ───────────


def test_owner_handoff_failure_keeps_the_in_flight_claim(
        tmp_path, monkeypatch):
    """#4879 F3: a failed owner hand-off must fail CLOSED (claim kept).

    `record_owner` is documented never-raise, so this forces the latent path:
    the client is LIVE but UNRECORDED, and the last-client decision must not
    become blind to it. Dropping the claim there would re-open the #3653
    window the claim exists to close; keeping it only costs a socket dir left
    for the reaper. The claim is released only when `original(...)` aborted or
    the owner record was written.
    """
    import json as _json

    import tortoise.embedded_lifecycle as _lifecycle
    from tortoise.sdk import TortoiseSDK

    db_path = str(tmp_path / "owner_handoff_failure.db")
    first = TortoiseSDK(db_path=db_path)
    second = None
    key = None
    # #4926 review: initialise the captured server BEFORE the `try`, so a
    # failure above the capture cannot turn the `finally` into an
    # `UnboundLocalError` that masks the real failure and skips the explicit
    # stop.
    server_pid = None
    try:
        first.org_create("HandoffCo")
        sock = _json.loads(
            Path(db_path + ".settings").read_text())["unixsocket"]
        key = os.path.abspath(sock)
        # #4926 review: `first.close()` below CANNOT reap — `record_owner` is
        # monkeypatched and the claim is retracted, so `cotenant_holds_server`
        # reads no owner evidence and fail-CLOSEDs. Capture the server so the
        # teardown can stop it explicitly instead of leaking an orphan the
        # reaper cannot see (its socket dir is gone).
        server_pid = _registry_redis_pid(db_path)

        def _explode(_socket_file):
            raise OSError("forced owner-record failure")

        monkeypatch.setattr(_lifecycle, "record_owner", _explode)
        second = TortoiseSDK(db_path=db_path, namespace="handoff")
        proj = second._get_proj()
        client = getattr(proj.db, "client", proj.db)
        assert client.ping(), (
            "#4879 F3: a failed owner hand-off must not break construction")
        assert _lifecycle._in_flight_replays.get(key, 0) >= 1, (
            "#4879 F3: the in-flight claim must be KEPT when the owner "
            "hand-off failed — the client is live but unrecorded")
        # #4926: the fail-CLOSED keep must hold for the ON-DISK half too —
        # for a co-tenant in another process it is the ONLY signal that this
        # live-but-unrecorded client exists.
        assert os.path.exists(_lifecycle._inflight_claim_path(key)), (
            "#4926: a failed owner hand-off must KEEP the on-disk claim "
            "alongside the in-memory one, or a cross-process reader loses "
            "the unrecorded live client (#3653)")
    finally:
        with contextlib.suppress(Exception):
            if second is not None:
                second.close()
        # Drop the deliberately-stuck claim BEFORE closing the last client
        # (the claim is exactly what would otherwise pin it forever). #4926:
        # the claim now has an on-disk half too, and the production release
        # path is the only thing that retracts it — so retract it here
        # explicitly.
        if key is not None:
            _lifecycle._in_flight_replays.pop(key, None)
            _lifecycle._retract_inflight_claim(key)
        with contextlib.suppress(Exception):
            first.close()
        # #4926 review: with the claim gone and `record_owner` monkeypatched,
        # the guard above fail-CLOSEDs and `first.close()` does NOT reap — so
        # stop the server explicitly (see the capture comment in `try`).
        if server_pid and _pid_alive(server_pid):
            _kill_quiet(server_pid)
        if server_pid:
            _wait_server_dead(server_pid)


def test_owner_handoff_returning_false_keeps_the_claim_and_the_guard(
        tmp_path, monkeypatch):
    """#4879 F3 (review): `record_owner` does NOT raise on its documented
    failures — it RETURNS False (`os.makedirs`/`os.open` OSError,
    embedded_lifecycle.py). On those paths `_owner_refcounts[key]` is NOT
    incremented, so the client is LIVE but UNRECORDED and the claim must be
    KEPT (fail CLOSED), exactly as when the hand-off raises. The sibling test
    forces the `raise` path, which the never-raise contract makes latent;
    this one forces the REAL failure mode.

    Two assertions, mechanism and verdict:
    (a) the in-flight claim survives the ignored-failure path, and
    (b) the last-client decision (`cotenant_holds_server`) reports the
        unrecorded live client as a co-tenant. The peer's pool is dropped
        first (the client itself stays live) so a raw CLIENT LIST can no
        longer see a peer and ONLY the kept claim can answer "shared" —
        without that isolation the CLIENT LIST fallback fails closed on its
        own and the guard verdict would be green even with the claim gone.
    RED (return value ignored): `cotenant_holds_server` returns False.
    """
    import json as _json

    import tortoise.embedded_lifecycle as _lifecycle
    from tortoise.embedded_lifecycle import cotenant_holds_server
    from tortoise.sdk import TortoiseSDK

    db_path = str(tmp_path / "owner_handoff_false.db")
    first = TortoiseSDK(db_path=db_path)
    second = None
    key = None
    # #4926 review: see the sibling raise-path test — initialise BEFORE the
    # `try` so the `finally` can never raise `UnboundLocalError`.
    server_pid = None
    try:
        first.org_create("HandoffFalseCo")
        sock = _json.loads(
            Path(db_path + ".settings").read_text())["unixsocket"]
        key = os.path.abspath(sock)
        # #4926 review: `first.close()` in the teardown cannot reap (see the
        # sibling raise-path test) — capture the server for an explicit stop.
        server_pid = _registry_redis_pid(db_path)

        def _fail_to_record(_socket_file):
            # The documented failure: nothing written, refcount untouched.
            return False

        monkeypatch.setattr(_lifecycle, "record_owner", _fail_to_record)
        second = TortoiseSDK(db_path=db_path, namespace="handoff-false")
        proj = second._get_proj()
        client = getattr(proj.db, "client", proj.db)
        assert client.ping(), (
            "#4879 F3: a falsy owner hand-off must not break construction")
        # (a) MECHANISM — the claim survives the ignored-failure path.
        assert _lifecycle._in_flight_replays.get(key, 0) >= 1, (
            "#4879 F3: `record_owner` returned False (nothing written) — the "
            "in-flight claim must be KEPT; the client is live but unrecorded")
        # ...because no record was written, the refcount branch alone is blind.
        assert _lifecycle._owner_refcounts.get(key, 0) == 1, (
            "#4879 F3: test setup — the falsy hand-off must leave the "
            "refcount at construction #1's single claim")
        # #4926: the on-disk half of the fail-CLOSED keep. See the sibling
        # raise-path test.
        assert os.path.exists(_lifecycle._inflight_claim_path(key)), (
            "#4926: a falsy owner hand-off must KEEP the on-disk claim "
            "alongside the in-memory one")
        # (b) VERDICT — isolate the claim from the CLIENT LIST fallback by
        # dropping the peer's connection (the peer object stays live; only
        # its pool is disconnected), then ask the last-client decision.
        proj1 = first._get_proj()
        peer = getattr(proj1.db, "client", proj1.db)
        _lifecycle.disconnect_only(peer)
        assert cotenant_holds_server(client) is True, (
            "#4879 F3: the last-client decision must read the UNRECORDED live "
            "client as a co-tenant — otherwise the #3653 blind-teardown "
            "window re-opens")
    finally:
        with contextlib.suppress(Exception):
            if second is not None:
                second.close()
        # Drop the deliberately-stuck claim BEFORE closing the last client.
        # #4926: its on-disk half is retracted with it (the production release
        # path is the only other thing that does this).
        if key is not None:
            _lifecycle._in_flight_replays.pop(key, None)
            _lifecycle._retract_inflight_claim(key)
        with contextlib.suppress(Exception):
            first.close()
        # #4926 review: with the claim gone and `record_owner` monkeypatched,
        # `first.close()` fail-CLOSEDs and does NOT reap — stop the server
        # explicitly (see the capture comment in `try`).
        if server_pid and _pid_alive(server_pid):
            _kill_quiet(server_pid)
        if server_pid:
            _wait_server_dead(server_pid)


# ── #4926: the mid-construction claim must be visible CROSS-PROCESS ────
#
# The #4879 fix above closes the ordering hole for co-tenants of the SAME
# process (`_in_flight_replays` is process-local memory). A peer PROCESS that
# redislite sends down the registry-replay branch has adopted this socket and
# is blocked in `_wait_for_server_start` before its first ping — so it holds
# NO owner record (`record_owner` runs only after the hand-off) and NO
# connection. The guard's cross-process evidence therefore reads
# `_owner_records == (1, 1)` and a raw CLIENT LIST sees only its own probe:
# "last client". The destructive branch then SHUTDOWNs the server and rmtrees
# its socket dir under the attaching peer.
#
# A construction publishes the claim on disk for exactly the in-memory claim's
# lifetime (`_publish_inflight_claim` / `_retract_inflight_claim`), and
# `cotenant_holds_server` reads it. The test below drives the interleaving
# deterministically with a stdin barrier at the exact seam — no sleeps, no
# whole-suite context.


def test_cross_process_midconstruction_claim_protects_the_attaching_peer(
        tmp_path):
    """#4926: a peer PROCESS mid-attach is a co-tenant of the last client.

    The child pauses INSIDE its attach window — the registry has been read and
    `self.socket_file` adopted, the first ping has not run — and the parent
    then runs the real production close seam across that window. RED (no
    on-disk claim): the parent's close SHUTDOWNs the server and removes its
    socket dir, and the peer dies on its ping (`ConnectionError: ...
    redis.socket. No such file or directory`). GREEN: the peer attaches and
    the server is still live.

    The mechanism assertions are the point: at the window the peer has NO
    owner record (`_owner_records` counts only the holder) and NO connection,
    so the published claim is the only signal that can answer "not last
    client".
    """
    from tortoise.embedded_lifecycle import (
        _inflight_claim_holds,
        cotenant_holds_server,
    )
    from tortoise.embedded_reaper import _owner_records
    from tortoise.projection import FalkorProjection

    db_path = str(tmp_path / "cross_process_midattach.db")
    proj = FalkorProjection(db_path, graph_name="test")
    inner = getattr(proj.db, "client", proj.db)
    pid, sock = inner.pid, inner.socket_file
    key = os.path.abspath(sock)
    assert sock and _pid_alive(pid), "the holder's server must be live"
    proc = None
    try:
        script = (  # noqa: UP031 - child-script %-template (matches siblings)
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "from redislite.client import RedisMixin\n"
            "_orig_wait = RedisMixin._wait_for_server_start\n"
            "def _paused(self, *a, **k):\n"
            "    print('PAUSED sock=%%s' %% (self.socket_file,), flush=True)\n"
            "    sys.stdin.readline()\n"
            "    return _orig_wait(self, *a, **k)\n"
            "RedisMixin._wait_for_server_start = _paused\n"
            "from tortoise.projection import FalkorProjection\n"
            "p = FalkorProjection(sys.argv[1], graph_name='test')\n"
            "print('ATTACHED', flush=True)\n"
            "sys.stdin.readline()\n"
        ) % _REPO_ROOT
        proc = _subprocess.Popen(
            [sys.executable, "-c", script, db_path],
            stdin=_subprocess.PIPE, stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE, text=True, env=_child_env(),
        )
        paused = _read_child_line(proc, "PAUSED ")
        kv = dict(part.split("=", 1) for part in paused.split()[1:])
        assert kv["sock"] == sock, (
            "test setup: the peer must replay THIS server's socket")

        # ── the mechanism: the peer is invisible to every OTHER signal ──
        assert _owner_records(key) == (1, 1), (
            "test setup: the peer's owner record is written only after its "
            "attach window, so the record branch must see just the holder")
        assert _inflight_claim_holds(key) is True, (
            "#4926: the mid-construction claim must be readable from another "
            "process at the attach window — the peer holds no record yet")

        # ── the verdict, then the REAL close seam across the window ──
        assert cotenant_holds_server(inner) is True, (
            "#4926: the last-client decision must see the attaching peer as a "
            "co-tenant")
        proj.db.close()  # the guarded production close seam
        assert os.path.exists(sock), (
            "#4926: the close removed the socket the peer was attaching to")
        assert _pid_alive(pid), "#4926: the close killed the peer's server"

        # ── the observable: the peer completes its attach ──
        proc.stdin.write("GO\n")
        proc.stdin.flush()
        assert _read_child_line(proc, "ATTACHED", timeout=60).startswith(
            "ATTACHED"), "#4926: the attaching peer never completed"
        assert _inflight_claim_holds(key) is False, (
            "#4926: the claim must be retracted once the peer has attached "
            "(its own owner record now protects it)")
    finally:
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.stdin.write("\n")
                proc.stdin.flush()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        # #4926: the guarded close above deliberately left the server ALIVE
        # (that is the property under test) and neutralised redislite's own
        # teardown of THIS client, so nothing else will stop it. `_t_close()`
        # cannot either: with `pidfile` already nulled it takes the
        # destructive path with `pid_before == 0` and only unlinks the socket
        # dir. A server left here is invisible to the reaper (its socket file
        # AND dir are gone), so it would leak once per run — stop it
        # explicitly.
        if _pid_alive(pid):
            _kill_quiet(pid)
        _wait_server_dead(pid)
        with contextlib.suppress(Exception):
            proj.db._t_close()


def _claim_file_name(lifecycle, key, pid, start) -> str:
    """A claim filename for ``key`` in the exact shape the writer emits."""
    return (f"{lifecycle.OWNER_INFLIGHT_PREFIX}{pid}-{start}"
            f"-{lifecycle._inflight_claim_digest(key)}")


def test_inflight_claim_is_a_liveness_hold_not_an_owner_record(tmp_path):
    """#4926: the claim's artifact contract, one assertion per property.

    (a) a published claim is readable by `_inflight_claim_holds` — and ONLY
        for its own socket (two servers can share one record dir when an
        explicit `unix_socket_path` puts their sockets in one directory);
    (b) BOTH reaper parsers ignore it: `_owner_records` (the `total`/`live`
        arithmetic) and `_owner_record_dir_present` (which feeds
        `_has_ownership_claim` and the `unattributed` flag);
    (c) retraction removes exactly this process's file for this socket and
        reclaims the directory it created;
    (d) the liveness case list mirrors `_owner_records`: a dead pid does not
        hold; a live pid with a DIFFERENT start (recycled pid) does not hold;
        a live pid with an unverifiable start DOES hold (fail closed);
        `pid <= 0` and a non-pid body are foreign and ignored; a missing dir
        does not hold.
    """
    import tortoise.embedded_lifecycle as _lifecycle
    from tortoise.embedded_reaper import (
        _owner_record_dir_present,
        _owner_records,
    )

    sock = str(tmp_path / "redis.socket")
    key = os.path.abspath(sock)
    record_dir = _lifecycle.owner_record_dir(key)
    # A SECOND socket in the SAME directory — the explicit-`unix_socket_path`
    # shape the reaper's `_has_ownership_claim` caveat documents as real.
    other_key = os.path.abspath(str(tmp_path / "other.socket"))
    # This process's own (pid, start) stamp — a claim naming a live pid with
    # a DIFFERENT start is deliberately read as recycled, so the isolation
    # assertions below must use the real one.
    start = _lifecycle._own_start_time()
    start_str = "unknown" if start is None else str(int(start))
    stamp = f"{os.getpid()}-{start_str}"

    # ── (a) publish, and per-socket isolation ──
    _lifecycle._publish_inflight_claim(key)
    assert _lifecycle._inflight_claim_holds(key) is True
    assert _lifecycle._inflight_claim_holds(other_key) is False, (
        "#4926: a claim for one socket must not hold a DIFFERENT server that "
        "merely shares its owner-record directory")
    other_claim = _claim_file_name(
        _lifecycle, other_key, os.getpid(), start_str)
    Path(record_dir, other_claim).write_text("")
    assert _lifecycle._inflight_claim_holds(other_key) is True
    _lifecycle._retract_inflight_claim(key)  # must not touch `other_claim`
    assert Path(record_dir, other_claim).exists(), (
        "#4926: retraction removes only THIS process's claim for ITS socket")

    # ── (b) both reaper parsers ignore the prefix ──
    os.unlink(Path(record_dir, other_claim))
    _lifecycle._retract_inflight_claim(key)
    _lifecycle._publish_inflight_claim(key)
    # A dir holding only a claim reads as "no owner evidence" to both.
    assert _owner_records(key) is None, (
        "#4926: a construction claim must not be counted as an owner record")
    assert _owner_record_dir_present(os.path.dirname(key)) is False, (
        "#4926: a claim must not make a server read as `attributed` by "
        "`_owner_record_dir_present` / `_has_ownership_claim`")
    # ...and with a real record beside it, the claim still adds nothing.
    Path(record_dir, stamp).write_text("")
    assert _owner_records(key) == (1, 1), (
        "#4926: the claim must not inflate the owner count beside a record")
    assert _owner_record_dir_present(os.path.dirname(key)) is True
    assert _lifecycle._inflight_claim_holds(key) is True

    # ── (c) retraction removes the file, then the dir it created ──
    _lifecycle._retract_inflight_claim(key)
    assert _lifecycle._inflight_claim_holds(key) is False
    assert not list(Path(record_dir).glob(
        f"{_lifecycle.OWNER_INFLIGHT_PREFIX}*")), (
        "#4926: retraction must remove this process's claim file")
    os.unlink(Path(record_dir, stamp))
    _lifecycle._retract_inflight_claim(key)
    assert not os.path.isdir(record_dir), (
        "#4926: retraction must reclaim the dir it created")

    # ── (d) the liveness case list ──
    def _plant(name: str) -> None:
        os.makedirs(record_dir, exist_ok=True)
        Path(record_dir, name).write_text("")

    def _clear() -> None:
        with contextlib.suppress(FileNotFoundError):
            for n in os.listdir(record_dir):
                os.unlink(os.path.join(record_dir, n))

    # a genuinely dead pid does not hold.
    dead = _subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    _plant(_claim_file_name(_lifecycle, key, dead.pid, 12345))
    assert _lifecycle._inflight_claim_holds(key) is False, (
        "#4926: a claim whose construction died before attaching must not "
        "pin the server forever (#3599)")
    # a LIVE pid whose recorded START differs -> recycled pid -> not a holder.
    assert start is not None, "test setup: this process has a start time"
    _clear()
    _plant(_claim_file_name(_lifecycle, key, os.getpid(), int(start) + 1000))
    assert _lifecycle._inflight_claim_holds(key) is False, (
        "#4926: a recycled pid must not hold the server — the recorded "
        "construction is provably gone")
    # a LIVE pid with an UNVERIFIABLE start -> LIVE (fail closed).
    for bogus in ("unknown", "not-a-number", "nan", "inf"):
        _clear()
        _plant(_claim_file_name(_lifecycle, key, os.getpid(), bogus))
        assert _lifecycle._inflight_claim_holds(key) is True, (
            f"#4926: an unverifiable start ({bogus!r}) for a live pid must "
            "count LIVE (fail closed), exactly as `_owner_records` treats an "
            "unverifiable owner")
    # `pid <= 0` (the `kill(0, 0)` process-group alias) and a non-pid body
    # behind the prefix are foreign files and are ignored. (A negative-pid
    # body like `inflight--1-…` is ignored too, but through the UNPARSEABLE
    # pid path, not the `pid <= 0` guard, so it is not the case pinned here.)
    for bogus in ("0", "not-a-pid"):
        _clear()
        _plant(_claim_file_name(_lifecycle, key, bogus, 12345))
        assert _lifecycle._inflight_claim_holds(key) is False, (
            f"#4926: a foreign claim body ({bogus!r}) must be ignored")
    _clear()
    _plant(_claim_file_name(_lifecycle, key, "-1", 12345))
    assert _lifecycle._inflight_claim_holds(key) is False, (
        "#4926: a negative-pid body must be ignored via the unparseable-pid "
        "path")
    # a missing owner-record dir is not a hold (the caller fails closed on it
    # through the `_owner_records` branch).
    _clear()
    os.rmdir(record_dir)
    assert _lifecycle._inflight_claim_holds(key) is False


def test_inflight_claim_transition_and_disk_ops_share_one_critical_section(
        tmp_path, monkeypatch):
    """#4926: the map transition and its on-disk op are ONE critical section.

    The disk half's correctness rests on being one step with the
    `_in_flight_replays` transition. Without that, two same-process
    constructions on one socket can lose each other's claim: B starts while
    A's file is still present (`FileExistsError` -> "reuse it"), then A's
    retract unlinks the file B is relying on — measured against the real
    functions (`_in_flight_replays[key] == 1` while
    `_inflight_claim_holds(key) is False`), which re-opens the fail-open
    window this PR closes. A two-thread reproduction is not deterministic, so
    the contract is pinned TWO ways, and the SECOND is the load-bearing one:
    (a) by counting critical-section entries (a construction's
    increment+publish is entry 1, its decrement+retract is entry 2), and (b)
    by asserting the claim lock is actually HELD at the moment each on-disk op
    runs. (b) is what catches the harmful mutant — moving
    `_publish_inflight_claim` (or `_retract_inflight_claim`) OUTSIDE the
    `with` block leaves the entry COUNT unchanged (still 1 and 2) while
    re-opening the fail-open window, in which the map says count > 0 and no
    file exists (or a peer reuses a file this retract then unlinks).
    """
    import tortoise.embedded_lifecycle as _lifecycle
    from tortoise.projection import FalkorProjection

    class _CountingLock:
        """A real lock wrapper that counts entries into the section."""

        def __init__(self, inner):
            self._inner = inner
            self.entries = 0

        def __enter__(self):
            self.entries += 1
            return self._inner.__enter__()

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

        def locked(self):
            return self._inner.locked()

    counter = _CountingLock(_lifecycle._inflight_claim_lock)
    monkeypatch.setattr(_lifecycle, "_inflight_claim_lock", counter)
    calls: dict = {}
    real_publish = _lifecycle._publish_inflight_claim
    real_retract = _lifecycle._retract_inflight_claim

    def _publish(key):
        calls["publish_entries"] = counter.entries
        calls["publish_locked"] = counter.locked()
        return real_publish(key)

    def _retract(key):
        calls["retract_entries"] = counter.entries
        calls["retract_locked"] = counter.locked()
        return real_retract(key)

    monkeypatch.setattr(_lifecycle, "_publish_inflight_claim", _publish)
    monkeypatch.setattr(_lifecycle, "_retract_inflight_claim", _retract)
    db_path = str(tmp_path / "claim_lock.db")
    first = FalkorProjection(db_path, graph_name="test")
    second = None
    try:
        # Construction #1 STARTS the server (no registry yet -> no replay, so
        # it never enters the critical section). Construction #2 REPLAYS it
        # and therefore publishes a claim that must be retracted when it
        # finishes.
        second = FalkorProjection(db_path, graph_name="test")
        assert calls.get("publish_entries") == 1, (
            "#4926: the publish must run in the SAME critical section as the "
            "0 -> 1 transition (entry 1)")
        assert calls.get("publish_locked") is True, (
            "#4926: the publish must run while the claim lock is HELD — moved "
            "outside the `with`, the map says count > 0 while no file exists "
            "(the fail-open window), and the entry COUNT alone cannot see it")
        assert calls.get("retract_entries") == 2, (
            "#4926: the retract must run in the SAME critical section as the "
            "1 -> 0 transition (entry 2)")
        assert calls.get("retract_locked") is True, (
            "#4926: the retract must run while the claim lock is HELD — moved "
            "outside the `with`, a same-process construction can reuse the "
            "file this retract then unlinks")
        assert counter.entries == 2, (
            "#4926: a construction must enter the claim lock exactly twice "
            "(increment+publish, decrement+retract) — a split critical "
            f"section leaves an interleaving window, got {counter.entries}")
    finally:
        with contextlib.suppress(Exception):
            if second is not None:
                second.close()
        with contextlib.suppress(Exception):
            first.close()


def test_late_claim_recheck_sees_a_peer_that_publishes_during_the_probes(
        tmp_path, monkeypatch):
    """#4926: the claim is RE-READ as the LAST evidence before teardown.

    The early claim check runs before the two slow probes (`_owner_records`
    forks `ps`; `_client_list` is a socket round-trip). A peer that publishes
    its claim DURING them is invisible to every other signal — no owner record
    (``_owner_records`` still reports only the holder) and no connection yet
    (it has not pinged) — so without the re-read the destructive verdict runs
    against a live peer. A claim's lifetime strictly covers that window, so
    reading it again immediately before the verdict catches exactly those
    peers.

    RED mutant: return straight from the probe verdict — `cotenant_holds_server`
    answers False (last client) and the server is torn down under the peer.
    """
    import tortoise.embedded_lifecycle as _lifecycle
    from tortoise import embedded_reaper as _reaper

    sock = str(tmp_path / "redis.socket")
    key = os.path.abspath(sock)
    # An `unknown` start stamp makes `_inflight_claim_holds` count the pid LIVE
    # (fail closed), so the claim holds for a race-free reason.
    claim = _claim_file_name(_lifecycle, key, os.getpid(), "unknown")

    def _owner_records_publishing_peer(probed_key):
        # A peer publishes its mid-construction claim WHILE we are probing.
        record_dir = _lifecycle.owner_record_dir(probed_key)
        os.makedirs(record_dir, exist_ok=True)
        Path(os.path.join(record_dir, claim)).write_text("")
        return (1, 1)  # only the holder has an owner record

    monkeypatch.setattr(_reaper, "_owner_records",
                        _owner_records_publishing_peer)
    # One client only -> the CLIENT LIST branch would otherwise say "last".
    monkeypatch.setattr(_reaper, "_client_list", lambda _key: [{"id": 1}])
    monkeypatch.setattr(_lifecycle, "disconnect_only", lambda _client: None)

    class _Client:
        socket_file = sock
        connection_pool = None

    assert _lifecycle._inflight_claim_holds(key) is False, (
        "test setup: the peer has not published yet")
    assert _lifecycle.cotenant_holds_server(_Client()) is True, (
        "#4926: a claim published during the slow probes must still hold the "
        "server — the decision is re-read before the destructive verdict")


def test_late_claim_recheck_treats_a_read_failure_as_a_hold(
        tmp_path, monkeypatch):
    """#4926: the late re-read fails CLOSED, like every neighbouring signal.

    RED mutant: let the re-read's exception propagate (or treat it as "no
    claim") — an unreadable claim store would then authorize a teardown.
    """
    import tortoise.embedded_lifecycle as _lifecycle
    from tortoise import embedded_reaper as _reaper

    sock = str(tmp_path / "redis.socket")
    real = _lifecycle._inflight_claim_holds
    calls = {"n": 0}

    def _flaky(probed_key):
        calls["n"] += 1
        if calls["n"] == 1:
            return real(probed_key)  # the early read still works
        raise OSError("cannot read the claim store")

    monkeypatch.setattr(_lifecycle, "_inflight_claim_holds", _flaky)
    monkeypatch.setattr(_reaper, "_owner_records", lambda _key: (1, 1))
    monkeypatch.setattr(_reaper, "_client_list", lambda _key: [{"id": 1}])
    monkeypatch.setattr(_lifecycle, "disconnect_only", lambda _client: None)

    class _Client:
        socket_file = sock
        connection_pool = None

    assert _lifecycle.cotenant_holds_server(_Client()) is True, (
        "#4926: a late re-read that cannot be performed must HOLD the server")


def test_inflight_claim_publish_never_breaks_construction_and_retries_enoent(
        tmp_path, monkeypatch):
    """#4926: the publish swallows I/O failure and retries the ENOENT race.

    Two safety decisions that only the happy path would otherwise exercise:
    (a) a claim that cannot be written must never raise out of the
        `RedisMixin.__init__` patch — the construction has to survive
        `ENOSPC`/`EMFILE`/`EACCES`;
    (b) the one race that can lose a claim outright is a peer's `rmdir`
        landing between our `makedirs` and our `open`, so `ENOENT` is retried
        exactly once.
    RED mutant: dropping the retry -> (b) fails on the missing file; letting
    the failure propagate -> (a) raises.
    """
    import tortoise.embedded_lifecycle as _lifecycle

    sock = str(tmp_path / "redis.socket")
    key = os.path.abspath(sock)
    real_open = os.open

    def _always_fail(path, flags, mode=0o777):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "open", _always_fail)
    _lifecycle._publish_inflight_claim(key)  # must NOT raise
    assert _lifecycle._inflight_claim_holds(key) is False

    calls = {"n": 0}

    def _fail_once(path, flags, mode=0o777):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FileNotFoundError(2, "No such file or directory")
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", _fail_once)
    _lifecycle._publish_inflight_claim(key)
    assert calls["n"] == 2, (
        "#4926: the ENOENT race must be retried exactly once")
    assert os.path.exists(_lifecycle._inflight_claim_path(key)), (
        "#4926: the retry must actually create the claim")
    _lifecycle._retract_inflight_claim(key)


# ── #4439: harness fixtures must not fork periodic RDB snapshots ──────────
#
# `redislite.configuration.DEFAULT_REDIS_SETTINGS['save']` ships a periodic
# save schedule, so every harness fixture server forked an
# `redis-rdb-bgsave` snapshot to persist data that is discarded by
# definition. `tests/_embedded.py` patches the default to Redis's disable
# form (`save ""`) at import time. These tests pin the mechanism AND the
# trap that made an earlier attempt wrong.

def test_harness_disables_redislite_rdb_save():
    """The harness default renders exactly `save ""` (Redis's disable form).

    #4439 acceptance 1: the generated server config must contain `save ""`.
    """
    from redislite import configuration

    from tests._embedded import REDIS_SAVE_DISABLED

    # The disable form must be the truthy 2-char string, not an empty one:
    # config() deletes falsy settings (see the negative control below).
    assert REDIS_SAVE_DISABLED == '""'
    assert configuration.DEFAULT_REDIS_SETTINGS["save"] == REDIS_SAVE_DISABLED

    save_lines = [l for l in configuration.config().splitlines()  # noqa: E741
                  if l.startswith("save")]
    assert save_lines == ['save ""'], (
        f"harness config must render exactly one `save \"\"` line, got "
        f"{save_lines!r}")


def test_falsy_save_omits_directive_documenting_trap(monkeypatch):
    """NEGATIVE CONTROL (#4439 trap): a falsy `save` renders NO `save` line.

    `redislite.configuration.config()` renders only truthy settings, so
    `save=[]` / `save=''` OMIT the directive — and Redis's built-in defaults
    then apply (measured on the bundled redis-server v8.6.2: `3600 1 / 300 100
    / 60 10000`), i.e. saving is NOT disabled.
    This control documents redislite's rendering semantics. The gate that a
    future "simplification" to a falsy harness value cannot pass silently is
    `test_harness_disables_redislite_rdb_save` (which reads the HARNESS
    constant and its rendered line) — this test intentionally monkeypatches
    redislite directly, so by itself it would still pass under that trap.

    `monkeypatch.setitem` restores the harness default at teardown — without
    it this test would leave the module global falsy and re-arm the storm for
    every later server in the session.
    """
    from redislite import configuration

    from tests._embedded import REDIS_SAVE_DISABLED

    for falsy in ([], ""):
        monkeypatch.setitem(configuration.DEFAULT_REDIS_SETTINGS, "save", falsy)
        save_lines = [l for l in configuration.config().splitlines()  # noqa: E741
                      if l.startswith("save")]
        assert save_lines == [], (
            f"falsy save={falsy!r} unexpectedly rendered {save_lines!r}; the "
            "trap (omitted directive → Redis built-in defaults) changed")

    # Prove the restore contract the harness depends on: after the mutations
    # are undone the disable form is back, so no later server is re-armed.
    monkeypatch.undo()
    assert configuration.DEFAULT_REDIS_SETTINGS["save"] == REDIS_SAVE_DISABLED, (
        "the falsy mutations must not survive the test — a leaked falsy "
        "default would re-arm the fork storm for every later server")


def test_live_fixture_server_reports_rdb_save_disabled(tmp_path):
    """A live harness fixture server gets persistence disabled end-to-end.

    #4439 acceptance 1 (live half): the server actually started by the
    harness writes `save ""` into its redis.config and reports an empty
    `save` value over the wire — so no periodic snapshot can ever fire.
    """
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection(str(tmp_path / "fixture.db"), graph_name="test",
                            skip_health_check=True)
    try:
        if not getattr(proj, "_is_embedded", False):
            pytest.skip("not an embedded construction (server-mode redirect)")
        config_file = proj.db.client.redis_configuration_filename
        with open(config_file) as fh:
            save_lines = [l for l in fh.read().splitlines()  # noqa: E741
                          if l.startswith("save")]
        assert save_lines == ['save ""'], (
            f"live fixture redis.config must disable saving, got {save_lines!r}")
        result = proj.db.execute_command("CONFIG", "GET", "save")
        # Redis returns the (empty) value, not the two-quote form.
        value = result[1] if isinstance(result, (list, tuple)) else (
            result.get("save") if isinstance(result, dict) else None)
        assert value == "", (
            f"live fixture must report save disabled (empty), got {result!r}")
    finally:
        with contextlib.suppress(Exception):
            proj.close()
