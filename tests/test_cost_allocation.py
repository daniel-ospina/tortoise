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


@pytest.fixture(autouse=True)
def _pin_cost_env(monkeypatch):
    """Hermetic: every test here depends on the allocation overrides, so the
    AMBIENT shell must not be able to change an outcome. Each test that wants an
    override sets it explicitly with ``monkeypatch.setenv``."""
    for line in ca.declared_lines():
        monkeypatch.delenv(line.env_var, raising=False)


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


@pytest.mark.parametrize("raw", ["oops", "-5", "3.5", "  ", "100000001"])
def test_malformed_env_override_fails_closed_instead_of_falling_back(monkeypatch, raw):
    """A PRESENT-but-unusable override is an explicit `unavailable` STATE, never
    a silent fallback to the declared default — which is 0 for four of the five
    lines, i.e. indistinguishable from "unconfigured"."""
    fly = next(ln for ln in ca.declared_lines() if ln.name == "fly_base")
    monkeypatch.setenv(fly.env_var, raw)
    snap = ca.evaluate_allocation(["org_a"], weights_by_org={"org_a": 1})
    line = next(ln for ln in snap.lines if ln.line == "fly_base")
    assert line.state == ca.STATE_UNAVAILABLE
    assert line.shares == (), "no share may be published for an unusable override"
    assert fly.env_var in line.detail, (
        f"detail must name the variable, got {line.detail!r}")
    assert repr(raw) in line.detail, (
        f"detail must name the offending value, got {line.detail!r}")


def test_line_total_cents_fails_closed_on_a_malformed_override(monkeypatch):
    """The value-only accessor must not hand back the DECLARED DEFAULT for a
    present-but-unusable override — that is exactly the silent fallback the
    fail-closed rule forbids. It raises instead, so the contract of the accessor
    matches the log line that claims the default is not used."""
    fly = next(ln for ln in ca.declared_lines() if ln.name == "fly_base")
    monkeypatch.setenv(fly.env_var, "oops")
    with pytest.raises(ValueError, match=fly.env_var):
        ca.line_total_cents(fly)


def test_line_total_cents_still_returns_the_declared_default_when_unset():
    """Unconfigured is not an error: an ABSENT variable yields the declared
    default (only a PRESENT-but-unusable one fails closed)."""
    fly = next(ln for ln in ca.declared_lines() if ln.name == "fly_base")
    assert ca.line_total_cents(fly) == fly.total_cents


def test_absent_override_uses_the_declared_default_without_an_error_state():
    """The fail-closed rule is about a PRESENT override; unset keeps the default
    and is not an error."""
    fly = next(ln for ln in ca.declared_lines() if ln.name == "fly_base")
    snap = ca.evaluate_allocation(["org_a"], weights_by_org={"org_a": 1})
    line = next(ln for ln in snap.lines if ln.line == "fly_base")
    assert line.state == ca.STATE_MEASURED
    assert line.total_cents == fly.total_cents


def test_override_provenance_describes_the_override_not_the_declaration(monkeypatch):
    """An operator-supplied invoice must NOT be published with the in-repo
    declaration's source/as_of/is_estimate."""
    fly = next(ln for ln in ca.declared_lines() if ln.name == "fly_base")
    monkeypatch.setenv(fly.env_var, "9999")
    snap = ca.evaluate_allocation(
        ["org_a"], now=datetime(2026, 10, 3, tzinfo=UTC),
        weights_by_org={"org_a": 1})
    line = next(ln for ln in snap.lines if ln.line == "fly_base")
    assert line.total_cents == 9999
    assert line.source == f"env override {fly.env_var}"
    assert line.as_of == "2026-10-03", "as_of must be the OBSERVATION date"
    assert line.as_of != fly.as_of
    assert line.is_estimate is False, "an operator's invoice is not an estimate"


def test_every_override_variable_is_documented_in_env_example():
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    missing = [ln.env_var for ln in ca.declared_lines() if ln.env_var not in text]
    assert missing == [], f"undocumented TORTOISE_COST_* vars: {missing}"


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


class _FakeMeteringPeriod:
    start_iso = "2026-09-01T00:00:00+00:00"
    label = "2026-09"


class _FakeQueryResult:
    def __init__(self, rows):
        self.result_set = rows


class _FakeRegistry:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *_a, **_k):
        return _FakeQueryResult(self._rows)


class _FakeRegistrySDK:
    def __init__(self, rows):
        self._rows = rows

    def _get_registry(self):
        return _FakeRegistry(self._rows)


def _wire_registry_read(monkeypatch, rows):
    monkeypatch.setattr(metering, "_supabase_mode", lambda: False)
    monkeypatch.setattr(
        metering, "_current_period", lambda _org: _FakeMeteringPeriod())
    monkeypatch.setattr(metering, "_reg_sdk", lambda: _FakeRegistrySDK(rows))


def test_measure_write_ops_absent_row_is_a_measured_zero(monkeypatch):
    """No row for the window is a genuine, MEASURED zero."""
    _wire_registry_read(monkeypatch, [])
    assert metering.measure_write_ops("org_a") == 0


def test_measure_write_ops_row_without_a_write_ops_column_is_a_measured_zero(
        monkeypatch):
    """A row merged by ANOTHER lane carries no ``write_ops`` — measured zero.

    The ask, embed and graph-storage writers MERGE their own
    ``MeteringRecord`` and set only their own columns, so an org whose first
    event in the window was an ask/embed call has a row whose ``write_ops`` is
    unset. That is "wrote nothing", not an unreadable read: reading it as a
    failure made ``measure_write_ops`` raise, which fails the WHOLE allocation
    snapshot closed and left ``tortoise_team_cost_cents`` unpublished — the
    exact dead-hook state #4493 exists to fix.
    """
    _wire_registry_read(monkeypatch, [(None,)])
    assert metering.measure_write_ops("org_a") == 0


def test_measure_write_ops_returns_the_measured_count(monkeypatch):
    _wire_registry_read(monkeypatch, [("42",)])
    assert metering.measure_write_ops("org_a") == 42


def test_measure_write_ops_read_failure_raises_and_never_returns_zero(monkeypatch):
    """An unreadable READ must raise — a 0 here would silently redistribute the
    org's share of a fixed cost (the distinction the module exists for)."""
    _wire_registry_read(monkeypatch, [])

    def _boom():
        raise RuntimeError("registry unreachable")

    monkeypatch.setattr(metering, "_reg_sdk", _boom)
    with pytest.raises(RuntimeError):
        metering.measure_write_ops("org_a")


def test_measure_write_ops_negative_count_raises(monkeypatch):
    """A negative count is corruption, not a measurement."""
    _wire_registry_read(monkeypatch, [(-3,)])
    with pytest.raises(ValueError):
        metering.measure_write_ops("org_a")


def test_measured_write_ops_basis_returns_none_when_any_org_is_unreadable(monkeypatch):
    """The fail-closed branch of ``_measured_write_ops_basis``: one unreadable
    org makes the WHOLE basis None, so every proportional line goes unavailable."""
    def _boom(_org_id):
        raise RuntimeError("window unreadable")

    monkeypatch.setattr(metering, "measure_write_ops", _boom)
    assert ha._measured_write_ops_basis(["org_a", "org_b"]) is None


def test_refresh_with_an_unreadable_basis_leaves_the_metric_untouched(monkeypatch):
    """Drives the REAL ``_measured_write_ops_basis`` so its except branch
    executes: one org's read raises, so the proportional lines are
    `unavailable`, no share is emitted, and the metric stays untouched."""
    ph = next(ln for ln in ca.declared_lines() if ln.name == "posthog")
    monkeypatch.setenv(ph.env_var, "100")
    monkeypatch.setattr(
        ha, "_iter_registered_orgs",
        lambda **_kw: [{"org_id": "org_a"}, {"org_id": "org_b"}])

    def _boom(_org_id):
        raise RuntimeError("window unreadable")

    monkeypatch.setattr(metering, "measure_write_ops", _boom)
    asyncio.run(ha._refresh_cost_allocation())

    snap = ca.current_snapshot()
    assert snap is not None and snap.enumeration_available is True
    line = next(ln for ln in snap.lines if ln.line == "posthog")
    assert line.state == ca.STATE_UNAVAILABLE
    assert line.shares == ()
    assert ca.allocation_by_org() == {}, (
        "a refresh whose basis is unreadable must publish nothing")


# ── Constraint (iv)/(v) · the metric, its state, and bounded cardinality ───


def test_publish_sets_the_gauge_and_the_metric_help_names_an_allocation():
    snap = ca.evaluate_allocation(
        ["org_a"], now=datetime(2026, 9, 15, tzinfo=UTC),
        weights_by_org={"org_a": 1})
    ca.publish(snap)
    assert ca.allocation_by_org().get("org_a", 0) > 0
    text = generate_latest().decode()
    assert "tortoise_team_cost_cents" in text
    assert "NOT a measurement" in text, "the help must name the allocation"


def test_last_known_good_is_kept_for_BOTH_unavailable_shapes(monkeypatch):
    """(a) the enumeration failed and (b) the enumeration succeeded but a LINE
    could not be read — the second used to fall through to a partial re-record
    that silently DROPPED every published per-org total. Both must keep the
    last-known-good metric untouched."""
    ph = next(ln for ln in ca.declared_lines() if ln.name == "posthog")
    monkeypatch.setenv(ph.env_var, "100")
    good = ca.evaluate_allocation(
        ["org_a", "org_b"], weights_by_org={"org_a": 3, "org_b": 1})
    assert good.state == ca.STATE_MEASURED
    ca.publish(good)
    before = ca.allocation_by_org()
    assert before, "sanity: last-known-good must be non-empty"

    # (a) the org ENUMERATION is unreadable
    ca.publish(ca.evaluate_allocation([]))
    assert ca.allocation_by_org() == before
    assert ca.current_snapshot().state == ca.STATE_UNAVAILABLE

    # (b) enumeration succeeded, the proportional BASIS could not be read
    partial = ca.evaluate_allocation(["org_a", "org_b"], weights_by_org=None)
    assert partial.state == ca.STATE_UNAVAILABLE
    assert partial.enumeration_available is True
    ca.publish(partial)
    assert ca.allocation_by_org() == before, (
        "a line-unavailable refresh must not re-record a PARTIAL set — the "
        "published totals silently drop")


def test_nonzero_residual_is_published_and_sums_to_the_declared_total(monkeypatch):
    """The residual projection is a real published child: a zero-weight total
    parks in ``__residual__`` and the published set still sums to the declared
    total. Deleting the projection breaks this test."""
    ph = next(ln for ln in ca.declared_lines() if ln.name == "posthog")
    monkeypatch.setenv(ph.env_var, "100")
    snap = ca.evaluate_allocation(["org_a"], weights_by_org={"org_a": 0})
    ca.publish(snap)
    published = ca.allocation_by_org()
    assert published.get(ca.RESIDUAL_ORG) == 100
    declared = sum(ln.total_cents for ln in snap.lines
                   if ln.state != ca.STATE_UNAVAILABLE)
    assert sum(published.values()) == declared


def test_a_successful_refresh_prunes_a_departed_org(monkeypatch):
    """The pruner's removal branch must actually execute.

    Every other publish test runs immediately after the ``_clean_metric``
    autouse fixture cleared the family, so ``keep`` always equals the current
    label set and ``prune_team_cost`` removes nothing. This test publishes a
    GOOD snapshot over two orgs, then a second SUCCESSFUL snapshot over one, so
    the child set legitimately SHRINKS. If the prune is replaced by a no-op,
    ``org_b`` survives at its stale value and the published total no longer
    equals the second snapshot's declared total.
    """
    fly = next(ln for ln in ca.declared_lines() if ln.name == "fly_base")
    ph = next(ln for ln in ca.declared_lines() if ln.name == "posthog")
    monkeypatch.setenv(fly.env_var, "300")
    monkeypatch.setenv(ph.env_var, "100")

    first = ca.evaluate_allocation(
        ["org_a", "org_b"], weights_by_org={"org_a": 3, "org_b": 1})
    assert first.state == ca.STATE_MEASURED
    ca.publish(first)
    assert ca.allocation_by_org().get("org_b", 0) > 0, (
        "sanity: org_b must be published by the first snapshot")

    # Second SUCCESSFUL snapshot: org_b is gone. org_a carries zero weight for
    # the proportional line, so a non-zero residual bucket must survive.
    second = ca.evaluate_allocation(["org_a"], weights_by_org={"org_a": 0})
    assert second.state == ca.STATE_MEASURED
    ca.publish(second)

    published = ca.allocation_by_org()
    # (a) the departed org is gone
    assert "org_b" not in published, (
        "a departed org must be PRUNED from the metric, not left stale")
    # (b) the fixed residual child survives the prune (it is not an org)
    assert published.get(ca.RESIDUAL_ORG, 0) > 0, (
        "the residual bucket is a fixed child, not an org: it must survive")
    # (c) the published set equals the SECOND snapshot's declared total
    declared = sum(ln.total_cents for ln in second.lines
                   if ln.state != ca.STATE_UNAVAILABLE)
    assert sum(published.values()) == declared, (
        "the published set must equal the SECOND snapshot's declared total")


def test_reconcile_reads_the_published_metric_and_can_fire(caplog):
    """The reconciliation compares the PUBLISHED metric, not the snapshot's own
    shares (summing those made the mismatch branch unreachable — both sides came
    from the same sum-exact function). A divergent metric value must WARN."""
    snap = ca.evaluate_allocation(["org_a"], weights_by_org={"org_a": 1})
    ca.publish(snap)
    monitoring.record_cost("unexpected_extra", 123)  # diverge the metric
    with caplog.at_level("WARNING", logger="tortoise.cost_allocation"):
        ca._reconcile_and_log(snap)
    assert any("does NOT reconcile" in r.message for r in caplog.records), (
        caplog.text)


def test_reconcile_fires_when_the_published_metric_under_publishes(caplog):
    """The reconciliation must catch a LOST share, not only an extra child.

    The over-publish test above covers ``published > declared``; on its own it
    leaves the guard satisfiable by ``if published > declared``, which silently
    disables detection of an UNDER-publish — the direction an allocation bug
    actually takes (a dropped or lost share collapsing the published sum below
    the declared total). Publish a good snapshot over two orgs, remove one
    published child, and assert the warning still fires.
    """
    snap = ca.evaluate_allocation(
        ["org_a", "org_b"], weights_by_org={"org_a": 1, "org_b": 1})
    assert snap.state == ca.STATE_MEASURED
    ca.publish(snap)

    declared = sum(ln.total_cents for ln in snap.lines)
    published = ca.allocation_by_org()
    assert declared > 0, "sanity: the declared total must be non-zero"
    assert published.get("org_b", 0) > 0, (
        "sanity: org_b must be published by the good snapshot")

    # Drop ONE published child: the sum now falls BELOW the declared total.
    monitoring.prune_team_cost(set(published) - {"org_b"})
    under = ca.allocation_by_org()
    assert sum(under.values()) < declared, (
        "sanity: the metric must now be UNDER-published")

    caplog.clear()
    with caplog.at_level("WARNING", logger="tortoise.cost_allocation"):
        ca._reconcile_and_log(snap)
    warnings = [r for r in caplog.records
                if "does NOT reconcile" in r.getMessage()]
    assert warnings, "an under-published metric must warn too"
    assert f"delta={sum(under.values()) - declared}" in warnings[0].getMessage(), (
        warnings[0].getMessage())


def test_reconcile_is_silent_when_the_published_metric_matches(caplog):
    snap = ca.evaluate_allocation(["org_a"], weights_by_org={"org_a": 1})
    ca.publish(snap)
    with caplog.at_level("WARNING", logger="tortoise.cost_allocation"):
        ca._reconcile_and_log(snap)
    assert not any("does NOT reconcile" in r.message for r in caplog.records), (
        caplog.text)


def test_reconcile_log_names_the_window_the_published_values_belong_to(caplog):
    """The INFO line must distinguish the ATTEMPTED window from the window the
    values read back from the metric belong to. When the attempt lands in the
    SAME window as the last successful publish the two agree; when it lands in a
    LATER window against a retained earlier publish they must DIFFER. A field
    that simply echoed the attempted window would satisfy the equal case alone
    and would be indistinguishable from a duplicate."""
    snap = ca.evaluate_allocation(
        ["org_a"], now=datetime(2026, 9, 15, tzinfo=UTC),
        weights_by_org={"org_a": 1})
    ca.publish(snap)
    with caplog.at_level("INFO", logger="tortoise.cost_allocation"):
        ca._reconcile_and_log(snap)
    msg = next(r.getMessage() for r in caplog.records
               if "kind=allocation" in r.getMessage())
    assert f"attempted_window={snap.window_start}..{snap.window_end}" in msg, msg
    assert f"published_window={snap.window_start}..{snap.window_end}" in msg, msg

    # DIVERGENT case: an October attempt against the retained September window.
    # ``publish`` is deliberately NOT called for the October snapshot, so the
    # metric still carries September and the two windows must differ in the log.
    caplog.clear()
    october = ca.evaluate_allocation([], now=datetime(2026, 10, 3, tzinfo=UTC))
    assert october.window_start != snap.window_start
    with caplog.at_level("INFO", logger="tortoise.cost_allocation"):
        ca._reconcile_and_log(october)
    msg = next(r.getMessage() for r in caplog.records
               if "kind=allocation" in r.getMessage())
    assert f"attempted_window={october.window_start}..{october.window_end}" in msg, (
        msg)
    assert f"published_window={snap.window_start}..{snap.window_end}" in msg, msg


def test_reconcile_log_marks_the_never_published_window_unknown(caplog):
    """The no-prior-publish path: with nothing ever published the retained
    window is the literal placeholder, so the INFO line must report
    ``published_window=unknown..unknown`` — NOT echo the attempted window into
    the published slot, which would attribute a figure that does not exist to
    the period that was attempted (the misstatement the field exists to
    prevent)."""
    # The autouse ``_clean_metric`` fixture leaves ``_last_published_window``
    # at None, so this is a genuine first-ever refresh.
    attempted = ca.evaluate_allocation(
        [], now=datetime(2026, 10, 3, tzinfo=UTC))
    assert attempted.state == ca.STATE_UNAVAILABLE
    with caplog.at_level("INFO", logger="tortoise.cost_allocation"):
        ca._reconcile_and_log(attempted)
    msg = next(r.getMessage() for r in caplog.records
               if "kind=allocation" in r.getMessage())
    assert "published_window=unknown..unknown" in msg, msg
    assert (
        f"published_window={attempted.window_start}..{attempted.window_end}"
        not in msg
    ), msg


def test_unavailable_warning_names_the_retained_window_not_the_attempted_one(caplog):
    """Across a month rollover the metric still holds September's values while
    the refresh attempts October. The warning must name SEPTEMBER — the window
    the metric ACTUALLY carries — and must not imply October's numbers were
    published."""
    good = ca.evaluate_allocation(
        ["org_a"], now=datetime(2026, 9, 15, tzinfo=UTC),
        weights_by_org={"org_a": 1})
    ca.publish(good)
    assert good.window_start.startswith("2026-09")

    unavailable = ca.evaluate_allocation([], now=datetime(2026, 10, 3, tzinfo=UTC))
    assert unavailable.window_start.startswith("2026-10")
    with caplog.at_level("WARNING", logger="tortoise.cost_allocation"):
        ca.publish(unavailable)
    msg = next(r.getMessage() for r in caplog.records
               if "unavailable" in r.getMessage()
               and "last-known-good" in r.getMessage())
    # Framing, capitalization-INSENSITIVE: the retained window is named as the
    # published one (the PREVIOUS successful window) and the attempted window as
    # NOT published. Asserting on the lowercase text pins the framing without
    # coupling to the message's capitalization; without these two a warning that
    # named the retained window while implying the attempt WAS published would
    # still pass — the misstatement the field exists to prevent.
    lower = msg.lower()
    assert "previous successful window" in lower, msg
    assert "not published" in lower, msg
    assert good.window_start in msg and good.window_end in msg, msg


def test_org_labels_are_bounded_with_a_fixed_overflow_child(monkeypatch):
    monkeypatch.setattr(ca, "MAX_ORG_LABELS", 2)
    orgs = ["org_1", "org_2", "org_3", "org_4"]
    snap = ca.evaluate_allocation(orgs, weights_by_org={o: 1 for o in orgs})
    ca.publish(snap)
    labels = ca.allocation_by_org()
    assert ca.ORG_OVERFLOW in labels
    assert len([k for k in labels if k != ca.ORG_OVERFLOW]) == 2
    # ``publish`` deliberately omits unavailable lines, so the declared total
    # it is expected to sum to is over the NON-unavailable lines — matching
    # ``_reconcile_and_log``.
    declared = sum(ln.total_cents for ln in snap.lines
                   if ln.state != ca.STATE_UNAVAILABLE)
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


def test_production_path_publishes_a_nonzero_value_for_a_real_org(
    monkeypatch, caplog
):
    """Drives the REAL production coroutine — not the metric object, not the
    allocation helper in isolation. This is the assertion indicator 1 asks for
    ('a test asserts the production call site, not just the counter object').

    It also pins the PRODUCTION COMPOSITION inside
    ``cost_allocation.refresh_and_publish``: ``publish`` must run BEFORE
    ``_reconcile_and_log``. Swapping those two statements leaves the
    helper-level window tests green (they call ``_reconcile_and_log``
    directly) while a first-ever refresh then logs
    ``published_window=unknown..unknown`` and warns ``does NOT reconcile``
    about a set that is in fact correct — the stale-figure misstatement the
    ``published_window`` field exists to prevent. This is a first-ever refresh
    (the autouse ``_clean_metric`` fixture leaves the tracker at None), so the
    swapped order cannot hide behind a prior publish.
    """
    monkeypatch.setattr(
        ha, "_iter_registered_orgs",
        lambda **_kw: [{"org_id": "org_real_1"}, {"org_id": "org_real_2"}])
    monkeypatch.setattr(
        ha, "_measured_write_ops_basis", lambda orgs: {o: 1 for o in orgs})

    with caplog.at_level("INFO", logger="tortoise.cost_allocation"):
        asyncio.run(ha._refresh_cost_allocation())

    published = ca.allocation_by_org()
    assert published, "the production path published nothing"
    assert published.get("org_real_1", 0) > 0
    snap = ca.current_snapshot()
    assert snap is not None and snap.state == "measured"
    assert snap.to_dict()["kind"] == "allocation"
    # The INFO line for THIS refresh must name the window its published values
    # belong to — the snapshot's own window, since this refresh published.
    msg = next(r.getMessage() for r in caplog.records
               if "kind=allocation" in r.getMessage())
    assert (
        f"attempted_window={snap.window_start}..{snap.window_end}" in msg
    ), msg
    assert (
        f"published_window={snap.window_start}..{snap.window_end}" in msg
    ), msg
    assert not any("does NOT reconcile" in r.getMessage()
                   for r in caplog.records), caplog.text


def test_event_retention_loop_awaits_the_cost_refresh():
    """The leg must live INSIDE the hourly ``while True:`` body — a call placed
    anywhere else fires once per process.

    This pins the SHAPE; it is no longer the only pin, because #5381 moved the
    loop body out of the ``_lifespan`` closure so it can be EXECUTED by
    ``test_a_raising_step_cannot_kill_the_retention_loop`` below. That runtime
    test is the stronger guard: it observes the steps actually running on a
    second iteration.

    #5381 also routes every step through the shared ``_guarded_step`` guard, so
    the refresh is an ARGUMENT to an awaited guard rather than a bare
    ``await _refresh_cost_allocation()``. This asserts the invariant that
    spelling encoded, and more: every top-level statement of the periodic body
    is an awaited call, every one of them bar the sleep goes through
    ``_guarded_step``, and all four known steps are guarded.

    The DIRECT statements of the body are inspected, not ``ast.walk`` results,
    so a step parked in a nested def that is never invoked does not count as
    scheduled — one of the regressions the original spelling caught.
    ``test_a_raising_step_cannot_kill_the_retention_loop`` below is the other
    half: it EXECUTES the loop. This half is what no runtime test can see —
    that no step sits outside the guard.
    """
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

    def _guarded_call(stmt: ast.stmt) -> tuple[str, str, str] | None:
        """``(label, step, form)`` for one guarded body statement, else ``None``.

        ``form`` is ``"offload"`` when the step is scheduled as
        ``functools.partial(run_on_daemon_worker, fn, ...)`` and ``"direct"``
        when the callee is already async. The distinction is load-bearing: the
        three sync sweeps MUST go through the offload (#2850/#2953) or their
        whole-fleet DB work runs ON the event loop and the guard then swallows
        an ``await None`` TypeError every interval.
        """
        if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await)
                and isinstance(stmt.value.value, ast.Call)):
            return None
        call = stmt.value.value
        if not (isinstance(call.func, ast.Name)
                and call.func.id == "_guarded_step" and len(call.args) >= 2):
            return None
        label, step = call.args[0], call.args[1]
        if not (isinstance(label, ast.Constant) and isinstance(label.value, str)):
            return None
        if isinstance(step, ast.Name):
            return label.value, step.id, "direct"
        if (isinstance(step, ast.Call)
                and isinstance(step.func, ast.Attribute)
                and step.func.attr == "partial"
                and len(step.args) >= 2
                and isinstance(step.args[0], ast.Name)
                and step.args[0].id == "run_on_daemon_worker"
                and isinstance(step.args[1], ast.Name)):
            return label.value, step.args[1].id, "offload"
        return None

    body: list[tuple[str, str, str]] = []
    slept = 0
    for stmt in while_loop.body:
        call = stmt.value.value if (
            isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await)
            and isinstance(stmt.value.value, ast.Call)) else None
        if (call is not None
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "sleep"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "asyncio"):
            slept += 1
            continue  # the interval wait, not a step
        guarded = _guarded_call(stmt)
        assert guarded is not None, (
            "every statement of the periodic body must be an awaited "
            "`_guarded_step(<label>, <step>)` call, so no step can sit outside "
            "the guard (#5381): " + ast.unparse(stmt))
        body.append(guarded)

    # Without the interval wait the loop is a 100% CPU busy-spin hammering the
    # DB every pass — worse than the defect above, and no other assertion here
    # would notice its removal. Only the STATEMENT is pinned, not the argument:
    # `sleep(0)` would still spin, and is left unpinned because nothing has
    # ever produced it in production.
    assert slept == 1, (
        "the periodic body must contain exactly one `await asyncio.sleep(...)` "
        f"statement (found {slept})")

    # ORDERED, label-paired, AND form-checked: a set of names accepted the same
    # four steps bound to the wrong call sites, and accepting a bare Name for a
    # sync sweep accepted dropping the offload entirely.
    expected = [("event retention sweep", "_sweep_events", "offload"),
                ("deleted-team purge", "_purge_deleted_orgs", "offload"),
                # #4029: the account-erasure step. Registered here because the
                # pin exists to force a new periodic step to be DELIBERATE —
                # and it must appear in the same relative order as the boot
                # path's tuple in ``_run_boot_sweeps``.
                ("deleted-account purge", "_purge_deleted_accounts", "offload"),
                ("oauth retention", "_sweep_oauth_retention", "offload"),
                ("cost allocation refresh", "_refresh_cost_allocation", "direct")]
    assert body == expected, (
        "the periodic body must guard exactly these steps, in order, each sync "
        "sweep scheduled through `run_on_daemon_worker`; a step outside the "
        f"guard can kill the loop (#5381). got={body} expected={expected}")

    # The ARMED callable must be the guarded module-level loop. As a closure the
    # pinned and armed functions were necessarily the same object; extracting it
    # decoupled them, so arming an unguarded local copy would leave every
    # assertion above inspecting dead code.
    assert len([n for n in tree.body
                if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "_event_retention_loop"]) == 1, (
        "`_event_retention_loop` must be defined ONCE at module level")
    lifespan = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "_lifespan"), None)
    assert lifespan is not None, "_lifespan not found"
    assert not [n for n in ast.walk(lifespan)
                if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "_event_retention_loop"], (
        "`_lifespan` must not redefine `_event_retention_loop` — the armed "
        "callable must be the guarded module-level loop (#5381)")
    assert [n for n in ast.walk(lifespan)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "create_task"
            and n.args and isinstance(n.args[0], ast.Call)
            and isinstance(n.args[0].func, ast.Name)
            and n.args[0].func.id == "_event_retention_loop"], (
        "`_lifespan` must arm the module-level `_event_retention_loop` through "
        "`create_task(...)` (#5381)")

    # The guard must swallow ANY exception. Sampling one class at runtime cannot
    # catch a narrowed clause: a test raising RuntimeError still passes against
    # `except (ValueError, RuntimeError)`, which re-opens #5381 for every real
    # DB/driver error. Pin the width itself.
    guard = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "_guarded_step"), None)
    assert guard is not None, "_guarded_step not found"
    assert any(isinstance(h.type, ast.Name) and h.type.id == "Exception"
               for t in ast.walk(guard) if isinstance(t, ast.Try)
               for h in t.handlers), (
        "`_guarded_step` must catch a bare `Exception`: a narrower clause "
        "re-opens #5381 for every class it misses")


def test_a_raising_step_cannot_kill_the_retention_loop(monkeypatch, caplog):
    """#5381: drive the REAL loop, not the step.

    ``_event_retention_loop`` had NO per-iteration guard, so any step that
    raised ended event retention AND the deleted-team purge for the life of the
    process. The invariant is "the loop keeps iterating after a step raises",
    and the only evidence for it is a SUBSEQUENT iteration actually running.

    The sibling test below calls ``_refresh_cost_allocation()`` DIRECTLY and
    never executes the loop — a bare no-op step passes it — so the loop's own
    invariant was never asserted by anything.
    """
    calls = {"sweep": 0, "purge": 0}

    def _boom(*_a, **_k):
        calls["sweep"] += 1
        raise RuntimeError("sweep exploded")

    def _purge(*_a, **_k):
        calls["purge"] += 1

    async def _noop(*_a, **_k):
        return None

    async def _inline(fn, *, name, timeout=None):  # no threads in this test
        return fn()

    monkeypatch.setattr(ha, "run_on_daemon_worker", _inline)
    monkeypatch.setattr(ha, "_sweep_events", _boom)
    monkeypatch.setattr(ha, "_purge_deleted_orgs", _purge)
    monkeypatch.setattr(ha, "_sweep_oauth_retention", lambda *_a, **_k: None)
    # The 4th step is the one the sibling test owns; keep this test focused.
    monkeypatch.setattr(ha, "_refresh_cost_allocation", _noop)

    async def _drive():
        task = asyncio.ensure_future(ha._event_retention_loop(0))
        try:
            for _ in range(500):
                if calls["sweep"] >= 2 and calls["purge"] >= 1:
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                # The loop task DIED — the defect under test. Swallowed here so
                # the assertion below reports it with a useful message instead
                # of an opaque escaped RuntimeError.
                pass

    with caplog.at_level("WARNING", logger="tortoise.hosted_api"):
        asyncio.run(_drive())

    assert calls["sweep"] >= 2, (
        "the loop died on the first raising step — it must iterate AGAIN "
        f"(sweep ran {calls['sweep']}x)")
    assert calls["purge"] >= 1, (
        "a raising step skipped its SIBLINGS — the guard must be per STEP, not "
        "per iteration")
    assert any("sweep exploded" in r.getMessage() for r in caplog.records), (
        "the swallowed failure must be LOGGED, not silently dropped: "
        + caplog.text)


def test_guarded_step_does_not_swallow_cancellation():
    """#5381: the guard must contain FAILURES without containing SHUTDOWN.

    ``_stop_liveness`` cancels these tasks and then `gather`s them with no
    timeout, so a step that swallowed ``CancelledError`` would hang shutdown.
    ``CancelledError`` derives from ``BaseException`` on 3.8+, so it was never
    caught by ``except Exception`` — but nothing else here would notice if the
    two clauses were ever collapsed into ``except BaseException``.
    """
    async def _cancelled():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ha._guarded_step("cancelled step", _cancelled))


def test_a_failing_refresh_cannot_kill_the_retention_loop(monkeypatch, caplog):
    def _boom(*_a, **_k):
        raise RuntimeError("allocator exploded")

    monkeypatch.setattr(ca, "refresh_and_publish", _boom)
    monkeypatch.setattr(
        ha, "_iter_registered_orgs", lambda **_kw: [{"org_id": "o"}])
    # No real metering/DB round trip: this test verifies ONLY the swallow path.
    monkeypatch.setattr(
        ha, "_measured_write_ops_basis", lambda orgs: {o: 1 for o in orgs})
    # The refresh must swallow its own failure. Since #5381 the loop's
    # `_guarded_step` also contains a raise, making this the inner of two
    # layers rather than the only one: this must NOT raise.
    with caplog.at_level("WARNING", logger="tortoise.hosted_api"):
        asyncio.run(ha._refresh_cost_allocation())
    # ... and the failure must have been OBSERVED and swallowed, not silently
    # dropped: a bare no-op ``_refresh_cost_allocation`` used to pass this test.
    assert any("cost allocation refresh failed" in r.getMessage()
               and "allocator exploded" in r.getMessage()
               for r in caplog.records), caplog.text


def test_an_unconfirmed_empty_enumeration_fails_closed(monkeypatch):
    """``_iter_registered_orgs`` returns [] on ANY failure, so [] is 'unknown',
    not 'no orgs'. It must never be published as a fleet-wide zero."""
    monkeypatch.setattr(ha, "_iter_registered_orgs", lambda **_kw: [])
    asyncio.run(ha._refresh_cost_allocation())
    snap = ca.current_snapshot()
    assert snap is not None and snap.state == "unavailable"
    assert ca.allocation_by_org() == {}


class _FakeControlPlane:
    """Supabase control-plane fake returning one fixed page for ``query``."""

    def __init__(self, rows):
        self._rows = rows
        self.limit_seen = None

    def query(self, _table, **kw):
        self.limit_seen = kw.get("limit")
        return self._rows


def _cap_rows():
    return [{"id": f"org_{i}", "name": None}
            for i in range(ha._ORG_ENUMERATION_MAX_ROWS)]


class _FakeSweepSDK:
    def _get_proj(self):
        return object()


def test_truncated_supabase_org_enumeration_fails_closed_for_the_cost_caller(monkeypatch):
    """#4493/#5388: ``query`` cannot distinguish a complete page from a
    truncated one, so a caller that needs the WHOLE fleet passes
    ``require_complete=True`` and gets ``None`` when the page FILLS the limit
    (a partial fleet must never prune orgs from the published metric)."""
    from tortoise import supabase_control as sc

    cp = _FakeControlPlane(_cap_rows())
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)

    assert ha._iter_registered_orgs(require_complete=True) is None
    assert cp.limit_seen == ha._ORG_ENUMERATION_MAX_ROWS, (
        "the enumeration must request an explicit limit — without one the "
        "server's db-max-rows truncation is invisible")


def test_a_filled_page_is_still_returned_to_a_best_effort_caller(monkeypatch):
    """The OTHER production caller — the fleet-wide event-retention sweep —
    must process the page it received. Encoding "incomplete" as "[]" silently
    turned retention into a no-op for the whole fleet at >=1000 orgs."""
    from tortoise import supabase_control as sc

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(
        sc, "get_control_plane", lambda: _FakeControlPlane(_cap_rows()))

    rows = ha._iter_registered_orgs()
    assert rows is not None, "a best-effort caller must still get its page"
    assert len(rows) == ha._ORG_ENUMERATION_MAX_ROWS
    assert rows[0] == {"org_id": "org_0", "name": None}


def test_retention_sweep_processes_a_full_page_at_the_cap(monkeypatch):
    """Behavioural pin for the sweep caller at the cap: it must actually sweep
    the orgs in the page it received — the regression the shared helper's
    ``[]``-on-truncation caused."""
    from tortoise import event_store
    from tortoise import supabase_control as sc

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(
        sc, "get_control_plane", lambda: _FakeControlPlane(_cap_rows()))
    monkeypatch.setattr(ha, "_make_sdk", lambda **_kw: _FakeSweepSDK())
    swept: list[str] = []
    monkeypatch.setattr(
        event_store, "purge_expired",
        lambda _proj, **_kw: swept.append("expired"))
    monkeypatch.setattr(
        event_store, "purge_overflow",
        lambda _proj, **_kw: swept.append("overflow"))

    ha._sweep_events()

    assert swept.count("expired") == ha._ORG_ENUMERATION_MAX_ROWS, (
        "the retention sweep must sweep the page it received, not nothing")
    assert swept.count("overflow") == ha._ORG_ENUMERATION_MAX_ROWS


def test_cost_refresh_at_the_enumeration_cap_keeps_last_known_good(monkeypatch):
    """The cost caller at the cap: a possibly-truncated page is UNKNOWN, so the
    refresh must leave the metric at last-known-good (never prune orgs beyond
    the page).

    The basis read is made READABLE on purpose. Left to the real
    ``_measured_write_ops_basis`` (which returns ``None`` without a DB), the
    proportional lines would be ``unavailable`` regardless of whether the
    enumeration was treated as complete — so the snapshot would be
    ``unavailable`` for the WRONG reason and the test would stay green even if
    the ``require_complete=True`` wiring were removed. With a readable basis the
    ONLY possible reason for ``unavailable`` is the unconfirmed enumeration
    (mutation: switching the caller to ``require_complete=False`` yields a
    ``measured`` snapshot whose pruner drops ``org_known``).
    """
    from tortoise import supabase_control as sc

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(
        sc, "get_control_plane", lambda: _FakeControlPlane(_cap_rows()))
    monkeypatch.setattr(
        ha, "_measured_write_ops_basis", lambda orgs: {o: 1 for o in orgs})

    good = ca.evaluate_allocation(["org_known"], weights_by_org={"org_known": 1})
    ca.publish(good)
    before = ca.allocation_by_org()
    assert before, "sanity: last-known-good must be non-empty"

    asyncio.run(ha._refresh_cost_allocation())

    assert ca.allocation_by_org() == before, (
        "an at-cap (possibly truncated) enumeration must leave the metric "
        "untouched — never prune orgs beyond the page")
    snap = ca.current_snapshot()
    assert snap is not None
    assert snap.enumeration_available is False, (
        "the completeness signal is the reason this snapshot is unavailable")
    assert snap.state == ca.STATE_UNAVAILABLE


def test_short_supabase_org_enumeration_is_returned(monkeypatch):
    from tortoise import supabase_control as sc

    class _FakeCP:
        def query(self, _table, **_kw):
            return [{"id": "org_a", "name": "A"}]

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: _FakeCP())
    expected = [{"org_id": "org_a", "name": "A"}]
    assert ha._iter_registered_orgs() == expected
    assert ha._iter_registered_orgs(require_complete=True) == expected


# ── Constraint (vii) + single writer · invariants that are ENFORCED ────────


def test_allocation_never_touches_the_measured_ledger():
    """#3665: ``metering_records`` holds MEASURED metres and the cohort ceiling
    sums them. An allocation landing there would corrupt the cap, so the
    separation is asserted, not just stated."""
    src = (REPO / "tortoise" / "cost_allocation.py").read_text(encoding="utf-8")
    for forbidden in ("MeteringRecord", "record_ask_usage",
                      "record_capture_usage", "record_write_ops"):
        assert forbidden not in src, f"cost_allocation must not reference {forbidden}"


#: Anything that reads/writes the ``TEAM_COST`` family in a way that can
#: mutate it: the metric object itself plus the three mutator helpers. (Readers
#: such as ``team_cost_cents`` are deliberately NOT here — ``_reconcile_and_log``
#: and ``allocation_by_org`` must be able to read the metric.)
_METRIC_NAMES = frozenset(
    {"TEAM_COST", "record_cost", "clear_team_cost", "prune_team_cost"})
#: Functions permitted to touch the metric outside ``monitoring.py``: the single
#: production writer, plus the explicit test seam (which must be able to clear).
_METRIC_WRITER_ALLOWLIST = {
    ("cost_allocation.py", "publish"),
    ("cost_allocation.py", "_reset_for_tests"),
}


def _metric_references(fn) -> set[str]:
    """Names/attributes of the TEAM_COST family referenced inside *fn*.

    AST-based, so a COMMENT or a DOCSTRING mention is not a hit — substring
    matching prose is the same class of defect the guard exists to catch.
    """
    refs: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id in _METRIC_NAMES:
            refs.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in _METRIC_NAMES:
            refs.add(node.attr)
    return refs


def test_publish_is_the_only_writer_of_the_team_cost_metric():
    """Assert on the METRIC, not a function name: a second caller of
    ``clear_team_cost``/``prune_team_cost`` — or a direct ``TEAM_COST`` write —
    must be caught.

    SCOPE (exactly what this guard enforces, and no more): it matches syntactic
    ``Name``/``Attribute`` occurrences of ``TEAM_COST`` and its mutators
    ANYWHERE inside a ``def``/``async def`` subtree in ``tortoise/**/*.py``
    outside ``tortoise/monitoring.py`` — INCLUDING a ``lambda`` or a
    class nested inside a function body, which ``ast.walk`` inspects and
    attributes to that function. Every such reference must sit inside
    ``publish`` (the one production writer) or ``_reset_for_tests`` (the
    explicit test seam). What it does NOT match is module-level and top-level
    class-body references, aliased imports, and ``getattr`` string lookups.
    The skip is implemented by file NAME, not by path: ``tortoise/monitoring.py``
    holds the definitions and must be skipped, and any OTHER file named
    ``monitoring.py`` is exempt for that same name-based reason — e.g.
    ``tortoise/shared_state/monitoring.py``, which holds no team-cost
    definitions at all. That is a disclosed hole: a NEW mutator added to any
    other ``monitoring.py`` would not be caught. The claim is stated at this
    strength, not a broader one, in ``docs/ops/cost-allocation.md``."""
    offenders: dict[str, set[str]] = {}
    for path in (REPO / "tortoise").rglob("*.py"):
        if path.name == "monitoring.py":
            continue  # the definitions themselves
        src = path.read_text(encoding="utf-8")
        # Cheap prefilter so the whole tree is not AST-parsed: a source file
        # cannot reference the family in AST form without the literal token.
        # The VERDICT is still AST-based (this only skips files that cannot
        # possibly match).
        if not any(name in src for name in _METRIC_NAMES):
            continue
        tree = ast.parse(src)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            refs = _metric_references(fn)
            if refs and (path.name, fn.name) not in _METRIC_WRITER_ALLOWLIST:
                offenders.setdefault(path.name, set()).update(refs)
    assert offenders == {}, (
        "unexpected references to TEAM_COST/its mutators outside monitoring.py "
        f"+ the writer allowlist: {offenders} — a second writer would break the "
        "reconciliation invariant")


#: The boot sweeps ``_run_boot_sweeps`` runs, in order, paired with the module
#: attribute the test monkeypatches and the guarded-step label they log under.
#: Stated once so the count is not hardcoded in the assertion: the earlier form
#: said "the remaining two", which the #4029 account-erasure step falsified.
_BOOT_SWEEPS = (
    ("event", "_sweep_events", "boot event retention sweep"),
    ("purge", "_purge_deleted_orgs", "boot deleted-team purge sweep"),
    ("account", "_purge_deleted_accounts", "boot deleted-account purge sweep"),
    ("oauth", "_sweep_oauth_retention", "boot oauth retention sweep"),
)


@pytest.mark.parametrize(
    "raising", [name for name, _attr, _label in _BOOT_SWEEPS])
def test_a_raising_boot_sweep_cannot_kill_the_other_boot_sweeps(
        monkeypatch, caplog, raising):
    """#5381/#7352: drive the REAL boot runner, not the step.

    ``_run_boot_sweeps`` routes each sweep through the same ``_guarded_step`` the
    periodic loop uses ("one runner, so the boot and periodic paths cannot drift
    apart"). Without that guard a raise in one boot sweep abandons every sweep
    after it — they never run at boot at all.

    The invariant is "a later sweep still runs after an earlier one raises", and
    the only evidence for it is a SUBSEQUENT sweep actually running. A test that
    drives a step directly cannot observe it: mutating the guarded call site to a
    bare ``await run_on_daemon_worker(fn, ...)`` left this whole file green, which
    is the gap this test closes.

    Parametrized over EVERY boot sweep so the evidence is per-step: an unguarded
    call site for ANY of them lets its raise escape and leaves the sweeps after
    it unrun. The #4029 ``_purge_deleted_accounts`` step is covered here, which a
    count of the two pre-existing sweeps could not do.
    """
    calls = {name: 0 for name, _attr, _label in _BOOT_SWEEPS}

    def _recorder(name):
        def _fn(*_a, **_k):
            calls[name] += 1
        return _fn

    def _boom(*_a, **_k):
        calls[raising] += 1
        raise RuntimeError("boot sweep exploded")

    async def _inline(fn, *, name, timeout=None):  # no threads in this test
        return fn()

    monkeypatch.setattr(ha, "run_on_daemon_worker", _inline)
    for name, attr, _label in _BOOT_SWEEPS:
        monkeypatch.setattr(
            ha, attr, _boom if name == raising else _recorder(name))

    with caplog.at_level("WARNING", logger="tortoise.hosted_api"):
        asyncio.run(ha._run_boot_sweeps())

    assert calls[raising] == 1, f"the {raising} boot sweep did not run at all"
    for name, _attr, _label in _BOOT_SWEEPS:
        if name == raising:
            continue
        assert calls[name] == 1, (
            f"the {raising} boot sweep raised and the {name} sweep never ran — "
            "that call site is not routed through the shared guard (#5381)")

    label = next(l for n, _a, l in _BOOT_SWEEPS if n == raising)
    assert any(label in r.getMessage() for r in caplog.records), (
        "the guarded boot failure was swallowed without a log record")
