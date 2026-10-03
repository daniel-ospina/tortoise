"""#4647 — the write surface must refuse a value the store cannot hold.

FalkorDB stores an integer as INT64 and a number as a double. A Python ``int`` is
unbounded and ``decimal.Decimal`` is arbitrary-precision, so an out-of-domain
value is SILENTLY ALTERED by the store: ``SET n.v = $v`` reports success and
stores a different number. Reproduced live against FalkorDB:

    wrote 2**70                        -> stored 9223372036854775807
    wrote -(2**70)                     -> stored -9223372036854775808
    wrote 2**63                        -> stored 9223372036854775807  (max + 1)
    wrote Decimal('0.12345678901234567890')
                                       -> stored 0.123456789012346

The predicate is EXACTNESS, not a digit count. A digit-count proxy was tried
first and was wrong in both directions (code-review P1 on #7007): it refused
``Decimal('1000000000000001')`` and ``Decimal(0.1)``, which a double holds
exactly, while admitting ``Decimal('0.1')``, which it silently rounds. The rule
is therefore "does the value round-trip through the store's own number type",
plus finiteness — which also catches the exponent axis (``Decimal('1E+400')`` ->
``inf``) and the non-finite Decimals.

BOTH DIRECTIONS ARE PINNED. The refusal half is worthless on its own: a guard
that also refused in-range values would break every existing write, so the
allowed half is asserted with the exact INT64 endpoints and the exact Decimals
that a double genuinely holds.
"""
from __future__ import annotations

import decimal

import pytest

from tortoise.sdk import _sanitize_props

D = decimal.Decimal


class TestRefusesWhatTheStoreWouldAlter:
    """Values FalkorDB silently rewrites must never reach the query."""

    @pytest.mark.parametrize("value", [2**63, 2**70, -(2**63) - 1, -(2**70), 10**40])
    def test_out_of_int64_range_is_refused(self, value):
        with pytest.raises(ValueError) as exc:
            _sanitize_props({"v": value})
        msg = str(exc.value)
        assert "'v'" in msg, msg
        assert str(2**63 - 1) in msg and str(-(2**63)) in msg, msg
        assert "clamped" in msg, msg
        assert "string" in msg, msg

    @pytest.mark.parametrize(
        "value",
        [
            D("0.1"),                    # the canonical inexact double
            D("0.12345678901234567890"),
            D("12345.6789"),
            D("1E+400"),                 # exponent axis -> inf
            D("1E-400"),                 # -> 0.0
            D("NaN"),
            D("Infinity"),
            D("-Infinity"),
        ],
    )
    def test_decimals_the_store_would_alter_are_refused(self, value):
        with pytest.raises(ValueError) as exc:
            _sanitize_props({"amount": value})
        msg = str(exc.value)
        assert "'amount'" in msg, msg
        assert "string" in msg, msg

    def test_the_refusal_happens_before_anything_is_written(self):
        with pytest.raises(ValueError):
            _sanitize_props({"good": 1, "bad": 2**70})

    def test_a_container_element_is_checked_not_just_the_top_level(self):
        """Only top-level props were inspected in the first cut (P2)."""
        with pytest.raises(ValueError):
            _sanitize_props({"arr": [1, 2**70, 3]})
        with pytest.raises(ValueError):
            _sanitize_props({"d": {"nested": [2**70]}})

    def test_a_huge_int_still_names_its_key(self):
        """The message must not be lost to the int->str digit limit (P3)."""
        with pytest.raises(ValueError) as exc:
            _sanitize_props({"v": 2**100000})
        msg = str(exc.value)
        assert "'v'" in msg, msg
        assert "bits" in msg, msg
        assert "4300 digits" not in msg, msg


class TestStillAllowsWhatStillFits:
    """The other half: a value the store holds FAITHFULLY must pass unchanged.

    Without this half the guard could 'pass' by refusing everything — and the
    first cut did exactly that for values a double holds exactly.
    """

    @pytest.mark.parametrize(
        "value",
        [0, 1, -1, 2**63 - 1, -(2**63)],   # the exact INT64 edges
    )
    def test_int64_endpoints_and_ordinary_ints_pass_through(self, value):
        assert _sanitize_props({"v": value})["v"] == value

    @pytest.mark.parametrize("value", [True, False])
    def test_bools_are_not_treated_as_ints(self, value):
        assert _sanitize_props({"flag": value})["flag"] is value

    @pytest.mark.parametrize(
        "value",
        [
            D("1.5"),
            D("1.50"),                   # == D('1.5'); trailing zeros are not precision
            D("-2.25"),
            D("1000000000000001"),        # refused by the OLD digit-count rule
            D(0.1),                      # this IS the double 0.1 — exact
            D("0"),
            D("-0"),
        ],
    )
    def test_decimals_a_double_holds_exactly_pass_through(self, value):
        assert _sanitize_props({"amount": value})["amount"] == value

    def test_other_types_are_left_to_the_existing_boundaries(self):
        out = _sanitize_props({"s": "2" * 70, "n": None, "f": 1.5})
        assert out["s"] == "2" * 70
        assert out["n"] is None
        assert out["f"] == 1.5

    def test_a_container_of_fine_values_still_passes(self):
        out = _sanitize_props({"arr": [1, 2, 3], "d": {"k": [4, 5]}})
        assert out["arr"] == [1, 2, 3] and out["d"] == {"k": [4, 5]}


class TestNumpyScalars:
    """numpy integers are NOT Python ``int`` subclasses (code-review P2)."""

    def test_numpy_ints_out_of_range_are_refused(self):
        np = pytest.importorskip("numpy")
        with pytest.raises(ValueError):
            _sanitize_props({"v": np.uint64(2**64 - 1)})
        with pytest.raises(ValueError):
            _sanitize_props({"v": np.uint64(2**63)})

    def test_numpy_ints_in_range_pass(self):
        np = pytest.importorskip("numpy")
        assert int(_sanitize_props({"v": np.int64(7)})["v"]) == 7
