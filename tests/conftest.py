"""D11 #578 — shared fixtures for the epic E2E suite.

provision_test_user: creates a provisioned test user (team + membership +
key) with tier + demo_seed control. Tier injection writes the Team node
directly (no user-facing tier path in v1). Used by E2E-1/3/4/5/10/11/12/13.
"""
from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

# #1371: opt-in fast interpreter-exit close for ephemeral embedded test
# servers (tortoise/embedded_lifecycle.py) — kills the ~10-15 min atexit
# teardown tail on every test run (local + CI + post-merge-validation, which
# all load this conftest). User-path DBs and explicit close() are unaffected.
os.environ.setdefault("TORTOISE_FAST_ATEXIT", "1")

# #1012: session-shared embedded projection fixture (construction centralized
# in tests/_embedded.py — one redislite server per session, not per test).
# tests/ is a namespace package (no __init__.py): resolve it via the repo
# root so conftest loads under `uv run pytest tests/` too (python -m pytest
# adds cwd, but uv run does not — CI uv-lock-check, issue #1012).
import sys  # noqa: I001
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests._embedded import shared_proj  # noqa: F401, I001

from tortoise.sdk import TortoiseSDK
from tortoise.pricing import tier_limits


@pytest.fixture
def provision_test_user():
    created = []

    def factory(tier: str = "free", demo_seed: bool = True):
        tmpdir = tempfile.mkdtemp()
        sdk = TortoiseSDK(os.path.join(tmpdir, "e2e.db"), namespace="e2e-tests")
        team = sdk.team_create(f"e2e-{os.urandom(4).hex()}")
        lim = tier_limits(tier)
        # #310 (review fix 16b): mirror production CREATE semantics — write
        # max_points (= max_graph_nodes, GAP-B mapping) + max_sessions too.
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.tier=$tier, t.max_graphs=$mg, "
            "t.max_users=$mu, t.max_api_keys=$mk, t.max_points=$mp, "
            "t.max_sessions=$ms, t.ops_allowance=$ops, t.graph_size_cap=$nodes",
            params={"id": team["id"], "tier": tier,
                    "mg": lim["max_graphs_per_team"], "mu": lim["max_users_per_team"],
                    "mk": lim["max_api_keys"], "mp": lim["max_graph_nodes"],
                    "ms": 1000, "ops": lim["included_write_ops_per_month"],
                    "nodes": lim["max_graph_nodes"]},
        )
        if demo_seed:
            try:  # noqa: SIM105
                sdk._graph_create(team["id"], "demo", kind="custom")
            except Exception:
                pass
        user_id = f"user-{os.urandom(4).hex()}"
        sdk.membership_create(team["id"], user_id, "owner")
        created.append(sdk)
        return {"sdk": sdk, "team_id": team["id"], "api_key": team["api_key"],
                "graph_name": team["graph_name"], "team_name": team["name"],
                "user_id": user_id}

    yield factory
    for sdk in created:
        try:  # noqa: SIM105
            sdk.close()
        except Exception:
            pass


@pytest.fixture
def test_user(provision_test_user):
    return provision_test_user(tier="free", demo_seed=True)


@pytest.fixture
def sdk_factory(tmp_path):
    """Shared embedded-SDK factory for the #432 suite (Tasks 1/2/3/5).

    Each call builds a TortoiseSDK on a FRESH embedded redislite DB file under
    the per-test tmp_path (unique per call), so concurrent workers (threads)
    each get an isolated graph. Embedded-vs-docker concurrency note
    (plan-review P2): the embedded redislite server is shared per-path but is
    NOT multi-connection-safe — two TortoiseSDK instances on the SAME path in
    one process each open their own server and last-close wins on the DB file.
    Tests that need cross-SDK sharing on one graph must run against a live
    FalkorDB (TORTOISE_DB_URI=docker://...) instead; the seq-atomicity test
    (Task 3) follows the plan's per-worker fresh-SDK construction.

    ensure_schema=False (default): :GraphEvent schema is created lazily by
    append_event on first emit (Task 3). ensure_schema=True eagerly installs
    it (used by the duplicate-append test).
    """
    import os

    def factory(_tmp_path=None, *, ensure_schema=False, namespace=None):
        base = _tmp_path if _tmp_path is not None else tmp_path
        db_path = os.path.join(str(base), f"evt-{os.urandom(4).hex()}.db")
        sdk = TortoiseSDK(db_path, namespace=namespace)
        if ensure_schema:
            from tortoise import event_store
            event_store.ensure_event_schema(sdk._get_proj())
        return sdk

    return factory


@pytest.fixture(scope="session")
def shared_embedded_db():
    """One shared embedded FalkorDBLite DB for the whole session (#221 R5).

    R5 mitigation for the redislite process leak (#176): tests that need an
    embedded (redislite) DB create ONE server per session instead of one per
    test. Each test wipes the graph on its own (or the per-test graph name
    isolates it), so state never leaks across tests while the subprocess
    count stays at 1.

    Restored 2026-08-08 (#647): the D11 conftest rewrite (#578) dropped this
    fixture but seven test files (test_ep_selector, test_ranking,
    test_recall_gaps_subgraph, test_recall_state,
    test_sdk_legacy_coverage, test_search_sessions_temporal,
    test_session_semantic_search) still depend on it. Kept via #281: the
    branch's own copy survived its merge of main (main had dropped the
    fixture at that point; the #647 restoration landed on main afterward).

    # TODO(#176): stopgap — remove when the redislite root-cause fix lands.
    # Issue #1005: superseded by lifecycle finalize (tortoise.FalkorDB /
    # TortoiseSDK close on GC) + the _redislite_hygiene session sweeps below;
    # kept because the fixture's shared path is still the cheap way for the
    # seven dependent files to share one server.
    """
    import tempfile as _tf
    db_path = os.path.join(_tf.mkdtemp(prefix="tortoise_shared_embedded_"), "shared.db")
    yield db_path


@pytest.fixture(scope="session", autouse=True)
def _redislite_hygiene():
    """Bound redislite orphan accumulation (#1005) + index-pid files (#1231).

    Session start: register this suite in the active-suite registry and run
    a CONCURRENCY-SAFE sweep (dir-gone/stale records only — never a live
    server of a concurrently running suite, which may sit at 0 clients
    between tests). Session end: run a full sweep, but only when no other
    suite is still active — otherwise defer to the last suite standing.

    #1231: an atexit fallback runs the stale index-pid sweep (and removes
    this suite's marker) when the process exits abnormally (pytest killed,
    watchdog SIGINT) so test-spawned lock files never accumulate.
    """
    import atexit
    import uuid

    from tortoise.embedded_reaper import (
        ACTIVE_SUITES_DIR,
        _ReaperLock,
        _run_sweep,
        active_suite_tokens,
        sweep_stale_index_pid_files,
    )

    marker_dir = ACTIVE_SUITES_DIR
    token = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    marker_path = None
    try:
        os.makedirs(marker_dir, exist_ok=True)
        marker_path = os.path.join(marker_dir, token)
        with open(marker_path, "w") as fh:
            fh.write(f"pid={os.getpid()}\n")
    except OSError:
        # never fail the suite over hygiene; remove any partial marker so a
        # poison file cannot degrade every future suite's sweep to only-safe
        # (the foreign-pytest guard still covers the markerless case)
        if marker_path:
            try:  # noqa: SIM105
                os.remove(marker_path)
            except OSError:
                pass
        marker_path = None

    def _sweep(only_safe: bool) -> dict:
        try:
            lock = _ReaperLock()
            if not lock.acquire():
                return {"skipped": "reaper-lock-held"}
            try:
                # Full end-sweep: disable the boot cooldown — at session end
                # no new client can appear, so servers younger than the 30s
                # cooldown are still safe to reap (otherwise the last minute
                # of the suite's servers leak until the next suite; observed
                # as 13 orphans on CI, issue #1005 follow-up).
                prev = os.environ.get("TORTOISE_REAPER_MIN_UPTIME")
                if not only_safe:
                    os.environ["TORTOISE_REAPER_MIN_UPTIME"] = "0"
                try:
                    acted = _run_sweep(
                        dry_run=False, batch_size=50, only_safe=only_safe,
                        jobs=8, kill_pacing=0.4)
                    return {"reaped": len(acted)}
                finally:
                    if prev is None:
                        os.environ.pop("TORTOISE_REAPER_MIN_UPTIME", None)
                    else:
                        os.environ["TORTOISE_REAPER_MIN_UPTIME"] = prev
            finally:
                lock.release()
        except Exception as exc:  # never fail the suite over hygiene
            return {"error": str(exc)}

    # Session start: only dir-gone/stale records are safe while other suites
    # may be mid-run (their per-test servers are 0-client between tests).
    start_result = _sweep(only_safe=True)
    print(f"[redislite-hygiene] start sweep: {start_result}")

    # #1231: atexit fallback — when this process exits abnormally (pytest
    # killed, watchdog SIGINT) the normal teardown below never runs; clean
    # the stale index-pid files and our own active-suite marker anyway so
    # test-spawned lock files don't accumulate on shared dev boxes.
    _teardown_ran = [False]

    def _atexit_cleanup() -> None:
        if _teardown_ran[0]:
            return
        if marker_path:
            try:  # noqa: SIM105
                os.remove(marker_path)
            except OSError:
                pass
        try:
            removed = sweep_stale_index_pid_files(dry_run=False)
            if removed:
                print(f"[redislite-hygiene] atexit stale index-pid cleanup: "
                      f"{len(removed)} removed")
        except Exception:
            pass

    atexit.register(_atexit_cleanup)

    yield

    def _cmdline_of(pid: str) -> str:
        """Short cmdline for a pid, for the hygiene log (issue #1103).
        Linux /proc first (CI), macOS ps fallback. Best-effort — "?" on
        failure (the pid may have just exited).
        """
        import subprocess as _sp
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                raw = fh.read()
            if raw:
                return raw.replace(b"\x00", b" ").decode(
                    errors="replace")[:120]
        except OSError:
            pass
        try:
            return (_sp.run(["ps", "-o", "args=", "-p", pid],
                            capture_output=True, text=True, timeout=5)
                    .stdout.strip()[:120])
        except Exception:
            return "?"

    def _foreign_suites_active() -> tuple[bool, list[dict]]:
        """(foreign, matches) — True when pytest processes outside this suite's
        process tree are running, plus the matched pids (pid + cmdline) so the
        decision is diagnosable (issue #1103). Catches suites running the
        pre-#1005 conftest, which do not write active-suite markers — without
        this, our full end-sweep could kill another suite's between-tests idle
        server (issue #1005 P1).
        """
        import subprocess as _sp
        my_pid = os.getpid()
        # Ancestors of THIS suite's invocation (runner shell → bash -c step →
        # timeout → stdbuf → python) carry "pytest" in their cmdline (the step
        # script text) but are NOT foreign suites. Without the ancestry skip,
        # the CI step wrapper false-positives and the end-sweep defers forever
        # — every suite leaks its servers (observed as 13 orphans on test (b),
        # issue #1005). Genuine concurrent suites (different process tree) are
        # not ancestors and are still detected below.
        ancestors: set[int] = set()
        pid = my_pid
        for _ in range(64):  # bounded walk to init
            try:
                ppid = int(_sp.run(["ps", "-o", "ppid=", "-p", str(pid)],
                                   capture_output=True, text=True, timeout=5
                                   ).stdout.strip())
            except (_sp.TimeoutExpired, OSError, ValueError):
                break
            if ppid <= 0 or ppid == pid:
                break
            ancestors.add(ppid)
            pid = ppid
        try:
            out = _sp.run(["pgrep", "-f", "pytest"], capture_output=True,
                          text=True, timeout=10)
        except (_sp.TimeoutExpired, OSError):
            return True, []  # unknown -> fail closed (defer full sweep)
        for line in out.stdout.splitlines():
            pid = line.strip()
            if not pid.isdigit() or int(pid) == my_pid or int(pid) in ancestors:
                continue
            try:
                r = _sp.run(["ps", "-o", "ppid=", "-p", pid],
                            capture_output=True, text=True, timeout=5)
            except (_sp.TimeoutExpired, OSError):
                return True, []  # unknown liveness -> fail closed
            raw = r.stdout.strip()
            if not raw:
                # pgrep/ps race — the pid exited between the two calls. A dead
                # process cannot be a live foreign suite, so it must NOT defer
                # the sweep (issue #1103: the race makes the full end-sweep
                # skip, leaking this suite's own 0-client servers as the CI
                # orphan count).
                continue
            try:
                ppid = int(raw)
            except ValueError:
                return True, []  # unparseable -> fail closed
            if ppid != my_pid:
                return True, [{"pid": pid, "cmdline": _cmdline_of(pid)}]
        return False, []

    # Session end: remove our marker first, then check for other active
    # suites (markers) AND foreign pytest processes (old-conftest suites).
    # Full sweep only when we are the last suite standing.
    if marker_path:
        try:  # noqa: SIM105
            os.remove(marker_path)
        except OSError:
            pass
    others = [t for t in active_suite_tokens() if t != token]
    foreign, foreign_matches = _foreign_suites_active()
    end_result = _sweep(only_safe=bool(others) or foreign)
    print(f"[redislite-hygiene] end sweep (other-suites={len(others)}, "
          f"foreign-pytest={foreign}): {end_result}")
    # #1231: normal teardown completed — the atexit fallback must not re-run.
    _teardown_ran[0] = True
    # Issue #1103: pytest capture swallows the print above, so the sweep
    # decision is invisible in CI logs. Mirror it to a file the CI orphan
    # check can dump. Best-effort — never fail the suite over hygiene logging.
    try:
        import json as _json
        log_dir = os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()
        log_path = os.path.join(log_dir, "redislite-hygiene-end.json")
        with open(log_path, "w") as fh:
            _json.dump({
                "token": token,
                "other_suites": others,
                "foreign_pytest": foreign,
                "foreign_pids": foreign_matches,
                "sweep": end_result,
            }, fh, indent=2)
    except Exception:
        pass




@pytest.fixture(autouse=True)
def _reset_ip_rate_limits():
    """#498 register + #1081 signup IP limiters are in-memory per process and
    share one TestClient host across a module — reset both per test."""
    # P3-FIX-6: getattr-guard so the red phase (before _SIGNUP_BUCKETS exists)
    # does not ImportError the whole suite; also reset the R8 tracker
    # (order-dependent dedup flake guard — module-scoped testclient host).
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import _register_buckets
    _register_buckets.clear()
    signup_buckets = getattr(ha_mod, "_SIGNUP_BUCKETS", None)
    if signup_buckets is not None:
        signup_buckets.clear()
    try:
        from tortoise.abuse import SIGNUP_TRACKER
        SIGNUP_TRACKER.reset()
    except (ImportError, AttributeError):
        pass
    yield
    _register_buckets.clear()
    if signup_buckets is not None:
        signup_buckets.clear()
    try:
        from tortoise.abuse import SIGNUP_TRACKER
        SIGNUP_TRACKER.reset()
    except (ImportError, AttributeError):
        pass
