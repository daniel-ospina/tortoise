"""#11892 option (3): the quota boundary must be legible from `health`.

The ticket's third option is the only one that does not need an owner decision
(raise the cap / adopt a prune policy are those), and it is the smallest: a
caller must be able to tell **open** from **closed** from **could not measure**
WITHOUT attempting a write first.

Today it cannot. `health` reports `graph_size` (a different population, from a
different module) and nothing about the enforced gate, whose predicate lives at
``tortoise/quota.py`` — ``if used >= limit: raise QuotaExceededError``. So a lane
drafts a finding, calls ``evidence``, and learns the write path is closed at the
refusal — which on the CLI even exits **0** (#11153), i.e. presents as success.

These tests pin three properties, each of which is one of this repo's recorded
rules:

1. **`state` is a STRING, never a tri-state boolean.** ``None`` is falsy, so
   ``if not writes_closed:`` would read "unknown" as "open" — the same
   false-PASS class as #11153. A `state` string mirrors the sibling
   `backup_watcher` block (``{"state": "disabled", "ok": true, ...}``).
2. **Unmeasured is ``None``, never ``0``.** ``points_used: 0`` reads as an empty
   graph; the #3253 ``graph_size_error`` rule and #9548's record counters
   ("an unread counter and a zero are different facts") are the precedent.
3. **Reported, never folded into ``status``.** A full graph is not a degraded
   process; folding it in would pin ``degraded`` for a healthy deployment and
   let Fly restart it forever — the rule `hosted_api.health`'s docstring already
   states for `change_feed`, `record`, `build` and `wait_bound_breaches`.

And one invariant that is the whole point: the report must AGREE with the gate.
If ``state == "closed"`` the gate must refuse; if the gate refuses the report
must say ``closed``. A report that can disagree with the refusal is a new
false signal, not a fix.
"""

from __future__ import annotations

import os

import pytest

from tortoise.quota import (
    QuotaCheckError,
    QuotaExceededError,
    enforce_org_limit,
    org_quota_state,
)


def _sdk(db_path, tag: str, points: int = 0):
    """An embedded SDK carrying exactly ``points`` counted points."""
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(str(db_path), namespace=f"test_11892_{tag}_{os.urandom(4).hex()}")
    for i in range(points):
        sdk.create_point("statement", f"claim {i}")
    return sdk


def _pin_count(monkeypatch, used: int):
    """Make BOTH the report and the gate measure ``used``.

    The boundary table has to reach 31,248 (the live state in #11892), which no
    test can materialise as points — and it must reach it for the GATE too, or
    the agreement assertion would compare the report against a different number
    than the one it describes. Patching ``_count_resource`` is what the gate and
    this report share by contract ("count on the SAME org SDK"), so both observe
    it.
    """
    import tortoise.quota as quota_mod

    monkeypatch.setattr(quota_mod, "_count_resource", lambda *a, **k: used)


def _refuses(limits, sdk) -> bool:
    """Ask the REAL gate, so agreement is measured against behaviour."""
    try:
        enforce_org_limit(limits, "points", sdk=sdk)
    except QuotaExceededError:
        return True
    return False


def _refuses_raising(limits, sdk) -> bool:
    """True when the gate refuses by RAISING ANYTHING (cap or fail-closed)."""
    try:
        enforce_org_limit(limits, "points", sdk=sdk)
    except (QuotaExceededError, QuotaCheckError):
        return True
    return False


# ── 1. agreement with the gate (the invariant) ────────────────────────────


@pytest.mark.parametrize("used,limit", [
    (0, 25_000),      # empty graph, generous cap
    (24_999, 25_000), # one slot of headroom — the last write that succeeds
    (25_000, 25_000), # AT the cap: the gate's own `used >= limit` boundary
    (31_248, 25_000), # the measured production state in #11892
    (1, 1),           # smallest non-trivial boundary
])
def test_the_report_agrees_with_the_gate_at_every_boundary(
        tmp_path, monkeypatch, used, limit):
    sdk = _sdk(tmp_path / f"u{used}.db", f"a{used}")
    _pin_count(monkeypatch, used)
    limits = {"org_id": "team11892", "max_points": limit}
    state = org_quota_state(limits, "points", sdk=sdk)
    refused = _refuses(limits, sdk)
    assert state["state"] == ("closed" if refused else "open"), (
        f"report says {state['state']!r} but the gate refused={refused} "
        f"(used={state['points_used']}, limit={state['points_limit']})")
    assert state["points_used"] == used
    assert state["points_limit"] == limit
    assert state["points_remaining"] == max(0, limit - used)
    assert state["error"] is None
    sdk.close()


def test_headroom_is_never_negative(tmp_path, monkeypatch):
    """Over the cap (the live case) reports 0 remaining, never a negative."""
    sdk = _sdk(tmp_path / "over.db", "over")
    _pin_count(monkeypatch, 31_248)
    state = org_quota_state({"org_id": "t", "max_points": 25_000},
                            "points", sdk=sdk)
    assert state["state"] == "closed"
    assert state["points_remaining"] == 0
    assert state["points_used"] == 31_248 and state["points_limit"] == 25_000
    sdk.close()


def test_an_unlimited_limit_is_open_and_says_so(tmp_path, monkeypatch):
    """A PRESENT-but-None limit means UNLIMITED (#683) — open, no number.

    ⛔ `points_used` is deliberately `None` here and NOT a measured count: the
    gate RETURNS before counting when the limit is unlimited (#4010 — "must not
    build a graph handle to prove it"), so paying for a full-graph count on every
    `health` call would buy a number nothing enforces. `points_limit: None` is
    what says "unlimited", so this `None` is not ambiguous with a failed read.
    """
    sdk = _sdk(tmp_path / "unl.db", "unl")
    calls = []
    import tortoise.quota as quota_mod

    monkeypatch.setattr(quota_mod, "_count_resource",
                        lambda *a, **k: calls.append(a) or 3)
    state = org_quota_state({"org_id": "t", "max_points": None}, "points", sdk=sdk)
    assert state["state"] == "open"
    assert state["points_limit"] is None
    assert state["points_remaining"] is None
    assert state["points_used"] is None
    assert state["error"] is None
    assert calls == [], "the gate skips the count when unlimited; so must we"
    sdk.close()


# ── 2. unmeasured is None, never 0 ────────────────────────────────────────


def test_an_unreadable_count_is_closed_never_zero(tmp_path, monkeypatch):
    """A failed count is a REFUSAL in the gate (#686), so it is `closed`.

    ⛔ Not `open` (that was the class this report exists to remove) and not a
    number — `points_used` stays `None` with the fault named. `unknown` is
    reserved for OUR OWN inability to finish the check, never for a gate verdict.
    """
    sdk = _sdk(tmp_path / "cnt.db", "cnt", points=1)
    import tortoise.quota as quota_mod

    def _boom(*a, **k):
        raise QuotaCheckError("count exploded")

    monkeypatch.setattr(quota_mod, "_count_resource", _boom)
    state = org_quota_state({"org_id": "t", "max_points": 25_000},
                            "points", sdk=sdk)
    assert state["state"] == "closed"
    # ⛔ the whole point: NOT 0, and NOT "open"
    assert state["points_used"] is None
    assert state["points_remaining"] is None
    assert state["error"] and "count" in state["error"]
    # …and the gate really does refuse in this state, which is why `closed` is
    # the honest word rather than "unknown".
    assert _refuses_raising({"org_id": "t", "max_points": 25_000}, sdk)
    sdk.close()


def test_a_missing_limit_key_is_closed_not_unlimited(tmp_path):
    """#310 GAP-B: a MISSING key is fail-closed; only an explicit None is open."""
    sdk = _sdk(tmp_path / "miss.db", "miss", points=1)
    state = org_quota_state({"org_id": "t"}, "points", sdk=sdk)
    assert state["state"] == "closed"
    assert state["points_limit"] is None
    assert state["error"] and "max_points" in state["error"]
    sdk.close()


def test_an_unreadable_limit_is_closed(tmp_path):
    """#4051: an int-formatted string is honoured; a float-formatted one refuses."""
    sdk = _sdk(tmp_path / "unr.db", "unr", points=1)

    honoured = org_quota_state({"org_id": "t", "max_points": "1000"},
                               "points", sdk=sdk)
    assert honoured["state"] == "open"
    assert honoured["points_limit"] == 1000

    refused = org_quota_state({"org_id": "t", "max_points": "1000.0"},
                              "points", sdk=sdk)
    assert refused["state"] == "closed"
    assert refused["points_limit"] is None
    assert refused["error"] and "max_points" in refused["error"]
    sdk.close()


def test_a_count_that_overruns_its_budget_is_unknown(tmp_path, monkeypatch):
    """A wedged count is reported as unmeasured, never as a number.

    The worker is a daemon and is abandoned (CPython cannot cancel it), so the
    budget is what keeps `health` answering; the report must then say `unknown`
    rather than publish the 0 it never measured.
    """
    import time

    sdk = _sdk(tmp_path / "slow.db", "slow", points=1)
    import tortoise.quota as quota_mod

    monkeypatch.setattr(quota_mod, "_count_resource",
                        lambda *a, **k: time.sleep(0.6) or 7)
    state = org_quota_state({"org_id": "t", "max_points": 25_000},
                            "points", sdk=sdk, timeout=0.05)
    assert state["state"] == "unknown"
    assert state["points_used"] is None
    assert "budget" in state["error"]
    sdk.close()


def test_no_org_context_is_not_applicable_not_open(tmp_path):
    """stdio/selfhost skip the gate — "no gate here", never "measured open"."""
    state = org_quota_state(None, "points")
    assert state["state"] == "not_applicable"
    assert state["points_used"] is None and state["points_limit"] is None
    assert state["error"], "not_applicable must say why it does not apply"


def test_an_unknown_resource_never_raises():
    state = org_quota_state({"org_id": "t", "max_points": 10}, "not_a_resource")
    assert state["state"] == "unknown"
    assert state["error"]


def test_a_gateable_but_unmodelled_resource_says_so():
    """`documents` IS gated, through a DERIVED branch this report does not model.

    Calling it "unknown resource" would misdescribe the gate; the honest answer is
    `unknown` with a message saying the branch is unmodelled (fresh review of
    #12426, non-blocking note made executable).
    """
    state = org_quota_state({"org_id": "t", "max_points": 10}, "documents")
    assert state["state"] == "unknown"          # fail-closed, never `open`
    assert state["error"] and "DERIVED" in state["error"]


# ── the allowance seam: the false PASS fresh review of #12426 found ───────


def test_a_fault_in_the_allowance_gate_is_closed_not_open(tmp_path, monkeypatch):
    """⛔ THE regression this round exists for.

    `enforce_org_limit` calls `enforce_allowances` FIRST (#5331/#9567), and
    "inert by default" is about what that gate REFUSES, not about what it READS:
    a fault in the read is a `QuotaCheckError` with no allowance configured. The
    report used to skip the seam entirely and publish `open` for a tenant whose
    every write was refused.
    """
    sdk = _sdk(tmp_path / "allow.db", "allow", points=1)
    _pin_count(monkeypatch, 100)
    import tortoise.allowance_gate as ag

    def _boom(*a, **k):
        raise QuotaCheckError("the team allowances could not be resolved")

    monkeypatch.setattr(ag, "enforce_allowances", _boom)
    state = org_quota_state({"org_id": "t", "max_points": 25_000},
                            "points", sdk=sdk)
    assert state["state"] == "closed", (
        "a refusal by the allowance seam must never read as an open write path")
    assert state["error"] and "allowance" in state["error"]
    sdk.close()


def test_an_armed_allowance_refusal_is_closed(tmp_path, monkeypatch):
    """A configured dimension at its cap refuses the write — so: `closed`."""
    sdk = _sdk(tmp_path / "arm.db", "arm", points=1)
    _pin_count(monkeypatch, 100)
    import tortoise.allowance_gate as ag

    def _refuse(*a, **k):
        raise QuotaExceededError("Team graph_mb limit reached (10)")

    monkeypatch.setattr(ag, "enforce_allowances", _refuse)
    state = org_quota_state({"org_id": "t", "max_points": 25_000},
                            "points", sdk=sdk)
    assert state["state"] == "closed"
    assert state["error"] and "allowance" in state["error"]
    sdk.close()


def test_an_unreadable_allowance_gate_module_is_closed(tmp_path, monkeypatch):
    """The gate turns an ImportError at that seam into a refusal; so do we.

    ⛔ Fault injection note: patching `builtins.__import__` does NOT reach this
    site, because an already-imported module is served from `sys.modules` and the
    hook is never consulted — the first version of this test passed a fake importer
    and read `open`. Putting `None` in `sys.modules` is what makes the real import
    machinery raise ImportError.
    """
    import sys

    sdk = _sdk(tmp_path / "imp.db", "imp", points=1)
    _pin_count(monkeypatch, 1)
    monkeypatch.setitem(sys.modules, "tortoise.allowance_gate", None)
    state = org_quota_state({"org_id": "t", "max_points": 25_000},
                            "points", sdk=sdk)
    assert state["state"] == "closed"
    assert state["error"] and "allowance gate is unavailable" in state["error"]
    sdk.close()


# ── "never raises" on shapes the caller can pass ─────────────────────────


@pytest.mark.parametrize("limits", [[], "str", 7, {"org_id": None}])
@pytest.mark.parametrize("resource", ["points", ["points"], None, "documents"])
def test_it_never_raises_on_a_shape_it_cannot_read(limits, resource):
    """⛔ Measured before the guard: AttributeError / TypeError escaped here.

    `health` was safe because `_org_quota_block` has a blanket except, but the
    docstring promises "never raises" for THIS function, and a future caller
    wiring it on that promise would get an exception instead of the dict.
    """
    state = org_quota_state(limits, resource)  # must not raise
    assert state["state"] in {"unknown", "not_applicable", "closed", "open"}
    assert "points_used" in state and "error" in state


def test_a_non_numeric_count_is_unknown_never_a_number(tmp_path, monkeypatch):
    sdk = _sdk(tmp_path / "nn.db", "nn", points=0)
    import tortoise.quota as quota_mod

    monkeypatch.setattr(quota_mod, "_count_resource", lambda *a, **k: None)
    state = org_quota_state({"org_id": "t", "max_points": 10}, "points", sdk=sdk)
    assert state["state"] == "unknown"
    assert state["points_used"] is None
    assert state["error"] and "not a count" in state["error"]
    sdk.close()


# ── the bounded count: worker identity, the settled-future window, reset ───


def test_the_count_uses_its_OWN_dedicated_worker(tmp_path, monkeypatch):
    """A count must not share the graph-size or probe slot (#3143's class)."""
    import tortoise.monitoring as mon
    import tortoise.quota as quota_mod

    sdk = _sdk(tmp_path / "wk.db", "wk", points=0)
    used = []
    real = mon.daemon_worker

    def _spy(name, **k):
        used.append(name)
        return real(name, **k)

    monkeypatch.setattr(mon, "daemon_worker", _spy)
    quota_mod.reset_quota_state_worker()
    org_quota_state({"org_id": "t", "max_points": 10}, "points", sdk=sdk)
    assert used == [quota_mod.QUOTA_STATE_WORKER_NAME]
    assert quota_mod.QUOTA_STATE_WORKER_NAME != mon.GRAPH_SIZE_WORKER_NAME
    sdk.close()


class _SettledFuture:
    """A future that is already finished when the wait expires (#11962's window)."""

    def __init__(self, value=None, exc=None):
        self._value, self._exc = value, exc

    def result(self, timeout=None):
        if timeout is None:
            return self._value
        raise TimeoutError("budget expired")

    def done(self):
        return True

    def cancelled(self):
        return False

    def exception(self):
        return self._exc


class _SettledWorker:
    def __init__(self, fut):
        self._fut = fut

    def submit(self, fn):
        return self._fut


def test_a_count_that_COMPLETED_in_the_window_is_not_called_an_overrun(
        monkeypatch):
    """The wait can expire AFTER the count landed — return its answer.

    Deleting this arm (the #11962 defect) previously left every test green; this
    drives it with a settled future so the branch is reachable.
    """
    import tortoise.monitoring as mon
    import tortoise.quota as quota_mod

    monkeypatch.setattr(mon, "daemon_worker",
                        lambda name, **k: _SettledWorker(_SettledFuture(value=7)))
    assert quota_mod._bounded_resource_count("t", "points", timeout=0.01) == 7


def test_a_count_that_FAILED_in_the_window_keeps_its_own_error(monkeypatch):
    """…and a landed FAILURE propagates as itself, not as a budget overrun."""
    import tortoise.monitoring as mon
    import tortoise.quota as quota_mod

    err = QuotaCheckError("store unreachable")
    monkeypatch.setattr(mon, "daemon_worker", lambda name, **k: _SettledWorker(
        _SettledFuture(exc=err)))
    with pytest.raises(QuotaCheckError):
        quota_mod._bounded_resource_count("t", "points", timeout=0.01)


def test_a_wedged_worker_has_a_reset_hatch(monkeypatch):
    """A stalled count must not silence `health.quota` until the next deploy."""
    import tortoise.monitoring as mon
    import tortoise.quota as quota_mod

    quota_mod.reset_quota_state_worker()
    first = mon.daemon_worker(quota_mod.QUOTA_STATE_WORKER_NAME)
    quota_mod.reset_quota_state_worker()
    second = mon.daemon_worker(quota_mod.QUOTA_STATE_WORKER_NAME)
    assert first is not second, "the hatch must drop the worker, not reuse it"
    quota_mod.reset_quota_state_worker()


# ── 3. the health surface carries it, and stays healthy ───────────────────


def _wire_health(monkeypatch, sdk, limits, metrics=None):
    """Point tortoise_health at an org context + a stub metrics report.

    ``_transport_mode`` is patched on ``mcp_server`` itself, not on ``mcp_auth``:
    the module imported the ContextVar by name at import time, so patching the
    origin module would leave this namespace's reference untouched — and the
    auth gate would reject the call before any report is built.
    """
    import tortoise.mcp_server as mcp
    from tortoise import mcp_auth

    monkeypatch.setattr(mcp, "_get_org_sdk", lambda: sdk)
    monkeypatch.setattr(mcp, "_transport_mode", _ctx("http"))
    monkeypatch.setattr(mcp.monitoring, "metrics", metrics or (
        lambda sdk=None, setup_timeout=None: {
            "status": "ok", "db": {"ok": True}, "graph_size": 31_248}))
    monkeypatch.setattr(mcp_auth, "_current_org_id", _ctx("team11892"))
    monkeypatch.setattr(mcp_auth, "_current_org_limits", _ctx(limits))


def _ctx(value):
    import contextvars

    var = contextvars.ContextVar("test_11892", default=None)
    var.set(value)
    return var


def test_health_carries_a_quota_block(tmp_path, monkeypatch):
    sdk = _sdk(tmp_path / "h1.db", "h1", points=2)
    _wire_health(monkeypatch, sdk, {"org_id": "team11892", "max_points": 2})
    from tortoise.mcp_server import tortoise_health

    report = tortoise_health()
    quota = report["quota"]
    assert quota["resource"] == "points"
    assert quota["state"] == "closed"
    assert quota["points_used"] == 2 and quota["points_limit"] == 2
    # Reported, never folded: a full graph is not a degraded process.
    assert report["status"] == "ok"
    sdk.close()


def test_a_closed_quota_does_not_degrade_health(tmp_path, monkeypatch):
    """The recorded rule: folding a quota into `status` restarts healthy boxes."""
    sdk = _sdk(tmp_path / "h2.db", "h2", points=5)
    _wire_health(monkeypatch, sdk, {"org_id": "team11892", "max_points": 5},
                 metrics=lambda sdk=None, setup_timeout=None: {
                     "status": "ok", "db": {"ok": True}, "graph_size": 5})
    from tortoise.mcp_server import tortoise_health

    report = tortoise_health()
    assert report["quota"]["state"] == "closed"
    assert report["status"] == "ok"
    sdk.close()


def test_health_still_answers_when_the_quota_read_explodes(tmp_path, monkeypatch):
    """`health` must never 5xx because a quota read failed (#2850's contract)."""
    sdk = _sdk(tmp_path / "h3.db", "h3", points=1)
    _wire_health(monkeypatch, sdk, {"org_id": "team11892", "max_points": 10})

    import tortoise.quota as quota_mod

    def _boom(*a, **k):
        raise RuntimeError("control plane on fire")

    monkeypatch.setattr(quota_mod, "org_quota_state", _boom)
    from tortoise.mcp_server import tortoise_health

    report = tortoise_health()
    assert report["status"] == "ok"
    assert report["quota"]["state"] == "unknown"
    assert report["quota"]["points_used"] is None
    assert report["quota"]["error"]
    sdk.close()


# ── the BLOCK-level half of the allowance fix (mutation-verified gap) ──────


def test_health_reports_closed_when_the_CARRIED_limits_arm_a_dimension(
        tmp_path, monkeypatch):
    """⛔ THE mutation a review found: delete the `_carry_allowances` call and the
    whole suite stayed green at 41 passed.

    The carry is what puts a configured dimension INTO the limits dict the gate
    (and therefore this report) evaluates, so without it the block published
    `open` for a tenant whose every write was refused. Arm a dimension through
    the real seam (`_carry_allowances` → `carry_resolved_allowances` → resolver)
    and assert the BLOCK — not just `org_quota_state` — says `closed`.
    """
    sdk = _sdk(tmp_path / "carry.db", "carry", points=1)
    _wire_health(monkeypatch, sdk, {"org_id": "team11892", "max_points": 25_000})
    import tortoise.mcp_server as mcp

    # The resolver supplies a configured (and BLOCKED, i.e. unreadable) allowance.
    monkeypatch.setattr(mcp, "_carry_allowances",
                        lambda limits, resource: {**limits,
                                                  "max_graph_storage_mb": 10})
    import tortoise.allowance_gate as ag

    monkeypatch.setattr(ag, "enforce_allowances",
                        lambda *a, **k: (_ for _ in ()).throw(
                            QuotaCheckError("dimension 'graph_storage' is BLOCKED")))
    from tortoise.mcp_server import tortoise_health

    report = tortoise_health()
    assert report["quota"]["state"] == "closed", (
        "a carried allowance that refuses the write must not read as open")
    assert report["status"] == "ok"  # still reported, never folded
    sdk.close()


def test_health_reports_closed_when_the_carry_itself_raises(tmp_path, monkeypatch):
    """A control-plane fault in the carry is a refusal, not `unknown`."""
    sdk = _sdk(tmp_path / "carryboom.db", "carryboom", points=1)
    _wire_health(monkeypatch, sdk, {"org_id": "team11892", "max_points": 25_000})
    import tortoise.mcp_server as mcp

    monkeypatch.setattr(mcp, "_carry_allowances",
                        lambda limits, resource: (_ for _ in ()).throw(
                            QuotaCheckError("allowances could not be resolved")))
    from tortoise.mcp_server import tortoise_health

    report = tortoise_health()
    assert report["quota"]["state"] == "closed"
    assert "could not be resolved/carried" in report["quota"]["error"]
    sdk.close()


def test_the_whole_block_runs_on_the_bounded_worker(tmp_path, monkeypatch):
    """⛔ Resolving the limits is I/O too — it must NOT run on the health thread.

    `resolve_org_limits` opens a registry SDK and runs a `MATCH (:Team)` with no
    timeout, so bounding only the count left an unbounded read on a tool whose
    contract is "always answers". Assert the resolver executes on the quota
    worker, and that a resolver that never returns still yields a report inside
    the budget.
    """
    import threading

    import tortoise.quota as quota_mod
    from tortoise.mcp_server import tortoise_health

    sdk = _sdk(tmp_path / "bound.db", "bound", points=1)
    # No carried limits → the block must resolve them itself.
    _wire_health(monkeypatch, sdk, None)
    seen = {}

    def _slow_resolve(org_id):
        seen["thread"] = threading.current_thread().name
        return {"org_id": org_id, "max_points": 25_000}

    monkeypatch.setattr(quota_mod, "resolve_org_limits", _slow_resolve)
    monkeypatch.setattr(quota_mod, "_count_resource", lambda *a, **k: 1)
    quota_mod.reset_quota_state_worker()
    report = tortoise_health()
    assert report["quota"]["state"] == "open"
    assert seen["thread"] != threading.current_thread().name, (
        "the limits resolution must not run on the request thread")

    # …and a resolver that never returns still yields a report INSIDE the budget
    # (the claim this test's name makes; fresh review of #12426, round 3).
    import time

    def _never(org_id):
        time.sleep(1.0)
        return {"org_id": org_id, "max_points": 25_000}

    monkeypatch.setattr(quota_mod, "resolve_org_limits", _never)
    monkeypatch.setattr(quota_mod, "QUOTA_STATE_TIMEOUT", 0.05)
    started = time.monotonic()
    slow = tortoise_health()
    elapsed = time.monotonic() - started
    assert elapsed < 0.9, f"health blocked {elapsed:.2f}s on an unbounded resolve"
    assert slow["quota"]["state"] == "unknown"
    assert slow["quota"]["points_used"] is None
    assert "budget" in slow["quota"]["error"]
    sdk.close()
    quota_mod.reset_quota_state_worker()
