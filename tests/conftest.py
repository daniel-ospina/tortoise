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

# RateLimitMiddleware (100 req/min per path+IP bucket) trips 429 in
# full-suite runs (>100 points per shared IP bucket — the same documented
# pattern as tests/test_hosted_api.py:22). Set BEFORE hosted_api imports so
# the middleware is constructed disabled; per-endpoint limiter tests
# (signup 2/24h, session 5/hr, recovery) delenv RATE_LIMIT_DISABLED — those
# read env at CALL time and stay live.
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")
# #1686: TEST_MODE must be visible BEFORE tests._embedded imports tortoise.
# projection (tests/_embedded.py:27 imports it) — the module-body
# Thread.start stamp install is gated on TEST_MODE, and conftest's own
# `from tests._embedded import shared_proj` (below) is what triggers that
# import. (Call-time redirect checks are unaffected — only the module-body
# install gate needs the env early.)
os.environ.setdefault("TORTOISE_TEST_MODE", "1")

# #1642 FIX 6: the session-end sweep loops discover->reap until the backlog
# is cleared or this wall-clock budget is exhausted, at a raised batch size
# — one completing suite can clear a multi-hundred orphan backlog (the old
# single batch_size=50 pass could not).
SWEEP_TIME_BUDGET = 30.0
SWEEP_BATCH_SIZE = 200

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
import sys  # noqa: E402, I001
from pathlib import Path  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests._embedded import shared_proj  # noqa: E402, F401, I001

# ── Epic #1647 (D-1=A): the test-session signal + redirect env ────────────
# Exported at CONFTEST IMPORT so the PRODUCT-side redirect
# (tortoise/projection/__init__.py) reads them without importing tests/
# (import cycle). TORTOISE_TEST_MODE is the test-session signal that gates
# the redirect (plan-review P0-4); TORTOISE_TEST_NO_REDIRECT is the in-repo
# carve-out exemption list (caller test-module stems, frame-identified);
# TORTOISE_TEST_SESSION is the session nonce folded into derived graph names.
from tests._embedded import TEST_NO_REDIRECT_STEMS  # noqa: E402

# (TORTOISE_TEST_MODE was already exported above — before the tests._embedded
# import — so projection's module-body Thread.start stamp install sees it;
# the call-time redirect checks read the env at construction regardless.)
from tortoise import projection as _projection_mod  # noqa: E402

_projection_mod._TEST_SESSION_ACTIVE = True
# #1686: install the Thread.start test-stem stamp AFTER the session flag is
# live (prod can never satisfy the flag, so even a leaked TEST_MODE=1 env
# cannot patch stdlib — see install_thread_stamp's docstring).
_projection_mod.install_thread_stamp()


# ── Epic #1686: per-thread test-module attribution ────────────────────────
# Record the RUNNING test module's stem on the current (main) thread so
# worker threads spawned during the test (TestClient portals, background
# threads) inherit it via projection's patched Thread.start — the
# frame-keyed carve-out exemption cannot see worker-thread stacks.


def pytest_runtest_setup(item):
    """Record the running test module's stem on the current thread (#1686)."""
    try:
        stem = (item.module.__name__.rsplit(".", 1)[-1]
                if item.module is not None else None)
    except AttributeError:
        stem = None
    _projection_mod._record_current_test_stem(stem)


def pytest_runtest_teardown(item, nextitem):
    """Clear the main-thread stem at test end (#1686).

    Only the MAIN thread's slot is cleared — child threads keep their
    spawn-time inherited stems (bounded in practice: module-scoped portal
    threads spawn within one module; documented in projection)."""
    _projection_mod._record_current_test_stem(None)
os.environ.setdefault("TORTOISE_TEST_NO_REDIRECT", ",".join(TEST_NO_REDIRECT_STEMS))

# Cycle-5 P2-1 / cycle-6 P1-5: the session nonce is an OVERWRITE (never
# setdefault) — a pre-set env value (dev shell, CI wrapper, task runners)
# would freeze the nonce for every session, so concurrent sessions share one
# journal filename and session A's end-sweep drops session B's live graphs.
# 6 bytes = 12 hex = 48 bits (the width matches the derived-name hash
# guards). The overwrite is paired with a 12-hex shape guard: os.urandom(6)
# always yields 12 hex chars, so the assert can only fire on a broken
# platform — fail loudly rather than export a malformed nonce.
import re as _re  # noqa: I001, E402
_SESSION_NONCE = os.urandom(6).hex()
assert _re.fullmatch(r"[0-9a-f]{12}", _SESSION_NONCE), \
    f"TORTOISE_TEST_SESSION must be 12 hex (48 bits), got {_SESSION_NONCE!r}"
os.environ["TORTOISE_TEST_SESSION"] = _SESSION_NONCE

# ── Epic #1647 Task 2 Step 7: the session created-graph journal ───────────
# The journal path is resolved at CONFTEST IMPORT (cycle-4 P2-9) — product-
# side appends (the redirect + the frame-gated from_uri seam) fire during
# TEST-MODULE imports and collect-only runs, before any session fixture.
# The export is URI-GATED (divergence from the plan's unconditional export,
# documented in the epic changelog): on the embedded lane there is no server
# to sweep, and leaving the env unset keeps embedded runs from writing
# journal files that a later docker session's stale sweep would misread as
# dead sessions' drop sets (their graphs were never minted on the server).
from tortoise.config import is_db_uri as _is_db_uri_conftest  # noqa: E402, I001
from tortoise.embedded_reaper import ACTIVE_SUITES_DIR as _ACTIVE_SUITES_DIR  # noqa: E402
if _is_db_uri_conftest(os.environ.get("TORTOISE_DB_URI")):
    _JOURNAL_PATH = os.path.join(
        _ACTIVE_SUITES_DIR, f"{_SESSION_NONCE}.graphs.jsonl")
    os.environ["TORTOISE_TEST_JOURNAL_FILE"] = _JOURNAL_PATH
    import tests._embedded as _embedded_mod
    _embedded_mod._JOURNAL_FILE = _JOURNAL_PATH

# ── Epic #1647 Task 10 Step 1a (P4, plan-review P1-9): URI-required ───────
# Default pytest requires TORTOISE_DB_URI; the carve-out is the sole embedded
# surface. Declared FIRST among the session fixtures so the enforcement
# fails the run before any hygiene/sweep machinery spins up. The named
# helper lives in tests/_embedded.py (pinned by test_markers.py — the
# tests.conftest import would re-execute conftest's top-level code).
from tests._embedded import _assert_p4_uri_required  # noqa: E402
from tortoise.pricing import tier_limits  # noqa: E402  (late import: after TEST_MODE env wiring)
from tortoise.sdk import TortoiseSDK  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _p4_uri_required():
    """Epic #1647 P4: fail the session when TORTOISE_DB_URI is unset UNLESS
    TORTOISE_TEST_CARVE_OUT=1 is set (the carve-out job / tier-2 URI-less
    legs / e2e surfaces opt in). A URI-less run that is not the carve-out is
    the pre-epic shape — migrated files would construct embedded and
    green-pass on the wrong backend."""
    _assert_p4_uri_required()


@pytest.fixture
def provision_test_user():
    created = []

    def factory(tier: str = "free", demo_seed: bool = True):
        tmpdir = tempfile.mkdtemp()
        # Epic #1647 (plan-review P1-5): under a supported URI, sweep the
        # shared non-test "e2e-tests" namespace to a guard-passing per-test
        # test_e2e_<uuid> (the SDK maps it to test_e2e_<uuid>_tortoise,
        # sdk.py L1115-1123) — "e2e-tests" would mint the non-test
        # team_e2e-tests graph on the server, shared by the whole suite.
        # URI unset → today's namespace unchanged (P1 zero-change).
        from tortoise.config import is_db_uri as _is_db_uri
        _ns = "e2e-tests"
        if _is_db_uri(os.environ.get("TORTOISE_DB_URI")):
            _ns = f"test_e2e_{os.urandom(4).hex()}"
        sdk = TortoiseSDK(os.path.join(tmpdir, "e2e.db"), namespace=_ns)
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
        # Epic #1647 (D-1=A): URI-aware seam — under a supported
        # TORTOISE_DB_URI the redirect flips the SDK's internal path=
        # construction to the server; pass a guard-passing per-call
        # namespace so the graph name is the deterministic
        # test_suite_<uuid>_tortoise (honored verbatim by the redirect)
        # instead of a path-derived name. URI unset → today's construction
        # (namespace None) unchanged.
        from tortoise.config import is_db_uri as _is_db_uri
        if namespace is None and _is_db_uri(os.environ.get("TORTOISE_DB_URI")):
            namespace = f"test_suite_{os.urandom(4).hex()}"
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
    #
    # Epic #1647 (D-1=A): URI-aware seam — this fixture yields the
    # session-stable shared PATH; under a supported TORTOISE_DB_URI the
    # consumers' path= constructions redirect to the server (the redirect
    # derives a per-session test_shared_<hash12> graph from this same
    # session-stable path, preserving the shared-tier semantics: one shared
    # server graph per session, per-test wipes). URI unset → today's
    # embedded shared server, unchanged.
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
    import time
    import uuid

    from tortoise.embedded_reaper import (
        ACTIVE_SUITES_DIR,
        _ReaperLock,
        _run_sweep,
        active_suite_markers,
        active_suite_tokens,
        sweep_stale_index_pid_files,
    )

    marker_dir = ACTIVE_SUITES_DIR
    token = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    marker_path = None
    try:
        os.makedirs(marker_dir, exist_ok=True)
        marker_path = os.path.join(marker_dir, token)
        # #1642 FIX 5: record the suite process's START time alongside its
        # pid — a recycled pid (the number reused by a different live
        # process) then reads as a stale marker, so a SIGKILLed suite can
        # never defer later sweeps to only-safe forever (#1448).
        from tortoise.embedded_reaper import _process_start_time
        start = _process_start_time(os.getpid())
        with open(marker_path, "w") as fh:
            fh.write(f"pid={os.getpid()}\n")
            if start is not None:
                fh.write(f"start={start}\n")
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

    # Epic #1647 Task 9 Step 3 (P3 hygiene gating): the sweeps become
    # no-ops on docker halves — gated on whether ANY embedded redislite
    # server is actually running (O(servers) pgrep, never the tempdir walk).
    # Docker halves create no embedded servers by construction (the 17
    # carve-out files moved to the URI-unset carve-out job; migrated files
    # redirect), so the gate logs "no hygiene action" (E2E-7) instead of
    # burning sweep time; a leftover embedded server (carve-out mis-wiring,
    # an embedded-lane straggler like tests/eval/retrieval/test_oracle)
    # still gets reaped — the gate is on ACTUAL creation, never a blind
    # lane assumption.
    def _embedded_servers_running() -> bool:
        from tortoise.embedded_reaper import _pgrep_redis_servers
        try:
            return bool(_pgrep_redis_servers())
        except Exception:
            return True  # probe failure: run the sweep (fail-safe direction)

    def _sweep(only_safe: bool) -> dict:
        if not _embedded_servers_running():
            return {"no_embedded_servers": True}
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
                    # #1642 FIX 6: loop discover->reap until the time budget
                    # is exhausted or the backlog is cleared, at a raised
                    # batch size — ONE completing suite must be able to clear
                    # a multi-hundred backlog (the old single batch_size=50
                    # pass could not; the 445-orphan wave needed 9 sweeps).
                    deadline = time.monotonic() + SWEEP_TIME_BUDGET
                    total = 0
                    # The budget bounds ITERATIONS, not wall time — one
                    # iteration at batch 200 with kill_pacing 0.4 takes ~80s
                    # of pacing, so a multi-hundred backlog can run past the
                    # 30s soft budget (review P2; it still terminates). The
                    # cron sweeps every 10 min make up the difference.
                    while True:
                        acted = _run_sweep(
                            dry_run=False, batch_size=SWEEP_BATCH_SIZE,
                            only_safe=only_safe, jobs=8, kill_pacing=0.4,
                            # Epic #1647 (PR #1684 CI-fix): the suite is
                            # ENDING — a server that ignores SIGTERM for 3s
                            # gets SIGKILL regardless; the default 10s wait ×
                            # many servers compounds past pytest-timeout under
                            # CI load (observed: TestMcpHandlers teardown
                            # timed out at 600s with the reaper in _kill).
                            sigterm_timeout=3.0,
                            # deadline is now threaded INTO reap(): the
                            # eager pre-probe cache is skipped and the record
                            # loop aborts once the budget is spent — the
                            # end-sweep can never run past pytest-timeout on
                            # a large stale backlog (observed: >300s teardown
                            # timeout redding the leg with the reaper in
                            # _kill/probe).
                            deadline=deadline)
                        total += len(acted)
                        if not acted or time.monotonic() >= deadline:
                            break
                    return {"reaped": total}
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

    # Session end: remove our marker first, then check for other active
    # suites (markers). #1642 FIX 4: foreign-suite detection is marker-FILE-
    # based ONLY — the pgrep -f "pytest" check was removed: it matched ANY
    # process with "pytest" in its cmdline (including the investigator's own
    # `pgrep -fl "pytest"` command and other agents' shell commands), a
    # permanent false positive that degraded every end-sweep to only_safe
    # forever. Markers verified by (pid, start_time) identity (FIX 5) are
    # the reliable signal; pre-#1005 conftest suites without markers are a
    # bounded residual — their servers converge via the scheduled reaper
    # (FIX 1) and the cron's orphan confirmation.
    # Full sweep only when we are the last suite standing.
    if marker_path:
        try:  # noqa: SIM105
            os.remove(marker_path)
        except OSError:
            pass
    others = [t for t in active_suite_tokens()
              if t != token and t.split('-', 1)[0] != str(os.getpid())]
    foreign_matches: list[dict] = []
    for m in active_suite_markers():
        # Epic #1647 (cycle-6 P2-16 / cycle-7 P1-2 — branch (a)): own/foreign
        # is PID-GROUPED, never token-compared. A docker session writes TWO
        # markers in ACTIVE_SUITES_DIR — its embedded-format marker
        # ({pid}-{uuid8}) AND the docker-format marker ({pid}-{nonce12}, the
        # Task 2 Step 7 session-end fixture) — same pid, different tokens. A
        # token-based predicate counts the session's OWN second marker as a
        # foreign suite: `others != []` forever → every end-sweep degrades to
        # only_safe and the 6 P2 carve-out stems' embedded servers leak (the
        # orphan-survival hazard). PID-grouped: a same-pid marker is OWN
        # regardless of token fragment — one session, one suite.
        if m.get("pid") != os.getpid():
            foreign_matches.append({"pid": m["pid"], "token": m["token"]})
    # #1642 FIX 4 review P2: keep the diagnostic signal real (was hardcoded
    # False after the pgrep-based foreign detection was removed).
    foreign = bool(foreign_matches)
    end_result = _sweep(only_safe=bool(others))
    print(f"[redislite-hygiene] end sweep (other-suites={len(others)}): "
          f"{end_result}")
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


# ── Epic #1647 Task 2 Step 7 (P2-14/P0-3/P1-8/P1-9/P2-3/P2-4) ─────────────
# The docker-lane session journal + stale/session-end/atexit sweeps. Mirrors
# _redislite_hygiene's active-suite registry + defer-to-last-suite-standing,
# sharing the SAME marker directory + liveness helpers (active_suite_markers
# / _process_start_time) so the recycled-pid guard (#1642 FIX 5) applies to
# docker sessions too. Declared AFTER _redislite_hygiene so its teardown runs
# BEFORE the reaper's (pytest tears session fixtures down in reverse setup
# order) — the server sweep completes before the redislite end-sweep.
_SERVER_SWEEP_GRAPH_LIST_CONSTANT = 20  # E2E-7: absorbs pre-existing/foreign


@pytest.fixture(scope="session", autouse=True)
def _server_graph_hygiene(_redislite_hygiene):
    """Docker-lane session sweep (URI set only).

    Session start: write this session's docker-format active-suite marker
    ({pid}-{nonce}, same pid=/start= lines as the embedded format so
    active_suite_markers() parses it identically — cycle-4 P2-1/P1-6), then
    run the STALE sweep: drop DEAD sessions' journaled graphs (liveness via
    active_suite_markers — cycle-8 P2-7; bare marker-file existence is NOT
    the liveness rule). Session end: drop THIS session's own journaled
    graphs (file = single source of truth, cycle-8 P1-2) and defer the FULL
    leftover sweep (scope=None) to the last suite standing (PID-grouped,
    cycle-6 P2-16). Atexit: repeat the own-journal drop when the session
    dies abnormally so the next session's stale sweep finds the journal
    already drained.

    Failure policy (cycle-8 P2-3/P2-4): log-and-continue; the journal file
    is removed only when every journaled graph dropped (keep-on-partial —
    the next session's stale sweep retries). Skip-on-non-loopback (cycle-4
    P1-8): ALLOW_REMOTE sessions end green.
    """
    from tortoise.config import is_db_uri as _is_db_uri_srv
    uri = os.environ.get("TORTOISE_DB_URI", "")
    if not uri or not _is_db_uri_srv(uri):
        yield
        return  # embedded lane — nothing to sweep
    import atexit
    import json as _json

    from tests._embedded import (
        _JOURNAL_FILE,
        _leftover_sweep,
        _read_journal,
        _session_end_own_sweep,
        _stale_sweep,
        _sweep_proj,
    )
    from tortoise.embedded_reaper import _process_start_time, active_suite_markers

    nonce = os.environ["TORTOISE_TEST_SESSION"]
    docker_token = f"{os.getpid()}-{nonce}"
    marker_path = None
    try:
        os.makedirs(_ACTIVE_SUITES_DIR, exist_ok=True)
        marker_path = os.path.join(_ACTIVE_SUITES_DIR, docker_token)
        start = _process_start_time(os.getpid())
        with open(marker_path, "w") as fh:
            fh.write(f"pid={os.getpid()}\n")
            if start is not None:
                fh.write(f"start={start}\n")
    except OSError:
        marker_path = None  # never fail the suite over marker hygiene

    # Session-start stale sweep — our own marker is live, so our own journal
    # (possibly holding module-import appends) is never classified dead.
    try:
        stale = _stale_sweep(uri, skip_on_non_loopback=True)
        if stale.get("stale"):
            print(f"[server-graph-hygiene] stale sweep: "
                  f"{len(stale['stale'])} dead session journal(s) dropped")
    except Exception as exc:
        print(f"[server-graph-hygiene] stale sweep skipped: {exc}")

    _teardown_ran = [False]

    def _atexit_cleanup() -> None:
        if _teardown_ran[0]:
            return
        try:  # noqa: SIM105
            _session_end_own_sweep(uri, _JOURNAL_FILE, skip_on_non_loopback=True)
        except Exception:
            pass  # hygiene never fails the interpreter exit
        if marker_path:
            try:  # noqa: SIM105
                os.remove(marker_path)
            except OSError:
                pass

    atexit.register(_atexit_cleanup)

    yield

    _teardown_ran[0] = True
    # Cycle-5 P2-3: capture the journal size BEFORE the sweep — the sweep
    # deletes the journal, so "journal size" is unreadable after.
    journal_size = len(_read_journal())
    try:
        own = _session_end_own_sweep(uri, _JOURNAL_FILE, skip_on_non_loopback=True)
    except Exception as exc:
        own = {"error": str(exc)}
        print(f"[server-graph-hygiene] session-end sweep failed: {exc}")
    # Cycle-6 P2-16: deferral is PID-grouped — same-pid markers (our own
    # embedded + docker markers) never defer; only a DIFFERENT pid (a
    # genuinely concurrent suite) defers the FULL leftover sweep.
    others = [m for m in active_suite_markers()
              if m.get("pid") != os.getpid()]
    full = None
    if not others:
        try:
            full = _leftover_sweep(uri, skip_on_non_loopback=True)
        except Exception as exc:
            full = {"error": str(exc)}
            print(f"[server-graph-hygiene] leftover sweep failed: {exc}")
    if marker_path:
        try:  # noqa: SIM105
            os.remove(marker_path)
        except OSError:
            pass
    # E2E-7 bound (cycle-8 P2-11): while last suite standing, post-sweep
    # GRAPH.LIST must be < journal_size + constant — the sweep's GRAPH.DELETE
    # leaves only pre-existing/foreign graphs. SOFTENED from a hard assert
    # (divergence documented in the epic changelog): a pre-existing dev
    # docker with many non-test graphs must not fail the suite at teardown
    # (cycle-8 P2-3 — hygiene never fails the suite); a trip is logged loudly
    # and mirrored to the hygiene log so the E2E-7 leak stays visible.
    if not others and not own.get("skipped") \
            and full and full.get("full_sweep", False):
        try:
            with _sweep_proj(uri) as probe:
                graph_count = len(probe.db.list_graphs() or [])
            bound = journal_size + _SERVER_SWEEP_GRAPH_LIST_CONSTANT
            if graph_count >= bound:
                msg = (f"GRAPH.LIST {graph_count} >= journal size {journal_size} "
                       f"+ constant {_SERVER_SWEEP_GRAPH_LIST_CONSTANT} — "
                       f"journaled graphs were NOT all deleted (E2E-7 leak)")
                print(f"[server-graph-hygiene] WARNING: {msg}")
                try:
                    log_dir = os.environ.get("RUNNER_TEMP") \
                        or tempfile.gettempdir()
                    with open(os.path.join(log_dir, "server-hygiene-end.json"),
                              "w") as fh:
                        _json.dump({"graph_list": graph_count,
                                    "journal_size": journal_size,
                                    "bound": bound, "violation": msg},
                                   fh, indent=2)
                except Exception:
                    pass
        except Exception as exc:
            print(f"[server-graph-hygiene] GRAPH.LIST bound check skipped: {exc}")


# ── Epic #1647 Task 4 (P2): session-start backend-identity tripwire ────────
@pytest.fixture(scope="session", autouse=True)
def _assert_backend_identity():
    """Epic #1647 E2E-6 tripwire: on docker-URI sessions, the session must
    be server mode — a dormant redirect would silently run the migrated
    suite on embedded and pass green. Cycle-2 P0-2: a non-loopback URI fails
    here, before ANY test writes. Cycle-2 P2-6: TORTOISE_TEST_EXPECT_URI=1
    (CI docker halves) fails a URI-less session instead of green-passing on
    the carve-out shape.

    Cycle-5 P1-4: the probe TRAVERSES THE REDIRECT. `from_uri` builds
    host-mode DIRECTLY, so `_is_embedded` is False on any reachable server
    regardless of redirect state: an inert redirect could never be detected
    (vacuous — the cycle-4 probe). The helper `_tripwire_probe()`
    (tests/test_tripwire.py) constructs via `path=` from a test_-named
    frame — `_caller_test_stem()` resolves to the non-exempt stem
    "test_tripwire", the redirect arms under URI + TEST_MODE, and
    `_is_embedded is False` IFF the redirect is armed — an inert redirect
    leaves the probe embedded and this assert fails at session start. An
    unreachable server raises during probe construction (the server-mode
    health check fails loud) — also a session failure, never a skip
    (D-4=A fail-closed).

    Records the observed backend into the session-scoped BackendIdentity
    record (tests._embedded.BACKEND_IDENTITY) so other conftest machinery
    (skip-guard, manifest, the Task 5 embedded_only hook) reads the lane
    without re-probing.

    Divergence note (deep-review Issue 4): the supported-URI gate is
    `is_db_uri` (scheme split on "://"), while the redirect's own gate is
    `_is_supported_uri_scheme` (split on ":") — a malformed value like
    "docker:foo" reads embedded here but the redirect refuses it at every
    construction (hostless → non-loopback RuntimeError). Fails closed
    either way (never a vacuous green); the predicate split is left
    untouched because is_db_uri is the wide seam predicate.
    """
    from tortoise.config import is_db_uri, is_loopback_uri  # shared predicates
    uri = os.environ.get("TORTOISE_DB_URI", "")
    # VGATE P2-2: EXPECT_URI must fail not only on an UNSET URI but also on
    # a set-but-unsupported-scheme URI (postgres://... or a bare path) —
    # either way the session would run the embedded lane and green-pass on
    # the wrong backend (the exact vacuous-pass class EXPECT_URI exists to
    # close). is_db_uri() covers both: False for empty and for any
    # non-supported value.
    if os.environ.get("TORTOISE_TEST_EXPECT_URI") == "1" and not is_db_uri(uri):
        if not uri:
            pytest.fail(
                "TORTOISE_TEST_EXPECT_URI=1 but TORTOISE_DB_URI unset — the "
                "docker-half session must run against the server (epic #1647 "
                "E2E-6)")
        pytest.fail(
            f"TORTOISE_TEST_EXPECT_URI=1 but TORTOISE_DB_URI {uri!r} is not "
            f"a supported connection URI — the docker-half session must run "
            f"against the server (epic #1647 E2E-6)")
    if not uri or not is_db_uri(uri):
        # Embedded session (carve-out) — the tripwire is inert, but the
        # backend identity is still recorded for conftest machinery.
        from tests._embedded import BACKEND_IDENTITY
        BACKEND_IDENTITY.backend = "embedded"
        BACKEND_IDENTITY.uri = uri
        yield
        return
    if not is_loopback_uri(uri) and os.environ.get("TORTOISE_TEST_ALLOW_REMOTE") != "1":
        pytest.fail(
            f"TORTOISE_DB_URI {uri!r} is not loopback — refusing before "
            f"any test writes (epic #1647 D-4/P0-2); set "
            f"TORTOISE_TEST_ALLOW_REMOTE=1 to override")
    from tests._embedded import BACKEND_IDENTITY
    from tests.test_tripwire import _tripwire_probe
    try:
        probe = _tripwire_probe()  # cycle-5 P1-4: through the redirect, not around it
    except Exception as exc:
        # Re-review Issue 1: remove the session journal ONLY for
        # connection-class failures. On a dead/unreachable server the
        # redirect journaled the probe mint BEFORE the failed connect — the
        # graph never came to exist, and this session's sweeps cannot
        # connect to drop the entry (keep-on-partial would retain the
        # journal in ACTIVE_SUITES_DIR until a later docker session's stale
        # sweep). Classification note (cycle-3 re-review): the server-mode
        # health check swallows the raw redis exception and raises the
        # RuntimeError "DB health check failed...", so refused-connect,
        # dropped-SYN timeout, and mid-session server death ALL classify as
        # journal-removal — a graph that did come to exist before the
        # failure is bounded by the journal-independent last-suite-standing
        # leftover sweep (test_-prefixed, scope=None), so no leak. Only a
        # NON-server failure (a redirect bug on a reachable URI) keeps the
        # journal, letting the session-end/stale sweeps drain real mints.
        from redis.exceptions import (  # noqa: I001 (late, deliberate)
            ConnectionError as _RedisConnError,
            TimeoutError as _RedisTimeoutError,
        )
        _conn_class = (_RedisConnError, _RedisTimeoutError, OSError)
        if isinstance(exc, _conn_class) or (
                isinstance(exc, RuntimeError)
                and "health check failed" in str(exc)):
            from tests._embedded import _remove_journal_file
            _remove_journal_file(os.environ.get("TORTOISE_TEST_JOURNAL_FILE", ""))
        raise
    try:
        assert probe._is_embedded is False, (
            "backend-identity tripwire: server session but the probe is "
            "embedded — the redirect is INERT; the migrated suite would "
            "green-pass on the wrong backend (epic #1647 E2E-6)")
    finally:
        probe.close()
    BACKEND_IDENTITY.backend = "server"
    BACKEND_IDENTITY.uri = uri
    yield


@pytest.fixture(scope="session")
def backend_identity():
    """The recorded session backend (epic #1647 E2E-6) — which lane this
    session actually ran on, recorded by the session-start tripwire
    (_assert_backend_identity). Consumers read this instead of re-probing:
    backend == "server" when the tripwire's redirect-traversing probe ran
    server-mode, "embedded" on URI-less sessions. The record is also
    reachable as tests._embedded.BACKEND_IDENTITY for non-fixture conftest
    machinery."""
    from tests._embedded import BACKEND_IDENTITY
    return BACKEND_IDENTITY


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
    # #1709: recovery limiter buckets (per-IP + per-token) are in-memory per
    # process and share the module-scoped TestClient host — reset per test.
    recover_buckets = getattr(ha_mod, "_RECOVER_BUCKETS", None)
    if recover_buckets is not None:
        recover_buckets.clear()
    recover_token_buckets = getattr(ha_mod, "_RECOVER_TOKEN_BUCKETS", None)
    if recover_token_buckets is not None:
        recover_token_buckets.clear()
    try:
        from tortoise.abuse import SIGNUP_TRACKER
        SIGNUP_TRACKER.reset()
    except (ImportError, AttributeError):
        pass
    try:
        from tortoise.abuse import RECOVERY_TRACKER
        RECOVERY_TRACKER.reset()
    except (ImportError, AttributeError):
        pass
    yield
    _register_buckets.clear()
    if signup_buckets is not None:
        signup_buckets.clear()
    if recover_buckets is not None:
        recover_buckets.clear()
    if recover_token_buckets is not None:
        recover_token_buckets.clear()
    try:
        from tortoise.abuse import SIGNUP_TRACKER
        SIGNUP_TRACKER.reset()
    except (ImportError, AttributeError):
        pass
    try:
        from tortoise.abuse import RECOVERY_TRACKER
        RECOVERY_TRACKER.reset()
    except (ImportError, AttributeError):
        pass


# ── Epic #1647 Task 5 (D-2=A): the embedded_only marker hook ──────────────
# The named helper lives in tests/_embedded.py (NOT conftest): an import via
# `tests.conftest` would re-execute conftest's top-level code mid-session
# (pytest loads conftest as the top-level `conftest` module; the
# namespace-package tests.conftest import is a SECOND instance that
# overwrites TORTOISE_TEST_SESSION and re-points the journal — review P0).
from tests._embedded import _embedded_only_skip  # noqa: E402


@pytest.fixture(autouse=True)
def _embedded_only_skip_hook(request):
    """Autouse D-2 skip: supported TORTOISE_DB_URI set + `embedded_only`
    marker present -> visible pytest.skip with the embedded-only reason.
    Delegates to the named helper `_embedded_only_skip` (cycle-5 P2-12) so
    the marker-semantics test drives the exact hook."""
    _embedded_only_skip(request)
