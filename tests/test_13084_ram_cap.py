"""#13084 — the per-account RAM cap armed at the tier number, and its estimate rules.

The cap bounds what a tenant may hold RESIDENT, per account, in the graph meter's
own unit (decimal MB). It never deletes anything, never counts nodes, and never
counts graphs (#11758, #9025/B7).

What is pinned here, in the order the issue asks for it:

* **arming** — solo/pro/team carry the tier's 30 %-of-price hot window, read from
  the artifact; free/anon are UNARMED (their artifact amount is an unset `0`,
  and a 0 cap would refuse the first byte);
* **the at-cap behaviour** — REFUSE the write, naming the cap and the tier and
  what to do, because the only evictable unit on FalkorDB is a whole graph;
* **the meter's honesty** — the reading is a LOWER BOUND (blind to the HNSW
  index), so a deadband warns at the cap and hard-refuses only at 1.5x, and a
  stale/absent reading is NO reading rather than 0 MB.
"""
from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from tortoise import pricing
from tortoise.allowance_gate import (
    AT_CAP_WARNING,
    DIMENSIONS,
    HARD_STOP_MULTIPLE,
    MeterReading,
    _graph_storage_mb,
    enforce_allowances,
)
from tortoise.quota import QuotaCheckError, QuotaExceededError, _arm_graph_storage

ORG = "team-13084"


def _limits(**overrides) -> dict:
    return {"org_id": ORG, "tier": "solo", "max_points": None,
            "max_api_keys": None, "max_sessions": None, "max_users": None,
            "max_graphs": None, **overrides}


def _reading(used: float, *, measured_at: str | None = None,
             estimated: bool = True) -> MeterReading:
    return MeterReading(
        used=used,
        measured_at=measured_at or datetime.now(UTC).isoformat(),
        estimated=estimated,
        note="SAMPLING ESTIMATE that excludes the HNSW vector index",
    )


def _meter(used: float, **kwargs):
    return lambda _org: _reading(used, **kwargs)


# ── 1. arming: the tier number, from the artifact ─────────────────────────


def test_solo_pro_team_are_armed_at_the_derived_tier_numbers():
    """$19 x 30 % / $74 per GB-month = 77 MB; pro 102; team 615.

    The figures are asserted against ``product/pricing.json`` through its ONE
    accessor, so this test fails if either the cap or the price moves — which is
    the point: two numbers for one allowance is how a cap and a price drift.
    """
    assert pricing.storage_limits("solo")["max_graph_storage_mb"] == 77
    assert pricing.storage_limits("pro")["max_graph_storage_mb"] == 102
    assert pricing.storage_limits("team")["max_graph_storage_mb"] == 615
    for tier, expected in (("solo", 77), ("pro", 102), ("team", 615)):
        limits = _arm_graph_storage({"org_id": ORG, "tier": tier}, tier)
        assert limits["max_graph_storage_mb"] == expected, tier


def test_free_and_anon_are_UNARMED_and_say_so():
    """The owner has not set those tiers' hot window, and a 0 cap is a landmine.

    ``enforce_allowances`` refuses at ``used >= allowance``, so emitting the
    artifact's ``0`` would refuse EVERY gated write — including a tenant's very
    first byte — on tiers that still grant a graph. The unset amount must arrive
    as an ABSENT key (unarmed), never as a zero cap.
    """
    for tier in ("free", "anon"):
        limits = _arm_graph_storage({"org_id": ORG, "tier": tier}, tier)
        assert "max_graph_storage_mb" not in limits, tier
        # ...and if a surface ever emits the 0 anyway, the gate is defended.
        enforce_allowances(_limits(tier=tier, max_graph_storage_mb=0),
                           meters={"graph_storage": _meter(0.0)})


def test_the_armed_allowance_reaches_the_gate_through_the_carry():
    """The arming seam (#9567 D12): a resolved allowance + the shared carry."""
    from tortoise.allowance_gate import carry_resolved_allowances

    resolved = _arm_graph_storage({"org_id": ORG, "tier": "solo"}, "solo")
    limits = carry_resolved_allowances({"org_id": ORG, "tier": "solo"},
                                       resolver=lambda _o: resolved)
    assert limits["max_graph_storage_mb"] == 77
    with pytest.raises(QuotaExceededError):
        enforce_allowances(limits, meters={"graph_storage": _meter(200.0)})


# ── 2. the at-cap behaviour: REFUSE the write, and say what to do ──────────


def test_at_the_tier_number_the_cap_only_WARNS(caplog):
    """An ESTIMATE may not be the sole cause of a hard refusal (deadband)."""
    with caplog.at_level("WARNING"):
        enforce_allowances(_limits(max_graph_storage_mb=77),
                           meters={"graph_storage": _meter(77.0)})
        enforce_allowances(_limits(max_graph_storage_mb=77),
                           meters={"graph_storage": _meter(100.0)})
    assert AT_CAP_WARNING in caplog.text


def test_the_hard_stop_is_1_5x_the_cap_and_refuses_with_a_usable_message():
    hard_stop = 77 * HARD_STOP_MULTIPLE
    assert hard_stop == 115.5
    enforce_allowances(_limits(max_graph_storage_mb=77),
                       meters={"graph_storage": _meter(hard_stop - 0.5)})
    with pytest.raises(QuotaExceededError) as caught:
        enforce_allowances(_limits(max_graph_storage_mb=77),
                           meters={"graph_storage": _meter(hard_stop)})
    message = str(caught.value)
    # the cap, the tier, the hard stop, why they differ, and what to do
    assert "77 MB" in message and "solo plan" in message
    assert "115.5 MB" in message and "1.5x the cap" in message
    assert "LOWER BOUND" in message and "under-reports" in message
    assert "Upgrade your plan" in message
    assert "reduce what you hold resident" in message
    # the structured payload carries the figures the gate COMPARED
    assert caught.value.resource == "graph_storage"
    assert caught.value.limit == 77
    assert caught.value.estimated is True  # never presented as exact


def test_the_refusal_names_the_dimension_and_the_meter_caveat():
    with pytest.raises(QuotaExceededError) as caught:
        enforce_allowances(
            _limits(max_graph_storage_mb=77),
            meters={"graph_storage": lambda _o: MeterReading(
                used=200.0, measured_at=datetime.now(UTC).isoformat(),
                estimated=True,
                note="SAMPLING ESTIMATE that excludes the HNSW vector index")})
    assert "HNSW vector index" in str(caught.value)


def test_reads_are_never_gated_even_far_over_the_cap():
    """Redis' ``noeviction`` posture: reads keep working, NEW work is refused."""
    enforce_allowances(_limits(max_graph_storage_mb=77), resource="api_keys",
                       meters={"graph_storage": _meter(10_000.0)})
    enforce_allowances(_limits(max_graph_storage_mb=77), resource="sessions",
                       meters={"graph_storage": _meter(10_000.0)})


# ── 3. the meter's honesty: a stale/absent reading is NOT zero ────────────


def test_a_missing_measured_at_is_no_reading_not_zero(monkeypatch):
    """The ledger's zero view and a degraded read both carry no timestamp.

    Reading either as 0 MB would compare as "under the cap" and pass silently —
    the fail-open the dimension's ``absent_is_zero=False`` exists to stop.
    """
    from tortoise import metering

    monkeypatch.setattr(
        metering, "get_graph_storage_reading",
        lambda _org: {"graph_storage_mb": 0.0,
                      "graph_storage_measured_at": None})
    assert _graph_storage_mb(ORG) is None
    with pytest.raises(QuotaCheckError):
        enforce_allowances(_limits(max_graph_storage_mb=77))


def test_a_stale_reading_is_refused_rather_than_enforced(monkeypatch):
    from tortoise import metering

    stale = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    monkeypatch.setattr(
        metering, "get_graph_storage_reading",
        lambda _org: {"graph_storage_mb": 10.0,
                      "graph_storage_measured_at": stale})
    with pytest.raises(QuotaCheckError) as caught:
        enforce_allowances(_limits(max_graph_storage_mb=77))
    assert "older than" in str(caught.value)


def test_a_fresh_reading_under_the_cap_is_served(monkeypatch):
    from tortoise import metering

    monkeypatch.setattr(
        metering, "get_graph_storage_reading",
        lambda _org: {"graph_storage_mb": 12.5,
                      "graph_storage_measured_at":
                          datetime.now(UTC).isoformat()})
    enforce_allowances(_limits(max_graph_storage_mb=77))


# ── 4. the contracts the cap must not break ───────────────────────────────


def test_the_cap_deletes_nothing():
    """#9025/B7: this bounds residency; it never removes stored history.

    Two halves — the gate CALLS no delete/eviction function (checked on the
    parsed AST, so prose that merely names one does not satisfy the test), and a
    refusal writes nothing to the meter (a refused write must not look like a
    measurement, and must not touch the graph at all).
    """
    import ast

    from tortoise import allowance_gate, graph_storage, metering

    tree = ast.parse(inspect.getsource(allowance_gate))
    forbidden = {"delete", "evict", "drop_graph", "_delete_nodes",
                 "execute_command"}
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute)
        else getattr(node.func, "id", "")
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert not (called & forbidden), called & forbidden

    def _must_not_be_called(*_a, **_k):
        raise AssertionError("a cap refusal must not write/mutate the store")

    orig_rec, orig_gs = (metering.record_graph_storage_reading,
                         graph_storage.record_graph_storage)
    metering.record_graph_storage_reading = _must_not_be_called
    graph_storage.record_graph_storage = _must_not_be_called
    try:
        with pytest.raises(QuotaExceededError):
            enforce_allowances(_limits(max_graph_storage_mb=77),
                               meters={"graph_storage": _meter(500.0)})
    finally:
        metering.record_graph_storage_reading = orig_rec
        graph_storage.record_graph_storage = orig_gs


def test_the_cap_is_per_account_in_MB_with_no_node_or_graph_count():
    entry = {d.dimension: d for d in DIMENSIONS}["graph_storage"]
    assert entry.unit == "MB"
    assert "node" not in entry.limit_key.lower()
    assert "graph" not in entry.unit.lower()
    # one dimension, one key, one account: the allowance is a scalar, not a map
    assert isinstance(entry.limit_key, str)
