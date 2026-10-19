"""#4495 — the published write-op allowance must be REACHABLE, and that verdict
must never be produced from an absent reading.

The defect: ``product/pricing.json`` publishes a monthly write-op flow and a
permanent node stock cap as two independent entitlements, with no designed
relationship. ``cap / allowance`` is 1.0–3.0 across the published tiers, while a
write op that actually stores something yields tens-to-hundreds of net-new
nodes, so the stock refuses the writes the flow was sold for.

These tests pin three things and change no value:

1. the ARITHMETIC (``pricing.allowance_reachability``) — including the r=0 case,
   which is REACHABLE by design (a dedup re-commit / supersede-only delta adds
   no memory, so it cannot bind the cap), and is NOT the same as absent;
2. the GUARANTEE — ``cap >= allowance * r`` — as an executable claim about
   today's published model. It is marked ``xfail(strict=True)`` because the
   model cannot satisfy it yet: the suite stays GREEN while the defect is open
   and goes RED the moment the numbers are fixed, which is the signal to remove
   the marker deliberately (three hand-derived tables on #4495 disagreed about
   their denominator; an executable claim cannot);
3. the ABSENCE RULE (``metering.get_write_op_yield`` + the tool): no ledger row
   means ``measured=False`` and exit 2 — never a 0 yield, which would read as
   "every tier is fine".
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest

import tortoise.metering as metering
import tortoise.pricing as pricing

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: Two LIVE observed yields, from the refusals recorded on #4495: a hosted
#: capture pre-flight estimate of +549 nodes (2026-09-26) and +48 (2026-10-08).
#: They disagree by an order of magnitude, which is why the guarantee is pinned
#: as a RANGE of r rather than a single figure — the direction survives both.
_OBSERVED_YIELDS = (48.0, 549.0)


class _Period:
    label = "2026-10"
    start_iso = "2026-10-01T00:00:00+00:00"
    end_iso = "2026-11-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _fresh_pricing():
    pricing.reload()
    yield
    pricing.reload()


@pytest.fixture
def synthetic_book(monkeypatch):
    """Point the loader at a temporary price book.

    ``_PRICING_PATH`` is resolved at import time (module constant), so the env
    var cannot redirect it here — the constant must be patched, and the cache
    reloaded, or the test would silently measure the REAL book.
    """
    def _apply(tiers: dict):
        book = {
            "$schema": "pricing-schema-v1",
            "billing": {"overage_unit": "write_ops",
                        "cost_basis": {"bytes_per_node": 1024}},
            "tiers": tiers,
        }
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(book, fh)
        monkeypatch.setattr(pricing, "_PRICING_PATH", path)
        pricing.reload()
        return path
    return _apply


def _tier(allowance: int, cap: int) -> dict:
    return {
        "max_graphs_per_team": 1,
        "max_users_per_team": 1,
        "max_api_keys": 1,
        "included_write_ops_per_month": allowance,
        "max_graph_nodes": cap,
        "overage": False,
    }


class TestArithmetic:
    """The predicate is pinned on synthetic tiers, so it does not depend on
    numbers the owner may change."""

    def test_cap_per_allowance_is_the_reachability_threshold(self, synthetic_book):
        synthetic_book({"t": _tier(allowance=1_000, cap=3_000)})

        at_threshold = pricing.allowance_reachability("t", 3.0)
        assert at_threshold["reachable"] is True
        assert at_threshold["unreachable_share"] == 0.0
        assert at_threshold["reachable_ops"] == 1_000

        above = pricing.allowance_reachability("t", 3.01)
        assert above["reachable"] is False
        assert above["reachable_ops"] < 1_000

    def test_zero_yield_is_reachable_and_not_an_error(self, synthetic_book):
        """A write that adds no memory consumes no stock: r=0 must be REACHABLE
        (dedup re-commit, supersede-only, replay). This is the case that makes
        "the allowance is unreachable" a claim about the documented usage, not
        an absolute one — so the tool must not overstate the defect either."""
        synthetic_book({"t": _tier(allowance=1_000, cap=1_000)})

        r0 = pricing.allowance_reachability("t", 0)
        assert r0["reachable"] is True
        assert r0["reachable_ops"] == 1_000
        assert r0["unreachable_share"] == 0.0
        assert r0["required_cap"] == 0

    def test_unreachable_share_is_the_unusable_fraction(self, synthetic_book):
        """cap=1,000 / allowance=10,000 at r=50: 20 ops fit, so 99.8% of the
        sold allowance is unusable — the exact shape #4495 reports."""
        synthetic_book({"t": _tier(allowance=10_000, cap=1_000)})

        r = pricing.allowance_reachability("t", 50.0)
        assert r["reachable_ops"] == 20
        assert r["unreachable_share"] == pytest.approx(0.998)
        assert r["required_cap"] == 500_000
        assert r["cap_shortfall"] == 499_000
        assert r["reachable"] is False

    @pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
    def test_a_nonsense_yield_raises_rather_than_passing(self, bad):
        with pytest.raises(ValueError):
            pricing.allowance_reachability("free", bad)

    def test_the_exact_boundary_does_not_contradict_itself(self, synthetic_book):
        """At r == cap/allowance the design is reachable, but the float
        comparison is not exact for most pairs. Before the tolerance, 423,323
        of ~8M integer (allowance, cap) pairs reported UNREACHABLE at
        r = cap/allowance while rendering '0.00% unusable' and 'cap needs to be
        0 nodes larger' — a table contradicting its own bottom line. This pins
        the non-binary-exact case (7 and 29)."""
        synthetic_book({"t": _tier(allowance=7, cap=29)})
        r = pricing.allowance_reachability("t", 29 / 7)
        assert r["reachable"] is True
        assert r["unreachable_share"] == 0.0
        assert r["cap_shortfall"] == 0.0
        assert r["reachable_ops"] == 7

    def test_a_tier_without_an_allowance_raises(self, synthetic_book):
        synthetic_book({"t": _tier(allowance=0, cap=1_000)})
        with pytest.raises(ValueError):
            pricing.allowance_reachability("t", 1.0)

    def test_a_null_node_cap_raises_instead_of_reading_as_zero(self,
                                                              synthetic_book):
        """`tier_limits` documents None as unlimited for the OBJECT-COUNT keys,
        but a null node cap is UNRANKABLE (billing.py's own rule: never
        unlimited). Coercing it to 0 would report the maximum possible
        shortfall as a fabricated finding — and the capture gate would
        TypeError on the comparison anyway."""
        tier = _tier(allowance=10_000, cap=0)
        tier["max_graph_nodes"] = None
        synthetic_book({"t": tier})
        with pytest.raises(ValueError, match="max_graph_nodes is null"):
            pricing.allowance_reachability("t", 48.0)


class TestPublishedModel:
    """The GUARANTEE, as an executable claim about today's real price book."""

    @pytest.mark.xfail(
        strict=True,
        reason="#4495: every published cap/allowance ratio is 1.0-3.0, which "
               "supports only a 1-3 node write. A capture/batch commit stores "
               "~48-549. When the ratios are raised this XPASSes (strict) and "
               "the marker must be removed deliberately — do not delete it.",
    )
    def test_every_tier_publishes_a_cap_that_supports_a_batch_write(self):
        """A tier's cap must absorb a write that stores a batch, not just a
        single node. 4 nodes/op is a deliberately LOW bar (a one-sentence
        capture is already estimated at 3 by the capture gate's own
        estimator), so this is the weakest form of the guarantee."""
        short = {
            t: pricing.allowance_reachability(t, 4.0)["cap_per_allowance"]
            for t in pricing.all_tiers()
            if not pricing.allowance_reachability(t, 4.0)["reachable"]
        }
        assert short == {}, (
            f"tiers whose cap/allowance supports < 4 nodes per write: {short}")

    def test_the_shortfall_is_the_calibration_input(self):
        """`required_cap` is what the cap would have to be for the two
        published numbers to be reachable together — the number a calibration
        needs, and the one three hand-derived tables on #4495 got differently.

        Derived from the published allowance, NOT hardcoded: when the owner
        calibrates, this must stay green while the guarantee test flips."""
        for tier in ("free", "solo", "pro", "team"):
            allowance = pricing.tier_limits(tier)[
                "included_write_ops_per_month"]
            for r in _OBSERVED_YIELDS:
                verdict = pricing.allowance_reachability(tier, r)
                assert verdict["required_cap"] == allowance * r
                assert verdict["cap_shortfall"] == pytest.approx(
                    max(0.0, allowance * r - verdict["cap"]))


class TestTierBoundary:
    """r = 1 (a single-node write) must be deliverable at EVERY published tier,
    because every published cap/allowance is >= 1.0. The docstring claimed the
    opposite in round 1 (that only 1.0-ratio tiers could deliver it), which
    would have understated reachability for solo/pro/team."""

    def test_a_one_node_write_is_deliverable_at_every_published_tier(self):
        for tier in pricing.all_tiers():
            verdict = pricing.allowance_reachability(tier, 1.0)
            assert verdict["reachable"] is True, (
                f"{tier} cannot deliver a one-node-per-write usage "
                f"(cap/allowance={verdict['cap_per_allowance']})")

    def test_the_1_0_ratio_tiers_are_the_zero_slack_point(self):
        """A ratio of exactly 1.0 has ZERO slack at r=1: the next node overflows.

        The tier LIST is derived, not pinned: which tiers sit at 1.0 is the
        owner's calibration to change, and hardcoding ``("free", "anon")`` would
        hard-fail the very fix this file exists to signal deliberately. The
        property is already pinned calibration-proof on a synthetic tier in
        ``TestArithmetic.test_cap_per_allowance_is_the_reachability_threshold``;
        this test asserts it on whichever REAL tiers currently sit at 1.0.
        """
        at_zero_slack = [
            t for t in pricing.all_tiers()
            if pricing.allowance_reachability(t, 1.0)["cap_per_allowance"] == 1.0
        ]
        if not at_zero_slack:
            pytest.skip("no published tier sits at a 1.0 cap/allowance ratio")
        for tier in at_zero_slack:
            assert pricing.allowance_reachability(tier, 1.0)["reachable"] is True
            assert pricing.allowance_reachability(
                tier, 1.01)["reachable"] is False


class TestAbsenceIsNotAYield:
    """A missing reading must never report reachability."""

    def test_no_org_is_unmeasured_not_zero(self):
        view = metering.get_write_op_yield("")
        assert view["measured"] is False
        assert view["nodes_per_op"] is None  # ⛔ not 0

    def test_empty_ledger_row_is_unmeasured_not_zero(self, monkeypatch):
        """A row with write_ops=0 (or no row at all) is an ABSENT reading: it
        says nothing about whether the promise holds. Reading it as r=0 would
        report a brand-new org as full reachability."""
        monkeypatch.setattr(metering, "_current_period", lambda org: _Period())
        monkeypatch.setattr(metering, "_supabase_mode", lambda: False)
        monkeypatch.setattr(metering, "_reg_sdk", lambda: _FakeSDK([]))

        view = metering.get_write_op_yield("org_x")
        assert view["measured"] is False
        assert view["nodes_per_op"] is None
        assert view["write_ops"] == 0

    def test_a_real_row_yields_the_ratio(self, monkeypatch):
        monkeypatch.setattr(metering, "_current_period", lambda org: _Period())
        monkeypatch.setattr(metering, "_supabase_mode", lambda: False)
        monkeypatch.setattr(metering, "_reg_sdk",
                            lambda: _FakeSDK([(3, 144)]))

        view = metering.get_write_op_yield("org_x")
        assert view["measured"] is True
        assert view["nodes_per_op"] == pytest.approx(48.0)
        assert view["nodes_attribution"] == "recorded"

    def test_billed_writes_with_no_recorded_yield_are_UNATTRIBUTED(
            self, monkeypatch):
        """⛔ The P1 (review round 1). Three write paths bill an op and leave
        ``nodes_written`` at its 0 default, so ``write_ops > 0, nodes_written
        == 0`` cannot be read as "this org consumes no stock" — that reports
        the exact org whose yield went unrecorded as FULLY REACHABLE, and it is
        the absence-as-green failure the reader exists to prevent."""
        monkeypatch.setattr(metering, "_current_period", lambda org: _Period())
        monkeypatch.setattr(metering, "_supabase_mode", lambda: False)
        monkeypatch.setattr(metering, "_reg_sdk", lambda: _FakeSDK([(37, 0)]))

        view = metering.get_write_op_yield("org_mcp_only")
        assert view["measured"] is False, (
            "a zero recorded yield must not be presented as a measurement")
        assert view["nodes_per_op"] is None  # ⛔ never 0
        assert view["nodes_attribution"] == "unattributed"
        assert view["write_ops"] == 37  # the ops ARE reported — they were billed

    def test_a_read_failure_is_not_reported_as_an_absence(self, monkeypatch):
        """A failed READ must not send an operator looking for writes that may
        exist."""
        monkeypatch.setattr(metering, "_current_period", lambda org: _Period())
        monkeypatch.setattr(metering, "_supabase_mode", lambda: False)

        def boom():
            raise RuntimeError("ledger unreachable")

        monkeypatch.setattr(metering, "_reg_sdk", boom)
        view = metering.get_write_op_yield("org_x")
        assert view["measured"] is False
        assert view["nodes_attribution"] == "read_error"

    def test_the_supabase_lane_reads_the_same_counters(self, monkeypatch):
        """Both lanes must agree — the Supabase path is the LIVE one."""
        from tortoise.supabase_control import (
            metering_get_write_op_counters,
        )

        calls = {}

        class _CP:
            def query(self, table, select=None, filters=None):
                calls.update(table=table, select=select, filters=filters)
                return [{"write_ops": 3, "nodes_written": 144}]

        got = metering_get_write_op_counters(_CP(), "org_x",
                                             _Period.start_iso)
        assert got == {"write_ops": 3, "nodes_written": 144}
        assert calls["table"] == "metering_records"
        assert "nodes_written" in calls["select"]
        # #3825: keyed on the window START, never the derived month label.
        assert ("period_start", "eq", _Period.start_iso) in calls["filters"]

    def test_the_supabase_branch_is_wired_with_the_window_start(
            self, monkeypatch):
        """The LIVE lane's wiring: `get_write_op_yield` must pass the window
        START (never the derived month label — the #3825 bug class) and must
        normalise the row through the same attribution rule."""
        import tortoise.supabase_control as sc

        seen = {}

        class _CP:
            def query(self, table, select=None, filters=None):
                seen.update(filters=filters)
                return [{"write_ops": 3, "nodes_written": 144}]

        monkeypatch.setattr(metering, "_current_period", lambda org: _Period())
        monkeypatch.setattr(metering, "_supabase_mode", lambda: True)
        monkeypatch.setattr(sc, "get_control_plane", lambda: _CP())

        view = metering.get_write_op_yield("org_x")
        assert view["measured"] is True
        assert view["nodes_per_op"] == pytest.approx(48.0)
        assert view["nodes_attribution"] == "recorded"
        assert ("period_start", "eq", _Period.start_iso) in seen["filters"]

    def test_a_window_fault_is_not_reported_as_an_absent_row(
            self, monkeypatch):
        """`_current_period` raises deliberately for a half-known subscription
        anchor (#3981) — a different fault from an unreadable ledger."""
        def boom(org):
            raise RuntimeError("unresolvable anchor")

        monkeypatch.setattr(metering, "_current_period", boom)
        view = metering.get_write_op_yield("org_x")
        assert view["measured"] is False
        assert view["nodes_attribution"] == "period_error"

    def test_an_absent_supabase_row_is_zeros(self):
        from tortoise.supabase_control import (
            metering_get_write_op_counters,
        )

        class _CP:
            def query(self, *a, **kw):
                return []

        assert metering_get_write_op_counters(
            _CP(), "org_x", _Period.start_iso) == {
                "write_ops": 0, "nodes_written": 0}


class _FakeSDK:
    """Just enough registry to serve one MeteringRecord row."""

    def __init__(self, row):
        self._row = row

    def _get_registry(self):
        row = self._row

        class _Reg:
            def query(self, *a, **kw):
                class _R:
                    result_set = row
                return _R()

        return _Reg()


class TestToolContract:
    """The CLI's exit codes ARE the finding: 2 must never be confusable with 0."""

    @staticmethod
    def _tool():
        path = _REPO_ROOT / "tools" / "write_op_reachability.py"
        spec = importlib.util.spec_from_file_location("wor_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_stated_unreachable_yield_exits_1_on_a_synthetic_book(
            self, synthetic_book):
        """The exit-code CONTRACT, pinned on a synthetic book.

        Deliberately not the live book: the calibration the xfail above
        anticipates would make the real model reachable, and a test that pins
        today's verdict would then hard-fail with no deliberate signal.
        """
        synthetic_book({"t": _tier(allowance=1_000, cap=1_000)})
        mod = self._tool()
        assert mod.main(["--nodes-per-op", "2", "--tier", "t"]) == 1
        assert mod.main(["--nodes-per-op", "1", "--tier", "t"]) == 0

    def test_hypothetical_zero_yield_exits_0_and_says_reachable(self, capsys):
        """The degenerate usage IS reachable — the tool must not overstate."""
        assert self._tool().main(["--nodes-per-op", "0"]) == 0
        assert "VERDICT: REACHABLE" in capsys.readouterr().out

    def test_no_input_exits_2_and_is_not_a_pass(self):
        with pytest.raises(SystemExit) as exc:
            self._tool().main([])
        assert exc.value.code == 2

    def test_unmeasured_org_exits_2(self, monkeypatch, capsys):
        """⛔ The absence rule at the CLI boundary: exit 2, never 0."""
        mod = self._tool()
        monkeypatch.setattr(metering, "get_write_op_yield", lambda org: {
            "write_ops": 0, "nodes_written": 0, "nodes_per_op": None,
            "measured": False, "nodes_attribution": "absent",
            "period": "2026-10", "period_start": None, "period_end": None,
        })
        assert mod.main(["--org", "org_x", "--accept-schema-writes"]) == 2
        assert "NOT MEASURABLE" in capsys.readouterr().err

    def test_unattributed_yield_exits_2_and_names_the_cause(
            self, monkeypatch, capsys):
        """The P1 driven end-to-end: 37 billed ops with no recorded yield must
        NOT print VERDICT: REACHABLE."""
        mod = self._tool()
        monkeypatch.setattr(metering, "get_write_op_yield", lambda org: {
            "write_ops": 37, "nodes_written": 0, "nodes_per_op": None,
            "measured": False, "nodes_attribution": "unattributed",
            "period": "2026-10", "period_start": None, "period_end": None,
        })
        assert mod.main(["--org", "org_x", "--accept-schema-writes"]) == 2
        captured = capsys.readouterr()
        assert "NOT MEASURABLE" in captured.err
        assert "nodes_written=0" in captured.err
        assert "REACHABLE" not in captured.out

    @pytest.mark.parametrize("bad", ["nan", "inf", "-1"])
    def test_an_unrankable_yield_exits_2_never_1(self, bad):
        """exit 1 means "a measurement CONFIRMED the defect" — a bad input or a
        crash must never be reported as that finding."""
        with pytest.raises(SystemExit) as exc:
            self._tool().main(["--nodes-per-op", bad])
        assert exc.value.code == 2

    def test_registry_mode_refuses_org_without_accepting_schema_writes(
            self, monkeypatch):
        """The read-only boundary, as tools/edge_census.py established it: the
        registry lane opens the SDK and its projection constructor runs
        idempotent index DDL."""
        mod = self._tool()
        monkeypatch.setattr(metering, "_supabase_mode", lambda: False)
        with pytest.raises(SystemExit) as exc:
            mod.main(["--org", "org_x"])
        assert exc.value.code == 2

    def test_measured_reachable_is_UNCERTIFIED_not_a_pass(self, monkeypatch,
                                                         capsys):
        """⛔ The round-2 P1. A measured ``r`` is a LOWER BOUND, so a measured
        REACHABLE certifies nothing: diluting one recorded node into 1,000
        billed ops collapses ``r`` to 0.001 and would otherwise read green.
        Exit 0 is reserved for a STATED yield."""
        mod = self._tool()
        monkeypatch.setattr(metering, "get_write_op_yield", lambda org: {
            "write_ops": 1_000, "nodes_written": 1, "nodes_per_op": 0.001,
            "measured": True, "nodes_attribution": "recorded",
            "period": "2026-10", "period_start": None, "period_end": None,
        })
        assert mod.main(["--org", "org_x", "--accept-schema-writes"]) == 3
        out = capsys.readouterr().out
        assert "NOT CERTIFIED" in out
        # the HEADLINE must not read as a pass either (round-3, text layer)
        assert "VERDICT: REACHABLE —" not in out

    def test_measured_unreachable_still_exits_1(self, monkeypatch):
        """...but a measured UNREACHABLE is SOUND: the true yield is >= the
        recorded one, so reality overshoots too."""
        mod = self._tool()
        monkeypatch.setattr(metering, "get_write_op_yield", lambda org: {
            "write_ops": 3, "nodes_written": 144, "nodes_per_op": 48.0,
            "measured": True, "nodes_attribution": "recorded",
            "period": "2026-10", "period_start": None, "period_end": None,
        })
        assert mod.main(["--org", "org_x", "--accept-schema-writes"]) == 1

    def test_period_error_and_read_error_are_named_separately(
            self, monkeypatch, capsys):
        mod = self._tool()
        for attr, needle in (("period_error", "WINDOW could not be resolved"),
                             ("read_error", "could NOT BE READ")):
            monkeypatch.setattr(metering, "get_write_op_yield", lambda org,
                                _a=attr: {
                "write_ops": 0, "nodes_written": 0, "nodes_per_op": None,
                "measured": False, "nodes_attribution": _a,
                "period": "2026-10", "period_start": None, "period_end": None,
            })
            assert mod.main(["--org", "org_x", "--accept-schema-writes"]) == 2
            assert needle in capsys.readouterr().err

    def test_an_unknown_tier_exits_2_instead_of_printing_free_numbers(
            self, capsys):
        """`tier_limits` falls back to free for an unknown name — a measurement
        tool must not answer for a tier that does not exist."""
        with pytest.raises(SystemExit) as exc:
            self._tool().main(["--nodes-per-op", "1", "--tier", "bogus"])
        assert exc.value.code == 2

    def test_an_empty_tier_set_exits_2(self, synthetic_book):
        """`all([])` is True: an empty surface must not print a green verdict."""
        synthetic_book({})
        with pytest.raises(SystemExit) as exc:
            self._tool().main(["--nodes-per-op", "1"])
        assert exc.value.code == 2

    def test_an_unreadable_price_book_exits_2_never_1(self, monkeypatch, tmp_path):
        """KeyError (config drift) / OSError (missing book) must not present as
        the P0 finding they never measured."""
        mod = self._tool()
        monkeypatch.setattr(pricing, "_PRICING_PATH",
                            str(tmp_path / "does-not-exist.json"))
        pricing.reload()
        with pytest.raises(SystemExit) as exc:
            mod.main(["--nodes-per-op", "48"])
        assert exc.value.code == 2

    def test_a_structurally_broken_price_book_exits_2_never_1(
            self, monkeypatch, tmp_path):
        """`tier_limits` raises TypeError for a non-dict tier entry (e.g.
        `"free": null`) — an unrankable book, not the P0 finding."""
        book = tmp_path / "broken.json"
        book.write_text(json.dumps({"tiers": {"free": None}}))
        monkeypatch.setattr(pricing, "_PRICING_PATH", str(book))
        pricing.reload()
        with pytest.raises(SystemExit) as exc:
            self._tool().main(["--nodes-per-op", "48"])
        assert exc.value.code == 2

    def test_json_payload_keeps_the_share_numeric(self, capsys, synthetic_book):
        """--json is machine-readable: a "<0.01" STRING in a numeric field
        breaks any compare/sort exactly on the near-threshold rows."""
        synthetic_book({"t": _tier(allowance=10_000, cap=10_000)})
        self._tool().main(["--nodes-per-op", "1.000005", "--tier", "t",
                           "--json"])
        payload = json.loads(capsys.readouterr().out)
        for row in payload["tiers"]:
            assert isinstance(row["unreachable_share_pct"], (int, float))

    def test_a_tier_restricted_pass_does_not_claim_the_whole_book(
            self, capsys, synthetic_book):
        """`--tier` evaluates a SUBSET: the verdict must say so.

        A pass produced from less than the thing it certifies is the exact
        overclaim this tool refuses at the measured-lower-bound boundary, and
        `--tier` can enter it from the other side: with a two-tier book where
        `bad` is unreachable at r=2, `--tier good` exits 0 — sound for `good`,
        but a line reading "every published tier can deliver" would be false
        (2 published, 1 evaluated). "every EVALUATED tier (1 of 2 published)"
        is the only wording that survives both facts.
        """
        synthetic_book({"good": _tier(allowance=1_000, cap=3_000),
                        "bad": _tier(allowance=1_000, cap=500)})
        mod = self._tool()
        assert mod.main(["--nodes-per-op", "2", "--tier", "good"]) == 0
        out = capsys.readouterr().out
        assert "every EVALUATED tier (1 of 2 published)" in out, out
        # The unqualified claim would be false for this book.
        assert "every published tier can deliver" not in out, out

    def test_a_tier_restricted_unreachable_counts_only_what_it_evaluated(
            self, capsys, synthetic_book):
        """The UNREACHABLE headline's denominator is the evaluated set, not the
        published one: `--tier bad` measured 1 tier, not 2."""
        synthetic_book({"good": _tier(allowance=1_000, cap=3_000),
                        "bad": _tier(allowance=1_000, cap=500)})
        assert self._tool().main(["--nodes-per-op", "2", "--tier", "bad"]) == 1
        out = capsys.readouterr().out
        assert "1/1 EVALUATED tier (1 of 2 published)" in out, out
        assert "1/2 published tiers" not in out, out

    def test_json_carries_the_evaluated_scope(self, capsys, synthetic_book):
        """Machine consumers get the same qualification the text carries:
        tiers_evaluated < tiers_published is what marks a subset verdict."""
        synthetic_book({"good": _tier(allowance=1_000, cap=3_000),
                        "bad": _tier(allowance=1_000, cap=500)})
        self._tool().main(["--nodes-per-op", "2", "--tier", "good",
                           "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["tiers_evaluated"] == 1
        assert payload["tiers_published"] == 2
        assert payload["verdict"] == "REACHABLE"

    def test_a_repeated_tier_cannot_switch_the_subset_guard_off(
            self, capsys, synthetic_book):
        """⛔ The scope decision must be SET-based, not row-count-based.

        `--tier` is appendable and rows are not deduped, so before this pin a
        caller repeating one tier `len(published)` times inflated the row count
        until `published > len(rows)` was false — and a ONE-tier run printed
        "every published tier can deliver" and reported `tiers_evaluated` equal
        to the published count. That is the overclaim the subset guard exists to
        prevent, reached through the guard itself.
        """
        synthetic_book({"good": _tier(allowance=1_000, cap=3_000),
                        "bad": _tier(allowance=1_000, cap=500)})
        mod = self._tool()
        assert mod.main(["--nodes-per-op", "2", "--tier", "good",
                         "--tier", "good"]) == 0
        out = capsys.readouterr().out
        assert "every EVALUATED tier (1 of 2 published)" in out, out
        assert "every published tier can deliver" not in out, out

        mod.main(["--nodes-per-op", "2", "--tier", "good", "--tier", "good",
                  "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["tiers_evaluated"] == 1
        assert payload["tiers_published"] == 2
