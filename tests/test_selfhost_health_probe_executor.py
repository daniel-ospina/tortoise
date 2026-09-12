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
    /health/ready  : HTTP 503 after 6020ms        (probe never ran)

``publish-selfhost.yml`` curls ``/health/ready`` and fails the publish on a
non-200, so that false 503 blocks a release of a perfectly healthy image;
``/health`` is the 60 s boot-wait in the same workflow.

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
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SELFHOST = REPO / "tortoise" / "selfhost.py"
SELFHOST_SRC = SELFHOST.read_text()

#: handler -> the pool that handler must own. Two pools, never one: see
#: ``test_liveness_and_readiness_do_not_share_a_pool``.
POOLS = {
    "health": "_LIVENESS_PROBE_EXECUTOR",
    "health_ready": "_READY_PROBE_EXECUTOR",
}

#: pool -> its production ``thread_name_prefix``. Pinned in the AST test below so
#: the fixture, the assertions and the production source cannot drift apart (a
#: silent rename would otherwise make the thread assertions vacuous).
THREAD_PREFIXES = {
    "_LIVENESS_PROBE_EXECUTOR": "selfhost-liveness-probe",
    "_READY_PROBE_EXECUTOR": "selfhost-ready-probe",
}


# ── AST helpers ────────────────────────────────────────────────────────────


def _handler(name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(ast.parse(SELFHOST_SRC)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in selfhost.py")


def _module_assign(name: str) -> ast.Assign:
    for node in ast.parse(SELFHOST_SRC).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node
    raise AssertionError(f"module-level {name} not found in selfhost.py")


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


@pytest.mark.parametrize("pool", sorted(POOLS.values()))
def test_probe_pool_is_module_level_sized_and_named(pool):
    """A per-call or default pool would reproduce the defect: per-call pools
    churn a thread per request (and cannot be bounded), and the default pool is
    the shared resource under attack. Each pool must be module-level,
    explicitly sized, and named so the thread is identifiable in a dump."""
    assign = _module_assign(pool)
    ctor = assign.value
    assert isinstance(ctor, ast.Call) and getattr(ctor.func, "id", None) == "ThreadPoolExecutor", (
        f"{pool} must be a module-level ThreadPoolExecutor"
    )
    kwargs = {kw.arg: kw.value for kw in ctor.keywords}
    assert "max_workers" in kwargs, f"{pool} must pin max_workers"
    assert not (
        isinstance(kwargs["max_workers"], ast.Constant) and kwargs["max_workers"].value is None
    ), f"{pool} with max_workers=None is the default (shared, cpu-derived) sizing"
    assert isinstance(kwargs["max_workers"], ast.Constant) and kwargs["max_workers"].value >= 2, (
        f"{pool} must hold two concurrent probes (a poll overlapping the deploy gate)"
    )
    assert "thread_name_prefix" in kwargs, (
        f"{pool} is unnamed — invisible in a thread dump, so a starved or wedged "
        "probe cannot be identified in production"
    )
    assert kwargs["thread_name_prefix"].value == THREAD_PREFIXES[pool], (
        f"{pool}'s thread_name_prefix is {kwargs['thread_name_prefix'].value!r}, "
        f"expected {THREAD_PREFIXES[pool]!r} — the behavioural tests assert probes run "
        "on that exact thread name, so a rename must not silently pass"
    )


def test_liveness_and_readiness_do_not_share_a_pool():
    """The pool split is load-bearing, not tidiness.

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
    distinct = set()
    for handler, pool in POOLS.items():
        node = _handler(handler)
        submits = list(_calls(_walk_own_body(node), func_name="_submit_probe"))
        assert len(submits) == 1, f"selfhost {handler} must dispatch exactly one probe"
        args = submits[0].args
        assert len(args) >= 1, f"selfhost {handler}'s _submit_probe call passes no executor"
        assert isinstance(args[0], ast.Name), (
            f"selfhost {handler} passes a non-module executor expression to _submit_probe"
        )
        assert args[0].id == pool, (
            f"selfhost {handler} dispatches through {args[0].id!r}, expected {pool!r}"
        )
        distinct.add(pool)
        # The other pool must not appear anywhere in this handler.
        other = {p for p in POOLS.values() if p != pool}
        assert not (other & _names_in(node)), (
            f"selfhost {handler} references {sorted(other & _names_in(node))} — the two "
            "handlers must not share a pool"
        )
    assert distinct == set(POOLS.values()), "the handlers do not use two distinct pools"


def test_submit_probe_targets_the_given_pool_only():
    """``_submit_probe`` is the single seam. It must submit to the executor it
    is GIVEN and never to the loop's default executor (``run_in_executor(None,
    ...)``) or through ``to_thread`` (which always uses the default pool)."""
    node = next(
        n
        for n in ast.walk(ast.parse(SELFHOST_SRC))
        if isinstance(n, ast.FunctionDef) and n.name == "_submit_probe"
    )
    params = [a.arg for a in node.args.args]
    assert params and params[0] == "executor", (
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
        assert call.func.value.id == "executor", (
            f"_submit_probe submits to {call.func.value.id!r} at line {call.lineno} — it "
            "must submit to its executor argument"
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


@pytest.mark.parametrize("name", sorted(POOLS))
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
    """The selfhost module with fresh, named, SEPARATE probe pools and a stubbed
    SDK/probe.

    The pools are replaced per test so the tests can assert on the THREAD each
    probe ran on, and so no worker leaks between tests. The handlers resolve the
    module globals by name, so patching them exercises the same seam production
    uses.
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

    pools = {}
    for pool_name in POOLS.values():
        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix=THREAD_PREFIXES[pool_name]
        )
        pools[pool_name] = pool
        # raising=False: on the PRE-FIX code these attributes do not exist, and the
        # behavioural tests must still get far enough to fail on the DEFECT (a
        # starved probe) rather than on a missing fixture attribute — otherwise the
        # mutation proof would only show that a global was renamed.
        monkeypatch.setattr(sh, pool_name, pool, raising=False)
    try:
        yield sh
    finally:
        for pool in pools.values():
            pool.shutdown(wait=False)


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
            started = time.perf_counter()
            r = await asyncio.wait_for(ac.get("/health"), timeout=3.0)
            return r, time.perf_counter() - started

    r, elapsed = asyncio.run(scenario())

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert len(seen) == 1, (
        "the /health DB probe did not run exactly once — a short-circuiting "
        "liveness handler must not be able to pass this test"
    )
    assert seen[0].startswith("selfhost-liveness-probe"), (
        f"the probe ran on thread {seen[0]!r}, not the dedicated liveness pool — "
        "it is still riding a shared executor (#3035)"
    )
    assert elapsed < 3.0, (
        f"/health took {elapsed:.2f}s while the default executor was saturated — "
        "the probe is queueing behind unrelated work (#3287)"
    )


def test_ready_does_not_lie_while_the_default_pool_is_saturated(selfhost, monkeypatch):
    """The regression that actually blocks releases: a starved readiness probe
    returned 503 for a HEALTHY database (measured 6020 ms, probe never ran),
    and publish-selfhost.yml fails the publish on a non-200 /health/ready."""
    seen: list[str] = []
    monkeypatch.setattr(
        _StubSDK, "_get_proj", lambda self: seen.append(threading.current_thread().name)
    )

    async def scenario():
        async with _Saturated(), _client(selfhost) as ac:
            started = time.perf_counter()
            r = await asyncio.wait_for(ac.get("/health/ready"), timeout=3.0)
            return r, time.perf_counter() - started

    r, elapsed = asyncio.run(scenario())

    assert r.status_code == 200, (
        f"/health/ready answered {r.status_code} ({r.text}) while the DB was "
        "healthy and only an UNRELATED task held the default executor — a "
        "starved probe is a false not-ready that fails the publish (#3287)"
    )
    assert r.json()["status"] == "ready"
    assert len(seen) == 1, "the readiness probe did not run exactly once"
    assert seen[0].startswith("selfhost-ready-probe"), (
        f"the readiness probe ran on thread {seen[0]!r}, not the dedicated readiness pool"
    )
    assert elapsed < 3.0, f"/health/ready took {elapsed:.2f}s while starved (#3287)"


def test_liveness_answers_while_readiness_workers_are_parked(selfhost, monkeypatch):
    """The pool split's regression test (found in review of this change).

    ``/health/ready``'s probe is ``sdk._get_proj()`` with NO internal bound, so
    concurrent readiness probes park every worker of the readiness pool. A
    SHARED pool would then hang ``/health`` — i.e. the unbounded probe would
    starve the liveness probe, which is the original defect one level down.
    With two pools, liveness is structurally immune.
    """
    release = threading.Event()
    monkeypatch.setattr(selfhost, "_READY_PROBE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(_StubSDK, "_get_proj", lambda self: release.wait(30))

    async def scenario():
        async with _Saturated(), _client(selfhost) as ac:
            # Park every readiness worker: both of these time out at 0.3s
            # while their workers stay blocked on release.
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


def test_readiness_pool_actually_has_two_usable_workers(selfhost, monkeypatch):
    """Both readiness workers must be usable concurrently. A single-slot pool
    would serialise the deploy gate's probe behind any other readiness poll."""
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
        "a second readiness probe never reached a second worker — the pool is "
        f"single-slot (threads seen: {threads})"
    )
    for r in responses:
        assert r.status_code == 200 and r.json()["status"] == "ready", r.text
    assert len({t for t in threads}) == 2, f"expected two distinct workers, saw {threads}"


def test_hung_db_still_fails_closed_within_the_bound(selfhost, monkeypatch):
    """#2988's guarantee must survive the new dispatch: a black-holed DB is
    REPORTED (503) within the bound, never waited out."""
    release = threading.Event()
    monkeypatch.setattr(selfhost, "_READY_PROBE_TIMEOUT_S", 0.2)
    monkeypatch.setattr(_StubSDK, "_get_proj", lambda self: release.wait(30))

    async def scenario():
        async with _client(selfhost) as ac:
            started = time.perf_counter()
            try:
                r = await ac.get("/health/ready")
            finally:
                release.set()
            return r, time.perf_counter() - started

    r, elapsed = asyncio.run(scenario())

    assert r.status_code == 503, r.text
    assert r.json()["status"] == "not_ready"
    assert elapsed < 1.5, (
        f"the endpoint waited {elapsed:.2f}s for a hung probe — the bound is not applied"
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
