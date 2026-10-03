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

The store cannot hold the value, so information is lost either way; the fix is to
fail closed at the SDK props boundary (``sdk._sanitize_props``) instead of
letting the alteration happen silently.

BOTH DIRECTIONS ARE PINNED. The refusal half is worthless on its own: a guard
that also refuses in-range values would break every existing write, so the
allowed half is asserted with the exact INT64 endpoints.
"""
from __future__ import annotations

import decimal

import pytest

from tortoise.sdk import _sanitize_props


class TestRefusesWhatTheStoreWouldAlter:
    """The values FalkorDB silently rewrites must never reach the query."""

    @pytest.mark.parametrize(
        "value",
        [2**63, 2**70, -(2**63) - 1, -(2**70), 10**40],
    )
    def test_out_of_int64_range_is_refused(self, value):
        with pytest.raises(ValueError) as exc:
            _sanitize_props({"v": value})
        # The message must NAME the range and the reason, or a caller cannot
        # act on it — and must name the offending key so a multi-prop write
        # says which one failed.
        msg = str(exc.value)
        assert "'v'" in msg, msg
        assert str(2**63 - 1) in msg and str(-(2**63)) in msg, msg
        assert "clamped" in msg, msg
        # It must offer the route that preserves the value.
        assert "string" in msg, msg

    def test_high_precision_decimal_is_refused(self):
        with pytest.raises(ValueError) as exc:
            _sanitize_props({"amount": decimal.Decimal("0.12345678901234567890")})
        msg = str(exc.value)
        assert "'amount'" in msg, msg
        assert "rounded" in msg, msg
        assert "string" in msg, msg

    def test_the_refusal_happens_before_anything_is_written(self):
        """A mixed props dict must be refused as a whole, not partially applied."""
        with pytest.raises(ValueError):
            _sanitize_props({"good": 1, "bad": 2**70})


class TestStillAllowsWhatStillFits:
    """The other half: an in-range write must pass through UNCHANGED.

    Without this half the guard could 'pass' by refusing everything.
    """

    @pytest.mark.parametrize(
        "value",
        [
            0,
            1,
            -1,
            2**63 - 1,      # INT64 max — the exact upper edge
            -(2**63),       # INT64 min — the exact lower edge
        ],
    )
    def test_int64_endpoints_and_ordinary_ints_pass_through(self, value):
        assert _sanitize_props({"v": value})["v"] == value

    @pytest.mark.parametrize("value", [True, False])
    def test_bools_are_not_treated_as_ints(self, value):
        """``bool`` is a subclass of ``int``; ``True`` must not become the number 1."""
        out = _sanitize_props({"flag": value})["flag"]
        assert out is value

    @pytest.mark.parametrize(
        "value",
        [
            decimal.Decimal("0.1"),
            decimal.Decimal("1.5"),
            decimal.Decimal("1.50"),        # trailing zeros are not extra precision
            decimal.Decimal("-2.25"),
            decimal.Decimal("12345.6789"),
        ],
    )
    def test_ordinary_decimals_pass_through(self, value):
        assert _sanitize_props({"amount": value})["amount"] == value

    def test_other_types_are_left_to_the_existing_boundaries(self):
        """This guard is about the numeric domain only."""
        out = _sanitize_props({"s": "2" * 70, "n": None, "f": 1.5})
        assert out["s"] == "2" * 70
        assert out["n"] is None
        assert out["f"] == 1.5
