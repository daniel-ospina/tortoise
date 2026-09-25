"""#4493 — fixed/shared SaaS cost ALLOCATION, and the revived per-team metric.

Evidence this file is built to produce (the issue's three indicators):

1. the per-team cost metric carries a NON-ZERO value for a real org, asserted
   through the PRODUCTION path (``hosted_api._refresh_cost_allocation``), not
   just the counter object;
2. a mechanical per-line allocation rule, named as an allocation;
3. a figure readable by a person (the snapshot + the metric), no dashboard.
"""
from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from prometheus_client import generate_latest

from tortoise import cost_allocation as ca
from tortoise import hosted_api as ha
from tortoise import metering, monitoring

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_metric():
    """The Prometheus Gauge is process-global — isolate every test."""
    ca._reset_for_tests()
    yield
    ca._reset_for_tests()


# ── Indicator 2 · the declared rule ────────────────────────────────────────


def test_declared_lines_are_the_fixed_saas_set():
    names = [ln.name for ln in ca.declared_lines()]
    assert names == ["fly_base", "falkordb_base", "supabase_base", "posthog", "sentry"]


def test_every_line_states_basis_total_source_as_of_and_estimate():
    for ln in ca.declared_lines():
        assert ln.basis in (ca.BASIS_EVEN, ca.BASIS_PROPORTIONAL)
        assert isinstance(ln.total_cents, int) and ln.total_cents >= 0
        assert ln.source.strip(), f"{ln.name} has no source"
        assert ln.as_of.strip(), f"{ln.name} has no as_of"
        assert isinstance(ln.is_estimate, bool)


def test_rule_is_declared_on_the_module_docstring_as_an_allocation():
    """Indicator 2: 'named explicitly as an allocation rather than a
    measurement'. The contract travels with the code, not only the issue."""
    doc = (ca.__doc__ or "").lower()
    assert "allocation" in doc
    assert "not a measurement" in doc or "is **not a measurement**" in doc


def test_import_invariant_rejects_a_malformed_rule():
    good = ca.LineSpec("x", ca.BASIS_EVEN, 1, "src", "2026-01-01", True, "ENV_X")
    bad_cases = [
        ca.LineSpec("x", "proportional-ish", 1, "src", "2026-01-01", True, "E"),
        ca.LineSpec("x", ca.BASIS_EVEN, -1, "src", "2026-01-01", True, "E"),
        ca.LineSpec("x", ca.BASIS_EVEN, 1, "  ", "2026-01-01", True, "E"),
        ca.LineSpec("x", ca.BASIS_EVEN, 1, "src", " ", True, "E"),
        ca.LineSpec("x", ca.BASIS_EVEN, 1, "src", "2026-01-01", True, " "),
        ca.LineSpec("", ca.BASIS_EVEN, 1, "src", "2026-01-01", True, "E"),
    ]
    for bad in bad_cases:
        with pytest.raises(ValueError):
            ca._assert_lines_valid([*ca.declared_lines(), bad])
    ca._assert_lines_valid([*ca.declared_lines(), good])


def test_env_override_supplies_a_real_invoice_figure(monkeypatch):
    fly = next(ln for ln in ca.declared_lines() if ln.name == "fly_base")
    monkeypatch.setenv(fly.env_var, "9999")
    assert ca.line_total_cents(fly) == 9999


@pytest.mark.parametrize("raw", ["oops", "-5", "3.5", "  "])
def test_malformed_env_override_falls_back_to_the_default(monkeypatch, raw):
    fly = next(ln for ln in ca.declared_lines() if ln.name == "fly_base")
    monkeypatch.setenv(fly.env_var, raw)
    assert ca.line_total_cents(fly) == fly.total_cents


# ── Constraint (iii) · largest remainder ───────────────────────────────────


def test_largest_remainder_sums_exactly_and_is_deterministic():
    weights = {"a": 1, "b": 1, "c": 1}
    first = ca.allocate_largest_remainder(100, weights)
    second = ca.allocate_largest_remainder(100, weights)
    assert sum(first.values()) == 100
    assert first == second, "a re-run must never reshuffle a cent"
    assert first == {"a": 34, "b": 33, "c": 33}


def test_zero_weight_sum_parks_the_total_in_the_residual_bucket():
    assert ca.allocate_largest_remainder(500, {}) == {ca.RESIDUAL_ORG: 500}
    assert ca.allocate_largest_remainder(500, {"a": 0, "b": 0}) == {ca.RESIDUAL_ORG: 500}


def test_zero_total_allocates_nothing():
    assert ca.allocate_largest_remainder(0, {"a": 1}) == {}


@pytest.mark.parametrize("bad", [-1, 1.5, "3", True])
def test_malformed_totals_are_rejected(bad):
    with pytest.raises(ValueError):
        ca.allocate_largest_remainder(bad, {"a": 1})


def test_malformed_weights_are_rejected():
    with pytest.raises(ValueError):
        ca.allocate_largest_remainder(10, {"a": -1})
    with pytest.raises(ValueError):
        ca.allocate_largest_remainder(10, {"a": 1.5})


# ── The rule applied · even and proportional ───────────────────────────────


def test_even_basis_splits_equally_across_orgs(monkeypatch):
    fly = next(ln for ln in ca.declared_lines() if ln.name == "fly_base")
    monkeypatch.setenv(fly.env_var, "300")
    snap = ca.evaluate_allocation(["org_a", "org_b", "org_c"])
    line = next(ln for ln in snap.lines if ln.line == "fly_base")
    assert line.state == "measured"
    assert {s.org_label: s.cents for s in line.shares} == {
        "org_a": 100, "org_b": 100, "org_c": 100}


def test_proportional_basis_splits_by_measured_write_ops(monkeypatch):
    ph = next(ln for ln in ca.declared_lines() if ln.name == "posthog")
    monkeypatch.setenv(ph.env_var, "100")
    snap = ca.evaluate_allocation(
        ["org_a", "org_b"], weights_by_org={"org_a": 3, "org_b": 1})
    line = next(ln for ln in snap.lines if ln.line == "posthog")
    assert line.state == "measured"
    assert {s.org_label: s.cents for s in line.shares} == {"org_a": 75, "org_b": 25}


def test_snapshot_names_both_the_kind_and_the_window():
    snap = ca.evaluate_allocation(["org_a"])
    payload = snap.to_dict()
    assert payload["kind"] == "allocation"
    assert payload["unit"] == "cents"
    assert payload["window"]["start"] and payload["window"]["end"]
    assert payload["lines"][0]["source"] and payload["lines"][0]["as_of"]


# ── Constraint (ii) · fail-closed, never a silent zero ─────────────────────


def test_unreadable_org_enumeration_is_unavailable_not_zero():
    snap = ca.evaluate_allocation([])
    assert snap.state == "unavailable"
    assert all(ln.state == "unavailable" for ln in snap.lines)
    assert all(ln.shares == () for ln in snap.lines), "no share may be emitted"


def test_missing_proportional_weight_makes_the_line_unavailable(monkeypatch):
    ph = next(ln for ln in ca.declared_lines() if ln.name == "posthog")
    monkeypatch.setenv(ph.env_var, "100")
    snap = ca.evaluate_allocation(["org_a", "org_b"], weights_by_org={"org_a": 3})
    line = next(ln for ln in snap.lines if ln.line == "posthog")
    assert line.state == "unavailable"
    assert line.shares == ()
    assert snap.state == "unavailable"


def test_a_measured_zero_basis_is_not_unavailable(monkeypatch):
    """The distinction the whole fail-closed rule rests on: an org that really
    consumed nothing is a MEASURED zero (the total parks in the residual
    bucket); an unreadable basis is `unavailable`. They must never collapse."""
    ph = next(ln for ln in ca.declared_lines() if ln.name == "posthog")
    monkeypatch.setenv(ph.env_var, "100")
    snap = ca.evaluate_allocation(["org_a"], weights_by_org={"org_a": 0})
    line = next(ln for ln in snap.lines if ln.line == "posthog")
    assert line.state == "not_measurable"
    assert line.residual_cents == 100
    assert snap.enumeration_available is True


def test_measure_write_ops_raises_when_the_window_is_unreadable(monkeypatch):
    def _boom(_org_id):
        raise RuntimeError("anchor unreadable")

    monkeypatch.setattr(metering, "_current_period", _boom)
    with pytest.raises(RuntimeError):
        metering.measure_write_ops("org_a")


# ── Constraint (iv)/(v) · the metric, its state, and bounded cardinality ───


def test_publish_sets_the_gauge_and_the_metric_help_names_an_allocation():
    snap = ca.evaluate_allocation(
        ["org_a"], now=datetime(2026, 9, 15, tzinfo=UTC))
    ca.publish(snap)
    assert ca.allocation_by_org().get("org_a", 0) > 0
    text = generate_latest().decode()
    assert "tortoise_team_cost_cents" in text
    assert "NOT a measurement" in text, "the help must name the allocation"


def test_unavailable_refresh_leaves_the_last_known_good_untouched():
    good = ca.evaluate_allocation(["org_a"])
    ca.publish(good)
    before = ca.allocation_by_org()
    ca.publish(ca.evaluate_allocation([]))  # unreadable
    assert ca.allocation_by_org() == before
    assert ca.current_snapshot().state == "unavailable"


def test_org_labels_are_bounded_with_a_fixed_overflow_child(monkeypatch):
    monkeypatch.setattr(ca, "MAX_ORG_LABELS", 2)
    snap = ca.evaluate_allocation(["org_1", "org_2", "org_3", "org_4"])
    ca.publish(snap)
    labels = ca.allocation_by_org()
    assert ca.ORG_OVERFLOW in labels
    assert len([k for k in labels if k != ca.ORG_OVERFLOW]) == 2
    declared = sum(ln.total_cents for ln in snap.lines)
    assert sum(labels.values()) == declared, "folding must not lose cents"


def test_org_label_injection_cannot_forge_an_exposition_line():
    # ``record_cost`` is what the writer calls; a hostile org id must not be
    # able to break out of its quoted label and FORGE a new exposition line.
    # prometheus_client escapes the quote/newline, so the hostile text survives
    # as data inside one sample — the assertion is that it never becomes a line.
    hostile = 'a"} 999\nbogus_metric{x="'
    monitoring.record_cost(hostile, 7)
    text = generate_latest().decode()
    forged = [ln for ln in text.splitlines() if ln.startswith("bogus_metric")]
    assert forged == [], f"label injection forged a line: {forged!r}"
    # The value round-trips under its original (unescaped) label key.
    assert ca.allocation_by_org().get(hostile) == 7


# ── Indicator 1 · the PRODUCTION call site ─────────────────────────────────


def test_production_path_publishes_a_nonzero_value_for_a_real_org(monkeypatch):
    """Drives the REAL production coroutine — not the metric object, not the
    allocation helper in isolation. This is the assertion indicator 1 asks for
    ('a test asserts the production call site, not just the counter object')."""
    monkeypatch.setattr(
        ha, "_iter_registered_orgs",
        lambda: [{"org_id": "org_real_1"}, {"org_id": "org_real_2"}])
    monkeypatch.setattr(
        ha, "_measured_write_ops_basis", lambda orgs: {o: 1 for o in orgs})

    asyncio.run(ha._refresh_cost_allocation())

    published = ca.allocation_by_org()
    assert published, "the production path published nothing"
    assert published.get("org_real_1", 0) > 0
    snap = ca.current_snapshot()
    assert snap is not None and snap.state == "measured"
    assert snap.to_dict()["kind"] == "allocation"


def test_event_retention_loop_awaits_the_cost_refresh():
    """The leg must live INSIDE the hourly ``while True:`` body — a call placed
    anywhere else fires once per process. Pinned statically because the loop is
    a closure inside ``_lifespan`` (the established pattern in
    ``tests/test_3036_oauth_retention.py``)."""
    tree = ast.parse((REPO / "tortoise" / "hosted_api.py").read_text(encoding="utf-8"))
    loop = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "_event_retention_loop"),
        None)
    assert loop is not None, "_event_retention_loop not found"
    while_loop = next(
        (n for n in ast.walk(loop)
         if isinstance(n, ast.While) and isinstance(n.test, ast.Constant)
         and n.test.value is True), None)
    assert while_loop is not None, "_event_retention_loop has no `while True:`"
    names = {n.id for n in ast.walk(while_loop) if isinstance(n, ast.Name)}
    assert "_refresh_cost_allocation" in names


def test_a_failing_refresh_cannot_kill_the_retention_loop(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("allocator exploded")

    monkeypatch.setattr(ca, "refresh_and_publish", _boom)
    monkeypatch.setattr(ha, "_iter_registered_orgs", lambda: [{"org_id": "o"}])
    # The retention loop has no per-iteration guard: this must NOT raise.
    asyncio.run(ha._refresh_cost_allocation())


def test_an_unconfirmed_empty_enumeration_fails_closed(monkeypatch):
    """``_iter_registered_orgs`` returns [] on ANY failure, so [] is 'unknown',
    not 'no orgs'. It must never be published as a fleet-wide zero."""
    monkeypatch.setattr(ha, "_iter_registered_orgs", lambda: [])
    asyncio.run(ha._refresh_cost_allocation())
    snap = ca.current_snapshot()
    assert snap is not None and snap.state == "unavailable"
    assert ca.allocation_by_org() == {}


# ── Constraint (vii) + single writer · invariants that are ENFORCED ────────


def test_allocation_never_touches_the_measured_ledger():
    """#3665: ``metering_records`` holds MEASURED metres and the cohort ceiling
    sums them. An allocation landing there would corrupt the cap, so the
    separation is asserted, not just stated."""
    src = (REPO / "tortoise" / "cost_allocation.py").read_text(encoding="utf-8")
    for forbidden in ("MeteringRecord", "record_ask_usage",
                      "record_capture_usage", "record_write_ops"):
        assert forbidden not in src, f"cost_allocation must not reference {forbidden}"


def test_cost_allocation_is_the_only_writer_of_the_team_cost_metric():
    hits = []
    for path in (REPO / "tortoise").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        if "record_cost(" in src and path.name != "monitoring.py":
            hits.append(path.name)
    assert hits == ["cost_allocation.py"], (
        f"unexpected writers of record_cost: {hits} — a second writer would "
        "break the reconciliation invariant")
