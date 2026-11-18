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

    # ⛔ #12477 review: reach the state a fold COULD flip. One breach is below
    # the refusal threshold, so with a single one this test would pass whether
    # or not the verdict folds the window in — a tautology dressed as a gate.
    for _ in range(10):
        monitoring.record_wait_bound_attempt("mcp")
        monitoring.record_wait_bound_breach("mcp")
    assert monitoring.wait_bound_breach_window()["refusing"] is True, (
        "the fixture must reach the refusing state, or `status: ok` is not "
        "evidence that the fold is absent")
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

    # ⛔ Enough to cross the refusal threshold (see the sibling test): the
    # decision this pins is "a REFUSING plane still reads `status: ok` here",
    # and a single breach cannot express it.
    for _ in range(10):
        monitoring.record_wait_bound_attempt("mcp")
        monitoring.record_wait_bound_breach("mcp")
    assert monitoring.wait_bound_breach_window()["refusing"] is True

    body = await ha.health()

    assert "wait_bound_breaches" in body, (
        "the hosted /health body must carry the breach channel — it is the "
        "surface that reported a misleading `ok` during #10602")
    assert set(body["wait_bound_breaches"]["by_surface"]) == {"mcp", "rest"}
    assert body["wait_bound_breaches"]["total"] - baseline["total"] >= 10
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


# ══ the OTHER outcome of the same breach: SERVED, not refused ════════════
#
# The channel above counts requests the bound REFUSED. A breach has a second
# outcome the refusal path cannot see: NO refusal is substituted and the caller
# is served the handler's OWN answer after the bound, with no response HEAD
# inside that bound (a head that missed it IS counted). (The timer may have fired and lost the race to a finished handler,
# or never have fired — the counter reports the OUTCOME, not which callback ran.)
#
# Measured on the live deployment 2026-11-17 (sha `55ec2c71b165`): a caller was
# answered a **401 at 20.27 s against the 10 s bound**, and
# `wait_bound_breaches.total` did not move. Two SEPARATE blind spots:
#
#   * the counter: it is written only on the refusal path, and a return from
#     `mcp_auth.await_under_wait_bound` is not a breach decision point at all, so
#     nothing incremented it (it takes no status — nothing filtered it); and
#   * the refusal SHARE: a 401 is in `_WAIT_BOUND_NON_DATA_STATUSES`, so it is
#     not even in the attempt denominator (by design — #12477 review round 5, an
#     unauthenticated client must not be able to dilute the ratio).
#
# So `/health` — the field §4.1.1 of the runbook tells an operator to read for
# "is the bound being honoured?" — reported a clean zero over a request that
# breached it by 2x. That is a false PASS of the instrument on the product's own
# promise, which is why the answer has its own cumulative channel rather than
# being folded into the refusal counter (`total` means "refused", and the
# distinction IS the diagnosis).
#
# ⛔ The three things a later reader is most likely to break, each pinned below:
# it is counted when it happened; it is NOT counted when it did not; and it
# never feeds the #12477 window (whose verdict 503s a deployment).


def _starved() -> dict:
    return monitoring.wait_bound_starved_counts()


def _blocking_app(block_s: float, status: int = 401):
    """An ASGI app that holds the EVENT LOOP (not merely awaits) and then
    answers — the #10845 / #4625 shape, which `_slow_app`'s `asyncio.sleep` does
    NOT reproduce (that yields, so the timer fires and a refusal goes out)."""
    import time as _time

    async def app(scope, receive, send):
        _time.sleep(block_s)
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"detail":"no"}'})

    return app


@pytest.mark.asyncio
async def test_a_starved_answer_is_counted_although_nothing_was_refused(
        baseline, monkeypatch):
    """THE REPRODUCTION, as a pin.

    A 0.05 s bound, a handler that blocks the loop 0.4 s, and a 401 answer: the
    caller waits 8x the bound, is served rather than refused, and — before this
    channel existed — every observable said nothing happened. Both halves are
    asserted: the answer still goes out (the fix must never convert it into a
    late refusal), and the breach is now counted.
    """
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)
    starved_before = _starved()
    # ⛔ A SPY, not a before/after read of the window: `total`/`buckets` are
    # derived from buckets that AGE OUT (120 s window, 10 s buckets), so an
    # equality on them can fail with no breach recorded whenever an earlier
    # test's bucket crosses a bucket boundary during this test's ~0.4 s drive.
    # The invariant is "the starved write never stamps the window", which is
    # exactly what calling this writer would mean.
    stamped = []
    monkeypatch.setattr(monitoring, "record_wait_bound_breach_recent",
                        lambda *a, **kw: stamped.append(a))

    mw = ha.WaitBoundMiddleware(_blocking_app(0.4, status=401))
    rec = await _drive(mw, _scope("/v1/thing"))

    assert rec.status == 401, (
        "the handler's own answer must still be served: refusing a computed "
        "401 after the fact would hand the client a Retry-After for an auth "
        "failure it must not retry")
    now = _starved()
    assert now["total"] - starved_before["total"] == 1, (
        "a request answered 0.4 s into a 0.05 s bound was counted nowhere — "
        "this is the 401-at-20.27s shape")
    assert _by(now, "rest") - _by(starved_before, "rest") == 1
    # …and it is NOT a refusal: the sibling channel must stay where it was.
    assert _counts()["total"] == baseline["total"], (
        "an ANSWERED breach is not a refusal; folding it into the refusal "
        "counter makes `total` untruthful in exactly the incident where the "
        "distinction is the diagnosis")
    # ⛔ and it must not reach the verdict: #12477's window is a refusal SHARE,
    # and its verdict 503s a deployment (widen it and a blocked-loop episode
    # becomes a self-inflicted outage, the false-red #12477 measured twice).
    assert stamped == [], (
        "the served-late write stamped the #12477 refusal window — a blocked "
        "loop would then 503 the deployment it just slowed down")


@pytest.mark.asyncio
async def test_a_request_answered_inside_the_bound_is_not_starved(
        baseline, monkeypatch):
    """The false-positive half. If the ordinary request counted, the channel
    would report a permanent breach and an operator would learn to ignore it.

    ⛔ The bound is deliberately LARGE here (30 s, not the 0.05 s the breach
    tests use): this assertion is an EQUALITY on a process-global counter, and
    the interval it measures is `t0` → `wait_for` return, which includes the
    traffic-class lookup, the read-stage timeline and the `ensure_future`. No
    handler blocks (so no slop can be absorbed by a timeout), so a scheduler
    hiccup on a loaded runner would fail a tight bound for a reason this test
    does not mean to test.
    """
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 30.0)
    starved_before = _starved()

    async def _ok(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"ok": true}'})

    rec = await _drive(ha.WaitBoundMiddleware(_ok), _scope("/v1/thing"))

    assert rec.status == 200
    assert _starved()["total"] == starved_before["total"], (
        "a request served INSIDE the bound was counted as a starved breach")


@pytest.mark.asyncio
async def test_a_refusal_is_not_counted_as_starved(baseline, monkeypatch):
    """The other direction of the same partition: a real refusal belongs to the
    refusal channel only. Counting it in both would double-report one request."""
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)
    starved_before = _starved()

    rec = await _drive(ha.WaitBoundMiddleware(_slow_app(5.0)), _scope())

    assert rec.status == 504
    assert _counts()["total"] - baseline["total"] == 1
    assert _starved()["total"] == starved_before["total"], (
        "a REFUSED request is not a starved (answered) one")


@pytest.mark.asyncio
async def test_a_starved_answer_inside_the_REARMED_cold_setup_budget_is_counted(
        monkeypatch):
    """The cold-setup exemption re-arms the budget for the request's OWN work,
    and that arm's lateness is NOT designed — it is a starved breach like any
    other.

    Pinned because an earlier revision muted the whole call once `exempted` was
    set, and the measured consequence was a 10x breach reported nowhere: a 0.05 s
    bound, an instant drain, then the request's own work blocking the loop 0.4 s
    returned at ~0.50 s with `on_starved` never invoked. The fix re-stamps
    `reference` with the re-armed budget, so the measured interval is the arm
    that actually answered.
    """
    from tortoise import mcp_auth as ma

    monkeypatch.setattr(ma, "_cold_setup_ran_during", lambda ref, owner=None: True)
    drained = []
    gate = asyncio.Event()

    async def _drain():
        drained.append(True)
        gate.set()

    monkeypatch.setattr(ma, "_drain_cold_setup", _drain)
    starved_before = _starved()

    async def _blocks_on_the_second_arm():
        # ⛔ The block must start AFTER the re-arm, or the first timer never fires
        # and the exemption arm is never entered — a body that blocked sooner
        # (`await sleep(0.02)` then `time.sleep(0.4)`) passes on the PRE-FIX code
        # too, i.e. it pins nothing. Measured on the real helper: that body gave
        # `drain 0, cold-checks 0, starved 1` with and without the fix.
        await gate.wait()
        import time as _time
        _time.sleep(0.4)
        return "done"

    task = asyncio.ensure_future(_blocks_on_the_second_arm())
    started = monitoring.time.monotonic()
    result = await ma.await_under_wait_bound(
        task, 0.05, reference=started,
        on_starved=lambda _origin: monitoring.record_wait_bound_starved("rest"))

    assert drained == [True], (
        "the cold-setup arm was never entered, so this test cannot discriminate "
        "the re-arm it claims to pin")
    assert result == "done"
    assert _starved()["total"] - starved_before["total"] == 1, (
        "a request that blocked the loop inside its RE-ARMED budget was not "
        "counted — the exemption flag muted the arm that answered")


@pytest.mark.asyncio
async def test_an_in_time_response_head_is_not_a_breach_however_late_the_body(
        baseline, monkeypatch):
    """The promise is about when the CALLER LEARNS something, so a response head
    delivered inside the bound is not a breach — however long the body (or the
    stream) then takes.

    ⛔ Both variants are driven, because a rule keyed on which arm fires split
    this same physical outcome (measured: started+blocked counted, started+
    yielding did not). And the shape is ROUTINE, not contrived: `GET /mcp` is a
    long-lived SSE stream (fastmcp sends its headers immediately and holds the
    stream for the session's life), so counting on close added one false
    "starved" per session — measured as `GET /mcp`, 0.05 s bound, head at t≈0,
    close at 0.4 s ⇒ `starved +1` before this rule existed.
    """
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)

    async def _streams_then_blocks(scope, receive, send):
        import time as _time
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        _time.sleep(0.4)      # the loop is held, so the timer cannot run first
        await send({"type": "http.response.body", "body": b"data: {}\n\n"})

    async def _streams_then_yields(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await asyncio.sleep(0.4)   # the timer wins; no refusal can be sent
        await send({"type": "http.response.body", "body": b"data: {}\n\n"})

    for app, label in ((_streams_then_blocks, "blocked loop"),
                       (_streams_then_yields, "yielding handler")):
        starved_before = _starved()
        rec = await _drive(ha.WaitBoundMiddleware(app),
                           _scope("/mcp", method="GET"))
        assert rec.status == 200, ("a started response is never replaced", label)
        assert _starved()["total"] == starved_before["total"], (
            f"a response head delivered inside the bound was counted as a "
            f"breach when the body/stream closed ({label})")


@pytest.mark.asyncio
async def test_a_head_that_misses_the_bound_is_counted(baseline, monkeypatch):
    """The OTHER side of the head rule, for the NON-RAISING started arm: a head
    that goes out AFTER the bound, with the response then still in flight, is a
    caller that really waited.

    It also pins the SURFACE split (driven on `/mcp` on purpose — see below).
    The raising variant of this arm is pinned separately by
    `test_a_started_response_that_RAISES_still_counts_the_breach`; both red if
    the arm's `_count_starved()` call is deleted. (An earlier revision of this
    docstring claimed to be the ONLY positive pin, which stopped being true once
    that test was added.)
    """
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)

    async def _blocks_then_sends_head_late(scope, receive, send):
        import time as _time
        _time.sleep(0.4)          # nothing sent while the budget runs out
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await asyncio.sleep(0.3)  # head is out; the timer now fires, no refusal
        await send({"type": "http.response.body", "body": b"data: {}\n\n"})

    starved_before = _starved()
    # ⛔ Driven on the `/mcp` surface ON PURPOSE: this is the PRE-SSE stall the
    # runbook says the `mcp` child covers, and it is the only shape that requires
    # `mcp` to be NON-ZERO on this family. Without a surface assertion here,
    # labelling the write "rest" unconditionally left the suite green (measured)
    # — and `total` alone cannot catch it, which the sibling channel's own test
    # says out loud ("`total` stays right either way; `by_surface` is the whole
    # feature").
    rec = await _drive(ha.WaitBoundMiddleware(_blocks_then_sends_head_late),
                       _scope("/mcp", method="POST"))

    assert rec.status == 200
    now = _starved()
    assert now["total"] - starved_before["total"] == 1, (
        "a response whose HEAD missed the bound (and which could no longer be "
        "refused) was counted nowhere")
    assert _by(now, "mcp") - _by(starved_before, "mcp") == 1, (
        "the pre-SSE stall on `/mcp` was not attributed to the `mcp` surface")
    assert _by(now, "rest") - _by(starved_before, "rest") == 0


@pytest.mark.asyncio
async def test_a_refusal_substituted_by_the_mcp_seam_is_not_a_starved_breach(
        baseline, monkeypatch):
    """The MCP dispatch seam REFUSES BY RETURNING its `-32009` result, not by
    raising — so the outer helper sees an ordinary late return for a request the
    caller was just refused.

    Measured before the abstention: one request, `breach +1 starved +1` — the
    served-answer channel reporting a refusal as a served answer. The seam's own
    claim flag (`_WAIT_BOUND_COUNTED_STATE`, the exactly-once contract) is the
    signal, and it is on the shared `scope["state"]` the mount forwards.
    """
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)
    starved_before = _starved()

    async def _seam_style_refusal(scope, receive, send):
        import time as _time
        # What `mcp_server._await_under_mcp_wait_bound` does on breach: claim the
        # count (once, in the shared state) and RETURN the refusal result.
        scope.setdefault("state", {})[ha._WAIT_BOUND_COUNTED_STATE] = True
        monitoring.record_wait_bound_breach("mcp")
        _time.sleep(0.4)                      # the loop was held, so the timer lost
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body",
                    "body": b'data: {"error": {"code": -32009}}\n\n'})

    await _drive(ha.WaitBoundMiddleware(_seam_style_refusal),
                 _scope("/mcp", method="POST"))

    assert _starved()["total"] == starved_before["total"], (
        "a request the MCP seam REFUSED in-band was counted as a served answer "
        "— one request, two counters, and the wrong one says 'served'")


@pytest.mark.asyncio
async def test_a_cold_setup_exemption_is_not_a_starved_breach(monkeypatch):
    """A request that outlives the bound only because it ran the ONE-TIME
    embedder load is exempt by decision (#4055): the load is drained outside
    the budget and the bound is re-armed for the request's own work. The
    FIRST arm's lateness is designed, so it must not be counted; the re-armed
    arm's is not (see the sibling test above)."""
    from tortoise import mcp_auth as ma

    monkeypatch.setattr(ma, "_cold_setup_ran_during", lambda ref, owner=None: True)

    async def _drain():
        await asyncio.sleep(0.15)

    monkeypatch.setattr(ma, "_drain_cold_setup", _drain)
    starved_before = _starved()

    async def _work():
        await asyncio.sleep(0.10)   # outlives the 0.05 bound, within drain+bound
        return "done"

    task = asyncio.ensure_future(_work())
    started = monitoring.time.monotonic()
    result = await ma.await_under_wait_bound(
        task, 0.05, reference=started,
        on_starved=lambda _origin: monitoring.record_wait_bound_starved("rest"))

    assert result == "done"
    assert _starved()["total"] == starved_before["total"], (
        "the designed #4055 cold-setup extension was counted as a starved "
        "breach — every cold start would read as a broken bound")


@pytest.mark.asyncio
async def test_a_raising_starved_write_does_not_break_the_response(
        monkeypatch):
    """Measurement must never be a NEW failure mode of the request it measures:
    a handler that answered must not become a 500 because its counter raised.

    ⛔ Asserting only the 200 would pass on a build with the whole starved WIRING
    removed (no call → no raise → 200), so the test also records that the write
    was ATTEMPTED and for which surface — that is what makes it fail when the
    path disappears. ⚠️ It does NOT pin the HELPER's swallow: `_count_starved`
    catches the raise one frame below it (measured — deleting the helper's
    swallow leaves this green). That one is pinned directly by
    `test_the_helper_swallows_a_failing_callback_and_still_returns`.
    """
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)
    attempted = []

    def _boom(surface):
        attempted.append(surface)
        raise RuntimeError("counter exploded")

    monkeypatch.setattr(monitoring, "record_wait_bound_starved", _boom)
    mw = ha.WaitBoundMiddleware(_blocking_app(0.4, status=200))

    rec = await _drive(mw, _scope("/v1/thing"))

    assert attempted == ["rest"], (
        "the starved write was never attempted for this request — this test "
        "would otherwise pass with the whole channel unwired")
    assert rec.status == 200, "a counter failure replaced the served response"


def test_the_starved_channel_is_unreadable_not_zeroed(monkeypatch):
    """An unread counter and a measured zero are different facts (#9548 P2)."""
    def _boom(*a, **kw):
        raise RuntimeError("family unreadable")

    monkeypatch.setattr(monitoring, "_collect_by_label", _boom)
    channel = monitoring.wait_bound_starved_counts()

    assert set(channel) == {"total", "by_surface", "error"}
    assert channel["total"] is None
    assert channel["by_surface"] is None
    assert "wait_bound_starved unreadable: RuntimeError" in channel["error"]


def test_an_unknown_surface_is_refused_by_the_starved_writer():
    """Same bounded label set as the sibling writer, same reason: the report
    zero-pads exactly the surfaces the writer accepts."""
    import pytest

    for bad in ("rest2", "", "MCP", None):
        with pytest.raises(ValueError):
            monitoring.record_wait_bound_starved(bad)


def test_the_starved_family_exports_both_surfaces_at_zero():
    """`/metrics` must show a ZERO-VALUED SERIES per surface before any starved
    breach, for the same absent-vs-zero reason the refusal family does (see the
    sibling exposition test). A FRESH PROCESS: the counters are process-global,
    so an in-process assertion passes for the wrong reason.

    ⛔ The assertion is on the SERIES LINES, not on a substring: matching
    `{surface="mcp"}` against the whole exposition is satisfied by the SIBLING
    family's line, so a substring pin passes with both starved children removed
    (measured — the first version of this test did exactly that).
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
              if line.startswith("tortoise_wait_bound_starved_total{")]
    assert set(series) == {
        'tortoise_wait_bound_starved_total{surface="mcp"} 0.0',
        'tortoise_wait_bound_starved_total{surface="rest"} 0.0',
    }, (
        "a scrape cannot tell 'nothing starved' from 'this build cannot see "
        "it' unless both children are exported at zero")


def test_the_health_surface_carries_the_starved_channel(baseline):
    """Present at zero, same shape as its sibling, on the MCP health report.

    (The hosted `/health` body is the OTHER surface and has its own test below —
    this one reads `monitoring.metrics()`, which the `tortoise_health` tool
    publishes.)
    """
    channel = monitoring.metrics()["wait_bound_starved"]
    assert set(channel) == {"total", "by_surface", "error"}
    assert set(channel["by_surface"]) == {"mcp", "rest"}
    assert channel["error"] is None


@pytest.mark.asyncio
async def test_the_hosted_health_endpoint_carries_the_starved_channel(
        baseline, monkeypatch):
    """The endpoint an operator curls FIRST, which is the one that read a
    misleading `ok` during #10602 — and REPORTED, never folded into `status`
    (the same recorded decision the breach channel carries)."""
    monkeypatch.setattr(ha._HEALTH_PROBE, "snapshot",
                        lambda: {"ok": True, "observed": True,
                                 "latency_ms": 1.0, "error": None})
    monkeypatch.setattr(ha, "_backup_watcher_health",
                        lambda: {"state": "running", "ok": True,
                                 "error": None, "expected": True})
    monkeypatch.setattr(ha, "_record_health",
                        lambda: {"ok": True, "degraded": False})
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)
    starved_before = _starved()

    await _drive(ha.WaitBoundMiddleware(_blocking_app(0.4, status=200)),
                 _scope("/v1/thing"))
    body = await ha.health()

    assert "wait_bound_starved" in body, (
        "the hosted /health body must carry the answered-breach channel; "
        "without it the only readable evidence of a starved bound is a 20 s "
        "client timeout")
    assert set(body["wait_bound_starved"]["by_surface"]) == {"mcp", "rest"}
    assert body["wait_bound_starved"]["total"] - starved_before["total"] == 1
    assert body["status"] == "ok", (
        "a starved breach is an observation, never a probe failure — folding "
        "it into `status` reverses the recorded decision (#10185/#10620)")


@pytest.mark.asyncio
async def test_a_drain_that_outlasts_the_budget_does_not_become_the_requests_breach(
        monkeypatch):
    """The re-armed budget is measured from the RE-ARM, not from arrival.

    A cold start (#4055) drains the one-time embedder load OUTSIDE the budget and
    re-arms it. If that drain outlasts the original bound — a measured ~57 s
    load against a 10 s bound — then work that finished DURING the drain would
    read as a breach of the caller's own budget unless the reference clock is
    re-stamped with the re-armed budget. This is the shape that discriminates the
    re-stamp: measured with the line deleted, this scenario counts 1; with it, 0
    (the shipped 0.4 s-block scenario counts 1 either way, which is why it could
    not pin this half).
    """
    from tortoise import mcp_auth as ma

    monkeypatch.setattr(ma, "_cold_setup_ran_during", lambda ref, owner=None: True)
    drained = []

    async def _drain():
        drained.append(True)
        await asyncio.sleep(0.9)      # outlasts the 0.5 s original budget

    monkeypatch.setattr(ma, "_drain_cold_setup", _drain)
    starved_before = _starved()

    async def _work():
        # ⛔ Must OUTLIVE the first budget (0.5 s) and finish inside the drain
        # (0.9 s): an instant task returns on the first loop iteration, so the
        # cold-setup arm is never entered and neither the re-stamp nor its
        # absence can change the outcome — measured on the first version of this
        # test (`drain_called == []`, deleting the re-stamp left it green).
        await asyncio.sleep(0.6)
        return "done"

    task = asyncio.ensure_future(_work())
    started = monitoring.time.monotonic()
    result = await ma.await_under_wait_bound(
        task, 0.5, reference=started,
        on_starved=lambda _origin: monitoring.record_wait_bound_starved("rest"))

    assert drained == [True], (
        "the cold-setup arm was never entered, so this test cannot discriminate "
        "the re-stamp it claims to pin")
    assert result == "done"
    assert _starved()["total"] == starved_before["total"], (
        "the drained cold-setup time was reported as a breach of the request's "
        "OWN re-armed budget — latency that was spent on setup, not on the work")


@pytest.mark.asyncio
async def test_an_async_starved_callback_is_rejected_out_loud(caplog):
    """An `async def` callback would return a coroutine nobody awaits: the
    channel would go permanently silent and the swallow would never fire, because
    the call itself succeeds. Pinned as a WARNING, not a silent black hole."""
    import logging

    from tortoise import mcp_auth as ma

    caplog.set_level(logging.WARNING, logger="tortoise.mcp_auth")
    starved_before = _starved()

    async def _async_cb(_origin):
        monitoring.record_wait_bound_starved("rest")

    async def _blocks():
        import time as _time
        _time.sleep(0.2)
        return "done"

    task = asyncio.ensure_future(_blocks())
    started = monitoring.time.monotonic()
    result = await ma.await_under_wait_bound(
        task, 0.05, reference=started, on_starved=_async_cb)

    assert result == "done", "a bad callback must not break the request"
    assert _starved()["total"] == starved_before["total"]
    # ⛔ `rec.getMessage()`, not `rec.message % rec.args`: `record.message` is
    # ALREADY formatted by the handler, so re-applying `%` raises TypeError on a
    # record that carries args — turning a clean failure into a test ERROR.
    assert any("SYNCHRONOUS" in rec.getMessage()
               for rec in caplog.records
               if rec.name == "tortoise.mcp_auth"), (
        "an async on_starved was dropped with no warning — the counter would "
        "read zero forever with nothing to find")


@pytest.mark.asyncio
async def test_a_cold_start_stream_is_measured_against_its_REARMED_budget(
        baseline, monkeypatch):
    """The started arm's head check must use the budget the answer was served
    under, not the transport arrival.

    A cold start (#4055) drains the one-time embedder load and RE-ARMS the bound.
    A head emitted DURING that drain — after the ORIGINAL bound, before the
    RE-ARM — is lateness the exemption attributes to the drain, so the head's
    delta against the re-armed origin is NEGATIVE and the exchange is not
    counted. Measured with the arrival as the origin instead: `starved +1` for
    exactly that caller (the arm cannot tell it apart from a genuinely late head,
    which is the direction `test_a_head_that_misses_the_bound_is_counted` pins).
    The helper publishes the effective origin (`budget_state`) precisely for this
    arm.
    """
    from tortoise import mcp_auth as ma

    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.5)
    monkeypatch.setattr(ma, "_cold_setup_ran_during", lambda ref, owner=None: True)

    async def _drain():
        await asyncio.sleep(0.9)          # re-arms at ~1.4 s

    monkeypatch.setattr(ma, "_drain_cold_setup", _drain)
    starved_before = _starved()

    async def _head_during_the_drain_then_hold(scope, receive, send):
        # Head at ~0.95 s: 0.45 s past the ORIGINAL bound, but BEFORE the
        # re-armed budget begins (~1.4 s) — so its delta against the origin that
        # governs this answer is NEGATIVE and it must not count. Then hold past
        # the re-armed deadline (~1.9 s) so the started arm is the one reached.
        await asyncio.sleep(0.95)
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await asyncio.sleep(1.2)
        await send({"type": "http.response.body", "body": b"data: {}\n\n"})

    rec = await _drive(ha.WaitBoundMiddleware(_head_during_the_drain_then_hold),
                       _scope("/v1/thing"))

    assert rec.status == 200
    assert _starved()["total"] == starved_before["total"], (
        "a cold-start response whose head went out DURING the drain (after the "
        "original bound, before the re-arm) was counted as a starved breach — "
        "the started arm compared it with the transport arrival instead of the "
        "budget that governed the answer")


@pytest.mark.asyncio
async def test_the_timeout_race_arm_is_counted(monkeypatch):
    """The FIRST of the two counting sites in `await_under_wait_bound`: a deadline
    that fires and loses the race to an ALREADY-FINISHED handler
    (`except TimeoutError: if task.done(): _note_starved()`).

    ⛔ Pinned synthetically because the arm is NOT reliably reachable in real time
    (a 260-request probe of the blocked-loop shape reached it 0/260, while the
    same tests instrumented reach it 3/3 — which is why the blocked-loop tests
    also red when its call is deleted). This pin makes the arm's OWN behaviour
    explicit rather than incidental: it asserts the count with the outcome
    unambiguous, so a later change to the race handling fails for its own reason
    instead of through five unrelated tests.
    """
    from tortoise import mcp_auth as ma

    starved_before = _starved()

    async def _instant():
        return "done"

    task = asyncio.ensure_future(_instant())
    await asyncio.sleep(0)                      # let it finish
    assert task.done()

    async def _already_expired(fut, timeout=None):
        # The timer callback ran, and the handler had finished by then.
        await asyncio.sleep(0)
        raise TimeoutError

    monkeypatch.setattr(ma.asyncio, "wait_for", _already_expired)
    started = monitoring.time.monotonic() - 1.0   # the bound is long gone

    result = await ma.await_under_wait_bound(
        task, 0.05, reference=started,
        on_starved=lambda _origin: monitoring.record_wait_bound_starved("rest"))

    assert result == "done", "the handler's own result is surfaced, not a refusal"
    assert _starved()["total"] - starved_before["total"] == 1, (
        "the timeout-vs-completion race arm counted nothing — this is the "
        "counting site a reader would delete as dead code")


@pytest.mark.asyncio
async def test_a_started_response_that_RAISES_still_counts_the_breach(
        baseline, monkeypatch):
    """The started arm counts on the RAISING path too — `except BaseException`
    beside the `else`, NOT a `finally` (a `finally` would also count a cancelled
    caller, exclusion 4): a handler that raises from an already-started response
    is still a caller that waited past the budget with a head out.

    Measured before that arm existed: 0.05 s bound, head at 0.4 s, then
    `raise RuntimeError` — the exception propagated and the breach was counted
    NOWHERE (the refusal channel is structurally blind to it, which is this
    change's whole premise). Pinned because reverting to a plain call after
    `await task` left the whole suite green.
    """
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)

    async def _blocks_sends_head_then_raises(scope, receive, send):
        import time as _time
        _time.sleep(0.3)            # nothing sent while the budget runs out
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        # ⛔ The yield is what reaches the STARTED arm: the (late) timer
        # callback runs here with the task still alive and the head already out.
        # Raising in the same step instead completes the task first, so the
        # helper's `task.done()` arm counts it and this test would pass with the
        # raising arm reverted to a plain call (measured).
        await asyncio.sleep(0.2)
        raise RuntimeError("handler blew up after its head")

    starved_before = _starved()
    with pytest.raises(RuntimeError):
        await _drive(ha.WaitBoundMiddleware(_blocks_sends_head_then_raises),
                     _scope("/v1/thing"))

    assert _starved()["total"] - starved_before["total"] == 1, (
        "a raising handler that had already sent a LATE head was counted "
        "nowhere — the exception skipped the count")


@pytest.mark.asyncio
async def test_the_helper_swallows_a_failing_callback_and_still_returns(
        monkeypatch):
    """`await_under_wait_bound`'s own swallow, pinned DIRECTLY.

    The middleware-level test (`test_a_raising_starved_write_does_not_break_the_
    response`) cannot pin this: `_count_starved` catches the raise one frame
    below, so deleting the helper's swallow left it green. Driven at the helper,
    where the swallow is the only guard between a counter failure and a served
    response turning into a 500.
    """
    from tortoise import mcp_auth as ma

    def _boom(_origin):
        raise RuntimeError("counter exploded at the seam")

    async def _blocks():
        import time as _time
        _time.sleep(0.2)
        return "done"

    task = asyncio.ensure_future(_blocks())
    started = monitoring.time.monotonic()
    result = await ma.await_under_wait_bound(
        task, 0.05, reference=started, on_starved=_boom)

    assert result == "done", (
        "a raising on_starved propagated out of the helper and replaced the "
        "served response — measurement must never be a new failure mode")


@pytest.mark.asyncio
async def test_the_normal_return_arm_is_counted(monkeypatch):
    """The OTHER counting site in `await_under_wait_bound`: the wait RETURNS the
    task's own result after the bound.

    ⛔ Pinned synthetically, and the reason is measured: the blocked-loop tests in
    this file reach the `task.done()` RACE arm instead (instrumented 3/3), because
    the harness's `_Recorder.__call__` never yields — so deleting this arm's
    `_note_starved()` left the whole file green. A stub that sleeps past the bound
    and then returns the finished task's value is the only deterministic way to
    drive it.
    """
    from tortoise import mcp_auth as ma

    starved_before = _starved()

    async def _instant():
        return "done"

    task = asyncio.ensure_future(_instant())
    await asyncio.sleep(0)                     # the handler finished in time
    assert task.done()

    async def _returns_after_the_deadline(fut, timeout=None):
        await asyncio.sleep(0)
        return await fut                       # a NORMAL return, only late

    monkeypatch.setattr(ma.asyncio, "wait_for", _returns_after_the_deadline)
    started = monitoring.time.monotonic() - 1.0   # the bound is long gone

    result = await ma.await_under_wait_bound(
        task, 0.05, reference=started,
        on_starved=lambda _origin: monitoring.record_wait_bound_starved("rest"))

    assert result == "done"
    assert _starved()["total"] - starved_before["total"] == 1, (
        "a NORMAL return after the bound counted nothing — this is the arm the "
        "served-late channel's headline shape goes through")


@pytest.mark.asyncio
async def test_a_CANCELLED_request_is_not_a_served_breach(baseline, monkeypatch):
    """A cancelled caller got NO ANSWER, so it is not a served-late breach.

    ⛔ This exclusion is a DELIBERATE GAP that drops real breaches, and it is the
    `except asyncio.CancelledError` arm that produces the zero — NOT the
    head-in-time abstention. The arm is inside `if response_started:`, so every
    request it governs had already emitted a head, and the test's own app emits
    its head LATE (0.3 s against a 0.05 s bound), which the head rule counts (see
    `test_a_head_that_misses_the_bound_is_counted`). Verified: deleting the head
    rule leaves this test green; reverting the cancellation arm reds it.

    The path is real and periodic: uvicorn cancels the request task on
    `connection_lost`, and every deploy/shutdown cancels in-flight streams. A
    count on cancellation (measured: a late-headed stream cancelled at 0.35 s →
    `starved +1`) books a deploy as a burst indistinguishable from a loop-block
    episode on this channel — which is why the class is reported by NEITHER
    channel (see `monitoring.record_wait_bound_starved`, exclusion 4).
    """
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)

    async def _blocks_sends_head_then_holds(scope, receive, send):
        import time as _time
        _time.sleep(0.3)                     # late head, nothing sent before it
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await asyncio.sleep(30)              # holds for the caller to cancel

    starved_before = _starved()
    request = asyncio.ensure_future(
        _drive(ha.WaitBoundMiddleware(_blocks_sends_head_then_holds),
               _scope("/v1/thing")))
    await asyncio.sleep(0.35)                # the started arm is now awaiting
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    assert _starved()["total"] == starved_before["total"], (
        "a CANCELLED request was counted as a served-late breach — the caller "
        "got no answer")


@pytest.mark.asyncio
async def test_a_failing_starved_write_cannot_break_the_STARTED_arm(
        baseline, monkeypatch):
    """`_count_starved`'s OWN `try/except`, pinned on the arm that has no outer
    guard.

    The helper-level test cannot reach it (`_count_starved` catches the raise one
    frame below, measured), and the started arm calls `_count_starved` directly
    — so without this case the guard is load-bearing and unverified: measured,
    deleting it turns a served 200 into `RuntimeError("counter exploded")`.
    """
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.05)
    attempted = []

    def _boom(surface):
        attempted.append(surface)
        raise RuntimeError("counter exploded")

    monkeypatch.setattr(monitoring, "record_wait_bound_starved", _boom)

    async def _blocks_then_sends_head_late(scope, receive, send):
        import time as _time
        _time.sleep(0.4)          # nothing sent while the budget runs out
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await asyncio.sleep(0.3)  # the timer fires here: the STARTED arm
        await send({"type": "http.response.body", "body": b"data: {}\n\n"})

    rec = await _drive(ha.WaitBoundMiddleware(_blocks_then_sends_head_late),
                       _scope("/v1/thing"))

    assert attempted, "the started arm never reached the write"
    assert rec.status == 200, (
        "a counter failure replaced a served response on the started arm — the "
        "arm has no outer guard, so `_count_starved`'s own is the only one")


# ── the OPERATOR SURFACE: a channel the process emits must be DOCUMENTED ──
#
# #13248 Finding 1 (P1) — the mechanism this arm exists for, measured:
# `docs/infra-runbook.md`'s `### 4.1.1c … wait_bound_starved` section (114 lines,
# added by #13197) was DELETED from the tree by a whole-file OURS conflict
# resolution, while `tortoise/hosted_api.py` still emitted the field (7
# occurrences on both sides). Nothing reded: the census gates count statements,
# the census pins are SCALARS, and the arm that would notice did not exist — so
# an operator following the merged runbook had no documented way to read
# `wait_bound_starved`, and a served-late answer over the bound (a `401` at
# 20.27 s with `wait_bound_breaches.total = 0`) reads as healthy.
#
# Derive the demanded set from `metrics()` rather than listing it here: a NEW
# wait-bound channel must fail this arm on the commit that adds it, not on the
# merge that later drops its section.

_RUNBOOK = (_ROOT := __import__("pathlib").Path(__file__).resolve().parent.parent) / "docs" / "infra-runbook.md"
_SUBSECTION_RE = __import__("re").compile(r"^### 4\.1\.1[a-z]? ")


def _subsection_body_naming(doc: str, channel: str) -> str | None:
    """The body of the numbered `4.1.1*` subsection whose HEADING names `channel`."""
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if _SUBSECTION_RE.match(line) and channel in line:
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("### ") or lines[j].startswith("## "):
                    end = j
                    break
            return "\n".join(lines[i + 1:end])
    return None


def test_every_emitted_wait_bound_channel_has_its_own_numbered_runbook_subsection():
    """Both channels `metrics()` emits must have a `### 4.1.1*` subsection naming each.

    Non-vacuity, both directions, MEASURED on the pre-fix bytes
    (`5bb957cef3:docs/infra-runbook.md`, the tree before #13197 restored the
    section): this arm reports `['wait_bound_starved']` — i.e. it REDS on the
    tree that lost the section, and passes on the tree that carries it.
    """
    emitted = sorted(k for k in monitoring.metrics() if k.startswith("wait_bound_"))
    assert emitted, (
        "`metrics()` no longer reports a `wait_bound_*` channel at all — this "
        "arm derives its demanded set from that surface, so re-point it rather "
        "than letting it pass over an empty set")
    doc = _RUNBOOK.read_text(encoding="utf-8")
    undoc = [ch for ch in emitted if _subsection_body_naming(doc, ch) is None]
    assert not undoc, (
        f"{_RUNBOOK.name} documents no subsection of its own for {undoc} — the "
        "process emits the field and the operator surface cannot read it. A "
        "whole-file conflict resolution that drops a section reds exactly here "
        "(#13248 Finding 1: 114 lines, `wait_bound_starved`)")

    # …and the operator RECIPE must survive somewhere in the runbook with it: the
    # section this pin protects is the one that tells an operator which jq path
    # distinguishes "breached" from "starved" (the #13197 story). The path is
    # asserted at DOCUMENT scope, not inside the channel's own subsection — the
    # `wait_bound_breaches.total` recipe lives in §4.1.1a, beside the sibling
    # channel, and pinning it under §4.1.1 would be a different claim.
    missing_path = [ch for ch in emitted if f".{ch}.total" not in doc]
    assert not missing_path, (
        f"the runbook no longer shows the operator any `.{missing_path[0]}.total` "
        "path — the operator recipe for a channel the process emits is gone")
