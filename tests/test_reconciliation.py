"""#7952 (§17.2) — reconciliation of the SECOND divergence surface.

§3 covers journal ↔ projection. The 2026-10-08 hybrid ruling adds
Postgres (truth) ↔ FalkorDB (cache), and **``truth == cache`` must be
checkable, not assumed**. These tests pin the two properties the issue names:

1. divergence is reported as a **NUMBER** — and separated by DIRECTION, because
   ``extra_in_cache`` is the second-writer (chokepoint) signature that a
   boolean cannot express;
2. a reconciliation that cannot see both sides **refuses** rather than
   reporting agreement — the #1382 fail-open class.

MUTATION PROOF (recorded in the PR body): returning a boolean verdict from
``reconcile_views`` (``divergent`` as ``bool``) REDs
``test_divergence_is_a_count_not_a_boolean``; folding the two directions into
one count REDs ``test_the_two_directions_are_separately_counted``; swallowing a
reader failure into an empty view REDs the two refusal tests.
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from tortoise.reconciliation import (
    ReconciliationUnavailable,
    divergent_key_count,
    reconcile,
    reconcile_views,
)


class TestCounts:
    def test_identical_views_are_zero_divergence(self):
        view = {"p1": {"content": "a", "status": "live"},
                "p2": {"content": "b"}}
        report = reconcile_views(view, dict(view))
        assert report.checked == 2
        assert report.divergent == 0
        assert report.missing_in_cache == 0
        assert report.extra_in_cache == 0
        assert report.field_mismatches == 0
        assert report.divergent_keys == ()

    def test_divergence_is_a_count_not_a_boolean(self):
        """The contract: a NUMBER, so an operator can see the magnitude."""
        report = reconcile_views(
            {"p1": {"x": 1}, "p2": {"x": 1}, "p3": {"x": 1}},
            {"p1": {"x": 1}})
        assert type(report.divergent) is int
        assert not isinstance(report.divergent, bool)
        assert report.divergent == 2
        assert report.checked == 3

    def test_the_two_directions_are_separately_counted(self):
        """`extra_in_cache` is the second-writer signature — never folded in."""
        report = reconcile_views(
            {"in_record_only": {"x": 1}, "shared": {"x": 1}},
            {"in_cache_only": {"x": 1}, "shared": {"x": 1}})
        assert report.missing_in_cache == 1       # the cache is BEHIND
        assert report.extra_in_cache == 1         # a chokepoint violation
        assert report.divergent == 2
        assert set(report.divergent_keys) == {"in_record_only", "in_cache_only"}

    def test_a_stale_value_is_a_field_mismatch(self):
        report = reconcile_views(
            {"p1": {"content": "new", "status": "live"}},
            {"p1": {"content": "old", "status": "live"}})
        assert report.field_mismatches == 1
        assert report.divergent == 1
        assert report.missing_in_cache == 0
        assert report.extra_in_cache == 0
        assert report.divergent_keys == ("p1",)

    def test_a_multi_field_mismatch_counts_fields_and_one_node(self):
        report = reconcile_views(
            {"p1": {"content": "new", "status": "live"}},
            {"p1": {"content": "old", "status": "dead"}})
        assert report.field_mismatches == 2   # fields
        assert report.divergent == 1          # nodes

    def test_a_field_present_on_one_side_only_is_a_mismatch(self):
        report = reconcile_views({"p1": {"a": 1, "b": 2}}, {"p1": {"a": 1}})
        assert report.field_mismatches == 1
        assert report.divergent == 1

    def test_a_none_valued_property_and_an_absent_one_diverge(self):
        """`None` present must not equal absent — the `set(keys)` comparison."""
        truth = {"p1": {"a": None}}
        cache = {"p1": {}}
        report = reconcile_views(truth, cache)
        assert report.field_mismatches == 1
        assert report.divergent == 1
        # the sampled and full paths must not drift apart
        assert report.divergent == divergent_key_count(["p1"], truth, cache)

    def test_a_shared_key_with_uncomparable_values_is_not_agreement(self):
        """A non-Mapping per-key value is compared WHOLE, never as `{} == {}`.

        Coercing both sides to `{}` made two differing values compare equal and
        report zero divergence — a check that cannot read its evidence must not
        report success (#1382). The two values here differ by construction.
        """
        report = reconcile_views({"p1": "alpha"}, {"p1": "beta"})
        assert report.field_mismatches == 1
        assert report.divergent == 1
        assert report.divergent_keys == ("p1",)

    def test_uncomparable_values_agree_when_they_are_equal(self):
        report = reconcile_views({"p1": [1, 2]}, {"p1": [1, 2]})
        assert report.field_mismatches == 0
        assert report.divergent == 0

    def test_key_order_does_not_matter(self):
        report = reconcile_views(
            {"p1": {"a": 1, "b": 2}}, {"p1": {"b": 2, "a": 1}})
        assert report.field_mismatches == 0
        assert report.divergent == 0

    def test_nested_and_unordered_values_compare_canonically(self):
        report = reconcile_views(
            {"p1": {"m": {"x": 1, "y": 2}, "s": {"a", "b"}}},
            {"p1": {"m": {"y": 2, "x": 1}, "s": {"b", "a"}}})
        assert report.divergent == 0

    def test_the_sample_is_bounded_but_the_count_is_exact(self):
        truth = {f"p{i}": {"x": i} for i in range(50)}
        report = reconcile_views(truth, {}, sample_limit=5)
        assert report.divergent == 50        # exact
        assert len(report.divergent_keys) == 5   # bounded

    def test_a_negative_sample_limit_is_refused(self):
        with pytest.raises(ValueError):
            reconcile_views({}, {}, sample_limit=-1)


class TestReportShape:
    def test_the_report_carries_no_boolean_verdict(self):
        """A convenience boolean is how a directional count stops being read."""
        report = reconcile_views({}, {})
        for name in ("coherent", "ok", "in_sync", "matches"):
            assert not hasattr(report, name)

    def test_as_dict_is_json_safe(self):
        import json

        report = reconcile_views({"p1": {"x": 1}}, {})
        payload = report.as_dict()
        assert payload["divergent"] == 1
        assert payload["truth_side"] == "record"
        assert payload["cache_side"] == "cache"
        assert isinstance(payload["divergent_keys"], list)
        assert json.loads(json.dumps(payload)) == payload

    def test_render_puts_the_number_in_context(self):
        report = reconcile_views({"p1": {"x": 1}}, {})
        line = report.render()
        assert "1 divergent of 1 checked" in line
        assert "missing_in_cache=1" in line

    def test_the_sides_are_nameable(self):
        report = reconcile_views({}, {}, truth_side="postgres",
                                 cache_side="falkordb")
        assert report.truth_side == "postgres"
        assert report.cache_side == "falkordb"
        assert "postgres vs falkordb" in report.render()

    def test_it_is_frozen(self):
        import dataclasses

        report = reconcile_views({}, {})
        with pytest.raises(dataclasses.FrozenInstanceError):
            report.divergent = 7  # type: ignore[misc]

    def test_a_positional_construction_keeps_its_pre_existing_fields(self):
        """`form_normalized` is APPENDED LAST — a mid-dataclass field re-binds.

        ``ReconciliationReport`` is frozen but NOT ``kw_only``, so inserting a
        new defaulted field before the pre-existing defaulted ones silently
        re-binds every positional construction: ``divergent_keys`` would arrive
        in ``form_normalized`` and the sample would vanish while the counts
        looked right (#9189 review). The first five fields are the count-only
        shape callers already knew.
        """
        from tortoise.reconciliation import ReconciliationReport

        report = ReconciliationReport(1, 0, 0, 0, 0, ("Point:p1",))

        assert report.divergent_keys == ("Point:p1",)
        assert report.form_normalized == 0
        assert report.truth_side == "record"
        assert report.cache_side == "cache"


class TestFailClosed:
    """An unreadable side is UNKNOWN divergence — never agreement (#1382)."""

    def test_an_unreadable_record_side_refuses(self):
        def _boom():
            raise RuntimeError("postgres is down")

        with pytest.raises(ReconciliationUnavailable) as excinfo:
            reconcile(_boom, lambda: {})
        assert "UNKNOWN, not zero" in str(excinfo.value)

    def test_an_unreadable_cache_side_refuses(self):
        def _boom():
            raise RuntimeError("falkordb is down")

        with pytest.raises(ReconciliationUnavailable):
            reconcile(lambda: {}, _boom)

    def test_a_reader_that_is_not_a_keyed_view_refuses(self):
        with pytest.raises(ReconciliationUnavailable):
            reconcile(lambda: ["p1"], lambda: {})

    def test_an_uncomparable_per_key_value_is_not_reported_as_agreement(self):
        """The public seam must not answer `0 divergent` for a bad reader."""
        report = reconcile(lambda: {"p1": "alpha"}, lambda: {"p1": "beta"})
        assert report.divergent == 1

    def test_a_readable_pair_reconciles(self):
        report = reconcile(lambda: {"p1": {"x": 1}}, lambda: {"p1": {"x": 1}})
        assert report.divergent == 0
        assert report.checked == 1


class TestFocusedCount:
    def test_it_counts_the_named_keys_that_disagree(self):
        truth = {"p1": {"x": 1}, "p2": {"x": 2}}
        cache = {"p1": {"x": 1}, "p2": {"x": 9}}
        assert divergent_key_count(["p1", "p2"], truth, cache) == 1

    def test_a_key_missing_from_either_side_disagrees(self):
        assert divergent_key_count(["p1"], {"p1": {"x": 1}}, {}) == 1
        assert divergent_key_count(["p1"], {}, {"p1": {"x": 1}}) == 1

    def test_it_agrees_with_the_full_reconciliation(self):
        truth = {"p1": {"x": 1}, "p2": {"x": 2}, "p3": {"x": 3}}
        cache = {"p1": {"x": 1}, "p2": {"x": 9}}
        keys = ["p1", "p2", "p3"]
        assert (divergent_key_count(keys, truth, cache)
                == reconcile_views(truth, cache).divergent)

    def test_it_agrees_on_an_uncomparable_value_and_a_none_vs_absent(self):
        """The two paths cannot drift — the focused count is not a second rule."""
        scalar_truth = {"p1": "alpha"}
        scalar_cache = {"p1": "beta"}
        assert (divergent_key_count(["p1"], scalar_truth, scalar_cache)
                == reconcile_views(scalar_truth, scalar_cache).divergent)

        none_truth = {"p1": {"a": None}}
        absent_cache = {"p1": {}}
        assert (divergent_key_count(["p1"], none_truth, absent_cache)
                == reconcile_views(none_truth, absent_cache).divergent)


class TestStoredVectorForm:
    """#8615 — a non-verbatim vector is STORED at float32; compare that form.

    Measured before the fix (private FalkorDB + a throwaway `postgres:16`, the
    shipped 39-record fixture, a full `rebuild_from_record`):
    `1 divergent of 7 checked (field_mismatches=1)`, the single field being
    `Point:p1 .embedding` — record `[0.1, 0.2]` against cache
    `[0.10000000149011612, 0.20000000298023224]`. A correctness-preserving
    projection, reported as a stale value on every rebuild, and structurally
    unable to go green.

    The rule: narrow the TRUTH side to the width `cache_projection.node_statement`
    writes (shared, one spelling — `tortoise/stored_form.py`), unless either
    side declares `embedding_verbatim`. It is NOT a tolerance: the comparison
    stays exact, and only the representation changes.
    """

    RECORD_VEC: ClassVar[list[float]] = [0.1, 0.2]

    @staticmethod
    def _stored(vector):
        """The value the cache holds — the narrowing the projection writes."""
        from tortoise.stored_form import narrow_to_stored_width
        return narrow_to_stored_width(vector)

    def _one(self, record_props, cache_props):
        return reconcile_views({"Point:p1": record_props},
                               {"Point:p1": cache_props})

    def test_a_faithful_projection_is_not_a_field_mismatch(self):
        stored = self._stored(self.RECORD_VEC)
        # The narrowing is REAL — otherwise this test proves nothing.
        assert stored is not None and stored != self.RECORD_VEC
        report = self._one({"id": "p1", "embedding": self.RECORD_VEC},
                           {"id": "p1", "embedding": stored})
        assert report.field_mismatches == 0
        assert report.divergent == 0
        assert report.divergent_keys == ()
        # The focused count is the same rule, not a second one.
        assert divergent_key_count(
            ["Point:p1"],
            {"Point:p1": {"id": "p1", "embedding": self.RECORD_VEC}},
            {"Point:p1": {"id": "p1", "embedding": stored}}) == 0

    def test_a_real_vector_divergence_is_still_a_mismatch(self):
        """Narrowing must not turn the check into a check that cannot fail."""
        report = self._one({"id": "p1", "embedding": [0.1, 0.2]},
                           {"id": "p1", "embedding": self._stored([0.1, 0.9])})
        assert report.field_mismatches == 1
        assert report.divergent == 1

    def test_the_narrowing_is_not_a_tolerance(self):
        """A cache value a band would forgive is a mismatch: only the RECORD
        side is narrowed, so the acceptance window is one point, not a width."""
        near = [0.100000001]   # 1e-9 above 0.1 — inside one float32 step
        assert self._stored(near) == self._stored([0.1])   # a band WOULD forgive it
        report = self._one({"id": "p1", "embedding": [0.1]},
                           {"id": "p1", "embedding": near})
        assert report.field_mismatches == 1

    def test_a_verbatim_vector_is_compared_exactly(self):
        """`embedding_verbatim` means the store holds the value UN-narrowed."""
        report = self._one(
            {"id": "p1", "embedding": [0.1, 0.2], "embedding_verbatim": True},
            {"id": "p1", "embedding": self._stored([0.1, 0.2]),
             "embedding_verbatim": True})
        assert report.field_mismatches == 1

    @pytest.mark.parametrize("on_truth,on_cache", [(True, False), (False, True)])
    def test_the_marker_on_EITHER_side_forces_the_exact_comparison(
            self, on_truth, on_cache):
        """Same rule as the journal half: a dropped flag must not hide a
        narrowed vector behind the side that kept it."""
        record = {"id": "p1", "embedding": [0.1, 0.2]}
        cache = {"id": "p1", "embedding": self._stored([0.1, 0.2])}
        if on_truth:
            record["embedding_verbatim"] = True
        if on_cache:
            cache["embedding_verbatim"] = True
        # the marker is PRESENT on one side only → its own presence is also a
        # mismatch (P5), so compare the vector field with the marker removed
        # from the counting by asking about the value rule directly.
        from tortoise.reconciliation import _props_agree
        assert not _props_agree("embedding", record, cache)

    def test_a_non_vector_list_property_is_still_compared_exactly(self):
        """The narrowing is declared per property, not applied to any list."""
        report = self._one({"id": "p1", "tags": [0.1, 0.2]},
                           {"id": "p1", "tags": self._stored([0.1, 0.2])})
        assert report.field_mismatches == 1

    def test_a_non_numeric_value_under_a_vector_name_compares_exactly(self):
        """`narrow_to_stored_width` is TOTAL — it degrades, it never raises."""
        report = self._one({"id": "p1", "embedding": ["a", "b"]},
                           {"id": "p1", "embedding": ["a", "c"]})
        assert report.field_mismatches == 1
        report = self._one({"id": "p1", "embedding": ["a", "b"]},
                           {"id": "p1", "embedding": ["a", "b"]})
        assert report.field_mismatches == 0

    def test_a_vector_of_a_different_width_is_a_mismatch(self):
        report = self._one({"id": "p1", "embedding": [0.1, 0.2]},
                           {"id": "p1", "embedding": self._stored([0.1])})
        assert report.field_mismatches == 1


class TestStoredBooleanForm:
    """#9184 — `embedding_verbatim` is `text` in the record and a boolean in the cache.

    The record column is ``embedding_verbatim text`` and the write path does not
    coerce, so a journal payload's ``True`` lands there as the string ``'true'``
    while ``cache_projection.node_statement`` writes a graph BOOLEAN. Exact
    equality therefore reports ONE permanent ``field_mismatches`` entry for
    every verbatim point — a faithful projection that can never go green, and
    the same class as #8615 (a property whose STORED FORM differs by schema).

    The rule is ``stored_form.BOOLEAN_FORM_PROPS`` + ``declared_boolean``: the
    two sides are compared by the boolean they DECLARE, over the tree's own
    recorded truthy/falsy vocabulary (#4097) and in BOTH directions. It is not
    ``bool(value)`` — ``bool('false')`` is ``True`` — so ``'false'`` reads as
    ``False`` and a genuinely different marker stays a divergence. Fields it
    absorbs are counted apart (``form_normalized``), so the decision is visible
    in the report instead of being folded into a mismatch count.
    """

    @staticmethod
    def _one(record_props, cache_props):
        return reconcile_views({"Point:p1": record_props},
                               {"Point:p1": cache_props})

    def test_a_faithful_verbatim_marker_is_not_a_field_mismatch(self):
        report = self._one({"id": "p1", "embedding_verbatim": "true"},
                           {"id": "p1", "embedding_verbatim": True})
        assert report.field_mismatches == 0
        assert report.divergent == 0
        assert report.divergent_keys == ()

    def test_the_form_divergence_is_its_own_named_count(self):
        """Not swallowed: an operator can see the rule decided something."""
        report = self._one({"id": "p1", "embedding_verbatim": "true"},
                           {"id": "p1", "embedding_verbatim": True})
        assert report.form_normalized == 1
        assert report.as_dict()["form_normalized"] == 1
        assert "form_normalized=1" in report.render()

    def test_it_does_not_touch_the_render_when_nothing_was_normalized(self):
        report = reconcile_views({"p1": {"x": 1}}, {"p1": {"x": 1}})
        assert "form_normalized" not in report.render()

    def test_a_genuinely_different_marker_is_still_a_mismatch(self):
        """The check must still be a check: `true` vs `False` is divergence."""
        report = self._one({"id": "p1", "embedding_verbatim": "true"},
                           {"id": "p1", "embedding_verbatim": False})
        assert report.field_mismatches == 1
        assert report.divergent == 1
        assert report.form_normalized == 0

    def test_the_false_spelling_is_FALSE_not_truthy(self):
        """`bool('false')` is `True` — the trap this rule must not fall into."""
        agreed = self._one({"id": "p1", "embedding_verbatim": "false"},
                           {"id": "p1", "embedding_verbatim": False})
        assert agreed.form_normalized == 1
        assert agreed.field_mismatches == 0
        disagreed = self._one({"id": "p1", "embedding_verbatim": "false"},
                              {"id": "p1", "embedding_verbatim": True})
        assert disagreed.field_mismatches == 1

    # ── the trap, in the COMPOSITION the marker feeds (#9189 review) ─────
    #
    # Fixing the MARKER's own comparison left the same string deciding the
    # VECTOR rule: `is_verbatim` read `'false'` truthily, so a record holding
    # the falsy spelling claimed the verbatim storage form and a cache that
    # legitimately stored the `f32` width was reported divergent FOREVER —
    # #8615's symptom, reached through #9184's spelling. The three tests below
    # are what the marker-only tests above cannot see, because they carry no
    # `embedding` at all.

    def test_the_falsy_spelling_does_not_force_a_permanent_vector_mismatch(self):
        """`'false'` means the store NARROWS — a cache in the DECLARED form agrees.

        ⚠️ The cache here is the one a CORRECTED writer produces, not the one
        the shipped `node_statement` produces today: `is_verbatim` still reads
        the falsy spelling truthily, so the real end-to-end run stores the
        marker `True` and the vector un-narrowed and reports the MARKER as one
        divergence until #9184's writer half lands. That residue is pinned
        where it is produced —
        `test_the_shipped_writer_stores_the_falsy_marker_truthily` below and
        `tests/test_record_reconcile_trigger.py::
        test_a_falsy_marker_point_reports_the_WRITER_half_as_divergence`.

        Pre-fix (`is_verbatim`'s truthiness in the CHECK's vector rule) this
        reported `field_mismatches == 1` on a point whose form the DECLARED
        marker says should narrow.
        """
        from tortoise.stored_form import narrow_to_stored_width
        stored = narrow_to_stored_width([0.1, 0.2])
        assert stored is not None and stored != [0.1, 0.2]   # the narrowing is REAL
        report = self._one(
            {"id": "p1", "embedding": [0.1, 0.2],
             "embedding_verbatim": "false"},
            {"id": "p1", "embedding": stored, "embedding_verbatim": False})
        assert report.field_mismatches == 0, report.render()
        assert report.divergent == 0
        assert report.form_normalized == 2   # the vector AND the marker

    def test_a_verbatim_record_still_refuses_the_narrowed_vector(self):
        """The `'false'` fix must not widen the rule for a true marker."""
        from tortoise.stored_form import narrow_to_stored_width
        stored = narrow_to_stored_width([0.1, 0.2])
        report = self._one(
            {"id": "p1", "embedding": [0.1, 0.2],
             "embedding_verbatim": "true"},
            {"id": "p1", "embedding": stored, "embedding_verbatim": True})
        assert report.field_mismatches == 1, report.render()
        assert report.form_normalized == 1   # only the marker's own form

    def test_an_unparseable_marker_keeps_the_strict_vector_comparison(self):
        """Outside the declared vocabulary, degrade to STRICT — never to False."""
        from tortoise.stored_form import narrow_to_stored_width
        stored = narrow_to_stored_width([0.1, 0.2])
        report = self._one(
            {"id": "p1", "embedding": [0.1, 0.2],
             "embedding_verbatim": "maybe"},
            {"id": "p1", "embedding": stored, "embedding_verbatim": "maybe"})
        assert report.field_mismatches == 1, report.render()

    def test_the_shipped_writer_stores_the_falsy_marker_truthily(self):
        """#9184's remaining half, pinned against the SHIPPED writer's OUTPUT.

        The hand-built caches above describe a corrected writer. This drives
        `cache_projection.node_statement` — the module that actually materializes
        a record node — and reconciles what it produces with the record, so the
        residual is measured rather than narrated: the marker diverges
        permanently for the falsy spelling until `is_verbatim` stops reading
        `'false'` truthily, while the vector (stored un-narrowed) agrees exactly.

        Pre-fix the framing claimed a clean end-to-end reconcile for `'false'`
        on the strength of a cache the shipped writer cannot produce (#9189
        review): a test that only feeds a hand-built cache certifies a fix the
        product cannot reach.
        """
        from tortoise import cache_projection as cp

        node = cp.RecordNode(label="Point", key="p1",
                             props={"id": "p1", "embedding": [0.1, 0.2],
                                    "embedding_verbatim": "false"})
        stmt = cp.node_statement(node)
        cache_props = dict(stmt.params["props"])
        cache_props["embedding"] = stmt.params.get("embedding")

        assert stmt.params.get("embedding") == [0.1, 0.2], (
            "the writer stored the vector narrowed — `is_verbatim` changed, so "
            "re-check this pin and the #9184 disposition")
        assert cache_props[cp.VERBATIM_MARKER] is True, (
            "the writer stored the marker as False — `is_verbatim` changed, so "
            "the falsy spelling now agrees end to end")

        report = reconcile_views({"Point:p1": dict(node.props)},
                                 {"Point:p1": cache_props})

        assert report.field_mismatches == 1, report.render()
        assert report.divergent_keys == ("Point:p1",)
        assert report.form_normalized == 0

    def test_the_check_and_the_writer_read_the_marker_differently(self):
        """#9184's remaining half, recorded where the difference is made.

        `is_verbatim` decides what the WRITER stores and still reads `'false'`
        truthily (so that vector is written un-narrowed and compared exactly by
        the agreement branch). The CHECK reads the DECLARED boolean. Pinning the
        asymmetry keeps the residual visible instead of re-discovering it.
        """
        from tortoise.stored_form import declares_verbatim, is_verbatim
        marker = {"embedding_verbatim": "false"}
        assert is_verbatim(marker) is True          # the writer: still truthy
        assert declares_verbatim(marker) is False   # the check: the declared value
        assert declares_verbatim({"embedding_verbatim": "true"}) is True
        assert declares_verbatim({}) is False       # no marker ⇒ the store narrows
        assert declares_verbatim({"embedding_verbatim": None}) is False
        assert declares_verbatim({"embedding_verbatim": "maybe"}) is True

    def test_an_unparseable_marker_value_stays_exact(self):
        """Outside the declared vocabulary, degrade to exact — never to False."""
        report = self._one({"id": "p1", "embedding_verbatim": "maybe"},
                           {"id": "p1", "embedding_verbatim": True})
        assert report.field_mismatches == 1
        assert report.form_normalized == 0

    def test_a_marker_on_one_side_only_is_still_a_mismatch(self):
        """Presence is checked before the form rule (a dropped marker counts)."""
        report = self._one({"id": "p1"},
                           {"id": "p1", "embedding_verbatim": True})
        assert report.field_mismatches == 1
        assert report.form_normalized == 0

    def test_the_rule_is_a_declared_table_not_a_truthiness_class(self):
        """A second boolean-ish property must be DECLARED, not silently equal."""
        from tortoise.stored_form import BOOLEAN_FORM_PROPS, VERBATIM_MARKER
        assert frozenset({VERBATIM_MARKER}) == BOOLEAN_FORM_PROPS
        report = self._one({"id": "p1", "some_flag": "true"},
                           {"id": "p1", "some_flag": True})
        assert report.field_mismatches == 1
        assert report.form_normalized == 0

    def test_declared_boolean_is_total_and_never_raises(self):
        """A record is a FILE: an odd value must degrade, not explode."""
        from tortoise.stored_form import declared_boolean
        assert declared_boolean(True) is True
        assert declared_boolean(False) is False
        assert declared_boolean(1) is True
        assert declared_boolean(0) is False
        assert declared_boolean(" TRUE ") is True
        assert declared_boolean("false") is False
        assert declared_boolean("maybe") is None
        assert declared_boolean(None) is None
        assert declared_boolean(2) is None
        assert declared_boolean([1]) is None
        assert declared_boolean({"a": 1}) is None


def _storage_arch_section(heading: str) -> str:
    """One `### …` section of STORAGE-ARCHITECTURE.md, up to the next heading."""
    doc = (Path(__file__).resolve().parents[1]
           / "docs/architecture/STORAGE-ARCHITECTURE.md").read_text()
    start = doc.index(heading)
    ends = [doc.find(marker, start + len(heading)) for marker in ("\n### ", "\n## ")]
    ends = [e for e in ends if e != -1]
    return doc[start:min(ends)] if ends else doc[start:]


def test_section_17_2_carries_the_stored_form_overrides_line():
    """The decision has ONE recorded home, and it must say WHAT it departs from.

    The rule this issue lands (narrow the truth side to the store's `vecf32`
    width) is a deliberate departure from "compare every property by exact
    value", which is what `_canonical`'s own docstring asserted. A record that
    states the choice but not what it overrides reads identically to an
    accident of history, so the next reader holding a stack of exact-comparison
    habits tidies it away. §17.2 owns reconciliation in
    `STORAGE-ARCHITECTURE.md`; the marker lives there (#8615).
    """
    section = _storage_arch_section("### 17.2 Cache coherence")
    overrides = [ln for ln in section.splitlines() if "OVERRIDES:" in ln]
    assert len(overrides) == 1, (
        "expected exactly one `OVERRIDES:` line in §17.2 (#8615)")
    assert "float32" in overrides[0] or "vecf32" in overrides[0], (
        "the `OVERRIDES:` line must name the DEFAULT departed from — exact "
        "by-value comparison defeated by the store's `float32`/`vecf32` width "
        "— not merely state the choice (#8615)")
    assert "stored_form" in section, (
        "§17.2 must name `tortoise/stored_form.py` as the one spelling of the "
        "storage form, shared by the writer and both checks (#8615)")


def test_section_17_2_names_9184_and_its_own_cost():
    """The exclusion is NAMED on the artifact a lane reads — never silent.

    #9189's own words: *"Excluding it silently is the failure this child exists
    to prevent."* The boolean-form rule is visible in three places, and this
    pins the doc half: §17.2 must name the defect issue, the count that keeps
    the rule's cost visible, and the fact that the schema decision is still
    open. A rule whose record stops saying so reads as an accident, and the next
    reader holding an exact-comparison habit deletes it.
    """
    section = _storage_arch_section("### 17.2 Cache coherence")
    assert "#9184" in section, "§17.2 must name the marker's stored-form defect"
    assert "form_normalized" in section, (
        "§17.2 must name the count that keeps the rule's cost visible")
    assert "declared_boolean" in section or "BOOLEAN_FORM_PROPS" in section, (
        "§17.2 must name the one spelling of the boolean form")
    assert "stays open" in section, (
        "§17.2 must record that the schema normalisation is still #9184's, so "
        "this rule is not mistaken for the fix")
    assert "record_reconcile" in section, (
        "§17.2 must name the reconciler's production trigger (#9189)")


def test_section_17_2_still_carries_exactly_one_overrides_line():
    """#9184's rule is a second stored-form exception, NOT a second `OVERRIDES:`.

    The marker is reserved for a decision that departs from the common default.
    The boolean form is the SAME decision shape #8615 already recorded (compare
    the stored form, not the schema's choice of type), so it extends that
    record rather than adding a competing one — two `OVERRIDES:` lines for one
    rule would make the section unreadable about which default it departs from.
    """
    section = _storage_arch_section("### 17.2 Cache coherence")
    assert len([ln for ln in section.splitlines() if "OVERRIDES:" in ln]) == 1
