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
(``tortoise/value_normalizer.py``, held branch ``feat/2817-value-normalizer``).
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
