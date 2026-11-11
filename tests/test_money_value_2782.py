"""#2782 — money as a flat, currency-safe structural value on an entity.

The representation contract is the WS-C design
(``docs/plans/2026-09-10-ws-c-numeric-normalization.md``, owner-approved;
Option (a) recorded on ``#2817``). The issue's own targets are:

  * an Object AND an Event can carry ``amount + currency`` and round-trip it
    without a schema error (including across ``rebuild_all``); and
  * a query can answer "how much is committed / released to date?" — a SUM and
    a RATIO against a threshold, not a range filter (design §1, §7.2 step 5).

This file pins the flat-field shape and the arithmetic. The prose→value
DERIVATION is deliberately absent here: it is ``#2817``'s S5 normalizer
(``tortoise/value_normalizer.py``).
Identity scoping — two same-named money entities under different parents
staying distinct — is ``#2730`` and is NOT papered over here (design §4).

Runnable (URI-less embedded):
    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
        tests/test_money_value_2782.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pydantic import ValidationError

from tortoise.commit_schema import CommitEvent, Entity
from tortoise.money import (
    AMOUNT_MINOR,
    AS_OF,
    CURRENCY,
    ISO_4217_MINOR_UNITS,
    MINOR_UNIT_EXPONENT,
    MONEY_VALUE_FIELDS,
    VERBATIM,
    MoneyValueError,
    money_props,
    ratio_bp,
    ratio_meets,
    read_money_value,
    total_minor,
)
from tortoise.sdk import TortoiseSDK

#: R$ 250.000,00 and R$ 100.000,00 in minor units at exponent 2.
COMMITTED_MINOR = 25_000_000
RELEASED_MINOR = 18_750_000  # exactly 75% of committed


@pytest.fixture
def journaled_sdk(tmp_path):
    """Embedded SDK with the JSONL journal wired (rebuild durability)."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(
        str(tmp_path / "money_2782.db"),
        event_log_path=str(events / "events.jsonl"),
    )
    yield sdk, events
    sdk.close()


def _node_props(sdk: TortoiseSDK, label: str, key: str, value: str) -> dict:
    rows = sdk._get_proj().g.query(
        f"MATCH (n:{label} {{{key}:$v}}) RETURN properties(n)",
        params={"v": value},
    ).result_set
    assert rows, f"no :{label} node with {key}={value!r}"
    return rows[0][0]


def _point_row(sdk: TortoiseSDK, content_prefix: str) -> dict:
    """The single :Point whose content starts with ``content_prefix``."""
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.content STARTS WITH $p "
        "RETURN properties(n)",
        params={"p": content_prefix},
    ).result_set
    assert len(rows) == 1, f"expected one :Point starting {content_prefix!r}"
    return rows[0][0]


# ── representation: the five flat sibling fields ───────────────────────────

class TestIso4217Table:
    """The pinned `(currency, exponent)` table (design OD2 / T4) — the table is
    the version pin, so its shape is asserted rather than left implicit."""

    def test_every_key_is_an_alpha3_code(self):
        assert all(len(c) == 3 and c.isupper() and c.isalpha()
                   for c in ISO_4217_MINOR_UNITS)

    def test_the_pinned_population_matches_the_standard(self):
        # ISO 4217 active codes at the pinned revision: 139 at the 2-digit
        # default, 17 at 0, 7 at 3, 2 at 4, and 13 with no minor unit.
        counts = {}
        for exponent in ISO_4217_MINOR_UNITS.values():
            counts[exponent] = counts.get(exponent, 0) + 1
        assert counts == {0: 17, 2: 139, 3: 7, 4: 2, None: 13}
        assert len(ISO_4217_MINOR_UNITS) == 178

    def test_the_anchors_the_design_names(self):
        assert ISO_4217_MINOR_UNITS["JPY"] == 0
        assert ISO_4217_MINOR_UNITS["USD"] == 2
        assert ISO_4217_MINOR_UNITS["BRL"] == 2
        assert ISO_4217_MINOR_UNITS["KWD"] == 3
        assert ISO_4217_MINOR_UNITS["BHD"] == 3
        assert ISO_4217_MINOR_UNITS["CLF"] == 4
        assert ISO_4217_MINOR_UNITS["UYW"] == 4
        assert ISO_4217_MINOR_UNITS["XAU"] is None


class TestMoneyProps:
    def test_builds_the_five_declared_flat_fields(self):
        props = money_props(
            COMMITTED_MINOR, "BRL", minor_unit_exponent=2,
            verbatim="R$ 250.000,00", as_of="2026-06-30")
        assert props == {
            AMOUNT_MINOR: 25_000_000,
            CURRENCY: "BRL",
            MINOR_UNIT_EXPONENT: 2,
            VERBATIM: "R$ 250.000,00",
            AS_OF: "2026-06-30",
        }
        assert tuple(props) == MONEY_VALUE_FIELDS

    def test_optional_fields_are_omitted_not_none(self):
        props = money_props(100, "USD", minor_unit_exponent=2)
        assert VERBATIM not in props
        assert AS_OF not in props

    def test_never_a_float(self):
        with pytest.raises(MoneyValueError):
            money_props(10.5, "USD", minor_unit_exponent=2)

    def test_bool_is_not_an_amount(self):
        # bool is an int subclass — True must not read as 1 minor unit.
        with pytest.raises(MoneyValueError):
            money_props(True, "USD", minor_unit_exponent=2)

    def test_an_amount_the_store_cannot_hold_is_refused(self):
        """#2817 review P1: the store's INTEGER domain is part of the value.

        FalkorDB silently CLAMPS an int above INT64 (#4647) and the write path
        refuses it — so admitting it at this boundary let a DERIVED amount sink
        the record it came from: at the capture seam the refusal costs the whole
        point and reports the whole capture `ok=False`. Measured pre-fix:
        ``EUR 123456789012345678`` → ``amountMinor`` 12345678901234567800 →
        ``ValueError: integer with 64 bits is outside the range`` → point not
        written. Refusing HERE makes the normalizer abstain instead.
        """
        for over in (2 ** 63, -(2 ** 63) - 1, 12345678901234567800):
            with pytest.raises(MoneyValueError, match=r"64 bits is outside the range"):
                money_props(over, "EUR", minor_unit_exponent=2)
        # ...and the boundary itself is inclusive: INT64 max is storable.
        assert money_props(
            2 ** 63 - 1, "EUR", minor_unit_exponent=2)[AMOUNT_MINOR] == 2 ** 63 - 1

    @pytest.mark.parametrize("bad", ["usd", "US", "US1", "", None, 840,
                                    "USD\n", " USD", "USD ", "U\nD"])
    def test_currency_must_be_iso_4217_alpha3(self, bad):
        with pytest.raises(MoneyValueError):
            money_props(100, bad, minor_unit_exponent=2)

    def test_currency_must_be_in_the_pinned_iso_4217_table(self):
        # three uppercase letters is not enough: the code must be one this
        # module's pinned table declares, or its scale cannot be checked.
        with pytest.raises(MoneyValueError, match="pinned table"):
            money_props(100, "ZZZ", minor_unit_exponent=2)

    @pytest.mark.parametrize("bad", [-1, 5, 2.0, "2", True, None])
    def test_exponent_is_a_bounded_int(self, bad):
        with pytest.raises(MoneyValueError):
            money_props(100, "USD", minor_unit_exponent=bad)

    @pytest.mark.parametrize("currency,exponent", [
        ("JPY", 0), ("KRW", 0), ("CLP", 0), ("XOF", 0),
        ("USD", 2), ("BRL", 2), ("EUR", 2),
        ("BHD", 3), ("KWD", 3), ("TND", 3),
        ("CLF", 4), ("UYW", 4),
    ])
    def test_the_pinned_pair_is_accepted(self, currency, exponent):
        assert money_props(100, currency, minor_unit_exponent=exponent)[
            MINOR_UNIT_EXPONENT] == exponent

    @pytest.mark.parametrize("currency,wrong", [
        ("JPY", 2), ("KRW", 2), ("USD", 0), ("USD", 1), ("USD", 3),
        ("KWD", 2), ("KWD", 4), ("CLF", 2), ("UYW", 3), ("BHD", 2),
    ])
    def test_a_mis_scaled_pair_is_refused(self, currency, wrong):
        # A (currency, exponent) pair the pinned table contradicts is a
        # silent unit error: JPY at exponent 2 is a 100x mis-scale.
        with pytest.raises(MoneyValueError, match="ISO 4217"):
            money_props(100, currency, minor_unit_exponent=wrong)

    @pytest.mark.parametrize("currency", ["XAU", "XAG", "XDR", "XXX", "XTS"])
    def test_a_currency_with_no_minor_unit_is_refused(self, currency):
        with pytest.raises(MoneyValueError, match="no ISO 4217 minor unit"):
            money_props(100, currency, minor_unit_exponent=2)

    def test_verbatim_must_be_non_empty_when_present(self):
        with pytest.raises(MoneyValueError):
            money_props(100, "USD", minor_unit_exponent=2, verbatim="")

    def test_as_of_must_be_an_iso_date(self):
        with pytest.raises(MoneyValueError):
            money_props(100, "USD", minor_unit_exponent=2, as_of="next tuesday")
        # a non-empty prefix is accepted exactly as Point._iso_date_prefix
        # accepts it — and nothing looser: trailing junk is not a date.
        with pytest.raises(MoneyValueError):
            money_props(100, "USD", minor_unit_exponent=2,
                        as_of="2026-06-30junk")
        with pytest.raises(MoneyValueError):
            money_props(100, "USD", minor_unit_exponent=2, as_of="2026-6-30")
        with pytest.raises(MoneyValueError):
            money_props(100, "USD", minor_unit_exponent=2, as_of="")
        assert money_props(
            100, "USD", minor_unit_exponent=2,
            as_of="2026-06-30T00:00:00Z")[AS_OF] == "2026-06-30T00:00:00Z"
        assert money_props(
            100, "USD", minor_unit_exponent=2,
            as_of="2026-06-30 12:00")[AS_OF] == "2026-06-30 12:00"
        # Point strips before matching; so does this (same acceptance set).
        assert money_props(
            100, "USD", minor_unit_exponent=2,
            as_of=" 2026-06-30 ")[AS_OF] == " 2026-06-30 "


# ── reading one value out of a mixed record set ────────────────────────────

class TestReadMoneyValue:
    def test_reads_a_valid_triple(self):
        assert read_money_value({
            AMOUNT_MINOR: 5, CURRENCY: "JPY", MINOR_UNIT_EXPONENT: 0,
        }) == ("JPY", 5, 0)

    def test_non_money_record_is_none(self):
        assert read_money_value({"name": "Tranche A"}) is None
        assert read_money_value({AMOUNT_MINOR: None, CURRENCY: None}) is None

    def test_present_but_malformed_refuses(self):
        with pytest.raises(MoneyValueError):
            read_money_value({AMOUNT_MINOR: 10.5, CURRENCY: "USD",
                              MINOR_UNIT_EXPONENT: 2})
        with pytest.raises(MoneyValueError):
            read_money_value({AMOUNT_MINOR: 10, CURRENCY: "USD"})

    def test_a_mis_scaled_record_is_refused(self):
        # The read boundary is as strict as the build boundary: a JPY record
        # at exponent 2 must not be summed as if the scale were 2.
        with pytest.raises(MoneyValueError, match="ISO 4217"):
            read_money_value({AMOUNT_MINOR: 100, CURRENCY: "JPY",
                              MINOR_UNIT_EXPONENT: 2})

    def test_a_record_with_an_unknown_currency_is_refused(self):
        with pytest.raises(MoneyValueError, match="pinned table"):
            read_money_value({AMOUNT_MINOR: 100, CURRENCY: "ZZZ",
                              MINOR_UNIT_EXPONENT: 2})

    def test_an_unstorable_amount_is_refused_on_read_too(self):
        # The read boundary refuses what the store could not have held — a
        # clamped amount is a data error, never something to sum.
        with pytest.raises(MoneyValueError, match=r"64 bits is outside the range"):
            read_money_value({AMOUNT_MINOR: 2 ** 63, CURRENCY: "EUR",
                              MINOR_UNIT_EXPONENT: 2})


# ── consumption: sum per currency, never mixed ─────────────────────────────

class TestTotalMinor:
    def test_sums_per_currency(self):
        records = [
            {AMOUNT_MINOR: 25_000_000, CURRENCY: "BRL", MINOR_UNIT_EXPONENT: 2},
            {AMOUNT_MINOR: 10_000_000, CURRENCY: "BRL", MINOR_UNIT_EXPONENT: 2},
            {AMOUNT_MINOR: 5_000, CURRENCY: "EUR", MINOR_UNIT_EXPONENT: 2},
        ]
        assert total_minor(records) == {"BRL": 35_000_000, "EUR": 5_000}

    def test_never_mixes_currencies(self):
        records = [
            {AMOUNT_MINOR: 100, CURRENCY: "USD", MINOR_UNIT_EXPONENT: 2},
            {AMOUNT_MINOR: 100, CURRENCY: "BRL", MINOR_UNIT_EXPONENT: 2},
        ]
        # two currency keys, never one merged 200 — amounts are not fungible.
        assert total_minor(records) == {"USD": 100, "BRL": 100}

    def test_a_mis_scaled_record_cannot_be_summed(self):
        # 1.000 USD is not a declared USD scale, so it is refused at the
        # record boundary — it can never be silently summed with 1.00 USD.
        records = [
            {AMOUNT_MINOR: 100, CURRENCY: "USD", MINOR_UNIT_EXPONENT: 2},
            {AMOUNT_MINOR: 1000, CURRENCY: "USD", MINOR_UNIT_EXPONENT: 3},
        ]
        with pytest.raises(MoneyValueError, match="ISO 4217"):
            total_minor(records)

    def test_a_mis_scaled_whole_currency_is_refused(self):
        # JPY is exponent 0; a set of JPY-at-2 records is a 100x error and
        # must raise instead of producing a confidently wrong total.
        with pytest.raises(MoneyValueError, match="ISO 4217"):
            total_minor([{AMOUNT_MINOR: 100, CURRENCY: "JPY",
                          MINOR_UNIT_EXPONENT: 2}])

    def test_different_currencies_may_hold_different_exponents(self):
        records = [
            {AMOUNT_MINOR: 100, CURRENCY: "JPY", MINOR_UNIT_EXPONENT: 0},
            {AMOUNT_MINOR: 100, CURRENCY: "KWD", MINOR_UNIT_EXPONENT: 3},
        ]
        assert total_minor(records) == {"JPY": 100, "KWD": 100}

    def test_non_money_records_are_skipped(self):
        records = [
            {"name": "portfolio company"},
            {AMOUNT_MINOR: 42, CURRENCY: "USD", MINOR_UNIT_EXPONENT: 2},
        ]
        assert total_minor(records) == {"USD": 42}

    def test_empty_is_empty(self):
        assert total_minor([]) == {}


# ── the unlock test: exact ratio, no truncation surprise ───────────────────

class TestRatio:
    def test_ratio_bp_is_exact_and_floored(self):
        assert ratio_bp(COMMITTED_MINOR, COMMITTED_MINOR) == 10_000
        assert ratio_bp(RELEASED_MINOR, COMMITTED_MINOR) == 7_500
        assert ratio_bp(1, 3) == 3_333  # floored, never floats

    def test_ratio_is_undefined_over_zero_whole(self):
        assert ratio_bp(100, 0) is None
        assert ratio_meets(100, 0, 7_500) is None

    def test_exactly_at_threshold_meets(self):
        # The grounded requirement: 75% spent unlocks tranche 2.
        assert ratio_meets(RELEASED_MINOR, COMMITTED_MINOR, 7_500) is True

    def test_below_threshold_does_not_meet(self):
        assert ratio_meets(RELEASED_MINOR - 1, COMMITTED_MINOR, 7_500) is False

    def test_below_threshold_by_one_minor_unit(self):
        # one minor unit short of 75% is not 75%: 18_749_999 / 25_000_000.
        assert ratio_bp(COMMITTED_MINOR - 1, COMMITTED_MINOR) == 9_999
        assert ratio_meets(18_749_999, COMMITTED_MINOR, 7_500) is False

    @pytest.mark.parametrize("bad", [-1, -10_000])
    def test_negative_operands_are_refused(self, bad):
        with pytest.raises(MoneyValueError):
            ratio_bp(bad, 100)
        with pytest.raises(MoneyValueError):
            ratio_meets(10, 100, bad)

    def test_deterministic(self):
        a = total_minor([
            {AMOUNT_MINOR: 1, CURRENCY: "USD", MINOR_UNIT_EXPONENT: 2},
            {AMOUNT_MINOR: 2, CURRENCY: "USD", MINOR_UNIT_EXPONENT: 2},
        ])
        b = total_minor([
            {AMOUNT_MINOR: 2, CURRENCY: "USD", MINOR_UNIT_EXPONENT: 2},
            {AMOUNT_MINOR: 1, CURRENCY: "USD", MINOR_UNIT_EXPONENT: 2},
        ])
        assert a == b == {"USD": 3}


# ── the design's decision: the field is SERVER-written ──────────────────────

class TestCommitContractStaysClosedByDesign:
    """WS-C design §7.1/S3 — the money field is **server-written**, so the
    typed commit contract does NOT accept it. The issue body's checklist row
    ("commit schema: amount+currency round-trip") is superseded by the
    owner-approved design; this pins that decision so a future change cannot
    quietly widen the frozen Layer-1 contract instead."""

    def test_commit_entity_rejects_an_amount(self):
        with pytest.raises(ValidationError):
            Entity(name="Tranche A", kind="funding_tranche",
                   amountMinor=COMMITTED_MINOR, currency="BRL")

    def test_commit_event_rejects_an_amount(self):
        with pytest.raises(ValidationError):
            CommitEvent(id="ev_" + "a" * 32, eventKind="disbursement",
                        content="released", source_ref="src_1",
                        amountMinor=COMMITTED_MINOR, currency="BRL")

    def test_commit_entity_still_accepts_its_three_declared_fields(self):
        # the closed contract is untouched, not broken
        assert Entity(name="Tranche A", kind="funding_tranche").name == \
            "Tranche A"


# ── the issue's target: round-trip on an Object AND an Event ───────────────

class TestEntityRoundTrip:
    def test_object_carries_and_returns_money_fields(self, journaled_sdk):
        sdk, _events = journaled_sdk
        created = sdk.create_object(
            "Tranche A", "venture:tranche",
            **money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2,
                          verbatim="R$ 250.000,00", as_of="2026-06-30"))
        node = _node_props(sdk, "Object", "name", "Tranche A")
        assert node[AMOUNT_MINOR] == COMMITTED_MINOR
        assert node[CURRENCY] == "BRL"
        assert node[MINOR_UNIT_EXPONENT] == 2
        assert node[VERBATIM] == "R$ 250.000,00"
        assert node[AS_OF] == "2026-06-30"
        # and the entity read door returns them
        got = sdk.get_entity(created["id"])
        assert got[AMOUNT_MINOR] == COMMITTED_MINOR
        assert got[CURRENCY] == "BRL"

    def test_event_carries_and_returns_money_fields(self, journaled_sdk):
        sdk, _events = journaled_sdk
        sdk.create_event(
            "Disbursement 1", "venture:disbursement",
            **money_props(RELEASED_MINOR, "BRL", minor_unit_exponent=2,
                          verbatim="R$ 187.500,00", as_of="2026-07-01"))
        node = _node_props(sdk, "Event", "name", "Disbursement 1")
        assert node[AMOUNT_MINOR] == RELEASED_MINOR
        assert node[CURRENCY] == "BRL"
        assert node[VERBATIM] == "R$ 187.500,00"

    def test_money_fields_survive_rebuild_all(self, journaled_sdk):
        """A value written live must still be there after a full replay —
        objects/events carry the same open-set prop lane as the other
        non-Point labels (the #2795/#2958 durability contract)."""
        sdk, events = journaled_sdk
        sdk.create_object(
            "Tranche R", "venture:tranche",
            **money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2,
                          verbatim="R$ 250.000,00"))
        sdk.create_event(
            "Disbursement R", "venture:disbursement",
            **money_props(RELEASED_MINOR, "BRL", minor_unit_exponent=2,
                          verbatim="R$ 187.500,00"))
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        obj = _node_props(sdk, "Object", "name", "Tranche R")
        ev = _node_props(sdk, "Event", "name", "Disbursement R")
        assert (obj[AMOUNT_MINOR], obj[CURRENCY]) == (COMMITTED_MINOR, "BRL")
        assert (ev[AMOUNT_MINOR], ev[CURRENCY]) == (RELEASED_MINOR, "BRL")


# ── the Point lane: a number asserted in prose carries the same flat value ──

class TestPointLane:
    """A Point asserts a claim rather than owning state, so it carries the SAME
    flat value — and the Point replay lane is the one that reports undeclared
    props. WS-C §7.1 requires the declaration ("declaration, not permission")
    so the shared live+replay writer does not log a false drift warning for a
    documented money prop on every rebuild (#2782)."""

    def test_the_five_fields_are_declared_for_point_replay(self):
        from tortoise.projection.entities import _EntityHandlers
        declared = _EntityHandlers._POINT_DECLARED_PROPS
        for field in MONEY_VALUE_FIELDS:
            assert field in declared, (
                f"money field {field!r} is not declared on the Point lane — a "
                "rebuild then logs a false 'not declared' drift warning")

    def test_the_declaration_is_derived_from_the_money_shape(self):
        """Single-sourced, so a field ADDED to `money_props` cannot silently
        stay undeclared here — the two would drift and the drift would show up
        only as rebuild noise."""
        from tortoise.projection.entities import _EntityHandlers
        assert set(MONEY_VALUE_FIELDS) <= _EntityHandlers._POINT_DECLARED_PROPS
        assert set(MONEY_VALUE_FIELDS) == set(money_props(
            COMMITTED_MINOR, "BRL", minor_unit_exponent=2,
            verbatim="R$ 250.000,00", as_of="2026-06-30"))

    def test_point_carries_the_money_value_and_survives_rebuild(
            self, journaled_sdk, caplog):
        import logging

        sdk, events = journaled_sdk
        sdk.create_point(
            "statement", "tranche one is R$ 250.000,00",
            **money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2,
                          verbatim="R$ 250.000,00", as_of="2026-06-30"))
        live = _point_row(sdk, "tranche one is")
        assert (live[AMOUNT_MINOR], live[CURRENCY], live[AS_OF]) == (
            COMMITTED_MINOR, "BRL", "2026-06-30")

        with caplog.at_level(logging.WARNING,
                             logger="tortoise.projection.entities"):
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

        after = _point_row(sdk, "tranche one is")
        assert (after[AMOUNT_MINOR], after[CURRENCY],
                after[MINOR_UNIT_EXPONENT], after[VERBATIM],
                after[AS_OF]) == (
            COMMITTED_MINOR, "BRL", 2, "R$ 250.000,00", "2026-06-30")
        undeclared = [r.getMessage() for r in caplog.records
                      if "is not declared" in r.getMessage()
                      and any(f"{f!r}" in r.getMessage()
                              for f in MONEY_VALUE_FIELDS)]
        assert undeclared == []

    def test_an_undeclared_point_prop_still_warns(self, journaled_sdk, caplog):
        """The discriminating control: the declaration suppresses the warning
        for the DECLARED shape only, so a genuinely undeclared prop still
        reports — the drift signal is not muted wholesale."""
        import logging

        sdk, events = journaled_sdk
        sdk.create_point("statement", "a claim with a stray prop",
                         frobnicator_level=7)
        with caplog.at_level(logging.WARNING,
                             logger="tortoise.projection.entities"):
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        assert any("'frobnicator_level' is not declared" in r.getMessage()
                   for r in caplog.records)
        assert _point_row(sdk, "a claim with a stray")["frobnicator_level"] == 7


# ── the grounded requirement end to end: released/tranche ratio ────────────

class TestTrancheReleaseScenario:
    def test_released_to_date_drives_the_unlock(self, journaled_sdk):
        """#2725's requirement, computed from the graph's own node props:
        committed on the Tranche Object, released across Disbursement
        Objects; the sum and the ratio decide the unlock."""
        sdk, _events = journaled_sdk
        sdk.create_object(
            "Tranche 1", "venture:tranche",
            **money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2,
                          verbatim="R$ 250.000,00"))
        for i, amt in enumerate([10_000_000, 8_750_000], start=1):
            sdk.create_event(
                f"Disbursement 1.{i}", "venture:disbursement",
                **money_props(amt, "BRL", minor_unit_exponent=2,
                              verbatim=f"R$ {amt}", as_of="2026-07-01"))

        rows = sdk._get_proj().g.query(
            "MATCH (n) WHERE n.amountMinor IS NOT NULL "
            "RETURN n.amountMinor AS amountMinor, n.currency AS currency, "
            "       n.minorUnitExponent AS minorUnitExponent").result_set
        committed = next(r for r in rows if r[0] == COMMITTED_MINOR)
        released_rows = [r for r in rows if r is not committed]

        # committed is the whole; the disbursements are the part
        whole = total_minor([{
            AMOUNT_MINOR: committed[0], CURRENCY: committed[1],
            MINOR_UNIT_EXPONENT: committed[2],
        }])["BRL"]
        part = total_minor([{
            AMOUNT_MINOR: r[0], CURRENCY: r[1], MINOR_UNIT_EXPONENT: r[2],
        } for r in released_rows])["BRL"]

        assert whole == COMMITTED_MINOR
        assert part == RELEASED_MINOR
        assert ratio_bp(part, whole) == 7_500
        assert ratio_meets(part, whole, 7_500) is True


# ── the write boundary: a value the reader refuses must not be writable ─────

#: The six shapes MEASURED landing on a node before the write gate existed
#: (#2782 re-measurement, 2026-11-10). Each one is refused by
#: `money.read_money_value` — i.e. each was already unreadable the moment it was
#: stored, which is what makes accepting it at write a durability bug rather
#: than a validation nicety.
MALFORMED_MONEY_SHAPES = [
    ("amount-with-no-currency", {"amountMinor": COMMITTED_MINOR}),
    ("float-amount", {"amountMinor": 25_000_000.5, "currency": "BRL",
                      "minorUnitExponent": 2}),
    ("mis-scaled-jpy", {"amountMinor": COMMITTED_MINOR, "currency": "JPY",
                        "minorUnitExponent": 2}),
    ("currency-with-no-exponent", {"amountMinor": COMMITTED_MINOR,
                                   "currency": "BRL"}),
    ("currency-alone", {"currency": "BRL"}),
    ("unknown-currency-code", {"amountMinor": COMMITTED_MINOR,
                               "currency": "XXX", "minorUnitExponent": 2}),
]


#: What a one-sided UPDATE may not do to a well-formed value. The first four
#: are "no money" read as a BAG — the reader engages on neither an
#: ``amountMinor`` nor a ``currency``, so a bag carrying only a deletion or only
#: an exponent is "no money" to it — and a gate grading the caller's props
#: therefore ADMITS them. Measured at #11836 on the pre-fix head: they were
#: ACCEPTED and the node was left unreadable. The last three ARE visible to a
#: bag-graded gate and were already refused by it; they are pinned here so the
#: merged-state rule does not regress the refusals it inherits.
ONE_SIDED_UPDATES = [
    ("currency-deleted", {"currency": None}),
    ("amount-deleted", {"amountMinor": None}),
    ("exponent-deleted", {"minorUnitExponent": None}),
    ("mis-scaled-exponent", {"minorUnitExponent": 3}),
    ("lowercase-currency", {"currency": "brl"}),
    ("float-amount", {"amountMinor": COMMITTED_MINOR + 0.5}),
    ("unknown-currency", {"currency": "XXX"}),
]


class TestWriteBoundary:
    """#2782 — the money shape is enforced at the WRITE boundary too.

    The 2026-11-10 re-measurement on this issue recorded that a caller "still
    cannot *hand us* an amount" and that a half-specified one is "only refused
    (or silently tolerated) at consume". The consume refusal is not harmless:
    `total_minor` refuses the WHOLE sum on the first malformed member, so one
    accepted malformed write makes "committed / released to date" unanswerable
    for every well-formed amount beside it. These tests pin the refusal at the
    door, and pin that the door is the reader's own rule (so the two cannot
    drift)."""

    @pytest.mark.parametrize("label,props",
                             MALFORMED_MONEY_SHAPES,
                             ids=[s[0] for s in MALFORMED_MONEY_SHAPES])
    def test_an_unreadable_money_value_is_refused_at_write(
            self, journaled_sdk, label, props):
        sdk, _events = journaled_sdk
        with pytest.raises(ValueError) as exc:
            sdk.create_object(f"Tranche {label}", "venture:tranche", **props)
        # the refusal names the write boundary and the field at fault — a
        # caller cannot act on "invalid input"
        assert "write boundary" in str(exc.value), str(exc.value)
        assert any(k in str(exc.value) for k in
                   ("amountMinor", "currency", "minorUnitExponent")), \
            str(exc.value)
        # and nothing landed: a refused write leaves no half-value behind
        rows = sdk._get_proj().g.query(
            "MATCH (n) WHERE n.amountMinor IS NOT NULL OR n.currency IS NOT NULL "
            "RETURN count(n)").result_set
        assert rows[0][0] == 0, "a refused money write still reached the graph"

    @pytest.mark.parametrize("door", ["create_object", "create_event"])
    def test_every_entity_door_shares_the_one_gate(self, journaled_sdk, door):
        """The gate lives at the shared intake (`_sanitize_props`), not on one
        label's door — so a second label cannot be the way in."""
        sdk, _events = journaled_sdk
        writer = getattr(sdk, door)
        with pytest.raises(ValueError, match="write boundary"):
            writer("Half a tranche", "venture:tranche",
                   amountMinor=COMMITTED_MINOR)

    def test_the_point_door_is_covered_too(self, journaled_sdk):
        sdk, _events = journaled_sdk
        with pytest.raises(ValueError, match="write boundary"):
            sdk.create_point("statement", "tranche one is 250000 BRL",
                             amountMinor=COMMITTED_MINOR)

    def test_an_update_cannot_install_a_malformed_value_either(
            self, journaled_sdk):
        """A create-only gate is one verb away from useless: an update that adds
        a half value is the same unreadable record.

        ⚠️ The node is deliberately BARE. Until #11836 this test started from a
        well-formed value and asserted that `update_entity(id, currency="BRL")`
        (a restatement of the currency already stored) was refused — i.e. it
        pinned the gate grading the BAG rather than the state the write leaves.
        Those two readings differ both ways, and the bag reading was the defect:
        it refused this harmless restatement while ADMITTING
        `update_entity(id, currency=None)`, which deletes the currency from a
        well-formed value. The gate now grades the merged state, so:
          * half value onto a bare node → REFUSED (this test);
          * restatement of a member that already matches → ADMITTED
            (`test_a_complete_update_that_restates_nothing_is_admitted`);
          * a member DELETED from a well-formed value → REFUSED
            (`test_a_one_sided_update_cannot_strip_a_well_formed_value`).
        """
        sdk, _events = journaled_sdk
        bare = sdk.create_object("Tranche bare", "venture:tranche")
        with pytest.raises(ValueError, match="write boundary"):
            sdk.update_entity(bare["id"], currency="BRL")
        assert read_money_value(sdk.get_entity(bare["id"])) is None

    @pytest.mark.parametrize("label,update", ONE_SIDED_UPDATES,
                             ids=[s[0] for s in ONE_SIDED_UPDATES])
    @pytest.mark.parametrize("door", ["update_entity", "update_point"])
    def test_a_one_sided_update_cannot_strip_a_well_formed_value(
            self, journaled_sdk, door, label, update):
        """The gate grades the state the UPDATE LEAVES, never the bag.

        `SET n += $props` REMOVES a property whose value is null, so a bag that
        says `currency=None` carries no money and passes any bag-shaped gate —
        while it strips the currency off a well-formed value, after which
        `total_minor` refuses the WHOLE set. `minorUnitExponent=3` on BRL is the
        mis-scale this value exists to refuse and is equally invisible to a
        bag-shaped gate. Both directions were measured on the pre-fix head of
        #11836: `currency=None` ACCEPTED, and the sums unreadable.
        """
        sdk, _events = journaled_sdk
        value = money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2,
                            verbatim="R$ 250.000,00")
        if door == "update_entity":
            created = sdk.create_object("Tranche one-sided",
                                        "venture:tranche", **value)
        else:
            created = sdk.create_point("statement",
                                       "tranche one is 250000 BRL", **value)
        with pytest.raises(ValueError, match="write boundary"):
            getattr(sdk, door)(created["id"], **update)
        # nothing landed, and the sum that the gate exists to keep answerable
        # still answers
        stored = sdk.get_entity(created["id"])
        assert read_money_value(stored) == ("BRL", COMMITTED_MINOR, 2), stored
        assert total_minor([stored]) == {"BRL": COMMITTED_MINOR}

    def test_a_complete_update_that_restates_nothing_is_admitted(
            self, journaled_sdk):
        """The other half of the same rule: a PART of a value is not a
        malformed value. `amountMinor=…` alone is coherent with the stored
        currency and exponent — and a bag-shaped gate REFUSED it, a false block
        on an ordinary edit (measured at #11836)."""
        sdk, _events = journaled_sdk
        created = sdk.create_object(
            "Tranche partial", "venture:tranche",
            **money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2))
        sdk.update_entity(created["id"], amountMinor=RELEASED_MINOR)
        assert read_money_value(sdk.get_entity(created["id"])) == \
            ("BRL", RELEASED_MINOR, 2)
        # and a member restated at the value it already holds is not a half
        # value — the pre-#11836 bag-shaped gate refused exactly this
        sdk.update_entity(created["id"], currency="BRL")
        assert read_money_value(sdk.get_entity(created["id"])) == \
            ("BRL", RELEASED_MINOR, 2)
        # RE-DENOMINATING at the value's own scale is admitted too, and that is
        # deliberate (it is what the docstring states): the result is a
        # complete readable value, so `currency="USD"` on a BRL carrier is the
        # caller asking for the same minor units under another code. The bag-
        # shaped gate refused it; the merged-state rule does not pretend a
        # legal edit is a malformed value. What it will not admit is a scale
        # the table contradicts (`minorUnitExponent=3` on BRL, one of the
        # ONE_SIDED_UPDATES shapes).
        sdk.update_entity(created["id"], currency="USD")
        assert read_money_value(sdk.get_entity(created["id"])) == \
            ("USD", RELEASED_MINOR, 2)

    def test_deleting_the_whole_value_is_still_allowed(self, journaled_sdk):
        """Clearing every member leaves NO money — a legitimate state (the
        node is simply not a money carrier), so the gate must not turn a
        deliberate delete into an error."""
        sdk, _events = journaled_sdk
        created = sdk.create_object(
            "Tranche cleared", "venture:tranche",
            **money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2))
        sdk.update_entity(created["id"], amountMinor=None, currency=None,
                          minorUnitExponent=None)
        assert read_money_value(sdk.get_entity(created["id"])) is None

    def test_the_optional_members_are_graded_with_the_value(self,
                                                            journaled_sdk):
        """`verbatim` and `asOf` are money members with their own validators,
        and `read_money_value` never applied them — so an int `verbatim` was
        durable state `money_props` would have refused. They are graded at the
        DOOR when the value carries money, and left alone when it does not
        (they are somebody else's props then)."""
        sdk, _events = journaled_sdk
        value = money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2)
        with pytest.raises(ValueError, match="write boundary"):
            sdk.create_object("Tranche bad verbatim", "venture:tranche",
                              verbatim=123, **value)
        created = sdk.create_object("Tranche cited", "venture:tranche",
                                    **value)
        with pytest.raises(ValueError, match="write boundary"):
            sdk.update_entity(created["id"], asOf="not-a-date")
        # no money in the bag: `asOf` is an ordinary prop, untouched
        note = sdk.create_object("Note asOf", "venture:note",
                                 asOf="2026-06-30")
        assert sdk.get_entity(note["id"])["asOf"] == "2026-06-30"

    def test_the_gate_is_the_readers_own_rule(self):
        """One home: every shape the reader refuses is refused at the door, and
        every shape the reader accepts is admitted — a second, hand-rolled list
        here would be the drift this test exists to catch."""
        from tortoise.money import refuse_malformed_money
        for _label, props in MALFORMED_MONEY_SHAPES:
            with pytest.raises(MoneyValueError):
                read_money_value(props)          # the reader refuses it
            with pytest.raises(MoneyValueError):
                refuse_malformed_money(props)    # so the door must too
        good = money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2,
                           verbatim="R$ 250.000,00", as_of="2026-06-30")
        assert read_money_value(good) == ("BRL", COMMITTED_MINOR, 2)
        assert refuse_malformed_money(good) is None

    @pytest.mark.parametrize("prop", [VERBATIM, AS_OF, MINOR_UNIT_EXPONENT])
    def test_a_non_money_prop_is_not_collateral_damage(self, journaled_sdk,
                                                       prop):
        """`MONEY_VALUE_FIELDS` is the money GROUP, not five reserved names: a
        sibling alone carries no amount, so it is not a money value — the gate
        inherits the reader's engagement rule and must stay a no-op."""
        sdk, _events = journaled_sdk
        value = {VERBATIM: "some quoted text",
                 AS_OF: "2026-06-30",
                 MINOR_UNIT_EXPONENT: 2}[prop]
        created = sdk.create_object(f"Object with {prop}", "venture:note",
                                    **{prop: value})
        assert sdk.get_entity(created["id"])[prop] == value

    def test_a_malformed_value_can_no_longer_poison_the_sum(self,
                                                            journaled_sdk):
        """The reason the gate exists, end to end: the sum that the indicator-3
        requirement rides stays answerable because the unreadable member cannot
        be written."""
        sdk, _events = journaled_sdk
        sdk.create_object("Tranche P", "venture:tranche",
                          **money_props(COMMITTED_MINOR, "BRL",
                                        minor_unit_exponent=2,
                                        verbatim="R$ 250.000,00"))
        with pytest.raises(ValueError, match="write boundary"):
            sdk.create_object("Tranche bad", "venture:tranche",
                              amountMinor=RELEASED_MINOR)  # no currency
        rows = sdk._get_proj().g.query(
            "MATCH (n) WHERE n.amountMinor IS NOT NULL "
            "RETURN n.amountMinor AS amountMinor, n.currency AS currency, "
            "       n.minorUnitExponent AS minorUnitExponent").result_set
        assert total_minor([{
            AMOUNT_MINOR: r[0], CURRENCY: r[1], MINOR_UNIT_EXPONENT: r[2],
        } for r in rows]) == {"BRL": COMMITTED_MINOR}


#: The two doors whose write is a bare ``SET n += $props`` — the only doors
#: where a key present with ``None`` DELETES the property (#2782 review
#: Finding 1). Both are updates, so both carry the update-shaped gate.
UPDATE_DOORS = ("update_entity", "update_point")


def _create_money_carrier(sdk, door: str, name: str = "Tranche U") -> str:
    """A well-formed money carrier, addressed by the verb ``door`` updates."""
    if door == "update_point":
        return sdk.create_point(
            "statement", "one tranche is 250000 BRL",
            **money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2,
                          verbatim="R$ 250.000,00"))["id"]
    return sdk.create_object(
        name, "venture:tranche",
        **money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2,
                      verbatim="R$ 250.000,00"))["id"]


def _money_node(sdk, node_id: str) -> dict:
    rows = sdk._get_proj().g.query(
        "MATCH (n {id:$id}) "
        "RETURN n.amountMinor, n.currency, n.minorUnitExponent",
        params={"id": node_id}).result_set
    assert rows, f"no node {node_id!r}"
    return {AMOUNT_MINOR: rows[0][0], CURRENCY: rows[0][1],
            MINOR_UNIT_EXPONENT: rows[0][2]}


def _money_node_by_event_id(sdk, event_id: str) -> dict:
    """The `:Event` shape `session_indexer` mints — addressed by `eventId`, with
    no `id` at all — read back by the key it actually carries."""
    rows = sdk._get_proj().g.query(
        "MATCH (n:Event {eventId:$eid}) "
        "RETURN n.amountMinor, n.currency, n.minorUnitExponent",
        params={"eid": event_id}).result_set
    assert rows, f"no :Event {event_id!r}"
    return {AMOUNT_MINOR: rows[0][0], CURRENCY: rows[0][1],
            MINOR_UNIT_EXPONENT: rows[0][2]}


def _whole_sum(sdk) -> dict:
    """`total_minor` over every node that carries an amount — the indicator-3
    reading, which REFUSES the whole sum on the first unreadable member."""
    rows = sdk._get_proj().g.query(
        "MATCH (n) WHERE n.amountMinor IS NOT NULL "
        "RETURN n.amountMinor, n.currency, n.minorUnitExponent").result_set
    return total_minor([{AMOUNT_MINOR: r[0], CURRENCY: r[1],
                         MINOR_UNIT_EXPONENT: r[2]} for r in rows])


class TestUpdateDoorCannotDeleteOrMisScale:
    """#2782 review Finding 1 — an UPDATE is a MERGE onto an existing value.

    The head gate inherited the reader's engagement rule (``amountMinor`` /
    ``currency`` non-None). That rule is right for ONE bag and wrong for an
    update, where the bag is merged onto a node that already carries the rest:

      * ``{"minorUnitExponent": None}`` read as "no money", so the gate no-oped
        — and the write is a bare ``SET n += $props``, where an explicit
        ``None`` REMOVES the property. The node was left holding
        ``{amountMinor, currency}``: the half-value this gate exists to refuse.
      * ``{"minorUnitExponent": 0}`` (any lone exponent) could not be
        scale-checked at all (the check is a ``(currency, exponent)`` pair and
        the stored currency is not visible to a bag-shaped predicate), so a
        100x mis-scale on a ``BRL`` node landed.

    Either way the money value stops being readable and ``total_minor`` refuses
    the WHOLE sum — the failure the write gate exists to prevent, reached one
    verb over. Measured on both update doors, embedded and on a real FalkorDB
    (review addendum, row for row).
    """

    @pytest.mark.parametrize("door", UPDATE_DOORS)
    @pytest.mark.parametrize("field", [AMOUNT_MINOR, CURRENCY,
                                       MINOR_UNIT_EXPONENT])
    def test_an_explicit_null_cannot_delete_a_money_field(self, journaled_sdk,
                                                          door, field):
        sdk, _events = journaled_sdk
        node_id = _create_money_carrier(sdk, door)
        with pytest.raises(ValueError) as exc:
            getattr(sdk, door)(node_id, **{field: None})
        assert "write boundary" in str(exc.value), str(exc.value)
        assert field in str(exc.value), str(exc.value)
        # the value the caller never asked to lose is still there …
        assert _money_node(sdk, node_id) == {
            AMOUNT_MINOR: COMMITTED_MINOR, CURRENCY: "BRL",
            MINOR_UNIT_EXPONENT: 2}
        # … and the sum is still answerable
        assert _whole_sum(sdk) == {"BRL": COMMITTED_MINOR}

    @pytest.mark.parametrize("door", UPDATE_DOORS)
    @pytest.mark.parametrize("exponent", [
        9,   # not an exponent at all
        0,   # a legal exponent for JPY/PYG — a 100x mis-scale for BRL
    ])
    def test_a_lone_exponent_cannot_be_installed_on_an_update(
            self, journaled_sdk, door, exponent):
        """A lone exponent on a MONEY node is graded against the stored value
        (the `(currency, exponent)` pair is what the table contradicts), so the
        mis-scale is refused even though the bag carries no currency."""
        sdk, _events = journaled_sdk
        node_id = _create_money_carrier(sdk, door)
        with pytest.raises(ValueError) as exc:
            getattr(sdk, door)(node_id, **{MINOR_UNIT_EXPONENT: exponent})
        assert "write boundary" in str(exc.value), str(exc.value)
        assert MINOR_UNIT_EXPONENT in str(exc.value), str(exc.value)
        assert _money_node(sdk, node_id) == {
            AMOUNT_MINOR: COMMITTED_MINOR, CURRENCY: "BRL",
            MINOR_UNIT_EXPONENT: 2}
        assert _whole_sum(sdk) == {"BRL": COMMITTED_MINOR}

    @pytest.mark.parametrize("door", UPDATE_DOORS)
    def test_the_same_exponent_the_node_already_holds_is_a_no_op(
            self, journaled_sdk, door):
        """The merged state decides, and the no-op is well-formed.

        The bag-shaped rule refused this ("a lone exponent cannot be
        scale-checked here") because it could not SEE the stored currency; the
        merged-state rule can, so `minorUnitExponent=2` on a BRL node at 2 is a
        write that leaves a valid value and is admitted. That is not a
        relaxation of the mis-scale refusal — `0` and `9` above are still
        refused — it is the same predicate applied to the state instead of to
        the bag, and it is what makes a re-run of a create (`create_point`'s
        dedup relay, `_create_money_carrier` above) idempotent."""
        sdk, _events = journaled_sdk
        node_id = _create_money_carrier(sdk, door)
        getattr(sdk, door)(node_id, **{MINOR_UNIT_EXPONENT: 2})
        assert _money_node(sdk, node_id) == {
            AMOUNT_MINOR: COMMITTED_MINOR, CURRENCY: "BRL",
            MINOR_UNIT_EXPONENT: 2}
        assert _whole_sum(sdk) == {"BRL": COMMITTED_MINOR}

    @pytest.mark.parametrize("door", UPDATE_DOORS)
    def test_a_whole_valid_value_is_still_an_updatable_write(self, journaled_sdk,
                                                             door):
        """The refusal is narrow: the shape ``money_props`` always produces —
        the whole value — still replaces one wholesale."""
        sdk, _events = journaled_sdk
        node_id = _create_money_carrier(sdk, door)
        getattr(sdk, door)(node_id, **money_props(
            RELEASED_MINOR, "BRL", minor_unit_exponent=2,
            verbatim="R$ 187.500,00"))
        assert _money_node(sdk, node_id) == {
            AMOUNT_MINOR: RELEASED_MINOR, CURRENCY: "BRL",
            MINOR_UNIT_EXPONENT: 2}
        assert _whole_sum(sdk) == {"BRL": RELEASED_MINOR}

    @pytest.mark.parametrize("door,prop", [
        ("update_entity", {"title": "Tranche U (renamed)"}),
        ("update_point", {"namespace": "finance"}),
    ])
    def test_a_non_money_update_on_a_money_node_is_untouched(
            self, journaled_sdk, door, prop):
        sdk, _events = journaled_sdk
        node_id = _create_money_carrier(sdk, door)
        getattr(sdk, door)(node_id, **prop)
        assert _money_node(sdk, node_id) == {
            AMOUNT_MINOR: COMMITTED_MINOR, CURRENCY: "BRL",
            MINOR_UNIT_EXPONENT: 2}

    def test_a_create_point_dedup_re_run_stays_idempotent(self, journaled_sdk):
        """A dedup hit is graded as an UPDATE, and a lone `minorUnitExponent` is a
        legitimate stray property on a create
        (`test_a_non_money_prop_is_not_collateral_damage`) — so the re-run must
        stay the no-op the first run was.

        #11836 review P2 (measured): the blanket lone-exponent refusal the update
        door carried refused the dedup relay, so the FIRST create succeeded and the
        SECOND, identical call RAISED — `ingest` calls `create_point(..., **props)`
        once per item, so re-ingest stopped being an idempotent no-op. This test is
        red against that rule and green under merged-state grading.

        ⚠️ Scope, stated: this does NOT discriminate bag grading from merged-state
        grading on its own — the bag here carries no amount and no currency, so a
        bag-shaped predicate no-ops on it too (a VGATE mutation check measured
        exactly that). The discrimination lives in
        `test_a_dedup_re_run_cannot_mis_scale_an_existing_money_point`, where the
        stored value is what decides.
        """
        sdk, _events = journaled_sdk
        first = sdk.create_point("statement", "curtains are blue",
                                 dedup=True, minorUnitExponent=2)
        again = sdk.create_point("statement", "curtains are blue",
                                 dedup=True, minorUnitExponent=2)
        assert again["id"] == first["id"], "the second call must dedup onto the first"
        assert sdk.get_point(first["id"])[MINOR_UNIT_EXPONENT] == 2

    def test_a_dedup_re_run_cannot_mis_scale_an_existing_money_point(
            self, journaled_sdk):
        """The discriminating row for the relay: on a MONEY carrier, a lone
        exponent in the re-run is refused because the merged state is a 100x
        mis-scale — while bag grading ADMITS it (the bag carries no
        `amountMinor`/`currency`, so it engages on nothing) and leaves
        `{amountMinor, currency, 0}`, which makes `total_minor` refuse the whole
        set.

        So the relay is graded on the state the write would LEAVE, not on the bag
        the caller passed — measured: with the bag restored as the graded view,
        this test fails on the `pytest.raises`.
        """
        sdk, _events = journaled_sdk
        pid = sdk.create_point(
            "statement", "tranche one is 250000 BRL",
            **money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2))["id"]
        with pytest.raises(ValueError, match="write boundary"):
            sdk.create_point("statement", "tranche one is 250000 BRL",
                             dedup=True, minorUnitExponent=0)
        assert _money_node(sdk, pid) == {
            AMOUNT_MINOR: COMMITTED_MINOR, CURRENCY: "BRL",
            MINOR_UNIT_EXPONENT: 2}
        assert _whole_sum(sdk) == {"BRL": COMMITTED_MINOR}



class TestALegacyOptionalMemberDoesNotBrickTheRow:
    """#11836 review Finding 1 (P1, measured): the merged-state view must carry
    the SCALE triple from the node and the two OPTIONAL members only from the
    CALLER.

    ``verbatim``/``asOf`` describe provenance, not scale, and the read side is
    deliberately lenient about them so rows written before this rule keep
    reading (``_validate_present_optional_fields``: "pre-existing data keeps
    reading"). Carrying the STORED ones into the graded view contradicted that
    in the one population the leniency exists for: a node holding
    ``asOf='Q3 2026'`` — accepted by the props passthrough before this change,
    and still reproduced by ``rebuild_all`` because replay is deliberately
    ungated — was REFUSED on ``update_entity(id, amountMinor=…)`` and on the
    ordinary idempotent capture re-ingest, for a member the caller never
    mentioned, with ``asOf=None`` as the only escape. A legacy row that cannot
    be written is a false block, and the write door is not the place to
    re-litigate a value it cannot see.
    """

    @pytest.mark.parametrize("door", UPDATE_DOORS)
    def test_a_legacy_non_iso_as_of_does_not_block_an_ordinary_update(
            self, journaled_sdk, door):
        sdk, _events = journaled_sdk
        node_id = _create_money_carrier(sdk, door)
        # The pre-change shape: a valid scale triple beside a free-text date.
        sdk._get_proj().g.query(
            "MATCH (n {id:$id}) SET n.asOf = $asOf",
            params={"id": node_id, "asOf": "Q3 2026"},
        )
        stored_asof = sdk._get_proj().g.query(
            "MATCH (n {id:$id}) RETURN n.asOf",
            params={"id": node_id}).result_set
        assert stored_asof[0][0] == "Q3 2026"
        # … and the value still reads, which is why the row exists.
        assert _whole_sum(sdk) == {"BRL": COMMITTED_MINOR}

        getattr(sdk, door)(node_id, amountMinor=COMMITTED_MINOR + 1)

        node = _money_node(sdk, node_id)
        assert node[AMOUNT_MINOR] == COMMITTED_MINOR + 1
        assert node[CURRENCY] == "BRL"
        assert node[MINOR_UNIT_EXPONENT] == 2
        # the legacy member is untouched — ordinary `SET +=` semantics of an
        # unnamed key, not this gate's business
        after_asof = sdk._get_proj().g.query(
            "MATCH (n {id:$id}) RETURN n.asOf",
            params={"id": node_id}).result_set
        assert after_asof[0][0] == "Q3 2026"

    @pytest.mark.parametrize("door", UPDATE_DOORS)
    def test_the_caller_s_own_as_of_is_still_graded(self, journaled_sdk, door):
        """The other half: leniency about a STORED member must not become
        leniency about the one the caller is writing NOW."""
        sdk, _events = journaled_sdk
        node_id = _create_money_carrier(sdk, door)
        with pytest.raises(ValueError, match="write boundary"):
            getattr(sdk, door)(node_id, amountMinor=COMMITTED_MINOR + 1,
                               asOf="Q3 2026")
        assert _money_node(sdk, node_id) == {
            AMOUNT_MINOR: COMMITTED_MINOR, CURRENCY: "BRL",
            MINOR_UNIT_EXPONENT: 2}
        assert _whole_sum(sdk) == {"BRL": COMMITTED_MINOR}

    def test_a_legacy_verbatim_does_not_block_the_idempotent_re_ingest(
            self, journaled_sdk):
        """The population the P1 actually bit: a capture re-ingest. The stored
        ``verbatim`` is not a string; a re-run naming the same money must not
        raise, or re-ingest stops being idempotent for that row."""
        sdk, _events = journaled_sdk
        pid = sdk.create_point(
            "statement", "tranche one is 250000 BRL",
            **money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2))["id"]
        sdk._get_proj().g.query(
            "MATCH (n {id:$id}) SET n.verbatim = $verbatim",
            params={"id": pid, "verbatim": 123},
        )
        twice = sdk.create_point(
            "statement", "tranche one is 250000 BRL", dedup=True,
            **money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2))
        assert twice["id"] == pid
        node = _money_node(sdk, pid)
        assert node[AMOUNT_MINOR] == COMMITTED_MINOR
        assert node[CURRENCY] == "BRL"



class TestTheProbeUsesTheWritesOwnKeyDiscipline:
    """#11836 review Finding 1 (P1, measured): the merged-state probe matched
    ``{id:$id}`` while the write it guards resolves by the label's OWN identity
    key — so on an ``:Event`` keyed by ``eventId`` the gate was inert in BOTH
    directions.

    ``_update_entity`` iterates ``_CANONICAL_ENTITY_ID_PROPS`` and each label's
    secondary key, so ``:Event`` is reached by ``eventId``. A probe on ``id``
    missed the very node the ``SET n += $props`` touched, fell through to the
    bag-only arm, and measured: ``update_entity(eventId, currency=None)`` was
    ADMITTED, leaving ``{amountMinor, None, 2}`` — a stored value that read fine
    made unreadable, with ``total_minor`` refusing the whole set — while
    ``update_entity(eventId, amountMinor=…)`` was REFUSED. ``:Event`` nodes with
    no ``id`` are not hypothetical: ``session_indexer`` mints them
    (``CREATE (e:Event {eventId:$eid})``).
    """

    def test_the_probe_enumerates_exactly_what_the_write_iterates(self):
        """One source of truth: if these two ever diverge, the gate grades a
        different node from the one the write touches — silently."""
        from tortoise.projection import (
            _CANONICAL_ENTITY_ID_PROPS,
            secondary_entity_id_props,
        )
        from tortoise.sdk import _entity_identity_probes

        assert _entity_identity_probes() == tuple(
            (f":{label}", key)
            for label, prop in _CANONICAL_ENTITY_ID_PROPS
            for key in (prop, *secondary_entity_id_props(label))
        )

    @staticmethod
    def _indexer_shaped_event(sdk, event_id: str, *,
                              node_id: str | None = None) -> None:
        """The shape `session_indexer` mints: an `:Event` addressed by
        `eventId`, optionally with no `id` at all."""
        sdk._get_proj().g.query(
            "CREATE (e:Event {eventId:$eid}) SET e += $props",
            params={"eid": event_id, "props": (
                {} if node_id is None else {"id": node_id})},
        )

    @pytest.mark.parametrize("with_id", [False, True])
    def test_a_currency_delete_by_event_id_is_refused(self, journaled_sdk,
                                                      with_id):
        """The FAIL-OPEN half, on both shapes: the indexer's id-less event, and
        one whose `id` differs from the `eventId` the caller addresses."""
        sdk, _events = journaled_sdk
        event_id = "ev_gate_11836"
        self._indexer_shaped_event(
            sdk, event_id,
            node_id=None if not with_id else "some-other-id")
        sdk.update_entity(event_id,
                          **money_props(COMMITTED_MINOR, "BRL",
                                        minor_unit_exponent=2))
        with pytest.raises(ValueError, match="write boundary"):
            sdk.update_entity(event_id, currency=None)
        node = _money_node_by_event_id(sdk, event_id)
        assert node[AMOUNT_MINOR] == COMMITTED_MINOR
        assert node[CURRENCY] == "BRL"
        assert node[MINOR_UNIT_EXPONENT] == 2

    @pytest.mark.parametrize("with_id", [False, True])
    def test_a_partial_edit_by_event_id_is_admitted(self, journaled_sdk,
                                                    with_id):
        """The FALSE-BLOCK half: `amountMinor` alone is a complete edit once the
        stored currency/exponent are visible — which is the entire point of
        grading the merged state."""
        sdk, _events = journaled_sdk
        event_id = "ev_gate_11836_partial"
        self._indexer_shaped_event(
            sdk, event_id,
            node_id=None if not with_id else "other-id-for-partial")
        sdk.update_entity(event_id,
                          **money_props(COMMITTED_MINOR, "BRL",
                                        minor_unit_exponent=2))
        sdk.update_entity(event_id, amountMinor=COMMITTED_MINOR + 1)
        node = _money_node_by_event_id(sdk, event_id)
        assert node[AMOUNT_MINOR] == COMMITTED_MINOR + 1
        assert node[CURRENCY] == "BRL"
        assert node[MINOR_UNIT_EXPONENT] == 2
        assert _whole_sum(sdk) == {"BRL": COMMITTED_MINOR + 1}

    @pytest.mark.parametrize("money_label_first", [True, False])
    def test_one_call_that_stamps_two_labels_grades_both(
            self, journaled_sdk, money_label_first):
        """The FAN-OUT half (VGATE round 8): `_update_entity` writes EVERY label
        whose identity key equals the caller's `$id`, so one call can stamp a
        `:Point` and an `:Object` that share an id. A probe that returned at the
        first matching pair graded one of them and left the other ungraded —
        measured: a money-free `:Point{id:'dup'}` beside a `:Object{id:'dup'}`
        carrying a complete triple made ``update_entity('dup', currency=None)``
        ADMITTED, leaving ``{amountMinor, None, 2}`` and making ``total_minor``
        refuse the whole set. The pre-change unlabelled ``MATCH`` did not have
        this hole (it returned every row), so it is a regression the union
        closes, and the ORDER of the two nodes must not matter.
        """
        sdk, _events = journaled_sdk
        g = sdk._get_proj().g
        money = money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2)
        if money_label_first:
            g.query("CREATE (o:Object {id:'dup-11836'}) SET o += $p",
                    params={"p": money})
            g.query("CREATE (pt:Point {id:'dup-11836'})")
        else:
            g.query("CREATE (pt:Point {id:'dup-11836'})")
            g.query("CREATE (o:Object {id:'dup-11836'}) SET o += $p",
                    params={"p": money})
        with pytest.raises(ValueError, match="write boundary"):
            sdk.update_entity("dup-11836", currency=None)
        rows = g.query(
            "MATCH (o:Object {id:'dup-11836'}) "
            "RETURN o.amountMinor, o.currency, o.minorUnitExponent"
        ).result_set
        assert tuple(rows[0]) == (COMMITTED_MINOR, "BRL", 2), (
            "the :Object's value was stripped by a write the gate admitted")
        assert _whole_sum(sdk) == {"BRL": COMMITTED_MINOR}


class TestTheUpdateArmOfThePredicate:
    """The rule itself, without a graph — so the boundary is readable in one
    place: an UPDATE is graded on the money state it LEAVES
    (:func:`money_view_after_update`), and a CREATE on its bag, which IS the
    state it leaves."""

    def test_a_lone_sibling_stays_a_no_op_on_a_create(self):
        from tortoise.money import refuse_malformed_money
        assert refuse_malformed_money({MINOR_UNIT_EXPONENT: 2}) is None
        assert refuse_malformed_money({VERBATIM: "quoted"}) is None
        assert refuse_malformed_money({AS_OF: "2026-06-30"}) is None

    def test_a_lone_exponent_is_refused_only_where_a_value_would_break(self):
        from tortoise.money import money_view_after_update, refuse_malformed_money
        money = {AMOUNT_MINOR: COMMITTED_MINOR, CURRENCY: "BRL",
                 MINOR_UNIT_EXPONENT: 2}
        # on a money node: 0 is a 100x mis-scale for BRL — refused
        with pytest.raises(MoneyValueError):
            refuse_malformed_money(
                money_view_after_update(money, {MINOR_UNIT_EXPONENT: 0}))
        # on a node that carries no money: a stray property, exactly as on a
        # create → admitted (this is what keeps `create_point`'s dedup re-run
        # idempotent instead of raising on the second call)
        assert refuse_malformed_money(
            money_view_after_update({}, {MINOR_UNIT_EXPONENT: 2})) is None

    @pytest.mark.parametrize("field", [AMOUNT_MINOR, CURRENCY,
                                       MINOR_UNIT_EXPONENT])
    def test_an_explicit_null_is_refused_on_an_update_only(self, field):
        from tortoise.money import money_view_after_update, refuse_malformed_money
        money = {AMOUNT_MINOR: COMMITTED_MINOR, CURRENCY: "BRL",
                 MINOR_UNIT_EXPONENT: 2}
        # a create drops a None value (the fold excludes it) — harmless
        assert refuse_malformed_money({field: None}) is None
        # an update DELETES on it — the members left behind are a half value
        with pytest.raises(MoneyValueError):
            refuse_malformed_money(
                money_view_after_update(money, {field: None}))


#: The event type each ``EventAPI`` door journals — the seam test below asserts
#: the door's OWN record is absent. (Before #11836 this string was hardcoded to
#: ``ObjectRegistered`` for every door, so it was vacuous on the other two.)
_SEAM_DOOR_EVENT = {
    "add_object": "ObjectRegistered",
    "add_event": "EventRecorded",
    "add_point": "PointAdded",
}


class TestNonSanitizedSeams:
    """#2782 review Findings 2 and 3 — the two LIVE props writers that bypass
    ``_sanitize_props``, so the SDK intake's gate did not cover them.

    Finding 2 (``api.EventAPI``) landed the exact shape #2782 exists to refuse
    durably on a node (``amountMinor`` with no ``currency`` and no
    ``minorUnitExponent`` beside it — the reader spells the two missing members
    ``None``, the store holds no null key) through the seam the extractor, ``ingest``,
    ``docs_miner``, ``mining`` and the CLI all write through. Finding 3
    (``create_source``) reported ``ok`` while the closed ``:Source`` surface
    dropped the amount — and journalled it anyway.

    #11836 review Finding 1: gating this seam at the ``_emit`` ENVELOPE covered
    only the doors that splat their props at the top level (``add_object``,
    ``add_event``). The Point door — the one the extractor writes through —
    passes its RECORD under ``point=``, so the envelope gate could not see the
    money keys and ``add_point(..., amountMinor=…)`` landed.
    """

    @pytest.mark.parametrize("door", ["add_object", "add_event", "add_point"])
    def test_the_pipeline_seam_cannot_land_a_malformed_money_value(
            self, journaled_sdk, tmp_path, door):
        """All THREE prop-carrying doors, including the one that NESTS its
        payload (`add_point` → `_emit("PointAdded", point=p, …)`).

        #11836 review P1 (measured): the gate graded the flat record, so
        `add_point(content, provenance, amountMinor=25_000_000)` was ACCEPTED
        and left `amountMinor` durable with no currency beside it — the reader
        spells that `currency: None`, the store holds no null key — and it
        survived `rebuild_all` — while the same bag through `add_object` was
        refused. A gate that covers
        the flat doors and not the nesting one is a gate with a hole in the
        shape of the payload.
        """
        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        sdk, _events = journaled_sdk
        api_log = tmp_path / f"api-events-{door}.jsonl"
        api = EventAPI(EventLog(str(api_log)), initiated_by="extractor",
                       projection=sdk._get_proj())
        writer = getattr(api, door)
        # `add_point` takes (content, provenance, **fields) — its money bag
        # lands on the RECORD it nests under `point=`, not on the envelope.
        payload = {
            "add_object": ("Tranche api", "venture:tranche"),
            "add_event": ("evt-1", "venture:tranche"),
            "add_point": ("Tranche api point", {"run_id": "run-api"}),
        }[door]
        with pytest.raises(ValueError, match="write boundary"):
            writer(*payload, amountMinor=COMMITTED_MINOR)  # no currency
        # ⚠️ whole-graph count, safe only because `journaled_sdk` mints a fresh
        # DB per test — a session-scoped fixture would make this order-dependent.
        rows = sdk._get_proj().g.query(
            "MATCH (n) WHERE n.amountMinor IS NOT NULL RETURN count(n)"
        ).result_set
        assert rows[0][0] == 0, f"{door} landed a malformed money value"
        # The refusal runs BEFORE the append (the seam's commit point), so a
        # refused write leaves NO record for a replay to fold — asserted over
        # the WHOLE log, not over one record name: the earlier version checked
        # `"ObjectRegistered"` on every parameter, which was vacuous on the two
        # doors that emit a different record (#11836 review P2), and a
        # door-specific name still could not see any OTHER record appended
        # before the refusal.
        assert not api_log.exists() or api_log.read_text().strip() == "", \
            f"{door} appended a record before the refusal"
        assert not api_log.exists() or _SEAM_DOOR_EVENT[door] not in \
            api_log.read_text()

    def test_the_point_records_own_money_bag_is_gated(self, journaled_sdk,
                                                     tmp_path):
        """#11836 Finding 1, the discriminating row: the same malformed shape
        that the top-level doors refuse must be refused through ``add_point``,
        and the refusal must name the money field (not just any ValueError)."""
        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        sdk, _events = journaled_sdk
        api = EventAPI(EventLog(str(tmp_path / "api-events-point.jsonl")),
                       initiated_by="extractor", projection=sdk._get_proj())
        with pytest.raises(ValueError) as exc:
            api.add_point("Tranche point record", {"run_id": "run-1"},
                          amountMinor=COMMITTED_MINOR)  # no currency
        assert "write boundary" in str(exc.value), str(exc.value)
        assert CURRENCY in str(exc.value), str(exc.value)
        assert sdk._get_proj().g.query(
            "MATCH (n) WHERE n.amountMinor IS NOT NULL RETURN count(n)"
        ).result_set[0][0] == 0

    def test_a_wellformed_value_on_the_point_record_still_lands(
            self, journaled_sdk, tmp_path):
        """The walk widened WHERE the gate looks, never WHAT it refuses: the
        extractor's real money producer (a whole value on the Point record)
        still lands and still sums."""
        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        sdk, _events = journaled_sdk
        api = EventAPI(EventLog(str(tmp_path / "api-events-ok.jsonl")),
                       initiated_by="extractor", projection=sdk._get_proj())
        api.add_point("Tranche point paid", {"run_id": "run-2"},
                      **money_props(COMMITTED_MINOR, "BRL",
                                    minor_unit_exponent=2))
        p = _point_row(sdk, "Tranche point paid")
        assert p[AMOUNT_MINOR] == COMMITTED_MINOR
        assert p[CURRENCY] == "BRL"
        assert _whole_sum(sdk) == {"BRL": COMMITTED_MINOR}

    def test_a_lone_exponent_is_still_a_no_op_through_the_seam(
            self, journaled_sdk, tmp_path):
        """The seam is a CREATE-shaped door, so the lone-sibling no-op the SDK
        create doors keep (`TestWriteBoundary::
        test_a_non_money_prop_is_not_collateral_damage`) holds here too — the
        walk did not widen the gate's ENGAGEMENT rule."""
        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        sdk, _events = journaled_sdk
        api = EventAPI(EventLog(str(tmp_path / "api-events-sibling.jsonl")),
                       initiated_by="extractor", projection=sdk._get_proj())
        api.add_point("Bare exponent point", {"run_id": "run-3"},
                      minorUnitExponent=2)
        assert _whole_sum(sdk) == {}  # no amount, no currency → not money

    def test_the_pipeline_seam_still_takes_a_wellformed_value(self,
                                                              journaled_sdk,
                                                              tmp_path):
        """The gate is the reader's rule, not a ban on the seam: a well-formed
        money bag still lands — the extractor's money producer is untouched."""
        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        sdk, _events = journaled_sdk
        api = EventAPI(EventLog(str(tmp_path / "api-events.jsonl")),
                       initiated_by="extractor",
                       projection=sdk._get_proj())
        oid = api.add_object(
            "Tranche via api", "venture:tranche",
            **money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2))
        assert _money_node(sdk, oid) == {
            AMOUNT_MINOR: COMMITTED_MINOR, CURRENCY: "BRL",
            MINOR_UNIT_EXPONENT: 2}

    def test_create_source_refuses_a_malformed_bag_instead_of_dropping_it(
            self, journaled_sdk):
        sdk, events = journaled_sdk
        with pytest.raises(ValueError, match="write boundary"):
            sdk.create_source("https://ex.invalid/a", "web",
                              amountMinor=COMMITTED_MINOR)
        log = events / "events.jsonl"
        assert not log.exists() or "SourceCreated" not in log.read_text(), \
            "a refused create_source still journalled the money value"
        rows = sdk._get_proj().g.query(
            "MATCH (n) WHERE n.amountMinor IS NOT NULL RETURN count(n)"
        ).result_set
        assert rows[0][0] == 0

    def test_a_wellformed_money_value_on_a_source_is_still_dropped(
            self, journaled_sdk):
        """⚠️ STATED BOUND, not a claim of closure: a ``:Source`` has no
        declared money field at all (``_SOURCE_EXTRA_PROPS``, #3998's closed
        surface denies undeclared keys), so a WELL-FORMED value is still
        dropped from the NODE. That is a ``:Source`` surface decision — a Source
        carries provenance, not a value — and not something this gate may widen;
        the gate's job is that the caller is refused when the shape is one the
        reader could not read, rather than silently losing an amount.

        ⛔ The bound is about the NODE, and this test says so explicitly: the
        value still rides the ``SourceCreated`` JOURNAL record (measured:
        ``amountMinor=7000000, currency='BRL'`` in the journal, ``[None, None]``
        on the node) and the replay fold drops it again, so the graph stays
        consistent while the record lane holds a durable value until #3998's
        surface decision covers it. Pinning the journal half keeps the next
        reader from reading the node-only assertion as "nothing is stored".
        """
        sdk, events = journaled_sdk
        sdk.create_source("https://ex.invalid/b", "web",
                          **money_props(COMMITTED_MINOR, "BRL",
                                        minor_unit_exponent=2))
        rows = sdk._get_proj().g.query(
            "MATCH (s:Source {url:'https://ex.invalid/b'}) "
            "RETURN s.amountMinor").result_set
        assert rows, "the :Source was not created"
        assert rows[0][0] is None, "money reached a :Source surface"
        journal = events / "events.jsonl"
        assert journal.exists(), "the create did not journal"
        text = journal.read_text()
        assert "SourceCreated" in text, "no SourceCreated record in the journal"
        assert f'"amountMinor": {COMMITTED_MINOR}' in text, (
            "the bound moved: the journal no longer carries the well-formed "
            "value, so this docstring is stale")
        assert '"currency": "BRL"' in text


class TestKnownResidualCreateDoorOnMatch:
    """⚠️ MEASURED RESIDUAL (#2782) — recorded, not papered over.

    ``_create_entity`` MERGEs, so a re-create that resolves to an EXISTING node
    reaches it, and the fold's extra-props passthrough sets the caller's lone
    ``minorUnitExponent`` on that node. The UPDATE doors refuse this shape
    (``TestUpdateDoorCannotDeleteOrMisScale``); a CREATE door cannot, because
    the bag alone does not name the target and ``MONEY_VALUE_FIELDS`` is a group
    — a lone sibling on a NEW record is a legitimate write, pinned by
    ``test_a_non_money_prop_is_not_collateral_damage``. Closing this needs a
    target-aware check at the create funnels (a stored read on the id the door
    already resolved). When that lands, THIS pin must be inverted deliberately.
    Tracked as tortoise#11868, which also carries the second door this bound
    reaches — ``api.EventAPI.add_point(..., id=<existing point>)`` MERGEs onto
    that id too (measured: `minorUnitExponent=9` lands and `total_minor` then
    refuses the whole set).
    """

    def test_a_recreate_onto_an_existing_money_node_still_installs_a_lone_exponent(
            self, journaled_sdk):
        sdk, _events = journaled_sdk
        node_id = _create_money_carrier(sdk, "update_entity")
        # same name ⇒ the same id ⇒ the MERGE's ON MATCH arm
        sdk.create_object("Tranche U", "venture:tranche",
                          minorUnitExponent=9)
        assert _money_node(sdk, node_id)[MINOR_UNIT_EXPONENT] == 9
        with pytest.raises(MoneyValueError):
            _whole_sum(sdk)  # the consequence, stated rather than implied


class TestCreateDedupFoldIsIdempotent:
    """#11836 review Finding 1 — ``create_point(dedup=True)`` is ONE call.

    The verb advertises ``dedup=True`` as *idempotent creation*, and its CREATE
    leg judges the caller's bag with the CREATE rule. A dedup HIT, though, folds
    that same bag into ``update_point``, which judged it with the ADDRESSED-EDIT
    rule — so the second identical call was refused for exactly the shapes the
    first run admitted, or deleted a field the first run never touched:

        create_point(kind, content, dedup=True, minorUnitExponent=2)
          run 1 -> point created (a lone sibling is a create-legal stray, pinned
                   by test_a_non_money_prop_is_not_collateral_damage)
          run 2 -> MoneyValueError: minorUnitExponent cannot be validated on an
                   update without its currency in the same write

        create_point(..., dedup=True, verbatim=None)
          run 1 -> no-op (the create passthrough drops ``None``)
          run 2 -> the fold's ``SET n += $props`` DELETED ``verbatim``

    The tenant path is the ordinary one: ``tortoise_create_point`` defaults
    ``dedup=True`` (``mcp_server.py``), so "create the same thing twice" is a
    retry, not a hand-built edge case. Measured on the pre-fix tree: the lone
    exponent and both null spellings of the money group RAISED on run 2.
    """

    CONTENT = "one tranche is 250000 BRL"

    def test_a_lone_exponent_does_not_make_the_retry_raise(self, journaled_sdk):
        sdk, _events = journaled_sdk
        first = sdk.create_point("statement", self.CONTENT, dedup=True,
                                 minorUnitExponent=2)
        second = sdk.create_point("statement", self.CONTENT, dedup=True,
                                  minorUnitExponent=2)
        assert second["id"] == first["id"], "the retry minted a second point"
        # the retry is admitted AND still carries the create leg's value: the
        # fix widens where the gate looks, never what it engages on
        assert _point_row(sdk, self.CONTENT)[MINOR_UNIT_EXPONENT] == 2

    @pytest.mark.parametrize("field", [AMOUNT_MINOR, CURRENCY,
                                       MINOR_UNIT_EXPONENT, VERBATIM],
                             ids=[AMOUNT_MINOR, CURRENCY,
                                  MINOR_UNIT_EXPONENT, VERBATIM])
    def test_an_explicit_null_on_a_retry_neither_raises_nor_deletes(
            self, journaled_sdk, field):
        """A CREATE does not DELETE: the retry of an idempotent create must
        leave the value the first run wrote exactly as it was."""
        sdk, _events = journaled_sdk
        created = sdk.create_point(
            "statement", self.CONTENT, dedup=True,
            **money_props(COMMITTED_MINOR, "BRL", minor_unit_exponent=2,
                          verbatim="R$ 250.000,00"))
        retried = sdk.create_point("statement", self.CONTENT, dedup=True,
                                   **{field: None})
        assert retried["id"] == created["id"]
        row = _point_row(sdk, self.CONTENT)
        assert {AMOUNT_MINOR: row[AMOUNT_MINOR], CURRENCY: row[CURRENCY],
                MINOR_UNIT_EXPONENT: row[MINOR_UNIT_EXPONENT],
                VERBATIM: row[VERBATIM]} == {
            AMOUNT_MINOR: COMMITTED_MINOR, CURRENCY: "BRL",
            MINOR_UNIT_EXPONENT: 2, VERBATIM: "R$ 250.000,00"}
        assert _whole_sum(sdk) == {"BRL": COMMITTED_MINOR}

    def test_a_wellformed_retry_still_replaces_the_value_and_sums(
            self, journaled_sdk):
        """The positive control: the fold still WRITES money — a fix that made
        the retry silently drop the bag would pass the two tests above."""
        sdk, _events = journaled_sdk
        sdk.create_point("statement", self.CONTENT, dedup=True,
                         **money_props(COMMITTED_MINOR, "BRL",
                                       minor_unit_exponent=2))
        sdk.create_point("statement", self.CONTENT, dedup=True,
                         **money_props(RELEASED_MINOR, "BRL",
                                       minor_unit_exponent=2))
        assert _point_row(sdk, self.CONTENT)[AMOUNT_MINOR] == RELEASED_MINOR
        assert _whole_sum(sdk) == {"BRL": RELEASED_MINOR}
