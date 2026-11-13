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

    The distinction that matters: `points_limit: None` here is "no cap", not
    "could not read the cap". `state` carries that, so a reader never has to
    infer it from an absence.
    """
    sdk = _sdk(tmp_path / "unl.db", "unl")
    _pin_count(monkeypatch, 3)
    state = org_quota_state({"org_id": "t", "max_points": None}, "points", sdk=sdk)
    assert state["state"] == "open"
    assert state["points_limit"] is None
    assert state["points_remaining"] is None
    assert state["points_used"] == 3
    assert state["error"] is None
    sdk.close()


# ── 2. unmeasured is None, never 0 ────────────────────────────────────────


def test_an_unreadable_count_is_unknown_never_zero(tmp_path, monkeypatch):
    """A failed count must not read as an empty graph."""
    sdk = _sdk(tmp_path / "cnt.db", "cnt", points=1)
    import tortoise.quota as quota_mod

    def _boom(*a, **k):
        raise QuotaCheckError("count exploded")

    monkeypatch.setattr(quota_mod, "_count_resource", _boom)
    state = org_quota_state({"org_id": "t", "max_points": 25_000},
                            "points", sdk=sdk)
    assert state["state"] == "unknown"
    # ⛔ the whole point: NOT 0, and NOT "open"
    assert state["points_used"] is None
    assert state["points_remaining"] is None
    assert state["error"], "an unknown state must name why it is unknown"
    sdk.close()


def test_a_missing_limit_key_is_unknown_not_unlimited(tmp_path):
    """#310 GAP-B: a MISSING key is fail-closed; only an explicit None is open."""
    sdk = _sdk(tmp_path / "miss.db", "miss", points=1)
    state = org_quota_state({"org_id": "t"}, "points", sdk=sdk)
    assert state["state"] == "unknown"
    assert state["points_limit"] is None
    assert state["error"] and "max_points" in state["error"]
    sdk.close()


def test_an_unreadable_limit_is_unknown(tmp_path):
    """#4051: an int-formatted string is honoured; a float-formatted one is not."""
    sdk = _sdk(tmp_path / "unr.db", "unr", points=1)

    honoured = org_quota_state({"org_id": "t", "max_points": "1000"},
                               "points", sdk=sdk)
    assert honoured["state"] == "open"
    assert honoured["points_limit"] == 1000

    refused = org_quota_state({"org_id": "t", "max_points": "1000.0"},
                              "points", sdk=sdk)
    assert refused["state"] == "unknown"
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
