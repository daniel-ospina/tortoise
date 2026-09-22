"""#3811 — the client-visible effect on ``/health``, executed over a real socket.

**What a client must observe.** A client that starts against the hosted service
must observe ``/health`` answer **HTTP 200** with a **JSON object** carrying
``status`` (a string, ``"ok"`` or ``"degraded"``) and a ``db`` object
(``{"ok": bool, "latency_ms": …, "error": …}``) — the field the deploy gate reads
*by value*, never by spelling (``.github/workflows/deploy-hosted.yml``, #4470).
The value may be ``degraded``: a dead downstream is *reported* in the body, never
hidden and never turned into a non-200 by the *handler*. (The body, not a 5xx,
is where health truth lives.)

The honest limit on "must observe 200": ``/health`` is **not** exempt from the
outermost ``WaitBoundMiddleware`` (``_TRANSPORT_WAIT_BOUND_EXEMPT`` covers only
``POST /v1/context``), so a request that does not complete inside its 10 s wait
bound (``tortoise/mcp_auth.py::_TRANSPORT_WAIT_BOUND_S``) is answered **504 +
``Retry-After``** instead of hanging (#4412, ``tests/test_transport_wait_bound.py``).
That refusal is *legible*, but a no-retry client cannot act on it — so a 200
inside the budget is still the requirement, and the refusal is the legible-failure
floor, not a substitute for it.

The statement of record is ``docs/infra-runbook.md`` §4.1.

**Within what budget, and where the budget comes from.** Pi's ``mcp-client``
connects **eagerly at session start**: one attempt per eager server, a hard
15 000 ms per-server budget and **no retry** (``DEFAULT_CONNECTION_TIMEOUT_MS =
15000``, ``~/.pi/agent/extensions/mcp-client/index.ts``; recorded in
``docs/infra-runbook.md`` §6.11). 15 s is therefore the wall-clock envelope in
which the hosted service must be reachable and answering for a client to start
at all — the client's own startup requirement, not a round number chosen to
pass. ``/health`` is the liveness surface whose stall is the *same* held
event-loop that fails that eager connect (the #2924 symptom — "/health
intermittently hangs >8s" means the loop was held, and the client's first
request times out inside the same 15 s). The observable deadline is that
client budget.

*Stated plainly, because the derivation is indirect and must not read as a
claim it is not:* the client's eager request targets ``/mcp``, not ``/health``.
``/health`` is the surface that reports whether the process is answerable at
all, so a ``/health`` response slower than the client's single attempt is, by
construction, a client-visible startup failure.

**How this differs from ``tests/test_health_ready_nonblocking.py``.** That file
pins *nonblocking structure* (AST pins on the handler/dispatch shape) and proves
the loop stays responsive **in-process**, under deliberately wedged probes. It
never starts the service, never issues a request over a socket, and names no
client-visible budget (its ``READY_WORST_CASE_BUDGET_S`` is a policy ceiling for
``/health/ready``, a different endpoint). This file is the complement the issue
asks for: **the resolved behaviour, executed over a real port against the real
handler** — status, body shape, and latency measured as a client measures it.

Mutation (the non-vacuousness proof): reintroduce the #2924/#2988 shape — make
``health`` call the probe inline instead of reading ``_HEALTH_PROBE.snapshot()``
— and the stalled-probe test reds with a client-side timeout. A guard that stays
green when the defect returns is a false PASS (LANE-DIRECTIVE §8).
"""
from __future__ import annotations

import contextlib
import threading
import time

#: The client's OWN startup deadline (see the module docstring). Derived from
#: Pi ``mcp-client``'s ``DEFAULT_CONNECTION_TIMEOUT_MS = 15000`` with NO retry;
#: recorded at ``docs/infra-runbook.md`` §6.11 and stated as the contract at
#: §4.1. Not chosen here.
CLIENT_STARTUP_CONNECT_BUDGET_S = 15.0

#: How long the fake data-plane probe stays wedged. Longer than the client's
#: whole startup budget, so a handler that waits for it cannot answer in time.
STALL_S = CLIENT_STARTUP_CONNECT_BUDGET_S + 5.0


@contextlib.contextmanager
def _live_hosted_service(monkeypatch, *, probe_interval_s: float = 3600.0):
    """Run the REAL hosted app on a REAL port (uvicorn, ephemeral) and yield
    its base URL.

    The real ``hosted_api.app`` with its real lifespan is used deliberately:
    the resolved status/body/latency must be the ones production serves, not a
    synthetic app's. ``probe_interval_s`` is patched low only where a test needs
    the background refresher to reach the injected probe promptly.
    """
    import uvicorn

    import tortoise.hosted_api as ha

    monkeypatch.setattr(ha, "_health_probe_interval", lambda: probe_interval_s)
    ha._HEALTH_PROBE.reset()

    server = uvicorn.Server(
        uvicorn.Config(ha.app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 60.0
        while not (server.started and server.servers) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "the hosted service never started"
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        ha._HEALTH_PROBE.reset()


def _get_health(base_url: str, *, timeout: float):
    """Issue the real request with the CLIENT's own timeout.

    The timeout is the client budget on purpose: an over-budget response must
    surface exactly as the client would experience it — a timeout — not as a
    slow-but-successful 200 that a laxer assertion would wave through.
    """
    import httpx

    with httpx.Client(timeout=timeout) as client:
        started = time.monotonic()
        response = client.get(f"{base_url}/health")
        elapsed = time.monotonic() - started
    return response, elapsed


def _assert_client_visible_effect(response, elapsed: float) -> None:
    """The state a client must observe: a 200 whose body carries the shape the
    deploy gate and dashboards read, inside the client's startup budget."""
    assert response.status_code == 200, (
        f"/health answered {response.status_code} — a client starting against "
        "the hosted service must observe 200 + the body shape below. A non-200 "
        "here is either the outermost WaitBoundMiddleware's 504 refusal (the "
        "request did not complete inside its 10s wait bound, "
        "mcp_auth._TRANSPORT_WAIT_BOUND_S) or a client timeout: both are "
        "client-visible startup failures, and the 504 refusal is *inside* the "
        "client's 15s budget rather than a pass for it (#3811, #4412)")
    body = response.json()
    assert body.get("status") in ("ok", "degraded"), (
        f"/health body carries status={body.get('status')!r}; a client reads "
        "only 'ok' | 'degraded'")
    db = body.get("db")
    assert isinstance(db, dict), f"/health body has no db object: {body!r}"
    assert isinstance(db.get("ok"), bool), (
        f"/health db.ok is {db.get('ok')!r}, not a bool — the deploy gate reads "
        "this field by value (#4470)")
    assert elapsed < CLIENT_STARTUP_CONNECT_BUDGET_S, (
        f"/health answered in {elapsed:.2f}s, past the client's own "
        f"{CLIENT_STARTUP_CONNECT_BUDGET_S:.0f}s eager-startup budget "
        "(Pi mcp-client, one attempt, no retry — docs/infra-runbook.md §6.11)")


def test_client_observes_a_fast_200_on_health_over_a_real_socket(monkeypatch):
    """The plain path: start the service, issue the real request, observe.

    This is the assertion #2924 lacked — what a client must *observe*, not what
    the handler's source says.
    """
    with _live_hosted_service(monkeypatch) as base_url:
        response, elapsed = _get_health(
            base_url, timeout=CLIENT_STARTUP_CONNECT_BUDGET_S)
    _assert_client_visible_effect(response, elapsed)


def test_health_answers_inside_the_client_budget_while_the_db_probe_is_stalled(
        monkeypatch):
    """The decisive one: a stalled data-plane probe must NOT be inherited.

    #2924's mechanism is exactly this coupling — ``/health`` shared fate with a
    FalkorDB round trip, so a multi-second DB stall became a multi-second (or
    unbounded) liveness response. With the probe wedged for longer than the
    client's whole startup budget, the endpoint must still answer 200 from
    memory, *while the probe is provably still stalled*.
    """
    import tortoise.hosted_api as ha

    release = threading.Event()
    entered = threading.Event()

    def _stalled_probe():
        entered.set()
        release.wait(STALL_S + 45.0)
        return {"ok": False, "latency_ms": 0.0, "error": "stalled"}

    monkeypatch.setattr(ha, "_probe_db", _stalled_probe)

    # A short refresher interval so the wedge is entered promptly; the handler
    # must not depend on it either way. ``release`` is set inside the service
    # context so a failed request unblocks the injected probe BEFORE shutdown
    # waits on it (otherwise a mutated handler holds the loop and the join
    # burns its whole timeout).
    with _live_hosted_service(monkeypatch, probe_interval_s=0.05) as base_url:
        try:
            deadline = time.monotonic() + 10.0
            while not entered.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert entered.is_set(), (
                "the injected probe never ran — the assertion below would be "
                "vacuous (nothing was stalled to inherit)")

            response, elapsed = _get_health(
                base_url, timeout=CLIENT_STARTUP_CONNECT_BUDGET_S)

            assert not release.is_set(), (
                "the stalled probe completed before /health answered — this run "
                "cannot distinguish 'read from memory' from 'waited it out'")
        finally:
            release.set()

    _assert_client_visible_effect(response, elapsed)
