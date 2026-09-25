"""D11 #578 — shared fixtures for the epic E2E suite.

provision_test_user: creates a provisioned test user (team + membership +
key) with tier + demo_seed control. Tier injection writes the Team node
directly (no user-facing tier path in v1). Used by E2E-1/3/4/5/10/11/12/13.
"""
from __future__ import annotations

import os
import shutil
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
# #2850: the loop-stall watchdog exits the process (os._exit) when the event
# loop stops ticking — that is the point in production, and catastrophic in a
# test runner that DELIBERATELY stalls the loop (the /healthz staleness tests)
# or that simply holds a GIL-heavy test for longer than the threshold. Tests
# exercise the watchdog with an injected exit_fn; the real one stays disarmed
# for the whole session.
os.environ.setdefault("TORTOISE_LOOP_STALL_EXIT_S", "0")
# #2850: the dedicated liveness listener defaults to the fixed 0.0.0.0:9090
# deployment contract. An ephemeral port for the test session keeps parallel
# test sessions on one host from fighting over it (and never exposes a
# listener on a shared CI box); the 9090 contract is asserted directly.
os.environ.setdefault("TORTOISE_HEALTHZ_PORT", "0")
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

# #4740: the session-end sweep report's field set is owned by the report
# builder in `tortoise/embedded_reaper.py` (`_HYGIENE_REPORT_FIELDS`), which
# this conftest imports. The orphan-bound harness reads the contract and the
# builder from that module; there is no second declaration here to drift.

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
from tests._embedded import (  # noqa: E402
    _assert_p4_uri_required,
    serialize_embedded_construction,
)
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


@pytest.fixture(scope="session", autouse=True)
def _fresh_capture_spool():
    """#3963: start every pytest SESSION with a fresh capture spool.

    Under pytest, ``tortoise.capture_spool.spool_dir()`` redirects to
    ``<tmp>/tortoise-capture-spool-tests/<sha256(test-id)>`` — keyed by test id
    so a test AND any process it spawns share one spool (the fail-closed guard
    that stops a test touching the developer's real captures). The key is
    stable across RUNS, so a second run of the same test would inherit run 1's
    `filed_key` and silently skip the capture — the suite would stop being
    re-runnable. Wipe the tree once per session.
    """
    import shutil
    from pathlib import Path

    shutil.rmtree(Path(tempfile.gettempdir()) / "tortoise-capture-spool-tests",
                  ignore_errors=True)
    yield


@pytest.fixture(scope="session", autouse=True)
def _serialize_embedded_construction():
    """#3546: install ONE process-wide embedded construction lock, once.

    The #3505 double-start race is a PROCESS-wide invariant (see
    `tests/_embedded.EMBEDDED_CONSTRUCTION_LOCK` for the mechanism and its
    scope), so it can never be closed by per-file lock objects: the two copies
    #3511 installed each serialized only their own module, leaving every other
    embedded fixture exposed. `tests/test_invites_http.py` was one — its
    seeded Membership landed on the loser daemon, so the app's registry anchor
    read an empty graph and POST /v1/invites 403'd
    ("Requires owner or admin role in team") instead of reaching its 402/200
    branch, reddening 20 of its tests.

    Session-scoped and autouse so it is in place before the first test
    constructs anything, and so it covers files that do not exist yet. Declared
    immediately AFTER `_p4_uri_required` so that gate stays the first session
    fixture to run (same-scope autouse fixtures are set up in declaration
    order); this fixture never needs a URI itself.
    """
    mp = pytest.MonkeyPatch()
    serialize_embedded_construction(mp)
    yield
    mp.undo()


@pytest.fixture(scope="session", autouse=True)
def _reclaim_session_tmpdirs():
    """#4096: reclaim SESSION-scoped test temp trees at the very end of the run.

    `tests/conftest.py:shared_embedded_db` and `tests/_embedded.py:shared_proj`
    each `mkdtemp` one shared tree for the whole session and (before this) never
    removed it. They cannot reclaim locally: their consumers never close their
    servers, and the pass-2 sweeps in `_redislite_hygiene` / `_server_graph_hygiene`
    read the socket/pid markers *inside* those trees — removing the tree in the
    shared fixture's own teardown (which reverse setup order places BEFORE the
    sweeps) would destroy that evidence and could orphan a live redislite server
    (#4068/#1005).

    `autouse`, and with no dependency on the shared fixtures, so it is set up
    regardless of which tests request the shared trees; it reads the registry they
    populate (`tests._embedded.SESSION_TMPDIRS`). The teardown-last edge is
    **structural, not alphabetical**: `_redislite_hygiene` declares this fixture as
    a dependency, so setup runs reclaim -> redislite -> server_graph and
    reverse-order teardown runs server_graph -> redislite -> reclaim. (pytest orders
    same-scope autouse fixtures by NAME, not declaration order — a rename would
    silently invert a declaration-order assumption.)
    """
    yield
    from tests import _embedded as _embedded_mod
    _embedded_mod.drain_session_tmpdirs()


_CALIBRATION_POSTURE_ENV = "TORTOISE_EP_REQUIRE_CALIBRATION"


@pytest.fixture(autouse=True)
def _restore_ep_calibration_posture():
    """Isolate the process-global fail-closed calibration knob per test.

    Three suites disable it for their own synthetic fixtures with a bare
    ``os.environ.setdefault`` (``test_decide``, ``test_ingest_safety``,
    ``epic903_fixtures.fresh_sdk``), and that mutation is never undone — so a
    test running LATER in the same process silently inherits the DISABLED
    posture. ``test_calibration.py::test_require_calibration_default`` asserts
    the fail-closed DEFAULT, so it reds whenever it happens to run after one of
    them in the same shard (reproduced: ``pytest tests/test_decide.py
    tests/test_calibration.py::test_require_calibration_default``).

    Snapshot/restore the ONE knob around every test so each suite's posture
    stays its own. A conftest guard rather than call-site edits: the mutators
    are shared helpers (``epic903_fixtures.fresh_sdk``) and new callers would
    re-introduce the leak.
    """
    saved = os.environ.get(_CALIBRATION_POSTURE_ENV)
    yield
    if saved is None:
        os.environ.pop(_CALIBRATION_POSTURE_ENV, None)
    else:
        os.environ[_CALIBRATION_POSTURE_ENV] = saved


@pytest.fixture
def provision_test_user():
    created = []
    tmpdirs = []

    def factory(tier: str = "free", demo_seed: bool = True):
        tmpdir = tempfile.mkdtemp()
        tmpdirs.append(tmpdir)
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
        team = sdk.org_create(f"e2e-{os.urandom(4).hex()}")
        lim = tier_limits(tier)
        # #310 (review fix 16b): mirror production CREATE semantics — write
        # max_points (= max_graph_nodes, GAP-B mapping). #4010: max_sessions is
        # written as NULL (unlimited) — it is never a cap, and a leftover
        # number here would re-create exactly the trap the issue names.
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.tier=$tier, t.max_graphs=$mg, "
            "t.max_users=$mu, t.max_api_keys=$mk, t.max_points=$mp, "
            "t.max_sessions=$ms, t.ops_allowance=$ops, t.graph_size_cap=$nodes",
            params={"id": team["id"], "tier": tier,
                    "mg": lim["max_graphs_per_team"], "mu": lim["max_users_per_team"],
                    "mk": lim["max_api_keys"], "mp": lim["max_graph_nodes"],
                    "ms": None, "ops": lim["included_write_ops_per_month"],
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
        return {"sdk": sdk, "org_id": team["id"], "api_key": team["api_key"],
                "graph_name": team["graph_name"], "org_name": team["name"],
                "user_id": user_id}

    yield factory
    for sdk in created:
        try:  # noqa: SIM105
            sdk.close()
        except Exception:
            pass
    # #4096: close first (above), then reclaim — removing the tree under a live
    # redislite server would orphan it.
    for d in tmpdirs:
        shutil.rmtree(d, ignore_errors=True)


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

    from tests._embedded import register_session_tmpdir

    tmpdir = _tf.mkdtemp(prefix="tortoise_shared_embedded_")
    register_session_tmpdir(tmpdir)
    db_path = os.path.join(tmpdir, "shared.db")
    yield db_path


@pytest.fixture(scope="session", autouse=True)
def _redislite_hygiene(_reclaim_session_tmpdirs):
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

    # #4740 review 11: the builder and the probe are reached through the
    # MODULE attribute rather than a bare imported name.
    from tortoise import embedded_reaper
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
                    # The budget bounds ITERATIONS, not wall time — one
                    # iteration at batch 200 with kill_pacing 0.4 takes ~80s
                    # of pacing, so a multi-hundred backlog can run past the
                    # 30s soft budget (review P2; it still terminates). The
                    # cron sweeps every 20 min make up the difference.
                    # #4740 review 9: the raw composition — the pre-sweep
                    # probe (`before`), the sweep, the post-sweep probe
                    # (`left`) and their arrangement into the report — lives in
                    # `embedded_reaper.build_end_sweep_report` (behaviourally
                    # pinned in tests/test_reaper.py). Both probes run while
                    # TORTOISE_REAPER_MIN_UPTIME still holds this sweep's own
                    # setting (the `finally` below restores it); `None` (not 0)
                    # marks a failed probe. The module-attribute call and
                    # probe (see the import block above) are pinned by
                    # `test_conftest_sweep_returns_build_end_sweep_report`.
                    return embedded_reaper.build_end_sweep_report(
                        lambda: _run_sweep(
                            dry_run=False, batch_size=SWEEP_BATCH_SIZE,
                            only_safe=only_safe, jobs=8, kill_pacing=0.4,
                            # Epic #1647 (PR #1684 CI-fix): the suite is
                            # ENDING — a server that ignores SIGTERM for 3s
                            # gets SIGKILL regardless; the default 10s wait ×
                            # many servers compounds past pytest-timeout under
                            # CI load (observed: TestMcpHandlers teardown
                            # timed out at 600s with the reaper in _kill).
                            sigterm_timeout=3.0,
                            # deadline is threaded INTO reap(): the eager
                            # pre-probe cache is skipped and the record loop
                            # aborts once the budget is spent — the end-sweep
                            # can never run past pytest-timeout on a large
                            # stale backlog (observed: >300s teardown timeout
                            # redding the leg with the reaper in _kill/probe).
                            deadline=deadline),
                        deadline,
                        embedded_reaper.live_embedded_server_count,
                    )
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
    # #1005 (epic #1647 E2E-7): close this process's OWN live embedded clients
    # BEFORE the end-sweep probes the population. The sweep's `left` is a
    # pgrep taken during fixture teardown; while the suite's own clients are
    # still open, every server they hold reads as a live-client server and
    # reap() declines it (embedded_reaper.py's 0-client gate) — so `left`
    # measures the suite's own client count (147 in the post-#4927 CI
    # sample) while the workflow's post-exit probe measures the residue (5).
    # Comparing those two is not a same-seam comparison, so `COUNT <= left`
    # is structurally incapable of failing on the leak it exists to catch.
    # Closing through the SAME idempotent seams atexit uses
    # (close_embedded_clients — the #1371 fast-close, the guarded `_t_close`,
    # and the raw-client `_cleanup` fallback) disconnects the pools while the
    # process is alive, so the sweep probe now measures the population the
    # workflow probe will, and the bound becomes meaningful. This runs AFTER
    # the other session finalizers: `_redislite_hygiene` tears down last
    # (every fixture that depends on it, including `_server_graph_hygiene`,
    # has already been finalized), so no finalizer is left holding a client
    # it still needs. Best-effort: hygiene never fails the suite over this.
    try:
        from tortoise.embedded_lifecycle import close_embedded_clients
        _pre_closed = close_embedded_clients()
        if _pre_closed:
            print(
                "[redislite-hygiene] in-process close before end sweep: "
                f"{_pre_closed} client(s)")
    except Exception:
        pass  # a failed close must not fail the suite (the sweep still runs)
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

    Failure policy (cycle-8 P2-3/P2-4): log-and-continue; the journal file is
    removed when no OWNED graph FAILED to drop (keep-on-partial — a failed
    drop keeps the journal so the next session's stale sweep retries it). A
    PRESERVED non-owned name does NOT keep the journal (#7795): retrying
    cannot make it ours, so the journal is consumed while those graphs
    remain. Skip-on-non-loopback (cycle-4 P1-8): ALLOW_REMOTE sessions end
    green.
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
        _live_graph_names,
        _owned_survivors,
        _read_journal,
        _session_end_own_sweep,
        _stale_sweep,
        _sweep_proj,
        _uri_default_graph_name,
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
    journal_names = set(_read_journal())
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

    # ── E2E-7 gate (#3634 Task 5). A SIBLING of the bound-check `if` above and a
    # direct child of `if not others:` (last-suite-standing only — do NOT widen
    # that). It gates ONLY on `not others` + the three own-sweep flags, NEVER on
    # the bound check's `full_sweep` condition (P1-A, Task 5 review): nested
    # inside that `if`, the gate was DISABLED exactly when the leftover sweep
    # failed or reported full_sweep=False — i.e. precisely when cleanup was
    # incomplete and survivors are most likely. It must also stay OUTSIDE every
    # `try` (an AssertionError under a broad `except Exception` is swallowed and
    # the gate is vacuous). Short-circuit on `error` too: a sweep that RAISED
    # sets own={"error": ...} with no `failed` key, so `not own.get("failed")`
    # alone would run the gate over names a dead sweep left and red the suite
    # (violating cycle-8 P2-3).
    # The nesting is DELIBERATE (SIM102): the `if not others` node must remain a
    # distinct AST ancestor of the gate's Raise (its own guard), not be folded
    # into the three-flag condition — the placement is itself pinned by
    # tests/test_server_hygiene_gate.py.
    if not others:  # noqa: SIM102
        if not own.get("skipped") and not own.get("failed") and not own.get("error"):
            # P1-C (Task 5 review): the survivor probe is the ONLY unguarded
            # server call on the teardown path. Its failure (connection, auth,
            # maxmemory, LOADING, a stall) must print-and-continue like the
            # bound check above — an infra failure that reds the suite is
            # INDISTINGUISHABLE in CI from a real E2E-7 leak, the one signal
            # this gate exists to make unambiguous. Only the genuine leak
            # AssertionError below may raise from this block; a failed probe
            # leaves `live_names` empty, so the gate reports no survivors.
            live_names: set[str] = set()
            try:
                live_names = _live_graph_names(uri)
            except Exception as exc:
                print(f"[server-graph-hygiene] E2E-7 survivor probe failed — "
                      f"gate skipped (infra skip, NOT a leak signal): {exc}")
            survivors = _owned_survivors(journal_names, live_names,
                                         _uri_default_graph_name())
            if survivors:
                raise AssertionError(
                    f"E2E-7: {len(survivors)} owned journalled graph(s) survived the "
                    f"sweep: {sorted(survivors)}")


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


# ── #1930: ambient TORTOISE_PACKS_DIR isolation ───────────────────────────
# The pack-dir env leg (epic #1891 WF-2) makes the whole suite
# ambient-env-sensitive: a developer/CI/operator machine that exports
# TORTOISE_PACKS_DIR must never silently redirect pack resolution in ANY
# test module. Cleared per test so caplog warn assertions are deterministic
# regardless of ordering (the warn-once sentinel is module-level).
@pytest.fixture(autouse=True)
def _packs_env_isolation(monkeypatch):
    """#1930: reset pack-resolution env + module state before each test.

    TORTOISE_PACKS_DIR is deleted unless a test sets it explicitly
    (monkeypatch restores after). domain_loader module state (_registry /
    _env_fallback_key / _PACKS_DIR) and the warn-once sentinel are reset so
    no test leaks pack-resolution state into the next.
    """
    monkeypatch.delenv("TORTOISE_PACKS_DIR", raising=False)
    import tortoise.pack_registry as pack_registry
    from tortoise import domain_loader
    domain_loader._registry = None
    domain_loader._env_fallback_key = None
    domain_loader._PACKS_DIR = None
    getattr(pack_registry, "_WARN_ONCE", set()).clear()
    yield
    domain_loader._registry = None
    domain_loader._env_fallback_key = None
    domain_loader._PACKS_DIR = None


# ── #3818 (P1-2): ambient CODEX_HOME isolation ───────────────────────────
# `capture_install.codex_home()` honors `$CODEX_HOME` for the whole tree, so a
# developer/CI/operator machine that exports it would send every direct
# `install_capture("codex", home=...)` — and every codex test in the suite —
# into the REAL `~/.codex` (the probe run wrote hooks.json +
# hooks/tortoise-session-end.sh there). Cleared per test so no codex test can
# mutate the machine it runs on; the sentinel is what the guard test in
# test_codex_capture_hook.py reads to prove the scrub ran.
@pytest.fixture(autouse=True)
def _codex_home_isolation(monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("TORTOISE_TEST_CODEX_HOME_SCRUBBED", "1")
    yield


# ── #3819 (P1-2): ambient CURSOR_HOME isolation ───────────────────────────
# Cursor has NO config-dir env var (verified: `CURSOR_HOME` appears nowhere in
# Cursor 3.20.21's bundle; it reads `~/.cursor/hooks.json`), so the resolver
# never consults one.  The scrub is DEFENSE-IN-DEPTH: a future code path that
# reintroduced an env-scoped Cursor root cannot silently redirect an install
# away from the real `~/.cursor` during an unrelated test.  It is NOT itself
# the guard — no test can observe a property the code does not consult.  The
# guard that CAN go red is
# `test_no_cursor_test_can_reach_the_real_cursor_store` in
# test_cursor_capture_hook.py, which re-introduces an ambient `CURSOR_HOME`
# (aimed at the live store) and asserts the resolved root is still under the
# tmp tree.
@pytest.fixture(autouse=True)
def _cursor_home_isolation(monkeypatch):
    monkeypatch.delenv("CURSOR_HOME", raising=False)
    yield


@pytest.fixture(autouse=True)
def _disable_embedder_autowarmup(monkeypatch):
    """#2952: keep the engine-init embedder warm-up out of the test suite.

    ``TortoiseSDK._get_proj`` starts a daemon warm-up thread (best-effort,
    #2952 (B)). Unstubbed tests would otherwise trigger a real HF model load
    in the background, and a failed load would emit WARNING noise into
    ``caplog`` assertions. Tests that exercise the warm-up call it directly
    (with a stubbed ``EmbeddingModel.get``).
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "0")
    yield


# ── #3820 (cycle-2 P1): the analytics-alert channel is OFF for every test ───
# The write path now has a terminal outcome, and three of its four exits can
# reach the alert leg (`dropped` at once, `fallback` once the streak crosses
# `_ANALYTICS_FALLBACK_ALERT_AFTER`, and the resolve on a recovered write).
# Unpatched, that leg builds the REAL `AlertStore` from whatever the process
# env carries: on a machine holding production secrets (an ambient
# `DR_ISSUES_PAT` + `R2_*`, or `SUPABASE_SERVICE_ROLE_KEY` with `SUPABASE_URL`
# deleted — which the P1-2 arm classifies as `fallback`) a suite run PUTs to
# prod R2, searches and can CREATE a real `[DR] ANALYTICS_SINK_DEGRADED` GitHub
# issue, and pushes Telegram. The per-file guard this replaces covered only
# `tests/test_analytics_write_path_resolution.py`; this one covers EVERY test.
# `_ANALYTICS_DEGRADED_STREAK` / `_ANALYTICS_RESOLVE_PENDING` /
# `_ANALYTICS_RESOLVE_NOT_BEFORE` are
# process globals with no other reset, the outcome counter is a process-global
# Prometheus `Counter`, and `_ANALYTICS_COUNTS` is the in-process dict the
# incident detail reads — all are reset per test.
#
# The LOCAL JSONL SINK is the fifth write path and is isolated here too: with
# `_ANALYTICS_FALLBACK_PATH` left as the module default (`None`) the writer
# resolves `~/.tortoise/analytics_fallback.jsonl` — the REAL file, and on the
# production box the DR runbook's recovery source (`docs/ops/registry-backup-dr.md`).
# Only four test files ever set the path, so every other emit path appended
# test-fixture ids to the real file. Redirecting it to `tmp_path` closes that
# suite-wide.
_REAL_ANALYTICS_ALERT_STORE = None
# The production module-level ``_ANALYTICS_COUNTS`` object, captured before the
# isolation replaces the attribute. The replacement is a fresh derivation each
# test, which is right for isolation but HIDES whether the real dict is itself
# derived — a test pinning that derivation must read this captured object.
_REAL_ANALYTICS_COUNTS = None


@pytest.fixture(autouse=True)
def _analytics_alert_isolation(monkeypatch, tmp_path):
    """Never let a test build a real analytics sink incident (#3820 P1).

    Patches the documented indirection seam ``_analytics_alert_store`` to a
    no-op and clears the episode latch, the degraded streak, the outcome
    counter and the in-process outcome counts. The local JSONL sink is
    redirected into the test's ``tmp_path`` as well, so no test can append to
    the REAL ``~/.tortoise/analytics_fallback.jsonl``. Tests that mean to
    exercise the store install their own fake via ``monkeypatch.setattr`` in
    the test body (that runs later, so it wins); the few that pin the REAL
    construction leg opt back in with the ``real_analytics_alert_store``
    fixture.
    """
    global _REAL_ANALYTICS_ALERT_STORE, _REAL_ANALYTICS_COUNTS
    import tortoise.hosted_api as ha
    import tortoise.monitoring as mon

    if _REAL_ANALYTICS_ALERT_STORE is None:
        _REAL_ANALYTICS_ALERT_STORE = ha._analytics_alert_store
    monkeypatch.setattr(ha, "_analytics_alert_store", lambda: None)
    monkeypatch.setattr(ha, "_ANALYTICS_DEGRADED_STREAK", 0)
    # `PENDING=True, NOT_BEFORE=None` is the process-start state: an incident
    # may have been left open by a dead process, so the first delivered write
    # probes (the CLEAN state omits the probe).
    monkeypatch.setattr(ha, "_ANALYTICS_RESOLVE_PENDING", True)
    monkeypatch.setattr(ha, "_ANALYTICS_RESOLVE_NOT_BEFORE", None)
    # #3820 cycle-9 P2-2: the resolve's in-flight claim. A leaked ``True`` from
    # one test would make every later test's resolve SKIP, so the suite-wide
    # isolation resets it with the rest of the resolve state.
    monkeypatch.setattr(ha, "_ANALYTICS_RESOLVE_INFLIGHT", False)
    if _REAL_ANALYTICS_COUNTS is None:
        _REAL_ANALYTICS_COUNTS = ha._ANALYTICS_COUNTS
    monkeypatch.setattr(ha, "_ANALYTICS_COUNTS",
                        {o: 0 for o in ha._ANALYTICS_OUTCOMES})
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH",
                        str(tmp_path / "analytics_fallback.jsonl"))
    mon.ANALYTICS_OUTCOME_COUNT.clear()


@pytest.fixture
def real_analytics_alert_store(monkeypatch, _analytics_alert_isolation):
    """Opt out of ``_analytics_alert_isolation`` for the REAL store leg.

    Only for tests that pin what the real builder does (T14/T15 in
    ``tests/test_analytics_fallback_alert.py``). Those replace the object store
    (``_backup_storage`` -> ``MemoryStorage``) and both egress endpoints, so
    restoring the real builder cannot reach R2, GitHub or Telegram.

    This fixture does NOT itself patch the object store or the egress
    callables — a requester that restores the real builder without them can
    reach real infrastructure. Every requester must install both (as T14 and
    T15 do).
    """
    import tortoise.hosted_api as ha

    real = _REAL_ANALYTICS_ALERT_STORE
    assert real is not None, "real builder not captured — check fixture order"
    monkeypatch.setattr(ha, "_analytics_alert_store", real)
    return real


@pytest.fixture
def real_analytics_counts(_analytics_alert_isolation):
    """The production module-level ``_ANALYTICS_COUNTS`` object.

    ``_analytics_alert_isolation`` REPLACES the module attribute with a fresh
    derivation each test. That is right for isolation, but it masks whether the
    REAL module-level dict is itself derived: a test pinning the derivation
    must read this captured object instead of the replacement.
    """
    assert _REAL_ANALYTICS_COUNTS is not None, (
        "real counts not captured — check fixture order")
    return _REAL_ANALYTICS_COUNTS


_REAL_OPERATOR_ALERT_STORE = None


@pytest.fixture(autouse=True)
def _operator_alert_isolation(monkeypatch):
    """Never let a test build a real #3981 operator incident.

    Patches the operator plane's OWN seam (``operator_alert.alert_store``) so it
    is isolated INDEPENDENTLY of ``_analytics_alert_isolation`` — notably, a test
    that restores the real analytics builder (``real_analytics_alert_store``)
    must not thereby un-isolate this plane. Resets the throttle/latch/bound and
    asserts the pool drained, so a worker cannot run after the test (and cannot
    write state into the next test). A test that needs the REAL builder requests
    ``real_operator_alert_store`` — a test that calls it under the autouse patch
    would silently get ``None`` and could never satisfy its own assertion.
    """
    global _REAL_OPERATOR_ALERT_STORE
    import tortoise.operator_alert as oa
    from tortoise import alert_channel

    if _REAL_OPERATOR_ALERT_STORE is None:
        _REAL_OPERATOR_ALERT_STORE = oa.alert_store
    monkeypatch.setattr(oa, "alert_store", lambda: None)
    oa.reset_operator_alert_state_for_tests()
    # The light leg's MemoryStorage is a PROCESS-wide singleton: a title filed
    # by one test stays "already filed" for the next, which is a latent DEDUP
    # collision rather than anything a test asked for. Reset it per test — and
    # the HOSTED leg's own singleton too: under TORTOISE_BACKUP_STORAGE=memory
    # `hosted_api._backup_storage` returns it, so the same collision is reachable
    # through the hosted builder (e.g. a cap-firing test), and resetting only one
    # leg leaves the suite order-dependent. Only touched when that module is
    # already loaded — this fixture must not import the hosted app for every test.
    alert_channel.reset_memory_storage_for_tests()
    _ha = sys.modules.get("tortoise.hosted_api")
    if _ha is not None:
        _ha._MEMORY_BACKUP_STORE = None
    yield
    # Honest limit: a handle aged past _INFLIGHT_STALE_S is dropped from _HANDLES,
    # so a genuinely wedged worker is untracked here and this join cannot speak for
    # it (its reservation is deliberately still held). This asserts the normal
    # case — nothing an individual test dispatched is still running when it ends.
    assert oa.join_operator_alerts(timeout=5.0) == 0, (
        "operator-alert pool did not drain")


@pytest.fixture
def real_operator_alert_store(monkeypatch, _operator_alert_isolation):
    """OPT OUT of ``_operator_alert_isolation`` for the builder-under-test.

    Restores the captured REAL ``operator_alert.alert_store``. As with
    ``real_analytics_alert_store``, this does NOT itself patch the object store
    or the egress callables: a requester that restores the real builder without
    them can reach real infrastructure, so every requester must install both.
    """
    import tortoise.operator_alert as oa

    assert _REAL_OPERATOR_ALERT_STORE is not None, (
        "real builder not captured — _operator_alert_isolation must run first"
    )
    monkeypatch.setattr(oa, "alert_store", _REAL_OPERATOR_ALERT_STORE)
    return _REAL_OPERATOR_ALERT_STORE


@pytest.fixture
def force_sparse_tfidf(monkeypatch):
    """#2573/#2772: pin the sparse TF-IDF fallback (no embedder) for the test.

    CI may run with the bge embedder cache present or absent (python-ci.yml
    treats a failed HF download as a WARN and lets the suite run
    TF-IDF-degraded), so any test pinning exact retrieval pool sizes must
    pin the embedder state or it is environment-dependent. Request this
    fixture in such a test; assertions are unchanged (this is the
    ``tests/eval/retrieval/test_oracle.py`` ``_force_sparse_tfidf`` pattern
    promoted to a shared named home — older inline copies predate it).
    """
    from tortoise.embeddings import EmbeddingModel
    monkeypatch.setattr(EmbeddingModel, "get", classmethod(
        lambda cls, load_timeout=None: None))
    return None


# ── #4069: per-test temp-directory teardown ────────────────────────────────
# `$TMPDIR` churned to 362,962 entries with nothing older than three days:
# the suite's `tempfile.mkdtemp(prefix=...)` call sites create a directory
# per invocation and never remove it, and that tree is the one the reaper's
# socket census walks (~41% CPU per call at 224k depth-2 entries). The fix
# is structural — re-exported here so the autouse fixture applies suite-wide,
# tracking every `mkdtemp` a test creates and removing it at teardown. The
# mechanism and its safety properties live in `tests/_tmpdir_hygiene.py`;
# the operator-invoked backlog sweep is `tools/tmpdir_sweep.py`.
from tests._tmpdir_hygiene import track_tempfile_artifacts  # noqa: E402, F401
