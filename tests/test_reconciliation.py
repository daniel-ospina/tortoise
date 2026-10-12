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
