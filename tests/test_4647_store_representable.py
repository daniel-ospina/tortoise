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

    def test_an_unrepresentable_keyword_argument_is_refused_too(self):
        """Cycle-4 P1: the guard covered ``props`` ONLY, so ``sourceDate`` — a
        first-class keyword argument assigned into the event dict AFTER the
        guard loop — still reached the store unguarded and was silently clamped.
        Reachable on the public surface as
        ``create_source(url, "web", sourceDate=2**70)`` and through ``ingest``,
        which splats ``**item`` into this method.
        """
        from unittest import mock

        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK.__new__(TortoiseSDK)
        with mock.patch.object(
            TortoiseSDK, "_create_entity", autospec=True
        ) as create:
            with pytest.raises(ValueError) as exc:
                TortoiseSDK.create_source(
                    sdk, "https://example.test/p", "web", sourceDate=2**70
                )
            assert "'sourceDate'" in str(exc.value), str(exc.value)
            assert not create.called, "the guard must fire BEFORE the write"

    @pytest.mark.parametrize("alpha,beta", [(2**70, 1.0), (1.0, 2**70), (D("0.1"), 1.0)])
    def test_set_point_baseline_is_guarded_too(self, alpha, beta):
        """Cycle-4 P1 — the THIRD bypass found. ``set_point_baseline`` writes
        ``alpha``/``beta`` straight into ``n.ep_alpha``/``n.ep_beta`` through a
        direct ``proj.g.query``, so it reaches the store without passing
        ``_sanitize_props`` at all: ``alpha=2**70`` was silently clamped to INT64
        max and ``alpha=Decimal('0.1')`` stored as a different number, corrupting
        the Beta prior that feeds EP confidence.
        """
        from unittest import mock

        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK.__new__(TortoiseSDK)
        sdk._evidence = {}
        with mock.patch.object(TortoiseSDK, "_get_proj", autospec=True) as proj:
            with pytest.raises(ValueError) as exc:
                TortoiseSDK.set_point_baseline(sdk, "p1", alpha, beta)
            msg = str(exc.value)
            assert "alpha" in msg or "beta" in msg, msg
            assert not proj.called, "the guard must fire BEFORE the write"
        assert sdk._evidence == {}, (
            "a refused write must not leave the in-memory prior divergent from the graph"
        )

    @pytest.mark.parametrize("alpha,beta", [(2.0, 3.0), (1.0, 1.0), (2**63 - 1, 1.0), (0.5, 0.25)])
    def test_set_point_baseline_does_not_refuse_an_ordinary_prior(self, alpha, beta):
        """The other half: the guard added to ``set_point_baseline`` must not
        reject a legitimate Beta prior. Asserted against the guard directly
        rather than through the whole method, which runs EP/dreaming work that a
        ``__new__``-built instance cannot support.
        """
        from tortoise.sdk import _reject_unrepresentable_number

        _reject_unrepresentable_number("alpha", alpha)
        _reject_unrepresentable_number("beta", beta)


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
    """The cap must REFUSE, not `return None`, and must sit AT the store limit.

    Cycle-3 P2: returning None at the cap re-admitted the very defect — a value
    nested past the cap was allowed through and the store then clamped its leaf.
    Cycle-5 P2: a cap of 12 was BELOW the store's own persistable depth (32,
    ``projection/entities.py::_PERSISTABLE_MAX_DEPTH``), so it refused structures
    the store holds exactly. The cap is now 32 and still fails closed.
    """

    def test_a_value_nested_past_the_cap_is_refused_not_admitted(self):
        # A benign leaf, so the refusal can ONLY come from the depth cap.
        deep: object = 1
        for _ in range(40):
            deep = [deep]
        with pytest.raises(ValueError) as exc:
            _sanitize_props({"k": deep})
        assert "'k'" in str(exc.value), str(exc.value)
        assert "nested deeper" in str(exc.value), str(exc.value)

    def test_a_leaf_at_depth_20_is_still_inspected(self):
        # Below the cap the guard must REACH the leaf and refuse it on the
        # INT64 branch — the cycle-3 defect was a leaf waved through by depth.
        deep: object = [2**70]
        for _ in range(20):
            deep = [deep]
        with pytest.raises(ValueError) as exc:
            _sanitize_props({"k": deep})
        assert "clamped" in str(exc.value), str(exc.value)

    def test_a_shallow_container_still_passes(self):
        assert _sanitize_props({"k": [[1, 2], [3]]})["k"] == [[1, 2], [3]]

    def test_a_legitimate_32_deep_container_is_not_refused(self):
        # The store holds depth-32 structures exactly (verified live against
        # FalkorDB); the old cap of 12 refused them (cycle-5 P2).
        deep: object = 1
        for _ in range(30):
            deep = [deep]
        assert _sanitize_props({"k": deep})["k"] == deep


class TestFractionTakesTheInt64Path:
    """Cycle-3 P2: ``Fraction(2**70)`` was admitted and the store clamped it."""

    def test_an_out_of_range_fraction_is_refused(self):
        import fractions

        with pytest.raises(ValueError):
            _sanitize_props({"v": fractions.Fraction(2**70)})

    def test_an_in_range_fraction_passes(self):
        import fractions

        assert _sanitize_props({"v": fractions.Fraction(3, 1)})["v"] == 3

    def test_a_non_integral_fraction_is_refused(self):
        """Cycle-6 P2: the cycle-5 branch admitted ``Fraction(1, 2)`` because it
        compared ``Fraction(float(value))`` — but the driver inlines
        ``str(value)``, which is ``'1/2'``, and the store rejects that with an
        opaque ``Invalid input '/'``. No non-integral ratio is storable."""
        import fractions

        with pytest.raises(ValueError) as exc:
            _sanitize_props({"v": fractions.Fraction(1, 3)})
        assert "'v'" in str(exc.value), str(exc.value)
        with pytest.raises(ValueError):
            _sanitize_props({"v": fractions.Fraction(1, 2)})

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

    def test_a_fraction_too_large_to_render_still_names_its_key(self):
        """Cycle-4 P2 (sibling of the ``OverflowError`` above): interpolating
        ``{value}`` rendered the numerator, so a numerator past CPython's
        4300-digit int->str limit made the MESSAGE itself raise, losing the key
        and the remedy.
        """
        import fractions

        with pytest.raises(ValueError) as exc:
            _sanitize_props({"v": fractions.Fraction(10**5000, 3)})
        msg = str(exc.value)
        assert "'v'" in msg, msg
        assert "4300 digits" not in msg, msg


class TestNumpyFloatingScalarsAreGuarded:
    """Cycle-5 P1: the guard covered numpy INTEGERS but not numpy FLOATS.

    The driver sends ``str(value)`` and Cypher parses that as a double, so
    ``np.float32(0.1)`` — whose shortest repr is ``'0.1'`` — reaches the store
    as the double ``0.1``, a DIFFERENT number from the float32 value (verified
    live: ``SET n.v=$v`` stored ``0.1``). ``np.longdouble`` is NOT cited as an
    alteration: on a 64-bit-longdouble platform it IS the double, so the store
    holds it exactly and the guard admits it — the predicate's longdouble
    compare only bites where ``str()`` differs from the double.
    """

    def test_numpy_float32_is_refused_because_the_store_would_alter_it(self):
        np = pytest.importorskip("numpy")
        with pytest.raises(ValueError) as exc:
            _sanitize_props({"v": np.float32(0.1)})
        assert "'v'" in str(exc.value), str(exc.value)

    def test_numpy_float16_is_refused_too(self):
        np = pytest.importorskip("numpy")
        # float16(0.1) renders as '0.1' but is 0.0999755859375 — altered.
        with pytest.raises(ValueError):
            _sanitize_props({"v": np.float16(0.1)})

    def test_numpy_float64_and_plain_float_pass(self):
        np = pytest.importorskip("numpy")
        assert _sanitize_props({"v": np.float64(0.1)})["v"] == np.float64(0.1)
        assert _sanitize_props({"v": 0.1})["v"] == 0.1
        assert _sanitize_props({"v": 1e308})["v"] == 1e308

    def test_a_float32_ARRAY_is_refused_too(self):
        """The scalar fix must survive the array path: ``.tolist()`` widened the
        float32 element to a Python float and hid the alteration."""
        np = pytest.importorskip("numpy")
        with pytest.raises(ValueError):
            _sanitize_props({"v": np.array([np.float32(0.1)])})


class TestNonFiniteFloatsAreRefused:
    """Cycle-5 P2: a non-finite plain float reached the store and failed there
    with an opaque ``Failed to parse query parameter``, while the same value as
    a Decimal was refused up front with a key-naming message. Symmetry."""

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_float_is_refused_with_the_key(self, value):
        import math

        with pytest.raises(ValueError) as exc:
            _sanitize_props({"v": value})
        assert "'v'" in str(exc.value), str(exc.value)
        assert math.isfinite(value) is False

    def test_a_non_finite_float_inside_an_embedding_is_refused(self):
        with pytest.raises(ValueError) as exc:
            _sanitize_props({"embedding": [0.1, float("inf")]})
        assert "embedding" in str(exc.value), str(exc.value)


class TestFlatFloatSequenceFastPath:
    """Cycle-5 P2 (perf): a 1536-dim embedding is the hot write path; the guard
    must not pay a Python-level recursive call per element. The fast path must
    still refuse a non-finite element — it is not a bypass."""

    def test_a_flat_float_embedding_passes(self):
        vec = [0.5] * 1536
        assert _sanitize_props({"embedding": vec})["embedding"] == vec

    def test_a_non_finite_element_still_refuses(self):
        with pytest.raises(ValueError):
            _sanitize_props({"embedding": [0.5] * 100 + [float("nan")]})

    def test_a_mixed_sequence_is_still_fully_inspected(self):
        with pytest.raises(ValueError):
            _sanitize_props({"arr": [0.5, 2**70]})


class TestDirectWriterSitesAreGuarded:
    """Cycle-5 P1/P2: three more writers hand raw Cypher parameters to the store
    and bypass ``_sanitize_props``. Each must refuse BEFORE any query."""

    def _bare_sdk(self):
        from tortoise.sdk import TortoiseSDK

        return TortoiseSDK.__new__(TortoiseSDK)

    def test_record_calibration_guards_its_numerics(self):
        from unittest import mock

        from tortoise.sdk import TortoiseSDK

        sdk = self._bare_sdk()
        with mock.patch.object(TortoiseSDK, "_get_proj", autospec=True) as proj:
            with pytest.raises(ValueError) as exc:
                TortoiseSDK.record_calibration(
                    sdk, precision=0.75, sample_size=2**70,
                    mean_grounding_delta=0.01)
            assert "sample_size" in str(exc.value), str(exc.value)
            assert not proj.return_value.g.query.called

    def test_complete_source_guards_external_id(self):
        from unittest import mock

        from tortoise.sdk import TortoiseSDK

        sdk = self._bare_sdk()
        with mock.patch.object(
            TortoiseSDK, "_resolve_source_url", autospec=True, return_value="u"
        ), mock.patch.object(TortoiseSDK, "_get_proj", autospec=True) as proj:
            with pytest.raises(ValueError) as exc:
                TortoiseSDK.complete_source(sdk, "u", external_id=2**70)
            assert "externalId" in str(exc.value), str(exc.value)
            assert not proj.return_value.g.query.called

    def test_session_event_write_guards_frontmatter_message_count(self):
        """A YAML frontmatter int is arbitrary-precision; the graph would clamp
        it while the journal recorded the original (live != replay)."""
        from unittest import mock

        from tortoise.sdk import TortoiseSDK

        sdk = self._bare_sdk()
        with mock.patch.object(TortoiseSDK, "_get_proj", autospec=True) as proj:
            with pytest.raises(ValueError) as exc:
                TortoiseSDK._session_event_write(
                    sdk, {"message_count": 2**70, "agent": "pi"}, "hi", "p.md",
                    "e1", "s1", "h", "t", False, None, "s.md", "p.md")
            assert "message_count" in str(exc.value), str(exc.value)
            assert not proj.return_value.g.query.called


class TestNumpyTemporalScalarsAreRefusedNotRaised:
    """Cycle-6 P2 / cycle-7 P2: ``np.timedelta64`` registers as
    ``numbers.Integral`` but neither it nor ``np.datetime64`` has a Cypher
    number literal — ``str()`` is ``'1 years'`` / ``'1970-01-02'``. Catching
    only the units whose ``int()`` raises left the calendar/sub-microsecond
    units admitted (silently dropped or opaque store error)."""

    @pytest.mark.parametrize("unit", ["D", "h", "s", "ms", "us", "ns", "ps", "Y", "M"])
    def test_a_timedelta64_unit_names_its_key(self, unit):
        np = pytest.importorskip("numpy")
        with pytest.raises(ValueError) as exc:
            _sanitize_props({"duration": np.timedelta64(5, unit)})
        assert "duration" in str(exc.value), str(exc.value)
        assert "TypeError" not in str(exc.value), str(exc.value)

    def test_a_datetime64_is_refused(self):
        np = pytest.importorskip("numpy")
        with pytest.raises(ValueError) as exc:
            _sanitize_props({"when": np.datetime64("1970-01-02")})
        assert "when" in str(exc.value), str(exc.value)

    def test_a_timedelta64_array_is_refused(self):
        np = pytest.importorskip("numpy")
        with pytest.raises(ValueError):
            _sanitize_props({"v": np.array([np.timedelta64(5, "s")])})


class TestAnnotateOperatorGuardsBeforeTheEmit:
    """Cycle-7 P2: ``annotate_operator`` emitted ``OperatorAnnotated`` BEFORE
    ``update_point``'s guard ran, so an inexact Decimal/Fraction/np.float32 that
    passed the [0,1] range check logged a FALSE \"LIVE-ONLY\" journal error for a
    write that was then refused. The guard must run before the emit."""

    def test_an_inexact_decimal_is_refused(self):
        from unittest import mock

        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK.__new__(TortoiseSDK)
        with mock.patch.object(
            TortoiseSDK, "get_point", autospec=True,
            return_value={"is_operator": True},
        ), mock.patch.object(TortoiseSDK, "_emit_event", autospec=True) as emit:
            with pytest.raises(ValueError) as exc:
                TortoiseSDK.annotate_operator(
                    sdk, "op1", bias=D("0.12345678901234567890"),
                    precision=0.5, consistency=0.5, directness=0.5)
            assert "bias" in str(exc.value), str(exc.value)
            assert not emit.called, "the guard must fire BEFORE the journal emit"


class TestNonFiniteDecimalNamesTheKeyBeforeTheRangeCheck:
    """Cycle-8 P2: ``0 <= Decimal('NaN') <= 1`` raises
    ``decimal.InvalidOperation`` (an ArithmeticError), so a range check placed
    BEFORE the guard escapes as a wrong-class, key-less error. The guard must
    run first at each such site."""

    def _bare(self):
        from tortoise.sdk import TortoiseSDK

        return TortoiseSDK.__new__(TortoiseSDK)

    def test_annotate_operator(self):
        from unittest import mock

        from tortoise.sdk import TortoiseSDK

        sdk = self._bare()
        with mock.patch.object(
            TortoiseSDK, "get_point", autospec=True,
            return_value={"is_operator": True},
        ):
            with pytest.raises(ValueError) as exc:
                TortoiseSDK.annotate_operator(
                    sdk, "op1", bias=D("NaN"),
                    precision=0.5, consistency=0.5, directness=0.5)
            assert "bias" in str(exc.value), str(exc.value)

    def test_mitigate_operator(self):
        from tortoise.sdk import TortoiseSDK

        sdk = self._bare()
        with pytest.raises(ValueError) as exc:
            TortoiseSDK.mitigate_operator(sdk, "op1", "why", strength=D("NaN"))
        assert "strength" in str(exc.value), str(exc.value)


class TestDirectWriterSitesAreGuardedRoundTwo:
    """Cycle-6 P1/P2: three more raw-Cypher writers bypass ``_sanitize_props``.
    Each must refuse BEFORE any query runs."""

    def _bare(self):
        from tortoise.sdk import TortoiseSDK

        return TortoiseSDK.__new__(TortoiseSDK)

    def test_mitigate_operator_guards_strength(self):
        from unittest import mock

        from tortoise.sdk import TortoiseSDK

        sdk = self._bare()
        with mock.patch.object(
            TortoiseSDK, "_get_proj", autospec=True
        ) as proj, mock.patch.object(
            TortoiseSDK, "get_point", autospec=True,
            return_value={"is_operator": True, "baseline_set": True},
        ):
            proj.return_value.g.query.return_value.result_set = []
            with pytest.raises(ValueError) as exc:
                TortoiseSDK.mitigate_operator(
                    sdk, "op1", "reason", strength=D("0.12345678901234567890"))
            assert "strength" in str(exc.value), str(exc.value)
            # cycle-8: the guard now precedes the idempotency read too,
            # so NO query may run for a refused strength.
            assert proj.return_value.g.query.call_count == 0

    def test_connect_issue_objects_guards_oid_and_issue_number(self):
        from unittest import mock

        from tortoise.sdk import TortoiseSDK

        sdk = self._bare()
        with mock.patch.object(TortoiseSDK, "_get_proj", autospec=True) as proj:
            with pytest.raises(ValueError) as exc:
                TortoiseSDK._connect_issue_objects(
                    sdk, "e1", {"issues": [{"number": 2**70, "title": "t"}]})
            assert "'oid'" in str(exc.value) or "issue_number" in str(exc.value), \
                str(exc.value)
            assert not proj.return_value.g.query.called

    def test_create_direct_edge_guards_weight_before_coercion(self):
        from unittest import mock

        from tortoise.sdk import TortoiseSDK

        sdk = self._bare()
        with mock.patch.object(TortoiseSDK, "_get_proj", autospec=True) as proj:
            with pytest.raises(ValueError) as exc:
                TortoiseSDK.create_direct_edge(
                    sdk, "IMPL", "a", "b", weight=D("0.12345678901234567890"))
            assert "weight" in str(exc.value), str(exc.value)
            assert not proj.return_value.g.query.called

    def test_reliability_cache_guard(self):
        from unittest import mock

        from tortoise.sdk import TortoiseSDK

        sdk = self._bare()
        with mock.patch.object(
            TortoiseSDK, "_resolve_source_url", autospec=True, return_value="u"
        ), mock.patch.object(TortoiseSDK, "_get_proj", autospec=True) as proj:
            with pytest.raises(ValueError) as exc:
                TortoiseSDK._write_reliability_cache(
                    sdk, "u", 2**70, {}, __import__("datetime").datetime.now())
            assert "reliability" in str(exc.value), str(exc.value)
            assert not proj.return_value.g.query.called

    def test_ingest_corpus_document_created_branch_guards_frontmatter(self, tmp_path):
        """The DEFAULT ingest branch (DocumentCreated) — also the MCP tool's
        path — takes arbitrary-precision frontmatter ints straight to $props."""
        import textwrap
        from unittest import mock

        from tortoise.sdk import TortoiseSDK

        f = tmp_path / "doc.md"
        f.write_text(textwrap.dedent(
            "---\n"
            "title: t\n"
            "type: 12345678901234567890123\n"
            "---\nbody\n"
        ))
        sdk = self._bare()
        with mock.patch.object(TortoiseSDK, "_get_proj", autospec=True) as proj:
            proj.return_value.g.query.return_value.result_set = []
            with pytest.raises(ValueError) as exc:
                TortoiseSDK.ingest_corpus(sdk, str(tmp_path), extract_metadata=False)
            assert "document_kind" in str(exc.value), str(exc.value)
