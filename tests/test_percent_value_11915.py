"""The `percent` value shape (#11915) — one bounded int, one evidence field.

Before this shape existed a shipped pack declared a value field NOTHING could
store: `packs/investment-portfolio-manager/manifest.yaml` declares
`targetPct` with `valueKind: percent, required: true` (#9663), the declaration
validates, it renders into the S2/S4 extraction prompt as
`targetPct(percent, required)` — and `grep -rn basisPoints tortoise/` found two
hits, both comments. A prompt that instructs a model to emit a value with no
storing shape is a prompt that lies.

What is asserted here, and why each half exists:

* the SHAPE (`percent_props` / `read_percent_value`): an int in basis points,
  bounded 0–10000, and `verbatim` only as the value's own evidence;
* the GATE at the SDK's one props intake — the #11836 pattern — including the
  payload walk that makes it non-inert on the journal-first door;
* the REFUSAL WORDING (`docs/ax/ax-principles.md` rule 2): it refused, it names
  the field and the bound, and it gives the corrected value where computable;
* the DECLARED-FIELD half: the shipped `targetPct` declaration still renders,
  and the shape it names is now storable (so the declaration is no longer an
  instruction that cannot land).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise import percent as percent_module
from tortoise.money import money_props
from tortoise.percent import (
    BASIS_POINTS,
    PERCENT_MAX_BP,
    PERCENT_MIN_BP,
    PERCENT_VALUE_FIELDS,
    PercentValueError,
    percent_correction,
    percent_props,
    read_percent_value,
    refuse_malformed_percent,
    refuse_malformed_percent_in_payload,
    refuse_shared_evidence,
)
from tortoise.projection.entities import _EntityHandlers
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def percent_sdk(tmp_path):
    """An embedded SDK (plus its journal) — the same shape the money value's
    lane uses, so the two shapes are exercised on one kind of store."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(
        str(tmp_path / "percent_11915.db"),
        event_log_path=str(events / "events.jsonl"),
    )
    sdk._percent_tmp = tmp_path
    yield sdk
    sdk.close()


# ── the shape ──────────────────────────────────────────────────────────────

class TestTheShape:
    def test_the_bound_is_the_invariant(self):
        """12% is 1200 because 100% is 10000 — the bound is what a generic
        `(value, unit)` pair cannot express, which is why the shape is
        per-unit (#11915's recorded decision)."""
        assert (PERCENT_MIN_BP, PERCENT_MAX_BP) == (0, 10_000)
        assert percent_props(1_200) == {BASIS_POINTS: 1_200}
        assert percent_props(0) == {BASIS_POINTS: 0}
        assert percent_props(10_000) == {BASIS_POINTS: 10_000}

    def test_the_fields_are_the_declared_two(self):
        assert PERCENT_VALUE_FIELDS == (BASIS_POINTS, "verbatim")
        assert tuple(percent_props(1_200)) == (BASIS_POINTS,)
        assert tuple(percent_props(1_200, verbatim="12%")) == PERCENT_VALUE_FIELDS

    @pytest.mark.parametrize("value", [-1, 10_001, 12_000, 10**18])
    def test_out_of_range_is_refused_never_clamped(self, value):
        """A percentage silently clamped to 100 % is a wrong number that reads
        as a real one — the refusal is the whole point of the bound."""
        with pytest.raises(PercentValueError, match="0–10000"):
            percent_props(value)

    @pytest.mark.parametrize("value", [12.0, 12.5, "1200", None, True, False])
    def test_only_an_int_is_a_percentage(self, value):
        # `bool` is an `int` subclass in Python: `True` is not a percent.
        with pytest.raises(PercentValueError, match="must be an int"):
            percent_props(value)

    @pytest.mark.parametrize("value,hint", [
        (12.5, "12.5% is 1250 basis points"),
        ("1200", "written as text is not a value"),
    ])
    def test_a_wrong_type_states_the_corrected_value(self, value, hint):
        """AX rule 2: the refusal says what to write instead, where computable
        — and states it as a CONVERSION, never applying it (a rounded
        percentage is not a basis-point count)."""
        with pytest.raises(PercentValueError) as caught:
            percent_props(value)
        assert hint in str(caught.value), str(caught.value)

    def test_verbatim_is_omitted_when_absent_never_none(self):
        assert "verbatim" not in percent_props(1_200)

    @pytest.mark.parametrize("verbatim", ["", 123, 0, []])
    def test_a_supplied_verbatim_must_be_source_text(self, verbatim):
        with pytest.raises(PercentValueError, match="verbatim"):
            percent_props(1_200, verbatim=verbatim)

    def test_the_machine_supplied_rule_has_no_fabricated_evidence_path(self):
        """Owner decision D4b: a machine-supplied value carries its ORIGIN
        (`source_ref` + the ingest reference on the carrier), not a verbatim —
        so the shape must be constructible with NO evidence field, and it must
        never invent one. There is no default, no derivation and no fallback
        here: absence is the machine-supplied case."""
        import inspect

        params = inspect.signature(percent_props).parameters
        assert params["verbatim"].default is None, (
            "a default verbatim would fabricate evidence for a machine-supplied "
            "value (D4b)")
        assert "verbatim" not in percent_props(1_200, verbatim=None)

    def test_the_value_carries_no_temporal_field(self):
        """#11827's clause, applied to this shape: the instant is the CARRIER's
        valid-time start, so no date field exists here — `asOf` is retired and
        must not reappear under a percent-shaped name."""
        for spelling in ("asOf", "as_of", "date", "when", "validFrom"):
            assert spelling not in PERCENT_VALUE_FIELDS
            assert spelling not in percent_props(1_200)


# ── the reader ─────────────────────────────────────────────────────────────

class TestTheReader:
    def test_no_percent_is_none_not_zero(self):
        """`None` (not 0) — a node with no percentage is not a node at 0 %."""
        assert read_percent_value({}) is None
        assert read_percent_value({"content": "no value here"}) is None

    def test_a_present_value_is_read(self):
        assert read_percent_value({BASIS_POINTS: 1_200}) == 1_200
        assert read_percent_value({BASIS_POINTS: 0}) == 0

    def test_a_present_but_malformed_value_raises(self):
        with pytest.raises(PercentValueError):
            read_percent_value({BASIS_POINTS: 12.5})
        with pytest.raises(PercentValueError):
            read_percent_value({BASIS_POINTS: 20_000})


# ── the refusal: three parts, and the corrected value ───────────────────────

class TestTheRefusalWording:
    def test_it_says_what_it_refused_and_the_bound(self):
        with pytest.raises(PercentValueError) as caught:
            refuse_malformed_percent({BASIS_POINTS: 12_000}, door="props")
        message = str(caught.value)
        assert "refused" in message          # 1. it refused
        assert "props" in message            # …and where
        assert BASIS_POINTS in message       # 2. the field
        assert "0–10000" in message          # …and the bound

    def test_it_gives_the_corrected_value_where_computable(self):
        """AX rule 2's third part, with the issue's own worked example:
        `basisPoints must be 0–10000: 12% is 1200, and 12000 would be 120%`."""
        with pytest.raises(PercentValueError) as caught:
            refuse_malformed_percent({BASIS_POINTS: 12_000})
        message = str(caught.value)
        assert "12% is 1200" in message
        assert "12000 would be 120%" in message

    def test_no_correction_is_invented_for_a_value_that_names_no_number(self):
        """A refusal must not guess: a string has no computable correction, so
        the message carries none (and still names the field and the bound)."""
        assert percent_correction("twelve percent") == ""
        assert percent_correction(True) == ""
        with pytest.raises(PercentValueError) as caught:
            refuse_malformed_percent({BASIS_POINTS: "1200"})
        assert "would be" not in str(caught.value)

    def test_the_gate_is_a_no_op_for_a_bag_with_no_percent(self):
        assert refuse_malformed_percent({}) is None
        assert refuse_malformed_percent({"verbatim": "quoted"}) is None
        assert refuse_malformed_percent({"basisPointsOther": 99_999}) is None


# ── the payload walk (the journal-first door) ──────────────────────────────

class TestThePayloadWalk:
    def test_a_nested_malformed_value_is_refused(self):
        """`PointAdded` nests the record under `point=` — a top-level-only gate
        is measurably INERT on that door (#11836 review Finding 1, the same
        shape for money)."""
        with pytest.raises(PercentValueError, match="write boundary"):
            refuse_malformed_percent_in_payload(
                {"type": "PointAdded",
                 "point": {"content": "x", BASIS_POINTS: 20_000}},
                door="EventAPI.PointAdded")
        with pytest.raises(PercentValueError, match="write boundary"):
            refuse_malformed_percent_in_payload(
                {"type": "EventRecorded",
                 "uses": [{BASIS_POINTS: 12.5}]})

    def test_a_clean_payload_is_admitted(self):
        payload = {"type": "PointAdded",
                   "point": {"content": "x", BASIS_POINTS: 1_200}}
        assert refuse_malformed_percent_in_payload(payload) is None

    def test_a_self_referential_payload_cannot_loop_the_walk(self):
        node: dict = {"content": "x"}
        node["self"] = node
        loop: list = []
        loop.append(loop)
        assert refuse_malformed_percent_in_payload(
            {"a": node, "b": loop}) is None

    @pytest.mark.parametrize("offset", [0, 1], ids=["at-the-bound", "one-past"])
    def test_the_bound_itself_still_grades(self, offset):
        """The END of the range is pinned, not only ``bound + 1`` (#11992).

        ⛔ ``test_a_payload_deeper_than_the_bound_is_refused_not_skipped`` builds
        its payload ``PAYLOAD_MAX_DEPTH + 1`` deep, so ``>`` and ``>=`` BOTH
        raise and the mutant is invisible — measured: flipping the comparison to
        ``>=`` left the whole suite green (239 passed), i.e. the bound was
        unfalsifiable while money's equivalent guard was pinned. The
        discriminating half is the payload AT the bound: the walk must GRADE it,
        not refuse it, because refusing it would be a false block on a write the
        gate exists to let through. Money's sibling pin
        (``test_the_bound_itself_still_grades``) is parametrised the same way.

        ``depth == PAYLOAD_MAX_DEPTH`` is accepted, ``depth == MAX + 1`` is
        refused — both halves are asserted, so neither ``>`` nor ``>=`` can
        satisfy the pair.
        """
        from tortoise.money import PAYLOAD_MAX_DEPTH

        depth = PAYLOAD_MAX_DEPTH + offset
        # ⛔ A FLAT base, so the wrap count IS the depth the walk sees (it seeds
        # `(payload, 0)` and pushes children at `depth + 1`): the sibling test's
        # `{"leaf": {"content": ...}}` base contributes one level of its own,
        # which is why it can only be run one-past and never AT the bound.
        payload: dict = {"content": "no percent anywhere"}
        for _ in range(depth):
            payload = {"nest": payload}

        if offset == 0:
            assert refuse_malformed_percent_in_payload(payload) is None, (
                f"a percent-free payload exactly at the bound (depth {depth}, "
                f"bound {PAYLOAD_MAX_DEPTH}) was refused by the DEPTH rule — the "
                "bound is inclusive, so `depth > PAYLOAD_MAX_DEPTH` must not "
                "fire at the bound itself")
            return

        with pytest.raises(PercentValueError) as caught:
            refuse_malformed_percent_in_payload(payload)
        assert "nests deeper than" in str(caught.value), (
            f"a payload one level PAST the bound (depth {depth}) was not refused "
            f"by the depth rule: {caught.value!r}")

    def test_a_payload_deeper_than_the_bound_is_refused_not_skipped(self):
        """A skip would be a silent fail-open: the walk stops looking and the
        gate reports clean. It refuses instead, and the message reports the
        SHAPE rather than pretending it found a percent member."""
        from tortoise.money import PAYLOAD_MAX_DEPTH

        payload: dict = {"leaf": {"content": "no percent anywhere"}}
        for _ in range(PAYLOAD_MAX_DEPTH + 1):
            payload = {"nest": payload}
        with pytest.raises(PercentValueError) as caught:
            refuse_malformed_percent_in_payload(payload)
        message = str(caught.value)
        assert "nests deeper than" in message
        assert BASIS_POINTS not in message, (
            "the depth refusal fires on a percent-free payload, so it must not "
            "claim a percent member it never found")


# ── the door: one intake, like money ───────────────────────────────────────

class TestTheWriteDoor:
    def test_a_point_stores_a_valid_percentage(self, percent_sdk):
        created = percent_sdk.create_point("statement", "target is 12%",
                                   **percent_props(1_200, verbatim="12%"))
        node = percent_sdk.get_entity(created["id"])
        assert node[BASIS_POINTS] == 1_200
        assert node["verbatim"] == "12%"

    def test_the_declared_point_lane_carries_the_shape(self):
        """Declared for the replay lane, or every rebuild logs a false
        `"Point prop 'basisPoints' is not declared"` drift warning."""
        for field in PERCENT_VALUE_FIELDS:
            assert field in _EntityHandlers._POINT_DECLARED_PROPS, field

    @pytest.mark.parametrize("value", [20_000, -1, 12.5, True])
    def test_a_malformed_percentage_cannot_be_created(self, percent_sdk, value):
        with pytest.raises(ValueError, match="write boundary"):
            percent_sdk.create_point("statement", "target is 120%",
                             basisPoints=value)

    def test_a_malformed_percentage_cannot_be_updated_in(self, percent_sdk):
        created = percent_sdk.create_point("statement", "target is 12%",
                                   **percent_props(1_200))
        with pytest.raises(ValueError, match="write boundary"):
            percent_sdk.update_point(created["id"], basisPoints=20_000)
        assert percent_sdk.get_entity(created["id"])[BASIS_POINTS] == 1_200

    def test_deleting_the_value_is_not_a_refusal(self):
        """`basisPoints=None` deletes the property (`SET n += $props`), which
        leaves "no percent here" — the no-op this gate must not refuse. A
        percent is ONE required scalar, so an update needs no merged view."""
        assert refuse_malformed_percent({BASIS_POINTS: None}) is None

    def test_the_journal_first_door_is_gated_too(self, percent_sdk):
        """`EventAPI.add_point` bypasses `_sanitize_props`; the payload walk is
        what keeps the shape's bound true on that door — and the refusal must
        land BEFORE a record is appended."""
        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        api = EventAPI(
            EventLog(str(percent_sdk._percent_tmp / "api-events.jsonl")),
            initiated_by="extractor", projection=percent_sdk._get_proj())
        with pytest.raises(ValueError) as caught:
            api.add_point("target is 200%", {"run_id": "run-1"},
                          basisPoints=20_000)
        assert "write boundary" in str(caught.value)
        assert BASIS_POINTS in str(caught.value)
        assert percent_sdk._get_proj().g.query(
            "MATCH (n) WHERE n.basisPoints IS NOT NULL RETURN count(n)"
        ).result_set[0][0] == 0

    def test_the_projection_declares_it_from_the_shape(self):
        """Single-sourced: a field added to the shape is declared by
        construction, so the declared set cannot drift from what is written."""
        assert set(PERCENT_VALUE_FIELDS) <= _EntityHandlers._POINT_DECLARED_PROPS
        assert set(PERCENT_VALUE_FIELDS) == set(percent_props(1_200,
                                                             verbatim="12%"))


# ── one `verbatim` per carrier ─────────────────────────────────────────────

class TestSharedSourceEvidence:
    """`verbatim` is ONE property on the carrier, so it is ONE value's evidence.

    #11827 review P1, measured before this rule existed: a Point carrying
    ``money_props(80_000, "EUR", minor_unit_exponent=2, verbatim="800")``, then
    ``update_point(id, **percent_props(1_200, verbatim="12%"))``, left
    ``amountMinor 80000 / currency EUR / verbatim "12%"`` — the money value's
    source text now quotes a percentage, and ``read_money_value`` still answers
    ``("EUR", 80000, 2)``, so nothing complained. That is the
    normalize-then-lose-the-source path ``money.py`` exists to prevent.
    """

    def test_a_bag_cannot_evidence_two_shapes_at_once(self):
        with pytest.raises(PercentValueError, match="single 'verbatim'"):
            refuse_shared_evidence({
                BASIS_POINTS: 1_200,
                "amountMinor": 80_000, "currency": "EUR",
                "minorUnitExponent": 2, "verbatim": "12% of EUR 800",
            })

    def test_a_percent_cannot_overwrite_a_money_values_evidence(
            self, percent_sdk):
        created = percent_sdk.create_point(
            "statement", "the tranche released EUR 800 committed",
            **money_props(80_000, "EUR", minor_unit_exponent=2,
                          verbatim="EUR 800"))
        with pytest.raises(ValueError, match="overwrite the source text"):
            percent_sdk.update_point(
                created["id"], **percent_props(1_200, verbatim="12%"))
        # …and the money value's evidence is INTACT, which is the point.
        assert percent_sdk.get_entity(created["id"])["verbatim"] == "EUR 800"

    def test_a_money_cannot_overwrite_a_percents_evidence(self, percent_sdk):
        created = percent_sdk.create_point(
            "statement", "the target is 12%",
            **percent_props(1_200, verbatim="12%"))
        with pytest.raises(ValueError, match="overwrite the source text"):
            percent_sdk.update_point(
                created["id"],
                **money_props(80_000, "EUR", minor_unit_exponent=2,
                              verbatim="EUR 800"))
        assert percent_sdk.get_entity(created["id"])[BASIS_POINTS] == 1_200

    def test_an_evidence_free_percent_may_join_a_money_carrier(self, percent_sdk):
        """The rule is about the EVIDENCE, not about how many values a carrier
        may have: a machine-supplied percent (D4b — no `verbatim` at all)
        overwrites nothing, and a node holding a budget AND a target percentage
        is a readable, legitimate state. A blanket "one shape per carrier" rule
        would refuse it — over-blocking for a defect that is not there."""
        created = percent_sdk.create_point(
            "statement", "the tranche released EUR 800 committed",
            **money_props(80_000, "EUR", minor_unit_exponent=2,
                          verbatim="EUR 800"))
        percent_sdk.update_point(created["id"], **percent_props(1_200))
        node = percent_sdk.get_entity(created["id"])
        assert node[BASIS_POINTS] == 1_200
        assert node["verbatim"] == "EUR 800", (
            "the money value's evidence must survive a percent that carries none")

    def test_the_journal_payload_walk_refuses_the_same_bag(self):
        """The bag-level half is refused at BOTH doors: the props intake and the
        journal-first payload walk (which sees the whole record, not one bag)."""
        with pytest.raises(ValueError, match="single 'verbatim'"):
            refuse_malformed_percent_in_payload(
                {"point": {BASIS_POINTS: 1_200, "amountMinor": 80_000,
                           "currency": "EUR", "minorUnitExponent": 2,
                           "verbatim": "12% of EUR 800"}},
                door="EventAPI.PointAdded")

    def test_a_carrier_with_no_value_evidence_is_unaffected(self, percent_sdk):
        created = percent_sdk.create_point("statement", "a plain claim")
        percent_sdk.update_point(created["id"],
                                 **percent_props(1_200, verbatim="12%"))
        assert percent_sdk.get_entity(created["id"])["verbatim"] == "12%"


# ── the module's surface ───────────────────────────────────────────────────

def test_every_public_name_is_exported():
    import types

    public = {
        name for name, value in vars(percent_module).items()
        if not name.startswith("_")
        and not isinstance(value, types.ModuleType)
        and getattr(value, "__module__", None) == percent_module.__name__
    }
    assert public <= set(percent_module.__all__), (
        "a public name is missing from __all__: "
        f"{sorted(public - set(percent_module.__all__))}")
