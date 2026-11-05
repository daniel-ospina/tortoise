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

    ⛔ COUNTED OVER THE AST, NOT THE RAW SOURCE (#11054 review P2). A substring
    count also matches the string inside a docstring or a comment, so an
    explanatory sentence naming the call would red this test for prose alone —
    a false block on the very comment that documents the invariant. Counting
    `ast.Call` nodes whose callee is `…record_wait_bound_breach` counts CALLS.

    ⛔ AND IT MUST MATCH BOTH SPELLINGS (#11054 review P3, cycle 4). Matching only
    `node.func.attr` counts `monitoring.record_wait_bound_breach(...)` and MISSES
    the bare-name form `record_wait_bound_breach(...)` that follows a
    `from tortoise.monitoring import record_wait_bound_breach` — there `func` is
    an `ast.Name` and `attr` is `None`. A second real call on a `dispatched=False`
    return, spelled that way, would double-count a refused request with this test
    green — precisely the arm the behavioural test cannot reach.
    """
    import ast
    import pathlib

    def _is_breach_call(node: ast.Call) -> bool:
        """Both spellings: `mod.record_wait_bound_breach(…)` and the bare name."""
        return (
            getattr(node.func, "attr", None) == "record_wait_bound_breach"
            or getattr(node.func, "id", None) == "record_wait_bound_breach")

    tree = ast.parse(
        pathlib.Path(mcp_server.__file__).read_text(encoding="utf-8"))
    sites = sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_breach_call(node))
    assert sites == 1, (
        f"tortoise/mcp_server.py has {sites} breach-counting call sites; the "
        "seam must have exactly one, on the main breach path. A second one on "
        "a `dispatched=False` return double-counts a refused request")


@pytest.mark.asyncio
async def test_a_breach_is_counted_once_whichever_detector_fires_first(
        baseline, monkeypatch):
    """ONE refused request is ONE count — in BOTH detector orders.

    `_WAIT_BOUND_REFUSED_STATE` is one-directional: it suppresses the seam when
    the MIDDLEWARE refused first. It says nothing about the reverse order, and
    that order is reachable — on a transport that does not start its response
    before dispatching (fastmcp's `json_response` mode) the seam's deadline has
    not collapsed, so the SEAM can refuse and count first, and the middleware's
    own bound then expires later in the SAME request.

    Without the claim bit that is two counts for one refusal, in a counter that
    drives a capacity decision. This drives the REAL seam counting helper and
    the REAL middleware over one shared `scope["state"]` dict, then asserts the
    delta is exactly 1 — the assertion the source-count test structurally
    cannot make.

    Mutation-verified: delete the `_claim_wait_bound_count()` guard and this is
    the only test in the file that reds.
    """
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)

    # In production the seam resolves this dict from FastMCP's request
    # ContextVar — set only inside a real MCP request — and Starlette's `Mount`
    # forwards the SAME mapping the middleware wrote. Bind the helper to the
    # scope's own dict so the seam and the middleware share state here exactly
    # as they do behind a live transport; nothing else about the seam is stubbed.
    shared_state: dict = {}
    monkeypatch.setattr(mcp_server, "_wait_bound_state", lambda: shared_state)

    async def seam_shaped_app(scope, receive, send):
        # The seam refused first: it claims the count and records it, exactly as
        # `_emit_mcp_wait_bound_breach_off_loop` does on the breach path.
        mcp_server._emit_mcp_wait_bound_breach_off_loop("org-1", 1, "probe")
        # …then it outlives the transport bound, so the MIDDLEWARE's own
        # deadline expires for the same request.
        await asyncio.sleep(5.0)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    mw = ha.WaitBoundMiddleware(seam_shaped_app)
    scope = _scope("/mcp/", method="POST")
    # Starlette supplies this dict on a real request; both detectors share it.
    scope["state"] = shared_state

    rec = await _drive(mw, scope)
    assert rec.status == 504, "the middleware must still refuse the stalled /mcp request"

    now = _counts()
    assert now["total"] - baseline["total"] == 1, (
        "one refused /mcp request must move the counter by exactly one even "
        "when BOTH detectors observe it")
    assert _by(now, "mcp") - _by(baseline, "mcp") == 1
    assert _by(now, "rest") - _by(baseline, "rest") == 0


# ── 9: the read is part of a "never 5xx" contract ───────────────────────

def test_an_unreadable_counter_is_reported_and_never_raises(baseline,
                                                            monkeypatch):
    """`hosted_api.health()` promises it never 5xxes, and wraps every other read
    for that reason (#10648 review P2, #2850). This channel is read on that
    endpoint, so it must not be the one read that can throw: a raise here turns
    the change that makes an outage visible into a 500 on the endpoint Fly
    probes.

    …and the failure must not be reported as a clean `0` either — that is the
    same false reassurance as an absent key. `error` carries it, and BOTH
    numeric fields abstain rather than padding to zero.
    """
    def _boom(_metric, _label, **_kw):
        raise RuntimeError("#10602 test: metric family unreadable")

    from tortoise import monitoring as _m
    monkeypatch.setattr(_m, "_collect_by_label", _boom)

    channel = _m.wait_bound_breach_counts()      # must not raise

    assert channel["error"], (
        "a failed read must be reported in `error`, never as a bare 0")
    assert "RuntimeError" in channel["error"]
    # ⛔ None, NOT 0. The failure shape is the None-not-zero rule this SAME
    # /health payload records for its `record` counters (#9548 review P2): "an
    # unread counter and a zero are different facts, and the obvious consumer
    # (`counts.get("write_failed", 0)`) would read the second from the first".
    # Padding the failure to 0 hands a `total`-keyed scrape or alert rule a
    # healthy reading out of a failed read — only a reader that happens to
    # check the sibling `error` is safe, and an alert rule cannot. Mutation-
    # verified: returning the padded zeros below fails here and nowhere else.
    assert channel["total"] is None, (
        "a failed read must not report `total: 0` — 0 is a MEASURED zero")
    assert channel["by_surface"] is None, (
        "a failed read must not report padded per-surface zeros either")

    # And the endpoint that promises never to 5xx still answers.
    body = asyncio.run(ha.health())
    assert body["wait_bound_breaches"]["error"]
    assert body["wait_bound_breaches"]["total"] is None


# ── 10: the bare family never reads "this build cannot see breaches" ─────

def test_the_metrics_exposition_carries_both_surfaces_at_zero():
    """`/metrics` must show a ZERO for each surface before any breach.

    The JSON body pads `by_surface` to both keys (#3253's rule), which is why
    test 1 can tell "no breach measured" from "this build cannot see
    breaches". A labelled `Counter` child is created LAZILY, on its first
    `.labels()`, so the exposition the runbook also points operators at
    (`prometheus_client.generate_latest()`, which `serve_health` serves) carried
    only the family's HELP/TYPE lines until a first refusal happened — the same
    absent-vs-zero ambiguity, on the other surface. Measured pre-fix:

        ``tortoise_wait_bound_breaches_total`` appears with NO `{surface=…}`
        sample at all in a fresh interpreter.

    ⛔ A FRESH PROCESS, on purpose: this file's own tests increment both
    children, and the counters are PROCESS-global, so an in-process assertion
    would pass on the unfixed code whenever an earlier test ran first — green
    for the wrong reason, which is exactly what a vacuous pin looks like here.
    """
    import subprocess
    import sys

    script = (
        "from prometheus_client import generate_latest\n"
        "import tortoise.monitoring\n"
        "print(generate_latest().decode(), end='')\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-2000:]

    series = [line for line in proc.stdout.splitlines()
              if line.startswith("tortoise_wait_bound_breaches_total{")]
    assert set(series) == {
        'tortoise_wait_bound_breaches_total{surface="mcp"} 0.0',
        'tortoise_wait_bound_breaches_total{surface="rest"} 0.0',
    }, (
        "the exposition must declare BOTH bounded surface children at import, "
        "so a scrape before the first refusal reads 0 (measured) rather than "
        "absent (unreadable): " f"{series}")


# ── 11: a failing counter must never eat the legible refusal ─────────────

@pytest.mark.asyncio
async def test_a_failing_counter_write_does_not_replace_the_refusal(
        baseline, monkeypatch):
    """Measurement must never be a NEW failure mode for the request it measures.

    Every other monitoring write on a request path in `hosted_api.py` is wrapped
    with exactly that rule (`record_egress`, `record_compute`). This channel's
    write sits on the REFUSAL path, AFTER the refusal decision and BEFORE the
    send — so an exception escaping it is not merely a lost measurement: it
    skips `_track_wait_bound_request` and `_send_wait_bound_refusal` and hands
    the client an opaque 500 with no `Retry-After` and no `-32009` body. The
    observation would destroy the legible refusal it exists to expose, which is
    the precise failure mode #3834/#10602 exist to remove.

    Mutation-verified: deleting the `try/except` around the middleware's
    `record_wait_bound_breach` call makes this the only failing test in the
    file — the refusal comes back as a raised `RuntimeError` instead of a 504.
    """
    def _boom(_surface):
        raise RuntimeError("#10602 test: counter write failed")

    monkeypatch.setattr(monitoring, "record_wait_bound_breach", _boom)
    monkeypatch.setattr(ha._monitoring, "record_wait_bound_breach", _boom)
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)
    mw = ha.WaitBoundMiddleware(_slow_app(5.0))

    rec = await _drive(mw, _scope("/mcp/", method="POST"))

    assert rec.status == 504, (
        "a failing counter write must not turn the wait-bound refusal into an "
        "opaque 500 — the client still gets the legible refusal")
    from tortoise.mcp_auth import ERR_TIMEOUT
    assert rec.json["error"]["code"] == ERR_TIMEOUT, (
        "the JSON-RPC `-32009` body is the product on this path; a counter "
        "that can suppress it re-creates the illegible outcome of #10602")


def test_the_mcp_count_write_is_guarded(monkeypatch):
    """The MCP arm's counting write is on the refusal path TOO.

    `_emit_mcp_wait_bound_breach_off_loop` runs BEFORE `_wait_bound_refusal_
    result` builds its `-32009` body, so an unguarded raise there replaces a
    legible tool refusal with a generic error. Asserted structurally because
    the seam's refusal return is only reachable through the whole MCP dispatch
    path; the behavioural half is pinned above for the transport arm, which
    shares the rule.
    """
    def _boom(_surface):
        raise RuntimeError("#10602 test: counter write failed")

    monkeypatch.setattr(monitoring, "record_wait_bound_breach", _boom)

    # Must not raise: the emit is scheduled after the count, so a surviving
    # call proves the count did not abort it.
    mcp_server._emit_mcp_wait_bound_breach_off_loop("org", 1250, "tortoise_search")


def test_an_unknown_surface_is_refused_rather_than_counted():
    """#10602: the label set is BOUNDED, and the writer is what enforces it.

    The import-time child initialisation is only legal because `surface` has two
    literal values, and the health report zero-pads exactly those two. So a
    writer that minted a third label would be counted while `/health` kept
    padding the two it knows — a correct `total` beside a per-surface map that
    silently omits the counted surface, which is the absent-vs-zero reading this
    whole channel exists to remove, reproduced one layer down.

    The refusal is safe by construction: both call sites invoke the writer
    inside `try/except` (pinned by `test_a_failing_counter_write_does_not_
    replace_the_refusal` and `test_the_mcp_count_write_is_guarded`).
    """
    import pytest

    for bad in ("rest2", "", "MCP", None):
        with pytest.raises(ValueError):
            monitoring.record_wait_bound_breach(bad)

    # …and the two the channel knows still count, so the guard is not a blanket
    # refusal that would silently zero the channel.
    monitoring.record_wait_bound_breach(monitoring.WAIT_BOUND_SURFACE_MCP)
    monitoring.record_wait_bound_breach(monitoring.WAIT_BOUND_SURFACE_REST)

    # The zero-padding set and the constants the writers import are the SAME
    # objects, so a new surface cannot be added to one without the other.
    assert set(monitoring._WAIT_BOUND_SURFACES) == {
        monitoring.WAIT_BOUND_SURFACE_MCP,
        monitoring.WAIT_BOUND_SURFACE_REST,
    }
    counts = monitoring.wait_bound_breach_counts()
    assert set(counts["by_surface"]) == set(monitoring._WAIT_BOUND_SURFACES), (
        "the health report must pad exactly the surfaces the writer accepts")
