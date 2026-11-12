"""#11758 — the two purchasable storage dimensions, per account, in the artifact.

This file pins the DECISION RECORD on #11758 (comment 6807826749, the owner's
"ok", 2026-11-08, to the packet in 6807281064) against the artifact, the loader
and the overage arithmetic:

    two purchasable dimensions per account, unlimited graphs —
    stored data $0.30/GB-month, hot capacity $85/GB-month

What it deliberately does NOT claim: that a free tenant is REFUSED at the
included amount. The refusal is `tortoise/allowance_gate.py`, and BOTH storage
dimensions are BLOCKED there on instrument defects (no per-org Postgres byte
meter; the graph meter under-reports the HNSW index 8.5x-21.7x, and its own
contract forbids capping on the ledger figure). No shipped path can refuse on a
storage dimension yet, so a test asserting that would be asserting a fiction.
What IS pinned here is the *billing* semantics the artifact and #4815 already
state: a tier that is not overage-eligible cannot be billed for overage, and
every paid tier can — plus the invariant that keeps the blocked gate blocked.

Two properties of this file are load-bearing and easy to lose in a refactor:

1. **The dimension names are the DECLARED registry names** from
   ``tortoise.cost_dimensions``, and the allowance keys are
   ``allowance_gate.ALLOWANCE_KEYS``' own values — pinned below, so the
   artifact, the loader and the gate cannot drift into three spellings.
2. **The storage allowance keys are ABSENT from ``tier_limits()``.** The
   allowance gate arms on key PRESENCE, so leaking them into the dict every
   caller treats as "the org's limits" would arm two blocked dimensions and
   answer HTTP 500 on every gated write. That is pinned as an invariant, not
   left as a comment.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest

import tortoise.pricing as pricing

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CANONICAL = _REPO_ROOT / "product" / "pricing.json"
_FIXTURE = _REPO_ROOT / "tests" / "e2e" / "hosted" / "fixtures" / "pricing-e2e.json"

#: Every artifact `tortoise.pricing.tier_limits` can be pointed at. The E2E
#: fixture is a served copy (``TORTOISE_PRICING_PATH``), so a required key that
#: only canonical declares makes ``tier_limits('e2e_small')`` raise inside the
#: E2E lane — a failure that would look like an E2E bug, not a pricing one.
_ARTIFACTS = {"product/pricing.json": _CANONICAL,
              "tests/e2e/hosted/fixtures/pricing-e2e.json": _FIXTURE}


@pytest.fixture(autouse=True)
def _fresh_pricing():
    pricing.reload()
    yield
    pricing.reload()


# ── the two dimensions exist, and are READ ───────────────────────────────────

class TestTheTwoDimensionsExist:
    def test_both_declared_dimensions_are_loadable(self):
        dims = pricing.storage_dimensions()
        assert set(dims) == {"postgres_storage", "graph_storage"}
        assert pricing.STORAGE_DIMENSIONS == ("postgres_storage", "graph_storage")

    def test_rates_are_the_decision_record_rates(self):
        """$0.30 stored, $85 hot — #11758 comment 6807826749, point 2."""
        assert pricing.storage_overage_rate("postgres_storage") == pytest.approx(0.30)
        assert pricing.storage_overage_rate("graph_storage") == pytest.approx(85.0)

    def test_the_labels_are_the_customer_facing_words(self):
        dims = pricing.storage_dimensions()
        assert dims["postgres_storage"]["label"] == "stored data"
        assert dims["graph_storage"]["label"] == "hot capacity"

    def test_an_undeclared_dimension_is_a_loud_error(self):
        """A default rate would price a dimension nobody decided."""
        with pytest.raises(KeyError):
            pricing.storage_overage_rate("cold_capacity")
        from tortoise.metering import storage_overage_cost_usd
        with pytest.raises(KeyError):
            storage_overage_cost_usd("solo", "cold_capacity", 1)

    def test_the_rate_is_read_from_the_artifact_not_hardcoded(self, tmp_path,
                                                             monkeypatch):
        """Point the loader at a copy whose rate was changed: the reader must
        follow the artifact. This is the #5313 finding (a cost cell consumed by
        nothing is a no-op) applied to the new cell.

        ⚠️ ``_PRICING_PATH`` is resolved at IMPORT time (a module constant), so
        setting ``TORTOISE_PRICING_PATH`` mid-test changes nothing — the
        monkeypatch is on the constant, which is the only route that works
        (``tests/test_write_op_reachability.py`` documents the same trap).
        """
        data = json.loads(_CANONICAL.read_text(encoding="utf-8"))
        data["billing"]["storage"]["dimensions"][
            "postgres_storage"]["overage_price_usd_per_gb_month"] = 1.75
        mutated = tmp_path / "pricing.json"
        mutated.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(pricing, "_PRICING_PATH", str(mutated))
        pricing.reload()
        assert pricing.storage_overage_rate("postgres_storage") == \
            pytest.approx(1.75)


# ── the unit trap: cost basis is per GB of DATA, not per provisioned GB ──────

class TestTheUnitTrap:
    def test_hot_cost_basis_is_per_gb_of_data_not_per_provisioned_gb(self):
        """The market quotes PROVISIONED RAM ($43.75/GB at the 8 GB tier,
        Neo4j Aura $65.70/GB); a graph may occupy 75% of it, so our cost is
        $43.75 / 0.75 = $58.33 per GB of DATA HELD HOT. Pricing hot capacity
        against the provisioned figure would look market-consistent and lose
        money (#11758 comment 6807281064).

        Mutation this REDs: replacing 58.33 with 43.75 (the provisioned rate)
        or 73.0 (the deprecated FalkorDB figure already in billing.cost_basis).
        """
        basis = pricing.storage_dimensions()["graph_storage"][
            "cost_basis_usd_per_gb_month"]
        assert basis == pytest.approx(58.33, abs=0.01)
        assert basis == pytest.approx(43.75 / 0.75, abs=0.01)
        assert basis != pytest.approx(43.75)
        assert basis != pytest.approx(73.0)

    def test_the_rate_sits_above_our_cost_and_below_the_market_effective_rate(self):
        """1.46x margin, and deliberately a DISCOUNT to the market's ~$87.6
        per GB of hot data (Neo4j Aura provisioned x 1.33)."""
        hot = pricing.storage_overage_rate("graph_storage")
        basis = pricing.storage_dimensions()["graph_storage"][
            "cost_basis_usd_per_gb_month"]
        assert hot > basis, "selling hot capacity below cost"
        assert hot < 65.70 * (4 / 3), (
            "hot capacity must stay below the market effective rate "
            "(~$87.6/GB of data) or the discount that justifies the ratio is gone")

    def test_gb_is_decimal_and_pinned_once(self):
        """Two meters, two units (bytes, decimal MB) — one GB definition.

        A MiB/decimal mix is the measured 3.59x error class in
        docs/architecture/STORAGE-ARCHITECTURE.md, so the conversion lives in
        exactly one constant and the artifact restates it.
        """
        assert pricing.BYTES_PER_GB == 1_000_000_000
        gb_def = json.loads(_CANONICAL.read_text(encoding="utf-8"))[
            "billing"]["storage"]["gb_definition"]
        assert "1,000,000,000" in gb_def or "1e9" in gb_def or "1000" in gb_def

    def test_each_dimensions_unit_is_the_one_its_meter_publishes(self):
        """`postgres_storage` is bytes (#5331's ruled base); `graph_storage` is
        MB because that is what GRAPH.MEMORY USAGE publishes. Converting either
        at the artifact would force a presentation factor into a rate."""
        dims = pricing.storage_dimensions()
        assert dims["postgres_storage"]["unit"] == "bytes"
        assert dims["graph_storage"]["unit"] == "MB"


# ── ONE registry: the artifact's keys ARE the gate's keys ───────────────────

def test_allowance_keys_are_the_allowance_gate_keys():
    """The artifact, the loader and the gate must not drift into three
    spellings. `allowance_gate.ALLOWANCE_KEYS` is the ONE declaration; the
    artifact's `allowance_key` must equal it for both storage dimensions.

    A runtime import of `allowance_gate` in `pricing.py` is avoided on purpose
    (it imports `quota`, which imports `pricing` — a cycle), so this pin lives
    here where importing both is safe.
    """
    from tortoise.allowance_gate import ALLOWANCE_KEYS

    dims = pricing.storage_dimensions()
    for name in pricing.STORAGE_DIMENSIONS:
        assert dims[name]["allowance_key"] == ALLOWANCE_KEYS[name], (
            f"{name}: artifact allowance_key {dims[name]['allowance_key']!r} != "
            f"allowance_gate.ALLOWANCE_KEYS {ALLOWANCE_KEYS[name]!r}")

    # …and the loader exposes the value under that SAME key.
    assert set(pricing.storage_limits("solo")) == {
        ALLOWANCE_KEYS[n] for n in pricing.STORAGE_DIMENSIONS}


def test_the_dimension_names_are_the_declared_cost_dimensions():
    from tortoise.cost_dimensions import by_name

    for name in pricing.STORAGE_DIMENSIONS:
        declared = by_name(name)  # raises KeyError when undeclared
        assert declared.name == name


# ── per ACCOUNT: nothing per graph, nothing per node ───────────────────────

class TestPerAccountNotPerGraph:
    def test_the_allowance_is_one_value_per_tier(self):
        """The artifact carries the allowance on the TIER, not on a graph — so
        there is nothing to multiply by the graph count."""
        data = json.loads(_CANONICAL.read_text(encoding="utf-8"))
        for tier, spec in data["tiers"].items():
            for name in pricing.STORAGE_DIMENSIONS:
                key = pricing.storage_dimensions()[name]["allowance_key"]
                assert key in spec, f"tier {tier!r} publishes no {key}"
                assert not isinstance(spec[key], (dict, list)), (
                    f"tier {tier!r} {key} is a collection — the allowance is "
                    f"PER ACCOUNT, so a per-graph split here would be the "
                    f"second-allowance layer the owner dropped (#6805077325)")

    def test_no_key_in_the_artifact_is_a_per_graph_or_per_node_price(self):
        """⛔ Nodes were retired as the customer-facing pricing unit
        (#4495/#5331) and the owner's correction on #11758 (6805077325) is
        "per account only". A new key that prices per graph or per node would
        be that decision reversed by a field name.

        `max_graph_nodes` is EXCLUDED because it is the documented legacy
        column that stays until the byte design replaces it — it is not a price
        and nothing here extends it.
        """
        data = json.loads(_CANONICAL.read_text(encoding="utf-8"))
        storage = data["billing"]["storage"]

        def _walk(node, path=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield from _walk(v, f"{path}.{k}" if path else k)
            else:
                yield path, node

        for path, _value in _walk(storage):
            leaf = path.rsplit(".", 1)[-1]
            assert not leaf.startswith("per_graph"), (
                f"{path} prices per graph — the shape is per account")
            assert not leaf.startswith("per_node"), (
                f"{path} prices per node — nodes are not a pricing unit")
        # And the two scales are explicitly present, so the block cannot be
        # emptied to make this pass.
        assert set(storage["dimensions"]) == set(pricing.STORAGE_DIMENSIONS)

    def test_a_second_graph_does_not_double_the_allowance(self):
        """The accounting statement of "per account": the allowance is resolved
        from the TIER alone, so asking twice (two graphs) gives one allowance,
        and an account total is compared against ONE included figure — not N.

        Mutation this REDs: making `storage_limits` take a graph count, or
        multiplying the allowance by anything derived from the org's graphs.
        """
        first = pricing.storage_limits("solo")
        second = pricing.storage_limits("solo")
        assert first == second
        total_over_two_graphs = sum(
            [3_000_000_000, 3_000_000_000])  # account total, not per-graph
        from tortoise.metering import storage_overage_cost_usd
        result = storage_overage_cost_usd("solo", "postgres_storage",
                                          total_over_two_graphs)
        # 6 GB against ONE 1 GB allowance = 5 GB over, at $0.30 — not 2 x 5 GB.
        assert result["overage_gb"] == pytest.approx(5.0)
        assert result["overage_cost_usd"] == pytest.approx(1.50)


# ── free refuses / paid bills — the BILLING semantics ──────────────────────

class TestFreeCannotPurchasePaidCan:
    def test_only_paid_tiers_can_be_billed_for_storage_overage(self):
        """#4815 + the decision record's point 4: free/anon are not refused —
        they simply cannot be BILLED (there is no overage for them to buy),
        while every paid tier bills and continues.

        ⛔ This is the billing semantic. The REFUSAL half is the allowance
        gate, which is BLOCKED for both storage dimensions — see the module
        docstring; asserting a live refusal here would be a fiction.
        """
        from tortoise.metering import storage_overage_cost_usd

        big = 50_000_000_000  # 50 GB — far over every included amount, INCLUDING
        # team's 7.8 GB record allowance (using 5 GB there would be UNDER the
        # allowance and make this test assert a fiction for that tier)
        for tier in ("free", "anon"):
            result = storage_overage_cost_usd(tier, "postgres_storage", big)
            assert result["overage_purchasable"] is False, (
                f"{tier} must not be billable for storage overage")
            assert result["overage_cost_usd"] == 0.0
        for tier in ("solo", "pro", "team"):
            result = storage_overage_cost_usd(tier, "postgres_storage", big)
            assert result["overage_purchasable"] is True, (
                f"{tier} is a paid tier — it bills and continues (#4815)")
            assert result["overage_cost_usd"] > 0.0

    def test_the_free_record_allowance_is_unconfigured_not_a_measured_zero(self):
        """`free`/`anon` publish `null` for the record allowance because the
        owner gave "small" and no NUMBER (#11758 comment 6807281064). The
        arithmetic must say UNCONFIGURED rather than report a fabricated 0 as
        if it had measured an allowance.

        ⛔ If this starts failing because someone set a number, that number has
        to come from the owner — it is not derivable from the rates, and the
        decision record says so.
        """
        from tortoise.metering import storage_overage_cost_usd

        for tier in ("free", "anon"):
            result = storage_overage_cost_usd(tier, "postgres_storage", 1)
            assert result["included"] is None
            assert result["allowance_configured"] is False
            assert result["overage_quantity"] == 0.0
        for tier in ("solo", "pro", "team"):
            result = storage_overage_cost_usd(tier, "postgres_storage", 1)
            assert result["allowance_configured"] is True

    def test_free_and_anon_publish_zero_hot_capacity(self):
        """The decision record states free/anon's included hot capacity as 0 —
        this one IS a number the owner gave, so it is pinned."""
        for tier in ("free", "anon"):
            assert pricing.storage_limits(tier)["max_graph_storage_mb"] == 0


# ── the included amounts: derived from §8, and MARKED unconfirmed ──────────

class TestIncludedAmounts:
    def test_included_amounts_match_the_section_8_derivation(self):
        """`docs/architecture/SEARCH-ARCHITECTURE.md` §8: the 30%-of-price rule
        yields a ~77 MB hot window and ~1 GB record at solo; pro ≈102 MB / 1.3
        GB, team ≈615 MB / 7.8 GB.

        ⚠️ Those are DERIVED defaults, not owner-confirmed prices — see
        `_included_amounts_status`. This test pins the derivation, and the test
        below pins that the artifact SAYS it is unconfirmed.
        """
        expected = {
            "solo": (77, 1_000_000_000),
            "pro": (102, 1_300_000_000),
            "team": (615, 7_800_000_000),
        }
        for tier, (hot_mb, record_bytes) in expected.items():
            limits = pricing.storage_limits(tier)
            assert limits["max_graph_storage_mb"] == hot_mb, tier
            assert limits["max_postgres_storage_bytes"] == record_bytes, tier

    def test_the_included_amounts_are_marked_unconfirmed_in_the_artifact(self):
        """The decision record lists the included amounts as explicitly NOT
        decided ("a product choice, not implied by the rates"). The artifact
        must not present them as settled."""
        status = json.loads(_CANONICAL.read_text(encoding="utf-8"))[
            "billing"]["storage"]["_included_amounts_status"]
        assert "NOT OWNER-CONFIRMED" in status
        assert "DERIVED" in status
        # And it names the derivation, so a reader can re-check the figure.
        assert "SEARCH-ARCHITECTURE" in status

    def test_storage_allowances_scale_with_the_price_under_the_30_percent_rule(self):
        """The record allowance is ~5.26% of the tier price's 30% budget (it is
        467x cheaper per byte, §8) — so it must be monotonically increasing
        with price without being the thing that binds. This pins the SHAPE; the
        exact bytes are pinned above."""
        paid = [t for t in pricing.all_tiers() if pricing.tier_price(t) > 0]
        by_price = sorted(paid, key=pricing.tier_price)
        allowances = [pricing.storage_limits(t)["max_postgres_storage_bytes"]
                      for t in by_price]
        assert allowances == sorted(allowances)
        assert len(set(allowances)) == len(allowances), (
            "two paid tiers share an included record allowance — the 30% rule "
            "gives each tier its own budget")


# ── the loader's fail-closed contract, and the anti-arming invariant ───────

class TestTheLoaderContract:
    def test_every_artifact_declares_the_storage_keys_on_every_tier(self):
        """`_REQUIRED_LIMIT_KEYS` gained both keys deliberately: a tier that
        publishes no storage allowance must fail LOUDLY, not read as "unlimited
        storage, free". The E2E fixture is a SERVED copy, so it must declare
        them too or `tier_limits('e2e_small')` raises inside the E2E lane.
        """
        canonical = json.loads(_CANONICAL.read_text(encoding="utf-8"))
        for name, path in _ARTIFACTS.items():
            data = json.loads(path.read_text(encoding="utf-8"))
            for tier, spec in data["tiers"].items():
                for key in ("max_postgres_storage_bytes",
                            "max_graph_storage_mb"):
                    assert key in spec, (
                        f"{name}: tier {tier!r} is missing {key} — a MISSING "
                        f"key is exactly the drift _REQUIRED_LIMIT_KEYS exists "
                        f"to catch")
            # `null` is a legitimate value; a MISSING key is not. For every tier
            # the two artifacts SHARE, they must agree on the value.
            for tier in set(data["tiers"]) & set(canonical["tiers"]):
                for key in ("max_postgres_storage_bytes",
                            "max_graph_storage_mb"):
                    assert data["tiers"][tier][key] == \
                        canonical["tiers"][tier][key], (
                            f"{name}: tier {tier!r} {key} "
                            f"{data['tiers'][tier][key]!r} != canonical "
                            f"{canonical['tiers'][tier][key]!r}")

    def test_a_tier_missing_a_storage_key_fails_closed(self, tmp_path,
                                                      monkeypatch):
        """A tier that publishes no storage allowance must fail LOUDLY rather
        than read as "unlimited storage, free" (see ``_REQUIRED_LIMIT_KEYS``).

        Mutation this REDs: dropping either storage key from
        ``_REQUIRED_LIMIT_KEYS`` — the artifact would then be free to ship a
        tier with no storage allowance at all.
        """
        data = json.loads(_CANONICAL.read_text(encoding="utf-8"))
        del data["tiers"]["solo"]["max_graph_storage_mb"]
        mutated = tmp_path / "pricing.json"
        mutated.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(pricing, "_PRICING_PATH", str(mutated))
        pricing.reload()
        with pytest.raises(KeyError, match="max_graph_storage_mb"):
            pricing.tier_limits("solo")

    def test_the_storage_keys_are_NOT_in_tier_limits(self):
        """⛔ THE ANTI-ARMING INVARIANT. `allowance_gate.enforce_allowances`
        arms on key PRESENCE (`if dim.limit_key not in limits: continue`), and
        BOTH storage dimensions are BLOCKED there — `postgres_storage` has no
        per-org byte meter, and `graph_storage`'s own contract forbids capping
        on a figure that under-reports the HNSW index 8.5x-21.7x. The
        `tier_limits()` dict is what callers hand around as "the org's limits",
        so a storage key in it is one `{**tier_limits(t), ...}` away from
        arming a blocked dimension and answering HTTP 500 on every gated write.

        Mutation this REDs: moving either storage key into `tier_limits()`'s
        return dict "for symmetry" with the other limits.
        """
        from tortoise.allowance_gate import ALLOWANCE_KEYS

        storage_keys = {ALLOWANCE_KEYS[n] for n in pricing.STORAGE_DIMENSIONS}
        for tier in pricing.all_tiers():
            leaks = storage_keys & set(pricing.tier_limits(tier))
            assert not leaks, (
                f"tier_limits({tier!r}) exposes {sorted(leaks)} — the storage "
                f"allowances belong on storage_limits() until the allowance "
                f"gate's storage dimensions are unblocked")

    def test_enforcing_on_a_tier_limits_dict_stays_inert(self):
        """The end-to-end statement of the invariant above: the gate must be a
        no-op on the dict the loader actually hands out, so the storage years
        of #11758 cannot accidentally start refusing writes."""
        from tortoise.allowance_gate import enforce_allowances
        from tortoise.pricing import tier_limits

        limits = dict(tier_limits("solo"), org_id="probe-org")
        enforce_allowances(limits, org_id="probe-org", resource="points")
        enforce_allowances(limits, org_id="probe-org",
                           resource="documents")

    def test_unknown_tier_falls_back_to_free_for_storage_too(self):
        assert pricing.storage_limits("enterprise-unknown") == \
            pricing.storage_limits("free")


def test_solo_costs_nineteen_dollars():
    """Owner ruling 2026-11-07 (#11758 body): solo = $19. The plan card, the
    financial model and the 30% derivation all read this one cell.

    Note for a reader who finds this surprising: $19 is almost exactly the bound
    the existing per-tier margin model already documented ($19.13 at cap), so
    the ruling is not a new price out of nowhere.
    """
    assert pricing.tier_price("solo") == 19


def test_the_storage_rates_do_not_leak_into_the_write_op_overage():
    """The write-op overage is unchanged (still $5 per 10k, its own unit). A
    first pass at #11758 could have "generalised" the overage block and
    re-priced production write-op billing by accident."""
    assert pricing.overage_price_per_10k() == 5.0
    assert pricing.overage_tiers() == ["solo", "pro", "team"]
