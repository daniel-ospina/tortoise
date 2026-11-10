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
that cannot serve, and — by two mechanisms, because an answer can stall in two
places — a loop that serves SLOWER than the bound too: no status line inside the
bound yields `000` → `DOWN` (`classify_code`, harness case 93), and headers
inside the bound with a body that never arrives yields a healthy-looking `200`
plus a non-zero curl exit, which `probe()` grades DOWN because the transfer did
not complete (harness case 100b). What it does NOT detect is a loop that serves
slowly WITHIN the bound: a 4 s answer passes a 5 s prober. It says nothing about
a deploy that never happened (`check-deploy-staleness.sh`), and it is not a
substitute for the TCP check that catches a dead process.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import threading
import time
from pathlib import Path

import pytest

# #1005: this module boots the REAL hosted app in-process, and the lifespan opens
# embedded (redislite) clients it never closes (the process exit does, in
# production). The shared helper reproduces the closing half; see
# `tests/_live_utils.py::close_embedded_clients_opened_since`.
from tests._live_utils import (
    close_embedded_clients_opened_since as _close_embedded_clients_opened_since,
)

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
    from tortoise import embedded_lifecycle as embedded_lifecycle

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
    # #1005: the real lifespan opens embedded (redislite) clients for the boot
    # sweeps and the DB-probe SDK and never closes them — in production the
    # process exit does. Snapshot before the boot so the teardown closes only
    # what THIS boot opened (a co-tenant's clients are left alone).
    embedded_before = set(embedded_lifecycle._embedded_clients)
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
        _close_embedded_clients_opened_since(embedded_before)


@pytest.fixture(autouse=True, scope="module")
def _close_embedded_clients_this_module_opened():
    """Race guard: close any embedded client the app's boot sweeps open late.

    The per-boot close above cannot see a client a wedged boot worker registers
    AFTER its own teardown (``monitoring.run_on_daemon_worker`` abandons rather
    than delays shutdown), and the next boot's snapshot would count it as
    pre-existing. This module-scoped sweep closes everything opened since the
    module started, before the suite's #1005 orphan-hygiene gate runs.
    Idempotent for every client the per-boot close already handled.
    """
    from tortoise import embedded_lifecycle as embedded_lifecycle

    before = set(embedded_lifecycle._embedded_clients)
    yield
    _close_embedded_clients_opened_since(before)


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

    And the age itself is pinned against a DELIBERATE interval, from both sides:
    a bare ``age >= 0`` is satisfied by a constant ``0.0``, which is what the
    plausible mutation of the handler produces — compute the field from the
    JUST-STAMPED ``_LIVENESS_LAST_SERVED_AT`` instead of from ``previous`` and
    every age after the first collapses to ``(now - now)``, leaving the field's
    documented contract quietly meaningless while the test stayed green
    (measured: the pre-fix assertion passed that mutant, 1 passed).
    """
    import tortoise.hosted_api as ha

    monkeypatch.setattr(ha, "_LIVENESS_SERVED_COUNT", 0)
    monkeypatch.setattr(ha, "_LIVENESS_LAST_SERVED_AT", None)

    with _live_hosted_service(monkeypatch) as captured:
        base = captured["base_url"]
        first, first_elapsed = _get(base, "/liveness", timeout=PROBE_BUDGET_S)
        # A DELIBERATE, measured interval between the two probes, so the age is
        # a property of the schedule rather than of incidental local latency.
        # The clock cannot be injected across the socket (the app runs under
        # uvicorn here, and patching `time.monotonic` would patch the client's
        # clock too), so 50 ms — 500x the µs-scale noise of two loopback
        # requests — is made explicit instead of hoped for, and both bounds
        # below are derived from it.
        gap_started = time.monotonic()
        time.sleep(0.05)
        second, second_elapsed = _get(base, "/liveness", timeout=PROBE_BUDGET_S)
        gap_ms = (time.monotonic() - gap_started) * 1000.0

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
    # ⛔ THE UPPER BOUND CARRIES THE FIRST REQUEST'S OWN ROUND TRIP, and the
    # derivation is why (`#11730`): the handler stamps BOTH probes on ENTRY
    # (`_LIVENESS_LAST_SERVED_AT = now`, before the response is written), while
    # `gap_ms` starts only after the FIRST response was DELIVERED. So
    #     age = (entry2 - entry1) = (entry2 - recv2) + (recv2 - recv1) + (recv1 - entry1)
    #                              ≤ 0            + gap_ms           + first_elapsed_ms
    # and `first_elapsed_ms` — the client's own measurement of probe 1 — is the
    # only term that bounds `(recv1 - entry1)`. Omitting it is what reddened CI:
    # the first request pays the cold-start cost of the whole app (measured on a
    # hosted runner: age=476.4 ms against a gap of 424.1 ms, i.e. 52 ms of probe-1
    # delivery latency the old `gap_ms + 25` could not hold). The 25 ms is slack
    # for the two statements between `_get` returning and `gap_started` being
    # read, NOT for delivery latency — that is derived, not hoped for.
    assert isinstance(age, (int, float)) and 40.0 <= age <= (
        gap_ms + first_elapsed * 1000.0 + 25.0
    ), (
        f"the second request reported last_served_age_ms={age!r}, but the two "
        f"probes are separated by a deliberate 50 ms interval and the client "
        f"measured {gap_ms:.1f} ms between them (probe 1 itself took "
        f"{first_elapsed * 1000.0:.1f} ms) — so the age of the PREVIOUS request "
        "is bounded below by the interval and above by the round trip. "
        "The lower bound reds computing the field from the just-stamped "
        "_LIVENESS_LAST_SERVED_AT instead of from `previous` — every age after "
        "the first collapses to 0.0 and the field no longer means 'age of the "
        "PREVIOUS request'; the upper bound reds one that measures from "
        "PROCESS START instead of from the previous request. "
        "Between them the field means what its docstring says it means")
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
    # ⛔ It must be `/liveness`'s OWN elapsed time, not `max(...)`: a blocked
    # /health always burns its whole client bound, so a max() over both is
    # satisfied by /health alone and would pass a run where /liveness answered
    # instantly with a fast 5xx — the state-read shape this test exists to
    # reject (review round 6).
    assert liveness_elapsed >= PROBE_BUDGET_S / 2, (
        f"/liveness returned in {liveness_elapsed:.2f}s, far below its "
        f"{PROBE_BUDGET_S:.0f}s bound, while the loop was held for {BLOCK_S:.0f}s "
        f"(health {health_elapsed:.2f}s) — it never met the block, so an answer "
        "that fast was not produced by being served")

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


def test_the_served_endpoint_is_rate_limit_exempt():
    """`/liveness` must stay in `RateLimitMiddleware.SKIP` (#10892 review round 6).

    The prober makes ONE request per run against a route that does no I/O of its
    own, so a per-IP bucket can only ever produce a 429 on a HEALTHY origin — and
    a 429 is a PROD DEGRADED page, i.e. the alarm firing because the alarm was
    rate limited. `hosted_api.py` argues that in a comment, but a comment cannot
    fail, and `tests/conftest.py` exports `RATE_LIMIT_DISABLED=1` — so no test
    in the suite can observe the middleware actually skipping the path. The
    membership is therefore pinned STRUCTURALLY, and the path is resolved FROM
    THE APP so a moved route cannot leave this green (the same pattern as the
    transport-bound test above).
    """
    import tortoise.hosted_api as ha

    routes = [
        route for route in ha.app.routes
        if getattr(route, "path", "").rstrip("/").endswith("/liveness")
    ]
    assert routes, (
        "no route ending in /liveness is registered on the app — this test's "
        "subject is gone, so it cannot certify anything (#10892)")

    skip = set(ha.RateLimitMiddleware.SKIP)
    for route in routes:
        assert route.path in skip, (
            f"{route.path} is NOT in RateLimitMiddleware.SKIP — the availability "
            "prober would then spend a per-IP bucket it does not need, and a 429 "
            "on a healthy origin would page PROD DEGRADED (#10892)")
