"""#3287 / #2988 / #3243 — the selfhost health endpoints must not share fate with work.

Three properties, one seam, pinned at two layers each:

* **#3287 — the readiness probe owns a DAEMON pool.** ``/health/ready`` hands
  its probe to ``monitoring.daemon_worker``, never the loop's SHARED default
  ``ThreadPoolExecutor``, whose queue is UNBOUNDED: a submission never fails, it
  just WAITS. ``asyncio.wait_for`` bounds the ANSWER, not the TRUTH — a starved
  readiness probe reported not_ready while the DB was fine.

  Measured on ``main`` with both probes stubbed to ~0 ms and one unrelated task
  occupying the default executor's only worker:

      /health        : NO ANSWER within 8.0s        (probe never ran)
      /health/ready  : HTTP 503 after 6021ms        (probe never ran)

  ``publish-selfhost.yml`` curls ``/health/ready`` and fails the publish on a
  non-200, so that false 503 blocks a release of a perfectly healthy image.

* **#2988 — ``/health`` answers from IN-MEMORY state.** The pre-#2988 handler
  AWAITED a probe, so its answer was on the request path; #3287 gave the probe
  its own pool, which removed the STARVATION but left the answer waiting on a
  worker. The acceptance bullet is stronger — "returns from in-memory state in
  <500 ms with the loop's default executor fully saturated" — so ``/health`` now
  reads ``monitoring.HealthProbe.snapshot()`` (the hosted /health coordinator,
  #3062) and hands off to NO pool at all. A background task
  (``_health_probe_loop``) keeps the verdict fresh.

* **#3243 — the cold-start allowance rides the REFRESHER, not the read.**
  ``PROBE_TIMEOUT`` (1.5 s) covers both probe phases and the projection
  cold-start scales with graph size, so a reachable large graph used to time out
  during setup and report ``db.ok=false`` on a liveness surface. The allowance
  (``probe_setup_timeout()``, the #3143/#3217 knob) is passed by the refresher,
  where a slow cold start costs nobody a request — the shape #3243's own notes
  prescribe ("derive a liveness allowance … on a coordinator whose *read* stays
  in-memory").

The suite is deliberately two-layered, in the style of
``test_health_ready_nonblocking.py``:

* **structural pins** — the shape cannot come back through a refactor that keeps
  the observable response intact. A response is identical whether the probe ran
  on a private pool or on a shared one, and identical whether the liveness read
  was in-memory or merely fast; only the AST can see it.
* **behavioural tests** — prove the new dispatch actually works (right pool,
  right thread, probe really ran), that the in-memory read really is independent
  of the executor, and that a slow cold start is rescued.
"""

from __future__ import annotations

import ast
import asyncio
import concurrent.futures
import contextlib
import contextvars
import threading
import time
from pathlib import Path

import pytest

import tortoise.monitoring as _mon

REPO = Path(__file__).resolve().parent.parent
SELFHOST = REPO / "tortoise" / "selfhost.py"
SELFHOST_SRC = SELFHOST.read_text()

#: The GENUINE ``monitoring.probe_db``, captured at import time — before any
#: fixture stubs it. The #3243 end-to-end test must exercise the real budget
#: arithmetic through the coordinator: a stub that merely records the
#: ``setup_timeout`` it was handed cannot distinguish the fix from the bug.
_REAL_PROBE_DB = _mon.probe_db

#: The readiness lane's name + width constants, read from the source under test.
READY_NAME_CONST = "_READY_PROBE_WORKER"
READY_WIDTH_CONST = "_READY_PROBE_WORKERS"
#: name constant -> the production pool name. It is ALSO the daemon thread-name
#: prefix (``_SingleSlotWorker`` suffixes a multi-worker pool's threads with
#: ``-<i>``), so it is pinned in the AST test below and asserted against the
#: threads the probes actually run on — a silent rename must not make the thread
#: assertions vacuous.
READY_POOL_NAME = "selfhost-ready-probe"
#: width constant -> the FEWEST workers it may carry.
#:
#: Readiness 6: this pool must not be NARROWER than the shared default executor
#: it replaced — ``min(32, cpu+4)`` = 6 on the 2-vCPU hosted box. Isolation is
#: the fix; shrinking the pool is not. At 2, three concurrent ``/health/ready``
#: requests queue the third, which then spends its whole
#: ``_READY_PROBE_TIMEOUT_S`` waiting for a worker and reports a false 503 for a
#: healthy DB — the defect this change exists to remove, re-entered through
#: readiness fan-in. Exercised by
#: ``test_readiness_fan_in_does_not_produce_a_false_503``.
READY_MIN_WORKERS = 6

#: Names the LIVENESS handler must NOT reference: the coordinator makes every
#: one of them wrong on the request path (#2988's in-memory bullet).
_LIVENESS_FORBIDDEN_NAMES = {
    "_submit_probe", "_probe_worker", "probe_db", "wrap_future",
    "run_in_executor", "to_thread", "_get_proj",
}
#: The same set for an ATTRIBUTE call (``sdk._get_proj()``): ``_names_in`` only
#: collects ``ast.Name``, so a forbidden callee reached through a receiver would
#: otherwise be invisible to the pin (the readiness sibling guards ``_get_proj``
#: this way too).
_LIVENESS_FORBIDDEN_ATTRS = {"query", "submit", "run_in_executor", "to_thread",
                             "wrap_future", "snapshot", "_get_proj"}


def _module_assign(name: str) -> ast.Assign:
    for node in ast.parse(SELFHOST_SRC).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node
    raise AssertionError(f"module-level {name} not found in selfhost.py")


def _module_literal(name: str):
    """The literal value of a module-level assignment (str or int)."""
    value = _module_assign(name).value
    assert isinstance(value, ast.Constant), (
        f"{name} must be a plain literal constant (got {ast.dump(value)[:80]}) — it is "
        "read by the pins and by the fixture, so it must not be computed"
    )
    return value.value


def _prod_workers(width_const: str) -> int:
    """The lane's production width, read from the source under test.

    The assertions must mirror PRODUCTION's width, not invent their own: a test
    that hardcoded 2 would let a too-narrow production width pass.
    """
    width = _module_literal(width_const)
    assert isinstance(width, int) and not isinstance(width, bool), (
        f"{width_const} must be an int, got {width!r}"
    )
    return width


# ── AST helpers ────────────────────────────────────────────────────────────


def _handler(name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(ast.parse(SELFHOST_SRC)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in selfhost.py")


def _func(name: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(SELFHOST_SRC)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in selfhost.py")


def _calls(nodes, attr: str | None = None, func_name: str | None = None):
    """Yield call nodes whose callee is ``X.attr`` or a bare ``func_name``."""
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        matched = (
            attr is not None
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == attr
        ) or (
            func_name is not None
            and isinstance(node.func, ast.Name)
            and node.func.id == func_name
        )
        if matched:
            yield node


def _walk_own_body(node: ast.AST):
    """The coroutine's OWN statements, not nested probe closures — a probe
    nested in the handler is exactly where synchronous I/O belongs."""
    for stmt in getattr(node, "body", []):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield from ast.walk(stmt)


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


# ── structural pins ────────────────────────────────────────────────────────


def test_ready_probe_lane_is_named_and_sized():
    """A per-call or default pool would reproduce the defect: per-call pools
    churn a thread per request (and cannot be bounded), and the default pool is
    the shared resource under attack. The readiness lane must name a pool and
    pin its width, and the name must be the exact string the thread assertions
    use."""
    assert _module_literal(READY_NAME_CONST) == READY_POOL_NAME, (
        f"{READY_NAME_CONST} is {_module_literal(READY_NAME_CONST)!r}, expected "
        f"{READY_POOL_NAME!r} — the behavioural tests assert probes run on that "
        "exact daemon-thread name, so a rename must not silently pass"
    )
    width = _prod_workers(READY_WIDTH_CONST)
    assert width >= READY_MIN_WORKERS, (
        f"{READY_WIDTH_CONST}={width} is below the required {READY_MIN_WORKERS} "
        "— a lane narrower than the shared default executor it replaced converts "
        "readiness fan-in into a queue-timeout false 503"
    )


def test_liveness_lane_constants_are_gone():
    """#2988: ``/health`` must hand off to NO pool. The ``#3287`` liveness lane
    (``_LIVENESS_PROBE_WORKER`` / ``_LIVENESS_PROBE_WORKERS``) is therefore
    dead and must be removed, not left as decoration — a retained pair would
    read as an active lane and invites reusing it."""
    tree = ast.parse(SELFHOST_SRC)
    assigned = {
        t.id
        for node in tree.body if isinstance(node, ast.Assign)
        for t in node.targets if isinstance(t, ast.Name)
    }
    leftovers = {n for n in assigned if "LIVENESS_PROBE" in n.upper()}
    assert not leftovers, (
        f"selfhost.py still defines {sorted(leftovers)} — the liveness lane was "
        "replaced by an in-memory coordinator, so a pool constant here is dead "
        "code that a later refactor will mistake for the live mechanism"
    )


def test_health_reads_the_coordinator_and_does_no_io():
    """THE structural core of #2988's first bullet.

    The response is identical whether the handler awaited a probe on a private
    pool or read a snapshot, so only the AST can tell them apart. Assert the
    in-memory read AND the absence of every I/O seam the old shapes used.
    """
    node = _handler("health")
    snapshots = list(_calls(_walk_own_body(node), attr="snapshot"))
    assert snapshots, (
        "selfhost health does not read _HEALTH_PROBE.snapshot() — it must answer "
        "from in-memory state, not from a probe (#2988)"
    )
    receivers = {
        call.func.value.id
        for call in snapshots
        if isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
    }
    assert receivers == {"_HEALTH_PROBE"}, (
        f"selfhost health reads {sorted(receivers) or 'nothing'} — the in-memory "
        "verdict must come from the shared HealthProbe coordinator"
    )
    forbidden = _names_in(node) & _LIVENESS_FORBIDDEN_NAMES
    assert not forbidden, (
        f"selfhost health references {sorted(forbidden)} — every one of those is "
        "request-path I/O or a pool hand-off, which is exactly what the in-memory "
        "read removes (#2988)"
    )
    offenders = [
        n.lineno
        for n in _walk_own_body(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in _LIVENESS_FORBIDDEN_ATTRS - {"snapshot"}
    ]
    assert not offenders, (
        f"selfhost health calls an I/O seam directly at line(s) {offenders} "
        "— synchronous work on the request path (#2988)"
    )


def test_lifespan_arms_and_cancels_the_liveness_refresher():
    """The in-memory verdict is only honest while SOMEBODY refreshes it (#1384):
    the lifespan must start ``_health_probe_loop`` and cancel it on shutdown (a
    leaked task survives TestClient reuse and writes into the next app's
    coordinator)."""
    node = _handler("_lifespan")
    body = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
    created = [
        call for call in body
        if isinstance(call.func, ast.Attribute)
        and call.func.attr == "create_task"
    ]
    assert any(
        (isinstance(arg, ast.Name) and arg.id == "_health_probe_loop")
        or (isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Name)
            and arg.func.id == "_health_probe_loop")
        for call in created for arg in call.args
    ), "selfhost _lifespan never creates the _health_probe_loop task"
    assert any(
        isinstance(call.func, ast.Attribute) and call.func.attr == "cancel"
        for call in body
    ), "selfhost _lifespan never cancels the refresher on shutdown"
    assert list(_calls(_walk_own_body(node), attr="reset")), (
        "selfhost _lifespan does not reset the coordinator, so a wedged probe "
        "from a previous app instance can survive into this one (#2850)"
    )


def test_probe_worker_resolves_to_the_shared_daemon_primitive():
    """#3286: the readiness lane is a ``monitoring.daemon_worker`` pool, and the
    reason is load-bearing, so pin the two properties the choice buys.

    A ``ThreadPoolExecutor`` would satisfy every other pin here while
    (a) joining its NON-daemon workers at interpreter exit — a probe parked in a
    socket read then delays ``docker stop``'s drain past the #2203 budget and
    blocks process exit — and (b) buffering submissions without bound, which is
    the very complaint this change makes about the default executor.
    """
    import tortoise.monitoring as mon
    from tortoise import selfhost as sh

    worker = sh._probe_worker(_module_literal(READY_NAME_CONST),
                              _prod_workers(READY_WIDTH_CONST))
    assert isinstance(worker, mon._SingleSlotWorker), (
        f"{READY_NAME_CONST} resolved to {type(worker).__name__}, not the "
        "_SingleSlotWorker daemon primitive — a ThreadPoolExecutor here "
        "reintroduces the non-daemon interpreter-exit join (#3286)"
    )
    # process-wide: the registry is keyed by name, so a second resolution must
    # return the SAME pool rather than leaking a new one per request.
    assert sh._probe_worker(_module_literal(READY_NAME_CONST), 1) is worker, (
        f"{READY_NAME_CONST} is not process-wide — resolving it twice built a "
        "second pool, so its width and backlog would not be the pinned ones"
    )
    assert worker.workers >= READY_MIN_WORKERS, (
        f"{READY_NAME_CONST} has {worker.workers} worker(s) — see READY_MIN_WORKERS"
    )
    assert worker._threads and all(t.daemon for t in worker._threads), (
        f"{READY_NAME_CONST} has non-daemon worker(s) — they are JOINED at "
        "interpreter exit, so a parked probe blocks shutdown (#3286)"
    )
    assert worker._max_backlog == mon._SingleSlotWorker.MAX_BACKLOG, (
        f"{READY_NAME_CONST}'s backlog is {worker._max_backlog!r}, not the primitive's "
        f"bounded {mon._SingleSlotWorker.MAX_BACKLOG} — an unbounded queue is "
        "the defect, not its fix"
    )


def test_probe_worker_starts_no_threads_at_import():
    """``daemon_worker`` starts its threads on CALL, so a module-level call would
    spawn them as a side effect of ``import tortoise.selfhost`` — including in
    every test and tool that only wants the app object."""
    module_calls = [
        node
        for node in ast.parse(SELFHOST_SRC).body
        if isinstance(node, (ast.Assign, ast.Expr))
        for node in ast.walk(node)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) == "daemon_worker"
             or getattr(node.func, "id", None) == "_probe_worker")
    ]
    assert not module_calls, (
        f"selfhost.py calls _probe_worker/daemon_worker at module level (line(s) "
        f"{[c.lineno for c in module_calls]}) — that starts the pools at import; "
        "resolve them lazily inside the handler instead"
    )
    assert "_probe_worker(" in SELFHOST_SRC, "the lazy resolver disappeared"


def test_ready_handler_owns_the_readiness_lane_only():
    """The readiness handler must dispatch exactly one probe, through ITS lane's
    constants — never hardcoded, never the (now removed) liveness lane's."""
    node = _handler("health_ready")
    submits = list(_calls(_walk_own_body(node), func_name="_submit_probe"))
    assert len(submits) == 1, "selfhost health_ready must dispatch exactly one probe"
    resolved = {
        call.args[0].id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and getattr(call.func, "id", None) == "_probe_worker"
        and call.args
        and isinstance(call.args[0], ast.Name)
    }
    assert resolved == {READY_NAME_CONST}, (
        f"selfhost health_ready resolves {sorted(resolved) if resolved else 'nothing'}, "
        f"expected {{{READY_NAME_CONST!r}}} — each handler must own its lane"
    )
    others = {
        n for n in _names_in(node)
        if "LIVENESS_PROBE" in n.upper()
    }
    assert not others, (
        f"selfhost health_ready references {sorted(others)} — the liveness lane is "
        "gone (#2988); readiness must resolve its own pool only"
    )


def test_submit_probe_targets_the_given_pool_only():
    """``_submit_probe`` is the single seam. It must submit to the pool it is
    GIVEN and never to the loop's default executor (``run_in_executor(None,
    ...)``) or through ``to_thread`` (which always uses the default pool), and
    it must propagate contextvars (a bare ``run_in_executor`` does not —
    cpython#78195 — and the SDK/projection layer reads them)."""
    node = _func("_submit_probe")
    params = [a.arg for a in node.args.args]
    assert params and params[0] == "pool", (
        f"_submit_probe must take the pool as its first parameter, got {params} — a "
        "hardcoded module pool reintroduces the sharing this split exists to prevent"
    )
    body = list(ast.walk(node))

    submits = list(_calls(body, attr="submit"))
    assert submits, "_submit_probe does not submit anything"
    for call in submits:
        assert isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name), (
            f"_submit_probe submits to a non-Name receiver at line {call.lineno}"
        )
        assert call.func.value.id == "pool", (
            f"_submit_probe submits to {call.func.value.id!r} at line {call.lineno} — it "
            "must submit to its pool argument"
        )

    assert not list(_calls(body, attr="to_thread")), (
        "_submit_probe uses asyncio.to_thread — that ALWAYS targets the loop's "
        "shared default executor, which is the defect (#3035/#3287)"
    )
    for call in _calls(body, attr="run_in_executor"):
        first = call.args[0] if call.args else None
        assert not (isinstance(first, ast.Constant) and first.value is None), (
            f"run_in_executor(None, ...) at line {call.lineno} uses the DEFAULT executor"
        )
    assert any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "copy_context"
        for call in body
        if isinstance(call, ast.Call)
    ), (
        "_submit_probe does not copy the caller's contextvars — a bare thread-pool "
        "submit loses them (cpython#78195), and the SDK/projection layer reads them"
    )


def test_ready_handler_does_not_touch_db_code_on_the_loop():
    """/health/ready is the 60 s publish gate's hard check; its probe must stay
    off the loop and out of the request-path body."""
    node = _handler("health_ready")
    offenders = [
        n.lineno
        for n in _walk_own_body(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"_get_proj", "query"}
    ]
    assert not offenders, (
        f"selfhost health_ready touches DB/projection code directly at line(s) "
        f"{offenders} — synchronous I/O on the event loop (#2988)"
    )
    assert not list(_calls(ast.walk(node), attr="to_thread")), (
        "selfhost health_ready uses asyncio.to_thread — shared default executor (#3035)"
    )


def test_ready_handler_keeps_its_bound_and_constant():
    """The dedicated pool must not cost us the #2988 safeguard: a genuinely
    black-holed DB is still REPORTED (503), not waited out."""
    node = _handler("health_ready")
    waits = list(_calls(ast.walk(node), attr="wait_for"))
    assert waits, "selfhost health_ready's probe is unbounded (#2988)"
    bounds = [
        kw
        for call in waits
        for kw in call.keywords
        if kw.arg == "timeout" and isinstance(kw.value, ast.Name)
    ]
    assert bounds and all(kw.value.id == "_READY_PROBE_TIMEOUT_S" for kw in bounds), (
        "the bound must be the module constant, so it can be reasoned about in one place"
    )
    assert "_READY_PROBE_TIMEOUT_S" in SELFHOST_SRC


def test_liveness_probe_carries_the_cold_start_allowance():
    """#3243's wiring: the refresher's probe must pass the #3143/#3217 allowance
    to ``monitoring.probe_db``. Without it the shared 1.5 s budget is spent in
    the projection cold start and a reachable large graph reads degraded."""
    node = _func("_probe_db")
    calls = list(_calls(ast.walk(node), func_name="probe_db"))
    assert calls, "selfhost _probe_db never calls monitoring.probe_db"
    allowances = [kw for call in calls for kw in call.keywords if kw.arg == "setup_timeout"]
    assert allowances, (
        "selfhost _probe_db calls probe_db WITHOUT setup_timeout — the liveness "
        "verdict then shares the 1.5s budget across both phases and false-degrades "
        "a reachable cold graph (#3243)"
    )
    for kw in allowances:
        assert (
            isinstance(kw.value, ast.Call)
            and getattr(kw.value.func, "id", None) == "probe_setup_timeout"
        ), (
            f"the allowance at line {kw.value.lineno} is not the resolved "
            "monitoring.probe_setup_timeout() — a literal here would ignore "
            "TORTOISE_PROBE_SETUP_TIMEOUT (#3143)"
        )


def test_liveness_coordinator_bound_sits_above_its_probe_total():
    """Layered-timeout discipline: the coordinator's ``timeout`` must exceed the
    explicit-allowance shape's real total (``setup_timeout + PROBE_TIMEOUT`` plus
    the SDK-acquisition budget the bound is charged for), or the refresher
    returns before the verdict it is waiting for and strands a worker on every
    cold start."""
    import tortoise.monitoring as mon
    from tortoise import selfhost as sh

    total = (mon.probe_setup_timeout() + mon.PROBE_TIMEOUT
             + mon.PROBE_SDK_ACQUISITION_BUDGET)
    assert sh._HEALTH_PROBE._timeout > total, (
        f"_HEALTH_PROBE timeout {sh._HEALTH_PROBE._timeout}s is not above the "
        f"allowance shape's total {total}s"
    )


def test_liveness_coordinator_window_follows_a_raised_allowance(monkeypatch):
    """The freshness window must FOLLOW the cold-start allowance, not sit at a
    fixed ``PROBE_STALE_AFTER``.

    ``snapshot()`` discards a result older than ``stale_after``, so a window
    frozen at the 30 s platform default discards the verdict of a REACHABLE
    cold start once ``TORTOISE_PROBE_SETUP_TIMEOUT`` pushes ``probe_duration``
    past 30 s — the #3243 false degrade, in steady state. Raise the allowance
    (the resolver reads it at CALL time) and pin that the window moved with it.

    This pins the RESOLVER. That the coordinator is BUILT from it is pinned
    structurally by
    ``test_liveness_coordinator_is_built_from_the_dynamic_window_and_bound``
    — the two are a pair, because a fixed ``stale_after=`` at the construction
    site leaves this test green while re-opening #3243.
    """
    import tortoise.monitoring as mon
    from tortoise import selfhost as sh

    assert sh._liveness_probe_stale_after() >= mon.PROBE_STALE_AFTER, (
        "the window is below the platform default it must never undercut"
    )

    monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", "300")
    raised = sh._liveness_probe_stale_after()
    assert raised > mon.PROBE_STALE_AFTER, (
        f"the window stayed at {raised}s under a 300s cold-start allowance — a "
        "reachable graph whose cold start exceeds the platform default would "
        "be discarded as stale between refreshes (#3243)"
    )
    assert raised >= sh._liveness_probe_hard_timeout(), (
        f"the window {raised}s no longer covers the coordinator's own bound "
        f"{sh._liveness_probe_hard_timeout()}s at that allowance (#3243)"
    )


def test_liveness_coordinator_is_built_from_the_dynamic_window_and_bound():
    """The resolvers' return values are not the contract — the COORDINATOR
    must be built from them.

    ``stale_after=_liveness_probe_stale_after()`` is what makes the freshness
    window follow a raised cold-start allowance; a literal (or any fixed bound)
    at the construction site re-opens the #3243 steady-state degrade while the
    resolver test above stays green. The bound is pinned in the same call for
    the same reason (``_liveness_probe_hard_timeout`` is what
    ``_liveness_probe_stale_after`` is taken from, so a frozen bound would
    silently freeze the window too).
    """
    call = _module_assign("_HEALTH_PROBE").value
    assert isinstance(call, ast.Call), (
        f"_HEALTH_PROBE is not a plain HealthProbe(...) call — the pins cannot "
        f"read it: {ast.dump(call)[:120]}"
    )
    assert getattr(call.func, "id", None) == "HealthProbe", (
        f"_HEALTH_PROBE is not a HealthProbe(...) call: "
        f"{ast.dump(call.func)[:80]}"
    )
    kwargs = {kw.arg: kw.value for kw in call.keywords}
    for kwarg, resolver in (("stale_after", "_liveness_probe_stale_after"),
                            ("timeout", "_liveness_probe_hard_timeout")):
        value = kwargs.get(kwarg)
        assert (isinstance(value, ast.Call)
                and getattr(value.func, "id", None) == resolver), (
            f"HealthProbe({kwarg}=...) is not {resolver}() — a fixed bound "
            "there re-opens the #3243 steady-state degrade while the resolver "
            "test stays green"
        )


def test_health_probe_loop_does_not_compound_the_interval():
    """#3243 review: the refresh is a FIXED CADENCE, not
    ``probe_duration + interval``. The loop must subtract its own run time from
    the sleep, or a slow (cold) probe pushes the cycle past ``PROBE_STALE_AFTER``
    and a reachable graph reads degraded between refreshes."""
    node = _handler("_health_probe_loop")
    sleeps = list(_calls(_walk_own_body(node), attr="sleep"))
    assert sleeps, "_health_probe_loop never sleeps"
    max_calls = [
        arg for call in sleeps for arg in call.args
        if isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Name)
        and arg.func.id == "max"
    ]
    assert max_calls, (
        "_health_probe_loop sleeps the raw interval — the cycle becomes "
        "probe_duration + interval, which can exceed PROBE_STALE_AFTER (#3243)"
    )
    # The clamped expression must subtract the run's elapsed time from the
    # interval: `max(0.0, health_probe_interval() - (loop.time() - started))`.
    subtracted = [
        a for arg in max_calls for a in arg.args
        if isinstance(a, ast.BinOp) and isinstance(a.op, ast.Sub)
        and any(
            isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "health_probe_interval"
            for n in ast.walk(a)
        )
    ]
    assert subtracted, (
        "the sleep is not `interval - elapsed` — the interval is not "
        "compensated for the probe's own duration (#3243)"
    )
    assert any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "time"
        for n in ast.walk(node)
    ), "_health_probe_loop does not measure the run's elapsed time"


# ── behavioural harness ────────────────────────────────────────────────────


class _StubSDK:
    """Stands in for ``tortoise.sdk.TortoiseSDK`` so no test ever starts a real
    (embedded) DB.

    The handlers import ``TortoiseSDK`` lazily inside the probe closure, so
    patching the attribute on ``tortoise.sdk`` is the same seam production
    uses. Without this the probes build a real SDK, which spins up
    FalkorDBLite — slow, environment-dependent, and irrelevant to the property
    under test (the pool the probe is dispatched to).
    """

    def __init__(self, *args, **kwargs) -> None:
        pass

    def _get_proj(self) -> None:  # overridden per test
        return None


def _ok_probe(sdk=None, setup_timeout=None):
    return {"ok": True, "latency_ms": 0.1, "error": None}


@pytest.fixture
def selfhost(monkeypatch, tmp_path):
    """The selfhost module with a stubbed SDK/probe.

    The readiness pool is NOT replaced: the probes run on the REAL
    process-wide ``monitoring.daemon_worker`` pool production resolves, so the
    thread-name and width assertions validate production's resource rather than
    a fixture-local stand-in. (Replacing it would let a mis-named or too-narrow
    production pool pass — the failure mode round-1 review found in this suite.)
    The workers are daemons parked on a queue, so they cost the process nothing
    at exit and need no teardown.
    """
    import importlib

    monkeypatch.setenv("TORTOISE_HOST", "127.0.0.1")
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "selfhost.db"))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_PROBE_SETUP_TIMEOUT", raising=False)

    import tortoise.monitoring as mon
    import tortoise.sdk as sdk_mod
    from tortoise import selfhost as sh

    importlib.reload(sh)

    monkeypatch.setattr(sdk_mod, "TortoiseSDK", _StubSDK)
    monkeypatch.setattr(mon, "probe_db", _ok_probe)
    yield sh


class _Saturated:
    """Saturate the event loop's DEFAULT executor to its last worker.

    ``asyncio.to_thread``/``run_in_executor(None, ...)`` both submit here. A
    pre-fix probe queues behind ``gate`` and the request hangs; an in-memory
    read is unaffected. Deterministic by construction — no timing assumption
    about how long the pool stays busy.
    """

    def __init__(self, workers: int = 1):
        self.workers = workers
        self.gate = threading.Event()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        self._hogs: list[asyncio.Future] = []

    async def __aenter__(self) -> _Saturated:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(self.pool)
        self._hogs = [loop.run_in_executor(None, self.gate.wait, 30) for _ in range(self.workers)]
        await asyncio.sleep(0.2)  # let the hogs take every worker
        return self

    async def __aexit__(self, *exc) -> None:
        self.gate.set()
        for hog in self._hogs:
            hog.cancel()
        await asyncio.sleep(0)
        self.pool.shutdown(wait=False)


def _client(selfhost_module):
    import httpx

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=selfhost_module.app),
        base_url="http://selfhost.test",
    )


def _seed(selfhost, monkeypatch, *, ok=True, error=None):
    """Resolve the coordinator once, the way the background refresher does, so
    the request-path read has a real in-memory verdict to serve."""
    import tortoise.monitoring as mon

    if ok:
        def _probe(sdk=None, setup_timeout=None):
            return {"ok": True, "latency_ms": 0.1, "error": None}
    else:
        def _probe(sdk=None, setup_timeout=None):
            raise ConnectionError(error or "NXDOMAIN")

    monkeypatch.setattr(mon, "probe_db", _probe)
    selfhost._HEALTH_PROBE.reset()
    return selfhost._HEALTH_PROBE.wait(10.0)


# ── behavioural proof ──────────────────────────────────────────────────────


def test_health_answers_instantly_from_memory_with_a_saturated_default_executor(
    selfhost, monkeypatch
):
    """THE acceptance test for #2988's first bullet (mirrors the hosted
    ``TestLivenessDecouple::test_health_returns_instantly_with_a_saturated_shared_executor``).

    A fast answer alone proves nothing — an endpoint that skips its probe would
    also be fast, and so would one that merely owns a private pool. So this
    asserts BOTH: the answer stays under the 500 ms acceptance bound AND the
    probe count does not move, i.e. the read really is in-memory.
    """
    import tortoise.monitoring as mon

    seen: list[str] = []

    def _probe(sdk=None, setup_timeout=None):
        seen.append(threading.current_thread().name)
        return {"ok": True, "latency_ms": 0.1, "error": None}

    monkeypatch.setattr(mon, "probe_db", _probe)
    # Seed the in-memory verdict the way the background refresher does.
    selfhost._HEALTH_PROBE.reset()
    seeded = selfhost._HEALTH_PROBE.wait(10.0)
    assert seeded["ok"] is True, seeded
    probes_after_seeding = len(seen)

    async def scenario():
        async with _Saturated(), _client(selfhost) as ac:
            await ac.get("/health")  # warm-up: pay routing/middleware init unmeasured
            warm = len(seen)
            started = time.perf_counter()
            r = await asyncio.wait_for(ac.get("/health"), timeout=3.0)
            return r, time.perf_counter() - started, warm, len(seen)

    r, elapsed, warm, after = asyncio.run(scenario())

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert elapsed < 0.5, (
        f"/health took {elapsed:.3f}s with the default executor saturated — the "
        "handler is still on the request path or still handing off to a worker, "
        "not reading in-memory state (#2988)"
    )
    assert warm == probes_after_seeding and after == probes_after_seeding, (
        f"the request path ran the DB probe ({probes_after_seeding} -> {warm} -> "
        f"{after} calls) — an in-memory read must not probe at all (#2988)"
    )


def test_refresher_keeps_the_verdict_fresh_without_a_request(selfhost, monkeypatch):
    """The in-memory verdict is only honest while the refresher runs — if it
    dies or stops, the DB verdict freezes (#1384). Run it for a couple of short
    cycles and prove it lands a fresh verdict with no request at all."""
    import tortoise.monitoring as mon

    calls = {"n": 0}

    def _probe(sdk=None, setup_timeout=None):
        calls["n"] += 1
        return {"ok": True, "latency_ms": 0.5, "error": None}

    monkeypatch.setattr(mon, "probe_db", _probe)
    monkeypatch.setattr(selfhost, "health_probe_interval", lambda: 0.05)
    selfhost._HEALTH_PROBE.reset()

    async def _run():
        task = asyncio.get_running_loop().create_task(selfhost._health_probe_loop())
        await asyncio.sleep(0.4)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run())
    assert calls["n"] >= 1, "the refresher never probed"
    view = selfhost._HEALTH_PROBE.snapshot()
    assert view["ok"] is True, view


def test_probe_sdk_is_reused_across_refreshes(selfhost, monkeypatch):
    """#3243 review: the probe must NOT rebuild the SDK every cycle.

    A fresh ``TortoiseSDK`` per refresh re-pays the projection cold start every
    cycle, which both holds the process-wide single probe-worker slot for the
    cold-start duration every interval (#3683) and can push the cycle past
    ``PROBE_STALE_AFTER`` so a REACHABLE graph reads degraded between refreshes
    — the #3243 lie in steady state. One cached connection, rebuilt only when
    the DB target changes (hosted ``_probe_sdk`` pattern).
    """
    first = selfhost._probe_sdk()
    assert selfhost._probe_sdk() is first, (
        "_probe_sdk() built a second connection — the refresher would re-pay the "
        "projection cold start on every cycle (#3243)"
    )
    monkeypatch.setenv("TORTOISE_DB_PATH", str(selfhost.__file__) + "-changed")
    assert selfhost._probe_sdk() is not first, (
        "a changed DB target did not rebuild the cached probe SDK — the probe "
        "would answer for a stale DB"
    )


def test_endpoint_degrades_within_one_refresh_after_a_healthy_verdict(
        selfhost, monkeypatch):
    """Replaces the deleted ``test_health_liveness_passes_no_setup_allowance``
    guard AT THE ENDPOINT LEVEL (#1384).

    The in-memory read moves /health's degrade latency from the probe's bound to
    the REFRESH PERIOD: after a healthy verdict, a DB that dies must flip
    /health to ``degraded`` on the next refresh, not wait out
    ``PROBE_STALE_AFTER``. A stopped FalkorDB fails fast, so the flip is one
    interval.
    """
    import tortoise.monitoring as mon

    state = {"ok": True}

    def _probe(sdk=None, setup_timeout=None):
        if state["ok"]:
            return {"ok": True, "latency_ms": 0.1, "error": None}
        raise ConnectionError("NXDOMAIN")

    monkeypatch.setattr(mon, "probe_db", _probe)
    monkeypatch.setattr(selfhost, "health_probe_interval", lambda: 0.05)
    selfhost._HEALTH_PROBE.reset()
    assert selfhost._HEALTH_PROBE.wait(5.0)["ok"] is True

    state["ok"] = False

    async def _run():
        task = asyncio.get_running_loop().create_task(selfhost._health_probe_loop())
        await asyncio.sleep(0.3)  # several refresh periods
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run())
    view = selfhost._HEALTH_PROBE.snapshot()
    assert view["ok"] is False, (
        f"/health still reports the graph healthy {view} after the DB died — "
        "the verdict is not refreshed, so a dead DB would serve a fossil 'ok' "
        "(#1384)"
    )


def test_health_reflects_a_dead_db_from_memory(selfhost, monkeypatch):
    """#1384's contract, unchanged by the in-memory read: a dead DB degrades
    /health (200 + db.ok=false) and never 5xxes the process. The verdict comes
    from the coordinator's snapshot, not from a per-request probe."""
    seed = _seed(selfhost, monkeypatch, ok=False, error="NXDOMAIN")
    assert seed["ok"] is False, seed

    async def scenario():
        async with _client(selfhost) as ac:
            return await ac.get("/health")

    r = asyncio.run(scenario())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "degraded"
    assert body["db"]["ok"] is False
    assert "NXDOMAIN" in body["db"]["error"]


def test_health_reports_an_honest_not_yet_state_before_the_first_probe(selfhost, monkeypatch):
    """A fresh process has no verdict yet. It must report degraded-with-a-reason
    (not a 500, and not a fabricated "ok") until the refresher lands one.

    The probe is held in flight deliberately: with an instant stub the probe can
    complete before the read path takes its view, which would make this assertion
    a coin flip rather than a pin.
    """
    import tortoise.monitoring as mon

    release = threading.Event()

    def _blocking(sdk=None, setup_timeout=None):
        release.wait(10)
        return {"ok": True, "latency_ms": 0.1, "error": None}

    monkeypatch.setattr(mon, "probe_db", _blocking)
    selfhost._HEALTH_PROBE.reset()
    try:
        async def scenario():
            async with _client(selfhost) as ac:
                return await ac.get("/health")

        r = asyncio.run(scenario())
    finally:
        release.set()

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "degraded"
    assert body["db"]["ok"] is False
    assert "in flight" in body["db"]["error"] or "not produced" in body["db"]["error"], (
        f"the not-yet verdict must carry an honest reason, got {body['db']}"
    )


def test_liveness_probe_passes_the_resolved_cold_start_allowance(selfhost, monkeypatch):
    """#3243's behavioural half: the probe the coordinator runs carries the
    #3143/#3217 allowance, resolved at CALL time (so ``.env``/env changes are
    honoured) rather than the shared 1.5 s budget."""
    import tortoise.monitoring as mon

    seen: dict = {}

    def _probe(sdk=None, setup_timeout=None):
        seen["setup_timeout"] = setup_timeout
        return {"ok": True, "latency_ms": 0.1, "error": None}

    monkeypatch.setattr(mon, "probe_db", _probe)
    monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", "42")
    selfhost._HEALTH_PROBE.reset()
    view = selfhost._HEALTH_PROBE.wait(10.0)

    assert view["ok"] is True, view
    assert seen["setup_timeout"] == 42.0, (
        f"the liveness probe passed setup_timeout={seen.get('setup_timeout')!r}, not "
        "the resolved TORTOISE_PROBE_SETUP_TIMEOUT — a reachable cold graph would "
        "be false-degraded (#3243)"
    )


def test_cold_start_allowance_rescues_a_reachable_large_graph(monkeypatch):
    """THE #3243 regression, at the mechanism level (no selfhost module needed).

    A reachable graph whose projection cold-start exceeds ``PROBE_TIMEOUT``: the
    shared-budget shape — what the pre-fix liveness path and the raw
    ``/health`` contract used — reports ``ok=False`` (the reported lie), while
    the explicit cold-start allowance reports it reachable. Both phases stay
    bounded, so the allowance is a ceiling, not an unbounded wait.
    """
    import tortoise.monitoring as mon

    monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", "20")
    setup_s = mon.PROBE_TIMEOUT + 0.4  # reachable, but slower than the shared budget

    class _OkGraph:
        def query(self, _q):
            return [[1]]

    class _OkProj:
        def __init__(self):
            self.g = _OkGraph()

    class _ColdSDK:
        """Reachable graph whose *cold start* is slow (the O(graph) index build)."""

        def _get_proj(self):
            time.sleep(setup_s)
            return _OkProj()

    shared = mon.probe_db(_ColdSDK(), setup_timeout=None)
    assert shared["ok"] is False, (
        f"the shared-budget shape unexpectedly passed ({shared}) — the test's "
        "setup delay no longer exceeds PROBE_TIMEOUT, so it cannot distinguish "
        "the bug from the fix"
    )

    allowed = mon.probe_db(_ColdSDK(), setup_timeout=mon.probe_setup_timeout())
    assert allowed["ok"] is True, (
        f"a reachable graph with a {setup_s:.1f}s cold start was reported "
        f"unreachable under the cold-start allowance (#3243): {allowed}"
    )


def test_health_reports_ok_for_a_reachable_graph_whose_cold_start_exceeds_probe_timeout(
        selfhost, monkeypatch):
    """#3243 END-TO-END through the real HTTP surface and the real probe.

    A REACHABLE graph whose projection cold-start overruns the shared
    ``PROBE_TIMEOUT`` (1.5s) must read ``status: ok`` / ``db.ok: true`` from
    ``/health``. This drives the genuine ``monitoring.probe_db`` budget
    arithmetic through the coordinator, so it fails if the refresher stops
    passing the cold-start allowance (the pre-fix verdict) — the wiring-level
    test above only records the ``setup_timeout`` value handed in, and cannot
    see the resulting verdict.
    """
    import tortoise.monitoring as mon

    cold_start = mon.PROBE_TIMEOUT + 0.5  # reachable, but slower than the shared budget
    monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", str(cold_start + 5.0))
    # Restore the REAL probe so the cold-start budget decision is exercised.
    monkeypatch.setattr(mon, "probe_db", _REAL_PROBE_DB)

    class _Graph:
        def query(self, _q):
            return [[1]]

    class _Proj:
        def __init__(self):
            self.g = _Graph()

    def _slow_cold_start(self):
        time.sleep(cold_start)
        return _Proj()

    monkeypatch.setattr(_StubSDK, "_get_proj", _slow_cold_start)

    selfhost._HEALTH_PROBE.reset()
    view = selfhost._HEALTH_PROBE.wait(30.0)
    assert view["ok"] is True, (
        f"the liveness coordinator reported a reachable, cold-starting graph as "
        f"unreachable — db.ok=false / degraded is the #3243 lie: {view}"
    )

    async def scenario():
        async with _client(selfhost) as ac:
            return await ac.get("/health")

    r = asyncio.run(scenario())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok", body
    assert body["db"]["ok"] is True, body["db"]


def test_unreachable_graph_still_fails_fast_under_the_cold_start_allowance(monkeypatch):
    """#3243's other half: the allowance is a CEILING, not a delay.

    A genuinely unreachable graph (refused / NXDOMAIN) must still be reported
    degraded inside the #1384 fast-degrade window even when the allowance is
    set to 20s. Otherwise giving the cold-start phase its own allowance would
    have traded the false degrade for a slow gate — the trade #3243's own
    notes forbid.
    """
    import tortoise.monitoring as mon

    monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", "20")

    class _RefusedSDK:
        def _get_proj(self):
            raise ConnectionError("NXDOMAIN / connection refused")

    started = time.monotonic()
    result = mon.probe_db(_RefusedSDK(), setup_timeout=mon.probe_setup_timeout())
    elapsed = time.monotonic() - started

    assert result["ok"] is False, result
    assert "refused" in (result["error"] or "") or "NXDOMAIN" in (result["error"] or ""), result
    assert elapsed < mon.PROBE_TIMEOUT, (
        f"an unreachable graph took {elapsed:.2f}s under a 20s cold-start "
        f"allowance — the allowance DELAYED the #1384 fast degrade instead of "
        "bounding a reachable cold start (#3243)"
    )


def test_ready_does_not_lie_while_the_default_pool_is_saturated(selfhost, monkeypatch):
    """The regression that actually blocks releases: a starved readiness probe
    returned 503 for a HEALTHY database (measured 6021 ms, probe never ran),
    and publish-selfhost.yml fails the publish on a non-200 /health/ready."""
    seen: list[str] = []
    monkeypatch.setattr(
        _StubSDK, "_get_proj", lambda self: seen.append(threading.current_thread().name)
    )

    async def scenario():
        async with _Saturated(), _client(selfhost) as ac:
            await ac.get("/health/ready")  # warm-up: pay startup cost unmeasured
            started = time.perf_counter()
            r = await asyncio.wait_for(ac.get("/health/ready"), timeout=8.0)
            return r, time.perf_counter() - started

    r, elapsed = asyncio.run(scenario())

    assert r.status_code == 200, (
        f"/health/ready answered {r.status_code} ({r.text}) while the DB was "
        "healthy and only an UNRELATED task held the default executor — a "
        "starved probe is a false not-ready that fails the publish (#3287)"
    )
    assert r.json()["status"] == "ready"
    assert len(seen) == 2, "the readiness probe did not run once per request"
    assert all(t.startswith("selfhost-ready-probe") for t in seen), (
        f"readiness probes ran on threads {seen}, not the dedicated readiness lane"
    )
    assert elapsed < 8.0, f"/health/ready took {elapsed:.2f}s while starved (#3287)"


def test_submit_probe_propagates_contextvars(selfhost, monkeypatch):
    """The dispatch seam must carry the CALLER's contextvars into the worker.

    ``asyncio.to_thread`` did (that is why #2988's move off-loop did not break
    the SDK/projection layer), and a bare ``run_in_executor(pool, fn)`` does not
    (cpython#78195). Losing them is silent — the probe answers from the wrong
    tenant/graph scope — so pin the property behaviourally on the readiness
    lane (the remaining request-path probe; /health no longer dispatches at all).
    """
    probe_var = contextvars.ContextVar("probe-context-var", default="unset")
    seen: list[str] = []
    monkeypatch.setattr(_StubSDK, "_get_proj", lambda self: seen.append(probe_var.get()))

    async def scenario():
        async with _client(selfhost) as ac:
            probe_var.set("caller-scope")
            return await ac.get("/health/ready")

    r = asyncio.run(scenario())
    assert r.status_code == 200, r.text
    assert seen == ["caller-scope"], (
        f"the probe saw {seen!r} instead of the caller's ContextVar value — the "
        "dispatch seam is dropping contextvars (cpython#78195)"
    )


def test_readiness_fan_in_does_not_produce_a_false_503(selfhost, monkeypatch):
    """The readiness pool must not be NARROWER than the executor it replaced.

    ``/health/ready``'s worker parks for the whole of ``_get_proj()`` (no inner
    bound — see the module comment in selfhost.py), and the OUTER
    ``_READY_PROBE_TIMEOUT_S`` cancels the await WITHOUT freeing it. So a pool of
    width W serves only W concurrent probes: request W+1 waits for a worker,
    burns its entire bound queueing, and is answered 503 for a HEALTHY database —
    exactly the false not-ready this change removes, re-entered through readiness
    fan-in rather than through unrelated load. The pre-fix shared executor was
    ``min(32, cpu+4)`` (6 on the 2-vCPU hosted box); a 2-worker pool silently
    gives 4 of those back.

    Arithmetic: probe 0.5 s held, bound 1.2 s, SIX concurrent requests. At width
    >= 6 every probe starts at t=0 and finishes at 0.5 s < 1.2 s. At width 2 the
    last two start at t=1.0 s and cannot answer before 1.5 s, past the bound.
    Margins are ~2.4x on both sides, so this is not a knife-edge race.
    """
    held = 0.5
    bound = 1.2
    concurrent_requests = 6
    width = _prod_workers(READY_WIDTH_CONST)
    monkeypatch.setattr(selfhost, "_READY_PROBE_TIMEOUT_S", bound)
    monkeypatch.setattr(_StubSDK, "_get_proj", lambda self: time.sleep(held))

    async def scenario():
        async with _client(selfhost) as ac:
            # Warm-up: the first request in a process pays a one-time startup
            # cost, which would otherwise be charged to the six measured
            # requests and could time them out for reasons unrelated to width.
            await ac.get("/health/ready")
            results = await asyncio.gather(
                *(ac.get("/health/ready") for _ in range(concurrent_requests))
            )
            return results

    results = asyncio.run(scenario())
    codes = [r.status_code for r in results]

    assert codes == [200] * concurrent_requests, (
        f"{codes.count(503)} of {concurrent_requests} concurrent /health/ready requests "
        f"returned 503 for a HEALTHY database (probe held {held}s, bound {bound}s, "
        f"{READY_WIDTH_CONST}={width}) — the lane is too narrow: queued requests time "
        "out before a worker ever runs their probe, and publish-selfhost.yml fails the "
        "release on that false 503"
    )


def test_liveness_answers_while_readiness_workers_are_parked(selfhost, monkeypatch):
    """/health must be structurally immune to a parked readiness worker.

    ``/health/ready``'s probe is ``sdk._get_proj()`` with NO internal bound, so
    concurrent readiness probes park every worker of the readiness lane. Under
    the pre-#3287 single-pool shape that starved liveness; under #2988 liveness
    does not touch a pool at all — it reads the coordinator's snapshot — so the
    immunity is now by construction, and this test proves the observable part:
    /health answers promptly while readiness is wedged.
    """
    release = threading.Event()
    monkeypatch.setattr(selfhost, "_READY_PROBE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(_StubSDK, "_get_proj", lambda self: release.wait(30))

    async def scenario():
        async with _Saturated(), _client(selfhost) as ac:
            # Park readiness workers: both of these time out at 0.3s while their
            # workers stay blocked on release.
            parked = await asyncio.gather(
                ac.get("/health/ready"), ac.get("/health/ready")
            )
            started = time.perf_counter()
            live = await asyncio.wait_for(ac.get("/health"), timeout=3.0)
            return parked, live, time.perf_counter() - started

    try:
        parked, live, elapsed = asyncio.run(scenario())
    finally:
        release.set()

    for r in parked:
        assert r.status_code == 503, r.text
    assert live.status_code == 200, (
        f"/health answered {live.status_code} while two readiness workers were "
        "parked — an unbounded readiness probe can starve liveness (#3287)"
    )
    assert live.json()["status"] in {"ok", "degraded"}, live.text
    assert elapsed < 3.0, (
        f"/health took {elapsed:.2f}s behind parked readiness workers — liveness "
        "is queueing behind readiness (#3287)"
    )


def test_readiness_lane_actually_has_two_usable_workers(selfhost, monkeypatch):
    """At least two readiness workers must be usable concurrently (the lane is
    wider — see ``READY_MIN_WORKERS``). A single-slot lane would serialise the
    deploy gate's probe behind any other readiness poll."""
    entered = threading.Event()
    release = threading.Event()
    threads: list[str] = []

    def _slow_get_proj(self):
        threads.append(threading.current_thread().name)
        entered.set()
        release.wait(10)

    monkeypatch.setattr(_StubSDK, "_get_proj", _slow_get_proj)

    async def scenario():
        async with _client(selfhost) as ac:
            first = asyncio.create_task(ac.get("/health/ready"))
            ready_deadline = time.perf_counter() + 3.0
            while not entered.is_set():
                if time.perf_counter() > ready_deadline:
                    return None, None
                await asyncio.sleep(0.01)
            # One worker is parked; a second must be reachable.
            second = asyncio.create_task(ac.get("/health/ready"))
            second_deadline = time.perf_counter() + 3.0
            both = False
            while time.perf_counter() < second_deadline:
                if len(threads) >= 2:
                    both = True
                    break
                await asyncio.sleep(0.01)
            release.set()
            return both, await asyncio.gather(first, second)

    try:
        both, responses = asyncio.run(scenario())
    finally:
        release.set()

    assert both, (
        "a second readiness probe never reached a second worker — the lane is "
        f"single-slot (threads seen: {threads})"
    )
    for r in responses:
        assert r.status_code == 200 and r.json()["status"] == "ready", r.text
    assert len({t for t in threads}) == 2, f"expected two distinct workers, saw {threads}"


def test_hung_db_still_fails_closed_within_the_bound(selfhost, monkeypatch):
    """#2988's guarantee must survive the new dispatch: a black-holed DB is
    REPORTED (503) within the bound, never waited out.

    Asserted SEMANTICALLY rather than on a tight wall-clock margin: the answer
    must arrive while the probe is still parked. A clock margin is not a safe
    proxy here — this test can run first in a process, where one-time startup
    costs a large multiple of the 0.2 s bound (measured 1.81 s) with no bearing
    on the bound, so a tight ceiling fails a correct implementation.
    """
    dispatched = []
    release = threading.Event()
    monkeypatch.setattr(selfhost, "_READY_PROBE_TIMEOUT_S", 0.2)

    def _hang(self):
        dispatched.append(threading.current_thread().name)
        release.wait(30)

    monkeypatch.setattr(_StubSDK, "_get_proj", _hang)

    async def scenario():
        async with _client(selfhost) as ac:
            await ac.get("/health/ready")  # warm-up: pay startup cost unmeasured
            started = time.perf_counter()
            try:
                r = await ac.get("/health/ready")
                # TWO probes dispatched (warm-up + measured) and the hang still
                # in force => the measured request answered without its probe
                # having completed.
                answered_while_hung = len(dispatched) == 2 and not release.is_set()
            finally:
                release.set()
            return r, time.perf_counter() - started, answered_while_hung

    r, elapsed, answered_while_hung = asyncio.run(scenario())

    assert r.status_code == 503, r.text
    assert r.json()["status"] == "not_ready"
    assert len(dispatched) == 2, (
        f"the readiness probe was dispatched {len(dispatched)} time(s), expected once per "
        "request — a handler that skips the probe must not be able to pass this test"
    )
    assert answered_while_hung, (
        "the endpoint did not answer until the probe finished: the 0.2 s bound was "
        "not applied, so a black-holed DB is waited out (#2988)"
    )
    assert elapsed < 8.0, (
        f"the endpoint waited {elapsed:.2f}s for a hung probe — the answer must arrive "
        "promptly, not after the 30 s hang"
    )
