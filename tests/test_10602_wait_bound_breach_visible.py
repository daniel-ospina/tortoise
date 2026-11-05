"""#10602 — a transport wait-bound breach must be OBSERVABLE in-process.

`#10602` is the P0 where the hosted graph could not answer a search: 6/6
single-agent `tortoise_search` calls came back as

    HTTP 504  {"error": {"code": -32009, "message": "The server's wait budget
               for this request was exceeded before a response was ready"}}

Measured on 2026-11-05, that fault no longer reproduces at the transport or the
retrieval layer (the degraded read tier's per-reader whole-corpus build is gone,
#10752). What this file pins is the OTHER half of the issue, which was still
open: **the refusal class had no channel the product's own health surface could
read.**

`-32009` breaches were emitted to exactly ONE place — `hosted_api.
_emit_wait_bound_breach`, a best-effort write to the external analytics sink on
the telemetry pool, swallowed at `logger.debug` on any failure. So:

* `tortoise_health` answered `status: "ok"` at 2026-11-02T15:44Z while every
  search was being refused, and finding the P0 required an agent to
  hand-measure six sequential calls (`#10602`'s own body).
* With no analytics sink reachable, a breach left no in-process trace at all.

The five properties below are the ones a later reader is most likely to break:

1. the channel is on the health surface, at zero, when nothing is wrong —
   a MISSING key and a measured ZERO must not read the same (`#3253`'s rule);
2. a breach moves it;
3. the REST arm counts one breach per refused request (not per return path);
4. the count does NOT depend on the telemetry pool accepting the emit — the
   pool's bounded backlog drops jobs under exactly the overload that causes
   breaches, so a counter riding inside `_emit` would be an inert enforcer;
5. it is ADDITIVE: it never rewrites `status`, because `degraded` stays
   reserved for an observed probe FAILURE (the recorded decision at
   `monitoring.metrics`), and a slow-but-reachable store must not flip it.
"""

from __future__ import annotations

import asyncio

import pytest
from test_transport_wait_bound import _drive, _scope, _slow_app

from tortoise import hosted_api as ha
from tortoise import mcp_server, monitoring


def _counts() -> dict:
    return monitoring.wait_bound_breach_counts()


def _by(counts: dict, surface: str) -> int:
    """Per-surface count, tolerating a revision that dropped the zero-padding —
    so a DELTA assertion fails for its own reason rather than borrowing #1's."""
    return counts["by_surface"].get(surface, 0)


@pytest.fixture
def baseline():
    """The families are PROCESS-global Prometheus counters, so every assertion
    below is a DELTA rather than an absolute — test order must not matter."""
    return _counts()


# ── 1: present at zero, on the health surface ─────────────────────────────

def test_health_reports_the_breach_channel_at_zero():
    """A caller must be able to tell "no breach was measured" from "this build
    cannot see breaches". An absent key reads as the second, silently."""
    report = monitoring.metrics()
    assert "wait_bound_breaches" in report, (
        "#10602: the health surface must carry the wait-bound breach channel — "
        "without it a breach is visible only in the external analytics sink")

    channel = report["wait_bound_breaches"]
    assert set(channel) == {"total", "by_surface", "error"}
    # Both surfaces are always present, at 0, for the same reason.
    assert set(channel["by_surface"]) == {"mcp", "rest"}
    assert channel["total"] == sum(channel["by_surface"].values())
    # `None` means MEASURED. A failed read must not masquerade as a clean zero.
    assert channel["error"] is None


# ── 2: a breach moves it ──────────────────────────────────────────────────

def test_a_breach_moves_the_counter(baseline):
    monitoring.record_wait_bound_breach("mcp")
    monitoring.record_wait_bound_breach("rest")
    monitoring.record_wait_bound_breach("rest")

    now = _counts()
    assert now["total"] - baseline["total"] == 3
    assert _by(now, "mcp") - _by(baseline, "mcp") == 1
    assert _by(now, "rest") - _by(baseline, "rest") == 2
    # …and it reaches the health payload a caller actually reads.
    assert monitoring.metrics()["wait_bound_breaches"]["total"] == now["total"]


# ── 3: the REST arm counts a real breach, once ────────────────────────────

@pytest.mark.asyncio
async def test_rest_breach_is_counted_once_per_refused_request(
        baseline, monkeypatch):
    """Driven through the REAL middleware, not the two call sites that return a
    refusal — the MCP seam has three refusal returns and only one is the
    truthful emit (the other two defer to the middleware's event). One refused
    request is one breach."""
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)
    mw = ha.WaitBoundMiddleware(_slow_app(5.0))

    rec = await _drive(mw, _scope())

    assert rec.status == 504
    now = _counts()
    assert now["total"] - baseline["total"] == 1
    assert _by(now, "rest") - _by(baseline, "rest") == 1


@pytest.mark.asyncio
async def test_a_request_that_answers_inside_the_bound_is_not_a_breach(
        baseline, monkeypatch):
    """The other direction: a served request — including one that legitimately
    answers with NOTHING — must not move the counter. A search returning an
    empty list is an ANSWER, never a failure (`#10602`'s own distinction)."""
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)
    mw = ha.WaitBoundMiddleware(_slow_app(0.0))

    rec = await _drive(mw, _scope())

    assert rec.status == 200
    assert _counts()["total"] == baseline["total"]


# ── 4: not dependent on the best-effort telemetry pool ────────────────────

def test_mcp_breach_is_counted_when_the_telemetry_pool_refuses_the_job(
        baseline, monkeypatch):
    """THE INERT-ENFORCER GUARD.

    `control_plane_worker("telemetry")` has a BOUNDED backlog and returns a
    pre-failed future when full — and the overload that fills it is the same
    overload that causes breaches. A counter incremented inside the submitted
    `_emit` closure would therefore under-count precisely when the number
    matters, i.e. it would look like an observation and behave like a no-op.
    """
    def _refuse(_kind):
        raise RuntimeError("#10602 test: telemetry backlog full")

    monkeypatch.setattr(monitoring, "control_plane_worker", _refuse)

    mcp_server._emit_mcp_wait_bound_breach_off_loop("org-test", 12_000,
                                                   "tortoise_search")

    now = _counts()
    assert now["total"] - baseline["total"] == 1, (
        "the breach must be counted on the calling thread, BEFORE the emit is "
        "scheduled — a refused telemetry job must not swallow the count")
    assert _by(now, "mcp") - _by(baseline, "mcp") == 1


# ── 5: additive, never a verdict ─────────────────────────────────────────

def test_the_breach_channel_never_rewrites_the_status_verdict(baseline, monkeypatch):
    """`degraded` is reserved for an OBSERVED probe failure — a recorded
    decision (`monitoring.metrics`), and the reason a slow-but-reachable store
    does not flip `/health`. This channel is additive; deriving `status` from it
    would silently reverse that decision.

    The status must be able to take a value OTHER than ``unknown`` for this to
    prove anything (with no SDK it is pinned at ``unknown`` and the assertion
    would pass for the wrong reason — mutation-verified: a mutant that derives
    status from this counter left the test green until this fixture was added).
    """
    monkeypatch.setattr(
        monitoring, "probe_db",
        lambda *_a, **_kw: {"ok": True, "observed": True,
                            "latency_ms": 1.0, "error": None})

    before = monitoring.metrics(sdk=object())["status"]
    assert before == "ok", "the fixture must reach a status a breach COULD flip"

    monitoring.record_wait_bound_breach("mcp")
    after = monitoring.metrics(sdk=object())

    assert after["status"] == "ok", (
        "a breach is an observation, never a probe failure — deriving `degraded` "
        "from it reverses the recorded decision that reserves `degraded`")
    assert after["wait_bound_breaches"]["total"] > baseline["total"]


# ── 6: the ORIGINAL false-`ok` surface carries it too ────────────────────

@pytest.mark.asyncio
async def test_hosted_health_endpoint_carries_the_breach_channel(baseline, monkeypatch):
    """The endpoint an operator curls FIRST, and the exact surface that read
    `status: "ok"` at 2026-11-02T15:44Z while 6/6 searches were refused.

    The hosted app builds its OWN `/health` dict and does NOT route through
    `monitoring.metrics()` (`tortoise/code_freshness.py` states the same scope
    for `server_code`), so a channel present only on the MCP tool would leave
    the operator's first query showing nothing — "this build cannot see
    breaches" when the true answer is "27 breaches since start".

    Same rule as `change_feed` and `build`: REPORTED, never folded into
    `status` (see `test_the_breach_channel_never_rewrites_the_status_verdict`).
    """
    # Hold all three inputs of `status` green, so it can reach a value a breach
    # COULD flip (`ok`). Without this the assertion below reads `degraded` in
    # this environment for reasons unrelated to the change, and a mutant that
    # folds the counter into `status` would ship green — measured.
    monkeypatch.setattr(ha._HEALTH_PROBE, "snapshot",
                        lambda: {"ok": True, "observed": True,
                                 "latency_ms": 1.0, "error": None})
    monkeypatch.setattr(ha, "_backup_watcher_health",
                        lambda: {"state": "running", "ok": True,
                                 "error": None, "expected": True})
    monkeypatch.setattr(ha, "_record_health",
                        lambda: {"ok": True, "degraded": False})

    before = await ha.health()
    assert before["status"] == "ok", (
        "the fixture must reach a status a breach COULD flip, or the assertion "
        "below is a tautology")
    assert before["wait_bound_breaches"]["total"] == baseline["total"]

    monitoring.record_wait_bound_breach("mcp")

    body = await ha.health()

    assert "wait_bound_breaches" in body, (
        "the hosted /health body must carry the breach channel — it is the "
        "surface that reported a misleading `ok` during #10602")
    assert set(body["wait_bound_breaches"]["by_surface"]) == {"mcp", "rest"}
    assert body["wait_bound_breaches"]["total"] - baseline["total"] == 1
    # Reported, never folded: a cumulative counter must not pin the deployment
    # degraded for the life of the process (the #10185 failure this forbids).
    assert body["status"] == "ok", (
        "a breach is an observation, never a probe failure — folding it into "
        "`status` reverses the recorded decision and re-creates #10185")


# ── 7: a `/mcp` pre-SSE stall is an MCP breach, not a REST one ────────────

@pytest.mark.asyncio
async def test_a_mcp_pre_sse_stall_is_counted_as_the_mcp_surface(
        baseline, monkeypatch):
    """THE LABEL MUST NAME THE SURFACE, NOT THE DETECTOR.

    `WaitBoundMiddleware` is app-wide and owns the `/mcp` refusal for a request
    that stalls BEFORE its SSE response begins, serving it with the JSON-RPC
    `-32009` body — the exact shape #10602 recorded. Labelling by ARM reports
    that refusal as `rest`, so a recurrence reads `{"mcp": 0, "rest": 27}` while
    MCP tool calls are the ones failing, and sends the operator to the REST
    routes. `total` stays right either way; `by_surface` is the whole feature.

    Mutation-verified: labelling the middleware arm `"rest"` unconditionally
    (the pre-review shape) fails this test and nothing else.
    """
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)
    mw = ha.WaitBoundMiddleware(_slow_app(5.0))

    rec = await _drive(mw, _scope("/mcp/", method="POST"))

    assert rec.status == 504
    # …and it really is the JSON-RPC `-32009` body, i.e. the #10602 shape.
    from tortoise.mcp_auth import ERR_TIMEOUT
    assert rec.json["error"]["code"] == ERR_TIMEOUT

    now = _counts()
    assert now["total"] - baseline["total"] == 1
    assert _by(now, "mcp") - _by(baseline, "mcp") == 1, (
        "a pre-SSE `/mcp` stall must be attributed to the MCP surface")
    assert _by(now, "rest") - _by(baseline, "rest") == 0


# ── 8: exactly-once across the MCP surface ──────────────────────────────

def test_the_mcp_module_has_exactly_one_breach_counting_site():
    """One refused MCP request is ONE count, whichever detector saw it.

    The seam has THREE refusal returns: the entry short-circuit and the
    `_take_graph_argument` arm both pass `dispatched=False` because the
    transport already answered AND already counted (as `mcp`), and only the
    main breach path counts. Adding a second `record_wait_bound_breach` call to
    either short-circuit double-counts one refused request — the source reports
    it as two breaches, in a counter intended to drive a capacity decision.

    A behavioural test would need to drive the whole MCP dispatch seam; this
    pins the invariant at its cause instead, and it is the only guard that
    survives a future edit adding a call site. Mutation-verified: adding
    `monitoring.record_wait_bound_breach("mcp")` to the `transport_refused`
    short-circuit fails here (and, at the time of writing, reddened no other
    test in the 232-test related population).
    """
    import pathlib

    source = pathlib.Path(mcp_server.__file__).read_text(encoding="utf-8")
    sites = source.count("record_wait_bound_breach(")
    assert sites == 1, (
        f"tortoise/mcp_server.py has {sites} breach-counting call sites; the "
        "seam must have exactly one, on the main breach path. A second one on "
        "a `dispatched=False` return double-counts a refused request")


# ── 9: the read is part of a "never 5xx" contract ───────────────────────

def test_an_unreadable_counter_is_reported_and_never_raises(baseline,
                                                            monkeypatch):
    """`hosted_api.health()` promises it never 5xxes, and wraps every other read
    for that reason (#10648 review P2, #2850). This channel is read on that
    endpoint, so it must not be the one read that can throw: a raise here turns
    the change that makes an outage visible into a 500 on the endpoint Fly
    probes.

    …and the failure must not be reported as a clean `0` either — that is the
    same false reassurance as an absent key. `error` carries it.
    """
    def _boom(_metric, _label, **_kw):
        raise RuntimeError("#10602 test: metric family unreadable")

    from tortoise import monitoring as _m
    monkeypatch.setattr(_m, "_collect_by_label", _boom)

    channel = _m.wait_bound_breach_counts()      # must not raise

    assert channel["error"], (
        "a failed read must be reported in `error`, never as a bare 0")
    assert "RuntimeError" in channel["error"]
    # The shape survives the failure, so a caller need not branch on it.
    assert set(channel["by_surface"]) == {"mcp", "rest"}
    assert channel["total"] == 0

    # And the endpoint that promises never to 5xx still answers.
    body = asyncio.run(ha.health())
    assert body["wait_bound_breaches"]["error"]
