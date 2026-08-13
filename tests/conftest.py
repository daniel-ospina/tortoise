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
            try:
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
        try:
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
    fixture but five test files (test_ep_selector, test_ranking,
    test_sdk_legacy_coverage, test_search_sessions_temporal,
    test_session_semantic_search) still depend on it. Kept via #281: the
    branch's own copy survived its merge of main (main had dropped the
    fixture at that point; the #647 restoration landed on main afterward).

    # TODO(#176): stopgap — remove when the redislite root-cause fix lands.
    # Issue #1005: superseded by lifecycle finalize (tortoise.FalkorDB /
    # TortoiseSDK close on GC) + the _redislite_hygiene session sweeps below;
    # kept because the fixture's shared path is still the cheap way for the
    # five dependent files to share one server.
    """
    import tempfile as _tf
    db_path = os.path.join(_tf.mkdtemp(prefix="tortoise_shared_embedded_"), "shared.db")
    yield db_path


@pytest.fixture(scope="session", autouse=True)
def _redislite_hygiene():
    """Bound redislite orphan accumulation (#1005).

    Session start: register this suite in the active-suite registry and run
    a CONCURRENCY-SAFE sweep (dir-gone/stale records only — never a live
    server of a concurrently running suite, which may sit at 0 clients
    between tests). Session end: run a full sweep, but only when no other
    suite is still active — otherwise defer to the last suite standing.

    Sweeps acquire the reaper singleton lock and are time-boxed + batch-
    capped so a dirty machine cannot stall suite startup.
    """
    import gc
    import uuid

    from tortoise.embedded_reaper import (
        ACTIVE_SUITES_DIR,
        _ReaperLock,
        _run_sweep,
        active_suite_tokens,
    )

    marker_dir = ACTIVE_SUITES_DIR
    token = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    os.makedirs(marker_dir, exist_ok=True)
    marker_path = os.path.join(marker_dir, token)
    try:
        with open(marker_path, "w") as fh:
            fh.write(f"pid={os.getpid()}\n")
    except OSError:
        marker_path = None

    def _sweep(only_safe: bool) -> dict:
        try:
            lock = _ReaperLock()
            if not lock.acquire():
                return {"skipped": "reaper-lock-held"}
            try:
                acted = _run_sweep(
                    dry_run=False, batch_size=50, only_safe=only_safe,
                    jobs=8, kill_pacing=0.4)
                return {"reaped": len(acted)}
            finally:
                lock.release()
        except Exception as exc:  # never fail the suite over hygiene
            return {"error": str(exc)}

    # Session start: only dir-gone/stale records are safe while other suites
    # may be mid-run (their per-test servers are 0-client between tests).
    start_result = _sweep(only_safe=True)
    print(f"[redislite-hygiene] start sweep: {start_result}")

    yield

    def _foreign_suites_active() -> bool:
        """True when pytest processes outside this suite's process tree are
        running. Catches suites running the pre-#1005 conftest, which do not
        write active-suite markers — without this, our full end-sweep could
        kill another suite's between-tests idle server (issue #1005 P1).
        """
        import subprocess as _sp
        my_pid = os.getpid()
        try:
            out = _sp.run(["pgrep", "-f", "pytest"], capture_output=True,
                          text=True, timeout=10)
        except (_sp.TimeoutExpired, OSError):
            return True  # unknown -> fail closed (defer full sweep)
        for line in out.stdout.splitlines():
            pid = line.strip()
            if not pid.isdigit() or int(pid) == my_pid:
                continue
            try:
                ppid = int(_sp.run(["ps", "-o", "ppid=", "-p", pid],
                                   capture_output=True, text=True, timeout=5
                                   ).stdout.strip())
            except (_sp.TimeoutExpired, OSError, ValueError):
                return True
            if ppid != my_pid:
                return True
        return False

    # Session end: remove our marker first, then check for other active
    # suites (markers) AND foreign pytest processes (old-conftest suites).
    # Full sweep only when we are the last suite standing.
    if marker_path:
        try:
            os.remove(marker_path)
        except OSError:
            pass
    others = [t for t in active_suite_tokens() if t != token]
    foreign = _foreign_suites_active()
    gc.collect()  # fire finalizers so SDK/projection close() runs first
    end_result = _sweep(only_safe=bool(others) or foreign)
    print(f"[redislite-hygiene] end sweep (other-suites={len(others)}, "
          f"foreign-pytest={foreign}): {end_result}")




@pytest.fixture(autouse=True)
def _reset_register_rate_limit():
    """/v1/register rate limiter is in-memory per process (3/hour/IP, #498).

    pytest runs all test files in ONE process, so onboarding/register tests
    in earlier files consume the budget of later files (429s in
    test_onboarding_integration, #493). Reset per test — the limiter's
    cross-test persistence has no value here (each test uses a fresh IP-less
    TestClient context).
    """
    from tortoise.hosted_api import _register_buckets
    _register_buckets.clear()
    yield
