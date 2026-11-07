"""#10892 — a request that must RETURN, and an alarm that fires on its absence.

**The incident this pins.** On 2026-11-04 `api.premiselabs.co` — the hosted
product API — was unusable for roughly two hours, and again for six hours the
same day, while **both Fly health checks reported `2 total, 2 passing`**. Every
request was refused with a transport-wait 504 arriving 30–45 s late; clients with
any sane timeout saw TCP accept and **zero bytes**.

Both checks are liveness-shaped in the sense this file fixes:

* `servicecheck-00-tcp-8000` is a TCP connect check (`fly.toml`, #2850) — the
  handshake is served by the KERNEL, so a totally hung app passes it. It did.
* `[checks.loop_liveness]` points at the `9090 /healthz` listener, which runs on
  its own thread and answers from **state the loop last wrote**
  (`monitoring.loop_heartbeat_info()`), with a verdict that errs to 200 by
  design (`stale AND idle`). A loop 8 minutes behind still read `status: ok` —
  measured `loop_lag_max_ms: 483685` and `passing` in the same payload.

**What was measured here, and it corrects the issue's own hypothesis.**
`/health` on port 8000 does **not** answer from cached state while the loop is
blocked — it cannot be served at all, so a client with a bounded timeout gets
**no answer** (`test_a_blocked_loop_cannot_serve_either_endpoint`). So the
defect was never "`/health` returns a wrong 200": it was that **nothing bounded
the absence** and nothing pointed at a request that has to be served. That is
what this change adds, and the two halves are pinned separately because either
one alone is a false PASS:

1. ``GET /liveness`` (`hosted_api.liveness`) — an answer that can only be
   produced BY being served: it yields to the loop once, then increments a
   per-process counter **for this request** and reports the age of the
   PREVIOUS one, stamped on handler ENTRY (not on delivery, so a request that
   ran the loop and never delivered still counts). No counter, no answer — so a replayed, cached or off-loop
   body is impossible rather than merely unlikely
   (`test_handler_yields_to_the_loop_and_reads_no_cached_state` closes the
   "make it a state read like `/healthz`" regression statically).
2. The prober that FAILS ON ABSENCE — `.github/workflows/availability-watchdog.yml`
   probes that path with a **5 s** per-request bound, against the API probe's
   generous 25 s. `/liveness` does no I/O of its own (one loop yield and one
   counter increment: no DB, no network). During the outage the app took
   9–12 s to `000` from outside (and 45 s to the eventual 504), so a 5 s bound
   alarms within one cycle and a 25 s status-only probe does not exist for this
   shape at all. The workflow half is pinned in
   `.github/scripts/availability-watchdog.test.sh` (cases 88b/99/100/101), not
   here — the
   harness already owns "the step, its label and the script's production-set
   membership must move together".

**Blind spots, stated rather than implied** (the discipline #10892's own scope
comment asks for — `issues/10892#issuecomment-6698714004`): this detects a loop
that cannot
serve, and — the prober's `--max-time` converts an answer later than the bound
into `000` → `DOWN` (`classify_code`, harness case 93) — a loop that serves
SLOWER than the bound too. What it does NOT detect is a loop that serves slowly
WITHIN the bound: a 4 s answer passes a 5 s prober. It says nothing about a
deploy that never happened (`check-deploy-staleness.sh`), and it is not a
substitute for the TCP check that catches a dead process.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOSTED_API = REPO / "tortoise" / "hosted_api.py"

#: The client budget this test holds the service to. It is not a round number
#: chosen to pass: the prober's own per-request bound is 5 s
#: (`.github/workflows/availability-watchdog.yml`, the served-liveness step), so
#: a client that waits longer than that is waiting past the point where the page
#: has already fired. 5 s is a DESIGN bound over a handler that does no I/O of
#: its own (one loop yield, one counter increment — no DB, no network), not a
#: multiple of a measured service time: nothing in this repo pins a latency
#: figure, so none is claimed here.
PROBE_BUDGET_S = 5.0

#: How long the loop is held hostage in the blocked-loop tests. Longer than the
#: client budget, so a handler that waits cannot answer inside it, and long
#: enough that the block is unambiguously in force for the whole probe.
BLOCK_S = 3 * PROBE_BUDGET_S


# ── live-service harness ───────────────────────────────────────────────────
# The REAL hosted app on a REAL port, because the claim under test is what a
# client observes over a socket. The loop is captured by wrapping
# `uvicorn.Server.serve` — no test hook is added to the app itself (a
# production backdoor is exactly the class this repo refuses).


@contextlib.contextmanager
def _live_hosted_service(monkeypatch):
    import uvicorn

    import tortoise.hosted_api as ha

    monkeypatch.setattr(ha, "_health_probe_interval", lambda: 3600.0)
    ha._HEALTH_PROBE.reset()

    captured: dict = {}
    orig_serve = uvicorn.Server.serve

    async def _serve_wrapper(self, *a, **kw):
        captured["loop"] = asyncio.get_running_loop()
        return await orig_serve(self, *a, **kw)

    monkeypatch.setattr(uvicorn.Server, "serve", _serve_wrapper)

    server = uvicorn.Server(
        uvicorn.Config(ha.app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 120.0
        while not (server.started and server.servers) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "the hosted service never started"
        port = server.servers[0].sockets[0].getsockname()[1]
        captured["base_url"] = f"http://127.0.0.1:{port}"
        yield captured
    finally:
        server.should_exit = True
        thread.join(timeout=60)
        ha._HEALTH_PROBE.reset()


def _get(base_url: str, path: str, *, timeout: float):
    """One real request. Returns ``(outcome, elapsed)`` where outcome is either a
    response or the EXCEPTION TYPE NAME — the client's own view, which is the
    whole point: during the outage the client's view was "nothing came back".
    """
    import httpx

    started = time.monotonic()
    try:
        with httpx.Client(timeout=timeout) as client:
            return client.get(f"{base_url}{path}"), time.monotonic() - started
    except Exception as exc:  # the exception IS the observation
        return type(exc).__name__, time.monotonic() - started


@contextlib.contextmanager
def _blocked_loop(captured):
    """Hold the event loop with a SYNCHRONOUS sleep, from inside the loop.

    This is the production shape (#10892): sentry-sdk's event builder ran
    `decode_source`/`tokenize` on the loop thread with the GIL held, so every
    other request had TCP already accepted and no bytes coming. `time.sleep`
    inside a coroutine reproduces that exactly — the thread is parked, so no
    task can run, while the socket stays accepted by the kernel.
    """
    entered = threading.Event()
    loop = captured["loop"]

    async def _block(seconds: float):
        entered.set()
        time.sleep(seconds)

    future = asyncio.run_coroutine_threadsafe(_block(BLOCK_S), loop)
    try:
        deadline = time.monotonic() + 30.0
        while not entered.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert entered.is_set(), (
            "the injected loop block never started — a test that then observes "
            "a fast answer would be vacuous (nothing was blocked)")
        yield
    finally:
        with contextlib.suppress(Exception):
            future.result(timeout=BLOCK_S + 30.0)


# ── 1. the endpoint is answerable, and only by being served ────────────────


def test_served_liveness_reports_a_counter_only_a_served_request_can_produce(monkeypatch):
    """Two requests must see two DIFFERENT counters, one apart.

    The counter is the endpoint's reason to exist: an answer read back from
    cached state (the `/healthz` shape this issue is about) cannot move it. The
    first-request ``null`` is asserted separately because "no sample" must not
    be spelled as ``0.0`` — that would make an unread age indistinguishable from
    a very fast one.
    """
    import tortoise.hosted_api as ha

    monkeypatch.setattr(ha, "_LIVENESS_SERVED_COUNT", 0)
    monkeypatch.setattr(ha, "_LIVENESS_LAST_SERVED_AT", None)

    with _live_hosted_service(monkeypatch) as captured:
        base = captured["base_url"]
        first, first_elapsed = _get(base, "/liveness", timeout=PROBE_BUDGET_S)
        second, second_elapsed = _get(base, "/liveness", timeout=PROBE_BUDGET_S)

    assert not isinstance(first, str), (
        f"/liveness did not answer ({first}) — this endpoint must be answerable "
        "by a served request inside the prober's own 5 s bound")
    assert not isinstance(second, str), f"the second /liveness did not answer ({second})"

    assert first.status_code == 200, f"/liveness answered {first.status_code}, not 200"
    cache_control = first.headers.get("cache-control", "")
    assert "no-store" in cache_control, (
        f"/liveness answered with Cache-Control={cache_control!r}; the docstring's "
        "provenance claim ('two probes must never see the same number') is only a "
        "property if no intermediary may replay the answer — an unpinned header is "
        "a comment, not a property")
    body = first.json()
    assert body["status"] == "ok"
    assert body["served"] == 1, (
        f"the first request carried served={body['served']!r}; the counter is "
        "incremented FOR THE REQUEST, so the first one must read 1")
    assert body["last_served_age_ms"] is None, (
        "the first request reported an age for a request that never happened "
        f"({body['last_served_age_ms']!r}) — null is the 'no sample' value")

    body2 = second.json()
    assert body2["served"] == 2, (
        f"the second request carried served={body2['served']!r}, expected 2 — a "
        "counter that does not move is a value cached, not served")
    age = body2["last_served_age_ms"]
    assert isinstance(age, (int, float)) and age >= 0, (
        f"last_served_age_ms={age!r} is not a non-negative number")
    assert first_elapsed < PROBE_BUDGET_S and second_elapsed < PROBE_BUDGET_S, (
        f"/liveness took {first_elapsed:.2f}s/{second_elapsed:.2f}s — the prober "
        f"bounds this at {PROBE_BUDGET_S:.0f}s")


def test_a_blocked_loop_cannot_serve_either_endpoint(monkeypatch):
    """THE MEASUREMENT: a blocked loop answers NOTHING, and that is the signal.

    This is the pin the issue's own `## Scope for a lane` asked for ("pin the
    failure shape as a test before writing any probe"), and its answer is the
    correction recorded in the module docstring: `/health` does **not** return a
    stale 200 here — it cannot be served at all, so the client sees no answer
    inside its bound. That is why the fix is a prober with a bound on ABSENCE
    plus a served endpoint whose answer proves provenance, and NOT a stricter
    reading inside `/health`.

    Red-or-nothing: if someone later makes `/health` answer from an off-loop
    cache (returning 200 while the loop is parked), this test reds — and it
    should, because that answer would be a state read wearing a liveness name,
    which is precisely what read `2/2 passing` through a two-hour outage.
    """
    with _live_hosted_service(monkeypatch) as captured:
        base = captured["base_url"]
        baseline, _ = _get(base, "/liveness", timeout=PROBE_BUDGET_S)
        assert not isinstance(baseline, str), (
            f"/liveness was already unanswerable before anything was blocked "
            f"({baseline}) — the assertion below would prove nothing")

        with _blocked_loop(captured):
            health_outcome, health_elapsed = _get(base, "/health", timeout=PROBE_BUDGET_S)
            liveness_outcome, liveness_elapsed = _get(
                base, "/liveness", timeout=PROBE_BUDGET_S)

        after, _after_elapsed = _get(base, "/liveness", timeout=PROBE_BUDGET_S)

    # A blocked loop may answer with the outermost WaitBoundMiddleware's 504
    # (when it resumes) or not at all (while it holds). Both are "no usable
    # answer inside the client's bound"; a 2xx is the failure this asserts.
    assert isinstance(health_outcome, str) or health_outcome.status_code >= 500, (
        "the loop was blocked for "
        f"{BLOCK_S:.0f}s and /health still answered {health_outcome.status_code} "
        f"in {health_elapsed:.2f}s — an endpoint that answers while the loop is "
        "parked is a STATE READ, and that is the reading that stayed green for "
        "two hours (#10892)")
    assert isinstance(liveness_outcome, str) or liveness_outcome.status_code >= 500, (
        "the loop was blocked and /liveness still answered "
        f"{liveness_outcome.status_code} in {liveness_elapsed:.2f}s — its answer "
        "must be impossible to produce without the loop running it")
    # The two assertions above accept either no answer at all (the probe's own
    # bound expired) or a 5xx (the outermost WaitBound fired first) — BOTH are
    # "no usable answer inside the client's bound". ⚠️ The 5xx arm is ROBUSTNESS,
    # not a path this test measures: `_TRANSPORT_WAIT_BOUND_S` is 10 s while
    # `PROBE_BUDGET_S` is 5 s and the block is held for `BLOCK_S`, so the client
    # budget always expires first and both probes end as a read timeout. So the
    # evidence that
    # the loop was genuinely HELD is not the ANSWER's shape, it is the TIME: a
    # probe that returned in a few ms never met the block at all. Asserting that
    # directly is deterministic where asserting "both timed out" was not (#10892
    # review round 4: those two branches used to forbid the 504 the messages
    # above call valid).
    assert max(health_elapsed, liveness_elapsed) >= PROBE_BUDGET_S / 2, (
        f"both probes returned far below their {PROBE_BUDGET_S:.0f}s bound "
        f"(health {health_elapsed:.2f}s, liveness {liveness_elapsed:.2f}s) while "
        f"the loop was held for {BLOCK_S:.0f}s — they never met the block, so "
        "this run observed nothing and cannot testify")

    # …and the service is genuinely serving again afterwards: the two probes
    # above observed a REAL block, not a dead process (which would make the
    # outage claim vacuous).
    assert not isinstance(after, str), (
        f"/liveness did not recover after the loop was released ({after})")
    assert after.status_code == 200


# ── 2. the static half: it cannot become a state read ──────────────────────


def _own_body(node: ast.AST):
    """Walk a coroutine's OWN statements, not the bodies of nested functions."""
    for stmt in getattr(node, "body", []):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield from ast.walk(stmt)


def _handler(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(HOSTED_API.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {HOSTED_API.name}")


def test_handler_yields_to_the_loop_and_reads_no_cached_state():
    """The provenance guarantee, structurally — and the regression it forbids.

    Two independent ways this endpoint could stop meaning anything, both
    silent in production:

    * it stops ``await``-ing at all, becoming a synchronous body that could be
      answered without the loop scheduling another task — the guarantee is then
      a comment, not a property;
    * it starts reading ``loop_heartbeat_info()`` / ``_HEALTH_PROBE`` /
      ``snapshot()`` — i.e. it becomes `/healthz`, answering about the loop from
      state the loop last wrote. That is the circularity #10892 is about.
    """
    body = list(_own_body(_handler("liveness")))

    awaits_calls = [n for n in body if isinstance(n, ast.Await)
                    and isinstance(n.value, ast.Call)]
    assert awaits_calls, (
        "the /liveness handler contains no `await <call>()` in its own body — "
        "without a trip through the loop's ready queue its answer could be "
        "produced by a synchronous handler, so 'served' would be an assertion "
        "rather than a property (#10892)")

    # The three state readers this pin forbids — and ONLY those. A bare `info`
    # used to be in this set, which matched any `.info` attribute, so a benign
    # `_logger.info(…)` inside the handler RED with a message asserting it "reads
    # cached state" — a false red whose cheapest repair is to weaken the set,
    # which is the property that would then be lost. `loop_heartbeat_info` is
    # already matched both as a bare name and as an attribute, so nothing is
    # given up by dropping it.
    forbidden = {"loop_heartbeat_info", "_HEALTH_PROBE", "snapshot"}
    offenders = []
    for node in body:
        if isinstance(node, ast.Name) and node.id in forbidden:
            offenders.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute) and node.attr in forbidden:
            offenders.append((node.lineno, node.attr))
    assert not offenders, (
        f"the /liveness handler reads cached state at {offenders} — those values "
        "are written by the loop's own timer/threads, so reading them here "
        "re-creates the `/healthz` blind spot (a loop 8 minutes behind reports "
        "ok). The served counter and its age must be computed IN the handler")


def test_the_served_endpoint_is_not_transport_bound_exempt():
    """A stalled `/liveness` must become a LEGIBLE refusal, not a silent hang.

    `_TRANSPORT_WAIT_BOUND_EXEMPT` is a small, reasoned set (`POST /v1/context`,
    plus the `/v1/internal/` prefix). Exempting this path would remove the only
    bound that ever fired during the outage (the 504 at ~45 s) and leave the
    prober with "no answer, no reason".
    """
    import tortoise.hosted_api as ha

    # The route under test, READ FROM THE APP rather than restated here: an
    # assertion over a literal ("/liveness") cannot see the endpoint MOVE, so
    # moving it under the exempt prefix would leave a literal-based assertion
    # green while the endpoint became exempt (fresh-context review, #11340).
    routes = [
        route for route in ha.app.routes
        if getattr(route, "path", "").rstrip("/").endswith("/liveness")
    ]
    assert routes, (
        "no route ending in /liveness is registered on the app — this test's "
        "subject is gone, so it cannot certify anything (#10892)")

    exempt = set(ha._TRANSPORT_WAIT_BOUND_EXEMPT)
    for route in routes:
        path = route.path
        assert ("GET", path) not in exempt, (
            f"{path} is transport-bound exempt — a stalled request would then hang "
            "with no refusal at all, and the 504 that made the 2026-11-04 outage "
            "legible would never be produced (see the exemption's own OVERRIDES "
            "block in hosted_api.py)")
        assert not path.startswith(ha._TRANSPORT_WAIT_BOUND_EXEMPT_PREFIX), (
            f"{path} sits under the "
            f"{ha._TRANSPORT_WAIT_BOUND_EXEMPT_PREFIX!r} prefix exemption — that "
            "prefix is for internal-only routes (the operators that already hold "
            "the bound themselves), and a probe that must RETURN cannot live under "
            "it: exempting the liveness route there would re-create the silent hang "
            "the 504 exists to make legible (#10892)")
