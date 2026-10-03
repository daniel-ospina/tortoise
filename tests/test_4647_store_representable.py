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

    def test_bools_pass_through(self):
        """0/1 are in range either way — this pins behaviour, not a branch."""
        assert _sanitize_props({"flag": True})["flag"] is True
        assert _sanitize_props({"flag": False})["flag"] is False

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


class TestIntegralDecimalTakesTheInt64Path:
    """A ``Decimal`` with no fraction/exponent is an INT64 literal, not a double.

    Cycle-2 review P1: the driver sends ``str(value)`` and Cypher parses a bare
    integer literal as INT64 — a DIFFERENT domain. Checking only the double
    round-trip was wrong in BOTH directions: ``Decimal(2**70)`` is exactly a
    double yet the store clamps it; ``Decimal('9223372036854775807')`` is not
    exactly a double yet the store holds it exactly. Verified live against
    FalkorDB that ``create_point(..., v=Decimal(2**63))`` stored
    9223372036854775807.
    """

    @pytest.mark.parametrize(
        "value", [D(2**63), D(2**70), D(10**19), D("10000000000000000000")]
    )
    def test_integral_decimal_out_of_int64_is_refused(self, value):
        with pytest.raises(ValueError) as exc:
            _sanitize_props({"v": value})
        assert "'v'" in str(exc.value), str(exc.value)

    @pytest.mark.parametrize(
        "value",
        [
            D("9223372036854775807"),   # INT64 max as a Decimal — held exactly
            D("9007199254740993"),      # > 2**53, still held exactly as an int
            D("1000000000000001"),
            D(0),
            D(-1),
        ],
    )
    def test_integral_decimal_inside_int64_passes(self, value):
        assert _sanitize_props({"v": value})["v"] == value

    def test_a_fractional_decimal_still_takes_the_double_path(self):
        with pytest.raises(ValueError):
            _sanitize_props({"v": D("0.1")})
        assert _sanitize_props({"v": D("1.5")})["v"] == D("1.5")

    def test_signaling_nan_still_names_its_key(self):
        """``float(Decimal('sNaN'))`` raises — the reason must survive (P3)."""
        with pytest.raises(ValueError) as exc:
            _sanitize_props({"v": D("sNaN")})
        assert "'v'" in str(exc.value), str(exc.value)

    def test_a_self_referential_container_is_bounded(self):
        """Bounded and fail-closed: a ValueError, never a RecursionError."""
        a: list = []
        a.append(a)
        with pytest.raises(ValueError):
            _sanitize_props({"k": a})


class TestCreateSourceBypassIsClosed:
    """``create_source`` uses ``_skip_sanitize=True``, so it must guard itself.

    Cycle-2 review P2: deleting the guard call left the ENTIRE suite green — the
    repair shipped untested. This test fails if the guard is removed, by
    asserting the refusal happens BEFORE ``_create_entity`` is reached.
    """

    @pytest.mark.parametrize("value", [2**70, D(2**63)])
    def test_an_unrepresentable_prop_is_refused_before_any_write(self, value):
        from unittest import mock

        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK.__new__(TortoiseSDK)
        with mock.patch.object(
            TortoiseSDK, "_create_entity", autospec=True
        ) as create:
            with pytest.raises(ValueError) as exc:
                TortoiseSDK.create_source(
                    sdk, "https://example.test/p", "web", v=value
                )
            assert "'v'" in str(exc.value), str(exc.value)
            assert not create.called, "the guard must fire BEFORE the write"


class TestNumpyArrays:

    def test_an_out_of_range_array_element_is_refused(self):
        np = pytest.importorskip("numpy")
        with pytest.raises(ValueError):
            _sanitize_props({"k": np.array([2**70])})
        with pytest.raises(ValueError):
            _sanitize_props({"k": np.array([[1, 2], [2**70, 3]])})

    def test_an_in_range_array_passes(self):
        np = pytest.importorskip("numpy")
        out = _sanitize_props({"k": np.array([1, 2, 3])})
        assert list(out["k"]) == [1, 2, 3]


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


class TestDepthCapFailsClosed:
    """Cycle-3 P2: the cap must REFUSE, not `return None`.

    Returning None at the cap re-admitted the very defect: a value nested past
    the cap was allowed through and the store then clamped its leaf. Proven
    before the fix — 13 nested lists around 2**70 -> no error, stored
    9223372036854775807.
    """

    def test_a_value_nested_past_the_cap_is_refused_not_admitted(self):
        deep: object = [2**70]
        for _ in range(20):
            deep = [deep]
        with pytest.raises(ValueError) as exc:
            _sanitize_props({"k": deep})
        assert "'k'" in str(exc.value), str(exc.value)
        assert "nested deeper" in str(exc.value), str(exc.value)

    def test_a_shallow_container_still_passes(self):
        assert _sanitize_props({"k": [[1, 2], [3]]})["k"] == [[1, 2], [3]]


class TestFractionTakesTheInt64Path:
    """Cycle-3 P2: ``Fraction(2**70)`` was admitted and the store clamped it."""

    def test_an_out_of_range_fraction_is_refused(self):
        import fractions

        with pytest.raises(ValueError):
            _sanitize_props({"v": fractions.Fraction(2**70)})

    def test_an_in_range_fraction_passes(self):
        import fractions

        assert _sanitize_props({"v": fractions.Fraction(3, 1)})["v"] == 3

    def test_an_inexact_fraction_is_refused(self):
        import fractions

        with pytest.raises(ValueError):
            _sanitize_props({"v": fractions.Fraction(1, 3)})
        assert _sanitize_props({"v": fractions.Fraction(1, 2)})["v"] == fractions.Fraction(1, 2)

    def test_a_fraction_too_large_for_a_float_still_names_its_key(self):
        """Cycle-4 P2: the conversion itself raised ``OverflowError`` before the
        predicate could answer, so the caller never received this guard's message.
        (The integral twin, ``Fraction(10**400)``, already refused correctly."""
        import fractions

        with pytest.raises(ValueError) as exc:
            _sanitize_props({"v": fractions.Fraction(10**400, 3)})
        msg = str(exc.value)
        assert "'v'" in msg, msg
        assert "OverflowError" not in msg, msg
