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


def _expected_pre_gate_state(limits, resource) -> str:
    """The state each PRE-GATE arm must publish, derived from the guard ORDER.

    ⛔ Round 4 of #12426: this file used to assert only
    `state in {"unknown", "not_applicable", "closed", "open"}` — a four-element
    membership over a FOUR-VALUED enum, which pinned nothing. Mutating either
    pre-gate arm below to `open` survived the whole file **and** the repo's
    quota/monitoring regression set (283 passed), so a refactor could publish
    `open` for a shape this report never measured with every case green — the
    exact false PASS the report exists to remove, one layer in. The expectation
    is therefore derived from `org_quota_state`'s own order, not by membership.

    `tortoise/quota.py` checks, in this order: (0) a non-dict `limits`,
    (0b) a non-str `resource`, (0c) `limits is None`, (0d) a missing/falsy
    `org_id`. The FIRST match decides — which is why a non-dict `limits` is
    `unknown` even when the resource is ALSO unreadable, and why a dict with no
    `org_id` plus an unreadable resource is `unknown`, not `not_applicable`.
    """
    if limits is not None and not isinstance(limits, dict):
        return "unknown"
    if not isinstance(resource, str):
        return "unknown"
    if limits is None or not limits.get("org_id"):
        return "not_applicable"
    raise AssertionError(
        f"this table covers only the PRE-GATE arms; {limits!r}/{resource!r} "
        "reaches the allowance gate, so it belongs to a boundary case")


@pytest.mark.parametrize("limits", [[], "str", 7, {"org_id": None}])
@pytest.mark.parametrize("resource", ["points", ["points"], None, "documents"])
def test_it_never_raises_on_a_shape_it_cannot_read(limits, resource):
    """⛔ Measured before the guard: AttributeError / TypeError escaped here.

    `health` was safe because `_org_quota_block` has a blanket except, but the
    docstring promises "never raises" for THIS function, and a future caller
    wiring it on that promise would get an exception instead of the dict.

    ⛔ "Never raises" is only HALF of it. Each arm is asserted against the state
    its own guard must publish (see `_expected_pre_gate_state`) and against
    `!= "open"`, because an unreadable shape is the one thing this report must
    never call admit-worthy.
    """
    state = org_quota_state(limits, resource)  # must not raise
    assert state["state"] == _expected_pre_gate_state(limits, resource), (
        f"pre-gate arm published {state['state']!r} for {limits!r}/{resource!r}")
    assert state["state"] != "open"
    # Unmeasured is `None`, never `0` — the arm measured nothing at all.
    assert state["points_used"] is None
    assert state["points_limit"] is None
    assert state["points_remaining"] is None
    assert state["error"]


def test_a_non_mapping_limits_shape_is_unknown_because_the_gate_RAISES():
    """The materially-false arm: `open` here would be a lie, not a lost nuance.

    `enforce_org_limit` cannot evaluate this dict either — `limits.get("org_id")`
    raises `AttributeError` on a list/str/int — so the WRITE PATH errors while a
    report calling it `open` says the write would be admitted. Round 4 of #12426
    measured exactly that mutation surviving; this pins the classification, so
    the surviving mutant now reds.

    (The sibling arm — a dict with a falsy `org_id` — is the milder one: the gate
    really does return early there, so `not_applicable` is a precision claim
    about measured-vs-not rather than a claim `open` would falsify.)
    """
    for bad in ([], "str", 7, object()):
        state = org_quota_state(bad, "points")
        assert state["state"] == "unknown", (
            f"a non-mapping limits shape must be `unknown`, got "
            f"{state['state']!r} for {bad!r}")
        assert "not a mapping" in state["error"], state["error"]
        assert state["points_used"] is None


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


def test_the_whole_block_pins_its_OWN_settled_future_window(monkeypatch):
    """⛔ `run_bounded_quota_state` carries its own #11962 re-read, and it was
    unpinned: replacing that body with a bare `unknown` return left 44 green, so a
    block that measured fine but answered after the wait expired would silently
    degrade to `unknown` exactly when latency rises (fresh review of #12426,
    round 3). A settled future must hand back the MEASURED state.
    """
    import tortoise.monitoring as mon
    import tortoise.quota as quota_mod

    measured = {"resource": "points", "state": "open", "points_used": 1,
                "points_limit": 10, "points_remaining": 9, "error": None}
    monkeypatch.setattr(mon, "daemon_worker", lambda name, **k: _SettledWorker(
        _SettledFuture(value=measured)))
    assert quota_mod.run_bounded_quota_state(lambda: measured) == measured
    # …including when an explicit budget expires under it.
    assert quota_mod.run_bounded_quota_state(
        lambda: measured, timeout=0.01) == measured


def test_an_unexpected_fault_in_the_allowance_gate_is_closed_not_unknown(
        tmp_path, monkeypatch):
    """⛔ An UNEXPECTED raise at the allowance seam refuses the write, so `closed`.

    This arm had ZERO coverage in round 5: the three sibling allowance tests
    exercise `QuotaCheckError` (:264), `QuotaExceededError` (:289) and a
    `sys.modules`-disabled `ImportError` (:306) — none reaches the generic
    `except Exception`. Reclassifying it to `open` therefore left this file AND
    the 3-file regression set green (47 / 284 passed), in a state where no write
    completes.

    ⛔ It is `closed`, NOT `unknown`, and the difference is measured rather than
    stylistic: `enforce_allowances` is the write gate's FIRST check
    (`enforce_org_limit` calls it at `quota.py:1356`, outside the guarded import,
    so the raise propagates), and `_safe` catches it — so no write completes. The
    count arm below reports its own unexpected raise as `closed` for the same
    reason. `unknown` is reserved for a budget expiry HERE, where the gate has no
    such timeout and its answer may still land.

    ⛔ CORRECTION (post-merge review of #12426): this docstring used to say
    `_safe` returns `ERR_QUOTA_SERVER` here. It does not — `ERR_QUOTA_SERVER`
    comes only from `_safe`'s `QuotaCheckError` branch (`mcp_server.py:3384`); the
    generic arm returns `_SafeError({"error": msg})` with no `code` at all (the
    module says so in `_carried_error_text`'s docstring). The classification rests
    on "no write completes", which is now driven below — not on the code.

    ⛔ AND THE GATE IS DRIVEN, not just the report asked: the first version of
    this test only called `org_quota_state` and asserted the word `closed`, so a
    defensive `try/except` added around the seam would have let `health` keep
    saying `closed` while the write COMPLETED, with this file green (measured:
    that mutant left 49 passed). The file already holds its count arm to this
    standard (`_refuses_raising`); the allowance arm now does too.
    """
    sdk = _sdk(tmp_path / "unexp.db", "unexp", points=1)
    _pin_count(monkeypatch, 100)
    import tortoise.allowance_gate as ag

    def _bug(*a, **k):
        # Deliberately NOT a QuotaCheckError/QuotaExceededError: a plain bug in
        # the gate, which is what this arm exists for.
        raise RuntimeError("unexpected fault inside the allowance gate")

    limits = {"org_id": "t", "max_points": 25_000}
    monkeypatch.setattr(ag, "enforce_allowances", _bug)
    state = org_quota_state(limits, "points", sdk=sdk)
    assert state["state"] == "closed", (
        "an unexpected fault at the allowance seam still refuses the write; "
        f"got {state['state']!r}")
    assert state["state"] != "open"
    assert state["error"] and "unexpected" in state["error"]
    assert state["points_used"] is None and state["points_limit"] is None
    # ⛔ The gate's own behaviour in this state, which is what `closed` CLAIMS.
    # ANY raise counts: this is not a modelled quota refusal, it is an uncoded
    # failure — and `_safe` turns any raise into an error, so no write completes.
    gate_raised = False
    try:
        enforce_org_limit(limits, "points", sdk=sdk)
    except Exception:  # the raise IS the refusal being pinned
        gate_raised = True
    assert gate_raised, (
        "`health` reports `closed`, so the gate must actually refuse by raising; "
        "otherwise the report asserts a refusal the write path does not make "
        "— #11153's false signal, inverted")
    sdk.close()


def test_the_selfhost_arm_is_not_applicable_not_closed(tmp_path, monkeypatch):
    """⛔ The gate returns EARLY for `SELFHOST_ORG_ID` (no billing, no registry), so
    a report calling that `closed` would assert a refusal the gate does not make
    — the report disagreeing with the gate it claims to describe. The arm was
    unpinned: deleting the `== SELFHOST_ORG_ID` half left 44 green (fresh review
    of #12426, round 3).
    """
    from tortoise import mcp_auth

    sdk = _sdk(tmp_path / "sh.db", "sh", points=1)
    _wire_health(monkeypatch, sdk, {"org_id": "selfhost", "max_points": 10})
    monkeypatch.setattr(mcp_auth, "_current_org_id", _ctx(mcp_auth.SELFHOST_ORG_ID))
    import tortoise.quota as quota_mod

    def _must_not_run(*a, **k):
        raise AssertionError("the selfhost arm must not resolve tenant limits")

    monkeypatch.setattr(quota_mod, "resolve_org_limits", _must_not_run)
    from tortoise.mcp_server import tortoise_health

    report = tortoise_health()
    assert report["quota"]["state"] == "not_applicable"
    assert report["quota"]["points_used"] is None
    assert report["status"] == "ok"
    sdk.close()


def test_no_org_context_is_not_applicable_not_closed(tmp_path, monkeypatch):
    """⛔ The OTHER half of the pre-gate guard: `not org_id`.

    `_enforce_quota` returns EARLY at `if not org_id: return` — a stdio/operator
    session has no tenant, so no write can be refused for quota. Round 3 pinned
    the `== SELFHOST_ORG_ID` half (the test above) and left this one unpinned;
    round 5 measured it: deleting `not org_id or` at `mcp_server.py:5016` left
    this file AND the 3-file regression set green (47 / 284 passed) while flipping
    a session-less `health` from `not_applicable` to `closed` — because
    `resolve_org_limits(None)` raises `QuotaCheckError` and the block reports that
    as a refusal.

    That is the same defect the selfhost half was found to have, in the shape the
    #11892 issue is about: a lane told to PARK a finding the graph would have
    accepted.
    """
    import tortoise.quota as quota_mod
    from tortoise import mcp_auth
    from tortoise.mcp_server import tortoise_health

    def _must_not_run(*a, **k):
        raise AssertionError(
            "with no org context the block must not resolve tenant limits")

    for label, falsy in (("none", None), ("emptystr", "")):
        sdk = _sdk(tmp_path / f"noorg_{label}.db", f"noorg_{label}", points=1)
        _wire_health(monkeypatch, sdk, {"org_id": "team11892", "max_points": 10})
        monkeypatch.setattr(mcp_auth, "_current_org_id", _ctx(falsy))
        monkeypatch.setattr(quota_mod, "resolve_org_limits", _must_not_run)

        report = tortoise_health()
        assert report["quota"]["state"] == "not_applicable", (
            f"org_id={falsy!r} must be `not_applicable`, got "
            f"{report['quota']['state']!r} — `closed` would assert a refusal the "
            f"gate does not make (`_enforce_quota` returns early at `if not "
            f"org_id: return`)")
        assert report["quota"]["points_used"] is None
        assert report["status"] == "ok"
        sdk.close()


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


def test_the_blocks_own_blanket_except_reports_unknown_never_open(
        tmp_path, monkeypatch):
    """⛔ The `_org_quota_block` blanket `except` — the last arm before `health`
    would 5xx — was unpinned. Found by a per-arm mutation sweep in round 5 of
    #12426, after two rounds of the same class had been reported one arm at a
    time.

    ⛔ `test_health_still_answers_when_the_quota_read_explodes` LOOKS like it
    covers this arm and does NOT: it patches `quota.org_quota_state` to raise, but
    that call runs inside `_compute` on the bounded worker, where
    `run_bounded_quota_state` catches it first and returns its own `unknown`. The
    block's blanket `except` is only reached by a fault OUTSIDE `_compute` — the
    guarded imports, or the ContextVar read — so flipping its hardcoded
    `state` to `open` left this whole file green (49 passed, measured).

    `open` here is the worst classification available: this is the arm that
    doubles as `health`'s last line of defence, so it would claim an admitted
    write path for a request whose quota machinery could not even be imported.
    """
    sdk = _sdk(tmp_path / "blanket.db", "blanket", points=1)
    _wire_health(monkeypatch, sdk, {"org_id": "team11892", "max_points": 10})

    from tortoise import mcp_auth
    from tortoise.mcp_server import tortoise_health

    class _ExplodingVar:
        """A ContextVar handle whose read faults — outside `_compute`."""

        def get(self, *a, **k):
            raise RuntimeError("the auth ContextVar is unusable")

        def set(self, *a, **k):  # pragma: no cover — never reached
            raise AssertionError("this test must not write the ContextVar")

    monkeypatch.setattr(mcp_auth, "_current_org_id", _ExplodingVar())

    report = tortoise_health()
    assert report["status"] == "ok", "a quota fault must never degrade status"
    assert report["quota"]["state"] == "unknown", (
        "the block's last-resort arm must be `unknown`, never `open`; got "
        f"{report['quota']['state']!r}")
    assert report["quota"]["points_used"] is None
    assert report["quota"]["error"]
    assert "quota state unavailable" in report["quota"]["error"]
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

    # The resolver supplies a configured allowance, and the stub refuses ONLY when
    # that allowance is actually present in the dict it is handed. ⛔ That
    # conditionality is the point: an UNCONDITIONAL raising stub makes this test
    # pass whether or not the carry happened, so the mutation that KEEPS the call
    # but discards its return (`_carry_allowances(limits, "points")` as a bare
    # statement) left 44 green while restoring the false PASS (fresh review of
    # #12426, round 3).
    monkeypatch.setattr(mcp, "_carry_allowances",
                        lambda limits, resource: {**limits,
                                                  "max_graph_storage_mb": 10})
    import tortoise.allowance_gate as ag

    seen_limits = []

    def _refuse_only_if_armed(the_limits, *a, **k):
        seen_limits.append(dict(the_limits))
        if "max_graph_storage_mb" in the_limits:
            raise QuotaCheckError("dimension 'graph_storage' is BLOCKED")

    monkeypatch.setattr(ag, "enforce_allowances", _refuse_only_if_armed)
    from tortoise.mcp_server import tortoise_health

    report = tortoise_health()
    assert seen_limits and "max_graph_storage_mb" in seen_limits[0], (
        "the allowance the carry adds must reach the gate's own input")
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
