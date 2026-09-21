"""#3287 — the selfhost health probes must not share a starvable executor.

#2988 (PR #3009) moved the probes OFF the event loop. It left them on the
loop's **shared** default ``ThreadPoolExecutor``, whose queue is UNBOUNDED: a
submission never fails, it just WAITS. So a probe does not have to hang to be
slow, it only has to queue behind unrelated ``to_thread`` work in the process.
``asyncio.wait_for`` bounds the ANSWER, not the TRUTH — a starved readiness
probe reports not_ready while the DB is fine.

Measured on ``main`` with both probes stubbed to ~0 ms and one unrelated task
occupying the default executor's only worker:

    /health        : NO ANSWER within 8.0s        (probe never ran)
    /health/ready  : HTTP 503 after 6021ms        (probe never ran)

``publish-selfhost.yml`` curls ``/health/ready`` and fails the publish on a
non-200, so that false 503 blocks a release of a perfectly healthy image;
``/health`` is the 60 s boot-wait in the same workflow.

**The pool is ``monitoring.daemon_worker`` (#3498's bounded multi-worker
form)** — the shared, reviewed primitive #3286 records as the unification
target, not a second bespoke executor. Two properties come from that choice and
are pinned here directly, because they are the reason for it:

* the workers are DAEMON, so a probe parked in a socket read cannot delay
  interpreter exit (``concurrent.futures.thread._python_exit`` JOINS a
  ``ThreadPoolExecutor``'s non-daemon workers; #2203's ``docker stop`` SIGKILLs
  10 s after SIGTERM and a wedged non-daemon worker eats that budget);
* the backlog is BOUNDED (``_SingleSlotWorker.MAX_BACKLOG``), so a saturated
  pool REFUSES a submission instead of buffering without bound — this change's
  own complaint about the default executor applied to its replacement.

The suite is deliberately two-layered, in the style of
``test_health_ready_nonblocking.py``:

* **structural pins** — the shape cannot come back through a refactor that
  keeps the observable response intact. A response is identical whether the
  probe ran on a private pool or on a shared one; only the AST can see it. The
  pool-split pin is here for a specific reason: the readiness probe
  (``sdk._get_proj``) has NO internal bound, so a SHARED pool lets a few
  concurrent readiness probes park every worker and starve liveness — which is
  the defect this change exists to remove, one level down.
* **behavioural tests** — prove the new dispatch actually works (right pool,
  right thread, probe really ran) and that fail-closed readiness survives.
"""

from __future__ import annotations

import ast
import asyncio
import concurrent.futures
import contextvars
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SELFHOST = REPO / "tortoise" / "selfhost.py"
SELFHOST_SRC = SELFHOST.read_text()

#: handler -> the (pool-NAME constant, pool-WIDTH constant) lane it must own.
#: Two lanes, never one: see ``test_probe_lanes_are_distinct_pools``.
LANES = {
    "health": ("_LIVENESS_PROBE_WORKER", "_LIVENESS_PROBE_WORKERS"),
    "health_ready": ("_READY_PROBE_WORKER", "_READY_PROBE_WORKERS"),
}

#: name constant -> the production pool name. It is ALSO the daemon thread-name
#: prefix (``_SingleSlotWorker`` suffixes a multi-worker pool's threads with
#: ``-<i>``), so these are pinned in the AST test below and asserted against the
#: threads the probes actually run on — a silent rename must not make the thread
#: assertions vacuous.
POOL_NAMES = {
    "_LIVENESS_PROBE_WORKER": "selfhost-liveness-probe",
    "_READY_PROBE_WORKER": "selfhost-ready-probe",
}

#: width constant -> the FEWEST workers it may carry.
#:
#: Liveness 2: its probe is inner-bounded (``monitoring.PROBE_TIMEOUT``), so a
#: worker always comes back; two absorbs an overlapping poll.
#:
#: Readiness 6: this pool must not be NARROWER than the shared default executor
#: it replaced — ``min(32, cpu+4)`` = 6 on the 2-vCPU hosted box. Isolation is
#: the fix; shrinking the pool is not. At 2, three concurrent ``/health/ready``
#: requests queue the third, which then spends its whole
#: ``_READY_PROBE_TIMEOUT_S`` waiting for a worker and reports a false 503 for a
#: healthy DB — the defect this change exists to remove, re-entered through
#: readiness fan-in. Exercised by
#: ``test_readiness_fan_in_does_not_produce_a_false_503``.
POOL_MIN_WORKERS = {
    "_LIVENESS_PROBE_WORKERS": 2,
    "_READY_PROBE_WORKERS": 6,
}

#: name constant -> its width constant (the two halves of one lane).
LANE_WIDTH = {
    "_LIVENESS_PROBE_WORKER": "_LIVENESS_PROBE_WORKERS",
    "_READY_PROBE_WORKER": "_READY_PROBE_WORKERS",
}


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


@pytest.mark.parametrize("name_const", sorted(POOL_NAMES))
def test_probe_lane_is_named_and_sized(name_const):
    """A per-call or default pool would reproduce the defect: per-call pools
    churn a thread per request (and cannot be bounded), and the default pool is
    the shared resource under attack. Each lane must name a pool and pin its
    width, and the name must be the exact string the thread assertions use."""
    assert _module_literal(name_const) == POOL_NAMES[name_const], (
        f"{name_const} is {_module_literal(name_const)!r}, expected "
        f"{POOL_NAMES[name_const]!r} — the behavioural tests assert probes run on "
        "that exact daemon-thread name, so a rename must not silently pass"
    )
    width_const = LANE_WIDTH[name_const]
    width = _prod_workers(width_const)
    assert width >= POOL_MIN_WORKERS[width_const], (
        f"{width_const}={width} is below the required {POOL_MIN_WORKERS[width_const]} "
        "— a lane narrower than the shared default executor it replaced converts "
        "readiness fan-in into a queue-timeout false 503"
    )


def test_probe_worker_resolves_to_the_shared_daemon_primitive():
    """#3286: the lanes are ``monitoring.daemon_worker`` pools, and the reason
    is load-bearing, so pin the two properties the choice buys.

    A ``ThreadPoolExecutor`` would satisfy every other pin here while
    (a) joining its NON-daemon workers at interpreter exit — a probe parked in a
    socket read then delays ``docker stop``'s drain past the #2203 budget and
    blocks process exit — and (b) buffering submissions without bound, which is
    the very complaint this change makes about the default executor.
    """
    import tortoise.monitoring as mon
    from tortoise import selfhost as sh

    lanes = {
        name_const: sh._probe_worker(_module_literal(name_const), _prod_workers(LANE_WIDTH[name_const]))
        for name_const in POOL_NAMES
    }
    # distinct RESOURCES, not merely distinct names (the names differ by
    # construction — see test_probe_lanes_are_distinct_pools).
    assert lanes["_LIVENESS_PROBE_WORKER"] is not lanes["_READY_PROBE_WORKER"], (
        "the two lanes resolve to the SAME pool — an unbounded readiness probe "
        "would then starve liveness, the defect this change removes"
    )
    for name_const, worker in lanes.items():
        assert isinstance(worker, mon._SingleSlotWorker), (
            f"{name_const} resolved to {type(worker).__name__}, not the "
            "_SingleSlotWorker daemon primitive — a ThreadPoolExecutor here "
            "reintroduces the non-daemon interpreter-exit join (#3286)"
        )
        # process-wide: the registry is keyed by name, so a second resolution
        # must return the SAME pool rather than leaking a new one per request.
        assert sh._probe_worker(_module_literal(name_const), 1) is worker, (
            f"{name_const} is not process-wide — resolving it twice built a "
            "second pool, so its width and backlog would not be the pinned ones"
        )
        assert worker.workers >= POOL_MIN_WORKERS[LANE_WIDTH[name_const]], (
            f"{name_const} has {worker.workers} worker(s) — see POOL_MIN_WORKERS"
        )
        assert worker._threads and all(t.daemon for t in worker._threads), (
            f"{name_const} has non-daemon worker(s) — they are JOINED at "
            "interpreter exit, so a parked probe blocks shutdown (#3286)"
        )
        assert worker._max_backlog == mon._SingleSlotWorker.MAX_BACKLOG, (
            f"{name_const}'s backlog is {worker._max_backlog!r}, not the primitive's "
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


def test_probe_lanes_are_distinct_pools():
    """The lane split is load-bearing, not tidiness.

    ``/health/ready``'s probe calls ``sdk._get_proj()`` DIRECTLY, so it is
    bounded only by the FalkorDB client's socket timeouts (5 s connect /
    10 s read) — NOT by ``monitoring.PROBE_TIMEOUT``. Enough concurrent
    readiness probes therefore park every worker of the readiness pool. If
    liveness shared that pool, parking readiness would hang ``/health`` — the
    same starvation defect, one level down. (Reproduced as a hang with a shared
    pool of 2 in ``test_liveness_answers_while_readiness_workers_are_parked``;
    the hosted twin reaches the same conclusion from the other direction, see
    ``test_health_ready_nonblocking.py``'s outer>inner bound pin.)
    """
    names = {name_const: _module_literal(name_const) for name_const in POOL_NAMES}
    assert len(set(names.values())) == len(names), (
        f"the two lanes share a pool NAME ({names}) — a single named pool cannot "
        "be split, and the pin below asserts distinct names on that basis"
    )
    for handler, (name_const, _width_const) in LANES.items():
        node = _handler(handler)
        submits = list(_calls(_walk_own_body(node), func_name="_submit_probe"))
        assert len(submits) == 1, f"selfhost {handler} must dispatch exactly one probe"
        # The pool must be resolved from THIS lane's constants — never hardcoded,
        # never the other lane's.
        resolved = {
            call.args[0].id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and getattr(call.func, "id", None) == "_probe_worker"
            and call.args
            and isinstance(call.args[0], ast.Name)
        }
        assert resolved == {name_const}, (
            f"selfhost {handler} resolves {sorted(resolved) if resolved else 'nothing'}, "
            f"expected {{{name_const!r}}} — each handler must own its lane"
        )
        other = {n for n in POOL_NAMES if n != name_const} | {
            w for n, w in LANE_WIDTH.items() if n != name_const
        }
        assert not (other & _names_in(node)), (
            f"selfhost {handler} references {sorted(other & _names_in(node))} — the two "
            "handlers must not share a pool"
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


@pytest.mark.parametrize("name", sorted(LANES))
def test_health_handlers_do_not_touch_db_code_on_the_loop(name):
    """Both endpoints (not just readiness) — /health is the 60 s boot-wait in
    publish-selfhost.yml and hung indefinitely when its probe queued."""
    node = _handler(name)
    offenders = [
        n.lineno
        for n in _walk_own_body(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"_get_proj", "query"}
    ]
    assert not offenders, (
        f"selfhost {name} touches DB/projection code directly at line(s) "
        f"{offenders} — synchronous I/O on the event loop (#2988)"
    )
    assert not list(_calls(ast.walk(node), attr="to_thread")), (
        f"selfhost {name} uses asyncio.to_thread — shared default executor (#3035)"
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


@pytest.fixture
def selfhost(monkeypatch, tmp_path):
    """The selfhost module with a stubbed SDK/probe.

    The pools are NOT replaced: the probes run on the REAL process-wide
    ``monitoring.daemon_worker`` pools production resolves, so the thread-name
    and width assertions validate production's resource rather than a
    fixture-local stand-in. (Replacing them would let a mis-named or too-narrow
    production pool pass — the failure mode round-1 review found in this suite.)
    The workers are daemons parked on a queue, so they cost the process nothing
    at exit and need no teardown.
    """
    import importlib

    monkeypatch.setenv("TORTOISE_HOST", "127.0.0.1")
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "selfhost.db"))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)

    import tortoise.monitoring as mon
    import tortoise.sdk as sdk_mod
    from tortoise import selfhost as sh

    importlib.reload(sh)

    monkeypatch.setattr(sdk_mod, "TortoiseSDK", _StubSDK)
    monkeypatch.setattr(
        mon, "probe_db", lambda sdk=None: {"ok": True, "latency_ms": 0.1, "error": None}
    )
    yield sh


class _Saturated:
    """Saturate the event loop's DEFAULT executor to its last worker.

    ``asyncio.to_thread``/``run_in_executor(None, ...)`` both submit here. A
    pre-fix probe queues behind ``gate`` and the request hangs; a probe on its
    own pool is unaffected. Deterministic by construction — no timing
    assumption about how long the pool stays busy.
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


# ── behavioural proof ──────────────────────────────────────────────────────


def test_health_answers_on_its_own_pool_while_the_default_pool_is_saturated(
    selfhost, monkeypatch
):
    """The discriminator for /health.

    A fast answer alone proves nothing: an endpoint that short-circuits its
    probe would also be fast. So this asserts BOTH that the probe RAN and which
    THREAD it ran on — the liveness pool — not merely that the call returned.
    """
    import tortoise.monitoring as mon

    seen: list[str] = []

    def _probe(sdk=None):
        seen.append(threading.current_thread().name)
        return {"ok": True, "latency_ms": 0.1, "error": None}

    monkeypatch.setattr(mon, "probe_db", _probe)

    async def scenario():
        async with _Saturated(), _client(selfhost) as ac:
            # Warm the process first: the FIRST request in a process pays a
            # one-time startup cost (measured ~1.6 s here, independent of this
            # change), which would otherwise be charged to the endpoint and make
            # a correct implementation look starved.
            await ac.get("/health")
            started = time.perf_counter()
            r = await asyncio.wait_for(ac.get("/health"), timeout=8.0)
            return r, time.perf_counter() - started

    r, elapsed = asyncio.run(scenario())

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert len(seen) == 2, (
        "the /health DB probe did not run once per request — a short-circuiting "
        "liveness handler must not be able to pass this test"
    )
    assert all(t.startswith("selfhost-liveness-probe") for t in seen), (
        f"probes ran on threads {seen}, not the dedicated liveness lane — "
        "they are still riding a shared executor (#3035)"
    )
    assert elapsed < 8.0, (
        f"/health took {elapsed:.2f}s while the default executor was saturated — "
        "the probe is queueing behind unrelated work (#3287)"
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
    tenant/graph scope — so pin the property behaviourally: a ContextVar set in
    the request's task must be visible inside the probe.
    """
    import tortoise.monitoring as mon

    probe_var = contextvars.ContextVar("probe-context-var", default="unset")
    seen: list[str] = []

    def _probe(sdk=None):
        seen.append(probe_var.get())
        return {"ok": True, "latency_ms": 0.1, "error": None}

    monkeypatch.setattr(mon, "probe_db", _probe)

    async def scenario():
        async with _client(selfhost) as ac:
            probe_var.set("caller-scope")
            return await ac.get("/health")

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
    width = _prod_workers("_READY_PROBE_WORKERS")
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
        f"_READY_PROBE_WORKERS={width}) — the lane is too narrow: queued requests time "
        "out before a worker ever runs their probe, and publish-selfhost.yml fails the "
        "release on that false 503"
    )


def test_liveness_answers_while_readiness_workers_are_parked(selfhost, monkeypatch):
    """The lane split's regression test (found in review of this change).

    ``/health/ready``'s probe is ``sdk._get_proj()`` with NO internal bound, so
    concurrent readiness probes park every worker of the readiness lane. A
    SHARED pool would then hang ``/health`` — i.e. the unbounded probe would
    starve the liveness probe, which is the original defect one level down.
    With two lanes, liveness is structurally immune.

    Both readiness workers are parked by construction (2 concurrent requests is
    >= the liveness width, and the assertion below is about the readiness LANE
    being unable to reach liveness at all).
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
        "parked — the two endpoints share a pool, so an unbounded readiness "
        "probe can starve liveness (#3287)"
    )
    assert live.json()["status"] in {"ok", "degraded"}, live.text
    assert elapsed < 3.0, (
        f"/health took {elapsed:.2f}s behind parked readiness workers — liveness "
        "is queueing behind readiness (#3287)"
    )


def test_readiness_lane_actually_has_two_usable_workers(selfhost, monkeypatch):
    """At least two readiness workers must be usable concurrently (the lane is
    wider — see ``POOL_MIN_WORKERS``). A single-slot lane would serialise the
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


def test_health_stays_200_degraded_when_the_db_is_down(selfhost, monkeypatch):
    """#1384's contract, unchanged by the new dispatch: a dead DB degrades
    /health (200 + db.ok=false) and never 5xxes the process."""
    import tortoise.monitoring as mon

    def _boom(sdk=None):
        raise ConnectionError("NXDOMAIN")

    monkeypatch.setattr(mon, "probe_db", _boom)

    async def scenario():
        async with _client(selfhost) as ac:
            return await ac.get("/health")

    r = asyncio.run(scenario())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "degraded"
    assert r.json()["db"]["ok"] is False
    assert "NXDOMAIN" in r.json()["db"]["error"]
